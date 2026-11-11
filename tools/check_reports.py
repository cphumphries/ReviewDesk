"""Check every Word document the reporting outputs make: LibreOffice must convert each one to PDF, and each must
keep the element order the OOXML schema lays down (Word offers to "repair" a file that does not).

It builds the demo review in tests/demo_review.py (a lead and a reviewer, every full-text decision, extraction, risk
of bias, and GRADE ratings: an intervention outcome, a test with its accuracy figures and its true and false results, a
statement kept with the lead's reason and a rating the lead changed), makes every .docx each person may download,
checks them, converts them with LibreOffice and reads the text back with pdftotext. The Summary of findings and
evidence profile must be among them, for both people. Not a pytest test, because LibreOffice may not be installed.

    python tools/check_reports.py              # exit code 0 when every document passes
    python tools/check_reports.py --keep DIR   # keep the files, PDFs and (with --png) page pictures in DIR
"""
from __future__ import annotations

import argparse
import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from xml.etree import ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
if not os.environ.get("REVIEWDESK_HOME"):
    os.environ["REVIEWDESK_HOME"] = tempfile.mkdtemp(prefix="reviewdesk-check-home-")
    atexit.register(shutil.rmtree, os.environ["REVIEWDESK_HOME"], ignore_errors=True)      # nothing is left behind
os.environ["REVIEWDESK_NO_REOPEN"] = "1"

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# The order of the child elements the schema allows (ECMA-376 part 1, transitional), for every element the writer
# uses. Names on one line with "|" share a place.
ORDER = {
    "pPr": "pStyle keepNext keepLines pageBreakBefore framePr widowControl numPr suppressLineNumbers pBdr shd tabs "
           "suppressAutoHyphens kinsoku wordWrap overflowPunct topLinePunct autoSpaceDE autoSpaceDN bidi adjustRightInd "
           "snapToGrid spacing ind contextualSpacing mirrorIndents suppressOverlap jc textDirection textAlignment "
           "textboxTightWrap outlineLvl divId cnfStyle rPr sectPr pPrChange",
    "rPr": "rStyle rFonts b bCs i iCs caps smallCaps strike dstrike outline shadow emboss imprint noProof snapToGrid vanish "
           "webHidden color spacing w kern position sz szCs highlight u effect bdr shd fitText vertAlign rtl cs em lang "
           "eastAsianLayout specVanish oMath",
    "sectPr": "headerReference|footerReference footnotePr endnotePr type pgSz pgMar paperSrc pgBorders lnNumType pgNumType "
              "cols formProt vAlign noEndnote titlePg textDirection bidi rtlGutter docGrid printerSettings sectPrChange",
    "tblPr": "tblStyle tblpPr tblOverlap bidiVisual tblStyleRowBandSize tblStyleColBandSize tblW jc tblCellSpacing tblInd "
             "tblBorders shd tblLayout tblCellMar tblLook tblCaption tblDescription tblPrChange",
    "tcPr": "cnfStyle tcW gridSpan hMerge vMerge tcBorders shd noWrap tcMar textDirection tcFitText vAlign hideMark",
    "tblBorders": "top left|start bottom right|end insideH insideV",
    "tblCellMar": "top left|start bottom right|end",
    "style": "name aliases basedOn next link autoRedefine hidden uiPriority semiHidden unhideWhenUsed qFormat locked personal "
             "personalCompose personalReply rsid pPr rPr tblPr trPr tcPr tblStylePr",
    "lvl": "start numFmt lvlRestart pStyle isLgl suff lvlText lvlPicBulletId legacy lvlJc pPr rPr",
    "abstractNum": "nsid multiLevelType tmpl name styleLink numStyleLink lvl",
    "numbering": "numPicBullet abstractNum num numIdMacAtCleanup",
    "tbl": "tblPr tblGrid tr",
    "tr": "tblPrEx trPr tc",
    "styles": "docDefaults latentStyles style",
    "settings": "writeProtection view zoom removePersonalInformation removeDateAndTime defaultTabStop autoHyphenation "
                "characterSpacingControl compat docVars rsids mathPr attachedSchema themeFontLang clrSchemeMapping",
}
RANK = {k: {n: i for i, names in enumerate(v.split()) for n in names.split("|")} for k, v in ORDER.items()}


# What the Summary of findings and evidence profile of the demo review must show (tests/demo_review.py, grade())
SOF_WORDS = ("Summary of findings 1", "Summary of findings 2", "Evidence profile", "Test result", "True positives",
             "False negatives", "True negatives", "False positives", "pre-test probability 20.0%", "Changed by the project lead",
             "Worded more strongly than GRADE's standard wording")


def local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def order_problems(name: str, xml: bytes) -> list:
    """Children out of the schema's order, unknown children, and the structural rules Word checks."""
    out = []
    root = ET.fromstring(xml)
    for el in root.iter():
        tag = local(el.tag)
        kids = [local(c.tag) for c in el]
        if tag in RANK:
            last = -1
            for k in kids:
                r = RANK[tag].get(k)
                if r is None:
                    out.append(f"{name}: <{k}> is not expected inside <{tag}>")
                    continue
                if r < last:
                    out.append(f"{name}: <{k}> is out of order inside <{tag}> ({' '.join(kids)})")
                last = max(last, r)
        if tag == "p" and "pPr" in kids and kids.index("pPr") != 0:
            out.append(f"{name}: <pPr> is not the first child of a paragraph")
        if tag == "r" and "rPr" in kids and kids.index("rPr") != 0:
            out.append(f"{name}: <rPr> is not the first child of a run")
        if tag == "tc" and (not kids or kids[-1] != "p"):
            out.append(f"{name}: a table cell does not end with a paragraph")
        if tag == "tbl":
            grid = el.find(W + "tblGrid")
            ncol = len(grid) if grid is not None else 0
            for tr in el.findall(W + "tr"):
                span = sum(int((tc.find(W + "tcPr/" + W + "gridSpan").get(W + "val") if tc.find(W + "tcPr/" + W + "gridSpan") is not None else 1))
                           for tc in tr.findall(W + "tc"))
                if span != ncol:
                    out.append(f"{name}: a table row covers {span} columns but the table has {ncol}")
        if tag == "body" and (not kids or kids[-1] != "sectPr"):
            out.append(f"{name}: the body does not end with the section properties")
    return out


def check_docx(label: str, data: bytes) -> list:
    out = []
    try:
        z = zipfile.ZipFile(__import__("io").BytesIO(data))
    except zipfile.BadZipFile:
        return [f"{label}: not a zip file"]
    names = z.namelist()
    for need in ("[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/styles.xml", "word/_rels/document.xml.rels"):
        if need not in names:
            out.append(f"{label}: {need} is missing")
    for n in names:
        if n.endswith((".xml", ".rels")):
            try:
                ET.fromstring(z.read(n))
            except ET.ParseError as e:
                out.append(f"{label}: {n} is not well-formed XML ({e})")
                continue
            if n.startswith("word/"):
                out += order_problems(f"{label} {n}", z.read(n))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--keep", help="a folder to keep the documents, PDFs and pictures in")
    ap.add_argument("--png", action="store_true", help="also draw each PDF page as a picture (pdftoppm, 60 dpi)")
    a = ap.parse_args()
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        print("LibreOffice (soffice) is not installed, so the documents cannot be converted.")
        return 2
    work = os.path.abspath(a.keep) if a.keep else tempfile.mkdtemp(prefix="reviewdesk-check-")
    os.makedirs(work, exist_ok=True)
    import demo_review
    from reviewdesk import certainty, reporting
    if getattr(certainty, "evidence_profile", None) is None:      # the GRADE part may not be merged yet: use a stand-in
        certainty.evidence_profile = demo_review.fake_profile
    for sub in ("project", "docx", "pdf", "png"):           # nothing left over from an earlier run
        shutil.rmtree(os.path.join(work, sub), ignore_errors=True)
    proj = os.path.join(work, "project")
    info = demo_review.build(proj)
    p = info["p"]
    if certainty.evidence_profile is not demo_review.fake_profile:
        demo_review.grade(info)                  # the GRADE outcomes: the Summary of findings and evidence profile
    problems, files = [], []
    for me in ("CH", "NH"):
        d = os.path.join(work, "docx", me)
        os.makedirs(d, exist_ok=True)
        for kind in reporting.DOWNLOADS:
            name = reporting.file_name(kind)
            if not name.endswith(".docx") or reporting.status(p, me, kind)[0] != "ready":
                continue
            data = reporting.make(p, me, kind)
            problems += check_docx(f"{me}/{name}", data)
            path = os.path.join(d, name)
            with open(path, "wb") as f:
                f.write(data)
            files.append((me, path))
        pack = zipfile.ZipFile(__import__("io").BytesIO(reporting.pack(p, me)))
        for n in pack.namelist():
            if n.endswith(".docx"):
                problems += check_docx(f"{me} pack/{n}", pack.read(n))
    profile = os.path.join(work, "lo-profile")
    for me in ("CH", "NH"):
        out = os.path.join(work, "pdf", me)
        os.makedirs(out, exist_ok=True)
        mine = [f for who, f in files if who == me]
        r = subprocess.run([soffice, f"-env:UserInstallation=file://{profile}", "--headless", "--convert-to", "pdf", "--outdir", out]
                           + mine, capture_output=True, text=True, timeout=600)
        if r.returncode:
            problems.append(f"LibreOffice failed ({r.returncode}): {r.stderr.strip()[:400]}")
        for f in mine:
            pdf = os.path.join(out, os.path.splitext(os.path.basename(f))[0] + ".pdf")
            if not os.path.isfile(pdf) or os.path.getsize(pdf) < 500:
                problems.append(f"{me}/{os.path.basename(f)}: LibreOffice did not convert it")
                continue
            if shutil.which("pdftotext"):
                text = subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, text=True).stdout
                title = os.path.splitext(os.path.basename(f))[0]
                if len(text.strip()) < 40:
                    problems.append(f"{me}/{os.path.basename(f)}: the PDF has no text")
                elif "Page 1 of" not in text:
                    problems.append(f"{me}/{os.path.basename(f)}: no page number in the footer")
                if os.path.basename(f) == reporting.file_name("rep_sof"):
                    flat = " ".join(text.split())
                    missing = [w for w in SOF_WORDS if w not in flat] + (["(nothing marked Not final)"] if "Not final" in flat else [])
                    if missing:
                        problems.append(f"{me}/{os.path.basename(f)}: the GRADE outputs lack {', '.join(missing)}")
                print(f"  {me}  {title[:60]:60s} {text.count(chr(12)):3d} pages")
            if a.png and shutil.which("pdftoppm"):
                png = os.path.join(work, "png", me)
                os.makedirs(png, exist_ok=True)
                subprocess.run(["pdftoppm", "-png", "-r", "60", pdf, os.path.join(png, os.path.splitext(os.path.basename(f))[0])],
                               capture_output=True)
    for me in ("CH", "NH"):                     # the GRADE outputs are checked for both people
        if not any(who == me and os.path.basename(f) == reporting.file_name("rep_sof") for who, f in files):
            problems.append(f"{me}: no {reporting.file_name('rep_sof')} was made ({reporting.status(p, me, 'rep_sof')[1]})")
    for x in problems:
        print("PROBLEM:", x)
    print(f"{len(files)} documents checked in {work}: " + ("all passed." if not problems else f"{len(problems)} problems."))
    if not a.keep:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
