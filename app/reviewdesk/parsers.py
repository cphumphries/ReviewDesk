"""Reading database exports: RIS (Ovid, EBSCO, Scopus, Cochrane, Web of Science, reference managers),
PubMed .nbib (MEDLINE format), Cochrane Library plain text, Web of Science tagged text, and CSV."""
from __future__ import annotations

import csv
import html
import io
import itertools
import re
import urllib.parse

FIELDS = ["title", "abstract", "authors", "year", "journal", "doi", "pmid", "pubtype", "volume", "issue",
          "pages", "url", "keywords", "language", "accession"]
# A DOI runs to the next space, quotation mark or angle bracket, with one exception: the DOIs Wiley and others gave
# articles before about 2001 hold the first page and an item code between angle brackets, and go on after them
# (10.1002/(SICI)1097-0258(19980830)17:16<1791::AID-SIM609>3.0.CO;2-5, 10.1175/1520-0477(1998)079<0061:APGTWA>2.0.CO;2).
# Cut at the "<", every article in one issue would have the same DOI. Only that shape is read as part of the DOI
# ("<", a page, one or two colons, a code, ">", then more of the DOI), so markup after a DOI (</p>, <br>, <sup>) still
# ends it.
DOI_RX = re.compile(r"10\.\d{4,9}/(?:[^\s\"<>]|<(?:[a-z0-9]*::|[a-z]{0,2}\d+[a-z]?:)[^\s\"<>:]+>(?=[^\s\"<>]))+", re.I)
ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")
CUT_SHORT = "Export it from the database again (or copy it again, if it was copied), then add it."


class FormatError(Exception):
    pass


def decode(raw: bytes) -> tuple[str, str]:
    """UTF-8 first. A file that is UTF-8 apart from a few stray bytes (one pasted Windows character) is read line
    by line, so one bad byte does not garble every accented letter in the file. Otherwise Windows-1252 (what
    Ovid and Excel often write); latin-1 cannot fail. UTF-16 (what some Windows programs save as 'Unicode') is
    recognised by its byte-order mark, or by the zero bytes between letters."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return raw.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:          # an odd number of bytes, or half of a character pair: part of it is missing
            raise FormatError("This file cannot be read: it is saved as UTF-16 (which some Windows programs call "
                              "'Unicode') and it stops in the middle of a character, or part of it is damaged, so it is "
                              "probably cut short. " + CUT_SHORT) from None
    head = raw[:2000]
    if len(head) >= 20 and head[1::2].count(0) > len(head) // 4 and head[0::2].count(0) < len(head) // 20:
        return raw.decode("utf-16-le", errors="replace"), "utf-16 (no byte-order mark)"
    if len(head) >= 20 and head[0::2].count(0) > len(head) // 4 and head[1::2].count(0) < len(head) // 20:
        return raw.decode("utf-16-be", errors="replace"), "utf-16 (no byte-order mark)"
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass
    lines = raw.split(b"\n")
    bad = 0
    out = []
    for ln in lines:
        try:
            out.append(ln.decode("utf-8"))
        except UnicodeDecodeError:
            bad += 1
            out.append(ln.decode("cp1252", errors="replace"))
    if bad <= max(3, len(lines) // 200):
        text = "\n".join(out)
        return (text[1:] if text.startswith("\ufeff") else text), f"utf-8 ({bad} line{'s' if bad != 1 else ''} read as Windows-1252)"
    for enc in ("cp1252", "latin-1"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1"


def clean_doi(s: str) -> str:
    s = s or ""
    if "%" in s:
        s = urllib.parse.unquote(s)          # some databases export DOIs URL-encoded: %28 for (
    if "&" in s:                             # ... or with its angle brackets as HTML entities (see DOI_RX)
        s = re.sub(r"(?i)&lt;", "<", re.sub(r"(?i)&gt;", ">", s))
    m = DOI_RX.search(s)
    d = m.group(0).rstrip(".;,)]").lower() if m else ""
    return "" if d.startswith("10.1002/central/") else d      # a Cochrane CENTRAL record number, not the article's DOI


def year_of(s: str) -> str:
    m = re.search(r"(19|20)\d\d", s or "")
    return m.group(0) if m else ""


MARKUP_RX = re.compile(r"(?i)</?(?:inf|sup|sub|i|b|em|strong|u|scp|sc)>")
NO_ABSTRACT_RX = re.compile(r"(?i)^\[?\s*no abstract(?: is)? available\.?\s*\]?\.?$")


def _clean(r: dict) -> dict:
    out = {}
    for k in FIELDS:
        v = r.get(k) or ""
        v = " ".join(str(v).split())
        if k in ("title", "abstract", "journal", "authors") and "&" in v:
            v = html.unescape(v)
        if k in ("title", "abstract") and "<" in v:          # Scopus marks sub- and superscripts: SpO<inf>2</inf>
            v = MARKUP_RX.sub("", v)
        if k == "abstract" and NO_ABSTRACT_RX.match(v):
            v = ""                                            # Scopus's "[No abstract available]" is not an abstract
        out[k] = v
    out["title"] = re.sub(r"^\[(.*)\]\.?$", r"\1", out["title"])      # Ovid puts translated titles in [brackets]
    return out


# ----------------------------------------------------------------------------------------------- RIS
RIS_LINE = re.compile(r"^([A-Z][A-Z0-9])\s{1,2}-(?:\s(.*))?$")


RIS_TYPES = {"JOUR": "Journal article", "EJOUR": "Journal article", "JFULL": "Journal article", "CONF": "Conference paper",
             "CPAPER": "Conference paper", "ABST": "Abstract", "BOOK": "Book", "EBOOK": "Book", "CHAP": "Book chapter",
             "ECHAP": "Book chapter", "THES": "Thesis", "RPRT": "Report", "ELEC": "Web page", "NEWS": "Newspaper article",
             "MGZN": "Magazine article", "INPR": "In press", "UNPB": "Unpublished work", "DATA": "Dataset", "STAND": "Standard",
             "PAT": "Patent", "SER": "Serial", "ICOMM": "Personal communication", "GOVDOC": "Government document",
             "CTLG": "Catalogue", "GEN": "", "PAMP": "Pamphlet", "SLIDE": "Slides", "AGGR": "Database entry", "DBASE": "Database entry"}


def _pubtype(*parts: str) -> str:
    """Publication types in words ('JOUR' becomes 'Journal article'), each once."""
    out = []
    for x in parts:
        x = RIS_TYPES.get(x.strip().upper(), x.strip()) if x else ""
        if x and x.lower() not in (y.lower() for y in out):
            out.append(x)
    return "; ".join(out)


def _lines(text: str) -> list[str]:
    """The lines of a file. A byte-order mark at the start of a line (two exports joined into one file) is dropped."""
    return [ln.lstrip("﻿") if ln[:1] == "﻿" else ln for ln in text.splitlines()]


def _joined(rec: dict) -> dict:
    """{tag: [the lines of each value]} as {tag: [each value]}. A value that runs over many lines is collected as a
    list and joined once, here: adding each line to a growing string takes time that grows with the square of the
    field's length, and a field of a few megabytes would hold the import (and the whole server) up for minutes."""
    return {tag: [" ".join(v) if len(v) > 1 else v[0] for v in vals] for tag, vals in rec.items()}


def parse_ris(text: str) -> list[dict]:
    """A record ends at its end-of-record line (ER with nothing after it). If that line is missing, the next record's
    TY line starts the new record all the same, so two records are never read as one. A line of a wrapped abstract
    that happens to begin 'ER  - ' and goes on is part of the abstract, not the end of the record."""
    recs, cur, last = [], {}, None
    for line in _lines(text):
        line = line.rstrip()
        m = RIS_LINE.match(line)
        tag, val = (m.group(1), (m.group(2) or "").strip()) if m else ("", "")
        if tag == "ER" and not val:
            if cur:
                recs.append(cur)
            cur, last = {}, None
        elif tag and tag != "ER":
            if tag == "TY" and "TY" in cur:       # the record before has no ER line
                recs.append(cur)
                cur = {}
            cur.setdefault(tag, []).append([val])
            last = tag
        elif last and line.strip():
            cur[last][-1].append(line.strip())
    if cur:
        recs.append(cur)
    out = []
    for r in map(_joined, recs):
        g = lambda *ts: next((v for t in ts for v in r.get(t, []) if v), "")
        abstract = max((" ".join(r.get(t, [])) for t in ("AB", "N2")), key=len)
        doi = clean_doi(g("DO")) or next((clean_doi(v) for t in ("M3", "L2", "UR", "N1", "C7", "LK") for v in r.get(t, [])
                                          if clean_doi(v)), "")
        pages = g("SP") + ("-" + g("EP") if g("EP") and g("SP") and g("EP") != g("SP") else "")
        pmid = ""
        for t in ("C5", "AN", "M1", "N1", "U2", "UR"):    # only explicit PubMed identifiers count
            for v in r.get(t, []):
                m = re.search(r"(?i)(?:pmid|pubmed(?:\s*id)?)[:\s]*?(?:NLM)?(\d{6,9})\b", v) or \
                    re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d{6,9})", v)
                if m:
                    pmid = m.group(1)
                    break
            if pmid:
                break
        if not pmid and re.search(r"(?i)medline", g("DB")) and re.fullmatch(r"\d{6,9}", g("AN")):
            pmid = g("AN")                                # Ovid MEDLINE: the accession number is the PubMed ID
        out.append(_clean({
            "title": g("TI", "T1", "CT", "BT"), "abstract": abstract,
            "authors": "; ".join(r.get("AU", []) or r.get("A1", []) or r.get("A2", [])),
            "year": year_of(g("PY", "Y1", "DA", "Y2")), "journal": g("JF", "T2", "JO", "JA", "J2", "SE"),
            "doi": doi, "pmid": pmid, "pubtype": _pubtype(g("M3"), g("PT"), g("TY")),
            "volume": g("VL"), "issue": g("IS"), "pages": pages, "url": g("UR", "L2", "LK"),
            "keywords": "; ".join(r.get("KW", [])), "language": g("LA"), "accession": g("AN", "ID")}))
    return out


# ----------------------------------------------------------------------------------------------- PubMed .nbib
MEDLINE_LINE = re.compile(r"^([A-Z][A-Z0-9]{0,3})\s*- (.*)$")


def parse_medline(text: str) -> list[dict]:
    """A blank line ends a record. If the blank line is missing, the next record's PMID line starts the new record
    all the same (a record has one PubMed ID), so two records are never read as one."""
    recs, cur, last = [], {}, None
    for line in _lines(text) + [""]:
        if not line.strip():
            if cur.get("PMID") or cur.get("TI"):
                recs.append(cur)
            cur, last = {}, None
            continue
        m = MEDLINE_LINE.match(line)
        if m:
            if m.group(1) == "PMID" and "PMID" in cur:      # no blank line after the record before
                recs.append(cur)
                cur = {}
            last = m.group(1)
            cur.setdefault(last, []).append([m.group(2).strip()])
        elif last:
            cur[last][-1].append(line.strip())
    out = []
    for r in map(_joined, recs):
        g = lambda t: (r.get(t) or [""])[0]
        doi = next((clean_doi(x) for x in r.get("LID", []) + r.get("AID", []) if "[doi]" in x), "")
        out.append(_clean({
            "title": g("TI") or g("BTI"), "abstract": " ".join(r.get("AB", [])), "authors": "; ".join(r.get("AU", []) or r.get("FAU", [])),
            "year": year_of(g("DP")), "journal": g("JT") or g("TA"), "doi": doi, "pmid": g("PMID"),
            "pubtype": "; ".join(r.get("PT", [])), "volume": g("VI"), "issue": g("IP"), "pages": g("PG"),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{g('PMID')}/" if g("PMID") else "",
            "keywords": "; ".join(r.get("OT", []) + r.get("MH", [])), "language": g("LA"), "accession": g("PMID")}))
    return out


# ----------------------------------------------------------------------------------------------- Cochrane plain text
# The field names of the Cochrane Library's export. Inside an abstract only these start a new field: a line of the
# abstract may itself begin with capitals and a colon ("AIM: to test ...", "RCT: ..."), and must not end it.
COCHRANE_TAGS = frozenset(("ID", "AU", "TI", "SO", "YR", "VL", "NO", "PG", "PM", "XR", "PT", "KY", "DOI", "AB", "US", "CC",
                           "LA", "PB", "SN", "MH", "EM", "IS", "CR", "DE", "SE"))
COCHRANE_LINE = re.compile(r"^([A-Z]{2,3}):\s?(.*)$")


def parse_cochrane_txt(text: str) -> list[dict]:
    """The Cochrane Library 'plain text' export: blocks starting 'Record #n of N', lines 'XX: value'."""
    blocks = re.split(r"(?m)^﻿?Record #\d+ of \d+\s*$", text)
    out = []
    for b in blocks:
        r, last = {}, None
        for line in b.splitlines():
            m = COCHRANE_LINE.match(line)
            if m and (last != "AB" or m.group(1) in COCHRANE_TAGS):
                last = m.group(1)
                r.setdefault(last, []).append([m.group(2).strip()])
            elif last and line.strip():
                r[last][-1].append(line.strip())
        r = _joined(r)
        if not r.get("TI") and not r.get("ID"):
            continue
        g = lambda t: (r.get(t) or [""])[0]
        pm = re.search(r"(\d{6,9})", " ".join(r.get("PM", [])))
        out.append(_clean({
            "title": g("TI"), "abstract": " ".join(r.get("AB", [])), "authors": "; ".join(a.strip() for x in r.get("AU", []) for a in x.split(";") if a.strip()),
            "year": year_of(g("YR")), "journal": g("SO"), "doi": clean_doi(g("DOI") or " ".join(r.get("US", []))),
            "pmid": pm.group(1) if pm else "", "pubtype": g("PT"), "volume": g("VL"), "issue": g("NO"), "pages": g("PG"),
            "url": g("US"), "keywords": g("KY"), "language": g("LA"), "accession": g("ID")}))
    return out


# ----------------------------------------------------------------------------------------------- Web of Science tagged
WOS_LINE = re.compile(r"^([A-Z][A-Z0-9]) (.*)$")


def parse_wos_tagged(text: str) -> list[dict]:
    """A record ends at its ER line. If that line is missing, the next record's PT line (the first line of every
    record) starts the new record all the same, so two records are never read as one; and a last record with no ER
    line is still read."""
    recs, cur, last = [], {}, None
    for line in _lines(text):
        if line.startswith("ER"):
            if cur:
                recs.append(cur)
            cur, last = {}, None
            continue
        m = WOS_LINE.match(line)
        if m:
            if m.group(1) == "PT" and "PT" in cur:        # the record before has no ER line
                recs.append(cur)
                cur = {}
            last = m.group(1)
            cur.setdefault(last, []).append(m.group(2).strip())
        elif last and line.startswith("   "):
            cur[last].append(line.strip())
    if "TI" in cur:
        recs.append(cur)
    out = []
    for r in recs:
        if "TI" not in r:
            continue
        g = lambda t: " ".join(r.get(t, []))
        out.append(_clean({
            "title": g("TI"), "abstract": g("AB"), "authors": "; ".join(r.get("AU", [])), "year": year_of(g("PY")),
            "journal": g("SO"), "doi": clean_doi(g("DI")), "pmid": (r.get("PM") or [""])[0], "pubtype": g("DT"),
            "volume": g("VL"), "issue": g("IS"), "pages": (g("BP") + ("-" + g("EP") if g("EP") else "")) if g("BP") else g("AR"),
            "url": "", "keywords": g("DE"), "language": g("LA"), "accession": g("UT")}))
    return out


# ----------------------------------------------------------------------------------------------- CSV
CSV_ALIAS = {"title": ["title", "ti", "articletitle", "documenttitle", "primarytitle", "studytitle", "publictitle", "scientifictitle"],
             "abstract": ["abstract", "ab", "abstractnote", "abstracttext", "briefsummary", "summary", "description"],
             "authors": ["authors", "author", "au", "authorfullnames", "creators"],
             "year": ["year", "publicationyear", "publishedyear", "py", "date", "datepublished"],
             "journal": ["journal", "sourcetitle", "jo", "secondarytitle", "journaltitle", "publicationtitle",
                         "journalbook", "journalname", "source"], "doi": ["doi", "do"],
             "pmid": ["pmid", "pubmedid"], "pubtype": ["publicationtype", "documenttype", "type", "pt"], "volume": ["volume", "vl"],
             "issue": ["issue", "is"], "pages": ["pages", "pg", "pagerange"], "url": ["url", "ur", "link", "studyurl", "weblink"],
             "keywords": ["keywords", "authorkeywords", "kw"], "language": ["language", "languageoforiginaldocument", "la"],
             "accession": ["accessionnumber", "eid", "covidence#", "id", "nctnumber", "trialid"]}


DATABASE_NAMES = {"scopus", "embase", "medline", "pubmed", "web of science", "cinahl", "psycinfo"}


class _OpenQuote(Exception):
    """A row that cannot be read, starting on this line of the file: a quotation mark never closed, a cell longer
    than the csv module reads (too_long), or a character a CSV file cannot hold (bad)."""

    def __init__(self, line: int, too_long: bool = False, bad: bool = False):
        self.line, self.too_long, self.bad = line, too_long, bad


def _one_row(lines: list, delim: str) -> bool:
    """Whether these lines, read strictly (every quotation mark properly paired), are exactly one row."""
    try:
        return len(list(csv.reader(iter(lines), delimiter=delim, strict=True))) == 1
    except csv.Error:
        return False


def _csv_rows(lines: list, delim: str) -> list:
    """The rows of a CSV file. A cell that opens with a quotation mark runs to the mark that closes it, across line
    breaks (an abstract of several paragraphs). If the mark is never closed, the csv module reads the rows that follow
    as part of that cell, and they vanish without a word; so every row that runs over more than one line is read again
    strictly, and a row whose quotation marks do not pair up is read as the one line it starts on, with its quotation
    marks as plain characters (a title that begins with one, in a file written without quoting), provided that line
    has as many cells as the header. Otherwise the file is refused (_OpenQuote)."""
    rows, i = [], 0
    while i < len(lines):
        reader = csv.reader(itertools.islice(lines, i, None), delimiter=delim)
        start = 1                                   # where the next row starts, counted in lines from lines[i]
        try:
            for row in reader:
                if reader.line_num > start and not _one_row(lines[i + start - 1:i + reader.line_num], delim):
                    raise _OpenQuote(i + start)
                rows.append(row)
                start = reader.line_num + 1
            return rows
        except _OpenQuote:
            err = _OpenQuote(i + start)
        except csv.Error as e:
            err = _OpenQuote(i + start, too_long="field limit" in str(e), bad="field limit" not in str(e))
        if not rows or err.bad or len(lines[err.line - 1]) > csv.field_size_limit():
            raise err
        try:
            plain = next(csv.reader([lines[err.line - 1]], delimiter=delim, quoting=csv.QUOTE_NONE), [])
        except csv.Error:
            raise _OpenQuote(err.line, bad=True) from None
        if len(plain) != len(rows[0]):
            raise err
        rows.append(plain)
        i = err.line
    return rows


def _csv_table(text: str, delim: str, as_before: list | None = None) -> list:
    """as_before: a list for a file a project has already read once (and may have screened): if its quotation marks
    do not pair up it is read as it was read then, rows lost and all, and the line is put in the list, because
    refusing it now would stop the project (a file cannot be taken out once screening has started)."""
    lines = io.StringIO(text, newline="").readlines()
    try:
        return _csv_rows(lines, delim)
    except _OpenQuote as e:
        if as_before is not None and not e.bad and not e.too_long:
            try:
                rows = list(csv.reader(lines, delimiter=delim))
                as_before.append(e.line)
                return rows
            except csv.Error:
                pass
        if e.bad:
            what = (f"The row that starts on line {e.line:,} of this file holds a character that a CSV file cannot "
                    "hold (most often a null character, left by a damaged copy).")
        elif e.too_long:
            what = (f"A cell in the row that starts on line {e.line:,} of this file is longer than ReviewDesk can "
                    f"read (more than {csv.field_size_limit():,} characters). Most often a quotation mark (\") on that "
                    "line is opened and never closed, so the rows after it were read as part of one cell; otherwise the "
                    "cell itself is too long (a whole article in one cell, say).")
        else:
            what = (f"The quotation marks (\") in the row that starts on line {e.line:,} of this file do not pair up: "
                    "one is opened and never closed, so ReviewDesk cannot tell where that row ends, and the rows after "
                    "it would be read as part of one cell and lost.")
        raise FormatError(what + f" Open the file in a text editor (not a spreadsheet), go to line {e.line:,} and "
                          "put it right (close or remove the quotation mark, or shorten the cell); or export the file "
                          "from the database again, as RIS if the database offers it.") from None


def parse_csv(text: str, as_before: list | None = None) -> list[dict]:
    head = text[:20000]
    first = head.splitlines()[0] if head else ""
    try:
        delim = csv.Sniffer().sniff(head, delimiters=",;\t|").delimiter
    except csv.Error:
        delim = max(",;\t", key=first.count)            # Excel in many countries saves with semicolons
    table = _csv_table(text, delim, as_before)
    names = table[0] if table else []
    # as csv.DictReader gives them: a row of nothing is skipped, cells beyond the header's are left out, missing ones are empty
    rows = [dict(zip(names, cells + [""] * (len(names) - len(cells)))) for cells in table[1:] if cells]
    norm = lambda s: re.sub(r"[^a-z0-9#]", "", (s or "").lower())
    out = []
    for row in rows:
        keys = {norm(k): k for k in row if k}
        r = {}
        for fld, al in CSV_ALIAS.items():
            r[fld] = next((str(row[keys[a]]).strip() for a in al if a in keys and str(row[keys[a]] or "").strip()), "")
        if r["journal"].strip().lower() in DATABASE_NAMES:      # a "Source" column naming the database, not the journal
            r["journal"] = ""
        if not r["pages"] and "pagestart" in keys:              # Scopus: "Page start" and "Page end"
            ps, pe = str(row[keys["pagestart"]] or "").strip(), str(row.get(keys.get("pageend", ""), "") or "").strip()
            r["pages"] = ps + (f"-{pe}" if pe and pe != ps else "")
        r["year"] = year_of(r["year"])
        r["doi"] = clean_doi(r["doi"])
        out.append(_clean(r))
    return out


# ----------------------------------------------------------------------------------------------- dispatch
def detect(text: str, filename: str = "") -> str:
    head = text[:5000]
    if re.search(r"(?m)^PMID-\s*\d", head):
        return "PubMed (MEDLINE format)"
    if re.search(r"(?m)^Record #\d+ of \d+", head):
        return "Cochrane Library plain text"
    if re.search(r"(?m)^FN (Clarivate|Thomson)", head) or (re.search(r"(?m)^PT [A-Z]$", head) and re.search(r"(?m)^ER\s*$", text)):
        return "Web of Science tagged text"
    if re.search(r"(?m)^TY\s{1,2}-", head):
        return "RIS"
    if filename.lower().endswith(".csv"):
        return "CSV"
    first = head.splitlines()[0] if head else ""
    if re.search(r"[,;\t]", first) and re.search(r"(?i)title", first):
        return "CSV"
    return ""


def parse_bytes(raw: bytes, filename: str = "", already_read: bool = False) -> tuple[list[dict], dict]:
    """The records in one export file and a note of how it was read. already_read: the project has read this file
    before (Remove duplicates has used it): a CSV file whose quotation marks do not pair up is then read as it was
    read at that time instead of being refused, and the note says on which line ("open_quote_line")."""
    if not raw.strip():
        raise FormatError("This file is empty (it has no text in it). The export may not have finished, or the wrong file "
                          "was chosen: export from the database again.")
    text, enc = decode(raw)
    if not text.strip("\ufeff \r\n\t\x00"):
        raise FormatError("This file has no text in it. Export from the database again.")
    fmt = detect(text, filename)
    if not fmt:
        raise FormatError("This file is not in a format ReviewDesk can read. Export from the database as RIS "
                          "(Ovid, EBSCO, Scopus, Cochrane, Web of Science), PubMed's 'Citation manager' (.nbib) "
                          "format, or CSV.")
    fn = {"RIS": parse_ris, "PubMed (MEDLINE format)": parse_medline, "Cochrane Library plain text": parse_cochrane_txt,
          "Web of Science tagged text": parse_wos_tagged, "CSV": parse_csv}[fmt]
    if fmt == "RIS":
        last = next((ln for ln in reversed(text.splitlines()) if ln.strip()), "")
        if not re.match(r"^ER\s{0,2}-?\s*$", last.strip()):
            raise FormatError("This file seems to be cut short: its last record does not end properly (there is no ER line). "
                              + CUT_SHORT)
    as_before: list = []
    recs = fn(text, as_before if already_read else None) if fmt == "CSV" else fn(text)
    info = {"format": fmt, "encoding": enc, "records": len(recs),
            "no_title": sum(1 for r in recs if not r["title"]),
            "no_abstract": sum(1 for r in recs if not r["abstract"]),
            "with_doi": sum(1 for r in recs if r["doi"]), "with_pmid": sum(1 for r in recs if r["pmid"])}
    if as_before:
        info["open_quote_line"] = as_before[0]
    if not recs:
        raise FormatError(f"The file was recognised as {fmt} but holds no records. Check the export finished.")
    return recs, info
