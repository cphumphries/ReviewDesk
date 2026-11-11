"""Data security: the local server answers only ReviewDesk's own pages on this computer, sends the browser's
protective headers, never serves files outside its own folder, keeps the AI key to the service it was saved for
(and encrypted on Windows), shows and follows only web addresses, never fetches from the local network, and keeps
this computer's copies of project data out of the roaming Windows profile. No real service or key is used."""
import http.client
import http.server
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request

import pytest

from reviewdesk import fulltext, server, userconfig, util
from reviewdesk import project as projmod


# ------------------------------------------------------------------------------------------------ the local server
@pytest.fixture(scope="module")
def running(tmp_path_factory):
    os.environ["REVIEWDESK_HOME"] = str(tmp_path_factory.mktemp("home"))
    httpd, url = server.serve(0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    port = httpd.server_address[1]
    tok = url.split("t=")[1]
    yield port, tok
    httpd.shutdown()


def req(port, method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": f"127.0.0.1:{port}"}
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, dict(r.getheaders()), data


def test_page_headers(running):
    port, _ = running
    st, hd, _ = req(port, "GET", "/")
    assert st == 200
    csp = hd["Content-Security-Policy"]
    for rule in ("default-src 'self'", "script-src 'self'", "connect-src 'self'", "base-uri 'none'", "form-action 'none'",
                 "frame-ancestors 'self'"):
        assert rule in csp
    assert "unsafe-inline" not in csp.split("style-src")[0]          # no inline scripts
    assert hd["Referrer-Policy"] == "no-referrer"
    assert hd["X-Frame-Options"] == "SAMEORIGIN"
    assert hd["X-Content-Type-Options"] == "nosniff"
    assert hd["Cross-Origin-Resource-Policy"] == "same-origin"


def test_api_answers_carry_strict_headers(running):
    port, tok = running
    st, hd, _ = req(port, "GET", "/api/app", {"X-Token": tok})
    assert st == 200
    assert hd["Content-Security-Policy"].startswith("default-src 'none'")
    assert hd["Cache-Control"] == "no-store" and hd["Referrer-Policy"] == "no-referrer"


def test_no_token_no_answer(running):
    port, _ = running
    assert req(port, "GET", "/api/app")[0] == 403
    assert req(port, "POST", "/api/user", {"Content-Type": "application/json"}, b"{}")[0] == 403


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "http://127.0.0.1:1"},                       # another program's page on this computer
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
])
def test_requests_started_by_other_pages_are_refused_even_with_the_token(running, headers):
    port, tok = running
    assert req(port, "GET", "/api/app", {"X-Token": tok, **headers})[0] == 403
    st, _, _ = req(port, "POST", "/api/user", {"X-Token": tok, "Content-Type": "application/json", **headers}, b'{"my_name": "x"}')
    assert st == 403


def _refused(port, headers, sent_first, send_later, wait=0.3):
    """Send a POST's headers and the first part of its body, read the whole answer, then send the rest and say that
    was all. Returns (status, what the connection gave after the answer: b"" for a clean end)."""
    import socket
    import time
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        head = "POST /api/user HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: application/json\r\n" % port
        head += "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
        s.sendall(head.encode("ascii") + b"x" * sent_first)
        got = b""
        while b"\r\n\r\n" not in got:
            got += s.recv(65536)
        top, _, body = got.partition(b"\r\n\r\n")
        length = int(re.search(rb"(?i)content-length: (\d+)", top).group(1))
        while len(body) < length:
            body += s.recv(65536)
        time.sleep(wait)                         # (were the connection broken off, the computer would know by now)
        for at in range(0, send_later, 20000):
            s.sendall(b"y" * min(20000, send_later - at))
        s.shutdown(socket.SHUT_WR)
        rest = b""
        while True:
            more = s.recv(65536)
            if not more:
                break
            rest += more
        return int(top.split()[1]), rest
    finally:
        s.close()


@pytest.mark.parametrize("why, headers, first, later, status", [
    ("no token", {"Content-Length": "60000"}, 1000, 59000, 403),
    ("a page from another site", {"X-Token": "TOKEN", "Origin": "https://evil.example", "Content-Length": "60000"}, 1000, 59000, 403),
    ("a page from an earlier session, sending a PDF", {"X-Token": "an-earlier-one", "Origin": "OWN", "Sec-Fetch-Site": "same-origin",
                                                       "Content-Length": "3000000"}, 1000, 2999000, 403),
    ("a length that is not a number", {"X-Token": "TOKEN", "Content-Length": "lots"}, 1000, 59000, 400),
    ("a file over the limit", {"X-Token": "TOKEN", "Content-Length": str(server.MAX_UPLOAD + 1)}, 1000, 2000000, 413),
])
def test_a_refused_request_is_read_to_its_end_so_that_its_answer_arrives(running, why, headers, first, later, status):
    """A request that is refused is answered before its body is read. The connection must then END, not be broken
    off with the body unread: on Windows a broken-off connection throws away the answer on its way, and the page
    would say ReviewDesk cannot be reached instead of what the answer says. So what the sender is still sending is
    read and thrown away first, and the sender sees a clean end. (Before this, sending the rest of the body here
    failed with a reset connection or a broken pipe.)"""
    port, tok = running
    headers = {k: v.replace("TOKEN", tok).replace("OWN", f"http://127.0.0.1:{port}") for k, v in headers.items()}
    got, rest = _refused(port, headers, first, later)
    assert (got, rest) == (status, b""), why


def test_a_stranger_who_sends_nothing_more_is_not_waited_for_for_long(running):
    """What is read from a sender that is not one of ReviewDesk's own pages is bounded: about two seconds, and a
    megabyte. Someone who announces a body and never sends it holds nothing up."""
    import socket
    import time
    port, _ = running
    s = socket.create_connection(("127.0.0.1", port), timeout=15)
    try:
        s.sendall(("POST /api/user HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Length: 500000\r\n\r\n" % port).encode("ascii"))
        start, got = time.monotonic(), b""
        while True:
            try:
                more = s.recv(65536)
            except ConnectionError:              # (broken off in the end, with the body never sent: that is theirs to see)
                break
            if not more:
                break
            got += more
        took = time.monotonic() - start
        assert got.startswith(b"HTTP/1.0 403") and 1.0 < took < 8.0, took
    finally:
        s.close()
    assert server.Handler.UNREAD_STRANGER == (1024 * 1024, 2.0)
    st, _, _ = req(port, "GET", "/")                                   # ... and the server answers as before
    assert st == 200


def test_reviewdesks_own_pages_are_answered(running):
    port, tok = running
    for headers in ({"Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"},
                    {"Origin": f"http://localhost:{port}"}, {"Sec-Fetch-Site": "none"}, {}):
        assert req(port, "GET", "/api/app", {"X-Token": tok, **headers})[0] == 200


def test_other_host_names_are_refused(running):
    port, tok = running
    assert req(port, "GET", "/api/app", {"X-Token": tok, "Host": "evil.example"})[0] == 403
    assert req(port, "GET", "/", {"Host": "rebind.example"})[0] == 403


@pytest.mark.parametrize("host", ["localhost.evil.example", "127.0.0.1.nip.io", "127.0.0.2", "0.0.0.0", "[::1]", "LOCALHOST.evil"])
def test_host_names_that_only_look_local_are_refused(running, host):
    port, tok = running
    assert req(port, "GET", "/api/app", {"X-Token": tok, "Host": f"{host}:{port}"})[0] == 403


@pytest.mark.parametrize("path", ["/static/../server.py", "/static/..%2fserver.py", "/static/../../reviewdesk/userconfig.py",
                                  "/static//etc/passwd", "/static/%2e%2e/server.py"])
def test_static_files_never_leave_their_folder(running, path):
    port, _ = running
    st, _, body = req(port, "GET", path)
    assert st == 404 and b"import" not in body


def test_the_key_is_never_shown_to_the_browser(running):
    port, tok = running
    userconfig.update(elm_url="https://api.example.org/v1", elm_key=userconfig.protect("sk-test-abcdefghijklmnop1234"))
    st, _, body = req(port, "GET", "/api/app", {"X-Token": tok})
    assert st == 200 and b"abcdefghijklmnop" not in body and b"sk-test" not in body
    assert json.loads(body)["user"]["elm_key_hint"] == "ending 1234"


# ------------------------------------------------------------------------------------------------ the AI key
@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWDESK_HOME", str(tmp_path / "home"))
    return tmp_path


def test_changing_the_address_removes_the_old_key(home):
    server.h_user({}, {"elm_url": "https://elm.example.ac.uk/api/v1", "elm_key": "key-for-service-one-1111"})
    assert userconfig.ai_key() == "key-for-service-one-1111"
    r = server.h_user({}, {"elm_url": "https://other.example.org/v1"})
    assert r["key_cleared"] is True and userconfig.ai_key() == ""


def test_changing_the_address_with_a_new_key_keeps_the_new_key(home):
    server.h_user({}, {"elm_url": "https://one.example.org/v1", "elm_key": "key-one-aaaaaaaaaaaa"})
    r = server.h_user({}, {"elm_url": "https://two.example.org/v1", "elm_key": "key-two-bbbbbbbbbbbb"})
    assert r["key_cleared"] is False and userconfig.ai_key() == "key-two-bbbbbbbbbbbb"


def test_saving_other_settings_keeps_the_key(home):
    server.h_user({}, {"elm_url": "https://one.example.org/v1", "elm_key": "key-one-aaaaaaaaaaaa"})
    server.h_user({}, {"elm_url": "https://one.example.org/v1/", "my_name": "Ada"})     # same address, slash added
    server.h_user({}, {"my_name": "Ada Lovelace"})
    assert userconfig.ai_key() == "key-one-aaaaaaaaaaaa"


def test_only_https_service_addresses_are_accepted(home):
    with pytest.raises(server.ApiError):
        server.h_user({}, {"elm_url": "http://api.example.org/v1", "elm_key": "key-one-aaaaaaaaaaaa"})
    server.h_user({}, {"elm_url": "http://127.0.0.1:9999/v1", "elm_key": "key-one-aaaaaaaaaaaa"})     # this computer is fine


@pytest.mark.skipif(sys.platform.startswith("win"), reason="not Windows")
def test_keys_elsewhere_are_kept_in_a_private_file(home):
    server.h_user({}, {"elm_url": "https://one.example.org/v1", "elm_key": "key-one-aaaaaaaaaaaa"})
    assert oct(os.stat(userconfig.path()).st_mode & 0o777) == "0o600"
    assert userconfig.protect("abc") == "abc" and userconfig.unprotect("abc") == "abc"


def test_a_key_encrypted_elsewhere_cannot_be_read_and_does_not_break_anything(home):
    assert userconfig.unprotect("dpapi:AAAAbm90LWEtcmVhbC1ibG9i") in ("",)


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows data protection")
def test_windows_encrypts_the_key(home):
    sealed = userconfig.protect("key-one-aaaaaaaaaaaa")
    assert sealed.startswith("dpapi:") and "key-one" not in sealed
    assert userconfig.unprotect(sealed) == "key-one-aaaaaaaaaaaa"
    server.h_user({}, {"elm_url": "https://one.example.org/v1", "elm_key": "key-one-aaaaaaaaaaaa"})
    assert "key-one" not in open(userconfig.path(), encoding="utf-8").read()
    assert userconfig.ai_key() == "key-one-aaaaaaaaaaaa"


def test_error_log_never_holds_the_key(home):
    from reviewdesk import ai
    userconfig.update(elm_url="https://one.example.org/v1", elm_key=userconfig.protect("key-one-aaaaaaaaaaaa"))
    assert "key-one-aaaaaaaaaaaa" not in ai.redact("failed with Authorization: Bearer key-one-aaaaaaaaaaaa")


# ------------------------------------------------------------------------------------------------ addresses
@pytest.mark.parametrize("u, ok", [("https://doi.org/10.1/x", True), ("http://example.org/a.pdf", True), ("HTTPS://X.ORG", True),
                                   ("javascript:alert(1)", False), (" javascript:alert(1)", False), ("data:text/html,x", False),
                                   ("file:///etc/passwd", False), ("//evil.example", False), ("http:/x", False),
                                   ("https://x.org/a b", False), ("https://x.org/\x00", False), ("", False), (None, False)])
def test_only_web_addresses(u, ok):
    assert bool(util.web_url(u)) is ok


def test_links_from_imported_records_are_cleaned(home):
    p = projmod.Project.create(str(home / "proj"), "Test", "Ada Lovelace", "AL")
    rec = {"id": "R00001", "title": "A title", "doi": "10.1/x", "pmid": "123", "url": "javascript:fetch('//evil')"}
    links = fulltext.links(p, rec)
    assert links["url"] == "" and links["doi"] == "https://doi.org/10.1/x"
    assert all(v == "" or v.startswith("https://") for v in links.values())


@pytest.mark.parametrize("url", ["http://127.0.0.1/x.pdf", "http://localhost/x.pdf", "http://10.0.0.1/x.pdf",
                                 "http://192.168.1.1/x.pdf", "http://169.254.169.254/latest/meta-data", "http://[::1]/x",
                                 "http://172.16.0.5/x.pdf", "http://0.0.0.0/x.pdf"])
def test_the_pdf_finder_never_reaches_this_computer_or_the_local_network(url, monkeypatch):
    monkeypatch.delenv("REVIEWDESK_OA_BASE", raising=False)
    assert fulltext.public_address(url) is False
    assert fulltext.fetch_pdf(url) == b""


@pytest.mark.parametrize("url", ["http://2130706433/x.pdf", "http://127.1/x.pdf",
                                 "http://0x7f.0.0.1/x.pdf", "http://[::ffff:127.0.0.1]/x.pdf", "http://[fe80::1]/x.pdf",
                                 "http://[fc00::1]/x.pdf", "http://100.64.0.1/x.pdf", "http://127.0.0.1.:8080/x.pdf",
                                 "http://user@127.0.0.1/x.pdf", "http://no-such-host.invalid/x.pdf"])
def test_other_ways_of_writing_a_local_address_are_refused(url, monkeypatch):
    monkeypatch.delenv("REVIEWDESK_OA_BASE", raising=False)
    assert fulltext.public_address(url) is False


def test_an_address_that_systems_read_differently_is_judged_by_what_this_system_makes_of_it(monkeypatch):
    """0177.0.0.1 is this computer (127.0.0.1) where a number that starts with 0 is read as octal, as Linux reads it; the
    public address 177.0.0.1 where it is read as decimal, as a Mac reads it; and no address at all on Windows. ReviewDesk
    asks the system that will make the connection (and checks again the address the connection reached): so the
    address is fetched only where it is a public one, and never where it leads back to this computer."""
    import socket
    monkeypatch.delenv("REVIEWDESK_OA_BASE", raising=False)
    try:
        reached = {i[4][0] for i in socket.getaddrinfo("0177.0.0.1", None)}
    except OSError:
        reached = set()
    assert reached in (set(), {"127.0.0.1"}, {"177.0.0.1"})
    assert fulltext.public_address("http://0177.0.0.1/x.pdf") is (reached == {"177.0.0.1"})


def test_a_public_address_is_allowed(monkeypatch):
    monkeypatch.delenv("REVIEWDESK_OA_BASE", raising=False)
    assert fulltext.public_address("http://93.184.216.34/paper.pdf") is True     # a public address (no DNS needed)


@pytest.fixture
def local_server():
    class H(http.server.BaseHTTPRequestHandler):
        hits = 0
        auth = []

        def _answer(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))       # (read what was sent, then answer)
            H.hits += 1
            H.auth.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.end_headers()
            self.wfile.write(b"%PDF-1.4 secret intranet document")
        do_GET = do_POST = _answer

        def log_message(self, *a):
            pass
    s = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s, H
    s.shutdown()


def test_a_pdf_on_this_computer_is_not_fetched(local_server, monkeypatch):
    s, H = local_server
    monkeypatch.delenv("REVIEWDESK_OA_BASE", raising=False)
    assert fulltext.fetch_pdf(f"http://127.0.0.1:{s.server_address[1]}/paper.pdf") == b""
    assert H.hits == 0


def test_redirects_to_local_addresses_are_refused(monkeypatch):
    monkeypatch.delenv("REVIEWDESK_OA_BASE", raising=False)
    h = fulltext._PublicRedirects()
    r = urllib.request.Request("http://93.184.216.34/a.pdf")
    for target in ("http://127.0.0.1/secret.pdf", "file:///etc/passwd", "http://192.168.0.1/"):
        with pytest.raises(urllib.error.URLError):
            h.redirect_request(r, None, 302, "Found", {}, target)


def test_a_name_that_changes_to_a_local_address_is_still_not_fetched(local_server, monkeypatch):
    """The address is checked when it is looked up and again once connected, so a name that gives a public address
    first and a local one a moment later still reaches nothing."""
    s, H = local_server
    monkeypatch.delenv("REVIEWDESK_OA_BASE", raising=False)
    monkeypatch.setattr(fulltext, "_OPENER", fulltext._build_opener())
    monkeypatch.setattr(fulltext, "public_address", lambda url: True)        # the first look-up said "public"
    with pytest.raises(urllib.error.URLError):
        fulltext.fetch_pdf(f"http://127.0.0.1:{s.server_address[1]}/paper.pdf")
    assert H.hits == 0


def test_the_ai_key_never_follows_a_redirect(home, local_server, monkeypatch):
    """Python passes a request's headers on when it follows a redirect, even to another site: an AI service address
    that redirects must stop with a message, and the key must reach nothing else."""
    from reviewdesk import ai
    s, H = local_server
    monkeypatch.setattr(ai, "_AI_OPENER", urllib.request.build_opener(ai._NoRedirects))

    class R(http.server.BaseHTTPRequestHandler):
        def _go(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))       # (read what was sent, then answer)
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{s.server_address[1]}/elsewhere")
            self.end_headers()
        do_GET = do_POST = _go

        def log_message(self, *a):
            pass
    r = http.server.HTTPServer(("127.0.0.1", 0), R)
    threading.Thread(target=r.serve_forever, daemon=True).start()
    try:
        server.h_user({}, {"elm_url": f"http://127.0.0.1:{r.server_address[1]}/v1", "elm_key": "test-key-not-real-0000"})
        with pytest.raises(ai.SetupError, match="does not follow it"):
            ai.list_models()
        with pytest.raises(ai.SetupError):
            ai._request(*ai.need_endpoint(), "/chat/completions", {"model": "m", "messages": []})
        assert H.hits == 0 and not any(H.auth)
    finally:
        r.shutdown()


@pytest.mark.parametrize("rid", ["../reviewdesk-project", "..\\reviewdesk-project", "R00001/../../x", "/etc/passwd", "", "R1"])
def test_pdf_file_names_are_only_ever_record_ids(home, rid):
    p = projmod.Project.create(str(home / "proj"), "Test", "Ada Lovelace", "AL")
    with pytest.raises(projmod.ProjectError):
        fulltext.pdf_path(p, rid)
    assert fulltext.pdf_path(p, "R00001").endswith("R00001.pdf")


# ------------------------------------------------------------------------------------------------ local copies
def test_local_copies_move_out_of_the_roaming_profile(tmp_path, monkeypatch):
    monkeypatch.delenv("REVIEWDESK_HOME", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    old = tmp_path / "Roaming" / "ReviewDesk" / "projects" / "abc"
    old.mkdir(parents=True)
    (old / "mirror.json").write_text("{}")
    new = userconfig.data_dir()
    assert new == str(tmp_path / "Local" / "ReviewDesk")
    assert (tmp_path / "Local" / "ReviewDesk" / "projects" / "abc" / "mirror.json").is_file()
    assert not (tmp_path / "Roaming" / "ReviewDesk" / "projects").exists()
    assert userconfig.config_dir() == str(tmp_path / "Roaming" / "ReviewDesk")     # settings stay where they were


def test_project_copies_use_the_local_data_folder(home):
    p = projmod.Project.create(str(home / "proj"), "Test", "Ada Lovelace", "AL")
    assert p._local("mirror", "x").startswith(userconfig.data_dir())
