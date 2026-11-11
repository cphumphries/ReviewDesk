"""De-duplication: the rule merges true duplicates across the five databases and keeps apart reports that only look
alike (a conference abstract and the article that followed, an erratum with the article's title, a preprint); pairs
the rule cannot decide are listed for a person; marking a pair as not duplicates, and merging by hand, work and last."""
from __future__ import annotations

import os

import pytest

import realistic
from helpers import ApiError, TruthMap, log_and_import, reference_project, ris, tiny_records
from reviewdesk import dedup
from reviewdesk.project import Project

REVIEW = realistic.Review(2026, "normal")


@pytest.fixture(scope="module")
def processed(tmp_path_factory):
    """The reference review's five database exports, added and processed (ReviewDesk's code, no server)."""
    base = tmp_path_factory.mktemp("dedup")
    old = os.environ.get("REVIEWDESK_HOME")
    os.environ["REVIEWDESK_HOME"] = str(base / "home")
    p = Project.create(str(base / "OneDrive" / "AI triage review"), "AI in ED triage", "Dr Colin Hawthorne", "CH")
    for e in REVIEW.files("main"):
        p.add_import("CH", e.name, e.data, {"set": "main", "database": e.database, "platform": e.platform})
    summary = p.process_imports("CH")
    yield p, summary, TruthMap(REVIEW, p.dir)
    if old is not None:
        os.environ["REVIEWDESK_HOME"] = old


def works_split(t: TruthMap) -> dict:
    """{work: its record IDs} for works that ended up in more than one record."""
    out = {}
    for rid, w in t.work.items():
        out.setdefault(w.wid, []).append(rid)
    return {wid: rids for wid, rids in out.items() if len(rids) > 1}


def test_the_rule_merges_the_true_duplicates_across_the_five_databases(processed):
    p, summary, t = processed
    unique = {w for e in REVIEW.files("main") for w in e.wids if w}
    read = sum(e.records for e in REVIEW.files("main"))
    assert summary["read"] == read and summary["empty"] == 1
    split = works_split(t)
    # every true duplicate is merged, including first authors with two-word surnames (Te Whāiti, Ní Fhaoláin) written
    # 'Surname, Given' in one database and 'Surname Initials' in another
    assert not split, [REVIEW.by_wid()[w] for w in split]
    assert len(p.records()) == len(unique)
    assert summary["duplicates"] == read - 1 - len(p.records())


def test_no_two_different_reports_are_ever_merged(processed):
    p, _, t = processed
    dd = p.dedup_report()
    by_wid = REVIEW.by_wid()
    for mg in dd["merges"]:
        removed = by_wid[t.imports[mg["from_import"]].wids[mg["from_index"]]]
        assert t.of(mg["kept"]).wid == removed.wid, (mg["rule"], t.of(mg["kept"]), removed)
    rules = {m["rule"] for m in dd["merges"]}
    assert {"DOI", "title, year and first author"} <= rules
    assert dd["rule"] == dedup.RULE_TEXT


def records_of(t: TruthMap, w) -> list:
    return sorted(rid for rid, x in t.work.items() if x is w)


def listed(p, a: str, b: str):
    return next((x for x in p.dedup_report()["possible"] if {x["a"], x["b"]} == {a, b}), None)


def test_a_conference_abstract_and_the_article_that_followed_are_kept_apart_and_listed(processed):
    p, _, t = processed
    conf = next(w for w in REVIEW.works if w.kind == "conference" and w.study == "S01")
    art = next(w for w in REVIEW.works if w.kind == "article" and w.study == "S01")
    assert realistic.tkey(conf.title) == realistic.tkey(art.title)
    (rc,), (ra,) = records_of(t, conf), records_of(t, art)
    assert rc != ra
    pair = listed(p, rc, ra)
    assert pair and pair["kinds_differ"] and not pair["identical"]


def test_an_erratum_with_the_articles_own_title_is_kept_apart(processed):
    p, _, t = processed
    er = next(w for w in REVIEW.works if w.kind == "erratum" and not w.title.startswith("Correction"))
    art = next(w for w in REVIEW.works if w.kind == "article" and w.title == er.title)
    (re_,), (ra,) = records_of(t, er), records_of(t, art)
    assert re_ != ra and "rratum" in p.by_id()[re_]["pubtype"]
    assert listed(p, re_, ra)["kinds_differ"]


def test_a_preprint_and_its_article_are_kept_apart(processed):
    p, _, t = processed
    pp = next(w for w in REVIEW.works if w.kind == "preprint")
    art = next(w for w in REVIEW.works if w.kind == "article" and w.study == pp.study)
    assert records_of(t, pp) and records_of(t, art) and set(records_of(t, pp)).isdisjoint(records_of(t, art))


def test_the_same_record_with_and_without_its_doi_is_merged(processed):
    p, _, t = processed
    w = REVIEW.works[0]
    with_doi = [db for db in realistic.DATABASES if ("main", db) in w.found and REVIEW._for(w, db)["doi"]]
    without = [db for db in realistic.DATABASES if ("main", db) in w.found and not REVIEW._for(w, db)["doi"]]
    assert with_doi and without
    (rid,) = records_of(t, w)
    assert p.by_id()[rid]["doi"] == w.doi                  # the merged record keeps the DOI
    assert len(p.by_id()[rid]["sources"]) == len(with_doi) + len(without)


def test_a_title_in_title_case_with_other_punctuation_is_the_same_title():
    assert dedup.tkey("Machine Learning-Based Triage: A Cluster-Randomised Trial.") == dedup.tkey("Machine learning based triage – a cluster randomised trial")
    assert dedup.tkey("Triage in Whangārei") == dedup.tkey("Triage in Whangarei")


# ------------------------------------------------------------------------------------------------ the rule, record by record
@pytest.fixture
def lead(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWDESK_HOME", str(tmp_path / "home"))
    p = Project.create(str(tmp_path / "proj"), "Test review", "Dr Colin Hawthorne", "CH")

    def run(*files):
        for k, (db, recs) in enumerate(files):
            p.add_import("CH", f"{db}-{k}.ris", ris(*recs), {"set": "main", "database": db})
        p.process_imports("CH")
        return p
    return run


ART = {"title": "Prospective evaluation of an AI triage tool in the emergency department", "authors": ["Ngata, Aroha", "Smith, John"],
       "year": "2025", "journal": "Emergency Medicine Journal", "doi": "10.1136/emermed-2024-214001", "abstract": "Background: …"}


def test_records_with_the_same_doi_and_similar_titles_merge(lead):
    p = lead(("MEDLINE", [ART]), ("Scopus", [{**ART, "title": ART["title"].upper() + "."}]))
    assert len(p.records()) == 1 and p.dedup_report()["merges"][0]["rule"] == "DOI"


def test_the_same_doi_with_a_different_title_is_listed_not_merged(lead):
    other = {**ART, "title": "Nurse workload and burnout during winter pressures in Scotland"}
    p = lead(("MEDLINE", [ART]), ("Embase", [other]))
    assert len(p.records()) == 2
    (x,) = p.dedup_report()["possible"]
    assert x["why"] == "same DOI, different titles"


def test_the_same_title_with_different_dois_is_never_merged(lead):
    p = lead(("MEDLINE", [ART]), ("Embase", [{**ART, "doi": "10.1136/emermed-2024-299999"}]))
    assert len(p.records()) == 2
    assert p.dedup_report()["possible"][0]["similarity"] == 1.0


def test_the_same_title_year_and_first_author_without_dois_merge(lead):
    p = lead(("MEDLINE", [ART]), ("CINAHL", [{**ART, "doi": "", "authors": ["Ngata A", "Smith J"]}]))
    assert len(p.records()) == 1 and p.dedup_report()["merges"][0]["rule"] == "title, year and first author"


def test_the_same_title_year_and_journal_merge_when_authors_differ_in_form(lead):
    p = lead(("MEDLINE", [ART]), ("CENTRAL", [{**ART, "doi": "", "authors": []}]))
    assert len(p.records()) == 1 and p.dedup_report()["merges"][0]["rule"] == "title, year and journal"


def test_years_one_apart_with_the_same_first_author_merge_but_two_apart_do_not(lead):
    p = lead(("MEDLINE", [ART]), ("Embase", [{**ART, "doi": "", "year": "2026", "journal": "Emerg Med J"}]),
             ("CINAHL", [{**ART, "doi": "", "year": "2023", "journal": "EMJ"}]))
    assert len(p.records()) == 2
    assert [m["rule"] for m in p.dedup_report()["merges"]] == ["title and first author"]


def test_a_short_title_is_never_enough_on_its_own(lead):
    short = {"title": "Triage", "authors": ["Ngata, A"], "year": "2024", "journal": "EMJ"}
    p = lead(("MEDLINE", [short]), ("Embase", [{**short, "journal": "Emerg Med J"}]))      # no DOI on either
    assert len(p.records()) == 2


SHORT_ART = {"title": "Ambulance offload delays", "authors": ["Ngata, Oliver", "Mohammed, Fatima"], "year": "2019",
             "journal": "American Journal of Emergency Medicine", "abstract": "Background: a recurring problem."}
IDS = {"doi": "10.1016/j.ajem.2019.10.038", "pmid": "37535631"}


def test_a_short_title_merges_with_the_same_first_author_year_and_journal(lead):
    # the beta test's case: the MEDLINE copy has a DOI and PubMed ID, the Embase copy neither (or the journal's name
    # written another way); a title under 25 characters cannot match on the title, year or author alone
    p = lead(("MEDLINE", [{**SHORT_ART, **IDS}]), ("Embase", [{**SHORT_ART, "journal": "The American journal of emergency medicine"}]))
    assert len(p.records()) == 1
    (m,) = p.dedup_report()["merges"]
    assert m["rule"] == "short title, year, first author and journal" and p.records()[0]["doi"] == IDS["doi"]


@pytest.mark.parametrize("change", [{"authors": ["Okafor, Nnamdi"]}, {"year": "2020"}, {"journal": "Emergency Medicine Australasia"},
                                    {"journal": ""}, {"authors": []}, {"volume": "38"}, {"pages": "1201-1206"}])
def test_a_short_title_is_kept_apart_unless_author_year_and_journal_all_agree(lead, change):
    a = {**SHORT_ART, **IDS, "volume": "37", "pages": "800-805"}
    p = lead(("MEDLINE", [a]), ("Embase", [{**SHORT_ART, "volume": "37", "pages": "800-5", **change}]))
    assert len(p.records()) == 2


@pytest.mark.parametrize("title", ["Editorial", "Introduction", "Guest editorial", "In this issue", "Letter to the editor"])
def test_a_heading_is_never_merged_on_its_title(lead, title):
    ed = {"title": title, "authors": ["Fraser, Ewan"], "year": "2025", "journal": "Emergency Medicine Journal"}
    # other authors or other years: never merged, and not even listed
    p = lead(("MEDLINE", [ed]), ("Embase", [{**ed, "authors": ["Okafor, N"]}]), ("CINAHL", [{**ed, "year": "2024"}]))
    assert len(p.records()) == 3 and not p.dedup_report()["possible"]
    # the same author, year and journal (the editor's column in two issues, or one editorial in two databases): kept
    # apart, and listed for the reviewers to decide
    q = lead(("Scopus", [{**ed, "volume": "42"}]))
    assert len(q.records()) == 4
    (x,) = q.dedup_report()["possible"]
    assert {x["a"], x["b"]} == {r["id"] for r in q.records() if r["journal"] and r["year"] == "2025" and r["authors"].startswith("Fraser")}


def test_a_short_generic_title_by_different_authors_or_in_different_years_is_never_merged():
    recs = [{"id": f"R{k}", "title": t, "authors": au, "year": y, "journal": "Emergency Medicine Journal", "pubtype": ""}
            for k, (t, au, y) in enumerate([("Triage", "Ngata, A", "2024"), ("Triage", "Okafor, N", "2024"),
                                            ("Triage", "Ngata, A", "2023"), ("Introduction", "Ngata, A", "2024"),
                                            ("Introduction", "Okafor, N", "2023")])]
    for r in recs:
        assert dedup.Index([x for x in recs if x is not r]).find(dict(r, id="new"), set()) == (None, "")
    assert dedup.keys_of(recs[3]) == [] and dedup.keys_of(recs[4]) == []          # a heading has no title key at all
    twin = dict(recs[0], id="new")
    assert dedup.Index(recs[:1]).find(twin, set())[1] == "short title, year, first author and journal"


def test_an_update_search_copy_of_a_short_titled_record_joins_it_however_short_the_title(api):
    """The beta test's S1: the update search finds again, in Embase without its DOI or PubMed ID, a record the main
    search found in MEDLINE; it must count as a duplicate (3,600 records, not 3,601)."""
    c, folder = api
    c.settings(screening={"mode": "dual", "calibration_size": 0, "ai_second_screener": False})
    c.add_import(ris({**SHORT_ART, **IDS}, *tiny_records(5, seed=3)), "medline.ris", "MEDLINE")
    c.process()
    rid = next(r["id"] for r in c.get("/api/records")["records"] if r["title"] == SHORT_ART["title"])
    c.post("/api/screening/start", {"set": "main"})
    c.add_import(ris(SHORT_ART, *tiny_records(2, seed=5, prefix="New: ")), "embase update.ris", "Embase", set_name="update")
    s = c.process()["summary"]
    assert s["records"] == 8 and s["duplicates"] == 1
    assert [r["title"] for r in c.get("/api/records", set="update")["records"]] == [r["title"] for r in tiny_records(2, seed=5, prefix="New: ")]
    m = next(m for m in c.get("/api/dedup")["merges"] if m["kept"] == rid)
    assert m["rule"] == "short title, year, first author and journal"
    pr = c.get("/api/prisma")["counts"]["db"]
    assert pr["records"] == 8 and pr["duplicates"] == 1 and not c.get("/api/prisma")["counts"]["checks"]


def test_pairs_from_different_searches_are_listed_and_can_be_merged_into_the_earlier_search(api):
    c, folder = api
    c.settings(screening={"mode": "dual", "calibration_size": 0, "ai_second_screener": False})
    c.add_import(ris({**SHORT_ART, **IDS}, *tiny_records(4, seed=3)), "medline.ris", "MEDLINE")
    c.process()
    c.post("/api/screening/start", {"set": "main"})
    # the update copy names the journal by its abbreviation: the rule cannot be sure, so a person decides
    c.add_import(ris({**SHORT_ART, "journal": "Am J Emerg Med"}), "embase update.ris", "Embase", set_name="update")
    c.process()
    d = c.get("/api/dedup")
    (x,) = d["possible"]
    assert x["a"]["title"] == x["b"]["title"] == SHORT_ART["title"] and x["sets"] == ["main", "update"] and not x["started"]
    rd = c.get("/api/readiness")
    assert any(i["key"] == "pairs" for s in rd["steps"] for i in s["missing"] + s["warnings"])
    d = c.post("/api/dedup/merge", {"keep": x["a"]["id"], "drop": x["b"]["id"]})
    assert not d["possible"] and not c.get("/api/records", set="update")["records"]
    pr = c.get("/api/prisma")["counts"]
    assert pr["db"]["duplicates"] == 1 and pr["db"]["manual_merges"] == 1 and not pr["checks"]
    assert "short title" in dedup.RULE_TEXT and "different searches" in dedup.RULE_TEXT


@pytest.mark.parametrize("pubtype", ["Erratum", "Letter", "Comment", "Retraction of publication", "Conference Abstract"])
def test_a_report_of_another_kind_with_the_same_title_is_kept_apart(lead, pubtype):
    p = lead(("MEDLINE", [{**ART, "pubtype": "Journal Article"}]), ("Embase", [{**ART, "doi": "", "pubtype": pubtype}]))
    assert len(p.records()) == 2
    (x,) = p.dedup_report()["possible"]
    assert x["kinds_differ"]


def test_the_same_title_and_year_by_other_authors_in_another_journal_is_kept_apart(lead):
    p = lead(("MEDLINE", [ART]), ("Embase", [{**ART, "doi": "", "authors": ["Okafor, N"], "journal": "Annals of Emergency Medicine"}]))
    assert len(p.records()) == 2


def test_a_two_word_surname_matches_across_author_formats(lead):
    a = {**ART, "doi": "", "authors": ["Te Whāiti, Aroha", "Smith, John"], "year": "2024"}
    b = {**ART, "doi": "", "authors": ["Te Whaiti A.", "Smith J."], "year": "2025", "journal": "Emerg Med J"}
    p = lead(("MEDLINE", [a]), ("Embase", [b]))
    assert len(p.records()) == 1


def test_a_record_with_nothing_to_screen_is_set_aside_and_counted(lead):
    p = lead(("CINAHL", [ART, {"title": "", "authors": [], "year": ""}]))
    assert len(p.records()) == 1 and p.dedup_report()["files"][0]["empty"] == 1


# ------------------------------------------------------------------------------------------------ by hand, through the API
@pytest.fixture
def api(desk, tmp_path):
    c = desk.client()
    folder = reference_project(c, str(tmp_path / "OneDrive"))
    return c, folder


def near_duplicate(c, folder) -> tuple:
    """Adds a second copy of one report that the rule cannot merge (a word of the title differs and there is no DOI or
    PubMed ID), as happens when a database rewrites a title, and returns (the copy's record ID, the original's)."""
    recs = c.get("/api/records", limit=1000)["records"]
    src = next(r for r in recs if r["authors"] and len(r["title"]) > 40 and r["year"])
    words = src["title"].split()
    title = " ".join(words[:-1] + ["(" + words[-1] + ")"]) + " a"           # very similar, but not the same title
    data = (f"TY  - JOUR\nTI  - {title}\n" + "".join(f"AU  - {a.strip()}\n" for a in src["authors"].split(";")[:3])
            + f"PY  - {src['year']}\nJO  - {src['journal']}\nER  - \n").encode("utf-8")
    c.add_import(data, "a rewritten title.ris", "Scopus")
    c.process()
    new = next(r["id"] for r in c.get("/api/records", limit=1000)["records"] if r["title"] == title)
    return new, src["id"]


def same_work(t, extra: dict):
    """Whether two records are reports of the same work, for records the generator made and the near-duplicate."""
    wid = lambda rid: t.of(extra.get(rid, rid)).wid
    return lambda a, b: wid(a) == wid(b)


def test_possible_duplicates_are_listed_with_what_the_reviewer_needs_to_decide(api):
    c, folder = api
    log_and_import(c, REVIEW)
    c.process()
    near_duplicate(c, folder)
    d = c.get("/api/dedup")
    assert d["rule"] == dedup.RULE_TEXT and len(d["possible"]) >= 3
    for x in d["possible"]:
        for side in ("a", "b"):
            assert {"id", "title", "year", "journal", "authors", "doi", "pubtype", "found_in"} <= set(x[side])
        assert x["why"] in ("very similar titles", "same DOI, different titles") and not x["started"]
    assert any(x["kinds_differ"] for x in d["possible"])
    assert d["merges"] and all(m["kept_title"] for m in d["merges"])


def test_marking_a_pair_as_not_duplicates_lasts_through_reprocessing(api):
    c, folder = api
    log_and_import(c, REVIEW)
    c.process()
    new, src = near_duplicate(c, folder)
    t = TruthMap(REVIEW, folder)
    same_as = same_work(t, {new: src})
    d = c.get("/api/dedup")
    differ = [x for x in d["possible"] if not same_as(x["a"]["id"], x["b"]["id"])]
    same = [x for x in d["possible"] if same_as(x["a"]["id"], x["b"]["id"])]
    assert differ and same
    d = c.post("/api/dedup/notdup", {"pairs": [[x["a"]["id"], x["b"]["id"]] for x in differ]})
    left = {frozenset((x["a"]["id"], x["b"]["id"])) for x in d["possible"]}
    assert left == {frozenset((x["a"]["id"], x["b"]["id"])) for x in same}
    c.process()
    assert {frozenset((x["a"]["id"], x["b"]["id"])) for x in c.get("/api/dedup")["possible"]} == left
    st = c.get("/api/dedup")["overrides"]
    assert len(st["not_dup"]) == len(differ)


def test_merging_a_pair_by_hand(api):
    c, folder = api
    log_and_import(c, REVIEW)
    c.process()
    new, src = near_duplicate(c, folder)
    t = TruthMap(REVIEW, folder)
    same_as = same_work(t, {new: src})
    n = c.get("/api/imports")["records"]
    x = [x for x in c.get("/api/dedup")["possible"] if same_as(x["a"]["id"], x["b"]["id"])][0]
    before = c.get("/api/prisma")["counts"]["db"]["duplicates"]
    with pytest.raises(ApiError) as e:                  # one by one: this pair is not plainly the same report
        c.post("/api/dedup/merge", {"pairs": [[x["a"]["id"], x["b"]["id"]]]})
    assert "need checking one by one" in e.value.message and "Nothing was merged" in e.value.message
    d = c.post("/api/dedup/merge", {"keep": x["a"]["id"], "drop": x["b"]["id"]})
    assert c.get("/api/imports")["records"] == n - 1
    m = next(m for m in d["merges"] if m.get("from_record") == x["b"]["id"])
    assert m["kept"] == x["a"]["id"] and m["rule"] == "marked as a duplicate by the review team"
    after = c.get("/api/prisma")["counts"]
    assert after["db"]["duplicates"] == before + 1 and after["db"]["manual_merges"] == 1 and not after["checks"]
    st, _, body = c.download("dedup")
    assert st == 200 and "marked as a duplicate by the review team" in body.decode("utf-8-sig")
    # undone before screening starts
    c.post("/api/dedup/notdup", {"kept": x["a"]["id"], "other": x["b"]["id"]})
    assert c.get("/api/imports")["records"] == n


def test_splitting_records_the_rule_merged(api):
    c, folder = api
    rec = {**ART}
    c.add_import(ris(rec), "medline.ris", "MEDLINE")
    c.add_import(ris({**rec, "title": rec["title"] + " (abridged)", "abstract": "A different, longer abstract for a letter."}), "embase.ris", "Embase")
    c.process()
    d = c.get("/api/dedup")
    (m,) = d["merges"]
    d = c.post("/api/dedup/notdup", {"kept": m["kept"], "other": m["fingerprint"]})
    assert c.get("/api/imports")["records"] == 2 and not d["merges"]


def test_merging_across_searches_keeps_the_earlier_searchs_record_and_merging_stops_once_screening_has_started(api, tmp_path):
    c, folder = api
    a, b = tiny_records(2, seed=3)
    c.add_import(ris(a), "medline.ris", "MEDLINE")
    c.add_import(ris(b), "citations.ris", "Citation searching", set_name="other")
    c.process()
    ids = {r["set"]: r["id"] for r in c.get("/api/records")["records"]}
    # as the rule merges across searches: the database search's record stays, whichever way round the pair is given,
    # and the other counts as a duplicate in the other-methods column, so both columns still add up
    d = c.post("/api/dedup/merge", {"keep": ids["other"], "drop": ids["main"]})
    assert [r["id"] for r in c.get("/api/records")["records"]] == [ids["main"]]
    m = next(m for m in d["merges"] if m.get("from_record"))
    assert m["kept"] == ids["main"] and m["from_record"] == ids["other"]
    pr = c.get("/api/prisma")["counts"]
    assert pr["other"]["duplicates"] == 1 and pr["db"]["duplicates"] == 0 and not pr["checks"]
    c.post("/api/dedup/notdup", {"kept": ids["main"], "other": ids["other"]})          # undone: apart again
    assert len(c.get("/api/records")["records"]) == 2
    more = tiny_records(6, seed=4)
    c.add_import(ris(*more), "embase.ris", "Embase")
    c.process()
    c.settings(screening={"mode": "dual", "calibration_size": 0, "ai_second_screener": False})
    c.post("/api/screening/start", {"set": "main"})
    main = [r["id"] for r in c.get("/api/records", set="main")["records"]]
    e = c.error("POST", "/api/dedup/merge", {"keep": main[0], "drop": main[1]})
    assert "Screening of these records has started" in e.message
    # a record from other methods can still join a database record that is being screened (it keeps its text)
    seen = c.get("/api/record", id=ids["main"])["title"]
    c.post("/api/dedup/merge", {"keep": ids["main"], "drop": ids["other"]})
    assert c.get("/api/record", id=ids["main"])["title"] == seen and not c.get("/api/records", set="other")["records"]
    assert not c.get("/api/prisma")["counts"]["checks"]


def test_record_ids_never_change_when_files_are_added_later(api):
    c, folder = api
    first = tiny_records(5, seed=8)
    c.add_import(ris(*first), "medline.ris", "MEDLINE")
    c.process()
    before = {r["title"]: r["id"] for r in c.get("/api/records")["records"]}
    later = tiny_records(3, seed=9, prefix="Later: ")
    c.add_import(ris(*(later + [first[2]])), "embase.ris", "Embase")
    c.process()
    after = {r["title"]: r["id"] for r in c.get("/api/records")["records"]}
    assert all(after[t] == rid for t, rid in before.items())
    assert len(after) == 8 and sorted(after.values())[-1] == "R00008"


def test_an_update_search_re_run_adds_only_new_records_and_keeps_screened_text(api):
    c, folder = api
    log_and_import(c, REVIEW, sets=("main",))
    c.process()
    c.settings(screening={"mode": "dual", "calibration_size": 0, "ai_second_screener": False})
    c.post("/api/screening/start", {"set": "main"})
    p0 = Project(folder)
    seen = {r["id"]: (r["title"], r["abstract"]) for r in p0.records()}
    log_and_import(c, REVIEW, sets=("update",))
    s = c.process()["summary"]
    t = TruthMap(REVIEW, folder)
    recs = Project(folder).records()
    new = [r for r in recs if r["set"] == "update"]
    assert {t.of(r["id"]).set for r in new} == {"update"}           # only works the first searches did not find
    assert len(new) == len({w for e in REVIEW.files("update") for w in e.wids if REVIEW.by_wid()[w].set == "update"})
    assert all((r["title"], r["abstract"]) == seen[r["id"]] for r in recs if r["id"] in seen)   # as screened
    pr = c.get("/api/prisma")["counts"]
    assert pr["db"]["update_included"] and not pr["checks"]
    assert s["read"] == sum(e.records for e in REVIEW.exports if e.set in ("main", "update"))


# ------------------------------------------------------------------------------------------------ names of imports
@pytest.fixture
def four_digits_repeat(monkeypatch):
    """The random four-digit number in the name of an import or a search comes out the same twice, as it does now and
    then (unique_ids.py stops that happening at random in the other tests)."""
    import random
    import unique_ids
    real = unique_ids.original()
    monkeypatch.setattr(random.SystemRandom, "randint", lambda self, a, b: 42424242 if (a, b) == unique_ids.RANGE else real(self, a, b))


def test_two_exports_added_on_the_same_day_never_share_an_id(api, four_digits_repeat):
    c, folder = api
    c.add_import(ris(*tiny_records(3, seed=71)), "embase update.ris", "Embase", set_name="update")
    c.add_import(ris(*tiny_records(4, seed=72)), "cinahl update.ris", "CINAHL", set_name="update")
    ids = [m["id"] for m in c.get("/api/imports")["imports"]]
    c.process()
    assert len(set(ids)) == 2 and c.get("/api/imports")["records"] == 7


def test_two_searches_logged_on_the_same_day_are_both_kept(api, four_digits_repeat):
    c, folder = api
    c.post("/api/searchlog", {"database": "MEDLINE", "date": "2026-10-05", "string": "triage AND AI", "results": "99"})
    c.post("/api/searchlog", {"database": "Embase", "date": "2026-10-05", "string": "triage AND AI", "results": "151"})
    assert sorted(e["database"] for e in c.get("/api/searchlog")["entries"]) == ["Embase", "MEDLINE"]
