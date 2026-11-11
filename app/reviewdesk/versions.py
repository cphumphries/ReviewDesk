"""One team, one version of ReviewDesk.

Everyone who works on a project must use the same version, or a newer one that can still be read by all: an older
ReviewDesk cannot read some of what a newer one records (version 1.2 shows "Something went wrong" once an ongoing study
or one of the newer risk-of-bias tools is recorded, and would let a disagreement be settled without the choices the
newer version offers). Released versions cannot be changed, so two things are done from 1.3.0 on:

- The project file says which version the project needs (`min_version`). A ReviewDesk older than that refuses to open
  the project and says which version to download. (Versions before 1.3.0 do not look, which is why the warning below
  exists.) NEEDS is the oldest version that can read everything THIS version writes: raise it, in the same change,
  whenever a change to the files would mislead or stop an older version.
  The project file is the lead's to write, so it is stamped when the lead saves it (a new project, any change to the
  settings or the plan, a copy restored) and when the lead opens the project with a version that needs more than the
  file says (server.project_summary; only this one thing in the file changes then), not when a reviewer first
  records something an older version cannot read; until then the warning below is what the others see.
- Every entry in the audit log carries the version that wrote it, and each person's first opening of a project with a
  version leaves an entry. From these, the Overview warns when someone last worked on the project with another
  version. Votes and answers are not in the audit log, so a person who has only ever voted, with an old version, is
  known only as someone who "has not opened the project with this version yet".
- Every entry in the audit log also says which version the project needed when it was written (`needs`: the NEEDS of
  the version that wrote it). The project file is the lead's alone to write, but everyone writes audit entries, and
  the first thing anyone does with a version is open the project, which leaves one. So as soon as ANY member has
  worked on a project with a version whose records this one cannot read, this one stops saving in that project
  (must_upgrade): it can still open it, to read and to export, and says who is ahead and what to download. This is
  what an older version does about a newer one without waiting for the lead, and it is all a version already
  released can ever do: so it is here from the first public version.
- Something only the lead can switch on, and that an older version would misread, raises what the project needs when
  it is switched on, not before (FEATURES, stamp, require): a team that does not use it can go on with a mix of the
  versions that understand everything else. From then on every audit entry of the project says so too (declares).
- 1.3.1 raised NEEDS itself: it writes every fingerprint and signature whole, which 1.3.0 cannot check. So a team
  moves from 1.3.0 to 1.3.1 on one day, the lead first: the lead's first opening of a project with 1.3.1 says so in
  the project file (1.3.0 then refuses to open it) and leaves an audit entry (a 1.3.0 that has it open stops saving).
  Both reach another computer only when the sync service brings them, which is why UPGRADING.md has every
  ReviewDesk closed first. A version that has been told to stop saving leaves its own files as they are, too
  (Project._heal_one): 1.3.0 did not, and gave the rows a newer version had written fingerprints of its own.
"""
from __future__ import annotations

import os
import re
import threading

from . import VERSION, util

# The oldest ReviewDesk that can read everything this version writes without being asked. 1.3.1: from that version
# every fingerprint and signature is written whole (integrity.py). An earlier version keeps and checks only the start
# of each, so it would report every row this one writes as changed outside ReviewDesk, and offer to put files back
# without them.
NEEDS = "1.3.1"
CHECKS = "1.3.0"           # the first version that stops saving in a project that needs a newer one (must_upgrade)
OPENED = "open the project"
# What the lead can switch on that an older version cannot read, and the first version that can. 1.3.0 would count a
# record the verification sample sent on as excluded (it does not know those votes), and would save votes without
# their times, leaving holes in what the project set out to record. (Both are no newer than NEEDS now, so switching
# one on raises nothing; the next thing of this kind goes here.)
FEATURES = {"time recording": "1.3.1", "verification sample": "1.3.1"}

_noted: set = set()
_lock = threading.Lock()


def parse(v) -> tuple:
    """'1.3.0' as (1, 3, 0), for comparing. Anything unreadable is (0,): older than every real version."""
    nums = re.findall(r"\d+", str(v or "").split("+")[0].split("-")[0])
    return tuple(int(n) for n in nums[:4]) or (0,)


def newer(a, b) -> bool:
    """Version a is newer than version b ('1.3' and '1.3.0' are the same)."""
    pa, pb = parse(a), parse(b)
    n = max(len(pa), len(pb))
    return pa + (0,) * (n - len(pa)) > pb + (0,) * (n - len(pb))


def require(settings: dict, version: str) -> bool:
    """Raise the version a project file about to be saved says the project needs (never lowering it). True if raised."""
    if newer(version, settings.get("min_version")):
        settings["min_version"] = version
        return True
    return False


def stamp(settings: dict) -> dict:
    """Mark a project file about to be saved with the version the project needs (never lowering what it says): what
    this version always writes, and what the settings in the file switch on."""
    require(settings, NEEDS)
    sc = settings.get("screening")
    if isinstance(sc, dict) and sc.get("record_time") is True:
        require(settings, FEATURES["time recording"])
    return settings


def declares(p) -> str:
    """The version an audit entry written now says the project needs: NEEDS, or what the project file asks for if
    that is newer (a feature the lead has switched on). Never stops an entry being written."""
    try:
        own = str(p.settings_ro().get("min_version") or "")
    except Exception:
        return NEEDS
    return own if parse(own) != (0,) and newer(own, NEEDS) and not newer(own, VERSION) else NEEDS


def too_old(settings: dict) -> str:
    """'' or why this ReviewDesk must not open the project: it needs a newer version."""
    need = str((settings or {}).get("min_version") or "")
    if not need or not newer(need, VERSION):
        return ""
    return (f"This project needs ReviewDesk {need} or later, and this is ReviewDesk {VERSION}. Someone in your team has "
            "worked on the project with the newer version, which records things this one cannot read. Download the new "
            "version, extract it to a new folder and start that instead (your settings and projects are kept; READ ME FIRST "
            "in the download says how). Everyone in the team must use it.")


def _file_need(rows: list) -> dict:
    """The newest version the entries of one audit file ask for ({} if none does), with the first entry to ask for it."""
    best: dict = {}
    for e in rows:
        n = e.get("needs") if isinstance(e, dict) else None
        if isinstance(n, str) and 0 < len(n) <= 20 and parse(n) != (0,) and newer(n, best.get("version")):
            best = {"version": n, "who": str(e.get("who") or ""), "when": str(e.get("t") or "")[:10]}
    return best


def needed(p) -> dict:
    """The newest version this project is known to need, and how it is known: {"version", "who", "when"} (who and when
    of the first audit entry to say so; '' for what the project file itself says). Nothing here compares clocks: the
    newest version anything asks for is the one needed, whenever it was written. Looked up for every request that
    changes something, so each audit file is read again only when it has changed."""
    best = {"version": "", "who": "", "when": ""}
    try:
        own = str(p.settings_ro().get("min_version") or "")
    except Exception:                                  # a project file that cannot be read says nothing here (the audit
        own = ""                                       # entries still do), and must not stop the request that puts it back
    if parse(own) != (0,):
        best["version"] = own
    for f in util.files_in(p.path("audit"), "*.jsonl"):
        def load(f=f):
            return _file_need(p._cached("audit:" + f, f, lambda: util.read_jsonl(f)))
        top = p._cached("needs:" + f, f, load)
        if top and (newer(top["version"], best["version"])
                    or (best["who"] and not newer(best["version"], top["version"]) and top["when"] < best["when"])):
            best = top
    return best


def behind(p) -> bool:
    """This ReviewDesk is older than the project is known to need: it must not save in it (must_upgrade says why to
    the person), nor put its own files right (Project._heal_one)."""
    n = needed(p)
    return bool(n["version"]) and newer(n["version"], VERSION)


def must_upgrade(p, me: str = "") -> str:
    """'' or why this ReviewDesk must not save anything in this project: someone has worked on it with a version that
    records things this one cannot read (see the module notes). The server refuses every change while this is so."""
    n = needed(p)
    if not n["version"] or not newer(n["version"], VERSION):
        return ""
    from .reporting import nice_date
    try:
        name = ((p.reviewer(n["who"]) or {}).get("name") or n["who"]) if n["who"] else ""
    except Exception:                                  # (the project file cannot be read just now: their initials, then)
        name = n["who"]
    who = "You have" if n["who"] and n["who"] == me else f"{name} has" if name else "Someone in your team has"
    when = f" (on {nice_date(n['when'])})" if n["when"] else ""
    return (f"This project now needs ReviewDesk {n['version']} or later, and this is ReviewDesk {VERSION}. {who} worked on "
            f"it with the newer version{when}, which records things this one cannot read. So that nothing is misread or "
            "overwritten, this version will not save any work in this project; you can still read it and download its "
            "reports. Download the new version, extract it to a new folder and start that instead (your settings and "
            "projects are kept; READ ME FIRST in the download says how). Everyone in the team must use it.")


def _latest(p) -> dict:
    """{person: their latest audit entry}, by time across their computers."""
    out: dict = {}
    for e in p.audit_entries():                       # in time order
        if e.get("who"):
            out[e["who"]] = e
    return out


def _has_worked(p, rid: str) -> bool:
    """The person has a file of their own in the project (votes, answers, judgements, a search log, a PDF record)."""
    for sub in ("decisions", "extraction", "rob", "certainty", "searches", "fulltexts"):
        try:
            names = os.listdir(p.path(sub))
        except OSError:
            continue
        if any(re.match(rf"(log-|index@)?{re.escape(rid)}@.*\.jsonl$", n) or n.startswith(f"index@{rid}@") for n in names):
            return True
    return False


def note_open(p, me: str) -> None:
    """Leave an audit entry the first time a member opens the project with this version (and whenever their latest
    entry was written by another version), so that the others can see who has moved to it. Once per person, project
    and run of the program at most; nothing is written for someone who is not in the project."""
    try:
        if not me or not p.reviewer(me):
            return
    except Exception:                                  # the project file cannot be read just now: nothing is noted, and
        return                                         # the request goes on (it may be the one that puts the file back)
    key = (p.dir, me)
    with _lock:
        if key in _noted:
            return
        _noted.add(key)
    try:
        last = _latest(p).get(me)
        was = str((last or {}).get("version") or "")
        if last is None or was != VERSION:
            p.audit(me, OPENED, {"with ReviewDesk": VERSION, **({"last used": was} if was else {})})
    except Exception:                                  # the folder could not be read or written just now: this is only a
        with _lock:                                    # note, so never stop the page for it; try again at the next opening
            _noted.discard(key)


def others(p, me: str) -> list:
    """What is known about the versions the other members use, where it matters:
    [{"id", "name", "state": "older" | "newer" | "unknown", "version", "when"}]."""
    def find():
        latest = _latest(p)
        out = []
        for r in p.settings_ro().get("reviewers") or []:
            rid = r.get("id")
            e = latest.get(rid)
            v = str((e or {}).get("version") or "")
            row = {"id": rid, "name": r.get("name") or rid, "version": v, "when": str((e or {}).get("t") or "")[:10]}
            if e is None:
                if _has_worked(p, rid):
                    out.append({**row, "state": "unknown"})
            elif newer(VERSION, v):
                out.append({**row, "state": "older"})
            elif newer(v, VERSION):
                out.append({**row, "state": "newer"})
        return out
    return [x for x in p.memo("versions.others", find) if x["id"] != me]


def _names(rows: list) -> str:
    names = [x["name"] for x in rows]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def warnings(p, me: str) -> list:
    """Plain sentences for the Overview, most serious first ([] when everyone is on this version)."""
    from .reporting import nice_date
    rows = others(p, me)
    out = []
    stop = must_upgrade(p, me)
    if stop:
        out.append(stop)
    newer_ = [x for x in rows if x["state"] == "newer"]
    if newer_ and not stop:
        top = max((x["version"] for x in newer_), key=parse)
        out.append(f"{_names(newer_)} {'uses' if len(newer_) == 1 else 'use'} ReviewDesk {top}, which is newer than yours "
                   f"({VERSION}). Download the new version before you carry on: everyone in a team must use the same "
                   "version, and this one may not be able to read what the newer one records.")
    need = declares(p)                              # what the project needs as it is now
    for x in [x for x in rows if x["state"] == "older"]:
        said = (f"{x['name']} last worked on this project with ReviewDesk {x['version'] or 'an older version'}"
                + (f" (on {nice_date(x['when'])})" if x["when"] else "") + ". ")
        how = "(READ ME FIRST in the download says how). This message goes when they open the project with it."
        if not newer(need, x["version"]):           # their version reads everything this project holds: no harm yet
            out.append(said + f"That version can still read everything in this project, so nothing is at risk, but a team "
                       f"should use one version, and theirs lacks what ReviewDesk {VERSION} added. Ask them to "
                       f"move to ReviewDesk {VERSION} when they next can " + how)
        elif not newer(CHECKS, x["version"]):       # it cannot read or check everything here, and knows to stop saving
            out.append(said + f"Everyone in the team must use ReviewDesk {need} or later: it records things their version "
                       "cannot read or check. Their version stops saving in this project once their computer has the "
                       "project's files as they now are, and must not be used in it before then. Ask them to move to the "
                       "new version before they next work on the project " + how)
        else:
            out.append(said + f"Everyone in the team must use ReviewDesk "
                       f"{VERSION}: an older version cannot read some of what this one records (it shows “Something went "
                       "wrong” once an ongoing study or one of the newer risk-of-bias tools is recorded), and must not be "
                       "used to settle disagreements. Ask them to move to the new version before they next work on the "
                       "project " + how)
    unknown = [x for x in rows if x["state"] == "unknown"]
    if unknown:
        one = len(unknown) == 1
        out.append(f"{_names(unknown)} {'has' if one else 'have'} worked on this project but {'has' if one else 'have'} "
                   f"not opened it with ReviewDesk {VERSION} yet. Make sure {'they use' if one else 'they all use'} this "
                   "version before they next work on it: an older version cannot read some of what this one records.")
    return out
