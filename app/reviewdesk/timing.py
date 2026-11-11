"""Time recording: how long a record was on screen before its vote. Off unless the lead switches it on.

It exists so that a team can say, from what was measured and not from memory, how long screening took: per record, per
person and per task (an evaluation of a way of screening needs this; so does planning the next review).

What is saved with a vote ("time" in its row), measured by the page that showed the record, in milliseconds:
  shown    from the record appearing on the page to the vote, by the clock
  active   the part of that during which the ReviewDesk page was the visible tab of a window in front
  quiet    the longest stretch of the active time with no key pressed and no pointer moved, pressed or scrolled
           (a long one suggests the person was away from the keyboard with the page left open; whoever analyses the
           times decides what to do with those, and can only do so if it is recorded)
  views    how many times the record was put on screen before this vote; the three times add up over those viewings,
           for as long as ReviewDesk stays open in that browser tab (a page reloaded starts from nothing: the time
           before a reload is not kept anywhere)
Nothing else is measured: not which keys, not where the pointer was, nothing between records and nothing outside the
ReviewDesk page. A full text is often read in another program or tab, which the page cannot see: at full text `active`
is the least the assessment took and `shown` the most, and `quiet` is not recorded (reading a PDF inside the page
gives the page no key or pointer events).

Who is timed
  The lead switches recording on or off for the project (Project set-up; the setting screening.record_time), at any
  time: it is not part of the screening plan. Switching it on makes the project need ReviewDesk 1.3.1 (versions.py),
  so that nobody goes on voting with a version that would leave the times out.
  Each person can leave their own votes out, and change their mind, on the screening page: their choice is an entry
  in their own audit file (OWN), so it follows them to every computer they use and anyone analysing the times can
  see from when to when a person's votes carry none. While recording is on, the screening pages say so.
The times a page sends are kept only when both say yes at the moment of the vote; otherwise they are dropped unread.
Times are never used for any record's outcome, and are shown to nobody in the pages (screening.without_cmp takes them
off every vote row a page is sent, the voter's own included): they leave ReviewDesk only in the lead's evaluation
download (evaluation.py)."""
from __future__ import annotations

from . import util
from .project import Project, clean_id

OWN = "time recording for my own votes"
KEYS = ("shown", "active", "quiet")
MAX_MS = 24 * 3600 * 1000            # a record left on screen for longer than a day says a day
MAX_VIEWS = 999


def project_on(p: Project) -> bool:
    return (p.settings_ro().get("screening") or {}).get("record_time") is True


def own_choice(p: Project, me: str) -> bool:
    """Whether this person lets their votes be timed: yes, unless their latest choice says no. Only an entry in one
    of their OWN audit files counts (nobody can make the choice for someone else by writing in a file of their own).
    Within a file the last one written is the latest; between the person's computers, the later time stamp. Looked up
    with every vote while recording is on, so each file is read again only when it has changed."""
    best = None
    for f in util.files_in(p.path("audit"), f"{clean_id(me)}@*.jsonl") if clean_id(me) else []:
        def load(f=f):
            last = None
            for e in p._cached("audit:" + f, f, lambda: util.read_jsonl(f)):
                if isinstance(e, dict) and e.get("action") == OWN and e.get("who") == me and isinstance(e.get("details"), dict):
                    last = (util.ts(e.get("t")), e["details"].get("on") is not False)
            return last
        got = p._cached("timing:" + f, f, load)
        if got and (best is None or got[0] >= best[0]):
            best = got
    return best[1] if best else True


def on_for(p: Project, me: str) -> bool:
    return project_on(p) and own_choice(p, me)


def set_own(p: Project, me: str, on: bool) -> dict:
    """Record this person's choice about their own votes (it is theirs alone to make)."""
    p.require_member(me)
    if own_choice(p, me) != bool(on):
        p.audit(me, OWN, {"on": bool(on)})
    return status(p, me)


def status(p: Project, me: str) -> dict:
    """What the pages need: is recording on for the project, and does it apply to this person's votes."""
    proj = project_on(p)
    return {"project": proj, "mine": proj and bool(me) and own_choice(p, me)}


def clean(t, full_text: bool = False) -> dict | None:
    """The times a page sent with a vote, as they are saved: whole milliseconds, none negative, none longer than a
    day, the active time no longer than the time shown and the quiet time no longer than the active time. None if
    what was sent is not a set of times (nothing is then saved with the vote: a vote is never refused over them)."""
    if not isinstance(t, dict):
        return None
    out = {}
    for k in KEYS:
        v = t.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v < 0 or v == float("inf"):
            if k == "quiet":
                continue
            return None
        out[k] = int(min(v, MAX_MS))
    out["active"] = min(out["active"], out["shown"])
    if full_text:
        out.pop("quiet", None)
    elif "quiet" in out:
        out["quiet"] = min(out["quiet"], out["active"])
    v = t.get("views")
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v != float("inf")
    out["views"] = int(min(max(v, 1), MAX_VIEWS)) if ok else 1
    return out


# ================================================================================================ routes
def h_mine(p: Project, me: str, q, body) -> dict:
    """A person says whether their own votes may be timed."""
    if body.get("as") is not None and body.get("as") != me:
        from .project import ProjectError
        raise ProjectError(f"This page was opened as {body.get('as') or 'nobody'}, but ReviewDesk is now set to "
                           f"{me or 'nobody'} (changed in another tab). Reload the page; nothing was saved.")
    return set_own(p, me, body.get("on") is not False)


API_POST = {"/api/timing/mine": h_mine}
