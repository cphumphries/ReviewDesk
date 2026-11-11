"""Two people on two computers sharing one project folder through OneDrive.

Each computer has its own settings folder (REVIEWDESK_HOME), so its own device ID; the lead (CH) and the reviewer (NH)
each run their own ReviewDesk on their own copy of the folder at the same time, and a stand-in for OneDrive copies the
files between the copies (FakeOneDrive in helpers.py). Their files never collide; each sees the other's work once it has
synced; when two copies of the lead's file meet (the lead worked on two computers before OneDrive caught up), OneDrive
keeps the second as state-<COMPUTER>.json, which ReviewDesk reports and the lead settles; and a computer that has not yet
received the latest files works safely with what it has."""
from __future__ import annotations

import json
import os

import pytest

from helpers import Desk, FakeOneDrive, ServerProcess, on_computer, read_bytes, read_text, record_plan, reference_plan, ris, \
    rob_plan, tiny_records, write_bytes, write_text
from reviewdesk import screening
from reviewdesk.project import Project, ProjectError

PEOPLE = [{"id": "CH", "name": "Dr Colin Hawthorne", "role": "lead"}, {"id": "NH", "name": "Nadine Hayes", "role": "reviewer"}]


def new_project(home: str, folder: str, n: int = 12, plan: dict | None = None) -> Desk:
    """CH makes the project on CH's computer, sets it up and adds and processes an export."""
    with on_computer(home):
        p = Project.create(folder, "AI in emergency department triage", "Dr Colin Hawthorne", "CH")
        p.update_settings("CH", {"question": "Q", "criteria": {"include": ["AI triage"], "exclude": [], "rules": []}, "reviewers": PEOPLE,
                                 "screening": plan or reference_plan(mode="dual", calibration_size=0, ai_second_screener=False)})
        record_plan(p)
        p.add_import("CH", "medline.ris", ris(*tiny_records(n, seed=61)), {"set": "main", "database": "MEDLINE"})
        p.process_imports("CH")
    return Desk(home, folder, "CH")


def files_by_device(folder: str) -> dict:
    out = {}
    for sub in ("decisions", "audit", "state", "fulltexts", "extraction", "rob", "searches", "ai"):
        d = os.path.join(folder, sub)
        for n in os.listdir(d) if os.path.isdir(d) else []:
            if "@" in n:
                out.setdefault(n.rsplit("@", 1)[1].split(".")[0][:6], set()).add(f"{sub}/{n}")
    return out


@pytest.fixture
def od(tmp_path):
    """CH's laptop and NH's laptop, each with its own copy of the project folder in OneDrive."""
    copies = {"COLIN-LAPTOP": str(tmp_path / "Colin" / "OneDrive" / "AI triage review"),
              "NADINE-PC": str(tmp_path / "Nadine" / "OneDrive - University" / "AI triage review")}
    cloud = FakeOneDrive(str(tmp_path / "cloud"), copies)
    ch = new_project(str(tmp_path / "home-CH"), copies["COLIN-LAPTOP"])
    cloud.sync()
    nh = Desk(str(tmp_path / "home-NH"), copies["NADINE-PC"], "NH")
    cloud.ch, cloud.nh, cloud.tmp = ch, nh, tmp_path
    return cloud


def test_each_computer_has_its_own_settings_folder_and_device_id(od):
    ch, nh = od.ch, od.nh
    assert ch.home != nh.home and ch.dev != nh.dev and len(ch.dev) == len(nh.dev) == 6
    assert nh.do(nh.p.my_id) == "NH" and ch.do(ch.p.my_id) == "CH"
    assert nh.p.key() == ch.p.key()                         # the same project, though each copy is in its own place
    assert nh.p.folder_key() != ch.p.folder_key()
    nh.do(nh.p.add_decision, "NH", nh.p.records()[0]["id"], "cal", "include")
    assert nh.do(nh.p._local) == os.path.join(nh.home, "projects", nh.p.folder_key())     # each computer's own copies
    assert os.path.isfile(os.path.join(nh.home, "projects", nh.p.folder_key(), "mirror", f"NH@{nh.dev}.jsonl"))
    assert not os.path.exists(os.path.join(ch.home, "projects", ch.p.folder_key(), "mirror", f"NH@{nh.dev}.jsonl"))


def test_the_lead_and_the_reviewer_work_at_the_same_time_and_their_files_never_collide(od):
    ch, nh = od.ch, od.nh
    ch.do(screening.start, ch.p, "CH", "main")
    assert od.sync() == []
    nh.reopen()
    order = nh.do(lambda: [x["id"] for x in screening.queue(nh.p, "NH", "screen", "main")])
    assert order == ch.do(lambda: [x["id"] for x in screening.queue(ch.p, "CH", "screen", "main")])
    for k, rid in enumerate(order):                          # both screen at once, OneDrive syncing as they go
        nh.do(nh.p.add_decision, "NH", rid, "ta", "include" if k % 3 == 0 else "exclude")
        ch.do(ch.p.add_decision, "CH", rid, "ta", "include" if k % 4 == 0 else "exclude")
        if k % 3 == 1:
            assert od.sync() == []                           # never two versions of one file
    assert od.sync() == []
    for d in (ch, nh):
        o = d.do(screening.overview, d.p)
        assert o["sets"]["main"]["counts"] == {"forward": 6, "excluded": 6}
    a, b = files_by_device(od.copies["COLIN-LAPTOP"]), files_by_device(od.copies["NADINE-PC"])
    assert a == b and set(a) == {ch.dev, nh.dev}
    assert a[nh.dev] == {f"decisions/NH@{nh.dev}.jsonl"}
    assert f"decisions/CH@{ch.dev}.jsonl" in a[ch.dev] and not any(f.startswith("decisions/NH") for f in a[ch.dev])
    for d in (ch, nh):
        assert d.do(d.p.sync_warnings) == [] and d.do(d.p.integrity_report)["problems"] == []


def test_two_running_reviewdesks_share_one_folder_and_see_each_others_work(tmp_path):
    folder = str(tmp_path / "Shared" / "AI triage review")
    running = []
    try:
        a = ServerProcess(str(tmp_path / "home-CH"))
        running.append(a)
        b = ServerProcess(str(tmp_path / "home-NH"))
        running.append(b)
        ca, cb = a.client("CH"), b.client("NH")
        ca.create_project(str(tmp_path / "Shared"))
        ca.me = "CH"
        ca.settings(question="Q", criteria={"include": ["AI triage"], "exclude": [], "rules": []}, reviewers=PEOPLE,
                    screening=reference_plan(mode="dual", calibration_size=0, ai_second_screener=False))
        ca.post("/api/rob/plan", {"plan": rob_plan()})
        ca.add_import(ris(*tiny_records(8, seed=62)), "medline.ris", "MEDLINE")
        ca.process()
        cb.open_project(folder)
        cb.be("NH")
        assert cb.summary()["is_lead"] is False and ca.summary()["is_lead"] is True
        ca.post("/api/screening/start", {"set": "main"})
        order = [x["id"] for x in cb.queue("screen")]
        for k, rid in enumerate(order):
            cb.decide(rid, "ta", "include" if k < 3 else "exclude")
            ca.decide(rid, "ta", "include" if k < 2 else "exclude")
        for c in (ca, cb):
            assert c.overview()["sets"]["main"]["counts"] == {"forward": 3, "excluded": 5}
        devs = files_by_device(folder)
        assert len(devs) == 2
        st, _, body = cb.request("POST", "/api/imports/process", b"{}", {"Content-Type": "application/json", "X-Project": cb.project_key})
        assert st == 403 and "Only the project lead" in json.loads(body)["error"]
        cb.post("/api/searchlog", {"database": "NEJM AI", "date": "2026-10-01", "string": "Hand-searched every issue", "results": "4"})
        names = {(e["who"], e["computer"]) for e in ca.get("/api/audit")["entries"]}
        assert {who for who, _ in names} == {"CH", "NH"} and len({dev for _, dev in names}) == 2
        assert [e["database"] for e in ca.get("/api/searchlog")["entries"]] == ["NEJM AI"]
    finally:
        for s in running:
            s.stop()


def test_the_lead_is_told_when_the_shared_files_were_last_saved_on_another_computer(desk, tmp_path):
    c = desk.client()
    c.create_project(str(tmp_path / "OneDrive"))
    folder = c.summary()["path"]
    assert c.summary()["lead_elsewhere"] == ""
    desktop = Desk(str(tmp_path / "home-CH-desktop"), folder, "CH")          # CH's other computer
    desktop.do(desktop.p.update_settings, "CH", {"question": "A question changed on the desktop"})
    assert "last saved by the lead on another computer" in c.summary()["lead_elsewhere"]


def test_onedrive_keeps_a_second_version_when_two_copies_of_the_leads_file_meet(tmp_path):
    copies = {"COLIN-LAPTOP": str(tmp_path / "laptop" / "AI triage review"), "COLIN-DESKTOP": str(tmp_path / "desktop" / "AI triage review"),
              "NADINE-PC": str(tmp_path / "nadine" / "AI triage review")}
    od = FakeOneDrive(str(tmp_path / "cloud"), copies)
    laptop = new_project(str(tmp_path / "home-CH-laptop"), copies["COLIN-LAPTOP"], n=10)
    od.sync()
    desktop = Desk(str(tmp_path / "home-CH-desktop"), copies["COLIN-DESKTOP"], "CH")
    nh = Desk(str(tmp_path / "home-NH"), copies["NADINE-PC"], "NH")
    ids = sorted(r["id"] for r in laptop.p.records())
    # CH works on both computers before OneDrive catches up: two versions of state/state.json
    laptop.do(laptop.p.mark_not_duplicate, "CH", ids[0], ids[1])
    desktop.do(desktop.p.mark_duplicate, "CH", ids[2], ids[3])
    desktop.do(desktop.p.mark_duplicate, "CH", ids[4], ids[5])
    od.upload("COLIN-LAPTOP")
    made = od.upload("COLIN-DESKTOP")
    assert made == ["state/state-COLIN-DESKTOP.json"]
    for c in od.copies:
        od.download(c)
    for d in (nh.reopen(), laptop.reopen(), desktop.reopen()):
        pass
    w = nh.do(nh.p.sync_warnings)
    assert ("state/state-COLIN-DESKTOP.json is a second version of state.json: two computers changed it before the sync service "
            "caught up, and ReviewDesk is using the other one. The project lead can compare them and choose (Sync problems, on the "
            "Overview).") in w
    (g,) = laptop.do(laptop.p.lead_conflicts)
    (f,) = g["files"]
    assert g["suffix"] == "-COLIN-DESKTOP" and f["file"] == "state/state.json" and f["copy"] == "state/state-COLIN-DESKTOP.json"
    assert f["current"]["saved_on"] == laptop.dev and f["other"]["saved_on"] == desktop.dev
    assert f["other"]["rev"] > f["current"]["rev"] and f["advice"] == "use_other"         # the desktop did more
    with pytest.raises(Exception):
        nh.do(nh.p.use_conflict_copy, "NH", f["copy"])                                    # only the lead chooses
    laptop.do(laptop.p.use_conflict_copy, "CH", f["copy"])
    assert laptop.do(laptop.p.sync_warnings) == []
    od.sync()
    nh.reopen()
    assert nh.do(nh.p.sync_warnings) == []
    st = nh.do(nh.p.state_ro)
    assert sorted(st["overrides"]["merge"]) == [[ids[2], ids[3]], [ids[4], ids[5]]] and st["overrides"]["not_dup"] == []
    replaced = os.listdir(os.path.join(copies["NADINE-PC"], "state", "_replaced"))
    assert any(n.startswith("state.json.") for n in replaced)           # the version not used is kept, never deleted
    assert any(e["action"] == "use the sync service's other version" for e in nh.do(nh.p.audit_entries))
    # the laptop's not-duplicate decision was in the version set aside: the lead makes it again
    laptop.do(laptop.p.mark_not_duplicate, "CH", ids[0], ids[1])
    od.sync()
    assert nh.reopen() and nh.do(nh.p.state_ro)["overrides"]["not_dup"] == [sorted([ids[0], ids[1]])]


def test_keeping_the_version_in_use_sets_the_second_version_aside(tmp_path):
    copies = {"LAPTOP": str(tmp_path / "a" / "proj"), "DESKTOP": str(tmp_path / "b" / "proj")}
    od = FakeOneDrive(str(tmp_path / "cloud"), copies)
    laptop = new_project(str(tmp_path / "home-1"), copies["LAPTOP"], n=6)
    od.sync()
    desktop = Desk(str(tmp_path / "home-2"), copies["DESKTOP"], "CH")
    laptop.do(laptop.p.update_settings, "CH", {"question": "Asked on the laptop"})
    desktop.do(desktop.p.update_settings, "CH", {"question": "Asked on the desktop"})
    od.upload("LAPTOP")
    assert od.upload("DESKTOP") == ["reviewdesk-project-DESKTOP.json"]
    od.download("LAPTOP")
    laptop.reopen()
    (g,) = laptop.do(laptop.p.lead_conflicts)
    assert g["files"][0]["file"] == "reviewdesk-project.json"
    laptop.do(laptop.p.keep_current_versions, "CH", g["suffix"])
    assert laptop.do(laptop.p.lead_conflicts) == [] and laptop.do(laptop.p.settings_ro)["question"] == "Asked on the laptop"


def test_a_second_version_of_a_reviewers_decisions_file_loses_nothing(od):
    ch, nh = od.ch, od.nh
    ch.do(screening.start, ch.p, "CH", "main")
    od.sync()
    nh.reopen()
    ids = nh.do(lambda: [x["id"] for x in screening.queue(nh.p, "NH", "screen", "main")])
    for rid in ids[:6]:
        nh.do(nh.p.add_decision, "NH", rid, "ta", "exclude")
    f = os.path.join(od.copies["NADINE-PC"], "decisions", f"NH@{nh.dev}.jsonl")
    lines = read_text(f).splitlines(keepends=True)
    copy = os.path.join(od.copies["NADINE-PC"], "decisions", f"NH@{nh.dev}-NADINE-PC.jsonl")
    write_text(copy, "".join(lines[:4]))     # an older second version OneDrive kept
    od.sync()
    ch.reopen()
    for d in (ch, nh):
        dec = d.do(d.p.decisions)["NH"]
        assert len(dec) == 6 and [x["r"] for x in dec] == ids[:6]           # each vote once
        assert d.do(d.p.sync_warnings) == []                               # merged in: nothing to do


def test_a_reviewer_whose_computer_has_not_received_the_leads_latest_files_works_safely(od):
    ch, nh = od.ch, od.nh
    ch.do(screening.start, ch.p, "CH", "main")
    od.sync()
    nh.reopen()
    ids = nh.do(lambda: [x["id"] for x in screening.queue(nh.p, "NH", "screen", "main")])
    # CH adds the update search; OneDrive has not yet brought NH the new records or the new state
    with on_computer(ch.home):
        ch.p.add_import("CH", "embase update.ris", ris(*tiny_records(4, seed=63, prefix="Update: ")), {"set": "update", "database": "Embase"})
        ch.p.process_imports("CH")
    new = sorted(r["id"] for r in ch.p.records() if r["set"] == "update")
    od.upload("COLIN-LAPTOP")
    od.download("NADINE-PC", skip=("state/",))
    nh.reopen()
    with pytest.raises(ProjectError, match="That record is not in the project"):
        nh.do(nh.p.add_decision, "NH", new[0], "ta", "include")
    assert nh.do(screening.tasks_for, nh.p, "NH")[0]["total"] == 12
    for rid in ids:                                            # NH carries on with what is there
        nh.do(nh.p.add_decision, "NH", rid, "ta", "exclude")
    assert nh.do(nh.p.sync_warnings) == []
    od.sync()
    nh.reopen()
    ch.reopen()
    nh.do(nh.p.add_decision, "NH", new[0], "ta", "include")    # once it has arrived
    od.sync()
    ch.reopen()
    assert len(ch.do(ch.p.decisions)["NH"]) == 13
    assert ch.do(ch.p.integrity_report)["problems"] == [] and nh.do(nh.p.integrity_report)["problems"] == []


def test_the_lead_cannot_apply_the_rule_until_the_reviewers_votes_have_arrived(tmp_path):
    copies = {"CH": str(tmp_path / "ch" / "proj"), "NH": str(tmp_path / "nh" / "proj")}
    od = FakeOneDrive(str(tmp_path / "cloud"), copies)
    ch = new_project(str(tmp_path / "home-CH"), copies["CH"], n=20, plan=reference_plan(calibration_size=0, ai_second_screener=False))
    ch.do(screening.start, ch.p, "CH", "main")
    od.sync()
    nh = Desk(str(tmp_path / "home-NH"), copies["NH"], "NH")
    sample = nh.do(nh.p.state_ro)["sets"]["main"]["sample"]
    for rid in sample:
        nh.do(nh.p.add_decision, "NH", rid, "ta", "exclude")
        ch.do(ch.p.add_decision, "CH", rid, "ta", "exclude")
    with pytest.raises(ProjectError, match=r"Both reviewers must finish the agreement sample first \(0 and 4 of 4 done\)"):
        ch.do(screening.apply_sample_decision, ch.p, "CH", "main")
    od.sync()
    ch.reopen()
    sv = ch.do(screening.apply_sample_decision, ch.p, "CH", "main")
    assert sv["phase"] == "screening"
    # until OneDrive brings NH the new state, NH's computer still shows the sample as the task
    od.upload("CH")
    od.download("NH", skip=("state/state.json",))
    nh.reopen()
    assert [t["task"] for t in nh.do(screening.tasks_for, nh.p, "NH")] == ["sample"]
    od.sync()
    nh.reopen()
    assert "screen" in [t["task"] for t in nh.do(screening.tasks_for, nh.p, "NH")]


def test_an_older_copy_of_a_reviewers_file_put_back_by_onedrive_is_noticed_and_repaired(od):
    ch, nh = od.ch, od.nh
    ch.do(screening.start, ch.p, "CH", "main")
    od.sync()
    nh.reopen()
    ids = nh.do(lambda: [x["id"] for x in screening.queue(nh.p, "NH", "screen", "main")])
    nh.do(nh.p.add_decision, "NH", ids[0], "ta", "include")
    f = os.path.join(od.copies["NADINE-PC"], "decisions", f"NH@{nh.dev}.jsonl")
    older = read_bytes(f)
    for rid in ids[1:5]:
        nh.do(nh.p.add_decision, "NH", rid, "ta", "exclude")
    od.sync()
    ch.reopen()
    assert ch.do(ch.p.integrity_report)["problems"] == []       # the lead's computer has seen the five votes
    write_bytes(f, older)                                   # OneDrive's version history puts back the older copy
    od.upload("NADINE-PC")
    od.download("COLIN-LAPTOP")
    ch.reopen()
    (p,) = ch.do(ch.p.integrity_report)["problems"]
    assert "4 rows this computer had seen are no longer there" in p["text"]
    nh.reopen()
    nh.do(nh.p.integrity_report)                                 # NH's computer puts its own rows back
    assert len([x for x in read_text(f).splitlines() if '"repair"' not in x]) == 5
    od.sync()
    ch.reopen()
    assert ch.do(ch.p.integrity_report)["problems"] == []
