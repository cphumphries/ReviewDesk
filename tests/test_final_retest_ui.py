"""What the final first-time-user re-test found, in the browser (Chromium, through Playwright), by keyboard where the
tester used the keyboard (the tester's kbd.py: Tab, and what has the focus, and whether it can be seen).

F2: the Overview, the side bar and the Reports page show the records found by other methods. N12: a page that shows
progress is drawn again when the window comes back into view and another computer's work has arrived; an old message
goes when the page changes. F4: ticking a design in the risk-of-bias plan with Space keeps the focus on it. F5: My
settings and the AI page lead back to the guided set-up, which opens again where it was left. F3 and F9: the Study
design item offers the plan's designs, and the focus goes to a new risk-of-bias assessment. F9 and F10: the full-text
page's PDF shows its focus, and the Save bar covers none of the exclusion reasons. F9: the list of models and the
statement check are not read out whole."""
from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from helpers import Desk, record_plan, rob_plan
from mock_elm import TEST_KEY
from reviewdesk import fulltext, review, screening, server
from reviewdesk.project import Project
from test_final_retest import TEAM, TITLES, citation_search, knee, ris   # the same small knee review

PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
FOCUS = """() => { const e = document.activeElement; if (!e || e === document.body) return {tag: 'BODY'};
  const cs = getComputedStyle(e), r = e.getBoundingClientRect();
  return {tag: e.tagName, type: e.type || '', text: (e.getAttribute('aria-label') || (e.labels && e.labels[0] ? e.labels[0].innerText : '')
    || e.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 80), outline: cs.outlineStyle, top: Math.round(r.top), bottom: Math.round(r.bottom),
    checked: !!e.checked}; }"""


@pytest.fixture
def running(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWDESK_HOME", str(tmp_path / "home-sp"))
    httpd, url = server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base, tok = url.split("/?")[0], url.split("t=")[1]

    def call(path, body=None):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     method="GET" if body is None else "POST",
                                     headers={"X-Token": tok, "Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    yield base, url, call
    httpd.shutdown()


@pytest.fixture
def browser():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                      # noqa: BLE001 - no browser on this computer
            pytest.skip(f"Chromium is not available: {e}")
        yield b
        b.close()


def page(browser, url, errors, w=1280, h=720):
    pg = browser.new_page(viewport={"width": w, "height": h})
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "favicon" not in m.text else None)
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(url)
    pg.wait_for_selector("h1")
    pg.evaluate("document.addEventListener('securitypolicyviolation', e => console.error('CSP ' + e.violatedDirective))")
    return pg


def open_as(call, p: Project, who: str) -> None:
    call("/api/project/open", {"path": p.dir})
    call("/api/me", {"reviewer": who})


def until(pg, js: str, seconds: float = 8) -> None:
    """Wait until the expression is true on the page (wait_for_function would evaluate a string, which the
    Content-Security-Policy forbids)."""
    for _ in range(int(seconds * 10)):
        if pg.evaluate(js):
            return
        pg.wait_for_timeout(100)
    raise AssertionError(f"not true after {seconds} s: {js}")


def to(pg, base, route, wait="h1"):
    pg.goto(f"{base}/#/{route}")
    pg.wait_for_selector(wait)
    pg.wait_for_timeout(400)


# ================================================================================================ F2
def test_the_overview_side_bar_and_reports_show_records_found_by_other_methods(running, browser, tmp_path):
    base, url, call = running
    p = knee(tmp_path)
    for r in p.records():
        fulltext.save_pdf(p, "SP", r["id"], PDF + r["id"].encode())
    citation_search(p)
    p.save_search("SP", {"database": "Hand-searching Osteoarthritis Practice 2020-2026", "platform": "by hand",
                         "date": "2026-11-03", "results": "3", "string": "Tables of contents"})
    open_as(call, p, "SP")
    errors = []
    pg = page(browser, url, errors)
    to(pg, base, "overview", ".rd-panel")
    where = pg.locator("ol.steps li", has_text="Title and abstract screening").inner_text()
    assert "Other methods: 1 record, not screened yet." in where
    panel = pg.inner_text(".rd-panel")
    assert "Citation searching and other sources" in panel and "1 record found by other methods" in panel
    assert "Hand-searching Osteoarthritis Practice 2020-2026 search of 3 November 2026 has no export file" in panel
    # the review is at the citation-searching step, and its work is on the screening page
    assert pg.locator("nav a.nav-now").inner_text().startswith("Screen titles and abstracts")
    assert pg.locator("section.next button").inner_text() == "Open screening"
    to(pg, base, "report", ".rd-banner")
    banner = pg.inner_text(".rd-banner")
    assert "1 record found by other methods has not been screened yet" in banner
    # screened: the step is done, the side bar moves on to extraction
    screening.start(p, "SP", "other")
    new = next(r["id"] for r in p.records() if r["set"] == "other")
    for who in ("AM", "SP"):
        p.add_decision(who, new, "ta", "exclude")
    p.save_search("SP", {**next(e for e in p.search_log() if e["database"].startswith("Hand")), "results": "0"})
    to(pg, base, "overview", "ol.steps")
    pg.wait_for_timeout(600)
    assert "Other methods: 0 sent on, 1 excluded, 0 still open." in pg.locator("ol.steps li", has_text="Title and abstract").inner_text()
    assert pg.locator("nav a.nav-now").inner_text().startswith("Extract data")
    assert errors == []


# ================================================================================================ N12
def test_the_full_text_page_follows_another_computers_work_and_old_messages_go(running, browser, tmp_path):
    base, url, call = running
    folder = str(tmp_path / "team" / "knee")
    p = Project.create(folder, "Exercise programmes for adults with knee osteoarthritis", "Dr Sam Patel", "SP")
    p.update_settings("SP", {"reviewers": TEAM, "question": "Exercise?", "ft_reasons": ["Wrong population"],
                             "criteria": {"include": ["Knee osteoarthritis"], "exclude": ["Surgery"], "rules": []},
                             "screening": {"mode": "dual", "reviewers": ["AM", "SP"], "calibration_size": 0}})
    record_plan(p, "SP", rob_plan("rct"))
    p.add_import("SP", "medline.ris", ris(TITLES), {"set": "main", "database": "MEDLINE"})
    p.process_imports("SP")
    screening.start(p, "SP", "main")
    ids = sorted(r["id"] for r in p.records())
    for rid in ids:
        p.add_decision("SP", rid, "ta", "include")
        p.add_decision("SP", rid, "ft", "include")
    am = Desk(str(tmp_path / "home-am"), folder, "AM")            # Alex, on another computer
    for rid in ids:
        am.do(am.p.add_decision, "AM", rid, "ta", "include")
    open_as(call, p, "SP")
    errors = []
    pg = page(browser, url, errors)
    to(pg, base, "fulltext", ".stat")
    included = lambda: pg.locator(".stat > div", has_text="included").locator("b").inner_text()   # noqa: E731
    assert included() == "0"
    pg.locator("tbody a.rowlink").last.focus()
    for rid in ids:                                                  # Alex finishes, on the other computer
        am.do(am.p.add_decision, "AM", rid, "ft", "include")
    pg.evaluate("window.dispatchEvent(new Event('focus'))")         # Sam comes back to the window
    until(pg, "[...document.querySelectorAll('.stat > div')].some((d) => /included/.test(d.innerText) && "
              "d.querySelector('b').innerText === '4')")
    assert included() == "4" and pg.evaluate("document.activeElement.classList.contains('rowlink')")   # the keyboard stays
    # nothing new: the page is left as it is
    pg.evaluate("document.querySelector('h1').dataset.mark = 'same'")
    pg.evaluate("window.dispatchEvent(new Event('focus'))")
    pg.wait_for_timeout(800)
    assert pg.evaluate("document.querySelector('h1').dataset.mark") == "same"
    # a message from an earlier page goes when another page is opened from the side bar
    pg.evaluate("import('/static/lib.js').then((m) => m.toast('Screening started'))")
    pg.wait_for_timeout(700)
    pg.locator("nav a[href='#/overview']").click()
    pg.wait_for_selector("section.next")
    assert pg.evaluate("document.getElementById('toast').className") == ""
    # one shown by the action that opens the page stays
    pg.evaluate("Promise.all([import('/static/lib.js'), import('/static/app.js')]).then(([l, a]) => { l.toast('Saved'); a.go('report'); })")
    pg.wait_for_selector("h1:text-is('PRISMA and reports')")
    assert pg.evaluate("document.getElementById('toast').className") == "show"
    assert errors == []


# ================================================================================================ F4
def test_ticking_a_design_with_space_keeps_the_focus_on_it(running, browser, tmp_path):
    base, url, call = running
    call("/api/project/create", {"parent": str(tmp_path), "name": "p", "title": "Knee review", "lead_name": "Dr Sam Patel",
                                 "lead_id": "SP"})
    errors = []
    pg = page(browser, url, errors)
    to(pg, base, "setup/robplan", ".rp-rows")
    name = "Cluster-randomised trials"
    pg.get_by_role("checkbox", name=name, exact=True).focus()
    pg.keyboard.press("Space")
    pg.wait_for_timeout(200)
    f = pg.evaluate(FOCUS)
    assert f["tag"] == "INPUT" and f["type"] == "checkbox" and f["text"] == name and f["checked"]
    pg.keyboard.press("Tab")                                         # on to the tool for that design, not back to the top
    assert pg.evaluate("document.activeElement.getAttribute('aria-label')") == f"Tool for {name}"
    pg.keyboard.press("Shift+Tab")
    pg.keyboard.press("Space")
    pg.wait_for_timeout(200)
    f = pg.evaluate(FOCUS)
    assert f["text"] == name and not f["checked"]
    assert errors == []


# ================================================================================================ F5
def test_my_settings_and_the_ai_page_lead_back_to_the_guided_set_up(running, browser, tmp_path):
    base, url, call = running
    from test_readiness import READY
    call("/api/project/create", {"parent": str(tmp_path), "name": "p", "title": "AI triage review", "lead_name": "Colin Hawthorne",
                                 "lead_id": "CH"})
    call("/api/settings", {"changes": {**READY, "screening": {**READY["screening"], "ai_second_screener": True}}})
    call("/api/rob/plan", {"plan": rob_plan("rct")})
    errors = []
    pg = page(browser, url, errors)
    for scr in ("review", "registration", "team", "criteria", "plan", "fulltext", "ai"):    # screens 1 to 7, as the lead goes
        to(pg, base, f"wizard/{scr}", ".wz-count")
    pg.get_by_role("button", name="Open My settings").click()
    pg.wait_for_selector("h1:text-is('My settings')")
    pg.get_by_role("button", name="Back to the guided set-up").click()
    pg.wait_for_selector("h1:text-is('The AI second screener')")
    pg.get_by_role("button", name="Open the AI second screener").click()
    pg.wait_for_selector("h1:text-is('AI second screener')")
    pg.get_by_role("button", name="Back to the guided set-up").click()
    pg.wait_for_selector("h1:text-is('The AI second screener')")
    # nothing is needed now, so the Overview offers the rest of the guided set-up, and it opens at the first screen not seen
    to(pg, base, "overview", ".rd-panel")
    pg.get_by_role("link", name="continue the guided set-up where you left it").click()
    pg.wait_for_selector("h1:text-is('The searches')")
    assert pg.locator(".wz-count").inner_text() == "Screen 8 of 9"
    to(pg, base, "settings")                                         # not from the guided set-up now: no way back shown
    assert pg.get_by_role("button", name="Back to the guided set-up").count() == 0
    to(pg, base, "wizard/finish", ".wz-count")
    to(pg, base, "setup", "h1")
    pg.get_by_role("button", name="Guided set-up").click()
    pg.wait_for_selector("h1:text-is('Check and finish')")            # every screen seen: Check and finish
    to(pg, base, "overview", "section.next")
    pg.wait_for_timeout(500)
    assert pg.get_by_role("link", name="continue the guided set-up where you left it").count() == 0
    assert errors == []


# ================================================================================================ F3 and F9
def test_the_design_choices_follow_the_plan_and_a_new_assessment_takes_the_focus(running, browser, tmp_path):
    base, url, call = running
    p = knee(tmp_path, form_first=True)
    k = {s["label"]: s["key"] for s in review.studies(p)}["Fischer 2022"]
    open_as(call, p, "AM")
    errors = []
    pg = page(browser, url, errors)
    to(pg, base, f"x/{k}", "#x-design")
    opts = pg.locator("#x-design option").all_inner_texts()
    assert opts == ["Choose…", "Randomised trials (individually randomised, parallel-group)", "Cluster-randomised trials",
                    "Diagnostic test accuracy studies", "Qualitative process evaluations", "Other"]
    pg.select_option("#x-design", label="Diagnostic test accuracy studies")
    pg.wait_for_timeout(1200)
    to(pg, base, f"x/{k}/rob", "#rob-design")
    assert pg.locator("#rob-design option:checked").inner_text() == "Diagnostic test accuracy studies"     # chosen already
    pg.fill("#rob-label", "Accuracy against X-ray")
    pg.get_by_role("button", name="Add the assessment").focus()
    pg.keyboard.press("Enter")
    until(pg, "!!document.activeElement && document.activeElement.tagName === 'H3'")
    f = pg.evaluate(FOCUS)
    assert f["text"] == "Accuracy against X-ray" and f["outline"] == "solid"
    assert pg.evaluate("document.activeElement.closest('.panel.rob') !== null")
    # in view, and not under the sticky tabs: the heading itself is what shows at its middle
    assert pg.evaluate("(() => { const e = document.activeElement, r = e.getBoundingClientRect(); "
                       "return e.contains(document.elementFromPoint(r.left + 20, (r.top + r.bottom) / 2)); })()")
    assert errors == []


# ================================================================================================ F9 and F10
@pytest.mark.parametrize("size", [(1280, 720), (390, 844)])
def test_the_full_text_page_by_keyboard(running, browser, tmp_path, size):
    base, url, call = running
    from test_final_retest import REASONS
    # the tester's seven exclusion reasons, and reports with their PDFs that Sam has still to assess
    p = Project.create(str(tmp_path / "knee"), "Knee", "Dr Sam Patel", "SP")
    p.update_settings("SP", {"reviewers": TEAM, "question": "Exercise?", "ft_reasons": REASONS,
                             "criteria": {"include": ["Knee osteoarthritis"], "exclude": ["Surgery"], "rules": []},
                             "screening": {"mode": "dual", "reviewers": ["AM", "SP"], "calibration_size": 0}})
    record_plan(p, "SP", rob_plan("rct"))
    p.add_import("SP", "medline.ris", ris(TITLES), {"set": "main", "database": "MEDLINE"})
    p.process_imports("SP")
    screening.start(p, "SP", "main")
    for r in p.records():
        for who in ("AM", "SP"):
            p.add_decision(who, r["id"], "ta", "include")
        fulltext.save_pdf(p, "SP", r["id"], PDF + r["id"].encode())
    rid = sorted(r["id"] for r in p.records())[0]
    open_as(call, p, "SP")
    errors = []
    w, h = size
    pg = page(browser, url, errors, w, h)
    to(pg, base, f"ft/{rid}", "text=Your decision")
    pg.get_by_role("button", name="Exclude", exact=True).focus()
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(400)
    bar = pg.evaluate("(() => { const r = document.querySelector('.ft-save').getBoundingClientRect(); return [r.top, r.bottom]; })()")
    if w > 900:
        assert abs(bar[1] - h) < 1                                   # on the very bottom: nothing shows below it
    for k in range(6):
        pg.keyboard.press("Tab")
        if pg.evaluate("document.activeElement.type") == "radio":
            break
    seen = []
    for k in range(7):                                               # every reason, by keyboard, clear of the Save bar
        f = pg.evaluate(FOCUS)
        assert f["type"] == "radio" and f["bottom"] <= bar[0], f
        seen.append(f["text"])
        pg.keyboard.press("ArrowDown")
        pg.wait_for_timeout(60)
    assert seen[-1] == "Not in English" and len(set(seen)) == 7
    # the PDF, reached by keyboard, shows that it has the focus
    pg.get_by_role("button", name="Include", exact=True).focus()
    for k in range(8):
        pg.keyboard.press("Shift+Tab")
        if pg.evaluate("document.activeElement.tagName") == "IFRAME":
            break
    pg.wait_for_timeout(200)
    f = pg.evaluate(FOCUS)
    assert f["tag"] == "IFRAME" and f["outline"] == "solid"
    assert errors == []


# ================================================================================================ F9: not read out whole
def test_the_list_of_models_and_the_statement_check_are_not_read_out_whole(running, browser, tmp_path, elm):
    base, url, call = running
    import demo_review
    from reviewdesk import certainty
    info = demo_review.build(str(tmp_path / "demo"))
    demo_review.grade(info)
    p = info["p"]
    oid = certainty.define(p, "CH", {"name": "Patient satisfaction", "importance": "important", "kind": "effect",
                                     "comparison": demo_review.GRADE_INTERVENTION, "measure": "", "synthesis": "narrative",
                                     "studies": [info["key"]["taylor"]]})["o"]
    for who in ("NH", "CH"):
        certainty.rate(p, who, oid, "start", "rct")
        for f in certainty.DOMAIN_IDS:
            certainty.rate(p, who, oid, f, -1 if f in ("rob", "imprecision") else 0, "Reason" if f in ("rob", "imprecision") else "")
        certainty.rate(p, who, oid, "effect", "1 trial; higher satisfaction")
        certainty.rate(p, who, oid, "statement", "AI-supported triage improves patient satisfaction.")    # low, worded as high
        certainty.set_done(p, who, oid, True)
    open_as(call, p, "CH")
    call("/api/user", {"elm_url": elm.url, "elm_key": TEST_KEY})
    errors = []
    pg = page(browser, url, errors)
    to(pg, base, f"c/{oid}/compare", ".c-final")
    box = pg.locator(".c-mismatch")
    assert box.count() == 1 and box.get_attribute("role") is None and box.get_attribute("aria-live") is None
    assert pg.evaluate("document.getElementById(document.querySelector('.c-mismatch').getAttribute('aria-labelledby')).innerText") \
        .startswith("The statement's wording does not match the certainty")
    to(pg, base, "ai", "text=Check the connection")
    pg.get_by_role("button", name="Check the connection").click()
    pg.wait_for_selector(".ai-model-list li")
    models = pg.locator(".ai-models")
    assert models.get_attribute("aria-live") is None and models.get_attribute("role") == "region"
    assert models.get_attribute("aria-label") == "Models your AI service offers"
    assert "available" in pg.inner_text("#toast")                    # what is announced: how many there are
    assert errors == []
