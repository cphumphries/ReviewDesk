"""Data extraction and risk of bias, as the reference review planned them: study characteristics extracted by NH and
checked by CH, outcome data extracted by both independently and compared; risk of bias with every built-in tool
(RoB 2 and its cluster and crossover versions, ROBINS-I V2, ROBINS-I (2016), ROBINS-E, QUADAS-2, PROBAST+AI and QUIPS),
each study's tool following from its design in the risk-of-bias plan; nothing of one person's shown to the other
before both have finished; the lead records the agreed values; and the workbooks open."""
from __future__ import annotations

import json

import pytest

from helpers import check_svg, check_xlsx, reference_plan, reference_project, ris, rob_plan, screen_all, svg_text, tiny_records
from reviewdesk import review, robtools

NH_SECRET = "NH-outcome-value-4417"


@pytest.fixture
def inc(desk, tmp_path):
    """Four included studies (the first has two reports), extracted with the general form: NH first, CH checking."""
    c = desk.client()
    c.folder = reference_project(c, str(tmp_path / "OneDrive"),
                                 screening=reference_plan(mode="dual", calibration_size=0, ai_second_screener=False),
                                 plan=rob_plan(*robtools.STANDARD_IDS))
    recs = tiny_records(6, seed=41)
    c.add_import(ris(*recs), "medline.ris", "MEDLINE")
    c.process()
    c.post("/api/screening/start", {"set": "main"})
    for who in ("NH", "CH"):
        screen_all(c, who, lambda r: "include", "screen")
    ids = sorted(x["id"] for x in c.queue("fulltext", "all"))
    names = {ids[0]: "Ngata 2024", ids[1]: "Ngata 2024", ids[2]: "Fraser 2025", ids[3]: "Kim 2023", ids[4]: "Patel 2022"}
    for who in ("NH", "CH"):
        screen_all(c, who, lambda r: ("include", "", names[r]) if r in names else ("exclude", "Wrong outcomes"), "fulltext")
    c.be("CH")
    c.ids = ids
    c.post("/api/extract/form", {"template": "generic", "first": "NH", "second": "CH"})
    c.keys = [s["key"] for s in c.get("/api/extract")["studies"]]
    return c


def study(c, key):
    return c.get("/api/extract/study", key=key)


def save(c, key, f, v, pg=""):
    return c.post("/api/extract/value", {"key": key, "f": f, "v": v, "pg": pg})


OUTCOMES = [{"outcome": "Time to triage", "measure": "median minutes", "group": "AI", "n": "812", "result": "4", "effect": "", "where": "Table 2"},
            {"outcome": "Under-triage", "measure": "%", "group": "AI", "n": "812", "result": "5.1", "effect": "RR 0.61 (0.45 to 0.83)", "where": "Table 3"}]


def test_the_included_studies_are_the_reports_given_one_study_name(inc):
    c = inc
    x = c.get("/api/extract")
    st = {s["label"]: s for s in x["studies"]}
    assert sorted(st) == ["Fraser 2025", "Kim 2023", "Ngata 2024", "Patel 2022"]
    assert st["Ngata 2024"]["reports"] == c.ids[:2] and st["Ngata 2024"]["key"] == c.ids[0]
    assert x["people"] == {"first": "NH", "second": "CH", "set": True, "gone": []}
    assert x["templates"] == {"generic": "General systematic review"}
    assert x["form"]["rev"] == 1


def test_the_form_needs_two_different_people_from_the_project(inc):
    c = inc
    for body, words in (({"template": "generic", "first": "NH", "second": "NH"}, "The first and second extractors must be different people."),
                        ({"template": "generic", "first": "", "second": "CH"}, "Choose the first extractor."),
                        ({"template": "generic", "first": "NH", "second": "ZZ"}, "ZZ is not in the project."),
                        ({"template": "nope"}, "Unknown template."),
                        ({"form": {"sections": []}, "first": "NH", "second": "CH"}, "The form needs at least one section")):
        assert words in c.error("POST", "/api/extract/form", body).message
    c.be("NH")
    assert c.error("POST", "/api/extract/form", {"template": "generic", "first": "NH", "second": "CH"}).status == 403


def test_answers_are_checked_against_the_kind_of_answer(inc):
    c = inc
    c.be("NH")
    k = c.keys[0]
    assert "give a number" in c.error("POST", "/api/extract/value", {"key": k, "f": "n", "v": "about 800"}).message
    assert "choose one of the options" in c.error("POST", "/api/extract/value", {"key": k, "f": "design", "v": "RCT"}).message
    assert "A table answer must be a list of rows." in c.error("POST", "/api/extract/value", {"key": k, "f": "outcomes", "v": "x"}).message
    assert "no longer on the form" in c.error("POST", "/api/extract/value", {"key": k, "f": "colour", "v": "x"}).message
    save(c, k, "n", "812")
    save(c, k, "design", "Cluster randomised trial")


def test_only_the_two_extractors_record_extraction(inc):
    c = inc
    c.settings(reviewers=[{"id": "CH", "name": "Dr Colin Hawthorne", "role": "lead"}, {"id": "NH", "name": "Nadine Hayes", "role": "reviewer"},
                          {"id": "SP", "name": "Sam Patel", "role": "reviewer"}])
    c.be("SP")
    e = c.error("POST", "/api/extract/value", {"key": c.keys[0], "f": "country", "v": "New Zealand"})
    assert e.message == "Only the two extractors named on the form record extraction."


def test_the_first_extractor_extracts_and_the_second_checks(inc):
    c = inc
    k = c.keys[0]
    c.be("CH")
    e = c.error("POST", "/api/extract/value", {"key": k, "f": "country", "v": "New Zealand"})
    assert e.message == "Checked items are checked once the first extractor has finished this study."
    save(c, k, "outcomes", OUTCOMES)                          # the outcome data: both extract, independently
    c.be("NH")
    for f, v in (("author_year", "Ngata 2024"), ("country", "Aotearoa New Zealand"), ("design", "Cluster randomised trial"),
                 ("n", "812"), ("setting", "Two emergency departments"), ("outcomes", [{**OUTCOMES[0], "result": NH_SECRET}])):
        save(c, k, f, v, pg="p. 3")
    c.post("/api/extract/done", {"key": k, "done": True})
    assert "You have marked this study as finished" in c.error("POST", "/api/extract/value", {"key": k, "f": "n", "v": "813"}).message
    c.be("CH")
    s = study(c, k)
    st = s["state"]
    assert st["role"] == "second" and st["done_first"] and not st["both_done"] and st["comparison"] == []
    assert st["other"]["country"]["v"] == "Aotearoa New Zealand"          # CH checks NH's characteristics
    assert "outcomes" not in st["other"]                                  # but not NH's outcome data
    for path in ("/api/extract", f"/api/extract/study?key={k}"):
        st_, _, body = c.request("GET", path)
        assert NH_SECRET.encode() not in body, path
    save(c, k, "n", "821")                                    # a correction to a checked item
    seen = st["seen"]
    # NH reopens and changes an answer after CH opened the study: CH must look again before finishing
    c.be("NH")
    c.post("/api/extract/done", {"key": k, "done": False})
    save(c, k, "setting", "Two urban emergency departments")
    c.post("/api/extract/done", {"key": k, "done": True})
    c.be("CH")
    e = c.error("POST", "/api/extract/done", {"key": k, "done": True, "seen": seen})
    assert "The first extractor has changed some answers since you opened this study" in e.message
    out = c.post("/api/extract/done", {"key": k, "done": True, "seen": study(c, k)["state"]["seen"]})
    cmp_ = {x["f"]: x for x in out["state"]["comparison"]}
    assert cmp_["country"]["status"] == "checked" and cmp_["country"]["final"] == "Aotearoa New Zealand"
    assert cmp_["n"]["status"] == "differs" and (cmp_["n"]["a"], cmp_["n"]["b"]) == ("812", "821") and cmp_["n"]["final"] is None
    assert cmp_["outcomes"]["status"] == "differs" and cmp_["funding"]["status"] == "empty"
    assert c.get("/api/extract")["studies"][0]["to_agree"] == 2
    # once both have seen each other's answers, neither can reopen: the lead records the agreed value
    for who in ("NH", "CH"):
        c.be(who)
        assert "cannot be reopened" in c.error("POST", "/api/extract/done", {"key": k, "done": False}).message
    c.be("NH")
    assert c.error("POST", "/api/extract/agree", {"key": k, "f": "n", "v": "812"}).status == 403
    c.be("CH")
    c.post("/api/extract/agree", {"key": k, "f": "n", "v": "812"})
    out = c.post("/api/extract/agree", {"key": k, "f": "outcomes", "v": OUTCOMES})
    cmp_ = {x["f"]: x for x in out["state"]["comparison"]}
    assert cmp_["n"]["agreed"] and cmp_["n"]["final"] == "812" and cmp_["outcomes"]["final"][1]["effect"] == "RR 0.61 (0.45 to 0.83)"
    assert c.get("/api/extract")["studies"][0]["to_agree"] == 0
    assert c.overview()["extraction"] == {"studies": 4, "finished": 1}


def test_outcome_data_from_the_two_extractors_are_hidden_from_each_other_until_both_finish(inc):
    c = inc
    k = c.keys[1]
    c.be("CH")
    save(c, k, "outcomes", [{**OUTCOMES[0], "result": "CH-value-0099"}])
    c.be("NH")
    save(c, k, "outcomes", [{**OUTCOMES[0], "result": NH_SECRET}])
    for path in ("/api/extract", f"/api/extract/study?key={k}"):
        assert b"CH-value-0099" not in c.request("GET", path)[2]
    st, _, body = c.download("extraction")
    assert b"CH-value-0099" not in body and NH_SECRET.encode() not in body
    c.post("/api/extract/done", {"key": k, "done": True})
    c.be("CH")
    assert NH_SECRET.encode() not in c.request("GET", f"/api/extract/study?key={k}")[2]
    assert "Checked items are checked once" not in json.dumps(c.post("/api/extract/done", {"key": k, "done": True,
                                                                                          "seen": study(c, k)["state"]["seen"]}))
    c.be("NH")
    s = study(c, k)["state"]
    assert s["both_done"] and s["other"]["outcomes"]["v"][0]["result"] == "CH-value-0099"


def test_answers_stay_with_their_study_when_its_first_report_is_excluded(inc):
    c = inc
    k = c.keys[0]                                     # Ngata 2024: reports ids[0] and ids[1]
    c.be("NH")
    save(c, k, "country", "Aotearoa New Zealand")
    c.be("CH")
    c.resolve(c.ids[0], "ft", "exclude", reason="Wrong publication type")
    studies = {s["label"]: s for s in c.get("/api/extract")["studies"]}
    assert studies["Ngata 2024"]["key"] == c.ids[1]
    c.be("NH")
    assert study(c, c.ids[0])["study"]["key"] == c.ids[1]          # an old link still works
    assert study(c, c.ids[1])["state"]["mine"]["country"]["v"] == "Aotearoa New Zealand"


# ------------------------------------------------------------------------------------------------ risk of bias
DESIGN_FOR = {"rob2": "rct", "rob2_cluster": "cluster_rct", "rob2_crossover": "crossover_rct", "robins2": "nrsi",
              "robins_ba": "its", "robins_e": "nrse", "quadas2": "dta", "probast_ai": "prediction", "quips": "prognostic_factor"}


def define(c, key, tool, label="Time to triage", **more):
    """Add an assessment as reviewers do: by choosing the study's design from the plan (the tool follows)."""
    return c.post("/api/rob/define", {"key": key, "design": DESIGN_FOR[tool], "label": label, **more})


def judge_all(c, who, key, aid, tool, pick, support=""):
    c.be(who)
    for code, _ in review.TOOLS[tool]["domains"]:
        c.post("/api/rob/judge", {"key": key, "a": aid, "d": code, "j": pick(code), "q": support})


def low(t, code):
    return review.scale_for(t, code)[0]


def most_severe(t, code):
    return max(review.scale_for(t, code), key=lambda j: robtools.sev(t, j) if robtools.sev(t, j) is not None else -9)


@pytest.mark.parametrize("tool", list(review.TOOLS))
def test_risk_of_bias_with_each_tool_is_judged_by_both_and_agreed_by_the_lead(inc, tool):
    c = inc
    t = review.TOOLS[tool]
    key = c.keys[list(review.TOOLS).index(tool) % 4]
    c.be("NH")
    s = define(c, key, tool, "Under-triage at 3 months, AI versus usual triage")
    (a,) = s["rob"]
    aid = a["a"]
    assert a["tool"] == tool and a["tool_name"] == t["name"] and a["design"] == DESIGN_FOR[tool]
    assert [d["d"] for d in a["domains"]] == [x[0] for x in t["domains"]]
    assert all(d["scale"] == review.scale_for(t, d["d"]) for d in a["domains"])
    assert a["link"][1].startswith("https://")                       # the official tool, for its signalling questions
    judge_all(c, "NH", key, aid, tool, lambda d: low(t, d), "NH support: " + NH_SECRET)
    first = t["domains"][0][0]
    e = c.error("POST", "/api/rob/judge", {"key": key, "a": aid, "d": first, "j": "Terrible"})
    assert e.message == "Choose one of the judgements."
    c.post("/api/rob/done", {"key": key, "a": aid, "done": True})
    c.be("CH")
    mine = study(c, key)["rob"][0]
    assert mine["done_a"] is True and mine["both_done"] is False
    assert all("a" not in d and d["mine"] is None for d in mine["domains"])          # NH's judgements are not shown
    assert NH_SECRET.encode() not in c.request("GET", f"/api/extract/study?key={key}")[2]
    worst = review.scale_for(t, first)[2]
    over = {"O": worst if worst in review.scale_for(t, "O") else most_severe(t, "O")} if "O" in dict(t["domains"]) else {}
    judge_all(c, "CH", key, aid, tool, lambda d: worst if d == first else over.get(d, low(t, d)), "CH: page 3")
    out = c.post("/api/rob/done", {"key": key, "a": aid, "done": True})
    v = out["rob"][0]
    assert v["both_done"] and v["problems"] == []
    d0 = next(d for d in v["domains"] if d["d"] == first)
    assert (d0["a"], d0["b"], d0["final"], d0["severer"]) == (low(t, first), worst, None, worst)
    for who in ("NH", "CH"):
        c.be(who)
        assert "cannot be reopened" in c.error("POST", "/api/rob/done", {"key": key, "a": aid, "done": False}).message
    c.be("CH")
    out = c.post("/api/rob/agree", {"key": key, "a": aid, "d": first, "j": worst})
    d0 = next(d for d in out["rob"][0]["domains"] if d["d"] == first)
    assert d0["agreed"] == worst and d0["final"] == worst
    if over:
        assert next(d for d in out["rob"][0]["domains"] if d["d"] == "O")["final"] is None
        out = c.post("/api/rob/agree", {"key": key, "a": aid, "d": "O", "j": over["O"]})
    assert out["rob"][0]["problems"] == []
    st, _, body = c.download("rob")
    sheets = check_xlsx(body)
    name = review.SHEET.get(tool, t["short"])[:31]
    assert name in sheets and sheets[name][1][2] == "Under-triage at 3 months, AI versus usual triage"
    assert sheets[name][1][1] == a["design_name"]
    assert sheets["Both reviewers"][1][4] in review.scale_for(t, first)
    assert any(r[0].startswith(t["name"]) for r in sheets["Plan and tools"])
    if t.get("domains_from"):                                       # N8: which version's domains are recorded
        assert any(t["domains_from"] in r[0] for r in sheets["Plan and tools"])
    st, _, body = c.download("rob_svg")
    assert st == 200 and t["name"] in svg_text(body)
    if not over:                                                   # QUIPS has no overall judgement, and says so
        assert "This tool has no overall judgement" in svg_text(body)


def test_each_cluster_trial_judges_d1b_and_rob2_for_individually_randomised_trials_has_no_d1b(inc):
    """RoB 2's cluster-trial domain (D1b) was once optional in the one RoB 2, so a cluster trial could be finished with D1b
    'Not applicable'. Cluster trials now have RoB 2 for cluster-randomised trials, whose D1a and D1b are both required
    and have no Not applicable; RoB 2 for individually randomised trials has no D1b."""
    c = inc
    key = c.keys[0]
    c.be("NH")
    assert "D1b" not in dict(review.TOOLS["rob2"]["domains"])
    aid = define(c, key, "rob2_cluster")["rob"][0]["a"]
    c.post("/api/rob/judge", {"key": key, "a": aid, "d": "D1a", "j": "Low risk"})
    assert c.error("POST", "/api/rob/judge", {"key": key, "a": aid, "d": "D1b", "j": "Not applicable"}).message == \
        "Choose one of the judgements."
    e = c.error("POST", "/api/rob/done", {"key": key, "a": aid, "done": True})
    assert e.message.startswith("Give a judgement for every domain first: Timing of identification or "
                                "recruitment of participants; Deviations")
    aid2 = define(c, c.keys[1], "robins2", "Over-triage")["rob"][0]["a"]
    c.post("/api/rob/judge", {"key": c.keys[1], "a": aid2, "d": "D1", "j": review.QUALIFIED_LOW})
    assert c.error("POST", "/api/rob/judge", {"key": c.keys[1], "a": aid2, "d": "D2", "j": review.QUALIFIED_LOW}).message == \
        "Choose one of the judgements."
    # ROBINS-I V2 has no plain "Low" for confounding or overall: only the qualified one (riskofbias.info, 2024 and 2025)
    assert c.error("POST", "/api/rob/judge", {"key": c.keys[1], "a": aid2, "d": "D1", "j": "Low risk"}).message == \
        "Choose one of the judgements."


def test_an_assessments_design_cannot_change_once_judged_and_only_the_two_reviewers_judge(inc):
    c = inc
    key = c.keys[2]
    c.be("NH")
    aid = define(c, key, "robins_ba")["rob"][0]["a"]
    c.post("/api/rob/judge", {"key": key, "a": aid, "d": "D1", "j": "Serious risk"})
    e = c.error("POST", "/api/rob/define", {"key": key, "a": aid, "design": "rct", "label": "Time to triage"})
    assert "its design and tool cannot change" in e.message
    c.post("/api/rob/define", {"key": key, "a": aid, "label": "Time to triage (minutes)"})        # renamed: design kept
    v = study(c, key)["rob"][0]
    assert v["label"] == "Time to triage (minutes)" and v["tool"] == "robins_ba" and v["design"] == "its"
    c.be("CH")
    c.settings(reviewers=[{"id": "CH", "name": "Dr Colin Hawthorne", "role": "lead"}, {"id": "NH", "name": "Nadine Hayes", "role": "reviewer"},
                          {"id": "SP", "name": "Sam Patel", "role": "reviewer"}])
    c.be("SP")
    assert c.error("POST", "/api/rob/judge", {"key": key, "a": aid, "d": "D2", "j": "Low risk"}).message == \
        "Only the two reviewers judge risk of bias."


def test_the_workbooks_open_and_keep_awkward_text_as_text(inc):
    c = inc
    k = c.keys[3]
    awkward = "=HYPERLINK(\"http://x\") & <b>bold</b> \x0b tab\tend 'quotes' “curly” Māori"
    c.be("NH")
    save(c, k, "notes", awkward)
    save(c, k, "n", "812")
    save(c, k, "outcomes", OUTCOMES)
    c.post("/api/extract/done", {"key": k, "done": True})
    c.be("CH")
    save(c, k, "outcomes", OUTCOMES)
    c.post("/api/extract/done", {"key": k, "done": True, "seen": study(c, k)["state"]["seen"]})
    st, hd, body = c.download("extraction")
    assert st == 200 and hd["content-type"].startswith("application/vnd.openxmlformats")
    assert "data%20extraction.xlsx" in hd["content-disposition"]
    sheets = check_xlsx(body)
    assert set(sheets) >= {"Extraction", "Sources", "Status", "Disagreements", "Form"}
    head, *rows = sheets["Extraction"]
    row = next(r for r in rows if r[0] == "Patel 2022")
    assert row[head.index("Notes")].startswith("=HYPERLINK") and "Māori" in row[head.index("Notes")]
    assert row[head.index("Number of participants")] == "812"
    table = next(v for n, v in sheets.items() if n.startswith("Results"))
    assert table[1][1:3] == ["Time to triage", "median minutes"]
    st, _, body = c.download("rob")
    assert check_xlsx(body)                                  # opens even with no assessments
    st, _, body = c.download("rob_svg")
    check_svg(body)


def test_a_number_written_with_a_thousands_comma_keeps_its_value_in_the_workbook(inc):
    c = inc
    k = c.keys[3]
    c.be("NH")
    save(c, k, "n", "12,500")
    c.post("/api/extract/done", {"key": k, "done": True})
    c.be("CH")
    c.post("/api/extract/done", {"key": k, "done": True, "seen": study(c, k)["state"]["seen"]})
    st, _, body = c.download("extraction")
    head, *rows = check_xlsx(body)["Extraction"]
    row = next(r for r in rows if r[0] == "Patel 2022")
    assert row[head.index("Number of participants")] in ("12500", "12,500")


def test_d1b_recorded_with_rob2_before_1_3_0_is_still_shown_and_exported(inc):
    """Before 1.3.0 RoB 2 carried D1b for cluster trials (Not applicable for others). Such judgements are never lost:
    the assessment shows D1b and the workbook exports it, though new RoB 2 assessments have no D1b."""
    c = inc
    key = c.keys[0]
    p = review.Project(c.folder)
    old = {"tool": "rob2", "label": "Time to triage", "deleted": False}            # as 1.2 wrote it: no design
    review._write(p, "NH", "rob", {"s": key, "a": "aOLD01", "def": old})
    t = review.TOOLS["rob2"]
    for who in ("NH", "CH"):
        for code, _ in t["domains"]:
            review._write(p, who, "rob", {"s": key, "a": "aOLD01", "d": code, "j": "Low risk", "q": ""})
        review._write(p, who, "rob", {"s": key, "a": "aOLD01", "d": "D1b", "j": "Not applicable", "q": f"{who}: not a cluster trial"})
        review._write(p, who, "rob", {"s": key, "a": "aOLD01", "done": True})
    v = study(c, key)["rob"][0]
    assert v["design"] == "" and v["both_done"]
    d1b = next(d for d in v["domains"] if d["d"] == "D1b")
    assert d1b["legacy"] and d1b["final"] == "Not applicable" and "recorded before ReviewDesk 1.3.0" in d1b["name"]
    head, row = check_xlsx(c.download("rob")[2])["RoB 2"][:2]
    i = next(k for k, h in enumerate(head) if h.startswith("D1b"))
    assert row[i] == "Not applicable" and row[1] == "(not recorded)"
    assert "D1b" in svg_text(c.download("rob_svg")[2])


# The overall judgement must follow each tool's rule (RoB 2: Sterne et al. BMJ 2019;366:l4898; ROBINS-I: Cochrane
# Handbook 25.3.5; ROBINS-E: Higgins et al. 2024; QUADAS-2: Whiting et al. 2011; PROBAST+AI): never less severe than the
# most severe domain; more severe only with a reason.
LESS_SEVERE = [("rob2", {"D3": "High risk"}, "Some concerns"), ("rob2", {"D2": "Some concerns"}, "Low risk"),
               ("rob2_cluster", {"D1b": "High risk"}, "Some concerns"), ("rob2_crossover", {"DS": "Some concerns"}, "Low risk"),
               ("robins2", {"D4": "Critical risk"}, "Serious risk"), ("robins2", {"D2": "Moderate risk"}, review.QUALIFIED_LOW),
               ("robins_ba", {"D1": "Serious risk"}, "Moderate risk"), ("robins_ba", {"D5": "No information"}, "Low risk"),
               ("robins_e", {"D4": "Very high risk"}, "High risk"), ("quadas2", {"D2": "Unclear"}, "Low risk of bias"),
               ("quadas2", {"A3": "High"}, None), ("probast_ai", {"D4": "High"}, "Unclear"), ("probast_ai", {"A2": "Unclear"}, None)]


@pytest.mark.parametrize("tool,domains,overall", LESS_SEVERE)
def test_an_overall_judgement_less_severe_than_a_domain_cannot_be_finished(inc, tool, domains, overall):
    c = inc
    t = review.TOOLS[tool]
    key = c.keys[0]
    c.be("NH")
    aid = define(c, key, tool)["rob"][0]["a"]
    picks = {**domains, **({"O": overall} if overall else {})}
    judge_all(c, "NH", key, aid, tool, lambda d: picks.get(d, low(t, d)), "page 4")
    e = c.error("POST", "/api/rob/done", {"key": key, "a": aid, "done": True})
    assert "is less severe than" in e.message
    worst = next(iter(domains))
    ov = "AO" if worst.startswith("A") else "O"
    want = domains[worst] if domains[worst] in review.scale_for(t, ov) else (
        "Moderate risk" if domains[worst] == "No information" else most_severe(t, ov))
    c.post("/api/rob/judge", {"key": key, "a": aid, "d": ov, "j": want, "q": "page 4"})
    assert c.post("/api/rob/done", {"key": key, "a": aid, "done": True})["rob"][0]["done_mine"]


def test_quadas_2_overall_is_low_only_when_every_domain_is_low():
    """QUADAS-2 background document: 'low' on all domains → low risk of bias (or low concern); 'high' or 'unclear' on
    one or more → at risk of bias (or concerns regarding applicability)."""
    low_all = {"D1": "Low", "D2": "Low", "D3": "Low", "D4": "Low", "A1": "Low", "A2": "Low", "A3": "Low"}
    ok = {**low_all, "O": "Low risk of bias", "AO": "Low concern regarding applicability"}
    assert review.overall_problems("quadas2", ok) == []
    assert review.overall_problems("quadas2", {**ok, "D3": "Unclear"})
    assert review.overall_problems("quadas2", {**ok, "A1": "High"})
    assert review.overall_problems("quadas2", {**ok, "D3": "Unclear", "O": "At risk of bias"}) == []
    # at risk although every domain is low: allowed, with the reason in its support box
    assert "say why" in review.overall_problems("quadas2", {**ok, "O": "At risk of bias"}, {})[0]


def test_quips_has_no_overall_judgement_and_no_rule_is_invented():
    t = review.TOOLS["quips"]
    assert [n for _, n in t["domains"]] == ["Study participation", "Study attrition", "Prognostic factor measurement",
                                             "Outcome measurement", "Study confounding", "Statistical analysis and reporting"]
    assert t["scale"] == ["Low risk", "Moderate risk", "High risk"] and not t["overall"] and t["rule"] == ""
    assert review.overall_problems("quips", {"D1": "High risk"}) == []


def test_each_tools_marks_are_distinct_and_unclear_is_a_question_mark():
    for k, t in review.TOOLS.items():
        mk = robtools.marks(t)
        for code, _ in t["domains"]:
            scale = review.scale_for(t, code)
            assert len({mk[j] for j in scale}) == len(scale), (k, code)
    assert robtools.marks(review.TOOLS["probast_ai"])["Unclear"] == "?" != robtools.marks(review.TOOLS["rob2"])["Some concerns"]
    assert robtools.marks(review.TOOLS["robins_ba"])["No information"] == "?"


def test_an_overall_judgement_more_severe_than_every_domain_needs_its_reason(inc):
    c = inc
    key = c.keys[0]
    c.be("NH")
    aid = define(c, key, "rob2")["rob"][0]["a"]
    judge_all(c, "NH", key, aid, "rob2", lambda d: {"O": "High risk"}.get(d, "Some concerns"))
    e = c.error("POST", "/api/rob/done", {"key": key, "a": aid, "done": True})
    assert "is more severe than every domain" in e.message and "say why" in e.message
    c.post("/api/rob/judge", {"key": key, "a": aid, "d": "O", "j": "High risk", "q": "Some concerns in four domains together"})
    assert c.post("/api/rob/done", {"key": key, "a": aid, "done": True})["rob"][0]["done_mine"]


def test_no_information_overall_only_when_a_domain_lacks_information_and_none_is_serious():
    assert review.overall_problems("robins_ba", {"D2": "No information", "O": "No information"}) == []
    assert review.overall_problems("robins_ba", {"D2": "Low risk", "O": "No information"})
    assert review.overall_problems("robins_ba", {"D2": "No information", "D3": "Serious risk", "O": "No information"})
    assert review.overall_problems("robins_ba", {"D2": "No information", "D3": "Moderate risk", "O": "Moderate risk"}) == []


def test_the_lead_is_told_when_agreed_domains_leave_the_overall_too_low_and_can_agree_it_again(inc):
    c = inc
    key = c.keys[0]
    c.be("NH")
    aid = define(c, key, "rob2")["rob"][0]["a"]
    for who, d3 in (("NH", "Low risk"), ("CH", "High risk")):
        judge_all(c, who, key, aid, "rob2", lambda d: {"D3": d3, "O": d3}.get(d, "Low risk"), "page 4")
        c.post("/api/rob/done", {"key": key, "a": aid, "done": True})
    c.be("CH")
    v = c.post("/api/rob/agree", {"key": key, "a": aid, "d": "D3", "j": "High risk"})["rob"][0]
    assert v["problems"] == []                                       # the overall is not agreed yet
    v = c.post("/api/rob/agree", {"key": key, "a": aid, "d": "O", "j": "Low risk"})["rob"][0]
    assert v["problems"] and "less severe than D3" in v["problems"][0]
    c.be("NH")
    assert study(c, key)["rob"][0]["problems"] == v["problems"]
    c.be("CH")
    assert c.post("/api/rob/agree", {"key": key, "a": aid, "d": "O", "j": "High risk"})["rob"][0]["problems"] == []


def test_changing_a_judgement_both_reviewers_gave_needs_a_reason_and_is_reported(inc):
    """An agreed judgement neither reviewer gave, where both gave the same one, is an override: the lead must say why,
    and the tables mark it and the methods text counts it."""
    c = inc
    key = c.keys[1]
    c.be("NH")
    aid = define(c, key, "rob2", "Under-triage")["rob"][0]["a"]
    for who in ("NH", "CH"):
        judge_all(c, who, key, aid, "rob2", lambda d: {"D2": "Some concerns", "O": "Some concerns"}.get(d, "Low risk"), "p. 2")
        c.post("/api/rob/done", {"key": key, "a": aid, "done": True})
    c.be("CH")
    e = c.error("POST", "/api/rob/agree", {"key": key, "a": aid, "d": "D2", "j": "High risk"})
    assert "Both reviewers judged D2 Deviations from intended interventions as “Some concerns”" in e.message
    c.post("/api/rob/agree", {"key": key, "a": aid, "d": "D2", "j": "High risk", "why": "Unblinded staff switched groups (p. 6)"})
    v = c.post("/api/rob/agree", {"key": key, "a": aid, "d": "O", "j": "High risk", "why": "Follows from D2"})["rob"][0]
    d2 = next(d for d in v["domains"] if d["d"] == "D2")
    assert d2["override"] == {"from": "Some concerns", "why": "Unblinded staff switched groups (p. 6)", "by": "CH"}
    c.post("/api/rob/agree", {"key": key, "a": aid, "d": "D1", "j": "Low risk"})          # the same as both: no reason needed
    head, *rows = check_xlsx(c.download("rob")[2])["Both reviewers"]
    assert any(r[3].startswith("D2") and r[-1] == "Unblinded staff switched groups (p. 6)" for r in rows)
    from reviewdesk import reporting
    from test_reporting import text
    p = review.Project(c.folder)
    t = text(reporting.rob_docx(p, "NH"))
    assert "both reviewers judged “Some concerns”; the project lead recorded “High risk”. Reason: Unblinded staff" in t
    assert "High risk [a]" in t and "Overall: Overall" not in t
    m = text(reporting.methods_docx(p, "NH"))
    assert "The project lead changed 2 judgements on which both reviewers had agreed (Fraser 2025: D2 and overall)" in m


