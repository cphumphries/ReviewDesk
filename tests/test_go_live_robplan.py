"""The risk-of-bias plan: which logged plan is the latest must not depend on two computers' clocks, and a project that
already has assessments but no plan (begun with ReviewDesk 1.2 or earlier) is offered a draft from the tools in use."""
import json
import os

import pytest

import demo_review
from reviewdesk import robplan, util


@pytest.fixture()
def demo(tmp_path):
    info = demo_review.build(str(tmp_path / "demo"))
    return info["p"], info


def _audit_plan_entries(p):
    return [e for e in p.audit_entries() if e.get("action") in robplan.PLAN_ACTIONS]


def test_every_plan_entry_carries_the_plans_revision_number(demo):
    p, info = demo
    entries = _audit_plan_entries(p)
    assert entries and all(isinstance((e.get("details") or {}).get("rev"), int) for e in entries)
    assert robplan.outside_change(p) is None


def test_a_fast_clock_on_the_computer_that_recorded_the_earlier_plan_is_not_a_change_outside_reviewdesk(demo, monkeypatch):
    """The plan is recorded on a computer whose clock is ten minutes fast, then changed four minutes later (true time)
    on a computer whose clock is right. By the stamps the first entry is the 'last'; by the revision number it is not."""
    p, info = demo
    lead = next(r["id"] for r in p.settings_ro()["reviewers"] if r.get("role") == "lead")
    pl = robplan.stored(p.settings_ro())
    first = _audit_plan_entries(p)[-1]
    # change the plan now (a later revision), with a reason: screening has started in the demonstration review
    new = {"designs": [d for d in pl["designs"]][:-1] or pl["designs"], "tools": pl.get("tools") or {}}
    if len(new["designs"]) == len(pl["designs"]):                       # only one design: add another instead
        new["designs"] = pl["designs"] + [{"id": "prognostic_factor", "name": robplan.NAMES["prognostic_factor"],
                                           "tool": robplan.RECOMMENDED["prognostic_factor"]}]
    res = robplan.save_plan(p, lead, new, "The protocol was amended after peer review of the protocol.")
    assert res["changed"]
    assert robplan.outside_change(p) is None
    # now pretend the FIRST entry's computer had a fast clock: its stamp is later than the second entry's
    entries = list(p.audit_entries())
    second = [e for e in entries if e.get("action") in robplan.PLAN_ACTIONS][-1]
    late = util.now()[:11] + "23:59:59.000+00:00"
    def skewed():
        out = []
        for e in entries:
            if e is first or (e.get("t") == first.get("t") and e.get("action") == first.get("action")):
                e = {**e, "t": late}
            out.append(e)
        return sorted(out, key=lambda e: e.get("t", ""))
    monkeypatch.setattr(p, "audit_entries", skewed)
    logged = [e for e in p.audit_entries() if e.get("action") in robplan.PLAN_ACTIONS]
    assert logged[-1]["t"] == late and (logged[-1]["details"] or {}).get("rev") < second["details"]["rev"]
    assert robplan._latest_logged(logged)["details"]["rev"] == second["details"]["rev"]
    assert robplan.outside_change(p) is None, "a clock ahead on the other computer must not read as a change made outside ReviewDesk"


def test_a_plan_really_changed_outside_reviewdesk_is_still_reported(demo):
    p, info = demo
    path = p.path("reviewdesk-project.json")
    with open(path, encoding="utf-8") as f:
        s = json.load(f)
    s["risk_of_bias_plan"]["designs"][0]["tool"] = "quips"
    s["risk_of_bias_plan"]["saved"] = "2020-01-01T00:00:00.000+00:00"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(s, f)
    os.utime(path, None)
    p2 = type(p)(info["folder"])
    pr = robplan.outside_change(p2)
    assert pr and "changed outside ReviewDesk" in pr["text"]


def test_an_older_plan_put_back_is_reported_even_though_it_was_once_recorded(demo):
    """Revision 1 was recorded, then revision 2. If the file holds revision 1 again, it matches an entry, but not the latest."""
    p, info = demo
    lead = next(r["id"] for r in p.settings_ro()["reviewers"] if r.get("role") == "lead")
    path = p.path("reviewdesk-project.json")
    with open(path, encoding="utf-8") as f:
        before = json.load(f)
    pl = robplan.stored(p.settings_ro())
    new = {"designs": pl["designs"] + [{"id": "prognostic_factor", "name": robplan.NAMES["prognostic_factor"],
                                        "tool": robplan.RECOMMENDED["prognostic_factor"]}], "tools": pl.get("tools") or {}}
    if any(d["id"] == "prognostic_factor" for d in pl["designs"]):
        new["designs"] = [d for d in pl["designs"] if d["id"] != "prognostic_factor"]
    robplan.save_plan(p, lead, new, "The protocol was amended after peer review of the protocol.")
    with open(path, encoding="utf-8") as f:
        after = json.load(f)
    after["risk_of_bias_plan"] = {**before["risk_of_bias_plan"], "saved": "2020-01-01T00:00:00.000+00:00"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(after, f)
    os.utime(path, None)
    p2 = type(p)(info["folder"])
    pr = robplan.outside_change(p2)
    assert pr and "does not match the plan last recorded" in pr["text"]


def test_entries_logged_before_the_revision_number_was_kept_are_ordered_by_time():
    a = {"t": "2026-11-01T10:00:00.000+00:00", "details": {"fingerprint": "A"}}
    b = {"t": "2026-11-02T10:00:00.000+00:00", "details": {"fingerprint": "B"}}
    assert robplan._latest_logged([a, b]) is b
    c = {"t": "2026-10-01T10:00:00.000+00:00", "details": {"fingerprint": "C", "rev": 3}}     # a numbered entry is later than any unnumbered one
    assert robplan._latest_logged(sorted([a, b, c], key=lambda e: e["t"])) is c
    d = {"t": "2026-09-01T10:00:00.000+00:00", "details": {"fingerprint": "D", "rev": 4}}
    assert robplan._latest_logged(sorted([a, b, c, d], key=lambda e: e["t"])) is d
    assert robplan._latest_logged([{"t": "x", "details": {"rev": True}}, b]) is b               # True is not a revision number


def test_a_project_with_assessments_but_no_plan_is_offered_the_designs_its_tools_are_used_for(demo):
    """As after an upgrade from 1.2: assessments exist, no plan. The plan's page offers a draft; nothing else treats it as the plan."""
    p, info = demo
    lead = next(r["id"] for r in p.settings_ro()["reviewers"] if r.get("role") == "lead")
    used = robplan._used_tools(p)
    assert used, "the demonstration review has risk-of-bias assessments"
    path = p.path("reviewdesk-project.json")
    with open(path, encoding="utf-8") as f:
        s = json.load(f)
    s.pop("risk_of_bias_plan", None)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(s, f)
    os.utime(path, None)
    p2 = type(p)(info["folder"])
    assert not robplan.recorded(p2) and not robplan.plan(p2).get("designs"), "plan() itself offers nothing: outputs and checks see no plan"
    pub = robplan.public_plan(p2, lead)
    ids = [d["id"] for d in pub["plan"]["designs"]]
    assert ids and all(used.get(robplan.RECOMMENDED[i]) for i in ids)
    assert {robplan.RECOMMENDED[i] for i in ids} == {t for t in used if t in set(robplan.RECOMMENDED.values())}
    assert pub["plan"]["legacy"]["in_use"] is True and pub["plan"]["legacy"]["tools"]
    assert not pub["recorded"]


def test_a_plan_whose_audit_entry_went_missing_is_not_called_a_change_outside_reviewdesk(demo, monkeypatch):
    """The sync service puts back the lead's audit file without its last entry (or ReviewDesk was stopped between saving
    the plan and logging it). The plan was saved by ReviewDesk: it must not be reported as changed outside ReviewDesk,
    and putting it right must not write a protocol amendment."""
    p, info = demo
    lead = next(r["id"] for r in p.settings_ro()["reviewers"] if r.get("role") == "lead")
    pl = robplan.stored(p.settings_ro())
    new = {"designs": pl["designs"] + [{"id": "prognostic_factor", "name": robplan.NAMES["prognostic_factor"],
                                         "tool": robplan.RECOMMENDED["prognostic_factor"]}], "tools": pl.get("tools") or {}}
    if any(d["id"] == "prognostic_factor" for d in pl["designs"]):
        new["designs"] = [d for d in pl["designs"] if d["id"] != "prognostic_factor"]
    assert robplan.save_plan(p, lead, new, "The protocol was amended after peer review of the protocol.")["changed"]
    amendments = len(p.settings_ro().get("amendments") or [])
    rev = robplan.stored(p.settings_ro())["rev"]
    # the last plan entry goes from the audit files
    gone = 0
    for n in os.listdir(p.path("audit")):
        path = p.path("audit", n)
        lines = open(path, encoding="utf-8").read().splitlines()
        keep = [x for x in lines if not (x.strip() and json.loads(x).get("action") in robplan.PLAN_ACTIONS
                                         and (json.loads(x).get("details") or {}).get("rev") == rev)]
        gone += len(lines) - len(keep)
        open(path, "w", encoding="utf-8").write("\n".join(keep) + "\n")
    assert gone == 1
    p._cache.clear(); p._memo.clear()
    assert robplan.outside_change(p) is None                       # for 15 minutes: the entry may still be on its way
    real = util.now
    monkeypatch.setattr(util, "now", lambda: _later(real()))
    pr = robplan.outside_change(p)
    assert pr and pr["log_behind"] and pr["fixes"] == ["accept"]
    assert pr["text"].startswith(f"The audit log has no entry for the risk-of-bias plan now in use (revision {rev}")
    assert "changed outside ReviewDesk" not in pr["text"] and "not a protocol amendment" in pr["text"]
    got = [x for x in p.integrity_report()["problems"] if x["kind"] == "robplan"]
    assert len(got) == 1 and got[0]["sig"] == pr["sig"]
    p.accept_problem(lead, pr["sig"], "It is the plan in the amended protocol, version 2.")
    assert robplan.outside_change(p) is None
    assert len(p.settings_ro().get("amendments") or []) == amendments          # no amendment was written
    last = _audit_plan_entries(p)[-1]
    assert last["action"] == robplan.LOGGED_AGAIN and last["details"]["rev"] == rev
    assert not [x for x in p.integrity_report()["problems"] if x["kind"] == "robplan"]


def _later(stamp: str) -> str:
    """The same time twenty minutes on, in the form util.now() gives."""
    import datetime
    t = datetime.datetime.fromisoformat(stamp) + datetime.timedelta(minutes=20)
    return t.isoformat(timespec="milliseconds")
