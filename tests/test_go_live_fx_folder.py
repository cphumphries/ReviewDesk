"""Final audit (fx-folder): what the data checks notice in the shared folder, and the cache of results.

Two computers share one project folder: the lead's (CH) and the reviewer's (NH), each with its own settings folder."""
from __future__ import annotations

import json
import os
import shutil

import pytest

from helpers import Desk, read_json, record_plan, reference_plan, ris, tiny_records, write_bytes, write_text
from realistic import make_pdf
from reviewdesk import fulltext, integrity, prisma, project as P, screening, util
from reviewdesk.project import MARKER, MissingFile, OlderCopy, Project

PEOPLE = [{"id": "CH", "name": "Dr Colin Hawthorne", "role": "lead"}, {"id": "NH", "name": "Nadine Hayes", "role": "reviewer"}]


def setup_two(tmp_path, n=8, start=True, plan=None):
    """The lead's computer makes the project and starts dual screening; both vote on every record."""
    folder = str(tmp_path / "OneDrive" / "AI triage review")
    ch_home = str(tmp_path / "home-CH")
    from helpers import on_computer
    with on_computer(ch_home):
        p = Project.create(folder, "AI triage review", "Dr Colin Hawthorne", "CH")
        p.update_settings("CH", {"question": "Q", "criteria": {"include": ["AI triage"], "exclude": [], "rules": []},
                                 "reviewers": PEOPLE,
                                 "screening": plan or reference_plan(mode="dual", calibration_size=0, ai_second_screener=False)})
        record_plan(p)
        if n:
            p.add_import("CH", "medline.ris", ris(*tiny_records(n, seed=7)), {"set": "main", "database": "MEDLINE"})
            p.process_imports("CH")
    ch = Desk(ch_home, folder, "CH")
    nh = Desk(str(tmp_path / "home-NH"), folder, "NH")
    if n and start:
        ch.do(screening.start, ch.p, "CH", "main")
        for r in sorted(x["id"] for x in ch.p.records()):
            nh.do(nh.p.add_decision, "NH", r, "ta", "exclude")
            ch.do(ch.p.add_decision, "CH", r, "ta", "exclude")
    return folder, ch, nh


def problems(d):
    d.reopen()
    return d.do(d.p.integrity_report)["problems"]


# ------------------------------------------------------------------------------------------------ B1: the cache
def test_the_prisma_counts_follow_a_search_log_entry_in_the_same_session(tmp_path):
    folder, ch, nh = setup_two(tmp_path, start=False)
    p = ch.p
    with P.request_scope():
        assert ch.do(prisma.counts, p)["db"]["registers_not_imported"] == {}
    ch.do(p.save_search, "CH", {"database": "ClinicalTrials.gov", "platform": "", "date": "2026-10-05", "string": "triage",
                                "results": "12"})
    with P.request_scope():
        assert ch.do(prisma.counts, p)["db"]["registers_not_imported"] == {"ClinicalTrials.gov": 12}


def test_a_setting_saved_twice_within_one_file_time_tick_is_never_ignored(tmp_path, monkeypatch):
    """A disk that keeps file times to 2 seconds (FAT, exFAT): two saves of the same length look the same by size
    and time alone."""
    folder, ch, nh = setup_two(tmp_path, n=4, start=False)
    real = os.replace

    def coarse(src, dst, *a, **kw):
        real(src, dst, *a, **kw)
        t = (os.stat(dst).st_mtime_ns // 2_000_000_000) * 2_000_000_000
        os.utime(dst, ns=(t, t))
    monkeypatch.setattr(os, "replace", coarse)
    p = ch.p
    with ch:
        p.update_settings("CH", {"screening": {"kappa_threshold": 0.6}})
        assert p.settings_ro()["screening"]["kappa_threshold"] == 0.6
        p.memo("k", lambda: p.settings_ro()["screening"]["kappa_threshold"])
        p.update_settings("CH", {"screening": {"kappa_threshold": 0.9}})
        assert p.settings_ro()["screening"]["kappa_threshold"] == 0.9
        assert p.memo("k", lambda: p.settings_ro()["screening"]["kappa_threshold"]) == 0.9


def test_the_index_of_records_is_never_taken_from_another_list_at_the_same_address(tmp_path, monkeypatch):
    folder, ch, nh = setup_two(tmp_path, n=4, start=False)
    p = ch.p
    with ch:
        monkeypatch.setattr(P, "id", lambda x: 1, raising=False)       # every object at "the same address"
        first = p.by_id()
        recs = json.loads(json.dumps(p.records()))
        recs[0]["title"] = "Another title"
        p._cache["records"] = (p._cache["records"][0], recs)
        assert p.by_id()[recs[0]["id"]]["title"] == "Another title" and p.by_id() is not first


# ------------------------------------------------------------------------------------------------ B2: a file that has gone
def test_a_reviewers_file_deleted_from_the_folder_is_reported_and_put_back(tmp_path):
    folder, ch, nh = setup_two(tmp_path)
    assert problems(ch) == []                             # the lead's computer has seen NH's file
    f = os.path.join(folder, "decisions", f"NH@{nh.dev}.jsonl")
    os.remove(f)
    (pr,) = problems(ch)
    assert pr["kind"] == "chain" and pr["fixes"] == ["chain"] and pr["file"] == f"decisions/NH@{nh.dev}.jsonl"
    assert pr["text"].startswith(f"Nadine Hayes's screening decisions (decisions/NH@{nh.dev}.jsonl): the file is missing "
                                 "from the project folder, so 8 rows this computer had seen")
    assert pr["text"].endswith("are not counted. If it was deleted or renamed, put it back.")
    ch.do(ch.p.restore_chained, "CH", pr["file"])
    assert problems(ch) == [] and len(ch.do(ch.reopen().decisions)["NH"]) == 8


def test_a_missing_file_is_not_reported_when_nothing_is_lost(tmp_path):
    folder, ch, nh = setup_two(tmp_path)
    f = os.path.join(folder, "decisions", f"NH@{nh.dev}.jsonl")
    copy = os.path.join(folder, "decisions", f"NH@{nh.dev} (1).jsonl")
    shutil.copy(f, copy)
    assert problems(ch) == []                             # the lead's computer has seen both
    os.remove(copy)                                       # a copy the sync service made, tidied away: nothing is lost
    assert problems(ch) == []
    # a person removed from the project in the ordinary way
    ch.do(ch.p.update_settings, "CH", {"reviewers": PEOPLE + [{"id": "AB", "name": "Ann Brown", "role": "reviewer"}]})
    ab = Desk(str(tmp_path / "home-AB"), folder, "AB")
    ab.do(ab.p.add_decision, "AB", ch.p.records()[0]["id"], "ta", "include")
    assert problems(ch) == []
    ch.do(ch.p.update_settings, "CH", {"reviewers": PEOPLE})
    os.remove(os.path.join(folder, "decisions", f"AB@{ab.dev}.jsonl"))
    assert problems(ch) == []
    # a computer that never saw a file reports nothing about it (a new computer, files still arriving)
    other = Desk(str(tmp_path / "home-CH2"), folder, "CH")
    os.remove(f)
    assert problems(other) == []


def test_a_missing_screening_state_is_not_read_as_a_review_not_started(tmp_path):
    folder, ch, nh = setup_two(tmp_path)
    st = os.path.join(folder, "state", "state.json")
    keep = st + ".away"
    os.rename(st, keep)
    with pytest.raises(MissingFile) as e:
        ch.reopen().state_ro()
    assert "state/state.json is not on this computer yet" in str(e.value) and e.value.what == "state/state.json"
    with pytest.raises(MissingFile):
        ch.do(ch.reopen().process_imports, "CH")          # nothing is built over the review in progress
    used = ch.do(ch.p.restore_backup, "CH", "state/state.json")
    assert used and ch.reopen().state_ro()["sets"]["main"]["phase"] == "screening"
    # a brand-new project (nothing imported) still opens normally
    f2, c2, _ = setup_two(tmp_path / "new", n=0)
    os.remove(os.path.join(f2, "state", "state.json"))
    assert c2.reopen().state_ro() == {}


def test_restoring_a_copy_keeps_the_file_in_place_until_the_copy_is_written(tmp_path, monkeypatch):
    folder, ch, nh = setup_two(tmp_path)
    st = os.path.join(folder, "state", "state.json")
    write_text(st, '{"rev": 3, "sets": {"main": {"pha')                 # damaged
    def full(path, text):
        raise OSError(28, "No space left on device", path)
    monkeypatch.setattr(util, "write_text_atomic", full)
    with pytest.raises(OSError):
        ch.do(ch.p.restore_backup, "CH", "state/state.json")
    assert os.path.isfile(st)                              # never a project without its screening state


# ------------------------------------------------------------------------------------------------ S4 and S5: older versions
def test_an_older_version_put_back_before_the_lead_read_the_new_one_again_is_reported(tmp_path):
    """Each version is remembered when this computer writes it, not only when it next reads it: the lead records an
    amendment and closes ReviewDesk; an older project file put back before the next opening is still seen as older."""
    folder, ch, nh = setup_two(tmp_path)
    st, pf = os.path.join(folder, "state", "state.json"), os.path.join(folder, MARKER)
    ch.reopen().state_ro(); ch.p.settings_ro()
    old_st, old_pf = open(st, "rb").read(), open(pf, "rb").read()
    import time
    time.sleep(0.01)
    ch.do(ch.p.mark_not_duplicate, "CH", ch.p.records()[0]["id"], ch.p.records()[1]["id"])   # written, never read again
    ch.do(ch.p.update_settings, "CH", {"highlight": {"include": ["triage"], "exclude": []}})
    write_bytes(st, old_st)
    write_bytes(pf, old_pf)
    assert {x["file"] for x in problems(ch)} == {"state/state.json", MARKER}


def test_an_older_screening_state_or_project_file_put_back_is_reported(tmp_path):
    folder, ch, nh = setup_two(tmp_path)
    st, pf = os.path.join(folder, "state", "state.json"), os.path.join(folder, MARKER)
    old_st, old_pf = open(st, "rb").read(), open(pf, "rb").read()
    nh.reopen().state_ro(); nh.p.settings_ro()
    import time
    time.sleep(0.01)
    ch.do(ch.p.mark_not_duplicate, "CH", ch.p.records()[0]["id"], ch.p.records()[1]["id"])   # a new revision
    ch.do(ch.p.update_settings, "CH", {"highlight": {"include": ["triage"], "exclude": []}})
    for d in (ch, nh):
        d.reopen().state_ro(); d.p.settings_ro()
        assert problems(d) == []
    write_bytes(st, old_st)                                # Version history, or the sync service, puts back older ones
    write_bytes(pf, old_pf)
    for d in (ch, nh):
        got = {x["file"]: x for x in problems(d)}
        assert set(got) == {"state/state.json", MARKER}
        assert got["state/state.json"]["fixes"] == ["backup"]
        assert got["state/state.json"]["text"].startswith("The screening state (state/state.json) is older than a version "
                                                          "this computer has already used: revision ")
        assert got[MARKER]["text"].startswith(f"The project file ({MARKER}) is older than a version this computer has "
                                              "already used")
    # the lead puts back the newer copies: nothing is reported on either computer, and nothing is lost
    for rel in ("state/state.json", MARKER):
        ch.do(ch.p.restore_backup, "CH", rel)
    assert problems(ch) == [] and problems(nh) == []
    assert ch.reopen().settings_ro()["highlight"]["include"] == ["triage"]


def test_restore_warns_about_a_newer_version_saved_on_the_leads_other_computer(tmp_path):
    from reviewdesk import robplan
    from helpers import rob_plan
    folder, ch, nh = setup_two(tmp_path, start=False)
    lap = Desk(str(tmp_path / "home-CH-laptop"), folder, "CH")
    lap.do(lap.p.update_settings, "CH", {"highlight": {"include": ["triage"], "exclude": []}})     # the laptop's own copy
    import time
    time.sleep(0.01)
    ch.reopen()
    ch.do(robplan.save_plan, ch.p, "CH", rob_plan("rct", "nrsi"), "The protocol was amended")     # not "change settings"
    lap.reopen()
    with pytest.raises(OlderCopy) as e:
        lap.do(lap.p.restore_backup, "CH", MARKER)
    assert "but a newer version of reviewdesk-project.json was saved after it" in str(e.value)
    assert "this computer has no copy of that version" in str(e.value)
    lap.do(lap.p.restore_backup, "CH", MARKER, "", True)                                # the lead is sure
    # a copy this computer saved itself is not "elsewhere"
    ch.reopen()
    ch.do(ch.p.update_settings, "CH", {"highlight": {"include": ["triage", "emergency"], "exclude": []}})
    ch.do(ch.p.restore_backup, "CH", MARKER)


# ------------------------------------------------------------------------------------------------ S6: PDFs still arriving
def test_a_pdf_whose_record_has_not_arrived_is_left_alone_not_recorded_as_this_computers(tmp_path):
    folder, ch, nh = setup_two(tmp_path, n=4)
    rid = sorted(r["id"] for r in ch.p.records())[0]
    nh.do(fulltext.save_pdf, nh.p, "NH", rid, make_pdf("A paper", "10.1000/x"), "found by the free-copy search", "https://example.org/x")
    idx = os.path.join(folder, "fulltexts", f"index@{nh.dev}.jsonl")
    away = idx + ".away"
    os.rename(idx, away)                                  # the PDF and NH's audit entry have arrived; her record has not
    assert problems(ch) == []
    assert not os.path.isfile(os.path.join(folder, "fulltexts", f"index@{ch.dev}.jsonl"))
    os.rename(away, idx)
    assert ch.do(ch.reopen().pdf_index)[rid]["by"] == "NH" and problems(ch) == []


def test_a_pdf_added_by_version_1_0_is_still_recorded(tmp_path):
    folder, ch, nh = setup_two(tmp_path, n=4)
    rid = sorted(r["id"] for r in ch.p.records())[1]
    write_bytes(os.path.join(folder, "fulltexts", f"{rid}.pdf"), make_pdf("An old paper", "10.1000/y"))
    util.append_jsonl(os.path.join(folder, "audit", "NH@abcdef.jsonl"),
                      {"t": "2026-01-05T10:00:00+00:00", "who": "NH", "action": "attach full text", "details": {"record": rid},
                       "version": "1.0.2", "computer": "abcdef"})
    assert problems(ch) == []
    assert ch.do(ch.p.pdf_index)[rid]["how"] == "added with an earlier version of ReviewDesk"


# ------------------------------------------------------------------------------------------------ S3: rows nobody signed
def test_a_file_with_no_fingerprints_and_unsigned_rows_after_signed_ones_are_reported(tmp_path):
    folder, ch, nh = setup_two(tmp_path, n=4)
    ids = sorted(r["id"] for r in ch.p.records())
    forged = os.path.join(folder, "decisions", "NH@c0ffee.jsonl")
    write_text(forged, "".join(json.dumps({"t": util.now(), "r": r, "s": "ta", "d": "include"}) + "\n" for r in ids))
    (pr,) = problems(ch)
    assert pr["file"] == "decisions/NH@c0ffee.jsonl" and "4 rows changed or added outside ReviewDesk" in pr["text"]
    os.remove(forged)
    assert problems(ch) == []
    f = os.path.join(folder, "decisions", f"NH@{nh.dev}.jsonl")
    rows = [json.loads(x) for x in open(f, encoding="utf-8") if x.strip()]
    extra = {"t": util.now(), "r": ids[0], "s": "ta", "d": "include"}
    extra[integrity.H] = integrity.row_hash(rows[-1][integrity.H], extra)       # a valid fingerprint, but no signature
    util.append_jsonl(f, extra)
    (pr,) = problems(ch)
    assert pr["file"] == f"decisions/NH@{nh.dev}.jsonl" and "1 row changed or added outside ReviewDesk" in pr["text"]


def test_rows_written_by_version_1_0_are_not_reported(tmp_path):
    folder, ch, nh = setup_two(tmp_path, n=4)
    ids = sorted(r["id"] for r in ch.p.records())
    util.append_jsonl(os.path.join(folder, "audit", "NH@abcdef.jsonl"),
                      {"t": "2026-01-05T10:00:00+00:00", "who": "NH", "action": "save search log entry", "details": {},
                       "version": "1.0.0", "computer": "abcdef"})
    util.append_jsonl(os.path.join(folder, "audit", "NH@abcdef.jsonl"),
                      {"t": "2026-02-05T10:00:00+00:00", "who": "NH", "action": "save search log entry", "details": {},
                       "version": "1.1.0", "computer": "abcdef"})
    old = os.path.join(folder, "decisions", "NH@abcdef.jsonl")
    write_text(old, "".join(json.dumps({"t": "2026-01-06T10:00:00+00:00", "r": r, "s": "ta", "d": "exclude"}) + "\n" for r in ids))
    assert problems(ch) == []
    util.append_jsonl(old, {"t": "2026-03-01T10:00:00+00:00", "r": ids[0], "s": "ta", "d": "include"})   # after 1.1 ran there
    (pr,) = problems(ch)
    assert pr["file"] == "decisions/NH@abcdef.jsonl"


# ------------------------------------------------------------------------------------------------ the seal (M2, M3, M5)
def test_sealing_again_writes_its_audit_entry_first(tmp_path, monkeypatch):
    folder, ch, nh = setup_two(tmp_path, n=4)
    os.remove(os.path.join(folder, "state", "seal.json"))
    real = util.write_json_atomic

    def stop(path, obj):
        real(path, obj)
        if path == os.path.join(folder, "state", "seal.json"):
            raise KeyboardInterrupt                       # ReviewDesk stopped just after writing the seal
    monkeypatch.setattr(util, "write_json_atomic", stop)
    with pytest.raises(KeyboardInterrupt):
        ch.do(ch.p.reseal, "CH", "The seal was deleted by mistake")
    monkeypatch.setattr(util, "write_json_atomic", real)
    assert problems(ch) == [] and problems(nh) == []


def test_records_of_a_started_set_left_unsealed_are_sealed_on_the_leads_computer(tmp_path, monkeypatch):
    folder, ch, nh = setup_two(tmp_path, n=4)
    ch.do(ch.p.add_import, "CH", "update.ris", ris(*tiny_records(3, seed=91, prefix="Update ")), {"set": "update", "database": "MEDLINE"})
    ch.do(ch.p.process_imports, "CH")
    monkeypatch.setattr(Project, "write_seal", lambda self, user, reason="": None)   # stopped before the seal
    ch.do(screening.start, ch.p, "CH", "update")
    monkeypatch.undo()
    upd = ch.reopen().state_ro()["sets"]["update"]["order"]
    assert problems(ch) == []
    assert all(r in read_json(os.path.join(folder, "state", "seal.json"))["records"] for r in upd)


def test_a_former_leads_seal_again_is_not_taken_for_tampering(tmp_path):
    folder, ch, nh = setup_two(tmp_path, n=4)
    os.remove(os.path.join(folder, "state", "seal.json"))
    ch.do(ch.reopen().reseal, "CH", "The seal was deleted by mistake")
    ch.do(ch.p.update_settings, "CH", {"reviewers": [{**PEOPLE[0], "role": "reviewer"}, {**PEOPLE[1], "role": "lead"}]})
    assert problems(ch) == [] and problems(nh) == []


def test_a_second_copy_of_an_audit_file_does_not_list_its_entries_twice(tmp_path):
    folder, ch, nh = setup_two(tmp_path, n=4)
    n = len(ch.reopen().audit_entries())
    f = os.path.join(folder, "audit", f"CH@{ch.dev}.jsonl")
    shutil.copy(f, f[:-6] + " (1).jsonl")
    assert len(ch.reopen().audit_entries()) == n
    assert any("an entry that is in both files is shown once" in w for w in ch.do(ch.p.sync_warnings))
