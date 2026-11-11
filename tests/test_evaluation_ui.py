"""The pages added in 1.3.1, in the browser: the lead switches time recording on and draws a verification sample; its
verifier screens it without any sign of what was decided before; each vote carries the time its record was on screen;
a person leaves their own votes out; and the stopwatch itself (what counts as active, what a long quiet stretch is).

Each test runs ReviewDesk as its own program (one per person) and drives Chromium; they skip when Playwright or
Chromium is not installed. No real AI service or key is used."""
from __future__ import annotations

import json
import os
import re

import pytest

import helpers
import test_evaluation as T
from mock_elm import TEST_KEY, MockELM
from test_go_live_fx_ui import BROKEN_WORDS

pw_api = pytest.importorskip("playwright.sync_api")


class Person:
    def __init__(self, root, who, folder):
        self.srv = helpers.ServerProcess(os.path.join(root, "home-" + who.lower()))
        self.c = self.srv.client(who)
        self.c.open_project(folder)
        self.c.be(who)
        self.url = f"http://127.0.0.1:{self.srv.port}/?t={self.srv.token}"


@pytest.fixture
def team(tmp_path):
    """A review in which NH has screened everything outside the agreement sample alone, with the AI behind (see
    test_evaluation.single_screened), and CH (the lead), NH and TK each at a computer of their own."""
    elm = MockELM().start()
    desk = helpers.LocalServer(str(tmp_path / "home-setup"))
    people = {}
    try:
        c = desk.client()
        c.post("/api/user", {"elm_url": elm.url, "elm_key": TEST_KEY})
        c.tmp, c.elm = tmp_path, elm
        R = T.single_screened(c)
        desk.stop()
        elm.stop()
        for who in ("CH", "NH", "TK"):
            people[who] = Person(str(tmp_path), who, R["folder"])
        yield R, people
    finally:
        desk.stop()
        elm.stop()
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


def quiet(pg, ms=400):
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(ms)


def go(pg, route, wait="h1"):
    pg.evaluate("r => { location.hash = '#/' + r; }", route)
    pg.wait_for_timeout(150)
    pg.wait_for_selector(wait)
    quiet(pg)


def open_page(browser, person, route="screening", width=1366, height=900, errors=None, wait="h1"):
    pg = browser.new_page(viewport={"width": width, "height": height})
    pg.set_default_timeout(10000)
    if errors is not None:
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "status of" not in m.text else None)
    pg.goto(person.url)
    pg.wait_for_selector("h1")
    go(pg, route, wait)
    return pg


def wait_pos(pg, n):
    """Wait until the screening page shows record n of its list (by its own words: the page's rules for the browser
    do not let a test evaluate a condition inside it)."""
    pg.locator(".screen-keys .small").filter(has_text=re.compile(rf"^{n} of ")).wait_for()


def rows_of(folder, who):
    """A person's votes from every computer they used, in the order they were cast."""
    from reviewdesk import util
    return sorted(T.decisions(folder, who), key=lambda d: util.ts(d["t"]))


def switch_on(pg):
    pg.get_by_role("button", name="Switch time recording on").click()
    box = pg.locator(".modal")
    assert "This is data about the people in your team." in box.inner_text() and "ReviewDesk 1.3.1 or later" in box.inner_text()
    box.get_by_role("button", name="Switch it on").click()
    quiet(pg, 600)


def unfold(pg):
    """The draw is an option the lead may never take: it is folded away until they open it."""
    assert not pg.locator("#vs-size").is_visible()
    pg.locator("details.verify > summary").click()
    pg.wait_for_selector("#vs-size", state="visible")


def draw(pg, size=20, mix=4, who="TK"):
    if not pg.locator("#vs-size").is_visible():
        unfold(pg)
    pg.fill("#vs-size", str(size))
    pg.fill("#vs-mix", str(mix))
    pg.select_option("#vs-who", who)
    pg.get_by_role("button", name="Draw the verification sample").click()
    box = pg.locator(".modal")
    text = box.inner_text()
    box.get_by_role("button", name="Draw the sample").click()
    quiet(pg, 700)
    return text


# ------------------------------------------------------------------------------------------------ the lead
def test_the_lead_switches_time_recording_on_and_draws_the_sample(team, browser):
    R, people = team
    errors = []
    pg = open_page(browser, people["CH"], errors=errors)
    panel = pg.locator("section[aria-labelledby='eval-h']")
    assert "Off. When it is on" in panel.inner_text() and pg.locator(".callout:has-text('Time recording is on')").count() == 0
    switch_on(pg)
    assert "On: with each vote" in pg.locator("section[aria-labelledby='eval-h']").inner_text()
    assert pg.locator(".callout:has-text('Time recording is on')").count() == 1
    assert T.read_json(os.path.join(R["folder"], "reviewdesk-project.json"))["screening"]["record_time"] is True
    # the draw: what there is to draw from, what the numbers would show, and a last question before it is fixed
    vs = pg.locator("details.verify")
    assert vs.locator("summary").inner_text() == "Verification sample (optional): check the records that left on one vote"
    unfold(pg)
    assert "39 records left the review on their vote alone (the AI would not keep them either)" in vs.inner_text()
    assert pg.input_value("#vs-size") == "39" and pg.get_attribute("#vs-mix", "max") == "5"
    # the form proposes the person there is no caution about, and says what the caution is when another is chosen
    assert pg.input_value("#vs-who") == "TK" and not pg.locator("#vs-caution").is_visible()
    pg.select_option("#vs-who", "CH")
    said = pg.locator("#vs-caution").inner_text()
    assert said.startswith("A caution about this choice: Dr Colin Hawthorne assesses full texts") and "is the project lead" in said
    assert "If someone else in the team can screen the sample, choose them" in said
    pg.select_option("#vs-who", "TK")
    assert not pg.locator("#vs-caution").is_visible()
    pg.fill("#vs-size", "20")
    pg.fill("#vs-mix", "9")
    said = vs.locator("p[role=status]").inner_text()
    assert said == "Between 0 and 5 records can be mixed in: Nadine Hayes sent on that many which Tane Kereama has not screened."
    for size, mix in (("20", "9"), ("1e1", "2"), ("2.5", "2"), ("0", "0"), ("40", "0")):      # refused before anything is asked
        pg.fill("#vs-size", "")
        pg.locator("#vs-size").press_sequentially(size)
        pg.fill("#vs-mix", mix)
        pg.get_by_role("button", name="Draw the verification sample").click()
        quiet(pg, 250)
        assert pg.locator(".modal").count() == 0 and "verify" not in T.state(R["folder"]), (size, mix)
    pg.fill("#vs-mix", "4")
    pg.fill("#vs-size", "20")
    assert "If Tane Kereama would send on none of the 20" in vs.locator("p[role=status]").inner_text()
    text = draw(pg, 20, 4, "TK")
    assert "20 records will be drawn at random from the 39, mixed with 4 records Nadine Hayes sent on, and given to Tane Kereama" in text
    assert "drawn once" in text and "closes to Tane Kereama the pages that show what became of records" in text
    vf = T.state(R["folder"])["verify"]
    assert vf["reviewer"] == "TK" and len(vf["sample"]) == 20 and len(vf["decoy"]) == 4
    vs = pg.locator("section.verify")
    assert "24 records, screened by Tane Kereama without seeing any earlier decision. 0 of 24 done." in vs.inner_text()
    assert "Nothing changes until the sample is finished" in vs.inner_text()
    assert pg.locator("#vs-size").count() == 0 and errors == []
    assert pg.evaluate("document.activeElement && document.activeElement.textContent") == "Verification sample"   # the keyboard is there
    for width, height in ((683, 384), (320, 640)):          # nothing breaks a word or pushes the page sideways
        pg.set_viewport_size({"width": width, "height": height})
        quiet(pg, 200)
        assert pg.evaluate(BROKEN_WORDS)["bad"] == []
        assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    pg.close()


def test_the_draw_form_fits_a_narrow_window(team, browser):
    R, people = team
    for width, height in ((683, 384), (320, 640)):
        pg = open_page(browser, people["CH"], width=width, height=height)
        unfold(pg)
        assert pg.evaluate(BROKEN_WORDS)["bad"] == []
        assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
        pg.close()


# ------------------------------------------------------------------------------------------------ the verifier
def test_the_verifier_screens_the_sample_and_each_vote_carries_its_time(team, browser):
    R, people = team
    ch = people["CH"].c
    ch.settings(screening={"record_time": True})
    ch.post("/api/screening/verify", {"set": "main", "size": 20, "decoys": 4, "reviewer": "TK"})
    vf = T.state(R["folder"])["verify"]
    errors = []
    pg = open_page(browser, people["TK"], errors=errors)
    task = pg.locator(".task:has-text('Verification sample')")
    assert "0 of 24 done" in task.inner_text()
    task.get_by_role("button", name="Start").click()
    pg.wait_for_selector("h2.rec-title")
    quiet(pg, 200)
    side = pg.locator("aside.side").inner_text()
    assert "no earlier decision is shown" in side and "Time recording is on." in side
    assert pg.locator("h1.title").inner_text() == "Verification sample"
    assert pg.locator(".decide.on").count() == 0 and "you voted" not in pg.locator(".screen-keys").inner_text()
    assert "change a vote until you vote on the last record: that finishes the sample, and its votes are then fixed" in side
    seen = []
    for k in range(23):
        pg.wait_for_selector("h2.rec-title")
        wait_pos(pg, k + 1)
        rid = pg.inner_text(".rid span")
        seen.append(rid)
        pg.wait_for_timeout(150 if k else 700)
        pg.keyboard.press("i" if rid in R["eligible"] else "e")
    wait_pos(pg, 24)
    quiet(pg, 300)
    assert seen == vf["order"][:23]
    rows = rows_of(R["folder"], "TK")
    assert [r["r"] for r in rows] == vf["order"][:23] and {r["s"] for r in rows} == {"vs"}
    for r in rows:
        t = r["time"]
        assert set(t) == {"shown", "active", "quiet", "views"} and t["views"] == 1
        assert 100 <= t["shown"] < 20000 and 0 <= t["quiet"] <= t["active"] <= t["shown"]
    assert rows[0]["time"]["shown"] >= 650                   # the first record was looked at for longer
    # going back and changing a vote while the sample is open: a new row, timed from when the record came back on screen
    pg.keyboard.press("ArrowLeft")
    wait_pos(pg, 23)
    quiet(pg, 300)
    assert "you voted" in pg.locator(".screen-keys").inner_text()
    pg.keyboard.press("m")
    wait_pos(pg, 24)
    quiet(pg, 300)
    again = rows_of(R["folder"], "TK")[-1]
    assert again["r"] == vf["order"][22] and again["d"] == "maybe" and again["time"]["views"] == 1
    assert 250 <= again["time"]["shown"] < 5000
    # the last record: its vote finishes the sample and fixes every vote in it, so it is asked for first
    last = vf["order"][23]
    assert pg.inner_text(".rid span") == last
    pg.keyboard.press("e")
    pg.wait_for_selector(".modal")
    said = pg.locator(".modal").inner_text()
    assert "Finish the verification sample?" in said and "Your vote on it (Exclude) finishes the sample" in said
    assert "cannot be changed afterwards" in said and "cancel and go back with ←" in said
    pg.keyboard.press("i")                                   # a key pressed with the question open votes on nothing
    pg.get_by_role("button", name="Cancel").click()
    quiet(pg, 300)
    assert len(rows_of(R["folder"], "TK")) == 24 and pg.inner_text(".rid span") == last
    assert people["TK"].c.overview()["verifying"]["left"] == 1
    pg.keyboard.press("i" if last in R["eligible"] else "e")
    pg.wait_for_selector(".modal")
    pg.get_by_role("button", name=re.compile("^Vote (Include|Exclude) and finish$")).click()
    pg.wait_for_selector("h1:has-text('Screen titles and abstracts')")
    quiet(pg, 400)
    assert "Verification sample finished: its votes are fixed" in pg.evaluate("document.getElementById('toast').textContent")
    assert pg.locator(".task:has-text('Verification sample')").count() == 0
    assert pg.locator(".callout:has-text('You are screening a verification sample.')").count() == 0
    assert "24 of 24 done. Finished: its votes are fixed." in pg.locator("section.verify").inner_text()
    rows = rows_of(R["folder"], "TK")
    # (the last record had been on screen once before the look back at the one before it: its two viewings add up)
    assert len(rows) == 25 and rows[-1]["r"] == last and rows[-1]["time"]["views"] == 2
    assert people["TK"].c.overview()["verifying"] is None
    # nothing is left to vote on: the room has no records for them now
    pg.evaluate("location.hash = '#/screen/verify/main'")
    pg.wait_for_selector("text=Nothing to screen here.")
    assert errors == []
    pg.close()
    # the lead now has the figures; the single reviewer only that it was done
    pg = open_page(browser, people["CH"])
    assert "Result: Tane Kereama would have sent on" in pg.locator("section.verify").inner_text()
    pg.close()
    pg = open_page(browser, people["NH"])
    text = pg.locator("section.verify").inner_text()
    assert "screened by Tane Kereama without seeing any earlier decision. Finished." in text
    assert "24" not in text and "Result" not in text and "would have sent on" not in text       # not even how many records
    pg.close()


def test_a_record_left_and_come_back_to_adds_up_its_time(team, browser):
    R, people = team
    ch = people["CH"].c
    ch.settings(screening={"record_time": True})
    ch.post("/api/screening/verify", {"set": "main", "size": 10, "decoys": 0, "reviewer": "TK"})
    pg = open_page(browser, people["TK"], "screen/verify/main", wait="h2.rec-title")
    pg.wait_for_timeout(400)
    pg.keyboard.press("ArrowRight")                          # looks at the first record, moves on without voting ...
    wait_pos(pg, 2)
    pg.wait_for_timeout(300)
    pg.keyboard.press("ArrowLeft")                           # ... and comes back to it
    wait_pos(pg, 1)
    pg.wait_for_timeout(400)
    pg.keyboard.press("e")
    quiet(pg, 400)
    first = rows_of(R["folder"], "TK")[-1]
    assert first["time"]["views"] == 2 and 750 <= first["time"]["shown"] < 5000
    pg.wait_for_timeout(200)
    pg.keyboard.press("e")                                   # the second record: seen once before without a vote
    quiet(pg, 400)
    second = rows_of(R["folder"], "TK")[-1]
    assert second["time"]["views"] == 2 and second["time"]["shown"] >= 450
    pg.close()


def test_a_person_leaves_their_own_votes_out(team, browser):
    R, people = team
    ch = people["CH"].c
    ch.settings(screening={"record_time": True})
    ch.post("/api/screening/verify", {"set": "main", "size": 10, "decoys": 0, "reviewer": "TK"})
    pg = open_page(browser, people["TK"])
    pg.get_by_role("button", name="What is saved, and your choice").click()
    box = pg.locator(".modal")
    text = box.inner_text()
    assert "It is your choice." in text and "not which keys you press" in text and "At present the times of your votes are saved." in text
    box.get_by_role("button", name="Leave my votes out").click()
    quiet(pg, 600)
    assert "Your own votes are left out, as you chose." in pg.locator(".callout:has-text('Time recording is on')").inner_text()
    go(pg, "screen/verify/main", "h2.rec-title")
    assert "Your own votes are left out" in pg.locator("aside.side").inner_text()
    pg.wait_for_timeout(200)
    pg.keyboard.press("e")
    quiet(pg, 400)
    assert "time" not in rows_of(R["folder"], "TK")[-1]
    # and changes their mind: from the screening page itself
    pg.locator("aside.side").get_by_role("button", name="What is saved, and your choice").click()
    box = pg.locator(".modal")
    assert "At present your votes are left out" in box.inner_text()
    box.get_by_role("button", name="Save the times of my votes again").click()
    quiet(pg, 600)
    pg.wait_for_selector("h2.rec-title")
    pg.wait_for_timeout(200)
    pg.keyboard.press("e")
    quiet(pg, 400)
    assert rows_of(R["folder"], "TK")[-1]["time"]["views"] == 1
    pg.close()


def test_with_recording_off_no_times_are_measured_or_sent(team, browser):
    R, people = team
    people["CH"].c.post("/api/screening/verify", {"set": "main", "size": 10, "decoys": 0, "reviewer": "TK"})
    sent = []
    pg = open_page(browser, people["TK"], "screen/verify/main", wait="h2.rec-title")
    pg.on("request", lambda r: sent.append(r.post_data) if r.url.endswith("/api/decide") else None)
    assert "Time recording" not in pg.locator("aside.side").inner_text()
    pg.wait_for_timeout(200)
    pg.keyboard.press("e")
    quiet(pg, 400)
    assert len(sent) == 1 and "time" not in json.loads(sent[0])
    assert "time" not in rows_of(R["folder"], "TK")[-1]
    pg.close()


def test_a_full_text_decision_is_timed_without_a_quiet_stretch(team, browser):
    R, people = team
    people["CH"].c.settings(screening={"record_time": True})
    rid = sorted(R["nh_yes"])[0]
    sent = []
    pg = open_page(browser, people["NH"], "ft/" + rid, wait=".ft-body")
    pg.on("request", lambda r: sent.append(r.post_data) if r.url.endswith("/api/decide") else None)
    assert "Time recording is on." in pg.locator("aside.side").inner_text()
    pg.wait_for_timeout(500)
    pg.get_by_role("button", name="Awaiting information").click()
    pg.get_by_role("button", name="Save and go to the next").click()
    quiet(pg, 500)
    body = json.loads(sent[0])
    assert set(body["time"]) == {"shown", "active", "views"} and body["time"]["shown"] >= 450
    row = rows_of(R["folder"], "NH")[-1]
    assert row["s"] == "ft" and set(row["time"]) == {"shown", "active", "views"}
    pg.close()


# ------------------------------------------------------------------------------------------------ the stopwatch
STOPWATCH = """async () => {
  const { stopwatch } = await import('/static/lib.js');
  const wait = (ms) => new Promise((ok) => setTimeout(ok, ms));
  let front = true;
  document.hasFocus = () => front;
  const away = async (ms) => { front = false; window.dispatchEvent(new Event('blur')); await wait(ms); front = true; window.dispatchEvent(new Event('focus')); };
  const key = () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true }));
  const w = stopwatch({ scope: 'test' + Math.random() });
  const out = {};
  w.show('a'); await wait(300); await away(400); await wait(200); key(); await wait(100);
  out.a = w.read('a');                       // shown 1000; active 600 (300 + 300); quiet 300 (to the blur; then 200, 100)
  w.show('b'); await wait(150); key(); await wait(450);
  out.b = w.read('b');                       // shown 600, active 600, quiet 450
  w.show('a'); await wait(200);
  out.a2 = w.read('a');                      // adds to the first viewing: shown 1200, active 800, quiet still 300, views 2
  out.b2 = w.read('b');                      // kept from its one viewing
  w.clear('a');
  out.gone = w.read('a') === undefined;
  w.show('a'); await wait(100); w.show('a');
  out.a3 = w.read('a');                      // after a saved vote: from nothing again
  const p = stopwatch({ quiet: false, scope: 'pdf' + Math.random() });
  p.show('x'); await wait(100);
  out.x = p.read('x');
  return out;
}"""


def test_the_stopwatch_counts_only_time_with_the_page_in_front(team, browser):
    R, people = team
    pg = open_page(browser, people["NH"], "overview")
    got = pg.evaluate(STOPWATCH)
    near = lambda v, want, slack=140: want - 30 <= v <= want + slack          # noqa: E731 - timers run late, never early
    a, b, a2, b2 = got["a"], got["b"], got["a2"], got["b2"]
    assert near(a["shown"], 1000) and near(a["active"], 600) and near(a["quiet"], 300) and a["views"] == 1, a
    assert near(b["shown"], 600) and near(b["active"], 600) and near(b["quiet"], 450) and b["views"] == 1, b
    assert near(a2["shown"], 1200, 250) and near(a2["active"], 800, 250) and near(a2["quiet"], 300) and a2["views"] == 2, a2
    assert b2["views"] == 1 and near(b2["shown"], 600) and got["gone"] is True
    assert got["a3"]["views"] == 1 and near(got["a3"]["shown"], 100)
    assert set(got["x"]) == {"shown", "active", "views"}
    pg.close()


# ------------------------------------------------------------------------------------------------ after the independent checks
def test_times_add_up_when_the_screening_page_is_left_and_come_back_to(team, browser):
    R, people = team
    ch = people["CH"].c
    ch.settings(screening={"record_time": True})
    ch.post("/api/screening/verify", {"set": "main", "size": 10, "decoys": 0, "reviewer": "TK"})
    pg = open_page(browser, people["TK"], "screen/verify/main", wait="h2.rec-title")
    pg.wait_for_timeout(600)
    pg.get_by_role("button", name="Back to the screening page").click()          # leaves the first record unvoted
    pg.wait_for_selector(".task")
    quiet(pg, 200)
    go(pg, "screen/verify/main", "h2.rec-title")
    pg.wait_for_timeout(300)
    pg.keyboard.press("e")
    quiet(pg, 400)
    row = rows_of(R["folder"], "TK")[-1]
    assert row["time"]["views"] == 2 and 850 <= row["time"]["shown"] < 6000, row["time"]
    pg.close()


def test_going_on_and_straight_back_is_one_viewing(team, browser):
    """A record stays on screen until the next one has loaded. Pressing the right arrow and then the left arrow before
    the next one arrives never took it off the screen: it is one viewing, and all of its time counts."""
    R, people = team
    ch = people["CH"].c
    ch.settings(screening={"record_time": True})
    ch.post("/api/screening/verify", {"set": "main", "size": 10, "decoys": 0, "reviewer": "TK"})
    order = T.state(R["folder"])["verify"]["order"]
    pg = open_page(browser, people["TK"], "overview")

    def slow(route):
        pg.wait_for_timeout(900)
        route.continue_()
    pg.route("**/api/record?id=" + order[2], slow)           # the third record is slow to arrive
    go(pg, "screen/verify/main", "h2.rec-title")
    pg.keyboard.press("ArrowRight")
    wait_pos(pg, 2)
    pg.wait_for_timeout(300)                                 # the second record is on screen
    pg.keyboard.press("ArrowRight")                          # on to the third, which has not arrived ...
    pg.wait_for_timeout(150)
    assert pg.inner_text(".rid span") == order[1]            # ... so the second is still what is shown
    pg.keyboard.press("ArrowLeft")                           # and back before it does
    wait_pos(pg, 2)
    pg.wait_for_timeout(1100)                                # (the third arrives meanwhile, and is not put on screen)
    assert pg.inner_text(".rid span") == order[1]
    pg.keyboard.press("e")
    quiet(pg, 400)
    row = rows_of(R["folder"], "TK")[-1]
    assert row["r"] == order[1] and row["time"]["views"] == 1 and row["time"]["shown"] >= 1500, row
    pg.close()


def test_moving_on_while_a_vote_is_being_saved_skips_no_record(team, browser):
    R, people = team
    people["CH"].c.post("/api/screening/verify", {"set": "main", "size": 10, "decoys": 0, "reviewer": "TK"})
    order = T.state(R["folder"])["verify"]["order"]
    pg = open_page(browser, people["TK"], "screen/verify/main", wait="h2.rec-title")

    def slow(route):
        pg.wait_for_timeout(700)
        route.continue_()
    pg.route("**/api/decide", slow)
    pg.wait_for_timeout(200)
    pg.keyboard.press("e")
    pg.wait_for_timeout(100)
    pg.keyboard.press("ArrowRight")                          # while the vote is still on its way
    pg.keyboard.press("ArrowRight")
    wait_pos(pg, 2)
    quiet(pg, 900)
    assert pg.inner_text(".rid span") == order[1] and pg.locator(".screen-keys .small").inner_text().startswith("2 of 10")
    assert [r["r"] for r in rows_of(R["folder"], "TK")] == [order[0]]
    pg.close()


def test_the_pages_that_show_earlier_decisions_say_that_they_are_closed_to_the_verifier(team, browser):
    R, people = team
    people["CH"].c.post("/api/screening/verify", {"set": "main", "size": 6, "decoys": 2, "reviewer": "TK"})
    errors = []
    pg = open_page(browser, people["TK"], errors=errors)
    note = pg.locator(".callout:has-text('You are screening a verification sample.')").inner_text()
    assert "(8 records left)" in note and "Review full texts, Extract data, Rate certainty, the audit log and every download" in note
    assert pg.locator(".task:has-text('Full-text review')").count() == 0
    for route in ("fulltext", "extract", "certainty"):
        go(pg, route)
        assert pg.locator("h1").inner_text() == "Closed until you finish the verification sample", route
        assert "You are screening a verification sample" in pg.locator(".callout").inner_text()
        assert pg.get_by_role("button", name="Try again").count() == 0
    pg.get_by_role("button", name="Go to Screen titles and abstracts").click()
    pg.wait_for_selector(".task:has-text('Verification sample')")
    go(pg, "report")
    assert "No downloads for now. You are screening a verification sample" in pg.locator(".callout[role=note]").inner_text()
    assert pg.get_by_role("button", name="Download the reporting pack").count() == 0
    assert pg.locator("button:has-text('Download')").count() == 0      # no file is offered, the picture included
    whys = pg.locator(".rep-files").all_inner_texts()                   # the reason in full once, then in a few words
    assert whys and set(whys) == {"Not yet. Closed until you finish the verification sample."}
    # the single reviewer's pages are as they were
    pg2 = open_page(browser, people["NH"], "fulltext")
    assert pg2.locator("h1").inner_text() != "Closed until you finish the verification sample"
    pg2.close()
    T.screen_all(people["TK"].c, "TK", lambda r: "exclude", "verify", stage="vs")
    go(pg, "fulltext")
    assert pg.locator("h1").inner_text() != "Closed until you finish the verification sample"
    go(pg, "screening")
    assert pg.locator(".callout:has-text('You are screening a verification sample.')").count() == 0
    assert [e for e in errors if "409" not in e and "403" not in e] == []
    pg.close()


def test_a_lead_who_verifies_is_told_why_and_a_lost_sample_keeps_the_pages_closed(team, browser):
    """The lead is not exempt while they screen a sample: "Decide together" says why it is closed (it used to do
    nothing at all). And if the sample is lost from the project's state, the page says so and stays closed."""
    R, people = team
    ch = people["CH"].c
    ch.post("/api/screening/verify", {"set": "main", "size": 6, "decoys": 2, "reviewer": "CH"})
    vf = T.state(R["folder"])["verify"]
    errors = []
    pg = open_page(browser, people["CH"], errors=errors)
    go(pg, "screening/decide")
    quiet(pg, 500)
    assert "You are screening a verification sample" in pg.evaluate("document.getElementById('toast').textContent")
    assert pg.locator(".modal").count() == 0
    go(pg, "ai")
    assert pg.locator("h1").inner_text() != "Closed until you finish the verification sample"       # the AI page stays open
    # the sample goes missing from the state (an older copy put back) after a vote: the copy in the lead's audit log
    # stands in, so nothing changes for the verifier, and the lead is told
    ch.decide(vf["order"][0], "vs", "exclude")
    path = os.path.join(R["folder"], "state", "state.json")
    st = helpers.read_json(path)
    del st["sets"]["main"]["verify"]
    helpers.write_json(path, st)
    go(pg, "overview")
    go(pg, "screening")
    assert "7 records left" in pg.locator(".callout:has-text('You are screening a verification sample.')").inner_text()
    assert "ReviewDesk is using the copy the audit log kept when the sample was drawn" in pg.locator("section.verify").inner_text()
    go(pg, "fulltext")
    assert pg.locator("h1").inner_text() == "Closed until you finish the verification sample"
    # the log loses it as well: closed still, and the page says why
    T.forget_the_draw(R["folder"])
    go(pg, "overview")
    go(pg, "screening")
    note = pg.locator(".callout.warn:has-text('Your verification sample is no longer in the project')").inner_text()
    assert "Your votes are kept." in note and "Draw it again below." in note
    assert pg.locator(".task:has-text('Verification sample')").count() == 0
    go(pg, "fulltext")
    assert pg.locator("h1").inner_text() == "Closed until the verification sample is drawn again"
    assert "is no longer in the project's files" in pg.locator(".callout").inner_text()
    go(pg, "report")
    assert set(pg.locator(".rep-files").all_inner_texts()) == {"Not yet. Closed until the verification sample is drawn again."}
    go(pg, "screening")
    draw(pg, size=6, mix=2, who="CH")
    assert T.state(R["folder"])["verify"]["order"] == vf["order"]
    assert "7 records left" in pg.locator(".callout:has-text('You are screening a verification sample.')").inner_text()
    assert [e for e in errors if "409" not in e and "403" not in e] == []
    pg.close()


def test_a_second_tab_cannot_finish_the_sample_without_the_question(team, browser):
    """A tab opened earlier does not know that the records it thinks are left were voted on in another tab. The server
    asks for the last vote to be meant, so the question still comes; and a tab left behind by a finished sample is
    told so and leaves the page."""
    R, people = team
    people["CH"].c.post("/api/screening/verify", {"set": "main", "size": 6, "decoys": 2, "reviewer": "TK"})
    vf = T.state(R["folder"])["verify"]
    tk = people["TK"].c
    for rid in vf["order"][:6]:
        tk.decide(rid, "vs", "exclude")
    errors = []
    a = open_page(browser, people["TK"], "screen/verify/main", errors=errors, wait="h2.rec-title")
    b = open_page(browser, people["TK"], "screen/verify/main", errors=errors, wait="h2.rec-title")
    wait_pos(a, 7)
    wait_pos(b, 7)
    b.keyboard.press("e")                                    # the seventh of eight, in the second tab: no question
    wait_pos(b, 8)
    assert b.locator(".modal").count() == 0 and len(rows_of(R["folder"], "TK")) == 7
    a.keyboard.press("ArrowRight")                           # the first tab still thinks two are left
    wait_pos(a, 8)
    quiet(a, 200)
    a.keyboard.press("e")
    a.wait_for_selector(".modal")
    assert "Finish the verification sample?" in a.locator(".modal").inner_text()
    a.get_by_role("button", name="Cancel").click()
    quiet(a, 300)
    assert len(rows_of(R["folder"], "TK")) == 7 and tk.overview()["verifying"]["left"] == 1
    a.keyboard.press("i")
    a.wait_for_selector(".modal")
    a.get_by_role("button", name="Vote Include and finish").click()
    a.wait_for_selector("h1:has-text('Screen titles and abstracts')")
    quiet(a, 300)
    rows = rows_of(R["folder"], "TK")
    assert len(rows) == 8 and rows[-1]["r"] == vf["order"][7] and rows[-1]["d"] == "include"
    assert tk.overview()["verifying"] is None
    # the second tab, left on the last record: its vote is refused, it says why, and it leaves the page
    b.keyboard.press("e")
    b.wait_for_selector(".modal")
    b.get_by_role("button", name="Vote Exclude and finish").click()
    b.wait_for_selector("h1:has-text('Screen titles and abstracts')")
    quiet(b, 300)
    assert "This verification sample is finished, so nothing was saved" in b.evaluate("document.getElementById('toast').textContent")
    assert len(rows_of(R["folder"], "TK")) == 8
    assert [e for e in errors if "409" not in e] == []
    a.close()
    b.close()


def test_leaving_your_votes_out_on_a_full_text_page_keeps_a_decision_not_yet_saved(team, browser):
    R, people = team
    people["CH"].c.settings(screening={"record_time": True})
    rid = sorted(R["nh_yes"])[0]
    sent = []
    pg = open_page(browser, people["NH"], "ft/" + rid, wait=".ft-body")
    pg.on("request", lambda r: sent.append(r.post_data) if r.url.endswith("/api/decide") else None)
    pg.get_by_role("button", name="Exclude", exact=True).click()
    pg.locator(".reasons input").first.check()
    pg.locator("aside.side textarea").fill("Not a triage tool: see page 3.")
    pg.locator("aside.side").get_by_role("button", name="What is saved, and your choice").click()
    pg.locator(".modal").get_by_role("button", name="Leave my votes out").click()
    quiet(pg, 500)
    assert pg.locator(".modal").count() == 0
    assert pg.locator("aside.side textarea").input_value() == "Not a triage tool: see page 3."
    assert pg.get_by_role("button", name="Exclude", exact=True).get_attribute("aria-pressed") == "true"
    assert pg.locator(".reasons input").first.is_checked()
    assert "Your own votes are left out, as you chose." in pg.locator("aside.side").inner_text()
    pg.get_by_role("button", name="Save and go to the next").click()
    quiet(pg, 500)
    body = json.loads(sent[0])
    assert body["decision"] == "exclude" and body["note"] == "Not a triage tool: see page 3." and "time" not in body
    assert "time" not in rows_of(R["folder"], "NH")[-1]
    pg.close()


def test_a_page_left_open_learns_that_recording_was_switched_off(team, browser):
    R, people = team
    ch = people["CH"].c
    ch.settings(screening={"record_time": True})
    ch.post("/api/screening/verify", {"set": "main", "size": 10, "decoys": 0, "reviewer": "TK"})
    sent = []
    pg = open_page(browser, people["TK"], "screen/verify/main", wait="h2.rec-title")
    pg.on("request", lambda r: sent.append(json.loads(r.post_data)) if r.url.endswith("/api/decide") else None)
    assert "Time recording is on." in pg.locator("aside.side").inner_text()
    ch.settings(screening={"record_time": False})            # the lead switches it off while the page is open
    pg.wait_for_timeout(200)
    pg.keyboard.press("e")
    quiet(pg, 500)
    assert "time" in sent[0] and "time" not in rows_of(R["folder"], "TK")[-1]       # sent once more, and not kept
    assert "Time recording" not in pg.locator("aside.side").inner_text()            # and the page has put itself right
    pg.wait_for_timeout(200)
    pg.keyboard.press("e")
    quiet(pg, 500)
    assert "time" not in sent[1]
    ch.settings(screening={"record_time": True})             # ... and on again
    pg.wait_for_timeout(200)
    pg.keyboard.press("e")
    quiet(pg, 500)
    assert "time" not in sent[2] and "Time recording is on." in pg.locator("aside.side").inner_text()
    pg.wait_for_timeout(300)
    pg.keyboard.press("e")
    quiet(pg, 500)
    assert sent[3]["time"]["views"] == 1 and rows_of(R["folder"], "TK")[-1]["time"]["shown"] >= 250
    pg.close()


def test_the_lead_ends_a_sample_that_cannot_be_finished(team, browser):
    R, people = team
    people["CH"].c.post("/api/screening/verify", {"set": "main", "size": 10, "decoys": 2, "reviewer": "TK"})
    order = T.state(R["folder"])["verify"]["order"]
    tk = people["TK"].c
    for rid in order[:5]:
        tk.decide(rid, "vs", "exclude")
    pg = open_page(browser, people["CH"])
    vs = pg.locator("section.verify")
    assert "5 of 12 done" in vs.inner_text() and "Result" not in vs.inner_text()
    vs.get_by_role("button", name="The sample cannot be finished").click()
    box = pg.locator(".modal")
    assert "Their votes so far (5 of 12) then take effect" in box.inner_text() and "not a way to see them sooner" in box.inner_text()
    box.get_by_role("button", name="End it as it stands").click()
    quiet(pg, 300)
    assert pg.locator(".modal").count() == 1 and "ended" not in T.state(R["folder"])["verify"]      # a reason is needed
    box.get_by_label("Why the sample cannot be finished").fill("Tane has left the team.")
    box.get_by_role("button", name="End it as it stands").click()
    quiet(pg, 700)
    text = pg.locator("section.verify").inner_text()
    assert "5 of 12 done. Ended by the project lead before every record was screened." in text
    assert "Reason given: Tane has left the team." in text and "Result:" in text and "they screened" in text
    assert T.state(R["folder"])["verify"]["ended"]["reason"] == "Tane has left the team."
    pg.close()
    pg = open_page(browser, people["TK"])
    assert pg.locator(".task:has-text('Verification sample')").count() == 0
    assert "Ended by the project lead before every record was screened." in pg.locator("section.verify").inner_text()
    pg.close()
