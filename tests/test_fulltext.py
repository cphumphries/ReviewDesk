"""Full-text review: adding PDFs (on a report, in bulk with record IDs in the file names, and the list of PDFs waiting
to be placed), the same PDF on two reports, reports not retrieved or awaiting classification, reasons required for
every exclusion, assessment by both reviewers, and settling their disagreements."""
from __future__ import annotations

import json
import os

import pytest

from helpers import ApiError, read_csv, reference_plan, reference_project, ris, screen_all, tiny_records
from realistic import make_pdf
from reviewdesk.parsers import parse_bytes

REASONS = ["Wrong population", "Wrong intervention", "Wrong comparator", "Wrong study design", "Wrong outcomes",
           "Wrong publication type", "Not in English"]


@pytest.fixture
def ft(desk, tmp_path):
    """Ten records screened by both; the first six (in screening order) sent on to full text."""
    c = desk.client()
    c.folder = reference_project(c, str(tmp_path / "OneDrive"),
                                 screening=reference_plan(mode="dual", calibration_size=0, ai_second_screener=False))
    recs = tiny_records(10, seed=31)
    c.add_import(ris(*recs), "medline.ris", "MEDLINE")
    c.process()
    c.post("/api/screening/start", {"set": "main"})
    order = [x["id"] for x in c.queue("screen")]
    for who in ("NH", "CH"):
        screen_all(c, who, lambda r: "include" if r in order[:6] else "exclude", "screen")
    c.be("CH")
    c.rec = {r["id"]: r for r in c.get("/api/records")["records"]}
    c.ids = sorted(order[:6])
    return c


def pdf(c, rid, **kw):
    r = c.rec[rid]
    return make_pdf(r["title"], r["doi"], "Emergency Medicine Journal", r["authors"], **kw)


def rows(c) -> dict:
    return {x["id"]: x for x in c.get("/api/fulltext")["rows"]}


# ------------------------------------------------------------------------------------------------ PDFs
def test_a_pdf_added_on_a_report_is_kept_fingerprinted_and_served_back(ft):
    c = ft
    rid = c.ids[0]
    data = pdf(c, rid)
    assert c.upload("/api/fulltext/upload", data, record=rid) == {"ok": True}
    assert rows(c)[rid]["pdf"] is True and c.get("/api/fulltext")["counts"]["with_pdf"] == 1
    st, hd, body = c.request("GET", f"/files/fulltext/{rid}.pdf")
    assert st == 200 and body == data and hd["content-type"] == "application/pdf"
    idx = [json.loads(x) for f in os.listdir(os.path.join(c.folder, "fulltexts")) if f.startswith("index@")
           for x in open(os.path.join(c.folder, "fulltexts", f), encoding="utf-8")]
    import hashlib
    assert idx[-1]["r"] == rid and idx[-1]["sha"] == hashlib.sha256(data).hexdigest() and idx[-1]["by"] == "CH"
    assert any(e["action"] == "attach full text" for e in c.get("/api/audit")["entries"])


def test_only_pdfs_and_only_reports_at_full_text_are_accepted(ft):
    c = ft
    with pytest.raises(ApiError) as e:
        c.upload("/api/fulltext/upload", b"<html>Log in to read this article</html>", record=c.ids[0])
    assert "That file is not a PDF." in e.value.message
    excluded = next(r for r in c.rec if r not in c.ids)
    with pytest.raises(ApiError) as e:
        c.upload("/api/fulltext/upload", pdf(c, excluded), record=excluded)
    assert "That record is not at full-text screening." in e.value.message


def test_a_new_pdf_on_a_report_keeps_the_old_one_aside(ft):
    c = ft
    rid = c.ids[0]
    c.upload("/api/fulltext/upload", pdf(c, rid, pages=3), record=rid)
    c.upload("/api/fulltext/upload", pdf(c, rid, pages=12), record=rid)
    aside = os.listdir(os.path.join(c.folder, "fulltexts", "_replaced"))
    assert len(aside) == 1 and aside[0].startswith(rid + ".pdf.")


def test_the_same_pdf_on_two_reports_needs_confirming(ft):
    c = ft
    a, b = c.ids[:2]
    data = pdf(c, a)
    c.upload("/api/fulltext/upload", data, record=a)
    with pytest.raises(ApiError) as e:
        c.upload("/api/fulltext/upload", data, record=b)
    assert f"This PDF is already the PDF of {a}. If it really holds both reports, add it again and confirm." == e.value.message
    c.upload("/api/fulltext/upload", data, record=b, same_ok="1")          # one PDF that holds both reports
    probs = c.get("/api/integrity")["report"]["problems"]
    assert not [p for p in probs if p["kind"] == "pdf_duplicate"]


def test_pdfs_added_in_bulk_are_placed_by_the_record_id_in_their_file_names(ft):
    c = ft
    a, b = c.ids[:2]
    r1 = c.upload("/api/fulltext/bulk", pdf(c, a, pages=1), filename=f"{a}.pdf")
    r2 = c.upload("/api/fulltext/bulk", pdf(c, b), filename=f"{b} - Ngata 2024 - triage.pdf")
    assert r1["result"] == "placed" and r1["how"] == "record ID in the file name" and r1["record"] == a
    assert r2["result"] == "placed" and r2["record"] == b
    again = c.upload("/api/fulltext/bulk", pdf(c, b), filename="copy.pdf")
    assert again["result"] == "already" and again["record"] == b


def test_a_pdf_that_says_which_report_it_is_is_placed(ft):
    c = ft
    a, b = c.ids[2:4]
    meta = c.upload("/api/fulltext/bulk", pdf(c, a, meta_doi=True), filename="download (3).pdf")
    printed = c.upload("/api/fulltext/bulk", pdf(c, b), filename="fulltext.pdf")
    assert meta["result"] == "placed" and meta["record"] == a and meta["how"] == "the DOI in the PDF's own details"
    assert printed["result"] == "placed" and printed["record"] == b and "the first printed in it" in printed["how"]


def test_a_pdf_that_only_looks_like_a_report_waits_for_a_person_to_check(ft):
    c = ft
    a, b, d = c.ids[:3]
    title_only = c.upload("/api/fulltext/bulk", make_pdf(c.rec[a]["title"], "", "Emergency Medicine Journal", ""), filename="paper.pdf")
    assert title_only["result"] == "waiting" and title_only["record"] == a and "check it is the right paper" in title_only["why"]
    letter = c.upload("/api/fulltext/bulk", make_pdf(c.rec[b]["title"], c.rec[b]["doi"], "Letter to the editor", "Re: the paper",
                                                       banner="Correspondence. Re:"), filename="letter.pdf")
    assert letter["result"] == "waiting" and letter["record"] == "" and "letter" in letter["why"]
    short = c.upload("/api/fulltext/bulk", pdf(c, d, pages=2), filename="abstract.pdf")
    assert short["result"] == "waiting" and "has only 2 pages" in short["why"]
    stranger = c.upload("/api/fulltext/bulk", make_pdf("Burnout among intensive care nurses in Norway", "10.1186/s13054-022-04001-1"),
                        filename="other.pdf")
    assert stranger["result"] == "refused" and "so it was not added" in stranger["why"]
    junk = c.upload("/api/fulltext/bulk", b"PK\x03\x04 not a pdf", filename="notes.docx")
    assert junk == {"file": "notes.docx", "result": "refused", "why": "not a PDF"}
    waiting = c.get("/api/fulltext/waiting")["waiting"]
    assert sorted(w["name"] for w in waiting) == ["abstract.pdf", "letter.pdf", "paper.pdf"]
    assert next(w for w in waiting if w["name"] == "paper.pdf")["suggest_title"] == c.rec[a]["title"]
    assert all(not r["pdf"] for r in c.get("/api/fulltext/waiting")["records"])


def test_a_waiting_pdf_is_placed_or_discarded_by_a_person(ft):
    c = ft
    a, b = c.ids[:2]
    c.upload("/api/fulltext/bulk", make_pdf(c.rec[a]["title"], "", "", ""), filename="paper.pdf")
    c.upload("/api/fulltext/bulk", make_pdf(c.rec[b]["title"], "", "", ""), filename="other paper.pdf")
    st, _, body = c.request("GET", "/files/waiting/paper.pdf")
    assert st == 200 and body.startswith(b"%PDF-")
    c.be("NH")                                                     # anyone in the project may place them
    out = c.post("/api/fulltext/assign", {"name": "paper.pdf", "record": a})
    assert [w["name"] for w in out["waiting"]] == ["other paper.pdf"]
    assert rows(c)[a]["pdf"] is True
    c.upload("/api/fulltext/upload", pdf(c, b), record=b)
    e = c.error("POST", "/api/fulltext/assign", {"name": "other paper.pdf", "record": b})
    assert f"{b} already has a PDF. Choose Replace if this one is the right one." == e.message
    assert [w["name"] for w in c.get("/api/fulltext/waiting")["waiting"]] == ["other paper.pdf"]     # still waiting
    out = c.post("/api/fulltext/discard", {"name": "other paper.pdf"})
    assert out["waiting"] == []
    e = c.error("POST", "/api/fulltext/assign", {"name": "other paper.pdf", "record": b})
    assert "no longer waiting" in e.message


def test_a_pdf_can_be_taken_off_a_report(ft):
    c = ft
    rid = c.ids[0]
    c.upload("/api/fulltext/upload", pdf(c, rid), record=rid)
    c.post("/api/fulltext/remove", {"record": rid})
    assert rows(c)[rid]["pdf"] is False
    assert os.listdir(os.path.join(c.folder, "fulltexts", "_replaced"))
    e = c.error("POST", "/api/fulltext/remove", {"record": rid})
    assert "That record has no PDF." in e.message


def test_reports_still_without_a_pdf_are_listed_for_the_library(ft):
    c = ft
    c.settings(fulltext={"library_prefix": "https://login.ezproxy.ed.ac.uk/login?url="})
    c.upload("/api/fulltext/upload", pdf(c, c.ids[0]), record=c.ids[0])
    missing = c.get("/api/fulltext/missing")["missing"]
    assert sorted(m["id"] for m in missing) == c.ids[1:]
    assert missing[0]["links"]["library"].startswith("https://login.ezproxy.ed.ac.uk/login?url=https://doi.org/10.")
    st, _, body = c.download("missing_ris")
    recs, info = parse_bytes(body, "missing.ris")
    assert info["format"] == "RIS" and sorted(r["doi"] for r in recs) == sorted(c.rec[r]["doi"] for r in c.ids[1:])
    assert all(f"ReviewDesk {rid}".encode() in body for rid in c.ids[1:])
    st, _, body = c.download("missing_csv")
    table = read_csv(body)
    assert table[0][:4] == ["Record", "Authors", "Year", "Title"] and sorted(r[0] for r in table[1:]) == c.ids[1:]
    assert all(r[10] == "not searched" for r in table[1:])


def test_the_free_copy_search_needs_an_email_address_first(ft):
    c = ft
    e = c.error("POST", "/api/fulltext/fetch", {})
    assert "Add your email address under My settings first" in e.message


# ------------------------------------------------------------------------------------------------ decisions
def test_an_exclusion_needs_one_of_the_projects_reasons(ft):
    c = ft
    c.be("NH")
    rid = c.ids[0]
    for body, words in (({"decision": "exclude"}, "Choose the exclusion reason."),
                        ({"decision": "exclude", "reason": "Too boring"}, "Choose the exclusion reason."),
                        ({"decision": "maybe"}, "Choose a full-text decision.")):
        e = c.error("POST", "/api/decide", {"record": rid, "stage": "ft", "as": "NH", **body})
        assert e.message == words
    c.decide(rid, "ft", "exclude", reason="Wrong study design")
    assert c.get("/api/conflicts")["reasons"] == REASONS


def test_both_reviewers_assess_every_full_text_without_seeing_each_other(ft):
    c = ft
    c.be("NH")
    assert c.task("fulltext")["total"] == 6 and c.task("fulltext")["left"] == 6
    c.decide(c.ids[0], "ft", "include", study="Ngata 2024", note="NH-ft-note-55")
    assert rows(c)[c.ids[0]]["status"] == "waiting for others" and rows(c)[c.ids[0]]["mine"] == "include"
    c.be("CH")
    r = rows(c)[c.ids[0]]
    assert r["status"] == "to do" and r["mine"] == "" and r["final_reason"] == ""
    for path in ("/api/fulltext", "/api/conflicts", "/api/overview", "/api/prisma", "/api/agreement?scope=fulltext"):
        st, _, body = c.request("GET", path)
        assert b"NH-ft-note-55" not in body and b"Ngata 2024" not in body, path
    assert c.get("/api/agreement", scope="fulltext")["stats"] == {}
    st, _, body = c.download("decisions")
    assert st == 403 and "one reviewer has voted and the other has not yet" in json.loads(body)["error"]
    c.decide(c.ids[0], "ft", "include", study="Ngata 2024")
    assert rows(c)[c.ids[0]]["status"] == "included"


def test_full_text_disagreements_are_settled_by_the_lead(ft):
    c = ft
    a, b, d = c.ids[:3]
    c.be("NH")
    c.decide(a, "ft", "include", study="Fraser 2025")
    c.decide(b, "ft", "exclude", reason="Wrong outcomes")
    c.decide(d, "ft", "awaiting")
    c.be("CH")
    c.decide(a, "ft", "exclude", reason="Wrong population")
    c.decide(b, "ft", "exclude", reason="Wrong study design")
    c.decide(d, "ft", "include", study="Kim 2023")
    conf = {x["id"]: x for x in c.get("/api/conflicts")["conflicts"]}
    assert conf[a]["kind"] == "include or exclude" and conf[b]["kind"] == "different reasons" and conf[d]["kind"] == "different decisions"
    assert conf[a]["votes"]["NH"]["study"] == "Fraser 2025" and conf[b]["votes"]["CH"]["reason"] == "Wrong study design"
    assert "Fraser 2025" in c.get("/api/conflicts")["studies"]
    c.be("NH")
    e = c.error("POST", "/api/resolve", {"record": a, "stage": "ft", "decision": "include", "as": "NH"})
    assert e.status == 403
    c.be("CH")
    c.resolve(a, "ft", "include", study="Fraser 2025", note="Disagreement not resolved: included, as the protocol says")
    c.resolve(b, "ft", "exclude", reason="Wrong study design")
    c.resolve(d, "ft", "awaiting")
    r = rows(c)
    assert (r[a]["status"], r[b]["status"], r[b]["final_reason"], r[d]["status"]) == ("included", "excluded", "Wrong study design", "awaiting")
    assert c.get("/api/conflicts")["conflicts"] == []
    st, _, body = c.download("excluded")
    table = read_csv(body)
    row = next(x for x in table if x[0] == b)
    head = table[0]
    # ReviewDesk records the decision the lead entered on the disagreement, not how it was reached
    assert row[head.index("Reason for exclusion")] == "Wrong study design"
    assert row[head.index("How decided")] == "decision recorded by the project lead"


def test_reports_not_retrieved_and_awaiting_classification_are_their_own_categories(ft):
    c = ft
    a, b, d, e_, f, g = c.ids
    for who in ("NH", "CH"):
        c.be(who)
        c.decide(a, "ft", "not_retrieved")
        c.decide(b, "ft", "awaiting", note="Authors emailed 3 March; no reply after two weeks")
        c.decide(d, "ft", "include", study="Okafor 2024")
        c.decide(e_, "ft", "include", study="Okafor 2024")                # a second report of the same study
        c.decide(f, "ft", "exclude", reason="Not in English")
        c.decide(g, "ft", "include", study="Patel 2025")
    r = rows(c)
    assert (r[a]["status"], r[b]["status"]) == ("not_retrieved", "awaiting")
    assert c.get("/api/fulltext")["counts"]["missing"] == 4               # not retrieved and excluded reports need no PDF
    pr = c.get("/api/prisma")["counts"]
    D = pr["db"]
    assert (D["sought"], D["not_retrieved"], D["assessed"], D["ft_excluded"], D["ft_included"], D["awaiting"]) == (6, 1, 5, 1, 3, 1)
    assert (pr["included_reports"], pr["included_studies"], pr["awaiting"]) == (3, 2, 1) and pr["complete"] and not pr["checks"]
    assert D["ft_reasons"] == {"Not in English": 1}
    s = c.get("/api/prisma")["summary"]
    assert ("The full texts of 6 reports were sought; 1 could not be retrieved and 5 were assessed by Nadine Hayes and Dr Colin "
            "Hawthorne independently.") in s
    assert "In all, 3 reports of 2 studies were included; 1 report is awaiting classification." in s
    svg = c.get("/api/prisma")["svg"]
    assert "Reports awaiting classification" in svg and "Not in English (n = 1)" in svg


def test_full_text_agreement_counts_include_against_exclude_only(ft):
    c = ft
    for who, votes in (("NH", ["include", "include", "exclude", "exclude", "awaiting", "include"]),
                       ("CH", ["include", "exclude", "exclude", "exclude", "include", "include"])):
        c.be(who)
        for rid, d in zip(c.ids, votes):
            c.decide(rid, "ft", d, reason="Wrong outcomes" if d == "exclude" else "", study="S" if d == "include" else "")
    ag = c.get("/api/agreement", scope="fulltext")
    assert ag["complete"] and ag["stats"]["n"] == 5                     # the awaiting one is left out
    assert (ag["stats"]["both_yes"], ag["stats"]["only_a"], ag["stats"]["only_b"], ag["stats"]["both_no"]) == (2, 1, 0, 2)
    assert "a report either of them marked as awaiting classification, ongoing or not retrieved is left out" in ag["note"]
    assert ag["left_out"] == ag["both_done"] - ag["stats"]["n"]
