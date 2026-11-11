"""Word documents (.docx) written with the standard library only.

Enough of Word for review reports: a title, headings (levels 1 to 3), paragraphs with bold, italic, monospace and
highlighted runs, bulleted and numbered lists, verbatim monospace blocks (search strategies, prompts), tables with a
header row that repeats on every page, column widths and a compact font, and page numbers in the footer. A table too
wide for an upright (portrait) page is put in a section of its own, turned on its side (landscape).

The XML follows the order the OOXML schema (ECMA-376, transitional) lays down for each element, because Word is
strict about it: an element out of order makes Word offer to "repair" the file. Text from records is escaped, and
characters XML cannot hold (control characters in some database exports) are dropped.

    d = Document("Studies excluded at full text", subtitle="My review")
    d.heading("Wrong population", 2)
    d.para(["Two reports were ", ("excluded", "b"), "."])
    d.table(["Study", "Title"], [["Smith 2020", "A trial"]], widths=[1, 4])
    data = d.to_bytes()
"""
from __future__ import annotations

import datetime as dt
import io
import re
import unicodedata
import zipfile

A4 = (11906, 16838)                     # width and height in twentieths of a point (twips)
MARGIN = 1134                           # 2 cm
PORTRAIT_WIDTH = A4[0] - 2 * MARGIN     # the width text and tables can use
LANDSCAPE_WIDTH = A4[1] - 2 * MARGIN
MIN_COL = 400                           # the narrowest column; a table with more columns than fit is split into parts
PART_COL = 700                          # the width a column of a part gets at least, on average
FONT, MONO = "Calibri", "Consolas"
INK, HEAD, RULE, HEAD_FILL = "1F2933", "1F3A5F", "8C96A0", "E7EEF6"

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_NOT_XML = re.compile("[^\u0009\u000a\u000d -퟿-�\U00010000-\U0010ffff]")


def clean(s) -> str:
    """Text as XML can hold it: characters XML 1.0 forbids (most control characters) are dropped."""
    return _NOT_XML.sub("", "" if s is None else str(s))


def esc(s) -> str:
    return (clean(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


# ------------------------------------------------------------------------------------------------ runs
# A paragraph's content is a string, or a list of pieces: each a string, or (text, flags) where flags holds any of
# b (bold), i (italic), m (monospace), h (highlighted: a gap for the authors to fill), s (small), g (grey).
def b(text) -> tuple:
    return (text, "b")


def i(text) -> tuple:
    return (text, "i")


def mono(text) -> tuple:
    return (text, "m")


def fill(text) -> tuple:
    """A highlighted gap for the authors to fill in, such as "[Authors to complete: ...]"."""
    return (text, "hb")


def _pieces(content) -> list:
    if content is None:
        return []
    if isinstance(content, (str, int, float)):
        return [(str(content), "")]
    if isinstance(content, tuple) and len(content) == 2 and isinstance(content[1], str) and not isinstance(content[0], tuple):
        return [(str(content[0]), content[1])]
    out = []
    for x in content:
        if isinstance(x, tuple):
            out.append((str(x[0]), x[1] if len(x) > 1 else ""))
        elif x is not None:
            out.append((str(x), ""))
    return out


def _rpr(flags: str, size: int = 0, color: str = "") -> str:
    """Run properties, in the schema's order: rFonts, b, i, color, sz, szCs, highlight."""
    x = ""
    if "m" in flags:
        x += f'<w:rFonts w:ascii="{MONO}" w:hAnsi="{MONO}" w:cs="{MONO}"/>'
    if "b" in flags:
        x += "<w:b/><w:bCs/>"
    if "i" in flags:
        x += "<w:i/><w:iCs/>"
    if "g" in flags or color:
        x += f'<w:color w:val="{color or "56616E"}"/>'
    if "s" in flags and not size:
        size = 18
    if size:
        x += f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
    if "h" in flags:
        x += '<w:highlight w:val="yellow"/>'
    return f"<w:rPr>{x}</w:rPr>" if x else ""


def _text_runs(text: str, rpr: str) -> str:
    """One piece of text as runs: line breaks become <w:br/>, tabs <w:tab/>."""
    out = []
    for k, line in enumerate(clean(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")):
        if k:
            out.append(f"<w:r>{rpr}<w:br/></w:r>")
        parts = line.split("\t")
        for j, part in enumerate(parts):
            if j:
                out.append(f"<w:r>{rpr}<w:tab/></w:r>")
            if part:
                out.append(f'<w:r>{rpr}<w:t xml:space="preserve">{esc(part)}</w:t></w:r>')
    return "".join(out)


def runs(content, size: int = 0, extra: str = "") -> str:
    return "".join(_text_runs(t, _rpr(f + extra, size)) for t, f in _pieces(content))


def _ppr(style: str = "", keep_next: bool = False, num: int = 0, shade: str = "", spacing: str = "", align: str = "",
         rpr: str = "", sect: str = "") -> str:
    """Paragraph properties, in the schema's order: pStyle, keepNext, numPr, shd, spacing, jc, rPr, sectPr."""
    x = f'<w:pStyle w:val="{style}"/>' if style else ""
    if keep_next:
        x += "<w:keepNext/>"
    if num:
        x += f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{num}"/></w:numPr>'
    if shade:
        x += f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>'
    if spacing:
        x += spacing
    if align:
        x += f'<w:jc w:val="{align}"/>'
    x += rpr + sect
    return f"<w:pPr>{x}</w:pPr>" if x else ""


def _p(content, style: str = "", keep_next: bool = False, num: int = 0, size: int = 0, align: str = "",
       extra: str = "") -> str:
    return f"<w:p>{_ppr(style, keep_next, num, align=align)}{runs(content, size, extra)}</w:p>"


class Cell:
    """A table cell with more than text: a background colour (fill, as hex), bold text, or spanning several columns."""

    def __init__(self, content, fill: str = "", bold: bool = False, span: int = 1):
        self.content, self.fill, self.bold, self.span = content, fill, bold, max(1, int(span))


def _span(c) -> int:
    return c.span if isinstance(c, Cell) else 1


def _cell_text(c) -> str:
    c = c.content if isinstance(c, Cell) else c
    return " ".join(t for t, _ in _pieces(c))


# ------------------------------------------------------------------------------------------------ the document
class Document:
    def __init__(self, title: str, subtitle: str = "", landscape: bool = False, footer: str = ""):
        self.title = clean(title)
        self.base = "landscape" if landscape else "portrait"
        self.footer = clean(footer or title)
        self._blocks: list = []          # (orientation, xml)
        self._lists = 0                  # numbered lists so far (each restarts at 1)
        self.landscape_tables = 0
        self._block(_p(self.title, "Title"))
        if subtitle:
            self._block(_p(subtitle, "Subtitle"))

    def _block(self, xml: str, orient: str = "", kind: str = "") -> None:
        self._blocks.append((orient or self.base, xml, kind))

    # -------------------------------------------------------------------------------------------- text
    def heading(self, content, level: int = 1) -> None:
        self._block(_p(content, f"Heading{max(1, min(3, int(level)))}"), kind="heading")

    def para(self, content, style: str = "", size: int = 0, align: str = "", keep_next: bool = False) -> None:
        self._block(_p(content, style, keep_next=keep_next, size=size, align=align))

    def note(self, content) -> None:
        """A smaller, grey paragraph: a note under a table, or how something was worked out."""
        self._block(_p(content, "Note"))

    def fill_in(self, text: str) -> None:
        """A highlighted line for the authors to complete."""
        self._block(_p([fill(text)]))

    def bullets(self, items: list, numbered: bool = False) -> None:
        if not items:
            return
        num = 1
        if numbered:
            self._lists += 1
            num = 1 + self._lists
        for x in items:
            self._block(_p(x, "ListParagraph", num=num))

    def pre(self, text: str, size: int = 17) -> None:
        """Verbatim text in a monospace font (a search strategy, a prompt), line breaks kept."""
        body = runs([(text or "", "m")], size=size)
        self._block(f'<w:p>{_ppr("Code")}{body}</w:p>')

    def page_break(self) -> None:
        self._block('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

    # -------------------------------------------------------------------------------------------- tables
    def table(self, header, rows: list, widths: list | None = None, compact: bool = False, landscape=None,
              caption: str = "", notes: list | None = None, first_bold: bool = False, keep_rows: bool = False,
              repeat: int = 1) -> str:
        """A table. header: the column headings (None for none); rows: lists of cells (text, runs or Cell).
        widths: relative widths (any numbers); otherwise worked out from the text. landscape: True, False, or None
        to turn the page on its side only when the table is too wide for an upright page (the headings just before
        it go with it). keep_rows: never split a row across two pages. A table with more columns than the page can
        hold (a tool the lead defines may have 60 items) is split into parts, one after another, each repeating the
        first `repeat` columns (the ones that say what each row is). Returns the orientation."""
        ncol = len(header) if header else max((sum(_span(c) for c in r) for r in rows), default=1)
        rows = [_fit(list(r), ncol) for r in rows]
        if landscape is None:
            orient = "landscape" if self.base == "landscape" or too_wide(header, rows, compact) else "portrait"
        else:
            orient = "landscape" if landscape else "portrait"
        total = LANDSCAPE_WIDTH if orient == "landscape" else PORTRAIT_WIDTH
        repeat = max(1, min(int(repeat), ncol - 1))
        if ncol * MIN_COL > total and ncol > repeat + 1:
            per = max(1, total // PART_COL - repeat)
            parts = -(-(ncol - repeat) // per)
            per = -(-(ncol - repeat) // parts)                  # the columns shared out evenly between the parts
            groups = [list(range(lo, min(lo + per, ncol))) for lo in range(repeat, ncol, per)]
            for g, cols_ in enumerate(groups, 1):
                keep = list(range(repeat)) + cols_
                self.table([header[c] for c in keep] if header else None, [_clip(r, keep) for r in rows],
                           widths=[widths[c] for c in keep] if widths and len(widths) == ncol else None, compact=compact,
                           landscape=orient == "landscape",
                           caption=f"{caption} (part {g} of {len(groups)})" if caption else "",
                           notes=notes if g == len(groups) else None, first_bold=first_bold, keep_rows=keep_rows,
                           repeat=repeat)
            return orient
        if orient == "landscape" and self.base != "landscape":
            self.landscape_tables += 1
        k = len(self._blocks)
        while k and self._blocks[k - 1][2] == "heading" and self._blocks[k - 1][0] != orient:
            k -= 1                                   # a heading stays with its table, on the same kind of page
        self._blocks[k:] = [(orient, xml, kind) for _, xml, kind in self._blocks[k:]]
        weights = list(widths) if widths and len(widths) == ncol else auto_widths(header, rows)
        s = float(sum(weights)) or 1.0
        cols = fit_words([max(MIN_COL, int(total * w / s)) for w in weights], min_widths(header, rows, compact), total)
        size = 18 if compact else 20
        style = "TableTextSmall" if compact else "TableText"
        line = f'w:val="single" w:sz="4" w:space="0" w:color="{RULE}"'
        borders = "".join(f"<w:{k} {line}/>" for k in ("top", "left", "bottom", "right", "insideH", "insideV"))
        mar = '<w:top w:w="45" w:type="dxa"/><w:left w:w="85" w:type="dxa"/><w:bottom w:w="45" w:type="dxa"/><w:right w:w="85" w:type="dxa"/>'
        x = [f'<w:tbl><w:tblPr><w:tblW w:w="{total}" w:type="dxa"/><w:tblBorders>{borders}</w:tblBorders>'
             f'<w:tblLayout w:type="fixed"/><w:tblCellMar>{mar}</w:tblCellMar>'
             + (f'<w:tblCaption w:val="{esc(clean(caption)[:250])}"/>' if caption else "") + "</w:tblPr>"]
        x.append("<w:tblGrid>" + "".join(f'<w:gridCol w:w="{w}"/>' for w in cols) + "</w:tblGrid>")

        def cell(c, w, head=False, bold=False):
            fill_, content, span = "", c, 1
            if isinstance(c, Cell):
                fill_, content, bold, span = c.fill, c.content, bold or c.bold, c.span
            if head:
                fill_ = fill_ or HEAD_FILL
            tcpr = (f'<w:tcW w:w="{w}" w:type="dxa"/>' + (f'<w:gridSpan w:val="{span}"/>' if span > 1 else "")
                    + (f'<w:shd w:val="clear" w:color="auto" w:fill="{fill_}"/>' if fill_ else ""))
            paras = str(content).split("\n\n") if isinstance(content, str) else [content]
            body = "".join(f"<w:p>{_ppr(style)}{runs(pc, size, 'b' if head or bold else '')}</w:p>" for pc in paras)
            return f"<w:tc><w:tcPr>{tcpr}</w:tcPr>{body or f'<w:p>{_ppr(style)}</w:p>'}</w:tc>"

        if header:
            x.append("<w:tr><w:trPr><w:cantSplit/><w:tblHeader/></w:trPr>"
                     + "".join(cell(h, w, head=True) for h, w in zip(header, cols)) + "</w:tr>")
        trpr = "<w:trPr><w:cantSplit/></w:trPr>" if keep_rows else ""
        for r in rows:
            cells, at = [], 0
            for k, c in enumerate(r):
                cells.append(cell(c, sum(cols[at:at + _span(c)]), bold=first_bold and k == 0))
                at += _span(c)
            x.append("<w:tr>" + trpr + "".join(cells) + "</w:tr>")
        x.append("</w:tbl>")
        if caption:
            self._block(_p(caption, "Caption", keep_next=True), orient)
        self._block("".join(x), orient)
        for n in notes or []:
            self._block(_p(n, "Note"), orient)
        self._block(f'<w:p>{_ppr("Spacer")}</w:p>', orient)      # keeps two tables apart (Word would join them)
        return orient

    def fields(self, pairs: list, caption: str = "") -> None:
        """A two-column table of labels and values (no header row)."""
        self.table(None, [[Cell(k, bold=True), v] for k, v in pairs], widths=[1, 2.6], landscape=False, caption=caption)

    # -------------------------------------------------------------------------------------------- the file
    def _sect(self, orient: str) -> str:
        w, hgt = (A4[1], A4[0]) if orient == "landscape" else A4
        o = ' w:orient="landscape"' if orient == "landscape" else ""
        return (f'<w:sectPr><w:footerReference w:type="default" r:id="rId4"/><w:type w:val="nextPage"/>'
                f'<w:pgSz w:w="{w}" w:h="{hgt}"{o}/><w:pgMar w:top="{MARGIN}" w:right="{MARGIN}" w:bottom="{MARGIN}" '
                f'w:left="{MARGIN}" w:header="567" w:footer="567" w:gutter="0"/><w:cols w:space="708"/>'
                '<w:docGrid w:linePitch="360"/></w:sectPr>')

    def _body(self) -> str:
        groups: list = []
        for orient, xml, _ in self._blocks:
            if groups and groups[-1][0] == orient:
                groups[-1][1].append(xml)
            else:
                groups.append((orient, [xml]))
        out = []
        for k, (orient, xmls) in enumerate(groups):
            out += xmls
            if k < len(groups) - 1:            # a section ends with a paragraph that carries its page set-up
                out.append('<w:p><w:pPr><w:pStyle w:val="Spacer"/><w:spacing w:before="0" w:after="0" w:line="20" '
                           'w:lineRule="exact"/><w:rPr><w:sz w:val="2"/></w:rPr>' + self._sect(orient) + "</w:pPr></w:p>")
        return "".join(out) + self._sect(groups[-1][0] if groups else self.base)

    def xml(self) -> str:
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document xmlns:w="{W_NS}" xmlns:r="{R_NS}">'
                f"<w:body>{self._body()}</w:body></w:document>")

    def to_bytes(self) -> bytes:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        nums = 1 + self._lists
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", CONTENT_TYPES)
            z.writestr("_rels/.rels", ROOT_RELS)
            z.writestr("docProps/core.xml", _core(self.title, stamp))
            z.writestr("docProps/app.xml", APP)
            z.writestr("word/_rels/document.xml.rels", DOC_RELS)
            z.writestr("word/document.xml", self.xml())
            z.writestr("word/styles.xml", STYLES)
            z.writestr("word/settings.xml", SETTINGS)
            z.writestr("word/numbering.xml", _numbering(nums))
            z.writestr("word/footer1.xml", _footer(self.footer))
        return buf.getvalue()


def _clip(row: list, keep: list) -> list:
    """A row (filling every column) with only the columns in keep, in order; a spanning cell keeps the columns it
    covers that are kept (keep is a run of first columns and then a run of others, so they are always side by side)."""
    out, at = [], 0
    for c in row:
        n = sum(1 for k in range(at, at + _span(c)) if k in keep)
        at += _span(c)
        if n:
            out.append(Cell(c.content, c.fill, c.bold, n) if isinstance(c, Cell) else c)
    return out


def _fit(row: list, ncol: int) -> list:
    """A row cut or padded to fill exactly ncol columns (a spanning cell counts for the columns it covers)."""
    out, used = [], 0
    for c in row:
        if used + _span(c) > ncol:
            break
        out.append(c)
        used += _span(c)
    return out + [""] * (ncol - used)


# ------------------------------------------------------------------------------------------------ widths
CHAR = {False: 105, True: 95}           # an average character of 10 pt (9 pt) Calibri, in twips, with some room
PAD = 190                               # cell margins and borders


def _col_text(header, rows, c) -> list:
    return ([_cell_text(header[c])] if header else []) + [_cell_text(r[c]) for r in rows if len(r) > c and _span(r[c]) == 1
                                                        and all(_span(x) == 1 for x in r)]


def auto_widths(header, rows) -> list:
    """Relative column widths from the text: room for the longest word, more for columns of long text."""
    ncol = len(header) if header else (sum(_span(c) for c in rows[0]) if rows else 1)
    out = []
    for c in range(ncol):
        texts = _col_text(header, rows, c)
        longest = max((min(len(w), 22) for t in texts for w in t.split()), default=4)
        body = [len(_cell_text(r[c])) for r in rows if all(_span(x) == 1 for x in r)] or [len(texts[0]) if texts else 4]
        mean = sum(body) / len(body)
        out.append(max(longest + 1.5, min(mean, 60) ** 0.9, 4))
    return out


def min_widths(header, rows, compact: bool = False) -> list:
    """The width each column needs so that its longest word is not broken across lines (headings are bold, so a
    little wider); words over 22 characters (web addresses, DOIs) may still break."""
    ncol = len(header) if header else (sum(_span(c) for c in rows[0]) if rows else 1)
    cw = CHAR[compact]

    def size(part: str) -> float:               # symbols such as the GRADE circles are about three letters wide
        return sum(3.2 if unicodedata.category(ch) in ("So", "Sm") else 1 for ch in part)

    def longest(text: str) -> int:              # a line may break after a hyphen or a slash, as Word breaks it
        return max((min(size(part), 22) for w in text.split() for part in re.split(r"(?<=[-/])", w)), default=0)
    out = []
    for c in range(ncol):
        head = longest(_cell_text(header[c])) * 1.08 if header else 0
        body = max((longest(_cell_text(r[c])) for r in rows if len(r) > c and all(_span(x) == 1 for x in r)), default=0)
        out.append(int(max(head, body, 2) * cw + PAD))
    return out


def fit_words(cols: list, need: list, total: int) -> list:
    """Column widths that add up to total, widened where a column is too narrow for its longest word and narrowed
    where there is room to spare, when the page is wide enough for every column's longest word."""
    cols = list(cols)
    if len(need) == len(cols) and sum(need) <= total and any(c < n for c, n in zip(cols, need)):
        cols = [max(c, n) for c, n in zip(cols, need)]
        over = sum(cols) - total
        slack = [c - n for c, n in zip(cols, need)]
        room = sum(slack)
        if room > 0:
            cols = [c - int(over * sl / room) for c, sl in zip(cols, slack)]
    cols[-1] += total - sum(cols)
    if cols[-1] < MIN_COL:                               # rounding never squeezes the last column to nothing
        short = MIN_COL - cols[-1]
        cols[-1] = MIN_COL
        for k in sorted(range(len(cols) - 1), key=lambda i: -cols[i]):     # taken from the widest, never below MIN_COL
            take = min(short, max(0, cols[k] - MIN_COL))
            cols[k] -= take
            short -= take
            if not short:
                break
    return cols


def too_wide(header, rows, compact: bool = False) -> bool:
    """True when a table needs more than an upright page: its columns would have to break words, or it has more
    columns than fit comfortably."""
    ncol = len(header) if header else (sum(_span(c) for c in rows[0]) if rows else 1)
    if ncol >= 9:
        return True
    cw = CHAR[compact]
    need = 0
    comfortable = 0
    for c in range(ncol):
        texts = _col_text(header, rows, c)
        longest = max((min(len(w), 22) for t in texts for w in t.split()), default=4)
        body = [len(_cell_text(r[c])) for r in rows if all(_span(x) == 1 for x in r)] or [4]
        need += longest * cw + PAD
        comfortable += min(sum(body) / len(body), 40) * cw + PAD
    return need > PORTRAIT_WIDTH or comfortable > 1.7 * PORTRAIT_WIDTH


# ------------------------------------------------------------------------------------------------ fixed parts
CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
    '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
    '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
    "</Types>")

ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
    '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
    "</Relationships>")

DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>'
    '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
    '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
    "</Relationships>")

APP = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
       '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
       'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>ReviewDesk</Application></Properties>')

SETTINGS = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:settings xmlns:w="{W_NS}">'
            '<w:zoom w:percent="100"/><w:defaultTabStop w:val="709"/><w:characterSpacingControl w:val="doNotCompress"/>'
            '<w:compat><w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat>'
            '<w:themeFontLang w:val="en-GB"/></w:settings>')


def _core(title: str, stamp: str) -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>{esc(title)}</dc:title><dc:creator>ReviewDesk</dc:creator><dc:language>en-GB</dc:language>"
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{stamp}</dcterms:modified></cp:coreProperties>')


def _field(code: str) -> str:
    return ('<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            f'<w:r><w:instrText xml:space="preserve"> {code} </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>')


def _footer(text: str) -> str:
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:ftr xmlns:w="{W_NS}" xmlns:r="{R_NS}">'
            '<w:p><w:pPr><w:pStyle w:val="Footer"/><w:jc w:val="center"/></w:pPr>'
            f'<w:r><w:t xml:space="preserve">{esc(text)}  ·  Page </w:t></w:r>{_field("PAGE")}'
            f'<w:r><w:t xml:space="preserve"> of </w:t></w:r>{_field("NUMPAGES")}</w:p></w:ftr>')


def _numbering(nums: int) -> str:
    """Bullets (list 1), and a numbered list of its own for every numbered list, so each starts at 1."""
    bullet = ('<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0">'
              '<w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/>'
              '<w:pPr><w:ind w:left="567" w:hanging="283"/></w:pPr></w:lvl></w:abstractNum>')
    decimal = "".join(
        f'<w:abstractNum w:abstractNumId="{k}"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0">'
        '<w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:lvlJc w:val="left"/>'
        '<w:pPr><w:ind w:left="567" w:hanging="340"/></w:pPr></w:lvl></w:abstractNum>' for k in range(1, nums))
    num = "".join(f'<w:num w:numId="{k + 1}"><w:abstractNumId w:val="{k}"/></w:num>' for k in range(nums))
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:numbering xmlns:w="{W_NS}">{bullet}{decimal}{num}</w:numbering>'


def _style(sid: str, name: str, ppr: str = "", rpr: str = "", based: str = "Normal", nxt: str = "", q: bool = True,
           kind: str = "paragraph", default: bool = False, ui: int = 0) -> str:
    """A style, its parts in the schema's order: name, basedOn, next, uiPriority, qFormat, pPr, rPr."""
    return (f'<w:style w:type="{kind}"' + (' w:default="1"' if default else "") + f' w:styleId="{sid}"><w:name w:val="{name}"/>'
            + (f'<w:basedOn w:val="{based}"/>' if based else "") + (f'<w:next w:val="{nxt}"/>' if nxt else "")
            + (f'<w:uiPriority w:val="{ui}"/>' if ui else "") + ("<w:qFormat/>" if q else "")
            + (f"<w:pPr>{ppr}</w:pPr>" if ppr else "") + (f"<w:rPr>{rpr}</w:rPr>" if rpr else "") + "</w:style>")


def _font(name: str) -> str:
    return f'<w:rFonts w:ascii="{name}" w:hAnsi="{name}" w:eastAsia="{name}" w:cs="{name}"/>'


def _sz(n: int) -> str:
    return f'<w:sz w:val="{n}"/><w:szCs w:val="{n}"/>'


STYLES = (
    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:styles xmlns:w="{W_NS}">'
    f'<w:docDefaults><w:rPrDefault><w:rPr>{_font(FONT)}{_sz(22)}<w:lang w:val="en-GB" w:eastAsia="en-GB" w:bidi="ar-SA"/>'
    '</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="264" w:lineRule="auto"/></w:pPr>'
    "</w:pPrDefault></w:docDefaults>"
    + _style("Normal", "Normal", based="", default=True, rpr=f'<w:color w:val="{INK}"/>')
    + _style("Title", "Title", '<w:spacing w:after="80"/>', f'<w:b/><w:bCs/><w:color w:val="{HEAD}"/>{_sz(40)}', nxt="Normal", ui=10)
    + _style("Subtitle", "Subtitle", '<w:spacing w:after="240"/>', f'<w:color w:val="56616E"/>{_sz(24)}', nxt="Normal", ui=11)
    + _style("Heading1", "heading 1", '<w:keepNext/><w:keepLines/><w:spacing w:before="360" w:after="120"/><w:outlineLvl w:val="0"/>',
             f'<w:b/><w:bCs/><w:color w:val="{HEAD}"/>{_sz(30)}', nxt="Normal", ui=9)
    + _style("Heading2", "heading 2", '<w:keepNext/><w:keepLines/><w:spacing w:before="260" w:after="100"/><w:outlineLvl w:val="1"/>',
             f'<w:b/><w:bCs/><w:color w:val="{HEAD}"/>{_sz(25)}', nxt="Normal", ui=9)
    + _style("Heading3", "heading 3", '<w:keepNext/><w:keepLines/><w:spacing w:before="200" w:after="80"/><w:outlineLvl w:val="2"/>',
             f'<w:b/><w:bCs/><w:color w:val="{INK}"/>{_sz(22)}', nxt="Normal", ui=9)
    + _style("Caption", "caption", '<w:keepNext/><w:spacing w:before="120" w:after="80"/>', f"<w:b/><w:bCs/>{_sz(20)}", nxt="Normal", ui=35)
    + _style("Note", "Note", '<w:spacing w:before="40" w:after="60" w:line="240" w:lineRule="auto"/>', f'<w:color w:val="56616E"/>{_sz(18)}')
    + _style("ListParagraph", "List Paragraph", '<w:spacing w:after="60"/><w:ind w:left="567"/><w:contextualSpacing/>', ui=34)
    + _style("Code", "Code", '<w:shd w:val="clear" w:color="auto" w:fill="F3F4F6"/><w:spacing w:before="60" w:after="160" w:line="240" w:lineRule="auto"/>',
             f"{_font(MONO)}{_sz(17)}")
    + _style("TableText", "Table text", '<w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/>', _sz(20))
    + _style("TableTextSmall", "Table text small", '<w:spacing w:before="0" w:after="0" w:line="228" w:lineRule="auto"/>', _sz(18))
    + _style("Spacer", "Spacer", '<w:spacing w:before="0" w:after="160" w:line="240" w:lineRule="auto"/>', _sz(12), q=False)
    + _style("Footer", "footer", '<w:spacing w:after="0"/>', f'<w:color w:val="56616E"/>{_sz(17)}', ui=99)
    + "</w:styles>")
