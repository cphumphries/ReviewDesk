"""Problems the full-scale beta test found (the S1 de-duplication tests are in test_dedup.py), each with its test.

S2, S3: while one reviewer screens alone with the AI behind her, nothing she can ask for may count the lead's second
looks or show the AI's keep rate: her responses must be the same whatever the AI kept (two worlds, same votes of hers).
S4: a crash part-way through Remove duplicates never raises a false data alarm, and the files and records agree.
S6 (and the novice beta test's N2, N14, N15): readiness counts as Needed only what can and must be done now, gives
each person their own work as Needed, and says what they are waiting for."""
from __future__ import annotations

import json
import os
import re

import pytest

from helpers import read_json, record_plan, reference_plan, reference_project, ris, screen_all, tiny_records
from mock_elm import TEST_KEY
from reviewdesk import readiness, review, screening, server, util
from reviewdesk.project import Project

TEAM = [{"id": "CH", "name": "Colin Hawthorne", "role": "lead"}, {"id": "NH", "name": "Nadine Hayes", "role": "reviewer"}]
READY = {"reviewers": TEAM, "question": "What are the effects and accuracy of AI triage tools in emergency departments?",
         "criteria": {"include": ["Prospective evaluation of an AI triage tool"], "exclude": ["Retrospective data only"], "rules": []},
         "screening": {"mode": "sample_then_single", "reviewers": ["NH", "CH"], "single_reviewer": "NH", "second_reviewer": "CH"}}


# ================================================================================================ S2 and S3: blind to the AI
def one_world(c, parent: str, ai_extra: int, second_looks: int) -> dict:
    """The reference plan on 40 records, with the same seed and the same votes from NH every time: only what the AI
    keeps beyond the eligible records (ai_extra of NH's exclusions) and how many second looks CH has given differ.
    Returns NH's view: every route and download she can ask for that could show it, as JSON text."""
    folder = reference_project(c, parent, screening=reference_plan(calibration_size=0, seed=20261104))
    c.add_import(ris(*tiny_records(40, seed=11)), "medline.ris", "MEDLINE")
    c.process()
    c.post("/api/screening/start", {"set": "main"})
    sv = read_json(os.path.join(folder, "state", "state.json"))["sets"]["main"]
    sample, rest = sv["sample"], [r for r in sv["order"] if r not in sv["sample"]]
    eligible = set(sample[:3]) | set(rest[:4])
    keep = eligible | set(rest[4:4 + ai_extra])
    by_title = {r["title"]: r["id"] for r in c.get("/api/records", limit=100)["records"]}
    c.elm.decide = lambda call: "INCLUDE" if by_title.get(call.title) in keep else "EXCLUDE"
    assert c.run_ai()["message"] == "Finished"
    vote = lambda r: "include" if r in eligible else "exclude"          # noqa: E731
    screen_all(c, "NH", vote, "sample")
    screen_all(c, "CH", vote, "sample")
    c.be("CH")
    assert c.post("/api/screening/apply", {"set": "main"})["sets"]["main"]["ai_net_active"]
    screen_all(c, "NH", vote, "screen")
    c.be("CH")
    second = [x["id"] for x in c.queue("second")]
    assert len(second) == ai_extra
    for rid in second[:second_looks]:                       # the lead's second looks: he excludes them again
        c.decide(rid, "ta", "exclude")
    lead_view = c.get("/api/agreement", scope="remainder")
    c.be("NH")
    got = {}
    for path, params in (("/api/overview", {}), ("/api/agreement", {"scope": "remainder"}), ("/api/agreement", {"scope": "sample"}),
                         ("/api/tasks", {}), ("/api/queue", {"task": "screen"}), ("/api/readiness", {}), ("/api/prisma", {}),
                         ("/api/timeline", {}), ("/api/conflicts", {}), ("/api/fulltext", {}), ("/api/ai", {})):
        got[path + json.dumps(params)] = c.get(path, **params)
    for kind in ("summary", "prisma"):
        st, _, body = c.download(kind)
        got["/download/" + kind] = body.decode("utf-8")
    text = json.dumps(got, sort_keys=True, ensure_ascii=False).replace(folder, "<folder>")
    text = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)?", "<time>", text)
    return {"nh": json.loads(text), "lead_remainder": lead_view, "nh_text": text}


def test_a_reviewer_screening_alone_sees_the_same_whatever_the_ai_kept(desk, elm, tmp_path):
    c = desk.client()
    c.post("/api/user", {"elm_url": elm.url, "elm_key": TEST_KEY})
    c.elm = elm
    a = one_world(c, str(tmp_path / "world A"), ai_extra=6, second_looks=3)       # the AI kept 13 of 40 (32.5%)
    b = one_world(c, str(tmp_path / "world B"), ai_extra=2, second_looks=1)       # the AI kept 9 of 40 (22.5%)
    # the worlds really differ: the lead sees his second looks counted
    assert a["lead_remainder"]["done_b"] == 3 and b["lead_remainder"]["done_b"] == 1
    # ... and the reviewer sees exactly the same in both
    for k in a["nh"]:
        assert a["nh"][k] == b["nh"][k], k
    # nothing counts the lead's votes on the records she screens alone, and no keep rate is anywhere
    rem = a["nh"]['/api/agreement{"scope": "remainder"}']
    assert "done_b" not in rem and "both_done" not in rem and rem["done_a"] == 32 and not rem["complete"]
    assert not re.search(r"kept \d", a["nh_text"])
    assert "32.5%" not in a["nh_text"] and '"keep_rate"' not in a["nh_text"]
    sr = a["nh"]["/api/overview{}"]["sets"]["main"]["sample_result"]
    assert sr["ai_rule"] == "the stopping rule allowed one reviewer to screen the rest, with the AI second screener behind them"
    assert "ai" not in sr
    assert ("Under the pre-specified stopping rule, the AI second screener stayed in use behind the single reviewer"
            in a["nh"]["/api/prisma{}"]["summary"])          # the outcome of the rule (exports.ai_rule_text), no figures


def test_the_lead_keeps_the_ai_figures(desk, elm, tmp_path):
    c = desk.client()
    c.post("/api/user", {"elm_url": elm.url, "elm_key": TEST_KEY})
    c.elm = elm
    one_world(c, str(tmp_path / "w"), ai_extra=6, second_looks=3)
    c.be("CH")
    assert "the AI kept 32.5% of records, within the 50% limit" in c.overview()["sets"]["main"]["sample_result"]["ai_rule"]
    assert "the AI kept 32.5% of records" in c.get("/api/prisma")["summary"]
    assert c.get("/api/agreement", scope="remainder")["both_done"] == 3


def test_the_rule_outcome_for_reviewers_has_no_figure():
    stop = {"ai_rule": "the AI kept 61.0% of records, above the 50% limit, so the AI step was dropped and both reviewers "
                       "screened every record", "ai_rule_kind": "stopping_rule", "ai": {"keep_rate": 0.61}, "kappa": 0.9}
    out = server.sample_result_for(stop, lead=False)
    assert out == {"ai_rule": "the stopping rule dropped the AI step, so both reviewers screened every record",
                   "ai_rule_kind": "stopping_rule", "kappa": 0.9}
    assert server.sample_result_for(stop, lead=True) is stop
    old = {"ai_rule": "the AI kept 30.1% of records, within the 50% limit, so every record the single reviewer excluded "
                      "but the AI would keep went to the second reviewer"}           # a project from before ai_rule_kind
    assert "%" not in server.sample_result_for(old, lead=False)["ai_rule"]
    dropped = {"ai_rule": "the AI step could not be completed (the service closed), so it was dropped and both reviewers "
                          "screened every record", "ai_rule_kind": "dropped"}
    assert server.sample_result_for(dropped, lead=False)["ai_rule"] == dropped["ai_rule"]       # no figure in it


# ================================================================================================ S4: a crash while adding records
class Crash(Exception):
    pass


def make_crash(monkeypatch, at: str):
    """Stop ReviewDesk (as a crash would) just before it writes `at`: 'records' (after the state with the new IDs),
    'dedup' (after the records) or 'state' (after both, before the state that adds the files)."""
    real = util.write_json_atomic
    seen = {"state": 0}

    def write(path, obj, *a, **kw):
        name = os.path.basename(path)
        if name == "state.json":
            seen["state"] += 1
        if (at == "records" and name == "records.json") or (at == "dedup" and name == "dedup.json") \
                or (at == "state" and name == "state.json" and seen["state"] == 2):
            raise Crash(at)
        return real(path, obj, *a, **kw)
    monkeypatch.setattr(util, "write_json_atomic", write)


def lead_project(tmp_path, name="p") -> Project:
    p = Project.create(str(tmp_path / name), "AI in emergency department triage", "Colin Hawthorne", "CH")
    p.update_settings("CH", {**READY, "screening": {"mode": "dual", "reviewers": ["NH", "CH"], "calibration_size": 0}})
    record_plan(p)
    p.add_import("CH", "medline.ris", ris(*tiny_records(8, seed=31)), {"set": "main", "database": "MEDLINE"})
    p.process_imports("CH")
    screening.start(p, "CH", "main")
    return p


@pytest.mark.parametrize("at", ["records", "dedup", "state"])
def test_a_crash_while_adding_records_never_raises_a_false_alarm(tmp_path, monkeypatch, at):
    control = lead_project(tmp_path, "control")                                   # the same, without a crash
    upd = ris(*tiny_records(3, seed=31)[:1], *tiny_records(4, seed=32, prefix="New: "))
    control.add_import("CH", "update.ris", upd, {"set": "update", "database": "Embase"})
    control.process_imports("CH")
    want = [(r["id"], r["title"]) for r in control.records()]

    p = lead_project(tmp_path)
    before = [r["id"] for r in p.records()]
    m = p.add_import("CH", "update.ris", upd, {"set": "update", "database": "Embase"})
    make_crash(monkeypatch, at)
    with pytest.raises(Crash):
        p.process_imports("CH")
    monkeypatch.undo()
    p = Project(p.dir)                                                             # ReviewDesk started again
    assert p.integrity_report()["problems"] == []                                  # no false alarm
    # the file is not added yet, and nothing of it is counted: the records and the PRISMA counts agree with that
    assert m["id"] not in p.state_ro()["imports_order"] and p.adding_interrupted() == [m["id"]]
    assert [r["id"] for r in p.records()] == before
    assert m["id"] not in [f["import"] for f in p.dedup_report()["files"]]
    assert screening.overview(p)["sets"].get("update", {}).get("records", 0) == 0
    # readiness says plainly what happened and what to do (and the lead's own Needed item counts)
    r = readiness.check(p, "CH")
    item = next(x for s in r["steps"] if s["id"] == "duplicates" for x in s["missing"] if x["key"] == "interrupted")
    assert item["text"].startswith("Adding records was interrupted") and "choose Remove duplicates and add the files again" in item["text"].replace("Choose", "choose")
    nh = readiness.check(p, "NH")
    assert any("add the imported files to the records again" in x["text"] for s in nh["steps"] for x in s["warnings"])
    # choosing Remove duplicates again gives what an uninterrupted run gives: the same IDs, none used twice
    p.process_imports("CH")
    assert [(r["id"], r["title"]) for r in p.records()] == want
    assert p.adding_interrupted() == [] and "processing" not in p.state_ro()
    assert p.integrity_report()["problems"] == []
    assert "interrupted" not in {x["key"] for s in readiness.check(p, "CH")["steps"] for x in s["missing"]}


def test_removing_a_processed_file_keeps_its_ids_known_until_the_records_are_rebuilt(tmp_path, monkeypatch):
    p = Project.create(str(tmp_path / "p"), "Review", "Colin Hawthorne", "CH")
    p.add_import("CH", "a.ris", ris(*tiny_records(3, seed=41)), {"set": "main", "database": "MEDLINE"})
    m = p.add_import("CH", "b.ris", ris(*tiny_records(2, seed=42)), {"set": "main", "database": "Embase"})
    p.process_imports("CH")
    p.remove_import("CH", m["id"])               # a crash before the rebuild that follows leaves its records in place
    assert p.integrity_report()["problems"] == []
    p.process_imports("CH")
    assert len(p.records()) == 3 and p.integrity_report()["problems"] == []


# ================================================================================================ S6: readiness
def make(tmp_path, name="p", **settings) -> Project:
    p = Project.create(str(tmp_path / name), "AI in emergency department triage", "Colin Hawthorne", "CH")
    p.update_settings("CH", {**READY, **settings})
    record_plan(p)
    return p


def items(r: dict, step: str | None = None, part: str = "both") -> list:
    out = []
    for s in r["steps"]:
        if step is None or s["id"] == step:
            out += (s["missing"] if part in ("both", "missing") else []) + (s["warnings"] if part in ("both", "warnings") else [])
    return out


def step(r: dict, sid: str) -> dict:
    return next(s for s in r["steps"] if s["id"] == sid)


def no_later_step_needs_anything(r: dict) -> None:
    for s in r["steps"]:
        if s["status"] == "later":
            assert s["missing"] == [] and s["warnings"] == [], s["id"]
        if s["status"] == "done":
            assert s["missing"] == [], s["id"]


def test_future_steps_are_not_needed_now(tmp_path):
    p = make(tmp_path)
    for who in ("CH", "NH"):
        r = readiness.check(p, who)
        no_later_step_needs_anything(r)
        assert r["missing"] == 0                                     # set up, nothing logged: nothing is needed yet
        assert step(r, "certainty")["status"] == "later" and step(r, "calibration")["status"] == "later"
        assert not [x for x in items(r) if "No studies are included" in x["text"] or "No outcomes" in x["text"]]
        assert not [x for x in items(r) if "No records have been added" in x["text"]]
    # what a later step will need is kept for later, not lost
    assert "no_records" in {x["key"] for x in step(readiness.check(p, "CH"), "calibration")["later"]}


def test_during_screening_each_reviewer_needs_only_their_own_work(tmp_path):
    p = make(tmp_path, screening={**READY["screening"], "calibration_size": 10})
    p.add_import("CH", "medline.ris", ris(*tiny_records(60, seed=5)), {"set": "main", "database": "MEDLINE"})
    p.process_imports("CH")
    # before screening starts, the reviewer is told she waits for the lead to start it
    nh = readiness.check(p, "NH")
    cal = step(nh, "calibration")
    assert cal["status"] == "now" and cal["missing"] == []
    assert cal["warnings"][0]["text"].startswith("Waiting for Colin Hawthorne (the project lead) to start title and abstract screening")
    assert cal["warnings"][0]["waiting"] and nh["missing"] == 0
    assert step(readiness.check(p, "CH"), "calibration")["warnings"] == []
    screening.start(p, "CH", "main")
    for who in ("CH", "NH"):
        r = readiness.check(p, who)
        no_later_step_needs_anything(r)
        need = items(r, part="missing")
        assert [x["key"] for x in need] == ["calibration_me"] and r["missing"] == 1 and need[0]["who"] == "me"


def full_text_project(tmp_path) -> tuple:
    p = make(tmp_path, screening={"mode": "dual", "reviewers": ["NH", "CH"], "calibration_size": 0},
             registration={"registry": "OSF Registries", "id": "", "date": "", "url": "https://osf.io/abcd1"})
    p.add_import("CH", "medline.ris", ris(*tiny_records(6, seed=21)), {"set": "main", "database": "MEDLINE"})
    p.process_imports("CH")
    screening.start(p, "CH", "main")
    recs = [r["id"] for r in p.records()]
    for rid in recs:
        p.add_decision("CH", rid, "ta", "include")
        p.add_decision("NH", rid, "ta", "include")
    return p, recs


def test_full_text_disagreements_say_the_reviewer_waits_for_the_joint_decision(tmp_path):
    p, recs = full_text_project(tmp_path)
    for rid in recs:                                         # NH assesses all six; CH three, and disagrees on one
        p.add_decision("NH", rid, "ft", "exclude" if rid == recs[2] else "include", reason="Wrong population" if rid == recs[2] else "")
    for rid in recs[:3]:
        p.add_decision("CH", rid, "ft", "include")
    nh = readiness.check(p, "NH")
    no_later_step_needs_anything(nh)
    ft = step(nh, "fulltext")
    assert ft["missing"] == []                               # nothing left for her to assess
    wait = next(x for x in ft["warnings"] if x["key"] == "ft_conflicts_wait")
    assert wait["waiting"] and wait["text"].startswith("Waiting for a joint decision on 1 full-text disagreement")
    assert "Colin Hawthorne (the project lead)" in wait["text"]
    open_ft = next(x for x in items(nh, "report") if x["key"] == "open_ft")
    assert open_ft["who"] != "me" and "still open" not in open_ft["text"]
    assert open_ft["text"] == ("The full-text decisions are not finished: 3 reports wait for another reviewer's decision and "
                               "1 waits for a joint decision, so the PRISMA diagram and the counts will still change.")
    ch = readiness.check(p, "CH")
    assert "task_fulltext" in {x["key"] for x in items(ch, "fulltext", "missing")}          # his own: needed
    assert next(x for x in items(ch, "report") if x["key"] == "open_ft")["who"] == "me"


def test_a_report_awaiting_information_is_not_nagged_for_a_pdf(tmp_path):
    p, recs = full_text_project(tmp_path)
    for who in ("CH", "NH"):
        p.add_decision(who, recs[0], "ft", "awaiting")
        p.add_decision(who, recs[1], "ft", "ongoing")
    r = readiness.check(p, "NH")
    no_pdf = next(x for x in items(r, "get_fulltexts") if x["key"] == "no_pdf")
    assert no_pdf["text"].startswith("4 reports at full text have no PDF yet")


def extract_project(tmp_path) -> tuple:
    p, recs = full_text_project(tmp_path)
    for who in ("CH", "NH"):
        for rid in recs[:2]:
            p.add_decision(who, rid, "ft", "include")
        for rid in recs[2:]:
            p.add_decision(who, rid, "ft", "exclude", reason="Wrong population")
    review.save_form(p, "CH", {**review.form(p), "first": "NH", "second": "CH"})
    return p, [s["key"] for s in review.studies(p)]


def test_extraction_says_the_first_extractor_waits_for_the_check_and_the_form_warning_goes_when_finished(tmp_path):
    p, keys = extract_project(tmp_path)
    assert len(keys) == 2
    assert "form_builtin" in {x["key"] for x in items(readiness.check(p, "CH"), "extract")}
    assert "task_extract" in {x["key"] for x in items(readiness.check(p, "NH"), "extract", "missing")}
    for k in keys:
        review.set_done(p, "NH", k, True)
    nh = step(readiness.check(p, "NH"), "extract")
    assert nh["missing"] == [] and nh["warnings"][0]["key"] == "extract_wait" and nh["warnings"][0]["waiting"]
    assert nh["warnings"][0]["text"].startswith("Waiting for Colin Hawthorne to check 2 studies you have extracted")
    assert "task_check" in {x["key"] for x in items(readiness.check(p, "CH"), "extract", "missing")}
    f = review.form(p)
    for k in keys:
        review.set_done(p, "CH", k, True, seen=review.checked_sig(f, review.answers(p)["values"].get("NH", {}).get(k, {})))
    ch = step(readiness.check(p, "CH"), "extract")
    assert ch["status"] == "done" and ch["warnings"] == [] and ch["missing"] == []


def test_each_reviewer_is_told_of_the_risk_of_bias_judgements_waiting_for_them(tmp_path):
    p, keys = extract_project(tmp_path)
    aids = {k: review.define_assessment(p, "CH", k, "rct", "Under-triage, AI versus usual triage")["a"] for k in keys}
    for who in ("CH", "NH"):
        task = next(x for x in items(readiness.check(p, who), "rob", "missing") if x["key"] == "rob_task")
        assert task["who"] == "me" and task["text"].startswith("You have 2 risk-of-bias assessments to judge and mark as finished in 2 studies")
    t = review.TOOLS["rob2"]
    for k, aid in aids.items():
        for code, _ in t["domains"]:
            review.judge(p, "NH", k, aid, code, t["scale"][0])
        review.rob_done(p, "NH", k, aid, True)
    nh = step(readiness.check(p, "NH"), "rob")
    assert nh["missing"] == [] and nh["warnings"][-1]["key"] == "rob_wait" and nh["warnings"][-1]["waiting"]
    assert nh["warnings"][-1]["text"].startswith("Waiting for Colin Hawthorne to finish 2 risk-of-bias assessments")
    assert "rob_task" in {x["key"] for x in items(readiness.check(p, "CH"), "rob", "missing")}


def test_a_logged_database_search_with_no_export_is_needed_before_screening(tmp_path):
    p = make(tmp_path)
    med = p.save_search("CH", {"database": "MEDLINE", "platform": "Ovid", "date": "2026-10-30", "results": "6", "string": "x"})
    p.save_search("CH", {"database": "Embase", "platform": "Ovid", "date": "2026-10-30", "results": "9", "string": "x"})
    p.save_search("CH", {"database": "CINAHL", "platform": "EBSCO", "date": "2026-10-31", "results": "4", "string": "x"})
    p.save_search("CH", {"database": "ClinicalTrials.gov", "platform": "web", "date": "2026-10-31", "results": "3", "string": "x"})
    p.save_search("CH", {"database": "Scopus", "platform": "Elsevier", "date": "2026-10-31", "results": "0", "string": "x"})
    p.add_import("CH", "medline.ris", ris(*tiny_records(6, seed=51)), {"set": "main", "database": "MEDLINE", "search_id": med["id"]})
    p.add_import("CH", "cinahl.ris", ris(*tiny_records(4, seed=52)), {"set": "main", "database": "CINAHL"})   # not linked
    p.process_imports("CH")
    for who in ("CH", "NH"):
        r = readiness.check(p, who)
        imp = step(r, "import")
        need = next(x for x in imp["missing"] if x["key"] == "no_file")
        assert need["text"].startswith("The Embase search of 30 October 2026 has no export file yet") and need["who"] == "me"
        assert need["fix"] == "import" and need["fix_label"] == "Open Import records"
        assert "MEDLINE" not in need["text"] and "CINAHL" not in need["text"] and "Scopus" not in need["text"]
        reg = next(x for x in imp["warnings"] if x["key"] == "registers_not_imported")  # a register: a Check, named once
        assert reg["text"].startswith("ClinicalTrials.gov (3 records) was searched, but its records have not been imported")
        assert "no_file_other" not in {x["key"] for x in imp["warnings"]}
        assert imp["status"] in ("now", "blocked")                                     # so it is on the Overview
        assert ("unlinked" in {x["key"] for x in imp["warnings"]}) is (who == "CH")    # (with the lead's check)
        first = step(r, "calibration")
        assert first["status"] == "blocked" and "no_file" in {x["key"] for x in first["missing"]}
    emb = next(e for e in p.search_log() if e["database"] == "Embase")
    p.add_import("CH", "embase.ris", ris(*tiny_records(9, seed=53)), {"set": "main", "database": "Embase", "search_id": emb["id"]})
    r = readiness.check(p, "CH")
    assert "no_file" not in {x["key"] for x in items(r)}


def test_the_words_a_user_reads(tmp_path):
    p = make(tmp_path)
    p.add_import("CH", "update.ris", ris(*tiny_records(2, seed=61)), {"set": "update", "database": "Embase"})
    p.process_imports("CH")
    with pytest.raises(Exception) as e:
        screening.start(p, "CH", "main")
    assert str(e.value).startswith("No records have been added yet.")
    assert "on the Screen titles and abstracts page" not in open(readiness.__file__, encoding="utf-8").read()
