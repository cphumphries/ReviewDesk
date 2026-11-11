"""Final audit, fixer fx-net: the AI runs, the free-copy PDF finder, the shape of requests, and start-up. Each test
fails without its fix. No real AI service, key or internet service is used: the stand-ins run on this computer."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from helpers import reference_plan, reference_project, ris, tiny_records
from mock_elm import MODEL_B, TEST_KEY
from reviewdesk import ai, fulltext, main, server, shapes, timeline, userconfig, util
from reviewdesk.project import Project

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture
def lead(desk, elm, tmp_path):
    c = desk.client()
    c.post("/api/user", {"elm_url": elm.url, "elm_key": TEST_KEY})
    folder = reference_project(c, str(tmp_path / "OneDrive"), screening=reference_plan(calibration_size=0))
    c.records = tiny_records(12, seed=5)
    c.add_import(ris(*c.records), "medline.ris", "MEDLINE")
    c.process()
    c.folder, c.elm = folder, elm
    return c


class Stranger:
    """A program on this computer that answers HTTP requests in a way an AI service or a publisher never should."""
    def __init__(self, mode: str):
        self.mode = mode
        stranger = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                stranger.answer(self)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                stranger.answer(self)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def answer(self, h):
        if self.mode == "html":
            body = b"<html><body>Sign in to continue</body></html>"
            h.send_response(200)
            h.send_header("Content-Type", "text/html")
            h.send_header("Content-Length", str(len(body)))
            h.end_headers()
            h.wfile.write(body)
        elif self.mode == "drop":
            h.close_connection = True
            h.connection.shutdown(socket.SHUT_RDWR)
        elif self.mode == "not_http":
            h.wfile.write(b"220 mail.example.org ESMTP ready\r\n")
            h.close_connection = True
        elif self.mode in ("trickle", "trickle_pdf"):
            if h.path.rstrip("/").endswith("/models"):
                body = json.dumps({"data": [{"id": "m-a"}, {"id": "m-b"}]}).encode()
                h.send_response(200)
                h.send_header("Content-Length", str(len(body)))
                h.end_headers()
                h.wfile.write(body)
                return
            h.send_response(200)
            h.send_header("Content-Length", "100000000")
            h.end_headers()
            if self.mode == "trickle_pdf":
                h.wfile.write(b"%PDF-1.7\n" + b" " * 1100)
            try:
                for _ in range(600):
                    h.wfile.write(b" ")
                    h.wfile.flush()
                    time.sleep(0.2)
            except OSError:
                pass

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


# ------------------------------------------------------------------------------------------------ A3: one run at a time
def test_two_ai_runs_can_never_start_at_once(lead, monkeypatch):
    """The second of two run requests sent while the service lists its models (slowly) was started as well: every
    call was paid for twice, and the two runs' answers broke each other's fingerprint chain for good."""
    c = lead
    real = ai.check_models_offered
    monkeypatch.setattr(ai, "check_models_offered", lambda *a, **kw: (time.sleep(0.8), real(*a, **kw))[1])
    got = []

    def start(scope):
        try:
            got.append(("started", c.post("/api/ai/run", {"scope": scope})["label"]))
        except Exception as e:                                  # noqa: BLE001
            got.append(("refused", str(e)))
    ts = [threading.Thread(target=start, args=(s,)) for s in ("all", "trial")]
    for t in ts:
        t.start()
        time.sleep(0.15)
    for t in ts:
        t.join()
    assert sorted(g[0] for g in got) == ["refused", "started"], got
    assert "already going" in next(g[1] for g in got if g[0] == "refused")
    st = c.wait_ai()
    assert st["message"] == "Finished"
    assert len(c.elm.calls) == st["total"] == 24
    assert c.summary()["data_problems"] == 0


def test_a_run_refused_before_it_starts_leaves_nothing_running(lead):
    c = lead
    c.post("/api/ai/config", {"passes": {"A": {"model": "no-such-model"}}})
    e = c.error("POST", "/api/ai/run", {"scope": "all"})
    assert "does not offer no-such-model" in e.message
    assert c.get("/api/ai")["status"].get("running") is not True
    c.post("/api/ai/config", {"passes": {"A": {"model": ai.DEFAULT_CONFIG["passes"]["A"]["model"]}}})
    assert c.run_ai()["message"] == "Finished"


def test_two_searches_for_free_copies_can_never_start_at_once(lead, monkeypatch):
    c = lead
    userconfig.update(unpaywall_email="colin@example.org")
    p = Project(c.folder)
    ids = [r["id"] for r in p.records()][:3]
    real = fulltext.Fetcher.running_elsewhere
    monkeypatch.setattr(fulltext.Fetcher, "running_elsewhere", classmethod(lambda cls, p: (time.sleep(0.5), real(p))[1]))
    monkeypatch.setattr(fulltext, "find_free_copy", lambda rec, email: {"status": "not_found", "source": "", "url": "", "tried": ["Unpaywall"]})
    got = []

    def start():
        try:
            fulltext.Fetcher.start(p, "CH", ids)
            got.append("started")
        except Exception as e:                                  # noqa: BLE001
            got.append(str(e))
    ts = [threading.Thread(target=start) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(got) == ["A search for free copies is already running.", "started"]
    for _ in range(100):
        if not fulltext.Fetcher.status(p)["running"]:
            break
        time.sleep(0.05)
    assert len(fulltext.search_results(p)) == 3


# ------------------------------------------------------------------------------------------------ A4, A10: a run that cannot go on
def test_a_key_refused_part_way_stops_the_run_at_once(lead):
    """From the seventh call the service refuses the key: before, every one of the remaining calls was still made,
    each carrying a record and the refused key."""
    c = lead
    c.elm.fail("http401", when=lambda call: len(c.elm.calls) > 6)
    st = c.run_ai()
    assert st["error"] and "refused the key" in st["message"]
    first = next(i for i, x in enumerate(c.elm.calls) if x.decision == "http401")
    assert len(c.elm.calls) - first - 1 <= 3                  # only the calls already on their way (4 at once)


def test_a_busy_service_at_the_start_is_called_busy(lead, quick_retries):
    c = lead
    c.elm.retry_after = "1"
    c.elm.fail("429")
    st = c.run_ai()
    assert st["error"] and st["message"].startswith("The AI service is busy") and "could not reach" not in st["message"]


# ------------------------------------------------------------------------------------------------ A5: Check the connection
@pytest.mark.parametrize("mode, words", [("html", "answered, but not as an AI service does"),
                                         ("drop", "closed the connection without answering"),
                                         ("not_http", "answered, but not as an AI service does")])
def test_check_the_connection_says_what_is_wrong_with_an_address_that_is_not_an_ai_service(desk, mode, words):
    s = Stranger(mode)
    try:
        c = desk.client()
        c.post("/api/user", {"elm_url": s.url, "elm_key": TEST_KEY})
        e = c.error("POST", "/api/ai/models", {})
        assert e.status == 400 and words in e.message and "Error" not in e.message
    finally:
        s.stop()


# ------------------------------------------------------------------------------------------------ A7: Stop
def test_stop_ends_a_run_whose_answer_trickles_in(desk, tmp_path):
    """The service sends its headers and then a byte now and then, for ever. Before, Stop changed nothing: the run
    stayed 'running' until ReviewDesk was restarted."""
    s = Stranger("trickle")
    try:
        c = desk.client()
        c.post("/api/user", {"elm_url": s.url, "elm_key": TEST_KEY})
        reference_project(c, str(tmp_path / "OneDrive"), screening=reference_plan(calibration_size=0))
        c.add_import(ris(*tiny_records(4, seed=3)), "medline.ris", "MEDLINE")
        c.process()
        c.post("/api/ai/config", {"passes": {"A": {"model": "m-a"}, "B": {"model": "m-b"}}})
        c.post("/api/ai/run", {"scope": "all"})
        time.sleep(1.0)
        st = c.post("/api/ai/stop", {})
        assert not st.get("running") or st["message"].startswith("Stopping")
        st = c.wait_ai(wait=8)
        assert st["message"] == "Stopped" and st["done"] == 0
    finally:
        s.stop()


def test_stop_ends_a_pdf_download_that_trickles_in(monkeypatch):
    s = Stranger("trickle_pdf")
    monkeypatch.setenv("REVIEWDESK_OA_BASE", "http://127.0.0.1:1")          # (lets the finder reach this computer)
    flag = []
    fulltext._STOP.flag = lambda: bool(flag)
    threading.Timer(1.0, lambda: flag.append(1)).start()
    t0 = time.time()
    try:
        with pytest.raises(util.ReplyStopped):
            fulltext.fetch_pdf(s.url.replace("/v1", "/paper.pdf"))
        assert time.time() - t0 < 5
    finally:
        fulltext._STOP.flag = None
        s.stop()


def test_a_reply_is_read_within_its_overall_time(monkeypatch):
    class Drip:
        def read1(self, n):
            time.sleep(0.2)
            return b"x"
    t0 = time.time()
    with pytest.raises(util.ReplyTooBig):
        util.read_reply(Drip(), 1024, 1)
    assert time.time() - t0 < 2
    with pytest.raises(util.ReplyStopped):
        util.read_reply(Drip(), 1024, 30, stopped=lambda: time.time() - t0 > 1.5)


# ------------------------------------------------------------------------------------------------ A9, B2: the shape of requests
def _files(folder: str) -> dict:
    out = {}
    for root, _, names in os.walk(folder):
        for n in names:
            if n.endswith((".jsonl", ".json")):
                f = os.path.join(root, n)
                out[os.path.relpath(f, folder)] = open(f, "rb").read()
    return out


PAGES = ("/api/overview", "/api/ai", "/api/readiness", "/api/prisma", "/api/timeline", "/api/searchlog", "/api/conflicts",
         "/api/fulltext", "/api/setup_summary")


@pytest.mark.parametrize("route, body", [
    ("/api/decide", {"record": "R00001", "stage": "ta", "decision": "include", "study": 5}),
    ("/api/decide", {"record": "R00001", "stage": "ta", "decision": "include", "note": ["x"]}),
    ("/api/decide", {"stage": "ta", "decision": "include"}),
    ("/api/decide", {"record": ["R00001"], "stage": "ta", "decision": "include"}),
    ("/api/resolve", {"record": ["R00001"], "stage": "ta", "decision": "include"}),
    ("/api/resolve", {"record": "R00001", "stage": "ta", "decision": "include", "study": {"a": 1}}),
    ("/api/resolve", {"record": "R99999", "stage": "ta", "decision": "include"}),
    ("/api/searchlog", {"database": 5, "date": "2026-10-01", "results": "10", "string": "x"}),
    ("/api/searchlog", {"database": "MEDLINE", "platform": ["Ovid"], "date": "2026-10-01", "results": "10", "string": "x"}),
    ("/api/settings", {"changes": {"title": 5}}),
    ("/api/settings", {"changes": {"question": ["a"]}}),
    ("/api/settings", {"changes": {"criteria": {"include": [1, 2]}}}),
    ("/api/settings", {"changes": {"screening": "x"}}),
    ("/api/settings", {"changes": {"screening": {"sample_fraction": "0.2"}}}),
    ("/api/settings", {"changes": {"known_studies": [5]}}),
    ("/api/settings", {"changes": {"registers": [{"name": ["x"], "records": 3}]}}),
    ("/api/settings", {"changes": {"dates": {"start": 2026}}}),
    ("/api/settings", {"changes": {"amendments": 5}}),
    ("/api/settings", {"changes": "x"}),
    ("/api/ai/config", {"passes": {"A": {"max_tokens": "lots"}}}),
    ("/api/ai/config", {"passes": {"A": {"model": 5}}}),
    ("/api/ai/config", {"concurrency": "x"}),
    ("/api/ai/config", {"locked": True}),
    ("/api/screening/calibration", {"set": "nope"}),
    ("/api/screening/start", {"set": ""}),
    ("/api/dedup/notdup", {"kept": ["R00001"], "other": "R00002"}),
    ("/api/dedup/merge", {"pairs": [["R00001"]]}),
    ("/api/dedup/merge", {"keep": "R00001"}),
    ("/api/dedup/notdup", {"other": "R00002"}),
    ("/api/dedup/notdup", {}),
    ("/api/worksheet", {"rev": "x"}),
    ("/api/fulltext/fetch", {"ids": [["R00001"]]}),
    ("/api/certainty/define", {"outcome": {"name": "Pain", "importance": ["critical"], "kind": "effect"}}),
    ("/api/rob/plan", {"plan": {"designs": 5}}),
    ("/api/project/open", {"path": ["x"]}),
])
def test_a_request_of_the_wrong_shape_is_refused_in_words_and_saves_nothing(lead, route, body):
    c = lead
    before = _files(c.folder)
    e = c.error("POST", route, body)
    assert e.status == 400 and "Something went wrong" not in e.message and "Error" not in e.message
    assert _files(c.folder) == before
    for page in PAGES:
        c.get(page)


def test_a_null_counts_as_left_out(lead):
    c = lead
    c.post("/api/ai/config", {"passes": {"A": None, "B": {"model": None}}, "concurrency": None})
    cfg = c.get("/api/ai")["config"]
    assert cfg["passes"]["A"]["model"] == ai.DEFAULT_CONFIG["passes"]["A"]["model"]
    assert cfg["passes"]["B"]["model"] == ai.DEFAULT_CONFIG["passes"]["B"]["model"]


def test_requests_that_cannot_be_read_get_a_plain_400(lead):
    c = lead
    h = {"Content-Type": "application/json", "X-Project": c.project_key}
    deep = b'{"a": ' + b"[" * 100000 + b"]" * 100000 + b"}"
    for body in (deep, b'{"record": "R00001", "stage": "ta", "decision": "include", "note": "\\ud83d"}',
                 b'{"record": "R00001\\u0000", "stage": "ta", "decision": "include"}'):
        st, _, data = c.request("POST", "/api/decide", body, h)
        assert st == 400 and "Something went wrong" not in json.loads(data)["error"]
    for q in ({"offset": "abc"}, {"limit": "x"}):
        e = c.error("GET", "/api/records", **q)
        assert e.status == 400
    assert c.get("/api/records", offset="2", limit="3")["records"]


def test_an_error_message_holding_a_lone_surrogate_is_still_sent(lead):
    c = lead
    st, _, data = c.request("POST", "/api/me", b'{"reviewer": "\\udc80"}', {"Content-Type": "application/json"})
    assert st == 400 and json.loads(data)["error"]
    msg = server.ApiError("That person \udc80 is not in this project.")
    h = server.Handler.__new__(server.Handler)
    sent = []
    h._send = lambda code, body, *a, **kw: sent.append((code, body))
    h._json(400, {"error": str(msg)})
    assert sent and json.loads(sent[0][1])["error"].startswith("That person")


def test_a_real_bug_still_reaches_the_error_log(lead, monkeypatch):
    c = lead
    monkeypatch.setattr(server, "h_tasks", lambda q, body: {}["no such key"])
    monkeypatch.setitem(server.ROUTES_GET, "/api/tasks", server.h_tasks)
    e = c.error("GET", "/api/tasks")
    assert e.status == 500 and e.message.startswith("Something went wrong")
    log = open(os.path.join(userconfig.data_dir(), "errors.jsonl"), encoding="utf-8").read()
    assert "KeyError" in log and "Traceback" in log


def test_a_search_log_entry_keeps_only_its_own_fields_and_who_recorded_it(lead):
    c = lead
    e = c.post("/api/searchlog", {"database": "MEDLINE", "date": "2026-10-01", "results": 10, "string": "x", "by": "NH",
                                  "files": ["x"], "mismatch": "y", "extra": {"a": 1}})
    assert e["by"] == "CH" and e["results"] == "10" and "files" not in e and "extra" not in e
    e2 = c.post("/api/searchlog", {"id": e["id"], "by": "NH", "database": "Embase", "date": "2026-10-01", "results": "4",
                                   "string": "x"})
    assert e2["by"] == "CH"


def test_pages_still_open_when_the_ai_set_up_on_disk_holds_values_of_the_wrong_kind(lead):
    c = lead
    util.write_json_atomic(os.path.join(c.folder, "ai", "config.json"),
                           {"passes": {"A": {"model": None, "max_tokens": "lots"}, "B": {"model": 5, "temperature": "hot"}},
                            "concurrency": "x", "fixed_prompts": 5})
    for page in ("/api/overview", "/api/ai", "/api/readiness", "/api/prisma"):
        c.get(page)
    cfg = c.get("/api/ai")["config"]
    assert cfg["passes"]["A"]["model"] == "" and cfg["passes"]["A"]["max_tokens"] == 16384 and cfg["concurrency"] == 4


def test_the_settings_page_s_own_requests_still_pass():
    shapes.settings({"title": "T", "short_title": "", "question": "Q", "review_type": "intervention",
                     "reviewers": [{"id": "CH", "name": "C", "role": "lead"}],
                     "criteria": {"include": ["a"], "exclude": [], "rules": []}, "ft_reasons": ["x"],
                     "highlight": {"include": [], "exclude": []}, "known_studies": [{"doi": "10.1/x"}, {"pmid": "123456"}],
                     "registers": [{"name": "ClinicalTrials.gov", "records": 14}],
                     "registration": {"registry": "", "id": "", "date": "", "url": ""},
                     "dates": {"start": "", "searches": "", "update_search": "", "end": ""},
                     "fulltext": {"mode": "dual", "library_prefix": "", "disagreement_default": ""},
                     "other_sets": {"update": {"mode": "inherit"}, "other": {"mode": "dual"}},
                     "screening": {"mode": "dual", "reviewers": ["CH"], "single_reviewer": "", "second_reviewer": "",
                                   "sample_fraction": 0.2, "kappa_threshold": 0.8, "calibration_size": 0,
                                   "ai_second_screener": False, "ai_keep_rate_stop": 0.5,
                                   "conflicts_at_title_abstract": "forward", "seed": 1}})


# ------------------------------------------------------------------------------------------------ B6
def test_a_reviewer_does_not_get_the_count_of_the_ai_s_failed_answers(lead, quick_retries):
    c = lead
    c.elm.fail("http500", when=lambda call: call.title == c.records[5]["title"] and call.model == MODEL_B)
    c.run_ai()
    assert c.get("/api/ai")["coverage"]["main"]["failed"] == 1
    c.be("NH")
    cov = c.get("/api/ai")["coverage"]["main"]
    assert "failed" not in cov and cov["done"] == 12


# ------------------------------------------------------------------------------------------------ B5
def test_a_long_unbroken_word_is_wrapped_at_once():
    t0 = time.time()
    lines = timeline._wrap("x" * 200000, 300, 13)
    assert time.time() - t0 < 3 and "".join(x.rstrip("-") for x in lines) == "x" * 200000
    assert all(timeline._tw(x, 13) <= 300 for x in lines)
    words = timeline._wrap("A short title that wraps onto two lines here", 120, 13)
    assert len(words) > 1 and " ".join(words) == "A short title that wraps onto two lines here"


# ------------------------------------------------------------------------------------------------ C2, C3, C7, C8
def test_a_year_that_is_not_a_number_does_not_spoil_the_look_up():
    assert fulltext._near_year(2021, "2020") and fulltext._near_year("2021", "n.d.") and fulltext._near_year(None, "2019")
    assert not fulltext._near_year("2015", "2021 Mar")


def test_a_found_pdf_over_the_size_limit_is_kept_as_a_link_and_not_fetched_again(lead, monkeypatch):
    c = lead
    userconfig.update(unpaywall_email="colin@example.org")
    p = Project(c.folder)
    rid = p.records()[0]["id"]
    monkeypatch.setattr(fulltext, "MAX_PDF", 100)
    monkeypatch.setattr(fulltext, "find_free_copy", lambda rec, email: {
        "status": "saved", "source": "Unpaywall", "url": "https://example.org/big.pdf", "pdf": b"%PDF-" + b"x" * 500,
        "tried": ["Unpaywall"]})
    fulltext.Fetcher.start(p, "CH", [rid])
    for _ in range(100):
        if not fulltext.Fetcher.status(p)["running"]:
            break
        time.sleep(0.05)
    assert fulltext.search_results(p)[rid]["status"] == "link_only"


def test_an_ai_address_from_the_environment_is_checked_like_a_saved_one(lead, monkeypatch):
    monkeypatch.setenv("REVIEWDESK_AI_URL", "http://ai.example.org/v1")
    with pytest.raises(ai.SetupError, match="https://.*REVIEWDESK_AI_URL"):
        ai.need_endpoint()
    e = lead.error("POST", "/api/ai/models", {})
    assert "REVIEWDESK_AI_URL" in e.message


def test_the_finder_refuses_to_run_if_python_no_longer_lets_it_check_where_a_download_goes(lead, monkeypatch):
    userconfig.update(unpaywall_email="colin@example.org")
    assert fulltext.GUARD_WORKS                          # on every Python ReviewDesk supports
    monkeypatch.setattr(fulltext, "GUARD_WORKS", False)
    with pytest.raises(fulltext.ProjectError, match="Add the PDFs by hand"):
        fulltext.Fetcher.start(Project(lead.folder), "CH", ["R00001"])
    assert fulltext.fetch_pdf("https://example.org/a.pdf") == b""


# ------------------------------------------------------------------------------------------------ start-up
def test_a_program_that_is_not_a_web_server_on_the_usual_port_does_not_stop_reviewdesk(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWDESK_HOME", str(tmp_path / "home"))
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)

    def answer():
        try:
            while True:
                conn, _ = srv.accept()
                conn.sendall(b"220 mail.example.org ESMTP ready\r\n")
                conn.close()
        except OSError:
            pass
    threading.Thread(target=answer, daemon=True).start()
    monkeypatch.setattr(server, "DEFAULT_PORT", srv.getsockname()[1])
    try:
        assert main._already_running() is None
    finally:
        srv.close()


def test_a_port_in_use_or_out_of_range_is_said_in_words(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REVIEWDESK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("REVIEWDESK_NO_REOPEN", "1")
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen(1)
    try:
        assert main.main(["--port", str(taken.getsockname()[1]), "--no-browser"]) == main.CANNOT_START
        assert "another program on this computer is using it" in " ".join(capsys.readouterr().out.split())
    finally:
        taken.close()
    with pytest.raises(SystemExit) as e:
        main.main(["--port", "70000", "--no-browser"])
    assert e.value.code == 2 and "not a port" in capsys.readouterr().err


def test_a_settings_folder_that_cannot_be_written_is_said_in_words(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REVIEWDESK_HOME", str(tmp_path / "home"))

    def refuse():
        raise OSError(30, "Read-only file system")
    monkeypatch.setattr(userconfig, "session_token", refuse)
    assert main.main(["--no-browser"]) == main.CANNOT_START
    out = " ".join(capsys.readouterr().out.split())
    assert "could not save its settings" in out and "Read-only file system" in out and "Traceback" not in out


def test_an_old_python_is_told_plainly_before_anything_else_is_read():
    start = os.path.join(HERE, "..", "app", "run_reviewdesk.py")
    src = open(start, encoding="utf-8").read()
    assert src.index("sys.version_info < (3, 9)") < src.index("from reviewdesk")
    assert "f\"" not in src and "f'" not in src and "->" not in src          # readable by Python 2.7 and 3.0 to 3.8
    old = shutil.which("python3.8")
    if old:
        r = subprocess.run([old, start], capture_output=True, text=True, timeout=60)
        assert r.returncode == 1 and r.stderr.strip().startswith("ReviewDesk needs Python 3.9 or later; this is Python 3.8")


# ------------------------------------------------------------------------------------------------ the timeline, one person
def _cards(svg: str) -> list:
    import re
    return [tuple(float(m.group(k)) for k in (1, 2, 3, 4)) for m in
            re.finditer(r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)" rx="7"', svg)]


def _no_overlap(svg: str) -> None:
    cards = _cards(svg)
    assert len(cards) > 10
    for i, (x, y, w, h) in enumerate(cards):
        for (x2, y2, w2, h2) in cards[i + 1:]:
            assert x + w <= x2 or x2 + w2 <= x or y + h <= y2 or y2 + h2 <= y, ((x, y, w, h), (x2, y2, w2, h2))


def _solo(tmp_path, monkeypatch):
    from test_go_live_fx_plan import SAM, SOLO_PLAN, make
    return make(tmp_path, monkeypatch, [SAM], SOLO_PLAN, ft_mode="single")


def test_the_timeline_of_a_one_person_review_says_one_person_does_each_step(tmp_path, monkeypatch):
    p = _solo(tmp_path, monkeypatch)
    m = timeline.project_model(p, "SS")
    steps = {x["id"]: x for x in m["steps"]}
    assert "calibration" not in steps and "sample" not in steps
    text = " ".join(" ".join(x["facts"]) for x in m["steps"])
    assert "extracted by both" not in text and "then agree" not in text and "Both " not in text
    assert steps["extract"]["facts"][0].startswith("Sam Solo extracts every study's details and outcome data alone")
    assert steps["rob"]["facts"][0].startswith("Sam Solo judges each result alone")
    assert steps["fulltext"]["facts"][0].startswith("Sam Solo assesses every full text alone")
    assert all(x["who_n"] == 1 for x in m["steps"])
    for v in ("landscape", "portrait"):
        _no_overlap(timeline.render(m, v))


def test_the_timeline_follows_the_plan_in_force_and_shows_a_round_that_cannot_be_finished(tmp_path, monkeypatch):
    from test_go_live_fx_plan import stuck_project
    p = stuck_project(tmp_path, monkeypatch)
    m = timeline.project_model(p, "SS")
    steps = {x["id"]: x for x in m["steps"]}
    assert "calibration" in steps
    assert "needs two reviewers" in steps["calibration"]["facts"][0] and "End the calibration round" in steps["calibration"]["facts"][0]
    for v in ("landscape", "portrait"):
        _no_overlap(timeline.render(m, v))
    screening_mod = __import__("reviewdesk.screening", fromlist=["x"])
    screening_mod.finish_calibration(Project(p.dir), "SS", "main")
    assert "calibration" not in {x["id"] for x in timeline.project_model(Project(p.dir), "SS")["steps"]}


def test_a_two_person_timeline_still_says_both():
    s = {"reviewers": [{"id": "CH", "name": "Dr Colin Hawthorne", "role": "lead"}, {"id": "NH", "name": "Nadine Hayes", "role": "reviewer"}],
         "screening": {"mode": "dual", "reviewers": ["CH", "NH"], "calibration_size": 50}, "fulltext": {"mode": "dual"}}
    m = timeline.model(s, 100)
    steps = {x["id"]: x for x in m["steps"]}
    assert steps["calibration"]["facts"][0].startswith("Both screen the same 50 records")
    assert steps["extract"]["facts"] == ["Outcome data are extracted by both, independently."]
    _no_overlap(timeline.render(m, "landscape"))


def test_the_key_never_goes_through_a_web_proxy_over_plain_http(desk, elm, monkeypatch):
    """A service on this computer is the only place the key may go over plain http; with a web proxy set on the
    computer, urllib sent it, unencrypted, to the proxy."""
    import urllib.request
    for k in ("no_proxy", "NO_PROXY"):                     # (a computer may list this one as reached directly)
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(ai, "_AI_OPENER", urllib.request.build_opener(
        ai._NoRedirects, urllib.request.ProxyHandler({"http": "http://127.0.0.1:9", "https": "http://127.0.0.1:9"})))
    c = desk.client()
    c.post("/api/user", {"elm_url": elm.url, "elm_key": TEST_KEY})
    assert c.post("/api/ai/models", {})["models"] and elm.model_lists == 1
