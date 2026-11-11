"""Data extraction and risk of bias, for the studies included at full text.

Who does what (as the protocol says). The first extractor extracts every item; the second extractor checks the items
marked "checked" against the paper (agreeing, or giving a correction) and extracts the items marked "both"
independently. Neither sees the other's answers to the "both" items until both have finished that study. Where the two
differ, the lead records the agreed value. Risk of bias is judged by both reviewers independently, domain by domain,
for each result assessed, and reconciled the same way.

Storage, in the shared folder (each file written by one person on one computer, chained; see integrity.py):
  extraction/form.json            the form (written by the lead; versioned)
  extraction/<ID>@<computer>.jsonl  each person's answers: {"s": study, "f": field, "v": value, "pg": where}, and
                                    {"s": study, "done": true|false}
  extraction/agreed@<computer>.jsonl the lead's agreed values
  rob/<ID>@<computer>.jsonl         each person's assessments ({"s", "a", "def": {"tool", "design", "design_name", "label",
                                    "deleted"}}), judgements ({"s", "a", "d", "j", "q"}) and {"s", "a", "done"}
  rob/agreed@<computer>.jsonl       the lead's agreed judgements ({"s", "a", "d", "j"}, with "why" when the lead changed a
                                    judgement both reviewers gave)

A study is the set of included reports given the same study name at full text (a report without a study name is a
study on its own). It is known by the lowest record ID among its reports.
"""
from __future__ import annotations

import copy
import os
import re

from . import integrity, robplan, robtools, screening, util
from .project import Project, ProjectError, clean_id, written_order

# ================================================================================================ forms
TYPES = ("text", "long", "number", "choice", "yesno", "table")
YESNO = ["Yes", "No", "Unclear", "Not reported"]


def _f(fid, label, typ="text", mode="check", options=None, help="", columns=None):
    d = {"id": fid, "label": label, "type": typ, "mode": mode, "help": help}
    if options:
        d["options"] = options
    if columns:
        d["columns"] = columns
    return d


GENERIC = {"name": "General systematic review", "sections": [
    {"title": "Study", "fields": [
        _f("author_year", "First author and year"), _f("country", "Country"), _f("design", "Study design", "choice",
           options=["Randomised trial", "Cluster randomised trial", "Non-randomised study with a comparison group",
                    "Before-after study", "Interrupted time series", "Cohort study", "Other"]),
        _f("setting", "Setting", "long"), _f("n", "Number of participants", "number"),
        _f("population", "Participants (as described)", "long"), _f("intervention", "Intervention", "long"),
        _f("comparator", "Comparator", "long"), _f("funding", "Funding and conflicts of interest", "long")]},
    {"title": "Results", "fields": [
        _f("outcomes", "Outcomes and results", "table", mode="both", columns=[
            {"id": "outcome", "label": "Outcome", "type": "text"}, {"id": "measure", "label": "Measure and time point", "type": "text"},
            {"id": "group", "label": "Group", "type": "text"}, {"id": "n", "label": "n", "type": "number"},
            {"id": "result", "label": "Result", "type": "text"}, {"id": "effect", "label": "Effect (95% CI)", "type": "text"},
            {"id": "where", "label": "Page or table", "type": "text"}])]},
    {"title": "Notes", "fields": [_f("notes", "Notes", "long")]}]}

TEMPLATES = {"generic": GENERIC}
DIFFER = ("differs", "changed")              # comparison results the lead must settle
DESIGN_OPTIONS = list(next(x for x in GENERIC["sections"][0]["fields"] if x["id"] == "design")["options"])


def form(p: Project) -> dict:
    f = p._cached("xform", p.path("extraction", "form.json"),
                  lambda: p._read_shared(("extraction", "form.json"), {}, "extraction/form.json") or {})
    if not f.get("sections"):
        f = dict(copy.deepcopy(GENERIC), rev=0, template="generic")
    return with_plan_designs(p, f)


def plan_designs(p: Project) -> list:
    """The general form's choices for the study's design once the risk-of-bias plan is recorded: the plan's design names
    (so that the design extraction records chooses the plan's design when an assessment is added), then Other. [] while
    no plan is recorded."""
    pl = robplan.plan(p)
    names = [str(d.get("name") or "").strip() for d in pl.get("designs") or []] if pl.get("recorded") else []
    names = list(dict.fromkeys(n for n in names if n))
    return names + ([] if not names or "other" in {n.lower() for n in names} else ["Other"])


def with_plan_designs(p: Project, f: dict) -> dict:
    """The form with the general form's own Study design item offering the plan's designs (plan_designs). Only that item
    as the general form has it (or as it was saved with the plan's designs) follows the plan: an item the lead wrote
    or edited keeps its own choices. The form file itself is not changed; answers already given stay as they are."""
    names = plan_designs(p)
    own = [x for x in fields(f) if x.get("id") == "design" and x.get("type") == "choice"
           and (x.get("options_from") == "robplan" or list(x.get("options") or []) == DESIGN_OPTIONS)]
    if not own:
        return f
    f = copy.deepcopy(f)
    for x in fields(f):
        if x.get("id") == "design" and x.get("type") == "choice":
            x["options"] = names or list(DESIGN_OPTIONS)
            x["options_from"] = "robplan"
    return f


def builtin_form(p: Project) -> dict:
    """The built-in general form as this project offers it (its Study design item following the risk-of-bias plan)."""
    return with_plan_designs(p, copy.deepcopy(GENERIC))


def fields(f: dict) -> list:
    return [x for sec in f.get("sections", []) for x in sec.get("fields", [])]


def people(p: Project, f: dict | None = None) -> dict:
    """{'first', 'second', 'set'}: who extracts everything, and who checks and extracts the outcome data. Until the lead
    names them, the plan's single reviewer extracts and the other reviewer checks. Never one person in both roles."""
    f = f or form(p)
    named = [f.get("first") or "", f.get("second") or ""]
    if named[0]:              # named by the lead: they keep their roles, even after leaving the project, until the lead
        first, second = named  # chooses again (nobody else steps into a role, and the answers given so far stay theirs)
        second = "" if second == first else second
        gone = [x for x in (first, second) if x and not p.reviewer(x)]
        return {"first": first, "second": second, "set": bool(first and second) and not gone, "gone": gone}
    pl = screening.plan(p, "main")
    two = [x for x in (pl.get("reviewers") or []) if p.reviewer(x)]
    first = (pl.get("single_reviewer") if p.reviewer(pl.get("single_reviewer") or "") else "") or (two[0] if two else "")
    second = next((x for x in two if x != first), "")
    return {"first": first, "second": second, "set": False, "gone": []}


def _answered_fields(p: Project) -> set:
    a = answers(p)
    out = {fid for who in a["values"].values() for s in who.values() for fid, d in s.items() if _norm(d.get("v"))}
    return out | {fid for s in a["agreed"].values() for fid in s}


def save_form(p: Project, user: str, new: dict) -> dict:
    p.require_lead(user)
    secs = new.get("sections")
    if not isinstance(secs, list) or not secs:
        raise ProjectError("The form needs at least one section with a field in it.")
    cur = form(p)
    old = {x["id"]: x for x in fields(cur)} if cur.get("rev") else {}
    old_sec = {x["id"]: sec.get("title", "") for sec in cur.get("sections", []) for x in sec.get("fields", [])}
    answered = _answered_fields(p) if old else set()
    retired = set(cur.get("retired") or []) | (answered - set(old))     # names of removed items, and any with answers
    removed = dict(cur.get("removed_items") or {})                      # each removed item as it was, to put back
    ids, back = set(), set()
    for sec in secs:
        if not str(sec.get("title") or "").strip():
            raise ProjectError("Give every section a title.")
        for fl in sec.get("fields", []):
            fl.pop("_ask", None)
            fl.pop("section", None)
            fl["id"] = clean_field_id(fl.get("id") or fl.get("label"))
            fl["mode"] = "both" if fl.get("mode") == "both" else "check"
            if fl.get("options_from") == "robplan" and (fl["id"] != "design" or fl.get("type") != "choice"
                                                        or list(fl.get("options") or []) != (plan_designs(p) or DESIGN_OPTIONS)):
                fl.pop("options_from")          # the lead changed the choices: they are the lead's own from now on
            was = removed.get(fl["id"])
            if fl["id"] not in old and fl["id"] in retired and was and was.get("type") == fl.get("type") \
                    and was.get("mode") == fl["mode"]:
                back.add(fl["id"])             # a removed item put back as it was: its answers come back with it
            elif fl["id"] not in old and fl["id"] in retired:  # a new item: never the name of a removed one
                base, k = fl["id"][:36], 2
                while f"{base}_{k}" in retired or f"{base}_{k}" in old or f"{base}_{k}" in ids:
                    k += 1
                fl["id"] = f"{base}_{k}"
            if not fl["id"] or fl["id"] in ids:
                raise ProjectError(f"Two items would have the same name ({fl.get('label')}): give them different labels.")
            ids.add(fl["id"])
            if fl.get("type") not in TYPES:
                raise ProjectError(f"Unknown kind of answer for {fl.get('label')}.")
            if not str(fl.get("label") or "").strip():
                raise ProjectError("Every item needs a label.")
            if fl["type"] == "choice" and not [o for o in fl.get("options", []) if str(o).strip()]:
                raise ProjectError(f"{fl['label']}: give the choices, one per line.")
            if fl["type"] == "table" and not fl.get("columns"):
                raise ProjectError(f"{fl['label']}: a table needs at least one column.")
            was = old.get(fl["id"])
            if was and fl["id"] in answered and (was["type"] != fl["type"] or was.get("mode") != fl["mode"]):
                raise ProjectError(f"“{was['label']}” already has answers, so its kind of answer and who extracts it cannot "
                                   "change (changing who could show one extractor the other's answers). Add a new item instead.")
    first, second = clean_id(new.get("first") or ""), clean_id(new.get("second") or "")
    if not first:
        raise ProjectError("Choose the first extractor.")
    if not second and len(p.reviewers()) > 1:
        raise ProjectError("Choose the second extractor.")
    if first == second:
        raise ProjectError("The first and second extractors must be different people.")
    for v in (first, second):
        if v and not p.reviewer(v):
            raise ProjectError(f"{v} is not in the project.")
    out = {"name": str(new.get("name") or cur.get("name") or "Extraction form"), "sections": secs, "first": first,
           "second": second, "rev": int(cur.get("rev", 0)) + 1, "saved": util.now(), "by": user,
           "template": new.get("template") or cur.get("template", ""),
           "retired": sorted((retired - back) | (set(old) - ids)),
           "removed_items": {**{k: v for k, v in removed.items() if k not in ids},
                             **{k: {**old[k], "section": old_sec.get(k, "")} for k in set(old) - ids}}}
    util.write_json_atomic(p.path("extraction", "form.json"), out)
    p.backup("extraction", "form.json")
    new_f = {x["id"]: x for x in fields(out)}
    p.audit(user, "save extraction form", {"revision": out["rev"], "items": len(ids), "first extractor": first,
                                           "second extractor": second,
                                           "added": [new_f[i]["label"] for i in new_f if i not in old and i not in back][:30],
                                           "removed": [old[i]["label"] for i in old if i not in new_f][:30],
                                           "put back": [new_f[i]["label"] for i in sorted(back)][:30],
                                           "renamed": [f"{old[i]['label']} → {new_f[i]['label']}" for i in new_f
                                                       if i in old and old[i]["label"] != new_f[i]["label"]][:30]})
    return out


def clean_field_id(s) -> str:
    return re.sub(r"[^a-z0-9_]", "", re.sub(r"\s+", "_", str(s or "").strip().lower()))[:40]


# ================================================================================================ studies
def studies(p: Project) -> list:
    """The included studies: [{'key', 'label', 'reports': [record IDs]}], in order of their first report."""
    def work():
        fto = screening.ft_outcomes(p)
        by = p.by_id()
        groups: dict = {}
        for rid, o in sorted(fto.items()):
            if o["status"] != "included":
                continue
            name = (o.get("study") or "").strip()
            groups.setdefault(name.lower() if name else "#" + rid, []).append((rid, name))
        from .reporting import first_author_year       # (here, not at the top: reporting reads this module)
        out = []
        for _, reps in groups.items():
            ids = sorted(r for r, _ in reps)
            name = next((n for _, n in reps if n), "")
            r0 = by.get(ids[0], {})
            out.append({"key": ids[0], "label": name or first_author_year(r0) or ids[0], "reports": ids,
                        "title": r0.get("title", "")})
        return sorted(out, key=lambda s: s["key"])
    return p.memo("x_studies", work)


def study_of(p: Project) -> dict:
    """{record ID: study key} for every included report."""
    return p.memo("x_study_of", lambda: {r: s["key"] for s in studies(p) for r in s["reports"]})


def resolve_key(p: Project, key: str, reports: list | None = None, so: dict | None = None) -> str:
    """The study a key (or any of a study's reports, as they were when a row was written) belongs to now ('' if none):
    answers stay with their study when its first report changes or stops being included."""
    so = study_of(p) if so is None else so
    if key in so:
        return so[key]
    for r in reports or []:
        if r in so:
            return so[r]
    return ""


def resolve_any(p: Project, key: str) -> str:
    """resolve_key, also for a key that is no longer a report of any study (its first report was excluded): the rows
    written under it say which reports the study had."""
    now = resolve_key(p, key)
    if now or not key:
        return now
    so = study_of(p)
    for sub in ("extraction", "rob"):
        for f in util.files_in(p.path(sub), "*.jsonl"):
            for d in p._cached(f"{sub}:{f}", f, lambda f=f: util.read_jsonl(f)):
                if isinstance(d, dict) and d.get("s") == key and d.get("rs"):
                    now = resolve_key(p, key, d["rs"], so)
                    if now:
                        return now
    return ""


def _rows(p: Project, sub: str) -> list:
    """(who, row) for every row of every file in sub, in time order (the rows of one file in the order written: see
    project.written_order), with each row's study key brought up to date.
    Rows for a study that is no longer included keep their old key (and so are not shown)."""
    out = []
    for n, f in enumerate(util.files_in(p.path(sub), "*.jsonl")):
        base = os.path.basename(f)
        who = base.split("@")[0]
        if "@" in base and not re.fullmatch(r"[A-Za-z0-9]+", who):
            continue                # a copy someone made by hand ("Copy of CH@….jsonl"): nobody's file, as for the votes
        rows = p._cached(f"{sub}:{f}", f, lambda f=f: util.read_jsonl(f))
        out += [(t, k, n, who, d) for t, k, d in written_order(rows)]     # one file: in the order written
    out.sort(key=lambda x: x[:3])
    res, so = [], study_of(p)
    for *_, who, d in out:
        now = resolve_key(p, d.get("s"), d.get("rs"), so)
        res.append((who, d if not now or now == d.get("s") else {**d, "s": now}))
    return res


def _write(p: Project, user: str, sub: str, row: dict) -> dict:
    p._heal_own_files()
    name = f"{clean_id(user)}@{p.dev()}.jsonl"
    st = next((s for s in studies(p) if s["key"] == row.get("s")), None)
    row = {"t": util.now(), **row, **({"rs": st["reports"]} if st else {})}
    return integrity.append(p.path(sub, name), row, mirror=p._local("mirror", f"{sub}__{name}"))


def _write_agreed(p: Project, user: str, sub: str, row: dict) -> dict:
    name = f"agreed@{p.dev()}.jsonl"
    st = next((s for s in studies(p) if s["key"] == row.get("s")), None)
    row = {"t": util.now(), **row, "by": user, **({"rs": st["reports"]} if st else {})}
    return integrity.append(p.path(sub, name), row, mirror=p._local("mirror", f"{sub}__{name}"))


def answers(p: Project) -> dict:
    """{'values': {who: {study: {field: row}}}, 'history': {who: {study: {field: [rows]}}}, 'done': {who: {study: row}},
    'agreed': {study: {field: row}}}. Shared: do not change the result."""
    def work():
        vals, hist, done, agreed = {}, {}, {}, {}
        for who, d in _rows(p, "extraction"):
            if who == "agreed":
                agreed.setdefault(d.get("s"), {})[d.get("f")] = d
            elif "done" in d:
                done.setdefault(who, {})[d.get("s")] = d
            elif d.get("f"):
                vals.setdefault(who, {}).setdefault(d.get("s"), {})[d["f"]] = d
                hist.setdefault(who, {}).setdefault(d.get("s"), {}).setdefault(d["f"], []).append(d)
        return {"values": vals, "history": hist, "done": done, "agreed": agreed}
    return p.memo("x_answers", work)


def _is_done(a: dict, who: str, key: str) -> bool:
    return bool((a["done"].get(who, {}).get(key) or {}).get("done"))


def _done_at(a: dict, who: str, key: str) -> float:
    d = a["done"].get(who, {}).get(key) or {}
    return util.ts(d.get("t", "")) if d.get("done") else 0.0


def _norm(v) -> str:
    if isinstance(v, list):
        return "|".join(";".join(f"{k}={_norm(r.get(k))}" for k in sorted(r)) for r in v if isinstance(r, dict) and any(
            str(x).strip() for x in r.values()))
    return " ".join(str("" if v is None else v).split()).lower()


def _value_at(a: dict, who: str, key: str, fid: str, when: float):
    """What someone's answer was at a moment (None if none yet)."""
    v = None
    for d in a["history"].get(who, {}).get(key, {}).get(fid, []):
        if util.ts(d.get("t", "")) <= when:
            v = d.get("v")
    return v


def checked_sig(f: dict, A: dict) -> str:
    """A fingerprint of the first extractor's answers to the checked items, as the second extractor sees them."""
    ids = {x["id"] for x in fields(f) if x["mode"] == "check"} | {k for k, v in (f.get("removed_items") or {}).items()
                                                                   if isinstance(v, dict) and v.get("mode") == "check"}
    return integrity.label(sorted((i, v) for i in ids for v in [_norm(A.get(i, {}).get("v"))] if v))
    # answered items only, removed ones too: a change to the form is not a change of answer


def study_state(p: Project, key: str, viewer: str) -> dict:
    """What a person may see of one study's extraction: their own answers, the other's where allowed, the comparison
    once both have finished, and the agreed values."""
    f = form(p)
    ppl = people(p, f)
    a = answers(p)
    first, second = ppl["first"], ppl["second"]
    A = a["values"].get(first, {}).get(key, {})
    B = a["values"].get(second, {}).get(key, {})
    done_a = _is_done(a, first, key)
    done_b = _is_done(a, second, key)
    solo = not second                          # one extractor only: their answers stand
    both_done = done_a and (done_b or solo)
    checked_at = _done_at(a, second, key)
    checked = ((a["done"].get(second, {}).get(key) or {}).get("checked")) if done_b else None
    mine = a["values"].get(viewer, {}).get(key, {})
    other, seen = None, ""
    if viewer == second and done_a:            # the checker sees the first extractor's 'checked' items
        other = {fid: v for fid, v in A.items() if _mode(f, fid) == "check" or both_done}
        seen = checked_sig(f, A)
    elif viewer == first and both_done:
        other = B
    comparison = []
    if both_done:
        agreed = a["agreed"].get(key, {})
        for fl in fields(f):
            fid = fl["id"]
            va = A.get(fid, {}).get("v")
            brow = B.get(fid)
            corrected = brow is not None and _norm(brow.get("v")) != ""
            if solo or (fl["mode"] == "check" and not corrected):
                status = "checked" if _norm(va) else "empty"
                vb = va if solo else (checked.get(fid) if isinstance(checked, dict) else _value_at(a, first, key, fid, checked_at))
                if not solo and _norm(vb) != _norm(va):
                    status = "changed"         # the first extractor changed it after the check
            else:
                vb = brow.get("v") if brow else None
                status = "same" if _norm(va) == _norm(vb) else "differs"
            final = agreed[fid]["v"] if fid in agreed else (va if status in ("same", "checked") else None)
            comparison.append({"f": fid, "label": fl["label"], "mode": fl["mode"], "a": va, "b": vb, "status": status,
                               "agreed": fid in agreed, "final": final, "pa": A.get(fid, {}).get("pg", ""),
                               "pb": (brow or {}).get("pg", "")})
    return {"first": first, "second": second, "solo": solo, "seen": seen,
            "role": "first" if viewer == first else "second" if viewer == second and second else "",
            "mine": mine, "other": other, "done_first": done_a, "done_second": done_b, "both_done": both_done,
            "comparison": comparison if both_done else [], "agreed": a["agreed"].get(key, {})}


def _mode(f: dict, fid: str) -> str:
    return next((x["mode"] for x in fields(f) if x["id"] == fid), "check")


def _study_key(p: Project, key: str) -> str:
    now = resolve_any(p, key)
    if not now:
        raise ProjectError("That study is not among the included studies (its full-text decision may have changed). "
                           "Reload the page.")
    return now


def save_value(p: Project, user: str, key: str, fid: str, value, where: str = "") -> dict:
    p.require_member(user)
    f = form(p)
    ppl = people(p, f)
    if user not in (ppl["first"], ppl["second"]):
        raise ProjectError("Only the two extractors named on the form record extraction.")
    key = _study_key(p, key)
    fl = next((x for x in fields(f) if x["id"] == fid), None)
    if not fl:
        raise ProjectError("That item is no longer on the form (reload the page).")
    a = answers(p)
    if _is_done(a, user, key):
        raise ProjectError("You have marked this study as finished. Choose 'Reopen' to change an answer.")
    if user == ppl["second"] and fl["mode"] == "check" and not _is_done(a, ppl["first"], key):
        raise ProjectError("Checked items are checked once the first extractor has finished this study.")
    value = _clean_value(fl, value)
    return _write(p, user, "extraction", {"s": key, "f": fid, "v": value, **({"pg": str(where)[:200]} if where else {})})


def _clean_value(fl: dict, v):
    if fl["type"] == "table":
        if not isinstance(v, list):
            raise ProjectError("A table answer must be a list of rows.")
        cols = [c["id"] for c in fl.get("columns", [])]          # in the form's order
        return [{k: str(r.get(k, ""))[:2000] for k in cols} for r in v if isinstance(r, dict)][:200]
    v = "" if v is None else str(v)[:20000]
    if fl["type"] == "number" and v.strip():
        if _number(v) is None:
            raise ProjectError(f"{fl['label']}: give a number (or leave it empty and explain in Where).")
    if fl["type"] in ("choice", "yesno") and v.strip():
        opts = fl.get("options") if fl["type"] == "choice" else YESNO
        if fl.get("options_from") == "robplan":          # an answer given before the choices followed the plan still counts
            opts = list(opts or []) + DESIGN_OPTIONS
        if v not in opts:
            raise ProjectError(f"{fl['label']}: choose one of the options.")
    return v


def set_done(p: Project, user: str, key: str, done: bool, seen: str = "") -> dict:
    """Finish a study (or reopen it). Once both extractors have finished, neither can reopen it: they have seen each
    other's answers, so changes are made by the lead as agreed values."""
    p.require_member(user)
    f = form(p)
    ppl = people(p, f)
    if user not in (ppl["first"], ppl["second"]):
        raise ProjectError("Only the two extractors named on the form record extraction.")
    key = _study_key(p, key)
    a = answers(p)
    first, second = ppl["first"], ppl["second"]
    both = _is_done(a, first, key) and (not second or _is_done(a, second, key))
    if not done and both and second:
        raise ProjectError("You have both finished this study and can see each other's answers, so it cannot be reopened. "
                           "Tell the lead what needs changing: the lead records it as the agreed value.")
    if done and user == second and not _is_done(a, first, key):
        raise ProjectError("Wait until the first extractor has finished this study: you then check their answers.")
    if done and user == second and seen != checked_sig(f, a["values"].get(first, {}).get(key, {})):
        raise ProjectError("The first extractor has changed some answers since you opened this study. Reload the page and "
                           "check them again before finishing.")
    extra = {}
    if done and user == second:
        A = a["values"].get(first, {}).get(key, {})
        extra = {"checked": {x["id"]: A[x["id"]].get("v") for x in fields(f) if x["mode"] == "check" and x["id"] in A}}
    row = _write(p, user, "extraction", {"s": key, "done": bool(done), **extra})
    p.audit(user, "finish extraction of a study" if done else "reopen extraction of a study", {"study": key})
    return row


def agree_value(p: Project, user: str, key: str, fid: str, value) -> dict:
    p.require_lead(user)
    key = _study_key(p, key)
    st = study_state(p, key, user)
    if not st["both_done"]:
        raise ProjectError("Both extractors must finish this study before values are agreed.")
    fl = next((x for x in fields(form(p)) if x["id"] == fid), None)
    if not fl:
        raise ProjectError("That item is no longer on the form.")
    row = _write_agreed(p, user, "extraction", {"s": key, "f": fid, "v": _clean_value(fl, value)})
    p.audit(user, "agree an extracted value", {"study": key, "item": fl["label"]})
    return row


def overview(p: Project, viewer: str) -> dict:
    f = form(p)
    ppl = people(p, f)
    a = answers(p)
    out = []
    for s in studies(p):
        k = s["key"]
        da, db = _is_done(a, ppl["first"], k), _is_done(a, ppl["second"], k)
        diff = 0
        if da and (db or not ppl["second"]):        # once both have finished, the comparison is no secret
            diff = sum(1 for c in study_state(p, k, viewer)["comparison"] if c["status"] in DIFFER and not c["agreed"])
        out.append({**s, "done_first": da, "done_second": db, "to_agree": diff,
                    "started_first": bool(a["values"].get(ppl["first"], {}).get(k)),
                    "started_second": bool(a["values"].get(ppl["second"], {}).get(k)),
                    "rob": rob_status(p, k)})
    keys = {s["key"] for s in studies(p)}
    by = p.by_id()
    orphans = sorted({d.get("s") for who, d in _rows(p, "extraction") if d.get("s") not in keys}
                     | {d.get("s") for who, d in _rows(p, "rob") if d.get("s") not in keys})
    return {"studies": out, "people": ppl, "form": {"name": f.get("name"), "rev": f.get("rev", 0)},
            "orphans": [{"key": o, "title": (by.get(o) or {}).get("title", ""), "study": _study_name(p, o)} for o in orphans if o]}


def _study_name(p: Project, rid: str) -> str:
    """The study name a report was given at full text, from anyone's latest vote on it ('' if none)."""
    V = screening.votes(p)
    res = p.resolutions().get((rid, "ft")) or {}
    names = [res.get("study", "")] + [(screening.latest(V, "ft", rid, w) or {}).get("study", "") for w in V.get("ft", {}).get(rid, {})]
    return next((n for n in names if n), "")


def progress(p: Project) -> dict:
    """How far extraction has got: {'studies', 'finished'} (finished by both extractors)."""
    sts, a, ppl = studies(p), answers(p), people(p)
    fin = sum(1 for s in sts if _is_done(a, ppl["first"], s["key"]) and (not ppl["second"] or _is_done(a, ppl["second"], s["key"])))
    return {"studies": len(sts), "finished": fin}


def final_values(p: Project) -> dict:
    """{study: {field: value}}: agreed values, or what both gave, or (checked items) what the first gave and the
    second confirmed. Empty where nothing is settled yet."""
    out = {}
    lead = p.lead_id()
    for s in studies(p):
        st = study_state(p, s["key"], lead)
        out[s["key"]] = {c["f"]: c["final"] for c in st["comparison"] if c["final"] is not None}
    return out


def _number(v):
    """A number as people write it, or None: 12500, 12,500 and 1,234.5 (commas between thousands), 12.5, and 12,5 (a
    decimal comma, when what follows the comma is not three digits)."""
    s = str(v or "").strip()
    if re.fullmatch(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?", s):
        return float(s.replace(",", ""))
    if re.fullmatch(r"-?\d+,\d+", s):
        return float(s.replace(",", "."))
    if re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        return float(s)
    return None


def _cell(fl_or_col: dict, v):
    """A value for a spreadsheet cell: numbers as numbers."""
    if v is None:
        return ""
    if fl_or_col.get("type") == "number" and isinstance(v, str):
        x = _number(v)
        if x is not None:
            return int(x) if x.is_integer() else x
    return v


def extraction_xlsx(p: Project) -> bytes:
    f = form(p)
    flds = fields(f)
    plain = [x for x in flds if x["type"] != "table"]
    tables = [x for x in flds if x["type"] == "table"]
    fin = final_values(p)
    ppl = people(p, f)
    sts = studies(p)
    a = answers(p)
    sheets = [("Extraction", [["Study", "Reports"] + [x["label"] for x in plain]]
               + [[s["label"], ", ".join(s["reports"])] + [_cell(x, fin.get(s["key"], {}).get(x["id"], "")) if not isinstance(
                   fin.get(s["key"], {}).get(x["id"]), list) else _show(fin[s["key"]][x["id"]], x) for x in plain] for s in sts])]
    used = {"Extraction"}
    sec_of = {x["id"]: sec["title"] for sec in f.get("sections", []) for x in sec.get("fields", [])}
    for t in tables:
        cols = t.get("columns", [])
        rows = [["Study"] + [c["label"] for c in cols]]
        for s in sts:
            v = fin.get(s["key"], {}).get(t["id"])
            for r in (v if isinstance(v, list) else []):
                rows.append([s["label"]] + [_cell(c, r.get(c["id"], "")) for c in cols])
        name = (t["label"] if t["label"] not in ("Results", "Outcomes and results") else f"{sec_of.get(t['id'], '')} {t['label']}")
        name = re.sub(r"[\[\]:*?/\\]", " ", name).strip()[:31] or "Table"
        k = 2
        while name in used:
            name = f"{name[:28]} {k}"
            k += 1
        used.add(name)
        sheets.append((name, rows))
    sources = [["Study", "Item", f"Where ({ppl['first']})", f"Where ({ppl['second']})"]]
    for s in sts:
        for x in flds:
            pa = (a["values"].get(ppl["first"], {}).get(s["key"], {}).get(x["id"]) or {}).get("pg", "")
            pb = (a["values"].get(ppl["second"], {}).get(s["key"], {}).get(x["id"]) or {}).get("pg", "")
            if (pa or pb) and x["id"] in fin.get(s["key"], {}):
                sources.append([s["label"], x["label"], pa, pb])
    status = [["Study", f"{ppl['first']} finished", f"{ppl['second']} finished", "Items still to agree"]]
    disagree = [["Study", "Item", ppl["first"], ppl["second"], "Agreed"]]
    for s in sts:
        st = study_state(p, s["key"], p.lead_id())
        status.append([s["label"], "yes" if st["done_first"] else "no", "yes" if st["done_second"] else "no",
                       sum(1 for c in st["comparison"] if c["status"] in DIFFER and not c["agreed"])])
        for c in st["comparison"]:
            if c["status"] in DIFFER:
                fl = next((x for x in flds if x["id"] == c["f"]), {})
                disagree.append([s["label"], c["label"], _show(c["a"], fl), _show(c["b"], fl), _show(c["final"], fl) if c["agreed"] else ""])
    sheets += [("Sources", sources), ("Status", status), ("Disagreements", disagree),
               ("Form", [["Section", "Item", "Kind", "Who", "Choices or columns", "Help"]] +
                [[sec["title"], x["label"], x["type"], "both independently" if x["mode"] == "both" else "first; second checks",
                  "; ".join(x.get("options", []) or [c["label"] for c in x.get("columns", [])]), x.get("help", "")]
                 for sec in f.get("sections", []) for x in sec.get("fields", [])])]
    return util.xlsx(sheets)


def _show(v, fl: dict | None = None) -> str:
    if isinstance(v, list):
        labels = {c["id"]: c["label"] for c in (fl or {}).get("columns", [])}
        order = [c["id"] for c in (fl or {}).get("columns", [])]
        return " | ".join("; ".join(f"{labels.get(k, k)}: {r.get(k)}" for k in (order or list(r)) if str(r.get(k, "")).strip())
                          for r in v if isinstance(r, dict))
    return "" if v is None else str(v)


# ================================================================================================ risk of bias
# The tools (built-in, and those the project lead defines) are in robtools.py; which tool each study design uses is
# the risk-of-bias plan (robplan.py). These names stay here for older callers.
TOOLS = robtools.TOOLS
SEVERITY = robtools.SEVERITY
QUALIFIED_LOW = robtools.QUALIFIED_LOW
scale_for = robtools.scale_for
OVERALL_RULES = {k: (t["overall"], t["rule"]) for k, t in TOOLS.items()}
MORE_SEVERE_EXAMPLE = {k: t["more_severe"] for k, t in TOOLS.items()}


def tool_def(p: Project, tool: str) -> dict:
    return robplan.tool_def(p, tool)


def overall_problems(tool, js: dict, support: dict | None = None, p: Project | None = None, domains: list | None = None) -> list:
    """See robtools.overall_problems. tool is a tool (as tool_def gives it) or a built-in tool's key."""
    t = tool if isinstance(tool, dict) else (robplan.tool_def(p, tool) if p is not None else TOOLS[tool])
    return robtools.overall_problems(t, js, support, domains)


def rob_rows(p: Project) -> dict:
    """{'defs': {(study, a): def}, 'j': {who: {(study, a, domain): row}}, 'done': {who: {(study, a): bool}},
    'agreed': {(study, a, domain): row}}. Shared: do not change the result."""
    def work():
        defs, j, done, agreed = {}, {}, {}, {}
        for who, d in _rows(p, "rob"):
            k = (d.get("s"), d.get("a"))
            if who == "agreed":
                agreed[(d.get("s"), d.get("a"), d.get("d"))] = d
            elif "def" in d:                     # a later row renames or removes it; it keeps its place and maker
                was = defs.get(k) or {}
                defs[k] = {**d["def"], "by": was.get("by", who), "t": was.get("t", d.get("t"))}
            elif "done" in d:
                done.setdefault(who, {})[k] = bool(d["done"])
            elif d.get("d"):
                j.setdefault(who, {})[(d.get("s"), d.get("a"), d["d"])] = d
        return {"defs": {k: v for k, v in defs.items() if not v.get("deleted")}, "j": j, "done": done, "agreed": agreed}
    return p.memo("rob_rows", work)


def rob_people(p: Project) -> list:
    pl = screening.plan(p, "main")
    return [x for x in (pl.get("reviewers") or [])][:2]


def rob_status(p: Project, key: str) -> dict:
    R = rob_rows(p)
    a, b = (rob_people(p) + ["", ""])[:2]
    assessments = [k[1] for k in R["defs"] if k[0] == key]
    done = sum(1 for x in assessments if R["done"].get(a, {}).get((key, x)) and (R["done"].get(b, {}).get((key, x)) or not b))
    return {"assessments": len(assessments), "both_done": done}


def _both_done(R: dict, a: str, b: str, key: str, aid: str) -> bool:
    return bool(R["done"].get(a, {}).get((key, aid)) and (R["done"].get(b, {}).get((key, aid)) or not b))


def _extra_codes(R: dict, t: dict, key: str, aid: str, whose: list) -> list:
    """Domains judged in this assessment that its tool no longer has (RoB 2's cluster domain D1b, from before 1.3.0):
    [(code, name)], from the judgements of the people listed (and the agreed ones if 'agreed' is listed)."""
    own = {c for c, _ in t["domains"]}
    names = dict(t.get("legacy") or [])
    codes = []
    for who in whose:
        rows = R["agreed"] if who == "agreed" else R["j"].get(who, {})
        for (s, a, c), row in rows.items():
            if s == key and a == aid and c not in own and c not in codes and (row.get("j") or who != "agreed"):
                codes.append(c)
    return [(c, names.get(c, f"{c} (a domain this tool no longer has)")) for c in sorted(codes)]


def define_assessment(p: Project, user: str, key: str, design: str, label: str, aid: str = "", deleted: bool = False) -> dict:
    """Add an assessment (one result of a study, judged with the tool the risk-of-bias plan gives for the study's
    design), or rename or remove one. Reviewers choose the design, never the tool. Its design can change only before
    anyone has judged it; it can be removed until both reviewers have finished it (the lead may always)."""
    p.require_member(user)
    key = _study_key(p, key)
    if not label.strip():
        raise ProjectError("Say which result this assessment is for (for example: pain at 6 weeks, exercise vs usual care).")
    R = rob_rows(p)
    a, b = (rob_people(p) + ["", ""])[:2]
    cur = R["defs"].get((key, aid)) if aid else None
    if aid and not cur:
        raise ProjectError("That assessment no longer exists (reload the page).")
    keep = cur is not None and (not design or design == cur.get("design", ""))
    if keep:                                       # renamed or removed: its design and tool stay as they are
        tool, design, dname = cur["tool"], cur.get("design", ""), cur.get("design_name", "")
    else:
        if not robplan.recorded(p):
            raise ProjectError("The risk-of-bias plan has not been recorded yet, so no assessment can be added. The project "
                               "lead records it in Project set-up (Risk-of-bias plan): which tool is used for each study "
                               "design, as the protocol says.")
        if not design:
            raise ProjectError("Choose the study's design from the risk-of-bias plan: the tool follows from it.")
        d = robplan.design(p, design)
        if not d:
            raise ProjectError("That study design is not in the risk-of-bias plan, so it cannot be assessed. If the review "
                               "includes studies of this design, the project lead adds it to the plan (Project set-up, "
                               "Risk-of-bias plan); once screening has started, that is a protocol amendment, with its "
                               "reason.")
        if d.get("none"):
            raise ProjectError(f"The risk-of-bias plan says studies of this design ({d['name']}) have no risk-of-bias "
                               f"assessment: {d.get('reason', '')}. If this study needs one, the project lead changes the "
                               "plan (Project set-up, Risk-of-bias plan).")
        tool, dname = d["tool"], d["name"]
        if cur is not None:
            started = any(k[0] == key and k[1] == aid for who in R["j"] for k in R["j"][who])
            if started and cur["tool"] != tool:
                raise ProjectError("Judgements have been recorded for this assessment, so its design and tool cannot change. "
                                   "Add a new one.")
    if cur is not None and _both_done(R, a, b, key, aid) and not p.is_lead(user):
        raise ProjectError("Both reviewers have finished this assessment: only the lead can rename or remove it.")
    aid = aid or ("a" + util.sha256_text(key + label + tool + util.now() + user)[:6])
    row = _write(p, user, "rob", {"s": key, "a": aid, "def": {"tool": tool, "design": design, "design_name": dname,
                                                              "label": label.strip()[:200], "deleted": bool(deleted)}})
    p.audit(user, "remove a risk-of-bias assessment" if deleted else "add or rename a risk-of-bias assessment",
            {"study": key, "assessment": label.strip()[:200], "design": dname, "tool": tool})
    return row


def judge(p: Project, user: str, key: str, aid: str, domain: str, judgement: str, support: str = "") -> dict:
    p.require_member(user)
    if user not in rob_people(p):
        raise ProjectError("Only the two reviewers judge risk of bias.")
    key = _study_key(p, key)
    R = rob_rows(p)
    d = R["defs"].get((key, aid))
    if not d:
        raise ProjectError("That assessment no longer exists (reload the page).")
    t = tool_def(p, d["tool"])
    mine_extra = [c for c, _ in _extra_codes(R, t, key, aid, [user])]
    if domain not in [x[0] for x in t["domains"]] + mine_extra:
        raise ProjectError("Unknown domain.")
    if judgement not in scale_for(t, domain) + [""]:
        raise ProjectError("Choose one of the judgements.")
    if R["done"].get(user, {}).get((key, aid)):
        raise ProjectError("You have marked this assessment as finished. Choose 'Reopen' to change a judgement.")
    return _write(p, user, "rob", {"s": key, "a": aid, "d": domain, "j": judgement, "q": str(support)[:4000]})


def rob_done(p: Project, user: str, key: str, aid: str, done: bool) -> dict:
    p.require_member(user)
    if user not in rob_people(p):
        raise ProjectError("Only the two reviewers judge risk of bias.")
    key = _study_key(p, key)
    R = rob_rows(p)
    d = R["defs"].get((key, aid))
    if not d:
        raise ProjectError("That assessment no longer exists (reload the page).")
    a, b = (rob_people(p) + ["", ""])[:2]
    both = _both_done(R, a, b, key, aid)
    if done:
        t = tool_def(p, d["tool"])
        mine = R["j"].get(user, {})
        missing = [name for code, name in t["domains"] if not (mine.get((key, aid, code)) or {}).get("j")]
        if missing:
            raise ProjectError("Give a judgement for every domain first: " + "; ".join(missing) + ".")
        off = [f"{code} {name}: “{(mine.get((key, aid, code)) or {}).get('j')}”" for code, name in t["domains"]
               if (mine.get((key, aid, code)) or {}).get("j") not in scale_for(t, code)]
        if off:
            raise ProjectError(f"These judgements are not on {t.get('short', t['name'])}'s scale for their domain now: "
                               + "; ".join(off) + ". Choose them again.")
        every = t["domains"] + _extra_codes(R, t, key, aid, [user])
        js = {code: (mine.get((key, aid, code)) or {}).get("j", "") for code, _ in every}
        qs = {code: (mine.get((key, aid, code)) or {}).get("q", "") for code, _ in every}
        wrong = robtools.overall_problems(t, js, qs, every)
        if wrong:
            raise ProjectError(" ".join(wrong))
    elif both and b:
        raise ProjectError("You have both finished this assessment and can see each other's judgements, so it cannot be "
                           "reopened. Tell the lead what needs changing: the lead records the agreed judgement.")
    row = _write(p, user, "rob", {"s": key, "a": aid, "done": bool(done)})
    p.audit(user, "finish a risk-of-bias assessment" if done else "reopen a risk-of-bias assessment",
            {"study": key, "assessment": d.get("label", "")})
    return row


def rob_view(p: Project, key: str, viewer: str, settled_only: bool = False) -> list:
    """The assessments of a study as one person may see them: the other's judgements only once both have finished.
    settled_only: compare the design with the extracted design only once extraction has settled it (for files that
    anyone may download)."""
    R = rob_rows(p)
    a, b = (rob_people(p) + ["", ""])[:2]
    out = []
    for (s, aid), d in sorted(R["defs"].items(), key=lambda kv: kv[1].get("t", "")):
        if s != key:
            continue
        t = tool_def(p, d["tool"])
        both = _both_done(R, a, b, key, aid)
        extra = _extra_codes(R, t, key, aid, [a, b, "agreed"] if both else [viewer] if viewer in (a, b) else [])
        doms = []
        for code, name in t["domains"] + extra:
            ja, jb = R["j"].get(a, {}).get((key, aid, code)), R["j"].get(b, {}).get((key, aid, code))
            ag = R["agreed"].get((key, aid, code))
            mine = ja if viewer == a else jb if viewer == b else None
            row = {"d": code, "name": name, "scale": scale_for(t, code), "legacy": code in dict(extra),
                   "overall": code in robtools.OVERALL_CODES,
                   "mine": {"j": mine.get("j", ""), "q": mine.get("q", "")} if mine else None}
            if both:
                row.update({"a": (ja or {}).get("j", ""), "qa": (ja or {}).get("q", ""), "b": (jb or {}).get("j", ""),
                            "qb": (jb or {}).get("q", ""), "agreed": (ag or {}).get("j")})
                if not b:
                    row["b"], row["qb"] = row["a"], ""
                row["final"] = (row["agreed"] or (row["a"] if row["a"] == row["b"] else None)) or None
                if row["a"] != row["b"] and "No information" not in (row["a"], row["b"]) and row["a"] and row["b"]:
                    sa, sb = robtools.sev(t, row["a"]), robtools.sev(t, row["b"])
                    if sa is not None and sb is not None:
                        row["severer"] = row["a"] if sa >= sb else row["b"]
                if row["agreed"] and row["a"] and row["a"] == row["b"] and row["agreed"] != row["a"]:
                    row["override"] = {"from": row["a"], "why": (ag or {}).get("why", ""), "by": (ag or {}).get("by", "")}
            doms.append(row)
        finals = {x["d"]: x.get("final") for x in doms}
        need = [c for c, _ in t["domains"]]
        problems = (robtools.overall_problems(t, finals, None, t["domains"] + extra)
                    if both and all(finals.get(c) for c in need) else [])
        warn = robplan.warning(p, key, d.get("design", ""), d["tool"], "" if settled_only else viewer)
        stale = robplan.superseded(p, d)
        out.append({"a": aid, "tool": d["tool"], "tool_name": t["name"], "tool_short": t.get("short", t["name"]),
                    "scale": t["scale"], "label": d["label"], "design": d.get("design", ""),
                    "design_name": d.get("design_name", ""), "problems": problems, "warning": warn, "superseded": stale,
                    "link": t.get("link"), "help": t.get("help", ""), "rule": t.get("rule", ""), "custom": bool(t.get("custom")),
                    "done_mine": bool(R["done"].get(viewer, {}).get((key, aid))), "done_a": bool(R["done"].get(a, {}).get((key, aid))),
                    "done_b": bool(R["done"].get(b, {}).get((key, aid))), "both_done": bool(both), "domains": doms,
                    "reviewers": [a, b]})
    return out


def rob_agree(p: Project, user: str, key: str, aid: str, domain: str, judgement: str, why: str = "") -> dict:
    """The lead records the agreed judgement for a domain. Where both reviewers gave the same judgement, a different
    agreed one changes what they agreed on: it needs its reason, which the risk-of-bias tables print."""
    p.require_lead(user)
    key = _study_key(p, key)
    view = next((x for x in rob_view(p, key, user) if x["a"] == aid), None)
    if not view or not view["both_done"]:
        raise ProjectError("Both reviewers must finish this assessment before judgements are agreed.")
    t = tool_def(p, view["tool"])
    dm = next((x for x in view["domains"] if x["d"] == domain), None)
    if not dm or judgement not in scale_for(t, domain):
        raise ProjectError("Choose one of the judgements.")
    why = " ".join(str(why or "").split())[:1000]
    changed = bool(dm.get("a")) and dm.get("a") == dm.get("b") and judgement != dm.get("a")
    if changed and len(why) < 5:
        raise ProjectError(f"Both reviewers judged {domain} {dm['name']} as “{dm['a']}”. To record a different agreed "
                           "judgement, give the reason: it is printed under the risk-of-bias tables and counted in the "
                           "methods.")
    row = _write_agreed(p, user, "rob", {"s": key, "a": aid, "d": domain, "j": judgement, **({"why": why} if changed else {})})
    p.audit(user, "agree a risk-of-bias judgement", {"study": key, "assessment": view["label"], "domain": domain,
                                                     "judgement": judgement,
                                                     **({"changed what both reviewers gave": dm["a"], "reason": why} if changed else {})})
    return row


def rob_table(p: Project) -> list:
    """Every assessment with its final judgements (agreed, or the same from both): rows for the summary."""
    lead = p.lead_id()
    out = []
    for s in studies(p):
        for v in rob_view(p, s["key"], lead, settled_only=True):
            out.append({"study": s["label"], "key": s["key"], "a": v["a"], "label": v["label"], "tool": v["tool"],
                        "tool_name": v["tool_name"], "tool_short": v["tool_short"], "design": v["design"],
                        "design_name": v["design_name"], "both_done": v["both_done"], "problems": v["problems"],
                        "warning": v["warning"], "superseded": v["superseded"],
                        "domains": [(d["d"], d["name"], d.get("final")) for d in v["domains"]],
                        "overrides": [(d["d"], d["name"], d["override"]["from"], d["final"], d["override"]["why"])
                                      for d in v["domains"] if d.get("override")]})
    return out


def _columns(t: dict, rows: list) -> list:
    """A tool's domains, with any kept domains (D1b) that these rows have, before the overall judgements."""
    own = [c for c, _ in t["domains"]]
    extra = []
    for r in rows:
        for code, name, j in r["domains"]:
            if code not in own and code not in [c for c, _ in extra]:
                extra.append((code, name))
    if not extra:
        return list(t["domains"])
    head = [x for x in t["domains"] if x[0] not in robtools.OVERALL_CODES]
    return head + extra + [x for x in t["domains"] if x[0] in robtools.OVERALL_CODES]


def _tool_order(p: Project, rows: list) -> list:
    """[(tool key, tool)] for the tools these rows use: the built-in ones in their order, then the lead's."""
    used = []
    for r in rows:
        if r["tool"] not in used:
            used.append(r["tool"])
    order = [k for k in TOOLS if k in used] + [k for k in used if k not in TOOLS]
    return [(k, tool_def(p, k)) for k in order]


def _sheet_name(name: str, used: set) -> str:
    base = re.sub(r"[\[\]:*?/\\]", " ", name).strip()[:31] or "Tool"
    out, k = base, 2
    while out in used:
        out = f"{base[:28]} {k}"
        k += 1
    used.add(out)
    return out


SHEET = {"rob2_cluster": "RoB 2 cluster trials", "rob2_crossover": "RoB 2 crossover trials"}


def rob_xlsx(p: Project) -> bytes:
    sheets = []
    rows = rob_table(p)
    used = set()
    for tool, t in _tool_order(p, rows):
        mine = [r for r in rows if r["tool"] == tool]
        cols = _columns(t, mine)
        head = ["Study", "Study design", "Result assessed"] + [f"{c}: {n}" for c, n in cols]
        body = []
        for r in mine:
            js = {c: j for c, _, j in r["domains"]}
            body.append([r["study"], r["design_name"] or "(not recorded)", r["label"]]
                        + [js.get(c) or ("(not yet agreed)" if r["both_done"] and c in js else "(not finished)" if c in js else "")
                           for c, _ in cols])
        sheets.append((_sheet_name(SHEET.get(tool, t.get("short") or t["name"]), used), [head] + body))
    lead = p.lead_id()
    a, b = (rob_people(p) + ["", ""])[:2]
    detail = [["Study", "Result", "Tool", "Domain", f"{a} judgement", f"{a} support", f"{b} judgement", f"{b} support", "Agreed",
               "Why the lead changed a judgement both gave"]]
    for s in studies(p):
        for v in rob_view(p, s["key"], lead, settled_only=True):
            for d in v["domains"]:
                if v["both_done"]:
                    detail.append([s["label"], v["label"], v["tool_name"], f"{d['d']} {d['name']}", d.get("a", ""), d.get("qa", ""),
                                   d.get("b", ""), d.get("qb", ""), d.get("agreed") or "", (d.get("override") or {}).get("why", "")])
    sheets.append(("Both reviewers", detail))
    sheets.append(("Plan and tools", plan_rows(p, rows)))
    return util.xlsx(sheets)


def plan_rows(p: Project, rows: list | None = None) -> list:
    """The risk-of-bias plan and every tool used, with its citation, domains and judgements (the key)."""
    pl = robplan.plan(p)
    changed, gone = robplan.changes_by_design(p)
    amended = bool(robplan.amendments(p))
    out = [["Risk-of-bias plan" + ("" if pl.get("recorded") else " (not recorded yet)"), "", ""],
           ["Study design", "Tool", "Citation or reason"] + (["Changed by protocol amendment"] if amended else [])]
    for d in list(pl.get("designs") or []) + gone:
        if d.get("none"):
            row = [d["name"], "No risk-of-bias assessment", d.get("reason", "")]
        else:
            t = tool_def(p, d["tool"])
            ver = robtools.version_words(t.get("version_given"), start=True)
            row = [d["name"], t["name"], f"{robtools.end_sentence(t.get('cite', ''))} {ver}.".strip() if ver else t.get("cite", "")]
        out.append(row + ([changed.get(d["id"]) or ("Removed" if d in gone else "")] if amended else []))
    out += [["", "", ""], ["Tool", "Domains", "Judgements (mark in the figure)"]]
    rows = rows if rows is not None else rob_table(p)
    keys = [k for k, _ in _tool_order(p, rows)]
    for d in pl.get("designs") or []:
        if d.get("tool") and d["tool"] not in keys:
            keys.append(d["tool"])
    for k in keys:
        t = tool_def(p, k)
        mk = robtools.marks(t)
        cols = _columns(t, [r for r in rows if r["tool"] == k])       # with a domain kept from before 1.3.0, if any uses it
        out.append([t["name"] + (f"; ReviewDesk records {t['domains_from']}" if t.get("domains_from") else ""),
                    "; ".join(f"{c}: {n}" for c, n in cols),
                    "; ".join(f"{j} ({mk.get(j, '')})" for j in robtools.every_judgement(t, [c for c, _ in cols]))])
    return out


def _wrap(text: str, width: int) -> list:
    lines, cur = [], ""
    for w in str(text).split():
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    return lines + ([cur] if cur else [])


COLOURS = {0: "#2E7D4F", 0.5: "#7FB58F", 1: "#E0B021", 2: "#C0392B", 3: "#5B1A14", -1: "#BBBBBB", -0.5: "#8A8A8A"}
NEUTRAL = "#4F6D8A"                         # a lead's answers that are not in order of concern


def colour(sv) -> str:
    """The colour for a severity: the nearest of the usual ones; neutral when the scale has no order."""
    if sv is None:
        return NEUTRAL
    return COLOURS[min(COLOURS, key=lambda k: (abs(k - sv), -k))]


def rob_svg(p: Project) -> str:
    """A traffic-light summary (one block per tool): rows are results, columns are domains, with a key under each that
    gives each mark's judgement in the tool's own words."""
    import html
    rows = rob_table(p)
    out, y = [], 20
    blocks = [(t, [r for r in rows if r["tool"] == k]) for k, t in _tool_order(p, rows)]
    blocks = [(t, m, _columns(t, m)) for t, m in blocks if m]
    X0, LW = 330, 54                       # where the first column of circles is; their spacing
    W = max([X0 + LW * len(cols) for _, _, cols in blocks] + [640]) + 10
    chars = int((W - 20) / 6.2)
    for t, mine, cols in blocks:
        mk = robtools.marks(t)
        out.append(f'<text x="10" y="{y + 14}" font-weight="bold">{html.escape(t["name"])}</text>')
        y += 26
        for k, (code, name) in enumerate(cols):
            out.append(f'<text x="{X0 + LW * k}" y="{y + 10}" text-anchor="middle" font-size="11">{html.escape(code)}</text>')
        y += 18
        for r in mine:
            label = _wrap(f'{r["study"]}: {r["label"]}', 50)[:2]
            for i, line in enumerate(label):
                out.append(f'<text x="10" y="{y + 11 + 13 * i}" font-size="11.5">{html.escape(line)}</text>')
            js = {c: j for c, _, j in r["domains"]}
            for k, (code, _) in enumerate(cols):
                j = js.get(code)
                cx = X0 + LW * k
                if code not in js:
                    continue
                if not j:
                    out.append(f'<circle cx="{cx}" cy="{y + 11}" r="12" fill="#fff" stroke="#999" stroke-dasharray="3 2"/>')
                else:
                    m = mk.get(j, "")
                    fs = 11 if len(m) < 3 else 9
                    out.append(f'<circle cx="{cx}" cy="{y + 11}" r="12" fill="{colour(robtools.sev(t, j))}"/>'
                               f'<text x="{cx}" y="{y + 15}" text-anchor="middle" font-size="{fs}" fill="#fff" '
                               f'font-weight="bold">{html.escape(m)}</text>')
            y += 30 if len(label) < 2 else 36
        key = [f"{code} {name}" for code, name in cols if code not in robtools.OVERALL_CODES]
        key += [{"O": "O overall judgement", "AO": "AO overall applicability"}[c] for c, _ in cols if c in robtools.OVERALL_CODES]
        for line in _wrap("; ".join(key) + ".", chars):
            out.append(f'<text x="10" y="{y + 10}" font-size="10.5" fill="#444">{html.escape(line)}</text>')
            y += 14
        legend = ("Key: " + "; ".join(f"{mk[j]} {j[:1].lower() + j[1:] if not t.get('custom') else j}"
                                      for j in robtools.every_judgement(t, [c for c, _ in cols]) if j in mk)
                  + "; dashed circle: not yet finished by both, or not yet agreed."
                  + (" The answers are not in order of concern, so every circle has the same colour." if t.get("custom")
                     and not t.get("ordered") else "") + ("" if any(c in robtools.OVERALL_CODES for c, _ in cols) else
                                          " This tool has no overall judgement."))
        for line in _wrap(legend, chars):
            out.append(f'<text x="10" y="{y + 10}" font-size="10.5" fill="#444">{html.escape(line)}</text>')
            y += 14
        y += 20
    H = max(y, 60)
    if not blocks:
        out.append('<text x="10" y="30">No risk-of-bias assessments yet.</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
            f'font-family="Arial, Helvetica, sans-serif" font-size="12"><rect width="{W}" height="{H}" fill="#fff"/>'
            + "".join(out) + "</svg>")
