"""Which votes count for agreement (kappa) without comparing two computers' clocks; a figure that changes after the
agreement rule was applied; and who assesses full texts fixed when screening starts.

Each vote now records, as it is written, what its voter could know as their own computer had the files at that moment
(screening.compare_state); agreement uses that mark, never one computer's clock against another's. A row without the
mark (every row an earlier version wrote) is judged by time, exactly as before."""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os

import pytest

from helpers import Desk, FakeOneDrive, on_computer, read_json, record_plan, reference_plan, reference_project, ris, \
    tiny_records
from reviewdesk import readiness, screening, stats, util
from reviewdesk.project import MARKER, Project, ProjectError

PEOPLE = [{"id": "CH", "name": "Dr Colin Hawthorne", "role": "lead"}, {"id": "NH", "name": "Nadine Hayes", "role": "reviewer"}]
TRUE = [dt.datetime(2026, 11, 9, 10, 0, tzinfo=dt.timezone.utc).timestamp()]     # the true time every computer shares


def tick(seconds: float) -> None:
    TRUE[0] += seconds


@contextlib.contextmanager
def clock(skew: float = 0.0):
    """Inside: this computer's clock reads the true time + skew (seconds)."""
    old = util.now
    util.now = lambda: dt.datetime.fromtimestamp(TRUE[0] + skew, dt.timezone.utc).isoformat(timespec="milliseconds")
    try:
        yield
    finally:
        util.now = old


class Computer:
    """One person at one computer with its own clock."""

    def __init__(self, desk: Desk, skew: float = 0.0):
        self.d, self.skew = desk, skew

    @property
    def p(self):
        return self.d.p

    def do(self, fn, *a, **kw):
        with clock(self.skew):
            return self.d.do(fn, *a, **kw)

    def vote(self, rec: str, d: str, stage: str = "ta", old: bool = False) -> dict:
        """A vote through the vote handler's path (marked), or as an earlier version wrote it (old: no mark)."""
        tick(1)
        if old:
            return self.do(self.p.add_decision, self.d.who, rec, stage, d)
        return self.do(screening.cast, self.p, self.d.who, rec, stage, d)

    def reopen(self):
        with clock(self.skew):
            return self.d.reopen()


def new_project(tmp_path, n: int = 40, **plan) -> Desk:
    home, folder = str(tmp_path / "home-CH"), str(tmp_path / "OneDrive" / "review")
    with on_computer(home), clock():
        p = Project.create(folder, "AI in emergency department triage", "Dr Colin Hawthorne", "CH")
        p.update_settings("CH", {"question": "Q", "criteria": {"include": ["AI triage"], "exclude": [], "rules": []},
                                 "reviewers": PEOPLE, "fulltext": {"mode": "dual"},
                                 "screening": reference_plan(**dict({"calibration_size": 0, "ai_second_screener": False}, **plan))})
        record_plan(p)
        p.add_import("CH", "medline.ris", ris(*tiny_records(n, seed=61)), {"set": "main", "database": "MEDLINE"})
        p.process_imports("CH")
        screening.start(p, "CH", "main")
    return Desk(home, folder, "CH")


def two(tmp_path, ch_skew: float = 0.0, nh_skew: float = 0.0, **plan) -> tuple:
    ch = Computer(new_project(tmp_path, **plan), ch_skew)
    nh = Computer(Desk(str(tmp_path / "home-NH"), ch.d.folder, "NH"), nh_skew)
    return ch, nh


def kappa(c: Computer, scope: str = "sample") -> dict:
    c.reopen()
    return c.do(screening.agreement, c.p, "main", scope)


# ------------------------------------------------------------------------------------------------ the auditor's case
@pytest.mark.parametrize("who,skew", [("NH", 900), ("NH", -900), ("CH", 900), ("CH", -900), ("NH", 0)])
def test_kappa_on_the_sample_does_not_depend_on_either_computers_clock(tmp_path, who, skew):
    """sync S1: 6 real disagreements in 20; CH changes his 6 votes ten minutes after both have finished. With either
    clock 15 minutes fast or slow, the kappa is the independent 0.40, and the rule keeps both reviewers screening."""
    ch, nh = two(tmp_path, ch_skew=skew if who == "CH" else 0, nh_skew=skew if who == "NH" else 0, sample_fraction=0.5)
    sample = ch.p.state_ro()["sets"]["main"]["sample"]
    for k, r in enumerate(sample):
        tick(20)
        nh.vote(r, "include" if k < 10 else "exclude")
        ch.vote(r, "include" if k < 4 else "exclude")
    a0 = kappa(ch)
    assert a0["complete"] and len(a0["disagreements"]) == 6 and round(a0["stats"]["kappa"], 3) == 0.4
    tick(600)
    for d in a0["disagreements"]:
        ch.vote(d["id"], "include")
    a1 = kappa(ch)
    assert round(a1["stats"]["kappa"], 3) == 0.4 and a1["changed_before_compare"] == 0 and not a1["meets_threshold"]
    rows = [d for d in ch.p.decisions()["CH"] if d["s"] == "ta"]
    assert [d["cmp"] for d in rows[-6:]] == [2] * 6               # changed once the comparison was open
    sv = ch.do(screening.apply_sample_decision, ch.p, "CH", "main")
    assert sv["remainder_mode"] == "dual" and round(sv["sample_result"]["kappa"], 3) == 0.4
    assert ch.do(screening.sample_recount, ch.p, "main") is None


def test_a_slip_corrected_before_the_sample_was_complete_on_that_computer_counts(tmp_path):
    ch, nh = two(tmp_path, nh_skew=900, sample_fraction=0.5)
    sample = ch.p.state_ro()["sets"]["main"]["sample"]
    for r in sample:
        nh.vote(r, "exclude")
    nh.vote(sample[0], "include")                         # corrected before CH has finished: nothing could be compared
    for r in sample:
        ch.vote(r, "include" if r == sample[0] else "exclude")
    a = kappa(ch)
    assert a["stats"]["agreement"] == 1.0 and a["changed_before_compare"] == 1


# ------------------------------------------------------------------------------------------------ record by record
@pytest.mark.parametrize("skew", [900, -900, 0])
def test_a_slip_corrected_by_the_second_voter_within_two_minutes_still_counts(tmp_path, skew):
    ch, nh = two(tmp_path, nh_skew=skew, mode="dual")
    order = ch.p.state_ro()["sets"]["main"]["order"]
    r1, r2 = order[:2]
    ch.vote(r1, "include")
    ch.vote(r2, "include")
    tick(30)
    nh.vote(r1, "exclude")                                # NH votes second (mark 1) and slips
    tick(60)
    nh.vote(r1, "include")                                # corrected a minute later: counts
    nh.vote(r2, "exclude")
    tick(300)
    nh.vote(r2, "include")                                # five minutes later, having seen the outcome: does not count
    marks = [d["cmp"] for d in nh.p.decisions()["NH"] if d["s"] == "ta"]
    assert marks == [1, 2, 1, 2]
    a = kappa(ch, "remainder")
    assert a["stats"]["both_yes"] == 1 and a["stats"]["only_a"] + a["stats"]["only_b"] == 1
    assert [d["id"] for d in a["disagreements"]] == [r2] and a["changed_before_compare"] == 1


@pytest.mark.parametrize("skew", [900, -900])
def test_a_vote_changed_before_the_other_reviewer_voted_still_counts(tmp_path, skew):
    """NH changes her vote before CH votes; with her clock 15 minutes fast the old rule would have dropped the change
    (its time stamp is after CH's vote); with it slow, CH's correction would have been taken for one made blind."""
    ch, nh = two(tmp_path, nh_skew=skew, mode="dual")
    r = ch.p.state_ro()["sets"]["main"]["order"][0]
    nh.vote(r, "exclude")
    tick(60)
    nh.vote(r, "include")                                  # still nothing to compare with (mark 0)
    tick(60)
    ch.vote(r, "include")
    tick(900)
    ch.vote(r, "exclude")                                  # long after he could see the outcome
    assert [d["cmp"] for d in nh.p.decisions()["NH"] if d["s"] == "ta"] == [0, 0]
    a = kappa(ch, "remainder")
    assert a["stats"]["both_yes"] == 1 and a["disagreements"] == []


def test_full_text_votes_are_marked_and_counted_the_same_way(tmp_path):
    ch, nh = two(tmp_path, nh_skew=900, mode="dual")
    order = ch.p.state_ro()["sets"]["main"]["order"][:3]
    for r in order:
        ch.vote(r, "include")
        nh.vote(r, "include")
    for r in order:
        nh.vote(r, "include", "ft")
        ch.vote(r, "exclude", "ft")
    tick(600)
    for r in order:
        ch.vote(r, "include", "ft")                       # changed after both had voted: kept, not used for agreement
    a = kappa(ch, "fulltext")
    assert a["stats"]["n"] == 3 and len(a["disagreements"]) == 3 and a["changed_before_compare"] == 0


# ------------------------------------------------------------------------------------------------ earlier rows
def old_rule(V, stage, recs, a, b, whole):
    """The rule of ReviewDesk 1.3.0 before this change, written out again: the votes it counted."""
    both = [r for r in recs if screening.first(V, stage, r, a) and screening.first(V, stage, r, b)]
    if whole:
        t = max((util.ts(screening.first(V, stage, r, w)["t"]) for r in both for w in (a, b)), default=0.0)
        cut = {r: (t, t) for r in both}
    else:
        cut = {}
        for r in both:
            ta, tb = util.ts(screening.first(V, stage, r, a)["t"]), util.ts(screening.first(V, stage, r, b)["t"])
            s = max(ta, tb)
            cut[r] = (s + screening.SLIP_SECONDS if ta >= tb else s, s + screening.SLIP_SECONDS if tb > ta else s)
    return {r: (screening.as_of(V, stage, r, a, cut[r][0]), screening.as_of(V, stage, r, b, cut[r][1])) for r in both}


def test_rows_without_the_mark_give_exactly_the_kappa_they_gave_before(tmp_path):
    """Every row an earlier version wrote has no mark: the time rule judges it, unchanged (so, with the clocks of the
    auditor's case, the figure is still the one 1.3.0 gave; only new votes are freed from the clocks)."""
    ch, nh = two(tmp_path, nh_skew=900, sample_fraction=0.5)
    sample = ch.p.state_ro()["sets"]["main"]["sample"]
    for k, r in enumerate(sample):
        tick(20)
        nh.vote(r, "include" if k < 10 else "exclude", old=True)
        ch.vote(r, "include" if k < 4 else "exclude", old=True)
    nh.vote(sample[12], "include", old=True)
    tick(600)
    for k, r in enumerate(sample[4:10]):
        ch.vote(r, "include", old=True)
    a = kappa(ch)
    assert not any("cmp" in d for rows in ch.p.decisions().values() for d in rows)
    V = screening.votes(ch.p)
    old = old_rule(V, "ta", sample, "NH", "CH", True)
    assert screening.independent(V, "ta", sample, "NH", "CH", True) == old
    pairs = [(x["d"] in screening.FWD, y["d"] in screening.FWD) for x, y in old.values()]
    assert a["stats"]["kappa"] == stats.kappa(stats.two_by_two(pairs))["kappa"]
    assert a["stats"]["kappa"] == 1.0              # as before: the old rows' time stamps decide, as they always did
    rest = [r for r in ch.p.state_ro()["sets"]["main"]["order"] if r not in sample]
    for r in rest[:6]:
        nh.vote(r, "exclude", old=True)
        tick(200)
        ch.vote(r, "include", old=True)
        tick(30)
        ch.vote(r, "exclude", old=True)
        nh.vote(r, "include", old=True)
    V = screening.votes(ch.p)
    assert screening.independent(V, "ta", rest, "NH", "CH", False) == old_rule(V, "ta", rest, "NH", "CH", False)


def test_mixed_rows_are_each_judged_by_their_own_rule(tmp_path):
    ch, nh = two(tmp_path, nh_skew=900, mode="dual")
    r1, r2, r3 = ch.p.state_ro()["sets"]["main"]["order"][:3]
    # r1: NH (an earlier version, clock 15 minutes fast) votes first; CH (this version) second, slips and corrects
    nh.vote(r1, "include", old=True)
    tick(60)
    ch.vote(r1, "exclude")
    tick(30)
    ch.vote(r1, "include")
    # r2: CH (this version) first, changes before NH votes; NH (earlier version) votes, then changes 10 minutes later
    ch.vote(r2, "exclude")
    ch.vote(r2, "include")
    tick(60)
    nh.vote(r2, "include", old=True)
    tick(600)
    nh.vote(r2, "exclude", old=True)
    # r3: NH's row has no mark and its time stamp (15 minutes fast) comes after CH's: by time it is the second vote
    nh.vote(r3, "include", old=True)
    tick(60)
    ch.vote(r3, "include")
    V = screening.votes(ch.p)
    ind = screening.independent(V, "ta", [r1, r2, r3], "NH", "CH", False)
    assert [ind[r1][0]["d"], ind[r1][1]["d"]] == ["include", "include"]          # CH's slip (mark 2, within 30 s) counts
    assert [ind[r2][0]["d"], ind[r2][1]["d"]] == ["include", "include"]          # NH's late change, by time, does not
    assert ind[r2][1] is not V["ta"][r2]["CH"]["first"]                          # CH's change before NH voted (mark 0) does
    assert ind[r3] == (V["ta"][r3]["NH"]["first"], V["ta"][r3]["CH"]["first"])


def test_a_malformed_mark_is_read_as_no_mark(tmp_path):
    ch, nh = two(tmp_path, mode="dual")
    r = ch.p.state_ro()["sets"]["main"]["order"][0]
    nh.vote(r, "include")
    tick(5)
    ch.do(ch.p.add_decision, "CH", r, "ta", "exclude", extra={"cmp": "2"})
    tick(5000)
    ch.do(ch.p.add_decision, "CH", r, "ta", "include", extra={"cmp": True})
    ind = screening.independent(screening.votes(ch.p), "ta", [r], "NH", "CH", False)
    assert ind[r][1]["d"] == "exclude"             # by time, as an earlier version's row: the late change does not count


# ------------------------------------------------------------------------------------------------ what a vote records
def test_the_mark_says_what_this_computer_had_when_the_vote_was_written(tmp_path):
    """Two computers, each with its own copy of the folder: NH's vote is marked by what her copy holds, not by what
    has happened elsewhere."""
    copies = {"CH-PC": str(tmp_path / "ch" / "review"), "NH-PC": str(tmp_path / "nh" / "review")}
    od = FakeOneDrive(str(tmp_path / "cloud"), copies)
    home = str(tmp_path / "home-CH")
    with on_computer(home), clock():
        p = Project.create(copies["CH-PC"], "AI triage", "Dr Colin Hawthorne", "CH")
        p.update_settings("CH", {"question": "Q", "criteria": {"include": ["AI triage"], "exclude": [], "rules": []},
                                 "reviewers": PEOPLE, "screening": reference_plan(mode="dual", calibration_size=0,
                                                                                  ai_second_screener=False)})
        record_plan(p)
        p.add_import("CH", "medline.ris", ris(*tiny_records(8, seed=61)), {"set": "main", "database": "MEDLINE"})
        p.process_imports("CH")
        screening.start(p, "CH", "main")
    od.sync()
    ch = Computer(Desk(home, copies["CH-PC"], "CH"))
    nh = Computer(Desk(str(tmp_path / "home-NH"), copies["NH-PC"], "NH"))
    r = ch.p.state_ro()["sets"]["main"]["order"][0]
    assert ch.vote(r, "include") == {k: v for k, v in ch.p.decisions()["CH"][-1].items() if k != "cmp"}
    assert ch.p.decisions()["CH"][-1]["cmp"] == 0
    nh.vote(r, "exclude")                                  # CH's vote has not reached her copy yet
    od.sync()
    nh.reopen()
    nh.vote(r, "include")                                  # now it has: both have voted on this record
    assert [d["cmp"] for d in nh.p.decisions()["NH"]] == [0, 2]
    od.sync()
    a = kappa(ch, "remainder")
    assert a["disagreements"][0]["NH"]["d"] == "exclude"    # her change once both votes were there does not count


# ------------------------------------------------------------------------------------------------ blinding
def test_no_page_or_download_shows_a_reviewer_the_mark(desk, tmp_path):
    c = desk.client()
    folder = reference_project(c, str(tmp_path / "OneDrive"), screening=reference_plan(mode="dual", calibration_size=0,
                                                                                ai_second_screener=False))
    c.add_import(ris(*tiny_records(6, seed=3)), "medline.ris", "MEDLINE")
    c.process()
    c.post("/api/screening/start", {"set": "main"})
    order = [x["id"] for x in c.queue("screen")]
    saved = c.decide(order[0], "ta", "include")             # CH first (mark 0)
    c.be("NH")
    saved_nh = c.decide(order[0], "ta", "exclude")          # NH second: her row says CH had voted (mark 1)
    for got in (saved, saved_nh):
        assert "cmp" not in json.dumps(got)
    for path in ("/api/record?id=" + order[0], "/api/queue?task=screen&set=main", "/api/tasks", "/api/overview",
                 "/api/conflicts", "/api/agreement?scope=remainder"):
        st, _, body = c.request("GET", path)
        assert st == 200 and b"cmp" not in body, path
    c.be("CH")
    for r in order:
        for who in ("CH", "NH"):
            if c.me != who:
                c.be(who)
            if not any(x["id"] == r and x["done"] for x in c.queue("screen")):
                c.decide(r, "ta", "exclude")
    c.be("CH")
    st, _, data = c.download("decisions")
    assert st == 200 and b"cmp" not in data and data.count(b"\n") > 6
    rows = [json.loads(x) for f in os.listdir(os.path.join(folder, "decisions"))
            for x in open(os.path.join(folder, "decisions", f), encoding="utf-8") if x.strip()]
    assert {d.get("cmp") for d in rows if d.get("r") == order[0]} == {0, 1}


# ------------------------------------------------------------------------------------------------ sync S7
def test_a_kappa_changed_by_votes_that_arrived_late_is_a_check_for_the_lead(tmp_path):
    """sync S7 (G1): NH corrects four slips before CH finishes, but her laptop is offline when the lead applies the
    rule. What was recorded stays; the lead is told that the votes on file now give another figure, and how to report it."""
    copies = {"CH-PC": str(tmp_path / "ch" / "review"), "NH-PC": str(tmp_path / "nh" / "review")}
    od = FakeOneDrive(str(tmp_path / "cloud"), copies)
    home = str(tmp_path / "home-CH")
    with on_computer(home), clock():
        p = Project.create(copies["CH-PC"], "AI triage", "Dr Colin Hawthorne", "CH")
        p.update_settings("CH", {"question": "Q", "criteria": {"include": ["AI triage"], "exclude": [], "rules": []},
                                 "reviewers": PEOPLE, "screening": reference_plan(calibration_size=0, ai_second_screener=False,
                                                                                  sample_fraction=0.5)})
        record_plan(p)
        p.add_import("CH", "medline.ris", ris(*tiny_records(40, seed=61)), {"set": "main", "database": "MEDLINE"})
        p.process_imports("CH")
        screening.start(p, "CH", "main")
    od.sync()
    ch = Computer(Desk(home, copies["CH-PC"], "CH"))
    nh = Computer(Desk(str(tmp_path / "home-NH"), copies["NH-PC"], "NH"))
    sample = ch.p.state_ro()["sets"]["main"]["sample"]
    for k, r in enumerate(sample):
        nh.vote(r, "include" if k < 8 else "exclude")
    od.sync()
    for r in sample[4:8]:
        nh.vote(r, "exclude")                              # corrected before CH finished; her laptop then goes offline
    for k, r in enumerate(sample):
        ch.vote(r, "include" if k < 8 else "exclude")
    ch.reopen()
    sv = ch.do(screening.apply_sample_decision, ch.p, "CH", "main")
    assert sv["remainder_mode"] == "single" and sv["sample_result"]["kappa"] == 1.0
    assert ch.do(screening.sample_recount_check, ch.p, "main") == ""
    od.sync()
    ch.reopen()
    nh.reopen()
    rc = ch.do(screening.sample_recount, ch.p, "main")
    assert rc["recorded"] == 1.0 and round(rc["now"], 3) == 0.545 and not rc["reaches_now"]
    st = ch.p.state_ro()["sets"]["main"]
    assert st["remainder_mode"] == "single" and st["sample_result"]["kappa"] == 1.0      # what the lead applied stays
    text = ch.do(screening.sample_recount_check, ch.p, "main")
    assert "0.55" in text and "1.00" in text and "below the threshold of 0.80" in text
    assert "arrive afterwards from another computer" in text and "Report both figures in the methods" in text
    assert "one reviewer screens the remaining records" in text and "—" not in text
    items = [x for s in ch.do(readiness.check, ch.p, "CH")["steps"] for x in s["missing"] + s["warnings"]
             if x["key"] == "kappa_recount"]
    assert len(items) == 1 and items[0]["text"] == text and not items[0]["blocks"]
    assert not [x for s in nh.do(readiness.check, nh.p, "NH")["steps"] for x in s["missing"] + s["warnings"] + s["later"]
                if x["key"] == "kappa_recount"]
    m = ch.do(screening.sample_recount_methods, ch.p, "main")
    assert m == ("The agreement rule was applied to the votes on file at the time, which gave a kappa of 1.00; votes cast "
                 "before then that arrived afterwards from another reviewer's computer give a kappa of 0.55 for the same "
                 "sample. The rule's outcome stands as it was applied.")


# ------------------------------------------------------------------------------------------------ methods MINOR 5
def _settings_file(p: Project) -> dict:
    return read_json(p.path(MARKER))


def test_who_assesses_full_texts_is_fixed_when_screening_starts(tmp_path):
    ch = Computer(new_project(tmp_path, n=8, mode="dual"))
    p = ch.p
    fixed = p.state_ro()["sets"]["main"]["plan_at_start"]
    assert fixed["ft_mode"] == "dual" and fixed["ft_fixed"] is True
    assert "ft_fixed" not in screening.plan(p, "main")
    with pytest.raises(ProjectError) as e:
        ch.do(p.update_settings, "CH", {"fulltext": {"mode": "single"}})
    assert "Screening has started, so the screening plan is fixed" in str(e.value) and "who assesses full texts" in str(e.value)
    ch.do(p.update_settings, "CH", {"fulltext": {"mode": "dual", "library_prefix": "x"}})      # saving the page as it is
    # an earlier version on another computer changes the setting anyway: the plan recorded at the start holds
    s = _settings_file(p)
    s["fulltext"]["mode"] = "single"
    with open(p.path(MARKER), "w", encoding="utf-8") as f:
        json.dump(s, f)
    p = ch.reopen()
    assert p.settings_ro()["fulltext"]["mode"] == "single"
    assert screening.ft_required(p, "main") == ["NH", "CH"] and screening.plan(p, "main")["ft_mode"] == "dual"
    assert screening.roles(p.settings_ro(), p.state_ro()["sets"]["main"], p.state_ro()["sets"]["main"]["plan_at_start"])["ft"] == ["NH", "CH"]


def test_a_set_started_by_an_earlier_version_reads_the_setting_as_before(tmp_path):
    ch = Computer(new_project(tmp_path, n=8, mode="dual"))
    st = ch.p.state()
    st["sets"]["main"]["plan_at_start"].pop("ft_fixed")              # as 1.2.1 and 1.3.0 recorded it
    ch.do(ch.p.save_state, st, "CH")
    s = _settings_file(ch.p)
    s["fulltext"]["mode"] = "single"
    with open(ch.p.path(MARKER), "w", encoding="utf-8") as f:
        json.dump(s, f)
    p = ch.reopen()
    assert p.state_ro()["sets"]["main"]["plan_at_start"]["ft_mode"] == "dual"
    assert screening.ft_required(p, "main") == ["NH"]               # the setting, as before (it can no longer change)


def test_the_methods_say_when_votes_that_arrived_late_give_another_kappa(tmp_path, monkeypatch):
    """The sentence screening.sample_recount_methods gives is printed in the methods summary and the screening agreement
    document, right after the rule's outcome (and nothing is printed when the figures agree)."""
    import io, re, zipfile
    import demo_review
    from reviewdesk import exports, reporting, screening as sc
    info = demo_review.build(str(tmp_path / "demo"))
    p = info["p"]
    assert sc.sample_recount_methods(p, "main") == ""
    assert "arrived afterwards from another" not in exports.methods_summary(p)
    words = "The agreement rule was applied to the votes on file at the time, which gave a kappa of 1.00; votes cast " \
            "before then that arrived afterwards from another reviewer's computer give a kappa of 0.55 for the same sample."
    monkeypatch.setattr(sc, "sample_recount_methods", lambda p, set_name="main": words if set_name == "main" else "")
    p._memo.clear()                                       # (the summary is kept until a project file changes)
    assert words in exports.methods_summary(p)
    xml = zipfile.ZipFile(io.BytesIO(reporting.agreement_docx(p, "CH"))).read("word/document.xml").decode("utf-8")
    assert words.replace("'", "&apos;") in xml or words in re.sub(r"<[^>]+>", "", xml).replace("&apos;", "'")
