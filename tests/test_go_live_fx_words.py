"""Final audit before 1.3.0 was published: the generated documents and workbooks, their wording, and the credits for
third-party material (fixer fx-words). Each test fails without its fix."""
import io
import zipfile
from xml.etree import ElementTree as ET

import pytest

import demo_review
from reviewdesk import exports, reporting

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_text(data: bytes) -> str:
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    return "\n".join("".join(t.text or "" for t in p.iter(W + "t")) for p in root.iter(W + "p"))


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    return demo_review.build(str(tmp_path_factory.mktemp("words") / "AI triage"))


# ------------------------------------------------------------------------------------------------ upgrade S1
def test_a_sample_result_stored_by_1_0_0_without_the_sample_counts_still_reports(demo):
    """1.0.0 stored the AI's accuracy on the agreement sample without 'sample_answered' and 'sample_records': the
    lead's PRISMA page, the methods summary and the methods text failed with KeyError."""
    p = demo["p"]
    st = p.state()
    ai_ = st["sets"]["main"]["sample_result"]["ai"]
    n = ai_["combined"]["n"]
    ai_.pop("sample_answered")
    ai_.pop("sample_records")
    p.save_state(st, "CH")
    t = exports.methods_summary(p, True)
    assert f"on the {n:,} sample records both models answered" in t and "sample records both models answered" in t
    assert " of None" not in t
    reporting.methods_docx(p, "CH")                    # made without error
    reporting.ai_docx(p, "CH")


# ------------------------------------------------------------------------------------------------ A2, C1, B4: workbooks
def parts_parse(data: bytes) -> dict:
    z = zipfile.ZipFile(io.BytesIO(data))
    return {n: ET.fromstring(z.read(n)) for n in z.namelist() if n.endswith((".xml", ".rels"))}


def sheet_names(data: bytes) -> list:
    wb = parts_parse(data)["xl/workbook.xml"]
    return [s.get("name") for s in wb.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet")]


def test_sheet_names_follow_excels_rules_and_a_double_quote_does_not_break_the_workbook():
    from reviewdesk import util
    data = util.xlsx([('Outcomes "as reported"', [["h"]]), ("Extraction", [["h"]]), ("extraction", [["h"]]),
                      ("'quoted'", [["h"]]), ("History", [["h"]]), ("a:b/c?d*e[f]g\\h", [["h"]]), ("", [["h"]])])
    names = sheet_names(data)
    assert names[0] == 'Outcomes "as reported"'
    assert len({n.casefold() for n in names}) == len(names)
    assert all(len(n) <= 31 and n[0] != "'" and n[-1] != "'" and n.casefold() != "history"
               and not set(n) & set(":\\/?*[]") for n in names)


def test_renaming_sheets_always_ends():
    import threading
    from reviewdesk import util
    got = []
    t = threading.Thread(target=lambda: got.append(util.xlsx([("A" * 28 + "_3", [["h"]]), ("A" * 31, [["h"]]),
                                                               ("A" * 31, [["h"]])])), daemon=True)
    t.start()
    t.join(10)
    assert got and len(set(n.casefold() for n in sheet_names(got[0]))) == 3


def test_a_long_cell_is_cut_before_escaping_and_a_lone_surrogate_is_dropped():
    from reviewdesk import util
    data = util.xlsx([("S", [["h"], ["a" + "&" * 40000], ["bad \ud83d char"]])])
    ws = parts_parse(data)["xl/worksheets/sheet1.xml"]
    texts = [t.text or "" for t in ws.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")]
    assert len(texts[1]) == util.XLSX_CELL_MAX and texts[2] == "bad  char"


# ------------------------------------------------------------------------------------------------ B4: Word tables
def test_a_long_caption_cut_at_an_ampersand_still_makes_a_document():
    from reviewdesk import docx
    d = docx.Document("T")
    d.table(["a", "b"], [["1", "2"]], caption="x" * 247 + "& more")
    parts_parse(d.to_bytes())


def test_a_table_with_more_columns_than_the_page_holds_is_split_into_parts():
    import re
    from reviewdesk import docx
    d = docx.Document("T")
    d.table(["Study", "Design"] + [f"I{k}" for k in range(60)],
            [[docx.Cell("A section", span=62)], ["Smith 2020", "Trial"] + ["Yes"] * 60], caption="Table 2. A tool",
            notes=["Items: every one."], repeat=2)
    data = d.to_bytes()
    parts_parse(data)
    doc = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode()
    widths = [int(x) for x in re.findall(r'<w:gridCol w:w="(-?\d+)"', doc)]
    assert min(widths) >= docx.MIN_COL
    t = docx_text(data)
    n = doc.count("<w:tbl>")
    assert n > 1 and t.count("Smith 2020") == n and f"Table 2. A tool (part {n} of {n})" in t
    assert t.count("Items: every one.") == 1 and all(f"I{k}" in t for k in range(60))


# ------------------------------------------------------------------------------------------------ A8: one reviewer
@pytest.fixture(scope="module")
def solo(tmp_path_factory):
    import helpers
    from reviewdesk import review, robplan, screening
    from reviewdesk.project import Project
    base = tmp_path_factory.mktemp("solo")
    p = Project.create(str(base / "solo"), "Solo review", "Sam Solo", "SS")
    p.update_settings("SS", {"question": "Does X work?", "criteria": {"include": ["Adults"], "exclude": ["Animals"], "rules": []},
                             "screening": {"mode": "single", "single_reviewer": "SS", "calibration_size": 0},
                             "fulltext": {"mode": "single"}})
    robplan.save_plan(p, "SS", helpers.rob_plan())
    e = p.save_search("SS", {"database": "MEDLINE", "platform": "Ovid", "date": "2026-10-01", "results": "12", "string": "x"})
    p.add_import("SS", "m.ris", helpers.ris(*helpers.tiny_records(12)),
                 {"set": "main", "database": "MEDLINE", "platform": "Ovid", "search_date": "2026-10-01",
                  "search_id": e["id"], "notes": "", "part": ""})
    p.process_imports("SS")
    screening.start(p, "SS", "main")
    order = p.state_ro()["sets"]["main"]["order"]
    for r in order:
        p.add_decision("SS", r, "ta", "include" if r in order[:3] else "exclude")
    p = Project(p.dir)
    ft = sorted(screening.ft_outcomes(p))
    p.add_decision("SS", ft[0], "ft", "include")
    p.add_decision("SS", ft[1], "ft", "include")
    p.add_decision("SS", ft[2], "ft", "exclude", "Wrong population")
    p = Project(p.dir)
    review.save_form(p, "SS", {**review.builtin_form(p), "first": "SS", "second": ""})
    for s in review.studies(p):
        review.save_value(p, "SS", s["key"], "country", "UK")
        review.set_done(p, "SS", s["key"], True)
        aid = review.define_assessment(p, "SS", s["key"], "rct", "primary", "")["a"]
        for code, _ in review.tool_def(p, "rob2")["domains"]:
            review.judge(p, "SS", s["key"], aid, code, "Low risk", "")
        review.rob_done(p, "SS", s["key"], aid, True)
    return Project(p.dir)


def test_a_one_reviewer_review_never_speaks_of_two_people(solo):
    p = solo
    ch = docx_text(reporting.characteristics_docx(p, "SS"))
    rob = docx_text(reporting.rob_docx(p, "SS"))
    exc = docx_text(reporting.excluded_docx(p, "SS"))
    inc = docx_text(reporting.included_docx(p, "SS"))
    summary = exports.methods_summary(p, True)
    for t in (ch, rob, exc, inc):
        assert "both extractors" not in t.lower() and "both reviewers" not in t.lower()
        assert "the reviewers" not in t and "second extractor confirmed" not in t
    assert "by the one extractor" in ch and "by the one reviewer who assessed risk of bias" in rob
    assert "the one reason for exclusion the reviewer recorded" in exc and "named as the reviewer named it" in inc
    assert "if Sam Solo voted Include or Maybe" in summary and "any reviewer who screened it" not in summary


def test_a_one_reviewer_calibration_round_is_described_without_a_second_reviewer(solo, tmp_path):
    import helpers
    from reviewdesk.project import Project
    folder = str(tmp_path / "solo")
    helpers.copytree(solo.dir, folder)
    p = Project(folder)
    st = p.state()
    sv = st["sets"]["main"]
    sv["calibration_rounds"] = [sv["order"][-3:]]
    sv["calibration_results"] = [{"n": 0, "agreement": None, "kappa": None}]
    p.save_state(st, "SS")
    t = exports.methods_summary(Project(folder), True)
    assert "Sam Solo first screened 3 records as a practice (calibration) round" in t
    assert "Calibration round 1" not in t and "calibration disagreements" not in t


# ------------------------------------------------------------------------------------------------ B1: one, none and "yet"
def test_a_title_ending_in_a_question_mark_and_pages_ending_in_a_full_stop_are_cited_cleanly():
    r = {"title": "Does triage work?", "journal": "J Test", "year": "2020", "pages": "1-9.", "authors": "Smith, J"}
    t = reporting.cite(r, ident=False)
    assert "work?. " not in t and "work? J Test" in t and "1-9." in t and "1-9.." not in t


def test_second_looks_still_to_come_are_not_described_as_done():
    t = exports.second_look_text({"total": 5, "forward": 0, "included": 0, "pending": 5}, "Nadine Hayes", "Colin Hawthorne")
    assert "screened again by" not in t and "Colin Hawthorne has not screened them yet." in t
    t = exports.second_look_text({"total": 5, "forward": 1, "included": 0, "pending": 2}, "Nadine Hayes", "Colin Hawthorne")
    assert "so far 3 have been screened again (1 went to full text), and 2 are still to be screened." in t
    t = exports.second_look_text({"total": 1, "forward": 1, "included": 1, "pending": 0}, "Nadine Hayes", "Colin Hawthorne")
    assert "1 was kept by the AI and screened again by Colin Hawthorne" in t


def test_finished_counts_read_right_for_none_one_and_all():
    assert reporting._finished(True, 0, 1, "included study", "included studies") == \
        "Neither extractor has finished the included study yet."
    assert reporting._finished(True, 2, 2) == "Both extractors have finished all 2 studies."
    assert reporting._finished(False, 1, 3) == "The extractor has finished 1 of the 3 studies."


def test_the_suggested_start_never_says_studies_have_no_assessment_when_they_have_one():
    from reviewdesk import certainty
    S = lambda label, *tools: {"label": label, "rob": [{"tool": t, "design": ""} for t in tools], "design": ""}  # noqa: E731
    for info in ([S("A 2020", "probast_ai")], [S("A 2020", "x_abcdef")], [S("A 2020", "quips"), S("B 2021", "robins_e")]):
        why = certainty.suggest_start({"kind": "effect"}, info)["why"]
        assert "no risk-of-bias assessment" not in why and "risk-of-bias tool" in why
    why = certainty.suggest_start({"kind": "effect"}, [S("A 2020", "quadas2"), S("B 2021", "probast_ai")])["why"]
    assert why.startswith("Some of these studies were assessed with QUADAS-2")
    why = certainty.suggest_start({"kind": "effect"}, [S("A 2020", "rob2"), S("B 2021", "x_abcdef")])["why"]
    assert why.startswith("A 2020 is a randomised trial") and "could not tell the design of B 2021" in why


def test_one_record_is_one_record(demo):
    p = demo["p"]
    import re
    assert not re.search(r"(?<![\d,])1 (records|reports|studies)\b", exports.methods_summary(p, True))


# ------------------------------------------------------------------------------------------------ C5
def test_an_answer_called_no_information_on_a_leads_ordered_scale_is_an_ordinary_answer():
    from reviewdesk import robtools
    t = robtools.custom_runtime("x_abcdef", {
        "name": "A tool", "items": [{"id": "I1", "label": "First"}, {"id": "I2", "label": "Second"}],
        "scale": {"answers": ["Low", "No information", "High"], "ordered": True}, "overall": {"use": True, "rule": True}})
    assert robtools.overall_problems(t, {"I1": "No information", "I2": "Low", "O": "No information"}) == []
    probs = robtools.overall_problems(t, {"I1": "No information", "I2": "Low", "O": "Low"})
    assert probs and "serious or critical" not in " ".join(probs) and "I1 First: No information" in probs[0]
    probs = robtools.overall_problems(t, {"I1": "High", "I2": "Low", "O": "No information"})
    assert probs and "serious or critical" not in probs[0] and "I1 First: High" in probs[0]


def test_a_one_reviewer_review_is_not_told_to_wait_for_a_second_person(solo):
    from reviewdesk import certainty
    st, why = reporting.status(solo, "SS", "rep_agreement")
    assert st == "none" and "One person does the screening" in why
    assert not any("Two reviewers are not chosen" in x["text"] for x in certainty.readiness(solo, "SS"))


# ------------------------------------------------------------------------------------------------ install S8: credits
def test_prisma_2020_is_credited_with_its_licence_and_as_adapted(demo):
    from reviewdesk import prisma
    cc = "CC BY 4.0, https://creativecommons.org/licenses/by/4.0/"
    assert prisma.SOURCE.startswith("Adapted from Page MJ, et al. BMJ 2021;372:n71") and cc in prisma.SOURCE
    t = docx_text(reporting.checklist_docx(demo["p"], "CH"))
    assert "Adapted from the PRISMA 2020 checklist" in t and "CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)" in t


def test_press_is_cited_and_the_prompts_are_not_called_the_press_checklist(demo):
    import json
    import os
    here = os.path.dirname(os.path.abspath(reporting.__file__))
    g = open(os.path.join(here, "guidance.json"), encoding="utf-8").read()
    js = open(os.path.join(here, "static", "views_search.js"), encoding="utf-8").read()
    assert "PRESS checklist" not in g and "PRESS checklist" not in js and "PRESS peer-review checklist" not in js
    assert "covering the areas of PRESS 2015 (McGowan J, et al. J Clin Epidemiol 2016;75:40-6)" in js
    assert len(json.loads(g)["checklist"]) == 6                 # ticks are stored by position: still six prompts
    t = docx_text(reporting.search_docx(demo["p"], "CH"))
    assert "PRESS checklist" not in t


def test_grade_wording_is_credited_where_it_is_shown():
    from reviewdesk import certainty
    assert "Balshem et al. (2011)" in certainty.INTRO and "Santesso et al. (2020)" in certainty.INTRO
    for ref in ("J Clin Epidemiol 2011;64:401-6", "Evid Based Med 2017;22:85-7", "J Clin Epidemiol 2020;119:126-35"):
        assert ref in certainty.GRADE_REFS


def test_the_risk_of_bias_tools_are_not_quoted_at_length():
    import os
    import re
    from reviewdesk import robtools
    src = open(robtools.__file__, encoding="utf-8").read()
    assert "it is appropriate to have an overall judgment" not in src
    assert "there are no rules available" not in src
    assert not re.search(r"timing of identification or recruitment of participants in a cluster-randomized trial", src)
    assert os.path.isfile(robtools.__file__)


# ------------------------------------------------------------------------------------------------ methods SHOULD FIX 1
def test_a_database_search_logged_without_its_export_is_named_not_counted_as_nothing(demo):
    """The demonstration review logs CINAHL (57 records) with no export imported."""
    from reviewdesk import prisma
    p = demo["p"]
    D = prisma.counts(p)["db"]
    assert D["databases_not_imported"] == {"CINAHL": 57}
    t = exports.methods_summary(p, True)
    assert f"Database searches returned {D['identified_databases']:,} records in the exports imported into ReviewDesk" in t
    assert "CINAHL (57 records) was also searched, but its records were not imported into ReviewDesk" in t
    assert "[Authors to complete: what was done with the records from CINAHL" in t
    assert any(x.startswith("† Database searches also run") and "CINAHL (57 records found)" in x for x in prisma.notes(p))
    assert f"Databases, total (n = {D['identified_databases']} imported)†" in prisma.svg(p)
    s = docx_text(reporting.search_docx(p, "CH"))
    assert "Not in this table: CINAHL (57 records found) and ANZCTR (4 records found)." in s
    assert "Records found in: CINAHL" in reporting.counts_csv(p, "CH").decode("utf-8-sig")


# ------------------------------------------------------------------------------------------------ methods SHOULD FIX 2
@pytest.fixture(scope="module")
def graded(tmp_path_factory):
    info = demo_review.build(str(tmp_path_factory.mktemp("graded") / "AI triage"))
    demo_review.grade(info)
    return info


def test_a_kept_statement_worded_more_strongly_than_its_certainty_says_so_wherever_it_is_printed(graded):
    """Under-triage is rated low, but its kept statement says 'probably' (moderate)."""
    from reviewdesk import certainty
    p = graded["p"]
    o = next(o for o in certainty.evidence_profile(p, "CH")["outcomes"] if o["statement_check"].get("kept"))
    assert o["statement_check"]["stronger"]
    foot = next(f["text"] for f in o["footnotes"] if f["item"] == "Plain-language statement")
    assert foot.startswith("Worded more strongly than GRADE's standard wording for low certainty, which is “may”; "
                           "the review team kept this wording because: ")
    mt = certainty.method_text(p)
    assert "worded more strongly than the standard wording for that certainty" in mt and "standard wording is “may”" in mt
    assert "except for" not in mt                       # no longer tucked inside the sentence about accuracy outcomes
    sof = docx_text(reporting.sof_docx(p, "CH"))
    assert "Worded more strongly than GRADE's standard wording for low certainty" in sof
    xl = certainty.profile_xlsx(p, "CH")
    sheets = [zipfile.ZipFile(io.BytesIO(xl)).read(n).decode() for n in zipfile.ZipFile(io.BytesIO(xl)).namelist()
              if n.startswith("xl/worksheets/")]
    assert any("Worded more strongly than GRADE" in x for x in sheets)
    chk = certainty.check_statement("Drug A probably reduces pain.", 2)
    assert chk["stronger"] and "will say that it is worded more strongly" in chk["why"]
    assert not certainty.check_statement("Drug A may reduce pain.", 3)["stronger"]      # more cautious: neutral


# ------------------------------------------------------------------------------------------------ methods SHOULD FIX 3, MINOR 8
def test_sensitivity_is_called_pooled_only_when_an_outcome_pools_its_results():
    from reviewdesk import certainty
    a = {"interval": "95% CI", "mode": "pooled", "sens": "0.86 (95% CI 0.79 to 0.91)", "spec": "0.80 (95% CI 0.7 to 0.9)",
         "prevalence": []}
    o = {"kind_id": "accuracy", "comparison": "The tool against expert triage", "accuracy": a}
    labels = lambda group: [k for k, _ in reporting._sof_header(group[0], None, group)]  # noqa: E731
    assert "Pooled sensitivity" in labels([{**o, "synthesis": "pooled", "studies": 3}])
    one = labels([{**o, "synthesis": "narrative", "studies": 1}])
    assert "Sensitivity (from the single study)" in one and not any(x.startswith("Pooled") for x in one)
    assert "Sensitivity (as entered by the review team; not pooled)" in labels([{**o, "synthesis": "narrative", "studies": 4}])
    assert certainty.figures_source([{"synthesis": "narrative", "studies": 1}]) == "single"


def test_the_per_1000_note_says_how_false_results_are_worked_out(graded):
    from reviewdesk import certainty
    p = graded["p"]
    assert certainty.COMPLEMENT_NOTE in certainty.method_text(p)
    assert certainty.COMPLEMENT_NOTE in docx_text(reporting.sof_docx(p, "CH")).replace("\n", " ")
