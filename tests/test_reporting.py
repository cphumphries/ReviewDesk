"""The reporting outputs: the Word writer, the "ongoing study" decision end to end, every output and the reporting
pack, for the project lead and for a reviewer, with the blinding rules the rest of ReviewDesk keeps.

The review is built through ReviewDesk's own functions (tests/demo_review.py): two reviewers, duplicates, a
calibration round, the agreement sample, the AI second screener (its answers written as the AI runner writes them,
no service is called), other methods, an update search, every full-text decision, extraction and risk of bias."""
import csv
import http.client
import importlib.util
import io
import json
import os
import threading
import zipfile
from xml.etree import ElementTree as ET

import pytest

import demo_review
from reviewdesk import certainty, docx, exports, prisma, reporting, review, screening, server

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("check_reports", os.path.join(os.path.dirname(HERE), "tools", "check_reports.py"))
check_reports = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_reports)

LEAD_ONLY_FILES = {"records.csv", "all decisions.csv", "AI decisions.csv", "AI second screener report.docx",
                   "AI prompts and settings.txt"}


# ------------------------------------------------------------------------------------------------ helpers
def paragraphs(data: bytes) -> list:
    """The text of each paragraph of a .docx, line breaks and tabs kept."""
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    out = []
    for p in root.iter(W + "p"):
        bits = []
        for el in p.iter():
            if el.tag == W + "t":
                bits.append(el.text or "")
            elif el.tag == W + "br":
                bits.append("\n")
            elif el.tag == W + "tab":
                bits.append("\t")
        out.append("".join(bits))
    return out


def text(data: bytes) -> str:
    return "\n".join(paragraphs(data))


def opens(name: str, data: bytes) -> None:
    """Each kind of file opens as what it says it is."""
    assert data, name
    if name.endswith((".docx", ".xlsx")):
        z = zipfile.ZipFile(io.BytesIO(data))
        assert "[Content_Types].xml" in z.namelist()
        for n in z.namelist():
            if n.endswith((".xml", ".rels")):
                ET.fromstring(z.read(n))
        if name.endswith(".docx"):
            assert check_reports.check_docx(name, data) == []
    elif name.endswith(".csv"):
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        assert rows and all(len(r) == len(rows[0]) for r in rows), name
    elif name.endswith(".svg"):
        assert ET.fromstring(data).tag.endswith("svg")
    elif name.endswith(".zip"):
        z = zipfile.ZipFile(io.BytesIO(data))
        for n in z.namelist():
            opens(n, z.read(n))
    else:
        data.decode("utf-8")


def every_text(data: bytes) -> str:
    """Everything readable in a file (inside zips too), for searching for things that must not be there."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return data.decode("utf-8", "replace")
    return "\n".join(every_text(z.read(n)) for n in z.namelist())


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    return demo_review.build(str(tmp_path_factory.mktemp("review") / "AI triage"))


@pytest.fixture
def no_grade(monkeypatch):
    monkeypatch.delattr(certainty, "evidence_profile", raising=False)


@pytest.fixture
def grade(monkeypatch):
    monkeypatch.setattr(certainty, "evidence_profile", demo_review.fake_profile, raising=False)


# ------------------------------------------------------------------------------------------------ the Word writer
def test_word_writer_makes_a_complete_document():
    d = docx.Document("Test <document> & co", subtitle="A subtitle")
    d.heading("Heading one", 1)
    d.heading("Heading two", 2)
    d.heading("Heading three", 3)
    d.para(["Plain, ", docx.b("bold"), " and ", docx.i("italic"), " text with a tab\there\nand a new line."])
    d.bullets(["first", "second"])
    d.bullets(["one", "two"], numbered=True)
    d.bullets(["again one"], numbered=True)
    d.pre("line 1\n  line 2 <&>")
    d.fill_in("[Authors to complete: something]")
    d.table(["A", "B"], [["1", docx.Cell("2", fill="D8EFE2", bold=True)], [docx.Cell("spans both", span=2)]], widths=[1, 3], compact=True)
    data = d.to_bytes()
    opens("x.docx", data)
    z = zipfile.ZipFile(io.BytesIO(data))
    doc = z.read("word/document.xml").decode()
    assert '<w:pStyle w:val="Title"/>' in doc and '<w:pStyle w:val="Heading3"/>' in doc
    assert "<w:tblHeader/>" in doc and '<w:gridSpan w:val="2"/>' in doc and 'w:fill="D8EFE2"' in doc
    assert "<w:highlight w:val=\"yellow\"/>" in doc and "<w:tab/>" in doc and "<w:br/>" in doc
    foot = z.read("word/footer1.xml").decode()
    assert " PAGE " in foot and " NUMPAGES " in foot
    num = z.read("word/numbering.xml").decode()
    assert num.count("<w:abstractNum ") == 3          # bullets, and each numbered list starts again at 1
    assert "Test &lt;document&gt; &amp; co" in z.read("docProps/core.xml").decode()
    assert 'w:val="en-GB"' in z.read("word/styles.xml").decode()
    t = text(data)
    assert "line 2 <&>" in t and "Plain, bold and italic text with a tab\there\nand a new line." in t


def test_characters_xml_cannot_hold_are_dropped_and_text_is_escaped():
    d = docx.Document("T")
    d.para('a\x01b\x0bc <w:t> & "q" ]]> ￾')
    data = d.to_bytes()
    ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    assert 'abc <w:t> & "q" ]]> ' in text(data)


def test_a_table_too_wide_for_an_upright_page_turns_on_its_side_with_its_heading():
    d = docx.Document("Wide")
    d.para("Before")
    d.table(["Study", "Value"], [["Smith 2020", "12"]])
    d.heading("The wide one", 2)
    d.table([f"Column number {k}" for k in range(10)], [[f"cell {k} with some words" for k in range(10)]])
    d.para("After")
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(d.to_bytes())).read("word/document.xml"))
    body = root.find(W + "body")
    sects = [e for e in body.iter(W + "sectPr")]
    orient = [(s.find(W + "pgSz").get(W + "orient") or "portrait") for s in sects]
    assert orient == ["portrait", "landscape", "portrait"]
    assert d.landscape_tables == 1
    # the heading is in the landscape section, just before its table
    kids = list(body)
    first_break = next(k for k, e in enumerate(kids) if e.find(f"{W}pPr/{W}sectPr") is not None)
    heading = next(k for k, e in enumerate(kids) if "The wide one" in "".join(t.text or "" for t in e.iter(W + "t")))
    assert heading > first_break
    assert not docx.too_wide(["Study", "Title", "Journal", "DOI"], [["Smith 2020", "A long title " * 5, "BMJ", "10.1136/x"]])


def test_wide_table_widths_fill_the_page():
    d = docx.Document("W", landscape=True)
    d.table(["a", "b", "c"], [["x", "y", "z"]], widths=[1, 2, 1])
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(d.to_bytes())).read("word/document.xml"))
    cols = [int(g.get(W + "w")) for g in root.iter(W + "gridCol")]
    assert sum(cols) == docx.LANDSCAPE_WIDTH and cols[1] > cols[0]


# ------------------------------------------------------------------------------------------------ ongoing studies
def test_ongoing_is_a_full_text_decision_like_awaiting(demo):
    assert "ongoing" in screening.FT_CHOICES and "awaiting" in screening.FT_CHOICES
    V = {"ft": {"R1": {"A": {"latest": {"d": "ongoing"}}, "B": {"latest": {"d": "ongoing"}}},
                "R2": {"A": {"latest": {"d": "ongoing"}}, "B": {"latest": {"d": "awaiting"}}}}}
    assert screening.ft_outcome("R1", ["A", "B"], V, {})["status"] == "ongoing"
    assert screening.ft_outcome("R2", ["A", "B"], V, {})["status"] == "conflict"
    assert screening.ft_outcome("R2", ["A", "B"], V, {("R2", "ft"): {"d": "ongoing"}})["status"] == "ongoing"
    p, ids = demo["p"], demo["ids"]
    fto = screening.ft_outcomes(p)
    assert fto[ids["ongoing"]]["status"] == "ongoing" and fto[ids["ongoing_resolved"]]["status"] == "ongoing"
    assert fto[ids["awaiting"]]["status"] == "awaiting"
    C = prisma.counts(p)
    assert C["ongoing"] == 2 and C["db"]["ongoing"] == 2 and C["awaiting"] == 1 and C["checks"] == []
    D = C["db"]
    assert D["ft_excluded"] + D["ft_included"] + D["awaiting"] + D["ongoing"] == D["assessed"]
    svg = prisma.svg(p)
    assert "Reports of ongoing studies" in svg and "(n = 2)" in svg and "Reports awaiting classification" in svg
    rows = list(csv.reader(io.StringIO(exports.records_csv(p).decode("utf-8-sig"))))
    assert sum(1 for r in rows if r[10] == "ongoing") == 2
    assert "2 reports of ongoing studies" in exports.methods_summary(p)
    assert screening.overview(p)["fulltext"]["counts"]["ongoing"] == 2


def test_the_full_text_pages_offer_ongoing_study():
    static = os.path.join(os.path.dirname(HERE), "app", "reviewdesk", "static")
    lib = open(os.path.join(static, "lib.js"), encoding="utf-8").read()
    ft = open(os.path.join(static, "views_ft.js"), encoding="utf-8").read()
    core = open(os.path.join(static, "views_core.js"), encoding="utf-8").read()
    assert 'ongoing: "Ongoing study"' in lib
    assert '["ongoing", "Ongoing study"]' in ft and '"awaiting", "ongoing", "not_retrieved"' in ft
    assert "ftc.ongoing" in core


# ------------------------------------------------------------------------------------------------ every output
def test_every_output_opens_for_the_lead_and_the_reviewer(demo, grade):
    p = demo["p"]
    seen = set()
    for me in ("CH", "NH"):
        for it in reporting.ITEMS:
            for kind, _ in it["files"]:
                state, why = reporting.status(p, me, kind)
                if state != "ready":
                    assert why, (me, kind)
                    continue
                name = reporting.file_name(kind)
                opens(name, reporting.make(p, me, kind))
                seen.add(kind)
        opens("pack.zip", reporting.pack(p, me))
    assert {k for k in reporting.MAKERS} <= seen | {"rep_ai", "rep_ai_prompts"}
    assert {"rep_ai", "rep_ai_prompts", "records", "decisions", "ai"} <= seen     # the lead had them


def test_module_downloads_follow_the_server_contract(demo):
    p = demo["p"]
    for kind, (name, mime, make, why_not) in reporting.DOWNLOADS.items():
        assert kind not in server.DOWNLOADS               # never hides one of the older downloads
        assert isinstance(why_not(p, "CH"), str) and callable(make) and "/" in mime and "." in name


def test_numbers_in_the_word_documents_match_the_prisma_counts(demo):
    p = demo["p"]
    C = prisma.counts(p)
    D = C["db"]
    m = text(reporting.methods_docx(p, "CH"))
    assert f"Database searches returned {D['identified_databases']:,} records" in m
    assert f"leaving {D['records']:,} records to screen" in m
    assert f"In all, {C['included_reports']} reports of {C['included_studies']} studies were included" in m
    for line in exports.methods_parts(p, True)[1:]:     # built on the methods summary, word for word
        if line and not isinstance(line, exports.Heading):
            assert line in m
    ex = reporting.excluded_docx(p, "CH")
    assert f"This list has the {D['ft_excluded'] + C['other']['ft_excluded']} reports that were assessed in full and excluded" in text(ex)
    for reason, n in D["ft_reasons"].items():
        assert f"{reason} ({n})" in paragraphs(ex)
    assert "This list has 1 report." in paragraphs(reporting.awaiting_docx(p, "CH"))
    assert f"This list has {C['ongoing']} reports." in paragraphs(reporting.ongoing_docx(p, "CH"))
    assert f"The review includes {C['included_studies']} studies, in {C['included_reports']} reports" in text(reporting.included_docx(p, "CH"))
    rows = list(csv.reader(io.StringIO(reporting.counts_csv(p, "CH").decode("utf-8-sig"))))
    got = {(r[0], r[2]): int(r[3]) for r in rows[1:]}
    A = "Identification of studies via databases and registers"
    assert got[(A, "Records screened")] == D["screened"] and got[(A, "Records excluded")] == D["ta_excluded"]
    assert got[(A, "Reports assessed for eligibility")] == D["assessed"] and got[(A, "Duplicate records removed")] == D["duplicates"]
    assert got[(A, "Reports of ongoing studies")] == D["ongoing"] and got[(A, "Reports awaiting classification")] == D["awaiting"]
    assert got[("Included", "Studies included in review")] == C["included_studies"]
    assert not any(r[1] == "Included" and "awaiting" in r[2] or "ongoing" in r[2] and r[1] != reporting.OUTFLOWS for r in rows[1:])
    # what leaves "Reports assessed for eligibility" has one stage name, which says so; every other stage is PRISMA's
    assert reporting.OUTFLOWS == "Reports assessed for eligibility (outflows)"
    assert {r[1] for r in rows[1:]} <= {"Identification", "Screening", "Included", reporting.OUTFLOWS} | {
        r[1] for r in rows[1:] if r[1].startswith("Not in the diagram")}
    assert all(r[1] == reporting.OUTFLOWS for r in rows[1:] if r[2].startswith("Reports excluded: ")
               or r[2] in ("Reports awaiting classification", "Reports of ongoing studies", "Reports included (from this column)"))
    # in each column: assessed - excluded - awaiting - ongoing = the reports included from it
    for part in (A, "Identification of studies via other methods"):
        excl = sum(n for (pt, box), n in got.items() if pt == part and box.startswith("Reports excluded: "))
        assert (got[(part, "Reports assessed for eligibility")] - excl - got[(part, "Reports awaiting classification")]
                - got[(part, "Reports of ongoing studies")]) == got[(part, "Reports included (from this column)")]
    assert sum(n for (pt, box), n in got.items() if box == "Reports included (from this column)") == C["included_reports"]


def test_the_agreement_report_has_the_sample_and_the_rule(demo):
    p = demo["p"]
    sr = p.state_ro()["sets"]["main"]["sample_result"]
    t = text(reporting.agreement_docx(p, "CH"))
    assert f"Agreement was {exports.stats.fmt_pct(sr['agreement'])}, {exports.stats.kappa_text(sr)}." in t
    assert sr["kappa_rule"] in t and "Calibration round" in t and "Full-text review" in t
    # the heading is not repeated as the table's caption
    assert "Agreement in the calibration round" in t and t.count("Calibration round\n") <= 1
    assert "Nadine Hayes screened these records alone" in t                  # the update search


def test_search_appendix_has_each_strategy_word_for_word_and_says_what_is_missing(demo):
    p = demo["p"]
    t = text(reporting.search_docx(p, "NH"))
    assert demo_review.STRATEGY in t
    assert "CINAHL" in t and "the platform or interface used (PRISMA-S item 1)" in t
    assert "No export file is linked to this search" in t and "ANZCTR" in t and "Updates (PRISMA-S item 12)" in t


def test_text_from_records_is_escaped(demo):
    p = demo["p"]
    data = reporting.included_docx(p, "CH")
    raw = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode()
    assert "&lt;score&gt; &amp; &quot;ED&quot; flow ]]&gt;" in raw and "\x01" not in raw
    assert 'AI triage <score> & "ED" flow ]]> in a prospective study of 400 adults' in text(data)
    assert "Values < 5 & > 2 were excluded" in text(reporting.characteristics_docx(p, "CH"))     # a finished study's answer


def test_characteristics_and_risk_of_bias_show_only_settled_values(demo):
    p = demo["p"]
    t = text(reporting.characteristics_docx(p, "NH"))
    assert "[to agree]" in t and "(not finished)" in t and "Ireland" not in t          # Haddad's country is unsettled
    assert "Cluster randomised trial" in t
    r = text(reporting.rob_docx(p, "NH"))
    assert "Some concerns" in r and "(not finished)" in r
    # the cluster trial is assessed with RoB 2 for cluster-randomised trials, and so judged on D1b (never "Not applicable")
    assert "RoB 2 for cluster-randomised trials" in r and "Not applicable" not in r
    assert "(cluster-randomised trials)" not in r and ") (" not in r                    # no doubled or nested brackets
    assert "Case series: no risk-of-bias assessment (included for context only" in text(reporting.methods_docx(p, "NH"))


def test_ai_report_follows_the_position_statement(demo):
    p = demo["p"]
    t = text(reporting.ai_docx(p, "CH"))
    for x in ("ReviewDesk", "Qwen/Qwen3.5-397B-A17B-FP8", "meta-llama/Llama-3.3-70B-Instruct", "ELM", "Sensitivity (95% CI)",
              "Every AI run", "How people checked the AI's answers", "Funding and interests", "Limitations and possible biases",
              "Data protection, confidentiality, licensing and ethics", "JBIES-25-00480", "Known studies check",
              "It is structured to cover the elements of the joint position statement",
              "ReviewDesk received no funding: Chris Humphries wrote it in his own time",
              "[Authors to complete: who paid for the AI service", "[Authors to complete: where the service processed the data",
              "[Authors to complete: whether the database licences", "Large language models can carry biases"):
        assert x in t, x
    assert "It follows the joint position statement" not in t and "each screened every record" not in t
    from reviewdesk import ai
    for prompt in ai.prompts(p).values():
        assert prompt in t                                                           # the exact prompts, verbatim
        assert ai.fingerprint(prompt) in t
    pt = reporting.ai_prompts_txt(p, "CH").decode()
    assert all(x in pt for x in ai.prompts(p).values()) and ai.USER in pt


def test_checklist_has_every_prisma_2020_item(demo, grade):
    p = demo["p"]
    want = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10a", "10b", "11", "12", "13a", "13b", "13c", "13d", "13e", "13f",
            "14", "15", "16a", "16b", "17", "18", "19", "20a", "20b", "20c", "20d", "21", "22", "23a", "23b", "23c", "23d",
            "24a", "24b", "24c", "25", "26", "27"]
    assert reporting.CHECKLIST_ITEMS == want
    where = reporting.checklist_where(p, "CH")
    assert "flow diagram" in where["16a"] and "excluded" in where["16b"] and "Search methods appendix" in where["7"]
    assert "Summary of findings" in where["22"] and "CRD420261234567" in where["24a"]
    t = text(reporting.checklist_docx(p, "CH"))
    assert "The authors must complete and check this checklist." in t


# ------------------------------------------------------------------------------------------------ GRADE
def test_summary_of_findings_from_the_evidence_profile(demo, grade):
    p = demo["p"]
    assert reporting.status(p, "NH", "rep_sof") == ("ready", "")
    t = text(reporting.make(p, "NH", "rep_sof"))
    assert "Time to triage" in t and "⊕⊕◯◯ Low" in t and "Moderate\nNot finished" in t and "Few events <10 & wide CI" in t
    assert "Large effect: Upgraded one level" in t and "Narrative" not in t
    m = text(reporting.methods_docx(p, "CH"))
    assert "Ratings were narrative where results were not pooled (Murad et al. 2017)." in m
    assert any(it["title"].startswith("Summary of findings") for g in reporting.h_reporting(p, "NH", {}, {})["groups"] for it in g["items"])


def test_without_grade_there_is_no_summary_of_findings(demo, no_grade, monkeypatch):
    p = demo["p"]
    assert reporting.status(p, "CH", "rep_sof")[0] == "later"
    assert not any(it["title"].startswith("Summary of findings") for g in reporting.h_reporting(p, "CH", {}, {})["groups"] for it in g["items"])
    assert "[Authors to complete: how the certainty of the evidence was assessed" in text(reporting.methods_docx(p, "CH"))

    def broken(p, me):
        raise RuntimeError("no outcomes file")
    monkeypatch.setattr(certainty, "evidence_profile", broken, raising=False)
    st = reporting.status(p, "CH", "rep_sof")
    assert st[0] == "later" and "no outcomes file" in st[1]
    opens("m.docx", reporting.methods_docx(p, "CH"))


# ------------------------------------------------------------------------------------------------ the pack and blinding
def test_the_reviewers_pack_holds_only_what_a_reviewer_may_have(demo, grade):
    p = demo["p"]
    z = zipfile.ZipFile(io.BytesIO(reporting.pack(p, "NH")))
    names = {n.rsplit("/", 1)[-1] for n in z.namelist()}
    assert not names & LEAD_ONLY_FILES
    everything = every_text(reporting.pack(p, "NH"))
    assert demo_review.AI_MARK not in everything                                     # no AI answer anywhere
    assert "sensitivity was" not in everything                                       # nor the AI's accuracy
    svg = next(n for n in z.namelist() if n.endswith("PRISMA flow diagram.svg"))
    assert z.read(svg).decode() == prisma.svg(p, blind=True)
    summ = next(n for n in z.namelist() if n.endswith("methods summary.txt"))
    assert z.read(summ).decode() == exports.methods_summary(p, full=False)
    readme = z.read("Read me first.txt").decode()
    assert "This is a reviewer's pack" in readme and "NOT INCLUDED" in readme and "AI second screener report" in readme
    for n in z.namelist():
        if n != "Read me first.txt":
            assert n.split("/", 1)[1] in readme                                    # every file is described


def test_the_leads_pack_has_everything(demo, grade):
    p = demo["p"]
    data = reporting.pack(p, "CH")
    names = {n.rsplit("/", 1)[-1] for n in zipfile.ZipFile(io.BytesIO(data)).namelist()}
    assert LEAD_ONLY_FILES <= names and "summary of findings (GRADE).docx" in names and "Read me first.txt" in names
    assert demo_review.AI_MARK in every_text(data)
    assert len(names) == len(zipfile.ZipFile(io.BytesIO(data)).namelist())          # no two files share a name


def test_nothing_shows_votes_or_the_ai_while_the_agreement_sample_is_open(tmp_path):
    info = demo_review.build_open_sample(str(tmp_path / "open"))
    p = info["p"]
    for me in ("CH", "NH"):
        for kind in ("records", "decisions", "ai", "rep_ai"):
            assert reporting.status(p, me, kind)[0] in ("later", "lead"), (me, kind)
        assert reporting.status(p, me, "rep_agreement")[0] == "later"
        data = reporting.pack(p, me)
        names = {n.rsplit("/", 1)[-1] for n in zipfile.ZipFile(io.BytesIO(data)).namelist()}
        assert not names & (LEAD_ONLY_FILES - {"AI prompts and settings.txt"})
        everything = every_text(data)
        assert demo_review.AI_MARK not in everything
        assert "Include" not in text(reporting.agreement_docx(p, me)).split("not finished")[0][-200:]
        assert "is not finished" in text(reporting.agreement_docx(p, me))
    assert reporting.status(p, "CH", "rep_ai_prompts")[0] == "ready"                 # the prompts are no secret to the lead


def test_an_empty_project_still_gets_a_pack(tmp_path):
    from reviewdesk.project import Project
    p = Project.create(str(tmp_path / "new"), "A new review", "Ada Lovelace", "AL")
    for it in reporting.ITEMS:
        for kind, _ in it["files"]:
            state, why = reporting.status(p, "AL", kind)
            assert state == "ready" or why
    z = zipfile.ZipFile(io.BytesIO(reporting.pack(p, "AL")))
    assert {"Read me first.txt", "2 Methods text/PRISMA 2020 checklist.docx", "3 Appendix and deposit files/data checks report.txt"} <= set(z.namelist())
    assert "no problems found" in z.read("3 Appendix and deposit files/data checks report.txt").decode()


# ------------------------------------------------------------------------------------------------ over the local server
@pytest.fixture(scope="module")
def served(demo):
    httpd, url = server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port, tok = httpd.server_address[1], url.split("t=")[1]

    def call(method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        c.request(method, path, body=json.dumps(body) if body is not None else None,
                  headers={"Host": f"127.0.0.1:{port}", "X-Token": tok, "Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data
    assert call("POST", "/api/project/open", {"path": demo["folder"]})[0] == 200
    yield call
    httpd.shutdown()


def test_downloads_over_the_server_follow_the_blinding_rules(demo, served):
    call = served
    p = server.APP.project
    for me in ("CH", "NH"):
        assert call("POST", "/api/me", {"reviewer": me})[0] == 200
        for kind in ("records", "decisions", "ai", "excluded", "included", "audit", "prisma", "summary", "searchlog"):
            assert reporting.core_why_not(p, me, kind) == server.download_blocked(p, kind), (me, kind)
        page = json.loads(call("GET", "/api/reporting")[2])
        files = {f["kind"]: f for g in page["groups"] for it in g["items"] for f in it["files"]}
        for kind, f in files.items():
            st, hd, data = call("GET", f"/download/{kind}")
            if f["state"] == "ready":
                assert st == 200, (me, kind, data[:200])
                name = __import__("urllib.parse").parse.unquote(hd["Content-Disposition"].split("''", 1)[1])
                opens(name, data)
            elif kind in reporting.DOWNLOADS:
                assert st == 403 and json.loads(data)["error"] == f["why"], (me, kind)
            elif f["state"] in ("lead", "later"):                 # the older downloads, refused in server.py's words
                assert st == 403, (me, kind)
        st, _, data = call("GET", "/download/rep_pack")
        assert st == 200
        names = {n.rsplit("/", 1)[-1] for n in zipfile.ZipFile(io.BytesIO(data)).namelist()}
        assert (LEAD_ONLY_FILES <= names) if me == "CH" else not (names & LEAD_ONLY_FILES)
    st, _, data = call("GET", "/download/rep_ai")
    assert st == 403 and "Only the project lead" in json.loads(data)["error"]


def test_the_server_accepts_an_ongoing_decision(demo, served):
    call = served
    call("POST", "/api/me", {"reviewer": "NH"})
    rid = demo["ids"]["ongoing"]
    st, _, data = call("POST", "/api/decide", {"record": rid, "stage": "ft", "decision": "ongoing", "note": "Still recruiting", "as": "NH"})
    assert st == 200 and json.loads(data)["saved"]["d"] == "ongoing"
    st, _, data = call("POST", "/api/decide", {"record": rid, "stage": "ft", "decision": "unknown", "as": "NH"})
    assert st == 400
    assert screening.ft_outcomes(server.APP.project)[rid]["status"] == "ongoing"


# ------------------------------------------------------------------------------------------------ the methodologist's beta findings
SVG = "{http://www.w3.org/2000/svg}"


def svg_boxes(svg: str):
    """The diagram's boxes as (x, y, w, h, text), its arrows as (x1, y1, x2, y2), its <text> lines and its width."""
    root = ET.fromstring(svg)
    rects = [tuple(float(r.get(k)) for k in ("x", "y", "width", "height")) for r in root.iter(SVG + "rect")
             if r.get("stroke") == "#333"]
    texts = [(float(t.get("x")), float(t.get("y")), t.text or "", t.get("font-size")) for t in root.iter(SVG + "text")]
    boxes = []
    for x, y, w, h in rects:
        inside = [s for tx, ty, s, _ in texts if x <= tx <= x + w and y <= ty <= y + h]
        boxes.append((x, y, w, h, " ".join(inside)))
    lines = [tuple(float(ln.get(k)) for k in ("x1", "y1", "x2", "y2")) for ln in root.iter(SVG + "line")]
    return boxes, lines, texts, float(root.get("width"))


def test_awaiting_and_ongoing_are_drawn_leaving_the_assessed_box_and_every_column_adds_up(demo):
    p = demo["p"]
    C = prisma.counts(p)
    for X in (C["db"], C["other"]):
        assert X["assessed"] - X["ft_excluded"] - X["awaiting"] - X["ongoing"] == X["ft_included"]
    assert C["db"]["ft_included"] + C["other"]["ft_included"] == C["included_reports"] and C["checks"] == []
    for blind in (False, True):
        boxes, lines, texts, _ = svg_boxes(prisma.svg(p, blind))
        assessed = [b_ for b_ in boxes if b_[4].startswith("Reports assessed for eligibility")]
        assert len(assessed) == 2                                              # one in each column
        for x, y, w, h, t in assessed:
            n_assessed = int(t.split("(n = ")[1].split(")")[0])
            beside = [b_ for b_ in boxes if b_[1] >= y - 0.5 and b_[1] + b_[3] <= y + h + 0.5 and x + w < b_[0] < x + w + 100]
            words = [b_[4] for b_ in beside]
            assert any(s.startswith("Reports excluded:") for s in words)
            assert any(s.startswith("Reports awaiting classification") for s in words)
            assert any(s.startswith("Reports of ongoing studies") for s in words)
            for bx, by_, bw, bh, s in beside:                                  # each with its own arrow from the assessed box
                assert any(abs(x1 - (x + w)) < 0.5 and abs(x2 - bx) < 0.5 and by_ < y2 < by_ + bh for x1, y1, x2, y2 in lines), s
            out = 0
            for _, _, _, _, s in beside:
                out += sum(int(n.split(")")[0]) for n in s.split("(n = ")[1:])
            col = C["db"] if x < 700 else C["other"]
            assert n_assessed - out == col["ft_included"]                      # assessed - excluded - awaiting - ongoing
        included = [b_ for b_ in boxes if b_[4].startswith("Studies included in review")]
        assert len(included) == 1 and not [b_ for b_ in boxes if b_[1] == included[0][1] and b_ is not included[0]]
    rows = list(csv.reader(io.StringIO(reporting.counts_csv(p, "CH").decode("utf-8-sig"))))
    assert not [r for r in rows if r[1] == "Included" and ("awaiting" in r[2] or "ongoing" in r[2])]
    assert [r[1] for r in rows if r[2] in ("Reports awaiting classification", "Reports of ongoing studies")] == [reporting.OUTFLOWS] * 4


def test_the_diagram_footnotes_fit_and_credit_the_template(demo):
    p = demo["p"]
    svg = prisma.svg(p)
    boxes, lines, texts, width = svg_boxes(svg)
    foot = [(x, s) for x, y, s, size in texts if size == "11"]
    assert foot and all(x + len(s) * 11 * 0.5 <= width for x, s in foot)        # nothing runs off the right edge
    flat = " ".join(s for _, s in foot)
    assert flat.endswith("Adapted from Page MJ, et al. BMJ 2021;372:n71 (CC BY 4.0, https://creativecommons.org/licenses/by/4.0/).")
    assert "** Every record excluded was excluded by a person; no automation tool excluded any record" in flat
    assert sum(1 for b_ in boxes if b_[4].startswith("Records excluded**")) == 2
    assert "are not in the PRISMA 2020 template's other-methods column" in flat
    C = prisma.counts(p)
    assert (f"Reports of included studies: {C['db']['ft_included']} from databases and registers and "
            f"{C['other']['ft_included']} from other methods.") in flat


def test_registers_that_were_not_imported_are_in_no_box_but_are_named_and_flagged(demo):
    from reviewdesk import readiness
    p = demo["p"]
    C = prisma.counts(p)
    D = C["db"]
    assert D["registers_not_imported"] == {"ANZCTR": 4}
    assert D["identified_registers"] == sum(D["registers"].values()) == 3
    assert D["identified_databases"] + D["identified_registers"] - D["duplicates"] - D["other_removed"] == D["records"]
    boxes, _, texts, _ = svg_boxes(prisma.svg(p))
    ident = next(b_[4] for b_ in boxes if b_[4].startswith("Records identified from:") and "Databases, total" in b_[4])
    # the box counts the register records imported, and says so; the footnote names the rest
    assert f"Registers (n = {D['identified_registers']} imported)*" in ident and "ANZCTR" not in ident
    flat = " ".join(s for _, _, s, size in texts if size == "11")
    assert ("* Registers also searched, whose records were not imported into ReviewDesk, so they are in none of these "
            "numbers: ANZCTR (4 records found).") in flat
    rows = list(csv.reader(io.StringIO(reporting.counts_csv(p, "CH").decode("utf-8-sig"))))
    reg = [r for r in rows if r[2] == "Registers, imported"]
    assert reg and int(reg[0][3]) == D["identified_registers"]
    anz = [r for r in rows if "ANZCTR" in r[2]]
    assert anz == [["Identification of studies via databases and registers",
                    "Not in the diagram (searched, not imported into ReviewDesk)", "Records found in: ANZCTR", "4"]]
    # the methods say so, with a highlighted line for how they were handled
    doc = reporting.methods_docx(p, "CH")
    assert ("ANZCTR (4 records) was also searched, but its records were not imported into ReviewDesk, so they were not "
            "de-duplicated or screened in ReviewDesk") in text(doc)
    raw = zipfile.ZipFile(io.BytesIO(doc)).read("word/document.xml").decode()
    para = next(x for x in raw.split("<w:p>") if "how the records from ANZCTR were handled outside ReviewDesk" in x)
    assert '<w:highlight w:val="yellow"/>' in para
    # and readiness warns (without blocking) that importing them would screen and count them
    for me in ("CH", "NH"):
        items = [x for st in readiness.check(p, me)["steps"] for x in st["warnings"] + st["missing"]
                 if x["key"] == "registers_not_imported"]
        assert items and not any(x["blocks"] for x in items)
        assert "ANZCTR (4 records)" in items[0]["text"] and "Import each register's export" in items[0]["text"]


def test_the_search_appendix_asks_for_every_register_search_and_only_asks_what_applies(demo):
    p = demo["p"]
    t = text(reporting.search_docx(p, "CH"))
    assert "Each database was searched separately" not in t
    assert "[Authors to complete: whether each database was searched on its own, or several were searched together" in t
    assert "[Missing from the search log for ANZCTR: the platform or interface used (PRISMA-S item 1), the date searched" in t
    assert "[Authors to complete: the date ANZCTR was searched (PRISMA-S item 13).]" in t
    cit = t.split("Citation searching (backward and forward; Web of Science)")[1].split("Search filters and prior work")[0]
    assert "years the database covers" not in cit and "Years covered" not in cit
    assert reporting._missing({"database": "Citation searching", "platform": "Web of Science"}, "other") == [
        "the date searched (PRISMA-S item 13)", "the number of records found (PRISMA-S item 15)",
        "the full search strategy, exactly as run (PRISMA-S item 8)", "the limits and filters used, or 'none' (PRISMA-S items 9 and 10)"]
    assert "the years the database covers" in reporting._missing({"database": "Embase"}, "databases")
    assert "the years the database covers" not in reporting._missing({"database": "ClinicalTrials.gov"}, "registers")
    assert reporting._source_name({"database": "CENTRAL (Cochrane Library)", "platform": "Cochrane Library"}) == "CENTRAL (Cochrane Library)"
    assert reporting._source_name({"database": "MEDLINE", "platform": "Ovid"}) == "MEDLINE (Ovid)"
    # never two brackets in a row: the platform and the date share one
    assert reporting._source_name({"database": "WHO ICTRP", "platform": "trialsearch.who.int"},
                                  "searched on 23 October 2026") == "WHO ICTRP (trialsearch.who.int; searched on 23 October 2026)"
    assert reporting._source_name({"database": "Citation searching (backward and forward)", "platform": "Web of Science"}) == \
        "Citation searching (backward and forward; Web of Science)"
    assert ") (" not in t


def test_the_methods_give_every_search_date_or_ask_for_it(demo):
    p = demo["p"]
    m = text(reporting.methods_docx(p, "CH"))
    on = {e["database"]: reporting.nice_date(e["date"]) for e in p.search_log()}      # the demo's dates count back from today
    assert f"ClinicalTrials.gov (searched on {on['ClinicalTrials.gov']}) and ANZCTR" in m
    assert "[Authors to complete: the date ANZCTR was last searched (PRISMA 2020 item 6)." in m
    assert f"Citation searching (4 records; searched on {on['Citation searching (backward and forward)']})" in m


def test_kappa_is_reported_with_truncation_a_bootstrap_interval_and_the_threshold(demo):
    p = demo["p"]
    sr = p.state_ro()["sets"]["main"]["sample_result"]
    for doc in (text(reporting.agreement_docx(p, "CH")), exports.methods_summary(p, True)):
        assert exports.stats.kappa_text(sr) in doc and "by percentile bootstrap" in doc
        assert "truncated at 1, the highest possible value" in doc
        assert "Both confidence intervals include values below the pre-specified threshold of 0.50." in doc
        assert "not estimable" not in doc
        assert "Percentile bootstrap intervals come from 2,000 resamples, with replacement, of the paired votes" in doc
    a = text(reporting.agreement_docx(p, "CH"))
    assert "(95% CI 95% CI" not in a and "Cohen's kappa (95% CI: large-sample; bootstrap)" in a
    other = a.split("Title and abstract screening: other methods")[1]
    assert "Records outside the agreement sample" not in other and "Records screened by both reviewers" in other
    assert "the reviewers agreed on all 3 records, each sending" in other
    assert "but the goodness-of-fit method of Donner and Eliasziw gives a 95% CI of" in other
    assert "Donner and Eliasziw (1992), which assumes that the two reviewers send records on at the same" in other
    assert "))" not in a and "100%" not in a.replace("100.0%", "")
    # full text: what was recorded about the disagreements, the rule set in advance, and a line for the authors
    d = exports.ft_disagreements(p)
    assert d["total"] and d["open"] == 0
    assert (f"The two reviewers' decisions differed on {d['total']} reports at full text. The project lead (Colin Hawthorne) "
            "recorded the decision on each, so none is still waiting for a decision. The rule set in advance for when the "
            "reviewers still disagree: “Discuss; if still not agreed, include") in a
    assert "settled together" not in a and "settled by the two reviewers" not in a
    raw = zipfile.ZipFile(io.BytesIO(reporting.agreement_docx(p, "CH"))).read("word/document.xml").decode()
    for what in ("how the disagreements at full text were resolved", "what was done with the calibration disagreements"):
        para = next(x for x in raw.split("<w:p>") if what in x)
        assert '<w:highlight w:val="yellow"/>' in para


def test_the_calibration_round_is_reported_with_its_interval_and_what_became_of_its_records(demo):
    p = demo["p"]
    cr = p.state_ro()["sets"]["main"]["calibration_results"][0]
    full = exports.calibration_result(p, "main", 0, cr)
    assert "table" in full and exports.stats.kappa_of(*[full["table"][c] for c in exports.stats.CELLS]) == pytest.approx(cr["kappa"])
    m = exports.methods_summary(p, True)
    assert f"Calibration round 1: Nadine Hayes and Colin Hawthorne screened the same {cr['n']} records independently" in m
    assert exports.stats.kappa_text(full) in m and "by the large-sample method" in m
    assert ("the calibration records were screened again later with the other records outside the agreement sample, and "
            "only those later votes counted.") in m


def test_full_text_agreement_says_which_reports_it_is_on(demo):
    p = demo["p"]
    ag = screening.agreement(p, "main", "fulltext")
    m = exports.methods_summary(p, True)
    assert (f"on the {ag['stats']['n']} reports from every record set that both reviewers voted to include or exclude "
            f"({ag['left_out']} reports that either reviewer marked as awaiting classification, ongoing or not retrieved are "
            "left out).") in m


def test_the_ai_figures_say_what_they_are_out_of(demo):
    from reviewdesk import ai
    p = demo["p"]
    cov, v = ai.coverage(p, "main"), ai.validate(p, "main")
    cs = v["combined"]
    m = exports.methods_summary(p, True)
    assert f"Both models gave a readable answer for {cov['done']} of the {cov['records']} records; for 1 record a call failed" in m
    assert (f"its specificity {exports.stats.fmt_pct(cs['specificity'])} ({cs['tn']}/{cs['tn'] + cs['fp']}; Wilson 95% CI "
            f"{exports.stats.fmt_pct(cs['spec_ci'][0])} to {exports.stats.fmt_pct(cs['spec_ci'][1])})") in m
    assert f"on the {v['sample_answered']} of {v['sample_records']} sample records both models answered" in m
    assert "every record in the main search was screened" not in m and "prompts deposited with the review" not in m
    t = text(reporting.ai_docx(p, "CH"))
    assert "each screened every record" not in t and "Across all" not in t and "from different developers" not in t
    assert v["A"]["n"] != v["B"]["n"]                                     # pass A failed on one sample record
    assert f"({v['A']['n']} for pass A and {v['B']['n']} for pass B)" in t and "Sample records" in t
    assert "Its answers for the records found by other methods were not used." in t
    assert "Of the sample records, 1 has no readable answer from both models and is left out" in t
    m_nh = exports.methods_summary(p, False)
    assert "The AI second screener's answers were not used for these records." in m_nh


def test_a_reviewer_never_sees_the_ai_keep_rate_or_how_many_answers_came_back(demo):
    import re
    p = demo["p"]
    lead = exports.methods_summary(p, True)
    assert re.search(r"kept [\d.]+% of records, within the \d+% limit", lead)
    for t in (exports.methods_summary(p, False), text(reporting.methods_docx(p, "NH")), reporting.methods_txt(p, "NH").decode(),
              text(reporting.agreement_docx(p, "NH")), every_text(reporting.pack(p, "NH"))):
        assert not re.search(r"kept [\d.]+%", t) and "% limit" not in t
        assert "readable answer for" not in t and "failed or unreadable answer and" not in t
    assert "the AI second screener stayed in use behind the single reviewer" in exports.methods_summary(p, False)


def test_risk_of_bias_tools_are_cited_and_described_as_they_are(demo):
    p = demo["p"]
    m = text(reporting.methods_docx(p, "NH"))
    # the plan, design by design, with each tool's citation, and when it was recorded
    assert "The risk-of-bias plan, recorded in ReviewDesk on " in m and "before screening started, set the tool for each study design:" in m
    assert "Randomised trials (individually randomised, parallel-group): RoB 2 (Sterne JAC, et al. BMJ 2019;366:l4898)." in m
    assert ("Prediction model studies (developing or validating a model, including AI models): PROBAST+AI (Moons KGM, et al. "
            "BMJ 2025;388:e082505).") in m
    assert "Cluster-randomised trials: RoB 2 for cluster-randomised trials (Eldridge S, et al." in m
    assert "RoB 2 for cluster-randomised trials (1 result in 1 study)" in m
    assert "a tool's accuracy against a reference standard" not in m
    assert "[Authors to complete: the version of RoB 2 used" in m and "the version of ROBINS-I V2 used" in m
    assert "confirm that this plan matches the registered protocol" not in m          # recorded before screening started
    r = text(reporting.rob_docx(p, "NH"))
    assert "the tool's accuracy against a reference standard" not in r and "PROBAST+AI (studies that develop or evaluate" in r
    assert "Table 1. The tool for each study design" in r and "Recorded on " in r and "before screening started." in r


def test_the_checklist_quotes_prisma_2020_and_claims_only_what_reportdesk_covers(demo, grade):
    official = {   # from the PRISMA 2020 checklist (Page MJ, et al. BMJ 2021;372:n71), word for word
        "13a": "Describe the processes used to decide which studies were eligible for each synthesis (e.g. tabulating the study "
               "intervention characteristics and comparing against the planned groups for each synthesis (item #5)).",
        "13d": "Describe any methods used to synthesize results and provide a rationale for the choice(s). If meta-analysis was "
               "performed, describe the model(s), method(s) to identify the presence and extent of statistical heterogeneity, "
               "and software package(s) used.",
        "13f": "Describe any sensitivity analyses conducted to assess robustness of the synthesized results.",
        "20a": "For each synthesis, briefly summarise the characteristics and risk of bias among contributing studies.",
        "20d": "Present results of all sensitivity analyses conducted to assess the robustness of the synthesized results.",
        "25": "Describe sources of financial or non-financial support for the review, and the role of the funders or sponsors in "
              "the review.",
        "6": "Specify all databases, registers, websites, organisations, reference lists and other sources searched or consulted "
             "to identify studies. Specify the date when each source was last searched or consulted."}
    items = {n: t for _, its in reporting.CHECKLIST for n, _, t in its}
    for n, t in official.items():
        assert items[n] == t, n
    p = demo["p"]
    where = reporting.checklist_where(p, "CH")
    assert not {"13a", "13c", "14", "21", "24c"} & set(where)            # methods items, a GRADE rating, no amendments
    assert "highlighted line" in where["24b"] or "to complete" not in where["24b"]
    t = text(reporting.checklist_docx(p, "CH"))
    assert "some items are shortened" not in t and "the sections, topics and checklist items are quoted word for word from" in t


def test_references_lists_and_labels_read_correctly():
    assert reporting._vancouver("Silva R.") == "Silva R" and reporting._vancouver("Bakker Jw.") == "Bakker JW"
    assert reporting._vancouver("Romano MJ.") == "Romano MJ" and reporting._vancouver("Wang Li") == "Wang Li"
    assert reporting._vancouver("World Health Organization") == "World Health Organization"
    assert reporting.first_author_year({"authors": "Becker Jw.; Smith J.", "year": "2026"}) == "Becker 2026"
    c = reporting.cite({"authors": "Silva R.; Walker K.; Patel J.", "title": "A trial", "journal": "BMJ", "year": "2026"})
    assert c.startswith("Silva R, Walker K, Patel J. A trial.") and ".." not in c
    assert reporting.identifier({"accession": "2020458163"}, "Embase") == "Accession number 2020458163 (Embase)"
    assert reporting.identifier({"accession": "40801012", "pmid": "40801012"}) == "PMID 40801012"
    assert reporting.identifier({"accession": "NCT06000001"}) == "NCT06000001"


def test_the_lists_say_how_reports_were_filed(demo):
    p = demo["p"]
    ex = text(reporting.excluded_docx(p, "CH"))
    assert "first exclusion reason that applied" not in ex and "listed under the one reason for exclusion the reviewers" in ex
    nr = paragraphs(reporting.not_retrieved_docx(p, "CH"))
    assert "DOI or identifier" in nr and "DOI" not in [x.strip() for x in nr]
    assert "accents and macrons" in text(reporting.included_docx(p, "CH"))


def test_one_ongoing_study_is_written_in_the_singular(demo, monkeypatch):
    p = demo["p"]
    real = prisma.counts

    def one(p_, blind=False):
        C = dict(real(p_, blind))
        C["ongoing"] = 1
        return C
    monkeypatch.setattr(exports.prisma, "counts", one)
    text_ = "\n".join(str(x) for x in exports._methods_summary(p, True))
    assert "1 report of an ongoing study (registered or under way, with no results yet) is listed but not included" in text_
    assert "1 report of ongoing studies" not in text_


def test_the_grade_workbook_is_in_the_pack(demo, grade):
    p = demo["p"]
    for me in ("CH", "NH"):
        z = zipfile.ZipFile(io.BytesIO(reporting.pack(p, me)))
        assert "1 Tables and figures/certainty of evidence (GRADE).xlsx" in z.namelist()
        assert "certainty of evidence (GRADE).xlsx" in z.read("Read me first.txt").decode()


def test_the_summary_of_findings_has_its_header_participants_importance_and_footnotes(demo, grade):
    p = demo["p"]
    data = reporting.make(p, "CH", "rep_sof")
    t = text(data)
    for x in ("Summary of findings: AI-supported triage compared with usual nurse triage", "Patient or population",
              "Review question", "In emergency departments, how do AI triage tools evaluated prospectively compare with usual triage?",
              "Emergency department patients (from the eligibility criteria)", "[Authors to complete: the setting",
              "AI-supported triage", "usual nurse triage",
              "Participants: 1,850", "Importance: critical", "Minutes from arrival to triage", "⊕⊕◯◯ Low (a, b)",
              "a. Time to triage, risk of bias (serious): Two studies at serious risk", "Studies, design and participants",
              "AI triage may reduce under-triage.\n[Check: This wording does not match the certainty of the evidence. Moderate "
              "certainty is worded with 'probably' or 'likely'; this says 'may'. Suggested: “AI triage probably reduces "
              "under-triage.”]", "The plain-language statement for under-triage does not match the certainty",
              "Not final. The outcome under-triage is not finished"):
        assert x in t, x
    raw = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode()
    flag = next(x for x in raw.split("<w:r>") if "[Check: This wording does not match" in x)
    assert '<w:highlight w:val="yellow"/>' in flag                       # the flag is highlighted, never silent
    # no word in the evidence profile's headings or cells is broken across lines: every column is wide enough
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    tbl = [x for x in root.iter(W + "tbl") if "Inconsistency" in "".join(e.text or "" for e in x.iter(W + "t"))][-1]
    cols = [int(g.get(W + "w")) for g in tbl.iter(W + "gridCol")]
    rows = [[" ".join("".join((e.text or "") if e.tag == W + "t" else " " if e.tag == W + "br" else "" for e in p_.iter())
                      for p_ in tc.findall(W + "p")) for tc in tr.findall(W + "tc")] for tr in tbl.findall(W + "tr")]
    body = [r for r in rows[1:] if len(r) == len(rows[0])]            # the effect has a row of its own beneath
    assert len(body) == 2 and "Effect" not in rows[0] and all(len(r) == 1 for r in rows[1:] if r not in body)
    need = docx.min_widths(rows[0], body, compact=True)
    assert all(c >= n for c, n in zip(cols, need)), (cols, need)


def test_table_columns_are_widened_so_words_are_not_broken():
    head = ["Outcome", "Studies", "Starting certainty", "Risk of bias", "Inconsistency", "Indirectness", "Imprecision",
            "Publication bias", "Other considerations", "Certainty", "Effect"]
    row = ["Time to triage", "3", "High", "Extremely serious", "Not serious", "Not serious", "Very serious", "Undetected",
           "None", "Very low", "A long description of the effect. " * 12]
    d = docx.Document("W", landscape=True)
    d.table(head, [row], compact=True)
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(d.to_bytes())).read("word/document.xml"))
    cols = [int(g.get(W + "w")) for g in root.iter(W + "gridCol")]
    need = docx.min_widths(head, [row], compact=True)
    assert sum(cols) == docx.LANDSCAPE_WIDTH and all(c >= n for c, n in zip(cols, need))


def test_the_ai_run_table_says_what_each_run_sent_and_when_a_run_was_interrupted(tmp_path):
    from reviewdesk import ai
    info = demo_review.build_open_sample(str(tmp_path / "runs"))
    p = info["p"]
    recs = p.records()
    p.audit("CH", "AI run started", {"label": "retry failed records", "records_checked": len(recs), "records_sent": 3,
                                     "calls": 4, "retries": 2, "models": {}, "prompts": {}, "endpoint": ""})
    demo_review._write_ai(p, recs[:2], info["roles"])                   # 4 answers saved, then ReviewDesk was closed
    runs = reporting._ai_runs(p)
    assert len(runs) == 2 and runs[0]["sent"] == len(recs) and not runs[0]["interrupted"]
    last = runs[-1]
    assert last["interrupted"] and last["done"] == 4 and last["sent"] == 3
    rows = reporting._run_rows(runs)
    assert rows[-1][1] == "interrupted" and rows[-1][3] == "3" and rows[-1][4].startswith("4 (2 were retries)")
    assert rows[-1][6].startswith("interrupted")
    old = reporting._run_rows([dict(runs[0], sent=None, checked=254)])          # an audit entry from before 1.3.1
    assert old[0][3] == "254 checked (how many were sent was not recorded)"
    assert ai.Runner.status(p).get("running") is not True


def test_register_searches_in_the_search_log_are_counted_once_outside_the_diagram(tmp_path):
    from reviewdesk import readiness
    from reviewdesk.project import Project
    p = Project.create(str(tmp_path / "regs"), "A review", "Ada Lovelace", "AL")
    p.save_search("AL", {"database": "ClinicalTrials.gov", "platform": "clinicaltrials.gov", "date": "2026-11-02", "results": "3",
                         "string": "triage AND artificial intelligence"})
    p.save_search("AL", {"database": "WHO ICTRP", "platform": "trialsearch.who.int", "date": "2026-11-02", "results": "2",
                         "string": "triage"})
    # the set-up box names ClinicalTrials.gov again (logged: not counted twice), ANZCTR and ISRCTN (not logged: listed)
    p.update_settings("AL", {"registers": [{"name": "ClinicalTrials.gov", "records": 3}, {"name": "ANZCTR", "records": 4},
                                           {"name": "ISRCTN", "records": ""}]})
    assert prisma.registers_not_imported(p) == {"ClinicalTrials.gov": 3, "WHO ICTRP": 2, "ANZCTR": 4, "ISRCTN": None}
    D = prisma.counts(p)["db"]
    assert D["identified_registers"] == 0 and D["registers_not_imported"]["WHO ICTRP"] == 2
    flat = " ".join(" ".join(prisma.notes(p)).split())
    assert ("* Registers also searched, whose records were not imported into ReviewDesk, so they are in none of these "
            "numbers: ClinicalTrials.gov (3 records found), WHO ICTRP (2 records found), ANZCTR (4 records found), ISRCTN "
            "(number of records not recorded).") in flat
    # the box says its count is of the records imported (none here), keeping the template's box
    assert "Registers (n = 0 imported)*" in prisma.svg(p)
    items = [x for st in readiness.check(p, "AL")["steps"] for x in st["warnings"] + st["missing"] if x["key"] == "registers_not_imported"]
    assert items and "WHO ICTRP (2 records)" in items[0]["text"] and not items[0]["blocks"]
    t = text(reporting.search_docx(p, "AL"))
    assert t.count("The records this search found were not imported into ReviewDesk") == 2
    assert "[Missing from the search log for ANZCTR:" in t and "[Missing from the search log for ClinicalTrials.gov" not in t
    # once an export is imported under the register's name, its records are in the diagram and no longer listed here
    p.add_import("AL", "who.ris", demo_review._ris([{"title": "A registered trial of AI triage", "year": "2026", "role": "x"}]),
                 {"set": "main", "database": "WHO ICTRP", "platform": "", "search_date": "2026-11-02", "search_id": "",
                  "notes": "", "part": ""})
    assert "WHO ICTRP" not in prisma.registers_not_imported(p)


def test_risk_of_bias_tables_name_an_overall_judgement_that_breaks_the_tools_rule(tmp_path):
    info = demo_review.build(str(tmp_path / "rob"))
    p, k = info["p"], info["key"]["a"]
    aid = next(a for (s, a), d in review.rob_rows(p)["defs"].items() if s == k)
    label = next(s_["label"] for s_ in review.studies(p) if s_["key"] == k)
    before = text(reporting.rob_docx(p, "CH"))
    assert "does not follow the tool's rule" not in before
    review.rob_agree(p, "CH", k, aid, "D1", "Critical risk", "Unmeasured severity at arrival")   # overall stays "Serious risk"
    t = text(reporting.rob_docx(p, "NH"))
    assert "Not final. For 1 assessment the agreed overall judgement does not follow the tool's rule" in t
    assert f"Check {label} (Under-triage): The overall judgement (Serious risk) is less severe than D1" in t
    # every domain needs a judgement now: a blank cell is "not finished" or "not yet agreed", never filled in as Not applicable
    assert "(not finished)" in t and 'r["both_done"] and code in t.get("optional"' not in open(reporting.__file__, encoding="utf-8").read()
