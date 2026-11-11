"""Reading database exports: every format the reference review's searches produce, with the quirks real exports have
(byte-order marks, Windows-1252, CRLF line ends, macrons, very long and missing abstracts, fields over several lines),
read to the right fields; and files that cannot be read refused with a plain message."""
from __future__ import annotations

import pytest

import realistic
from helpers import ApiError, reference_project
from reviewdesk import parsers
from reviewdesk.parsers import FormatError, parse_bytes

REVIEW = realistic.Review(2026, "normal")
SCOPUS_RIS = realistic.Review(2026, "normal", scopus_format="ris")


def norm(s: str) -> str:
    return " ".join((s or "").split())


def first_surname(authors: str) -> str:
    a = authors.split(";")[0].strip()
    return a.split(",")[0] if "," in a else a.rsplit(" ", 1)[0]


EXPORTS = [("MEDLINE", "main"), ("Embase", "main"), ("CINAHL", "main"), ("Scopus", "main"), ("CENTRAL", "main"),
           ("Citation searching", "other"), ("NEJM AI", "other"), ("MEDLINE", "update"), ("Embase", "update")]


@pytest.mark.parametrize("database, set_name", EXPORTS)
def test_every_export_of_the_reference_review_reads_record_for_record(database, set_name):
    e = REVIEW.export(database, set_name)
    recs, info = parse_bytes(e.data, e.name)
    assert info["records"] == e.records == len(recs)
    for r, w in zip(recs, e.works()):
        if w is None:                                   # CINAHL's record with nothing in it but its accession number
            assert not (r["title"] or r["abstract"] or r["doi"] or r["pmid"])
            continue
        want = REVIEW._for(w, database)
        assert realistic.tkey(r["title"]) == realistic.tkey(want["title"]), (r["title"], want["title"])
        assert r["year"] == str(want["year"])
        assert r["doi"] == want["doi"].lower()
        if want["abstract"]:
            assert norm(r["abstract"]) == norm(realistic.ascii_fold(want["abstract"]) if database == "Embase" else want["abstract"])
        if want["authors"]:
            assert realistic.tkey(first_surname(r["authors"])) == realistic.tkey(want["authors"][0][0])
        if w.kind == "conference":
            assert "onference" in r["pubtype"]
        if w.kind == "erratum" and database in ("MEDLINE", "Embase"):
            assert "rratum" in r["pubtype"]


def test_medline_from_ovid_is_utf8_with_a_byte_order_mark_and_crlf():
    e = REVIEW.export("MEDLINE")
    assert e.data[:3] == b"\xef\xbb\xbf" and b"\r\n" in e.data
    recs, info = parse_bytes(e.data, e.name)
    assert info["encoding"] == "utf-8" and info["format"] == "RIS"
    assert not recs[0]["title"].startswith("﻿")
    assert all("\r" not in r["title"] + r["abstract"] for r in recs)


def test_maori_macrons_survive_in_utf8_exports():
    e = REVIEW.export("MEDLINE")
    recs, _ = parse_bytes(e.data, e.name)
    text = " ".join(r["title"] + " " + r["abstract"] + " " + r["authors"] for r in recs)
    assert any(word in text for word in ("Whangārei", "Māori", "Te Whāiti", "Rāwiri", "Pōtae", "Hēmi"))
    assert "Ä" not in text and "â€" not in text                   # no mojibake


def test_embase_in_windows_1252_reads_dashes_and_curly_quotes():
    e = REVIEW.export("Embase")
    with pytest.raises(UnicodeDecodeError):
        e.data.decode("utf-8")
    recs, info = parse_bytes(e.data, e.name)
    assert info["encoding"] == "cp1252"
    text = " ".join(r["title"] + r["abstract"] + r["authors"] for r in recs)
    assert "’" in text or "–" in text
    assert "â€" not in text and "�" not in text


def test_a_long_abstract_wrapped_over_many_lines_is_read_whole():
    long = next(w for w in REVIEW.works if len(w.abstract) > 6000)
    for db in ("Embase", "MEDLINE", "Scopus"):
        e = REVIEW.export(db)
        if long.wid in e.wids:
            r = parse_bytes(e.data, e.name)[0][e.wids.index(long.wid)]
            assert len(r["abstract"]) > 6000
            assert norm(r["abstract"]) == norm(realistic.ascii_fold(long.abstract))


def test_scopus_csv_with_line_breaks_inside_an_abstract_and_no_abstract_available():
    e = REVIEW.export("Scopus")
    assert e.data.startswith(b"\xef\xbb\xbf") and b"\nResults:" in e.data
    recs, info = parse_bytes(e.data, e.name)
    assert info["format"] == "CSV" and len(recs) == e.records
    assert all("\n" not in r["abstract"] for r in recs)
    by = dict(zip(e.wids, recs))
    letters = [w for w in e.works() if w and not w.abstract]
    assert letters and all(by[w.wid]["abstract"] == "" for w in letters)     # "[No abstract available]" is not an abstract
    assert all(r["accession"].startswith("2-s2.0-") for r in recs)


def test_scopus_as_ris_reads_too():
    e = SCOPUS_RIS.export("Scopus")
    recs, info = parse_bytes(e.data, e.name)
    assert info["format"] == "RIS" and len(recs) == e.records
    for r, w in zip(recs, e.works()):
        assert r["journal"] == w.journal and r["year"] == str(SCOPUS_RIS._for(w, "Scopus")["year"])


def test_scopus_csv_journal_is_the_source_title():
    e = REVIEW.export("Scopus")
    recs, _ = parse_bytes(e.data, e.name)
    assert [r["journal"] for r in recs] == [REVIEW._for(w, "Scopus")["journal"] for w in e.works()]


def test_scopus_csv_page_range_is_read():
    e = REVIEW.export("Scopus")
    recs, _ = parse_bytes(e.data, e.name)
    w = next(w for w in e.works() if w.pages)
    assert recs[e.wids.index(w.wid)]["pages"] == w.pages


def test_scopus_subscript_markup_is_removed_from_titles():
    row = ('﻿Authors,Title,Year,Source title,DOI,Abstract,Document Type,Source,EID\n'
           '"Ngata, A.",Continuous SpO<inf>2</inf> monitoring at emergency triage,2025,Emergency Medicine Journal,,'
           'Background: x,Article,Scopus,2-s2.0-85000000001\n')
    r = parse_bytes(row.encode("utf-8"), "scopus.csv")[0][0]
    assert r["title"] == "Continuous SpO2 monitoring at emergency triage"


def test_cinahl_entities_and_accession_numbers():
    e = REVIEW.export("CINAHL")
    assert b"&amp;" in e.data or not any("&" in w.title for w in e.works() if w)
    recs, _ = parse_bytes(e.data, e.name)
    assert all("&amp;" not in r["title"] for r in recs)
    assert all(r["accession"].isdigit() for r in recs if r["title"])
    by = dict(zip(e.wids, recs))                          # EBSCO writes the PubMed ID as "PMID: NLM12345678"
    assert all(by[w.wid]["pmid"] == (w.pmid or "") for w in e.works() if w)
    assert any(r["pmid"] for r in recs)


def test_ovid_translated_titles_lose_their_brackets():
    e = REVIEW.export("Embase")
    w = next(w for w in e.works() if w and w.language != "English")
    assert f"[{realistic.ascii_fold(w.title)}]".encode("cp1252") in e.data
    r = parse_bytes(e.data, e.name)[0][e.wids.index(w.wid)]
    assert r["title"] == realistic.ascii_fold(w.title) and r["language"] == "Spanish"


def test_ovid_dois_given_as_links_are_read_as_dois():
    e = REVIEW.export("MEDLINE")
    assert b"DO  - https://dx.doi.org/10." in e.data
    recs, _ = parse_bytes(e.data, e.name)
    assert all(r["doi"].startswith("10.") for r in recs if r["doi"])


def test_cochrane_central_plain_text():
    e = REVIEW.export("CENTRAL")
    assert e.data.startswith(b"Record #1 of ")
    recs, info = parse_bytes(e.data, e.name)
    assert info["format"] == "Cochrane Library plain text"
    for r, w in zip(recs, e.works()):
        assert not r["doi"].startswith("10.1002/central")       # the record's own address is not the article's DOI
        assert r["accession"].startswith("CN-")
        if w.pmid and w.kind != "registry":
            assert r["pmid"] == w.pmid
    reg = [r for r, w in zip(recs, e.works()) if w.kind == "registry"]
    assert all(r["pubtype"] == "Trial registry record" for r in reg)


def test_pubmed_nbib_joins_wrapped_lines():
    e = REVIEW.export("Citation searching", "other")
    assert b"\n      " in e.data
    recs, info = parse_bytes(e.data, e.name)
    assert info["format"] == "PubMed (MEDLINE format)"
    for r, w in zip(recs, e.works()):
        assert norm(r["title"]).rstrip(".") == norm(w.title).rstrip(".")
        assert r["pmid"].isdigit() and r["url"] == f"https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}/"
        assert r["language"] in ("eng", "spa")


def test_utf16_from_a_windows_program_is_read():
    text = "TY  - JOUR\r\nTI  - Triage in Whangārei\r\nPY  - 2024\r\nER  - \r\n"
    for raw in (text.encode("utf-16"), text.encode("utf-16-le")):
        recs, info = parse_bytes(raw, "export.ris")
        assert recs[0]["title"] == "Triage in Whangārei" and "utf-16" in info["encoding"]


def test_one_stray_windows_byte_does_not_garble_a_utf8_file():
    lines = [f"TY  - JOUR\nTI  - Māori health and triage {i}\nAB  - Pain score {i}\nPY  - 2024\nER  - \n" for i in range(30)]
    raw = "".join(lines).encode("utf-8").replace(b"Pain score 7", "Pain score 7 – 8".encode("cp1252"))   # one pasted dash
    recs, info = parse_bytes(raw, "x.ris")
    assert info["encoding"].startswith("utf-8 (1 line")
    assert [r["title"] for r in recs] == [f"Māori health and triage {i}" for i in range(30)]
    assert recs[7]["abstract"] == "Pain score 7 – 8"


def test_old_mac_line_ends_are_read():
    raw = b"TY  - JOUR\rTI  - A title\rPY  - 2023\rER  - \r"
    assert parse_bytes(raw, "x.ris")[0][0]["title"] == "A title"


def test_a_csv_from_a_trial_register_reads_its_own_column_names():
    raw = ('NCT Number,Study Title,Study URL,Brief Summary,Study Status\n'
           'NCT05123456,AI-supported triage in the emergency department,https://clinicaltrials.gov/study/NCT05123456,'
           '"A trial of machine learning triage, with ""quotes"".",Recruiting\n').encode("utf-8")
    r = parse_bytes(raw, "ctg-studies.csv")[0][0]
    assert r["title"] == "AI-supported triage in the emergency department" and r["accession"] == "NCT05123456"
    assert r["abstract"] == 'A trial of machine learning triage, with "quotes".' and r["url"].startswith("https://clinicaltrials.gov/")


def test_a_semicolon_csv_saved_by_excel_is_read():
    raw = "Title;Authors;Year;Journal;DOI\nTriage and AI;Ngata, A.;2024;EMJ;10.1136/abc\n".encode("cp1252")
    r = parse_bytes(raw, "list.csv")[0][0]
    assert (r["title"], r["year"], r["doi"]) == ("Triage and AI", "2024", "10.1136/abc")


def test_url_encoded_dois_are_decoded():
    assert parsers.clean_doi("https://doi.org/10.1016/S2589-7500%2824%2900123-4") == "10.1016/s2589-7500(24)00123-4"
    assert parsers.clean_doi("doi:10.1136/emermed-2023-213456.") == "10.1136/emermed-2023-213456"


# ------------------------------------------------------------------------------------------------ files that cannot be read
@pytest.mark.parametrize("raw, words", [
    (b"", "This file is empty"),
    (b"   \r\n\t ", "This file is empty"),
    (b"\xef\xbb\xbf\r\n", "has no text in it"),
    (b"TY  - JOUR\nTI  - A title\nAB  - cut off in the mid", "cut short"),
    (b"PK\x03\x04\x14\x00\x06\x00\x08\x00\x00\x00!\x00" + bytes(range(256)) * 4, "not in a format ReviewDesk can read"),
    (b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj << >> endobj", "not in a format ReviewDesk can read"),
    (b"Record #1 of 1\nnothing here\n", "holds no records"),
])
def test_broken_files_give_a_plain_message(raw, words):
    with pytest.raises(FormatError) as e:
        parse_bytes(raw, "export.ris" if not raw.startswith(b"PK") else "export.xlsx")
    assert words in str(e.value)


def test_broken_files_are_refused_through_the_api_and_nothing_is_saved(desk, tmp_path):
    c = desk.client()
    folder = reference_project(c, str(tmp_path / "OneDrive"))
    with pytest.raises(ApiError) as x:
        c.add_import(b"", "x.ris", "MEDLINE")
    assert x.value.status == 400 and "This file is empty" in x.value.message
    try:
        c.add_import(b"TY  - JOUR\nTI  - A title\n", "medline.ris", "MEDLINE")
        raise AssertionError("not refused")
    except ApiError as e:
        assert e.status == 400 and "cut short" in e.message and "Export it from the database again" in e.message
    csv_no_title = ("Name,Year\n" + "".join(f"Thing {i},2024\n" for i in range(5))).encode()
    try:
        c.add_import(csv_no_title, "scopus.csv", "Scopus")
        raise AssertionError("not refused")
    except ApiError as e:
        assert "have no title" in e.message and "column called Title" in e.message
    assert c.get("/api/imports")["imports"] == []
    import os
    assert [f for f in os.listdir(os.path.join(folder, "imports")) if not f.startswith(".")] == []


def test_the_same_file_twice_and_a_missing_database_are_refused(desk, tmp_path):
    c = desk.client()
    reference_project(c, str(tmp_path / "OneDrive"))
    e = REVIEW.export("CENTRAL")
    c.add_import(e.data, e.name, "CENTRAL")
    with pytest.raises(ApiError) as x:
        c.add_import(e.data, "copy of " + e.name, "CENTRAL")
    assert "already been added" in x.value.message
    with pytest.raises(ApiError) as x:
        c.add_import(REVIEW.export("CINAHL").data, "cinahl.ris", " ")
    assert "Say which database" in x.value.message
    with pytest.raises(ApiError) as x:
        c.add_import(REVIEW.export("CINAHL").data, "cinahl.ris", "CINAHL", set_name="")
    assert "Say which search this file comes from" in x.value.message
