"""Small helpers shared by every module: time stamps, hashing, safe file writing, JSON Lines."""
from __future__ import annotations

import datetime as dt
import errno
import glob
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import unicodedata


def now() -> str:
    """The local time with its UTC offset, to the millisecond (ISO 8601)."""
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="milliseconds")


def ts(s: str) -> float:
    """Seconds since 1970 for a time stamp written by now(), so that times from computers in different time
    zones (or either side of a clock change) sort correctly. Unreadable stamps sort first."""
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.timestamp()
    except (ValueError, TypeError):
        return 0.0


WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def today() -> str:
    return dt.date.today().isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


WINDOWS_MAX_PATH = 259           # characters in a path, unless long paths are turned on in Windows
IN_USE = (32, 33)                # Windows: ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION
_LONG_PATHS: list = []


def windows_path_limit() -> bool:
    """True when this computer limits a path to 259 characters: Windows, unless long paths are turned on (the
    LongPathsEnabled setting; the Python ReviewDesk runs with can then use longer paths)."""
    if not sys.platform.startswith("win"):
        return False
    if not _LONG_PATHS:
        on = False
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as k:
                on = winreg.QueryValueEx(k, "LongPathsEnabled")[0] == 1
        except (OSError, ImportError):
            on = False
        _LONG_PATHS.append(on)
    return not _LONG_PATHS[0]


def in_use(e: BaseException) -> bool:
    """True when Windows refused to change a file because another program has it open (Adobe Reader holding a PDF,
    Excel holding a spreadsheet)."""
    return isinstance(e, OSError) and getattr(e, "winerror", None) in IN_USE


LONG_PATH_ADVICE = ("Open the project from a shorter path (for example, add a shortcut to the shared folder near the top "
                    "of your OneDrive, or move it there), or ask your IT service desk to turn on long paths in Windows.")


def file_problem(e: BaseException, root: str = "") -> str:
    """What went wrong with a file, in words a person can act on, or '' if e is not a problem with a file that this can
    explain. root: the project folder, so that a file in it is named as the person sees it there."""
    if not isinstance(e, OSError):
        return ""
    names = [n for n in (e.filename, getattr(e, "filename2", None)) if isinstance(n, str) and n]

    def shown(n: str) -> str:
        if root and n and os.path.normcase(os.path.abspath(n)).startswith(os.path.normcase(os.path.abspath(root)) + os.sep):
            return os.path.relpath(n, root)
        return n
    # a whole file is written to a temporary file first, which then takes the real one's place: name the real one
    real = [n for n in names if not os.path.basename(n).startswith(".tmp-")]
    label = shown((real or names or [""])[0])
    what = (f"The PDF {label}" if label.lower().endswith(".pdf") else f"The file {label}") if label else "A file"
    win = getattr(e, "winerror", None)
    longest = max(names, key=len) if names else ""
    if in_use(e):
        return (f"{what} is open in another program (such as Adobe Reader, Word or Excel), so ReviewDesk could not change "
                "it. Close it in that program, then try again.")
    if win == 206 or (win in (2, 3) and len(longest) > WINDOWS_MAX_PATH and windows_path_limit()):
        return (f"Windows could not use {shown(longest) or 'a file'}: its path is {len(longest)} characters long, and "
                f"Windows allows {WINDOWS_MAX_PATH} unless long paths are turned on. " + LONG_PATH_ADVICE)
    if e.errno == errno.ENAMETOOLONG:
        return (f"The name or path of {label or 'a file'} is too long for this computer. Open the project from a shorter "
                "path, then try again.")
    if isinstance(e, PermissionError) or win == 5:
        return (f"ReviewDesk is not allowed to change {label or 'a file'}. It may be read-only, in a folder you can only "
                "read, or protected by Windows (Controlled folder access). Check that you can change files in the project "
                "folder, or ask your IT service desk, then try again.")
    if e.errno == errno.ENOSPC or win in (39, 112):
        return (f"There is no room left on the disk for {label or 'the file'}. Free some space (in OneDrive you can make "
                "files online-only: right-click, Free up space), then try again.")
    return ""


def _replace(src: str, dst: str) -> None:
    """os.replace with a short retry: on Windows, OneDrive or a virus scanner can hold a file for a moment."""
    for attempt in range(8):
        try:
            os.replace(src, dst)
            _wrote(dst)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.25 * (attempt + 1))


# How many times this process has written a file (see project.request_scope: a project's files are looked at once a
# request, and again only after this process writes), and the count when it last wrote each file: part of the key of
# every cached result, so a file this process writes is read again even where the disk keeps times to 2 seconds.
WRITES = 0
WROTE: dict = {}


def _wrote(path: str = "") -> None:
    global WRITES
    WRITES += 1
    if path:
        WROTE[os.path.normcase(os.path.abspath(path))] = WRITES


def write_text_atomic(path: str, text: str) -> None:
    """Write a whole file in one step, so a reader (or OneDrive) never sees half a file."""
    _wrote(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        _replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def write_bytes_atomic(path: str, data: bytes) -> None:
    _wrote(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def write_json_atomic(path: str, obj) -> None:
    write_text_atomic(path, json.dumps(obj, ensure_ascii=False, indent=1))


def read_json(path: str, default=None):
    if not os.path.isfile(path):
        return default
    for attempt in range(4):
        try:
            with open(path, encoding="utf-8-sig") as f:
                return json.load(f)
        except FileNotFoundError:  # it went between the look and the read (a sync client replacing it): not there
            return default
        except (ValueError, PermissionError):
            if attempt == 3:
                raise
            time.sleep(0.3)        # a sync client may be part-way through writing it
    return default


def ensure_newline(path: str) -> None:
    """If a file was cut off part-way through a line, end that line so the next one starts cleanly."""
    try:
        with open(path, "a+b") as f:
            f.seek(0, os.SEEK_END)
            if f.tell():
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    f.seek(0, os.SEEK_END)
                    f.write(b"\n")
    except FileNotFoundError:
        pass


def append_jsonl(path: str, obj) -> None:
    """Append one record to a JSON Lines file and flush it to disk straight away."""
    _wrote(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    for attempt in range(8):
        try:
            with open(path, "a+b") as f:
                f.seek(0, os.SEEK_END)
                if f.tell():                          # a write cut off part-way leaves no newline: start a fresh line
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":
                        f.seek(0, os.SEEK_END)
                        f.write(b"\n")
                f.seek(0, os.SEEK_END)
                f.write(line.encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
            _wrote(path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.25 * (attempt + 1))


def read_jsonl(path: str) -> list:
    """Every complete line of a JSON Lines file. A last line still being synced is ignored, not fatal."""
    out = []
    if not os.path.isfile(path):
        return out
    try:
        f = open(path, encoding="utf-8-sig", errors="replace")
    except FileNotFoundError:      # it went between the look and the read
        return out
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def files_in(folder: str, pattern: str) -> list:
    """The files in folder whose names match pattern (with * and ?), sorted. Only the name is a pattern: the folder's
    path is taken exactly as it is, so a folder such as 'MSc [2026-27]' is not read as a pattern, as glob would read it
    (where [2026-27] means one character from 2, 0, 2 or 6-2, so nothing would be found)."""
    return sorted(glob.glob(os.path.join(glob.escape(folder), pattern)))


def safe_filename(name: str, max_len: int = 120) -> str:
    """A file name that is safe on Windows, macOS and OneDrive. A long name is shortened at a word boundary (where
    that keeps most of it) and keeps its extension."""
    name = unicodedata.normalize("NFC", name or "file")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        name = "file"
    stem, ext = os.path.splitext(name)
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,8}", ext):             # 'Smith 2026. Final version' has no extension
        stem, ext = name, ""
    if stem.split(".")[0].upper() in WINDOWS_RESERVED:          # CON.ris, nul.txt ... are devices on Windows
        stem = "_" + stem
        name = stem + ext
    if len(name) <= max_len:
        return name
    room = max(1, max_len - len(ext))
    cut = stem[:room + 1]
    space = cut.rfind(" ")
    cut = cut[:space] if space >= room * 0.6 else stem[:room]
    cut = cut.rstrip(" ,;:-–—_.«‹“‘\"'([{") or stem[:room]
    return (cut + ext).rstrip(" .") or "file"


def unique_path(folder: str, name: str) -> str:
    """folder/name, or folder/name (2) ... if that already exists."""
    path = os.path.join(folder, name)
    stem, ext = os.path.splitext(name)
    k = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{stem} ({k}){ext}")
        k += 1
    return path


def csv_safe(v) -> str:
    """Text a spreadsheet will show as text rather than run as a formula."""
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


def web_url(u) -> str:
    """The address if it is an ordinary web address (http or https), otherwise ''. Addresses come from imported
    records and from search services, so anything else (javascript:, file:, data:) is never shown or followed."""
    u = str(u or "").strip()
    if not re.match(r"(?i)https?://[^\s/]", u) or re.search(r"[\x00-\x20\x7f]", u):
        return ""
    return u


class ReplyTooBig(Exception):
    """A reply from another computer was larger, or slower to arrive, than anything ReviewDesk asks for."""


class ReplyStopped(Exception):
    """The person pressed Stop while a reply was arriving."""


def read_reply(r, most: int, seconds: float, stopped=None) -> bytes:
    """Everything a service sent, but never more than `most` bytes and never for longer than `seconds` in all. (A
    time limit on the connection alone applies to each piece that arrives, so a service that sends a byte at a time
    could otherwise hold a request open for ever, and one that sends without end could fill the memory.)
    stopped: a function that says whether the person has pressed Stop; it is asked between pieces (ReplyStopped)."""
    end = time.monotonic() + seconds
    read = getattr(r, "read1", None) or r.read
    out, size = [], 0
    while True:
        if stopped is not None and stopped():
            raise ReplyStopped()
        part = read(65536)
        if not part:
            return b"".join(out)
        size += len(part)
        if size > most:
            raise ReplyTooBig(f"the reply was larger than {most // (1024 * 1024)} MB")
        if time.monotonic() > end:
            raise ReplyTooBig("the reply did not arrive within the time allowed")
        out.append(part)


def one_line(v) -> str:
    """A value as one line of text: a field written into a line-based file (RIS) can then never start a new line, and
    so never a new tag or record."""
    return " ".join(str(v if v is not None else "").split())


def plural(n: int, word: str, words: str | None = None) -> str:
    return f"{n:,} {word if n == 1 else (words or word + 's')}"


def short_text(text: str, limit: int) -> str:
    """text cut to at most limit characters, at a word boundary, without trailing punctuation."""
    t = " ".join((text or "").split())
    if len(t) <= limit:
        return t
    cut = t[:limit + 1].rsplit(" ", 1)[0] if " " in t[:limit + 1] else t[:limit]
    return cut.rstrip(" ,;:-–—")


# Windows file attributes set by OneDrive, SharePoint and other cloud-file providers (Files On-Demand)
_WIN_OFFLINE = 0x1000
_WIN_RECALL_ON_OPEN = 0x40000
_WIN_RECALL_ON_DATA_ACCESS = 0x400000
_MAC_SF_DATALESS = 0x40000000      # macOS File Provider placeholder: the contents are not on this Mac


def online_only(st) -> bool:
    """True if a file's contents are stored online only (a cloud placeholder), from its os.stat() result.
    Reading such a file makes the sync service download it first, which is slow and fails when offline.
    Checking does not download anything. Windows hides some placeholder attributes (offline, reparse point) from
    Python by default, but not 'recall on data access', which is set while a file's contents are not on the device."""
    attrs = getattr(st, "st_file_attributes", 0) or 0
    if attrs & (_WIN_OFFLINE | _WIN_RECALL_ON_OPEN | _WIN_RECALL_ON_DATA_ACCESS):
        return True
    return bool((getattr(st, "st_flags", 0) or 0) & _MAC_SF_DATALESS)


def computer_name() -> str:
    """This computer's own name (as Windows shows it), so people can tell which computer did what."""
    import socket
    try:
        return socket.gethostname()[:40]
    except OSError:
        return ""


# Characters an XML 1.0 file cannot hold (most control characters, lone surrogates, U+FFFE and U+FFFF): dropped from
# workbook text, as docx.clean drops them from Word documents
_NOT_XML = re.compile("[^\u0009\u000a\u000d\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")
XLSX_CELL_MAX = 32000            # Excel holds at most 32,767 characters in a cell


def xml_text(v) -> str:
    """Text as XML can hold it (see _NOT_XML)."""
    return _NOT_XML.sub("", "" if v is None else str(v))


def sheet_name(name, taken: list, i: int) -> str:
    """A worksheet name Excel accepts: at most 31 characters, none of : \\ / ? * [ ], no apostrophe first or last, not
    the reserved name History, and not the same as a name in taken when case is ignored (a name already taken gets
    _<i> added, as before 1.3.0, then _<i>_2, _<i>_3 and so on)."""
    clean = "".join(ch for ch in xml_text(name) if ch not in "[]:*?/\\").strip()[:31].strip().strip("'")
    if not clean or clean.casefold() == "history":
        clean = f"Sheet{i}" if not clean else f"{clean} ({i})"
    used = {n.casefold() for n in taken}
    k = 1
    out = clean
    while out.casefold() in used:            # ends: every turn tries a different suffix, and taken is finite
        suffix = f"_{i}" if k == 1 else f"_{i}_{k}"
        out = clean[:min(28, 31 - len(suffix))].rstrip().rstrip("'") + suffix
        k += 1
    return out


def xlsx(sheets: list) -> bytes:
    """A small Excel workbook without any library: sheets = [(name, [row, ...]), ...]; the first row of each sheet is
    its header (bold, frozen). Cells are text, or numbers when they are numbers."""
    import io
    import zipfile
    from xml.sax.saxutils import escape

    def col(n):
        s = ""
        n += 1
        while n:
            n, r = divmod(n - 1, 26)
            s = chr(65 + r) + s
        return s

    def cell(ref, v, bold):
        st = ' s="1"' if bold else ' s="0"'        # the wrapping style, so long text wraps in LibreOffice too
        if isinstance(v, bool):
            v = "yes" if v else "no"
        if isinstance(v, (int, float)) and not bold:
            return f'<c r="{ref}"{st}><v>{v}</v></c>'
        t = escape(xml_text(v)[:XLSX_CELL_MAX])      # cut before escaping, so no entity is cut in half
        return f'<c r="{ref}" t="inlineStr"{st}><is><t xml:space="preserve">{t}</t></is></c>'
    buf = io.BytesIO()
    names = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, (name, rows) in enumerate(sheets, 1):
            names.append(sheet_name(name, names, i))
            body = []
            for r, row in enumerate(rows):
                body.append(f'<row r="{r + 1}">' + "".join(cell(f"{col(c)}{r + 1}", v, r == 0) for c, v in enumerate(row)) + "</row>")
            widths = "".join(f'<col min="{c + 1}" max="{c + 1}" width="{min(60, max(10, max((len(str(row[c])) for row in rows[:200] if c < len(row)), default=10) + 2))}" customWidth="1"/>'
                             for c in range(max((len(r) for r in rows), default=0)))
            z.writestr(f"xl/worksheets/sheet{i}.xml",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                       '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
                       + (f"<cols>{widths}</cols>" if widths else "") + f'<sheetData>{"".join(body)}</sheetData></worksheet>')
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(1, len(sheets) + 1)) + "</Types>")
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(n, {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(names, 1)) + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                             for i in range(1, len(sheets) + 1))
                   + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                   '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
                   '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs>'
                   '<cellXfs count="2"><xf fontId="0" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>'
                   '<xf fontId="1" applyFont="1" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf></cellXfs></styleSheet>')
    return buf.getvalue()
