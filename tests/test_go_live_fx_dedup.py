"""The final audit's findings on reading database exports and removing duplicates (code-data B3, S5, M4): different
reports are never merged on weak evidence, a malformed export never loses or merges records without a word, and a
date that cannot be is shown as typed."""
from __future__ import annotations

import pytest

from reviewdesk import dedup, integrity, parsers, prisma, project, screening, timeline, userconfig
from reviewdesk.parsers import FormatError, clean_doi, parse_bytes
from reviewdesk.project import Project

SUPPL = "10.1186/s13054-019-2358-0"          # one DOI for a whole supplement of meeting abstracts
SICI1 = "10.1002/(SICI)1097-0258(19970830)16:16<1791::AID-SIM609>3.0.CO;2-5"
SICI2 = "10.1002/(SICI)1097-0258(19970830)16:16<1805::AID-SIM610>3.0.CO;2-0"


def rec(**kw) -> dict:
    base = {k: "" for k in parsers.FIELDS}
    base.update(kw)
    return parsers._clean(base)


def merged(a: dict, b: dict) -> str:
    """The rule that merges b into a (as process_imports asks it), or '' when they are kept apart."""
    idx = dedup.Index([dict(a, id="R00001", set="main", sources=["i1"])])
    b = dict(b, _tmp="", _fp=dedup.fingerprint(b))
    h, how = idx.find(b, set())
    return how if h is not None else ""


def listed(a: dict, b: dict) -> list:
    return dedup.possible_duplicates([dict(a, id="R00001", set="main"), dict(b, id="R00002", set="main")])


def ris(recs: list) -> bytes:
    out = []
    for r in recs:
        out.append("TY  - " + r.get("ty", "JOUR"))
        for tag, key in (("TI", "title"), ("AB", "abstract"), ("PY", "year"), ("JF", "journal"), ("DO", "doi"), ("VL", "volume"),
                         ("IS", "issue"), ("SP", "pages"), ("M3", "m3")):
            if r.get(key):
                out.append(f"{tag}  - {r[key]}")
        out += ["AU  - " + a for a in r.get("authors", [])] + ["ER  - ", ""]
    return "\n".join(out).encode("utf-8")


# ------------------------------------------------------------------------------------------- B3: no false merges
def test_abstracts_sharing_a_supplement_doi_are_listed_not_merged():
    adults = rec(title="Machine learning triage to predict hospital admission from the emergency department in adults: a "
                       "prospective validation", authors="Lee S; Park J", year="2019", journal="Critical Care", doi=SUPPL,
                 pages="P101", pubtype="Conference Abstract")
    children = rec(title="Machine learning triage to predict hospital admission from the emergency department in children: a "
                         "prospective validation", authors="Kim H; Park J", year="2019", journal="Critical Care", doi=SUPPL,
                   pages="P317", pubtype="Conference Abstract")
    derivation = rec(title="An artificial intelligence triage tool for sepsis in the emergency department", authors="Lee S",
                     year="2019", journal="Critical Care", doi=SUPPL, pubtype="Conference Abstract")
    validation = rec(title="An artificial intelligence triage tool for sepsis in the emergency department: external "
                           "validation in three hospitals", authors="Garcia M", year="2019", journal="Critical Care", doi=SUPPL,
                     pubtype="Conference Abstract")
    assert merged(adults, children) == "" and merged(derivation, validation) == ""
    assert [x["why"] for x in listed(adults, children)] == ["same DOI, different volume, issue or pages"]
    assert [x["why"] for x in listed(derivation, validation)] == ["same DOI, different first authors"]


def test_a_title_in_another_script_is_a_title_not_an_empty_one():
    chinese = rec(title="急诊分诊人工智能系统的前瞻性研究", authors="Zhang W", year="2019", journal="Critical Care", doi=SUPPL)
    english = rec(title="Deep learning for chest pain triage: a randomised trial", authors="Zhang W", year="2019",
                  journal="Critical Care", doi=SUPPL)
    assert dedup.title_key(chinese["title"]) == "急诊分诊人工智能系统的前瞻性研究"
    assert not dedup.similar_titles(chinese["title"], english["title"]) and merged(chinese, english) == ""
    one = rec(title="COVID-19患者急诊分诊的前瞻性研究", authors="Zhang W", year="2021", journal="Zhonghua Ji Zhen Yi Xue Za Zhi")
    two = rec(title="COVID-19疫情期间儿科急诊就诊量变化", authors="Zhang W", year="2021", journal="Zhonghua Ji Zhen Yi Xue Za Zhi")
    assert merged(one, two) == ""
    assert merged(one, dict(one)) != ""                     # the same record twice still merges


def test_old_wiley_dois_are_read_whole():
    assert clean_doi(SICI1) == SICI1.lower() and clean_doi(SICI2) == SICI2.lower()
    assert clean_doi(SICI1.replace("<", "&lt;").replace(">", "&gt;")) == SICI1.lower()
    assert clean_doi("10.1175/1520-0477(1998)079<0061:APGTWA>2.0.CO;2") == "10.1175/1520-0477(1998)079<0061:apgtwa>2.0.co;2"
    assert clean_doi("<p>10.1000/abc.1</p>") == "10.1000/abc.1"             # markup after a DOI still ends it
    one = rec(title="Tutorial in biostatistics: triage scores in emergency care. Part I: development", authors="Altman D",
              year="1997", journal="Statistics in Medicine", doi=clean_doi(SICI1), pages="1791-1804")
    two = rec(title="Tutorial in biostatistics: triage scores in emergency care. Part II: validation", authors="Royston P",
              year="1997", journal="Statistics in Medicine", doi=clean_doi(SICI2), pages="1805-1820")
    assert merged(one, two) == ""


@pytest.mark.parametrize("a,b", [("Interleukin-1α", "Interleukin-1β"), ("HER2+", "HER2−"), ("HER2(+)", "HER2(-)"),
                                 ("HER2-positive", "HER2-negative"), ("CD4⁺", "CD4-"), ("≥65 years", "<65 years")])
def test_titles_that_differ_only_in_a_greek_letter_or_a_sign_are_different_reports(a, b):
    t = "{} patients at emergency department triage: sepsis mortality in a cohort"
    one = rec(title=t.format(a), authors="Wang L", year="2018", journal="Shock", doi="10.1097/shk.0001")
    two = rec(title=t.format(b), authors="Wang L", year="2018", journal="Shock", pmid="29000001")
    assert dedup.title_key(one["title"]) != dedup.title_key(two["title"])
    assert merged(one, two) == ""
    assert not dedup.safe_to_merge_in_bulk(dict(one, doi=""), dict(two, pmid=""))


@pytest.mark.parametrize("a,b", [("TNF-alpha", "TNF-α"), ("Évaluation", "Evaluation"), ("pain & triage", "pain and triage"),
                                 ("[Triage of chest pain]", "Triage of chest pain"), ("IL–6", "IL-6"),
                                 ("Triage - a study", "Triage: a study"), ("Learning-based", "Learning based")])
def test_what_databases_write_differently_still_compares_equal(a, b):
    t = "{} in the emergency department: a prospective cohort study"
    assert dedup.title_key(t.format(a)) == dedup.title_key(t.format(b))


def test_a_conference_abstract_typed_abst_is_never_merged_on_its_title_with_the_paper():
    abst = rec(title="Machine learning triage in the emergency department: a prospective evaluation", authors="Smith J",
               year="2020", journal="Ann Emerg Med", pubtype=parsers._pubtype("", "", "ABST"), pages="S12")
    paper = rec(title="Machine learning triage in the emergency department: a prospective evaluation", authors="Smith JA; Jones K",
                year="2021", journal="Ann Emerg Med", pubtype="Journal article", doi="10.1016/j.annemergmed.2021.01.001")
    assert dedup.is_conference(abst["pubtype"]) and merged(abst, paper) == ""
    assert not dedup.is_conference("Journal Article; English Abstract")          # PubMed: the article has an English abstract


def test_an_erratum_given_its_article_doi_is_listed_not_merged():
    art = rec(title="Triage by machine learning in the emergency department: a cluster randomised trial", authors="Smith J",
              year="2020", journal="Lancet", doi="10.1016/s0140-6736(20)30001-1", pubtype="Journal Article")
    err = rec(title="Erratum: Triage by machine learning in the emergency department: a cluster randomised trial",
              authors="Smith J", year="2020", journal="Lancet", doi="10.1016/s0140-6736(20)30001-1", pubtype="Erratum")
    assert merged(art, err) == ""
    assert listed(art, err)[0]["why"] == "same DOI, different kinds of report"


def test_title_rules_need_the_details_and_authors_to_agree():
    column1 = rec(title="Images in emergency medicine", authors="Adams R", year="1998", journal="Annals of Emergency Medicine",
                  pmid="9500001", pages="100")
    column2 = rec(title="Images in emergency medicine", authors="Baker S", year="1998", journal="Annals of Emergency Medicine",
                  doi="10.1016/s0196-0644(98)70001-1", pages="455")
    assert merged(column1, column2) == ""
    protocol = rec(title="The TRIAGE-AI trial: artificial intelligence versus nurse triage in the emergency department",
                   authors="Brown T", year="2019", journal="Trials")
    results = rec(title="The TRIAGE-AI trial: artificial intelligence versus nurse triage in the emergency department",
                  authors="Brown T", year="2020", journal="JAMA", doi="10.1001/jama.2020.1")
    assert merged(protocol, results) == "" and listed(protocol, results)


def test_the_same_report_from_two_databases_still_merges():
    medline = rec(title="Machine learning triage in the emergency department: a prospective evaluation.", authors="Smith JA",
                  year="2021", journal="Acad Emerg Med", doi="10.1111/acem.1", pmid="34000001")
    embase = rec(title="Machine learning triage in the emergency department", authors="Smith J.A.", year="2021",
                 journal="Academic Emergency Medicine", doi="10.1111/ACEM.1")
    assert merged(medline, embase) == "DOI"
    a = rec(title="What predicts stroke among pregnant women? A multicentre audit", authors="Ishikawa, Ji-woo; Park, J",
            year="2024", journal="Annals of Emergency Medicine", pmid="37346703")
    b = rec(title="What predicts stroke among pregnant women? A multicentre audit", authors="Ishikawa Jw.; Park Jw.",
            year="2024", journal="Annals of Emergency Medicine")
    assert merged(a, b) == "title, year and first author"
    epub = rec(title="An XGBoost-based triage aid versus nurse triage: a stepped-wedge trial", authors="Te Whāiti, Aroha",
               year="2024", journal="Journal of Emergency Nursing", pmid="38482629")
    issue = rec(title="An XGBoost-based triage aid versus nurse triage: a stepped-wedge trial", authors="Te Whaiti A.",
                year="2025", journal="J Emerg Nurs")
    assert merged(epub, issue) == "title and first author"
    assert dedup.journals_agree("Ann Emerg Med", "Annals of Emergency Medicine")
    assert not dedup.journals_agree("Trials", "JAMA")


def test_the_not_duplicates_fingerprint_is_what_1_3_0_saved():
    r = rec(title="Interleukin-1α at triage", authors="Smith JA, Jones K; Brown T", year="2020", doi="10.1/X")
    assert dedup.fingerprint(r) == "fp:interleukin1attriage|2020|10.1/x|smithja"
    assert dedup.fingerprint(rec(title="T", authors="Kaur Jw.")) == "fp:t|||kaurjw"


def test_remove_duplicates_keeps_the_eight_different_reports(tmp_path, monkeypatch):
    p = _project(tmp_path, monkeypatch)
    p.add_import("CH", "embase.ris", ris(_B3), {"set": "main", "database": "Embase"})
    s = p.process_imports("CH")
    assert (s["read"], s["duplicates"], s["records"]) == (8, 0, 8)
    assert len(p.dedup_report()["possible"]) >= 4


def test_a_screened_record_stays_as_it_was_when_the_new_rule_splits_an_old_merge(tmp_path, monkeypatch):
    """A project whose duplicates were removed under the old rule (one DOI was enough), then screened. Removing
    duplicates again with the new rule leaves every screened record with its ID and the text people saw; the report
    the old rule had merged away comes back as a record of its own, at the end of the screening order."""
    p = _project(tmp_path, monkeypatch)
    p.add_import("CH", "embase.ris", ris(_B3[:2] + _filler(6)), {"set": "main", "database": "Embase"})
    real = dedup.id_doubt
    monkeypatch.setattr(dedup, "id_doubt", lambda a, b: "")       # the old rule: a shared DOI and similar titles merge
    assert p.process_imports("CH")["records"] == 7
    monkeypatch.setattr(dedup, "id_doubt", real)
    seen = {r["id"]: integrity.record_print(r) for r in p.records()}
    screening.start(p, "CH", "main")
    order = list(p.state()["sets"]["main"]["order"])
    for rid in order:
        for who in ("CH", "NH"):
            p.add_decision(who, rid, "ta", "exclude")
    assert p.process_imports("CH")["records"] == 8
    now = {r["id"]: r for r in p.records()}
    assert all(integrity.record_print(now[rid]) == fp for rid, fp in seen.items())
    new_order = p.state()["sets"]["main"]["order"]
    assert new_order[:len(order)] == order and len(new_order) == len(order) + 1
    assert "in children" in now[new_order[-1]]["title"]
    c = prisma.counts(p)
    assert c["checks"] == [] and c["db"]["pending_ta"] == 1
    assert not p.integrity_report()["problems"]


# ------------------------------------------------------------------------------------------- S5: malformed exports
def _titles(raw: bytes, name: str) -> list:
    return [r["title"] for r in parse_bytes(raw, name)[0]]


def test_a_ris_record_with_no_er_line_is_not_merged_into_the_next():
    five = [{"title": f"Triage study number {i}: a cohort", "year": "2020", "authors": [f"Smith{i}, J"]} for i in range(1, 6)]
    text = ris(five).decode()
    gap = text.replace("ER  - \n\nTY  - JOUR\nTI  - Triage study number 3", "TY  - JOUR\nTI  - Triage study number 3")
    assert len(_titles(gap.encode(), "x.ris")) == 5
    none = "\n".join(ln for ln in text.splitlines() if ln != "ER  - ") + "\nER  - \n"
    assert len(_titles(none.encode(), "x.ris")) == 5
    wrapped = text.replace("PY  - 2020\n", "PY  - 2020\nAB  - Background: a long abstract\nER  - the abbreviation of the "
                           "emergency room\nand more of it.\n", 1)
    recs = parse_bytes(wrapped.encode(), "x.ris")[0]
    assert len(recs) == 5 and recs[0]["abstract"].endswith("emergency room and more of it.")


def test_a_pubmed_record_with_no_blank_line_after_it_is_not_merged_into_the_next():
    recs = "".join(f"PMID- 3456789{i}\nTI  - Triage study {i}\nAU  - Smith J\nDP  - 2020\n\n" for i in range(1, 6))
    assert [r["pmid"] for r in parse_bytes(recs.replace("\n\n", "\n").encode(), "x.nbib")[0]] == \
        [f"3456789{i}" for i in range(1, 6)]
    joined = recs.encode() + "﻿".encode() + "PMID- 34567896\nTI  - Triage study 6\n\n".encode()
    assert parse_bytes(joined, "x.nbib")[0][-1]["pmid"] == "34567896"


def test_a_web_of_science_record_with_no_er_line_is_not_merged_or_lost():
    one = "PT J\nAU Smith, J\nTI Triage study {}\nPY 2020\n"
    text = "FN Clarivate Analytics Web of Science\nVR 1.0\n" + one.format(1) + one.format(2) + "ER\n\n" + one.format(3) + "ER\n\n" \
        + one.format(4)
    assert _titles(text.encode(), "x.txt") == [f"Triage study {i}" for i in range(1, 5)]


def test_a_cochrane_abstract_line_starting_with_capitals_and_a_colon_stays_in_the_abstract():
    text = "Record #1 of 1\nID: CN-01\nTI: Triage\nAB: Background: triage.\nAIM: To test the tool.\nYR: 2020\n"
    r = parse_bytes(text.encode(), "x.txt")[0][0]
    assert r["abstract"] == "Background: triage. AIM: To test the tool." and r["year"] == "2020"


def test_a_tab_file_with_an_unclosed_quote_loses_no_rows():
    rows = [f"Study {i} of triage\tSmith{i} J\t2020" for i in range(1, 10)]
    rows[3] = '"Smart triage: a trial\tJones K\t2021'
    titles = _titles(("Title\tAuthors\tYear\n" + "\n".join(rows) + "\n").encode(), "x.txt")
    assert len(titles) == 9 and titles[3] == '"Smart triage: a trial' and titles[4] == "Study 5 of triage"


def test_a_csv_with_an_unclosed_quote_it_cannot_place_is_refused_with_its_line():
    text = "Title,Authors,Year\nStudy 1,Smith J,2020\n\"Smart triage, a trial,Jones K,2021\nStudy 3,Brown T,2022\n"
    with pytest.raises(FormatError, match="line 3 of this file"):
        parse_bytes(text.encode(), "x.csv")


def test_properly_quoted_cells_over_several_lines_are_read_as_before():
    text = 'Title,Abstract,Year\n"A, title","Line one.\n""Quoted"" line two, with a comma.",2020\nB,plain,2021\n'
    recs = parse_bytes(text.encode(), "x.csv")[0]
    assert [r["title"] for r in recs] == ["A, title", "B"] and recs[0]["abstract"] == 'Line one. "Quoted" line two, with a comma.'


def test_a_cell_too_long_and_a_utf16_file_cut_short_get_a_plain_message():
    with pytest.raises(FormatError, match="longer than ReviewDesk can read"):
        parse_bytes(("Title,Abstract\nA title,\"" + "x" * 140000 + "\"\n").encode(), "x.csv")
    with pytest.raises(FormatError, match="UTF-16"):
        parse_bytes("TY  - JOUR\nTI  - Title\nER  - \n".encode("utf-16")[:-1], "x.ris")


# ------------------------------------------------------------------------------------------- M4: impossible dates
@pytest.mark.parametrize("typed", ["2026-00", "2026-13", "2026-02-30", "2026-00-10"])
def test_a_planned_date_that_cannot_be_is_shown_as_typed(typed):
    assert timeline._when(typed) == typed
    assert timeline._when("2026-12") == "December 2026" and timeline._when("2026-02-28") == "28 February 2026"


# ------------------------------------------------------------------------------------------- helpers
_B3 = [
    dict(title="Machine learning triage to predict hospital admission from the emergency department in adults: a prospective "
               "validation", authors=["Lee S."], year="2019", journal="Critical Care", volume="23", pages="P101", doi=SUPPL,
         m3="Conference Abstract"),
    dict(title="Machine learning triage to predict hospital admission from the emergency department in children: a prospective "
               "validation", authors=["Kim H."], year="2019", journal="Critical Care", volume="23", pages="P317", doi=SUPPL,
         m3="Conference Abstract"),
    dict(title="An artificial intelligence triage tool for sepsis in the emergency department", authors=["Garcia M."], year="2019",
         journal="Critical Care", volume="23", pages="P455", doi=SUPPL, m3="Conference Abstract"),
    dict(title="An artificial intelligence triage tool for sepsis in the emergency department: external validation in three "
               "hospitals", authors=["Okafor C."], year="2019", journal="Critical Care", volume="23", pages="P456", doi=SUPPL,
         m3="Conference Abstract"),
    dict(title="Tutorial in biostatistics: triage scores in emergency care. Part I: development", authors=["Altman D."],
         year="1997", journal="Statistics in Medicine", volume="16", issue="16", pages="1791", doi=SICI1),
    dict(title="Tutorial in biostatistics: triage scores in emergency care. Part II: validation", authors=["Royston P."],
         year="1997", journal="Statistics in Medicine", volume="16", issue="16", pages="1805", doi=SICI2),
    dict(title="Interleukin-1α at emergency department triage predicts sepsis mortality", authors=["Wang L."], year="2018",
         journal="Shock", volume="49", pages="101"),
    dict(title="Interleukin-1β at emergency department triage predicts sepsis mortality", authors=["Wang L."], year="2018",
         journal="Shock", volume="50", pages="655"),
]


def _filler(n: int) -> list:
    return [dict(title=f"A cohort study of emergency department presentation number {i} and its outcomes", year="2021",
                 journal=f"Journal {i}", authors=[f"Author{i}, A"], doi=f"10.1000/j.{i}") for i in range(n)]


def _project(tmp_path, monkeypatch) -> Project:
    monkeypatch.setenv("REVIEWDESK_HOME", str(tmp_path / "home"))
    from reviewdesk import robplan, robtools
    p = Project.create(str(tmp_path / "review"), "A review", "Dr Colin Hawthorne", "CH")
    userconfig.set_identity(p.dir, "CH", p.key())
    p.update_settings("CH", {"reviewers": [{"id": "CH", "name": "Dr Colin Hawthorne", "role": "lead"},
                                           {"id": "NH", "name": "Nadine Hayes", "role": "reviewer"}],
                             "question": "Does it work?", "criteria": {"include": ["adults"], "exclude": ["children"], "rules": []},
                             "screening": {"mode": "dual", "reviewers": ["CH", "NH"], "single_reviewer": "NH",
                                           "second_reviewer": "CH", "calibration_size": 0}})
    std = {d: (n, t) for d, n, t in robtools.STANDARD_DESIGNS}
    robplan.save_plan(p, "CH", {"designs": [{"id": "rct", "name": std["rct"][0], "tool": std["rct"][1]}], "tools": {}})
    assert project.Index is dedup.Index
    return p
