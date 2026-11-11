"""Screening plans with one reviewer, and the screening logic's robustness (final audit before 1.3.0 was published).

A person trying ReviewDesk alone chooses "One reviewer screens every record". With the form's default of 100
calibration records, 1.3.0 started a calibration round nobody could ever finish; with the first and second reviewer
left empty, full texts sent on were "included" with nobody's vote. Each test here fails without its fix."""
from __future__ import annotations

import json
import os

import pytest

from reviewdesk import prisma, readiness, screening, stats, userconfig, util, workflow
from reviewdesk.project import Project, ProjectError

SAM = {"id": "SS", "name": "Sam Solo", "role": "lead"}
CH = {"id": "CH", "name": "Dr Colin Hawthorne", "role": "lead"}
NH = {"id": "NH", "name": "Nadine Hayes", "role": "reviewer"}


def ris(n: int) -> bytes:
    out = []
    for i in range(n):
        out += ["TY  - JOUR", f"TI  - A prospective evaluation of emergency department triage tool number {i} in adults",
                f"AU  - Author{i}, A", "PY  - 2021", f"JF  - Journal {i % 5}", f"DO  - 10.1000/solo.{i}", f"AB  - Abstract {i}.",
                "ER  - ", ""]
    return "\n".join(out).encode()


def make(tmp_path, monkeypatch, people, screening_plan, ft_mode="dual", n=12) -> Project:
    monkeypatch.setenv("REVIEWDESK_HOME", str(tmp_path / "home"))
    from reviewdesk import robplan, robtools
    lead = people[0]
    p = Project.create(str(tmp_path / "review"), "A review", lead["name"], lead["id"])
    userconfig.set_identity(p.dir, lead["id"], p.key())
    p.update_settings(lead["id"], {"reviewers": people, "question": "Does it work?",
                                   "criteria": {"include": ["adults"], "exclude": ["children"], "rules": []},
                                   "screening": screening_plan, "fulltext": {"mode": ft_mode}})
    std = {d: (nm, t) for d, nm, t in robtools.STANDARD_DESIGNS}
    robplan.save_plan(p, lead["id"], {"designs": [{"id": "rct", "name": std["rct"][0], "tool": std["rct"][1]}], "tools": {}})
    p.add_import(lead["id"], "medline.ris", ris(n), {"set": "main", "database": "MEDLINE"})
    p.process_imports(lead["id"])
    return p


def again(p: Project) -> Project:
    """The project as the next request sees it (nothing remembered from before)."""
    return Project(p.dir)


def ft_mode_as_130(p: Project, mode: str) -> None:
    """Who assesses full texts changed after screening started, as ReviewDesk 1.3.0 allowed (this version refuses it):
    the setting written straight into the project file, and the started sets' plans without the recorded choice."""
    from reviewdesk.project import MARKER
    s = util.read_json(p.path(MARKER), {})
    s.setdefault("fulltext", {})["mode"] = mode
    util.write_json_atomic(p.path(MARKER), s)
    st = p.state()
    for sv in st.get("sets", {}).values():
        (sv.get("plan_at_start") or {}).pop("ft_fixed", None)
    p.save_state(st, p.lead_id())


def items(rd: dict) -> list:
    return [x for st in rd["steps"] for k in ("missing", "warnings", "later") for x in st.get(k, [])]


SOLO_PLAN = {"mode": "single", "reviewers": ["SS"], "single_reviewer": "SS", "second_reviewer": "", "calibration_size": 100,
             "ai_second_screener": False}


# ================================================================================================ one reviewer, a new project
def test_a_one_reviewer_plan_starts_straight_into_screening_whatever_the_calibration_number(tmp_path, monkeypatch):
    p = make(tmp_path, monkeypatch, [SAM], SOLO_PLAN, ft_mode="single")
    assert screening.plan(p, "main")["calibration_size"] == 0
    steps = [s["id"] for s in workflow.steps_for(p.settings_ro(), 12)]
    assert "calibration" not in steps and "sample" not in steps
    rd = readiness.check(p, "SS")
    words = " ".join(x["text"] for x in items(rd))
    assert "second reviewer" not in words.lower() and "calibration" not in words.lower()
    sv = screening.start(p, "SS", "main")
    assert sv["phase"] == "screening" and not sv.get("calibration_rounds")
    p = again(p)
    assert [(t["task"], t["left"]) for t in screening.tasks_for(p, "SS")] == [("screen", 12)]
    # the steps of this review say one person does them
    st = {s["id"]: s for s in workflow.steps_for(p.settings_ro(), 12, main=p.state_ro()["sets"]["main"])}
    assert "Sam Solo extracts every study's details and outcome data alone" in st["extract"]["what"]
    assert "Sam Solo judges each result alone" in st["rob"]["what"]
    assert st["fulltext"]["what"].startswith("Sam Solo assesses every full text alone.")
    assert st["rob"]["who"] == "one reviewer"


def test_a_project_of_one_with_full_texts_by_both_is_told_what_to_change_before_it_starts(tmp_path, monkeypatch):
    p = make(tmp_path, monkeypatch, [SAM], SOLO_PLAN, ft_mode="dual")
    with pytest.raises(ProjectError) as e:
        screening.start(p, "SS", "main")
    assert "Choose 'One reviewer' for full-text review" in str(e.value)
    found = [x for x in items(readiness.check(p, "SS")) if x["key"] == "plan_ft_two"]
    assert found and found[0]["fix"] == "setup/fulltext" and found[0]["blocks"]


def test_a_single_screener_plan_with_the_reviewer_boxes_empty_never_draws_a_calibration_round(tmp_path, monkeypatch):
    """Two people; NH screens alone; first and second reviewer left on 'Choose...'; calibration 100 (t28)."""
    p = make(tmp_path, monkeypatch, [CH, NH], {"mode": "single", "reviewers": [], "single_reviewer": "NH",
                                               "second_reviewer": "", "calibration_size": 100, "ai_second_screener": False},
             ft_mode="single")
    sv = screening.start(p, "CH", "main")
    assert sv["phase"] == "screening" and not sv.get("calibration_rounds")
    assert screening.ft_required(again(p), "main") == ["NH"]


def test_a_single_screener_plan_with_both_reviewers_named_has_no_calibration_round_either(tmp_path, monkeypatch):
    """The steps of the review left the round out for this plan, but Start drew it (t26)."""
    p = make(tmp_path, monkeypatch, [CH, NH], {"mode": "single", "reviewers": ["CH", "NH"], "single_reviewer": "NH",
                                               "second_reviewer": "CH", "calibration_size": 100, "ai_second_screener": False})
    sv = screening.start(p, "CH", "main")
    assert sv["phase"] == "screening" and not sv.get("calibration_rounds")


# ================================================================================================ already stuck
def stuck_project(tmp_path, monkeypatch) -> Project:
    """A project as ReviewDesk 1.3.0 left it: one person, 'One reviewer screens every record', a calibration round of
    the last 6 records drawn at Start, every one of them voted on, and nothing able to finish it."""
    p = make(tmp_path, monkeypatch, [SAM], dict(SOLO_PLAN, calibration_size=0), ft_mode="dual")
    p.update_settings("SS", {"fulltext": {"mode": "single"}})
    screening.start(p, "SS", "main")
    ft_mode_as_130(p, "dual")                                     # (as 1.3.0 allowed: full texts by "both")
    p = again(p)
    st = p.state()
    sv = st["sets"]["main"]
    old_plan = {k: v for k, v in sv["plan_at_start"].items() if k not in ("set", "same_person_twice")}
    old_plan.update(calibration_size=6, reviewers=["SS"])
    sv.update(phase="calibration", calibration_rounds=[sv["order"][-6:]], plan_at_start=old_plan)
    p.save_state(st, "SS")
    p = again(p)
    for rid in sv["order"][-6:]:
        p.add_decision("SS", rid, "cal", "include")
    return again(p)


def test_a_project_stuck_in_calibration_with_one_reviewer_can_be_moved_on_by_the_lead(tmp_path, monkeypatch):
    p = stuck_project(tmp_path, monkeypatch)
    cal = p.state_ro()["sets"]["main"]["calibration_rounds"][0]
    assert "cannot be finished" in screening.calibration_stuck(p, "main")
    assert not [t for t in screening.tasks_for(p, "SS") if t["task"] == "calibration"]
    found = [x for x in items(readiness.check(p, "SS")) if x["key"] == "calibration_stuck"]
    assert found and found[0]["blocks"] and found[0]["fix"] == "setup/plan" and "End the calibration round" in found[0]["text"]
    with pytest.raises(ProjectError) as e:
        screening.finish_calibration(p, "SS", "main", another_round=True)
    assert "Another round would be the same" in str(e.value)
    sv = screening.finish_calibration(again(p), "SS", "main")
    assert sv["phase"] == "screening" and not sv.get("calibration_results")
    assert sv["calibration_ended"][0]["records"] == 6 and sv["calibration_ended"][0]["votes"] == {"SS": 6}
    p = again(p)
    assert any(e["action"] == "end calibration round that could not be finished" for e in p.audit_entries())
    # the calibration records are screened with the others; the practice votes are kept but never counted
    assert [(t["task"], t["left"]) for t in screening.tasks_for(p, "SS")] == [("screen", 12)]
    o = screening.ta_outcomes(p, "main")
    assert all(o[r]["status"] != "forward" for r in cal)
    assert len(p.decisions()["SS"]) == 6
    assert "calibration" not in [s["id"] for s in workflow.steps_for(p.settings_ro(), 12, main=p.state_ro()["sets"]["main"])]
    assert screening.calibration_stuck(p, "main") == ""
    # and screening goes on to the end: no full text is included before Sam has assessed it
    for rid in p.state_ro()["sets"]["main"]["order"]:
        p.add_decision("SS", rid, "ta", "include")
    p = again(p)
    assert {o["status"] for o in screening.ft_outcomes(p).values()} == {"pending"}
    rid = p.state_ro()["sets"]["main"]["order"][0]
    p.add_decision("SS", rid, "ft", "include", study="Author0 2021")
    assert screening.ft_outcomes(again(p))[rid]["status"] == "included"


def test_a_two_person_calibration_round_is_not_affected(tmp_path, monkeypatch):
    p = make(tmp_path, monkeypatch, [CH, NH], {"mode": "dual", "reviewers": ["CH", "NH"], "single_reviewer": "",
                                               "second_reviewer": "", "calibration_size": 4, "ai_second_screener": False})
    sv = screening.start(p, "CH", "main")
    assert sv["phase"] == "calibration" and len(sv["calibration_rounds"][0]) == 4
    p = again(p)
    assert screening.calibration_stuck(p) == ""
    with pytest.raises(ProjectError) as e:
        screening.finish_calibration(p, "CH", "main")
    assert "Both reviewers must finish" in str(e.value)


# ================================================================================================ never included without votes
def test_nobody_required_to_assess_a_report_means_it_is_not_yet_assessed():
    for req in ([], ["NH"], ["CH", "NH"]):
        assert screening.ft_outcome("r1", req, {}, {})["status"] == "pending"


def test_an_old_single_plan_with_empty_reviewer_boxes_needs_the_screeners_full_text_votes(tmp_path, monkeypatch):
    """1.3.0 started this plan (two people, NH screens alone, reviewer boxes empty, full texts by 'both'): every report
    sent on was then 'included' with no vote, and PRISMA said the review was complete."""
    p = make(tmp_path, monkeypatch, [CH, NH], {"mode": "single", "reviewers": [], "single_reviewer": "NH",
                                               "second_reviewer": "", "calibration_size": 0, "ai_second_screener": False},
             ft_mode="single")
    screening.start(p, "CH", "main")
    ft_mode_as_130(p, "dual")
    p = again(p)
    st = p.state()
    st["sets"]["main"]["plan_at_start"]["reviewers"] = []           # as 1.3.0 recorded it
    p.save_state(st, "CH")
    p = again(p)
    order = p.state_ro()["sets"]["main"]["order"]
    for rid in order[:3]:
        p.add_decision("NH", rid, "ta", "include")
    for rid in order[3:]:
        p.add_decision("NH", rid, "ta", "exclude")
    p = again(p)
    assert screening.ft_required(p, "main") == ["NH"]
    assert {o["status"] for o in screening.ft_outcomes(p).values()} == {"pending"}
    assert prisma.counts(p)["included_reports"] == 0


@pytest.mark.parametrize("plan", [
    {"mode": "single", "reviewers": [], "single_reviewer": "", "second_reviewer": ""},
    {"mode": "dual", "reviewers": [], "single_reviewer": "", "second_reviewer": ""},
    {"mode": "sample_then_single", "reviewers": ["CH"], "single_reviewer": "", "second_reviewer": ""}])
def test_full_text_by_one_reviewer_with_nobody_chosen_breaks_no_page(tmp_path, monkeypatch, plan):
    """ft_required took the first of an empty list: about 40 pages answered with an error (code-logic A6)."""
    p = make(tmp_path, monkeypatch, [CH, NH], dict(plan, calibration_size=0, ai_second_screener=False), ft_mode="single")
    assert screening.ft_required(p, "main") == (plan["reviewers"] or [])[:1]
    screening.ft_outcomes(p)
    prisma.counts(p)
    rd = readiness.check(p, "CH")
    needed = [x for x in items(rd) if x["blocks"]]
    assert needed and all(x["fix"] for x in needed)
    assert any(x["text"].startswith("Choose") for x in needed)


# ================================================================================================ kappa beside its threshold
def test_a_kappa_just_under_the_threshold_is_never_printed_as_the_threshold():
    assert stats.fmt_kappa(0.7953964, 0.8) == "0.795"
    assert stats.fmt_kappa(0.7999999999999999, 0.8) == "0.80"           # (reaches it, allowing for rounding)
    assert stats.fmt_kappa(0.83, 0.8) == "0.83" and stats.fmt_kappa(0.8041, 0.804) == "0.804"
    assert stats.fmt_threshold(0.8) == "0.80" and stats.fmt_threshold(0.805) == "0.805"
    k = stats.kappa({"n": 40, "both_yes": 15, "only_a": 2, "only_b": 2, "both_no": 21})
    assert "Cohen's kappa 0.795 " in stats.kappa_text(k, threshold=0.8)
    assert stats.kappa_cell(dict(k, threshold=0.8)).startswith("0.795 ")


# ================================================================================================ one foreign row
@pytest.mark.parametrize("row", [{"note": "no record, stage or decision"}, {"r": "<rid>", "s": "ta"},
                                 {"r": "<rid>", "s": "ta", "d": "perhaps"}, {"s": "ta", "d": "include"}])
def test_one_foreign_row_in_a_decisions_file_breaks_no_page(tmp_path, monkeypatch, row):
    """A row ReviewDesk did not write, in a file of another computer (which this computer cannot repair): the data
    checks report it; the votes leave it out instead of every page failing."""
    p = make(tmp_path, monkeypatch, [CH, NH], {"mode": "dual", "reviewers": ["CH", "NH"], "calibration_size": 0,
                                               "single_reviewer": "", "second_reviewer": "", "ai_second_screener": False})
    screening.start(p, "CH", "main")
    p = again(p)
    rid = p.state_ro()["sets"]["main"]["order"][0]
    for who in ("CH", "NH"):
        p.add_decision(who, rid, "ta", "exclude")
    own = util.files_in(p.path("decisions"), "NH@*.jsonl")[0]
    other = p.path("decisions", "NH@abcdef.jsonl")                 # as if NH had voted on her own computer
    os.replace(own, other)
    os.remove(p._local("mirror", os.path.basename(own)))
    junk = {k: (rid if v == "<rid>" else v) for k, v in row.items()}
    with open(other, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(junk, t="2099-01-01T10:00:00Z")) + "\n")
    p = again(p)
    assert screening.ta_outcomes(p, "main")[rid]["status"] == "excluded"
    assert screening.overview(p)["sets"]["main"]["counts"]["excluded"] == 1
    prisma.counts(p)
    readiness.check(p, "CH")
    assert p.integrity_report()["problems"]


# ================================================================================================ words
def test_counts_of_records_read_correctly_in_the_singular():
    assert screening.ai_not_sent(1).startswith("1 record in this set has not been sent")
    assert screening.ai_not_sent(2).startswith("2 records in this set have not been sent")
    assert "but 1 record in this set has not" in screening.ai_planned_not_sent(1)


def test_the_last_record_left_reads_in_the_singular(tmp_path, monkeypatch):
    p = make(tmp_path, monkeypatch, [SAM], SOLO_PLAN, ft_mode="single", n=1)
    screening.start(p, "SS", "main")
    texts = [x["text"] for x in items(readiness.check(again(p), "SS"))]
    assert "You have 1 of 1 record left to screen." in texts


def test_the_whole_sentence_about_a_kappa_just_under_the_threshold_reads_correctly():
    """Table (17, 2, 2, 19): kappa 0.799499 (methods audit, SHOULD FIX 4)."""
    t = {"both_yes": 17, "only_a": 2, "only_b": 2, "both_no": 19, "n": 40}
    k = stats.kappa(t)
    sr = {**{x: k[x] for x in ("n", "agreement", "kappa", "ci_low", "ci_high")}, "table": t, "threshold": 0.8}
    text = stats.kappa_text(sr)
    assert text.startswith("Cohen's kappa 0.799 (") and "0.80 (" not in text
    assert stats.threshold_text(sr, 0.8) == "Both confidence intervals include values at or above the pre-specified threshold of 0.80."


def test_the_small_sample_sentence_reads_correctly_at_its_edges():
    one = stats.kappa({"both_yes": 0, "only_a": 1, "only_b": 0, "both_no": 0, "n": 1})
    s = stats.small_sample_text(one, "A", "B", "the decision")
    assert "1 record," in s and "1 records" not in s and "largely by chance" not in s and "0 by construction" in s
    flat = stats.kappa({"both_yes": 0, "only_a": 0, "only_b": 3, "both_no": 9, "n": 12})
    s = stats.small_sample_text(flat, "Nadine Hayes", "Dr Colin Hawthorne")
    assert "Because Nadine Hayes gave the same answer for every record, kappa is 0 by construction" in s
    usual = stats.kappa({"both_yes": 3, "only_a": 1, "only_b": 1, "both_no": 15, "n": 20})
    assert "largely by chance" in stats.small_sample_text(usual, "A", "B")
