"""Knowing whether the project's data were changed outside ReviewDesk.

A shared folder can be changed by anything that can reach it: the sync service putting back an older file or losing a
change, a person opening a file in another program, a file copied over another or deleted. ReviewDesk cannot stop
that. What it does is notice it on each computer, as far as that computer can tell, and list it under Data problems:

* Chained records. Every row in a file of decisions (votes, agreed decisions, extraction, risk of bias, the PDF index)
  carries a fingerprint of itself and of the row before it. A row edited or deleted in the middle of a file no
  longer matches, and the file is reported, naming the row. A row with no fingerprint (unless version 1.0, which
  wrote none, wrote it) or without a signature after a signed row is reported too.
* Copies on each computer. Each computer keeps its own copy of every chained file and every PDF as last seen intact.
  A file that has lost rows this computer had seen, or that has gone, is reported, and can be put back from that
  copy. A computer that never saw a file cannot tell what it held.
* The seal. When screening of a set starts, the lead's computer records a fingerprint of every record's text, of every
  imported export and of the review question and criteria (state/seal.json); every computer checks them again each
  time the data checks run.
* PDFs. Each full text is fingerprinted when it is added (fulltexts/index@...jsonl); a PDF replaced or removed outside
  ReviewDesk is reported.
* Versions. Each computer remembers the newest version of the screening state and of the project file it has read,
  and reports an older one put back later.
* Signatures. Rows a computer writes in its own files are also signed with a secret that never leaves that computer,
  so that computer, and only that one, can tell its own rows from rows written by anything else.

What this does not do: the fingerprints have no secret, so anyone who can write to the shared folder can compute
them. On a computer that has not seen the real rows, a team member who sets out to do so can add rows, or a whole
file, in someone else's name, and nothing here will notice. The checks are for accidents, sync faults and changes to
rows a computer has already seen; the team members who can write to the folder are trusted.

How long a fingerprint is. Every fingerprint is a SHA-256, and every signature an HMAC-SHA-256, written out in full
(64 hexadecimal characters, 256 bits). Versions before 1.3.1 kept only the start of each: 80 bits of a row's
fingerprint, 64 bits of a signature and of the fingerprint of a record's text, of the review question and of the
criteria. What those versions wrote is still in the projects they were used in, and cannot be written again (a row
belongs to the person and the computer that wrote it), so it is still checked, against the start of the same SHA-256:

* a row with a fingerprint of the old length is accepted only before the first row of its file that has one of the
  full length, right or wrong (ReviewDesk never writes one of the old length after a whole one);
* a row's fingerprint and its signature are of one time: the old lengths together, or both whole. No version wrote
  one of each, so a row that has one of each was not written by ReviewDesk;
* a record, the question or the criteria sealed with a fingerprint of the old length keeps it, and is compared with it;
* and only in a project begun with a version before 1.3.1 (the project file says which version created it, and so
  does the first entry in the audit log). In a project begun with 1.3.1 or later nothing of the old length is ever
  accepted.

That is also why a version before 1.3.1 cannot check what this one writes (versions.NEEDS).

What the old lengths still allow, in a project begun with an earlier version. The fingerprint of the old length is
the start of the whole one, so in a file that has no whole row before them, the whole rows at its end can be cut down
to look like rows of that time (each fingerprint worked out again from the one before, each signature cut): their
content cannot be changed that way, but the file alone no longer shows that they were whole. A computer that has seen
the rows notices (its copy holds the whole ones). The computer that wrote them puts its own rows back, where it keeps
a copy of the file to do it from: it keeps one of every file but the AI's answers, which are reported and put back
by hand. A computer that has never seen the rows cannot tell. And a version before 1.3.1 run again on a computer that
has used this one, which UPGRADING.md forbids and the project file stops once the lead has opened the project with
this version, repairs that computer's own files in its own way when it opens the project: rows this version wrote can
come out of that with fingerprints of the old length. They are still that computer's rows, with its signature, and
nothing is lost.

Not everything that is a short piece of a SHA-256 is a check. label() names things (a problem in the data checks, so
that one already accepted can be recognised) and stays 64 bits, as do the project's key, a computer's name, and in
ai.py the name of a prompt and of the text an answer was given for (which say what an answer belongs to, and are in
file names and reports): they are names to look things up by. Whether anything was changed outside ReviewDesk is
never decided by one of them.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading

from . import userconfig, util

H = "h"                      # the field that holds a row's fingerprint
K = "k"                      # the field that holds this computer's signature of it
FULL = 64                    # hexadecimal characters in a whole SHA-256: every fingerprint and signature written now
OLD_ROW = 20                 # ... kept of a row's fingerprint by versions before FULL_SINCE (80 bits)
OLD_PRINT = 16               # ... kept of a signature, and of the fingerprint of a record or a text (64 bits)
FULL_SINCE = "1.3.1"         # the first version to write them whole
CHAINED = ("decisions", "state", "extraction", "rob", "certainty", "fulltexts", "ai")   # folders whose .jsonl files are chained
_LOCKS: dict = {}
_LOCKS_LOCK = threading.Lock()


def _lock(path: str):
    """One writer at a time per file in this program (re-entrant: a repair may add a row while it holds it)."""
    with _LOCKS_LOCK:
        return _LOCKS.setdefault(os.path.normcase(os.path.abspath(path)), threading.RLock())


def row_hash(prev: str, row: dict) -> str:
    """The fingerprint of a row that follows the row whose fingerprint is prev ('' for the first)."""
    return hashlib.sha256((str(prev) + "\n" + _body(row)).encode("utf-8")).hexdigest()


def same(kept, now: str, old: bool = True, short: int = OLD_PRINT) -> bool:
    """Whether a fingerprint that was kept is the fingerprint worked out now (always a whole one). With old, a kept
    fingerprint of the length written before FULL_SINCE counts when it is the start of the whole one."""
    if not isinstance(kept, str) or not kept:
        return False
    return kept == now or (old and len(kept) == short and now.startswith(kept))


def follows(prev: str, row: dict, old: bool = True) -> bool:
    """Whether a row carries the fingerprint of a row in this place (after the row whose fingerprint is prev). With
    old, a fingerprint of the length written before FULL_SINCE counts (see the notes at the top)."""
    return isinstance(row, dict) and same(row.get(H), row_hash(prev, row), old, OLD_ROW)


def whole(row) -> bool:
    """The row carries a fingerprint of the full length (whether or not it is the right one)."""
    return isinstance(row, dict) and isinstance(row.get(H), str) and len(row[H]) == FULL


def of_one_time(row) -> bool:
    """A row's fingerprint and signature are of the same time: both whole, or both of the lengths written before
    FULL_SINCE (a row with no signature has nothing to disagree with). No version wrote one of each."""
    if not isinstance(row, dict) or K not in row:
        return True
    h, k = row.get(H), row[K]
    if not isinstance(h, str) or not isinstance(k, str):
        return False
    return (len(h), len(k)) in ((FULL, FULL), (OLD_ROW, OLD_PRINT))


def last_hash(path: str) -> str:
    """The fingerprint of the last complete row of a file ('' if none). Rows end at a line feed and nowhere else:
    text typed or pasted into a note can hold other characters that some tools treat as line ends (U+2028, U+2029,
    U+0085), and a row is read whole however long it is."""
    if not os.path.isfile(path):
        return ""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        span = 65536
        while True:
            start = max(0, size - span)
            f.seek(start)
            lines = f.read().split(b"\n")
            if start > 0:
                lines = lines[1:]                        # the first piece may be the end of a row that began earlier
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue                             # a last line still being written
                return str(d.get(H, "")) if isinstance(d, dict) else ""
            if start == 0:
                return ""
            span *= 8                                    # no whole row in that much of the end: look further back


def _body(row: dict) -> str:
    return json.dumps({k: v for k, v in row.items() if k not in (H, K)}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sign(row: dict) -> str:
    """This computer's signature of a row's content (not of its place in the file, so a genuine row can be moved)."""
    return hmac.new(userconfig.device_secret().encode(), _body(row).encode("utf-8"), hashlib.sha256).hexdigest()


def signed_here(row) -> bool:
    """This computer signed the row. A signature of the length written before FULL_SINCE counts only on a row whose
    fingerprint is of that length too (a row an earlier version wrote, and has not been given a new place since), and
    a whole one only beside a whole fingerprint."""
    if not isinstance(row, dict) or K not in row or not of_one_time(row):
        return False
    kept, now = row[K], sign(row)
    return hmac.compare_digest(kept.encode("utf-8"), (now if len(kept) == FULL else now[:OLD_PRINT]).encode("utf-8"))


def unsigned(rows: list) -> list:
    """Positions of chained rows in one of this computer's own files that this computer did not sign (rows written
    before signatures were used, at the start of a file, are accepted)."""
    bad, signed = [], False
    for k, d in enumerate(rows):
        if not isinstance(d, dict) or H not in d:
            continue
        if K in d:
            signed = True
            if not signed_here(d):
                bad.append(k)
        elif signed:
            bad.append(k)
    return bad


def append(path: str, row: dict, mirror: str = "", snapshot: str = "") -> dict:
    """Add a row to a chained file (and to this computer's own copy of it). The chain continues from this computer's
    copy when there is one, because that copy is the true record of what this computer wrote. A computer's first row
    in a file it has no copy of yet (a project moved to another folder, or written by an older version) first copies
    the file as it is, if it is intact, so its copy is complete. Rows in a computer's own files are signed."""
    with _lock(path):
        if mirror and not os.path.isfile(mirror) and os.path.isfile(path):
            have = util.read_jsonl(path)
            if not snapshot:                       # this computer's last intact view of the file (see project._check_chain)
                name = os.path.basename(mirror)
                snapshot = os.path.join(os.path.dirname(os.path.dirname(mirror)), "snap", name if "__" in name else "decisions__" + name)
            seen = util.read_jsonl(snapshot) if os.path.isfile(snapshot) else []
            if seen and not verify(seen) and len(seen) > len(have) and have == seen[:len(have)]:
                have = seen                        # the file lost rows this computer had seen: start from those
            if have and not verify(have):
                os.makedirs(os.path.dirname(mirror), exist_ok=True)
                util.write_text_atomic(mirror, "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in have))
        prev = last_hash(mirror) if mirror and os.path.isfile(mirror) else last_hash(path)
        row = dict(row)
        row.pop(H, None)
        row.pop(K, None)
        row[H] = row_hash(prev, row)
        if mirror:
            row[K] = sign(row)
        util.append_jsonl(path, row)
        if mirror:
            try:
                util.append_jsonl(mirror, row)
            except OSError:
                pass
        return row


def verify(rows: list, old: bool = True) -> list:
    """Positions (0-based) of rows that do not match their fingerprint, or do not follow the row before them.
    Rows written before fingerprints were used (no 'h', at the start of a file) are accepted as they are; a row
    without one after the first row that has one was not written by ReviewDesk. With old, a fingerprint of the
    length written before FULL_SINCE is accepted until the first row that has one of the full length (right or
    wrong): from there the file was being written by a version that writes them whole, and one of the old length
    does not belong. Without old (a project begun with FULL_SINCE or later) none is accepted. A row whose
    fingerprint and signature are not of one time (of_one_time) is reported too."""
    bad, prev, chained = [], "", False
    for k, d in enumerate(rows):
        if not isinstance(d, dict) or H not in d:
            if chained:
                bad.append(k)
            continue
        chained = True
        if not follows(prev, d, old) or not of_one_time(d):
            bad.append(k)
        if whole(d):
            old = False
        prev = d[H] if isinstance(d[H], str) else ""
    return bad


def record_print(r: dict) -> str:
    """A fingerprint of what a reviewer sees of a record."""
    body = json.dumps({k: r.get(k, "") for k in ("title", "abstract", "authors", "year", "journal", "doi", "pmid", "pubtype")},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def text_print(x) -> str:
    """A fingerprint of something that can be written as JSON (the review question, the criteria, a plan)."""
    return hashlib.sha256(json.dumps(x, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def label(x) -> str:
    """A short name for something that can be written as JSON: what a problem in the data checks is and the state of
    the file it is about, so that one the lead has accepted is recognised when it is found again. A name, not a check
    (see the notes at the top): it is the length it has always been, so that the names already kept in projects still
    match. Where what is named includes a fingerprint, shortened() of it goes in, for the same reason."""
    return text_print(x)[:OLD_PRINT]


def shortened(print_: str) -> str:
    """A fingerprint as versions before FULL_SINCE kept it, for label() only."""
    return str(print_)[:OLD_PRINT]


# ------------------------------------------------------------------------------------------------ files with a cache
class FileHashes:
    """sha256 of large files (exports, PDFs), worked out again only when a file's size or time changes."""

    def __init__(self, path: str):
        self.path = path
        try:
            self.known = util.read_json(path, {}) or {}
        except ValueError:
            self.known = {}
        self.changed = False

    def of(self, f: str, legacy_text: bool = False) -> str:
        """The file's sha256; legacy_text: the way exports were fingerprinted when added (the bytes read as Latin-1
        text and written as UTF-8)."""
        try:
            st = os.stat(f)
        except OSError:
            return ""
        if not os.path.isfile(f):
            return ""
        key = f"{st.st_mtime_ns}:{st.st_size}"
        ck = ("text:" if legacy_text else "") + f
        got = self.known.get(ck)
        if got and got[0] == key:
            return got[1]
        try:
            if legacy_text:
                with open(f, "rb") as fh:
                    sha = util.sha256_text(fh.read().decode("latin-1"))
            else:
                sha = util.sha256_file(f)
        except OSError:
            return ""
        self.known[ck] = [key, sha]
        self.changed = True
        return sha

    def save(self) -> None:
        if self.changed:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                util.write_json_atomic(self.path, self.known)
            except OSError:
                pass


def chained_files(p) -> list:
    out = []
    for sub in CHAINED:
        out += util.files_in(p.path(sub), "*.jsonl")
    return out
