"""What an evaluation of the screening needs (from 1.3.1), through the API: the verification sample (a second person
screens, blind, a random sample of the records that left the review on one reviewer's vote, mixed with some that
reviewer sent on; whatever they would send on goes to full text), the time each vote took (only when the lead has
switched recording on and the voter has not left their own votes out), and the lead's evaluation download.
No real AI service or key is used."""
from __future__ import annotations

import io
import json
import os
import re
import zipfile

import pytest

from helpers import read_csv, read_json, reference_plan, reference_project, ris, screen_all, tiny_records, write_json
from mock_elm import TEST_KEY
from reviewdesk import evaluation, screening, server, stats, timing, versions
from reviewdesk.project import MARKER, Project, ProjectError

TEAM = [{"id": "CH", "name": "Dr Colin Hawthorne", "role": "lead"}, {"id": "NH", "name": "Nadine Hayes", "role": "reviewer"},
        {"id": "TK", "name": "Tane Kereama", "role": "reviewer"}]


def state(folder: str) -> dict:
    return read_json(os.path.join(folder, "state", "state.json"))["sets"]["main"]


@pytest.fixture
def lead(desk, elm, tmp_path):
    """CH at the lead's computer, the AI service set to the stand-in."""
    c = desk.client()
    c.post("/api/user", {"elm_url": elm.url, "elm_key": TEST_KEY})
    c.tmp, c.elm = tmp_path, elm
    return c


@pytest.fixture
def needing_less(monkeypatch):
    """As if this version did not itself need 1.3.1. (It does: it writes every fingerprint whole, which 1.3.0 cannot
    check; tests/test_fingerprints.py.) What the lead switches on can then be seen to raise what a project needs,
    which is how the next thing an older version cannot read will have to work."""
    monkeypatch.setattr(versions, "NEEDS", "1.3.0")


def titles(c) -> dict:
    return {r["id"]: r["title"] for r in c.get("/api/records", limit=1000)["records"]}


def single_screened(c, n: int = 60, finish: bool = True, ai: bool = True, **plan) -> dict:
    """A review of n records taken to the point where NH has screened everything outside the agreement sample alone
    (with the AI behind, unless ai=False). Returns what the tests need to know about it. In it:
      eligible   records that should go on: 3 in the sample, 8 outside it
      NH         sends on 5 of the 8 and wrongly excludes 3: one the AI keeps (so it gets a second look) and two the AI
                 would not keep either (only a verification sample can find those)
      the AI     keeps the 5 NH sent on, the one NH missed, and 3 that are not eligible"""
    folder = reference_project(c, str(c.tmp / "OneDrive"), reviewers=TEAM,
                               screening=reference_plan(calibration_size=0, ai_second_screener=ai, **plan))
    c.records = tiny_records(n, seed=31)
    c.add_import(ris(*c.records), "medline.ris", "MEDLINE")
    c.process()
    c.post("/api/screening/start", {"set": "main"})
    sv = state(folder)
    sample, rest = sv["sample"], [r for r in sv["order"] if r not in sv["sample"]]
    eligible = set(sample[:3]) | set(rest[:8])
    nh_yes = set(rest[:5])
    missed_kept, missed_lost = rest[5], set(rest[6:8])
    ai_keep = set(sample[:3]) | nh_yes | {missed_kept} | set(rest[8:11])
    if ai:
        by_title = {t: rid for rid, t in titles(c).items()}
        c.elm.decide = lambda call: "INCLUDE" if by_title.get(call.title) in ai_keep else "EXCLUDE"
        assert c.run_ai()["message"] == "Finished"
    vote = lambda r: "include" if r in eligible else "exclude"      # noqa: E731
    screen_all(c, "NH", vote, "sample")
    screen_all(c, "CH", vote, "sample")
    c.be("CH")
    o = c.post("/api/screening/apply", {"set": "main"})["sets"]["main"]
    assert o["remainder_mode"] == "single" and o["ai_net_active"] is ai
    nh_vote = {r: ("include" if r in nh_yes else "exclude") for r in rest}
    out = {"folder": folder, "sample": sample, "rest": rest, "eligible": eligible, "nh_yes": nh_yes, "nh_vote": nh_vote,
           "missed_kept": missed_kept, "missed_lost": missed_lost, "ai_keep": ai_keep if ai else set()}
    if finish:
        screen_all(c, "NH", nh_vote.get, "screen")
        c.be("CH")
    return out


def verify_of(c) -> dict:
    return c.overview()["sets"]["main"]["verify"]


def forget_the_draw(folder: str) -> None:
    """The lead's audit log loses what it says about the sample (as if that file had been lost or put back as well):
    the state is then the only place that holds it."""
    d = os.path.join(folder, "audit")
    for n in os.listdir(d):
        if n.startswith("CH@"):
            with open(os.path.join(d, n), encoding="utf-8") as fh:
                rows = [json.loads(x) for x in fh.read().splitlines() if x.strip()]
            with open(os.path.join(d, n), "w", encoding="utf-8") as fh:
                fh.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows
                                 if r.get("action") not in (evaluation.DRAW, evaluation.END)))


# ------------------------------------------------------------------------------------------------ the draw
def test_the_sample_is_drawn_from_the_records_one_reviewer_and_the_ai_both_let_go(lead):
    c = lead
    R = single_screened(c)
    rest = R["rest"]
    v = verify_of(c)
    alone = [r for r in rest if R["nh_vote"][r] == "exclude" and r not in R["ai_keep"]]
    assert v["drawn"] is False and v["why_not"] == "" and v["population"] == len(alone) == 48 - 5 - 4
    assert v["candidates"] == ["CH", "TK"] and v["ai_net"] is True and v["lost_votes"] == 0
    assert v["decoy_pool"] == 5 and v["decoy_pools"] == {"CH": 5, "TK": 5}
    # the form proposes the one person there is no caution about: CH assesses full texts and is the lead
    assert v["reviewer"] == "TK" and set(v["cautions"]) == {"CH"}
    assert v["cautions"]["CH"] == ("Dr Colin Hawthorne assesses full texts, so may already have seen the records mixed in on "
                                   "the full-text list and is the project lead, who can look up what was decided on any record.")
    out = c.post("/api/screening/verify", {"set": "main", "size": 12, "decoys": 3, "reviewer": "TK", "as": "CH"})["verify"]
    assert out["drawn"] is True and out["reviewer"] == "TK" and out["total"] == 15 and out["done"] == 0
    assert out["size"] == 12 and out["decoys"] == 3 and out["population"] == 39 and "results" not in out
    vf = state(R["folder"])["verify"]
    assert set(vf["sample"]) <= set(alone) and len(set(vf["sample"])) == 12
    assert set(vf["decoy"]) <= R["nh_yes"] and len(set(vf["decoy"])) == 3
    assert sorted(vf["order"]) == sorted(vf["sample"] + vf["decoy"]) and vf["order"] != sorted(vf["order"])
    assert vf["seed"] == state(R["folder"])["seed"] + evaluation.VERIFY_SEED_STEP and vf["single_reviewer"] == "NH"
    # the same seed and records give the same draw (it can be checked by anyone, and cannot be repeated for another)
    import random
    rng = random.Random(vf["seed"])
    a, b = sorted(alone), sorted(R["nh_yes"])
    rng.shuffle(a)
    rng.shuffle(b)
    assert vf["sample"] == a[:12] and vf["decoy"] == b[:3]
    order = sorted(a[:12] + b[:3])
    rng.shuffle(order)
    assert vf["order"] == order
    log = [e for e in c.get("/api/audit")["entries"] if e["action"] == evaluation.DRAW]
    assert len(log) == 1 and log[0]["details"] == {"set": "main", "sample": 12, "of": 39, "mixed in": 3, "screened by": "TK",
                                                   "seed": vf["seed"]}
    e = c.error("POST", "/api/screening/verify", {"set": "main", "size": 5, "decoys": 0})
    assert e.status == 400 and "has been drawn (it is drawn once)" in e.message
    assert state(R["folder"])["verify"] == vf


def test_it_cannot_be_drawn_before_the_single_reviewer_has_finished_or_when_both_screen_everything(lead):
    c = lead
    R = single_screened(c, finish=False)
    v = verify_of(c)
    assert v["drawn"] is False and "Nadine Hayes still has 48 records to screen" in v["why_not"] and "population" not in v
    e = c.error("POST", "/api/screening/verify", {"set": "main", "size": 5})
    assert e.status == 400 and "can be drawn when they have finished" in e.message
    c.be("NH")
    for it in c.queue("screen")[:-1]:
        c.decide(it["id"], "ta", R["nh_vote"][it["id"]])
    c.be("CH")
    assert "still has 1 record to screen" in verify_of(c)["why_not"]
    assert "verify" not in state(R["folder"])


def test_the_lead_chooses_the_numbers_within_what_there_is(lead):
    c = lead
    R = single_screened(c)
    for body, words in (({"size": 0}, "between 1 and 39 records"), ({"size": 40}, "39 checks every one"),
                        ({"size": 5, "decoys": 6}, "between 0 and 5"), ({"size": 5, "decoys": -1}, "between 0 and 5"),
                        ({"size": 5, "reviewer": "NH"}, "someone in the project other than Nadine Hayes"),
                        ({"size": 5, "reviewer": "ZZ"}, "someone in the project other than Nadine Hayes")):
        e = c.error("POST", "/api/screening/verify", {"set": "main", **body})
        assert e.status == 400 and words in e.message, body
    for body in ({"size": "5"}, {"size": 2.5}, {"size": 5, "decoys": "1"}, {"size": 5, "reviewer": 3}, {"set": "all", "size": 5}):
        e = c.error("POST", "/api/screening/verify", {"set": "main", **body})
        assert e.status == 400 and "could not read that request" in e.message, body
    assert "verify" not in state(R["folder"])
    c.be("NH")
    e = c.error("POST", "/api/screening/verify", {"set": "main", "size": 5})
    assert e.status == 403 and "Only the project lead" in e.message
    c.be("CH")
    out = c.post("/api/screening/verify", {"set": "main", "size": 39})["verify"]       # every one of them, no records mixed in
    assert out["total"] == 39 and out["reviewer"] == "TK" and out["decoys"] == 0


def test_without_the_ai_the_sample_is_drawn_from_everything_the_single_reviewer_excluded(lead):
    c = lead
    R = single_screened(c, ai=False)
    v = verify_of(c)
    assert v["population"] == 48 - 5 and v["ai_net"] is False
    c.post("/api/screening/verify", {"set": "main", "size": 43, "decoys": 5, "reviewer": "TK"})
    screen_all(c, "TK", lambda r: "include" if r in R["eligible"] else "exclude", "verify", stage="vs")
    c.be("CH")
    res = verify_of(c)["results"]
    assert res["sent_on"]["k"] == 3 and res["sent_on"]["n"] == 43 and res["not_sampled"] == 0
    assert res["decoys"] == {"n": 5, "sent_on": 5}
    assert c.overview()["sets"]["main"]["counts"]["forward"] == 3 + 5 + 3
    text = c.get("/api/prisma")["summary"]
    assert "all 43 records excluded on Nadine Hayes's vote alone, mixed with 5 records" in text
    assert "Every such record was checked" not in text          # (three of them are still to be decided at full text)


# ------------------------------------------------------------------------------------------------ the verifier's task
def drawn(c, size=20, decoys=4, reviewer="TK", **kw) -> dict:
    R = single_screened(c, **kw)
    c.post("/api/screening/verify", {"set": "main", "size": size, "decoys": decoys, "reviewer": reviewer})
    R["vf"] = state(R["folder"])["verify"]
    return R


def test_only_the_verifier_has_the_task_and_nothing_shows_what_was_decided_before(lead):
    c = lead
    R = drawn(c)
    vf = R["vf"]
    for who in ("CH", "NH"):
        c.be(who)
        assert c.task("verify") is None and c.queue("verify") == []
        e = c.error("POST", "/api/decide", {"record": vf["order"][0], "stage": "vs", "decision": "include", "as": who})
        assert e.status == 400 and "not in a verification sample of yours" in e.message
    c.be("TK")
    t = c.task("verify")
    assert t["label"] == "Verification sample" and t["total"] == 24 and t["left"] == 24
    assert [x["id"] for x in c.queue("verify")] == vf["order"] and not any(x["done"] for x in c.queue("verify"))
    assert c.task("screen") is None and c.task("second") is None
    for rid in vf["order"]:
        st, _, body = c.request("GET", "/api/record?id=" + rid)
        rec = json.loads(body)
        assert st == 200 and rec["mine"] == {"cal": None, "ta": None, "ft": None, "vs": None}
        assert b'"d":' not in body and b"NH" not in body      # nobody's vote, and no sign of who screened it
    o = c.overview()
    assert o["sets"]["main"]["verify"] == {"drawn": True, "drawn_at": vf["drawn"], "reviewer": "TK", "single_reviewer": "NH",
                                           "total": 24, "done": 0, "complete": False, "ended": False}
    assert o["verifying"] == {"set": "main", "left": 24, "total": 24}
    # a record that is not in the sample cannot be voted on as if it were
    other = next(r for r in R["rest"] if r not in vf["order"])
    e = c.error("POST", "/api/decide", {"record": other, "stage": "vs", "decision": "include", "as": "TK"})
    assert e.status == 400 and "not in a verification sample of yours" in e.message
    e = c.error("POST", "/api/decide", {"record": vf["order"][0], "stage": "vs", "decision": "awaiting", "as": "TK"})
    assert e.status == 400 and "Choose Include, Maybe or Exclude" in e.message
    for kind in ("records", "decisions", "evaluation", "ai"):
        assert c.download(kind)[0] == 403


def test_a_sampled_record_the_verifier_would_send_on_goes_to_full_text_and_nothing_else_changes(lead):
    c = lead
    R = drawn(c, size=39, decoys=5)
    vf = R["vf"]
    before = c.overview()["sets"]["main"]["counts"]
    p = Project(R["folder"])
    ag0 = screening.agreement(p, "main", "sample")["stats"]
    lost = sorted(R["missed_lost"])
    assert set(lost) <= set(vf["sample"])
    c.be("TK")
    rest_first = [r for r in vf["order"] if r not in lost] + lost         # the two that matter, and one more, come last
    for k, rid in enumerate(rest_first):
        d = "maybe" if rid == lost[0] else "include" if rid == lost[1] else "exclude"      # TK even excludes the mixed-in ones
        c.decide(rid, "vs", d)
        if k == len(rest_first) - 2:
            # one sampled record already voted Maybe, one record still to do: nothing has moved, for anyone
            for who in ("TK", "NH", "CH"):
                c.be(who)
                assert c.overview()["sets"]["main"]["counts"] == (before if who == "CH" else c.overview()["sets"]["main"]["counts"])
                assert c.get("/api/prisma")["counts"]["db"]["sought"] == before["forward"]
            assert screening.ta_outcomes(Project(R["folder"]), "main")[lost[0]]["status"] == "excluded"
            c.be("TK")
    assert c.task("verify") is None and c.queue("verify") == []      # finished: nothing is left to do, or to change
    c.be("CH")
    after = c.overview()["sets"]["main"]["counts"]
    assert after["forward"] == before["forward"] + 2 and after["excluded"] == before["excluded"] - 2
    p = Project(R["folder"])
    o = screening.ta_outcomes(p, "main")
    for rid in lost:
        assert o[rid] == {"status": "forward", "how": screening.VERIFIED_ON, "required": ["NH"]}
    for rid in vf["decoy"]:                                  # sent on by NH, "excluded" by TK: still on their way
        assert o[rid]["status"] == "forward" and o[rid]["how"] == "both"
    assert screening.agreement(p, "main", "sample")["stats"] == ag0
    assert screening.half_voted(p) == 0 and not screening.conflicts(p)
    assert set(lost) <= set(screening.ft_outcomes(p))
    # the sample is finished, so its verifier can now see what was decided before: its votes are fixed
    c.be("TK")
    e = c.error("POST", "/api/decide", {"record": lost[0], "stage": "vs", "decision": "exclude", "as": "TK"})
    assert "This verification sample is finished, so nothing was saved" in str(e)
    assert screening.ta_outcomes(Project(R["folder"]), "main")[lost[0]]["status"] == "forward"
    # NH's own counts do not show which of their exclusions these were, only that two more records went on
    c.be("NH")
    assert c.overview()["sets"]["main"]["counts"]["forward"] == after["forward"]
    assert "results" not in c.overview()["sets"]["main"]["verify"]


def test_the_figures_go_to_the_lead_alone_and_only_when_the_verifier_has_finished(lead):
    c = lead
    R = drawn(c, size=20, decoys=4)
    vf = R["vf"]
    lost_in = [r for r in vf["sample"] if r in R["missed_lost"]]
    c.be("TK")
    for rid in vf["order"][:-1]:
        c.decide(rid, "vs", "include" if rid in R["eligible"] else "exclude")
    for who in ("CH", "NH", "TK"):
        c.be(who)
        v = verify_of(c)
        assert v["complete"] is False and "results" not in v
        assert (v.get("done"), v.get("total")) == ((None, None) if who == "NH" else (23, 24))     # NH: not even how many
    c.be("CH")
    assert ("So far Tane Kereama has screened 23 of the 24; nothing is sent on until the sample is finished."
            in c.get("/api/prisma")["summary"])
    for kind in ("decisions", "evaluation", "evaluation_share"):          # not the lead either, until it is finished
        st, _, body = c.download(kind)
        assert st == 403 and b"still being screened (23 of 24 done)" in body, kind
    assert c.download("records")[0] == 200                   # (nothing in it has changed, or will until the sample is finished)
    c.be("NH")
    assert "The sample is still being screened." in c.get("/api/prisma")["summary"]
    c.be("TK")
    last = vf["order"][-1]
    c.decide(last, "vs", "include" if last in R["eligible"] else "exclude")
    for who in ("NH", "TK"):
        c.be(who)
        v = verify_of(c)
        assert v["complete"] is True and "results" not in v and "population" not in v and "size" not in v
        text = c.get("/api/prisma")["summary"]
        assert "(The verification sample's figures are shown to the project lead.)" in text
        # no number in it: how many records left on NH's vote alone would tell NH how many of her exclusions the AI kept
        para = next(x for x in text.split("\n") if x.startswith("To check the records that left the review"))
        assert not re.search(r"\d", re.sub(r"drawn on \d+ \w+ \d{4}", "drawn on a day", para)), para
        assert "a random sample of the records excluded by Nadine Hayes and not kept by the AI second screener, mixed with records" in para
        log = [e for e in c.get("/api/audit")["entries"] if e["action"] == evaluation.DRAW] if who == "NH" else []
        assert who == "TK" or log[0]["details"] == {"set": "main", "screened by": "TK"}
    c.be("CH")
    res = verify_of(c)["results"]
    k = len(lost_in)
    lo, hi = stats.wilson(k, 20)
    assert res["population"] == 39 and res["not_sampled"] == 19 and res["complete"] is True
    assert res["sent_on"]["k"] == k and res["sent_on"]["n"] == 20 and res["sent_on"]["rate"] == pytest.approx(k / 20)
    assert res["sent_on"]["ci"] == pytest.approx([lo, hi])
    assert res["sent_on"]["projected"] == pytest.approx({"records": 19 * k / 20, "low": 19 * lo, "high": 19 * hi})
    assert res["decoys"] == {"n": 4, "sent_on": 4} and sorted(res["sent_on_records"]) == sorted(lost_in)
    assert res["full_text"]["pending"] == k and ("included" in res) == (k == 0)
    text = c.get("/api/prisma")["summary"]
    assert (f"20 records taken at random (seed {vf['seed']}) from the 39 excluded by Nadine Hayes and not kept by the AI second "
            "screener, mixed with 4 records taken at random from those Nadine Hayes had sent on") in text
    assert f"Tane Kereama would have sent on {k} of the 20 (" in text and "and 4 of the 4 records Nadine Hayes had sent on." in text


def test_missed_studies_are_estimated_once_full_text_has_decided(lead):
    c = lead
    R = drawn(c, size=39, decoys=0, reviewer="TK")
    c.settings(fulltext={"mode": "dual"})
    screen_all(c, "TK", lambda r: "include" if r in R["missed_lost"] else "exclude", "verify", stage="vs")
    c.be("CH")
    res = verify_of(c)["results"]
    assert res["sent_on"]["k"] == 2 and res["full_text"]["pending"] == 2 and "included" not in res
    lost = sorted(R["missed_lost"])
    for who in ("NH", "CH"):
        c.be(who)
        c.decide(lost[0], "ft", "include")
        c.decide(lost[1], "ft", "exclude", reason="Wrong population")
    c.be("CH")
    res = verify_of(c)["results"]
    assert res["full_text"] == {"included": 1, "excluded": 1, "pending": 0, "conflict": 0, "other": 0}
    assert res["included"]["k"] == 1 and res["included"]["n"] == 39 and res["not_sampled"] == 0
    text = c.get("/api/prisma")["summary"]
    assert "At full text, of those 2 records, 1 was included and 1 excluded." in text
    assert "Every such record was checked, so nothing is left to estimate." in text


def test_a_projection_is_given_for_the_records_not_sampled(lead):
    c = lead
    drawn(c, size=30, decoys=0)
    screen_all(c, "TK", lambda r: "exclude", "verify", stage="vs")
    c.be("CH")
    res = verify_of(c)["results"]
    lo, hi = stats.wilson(0, 30)
    assert res["sent_on"]["k"] == 0 and res["included"]["k"] == 0 and res["included"]["projected"]["high"] == pytest.approx(9 * hi)
    text = c.get("/api/prisma")["summary"]
    assert "Tane Kereama would have sent on 0 of the 30 (0.0%; 95% confidence interval 0.0% to " in text
    assert f"Among the 9 such records not checked, that suggests about 0 a second reviewer would have sent on (95% confidence interval 0 to {round(9 * hi)})" in text


# ------------------------------------------------------------------------------------------------ versions
def test_drawing_a_sample_makes_the_project_need_this_version(needing_less, lead):
    c = lead
    R = single_screened(c)
    assert read_json(os.path.join(R["folder"], MARKER))["min_version"] == "1.3.0"
    assert all(e["needs"] == "1.3.0" for e in c.get("/api/audit")["entries"])
    c.post("/api/screening/verify", {"set": "main", "size": 10})
    assert read_json(os.path.join(R["folder"], MARKER))["min_version"] == "1.3.1"
    log = c.get("/api/audit")["entries"]
    assert log[-1]["action"] == evaluation.DRAW and log[-1]["needs"] == "1.3.1"
    p = Project(R["folder"])
    assert versions.needed(p)["version"] == "1.3.1" and versions.must_upgrade(p, "NH") == ""


def test_an_older_reviewdesk_stops_saving_once_a_sample_is_drawn_or_times_are_recorded(lead, monkeypatch):
    c = lead
    R = single_screened(c)
    c.settings(screening={"record_time": True})
    assert read_json(os.path.join(R["folder"], MARKER))["min_version"] == "1.3.1"
    assert c.get("/api/audit")["entries"][-1]["needs"] == "1.3.1"
    p = Project(R["folder"])                                 # a ReviewDesk 1.3.0 that already has the project open ...
    monkeypatch.setattr(versions, "VERSION", "1.3.0")
    stop = versions.must_upgrade(p, "NH")
    assert "This project now needs ReviewDesk 1.3.1 or later, and this is ReviewDesk 1.3.0." in stop
    with pytest.raises(ProjectError, match="This project needs ReviewDesk 1.3.1 or later"):     # ... and one that opens it
        Project(R["folder"])


def test_a_teammate_on_the_version_before_is_told_what_is_true_of_it(lead):
    c = lead
    R = single_screened(c)
    aud = os.path.join(R["folder"], "audit")
    for f in os.listdir(aud):                               # NH did all her work with ReviewDesk 1.3.0
        if f.startswith("NH@"):
            rows = [json.loads(x) for x in open(os.path.join(aud, f), encoding="utf-8") if x.strip()]
            with open(os.path.join(aud, f), "w", encoding="utf-8", newline="\n") as out:
                out.writelines(json.dumps({**r, "version": "1.3.0"}, ensure_ascii=False) + "\n" for r in rows)
    w = versions.warnings(Project(R["folder"]), "CH")
    assert len(w) == 1 and w[0].startswith("Nadine Hayes last worked on this project with ReviewDesk 1.3.0 (on ")
    # 1.3.0 cannot check the whole fingerprints this version writes, and knows to stop saving: neither "nothing is at
    # risk" nor what is said of the versions before it, which do not check and show "Something went wrong"
    assert "Everyone in the team must use ReviewDesk 1.3.1 or later" in w[0] and "Something went wrong" not in w[0]
    assert "it records things their version cannot read or check. Their version stops saving in this project once" in w[0]
    assert "nothing is at risk" not in w[0]
    c.post("/api/screening/verify", {"set": "main", "size": 10, "reviewer": "TK"})
    assert versions.warnings(Project(R["folder"]), "CH") == w               # (a sample changes nothing in that)
    # and whoever screens the sample cannot be taken out of the project
    e = c.error("POST", "/api/settings", {"changes": {"reviewers": TEAM[:2]}})
    assert e.status == 400 and "cannot be removed once screening has started" in e.message


def test_a_teammate_on_an_older_version_that_reads_everything_is_only_asked_to_move(lead, monkeypatch):
    c = lead
    R = single_screened(c)
    monkeypatch.setattr(versions, "VERSION", "1.3.2")       # the next version, if it changes nothing an older one must read:
    w = versions.warnings(Project(R["folder"]), "CH")       # everyone last worked with 1.3.1
    assert w and all("can still read everything in this project, so nothing is at risk" in x for x in w)
    assert all("stops saving" not in x and "Something went wrong" not in x for x in w)


def test_the_data_checks_find_nothing_wrong_with_a_sample_its_votes_or_their_times(lead):
    c = lead
    R = drawn(c, size=10, decoys=2)
    c.settings(screening={"record_time": True})
    c.be("TK")
    for rid in R["vf"]["order"]:
        vote_timed(c, rid, stage="vs", decision="exclude", shown=3000, active=2500, quiet=800)
    c.be("CH")
    assert c.get("/api/integrity")["report"]["problems"] == [] and c.summary()["data_problems"] == 0
    st, _, body = c.download("decisions")
    rows = read_csv(body)
    assert sum(1 for r in rows if r[5] == "verification sample") == 12 and st == 200
    ready = c.get("/api/readiness")
    assert "verification" not in json.dumps(ready)          # finished: nothing left to say about it


def test_an_unfinished_sample_is_among_the_things_still_open(lead):
    c = lead
    R = drawn(c, size=10, decoys=2)
    c.be("TK")
    c.decide(R["vf"]["order"][0], "vs", "exclude")
    mine = json.dumps(c.get("/api/readiness"))
    assert "You have 11 of 12 records left in the verification sample." in mine
    assert "The verification sample is not finished (you have screened 1 of its 12 records)" in mine
    c.be("NH")
    theirs = json.dumps(c.get("/api/readiness"))
    assert "The verification sample is not finished (Tane Kereama is still screening it)" in theirs
    c.be("CH")
    assert "The verification sample is not finished (Tane Kereama has screened 1 of its 12 records)" in json.dumps(c.get("/api/readiness"))
    assert "left in the verification sample" not in theirs


# ------------------------------------------------------------------------------------------------ time recording
def decisions(folder: str, who: str) -> list:
    out = []
    for f in sorted(os.listdir(os.path.join(folder, "decisions"))):
        if f.startswith(who + "@"):
            out += [json.loads(x) for x in open(os.path.join(folder, "decisions", f), encoding="utf-8") if x.strip()]
    return out


def vote_timed(c, rid, stage="ta", decision="exclude", **time):
    return c.post("/api/decide", {"record": rid, "stage": stage, "decision": decision, "as": c.me, "time": time,
                                  **({"finish": True} if stage == "vs" else {})})


def test_times_are_saved_only_while_the_lead_has_recording_on(lead):
    c = lead
    R = single_screened(c, finish=False)
    rest = R["rest"]
    assert c.overview()["timing"] == {"project": False, "mine": False}
    c.be("NH")
    vote_timed(c, rest[0], shown=9000, active=8000, quiet=3000, views=1)
    assert "time" not in decisions(R["folder"], "NH")[-1]
    c.be("CH")
    c.settings(screening={"record_time": True})
    log = c.get("/api/audit")["entries"][-1]
    assert log["action"] == "change settings" and log["details"]["time recording"] == "switched on"
    assert log["details"]["changed"] == ["time recording"]          # (not "screening plan": the plan has not changed)
    c.be("NH")
    assert c.overview()["timing"] == {"project": True, "mine": True}
    out = vote_timed(c, rest[1], shown=9000.7, active=8000, quiet=3000, views=2)
    assert out["timed"] is True and "time" not in out["saved"]           # saved, and not sent back: times are on no page
    assert out["timing"] == {"project": True, "mine": True}
    assert decisions(R["folder"], "NH")[-1]["time"] == {"shown": 9000, "active": 8000, "quiet": 3000, "views": 2}
    st, _, body = c.request("GET", "/api/record?id=" + rest[1])
    assert st == 200 and b'"time"' not in body
    c.decide(rest[2], "ta", "exclude")                       # a page that sends no times: the vote is saved without them
    assert "time" not in decisions(R["folder"], "NH")[-1]
    c.be("CH")
    c.settings(screening={"record_time": False})
    assert c.get("/api/audit")["entries"][-1]["details"]["time recording"] == "switched off"
    c.be("NH")
    assert vote_timed(c, rest[3], shown=9000, active=8000, quiet=3000)["timed"] is False      # (a page still open learns it)
    assert "time" not in decisions(R["folder"], "NH")[-1]
    # switching it on or off is not a change to the screening plan, which stays fixed
    c.be("CH")
    e = c.error("POST", "/api/settings", {"changes": {"screening": {"record_time": True, "kappa_threshold": 0.5}}})
    assert e.status == 400 and "the screening plan is fixed" in e.message
    e = c.error("POST", "/api/settings", {"changes": {"screening": {"record_time": "yes"}}})
    assert e.status == 400 and "should be yes or no" in e.message


def test_each_person_can_leave_their_own_votes_out(lead):
    c = lead
    R = single_screened(c, finish=False)
    rest = R["rest"]
    c.settings(screening={"record_time": True})
    c.be("NH")
    assert c.post("/api/timing/mine", {"on": False, "as": "NH"}) == {"project": True, "mine": False}
    assert c.overview()["timing"] == {"project": True, "mine": False}
    vote_timed(c, rest[0], shown=5000, active=5000, quiet=100)
    assert "time" not in decisions(R["folder"], "NH")[-1]
    c.be("CH")                                               # the choice is each person's own: the lead's votes are still timed
    assert c.overview()["timing"] == {"project": True, "mine": True}
    c.be("NH")
    assert c.post("/api/timing/mine", {"on": True, "as": "NH"}) == {"project": True, "mine": True}
    vote_timed(c, rest[1], shown=5000, active=5000, quiet=100)
    assert decisions(R["folder"], "NH")[-1]["time"]["shown"] == 5000
    own = [e for e in c.get("/api/audit")["entries"] if e["action"] == timing.OWN]
    assert [(e["who"], e["details"]) for e in own] == [("NH", {"on": False}), ("NH", {"on": True})]
    c.post("/api/timing/mine", {"on": True, "as": "NH"})                  # saying the same again logs nothing
    assert len([e for e in c.get("/api/audit")["entries"] if e["action"] == timing.OWN]) == 2
    e = c.error("POST", "/api/timing/mine", {"on": False, "as": "CH"})
    assert e.status == 400 and "opened as CH" in e.message
    e = c.error("POST", "/api/timing/mine", {"on": "no"})
    assert e.status == 400 and "could not read that request" in e.message
    # the choice is read from the person's own audit files only: a row in someone else's file saying otherwise is not theirs
    aud = os.path.join(R["folder"], "audit")
    theirs = next(f for f in os.listdir(aud) if f.startswith("CH@"))
    with open(os.path.join(aud, theirs), "a", encoding="utf-8", newline="\n") as out:
        out.write(json.dumps({"t": "2099-01-01T00:00:00.000+00:00", "who": "NH", "action": timing.OWN, "details": {"on": False},
                              "version": "1.3.1", "needs": "1.3.1"}) + "\n")
    assert c.overview()["timing"] == {"project": True, "mine": True}
    assert timing.own_choice(Project(R["folder"]), "NH") is True and timing.own_choice(Project(R["folder"]), "ZZ") is True
    # ... and from every computer the person uses: the later choice wins
    mine = next(f for f in os.listdir(aud) if f.startswith("NH@"))
    with open(os.path.join(aud, "NH@otherlaptop.jsonl"), "w", encoding="utf-8", newline="\n") as out:
        out.write(json.dumps({"t": "2099-01-02T00:00:00.000+00:00", "who": "NH", "action": timing.OWN, "details": {"on": False},
                              "version": "1.3.1", "needs": "1.3.1"}) + "\n")
    assert timing.own_choice(Project(R["folder"]), "NH") is False and mine != "NH@otherlaptop.jsonl"
    assert c.overview()["timing"] == {"project": True, "mine": False}


@pytest.mark.parametrize("sent, kept", [
    ({"shown": 1000, "active": 5000, "quiet": 9000}, {"shown": 1000, "active": 1000, "quiet": 1000, "views": 1}),
    ({"shown": 10 ** 12, "active": 10 ** 12, "quiet": 0, "views": 10 ** 6}, {"shown": 86400000, "active": 86400000, "quiet": 0, "views": 999}),
    ({"shown": 1000, "active": 900}, {"shown": 1000, "active": 900, "views": 1}),
    ({"shown": 1000, "active": 900, "quiet": -5, "views": 0}, {"shown": 1000, "active": 900, "views": 1}),
    ({"shown": -1, "active": 900, "quiet": 5}, None), ({"shown": 1000}, None), ({}, None),
    ({"shown": True, "active": 1, "quiet": 1}, None), ({"shown": float("nan"), "active": 1, "quiet": 1}, None),
    ({"shown": float("inf"), "active": float("inf"), "quiet": float("inf"), "views": float("inf")}, None),
    ({"shown": 1000, "active": 900, "quiet": float("inf"), "views": float("inf")}, {"shown": 1000, "active": 900, "views": 1})])
def test_times_are_tidied_before_they_are_saved_and_never_stop_a_vote(sent, kept):
    assert timing.clean(sent) == kept


def test_times_of_the_wrong_kind_are_refused_and_at_full_text_no_quiet_time_is_kept(lead):
    c = lead
    R = single_screened(c)
    c.settings(screening={"record_time": True})
    c.be("NH")
    for bad in ({"shown": "long"}, {"shown": 1, "active": [1]}, "5 seconds", [1, 2, 3]):
        e = c.error("POST", "/api/decide", {"record": R["rest"][0], "stage": "ta", "decision": "exclude", "as": "NH", "time": bad})
        assert e.status == 400 and "could not read that request" in e.message, bad
    rid = sorted(R["nh_yes"])[0]
    vote_timed(c, rid, stage="ft", decision="include", shown=60000, active=20000, quiet=15000, views=1)
    assert decisions(R["folder"], "NH")[-1]["time"] == {"shown": 60000, "active": 20000, "views": 1}
    assert timing.clean("x") is None and timing.clean(None) is None


# ------------------------------------------------------------------------------------------------ the evaluation download
def unzip(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.testzip() is None
        return {n: z.read(n) for n in z.namelist()}


def table(data: bytes) -> list:
    rows = read_csv(data)
    return [dict(zip(rows[0], r)) for r in rows[1:]]


def test_the_evaluation_download_holds_every_record_vote_and_answer(lead):
    c = lead
    R = single_screened(c, finish=False)
    c.settings(screening={"record_time": True})
    c.be("NH")
    for k, it in enumerate(c.queue("screen")):
        vote_timed(c, it["id"], decision=R["nh_vote"][it["id"]], shown=4000 + k, active=3000 + k, quiet=500, views=1)
    c.be("CH")
    screen_all(c, "CH", lambda r: "include" if r in R["eligible"] else "exclude", "second")
    c.post("/api/screening/verify", {"set": "main", "size": 20, "decoys": 4, "reviewer": "TK"})
    vf = state(R["folder"])["verify"]
    c.be("TK")
    for rid in vf["order"]:
        vote_timed(c, rid, stage="vs", decision="include" if rid in R["eligible"] else "exclude", shown=7000, active=6500, quiet=900)
    c.be("NH")
    assert c.download("evaluation")[0] == 403
    c.be("CH")
    st, head, data = c.download("evaluation")
    assert st == 200 and head["content-type"] == "application/zip" and "evaluation%20data.zip" in head["content-disposition"]
    files = unzip(data)
    assert sorted(files) == ["README.txt", "audit log.csv", "events.csv", "records.csv", "summary.json", "verification.csv", "votes.csv"]
    readme = files["README.txt"].decode("utf-8")
    recs = table(files["records.csv"])
    votes = table(files["votes.csv"])
    ver = table(files["verification.csv"])
    events = table(files["events.csv"])
    for name, rows in (("records.csv", recs), ("votes.csv", votes), ("verification.csv", ver), ("events.csv", events)):
        part = readme.split("\n" + name + ":")[1].split("\n\n")[0]
        for col in rows[0]:                                   # every column is described in the read-me, under its file
            words = re.sub(r"^AI pass [AB]:", "AI pass A / B:", re.sub(r"^Vote of (NH|CH|TK)", "Vote of XX", col))
            for w in words.replace("AI pass A / B: ", "").split(", ") if words.startswith("AI pass") else [words]:
                assert w in part, (name, col)
    # records: one row each, in the random order, with the plan's view of it
    sv = state(R["folder"])
    assert [r["Record"] for r in recs] == sv["order"] and [int(r["Position in the random order"]) for r in recs] == list(range(1, 61))
    by = {r["Record"]: r for r in recs}
    for rid in R["sample"]:
        assert by[rid]["In the agreement sample"] == "yes" and by[rid]["Had to screen it"] == "NH; CH"
        assert by[rid]["Vote of NH that counts for agreement"] == by[rid]["Vote of NH"] != ""
        assert by[rid]["Vote of CH that counts for agreement"] == by[rid]["Vote of CH"] != ""
    assert len({r["Text fingerprint"] for r in recs}) == 60 and all(re.fullmatch(r"[0-9a-f]{16}", r["Text fingerprint"]) for r in recs)
    kept = R["missed_kept"]
    assert by[kept]["Second look"] == "yes" and by[kept]["How decided"] == "second look sent it on" and by[kept]["Vote of CH"] == "include"
    assert by[kept]["AI (kept unless both exclude)"] == "keep" and by[kept]["AI pass A: answer"] == "INCLUDE"
    for rid in R["rest"]:
        assert by[rid]["In the agreement sample"] == "no" and by[rid]["Vote of NH"] == R["nh_vote"][rid]
        assert by[rid]["Vote of NH that counts for agreement"] == ""
        assert by[rid]["AI (kept unless both exclude)"] == ("keep" if rid in R["ai_keep"] else "exclude")
        assert by[rid]["Verification sample"] == ("sample" if rid in vf["sample"] else "mixed in" if rid in vf["decoy"] else "")
        assert int(by[rid]["AI pass A: prompt tokens"]) > 0 and by[rid]["AI pass B: model as served"]
    for rid in vf["sample"]:
        want = "include" if rid in R["eligible"] else "exclude"
        assert by[rid]["Verifier's vote"] == want
        assert by[rid]["Title and abstract outcome"] == ("forward" if want == "include" else "excluded")
        assert by[rid]["How decided"] == ("verification sent it on" if want == "include" else "single reviewer")
    # votes: every vote, with its task and its times
    assert len(votes) == 12 * 2 + 48 + 4 + 24
    tasks = {}
    for v in votes:
        tasks[(v["Reviewer"], v["Task"])] = tasks.get((v["Reviewer"], v["Task"]), 0) + 1
    assert tasks == {("NH", "agreement sample"): 12, ("CH", "agreement sample"): 12, ("NH", "screening"): 48,
                     ("CH", "second look"): 4, ("TK", "verification sample"): 24}
    for v in votes:
        timed = v["Task"] in ("screening", "verification sample")
        assert v["Time recorded"] == ("yes" if timed else "no") and (v["Shown (ms)"] != "") == timed
        assert v["Latest"] == "yes" and v["Vote number"] == "1"
        assert v["Counts for agreement"] == ("yes" if v["Task"] == "agreement sample" else "")
    assert {v["Part in the plan"] for v in votes if v["Reviewer"] == "NH"} == {"first reviewer; single reviewer; full-text assessor"}
    assert {v["Part in the plan"] for v in votes if v["Reviewer"] == "TK"} == {"verifier"}
    nh = [v for v in votes if v["Reviewer"] == "NH" and v["Task"] == "screening"]
    assert [int(v["Shown (ms)"]) for v in nh] == [4000 + k for k in range(48)] and {v["Longest quiet stretch (ms)"] for v in nh} == {"500"}
    assert [v["When"] for v in votes] == sorted(v["When"] for v in votes)
    # the verification sample, in the order shown
    assert [r["Record"] for r in ver] == vf["order"] and [int(r["Position shown"]) for r in ver] == list(range(1, 25))
    for r in ver:
        assert r["Sample or mixed in"] == ("sample" if r["Record"] in vf["sample"] else "mixed in")
        assert r["Single reviewer"] == "NH" and r["Verifier"] == "TK" and r["Shown (ms)"] == "7000"
        assert r["Single reviewer's vote"] == ("exclude" if r["Record"] in vf["sample"] else "include")
        assert r["AI (kept unless both exclude)"] == ("exclude" if r["Record"] in vf["sample"] else "keep")
    # the summary, as data anyone's analysis can start from
    s = json.loads(files["summary.json"])
    m = s["sets"]["main"]
    assert s["made_with"] == "ReviewDesk 1.3.1" and m["records"] == 60 and m["agreement_sample"] == 12
    assert m["plan"]["mode"] == "sample_then_single" and m["plan"]["kappa_threshold"] == 0.8 and m["remainder_mode"] == "single"
    assert m["people"] == {"NH": ["first reviewer", "single reviewer", "full-text assessor"],
                           "CH": ["second reviewer", "full-text assessor"], "TK": ["verifier"]}
    assert m["sample_result"]["kappa"] == 1.0 and m["second_looks"]["total"] == 4
    assert m["verification"]["size"] == 20 and m["verification"]["results"]["sent_on"]["n"] == 20
    assert m["verification"]["results"]["decoys"] == {"n": 4, "sent_on": 4}
    assert s["ai"]["totals"]["A"]["answers"] == 60 and s["ai"]["totals"]["A"]["prompt_tokens"] > 0
    assert s["time_recording"]["on_now"] is True
    assert [x["what"] for x in s["time_recording"]["changes"]] == ["time recording switched on"]
    assert "NaN" not in files["summary.json"].decode("utf-8")
    # the dated actions, with numbers only
    assert [e["Action"] for e in events] == [
        "process imports", "change AI settings", "start screening", "AI run started", "AI run ended", "change AI settings",
        "apply sample decision", "time recording switched on", evaluation.DRAW], [e["Action"] for e in events]
    acts = {e["Action"]: e for e in events}
    assert acts["AI run ended"]["Figures"] == "done: 120; errors: 0; stopped: no" and acts["AI run ended"]["Who"] == "CH"
    assert acts[evaluation.DRAW]["Figures"] == "sample: 20; of: 39; mixed in: 4" and acts["start screening"]["ReviewDesk version"] == "1.3.1"
    # nothing personal that was not asked for: no notes, no names, no abstracts, no AI reasons
    blob = b"".join(files[n] for n in ("records.csv", "votes.csv", "verification.csv", "summary.json", "events.csv"))
    assert b"Nadine" not in blob and b"Background:" not in blob
    assert "This is the team's copy" in readme and "audit log.csv: every action" in readme


def test_the_copy_for_sharing_shows_people_as_letters_and_leaves_the_audit_log_out(lead):
    c = lead
    R = drawn(c, size=10, decoys=2)
    c.settings(screening={"record_time": True})
    c.be("TK")
    for rid in R["vf"]["order"]:
        vote_timed(c, rid, stage="vs", decision="exclude", shown=3000, active=2500, quiet=800)
    c.post("/api/timing/mine", {"on": False, "as": "TK"})
    assert c.download("evaluation_share")[0] == 403
    c.be("CH")
    st, head, data = c.download("evaluation_share")
    assert st == 200 and "evaluation%20data%20for%20sharing.zip" in head["content-disposition"]
    files = unzip(data)
    assert sorted(files) == ["README.txt", "events.csv", "records.csv", "summary.json", "verification.csv", "votes.csv"]
    readme = files["README.txt"].decode("utf-8")
    assert "This is the copy for sharing" in readme and "audit log.csv" not in readme.split("events.csv:")[1]
    recs, votes, ver, events = (table(files[n]) for n in ("records.csv", "votes.csv", "verification.csv", "events.csv"))
    assert {v["Reviewer"] for v in votes} == {"Reviewer A", "Reviewer B", "Reviewer C"}
    assert "Vote of Reviewer B" in recs[0] and "Vote of Reviewer A that counts for agreement" in recs[0] and "Vote of NH" not in recs[0]
    assert {r["Had to screen it"] for r in recs} <= {"Reviewer B; Reviewer A", "Reviewer B", ""}
    assert {(r["Single reviewer"], r["Verifier"]) for r in ver} == {("Reviewer B", "Reviewer C")}
    assert {e["Who"] for e in events} == {"Reviewer A", "Reviewer C"} and events[-1]["Action"] == timing.OWN and events[-1]["Figures"] == "on: no"
    s = json.loads(files["summary.json"])
    m = s["sets"]["main"]
    assert [x["id"] for x in s["people"]] == ["Reviewer A", "Reviewer B", "Reviewer C"]
    assert m["plan"]["reviewers"] == ["Reviewer B", "Reviewer A"] and m["plan"]["single_reviewer"] == "Reviewer B"
    assert set(m["people"]) == {"Reviewer A", "Reviewer B", "Reviewer C"} and m["sample_result"]["reviewers"] == ["Reviewer B", "Reviewer A"]
    assert m["verification"]["reviewer"] == "Reviewer C" and m["verification"]["by"] == "Reviewer A"
    assert [x["who"] for x in s["time_recording"]["changes"]] == ["Reviewer A", "Reviewer C"]
    # no initials or names anywhere in it (the record IDs start with R, the people's initials never appear as words)
    blob = b"".join(files.values()).decode("utf-8")
    for word in ("Nadine", "Hayes", "Hawthorne", "Kereama", "Colin", "Tane"):
        assert word not in blob
    assert not re.search(r'(?<![A-Za-z0-9])(NH|CH|TK)(?![A-Za-z0-9])', blob)
    # the team's copy of the same project still names them by their initials
    team = unzip(c.download("evaluation")[2])
    assert {v["Reviewer"] for v in table(team["votes.csv"])} == {"NH", "CH", "TK"} and "audit log.csv" in team


def test_a_repeat_run_in_a_second_project_is_matched_record_for_record(lead):
    """Run-to-run stability: the same exports, criteria and AI set-up in a project of their own, one reviewer
    screening every record, the AI run on everything, screening started; its evaluation data then carries each
    record's text fingerprint and both models' answers, to set beside the first project's."""
    c = lead
    single_screened(c)
    first = unzip(c.download("evaluation")[2])
    one = {r["Text fingerprint"]: r for r in table(first["records.csv"])}
    c.post("/api/project/close", {})
    reference_project(c, str(c.tmp / "OneDrive"), name="AI triage repeat run 1", reviewers=TEAM,
                      screening=reference_plan(calibration_size=0, mode="single"))
    c.add_import(ris(*reversed(c.records)), "medline.ris", "MEDLINE")          # (in another order: the record IDs differ)
    c.process()
    flip = sorted(one)[0]                                                      # one record's answer comes out differently
    title_of = {r["Text fingerprint"]: r["Title"] for r in one.values()}
    was = one[flip]["AI (kept unless both exclude)"]
    kept_titles = {r["Title"] for r in one.values() if r["AI (kept unless both exclude)"] == "keep"}
    c.elm.decide = lambda call: (("EXCLUDE" if was == "keep" else "INCLUDE") if call.title[:200] == title_of[flip]
                                 else "INCLUDE" if call.title[:200] in kept_titles else "EXCLUDE")
    assert c.run_ai()["message"] == "Finished"
    c.post("/api/screening/start", {"set": "main"})
    again = unzip(c.download("evaluation")[2])
    two = {r["Text fingerprint"]: r for r in table(again["records.csv"])}
    assert set(two) == set(one) and len(two) == 60
    assert sum(1 for f in one if one[f]["Record"] != two[f]["Record"]) > 50          # matched by the text, not by the ID
    differ = [f for f in one if one[f]["AI (kept unless both exclude)"] != two[f]["AI (kept unless both exclude)"]]
    assert differ == [flip]
    s1, s2 = json.loads(first["summary.json"]), json.loads(again["summary.json"])
    assert s1["ai"]["prompt_fingerprints"] == s2["ai"]["prompt_fingerprints"] and s1["ai"]["models"] == s2["ai"]["models"]
    assert s1["ai"]["settings"] == s2["ai"]["settings"]


def test_the_reporting_pack_leaves_the_evaluation_data_out(lead):
    c = lead
    drawn(c, size=10, decoys=2)
    c.be("CH")
    waits = next(g for g in c.get("/api/reporting")["groups"] if g["id"] == "evaluation")["items"][0]["files"]
    assert {f["state"] for f in waits} == {"later"} and "still being screened (0 of 12 done)" in waits[0]["why"]
    c.be("TK")                                             # the verifier, in the middle of the sample: no file at all
    assert c.download("evaluation")[0] == 403 and c.download("evaluation_share")[0] == 403 and c.download("rep_pack")[0] == 403
    assert {f["state"] for g in c.get("/api/reporting")["groups"] for it in g["items"] for f in it["files"]} == {"later"}
    screen_all(c, "TK", lambda r: "exclude", "verify", stage="vs")
    c.be("CH")
    rep = c.get("/api/reporting")
    group = next(g for g in rep["groups"] if g["id"] == "evaluation")
    item = group["items"][0]
    assert item["title"] == "Evaluation data" and item["lead_only"] is True
    assert [(f["kind"], f["state"]) for f in item["files"]] == [("evaluation", "ready"), ("evaluation_share", "ready")]
    st, _, body = c.download("rep_pack")
    assert st == 200
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = z.namelist()
        readme = z.read("Read me first.txt").decode("utf-8")
    assert not [n for n in names if "evaluation" in n.lower()] and "Evaluation data" not in readme
    assert len([n for n in names if n != "Read me first.txt"]) == rep["ready"]         # the page's count is the pack's
    c.be("NH")                                             # a reviewer sees that it exists, and that it is the lead's
    theirs = next(g for g in c.get("/api/reporting")["groups"] if g["id"] == "evaluation")["items"][0]
    assert {f["state"] for f in theirs["files"]} == {"lead"}


# ------------------------------------------------------------------------------------------------ what keeps the sample blind
EVERY_DOWNLOAD = sorted(set(server.DOWNLOADS) | set(server.MODULE_DOWNLOADS) | {"dedup"})
OPEN_TO_A_VERIFIER = ("/api/app", "/api/overview", "/api/tasks", "/api/readiness", "/api/prisma", "/api/reporting", "/api/records",
                      "/api/agreement", "/api/searchlog", "/api/imports", "/api/dedup", "/api/timeline", "/api/sync",
                      "/api/integrity", "/api/ai", "/api/rob/plan", "/api/worksheet")


# Every route there is, sorted by what it could tell someone screening a verification sample. A route added later
# fails the test below until it has been put in one of these lists, which means: until someone has asked whether it
# says, of any record, that it reached full text (a PDF, a full-text vote, a study, an AI answer, an earlier vote).
GET_REVIEWED = {"/api/agreement",        # scope "fulltext" is closed in the handler; the other scopes are of other records
                "/api/ai",               # the AI's answers on known studies are hidden in the handler
                "/api/integrity",        # the checks of the PDFs are left out in the handler
                "/api/record",           # "pdf" and the link to a free copy are hidden in the handler
                "/api/queue",            # task "fulltext" is closed in the handler
                "/api/app", "/api/overview", "/api/tasks", "/api/readiness", "/api/prisma", "/api/reporting", "/api/records",
                "/api/searchlog", "/api/imports", "/api/dedup", "/api/timeline", "/api/sync", "/api/rob/plan",
                "/api/worksheet", "/api/guidance", "/api/fs", "/api/setup_summary"}
POST_REVIEWED = {"/api/decide",          # stage "ft" is closed in the handler
                 "/api/integrity/fix",   # the repairs of PDFs are closed in the handler
                 "/api/ai/config", "/api/ai/models", "/api/ai/run", "/api/ai/stop", "/api/dedup/merge", "/api/dedup/notdup",
                 "/api/imports/link", "/api/imports/process", "/api/imports/remove", "/api/me", "/api/project/close",
                 "/api/project/create", "/api/project/open", "/api/restore", "/api/rob/plan", "/api/screening/apply",
                 "/api/screening/calibration", "/api/screening/start", "/api/screening/verify", "/api/screening/verify/end",
                 "/api/searchlog", "/api/settings", "/api/sync/dismiss", "/api/sync/keep", "/api/sync/use", "/api/timing/mine",
                 "/api/user", "/api/worksheet"}


def test_every_route_has_been_sorted_by_what_it_tells_a_verifier():
    assert set(server.ROUTES_GET) == set(server.VERIFIER_CLOSED) | GET_REVIEWED
    assert not set(server.VERIFIER_CLOSED) & GET_REVIEWED
    assert {r for r in server.ROUTES_POST if not r.startswith(server.VERIFIER_CLOSED_CHANGES)} == POST_REVIEWED
    assert set(OPEN_TO_A_VERIFIER) <= GET_REVIEWED
    # every uploaded file is for full texts or imports, and the full-text ones are closed
    assert {r for r in server.ROUTES_RAW if not r.startswith(server.VERIFIER_CLOSED_CHANGES)} == {"/api/imports/upload"}


@pytest.mark.parametrize("verifier", ["TK", "CH"])
def test_the_pages_that_show_what_became_of_records_are_closed_to_the_verifier_until_they_finish(lead, verifier):
    """The records mixed into a sample were sent on, so they are at full text; the sampled ones were excluded, so they
    are not. Whatever lists records at or beyond full text would sort the verifier's queue for them: the full-text
    pages and PDFs, the included studies, the audit log (which names records whose PDFs were added) and the downloads.
    The project lead is not exempt."""
    c = lead
    R = drawn(c, size=20, decoys=4, reviewer=verifier)
    vf = R["vf"]
    c.be(verifier)
    for path in server.VERIFIER_CLOSED + ("/files/fulltext/" + vf["decoy"][0] + ".pdf", "/files/waiting/x.pdf"):
        st, _, body = c.request("GET", path)
        out = json.loads(body)
        assert st == 409 and out["verifying"] is True, path
        assert "You are screening a verification sample" in out["error"] and "(24 records left)" in out["error"], path
        assert not any(r.encode() in body for r in vf["order"]), path
    e = c.error("GET", "/api/queue", task="fulltext")
    assert e.status == 409 and "verification sample" in e.message
    assert c.task("fulltext") is None and c.task("verify")["left"] == 24
    for kind in EVERY_DOWNLOAD:
        st, _, body = c.download(kind)
        assert st == 403 and b"verification sample" in body, kind
    e = c.error("POST", "/api/decide", {"record": vf["decoy"][0], "stage": "ft", "decision": "include", "as": verifier})
    assert e.status == 409 and "verification sample" in e.message
    for path, body in (("/api/fulltext/fetch", {}), ("/api/fulltext/remove", {"record": vf["decoy"][0]}),
                       ("/api/resolve", {"record": vf["decoy"][0], "stage": "ft", "decision": "include"}),
                       ("/api/extract/done", {"key": "x", "done": True}), ("/api/certainty/done", {"o": "x", "done": True}),
                       ("/api/rob/judge", {"key": "x", "a": "y", "d": "D1", "j": "Low risk"})):
        e = c.error("POST", path, body)
        assert e.status == 409 and "verification sample" in e.message, path
    st, _, body = c.request("POST", "/api/fulltext/upload?record=" + vf["decoy"][0], body=b"%PDF-1.4 not really",
                            headers={"Content-Type": "application/octet-stream", "X-Project": c.get("/api/app")["project"]["key"]})
    assert st == 409 and b"verification sample" in body
    # what stays open tells the two kinds of record apart nowhere
    for path in OPEN_TO_A_VERIFIER:
        st, _, body = c.request("GET", path, params={"limit": 1000} if path == "/api/records" else None)
        assert st == 200, path
        seen = {r for r in vf["order"][1:] if ('"' + r + '"').encode() in body}      # (their task names the record that is next)
        assert not seen or (seen & set(vf["sample"]) and seen & set(vf["decoy"])), (path, sorted(seen))
    for rid in vf["order"]:
        rec = c.get("/api/record", id=rid)
        assert rec["pdf"] is False and rec["mine"]["ta"] is None and rec["mine"]["ft"] is None
    # everyone else carries on as before
    other = "NH"
    c.be(other)
    for path in server.VERIFIER_CLOSED:
        assert c.request("GET", path, params={"key": "x", "o": "x"} if "study" in path or "outcome" in path else None)[0] in (200, 400, 404), path
    assert c.task("fulltext") is not None
    # ... and so does the verifier, once the sample is finished
    screen_all(c, verifier, lambda r: "exclude", "verify", stage="vs")
    for path in ("/api/fulltext", "/api/fulltext/waiting", "/api/fulltext/missing", "/api/conflicts", "/api/audit", "/api/extract",
                 "/api/certainty"):
        assert c.request("GET", path)[0] == 200, path
    assert c.overview()["verifying"] is None and (verifier == "TK" or c.task("fulltext") is not None)
    assert c.download("prisma")[0] == 200


def test_nothing_moves_while_the_verifier_votes(lead):
    """A sampled record the verifier would send on goes to full text only when the sample is finished. Otherwise every
    count, and the list of reports at full text, would answer "was that one excluded before?" after each Include, for
    the verifier and for the reviewer being checked."""
    c = lead
    R = drawn(c, size=39, decoys=5)
    vf = R["vf"]

    def seen_by(who):
        c.be(who)
        ft = [x["id"] for x in c.get("/api/fulltext")["rows"]] if who != "TK" else None
        return (c.overview()["sets"]["main"]["counts"], c.get("/api/prisma")["counts"]["db"], ft, c.overview()["fulltext"])
    before = {who: seen_by(who) for who in ("CH", "NH", "TK")}
    c.be("TK")
    for rid in vf["order"][:-1]:
        c.decide(rid, "vs", "include")                       # every one of them, the sampled and the mixed in alike
    assert {who: seen_by(who) for who in ("CH", "NH", "TK")} == before
    p = Project(R["folder"])
    assert all(o["how"] != screening.VERIFIED_ON for o in screening.ta_outcomes(p, "main").values())
    c.be("TK")
    c.decide(vf["order"][-1], "vs", "include")               # the last one: now they all go on together
    c.be("CH")
    after = c.overview()["sets"]["main"]["counts"]
    assert after["forward"] == before["CH"][0]["forward"] + 39 and after["excluded"] == before["CH"][0]["excluded"] - 39
    assert set(vf["sample"]) <= {x["id"] for x in c.get("/api/fulltext")["rows"]}


def test_a_reviewer_is_given_no_number_of_the_sample(lead):
    """How many records left on the single reviewer's vote alone, set beside her own count of exclusions, is how many
    of them the AI kept. Only the lead is given the sample's numbers; its verifier, how many records they have."""
    c = lead
    R = drawn(c, size=20, decoys=4)
    for who in ("NH", "TK"):
        c.be(who)
        st, _, body = c.request("GET", "/api/overview")
        v = json.loads(body)["sets"]["main"]["verify"]
        assert set(v) == ({"drawn", "drawn_at", "reviewer", "single_reviewer", "complete", "ended"} |
                          ({"total", "done"} if who == "TK" else set()))
        for path in ("/api/overview", "/api/prisma", "/api/readiness", "/api/tasks", "/api/reporting", "/api/timeline"):
            st, _, body = c.request("GET", path)
            text = body.decode("utf-8")
            assert st == 200 and '"population"' not in text and "from the 39" not in text and "39 records" not in text, (who, path)
            assert "seed " + str(R["vf"]["seed"]) not in text, (who, path)
    c.be("NH")
    st, _, body = c.request("GET", "/api/audit")
    entry = next(e for e in json.loads(body)["entries"] if e["action"] == evaluation.DRAW)
    assert entry["details"] == {"set": "main", "screened by": "TK"}
    st, _, body = c.download("audit")
    row = next(r for r in read_csv(body) if r[2] == evaluation.DRAW)
    assert row[3] == "set: main; screened by: TK"
    c.be("CH")
    full = next(e for e in c.get("/api/audit")["entries"] if e["action"] == evaluation.DRAW)
    assert full["details"]["of"] == 39 and full["details"]["sample"] == 20


def test_no_page_is_sent_how_long_anyone_took(lead):
    c = lead
    R = single_screened(c)
    c.settings(screening={"record_time": True})
    rid = sorted(R["nh_yes"])[0]
    c.be("NH")
    vote_timed(c, rid, stage="ft", decision="include", shown=60000, active=20000)
    c.be("CH")
    c.post("/api/decide", {"record": rid, "stage": "ft", "decision": "exclude", "reason": "Wrong population", "as": "CH",
                           "time": {"shown": 50000, "active": 10000}})
    assert decisions(R["folder"], "CH")[-1]["time"] == {"shown": 50000, "active": 10000, "views": 1}
    for who in ("CH", "NH"):
        c.be(who)
        st, _, body = c.request("GET", "/api/conflicts")
        out = json.loads(body)
        assert st == 200 and [x["id"] for x in out["conflicts"]] == [rid] and b'"time"' not in body and b"50000" not in body
        for path in ("/api/overview", "/api/fulltext", "/api/record?id=" + rid, "/api/agreement?scope=fulltext"):
            assert b'"time"' not in c.request("GET", path)[2], path


def test_a_damaged_sample_in_the_shared_state_stops_no_page(lead):
    """The state is a shared file. Whatever it holds under "verify", every page still opens: a value of the wrong kind
    is put right or left out, and for something that cannot be a sample the copy the lead's audit log kept at the draw
    stands in. Only if that is gone as well does it count as none drawn (the lead is told that its votes have lost
    their sample)."""
    c = lead
    R = drawn(c, size=10, decoys=2)
    screen_all(c, "TK", lambda r: "include" if r in R["eligible"] else "exclude", "verify", stage="vs")
    path = os.path.join(R["folder"], "state", "state.json")
    good = read_json(path)
    c.be("CH")
    as_drawn = verify_of(c)
    assert as_drawn["complete"] is True and as_drawn["from_log"] is False

    def with_verify(**changes):
        st = json.loads(json.dumps(good))
        st["sets"]["main"]["verify"].update(changes)
        st["rev"] = good["rev"]
        write_json(path, st)

    def every_page_opens(note):
        for who in ("CH", "TK", "NH"):
            c.be(who)
            for route in ("/api/overview", "/api/prisma", "/api/readiness", "/api/tasks", "/api/reporting", "/api/app"):
                st, _, body = c.request("GET", route)
                assert st == 200, (note, who, route, body[:300])
        c.be("CH")
        for kind in ("evaluation", "evaluation_share", "summary", "records", "decisions", "rep_methods"):
            st, _, body = c.download(kind)
            assert st == 200, (note, kind, body[:300])
        return c.overview()["sets"]["main"]

    for note, changes in (("population not a number", {"population": "abc"}), ("population too small", {"population": 1}),
                          ("no single reviewer", {"single_reviewer": ["NH"]}), ("odd seed and date", {"seed": "x", "drawn": 5}),
                          ("ended is not a record", {"ended": "yes"}), ("no decoy pool", {"decoy_pool": None, "by": 7})):
        with_verify(**changes)
        sv = every_page_opens(note)
        assert sv["verify"]["drawn"] is True and sv["verify"]["population"] >= 10, note
    for note, changes in (("empty lists", {"sample": [], "decoy": [], "order": []}), ("order does not match", {"order": ["R00001"]}),
                          ("a record twice", {"sample": good["sets"]["main"]["verify"]["sample"][:1] * 2}),
                          ("no verifier", {"reviewer": ""}), ("lists of numbers", {"sample": [1, 2], "order": [1, 2], "decoy": []})):
        with_verify(**changes)
        sv = every_page_opens(note)
        assert sv["verify"]["drawn"] is True and sv["verify"]["from_log"] is True, note      # the logged draw stands in
        assert sv["verify"]["complete"] is True and sv["verify"]["results"] == as_drawn["results"], note
    st = json.loads(json.dumps(good))
    st["sets"]["main"]["verify"] = ["not", "a", "sample"]
    write_json(path, st)
    assert every_page_opens("a list")["verify"]["results"] == as_drawn["results"]
    forget_the_draw(R["folder"])                             # ... and without the log's copy: none drawn
    sv = every_page_opens("a list, and no log")
    assert sv["verify"]["drawn"] is False and sv["verify"]["lost_votes"] == 12
    with_verify(sample=[], decoy=[], order=[])
    assert every_page_opens("empty lists, and no log")["verify"]["lost_votes"] == 12
    write_json(path, good)
    sv = every_page_opens("as it was")
    assert sv["verify"]["complete"] is True and sv["verify"]["from_log"] is False


def test_a_sample_the_state_has_lost_stands_as_the_lead_s_log_holds_it(lead):
    """Every draw is logged whole in the lead's audit log. If the state loses the sample, that copy stands in: the same
    records, in the same order, for the same verifier, and nothing has to be drawn again. Only if the log has lost
    it too is it gone: the lead is then told, and can only draw it again."""
    c = lead
    R = drawn(c, size=39, decoys=2)
    lost = sorted(R["missed_lost"])
    screen_all(c, "TK", lambda r: "include" if r in lost else "exclude", "verify", stage="vs")
    c.be("CH")
    was = verify_of(c)
    assert c.overview()["sets"]["main"]["counts"]["forward"] == 8 + 2 and was["from_log"] is False
    log = [e for e in c.get("/api/audit")["entries"] if e["action"] == evaluation.DRAW]
    assert len(log) == 1 and "draw" not in log[0]["details"] and log[0]["details"]["sample"] == 39     # (its numbers, as before)
    path = os.path.join(R["folder"], "state", "state.json")
    st = read_json(path)
    kept = st["sets"]["main"].pop("verify")                   # an older copy of the state is put back: no sample in it
    write_json(path, st)
    sv = c.overview()["sets"]["main"]
    assert sv["counts"]["forward"] == 8 + 2 and sv["verify"] == {**was, "from_log": True}
    assert screening._prep(Project(R["folder"]), "main")[0]["_verify"] == screening.verify_of({"verify": kept})
    for who in ("NH", "TK"):
        c.be(who)
        v = verify_of(c)
        assert v["complete"] is True and "from_log" not in v and c.overview()["verifying"] is None
    c.be("CH")
    e = c.error("POST", "/api/screening/verify", {"set": "main", "size": 5, "decoys": 0, "reviewer": "TK"})
    assert e.status == 400 and "has been drawn (it is drawn once)" in e.message
    assert c.download("evaluation")[0] == 200
    # the log loses it too: now it is gone, and the lead is told
    forget_the_draw(R["folder"])
    sv = c.overview()["sets"]["main"]
    assert sv["counts"]["forward"] == 8 and sv["verify"]["drawn"] is False and sv["verify"]["lost_votes"] == 41
    c.be("NH")
    assert c.overview()["sets"]["main"]["verify"] is None     # (the lead's to put right)
    assert c.overview()["verifying"] is None and c.request("GET", "/api/fulltext")[0] == 200
    c.be("TK")                                                 # whoever has votes on it stays closed out meanwhile
    assert c.overview()["verifying"] == {"set": "main", "left": 0, "total": 0, "lost": True}
    assert c.request("GET", "/api/fulltext")[0] == 409
    c.be("CH")
    out = c.post("/api/screening/verify", {"set": "main", "size": 39, "decoys": 2, "reviewer": "TK"})["verify"]
    again = state(R["folder"])["verify"]
    assert again["sample"] == kept["sample"] and again["order"] == kept["order"]       # the same seed, the same sample
    assert out["complete"] is True and c.overview()["sets"]["main"]["counts"]["forward"] == 8 + 2


def state_copies(verify: bool) -> list:
    """The copies of the screening state kept on the lead's computer, oldest first, with or without a sample in them."""
    d = server.APP.project._local("backups")
    names = sorted(n for n in os.listdir(d) if n.startswith("state__state.json."))
    return [n for n in names if ("verify" in (read_json(os.path.join(d, n)).get("sets", {}).get("main") or {})) is verify]


@pytest.mark.parametrize("verifier", ["TK", "CH"])
def test_putting_back_an_older_state_does_not_open_the_pages_to_the_verifier(lead, verifier):
    """The lead puts back a copy of the screening state from before the draw, or from before screening started (by
    mistake, or to look). The sample is not lost with it: the lead's log holds the draw, so its verifier carries on
    where they were, with the pages that show what became of records still closed. Putting back a state is no way
    to see what was decided and then go on voting."""
    c = lead
    R = single_screened(c)
    c.post("/api/screening/verify", {"set": "main", "size": 20, "decoys": 4, "reviewer": verifier})
    vf = state(R["folder"])["verify"]
    c.be(verifier)
    for rid in vf["order"][:3]:
        c.decide(rid, "vs", "exclude")
    before = state_copies(verify=False)
    oldest, newest = before[0], before[-1]
    assert not read_json(os.path.join(server.APP.project._local("backups"), oldest)).get("sets", {}).get("main", {}).get("order")
    for copy_ in (newest, oldest):                             # from just before the draw; from before screening started
        c.be("CH")
        c.post("/api/restore", {"file": "state/state.json", "copy": copy_, "older_ok": True})
        assert "verify" not in (read_json(os.path.join(R["folder"], "state", "state.json")).get("sets", {}).get("main") or {})
        c.be(verifier)
        assert c.overview()["verifying"] == {"set": "main", "left": 21, "total": 24}, copy_
        for path in server.VERIFIER_CLOSED + ("/files/fulltext/" + vf["decoy"][0] + ".pdf",):
            st, _, body = c.request("GET", path)
            assert st == 409 and json.loads(body)["verifying"] is True, (copy_, path)
            assert "You are screening a verification sample" in json.loads(body)["error"], (copy_, path)
        # ... and they can carry on with it, unless the state is from before the records were there at all
        there = copy_ != oldest
        assert (c.task("verify") or {}).get("left") == (21 if there else None), copy_
        assert [x["id"] for x in c.queue("verify")] == (vf["order"] if there else []), copy_
        assert c.download("decisions")[0] == 403
        st, _, body = c.request("GET", "/api/record", params={"id": vf["decoy"][0]})      # (the oldest state has no records yet)
        assert (st == 404 and copy_ == before[0]) or json.loads(body)["pdf"] is False, copy_
    c.be("CH")
    c.post("/api/restore", {"file": "state/state.json", "copy": newest, "older_ok": True})
    sv = verify_of(c)
    assert sv["drawn"] is True and sv["from_log"] is True and (sv["done"], sv["total"]) == (3, 24)
    c.be("NH")                                                 # nobody else is held up
    assert c.overview()["verifying"] is None and c.request("GET", "/api/fulltext")[0] == 200
    # the verifier finishes the same sample, in the same order; nothing was drawn again
    c.be(verifier)
    assert [x["id"] for x in c.queue("verify")] == vf["order"] and c.task("verify")["left"] == 21
    screen_all(c, verifier, lambda r: "exclude", "verify", stage="vs")
    assert c.overview()["verifying"] is None and c.request("GET", "/api/fulltext")[0] == 200
    # whoever analyses the data can see that it happened, and when
    c.be("CH")
    files = unzip(c.download("evaluation")[2])
    events = [r["Action"] for r in table(files["events.csv"])]
    assert events.count(evaluation.DRAW) == 1
    assert events[events.index(evaluation.DRAW) + 1:].count("screening state put back from an earlier copy") == 3
    assert len(table(files["verification.csv"])) == 24 and json.loads(files["summary.json"])["sets"]["main"]["verification"]["drawn"]


@pytest.mark.parametrize("verifier", ["TK", "CH"])
def test_a_sample_lost_altogether_keeps_the_pages_closed_until_it_is_drawn_again(lead, verifier):
    """Should the lead's log lose the draw as well as the state (the last line of defence), whoever has votes on the
    sample stays closed out until the lead draws it again: whatever the state now holds, a copy from before screening
    started included."""
    c = lead
    R = single_screened(c)
    c.post("/api/screening/verify", {"set": "main", "size": 20, "decoys": 4, "reviewer": verifier})
    vf = state(R["folder"])["verify"]
    c.be(verifier)
    for rid in vf["order"][:3]:
        c.decide(rid, "vs", "exclude")
    before = state_copies(verify=False)
    forget_the_draw(R["folder"])
    for copy_ in (before[0], before[-1]):                      # from before screening started; from just before the draw
        c.be("CH")
        c.post("/api/restore", {"file": "state/state.json", "copy": copy_, "older_ok": True})
        c.be(verifier)
        assert c.overview()["verifying"] == {"set": "main", "left": 0, "total": 0, "lost": True}, copy_
        assert c.task("verify") is None and c.task("fulltext") is None
        for path in server.VERIFIER_CLOSED + ("/files/fulltext/" + vf["decoy"][0] + ".pdf",):
            st, _, body = c.request("GET", path)
            out = json.loads(body)
            assert st == 409 and out["verifying"] is True and out["title"] == "Closed until the verification sample is drawn again", path
            assert "is no longer in the project's files" in out["error"] and "has drawn the sample again" in out["error"], path
        assert c.download("decisions")[0] == 403
        st, _, body = c.request("GET", "/api/record", params={"id": vf["decoy"][0]})      # (the oldest state has no records yet)
        assert (st == 404 and copy_ == before[0]) or json.loads(body)["pdf"] is False, copy_
    e = c.error("POST", "/api/decide", {"record": vf["order"][5], "stage": "vs", "decision": "include", "as": verifier})
    assert e.status == 400 and "not in a verification sample of yours" in e.message          # no sample: no vote on it
    c.be("NH")                                                 # nobody else is held up
    assert c.overview()["verifying"] is None and c.request("GET", "/api/fulltext")[0] == 200
    c.be("CH")
    sv = verify_of(c)
    assert sv["drawn"] is False and sv["lost_votes"] == 3
    # drawn again with the same numbers: the same sample, the votes count again, and the verifier carries on
    c.post("/api/screening/verify", {"set": "main", "size": 20, "decoys": 4, "reviewer": verifier})
    again = state(R["folder"])["verify"]
    assert again["order"] == vf["order"] and again["sample"] == vf["sample"]
    c.be(verifier)
    assert c.overview()["verifying"] == {"set": "main", "left": 21, "total": 24}
    assert c.request("GET", "/api/fulltext")[0] == 409
    screen_all(c, verifier, lambda r: "exclude", "verify", stage="vs")
    assert c.overview()["verifying"] is None and c.request("GET", "/api/fulltext")[0] == 200


def test_putting_back_an_older_project_file_does_not_lower_the_version_the_project_needs(needing_less, lead):
    """Drawing a sample makes the project need 1.3.1, which the project file says. A copy of that file from before the
    draw, put back, must not say less: ReviewDesk 1.3.0 would then save in the project again, and count the records
    the sample sent on as excluded. (Seen as if this version needed only 1.3.0 until the draw: see needing_less.)"""
    c = lead
    R = single_screened(c)
    d = server.APP.project._local("backups")
    marker = os.path.join(R["folder"], MARKER)
    c.settings(known_studies=[{"doi": "10.9999/a-change-that-is-saved"}])          # (so that a copy from before the draw is kept)
    before = sorted(n for n in os.listdir(d) if n.startswith(MARKER + "."))[-1]
    assert read_json(os.path.join(d, before)).get("min_version") == "1.3.0" == read_json(marker)["min_version"]
    c.post("/api/screening/verify", {"set": "main", "size": 6, "decoys": 2, "reviewer": "TK"})
    assert read_json(marker)["min_version"] == "1.3.1"
    c.post("/api/restore", {"file": MARKER, "copy": before, "older_ok": True})
    assert read_json(marker)["min_version"] == "1.3.1"
    with open(marker, "w", encoding="utf-8") as fh:            # the file is damaged as well: the sample itself says so
        fh.write('{"title": ')
    server.APP.project.restore_backup("CH", MARKER, before, True)
    assert read_json(marker)["min_version"] == "1.3.1" and c.overview()["sets"]["main"]["verify"]["drawn"] is True


def test_a_sample_the_lead_ended_stays_ended_whatever_state_is_put_back(lead):
    """Ending a sample opens every page to its verifier. A copy of the state from between the draw and the end holds
    the sample not yet ended: put back, it must not open the sample again for a verifier who has now seen which of
    its records were mixed in. The end is in the lead's log, and the log decides."""
    c = lead
    R = drawn(c, size=20, decoys=4)
    vf = R["vf"]
    c.be("TK")
    for rid in vf["order"][:5]:
        c.decide(rid, "vs", "exclude")
    c.be("CH")
    ended = c.post("/api/screening/verify/end", {"set": "main", "reason": "Tane left the team.", "as": "CH"})["verify"]
    assert ended["ended"] is True and ended["done"] == 5
    unended = [n for n in state_copies(verify=True)
               if "ended" not in read_json(os.path.join(server.APP.project._local("backups"), n))["sets"]["main"]["verify"]]
    assert unended
    for copy_ in (unended[-1], state_copies(verify=False)[-1]):          # the sample not yet ended; no sample at all
        c.be("CH")
        c.post("/api/restore", {"file": "state/state.json", "copy": copy_, "older_ok": True})
        assert "ended" not in (state(R["folder"]).get("verify") or {})
        v = verify_of(c)
        assert v["ended"] is True and v["complete"] is True and v["results"] == ended["results"], copy_
        assert v["ended_why"] == "Tane left the team."
        c.be("TK")
        assert c.overview()["verifying"] is None and c.task("verify") is None and c.request("GET", "/api/fulltext")[0] == 200
        e = c.error("POST", "/api/decide", {"record": vf["order"][10], "stage": "vs", "decision": "include", "as": "TK", "finish": True})
        assert e.status == 400 and "has been ended by the project lead" in e.message, copy_
    c.be("CH")
    assert "The sample was ended as it stood by Dr Colin Hawthorne" in c.get("/api/prisma")["summary"]
    # ended when the state had already lost the sample: put back into the state, ended; and it stays so
    e = c.error("POST", "/api/screening/verify/end", {"set": "main", "reason": "again"})
    assert e.status == 400 and "already finished" in e.message


def test_a_sample_stays_drawn_when_the_lead_role_moves(lead):
    """The draw is in the audit file of whoever was the lead then. If someone else is the lead now, it is still the
    sample: the new lead cannot draw another (which would free the first verifier, who could then look and later be
    given their sample back), and putting back an older state changes nothing."""
    c = lead
    R = single_screened(c)
    c.post("/api/screening/verify", {"set": "main", "size": 20, "decoys": 4, "reviewer": "TK"})
    vf = state(R["folder"])["verify"]
    c.be("TK")
    for rid in vf["order"][:3]:
        c.decide(rid, "vs", "exclude")
    c.be("CH")
    c.settings(reviewers=[{**r, "role": "lead" if r["id"] == "NH" else "reviewer"} for r in TEAM])
    c.be("NH")
    assert c.get("/api/app")["project"]["is_lead"] is True
    c.post("/api/restore", {"file": "state/state.json", "copy": state_copies(verify=False)[-1], "older_ok": True})
    v = verify_of(c)
    assert v["drawn"] is True and v["reviewer"] == "TK" and v["from_log"] is True and (v["done"], v["total"]) == (3, 24)
    e = c.error("POST", "/api/screening/verify", {"set": "main", "size": 20, "decoys": 4, "reviewer": "CH"})
    assert e.status == 400 and "has been drawn (it is drawn once)" in e.message
    c.be("TK")
    assert c.overview()["verifying"] == {"set": "main", "left": 21, "total": 24} and c.request("GET", "/api/fulltext")[0] == 409
    assert [x["id"] for x in c.queue("verify")] == vf["order"]
    # an entry someone writes into their own audit file is not a draw: they were never the lead
    with open(os.path.join(R["folder"], "audit", "TK@000000.jsonl"), "w", encoding="utf-8") as fh:
        fake = {"reviewer": "CH", "single_reviewer": "NH", "sample": vf["sample"][:3], "decoy": [], "order": vf["sample"][:3]}
        fh.write(json.dumps({"t": "2030-01-01T10:00:00.000+00:00", "who": "TK", "action": evaluation.DRAW,
                             "details": {"set": "main", "draw": fake}}) + "\n")
    assert c.overview()["verifying"] == {"set": "main", "left": 21, "total": 24}
    assert screening._prep(Project(R["folder"]), "main")[0]["_verify"]["order"] == vf["order"]


def test_two_last_votes_at_once_cannot_both_slip_through(lead):
    """With two records left, two tabs (or two computers) each vote on one at the same moment, neither saying
    "finish". One of them is the last vote, whichever comes second: it is refused, and the sample is not finished."""
    import threading
    c = lead
    R = drawn(c, size=6, decoys=2)
    vf = R["vf"]
    c.be("TK")
    for rid in vf["order"][:-2]:
        c.decide(rid, "vs", "exclude")
    got = {}

    def vote(rid):
        got[rid] = c.request("POST", "/api/decide", json.dumps({"record": rid, "stage": "vs", "decision": "exclude", "as": "TK"}).encode(),
                             headers={"Content-Type": "application/json", "X-Project": c.project_key})[0]
    threads = [threading.Thread(target=vote, args=(rid,)) for rid in vf["order"][-2:]]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(got.values()) == [200, 409], got
    assert c.overview()["verifying"] == {"set": "main", "left": 1, "total": 8}


def test_a_sample_written_by_hand_cannot_make_votes_count_that_the_draw_would_not(lead):
    """The state is a shared file. A "verify" entry edited by hand must not let someone check their own exclusions,
    or send on a record that two people excluded: only what a draw could have made counts."""
    c = lead
    R = drawn(c, size=20, decoys=4)
    vf, path = R["vf"], os.path.join(R["folder"], "state", "state.json")
    both_excluded = [r for r in R["sample"] if r not in R["eligible"]][:2]
    st = read_json(path)
    st["sets"]["main"]["verify"]["sample"] = vf["sample"] + both_excluded
    st["sets"]["main"]["verify"]["order"] = vf["order"] + both_excluded
    write_json(path, st)
    screen_all(c, "TK", lambda r: "include", "verify", stage="vs")
    o = screening.ta_outcomes(Project(R["folder"]), "main")
    assert all(o[r]["status"] == "forward" and o[r]["how"] == screening.VERIFIED_ON for r in vf["sample"])
    assert all(o[r] == {"status": "excluded", "how": "both excluded", "required": ["NH", "CH"]} for r in both_excluded)
    # the single reviewer written in as their own verifier: not a sample at all, so the one the lead's log holds
    # stands (what TK found still counts), and NH has no task and no vote
    st["sets"]["main"]["verify"]["reviewer"] = "NH"
    write_json(path, st)
    assert screening.verify_of(state(R["folder"])) is None
    p = Project(R["folder"])
    assert screening._prep(p, "main")[0]["_verify"]["reviewer"] == "TK" and screening._prep(p, "main")[0]["_verify_logged"] is True
    c.be("NH")
    assert c.task("verify") is None
    e = c.error("POST", "/api/decide", {"record": vf["sample"][0], "stage": "vs", "decision": "include", "as": "NH"})
    assert e.status == 400 and "not in a verification sample of yours" in e.message
    o = screening.ta_outcomes(p, "main")
    assert all(o[r]["status"] == "forward" and o[r]["how"] == screening.VERIFIED_ON for r in vf["sample"])
    assert all(o[r]["status"] == "excluded" for r in both_excluded)
    # ... and with no logged draw either there is no sample, so nothing it found counts
    forget_the_draw(R["folder"])
    o = screening.ta_outcomes(Project(R["folder"]), "main")
    assert all(o[r]["status"] == "excluded" for r in vf["sample"])
    assert c.task("verify") is None and c.overview()["verifying"] is None


def test_the_data_checks_and_a_record_s_links_say_nothing_about_full_texts_to_the_verifier(lead, monkeypatch):
    """Only a record that reached full text can have a PDF, or a free copy that was looked for. The records mixed into
    a sample did; the sampled ones did not. So the checks of the PDFs (which name records), their repairs (whose
    answers differ for a record with a PDF) and the link to a copy that was found are kept from the verifier."""
    from realistic import make_pdf
    from reviewdesk import fulltext
    c = lead
    R = single_screened(c)
    recs = {r["id"]: r for r in c.get("/api/records", limit=1000)["records"]}
    with_pdf, with_link = sorted(R["nh_yes"])[:2]
    r = recs[with_pdf]
    c.upload("/api/fulltext/upload", make_pdf(r["title"], r.get("doi", ""), "Emergency Medicine Journal", r.get("authors", "")), record=with_pdf)
    monkeypatch.setattr(fulltext, "search_results", lambda p: {with_link: {"status": "link_only", "url": "https://example.org/paper"}})
    c.post("/api/screening/verify", {"set": "main", "size": 20, "decoys": 5, "reviewer": "TK"})
    vf = state(R["folder"])["verify"]
    assert {with_pdf, with_link} <= set(vf["decoy"])
    held = [os.path.join(d, n) for d, _, names in os.walk(R["folder"]) for n in names if n == with_pdf + ".pdf"]
    assert len(held) == 1
    os.remove(held[0])                     # the PDF is missing from the shared folder (not synced yet, say)
    c.be("NH")
    rep = c.get("/api/integrity")
    assert rep["pdf_checks_hidden"] is False and c.get("/api/app")["project"]["data_problems"] == 1
    assert [x["kind"] for x in rep["report"]["problems"]] == ["pdf"] and with_pdf in rep["report"]["problems"][0]["text"]
    assert c.get("/api/record", id=with_link)["links"]["found"] == "https://example.org/paper"
    c.be("TK")
    st, _, body = c.request("GET", "/api/integrity")
    rep = json.loads(body)
    assert st == 200 and rep["report"]["problems"] == [] and rep["pdf_checks_hidden"] is True
    assert c.get("/api/app")["project"]["data_problems"] == 0      # (nor counted on their pages)
    assert not any(('"' + r + '"').encode() in body or (r + " ").encode() in body for r in vf["order"])
    for kind in ("pdf", "adopt", "keep", "takeoff", "stray"):
        for rid in (with_pdf, vf["sample"][0]):                # the same answer for a record with a PDF and one without
            e = c.error("POST", "/api/integrity/fix", {"kind": kind, "record": rid})
            assert e.status == 409 and "You are screening a verification sample" in e.message, (kind, rid)
    assert not [n for d, _, names in os.walk(R["folder"]) for n in names if n.startswith(with_pdf + ".pdf")]     # nothing was moved
    views = {rid: c.get("/api/record", id=rid) for rid in vf["order"]}
    assert {(v["pdf"], v["links"]["found"]) for v in views.values()} == {(False, "")}
    # what a record's view holds beside the record itself is the same for every record of the sample
    own = lambda v: json.dumps({k: (sorted(x for x in v[k] if v[k][x]) if k == "links" else v[k]) for k in ("links", "mine", "pdf")}, sort_keys=True)   # noqa: E731
    by_kind = {kind: {own(views[rid]) for rid in vf[kind] if bool(recs[rid].get("doi")) == bool(recs[vf["sample"][0]].get("doi"))} for kind in ("sample", "decoy")}
    assert by_kind["sample"] == by_kind["decoy"] and len(by_kind["sample"]) == 1
    # when they have finished, both are theirs to see like anyone's
    screen_all(c, "TK", lambda r: "exclude", "verify", stage="vs")
    assert c.get("/api/record", id=with_link)["links"]["found"] == "https://example.org/paper"
    rep = c.get("/api/integrity")
    assert [x["kind"] for x in rep["report"]["problems"]] == ["pdf"] and rep["pdf_checks_hidden"] is False


def test_the_full_text_disagreements_are_not_listed_for_the_verifier(lead):
    """Once both full-text reviewers have voted on every report, the comparison of their votes lists each disagreement
    by record. Only records at full text are on it: the records mixed into a sample are, the sampled ones are not."""
    c = lead
    R = single_screened(c)
    c.be("CH")
    at_ft = [x["id"] for x in c.get("/api/fulltext")["rows"]]
    assert set(R["nh_yes"]) <= set(at_ft) and len(at_ft) == 8
    for who, exclude in (("NH", set()), ("CH", set(sorted(R["nh_yes"])[:3]))):        # they disagree on three of NH's five
        c.be(who)
        for rid in at_ft:
            c.decide(rid, "ft", "exclude" if rid in exclude else "include", reason="Wrong population" if rid in exclude else "")
    c.be("CH")
    c.post("/api/screening/verify", {"set": "main", "size": 20, "decoys": 5, "reviewer": "TK"})
    vf = state(R["folder"])["verify"]
    assert set(vf["decoy"]) == set(R["nh_yes"])
    c.be("NH")
    listed = {x["id"] for x in c.get("/api/agreement", scope="fulltext")["disagreements"]}
    assert listed == set(sorted(R["nh_yes"])[:3])                           # (anyone else may read it, as before)
    c.be("TK")
    st, _, body = c.request("GET", "/api/agreement", params={"scope": "fulltext"})
    assert st == 409 and json.loads(body)["verifying"] is True and not any(r.encode() in body for r in vf["order"])
    for scope in ("sample", "remainder", "calibration"):                   # the others say nothing about these records
        st, _, body = c.request("GET", "/api/agreement", params={"scope": scope})
        assert st == 200 and not any(('"' + r + '"').encode() in body for r in vf["order"]), scope
    screen_all(c, "TK", lambda r: "exclude", "verify", stage="vs")
    assert {x["id"] for x in c.get("/api/agreement", scope="fulltext")["disagreements"]} == listed


def test_the_data_checks_still_answer_when_the_state_file_is_damaged(lead):
    """Whether someone is screening a sample is read from the state. With that file damaged the data checks must still
    answer, with the damaged file as their problem: that is where it is put right."""
    c = lead
    R = drawn(c, size=6, decoys=2)
    path = os.path.join(R["folder"], "state", "state.json")
    good = read_json(path)
    for who in ("TK", "CH", "NH"):                             # the verifier, the lead, anyone
        c.be(who)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"sets": {"main": ')
        st, _, body = c.request("GET", "/api/integrity")
        rep = json.loads(body)
        assert st == 200 and [x["kind"] for x in rep["report"]["problems"]] == ["damaged"], (who, body[:300])
        assert rep["pdf_checks_hidden"] is False
        write_json(path, good)
        assert c.get("/api/integrity")["pdf_checks_hidden"] is (who == "TK")


def test_a_lead_who_verifies_is_not_shown_the_ai_s_answers_on_known_studies(lead):
    """With the AI behind the single reviewer, every sampled record is one the AI would not keep, and most of those
    mixed in are ones it kept: the AI's answer on a record of the sample says which kind it is."""
    c = lead
    R = drawn(c, size=20, decoys=4, reviewer="CH")
    vf = R["vf"]
    t = titles(c)
    c.settings(known_studies=[{"title": t[vf["sample"][0]]}, {"title": t[vf["decoy"][0]]}, {"doi": "10.9999/not-in-the-records"}])
    known = c.get("/api/ai")["known"]
    assert [k.get("record") for k in known] == [vf["sample"][0], vf["decoy"][0], None]
    assert all(set(k) == {"known", "record", "hidden", "hidden_why"} and k["hidden_why"] == "verifying" for k in known)
    assert [k["hidden"] for k in known] == [True, True, False]
    screen_all(c, "CH", lambda r: "exclude", "verify", stage="vs")
    known = c.get("/api/ai")["known"]
    assert (known[0]["A"], known[1]["A"]) == ("EXCLUDE", "INCLUDE") and "hidden" not in known[0]


def test_the_lead_can_end_a_sample_that_cannot_be_finished(lead):
    c = lead
    R = drawn(c, size=20, decoys=4)
    vf = R["vf"]
    lost_in = [r for r in vf["sample"] if r in R["missed_lost"]]
    first = lost_in + [r for r in vf["order"] if r not in lost_in][:9]
    c.be("TK")
    for rid in first:
        c.decide(rid, "vs", "include" if rid in R["eligible"] else "exclude")
    before = c.overview()["sets"]["main"]["counts"]
    e = c.error("POST", "/api/screening/verify/end", {"set": "main", "reason": "I would rather stop", "as": "TK"})
    assert e.status == 403 and "Only the project lead" in e.message
    c.be("CH")
    for body, words in (({"reason": ""}, "Say why the sample cannot be finished"), ({"reason": "  x "}, "Say why"),
                        ({"set": "update", "reason": "gone away"}, "No verification sample has been drawn")):
        e = c.error("POST", "/api/screening/verify/end", {"set": "main", **body})
        assert e.status == 400 and words in e.message, body
    e = c.error("POST", "/api/screening/verify/end", {"set": "main", "reason": 5})
    assert e.status == 400 and "could not read that request" in e.message
    assert "ended" not in state(R["folder"])["verify"]
    out = c.post("/api/screening/verify/end", {"set": "main", "reason": "Tane left the team on 3 March.", "as": "CH"})["verify"]
    assert out["complete"] is True and out["ended"] is True and out["done"] == len(first) and out["ended_why"] == "Tane left the team on 3 March."
    res = out["results"]
    n = sum(1 for r in first if r in vf["sample"])
    assert res["ended"] is True and res["sent_on"]["n"] == n and res["sent_on"]["k"] == len(lost_in) and res["not_sampled"] == 39 - n
    assert res["drawn"] == {"sample": 20, "mixed_in": 4}
    after = c.overview()["sets"]["main"]["counts"]
    assert after["forward"] == before["forward"] + len(lost_in)          # what was found is not lost with the sample
    text = c.get("/api/prisma")["summary"]
    assert (f"The sample was ended as it stood by Dr Colin Hawthorne on {exports_day(out)}, when Tane Kereama had screened "
            f"{len(first)} of the 24 records (Tane left the team on 3 March); the figures are for the records screened, and the "
            "rest count as not checked.") in text
    assert f"Tane Kereama would have sent on {len(lost_in)} of the {n} (" in text
    log = c.get("/api/audit")["entries"][-1]
    assert log["action"] == evaluation.END and log["details"] == {"set": "main", "screened": len(first), "of": 24,
                                                                  "reason": "Tane left the team on 3 March."}
    e = c.error("POST", "/api/screening/verify/end", {"set": "main", "reason": "again"})
    assert e.status == 400 and "already finished" in e.message
    # for its verifier it is over: no task, no more votes, and the pages are open again
    c.be("TK")
    assert c.task("verify") is None and c.queue("verify") == [] and c.overview()["verifying"] is None
    e = c.error("POST", "/api/decide", {"record": vf["order"][-1], "stage": "vs", "decision": "include", "as": "TK"})
    assert e.status == 400 and "has been ended by the project lead" in e.message
    assert c.request("GET", "/api/fulltext")[0] == 200
    assert c.get("/api/audit")["entries"][-1]["details"] == {"set": "main", "reason": "Tane left the team on 3 March."}
    v = c.overview()["sets"]["main"]["verify"]
    assert v["ended"] is True and v["complete"] is True and "results" not in v
    # what everyone but the lead reads says that it was ended too (without how far it had got: that is the lead's)
    for who in ("TK", "NH"):
        c.be(who)
        para = next(x for x in c.get("/api/prisma")["summary"].split("\n") if x.startswith("To check the records that left the review"))
        assert (f"The sample was ended as it stood by Dr Colin Hawthorne on {exports_day(out)}, before Tane Kereama had screened "
                "every record of it (Tane left the team on 3 March); the records not screened count as not checked. "
                "(The verification sample's figures are shown to the project lead.)") in para
        assert not re.search(r"\d", re.sub(r"on \d+ \w+ \d{4}", "on a day", para).replace("3 March", "")), para
        assert b"The sample was ended as it stood" in c.download("summary")[2]
    # a vote that reaches the files after the end all the same (their computer had not heard that it was ended)
    # counts for nothing: not one on a record they had not reached, not a change to one they had
    import time
    time.sleep(0.02)
    p = Project(R["folder"])
    late = [next(r for r in vf["sample"] if r not in first), next(r for r in first if r in vf["sample"] and r not in lost_in)]
    for rid in late:
        p.add_decision("TK", rid, "vs", "include")
    c.be("CH")
    v = verify_of(c)
    assert v["done"] == len(first) and v["results"] == res
    assert c.overview()["sets"]["main"]["counts"] == after
    rows = [r for r in table(unzip(c.download("evaluation")[2])["votes.csv"]) if r["Stage"] == "verification sample"]
    assert sorted(r["Record"] for r in rows if r["Written after its sample was finished"] == "yes") == sorted(late)
    assert len(rows) == len(first) + 2


def test_the_vote_that_finishes_a_sample_has_to_say_so(lead):
    """The vote on the last record fixes every vote of the sample, so the page asks before it sends it. The server
    asks for the same ("finish"), because a second tab or another computer does not know how many records are left."""
    c = lead
    R = drawn(c, size=6, decoys=2)
    vf = R["vf"]
    c.be("TK")
    for rid in vf["order"][:-1]:
        out = c.decide(rid, "vs", "exclude", finish=False)
        assert out["sample_finished"] is False
    last = vf["order"][-1]
    assert c.decide(vf["order"][0], "vs", "include", finish=False)["sample_finished"] is False       # a change: not the last one
    e = c.error("POST", "/api/decide", {"record": last, "stage": "vs", "decision": "exclude", "as": "TK"})
    assert e.status == 409 and e.data["last_of_sample"] is True and "Nothing was saved yet" in e.message
    e = c.error("POST", "/api/decide", {"record": last, "stage": "vs", "decision": "exclude", "as": "TK", "finish": False})
    assert e.status == 409 and e.data["last_of_sample"] is True
    assert c.overview()["verifying"] == {"set": "main", "left": 1, "total": 8} and c.request("GET", "/api/fulltext")[0] == 409
    assert c.decide(last, "vs", "exclude")["sample_finished"] is True
    assert c.overview()["verifying"] is None
    e = c.error("POST", "/api/decide", {"record": last, "stage": "vs", "decision": "include", "as": "TK", "finish": True})
    assert e.status == 409 and e.data["sample_finished"] is True
    assert "sample_finished" not in c.decide(R["rest"][0], "ta", "exclude")        # (only a vote on a sample says so)


def test_a_vote_can_be_changed_until_the_sample_is_finished_and_is_fixed_from_then_on(lead):
    """Until the last record has had its first vote the verifier has seen nothing of what was decided before, and may
    change any vote: the latest counts. From that moment they can see how each record was decided, so a later vote is
    refused; one that reaches the files all the same (from a computer that had not yet heard, or a file edited by
    hand) counts for nothing, and the evaluation data says which rows those are."""
    import time
    c = lead
    R = drawn(c, size=39, decoys=4)                 # every record that left on the one vote
    vf, folder = R["vf"], R["folder"]
    a, b = sorted(r for r in vf["sample"] if r in R["missed_lost"])        # the two records only this sample can find
    c.be("TK")
    c.decide(a, "vs", "include")
    c.decide(a, "vs", "exclude")               # a change of mind with nothing seen: allowed, and the latest counts
    c.decide(b, "vs", "exclude")
    c.decide(b, "vs", "maybe")
    assert c.task("verify")["left"] == 41 and [x["done"] for x in c.queue("verify")].count(True) == 2
    for rid in vf["order"]:
        if rid not in (a, b):
            c.decide(rid, "vs", "exclude")
    assert c.task("verify") is None and c.queue("verify") == []
    o = screening.ta_outcomes(Project(folder), "main")
    assert o[a]["status"] == "excluded" and (o[b]["status"], o[b]["how"]) == ("forward", screening.VERIFIED_ON)
    c.be("CH")
    res = verify_of(c)["results"]
    counts = c.overview()["sets"]["main"]["counts"]
    assert (res["sent_on"]["k"], res["sent_on"]["n"]) == (1, 39) and res["sent_on_records"] == [b]
    # from now on TK can open the full-text list and the decisions, so the server takes no more votes on the sample
    c.be("TK")
    assert c.request("GET", "/api/fulltext")[0] == 200
    for rid, d in ((a, "include"), (b, "exclude"), (vf["decoy"][0], "include")):
        e = c.error("POST", "/api/decide", {"record": rid, "stage": "vs", "decision": d, "as": "TK"})
        assert e.status == 409 and "is finished, so nothing was saved: its votes were fixed when you voted on its last record" in e.message
    assert screening.ta_outcomes(Project(folder), "main")[b]["status"] == "forward"
    # ... and rows that reach the files all the same change nothing
    time.sleep(0.02)
    p = Project(folder)
    p.add_decision("TK", a, "vs", "include")
    p.add_decision("TK", b, "vs", "exclude")
    p = Project(folder)
    V = screening.votes(p)
    assert screening.latest(V, "vs", a, "TK")["d"] == "include"            # (it is on file, and it is the latest row)
    counted, done = screening.verify_votes(screening.verify_of(state(folder)), V)
    assert done is True and counted[a]["d"] == "exclude" and counted[b]["d"] == "maybe" and len(counted) == 43
    o = screening.ta_outcomes(p, "main")
    assert o[a]["status"] == "excluded" and o[b]["status"] == "forward"
    c.be("CH")
    assert verify_of(c)["results"] == res and c.overview()["sets"]["main"]["counts"] == counts
    files = unzip(c.download("evaluation")[2])
    votes = [r for r in table(files["votes.csv"]) if r["Stage"] == "verification sample"]
    late = [r for r in votes if r["Written after its sample was finished"] == "yes"]
    assert sorted((r["Record"], r["Decision"], r["Vote number"], r["Latest"]) for r in late) == sorted([(a, "include", "3", "yes"), (b, "exclude", "3", "yes")])
    assert len(votes) == 43 + 2 + 2 and all(r["Written after its sample was finished"] == "" for r in votes if r not in late)
    ver = {r["Record"]: r for r in table(files["verification.csv"])}
    assert (ver[a]["Verifier's vote"], ver[a]["Verifier's votes on it"]) == ("exclude", "2")
    assert (ver[b]["Verifier's vote"], ver[b]["Verifier's votes on it"]) == ("maybe", "2")
    assert ver[vf["decoy"][0]]["Verifier's votes on it"] == "1"
    rec = {r["Record"]: r for r in table(files["records.csv"])}
    assert rec[a]["Verifier's vote"] == "exclude" and rec[b]["Verifier's vote"] == "maybe"
    readme = files["README.txt"].decode("utf-8")
    assert "Written after its sample was finished" in readme and "such a vote counts for nothing" in readme
    # the clock of the verifier's computer put back while they screened does not turn the order round: what counts is
    # the order their rows were written in
    V2 = {"vs": {"r1": {"TK": {"all": [{"t": "2026-03-01T10:30:00.000+00:00", "d": "exclude"}, {"t": "2026-03-01T10:20:00.000+00:00", "d": "include"}], "at": [0, 3]}},
                 "r2": {"TK": {"all": [{"t": "2026-03-01T10:25:00.000+00:00", "d": "exclude"}], "at": [2]}}}}
    tidy = {"reviewer": "TK", "order": ["r1", "r2"], "sample": ["r1", "r2"], "decoy": [], "ended": None}
    counted, done = screening.verify_votes(tidy, V2)
    assert done is True and counted["r1"]["d"] == "exclude"      # r1's second vote was written after r2's first: too late
    V2["vs"]["r1"]["TK"]["at"] = [0, 1]
    assert screening.verify_votes(tidy, V2)[0]["r1"]["d"] == "include"     # written before it: a change of mind in time


def exports_day(out) -> str:
    from reviewdesk import exports, util
    return exports._day(util.now())


def test_someone_who_has_left_the_project_is_a_letter_in_the_copy_for_sharing_too(lead):
    c = lead
    team = TEAM + [{"id": "XQ", "name": "Xanthe Quill", "role": "reviewer"}]
    reference_project(c, str(c.tmp / "OneDrive"), reviewers=team,
                      screening=reference_plan(calibration_size=0, mode="single", ai_second_screener=False))
    c.add_import(ris(*tiny_records(12, seed=5)), "medline.ris", "MEDLINE")
    c.process()
    c.settings(screening={"record_time": True})
    c.post("/api/screening/start", {"set": "main"})
    c.be("XQ")
    c.post("/api/timing/mine", {"on": False, "as": "XQ"})
    rid = c.get("/api/records")["records"][0]["id"]
    c.decide(rid, "ta", "include")                           # (anyone may vote; the plan did not ask her to)
    c.be("CH")
    files = unzip(c.download("evaluation_share")[2])
    votes, recs = (table(files[n]) for n in ("votes.csv", "records.csv"))
    assert [(v["Reviewer"], v["Part in the plan"]) for v in votes] == [("Reviewer D", "")]       # a vote the plan did not ask for
    assert next(r for r in recs if r["Record"] == rid)["Vote of Reviewer D"] == "include"
    c.settings(reviewers=TEAM)                               # she leaves the project (her votes no longer count for anything)
    files = unzip(c.download("evaluation_share")[2])
    blob = b"".join(files.values()).decode("utf-8")
    assert not re.search(r"(?<![A-Za-z0-9])(XQ|NH|CH|TK)(?![A-Za-z0-9])", blob) and "Xanthe" not in blob
    events = table(files["events.csv"])
    assert [e["Who"] for e in events if e["Action"] == timing.OWN] == ["Reviewer D"]            # still a letter, never her initials
    assert [x["who"] for x in json.loads(files["summary.json"])["time_recording"]["changes"]] == ["Reviewer A", "Reviewer D"]
    team_copy = unzip(c.download("evaluation")[2])
    assert [e["Who"] for e in table(team_copy["events.csv"]) if e["Action"] == timing.OWN] == ["XQ"]
