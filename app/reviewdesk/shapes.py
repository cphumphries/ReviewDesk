"""The shape of what the pages send. Every request that changes something is checked here, where it arrives
(server.Handler._post), before anything is saved: each field must be of the kind the page sends (text, a number, yes or
no, a list or a group of fields), an ID is text, and a record set is one of the project's sets. A request of the wrong
shape (sent by a script, a browser extension, or a page with a bug) is refused with a 400 and plain words, and nothing
is saved; what it would otherwise have stored goes into append-only files that sync to the whole team, and one bad
value there could stop every page from opening, for everyone.

A field set to null counts as left out. Fields the table does not name are left to the handler (most ignore them).
Text anywhere in a request must be text a file can hold: no lone surrogates and no NUL characters."""
from __future__ import annotations

from .project import DEFAULT_SETTINGS, SETS


class BadRequest(ValueError):
    """A request ReviewDesk cannot use, with what is wrong in plain words."""


TEXT, REQ_TEXT, BOOL, INT, NUM, SET, ANY = "text", "required text", "yes or no", "whole number", "number", "set", "any"
LIST_TEXT, DICT, LIST_DICT, PAIRS, DICT_OF_DICT = "list of text", "group", "list of groups", "pairs", "groups"
MAX_DEPTH = 32                       # the deepest a request may nest (a lead-defined tool's scale is about six levels)


# what each field of each route must be (routes not listed get only the checks every request gets)
ROUTES: dict = {
    "/api/project/open": {"path": TEXT},
    "/api/project/create": {"parent": TEXT, "name": TEXT, "title": TEXT, "lead_name": TEXT, "lead_id": TEXT},
    "/api/project/close": {},
    "/api/me": {"reviewer": TEXT},
    "/api/user": {"my_name": TEXT, "unpaywall_email": TEXT, "elm_key": TEXT, "elm_url": TEXT},
    "/api/settings": {"changes": "settings"},
    "/api/worksheet": {"rev": INT},
    "/api/searchlog": {"id": TEXT, "by": TEXT, "database": TEXT, "platform": TEXT, "date": TEXT, "coverage": TEXT,
                       "string": TEXT, "limits": TEXT, "results": (TEXT, INT), "notes": TEXT, "deleted": BOOL},
    "/api/imports/remove": {"id": TEXT},
    "/api/imports/link": {"id": TEXT, "search_id": TEXT},
    "/api/imports/process": {},
    "/api/dedup/notdup": {"kept": TEXT, "other": TEXT, "pairs": PAIRS},
    "/api/dedup/merge": {"keep": TEXT, "drop": TEXT, "pairs": PAIRS},
    "/api/screening/start": {"set": SET},
    "/api/screening/calibration": {"set": SET, "another": BOOL},
    "/api/screening/apply": {"set": SET, "drop_ai": TEXT},
    "/api/screening/verify": {"set": SET, "size": INT, "decoys": INT, "reviewer": TEXT, "as": TEXT},
    "/api/screening/verify/end": {"set": SET, "reason": TEXT, "as": TEXT},
    "/api/timing/mine": {"on": BOOL, "as": TEXT},
    "/api/decide": {"record": REQ_TEXT, "stage": TEXT, "decision": TEXT, "reason": TEXT, "note": TEXT, "study": TEXT,
                    "as": TEXT, "time": {"shown": NUM, "active": NUM, "quiet": NUM, "views": NUM}, "finish": BOOL},
    "/api/resolve": {"record": REQ_TEXT, "stage": TEXT, "decision": TEXT, "reason": TEXT, "note": TEXT, "study": TEXT,
                     "as": TEXT},
    "/api/ai/config": "ai config",
    "/api/ai/models": {},
    "/api/ai/run": {"scope": TEXT},
    "/api/ai/stop": {},
    "/api/fulltext/fetch": {"ids": LIST_TEXT},
    "/api/fulltext/stop": {},
    "/api/fulltext/assign": {"name": TEXT, "record": TEXT, "replace": BOOL, "same_ok": BOOL},
    "/api/fulltext/discard": {"name": TEXT},
    "/api/fulltext/remove": {"record": TEXT, "sha": TEXT},
    "/api/sync/use": {"copy": TEXT, "suffix": TEXT},
    "/api/sync/keep": {"copy": TEXT, "suffix": TEXT},
    "/api/sync/dismiss": {},
    "/api/restore": {"file": TEXT, "copy": TEXT, "older_ok": BOOL},
    "/api/integrity/fix": {"kind": TEXT, "file": TEXT, "record": TEXT, "reason": TEXT, "sig": TEXT},
    "/api/extract/form": {"template": TEXT, "first": TEXT, "second": TEXT, "form": DICT},
    "/api/extract/value": {"key": TEXT, "f": TEXT, "v": ANY, "pg": TEXT},
    "/api/extract/done": {"key": TEXT, "done": BOOL, "seen": TEXT},
    "/api/extract/agree": {"key": TEXT, "f": TEXT, "v": ANY},
    "/api/rob/define": {"key": TEXT, "design": TEXT, "label": TEXT, "a": TEXT, "deleted": BOOL},
    "/api/rob/judge": {"key": TEXT, "a": TEXT, "d": TEXT, "j": TEXT, "q": TEXT},
    "/api/rob/done": {"key": TEXT, "a": TEXT, "done": BOOL},
    "/api/rob/agree": {"key": TEXT, "a": TEXT, "d": TEXT, "j": TEXT, "why": TEXT},
    "/api/rob/plan": {"plan": {"designs": LIST_DICT, "tools": DICT_OF_DICT}, "reason": TEXT},
    "/api/certainty/define": {"outcome": {"name": TEXT, "importance": TEXT, "kind": TEXT, "synthesis": TEXT, "result": TEXT,
                                          "studies": LIST_TEXT, "comparison": TEXT, "measure": TEXT, "participants": TEXT},
                              "o": TEXT, "deleted": BOOL},
    "/api/certainty/rate": {"o": TEXT, "f": TEXT, "v": ANY, "x": TEXT, "as": TEXT},
    "/api/certainty/done": {"o": TEXT, "done": BOOL, "as": TEXT},
    "/api/certainty/agree": {"o": TEXT, "f": TEXT, "v": ANY, "x": TEXT, "why": TEXT, "as": TEXT},
    "/api/certainty/keep_statement": {"o": TEXT, "x": TEXT, "as": TEXT},
    "/api/certainty/accuracy": {"figures": DICT, "as": TEXT},
}

# The settings: each must have the kind of its default (project.DEFAULT_SETTINGS), all the way down. What a list
# holds, and a few settings with no default of that kind, are given here.
SETTING_ITEMS = {"reviewers": {"id": TEXT, "name": TEXT, "role": TEXT},
                 "known_studies": {"doi": (TEXT, INT), "pmid": (TEXT, INT), "title": TEXT},
                 "registers": {"name": TEXT, "records": (NUM, TEXT)}}
SETTING_ANY = {("screening", "seed"): (INT, TEXT)}
NOT_SETTINGS = ("tool", "schema")          # written by ReviewDesk itself
PASSED_ON = ("risk_of_bias_plan",)         # refused by project.update_settings with its own words

AI_PASS = {"model": TEXT, "reasoning": TEXT, "temperature": NUM, "max_tokens": INT}
AI_CONFIG = ("passes", "concurrency", "allow_closed_models", "base_url")


def _name(path: list) -> str:
    return ".".join(str(x) for x in path)


def _bad(path: list, want: str) -> BadRequest:
    return BadRequest(f"ReviewDesk could not read that request: “{_name(path)}” should be {_A.get(want, want)}. Reload "
                      "the page and try again; nothing was saved.")


_A = {TEXT: "text", REQ_TEXT: "text", BOOL: "yes or no (true or false)", INT: "a whole number", NUM: "a number",
      SET: "one of the record sets (" + ", ".join(SETS) + ")", LIST_TEXT: "a list of text", DICT: "a group of fields",
      LIST_DICT: "a list of groups of fields", DICT_OF_DICT: "a group of groups of fields", PAIRS: "a list of pairs of record IDs"}


def _is(v, kind) -> bool:
    if isinstance(kind, tuple):
        return any(_is(v, k) for k in kind)
    if kind == ANY:
        return True
    if kind in (TEXT, REQ_TEXT):
        return isinstance(v, str)
    if kind == BOOL:
        return isinstance(v, bool)
    if kind == INT:
        return isinstance(v, int) and not isinstance(v, bool)
    if kind == NUM:
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if kind == SET:
        return isinstance(v, str) and v in SETS
    if kind == LIST_TEXT:
        return isinstance(v, list) and all(isinstance(x, str) for x in v)
    if kind == DICT:
        return isinstance(v, dict)
    if kind == LIST_DICT:
        return isinstance(v, list) and all(isinstance(x, dict) for x in v)
    if kind == DICT_OF_DICT:
        return isinstance(v, dict) and all(isinstance(x, dict) for x in v.values())
    if kind == PAIRS:
        return isinstance(v, list) and all(isinstance(x, list) and len(x) == 2 and all(isinstance(y, str) for y in x) for x in v)
    raise AssertionError(kind)


def _text_ok(body, depth: int = 0) -> None:
    """Every piece of text (keys too) can be written to a file; nothing nests deeper than MAX_DEPTH."""
    if depth > MAX_DEPTH:
        raise BadRequest("ReviewDesk could not read that request: it is nested far deeper than any page sends. Reload "
                         "the page and try again; nothing was saved.")
    items = body.items() if isinstance(body, dict) else enumerate(body) if isinstance(body, list) else ()
    for k, v in items:
        for s in (k, v):
            if isinstance(s, str) and ("\x00" in s or any("\ud800" <= ch <= "\udfff" for ch in s)):
                raise BadRequest("ReviewDesk could not read that request: some of its text holds characters that cannot "
                                 "be saved (a broken character from a copy and paste, perhaps). Type that text again; "
                                 "nothing was saved.")
        if isinstance(v, (dict, list)):
            _text_ok(v, depth + 1)


def _fields(body: dict, spec: dict, path: list) -> None:
    for k, kind in spec.items():
        if k not in body:
            if kind == REQ_TEXT:
                raise BadRequest(f"ReviewDesk could not read that request: it does not say which {k} it is about. Reload "
                                 "the page and try again; nothing was saved.")
            continue
        v = body[k]
        if v is None and kind != REQ_TEXT:
            continue
        if kind == "settings":
            settings(v, path + [k])
        elif isinstance(kind, dict):
            if not isinstance(v, dict):
                raise _bad(path + [k], DICT)
            _fields(v, kind, path + [k])
        elif not _is(v, kind):
            raise _bad(path + [k], kind if not isinstance(kind, tuple) else kind[0])
        elif kind == REQ_TEXT and not v.strip():
            raise BadRequest(f"ReviewDesk could not read that request: it does not say which {k} it is about. Reload "
                             "the page and try again; nothing was saved.")


def settings(changes, path: list | None = None) -> None:
    """Settings sent to /api/settings: each known setting has the kind of its default, all the way down."""
    path = path or ["changes"]
    if not isinstance(changes, dict):
        raise _bad(path, DICT)
    for k, v in changes.items():
        if k in PASSED_ON:
            continue
        if k not in DEFAULT_SETTINGS or k in NOT_SETTINGS:
            raise BadRequest(f"ReviewDesk could not read that request: “{k}” is not a setting that can be changed here. "
                             "Reload the page and try again; nothing was saved.")
        _like(v, DEFAULT_SETTINGS[k], path + [k], (k,))


def _like(v, default, path: list, key: tuple) -> None:
    if key in SETTING_ANY:
        if v is not None and not _is(v, SETTING_ANY[key]):
            raise _bad(path, SETTING_ANY[key][0])
        return
    if isinstance(default, dict):
        if not isinstance(v, dict):
            raise _bad(path, DICT)
        for k, x in v.items():
            if k in default:
                _like(x, default[k], path + [k], key + (k,))
            elif isinstance(x, (dict, list)):          # a setting this version does not know: plain values only
                raise _bad(path + [k], TEXT)
    elif isinstance(default, list):
        if not isinstance(v, list):
            raise _bad(path, LIST_TEXT if key[-1] not in SETTING_ITEMS else LIST_DICT)
        item = SETTING_ITEMS.get(key[-1]) if len(key) == 1 else None
        for i, x in enumerate(v):
            if item:
                if not isinstance(x, dict):
                    raise _bad(path + [i], DICT)
                _fields(x, item, path + [i])
            elif not isinstance(x, str):
                raise _bad(path + [i], TEXT)
    elif isinstance(default, bool):
        if not isinstance(v, bool):
            raise _bad(path, BOOL)
    elif isinstance(default, (int, float)):
        if not _is(v, NUM):
            raise _bad(path, NUM)
    elif isinstance(default, str):
        if not isinstance(v, str):
            raise _bad(path, TEXT)


def ai_config(body: dict) -> None:
    """What the AI page may change: the two models and their settings, how many calls at once, and whether models
    hosted outside the University may be used. The fixed prompts and the lock are set by ReviewDesk itself."""
    for k, v in body.items():
        if k not in AI_CONFIG:
            raise BadRequest(f"ReviewDesk could not read that request: “{k}” cannot be changed from the AI page. Reload "
                             "the page and try again; nothing was saved.")
        if v is None:
            continue
        if k == "passes":
            if not isinstance(v, dict):
                raise _bad([k], DICT)
            for ps, pv in list(v.items()):
                if ps not in ("A", "B"):
                    raise BadRequest("ReviewDesk could not read that request: the AI has two passes, A and B. Reload the "
                                     "page and try again; nothing was saved.")
                if pv is None:                     # left out (save_config would otherwise store it)
                    del v[ps]
                    continue
                if not isinstance(pv, dict):
                    raise _bad([k, ps], DICT)
                for f in ("model", "reasoning", "max_tokens"):     # (a temperature of null means the service's own)
                    if f in pv and pv[f] is None:
                        del pv[f]
                _fields(pv, AI_PASS, [k, ps])
                if pv.get("max_tokens") is not None and pv["max_tokens"] < 1:
                    raise _bad([k, ps, "max_tokens"], INT)
        elif k == "concurrency":
            if not _is(v, INT) or not 1 <= v <= 16:
                raise BadRequest("Calls at the same time should be a whole number from 1 to 16. Nothing was saved.")
        elif k == "allow_closed_models":
            if not isinstance(v, bool):
                raise _bad([k], BOOL)
        elif not isinstance(v, str):
            raise _bad([k], TEXT)


def check(route: str, body: dict) -> dict:
    """The request as the handler should see it (fields set to null left out), or BadRequest."""
    _text_ok(body)
    spec = ROUTES.get(route)
    body = {k: v for k, v in body.items() if v is not None or (isinstance(spec, dict) and spec.get(k) == REQ_TEXT)}
    if spec == "ai config":
        ai_config(body)
    elif isinstance(spec, dict):
        _fields(body, spec, [])
    return body
