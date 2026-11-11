"""The risk-of-bias plan: for each study design the review includes, which tool is used.

The protocol fixes it before the work begins, so that the choice of tool cannot follow the results. In ReviewDesk the
project lead records it in set-up, and it is needed before screening starts (as the eligibility criteria are). A
project whose screening started before ReviewDesk asked for a plan needs it before any assessment is added; the methods
text then says when it was recorded and asks the authors to confirm it matches the registered protocol. Once screening
has started, only the lead can change it, with a reason: each change is a protocol amendment, kept with the
criteria's amendments (settings "amendments", PRISMA 2020 item 24c), in the audit log, and in the methods text.

Reviewers do not choose a tool. They choose the study's design from the plan and the tool follows; a design that is not
in the plan cannot be assessed until the lead adds it.

Stored in the project settings (written by the lead only) as "risk_of_bias_plan":
  {"designs": [{"id", "name", "tool" (a tool key, or "" for no assessment), "none": bool, "reason"}],
   "tools": {key: a lead-defined tool (robtools.check_custom)},
   "recorded", "recorded_by", "before_screening": bool, "rev", "saved", "saved_by"}
Projects made before 1.3.0 kept a list of the tools they expected to use ("risk_of_bias_tools", "risk_of_bias_other");
those are never changed or removed, and a draft plan is made from them for the lead to check and record.
"""
from __future__ import annotations

import copy
import re

from . import integrity, robtools, util, versions
from .project import MARKER, Project, ProjectError

KEY = "risk_of_bias_plan"
NAMES = {d: n for d, n, _ in robtools.STANDARD_DESIGNS}
RECOMMENDED = {d: t for d, _, t in robtools.STANDARD_DESIGNS}

# Words in an extracted study design, and the designs of the plan they fit (most specific first). The first rule that
# matches decides; an extracted design that fits none of them is not compared.
DESIGN_WORDS = [
    (r"cluster.*cross-?over|cross-?over.*cluster", []),        # cluster crossover: no built-in tool covers it
    (r"cluster", ["cluster_rct"]),
    (r"cross-?over", ["crossover_rct"]),
    (r"time[ -]?series|\bits\b", ["its"]),
    (r"controlled before|before[- ]?(and[- ]?)?after[^.]*control", ["nrsi"]),
    (r"before[- ]?(and[- ]?)?after|pre[- ]?post|uncontrolled", ["before_after", "nrsi"]),
    (r"prognostic factor", ["prognostic_factor"]),
    (r"predict|model (development|validation)|develop\w* (a |an )?model|validat\w* (a |an )?model", ["prediction"]),
    (r"diagnostic|test accuracy|accuracy", ["dta", "prediction"]),
    (r"exposure", ["nrse"]),
    (r"case-?control|cross-?sectional", ["dta", "prediction"]),  # not follow-up studies: not ROBINS-I or ROBINS-E
    (r"non-?random|quasi|comparison group|concurrent control|controlled", ["nrsi", "nrse"]),
    (r"random", ["rct", "crossover_rct"]),
    (r"cohort|observational|registry", ["nrsi", "nrse", "prognostic_factor", "dta", "prediction"]),
]
BROAD = (r"case-?control|cross-?sectional", r"cohort|observational|registry")   # these hint at a design, no more
TRIALS = ("cluster_rct", "crossover_rct", "rct")
FOLLOW_UP_TOOLS = ("robins2", "robins_ba", "robins_e")     # for follow-up (cohort-type) studies only
RCT_TOOLS = ("rob2", "rob2_cluster", "rob2_crossover")


def _mid(name: str) -> str:
    """A design's name inside a sentence: its first letter in lower case unless the first word is an acronym."""
    w = (name or "").split(" ", 1)[0]
    return name[:1].lower() + name[1:] if w[:1].isupper() and w[1:2].islower() else name


def _clean(s, n: int = 200) -> str:
    return " ".join(str(s or "").split())[:n]


# ================================================================================================ reading the plan
def stored(s: dict) -> dict:
    return s.get(KEY) if isinstance(s.get(KEY), dict) else {}


def plan(p: Project) -> dict:
    """The plan as recorded, or (not recorded yet) a draft: the one from before ReviewDesk 1.3.0 if the project named
    its tools then, otherwise empty. Shared: do not change the result."""
    s = p.settings_ro()
    pl = stored(s)
    if pl.get("recorded"):
        return {**pl, "draft": False}
    if pl.get("designs"):
        return {**pl, "draft": True}
    return {**draft_from_legacy(s), "draft": True}


def recorded(p: Project) -> bool:
    return bool(stored(p.settings_ro()).get("recorded"))


def draft_from_legacy(s: dict) -> dict:
    """A draft plan made from the tools a project named before 1.3.0 (they are kept as they were)."""
    tools = [t for t in s.get("risk_of_bias_tools") or [] if isinstance(t, str)]
    other = _clean(s.get("risk_of_bias_other"), 400)
    want = []
    for t in tools:
        want += {"rob2": ["rct", "cluster_rct"], "robins2": ["nrsi"], "robins_ba": ["before_after", "its"],
                 "probast_ai": ["prediction"]}.get(t, [])
    low = other.lower()
    for words, d in (("quadas", "dta"), ("robins-e", "nrse"), ("robins e", "nrse"), ("quips", "prognostic_factor"),
                     ("crossover", "crossover_rct"), ("cross-over", "crossover_rct"), ("cluster", "cluster_rct")):
        if words in low:
            want.append(d)
    designs = [{"id": d, "name": NAMES[d], "tool": RECOMMENDED[d], "none": False, "reason": ""}
               for d in robtools.STANDARD_IDS if d in want]
    out = {"designs": designs, "tools": {}}
    if tools or other:
        out["legacy"] = {"tools": [robtools.TOOLS[t]["name"] for t in tools if t in robtools.TOOLS], "other": other}
    return out


def draft_from_use(p: Project) -> dict:
    """A draft plan for a project whose assessments were added before there was a plan (it was begun with ReviewDesk
    1.2 or earlier, where the reviewer chose the tool): the standard designs whose usual tool those assessments
    already use. {} when there are none. Only the plan's own page offers it (public_plan), for the lead to check against
    the protocol and record: until then no check, output or assessment treats these designs as the plan."""
    used = _used_tools(p)
    designs = [{"id": d, "name": NAMES[d], "tool": RECOMMENDED[d], "none": False, "reason": ""}
               for d in robtools.STANDARD_IDS if used.get(RECOMMENDED[d])]
    if not designs:
        return {}
    return {"designs": designs, "tools": {},
            "legacy": {"tools": [robtools.TOOLS[t]["name"] for t in robtools.TOOLS if used.get(t)], "other": "", "in_use": True}}


def tools(p: Project) -> dict:
    """{key: tool}: the built-in tools and this project's lead-defined ones, in one shape."""
    out = dict(robtools.TOOLS)
    for k, d in (stored(p.settings_ro()).get("tools") or {}).items():
        if isinstance(d, dict):
            out[k] = robtools.custom_runtime(k, d)
    return out


def tool_def(p: Project, key: str) -> dict:
    """A tool by its key. A tool that is no longer known (its definition was lost) still shows what was recorded."""
    t = tools(p).get(key)
    if t:
        return t
    return {"name": f"Unknown tool ({key})", "short": key, "scale": [], "domains": [], "overall": {}, "rule": "",
            "more_severe": "", "cite": "", "version": "", "link": None, "for": "", "help": "", "custom": True,
            "severity": {}, "key": key}


def design(p: Project, did: str) -> dict | None:
    return next((d for d in plan(p).get("designs") or [] if d.get("id") == did), None)


def started(p: Project) -> bool:
    """Screening of any set of records has started."""
    return any((v or {}).get("phase") not in (None, "not_started") for v in (p.state_ro().get("sets") or {}).values())


def started_on(p: Project) -> str:
    """When screening first started ('' if it has not)."""
    times = [str((v or {}).get("started") or "") for v in (p.state_ro().get("sets") or {}).values()]
    times = [t for t in times if t]
    return min(times) if times else ""


def problems(pl: dict) -> list:
    """What is wrong with a plan as stored: [(key, text)]."""
    out = []
    for d in pl.get("designs") or []:
        if d.get("none") and len(_clean(d.get("reason"))) < 5:
            out.append(("rob_plan_reason", f"The risk-of-bias plan says studies of the design “{d.get('name')}” have no "
                        "risk-of-bias assessment, but gives no reason. Give the reason (Project set-up, Risk-of-bias "
                        "plan): it is reported in the methods."))
    return out


# ================================================================================================ saving it
def _norm_design(d: dict, known: dict, k: int) -> dict:
    did = str(d.get("id") or "")
    name = _clean(d.get("name"), 160)
    if did in NAMES:
        name = name or NAMES[did]
    elif not re.fullmatch(r"d[0-9a-f]{6,12}", did):
        did = "d" + util.sha256_text(f"{name}|{k}|{util.now()}")[:8]
    if not name:
        raise ProjectError("Give every study design in the plan a name (for example: Qualitative studies).")
    none = bool(d.get("none")) or not d.get("tool")
    reason = _clean(d.get("reason"), 600)
    tool = "" if none else str(d.get("tool") or "")
    if none and len(reason) < 5:
        raise ProjectError(f"“{name}”: say why these studies have no risk-of-bias assessment. The reason is reported in "
                           "the methods.")
    if not none and tool not in known:
        raise ProjectError(f"“{name}”: choose its tool.")
    return {"id": did, "name": name, "tool": tool, "none": none, "reason": reason if none else ""}


def _used_tools(p: Project) -> dict:
    """{tool key: number of assessments using it}."""
    from . import review
    out: dict = {}
    for d in review.rob_rows(p)["defs"].values():
        out[d.get("tool")] = out.get(d.get("tool"), 0) + 1
    return out


def _fixed_part(d: dict) -> tuple:
    """What of a lead-defined tool cannot change once an assessment uses it: its items (by number), its scale and its
    overall judgement. Labels, name, version, citation and link can still be corrected."""
    return ([x["id"] for x in d.get("items") or []], d.get("scale"), d.get("overall"))


def summary_lines(pl: dict, known: dict) -> list:
    """The plan in words, one design per line."""
    out = []
    for d in pl.get("designs") or []:
        if d.get("none"):
            out.append(f"{d['name']}: no risk-of-bias assessment ({d.get('reason', '')})")
        else:
            out.append(f"{d['name']}: {(known.get(d['tool']) or {}).get('name', d['tool'])}")
    return out


def _changes(old: dict, new: dict, known_old: dict, known_new: dict) -> list:
    """What changed between two plans, in words to go inside a sentence (joined with semicolons)."""
    od = {d["id"]: d for d in old.get("designs") or []}
    nd = {d["id"]: d for d in new.get("designs") or []}
    tname = lambda d, kn: ("no risk-of-bias assessment" if d.get("none") else (kn.get(d["tool"]) or {}).get("short", d["tool"]))  # noqa: E731
    out = []
    for i, d in nd.items():
        if i not in od:
            out.append(f"added {_mid(d['name'])}, " + ("with no risk-of-bias assessment" if d.get("none") else
                                                        f"assessed with {tname(d, known_new)}"))
        else:
            o = od[i]
            if o["name"] != d["name"]:
                out.append(f"renamed {_mid(o['name'])} as {_mid(d['name'])}")
            if (o.get("tool"), o.get("none")) != (d.get("tool"), d.get("none")):
                out.append(f"changed the tool from {tname(o, known_old)} to {tname(d, known_new)} for {_mid(d['name'])}")
            elif d.get("none") and o.get("reason") != d.get("reason"):
                out.append(f"changed the reason why {_mid(d['name'])} have no assessment")
    for i, o in od.items():
        if i not in nd:
            out.append(f"removed {_mid(o['name'])}")
    ot, nt = old.get("tools") or {}, new.get("tools") or {}
    for k, t in nt.items():
        if k not in ot:
            out.append(f"defined the tool {t['name']}")
        elif t != ot[k]:
            out.append(f"changed the definition of {t['name']}")
    for k, t in ot.items():
        if k not in nt:
            out.append(f"removed the tool {t.get('name', k)}")
    return out


def save_plan(p: Project, user: str, new: dict, reason: str = "") -> dict:
    """Record the plan, or change it (lead only). Once screening has started, a change to a recorded plan needs its
    reason and is recorded as a protocol amendment."""
    p.require_lead(user)
    reason = _clean(reason, 1000)
    with p.lock:
        cur = util.read_json(p.path(MARKER), {}) or {}
        old = stored(cur)
        used = _used_tools(p)
        # the lead-defined tools first: the designs may use them
        tools_in = new.get("tools") if isinstance(new.get("tools"), dict) else {}
        tdefs = {}
        for k, d in tools_in.items():
            k = str(k)
            if not re.fullmatch(robtools.CUSTOM_PREFIX + r"[0-9a-f]{6,12}", k):
                k = robtools.CUSTOM_PREFIX + util.sha256_text(f"{(d or {}).get('name')}|{util.now()}|{len(tdefs)}")[:8]
            try:
                clean = robtools.check_custom(d or {})
            except ValueError as e:
                raise ProjectError(str(e)) from None
            was = (old.get("tools") or {}).get(k)
            if was and used.get(k) and _fixed_part(was) != _fixed_part(clean):
                raise ProjectError(f"{was.get('name')} is already used by {util.plural(used[k], 'assessment')}, so its items, "
                                   "answer scale and overall judgement cannot change (the judgements already given would no "
                                   "longer fit). You can still correct the labels, version, citation and link; for a "
                                   "different tool, define a new one.")
            if was:
                clean = {**clean, "created": was.get("created", ""), "created_by": was.get("created_by", "")}
            else:
                clean = {**clean, "created": util.now(), "created_by": user}
            tdefs[k] = clean
        for k, was in (old.get("tools") or {}).items():
            if k not in tdefs and used.get(k):
                raise ProjectError(f"{was.get('name')} is used by {util.plural(used[k], 'assessment')}, so it cannot be "
                                   "removed. Take it out of the plan's designs instead: the assessments made with it keep it.")
        names = {n.lower() for n in [t["name"] for t in tdefs.values()]}
        if len(names) != len(tdefs):
            raise ProjectError("Two tools you defined have the same name. Give each its own name.")
        known = dict(robtools.TOOLS)
        known.update({k: robtools.custom_runtime(k, d) for k, d in tdefs.items()})
        designs, seen = [], set()
        for k, d in enumerate(new.get("designs") or []):
            if not isinstance(d, dict):
                continue
            nd = _norm_design(d, known, k)
            if nd["name"].lower() in seen:
                raise ProjectError(f"“{nd['name']}” is in the plan twice. Each study design needs its own name.")
            seen.add(nd["name"].lower())
            if nd["id"] in {x["id"] for x in designs}:
                nd["id"] = "d" + util.sha256_text(f"{nd['name']}|{k}|{util.now()}")[:8]
            designs.append(nd)
        if not designs:
            raise ProjectError("Add at least one study design to the plan, with the tool for it: the designs your protocol "
                               "says the review includes.")
        out = {"designs": designs, "tools": tdefs}
        was_recorded = bool(old.get("recorded"))
        known_old = dict(robtools.TOOLS)
        known_old.update({k: robtools.custom_runtime(k, d) for k, d in (old.get("tools") or {}).items() if isinstance(d, dict)})
        changes = _changes(old, out, known_old, known) if was_recorded else []
        if was_recorded and not changes:
            return {"plan": old, "changed": False, "amendment": False}
        is_started = started(p)
        amendment = was_recorded and is_started
        if amendment and len(reason) < 10:
            raise ProjectError("Screening has started, so a change to the risk-of-bias plan is a protocol amendment. Say "
                               "why it is needed: the reason is reported in the methods (PRISMA 2020 item 24c).")
        now = util.now()
        out.update({"recorded": old.get("recorded") if was_recorded else now,
                    "recorded_by": old.get("recorded_by") if was_recorded else user,
                    "before_screening": old.get("before_screening") if was_recorded else not is_started,
                    "rev": _next_rev(p, old), "saved": now, "saved_by": user})
        cur[KEY] = out
        if amendment:
            cur.setdefault("amendments", []).append({"date": now, "by": user, "what": KEY, "reason": reason,
                                                     "changes": changes, "before": summary_lines(old, known_old),
                                                     "after": summary_lines(out, known),
                                                     "designs_before": snapshot(old), "designs_after": snapshot(out)})
        cur["updated"], cur["updated_by"], cur["updated_on"] = now, user, p.dev()
        versions.stamp(cur)
        util.write_json_atomic(p.path(MARKER), cur)
        p._saw_version(MARKER, cur)
        p.backup(MARKER)
    action = ("change the risk-of-bias plan (protocol amendment)" if amendment else
              "change the risk-of-bias plan" if was_recorded else "record the risk-of-bias plan")
    p.audit(user, action, {"plan": summary_lines(out, known)[:40], "fingerprint": fingerprint(out), "rev": out["rev"],
                           **_logged_plan(out),
                           **({"changes": changes[:40]} if changes else {}),
                           **({"reason": reason} if amendment else {}),
                           **({"after screening started": True} if is_started else {})})
    return {"plan": out, "changed": True, "amendment": amendment}


def snapshot(pl: dict) -> list:
    """The plan's designs as they stood: [{id, name, tool, none, reason}]."""
    return [{k: d.get(k) for k in ("id", "name", "tool", "none", "reason")} for d in pl.get("designs") or []]


def _logged_plan(pl: dict) -> dict:
    """What the audit entry keeps of a plan, so that a change made outside ReviewDesk can be described: its designs, and
    the names of the lead's tools."""
    return {"designs": snapshot(pl)[:60], "tools": {k: (t or {}).get("name", k) for k, t in (pl.get("tools") or {}).items()}}


def fingerprint(pl: dict) -> str:
    """What the plan says (its designs, tools and when it was recorded), for the data checks."""
    return integrity.text_print({"designs": [{k: d.get(k) for k in ("id", "name", "tool", "none", "reason")}
                                             for d in pl.get("designs") or []],
                                 "tools": {k: {f: v for f, v in (t or {}).items() if f not in ("created", "created_by")}
                                           for k, t in (pl.get("tools") or {}).items()},
                                 "recorded": pl.get("recorded", ""), "before_screening": pl.get("before_screening")})


LOGGED_AGAIN = "record the risk-of-bias plan in the audit log again"
PLAN_ACTIONS = ("record the risk-of-bias plan", "change the risk-of-bias plan",
                "change the risk-of-bias plan (protocol amendment)", "record a change made to the risk-of-bias plan outside ReviewDesk",
                LOGGED_AGAIN)


def _renamed(line: str) -> str:
    """A line of a plan in words as an older ReviewDesk wrote it, with any tool's former name as it is named now."""
    for t in robtools.TOOLS.values():
        for old in t.get("former_names") or ():
            if line.endswith(": " + old):
                return line[: -len(old)] + t["name"]
    return str(line)


def _latest_logged(logged: list) -> dict:
    """The plan entry that was recorded last. Each entry carries the plan's revision number (a count kept in the plan
    itself and raised by one at every change), so the order does not depend on the clocks of the computers that wrote
    them: a clock ten minutes fast on one of the lead's two computers must not make its entry look like the latest.
    Entries from before the number was logged come first, in time order."""
    def key(ie):
        i, e = ie
        rev = (e.get("details") or {}).get("rev")
        return (1, rev, i) if isinstance(rev, int) and not isinstance(rev, bool) else (0, 0, i)
    return max(enumerate(logged), key=key)[1]


def _next_rev(p: Project, pl: dict) -> int:
    """The revision number for the plan about to be saved: one more than the plan in the file and than every plan entry
    in the audit log (the file's own count can be lost: the plan removed and recorded again, an older file put back)."""
    revs = [(e.get("details") or {}).get("rev") for e in p.audit_entries() if e.get("action") in PLAN_ACTIONS]
    return max([int(pl.get("rev") or 0)] + [r for r in revs if isinstance(r, int) and not isinstance(r, bool)]) + 1


def outside_change(p: Project) -> dict | None:
    """A problem for the data checks when the plan in the project file is not the plan last recorded in ReviewDesk
    (it was edited, or removed, outside ReviewDesk), as the criteria are compared with the seal and amendments. A plan
    saved in the last 15 minutes may not have its audit entry on this computer yet (the sync service copies files one
    at a time), so it is checked once that time has passed."""
    logged = [e for e in p.audit_entries() if e.get("action") in PLAN_ACTIONS]
    if not logged:
        return None
    last = _latest_logged(logged)
    det = last.get("details") or {}
    pl = stored(p.settings_ro())
    if det.get("fingerprint"):
        same = pl.get("recorded") and integrity.same(det["fingerprint"], fingerprint(pl), p._old_fingerprints())
    else:                                    # recorded before fingerprints were kept: compare the designs in words
        same = pl.get("recorded") and summary_lines(pl, tools(p))[:40] == [_renamed(x) for x in det.get("plan") or []]
    if same:
        return None
    behind = False
    if isinstance(det.get("rev"), int) and isinstance(pl.get("rev"), int):
        newer = pl["rev"] > det["rev"]       # the plan in the file is a later revision than any entry that has arrived
        behind = newer and bool(pl.get("recorded"))
    else:
        newer = bool(pl.get("saved")) and util.ts(pl["saved"]) > util.ts(last.get("t", ""))
    if pl.get("saved") and util.ts(util.now()) - util.ts(pl["saved"]) < 900 and newer:
        return None
    when = str(last.get("t", ""))[:10]
    if behind:
        # ReviewDesk saved this plan (only it raises the revision number), but the entry it wrote in the audit log is
        # not here: that computer's audit file has not arrived, an older copy of it was put back, or ReviewDesk was
        # stopped between the two writes. The plan was not changed outside ReviewDesk, and to record it in the log
        # again is not a protocol amendment (an amendment made with this plan is already in the project file).
        return {"kind": "robplan", "log_behind": True, "file": MARKER, "fixes": ["accept"],
                "sig": integrity.label(["robplan-log", integrity.shortened(fingerprint(pl)), pl["rev"], det["rev"]]),
                "text": (f"The audit log has no entry for the risk-of-bias plan now in use (revision {pl['rev']}"
                         + (f", saved {str(pl['saved'])[:10]}" if pl.get("saved") else "") +
                         f"): the last plan it records is revision {det['rev']} ({when}). The audit file of the computer "
                         "that saved the plan has probably not arrived on this computer yet, or the sync service has put "
                         "back an older copy of it. Check that the plan under Project set-up is the one the team agreed. "
                         "If it is, the project lead can record it in the log again; that is not a protocol amendment.")}
    text = ("The risk-of-bias plan was removed from the project file outside ReviewDesk" if not pl.get("recorded") else
            "The risk-of-bias plan was changed outside ReviewDesk") + \
           f": it does not match the plan last recorded in ReviewDesk ({when})." + \
           ("" if pl.get("recorded") else " Put back the copy kept on this computer, or record the plan again (Project "
                                           "set-up, Risk-of-bias plan).")
    return {"kind": "robplan", "file": MARKER, "fixes": ["backup", "accept"] if pl.get("recorded") else ["backup"],
            "sig": integrity.label(["robplan", integrity.shortened(fingerprint(pl)), last.get("t", "")]), "text": text}


def log_again(p: Project, user: str, reason: str) -> None:
    """The lead confirms that the plan in use is the one the team agreed, where its entry in the audit log is missing
    (see outside_change): the plan is logged again as it stands. Nothing in the project file changes."""
    pl = stored(p.settings_ro())
    p.audit(user, LOGGED_AGAIN, {"plan": summary_lines(pl, tools(p))[:40], "fingerprint": fingerprint(pl), "rev": pl.get("rev"),
                                 **_logged_plan(pl), "reason": reason[:500]})


def accept_outside_change(p: Project, user: str, reason: str) -> None:
    """The lead records a change made to the plan outside ReviewDesk as correct: it becomes a protocol amendment, with
    the reason, and the plan as it now stands is the one recorded (called by Project.accept_problem)."""
    logged = [e for e in p.audit_entries() if e.get("action") in PLAN_ACTIONS]
    det = (_latest_logged(logged) if logged else {}).get("details") or {}
    before = list(det.get("plan") or [])
    with p.lock:
        cur = util.read_json(p.path(MARKER), {}) or {}
        pl = stored(cur)
        known = tools(p)
        was = det.get("designs")                 # the designs last recorded in ReviewDesk (kept in its audit entries)
        if was is None:
            was = _designs_from_lines(p, before)
        words = []
        if was is not None:
            known_old = dict(robtools.TOOLS)
            known_old.update({k: {"name": n, "short": n} for k, n in (det.get("tools") or {}).items()})
            words = _changes({"designs": was}, {"designs": pl.get("designs") or []}, known_old, known)
            old_t, new_t = det.get("tools") or {}, {k: t.get("name", k) for k, t in (pl.get("tools") or {}).items()}
            words += [f"defined the tool {n}" for k, n in new_t.items() if k not in old_t]
            words += [f"removed the tool {n}" for k, n in old_t.items() if k not in new_t]
        what = "recorded as correct a change made to the plan outside ReviewDesk" + (
            ", which " + "; ".join(words) if words else "")
        cur.setdefault("amendments", []).append({
            "date": util.now(), "by": user, "what": KEY, "reason": reason[:1000], "outside_reviewdesk": True,
            "changes": [what], "before": before, "after": summary_lines(pl, known),
            "designs_before": was, "designs_after": snapshot(pl)})
        pl["rev"] = _next_rev(p, pl)
        util.write_json_atomic(p.path(MARKER), cur)
        p._saw_version(MARKER, cur)
        p.backup(MARKER)
    p.audit(user, "record a change made to the risk-of-bias plan outside ReviewDesk",
            {"plan": summary_lines(pl, known)[:40], "fingerprint": fingerprint(pl), "rev": pl["rev"], **_logged_plan(pl),
             "reason": reason[:500]})


def amendments(p: Project) -> list:
    """The plan's amendments, oldest first. One recorded before ReviewDesk kept each amendment's designs has the plan
    before and after only in words: its designs are read back from them where that is safe, and its words follow the
    tools' present names and put design names in lower case mid-sentence, as later amendments do."""
    out = []
    for a in p.settings_ro().get("amendments") or []:
        if a.get("what") != KEY:
            continue
        if a.get("designs_before") is None and not a.get("outside_reviewdesk"):
            a = _from_words(p, a)
        out.append(a)
    return out


def _former(text: str) -> str:
    """A tool's former name or short name, in older words, as it is named now."""
    for t in robtools.TOOLS.values():
        for old in t.get("former_names") or ():
            text = text.replace(old, t["name"])
        for old in t.get("former_short") or ():
            text = text.replace(old, t["short"])
    return text


def _designs_from_lines(p: Project, lines) -> list | None:
    """A plan's designs read back from its lines in words (summary_lines), or None if any line cannot be read."""
    if not isinstance(lines, list) or not lines:
        return None
    pl = plan(p)
    names = {}
    for k, t in robtools.TOOLS.items():
        for n in (t["name"], *(t.get("former_names") or ())):
            names[n] = k
    names.update({t.get("name", k): k for k, t in (pl.get("tools") or {}).items()})
    ids = {d["name"]: d["id"] for d in pl.get("designs") or []}
    ids.update({n: i for i, n in NAMES.items()})
    out = []
    for line in lines:
        line = str(line)
        m = re.match(r"^(.+?): no risk-of-bias assessment \((.*)\)$", line)
        if m:
            d = {"name": m.group(1), "tool": "", "none": True, "reason": m.group(2)}
        else:
            hit = next((n for n in sorted(names, key=len, reverse=True) if line.endswith(": " + n)), None)
            if not hit:
                return None
            d = {"name": line[: -len(hit) - 2], "tool": names[hit], "none": False, "reason": ""}
        if d["name"] not in ids:
            return None
        out.append({"id": ids[d["name"]], **d})
    return out


def _from_words(p: Project, a: dict) -> dict:
    words = []
    for c in a.get("changes") or []:
        c = _former(str(c))
        m = re.match(r"^changed the tool for (.+) from (.+) to (.+)$", c)
        if m:                                            # the older order: the design's name (it may hold a comma) last
            c = f"changed the tool from {m.group(2)} to {m.group(3)} for {m.group(1)}"
        m = re.match(r"^(added |removed |renamed |changed the tool from .+? for |changed the reason why )(.*)$", c)
        if m:
            rest = m.group(2)
            if m.group(1) == "renamed " and " as " in rest:
                x, y = rest.split(" as ", 1)
                rest = f"{_mid(x)} as {_mid(y)}"
            else:
                rest = _mid(rest)
            c = m.group(1) + rest
        words.append(c)
    a = {**a, "changes": words}
    if any(c.startswith("renamed ") for c in words):       # a design read back by its name would look removed and added
        return a
    before, after = _designs_from_lines(p, a.get("before")), _designs_from_lines(p, a.get("after"))
    if before is not None and after is not None:
        a.update({"designs_before": before, "designs_after": after})
    return a


# ================================================================================================ designs and extraction
def _name_key(s) -> str:
    """A design's name for matching: lower case, words only, each word in the singular ('Qualitative studies' and
    'qualitative study' match)."""
    words = re.sub(r"[^a-z0-9]+", " ", _clean(s).lower()).split()
    return " ".join(w[:-3] + "y" if w.endswith("ies") and len(w) > 4 else w[:-1] if w.endswith("s") and not
                    w.endswith("ss") and len(w) > 3 else w for w in words)


def compatible(pl: dict, text: str) -> list:
    """The plan's designs (ids) that an extracted study design fits, the best first; [] when it fits none or cannot be
    told. A design named exactly as extraction records it comes first (the general form offers the plan's names), then
    a design the lead named that contains it or is contained in it, then the word rules (DESIGN_WORDS)."""
    t = _clean(text).lower()
    if not t:
        return []
    designs = pl.get("designs") or []
    k = _name_key(t)
    out = [d["id"] for d in designs if k and _name_key(d.get("name")) == k]
    for d in designs:                                   # a design the lead named much as extraction does
        n = _name_key(d.get("name"))
        if n and len(k) >= 6 and (k in n or n in k) and d["id"] not in NAMES and d["id"] not in out:
            out.append(d["id"])
    for pat, ids in DESIGN_WORDS:
        if re.search(pat, t):
            out += [i for i in ids if i not in out]
            break
    have = {d["id"] for d in designs}
    return [i for i in out if i in have]


def _design_field(p: Project):
    from . import review
    flds = review.fields(review.form(p))
    return next((x for x in flds if x["id"] == "design"), None) or next(
        (x for x in flds if "design" in x["label"].lower() and x["type"] in ("choice", "text")), None)


def extracted_design(p: Project, key: str, viewer: str = "") -> str:
    """The study's design as extraction recorded it: the settled value; or, for a viewer, what they may see of it (their
    own answer, or the first extractor's once it is shown to them). '' if nothing is recorded."""
    from . import review
    fl = _design_field(p)
    if not fl:
        return ""
    fin = p.memo("x_final_values", lambda: review.final_values(p)).get(key, {})
    v = fin.get(fl["id"])
    if v:
        return str(v)
    if not viewer:
        return ""
    st = review.study_state(p, key, viewer)
    for src in (st.get("mine") or {}, st.get("other") or {}):
        v = (src.get(fl["id"]) or {}).get("v")
        if v:
            return str(v)
    return ""


def suggested(p: Project, key: str, viewer: str = "") -> dict:
    """{'text': the extracted design, 'designs': the plan's designs it fits} for a study."""
    text = extracted_design(p, key, viewer)
    if not text:
        return {"text": "", "designs": [], "warnings": {}}
    pl = plan(p)
    warn = {d["id"]: warning(p, key, d["id"], d["tool"], viewer, adding=True) for d in pl.get("designs") or []
            if d.get("tool") and not d.get("none")}
    return {"text": text, "designs": compatible(pl, text), "warnings": {k: v for k, v in warn.items() if v}}


STANDARD_PLAN = {"designs": [{"id": d, "name": n, "tool": t} for d, n, t in robtools.STANDARD_DESIGNS]}


def _misfit(text: str, tool: str) -> str:
    """'' or why a built-in tool does not cover a study of the design extraction records, whatever the plan says."""
    t = _clean(text).lower()
    if tool in RCT_TOOLS and re.search(DESIGN_WORDS[0][0], t):
        return ("No built-in tool covers cluster crossover trials: RoB 2 for cluster-randomised trials is for "
                "parallel-group cluster trials, and RoB 2 for crossover trials for individually randomised ones. Say in "
                "the review how the tool was used for this trial.")
    if tool in FOLLOW_UP_TOOLS and re.search(r"case-?control|cross-?sectional", t):
        return (f"{robtools.TOOLS[tool]['short']} is for follow-up (cohort-type) studies, and a case-control or "
                "cross-sectional study is not one. Check the design, and the tool the plan gives for it.")
    return ""


def warning(p: Project, key: str, did: str, tool: str, viewer: str = "", adding: bool = False) -> str:
    """'' or why an assessment's design (or, for one added before the plan, its tool) does not fit the design
    extraction recorded for the study: a design of the plan it fits better, a standard design the plan does not have
    (a cluster trial when the plan has no design for cluster trials), or a built-in tool that does not cover it.
    adding: worded for the form that adds an assessment, before it is added."""
    text = extracted_design(p, key, viewer)
    if not text:
        return ""
    pl = plan(p)
    by = {d["id"]: d for d in pl.get("designs") or []}
    misfit = _misfit(text, tool)
    if misfit:
        return f"Extraction records this study's design as “{text}”. " + misfit
    fit = compatible(pl, text)                           # the plan's designs it fits
    std = compatible(STANDARD_PLAN, text)                # the standard designs it fits, in the plan or not
    chosen = (by.get(did) or {}).get("name") or did
    short = robtools.TOOLS[tool]["short"] if tool in robtools.TOOLS else tool_def(p, tool).get("short") or "its tool"
    what = f"the design “{chosen}”, with {short}" if did else short
    cluster = (" A cluster-randomised trial is assessed with RoB 2 for cluster-randomised trials, which also judges bias "
               "arising from the timing of identification or recruitment of participants (D1b).") if (
        "cluster_rct" in (fit or std) and tool == "rob2") else ""
    if fit:
        if (did and did in fit) or (not did and tool in {by[i]["tool"] for i in fit}):
            return ""
        fits = " or ".join(f"“{by[i]['name']}”" for i in fit[:3])
        if adding:
            return (f"Extraction records this study's design as “{text}”, which fits {fits} in the risk-of-bias plan, not "
                    f"“{chosen}”.{cluster} You can still add it if extraction is wrong.")
        return (f"Extraction records this study's design as “{text}”, which fits {fits} in the risk-of-bias plan, but this "
                f"assessment uses {what}.{cluster} Check the design: if extraction is right, add the assessment again with "
                "the right design and remove this one.")
    rule = next((pat for pat, _ in DESIGN_WORDS if re.search(pat, _clean(text).lower())), "")
    specific = rule not in BROAD and (tool in robtools.TOOLS or set(std) & set(TRIALS))   # (a lead's tool: trials only)
    if std and specific and tool not in {RECOMMENDED[i] for i in std}:
        names = " or ".join(_mid(NAMES[i]) for i in std[:3])
        tools_ = " or ".join(dict.fromkeys(robtools.TOOLS[RECOMMENDED[i]]["short"] for i in std[:3]))
        them = "them" if len(std) == 1 else "any of these"
        usual = cluster or f" Such studies are usually assessed with {tools_}."
        if not pl.get("recorded") and not adding:
            return (f"Extraction records this study's design as “{text}”, which fits {names}, but this assessment uses "
                    f"{what}: the tool was chosen when it was added, before the risk-of-bias plan was recorded.{usual} "
                    "If extraction is right, the project lead records the plan with that design in it (Project set-up, "
                    "Risk-of-bias plan), and the assessment is added again with it.")
        if adding:
            return (f"Extraction records this study's design as “{text}”, which fits {names}, but the risk-of-bias plan "
                    f"has no design for {them}, so it would be assessed with {short}.{usual} If extraction is right, ask "
                    "the project lead to add the design to the plan (Project set-up, Risk-of-bias plan; a protocol "
                    "amendment once screening has started), then add the assessment with it.")
        return (f"Extraction records this study's design as “{text}”, which fits {names}, but the risk-of-bias plan "
                f"has no design for {them}, so this assessment uses {what}.{usual} If extraction is right, the project "
                "lead adds the design to the plan (Project set-up, Risk-of-bias plan; a protocol amendment once "
                "screening has started), and the assessment is added again with it.")
    return ""


def history(p: Project) -> dict:
    """How the plan's designs changed after it was recorded: {'original': [designs as first recorded], 'events':
    {design id: [(date, what, old tool, new tool)]}} with what 'added', 'removed' or 'tool'. Amendments recorded before
    ReviewDesk kept the designs in each amendment are read from their words only ('original' is then None)."""
    ams = amendments(p)
    cur = snapshot(plan(p))
    if not ams:
        return {"original": cur, "events": {}}
    if not all(a.get("designs_before") is not None or (k and a.get("outside_reviewdesk")) for k, a in enumerate(ams)):
        return {"original": None, "events": {}}
    original = next((a["designs_before"] for a in ams if a.get("designs_before") is not None), cur)
    events: dict = {}
    state = {d["id"]: d for d in original}
    for k, a in enumerate(ams):
        after = a.get("designs_after")
        if after is None:
            after = ams[k + 1].get("designs_before") if k + 1 < len(ams) else cur
        new = {d["id"]: d for d in after or []}
        for i, d in new.items():
            if i not in state:
                events.setdefault(i, []).append((a["date"], "added", "", d.get("tool", "")))
            elif (state[i].get("tool"), state[i].get("none")) != (d.get("tool"), d.get("none")):
                events.setdefault(i, []).append((a["date"], "tool", state[i].get("tool", ""), d.get("tool", "")))
        for i, d in state.items():
            if i not in new:
                events.setdefault(i, []).append((a["date"], "removed", d.get("tool", ""), ""))
        state = new
    return {"original": original, "events": events}


def changes_by_design(p: Project) -> tuple:
    """({design id: 'Added on 4 November 2026; On 9 December 2026, changed from RoB 2'}, [designs the amendments
    removed]) for the plan's tables; ({}, []) when the plan has not been amended or its amendments cannot be read."""
    ams = amendments(p)
    if not ams:
        return {}, []
    hist = history(p)
    tname = lambda key: (tool_def(p, key).get("short") or key) if key else "no risk-of-bias assessment"   # noqa: E731
    out = {}
    for i, evs in hist["events"].items():
        out[i] = "; ".join(f"Added on {_day(w)}" if what == "added" else f"Removed on {_day(w)}" if what == "removed"
                           else f"On {_day(w)}, changed from {tname(was)}" for w, what, was, _ in evs)
    seen = {d["id"]: d for d in hist["original"] or []}
    for a in ams:
        seen.update({d["id"]: d for d in a.get("designs_after") or []})
    have = {d["id"] for d in plan(p).get("designs") or []}
    return out, [d for i, d in seen.items() if i not in have]


def superseded(p: Project, d: dict) -> str:
    """'' or why an assessment's tool is no longer the one the plan gives for its design (the plan was amended after
    the assessment was added): the methods text and tables report it, and the lead decides what to do."""
    did, tool = d.get("design", ""), d.get("tool", "")
    if not did or not recorded(p):
        return ""
    now = design(p, did)
    if now and not now.get("none") and now.get("tool") == tool:
        return ""
    when = next((e[0] for e in reversed(history(p)["events"].get(did, [])) if e[1] in ("tool", "removed")), "")
    if not when:
        when = (amendments(p)[-1:] or [{}])[0].get("date", "")
    day = _day(when)
    old = robtools.TOOLS[tool]["short"] if tool in robtools.TOOLS else tool_def(p, tool)["name"]
    name = _mid(d.get("design_name") or (now or {}).get("name") or did)
    on = f" (amended{' on ' + day if day else ''})"
    gave = old if name.lower() in old.lower() else f"{old} for {name}"
    if not now or now.get("none"):
        change = (f"it no longer has that design{on}" if not now else
                  f"it now gives these studies no risk-of-bias assessment{on}")
        todo = ("Check the study's design: if another design of the plan fits it, add the assessment again with that "
                "design; otherwise keep this assessment and say why in the review, or remove it.")
    else:
        change = f"it now gives {tool_def(p, now['tool']).get('short', now['tool'])}{on}"
        todo = "Assess the result again with the plan's tool (add it again), or keep this assessment and say why in the review."
    return f"When this assessment was added, the plan gave {gave}; {change}. {todo}"


MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
          "December")


def _day(s) -> str:
    t = str(s or "")[:10]
    try:
        y, m, dd = (int(x) for x in t.split("-"))
        return f"{dd} {MONTHS[m - 1]} {y}"
    except (ValueError, IndexError):
        return ""


# ================================================================================================ the page
def public_plan(p: Project, me: str) -> dict:
    pl = plan(p)
    if pl.get("draft") and not pl.get("designs"):       # nothing recorded or named: offer the designs already in use
        pl = {**pl, **draft_from_use(p)}
    ts = tools(p)
    used = _used_tools(p)
    return {"plan": copy.deepcopy({k: v for k, v in pl.items()}), "recorded": bool(pl.get("recorded")),
            "standard": [{"id": d, "name": n, "tool": t} for d, n, t in robtools.STANDARD_DESIGNS],
            "tools": {k: robtools.public(t) for k, t in ts.items()}, "builtin": list(robtools.TOOLS),
            "presets": {k: {"label": v["label"], "answers": v["answers"]} for k, v in robtools.SCALE_PRESETS.items()},
            "started": started(p), "started_on": started_on(p), "is_lead": p.is_lead(me), "used": used,
            "amendments": amendments(p), "problems": [t for _, t in problems(pl)]}


def h_plan_get(p: Project, me: str, q, body) -> dict:
    p.require_member(me)
    return public_plan(p, me)


def h_plan_post(p: Project, me: str, q, body) -> dict:
    res = save_plan(p, me, body.get("plan") or {}, body.get("reason", ""))
    return {**public_plan(p, me), "changed": res["changed"], "amendment": res["amendment"]}


API_GET = {"/api/rob/plan": h_plan_get}
API_POST = {"/api/rob/plan": h_plan_post}
