"""Imported text stays text, in the browser. Static analysis points at the one place the pages set markup (the "html"
attribute of h() in static/lib.js), which the screening page uses to show a record's title and abstract with the
team's highlight words marked. A record comes from a database export, and a highlight word from a settings file in a
shared folder: neither may ever become markup, a link or a script.

The test runs ReviewDesk as its own program and drives Chromium; it skips when Playwright or Chromium is not
installed."""
from __future__ import annotations

import os

import pytest

import helpers
from helpers import reference_plan, reference_project, ris, tiny_records

pw_api = pytest.importorskip("playwright.sync_api")

TITLE = ('Triage <img src=x onerror="window.pwned=1"> & <script>window.pwned=2</script> "acuity" in <b>children</b> '
         "&lt;not a tag&gt; &amp; <a href=\"javascript:window.pwned=3\">sepsis</a>")
ABSTRACT = ('Background: </p></div><svg onload="window.pwned=4"><iframe src="javascript:window.pwned=5"></iframe> triage of '
            'children <style>*{display:none}</style> &quot;quoted&quot; and <mark class="hi-ex">not ours</mark>.')
WORDS_IN = ["triage", 'acuity" onmouseover="window.pwned=6', "<b>children</b>", "child*", "<img", "&amp;"]
WORDS_EX = ["<script>", "svg onload", "sepsis</a>", "(.*)+$", "[a-", "\\"]


@pytest.fixture
def browser():
    with pw_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                      # noqa: BLE001 - no browser on this computer
            pytest.skip(f"Chromium is not available: {e}")
        yield b
        b.close()


def test_a_record_s_text_and_the_highlight_words_never_become_markup(tmp_path, browser):
    desk = helpers.LocalServer(str(tmp_path / "home-setup"))
    srv = None
    try:
        c = desk.client()
        folder = reference_project(c, str(tmp_path / "OneDrive"), screening=reference_plan(calibration_size=0),
                                   highlight={"include": WORDS_IN, "exclude": WORDS_EX})
        recs = tiny_records(5, seed=11)
        for k, r in enumerate(recs):                 # every record is hostile, so whichever comes first is
            r["title"], r["abstract"] = f"{TITLE} ({k + 1})", f"{ABSTRACT} ({k + 1})"
        c.add_import(ris(*recs), "medline.ris", "MEDLINE")
        c.process()
        c.post("/api/screening/start", {"set": "main"})
        task = c.tasks()[0]
        kept = {}                                    # the records as ReviewDesk stored them
        for x in c.queue(task["task"]):
            got = c.get("/api/record", id=x["id"])
            kept[x["id"]] = got.get("record", got)
        assert all("<img" in r["title"] and "<svg" in r["abstract"] for r in kept.values())     # (text is kept as text)
        desk.stop()

        srv = helpers.ServerProcess(os.path.join(str(tmp_path), "home-ch"))
        me = srv.client("CH")
        me.open_project(folder)
        me.be("CH")
        errors = []
        pg = browser.new_page(viewport={"width": 1366, "height": 900})
        pg.set_default_timeout(10000)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("dialog", lambda d: (errors.append("a dialog opened: " + d.message), d.dismiss()))
        pg.goto(f"http://127.0.0.1:{srv.port}/?t={srv.token}")
        pg.wait_for_selector("h1")
        pg.evaluate("r => { location.hash = '#/' + r; }", f"screen/{task['task']}/main")
        pg.wait_for_selector("h2.rec-title")
        pg.wait_for_timeout(600)

        shown = pg.evaluate("""() => {
          const title = document.querySelector("h2.rec-title"), abs = document.querySelector(".abstract");
          const inside = (el) => [...el.querySelectorAll("*")].map((x) => x.tagName + (x.tagName === "MARK" ? "." + x.className : ""));
          const attrs = (el) => [...el.querySelectorAll("*")].flatMap((x) => [...x.attributes].map((a) => a.name));
          return { title: title.textContent, abstract: abs.textContent, titleTags: inside(title), absTags: inside(abs),
                   attrs: [...attrs(title), ...attrs(abs)], pwned: window.pwned === undefined ? null : window.pwned,
                   marks: [...document.querySelectorAll("h2.rec-title mark, .abstract mark")].map((m) => [m.className, m.textContent]) };
        }""")
        # the words on the page are the record's own, character for character
        norm = lambda s: " ".join(s.split())                                          # noqa: E731
        rec = next((r for r in kept.values() if norm(r["title"]) == norm(shown["title"])), None)
        assert rec is not None, (shown["title"], [r["title"] for r in kept.values()][:1])
        assert norm(shown["abstract"]) == norm(rec["abstract"])
        # nothing in them became an element: the only elements are the highlights (and the abstract's paragraphs)
        assert set(shown["titleTags"]) <= {"MARK.hi-in", "MARK.hi-ex"}, shown["titleTags"]
        assert set(shown["absTags"]) <= {"P", "MARK.hi-in", "MARK.hi-ex"}, shown["absTags"]
        assert set(shown["attrs"]) <= {"class"}, shown["attrs"]                       # no handler, no address, no style
        assert shown["pwned"] is None and errors == []
        # ... and the highlight words work as words, the awkward ones included
        marked = {(c_, t.lower()) for c_, t in shown["marks"]}
        assert ("hi-in", "triage") in marked and ("hi-in", "children") in marked
        assert ("hi-in", "<img") in marked and ("hi-ex", "sepsis</a>") in marked and ("hi-ex", "svg onload") in marked
        assert all(c_ in ("hi-in", "hi-ex") for c_, _ in marked)
        pg.close()
    finally:
        desk.stop()
        if srv:
            srv.stop()
