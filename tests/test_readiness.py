"""What is missing before each step (readiness.py), the guided set-up and the set-up summary.

The checks: a fresh project shows what set-up needs; filling it in clears them; the AI's items appear only when the
AI is planned and only for the project lead; a missing registration warns but never blocks; gaps in the search log
are named; a reviewer sees what they are waiting for but not the lead's own items; the words match the server's
refusals; and the checks are fast on a project of 3,000 records. In the browser (when Playwright is installed): the
guided set-up from a new project to its last screen, and the banner on the screening page."""
import json
import os
import random
import threading
import time
import urllib.request

import pytest

from helpers import record_plan, rob_plan
from reviewdesk import ai, readiness, screening, server, userconfig
from reviewdesk.project import Project, ProjectError

TEAM = [{"id": "CH", "name": "Colin Hawthorne", "role": "lead"}, {"id": "NH", "name": "Nadine Hayes", "role": "reviewer"}]
READY = {"reviewers": TEAM, "question": "What are the effects and accuracy of AI triage tools in emergency departments?",
         "criteria": {"include": ["Prospective evaluation of an AI triage tool"], "exclude": ["Retrospective data only"], "rules": []},
         "screening": {"mode": "sample_then_single", "reviewers": ["NH", "CH"], "single_reviewer": "NH", "second_reviewer": "CH"}}


@pytest.fixture(autouse=True)
def no_ai_service(monkeypatch):
    for k in ("REVIEWDESK_AI_URL", "ELM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    userconfig.update(elm_key="", elm_url="", unpaywall_email="")


def make(tmp_path, name="p") -> Project:
    return Project.create(str(tmp_path / name), "AI in emergency department triage", "Colin Hawthorne", "CH")


def ready(p: Project, **more) -> Project:
    """The settings screening needs, and the risk-of-bias plan."""
    p.update_settings("CH", {**READY, **more})
    record_plan(p)
    return p


def ris(n: int, seed: int = 1) -> bytes:
    rng = random.Random(seed)
    words = "triage emergency artificial intelligence machine learning model nurse patient acuity accuracy prospective".split()
    out = []
    for i in range(n):
        title = " ".join(rng.choice(words) for _ in range(9)) + f" study number {i}"
        out.append(f"TY  - JOUR\nTI  - {title}\nAB  - {' '.join(rng.choice(words) for _ in range(80))}\nAU  - Author{i}, A\n"
                   f"PY  - 20{rng.randint(10, 25)}\nJO  - Journal {i % 40}\nDO  - 10.1000/x{seed}.{i}\nER  - \n")
    return "\n".join(out).encode()


def step(r: dict, sid: str) -> dict:
    return next(s for s in r["steps"] if s["id"] == sid)


def keys(items) -> set:
    return {x["key"] for x in items}


def everything(r: dict) -> list:
    return [x for s in r["steps"] for x in s["missing"] + s["warnings"]]


# ------------------------------------------------------------------------------------------------ set-up
def test_fresh_project_shows_what_set_up_needs(tmp_path):
    p = make(tmp_path)
    r = readiness.check(p, "CH")
    st = step(r, "setup")
    assert st["status"] == "now" and not r["setup_done"]
    assert {"people_two", "plan_two", "plan_single", "criteria", "question", "rob_plan"} <= keys(st["missing"])
    assert all(x["blocks"] and x["who"] == "lead" and x["fix"].startswith("setup/") and x["fix_label"] for x in st["missing"])
    # the words are the screening plan's own (screening.check_plan), so they match what the server says
    texts = " ".join(x["text"] for x in st["missing"])
    for msg in screening.check_plan(p, screening.plan(p, "main")):
        assert msg in texts
    # later steps wait for earlier ones
    assert step(r, "duplicates")["status"] == "later" and step(r, "calibration")["status"] == "later"
    # every step of the plan is there, in order, with the page that shows it
    assert [s["id"] for s in r["steps"]][:3] == ["setup", "register", "searches"]
    assert step(r, "calibration")["page"] == "screen" and step(r, "register")["page"] == "setup"


def test_filling_in_the_settings_clears_the_set_up_items(tmp_path):
    p = make(tmp_path)
    p.update_settings("CH", READY)
    st = step(readiness.check(p, "CH"), "setup")                 # the risk-of-bias plan is needed, as the criteria are
    assert keys(st["missing"]) == {"rob_plan"} and st["missing"][0]["fix"] == "setup/robplan"
    record_plan(p)
    r = readiness.check(p, "CH")
    st = step(r, "setup")
    assert st["missing"] == [] and st["status"] == "done" and r["setup_done"]
    # only the partial criteria warnings come back if one half is emptied
    p.update_settings("CH", {"criteria": {"include": ["x"], "exclude": [], "rules": []}})
    st = step(readiness.check(p, "CH"), "setup")
    assert keys(st["warnings"]) == {"criteria_exclude"} and st["missing"] == []
    p.update_settings("CH", {"ft_reasons": []})
    r = readiness.check(p, "CH")
    assert "ft_reasons" in keys(step(r, "setup")["warnings"])                 # a warning at set-up ...
    ft = step(r, "fulltext")                                                  # ... and needed before full texts, which
    assert ft["status"] == "later" and ft["missing"] == [] and "ft_reasons" in keys(ft["later"])   # cannot start yet


def test_ai_items_only_when_planned_and_only_for_the_lead(tmp_path):
    p = make(tmp_path)
    ready(p)
    r = readiness.check(p, "CH")
    assert "ai" not in [s["id"] for s in r["steps"]]
    assert not [x for x in everything(r) if x["key"].startswith("ai_") or x["key"] == "known_studies"]
    p.update_settings("CH", {"screening": {"ai_second_screener": True}})
    r = readiness.check(p, "CH")
    st = step(r, "ai")
    assert {"ai_address", "ai_key"} <= keys(st["missing"])
    assert {"known_studies", "ai_models_check"} <= keys(st["warnings"])
    assert all("My settings" in x["text"] for x in st["missing"])
    # someone who is not the lead never sees the AI's state, anywhere
    rn = readiness.check(p, "NH")
    assert step(rn, "ai")["missing"] == [] and step(rn, "ai")["warnings"] == []
    assert not [x for x in everything(rn) if "AI" in x["text"] or x["key"].startswith("ai_")]
    # with an address and key set on this computer, only the warnings remain
    userconfig.update(elm_url="https://ai.example.org/v1", elm_key="sk-test-1234567890")
    st = step(readiness.check(p, "CH"), "ai")
    assert st["missing"] == []
    p.update_settings("CH", {"known_studies": [{"doi": "10.1000/known"}]})
    assert "known_studies" not in keys(step(readiness.check(p, "CH"), "ai")["warnings"])


def test_ai_run_with_no_model_is_refused_in_the_same_words(tmp_path, monkeypatch):
    p = make(tmp_path)
    ready(p, screening={**READY["screening"], "ai_second_screener": True})
    ai.save_config(p, "CH", {"passes": {"B": {"model": ""}}})
    item = next(x for x in step(readiness.check(p, "CH"), "ai")["missing"] if x["key"] == "ai_models")
    assert "pass B" in item["text"] and "pass A" not in item["text"]
    monkeypatch.setenv("REVIEWDESK_AI_URL", "http://127.0.0.1:9")          # never reached: refused first
    monkeypatch.setenv("ELM_API_KEY", "test-key")
    with pytest.raises(ProjectError) as e:
        ai.Runner.start(p, "CH", [], "all records")
    assert str(e.value) == item["text"]
    assert item["text"] in readiness.blocking(p, "CH", "ai")


# ------------------------------------------------------------------------------------------------ registration and searches
def test_registration_missing_is_a_warning_not_a_block(tmp_path):
    p = make(tmp_path)
    ready(p)
    r = readiness.check(p, "CH")
    reg = step(r, "register")
    assert "registration" in keys(reg["warnings"]) and reg["missing"] == [] and reg["status"] == "now"
    assert "24a" in next(x for x in reg["warnings"] if x["key"] == "registration")["text"]
    assert "registration" in keys(step(r, "searches")["warnings"])            # before searching
    assert "registration" in keys(step(r, "report")["later"])                 # and missing for reporting, later
    assert readiness.blocking(p, "CH", "register") == [] and readiness.blocking(p, "CH", "searches") == []
    # "not registered yet" with a planned registry still counts as not registered
    p.update_settings("CH", {"registration": {"registry": "PROSPERO", "id": "", "date": "", "url": ""}})
    assert "registration" in keys(step(readiness.check(p, "CH"), "register")["warnings"])
    p.update_settings("CH", {"registration": {"registry": "PROSPERO", "id": "CRD42026000001", "date": "2026-10-01", "url": ""}})
    r = readiness.check(p, "CH")
    assert step(r, "register")["status"] == "done"
    assert "registration" not in keys(x for x in everything(r))


def test_search_log_gaps_are_named(tmp_path):
    p = make(tmp_path)
    ready(p)
    ws = p.worksheet()
    p.save_worksheet("CH", {**ws, "databases": ["medline", "embase", "registers"], "registers": ["ClinicalTrials.gov"]})
    a = p.save_search("CH", {"database": "MEDLINE", "date": "2026-10-30", "string": "triage AND artificial intelligence"})
    p.save_search("NH", {"database": "Embase", "platform": "Ovid", "date": "2026-10-31", "results": "812", "string": "x"})
    st = step(readiness.check(p, "CH"), "searches")
    gaps = [x for x in st["warnings"] if x["key"] == "search_gaps:" + a["id"]]
    assert len(gaps) == 1
    text = gaps[0]["text"]
    assert text.startswith("The MEDLINE search of 30 October 2026 is missing")
    assert "the platform" in text and "the number of records it found" in text and "strategy" not in text.split("PRISMA-S")[0]
    assert gaps[0]["fix"] == "searches/log" and not gaps[0]["blocks"]
    assert not [x for x in st["warnings"] if x["key"].startswith("search_gaps:") and "Embase" in x["text"]]
    left = next(x for x in st["warnings"] if x["key"] == "planned_not_logged")
    assert left["text"].startswith("Trial registers is in the search plan")
    p.save_search("CH", {"database": "ClinicalTrials.gov", "platform": "web", "date": "2026-11-01", "results": "14", "string": "triage AI"})
    assert "planned_not_logged" not in keys(step(readiness.check(p, "CH"), "searches")["warnings"])
    # NH sees the gap in the lead's search too, saying who can put it right
    mine = [x for x in step(readiness.check(p, "NH"), "searches")["warnings"] if x["key"] == "search_gaps:" + a["id"]]
    assert mine and mine[0]["who"] == "other" and "Colin Hawthorne logged it" in mine[0]["text"]


def test_imports_unlinked_mismatched_and_unprocessed(tmp_path):
    p = make(tmp_path)
    ready(p)
    s = p.save_search("CH", {"database": "MEDLINE", "platform": "Ovid", "date": "2026-10-30", "results": "60", "string": "x"})
    p.add_import("CH", "medline.ris", ris(50), {"set": "main", "database": "MEDLINE", "search_id": s["id"]})
    p.add_import("CH", "embase.ris", ris(20, seed=2), {"set": "main", "database": "Embase"})
    r = readiness.check(p, "CH")
    imp = step(r, "import")
    mm = next(x for x in imp["warnings"] if x["key"] == "mismatch:" + s["id"])
    assert "search found 60 records but the files linked to it hold 50" in mm["text"]
    assert "embase.ris (Embase)" in next(x for x in imp["warnings"] if x["key"] == "unlinked")["text"]
    assert "unprocessed" in keys(step(r, "duplicates")["missing"])
    # screening cannot start before the files are de-duplicated, so nothing about it is needed yet: the first screening
    # step keeps what screening.start refuses, in its words, for later
    first = step(r, "calibration")
    with pytest.raises(ProjectError) as e:
        screening.start(p, "CH", "main")
    assert first["status"] == "later" and first["missing"] == [] and str(e.value) in [x["text"] for x in first["later"]]
    p.process_imports("CH")
    r = readiness.check(p, "CH")
    assert "unprocessed" not in keys(step(r, "duplicates")["missing"]) and step(r, "calibration")["missing"] == []
    assert step(r, "calibration")["status"] == "now"


def test_plan_refusals_match_screening_start(tmp_path):
    p = make(tmp_path)
    p.update_settings("CH", {"reviewers": TEAM, "question": "q", "criteria": {"include": ["a"], "exclude": ["b"], "rules": []}})
    p.add_import("CH", "medline.ris", ris(30), {"set": "main", "database": "MEDLINE"})
    p.process_imports("CH")
    r = readiness.check(p, "CH")
    texts = [x["text"] for x in step(r, "calibration")["missing"]]
    assert step(r, "calibration")["status"] == "blocked"
    with pytest.raises(ProjectError) as e:
        screening.start(p, "CH", "main")
    for problem in screening.check_plan(p, screening.plan(p, "main")):        # start joins them into one message
        assert problem in str(e.value) and any(t.startswith(problem) for t in texts)


# ------------------------------------------------------------------------------------------------ who sees what
def test_reviewer_sees_waiting_items_but_not_the_leads(tmp_path):
    p = make(tmp_path)
    p.update_settings("CH", {"reviewers": TEAM})
    rn = readiness.check(p, "NH")
    st = step(rn, "setup")
    assert st["missing"] == [] and len(st["warnings"]) == 1
    w = st["warnings"][0]
    assert w["text"].startswith("Waiting for Colin Hawthorne (the project lead) to ")
    assert "write the review question" in w["text"] and "write the eligibility criteria" in w["text"]
    # what she waits for is information, not something needed from her: not counted, nothing to fix
    assert w["fix"] == "" and w["who"] == "lead" and not w["blocks"] and w["waiting"]
    assert rn["missing"] == 0 and rn["warnings"] == 0
    assert not [x for x in everything(rn) if x["key"] in ("registration", "pairs", "unlinked", "form_builtin")]
    assert not rn["is_lead"] and readiness.check(p, "CH")["is_lead"]
    assert readiness.blocking(p, "NH", "setup") == []


def test_calibration_progress_for_each_reviewer(tmp_path):
    p = make(tmp_path)
    ready(p, screening={**READY["screening"], "calibration_size": 10})
    p.add_import("CH", "medline.ris", ris(100), {"set": "main", "database": "MEDLINE"})
    p.process_imports("CH")
    screening.start(p, "CH", "main")
    cal = p.state_ro()["sets"]["main"]["calibration_rounds"][0]
    for rid in cal:
        p.add_decision("CH", rid, "cal", "include")
    for rid in cal[:4]:
        p.add_decision("NH", rid, "cal", "exclude")
    # the person with records left has the Needed item; the one who has finished is only told what they wait for
    lead = step(readiness.check(p, "CH"), "calibration")
    assert lead["status"] == "now" and lead["missing"] == []
    w = lead["warnings"][0]
    assert w["text"].startswith("Waiting for Nadine Hayes to finish the calibration round (4 of 10 done)") and w["waiting"]
    nh = step(readiness.check(p, "NH"), "calibration")
    assert nh["missing"][0]["text"].startswith("You have 6 records of the calibration round left")
    assert nh["missing"][0]["fix"] == "screen/calibration/main" and nh["missing"][0]["who"] == "me"
    for rid in cal[4:]:
        p.add_decision("NH", rid, "cal", "exclude")
    assert "calibration_discuss" in keys(step(readiness.check(p, "CH"), "calibration")["missing"])
    nh = step(readiness.check(p, "NH"), "calibration")
    assert nh["missing"] == [] and "Talk the disagreements through together with Colin Hawthorne" in nh["warnings"][0]["text"]


def test_full_texts_extraction_and_reporting(tmp_path):
    p = make(tmp_path)
    ready(p, **{"screening": {"mode": "dual", "reviewers": ["NH", "CH"], "calibration_size": 0},
                             "registration": {"registry": "OSF Registries", "id": "", "date": "", "url": "https://osf.io/abcd1"}})
    p.add_import("CH", "medline.ris", ris(6), {"set": "main", "database": "MEDLINE"})
    p.process_imports("CH")
    screening.start(p, "CH", "main")
    recs = [r["id"] for r in p.records()]
    for rid in recs:
        p.add_decision("CH", rid, "ta", "include")
        p.add_decision("NH", rid, "ta", "include" if rid != recs[0] else "exclude")
    r = readiness.check(p, "NH")
    get = step(r, "get_fulltexts")
    assert get["status"] == "now" and {"no_pdf", "email"} <= keys(get["warnings"])
    assert next(x for x in get["warnings"] if x["key"] == "email")["fix"] == "settings"
    userconfig.update(unpaywall_email="nh@example.ac.uk")
    assert "email" not in keys(step(readiness.check(p, "NH"), "get_fulltexts")["warnings"])   # only for whoever has not set it
    assert "task_fulltext" in keys(step(r, "fulltext")["missing"])          # her own work left: needed now
    # both include two reports at full text, disagree on a third, and the fourth waits for the authors
    for who in ("CH", "NH"):
        p.add_decision(who, recs[1], "ft", "include")
        p.add_decision(who, recs[2], "ft", "include")
        p.add_decision(who, recs[4], "ft", "awaiting")
    p.add_decision("CH", recs[3], "ft", "include")
    p.add_decision("NH", recs[3], "ft", "exclude", reason="Wrong population")
    lead = readiness.check(p, "CH")
    assert "ft_conflicts" in keys(step(lead, "fulltext")["warnings"])
    ex = step(lead, "extract")
    assert ex["status"] == "now" and {"form_builtin", "extractors_default"} <= keys(ex["warnings"]) and ex["missing"] == []
    assert "rob_none" in keys(step(lead, "rob")["warnings"])
    rep = keys(step(lead, "report")["warnings"])
    assert {"open_ft", "awaiting", "open_extract", "open_rob"} <= rep and "registration" not in rep   # a link is enough
    assert "task_extract" in keys(step(readiness.check(p, "NH"), "extract")["missing"])           # NH extracts first
    assert not [x for x in everything(readiness.check(p, "NH")) if x["key"] in ("form_builtin", "extractors_default", "ft_conflicts")]
    # once the lead names the extractors and adapts the form, those warnings go
    from reviewdesk import review
    f = review.form(p)
    f["sections"][0]["fields"].append({"id": "triage_scale", "label": "Triage scale used", "type": "text", "mode": "check"})
    review.save_form(p, "CH", {**f, "first": "NH", "second": "CH"})
    ex = step(readiness.check(p, "CH"), "extract")
    assert not {"form_builtin", "extractors_default"} & keys(ex["warnings"])


def test_not_a_member_sees_no_items(tmp_path):
    p = make(tmp_path)
    r = readiness.check(p, "")
    assert everything(r) == [] and r["missing"] == 0


# ------------------------------------------------------------------------------------------------ speed
def test_check_is_fast_on_3000_records(tmp_path):
    p = make(tmp_path)
    ready(p, screening={**READY["screening"], "ai_second_screener": True})
    s = p.save_search("CH", {"database": "MEDLINE", "platform": "Ovid", "date": "2026-10-30", "results": "3000", "string": "x"})
    p.add_import("CH", "medline.ris", ris(3000), {"set": "main", "database": "MEDLINE", "search_id": s["id"]})
    p.process_imports("CH")
    screening.start(p, "CH", "main")
    cal = p.state_ro()["sets"]["main"]["calibration_rounds"][0]
    for rid in cal:
        p.add_decision("CH", rid, "cal", "include")
    for rid in cal[:50]:
        p.add_decision("NH", rid, "cal", "maybe")
    assert len(p.records()) == 3000
    fresh = Project(p.dir)                         # nothing remembered yet: the first Overview load
    t0 = time.perf_counter()
    readiness.check(fresh, "CH")
    cold = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(5):
        r = readiness.check(fresh, "CH")
        readiness.check(fresh, "NH")
    warm = (time.perf_counter() - t0) / 10
    print(f"readiness.check on 3,000 records: first {cold * 1000:.0f} ms, then {warm * 1000:.1f} ms")
    assert cold < 1.0 and warm < 0.25
    assert step(r, "calibration")["status"] == "now"


# ------------------------------------------------------------------------------------------------ the set-up summary
def test_set_up_summary_lists_every_choice(tmp_path):
    p = make(tmp_path)
    p.update_settings("CH", {**READY, "registration": {"registry": "PROSPERO", "id": "", "date": "", "url": ""},
                             "dates": {"searches": "2026-11-30", "update_search": "2027-04-15"}, "risk_of_bias_tools": ["rob2", "probast_ai"]})
    # tools named before 1.3.0 become a draft plan, kept as they were, until the lead records the plan
    text = readiness.summary_text(p)
    assert "Recorded: Not recorded yet: needed before screening starts" in text and "Named before ReviewDesk 1.3.0: RoB 2" in text
    assert "Cluster-randomised trials: RoB 2 for cluster-randomised trials" in text
    record_plan(p, plan=rob_plan("rct", "prediction"))
    p.save_worksheet("CH", {**p.worksheet(), "databases": ["medline", "embase"], "registers": ["ANZCTR"]})
    text = readiness.summary_text(p)
    for want in ("Set-up summary: AI in emergency department triage", "Not registered yet (planned in PROSPERO)",
                 "Final searches run: 30 November 2026", "Update search: 15 April 2027", "Nadine Hayes (NH): Reviewer",
                 "1. Prospective evaluation of an AI triage tool", "20% of the records", "Who screens on their own: Nadine Hayes (NH)",
                 "PROBAST+AI", "before screening started", "1. MEDLINE", "2. Embase", "1. ANZCTR", "not yet adapted"):
        assert want in text, want
    rows = next(x["rows"] for x in readiness.summary(p)["sections"] if x["title"] == "Risk-of-bias plan")
    assert ["Randomised trials (individually randomised, parallel-group)", "RoB 2 (individually randomised, parallel-group "
            "trials)"] in rows
    sec = {s["title"] for s in readiness.summary(p)["sections"]}
    assert "AI second screener" not in sec                                     # only when it is planned
    assert {"The review", "Protocol registration", "Planned dates", "The team", "Eligibility criteria", "Screening plan",
            "Full texts, extraction and risk of bias", "Risk-of-bias plan", "Searches"} <= sec


@pytest.fixture()
def running(tmp_path_factory):
    os.environ["REVIEWDESK_HOME"] = str(tmp_path_factory.mktemp("home"))
    httpd, url = server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base, tok = url.split("/?")[0], url.split("t=")[1]

    def call(method, path, body=None, raw=False):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                     headers={"X-Token": tok, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                data = r.read()
                return (r.status, data if raw else json.loads(data))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
    yield base, url, call
    httpd.shutdown()


def test_routes(running, tmp_path):
    base, url, call = running
    st, _ = call("POST", "/api/project/create", {"parent": str(tmp_path), "name": "r", "title": "Route test", "lead_name": "Colin Hawthorne",
                                                  "lead_id": "CH"})
    assert st == 200
    st, rd = call("GET", "/api/readiness")
    assert st == 200 and rd["is_lead"] and step(rd, "setup")["missing"]
    st, sm = call("GET", "/api/setup_summary")
    assert st == 200 and sm["sections"][0]["title"] == "The review"
    st, data = call("GET", "/download/setup_summary", raw=True)
    assert st == 200 and data.decode("utf-8").startswith("Set-up summary: Route test")


# ------------------------------------------------------------------------------------------------ in the browser
def _browser():
    pw = pytest.importorskip("playwright.sync_api")
    return pw.sync_playwright


def test_guided_set_up_in_the_browser(running, tmp_path):
    sync_playwright = _browser()
    base, url, call = running
    parent = tmp_path / "projects"
    parent.mkdir()
    errors = []
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception as e:                      # noqa: BLE001 - no browser on this computer
            pytest.skip(f"Chromium is not available: {e}")
        ctx = b.new_context(viewport={"width": 1366, "height": 900})
        pg = ctx.new_page()
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(url)
        pg.evaluate("document.addEventListener('securitypolicyviolation', e => console.error('CSP ' + e.violatedDirective))")
        pg.click("text=Create a new project")
        pg.fill("input[placeholder^='e.g. Exercise programmes']", "AI in emergency department triage: prospective clinical evaluations")
        pg.fill("div.modal .grid2 input >> nth=0", "Colin Hawthorne")
        pg.get_by_label("Where to put it", exact=True).fill(str(parent))      # typed or pasted, as well as chosen
        pg.click("div.modal button.primary:has-text('Create project')")
        pg.click("text=Guide me through it (recommended)")
        settings = lambda: call("GET", "/api/app")[1]["project"]["settings"]   # noqa: E731
        go_on = lambda h1: (pg.click("text=Save and continue"), pg.wait_for_selector(f"h1:text-is('{h1}')"))  # noqa: E731

        pg.wait_for_selector("h1:text-is('The review')")
        assert pg.locator(".wz-count").inner_text() == "Screen 1 of 8"
        pg.get_by_label("Short title", exact=True).fill("AI triage review")
        pg.get_by_label("Review question", exact=True).fill("What are the effects of AI triage tools?")
        go_on("Protocol registration and planned dates")
        assert settings()["short_title"] == "AI triage review" and settings()["question"] == "What are the effects of AI triage tools?"

        pg.check("text=Yes, it is registered")
        pg.get_by_label("Registry", exact=True).fill("PROSPERO")
        pg.get_by_label("Registration number", exact=True).fill("CRD42026000001")
        pg.get_by_label("Final searches run", exact=True).fill("2026-11-30")
        pg.get_by_label("Update search", exact=True).fill("2027-04-15")
        go_on("The team")
        s = settings()
        assert s["registration"]["id"] == "CRD42026000001" and s["dates"]["update_search"] == "2027-04-15"

        pg.click("text=Add a person")
        pg.fill("input[aria-label='Name of person 2']", "Nadine Hayes")
        pg.fill("input[aria-label='Initials of person 2']", "NH")
        go_on("Eligibility criteria")
        assert [r["id"] for r in settings()["reviewers"]] == ["CH", "NH"]

        pg.get_by_label("Include (every point must be met)", exact=True).fill("Prospective evaluation\nIn an emergency department")
        pg.get_by_label("Exclude (any one point rules a record out)", exact=True).fill("Retrospective data only")
        go_on("The screening plan")
        assert settings()["criteria"]["include"] == ["Prospective evaluation", "In an emergency department"]

        pg.get_by_label("First reviewer", exact=True).select_option("NH")
        pg.get_by_label("Second reviewer", exact=True).select_option("CH")
        pg.get_by_label("Who screens the rest on their own", exact=True).select_option("NH")
        pg.check("text=Use the AI as a second screener behind single screening")
        pg.get_by_label("Second reviewer for those records", exact=True).select_option("CH")
        go_on("Full texts, extraction and risk of bias")
        sc = settings()["screening"]
        assert sc["reviewers"] == ["NH", "CH"] and sc["single_reviewer"] == "NH" and sc["ai_second_screener"]

        # the risk-of-bias plan: designs ticked with their recommended tools, and a tool the lead defines
        pg.get_by_role("checkbox", name="Randomised trials (individually randomised, parallel-group)", exact=True).check()
        pg.get_by_role("checkbox", name="Diagnostic test accuracy studies", exact=True).check()
        pg.get_by_role("button", name="Define a tool").click()
        pg.fill("#rt-name", "Qualitative appraisal checklist")
        pg.fill("#rt-version", "2020 edition")
        pg.fill("#rt-cite", "Lockwood C, et al. Int J Evid Based Healthc 2015;13:179-87")
        pg.fill("input[aria-label='Item 1']", "Methods fit the question")
        pg.fill("input[aria-label='Item 2']", "Participants' voices represented")
        pg.click("text=The tool has an overall judgement")
        # on the items' own answers, in order: "never less severe than the worst item" is ticked at first (F7)
        assert pg.get_by_role("checkbox", name="The overall judgement is never less severe than the worst item").is_checked()
        pg.click("div.modal button.primary:has-text('Add the tool')")
        pg.click("text=Add another study design")
        pg.fill("input[aria-label='Study design']", "Qualitative studies")
        pg.locator("select[aria-label='Tool for this design']").select_option(label="Qualitative appraisal checklist")
        go_on("The AI second screener")                                       # shown now that the AI is planned
        plan = call("GET", "/api/rob/plan")[1]
        assert plan["recorded"] and plan["plan"]["before_screening"]
        assert [(d["name"], d["tool"]) for d in plan["plan"]["designs"]][:2] == [
            ("Randomised trials (individually randomised, parallel-group)", "rob2"), ("Diagnostic test accuracy studies", "quadas2")]
        (tool,) = plan["plan"]["tools"].values()
        assert plan["plan"]["designs"][2]["name"] == "Qualitative studies" and plan["plan"]["designs"][2]["tool"] in plan["plan"]["tools"]
        assert [x["label"] for x in tool["items"]] == ["Methods fit the question", "Participants' voices represented"]
        assert tool["overall"] == {"use": True, "same": True, "rule": True} and tool["scale"] == {"preset": "ynu"}
        assert pg.locator(".wz-count").inner_text() == "Screen 7 of 9"
        assert call("GET", "/api/extract")[1]["people"]["set"]                 # the extractors were chosen

        pg.get_by_label("Eligible studies you already know", exact=True).fill("10.1000/known1")
        go_on("The searches")
        assert settings()["known_studies"] == [{"doi": "10.1000/known1"}]

        pg.check("input[aria-label='MEDLINE']")
        pg.check("input[aria-label='Embase']")
        pg.get_by_label("Trial registers (one per line)", exact=True).fill("ClinicalTrials.gov\nANZCTR")
        go_on("Check and finish")
        ws = call("GET", "/api/worksheet")[1]
        assert set(ws["databases"]) == {"medline", "embase", "registers"} and ws["registers"] == ["ClinicalTrials.gov", "ANZCTR"]

        assert pg.locator(".callout.good").inner_text().startswith("The set-up is complete")
        with ctx.expect_page() as summary:
            pg.click("text=Open the summary to print (new tab)")
        sp = summary.value
        sp.wait_for_selector(".wz-sum")
        assert "CRD42026000001" in sp.inner_text("body") and "ANZCTR" in sp.inner_text("body")
        sp.close()
        pg.get_by_role("button", name="Finish", exact=True).click()
        pg.wait_for_selector("section.next")
        assert errors == []
        b.close()


def test_screening_banner_appears_and_goes_in_the_browser(running, tmp_path):
    sync_playwright = _browser()
    base, url, call = running
    st, _ = call("POST", "/api/project/create", {"parent": str(tmp_path), "name": "b", "title": "Banner test", "lead_name": "Colin Hawthorne",
                                                  "lead_id": "CH"})
    assert st == 200
    # records are in, so screening could start but for the plan (before that, nothing about screening is needed yet)
    p = Project(call("GET", "/api/app")[1]["project"]["path"])
    p.add_import("CH", "medline.ris", ris(40), {"set": "main", "database": "MEDLINE"})
    p.process_imports("CH")
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception as e:                      # noqa: BLE001
            pytest.skip(f"Chromium is not available: {e}")
        pg = b.new_page(viewport={"width": 1366, "height": 900})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(url)
        pg.goto(base + "/#/screening")
        pg.wait_for_selector(".rd-banner")
        text = pg.inner_text(".rd-banner")
        assert "Before you go on" in text and "Choose the two reviewers who screen in duplicate" in text
        assert pg.locator(".rd-banner + *").count() and pg.evaluate(
            "document.querySelector('.rd-banner').previousElementSibling.classList.contains('page-head')")
        # its fix opens the screening plan on the set-up page
        pg.locator(".rd-banner li", has_text="Choose who screens on their own").get_by_role("button").click()
        pg.wait_for_url("**/#/setup/plan")
        # put it right: the team, the plan, the question and criteria, and some records
        assert call("POST", "/api/settings", {"changes": {**READY, "screening": {"mode": "dual", "reviewers": ["NH", "CH"]}}})[0] == 200
        assert call("POST", "/api/rob/plan", {"plan": rob_plan("rct")})[0] == 200
        pg.goto(base + "/#/overview")
        pg.goto(base + "/#/screening")
        pg.wait_for_selector("h1:text-is('Screen titles and abstracts')")
        pg.wait_for_timeout(600)
        assert pg.locator(".rd-banner").count() == 0
        assert errors == []
        b.close()
