"""Every page opens, for the project lead and for a reviewer, in a new project and in a finished demonstration review.

A page whose code fails shows the error in its own box instead of its heading, without any console error (1.3.0's
PRISMA and reports page once showed only "STATE_WORD is not defined"), so each page must show its heading, and no
console error, page error or Content-Security-Policy violation may happen on the way."""
import json
import threading
import urllib.request

import pytest

from reviewdesk import server

ROUTES = ["overview", "timeline", "setup", "wizard", "settings", "searches", "import", "duplicates", "screening", "ai",
          "fulltext", "extract", "certainty", "report"]


@pytest.fixture
def running(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWDESK_HOME", str(tmp_path / "home"))
    httpd, url = server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base, tok = url.split("/?")[0], url.split("t=")[1]

    def call(path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"X-Token": tok, "Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    yield base, url, call
    httpd.shutdown()


def _visit_all(base, url, who):
    pw = pytest.importorskip("playwright.sync_api")
    problems = []
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                      # noqa: BLE001 - no browser on this computer
            pytest.skip(f"Chromium is not available: {e}")
        pg = b.new_page(viewport={"width": 1366, "height": 900})
        msgs = []
        pg.on("console", lambda m: msgs.append(m.text) if m.type == "error" and "favicon" not in m.text else None)
        pg.on("pageerror", lambda e: msgs.append("page error: " + str(e)))
        pg.goto(url)
        pg.wait_for_selector("h1")
        for r in ROUTES:
            msgs.clear()
            pg.goto(f"{base}/#/{r}")
            pg.reload()
            pg.evaluate("document.addEventListener('securitypolicyviolation', e => console.error('CSP ' + e.violatedDirective))")
            try:
                pg.wait_for_selector("main h1, .page h1", timeout=8000)
            except Exception:                       # noqa: BLE001 - reported below with what the page shows instead
                shown = pg.evaluate("(document.querySelector('.callout.bad')||document.body).innerText.slice(0, 300)")
                problems.append(f"{who} #/{r}: no heading; the page shows {shown!r}")
                continue
            pg.wait_for_timeout(300)
            if msgs:
                problems.append(f"{who} #/{r}: {msgs}")
        b.close()
    return problems


def test_every_page_of_a_new_project_opens(running, tmp_path):
    base, url, call = running
    call("/api/project/create", {"parent": str(tmp_path), "name": "new", "title": "A new review", "lead_name": "Ada Lovelace",
                                 "lead_id": "AL"})
    assert _visit_all(base, url, "lead") == []


def test_every_page_of_a_finished_review_opens_for_the_lead_and_the_reviewer(running, tmp_path):
    import demo_review
    base, url, call = running
    info = demo_review.build(str(tmp_path / "demo"))
    demo_review.grade(info)
    call("/api/project/open", {"path": info["folder"]})
    problems = []
    for who in ("CH", "NH"):
        call("/api/me", {"reviewer": who})
        problems += _visit_all(base, url, who)
    assert problems == []
