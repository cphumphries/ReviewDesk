"""What the first-time-user beta test found, on the server side: an imported file linked to its search after it was
added (N8), the same person as both reviewers refused (N18), the empty disagreement rule and short title of a new
project (N6 and the wording list), two study names for one included report shown rather than chosen silently (N23), and
the step the review is at, as the steps page, the strip and the Overview all show it (N13)."""
import json
import os

import pytest

from helpers import Desk, on_computer
from reviewdesk import screening, timeline
from reviewdesk.project import PermissionDenied, Project, ProjectError, check_values

TEAM = [{"id": "SP", "name": "Dr Sam Patel", "role": "lead"}, {"id": "AM", "name": "Alex Morgan", "role": "reviewer"}]
TWO = "The first and second reviewers must be two different people."


def ris(n: int, tag: str) -> bytes:
    return "\n".join(f"TY  - JOUR\nTI  - Exercise for knee osteoarthritis {tag} trial number {i}\nAB  - A randomised trial of "
                     f"exercise, number {i} from {tag}.\nAU  - Author{i}, A\nPY  - 2021\nJO  - Journal {i}\nDO  - 10.5555/{tag}.{i}\n"
                     "ER  - \n" for i in range(n)).encode()


@pytest.fixture
def two(tmp_path):
    """A project with a lead (SP, on one computer) and a reviewer (AM, on another), sharing one folder."""
    folder = str(tmp_path / "team" / "proj")
    with on_computer(str(tmp_path / "home-sp")):
        p = Project.create(folder, "Exercise programmes for adults with knee osteoarthritis: a systematic review", "Dr Sam Patel", "SP")
        p.update_settings("SP", {"reviewers": TEAM})
    sp = Desk(str(tmp_path / "home-sp"), folder, "SP")
    am = Desk(str(tmp_path / "home-am"), folder, "AM")
    return sp, am


def log_search(desk, who, database, n):
    return desk.do(desk.p.save_search, who, {"database": database, "platform": "Ovid", "date": "2026-11-02", "string": "1 knee.mp.",
                                              "results": str(n)})


# ================================================================================================ N18
def test_the_same_person_cannot_be_both_reviewers(two):
    sp, _ = two
    with pytest.raises(ProjectError) as e:
        sp.do(sp.p.update_settings, "SP", {"screening": {"reviewers": ["AM", "AM"]}})
    assert str(e.value).startswith(TWO)
    assert sp.p.settings_ro()["screening"]["reviewers"] == ["SP"]          # nothing was saved
    sp.do(sp.p.update_settings, "SP", {"screening": {"reviewers": ["AM", "SP"]}})
    assert sp.p.settings_ro()["screening"]["reviewers"] == ["AM", "SP"]


def test_the_settings_check_refuses_the_same_person_twice_and_allows_one():
    with pytest.raises(ProjectError, match="^The first and second reviewers must be two different people"):
        check_values({"screening": {"reviewers": ["SP", "SP"]}})
    check_values({"screening": {"reviewers": ["SP"]}})                     # a new project names only its lead so far
    check_values({"screening": {"reviewers": ["SP", "AM"]}})


def test_the_server_refuses_the_same_person_twice_with_the_plain_message(desk):
    c = desk.client()
    c.create_project(os.path.join(desk.home, "..", "projects"), title="Knee review", lead_name="Dr Sam Patel", lead_id="SP")
    c.settings(reviewers=TEAM)
    e = c.error("POST", "/api/settings", {"changes": {"screening": {"reviewers": ["AM", "AM"]}}})
    assert e.status == 400 and e.message.startswith(TWO)


def test_a_plan_saved_with_the_same_person_twice_is_named_plainly(two):
    """A project saved by 1.3.0 may hold it already: the checks say what is wrong, not "choose the two reviewers"."""
    sp, _ = two
    s = json.load(open(sp.p.path("reviewdesk-project.json"), encoding="utf-8"))
    s["screening"]["reviewers"] = ["AM", "AM"]
    json.dump(s, open(sp.p.path("reviewdesk-project.json"), "w", encoding="utf-8"))
    p = sp.reopen()
    probs = screening.check_plan(p, screening.plan(p, "main"))
    assert any(x.startswith(TWO[:-1]) for x in probs) and not any("Choose the two reviewers" in x for x in probs)


# ================================================================================================ N8
def test_a_file_can_be_linked_to_its_search_after_it_was_added(two):
    sp, am = two
    medline = log_search(sp, "SP", "MEDLINE", 3)
    embase = log_search(sp, "SP", "Embase", 2)
    m = am.do(am.p.add_import, "AM", "embase.ris", ris(2, "emb"), {"set": "main", "database": "Embase"})
    path = next(x for x in sp.p.imports() if x["id"] == m["id"])["_path"] + ".meta.json"
    meta = open(path, "rb").read()
    assert not m["search_id"]
    am.do(am.p.link_import, "AM", m["id"], embase["id"])           # the person who added it
    got = {x["id"]: x for x in sp.p.imports()}[m["id"]]
    assert got["search_id"] == embase["id"] and got["search_date"] == "2026-11-02"
    # the import's own details file is never rewritten: the link lives in the linker's own search log file
    assert json.load(open(path, encoding="utf-8"))["search_id"] == "" and open(path, "rb").read() == meta
    assert os.path.isfile(am.p.path("searches", f"log-AM@{am.dev}.jsonl"))
    # the search log stays a list of searches, with the file now under its search
    log = sp.p.search_log()
    assert [e["database"] for e in log] == ["Embase", "MEDLINE"]
    # the lead can change it (the latest link wins), and take it off
    sp.do(sp.p.link_import, "SP", m["id"], medline["id"])
    assert {x["id"]: x for x in am.p.imports()}[m["id"]]["search_id"] == medline["id"]
    sp.do(sp.p.link_import, "SP", m["id"], "")
    assert {x["id"]: x for x in am.p.imports()}[m["id"]]["search_id"] == ""


def test_only_the_person_who_added_a_file_or_the_lead_can_link_it(two, tmp_path):
    sp, am = two
    e = log_search(sp, "SP", "MEDLINE", 2)
    m = sp.do(sp.p.add_import, "SP", "medline.ris", ris(2, "med"), {"set": "main", "database": "MEDLINE"})
    with pytest.raises(PermissionDenied):
        am.do(am.p.link_import, "AM", m["id"], e["id"])
    with pytest.raises(ProjectError, match="not in the search log"):
        sp.do(sp.p.link_import, "SP", m["id"], "S-XX-20261102-0000")
    with pytest.raises(ProjectError):
        sp.do(sp.p.link_import, "SP", "no-such-import", e["id"])
    assert not sp.p.imports()[0]["search_id"]


def test_linking_over_http_and_the_search_log_counts_the_linked_file(desk, tmp_path):
    c = desk.client()
    c.create_project(str(tmp_path / "projects"), title="Knee review", lead_name="Dr Sam Patel", lead_id="SP")
    entry = c.post("/api/searchlog", {"database": "Cochrane CENTRAL", "platform": "Cochrane Library", "date": "2026-11-02",
                                      "string": "knee", "results": "3"})
    m = c.add_import(ris(2, "cen"), "central.ris", "Cochrane CENTRAL")
    out = c.post("/api/imports/link", {"id": m["id"], "search_id": entry["id"]})
    assert next(x for x in out["imports"] if x["id"] == m["id"])["search_id"] == entry["id"]
    log = c.get("/api/searchlog")["entries"]
    assert log[0]["files"] == [{"filename": "central.ris", "records": 2, "part": ""}]
    assert "the files linked to it hold 2" in log[0]["mismatch"]          # the check a link makes possible
    assert c.error("POST", "/api/imports/link", {"id": m["id"], "search_id": "nope"}).status == 400


# ================================================================================================ N6 and the wording list
def test_a_new_project_leaves_the_disagreement_rule_and_the_short_title_to_the_lead(two):
    sp, _ = two
    s = sp.p.settings_ro()
    assert s["fulltext"]["disagreement_default"] == "" and s["short_title"] == ""


# ================================================================================================ N23: two study names
def test_two_study_names_for_one_included_report_are_shown():
    """Votes as screening.votes() reads them: both included the report, each naming its study."""
    V = {"ft": {"R1": {"SP": {"latest": {"d": "include", "study": "Okafor 2021"}},
                       "AM": {"latest": {"d": "include", "study": "Okafor et al. 2021"}}}}}
    o = screening.ft_outcome("R1", ["SP", "AM"], V, {})
    assert o["status"] == "included" and o["study"] == "Okafor 2021"
    assert o["study_names"] == [{"by": "SP", "name": "Okafor 2021"}, {"by": "AM", "name": "Okafor et al. 2021"}]
    V["ft"]["R1"]["AM"]["latest"]["study"] = "okafor 2021 "                # the same name, typed differently
    assert "study_names" not in screening.ft_outcome("R1", ["SP", "AM"], V, {})
    V["ft"]["R1"]["AM"]["latest"]["study"] = ""                             # one name only
    assert "study_names" not in screening.ft_outcome("R1", ["SP", "AM"], V, {})


# ================================================================================================ N13: where the review is
def test_the_steps_are_shown_done_only_when_what_they_build_on_is_done():
    st = {"sample": "now", "agreement_rule": "now", "extract": "now", "rob": "done", "certainty": "done"}
    out = timeline.shown(st)
    assert out["sample"] == "done"                       # both finished the sample: the agreement rule is under way
    assert out["certainty"] == "now"                     # GRADE builds on extraction, which is still open
    assert timeline.shown({"extract": "done", "rob": "done", "certainty": "done"})["certainty"] == "done"
    assert timeline.shown({"sample": "now", "agreement_rule": "later"})["sample"] == "now"


def test_you_are_here_leaves_out_steps_done_outside_reviewdesk_unless_nothing_else_is_open():
    s = dict(timeline.GENERIC_SETTINGS)
    ids = [x["id"] for x in timeline.workflow.steps_for(s, 0)]
    st = {k: "later" for k in ids}
    st.update({"setup": "done", "register": "now", "searches": "now"})
    m = timeline.model(s, 0, st)
    assert m["here"] == "search"                         # not "plan": registering is done outside ReviewDesk
    st["searches"] = "later"
    assert timeline.model(s, 0, st)["here"] == "plan"    # nothing else open: registering is where the review is
