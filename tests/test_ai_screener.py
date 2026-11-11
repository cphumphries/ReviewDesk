"""The AI second screener against the stand-in service: what is sent, how replies are read, and what happens when
the service fails (errors, time-outs, unreadable or cut-off replies, a busy service, a wrong key): a failure always
counts as "kept", a run that cannot work stops at once with a plain message, and a new run sends only what is missing.
No real AI service or key is used."""
from __future__ import annotations

import json
import os

import pytest

from helpers import reference_plan, reference_project, ris, tiny_records
from mock_elm import MODEL_A, MODEL_B, TEST_KEY
from reviewdesk import ai
from reviewdesk.project import Project


@pytest.fixture
def lead(desk, elm, tmp_path):
    c = desk.client()
    c.post("/api/user", {"elm_url": elm.url, "elm_key": TEST_KEY})
    folder = reference_project(c, str(tmp_path / "OneDrive"), screening=reference_plan(calibration_size=0))
    c.records = tiny_records(12, seed=21)
    c.records[3]["abstract"] = "Background: " + "A very long abstract about emergency triage. " * 300
    c.records[4]["authors"] = ["Secretname, Wiremu"]
    c.add_import(ris(*c.records), "medline.ris", "MEDLINE")
    c.process()
    c.folder, c.elm = folder, elm
    return c


def answers(c, ps="A") -> dict:
    out = {}
    for f in os.listdir(os.path.join(c.folder, "ai")):
        if f.startswith(f"answers_{ps}@"):
            for line in open(os.path.join(c.folder, "ai", f), encoding="utf-8"):
                d = json.loads(line)
                out[d["id"]] = d
    return out


def title_of(c, rid):
    return next(r["title"] for r in c.get("/api/records")["records"] if r["id"] == rid)


def test_the_service_lists_its_models(lead):
    c = lead
    m = c.post("/api/ai/models", {})
    assert MODEL_A in m["models"] and MODEL_B in m["models"] and m["elm"] is False
    assert c.elm.model_lists == 1 and c.elm.auth[-1] == "Bearer " + TEST_KEY


def test_every_record_is_read_by_both_models_and_kept_unless_both_exclude(lead):
    c = lead
    rid_of = {r["title"]: r["id"] for r in c.get("/api/records")["records"]}
    first = c.records[0]["title"]
    c.elm.decide = lambda call: "EXCLUDE" if call.title != first and (call.model == MODEL_A or call.title.startswith(("A", "E", "I"))) else "INCLUDE"
    st = c.run_ai()
    assert st["message"] == "Finished" and st["done"] == 24 and st["errors"] == 0
    assert c.elm.count(MODEL_A) == 12 and c.elm.count(MODEL_B) == 12
    p = Project(c.folder)
    comb = ai.combined(p)
    for t, rid in rid_of.items():
        both = t != first and t.startswith(("A", "E", "I"))
        assert comb[rid] == ("exclude" if both else "keep")
    cov = c.get("/api/ai")["coverage"]["main"]
    assert cov == {"records": 12, "done": 12, "failed": 0, "missing": 0}


def test_only_the_title_abstract_and_details_of_a_record_are_sent(lead):
    c = lead
    c.run_ai()
    for call in c.elm.calls:
        assert "Secretname" not in call.user and "Wiremu" not in call.user           # no authors
        assert "R0000" not in call.user and "10.1136/" not in call.user               # no record IDs or DOIs
        assert call.user.startswith("Record to screen\nTitle: ") and "| Journal: Emergency Medicine Journal |" in call.user
        assert "REVIEW QUESTION" in call.system and "Retrospective development or validation only" in call.system
        assert call.headers.get("Authorization") == "Bearer " + TEST_KEY
    long = [x for x in c.elm.calls if x.title == c.records[3]["title"]][0]
    assert long.abstract.endswith("[abstract truncated]") and len(long.abstract) < 6100
    a = next(x for x in c.elm.calls if x.model == MODEL_A)
    b = next(x for x in c.elm.calls if x.model == MODEL_B)
    assert a.body["reasoning_effort"] == "high" and a.body["max_tokens"] == 16384 and "response_format" not in a.body
    assert b.body["temperature"] == 0.0 and b.body["response_format"] == {"type": "json_object"}


def test_a_failed_call_counts_as_kept_after_the_retries(lead, quick_retries):
    c = lead
    bad = {c.records[5]["title"], c.records[6]["title"]}
    c.elm.default = "EXCLUDE"
    c.elm.fail("http500", when=lambda call: call.title in bad and call.model == MODEL_B)
    st = c.run_ai()
    assert st["message"] == "Finished" and st["errors"] == 2
    assert c.elm.count(MODEL_B, c.records[5]["title"]) == 5            # five attempts
    assert [round(w) for w in quick_retries[:4]] in ([1, 2, 4, 8], [2, 3, 5, 9], [1, 2, 4, 9], [1, 3, 5, 8]) or len(quick_retries) >= 8
    cov = c.get("/api/ai")["coverage"]["main"]
    assert cov == {"records": 12, "done": 10, "failed": 2, "missing": 0}
    comb = ai.combined(Project(c.folder))
    kept = {title_of(c, rid) for rid, v in comb.items() if v == "keep"}
    assert kept == bad
    b = answers(c, "B")
    errs = [d for d in b.values() if d["decision"] == "ERROR"]
    assert len(errs) == 2 and all(d["reason"] == "Unreachable: no answer after 5 attempts: the service returned an error"
                                  for d in errs)                       # the error's code is in the error log only


def test_a_new_run_sends_only_the_records_still_without_an_answer(lead, quick_retries):
    c = lead
    bad = c.records[7]["title"]
    c.elm.fail("http500", when=lambda call: call.title == bad)
    c.run_ai()
    c.elm.heal()
    c.elm.reset()
    st = c.run_ai()
    assert st["message"] == "Finished" and c.elm.count() == 2 and c.elm.titles() == {bad}
    assert c.get("/api/ai")["coverage"]["main"]["failed"] == 0


def test_a_call_that_times_out_counts_as_kept(lead, quick_retries):
    c = lead
    c.elm.hang = 1.5                                           # longer than the (shortened) time limit of each call
    slow = c.records[8]["title"]
    c.elm.fail("timeout", when=lambda call: call.title == slow and call.model == MODEL_B)
    st = c.run_ai(wait=60)
    assert st["message"] == "Finished" and st["errors"] == 1
    d = next(d for d in answers(c, "B").values() if d["decision"] == "ERROR")
    assert "could not be reached" in d["reason"]


@pytest.mark.parametrize("mode, calls, words", [("drop", 5, "could not be reached"), ("not_json", 1, "the reply could not be read"),
                                                ("no_choices", 1, "the reply had no answer in it")])
def test_a_dropped_connection_or_a_reply_that_is_not_an_answer_counts_as_kept(lead, quick_retries, mode, calls, words):
    c = lead
    t = c.records[4]["title"]
    c.elm.default = "EXCLUDE"
    c.elm.fail(mode, when=lambda call: call.title == t and call.model == MODEL_B)
    st = c.run_ai()
    assert st["message"] == "Finished" and st["errors"] == 1
    assert c.elm.count(MODEL_B, t) == calls
    d = next(d for d in answers(c, "B").values() if d["decision"] == "ERROR")
    assert words in d["reason"]
    comb = {title_of(c, k): v for k, v in ai.combined(Project(c.folder)).items()}
    assert comb[t] == "keep" and list(comb.values()).count("keep") == 1


def test_an_unreadable_reply_is_asked_for_again_and_then_counts_as_kept(lead):
    c = lead
    once, always = c.records[1]["title"], c.records[2]["title"]
    c.elm.default = "EXCLUDE"
    c.elm.fail("unreadable", n=1, when=lambda call: call.title == once and call.model == MODEL_B)
    c.elm.fail("unreadable", when=lambda call: call.title == always and call.model == MODEL_B)
    c.run_ai()
    retry = [x for x in c.elm.calls if x.title == once and x.model == MODEL_B]
    assert len(retry) == 2 and retry[1].retry and "Reply with the JSON object only" in retry[1].body["messages"][-1]["content"]
    b = {title_of(c, k): v for k, v in answers(c, "B").items()}
    assert b[once]["decision"] == "EXCLUDE" and b[once]["retried"] is True
    assert b[always]["decision"] == "UNPARSED" and b[always]["reason"] == "reply could not be read"
    comb = {title_of(c, k): v for k, v in ai.combined(Project(c.folder)).items()}
    assert comb[always] == "keep" and comb[once] == "exclude"


def test_a_reply_cut_off_is_asked_for_again_with_more_room(lead):
    c = lead
    t = c.records[9]["title"]
    c.elm.fail("cut", n=1, when=lambda call: call.title == t and call.model == MODEL_B)
    c.run_ai()
    calls = [x for x in c.elm.calls if x.title == t and x.model == MODEL_B]
    assert [x.body["max_tokens"] for x in calls] == [600, 1200]


def test_reasoning_that_never_ends_counts_as_kept(lead):
    c = lead
    t = c.records[10]["title"]
    c.elm.default = "EXCLUDE"
    c.elm.fail("think", when=lambda call: call.title == t and call.model == MODEL_A)
    c.run_ai()
    a = {title_of(c, k): v for k, v in answers(c, "A").items()}
    assert a[t]["decision"] == "UNPARSED"


def test_a_busy_service_is_waited_for_as_long_as_it_asks(lead, quick_retries):
    c = lead
    t = c.records[11]["title"]
    c.elm.retry_after = "7"
    c.elm.fail("429", n=2, when=lambda call: call.title == t and call.model == MODEL_A)
    st = c.run_ai()
    assert st["errors"] == 0 and c.elm.count(MODEL_A, t) == 3
    assert sum(1 for w in quick_retries if 7 <= w < 8) == 2


def test_a_service_that_refuses_json_mode_is_read_as_plain_text(lead):
    c = lead
    c.elm.refuse_json_mode = True
    st = c.run_ai()
    assert st["message"] == "Finished" and st["errors"] == 0
    ended = [e for e in c.get("/api/audit")["entries"] if e["action"] == "AI run ended"][-1]
    assert "JSON mode not accepted: plain text parsed" in ended["details"]["adaptations"]["B"]


def test_the_audit_log_does_not_tell_a_reviewer_how_many_of_the_ais_answers_failed(lead):
    """The number of failed answers is one of the AI's figures, which only the lead sees on the pages: the audit log
    (the page and the download) must not give it to the other reviewer."""
    c = lead
    recs = sorted(c.get("/api/records")["records"], key=lambda r: r["id"])
    first = recs[0]["title"]
    c.elm.fail("unreadable", when=lambda call: call.title == first and call.model == MODEL_B)
    st = c.run_ai()
    assert st["errors"] == 1
    ended = [e for e in c.get("/api/audit")["entries"] if e["action"] == "AI run ended"][-1]
    assert ended["details"]["errors"] == st["errors"]                    # the lead sees it
    status, _, data = c.download("audit")
    assert status == 200 and b"errors" in data
    c.be("NH")
    ended = [e for e in c.get("/api/audit")["entries"] if e["action"] == "AI run ended"][-1]
    assert "errors" not in ended["details"] and ended["details"]["done"] == st["done"]
    status, _, data = c.download("audit")
    line = [x for x in data.decode("utf-8-sig").splitlines() if "AI run ended" in x][-1]
    assert status == 200 and "errors" not in line and "done" in line


def test_a_wrong_key_stops_the_run_at_once_with_a_plain_message(lead):
    c = lead
    c.elm.key = "a-different-key"
    st = c.run_ai()
    assert st.get("error") and st["message"] == "The AI service refused the key. Check the key under My settings."
    assert c.elm.count() == 1 and answers(c, "A") == {}


def test_a_service_that_cannot_be_reached_stops_the_run_at_once(lead, quick_retries):
    c = lead
    c.elm.stop()
    st = c.run_ai()
    assert st.get("error") and st["message"].startswith(f"ReviewDesk could not reach the AI service at {c.elm.url}. Check the "
                                                        "address under My settings, and your internet connection (or VPN)")
    assert "Nothing else was sent." in st["message"] and "Errno" not in st["message"] and "urlopen" not in st["message"]


def test_eight_failures_in_a_row_stop_the_run_and_keep_what_it_got(lead, quick_retries):
    c = lead
    first = c.records[0]["title"]
    c.elm.fail("http500", when=lambda call: call.title != first)
    st = c.run_ai()
    assert "Stopped: the AI service stopped answering (8 calls in a row failed" in st["message"]
    assert len(answers(c, "A")) + len(answers(c, "B")) < 24
    assert c.get("/api/ai")["coverage"]["main"]["missing"] > 0


def test_a_model_the_service_does_not_offer_stops_the_run_before_it_starts(lead):
    c = lead
    c.post("/api/ai/config", {"passes": {"A": {"model": "Qwen/Qwen9-Imaginary"}}})
    e = c.error("POST", "/api/ai/run", {"scope": "all"})
    assert "Your AI service does not offer Qwen/Qwen9-Imaginary" in e.message
    assert c.elm.count() == 0


def test_the_university_service_never_sends_records_to_a_model_hosted_elsewhere(lead):
    c = lead
    c.post("/api/user", {"elm_url": "https://elm.edina.ac.uk/api/v1", "elm_key": "test-key-for-elm-0000"})
    c.post("/api/ai/config", {"passes": {"B": {"model": "openai/gpt-4o"}}})
    e = c.error("POST", "/api/ai/run", {"scope": "all"})
    assert "openai/gpt-4o is not a University-hosted model" in e.message


def test_only_the_lead_runs_the_ai(lead):
    c = lead
    c.be("NH")
    e = c.error("POST", "/api/ai/run", {"scope": "all"})
    assert e.status == 400 or e.status == 403
    assert "Only the project lead" in e.message


def test_the_prompts_are_fixed_when_screening_starts(lead):
    c = lead
    c.run_ai()
    before = c.get("/api/ai")["fingerprints"]
    c.settings(criteria={"include": ["Adults", "AI triage tools"], "exclude": ["Reviews"], "rules": []})
    changed = c.get("/api/ai")["fingerprints"]
    assert changed != before                                # before screening starts, the prompts follow the criteria
    assert c.get("/api/ai")["coverage"]["main"]["missing"] == 12        # and old answers no longer count
    c.run_ai()
    c.post("/api/screening/start", {"set": "main"})
    c.settings(criteria={"include": ["Children only"], "exclude": [], "rules": []})
    assert c.get("/api/ai")["fingerprints"] == changed and c.get("/api/ai")["coverage"]["main"]["missing"] == 0
    fixed = c.get("/api/ai")["prompts"]["A"]
    assert "Adults" in fixed and "Children only" not in fixed
    names = os.listdir(os.path.join(c.folder, "ai", "prompts"))
    assert f"pass_A_{changed['A']}.txt" in names            # every prompt used is kept, to report


def test_the_set_up_cannot_change_once_its_answers_have_been_used(lead):
    c = lead
    c.run_ai()
    c.post("/api/screening/start", {"set": "main"})
    for who in ("NH", "CH"):
        c.be(who)
        for it in c.queue("sample"):
            c.decide(it["id"], "ta", "exclude")
    e = c.error("POST", "/api/ai/config", {"passes": {"A": {"model": MODEL_B}}})
    assert "AI models and settings are now fixed" in e.message
    c.post("/api/screening/apply", {"set": "main"})
    e = c.error("POST", "/api/ai/config", {"passes": {"A": {"model": MODEL_B}}})
    assert "cannot change now" in e.message


@pytest.mark.parametrize("reply, want", [
    ('{"decision": "EXCLUDE", "criterion": "design", "reason": "retrospective"}', "EXCLUDE"),
    ('<think>Maybe EXCLUDE? {"decision": "EXCLUDE"}</think>\n{"decision": "INCLUDE", "reason": "ok"}', "INCLUDE"),
    ('{"decision": "EXCLUDE"} and then {"decision": "MAYBE"}', "MAYBE"),               # conflicting: the most inclusive
    ('The decision is MAYBE because the setting is unclear.', "MAYBE"),
    ('I would EXCLUDE this record.', None),                                             # loose prose never excludes
    ('<think>still thinking about the criteria', None),
    ('```json\n{"decision": "include", "criterion": "none", "reason": "meets all"}\n```', "INCLUDE"),
    ('', None),
])
def test_how_a_reply_is_read(reply, want):
    got = ai.parse_reply(reply)
    assert (got[0] if got else None) == want


def test_service_problems_are_put_in_plain_words_and_the_detail_goes_to_the_error_log(lead):
    from reviewdesk import userconfig
    c = lead
    log = os.path.join(userconfig.data_dir(), "errors.jsonl")
    c.elm.key = "a-different-key"
    e = c.error("POST", "/api/ai/models", {})
    assert e.message == "The AI service refused the request. Check the key under My settings."
    c.elm.stop()
    e = c.error("POST", "/api/ai/models", {})
    assert e.message == (f"ReviewDesk could not reach the AI service at {c.elm.url}. Check the address under My settings, "
                         "and your internet connection (or VPN).")
    assert "Connection refused" in open(log, encoding="utf-8").read()            # the detail is kept, off the screen


def test_a_failed_test_call_counts_and_each_run_logs_what_it_sent(lead):
    c = lead
    recs = sorted(c.get("/api/records")["records"], key=lambda r: r["id"])
    first = recs[0]["title"]                                   # each model's first call is the run's test call
    c.elm.fail("unreadable", when=lambda call: call.title == first and call.model == MODEL_B)
    st = c.run_ai()
    assert st["errors"] == 1 and st["done"] == 24
    audit = c.get("/api/audit")["entries"]
    start = [x for x in audit if x["action"] == "AI run started"][-1]["details"]
    end = [x for x in audit if x["action"] == "AI run ended"][-1]["details"]
    assert (start["records_checked"], start["records_sent"], start["calls"], start["retries"]) == (12, 12, 24, 0)
    assert end["errors"] == 1 and end["done"] == 24
    st = c.run_ai()                                            # only the unreadable answer is tried again
    assert st["errors"] == 1 and st["done"] == 1
    audit = c.get("/api/audit")["entries"]
    start = [x for x in audit if x["action"] == "AI run started"][-1]["details"]
    assert (start["records_checked"], start["records_sent"], start["calls"], start["retries"]) == (12, 1, 1, 1)
    from reviewdesk import reporting
    rows = reporting._run_rows(reporting._ai_runs(Project(c.folder)))
    assert [r[3:6] for r in rows] == [["12", "24", "1"], ["1", "1 (1 was a retry)", "1"]]
