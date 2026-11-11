"""What the Windows beta test of 1.3.0 found (the Windows download run under Wine), each put right:

W1 square brackets in a project folder's path hid its files; W2 paths too long for Windows; W3 another version of
ReviewDesk already running; W4 a file held open by another program; W5 moving this computer's copies out of the
roaming profile; W6 download names, the recent projects list, the port, the launcher and the stop instruction; W7 the
timeline's estimate of the agreement sample; W8 the names of imported files; W9 initials on the start page."""
from __future__ import annotations

import errno
import http.server
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse

import pytest

import demo_review
from helpers import APP, HERE, ApiError, LocalServer, on_computer, read_text, record_plan, reference_plan, reference_project, ris, \
    screen_all, tiny_records
from realistic import make_pdf
from reviewdesk import VERSION, fulltext, main as rdmain, screening, server, timeline, userconfig, util
from reviewdesk import project as projmod
from reviewdesk.project import LONG_PATH, LONGEST_NAME, SAFE_PATH, WAITING_NAME, Project

ROOT = os.path.dirname(HERE)


def win_error(cls, code: int, text: str, filename: str, filename2: str | None = None) -> OSError:
    """The error Windows gives (with its winerror code), made on any system."""
    e = cls(errno.EACCES if cls is PermissionError else errno.ENOENT, text, filename, None, filename2)
    e.winerror = code
    return e


# ================================================================================================ W1 square brackets
BRACKETS = "MSc [2026-27]"


def link_folder(target: str, link: str) -> None:
    """Make `link` a link to the folder `target`, or skip the test where this computer cannot: Windows lets an ordinary
    account make links only with Developer Mode switched on, and some systems make a link that does not show the
    folder's files."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"this computer cannot make a link to a folder: {e}")
    if not os.path.isfile(os.path.join(link, "reviewdesk-project.json")):
        pytest.skip("a link to a folder does not show the folder's files on this computer")


def counts(obj, path: str = "") -> dict:
    """Every number, yes/no and list length in an answer from ReviewDesk, by where it is."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(counts(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        out[path + "[]"] = len(obj)
        for i, v in enumerate(obj):
            out.update(counts(v, f"{path}[{i}]"))
    elif isinstance(obj, (bool, int, float)):
        out[path] = obj
    return out


PAGES = ["/api/overview", "/api/tasks", "/api/imports", "/api/dedup", "/api/records", "/api/searchlog", "/api/audit",
         "/api/agreement?scope=calibration", "/api/agreement?scope=sample", "/api/conflicts", "/api/ai", "/api/fulltext",
         "/api/fulltext/waiting", "/api/prisma", "/api/integrity", "/api/extract", "/api/readiness", "/api/timeline",
         "/api/reporting", "/api/certainty"]


def finish_and_count(c, folder: str) -> dict:
    """Open the review, add its PDFs (on reports; through the list of PDFs waiting to be placed, one placed, one taken
    off the list and one left there), then every count every page shows, to the lead and to the reviewer."""
    c.open_project(folder)
    c.be("CH")
    rows = c.get("/api/fulltext")["rows"]
    recs = {r["id"]: r for r in c.get("/api/records")["records"]}
    paper = lambda rid: make_pdf(recs[rid]["title"], recs[rid].get("doi", ""), "Journal", recs[rid].get("authors", ""))  # noqa: E731
    ids = [r["id"] for r in rows]
    for rid in ids[:4]:
        assert c.upload("/api/fulltext/upload", paper(rid), record=rid) == {"ok": True}
    for k, rid in enumerate(ids[4:7]):
        title_only = make_pdf(recs[rid]["title"], "", "", "", body=f"copy {k}")     # matched by title only: waits
        out = c.upload("/api/fulltext/bulk", title_only, filename=f"publisher download {k}.pdf")
        assert out["result"] == "waiting", out
    waiting = [w["name"] for w in c.get("/api/fulltext/waiting")["waiting"]]
    assert len(waiting) >= 2, waiting
    c.post("/api/fulltext/assign", {"name": waiting[0], "record": ids[7]})
    c.post("/api/fulltext/discard", {"name": waiting[1]})
    out = {}
    for who in ("CH", "NH"):
        c.be(who)
        app = c.get("/api/app")["project"]
        out[f"{who} /api/app"] = counts({k: app[k] for k in ("is_lead", "member", "warnings", "repairs", "sync_problems",
                                                             "data_problems", "processed", "has_records")})
        for page in PAGES:
            path, _, q = page.partition("?")
            out[f"{who} {page}"] = counts(c.get(path, **dict(urllib.parse.parse_qsl(q))))
    return out


def differences(a: dict, b: dict) -> list:
    """Counts that differ. (Not the copies of the lead's files this computer keeps: one is kept for each second in which
    a file was saved, so how many there are depends on how fast the computer is.)"""
    return [(page, k, a[page].get(k), b[page].get(k)) for page in a for k in sorted(set(a[page]) | set(b[page]))
            if a[page].get(k) != b[page].get(k) and not k.startswith(".backups.")]


def test_a_whole_review_in_a_folder_with_square_brackets_counts_everything(tmp_path):
    """The same small review (tests/demo_review.py: searches, imports, duplicates, calibration, the agreement sample,
    the AI second screener, other methods, the update search, full texts, extraction and risk of bias), made in a
    plain folder and in 'MSc [2026-27]', shows the same numbers on every page, to the lead and to the reviewer."""
    home = str(tmp_path / "home-CH")
    os.makedirs(home)
    plain = str(tmp_path / "OneDrive" / "MSc 2026-27" / "AI triage")
    bracketed = str(tmp_path / "OneDrive" / BRACKETS / "AI triage")
    with on_computer(home):
        for folder in (plain, bracketed):
            demo_review.build(folder)
    s = LocalServer(home)
    try:
        c = s.client()
        got = {f: finish_and_count(c, f) for f in (plain, bracketed)}
    finally:
        s.stop()
    a, b = got[plain], got[bracketed]
    assert differences(a, b) == []
    # and they are the real numbers, as the project's own files give them, not two sets of zeros
    with on_computer(home):
        p = Project(bracketed)
        recs = p.records()
        n_main = sum(1 for r in recs if r["set"] == "main")
        n_db = sum(1 for r in recs if r["set"] in ("main", "update"))
        votes = p.decisions()
        sample = p.state_ro()["sets"]["main"]["sample"]
        n_audit = len(p.audit_entries())
        n_res = len(p.resolutions())                                            # the three the lead recorded
    assert len(recs) > 40 and len(votes["NH"]) > 40 and len(votes["CH"]) > 20 and n_audit > 50 and n_res == 3
    assert a["CH /api/imports"][".imports[]"] == 5
    assert a["CH /api/records"][".records[]"] == len(recs)
    assert a["CH /api/searchlog"][".entries[]"] == 6
    assert a["CH /api/audit"][".entries[]"] == n_audit
    for who in ("CH", "NH"):
        assert a[f"{who} /api/overview"][".sets.main.records"] == n_main
        assert a[f"{who} /api/overview"][".sets.main.sample"] == len(sample) == 11
        assert a[f"{who} /api/agreement?scope=sample"][".stats.n"] == len(sample)
        assert a[f"{who} /api/agreement?scope=sample"][".both_done"] == len(sample)
        assert a[f"{who} /api/integrity"][".report.problems[]"] == 0 and a[f"{who} /api/app"][".data_problems"] == 0
        assert a[f"{who} /api/prisma"][".counts.db.records"] == n_db
        assert a[f"{who} /api/prisma"][".counts.db.ft_included"] == 7
    ai = a["CH /api/ai"]
    assert ai[".coverage.main.done"] + ai[".coverage.main.failed"] == n_main and ai[".coverage.main.missing"] == 0
    assert a["CH /api/fulltext/waiting"][".waiting[]"] == 1
    assert sum(v for k, v in a["CH /api/fulltext"].items() if k.endswith("].pdf")) == 5
    assert a["CH /api/extract"][".studies[]"] >= 5


def test_files_are_found_in_a_folder_whose_name_looks_like_a_pattern(tmp_path):
    names = [BRACKETS, "Review [draft]", "[!x]"]
    if os.name != "nt":                                 # Windows allows neither * nor ? in a folder's name
        names += ["a*b", "what?"]
    for name in names:
        d = tmp_path / name
        d.mkdir()
        (d / "CH@abc123.jsonl").write_text("{}\n")
        (d / "NH@abc123.jsonl").write_text("{}\n")
        (d / ".tmp-123.jsonl").write_text("{}\n")
        assert [os.path.basename(f) for f in util.files_in(str(d), "*.jsonl")] == ["CH@abc123.jsonl", "NH@abc123.jsonl"]
        assert [os.path.basename(f) for f in util.files_in(str(d), "CH@*.jsonl")] == ["CH@abc123.jsonl"]


def test_no_glob_is_given_a_folder_path():
    """Every file listing in the program goes through util.files_in (the folder never a pattern)."""
    import re
    found = []
    for d, _, names in os.walk(os.path.join(APP, "reviewdesk")):
        for n in names:
            if n.endswith(".py"):
                text = read_text(os.path.join(d, n))
                for k, line in enumerate(text.splitlines(), 1):
                    if re.search(r"\bglob\.(i?glob)\(|\bfnmatch\b|\.rglob\(|Path\([^)]*\)\.glob\(", line) and n != "util.py":
                        found.append(f"{n}:{k}: {line.strip()}")
    assert found == []


# ================================================================================================ W2 long paths
LONG_PUBLISHER_NAME = ("Machine learning triage in the emergency department - a prospective multicentre evaluation "
                       "(Annals of Emergency Medicine, 2025, full text, accepted manuscript).pdf")


def test_every_name_reviewdesk_makes_in_a_project_fits_the_room_left_for_it(tmp_path, monkeypatch):
    """The longest names ReviewDesk makes: imports with long names, removed (and given a number twice over, because a
    file of that name was already there each time), PDFs with long names waiting to be placed (and the name each has
    while it is being placed), files found in the fulltexts folder, and a whole review besides. None is longer than
    LONGEST_NAME, LONGEST_NAME is not much more than they need, and the longest project folder allowed leaves room."""
    assert LONG_PATH + 1 + LONGEST_NAME < SAFE_PATH <= 250 and LONGEST_NAME < 90
    folder = str(tmp_path / "p")
    who = "CHRISTOP"                                                          # the longest initials allowed
    with on_computer(str(tmp_path / "home")):
        p = Project.create(folder, "Names", "Colin Hawthorne", who)
        export = "Embase_" + "y" * 90 + ".ris"                                # a long name with no word to cut at
        iid = util.today().replace("-", "") + "-" + who + "-1234"
        monkeypatch.setattr(projmod, "_unique_id", lambda stem, taken: iid)   # so the name is known before it is made
        name = util.safe_filename(f"{iid} {export}", max_len=projmod.IMPORT_NAME)
        assert len(name) == projmod.IMPORT_NAME
        for k in range(12):                    # files of those names already there: (2) ... (12), here and in _removed
            stem, ext = os.path.splitext(name)
            for d, n in (("imports", name if k == 0 else f"{stem} ({k + 1}){ext}"),
                         (os.path.join("imports", "_removed"), f"{stem} (12){ext}.meta" + (f" ({k + 1})" if k else "") + ".json")):
                os.makedirs(p.path(d), exist_ok=True)
                open(p.path(d, n), "w").close()
        m = p.add_import(who, export, ris(*tiny_records(2)), {"set": "main", "database": "Embase"})
        monkeypatch.undo()
        assert m["filename"] == f"{stem} (13){ext}"
        p.remove_import(who, m["id"])
        long_publisher = "x" * 150 + ".pdf"
        for k in range(12):
            fulltext.hold_pdf(p, who, make_pdf("A paper", body=str(k)), long_publisher, "why")
        fulltext.hold_pdf(p, who, make_pdf("A paper"), LONG_PUBLISHER_NAME, "why")
        with open(os.path.join(folder, "fulltexts", "z" * 150 + ".pdf"), "wb") as f:      # put there by hand
            f.write(make_pdf("Another paper"))
        p.move_stray_files(who)
        demo_review.build(str(tmp_path / "review"))
        claim = len(f".placing-{p.dev()}")
    longest = []
    for top in (folder, str(tmp_path / "review")):
        for d, _, names in os.walk(top):
            for n in names:
                rel = os.path.relpath(os.path.join(d, n), top)
                extra = claim if os.path.basename(d) == "_unmatched" and n.lower().endswith(".pdf") else 0
                longest.append((len(rel) + extra, rel))
    longest.sort()
    assert longest[-1][0] <= LONGEST_NAME, longest[-5:]
    assert longest[-1][0] >= LONGEST_NAME - 2, longest[-5:]                # the room is not much more than is needed
    waiting = fulltext.waiting(p)
    assert len(waiting) == 14 and all(len(w["name"]) <= WAITING_NAME + 5 for w in waiting)
    assert {w["from"] for w in waiting} == {long_publisher, LONG_PUBLISHER_NAME, "z" * 150 + ".pdf"}   # the names they came with


def test_waiting_pdfs_are_named_at_a_word_boundary_and_never_overwrite_each_other(tmp_path):
    with on_computer(str(tmp_path / "home")):
        p = Project.create(str(tmp_path / "p"), "Names", "Colin Hawthorne", "CH")
        a = fulltext.hold_pdf(p, "CH", make_pdf("One"), LONG_PUBLISHER_NAME, "why")
        b = fulltext.hold_pdf(p, "CH", make_pdf("Two"), "paper.pdf", "why")
        c = fulltext.hold_pdf(p, "CH", make_pdf("Three"), "paper", "why")              # no .pdf: it is added first
    assert a == "Machine learning triage in the.pdf"
    assert (b, c) == ("paper.pdf", "paper (2).pdf")
    d = p.path("fulltexts", "_unmatched")
    assert sorted(os.listdir(d)) == sorted([a, a + ".json", b, b + ".json", c, c + ".json"])


def test_a_project_is_created_only_where_its_files_have_room(desk, tmp_path):
    c = desk.client()
    parent = str(tmp_path)
    while len(parent) < LONG_PATH - 40:
        parent = os.path.join(parent, "Teams library")
    os.makedirs(parent, exist_ok=True)
    name = "x" * (LONG_PATH - len(parent) - 1)
    assert len(os.path.join(parent, name)) == LONG_PATH
    with pytest.raises(ApiError) as e:
        c.create_project(parent, name + "y")
    assert f"would be {LONG_PATH + 1} characters long, and at most {LONG_PATH} leaves room" in e.value.message
    assert c.create_project(parent, name)["project"]["path"] == os.path.join(parent, name)


def test_opening_a_project_through_a_path_too_long_for_windows_warns_once_a_session(desk, tmp_path, monkeypatch):
    c = desk.client()
    folder = reference_project(c, str(tmp_path / "short"))
    deep = str(tmp_path)
    while len(deep) < LONG_PATH + 10:
        deep = os.path.join(deep, "School of Health in Social Science - MSc Dissertations 2026-27")
    os.makedirs(os.path.dirname(deep), exist_ok=True)
    link_folder(folder, deep)                                 # the reviewer's OneDrive reaches the same folder deeper down
    monkeypatch.setattr(util, "windows_path_limit", lambda: False)
    assert "warning" not in c.open_project(deep) and "path_warning" not in c.get("/api/app")     # long paths turned on
    monkeypatch.setattr(util, "windows_path_limit", lambda: True)       # Windows as it usually is
    out = c.open_project(deep)
    assert out["warning"].startswith(f"This project's folder path is {len(deep)} characters long.")
    assert "path_warning" not in c.get("/api/app", probe="1")        # a second start checking what is running
    first = c.get("/api/app")["path_warning"]
    assert first == out["warning"]
    for want in (f"up to {LONG_PATH} characters is safe", "Open the project from a shorter path",
                 "ask your IT service desk to turn on long paths"):
        assert want in first, want
    assert "path_warning" not in c.get("/api/app")                       # once in each session
    assert "path_warning" not in c.get("/api/app")
    assert "warning" not in c.open_project(folder) and "path_warning" not in c.get("/api/app")   # a short path: nothing


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


def _pdf(c, rid, **kw):
    r = c.rec[rid]
    return make_pdf(r["title"], r["doi"], "Emergency Medicine Journal", r["authors"], **kw)


def _failing_replace(monkeypatch, when, error):
    """os.replace fails with `error` (made from the two paths) whenever when(src, dst) is true."""
    real = os.replace

    def replace(src, dst, *a, **kw):
        if when(str(src), str(dst)):
            raise error(str(src), str(dst))
        return real(src, dst, *a, **kw)
    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(util.time, "sleep", lambda s: None)          # (the retries for a short hold, without the waits)


def test_placing_a_waiting_pdf_says_what_really_went_wrong(ft, monkeypatch):
    c = ft
    rid = c.ids[0]
    c.upload("/api/fulltext/bulk", make_pdf(c.rec[rid]["title"], "", "", ""), filename="paper.pdf")
    # the name it would have while it is placed is too long for Windows
    _failing_replace(monkeypatch, lambda s, d: ".placing-" in d,
                     lambda s, d: win_error(OSError, 206, "The filename or extension is too long", s, d))
    e = c.error("POST", "/api/fulltext/assign", {"name": "paper.pdf", "record": rid})
    claim = os.path.join("fulltexts", "_unmatched", "paper.pdf.placing-")
    assert e.message.startswith(f"Windows could not use {claim}") and "characters long, and Windows allows 259" in e.message
    assert "ask your IT service desk to turn on long paths" in e.message
    # it is open in another program
    _failing_replace(monkeypatch, lambda s, d: ".placing-" in d,
                     lambda s, d: win_error(PermissionError, 32, "Sharing violation", s, d))
    e = c.error("POST", "/api/fulltext/assign", {"name": "paper.pdf", "record": rid})
    assert e.message == (f"The PDF {os.path.join('fulltexts', '_unmatched', 'paper.pdf')} is open in another program (such "
                         "as Adobe Reader, Word or Excel), so ReviewDesk could not change it. Close it in that program, "
                         "then try again.")
    monkeypatch.undo()
    assert [w["name"] for w in c.get("/api/fulltext/waiting")["waiting"]] == ["paper.pdf"]       # still waiting
    c.post("/api/fulltext/discard", {"name": "paper.pdf"})
    e = c.error("POST", "/api/fulltext/assign", {"name": "paper.pdf", "record": rid})            # really gone
    assert e.message == "That file is no longer waiting (someone may have placed or removed it already)."


# ================================================================================================ W4 a file open elsewhere
def test_a_pdf_open_in_another_program_gives_a_plain_message_and_changes_nothing(ft, monkeypatch):
    c = ft
    rid = c.ids[0]
    first = _pdf(c, rid)
    c.upload("/api/fulltext/upload", first, record=rid)
    pdf_file = os.path.join(c.folder, "fulltexts", f"{rid}.pdf")
    held = lambda s, d: os.path.abspath(s) == os.path.abspath(pdf_file)            # noqa: E731 - Adobe Reader holds it
    _failing_replace(monkeypatch, held, lambda s, d: win_error(PermissionError, 32, "Sharing violation", s, d))
    want = (f"The PDF {os.path.join('fulltexts', rid + '.pdf')} is open in another program (such as Adobe Reader, Word or "
            "Excel), so ReviewDesk could not change it. Close it in that program, then try again.")
    for method, path, body, params in (("upload", "/api/fulltext/upload", _pdf(c, rid, body="a better copy"), {"record": rid}),
                                       ("post", "/api/fulltext/remove", {"record": rid}, {})):
        with pytest.raises(ApiError) as e:
            getattr(c, method)(path, body, **params)
        assert e.value.status == 409 and e.value.message == want
    st, _, body = c.request("GET", f"/files/fulltext/{rid}.pdf")
    assert st == 200 and body == first                                        # nothing changed
    assert c.get("/api/integrity")["report"]["problems"] == []
    # a waiting PDF open in another program is not taken off the list
    c.upload("/api/fulltext/bulk", make_pdf(c.rec[c.ids[1]]["title"], "", "", ""), filename="other.pdf")
    waiting_pdf = os.path.join(c.folder, "fulltexts", "_unmatched", "other.pdf")
    real_remove = os.remove
    monkeypatch.setattr(os, "remove", lambda f, *a, **k: (_ for _ in ()).throw(win_error(PermissionError, 32, "Sharing violation", f))
                        if os.path.abspath(f) == waiting_pdf else real_remove(f, *a, **k))
    e = c.error("POST", "/api/fulltext/discard", {"name": "other.pdf"})
    assert e.message.startswith(f"The PDF {os.path.join('fulltexts', '_unmatched', 'other.pdf')} is open in another program")
    monkeypatch.undo()
    assert [w["name"] for w in c.get("/api/fulltext/waiting")["waiting"]] == ["other.pdf"]


def test_file_problems_are_put_in_plain_words(tmp_path):
    root = str(tmp_path / "AI triage")
    inside = os.path.join(root, "state", "state.json")
    tmp = os.path.join(root, "state", ".tmp-abc123")
    assert util.file_problem(win_error(PermissionError, 32, "x", tmp, inside), root).startswith(
        f"The file {os.path.join('state', 'state.json')} is open in another program")
    assert util.file_problem(win_error(PermissionError, 5, "Access is denied", inside), root).startswith(
        f"ReviewDesk is not allowed to change {os.path.join('state', 'state.json')}.")
    assert "no room left on the disk" in util.file_problem(OSError(errno.ENOSPC, "No space", inside), root)
    assert "too long for this computer" in util.file_problem(OSError(errno.ENAMETOOLONG, "File name too long", inside), root)
    assert util.file_problem(ValueError("x"), root) == "" and util.file_problem(OSError(errno.EIO, "I/O error", inside), root) == ""


# ================================================================================================ W3 another version running
class _OldReviewDesk(http.server.BaseHTTPRequestHandler):
    """ReviewDesk 1.2.1 as a second start meets it: it does not know the question 1.3.0 asks, and refuses a request
    without its token. (`claims` makes it another program that says it is this version, to test that it is not
    believed.) Everything it is sent is kept in `asked`."""
    server_version = "ReviewDesk/1.2.1"
    claims = None
    asked: list = []

    def do_GET(self):
        type(self).asked.append((self.path, dict(self.headers)))
        body = json.dumps(self.claims if self.claims else {"error": "expired", "expired": True}).encode()
        self.send_response(200 if self.claims else 403)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def running_copy(monkeypatch):
    """A ReviewDesk answering on the usual port (made up here, on a free port)."""
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _OldReviewDesk)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(server, "DEFAULT_PORT", httpd.server_address[1])
    monkeypatch.setattr(_OldReviewDesk, "asked", [])
    opened = []
    monkeypatch.setattr(rdmain.webbrowser, "open", lambda url: opened.append(url))
    yield httpd, opened
    httpd.shutdown()
    httpd.server_close()


def test_another_version_running_says_which_and_what_to_do(running_copy, capsys, monkeypatch):
    httpd, opened = running_copy
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(SystemExit) as e:
        rdmain.main([])
    assert e.value.code == rdmain.OTHER_VERSION_RUNNING == 3          # the start file then waits for a key
    out = " ".join(capsys.readouterr().out.split())
    assert f"ReviewDesk 1.2.1 is already running on this computer, so ReviewDesk {VERSION} cannot start." in out
    assert "find the other black ReviewDesk window (the one that says ReviewDesk 1.2.1 is running) and close it" in out
    assert 'Then double-click "Start ReviewDesk" again.' in out
    assert opened == []                                                # the old version is not opened
    _token_never_sent()


def _token_never_sent():
    """Whatever is listening on ReviewDesk's port is asked without the session token, in a header or the address."""
    token = userconfig.session_token()
    assert _OldReviewDesk.asked
    for path, headers in _OldReviewDesk.asked:
        assert token not in path and token not in " ".join(headers.values()) and "X-Token" not in headers


def test_the_same_version_running_is_opened_as_before(capsys, monkeypatch):
    """This computer's own ReviewDesk proves itself (it signs a number sent to it with the session token, which is
    never sent to find out), and the second start opens it."""
    httpd, url = server.serve(0)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    monkeypatch.setattr(server, "DEFAULT_PORT", httpd.server_address[1])
    opened = []
    monkeypatch.setattr(rdmain.webbrowser, "open", lambda u: opened.append(u))
    try:
        assert rdmain.main([]) == 0
    finally:
        httpd.shutdown()
        httpd.server_close()
    out = capsys.readouterr().out
    assert "ReviewDesk is already running. Opening it in your web browser:" in out
    assert opened == [f"http://127.0.0.1:{httpd.server_address[1]}/?t={userconfig.session_token()}"] == [url]


@pytest.mark.parametrize("claims", [{"name": "ReviewDesk", "version": VERSION},
                                    {"name": "ReviewDesk", "version": VERSION, "proof": "0" * 64}])
def test_another_program_on_the_port_is_never_given_the_token_or_opened(running_copy, monkeypatch, claims):
    """Another program (or another person's ReviewDesk on a shared computer) that has ReviewDesk's usual port, and
    says it is this version, cannot prove it: it is not sent the session token, and the browser is not sent to it."""
    httpd, opened = running_copy
    monkeypatch.setattr(_OldReviewDesk, "server_version", f"ReviewDesk/{VERSION}")
    monkeypatch.setattr(_OldReviewDesk, "claims", claims)
    assert rdmain._already_running() == (VERSION, "")                  # so ReviewDesk starts on a port of its own
    _token_never_sent()
    assert opened == []


def test_the_start_files_keep_the_window_open_when_another_version_runs():
    bat = read_text(os.path.join(ROOT, "launchers", "Start ReviewDesk.bat"))
    assert "if errorlevel 3 if not errorlevel 4 (" in bat and "pause\n  exit /b 3" in bat.replace("\r\n", "\n")
    mac = read_text(os.path.join(ROOT, "launchers", "Start ReviewDesk (Mac).command"))
    assert 'if [ "$code" -eq 3 ]; then' in mac and "read -n 1" in mac


# ================================================================================================ W5 this computer's copies
@pytest.fixture
def windows_profile(tmp_path, monkeypatch):
    """%APPDATA% and %LOCALAPPDATA% of a Windows account, with the copies an earlier version kept in %APPDATA%."""
    monkeypatch.delenv("REVIEWDESK_HOME", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")

    def profile(k=0, files=None):
        base = tmp_path / f"user{k}"
        monkeypatch.setenv("APPDATA", str(base / "Roaming"))
        monkeypatch.setenv("LOCALAPPDATA", str(base / "Local"))
        old = base / "Roaming" / "ReviewDesk" / "projects"
        for rel, data in (files or {"abc/mirror/NH@3f9a1c.jsonl": b'{"r": 1}\n', "abc/snap/pdf/R00012.0123.pdf": b"%PDF-1.4 x" * 5000,
                                    "def/seal.json": b"{}"}).items():
            f = old / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
        return base, old, base / "Local" / "ReviewDesk" / "projects"
    return profile


def tree(root) -> dict:
    """{path below root, with forward slashes on every system: the file's bytes}"""
    return {os.path.relpath(os.path.join(d, n), root).replace(os.sep, "/"): open(os.path.join(d, n), "rb").read()
            for d, _, names in os.walk(root) for n in names}


def test_requests_arriving_together_at_the_first_start_all_get_the_new_place(windows_profile):
    for k in range(25):
        base, old, new = windows_profile(k)
        want, got, barrier = tree(old), [], threading.Barrier(6)

        def ask():
            barrier.wait()
            got.append(userconfig.data_dir())
        ts = [threading.Thread(target=ask) for _ in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert got == [str(base / "Local" / "ReviewDesk")] * 6
        assert tree(new) == want and not old.exists()


def test_copies_on_another_drive_are_copied_checked_and_only_then_removed(windows_profile, monkeypatch):
    base, old, new = windows_profile()
    want = tree(old)
    real = os.replace

    def replace(src, dst):                                     # a roaming profile on a server: no rename across drives
        if os.path.abspath(src) == str(old):
            raise OSError(errno.EXDEV, "The system cannot move the file to a different disk drive", src)
        return real(src, dst)
    monkeypatch.setattr(os, "replace", replace)
    # the first time, a copy does not come out identical: nothing is removed, and the old place stays in use
    monkeypatch.setattr(userconfig, "_same", lambda a, b: False)
    assert userconfig.data_dir() == str(base / "Roaming" / "ReviewDesk")
    assert tree(old) == want and not new.exists()
    monkeypatch.undo()
    monkeypatch.delenv("REVIEWDESK_HOME", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(base / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(base / "Local"))
    monkeypatch.setattr(os, "replace", replace)
    userconfig._DATA_DIRS.clear()                              # ReviewDesk started again
    assert userconfig.data_dir() == str(base / "Local" / "ReviewDesk")
    assert tree(new) == want and not old.exists()


def test_an_old_projects_folder_made_again_later_is_merged_without_losing_newer_copies(windows_profile):
    base, old, new = windows_profile()
    assert userconfig.data_dir() == str(base / "Local" / "ReviewDesk") and not old.exists()
    (new / "abc" / "mirror" / "NH@3f9a1c.jsonl").write_bytes(b'{"r": 1}\n{"r": 2}\n')           # newer here
    (new / "def" / "seal.json").write_bytes(b'{"older": true}')
    os.utime(new / "def" / "seal.json", (time.time() - 3600, time.time() - 3600))
    # an earlier version is run again: it keeps its copies in the old place once more
    windows_profile(0, {"abc/mirror/NH@3f9a1c.jsonl": b'{"r": 1}\n', "def/seal.json": b'{"newer": true}',
                        "abc/snap/pdf/R00012.0123.pdf": b"%PDF-1.4 x" * 5000, "ghi/folder.json": b'{"path": "x"}'})
    os.utime(old / "abc" / "mirror" / "NH@3f9a1c.jsonl", (time.time() - 7200, time.time() - 7200))
    userconfig._DATA_DIRS.clear()                              # ReviewDesk started again
    assert userconfig.data_dir() == str(base / "Local" / "ReviewDesk")
    got = tree(new)
    assert got["abc/mirror/NH@3f9a1c.jsonl"] == b'{"r": 1}\n{"r": 2}\n'          # the newer copy here is kept
    assert got["def/seal.json"] == b'{"newer": true}'                            # the newer copy from the old place is used
    assert got["ghi/folder.json"] == b'{"path": "x"}'                            # only in the old place: moved
    assert got["abc/snap/pdf/R00012.0123.pdf"] == b"%PDF-1.4 x" * 5000
    assert not old.exists()
    aside = [d for d in os.listdir(base / "Local" / "ReviewDesk") if d.startswith("projects set aside ")]
    assert len(aside) == 1                                                       # and the other versions are kept
    assert tree(base / "Local" / "ReviewDesk" / aside[0]) == {"abc/mirror/NH@3f9a1c.jsonl": b'{"r": 1}\n',
                                                              "def/seal.json": b'{"older": true}'}


# ================================================================================================ W6 smaller things
def test_download_names_are_names_windows_accepts(desk, tmp_path):
    c = desk.client()
    reference_project(c, str(tmp_path), short_title="AI/ML triage: ED? *draft* <v2> |final|.")
    st, hd, _ = c.download("timeline_svg")
    assert st == 200
    name = urllib.parse.unquote(hd["content-disposition"].split("filename*=UTF-8''")[1])
    assert name == "AI_ML triage_ ED_ _draft_ _v2_ _final_. - steps of the review.svg"
    assert not set(name) & set('<>:"/\\|?*') and name == util.safe_filename(name)


def test_the_recent_list_holds_a_folder_once_however_its_name_was_typed(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "normcase", lambda s: s.lower())          # as on Windows
    a = str(tmp_path / "AI Triage Review")
    userconfig.remember_project(a, "One")
    userconfig.remember_project(str(tmp_path).upper() + "/ai triage review", "One")
    userconfig.remember_project(a + "/", "One")
    assert [r["path"] for r in userconfig.load()["recent"] if r["path"].lower() == a.lower()] == [a]


def test_starting_never_asks_the_system_for_this_computers_full_name(monkeypatch):
    """Python's own web server asks for it as it starts (socket.getfqdn), and nothing in ReviewDesk uses it. On a Mac
    whose network leaves the question unanswered, ReviewDesk sat for half a minute before it said it was running."""
    import socket
    from reviewdesk import server
    asked = []
    monkeypatch.setattr(socket, "getfqdn", lambda *a: asked.append(a) or "this-computer.example")
    httpd = server.Server(("127.0.0.1", 0), server.Handler)
    try:
        assert asked == []
        assert (httpd.server_name, httpd.server_port) == ("127.0.0.1", httpd.server_address[1]) and httpd.server_port > 0
    finally:
        httpd.server_close()


def test_the_console_says_when_the_usual_port_is_taken(tmp_path):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    taken = s.getsockname()[1]
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ, REVIEWDESK_HOME=str(home), REVIEWDESK_URL_FILE=str(home / "url.txt"), REVIEWDESK_NO_REOPEN="1",
               PYTHONIOENCODING="utf-8")
    code = (f"import sys; sys.path.insert(0, {APP!r}); from reviewdesk import server, main; server.DEFAULT_PORT = {taken}; "
            "main.main(['--no-browser'])")
    log = open(home / "console.txt", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        end = time.time() + 30
        while time.time() < end and not (home / "url.txt").exists():
            time.sleep(0.05)
        time.sleep(0.2)
        port = urllib.parse.urlsplit(read_text(str(home / "url.txt"))).port
    finally:
        proc.terminate()
        proc.wait(10)
        log.close()
        s.close()
    out = " ".join(read_text(str(home / "console.txt")).split())
    assert port != taken
    assert (f"Another program on this computer is using ReviewDesk's usual address (port {taken}), so ReviewDesk is "
            f"using port {port} instead. Browser tabs and bookmarks from earlier will not work this time") in out


def test_the_stop_instruction_matches_what_the_console_does():
    assert rdmain.stop_instruction("win32").endswith("close this window (or press Ctrl+C, then Y).")
    assert rdmain.stop_instruction("darwin").endswith("close this window (or press Ctrl+C).")


def test_the_windows_start_file_says_extract_all_when_run_from_inside_the_zip():
    bat = read_text(os.path.join(ROOT, "launchers", "Start ReviewDesk.bat")).replace("\r\n", "\n")
    assert bat.index('if not exist "%~dp0app\\run_reviewdesk.py" goto notextracted') < bat.index("rem 1. The Python")
    zipped = bat[bat.index(":notextracted"):]
    assert 'if /i "%here:.zip\\=%"=="%here%" goto missingapp' in zipped and "choose Extract All" in zipped and "pause" in zipped
    assert "copy the whole ReviewDesk folder again" not in bat


# ================================================================================================ W7, W8
def test_the_timeline_estimates_the_sample_from_the_records_it_is_drawn_from(tmp_path):
    with on_computer(str(tmp_path / "home")):
        p = Project.create(str(tmp_path / "p"), "Sample", "Colin Hawthorne", "CH")
        p.update_settings("CH", {"reviewers": [{"id": "CH", "name": "Colin Hawthorne", "role": "lead"},
                                               {"id": "NH", "name": "Nadine Hayes", "role": "reviewer"}],
                                 "question": "Does AI triage help?", "criteria": {"include": ["Prospective"], "exclude": [], "rules": []},
                                 "screening": {"reviewers": ["NH", "CH"], "mode": "sample_then_single", "sample_fraction": 0.2,
                                               "calibration_size": 0, "single_reviewer": "NH", "second_reviewer": "CH",
                                               "ai_second_screener": False}})
        record_plan(p)
        for set_, n, seed in (("main", 46, 1), ("update", 20, 2), ("other", 9, 3)):
            p.add_import("CH", f"{set_}.ris", ris(*tiny_records(n, seed=seed, prefix=set_)), {"set": set_, "database": "MEDLINE"})
        p.process_imports("CH")
        n_main = sum(1 for r in p.records() if r["set"] == "main")
        assert len(p.records()) > n_main
        m = timeline.project_model(p, "CH")
        step = next(s for ph in m["phases"] for s in ph["steps"] if s["id"] == "sample")
        about = f"(about {round(n_main * 0.2)})"
        assert about in json.dumps(step, ensure_ascii=False)
        screening.start(p, "CH", "main")
        assert len(p.state_ro()["sets"]["main"]["sample"]) == round(n_main * 0.2)


def test_long_import_names_are_cut_at_a_word_and_keep_their_extension(tmp_path):
    name = "MEDLINE (Ovid) results 28.11.2026 «AI triage in the emergency department».ris"
    assert util.safe_filename(f"20261128-NH-4821 {name}", max_len=48) == "20261128-NH-4821 MEDLINE (Ovid) results.ris"
    assert util.safe_filename("results 28.11.2026 « AI triage ».ris", max_len=26) == "results 28.11.2026.ris"     # no « left
    assert util.safe_filename("x" * 80 + ".ris", max_len=48) == "x" * 44 + ".ris"
    assert util.safe_filename("Smith 2026. Final version of the paper", max_len=20) == "Smith 2026. Final"
    assert util.safe_filename("nul.ris") == "_nul.ris" and util.safe_filename("a/b:c?.ris") == "a_b_c_.ris"
    with on_computer(str(tmp_path / "home")):
        p = Project.create(str(tmp_path / "p"), "Names", "Nadine Hayes", "NH")
        m = p.add_import("NH", name, ris(*tiny_records(3)), {"set": "main", "database": "MEDLINE"})
    assert m["filename"].endswith(" MEDLINE (Ovid) results.ris") and len(m["filename"]) <= projmod.IMPORT_NAME
    assert m["original_name"] == name


# ================================================================================================ W9 and the page
def test_the_page_skips_titles_in_initials_and_shows_a_long_path_once(desk, tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api")
    c = desk.client()
    folder = reference_project(c, str(tmp_path / "short"), short_title="AI/ML triage: ED?")
    deep = str(tmp_path)
    while len(deep) < LONG_PATH + 10:
        deep = os.path.join(deep, "School of Health in Social Science - MSc Dissertations 2026-27")
    os.makedirs(os.path.dirname(deep), exist_ok=True)
    link_folder(folder, deep)
    c.post("/api/project/close", {})
    monkeypatch.setattr(util, "windows_path_limit", lambda: True)
    errors = []
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                      # noqa: BLE001 - no browser on this computer
            pytest.skip(f"Chromium is not available: {e}")
        pg = b.new_page(viewport={"width": 1280, "height": 900})
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(f"http://127.0.0.1:{desk.port}/?t={desk.token}")
        pg.evaluate("document.addEventListener('securitypolicyviolation', e => console.error('CSP ' + e.violatedDirective))")
        # W9: initials on the start page
        pg.click("text=Create a new project")
        name, initials = pg.locator("div.modal .grid2 input >> nth=0"), pg.locator("div.modal .grid2 input >> nth=1")
        for typed, want in (("Dr Colin Hawthorne", "CH"), ("Prof. Ada Lovelace", "AL"), ("Mrs Nadine Hayes", "NH"),
                            ("Nadine Hayes", "NH"), ("Dr", "D")):
            name.fill(typed)
            assert initials.input_value() == want, typed
        pg.click("div.modal button:has-text('Cancel')")
        # W2: a project opened through a path too long for Windows: said once, clearly
        c.open_project(deep)
        pg.goto(f"http://127.0.0.1:{desk.port}/?t={desk.token}#/overview")
        dlg = pg.locator("div.modal[role=dialog]")
        dlg.wait_for()
        assert dlg.locator("h2").inner_text() == "This project's folder path is too long for Windows"
        assert "Open the project from a shorter path" in dlg.inner_text()
        pg.screenshot(path=str(tmp_path / "long-path.png"))
        pg.click("div.modal button:has-text('I understand')")
        assert dlg.count() == 0
        pg.reload()
        pg.wait_for_selector("h1")
        pg.wait_for_timeout(400)
        assert pg.locator("div.modal[role=dialog]").count() == 0                 # once in each session
        # W6: the toast names the file as it is saved
        pg.goto(f"http://127.0.0.1:{desk.port}/?t={desk.token}#/timeline")
        with pg.expect_download() as dl:
            pg.click("button:has-text('Download SVG')")
        saved = dl.value.suggested_filename
        assert saved.startswith("AI_ML triage_ ED_ - steps of the review") and saved.endswith(".svg")
        assert pg.locator("#toast").inner_text() == f"Downloaded {saved}"
        b.close()
    assert errors == []
