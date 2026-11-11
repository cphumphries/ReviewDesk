"""Title and abstract screening as the reference review planned it, through the API: a random order from a recorded
seed; a calibration round whose votes are not counted; the agreement sample, blind until both have finished; the
kappa rule (single screening with the AI second screener behind it, or both screening everything); the AI stopping
limit; the AI's answers shown only to the lead; and disagreements under both rules ("forward" and "discuss")."""
from __future__ import annotations

import json
import os
import random
import re

import pytest

from helpers import ApiError, read_json, reference_plan, reference_project, ris, screen_all, tiny_records
from mock_elm import TEST_KEY
from reviewdesk import stats

SECRET_NOTE = "NH-private-note-7731"


def state(folder: str) -> dict:
    return read_json(os.path.join(folder, "state", "state.json"))["sets"]["main"]


@pytest.fixture
def lead(desk, elm, tmp_path):
    """CH at the lead's computer, the AI service set to the stand-in."""
    c = desk.client()
    c.post("/api/user", {"elm_url": elm.url, "elm_key": TEST_KEY})
    c.tmp = tmp_path
    c.elm = elm
    return c


def project(c, n: int = 40, **plan) -> str:
    folder = reference_project(c, str(c.tmp / "OneDrive"), screening=reference_plan(**plan))
    c.records = tiny_records(n, seed=11)
    c.add_import(ris(*c.records), "medline.ris", "MEDLINE")
    c.process()
    return folder


def titles(c) -> dict:
    return {r["id"]: r["title"] for r in c.get("/api/records", limit=1000)["records"]}


def every_response_hides(c, rid_list, needle=SECRET_NOTE):
    """Nothing CH's page can ask for shows NH's vote (here: the note NH wrote with every vote)."""
    for path in ("/api/overview", "/api/conflicts", "/api/agreement", "/api/prisma", "/api/tasks", "/api/fulltext"):
        st, _, body = c.request("GET", path)
        assert needle.encode() not in body, path
    for rid in rid_list:
        st, _, body = c.request("GET", "/api/record?id=" + rid)
        assert st == 200 and needle.encode() not in body


# ------------------------------------------------------------------------------------------------ the random order
def test_records_are_screened_in_a_random_order_from_a_recorded_seed(lead):
    c = lead
    folder = project(c, 30, mode="dual", calibration_size=0, ai_second_screener=False)
    seed = c.summary()["settings"]["screening"]["seed"]
    assert isinstance(seed, int) and 10_000_000 <= seed <= 99_999_999
    c.post("/api/screening/start", {"set": "main"})
    sv = state(folder)
    ids = sorted(titles(c))
    expected = ids[:]
    random.Random(seed).shuffle(expected)
    assert sv["order"] == expected and sv["seed"] == seed and "random.Random(seed).shuffle" in sv["rng"]
    assert [x["id"] for x in c.queue("screen")] == expected
    started = [e for e in c.get("/api/audit")["entries"] if e["action"] == "start screening"]
    assert started[-1]["details"]["seed"] == seed and started[-1]["details"]["records"] == 30
    assert f"(seed {seed})" in c.get("/api/prisma")["summary"]
    e = c.error("POST", "/api/screening/start", {"set": "main"})
    assert "already started" in e.message
    e = c.error("POST", "/api/settings", {"changes": {"screening": {"seed": 1}}})
    assert "screening plan is fixed" in e.message and "seed" in e.message


def test_screening_cannot_start_until_the_plan_is_complete(lead):
    c = lead
    folder = project(c, 10, reviewers=["NH"], ai_second_screener=False)
    e = c.error("POST", "/api/screening/start", {"set": "main"})
    assert "Choose the two reviewers who screen in duplicate (Project set-up, Screening plan)." in e.message
    c.settings(screening={"reviewers": ["NH", "CH"], "single_reviewer": "XX"})
    e = c.error("POST", "/api/screening/start", {"set": "main"})
    assert "Choose who screens on their own after the sample" in e.message
    c.settings(screening={"single_reviewer": "NH", "ai_second_screener": True, "second_reviewer": "NH"})
    e = c.error("POST", "/api/screening/start", {"set": "main"})
    assert "choose a second reviewer who is not the single reviewer" in e.message
    c.settings(screening={"second_reviewer": "CH"}, criteria={"include": [], "exclude": [], "rules": []})
    e = c.error("POST", "/api/screening/start", {"set": "main"})
    assert "Write the eligibility criteria first" in e.message
    assert read_json(os.path.join(folder, "state", "state.json"))["sets"] == {}       # nothing was started


# ------------------------------------------------------------------------------------------------ calibration
def test_the_calibration_round_is_practice_and_its_votes_are_not_counted(lead):
    c = lead
    folder = project(c, 30, calibration_size=6, ai_second_screener=False)
    c.post("/api/screening/start", {"set": "main"})
    sv = state(folder)
    cal = sv["calibration_rounds"][0]
    assert sv["phase"] == "calibration" and len(cal) == 6 and cal == sv["order"][-6:]
    assert not set(cal) & set(sv["sample"]) and len(sv["sample"]) == 6
    c.be("NH")
    assert [t["task"] for t in c.tasks()] == ["calibration"]
    assert c.task("calibration")["label"] == "Calibration round 1"
    nh = {r: ("include" if k % 2 else "exclude") for k, r in enumerate(cal)}
    ch = {r: ("include" if k % 3 else "exclude") for k, r in enumerate(cal)}
    screen_all(c, "NH", nh.get, "calibration")
    c.be("CH")
    for r in cal[:5]:                                       # CH has one record still to do
        c.decide(r, "cal", ch[r])
    e = c.error("POST", "/api/screening/calibration", {"set": "main"})
    assert "Both reviewers must finish the calibration records first (6 and 5 of 6 done)." in e.message
    ag = c.get("/api/agreement", scope="calibration")
    assert not ag["complete"] and ag["stats"] == {} and ag["disagreements"] == []
    c.decide(cal[5], "cal", ch[cal[5]])
    ag = c.get("/api/agreement", scope="calibration")
    pairs = [(nh[r] == "include", ch[r] == "include") for r in cal]
    assert ag["complete"] and ag["stats"]["kappa"] == pytest.approx(stats.kappa(stats.two_by_two(pairs))["kappa"])
    assert {d["id"] for d in ag["disagreements"]} == {r for r in cal if nh[r] != ch[r]}
    c.post("/api/screening/calibration", {"set": "main"})
    sv = state(folder)
    assert sv["phase"] == "sample" and sv["calibration_results"][0]["n"] == 6
    # the calibration votes count for nothing: no outcome, nothing screened, and the records are screened again later
    o = c.overview()["sets"]["main"]
    assert o["counts"].get("forward", 0) == 0 and o["counts"].get("excluded", 0) == 0
    assert c.get("/api/prisma")["counts"]["db"]["screened"] == 0
    assert all(x["done"] is False for x in c.queue("sample"))
    for who in ("NH", "CH"):
        screen_all(c, who, lambda r: "exclude", "sample")
    c.post("/api/screening/apply", {"set": "main"})        # kappa cannot be calculated: both screen everything
    rest = [x for x in c.queue("screen")]
    assert set(cal) <= {x["id"] for x in rest} and not any(x["done"] for x in rest if x["id"] in cal)
    summary = c.get("/api/prisma")["summary"]
    assert "Calibration round 1:" in summary and "These practice votes were not used for the agreement sample or for any record's outcome" in summary
    assert "the calibration records were screened again later" in summary


def test_another_calibration_round_uses_new_records(lead):
    c = lead
    folder = project(c, 40, calibration_size=5, ai_second_screener=False)
    c.post("/api/screening/start", {"set": "main"})
    first = state(folder)["calibration_rounds"][0]
    for who in ("NH", "CH"):
        screen_all(c, who, lambda r: "exclude" if who == "NH" else "include", "calibration")
    c.be("CH")
    c.post("/api/screening/calibration", {"set": "main", "another": True})
    sv = state(folder)
    assert sv["phase"] == "calibration" and len(sv["calibration_rounds"]) == 2
    second = sv["calibration_rounds"][1]
    assert len(second) == 5 and not set(second) & set(first) and not set(second) & set(sv["sample"])
    c.be("NH")
    assert c.task("calibration")["label"] == "Calibration round 2" and c.task("calibration")["left"] == 5


def test_a_calibration_agreement_gate_holds_screening_back(lead):
    c = lead
    project(c, 30, calibration_size=4, calibration_gate=0.9, ai_second_screener=False)
    c.post("/api/screening/start", {"set": "main"})
    screen_all(c, "NH", lambda r: "include", "calibration")
    screen_all(c, "CH", lambda r: "exclude", "calibration")
    e = c.error("POST", "/api/screening/calibration", {"set": "main"})
    assert "Agreement on calibration was 0.0%, below the 90% the plan requires" in e.message


# ------------------------------------------------------------------------------------------------ the agreement sample
def test_the_agreement_sample_is_blind_until_both_have_finished(lead):
    c = lead
    folder = project(c, 40, calibration_size=0)
    c.post("/api/screening/start", {"set": "main"})
    sample = state(folder)["sample"]
    assert len(sample) == 8 and state(folder)["phase"] == "sample"
    nh = {r: ("include" if k < 3 else "exclude") for k, r in enumerate(sample)}
    ch = {r: ("include" if k < 2 or k == 5 else "exclude") for k, r in enumerate(sample)}
    c.be("NH")
    for r in sample:
        c.post("/api/decide", {"record": r, "stage": "ta", "decision": nh[r], "note": SECRET_NOTE, "as": "NH"})
    c.be("CH")
    for r in sample[:4]:
        c.decide(r, "ta", ch[r])
    for who in ("CH", "NH"):
        c.be(who)
        ag = c.get("/api/agreement", scope="sample")
        assert not ag["complete"] and ag["stats"] == {} and ag["disagreements"] == [] and "meets_threshold" not in ag
        assert (ag["done_a"], ag["done_b"]) == (8, 4)
        assert c.get("/api/conflicts")["conflicts"] == []
        counts = c.overview()["sets"]["main"]["counts"]
        assert counts == {"pending": 8, "not_open": 32}       # nothing hints at a vote
        for kind in ("records", "decisions"):
            st, _, body = c.download(kind)
            assert st == 403
    c.be("CH")
    every_response_hides(c, sample)
    rv = c.get("/api/record", id=sample[0])
    assert rv["mine"]["ta"]["d"] == ch[sample[0]] and "NH" not in json.dumps(rv["mine"])
    st, _, body = c.download("decisions")
    assert "calibration round and agreement sample are finished" in json.loads(body)["error"]
    e = c.error("POST", "/api/screening/apply", {"set": "main"})
    assert "Both reviewers must finish the agreement sample first (8 and 4 of 8 done)." in e.message
    for r in sample[4:]:
        c.decide(r, "ta", ch[r])
    ag = c.get("/api/agreement", scope="sample")
    pairs = [(nh[r] in ("include", "maybe"), ch[r] in ("include", "maybe")) for r in sample]
    want = stats.kappa(stats.two_by_two(pairs))
    assert ag["complete"] and ag["stats"]["kappa"] == pytest.approx(want["kappa"]) and ag["stats"]["agreement"] == pytest.approx(want["agreement"])
    assert ag["meets_threshold"] is (want["kappa"] >= 0.8)
    dis = {d["id"]: d for d in ag["disagreements"]}
    assert set(dis) == {r for r in sample if nh[r] != ch[r]}
    assert all(d["NH"]["note"] == SECRET_NOTE for d in dis.values())


def test_while_the_sample_is_open_nobody_is_told_that_the_other_reviewer_changed_a_vote(lead):
    """The count of votes changed before the comparison is one of the round's results: until both have finished, it
    would tell a reviewer that the other had changed a vote."""
    c = lead
    folder = project(c, 30, calibration_size=0, ai_second_screener=False)
    c.post("/api/screening/start", {"set": "main"})
    sample = state(folder)["sample"]
    c.decide(sample[0], "ta", "exclude")
    c.be("NH")
    c.decide(sample[0], "ta", "exclude")
    c.be("CH")
    c.decide(sample[0], "ta", "include")                  # the lead changes a vote; NH has not finished
    for who in ("NH", "CH"):
        c.be(who)
        ag = c.get("/api/agreement", scope="sample")
        assert not ag["complete"] and "changed_before_compare" not in ag and ag["stats"] == {}, who
    for who in ("CH", "NH"):
        c.be(who)
        for r in sample[1:]:
            c.decide(r, "ta", "exclude")
    ag = c.get("/api/agreement", scope="sample")
    assert ag["complete"] and ag["changed_before_compare"] == 1


def test_a_slip_corrected_before_the_sample_closes_counts_as_corrected(lead):
    c = lead
    folder = project(c, 30, calibration_size=0, ai_second_screener=False)
    c.post("/api/screening/start", {"set": "main"})
    sample = state(folder)["sample"]
    screen_all(c, "CH", lambda r: "include" if r == sample[0] else "exclude", "sample")
    c.be("NH")
    c.decide(sample[0], "ta", "exclude")
    c.decide(sample[0], "ta", "include")                  # NH corrects a slip with the left arrow, before finishing
    for r in sample[1:]:
        c.decide(r, "ta", "exclude")
    ag = c.get("/api/agreement", scope="sample")
    assert ag["stats"]["agreement"] == 1.0 and ag["changed_before_compare"] == 1
    c.decide(sample[1], "ta", "include")                  # a change once both have finished is kept, but not for agreement
    ag = c.get("/api/agreement", scope="sample")
    assert ag["stats"]["agreement"] == 1.0
    assert c.overview()["sets"]["main"]["counts"]["forward"] == 2      # but it decides the record's outcome


# ------------------------------------------------------------------------------------------------ the kappa rule
def ai_answers(c, keep: set, maybe: set = frozenset()):
    """The AI stand-in keeps the records whose IDs are in keep (and answers Maybe for those in maybe)."""
    names = titles(c)
    by_title = {t: rid for rid, t in names.items()}

    def decide(call):
        rid = by_title.get(call.title)
        return "MAYBE" if rid in maybe else ("INCLUDE" if rid in keep else "EXCLUDE")
    c.elm.decide = decide


def test_kappa_at_the_threshold_allows_single_screening_with_the_ai_second_screener(lead):
    c = lead
    folder = project(c, 40, calibration_size=0)
    c.post("/api/screening/start", {"set": "main"})
    sv = state(folder)
    sample, rest = sv["sample"], [r for r in sv["order"] if r not in sv["sample"]]
    eligible = set(sample[:3]) | set(rest[:6])
    ai_keep = eligible | set(rest[6:9])                     # the AI also keeps 3 that are not eligible
    ai_answers(c, ai_keep)
    assert c.run_ai()["message"] == "Finished"
    assert c.elm.count() == 80 and len(c.elm.titles()) == 40
    vote = lambda r: "include" if r in eligible else "exclude"      # noqa: E731
    screen_all(c, "NH", vote, "sample")
    screen_all(c, "CH", vote, "sample")
    c.be("CH")
    prev = c.get("/api/agreement", scope="sample")["ai_preview"]
    assert prev["over"] is False and prev["keep_rate"] == pytest.approx(12 / 40)
    o = c.post("/api/screening/apply", {"set": "main"})["sets"]["main"]
    sr = o["sample_result"]
    assert o["remainder_mode"] == "single" and o["ai_net_active"] is True
    assert sr["kappa"] == 1.0 and sr["kappa_rule"] == "kappa reached the threshold, so single screening was allowed"
    assert sr["ai_rule_kind"] == "net" and sr["ai"]["keep_rate"] == pytest.approx(0.3)
    assert sr["ai"]["combined"]["sensitivity"] == 1.0
    assert c.get("/api/ai")["config"]["locked"] is True
    # NH screens the rest alone; CH has only the second looks
    c.be("NH")
    assert c.task("screen")["total"] == 32 and c.task("second") is None
    c.be("CH")
    assert c.task("screen") is None and c.task("second") is None
    nh_vote = {r: ("include" if r in rest[:4] else "exclude") for r in rest}       # NH misses two eligible ones
    screen_all(c, "NH", nh_vote.get, "screen")
    c.be("CH")
    second = [x["id"] for x in c.queue("second")]
    assert set(second) == {r for r in rest if nh_vote[r] == "exclude" and r in ai_keep}
    assert c.task("second")["label"] == "Second look (records the AI would keep)"
    # NH sees the records waiting for a second look as excluded: nothing shows what the AI said
    c.be("NH")
    blind = c.overview()["sets"]["main"]["counts"]
    c.be("CH")
    full = c.overview()["sets"]["main"]["counts"]
    assert full["pending"] == len(second) and blind.get("pending", 0) == 0
    assert blind["excluded"] == full["excluded"] + len(second)
    st, _, body = c.download("records")
    assert st == 200                                        # a second look is not blind by design
    screen_all(c, "CH", lambda r: "include" if r in eligible else "exclude", "second")
    o = c.overview()["sets"]["main"]
    assert o["counts"] == {"forward": 3 + 4 + 2, "excluded": 40 - 9}
    out = c.get("/api/prisma")
    assert out["counts"]["db"]["screened"] == 40 and out["counts"]["db"]["sought"] == 9
    assert re.search(r"Of the records the single reviewer excluded, \d+ (was|were) kept by the AI and screened again by the "
                     r"second reviewer", out["summary"])


def test_kappa_below_the_threshold_means_both_screen_everything(lead):
    c = lead
    folder = project(c, 40, calibration_size=0)
    c.post("/api/screening/start", {"set": "main"})
    sv = state(folder)
    sample = sv["sample"]
    ai_answers(c, set(sample[:3]))
    c.run_ai()
    screen_all(c, "NH", lambda r: "include" if r in sample[:3] else "exclude", "sample")
    screen_all(c, "CH", lambda r: "include" if r in sample[1:5] else "exclude", "sample")
    o = c.post("/api/screening/apply", {"set": "main"})["sets"]["main"]
    assert o["remainder_mode"] == "dual" and o["ai_net_active"] is False
    sr = o["sample_result"]
    assert sr["kappa"] < 0.8 and sr["kappa_rule"] == "kappa was below the threshold"
    assert sr["ai_rule_kind"] == "not_needed" and "ai" in sr          # its accuracy is still reported
    for who in ("NH", "CH"):
        c.be(who)
        assert c.task("screen")["total"] == 32 and c.task("second") is None
    assert "both reviewers screened every remaining record" in sr["remainder"]


def test_the_ai_stopping_limit_drops_the_ai_step_when_it_keeps_too_much(lead):
    c = lead
    folder = project(c, 40, calibration_size=0)
    c.post("/api/screening/start", {"set": "main"})
    sv = state(folder)
    order, sample = sv["order"], sv["sample"]
    ai_answers(c, set(order[:24]))                           # keeps 60%, over the 50% limit
    c.run_ai()
    vote = lambda r: "include" if r in sample[:3] else "exclude"     # noqa: E731
    screen_all(c, "NH", vote, "sample")
    screen_all(c, "CH", vote, "sample")
    assert c.get("/api/agreement", scope="sample")["ai_preview"] == {"keep_rate": pytest.approx(0.6), "limit": 0.5, "over": True}
    o = c.post("/api/screening/apply", {"set": "main"})["sets"]["main"]
    sr = o["sample_result"]
    assert sr["ai_rule_kind"] == "stopping_rule" and o["remainder_mode"] == "dual" and not o["ai_net_active"]
    assert "the AI kept 60.0% of records, above the 50% limit" in sr["ai_rule"]
    assert ("This keep rate fed the pre-specified stopping rule: the AI kept 60.0% of records, above the 50% limit, so the AI "
            "step was dropped") in c.get("/api/prisma")["summary"]


def test_the_ai_must_have_screened_every_record_before_the_rule_is_applied(lead):
    c = lead
    folder = project(c, 30, calibration_size=0)
    c.post("/api/screening/start", {"set": "main"})
    keep = set(state(folder)["sample"][::2])     # both kinds of vote in the sample, so kappa can be worked out (it is 1)
    for who in ("NH", "CH"):
        screen_all(c, who, lambda r: "include" if r in keep else "exclude", "sample")
    e = c.error("POST", "/api/screening/apply", {"set": "main"})
    assert "have not been sent to the AI yet" in e.message and "Run the AI screening to the end" in e.message
    o = c.post("/api/screening/apply", {"set": "main", "drop_ai": "the University service was withdrawn"})["sets"]["main"]
    assert o["sample_result"]["ai_rule_kind"] == "dropped" and o["remainder_mode"] == "dual"
    assert "Deviation from the protocol: the AI step could not be completed (the University service was withdrawn)" in c.get("/api/prisma")["summary"]


def test_the_ais_answers_are_shown_only_to_the_lead_and_only_after_the_sample(lead):
    c = lead
    folder = project(c, 40, calibration_size=0)
    c.post("/api/screening/start", {"set": "main"})
    sv = state(folder)
    sample = sv["sample"]
    names = titles(c)
    c.settings(known_studies=[{"title": names[sample[0]], "doi": "", "pmid": ""},          # one in the sample
                              {"title": names[sv["order"][-1]], "doi": "", "pmid": ""}])   # one outside it
    ai_answers(c, set(sample[:3]) | set(sv["order"][10:14]))
    c.run_ai()
    # during the sample: hidden from everyone, except that the lead may check a known study outside the sample
    for who in ("CH", "NH"):
        c.be(who)
        a = c.get("/api/ai")
        assert a["visible"] is False and "validation" not in a
        inside, outside = a["known"]
        assert inside.get("A") is None and inside["hidden"] is True and inside["hidden_why"] == "sample"
        assert (outside.get("A") == "EXCLUDE") is (who == "CH")
        st, _, body = c.download("ai")
        assert st == 403
    screen_all(c, "NH", lambda r: "include" if r in sample[:3] else "exclude", "sample")
    screen_all(c, "CH", lambda r: "include" if r in sample[:3] else "exclude", "sample")
    c.be("CH")
    a = c.get("/api/ai")
    assert a["visible"] is True and a["validation"]["sample_records"] == 8 and a["known"][0]["A"] in ("INCLUDE", "EXCLUDE")
    st, _, body = c.download("ai")
    assert st == 200 and b"Pass A" in body
    c.post("/api/screening/apply", {"set": "main"})
    # the reviewer never sees them
    c.be("NH")
    a = c.get("/api/ai")
    assert a["visible"] is False and "validation" not in a and a["known"][0]["hidden"] is True
    st, _, body = c.download("ai")
    assert st == 403 and "Only the project lead can download the AI's answers" in json.loads(body)["error"]
    assert c.get("/api/prisma")["downloads"]["ai"] == "Only the project lead can download the AI's answers."
    o = c.overview()["sets"]["main"]
    assert "ai" not in o["sample_result"] and o["ai_net_active"]
    pr = c.get("/api/prisma")
    assert "sensitivity" not in pr["summary"] and "(How many answers came back, and the AI's accuracy figures, are shown to the project lead.)" in pr["summary"]
    assert "% of records, within" not in pr["summary"]                     # nor the AI's keep rate
    for rid in sample:
        st, _, body = c.request("GET", "/api/record?id=" + rid)
        assert b"INCLUDE" not in body and b"EXCLUDE" not in body
    c.be("CH")
    assert "sensitivity was 100.0%" in c.get("/api/prisma")["summary"]


# ------------------------------------------------------------------------------------------------ disagreements
def test_a_record_either_reviewer_sends_on_goes_to_full_text_under_the_forward_rule(lead):
    c = lead
    project(c, 12, mode="dual", calibration_size=0, ai_second_screener=False, conflicts_at_title_abstract="forward")
    c.post("/api/screening/start", {"set": "main"})
    order = [x["id"] for x in c.queue("screen")]
    screen_all(c, "NH", lambda r: "include" if r in order[:3] else "exclude", "screen")
    c.be("CH")
    assert c.overview()["sets"]["main"]["counts"] == {"pending": 12}        # NH's votes wait for CH's
    screen_all(c, "CH", lambda r: "maybe" if r in order[2:5] else "exclude", "screen")
    o = c.overview()
    assert o["sets"]["main"]["counts"] == {"forward": 5, "excluded": 7} and o["conflicts"] == 0
    assert c.get("/api/conflicts")["conflicts"] == []
    assert len(c.get("/api/fulltext")["rows"]) == 5


def test_disagreements_are_decided_together_under_the_discuss_rule(lead):
    c = lead
    project(c, 12, mode="dual", calibration_size=0, ai_second_screener=False, conflicts_at_title_abstract="discuss")
    c.post("/api/screening/start", {"set": "main"})
    order = [x["id"] for x in c.queue("screen")]
    screen_all(c, "NH", lambda r: "include" if r in order[:3] else "exclude", "screen")
    assert c.get("/api/conflicts")["conflicts"] == []                       # nothing to discuss before CH has voted
    screen_all(c, "CH", lambda r: "include" if r in order[2:5] else "exclude", "screen")
    conf = c.get("/api/conflicts")
    ids = {x["id"] for x in conf["conflicts"]}
    assert ids == {order[0], order[1], order[3], order[4]} and conf["names"] == {"CH": "Dr Colin Hawthorne", "NH": "Nadine Hayes"}
    x = next(x for x in conf["conflicts"] if x["id"] == order[0])
    assert x["votes"]["NH"]["d"] == "include" and x["votes"]["CH"]["d"] == "exclude" and x["stage"] == "ta"
    assert x["abstract"] and x["title"]
    c.be("NH")
    e = c.error("POST", "/api/resolve", {"record": order[0], "stage": "ta", "decision": "include", "as": "NH"})
    assert e.status == 403 and "Only the project lead" in e.message
    c.be("CH")
    c.resolve(order[0], "ta", "include", note="agreed at the meeting")
    c.resolve(order[1], "ta", "exclude")
    c.resolve(order[3], "ta", "maybe")
    o = c.overview()
    assert o["conflicts"] == 1 and o["sets"]["main"]["counts"] == {"forward": 3, "excluded": 8, "conflict": 1}
    assert c.get("/api/prisma")["counts"]["db"]["pending_ta"] == 1
    st, _, body = c.download("decisions")
    assert st == 200 and "agreed at the meeting" in body.decode("utf-8-sig") and "resolution" in body.decode("utf-8-sig")
    summary = c.get("/api/prisma")["summary"]
    assert "the project lead recorded the decision, and the record went forward to full text if that decision sent it on" in summary
    assert "[Authors to complete: how disagreements at title and abstract were resolved" in summary
    assert "they decided together" not in summary


def test_a_second_look_is_not_a_disagreement_under_the_discuss_rule(lead):
    c = lead
    folder = project(c, 30, calibration_size=0, conflicts_at_title_abstract="discuss")
    c.post("/api/screening/start", {"set": "main"})
    sv = state(folder)
    rest = [r for r in sv["order"] if r not in sv["sample"]]
    ai_answers(c, set(sv["sample"][:2]) | {rest[0]})
    c.run_ai()
    for who in ("NH", "CH"):
        screen_all(c, who, lambda r: "include" if r in sv["sample"][:2] else "exclude", "sample")
    c.post("/api/screening/apply", {"set": "main"})
    screen_all(c, "NH", lambda r: "exclude", "screen")
    screen_all(c, "CH", lambda r: "include", "second")
    assert c.get("/api/conflicts")["conflicts"] == []
    assert c.overview()["sets"]["main"]["counts"]["forward"] == 3


# ------------------------------------------------------------------------------------------------ safeguards
def test_a_vote_from_a_page_opened_as_someone_else_is_not_saved(lead):
    c = lead
    project(c, 6, mode="dual", calibration_size=0, ai_second_screener=False)
    c.post("/api/screening/start", {"set": "main"})
    rid = c.queue("screen")[0]["id"]
    c.be("NH")
    e = c.error("POST", "/api/decide", {"record": rid, "stage": "ta", "decision": "include", "as": "CH"})
    assert e.status == 409 and "This page was opened as CH, but ReviewDesk is now set to NH" in e.message
    key = c.project_key
    c.project_key = key[:-1] + ("0" if key[-1] != "0" else "1")
    e = c.error("POST", "/api/decide", {"record": rid, "stage": "ta", "decision": "include", "as": "NH"})
    assert e.status == 409 and "belongs to another project" in e.message
    c.project_key = key
    for body, words in (({"record": rid, "stage": "ta", "decision": "yes"}, "Choose Include, Maybe or Exclude."),
                        ({"record": rid, "stage": "xx", "decision": "include"}, "Unknown stage."),
                        ({"record": "R99999", "stage": "ta", "decision": "include"}, "That record is not in the project.")):
        e = c.error("POST", "/api/decide", {**body, "as": "NH"})
        assert words in e.message
    assert c.get("/api/record", id=rid)["mine"]["ta"] is None


def test_the_criteria_can_change_after_screening_starts_but_the_change_is_recorded(lead):
    c = lead
    project(c, 6, mode="dual", calibration_size=0, ai_second_screener=False)
    c.post("/api/screening/start", {"set": "main"})
    r = c.settings(criteria={"include": ["Adults only"], "exclude": [], "rules": []})["result"]
    assert r["changed_after_start"] == ["criteria"]
    am = c.summary()["settings"]["amendments"]
    assert am[-1]["what"] == "criteria" and am[-1]["by"] == "CH"
    assert "Changes after screening started" in c.get("/api/prisma")["summary"]


def test_only_the_lead_starts_screening_and_applies_the_rule(lead):
    c = lead
    project(c, 10, calibration_size=0, ai_second_screener=False)
    c.be("NH")
    e = c.error("POST", "/api/screening/start", {"set": "main"})
    assert e.status == 403 and "Only the project lead (Dr Colin Hawthorne) can do this" in e.message
    with pytest.raises(ApiError):
        c.post("/api/screening/apply", {"set": "main"})
