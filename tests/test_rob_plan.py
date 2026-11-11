"""The risk-of-bias plan, set before the work begins: which tool each study design the review includes is assessed
with. It is needed before screening starts; once screening has started only the lead changes it, with a reason, as a
protocol amendment. Reviewers choose a study's design from the plan and the tool follows. Tools the lead defines are
judged, blinded, agreed and reported exactly as the built-in ones.

These tests use a review of anything (exercise for low back pain, say), not the reference review: the tools must not
be tailored to one review."""
from __future__ import annotations

import re

import pytest

from helpers import check_svg, check_xlsx, ris, rob_plan, svg_text, tiny_records
from reviewdesk import exports, readiness, reporting, review, robplan, robtools, screening, util
from reviewdesk.project import MARKER, PermissionDenied, Project, ProjectError
from test_reporting import text

TEAM = [{"id": "AL", "name": "Ann Lead", "role": "lead"}, {"id": "BR", "name": "Ben Reviewer", "role": "reviewer"}]
SETTINGS = {"reviewers": TEAM, "question": "Does exercise therapy reduce pain in adults with chronic low back pain?",
            "criteria": {"include": ["Adults with chronic low back pain", "Exercise therapy"], "exclude": ["Acute pain"], "rules": []},
            "screening": {"mode": "dual", "reviewers": ["BR", "AL"], "calibration_size": 0}}
# A lead-defined appraisal tool: only the labels the lead typed (never a checklist's own wording)
QUAL = {"name": "Qualitative appraisal checklist", "version": "2020 edition", "citation": "Lockwood C, et al. Int J Evid Based "
        "Healthc 2015;13:179-87", "link": "https://example.org/qualitative-checklist",
        "items": ["Methods fit the question", "Participants' voices represented", "Ethics approval reported"],
        "scale": {"preset": "ynu"}, "overall": {"use": True, "same": True, "rule": True}}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWDESK_HOME", str(tmp_path / "home"))


def make(tmp_path, plan: dict | None = None, start: bool = True, studies: int = 3) -> Project:
    """A dual-screening review with `studies` studies included at full text (none if not started)."""
    p = Project.create(str(tmp_path / "review"), "Exercise for chronic low back pain", "Ann Lead", "AL")
    p.update_settings("AL", SETTINGS)
    if plan is not None:
        robplan.save_plan(p, "AL", plan)
    p.add_import("AL", "medline.ris", ris(*tiny_records(6, seed=7)), {"set": "main", "database": "MEDLINE"})
    p.process_imports("AL")
    if start:
        screening.start(p, "AL", "main")
        ids = sorted(r["id"] for r in p.records())
        for who in ("BR", "AL"):
            for rid in ids:
                p.add_decision(who, rid, "ta", "include")
            for k, rid in enumerate(ids[:studies]):
                p.add_decision(who, rid, "ft", "include", study=f"Study {k + 1}")
        review.save_form(p, "AL", {**review.GENERIC, "first": "BR", "second": "AL"})
    return p


def keys(p: Project) -> dict:
    return {s["label"]: s["key"] for s in review.studies(p)}


def judge_all(p, who, key, aid, pick, support="p. 3"):
    t = review.tool_def(p, review.rob_rows(p)["defs"][(key, aid)]["tool"])
    for code, _ in t["domains"]:
        review.judge(p, who, key, aid, code, pick(t, code), support)
    review.rob_done(p, who, key, aid, True)


def extract_design(p, key, design):
    """Both extractors settle the study's design (BR extracts, AL checks)."""
    review.save_value(p, "BR", key, "design", design)
    review.set_done(p, "BR", key, True)
    f = review.form(p)
    review.set_done(p, "AL", key, True, review.checked_sig(f, review.answers(p)["values"]["BR"][key]))


# ================================================================================================ needed before screening
def test_the_plan_is_needed_before_screening_starts_like_the_criteria(tmp_path):
    p = make(tmp_path, start=False)
    with pytest.raises(ProjectError, match="Record the risk-of-bias plan first"):
        screening.start(p, "AL", "main")
    st = next(s for s in readiness.check(p, "AL")["steps"] if s["id"] == "setup")
    item = next(x for x in st["missing"] if x["key"] == "rob_plan")
    assert item["blocks"] and item["fix"] == "setup/robplan" and item["who"] == "lead"
    nh = next(s for s in readiness.check(p, "BR")["steps"] if s["id"] == "setup")
    assert any("record the risk-of-bias plan" in x["text"] for x in nh["warnings"])          # the reviewer waits for it
    out = robplan.save_plan(p, "AL", rob_plan("rct", "nrsi"))
    assert out["changed"] and not out["amendment"] and out["plan"]["before_screening"] is True
    screening.start(p, "AL", "main")
    a = [e for e in p.audit_entries() if e["action"] == "record the risk-of-bias plan"]
    assert a and a[0]["details"]["plan"][0].startswith("Randomised trials (individually randomised, parallel-group): RoB 2")


def test_a_plan_needs_its_designs_tools_and_reasons_and_only_the_lead_records_it(tmp_path):
    p = make(tmp_path, start=False)
    cases = [({"designs": []}, "Add at least one study design"),
             ({"designs": [{"id": "rct", "name": "Randomised trials", "tool": ""}]}, "say why these studies have no risk-of-bias"),
             ({"designs": [{"name": "Case reports", "none": True, "reason": ""}]}, "say why these studies have no"),
             ({"designs": [{"name": "Cohort studies", "tool": "nos"}]}, "choose its tool"),
             ({"designs": [{"name": "Trials", "tool": "rob2"}, {"name": "trials", "tool": "rob2"}]}, "is in the plan twice"),
             ({"designs": [{"name": "Qualitative studies", "tool": "x_1234abcd"}],
               "tools": {"x_1234abcd": {**QUAL, "version": ""}}}, "give the version used"),
             ({"designs": [{"name": "Qualitative studies", "tool": "x_1234abcd"}],
               "tools": {"x_1234abcd": {**QUAL, "citation": ""}}}, "give its citation")]
    for plan, words in cases:
        with pytest.raises(ProjectError, match=words):
            robplan.save_plan(p, "AL", plan)
    with pytest.raises(PermissionDenied):
        robplan.save_plan(p, "BR", rob_plan("rct"))
    with pytest.raises(ProjectError, match="saved on its own"):                 # not through the general settings
        p.update_settings("AL", {"risk_of_bias_plan": rob_plan("rct")})
    assert not robplan.recorded(p)
    # a design with no assessment is allowed, with its reason; it is reported in the methods
    robplan.save_plan(p, "AL", rob_plan("rct", extra=[{"name": "Case reports", "none": True, "reason": "Used to describe harms only"}]))
    assert robplan.plan(p)["designs"][1]["reason"] == "Used to describe harms only"


def test_a_change_before_screening_is_not_an_amendment_but_after_it_needs_a_reason(tmp_path):
    p = make(tmp_path, plan=rob_plan("rct"), start=False)
    out = robplan.save_plan(p, "AL", rob_plan("rct", "cluster_rct"))           # before screening: no reason, no amendment
    assert out["changed"] and not out["amendment"] and not robplan.amendments(p)
    assert robplan.save_plan(p, "AL", rob_plan("rct", "cluster_rct"))["changed"] is False
    screening.start(p, "AL", "main")
    new = rob_plan("rct", "cluster_rct", extra=[{"name": "Qualitative studies", "tool": "x_ab12cd34"}], tools={"x_ab12cd34": QUAL})
    with pytest.raises(ProjectError, match="protocol amendment. Say why"):
        robplan.save_plan(p, "AL", new)
    out = robplan.save_plan(p, "AL", new, "Qualitative studies of patients' views were found and the protocol was amended")
    assert out["amendment"]
    (am,) = robplan.amendments(p)
    assert am["by"] == "AL" and am["reason"].startswith("Qualitative studies of patients' views")
    assert am["changes"] == ["added qualitative studies, assessed with Qualitative appraisal checklist",
                             "defined the tool Qualitative appraisal checklist"]
    assert "Qualitative studies: Qualitative appraisal checklist" in am["after"]
    e = [x for x in p.audit_entries() if x["action"] == "change the risk-of-bias plan (protocol amendment)"]
    assert e and e[0]["details"]["reason"] == am["reason"]
    # reported: the methods text's risk-of-bias section and its amendments, and the checklist's item 24c
    m = text(reporting.methods_docx(p, "AL"))
    day = reporting.nice_date(am["date"])
    assert ("The plan was amended after screening started, as a protocol amendment. On " + day + ", Ann Lead added "
            "qualitative studies, assessed with Qualitative appraisal checklist; defined the tool Qualitative appraisal "
            "checklist (reason: qualitative studies of patients'") in m
    # N5: the list is the plan as it stood when screening started; then the amendment; then the plan as amended
    sec = m[m.index("Risk of bias in included studies"):m.index("Certainty of the evidence")]
    first = sec[sec.index("set the tool for each study design:"):sec.index("The plan was amended")]
    assert "Cluster-randomised trials: RoB 2 for cluster-randomised trials" in first and "Qualitative studies" not in first
    after = sec[sec.index("As amended, the plan gives:"):]
    assert "Qualitative studies: Qualitative appraisal checklist, version 2020 edition" in after
    assert "Added on " + day in text(reporting.rob_docx(p, "AL"))
    summary = exports.methods_summary(p, True)
    assert "changed the risk-of-bias plan (added qualitative studies" in summary
    assert "\nOn " + reporting.nice_date(am["date"]) + ", Ann Lead changed the risk-of-bias plan" in summary   # never a numeral first
    assert "risk-of-bias plan" in reporting.checklist_where(p, "AL")["24c"]
    # the plan stays as it was first recorded: before screening started
    assert robplan.plan(p)["before_screening"] is True and robplan.plan(p)["rev"] == 3


# ================================================================================================ adding an assessment
def test_reviewers_choose_the_design_and_a_design_not_in_the_plan_cannot_be_assessed(tmp_path):
    plan = rob_plan("rct", extra=[{"name": "Case series", "none": True, "reason": "Not used in the synthesis"}])
    p = make(tmp_path, plan=plan)
    k = keys(p)["Study 1"]
    with pytest.raises(ProjectError, match="Choose the study's design from the risk-of-bias plan"):
        review.define_assessment(p, "BR", k, "", "Pain at 12 weeks")
    with pytest.raises(ProjectError, match="not in the risk-of-bias plan, so it cannot be assessed.*adds it to the plan"):
        review.define_assessment(p, "BR", k, "dta", "Pain at 12 weeks")
    case = robplan.plan(p)["designs"][1]["id"]
    with pytest.raises(ProjectError, match=r"says studies of this design \(Case series\) have no risk-of-bias assessment: Not used"):
        review.define_assessment(p, "BR", k, case, "Pain at 12 weeks")
    row = review.define_assessment(p, "BR", k, "rct", "Pain at 12 weeks")
    d = review.rob_rows(p)["defs"][(k, row["a"])]
    assert (d["design"], d["tool"], d["design_name"]) == ("rct", "rob2", "Randomised trials (individually randomised, parallel-group)")
    # the lead adds the design (an amendment, as screening has started); then it can be assessed
    robplan.save_plan(p, "AL", {**plan, "designs": plan["designs"] + [{"id": "dta", "name": "Diagnostic test accuracy studies",
                                                                       "tool": "quadas2"}]}, "Accuracy studies of the clinical test were found")
    review.define_assessment(p, "BR", k, "dta", "Accuracy of the straight-leg raise test")
    v = review.rob_view(p, k, "BR")
    assert [(x["design_name"], x["tool"]) for x in v] == [("Randomised trials (individually randomised, parallel-group)", "rob2"),
                                                         ("Diagnostic test accuracy studies", "quadas2")]


def test_an_old_project_started_without_a_plan_needs_it_before_any_assessment(tmp_path):
    p = make(tmp_path, plan=rob_plan("rct"))
    # as a project from before 1.3.0: no plan, but the tools it expected to use
    s = util.read_json(p.path(MARKER), {})
    s.pop("risk_of_bias_plan")
    s.update({"risk_of_bias_tools": ["rob2", "probast_ai"], "risk_of_bias_other": "QUADAS-2 for the clinical tests"})
    util.write_json_atomic(p.path(MARKER), s)
    k = keys(p)["Study 1"]
    with pytest.raises(ProjectError, match="The risk-of-bias plan has not been recorded yet"):
        review.define_assessment(p, "BR", k, "rct", "Pain at 12 weeks")
    draft = robplan.plan(p)
    assert draft["draft"] and [d["id"] for d in draft["designs"]] == ["rct", "cluster_rct", "dta", "prediction"]
    assert draft["legacy"]["other"] == "QUADAS-2 for the clinical tests"
    rob = next(x for x in readiness.check(p, "AL")["steps"] if x["id"] == "rob")
    item = next(x for x in rob["missing"] if x["key"] == "rob_plan_missing")
    assert "Screening started before ReviewDesk asked for it" in item["text"] and item["fix"] == "setup/robplan"
    robplan.save_plan(p, "AL", {"designs": draft["designs"]})                  # recorded late: not an amendment
    assert not robplan.amendments(p) and robplan.plan(p)["before_screening"] is False
    m = text(reporting.methods_docx(p, "AL"))
    assert "recorded in ReviewDesk on" in m and "after screening had started (on" in m
    assert "[Authors to complete: confirm that this plan matches the registered protocol" in m
    s = p.settings_ro()                                                          # the old settings are kept as they were
    assert s["risk_of_bias_tools"] == ["rob2", "probast_ai"] and s["risk_of_bias_other"] == "QUADAS-2 for the clinical tests"
    review.define_assessment(p, "BR", k, "rct", "Pain at 12 weeks")


# ================================================================================================ the extracted design
def test_a_cluster_trial_assessed_as_individually_randomised_is_flagged(tmp_path):
    """R1: a study extracted as a cluster randomised trial, assessed with RoB 2 for individually randomised trials."""
    p = make(tmp_path, plan=rob_plan("rct", "cluster_rct", "nrsi"))
    k = keys(p)["Study 1"]
    extract_design(p, k, "Cluster randomised trial")
    sug = robplan.suggested(p, k, "BR")
    assert (sug["text"], sug["designs"]) == ("Cluster randomised trial", ["cluster_rct"])
    # the form that adds an assessment warns before it is added, for each design of the plan the study does not fit
    assert set(sug["warnings"]) == {"rct", "nrsi"} and sug["warnings"]["rct"].endswith("You can still add it if extraction is wrong.")
    assert "not “Randomised trials (individually randomised, parallel-group)”. A cluster-randomised trial is assessed with " \
           "RoB 2 for cluster-randomised trials" in sug["warnings"]["rct"]
    aid = review.define_assessment(p, "BR", k, "rct", "Pain at 12 weeks")["a"]    # a warning, never a block
    w = review.rob_view(p, k, "BR")[0]["warning"]
    assert w.startswith("Extraction records this study's design as “Cluster randomised trial”, which fits “Cluster-randomised "
                        "trials” in the risk-of-bias plan, but this assessment uses the design “Randomised trials")
    assert "RoB 2 for cluster-randomised trials, which also judges bias arising from the timing of identification or " \
           "recruitment of participants (D1b)" in w
    rob = next(x for x in readiness.check(p, "AL")["steps"] if x["id"] == "rob")
    assert any(x["key"] == "rob_design" for x in rob["warnings"])
    for who in ("BR", "AL"):
        judge_all(p, who, k, aid, lambda t, c: robtools.scale_for(t, c)[0])
    t = text(reporting.rob_docx(p, "BR"))
    assert "Check the design of Study 1 (Pain at 12 weeks): Extraction records this study's design as “Cluster randomised trial”" in t
    assert "In 1 assessment, the study design (and so the tool) does not fit the design recorded in data extraction" in t
    # the matching design: no warning
    review.define_assessment(p, "BR", k, "cluster_rct", "Pain at 12 weeks (cluster)")
    assert review.rob_view(p, k, "BR")[1]["warning"] == ""
    # an assessment from before the plan (RoB 2, no design) of the same study is flagged by its tool
    review._write(p, "BR", "rob", {"s": k, "a": "aOLD02", "def": {"tool": "rob2", "label": "Disability", "deleted": False}})
    old = next(x for x in review.rob_view(p, k, "BR") if x["a"] == "aOLD02")
    assert "this assessment uses RoB 2" in old["warning"] and "D1b" in old["warning"]


def test_an_uncertain_extracted_design_is_neither_suggested_nor_flagged(tmp_path):
    p = make(tmp_path, plan=rob_plan("nrsi", "prediction"))
    k = keys(p)["Study 1"]
    extract_design(p, k, "Cohort study")                       # could be either design in the plan
    assert robplan.suggested(p, k, "BR")["designs"] == ["nrsi", "prediction"]
    review.define_assessment(p, "BR", k, "prediction", "Accuracy of the model")
    assert review.rob_view(p, k, "BR")[0]["warning"] == ""


def test_the_suggested_design_keeps_extraction_blind(tmp_path):
    """The second extractor sees the first extractor's design only once the first has finished the study."""
    p = make(tmp_path, plan=rob_plan("rct", "cluster_rct"))
    k = keys(p)["Study 1"]
    review.save_value(p, "BR", k, "design", "Cluster randomised trial")
    assert robplan.suggested(p, k, "AL")["text"] == ""          # AL is the second extractor: BR has not finished
    assert robplan.suggested(p, k, "BR")["text"] == "Cluster randomised trial"
    review.set_done(p, "BR", k, True)
    assert robplan.suggested(p, k, "AL")["designs"] == ["cluster_rct"]


def test_an_included_study_with_no_assessment_is_named_when_its_tool_is_known(tmp_path):
    p = make(tmp_path, plan=rob_plan("rct", extra=[{"name": "Case series", "none": True, "reason": "Harms only"}]))
    k = keys(p)
    extract_design(p, k["Study 1"], "Randomised trial")
    rob = next(x for x in readiness.check(p, "BR")["steps"] if x["id"] == "rob")
    known = next(x for x in rob["warnings"] if x["key"] == "rob_none_known")
    assert "tool. Study 1: randomised trials (individually randomised, parallel-group), with RoB 2. Add one" in known["text"]
    assert "2 included studies have no risk-of-bias assessment yet" in next(x for x in rob["warnings"] if x["key"] == "rob_none")["text"]


# ================================================================================================ a tool the lead defines
def test_a_lead_defined_tool_is_judged_blinded_agreed_and_reported_like_a_built_in_one(tmp_path):
    plan = rob_plan("rct", extra=[{"name": "Qualitative studies", "tool": "x_ab12cd34"}], tools={"x_ab12cd34": QUAL})
    p = make(tmp_path, plan=plan)
    k = keys(p)["Study 2"]
    did = next(d["id"] for d in robplan.plan(p)["designs"] if d["name"] == "Qualitative studies")
    aid = review.define_assessment(p, "BR", k, did, "Experience of exercise")["a"]
    t = review.tool_def(p, "x_ab12cd34")
    assert [c for c, _ in t["domains"]] == ["I1", "I2", "I3", "O"] and t["scale"] == ["Yes", "No", "Unclear", "Not applicable"]
    with pytest.raises(ProjectError, match="Choose one of the judgements"):
        review.judge(p, "BR", k, aid, "I1", "Partly")
    picks = {"I1": "Yes", "I2": "No", "I3": "Not applicable", "O": "Yes"}
    for c, j in picks.items():
        review.judge(p, "BR", k, aid, c, j, "BR's notes, p. 5")
    with pytest.raises(ProjectError, match=r"overall judgement \(Yes\) is less severe than I2 Participants' voices represented: No"):
        review.rob_done(p, "BR", k, aid, True)                                 # the lead's rule
    review.judge(p, "BR", k, aid, "O", "No", "BR's notes, p. 5")
    review.rob_done(p, "BR", k, aid, True)
    seen = review.rob_view(p, k, "AL")[0]
    assert all(d["mine"] is None and "a" not in d for d in seen["domains"])    # blinded until both finish
    judge_all(p, "AL", k, aid, lambda t_, c: {"I2": "Unclear", "O": "Unclear"}.get(c, "Yes"))
    v = review.rob_view(p, k, "AL")[0]
    i2 = next(d for d in v["domains"] if d["d"] == "I2")
    assert v["both_done"] and (i2["a"], i2["b"], i2["severer"]) == ("No", "Unclear", "No")
    for c in ("I2", "I3", "O"):
        review.rob_agree(p, "AL", k, aid, c, {"I2": "No", "I3": "Not applicable", "O": "No"}[c])
    assert all(d["final"] for d in review.rob_view(p, k, "AL")[0]["domains"])
    # every output, in the tool's own words, with its citation as the lead gave it
    sheets = check_xlsx(review.rob_xlsx(p))
    assert sheets["Qualitative appraisal checklist"][0][3:] == ["I1: Methods fit the question", "I2: Participants' voices represented",
                                                                "I3: Ethics approval reported", "O: Overall judgement"]
    assert sheets["Qualitative appraisal checklist"][1][3:] == ["Yes", "No", "Not applicable", "No"]
    svg = review.rob_svg(p).encode()
    check_svg(svg)
    assert "Key: Y Yes; ? Unclear; N No; NA Not applicable" in svg_text(svg)
    r = text(reporting.rob_docx(p, "BR"))
    assert "Qualitative appraisal checklist, version 2020 edition: Lockwood C, et al. Int J Evid Based Healthc 2015;13:179-87" in r
    m = text(reporting.methods_docx(p, "BR"))
    assert ("Qualitative studies: Qualitative appraisal checklist, version 2020 edition (Lockwood C, et al. Int J Evid Based "
            "Healthc 2015;13:179-87), a tool defined in ReviewDesk by the labels of its items only.") in m
    # it says what ReviewDesk recorded (not a discussion it cannot see), and reads as the other sections do
    sec = m[m.index("Risk of bias in included studies"):m.index("Certainty of the evidence")]
    assert "after discussion" not in sec and "the project lead (Ann Lead) recorded the agreed judgement." in sec
    assert "[Authors to complete: how differences between the two reviewers' judgements were resolved" in sec
    for line in sec.splitlines()[1:]:
        assert not re.match(r"\d", line) and ") (" not in line and "((" not in line and "))" not in line, line
    # once used, its items, scale and overall judgement are fixed; labels can still be corrected; it cannot be removed
    changed = {**QUAL, "items": [{"id": "I1", "label": "Methods fit the question"}, {"id": "I2", "label": "Voices represented"},
                                 {"id": "I3", "label": "Ethics approval reported"}], "next": 3}
    robplan.save_plan(p, "AL", {**plan, "tools": {"x_ab12cd34": changed}}, "Shorter label for item 2, as in our protocol")
    assert dict(review.tool_def(p, "x_ab12cd34")["domains"])["I2"] == "Voices represented"
    with pytest.raises(ProjectError, match="its items, answer scale and overall judgement cannot change"):
        robplan.save_plan(p, "AL", {**plan, "tools": {"x_ab12cd34": {**changed, "scale": {"preset": "ypn"}}}}, "Trying another scale")
    with pytest.raises(ProjectError, match="cannot be removed"):
        robplan.save_plan(p, "AL", rob_plan("rct"), "Dropping the qualitative studies")


def test_a_lead_defined_scale_can_be_unordered_or_ordered_with_its_own_overall_scale():
    own = robtools.check_custom({"name": "Star rating", "version": "1", "citation": "Wells GA, et al.", "items": ["Selection", "Comparability"],
                                 "scale": {"preset": "own", "answers": ["Star", "No star"], "ordered": False},
                                 "overall": {"use": True, "same": True, "rule": True}})
    assert own["overall"]["rule"] is False                     # no order: no rule, and every circle the same colour
    t = robtools.custom_runtime("x_1", own)
    assert robtools.sev(t, "Star") is None and review.colour(None) == review.NEUTRAL
    assert robtools.overall_problems(t, {"I1": "No star", "O": "Star"}) == []
    graded = robtools.check_custom({"name": "Confidence", "version": "2017", "citation": "Shea BJ, et al. BMJ 2017;358:j4008",
                                    "items": ["Item 2", "Item 4"], "scale": {"preset": "ypn"},
                                    "overall": {"use": True, "same": False, "preset": "own", "ordered": True,
                                                "answers": ["High", "Moderate", "Low", "Critically low"]}})
    g = robtools.custom_runtime("x_2", graded)
    assert robtools.scale_for(g, "O") == ["High", "Moderate", "Low", "Critically low"] and g["overall"] == {}
    with pytest.raises(ValueError, match="give at least two answers"):
        robtools.check_custom({**QUAL, "scale": {"preset": "own", "answers": ["Yes"]}})
    with pytest.raises(ValueError, match="should start with https://"):
        robtools.check_custom({**QUAL, "link": "javascript:alert(1)"})


def test_the_plan_route_is_for_everyone_to_read_and_the_lead_to_write(desk, tmp_path):
    c = desk.client()
    c.create_project(str(tmp_path / "Shared"))
    c.me = "CH"                                       # the person who made the project leads it
    c.settings(reviewers=[{"id": "CH", "name": "Colin Hawthorne", "role": "lead"}, {"id": "BR", "name": "Ben Reviewer", "role": "reviewer"}])
    got = c.get("/api/rob/plan")
    assert not got["recorded"] and got["is_lead"] and [d["id"] for d in got["standard"]] == robtools.STANDARD_IDS
    assert set(got["tools"]) == set(robtools.TOOLS) and got["presets"]["ynu"]["answers"][0] == "Yes"
    assert all(t["link"][1].startswith("https://") for t in got["tools"].values())
    saved = c.post("/api/rob/plan", {"plan": rob_plan("rct", "dta")})
    assert saved["recorded"] and saved["changed"] and not saved["amendment"]
    c.be("BR")
    assert not c.get("/api/rob/plan")["is_lead"]
    assert c.error("POST", "/api/rob/plan", {"plan": rob_plan("rct")}).status == 403
    assert c.error("POST", "/api/settings", {"changes": {"risk_of_bias_plan": {}}}).status in (400, 403)


def test_a_study_nobody_named_is_labelled_with_its_first_authors_surname_and_year(tmp_path):
    p = Project.create(str(tmp_path / "labels"), "Exercise for chronic low back pain", "Ann Lead", "AL")
    p.update_settings("AL", SETTINGS)
    robplan.save_plan(p, "AL", rob_plan("rct"))
    recs = [{"title": "Exercise for back pain: a randomised trial", "authors": ["Becker Jw.", "Smith A"], "year": "2026"},
            {"title": "Walking for back pain: a randomised trial", "authors": ["Novak, Jana", "Ito, K"], "year": "2025"}]
    p.add_import("AL", "medline.ris", ris(*recs), {"set": "main", "database": "MEDLINE"})
    p.process_imports("AL")
    screening.start(p, "AL", "main")
    for who in ("BR", "AL"):
        for r in p.records():
            p.add_decision(who, r["id"], "ta", "include")
            p.add_decision(who, r["id"], "ft", "include")                  # no study name given
    assert sorted(s["label"] for s in review.studies(p)) == ["Becker 2026", "Novak 2025"]


def test_adding_an_assessment_in_the_browser_starts_from_the_extracted_design(desk, tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    from helpers import reference_plan, reference_project, screen_all
    c = desk.client()
    reference_project(c, str(tmp_path / "OneDrive"), screening=reference_plan(mode="dual", calibration_size=0, ai_second_screener=False),
                      plan=rob_plan("rct", "cluster_rct"))
    c.add_import(ris(*tiny_records(2, seed=3)), "medline.ris", "MEDLINE")
    c.process()
    c.post("/api/screening/start", {"set": "main"})
    for who in ("NH", "CH"):
        screen_all(c, who, lambda r: "include", "screen")
    for who in ("NH", "CH"):
        screen_all(c, who, lambda r: ("include", "", "Becker 2026"), "fulltext")
    c.be("CH")
    c.post("/api/extract/form", {"template": "generic", "first": "NH", "second": "CH"})
    key = c.get("/api/extract")["studies"][0]["key"]
    c.be("NH")
    c.post("/api/extract/value", {"key": key, "f": "design", "v": "Cluster randomised trial"})
    c.post("/api/extract/done", {"key": key, "done": True})
    errors = []
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                      # noqa: BLE001 - no browser on this computer
            pytest.skip(f"Chromium is not available: {e}")
        pg = b.new_page(viewport={"width": 1280, "height": 900})
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(desk.url)
        base = desk.url.split("/?")[0]
        pg.goto(f"{base}/#/x/{key}/rob")
        pg.wait_for_selector("#rob-design")
        assert pg.input_value("#rob-design") == "cluster_rct"                   # chosen from what extraction recorded
        assert "RoB 2 for cluster-randomised trials" in pg.inner_text(".rob-about")
        pg.select_option("#rob-design", "rct")
        assert "Check the design." in pg.inner_text(".rob-about")              # a warning, not a block
        pg.select_option("#rob-design", "cluster_rct")
        pg.fill("#rob-label", "Pain at 12 weeks")
        pg.click("text=Add the assessment")
        pg.wait_for_selector(".panel.rob")
        head = pg.inner_text(".panel.rob")
        assert "Design: Cluster-randomised trials. Tool: RoB 2 for cluster-randomised trials." in head
        assert pg.locator("label[for$='-D1b']").inner_text().startswith("D1b Timing of identification")
        assert errors == [], errors
        b.close()
    (a,) = c.get("/api/extract/study", key=key)["rob"]
    assert (a["design"], a["tool"]) == ("cluster_rct", "rob2_cluster")


# ================================================================================================ the second re-check
def free_text_design(p):
    """The extraction form with the study design typed in, as many leads' forms have it."""
    import copy
    f = copy.deepcopy(review.GENERIC)
    for sec in f["sections"]:
        for fl in sec["fields"]:
            if fl["id"] == "design":
                fl.update(type="text", options=[])
    review.save_form(p, "AL", {**f, "first": "BR", "second": "AL"})


def test_a_cluster_trial_is_flagged_when_the_plan_has_no_design_for_cluster_trials(tmp_path):
    """N1: the plan has randomised trials only; a study extraction records as a cluster trial is assessed as
    individually randomised. The design is in the plan, but not the right one: the warning names the tool."""
    p = make(tmp_path, plan=rob_plan("rct", "nrsi"))
    free_text_design(p)
    k = keys(p)
    extract_design(p, k["Study 1"], "Stepped-wedge cluster randomised trial")
    sug = robplan.suggested(p, k["Study 1"], "BR")
    assert sug["designs"] == [] and "but the risk-of-bias plan has no design for them, so it would be assessed with RoB 2" \
        in sug["warnings"]["rct"] and "ask the project lead to add the design" in sug["warnings"]["rct"]
    aid = review.define_assessment(p, "BR", k["Study 1"], "rct", "Pain at 12 weeks")["a"]
    w = review.rob_view(p, k["Study 1"], "BR")[0]["warning"]
    assert w.startswith("Extraction records this study's design as “Stepped-wedge cluster randomised trial”, which fits "
                        "cluster-randomised trials, but the risk-of-bias plan has no design for them, so this assessment uses "
                        "the design “Randomised trials (individually randomised, parallel-group)”, with RoB 2.")
    assert "with RoB 2. A cluster-randomised trial is assessed with RoB 2 for cluster-randomised trials, which also " \
           "judges bias arising from the timing of identification or recruitment of participants (D1b). If extraction" in w
    assert "the project lead adds the design to the plan" in w
    rob = next(x for x in readiness.check(p, "AL")["steps"] if x["id"] == "rob")
    item = next(x for x in rob["warnings"] if x["key"] == "rob_design")              # for the lead to act on
    assert "Study 1 (Pain at 12 weeks)" in item["text"] and "does not fit the design extraction records" in item["text"]
    assert not any(x["key"] == "rob_design" for x in next(x for x in readiness.check(p, "BR")["steps"] if x["id"] == "rob")["warnings"])
    for who in ("BR", "AL"):
        judge_all(p, who, k["Study 1"], aid, lambda t, c: robtools.scale_for(t, c)[0])
    t = text(reporting.rob_docx(p, "AL"))
    assert "Not final" in t and "Check the design of Study 1 (Pain at 12 weeks)" in t
    # a crossover trial, a test accuracy study: each named with its usual tool
    extract_design(p, k["Study 2"], "Randomised crossover trial")
    review.define_assessment(p, "BR", k["Study 2"], "rct", "Pain at 2 weeks")
    assert "usually assessed with RoB 2 for crossover trials" in review.rob_view(p, k["Study 2"], "BR")[0]["warning"]
    extract_design(p, k["Study 3"], "Diagnostic test accuracy study")
    review.define_assessment(p, "BR", k["Study 3"], "nrsi", "Accuracy of the straight-leg raise")
    assert "usually assessed with QUADAS-2 or PROBAST+AI" in review.rob_view(p, k["Study 3"], "BR")[0]["warning"]


def test_a_broad_design_word_alone_is_not_flagged_against_a_plan_that_lacks_it(tmp_path):
    """N1's limits: 'cohort study' could be several designs, so a plan without it raises nothing; nor does an assessment
    made with the design the plan has under the lead's own name (which, since the final re-test, is chosen first)."""
    p = make(tmp_path, plan=rob_plan("rct", extra=[{"name": "Cohort studies", "tool": "x_ab12cd34"}],
                                     tools={"x_ab12cd34": {**QUAL, "name": "Cohort checklist"}}))
    k = keys(p)["Study 1"]
    extract_design(p, k, "Cohort study")
    did = robplan.plan(p)["designs"][1]["id"]
    review.define_assessment(p, "BR", k, did, "Pain at 1 year")
    assert review.rob_view(p, k, "BR")[0]["warning"] == ""
    # the lead's "Cohort studies" is the design "Cohort study" fits (singular or plural): it is chosen when an assessment
    # is added, and only the other design draws a warning
    sug = robplan.suggested(p, k, "BR")
    assert sug["designs"] == [did] and set(sug["warnings"]) == {"rct"} and "fits “Cohort studies”" in sug["warnings"]["rct"]
    # a plan without such a design: the broad word alone raises nothing
    assert robplan.compatible(rob_plan("rct"), "Cohort study") == []


def test_case_control_cross_sectional_and_cluster_crossover_studies_are_not_routed_to_tools_that_do_not_cover_them(tmp_path):
    """N7: ROBINS-I V2 and ROBINS-E are for follow-up studies; RoB 2 for cluster-randomised trials is for parallel-group
    cluster trials (stepped-wedge trials included, with a caveat in its help)."""
    std = robplan.STANDARD_PLAN
    for design in ("Case-control study", "Nested case-control study", "Cross-sectional study"):
        assert not {"nrsi", "nrse"} & set(robplan.compatible(std, design)), design
    assert robplan.compatible(std, "Cluster randomised crossover trial") == []
    assert robplan.compatible(std, "Cohort study")[:2] == ["nrsi", "nrse"]
    p = make(tmp_path, plan=rob_plan("rct", "cluster_rct", "nrse"))
    free_text_design(p)
    k = keys(p)
    extract_design(p, k["Study 1"], "Nested case-control study")
    review.define_assessment(p, "BR", k["Study 1"], "nrse", "Asthma by age 5")
    w = review.rob_view(p, k["Study 1"], "BR")[0]["warning"]
    assert "ROBINS-E is for follow-up (cohort-type) studies, and a case-control or cross-sectional study is not one" in w
    extract_design(p, k["Study 2"], "Cluster randomised crossover trial")
    review.define_assessment(p, "BR", k["Study 2"], "cluster_rct", "Infections")
    w = review.rob_view(p, k["Study 2"], "BR")[0]["warning"]
    assert "No built-in tool covers cluster crossover trials" in w and "Say in the review how the tool was used" in w
    assert "Stepped-wedge trials are included" in robtools.TOOLS["rob2_cluster"]["help"]
    assert "signalling question 2.6" in robtools.TOOLS["rob2_cluster"]["help"]
    assert "Cluster crossover trials are not covered" in robtools.TOOLS["rob2_cluster"]["help"]


def test_quadas_2_at_risk_with_an_unclear_domain_needs_no_reason_but_low_still_needs_every_domain_low():
    """N4: QUADAS-2 says a study judged high or unclear in any domain is 'at risk of bias' (and likewise for
    applicability): that is its rule, not a judgement more severe than the domains."""
    t = robtools.TOOLS["quadas2"]
    low = {"D1": "Low", "D2": "Low", "D3": "Low", "D4": "Low", "A1": "Low", "A2": "Low", "A3": "Low"}
    unclear = {**low, "D3": "Unclear", "A2": "Unclear", "O": "At risk of bias", "AO": "Concerns regarding applicability"}
    assert robtools.overall_problems(t, unclear, {}) == []                       # no reason asked for
    every_low = {**low, "O": "At risk of bias", "AO": "Low concern regarding applicability"}
    (why,) = robtools.overall_problems(t, every_low, {})
    assert why.startswith("The overall judgement (At risk of bias) is more severe than every domain")
    assert robtools.overall_problems(t, every_low, {"O": "Patients were selected by the index test's result"}) == []
    (bad,) = robtools.overall_problems(t, {**low, "D3": "Unclear", "O": "Low risk of bias"}, {})
    assert bad.startswith("The overall judgement (Low risk of bias) is less severe than D3 Reference standard: Unclear")
    # other tools keep asking: RoB 2 'High risk' with 'Some concerns' domains needs its reason
    r = robtools.TOOLS["rob2"]
    js = {"D1": "Some concerns", "D2": "Low risk", "D3": "Low risk", "D4": "Low risk", "D5": "Low risk", "O": "High risk"}
    assert robtools.overall_problems(r, js, {}) and not robtools.overall_problems(r, js, {"O": "Several concerns add up"})


def test_an_assessment_whose_tool_the_plan_no_longer_gives_is_flagged_and_reported(tmp_path):
    """N3: the plan's tool for a design is changed by amendment after assessments used the old one. They keep their
    tool (their judgements are on its scale), but they are flagged and the methods text says so."""
    plan = rob_plan("rct", "nrsi")
    p = make(tmp_path, plan=plan)
    k = keys(p)
    aid = review.define_assessment(p, "BR", k["Study 1"], "nrsi", "Pain at 12 weeks")["a"]
    review.define_assessment(p, "BR", k["Study 2"], "rct", "Pain at 12 weeks")
    for who in ("BR", "AL"):
        judge_all(p, who, k["Study 1"], aid, lambda t, c: robtools.scale_for(t, c)[0])
    assert review.rob_view(p, k["Study 1"], "AL")[0]["superseded"] == ""
    amended = {**plan, "designs": [plan["designs"][0], {**plan["designs"][1], "tool": "robins_ba"}]}
    robplan.save_plan(p, "AL", amended, "The protocol was amended to use the published version of ROBINS-I")
    (am,) = robplan.amendments(p)
    day = reporting.nice_date(am["date"])
    assert am["changes"] == ["changed the tool from ROBINS-I V2 to ROBINS-I 2016 for non-randomised studies of "
                             "interventions, with a comparison group"]
    v = review.rob_view(p, k["Study 1"], "AL")[0]
    assert v["tool"] == "robins2" and v["superseded"].startswith(
        "When this assessment was added, the plan gave ROBINS-I V2 for non-randomised studies of interventions, with a "
        f"comparison group; it now gives ROBINS-I 2016 (amended on {robplan._day(am['date'])}). Assess the result again")
    assert review.rob_view(p, k["Study 2"], "AL")[0]["superseded"] == ""
    rob = next(x for x in readiness.check(p, "AL")["steps"] if x["id"] == "rob")
    item = next(x for x in rob["warnings"] if x["key"] == "rob_tool")
    assert item["text"].startswith("1 risk-of-bias assessment was made with a tool the risk-of-bias plan no longer gives")
    assert "after they were added: Study 1 (Pain at 12 weeks). Decide what to do with each" in item["text"]
    t = text(reporting.rob_docx(p, "AL"))
    assert "Not final" in t and "the tool is one the risk-of-bias plan no longer gives for the study's design" in t
    assert "Check the tool of Study 1 (Pain at 12 weeks): When this assessment was added, the plan gave ROBINS-I V2" in t
    assert f"On {day}, changed from ROBINS-I V2" in t                              # the plan table's last column
    m = text(reporting.methods_docx(p, "AL"))
    assert "Risk of bias was assessed for 2 results in 2 studies, named in the risk-of-bias tables. By tool:" in m
    assert ("Of these, 1 result was assessed with a tool the plan gave before it was amended: ROBINS-I V2, for "
            "non-randomised studies of interventions, with a comparison group (1 result; the plan now gives ROBINS-I "
            "2016). The others were assessed with the tool the plan gives for the study's design.") in m
    assert "each with the tool the plan gives for the study's design" not in m


def test_the_methods_text_counts_the_results_assessed_and_claims_nothing_it_does_not_record(tmp_path):
    """N2: the results assessed are counted (the authors say which and why); nothing says the reviewers used the
    signalling questions, which ReviewDesk does not record."""
    p = make(tmp_path, plan=rob_plan("rct", "nrsi"))
    k = keys(p)
    review.define_assessment(p, "BR", k["Study 1"], "rct", "Pain at 12 weeks")
    review.define_assessment(p, "BR", k["Study 1"], "rct", "Disability at 12 weeks")
    review.define_assessment(p, "BR", k["Study 2"], "nrsi", "Pain at 12 weeks")
    m = text(reporting.methods_docx(p, "AL"))
    assert ("Risk of bias was assessed for 3 results in 2 studies, named in the risk-of-bias tables, each with the tool "
            "the plan gives for the study's design. By tool: RoB 2 (2 results in 1 study) and ROBINS-I V2 (1 result in 1 "
            "study).") in m
    assert "[Authors to complete: which results were assessed for risk of bias, and why those" in m
    assert "for each result that feeds the synthesis" not in m and "using each tool's signalling questions" not in m
    assert "whether the reviewers used each tool's signalling questions or guidance" in m


def test_assessments_from_before_the_plan_are_described_as_such(tmp_path):
    """N6: a project whose assessments all predate the plan does not say they used the plan's tools."""
    p = make(tmp_path, plan=rob_plan("rct"))
    k = keys(p)
    for n, s in enumerate(("Study 1", "Study 2")):
        review._write(p, "BR", "rob", {"s": k[s], "a": f"aOLD0{n}", "def": {"tool": "rob2", "label": "Pain", "deleted": False}})
    m = text(reporting.methods_docx(p, "AL"))
    assert ("Risk of bias was assessed for 2 results in 2 studies, named in the risk-of-bias tables, each with the tool "
            "chosen when it was added, before the risk-of-bias plan was recorded.") in m
    sec = m[m.index("Risk of bias in included studies"):m.index("Certainty of the evidence")]
    assert "the tool the plan gives" not in sec and "Of these" not in sec
    review.define_assessment(p, "BR", k["Study 3"], "rct", "Pain")              # one more, with the plan
    m = text(reporting.methods_docx(p, "AL"))
    assert ("Risk of bias was assessed for 3 results in 3 studies, named in the risk-of-bias tables. By tool: RoB 2 "
            "(3 results in 3 studies).") in m
    assert ("Of these, 2 results were assessed before the plan was recorded, with the tool chosen at the time; the others "
            "with the tool the plan gives for the study's design.") in m


def test_the_plan_as_it_started_then_each_amendment_then_the_plan_as_amended(tmp_path):
    """N5: the methods list is the plan when screening started; a design added or removed by amendment is shown as
    such in the methods text and in the plan table."""
    plan = rob_plan("rct", "cluster_rct")
    p = make(tmp_path, plan=plan)
    k = keys(p)
    review.define_assessment(p, "BR", k["Study 1"], "cluster_rct", "Pain at 12 weeks")
    new = rob_plan("rct", "prognostic_factor")
    robplan.save_plan(p, "AL", new, "The protocol was amended: prognostic factor studies replace the cluster trials")
    (am,) = robplan.amendments(p)
    assert am["changes"] == ["added prognostic factor studies, assessed with QUIPS", "removed cluster-randomised trials"]
    day = reporting.nice_date(am["date"])
    m = text(reporting.methods_docx(p, "AL"))
    sec = m[m.index("Risk of bias in included studies"):m.index("Certainty of the evidence")]
    first = sec[sec.index("set the tool for each study design:"):sec.index("The plan was amended")]
    assert "Cluster-randomised trials: RoB 2 for cluster-randomised trials" in first and "Prognostic" not in first
    assert (f"On {day}, Ann Lead added prognostic factor studies, assessed with QUIPS; removed cluster-randomised trials "
            "(reason: the protocol was amended") in sec
    after = sec[sec.index("As amended, the plan gives:"):]
    assert "Prognostic factor studies: QUIPS" in after and "Cluster-randomised" not in after.split("Risk of bias was")[0]
    assert ("Risk of bias was assessed for 1 result in 1 study, named in the risk-of-bias tables. By tool: RoB 2 for "
            "cluster-randomised trials (1 result in 1 study).\nIt was assessed with a tool the plan gave before it was "
            "amended: RoB 2 for cluster-randomised trials (1 result; the plan no longer has this design).") in sec
    assert review.rob_view(p, k["Study 1"], "AL")[0]["superseded"].startswith(
        "When this assessment was added, the plan gave RoB 2 for cluster-randomised trials; it no longer has that design")
    t = text(reporting.rob_docx(p, "AL"))
    assert f"Removed on {day}" in t and f"Added on {day}" in t
    assert "Changed since by 1 protocol amendment, dated in the last column" in t
    sheet = check_xlsx(review.rob_xlsx(p))["Plan and tools"]                       # and the workbook
    gone = next(r for r in sheet if r[0] == "Cluster-randomised trials")
    assert gone[1] == "RoB 2 for cluster-randomised trials" and gone[3] == f"Removed on {day}"
    assert any(r[0] == "Prognostic factor studies" and r[3] == f"Added on {day}" for r in sheet)


def test_the_tool_names_say_which_rob_version_and_need_no_nested_brackets(tmp_path):
    """N8 and N9: ROBINS-I V2's domains are those of one draft, and the outputs say which; tool names, headings and
    keys have no doubled or nested brackets, no unused 'Not applicable', and D1b is named as the cribsheet names it."""
    t = robtools.TOOLS["robins2"]
    assert "draft of 20 November 2025" in t["help"] and "22 November 2024 had seven" in t["domains_from"]
    p = make(tmp_path, plan=rob_plan("rct", "cluster_rct", "nrsi", "before_after"))
    k = keys(p)
    for s, d in (("Study 1", "rct"), ("Study 2", "nrsi"), ("Study 3", "before_after")):
        aid = review.define_assessment(p, "BR", k[s], d, "Pain")["a"]
        for who in ("BR", "AL"):
            judge_all(p, who, k[s], aid, lambda t_, c: robtools.scale_for(t_, c)[0])
    m = text(reporting.methods_docx(p, "AL"))
    assert re.search(r"Non-randomised studies of interventions, with a comparison group: ROBINS-I V2 \([^()]*; ReviewDesk "
                     r"records the six domains of the draft of 20 November 2025, which has no domain for deviations from "
                     r"intended interventions; the launch version of 22 November 2024 had seven\)\.", m)
    assert "[Authors to complete: the version of RoB 2 used" in m and "the version of ROBINS-I V2 used" in m
    sheets = check_xlsx(review.rob_xlsx(p))
    plan_sheet = sheets["Plan and tools"]
    assert any("ReviewDesk records the six domains of the draft of 20 November 2025" in r[0] for r in plan_sheet)
    rob2 = next(r for r in plan_sheet if r[0].startswith("RoB 2 (individually"))
    assert "Not applicable" not in rob2[2] and "(NA)" not in rob2[2]
    svg = svg_text(review.rob_svg(p))
    r = text(reporting.rob_docx(p, "AL"))
    for out in (m, r, svg, " ".join(" ".join(row) for row in plan_sheet)):
        assert ") (" not in out and "((" not in out and "))" not in out
        assert "ROBINS-I (2016)" not in out and "(cluster-randomised trials)" not in out
    assert "Not applicable" not in svg and "NA not applicable" not in svg
    assert dict(robtools.TOOLS["rob2_cluster"]["domains"])["D1b"] == \
        "Timing of identification or recruitment of participants"


def test_a_plan_changed_outside_reviewdesk_is_found_and_put_back_or_recorded_as_an_amendment(tmp_path):
    """The data checks: the plan in the project file is compared with the plan last recorded in ReviewDesk (its
    fingerprint is kept in the audit log). The lead puts the file back, or records the change as correct with a reason,
    which makes it a protocol amendment the methods text reports."""
    p = make(tmp_path, plan=rob_plan("rct", "nrsi"))
    assert not [x for x in p.integrity_report()["problems"] if x["kind"] == "robplan"]

    def edit(f):
        s = util.read_json(p.path(MARKER), {})
        f(s["risk_of_bias_plan"])
        util.write_json_atomic(p.path(MARKER), s)
        p._memo.clear()

    edit(lambda pl: pl["designs"][1].update(tool="robins_e"))                    # by hand, in a text editor
    (pr,) = [x for x in p.integrity_report()["problems"] if x["kind"] == "robplan"]
    assert pr["text"].startswith("The risk-of-bias plan was changed outside ReviewDesk: it does not match the plan last "
                                 "recorded in ReviewDesk")
    assert pr["fixes"] == ["backup", "accept"] and pr["file"] == MARKER
    # put back: the copy ReviewDesk kept
    p.restore_backup("AL", MARKER)
    p._memo.clear()
    assert robplan.plan(p)["designs"][1]["tool"] == "robins2"
    assert not [x for x in p.integrity_report()["problems"] if x["kind"] == "robplan"]
    # or recorded as correct: a protocol amendment, with the reason, in the methods text
    edit(lambda pl: pl["designs"][1].update(tool="robins_e"))
    (pr,) = [x for x in p.integrity_report()["problems"] if x["kind"] == "robplan"]
    with pytest.raises(PermissionDenied):
        p.accept_problem("BR", pr["sig"], "It was corrected to match the protocol")
    p.accept_problem("AL", pr["sig"], "The protocol names ROBINS-E for these studies; the plan was corrected by hand")
    assert not [x for x in p.integrity_report()["problems"] if x["kind"] == "robplan"]
    (am,) = robplan.amendments(p)
    assert am["outside_reviewdesk"] and am["changes"] == [
        "recorded as correct a change made to the plan outside ReviewDesk, which changed the tool from ROBINS-I V2 to "
        "ROBINS-E for non-randomised studies of interventions, with a comparison group"]
    assert any(e["action"] == "record a change made to the risk-of-bias plan outside ReviewDesk" for e in p.audit_entries())
    m = text(reporting.methods_docx(p, "AL"))
    assert (f"On {reporting.nice_date(am['date'])}, Ann Lead recorded as correct a change made to the plan outside "
            "ReviewDesk, which changed the tool from ROBINS-I V2 to ROBINS-E for non-randomised studies of interventions, "
            "with a comparison group (reason: the protocol names ROBINS-E") in m
    t = text(reporting.rob_docx(p, "AL"))
    assert "changed from ROBINS-I V2" in t
    # a change made in ReviewDesk afterwards is not reported; nor is the plan's removal missed
    robplan.save_plan(p, "AL", rob_plan("rct", "nrse"), "The non-randomised studies all concern exposures")
    assert not [x for x in p.integrity_report()["problems"] if x["kind"] == "robplan"]
    s = util.read_json(p.path(MARKER), {})
    s.pop("risk_of_bias_plan")
    util.write_json_atomic(p.path(MARKER), s)
    p._memo.clear()
    (pr,) = [x for x in p.integrity_report()["problems"] if x["kind"] == "robplan"]
    assert pr["text"].startswith("The risk-of-bias plan was removed from the project file outside ReviewDesk")
    assert pr["fixes"] == ["backup"] and "record the plan again" in pr["text"]       # a removal cannot be "correct"
    robplan.save_plan(p, "AL", rob_plan("rct", "nrse"), "Recorded again after it was removed outside ReviewDesk")
    assert not [x for x in p.integrity_report()["problems"] if x["kind"] == "robplan"]


def test_a_plan_recorded_before_fingerprints_is_compared_in_words_under_its_tools_former_names(tmp_path):
    """A plan recorded before fingerprints were kept has none in its audit entry, only its lines in words, which name
    ROBINS-I 2016 as it was then named: that is not a change made outside ReviewDesk."""
    p = make(tmp_path, plan=rob_plan("rct", "before_after"))
    p.audit("AL", "change the risk-of-bias plan", {"plan": [
        "Randomised trials (individually randomised, parallel-group): RoB 2 (individually randomised, parallel-group trials)",
        "Uncontrolled before-after studies: " + robtools.TOOLS["robins_ba"]["former_names"][0]]})
    assert robplan.plan(p)["designs"][1]["name"] == "Uncontrolled before-after studies"
    assert not [x for x in p.integrity_report()["problems"] if x["kind"] == "robplan"]
    s = util.read_json(p.path(MARKER), {})
    s["risk_of_bias_plan"]["designs"][1]["tool"] = "robins2"
    util.write_json_atomic(p.path(MARKER), s)
    p._memo.clear()
    assert [x for x in p.integrity_report()["problems"] if x["kind"] == "robplan"]


def test_an_amendment_recorded_before_designs_were_kept_is_read_back_from_its_words(tmp_path):
    """An earlier ReviewDesk kept an amendment's plan before and after only in words, with design names capitalised mid-sentence and
    ROBINS-I 2016 under its former name: the designs are read back (so the plan table dates the change) and the
    words are brought up to date."""
    p = make(tmp_path, plan=rob_plan("rct", "before_after"))
    s = util.read_json(p.path(MARKER), {})
    s["risk_of_bias_plan"]["designs"][1]["tool"] = "robins2"
    old = robtools.TOOLS["robins_ba"]["former_names"][0]
    s["amendments"] = [{"date": "2026-11-04T10:00:00Z", "by": "AL", "what": "risk_of_bias_plan", "reason": "Protocol amended",
                        "changes": ["changed the tool for Uncontrolled before-after studies from ROBINS-I (2016) to ROBINS-I V2"],
                        "before": ["Randomised trials (individually randomised, parallel-group): RoB 2 (individually "
                                   "randomised, parallel-group trials)", "Uncontrolled before-after studies: " + old],
                        "after": ["Randomised trials (individually randomised, parallel-group): RoB 2 (individually "
                                  "randomised, parallel-group trials)", "Uncontrolled before-after studies: ROBINS-I V2 "
                                  "(non-randomised studies of interventions)"]}]
    util.write_json_atomic(p.path(MARKER), s)
    p._memo.clear()
    (am,) = robplan.amendments(p)
    assert am["changes"] == ["changed the tool from ROBINS-I 2016 to ROBINS-I V2 for uncontrolled before-after studies"]
    assert [d["tool"] for d in am["designs_before"]] == ["rob2", "robins_ba"]
    m = text(reporting.methods_docx(p, "AL"))
    assert "Uncontrolled before-after studies: ROBINS-I 2016 (Sterne JA, et al. BMJ 2016" in m      # the plan as it started
    assert "ROBINS-I (2016)" not in m and "On 4 November 2026, Ann Lead changed the tool from ROBINS-I 2016 to ROBINS-I V2" in m
    assert "On 4 November 2026, changed from ROBINS-I 2016" in text(reporting.rob_docx(p, "AL"))
    assert "(changed the tool from ROBINS-I 2016 to ROBINS-I V2 for uncontrolled before-after studies)" in \
        exports.methods_summary(p, True)
