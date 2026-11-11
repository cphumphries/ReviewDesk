"""Rating the certainty of the evidence with GRADE: outcomes defined by the lead, ratings by the two reviewers made
independently and kept from each other until both have finished, the certainty worked out by GRADE's rules, agreement
by the lead, the evidence profile and its workbook, and the data checks on the certainty folder. Projects are built
the way a team builds them: records imported, screened, and included at full text.

GRADE sources the rules follow: guidance 18 (ROBINS-I, Schünemann et al. 2019), 21 (test accuracy, Schünemann et al.
2020), 34 (imprecision, Zeng et al. 2022), guidelines 9 (rating up, Guyatt et al. 2011), informative statements
(Santesso et al. 2020) and narrative ratings (Murad et al. 2017)."""
import http.client
import io
import json
import os
import re
import threading
import zipfile
from xml.etree import ElementTree

import pytest

from helpers import record_plan, rob_plan
from reviewdesk import certainty as C
from reviewdesk import docx, readiness, reporting, review, screening, server, userconfig, util
from reviewdesk.project import PermissionDenied, Project, ProjectError

TITLES = ["Machine learning triage: a cluster randomised trial", "AI-supported triage: a controlled before-after study",
          "Validation of an AI triage tool against nurse triage", "Validation of an AI triage tool: second report",
          "An unrelated record"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWDESK_HOME", str(tmp_path / "home"))
    return tmp_path


def build(tmp_path) -> Project:
    """CH (lead) and NH screen in duplicate; three studies are included at full text (the third has two reports)."""
    p = Project.create(str(tmp_path / "review"), "AI in emergency department triage", "Colin Hawthorne", "CH")
    p.update_settings("CH", {"reviewers": [{"id": "CH", "name": "Colin Hawthorne", "role": "lead"},
                                           {"id": "NH", "name": "Nadine Hayes", "role": "reviewer"},
                                           {"id": "SP", "name": "Sam Patel", "role": "reviewer"}],
                             "question": "Do AI triage tools improve emergency department triage?",
                             "criteria": {"include": ["Prospective evaluation"], "exclude": [], "rules": []},
                             "screening": {"reviewers": ["NH", "CH"], "mode": "dual", "calibration_size": 0,
                                           "single_reviewer": "NH", "second_reviewer": "CH"}})
    ris = "".join(f"TY  - JOUR\nTI  - {t}\nAU  - Author{i}, A\nPY  - 202{i}\nJO  - Emerg Med J\nAB  - Abstract {i}.\nER  - \n\n"
                  for i, t in enumerate(TITLES, 1))
    p.add_import("CH", "medline.ris", ris.encode(), {"set": "main", "database": "MEDLINE"})
    p.process_imports("CH")
    record_plan(p, "CH", rob_plan("rct", "before_after", "its", "nrse", "prognostic_factor", "dta", "prediction"))
    screening.start(p, "CH", "main")
    ids = sorted(r["id"] for r in p.records())
    names = ["Lee 2025", "Okafor 2024", "Haddad 2026", "Haddad 2026"]
    for who in ("NH", "CH"):
        for rid in ids:
            p.add_decision(who, rid, "ta", "include")
        for rid, name in zip(ids[:4], names):
            p.add_decision(who, rid, "ft", "include", study=name)
    return p


@pytest.fixture
def proj(home):
    return build(home)


def keys(p):
    return {s["label"]: s["key"] for s in review.studies(p)}


def outcome(p, name="Time to triage", kind="effect", studies=("Okafor 2024",), **kw):
    k = keys(p)
    return C.define(p, "CH", {"name": name, "importance": kw.get("importance", "critical"), "kind": kind,
                              "comparison": kw.get("comparison", "AI-supported triage compared with usual triage"),
                              "measure": "minutes", "synthesis": kw.get("synthesis", ""),
                              "studies": [k[s] for s in studies]})["o"]


VERY_UNCERTAIN = ("The evidence is very uncertain about the effect of AI-supported triage on time to triage compared "
                  "with usual triage.")


def rate_all(p, who, oid, start="nrs", rob=-1, imprecision=-1, upgrades=None, statement=VERY_UNCERTAIN,
             effect="2 studies, 900 patients; shorter in both", why="Confounding by indication", up_why=""):
    """Every rating for one outcome. The defaults (Low start, −1, −1) give very low certainty, worded to match."""
    C.rate(p, who, oid, "start", start, "Planned in the protocol" if start == "nrs" else "")
    C.rate(p, who, oid, "rob", rob, why if rob else "")
    for f in ("inconsistency", "indirectness", "publication"):
        C.rate(p, who, oid, f, 0)
    C.rate(p, who, oid, "imprecision", imprecision, "Few participants" if imprecision else "")
    for f, v in (upgrades or {}).items():
        C.rate(p, who, oid, f, v, "Large and consistent effect")
    if up_why:
        C.rate(p, who, oid, "up_why", up_why)
    C.rate(p, who, oid, "effect", effect)
    C.rate(p, who, oid, "statement", statement)


# ------------------------------------------------------------------------------------------------ outcomes
def test_only_the_lead_defines_outcomes_and_changes_are_audited(proj):
    p = proj
    k = keys(p)
    with pytest.raises(PermissionDenied):
        C.define(p, "NH", {"name": "Time to triage", "importance": "critical", "kind": "effect", "studies": [k["Lee 2025"]]})
    with pytest.raises(ProjectError, match="name"):
        C.define(p, "CH", {"name": " ", "importance": "critical", "kind": "effect"})
    with pytest.raises(ProjectError, match="important"):
        C.define(p, "CH", {"name": "Time to triage", "importance": "very", "kind": "effect"})
    with pytest.raises(ProjectError, match="kind"):
        C.define(p, "CH", {"name": "Time to triage", "importance": "critical", "kind": "prognosis"})
    with pytest.raises(ProjectError, match="no longer among the included"):
        C.define(p, "CH", {"name": "Time to triage", "importance": "critical", "kind": "effect", "studies": ["R99999"]})
    oid = outcome(p, studies=("Okafor 2024", "Haddad 2026"))
    o = dict(C.outcomes(p))[oid]
    assert o["name"] == "Time to triage" and C.studies_of(p, o)[0] == [k["Okafor 2024"], k["Haddad 2026"]]
    assert o["by"] == "CH"
    C.define(p, "CH", {"name": "Time to triage (minutes)", "importance": "important", "kind": "effect",
                       "studies": [k["Okafor 2024"]]}, oid)
    assert dict(C.outcomes(p))[oid]["name"] == "Time to triage (minutes)" and len(C.outcomes(p)) == 1
    acts = [e for e in p.audit_entries() if "certainty outcome" in e["action"]]
    assert [e["action"] for e in acts] == ["add a certainty outcome", "change a certainty outcome"]
    assert set(acts[1]["details"]["changed"]) >= {"name", "importance", "studies"}
    C.define(p, "CH", {}, oid, deleted=True)
    assert C.outcomes(p) == [] and p.audit_entries()[-1]["action"] == "remove a certainty outcome"
    assert "no longer exists" in C.h_outcome(p, "NH", {"o": [oid]}, {})["gone"]
    with pytest.raises(ProjectError, match="no longer exists"):
        C.rate(p, "NH", oid, "rob", 0)
    assert os.path.isfile(p.path("certainty", f"outcomes@{p.dev()}.jsonl"))


def test_changing_kind_or_studies_sends_a_finished_reviewer_back_and_is_refused_once_both_have_finished(proj):
    p = proj
    k = keys(p)
    oid = outcome(p)
    rate_all(p, "NH", oid)
    C.set_done(p, "NH", oid, True)
    assert C.summary(p, oid, dict(C.outcomes(p))[oid])["state_a"] == "finished"
    C.define(p, "CH", {"name": "Time to triage", "importance": "critical", "kind": "effect",
                       "studies": [k["Okafor 2024"], k["Lee 2025"]]}, oid)
    d = dict(C.outcomes(p))[oid]
    assert C.summary(p, oid, d)["state_a"] == "check again"
    assert C.outcome_view(p, oid, "NH")["stale_mine"] is True
    C.rate(p, "NH", oid, "imprecision", -2, "Very few events")       # NH can change ratings again
    C.set_done(p, "NH", oid, True)
    rate_all(p, "CH", oid)
    C.set_done(p, "CH", oid, True)
    assert C.both_done(p, oid)
    with pytest.raises(ProjectError, match="can no longer change"):
        C.define(p, "CH", {"name": "Time to triage", "importance": "critical", "kind": "effect",
                           "studies": [k["Okafor 2024"]]}, oid)
    C.define(p, "CH", {"name": "Time to triage, minutes", "importance": "critical", "kind": "effect",
                       "studies": [k["Okafor 2024"], k["Lee 2025"]]}, oid)       # the name may still change
    assert C.both_done(p, oid)


# ------------------------------------------------------------------------------------------------ independence and blinding
def test_ratings_are_independent_and_blind_until_both_have_finished(proj):
    p = proj
    oid = outcome(p)
    rate_all(p, "NH", oid, rob=-1, why="NH: only Okafor 2024 is at serious risk")
    rate_all(p, "CH", oid, rob=-2, why="CH: both studies at serious risk")
    assert os.path.isfile(p.path("certainty", f"NH@{p.dev()}.jsonl")) and os.path.isfile(p.path("certainty", f"CH@{p.dev()}.jsonl"))
    with pytest.raises(ProjectError, match="Only the two reviewers"):
        C.rate(p, "SP", oid, "rob", -1, "x")
    v = C.outcome_view(p, oid, "NH")
    assert v["mine"]["rob"]["v"] == -1 and v["comparison"] is None
    C.set_done(p, "NH", oid, True)
    for who in ("CH", "SP"):                     # the lead (also the other reviewer), and anyone else
        v = C.outcome_view(p, oid, who)
        assert v["comparison"] is None and "NH: only" not in json.dumps(v)
        assert (v["mine"] or {}).get("rob", {}).get("v") == (-2 if who == "CH" else None)
        s = C.summary(p, oid, dict(C.outcomes(p))[oid])
        assert "NH: only" not in json.dumps(s)
        assert s["certainty"] == "" and s["to_agree"] == 0
    assert C.evidence_profile(p, "NH")["outcomes"] == []           # not the lead: nothing until both have finished
    lead_view = C.evidence_profile(p, "CH")["outcomes"][0]
    assert lead_view["complete"] is False and all(d["rating"] == "" for d in lead_view["domains"])
    with pytest.raises(ProjectError, match="finished"):
        C.rate(p, "NH", oid, "rob", -2, "changed my mind")
    C.set_done(p, "NH", oid, False)              # one has finished: they may still reopen
    C.set_done(p, "NH", oid, True)
    C.set_done(p, "CH", oid, True)
    v = C.outcome_view(p, oid, "NH")
    c = v["comparison"]
    rob = next(it for it in c["items"] if it["f"] == "rob")
    assert rob["a"]["v"] == -1 and rob["b"]["v"] == -2 and rob["differs"] and c["to_agree"] == 1 and c["level"] is None
    with pytest.raises(ProjectError, match="cannot be\\s+reopened"):
        C.set_done(p, "NH", oid, False)
    assert C.evidence_profile(p, "NH")["outcomes"][0]["name"] == "Time to triage"


def test_finishing_needs_every_rating_and_every_explanation(proj):
    p = proj
    oid = outcome(p)
    C.rate(p, "NH", oid, "start", "rct")
    C.rate(p, "NH", oid, "rob", -1)
    with pytest.raises(ProjectError) as e:
        C.set_done(p, "NH", oid, True)
    msg = str(e.value)
    assert "inconsistency" in msg and "Explain the rating for risk of bias" in msg and "effect in words" in msg
    assert "different starting certainty" not in msg  # nothing is known about Okafor 2024 yet, so nothing was suggested
    nothing = outcome(p, "Over-triage", studies=())
    with pytest.raises(ProjectError, match="no studies"):
        C.set_done(p, "NH", nothing, True)


def test_the_start_is_suggested_from_risk_of_bias_tools_and_extracted_design(proj):
    p = proj
    k = keys(p)
    oid = outcome(p, studies=("Okafor 2024",))
    v = C.outcome_view(p, oid, "NH")
    assert v["suggest"]["start"] == ""                 # nothing known about the study yet
    review.define_assessment(p, "NH", k["Okafor 2024"], "before_after", "Time to triage")
    assert C.outcome_view(p, oid, "NH")["suggest"]["start"] == "nrs_robins"
    rct = outcome(p, "Under-triage", studies=("Lee 2025",))
    review.define_assessment(p, "NH", k["Lee 2025"], "rct", "Under-triage")
    assert C.outcome_view(p, rct, "NH")["suggest"]["start"] == "rct"
    both = outcome(p, "Length of stay", studies=("Lee 2025", "Okafor 2024"))
    assert C.outcome_view(p, both, "NH")["suggest"]["start"] == ""     # randomised and not: GRADE rates them apart
    acc = outcome(p, "Accuracy", kind="accuracy", studies=("Haddad 2026",))
    assert C.outcome_view(p, acc, "NH")["suggest"]["start"] == "dta"
    # the other tools' starts are suggestions, each with its GRADE source: ROBINS-E (Higgins et al. 2024), QUIPS (GRADE
    # guidelines 28), and QUADAS-2 on an outcome that is not set as accuracy
    for design, label, start, words in (("nrse", "Exposure", "nrs_robins_e", "Higgins et al. 2024"),
                                        ("prognostic_factor", "Prognosis", "prog", "GRADE guidelines 28"),
                                        ("dta", "Sensitivity", "", "QUADAS-2")):
        p2 = outcome(p, label, studies=("Haddad 2026",))
        aid = review.define_assessment(p, "NH", k["Haddad 2026"], design, label)["a"]
        sug = C.outcome_view(p, p2, "NH")["suggest"]
        assert sug["start"] == start and words in sug["why"], (design, sug)
        review.define_assessment(p, "CH", k["Haddad 2026"], "", label, aid, deleted=True)
    assert C._design_word("Before-after study") == "nonrandomised" and C._design_word("Cluster randomised trial") == "randomised"
    assert C._design_word("Non-randomised study with a comparison group") == "nonrandomised"
    # a start other than the suggestion needs a reason
    C.rate(p, "NH", oid, "start", "nrs")
    assert any("different starting certainty" in x for x in C.finish_problems(p, "NH", oid))
    C.rate(p, "NH", oid, "start", "nrs", "We start non-randomised studies at Low, as the protocol says.")
    assert not any("different starting certainty" in x for x in C.finish_problems(p, "NH", oid))


def test_the_extraction_gives_the_design_and_the_number_of_participants(proj):
    p = proj
    k = keys(p)
    f = review.form(p)
    ppl = review.people(p, f)
    for label, design, n in (("Lee 2025", "Randomised trial", "120"), ("Okafor 2024", "Before-after study", "300")):
        review.save_value(p, ppl["first"], k[label], "design", design)
        review.save_value(p, ppl["first"], k[label], "n", n)
        review.set_done(p, ppl["first"], k[label], True)
        seen = review.checked_sig(f, review.answers(p)["values"][ppl["first"]][k[label]])
        review.set_done(p, ppl["second"], k[label], True, seen)
    rct = outcome(p, "Under-triage", studies=("Lee 2025",))
    v = C.outcome_view(p, rct, "NH")
    assert v["suggest"]["start"] == "rct" and v["studies"][0]["design"] == "Randomised trial"
    assert C.outcome_view(p, outcome(p, studies=("Okafor 2024",)), "NH")["suggest"]["start"] == "nrs"
    both = outcome(p, "Length of stay", studies=("Lee 2025", "Okafor 2024", "Haddad 2026"))
    d = dict(C.outcomes(p))[both]
    assert C.participants(p, d) == "420 (in 2 of 3 studies)"
    C.define(p, "CH", {**{x: d[x] for x in ("name", "importance", "kind")}, "studies": d["studies"],
                       "participants": "2,410 patients (from the meta-analysis)"}, both)
    assert C.participants(p, dict(C.outcomes(p))[both]) == "2,410 patients (from the meta-analysis)"


# ------------------------------------------------------------------------------------------------ the arithmetic
def test_the_certainty_arithmetic():
    z = {d: 0 for d in C.DOMAIN_IDS}
    assert C.compute("rct", z, {})["text"] == "High"
    assert C.compute("rct", {**z, "rob": -1, "imprecision": -1}, {})["text"] == "Low"
    assert C.compute("rct", {**z, "rob": -2, "imprecision": -2, "publication": -1}, {})["level"] == 1     # never below Very low
    assert C.compute("nrs", z, {})["text"] == "Low"
    assert C.compute("nrs", z, {"large_effect": 2})["text"] == "High"
    assert C.compute("nrs", {**z, "imprecision": -1}, {"large_effect": 1, "dose_response": 1})["text"] == "Moderate"
    assert C.compute("nrs", z, {"large_effect": 2, "dose_response": 1})["level"] == 4                  # never above High
    assert C.compute("nrs_robins", {**z, "rob": -3}, {})["text"] == "Very low"
    assert C.compute("nrs_robins", {**z, "rob": -3}, {})["problems"] == []
    assert C.compute("rct", {**z, "rob": -3}, {})["problems"]                                         # −3: not for trials
    assert C.compute("nrs", {**z, "rob": -3}, {})["problems"]                                         # nor a Low start
    assert C.compute("dta", z, {"dose_response": 1})["problems"]                                      # accuracy: not rated up
    assert C.compute("rct", {**z, "rob": None}, {})["level"] is None
    assert C.compute("", z, {})["level"] is None


def test_imprecision_can_be_rated_down_three_levels():
    """GRADE guidance 34 (Zeng et al. 2022): consider rating down two or three levels for imprecision."""
    z = {d: 0 for d in C.DOMAIN_IDS}
    for start in ("rct", "nrs_robins", "nrs", "dta"):
        assert C.allowed_down("imprecision", start) == [0, -1, -2, -3]
        assert C.allowed_down("publication", start) == [0, -1, -2]
        assert C.allowed_down("inconsistency", start) == [0, -1, -2]
    assert C.allowed_down("rob", "nrs_robins") == [0, -1, -2, -3] and C.allowed_down("rob", "rct") == [0, -1, -2]
    r = C.compute("rct", {**z, "imprecision": -3}, {})
    assert r["problems"] == [] and r["level"] == 1 and r["text"] == "Very low"                        # High − 3
    assert C.compute("dta", {**z, "imprecision": -3}, {})["problems"] == []
    assert C.compute("nrs_robins", {**z, "rob": -2, "imprecision": -3}, {})["level"] == 1           # never below Very low
    assert C.compute("nrs", {**z, "imprecision": -3}, {"large_effect": 2})["text"] == "Very low"     # 2 − 3 + 2 = 1
    for f in ("inconsistency", "indirectness", "publication"):
        probs = C.compute("rct", {**z, f: -3}, {})["problems"]
        assert probs and "only imprecision" in probs[0]
    # the help, the page and the methods paragraph say so
    assert "three levels" in C.HELP["imprecision"] and "Zeng et al. 2022" in C.HELP["imprecision"]
    assert C.grade_terms()["extreme_always"] == ["imprecision"]


def test_rating_up_is_allowed_with_cautions_where_grade_calls_it_unusual():
    """GRADE guidance 18: rating up is usually only for non-randomised studies with no reason to rate down; GRADE
    guidelines 9: randomised trials are rarely rated up. Both are allowed, with a caution and a reason."""
    z = {d: 0 for d in C.DOMAIN_IDS}
    r = C.compute("rct", {**z, "imprecision": -1}, {"large_effect": 1})
    assert r["problems"] == [] and r["level"] == 4                                                   # counted now
    assert {c["id"] for c in r["cautions"]} == {"up_after_down", "up_rct"}
    assert "imprecision" in r["cautions"][0]["text"] and "GRADE guidance 18" in r["cautions"][0]["text"]
    assert C.needs_why("rct", {**z, "imprecision": -1}, {"large_effect": 1})
    assert C.needs_why("rct", z, {"dose_response": 1})                                               # trials, nothing down
    r = C.compute("nrs", {**z, "rob": -1, "imprecision": -1}, {"large_effect": 2})
    assert [c["id"] for c in r["cautions"]] == ["up_after_down"] and "risk of bias and imprecision" in r["cautions"][0]["text"]
    assert not C.needs_why("nrs", z, {"large_effect": 2})                                           # GRADE's usual case
    assert C.compute("nrs", z, {"large_effect": 2})["cautions"] == []
    assert not C.needs_why("dta", {**z, "rob": -1}, {"large_effect": 1})                            # not counted at all
    # a ROBINS-I start not rated down for risk of bias: a caution, never a block
    r = C.compute("nrs_robins", z, {})
    assert r["level"] == 4 and r["problems"] == [] and [c["id"] for c in r["cautions"]] == ["robins_not_down"]
    assert "two for serious and three for critical" in r["cautions"][0]["text"]
    assert C.compute("nrs_robins", {**z, "rob": -1}, {})["cautions"] == []
    assert "moderate risk of bias, rate down one level; serious, two levels; critical, three levels" in C.HELP["rob"]
    assert "at least two levels" in C.HELP["rob"]


def test_extremely_serious_and_rating_up_are_refused_where_grade_does_not_allow_them(proj):
    p = proj
    oid = outcome(p)
    with pytest.raises(ProjectError, match="starting certainty first"):
        C.rate(p, "NH", oid, "rob", -3, "Critical confounding")
    C.rate(p, "NH", oid, "start", "rct")
    with pytest.raises(ProjectError, match="Extremely serious"):
        C.rate(p, "NH", oid, "rob", -3, "Critical confounding")
    C.rate(p, "NH", oid, "large_effect", 1, "Relative risk 0.4")     # randomised trials: unusual, but allowed
    assert any("randomised trials" in x and "Say why rating up is justified" in x for x in C.finish_problems(p, "NH", oid))
    C.rate(p, "NH", oid, "large_effect", 0)                 # None is always fine
    assert not any("rating up" in x for x in C.finish_problems(p, "NH", oid))
    for bad in ([1], True, "serious", 1.5):
        with pytest.raises(ProjectError, match="Choose one of"):
            C.rate(p, "NH", oid, "rob" if bad != [1] else "start", bad, "x")
    with pytest.raises(ProjectError, match="no longer exists"):
        C.define(p, "CH", {}, "", deleted=True)
    C.rate(p, "NH", oid, "imprecision", -3, "Interval from a large benefit to a large harm")
    for f in ("inconsistency", "indirectness", "publication"):
        with pytest.raises(ProjectError, match="only imprecision"):
            C.rate(p, "NH", oid, f, -3, "x")
    C.rate(p, "NH", oid, "start", "nrs")
    with pytest.raises(ProjectError, match="Extremely serious"):
        C.rate(p, "NH", oid, "rob", -3, "Critical confounding")
    with pytest.raises(ProjectError, match="one or two levels"):
        C.rate(p, "NH", oid, "dose_response", 2, "x")
    C.rate(p, "NH", oid, "large_effect", 2, "Relative risk 0.15")
    C.rate(p, "NH", oid, "start", "nrs_robins")
    C.rate(p, "NH", oid, "rob", -3, "Critical risk of confounding in the only study")
    C.rate(p, "NH", oid, "start", "dta")                    # changing the start afterwards: finishing is refused
    probs = C.finish_problems(p, "NH", oid)
    assert any("Extremely serious" in x for x in probs) and any("test accuracy" in x for x in probs)
    acc = outcome(p, "Accuracy", kind="accuracy", studies=("Haddad 2026",))
    C.rate(p, "NH", acc, "start", "dta")
    with pytest.raises(ProjectError, match="test accuracy"):
        C.rate(p, "NH", acc, "confounding", 1, "x")


# ------------------------------------------------------------------------------------------------ agreement
def test_the_lead_agrees_differences_and_the_profile_and_progress_follow(proj):
    p = proj
    oid = outcome(p, synthesis="narrative")
    outcome(p, "Under-triage", studies=("Lee 2025",))
    assert C.progress(p) == {"outcomes": 2, "finished": 0}
    rate_all(p, "NH", oid, rob=-1, upgrades={"large_effect": 1}, up_why="Relative risk 0.3 in both studies")
    rate_all(p, "CH", oid, rob=-2)
    with pytest.raises(ProjectError, match="Both reviewers must finish"):
        C.agree(p, "CH", oid, "rob", -2, "Serious confounding")
    C.set_done(p, "NH", oid, True)
    C.set_done(p, "CH", oid, True)
    with pytest.raises(PermissionDenied):
        C.agree(p, "NH", oid, "rob", -1, "Mine")
    with pytest.raises(ProjectError, match="explanation"):
        C.agree(p, "CH", oid, "rob", -2, "")
    c = C.comparison(p, oid)
    assert c["to_agree"] == 3 and not c["complete"]                  # risk of bias, large effect, why rated up
    assert C.readiness(p, "CH")[0]["fix"] == f"c/{oid}/compare"
    C.agree(p, "CH", oid, "rob", -2, "Serious confounding in both studies")
    C.agree(p, "CH", oid, "large_effect", 0)
    c = C.comparison(p, oid)
    assert c["to_agree"] == 0 and c["complete"] and c["text"] == "Very low"   # Low − 2 − 1, kept at Very low
    assert next(it for it in c["items"] if it["f"] == "up_why")["na"]       # nothing is rated up: no reason needed
    assert p.audit_entries()[-1]["action"] == "agree a certainty rating"
    assert C.progress(p) == {"outcomes": 2, "finished": 1}
    prof = C.evidence_profile(p, "CH")
    o = next(x for x in prof["outcomes"] if x["name"] == "Time to triage")
    assert set(o) >= {"name", "importance", "kind", "studies", "participants", "design", "start", "domains", "upgrades",
                      "certainty", "effect", "statement", "complete", "comparison", "measure", "statement_check",
                      "statement_mismatch", "cautions", "upgrade_justification", "footnotes", "problems"}
    assert o["complete"] and o["certainty"] == "Very low" and o["start"] == "Low" and o["studies"] == 1
    assert o["importance"] == "Critical" and o["importance_id"] == "critical" and o["design"] == "Non-randomised studies"
    assert o["comparison"] == "AI-supported triage compared with usual triage" and o["measure"] == "minutes"
    assert o["participants"] == ""                       # nothing extracted and nothing recorded by the lead
    rob = next(d for d in o["domains"] if d["id"] == "rob")
    assert rob == {"id": "rob", "name": "Risk of bias", "rating": "Very serious", "change": -2,
                   "explanation": "Serious confounding in both studies", "override": ""}
    imp = next(d for d in o["domains"] if d["id"] == "imprecision")
    assert imp["rating"] == "Serious" and imp["explanation"] == "Few participants"
    pub = next(d for d in o["domains"] if d["id"] == "publication")
    assert pub["rating"] == "Undetected" and pub["change"] == 0               # GRADE's words, not "No serious concerns"
    assert [u["rating"] for u in o["upgrades"]] == ["None", "None", "None"]
    assert set(o["domains"][0]) >= {"id", "name", "rating", "explanation"}
    assert o["effect"] and o["statement"] == VERY_UNCERTAIN
    assert o["statement_check"]["ok"] and not o["statement_mismatch"] and o["problems"] == [] and o["cautions"] == []
    assert [(f["item"], f["text"]) for f in o["footnotes"]] == [("Risk of bias", "Serious confounding in both studies"),
                                                                ("Imprecision", "Few participants")]
    assert "found in the studies" in o["meaning"] and "estimate" not in o["meaning"]     # narrative: no single estimate
    assert next(x for x in prof["outcomes"] if x["name"] == "Under-triage")["complete"] is False
    assert [x["name"] for x in C.evidence_profile(p, "NH")["outcomes"]] == ["Time to triage"]
    m = prof["method_text"]
    assert "Two reviewers (Nadine Hayes and Colin Hawthorne)" in m and "independently" in m and "low certainty" in m
    assert "Murad" in m and "Santesso" in m and "project lead (Colin Hawthorne)" in m
    assert "NH" not in m and "(CH)" not in m                                   # full names, as the rest of the methods
    assert "three levels for extremely serious imprecision (Zeng et al. 2022)" in m
    assert "\n" not in m


def test_a_randomised_body_needs_no_agreement_on_rating_up(proj):
    p = proj
    oid = outcome(p, "Under-triage", studies=("Lee 2025",))
    for who in ("NH", "CH"):
        rate_all(p, who, oid, start="rct", rob=0, imprecision=-1,
                 statement="AI-supported triage probably reduces under-triage compared with usual triage.")
        C.set_done(p, who, oid, True)
    c = C.comparison(p, oid)
    assert c["complete"] and c["text"] == "Moderate" and c["to_agree"] == 0
    ups = [it for it in c["items"] if it["f"] in C.UPGRADE_IDS]                # rated (at None by both), not skipped
    assert all(not it["differs"] and it["final"] == 0 and not it["na"] for it in ups)
    assert C.statements(dict(C.outcomes(p))[oid], 3)[0]["text"] == ("AI-supported triage probably reduces under-triage "
                                                                     "compared with usual triage.")


def test_suggested_statements_follow_the_certainty_level():
    d = {"name": "Time to triage", "kind": "effect", "comparison": "AI-supported triage versus usual triage"}
    assert C.statements(d, 4)[0]["text"] == "AI-supported triage reduces time to triage compared with usual triage."
    assert "probably" in C.statements(d, 3)[1]["text"] and "may increase" in C.statements(d, 2)[1]["text"]
    assert C.statements(d, 1)[0]["text"].startswith("The evidence is very uncertain about the effect of AI-supported triage")
    # Santesso et al. 2020: "little to no difference", and the sizes of effect
    texts = {lvl: {s["label"]: s["text"] for s in C.statements(d, lvl)} for lvl in (4, 3, 2)}
    assert texts[4]["Little to no difference"] == ("AI-supported triage results in little to no difference in time to "
                                                   "triage compared with usual triage.")
    assert texts[3]["Little to no difference"].startswith("AI-supported triage probably results in little to no difference")
    assert texts[2]["Little to no difference"].startswith("AI-supported triage may result in little to no difference")
    assert texts[4]["Slight reduction"] == "AI-supported triage reduces time to triage slightly compared with usual triage."
    assert texts[3]["Large reduction"].startswith("AI-supported triage probably results in a large reduction in time")
    assert texts[2]["Slight increase"].startswith("AI-supported triage may increase time to triage slightly")
    assert not any("little or no" in s["text"] for lvl in LEVEL_IDS for s in C.statements(d, lvl))
    assert C.statements(d, 1)[1]["text"] == ("AI-supported triage may reduce time to triage compared with usual triage, "
                                             "but the evidence is very uncertain.")
    for lvl in LEVEL_IDS:                        # every suggestion passes ReviewDesk's own check
        assert all(C.check_statement(s["text"], lvl, d)["ok"] for s in C.statements(d, lvl)), lvl
    a = {"name": "Accuracy for urgent patients", "kind": "accuracy", "comparison": "The AI tool against nurse triage"}
    assert C.statements(a, 3)[0]["text"] == "The AI tool probably has the accuracy shown against nurse triage."
    # the re-review's "probably has the accuracy shown for specificity for high acuity (true negatives and false
    # positives)": the outcome's name is no longer put into the sentence
    s = {"name": "Specificity for high acuity (true negatives and false positives)", "kind": "accuracy",
         "comparison": "The AI triage tool against expert triage"}
    assert C.statements(s, 3)[0]["text"] == "The AI triage tool probably has the specificity shown against expert triage."
    assert C.statements(a, 1)[0]["text"] == "The evidence is very uncertain about the accuracy of the AI tool against nurse triage."
    assert "very little confidence" in C.meaning(1) and "accuracy" in C.meaning(2, "accuracy")


def test_suggested_statements_agree_with_the_intervention():
    """The novice beta test printed "Exercise programmes probably reduces pain": the verbs follow the intervention."""
    def say(who, lvl=3, label="Reduces", kind="effect"):
        d = {"name": "Pain", "kind": kind, "comparison": f"{who} compared with usual care" if kind == "effect"
             else f"{who} against the reference standard"}
        return next(s["text"] for s in C.statements(d, lvl) if s["label"] == label)
    assert say("Exercise programmes") == "Exercise programmes probably reduce pain compared with usual care."
    assert say("Physiotherapy") == "Physiotherapy probably reduces pain compared with usual care."
    assert say("AI triage tools") == "AI triage tools probably reduce pain compared with usual care."
    assert say("NSAIDs") == "NSAIDs probably reduce pain compared with usual care."
    assert say("Exercise programmes", 4, "Increases") == "Exercise programmes increase pain compared with usual care."
    assert say("Exercise programmes", 4, "Little to no difference").startswith("Exercise programmes result in little to no")
    assert say("Exercise programmes", 3, "Large reduction").startswith("Exercise programmes probably result in a large")
    assert say("Physiotherapy", 4, "Slight increase") == "Physiotherapy increases pain slightly compared with usual care."
    assert say("Exercise programmes", 2) == "Exercise programmes may reduce pain compared with usual care."
    assert say("AI triage tools", 3, "Accuracy as shown", "accuracy") == (
        "AI triage tools probably have the accuracy shown against the reference standard.")
    assert say("The AI tool", 4, "Accuracy as shown", "accuracy").startswith("The AI tool has the accuracy shown")
    for who in ("Exercise programmes", "AI triage tools", "NSAIDs", "SSRIs", "Antibiotics", "ACE inhibitors",
                "Exercise programmes for older adults", "Group sessions (weekly)", "Tele-rehabilitation programmes"):
        assert C.plural(who), who
    for who in ("Physiotherapy", "AI-supported triage", "The AI tool", "CBT", "Mindfulness", "Dialysis", "Aerobics",
                "Diabetes education", "Physics teaching", "A stimulus", "Combination of drugs", "Self-management",
                "Triage with AI tools", "", "the intervention"):
        assert not C.plural(who), who


LEVEL_IDS = (4, 3, 2, 1)


def test_certainty_definitions_without_a_single_estimate_for_narrative_ratings():
    """Murad et al. 2017: where results are not pooled there is no single estimate to be confident in."""
    for lvl in LEVEL_IDS:
        assert "estimate" in C.meaning(lvl)                                       # pooled: Balshem et al. 2011
        for kind in ("effect", "accuracy"):
            m = C.meaning(lvl, kind, "narrative")
            assert "estimate" not in m and "found in the studies" in m, m
    assert C.meaning(3, "effect", "narrative") == ("We are moderately confident: the true effect is probably close to the "
                                                   "effects found in the studies, but it may be substantially different.")
    assert "true accuracy" in C.meaning(1, "accuracy", "narrative")
    T = C.grade_terms()
    assert T["meaning_narrative"]["2"] == C.meaning(2, "effect", "narrative")
    assert T["meaning_narrative_accuracy"]["4"] == C.meaning(4, "accuracy", "narrative")


def test_the_statement_wording_is_checked_against_the_certainty():
    """Santesso et al. 2020: high plain; moderate "probably" or "likely"; low "may"; very low "very uncertain"."""
    d = {"name": "Time to triage", "kind": "effect", "comparison": "AI-supported triage compared with usual triage"}
    ok = {1: ["The evidence is very uncertain about the effect of AI triage on time to triage.",
              "AI triage may reduce time to triage, but the evidence is very uncertain.",
              "We are very uncertain whether AI triage reduces time to triage.", "Very low-certainty evidence: no conclusion."],
          2: ["AI triage may reduce time to triage.", "AI triage might reduce time to triage.",
              "The evidence suggests AI triage reduces time to triage.", "AI triage may result in little to no difference."],
          3: ["AI triage probably reduces time to triage.", "AI triage likely results in a large reduction in time to triage.",
              "AI triage is likely to reduce time to triage, though it may not help children."],
          4: ["AI triage reduces time to triage.", "Patients are less likely to be under-triaged with AI triage.",
              "AI triage results in little to no difference in time to triage."]}
    for lvl, texts in ok.items():
        for t in texts:
            assert C.check_statement(t, lvl, d)["ok"], (lvl, t)
    bad = [(1, "AI triage may reduce time to triage.", "may", "low"),
           (1, "AI triage reduces time to triage.", "", ""),
           (2, "AI triage probably reduces time to triage.", "probably", "moderate"),
           (2, "The evidence is very uncertain about the effect of AI triage.", "very uncertain", "very low"),
           (3, "AI triage may reduce time to triage.", "may", "low"),
           (3, "AI triage reduces time to triage.", "", ""),
           (4, "AI triage probably reduces time to triage.", "probably", "moderate"),
           (4, "AI triage may increase time to triage.", "may", "low")]
    for lvl, t, word, said in bad:
        r = C.check_statement(t, lvl, d)
        assert not r["ok"] and r["word"] == word, (lvl, t)
        assert r["why"].startswith("The plain-language statement does not match the certainty of the evidence. "
                                   f"For {C.LEVELS[lvl].lower()} certainty, GRADE's wording {C.WORDING_RULE[lvl]}")
        assert (f"says “{word}”, which is the wording for {said} certainty" if word else "readers will take it as high") in r["why"]
        assert C.check_statement(r["suggestion"], lvl, d)["ok"]                  # the suggestion itself matches
    # very low needs "very uncertain" (Santesso et al. 2020): a bare "uncertain" is GRADE's wording for no level
    for t in ("AI-supported triage may reduce time to triage, but the evidence is uncertain.",
              "We are uncertain whether AI triage reduces time to triage.",
              "The evidence is uncertain about the effect of AI triage on time to triage."):
        r = C.check_statement(t, 1, d)
        assert not r["ok"] and r["word"] == "uncertain" and r["said"] is None, t
        assert "“very uncertain”, not just “uncertain”" in r["why"] and "says “uncertain” without “very”" in r["why"]
        assert r["suggestion"] == VERY_UNCERTAIN
    assert C.check_statement("AI triage may reduce time to triage, but the evidence is uncertain.", 2, d)["ok"]
    assert not C.check_statement("The evidence is uncertain about AI triage.", 2, d)["ok"]
    assert C.check_statement("The evidence is uncertain about AI triage.", 2, d)["word"] == "uncertain"
    item = C._mismatch_item({"name": "Time to triage"}, "o1", {"level": 1, "statement_check": C.check_statement(
        "AI triage may reduce time to triage, but the evidence is uncertain.", 1, d)}, "me")
    assert "but the statement says “uncertain” without “very”, which is not GRADE's wording for any level" in item["text"]
    # the suggestion keeps the direction and size of what was written
    assert C.check_statement("AI triage may increase time to triage.", 3, d)["suggestion"] == (
        "AI-supported triage probably increases time to triage compared with usual triage.")
    assert C.check_statement("AI triage may make little or no difference.", 4, d)["suggestion"] == (
        "AI-supported triage results in little to no difference in time to triage compared with usual triage.")
    assert C.check_statement("AI triage may slightly reduce time to triage.", 3, d)["suggestion"] == (
        "AI-supported triage probably reduces time to triage slightly compared with usual triage.")
    assert C.check_statement("AI triage reduces time to triage.", 1, d)["suggestion"].startswith("The evidence is very uncertain")
    assert C.check_statement("", 2, d)["ok"] and C.check_statement("Anything", None, d)["ok"]   # nothing to check yet
    # the page checks with the same patterns: they must work in JavaScript too (no look-behind)
    pats = C.grade_terms()["wording"]["patterns"]
    assert set(pats) == {"ignore", "very_uncertain", "uncertain", "may", "probably"}
    assert not any("(?<" in x for x in pats.values())


def test_readiness_says_what_is_missing(proj):
    p = proj
    r = C.readiness(p, "NH")
    assert r[0]["fix"] == "certainty" and r[0]["blocks"] and r[0]["who"] == "lead"
    assert C.readiness(p, "CH")[0]["who"] == "me"
    empty = outcome(p, "Over-triage", studies=())
    oid = outcome(p)
    r = C.readiness(p, "NH")
    assert any("Over-triage" in x["text"] and x["blocks"] for x in r)
    mine = next(x for x in r if x["text"].startswith("You have not finished"))
    assert mine["who"] == "me" and mine["fix"] == f"c/{oid}" and not mine["blocks"]
    assert any(x["text"].startswith("Colin Hawthorne has not finished") and x["who"] == "lead" for x in r)
    for item in r:
        assert set(item) == {"text", "fix", "fix_label", "who", "blocks"}
    C.define(p, "CH", {}, empty, deleted=True)


# ------------------------------------------------------------------------------------------------ the data checks
def test_a_hand_edited_or_deleted_certainty_row_is_a_data_problem(home, monkeypatch):
    p = build(home)
    oid = outcome(p)
    # NH rates on her own computer: her file there is not one of this computer's own files
    monkeypatch.setenv("REVIEWDESK_HOME", str(home / "nh-laptop"))
    nh_dev = userconfig.device_id()
    rate_all(p, "NH", oid)
    monkeypatch.setenv("REVIEWDESK_HOME", str(home / "home"))
    assert nh_dev != p.dev()
    rel = f"certainty/NH@{nh_dev}.jsonl"
    assert not p.integrity_report()["problems"]          # seen intact by this computer
    f = p.path(*rel.split("/"))
    rows = util.read_jsonl(f)
    good = [dict(r) for r in rows]
    rows[1]["v"] = -2                                    # a rating changed by hand
    util.write_text_atomic(f, "".join(json.dumps(r) + "\n" for r in rows))
    probs = p.integrity_report()["problems"]
    pr = next(x for x in probs if x["file"] == rel)
    assert pr["kind"] == "chain" and "certainty (GRADE) ratings" in pr["text"] and "changed or added" in pr["text"]
    assert "Nadine Hayes" in pr["text"] and "chain" in pr["fixes"]
    p.restore_chained("CH", rel)                         # the lead puts back this computer's copy
    assert not [x for x in p.integrity_report()["problems"] if x["file"] == rel]
    util.write_text_atomic(f, "".join(json.dumps(r) + "\n" for r in good[:-2]))   # rows deleted
    pr = next(x for x in p.integrity_report()["problems"] if x["file"] == rel)
    assert "no longer there" in pr["text"]


def test_own_certainty_files_are_put_right_and_forged_agreed_rows_are_found(proj):
    p = proj
    oid = outcome(p)
    rate_all(p, "CH", oid)
    mine = p.path("certainty", f"CH@{p.dev()}.jsonl")
    rows = util.read_jsonl(mine)
    util.write_text_atomic(mine, "".join(json.dumps(r) + "\n" for r in rows[:-3]))     # an older copy put back
    p.integrity_report()
    assert len(util.read_jsonl(mine)) == len(rows)                                     # written back from this computer
    assert any("certainty/CH@" in x for x in p.repairs())
    # a row added by hand to one of this computer's own files is taken out again (and kept in state/_replaced)
    util.append_jsonl(mine, {"t": util.now(), "o": oid, "f": "rob", "v": 0, "x": "", "h": "0" * 20})
    p.integrity_report()
    assert not any(r.get("h") == "0" * 20 for r in util.read_jsonl(mine)) and os.listdir(p.path("state", "_replaced"))
    # agreed ratings written by hand, in a file no computer wrote
    forged = p.path("certainty", "agreed@ffffff.jsonl")
    util.append_jsonl(forged, {"t": util.now(), "o": oid, "f": "rob", "v": 0, "x": "", "by": "CH", "h": "0" * 20})
    pr = next(x for x in p.integrity_report()["problems"] if x["file"] == "certainty/agreed@ffffff.jsonl")
    assert pr["text"].startswith("The agreed certainty (GRADE) ratings")


def test_the_rows_are_read_again_when_a_file_changes(proj):
    p = proj
    oid = outcome(p)
    assert C.rows(p)["vals"] == {}
    C.rate(p, "NH", oid, "rob", 0)
    assert C.rows(p)["vals"]["NH"][oid]["rob"]["v"] == 0


# ------------------------------------------------------------------------------------------------ the workbook and the pages
def _sheet(data: bytes, n: int) -> list:
    z = zipfile.ZipFile(io.BytesIO(data))
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    root = ElementTree.fromstring(z.read(f"xl/worksheets/sheet{n}.xml"))
    return [["".join(t.text or "" for t in c.iter(f"{{{ns['m']}}}t")) or (c.findtext("m:v", "", ns)) for c in r.findall("m:c", ns)]
            for r in root.iter(f"{{{ns['m']}}}row")]


def test_the_evidence_profile_workbook_opens(proj):
    p = proj
    oid = outcome(p)
    assert C._why_not(p, "CH")                            # nothing finished and agreed yet
    for who in ("NH", "CH"):
        rate_all(p, who, oid)
        C.set_done(p, who, oid, True)
    assert C._why_not(p, "CH") == "" and C._why_not(p, "NH") == ""
    data = C.profile_xlsx(p, "CH")
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.testzip() is None
    wb = z.read("xl/workbook.xml").decode()
    for name in ("Evidence profile", "Footnotes", "Both reviewers", "Method"):
        assert f'name="{name}"' in wb
    prof = _sheet(data, 1)
    assert prof[0][0] == "Outcome" and "Certainty" in prof[0]
    # a GRADE evidence profile's columns, in its order, with the number of participants and the importance
    assert prof[0][:13] == ["Outcome", "Number of studies", "Study design", "Starting certainty", "Risk of bias",
                            "Inconsistency", "Indirectness", "Imprecision", "Publication bias", "Large effect",
                            "Dose-response gradient", "Plausible residual confounding", "Number of participants"]
    assert prof[0][13:17] == ["Effect", "Certainty", "Importance", "Plain-language statement"]
    row = dict(zip(prof[0], prof[1]))
    assert row["Outcome"] == "Time to triage" and row["Certainty"].startswith("Very low")
    assert row["Importance"] == "Critical" and row["Publication bias"] == "Undetected"
    assert row["Risk of bias"] == "Serious (−1) [a]" and "[a] Risk of bias: Confounding by indication" in row["Footnotes"]
    assert row["Imprecision"] == "Serious (−1) [b]" and row["Finished and agreed"] == "yes"
    foot = _sheet(data, 2)
    assert foot[0] == ["Outcome", "Mark", "Item", "Rating", "Explanation"]
    assert ["Time to triage", "b", "Imprecision", "Serious", "Few participants"] in foot
    both = _sheet(data, 3)
    assert both[0][2] == "Nadine Hayes rating" and both[0][4] == "Colin Hawthorne rating"
    method = _sheet(data, 4)
    assert ["Serious (−1)", "Strongly suspected"] in method and any("found in the studies" in " ".join(r) for r in method)
    # no word is broken in the headers: every column is wider than its longest header word (bold, so with room)
    sheet = zipfile.ZipFile(io.BytesIO(data)).read("xl/worksheets/sheet1.xml").decode()
    widths = [float(w) for w in re.findall(r'<col min="\d+" max="\d+" width="([\d.]+)"', sheet)]
    assert len(widths) == len(prof[0])
    for head, w in zip(prof[0], widths):
        assert w >= max(len(x) for x in head.split()) * 1.15 + 3, head
    for r in prof[1:]:                                                              # nor in the cells
        for cell, w in zip(r, widths):
            assert all(len(x) + 2 <= w for x in str(cell).split()), cell
    for n in (1, 2, 3, 4):           # every cell names its (wrapping) style, so LibreOffice wraps it too, not only Excel
        xml = zipfile.ZipFile(io.BytesIO(data)).read(f"xl/worksheets/sheet{n}.xml").decode()
        cells = re.findall(r"<c [^>]*>", xml)
        assert cells and all(' s="' in c for c in cells) and not re.search(r'<c [^>]*\bs="[^"]*"[^>]*\bs="', xml)
    assert C.DOWNLOADS["certainty"][0].endswith(".xlsx")


def _finish_both(p, oid, **kw):
    for who in ("NH", "CH"):
        rate_all(p, who, oid, **kw)
        C.set_done(p, who, oid, True)


def test_a_statement_that_does_not_match_the_certainty_keeps_the_outcome_unfinished(proj):
    """The beta test printed "Very low … may reduce": now the outcome is not finished until the wording matches or the
    lead records why it differs, and the profile flags it until then."""
    p = proj
    oid = outcome(p)
    may = "AI-supported triage may reduce time to triage compared with usual triage."
    _finish_both(p, oid, statement=may)                       # Low − 1 − 1: very low, worded as low
    c = C.comparison(p, oid)
    assert c["level"] == 1 and c["to_agree"] == 0 and not c["complete"]
    chk = c["statement_check"]
    assert not chk["ok"] and not chk["kept"] and chk["word"] == "may"
    assert chk["suggestion"] == VERY_UNCERTAIN
    assert any(x.startswith(chk["why"]) and VERY_UNCERTAIN in x for x in c["problems"])
    assert C.progress(p) == {"outcomes": 1, "finished": 0}
    # a Needed item, naming the outcome, wherever readiness is shown (Rate certainty, the Overview)
    need = [x for x in C.readiness(p, "CH") if x.get("key") == f"certainty:statement:{oid}"]
    assert len(need) == 1 and need[0]["blocks"] and need[0]["who"] == "me" and need[0]["fix"] == f"c/{oid}/compare"
    assert need[0]["text"].startswith("“Time to triage”: the plain-language statement does not match the certainty. The "
                                      "agreed certainty is very low, but the statement says “may”, the wording for low")
    assert VERY_UNCERTAIN in need[0]["text"] and need[0]["text"].count("does not match") == 1
    assert not any("does not match" in x["text"] for x in C.readiness(p, "CH") if not x["blocks"])   # not twice
    assert next(x for x in C.readiness(p, "NH") if x.get("key") == need[0]["key"])["who"] == "lead"
    step = next(s for s in readiness.check(p, "CH")["steps"] if s["id"] == "certainty")
    assert [x["key"] for x in step["missing"]] == [need[0]["key"]] and step["status"] == "now"
    rep = next(s for s in readiness.check(p, "CH")["steps"] if s["id"] == "report")             # and before reporting
    assert need[0]["key"] in [x["key"] for x in rep["missing"]] and rep["status"] != "done"
    step = next(s for s in readiness.check(p, "NH")["steps"] if s["id"] == "certainty")      # the reviewer waits
    assert any("Waiting for Colin Hawthorne (the project lead) to put right the statement for “Time to triage”."
               == x["text"] and x["waiting"] for x in step["warnings"])
    o = C.evidence_profile(p, "CH")["outcomes"][0]
    assert o["statement_mismatch"] and not o["complete"] and o["statement_check"]["why"] == chk["why"]
    assert "The wording for time to triage does not yet match its certainty and is still to be agreed." in C.method_text(p)
    two = outcome(p, "Over-triage", studies=("Okafor 2024",))
    _finish_both(p, two, statement="AI-supported triage may reduce over-triage.")
    assert "The wording for time to triage and over-triage does not yet match their certainty" in C.method_text(p)
    assert len([x for x in C.readiness(p, "CH") if x["blocks"]]) == 2                  # one Needed item each, named
    C.define(p, "CH", {}, two, deleted=True)
    row = dict(zip(*_sheet(C.profile_xlsx(p, "CH"), 1)[:2]))
    assert "[Does not match very low certainty: not yet resolved" in row["Plain-language statement"]
    assert "does not match the certainty" in row["Still to resolve"] and row["Finished and agreed"] == "no"
    # keeping the wording needs the lead and a reason
    with pytest.raises(PermissionDenied):
        C.keep_statement(p, "NH", oid, "The journal's style")
    with pytest.raises(ProjectError, match="Say why"):
        C.keep_statement(p, "CH", oid, "  ")
    C.keep_statement(p, "CH", oid, "The protocol words very low certainty this way")
    assert p.audit_entries()[-1]["action"] == "keep a plain-language statement worded differently from its certainty"
    c = C.comparison(p, oid)
    assert c["complete"] and c["statement_check"]["kept"] and c["statement_check"]["reason"].startswith("The protocol")
    assert C.progress(p) == {"outcomes": 1, "finished": 1}
    assert not [x for x in C.readiness(p, "CH") if x["blocks"]]
    o = C.evidence_profile(p, "CH")["outcomes"][0]
    assert o["complete"] and not o["statement_mismatch"]
    assert o["footnotes"][-1] == {"item": "Plain-language statement", "rating": "",
                                  "text": "Worded more strongly than GRADE's standard wording for very low certainty, "
                                          "which is “the evidence is very uncertain”; the review team kept this wording "
                                          "because: The protocol words very low certainty this way."}
    assert ("The plain-language statement for time to triage (very low certainty, whose standard wording is “the evidence "
            "is very uncertain”) was worded more strongly than the standard wording for that certainty; the review "
            "team's reason is given in a footnote.") in C.method_text(p)
    row = dict(zip(*_sheet(C.profile_xlsx(p, "CH"), 1)[:2]))
    assert row["Plain-language statement"] == may + " [c]" and "[c] Plain-language statement: Worded more strongly" in row["Footnotes"]
    # the reason holds only for that wording: a new agreed statement is checked again
    C.agree(p, "CH", oid, "statement", "AI-supported triage probably reduces time to triage.")
    c = C.comparison(p, oid)
    assert not c["complete"] and not c["statement_check"]["kept"] and c["statement_check"]["word"] == "probably"
    C.agree(p, "CH", oid, "statement", VERY_UNCERTAIN)
    assert C.comparison(p, oid)["complete"]
    with pytest.raises(ProjectError, match="already matches"):
        C.keep_statement(p, "CH", oid, "No need")


def test_rating_up_after_rating_down_needs_a_reason_that_becomes_a_footnote(proj):
    """GRADE guidance 18: rating up is usually only for evidence with no reason to rate down. Not blocked: the
    reviewers say why, the lead agrees the reason, and it is reported."""
    p = proj
    oid = outcome(p)
    rate_all(p, "NH", oid, start="nrs", rob=-1, imprecision=0, upgrades={"large_effect": 2},
             statement="AI-supported triage probably reduces time to triage compared with usual triage.")
    with pytest.raises(ProjectError, match="Say why rating up is justified"):
        C.set_done(p, "NH", oid, True)
    C.rate(p, "NH", oid, "up_why", "The effect is consistent and far larger than confounding could explain")
    C.set_done(p, "NH", oid, True)
    rate_all(p, "CH", oid, start="nrs", rob=-1, imprecision=0, upgrades={"large_effect": 2},
             statement="AI-supported triage probably reduces time to triage compared with usual triage.",
             up_why="Relative risk below 0.2 in every study")
    C.set_done(p, "CH", oid, True)
    c = C.comparison(p, oid)
    why = next(it for it in c["items"] if it["f"] == "up_why")
    assert why["differs"] and c["to_agree"] == 1 and not c["complete"]          # two reasons: the lead agrees one
    C.agree(p, "CH", oid, "up_why", "Relative risk below 0.2 in every study, far larger than confounding explains")
    c = C.comparison(p, oid)
    assert c["complete"] and c["text"] == "Moderate" and c["why_needed"]       # Low − 1 + 2
    assert [w["id"] for w in c["cautions"]] == ["up_after_down"]
    o = C.evidence_profile(p, "CH")["outcomes"][0]
    assert o["upgrade_justification"].startswith("Relative risk below 0.2")
    assert o["upgrades"][0]["justification"] == o["upgrade_justification"] and "justification" not in o["upgrades"][1]
    assert o["cautions"] and "GRADE guidance 18" in o["cautions"][0]
    assert o["footnotes"][-1]["item"] == "Why rating up is justified" and "Reason: Relative risk" in o["footnotes"][-1]["text"]
    m = C.method_text(p)
    assert "The evidence for time to triage was rated up although it was also rated down" in m and "footnote" in m
    row = dict(zip(*_sheet(C.profile_xlsx(p, "CH"), 1)[:2]))
    assert row["Large effect"] == "Rated up two levels (+2) [b, c]" and "[c] Why rating up is justified:" in row["Footnotes"]
    assert "GRADE guidance 18" in row["GRADE cautions"]


def test_randomised_trials_can_be_rated_up_with_a_reason(proj):
    """GRADE guidelines 9 calls rating up randomised trials unusual, not forbidden."""
    p = proj
    oid = outcome(p, "Under-triage", studies=("Lee 2025",))
    for who in ("NH", "CH"):
        rate_all(p, who, oid, start="rct", rob=0, imprecision=-1, upgrades={"large_effect": 1},
                 statement="AI-supported triage reduces under-triage compared with usual triage.",
                 up_why="Very large effect in a well-run trial" if who == "NH" else "")
    C.set_done(p, "NH", oid, True)
    with pytest.raises(ProjectError, match="randomised trials"):
        C.set_done(p, "CH", oid, True)
    C.rate(p, "CH", oid, "up_why", "Very large effect in a well-run trial")
    C.set_done(p, "CH", oid, True)
    c = C.comparison(p, oid)
    assert c["complete"] and c["text"] == "High" and {w["id"] for w in c["cautions"]} == {"up_after_down", "up_rct"}
    assert "Guyatt et al. 2011" in C.method_text(p)


def _docx_text(data: bytes) -> str:
    """A Word document's text: a line for each paragraph, line breaks kept."""
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    root = ElementTree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    return "\n".join("".join((e.text or "") if e.tag == W + "t" else "\n" if e.tag == W + "br" else ""
                              for e in para.iter()) for para in root.iter(W + "p"))


def test_robins_i_evidence_not_rated_down_for_risk_of_bias_needs_a_reason_shown_everywhere(proj):
    """GRADE guidance 18: non-randomised studies assessed with ROBINS-I start at High and are generally rated down at
    least two levels for risk of bias. The re-review finished such an outcome at High with no footnote anywhere: now
    the reviewers and the lead must explain the risk-of-bias rating, and GRADE's caution with the reason reaches the
    footnotes of the summary of findings, the evidence profile and the workbook, and the methods text."""
    p = proj
    oid = outcome(p, "Length of stay", studies=("Okafor 2024", "Haddad 2026"))
    high = "AI-supported triage results in little to no difference in length of stay compared with usual triage."
    rate_all(p, "NH", oid, start="nrs_robins", rob=0, imprecision=0, statement=high)
    with pytest.raises(ProjectError, match="GRADE guidance 18.*Say why risk of bias is not rated down"):
        C.set_done(p, "NH", oid, True)
    reason = "Every study measured and adjusted for the important confounders"
    C.rate(p, "NH", oid, "rob", 0, reason)
    C.set_done(p, "NH", oid, True)
    rate_all(p, "CH", oid, start="nrs_robins", rob=0, imprecision=0, statement=high)
    C.rate(p, "CH", oid, "rob", 0, "Confounding well controlled")
    C.set_done(p, "CH", oid, True)
    c = C.comparison(p, oid)
    assert c["complete"] and c["text"] == "High" and [w["id"] for w in c["cautions"]] == ["robins_not_down"]
    o = C.evidence_profile(p, "CH")["outcomes"][0]
    rob = next(x for x in o["domains"] if x["id"] == "rob")
    assert rob["explanation"] == ("Not rated down, although the evidence comes from non-randomised studies assessed with "
                                  "ROBINS-I, which GRADE guidance 18 generally rates down by at least two levels for risk "
                                  "of bias (Schünemann et al. 2019). Reason: " + reason)
    assert o["footnotes"][0] == {"item": "Risk of bias", "rating": "No serious concerns", "text": rob["explanation"]}
    m = C.method_text(p)
    assert ("The evidence for length of stay came from non-randomised studies assessed with ROBINS-I but was not rated "
            "down for risk of bias, although GRADE guidance 18 generally rates such evidence down by at least two levels "
            "(Schünemann et al. 2019); the reviewers' reason, also given in a footnote: “" + reason + "”.") in m
    t = _docx_text(reporting.sof_docx(p, "CH"))                        # the Word summary of findings and profile
    assert "a. Length of stay, risk of bias (no serious concerns): Not rated down, although" in t and reason in t
    assert t.count("Not rated down, although") == 2                    # the summary of findings and the evidence profile
    assert "No serious concerns (a)" in t
    row = dict(zip(*_sheet(C.profile_xlsx(p, "CH"), 1)[:2]))
    assert row["Risk of bias"] == "No serious concerns [a]" and "[a] Risk of bias: Not rated down" in row["Footnotes"]
    assert "GRADE guidance 18" in row["GRADE cautions"]
    # the lead's agreed rating needs the explanation too
    with pytest.raises(ProjectError, match="Say why risk of bias is not rated down"):
        C.agree(p, "CH", oid, "rob", 0, "")
    C.agree(p, "CH", oid, "rob", 0, "Agreed: confounding was well controlled in every study")
    assert "“Agreed: confounding was well controlled in every study”" in C.method_text(p)
    assert C.comparison(p, oid)["overrides"] == 0                      # the same rating: not a change
    # rated down for risk of bias but rated up back to High: GRADE 18's caution, with the reason why rating up is justified
    two = outcome(p, "Time to triage", studies=("Okafor 2024",))
    st = "AI-supported triage reduces time to triage compared with usual triage."
    rate_all(p, "NH", two, start="nrs_robins", rob=-1, imprecision=0, upgrades={"large_effect": 1}, statement=st)
    with pytest.raises(ProjectError, match="ends at High certainty.*Say why rating up is justified"):
        C.set_done(p, "NH", two, True)
    for who in ("NH", "CH"):
        rate_all(p, who, two, start="nrs_robins", rob=-1, imprecision=0, upgrades={"large_effect": 1}, statement=st,
                 up_why="The time halved in every study, far more than confounding could explain")
        C.set_done(p, who, two, True)
    c = C.comparison(p, two)
    assert c["complete"] and c["text"] == "High" and [w["id"] for w in c["cautions"]] == ["up_after_down", "robins_high"]
    o = next(x for x in C.evidence_profile(p, "CH")["outcomes"] if x["id"] == two)
    assert "ends at High certainty" in o["footnotes"][-1]["text"] and o["footnotes"][-1]["text"].endswith(
        "Reason: The time halved in every study, far more than confounding could explain")
    assert ("The evidence for time to triage came from non-randomised studies assessed with ROBINS-I and ended at high "
            "certainty after rating up") in C.method_text(p)
    # an older project, where both finished without the explanation: not finished until the lead records it
    three = outcome(p, "Over-triage", studies=("Okafor 2024",))
    for who in ("NH", "CH"):
        rate_all(p, who, three, start="nrs_robins", rob=0, imprecision=-1,
                 statement="AI-supported triage probably reduces over-triage compared with usual triage.")
        review._write(p, who, "certainty", {"o": three, "done": True, "brev": 0})       # as ReviewDesk 1.3.0 allowed
    c = C.comparison(p, three)
    assert not c["complete"] and any(x.startswith("Risk of bias is not rated down") and
                                     x.endswith("Record the agreed explanation for risk of bias (it becomes a footnote).")
                                     for x in c["problems"])
    assert any("“Over-triage”: Risk of bias is not rated down" in x["text"] for x in C.readiness(p, "CH"))
    C.agree(p, "CH", three, "rob", 0, "Confounders adjusted for in the only study")
    assert C.comparison(p, three)["complete"]
    assert C.robins_unexplained("nrs_robins", {f: 0 for f in C.DOMAIN_IDS}, {}, "") and not C.robins_unexplained(
        "nrs", {f: 0 for f in C.DOMAIN_IDS}, {}, "") and not C.robins_unexplained("rct", {f: 0 for f in C.DOMAIN_IDS}, {}, "")
    assert C.nrs_high("nrs_robins") and not C.nrs_high("nrs") and not C.nrs_high("rct") and not C.nrs_high("dta")


def test_a_change_to_a_rating_both_reviewers_gave_needs_a_reason_and_is_reported(proj):
    """The re-review: the lead changed a rating both reviewers gave identically, and GRADE then reported 0 differences
    and said nothing. Now such a change (or any rating neither reviewer gave) needs the lead's reason, is a footnote
    naming the domain, the reviewers' rating and the reason, and is counted in the methods text."""
    p = proj
    oid = outcome(p)
    _finish_both(p, oid)                                               # identical: Low − 1 − 1, very low
    c = C.comparison(p, oid)
    assert c["complete"] and c["differences"] == 0 and c["overrides"] == 0
    assert "They gave the same rating for all 9 ratings on the outcome both had finished." in C.method_text(p)
    with pytest.raises(ProjectError, match="Neither reviewer gave this rating: both reviewers rated it no serious "
                                           "concerns. Say why you are changing it"):
        C.agree(p, "CH", oid, "inconsistency", -1, "Directions differ")
    why = "Both reviewers missed that one study found the opposite direction"
    C.agree(p, "CH", oid, "inconsistency", -1, "Directions differ", why=why)
    assert p.audit_entries()[-1]["details"]["changed from"] == "both reviewers rated it no serious concerns"
    c = C.comparison(p, oid)
    it = next(i for i in c["items"] if i["f"] == "inconsistency")
    assert c["complete"] and c["overrides"] == 1 and c["differences"] == 0
    assert it["override"] == {"gave": "both reviewers rated it no serious concerns", "same": True, "why": why, "by": "CH"}
    o = C.evidence_profile(p, "CH")["outcomes"][0]
    note = ("Changed by the project lead (Colin Hawthorne): both reviewers rated it no serious concerns. Reason: " + why)
    assert [f for f in o["footnotes"] if f.get("override")] == [{"item": "Inconsistency", "rating": "Serious", "text": note,
                                                                 "override": True}]
    assert next(x for x in o["domains"] if x["id"] == "inconsistency")["override"] == note
    m = C.method_text(p)
    assert ("The project lead also changed 1 rating that both reviewers had given identically: inconsistency for time "
            "to triage. The reason is given in a footnote.") in m
    t = _docx_text(reporting.sof_docx(p, "CH"))
    assert f"c. Time to triage, inconsistency (serious): {note}" in t and "Serious (b, c)" in t
    data = C.profile_xlsx(p, "CH")
    row = dict(zip(*_sheet(data, 1)[:2]))
    assert row["Inconsistency"] == "Serious (−1) [b, c]" and f"[c] Inconsistency: {note}" in row["Footnotes"]
    both = _sheet(data, 3)
    assert both[0][-1] == "Why the lead changed it" and any(r[1] == "Inconsistency" and r[-1] == why for r in both)
    # the starting certainty too; and a rating neither reviewer gave where they differed
    with pytest.raises(ProjectError, match="Neither reviewer gave this rating"):
        C.agree(p, "CH", oid, "start", "rct")
    two = outcome(p, "Over-triage", studies=("Okafor 2024",))
    rate_all(p, "NH", two, imprecision=-1)
    rate_all(p, "CH", two, imprecision=-2, statement=VERY_UNCERTAIN)
    for who in ("NH", "CH"):
        C.set_done(p, who, two, True)
    assert C.comparison(p, two)["to_agree"] == 1
    with pytest.raises(ProjectError, match="the reviewers rated it serious \\(−1\\) and very serious \\(−2\\)"):
        C.agree(p, "CH", two, "imprecision", 0)
    C.agree(p, "CH", two, "imprecision", 0, why="The interval excludes any important effect")
    m = C.method_text(p)
    assert "They gave the same rating for 17 of 18 ratings (94.4%) on the 2 outcomes both had finished; the project " \
           "lead recorded the agreed rating for the difference." in m
    assert ("The project lead also changed 1 rating that both reviewers had given identically: inconsistency for time "
            "to triage. The project lead recorded 1 rating that neither reviewer had given: imprecision for over-triage. "
            "The reasons are given in footnotes.") in m
    # agreeing the reviewers' own rating again needs no reason, and the change is gone
    C.agree(p, "CH", oid, "inconsistency", 0)
    assert C.comparison(p, oid)["overrides"] == 0
    # a change recorded before reasons were asked for keeps the outcome unfinished until the lead gives one
    review._write_agreed(p, "CH", "certainty", {"o": oid, "f": "indirectness", "v": -1, "x": "Older adults only"})
    c = C.comparison(p, oid)
    assert not c["complete"] and any("neither reviewer gave" in x for x in c["problems"])


def test_names_are_in_sentence_case_and_counts_agree_in_every_grade_output(proj):
    """The re-review found "time to triage (critical)" and "Time to triage (low)" in one section, and "Some outcomes are
    not finished" for one outcome. Names: a capital at the start of a cell or sentence, a small letter inside one,
    acronyms and proper nouns kept."""
    for name, inside, start in (("Time to triage", "time to triage", "Time to triage"),
                                ("time to triage", "time to triage", "Time to triage"),
                                ("Under-triage", "under-triage", "Under-triage"),
                                ("ED length of stay", "ED length of stay", "ED length of stay"),
                                ("HbA1c at 12 weeks", "HbA1c at 12 weeks", "HbA1c at 12 weeks"),
                                ("eGFR decline", "eGFR decline", "eGFR decline"),
                                ("Glasgow Coma Scale score", "Glasgow Coma Scale score", "Glasgow Coma Scale score"),
                                ("Sensitivity for high acuity (true positives and false negatives)",
                                 "sensitivity for high acuity (true positives and false negatives)",
                                 "Sensitivity for high acuity (true positives and false negatives)")):
        assert (C.name_text(name), C.name_title(name)) == (inside, start), name
    p = proj
    oid = outcome(p, "Time to triage")
    t = _docx_text(reporting.sof_docx(p, "CH"))
    assert "Not final. The outcome time to triage is not finished on the Rate certainty (GRADE) page." in t
    two = outcome(p, "ED length of stay", importance="important")
    t = _docx_text(reporting.sof_docx(p, "CH"))
    assert "Not final. The outcomes time to triage and ED length of stay are not finished" in t
    assert "The critical outcome rated is time to triage; the important outcome is ED length of stay." in C.method_text(p)
    low = outcome(p, "under-triage", importance="important", studies=("Lee 2025",))      # typed in small letters
    _finish_both(p, oid)
    _finish_both(p, low, statement="The evidence is very uncertain about the effect of AI-supported triage on "
                                   "under-triage compared with usual triage.")
    m = C.method_text(p)
    assert "The critical outcome rated was time to triage; the important outcomes were ED length of stay and " \
           "under-triage." in m
    t = _docx_text(reporting.sof_docx(p, "CH"))
    assert "\nUnder-triage\n" in t and "a. Time to triage, risk of bias (serious)" in t
    assert "Under-triage, risk of bias (serious)" in t and "under-triage, risk" not in t
    assert "Not final. The outcome ED length of stay is not finished" in t
    sec = reporting._certainty_text(p, "CH")[-1]
    assert sec == ("The certainty of the evidence was rated for 3 outcomes (2 of them finished): very low for time to "
                   "triage and under-triage; not yet finished: ED length of stay.")
    assert [r[0] for r in _sheet(C.profile_xlsx(p, "CH"), 1)[1:]] == ["Time to triage", "ED length of stay", "Under-triage"]
    # the statement flagged for one outcome is singular, for two plural
    _finish_both(p, two, statement="AI-supported triage may reduce ED length of stay.")
    t = _docx_text(reporting.sof_docx(p, "CH"))
    assert ("Not final. The plain-language statement for ED length of stay does not match the certainty of the evidence: "
            "it is flagged in the summary of findings.") in t


def test_the_word_evidence_profile_never_breaks_a_word(proj):
    """The re-review rendered "Startin g certain ty", "Undetect ed", "Moder ate" and split GRADE symbols. Every column
    of the Word evidence profile is now at least as wide as its longest word (the effect has a row of its own), with
    long names, every rating word and every certainty level present."""
    p = proj
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    plans = [("Sensitivity for high acuity (true positives and false negatives)", "accuracy", "dta", -1, 0, 0, 0, 0),
             ("Under-triage", "effect", "rct", -1, -3, 0, -1, 0), ("Over-triage", "effect", "nrs_robins", -3, -1, 0, 0, -1),
             ("Time to triage", "effect", "nrs_robins", -2, -1, 1, 0, 0), ("Specificity", "accuracy", "dta", 0, -1, 0, -1, 0)]
    for name, kind, start, rob, imp, up, pub, inc in plans:
        oid = outcome(p, name, kind=kind, studies=("Haddad 2026",),
                      comparison="The AI tool against nurse triage" if kind == "accuracy" else
                      "AI-supported triage compared with usual triage")
        for who in ("NH", "CH"):
            rate_all(p, who, oid, start=start, rob=rob, imprecision=imp, upgrades={"large_effect": up} if up else None,
                     up_why="Halved in every study" if up else "", statement="x")
            C.rate(p, who, oid, "publication", pub, "Only studies by the developers" if pub else "")
            C.rate(p, who, oid, "inconsistency", inc, "Directions differ" if inc else "")
            lvl = C.compute(start, {"rob": rob, "inconsistency": inc, "indirectness": 0, "imprecision": imp,
                                    "publication": pub}, {"large_effect": up})["level"]
            C.rate(p, who, oid, "statement", C.statements(dict(C.outcomes(p))[oid], lvl)[0]["text"])
            C.set_done(p, who, oid, True)
    data = reporting.sof_docx(p, "CH")
    root = ElementTree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    tbl = [x for x in root.iter(W + "tbl") if "Inconsistency" in "".join(e.text or "" for e in x.iter(W + "t"))][-1]
    cols = [int(g.get(W + "w")) for g in tbl.iter(W + "gridCol")]
    rows = [[" ".join("".join((e.text or "") if e.tag == W + "t" else " " if e.tag == W + "br" else "" for e in p_.iter())
                      for p_ in tc.findall(W + "p")) for tc in tr.findall(W + "tc")] for tr in tbl.findall(W + "tr")]
    assert rows[0] == ["Outcome", "Studies, design and participants", "Risk of bias", "Inconsistency", "Indirectness",
                       "Imprecision", "Publication bias", "Other considerations", "Certainty"]
    body = [r for r in rows[1:] if len(r) == len(rows[0])]
    effects = [r for r in rows[1:] if len(r) == 1]
    assert len(body) == len(effects) == len(plans) and all(r[0].startswith("Effect for ") for r in effects)
    assert effects[1][0].startswith("Effect for under-triage: ")
    words = " ".join(" ".join(r) for r in body)
    for w in ("Extremely serious", "Strongly suspected", "Very serious", "⊕⊕⊕◯", "⊕◯◯◯", "Moderate", "Very low",
              "Starting certainty: high"):
        assert w in words, w
    assert "assessed with ROBINS\u2011I" in words           # a non-breaking hyphen: no "I" alone on a line
    need = docx.min_widths(rows[0], body, compact=True)
    assert all(c >= n for c, n in zip(cols, need)), list(zip(rows[0], cols, need))
    assert sum(cols) == docx.LANDSCAPE_WIDTH


def test_imprecision_rated_down_three_levels_by_both_and_agreed(proj):
    p = proj
    oid = outcome(p, "Under-triage", studies=("Lee 2025",))
    st = "The evidence is very uncertain about the effect of AI-supported triage on under-triage."
    rate_all(p, "NH", oid, start="rct", rob=0, imprecision=-3, statement=st)
    rate_all(p, "CH", oid, start="rct", rob=0, imprecision=-2, statement=st)
    for who in ("NH", "CH"):
        C.set_done(p, who, oid, True)
    C.agree(p, "CH", oid, "imprecision", -3, "The interval runs from a large benefit to a large harm")
    c = C.comparison(p, oid)
    assert c["complete"] and c["text"] == "Very low"
    imp = next(x for x in C.evidence_profile(p, "CH")["outcomes"][0]["domains"] if x["id"] == "imprecision")
    assert imp["rating"] == "Extremely serious" and imp["change"] == -3
    with pytest.raises(ProjectError, match="only imprecision"):
        C.agree(p, "CH", oid, "inconsistency", -3, "x")


def test_test_accuracy_follows_grade_for_test_accuracy(proj):
    """GRADE guidance 21: patients with diagnostic uncertainty and an appropriate reference standard; publication bias
    not judged by funnel plots; separate outcomes; statements not credited to Santesso et al. (interventions)."""
    p = proj
    assert "diagnostic uncertainty" in C.HELP["start"] and "appropriate reference standard" in C.HELP["start"]
    pub = C.HELP_ACCURACY["publication"]
    assert "misleading" in pub and "no benefit" not in pub and "GRADE guidance 21" in pub
    assert "written for the effects of interventions" in C.HELP_ACCURACY["statement"]
    assert "per 1,000 patients tested" in C.HELP_ACCURACY["effect"]
    T = C.grade_terms()
    assert T["help_accuracy"]["statement"] == C.HELP_ACCURACY["statement"]
    assert next(dm for dm in T["domains"] if dm["id"] == "publication")["help_accuracy"] == pub
    tp = outcome(p, "True positives", kind="accuracy", studies=("Haddad 2026",), comparison="The AI tool against nurse triage")
    fn = outcome(p, "False negatives", kind="accuracy", studies=("Haddad 2026",), comparison="The AI tool against nurse triage")
    assert "diagnostic uncertainty" in C.outcome_view(p, tp, "NH")["suggest"]["why"]
    m = C.method_text(p)
    assert "patients with diagnostic uncertainty, comparing the test with an appropriate reference standard" in m
    assert "each accuracy outcome is rated separately" in m and "Santesso" not in m
    for oid in (tp, fn):
        _finish_both(p, oid, start="dta", rob=-1, imprecision=0,
                     statement="The AI tool probably has the accuracy shown for true positives against nurse triage.")
    m = C.method_text(p)
    assert "Findings about accuracy were described with the same words for each level of certainty" in m
    assert "Santesso" not in m
    assert not C.comparison(p, tp)["complete"] and C.ACC_MISSING in C.comparison(p, tp)["problems"]
    C.set_accuracy(p, "CH", {"comparison": "The AI tool against nurse triage", "condition": "urgent conditions",
                             "prev": [{"p": 10, "source": "the median of the included studies"}],
                             "sens": {"est": 0.9}})
    assert C.comparison(p, tp)["complete"] and C.comparison(p, fn)["complete"]
    o = C.evidence_profile(p, "CH")["outcomes"][0]
    assert all(u["rating"] == "None" for u in o["upgrades"]) and "found in the studies" not in o["meaning"]
    assert [(r["id"], r["values"]) for r in o["accuracy"]["rows"]] == [("tp", ["90"])]


def test_test_accuracy_has_its_pre_test_probability_and_numbers_per_1000(proj):
    """GRADE guidance 21 part 2 (Schünemann et al. 2020): a test's summary of findings gives the true and false results
    per 1,000 people tested at a stated pre-test probability, with the certainty of each. The re-review found only the
    intervention layout and the statement "probably has the accuracy shown for specificity for high acuity (true
    negatives and false positives)"."""
    p = proj
    comp = "The AI tool against nurse triage"
    sens = outcome(p, "Sensitivity for urgent patients", kind="accuracy", studies=("Haddad 2026",), comparison=comp)
    k = keys(p)
    spec = C.define(p, "CH", {"name": "Urgent patients: results without the condition", "importance": "important",
                              "kind": "accuracy", "comparison": comp, "result": "spec",
                              "studies": [k["Haddad 2026"]]})["o"]
    with pytest.raises(ProjectError, match="which results"):
        C.define(p, "CH", {"name": "Other", "importance": "important", "kind": "accuracy", "result": "auc"})
    defs = dict(C.outcomes(p))
    assert C.accuracy_result(defs[sens]) == "sens" and C.accuracy_result(defs[spec]) == "spec"
    assert C.accuracy_result({"name": "False negatives (missed urgent patients)"}) == "fn"
    assert C.accuracy_result({"name": "True negatives and false positives"}) == "spec"
    assert C.accuracy_result({"name": "Accuracy for urgent patients"}) == ""
    need = [x for x in C.readiness(p, "CH") if x["fix_label"] == "Enter the accuracy figures"]
    assert len(need) == 1 and need[0]["text"].startswith("“The AI tool against nurse triage”: the summary of findings "
                                                          "needs the pre-test probability") and not need[0]["blocks"]
    # only the lead, with the condition, a pre-test probability with its source, and the sensitivity or specificity
    good = {"comparison": comp, "condition": "urgent conditions", "threshold": "Triage category 1 or 2",
            "prev": [{"p": "20", "source": "the median of the included studies"}, {"p": "5%", "source": "a national audit"}],
            "mode": "pooled", "interval": "ci", "sens": {"est": "0.85", "lo": "0.78", "hi": "0.90"},
            "spec": {"est": "78%", "lo": "70", "hi": "84"}}
    with pytest.raises(PermissionDenied):
        C.set_accuracy(p, "NH", good)
    for bad, msg in (({"condition": ""}, "target condition"), ({"prev": []}, "pre-test probability"),
                     ({"prev": [{"p": "120", "source": "x"}]}, "between 0 and 100"),
                     ({"prev": [{"p": "20", "source": ""}]}, "comes from"),
                     ({"sens": {"est": "0.95", "lo": "0.80", "hi": "0.90"}}, "within its interval"),
                     ({"sens": {"est": "0.85", "lo": "0.80"}}, "both ends"), ({"sens": {}, "spec": {}}, "sensitivity or"),
                     ({"comparison": "Another test against nurse triage"}, "No accuracy outcome")):
        with pytest.raises(ProjectError, match=msg):
            C.set_accuracy(p, "CH", {**good, **bad})
    C.set_accuracy(p, "CH", good)
    assert p.audit_entries()[-1]["action"] == "record the accuracy figures for a test"
    n20, n5 = C.per_1000(C.accuracy_figures(p, defs[sens]))
    assert (n20["with"], n20["without"]) == (200, 800) and (n5["with"], n5["without"]) == (50, 950)
    assert {r: C.num_text(n20[r]) for r in C.ROW_IDS} == {"tp": "170 (156 to 180)", "fn": "30 (20 to 44)",
                                                          "tn": "624 (560 to 672)", "fp": "176 (128 to 240)"}
    assert n5["tp"]["n"] + n5["fn"]["n"] == 50 and n5["tn"]["n"] + n5["fp"]["n"] == 950
    v = C.accuracy_view(p, defs[sens])
    assert v["missing"] == "" and v["sens"] == "0.85 (95% CI 0.78 to 0.90)" and v["condition"] == "urgent conditions"
    assert [(r["name"], r["who"], r["values"]) for r in v["rows"]] == [
        ("True positives", "people with urgent conditions correctly classified as having it", ["170 (156 to 180)", "43 (39 to 45)"]),
        ("False negatives", "people with urgent conditions incorrectly classified as not having it", ["30 (20 to 44)", "7 (5 to 11)"])]
    # the suggested statements read as natural frequencies, worded for the certainty, and pass the check
    d = {**C.outcomes(p)[0][1], "_acc": C.accuracy_figures(p, defs[sens])}
    say = {lvl: [s["text"] for s in C.statements(d, lvl)] for lvl in LEVEL_IDS}
    assert say[3] == ["Of 1,000 people tested, of whom 200 have urgent conditions, the AI tool probably correctly "
                      "identifies about 170 of them and misses about 30.",
                      "The AI tool probably correctly identifies about 850 per 1,000 people with urgent conditions and "
                      "misses about 150."]
    assert say[2][0].endswith("the AI tool may correctly identify about 170 of them and miss about 30.")
    assert say[4][0].endswith("the AI tool correctly identifies about 170 of them and misses about 30.")
    assert say[1] == ["The evidence is very uncertain about the sensitivity of the AI tool for urgent conditions.",
                      "Of 1,000 people tested, of whom 200 have urgent conditions, the AI tool may correctly identify "
                      "about 170 of them and miss about 30, but the evidence is very uncertain."]
    ds = {**defs[spec], "_acc": C.accuracy_figures(p, defs[spec])}
    assert C.statements(ds, 3)[0]["text"] == ("Of 1,000 people tested, of whom 800 do not have urgent conditions, the AI "
                                              "tool probably correctly classifies about 624 of them as not having it and "
                                              "incorrectly classifies about 176 as having it.")
    for lvl in LEVEL_IDS:
        for dd in (d, ds):
            assert all(C.check_statement(s["text"], lvl, dd)["ok"] for s in C.statements(dd, lvl)), (lvl, dd["name"])
    assert C.outcome_view(p, sens, "NH")["statements"]["3"][0]["text"] == say[3][0]
    # finished: the summary of findings has GRADE 21's layout
    for oid, lvl, st in ((sens, 2, say[2][0]), (spec, 3, C.statements(ds, 3)[0]["text"])):
        for who in ("NH", "CH"):
            rate_all(p, who, oid, start="dta", rob=-1, imprecision=-1 if lvl == 2 else 0, statement=st)
            C.set_done(p, who, oid, True)
        assert C.comparison(p, oid)["complete"], C.comparison(p, oid)["problems"]
    t = _docx_text(reporting.sof_docx(p, "CH"))
    for x in ("Target condition\nUrgent conditions", "Threshold\nTriage category 1 or 2",
              # one study and nothing pooled: the figures are that study's, never "pooled"
              "Sensitivity (from the single study)\n0.85 (95% CI 0.78 to 0.90)",
              "Specificity (from the single study)\n0.78 (95% CI 0.70 to 0.84)",
              "Pre-test probabilities (prevalence)\n20.0%, from the median of the included studies; 5.0%, from a national "
              "audit", "Test result\nPer 1,000 people tested (95% CI), pre-test probability 20.0%\nPer 1,000 people tested "
              "(95% CI), pre-test probability 5.0%\nNumber of participants (studies)\nCertainty of the evidence (GRADE)",
              "True positives\nPeople with urgent conditions correctly classified as having it\n170 (156 to 180)\n43 (39 to 45)",
              "False positives\nPeople without urgent conditions incorrectly classified as having it\n176 (128 to 240)",
              "What this means: " + say[2][0], "Sensitivity for urgent patients · importance: critical"):
        assert x in t, x
    assert t.index("True positives\n") < t.index("False negatives\n") < t.index("True negatives\n") < t.index("False positives\n")
    assert "At a pre-test probability of 20.0% from the median of the included studies, per 1,000 people tested, with " \
           "95% confidence intervals in brackets: true positives 170 (156 to 180); false negatives 30 (20 to 44)." in t
    m = C.method_text(p)
    assert ("Accuracy was presented as the numbers of true and false results per 1,000 people tested (Schünemann et al. "
            "2020): for the AI tool against nurse triage, at a pre-test probability of 20.0% from the median of the "
            "included studies and of 5.0% from a national audit, worked out from the sensitivity and specificity of "
            "the single study.") in m and "Pooled" not in t
    row = dict(zip(*_sheet(C.profile_xlsx(p, "CH"), 1)[:2]))
    assert "At a pre-test probability of 5.0% from a national audit, per 1,000 people tested" in row["Effect"]
    # the numbers per 1,000 entered directly: they must add up to the people with (and without) the condition
    with pytest.raises(ProjectError, match="must add up to 200"):
        C.set_accuracy(p, "CH", {**good, "mode": "direct", "prev": good["prev"][:1],
                                 "direct": [{"tp": "170 (156 to 182)", "fn": "40"}]})
    with pytest.raises(ProjectError, match="whole number per 1,000"):
        C.set_accuracy(p, "CH", {**good, "mode": "direct", "prev": good["prev"][:1], "direct": [{"tp": "about 170"}]})
    C.set_accuracy(p, "CH", {**good, "mode": "direct", "prev": good["prev"][:1],
                             "direct": [{"tp": "171 (156 to 182)", "fn": "29 (18 to 44)", "tn": "620", "fp": "180"}]})
    assert C.accuracy_view(p, defs[sens])["rows"][0]["values"] == ["171 (156 to 182)"]
    assert "as calculated by the authors" in C.method_text(p)
    # the comparison reworded: its figures go with it
    C.define(p, "CH", {**defs[sens], "comparison": "The AI triage tool against nurse triage", "studies": [k["Haddad 2026"]]}, sens)
    C.define(p, "CH", {**defs[spec], "comparison": "The AI triage tool against nurse triage", "studies": [k["Haddad 2026"]]}, spec)
    assert C.accuracy_view(p, dict(C.outcomes(p))[sens])["rows"][0]["values"] == ["171 (156 to 182)"]


@pytest.fixture
def running(proj):
    httpd, url = server.serve(0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    server.APP.project = proj
    yield proj, httpd.server_address[1], url.split("t=")[1]
    httpd.shutdown()


def call(port, tok, method, path, body=None, key=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    hd = {"Host": f"127.0.0.1:{port}", "X-Token": tok, "Content-Type": "application/json"}
    if key:
        hd["X-Project"] = key
    c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=hd)
    r = c.getresponse()
    data = r.read()
    c.close()
    try:
        return r.status, json.loads(data)
    except ValueError:
        return r.status, data


def test_the_pages_keep_ratings_blind_through_the_api(running):
    p, port, tok = running
    k = keys(p)
    as_ = lambda who: call(port, tok, "POST", "/api/me", {"reviewer": who})   # noqa: E731
    as_("NH")
    st, r = call(port, tok, "POST", "/api/certainty/define", {"outcome": {"name": "Time to triage", "importance": "critical",
                                                                          "kind": "effect", "studies": [k["Okafor 2024"]]}})
    assert st == 403                                       # only the lead
    as_("CH")
    st, r = call(port, tok, "POST", "/api/certainty/define", {"outcome": {"name": "Time to triage", "importance": "critical",
                                                                          "kind": "effect", "studies": [k["Okafor 2024"]]}})
    assert st == 200 and r["outcomes"][0]["name"] == "Time to triage"
    oid = r["o"]
    as_("NH")
    secret = "NH's private reason: confounding by indication"
    for f, v, x in (("start", "nrs", "Protocol"), ("rob", -1, secret), ("inconsistency", 0, ""), ("indirectness", 0, ""),
                    ("imprecision", 0, ""), ("publication", 0, ""), ("effect", "1 study, 400 patients", ""),
                    ("statement", "AI-supported triage may reduce time to triage.", "")):
        st, r = call(port, tok, "POST", "/api/certainty/rate", {"o": oid, "f": f, "v": v, "x": x, "as": "NH"})
        assert st == 200, r
    st, r = call(port, tok, "POST", "/api/certainty/rate", {"o": oid, "f": "rob", "v": -1, "x": "", "as": "CH"})
    assert st == 400 and "another tab" in r["error"]       # the page was opened as someone else
    st, r = call(port, tok, "POST", "/api/certainty/done", {"o": oid, "done": True, "as": "NH"})
    assert st == 200 and r["view"]["done_mine"]
    as_("CH")
    for path in (f"/api/certainty/outcome?o={oid}", "/api/certainty", "/api/readiness", "/api/overview"):
        st, body = call(port, tok, "GET", path)
        assert st == 200 and secret not in json.dumps(body), path
    st, body = call(port, tok, "GET", "/download/certainty")
    assert st == 403
    st, body = call(port, tok, "GET", f"/api/certainty/outcome?o={oid}")
    assert body["view"]["comparison"] is None and body["view"]["state_a"] == "finished"
    for f, v in (("start", "nrs"), ("rob", 0), ("inconsistency", 0), ("indirectness", 0), ("imprecision", 0), ("publication", 0),
                 ("effect", "1 study, 400 patients"), ("statement", "AI-supported triage may reduce time to triage.")):
        assert call(port, tok, "POST", "/api/certainty/rate", {"o": oid, "f": f, "v": v, "x": "Protocol" if f == "start" else ""})[0] == 200
    st, r = call(port, tok, "POST", "/api/certainty/done", {"o": oid, "done": True})
    assert st == 200 and secret in json.dumps(r["view"]["comparison"])      # now both have finished: both see both
    st, r = call(port, tok, "POST", "/api/certainty/agree", {"o": oid, "f": "rob", "v": 0, "x": ""})
    assert st == 200 and r["view"]["comparison"]["complete"]
    # the lead agrees a statement worded as moderate for low certainty: the outcome is not finished until put right
    st, r = call(port, tok, "POST", "/api/certainty/agree", {"o": oid, "f": "statement",
                                                             "v": "AI-supported triage probably reduces time to triage."})
    c = r["view"]["comparison"]
    assert st == 200 and not c["complete"] and c["statement_check"]["word"] == "probably"
    st, ov = call(port, tok, "GET", "/api/overview")
    assert ov["certainty"] == {"outcomes": 1, "finished": 0}
    st, r = call(port, tok, "POST", "/api/certainty/keep_statement", {"o": oid, "x": "", "as": "CH"})
    assert st == 400 and "Say why" in r["error"]
    st, r = call(port, tok, "POST", "/api/certainty/keep_statement", {"o": oid, "x": "Agreed with the supervisor", "as": "NH"})
    assert st == 400 and "another tab" in r["error"]
    st, r = call(port, tok, "POST", "/api/certainty/keep_statement", {"o": oid, "x": "Agreed with the supervisor", "as": "CH"})
    assert st == 200 and r["view"]["comparison"]["complete"] and r["view"]["comparison"]["statement_check"]["kept"]
    st, ov = call(port, tok, "GET", "/api/overview")
    assert ov["certainty"] == {"outcomes": 1, "finished": 1}
    as_("NH")
    st, data = call(port, tok, "GET", "/download/certainty")
    assert st == 200 and zipfile.ZipFile(io.BytesIO(data)).testzip() is None


def _paragraphs(data: bytes) -> list:
    """(style, text, in a table) for every paragraph of a Word document, in tables too; line breaks as spaces."""
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    root = ElementTree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    cells = {id(x) for tc in root.iter(W + "tc") for x in tc.iter(W + "p")}
    out = []
    for para in root.iter(W + "p"):
        st = para.find(f"{W}pPr/{W}pStyle")
        t = "".join((e.text or "") if e.tag == W + "t" else " " if e.tag == W + "br" else "" for e in para.iter())
        out.append((st.get(W + "val") if st is not None else "", t, id(para) in cells))
    return out


def _depth(t: str) -> int:
    d = most = 0
    for ch in t:
        d += ch == "("
        d -= ch == ")"
        most = max(most, d)
    return most


def test_the_grade_outputs_follow_the_report_style_rules(proj):
    """As in every report (fix-report2): no sentence starts with a number, no bracket is doubled or nested, and every
    percentage has one decimal place (the 95% of a confidence interval is a name, not a result). Outcome names with
    brackets of their own, a test, a rating changed by the lead, ROBINS-I left high and an unfinished outcome."""
    p = proj
    comp = "The AI tool against nurse triage"
    sens = outcome(p, "Sensitivity for urgent patients (true positives and false negatives)", kind="accuracy",
                   studies=("Haddad 2026",), comparison=comp)
    C.set_accuracy(p, "CH", {"comparison": comp, "condition": "urgent conditions", "prev": [
        {"p": "12.5", "source": "the median of the included studies"}, {"p": "30", "source": "a national audit"}],
        "sens": {"est": "0.85", "lo": "0.78", "hi": "0.90"}})
    d = {**dict(C.outcomes(p))[sens], "_acc": C.accuracy_figures(p, dict(C.outcomes(p))[sens])}
    _finish_both(p, sens, start="dta", rob=-1, imprecision=0, statement=C.statements(d, 3)[0]["text"])
    los = outcome(p, "Length of stay (hours)", studies=("Okafor 2024",))
    for who in ("NH", "CH"):
        rate_all(p, who, los, start="nrs_robins", rob=0, imprecision=0,
                 statement="AI-supported triage results in little to no difference in length of stay.")
        C.rate(p, who, los, "rob", 0, "Confounders adjusted for")
        C.set_done(p, who, los, True)
    C.agree(p, "CH", los, "imprecision", -1, "Few participants", why="Both missed the small sample")
    outcome(p, "Over-triage (proportion of patients)", importance="important")           # not finished
    m = C.method_text(p)
    sof = reporting.sof_docx(p, "CH")
    meth = reporting.methods_docx(p, "CH")
    texts = [("methods paragraph", m, False)] + [
        ("summary of findings", t, cell) for style, t, cell in _paragraphs(sof)
        if style not in ("Title", "Subtitle", "Caption") and not style.startswith("Heading")]
    on = False
    for style, t, _ in _paragraphs(meth):
        if style.startswith("Heading"):
            on = t == "Certainty of the evidence"
        elif on:
            texts.append(("methods text", t, False))
    assert any(w == "methods text" for w, _, _ in texts) and len(texts) > 40
    for where, t, cell in texts:
        t = " ".join(t.split())
        if not cell:                                     # a sentence; a table cell may start "8 studies" or "0.85"
            assert not re.match(r"\d", t), (where, t)
            assert not re.search(r"(?<!al)[.!?] \d", t), (where, t)
        assert ") (" not in t and "((" not in t and "))" not in t and _depth(t) <= 1, (where, t)
        for x in re.findall(r"(\d+(?:\.\d+)?)%(?! CI| confidence)", t):
            assert re.fullmatch(r"\d+\.\d", x), (where, x, t)
    joined = " ".join(t for _, t, _ in texts)
    assert "12.5%" in joined and "30.0%" in joined and "%" in m
    sec = reporting._certainty_text(p, "CH")[-1]
    assert sec.endswith("not yet finished: length of stay (hours) and over-triage (proportion of patients).")
    assert _depth(sec) <= 1
    for row in _sheet(C.profile_xlsx(p, "CH"), 1)[1:]:                   # the workbook's effect cells too
        assert ") (" not in row[13] and _depth(row[13]) <= 1, row[13]


def test_the_method_text_describes_only_the_starts_used_for_prognosis(proj):
    """A review of prognostic factors (QUIPS) starts observational evidence at High (GRADE guidelines 28): the method
    text says so, and does not describe starts for the effects of interventions that were not used."""
    p = proj
    k = keys(p)
    review.define_assessment(p, "NH", k["Okafor 2024"], "prognostic_factor", "Age and under-triage")
    oid = outcome(p, "Age and under-triage", studies=("Okafor 2024",))
    assert C.outcome_view(p, oid, "NH")["suggest"]["start"] == "prog"
    for who in ("NH", "CH"):
        rate_all(p, who, oid, start="prog", rob=-2, imprecision=-1)
        C.set_done(p, who, oid, True)
    assert C.comparison(p, oid)["text"] == "Very low"                         # High − 2 − 1
    m = C.method_text(p)
    assert "Evidence about prognostic factors from observational studies started at high certainty (Foroutan et al. 2020)." in m
    assert "evidence from randomised trials" not in m.lower()


def test_the_demo_review_has_grade_outcomes_for_the_report_checker(tmp_path):
    """tests/demo_review.py grade(): what tools/check_reports.py renders for both people. Every outcome finished and
    agreed: an intervention outcome, a test with its figures and its true and false results, a statement kept with the
    lead's reason, and a rating the lead changed with the reason."""
    import demo_review
    info = demo_review.build(str(tmp_path / "demo"))
    ids = demo_review.grade(info)
    p = info["p"]
    assert len(ids) == 7 and all(C.comparison(p, oid)["complete"] for oid in ids.values())
    assert C.comparison(p, ids["Under-triage"])["statement_check"]["kept"]
    assert C.comparison(p, ids["Over-triage"])["overrides"] == 1
    results = [C.accuracy_view(p, dict(C.outcomes(p))[oid])["result"] for name, oid in ids.items()
               if dict(C.outcomes(p))[oid]["kind"] == "accuracy"]
    assert results == ["tp", "fn", "tn", "fp"]
    for me in ("CH", "NH"):
        assert reporting.status(p, me, "rep_sof") == ("ready", "") and C._why_not(p, me) == ""
        t = _docx_text(reporting.make(p, me, "rep_sof"))
        assert "Not final" not in t and "Summary of findings 2 (PRISMA 2020 item 22): test accuracy" in t
        for x in ("True positives\nPeople with high acuity (triage category 1 or 2) correctly classified as having it\n"
                  "172 (158 to 182)\n344 (316 to 364)", "False positives\n", "Worded more strongly than GRADE's standard "
                  "wording for low certainty", "Changed by the project lead (Colin Hawthorne): both reviewers rated it no serious "
                  "concerns.", "the AI triage tool may miss about 28 of them."):
            assert x in t, (me, x)
