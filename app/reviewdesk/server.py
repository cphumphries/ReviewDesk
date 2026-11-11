"""The local web server: serves the screens and answers the browser's requests. It listens only on this
computer (127.0.0.1), and every request must carry the session token the browser was given at start-up."""
from __future__ import annotations

import hmac
import json
import mimetypes
import os
import re
import secrets
import socket
import string
import sys
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import NAME, VERSION, ai, exports, fulltext, prisma, review, screening, userconfig, util
from . import certainty, evaluation, readiness, reporting, robplan, robtools, shapes, timeline, timing, versions
from .parsers import FormatError
from .project import LONG_PATH, MARKER, DamagedFile, OlderCopy, PermissionDenied, Project, ProjectError, SETS, clean_id, path_too_long, request_scope

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
MAX_UPLOAD = 300 * 1024 * 1024
DEFAULT_PORT = 47821          # the same address every time, so a tab left open still works after a restart
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                ".woff2": "font/woff2", ".woff": "font/woff", ".txt": "text/plain; charset=utf-8", ".ico": "image/x-icon"}


class App:
    def __init__(self):
        self.token = userconfig.session_token()
        self.project: Project | None = None
        self.path_warned: set = set()            # project folders whose too-long path has been pointed out this session
        cfg = userconfig.load()
        last = (cfg.get("recent") or [{}])[0].get("path")
        if last and os.path.isfile(os.path.join(last, MARKER)) and os.environ.get("REVIEWDESK_NO_REOPEN") != "1":
            try:
                self.project = Project(last)
            except Exception:
                self.project = None

    def me(self) -> str:
        return userconfig.identity_for(self.project.dir, self.project.key()) if self.project else ""


APP: App | None = None


class ApiError(Exception):
    def __init__(self, msg, code=400, data=None):
        super().__init__(msg)
        self.code = code
        self.data = data or {}             # sent with the message, for a page that treats this refusal in its own way


def need_project() -> Project:
    if not APP.project:
        raise ApiError("No project is open.", 409)
    if not APP.project.exists():               # moved, renamed, or the sync service is not running
        gone = APP.project.dir
        APP.project = None
        raise ApiError(f"The project folder is no longer at {gone}. It may have been moved or renamed, or OneDrive may not "
                       "be running. Open it again from where it is now (Open a project): ReviewDesk will remember who "
                       "you are in it.", 409)
    return APP.project


# ======================================================================================================== handlers
def h_app(q, body):
    cfg = userconfig.load()
    return {"name": NAME, "version": VERSION, "user": userconfig.public_view(cfg),
            "project": project_summary() if APP.project else None,
            "recent": [r for r in cfg.get("recent", []) if os.path.isfile(os.path.join(r["path"], MARKER))],
            "python": sys.version.split()[0]}


def h_app_page(q, body):
    """What the page loads first, every time it is drawn. A project opened through a path too long for Windows on
    this computer is pointed out here, once in each session (the page shows it until it is closed)."""
    out = h_app(q, body)
    p = APP.project
    if p and not q.get("probe") and os.path.normcase(p.dir) not in APP.path_warned:     # (probe: a second start checking)
        warn = path_too_long(p.dir)
        if warn:
            APP.path_warned.add(os.path.normcase(p.dir))
            out["path_warning"] = warn
    return out


def project_summary() -> dict:
    p = need_project()
    s = p.settings_ro()
    me = APP.me()
    st = p.state_ro()
    lead = p.is_lead(me)
    elsewhere = ""
    if lead:                                   # the newer of the lead's two shared files says where the lead last worked
        last = max([(util.ts(st.get("saved_at") or ""), st.get("saved_on") or ""), (util.ts(s.get("updated") or ""), s.get("updated_on") or "")])
        if last[1] and last[1] != p.dev():
            elsewhere = ("The project's shared files were last saved by the lead on another computer. If you work as the "
                         "lead on two computers, let OneDrive finish syncing before you switch, and never have ReviewDesk "
                         "open on both at once.")
    if lead and any(v.get("phase") not in (None, "not_started") for v in st.get("sets", {}).values()) and \
            not os.path.isfile(p.path("state", "seal.json")) and not p.ever_sealed():
        p.write_seal(me)                         # a project started before the data checks existed
    try:
        probs = p.integrity_report()["problems"]
        if evaluation.verifying(p, me):          # (the checks of the PDFs are not shown to them: h_integrity)
            probs = [x for x in probs if not str(x.get("kind") or "").startswith("pdf")]
        problems = len(probs)
    except DamagedFile:
        problems = 1
    # one team, one version: this opening is noted for the others, and anyone who last used another version is named
    versions.note_open(p, me)
    try:
        stop = versions.must_upgrade(p, me)      # (shown to someone who has not yet said who they are, too)
        if lead and not stop and versions.newer(versions.NEEDS, s.get("min_version")):
            # The lead's first opening with a version that needs more than the project file says: the file says so
            # from now on, and an older ReviewDesk no longer opens the project at all (versions.py). Nothing else in
            # the file changes: it is not a save, and must not make an older copy of the file look new.
            try:
                p.need_version(me, versions.NEEDS, quiet=True)
                s = p.settings_ro()
            except (DamagedFile, ProjectError, OSError):
                pass                             # (a project file that cannot be read or written says so elsewhere)
        other_versions = versions.warnings(p, me) if p.reviewer(me) else ([stop] if stop else [])
    except (OSError, ValueError):
        other_versions = []
    return {"path": p.dir, "key": p.folder_key(), "title": s.get("title"), "settings": s, "me": me, "is_lead": lead,
            "member": bool(p.reviewer(me)), "warnings": p.sync_warnings(), "repairs": p.repairs(), "lead_elsewhere": elsewhere,
            "version_warnings": other_versions,
            "sync_problems": len(p.lead_conflicts()) if lead else 0, "data_problems": problems,
            "processed": bool(st.get("imports_order")), "has_records": bool(p.records())}


def h_fs(q, body):
    path = (q.get("path") or [""])[0]
    roots = []
    home = os.path.expanduser("~")
    for label, pth in (("Home", home), ("OneDrive", os.environ.get("OneDriveCommercial") or ""),
                       ("OneDrive (personal)", os.environ.get("OneDriveConsumer") or ""), ("OneDrive", os.environ.get("OneDrive") or ""),
                       ("Documents", os.path.join(home, "Documents")), ("Desktop", os.path.join(home, "Desktop"))):
        if pth and os.path.isdir(pth) and pth not in [r["path"] for r in roots]:
            roots.append({"label": label, "path": pth})
    if sys.platform.startswith("win"):
        for d in string.ascii_uppercase:
            if os.path.isdir(f"{d}:\\"):
                roots.append({"label": f"{d}:", "path": f"{d}:\\"})
    else:
        roots.append({"label": "/", "path": "/"})
    if not path:
        path = roots[1]["path"] if len(roots) > 1 and "OneDrive" in roots[1]["label"] else home
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise ApiError("That folder does not exist.")
    dirs = []
    try:
        for n in sorted(os.listdir(path), key=str.lower):
            fp = os.path.join(path, n)
            if n.startswith((".", "$", "~")) or not os.path.isdir(fp):
                continue
            dirs.append({"name": n, "path": fp, "is_project": os.path.isfile(os.path.join(fp, MARKER))})
    except PermissionError:
        raise ApiError("You do not have permission to open that folder.")
    parent = os.path.dirname(path)
    return {"path": path, "parent": parent if parent != path else "", "dirs": dirs, "roots": roots,
            "is_project": os.path.isfile(os.path.join(path, MARKER)), "empty": not any(True for _ in os.scandir(path))}


def h_open(q, body):
    path = body.get("path", "")
    try:
        APP.project = Project(path)
    except ProjectError as e:
        raise ApiError(str(e))
    userconfig.remember_project(APP.project.dir, APP.project.settings_ro().get("title", ""))
    out = h_app(q, body)
    warn = path_too_long(APP.project.dir)
    if warn:
        out["warning"] = warn
    return out


def h_create(q, body):
    parent, name = body.get("parent", ""), util.safe_filename(body.get("name", "").strip())
    if not os.path.isdir(parent):
        raise ApiError("Choose the folder the project should go in.")
    if not name or not body.get("title") or not body.get("lead_name") or not clean_id(body.get("lead_id", "")):
        raise ApiError("Give the project a folder name and title, and your name and initials.")
    target = os.path.abspath(os.path.join(parent, name))
    if len(target) > LONG_PATH:
        raise ApiError(f"That place is too deep in your folders: the project folder's path would be {len(target)} "
                       f"characters long, and at most {LONG_PATH} leaves room for the files ReviewDesk makes inside it "
                       "(Windows cannot open files with paths over 259 characters, and other people's OneDrive paths may "
                       "be longer still). Choose a folder nearer the top of OneDrive, or a shorter folder name.")
    p = Project.create(target, body["title"], body["lead_name"], body["lead_id"])
    APP.project = p
    userconfig.remember_project(p.dir, body["title"])
    userconfig.set_identity(p.dir, clean_id(body["lead_id"]), p.key())
    if not userconfig.load().get("my_name"):
        userconfig.update(my_name=body["lead_name"])
    out = h_app(q, body)
    if len(target) > 110:
        out["warning"] = (f"The project folder's path is {len(target)} characters long. That is fine here, but if someone "
                          "else's OneDrive path is longer, Windows may not open some files. A shorter path is safer.")
    return out


def h_close(q, body):
    APP.project = None
    return h_app(q, body)


def h_me(q, body):
    p = need_project()
    rid = body.get("reviewer", "")
    if not p.reviewer(rid):
        raise ApiError("That person is not in this project.")
    userconfig.set_identity(p.dir, rid, p.key())
    return project_summary()


def h_user(q, body):
    allowed = {k: v for k, v in body.items() if k in ("my_name", "unpaywall_email", "elm_key", "elm_url")}
    em = str(allowed.get("unpaywall_email") or "").strip()
    if em and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", em):
        raise ApiError("That does not look like an email address (Unpaywall needs a real one, such as your work address).")
    url = str(allowed.get("elm_url") or "").strip()
    if url and ai.address_problem(url):
        raise ApiError(ai.address_problem(url))
    cleared = False
    if "elm_url" in allowed:
        allowed["elm_url"] = url
        before = (userconfig.load().get("elm_url") or "").strip().rstrip("/").lower()
        if url.rstrip("/").lower() != before and not str(allowed.get("elm_key") or "").strip():
            # a key belongs to the service it was issued by: a new address never receives the old key
            cleared = bool(userconfig.ai_key())
            allowed["elm_key"] = ""
    if "elm_key" in allowed:
        allowed["elm_key"] = userconfig.protect(str(allowed["elm_key"] or "").strip())
    userconfig.update(**allowed)
    return {**userconfig.public_view(userconfig.load()), "key_cleared": cleared}


def h_settings(q, body):
    p = need_project()
    res = p.update_settings(APP.me(), body.get("changes", {}))
    return {"result": res, "project": project_summary()}


def h_guidance(q, body):
    return util.read_json(os.path.join(HERE, "guidance.json"), {})


def h_worksheet_get(q, body):
    return need_project().worksheet()


def h_worksheet_put(q, body):
    return need_project().save_worksheet(APP.me(), body)


def h_searchlog_get(q, body):
    p = need_project()
    imps = p.imports()
    out = []
    for e in p.search_log():
        e = dict(e)
        e["files"] = [{"filename": m.get("original_name") or m["filename"], "records": m["parse"]["records"], "part": m.get("part", "")}
                      for m in imps if m.get("search_id") == e.get("id")]
        got = sum(f["records"] for f in e["files"])
        want = str(e.get("results") or "").replace(",", "").strip()
        if e["files"] and want.isdigit() and int(want) != got:
            e["mismatch"] = (f"The search found {util.plural(int(want), 'record')} but the files linked to it hold {got:,}. "
                             + ("A part of the export may be missing (databases often export at most a few thousand at a "
                                "time)." if got < int(want) else "Check the number recorded, or whether a file was added twice."))
        out.append(e)
    return {"entries": out}


SEARCH_FIELDS = ("id", "database", "platform", "date", "coverage", "string", "limits", "results", "notes", "deleted")


def h_searchlog_post(q, body):
    """Only the fields of a search-log entry are kept, as text (shapes.py has checked their kinds), and who recorded
    the entry is never taken from the request: it is the person saving a new entry, or whoever recorded it first."""
    p = need_project()
    me = APP.me()
    entry = {k: (str(body[k]) if k != "deleted" else bool(body[k])) for k in SEARCH_FIELDS if body.get(k) is not None}
    if entry.get("id"):
        was = next((e for e in p.search_log() if e.get("id") == entry["id"]), None)
        if was and isinstance(was.get("by"), str) and was["by"]:
            entry["by"] = was["by"]
    return p.save_search(me, entry)


def h_import_link(q, body):
    """Link an imported file to its search in the search log, after it was added (or unlink it)."""
    p = need_project()
    p.link_import(APP.me(), str(body.get("id") or ""), str(body.get("search_id") or ""))
    return h_imports(q, body)


def h_imports(q, body):
    p = need_project()
    st = p.state_ro()
    done = set(st.get("imports_order", []))
    out = []
    for m in p.imports(include_missing=True):
        miss, broken = m.get("_missing", False), m.get("_damaged", False)
        m = {k: v for k, v in m.items() if not k.startswith("_")}
        m["processed"] = m["id"] in done
        m["missing"] = miss and not broken
        m["damaged"] = broken
        out.append(m)
    dd = p.dedup_report()
    return {"imports": out, "dedup": {k: dd.get(k) for k in ("processed", "by", "files")} if dd else None,
            "records": len(p.records()), "sets": SETS}


def h_upload(q, body, raw):
    p = need_project()
    g = lambda k: (q.get(k) or [""])[0]
    m = p.add_import(APP.me(), g("filename"), raw, {"set": g("set"), "database": g("database"), "platform": g("platform"),
                                                     "search_date": g("search_date"), "search_id": g("search_id"),
                                                     "notes": g("notes"), "part": g("part")})
    return {k: v for k, v in m.items() if not k.startswith("_")}


def h_import_remove(q, body):
    p = need_project()
    if p.remove_import(APP.me(), body.get("id", "")):
        p.process_imports(APP.me())          # it had been processed: rebuild the records without it
    return h_imports(q, body)


def h_process(q, body):
    p = need_project()
    return {"summary": p.process_imports(APP.me()), **h_imports(q, body)}


def h_dedup(q, body):
    p = need_project()
    dd = p.dedup_report()
    by = p.by_id()
    merges = []
    for m in dd.get("merges", []):
        k = by.get(m["kept"], {})
        merges.append({**m, "kept_title": k.get("title", ""), "kept_year": k.get("year", ""), "kept_journal": k.get("journal", ""),
                       "kept_authors": (k.get("authors", "") or "")[:120]})
    from .dedup import fingerprint, is_comment, is_conference, is_preprint, kept_first
    report_kind = lambda r: (is_conference(r.get("pubtype")), is_comment(r.get("pubtype")), is_preprint(r))
    imps = {m["id"]: m for m in p.imports(include_missing=True)}
    st = p.state_ro()
    nd = {frozenset(x) for x in st.get("overrides", {}).get("not_dup", [])}
    started = {k for k, v in st.get("sets", {}).items() if v.get("phase") not in (None, "not_started")}
    view = lambda r: {**{k: r.get(k, "") for k in ("id", "title", "year", "journal", "authors", "doi", "pubtype", "language")},
                      "found_in": sorted({imps[s]["database"] for s in r.get("sources", []) if s in imps})}
    poss = []
    for x in dd.get("possible", []):
        a, b = by.get(x["a"]), by.get(x["b"])
        if not (a and b):
            continue
        if {frozenset((a["id"], b["id"])), frozenset((a["id"], fingerprint(b))), frozenset((fingerprint(a), b["id"]))} & nd:
            continue                                   # marked as not the same since the list was made
        a, b = kept_first(a, b)                        # from two searches: the earlier one's record would stay
        poss.append({"a": view(a), "b": view(b), "similarity": x["similarity"], "identical": x.get("identical", False),
                     "kinds_differ": report_kind(a) != report_kind(b), "sets": [a.get("set"), b.get("set")],
                     "why": x.get("why", "very similar titles"), "started": b.get("set") in started})
    files = [{**f, "original_name": imps.get(f.get("import"), {}).get("original_name") or f.get("filename")} for f in dd.get("files", [])]
    return {"rule": dd.get("rule", ""), "files": files, "merges": merges, "possible": poss,
            "processed": dd.get("processed"), "overrides": st.get("overrides", {})}


def h_notdup(q, body):
    p = need_project()
    pairs = body.get("pairs") or [[body.get("kept"), body.get("other")]]
    if not all(a and b for a, b in pairs):
        raise ApiError("Choose the two records to keep apart. Reload the page and try again; nothing was changed.")
    for kept, other in pairs:                  # many at once: one rebuild at the end
        p.mark_not_duplicate(APP.me(), kept, other)
    p.process_imports(APP.me())
    return h_dedup(q, body)


def h_merge(q, body):
    p = need_project()
    pairs = body.get("pairs") or [[body.get("keep"), body.get("drop")]]
    if not all(a and b for a, b in pairs):
        raise ApiError("Choose the two records to merge. Reload the page and try again; nothing was merged.")
    if body.get("pairs"):                      # merging many at once: only pairs that are plainly the same report
        from .dedup import safe_to_merge_in_bulk
        by = p.by_id()
        unsafe = [f"{a} and {b}" for a, b in pairs if not (by.get(a) and by.get(b) and safe_to_merge_in_bulk(by[a], by[b]))]
        if unsafe:
            raise ApiError("Some of these pairs are not plainly the same report, so they need checking one by one: "
                           + ", ".join(unsafe[:10]) + ("…" if len(unsafe) > 10 else "") + ". Nothing was merged.")
    for keep, drop in pairs:
        p.mark_duplicate(APP.me(), keep, drop)
    p.process_imports(APP.me())
    return h_dedup(q, body)


def record_view(p: Project, rid: str, stage: str | None = None) -> dict:
    by = p.by_id()
    r = by.get(rid)
    if not r:
        p.note_syncing()
        raise ApiError("This record is not on this computer yet: the sync service is probably still copying the project "
                       "files. Wait a minute, then reload the page.", 404)
    me = APP.me()
    V = screening.votes(p)
    imps = {m["id"]: m for m in p.imports()}
    out = dict(r)
    out["found_in"] = sorted({imps[s]["database"] for s in r.get("sources", []) if s in imps})
    out["mine"] = screening.own_votes(V, rid, me)
    out["links"] = fulltext.links(p, r)
    # whether a record has a PDF, or a free copy was looked for and found, says that it reached full text: not told to
    # someone screening a verification sample (the records mixed into it did; the sampled ones did not)
    blind = bool(evaluation.verifying(p, me))
    if blind:
        out["links"]["found"] = ""
    out["pdf"] = fulltext.has_pdf(p, rid) and not blind
    return out


def h_record(q, body):
    p = need_project()
    return record_view(p, (q.get("id") or [""])[0])


def h_records(q, body):
    p = need_project()
    g = lambda k, d="": (q.get(k) or [d])[0]
    text, set_ = g("q").lower(), g("set")
    recs = [r for r in p.records() if (not set_ or r["set"] == set_)
            and (not text or text in (r["title"] + " " + r.get("authors", "") + " " + r.get("doi", "") + " " + r["id"]).lower())]
    try:
        off, lim = max(0, int(g("offset", "0"))), max(0, int(g("limit", "50")))
    except ValueError:
        raise ApiError("ReviewDesk could not read that request: the place in the list should be a whole number. Reload "
                       "the page and try again.") from None
    return {"total": len(recs), "records": [{k: r.get(k, "") for k in ("id", "set", "title", "authors", "year", "journal", "doi")}
                                            for r in recs[off: off + lim]]}


def h_start(q, body):
    p = need_project()
    screening.start(p, APP.me(), body.get("set", "main"))
    return screening.overview(p)


def h_calibration(q, body):
    p = need_project()
    screening.finish_calibration(p, APP.me(), body.get("set", "main"), bool(body.get("another")))
    return screening.overview(p)


def h_apply(q, body):
    p = need_project()
    screening.apply_sample_decision(p, APP.me(), body.get("set", "main"), drop_ai=str(body.get("drop_ai") or ""))
    return screening.overview(p)


# What the AI stopping rule decided, as anyone but the lead may read it (the lead's words, screening.py, give the share
# of records the AI kept, which is worked out from the AI's answers): {kind: (on its own, after "Under the stopping rule, ")}
AI_RULE_OUTCOME = {
    "net": ("the stopping rule allowed one reviewer to screen the rest, with the AI second screener behind them",
            "one reviewer screened the rest, with the AI second screener behind them"),
    "stopping_rule": ("the stopping rule dropped the AI step, so both reviewers screened every record",
                      "the AI step was dropped and both reviewers screened every record")}
AI_FIGURES = (re.compile(r" \(\d[\d,]* records? had such an answer\)"),             # failed calls: from the AI's answers
              re.compile(r"; \d[\d,]* records? had a failed or unreadable answer and counted as kept"))


def _ai_rule_kind(sr: dict) -> str:
    rule = sr.get("ai_rule") or ""
    return sr.get("ai_rule_kind") or ("net" if "within the" in rule else "stopping_rule" if "above the" in rule else "")


def sample_result_for(sr: dict | None, lead: bool) -> dict | None:
    """A set's agreement-sample result as this person may see it: anyone but the lead gets no AI accuracy figures
    and only the outcome of the AI stopping rule, never the share of records the AI kept."""
    if lead or not sr:
        return sr
    out = {k: v for k, v in sr.items() if k != "ai"}
    if out.get("ai_rule") and _ai_rule_kind(sr) in AI_RULE_OUTCOME:
        out["ai_rule"] = AI_RULE_OUTCOME[_ai_rule_kind(sr)][0]
    return out


def summary_for(p: Project, lead: bool) -> str:
    """The methods summary as this person may see it: anyone but the lead gets the outcome of the AI stopping rule
    instead of the share of records the AI kept, and no count of the AI's failed answers."""
    text = exports.methods_summary(p, full=lead)
    if lead:
        return text
    for sv in p.state_ro().get("sets", {}).values():
        sr = sv.get("sample_result") or {}
        rule, kind = sr.get("ai_rule") or "", _ai_rule_kind(sr)
        if rule and kind in AI_RULE_OUTCOME:
            text = text.replace(f"Under the pre-specified stopping rule, {rule}.",
                                f"Under the pre-specified stopping rule, {AI_RULE_OUTCOME[kind][1]}.")
            text = text.replace(rule, AI_RULE_OUTCOME[kind][0])
    for rx in AI_FIGURES:
        text = rx.sub("", text)
    return text


def h_overview(q, body):
    p = need_project()
    o = screening.overview(p)
    lead = p.is_lead(APP.me())
    if not lead:
        for set_name, sv in o["sets"].items():          # the AI's accuracy figures, keep rate and the records it missed: lead only
            sv["sample_result"] = sample_result_for(sv.get("sample_result"), lead)
            if sv.get("ai_net_active"):
                sv["counts"] = screening.counts_blind_to_ai(p, set_name)
    o["tasks"] = screening.tasks_for(p, APP.me()) if p.reviewer(APP.me()) else []
    for set_name, sv in o["sets"].items():              # the verification sample, as this person may see it
        sv["verify"] = evaluation.verification(p, set_name, APP.me()) if p.reviewer(APP.me()) else None
    o["verifying"] = evaluation.verifying(p, APP.me())  # (pages that show earlier decisions are closed to them meanwhile)
    o["timing"] = timing.status(p, APP.me())
    cov = ai.coverage(p)
    if not lead:                  # how many of the AI's answers could not be read: worked out from its answers
        cov = {"records": cov["records"], "done": cov["done"] + cov["failed"], "missing": cov["missing"]}
    o["ai"] = {"planned": p.settings_ro()["screening"].get("ai_second_screener"), "coverage": cov,
               "status": ai.Runner.status(p)}
    imps, done = p.imports(include_missing=True), set(p.state_ro().get("imports_order", []))
    o["imports"] = {"total": len(imps), "unprocessed": sum(1 for m in imps if m["id"] not in done),
                    "missing": sum(1 for m in imps if m.get("_missing"))}
    o["searches"] = len(p.search_log())
    o["records"] = len(p.records())
    o["extraction"] = review.progress(p)
    o["certainty"] = certainty.progress(p)
    return o


def h_tasks(q, body):
    p = need_project()
    return {"tasks": screening.tasks_for(p, APP.me())}


def closed_to_verifier(p: Project) -> None:
    """Refuse, with the reason, when the person asking is screening a verification sample that is not finished: what
    they asked for lists records by what became of them (evaluation.blocked)."""
    why = evaluation.blocked(p, APP.me())
    if why:
        raise ApiError(why, 409, {"verifying": True, "title": evaluation.blocked_title(p, APP.me())})


# The pages' data that list records by what became of them: every record at full text (the records mixed into a
# verification sample are there, the sampled ones are not), the included studies, and the audit log (which names the
# records whose PDFs were added and whose disagreements were settled). Refused to a verifier until their sample is
# finished, with every download and every PDF (Handler._get).
VERIFIER_CLOSED = ("/api/fulltext", "/api/fulltext/fetch", "/api/fulltext/waiting", "/api/fulltext/missing", "/api/conflicts",
                   "/api/audit", "/api/extract", "/api/extract/study", "/api/certainty", "/api/certainty/outcome")
# ... and what changes them (their answers name those records too, and the pages that send them are closed anyway)
VERIFIER_CLOSED_CHANGES = ("/api/fulltext/", "/api/extract/", "/api/certainty/", "/api/rob/define", "/api/rob/judge",
                           "/api/rob/done", "/api/rob/agree", "/api/resolve")


def h_queue(q, body):
    p = need_project()
    g = lambda k: (q.get(k) or [""])[0]
    if g("task") == "fulltext":
        closed_to_verifier(p)
    items = screening.queue(p, APP.me(), g("task"), g("set") or "main")
    return {"items": items}


def same_person(body: dict) -> str:
    """The person this page was opened as must still be the person ReviewDesk thinks is at this computer (another
    tab may have switched): otherwise a vote could be saved under the wrong name."""
    me = APP.me()
    if body.get("as") is not None and body.get("as") != me:
        raise ApiError(f"This page was opened as {body.get('as') or 'nobody'}, but ReviewDesk is now set to "
                       f"{me or 'nobody'} (changed in another tab). Reload the page; nothing was saved.", 409)
    return me


def h_decide(q, body):
    p = need_project()
    me = same_person(body)
    stage, d = body.get("stage"), body.get("decision")
    if stage in ("cal", "ta", "vs") and d not in screening.TA_CHOICES:
        raise ApiError("Choose Include, Maybe or Exclude.")
    if stage == "ft":
        if d not in screening.FT_CHOICES:
            raise ApiError("Choose a full-text decision.")
        if d == "exclude" and body.get("reason") not in p.settings_ro().get("ft_reasons", []):
            raise ApiError("Choose the exclusion reason.")
    if stage not in ("cal", "ta", "ft", "vs"):
        raise ApiError("Unknown stage.")
    with p.lock:                       # checked and written in one go: two votes at once cannot both be "not the last"
        if stage == "vs":                  # only the verifier votes here, and only on the records drawn for them
            rec = p.by_id().get(body["record"]) or {}
            set_name = rec.get("set", "main") if rec.get("set", "main") in SETS else "main"
            sv = screening._prep(p, set_name)[0]
            vf = sv["_verify"]
            if not vf or vf["reviewer"] != me or body["record"] not in vf["order"]:
                raise ApiError("This record is not in a verification sample of yours, so nothing was saved. Go back to the "
                               "screening page and open your task from there.")
            if vf["ended"]:
                raise ApiError("This verification sample has been ended by the project lead, so nothing was saved: its votes "
                               "stand as they were when it ended.")
            if not sv["_verify_open"]:                     # they can now see what was decided before
                raise ApiError("This verification sample is finished, so nothing was saved: its votes were fixed when you "
                               "voted on its last record.", 409, {"sample_finished": True})
            # The vote that finishes the sample fixes every vote in it, so it has to be meant: the page asks first and
            # sends "finish". (Asked for here and not only in the page: a second tab does not know how many are left.)
            counted = sv["_verify_votes"]
            if body["record"] not in counted and len(counted) == len(vf["order"]) - 1 and body.get("finish") is not True:
                raise ApiError("This is the last record of the verification sample: your vote on it finishes the sample "
                               "and fixes its votes. Nothing was saved yet.", 409, {"last_of_sample": True})
        if stage == "ft":
            closed_to_verifier(p)
        # how long the record was on screen: kept only while the project records times and this person has not opted out
        recording = timing.on_for(p, me)
        took = timing.clean(body.get("time"), full_text=stage == "ft") if recording else None
        dec = screening.cast(p, me, body["record"], stage, d, body.get("reason", ""), body.get("note", ""), body.get("study", ""),
                             time=took)
    # "timed": whether a time was kept for this vote; "timing": how things stand for this person now (a page opened
    # before the lead switched recording on or off, or before their own choice in another tab, puts itself right)
    out = {"saved": dec, "timed": recording, "timing": timing.status(p, me)}
    if stage == "vs":                  # (whether that was the vote that finished the sample: the page then leaves it)
        out["sample_finished"] = not screening._prep(p, set_name)[0]["_verify_open"]
    return out


def screened_alone(p: Project, set_name: str) -> bool:
    """The records outside a set's agreement sample are screened by one reviewer (with the AI behind, or not)."""
    sv = p.state_ro().get("sets", {}).get(set_name) or {}
    return (sv.get("remainder_mode") or screening.plan(p, set_name)["mode"]) == "single"


def h_agreement(q, body):
    p = need_project()
    g = lambda k, d="": (q.get(k) or [d])[0]
    set_name, scope = g("set", "main"), g("scope", "sample")
    if scope == "fulltext":             # its disagreements name records at full text: not for someone screening a
        closed_to_verifier(p)           # verification sample (the records mixed into it are there)
    ag = screening.agreement(p, set_name, scope)
    me = APP.me()
    if scope == "remainder" and not p.is_lead(me) and screened_alone(p, set_name):
        # One reviewer screens these records alone: the other's votes on them are second looks at exclusions the AI
        # would keep, so how many there are (or anything counting them) would show what the AI kept. Only your own count.
        mine = {ag["reviewers"][0]: "done_a", ag["reviewers"][1]: "done_b"}.get(me)
        for k in ("done_a", "done_b", "both_done", "changed_before_compare"):
            if k != mine:
                ag.pop(k, None)
        ag.update({"complete": False, "note": "One reviewer screens these records alone, so there is nothing to compare."})
    if not ag["complete"]:                # nobody (the lead included) sees the comparison until both have finished
        ag["disagreements"] = []
        ag["stats"] = {}
        ag.pop("meets_threshold", None)
        ag.pop("changed_before_compare", None)       # (it would say that the other reviewer has changed a vote)
    elif scope == "sample" and p.is_lead(APP.me()):
        pl = screening.plan(p, set_name)
        if pl["ai_second_screener"]:      # what the AI stopping rule will say, before the lead applies the rule
            cov = ai.coverage(p, set_name)
            if cov["missing"]:
                ag["ai_preview"] = {"missing": cov["missing"]}
            else:
                v = ai.validate(p, set_name)
                ag["ai_preview"] = {"keep_rate": v["keep_rate"], "limit": pl["ai_keep_rate_stop"],
                                    "over": v["keep_rate"] is not None and v["keep_rate"] > pl["ai_keep_rate_stop"]}
    return ag


def h_conflicts(q, body):
    p = need_project()
    names = {r["id"]: r["name"] for r in p.reviewers()}
    by = p.by_id()
    out = []
    for c in screening.conflicts(p):           # what the two need to decide together: the record itself too
        r = by.get(c["id"]) or {}
        out.append({**c, "abstract": r.get("abstract", ""), "authors": (r.get("authors") or "")[:200], "year": r.get("year", ""),
                    "journal": r.get("journal", ""), "doi": r.get("doi", ""), "pubtype": r.get("pubtype", "")})
    studies = sorted({str(o.get("study")) for o in screening.ft_outcomes(p).values() if o.get("study")} |
                     {str(v.get("study")) for c in out for v in c["votes"].values() if v and v.get("study")})
    return {"conflicts": out, "names": names, "reasons": p.settings_ro().get("ft_reasons", []), "studies": studies}


def h_resolve(q, body):
    p = need_project()
    same_person(body)
    stage, d = body.get("stage"), body.get("decision")
    if stage == "ta" and d not in screening.TA_CHOICES:
        raise ApiError("Choose Include, Maybe or Exclude.")
    if stage == "ft":
        if d not in screening.FT_CHOICES:
            raise ApiError("Choose a full-text decision.")
        if d == "exclude" and body.get("reason") not in p.settings_ro().get("ft_reasons", []):
            raise ApiError("Choose the exclusion reason.")
    if stage not in ("ta", "ft") or not body.get("record"):
        raise ApiError("Unknown stage.")
    if body["record"] not in p.by_id():                  # checked before anything is saved
        raise ApiError("That record is not in the project.")
    p.add_resolution(APP.me(), body["record"], body["stage"], body["decision"], body.get("reason", ""), body.get("note", ""),
                     body.get("study", ""))
    return h_conflicts(q, body)


# --------------------------------------------------------------------------------------------------------- AI
def h_ai(q, body):
    p = need_project()
    me = APP.me()
    c = ai.config(p)
    cov = {s: ai.coverage(p, s) for s in SETS if any(r["set"] == s for r in p.records())}
    if not p.is_lead(me):         # how many of the AI's answers could not be read is worked out from its answers: as
        cov = {s: {"records": c_["records"], "done": c_["done"] + c_["failed"], "missing": c_["missing"]}
               for s, c_ in cov.items()}                   # on the Overview, they count as done (the page shows n/a)
    out = {"config": c, "coverage": cov,
           "status": ai.Runner.status(p), "is_lead": p.is_lead(me), "visible": ai.visible(p, me),
           "key_set": bool(userconfig.ai_key()), "endpoint": ai.endpoint()[0], "elm": ai.is_elm(ai.endpoint()[0]),
           "prompts": ai.prompts(p), "fingerprints": {ps: v[1] for ps, v in ai.current_keys(p).items()},
           "planned": p.settings_ro()["screening"].get("ai_second_screener"),
           "models_fixed": ai._sample_done(p) or bool(c.get("locked")), "limit": screening.plan(p, "main")["ai_keep_rate_stop"]}
    if out["visible"] and evaluation.verifying(p, me):
        # the AI's answers on known studies would tell someone screening a verification sample which of its records the
        # AI kept (none of the sampled ones; most of those mixed in): not shown until they have finished
        sv = p.state_ro().get("sets", {}).get("main", {})
        if sv.get("sample"):
            out["validation"] = ai.validate(p, "main")
        out["known"] = [{"known": k["known"], "record": k["record"], "hidden": bool(k.get("record")), "hidden_why": "verifying"}
                        for k in ai.known_check(p)]
    elif out["visible"]:
        sv = p.state_ro().get("sets", {}).get("main", {})
        if sv.get("sample"):
            out["validation"] = ai.validate(p, "main")
        out["known"] = ai.known_check(p)
    else:
        # the lead may check the known studies before the sample is finished, except any that are in the
        # calibration or sample records, which stay hidden until both reviewers have finished them
        sv = p.state_ro().get("sets", {}).get("main", {})
        started = sv.get("phase") not in (None, "not_started")
        hidden = set(sv.get("sample", [])) | {r for rnd in sv.get("calibration_rounds", []) for r in rnd}
        out["known"] = []
        for k in ai.known_check(p):
            if p.is_lead(me) and started and k.get("record") and k["record"] not in hidden:
                out["known"].append(k)
            else:
                out["known"].append({"known": k["known"], "record": k["record"], "hidden": bool(k.get("record")),
                                     "hidden_why": "sample" if started else "not_started"})
    return out


def h_ai_config(q, body):
    p = need_project()
    return ai.save_config(p, APP.me(), body)


def h_ai_models(q, body):
    try:
        return ai.list_models()
    except ai.SetupError as e:
        raise ApiError(str(e))


def h_ai_run(q, body):
    p = need_project()
    scope = body.get("scope", "all")
    recs = p.records()
    if scope == "known":
        ids = [k["record"] for k in ai.known_check(p) if k.get("record")]
        if not ids:
            raise ApiError("None of the project's known studies are in the records yet.")
        label = "known studies"
    elif scope == "trial":
        import random as _r
        st = p.state_ro().get("sets", {}).get("main", {})
        hidden = set(st.get("sample", []))
        pool = [r["id"] for r in recs if r["id"] not in hidden]
        ids = _r.Random(7).sample(pool, min(20, len(pool)))
        label = "trial of 20 records"
    else:
        ids = [r["id"] for r in recs]
        label = "all records"
    return ai.Runner.start(p, APP.me(), ids, label)


def h_ai_stop(q, body):
    p = need_project()
    p.require_lead(APP.me())
    ai.Runner.stop(p)
    return ai.Runner.status(p)


# --------------------------------------------------------------------------------------------------------- full text
def h_fulltext(q, body):
    p = need_project()
    me = APP.me()
    by = p.by_id()
    V = screening.votes(p)
    fto = screening.ft_outcomes(p)
    found = fulltext.search_results(p)
    rows = []
    for rid, o in sorted(fto.items()):
        r = by.get(rid)
        if not r:
            p.note_syncing()
            continue
        mine = screening.latest(V, "ft", rid, me)
        show = o["status"] != "pending"
        sr = found.get(rid, {})
        rows.append({"id": rid, "title": r["title"], "year": r["year"], "journal": r["journal"], "doi": r["doi"],
                     "authors": (r.get("authors") or "")[:80], "pdf": fulltext.has_pdf(p, rid),
                     "search": {"status": sr.get("status", ""), "url": util.web_url(sr.get("url", "")), "source": sr.get("source", "")},
                     "mine": mine["d"] if mine else "", "my_reason": (mine or {}).get("reason", ""),
                     "status": o["status"] if o["status"] != "pending" else ("waiting for others" if mine else "to do"),
                     "final_reason": o.get("reason", "") if show else "", "required": o.get("required", []),
                     # both included it but named its study differently (shown once both have decided)
                     "study": o.get("study", "") if o["status"] == "included" else "", "study_names": o.get("study_names", [])})
    need = needs_pdf(p)
    st = fulltext.Fetcher.status(p)
    st.pop("log", None)
    other = fulltext.Fetcher.running_elsewhere(p)
    st["elsewhere"] = ((p.reviewer(other) or {}).get("name") or other) if other else ""
    return {"rows": rows, "reasons": p.settings_ro().get("ft_reasons", []), "fetch": st,
            "email_set": bool(userconfig.load().get("unpaywall_email")),
            "counts": {"reports": len(rows), "with_pdf": sum(1 for x in rows if x["pdf"]), "missing": len(need),
                       "never_searched": sum(1 for r in need if not found.get(r)), "waiting": len(fulltext.waiting(p))},
            "library_prefix": p.settings_ro()["fulltext"].get("library_prefix") or ""}


def needs_pdf(p: Project) -> list:
    """Reports at full text with no PDF yet (except those excluded, not retrievable, or ongoing studies, which
    are listed from their registry entries and have no report to find)."""
    return [rid for rid, o in sorted(screening.ft_outcomes(p).items())
            if o["status"] not in ("excluded", "not_retrieved", "ongoing") and not fulltext.has_pdf(p, rid)]


def h_ft_upload(q, body, raw):
    p = need_project()
    rid = (q.get("record") or [""])[0]
    if rid not in screening.ft_outcomes(p):
        raise ApiError("That record is not at full-text screening.")
    other = fulltext.pdf_owner(p, util.sha256_bytes(raw), but=rid)
    if other and (q.get("same_ok") or [""])[0] != "1":
        raise ApiError(f"This PDF is already the PDF of {other}. If it really holds both reports, add it again and confirm.")
    fulltext.save_pdf(p, APP.me(), rid, raw, same_as=other)
    return {"ok": True}


def h_ft_fetch(q, body):
    p = need_project()
    ids = [i for i in (body.get("ids") or needs_pdf(p)) if i in screening.ft_outcomes(p)]
    if not ids:
        raise ApiError("Every report at full text already has a PDF.")
    return fulltext.Fetcher.start(p, APP.me(), ids)


def h_ft_stop(q, body):
    p = need_project()
    fulltext.Fetcher.stop(p)
    return fulltext.Fetcher.status(p)


def h_ft_bulk(q, body, raw):
    p = need_project()
    name = os.path.basename((q.get("filename") or ["file.pdf"])[0].replace("\\", "/"))
    return fulltext.add_many(p, APP.me(), raw, name, list(screening.ft_outcomes(p)))


def h_ft_missing(q, body):
    p = need_project()
    return {"missing": fulltext.missing(p, needs_pdf(p)), "library_prefix": p.settings_ro()["fulltext"].get("library_prefix") or ""}


def h_ft_waiting(q, body):
    p = need_project()
    by = p.by_id()
    fto = screening.ft_outcomes(p)
    rows = []
    for w in fulltext.waiting(p):
        sug = w.get("suggest", "")
        rows.append({**w, "suggest_title": by.get(sug, {}).get("title", "") if sug else ""})
    return {"waiting": rows, "records": [{"id": r, "title": by[r]["title"], "year": by[r].get("year", ""),
                                          "authors": (by[r].get("authors") or "").split(";")[0][:40], "pdf": fulltext.has_pdf(p, r)}
                                         for r in sorted(fto) if r in by]}


def h_ft_assign(q, body):
    p = need_project()
    rid = body.get("record", "")
    if rid not in screening.ft_outcomes(p):
        raise ApiError("Choose a report that is at full-text screening.")
    fulltext.assign_waiting(p, APP.me(), body.get("name", ""), rid, bool(body.get("replace")), bool(body.get("same_ok")))
    return h_ft_waiting(q, body)


def h_ft_discard(q, body):
    p = need_project()
    fulltext.discard_waiting(p, APP.me(), body.get("name", ""))
    return h_ft_waiting(q, body)


def h_ft_remove(q, body):
    p = need_project()
    fulltext.remove_pdf(p, APP.me(), body.get("record", ""), body.get("sha", ""))
    return {"ok": True}


def h_ft_fetch_status(q, body):
    st = fulltext.Fetcher.status(need_project())
    st.pop("log", None)
    return st


# --------------------------------------------------------------------------------------------------------- reports
def h_prisma(q, body):
    """Reviewers see the diagram as the lead does, except that while the AI second screener is behind single
    screening, records waiting for a second look are counted as excluded (so no number shows what the AI said)."""
    p = need_project()
    lead = p.is_lead(APP.me())
    note = "" if lead or not any(sv.get("ai_net_active") for sv in p.state_ro().get("sets", {}).values()) else (
        "Records excluded at title and abstract that are waiting for a second look are counted here as excluded, so that "
        "these numbers never show what the AI said about a record. The project lead's diagram counts them as still open.")
    return {"counts": prisma.counts(p, blind=not lead), "svg": prisma.svg(p, blind=not lead),
            "summary": summary_for(p, lead), "blind_note": note,
            "downloads": {k: download_blocked(p, k) for k in DOWNLOADS}, "is_lead": lead}


# --------------------------------------------------------------------------------------------------------- sync problems
def h_sync(q, body):
    p = need_project()
    lead = p.is_lead(APP.me())
    return {"warnings": p.sync_warnings(), "is_lead": lead, "conflicts": p.lead_conflicts() if lead else [],
            "backups": p.backups() if lead else {}}


def h_sync_use(q, body):
    p = need_project()
    if body.get("copy"):
        p.use_conflict_copy(APP.me(), body["copy"])
    else:
        p.use_conflict_copies(APP.me(), body.get("suffix", ""))
    return h_sync(q, body)


def h_sync_keep(q, body):
    p = need_project()
    if body.get("copy"):
        p.keep_current_version(APP.me(), body["copy"])
    else:
        p.keep_current_versions(APP.me(), body.get("suffix", ""))
    return h_sync(q, body)


def h_sync_dismiss(q, body):
    p = need_project()
    p.dismiss_notices(APP.me())
    return h_sync(q, body)


def h_restore(q, body):
    if not APP.project:
        raise ApiError("No project is open.", 409)
    p = APP.project
    try:
        used = p.restore_backup(APP.me(), body.get("file", ""), body.get("copy", ""), bool(body.get("older_ok")))
    except OlderCopy as e:
        raise ApiError(str(e), 409) from None
    return {"restored": body.get("file", ""), "copy": used}


def h_integrity(q, body):
    p = need_project()
    lead = p.is_lead(APP.me())
    try:
        rep = p.integrity_report()
    except DamagedFile as e:
        rep = {"problems": [{"kind": "damaged", "file": e.what, "text": str(e), "fix": "backup", "sig": ""}], "accepted": [],
               "checked": util.now(), "sealed": "", "records_sealed": 0}
    # The checks of the full-text PDFs name records, and only a record that reached full text can have a PDF: left out
    # for someone screening a verification sample (the records mixed into it are at full text; the sampled ones are not)
    try:
        blind = bool(evaluation.verifying(p, APP.me()))
    except DamagedFile:                 # (the report above says which file: there is nothing to keep from anyone)
        blind = False
    if blind:
        about_pdf = lambda x: str(x.get("kind") or "").startswith("pdf")                 # noqa: E731
        rep = {**rep, "problems": [x for x in rep["problems"] if not about_pdf(x)],
               "accepted": [x for x in rep.get("accepted") or [] if not (isinstance(x, dict) and about_pdf(x))]}
    return {"report": rep, "warnings": p.sync_warnings(), "repairs": p.repairs(), "is_lead": lead, "pdf_checks_hidden": blind,
            "conflicts": p.lead_conflicts() if lead else [], "backups": p.backups() if lead else {}, "dev": p.dev(), "result": q.get("_result")}


def h_integrity_fix(q, body):
    p = need_project()
    me = APP.me()
    kind = body.get("kind")
    result = ""
    if kind in ("pdf", "adopt", "keep", "takeoff", "stray"):      # the repairs of full-text PDFs: their answers say which
        closed_to_verifier(p)                                      # records have one (see h_integrity)
    if kind == "chain":
        r = p.restore_chained(me, body.get("file", ""))
        result = f"Put back {util.plural(r['rows'], 'row')} from this computer's copy." + (
            f" {r['kept_aside']:,} later row{'s' if r['kept_aside'] != 1 else ''} could not be checked and {'were' if r['kept_aside'] != 1 else 'was'} "
            "kept aside with the file as it was (state/_replaced); the person's own computer puts back its own rows when "
            "they next open ReviewDesk." if r["kept_aside"] else "")
    elif kind == "pdf":
        p.restore_pdf(me, body.get("record", ""))
    elif kind == "adopt":
        p.adopt_pdfs(me)
    elif kind == "keep":
        p.adopt_pdfs(me, body.get("record", ""))
    elif kind == "takeoff":
        p.take_off_found_pdf(me, body.get("record", ""))
    elif kind == "stray":
        p.move_stray_files(me)
    elif kind == "seal":
        p.restore_seal_copy(me)
    elif kind == "reseal":
        p.reseal(me, body.get("reason", ""))
    elif kind == "accept":
        p.accept_problem(me, body.get("sig", ""), body.get("reason", ""))
    else:
        raise ApiError("Unknown repair.")
    return h_integrity({"_result": result}, body)


# --------------------------------------------------------------------------------------------------------- extraction
def h_extract(q, body):
    p = need_project()
    me = APP.me()
    f = review.form(p)
    out = review.overview(p, me)
    out.update({"is_lead": p.is_lead(me), "me": me, "form_full": f, "templates": {k: v["name"] for k, v in review.TEMPLATES.items()},
                "reviewers": [{"id": r["id"], "name": r.get("name", "")} for r in p.reviewers()],
                "rob_people": review.rob_people(p), "tools": {k: {"name": t["name"]} for k, t in robplan.tools(p).items()},
                "plan_recorded": robplan.recorded(p),
                "answered": sorted(review._answered_fields(p)) if p.is_lead(me) else []})
    return out


def h_extract_study(q, body):
    p = need_project()
    me = APP.me()
    key = review.resolve_any(p, (q.get("key") or [""])[0])      # an old link to a study whose first report changed still works
    st = next((s for s in review.studies(p) if s["key"] == key), None)
    if not st:
        raise ApiError("That study is not among the included studies (its full-text decision may have changed).", 404)
    by = p.by_id()
    reps = [{"id": r, "title": by.get(r, {}).get("title", ""), "year": by.get(r, {}).get("year", ""),
             "journal": by.get(r, {}).get("journal", ""), "doi": by.get(r, {}).get("doi", ""), "pdf": fulltext.has_pdf(p, r)}
            for r in st["reports"]]
    keys = [s["key"] for s in review.studies(p)]
    i = keys.index(key)
    return {"study": {**st, "reports": reps}, "form": review.form(p), "state": review.study_state(p, key, me),
            "rob": review.rob_view(p, key, me), "is_lead": p.is_lead(me), "me": me, **rob_choices(p, key, me),
            "rob_people": review.rob_people(p), "prev": keys[i - 1] if i else "", "next": keys[i + 1] if i + 1 < len(keys) else "",
            "position": i + 1, "total": len(keys)}


def rob_choices(p: Project, key: str, me: str) -> dict:
    """What the Risk of bias tab needs to add an assessment: the plan's designs with their tools, and the design
    extraction recorded for the study (as this person may see it)."""
    pl = robplan.plan(p)
    ts = robplan.tools(p)
    used = {d["tool"] for d in pl.get("designs") or [] if d.get("tool")} | {a["tool"] for a in review.rob_view(p, key, me)}
    return {"plan": {"recorded": bool(pl.get("recorded")), "designs": [{k: d.get(k) for k in ("id", "name", "tool", "none", "reason")}
                                                                       for d in pl.get("designs") or []]},
            "tools": {k: robtools.public(ts[k]) if k in ts else robtools.public(robplan.tool_def(p, k)) for k in used},
            "suggest": robplan.suggested(p, key, me)}


def h_extract_form(q, body):
    p = need_project()
    if body.get("template"):
        t = review.TEMPLATES.get(body["template"])
        if not t:
            raise ApiError("Unknown template.")
        new = json.loads(json.dumps(t))
        new.update({"template": body["template"], "first": body.get("first", ""), "second": body.get("second", "")})
    else:
        new = body.get("form") or {}
    return {"form": review.save_form(p, APP.me(), new)}


def h_extract_value(q, body):
    p = need_project()
    review.save_value(p, APP.me(), body.get("key", ""), body.get("f", ""), body.get("v"), body.get("pg", ""))
    return {"ok": True}


def h_extract_done(q, body):
    p = need_project()
    review.set_done(p, APP.me(), body.get("key", ""), bool(body.get("done")), body.get("seen", ""))
    return h_extract_study({"key": [body.get("key", "")]}, {})


def h_extract_agree(q, body):
    p = need_project()
    review.agree_value(p, APP.me(), body.get("key", ""), body.get("f", ""), body.get("v"))
    return h_extract_study({"key": [body.get("key", "")]}, {})


def h_rob_define(q, body):
    p = need_project()
    review.define_assessment(p, APP.me(), body.get("key", ""), str(body.get("design") or ""), str(body.get("label", "")),
                             body.get("a", ""), bool(body.get("deleted")))
    return h_extract_study({"key": [body.get("key", "")]}, {})


def h_rob_judge(q, body):
    p = need_project()
    review.judge(p, APP.me(), body.get("key", ""), body.get("a", ""), body.get("d", ""), body.get("j", ""), body.get("q", ""))
    return {"ok": True}


def h_rob_done(q, body):
    p = need_project()
    review.rob_done(p, APP.me(), body.get("key", ""), body.get("a", ""), bool(body.get("done")))
    return h_extract_study({"key": [body.get("key", "")]}, {})


def h_rob_agree(q, body):
    p = need_project()
    review.rob_agree(p, APP.me(), body.get("key", ""), body.get("a", ""), body.get("d", ""), body.get("j", ""), body.get("why", ""))
    return h_extract_study({"key": [body.get("key", "")]}, {})


def h_audit(q, body):
    p = need_project()
    return {"entries": exports.audit_for(p, p.is_lead(APP.me()))[-500:]}


DOWNLOADS = {
    "records": ("records.csv", "text/csv", exports.records_csv),
    "decisions": ("all decisions.csv", "text/csv", exports.decisions_csv),
    "included": ("included reports.ris", "application/x-research-info-systems", exports.included_ris),
    "excluded": ("excluded full texts with reasons.csv", "text/csv", exports.excluded_fulltext_csv),
    "searchlog": ("search log.csv", "text/csv", exports.search_log_csv),
    "ai": ("AI decisions.csv", "text/csv", exports.ai_csv),
    "audit": ("audit log.csv", "text/csv", lambda p: exports.audit_csv(p, p.is_lead(APP.me()))),
    "prisma": ("PRISMA flow diagram.svg", "image/svg+xml", lambda p: prisma.svg(p, blind=not p.is_lead(APP.me())).encode("utf-8")),
    "summary": ("methods summary.txt", "text/plain; charset=utf-8",
                lambda p: summary_for(p, p.is_lead(APP.me())).encode("utf-8")),
    "dedup": ("de-duplication log.csv", "text/csv", None),
    "missing_ris": ("PDFs still to find.ris", "application/x-research-info-systems", lambda p: fulltext.missing_ris(p, needs_pdf(p))),
    "missing_csv": ("PDFs still to find.csv", "text/csv", lambda p: fulltext.missing_csv(p, needs_pdf(p))),
    "evaluation": ("evaluation data.zip", "application/zip", evaluation.evaluation_zip),
    "evaluation_share": ("evaluation data for sharing.zip", "application/zip", lambda p: evaluation.evaluation_zip(p, share=True)),
    "extraction": ("data extraction.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", review.extraction_xlsx),
    "rob": ("risk of bias.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", review.rob_xlsx),
    "rob_svg": ("risk of bias summary.svg", "image/svg+xml", lambda p: review.rob_svg(p).encode("utf-8")),
}


def comparing_now(p: Project) -> bool:
    """True while a calibration round or agreement sample is open: each reviewer's votes must stay unseen."""
    return any(sv.get("phase") in ("calibration", "sample") for sv in p.state_ro().get("sets", {}).values())


def download_blocked(p: Project, kind: str) -> str:
    """Files listing every vote (or every record's outcome, which shows where the AI disagreed with a single
    reviewer) go only to the lead, and not while one reviewer could see a vote the other has yet to match, nor to a
    lead who is in the middle of screening a verification sample (reporting._core_block gives the same answers)."""
    if kind == "ai":
        if not p.is_lead(APP.me()):
            return "Only the project lead can download the AI's answers."
        if not ai.visible(p, APP.me()):
            return "The AI's answers can be downloaded once the agreement sample is finished."
        return ""
    if kind not in ("records", "decisions", "evaluation", "evaluation_share"):
        return ""
    if not p.is_lead(APP.me()):
        return ("This file shows every reviewer's decisions and, where they are recorded, how long each took, so only the "
                "project lead can download it." if kind.startswith("evaluation") else
                "This file shows every reviewer's decisions, so only the project lead can download it.")
    if kind != "records" and evaluation.open_sample(p):       # (the votes of a verification sample still being screened)
        return evaluation.open_sample(p)
    if comparing_now(p):
        return ("This file shows every reviewer's decisions, so it can be downloaded once the calibration round and "
                "agreement sample are finished.")
    n = screening.half_voted(p)
    if n:
        return (f"This file shows every reviewer's decisions, and on {n:,} record{'s' if n != 1 else ''} one reviewer has "
                "voted and the other has not yet. It can be downloaded when both have voted on every record they share.")
    return ""


def dedup_csv(p: Project) -> bytes:
    dd = p.dedup_report()
    imps = {m["id"]: m for m in p.imports()}
    rows = [[m["kept"], (imps.get(m.get("from_import", ""), {}).get("original_name") or imps.get(m.get("from_import", ""), {}).get("filename")
             or m.get("from_record", "")), m["title"], m["year"],
             m.get("journal", ""), m.get("doi", ""), m["rule"]] for m in dd.get("merges", [])]
    return exports._csv(["Kept as", "Removed record from", "Removed title", "Year", "Journal", "DOI", "Matched on"], rows)


ROUTES_GET = {
    "/api/app": h_app_page, "/api/fs": h_fs, "/api/guidance": h_guidance, "/api/worksheet": h_worksheet_get,
    "/api/searchlog": h_searchlog_get, "/api/imports": h_imports, "/api/dedup": h_dedup, "/api/record": h_record,
    "/api/records": h_records, "/api/overview": h_overview, "/api/tasks": h_tasks, "/api/queue": h_queue,
    "/api/agreement": h_agreement, "/api/conflicts": h_conflicts, "/api/ai": h_ai, "/api/fulltext": h_fulltext,
    "/api/fulltext/fetch": h_ft_fetch_status, "/api/prisma": h_prisma, "/api/audit": h_audit, "/api/sync": h_sync,
    "/api/fulltext/waiting": h_ft_waiting, "/api/fulltext/missing": h_ft_missing, "/api/integrity": h_integrity, "/api/extract": h_extract,
    "/api/extract/study": h_extract_study,
}
ROUTES_POST = {
    "/api/project/open": h_open, "/api/project/create": h_create, "/api/project/close": h_close, "/api/me": h_me,
    "/api/user": h_user, "/api/settings": h_settings, "/api/worksheet": h_worksheet_put, "/api/searchlog": h_searchlog_post,
    "/api/imports/remove": h_import_remove, "/api/imports/process": h_process, "/api/imports/link": h_import_link, "/api/dedup/notdup": h_notdup,
    "/api/dedup/merge": h_merge, "/api/screening/start": h_start, "/api/screening/calibration": h_calibration,
    "/api/screening/apply": h_apply, "/api/decide": h_decide, "/api/resolve": h_resolve, "/api/ai/config": h_ai_config,
    "/api/ai/models": h_ai_models, "/api/ai/run": h_ai_run, "/api/ai/stop": h_ai_stop, "/api/fulltext/fetch": h_ft_fetch,
    "/api/sync/use": h_sync_use, "/api/sync/keep": h_sync_keep, "/api/sync/dismiss": h_sync_dismiss, "/api/restore": h_restore,
    "/api/fulltext/stop": h_ft_stop, "/api/fulltext/assign": h_ft_assign, "/api/fulltext/discard": h_ft_discard,
    "/api/fulltext/remove": h_ft_remove, "/api/integrity/fix": h_integrity_fix, "/api/extract/form": h_extract_form,
    "/api/extract/value": h_extract_value, "/api/extract/done": h_extract_done, "/api/extract/agree": h_extract_agree,
    "/api/rob/define": h_rob_define, "/api/rob/judge": h_rob_judge, "/api/rob/done": h_rob_done, "/api/rob/agree": h_rob_agree,
}
ROUTES_RAW = {"/api/imports/upload": h_upload, "/api/fulltext/upload": h_ft_upload, "/api/fulltext/bulk": h_ft_bulk}

# The parts added in 1.3 keep their own routes and downloads. Their handlers take (project, me, query, body), and a
# download is (file name, type, make(project, me) -> bytes, why_not(project, me) -> "" or the reason it is refused).
MODULES = (readiness, timeline, certainty, reporting, robplan, evaluation, timing)
MODULE_DOWNLOADS: dict = {}


def _bind(fn):
    return lambda q, body: fn(need_project(), APP.me(), q, body)


for _m in MODULES:
    for _path, _fn in getattr(_m, "API_GET", {}).items():
        ROUTES_GET[_path] = _bind(_fn)
    for _path, _fn in getattr(_m, "API_POST", {}).items():
        ROUTES_POST[_path] = _bind(_fn)
    MODULE_DOWNLOADS.update(getattr(_m, "DOWNLOADS", {}))


class Handler(BaseHTTPRequestHandler):
    server_version = f"{NAME}/{VERSION}"

    def log_message(self, fmt, *args):
        if os.environ.get("REVIEWDESK_DEBUG"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def handle(self):
        try:
            super().handle()
        except GONE:                                   # the browser closed the tab or moved on: nothing to do
            pass

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")              # addresses never leak to other sites
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")  # other sites cannot load these responses
        self.send_header("X-Frame-Options", "SAMEORIGIN")                # other sites cannot frame ReviewDesk
        extra = dict(extra or {})
        extra.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'self'")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    # A request that is refused is answered before its body is read (nothing is taken from a sender who has not shown
    # the session token, and a file that is too large is not read to be turned away). If the connection were then
    # closed with that body still unread, the computer would break it off instead of ending it (a "reset"), and on
    # Windows that throws away the answer on its way to the sender: the page would report that ReviewDesk cannot be
    # reached instead of what the answer says (an earlier session's page, a file over the limit). So the rest of
    # what was sent is read and thrown away first: a little, for a short while, from a sender that is not one of
    # ReviewDesk's own pages; all of it from one that is (a page left open from an earlier session has the wrong
    # token, and may be sending a PDF: the browser says in its headers that the request comes from ReviewDesk's own
    # address, which a page from elsewhere cannot make it say).
    UNREAD_STRANGER = (1024 * 1024, 2.0)             # (bytes, seconds)
    UNREAD_OWN_PAGE = (8 * 1024 * 1024 * 1024, 120.0)

    def _unread(self) -> tuple:
        """How much of an unread body to read and throw away: all of it only when the browser itself says the request
        was started by a page at ReviewDesk's own address (a request that says nothing of where it comes from is some
        other program's, not a browser's: browsers name the page's address with every POST)."""
        port = self.server.server_address[1]
        named = self.headers.get("Origin") in (f"http://127.0.0.1:{port}", f"http://localhost:{port}") \
            or (self.headers.get("Sec-Fetch-Site") or "").lower() == "same-origin"
        own = named and self._this_computer() and not self._from_elsewhere()
        return self.UNREAD_OWN_PAGE if own else self.UNREAD_STRANGER

    def _discard_body(self, most: int, seconds: float) -> None:
        self.close_connection = True
        try:
            self.wfile.flush()                             # the answer goes first: the sender is not kept waiting
            if self.headers.get("Transfer-Encoding"):
                n = -1
            else:
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = -1
            if n == 0:
                return
            if n < 0:                                      # no length to go by: "that was all", then read until they stop
                self.connection.shutdown(socket.SHUT_WR)
            left, end = (most if n < 0 else min(n, most)), time.monotonic() + seconds
            while left > 0:
                wait = end - time.monotonic()
                if wait <= 0:
                    break
                self.connection.settimeout(wait)
                got = self.rfile.read1(min(65536, left))
                if not got:
                    break
                left -= len(got)
        except (OSError, ValueError):                      # they have gone, or stopped sending: nothing more to do
            pass

    def _json(self, code, obj):
        try:
            data = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        except UnicodeEncodeError:            # text that UTF-8 cannot hold (a lone surrogate): sent as \u escapes
            data = json.dumps(obj, ensure_ascii=True, default=str).encode("ascii")
        self._send(code, data)

    def _from_elsewhere(self) -> bool:
        """True for a request a web page from another site (or another program's page on this computer) started:
        the browser says so in Origin and Sec-Fetch-Site. ReviewDesk's own pages are same-origin."""
        origin = self.headers.get("Origin")
        port = self.server.server_address[1]
        if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            return True
        return (self.headers.get("Sec-Fetch-Site") or "").lower() in ("cross-site", "same-site")

    def _this_computer(self) -> bool:
        """The request is addressed to this computer by name: 127.0.0.1 or localhost, with or without a port, and
        nothing else in the Host header (a page on another site that has been pointed at this computer, 'DNS
        rebinding', still sends its own site's name)."""
        return bool(re.fullmatch(r"(127\.0\.0\.1|localhost)(:\d{1,5})?", self.headers.get("Host") or "", flags=re.I))

    def _authorised(self, q) -> bool:
        tok = self.headers.get("X-Token") or (q.get("t") or [""])[0]
        if not secrets.compare_digest((tok or "").encode("utf-8", "replace"), APP.token.encode("utf-8")):
            return False
        return self._this_computer() and not self._from_elsewhere()

    def send_error(self, code, message=None, explain=None):
        """The answers Python's server gives by itself (a request it cannot read, a method ReviewDesk does not use)
        carry the same headers as every other answer, and no page of their own."""
        self.close_connection = True
        try:
            body = b"" if (code < 200 or code in (204, 304) or getattr(self, "command", "") == "HEAD") else \
                json.dumps({"error": "ReviewDesk could not read that request."}).encode("utf-8")
            self._send(code, body, extra={"Connection": "close"})
            if getattr(self, "headers", None) is not None:      # (whatever came with it was not read)
                self._discard_body(*self.UNREAD_STRANGER)
        except Exception:                               # noqa: BLE001, S110 - the connection has gone: nothing to answer
            pass

    def _probe(self, q):
        """Is this ReviewDesk the caller's own? A second start asks before it opens the browser here (main.py). The
        caller sends a number used once; the answer is that number signed with this computer's session token, which
        only a ReviewDesk with the same settings can make. The token itself is never sent to find out, so another
        program that has taken ReviewDesk's port learns nothing."""
        n = (q.get("n") or [""])[0][:64]
        proof = hmac.new(APP.token.encode("utf-8"), n.encode("utf-8", "replace"), "sha256").hexdigest() if n else ""
        return self._json(200, {"name": NAME, "version": VERSION, "proof": proof})

    def do_GET(self):
        with request_scope():                 # the project's files are looked at once a request (Project.sig)
            self._get()

    def do_POST(self):
        with request_scope():
            self._post()

    def _get(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if not self._this_computer():
                return self._json(403, {"error": "ReviewDesk only answers this computer."})
            if u.path == "/api/probe" and not self._from_elsewhere():
                return self._probe(q)
            if u.path in ("/", "/index.html"):
                return self._static("index.html")
            if u.path.startswith("/static/"):
                return self._static(u.path[len("/static/"):])
            if not self._authorised(q):
                return self._json(403, {"error": EXPIRED, "expired": True})
            if (u.path.startswith("/files/") or u.path in VERIFIER_CLOSED) and APP.project:
                closed_to_verifier(APP.project)      # (a verification sample is screened without sight of these)
            if u.path.startswith("/files/fulltext/"):
                return self._pdf(u.path.rsplit("/", 1)[-1])
            if u.path.startswith("/files/waiting/"):
                return self._waiting_pdf(urllib.parse.unquote(u.path.rsplit("/", 1)[-1]))
            if u.path.startswith("/download/"):
                return self._download(u.path.rsplit("/", 1)[-1])
            fn = ROUTES_GET.get(u.path)
            if not fn:
                return self._json(404, {"error": "Not found"})
            self._json(200, fn(q, {}))
        except Exception as e:
            self._error(e)

    def _post(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if not self._authorised(q):
                self._json(403, {"error": EXPIRED, "expired": True})
                return self._discard_body(*self._unread())
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0:
                self._json(400, {"error": "ReviewDesk could not read that request."})
                return self._discard_body(*self.UNREAD_STRANGER)
            if n > MAX_UPLOAD:
                self._json(413, {"error": "That file is too large (over 300 MB)."})
                return self._discard_body(*self.UNREAD_OWN_PAGE)
            raw = self.rfile.read(n) if n else b""
            self._same_project(u.path)
            if APP.project and u.path not in self.NO_PROJECT_CHECK:    # whoever changes something has opened the project
                versions.note_open(APP.project, APP.me())              # with this version (noted once: see versions.py)
                if u.path not in self.ALLOWED_WHEN_TOO_OLD:
                    stop = versions.must_upgrade(APP.project, APP.me())
                    if stop:                 # someone works on this project with a version this one cannot read: no
                        raise ApiError(stop, 409)                      # change is saved until this one is replaced
            if APP.project and u.path.startswith(VERIFIER_CLOSED_CHANGES):
                closed_to_verifier(APP.project)      # (a verification sample is screened without sight of these)
            if u.path in ROUTES_RAW:
                return self._json(200, ROUTES_RAW[u.path](q, {}, raw))
            fn = ROUTES_POST.get(u.path)
            if not fn:
                return self._json(404, {"error": "Not found"})
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, UnicodeDecodeError, RecursionError):
                raise ApiError("The request could not be read. Reload the page and try again.")
            if not isinstance(body, dict):
                raise ApiError("The request could not be read. Reload the page and try again.")
            try:
                body = shapes.check(u.path, body)          # the kind of every field, before anything is saved
            except shapes.BadRequest as e:
                raise ApiError(str(e)) from None
            self._json(200, fn(q, body))
        except Exception as e:
            self._error(e)

    NO_PROJECT_CHECK = ("/api/project/open", "/api/project/create", "/api/project/close", "/api/user")
    ALLOWED_WHEN_TOO_OLD = ("/api/me",)          # saying who you are changes nothing in the project

    def _same_project(self, path: str) -> None:
        """A change sent from a page opened on another project (a tab left open after switching project) is refused,
        so it can never land on a record with the same ID in the project open now."""
        want = self.headers.get("X-Project")
        if want and path not in self.NO_PROJECT_CHECK and APP.project and want != APP.project.folder_key():
            raise ApiError("This page belongs to another project than the one now open in ReviewDesk (it was switched in "
                           "another tab). Reload the page; nothing was saved.", 409)

    def _error(self, e):
        if isinstance(e, GONE):
            return None
        try:
            return self._error_reply(e)
        except GONE:
            return None

    def _error_reply(self, e):
        if isinstance(e, ApiError):
            return self._json(e.code, {**e.data, "error": str(e)})
        if isinstance(e, DamagedFile):
            p = APP.project
            copies = {}
            try:
                copies = p.backups() if p else {}
            except Exception:                # noqa: BLE001, S110 - no list of copies: the message is shown without one
                pass
            good = [c for c in copies.get(e.what, []) if not c.get("unreadable")]
            lead = ""
            try:                             # the project file is its lead's to put back: this computer's copies say who
                if p and e.what == MARKER and good:
                    _, lid, name = p.lead_by_copies()
                    if lid != APP.me():
                        good, lead = [], name or lid or "someone else"
            except Exception:                # noqa: BLE001 - then no copy is offered here
                good = []
            return self._json(409, {"error": str(e), "damaged": {"file": e.what, "can_restore": bool(good), "copies": good,
                                                                 **({"lead": lead} if lead else {})}})
        if isinstance(e, PermissionDenied):
            return self._json(403, {"error": str(e)})
        if isinstance(e, (ProjectError, ai.SetupError, FormatError)):
            return self._json(400, {"error": str(e)})
        tb = traceback.format_exc()
        plain = util.file_problem(e, APP.project.dir if APP and APP.project else "")
        if plain:                                     # a file open in another program, a path too long, ...: say so
            try:
                util.append_jsonl(os.path.join(userconfig.data_dir(), "errors.jsonl"), {"t": util.now(), "error": ai.redact(tb),
                                                                                           "version": VERSION, "shown": plain})
            except Exception:                # noqa: BLE001, S110 - an error log that cannot be written must not hide the error
                pass
            return self._json(409, {"error": plain})
        try:
            util.append_jsonl(os.path.join(userconfig.data_dir(), "errors.jsonl"), {"t": util.now(), "error": ai.redact(tb),
                                                                                       "version": VERSION})
        except Exception:                    # noqa: BLE001, S110 - as above
            pass
        sys.stderr.write(tb)
        ref = util.one_line(f"{type(e).__name__}: {e}")[:200]
        return self._json(500, {"error": "Something went wrong inside ReviewDesk: a fault in the program, not something you "
                                         "did. Try again; if it happens again, please report it, quoting this reference: "
                                         f"{ref}. The details are saved in "
                                         f"{os.path.join(userconfig.data_dir(), 'errors.jsonl')}."})

    def _static(self, rel):
        rel = rel.split("?")[0]
        path = os.path.normpath(os.path.join(STATIC, rel))
        if not path.startswith(STATIC + os.sep) or not os.path.isfile(path):
            return self._json(404, {"error": "Not found"})
        # fixed types: on Windows, mimetypes reads the registry, which some installed programs have changed
        ctype = STATIC_TYPES.get(os.path.splitext(path)[1].lower()) or mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            data = f.read()
        if rel == "index.html":
            data = data.replace(b"__VERSION__", VERSION.encode())
        self._send(200, data, ctype, {"Content-Security-Policy": PAGE_CSP, "Cross-Origin-Opener-Policy": "same-origin"})

    def _pdf(self, name):
        p = need_project()
        rid = name[:-4] if name.endswith(".pdf") else name
        if rid not in p.by_id():
            return self._json(404, {"error": "No such record."})
        path = fulltext.pdf_path(p, rid)
        if not os.path.isfile(path):
            return self._json(404, {"error": "No PDF for this record yet."})
        with open(path, "rb") as f:
            self._send(200, f.read(), "application/pdf", {"Content-Disposition": f'inline; filename="{rid}.pdf"',
                                                          "Content-Security-Policy": "frame-ancestors 'self'"})

    def _waiting_pdf(self, name):
        p = need_project()
        f = os.path.join(fulltext._waiting_dir(p), os.path.basename(name))
        if not name.lower().endswith(".pdf") or not os.path.isfile(f):
            return self._json(404, {"error": "That file is no longer waiting."})
        with open(f, "rb") as fh:
            self._send(200, fh.read(), "application/pdf", {"Content-Disposition": "inline",
                                                           "Content-Security-Policy": "frame-ancestors 'self'"})

    def _download(self, kind):
        p = need_project()
        why = evaluation.blocked(p, APP.me())            # someone screening a verification sample: no download at all
        if why:
            return self._json(403, {"error": why, "verifying": True})
        if kind == "dedup":
            name, ctype, data = "de-duplication log.csv", "text/csv", dedup_csv(p)
        elif kind in MODULE_DOWNLOADS:
            name, ctype, make, why_not = MODULE_DOWNLOADS[kind]
            why = why_not(p, APP.me()) if why_not else ""
            if why:
                return self._json(403, {"error": why})
            data = make(p, APP.me())
        else:
            if kind not in DOWNLOADS:
                return self._json(404, {"error": "Unknown download."})
            name, ctype, fn = DOWNLOADS[kind]
            why = download_blocked(p, kind)
            if why:
                return self._json(403, {"error": why})
            data = fn(p)
        st = p.settings()
        short = st.get("short_title") or " ".join(str(st.get("title") or "").split()[:6])[:50].rstrip(" ,;:-") or "review"
        fname = urllib.parse.quote(util.safe_filename(f"{short} - {name}"))     # a name Windows accepts as it is
        self._send(200, data, ctype, {"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"})


# The page's own rules for the browser: only ReviewDesk's own files, no inline scripts, nothing sent elsewhere.
PAGE_CSP = ("default-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; frame-src 'self'; object-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'")

EXPIRED = ("This browser tab does not have ReviewDesk's current address (it was opened from an old link, a bookmark or "
           "another browser). Use the tab ReviewDesk opened when it started, or copy the full address, including the part "
           "after ?t=, from the ReviewDesk window.")
GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows, SO_REUSEADDR would let a second program take a port that is in use; ask for sole use instead.
    allow_reuse_address = not sys.platform.startswith("win")

    def server_bind(self):
        import socket
        if sys.platform.startswith("win") and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], GONE):
            return
        super().handle_error(request, client_address)


class PortTaken(OSError):
    """The port asked for (--port) cannot be used: another program has it."""
    def __init__(self, port: int):
        super().__init__(f"port {port} is in use")
        self.port = port


def serve(port: int | None = None) -> tuple:
    """Listen on the usual port (so open tabs keep working after a restart), or any free port if it is taken."""
    global APP
    # The port is taken first, and only then is the last project opened (which can take seconds): two starts at
    # the same moment (a double double-click) then cannot both think the usual port is free.
    if port is None:
        try:
            httpd = Server(("127.0.0.1", DEFAULT_PORT), Handler)
        except OSError:
            httpd = Server(("127.0.0.1", 0), Handler)
    else:
        try:
            httpd = Server(("127.0.0.1", port), Handler)
        except OSError as e:
            raise PortTaken(port) from e
    try:
        APP = App()
    except BaseException:
        httpd.server_close()
        raise
    url = f"http://127.0.0.1:{httpd.server_address[1]}/?t={APP.token}"
    return httpd, url
