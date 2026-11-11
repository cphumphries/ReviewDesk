"""Final audit of 1.3.0 (fixer fx-order): which row counts as the latest, the lead working on two computers, and this
computer's identity. Each test fails on the code as it was audited."""
from __future__ import annotations

import json
import os
import random
import socket
import sys
import threading

import pytest

from helpers import record_plan, ris, tiny_records
from reviewdesk import review, screening, userconfig, util
from reviewdesk.project import Project, ProjectError, written_order

PEOPLE = [{"id": "CH", "name": "Dr Colin Hawthorne", "role": "lead"}, {"id": "NH", "name": "Nadine Hayes", "role": "reviewer"}]


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWDESK_HOME", str(tmp_path / "home"))
    p = Project.create(str(tmp_path / "review"), "A review", "Dr Colin Hawthorne", "CH")
    userconfig.set_identity(p.dir, "CH", p.key())
    p.update_settings("CH", {"reviewers": PEOPLE, "question": "Does it work?",
                             "criteria": {"include": ["adults"], "exclude": ["children"], "rules": []},
                             "screening": {"mode": "dual", "reviewers": ["CH", "NH"], "single_reviewer": "NH",
                                           "second_reviewer": "CH", "calibration_size": 0}})
    record_plan(p)
    p.add_import("CH", "medline.ris", ris(*tiny_records(6, seed=5)), {"set": "main", "database": "MEDLINE"})
    p.process_imports("CH")
    return p


def at(monkeypatch, stamp: str) -> None:
    monkeypatch.setattr(util, "now", lambda: stamp)


# ------------------------------------------------------------------------------------------------ 1. the order written
def test_a_correction_counts_after_the_clock_is_put_back(proj, monkeypatch):
    """Windows puts a fast clock right between a vote and its correction: the correction is stamped earlier than the
    vote it corrects. It was ignored (the page said it was saved); the order written must decide within one file."""
    p = proj
    rid = screening.start(p, "CH", "main")["order"][0]
    at(monkeypatch, "2026-11-09T10:05:00.000+00:00")
    p.add_decision("NH", rid, "ta", "exclude")
    at(monkeypatch, "2026-11-09T10:01:30.000+00:00")
    p.add_decision("NH", rid, "ta", "include")
    assert screening.latest(screening.votes(p), "ta", rid, "NH")["d"] == "include"
    # the agreed decisions, the PDF record and the search log are read the same way
    at(monkeypatch, "2026-11-09T10:05:00.000+00:00")
    p.add_resolution("CH", rid, "ta", "exclude")
    at(monkeypatch, "2026-11-09T10:01:00.000+00:00")
    p.add_resolution("CH", rid, "ta", "include")
    assert p.resolutions()[(rid, "ta")]["d"] == "include"


def test_extraction_rows_written_after_the_clock_was_put_back_still_count_last(proj):
    """review._rows (extraction, risk of bias, GRADE, agreed values): one file, written in order."""
    p = proj
    f = p.path("extraction", "NH@abcdef.jsonl")
    rows = [{"t": "2026-11-09T10:05:00.000+00:00", "s": "S1", "f": "n", "v": "100"},
            {"t": "2026-11-09T10:01:00.000+00:00", "s": "S1", "f": "n", "v": "120"}]
    with open(f, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in rows))
    got = [d["v"] for who, d in review._rows(p, "extraction") if who == "NH"]
    assert got == ["100", "120"]


def test_two_computers_are_still_interleaved_by_time_and_time_zones_still_count(proj):
    """Rows from one person's two computers are put in time order, as instants (a +13:00 stamp is earlier than a
    +00:00 stamp of a smaller clock reading)."""
    a = [{"t": "2026-10-25T00:50:00.000+01:00", "d": "exclude"}, {"t": "2026-10-25T01:30:00.000+00:00", "d": "include"}]
    b = [{"t": "2026-10-25T13:00:00.000+13:00", "d": "maybe"}]
    rows = sorted([(t, k, 0, d) for t, k, d in written_order(a)] + [(t, k, 1, d) for t, k, d in written_order(b)],
                  key=lambda x: x[:3])
    assert [d["d"] for *_, d in rows] == ["exclude", "maybe", "include"]


def _old_decisions(p) -> dict:
    """Project.decisions as it was in 1.3.0 (sorted by stamp, then position)."""
    import glob
    out = {}
    for r in p.reviewers():
        files = util.files_in(p.path("decisions"), f"{glob.escape(r['id'])}@*.jsonl")
        seen, rows = set(), []
        for f in files:
            for k, d in enumerate(util.read_jsonl(f)):
                if not isinstance(d, dict) or "repair" in d:
                    continue
                key = p._content_key(d)
                if key not in seen:
                    seen.add(key)
                    rows.append((util.ts(d.get("t", "")), k, d))
        rows.sort(key=lambda x: x[:2])
        out[r["id"]] = [d for *_, d in rows]
    return out


def _old_read_all(pattern: str) -> list:
    rows = []
    for n, f in enumerate(util.files_in(*os.path.split(pattern))):
        rows += [(util.ts(d.get("t", "")), n, k, d) for k, d in enumerate(util.read_jsonl(f)) if isinstance(d, dict) and "repair" not in d]
    rows.sort(key=lambda x: x[:3])
    return [d for *_, d in rows]


def _old_rows(p, sub: str) -> list:
    out = []
    for f in util.files_in(p.path(sub), "*.jsonl"):
        who = os.path.basename(f).split("@")[0]
        out += [(util.ts(d.get("t", "")), who, k, d) for k, d in enumerate(util.read_jsonl(f)) if isinstance(d, dict) and "repair" not in d]
    out.sort(key=lambda x: (x[0], x[2]))
    return [(who, d) for _, who, _, d in out]


@pytest.mark.parametrize("seed", range(12))
def test_files_whose_stamps_run_in_order_are_read_exactly_as_before(proj, seed):
    """Every project made so far (each file's stamps in order): the rows come out in exactly the order 1.3.0 gave,
    ties, several computers, conflict copies, time zones and repair notes included."""
    p = proj
    rnd = random.Random(seed)
    ids = [r["id"] for r in p.records()]
    zones = ["+00:00", "+01:00", "+13:00", "-05:00"]

    def stamp(sec: int) -> str:
        z = rnd.choice(zones)
        import datetime as dt
        sign = 1 if z[0] == "+" else -1
        tz = dt.timezone(sign * dt.timedelta(hours=int(z[1:3])))
        return dt.datetime.fromtimestamp(1_790_000_000 + sec, tz).isoformat(timespec="milliseconds")

    def write(path, n_rows, extra):
        sec, rows = rnd.randrange(50), []
        for k in range(n_rows):
            sec += rnd.choice([0, 0, 1, 2, 7])                 # ties within a file and across files
            if rnd.random() < 0.05:
                rows.append({"t": stamp(sec), "repair": {"dropped": []}})
            else:
                rows.append({"t": stamp(sec), **extra(k)})
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows))
        return rows

    for who in ("CH", "NH"):
        for dev in ("aaaaaa", "bbbbbb", "cccccc")[: rnd.randint(1, 3)]:
            rows = write(p.path("decisions", f"{who}@{dev}.jsonl"), rnd.randint(1, 40),
                         lambda k: {"r": rnd.choice(ids), "s": "ta", "d": rnd.choice(["include", "exclude", "maybe"]), "n": k})
            if rnd.random() < 0.5:                             # a conflict copy holding part of the same rows
                with open(p.path("decisions", f"{who}@{dev}-LAPTOP.jsonl"), "w", encoding="utf-8") as fh:
                    fh.write("".join(json.dumps(r) + "\n" for r in rows[: rnd.randint(0, len(rows))]))
        write(p.path("state", f"resolutions@{who.lower()}0000.jsonl"), rnd.randint(1, 20),
              lambda k: {"r": rnd.choice(ids), "s": "ta", "d": rnd.choice(["include", "exclude"]), "by": "CH", "n": k})
        write(p.path("extraction", f"{who}@{rnd.choice(['aaaaaa', 'bbbbbb'])}.jsonl"), rnd.randint(1, 30),
              lambda k: {"s": rnd.choice(["S1", "S2"]), "f": rnd.choice(["n", "age"]), "v": str(k)})
    p._cache.clear()
    p._memo.clear()
    assert p.decisions() == _old_decisions(p)
    assert p._read_all(p.path("state", "resolutions*.jsonl"), "res") == _old_read_all(p.path("state", "resolutions*.jsonl"))
    assert review._rows(p, "extraction") == _old_rows(p, "extraction")


def test_repairing_an_own_file_keeps_the_order_written(proj, monkeypatch):
    """When an older copy of a reviewer's file is put back, this computer writes its rows back in the order it wrote
    them, not in the order of their stamps."""
    p = proj
    rid = screening.start(p, "CH", "main")["order"][0]
    at(monkeypatch, "2026-11-09T10:05:00.000+00:00")
    p.add_decision("NH", rid, "ta", "exclude")
    path = util.files_in(p.path("decisions"), "NH@*.jsonl")[0]
    with open(path, encoding="utf-8") as fh:
        older = fh.read()
    at(monkeypatch, "2026-11-09T10:01:30.000+00:00")
    p.add_decision("NH", rid, "ta", "include")
    with open(path, "w", encoding="utf-8") as fh:              # the sync service puts the older copy back
        fh.write(older)
    p._cache.clear()
    p._memo.clear()
    p._heal_own_files()
    assert [d["d"] for d in util.read_jsonl(path) if "d" in d] == ["exclude", "include"]
    assert screening.latest(screening.votes(p), "ta", rid, "NH")["d"] == "include"


# ------------------------------------------------------------------------------------------------ 5, 6. this computer's identity
def test_settings_changed_at_the_same_moment_are_all_kept(tmp_path, monkeypatch):
    """Two requests at once on a new computer (a name saved while the first vote makes the computer's ID and signing
    secret): each change used to be able to undo the other."""
    monkeypatch.setenv("REVIEWDESK_HOME", str(tmp_path / "home"))
    lost = 0
    for k in range(40):
        monkeypatch.setattr(userconfig, "_SEED", {}, raising=False)
        monkeypatch.setattr(userconfig, "_SECRET", {})
        try:
            os.remove(userconfig.path())
        except OSError:
            pass
        start, got = threading.Barrier(3), {}

        def one():
            start.wait()
            for i in range(3):
                userconfig.update(my_name=f"Colin {i}")

        def two():
            start.wait()
            got["secret"] = userconfig.device_secret()

        def three():
            start.wait()
            userconfig.set_identity(str(tmp_path / f"review{k}"), "CH", "key1")
            userconfig.remember_project(str(tmp_path / f"review{k}"), "A review")
        ts = [threading.Thread(target=f) for f in (one, two, three)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        cfg = userconfig.load()
        lost += (not cfg.get("device_seed") or got["secret"] not in (cfg.get("device_secrets") or {}).values()
                 or cfg.get("my_name") != "Colin 2" or not cfg.get("identity") or not cfg.get("recent"))
    assert lost == 0


def test_the_computer_id_seed_is_kept_in_memory_once_read(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWDESK_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(userconfig, "_SEED", {}, raising=False)
    first = userconfig.device_id()
    os.remove(userconfig.path())                        # settings.json lost while ReviewDesk runs
    assert userconfig.device_id() == first
    userconfig.update(my_name="Colin")
    assert userconfig.load().get("device_seed")         # and written back with the next change


def _id(seed: str, host: str) -> str:
    import hashlib
    return hashlib.sha256(f"{seed}|{host}".encode("utf-8")).hexdigest()[:6]


def test_a_mac_keeps_its_id_when_its_host_name_follows_the_network(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWDESK_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(userconfig, "_SEED", {}, raising=False)
    monkeypatch.setattr(userconfig, "_HOST", {}, raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: "Colins-MacBook-Pro.local")
    home = userconfig.device_id()
    monkeypatch.setattr(userconfig, "_SEED", {}, raising=False)              # ReviewDesk started again, on campus
    monkeypatch.setattr(userconfig, "_HOST", {}, raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: "dhcp-10-4-17-203.eduroam.example.ac.uk")
    assert userconfig.device_id() == home
    assert userconfig.load()["device_host"] == "Colins-MacBook-Pro.local"


def test_an_existing_installation_keeps_its_id_on_every_system(tmp_path, monkeypatch):
    """An ID made by an earlier version (seed, no remembered host name) does not change; on a Mac the host name of
    that first start is remembered from then on."""
    for platform in ("win32", "linux", "darwin"):
        home = tmp_path / platform
        home.mkdir()
        (home / "settings.json").write_text(json.dumps({"device_seed": "0123456789abcdef"}), encoding="utf-8")
        monkeypatch.setenv("REVIEWDESK_HOME", str(home))
        monkeypatch.setattr(sys, "platform", platform)
        monkeypatch.setattr(userconfig, "_SEED", {}, raising=False)
        monkeypatch.setattr(userconfig, "_HOST", {}, raising=False)
        monkeypatch.setattr(socket, "gethostname", lambda: "DESK-0042")
        assert userconfig.device_id() == _id("0123456789abcdef", "DESK-0042")
        assert ("device_host" in userconfig.load()) == (platform == "darwin")


def test_windows_and_linux_still_tell_two_computers_with_one_roaming_settings_folder_apart(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWDESK_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(userconfig, "_SEED", {}, raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: "PC-0001")
    a = userconfig.device_id()
    monkeypatch.setattr(socket, "gethostname", lambda: "PC-0002")       # the same roaming profile on another desktop
    assert userconfig.device_id() != a


# ------------------------------------------------------------------------------------------------ 3. export IDs
def _same_id_as(p, m_new: dict, m_old: dict) -> None:
    """What two computers drawing one number did: the details file of m_new carries m_old's ID."""
    path = os.path.join(p.path("imports"), m_new["filename"] + ".meta.json")
    with open(path, encoding="utf-8") as fh:
        meta = json.load(fh)
    meta["id"] = m_old["id"]
    util.write_json_atomic(path, meta)


def test_new_export_ids_have_eight_random_digits(proj):
    m = proj.add_import("CH", "embase.ris", ris(*tiny_records(3, seed=9)), {"set": "main", "database": "Embase"})
    assert len(m["id"].rsplit("-", 1)[1]) == 8


def test_two_exports_with_one_id_are_refused_before_any_is_left_out(proj):
    p = proj
    a = p.add_import("NH", "cinahl.ris", ris(*tiny_records(7, seed=31, prefix="CINAHL ")), {"set": "main", "database": "CINAHL"})
    b = p.add_import("NH", "scopus.ris", ris(*tiny_records(9, seed=32, prefix="Scopus ")), {"set": "main", "database": "Scopus"})
    _same_id_as(p, b, a)
    before = len(p.records())
    with pytest.raises(ProjectError) as e:
        p.process_imports("CH")
    msg = str(e.value)
    assert "Two exported files have the same ID" in msg and "cinahl.ris" in msg and "scopus.ris" in msg
    assert "remove one of them and add it again" in msg and "Nothing was changed." in msg
    assert len(p.records()) == before


def test_an_id_shared_after_it_was_used_says_which_file_was_left_out(proj):
    p = proj
    a = p.add_import("NH", "cinahl.ris", ris(*tiny_records(7, seed=31, prefix="CINAHL ")), {"set": "main", "database": "CINAHL"})
    p.process_imports("CH")
    b = p.add_import("NH", "scopus.ris", ris(*tiny_records(9, seed=32, prefix="Scopus ")), {"set": "main", "database": "Scopus"})
    _same_id_as(p, b, a)
    with pytest.raises(ProjectError) as e:
        p.process_imports("CH")
    msg = str(e.value)
    assert "has so far read only cinahl.ris" in msg and "scopus.ris.meta.json" in msg
    assert "out of the imports folder" in msg and "Nothing was changed." in msg


# ------------------------------------------------------------------------------------------------ 4. a moved project folder
def _restorable(p) -> dict:
    return {k: len(v) for k, v in p.backups().items()}


def test_a_renamed_project_folder_keeps_this_computers_copies(proj, tmp_path):
    p = proj
    before = _restorable(p)
    assert before.get("state/state.json")
    new = str(tmp_path / "review renamed")
    os.rename(p.dir, new)
    q = Project(new)
    assert _restorable(q) == before
    assert q.my_id() == "CH"
    assert len(os.listdir(os.path.join(userconfig.data_dir(), "projects"))) == 1
    assert any("have moved with it" in n for n in q.repairs())


def test_a_copy_of_the_project_folder_never_takes_the_originals_copies(proj, tmp_path):
    import shutil
    p = proj
    copy = str(tmp_path / "practice copy")
    shutil.copytree(p.dir, copy)
    q = Project(copy)
    assert _restorable(q) == {}                          # the original is still where it was: the copy starts afresh
    assert _restorable(Project(p.dir))                   # and the original keeps its own


def test_a_copy_opened_while_the_real_projects_drive_is_away_does_not_take_its_copies(tmp_path):
    """The real project is on a drive (or network share) that is not connected just now, so its whole place is gone,
    not only its folder. A practice copy opened meanwhile must start afresh: when the drive comes back, the real
    project still has this computer's copies of its files."""
    import shutil
    drive = tmp_path / "drive" / "reviews"
    os.makedirs(drive)
    p = Project.create(str(drive / "review"), "A review", "Colin Hawthorne", "CH")
    p.save_state(p.state(), "CH")
    before = _restorable(p)
    assert before.get("state/state.json")
    copy = str(tmp_path / "practice copy")
    shutil.copytree(p.dir, copy)
    os.rename(tmp_path / "drive", tmp_path / "drive-unplugged")       # the drive goes away, with everything on it
    q = Project(copy)
    assert _restorable(q) == {} and not any("have moved with it" in n for n in q.repairs())
    os.rename(tmp_path / "drive-unplugged", tmp_path / "drive")       # ... and comes back
    assert _restorable(Project(p.dir)) == before


def test_when_two_places_could_be_the_old_one_nothing_is_taken(proj, tmp_path):
    import shutil
    p = proj
    copy = str(tmp_path / "second")
    shutil.copytree(p.dir, copy)
    Project(copy).save_state(Project(copy).state(), "CH")          # the second place gets copies of its own
    new = str(tmp_path / "third")
    shutil.copytree(p.dir, new)
    shutil.rmtree(p.dir)
    shutil.rmtree(copy)
    assert _restorable(Project(new)) == {}


# ------------------------------------------------------------------------------------------------ 7. times as instants
def test_the_newest_copy_is_the_newest_across_the_end_of_summer_time(proj, monkeypatch):
    import datetime as dt
    import time as _time
    p = proj
    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/London"
    if hasattr(_time, "tzset"):
        _time.tzset()
    real_time = _time.time
    try:
        for label, h, m in (("01:30 BST", 0, 30), ("01:05 GMT", 1, 5)):
            e = dt.datetime(2026, 10, 25, h, m, tzinfo=dt.timezone.utc).timestamp()
            _time.time = lambda e=e: e
            st = p.state()
            st["note"] = label
            p.save_state(st, "CH")
        _time.time = real_time
        newest = [c["file"] for c in p.backups()["state/state.json"] if "20261025" in c["file"]][0]   # (the night only)
        with open(os.path.join(p._local("backups"), newest), encoding="utf-8") as fh:
            assert json.load(fh)["note"] == "01:05 GMT"
    finally:
        _time.time = real_time
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        if hasattr(_time, "tzset"):
            _time.tzset()


def test_copies_named_by_earlier_versions_are_still_listed_and_come_before_new_ones(proj):
    from reviewdesk.project import backup_stamp, backup_when
    p = proj
    d = p._local("backups")
    with open(os.path.join(d, "state__state.json.29991231-235959"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"rev": 1}))
    p.save_state(p.state(), "CH")
    names = [c["file"] for c in p.backups()["state/state.json"]]
    assert names[0].rpartition(".")[2].startswith("u") and "state__state.json.29991231-235959" in names
    assert backup_when("x.json.29991231-235959") == "2999-12-31 23:59"
    assert backup_when("x.json." + backup_stamp(0))[:4] in ("1969", "1970")


def test_two_versions_of_the_seal_from_two_time_zones_combine_by_the_moment(proj):
    p = proj
    a = {"records": {}, "sealed": "2026-11-09T10:30:00.000+13:00", "updated": "2026-11-09T10:30:00.000+13:00"}
    b = {"records": {}, "sealed": "2026-11-09T00:00:00.000+00:00", "updated": "2026-11-09T00:00:00.000+00:00"}
    m = p._merge_seals(a, b)                      # 10:30 at +13:00 is 21:30 the day before in UTC
    assert m["sealed"] == a["sealed"] and m["updated"] == b["updated"]


# ------------------------------------------------------------------------------------------------ 2. the lead on two computers
def _two_computers(tmp_path, swap: bool = False):
    """The auditor's case (sync B3): the lead adds an update search on the desktop and starts screening it, both
    reviewers include all five records; the laptop, not caught up, adds a hand-search export, which takes the same
    record IDs. OneDrive then keeps second versions of the lead's files. swap: the laptop's versions won the names."""
    from helpers import Desk, FakeOneDrive, on_computer, reference_plan
    root = str(tmp_path)
    copies = {"DESKTOP": f"{root}/desk/proj", "LAPTOP": f"{root}/lap/proj", "NADINE-PC": f"{root}/nh/proj"}
    od = FakeOneDrive(f"{root}/cloud", copies)
    with on_computer(f"{root}/home-desk"):
        p = Project.create(copies["DESKTOP"], "A review", "Dr Colin Hawthorne", "CH")
        p.update_settings("CH", {"question": "Q", "criteria": {"include": ["AI triage"], "exclude": [], "rules": []}, "reviewers": PEOPLE,
                                 "screening": reference_plan(mode="dual", calibration_size=0, ai_second_screener=False)})
        record_plan(p)
        p.add_import("CH", "medline.ris", ris(*tiny_records(12, seed=61)), {"set": "main", "database": "MEDLINE"})
        p.process_imports("CH")
    desktop = Desk(f"{root}/home-desk", copies["DESKTOP"], "CH")
    desktop.do(screening.start, desktop.p, "CH", "main")
    od.sync()
    laptop = Desk(f"{root}/home-lap", copies["LAPTOP"], "CH")
    nh = Desk(f"{root}/home-nh", copies["NADINE-PC"], "NH")
    with on_computer(desktop.home):
        desktop.p.add_import("CH", "embase.ris", ris(*tiny_records(5, seed=71, prefix="Embase: ")), {"set": "update", "database": "Embase"})
        desktop.p.process_imports("CH")
        screening.start(desktop.p, "CH", "update")
    od.sync("DESKTOP", "NADINE-PC")
    nh.reopen()
    emb = {r["id"]: r["title"] for r in desktop.p.records() if r["set"] == "update"}
    for rid in sorted(emb):
        nh.do(nh.p.add_decision, "NH", rid, "ta", "include")
        desktop.do(desktop.p.add_decision, "CH", rid, "ta", "include")
    od.sync("NADINE-PC", "DESKTOP")
    with on_computer(laptop.home):
        laptop.p.add_import("CH", "handsearch.ris", ris(*tiny_records(5, seed=72, prefix="Hand: ")), {"set": "other", "database": "NEJM AI"})
        laptop.p.process_imports("CH")
    assert {r["id"] for r in laptop.p.records() if r["set"] == "other"} == set(emb)      # the IDs were given out twice
    od.upload("LAPTOP")
    for c in copies:
        od.download(c)
    if swap:
        d = desktop.p.path("state")
        for n in ("state", "records", "dedup"):
            os.replace(f"{d}/{n}.json", f"{d}/{n}.tmp")
            os.replace(f"{d}/{n}-LAPTOP.json", f"{d}/{n}.json")
            os.replace(f"{d}/{n}.tmp", f"{d}/{n}-DESKTOP.json")
    desktop.reopen()
    return desktop, emb


def _votes_on_their_records(desk, emb) -> None:
    from helpers import on_computer
    with on_computer(desk.home):
        p = desk.reopen()
        by = p.by_id()
        assert all(by[rid]["title"] == t for rid, t in emb.items())
        V = screening.votes(p)["ta"]
        assert all(V[rid][w]["latest"]["d"] == "include" for rid in emb for w in ("CH", "NH"))
        assert p.integrity_report()["problems"] == []


@pytest.mark.parametrize("swap", [False, True])
@pytest.mark.parametrize("how", ["file by file", "for the group"])
def test_following_the_advice_never_attaches_a_vote_to_another_record(tmp_path, swap, how):
    """The three files are one decision, advised by the state's revision; following the highlighted button (on any of
    the three, or for the whole group) and then the next step it names leaves every vote on the record it was cast
    on, and adds the export from the other computer with IDs of its own."""
    from helpers import on_computer
    desktop, emb = _two_computers(tmp_path, swap)
    with on_computer(desktop.home):
        p = desktop.p
        groups = p.lead_conflicts()
        sc = next(g["state"] for g in groups if g.get("state"))
        assert sc["advice"] == ("use_other" if swap else "keep")
        assert "revision 7, the other 6" in sc["why"]
        files = [f for g in groups for f in g["files"] if f.get("together")]
        assert len(files) == 3 and {f["advice"] for f in files} == {sc["advice"]}
        if how == "file by file":
            for f in files:
                (p.use_conflict_copy if f["advice"] == "use_other" else p.keep_current_version)("CH", f["copy"])
        else:
            g = next(g for g in groups if g.get("state"))
            (p.use_conflict_copies if sc["advice"] == "use_other" else p.keep_current_versions)("CH", g["suffix"])
        assert p.lead_conflicts() == []
        notes = " ".join(p.sync_warnings())
        assert "Then choose Remove duplicates on the Import records page: 1 export (handsearch.ris)" in notes
    _votes_on_their_records(desktop, emb)
    with on_computer(desktop.home):
        p = desktop.reopen()
        p.process_imports("CH")                       # the next step the advice names
        assert len(p.records()) == 22 and sum(r["set"] == "other" for r in p.records()) == 5
        assert not any(r["title"].startswith("Hand:") and r["id"] in emb for r in p.records())
    _votes_on_their_records(desktop, emb)


@pytest.mark.parametrize("swap", [False, True])
def test_the_choice_that_would_move_votes_to_other_records_is_refused(tmp_path, swap):
    from helpers import on_computer
    desktop, emb = _two_computers(tmp_path, swap)
    with on_computer(desktop.home):
        p = desktop.p
        g = next(g for g in p.lead_conflicts() if g.get("state"))
        wrong = p.keep_current_versions if swap else p.use_conflict_copies
        with pytest.raises(ProjectError) as e:
            wrong("CH", g["suffix"])
        assert "5 records that have been screened" in str(e.value) and "Nothing was changed." in str(e.value)
        assert len([f for g in p.lead_conflicts() for f in g["files"] if f.get("together")]) == 3     # nothing settled
    if not swap:
        _votes_on_their_records(desktop, emb)


def test_a_tie_is_settled_by_the_seal_and_the_votes(tmp_path):
    from helpers import on_computer
    desktop, emb = _two_computers(tmp_path)
    path = desktop.p.path("state", "state-LAPTOP.json")
    st = util.read_json(path, {})
    st["rev"] = 7                                          # both computers saved the state as often
    util.write_json_atomic(path, st)
    with on_computer(desktop.home):
        sc = next(g["state"] for g in desktop.p.lead_conflicts() if g.get("state"))
        assert sc["advice"] == "keep"
        assert sc["why"].startswith("Neither screening state is later (both have revision 7), but in the second version 5 records")


def test_when_neither_looks_newer_it_says_so_and_what_each_holds(tmp_path):
    from helpers import on_computer
    desktop, emb = _two_computers(tmp_path)
    with on_computer(desktop.home):
        p = desktop.p
        for d in os.listdir(p.path("decisions")):           # (no votes and no seal to tell them apart)
            os.remove(p.path("decisions", d))
        import shutil
        shutil.rmtree(p._local("mirror"), ignore_errors=True)
        for n in os.listdir(p.path("state")):
            if n.startswith("seal"):
                os.remove(p.path("state", n))
        path = p.path("state", "state-LAPTOP.json")
        st = util.read_json(path, {})
        st["rev"] = 7
        util.write_json_atomic(path, st)
        p._cache.clear()
        p._memo.clear()
        sc = next(g["state"] for g in p.lead_conflicts() if g.get("state"))
        assert sc["advice"] is None and sc["why"].startswith("Neither version looks newer (both have revision 7)")
        assert sc["current"].startswith("The version in use: revision 7") and "17 records from 2 exports" in sc["current"]
        assert sc["other"].startswith("The second version: revision 7")


def test_records_mixed_from_two_computers_are_named_as_such_and_remove_duplicates_puts_them_right(tmp_path):
    """After the fact (the files were mixed by an earlier version, file by file): the data problem says what happened and
    what to do, and does not offer 'This change is correct'."""
    from helpers import on_computer
    desktop, emb = _two_computers(tmp_path)
    d = desktop.p.path("state")
    for n in ("records", "dedup"):
        os.replace(f"{d}/{n}-LAPTOP.json", f"{d}/{n}.json")
    os.remove(f"{d}/state-LAPTOP.json")
    with on_computer(desktop.home):
        p = desktop.reopen()
        pr = next(x for x in p.integrity_report()["problems"] if "now hold" in x["text"])
        assert pr["text"].startswith("5 screened records (")
        assert "now hold the records of handsearch.ris instead" in pr["text"] and "Choose Remove duplicates" in pr["text"]
        assert "accept" not in pr["fixes"]
        p.process_imports("CH")
    _votes_on_their_records(desktop, emb)


def test_a_computer_that_has_votes_on_ids_its_state_has_not_given_out_does_not_add_exports(tmp_path, proj):
    p = proj
    p.add_import("CH", "embase.ris", ris(*tiny_records(3, seed=9, prefix="Embase: ")), {"set": "main", "database": "Embase"})
    rows = [{"t": util.now(), "r": "R00042", "s": "ta", "d": "include"}]
    with open(p.path("decisions", "NH@abcdef.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in rows))
    p._memo.clear()
    with pytest.raises(ProjectError) as e:
        p.process_imports("CH")
    assert "record ID R00042 is already in use" in str(e.value) and "Wait for OneDrive" in str(e.value)
