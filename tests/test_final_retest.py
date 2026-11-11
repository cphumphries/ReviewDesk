"""What the final first-time-user re-test found, on the server side.

F2: records found by other methods (citation searching, hand-searching) are the citation-searching step's own work
until they are screened: the Overview and the Reports page say so, as they do for database records, and a logged
other-methods search with no export file is a Check there too; the PRISMA counts stay right throughout. F3: once the
risk-of-bias plan is recorded, the general form's Study design item offers the plan's designs (plus Other), and the
design extraction records chooses the plan's design. F6: a tool's version and citation are not doubled ("version
Version", "1-9.."). F7: a lead-defined tool's overall judgement on its items' ordered answers is never less severe than
the worst item unless the lead says otherwise. F8: a test's name inside a sentence is in sentence case. And the set-up
summary's words (First AI model; Records from the update search are screened: ...)."""
from __future__ import annotations


import pytest

from helpers import rob_plan
from reviewdesk import certainty, prisma, readiness, reporting, review, robplan, robtools, screening
from reviewdesk.project import Project
from test_reporting import text

TEAM = [{"id": "SP", "name": "Dr Sam Patel", "role": "lead"}, {"id": "AM", "name": "Alex Morgan", "role": "reviewer"}]
REASONS = ["Wrong population", "Wrong intervention", "Wrong comparator", "Wrong study design", "Wrong outcomes",
           "Wrong publication type", "Not in English"]
TOOL = {"name": "Short appraisal checklist for qualitative process evaluations", "version": "Version 1, 2026",
        "citation": "Invented A, Example B. A short appraisal checklist for qualitative process evaluations. Example "
                    "Journal 2026;1:1-9.",
        "items": [{"label": "Aims stated"}, {"label": "Methods fit the aims"}], "scale": {"preset": "ynu"},
        "overall": {"use": True, "same": True}}
TITLES = [("Supervised strengthening exercise for knee osteoarthritis: a randomised controlled trial", "Okafor, Chidi"),
          ("Group exercise classes for knee osteoarthritis: a cluster randomised trial in community centres", "Takahashi, Kenji"),
          ("Accuracy of clinical examination for knee osteoarthritis against X-ray: a diagnostic accuracy study", "Fischer, Anna"),
          ("Experiences of a group exercise programme for knee osteoarthritis: a qualitative process evaluation", "Nakamura, Rei")]
NEW = ("Stepped exercise programme for knee osteoarthritis in general practice: a randomised trial", "Dalton, Grace")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWDESK_HOME", str(tmp_path / "home"))


def ris(rows) -> bytes:
    return "\n".join(f"TY  - JOUR\nTI  - {t}\nAU  - {a}\nPY  - 2022\nJO  - Knee Journal\nAB  - Adults with knee "
                     f"osteoarthritis.\nDO  - 10.5555/{abs(hash(t)) % 10 ** 8}\nER  - \n" for t, a in rows).encode()


def knee(tmp_path, form_first: bool = False) -> Project:
    """The tester's review, small: dual screening, four reports included at full text by both, the plan with a tool the
    lead defined. form_first: the extraction form (with its extractors) saved before the plan, as the guided set-up does."""
    p = Project.create(str(tmp_path / "knee"), "Exercise programmes for adults with knee osteoarthritis", "Dr Sam Patel", "SP")
    p.update_settings("SP", {"reviewers": TEAM, "question": "Exercise for knee osteoarthritis?", "ft_reasons": REASONS,
                             "criteria": {"include": ["Adults with knee osteoarthritis"], "exclude": ["Knee surgery"], "rules": []},
                             "screening": {"mode": "dual", "reviewers": ["AM", "SP"], "calibration_size": 0}})
    if form_first:
        review.save_form(p, "SP", {**review.GENERIC, "first": "AM", "second": "SP"})
    robplan.save_plan(p, "SP", rob_plan("rct", "cluster_rct", "dta", extra=[
        {"name": "Qualitative process evaluations", "tool": "x_ab12cd34"}], tools={"x_ab12cd34": TOOL}))
    s = p.save_search("SP", {"database": "MEDLINE", "platform": "Ovid", "date": "2026-11-02", "results": "4", "string": "knee.mp."})
    p.add_import("SP", "medline.ris", ris(TITLES), {"set": "main", "database": "MEDLINE", "search_id": s["id"]})
    p.process_imports("SP")
    screening.start(p, "SP", "main")
    for who in ("AM", "SP"):
        for rid in sorted(r["id"] for r in p.records()):
            p.add_decision(who, rid, "ta", "include")
            p.add_decision(who, rid, "ft", "include")
    return p


def step(r: dict, sid: str) -> dict:
    return next(s for s in r["steps"] if s["id"] == sid)


def keys(items) -> set:
    return {x["key"] for x in items}


def citation_search(p: Project) -> None:
    """A reference-list file: one record new, one already found by the database search."""
    s = p.save_search("SP", {"database": "Reference lists of the included studies", "platform": "by hand", "date": "2026-11-03",
                             "results": "2", "string": "Backward citation searching"})
    p.add_import("SP", "reference_lists.ris", ris([NEW, TITLES[0]]),
                 {"set": "other", "database": "Reference lists of the included studies", "search_id": s["id"]})
    p.process_imports("SP")


# ================================================================================================ F2
def test_unscreened_records_from_other_methods_are_the_citation_steps_own_work(tmp_path):
    p = knee(tmp_path)
    citation_search(p)
    C = prisma.counts(p)["other"]
    assert (C["identified"], C["duplicates"], C["records"], C["screened"], C["pending_ta"]) == (2, 1, 1, 0, 1)
    lead = readiness.check(p, "SP")
    cit = step(lead, "citations")
    assert cit["status"] == "now" and cit["route"] == "screening" and cit["page"] == "screen"   # the work is screening them
    item = next(x for x in cit["missing"] if x["key"] == "start_other")
    assert item["text"].startswith("1 record found by other methods (citation searching, hand-searching) has not been screened "
                                   "yet. Start its screening with the Start screening button in the Other methods panel")
    assert item["fix"] == "screening" and item["who"] == "lead"
    assert step(lead, "screen")["status"] == "done"            # the database records' screening is still finished
    rep = next(x for x in step(lead, "report")["warnings"] if x["key"] == "not_screened_other")
    assert rep["text"].startswith("1 record found by other methods has not been screened yet")
    # the reviewer waits for the lead to start it
    am = step(readiness.check(p, "AM"), "citations")
    assert any(x["waiting"] and "Waiting for Dr Sam Patel (the project lead) to start screening the 1 record found by other "
               "methods" in x["text"] for x in am["warnings"])
    # started: the records are each reviewer's to screen, in this step
    screening.start(p, "SP", "other")
    cit = step(readiness.check(p, "AM"), "citations")
    assert "task_screen_other" in keys(cit["missing"]) and cit["route"] == "screening" and cit["status"] == "now"
    assert "not_screened_other" not in keys(step(readiness.check(p, "SP"), "report")["warnings"])
    assert "open_ta" in keys(step(readiness.check(p, "SP"), "report")["warnings"])
    new = next(r["id"] for r in p.records() if r["set"] == "other")
    for who in ("AM", "SP"):
        p.add_decision(who, new, "ta", "exclude")
    cit = step(readiness.check(p, "SP"), "citations")
    assert cit["status"] == "done" and cit["route"] == "import" and cit["missing"] == []
    C = prisma.counts(p)["other"]
    assert (C["records"], C["screened"], C["ta_excluded"], C["pending_ta"]) == (1, 1, 1, 0) and not prisma.counts(p)["checks"]


def test_an_other_methods_search_with_no_file_is_a_check_on_the_overview_and_the_reports(tmp_path):
    p = knee(tmp_path)
    citation_search(p)
    screening.start(p, "SP", "other")
    new = next(r["id"] for r in p.records() if r["set"] == "other")
    for who in ("AM", "SP"):
        p.add_decision(who, new, "ta", "exclude")
    hand = p.save_search("SP", {"database": "Hand-searching Osteoarthritis Practice 2020-2026", "platform": "by hand",
                                "date": "2026-11-03", "results": "3", "string": "Tables of contents"})
    p.save_search("SP", {"database": "ClinicalTrials.gov", "platform": "web", "date": "2026-11-03", "results": "5", "string": "knee"})
    r = readiness.check(p, "SP")
    cit = step(r, "citations")
    assert cit["status"] == "now"                              # (so its items are on the Overview)
    item = next(x for x in cit["warnings"] if x["key"] == "no_file_other")
    assert item["text"].startswith("The Hand-searching Osteoarthritis Practice 2020-2026 search of 3 November 2026 has no "
                                   "export file, so the records it found are not screened in ReviewDesk")
    assert "ClinicalTrials.gov" not in item["text"]           # a register searched and only listed is not this step's
    assert "record 0 as the number found" in item["text"] and not item["blocks"]
    assert "no_file_other" in keys(step(r, "report")["warnings"])
    # it found nothing new: recording 0 settles it
    p.save_search("SP", {**hand, "results": "0"})
    r = readiness.check(p, "SP")
    assert step(r, "citations")["status"] == "done" and "no_file_other" not in keys(step(r, "report")["warnings"])


def test_records_from_hand_searching_before_screening_wait_for_the_database_records(tmp_path):
    """Hand-searching run with the database searches: its records are not screened before the database records' own
    screening (with its calibration and agreement sample) has started."""
    p = Project.create(str(tmp_path / "early"), "Knee", "Dr Sam Patel", "SP")
    p.update_settings("SP", {"reviewers": TEAM, "question": "Exercise?", "ft_reasons": REASONS,
                             "criteria": {"include": ["Knee osteoarthritis"], "exclude": ["Surgery"], "rules": []},
                             "screening": {"mode": "dual", "reviewers": ["AM", "SP"], "calibration_size": 0}})
    robplan.save_plan(p, "SP", rob_plan("rct"))
    p.add_import("SP", "medline.ris", ris(TITLES), {"set": "main", "database": "MEDLINE"})
    p.add_import("SP", "handsearch.ris", ris([NEW]), {"set": "other", "database": "Hand-searching Knee Journal"})
    p.process_imports("SP")
    cit = step(readiness.check(p, "SP"), "citations")
    assert cit["status"] == "now" and "start_other" not in keys(cit["missing"])     # not yet: main screening first
    screening.start(p, "SP", "main")
    cit = step(readiness.check(p, "SP"), "citations")
    assert "start_other" in keys(cit["missing"]) and cit["route"] == "screening"


def test_a_review_without_other_methods_is_unchanged(tmp_path):
    p = knee(tmp_path)
    r = readiness.check(p, "SP")
    cit = step(r, "citations")
    assert cit["status"] == "now" and cit["route"] == "import" and cit["missing"] == cit["warnings"] == []
    assert not {"not_screened_other", "no_file_other"} & keys(step(r, "report")["warnings"])


# ================================================================================================ the set-up summary
def test_the_set_up_summary_uses_the_words_of_the_set_up_page(tmp_path):
    p = Project.create(str(tmp_path / "s"), "Knee", "Dr Sam Patel", "SP")
    p.update_settings("SP", {"reviewers": TEAM, "screening": {"mode": "sample_then_single", "reviewers": ["AM", "SP"],
                                                              "single_reviewer": "AM", "second_reviewer": "SP",
                                                              "ai_second_screener": True},
                             "other_sets": {"update": {"mode": "inherit"}, "other": {"mode": "single"}}})
    t = readiness.summary_text(p)
    assert "  First AI model: " in t and "  Second AI model: " in t and "pass A" not in t and "pass B" not in t
    assert "Records from the update search are screened: the same way the main screening ended" in t
    assert "Records from other methods (citation searching, hand-searching) are screened: by one reviewer" in t
    assert "screened by:" not in t


# ================================================================================================ F3
def test_the_general_forms_study_design_offers_the_plans_designs(tmp_path):
    p = Project.create(str(tmp_path / "f"), "Knee", "Dr Sam Patel", "SP")
    p.update_settings("SP", {"reviewers": TEAM})
    design = lambda: next(x for x in review.fields(review.form(p)) if x["id"] == "design")   # noqa: E731
    assert design()["options"] == review.DESIGN_OPTIONS                  # no plan yet: the built-in choices
    robplan.save_plan(p, "SP", rob_plan("rct", "dta", extra=[{"name": "Qualitative process evaluations", "tool": "x_ab12cd34"}],
                                        tools={"x_ab12cd34": TOOL}))
    want = ["Randomised trials (individually randomised, parallel-group)", "Diagnostic test accuracy studies",
            "Qualitative process evaluations", "Other"]
    assert design()["options"] == want
    # saved as it is (the wizard saves the extractors with the form): still the plan's, and still "not yet adapted"
    review.save_form(p, "SP", {**review.form(p), "first": "AM", "second": "SP"})
    assert design()["options"] == want and readiness._form_unchanged(review.form(p), p)
    # an amendment to the plan: the choices follow it
    robplan.save_plan(p, "SP", rob_plan("rct", "dta", "cluster_rct", extra=[{"name": "Qualitative process evaluations",
                                                                             "tool": "x_ab12cd34"}], tools={"x_ab12cd34": TOOL}))
    assert "Cluster-randomised trials" in design()["options"]
    # the lead's own choices are the lead's: they no longer follow the plan
    f = review.form(p)
    next(x for x in review.fields(f) if x["id"] == "design")["options"] = ["RCT", "Qualitative", "Other"]
    review.save_form(p, "SP", {**f, "first": "AM", "second": "SP"})
    assert design()["options"] == ["RCT", "Qualitative", "Other"] and "options_from" not in design()
    robplan.save_plan(p, "SP", rob_plan("rct", extra=[{"name": "Qualitative process evaluations", "tool": "x_ab12cd34"}],
                                        tools={"x_ab12cd34": TOOL}))
    assert design()["options"] == ["RCT", "Qualitative", "Other"]


def test_a_form_saved_before_the_plan_follows_it_and_its_answers_still_count(tmp_path):
    """The tester's order: the extractors (and so the form, as built in) saved before the plan. An answer from the
    form's earlier choices ("Randomised trial") still counts, and still finds its design."""
    p = knee(tmp_path, form_first=True)
    assert next(x for x in review.fields(review.form(p)) if x["id"] == "design")["options"][0] == \
        "Randomised trials (individually randomised, parallel-group)"
    st = {s["label"]: s["key"] for s in review.studies(p)}
    k_rct, k_dta, k_qual, k_cl = (st[x] for x in ("Okafor 2022", "Fischer 2022", "Nakamura 2022", "Takahashi 2022"))
    pl = robplan.plan(p)
    qual = next(d["id"] for d in pl["designs"] if d["name"] == "Qualitative process evaluations")
    review.save_value(p, "AM", k_dta, "design", "Diagnostic test accuracy studies")
    review.save_value(p, "AM", k_qual, "design", "Qualitative process evaluations")
    review.save_value(p, "AM", k_cl, "design", "Cluster-randomised trials")
    review.save_value(p, "AM", k_rct, "design", "Randomised trial")          # the earlier answer can be given again
    # the design each answer fits is chosen when an assessment is added (the exact name first, then the word rules)
    assert robplan.suggested(p, k_dta, "AM")["designs"][0] == "dta"
    assert robplan.suggested(p, k_qual, "AM")["designs"] == [qual]
    assert robplan.suggested(p, k_cl, "AM")["designs"][0] == "cluster_rct"
    assert robplan.suggested(p, k_rct, "AM")["designs"][0] == "rct"
    # once both have finished, the lead can agree a value given from the earlier choices
    review.set_done(p, "AM", k_rct, True)
    review.set_done(p, "SP", k_rct, True, review.checked_sig(review.form(p), review.answers(p)["values"]["AM"][k_rct]))
    review.agree_value(p, "SP", k_rct, "design", "Randomised trial")
    assert robplan.extracted_design(p, k_rct) == "Randomised trial"


def test_every_standard_design_name_fits_its_own_design_first():
    """The general form now offers the plan's names: each fits its own design before any word rule ("Uncontrolled
    before-after studies" would otherwise fit the rule for controlled before-after studies first)."""
    for did, name, _ in robtools.STANDARD_DESIGNS:
        assert robplan.compatible(robplan.STANDARD_PLAN, name)[0] == did, name
    lead = {"designs": [{"id": "d_x1", "name": "Qualitative studies", "tool": "x_1"}]}
    assert robplan.compatible(lead, "Qualitative study") == ["d_x1"]         # singular and plural match
    assert robplan.compatible(lead, "Qualitative process evaluation") == []


# ================================================================================================ F6
def test_a_tools_version_and_citation_are_not_doubled(tmp_path):
    assert robtools.version_words("Version 2, 2025") == "version 2, 2025"
    assert robtools.version_words("version: 3", start=True) == "Version 3"
    assert robtools.version_words("2020 edition") == "version 2020 edition" and robtools.version_words("") == ""
    assert robtools.end_sentence("Example Journal 2026;1:1-9.") == "Example Journal 2026;1:1-9."
    assert robtools.end_sentence("Example Journal 2026;1:1-9") == "Example Journal 2026;1:1-9."
    p = knee(tmp_path)
    review.save_form(p, "SP", {**review.form(p), "first": "AM", "second": "SP"})
    k = {s["label"]: s["key"] for s in review.studies(p)}["Nakamura 2022"]
    qual = next(d["id"] for d in robplan.plan(p)["designs"] if d["name"] == "Qualitative process evaluations")
    review.define_assessment(p, "AM", k, qual, "Experiences of the programme")
    outputs = {"summary": readiness.summary_text(p), "methods": text(reporting.methods_docx(p, "SP")),
               "rob": text(reporting.rob_docx(p, "SP")), "plan rows": repr(review.plan_rows(p))}
    for name, t in outputs.items():
        assert "ersion Version" not in t and "ersion version" not in t, name
        assert "1-9.." not in t and "1-9.;" not in t, name
    assert "Version 1, 2026. Citation: Invented A, Example B." in outputs["summary"]
    assert "Example Journal 2026;1:1-9. 2 items." in outputs["summary"]
    assert "Short appraisal checklist for qualitative process evaluations, version 1, 2026" in outputs["methods"]


# ================================================================================================ F7
def test_the_never_less_severe_rule_is_on_unless_the_lead_says_otherwise():
    base = {k: v for k, v in TOOL.items() if k != "overall"}
    assert robtools.check_custom({**base, "overall": {"use": True, "same": True}})["overall"]["rule"] is True
    assert robtools.check_custom({**base, "overall": {"use": True, "same": True, "rule": False}})["overall"]["rule"] is False
    own = {**base, "scale": {"preset": "own", "answers": ["Met", "Not met"], "ordered": False}}
    assert robtools.check_custom({**own, "overall": {"use": True, "same": True}})["overall"]["rule"] is False   # not in order
    # so an overall "Yes" beside an item answered "No" is questioned when the reviewer finishes
    t = robtools.custom_runtime("x_1", robtools.check_custom({**base, "overall": {"use": True, "same": True}}))
    ids = [c for c, _ in t["domains"] if c != "O"]
    assert robtools.overall_problems(t, {ids[0]: "No", ids[1]: "Yes", "O": "Yes"})
    assert not robtools.overall_problems(t, {ids[0]: "No", ids[1]: "Yes", "O": "No"})


# ================================================================================================ F8
def test_a_tests_name_inside_a_sentence_is_in_sentence_case(tmp_path):
    p = Project.create(str(tmp_path / "g"), "Knee", "Dr Sam Patel", "SP")
    comp = "Clinical examination versus X-ray"
    certainty.define(p, "SP", {"name": "True positives", "importance": "critical", "kind": "accuracy", "comparison": comp,
                               "measure": "", "synthesis": "narrative", "result": "tp", "studies": []})
    certainty.set_accuracy(p, "SP", {"comparison": comp, "condition": "knee osteoarthritis (Kellgren-Lawrence grade 2 or more)",
                                     "threshold": "", "mode": "pooled", "interval": "ci",
                                     "prev": [{"p": "30", "source": "primary care audits"}],
                                     "sens": {"est": "0.80", "lo": "0.70", "hi": "0.88"},
                                     "spec": {"est": "0.75", "lo": "0.65", "hi": "0.83"}})
    d = {"name": "True positives", "kind": "accuracy", "comparison": comp, "result": "tp",
         "_acc": certainty.accuracy_figures(p, {"comparison": comp})}
    low = certainty.statements(d, 2)[0]["text"]
    assert low == ("Of 1,000 people tested, of whom 300 have knee osteoarthritis (Kellgren-Lawrence grade 2 or more), clinical "
                   "examination may correctly identify about 240 of them.")
    assert certainty.statements(d, 1)[0]["text"] == ("The evidence is very uncertain about how many people with knee "
                                                     "osteoarthritis (Kellgren-Lawrence grade 2 or more) clinical examination "
                                                     "correctly identifies.")
    assert "for clinical examination versus X-ray, at a pre-test probability" in certainty.method_text(p)
    # an acronym or a name keeps its capitals, as outcome names do
    for name in ("MRI", "X-ray", "C-reactive protein", "Ottawa Knee Rules", "HbA1c testing"):
        assert certainty.mid_name(name) == name
    assert certainty.mid_name("The AI tool") == "the AI tool" and certainty.mid_name("Clinical examination") == "clinical examination"
