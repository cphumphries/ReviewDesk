"""The PRISMA 2020 flow diagram adds up in every box (databases, registers, other methods and the update search), its
numbers match the truth of the searches, every download opens (CSV parses, workbooks are valid, SVG parses, RIS reads
back with ReviewDesk's own parser), no spreadsheet cell can start a formula, and files that show every vote are
refused to a reviewer and while a sample is open."""
from __future__ import annotations

import json
from collections import Counter

import pytest

import realistic
from helpers import (LocalServer, SimReviewer, TruthMap, check_svg, check_xlsx, formula_cells, log_and_import, read_csv,
                     reference_plan, reference_project, ris, screen_all, svg_text, tiny_records)
from reviewdesk import server
from reviewdesk.parsers import parse_bytes

REVIEW = realistic.Review(2026, "normal")
TRICKY = [{"title": t, "authors": ["@handle, A", "-Smith, B"], "year": "2024", "journal": "+Journal", "doi": "", "abstract": "=cmd|' /C calc'!A0"}
          for t in ("=HYPERLINK(\"http://example.org\",\"Click\") triage of chest pain in the emergency department",
                    "+44 7700 900123: telephone numbers in emergency department triage notes",
                    "-Omics and machine learning for emergency department triage prediction",
                    "@risk patients identified by an AI triage tool in the emergency department")]


@pytest.fixture
def done(reviewed):
    reviewed[3].activate()
    return reviewed


@pytest.fixture(scope="module")
def reviewed(tmp_path_factory):
    """The reference review's searches (all five databases, the update search, citation searching and a hand-search)
    screened to the end by both reviewers, from the truth, with two full-text disagreements settled by the lead."""
    base = tmp_path_factory.mktemp("prisma")
    s = LocalServer(str(base / "home-CH"))
    c = s.client()
    folder = reference_project(c, str(base / "OneDrive"), screening=reference_plan(mode="dual", calibration_size=0, ai_second_screener=False),
                               other_sets={"update": {"mode": "dual"}, "other": {"mode": "dual"}})
    log_and_import(c, REVIEW, sets=("main", "update", "other"))
    c.add_import(ris(*TRICKY), "hand-search extras.ris", "Hand-searching", set_name="other")
    c.process()
    t = TruthMap(REVIEW, folder)
    # the lead checks the pairs flagged as possible duplicates, from the truth
    same, differ = [], []
    for x in c.get("/api/dedup")["possible"]:
        wa, wb = t.of(x["a"]["id"]), t.of(x["b"]["id"])
        (same if wa and wb and wa.wid == wb.wid else differ).append([x["a"]["id"], x["b"]["id"]])
    for a, b in same:
        c.post("/api/dedup/merge", {"keep": a, "drop": b})
    if differ:
        c.post("/api/dedup/notdup", {"pairs": differ})
    t.refresh()
    for set_name in ("main", "update", "other"):
        c.post("/api/screening/start", {"set": set_name})
    sims = {who: SimReviewer(c, who, t) for who in ("NH", "CH")}
    tricky_ids = {r["id"] for r in c.get("/api/records", set="other")["records"] if r["title"][:1] in "=+-@"}
    for set_name in ("main", "update", "other"):
        for who in ("NH", "CH"):
            if who == "NH":
                c.be(who)
            sim = sims[who]
            sim._ensure()
            for it in c.queue("screen", set_name):
                if it["id"] in tricky_ids:
                    c.decide(it["id"], "ta", "exclude")
                elif not it["done"]:
                    d, _ = sim.ta_choice(it["id"], "ta", "screen")
                    c.decide(it["id"], "ta", d)
    inc = sorted(rid for rid, w in t.work.items() if w and w.ft == "include")
    excl = sorted(rid for rid, w in t.work.items() if w and w.ft == "exclude")
    sims["NH"].forced = {("ft", inc[0]): ("exclude", "Wrong outcomes"), ("ft", excl[0]): ("exclude", "Wrong comparator")}
    for who in ("NH", "CH"):
        sims[who].fulltext()
    c.be("CH")
    for x in c.get("/api/conflicts")["conflicts"]:
        w = t.of(x["id"])
        c.resolve(x["id"], "ft", w.ft, reason=w.ft_reason, study=w.study_label)
    yield c, folder, t, s
    s.stop()


def column_checks(X: dict, identified: int) -> None:
    assert identified - X["duplicates"] - X["other_removed"] == X["records"]
    assert X["screened"] + X["pending_ta"] == X["records"]
    assert X["ta_excluded"] + X["sought"] == X["screened"]
    assert X["not_retrieved"] + X["assessed"] + X["pending_ft"] == X["sought"]
    # awaiting classification and ongoing studies leave "Reports assessed for eligibility" like the exclusions do
    assert X["assessed"] - X["ft_excluded"] - X["awaiting"] - X["ongoing"] == X["ft_included"]
    assert sum(X["ft_reasons"].values()) == X["ft_excluded"]


def test_every_box_of_the_diagram_adds_up(done):
    c, folder, t, _ = done
    pr = c.get("/api/prisma")
    C = pr["counts"]
    assert C["checks"] == [] and C["complete"] is True
    D, O = C["db"], C["other"]
    column_checks(D, D["identified_databases"] + D["identified_registers"])
    column_checks(O, O["identified"])
    assert D["update_included"] is True and D["automation"] == 0
    assert D["ft_included"] + O["ft_included"] == C["included_reports"]
    # the registers typed in Project set-up and never imported are in no box: identification adds up without them
    assert D["registers_not_imported"] and D["identified_registers"] == sum(D["registers"].values()) == 0
    assert D["identified_databases"] - D["duplicates"] - D["other_removed"] == D["records"] == D["screened"]


def test_the_numbers_are_those_of_the_searches_and_their_truth(done):
    c, folder, t, _ = done
    C = c.get("/api/prisma")["counts"]
    D, O = C["db"], C["other"]
    per_db = Counter()
    for e in REVIEW.exports:
        if e.set in ("main", "update"):
            per_db[e.database] += e.records
    assert D["databases"] == dict(per_db) and D["registers"] == {}
    assert D["registers_not_imported"] == {"ClinicalTrials.gov": 11, "ANZCTR": 4}
    by = REVIEW.by_wid()
    db_works = {w for e in REVIEW.exports if e.set in ("main", "update") for w in e.wids if w}
    other_works = {w for e in REVIEW.exports if e.set == "other" for w in e.wids if w} - db_works
    assert D["records"] == len(db_works) and D["other_removed"] == 1
    assert O["records"] == len(other_works) + len(TRICKY)
    assert O["identified"] == sum(e.records for e in REVIEW.exports if e.set == "other") + len(TRICKY)
    ws = [by[w] for w in db_works]
    assert D["ta_excluded"] == sum(1 for w in ws if w.ta == "exclude")
    assert D["sought"] == sum(1 for w in ws if w.ta == "include")
    assert D["not_retrieved"] == sum(1 for w in ws if w.ft == "not_retrieved")
    assert D["awaiting"] == sum(1 for w in ws if w.ft == "awaiting")
    assert D["ft_reasons"] == dict(Counter(w.ft_reason for w in ws if w.ft == "exclude"))
    allw = [by[w] for w in db_works | other_works]
    assert C["included_reports"] == sum(1 for w in allw if w.ft == "include")
    assert C["included_studies"] == len({w.study_label.lower() for w in allw if w.ft == "include"}) == len(REVIEW.eligible_studies())


def test_the_diagram_is_an_svg_with_the_numbers_in_its_boxes(done):
    c, folder, t, _ = done
    st, hd, body = c.download("prisma")
    assert st == 200 and hd["content-type"] == "image/svg+xml"
    text = svg_text(body)
    C = c.get("/api/prisma")["counts"]
    for db, n in C["db"]["databases"].items():
        assert f"{db} (n = {n:,})" in text
    assert "Registers (n = 0 imported)*" in text and f"Registers (n = {11 + 4}" not in text
    flat = " ".join(text.split())
    assert ("* Registers also searched, whose records were not imported into ReviewDesk, so they are in none of these "
            "numbers: ClinicalTrials.gov (11 records found), ANZCTR (4 records found).") in flat
    assert "Adapted from Page MJ, et al. BMJ 2021;372:n71 (CC BY 4.0, https://creativecommons.org/licenses/by/4.0/)." in flat
    assert "Records excluded**" in text and "** Every record excluded was excluded by a person" in flat
    assert "the PRISMA 2020 template's other-methods column" in flat
    assert f"Records screened (n = {C['db']['screened']:,})" in text.replace("\n", " ")
    assert "Identification of studies via other methods" in text and "Database counts include the update search." in text
    assert f"Studies included in review (n = {C['included_studies']})" in text.replace("\n", " ")


DOWNLOAD_KINDS = sorted(set(server.DOWNLOADS) | {"dedup"})


@pytest.mark.parametrize("kind", DOWNLOAD_KINDS)
def test_every_download_opens(done, kind):
    c, folder, t, _ = done
    c.be("CH")
    st, hd, body = c.download(kind)
    assert st == 200, body[:300]
    assert hd["content-disposition"].startswith("attachment; filename*=UTF-8''AI%20triage%20-%20")
    name = server.DOWNLOADS.get(kind, ("de-duplication log.csv",))[0]
    if name.endswith(".csv"):
        rows = read_csv(body)
        assert rows and all(len(r) == len(rows[0]) for r in rows), "every row has a cell for every column"
        assert formula_cells(rows) == []
    elif name.endswith(".xlsx"):
        check_xlsx(body)
    elif name.endswith(".svg"):
        check_svg(body)
    elif name.endswith(".ris"):
        if body.strip():
            recs, info = parse_bytes(body, name)
            assert info["format"] == "RIS"
    elif name.endswith(".zip"):                  # the evaluation data: every spreadsheet in it opens the same way
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            assert z.testzip() is None and "README.txt" in z.namelist()
            for inner in z.namelist():
                if inner.endswith(".csv"):
                    rows = read_csv(z.read(inner))
                    assert rows and all(len(r) == len(rows[0]) for r in rows), inner
                    assert formula_cells(rows) == [], inner
    else:
        assert body.decode("utf-8").startswith("Methods summary for")


def test_no_cell_in_any_spreadsheet_can_start_a_formula(done):
    c, folder, t, _ = done
    st, _, body = c.download("records")
    rows = read_csv(body)
    tricky = [r for r in rows if r[2].startswith("'")]
    assert len(tricky) == 4 and {r[2][:2] for r in tricky} == {"'=", "'+", "'-", "'@"}
    assert all(r[3].startswith("'@") for r in tricky) and all(r[5] == "'+Journal" for r in tricky)
    st, _, body = c.download("decisions")
    assert formula_cells(read_csv(body)) == []


def test_the_included_reports_read_back_as_the_included_records(done):
    c, folder, t, _ = done
    st, _, body = c.download("included")
    recs, _ = parse_bytes(body, "included.ris")
    inc = sorted(x["id"] for x in c.get("/api/fulltext")["rows"] if x["status"] == "included")
    assert len(recs) == len(inc) == c.get("/api/prisma")["counts"]["included_reports"]
    by = {r["id"]: r for r in c.get("/api/records", limit=1000)["records"]}
    assert sorted(r["title"] for r in recs) == sorted(by[i]["title"] for i in inc)
    assert all(r["doi"] == by[i]["doi"] for r, i in zip(sorted(recs, key=lambda r: r["title"]), sorted(inc, key=lambda i: by[i]["title"])))
    assert b"; study: " in body


def test_the_search_log_and_methods_summary_say_what_was_done(done):
    c, folder, t, _ = done
    st, _, body = c.download("searchlog")
    rows = read_csv(body)
    assert rows[0] == ["Database", "Platform", "Coverage", "Date searched", "Records", "Limits and filters", "Search strategy",
                       "Export files", "Notes", "Run by"]
    med = next(r for r in rows if r[0] == "MEDLINE" and r[5] == "None")
    assert med[1] == "Ovid" and "exp Artificial Intelligence/" in med[6] and med[7].startswith("medline_ovid_")
    assert all(not e.get("mismatch") for e in c.get("/api/searchlog")["entries"])
    summary = c.download("summary")[2].decode("utf-8")
    D = c.get("/api/prisma")["counts"]["db"]
    assert f"Database searches returned {D['identified_databases']:,} records" in summary
    assert ("ClinicalTrials.gov (11 records) and ANZCTR (4 records) were also searched, but their records were not imported "
            "into ReviewDesk, so they were not de-duplicated or screened in ReviewDesk and are in none of the counts above or "
            "in the flow diagram.") in summary
    assert "[Authors to complete: how the records from ClinicalTrials.gov and ANZCTR were handled outside ReviewDesk" in summary
    assert "pairs were merged" in summary or "pair was merged" in summary
    assert "0 pairs" not in summary and "0 pair " not in summary
    assert "Screening is not finished" not in summary


def test_files_that_show_every_vote_are_refused_to_a_reviewer(done):
    c, folder, t, _ = done
    c.be("NH")
    for kind, words in (("records", "only the project lead can download it"), ("decisions", "only the project lead can download it"),
                        ("ai", "Only the project lead can download the AI's answers")):
        st, _, body = c.download(kind)
        assert st == 403 and words in json.loads(body)["error"]
    for kind in ("prisma", "summary", "searchlog", "excluded", "included", "extraction", "rob", "rob_svg", "dedup", "audit"):
        assert c.download(kind)[0] == 200, kind
    blocked = c.get("/api/prisma")["downloads"]
    assert blocked["records"] and blocked["decisions"] and blocked["ai"] and not blocked["prisma"]
    c.be("CH")


def test_files_that_show_every_vote_wait_while_a_sample_is_open(desk, tmp_path):
    c = desk.client()
    reference_project(c, str(tmp_path / "OneDrive"), screening=reference_plan(calibration_size=4, ai_second_screener=False))
    c.add_import(ris(*tiny_records(20, seed=5)), "medline.ris", "MEDLINE")
    c.process()
    assert c.download("records")[0] == 200                     # nothing screened yet
    c.post("/api/screening/start", {"set": "main"})
    for kind in ("records", "decisions"):
        st, _, body = c.download(kind)
        assert st == 403 and "once the calibration round and agreement sample are finished" in json.loads(body)["error"]
    for who in ("NH", "CH"):
        screen_all(c, who, lambda r: "exclude", "calibration")
    c.be("CH")
    c.post("/api/screening/calibration", {"set": "main"})
    assert c.download("decisions")[0] == 403                   # the sample is open now
    for who in ("NH", "CH"):
        screen_all(c, who, lambda r: "include" if r[-1] in "02468" else "exclude", "sample")
    c.be("CH")
    assert c.download("decisions")[0] == 403                   # still open until the lead applies the rule
    c.post("/api/screening/apply", {"set": "main"})
    assert c.download("decisions")[0] == 200


def test_the_diagram_adds_up_at_every_step_of_screening(desk, tmp_path):
    c = desk.client()
    reference_project(c, str(tmp_path / "OneDrive"), screening=reference_plan(mode="dual", calibration_size=0, ai_second_screener=False))
    recs = tiny_records(12, seed=6)
    c.add_import(ris(*recs[:8]), "medline.ris", "MEDLINE")
    c.add_import(ris(*recs[4:], {"title": "", "year": ""}), "embase.ris", "Embase")
    c.add_import(ris(*tiny_records(3, seed=7)), "citations.ris", "Citation searching", set_name="other")
    c.add_import(ris(*tiny_records(2, seed=8)), "ctgov.ris", "ClinicalTrials.gov")
    c.process()

    def check():
        C = c.get("/api/prisma")["counts"]
        assert C["checks"] == []
        column_checks(C["db"], C["db"]["identified_databases"] + C["db"]["identified_registers"])
        column_checks(C["other"], C["other"]["identified"])
        return C
    C = check()
    assert C["db"]["registers"] == {"ClinicalTrials.gov": 2} and C["db"]["identified_databases"] == 8 + 9
    assert (C["db"]["duplicates"], C["db"]["other_removed"], C["db"]["records"]) == (4, 1, 14)
    c.post("/api/screening/start", {"set": "main"})
    c.post("/api/screening/start", {"set": "other"})
    for k, who in enumerate(("NH", "CH")):
        for set_name in ("main", "other"):
            screen_all(c, who, lambda r: "include" if int(r[1:]) % 3 == 0 else "exclude", "screen", set_name)
            check()
    for who in ("NH", "CH"):
        screen_all(c, who, lambda r: ("include", "", "Study " + r) if int(r[1:]) % 2 else ("exclude", "Wrong outcomes"), "fulltext")
        check()
    assert check()["complete"]
