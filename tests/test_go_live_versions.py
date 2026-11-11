"""One team, one version: a project says which ReviewDesk it needs, an older one refuses it, and the Overview warns when
someone last worked on the project with another version (the upgrade audit's finding B2)."""
import json
import os

import pytest

import demo_review
from reviewdesk import VERSION, util, versions


def test_versions_compare_as_numbers_not_text():
    assert versions.newer("1.10.0", "1.9.9") and versions.newer("1.3.1", "1.3.0") and versions.newer("2.0", "1.99.99")
    assert not versions.newer("1.3", "1.3.0") and not versions.newer("1.3.0", "1.3") and not versions.newer("1.2.1", "1.3.0")
    assert versions.newer("1.3.0", "") and versions.newer("1.3.0", None) and not versions.newer("", "1.0.0")
    assert versions.parse("1.3.0-beta2") == (1, 3, 0) and versions.parse("nonsense") == (0,)


def test_a_project_file_is_stamped_with_the_version_it_needs_and_never_lowered():
    assert versions.stamp({})["min_version"] == versions.NEEDS
    assert versions.stamp({"min_version": "1.0.0"})["min_version"] == versions.NEEDS
    assert versions.stamp({"min_version": "9.9.9"})["min_version"] == "9.9.9"
    assert not versions.newer(versions.NEEDS, VERSION), "this version must be able to open what it writes"


def test_a_project_that_needs_a_newer_version_is_refused_in_plain_words():
    assert versions.too_old({}) == "" and versions.too_old({"min_version": VERSION}) == "" and versions.too_old({"min_version": "1.0.0"}) == ""
    msg = versions.too_old({"min_version": "9.4.0"})
    assert "needs ReviewDesk 9.4.0 or later" in msg and f"this is ReviewDesk {VERSION}" in msg and "READ ME FIRST in the download says how" in msg
    assert "Error" not in msg and "min_version" not in msg


@pytest.fixture()
def demo(tmp_path):
    info = demo_review.build(str(tmp_path / "demo"))
    versions._noted.clear()
    return info["p"], info


def _as_if_written_by(p, who, version, also_strip=False):
    """Rewrite who's audit entries as an older ReviewDesk would have written them (same rows, another version)."""
    for f in util.files_in(p.path("audit"), f"{who}@*.jsonl"):
        rows = [json.loads(line) for line in open(f, encoding="utf-8") if line.strip()]
        with open(f, "w", encoding="utf-8", newline="\n") as out:
            for r in rows:
                r["version"] = version
                out.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.utime(f, None)
    p._cache.clear(); p._memo.clear()


def test_nothing_is_said_when_everyone_uses_this_version(demo):
    p, info = demo
    assert versions.others(p, "CH") == [] and versions.warnings(p, "CH") == [] and versions.warnings(p, "NH") == []


def test_someone_who_last_worked_with_an_older_version_is_named_with_the_version_and_the_day(demo):
    p, info = demo
    _as_if_written_by(p, "NH", "1.2.1")
    (row,) = versions.others(p, "CH")
    assert row["id"] == "NH" and row["state"] == "older" and row["version"] == "1.2.1" and row["when"]
    (w,) = versions.warnings(p, "CH")
    assert w.startswith("Nadine Hayes last worked on this project with ReviewDesk 1.2.1 (on ")
    assert f"Everyone in the team must use ReviewDesk {VERSION}" in w and "must not be used to settle disagreements" in w
    # she is not told about herself, and the lead is not warned about himself
    assert versions.warnings(p, "NH") == []
    # ... and it goes when she opens the project with this version: that opening leaves an entry
    versions.note_open(p, "NH")
    p._cache.clear(); p._memo.clear()
    last = [e for e in p.audit_entries() if e.get("who") == "NH"][-1]
    assert last["action"] == versions.OPENED and last["version"] == VERSION and last["details"]["last used"] == "1.2.1"
    assert versions.warnings(p, "CH") == []


def test_opening_leaves_one_entry_per_person_and_none_when_the_latest_is_already_this_version(demo):
    p, info = demo
    n = len(p.audit_entries())
    versions.note_open(p, "CH"); versions.note_open(p, "CH"); versions.note_open(p, "NH")
    p._cache.clear(); p._memo.clear()
    assert len(p.audit_entries()) == n, "both already have entries written by this version: nothing to add"
    versions.note_open(p, "ZZ")                                    # not in the project: nothing is written
    versions.note_open(p, "")
    p._cache.clear(); p._memo.clear()
    assert len(p.audit_entries()) == n and not util.files_in(p.path("audit"), "ZZ@*.jsonl")


def test_someone_on_a_newer_version_means_this_computer_must_upgrade(demo):
    p, info = demo
    _as_if_written_by(p, "NH", "9.4.0")
    (w,) = versions.warnings(p, "CH")
    assert w.startswith("Nadine Hayes uses ReviewDesk 9.4.0, which is newer than yours") and "Download the new version" in w


def test_someone_who_has_only_voted_is_not_known_to_have_opened_it_with_this_version(demo):
    """Votes are not in the audit log: a reviewer with files of her own and no audit entry at all is named as not
    having opened the project with this version yet; a member with no files at all is not mentioned."""
    p, info = demo
    for f in util.files_in(p.path("audit"), "NH@*.jsonl"):
        os.rename(f, f + ".aside")
    p._cache.clear(); p._memo.clear()
    (row,) = versions.others(p, "CH")
    assert row["state"] == "unknown"
    (w,) = versions.warnings(p, "CH")
    assert w.startswith(f"Nadine Hayes has worked on this project but has not opened it with ReviewDesk {VERSION} yet.")
    s = p.settings()
    p.update_settings("CH", {"reviewers": s["reviewers"] + [{"id": "QQ", "name": "Quentin Quill", "role": "reviewer"}]})
    p._cache.clear(); p._memo.clear()
    assert [x["id"] for x in versions.others(p, "CH")] == ["NH"], "someone just added, with no work yet, is not mentioned"


def test_a_new_project_says_which_version_it_needs_and_saving_settings_keeps_it(tmp_path):
    from reviewdesk.project import Project
    p = Project.create(str(tmp_path / "new"), "A review", "Colin Hawthorne", "CH")
    path = p.path("reviewdesk-project.json")
    assert json.load(open(path, encoding="utf-8"))["min_version"] == versions.NEEDS
    # a project begun with an earlier version has none until the lead saves its settings with this one
    s = json.load(open(path, encoding="utf-8"))
    s.pop("min_version")
    json.dump(s, open(path, "w", encoding="utf-8"))
    p = Project(str(tmp_path / "new"))
    p.update_settings("CH", {"question": "Does it work?"})
    assert json.load(open(path, encoding="utf-8"))["min_version"] == versions.NEEDS


def test_a_project_that_needs_a_newer_reviewdesk_is_not_opened(tmp_path):
    from reviewdesk.project import Project, ProjectError
    p = Project.create(str(tmp_path / "new"), "A review", "Colin Hawthorne", "CH")
    path = p.path("reviewdesk-project.json")
    s = json.load(open(path, encoding="utf-8"))
    s["min_version"] = "9.4.0"
    json.dump(s, open(path, "w", encoding="utf-8"))
    before = sorted(os.listdir(tmp_path / "new"))
    with pytest.raises(ProjectError) as e:
        Project(str(tmp_path / "new"))
    assert "needs ReviewDesk 9.4.0 or later" in str(e.value)
    assert sorted(os.listdir(tmp_path / "new")) == before, "nothing is written to a project this version must not open"


def test_the_page_is_told_who_last_used_another_version_and_this_opening_is_noted(tmp_path):
    """Through the running program: the project summary every page gets carries the warnings, and a member's first
    opening after someone's audit entries were written by an older version leaves its own entry."""
    import helpers
    info = demo_review.build(str(tmp_path / "demo"))
    p = info["p"]
    _as_if_written_by(p, "NH", "1.2.1")
    _as_if_written_by(p, "CH", "1.2.1")                               # the lead, too, last worked with the old version
    versions._noted.clear()
    srv = helpers.ServerProcess(str(tmp_path / "home-ch"))
    try:
        c = srv.client("CH")
        c.open_project(info["folder"])
        c.be("CH")
        app = c.get("/api/app")
        (w,) = app["project"]["version_warnings"]
        assert w.startswith("Nadine Hayes last worked on this project with ReviewDesk 1.2.1")
    finally:
        srv.stop()
    from reviewdesk.project import Project
    q = Project(info["folder"])
    mine = [e for e in q.audit_entries() if e.get("who") == "CH"]
    assert mine[-1]["action"] == versions.OPENED and mine[-1]["version"] == VERSION, "the lead's opening with this version is on record"


# ------------------------------------------------------------------------------------------------ an older version stops saving
def _worked_with(p, who, version, needs):
    """As if `who` had opened the project with a later ReviewDesk, whose records need `needs` to be read."""
    f = util.files_in(p.path("audit"), f"{who}@*.jsonl")[0]
    with open(f, "a", encoding="utf-8", newline="\n") as out:
        out.write(json.dumps({"t": util.now(), "who": who, "action": versions.OPENED, "details": {"with ReviewDesk": version},
                              "version": version, "needs": needs, "computer": "abc123", "computer_name": "X"}) + "\n")
    os.utime(f, None)
    p._cache.clear(); p._memo.clear()


def test_every_audit_entry_says_which_version_the_project_needs(demo):
    p, info = demo
    entries = p.audit_entries()
    assert entries and all(e.get("needs") == versions.NEEDS for e in entries)
    assert versions.needed(p)["version"] == versions.NEEDS and versions.must_upgrade(p, "CH") == ""


def test_a_newer_version_that_this_one_can_still_read_only_warns(demo):
    p, info = demo
    _worked_with(p, "NH", "9.4.0", VERSION)                       # newer, but it writes nothing this version cannot read
    assert versions.must_upgrade(p, "CH") == ""
    (w,) = versions.warnings(p, "CH")
    assert w.startswith("Nadine Hayes uses ReviewDesk 9.4.0, which is newer than yours")
    for junk in ("", "soon", "9" * 30, 7, None, ["9.4.0"], "0"):   # nothing but a real version number counts
        _worked_with(p, "NH", "9.4.0", junk)
        assert versions.must_upgrade(p, "CH") == "", junk


def test_once_anyone_has_used_a_version_this_one_cannot_read_it_says_who_and_what_to_do(demo):
    """The project file is the lead's alone to write, so until the lead saves it nothing in it says that a reviewer has
    moved to a newer version. Every audit entry says which version the project needs, so the reviewer's first opening
    is enough."""
    p, info = demo
    _worked_with(p, "NH", "9.4.0", "9.4.0")
    msg = versions.must_upgrade(p, "CH")
    assert msg.startswith(f"This project now needs ReviewDesk 9.4.0 or later, and this is ReviewDesk {VERSION}. Nadine Hayes has "
                          "worked on it with the newer version (on ")
    for want in ("will not save any work in this project", "you can still read it and download its reports",
                 "READ ME FIRST in the download says how", "Everyone in the team must use it."):
        assert want in msg, want
    assert "needs\"" not in msg and "Error" not in msg
    assert versions.warnings(p, "CH") == [msg], "one message, not the milder one as well"
    assert versions.must_upgrade(p, "NH").split(". ")[1].startswith("You have worked on it with the newer version")
    assert versions.needed(p) == {"version": "9.4.0", "who": "NH", "when": util.now()[:10]}
    _worked_with(p, "CH", "9.5.0", "9.5.0")                       # the newest need is the one that counts
    assert "needs ReviewDesk 9.5.0 or later" in versions.must_upgrade(p, "NH")


def test_an_older_version_stops_saving_but_still_reads_and_exports(tmp_path):
    import helpers
    info = demo_review.build(str(tmp_path / "demo"))
    p = info["p"]
    versions._noted.clear()
    srv = helpers.ServerProcess(str(tmp_path / "home-ch"))
    try:
        c = srv.client("CH")
        c.open_project(info["folder"])
        c.be("CH")
        rec = next(r["id"] for r in p.records())
        assert c.get("/api/app")["project"]["version_warnings"] == []
        _worked_with(p, "NH", "9.4.0", "9.4.0")                   # NH's first opening with a later version arrives
        files = {}
        for d, _, names in os.walk(info["folder"]):
            for n in names:
                if "audit" not in d:
                    files[os.path.join(d, n)] = open(os.path.join(d, n), "rb").read()
        app = c.get("/api/app")
        (w,) = app["project"]["version_warnings"]
        assert w.startswith("This project now needs ReviewDesk 9.4.0 or later")
        for path, body in (("/api/decide", {"record": rec, "stage": "ta", "decision": "include", "as": "CH"}),
                           ("/api/settings", {"changes": {"question": "Changed with the old version?"}}),
                           ("/api/searchlog", {"database": "Embase", "date": "2026-11-01", "string": "x", "results": "3"}),
                           ("/api/imports/process", {})):
            e = c.error("POST", path, body)
            assert e.status == 409 and e.message == w, path
        e = None
        try:
            c.add_import(helpers.ris(*helpers.tiny_records(2, seed=77)), "late.ris", "MEDLINE")
        except helpers.ApiError as err:
            e = err
        assert e is not None and e.status == 409 and e.message == w
        for path, was in files.items():                            # nothing of the review's was written
            assert open(path, "rb").read() == was, path
        assert sorted(f for d, _, names in os.walk(info["folder"]) for f in (os.path.join(d, n) for n in names)
                      if "audit" not in f) == sorted(files)
        # reading, exporting, saying who you are and closing all still work
        assert c.get("/api/overview")["sets"] and rec in json.dumps(c.get("/api/record", id=rec))
        for kind in ("prisma", "summary", "audit", "rep_pack"):
            st, _, data = c.download(kind)
            assert st == 200 and len(data) > 100, kind
        assert c.be("NH")["me"] == "NH"
        assert c.get("/api/app")["project"]["version_warnings"][0].split(". ")[1].startswith("You have worked on it")
        c.post("/api/project/close", {})
        made = c.create_project(str(tmp_path / "new"), "Another review", lead_name="Dr Colin Hawthorne", lead_id="CH")
        assert made["project"]["version_warnings"] == []           # other projects are not affected
        c.settings(question="A new project saves as usual.")
    finally:
        srv.stop()
