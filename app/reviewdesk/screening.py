"""The screening rules: who screens which record, what each record's outcome is, and agreement.

Title and abstract (stage "ta")
  Votes are Include, Maybe or Exclude; Include and Maybe both send a record forward.
  Plans per record set:
    dual                 two reviewers screen every record independently
    single               one reviewer screens every record
    sample_then_single   both screen a random sample first; if Cohen's kappa on it reaches the threshold,
                         one reviewer screens the rest, otherwise both do
  An optional AI second screener sits behind single screening: any record the single reviewer excludes
  that the AI would keep is screened by the second reviewer. The AI never excludes anything.
  Calibration (stage "cal") is a practice round on records outside the sample; it is never counted.
  A verification sample (stage "vs", from 1.3.1; evaluation.py) may be drawn once the single reviewer has finished: a
  second person screens, without seeing any earlier decision, a random sample of the records that left the review on
  the single reviewer's vote alone (with the AI behind: those the AI would not keep either), mixed with some the
  single reviewer sent on. Once the sample is finished (its verifier has voted on every record of it, or the lead
  has ended it), any sampled record that person would send on goes to full text; until then their votes change
  nothing at all, so that nothing anyone can see moves as they vote. Their votes change nothing else, and are never
  counted for agreement. The moment a sample is finished its votes are fixed: until then its verifier may change
  any of them (they see nothing of what was decided before); from then on they can see how each record was decided,
  so a vote written later never counts (verify_votes).

Full text (stage "ft")
  Include, Exclude (with a reason), Awaiting information (awaiting classification), Ongoing study (registered or
  ongoing, no results yet: listed, not included), or Not retrieved; by two reviewers independently
  unless the project says otherwise. Disagreements are settled together and recorded by the lead. Who assesses (both
  or one) is fixed when a record set starts, with the rest of its plan; a set started by an earlier version reads the
  setting, which cannot be changed once screening has started (Project.update_settings).

Agreement uses each reviewer's independent vote on a record: their latest vote cast before they could see the other's.
Since 1.3.0 each vote records, when it is written, what its voter could know, as their own computer had the files at
that moment ("cmp" in the row, worked out by compare_state; never shown to a reviewer):
  0  nothing to compare with yet: the other reviewer had not voted on the record (for the calibration round and the
     agreement sample, which are compared as a whole: the round was not yet complete on this computer)
  1  the other reviewer's vote on the record was there, and this is this voter's first vote on it (they vote second,
     still blind)
  2  the comparison was open to this voter: both had finished every record of the round or sample, or (elsewhere)
     both had already voted on this record
A vote counts for agreement if it comes before the voter's first row marked 2, and the second to vote (whose first
vote is marked 1) may correct a slip (with the left-arrow key) within two minutes of that first vote, outside the
calibration round and the agreement sample: both are rows of one person, so no two computers' clocks are compared.
A row without the mark (written by an earlier version) is judged as before, by time: for the calibration round and the
agreement sample, cast by the moment the second reviewer finished; elsewhere, by the moment the second of the two
first voted on that record (the second to vote: two minutes after it). Votes changed after that are kept, and decide
the record's outcome, but are not used for agreement.
A record that needs two reviewers goes forward only once both have voted, so neither sees the other's vote.

Who a plan needs (settings_plan, plan_problems, roles: one set of rules, used by start, the checks of what is missing
and the steps of the review in workflow.py)
  The first and second reviewer are the people who work in duplicate: they screen (in the two plans where both
  screen), run the calibration round, assess full texts when both assess, judge risk of bias and rate certainty.
    dual, sample_then_single   need two different reviewers who are in the project
    single                     needs the one person who screens. It has no calibration round (a practice round for two
                               people who screen the same records); whatever number the settings hold is not used.
                               With no first reviewer named, the person who screens is the review's one reviewer.
  Full text by both needs two different reviewers; by one, it is the person who screens alone (single,
  sample_then_single) or the first reviewer (dual). Nobody required to assess a report means it is not yet assessed:
  a report is never included, excluded or anything else without the votes its plan requires.
  A plan with one reviewer is allowed, and every output says that one person did the work; what cannot work with
  one person (the AI second screener, which sends records to a second reviewer) is refused before screening starts.
"""
from __future__ import annotations

import copy
import os
import random

from . import stats, util
from .project import Project, ProjectError, SETS, clean_id, written_order

FWD = {"include", "maybe"}
TA_CHOICES = ("include", "maybe", "exclude")
SLIP_SECONDS = 120  # a vote corrected this soon after it was cast (with the ← key) counts as corrected, not a disagreement
EPS = stats.THRESHOLD_EPS   # kappa is compared with the threshold allowing for rounding: 0.7999999999999999 meets 0.80
FT_CHOICES = ("include", "exclude", "awaiting", "ongoing", "not_retrieved")


# ================================================================================================ votes
STAGE_CHOICES = {"cal": TA_CHOICES, "ta": TA_CHOICES, "ft": FT_CHOICES, "vs": TA_CHOICES}
STAGE_NAMES = {"cal": "calibration", "ta": "title and abstract", "ft": "full text", "vs": "verification sample"}


def is_vote(d) -> bool:
    """A row of a decisions file that is a vote: it names a record, a stage and a decision that stage allows. Anything
    else in the file (a row ReviewDesk did not write) is not counted; the data checks report it
    (Project.integrity_report)."""
    return (isinstance(d, dict) and isinstance(d.get("r"), str) and bool(d["r"]) and isinstance(d.get("s"), str)
            and isinstance(d.get("d"), str) and d["d"] in STAGE_CHOICES.get(d["s"], ()))


def votes(p: Project) -> dict:
    """{stage: {record: {reviewer: {"first": d, "latest": d, "n": k, "all": [d, ...], "at": [i, ...]}}}}
    ("at": where each of those votes stands among all that person's rows, oldest first: Project.decisions)"""
    dec = p.decisions()
    key = tuple((r, len(v), v[-1].get("t") if v else "") for r, v in sorted(dec.items()))
    hit = p._cache.get("votes")
    if hit and hit[0] == key:
        return hit[1]
    V: dict = {}
    for rid, rows in dec.items():
        for at, d in enumerate(rows):
            if not is_vote(d):
                continue
            slot = V.setdefault(d["s"], {}).setdefault(d["r"], {}).get(rid)
            if slot is None:
                V[d["s"]][d["r"]][rid] = {"first": d, "latest": d, "n": 1, "all": [d], "at": [at]}
            else:
                slot["latest"] = d
                slot["n"] += 1
                slot["all"].append(d)
                slot["at"].append(at)
    p._cache["votes"] = (key, V)
    return V


def latest(V: dict, stage: str, rec: str, who: str):
    x = V.get(stage, {}).get(rec, {}).get(who)
    return x["latest"] if x else None


def first(V: dict, stage: str, rec: str, who: str):
    x = V.get(stage, {}).get(rec, {}).get(who)
    return x["first"] if x else None


def as_of(V: dict, stage: str, rec: str, who: str, when: float):
    """This reviewer's latest vote on the record cast at or before `when` (seconds since 1970)."""
    x = V.get(stage, {}).get(rec, {}).get(who)
    if not x:
        return None
    got = None
    for d in x["all"]:
        if util.ts(d.get("t")) <= when:
            got = d
    return got or x["first"]


CMP = "cmp"           # what a vote's voter could know when it was written (see the module notes)


def _cmp(d: dict):
    """A row's mark (0, 1 or 2), or None for a row without one (an earlier version's)."""
    c = d.get(CMP)
    return c if type(c) is int and c in (0, 1, 2) else None


def compare_state(p: Project, me: str, rec: str, stage: str) -> int:
    """What this voter could know about the other reviewer's votes as this computer has the files now, for a vote
    about to be written (see the module notes): 0, 1 or 2. Uses only what votes(p) and the state already hold."""
    V = votes(p)
    on = V.get(stage, {}).get(rec, {})
    mine = me in on
    other = any(w != me for w in on)
    whole = None                                # the round or sample this record is compared in as a whole
    if stage in ("cal", "ta"):
        set_name = (p.by_id().get(rec) or {}).get("set", "main")
        sv = p.state_ro().get("sets", {}).get(set_name) or {}
        if stage == "cal":
            whole = next((rnd for rnd in sv.get("calibration_rounds") or [] if rec in rnd), None)
        elif rec in (sv.get("sample") or ()):
            whole = sv.get("sample")
        if whole is not None:
            R = named(plan(p, set_name))
            S = V.get(stage, {})
            done = len(R) == 2 and all(R[0] in S.get(r, {}) and R[1] in S.get(r, {}) for r in whole)
            return 2 if done else (1 if other and not mine else 0)
    return 2 if other and mine else (1 if other else 0)


NOT_SHOWN = (CMP, "time")


def without_cmp(d):
    """A vote row as a page may be sent it: without its mark, which would show whether the other had voted, and
    without the time it took (timing.py: times are shown on no page; they leave ReviewDesk only in the lead's
    evaluation download)."""
    return {k: v for k, v in d.items() if k not in NOT_SHOWN} if isinstance(d, dict) and (CMP in d or "time" in d) else d


def cast(p: Project, me: str, rec: str, stage: str, decision: str, reason: str = "", note: str = "",
         study: str = "", time: dict | None = None) -> dict:
    """Save one vote, marked with what its voter could know (compare_state), worked out and written under the
    project's lock. time: how long the record was on screen (timing.clean), saved with the vote when the project
    records times. Returns the row as the voter may see it."""
    with p.lock:
        c = compare_state(p, me, rec, stage) if rec in p.by_id() else 0   # (add_decision refuses an unknown record)
        extra = {CMP: c, **({"time": time} if time else {})}
        return without_cmp(p.add_decision(me, rec, stage, decision, reason, note, study, extra=extra))


def own_votes(V: dict, rec: str, me: str) -> dict:
    """{stage: this person's latest vote on the record, as they may see it}."""
    return {s: without_cmp(latest(V, s, rec, me)) for s in ("cal", "ta", "ft", "vs")}


# ================================================================================================ plans
def _num(v, default, kind=float):
    """A setting as a number; the default only when it is missing or not a number (0 stays 0)."""
    try:
        return kind(v) if v is not None and v != "" else default
    except (TypeError, ValueError):
        return default


def named(pl: dict) -> list:
    """The plan's reviewers (the first and the second reviewer), each once, in order: nobody, one person or two."""
    out = []
    for x in (pl.get("reviewers") or [])[:2]:
        if x and isinstance(x, str) and x not in out:
            out.append(x)
    return out


def plan(p: Project, set_name: str) -> dict:
    """The screening plan in force for a record set. Shared: do not change the result."""
    return p.memo(("plan", set_name), lambda: _plan(p, set_name))


def _plan(p: Project, set_name: str) -> dict:
    s = p.settings_ro()
    st = p.state_ro().get("sets", {})
    fixed = (st.get(set_name, {}) or {}).get("plan_at_start")
    if fixed:                                   # once a set has started, its pre-specified plan is the one in force
        out = dict(fixed)
        out["ft_mode"] = fixed_ft_mode(fixed, s)
        out.pop("ft_fixed", None)
        out.setdefault("conflicts", (s.get("screening") or {}).get("conflicts_at_title_abstract", "forward"))
        out["set"] = set_name
        return _in_force(out)
    return settings_plan(s, set_name, st.get("main", {}) or {})


def fixed_ft_mode(fixed: dict, s: dict) -> str:
    """Who assesses full texts under a started set's plan ("dual" or "single"): as recorded when the set started, or,
    for a set started by an earlier version (which recorded the setting but did not hold to it), the setting, which
    cannot be changed once screening has started."""
    if fixed.get("ft_fixed") and fixed.get("ft_mode") in ("dual", "single"):
        return fixed["ft_mode"]
    return (s.get("fulltext") or {}).get("mode", "dual")


def settings_plan(s: dict, set_name: str = "main", main: dict | None = None) -> dict:
    """The plan a record set would start with, from the settings alone (s; main: the main set's state, for a set
    that follows how the main screening ended). It is the plan as it would be in force: see the module notes."""
    sc = s.get("screening") or {}
    base = {"reviewers": list(sc.get("reviewers") or [])[:2], "single_reviewer": sc.get("single_reviewer") or "",
            "second_reviewer": sc.get("second_reviewer") or "", "conflicts": sc.get("conflicts_at_title_abstract", "forward"),
            "ai_second_screener": bool(sc.get("ai_second_screener")), "ai_keep_rate_stop": _num(sc.get("ai_keep_rate_stop"), 0.5),
            "kappa_threshold": _num(sc.get("kappa_threshold"), 0.8), "sample_fraction": _num(sc.get("sample_fraction"), 0.2),
            "calibration_size": max(0, _num(sc.get("calibration_size"), 0, int)), "calibration_gate": _num(sc.get("calibration_gate"), 0.0),
            "ft_mode": (s.get("fulltext") or {}).get("mode", "dual"), "set": set_name}
    main_mode = sc.get("mode", "sample_then_single")
    if main_mode == "dual":                     # nobody screens alone in that plan: a name left over from another choice
        base["single_reviewer"] = ""            # (the set-up pages do not show the box) is no part of it
    if set_name == "main":
        base["mode"] = main_mode
        if main_mode == "dual":
            base["ai_second_screener"] = False
    else:
        main = main or {}
        other = (s.get("other_sets") or {}).get(set_name, {}) or {}
        mode = other.get("mode", "dual")
        if mode == "inherit":
            base["mode"] = main.get("remainder_mode") or ("dual" if len(named(base)) == 2 else "single")
            base["ai_second_screener"] = bool(main.get("ai_net_active"))
            base["inherited"] = True
        else:
            base["mode"] = mode
            base["ai_second_screener"] = bool(other.get("ai_second_screener", False)) and mode == "single"
        if base["mode"] == "single" and not base["single_reviewer"]:
            base["single_reviewer"] = (named(base) or [""])[0]       # the first reviewer screens this set alone
    if base["mode"] == "single" or set_name != "main":
        base["calibration_size"] = 0            # a practice round for two people who screen the same records
    return _in_force(base)


def _in_force(pl: dict) -> dict:
    """A plan with its reviewers as the rest of the program may rely on them: each named once, and, when one
    reviewer screens every record and no first reviewer is named, that person (so that the later stages, which the
    reviewers do, are not left with nobody)."""
    raw = [x for x in (pl.get("reviewers") or [])[:2] if x]
    out = dict(pl)
    out["reviewers"] = named(pl)
    out.pop("same_person_twice", None)
    if len(raw) == 2 and raw[0] == raw[1]:
        out["same_person_twice"] = True
    if out.get("mode") == "single" and not out["reviewers"] and out.get("single_reviewer"):
        out["reviewers"] = [out["single_reviewer"]]
    return out


# What the plan's own checks say (readiness.PLAN_MESSAGES gives each its place and its button, by how it starts).
SAME_TWICE = "The first and second reviewers must be two different people (Project set-up, Screening plan)."
CHOOSE_TWO = "Choose the two reviewers who screen in duplicate (Project set-up, Screening plan)."
PEOPLE_TWO = ("Add the second reviewer to the team (Project set-up, People). The screening plan needs two people who "
              "screen independently, so that one person's mistakes are caught. If you are working alone, choose 'One "
              "reviewer screens every record' instead (Project set-up, Screening plan).")
NOT_IN_PROJECT = "One of the two screening reviewers is not in the project's list of people."
CHOOSE_SINGLE = "Choose who screens every record (Project set-up, Screening plan)."
CHOOSE_SINGLE_AFTER = "Choose who screens on their own after the sample (Project set-up, Screening plan)."
AI_SECOND = "With the AI second screener, choose a second reviewer who is not the single reviewer."
AI_PEOPLE = ("The AI second screener needs a second person in the project: any record the single reviewer excludes "
             "that the AI would keep goes to a second reviewer. Add a second person (Project set-up, People), or untick "
             "the AI second screener (Project set-up, Screening plan).")
FT_WHERE = "Project set-up, Full texts, update searches and other methods"


def _only(pl: dict, names: dict) -> str:
    """'only Sam Solo' / 'no reviewer': who the plan names, for a message that it needs two."""
    R = named(pl)
    return f"only {names.get(R[0], R[0])}" if R else "no reviewer"


def plan_problems(pl: dict, people: list) -> list:
    """What stops a record set starting with this plan, as far as its people and numbers go (check_plan adds the
    question, the criteria and the risk-of-bias plan). people: the project's people, [{"id", "name"}]. Each message
    says what to change and where."""
    out = []
    ids = {r.get("id") for r in people if isinstance(r, dict)}
    names = {r.get("id"): r.get("name") or r.get("id") for r in people if isinstance(r, dict)}
    mode, R, main = pl["mode"], named(pl), pl.get("set", "main") == "main"
    two = len(R) == 2
    alone = len(ids) < 2                        # one person in the project
    what = "" if main else SETS.get(pl.get("set"), "").lower()
    if mode in ("dual", "sample_then_single"):
        if pl.get("same_person_twice"):
            out.append(SAME_TWICE)
        elif not two and not main:
            out.append(f"Records from the {what} are set to be screened by both reviewers, which needs a first and a second "
                       f"reviewer, and the screening plan names {_only(pl, names)}. Choose 'By one reviewer' for these "
                       f"records ({FT_WHERE}).")
        elif not two:
            if alone:
                out.append(PEOPLE_TWO)
            out.append(CHOOSE_TWO)
        elif not set(R) <= ids:
            out.append(NOT_IN_PROJECT)
    elif pl.get("same_person_twice"):
        out.append(SAME_TWICE)
    elif R and not set(R) <= ids and R != [pl["single_reviewer"]]:
        out.append(NOT_IN_PROJECT)
    if mode in ("single", "sample_then_single"):
        if pl["single_reviewer"] not in ids:
            out.append(CHOOSE_SINGLE if mode == "single" else CHOOSE_SINGLE_AFTER)
        if pl["ai_second_screener"] and (pl["second_reviewer"] not in ids or pl["second_reviewer"] == pl["single_reviewer"]):
            out.append(AI_PEOPLE if alone and mode == "single" else AI_SECOND)
        if mode == "sample_then_single" and two:
            if pl["single_reviewer"] and pl["single_reviewer"] not in R:
                out.append("The person who screens alone must be one of the two who screen the agreement sample, "
                           "because the sample is what shows their screening agrees with a second reviewer's.")
            if pl["ai_second_screener"] and pl["second_reviewer"] and pl["second_reviewer"] not in R:
                out.append("The second reviewer must be one of the two who screen the agreement sample.")
    if mode == "single" and pl.get("ft_mode", "dual") != "single" and not two and not pl.get("same_person_twice"):
        # (the plans in which both screen already need the two reviewers: said once, above)
        if alone:
            out.append("Full-text review is set to both reviewers, which needs two people, and the project has one. Choose "
                       f"'One reviewer' for full-text review ({FT_WHERE}), or add a second person (Project set-up, People) "
                       "and choose the first and second reviewers.")
        else:
            out.append("Full-text review is set to both reviewers, which needs a first and a second reviewer, and the "
                       f"screening plan names {_only(pl, names)}. Choose the first and second reviewers (Project set-up, "
                       f"Screening plan), or choose 'One reviewer' for full-text review ({FT_WHERE}).")
    if mode == "sample_then_single" and not (0.05 <= pl["sample_fraction"] <= 0.9):
        out.append("The agreement sample should be between 5% and 90% of the records.")
    if mode == "sample_then_single" and not (0 < pl["kappa_threshold"] <= 1):
        out.append("The kappa needed for single screening must be between 0.01 and 1 (for example 0.8).")
    return out


def check_plan(p: Project, pl: dict) -> list:
    """Everything that stops a record set starting with this plan, each in words that say what to do and where."""
    problems = plan_problems(pl, p.reviewers())
    s = p.settings_ro()
    crit = s.get("criteria") or {}
    if not any(str(x).strip() for k in ("include", "exclude") for x in (crit.get(k) or [])):
        problems.append("Write the eligibility criteria first (Project set-up): reviewers screen against them, and they are "
                        "fixed for the AI screener when screening starts.")
    if not (s.get("question") or "").strip():
        problems.append("Write the review question first (Project set-up).")
    from . import robplan
    if not robplan.recorded(p) and not robplan.started(p):
        problems.append("Record the risk-of-bias plan first (Project set-up, Risk-of-bias plan): which tool is used for "
                        "each study design the review includes. The protocol fixes it before the work begins, so that the "
                        "choice of tool cannot follow the results.")
    for _, text in robplan.problems(robplan.plan(p)) if robplan.recorded(p) and not robplan.started(p) else []:
        problems.append(text)
    return problems


# ================================================================================================ who does what
def roles(s: dict, main: dict | None = None, fixed: dict | None = None) -> dict:
    """Who does each stage under the main plan, from the settings (or from fixed, the plan in force once screening
    has started): {"plan", "reviewers", "pair": two reviewers or [], "screen": who screens alone or "", "ft": who
    assesses full texts, "raters": who judges risk of bias and rates certainty, "extract": (first, second or "") as
    Extract data takes them from the plan until the lead names others, "one": True when one person does every stage}.
    Only people who are in the project are named."""
    ids = [r.get("id") for r in s.get("reviewers") or [] if isinstance(r, dict) and r.get("id")]
    pl = _in_force(dict({k: v for k, v in fixed.items() if k != "ft_fixed"}, ft_mode=fixed_ft_mode(fixed, s), set="main")) \
        if fixed else settings_plan(s, "main", main)
    R = [x for x in named(pl) if x in ids]
    single = pl["single_reviewer"] if pl["single_reviewer"] in ids else ""
    pair = R if len(R) == 2 else []
    ft = ft_people(pl)
    ft = [x for x in ft if x in ids]
    first = single or (R[0] if R else "")             # (as review.people takes them from the plan)
    second = next((x for x in R if x != first), "")
    one = pl["mode"] == "single" and bool(single) and len(R) <= 1 and (not R or R == [single]) and ft == [single]
    return {"plan": pl, "reviewers": R, "pair": pair, "screen": single if pl["mode"] != "dual" else "", "ft": ft,
            "raters": R, "extract": (first, second), "one": one, "people": len(ids)}


# ================================================================================================ starting a set
def start(p: Project, user: str, set_name: str) -> dict:
    p.require_lead(user)
    if set_name not in SETS:
        raise ProjectError("Unknown record set.")
    with p.lock:
        st = p.state()
        sv = st.setdefault("sets", {}).setdefault(set_name, {})
        if sv.get("phase") not in (None, "not_started"):
            raise ProjectError("Screening of this set has already started.")
        every = p.imports(include_missing=True)
        broken = [m["filename"] for m in every if m.get("_damaged")]
        if broken:
            raise ProjectError("The details file of " + ", ".join(broken) + " cannot be read (it is damaged). Restore it from "
                               "OneDrive's version history (right-click it in the imports folder), then start screening.")
        missing = [m.get("original_name") or m["filename"] for m in every if m.get("_missing")]
        if missing:
            raise ProjectError("These added files have not arrived on this computer yet: " + ", ".join(missing) + ". Wait until "
                               "the sync service has finished copying them, remove duplicates (Import records), then start screening, so "
                               "that every record is in the random order and can fall in the samples.")
        imps = p.imports()
        pending = [m.get("original_name") or m["filename"] for m in imps if m["id"] not in st.get("imports_order", []) and m["set"] == set_name]
        if pending:
            raise ProjectError("Duplicates have not been removed from some imported files yet: " + ", ".join(pending) +
                               ". On Import records, choose Remove duplicates and add the files to the records, so that "
                               "every record is de-duplicated before screening starts.")
        ids = sorted(r["id"] for r in p.records() if r["set"] == set_name)
        if not ids:
            raise ProjectError(f"No records have been added{'' if set_name == 'main' else ' from the ' + SETS[set_name].lower()} "
                               "yet. Import the exports and remove duplicates first (Import records).")
        pl = plan(p, set_name)
        problems = check_plan(p, pl)
        if problems:
            raise ProjectError(" ".join(problems))
        if pl["ai_second_screener"] and pl["mode"] == "single":
            from . import ai
            cov = ai.coverage(p, set_name)
            if cov["missing"]:
                raise ProjectError(ai_not_sent(cov["missing"]) + " Run it first (AI second screener page, Screen every "
                                   "record), so that only records the AI would keep go to the second reviewer.")
        seed = int(p.settings_ro()["screening"].get("seed") or 0) + {"main": 0, "update": 1, "other": 2}[set_name]
        order = ids[:]
        random.Random(seed).shuffle(order)
        sv.update({"order": order, "seed": seed, "started": util.now(), "started_by": user,
                   "plan_at_start": dict(pl, ft_fixed=True),       # who assesses full texts is fixed with the rest
                   "rng": "Python random.Random(seed).shuffle of the record IDs sorted in ascending order"})
        if pl["ai_second_screener"]:
            from . import ai
            if not ai.config(p).get("fixed_prompts"):     # the AI keeps the criteria as they were when screening began
                ai.save_config(p, user, {"fixed_prompts": ai.prompts(p), "prompts_fixed_at": util.now()})
        n_sample = 0
        if pl["mode"] == "sample_then_single":
            n_sample = max(1, round(len(order) * pl["sample_fraction"]))
            sv["sample"] = order[:n_sample]
        # a calibration round is for the two reviewers who screen in duplicate: never drawn without them
        cal_n = max(0, min(pl["calibration_size"], len(order) - n_sample)) if pl["mode"] != "single" and len(named(pl)) == 2 else 0
        if cal_n > 0:
            sv["calibration_rounds"] = [order[-cal_n:]]
            sv["phase"] = "calibration"
        else:
            sv["phase"] = "sample" if pl["mode"] == "sample_then_single" else "screening"
        if pl["mode"] in ("dual", "single"):
            sv["remainder_mode"] = pl["mode"]
            sv["ai_net_active"] = bool(pl["ai_second_screener"] and pl["mode"] == "single")
        p.save_state(st, user)
        p.write_seal(user)                      # what screening starts from, so later changes can be noticed
        p.audit(user, "start screening", {"set": set_name, "records": len(order), "seed": seed, "sample": n_sample,
                                          "calibration": cal_n, "mode": pl["mode"]})
        return sv


# ================================================================================================ title and abstract
def _ai_keep(p: Project) -> dict:
    from . import ai
    return ai.combined(p)


def ta_required(sv: dict, pl: dict, rec: str, V: dict, ai_keep: dict) -> list:
    """Who must screen this record at title and abstract, as things stand."""
    phase = sv.get("phase", "not_started")
    if phase == "not_started" or rec not in sv.get("_order_set", ()):
        return []
    if rec in sv.get("_sample_set", ()):
        return list(pl["reviewers"])
    if phase in ("calibration", "sample") and pl["mode"] == "sample_then_single":
        return []
    mode = sv.get("remainder_mode") or pl["mode"]
    if mode == "dual":
        return list(pl["reviewers"])
    req = [pl["single_reviewer"]]
    if sv.get("ai_net_active") and pl["second_reviewer"]:
        v = latest(V, "ta", rec, pl["single_reviewer"])
        if v and v["d"] not in FWD and ai_keep.get(rec, "keep") == "keep":
            req.append(pl["second_reviewer"])
    return req


def verify_of(sv: dict) -> dict | None:
    """A set's verification sample, as a tidy copy of what the state holds: every field of the kind the code relies
    on. None when none was drawn, or when what is there cannot be one. The state is a shared file, so it is read with
    care: a value of the wrong kind there must never stop a page from opening."""
    vf = sv.get("verify") if isinstance(sv, dict) else None
    if not isinstance(vf, dict):
        return None
    who = vf.get("reviewer")
    lists = [vf.get(k) for k in ("sample", "decoy", "order")]
    if not isinstance(who, str) or not who or \
            not all(isinstance(x, list) and all(isinstance(r, str) and r for r in x) for x in lists):
        return None
    sample, decoy, order = lists
    if not sample or len(set(sample)) != len(sample) or len(set(decoy)) != len(decoy) or set(sample) & set(decoy) \
            or sorted(order) != sorted(sample + decoy):
        return None
    if who == vf.get("single_reviewer"):         # nobody checks their own exclusions: not a sample ReviewDesk drew
        return None
    whole = lambda v, d: v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else d      # noqa: E731
    text = lambda v: v if isinstance(v, str) else ""                                                # noqa: E731
    ended = vf.get("ended") if isinstance(vf.get("ended"), dict) else None
    return {"reviewer": who, "sample": list(sample), "decoy": list(decoy), "order": list(order),
            "population": max(whole(vf.get("population"), len(sample)), len(sample)),
            "decoy_pool": max(whole(vf.get("decoy_pool"), len(decoy)), len(decoy)),
            "single_reviewer": text(vf.get("single_reviewer")), "by": text(vf.get("by")), "drawn": text(vf.get("drawn")),
            "seed": whole(vf.get("seed"), None), "ai_net": vf.get("ai_net") is True, "rng": text(vf.get("rng")),
            "ended": {k: text(ended.get(k)) for k in ("at", "by", "reason")} if ended else None}


DRAWN = "draw the verification sample"                      # the audit actions (evaluation.DRAW, END)
ENDED = "end the verification sample as it stands"


def verify_logged(p: Project, set_name: str) -> dict | None:
    """A set's verification sample as the audit log holds it (None if it holds none): every draw is logged whole, and
    so is the end of a sample the lead ended. A sample is drawn once, so the latest draw the log holds IS the set's
    sample, whatever the state holds now: the state is rewritten all the time and older copies of it can be put back,
    the log only grows. So putting back a state from before the draw (or from before the sample was ended) changes
    nothing: the same records in the same order for the same verifier, ended if it was ended, and nothing to draw
    again. (_prep_ uses the state's entry only when the log holds no draw.)
    Whose entries count: those in a person's OWN audit files (the file's name and the entry's "who" agree), written by
    the project lead, or by someone the log shows doing what only a lead can (they were the lead then: the role can
    move)."""
    return p.memo(("verify_logged", set_name), lambda: _verify_logged(p, set_name))


LEAD_ONLY = ("create project", "change settings")           # (as Project._reseal_logged takes them)


def _verify_logged(p: Project, set_name: str) -> dict | None:
    lead = clean_id(p.lead_id())
    rows = []
    for n, f in enumerate(util.files_in(p.path("audit"), "*@*.jsonl")):
        owner = os.path.basename(f).split("@")[0]

        def load(f=f, owner=owner):
            mine = [(t, k, e) for t, k, e in written_order(p._cached("audit:" + f, f, lambda: util.read_jsonl(f)))
                    if clean_id(str(e.get("who") or "")) == owner]
            return (any(e.get("action") in LEAD_ONLY for _, _, e in mine),
                    [(t, k, e) for t, k, e in mine if e.get("action") in (DRAWN, ENDED) and isinstance(e.get("details"), dict)])
        was_lead, got = p._cached("verifylog:" + f, f, load)
        rows += [(t, n, k, e, owner, was_lead) for t, k, e in got]
    leads = {lead} | {owner for *_, owner, was_lead in rows if was_lead}
    draw = None
    for t, n, k, e, owner, _ in sorted(rows, key=lambda x: x[:3]):
        det = e["details"]
        if owner not in leads or det.get("set") != set_name:
            continue
        if e["action"] == DRAWN and isinstance(det.get("draw"), dict):
            draw = {k_: v for k_, v in det["draw"].items() if k_ != "ended"}
        elif e["action"] == ENDED and draw is not None:
            draw["ended"] = {"at": e.get("t", ""), "by": str(e.get("who") or ""), "reason": str(det.get("reason") or "")}
    return draw


def verify_votes(vf: dict | None, V: dict) -> tuple[dict, bool]:
    """({record: its verifier's vote that counts}, whether the sample is finished) for a verification sample.
    A sample is finished at the moment the last of its records gets its first vote, or when the lead ends it as it
    stands, whichever comes first. Up to that moment the verifier sees nothing of what was decided before and may
    change any vote: the latest one counts. From that moment they can see how every record was decided, so a vote
    written later never counts: not for a record's outcome, not for the figures. (The server refuses such a vote.
    One can still arrive, from a computer that had not yet heard that the sample was finished or from a file edited
    by hand: this rule reads the files, so every computer gives the same answer whichever of them took the vote.)"""
    if not vf:
        return {}, False
    mine = V.get("vs", {})
    slots = {r: mine.get(r, {}).get(vf["reviewer"]) for r in vf["order"]}
    # the row that finished it: the first vote on the last record to get one, by the order the verifier's rows were
    # written in (not by their time stamps alone, which a clock put right can turn round)
    last = max(s["at"][0] for s in slots.values()) if all(slots.values()) else None
    # ended by the lead: on another computer, so only the two clocks can say which votes came first
    end = (util.ts(vf["ended"]["at"]) or float("inf")) if vf["ended"] else None     # (no readable time: all count)
    out = {}
    for r, s in slots.items():
        ok = [d for d, at in zip(s["all"], s["at"]) if (last is None or at <= last)
              and (end is None or util.ts(d.get("t")) <= end)] if s else []
        if ok:
            out[r] = ok[-1]
    return out, last is not None or end is not None


def verify_finished(vf: dict | None, V: dict) -> bool:
    """A verification sample is finished when its verifier has voted on every record of it, or the lead has ended
    it as it stands. Only then do its votes send any record on, and from then on they are fixed (verify_votes)."""
    return verify_votes(vf, V)[1]


def _prep(p: Project, set_name: str) -> tuple[dict, dict]:
    return p.memo(("prep", set_name), lambda: _prep_(p, set_name))


def verify_duty(p: Project, user: str) -> tuple | None:
    """(set, "open" or "lost") while the pages that show what became of records must stay closed to this person:
      open   they are screening a verification sample that is not finished;
      lost   the project holds votes of theirs on a verification sample that its state no longer holds (an older copy
             of the state was put back, or the entry was damaged). The pages stay closed until the lead has drawn the
             sample again (the same numbers give the same sample, and the votes count again), so that losing the entry
             is never a way to see what was decided before and then carry on voting.
    None otherwise."""
    return p.memo(("verify_duty", user), lambda: _verify_duty(p, user)) if user else None


def _verify_duty(p: Project, user: str) -> tuple | None:
    mine = [r for r, who in (votes(p).get("vs") or {}).items() if user in who]
    by = p.by_id() if mine else {}
    for set_name in SETS:
        sv, pl = _prep(p, set_name)
        vf = sv["_verify"]
        if vf:
            if vf["reviewer"] == user and sv["_verify_open"]:
                return set_name, "open"
        # no sample is held for this set, yet they have voted on one: whatever else the state holds or has lost
        # (a copy from before screening started has no order to look the records up in: the records say their set)
        elif any((by.get(r) or {}).get("set", "main") == set_name for r in mine):
            return set_name, "lost"
    return None


def _prep_(p: Project, set_name: str) -> tuple[dict, dict]:
    sv = dict(p.state_ro().get("sets", {}).get(set_name, {}) or {})
    sv["_order_set"] = set(sv.get("order", []))
    sv["_sample_set"] = set(sv.get("sample", []))
    pl = plan(p, set_name)
    # While the agreement sample is open, none of its records moves on (not even one both reviewers have voted
    # on), so that nothing on screen hints at the other reviewer's votes before the two are compared.
    # The verification sample (the tidy copy, or None), and whether it is finished: until it is, its votes change no
    # record's outcome, so that nothing its verifier (or anyone) can see moves as they vote.
    held, vf = verify_of(sv), verify_of({"verify": verify_logged(p, set_name)})
    same = bool(held and vf) and (held["order"], held["reviewer"]) == (vf["order"], vf["reviewer"])
    if vf is None:                                # the log holds no draw: the sample is what the state holds, if any
        vf = held
    elif same and held["ended"] and not vf["ended"]:
        vf["ended"] = held["ended"]               # (ended, the state says, and the log has lost that entry)
    # the sample in force is the log's and the state does not hold it (an older copy of the state was put back)
    sv["_verify_logged"] = vf is not None and held is not vf and not same
    sv["_verify"] = vf
    sv["_verify_votes"], done = verify_votes(vf, votes(p))      # (the votes that count: none written after it finished)
    sv["_verify_open"] = bool(vf) and not done
    sv["_verify_set"], sv["_verifier"] = (set(vf["sample"]), vf["reviewer"]) if done else (set(), "")
    sv["_sample_open"] = False
    if sv["_sample_set"] and sv.get("phase") in ("calibration", "sample"):
        V = votes(p)
        a, b = (pl["reviewers"] + ["", ""])[:2]
        sv["_sample_open"] = not all(latest(V, "ta", r, a) and latest(V, "ta", r, b) for r in sv["_sample_set"])
    return sv, pl


VERIFIED_ON = "verification sent it on"
ALONE = "single reviewer"               # how a record was excluded when one person's vote decided it


def ta_outcome(p: Project, rec: str, set_name: str, sv: dict, pl: dict, V: dict, R: dict, ai_keep: dict) -> dict:
    res = R.get((rec, "ta"))
    if res and res.get("d") in TA_CHOICES:
        return {"status": "forward" if res["d"] in FWD else "excluded", "how": "agreed together", "required": []}
    req = ta_required(sv, pl, rec, V, ai_keep)
    if not req:
        return {"status": "not_open", "how": "", "required": []}
    if sv.get("_sample_open") and rec in sv["_sample_set"]:
        return {"status": "pending", "how": "", "required": req, "missing": [], "note": "agreement sample still open"}
    got = {w: latest(V, "ta", rec, w) for w in req}
    fwd = [w for w, v in got.items() if v and v["d"] in FWD]
    missing = [w for w, v in got.items() if not v]
    if missing:                       # nobody learns another reviewer's vote before casting their own
        return {"status": "pending", "how": "", "required": req, "missing": missing}
    second = len(req) == 2 and pl["mode"] != "dual" and rec not in sv["_sample_set"] and (sv.get("remainder_mode") == "single")
    if fwd and (pl["conflicts"] == "forward" or second):
        how = "second look sent it on" if second and fwd == [pl["second_reviewer"]] else ("both" if len(fwd) == len(req) else "one reviewer sent it on")
        return {"status": "forward", "how": how, "required": req}
    if fwd and len(fwd) < len(req):
        return {"status": "conflict", "how": "", "required": req}
    if fwd:
        return {"status": "forward", "how": "both", "required": req}
    # excluded, and in the verification sample: sent on if its verifier would. Only a record that left on one person's
    # vote, and not the verifier's own (the draw takes no other; a file edited by hand must not make one count)
    if rec in sv.get("_verify_set", ()) and len(req) == 1 and sv["_verifier"] not in req:
        v = sv["_verify_votes"].get(rec)
        if v and v["d"] in FWD:
            return {"status": "forward", "how": VERIFIED_ON, "required": req}
    return {"status": "excluded", "how": "both excluded" if len(req) == 2 else ALONE, "required": req}


def ta_outcomes(p: Project, set_name: str) -> dict:
    """{record: outcome} for a set. Shared: do not change the result."""
    return p.memo(("ta_outcomes", set_name), lambda: _ta_outcomes(p, set_name))


def _ta_outcomes(p: Project, set_name: str) -> dict:
    sv, pl = _prep(p, set_name)
    V, R, ai_keep = votes(p), p.resolutions(), _ai_keep(p)
    return {rec: ta_outcome(p, rec, set_name, sv, pl, V, R, ai_keep) for rec in sv.get("order", [])}


# ================================================================================================ full text
def ft_people(pl: dict) -> list:
    """Who must assess every full text under a plan: both reviewers, or one (the person who screens alone, or the
    first reviewer). Nobody when nobody is chosen yet; a report is then not yet assessed (ft_outcome)."""
    R = named(pl)
    if pl.get("ft_mode", "dual") == "single":
        one = pl.get("single_reviewer") or (R[0] if R else "")
        return [one] if one else []
    return R or ([pl["single_reviewer"]] if pl.get("single_reviewer") else [])


def ft_required(p: Project, rec_set: str) -> list:
    return ft_people(plan(p, rec_set))


FT_STATUS = {"include": "included", "exclude": "excluded", "awaiting": "awaiting", "ongoing": "ongoing",
             "not_retrieved": "not_retrieved"}


def ft_outcome(rec: str, req: list, V: dict, R: dict) -> dict:
    res = R.get((rec, "ft"))
    if res and res.get("d") in FT_STATUS:
        return {"status": FT_STATUS[res["d"]], "reason": res.get("reason", ""),
                "study": res.get("study", ""), "how": "agreed together", "required": req}
    got = {w: latest(V, "ft", rec, w) for w in req}
    missing = [w for w, v in got.items() if not v]
    if missing or not req:             # nobody required (nobody chosen yet) is not an assessment: never "included"
        return {"status": "pending", "missing": missing, "required": req, "voted": [w for w in req if w not in missing]}
    how = "both" if len(req) > 1 else "one reviewer"
    ds = [v["d"] for v in got.values()]
    if all(d == "include" for d in ds):
        studies = [v.get("study", "") for v in got.values() if v.get("study")]
        out = {"status": "included", "study": studies[0] if studies else "", "how": how, "required": req}
        named_ = {w: v["study"].strip() for w, v in got.items() if (v.get("study") or "").strip()}
        if len({x.lower() for x in named_.values()}) > 1:          # two names: the first is used, so say so
            out["study_names"] = [{"by": w, "name": x} for w, x in named_.items()]
        return out
    if all(d == "exclude" for d in ds):
        reasons = {v.get("reason", "") for v in got.values()}
        if len(reasons) == 1:
            return {"status": "excluded", "reason": reasons.pop(), "how": how, "required": req}
        return {"status": "conflict", "kind": "different reasons", "required": req}
    if all(d == "not_retrieved" for d in ds):
        return {"status": "not_retrieved", "how": how, "required": req}
    if all(d == "awaiting" for d in ds):
        return {"status": "awaiting", "how": how, "required": req}
    if all(d == "ongoing" for d in ds):
        return {"status": "ongoing", "how": how, "required": req}
    return {"status": "conflict", "kind": "include or exclude" if {"include", "exclude"} <= set(ds) else "different decisions",
            "required": req}


def ft_outcomes(p: Project) -> dict:
    """{record: full-text outcome} for every record sent on to full text. Shared: do not change the result."""
    return p.memo("ft_outcomes", lambda: _ft_outcomes(p))


def _ft_outcomes(p: Project) -> dict:
    V, R = votes(p), p.resolutions()
    out = {}
    req = {set_name: ft_required(p, set_name) for set_name in SETS}
    for set_name in SETS:
        for rec, o in ta_outcomes(p, set_name).items():
            if o["status"] == "forward":
                out[rec] = ft_outcome(rec, req[set_name], V, R)
    return out


# ================================================================================================ tasks for one person
def tasks_for(p: Project, user: str) -> list:
    """The screening tasks open for this person, with what is left."""
    return [dict(t) for t in p.memo(("tasks", user), lambda: _tasks_for(p, user))]


def _tasks_for(p: Project, user: str) -> list:
    out = []
    V, R, ai_keep = votes(p), p.resolutions(), _ai_keep(p)
    for set_name in SETS:
        sv, pl = _prep(p, set_name)
        # A verification sample to screen (finished: its votes are fixed, no task). It is offered whatever phase the
        # state says the set is in: a state put back from an earlier point does not take the sample away (the log
        # holds it), and its verifier, who is closed out of other pages until they finish, must be able to finish.
        vf, vtask = sv["_verify"], None
        if vf and vf["reviewer"] == user and sv["_verify_open"] and vf["order"][0] in p.by_id():
            left = [r for r in vf["order"] if r not in sv["_verify_votes"]]
            vtask = {"task": "verify", "set": set_name, "label": "Verification sample", "total": len(vf["order"]),
                     "left": len(left), "next": left[:1]}
        if sv.get("phase") in (None, "not_started"):
            if vtask:
                out.append(vtask)
            continue
        order = sv.get("order", [])
        if sv.get("phase") == "calibration" and user in pl["reviewers"] and not _stuck(p, sv, pl):
            cal = sv["calibration_rounds"][-1]
            left = [r for r in cal if not latest(V, "cal", r, user)]
            out.append({"task": "calibration", "set": set_name, "label": f"Calibration round {len(sv['calibration_rounds'])}",
                        "total": len(cal), "left": len(left), "next": left[:1]})
        sample = sv.get("sample", [])
        if sample and sv.get("phase") in ("sample", "screening") and user in pl["reviewers"]:
            left = [r for r in sample if not latest(V, "ta", r, user)]
            out.append({"task": "sample", "set": set_name, "label": "Agreement sample", "total": len(sample),
                        "left": len(left), "next": left[:1]})
        if sv.get("phase") == "screening":
            mine, second = [], []
            for r in order:
                if r in sv["_sample_set"]:
                    continue
                req = ta_required(sv, pl, r, V, ai_keep)
                if user not in req:
                    continue
                if len(req) == 2 and (sv.get("remainder_mode") or pl["mode"]) == "single":
                    if user == pl["second_reviewer"]:
                        second.append(r)
                    else:
                        mine.append(r)
                else:
                    mine.append(r)
            if mine:
                left = [r for r in mine if not latest(V, "ta", r, user) and (r, "ta") not in R]
                label = "Title and abstract screening"          # the set is named beside it
                out.append({"task": "screen", "set": set_name, "label": label, "total": len(mine), "left": len(left), "next": left[:1]})
            if second:
                left = [r for r in second if not latest(V, "ta", r, user) and (r, "ta") not in R]
                out.append({"task": "second", "set": set_name, "label": "Second look (records the AI would keep)",
                            "total": len(second), "left": len(left), "next": left[:1]})
        if vtask:
            out.append(vtask)
    # full text (not while this person is screening a verification sample: the full-text pages are closed to them
    # until it is finished, because the records mixed into the sample are listed there: evaluation.blocked)
    verifying = bool(verify_duty(p, user))
    fto = ft_outcomes(p)
    mine = [r for r, o in fto.items() if user in o.get("required", [])] if not verifying else []
    if mine:
        left = [r for r in mine if not latest(V, "ft", r, user) and (r, "ft") not in R]
        out.append({"task": "fulltext", "set": "all", "label": "Full-text review", "total": len(mine), "left": len(left),
                    "next": left[:1]})
    return out


def queue(p: Project, user: str, task: str, set_name: str) -> list:
    """Record IDs for this person and task, in screening order, each marked done or not. The screening room
    opens at the first one not done, so going back (left arrow) reaches the earlier votes, even after a reload."""
    return [dict(x) for x in p.memo(("queue", user, task, set_name), lambda: _queue(p, user, task, set_name))]


def _queue(p: Project, user: str, task: str, set_name: str) -> list:
    V, R, ai_keep = votes(p), p.resolutions(), _ai_keep(p)
    if task == "fulltext":
        recs = sorted(r for r, o in ft_outcomes(p).items() if user in o.get("required", []))
        stage = "ft"
    else:
        sv, pl = _prep(p, set_name)
        stage = "cal" if task == "calibration" else "ta"
        if task == "verify":                    # only its verifier has this queue; nothing but their own votes marks it
            vf = sv["_verify"]
            recs = vf["order"] if vf and vf["reviewer"] == user and sv["_verify_open"] and vf["order"][0] in p.by_id() else []
            return [{"id": r, "done": r in sv["_verify_votes"]} for r in recs]
        if task == "calibration":
            recs = sv["calibration_rounds"][-1] if sv.get("calibration_rounds") else []
        elif task == "sample":
            recs = sv.get("sample", [])
        else:
            recs = []
            for r in sv.get("order", []):
                if r in sv["_sample_set"]:
                    continue
                req = ta_required(sv, pl, r, V, ai_keep)
                if user not in req:
                    continue
                is_second = len(req) == 2 and (sv.get("remainder_mode") or pl["mode"]) == "single" and user == pl["second_reviewer"]
                if (task == "second") == is_second:
                    recs.append(r)
    st = "ft" if stage == "ft" else "ta"
    return [{"id": r, "done": bool(latest(V, stage, r, user)) or (stage != "cal" and (r, st) in R)} for r in recs]


# ================================================================================================ agreement
def _counted(x: dict, cut: float, slip: bool) -> dict:
    """The vote of one reviewer on one record that counts for agreement (see the module notes). x: votes() slot; cut:
    the time rule's moment, for rows without a mark; slip: this reviewer voted second outside a round compared as a
    whole, so may correct a slip within SLIP_SECONDS of their own first vote."""
    t0 = util.ts(x["first"].get("t"))
    got, opened = None, False
    for d in x["all"]:
        c = _cmp(d)
        if c is None:
            ok = not opened and util.ts(d.get("t")) <= cut
        elif c < 2:
            ok = not opened
        else:
            ok = slip and not opened and util.ts(d.get("t")) - t0 <= SLIP_SECONDS
            opened = not ok
        if ok:
            got = d
    return got or x["first"]


def independent(V: dict, stage: str, recs: list, a: str, b: str, whole: bool) -> dict:
    """{record: (a's vote, b's vote)} that count for agreement, for the records both have voted on. whole: the records
    are compared as a whole (a calibration round, the agreement sample), else record by record (see the module notes)."""
    S = V.get(stage, {})
    both = [r for r in recs if a in S.get(r, {}) and b in S.get(r, {})]
    if whole:
        t_all = max((util.ts(S[r][w]["first"].get("t")) for r in both for w in (a, b)), default=0.0)
    out = {}
    for r in both:
        xa, xb = S[r][a], S[r][b]
        ta, tb = util.ts(xa["first"].get("t")), util.ts(xb["first"].get("t"))
        if whole:
            out[r] = (_counted(xa, t_all, False), _counted(xb, t_all, False))
            continue
        # outside a whole round: the first to vote may change their vote until the other votes; the second to vote may
        # correct a slip within SLIP_SECONDS of their own vote. A marked first vote says who voted second (1); an
        # earlier version's, the time stamps (as before).
        ca, cb = _cmp(xa["first"]), _cmp(xb["first"])
        sa = ca == 1 if ca is not None else ta >= tb
        sb = cb == 1 if cb is not None else tb > ta
        second = max(ta, tb)
        out[r] = (_counted(xa, second + SLIP_SECONDS if ta >= tb else second, sa),
                  _counted(xb, second + SLIP_SECONDS if tb > ta else second, sb))
    return out


def agreement(p: Project, set_name: str, scope: str) -> dict:
    """scope: calibration (latest round) | sample | remainder | fulltext"""
    V = votes(p)
    by = p.by_id()
    if scope == "fulltext":
        sv, pl = _prep(p, "main")
        recs = list(ft_outcomes(p))
        stage, a, b = "ft", (pl["reviewers"] + ["", ""])[0], (pl["reviewers"] + ["", ""])[1]
    else:
        sv, pl = _prep(p, set_name)
        a, b = (pl["reviewers"] + ["", ""])[:2]
        if scope == "calibration":
            recs, stage = (sv.get("calibration_rounds") or [[]])[-1], "cal"
        elif scope == "sample":
            recs, stage = sv.get("sample", []), "ta"
        else:
            recs, stage = [r for r in sv.get("order", []) if r not in sv["_sample_set"]], "ta"
    out = {"scope": scope, "set": set_name, "reviewers": [a, b], "records": len(recs)}
    both = [r for r in recs if first(V, stage, r, a) and first(V, stage, r, b)]
    out["done_a"] = sum(1 for r in recs if first(V, stage, r, a))
    out["done_b"] = sum(1 for r in recs if first(V, stage, r, b))
    out["both_done"] = len(both)
    out["complete"] = bool(recs) and len(both) == len(recs)
    ind = independent(V, stage, both, a, b, scope in ("calibration", "sample"))
    whole = scope in ("calibration", "sample")
    out["changed_before_compare"] = sum(1 for r in both for w, v in zip((a, b), ind[r]) if v is not first(V, stage, r, w))
    if stage == "ft":
        usable = [r for r in both if ind[r][0]["d"] in ("include", "exclude") and ind[r][1]["d"] in ("include", "exclude")]
        pairs = [(ind[r][0]["d"] == "include", ind[r][1]["d"] == "include") for r in usable]
        if out["complete"]:                  # until both have finished, this would hint at the other's votes
            out["left_out"] = len(both) - len(usable)
        out["note"] = ("Include versus exclude, on the reports from every record set that both reviewers voted to include or "
                       "exclude (a report either of them marked as awaiting classification, ongoing or not retrieved is left "
                       "out), using each reviewer's vote before they could see the other's (a slip corrected within two "
                       "minutes counts as corrected).")
    else:
        usable = both
        pairs = [(ind[r][0]["d"] in FWD, ind[r][1]["d"] in FWD) for r in both]
        out["note"] = ("Goes forward (Include or Maybe) versus Exclude, using each reviewer's own vote made before the "
                       "two were compared" + (" (a slip corrected before then counts as corrected)." if whole else
                                              " (a slip corrected within two minutes counts as corrected)."))
    out["stats"] = stats.kappa(stats.two_by_two(pairs))
    dis = []
    for r in usable:
        fa, fb = ind[r]
        ya = fa["d"] in FWD if stage != "ft" else fa["d"] == "include"
        yb = fb["d"] in FWD if stage != "ft" else fb["d"] == "include"
        if ya != yb or (stage == "ft" and fa["d"] == fb["d"] == "exclude" and fa.get("reason") != fb.get("reason")):
            dis.append({"id": r, "title": by.get(r, {}).get("title", ""), a: {k: fa.get(k, "") for k in ("d", "reason", "note")},
                        b: {k: fb.get(k, "") for k in ("d", "reason", "note")}})
    out["disagreements"] = dis
    if scope == "sample":
        out["threshold"] = pl["kappa_threshold"]
        k = out["stats"].get("kappa")
        out["meets_threshold"] = stats.reaches(k, pl["kappa_threshold"])
        # as the page prints them side by side: kappa with enough decimals to show which side of the threshold it is
        # on (kept with the figures, which nobody is sent before both reviewers have finished)
        out["stats"]["kappa_shown"] = stats.fmt_kappa(k, pl["kappa_threshold"])
        out["threshold_shown"] = stats.fmt_threshold(pl["kappa_threshold"])
    return out


# ================================================================================================ moving on
def _stuck(p: Project, sv: dict, pl: dict) -> str:
    """Why a set's calibration round can never be finished ("" when it can, or when there is none open): the round
    compares two reviewers, and the plan in force names fewer than two who are in the project. ReviewDesk 1.3.0 could
    start such a round (a one-reviewer plan with a number of calibration records); nothing later can."""
    if sv.get("phase") != "calibration":
        return ""
    R = [x for x in named(pl) if p.reviewer(x)]
    if len(R) >= 2:
        return ""
    who = f"only {(p.reviewer(R[0]) or {}).get('name') or R[0]}" if R else "no reviewer"
    return ("This calibration round cannot be finished: a calibration round compares two reviewers who screen the same "
            f"records, and the screening plan fixed when screening started names {who}.")


def calibration_stuck(p: Project, set_name: str = "main") -> str:
    """_stuck for a record set: "" or why its calibration round can never be finished."""
    sv = p.state_ro().get("sets", {}).get(set_name) or {}
    return _stuck(p, sv, plan(p, set_name)) if sv.get("phase") == "calibration" else ""


END_STUCK = ("End the round: the project lead chooses 'End the calibration round' (Project set-up, Screening plan). "
             "Screening then goes on with the plan as it is; the calibration records are screened with the others, and "
             "the practice votes are kept but never counted.")


def finish_calibration(p: Project, user: str, set_name: str = "main", another_round: bool = False) -> dict:
    """The lead moves a set on from its calibration round, once both reviewers have finished it (or draws another
    round). A round that can never be finished (see _stuck) is ended instead: the same move on, recorded in the audit
    log as ended without a comparison, and never reported as a calibration round."""
    p.require_lead(user)
    with p.lock:
        st = p.state()
        sv = st["sets"][set_name]
        if sv.get("phase") != "calibration":
            raise ProjectError("This set is not in calibration.")
        pl = plan(p, set_name)
        why = _stuck(p, sv, pl)
        if why:
            if another_round:
                raise ProjectError(why + " Another round would be the same. " + END_STUCK)
            V = votes(p)
            cal = sv["calibration_rounds"][-1] if sv.get("calibration_rounds") else []
            cast = {w: sum(1 for r in cal if latest(V, "cal", r, w)) for w in sorted({w for r in cal for w in V.get("cal", {}).get(r, {})})}
            sv.setdefault("calibration_ended", []).append({"round": len(sv.get("calibration_rounds") or []), "records": len(cal),
                                                          "ended": util.now(), "by": user, "why": why, "votes": cast})
            sv["phase"] = "sample" if pl["mode"] == "sample_then_single" else "screening"
            p.save_state(st, user)
            p.audit(user, "end calibration round that could not be finished",
                    {"set": set_name, "round": len(sv.get("calibration_rounds") or []), "records": len(cal),
                     "reason": why, "practice votes kept, not counted": cast, "goes on to": sv["phase"]})
            return sv
        ag = agreement(p, set_name, "calibration")
        if not ag["complete"]:
            raise ProjectError(f"Both reviewers must finish the calibration records first ({ag['done_a']} and "
                               f"{ag['done_b']} of {ag['records']} done).")
        if another_round:
            used = {r for rnd in sv["calibration_rounds"] for r in rnd} | set(sv.get("sample", []))
            pool = [r for r in reversed(sv["order"]) if r not in used]
            n = min(pl["calibration_size"], len(pool))
            if not n:
                raise ProjectError("There are no unused records left for another calibration round.")
            sv["calibration_rounds"].append(pool[:n])
            p.save_state(st, user)
            p.audit(user, "another calibration round", {"set": set_name, "records": n})
            return sv
        gate = pl["calibration_gate"]
        if gate and (ag["stats"]["agreement"] or 0) < gate:
            raise ProjectError(f"Agreement on calibration was {stats.fmt_pct(ag['stats']['agreement'])}, below the "
                               f"{stats.fmt_pct(gate, 0)} the plan requires. Discuss the disagreements, then start another round.")
        sv.setdefault("calibration_results", []).append({k: ag["stats"].get(k) for k in ("n", "agreement", "kappa", "ci_low", "ci_high", "ci_note")})
        sv["phase"] = "sample" if pl["mode"] == "sample_then_single" else "screening"
        p.save_state(st, user)
        p.audit(user, "finish calibration", {"set": set_name, "agreement": ag["stats"]["agreement"], "kappa": ag["stats"]["kappa"]})
        return sv


def ai_planned_not_sent(n: int) -> str:
    """What stops the agreement rule being applied while the AI has records left (the same words in the checks)."""
    return (f"The AI second screener is planned but {util.plural(n, 'record')} in this set {'has' if n == 1 else 'have'} "
            "not been sent to the AI yet. Run the AI screening to the end (AI second screener page) first. (Records the "
            "AI could not answer count as kept, so they do not hold this up.)")


def ai_not_sent(n: int) -> str:
    """'1 record in this set has not been sent to the AI second screener yet.'"""
    return (f"{util.plural(n, 'record')} in this set {'has' if n == 1 else 'have'} not been sent to the AI second "
            "screener yet.")


def apply_sample_decision(p: Project, user: str, set_name: str = "main", drop_ai: str = "") -> dict:
    """Apply the pre-specified rule to the agreement sample: single screening if kappa reaches the threshold,
    otherwise both screen everything; then, if the AI second screener is planned, the keep-rate stopping rule."""
    p.require_lead(user)
    from . import ai
    with p.lock:
        st = p.state()
        sv = st["sets"][set_name]
        if sv.get("phase") != "sample":
            raise ProjectError("The agreement sample is not open for a decision.")
        ag = agreement(p, set_name, "sample")
        pl = plan(p, set_name)
        if not ag["complete"]:
            raise ProjectError(f"Both reviewers must finish the agreement sample first ({ag['done_a']} and {ag['done_b']} "
                               f"of {ag['records']} done).")
        k = ag["stats"].get("kappa")
        result = {"n": ag["stats"]["n"], "agreement": ag["stats"]["agreement"], "kappa": k, "ci_low": ag["stats"].get("ci_low"),
                  "ci_high": ag["stats"].get("ci_high"), "ci_note": ag["stats"].get("ci_note"), "note": ag["stats"].get("note"), "threshold": pl["kappa_threshold"], "table": {x: ag["stats"][x] for x in
                  ("both_yes", "only_a", "only_b", "both_no")}, "reviewers": ag["reviewers"], "decided": util.now(), "by": user}
        single = stats.reaches(k, pl["kappa_threshold"])
        result["kappa_rule"] = ("kappa reached the threshold, so single screening was allowed" if single else
                                "kappa was below the threshold" if k is not None else "kappa could not be calculated")
        net = False
        cov = ai.coverage(p, set_name) if pl["ai_second_screener"] else None
        if pl["ai_second_screener"] and not cov["missing"] and not drop_ai.strip():
            result["ai"] = ai.validate(p, set_name)          # reported whatever the kappa
        if single and pl["ai_second_screener"] and drop_ai.strip():
            result["ai_rule"] = (f"the AI step could not be completed ({drop_ai.strip()}), so it was dropped and both "
                                 "reviewers screened every record")
            result["ai_rule_kind"] = "dropped"
            single = False
        elif not single and pl["ai_second_screener"]:
            result["ai_rule"] = "the AI step was not needed, because both reviewers screened every record"
            result["ai_rule_kind"] = "not_needed"
        elif single and pl["ai_second_screener"]:
            if cov["missing"]:
                raise ProjectError(ai_planned_not_sent(cov["missing"]))
            val = result["ai"]
            if val["keep_rate"] is not None and val["keep_rate"] > pl["ai_keep_rate_stop"]:
                result["ai_rule"] = (f"the AI kept {stats.fmt_pct(val['keep_rate'])} of records, above the "
                                     f"{stats.fmt_pct(pl['ai_keep_rate_stop'], 0)} limit, so the AI step was dropped and "
                                     "both reviewers screened every record")
                result["ai_rule_kind"] = "stopping_rule"
                single = False
            else:
                net = True
                result["ai_rule_kind"] = "net"
                result["ai_rule"] = (f"the AI kept {stats.fmt_pct(val['keep_rate'])} of records, within the "
                                     f"{stats.fmt_pct(pl['ai_keep_rate_stop'], 0)} limit, so every record the single reviewer "
                                     "excluded but the AI would keep went to the second reviewer")
        if pl["ai_second_screener"] and result.get("ai") and not ai.config(p).get("locked"):
            ai.save_config(p, user, {"locked": True, "locked_at": util.now()})   # the set-up whose answers were used
        names = {r["id"]: r["name"] for r in p.reviewers()}
        result["remainder"] = (f"{names.get(pl['single_reviewer'], pl['single_reviewer'])} screened the remaining records alone"
                               + (f", and every record they excluded that the AI would keep went to "
                                  f"{names.get(pl['second_reviewer'], pl['second_reviewer'])}" if net else "")
                               if single else "both reviewers screened every remaining record")
        sv["sample_result"] = result
        sv["remainder_mode"] = "single" if single else "dual"
        sv["ai_net_active"] = net
        sv["phase"] = "screening"
        p.save_state(st, user)
        p.audit(user, "apply sample decision", {"set": set_name, "kappa": k, "remainder": sv["remainder_mode"], "ai_net": net})
        return sv


def sample_recount(p: Project, set_name: str = "main") -> dict | None:
    """When the agreement rule has been applied and the independent votes now on file give another kappa than the one
    recorded (votes cast before the rule was applied arrived afterwards from another computer): {"recorded", "now",
    "recorded_shown", "now_shown", "threshold", "reaches_now", "remainder"}; otherwise None. The recorded figure and
    the rule's outcome are never changed: they are what the lead saw and applied."""
    def work():
        sv = p.state_ro().get("sets", {}).get(set_name) or {}
        sr = sv.get("sample_result")
        if not isinstance(sr, dict) or sv.get("phase") != "screening":
            return None
        ag = agreement(p, set_name, "sample")
        if not ag["complete"]:                  # (a file that has not arrived: the data checks say so)
            return None
        thr = sr.get("threshold")
        then, now = sr.get("kappa"), ag["stats"].get("kappa")
        shown = (stats.fmt_kappa(then, thr), stats.fmt_kappa(now, thr))
        if shown[0] == shown[1] and ag["stats"].get("n") == sr.get("n"):
            return None
        return {"recorded": then, "now": now, "recorded_shown": shown[0], "now_shown": shown[1], "threshold": thr,
                "reaches_now": stats.reaches(now, thr), "remainder": sv.get("remainder_mode")}
    return p.memo(("sample_recount", set_name), work)


def _k(k, shown: str) -> str:
    return shown if k is not None else "not calculable"


def sample_recount_check(p: Project, set_name: str = "main") -> str:
    """The lead's check on the Overview when sample_recount finds another figure: "" or what to know and do."""
    rc = sample_recount(p, set_name)
    if not rc:
        return ""
    where = "" if set_name == "main" else f" from the {SETS.get(set_name, set_name).lower()}"
    side = ""
    if rc["now"] is not None and rc["threshold"] is not None:
        side = (f" ({'at or above' if rc['reaches_now'] else 'below'} the threshold of "
                f"{stats.fmt_threshold(rc['threshold'])})")
    outcome = ("one reviewer screens the remaining records" if rc["remainder"] == "single"
               else "both reviewers screen every remaining record")
    return (f"Kappa on the agreement sample{where} now differs from the figure recorded when the agreement rule was "
            f"applied: the independent votes on file give {_k(rc['now'], rc['now_shown'])}{side}, and the figure recorded "
            f"is {_k(rc['recorded'], rc['recorded_shown'])}. "
            "This happens when votes cast before the rule was applied arrive afterwards from another computer (for "
            f"example a laptop that was offline). The rule's outcome stands as recorded: {outcome}. Report both figures "
            "in the methods, or discuss with the team what to do.")


def sample_recount_methods(p: Project, set_name: str = "main") -> str:
    """The methods sentence when sample_recount finds another figure: "" or one sentence."""
    rc = sample_recount(p, set_name)
    if not rc:
        return ""
    return (f"The agreement rule was applied to the votes on file at the time, which gave a kappa of "
            f"{_k(rc['recorded'], rc['recorded_shown'])}; votes cast before then that arrived afterwards from another "
            f"reviewer's computer give a kappa of {_k(rc['now'], rc['now_shown'])} for the same sample. The rule's "
            "outcome stands as it was applied.")


# ================================================================================================ conflicts
def conflicts(p: Project) -> list:
    """Records waiting to be decided together. Shared: do not change the result."""
    return p.memo("conflicts", lambda: _conflicts(p))


def _conflicts(p: Project) -> list:
    out = []
    by = p.by_id()
    V = votes(p)
    for set_name in SETS:
        for rec, o in ta_outcomes(p, set_name).items():
            if o["status"] == "conflict":
                out.append({"id": rec, "stage": "ta", "set": set_name, "title": (by.get(rec) or {}).get("title", rec),
                            "votes": {w: without_cmp(latest(V, "ta", rec, w)) for w in o["required"]}})
    for rec, o in ft_outcomes(p).items():
        if o["status"] == "conflict":
            out.append({"id": rec, "stage": "ft", "set": (by.get(rec) or {}).get("set", ""),
                        "title": (by.get(rec) or {}).get("title", rec), "kind": o.get("kind"),
                        "votes": {w: without_cmp(latest(V, "ft", rec, w)) for w in o["required"]}})
    return out


# ================================================================================================ overview
def overview(p: Project) -> dict:
    return copy.deepcopy(p.memo("overview", lambda: _overview(p)))


def _overview(p: Project) -> dict:
    by = p.by_id()
    st = p.state_ro()
    out = {"sets": {}}
    for set_name in SETS:
        n = sum(1 for r in by.values() if r["set"] == set_name)
        sv = st.get("sets", {}).get(set_name, {})
        if not n and not sv:
            continue
        o = ta_outcomes(p, set_name) if sv.get("order") else {}
        counts = {}
        for x in o.values():
            counts[x["status"]] = counts.get(x["status"], 0) + 1
        pl = plan(p, set_name)
        sr = sv.get("sample_result")
        if sr:                                  # kappa and its threshold as they are printed side by side
            sr = dict(sr, kappa_shown=stats.fmt_kappa(sr.get("kappa"), sr.get("threshold")),
                      threshold_shown=stats.fmt_threshold(sr.get("threshold")))
        out["sets"][set_name] = {"label": SETS[set_name], "records": n, "phase": sv.get("phase", "not_started"),
                                 "counts": counts, "remainder_mode": sv.get("remainder_mode"),
                                 "ai_net_active": sv.get("ai_net_active", False), "sample": len(sv.get("sample", [])),
                                 "sample_result": sr, "plan": dict(pl, threshold_shown=stats.fmt_threshold(pl["kappa_threshold"])),
                                 "calibration_rounds": len(sv.get("calibration_rounds", [])),
                                 "calibration_stuck": _stuck(p, sv, pl)}
    fto = ft_outcomes(p)
    fc = {}
    for x in fto.values():
        fc[x["status"]] = fc.get(x["status"], 0) + 1
    out["fulltext"] = {"records": len(fto), "counts": fc}
    out["conflicts"] = len(conflicts(p))
    return out


def statuses_blind_to_ai(p: Project, set_name: str) -> dict:
    """{record: status} for a set as a reviewer may see it while the AI second screener is behind single screening:
    a record the single reviewer excluded that waits for its second look shows as excluded."""
    def work():
        sv, pl = _prep(p, set_name)
        out = {}
        for rec, o in ta_outcomes(p, set_name).items():
            st = o["status"]
            if st == "pending" and len(o.get("required", [])) == 2 and o.get("missing") == [pl["second_reviewer"]] \
                    and rec not in sv["_sample_set"] and sv.get("remainder_mode") == "single" and sv.get("ai_net_active"):
                st = "excluded"
            out[rec] = st
        return out
    return p.memo(("blind_status", set_name), work)


def counts_blind_to_ai(p: Project, set_name: str) -> dict:
    """The set's counts as a reviewer may see them while the AI second screener is behind single screening: a record
    the single reviewer excluded that waits for its second look is counted as excluded, so the counts cannot show
    them which of their exclusions the AI would keep."""
    def work():
        sv, pl = _prep(p, set_name)
        out: dict = {}
        for rec, o in ta_outcomes(p, set_name).items():
            st = o["status"]
            if st == "pending" and len(o.get("required", [])) == 2 and o.get("missing") == [pl["second_reviewer"]] \
                    and rec not in sv["_sample_set"] and sv.get("remainder_mode") == "single":
                st = "excluded"
            out[st] = out.get(st, 0) + 1
        return out
    return dict(p.memo(("blind_counts", set_name), work))


def half_voted(p: Project) -> int:
    """Records two reviewers screen independently on which one has voted and the other has not. While there are
    any, files listing every vote would show a reviewer the other's vote first."""
    def work():
        n = 0
        for set_name in SETS:
            sv, pl = _prep(p, set_name)
            for rec, o in ta_outcomes(p, set_name).items():
                if o["status"] == "pending" and len(o.get("required", [])) == 2 and len(o.get("missing", [])) == 1:
                    second = pl["mode"] != "dual" and rec not in sv["_sample_set"] and sv.get("remainder_mode") == "single"
                    n += not second        # a second look is not blind by design
                elif o["status"] == "pending" and o.get("note"):
                    n += 1                 # the agreement sample is still open
        n += sum(1 for o in ft_outcomes(p).values() if o["status"] == "pending" and len(o.get("required", [])) == 2
                 and len(o.get("missing", [])) == 1)
        return n
    return p.memo("half_voted", work)


def second_look_stats(p: Project, set_name: str = "main") -> dict:
    """How many records went to a second look (excluded by the single reviewer, kept by the AI), how many the second
    reviewer sent to full text, and how many of those were included."""
    sv, pl = _prep(p, set_name)
    if sv.get("remainder_mode") != "single" or not sv.get("ai_net_active"):
        return {"total": 0, "forward": 0, "included": 0, "pending": 0}
    V, R, ai_keep = votes(p), p.resolutions(), _ai_keep(p)
    fto = ft_outcomes(p)
    total = fwd = inc = pend = 0
    for rec in sv.get("order", []):
        if rec in sv["_sample_set"]:
            continue
        req = ta_required(sv, pl, rec, V, ai_keep)
        if len(req) == 2:
            total += 1
            o = ta_outcome(p, rec, set_name, sv, pl, V, R, ai_keep)
            if o["status"] == "forward":
                fwd += 1
                inc += fto.get(rec, {}).get("status") == "included"
            elif o["status"] == "pending":
                pend += 1
    return {"total": total, "forward": fwd, "included": inc, "pending": pend}

