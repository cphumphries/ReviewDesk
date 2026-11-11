"""Fingerprints and signatures are whole from 1.3.1 on (a SHA-256 and an HMAC-SHA-256, 256 bits each), where earlier
versions kept only the start of each (80 and 64 bits). Three things have to hold:

* everything written now is whole;
* a project that an earlier version worked in goes on as it was: nothing in it is reported, nothing in it is written
  again, what the lead had accepted stays accepted, and what is added to it from now on is whole;
* a fingerprint of the old length is accepted nowhere else: not after a whole one in the same file, and nowhere at
  all in a project begun with this version.

helpers.earlier_version() makes ReviewDesk write as 1.3.0 did. NH's rows are written on NH's own computer (its own
settings folder, so its own device ID and secret); the lead's ReviewDesk runs here."""
from __future__ import annotations

import glob
import hashlib
import hmac
import json
import os

import pytest

from helpers import Desk, earlier_version, read_text, reference_plan, reference_project, ris, tiny_records, write_json
from realistic import make_pdf
from reviewdesk import VERSION, integrity, review, robplan, server, userconfig, versions
from reviewdesk.project import ProjectError

FULL, OLD_ROW, OLD_PRINT = 64, 20, 16


def build(desk, tmp_path):
    """Six records screened by both (NH on NH's computer), four at full text and included, the extraction form set, an
    answer and a risk-of-bias judgement from NH, a PDF, and the data checks run once on the lead's computer."""
    c = desk.client()
    folder = reference_project(c, str(tmp_path / "OneDrive"), screening=reference_plan(mode="dual", calibration_size=0, ai_second_screener=False))
    c.add_import(ris(*tiny_records(6, seed=51)), "medline.ris", "MEDLINE")
    c.process()
    c.post("/api/screening/start", {"set": "main"})
    nh = Desk(str(tmp_path / "home-NH"), folder, "NH")
    ids = sorted(r["id"] for r in c.get("/api/records")["records"])
    for r in ids:
        nh.do(nh.p.add_decision, "NH", r, "ta", "include" if r in ids[:4] else "exclude", note="by NH")
        c.decide(r, "ta", "include" if r in ids[:4] else "exclude")
    for r in ids[:4]:
        nh.do(nh.p.add_decision, "NH", r, "ft", "include", study="Study " + r)
        c.decide(r, "ft", "include", study="Study " + r)
    c.post("/api/extract/form", {"template": "generic", "first": "NH", "second": "CH"})
    nh.do(review.save_value, nh.p, "NH", ids[0], "country", "Aotearoa New Zealand")
    aid = c.post("/api/rob/define", {"key": ids[0], "design": "rct", "label": "Time to triage"})["rob"][0]["a"]
    nh.do(review.judge, nh.p, "NH", ids[0], aid, "D1", "Low risk", "Central randomisation")
    c.upload("/api/fulltext/upload", make_pdf("A trial of triage", pages=2), record=ids[0])
    assert problems(c) == []
    c.folder, c.nh, c.ids = folder, nh, ids
    return c


def problems(c):
    return c.get("/api/integrity")["report"]["problems"]


def fresh(c):
    """ReviewDesk started again on both computers: nothing remembered in memory."""
    p = server.APP.project
    p._cache.clear(); p._memo.clear()
    versions._noted.clear()
    c.nh.reopen()


def rows_of(path):
    return [json.loads(x) for x in read_text(path).splitlines() if x.strip()]


def write_rows(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def chained(folder):
    return sorted(f for sub in integrity.CHAINED for f in glob.glob(os.path.join(folder, sub, "*.jsonl")))


def lengths(folder):
    """{(length of fingerprint, length of signature)} over every chained row in the project."""
    return {(len(d["h"]), len(d.get("k", ""))) for f in chained(folder) for d in rows_of(f) if "h" in d}


def seal_of(folder):
    return json.loads(read_text(os.path.join(folder, "state", "seal.json")))


def plan_prints(folder):
    return [len(e["details"]["fingerprint"]) for f in glob.glob(os.path.join(folder, "audit", "*.jsonl")) for e in rows_of(f)
            if isinstance(e.get("details"), dict) and "fingerprint" in e["details"]]


def everything(folder):
    """Every file of the project folder, to see that nothing was written again."""
    out = {}
    for d, _, names in os.walk(folder):
        for n in names:
            with open(os.path.join(d, n), "rb") as fh:
                out[os.path.relpath(os.path.join(d, n), folder)] = fh.read()
    return out


# ------------------------------------------------------------------------------------------------ the functions
def test_a_fingerprint_is_a_whole_sha256_and_a_signature_a_whole_hmac():
    row = {"t": "2026-11-11T10:00:00", "r": "R00001", "s": "ta", "d": "include", "note": "naïve – “quoted”"}
    body = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert integrity.row_hash("", row) == hashlib.sha256(("\n" + body).encode("utf-8")).hexdigest()
    assert integrity.row_hash("abc", row) == hashlib.sha256(("abc\n" + body).encode("utf-8")).hexdigest()
    assert integrity.sign(row) == hmac.new(userconfig.device_secret().encode(), body.encode("utf-8"), hashlib.sha256).hexdigest()
    rec = {"title": "T", "abstract": "A", "authors": "X", "year": "2026", "journal": "J", "doi": "", "pmid": "", "pubtype": ""}
    assert len(integrity.record_print(rec)) == len(integrity.text_print({"q": 1})) == len(integrity.row_hash("", row)) == FULL
    assert integrity.row_hash("", {**row, "h": "x", "k": "y"}) == integrity.row_hash("", row)     # (they are not part of what is fingerprinted)


def test_a_label_is_not_a_check_and_stays_as_it_was():
    x = ["records", [("R00001", "0123456789abcdef")]]
    assert integrity.label(x) == hashlib.sha256(json.dumps(x, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    assert integrity.shortened("0123456789abcdef0123") == "0123456789abcdef"


def test_a_kept_fingerprint_of_the_old_length_is_compared_with_the_start_of_the_whole_one():
    now = integrity.text_print({"criteria": "adults"})
    assert integrity.same(now, now) and integrity.same(now, now, old=False)
    assert integrity.same(now[:16], now) and not integrity.same(now[:16], now, old=False)
    assert integrity.same(now[:20], now, short=20) and not integrity.same(now[:20], now)          # only its own old length
    for kept in ("", None, 5, now[:15], now[:17], now[:63], now + "0", now[:15] + "x", "x" * 64, now.upper()):
        assert not integrity.same(kept, now), kept


def _chain(rows, old_from=0, old_to=0):
    """The rows fingerprinted in order as ReviewDesk does, those in [old_from, old_to) as versions before 1.3.1 did."""
    out, prev = [], ""
    for k, r in enumerate(rows):
        d = dict(r)
        d["h"] = integrity.row_hash(prev, d)[:OLD_ROW] if old_from <= k < old_to else integrity.row_hash(prev, d)
        d["k"] = integrity.sign(d)[:OLD_PRINT] if old_from <= k < old_to else integrity.sign(d)
        out.append(d)
        prev = d["h"]
    return out


ROWS = [{"t": f"2026-11-0{k + 1}T09:00:00", "r": f"R0000{k + 1}", "s": "ta", "d": "include"} for k in range(6)]


def test_rows_of_an_earlier_version_are_accepted_until_the_first_whole_one_and_never_after():
    whole, old, mixed = _chain(ROWS), _chain(ROWS, 0, 6), _chain(ROWS, 0, 3)
    for rows in (whole, old, mixed):
        assert integrity.verify(rows) == [] and integrity.unsigned(rows) == []
    assert [len(d["h"]) for d in mixed] == [20, 20, 20, 64, 64, 64]
    assert integrity.verify(_chain(ROWS, 3, 6)) == [3, 4, 5]                # of the old length after whole ones
    assert integrity.verify(_chain(ROWS, 2, 3)) == [2]                      # one of them among whole ones
    assert integrity.verify(old, old=False) == [0, 1, 2, 3, 4, 5]           # a project begun with this version
    assert integrity.verify(mixed, old=False) == [0, 1, 2] and integrity.verify(whole, old=False) == []
    # it is the length that closes the door, right or wrong: after a fingerprint of the full length none of the old is accepted
    forged = _chain(ROWS, 0, 6)
    forged[2] = {**forged[2], "h": "0" * 64}
    assert integrity.verify(forged) == [2, 3, 4, 5]


def test_a_fingerprint_and_a_signature_of_different_times_are_not_a_row_reviewdesk_wrote():
    old, mixed = _chain(ROWS, 0, 6), _chain(ROWS, 0, 3)
    whole = mixed[5]
    for row in (old[0], whole, {"t": "x"}, {"t": "x", "h": old[0]["h"]}):  # (the last two: rows from before signatures)
        assert integrity.of_one_time(row)
    cut_h = {**whole, "h": whole["h"][:OLD_ROW]}                            # the fingerprint cut down, the signature left
    cut_k = {**whole, "k": whole["k"][:OLD_PRINT]}                          # the signature cut down, the fingerprint left
    long_k = {**old[0], "k": integrity.sign(old[0])}                        # a whole signature beside an old fingerprint
    for row in (cut_h, cut_k, long_k, {**whole, "h": 5}, {**whole, "h": []}, {**whole, "k": None}, {**whole, "k": {}},
                {**whole, "h": whole["h"][:OLD_PRINT], "k": whole["k"][:OLD_ROW]}):
        assert not integrity.of_one_time(row) and not integrity.signed_here(row), row
    assert integrity.verify(mixed[:5] + [cut_h]) == [5] and integrity.unsigned(mixed[:5] + [cut_h]) == [5]
    first = mixed[:3] + [{**mixed[3], "h": mixed[3]["h"][:OLD_ROW]}]        # even on the first whole row of a file, where a
    assert integrity.verify(first) == [3] and integrity.unsigned(first) == [3]   # fingerprint of the old length is allowed
    assert integrity.verify(first, old=False) == [0, 1, 2, 3]


def test_what_the_old_lengths_still_allow_and_what_they_do_not():
    """In a project begun with an earlier version the whole rows at the end of a file can be cut down to that version's
    lengths (integrity.py says so): the file alone cannot tell them from that version's rows. Nothing else can."""
    mixed = _chain(ROWS, 0, 3)
    cut = lambda d: {**d, "h": d["h"][:OLD_ROW], "k": d["k"][:OLD_PRINT]}   # noqa: E731
    assert integrity.verify(mixed[:5] + [cut(mixed[5])]) == [5]             # after a whole row: reported
    assert integrity.verify(mixed[:3] + [cut(mixed[3])] + mixed[4:]) == [4]      # before a whole row: it no longer follows
    alone = mixed[:3] + [cut(mixed[3])]
    assert integrity.verify(alone) == [] and integrity.unsigned(alone) == []     # (the limit; a computer that saw the
    assert integrity.verify(alone, old=False) == [0, 1, 2, 3]                    # row, and the one that wrote it, do tell)
    changed = [dict(d) for d in alone]
    changed[3]["d"] = "exclude"                                             # and its content is still held by both
    assert integrity.verify(changed) == [3] and integrity.unsigned(changed) == [3]


def test_a_row_changed_or_out_of_place_is_reported_whatever_the_length_of_its_fingerprint():
    for rows in (_chain(ROWS), _chain(ROWS, 0, 6), _chain(ROWS, 0, 3)):
        edited = [dict(d) for d in rows]
        edited[1]["d"] = "exclude"
        assert integrity.verify(edited) == [1]
        assert integrity.verify(rows[:1] + rows[2:]) == [1]                 # a row taken out: the next no longer follows
        assert integrity.verify([rows[1], rows[0]] + rows[2:]) == [0, 1, 2]
    for kept in (5, None, "", [], {}, ["a"], {"a": 1}, "0" * 20, "0" * 64, "g" * 64):
        rows = _chain(ROWS, 0, 3)
        rows[4] = {**rows[4], "h": kept}
        assert 4 in integrity.verify(rows)                                  # (and nothing here stops on a damaged row)


def test_a_signature_of_the_old_length_counts_only_beside_a_fingerprint_of_the_old_length():
    old, whole = _chain(ROWS, 0, 6)[0], _chain(ROWS)[0]
    assert integrity.signed_here(old) and integrity.signed_here(whole)
    assert not integrity.signed_here({**whole, "k": whole["k"][:16]})       # shortened beside a whole fingerprint
    assert not integrity.signed_here({**old, "k": "0" * 16}) and not integrity.signed_here({**whole, "k": "0" * 64})
    assert not integrity.signed_here({**old, "k": old["k"][:15]}) and not integrity.signed_here({k: v for k, v in old.items() if k != "k"})
    rows = _chain(ROWS, 0, 3)
    rows[4] = {**rows[4], "k": rows[4]["k"][:16]}
    assert integrity.unsigned(rows) == [4]


# ------------------------------------------------------------------------------------------------ written now
def test_everything_this_version_writes_is_whole(desk, tmp_path):
    c = build(desk, tmp_path)
    assert lengths(c.folder) == {(FULL, FULL)}
    seal = seal_of(c.folder)
    assert len(seal["records"]) == 6 and {len(v) for v in seal["records"].values()} == {FULL}
    assert len(seal["criteria"]) == len(seal["question"]) == FULL
    assert plan_prints(c.folder) == [FULL]
    assert all(len(v) == FULL for v in seal["imports"].values())             # (the exports and the PDFs always were)
    assert len(rows_of(glob.glob(os.path.join(c.folder, "fulltexts", "index@*.jsonl"))[0])[0]["sha"]) == FULL
    s = json.loads(read_text(os.path.join(c.folder, "reviewdesk-project.json")))
    assert s["created_with"] == VERSION and s["min_version"] == versions.NEEDS == "1.3.1"
    assert not server.APP.project._old_fingerprints()


def test_the_version_a_project_was_created_with_cannot_be_changed_from_a_page(desk, tmp_path):
    c = build(desk, tmp_path)
    e = c.error("POST", "/api/settings", {"changes": {"created_with": "1.2.0"}})
    assert "is not a setting that can be changed here" in e.message
    assert json.loads(read_text(os.path.join(c.folder, "reviewdesk-project.json")))["created_with"] == VERSION


# ------------------------------------------------------------------------------------------------ an earlier version's project
@pytest.fixture
def earlier(desk, tmp_path):
    """The same review, written as 1.3.0 wrote it, with a record's title changed outside ReviewDesk and that change
    recorded as correct by the lead: then ReviewDesk is started again as this version, on both computers."""
    with earlier_version():
        c = build(desk, tmp_path)
        c.nh.do(versions.note_open, c.nh.p, "NH")                           # (as opening the project in the browser does)
        f = os.path.join(c.folder, "state", "records.json")
        recs = json.loads(read_text(f))
        recs[5]["title"] += " (corrected)"
        write_json(f, recs)
        (pr,) = problems(c)
        c.post("/api/integrity/fix", {"kind": "accept", "sig": pr["sig"], "reason": "The publisher corrected the title."})
        assert problems(c) == [] and c.nh.do(c.nh.p.integrity_report)["problems"] == []
        c.accepted, c.changed = pr["sig"], recs[5]["id"]
    fresh(c)
    return c


def test_an_earlier_versions_project_is_as_that_version_wrote_it(earlier):
    c = earlier
    assert lengths(c.folder) == {(OLD_ROW, OLD_PRINT)}
    seal = seal_of(c.folder)
    assert {len(v) for v in seal["records"].values()} == {OLD_PRINT} and len(seal["criteria"]) == len(seal["question"]) == OLD_PRINT
    assert plan_prints(c.folder) == [OLD_PRINT]
    s = json.loads(read_text(os.path.join(c.folder, "reviewdesk-project.json")))
    assert s["created_with"] == "1.3.0" and s["min_version"] == "1.3.0" and server.APP.project._old_fingerprints()


def test_nothing_in_it_is_reported_or_written_again_and_what_was_accepted_stays_accepted(earlier):
    c = earlier
    before = everything(c.folder)
    assert c.summary()["data_problems"] == 0                                # the lead opens the project with this version
    rep = c.get("/api/integrity")["report"]
    assert rep["problems"] == [] and [a["sig"] for a in rep["accepted"]] == [c.accepted]
    assert c.nh.do(c.nh.p.integrity_report)["problems"] == []               # on NH's computer too
    assert robplan.outside_change(server.APP.project) is None
    after = everything(c.folder)
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert changed == {os.path.join("audit", f"CH@{server.APP.project.dev()}.jsonl"), "reviewdesk-project.json"}   # the note of
    # the opening, and what the project now needs (that one thing in the project file: see the lead's first opening below)
    for f in chained(c.folder):                                             # and every file still verifies, row for row
        assert integrity.verify(rows_of(f)) == [], f


def test_what_is_added_to_it_now_is_whole_and_the_file_goes_on(earlier):
    c = earlier
    c.decide(c.ids[4], "ta", "include", note="looked again")
    c.nh.do(c.nh.p.add_decision, "NH", c.ids[4], "ta", "include", note="looked again too")
    c.nh.do(review.save_value, c.nh.p, "NH", c.ids[0], "n", "812")
    for who, dev in (("CH", server.APP.project.dev()), ("NH", c.nh.dev)):
        rows = rows_of(os.path.join(c.folder, "decisions", f"{who}@{dev}.jsonl"))
        assert [(len(d["h"]), len(d["k"])) for d in rows[-2:]] == [(OLD_ROW, OLD_PRINT), (FULL, FULL)]
        assert integrity.verify(rows) == []
    assert lengths(c.folder) == {(OLD_ROW, OLD_PRINT), (FULL, FULL)}
    assert problems(c) == [] and c.nh.do(c.nh.p.integrity_report)["problems"] == []
    mine = c.nh.do(lambda: integrity.unsigned(rows_of(os.path.join(c.folder, "decisions", f"NH@{c.nh.dev}.jsonl"))))
    assert mine == []                                                       # NH's computer knows its own rows, old and new


def test_a_row_of_the_old_length_added_after_a_whole_one_is_reported(earlier):
    c = earlier
    c.nh.do(c.nh.p.add_decision, "NH", c.ids[4], "ta", "include", note="written by this version")
    assert problems(c) == []
    f = os.path.join(c.folder, "decisions", f"NH@{c.nh.dev}.jsonl")
    rows = rows_of(f)
    extra = {"t": "2026-11-11T12:00:00", "r": c.ids[5], "s": "ta", "d": "include"}
    extra["h"] = integrity.row_hash(rows[-1]["h"], extra)[:OLD_ROW]           # as an earlier version would have chained it
    extra["k"] = "0" * OLD_PRINT
    write_rows(f, rows + [extra])
    (pr,) = problems(c)
    assert pr["kind"] == "chain" and pr["file"].endswith(f"NH@{c.nh.dev}.jsonl") and "1 row changed or added outside ReviewDesk" in pr["text"]


def test_a_record_or_the_criteria_changed_outside_reviewdesk_is_still_noticed_against_the_old_seal(earlier):
    c = earlier
    f = os.path.join(c.folder, "state", "records.json")
    recs = json.loads(read_text(f))
    recs[1]["abstract"] = "A different abstract, pasted in later."
    write_json(f, recs)
    (pr,) = problems(c)                                                     # with the one already accepted: a new state of the file
    assert pr["kind"] == "records" and pr["sig"] != c.accepted
    assert pr["text"].startswith("The text of 2 records differs from what was screened (") and recs[1]["id"] in pr["text"] \
        and c.changed in pr["text"]
    c.post("/api/restore", {"file": "state/records.json"})
    assert problems(c) == []
    f = os.path.join(c.folder, "reviewdesk-project.json")
    s = json.loads(read_text(f))
    s["criteria"]["include"] = list(s["criteria"]["include"]) + ["and anything else"]
    write_json(f, s)
    (pr,) = problems(c)
    assert pr["kind"] == "criteria" and "eligibility criteria were changed outside ReviewDesk" in pr["text"]


def test_a_record_sealed_by_an_earlier_version_keeps_its_fingerprint_and_new_ones_are_whole(earlier):
    c = earlier
    before = seal_of(c.folder)
    c.add_import(ris(*tiny_records(3, seed=77)), "update.ris", "MEDLINE", set_name="update")
    c.process()
    c.post("/api/screening/start", {"set": "update"})
    seal = seal_of(c.folder)
    assert {k: v for k, v in seal["records"].items() if k in before["records"]} == before["records"]
    new = [v for k, v in seal["records"].items() if k not in before["records"]]
    assert len(new) == 3 and {len(v) for v in new} == {FULL}
    assert seal["criteria"] == before["criteria"] and seal["question"] == before["question"]
    assert problems(c) == [] and c.nh.do(c.nh.p.integrity_report)["problems"] == []


def test_the_risk_of_bias_plan_changed_outside_reviewdesk_is_still_noticed(earlier):
    c = earlier
    f = os.path.join(c.folder, "reviewdesk-project.json")
    s = json.loads(read_text(f))
    s["risk_of_bias_plan"]["designs"][0]["name"] += " (changed by hand)"
    s["risk_of_bias_plan"]["saved"] = "2026-01-01T00:00:00"
    write_json(f, s)
    kinds = {pr["kind"] for pr in problems(c)}
    assert "robplan" in kinds, kinds


def test_this_computer_puts_back_its_own_file_with_the_rows_as_they_were(earlier):
    c = earlier
    f = os.path.join(c.folder, "decisions", f"NH@{c.nh.dev}.jsonl")
    whole_file = read_text(f)
    rows = rows_of(f)
    write_rows(f, rows[:-3])                                                # the sync service put an older copy back
    c.nh.do(c.nh.p._heal_own_files)
    assert read_text(f) == whole_file                                       # row for row, with the fingerprints they had
    c.nh.do(c.nh.p.add_decision, "NH", c.ids[4], "ta", "include")
    stray = {"t": "2026-11-11T12:00:00", "r": c.ids[5], "s": "ta", "d": "include"}
    stray["h"], stray["k"] = integrity.row_hash(rows[3]["h"], stray)[:OLD_ROW], "0" * OLD_PRINT
    now = rows_of(f)
    write_rows(f, now[:4] + [stray] + now[4:])                              # a row this computer did not write, in the middle
    c.nh.do(c.nh.p._heal_own_files)
    healed = rows_of(f)
    assert [d for d in healed if "repair" not in d] == now and integrity.verify(healed) == []     # nothing genuine lost or changed
    assert c.nh.do(lambda: integrity.unsigned(healed)) == []


def test_a_genuine_row_that_has_to_take_a_new_place_is_given_a_whole_fingerprint_and_the_rest_are_left(earlier):
    """Two runs of ReviewDesk on NH's computer each added to the file from the same row (one of them from a copy of the
    settings folder that was behind). Both sets of rows are NH's computer's own: none is dropped, the rows that still
    follow keep the fingerprints the earlier version gave them, and those that now follow other rows get new ones."""
    c = earlier
    f = os.path.join(c.folder, "decisions", f"NH@{c.nh.dev}.jsonl")
    rows = rows_of(f)
    with earlier_version():                                                 # (both were written by the earlier version)
        def two_more():
            out, prev = [], rows[-3]["h"]
            for k, rid in enumerate(c.ids[:2]):
                d = {"t": f"2027-01-0{k + 1}T09:00:00", "r": rid, "s": "ta", "d": "maybe", "note": "from the other run"}
                d["h"] = integrity.row_hash(prev, d)
                d["k"] = integrity.sign(d)
                out.append(d)
                prev = d["h"]
            return out
        other = c.nh.do(two_more)
    assert [(len(d["h"]), len(d["k"])) for d in other] == [(OLD_ROW, OLD_PRINT)] * 2
    write_rows(f, rows[:-2] + other)                                        # the shared file has the other run's rows,
    c.nh.do(c.nh.p._heal_own_files)                                         # this computer's copy the last two of its own
    healed = rows_of(f)
    kept, moved, repair = healed[:len(rows)], healed[len(rows):-1], healed[-1]
    assert kept == rows[:-2] + other                                        # as they were, old fingerprints and all
    strip = lambda d: {k: v for k, v in d.items() if k not in ("h", "k")}   # noqa: E731
    assert [strip(d) for d in moved] == [strip(d) for d in rows[-2:]]       # this computer's two rows, in their new place,
    assert [(len(d["h"]), len(d["k"])) for d in moved] == [(FULL, FULL)] * 2    # fingerprinted and signed by this version
    assert repair["repair"]["moved"] == {rows[-2]["h"]: moved[0]["h"], rows[-1]["h"]: moved[1]["h"]} and repair["repair"]["dropped"] == []
    assert integrity.verify(healed) == [] and c.nh.do(lambda: integrity.unsigned(healed)) == []
    assert problems(c) == [] and c.nh.do(c.nh.p.integrity_report)["problems"] == []


def test_the_lead_puts_a_file_back_from_this_computers_copy_and_keeps_the_rows_that_follow(earlier):
    c = earlier
    assert problems(c) == []                                                # (the lead's computer has its copy of NH's file)
    c.nh.do(c.nh.p.add_decision, "NH", c.ids[4], "ta", "include", note="after the lead's copy was made")
    f = os.path.join(c.folder, "decisions", f"NH@{c.nh.dev}.jsonl")
    rows = rows_of(f)
    edited = [dict(d) for d in rows]
    edited[2]["d"] = "exclude" if edited[2]["d"] == "include" else "include"
    write_rows(f, edited)
    (pr,) = problems(c)
    assert pr["kind"] == "chain" and "chain" in pr["fixes"]
    out = c.post("/api/integrity/fix", {"kind": "chain", "file": pr["file"]})
    assert out["report"]["problems"] == []
    assert rows_of(f) == rows                                               # the copy, then the whole row that followed it


@pytest.mark.parametrize("signature_too", [False, True])
def test_a_whole_row_cut_down_to_an_earlier_versions_lengths_is_noticed_and_put_back_once(earlier, signature_too):
    """The fingerprint of the old length is the start of the whole one, so a whole row can be cut down to it. Cut alone
    (the signature left whole) it is a row no version wrote. With the signature cut too it looks like an earlier
    version's row: the lead's computer, which had seen the row, misses the whole one; and the computer that wrote it puts
    its own row back, and does not take the other for a second vote."""
    c = earlier
    c.nh.do(c.nh.p.add_decision, "NH", c.ids[4], "ta", "include", note="written by this version")
    assert problems(c) == []                                                # (the lead's computer has now seen the row)
    f = os.path.join(c.folder, "decisions", f"NH@{c.nh.dev}.jsonl")
    rows = rows_of(f)
    last = rows[-1]
    assert (len(last["h"]), len(last["k"])) == (FULL, FULL)
    cut = {**last, "h": last["h"][:OLD_ROW], **({"k": last["k"][:OLD_PRINT]} if signature_too else {})}
    write_rows(f, rows[:-1] + [cut])
    (pr,) = problems(c)
    assert pr["kind"] == "chain" and pr["file"].endswith(f"NH@{c.nh.dev}.jsonl")
    c.nh.do(c.nh.p._heal_own_files)
    healed = rows_of(f)
    assert healed[:-1] == rows and healed[-1]["repair"] == {"dropped": [cut["h"]], "moved": {}, "by_computer": c.nh.dev}
    assert len([d for d in healed if d.get("note") == "written by this version"]) == 1       # one vote, as NH cast it
    assert integrity.verify(healed) == [] and c.nh.do(lambda: integrity.unsigned(healed)) == []
    assert problems(c) == [] and c.nh.do(c.nh.p.integrity_report)["problems"] == []


@pytest.mark.parametrize("kept", [[], {}, 5, None, ["a"], {"a": 1}])
def test_a_fingerprint_that_is_not_text_is_reported_and_stops_nothing(earlier, kept):
    c = earlier
    f = os.path.join(c.folder, "decisions", f"NH@{c.nh.dev}.jsonl")
    rows = rows_of(f)
    damaged = [dict(d) for d in rows]
    damaged[2]["h"] = kept
    write_rows(f, damaged)
    (pr,) = problems(c)
    assert pr["kind"] == "chain" and pr["file"].endswith(f"NH@{c.nh.dev}.jsonl")
    assert c.summary()["data_problems"] == 1                                # the project still opens, and says so
    fresh(c)                                                                # (and opens again after a restart)
    assert c.summary()["data_problems"] == 1
    out = c.post("/api/integrity/fix", {"kind": "chain", "file": pr["file"]})    # the lead can put the file back,
    assert out["report"]["problems"] == [] and rows_of(f) == rows
    write_rows(f, damaged)
    c.nh.do(c.nh.p._heal_own_files)                                         # and so does the computer that wrote it
    healed = rows_of(f)
    assert healed[:-1] == rows and healed[-1]["repair"]["dropped"] == [""] and healed[-1]["repair"]["moved"] == {}
    assert problems(c) == [] and c.nh.do(c.nh.p.integrity_report)["problems"] == []
    damaged = rows_of(f)
    damaged[-1]["repair"] = {"dropped": kept, "moved": kept}                # (a note of a repair, damaged the same way)
    write_rows(f, damaged)
    assert [pr["kind"] for pr in problems(c)] == ["chain"]


# ------------------------------------------------------------------------------------------------ a project begun with this version
def test_a_project_begun_with_this_version_accepts_nothing_of_the_old_length(desk, tmp_path):
    c = build(desk, tmp_path)
    f = os.path.join(c.folder, "decisions", f"NH@{c.nh.dev}.jsonl")
    whole_file, rows = read_text(f), rows_of(f)
    old, prev = [], ""
    for d in rows:                                                          # the same rows, as an earlier version would
        d = {k: v for k, v in d.items() if k not in ("h", "k")}             # have fingerprinted them: each one correct
        d["h"] = integrity.row_hash(prev, d)[:OLD_ROW]                      # by the old rule
        d["k"] = "0" * OLD_PRINT
        old.append(d)
        prev = d["h"]
    assert integrity.verify(old) == []                                      # (the rule for an earlier version's project)
    write_rows(f, old)
    (pr,) = problems(c)
    assert pr["kind"] == "chain" and f"{len(old)} rows changed or added outside ReviewDesk" in pr["text"]
    with open(f, "w", encoding="utf-8", newline="") as fh:
        fh.write(whole_file)
    assert problems(c) == []
    signed_old = [{**d, "k": d["k"][:OLD_PRINT]} for d in rows]             # whole fingerprints, signatures cut short
    write_rows(f, signed_old)
    (pr,) = problems(c)
    assert pr["kind"] == "chain"
    with open(f, "w", encoding="utf-8", newline="") as fh:
        fh.write(whole_file)
    seal_f = os.path.join(c.folder, "state", "seal.json")
    seal = seal_of(c.folder)
    rid = sorted(seal["records"])[0]
    seal["records"][rid] = seal["records"][rid][:OLD_PRINT]                  # the start of the right fingerprint
    write_json(seal_f, seal)
    assert problems(c) != []


@pytest.mark.parametrize("made", ["1.3.0", "1.2", "", None, 5, "nonsense"])
def test_the_project_file_alone_cannot_make_it_a_project_of_an_earlier_version(desk, tmp_path, made):
    """Which version began a project is said twice: in the project file, and by the audit log's first entry. Changing
    the first by hand does not bring the old lengths back."""
    c = build(desk, tmp_path)
    p = server.APP.project
    pf = os.path.join(c.folder, "reviewdesk-project.json")
    s = json.loads(read_text(pf))
    if made is None:
        del s["created_with"]
    else:
        s["created_with"] = made
    write_json(pf, s)
    fresh(c)
    assert not p._old_fingerprints() and not c.nh.p._old_fingerprints()
    f = os.path.join(c.folder, "decisions", f"NH@{c.nh.dev}.jsonl")
    old, prev = [], ""
    for d in rows_of(f):
        d = {k: v for k, v in d.items() if k not in ("h", "k")}
        d["h"] = integrity.row_hash(prev, d)[:OLD_ROW]
        old.append(d)
        prev = d["h"]
    write_rows(f, old)
    assert "chain" in [pr["kind"] for pr in problems(c)]


def test_a_project_from_before_the_project_file_said_which_version_began_it_keeps_the_old_lengths(earlier):
    c = earlier
    pf = os.path.join(c.folder, "reviewdesk-project.json")
    s = json.loads(read_text(pf))
    del s["created_with"]                                                   # (as versions before 1.3.0 left it)
    write_json(pf, s)
    fresh(c)
    assert server.APP.project._old_fingerprints() and c.nh.p._old_fingerprints()
    assert [pr["kind"] for pr in problems(c)] == []


# ------------------------------------------------------------------------------------------------ versions
def test_the_leads_first_opening_with_this_version_says_in_the_project_file_what_the_project_needs(earlier):
    c = earlier
    pf = os.path.join(c.folder, "reviewdesk-project.json")
    before = json.loads(read_text(pf))
    assert before["min_version"] == "1.3.0"
    c.be("NH")                                                              # a reviewer opening it first changes nothing:
    assert c.summary()["settings"]["min_version"] == "1.3.0" and json.loads(read_text(pf)) == before   # the file is the lead's
    c.be("CH")
    assert c.summary()["settings"]["min_version"] == "1.3.1"
    after = json.loads(read_text(pf))
    assert {k for k in set(before) | set(after) if before.get(k) != after.get(k)} == {"min_version"}   # nothing else: not
    assert after["min_version"] == "1.3.1" and list(after) == list(before)                             # even when it was saved
    assert after["created_with"] == "1.3.0" and server.APP.project._old_fingerprints()
    written = read_text(pf)
    assert c.summary()["data_problems"] == 0 and problems(c) == [] and read_text(pf) == written    # once, and nothing is reported
    with earlier_version():                                                 # 1.3.0 no longer opens the project at all,
        with pytest.raises(ProjectError, match="This project needs ReviewDesk 1.3.1 or later, and this is ReviewDesk 1.3.0"):
            c.nh.reopen()                                                   # so it cannot work on its files either


def test_the_leads_opening_does_not_make_an_older_copy_of_the_project_file_look_new(earlier):
    """Saying what the project needs is not a save. A project file that the sync service has put back from an older
    copy is still reported as older, on every computer that had seen the newer one, after the lead has opened the
    project: the lead must see it, and choose which copy is right."""
    c = earlier
    pf = os.path.join(c.folder, "reviewdesk-project.json")
    older = read_text(pf)                                                   # as 1.3.0 left it
    c.settings(short_title="Triage")                                        # the lead saves it with this version,
    assert problems(c) == [] and c.nh.do(c.nh.p.integrity_report)["problems"] == []   # and both computers have seen that
    with open(pf, "w", encoding="utf-8", newline="") as fh:
        fh.write(older)                                                     # the sync service puts the older copy back
    fresh(c)
    assert [pr["kind"] for pr in problems(c)] == ["older"]
    assert c.summary()["settings"]["min_version"] == "1.3.1"                # the lead opens the project
    was, now = json.loads(older), json.loads(read_text(pf))
    assert {k for k in set(was) | set(now) if was.get(k) != now.get(k)} == {"min_version"}
    assert c.summary()["data_problems"] == 1 and [pr["kind"] for pr in problems(c)] == ["older"]
    assert [pr["kind"] for pr in c.nh.do(c.nh.p.integrity_report)["problems"]] == ["older"]
    c.post("/api/restore", {"file": "reviewdesk-project.json"})             # the lead puts the newer copy back
    assert problems(c) == [] and c.nh.do(c.nh.p.integrity_report)["problems"] == []
    assert json.loads(read_text(pf))["short_title"] == "Triage" and json.loads(read_text(pf))["min_version"] == "1.3.1"


def test_a_version_told_to_stop_saving_leaves_its_own_files_as_they_are(earlier):
    """1.3.0 repaired its own files in its own way even when a newer version had worked on the project (on the same
    computer, when someone went back to it), which gave the newer version's rows fingerprints of 1.3.0's length. A
    version that must not save does not repair either: the newer version does."""
    c = earlier
    f = os.path.join(c.folder, "decisions", f"NH@{c.nh.dev}.jsonl")
    rows = rows_of(f)
    write_rows(f, rows[:-2])                                                # an older copy put back
    newer_entry = {"t": "2027-02-01T09:00:00.000+00:00", "who": "CH", "action": "open the project", "version": "9.0.0", "needs": "9.0.0"}
    with open(os.path.join(c.folder, "audit", "CH@elsewhere.jsonl"), "w", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(newer_entry) + "\n")
    fresh(c)
    assert c.nh.do(lambda: versions.must_upgrade(c.nh.p, "NH")).startswith("This project now needs ReviewDesk 9.0.0 or later")
    c.nh.do(c.nh.p._heal_own_files)
    assert rows_of(f) == rows[:-2]                                          # left as it is
    pf = os.path.join(c.folder, "reviewdesk-project.json")                  # (and with the project file damaged as well, it is
    good = read_text(pf)                                                    # still told to stop, not to put that file back)
    with open(pf, "w", encoding="utf-8", newline="") as fh:
        fh.write('{"title": "cut sho')
    c.nh.p._cache.clear(); c.nh.p._memo.clear()
    assert c.nh.do(lambda: versions.must_upgrade(c.nh.p, "NH")).startswith("This project now needs ReviewDesk 9.0.0 or later")
    assert c.nh.do(lambda: versions.behind(c.nh.p))
    with open(pf, "w", encoding="utf-8", newline="") as fh:
        fh.write(good)
    os.remove(os.path.join(c.folder, "audit", "CH@elsewhere.jsonl"))
    fresh(c)
    c.nh.do(c.nh.p._heal_own_files)
    assert rows_of(f) == rows                                               # (and repaired as ever when nothing says stop)


def test_an_earlier_version_cannot_check_what_this_one_writes_so_the_project_needs_this_one(earlier):
    c = earlier
    p = server.APP.project
    assert versions.NEEDS == integrity.FULL_SINCE == "1.3.1" and not versions.newer(versions.NEEDS, VERSION)
    c.get("/api/app")                                                       # the lead opens the project with this version
    assert versions.needed(p)["version"] == "1.3.1" and versions.must_upgrade(p, "NH") == ""
    with earlier_version():                                                 # NH's computer has 1.3.0, with the project open
        stop = c.nh.do(lambda: versions.must_upgrade(c.nh.p, "NH"))
        assert stop.startswith("This project now needs ReviewDesk 1.3.1 or later, and this is ReviewDesk 1.3.0. ")
        assert "this version will not save any work in this project" in stop
    (w,) = [x for x in versions.warnings(p, "CH") if x.startswith("Nadine Hayes last worked")]
    assert "with ReviewDesk 1.3.0" in w and "Everyone in the team must use ReviewDesk 1.3.1 or later" in w
    assert "Their version stops saving in this project once their computer has the project's files as they now are" in w
    assert "must not be used in it before then" in w
    assert json.loads(read_text(os.path.join(c.folder, "reviewdesk-project.json")))["min_version"] == "1.3.1"
    with earlier_version():                                                 # and started again, 1.3.0 does not open it at all
        with pytest.raises(ProjectError, match="This project needs ReviewDesk 1.3.1 or later, and this is ReviewDesk 1.3.0"):
            c.nh.reopen()


def test_a_reviewer_who_moves_first_stops_the_earlier_version_saving_and_is_named(earlier):
    c = earlier
    with earlier_version():
        assert c.nh.do(lambda: versions.must_upgrade(c.nh.p, "CH")) == ""   # (nobody has used the new version yet)
    c.nh.do(versions.note_open, c.nh.p, "NH")                               # NH opens the project with this version
    fresh(c)
    with earlier_version():                                                 # the lead's ReviewDesk is still 1.3.0
        stop = versions.must_upgrade(server.APP.project, "CH")
        assert stop.startswith("This project now needs ReviewDesk 1.3.1 or later, and this is ReviewDesk 1.3.0. Nadine Hayes has worked on")
        assert json.loads(read_text(os.path.join(c.folder, "reviewdesk-project.json")))["min_version"] == "1.3.0"
