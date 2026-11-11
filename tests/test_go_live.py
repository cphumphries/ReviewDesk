"""Defects found by the final audit before 1.3.0 was published, each with the test that would have caught it."""
import os

import pytest

from reviewdesk import integrity, prisma, screening, userconfig, util
from reviewdesk.project import Project, ProjectError


def ris(recs: list) -> bytes:
    out = []
    for r in recs:
        out.append("TY  - JOUR")
        for tag, key in (("TI", "title"), ("AB", "abstract"), ("PY", "year"), ("JF", "journal"), ("DO", "doi"), ("AN", "an"),
                         ("DB", "db"), ("N1", "n1")):
            if r.get(key):
                out.append(f"{tag}  - {r[key]}")
        out += ["AU  - " + a for a in r.get("authors", [])] + ["ER  - ", ""]
    return "\n".join(out).encode("utf-8")


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWDESK_HOME", str(tmp_path / "home"))
    from reviewdesk import robplan, robtools
    p = Project.create(str(tmp_path / "review"), "A review", "Dr Colin Hawthorne", "CH")
    userconfig.set_identity(p.dir, "CH", p.key())
    p.update_settings("CH", {"reviewers": [{"id": "CH", "name": "Dr Colin Hawthorne", "role": "lead"},
                                           {"id": "NH", "name": "Nadine Hayes", "role": "reviewer"}],
                             "question": "Does it work?", "criteria": {"include": ["adults"], "exclude": ["children"], "rules": []},
                             "screening": {"mode": "dual", "reviewers": ["CH", "NH"], "single_reviewer": "NH", "second_reviewer": "CH",
                                           "calibration_size": 0}})
    std = {d: (n, t) for d, n, t in robtools.STANDARD_DESIGNS}
    robplan.save_plan(p, "CH", {"designs": [{"id": "rct", "name": std["rct"][0], "tool": std["rct"][1]}], "tools": {}})
    return p


def filler(n: int) -> list:
    return [{"title": f"A cohort study of emergency department presentation number {i} and its outcomes at one year",
             "abstract": f"Abstract {i}.", "year": "2021", "journal": f"Journal {i % 5}", "authors": [f"Author{i}, A"],
             "doi": f"10.1000/j.{i}"} for i in range(n)]


def test_removing_duplicates_again_never_takes_a_screened_record_out_of_the_review(proj):
    """The same paper from three databases: MEDLINE (PubMed ID, full title), Embase (DOI, title without its subtitle)
    and CENTRAL (both identifiers). The first run keeps the Embase copy apart (it shares nothing with MEDLINE's) and
    merges CENTRAL's into MEDLINE's, which gains the DOI. Removing duplicates again (as the update search needs) once
    merged the Embase record away then, although both reviewers had screened it: its ID stayed in the screening
    order with its votes, nobody could assess it at full text, and the PRISMA numbers no longer added up."""
    p = proj
    title = "Machine learning triage in the emergency department"
    p.add_import("CH", "medline.ris", ris([{"title": title + ": a pragmatic randomised controlled trial", "year": "2021",
                                            "journal": "Emerg Med J", "authors": ["Smith, J"], "an": "34000001",
                                            "db": "Ovid MEDLINE(R)", "abstract": "A."}] + filler(8)), {"set": "main", "database": "MEDLINE"})
    p.add_import("CH", "embase.ris", ris([{"title": title, "year": "2021", "journal": "Emergency Medicine Journal", "authors": ["Smith J."],
                                           "doi": "10.1136/emermed-2021-000001", "abstract": "A longer abstract."}]),
                 {"set": "main", "database": "Embase"})
    p.add_import("CH", "central.ris", ris([{"title": title + ": a pragmatic randomised controlled trial", "year": "2021",
                                            "journal": "Emerg Med J", "authors": ["Smith J"], "doi": "10.1136/emermed-2021-000001",
                                            "n1": "PMID: 34000001", "abstract": "A."}]), {"set": "main", "database": "CENTRAL"})
    assert p.process_imports("CH")["records"] == 10
    apart = next(r["id"] for r in p.records() if r["title"] == title)
    seen = {r["id"]: dict(r) for r in p.records()}
    screening.start(p, "CH", "main")
    for rid in p.state()["sets"]["main"]["order"]:
        for who in ("CH", "NH"):
            p.add_decision(who, rid, "ta", "include" if rid == apart else "exclude")
    p.add_import("CH", "update.ris", ris([{"title": "A new study of nurse-led triage in rural emergency departments", "year": "2022",
                                           "journal": "Rural Health", "authors": ["Nguyen, T"], "doi": "10.1000/new.1"}]),
                 {"set": "update", "database": "MEDLINE"})
    assert p.process_imports("CH")["records"] == 11
    now = {r["id"]: r for r in p.records()}
    assert set(seen) <= set(now) and apart in now
    for rid, r in seen.items():                                     # and each still reads exactly as it was screened
        assert integrity.record_print(now[rid]) == integrity.record_print(r)
    c = prisma.counts(p)
    assert c["db"]["records"] == 11 and c["db"]["screened"] == 10 and c["db"]["pending_ta"] == 1    # (the update record)
    assert c["checks"] == []
    assert not [x for x in p.integrity_report()["problems"]]
    p.add_decision("NH", apart, "ft", "include")                    # it can still be assessed
    assert p.process_imports("CH")["records"] == 11                 # and a third run changes nothing either
    assert apart in {r["id"] for r in p.records()}


@pytest.mark.parametrize("note", ["Wrong population (children only)", "a b", "a\u0085b",
                                  pytest.param("x" * 70000, id="70,000 letters"), "plain"])
def test_a_row_ends_only_at_a_line_feed(tmp_path, monkeypatch, note):
    """Text pasted into a note can hold U+2028, U+2029 or U+0085, which some tools treat as line ends; the row after
    such a note was once chained to the wrong row, and the file reported for ever as changed outside ReviewDesk. The
    same happened after any row longer than 64 KiB."""
    monkeypatch.setenv("REVIEWDESK_HOME", str(tmp_path / "home"))
    f = str(tmp_path / "rows.jsonl")
    for k, text in enumerate(["first", note, "after it", "last"]):
        integrity.append(f, {"t": util.now(), "n": k, "note": text})
    rows = util.read_jsonl(f)
    assert len(rows) == 4 and integrity.verify(rows) == []
    assert integrity.last_hash(f) == rows[-1][integrity.H]


# ================================================================================================ security
@pytest.mark.parametrize("url, ok", [
    ("https://service.example.org/v1", True), ("HTTPS://Service.Example.org/v1", True), ("http://127.0.0.1:8000/v1", True),
    ("http://localhost/v1", True),
    ("http://service.example.org/v1", False),                         # the key would go unencrypted
    ("http://localhost:1@service.example.org/v1", False),             # reads as this computer; is service.example.org
    ("https://user:pw@service.example.org/v1", False), ("http://127.0.0.1@service.example.org/", False),
    ("ftp://service.example.org/v1", False), ("https://exa mple.org", False), ("https://example.org\\@evil.example/v1", False),
    ("http://[::1]:8000/v1", False), ("//service.example.org", False), ("service.example.org", False), ("", False)])
def test_the_ai_key_goes_only_to_an_https_address_or_this_computer(url, ok):
    """The address is judged by the host a request to it would really reach, never by how the text begins."""
    from reviewdesk import ai
    assert (ai.address_problem(url) == "") is ok


def test_an_address_that_only_begins_like_elm_is_not_elm():
    from reviewdesk import ai
    assert ai.is_elm(ai.ELM_URL) and ai.is_elm(ai.ELM_URL.rstrip("/") + "/")
    host = ai.ELM_URL.split("/")[2]
    assert not ai.is_elm(f"https://{host}.example.org/api/v1") and not ai.is_elm(f"http://{host}/api/v1")


def test_the_key_is_not_sent_to_an_address_the_check_refuses_even_if_it_is_in_the_settings():
    from reviewdesk import ai
    with pytest.raises(ai.SetupError):
        ai._request("http://localhost:1@service.example.org/v1", "a-key", "/models")


class _Drip:
    """A reply that arrives a little at a time and never ends."""
    def __init__(self, piece: bytes, wait: float = 0.0):
        self.piece, self.wait = piece, wait

    def read1(self, n: int) -> bytes:
        import time
        time.sleep(self.wait)
        return self.piece


def test_a_reply_is_read_only_up_to_a_size_and_a_time():
    with pytest.raises(util.ReplyTooBig, match="larger than 1 MB"):
        util.read_reply(_Drip(b"x" * 65536), 1024 * 1024, 30)
    with pytest.raises(util.ReplyTooBig, match="did not arrive within the time allowed"):
        util.read_reply(_Drip(b"x", wait=0.3), 1024 * 1024, 1)
    import io
    assert util.read_reply(io.BytesIO(b'{"ok": true}'), 1024, 5) == b'{"ok": true}'


def test_a_field_can_never_start_a_new_line_in_a_ris_file(proj):
    """A study name is typed by a reviewer; with a line break in it, it once wrote lines of its own into the RIS file
    of included studies, where a reference manager would read them as another record."""
    from reviewdesk import exports, fulltext
    p = proj
    p.add_import("CH", "one.ris", ris(filler(3)), {"set": "main", "database": "MEDLINE"})
    p.process_imports("CH")
    screening.start(p, "CH", "main")
    rid = p.state()["sets"]["main"]["order"][0]
    for who in ("CH", "NH"):
        p.add_decision(who, rid, "ta", "include")
        p.add_decision(who, rid, "ft", "include", study="Smith 2021\nER  - \nTY  - JOUR\nTI  - Not a real record")
    out = exports.included_ris(p).decode("utf-8")
    assert out.count("TY  - ") == 1 and out.count("ER  - ") == 1
    assert "study: Smith 2021 ER - TY - JOUR TI - Not a real record" in out
    assert fulltext.missing_ris(p, [rid]).decode("utf-8").count("TY  - ") == 1


def _raw(port: int, text: bytes) -> bytes:
    import socket
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(text)
        out = b""
        while True:
            part = s.recv(65536)
            if not part:
                return out
            out += part


def test_requests_the_server_cannot_read_get_a_plain_answer_with_the_usual_headers(desk):
    port, tok = desk.port, desk.token
    host = f"Host: 127.0.0.1:{port}\r\n".encode()
    for request in (b"PUT /api/app HTTP/1.1\r\n" + host + b"\r\n",                       # a method ReviewDesk does not use
                    b"GET /" + b"a" * 70000 + b" HTTP/1.1\r\n" + host + b"\r\n",          # an address too long to read
                    b"POST /api/user HTTP/1.1\r\n" + host + b"X-Token: " + tok.encode() + b"\r\nContent-Length: abc\r\n\r\n",
                    b"POST /api/user HTTP/1.1\r\n" + host + b"X-Token: " + tok.encode() + b"\r\nContent-Length: -5\r\n\r\n"):
        answer = _raw(port, request)
        head = answer.split(b"\r\n\r\n")[0].decode("latin-1")
        assert head.split()[1] in ("400", "414", "501"), head[:80]
        assert "Traceback" not in answer.decode("latin-1") and "ValueError" not in answer.decode("latin-1")
        for must in ("X-Content-Type-Options: nosniff", "Cache-Control: no-store", "Content-Security-Policy: default-src 'none'"):
            assert must in head, (must, head)


@pytest.mark.parametrize("host", ["127.0.0.1:{port}@evil.example", "localhost, evil.example", "evil.example",
                                  "127.0.0.1.evil.example", "localhost:{port}:1", "127.0.0.1:{port}/x", ""])
def test_only_a_request_addressed_to_this_computer_and_nothing_else_is_answered(desk, host):
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", desk.port, timeout=10)
    c.putrequest("GET", "/api/app", skip_host=True)
    c.putheader("Host", host.format(port=desk.port))
    c.putheader("X-Token", desk.token)
    c.endheaders()
    r = c.getresponse()
    r.read()
    c.close()
    assert r.status in (400, 403)


SLOW_PDFS = [                 # (named, so that a test's name is not the megabyte itself: pytest keeps the name of the
    ("ten thousand unfinished text objects", b"%PDF-1.4\n" + b"BT " * 10000),     # test it is running in an environment
    ("a string that never ends", b"%PDF-1.4\nBT " + b"(" * 300000 + b" ET"),      # variable, and Windows refuses one
    ("dictionaries that never end", b"%PDF-1.4\nBT " + b"<< " * 200000 + b" ET"), # longer than 32,767 characters)
    ("titles that never end", b"%PDF-1.4\n" + b"<dc:title>" * 100000),
    ("page trees without end", b"%PDF-1.4\n" + b"/Type /Pages " * 100000)]


@pytest.mark.parametrize("name, raw", SLOW_PDFS, ids=[name for name, _ in SLOW_PDFS])
def test_a_pdf_made_to_be_slow_is_read_in_seconds(name, raw):
    """Each of these once took time that grew with the square of the file's size (8 seconds for 30 KB, minutes for a
    megabyte), and the whole of ReviewDesk waited while one was read."""
    import time
    from reviewdesk import fulltext
    t = time.time()
    fulltext.pdf_identifiers(raw)
    assert time.time() - t < 30, name


def test_a_pdf_is_never_unpacked_beyond_a_fixed_size():
    import zlib
    from reviewdesk import fulltext
    raw = b"%PDF-1.4\n" + b"".join(b"stream\n" + zlib.compress(b"A" * 4_000_000) + b"\nendstream\n" for _ in range(40))
    assert len(raw) < 400_000
    assert len(fulltext.pdf_text_bits(raw)) <= fulltext.MAX_PDF_TEXT + len(raw) + 4000


def test_strings_in_page_text_are_read_as_before():
    from reviewdesk import fulltext
    page = (r"BT /F1 12 Tf (A randomised trial \(pilot\) of) Tj [(tri) -20 (age)] TJ <48656C6C6F> Tj "
            r"/Span << /ActualText (hidden) >> BDC (doi:10.1000/abc.123) Tj EMC (nested (brackets) here) Tj ET")
    assert fulltext._pdf_strings(page) == "A randomised trial (pilot) of triage Hello doi:10.1000/abc.123 nested (brackets) here"


# ---------------------------------------------------------------- a file already in use is never refused afterwards
def test_a_csv_file_already_in_use_is_still_read_when_its_quotation_marks_do_not_pair_up(tmp_path, monkeypatch):
    """A CSV file with a quotation mark that is never closed is refused when it is added now. But a project may have
    read such a file with an earlier ReviewDesk (rows after the mark were lost without a word), and a file cannot be
    taken out once screening has started: refusing it then would stop Remove duplicates, and with it the update
    search, for good. It is read as it was read then, and a note says which line to look at."""
    import csv as _csv
    from reviewdesk import parsers
    from reviewdesk.parsers import FormatError, parse_bytes
    text = ("Title,Authors,Year\nStudy 1 of triage,Smith J,2020\n\"Smart triage, a trial,Jones K,2021\n"
            "Study 3 of triage,Brown T,2022\n")
    with pytest.raises(FormatError, match="line 3 of this file"):
        parse_bytes(text.encode(), "x.csv")
    recs, info = parse_bytes(text.encode(), "x.csv", already_read=True)
    assert info["open_quote_line"] == 3 and [r["title"] for r in recs][0] == "Study 1 of triage"
    # a well-formed file is read the same way whether or not it was read before, and carries no note
    good = "Title,Authors,Year\nStudy 1 of triage,Smith J,2020\nStudy 2 of triage,Jones K,2021\n"
    a, ia = parse_bytes(good.encode(), "x.csv")
    b, ib = parse_bytes(good.encode(), "x.csv", already_read=True)
    assert a == b and "open_quote_line" not in ia and "open_quote_line" not in ib

    # in a project: the file was added and used when ReviewDesk read it without the check
    p = Project.create(str(tmp_path / "proj"), "A review", "Colin Hawthorne", "CH")
    strict = parsers._csv_rows
    monkeypatch.setattr(parsers, "_csv_rows", lambda lines, delim: list(_csv.reader(lines, delimiter=delim)))
    p.add_import("CH", "x.csv", text.encode(), {"set": "main", "database": "Scopus", "platform": "", "search_date": "2026-11-01",
                                                "search_id": "", "notes": "", "part": ""})
    n = p.process_imports("CH")["records"]
    assert n >= 1
    monkeypatch.setattr(parsers, "_csv_rows", strict)
    p = Project(str(tmp_path / "proj"))                               # ReviewDesk as it is now
    again = p.process_imports("CH")
    assert again["records"] == n, "the file is read as it was read then: Remove duplicates still works, same records"
    notes = " ".join(str(x) for x in p.repairs()) + " ".join(str(x) for x in p.sync_warnings())
    assert "line 3 of this file is opened and never closed" in notes and "x.csv" in notes
    # ... while the same file added today is refused before anything is saved
    with pytest.raises((FormatError, ProjectError)):
        p.add_import("CH", "y.csv", text.replace("Study 1", "Study 9").encode(),
                     {"set": "main", "database": "Embase", "platform": "", "search_date": "2026-11-01", "search_id": "",
                      "notes": "", "part": ""})


# ---------------------------------------------------------------- the reports quote the duplicate rule that was used
def test_the_reports_give_the_duplicate_rule_that_was_recorded_not_simply_todays(tmp_path):
    """The rule for duplicates can change between versions of ReviewDesk. The methods text and the search appendix give
    the rule 'word for word': that must be the rule recorded when the duplicates were removed, with the version that
    used it, and when the rule changed after screening started (records being screened keep their merges) both rules."""
    import json as _json
    import demo_review
    from reviewdesk import VERSION, exports, reporting
    from reviewdesk.dedup import RULE_TEXT
    from reviewdesk.project import rule_history
    info = demo_review.build(str(tmp_path / "demo"))
    p = info["p"]
    dd = p.dedup_report()
    assert dd["version"] == VERSION and [h["rule"] for h in dd["rules"]] == [RULE_TEXT]
    r = exports.dedup_rules(p)
    assert r["latest"] == RULE_TEXT and r["version"] == VERSION and r["earlier"] == [] and r["earlier_note"] == ""

    # as a project whose duplicates were removed by an earlier version: its record holds that version's rule, no version
    path = p.path("state", "dedup.json")
    old = _json.load(open(path, encoding="utf-8"))
    old.pop("rules"); old.pop("version"); old["rule"] = "The rule of an earlier version: records matched on title alone."
    _json.dump(old, open(path, "w", encoding="utf-8"))
    q = Project(info["folder"])
    r = exports.dedup_rules(q)
    assert r["latest"].startswith("The rule of an earlier version") and r["version"] == "" and not r["earlier"]
    summary = exports.methods_summary(q)
    assert "the rule of an earlier version: records matched on title alone." in summary
    assert RULE_TEXT[:60].lower() not in summary.lower(), "today's rule was not the one used: it must not be printed as if it were"

    # Remove duplicates again with this version, screening having started: both rules are kept, in order
    q.process_imports("CH")
    q = Project(info["folder"])
    hist = q.dedup_report()["rules"]
    assert [h["rule"][:30] for h in hist] == ["The rule of an earlier version", RULE_TEXT[:30]] and hist[1]["version"] == VERSION
    r = exports.dedup_rules(q)
    assert r["latest"] == RULE_TEXT and [e["rule"][:30] for e in r["earlier"]] == ["The rule of an earlier version"]
    assert "records already being screened kept the merges made under the earlier rule" in r["earlier_note"]
    assert r["earlier_note"] in exports.methods_summary(q)
    import io as _io, re as _re, zipfile as _zip
    xml = _zip.ZipFile(_io.BytesIO(reporting.search_docx(q, "CH"))).read("word/document.xml").decode("utf-8")
    words = _re.sub(r"<[^>]+>", "", xml)
    assert f"Duplicates were removed in ReviewDesk {VERSION}, which reads the files" in words
    assert "An earlier rule was used until " in words and "with an earlier version of ReviewDesk" in words
    assert "That rule, word for word: The rule of an earlier version: records matched on title alone." in words

    # and the history itself: nothing recorded yet gives today's rule; the same rule again adds nothing
    assert [h["rule"] for h in rule_history({}, "2026-11-10T10:00:00+00:00")] == [RULE_TEXT]
    assert rule_history({"rules": hist}, "2026-12-01T10:00:00+00:00") == hist


def test_a_file_that_vanishes_between_the_look_and_the_read_is_simply_not_there(tmp_path, monkeypatch):
    """A sync client replaces files by removing and renaming: one can be there when looked for and gone when opened."""
    import os
    f = str(tmp_path / "x.json")
    open(f, "w").write("{}")
    real = os.path.isfile
    monkeypatch.setattr(os.path, "isfile", lambda p: True if p.endswith(("gone.json", "gone.jsonl")) else real(p))
    assert util.read_json(str(tmp_path / "gone.json"), {"d": 1}) == {"d": 1}
    assert util.read_jsonl(str(tmp_path / "gone.jsonl")) == []
    assert util.read_json(f, None) == {}


def test_a_pdf_put_in_the_folder_by_hand_with_a_capital_extension_is_reported(tmp_path):
    """R00001.PDF is not the record's PDF (that is R00001.pdf exactly), so it must be listed as a file ReviewDesk does
    not use, not passed over because its name begins with a record's ID."""
    import demo_review
    info = demo_review.build(str(tmp_path / "demo"))
    p = info["p"]
    rid = next(r["id"] for r in p.records() if not os.path.isfile(p.path("fulltexts", r["id"] + ".pdf")))
    with open(p.path("fulltexts", rid + ".PDF"), "wb") as f:
        f.write(b"%PDF-1.4\n% put here by hand\n")
    p._cache.clear(); p._memo.clear()
    stray = [x for x in p.integrity_report()["problems"] if x["kind"] == "pdf_stray"]
    assert stray and rid + ".PDF" in stray[0]["names"]


def test_the_methods_text_leaves_no_source_cited_by_author_and_year_without_its_reference(tmp_path):
    """The methods text names its methods' sources by author and year. The references ReviewDesk holds word for word
    are printed in full; the others are a highlighted line for the authors, so none is left hanging."""
    import demo_review
    from reviewdesk import certainty, reporting, stats
    info = demo_review.build(str(tmp_path / "demo"))
    demo_review.grade(info)
    p = info["p"]
    txt = reporting.methods_txt(p, "CH").decode("utf-8")
    assert "REFERENCES FOR THE METHODS NAMED ABOVE" in txt
    if "Santesso et al." in txt.split("REFERENCES FOR THE METHODS NAMED ABOVE")[0]:
        assert certainty.GRADE_REFS in txt
    body = txt.split("REFERENCES FOR THE METHODS NAMED ABOVE")[0]
    if "Fleiss, Cohen and Everitt" in body:
        assert "[Authors to complete: add to your reference list" in txt and "Fleiss, Cohen and Everitt (1969)" in txt.split("REFERENCES")[1]
    if "Donner and Eliasziw" in body:
        assert stats.GOF_REF in txt
    full, fill = reporting.methods_references([("H", ["Nothing is cited here.", ("fill", "[x]"), ("bullets", ["a", "b"])])])
    assert full == [] and fill == ""
    full, fill = reporting.methods_references([("H", ["the standard error of Fleiss, Cohen and Everitt (1969)", "as Zeng et al. (2022) say"])])
    assert full == [] and fill == ("[Authors to complete: add to your reference list the sources cited above by author and "
                                   "year: Fleiss, Cohen and Everitt (1969); Zeng et al. (2022), GRADE guidance 34.]")


def test_a_pdf_that_repeats_an_unclosed_doi_field_thousands_of_times_is_read_quickly():
    """The look for a DOI in a PDF's own details used open-ended patterns: "/doi (" 20,000 times, never closed, took
    time that grew with the square of the size and stalled every page while PDFs were being added."""
    import time
    from reviewdesk import fulltext
    for piece in (b"/doi (", b"pdfx:doi ", b"<prism:doi>   ", b"/doi (10.1000/x "):
        raw = b"%PDF-1.4\n" + piece * 40000 + b"\n%%EOF\n"
        t0 = time.monotonic()
        got = fulltext.pdf_identifiers(raw)
        assert time.monotonic() - t0 < 5, f"{piece!r}: {time.monotonic() - t0:.1f} s"
        assert isinstance(got, dict)
    # and an ordinary DOI in the details is still found
    got = fulltext.pdf_identifiers(b"%PDF-1.4\n1 0 obj << /doi (10.1234/abc.2026.001) >> endobj\n%%EOF\n")
    assert got["meta_doi"] == "10.1234/abc.2026.001"
    got = fulltext.pdf_identifiers(b"%PDF-1.4\n<x:xmpmeta><prism:doi>10.1234/abc.2026.002</prism:doi></x:xmpmeta>\n%%EOF\n")
    assert got["meta_doi"] == "10.1234/abc.2026.002"


def test_the_search_log_counts_one_record_as_one_record():
    from reviewdesk import readiness
    one = readiness.search_mismatch({"results": "1"}, [{"parse": {"records": 3}}])
    assert one.startswith("The search found 1 record but the files linked to it hold 3.")
    many = readiness.search_mismatch({"results": "1,204"}, [{"parse": {"records": 1000}}])
    assert many.startswith("The search found 1,204 records but the files linked to it hold 1,000. A part of the export")


def test_a_copy_of_someones_answers_made_by_hand_is_not_read_as_another_person(tmp_path):
    """"Copy of CH@….jsonl" (what Google Drive calls a copy made in its web page) in extraction/, rob/ or certainty/ was
    read as the work of a person called "Copy of CH". It is nobody's file, as it already was among the votes."""
    import shutil
    import demo_review
    from reviewdesk import review
    info = demo_review.build(str(tmp_path / "demo"))
    demo_review.grade(info)
    p = info["p"]
    before = (review.answers(p), review.rob_rows(p)["j"])
    people = {r["id"] for r in p.reviewers()}
    made = 0
    for sub in ("extraction", "rob", "certainty", "decisions"):
        for n in sorted(os.listdir(p.path(sub))):
            if n.split("@")[0] in people and n.endswith(".jsonl"):
                shutil.copyfile(p.path(sub, n), p.path(sub, "Copy of " + n))
                made += 1
    assert made >= 4
    p._cache.clear(); p._memo.clear()
    a, j = review.answers(p), review.rob_rows(p)["j"]
    assert set(a["done"]) | set(a["values"]) | set(j) <= people | {"agreed"}
    assert (a, j) == before and set(p.decisions()) == people


def test_a_vote_does_not_compare_every_row_of_this_computers_files_when_nothing_is_wrong(proj, monkeypatch):
    """Every vote first checks this computer's own files against its private copies. When the two are the same byte
    for byte (the usual case) that is all it needs to know: comparing them row by row made each vote slower as the
    review grew (a quarter of a second at 5,000 votes each). A file that does differ is still put right."""
    p = proj
    p.add_import("CH", "medline.ris", ris(filler(6)), {"set": "main", "database": "MEDLINE"})
    p.process_imports("CH")
    ids = sorted(r["id"] for r in p.records())
    p.add_decision("CH", ids[0], "ta", "include")
    calls = []
    real = Project._row_key
    monkeypatch.setattr(Project, "_row_key", staticmethod(lambda d: calls.append(1) or real(d)))
    p.add_decision("CH", ids[1], "ta", "exclude")
    p._cache.clear(); p._memo.clear()
    assert [d["r"] for d in p.decisions()["CH"]] == ids[:2] and calls == []
    # an older copy of the shared file is put back: the next vote puts the lost row back first
    path = next(os.path.join(p.path("decisions"), n) for n in os.listdir(p.path("decisions")) if n.startswith("CH@"))
    lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
    open(path, "w", encoding="utf-8").write(lines[0])
    p._cache.clear(); p._memo.clear()
    p.add_decision("CH", ids[2], "ta", "exclude")
    p._cache.clear(); p._memo.clear()
    assert [d["r"] for d in p.decisions()["CH"]] == ids[:3] and calls


def test_a_safety_copy_that_could_not_be_kept_is_reported_until_the_next_one_is(proj, monkeypatch):
    """When this computer's disk is full, the copy kept for "Restore a copy kept on this computer" failed without a
    word: the lead believed a copy of every version existed."""
    import errno
    p = proj
    assert p.sync_warnings() == []
    real = util.write_bytes_atomic

    def full(path, data, *a, **k):
        if os.sep + "backups" + os.sep in path:
            raise OSError(errno.ENOSPC, "No space left on device", path)
        return real(path, data, *a, **k)
    monkeypatch.setattr(util, "write_bytes_atomic", full)
    p.update_settings("CH", {"question": "Does it still work?"})               # the save itself succeeds
    assert p.settings_ro()["question"] == "Does it still work?"
    w = p.sync_warnings()
    assert len(w) == 1 and w[0].startswith("ReviewDesk could not keep its safety copy of reviewdesk-project.json on this "
                                           "computer (its disk may be full). The file itself was saved")
    monkeypatch.setattr(util, "write_bytes_atomic", real)
    p.update_settings("CH", {"question": "Does it work again?"})
    assert p.sync_warnings() == []


def test_a_start_up_message_never_cuts_a_folders_path_in_two():
    """The message that the settings folder cannot be written names the folder: cut in the middle to fit the window,
    the path could not be copied."""
    from reviewdesk import main as rdmain
    path = "/home/sam/a-very-long-folder-name/" + "x" * 90 + "/ReviewDesk"
    out = rdmain._wrap(f"ReviewDesk cannot start: it could not save its settings in {path} (Read-only file system).")
    assert path in out and all(len(line) <= 78 or path in line for line in out.splitlines())
