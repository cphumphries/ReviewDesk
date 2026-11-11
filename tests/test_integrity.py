"""The data checks: a row edited, deleted or added outside ReviewDesk in a file of decisions, extraction answers or
risk-of-bias judgements is reported as a data problem (naming the record), and the repairs work: the lead puts the file
back from the copy their computer kept, and each person's own computer repairs its own files. Changes to the screened
records, the criteria, the seal, the exports and the PDFs are reported too, and can be put back or recorded as correct.

NH's rows are written on NH's own computer (its own settings folder, so its own device ID and secret); the lead's
ReviewDesk runs here. Both use the same project folder."""
from __future__ import annotations

import json
import os

import pytest

from helpers import Desk, read_bytes, read_text, reference_plan, reference_project, ris, tiny_records, write_bytes, write_json, write_text
from realistic import make_pdf
from reviewdesk import review, server
from reviewdesk.project import PermissionDenied


@pytest.fixture
def two(desk, tmp_path):
    """Six records screened by both (NH on NH's computer), four at full text, all four included, the extraction form
    set; NH has extracted two answers and judged one domain of risk of bias."""
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
    nh.do(review.save_value, nh.p, "NH", ids[0], "n", "812")
    aid = c.post("/api/rob/define", {"key": ids[0], "design": "rct", "label": "Time to triage"})["rob"][0]["a"]
    nh.do(review.judge, nh.p, "NH", ids[0], aid, "D1", "Low risk", "Central randomisation")
    rep = c.get("/api/integrity")["report"]              # the lead's computer has now seen every file intact
    assert rep["problems"] == [], rep["problems"]
    c.folder, c.nh, c.ids, c.aid = folder, nh, ids, aid
    return c


def rows_of(path):
    return [json.loads(x) for x in read_text(path).splitlines() if x.strip()]


def write_rows(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def nh_file(c, sub="decisions"):
    return os.path.join(c.folder, sub, f"NH@{c.nh.dev}.jsonl")


def problems(c):
    return c.get("/api/integrity")["report"]["problems"]


@pytest.mark.parametrize("sub, who_words", [("decisions", "Nadine Hayes's screening decisions"),
                                            ("extraction", "Nadine Hayes's extraction answers"),
                                            ("rob", "Nadine Hayes's risk-of-bias judgements")])
def test_a_row_edited_outside_reviewdesk_is_reported_and_the_lead_puts_the_file_back(two, sub, who_words):
    c = two
    f = nh_file(c, sub)
    good = read_text(f)
    rows = rows_of(f)
    k = next(i for i, r in enumerate(rows) if r.get("d") == "exclude" or "v" in r or r.get("j"))
    rows[k] = {**rows[k], **({"d": "include"} if sub == "decisions" else {"v": "Australia"} if sub == "extraction" else {"j": "High risk"})}
    write_rows(f, rows)
    (p,) = problems(c)
    rel = f"{sub}/NH@{c.nh.dev}.jsonl"
    assert p["kind"] == "chain" and p["file"] == rel and p["own"] is False and p["fixes"] == ["chain"]
    assert p["text"].startswith(f"{who_words} ({rel}): 1 row changed or added outside ReviewDesk")
    assert (rows[k].get("r") or rows[k].get("s")) in p["text"]
    assert c.summary()["data_problems"] == 1
    out = c.post("/api/integrity/fix", {"kind": "chain", "file": rel})
    assert out["result"].startswith(f"Put back {len(rows)} row{'s' if len(rows) != 1 else ''} from this computer's copy.")
    assert out["report"]["problems"] == [] and read_text(f) == good
    assert any(n.startswith(rel.replace("/", "__")) for n in os.listdir(os.path.join(c.folder, "state", "_replaced")))
    assert any(e["action"] == "put back a file from this computer's copy" for e in c.get("/api/audit")["entries"])


def test_a_deleted_row_is_reported_as_an_older_copy_put_back(two):
    c = two
    f = nh_file(c)
    rows = rows_of(f)
    write_rows(f, rows[:-1])
    (p,) = problems(c)
    assert "1 row this computer had seen is no longer there" in p["text"] and rows[-1]["r"] in p["text"]
    assert "so an older copy was probably put back" in p["text"]


def test_a_row_added_by_hand_is_reported(two):
    c = two
    f = nh_file(c)
    rows = rows_of(f)
    forged = {**rows[-1], "r": c.ids[-1], "d": "include", "t": rows[-1]["t"]}
    write_rows(f, rows + [forged])
    (p,) = problems(c)
    assert "1 row changed or added outside ReviewDesk" in p["text"] and c.ids[-1] in p["text"]


def test_the_reviewers_own_computer_repairs_its_own_file(two):
    c = two
    f = nh_file(c)
    rows = rows_of(f)
    edited = [dict(r) for r in rows]
    edited[0]["d"] = "maybe"
    write_rows(f, edited[:3] + edited[4:])                  # one row changed and one lost
    nh = c.nh
    p = nh.reopen()
    rep = nh.do(p.integrity_report)
    assert [x for x in rep["problems"] if x["file"].startswith("decisions/NH@")] == []
    assert any("written back" in x and "removed" in x for x in p.repairs())
    assert [r["d"] for r in rows_of(f) if "repair" not in r] == [r["d"] for r in rows]
    assert problems(c) == []                                 # and the lead's computer agrees


def test_a_row_with_a_valid_fingerprint_but_not_signed_by_its_computer_is_removed_there(two):
    from reviewdesk import integrity
    c = two
    f = nh_file(c)
    rows = rows_of(f)
    forged = {"t": rows[-1]["t"], "r": c.ids[-1], "s": "ta", "d": "include"}
    forged[integrity.H] = integrity.row_hash(rows[-1][integrity.H], forged)      # someone who knows how fingerprints are made
    write_rows(f, rows + [forged])
    nh = c.nh
    p = nh.reopen()
    nh.do(p.integrity_report)
    assert [r for r in rows_of(f) if r.get("h") == forged["h"]] == []
    kept = os.listdir(os.path.join(c.folder, "state", "_replaced"))
    assert any(n.startswith("decisions__NH@") for n in kept)


def test_the_text_of_a_screened_record_changed_outside_reviewdesk(two):
    c = two
    f = os.path.join(c.folder, "state", "records.json")
    recs = json.loads(read_text(f))
    recs[1]["abstract"] = "A different abstract, pasted in later."
    write_json(f, recs)
    (p,) = problems(c)
    assert p["kind"] == "records" and p["text"] == f"The text of 1 record differs from what was screened ({recs[1]['id']})."
    assert p["fixes"] == ["backup", "accept"]
    e = c.error("POST", "/api/integrity/fix", {"kind": "accept", "sig": p["sig"], "reason": "ok"})
    assert "Say why this difference is correct" in e.message
    out = c.post("/api/integrity/fix", {"kind": "accept", "sig": p["sig"], "reason": "The publisher corrected the abstract."})
    assert out["report"]["problems"] == [] and out["report"]["accepted"][-1]["reason"] == "The publisher corrected the abstract."
    recs[1]["title"] = "Changed again"
    write_json(f, recs)
    assert len(problems(c)) == 1                             # a further change is reported again
    c.post("/api/restore", {"file": "state/records.json"})
    assert problems(c) == []


def test_criteria_changed_outside_reviewdesk_are_recorded_as_an_amendment(two):
    c = two
    f = os.path.join(c.folder, "reviewdesk-project.json")
    s = json.loads(read_text(f))
    s["criteria"]["include"].append("Adults only")
    write_json(f, s)
    (p,) = problems(c)
    assert p["kind"] == "criteria" and "The eligibility criteria were changed outside ReviewDesk" in p["text"]
    c.post("/api/integrity/fix", {"kind": "accept", "sig": p["sig"], "reason": "Agreed at the protocol meeting on 3 March."})
    assert problems(c) == []
    am = c.summary()["settings"]["amendments"][-1]
    assert am["what"] == "criteria" and am["outside_reviewdesk"] is True and "Adults only" in am["after"]["include"]
    assert "Changes after screening started" in c.get("/api/prisma")["summary"]


def test_a_missing_seal_is_put_back_from_this_computers_copy(two):
    c = two
    os.remove(os.path.join(c.folder, "state", "seal.json"))
    (p,) = problems(c)
    assert p["sig"] == "seal-missing" and p["fixes"][0] == "seal"
    out = c.post("/api/integrity/fix", {"kind": "seal"})
    assert out["report"]["problems"] == [] and out["report"]["records_sealed"] == 6


def test_a_seal_changed_outside_reviewdesk_is_reported(two):
    c = two
    f = os.path.join(c.folder, "state", "seal.json")
    seal = json.loads(read_text(f))
    first = sorted(seal["records"])[0]
    seal["records"][first] = "0" * 16
    write_json(f, seal)
    texts = [p["text"] for p in problems(c)]
    assert any("was changed outside ReviewDesk: it no longer matches this computer's copy of it" in t for t in texts)
    c.post("/api/integrity/fix", {"kind": "seal"})
    assert problems(c) == []


def test_an_export_changed_after_it_was_added(two):
    c = two
    imp = os.path.join(c.folder, "imports")
    f = next(os.path.join(imp, n) for n in os.listdir(imp) if n.endswith(".ris"))
    write_bytes(f, b"TY  - JOUR\nTI  - Added later by hand\nER  - \n", append=True)
    (p,) = problems(c)
    assert p["kind"] == "import" and "was changed after it was added" in p["text"] and p["fixes"] == ["accept"]
    e = c.error("POST", "/api/imports/process", {})
    assert "has changed since it was added" in e.message


def test_pdfs_replaced_added_or_left_in_the_folder_outside_reviewdesk(two):
    c = two
    a, b, d = c.ids[:3]
    good = make_pdf("A paper", "10.1136/emj-1", pages=8)
    c.upload("/api/fulltext/upload", good, record=a)
    assert problems(c) == []
    ft = os.path.join(c.folder, "fulltexts")
    write_bytes(os.path.join(ft, f"{a}.pdf"), make_pdf("Another paper", pages=8))
    (p,) = problems(c)
    assert p["text"] == f"The PDF for {a} was replaced outside ReviewDesk." and p["fixes"] == ["pdf", "keep"]
    c.post("/api/integrity/fix", {"kind": "pdf", "record": a})
    assert read_bytes(os.path.join(ft, f"{a}.pdf")) == good and problems(c) == []
    write_bytes(os.path.join(ft, f"{b}.pdf"), make_pdf("Put here by hand", pages=8))
    write_bytes(os.path.join(ft, "paper from the library.pdf"), make_pdf("From the library", pages=8))
    kinds = {p["kind"]: p for p in problems(c)}
    assert set(kinds) == {"pdf_unrecorded", "pdf_stray"} and kinds["pdf_unrecorded"]["records"] == [b]
    c.post("/api/integrity/fix", {"kind": "adopt"})
    c.post("/api/integrity/fix", {"kind": "stray"})
    assert problems(c) == []
    assert [w["name"] for w in c.get("/api/fulltext/waiting")["waiting"]] == ["paper from the library.pdf"]
    os.remove(os.path.join(ft, f"{a}.pdf"))
    (p,) = problems(c)
    assert p["text"] == f"The PDF for {a} is missing from the project folder."
    c.post("/api/integrity/fix", {"kind": "pdf", "record": a})
    assert problems(c) == []


@pytest.mark.parametrize("rel", ["state/state.json", "reviewdesk-project.json"])
@pytest.mark.parametrize("held", ["[1, 2]", '"text"', "0", '{"cut": "sho'])
def test_a_shared_file_that_holds_something_else_than_reviewdesk_writes_there_is_a_damaged_file(two, rel, held):
    """It can be read, but it is not the settings or the state: no page can work with it (every page said "Something
    went wrong"), so it is reported as the damaged file it is, with the copy to put back. And the copy can be put
    back, whichever file it is and however it is damaged: for the project file 1.3.0 answered that request with the
    same message again, because it asked the project file what the project needs before every change."""
    c = two
    c.settings(short_title="Triage")                                  # (so that this computer has a copy of each to offer)
    f = os.path.join(c.folder, *rel.split("/"))
    write_text(f, held)
    for path in ("/api/overview", "/api/app"):
        st, _, body = c.request("GET", path)
        out = json.loads(body)
        assert st == 409 and f"The project file {rel} cannot be read" in out["error"], (path, st, body[:200])
        assert out["damaged"]["file"] == rel and out["damaged"]["can_restore"] is True
    st, _, body = c.request("GET", "/api/integrity")
    if rel == "state/state.json":                                     # the data checks list it, as when it is cut short;
        assert st == 200 and [x["kind"] for x in json.loads(body)["report"]["problems"]] == ["damaged"], body[:300]
    else:                                                             # without the project file they cannot run, as then
        assert st == 409 and json.loads(body)["damaged"]["file"] == rel, body[:300]
    assert read_text(f) == held                                       # (nothing was written over it meanwhile)
    assert c.post("/api/restore", {"file": rel})["restored"] == rel
    assert c.get("/api/integrity")["report"]["problems"] == [] and c.summary()["settings"]["short_title"] == "Triage"


def test_a_damaged_project_file_is_put_back_only_by_the_lead_its_newest_copy_here_names(two):
    """A damaged project file cannot say who the lead is, so the newest copy of it on the computer does. A reviewer is
    not offered the copies and cannot put one back: not at the lead's computer, and not on a computer that holds copies
    of the project file because its settings folder was copied from the lead's. Nor can someone who has handed the
    project on."""
    c = two
    rel, cut = "reviewdesk-project.json", '{"title": "cut sho'
    f = os.path.join(c.folder, rel)
    c.settings(short_title="Triage")
    good = read_text(f)
    c.be("NH")                                                        # a reviewer, at the lead's computer
    write_text(f, cut)
    st, _, body = c.request("GET", "/api/overview")
    assert st == 409 and json.loads(body)["damaged"] == {"file": rel, "can_restore": False, "copies": [], "lead": "Dr Colin Hawthorne"}
    e = c.error("POST", "/api/restore", {"file": rel})
    assert e.status == 403 and "Only the project lead can put the project file back" in e.message
    assert "names Dr Colin Hawthorne as the lead" in e.message and read_text(f) == cut
    src = server.APP.project._local("backups")                        # a reviewer's own computer, with the lead's copies
    dst = c.nh.do(lambda: c.nh.p._local("backups"))
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(src):
        if name.startswith(rel + "."):
            write_bytes(os.path.join(dst, name), read_bytes(os.path.join(src, name)))
    with pytest.raises(PermissionDenied, match="Only the project lead can put the project file back"):
        c.nh.do(c.nh.p.restore_backup, "NH", rel)
    assert read_text(f) == cut
    write_text(f, good)
    c.be("CH")                                                        # the lead: offered the copies, and puts one back
    write_text(f, cut)
    newest = sorted(n for n in os.listdir(src) if n.startswith(rel + "."))[-1]
    write_text(os.path.join(src, newest[:-6] + "999999"), "[1]")      # (a newer "copy" that is no project file at all, and
    write_text(os.path.join(src, newest[:-6] + "999998"), '{"cut')    # one cut short, are passed over: not offered, not used)
    st, _, body = c.request("GET", "/api/overview")
    out = json.loads(body)["damaged"]
    assert st == 409 and out["can_restore"] is True and [x["file"] for x in out["copies"]][0] == newest and "lead" not in out
    assert c.post("/api/restore", {"file": rel}) == {"restored": rel, "copy": newest}
    assert c.summary()["settings"]["short_title"] == "Triage"
    for junk in ("999999", "999998"):
        os.remove(os.path.join(src, newest[:-6] + junk))
    people = [dict(r, role="lead" if r["id"] == "NH" else "reviewer") for r in c.summary()["settings"]["reviewers"]]
    c.settings(reviewers=people)                                      # the lead hands the project on to NH, from here:
    write_text(f, cut)                                                # this computer's newest copy now names NH
    e = c.error("POST", "/api/restore", {"file": rel})
    assert e.status == 403 and "names Nadine Hayes as the lead" in e.message and read_text(f) == cut
    st, _, body = c.request("GET", "/api/overview")
    assert st == 409 and json.loads(body)["damaged"]["can_restore"] is False and json.loads(body)["damaged"]["lead"] == "Nadine Hayes"


def test_a_damaged_shared_file_is_reported_with_the_copy_to_put_back(two):
    c = two
    f = os.path.join(c.folder, "state", "state.json")
    write_text(f, '{"rev": 3, "sets": {"main": {"pha')               # cut short by a sync service
    st, _, body = c.request("GET", "/api/overview")
    out = json.loads(body)
    assert st == 409 and "The project file state/state.json cannot be read" in out["error"]
    assert out["damaged"]["file"] == "state/state.json" and out["damaged"]["can_restore"] is True
    r = c.post("/api/restore", {"file": "state/state.json"})
    assert r["restored"] == "state/state.json"
    assert c.overview()["sets"]["main"]["phase"] == "screening"
