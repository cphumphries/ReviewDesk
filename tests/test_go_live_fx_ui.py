"""The pages in the browser (final audit, fixer fx-ui): what a person chose or typed is never lost to a redraw or a
refused save, long text never pushes a page sideways, the accessibility points axe found, listeners released when a
page is left, and a plain message with a way out when a page cannot be opened.

Each test runs ReviewDesk as its own program (one per person) on the finished demonstration review and drives
Chromium; they skip when Playwright or Chromium is not installed."""
from __future__ import annotations

import json
import os
import re

import pytest

import demo_review
import helpers

pw_api = pytest.importorskip("playwright.sync_api")

LONGWORD = "Pneumonoultramicroscopicsilicovolcanoconiosis" * 6          # 270 letters, no place to break


class Person:
    def __init__(self, root, who, folder):
        self.home = os.path.join(root, "home-" + who.lower())
        self.srv = helpers.ServerProcess(self.home)
        self.c = self.srv.client(who)
        self.c.open_project(folder)
        self.c.be(who)
        self.base = f"http://127.0.0.1:{self.srv.port}"
        self.url = f"{self.base}/?t={self.srv.token}"


@pytest.fixture
def review(tmp_path):
    folder = str(tmp_path / "project")
    with helpers.on_computer(str(tmp_path / "home-ch")):
        info = demo_review.build(folder)
        demo_review.grade(info)
    people = {}
    try:
        people["CH"] = Person(str(tmp_path), "CH", folder)
        people["NH"] = Person(str(tmp_path), "NH", folder)
        yield info, people
    finally:
        for x in people.values():
            x.srv.stop()


@pytest.fixture
def browser():
    with pw_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                      # noqa: BLE001 - no browser on this computer
            pytest.skip(f"Chromium is not available: {e}")
        yield b
        b.close()


def open_page(browser, person, route="overview", width=1366, height=900, errors=None, wait="h1"):
    pg = browser.new_page(viewport={"width": width, "height": height})
    pg.set_default_timeout(10000)
    if errors is not None:
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "status of" not in m.text else None)
    pg.goto(person.url)
    pg.wait_for_selector("h1")
    go(pg, route, wait)
    return pg


def go(pg, route, wait="h1"):
    pg.evaluate("r => { location.hash = '#/' + r; }", route)
    pg.wait_for_timeout(150)
    pg.wait_for_selector(wait)
    quiet(pg)


def quiet(pg, ms=400):
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(ms)


def toast(pg):
    return pg.evaluate("(() => { const t = document.getElementById('toast'); return t.className ? t.textContent : ''; })()")


# ------------------------------------------------------------------------------------------------ F1: redraws keep choices
def _rob_to_agree(info, ch):
    """An assessment both reviewers finished, differing on every domain (as t_lost.py sets it up)."""
    key = info["key"]["tricky"]
    c = ch.c
    for f, v in (("country", "Ireland"), ("design", "Randomised trial"), ("setting", "Two emergency departments")):
        c.post("/api/extract/value", {"key": key, "f": f, "v": v, "pg": "p. 4"})
    seen = c.get("/api/extract/study", key=key)["state"]["seen"]
    c.post("/api/extract/done", {"key": key, "done": True, "seen": seen})
    st = c.get("/api/extract/study", key=key)
    a = next(x for x in st["rob"] if not x["both_done"])
    for dm in a["domains"]:
        c.post("/api/rob/judge", {"key": key, "a": a["a"], "d": dm["d"], "j": dm["scale"][-1] if dm["d"] in ("D1", "D2", "D3", "O") else dm["scale"][0], "q": "support"})
    c.post("/api/rob/done", {"key": key, "a": a["a"], "done": True})
    return key, a["a"]


def test_saving_one_agreed_judgement_keeps_the_others_chosen(review, browser):
    info, people = review
    ch = people["CH"]
    key, aid = _rob_to_agree(info, ch)
    pg = open_page(browser, ch, f"x/{key}/rob")
    sels = pg.locator(".rob-table .agree select")
    n = sels.count()
    assert n >= 3
    for k in range(n):
        sels.nth(k).select_option("Low risk")
    assert pg.locator(".rob-table .unsaved-note:not([hidden])").count() == n
    pg.locator(".rob-table .agree").first.get_by_role("button", name=re.compile("^Save the agreed judgement")).click()
    quiet(pg, 700)
    sels = pg.locator(".rob-table .agree select")
    assert [sels.nth(k).input_value() for k in range(sels.count())] == ["Low risk"] * (n - 1)
    assert pg.locator(".rob-table .unsaved-note:not([hidden])").count() == n - 1       # still said: not saved yet
    pg.locator(".rob-table .agree").first.get_by_role("button", name=re.compile("^Save the agreed judgement")).click()
    quiet(pg, 700)
    a = next(x for x in ch.c.get("/api/extract/study", key=key)["rob"] if x["a"] == aid)
    finals = [d.get("final") for d in a["domains"] if d.get("a") != d.get("b")]
    assert finals[:2] == ["Low risk", "Low risk"]


def test_saving_one_agreed_value_keeps_the_others_typed(review, browser):
    info, people = review
    ch = people["CH"]
    key, _ = _rob_to_agree(info, ch)
    pg = open_page(browser, ch, f"x/{key}/compare")
    panels = pg.locator(".panel.cmp:not(.settled)")
    n = panels.count()
    assert n >= 2
    typed = []
    for k in range(n):
        box = panels.nth(k).locator("input[type=text], textarea, select").first
        if box.evaluate("e => e.tagName") == "SELECT":
            box.select_option(index=1)
        else:
            box.fill(f"Agreed value {k + 1}")
        typed.append(box.input_value())
    panels.nth(0).get_by_role("button", name="Save the agreed value").click()
    quiet(pg, 700)
    left = pg.locator(".panel.cmp:not(.settled)")
    assert [left.nth(k).locator("input[type=text], textarea, select").first.input_value() for k in range(left.count())] == typed[1:]
    assert left.nth(0).locator(".unsaved-note").is_visible()


def test_saving_one_agreed_rating_keeps_the_other_explanations(review, browser):
    info, people = review
    ch, nh = people["CH"], people["NH"]
    oid = ch.c.post("/api/certainty/define", {"o": "", "outcome": {"name": "Length of stay", "importance": "important", "kind": "effect",
                    "comparison": "AI triage compared with usual triage", "measure": "Hours", "synthesis": "narrative", "participants": "", "studies": [info["key"]["taylor"]]}})["o"]
    for cl, who, downs in ((ch.c, "CH", {"rob": -1, "inconsistency": -1, "indirectness": 0, "imprecision": -1, "publication": 0}),
                           (nh.c, "NH", {"rob": 0, "inconsistency": 0, "indirectness": 0, "imprecision": -2, "publication": 0})):
        cl.post("/api/certainty/rate", {"o": oid, "f": "start", "v": "rct", "x": "", "as": who})
        for f, v in downs.items():
            cl.post("/api/certainty/rate", {"o": oid, "f": f, "v": v, "x": "Because of the one trial" if v else "", "as": who})
        cl.post("/api/certainty/rate", {"o": oid, "f": "effect", "v": f"{who}: about an hour shorter", "x": "", "as": who})
        cl.post("/api/certainty/rate", {"o": oid, "f": "statement", "v": "AI triage may shorten the stay.", "x": "", "as": who})
        cl.post("/api/certainty/done", {"o": oid, "done": True, "as": who})
    pg = open_page(browser, ch, f"c/{oid}/compare")
    panels = pg.locator(".panel.cmp:not(.settled)")
    n = panels.count()
    assert n >= 3
    for k in range(n):
        p = panels.nth(k)
        if p.locator("select").count():
            p.locator("select").first.select_option(index=2)
        p.locator("textarea").first.fill(f"Agreed explanation {k + 1}")
    panels.nth(0).locator("button.primary").click()
    quiet(pg, 800)
    left = pg.locator(".panel.cmp:not(.settled)")
    assert [left.nth(k).locator("textarea").first.input_value() for k in range(left.count())] == [f"Agreed explanation {k + 1}" for k in range(1, n)]


# ------------------------------------------------------------------------------------------------ F2: refused saves
REFUSED = json.dumps({"error": "ReviewDesk could not write to the project folder just now: another program (often OneDrive, while it syncs) has the file open. Wait a moment and try again."})


def test_answers_whose_save_was_refused_are_kept_retried_and_stop_the_study_being_finished(review, browser):
    info, people = review
    nh = people["NH"]
    key = info["key"]["tricky"]
    pg = open_page(browser, nh, "x/" + key)
    pg.get_by_role("button", name="Reopen to change an answer").click()
    quiet(pg, 700)
    refuse = {"on": True}

    def handler(route):
        body = json.loads(route.request.post_data or "{}")
        if refuse["on"] and body.get("f") in ("country", "population"):
            route.fulfill(status=409, content_type="application/json", body=REFUSED)
        else:
            route.continue_()
    pg.route("**/api/extract/value", handler)
    pg.fill("#x-country", "Aotearoa New Zealand")
    pg.locator("#x-country").blur()
    pg.fill("#x-population", "Adults attending two urban emergency departments")
    pg.locator("#x-population").blur()
    pg.wait_for_timeout(600)
    # the long-answer box is marked the same way as the short one, in colour and in words
    for sel in ("#x-country", "#x-population"):
        assert pg.get_attribute(sel, "aria-invalid") == "true"
        assert pg.evaluate("s => getComputedStyle(document.querySelector(s)).borderTopColor", sel) == "rgb(164, 47, 73)"
    assert pg.locator(".xfield .saved.notsaved").count() == 2
    # finishing is refused, naming the answers
    pg.get_by_role("button", name="Mark this study as finished").click()
    quiet(pg, 800)
    said = toast(pg)
    assert "not marked as finished" in said and "Country" in said
    st = nh.c.get("/api/extract/study", key=key)["state"]
    assert not st["done_first"]
    # once the folder can be written again, the next save sends them too, and finishing works
    refuse["on"] = False
    pg.fill("#x-funding", "Health Research Council")
    pg.locator("#x-funding").blur()
    pg.wait_for_timeout(1200)
    mine = nh.c.get("/api/extract/study", key=key)["state"]["mine"]
    assert mine["country"]["v"] == "Aotearoa New Zealand"
    assert mine["population"]["v"] == "Adults attending two urban emergency departments"
    assert pg.get_attribute("#x-population", "aria-invalid") is None
    pg.get_by_role("button", name="Mark this study as finished").click()
    if pg.locator(".modal").count():
        pg.locator(".modal button.primary").click()
    quiet(pg, 800)
    assert nh.c.get("/api/extract/study", key=key)["state"]["done_first"]


def test_leaving_a_study_with_a_refused_save_asks_first(review, browser):
    info, people = review
    nh = people["NH"]
    key = info["key"]["tricky"]
    pg = open_page(browser, nh, "x/" + key)
    pg.get_by_role("button", name="Reopen to change an answer").click()
    quiet(pg, 700)
    pg.route("**/api/extract/value", lambda r: r.fulfill(status=409, content_type="application/json", body=REFUSED))
    pg.fill("#x-country", "Typed while OneDrive had the file")
    pg.locator("#x-country").blur()
    pg.wait_for_timeout(500)
    pg.get_by_role("button", name="← All studies").click()
    pg.wait_for_selector(".modal")
    assert "not saved" in pg.inner_text(".modal h2")
    assert "Country" in pg.inner_text(".modal")
    assert "#/x/" in pg.evaluate("location.hash")


def test_the_not_running_message_goes_once_reviewdesk_answers_again(review, browser):
    info, people = review
    nh = people["NH"]
    key = info["key"]["tricky"]
    pg = open_page(browser, nh, "x/" + key)
    pg.get_by_role("button", name="Reopen to change an answer").click()
    quiet(pg, 700)
    down = {"on": True}
    pg.route("**/api/extract/value", lambda r: r.abort("connectionrefused") if down["on"] else r.continue_())
    pg.fill("#x-country", "Typed while ReviewDesk was closed")
    pg.locator("#x-country").blur()
    pg.wait_for_timeout(500)
    assert toast(pg).startswith("ReviewDesk is not running")
    down["on"] = False
    pg.fill("#x-funding", "Typed after it was started again")
    pg.locator("#x-funding").blur()
    pg.wait_for_timeout(1200)
    assert not toast(pg).startswith("ReviewDesk is not running")
    mine = nh.c.get("/api/extract/study", key=key)["state"]["mine"]
    assert mine["country"]["v"] == "Typed while ReviewDesk was closed"          # what was typed meanwhile is sent too


def test_a_refused_risk_of_bias_judgement_stops_the_assessment_being_finished(review, browser):
    info, people = review
    ch = people["CH"]
    key = info["key"]["tricky"]
    pg = open_page(browser, ch, f"x/{key}/rob")
    pg.route("**/api/rob/judge", lambda r: r.fulfill(status=409, content_type="application/json", body=REFUSED))
    sel = pg.locator(".panel.rob .domain select").first
    sel.select_option(index=1)
    pg.wait_for_timeout(500)
    assert sel.get_attribute("aria-invalid") == "true"
    pg.get_by_role("button", name="Mark this assessment as finished").click()
    quiet(pg, 700)
    assert "not marked as finished" in toast(pg)
    a = next(x for x in ch.c.get("/api/extract/study", key=key)["rob"] if not x["both_done"])
    assert not a["done_mine"]


def test_a_refused_grade_rating_stops_the_outcome_being_finished(review, browser):
    info, people = review
    ch = people["CH"]
    oid = ch.c.post("/api/certainty/define", {"o": "", "outcome": {"name": "Admissions", "importance": "important", "kind": "effect",
                    "comparison": "AI triage compared with usual triage", "measure": "Risk ratio", "synthesis": "narrative", "participants": "", "studies": [info["key"]["taylor"]]}})["o"]
    pg = open_page(browser, ch, f"c/{oid}/rate")
    pg.route("**/api/certainty/rate", lambda r: r.fulfill(status=409, content_type="application/json", body=REFUSED))
    pg.fill("#c-effect", "About an hour shorter")
    pg.locator("#c-effect").blur()
    pg.wait_for_timeout(500)
    assert pg.get_attribute("#c-effect", "aria-invalid") == "true"
    pg.get_by_role("button", name="Mark this outcome as finished").click()
    quiet(pg, 600)
    assert "not marked as finished" in pg.inner_text(".xdone .callout.bad")


# ------------------------------------------------------------------------------------------------ notes and worksheets
def test_leaving_a_full_text_report_with_an_unsaved_note_asks_first(review, browser):
    info, people = review
    ch = people["CH"]
    rows = [r["id"] for r in ch.c.get("/api/fulltext")["rows"]]
    pg = open_page(browser, ch, "ft/" + rows[1])
    pg.fill("aside.side textarea", "Fails criterion 2 (Methods, p. 3)")
    pg.get_by_role("button", name="Next report").click()
    pg.wait_for_selector(".modal")
    pg.locator(".modal").get_by_role("button", name="Cancel").click()
    assert pg.input_value("aside.side textarea") == "Fails criterion 2 (Methods, p. 3)"
    pg.get_by_role("button", name="Next report").click()
    pg.wait_for_selector(".modal")
    pg.locator(".modal").get_by_role("button", name="Save and go").click()
    quiet(pg, 700)
    rec = ch.c.get("/api/record", id=rows[1])
    assert rec["mine"]["ft"]["note"] == "Fails criterion 2 (Methods, p. 3)"


def test_a_screening_note_typed_and_not_voted_on_is_kept_for_its_record(tmp_path, browser):
    folder = str(tmp_path / "project")
    with helpers.on_computer(str(tmp_path / "home-ch")):
        demo_review.build_open_sample(folder)
    ch = Person(str(tmp_path), "CH", folder)
    try:
        pg = open_page(browser, ch, "screen/sample/main", wait="h2.rec-title")
        first = pg.inner_text("h2.rec-title")
        pg.fill("aside.side textarea", "Check the setting: wards or emergency department?")
        pg.locator("button[aria-label='Next record']").click()
        quiet(pg, 300)
        assert pg.inner_text("h2.rec-title") != first
        pg.locator("button[aria-label='Previous record']").click()
        quiet(pg, 300)
        assert pg.inner_text("h2.rec-title") == first
        assert pg.input_value("aside.side textarea") == "Check the setting: wards or emergency department?"
        assert pg.locator("#scr-note-unsaved").is_visible()
    finally:
        ch.srv.stop()


def test_the_search_worksheet_is_kept_while_a_search_is_recorded_and_leaving_asks(review, browser):
    info, people = review
    ch = people["CH"]
    pg = open_page(browser, ch, "searches/build")
    before = pg.locator("fieldset legend", has_text="Concept").count()
    pg.get_by_role("button", name="Add a concept").click()
    fs = pg.locator("fieldset").filter(has_text=f"Concept {before + 1}")
    fs.get_by_label("Name").fill("Triage")
    # the log tab, a search recorded (the page is drawn again from the server): the concept is still there
    pg.get_by_role("tab", name=re.compile("^Search log")).click()
    pg.get_by_role("button", name="Record a search").click()
    pg.get_by_label("Database", exact=True).fill("CINAHL")
    pg.get_by_label("The exact search, as run").fill("1. triag*")
    pg.locator(".modal button.primary").click()
    quiet(pg, 600)
    pg.get_by_role("tab", name="Build the search").click()
    assert pg.locator("fieldset legend", has_text="Concept").count() == before + 1
    pg.locator("nav.nav a", has_text="Overview").click()
    pg.wait_for_selector(".modal")
    assert "worksheet" in pg.inner_text(".modal h2")


def test_save_settings_keeps_the_risk_of_bias_plan_ticks_and_says_what_to_do(tmp_path, browser):
    parent = str(tmp_path / "p")
    os.makedirs(parent)
    home = str(tmp_path / "home-ch")
    srv = helpers.ServerProcess(home)
    try:
        c = srv.client("CH")
        c.create_project(parent)
        c.be("CH")
        person = type("P", (), {})()
        person.c, person.base = c, f"http://127.0.0.1:{srv.port}"
        person.url = f"{person.base}/?t={srv.token}"
        pg = open_page(browser, person, "setup")
        rows = pg.locator("#setup-robplan .rp-row input[type=checkbox]")
        rows.first.check()
        pg.locator("#setup-review input[type=text]").first.fill("A changed title")
        pg.get_by_role("button", name="Save settings").click()
        quiet(pg, 800)
        assert pg.locator("#setup-robplan .rp-row input[type=checkbox]").first.is_checked()
        assert "risk-of-bias plan" in toast(pg)
        assert pg.evaluate("document.activeElement.textContent") == "Record the risk-of-bias plan"
        assert c.get("/api/app")["project"]["title"] == "A changed title" or c.get("/api/app")["project"]["settings"]["title"] == "A changed title"
        assert not c.get("/api/rob/plan")["recorded"]
    finally:
        srv.stop()


# ------------------------------------------------------------------------------------------------ F3: long text
def test_long_unbroken_text_does_not_push_pages_sideways(review, browser):
    info, people = review
    ch = people["CH"]
    ch.c.settings(question="In emergency departments, " + LONGWORD,
                  registration={"registry": "PROSPERO", "id": "CRD42" + "0" * 80, "date": "2026-09-01", "url": "https://example.org/" + "a" * 200},
                  criteria={"include": [LONGWORD], "exclude": ["Retrospective studies"], "rules": []})
    wide = {}
    for width in (320, 1366):
        pg = open_page(browser, ch, "overview", width=width, height=700)
        for r in ("overview", "setup", "searches/log", "duplicates", "screening", "wizard/registration"):
            go(pg, r)
            wide[f"{width} {r}"] = pg.evaluate("document.scrollingElement.scrollWidth - document.documentElement.clientWidth")
        pg.close()
    assert {k: v for k, v in wide.items() if v > 0} == {}


# ------------------------------------------------------------------------------------------------ F4: accessibility
def test_names_contain_the_visible_text_and_small_targets_are_big_enough(review, browser):
    info, people = review
    ch = people["CH"]
    pg = open_page(browser, ch, "report")
    labels = pg.evaluate("[...document.querySelectorAll('.rep-files button')].map(b => [b.textContent.trim(), b.getAttribute('aria-label') || ''])")
    assert labels and all(name.startswith(text) for text, name in labels)
    go(pg, "wizard/review")
    assert pg.locator(".wz-steps button[aria-label]").count() == 0
    names = pg.evaluate("[...document.querySelectorAll('.wz-steps button')].map(b => [b.querySelector('.l').textContent, b.textContent])")
    assert names and all(v in name and name.startswith("Screen ") for v, name in names)
    key, _ = info["key"]["tricky"], None
    go(pg, f"x/{key}/rob")
    heights = pg.evaluate("[...document.querySelectorAll('.rob-head button')].map(b => b.getBoundingClientRect().height)")
    assert heights and min(heights) >= 24
    ftrow = next(r for r in ch.c.get("/api/fulltext")["rows"] if not r["pdf"])
    go(pg, "ft/" + ftrow["id"])
    heights = pg.evaluate("[...document.querySelectorAll('.nopdf li a')].map(a => a.getBoundingClientRect().height)")
    assert heights and min(heights) >= 24


def test_a_finished_studys_wide_table_can_be_reached_from_the_keyboard(review, browser):
    info, people = review
    nh = people["NH"]
    pg = open_page(browser, nh, "x/" + info["key"]["taylor"])
    tables = pg.locator("table.t.cells")
    assert tables.count()
    assert all(tables.nth(k).get_attribute("tabindex") == "0" for k in range(tables.count()))


# ------------------------------------------------------------------------------------------------ F5: listeners
COUNT = """(() => { const n = { beforeprint: 0, afterprint: 0, keydown: 0 }; const add = EventTarget.prototype.addEventListener, rem = EventTarget.prototype.removeEventListener;
  EventTarget.prototype.addEventListener = function (t, f, o) { if ((this === window || this === document) && t in n) n[t]++; return add.call(this, t, f, o); };
  EventTarget.prototype.removeEventListener = function (t, f, o) { if ((this === window || this === document) && t in n) n[t]--; return rem.call(this, t, f, o); };
  window.__listeners = n; })();"""


def test_pages_take_their_window_listeners_off_when_left(review, browser):
    info, people = review
    ch = people["CH"]
    pg = browser.new_page(viewport={"width": 1366, "height": 900})
    pg.add_init_script(COUNT)
    pg.goto(ch.url)
    pg.wait_for_selector("h1")
    go(pg, "overview")
    start = pg.evaluate("({ ...window.__listeners })")
    for _ in range(4):
        go(pg, "timeline")
        go(pg, "screen/sample/main", wait="h1")
        go(pg, "overview")
    assert pg.evaluate("({ ...window.__listeners })") == start


# ------------------------------------------------------------------------------------------------ requests that fail
def test_a_page_whose_request_fails_says_so_and_offers_a_way_out(review, browser):
    info, people = review
    ch = people["CH"]
    pg = open_page(browser, ch, "overview")
    key = info["key"]["taylor"]
    for mode in ("500", "notjson", "abort"):
        def handler(route, mode=mode):
            if mode == "500":
                route.fulfill(status=500, body="")
            elif mode == "notjson":
                route.fulfill(status=200, content_type="application/json", body='{"records": [1, 2')
            else:
                route.abort("connectionrefused")
        pg.route("**/api/extract/study*", handler)
        go(pg, "x/" + key, wait="h1")
        assert pg.inner_text("h1") == "This page could not be opened"
        text = pg.inner_text(".callout.bad")
        assert text and "(" not in text.split(".")[0] and not re.search(r"\b[45]\d\d\b", text)
        assert pg.get_by_role("button", name="Try again").is_visible()
        assert pg.get_by_role("button", name="Go to the Overview").is_visible()
        pg.unroute("**/api/extract/study*")
        pg.get_by_role("button", name="Try again").click()
        quiet(pg, 500)
        assert pg.inner_text("h1").startswith("Extract data:")
        go(pg, "overview")


# ------------------------------------------------------------------------------------------------ one-reviewer projects
def _solo_person(tmp_path, p):
    srv = helpers.ServerProcess(str(tmp_path / "home"))
    person = type("P", (), {})()
    person.srv, person.c = srv, srv.client("SS")
    person.c.open_project(p.dir)
    person.c.be("SS")
    person.base = f"http://127.0.0.1:{srv.port}"
    person.url = f"{person.base}/?t={srv.token}"
    return person


def test_a_calibration_round_that_cannot_be_finished_can_be_ended_from_the_screening_page(tmp_path, monkeypatch, browser):
    from test_go_live_fx_plan import stuck_project
    p = stuck_project(tmp_path, monkeypatch)
    ss = _solo_person(tmp_path, p)
    try:
        pg = open_page(browser, ss, "screening")
        assert "cannot be finished" in pg.inner_text("main")
        pg.locator(".panel button.primary", has_text="End the calibration round").click()
        pg.locator(".modal").get_by_role("button", name="End the calibration round").click()
        quiet(pg, 700)
        assert ss.c.get("/api/overview")["sets"]["main"]["phase"] == "screening"
    finally:
        ss.srv.stop()


def test_a_kappa_just_under_its_threshold_is_shown_with_the_decimals_that_say_so(review, browser):
    info, people = review
    ch = people["CH"]
    pg = browser.new_page(viewport={"width": 1366, "height": 900})

    def overview(route):
        r = route.fetch()
        d = r.json()
        sv = d["sets"]["main"]
        sv["phase"] = "screening"
        sv["sample_result"] = {"n": 200, "agreement": 0.93, "kappa": 0.7954, "ci_low": 0.7, "ci_high": 0.88, "threshold": 0.8,
                               "kappa_rule": "below the threshold, so both reviewers screen every remaining record",
                               "kappa_shown": "0.795", "threshold_shown": "0.80"}
        route.fulfill(response=r, body=json.dumps(d))
    pg.route("**/api/overview", overview)
    pg.goto(ch.url)
    pg.wait_for_selector("h1")
    go(pg, "screening")
    text = pg.inner_text("main")
    assert "Cohen's kappa 0.795" in text and "threshold 0.80" in text


def test_someone_working_alone_is_offered_full_texts_by_one_reviewer(tmp_path, monkeypatch, browser):
    from test_go_live_fx_plan import make, SAM, SOLO_PLAN
    p = make(tmp_path, monkeypatch, [SAM], SOLO_PLAN)
    ss = _solo_person(tmp_path, p)
    try:
        pg = open_page(browser, ss, "setup/fulltext")
        sel = pg.locator("#setup-fulltext select").first
        assert sel.input_value() == "single"
        assert "needs a second person" in pg.inner_text("#setup-fulltext")
        pg.get_by_role("button", name="Save settings").click()
        quiet(pg, 700)
        assert ss.c.get("/api/app")["project"]["settings"]["fulltext"]["mode"] == "single"
        go(pg, "wizard/fulltext")
        assert pg.locator("select").first.input_value() == "single"
        assert "needs a second person" in pg.inner_text("main")
    finally:
        ss.srv.stop()


# ------------------------------------------------------------------------------------------------ later items
def test_the_free_copy_search_says_it_is_stopping(review, browser):
    info, people = review
    ch = people["CH"]
    pg = browser.new_page(viewport={"width": 1366, "height": 900})

    def status(route):
        if route.request.method != "GET":
            route.continue_()
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "running": True, "done": 3, "total": 12, "found": 1, "message": "Stopping: the report being looked up now is left unfinished."}))
    pg.route(re.compile(r"/api/fulltext/fetch$"), status)

    def listing(route):                     # the page asks for the search's status when the list says one is running
        r = route.fetch()
        d = r.json()
        d["fetch"] = {"running": True, "done": 3, "total": 12, "found": 1}
        route.fulfill(response=r, body=json.dumps(d))
    pg.route(re.compile(r"/api/fulltext$"), listing)
    pg.goto(ch.url)
    pg.wait_for_selector("h1")
    go(pg, "fulltext")
    pg.wait_for_timeout(500)
    text = pg.inner_text(".getpdfs")
    assert "Stopping: the report being looked up now is left unfinished. 3 of 12 checked" in text


def test_a_project_file_not_yet_synced_is_not_called_damaged(review, browser):
    info, people = review
    ch = people["CH"]
    pg = browser.new_page(viewport={"width": 1366, "height": 900})
    msg = ("The project file state/state.json is not on this computer yet, but this project already has records, so ReviewDesk "
           "needs it to show the review as it is.")
    pg.route(re.compile(r"/api/overview$"), lambda r: r.fulfill(status=409, content_type="application/json", body=json.dumps(
        {"error": msg, "damaged": {"file": "state/state.json", "copies": [], "can_restore": False}})))
    pg.goto(ch.url)
    pg.wait_for_selector("h1")
    assert pg.inner_text("h1") == "A project file is not on this computer yet"
    assert pg.get_by_role("button", name="Try again").is_visible()


def test_a_damaged_project_file_is_put_back_from_the_page_that_reports_it(review, browser):
    """Since 1.3.0 every change first asked the project file which version the project needs, so the one request that
    puts a damaged project file back was answered with the message the page was already showing."""
    info, people = review
    ch = people["CH"]
    ch.c.settings(short_title="Triage")                 # (the lead's computer keeps a copy of the project file it saves)
    pf = os.path.join(info["p"].dir, "reviewdesk-project.json")
    good = helpers.read_text(pf)
    title = json.loads(good)["title"]
    ch.c.be("NH")                                       # a reviewer at the lead's computer is not offered the copies
    helpers.write_text(pf, '{"title": "cut sho')        # (the file was cut short by a sync service)
    pg = browser.new_page(viewport={"width": 1366, "height": 900})
    pg.set_default_timeout(10000)
    pg.goto(ch.url)
    pg.wait_for_selector("h1")
    assert pg.inner_text("h1") == "A project file cannot be read"
    assert "Only the project lead can put the project file back" in pg.inner_text("body")
    assert pg.get_by_role("button", name="Restore the chosen copy").count() == 0
    helpers.write_text(pf, good)
    ch.c.be("CH")                                       # the lead is, and the button does what it says
    helpers.write_text(pf, '{"title": "cut sho')
    pg.reload()
    pg.wait_for_selector("h1")
    assert pg.inner_text("h1") == "A project file cannot be read"
    assert "reviewdesk-project.json cannot be read" in pg.inner_text(".callout.bad")
    pg.get_by_role("button", name="Restore the chosen copy").click()
    pg.wait_for_function("() => { const h = document.querySelector('h1'); return h && h.innerText !== 'A project file cannot be read'; }")
    now = json.loads(helpers.read_text(pf))
    assert now["title"] == title and now["short_title"] == "Triage"
    assert "cannot be read" not in pg.inner_text("body")


def test_the_version_banner_wraps_on_a_phone(review, browser):
    info, people = review
    ch = people["CH"]
    pg = browser.new_page(viewport={"width": 320, "height": 640})

    def app(route):
        r = route.fetch()
        d = r.json()
        d["project"]["version_warnings"] = ["Nadine Hayes last worked on this project with ReviewDesk 1.2.1 on NH-LAPTOP-WITH-A-VERY-LONG-COMPUTER-NAME-" + LONGWORD + ". Everyone should use the same version."]
        route.fulfill(response=r, body=json.dumps(d))
    pg.route(re.compile(r"/api/app$"), app)
    pg.goto(ch.url)
    pg.wait_for_selector(".callout.bad.banner")
    for r in ("overview", "setup", "report"):
        go(pg, r)
        assert pg.evaluate("document.scrollingElement.scrollWidth - document.documentElement.clientWidth") <= 0, r


# ------------------------------------------------------------------------------------------------ narrow and zoomed windows
BROKEN_WORDS = r"""() => { const bad = [];
  const broken = (el) => { const tw = document.createTreeWalker(el, NodeFilter.SHOW_TEXT); let n;
    while ((n = tw.nextNode())) { const re = /[A-Za-z]{2,}/g; let m;
      while ((m = re.exec(n.nodeValue))) { const r = document.createRange(); r.setStart(n, m.index); r.setEnd(n, m.index + m[0].length);
        if (new Set([...r.getClientRects()].filter(x => x.width > 0).map(x => Math.round(x.top))).size > 1) return m[0]; } }
    return ""; };
  for (const e of document.querySelectorAll('button, .btn, .pill, kbd')) { if (!e.offsetParent) continue; const w = broken(e);
    if (w) bad.push(e.textContent.trim().slice(0, 30) + ': ' + w); }
  const bars = [...document.querySelectorAll('.topbar, .screen-top, .screen-keys, .wz-actions')].filter(e => /sticky|fixed/.test(getComputedStyle(e).position));
  return { bad, share: Math.round(100 * bars.reduce((a, e) => a + e.getBoundingClientRect().height, 0) / innerHeight) }; }"""


def test_no_button_breaks_a_word_in_a_narrow_or_zoomed_window(review, browser):
    """Long words may break anywhere so that they never push a page sideways. That let a button squeezed in a row
    break its own name: at 200% zoom on a laptop the votes read "In / cl / ud / e", and the bars above and below the
    record covered more than half the window; on a phone the guided set-up's Back read "Ba / ck"."""
    info, people = review
    ch = people["CH"]
    for width, height in ((683, 384), (320, 640), (900, 700)):
        pg = open_page(browser, ch, "overview", width, height)
        for route, wait in (("screen/sample/main", ".screen-keys"), ("wizard/team", ".wz-actions"), ("wizard/fulltext", ".wz-actions"),
                            ("wizard/criteria", ".wz-actions"), ("setup", "h1"), ("fulltext", "h1")):
            go(pg, route, wait)
            got = pg.evaluate(BROKEN_WORDS)
            assert got["bad"] == [], (width, route, got["bad"])
            if route.startswith("screen/") and height == 384:
                assert got["share"] <= 42, (width, route, got["share"])      # as it was before words could break anywhere
        pg.close()
