"""The project folder: what is in it, who may write which file, and the record store.

Layout (inside the shared project folder)
  reviewdesk-project.json   settings: question, criteria, reviewers, screening plan   (written by the lead)
  state/state.json          screening state, ID map, de-duplication overrides         (lead)
  state/records.json        the de-duplicated records                                 (lead)
  state/dedup.json          every merge and the per-file counts                       (lead)
  state/resolutions@<dev>.jsonl  decisions agreed together after a disagreement       (lead)
  searches/worksheet.json   the search-building worksheet                             (anyone; edits are version-checked)
  searches/log-<ID>@<dev>.jsonl  each person's search log entries                     (that person)
  imports/                  database exports and a .meta.json beside each             (anyone; new files only)
  decisions/<ID>@<dev>.jsonl  each reviewer's screening decisions, append-only        (that reviewer, on that computer)
  fulltexts/<record>.pdf    full texts                                                (anyone)
  ai/                       AI screening settings, prompts and answers                (lead)
  audit/<ID>@<dev>.jsonl    what each person did, when, with which version            (that person)

Every file has one writer, so a sync service never has to merge two people's edits to the same file. Files
that grow (decisions, logs) also carry a short name for the computer (<dev>), so a person who works on two
computers still never has two computers writing one file.
"""
from __future__ import annotations

import contextlib
import copy
import glob
import hashlib
import json
import os
import random
import secrets
import re
import sys
import threading
import time

from . import NAME, SCHEMA, VERSION, integrity, userconfig, util, versions
from .dedup import Index, RULE_TEXT, SET_RANK, fingerprint, kept_first, merge_into, possible_duplicates
from .parsers import FormatError, parse_bytes

MARKER = "reviewdesk-project.json"
SETS = {"main": "Database searches", "update": "Update search", "other": "Other methods"}
# Windows allows paths of 259 characters (unless long paths are turned on). The longest names ReviewDesk makes inside
# a project, from the project folder on, are a PDF waiting to be placed while it is being placed
# (fulltexts\_unmatched\<36 characters> (99).pdf.placing-<computer>: 81) and a removed import's details
# (imports\_removed\<44 characters> (99).ris.meta (99).json: 85); tests/test_windows.py checks every name made.
WAITING_NAME = 40              # characters in the name of a PDF waiting to be placed (the name it came with is kept too)
IMPORT_NAME = 48               # characters in the name of an imported file in the imports folder
LONGEST_NAME = 85
SAFE_PATH = 250                # the project folder + \ + the longest name stays under this
LONG_PATH = SAFE_PATH - 2 - LONGEST_NAME       # the longest project folder path that leaves room for every file


def path_too_long(folder: str) -> str:
    """'' if this computer can use every file ReviewDesk makes in this project folder; otherwise what to do about it."""
    folder = os.path.abspath(folder)
    if len(folder) <= LONG_PATH or not util.windows_path_limit():
        return ""
    return (f"This project's folder path is {len(folder)} characters long. The files ReviewDesk makes inside it add up "
            f"to {LONGEST_NAME + 1} characters more, but Windows on this computer allows at most {util.WINDOWS_MAX_PATH} "
            "characters in a path, so it may refuse to save or open some of them, and your work would not be saved. A "
            f"folder path of up to {LONG_PATH} characters is safe. " + util.LONG_PATH_ADVICE)


# the parts of the screening plan that are fixed once screening of a set has started
PLAN_KEYS = ("mode", "reviewers", "sample_fraction", "kappa_threshold", "single_reviewer", "second_reviewer",
             "ai_second_screener", "ai_keep_rate_stop", "conflicts_at_title_abstract", "seed")


class PermissionDenied(Exception):
    pass


class ProjectError(Exception):
    pass


class OlderCopy(ProjectError):
    """A restore that would put back a copy older than work the lead did on another computer."""


_SCOPE = threading.local()


@contextlib.contextmanager
def request_scope():
    """One request to ReviewDesk: within it, a project's files are looked at once for Project.sig (and again after
    this process writes a file). Each thread has its own; outside a request (tests, scripts) every call looks."""
    _SCOPE.sigs = {}
    try:
        yield
    finally:
        _SCOPE.sigs = None


class DamagedFile(ProjectError):
    """A shared project file that cannot be read (for example emptied by a sync tool or a crash)."""
    def __init__(self, path: str, what: str):
        self.path, self.what = path, what
        super().__init__(f"The project file {what} cannot be read: it may have been emptied or cut short by the sync "
                         "service. Restore an earlier version (in OneDrive: right-click the file, Version history), or, "
                         "if you are the project lead, use the copy ReviewDesk kept on your computer.")


class MissingFile(DamagedFile):
    """A shared project file that is not in the folder although the project needs it (not synced yet, or removed)."""
    def __init__(self, path: str, what: str):
        self.path, self.what = path, what
        ProjectError.__init__(self, f"The project file {what} is not on this computer yet, but this project already has "
                              "records, so ReviewDesk needs it to show the review as it is. If this computer has only just "
                              "been set up, OneDrive may still be copying the project folder: wait a few minutes, then "
                              "choose Try again. If it does not arrive, it was deleted or moved: the project lead can "
                              "restore a copy kept on their computer, or put it back from OneDrive's recycle bin or "
                              "version history.")


DEFAULT_SETTINGS = {
    "tool": NAME, "schema": SCHEMA,
    "title": "", "short_title": "", "question": "", "review_type": "intervention",
    "reviewers": [],                          # [{"id": "CH", "name": "...", "role": "lead"|"reviewer"}]
    "criteria": {"include": [], "exclude": [], "rules": []},
    "ft_reasons": ["Wrong population", "Wrong intervention", "Wrong comparator", "Wrong study design",
                   "Wrong outcomes", "Wrong publication type", "Not in English"],
    "highlight": {"include": [], "exclude": []},
    "known_studies": [],                      # [{"doi": "", "pmid": "", "title": ""}] for checking searches and the AI
    "screening": {
        "reviewers": [],                      # the two people who screen in duplicate
        "calibration_size": 100, "calibration_gate": 0.0,
        "mode": "sample_then_single",         # dual | sample_then_single | single
        "sample_fraction": 0.20, "kappa_threshold": 0.80,
        "single_reviewer": "", "second_reviewer": "",
        "ai_second_screener": False, "ai_keep_rate_stop": 0.50,
        "conflicts_at_title_abstract": "forward",   # forward | discuss
        "seed": None,
        # Save with each vote how long its record was on screen (timing.py). Not part of the plan: the lead may switch
        # it on or off at any time, each person may leave their own votes out, and the reviewers are told on the page.
        "record_time": False,
    },
    "fulltext": {"mode": "dual", "library_prefix": "",
                 "disagreement_default": ""},       # the protocol's own rule, in the lead's words (set-up shows an example)
    "other_sets": {"update": {"mode": "inherit"}, "other": {"mode": "dual"}},
    "registers": [],                          # [{"name": "ClinicalTrials.gov", "records": 12}] for PRISMA
    "registration": {"registry": "", "id": "", "date": "", "url": ""},   # the protocol's registration (PRISMA item 24a)
    "dates": {"start": "", "searches": "", "update_search": "", "end": ""},   # planned dates, as the protocol gives them
    "risk_of_bias_tools": [],                 # before 1.3.0: the tools expected (kept; the plan replaces them, robplan.py)
    "risk_of_bias_other": "",
}


SETTING_NAMES = {"title": "title", "short_title": "short title", "question": "review question", "review_type": "type of question",
                 "reviewers": "people", "criteria": "eligibility criteria", "ft_reasons": "full-text exclusion reasons",
                 "highlight": "highlight words", "known_studies": "known studies", "screening": "screening plan",
                 "fulltext": "full-text settings", "other_sets": "plans for update and other-methods records",
                 "registers": "trial registers", "amendments": "amendments",
                 "registration": "protocol registration", "dates": "planned dates",
                 "risk_of_bias_tools": "risk-of-bias tools", "risk_of_bias_other": "other risk-of-bias tools",
                 "risk_of_bias_plan": "risk-of-bias plan"}


class Project:
    _locks: dict[str, threading.RLock] = {}

    def __init__(self, folder: str):
        self.dir = os.path.abspath(folder)
        if not os.path.isfile(os.path.join(self.dir, MARKER)):
            raise ProjectError(f"{self.dir} is not a ReviewDesk project folder (there is no {MARKER} in it).")
        try:                                     # a project that needs a newer ReviewDesk is not opened by this one: it
            needs = util.read_json(os.path.join(self.dir, MARKER), {})     # could misread, or spoil, what the newer one wrote
        except (OSError, ValueError):
            needs = {}                           # (a file that cannot be read is dealt with where it is first needed)
        if isinstance(needs, dict) and versions.too_old(needs):
            raise ProjectError(versions.too_old(needs))
        self.lock = Project._locks.setdefault(self.dir, threading.RLock())
        self._cache: dict[str, tuple] = {}
        self._memo: dict = {}
        self._notices: dict = {}                 # problems found while reading (shown with the sync warnings): {key: (text, time)}
        self._key = ""
        self._root = ""
        self._cloud: tuple = (0.0, [])           # (when checked, project files stored online only on this computer)
        self._recheck_at = 0.0                   # the data checks left something alone while it arrives: look again then
        for sub in ("state", "searches", "imports", "decisions", "fulltexts", "ai", "audit", "extraction", "rob", "certainty"):
            os.makedirs(os.path.join(self.dir, sub), exist_ok=True)
        self._tidy_temp_files()
        # Look for online-only files before anything is read: reading one makes the sync service download it, after
        # which it no longer looks online only. What is found now is remembered until the notes are cleared.
        self._cloud_open: list = self.online_only_files(max_age=0)

    def exists(self) -> bool:
        return os.path.isfile(self.path(MARKER))

    def key(self) -> str:
        """A short name for this project that survives the folder being moved or renamed (it comes from the
        identity written into the project file when the project was made). Remembered on this computer per folder,
        so it is still known if the project file is damaged."""
        if self._key:
            return self._key
        try:
            d = util.read_json(self.path(MARKER), {}) or {}
            ident = str(d.get("project_id") or d.get("created") or "") if isinstance(d, dict) else ""
        except ValueError:
            ident = ""
        if ident:
            self._key = hashlib.sha256(ident.encode("utf-8")).hexdigest()[:12]
            userconfig.remember_key(self.dir, self._key)
            return self._key
        return userconfig.known_key(self.dir) or hashlib.sha256(os.path.basename(self.dir).encode("utf-8")).hexdigest()[:12]

    def _tidy_temp_files(self) -> None:
        """Remove half-written temporary files left by a crash (older than ten minutes), so the sync service does
        not copy them to everyone."""
        cutoff = time.time() - 600
        for sub in ("", "state", "ai", "searches", "imports", "decisions", "audit"):
            try:
                for e in os.scandir(self.path(sub) if sub else self.dir):
                    if e.name.startswith(".tmp-") and e.is_file() and e.stat().st_mtime < cutoff:
                        try:
                            os.remove(e.path)
                        except OSError:
                            pass
            except OSError:
                pass

    # ------------------------------------------------------------------------------------------------ change signature
    def sig(self) -> tuple:
        """Size and time of every file that results depend on: anything computed from the project can be reused
        until one of these changes (another person's computer syncing a file counts as a change). Inside one request
        (request_scope) the files are looked at once, and again only after this process has written a file: a page can
        ask for this thousands of times, and each look is a call to the file system for every file (slow on some
        computers, and in synced folders)."""
        scope = getattr(_SCOPE, "sigs", None)
        if scope is None:
            return self._sig()
        hit = scope.get(self.dir)
        if hit is not None and hit[0] == util.WRITES:
            return hit[1]
        gen = util.WRITES                      # read before looking, so a write meanwhile makes the next call look again
        out = self._sig()
        scope[self.dir] = (gen, out)
        return out

    def _sig(self) -> tuple:
        """Every file a result can depend on: the shared files, every person's rows, the search log and worksheet,
        the imports' files and details, and the audit log (each by size, time and this process's last write to it)."""
        out = []
        for f in (self.path(MARKER), self.path("state", "state.json"), self.path("state", "records.json"),
                  self.path("state", "seal.json"), self.path("state", "dedup.json"), self.path("extraction", "form.json"),
                  self.path("searches", "worksheet.json")):
            try:
                out.append(_stamp(f, os.stat(f)))
            except OSError:
                out.append(None)
        for sub in ("decisions", "state", "ai", "extraction", "rob", "certainty", "fulltexts", "searches", "imports", "audit"):
            try:
                for e in sorted(os.scandir(self.path(sub)), key=lambda e: e.name):
                    if sub == "imports":
                        use = not e.name.startswith(".") and e.is_file()
                    else:
                        use = e.name.endswith(".jsonl") or e.name == "config.json" or (sub == "state" and e.name.startswith("seal"))
                    if use:
                        out.append((sub, e.name) + _stamp(e.path, e.stat()))
            except OSError:
                pass
        return tuple(out)

    def memo(self, key, fn):
        """fn(), reused until the project's files change. The result is shared: callers must not change it."""
        s = self.sig()
        hit = self._memo.get(key)
        if hit is not None and hit[0] == s:
            return hit[1]
        val = fn()
        self._memo[key] = (s, val)
        return val

    # ------------------------------------------------------------------------------------------------ creation
    @staticmethod
    def create(folder: str, title: str, lead_name: str, lead_id: str) -> "Project":
        folder = os.path.abspath(folder)
        if clean_id(lead_id) in util.WINDOWS_RESERVED:
            raise ProjectError(f"{clean_id(lead_id)} cannot be used as initials, because Windows reserves it as a device "
                               "name. Add a letter (for example a middle initial).")
        os.makedirs(folder, exist_ok=True)
        if os.path.isfile(os.path.join(folder, MARKER)):
            raise ProjectError("There is already a ReviewDesk project in that folder. Open it instead.")
        clutter = [n for n in os.listdir(folder) if not n.startswith((".", "~", "desktop.ini"))]
        if clutter:
            raise ProjectError("Choose an empty folder for a new project (ReviewDesk creates its own files and subfolders).")
        lead_id = clean_id(lead_id)
        s = _deep_copy(DEFAULT_SETTINGS)
        s.update({"title": title.strip(), "short_title": "", "created": util.now(), "created_with": VERSION,
                  "project_id": secrets.token_hex(8),
                  "reviewers": [{"id": lead_id, "name": lead_name.strip(), "role": "lead"}]})
        s["screening"]["reviewers"] = [lead_id]
        s["screening"]["second_reviewer"] = lead_id
        s["screening"]["seed"] = random.SystemRandom().randint(10_000_000, 99_999_999)
        versions.stamp(s)
        util.write_json_atomic(os.path.join(folder, MARKER), s)
        util.write_text_atomic(os.path.join(folder, "READ ME FIRST.txt"), README_TEXT)
        p = Project(folder)
        util.write_json_atomic(p.path("state", "state.json"), {"rev": 1, "imports_order": [], "id_map": {}, "next_id": 1,
                                                                "overrides": {"not_dup": [], "merge": []}, "sets": {}})
        util.write_json_atomic(p.path("searches", "worksheet.json"), {"rev": 1, "framework": "pico", "concepts": [],
                                                                       "databases": [], "notes": ""})
        p.audit(lead_id, "create project", {"title": title})
        return p

    # ------------------------------------------------------------------------------------------------ paths and files
    def path(self, *parts) -> str:
        return os.path.join(self.dir, *parts)

    @staticmethod
    def dev() -> str:
        return userconfig.device_id()

    def _read_all(self, pattern: str, key: str) -> list:
        """Every row of every file matching pattern (one file per person per computer), in time order; the rows of one
        file always in the order they were written (see written_order)."""
        rows = []
        for n, f in enumerate(util.files_in(*os.path.split(pattern))):      # only the name is a pattern, never the folder
            got = self._cached(key + ":" + f, f, lambda f=f: util.read_jsonl(f))
            rows += [(t, n, k, d) for t, k, d in written_order(got)]
        rows.sort(key=lambda x: x[:3])
        return [d for *_, d in rows]

    def _cached(self, key: str, path: str, loader):
        """loader(), reused until the file changes: its size or time, or a write to it by this process (which counts
        even where the disk keeps file times only to 2 seconds)."""
        try:
            sig = _stamp(path, os.stat(path))
        except OSError:
            sig = None
        hit = self._cache.get(key)
        if hit and hit[0] == sig:
            return hit[1]
        val = loader()
        self._cache[key] = (sig, val)
        return val

    def _read_shared(self, rel: tuple, default, what: str, kind=None):
        """A shared JSON file (default if it is not there). One that cannot be read is a DamagedFile, and so, with
        kind, is one that can be read but holds something else than ReviewDesk writes there (a list where the
        settings should be): no page can work with it, and the copy kept on this computer puts it right."""
        path = self.path(*rel)
        try:
            d = util.read_json(path, default)
        except ValueError:
            raise DamagedFile(path, what)
        if kind is not None and d is not None and not isinstance(d, kind):
            raise DamagedFile(path, what)
        return d

    def settings_ro(self) -> dict:
        """The settings, merged with the defaults. Shared: do not change the result (use settings() for a copy)."""
        def load():
            s = self._read_shared((MARKER,), {}, MARKER, dict)
            self._saw_version(MARKER, s)
            out = _deep_copy(DEFAULT_SETTINGS)
            _merge(out, s or {})
            return out
        return self._cached("settings", self.path(MARKER), load)

    def settings(self) -> dict:
        return _deep_copy(self.settings_ro())

    def state_ro(self) -> dict:
        """The screening state. Shared: do not change the result (use state() for a copy to edit and save). A missing
        file is a project not yet started only while nothing has been imported or screened: once there are records,
        a seal or votes, it is a file not here yet (still syncing) or removed (MissingFile), never a fresh start."""
        def load():
            st = self._read_shared(("state", "state.json"), None, "state/state.json", dict)
            if st is None:
                if self._under_way():
                    raise MissingFile(self.path("state", "state.json"), "state/state.json")
                return {}
            self._saw_version("state/state.json", st)
            return st or {}
        return self._cached("state", self.path("state", "state.json"), load)

    def _under_way(self) -> bool:
        """Whether this project has been taken past its creation: records, a seal, or anyone's votes."""
        if os.path.isfile(self.path("state", "records.json")) or os.path.isfile(self.path("state", "seal.json")):
            return True
        try:
            return any(e.name.endswith(".jsonl") and e.stat().st_size > 0 for e in os.scandir(self.path("decisions")))
        except OSError:
            return False

    def state(self) -> dict:
        return _deep_copy(self.state_ro())

    def save_state(self, st: dict, user: str) -> None:
        self.require_lead(user)
        st["rev"] = int(st.get("rev", 0)) + 1
        st["saved_by"], st["saved_at"], st["saved_on"] = user, util.now(), self.dev()
        util.write_json_atomic(self.path("state", "state.json"), st)
        self._saw_version("state/state.json", st)      # (remembered now: a version put back before the next reading
        self.backup("state", "state.json")             # is still seen to be older than this one)

    def records(self) -> list:
        """The records. While a run of Remove duplicates that stopped part-way is waiting to be done again, records
        first found in the files it was adding are left out, as those files still count as not added."""
        recs = self._cached("records", self.path("state", "records.json"),
                            lambda: self._read_shared(("state", "records.json"), [], "state/records.json") or [])
        adding = self.adding_interrupted()
        if not adding:
            return recs
        key = tuple(adding)
        hit = self._cache.get("records_added")
        if hit and hit[0][0] is recs and hit[0][1] == key:     # the same list object (never an id(): ids are reused)
            return hit[1]
        out = [r for r in recs if str(r.get("first_source") or "").split("#")[0] not in adding]
        self._cache["records_added"] = ((recs, key), out)
        return out

    def adding_interrupted(self) -> list:
        """The imports a run of Remove duplicates was adding when ReviewDesk stopped part-way (closed, or the computer
        went to sleep) and that have not been added since: [] normally (see process_imports)."""
        try:
            st = self.state_ro()
        except DamagedFile:
            return []
        run = st.get("processing")
        if not isinstance(run, dict) or not run.get("adding"):
            return []
        done = set(st.get("imports_order") or [])
        return [i for i in run["adding"] if i not in done]

    # ------------------------------------------------------------------------------------------------ local copies
    def _local(self, *parts) -> str:
        return os.path.join(userconfig.data_dir(), "projects", self._local_root(), *parts)

    def _local_root(self) -> str:
        """This computer's private folder for this project folder: its copy of its own decisions, the counts it has
        seen, and copies of the lead's files. It belongs to one folder, by its place on this computer, and is never
        handed to another: a copy of the project folder (a backup or a practice copy), even one that is later deleted
        or renamed, can never pass its votes or files to the real project. A project folder that is moved or renamed
        takes its private folder with it (see _adopt_moved)."""
        if not self._root:
            self._root = self.folder_key()
            try:
                d = os.path.join(userconfig.data_dir(), "projects", self._root)
                if not os.path.exists(d):
                    self._adopt_moved(d)
                if not os.path.isfile(os.path.join(d, "folder.json")):
                    os.makedirs(d, exist_ok=True)
                    util.write_json_atomic(os.path.join(d, "folder.json"), {"path": self.dir, "since": util.now()})
            except OSError:
                pass
        return self._root

    def _adopt_moved(self, d: str) -> None:
        """This project folder is new to this computer: if it is the same project moved or renamed (one private folder
        of this project names a place that no longer holds it: gone, empty, or holding another project), that private
        folder becomes this one's, with its own copies, backups and counts. Never when the old place still holds this
        project (a copy of the folder stays separate), when it cannot be read, when the folder the old place was in
        is not there either (the whole place may be on a drive or a network share that is not connected just now: a
        practice copy opened meanwhile must not take the real project's copies), or when more than one place could
        be it."""
        base, key = os.path.dirname(d), self.key()
        found = []
        try:
            names = os.listdir(base)
        except OSError:
            return
        for n in names:
            if not n.startswith(key + "-") or os.path.join(base, n) == d:
                continue
            try:
                was = str((util.read_json(os.path.join(base, n, "folder.json"), {}) or {}).get("path") or "")
            except (ValueError, OSError, AttributeError):
                continue
            if was and not userconfig.same_folder(was, self.dir) and not self._holds_this_project(was) \
                    and os.path.isdir(os.path.dirname(os.path.abspath(was))):
                found.append((n, was))
        if len(found) != 1:
            return
        n, was = found[0]
        try:
            info = util.read_json(os.path.join(base, n, "folder.json"), {}) or {}
            os.replace(os.path.join(base, n), d)
            util.write_json_atomic(os.path.join(d, "folder.json"), {**info, "path": self.dir, "moved_from": was,
                                                                     "moved": util.now()})
        except (OSError, ValueError):
            return
        self._notice("moved:" + n, f"This project was at {was} before. This computer's own copies of its files (kept for "
                     "the data checks and for restoring) have moved with it.", ttl=1800, info=True)

    def _holds_this_project(self, folder: str) -> bool:
        """Whether a folder still holds this project (True also when that cannot be told: it is not read)."""
        if not os.path.isdir(folder):
            return False
        try:
            d = util.read_json(os.path.join(folder, MARKER), None)
        except (ValueError, OSError):
            return True
        if not isinstance(d, dict):
            return False
        ident = str(d.get("project_id") or d.get("created") or "")
        return not ident or hashlib.sha256(ident.encode("utf-8")).hexdigest()[:12] == self.key()

    def folder_key(self) -> str:
        """This project in this folder on this computer: different for a copy of the folder, so a page opened on one
        can never write into the other."""
        me = os.path.normcase(os.path.abspath(self.dir))
        return self.key() + "-" + hashlib.sha256(me.encode("utf-8")).hexdigest()[:8]

    def backup(self, *rel) -> None:
        """Keep the last few versions of a shared file this computer wrote, outside the shared folder."""
        src = self.path(*rel)
        if not os.path.isfile(src):
            return
        d = self._local("backups")
        os.makedirs(d, exist_ok=True)
        name = "__".join(rel)
        try:
            with open(src, "rb") as fh:                # a plain copy: no platform copy routine, no file attributes
                data = fh.read()
            util.write_bytes_atomic(os.path.join(d, f"{name}.{backup_stamp(time.time())}"), data)
        except OSError:                                # the save itself is done; say that no copy of it could be kept
            self._notice("backup:" + name, f"ReviewDesk could not keep its safety copy of {'/'.join(rel)} on this computer "
                         "(its disk may be full). The file itself was saved, but Restore a copy kept on this computer will "
                         "not offer this version. Free some space on this computer; the next save keeps a copy again.",
                         ttl=1800)
            return
        self._notices.pop("backup:" + name, None)
        try:
            old = sorted(f for f in os.listdir(d) if f.startswith(name + "."))       # (oldest first: see backup_stamp)
            for f in old[:-5]:
                os.remove(os.path.join(d, f))
        except OSError:
            pass

    RESTORABLE = ("state/state.json", "state/records.json", "state/dedup.json", MARKER, "ai/config.json", "searches/worksheet.json",
                  "state/seal.json", "extraction/form.json")
    OBJECTS = (MARKER, "state/state.json")       # files that hold a JSON object or are damaged (_read_shared): a copy of one
                                                 # that holds anything else is no copy to put back

    def backups(self) -> dict:
        """{shared file: [copy, ...] newest first}, each copy with what it says about itself."""
        d = self._local("backups")
        out = {}
        if os.path.isdir(d):
            for f in sorted(os.listdir(d), reverse=True):
                name, _, stamp = f.rpartition(".")
                rel = name.replace("__", "/")
                info = {"file": f, "saved": backup_when(f)}
                try:
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        x = json.load(fh)
                    if rel in self.OBJECTS and not isinstance(x, dict):
                        info["unreadable"] = True
                    elif isinstance(x, list):
                        info["what"] = f"{len(x):,} records"
                    elif isinstance(x, dict):
                        bits = []
                        if x.get("rev") is not None: bits.append(f"revision {x['rev']}")
                        phases = {k: v.get("phase") for k, v in (x.get("sets") or {}).items() if isinstance(v, dict)}
                        if phases: bits.append("; ".join(f"{k}: {v}" for k, v in phases.items()))
                        if x.get("processed"): bits.append(f"de-duplicated {str(x['processed'])[:16].replace('T', ' ')}")
                        if x.get("updated"): bits.append(f"settings saved {str(x['updated'])[:16].replace('T', ' ')}")
                        if isinstance(x.get("passes"), dict):
                            bits.append("models " + " and ".join(str((v or {}).get("model", "?")).split("/")[-1] for v in x["passes"].values()))
                            if x.get("locked"): bits.append("fixed")
                        if x.get("saved_at") and not x.get("sets"): bits.append(f"saved {str(x['saved_at'])[:16].replace('T', ' ')}")
                        if isinstance(x.get("concepts"), list): bits.append(f"{len(x['concepts'])} concepts")
                        if isinstance(x.get("records"), dict) and "sealed" in x:     # state/seal.json
                            bits = [f"{len(x['records']):,} records sealed", f"updated {str(x.get('updated', ''))[:16].replace('T', ' ')}"]
                        if isinstance(x.get("sections"), list):                      # extraction/form.json
                            bits = [f"form revision {x.get('rev', 0)}", f"{sum(len(c.get('fields', [])) for c in x['sections'])} items"]
                        info["what"] = ", ".join(bits)
                except (ValueError, OSError):
                    info["unreadable"] = True
                out.setdefault(rel, []).append(info)
        return out

    # ------------------------------------------------------------------------------------------------ versions seen here
    # Each computer remembers, in its own folder for the project, the newest version of each of the lead's shared files
    # it has read (revision, when, where, by whom). A version older than that, put back later by the sync service or
    # from version history, is then reported (_older_than_seen), and "Restore a copy kept on this computer" can tell
    # when a newer version exists that this computer has no copy of (newer_work_elsewhere).
    _SEEN_LOCK = threading.Lock()
    RESTORE_ACTS = ("restore from this computer's copy", "use the sync service's other version")
    SEEN = ("state/state.json", MARKER, "searches/worksheet.json", "extraction/form.json", "state/seal.json")

    def _seen(self) -> dict:
        f = self._local("seen.json")
        def load():
            try:
                d = util.read_json(f, {}) or {}
            except (ValueError, OSError):
                d = {}
            return d if isinstance(d, dict) else {}
        return self._cached("seen", f, load)

    def _save_seen(self, seen: dict) -> None:
        try:
            os.makedirs(self._local(), exist_ok=True)
            util.write_json_atomic(self._local("seen.json"), seen)
        except OSError:
            pass

    def _saw_version(self, rel: str, d, reset: bool = False) -> None:
        """Remember a version of a shared file this computer has read, if it is the newest seen so far (a version saved
        later counts as newer even with a lower revision: the lead carried on from an older copy on purpose). With
        reset (a version the lead chose here): it becomes the one to compare with, even if older."""
        v = _version(d)
        if not v:
            return
        with Project._SEEN_LOCK:
            seen = copy.deepcopy(self._seen())
            cur = dict(seen.get(rel) or {}) if not reset else {"acts": (seen.get(rel) or {}).get("acts", 0)}
            n = cur.get("newest") or {}
            changed = reset
            if not n or _newer(v, n) or (v["at"] and n.get("at") and util.ts(v["at"]) > util.ts(n["at"])):
                if v != n:
                    cur["newest"], changed = v, True
            per = dict(cur.get("per") or {})
            if v["on"] and v["at"] and (reset or not per.get(v["on"]) or util.ts(v["at"]) > util.ts(per[v["on"]])):
                if per.get(v["on"]) != v["at"]:
                    per[v["on"]], changed = v["at"], True
            cur["per"] = per
            if changed:
                seen[rel] = cur
                self._save_seen(seen)

    def _older_than_seen(self) -> list:
        """Data problems: the screening state or the project file in the folder is older than a version this computer
        has already read (an older copy put back by the sync service or from version history). Not reported when the
        lead chose that version in ReviewDesk (restored a copy, or used the sync service's other version)."""
        probs = []
        acts: dict = {}
        for e in self.audit_entries():
            if e.get("action") in self.RESTORE_ACTS:
                f = str((e.get("details") or {}).get("file") or "")
                acts[f] = acts.get(f, 0) + 1
        for rel in ("state/state.json", MARKER):
            try:
                d = util.read_json(self.path(*rel.split("/")), None)
            except (ValueError, OSError):
                continue                                  # a damaged file is reported on its own
            v = _version(d)
            known = self._seen().get(rel) or {}
            if not v or not known.get("newest"):
                continue
            n = known["newest"]
            older = {}
            if v["rev"] is not None and n.get("rev") is not None and v["rev"] < n["rev"] and v["at"] and n.get("at") \
                    and util.ts(v["at"]) < util.ts(n["at"]):
                older = n
            was = (known.get("per") or {}).get(v["on"]) if v["on"] and v["at"] else ""
            if not older and was and util.ts(v["at"]) < util.ts(was):
                older = {"at": was, "on": v["on"]}
            if older and acts.get(rel, 0) > known.get("acts", 0):
                self._saw_version(rel, d, reset=True)     # the lead chose this version in ReviewDesk since
                older = {}
            if not older:
                if acts.get(rel, 0) != known.get("acts", 0):
                    with Project._SEEN_LOCK:
                        seen = copy.deepcopy(self._seen())
                        seen.setdefault(rel, {})["acts"] = acts.get(rel, 0)
                        self._save_seen(seen)
                continue
            when = lambda x: str(x or "")[:16].replace("T", " ")      # noqa: E731
            if rel == MARKER:
                text = (f"The project file ({MARKER}) is older than a version this computer has already used: it was saved "
                        f"{when(v['at'])}, but this computer has seen a version saved {when(older['at'])} on the same "
                        "computer. An older copy was probably put back (by the sync service, or from version history), so "
                        "settings changed since then (such as amendments to the criteria or the risk-of-bias plan) are not "
                        "in use. The project lead can put back the newer version: Restore a copy kept on this computer, or "
                        "use OneDrive's version history.")
            else:
                seen_rev = f"revision {older['rev']}" if older.get("rev") is not None else "a version"
                text = (f"The screening state (state/state.json) is older than a version this computer has already used: "
                        f"revision {v['rev']} is in the folder, but this computer has seen {seen_rev}, saved "
                        f"{when(older['at'])}{(' by ' + older['by']) if older.get('by') else ''}. An older copy was "
                        "probably put back (by the sync service, or from version history), so what was recorded since "
                        "then (such as starting screening or the agreement sample's result) is not in use. The project "
                        "lead can put back the newer version: Restore a copy kept on this computer, or use OneDrive's "
                        "version history.")
            probs.append({"kind": "older", "file": rel, "fixes": ["backup"], "text": text,
                          "sig": integrity.label(["older", rel, v, older])})
        return probs

    def _copy_versions(self, rel: str) -> list:
        """[(version, content)] of each copy of a shared file kept on this computer (not records.json: no version)."""
        out = []
        if rel == "state/records.json":
            return out
        d = self._local("backups")
        name = rel.replace("/", "__")
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if f.startswith(name + "."):
                try:
                    x = util.read_json(os.path.join(d, f), None)
                except (ValueError, OSError):
                    continue
                out.append((_version(x), x))
        return out

    def newer_work_elsewhere(self, rel: str, copy_data) -> list:
        """Newer versions of a shared file than a copy kept on this computer, that this computer has no copy of (so
        another computer saved them, and restoring the copy would undo that work): the version in the folder now, and
        the newest this computer has read. Judged by what each version says about itself (revision, when and where
        it was saved), so no kind of change is left out. The records and the de-duplication record go with the
        screening state, so a newer screening state counts for them too."""
        here = self.dev()
        names: dict = {}
        out = []
        for r in [rel] + (["state/state.json"] if rel in ("state/records.json", "state/dedup.json") else []):
            mine = self._copy_versions(r)
            if r == rel:
                base = _version(copy_data)
            else:                                     # this computer's newest copy of the screening state
                base = {}
                for v, _ in mine:
                    if not base or _newer(v, base):
                        base = v
            try:
                cur = util.read_json(self.path(*r.split("/")), None)
            except (ValueError, OSError):
                cur = None
            known = [x for x in (_version(cur), (self._seen().get(r) or {}).get("newest")) if x]
            if not base and isinstance(cur, dict) and isinstance(copy_data, dict) and r == rel and cur != copy_data \
                    and not any(x == cur for _, x in mine):
                out.append({"file": r, "what": "", "t": "", "computer": ""})     # a file that says nothing of itself
                continue
            for a in known:
                if base and _newer(a, base) and a.get("on") != here and not any(_same(a, v) for v, _ in mine):
                    if not names:
                        for e in self.audit_entries():
                            if e.get("computer") and e.get("computer_name"):
                                names[e["computer"]] = e["computer_name"]
                    out.append({"file": r, "what": f"revision {a['rev']}" if a.get("rev") is not None else "",
                                "t": str(a.get("at") or "")[:16].replace("T", " "), "by": a.get("by", ""),
                                "computer": names.get(a.get("on", ""), "another computer" if a.get("on") else "")})
                    break
        return out

    def restore_backup(self, user: str, rel: str, copy: str = "", older_ok: bool = False) -> str:
        """Put back a readable copy of a shared file kept on this computer (lead only): the one named, or the newest.
        Refused (unless older_ok) when a newer version of the file exists that this computer has no copy of, because
        the computer that saved it may hold it. If the project file itself is damaged it cannot say who the lead is:
        the newest copy of it on this computer does (lead_by_copies), and only that person puts it back. The copy is
        written over the file in one step, after the file as it was is kept in state/_replaced, so a stop part-way
        never leaves the project without the file. The restored screening state or project file is saved as a new
        version (a new revision, saved now), so other computers take it as the newest."""
        try:
            if rel == "searches/worksheet.json":          # written by anyone in the project
                self.require_member(user)
            else:
                self.require_lead(user)
        except DamagedFile:
            if rel != MARKER:
                raise
            known, lead, name = self.lead_by_copies()
            if known and lead != user:
                raise PermissionDenied("Only the project lead can put the project file back, and this computer's newest "
                                       f"copy of it names {name or lead or 'nobody'} as the lead. The lead's own computer "
                                       "shows them this page too, with a copy to put back if it keeps one; or anyone can "
                                       "use OneDrive's version history.") from None
        if rel not in self.RESTORABLE:
            raise ProjectError("That file cannot be restored here.")
        d = self._local("backups")
        name = rel.replace("/", "__")
        copies = sorted((f for f in (os.listdir(d) if os.path.isdir(d) else []) if f.startswith(name + ".")), reverse=True)
        if copy:
            if copy not in copies:
                raise ProjectError("That copy is no longer on this computer.")
            copies = [copy]
        for f in copies:
            try:
                with open(os.path.join(d, f), encoding="utf-8") as fh:
                    text = fh.read()
                data = json.loads(text)
            except (ValueError, OSError):
                continue
            if rel in self.OBJECTS and not isinstance(data, dict):
                continue
            when = backup_when(f)
            later = self.newer_work_elsewhere(rel, data)
            if later and not older_ok:
                last = later[-1]
                what = ", ".join(x for x in (last["what"], ("saved " + last["t"]) if last["t"] else "",
                                             ("by " + last["by"]) if last.get("by") else "") if x)
                raise OlderCopy(f"This computer's copy is from {when}, but a newer version of {last['file']} was saved "
                                f"after it{(' on ' + last['computer']) if last['computer'] else ''}"
                                f"{(' (' + what + ')') if what else ''}, and this computer has no copy of that version. "
                                "That computer may have a newer copy: restore it there, or use OneDrive's version history. "
                                "Restoring this older copy would undo that work.")
            target = self.path(*rel.split("/"))
            if rel == "state/seal.json":
                data = self._merge_seals(data, util.read_json(self._local("seal.json"), {}) or {})
                text = json.dumps(data, ensure_ascii=False)
            elif rel in ("state/state.json", MARKER) and isinstance(data, dict):
                if rel == "state/state.json":
                    try:
                        now = util.read_json(target, None)
                    except (ValueError, OSError):
                        now = None
                    revs = [x.get("rev") for x in (data, now if isinstance(now, dict) else {}, (self._seen().get(rel) or {}).get("newest") or {})]
                    data["rev"] = max([x for x in revs if isinstance(x, int) and not isinstance(x, bool)] or [0]) + 1
                    data["saved_by"], data["saved_at"], data["saved_on"] = user, util.now(), self.dev()
                else:
                    data["updated"], data["updated_by"], data["updated_on"] = util.now(), user, self.dev()
                    versions.stamp(data)
                    # An older copy never lowers the version the project needs: what the file it replaces says (if that
                    # can still be read), and what a verification sample already drawn asks for (an older ReviewDesk
                    # would count the records it sent on as excluded).
                    try:
                        was = util.read_json(target, None)
                        versions.require(data, str(was.get("min_version") or "") if isinstance(was, dict) else "")
                    except (ValueError, OSError):
                        pass
                    drawn = False
                    try:                                  # (the state says so by itself, whatever else can be read)
                        sets = self.state_ro().get("sets", {})
                        drawn = any(isinstance(sv, dict) and isinstance(sv.get("verify"), dict) for sv in sets.values())
                    except (ProjectError, ValueError, OSError, AttributeError):
                        pass
                    try:                                  # (... or the lead's log, if the state has lost the sample)
                        from . import screening
                        drawn = drawn or any(screening._prep(self, k)[0]["_verify"] for k in SETS)
                    except (ProjectError, ValueError, OSError, KeyError, TypeError, AttributeError):
                        pass
                    if drawn:
                        versions.require(data, versions.FEATURES["verification sample"])
                text = json.dumps(data, ensure_ascii=False, indent=1)
            if os.path.isfile(target):                     # the file as it was, kept first; then the copy over it
                keep = self.path("state", "_replaced")
                os.makedirs(keep, exist_ok=True)
                with open(target, "rb") as fh:
                    old = fh.read()
                util.write_bytes_atomic(util.unique_path(keep, os.path.basename(target) + "." + time.strftime("%Y%m%d-%H%M%S")), old)
            util.write_text_atomic(target, text)
            if rel == "state/seal.json":
                self._keep_seal_copy(data)
            self._cache.clear()
            self._memo.clear()
            if rel in self.SEEN:
                self._saw_version(rel, data, reset=True)
            self.audit(user, "restore from this computer's copy", {"file": rel, "copy": f, "older_than_work_elsewhere": bool(later)})
            return f
        raise ProjectError("This computer has no readable copy of that file. Restore it from OneDrive's version history.")

    def lead_by_copies(self) -> tuple:
        """(a readable copy was found, the lead's ID, the lead's name) according to the newest readable copy of the
        project file kept on this computer: what there is to go by when the project file itself cannot be read. A
        computer keeps a copy of each version it saved, so its newest one names whoever was the lead when this
        computer last wrote the file (someone else, if the lead was handed on from here; and whoever the copies came
        from, on a computer whose settings folder was copied from the lead's)."""
        d = self._local("backups")
        name = MARKER.replace("/", "__")
        for f in sorted((f for f in (os.listdir(d) if os.path.isdir(d) else []) if f.startswith(name + ".")), reverse=True):
            try:
                with open(os.path.join(d, f), encoding="utf-8") as fh:
                    data = json.load(fh)
            except (ValueError, OSError):
                continue
            if not isinstance(data, dict):             # (not a copy of the project file at all: as if it could not be read)
                continue
            people = data.get("reviewers")
            lead = next((r for r in people if isinstance(r, dict) and r.get("role") == "lead"), {}) if isinstance(people, list) else {}
            return True, str(lead.get("id") or ""), str(lead.get("name") or "")
        return False, "", ""

    def by_id(self) -> dict:
        recs = self.records()
        hit = self._cache.get("by_id")
        if hit and hit[0] is recs:                 # the same list object (never an id(): ids are reused)
            return hit[1]
        m = {r["id"]: r for r in recs}
        self._cache["by_id"] = (recs, m)
        return m

    # ------------------------------------------------------------------------------------------------ people
    def reviewers(self) -> list:
        return self.settings().get("reviewers", [])

    def reviewer(self, rid: str) -> dict | None:
        return next((r for r in self.reviewers() if r["id"] == rid), None)

    def lead_id(self) -> str:
        return next((r["id"] for r in self.reviewers() if r.get("role") == "lead"), "")

    def is_lead(self, rid: str) -> bool:
        r = self.reviewer(rid)
        return bool(r and r.get("role") == "lead")

    def require_member(self, rid: str) -> None:
        if not self.reviewer(rid):
            raise PermissionDenied("Choose who you are in this project first (top right).")

    def require_lead(self, rid: str) -> None:
        if not self.is_lead(rid):
            lead = self.reviewer(self.lead_id()) or {}
            raise PermissionDenied(f"Only the project lead ({lead.get('name', 'the lead')}) can do this, so that one "
                                   "person writes the shared project files.")

    def update_settings(self, user: str, changes: dict) -> dict:
        self.require_lead(user)
        if "risk_of_bias_plan" in changes:          # its own route checks it, and records changes as amendments
            raise ProjectError("The risk-of-bias plan is saved on its own (Project set-up, Risk-of-bias plan).")
        with self.lock:
            cur = util.read_json(self.path(MARKER), {}) or {}
            before = _deep_copy(cur)
            st = self.state()
            now_s = self.settings()
            started = any(v.get("phase") not in (None, "not_started") for v in st.get("sets", {}).values())
            locked = []
            if started:
                for k in ("criteria", "question"):
                    if k in changes and changes[k] != now_s.get(k):
                        locked.append(k)
                sc = changes.get("screening") or {}
                moved = [k for k in PLAN_KEYS if k in sc and sc[k] != now_s["screening"].get(k)]
                ft_now = (now_s.get("fulltext") or {}).get("mode", "dual")
                if isinstance(changes.get("fulltext"), dict) and changes["fulltext"].get("mode", ft_now) != ft_now:
                    moved.append("who assesses full texts")      # fixed with the rest of the plan (screening.fixed_ft_mode)
                if moved:
                    raise ProjectError("Screening has started, so the screening plan is fixed (it was recorded when screening "
                                       "began, as the protocol pre-specifies it). Changes refused: " + ", ".join(moved) + ".")
                for set_name, sv in st.get("sets", {}).items():
                    if set_name != "main" and sv.get("phase") not in (None, "not_started"):
                        o = (changes.get("other_sets") or {}).get(set_name)
                        if o is not None and o != now_s.get("other_sets", {}).get(set_name):
                            raise ProjectError(f"Screening of the {SETS[set_name].lower()} has started, so its plan is fixed.")
            _merge(cur, changes)
            if "reviewers" in changes:
                ids = [clean_id(r["id"]) for r in changes["reviewers"]]
                if len(set(ids)) != len(ids):
                    raise ProjectError("Each person needs different initials.")
                if any(not i for i in ids):
                    raise ProjectError("Give everyone initials (letters or numbers).")
                bad = [i for i in ids if i in util.WINDOWS_RESERVED]
                if bad:
                    raise ProjectError(f"{bad[0]} cannot be used as initials, because Windows reserves it as a device name. "
                                       "Add a letter (for example a middle initial).")
                if sum(1 for r in changes["reviewers"] if r.get("role") == "lead") != 1:
                    raise ProjectError("The project needs exactly one lead.")
                if started:
                    gone = {r["id"] for r in now_s["reviewers"]} - set(ids)
                    needed = set()
                    for sv in st.get("sets", {}).values():
                        pl = sv.get("plan_at_start") or {}
                        needed |= set(pl.get("reviewers", [])) | {pl.get("single_reviewer"), pl.get("second_reviewer")}
                        if isinstance(sv.get("verify"), dict):       # whoever screens a verification sample stays too
                            needed.add(sv["verify"].get("reviewer"))
                    from . import screening                          # (... also when only the lead's log still holds it)
                    needed |= {(screening._prep(self, k)[0]["_verify"] or {}).get("reviewer") for k in SETS}
                    if gone & needed:
                        raise ProjectError("Someone in the screening plan cannot be removed once screening has started.")
                if any(not (r.get("name") or "").strip() for r in changes["reviewers"]):
                    raise ProjectError("Give everyone a name.")
                if clean_id(user) not in ids:
                    raise ProjectError("Your own initials cannot be changed or removed: ReviewDesk records your work under "
                                       f"them ({clean_id(user)}).")
                cur["reviewers"] = [{"id": clean_id(r["id"]), "name": r["name"].strip(), "role": r.get("role", "reviewer")}
                                    for r in changes["reviewers"]]
                if not started:                     # nobody removed may stay in the screening plan
                    sc = cur.setdefault("screening", {})
                    sc["reviewers"] = [x for x in (sc.get("reviewers") or []) if x in ids]
                    for k in ("single_reviewer", "second_reviewer"):
                        if sc.get(k) and sc[k] not in ids:
                            sc[k] = ""
            check_values(cur)
            changed = sorted(k for k in set(cur) | set(before) if k not in ("updated", "updated_by", "updated_on")
                             and cur.get(k) != before.get(k))
            if not changed:
                return {"changed_after_start": [], "changed": []}
            if locked:                              # a dated record of what changed, for the report
                for k in locked:
                    cur.setdefault("amendments", []).append({"date": util.now(), "by": user, "what": k,
                                                            "before": now_s.get(k), "after": cur.get(k)})
            cur["updated"], cur["updated_by"], cur["updated_on"] = util.now(), user, self.dev()
            versions.stamp(cur)
            util.write_json_atomic(self.path(MARKER), cur)
            self._saw_version(MARKER, cur)             # (remembered now, not at the next reading: see _older_than_seen)
            self.backup(MARKER)
            details = {"changed": [SETTING_NAMES.get(k, k.replace("_", " ")) for k in changed]}
            timed = [bool((x.get("screening") or {}).get("record_time")) for x in (before, cur)]
            if timed[0] != timed[1]:                # said in so many words: the reviewers are being timed, or no longer
                details["time recording"] = "switched on" if timed[1] else "switched off"
                rest = [{k: v for k, v in (x.get("screening") or {}).items() if k != "record_time"} for x in (before, cur)]
                if rest[0] == rest[1]:              # and not called a change to the screening plan, which it is not
                    details["changed"] = [x for x in details["changed"] if x != SETTING_NAMES["screening"]] + ["time recording"]
            if locked:
                details["after screening started"] = [SETTING_NAMES.get(k, k) for k in locked]
            self.audit(user, "change settings", details)
            return {"changed_after_start": locked, "changed": changed}

    def need_version(self, user: str, version: str, quiet: bool = False) -> bool:
        """Lead only: say in the project file that the project now needs this version of ReviewDesk or a later one
        (something has been switched on that an older version would misread: versions.FEATURES). An older ReviewDesk
        then refuses to open the project, and one that has it open stops saving. True if the file was changed.
        With quiet, nothing else in the file changes, not even when and where it was last saved: for the lead's first
        opening with a newer version (server.project_summary), which is not a save anyone chose. A project file that
        the sync service has put back from an older copy must go on looking as old as it is (_older_than_seen), and
        two computers of the lead's that both do this write the same file."""
        self.require_lead(user)
        with self.lock:
            cur = self._read_shared((MARKER,), None, MARKER)
            if not isinstance(cur, dict) or not cur or not versions.require(cur, version):
                return False                   # (a project file that is not here just now is never written from nothing)
            if not quiet:
                cur["updated"], cur["updated_by"], cur["updated_on"] = util.now(), user, self.dev()
            util.write_json_atomic(self.path(MARKER), cur)
            if not quiet:
                self._saw_version(MARKER, cur)
                self.backup(MARKER)
            return True

    # ------------------------------------------------------------------------------------------------ audit
    def _live_notices(self) -> dict:
        now = time.time()                              # (other requests may add a note meanwhile: work on a snapshot)
        for k in [k for k, v in list(self._notices.items()) if v[2] and now - v[1] > v[2]]:
            self._notices.pop(k, None)                 # a passing note (a repair already made) is shown for a while only
        return dict(self._notices)

    def sync_notices(self) -> list:
        """Notes that need someone to act."""
        return [v[0] for v in self._live_notices().values() if not (len(v) > 3 and v[3])]

    def repairs(self) -> list:
        """Notes about repairs this computer has already made (nothing to do)."""
        return [v[0] for v in self._live_notices().values() if len(v) > 3 and v[3]]

    def _notice(self, key: str, text: str, ttl: float = 0, info: bool = False) -> None:
        self._notices[key] = (text, time.time(), ttl, info)

    def my_id(self) -> str:
        return userconfig.identity_for(self.dir, self.key())

    def note_syncing(self) -> None:
        self._notice("syncing", "Some records are not on this computer yet: the sync service is still copying the project "
                     "files. Wait a minute, then reload the page.", ttl=300)

    def dismiss_notices(self, user: str) -> None:
        """'I have dealt with these': forget the notes."""
        self.require_member(user)
        self._notices.clear()
        self._cloud = (0.0, [])                  # look again now: the folder may have been set to stay on this device
        self._cloud_open = []

    def audit(self, user: str, action: str, details: dict | None = None) -> None:
        util.append_jsonl(self.path("audit", f"{clean_id(user) or 'unknown'}@{self.dev()}.jsonl"),
                          {"t": util.now(), "who": user, "action": action, "details": details or {}, "version": VERSION,
                           "needs": versions.declares(self),   # the oldest ReviewDesk that can read this project now
                           "computer": self.dev(), "computer_name": util.computer_name()})

    def audit_entries(self) -> list:
        """Every audit entry, in time order. An entry that is in two files (a copy the sync service made of an audit
        file) is listed once. Shared: do not change the result."""
        def load():
            seen, out = set(), []
            for e in self._read_all(self.path("audit", "*.jsonl"), "audit"):
                k = json.dumps(e, sort_keys=True, ensure_ascii=False)
                if k not in seen:
                    seen.add(k)
                    out.append(e)
            return out
        return self.memo("audit_entries", load)

    # ------------------------------------------------------------------------------------------------ sync problems
    def sync_warnings(self) -> list:
        """Signs that a sync service kept two versions of a file. OneDrive names its copy 'state-LAPTOP-7Q2.json',
        Dropbox 'state (Sam's conflicted copy).json', Google Drive 'state (1).json'."""
        out = []
        fixed = {"": [MARKER], "state": ["state.json", "records.json", "dedup.json", "seal.json"], "ai": ["config.json"],
                 "searches": ["worksheet.json"], "extraction": ["form.json"]}
        for sub, names in fixed.items():
            try:
                present = os.listdir(self.path(sub) if sub else self.dir)
            except OSError:
                continue
            for n in present:
                for want in names:
                    stem, ext = os.path.splitext(want)
                    if n != want and n.lower().startswith(stem.lower()) and n.lower().endswith(ext.lower()) and \
                            re.match(r"^[- (_]", n[len(stem):]):
                        out.append(f"{(sub + '/') if sub else ''}{n} is a second version of {want}: two computers changed it "
                                   "before the sync service caught up, and ReviewDesk is using the other one. "
                                   + ("The two are combined when the project lead's computer next checks the project."
                                      if want == "seal.json" else
                                      "The project lead can compare them and choose (Sync problems, on the Overview)."))
        ids = {r["id"] for r in self.reviewers()}
        grow = {"decisions": r"^([A-Z0-9]{1,8})(@[0-9a-f]{6})?\.jsonl$", "audit": r"^([A-Z0-9]{1,8}|UNKNOWN)(@[0-9a-f]{6})?\.jsonl$",
                "state": r"^(resolutions)(@[0-9a-f]{6})?\.jsonl$", "ai": r"^(answers_[AB])(@[0-9a-f]{6})?\.jsonl$",
                "searches": r"^log-([A-Z0-9]{1,8})(@[0-9a-f]{6})?\.jsonl$"}
        for sub, rx in grow.items():
            try:
                present = os.listdir(self.path(sub))
            except OSError:
                continue
            for n in present:
                if not n.endswith(".jsonl") or n.startswith("."):
                    continue
                m = re.match(rx, n)
                if not m and sub in ("decisions", "state", "ai"):
                    continue                      # copies of these are merged in automatically; nothing is lost
                if not m:
                    out.append(f"{sub}/{n} looks like a copy made by the sync service: an entry that is in both files is "
                               "shown once. Tell the project lead.")
                elif sub == "decisions" and m.group(1) not in ids:
                    out.append(f"decisions/{n} belongs to someone who is no longer in the project list: their decisions "
                               "are not counted.")
        every = self.imports(include_missing=True)
        broken = [m["filename"] for m in every if m.get("_damaged")]
        if broken:
            out.append(f"The details file of {len(broken)} import{'' if len(broken) == 1 else 's'} cannot be read (damaged): "
                       + ", ".join(b + ".meta.json" for b in broken[:3]) + ". Restore it from OneDrive's version history "
                       "(right-click it in the imports folder).")
        recent = lambda m: not util.ts(m.get("added", "")) or time.time() - util.ts(m.get("added", "")) <= 3600   # noqa: E731
        missing = [m["filename"] for m in every if m.get("_missing") and not m.get("_damaged") and recent(m)]
        if missing:
            out.append(f"{len(missing)} imported file{' has' if len(missing) == 1 else 's have'} not finished syncing to this computer ({', '.join(missing[:3])}"
                       f"{'…' if len(missing) > 3 else ''}). Wait before processing imports or starting screening.")
        cloud = sorted(set(self.online_only_files()) | set(self._cloud_open))
        if cloud:
            out.append(online_only_message(len(cloud)))
        return out + self.sync_notices()

    def online_only_files(self, max_age: float = 60) -> list:
        """Project files whose contents are stored online only on this computer (OneDrive or SharePoint 'Files
        On-Demand', or a Mac's File Provider): ReviewDesk then waits for each download, and cannot read them
        offline. Checked from the folder listing, which downloads nothing; re-checked at most once a minute."""
        when, found = self._cloud
        if time.time() - when < max_age:
            return found
        found, todo = [], [self.dir]
        while todo:
            try:
                entries = list(os.scandir(todo.pop()))
            except OSError:
                continue
            for e in entries:
                if e.name.startswith("."):
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        todo.append(e.path)
                    elif util.online_only(e.stat(follow_symlinks=False)):
                        found.append(os.path.relpath(e.path, self.dir).replace(os.sep, "/"))
                except OSError:
                    pass
        found.sort()
        self._cloud = (time.time(), found)
        return found

    # The screening state, the records and the de-duplication record only make sense together (the state gives out the
    # IDs the records carry, and says which records have been screened): second versions of them are one decision.
    TOGETHER = ("state/state.json", "state/records.json", "state/dedup.json")

    def lead_conflicts(self) -> list:
        """Second versions of the lead's files made by the sync service, one entry per file, with what each version
        says about itself (revision, when and where it was saved, how many records), grouped by the sync service's
        suffix. The screening state, the records and the de-duplication record are one decision (see TOGETHER): the
        group's "state" entry gives one piece of advice for them, and what to do next."""
        self._combine_seal_versions()           # second versions of the seal are combined, not chosen between
        names = {"state": ["state.json", "records.json", "dedup.json", "seal.json"], "": [MARKER], "ai": ["config.json"],
                 "searches": ["worksheet.json"], "extraction": ["form.json"]}
        groups: dict = {}
        for sub, wants in names.items():
            folder = self.path(sub) if sub else self.dir
            try:
                present = os.listdir(folder)
            except OSError:
                continue
            for n in present:
                for want in wants:
                    stem, ext = os.path.splitext(want)
                    if n != want and n.startswith(stem) and n.endswith(ext) and re.match(r"^[- (_]", n[len(stem):]):
                        suffix = n[len(stem):-len(ext)] if ext else n[len(stem):]
                        info = {"file": f"{sub + '/' if sub else ''}{want}", "copy": f"{sub + '/' if sub else ''}{n}"}
                        for label, path in (("current", os.path.join(folder, want)), ("other", os.path.join(folder, n))):
                            info[label] = self._describe_version(path)
                        a, b = info["current"], info["other"]
                        if a.get("unreadable") != b.get("unreadable"):
                            info["advice"] = "use_other" if a.get("unreadable") else "keep"
                        elif a.get("rev") is not None and b.get("rev") is not None and a["rev"] != b["rev"]:
                            info["advice"] = "use_other" if b["rev"] > a["rev"] else "keep"
                        elif a.get("stamp") and b.get("stamp") and util.ts(a["stamp"]) != util.ts(b["stamp"]):
                            info["advice"] = "use_other" if util.ts(b["stamp"]) > util.ts(a["stamp"]) else "keep"
                        groups.setdefault(suffix, []).append(info)
        out = []
        for k, files in groups.items():
            g = {"suffix": k, "files": files}
            trio = [f for f in files if f["file"] in self.TOGETHER]
            if trio:
                g["state"] = self._state_choice(trio)
                for f in trio:
                    f["together"] = True
                    f["advice"] = g["state"]["advice"]
            out.append(g)
        return out

    def _state_choice(self, trio: list) -> dict:
        """One piece of advice for the second versions of the screening state, records and de-duplication record that
        share a suffix (the lead's two computers each saved them). The version whose state.json has the higher revision
        is the later one; but never advise a version in which a record that has been voted on is another record, or
        missing, or no longer being screened (the votes would then count for a different record): on a tie, or when
        the later version would do that, the version that agrees with the seal and the votes is advised, and if that
        does not settle it, nothing is (and what each holds is said). What to do next is said for either choice."""
        def version(which: str) -> dict:
            out = {}
            for rel in self.TOGETHER:
                f = next((x for x in trio if x["file"] == rel), None)
                path = self.path(*(f["copy"] if f and which == "other" else rel).split("/"))
                try:
                    out[rel] = util.read_json(path, None)
                except (ValueError, OSError):
                    out[rel] = ValueError
            return out
        cur, oth = version("current"), version("other")
        try:
            seal = util.read_json(self.path("state", "seal.json"), {}) or {}
        except (ValueError, OSError):
            seal = {}
        for _, other in self._seal_copies():
            seal = self._merge_seals(seal, other) if seal else other
        sealed, seal_sets = seal.get("records") or {}, seal.get("sets") or {}
        old_ok = self._old_fingerprints()
        voted = {d.get("r") for rows in self.decisions().values() for d in rows if isinstance(d, dict)}
        voted |= {r for r, _ in self.resolutions()}

        def wrong(v: dict):
            """The screened records this version does not hold as they were screened (None: it cannot be read)."""
            st, recs = v["state/state.json"], v["state/records.json"]
            if not isinstance(st, dict) or not isinstance(recs, list) or v["state/dedup.json"] is ValueError:
                return None
            by = {r.get("id"): r for r in recs if isinstance(r, dict)}
            sets = st.get("sets") or {}
            screened = {rid for s in sets.values() if isinstance(s, dict) and s.get("phase") not in (None, "not_started")
                        for rid in s.get("order") or []}
            bad = {rid for rid, h in sealed.items() if rid not in by or not integrity.same(h, integrity.record_print(by[rid]), old_ok)}
            bad |= {rid for rid, s in seal_sets.items() if rid in by and by[rid].get("set", "") != s}
            return bad | {rid for rid in voted if rid and (rid not in by or rid not in screened)}

        def holds(v: dict, here: str) -> str:
            st, recs = v["state/state.json"], v["state/records.json"]
            if not isinstance(st, dict) or not isinstance(recs, list):
                return f"{here} cannot be read (it is damaged)"
            bits = [f"revision {st.get('rev', 0)}"]
            if st.get("saved_at"):
                on = "this computer" if st.get("saved_on") == self.dev() else f"computer {st.get('saved_on') or '?'}"
                bits.append(f"saved {time.strftime('%Y-%m-%d %H:%M', time.localtime(util.ts(st['saved_at'])))} on {on}")
            n_imp = len(st.get("imports_order") or [])
            bits.append(f"{util.plural(len(recs), 'record')} from {util.plural(n_imp, 'export')}")
            started = [SETS.get(k, k).lower() for k, s in (st.get("sets") or {}).items()
                       if isinstance(s, dict) and s.get("phase") not in (None, "not_started")]
            bits.append("screening started for " + " and ".join(started) if started else "screening not started")
            return f"{here}: " + ", ".join(bits)

        def names(xs) -> str:
            xs = sorted(xs)
            return ", ".join(xs[:5]) + ("…" if len(xs) > 5 else "")

        a, b = wrong(cur), wrong(oth)
        ra, rb = [v["state/state.json"].get("rev") if isinstance(v["state/state.json"], dict) else None for v in (cur, oth)]
        label = {"keep": "the version in use", "use_other": "the second version"}
        advice, why, spoil = None, "", {"keep": set(), "use_other": set()}
        if a is None or b is None:
            if a is None and b is not None:
                advice, why = "use_other", "The version in use cannot be read (it is damaged)."
            elif b is None and a is not None:
                advice, why = "keep", "The second version cannot be read (it is damaged)."
            else:
                why = "Neither version can be read in full (both are damaged): restore a copy, or use OneDrive's version history."
        else:
            fits = {"keep": not (a - b), "use_other": not (b - a)}
            spoil = {"keep": a - b, "use_other": b - a}
            later = None
            if ra is not None and rb is not None and ra != rb:
                later = "use_other" if rb > ra else "keep"
            if later and fits[later]:
                advice = later
                why = (f"{label[later][0].upper() + label[later][1:]} is the later one: its screening state has revision "
                       f"{max(ra, rb)}, the other {min(ra, rb)}.")
            else:
                other = {"keep": "use_other", "use_other": "keep"}
                pick = [k for k in ("keep", "use_other") if fits[k] and (not later or k != later)]
                if later and pick:
                    advice = pick[0]
                    bad = spoil[later]
                    why = (f"{label[later][0].upper() + label[later][1:]} has the later screening state (revision "
                           f"{max(ra, rb)}, the other {min(ra, rb)}), but in it {util.plural(len(bad), 'record')} that "
                           f"{'has' if len(bad) == 1 else 'have'} been screened ({names(bad)}) "
                           f"{'is' if len(bad) == 1 else 'are'} missing or {'is a different record' if len(bad) == 1 else 'are different records'}, "
                           f"so the votes cast on {'it' if len(bad) == 1 else 'them'} would count for another record. "
                           f"Keep the screened records as they were: choose {label[advice]}.")
                elif not later and len(pick) == 1:
                    advice = pick[0]
                    bad = spoil[other[advice]]
                    why = (f"Neither screening state is later (both have revision {ra}), but in {label[other[advice]]} "
                           f"{util.plural(len(bad), 'record')} that {'has' if len(bad) == 1 else 'have'} been screened "
                           f"({names(bad)}) {'is' if len(bad) == 1 else 'are'} missing or "
                           f"{'is a different record' if len(bad) == 1 else 'are different records'}. Choose {label[advice]}.")
                else:
                    why = ("Neither version looks newer" + (f" (both have revision {ra})" if ra == rb and ra is not None else "")
                           + (", and both hold every screened record as it was screened." if fits["keep"] and fits["use_other"]
                              else ", and neither holds every screened record as it was screened: restore a copy kept on "
                                   "this computer, or ask for help (see the user guide, Problems).")
                           + " Compare what each holds and choose the one with the work you know was done last.")
        nxt = {}
        for choice, v in (("keep", cur), ("use_other", oth)):
            st = v["state/state.json"] if isinstance(v["state/state.json"], dict) else {}
            order = set(st.get("imports_order") or [])
            waiting = [m.get("original_name") or m["filename"] for m in self.imports() if m["id"] not in order]
            nxt[choice] = (f"Then choose Remove duplicates on the Import records page: {util.plural(len(waiting), 'export')} "
                           f"({', '.join(waiting[:3])}{'…' if len(waiting) > 3 else ''}) "
                           f"{'is' if len(waiting) == 1 else 'are'} not in the records of this version, probably added on "
                           f"the other computer. Remove duplicates adds {'its' if len(waiting) == 1 else 'their'} records, "
                           "with IDs of their own." if waiting else "")
        return {"files": [f["file"] for f in trio], "copies": [f["copy"] for f in trio], "advice": advice, "why": why,
                "current": holds(cur, "The version in use"), "other": holds(oth, "The second version"),
                "next": nxt, "spoils": {k: sorted(v) for k, v in spoil.items()}}

    @staticmethod
    def _describe_version(path: str) -> dict:
        out: dict = {}
        try:
            d = util.read_json(path, {})
            if isinstance(d, dict):
                out = {k: d.get(k) for k in ("rev", "saved_at", "saved_by", "saved_on", "updated", "updated_by", "processed", "by")
                       if d.get(k) is not None}
                out["stamp"] = str(d.get("saved_at") or d.get("updated") or d.get("processed") or "")
                if isinstance(d.get("sets"), dict):
                    out["phases"] = {k: v.get("phase") for k, v in d["sets"].items() if isinstance(v, dict)}
                if isinstance(d.get("merges"), list):
                    out["merges"] = len(d["merges"])
            elif isinstance(d, list):
                out = {"records": len(d), "stamp": max((str(r.get("added", "")) for r in d if isinstance(r, dict)), default="",
                                                       key=lambda x: util.ts(x) if x else float("-inf"))}
        except (ValueError, OSError):
            out = {"unreadable": True}
        try:
            out["modified"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path)))
        except OSError:
            pass
        return out

    def _conflict(self, copy: str) -> tuple:
        """(the group of second versions, the entry for this one)."""
        for g in self.lead_conflicts():
            for f in g["files"]:
                if f["copy"] == copy:
                    return g, f
        raise ProjectError("That second version is no longer there (the sync service may have settled it).")

    def _settled_together(self, copy: str) -> bool:
        """A second version of the state, records or de-duplication record that is gone because it was settled with
        the others of its group (an earlier choice on the same page)."""
        return (copy.startswith("state/") and any(copy.startswith(rel[:-5]) for rel in self.TOGETHER)
                and not os.path.exists(self.path(*copy.split("/"))))

    @staticmethod
    def _refuse_spoiling_choice(g: dict, choice: str) -> None:
        """Never put in use a screening state and records in which records people have voted on are other records or
        missing, when the other version keeps them as they were."""
        sc = g.get("state") or {}
        bad = (sc.get("spoils") or {}).get(choice) or []
        if bad and sc.get("advice") and sc["advice"] != choice:
            n = len(bad)
            raise ProjectError(f"In {'the second version' if choice == 'use_other' else 'the version in use'}, "
                               f"{util.plural(n, 'record')} that {'has' if n == 1 else 'have'} been screened "
                               f"({', '.join(bad[:5])}{'…' if n > 5 else ''}) {'is' if n == 1 else 'are'} missing or "
                               f"{'is a different record' if n == 1 else 'are different records'}, so the votes cast on "
                               f"{'it' if n == 1 else 'them'} would count for another record. Choose "
                               f"{'the second version' if sc['advice'] == 'use_other' else 'to keep the version in use'} "
                               "instead. Nothing was changed.")

    def _after_state_choice(self, g: dict, choice: str) -> None:
        nxt = (g.get("state") or {}).get("next", {}).get(choice, "")
        if nxt:
            self._notice("statechoice", "The second versions of the screening state, records and de-duplication record "
                         "are settled. " + nxt)

    def use_conflict_copy(self, user: str, copy: str) -> str:
        """Lead only: make this second version the one in use (with the others of its group, for the screening state,
        records and de-duplication record). The version it replaces is kept in state/_replaced."""
        self.require_lead(user)
        if self._settled_together(copy):
            return copy
        g, f = self._conflict(copy)
        todo = [x for x in g["files"] if x.get("together")] if f.get("together") else [f]
        if f.get("together"):
            self._refuse_spoiling_choice(g, "use_other")
        for x in todo:
            if x["other"].get("unreadable"):
                raise ProjectError(f"{x['copy']} cannot be read (it is damaged), so it cannot be used"
                                   + (" (the screening state, records and de-duplication record are used together)"
                                      if len(todo) > 1 else "") + ". Keep the version in use.")
        keep = self.path("state", "_replaced")
        os.makedirs(keep, exist_ok=True)
        with self.lock:
            for x in todo:
                cur, other = self.path(*x["file"].split("/")), self.path(*x["copy"].split("/"))
                if os.path.isfile(cur):
                    util._replace(cur, util.unique_path(keep, os.path.basename(cur) + "." + time.strftime("%Y%m%d-%H%M%S")))
                util._replace(other, cur)
                self._cache.clear()
                self._memo.clear()
                self.backup(*x["file"].split("/"))
        for x in todo:
            self.audit(user, "use the sync service's other version", {"file": x["file"], "copy": x["copy"]})
        if f.get("together"):
            self._after_state_choice(g, "use_other")
        return f["file"]

    def keep_current_version(self, user: str, copy: str) -> str:
        """Lead only: keep the version in use (with the others of its group, for the screening state, records and
        de-duplication record); move the second version to state/_replaced."""
        self.require_lead(user)
        if self._settled_together(copy):
            return copy
        g, f = self._conflict(copy)
        todo = [x for x in g["files"] if x.get("together")] if f.get("together") else [f]
        if f.get("together"):
            self._refuse_spoiling_choice(g, "keep")
        for x in todo:
            if x["current"].get("unreadable"):
                raise ProjectError(f"The version of {x['file']} in use cannot be read (it is damaged): use the second "
                                   "version, or restore a copy.")
        keep = self.path("state", "_replaced")
        os.makedirs(keep, exist_ok=True)
        for x in todo:
            other = self.path(*x["copy"].split("/"))
            util._replace(other, util.unique_path(keep, os.path.basename(other)))
            self.audit(user, "keep the version in use", {"file": x["file"], "copy": x["copy"]})
        self._cache.clear()
        if f.get("together"):
            self._after_state_choice(g, "keep")
        return f["file"]

    def use_conflict_copies(self, user: str, suffix: str) -> int:
        group = next((g for g in self.lead_conflicts() if g["suffix"] == suffix), None)
        if not group:
            raise ProjectError("Those copies are no longer there.")
        return len([self.use_conflict_copy(user, f["copy"]) for f in group["files"]])

    def keep_current_versions(self, user: str, suffix: str) -> int:
        group = next((g for g in self.lead_conflicts() if g["suffix"] == suffix), None)
        if not group:
            raise ProjectError("Those copies are no longer there.")
        return len([self.keep_current_version(user, f["copy"]) for f in group["files"]])

    # ------------------------------------------------------------------------------------------------ imports
    def add_import(self, user: str, filename: str, raw: bytes, meta: dict) -> dict:
        self.require_member(user)
        recs, info = parse_bytes(raw, filename)          # refuse unreadable files before saving anything
        if meta.get("set") not in SETS:
            raise ProjectError("Say which search this file comes from: database searches, update search or other methods.")
        if not (meta.get("database") or "").strip():
            raise ProjectError("Say which database (or other source) the file comes from.")
        sha = util.sha256_text(raw.decode("latin-1"))
        if info["records"] and info.get("no_title", 0) > 0.5 * info["records"]:
            raise ProjectError(f"{info['no_title']:,} of the {info['records']:,} records in this file have no title, so it was "
                               "probably not read correctly. If it is a CSV file, check it has a column called Title (and "
                               "Abstract, Authors, Year, Journal, DOI), separated by commas; otherwise export it from the "
                               "database again as RIS.")
        with self.lock:                       # the check and the write together: a double click cannot add a file twice
            for m in self.imports(include_missing=True):
                if m.get("sha256") == sha:
                    raise ProjectError(f"This file has already been added ({m.get('original_name') or m['filename']}, by "
                                       f"{m['added_by']} on {m['added'][:10]}).")
            return self._write_import(user, filename, raw, meta, recs, info, sha)

    def _write_import(self, user, filename, raw, meta, recs, info, sha) -> dict:
        taken = {m["id"] for m in self.imports(include_missing=True)} | set(self.state_ro().get("imports_order", []))
        iid = _unique_id(f"{util.today().replace('-', '')}-{clean_id(user)}", taken)
        name = util.safe_filename(f"{iid} {filename}", max_len=IMPORT_NAME)   # the ID in the name: two people can add same-named files
        dest = util.unique_path(self.path("imports"), name)
        util.write_bytes_atomic(dest, raw)
        m = {"id": iid, "filename": os.path.basename(dest), "original_name": filename, "set": meta["set"],
             "database": meta["database"].strip(), "platform": (meta.get("platform") or "").strip(),
             "search_date": meta.get("search_date") or "", "search_id": meta.get("search_id") or "",
             "part": meta.get("part") or "", "notes": meta.get("notes") or "",
             "added_by": user, "added": util.now(), "sha256": sha, "parse": info}
        util.write_json_atomic(dest + ".meta.json", m)
        self.audit(user, "add import", {"file": m["filename"], "database": m["database"], "set": m["set"], "records": info["records"]})
        return m

    def imports(self, include_missing: bool = False) -> list:
        """Every import's details. A file whose details have synced but whose data has not (or was moved) is
        left out, unless include_missing."""
        out = []
        for f in util.files_in(self.path("imports"), "*.meta.json"):
            try:
                m = util.read_json(f, {})
            except ValueError:                        # cut short by the sync service: treat as not here yet
                name = os.path.basename(f)[: -len(".meta.json")]
                m = {"id": name.split(" ")[0], "filename": name, "original_name": name, "set": "main", "database": "?",
                     "added": "", "added_by": "?", "parse": {"records": 0}, "_damaged": True}
            if not m:
                continue
            m["_path"] = f[: -len(".meta.json")]
            m["_missing"] = m.get("_damaged", False) or not os.path.isfile(m["_path"])
            if include_missing or not m["_missing"]:
                out.append(m)
        return self._with_links(sorted(out, key=lambda m: util.ts(m.get("added", ""))))

    def remove_import(self, user: str, iid: str) -> bool:
        """Move an import to imports/_removed. A processed file can be removed only before screening of its set
        starts; the records are then rebuilt without it. Returns True if the records need rebuilding."""
        self.require_lead(user)
        with self.lock:
            st = self.state()
            m = next((x for x in self.imports(include_missing=True) if x["id"] == iid), None)
            if not m:
                raise ProjectError("No such import.")
            processed = iid in st.get("imports_order", [])
            if processed:
                sv = st.get("sets", {}).get(m["set"], {})
                if sv.get("phase") not in (None, "not_started"):
                    raise ProjectError("Screening of these records has started, so this file can no longer be removed. "
                                       "Its records are screened like any others; note the problem in the search log.")
                # (its IDs stay in the id_map: until the records are rebuilt without it, a crash must not leave
                # records whose IDs the data checks no longer know, and a file put back gets its old IDs again)
                st["imports_order"] = [x for x in st["imports_order"] if x != iid]
                self.save_state(st, user)
            trash = self.path("imports", "_removed")
            os.makedirs(trash, exist_ok=True)
            for p in (m["_path"], m["_path"] + ".meta.json"):
                if os.path.exists(p):
                    util._replace(p, util.unique_path(trash, os.path.basename(p)))
            self.audit(user, "remove import", {"file": m["filename"], "was_processed": processed})
            return processed

    def parse_import(self, m: dict) -> list:
        key = "parse:" + m["id"]

        def load():
            with open(m["_path"], "rb") as fh:
                raw = fh.read()
            if m.get("sha256") and util.sha256_text(raw.decode("latin-1")) != m["sha256"]:
                raise ProjectError(f"{m['filename']} has changed since it was added (its contents no longer match). Put "
                                   "the original file back, or remove this import and add the new file as a new import.")
            # a file Remove duplicates has already used keeps the reading it had, even if it would be refused today
            used = m["id"] in (self.state_ro().get("imports_order") or [])
            recs, info = parse_bytes(raw, m["filename"], already_read=used)
            if info.get("open_quote_line"):
                self._notice("open-quote:" + m["id"],
                             f"{m.get('original_name') or m['filename']}: a quotation mark (\") on line "
                             f"{info['open_quote_line']:,} of this file is opened and never closed, so the rows after it "
                             "may have been read as part of one cell and left out when the file was first added. It is "
                             "still read that way, because Remove duplicates has already used it. Open the file in a text "
                             "editor, check which records follow that line, and add any that are missing from the records "
                             "as a new file (Import records).", ttl=86400, info=True)
            return recs
        return self._cached(key, m["_path"], load)

    # ------------------------------------------------------------------------------------------------ de-duplication
    def process_imports(self, user: str) -> dict:
        """Rebuild the record store from every import, keeping every ID already given out."""
        self.require_lead(user)
        with self.lock:
            st = self.state()
            every = self.imports(include_missing=True)
            known = {m["id"] for m in every}
            gone = [i for i in st.get("imports_order", []) if i not in known]
            missing = [m for m in every if m["_missing"]]
            same = {}
            for m in every:
                if m.get("sha256"):
                    same.setdefault(m["sha256"], []).append(m)
            twice = [g for g in same.values() if len(g) > 1]
            if twice:
                g = twice[0]
                raise ProjectError("The same file was added more than once (probably on two computers before the first had "
                                   "synced): " + " and ".join(f"{x.get('original_name') or x['filename']} ({x.get('database')}, "
                                   f"added by {x.get('added_by')})" for x in g) + ". Remove all but one of them on the Import "
                                   "page, then try again. Nothing was changed.")
            self._refuse_shared_ids(every, st)
            self._refuse_if_behind(st)
            old = [m for m in missing if not m.get("_damaged") and util.ts(m.get("added", ""))
                   and time.time() - util.ts(m.get("added", "")) > 3600]
            if old:
                raise ProjectError("These exports are missing from the imports folder: " + ", ".join(
                    f"{m.get('original_name') or m['filename']} (added by {m.get('added_by', '?')} on {str(m.get('added', ''))[:10]})"
                    for m in old) + ". If one was deleted or renamed, put it back (OneDrive's recycle bin or version history), "
                    "or remove it on the Import page if its records are not being screened yet. Nothing was changed.")
            if gone or missing:
                names = [(m.get("original_name") or m["filename"]) + (" (its details file is damaged: restore it from OneDrive's "
                         "version history)" if m.get("_damaged") else "") for m in missing] + [f"the import {i}" for i in gone]
                raise ProjectError("These files have been added but are not in the imports folder on this computer yet: "
                                   + ", ".join(names) + ". The sync service is probably still copying them: wait until it has "
                                   "finished (check the folder in OneDrive) and try again. Nothing was changed.")
            imps = {m["id"]: m for m in every}
            order = list(st.get("imports_order", []))
            new = sorted((m for i, m in imps.items() if i not in order and not m["_missing"]),   # unsynced files wait
                         key=lambda m: (SET_RANK[m["set"]], db_rank(m["database"]), util.ts(m["added"]), m["added"]))
            order += [m["id"] for m in new]
            id_map = dict(st.get("id_map", {}))
            next_id = int(st.get("next_id", 1))
            ov = st.get("overrides", {"not_dup": [], "merge": []})
            not_dups = {frozenset(p) for p in ov.get("not_dup", [])}
            old_by_id = {r["id"]: r for r in self.records()}
            started_sets = {k for k, v in st.get("sets", {}).items() if v.get("phase") not in (None, "not_started")}
            # Records already in a screening order: each stays a record of its own, with its ID, whatever this run
            # would now match it to. (A record kept apart from another in an earlier run can match it in a later one,
            # once the other has gained an identifier from a third copy; merging it then would take a record people
            # have screened, and its votes, out of the review.)
            in_screening = {rid for name, sv in st.get("sets", {}).items() if name in started_sets for rid in sv.get("order") or []}
            kept, merges, per_file = [], [], []
            idx = Index(kept)
            for iid in order:
                m = imps[iid]
                try:
                    recs = self.parse_import(m)
                except FormatError as e:
                    raise ProjectError(f"{m['filename']} can no longer be read: {e}")
                n_new = n_dup = n_empty = 0
                for k, r in enumerate(recs):
                    key = f"{iid}#{k}"
                    if not (r["title"] or r["abstract"] or r["doi"] or r["pmid"]):
                        n_empty += 1                      # nothing at all to screen or to look up
                        continue
                    r = dict(r)
                    r["_fp"] = fingerprint(r)
                    r["_tmp"] = id_map.get(key, "")
                    h, how = (None, "") if r["_tmp"] in in_screening else idx.find(r, not_dups)
                    if h is not None:
                        old = kept[h]
                        frozen = old["id"] in old_by_id and old.get("set") in started_sets
                        if frozen:
                            if iid not in old["sources"]:
                                old["sources"].append(iid)
                        else:
                            merge_into(old, r, iid)
                        merges.append({"kept": old["id"], "from_import": iid, "from_index": k, "fingerprint": r["_fp"],
                                       "title": r["title"][:300], "year": r["year"], "authors": r["authors"][:120],
                                       "journal": r["journal"][:120], "doi": r["doi"], "rule": how,
                                       "had_id": id_map.get(key, "")})
                        idx.add(h, old)
                        n_dup += 1
                        continue
                    rid = id_map.get(key)
                    if not rid:
                        rid = f"R{next_id:05d}"
                        next_id += 1
                        id_map[key] = rid
                    if rid in old_by_id and old_by_id[rid].get("set") in started_sets:
                        rec = dict(old_by_id[rid])            # screened records keep exactly the text people saw
                        rec["sources"] = [iid]
                    else:
                        rec = {k2: r.get(k2, "") for k2 in ("title", "abstract", "authors", "year", "journal", "doi", "pmid",
                                                             "pubtype", "volume", "issue", "pages", "url", "keywords",
                                                             "language", "accession")}
                        rec.update({"id": rid, "set": m["set"], "sources": [iid], "first_source": key,
                                    "added": old_by_id.get(rid, {}).get("added") or util.now()})
                    kept.append(rec)
                    idx.add(len(kept) - 1, rec)
                    n_new += 1
                per_file.append({"import": iid, "filename": m["filename"], "database": m["database"], "set": m["set"],
                                 "read": len(recs), "empty": n_empty, "duplicates": n_dup, "new": n_new})
            # manual merges: the second record of each pair is folded into the first. Pairs are followed through
            # earlier merges (A+B, then B+C means C joins A), and a pair already joined is skipped, so nothing is
            # counted twice.
            by = {r["id"]: r for r in kept}
            retired = {}

            def root(x):
                while x in retired:
                    x = retired[x]
                return x
            for keep0, drop0 in ov.get("merge", []):
                keep, drop = root(keep0), root(drop0)
                if keep in by and drop in by and keep != drop and drop not in in_screening:
                    if keep in old_by_id and by[keep].get("set") in started_sets:
                        if by[drop]["sources"][0] not in by[keep]["sources"]:    # screened text stays as it was seen
                            by[keep]["sources"].append(by[drop]["sources"][0])
                    else:
                        merge_into(by[keep], by[drop], by[drop]["sources"][0])
                    for s in by[drop]["sources"][1:]:
                        if s not in by[keep]["sources"]:
                            by[keep]["sources"].append(s)
                    retired[drop] = keep
                    merges.append({"kept": keep, "from_record": drop, "title": by[drop]["title"][:300], "year": by[drop]["year"],
                                   "authors": by[drop]["authors"][:120], "journal": by[drop]["journal"][:120],
                                   "doi": by[drop]["doi"], "rule": "marked as a duplicate by the review team"})
                    for pf in per_file:
                        if pf["import"] == by[drop]["sources"][0]:
                            pf["duplicates"] += 1
                            pf["new"] -= 1
            kept = [r for r in kept if r["id"] not in retired]
            lost = sorted(in_screening - {r["id"] for r in kept})
            if lost:                               # (cannot happen by the rules above; never write the records if it does)
                raise ProjectError(f"{util.plural(len(lost), 'record')} already being screened ({', '.join(lost[:10])}"
                                   f"{'…' if len(lost) > 10 else ''}) would no longer be in the records, so nothing was "
                                   "changed. Check that every export on the Import records page is still in the imports "
                                   "folder; if they all are, please report this (see the user guide, Problems).")
            for mg in merges:                     # the log names the record each one now lives in
                mg["kept"] = root(mg["kept"])
            for r in kept:
                r.pop("_fp", None)
                r.pop("_tmp", None)
            dedup = {"processed": util.now(), "by": user, "rule": RULE_TEXT, "version": VERSION, "files": per_file,
                     "merges": merges, "retired": retired, "possible": possible_duplicates(kept, not_dups=not_dups)}
            # (when no set has started, every record has just been matched again under today's rule: no earlier rule applies)
            dedup["rules"] = rule_history(util.read_json(self.path("state", "dedup.json"), {}) if started_sets else {},
                                          dedup["processed"])
            # Crash-safe writing (everything is worked out above; three files change). First the state takes the new
            # IDs as ReviewDesk's own and notes the files being added: if ReviewDesk stops before the end, the data
            # checks know every ID in the records, and the files being added (and their records) count as not added
            # yet (records(), dedup_report()) until Remove duplicates is chosen again, which gives the same IDs.
            adding = [m["id"] for m in new]
            if adding or set(id_map.values()) - set((st.get("id_map") or {}).values()):
                st.update({"id_map": id_map, "next_id": next_id})
                if adding:
                    st["processing"] = {"started": util.now(), "by": user, "adding": adding}
                self.save_state(st, user)
            util.write_json_atomic(self.path("state", "records.json"), kept)
            self.backup("state", "records.json")
            util.write_json_atomic(self.path("state", "dedup.json"), dedup)
            self.backup("state", "dedup.json")
            st.pop("processing", None)
            st.update({"imports_order": order, "id_map": id_map, "next_id": next_id, "processed_at": util.now()})
            # records added to a set whose screening has started join the end of its order
            for set_name, sv in st.get("sets", {}).items():
                if sv.get("order"):
                    have = set(sv["order"])
                    extra = [r["id"] for r in kept if r["set"] == set_name and r["id"] not in have]
                    if extra:
                        extra.sort()
                        random.Random(int(sv.get("seed") or 0) + len(sv["order"])).shuffle(extra)   # still in random order
                        sv["order"] += extra
                        sv.setdefault("added_later", []).extend(extra)
            self.save_state(st, user)
            if any(v.get("phase") not in (None, "not_started") for v in st.get("sets", {}).values()):
                self.write_seal(user)           # records added to a started set are sealed as they first appear
            summary = {"records": len(kept), "files": len(per_file), "read": sum(f["read"] for f in per_file),
                       "duplicates": sum(f["duplicates"] for f in per_file), "empty": sum(f["empty"] for f in per_file),
                       "new_files": [m["filename"] for m in new]}
            self.audit(user, "process imports", summary)
            self._notices.pop("statechoice", None)          # (the step it asked for is done)
            return summary

    def _refuse_if_behind(self, st: dict) -> None:
        """Record IDs come from state.json (next_id). If records, the seal or anyone's votes already use an ID this
        state.json has not given out, this computer's copy of it is older than the one on the computer that gave those
        IDs out (the lead's other computer, not caught up yet): adding an export now would give the same IDs to other
        records. (A computer that has received nothing at all from the other cannot tell; the second versions the
        sync service then makes are settled as one decision, see _state_choice.)"""
        if "next_id" not in st:
            return
        nxt = int(st.get("next_id") or 1)

        def num(rid) -> int:
            m = re.fullmatch(r"R(\d+)", str(rid or ""))
            return int(m.group(1)) if m else 0
        used = {r.get("id") for r in self.records()}
        try:
            used |= set((util.read_json(self.path("state", "seal.json"), {}) or {}).get("records") or {})
        except (ValueError, OSError, AttributeError):
            pass
        used |= {d.get("r") for rows in self.decisions().values() for d in rows if isinstance(d, dict)}
        ahead = sorted((x for x in used if num(x) >= nxt), key=num)
        if ahead:
            raise ProjectError(f"This computer has not received the latest screening state yet: record ID"
                               f"{'s' if len(ahead) != 1 else ''} {ahead[0]}{' to ' + ahead[-1] if len(ahead) > 1 else ''} "
                               f"{'are' if len(ahead) != 1 else 'is'} already in use, but this computer's copy of "
                               "state/state.json would give them out again to other records. Wait for OneDrive to finish "
                               "syncing the project folder and try again, or carry on on the computer you used last. "
                               "Nothing was changed.")

    def _refuse_shared_ids(self, every: list, st: dict) -> None:
        """Two exports with one ID (added on two computers on one day by an earlier version, when IDs had four random digits):
        Remove duplicates would read only one of them and leave the other's records out without a word."""
        by: dict = {}
        for m in every:
            if not m.get("_damaged"):
                by.setdefault(m["id"], []).append(m)
        twice = [g for g in by.values() if len(g) > 1]
        if not twice:
            return
        g = twice[0]                                  # (imports() lists them in the order added)
        name = lambda m: f"{m.get('original_name') or m['filename']} ({m.get('database') or '?'}, added by "   # noqa: E731
        name2 = lambda m: name(m) + f"{m.get('added_by', '?')} on {str(m.get('added', ''))[:10]})"           # noqa: E731
        first = g[0]
        try:
            used = next((f.get("filename") for f in self.dedup_report().get("files") or [] if f.get("import") == first["id"]), "")
        except DamagedFile:
            used = ""
        read = next((m for m in g if m["filename"] == used), None)
        if read is not None:
            others = [m for m in g if m is not read]
            todo = (f"Remove duplicates has so far read only {read.get('original_name') or read['filename']}. Move "
                    + " and ".join(f"{m['filename']} and {m['filename']}.meta.json" for m in others)
                    + " out of the imports folder (keep them somewhere safe), add "
                    + ("that file" if len(others) == 1 else "those files") + " again on the Import records page, where "
                    + ("it gets an ID of its own" if len(others) == 1 else "each gets an ID of its own") + ", then try again.")
        else:
            todo = ("On the Import records page, remove " + ("one of them" if len(g) == 2 else "all but one of them")
                    + " and add it again: it gets an ID of its own. Then try again.")
        raise ProjectError(f"{'Two' if len(g) == 2 else len(g)} exported files have the same ID ({first['id']}): " + " and ".join(name2(m) for m in g)
                           + ". This can happen when files are added on two computers on the same day, and Remove "
                           "duplicates would then leave the records of one of them out. " + todo + " Nothing was changed.")

    def dedup_report(self) -> dict:
        dd = self._cached("dedup", self.path("state", "dedup.json"),
                          lambda: self._read_shared(("state", "dedup.json"), {}, "state/dedup.json") or {})
        adding = self.adding_interrupted()
        if not adding or not dd:
            return dd
        return {**dd, "files": [f for f in dd.get("files") or [] if f.get("import") not in adding],   # not added yet
                "merges": [m for m in dd.get("merges") or [] if m.get("from_import") not in adding]}

    def _started_sets(self, st: dict) -> set:
        return {k for k, v in st.get("sets", {}).items() if v.get("phase") not in (None, "not_started")}

    def mark_not_duplicate(self, user: str, kept_id: str, fingerprint_or_id: str) -> None:
        self.require_lead(user)
        with self.lock:
            st = self.state()
            pair = sorted([kept_id, fingerprint_or_id])
            undo = [p for p in st["overrides"]["merge"] if sorted(p) == pair]
            if undo:
                by = self.by_id()
                started = self._started_sets(st)
                dropped = self._set_of(undo[0][1], st) or by.get(kept_id, {}).get("set")
                if dropped in started:           # the record merged away would come back into screening that has begun
                    raise ProjectError("Screening of these records has started, so this merge can no longer be undone.")
            if pair not in st["overrides"]["not_dup"]:
                st["overrides"]["not_dup"].append(pair)
            st["overrides"]["merge"] = [p for p in st["overrides"]["merge"] if sorted(p) != pair]
            self.save_state(st, user)
            self.audit(user, "mark not duplicate", {"pair": pair})

    def _set_of(self, rid: str, st: dict) -> str:
        """The set of a record, also of one merged away (from the file it was first found in)."""
        r = self.by_id().get(rid)
        if r:
            return r.get("set", "")
        key = next((k for k, v in (st.get("id_map") or {}).items() if v == rid), "")
        m = next((m for m in self.imports(include_missing=True) if key and m["id"] == key.split("#")[0]), None)
        return (m or {}).get("set", "")

    def mark_duplicate(self, user: str, keep_id: str, drop_id: str) -> None:
        """Merge two records by hand. Records found by different searches merge as the rule merges them: the earlier
        search's record stays and the other counts as a duplicate in its own search's column, so every PRISMA column
        still adds up. Refused once screening of the record that would go has started."""
        self.require_lead(user)
        with self.lock:
            st = self.state()
            by = self.by_id()
            a, b = by.get(keep_id), by.get(drop_id)
            if not a or not b or keep_id == drop_id:
                raise ProjectError("Those records are not both in the project.")
            a, b = kept_first(a, b)
            if b["set"] in self._started_sets(st):
                raise ProjectError("Screening of these records has started, so they can no longer be merged. Screen both "
                                   "as usual; if both reach full text, give them the same study name so they count as one study.")
            if [a["id"], b["id"]] not in st["overrides"]["merge"]:
                st["overrides"]["merge"].append([a["id"], b["id"]])
            self.save_state(st, user)
            self.audit(user, "mark duplicate", {"keep": a["id"], "drop": b["id"]})

    # ------------------------------------------------------------------------------------------------ decisions
    def add_decision(self, user: str, rec_id: str, stage: str, decision: str, reason: str = "", note: str = "",
                     study: str = "", extra: dict | None = None) -> dict:
        self.require_member(user)
        if rec_id not in self.by_id():
            raise ProjectError("That record is not in the project.")
        d = {"t": util.now(), "r": rec_id, "s": stage, "d": decision}
        if reason: d["reason"] = reason
        if note: d["note"] = note
        if study: d["study"] = study
        if extra: d.update(extra)
        name = f"{clean_id(user)}@{self.dev()}.jsonl"
        self._heal_own_files()                  # an older copy put back by the sync service is repaired first
        # chained to the row before it, and kept in a private copy on this computer (see integrity.py)
        return integrity.append(self.path("decisions", name), d, mirror=self._local("mirror", name))

    @staticmethod
    def _row_key(d: dict) -> str:
        return json.dumps(d, sort_keys=True, ensure_ascii=False)

    @staticmethod
    def _content_key(d: dict) -> str:
        """A row's content without its fingerprint and signature (the same row, wherever in a file it now is)."""
        return json.dumps({k: v for k, v in d.items() if k not in (integrity.H, integrity.K)}, sort_keys=True, ensure_ascii=False)

    def _heal_own_files(self) -> None:
        """This computer's own files (decisions, agreed decisions, extraction, risk of bias, PDF records): if the shared
        copy has lost rows (an older version was put back, or the sync service lost a change), or has rows this computer
        did not write, put it right from this computer's own copy."""
        mdir = self._local("mirror")
        if not os.path.isdir(mdir):
            return
        mine_tag = f"@{self.dev()}.jsonl"
        for name in os.listdir(mdir):
            if not name.endswith(mine_tag):              # only files this computer writes (settings copied from another
                continue                                 # computer bring that computer's copies with them)
            sub, _, base = name.rpartition("__")        # "extraction__CH@abc.jsonl" -> extraction/CH@abc.jsonl
            shared_path = self.path(sub or "decisions", base)
            with integrity._lock(shared_path):          # never while this computer is adding a row to it
                self._heal_one(os.path.join(mdir, name), name, base, shared_path)

    def _heal_one(self, mpath: str, name: str, base: str, shared_path: str) -> None:
        """Rebuild one own file: the longest intact start of the shared file (every row fingerprinted in order and
        signed by this computer), then every other genuine row of this computer's (from its copy, or signed by it), in
        the order written. A genuine row that no longer follows on is given a new fingerprint (and the change is
        recorded, so other computers expect it). Rows not written by this computer, or changed, are dropped and kept
        in state/_replaced. Nothing genuine is ever dropped."""
        H = integrity.H
        try:                                   # the usual case, found cheaply: the two files are the same byte for byte,
            if os.path.getsize(mpath) == os.path.getsize(shared_path):      # so there is nothing to put right (the same
                with open(mpath, "rb") as a, open(shared_path, "rb") as b:  # answer as the comparison below gives, without
                    if a.read() == b.read():                                # reading every row: this runs on every vote)
                        return
        except OSError:
            pass
        mine = [x for x in util.read_jsonl(mpath) if isinstance(x, dict)]
        shared = [x for x in util.read_jsonl(shared_path) if isinstance(x, dict)]
        key = self._row_key
        mine_keys = {key(x) for x in mine}
        shared_keys = {key(x) for x in shared}
        missing = [x for x in mine if key(x) not in shared_keys]
        extra = [x for x in shared if key(x) not in mine_keys]
        if not missing and not extra:
            return
        try:
            if versions.behind(self):          # a newer version has worked on this project, perhaps on this computer:
                return                         # what it wrote is not this version's to judge, or to write again
        except (ProjectError, ValueError, OSError):
            pass
        old_ok = self._old_fingerprints()
        bad = set(integrity.verify(shared, old_ok)) | set(integrity.unsigned(shared))
        # One of this computer's own rows, in the shared file under another fingerprint or signature than this
        # computer gave it (cut short, or written again by something else), is not a second row: the one in this
        # computer's copy is the row, and the other goes.
        mine_content = {self._content_key(x) for x in mine}
        altered = {key(x) for x in extra if self._content_key(x) in mine_content}
        bad |= {k for k, x in enumerate(shared) if key(x) in altered}
        rel = os.path.relpath(shared_path, self.dir).replace(os.sep, "/")
        if not missing and not bad:
            util.write_text_atomic(mpath, self._jsonl(shared))      # this computer's copy was behind: complete it
            return
        cut = min(bad) if bad else len(shared)
        rebuilt = shared[:cut]
        have = {key(x) for x in rebuilt}
        genuine = lambda x: key(x) in mine_keys or (integrity.signed_here(x) and key(x) not in altered)   # noqa: E731
        def in_order(rows):                    # (time no earlier than the row before, position): the order written
            last, out = None, []
            for x in rows:
                t = util.ts(x.get("t", ""))
                last = t if last is None or t > last else last
                out.append(last)
            return out
        both = [(t, 0, k, x) for k, (t, x) in enumerate(zip(in_order(mine), mine))] + \
               [(t, 1, k, x) for k, (t, x) in enumerate(zip(in_order(shared), shared)) if k >= cut]
        rest = [x for *_, x in sorted((y for y in both if key(y[3]) not in have), key=lambda y: y[:3])]
        moved, dropped, seen = {}, [], set()
        old_ok = old_ok and not any(integrity.whole(r) for r in rebuilt)   # (one of the old length: only before a whole one)
        for x in rest:
            k = key(x)
            if k in seen:
                continue
            seen.add(k)
            if not genuine(x):
                dropped.append(x)
                continue
            if H not in x and all(H not in r for r in rebuilt):   # a row from version 1.0, still among its kind
                rebuilt.append(x)
                continue
            prev = rebuilt[-1].get(H, "") if rebuilt else ""
            if not integrity.follows(prev, x, old_ok):      # it followed another row: fingerprint it where it now is
                y = {k2: v for k2, v in x.items() if k2 not in (integrity.H, integrity.K)}
                y[H] = integrity.row_hash(prev, y)
                y[integrity.K] = integrity.sign(y)
                if isinstance(x.get(H), str) and x[H]:
                    moved[x[H]] = y[H]
                x = y
            rebuilt.append(x)
            old_ok = old_ok and not integrity.whole(x)
        for x in shared[cut:]:                          # rows of the shared file neither kept nor moved
            if key(x) not in seen and key(x) not in have:
                dropped.append(x)
        if (dropped or moved) and os.path.isfile(shared_path):
            self._keep_replaced(shared_path)
        util.write_text_atomic(shared_path, self._jsonl(rebuilt))
        util.write_text_atomic(mpath, self._jsonl(rebuilt))
        if dropped or moved:                            # so other computers' copies do not bring rows back, or miss moved ones
            integrity.append(shared_path, {"t": util.now(), "repair": {"dropped": [x[H] if isinstance(x.get(H), str) else "" for x in dropped],
                                                                       "moved": moved, "by_computer": self.dev()}}, mirror=mpath)
        bits = []
        if missing:
            bits.append(f"{len(missing):,} of this computer's rows {'were' if len(missing) != 1 else 'was'} missing (an older "
                        f"copy had probably been put back) and {'have' if len(missing) != 1 else 'has'} been written back")
        if dropped:
            bits.append(f"{len(dropped):,} row{'s' if len(dropped) != 1 else ''} not written by this computer, or changed, "
                        f"{'were' if len(dropped) != 1 else 'was'} removed (the file as it was is kept in state/_replaced)")
        self._notice("heal:" + name, f"{rel}: " + "; ".join(bits or ["put in order"]) + ".", ttl=1800, info=True)
        if self.my_id():
            self.audit(self.my_id(), "repair own file from this computer's copy", {"file": rel, "dropped": len(dropped),
                                                                                    "restored": len(missing), "moved": len(moved)})

    @staticmethod
    def _jsonl(rows: list) -> str:
        return "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows)

    def _keep_replaced(self, path: str) -> None:
        """Keep a file that is about to be replaced, named after its folder and the time (never deleted)."""
        keep = self.path("state", "_replaced")
        os.makedirs(keep, exist_ok=True)
        rel = os.path.relpath(path, self.dir).replace(os.sep, "__").replace("/", "__")
        with open(path, "rb") as fh:
            util.write_bytes_atomic(util.unique_path(keep, rel + "." + time.strftime("%Y%m%d-%H%M%S")), fh.read())

    # ------------------------------------------------------------------------------------------------ integrity
    def _check_chain(self, path: str, rows: list) -> tuple:
        """(changed, missing): positions of rows changed or added outside ReviewDesk, and rows this computer saw in
        the file before that are no longer there. While a file is intact, this computer keeps its own copy of it."""
        H = integrity.H
        rel = os.path.relpath(path, self.dir).replace(os.sep, "/")
        bad = sorted(set(integrity.verify(rows, self._old_fingerprints())) | set(self._unproven(rel, rows)))
        snap = self._local("snap", rel.replace("/", "__"))
        seen = self._cached("snap:" + snap, snap, lambda: util.read_jsonl(snap)) if os.path.isfile(snap) else []
        text = lambda xs: {x for x in xs if isinstance(x, str)}       # noqa: E731 (a fingerprint that is not text
        have = text(d.get(H) for d in rows if isinstance(d, dict))     # is a damaged row, which verify reports)
        excused = set()
        for d in rows:
            rep = d.get("repair") if isinstance(d, dict) else None
            if isinstance(rep, dict):
                dropped, moved = rep.get("dropped"), rep.get("moved")
                excused |= text(dropped if isinstance(dropped, list) else []) | text(moved if isinstance(moved, dict) else [])
        keys = {self._content_key(d) for d in rows if isinstance(d, dict)}
        missing = [d for d in seen if isinstance(d, dict) and (d[H] not in have and d[H] not in excused if isinstance(d.get(H), str) and d[H]
                                                               else self._content_key(d) not in keys)]   # rows from 1.0: by
        # content (a 1.0 row written back after an older copy was put back gets a fingerprint, and is the same row)
        if not bad and not missing and len(rows) != len(seen):
            try:
                os.makedirs(os.path.dirname(snap), exist_ok=True)
                with open(path, "rb") as fh:
                    util.write_bytes_atomic(snap, fh.read())
            except OSError:
                pass
        return bad, missing

    def _since_1_1(self):
        """(whether version 1.0 of ReviewDesk, which put no fingerprints in rows, wrote in this project; {computer: when a
        later version first wrote in the audit log on it}). A row with no fingerprint can only be from 1.0 if 1.0 was
        used in the project, and on its computer before a later version was."""
        def load():
            old, later = False, {}
            for e in self.audit_entries():
                c, v = str(e.get("computer") or ""), str(e.get("version") or "")
                if v.startswith("1.0."):
                    old = True
                elif c and v and (c not in later or util.ts(e.get("t", "")) < util.ts(later[c])):
                    later[c] = str(e.get("t", ""))
            return old, later
        return self.memo("since_1_1", load)

    def _old_fingerprints(self) -> bool:
        """Whether this project can hold fingerprints and signatures of the lengths written before 1.3.1 (see
        integrity.py): only one begun with an earlier version can. Two things say which version began it, and both
        must allow it: the project file (created_with, which cannot be changed in ReviewDesk; a project older than
        that field has none) and the audit log, whose "create project" entry carries the version that wrote it. A
        project file that cannot be read refuses nothing on this ground: that is reported in its own right."""
        try:
            made = self.settings_ro().get("created_with")
        except (ProjectError, ValueError, OSError):
            return True
        if isinstance(made, str) and versions.parse(made) != (0,) and not versions.newer(integrity.FULL_SINCE, made):
            return False
        def begun_whole():
            return any(e.get("action") == "create project" and versions.parse(e.get("version")) != (0,)
                       and not versions.newer(integrity.FULL_SINCE, e.get("version")) for e in self.audit_entries())
        try:
            return not self.memo("begun_whole", begun_whole)
        except (ProjectError, ValueError, OSError):
            return True

    def _unproven(self, rel: str, rows: list) -> list:
        """Positions of rows that ReviewDesk would not have written: rows with no fingerprint at the start of a file,
        unless version 1.0 can have written them (see _since_1_1), rows without a signature after a signed row
        (every version since 1.1 signs every row it writes), and rows whose signature is of the length versions
        before 1.3.1 wrote where none can be: beside a whole fingerprint (a version that writes one writes the other),
        or anywhere in a project begun with 1.3.1 or later. This can be checked on any computer; whether a signature
        is genuine can be checked only on the computer that made it."""
        H, K = integrity.H, integrity.K
        m = re.search(r"@([0-9a-f]{6})", os.path.basename(rel))
        old, later = self._since_1_1()
        cut = later.get(m.group(1), "") if m else ""
        whole_only = not self._old_fingerprints()
        out, chained, signed = [], False, False
        for k, d in enumerate(rows):
            if not isinstance(d, dict):
                continue
            if H not in d:
                if not chained and not (old and (not cut or util.ts(d.get("t", "")) <= util.ts(cut))):
                    out.append(k)                     # (one after a fingerprinted row is reported by verify)
                continue
            chained = True
            if K in d:
                signed = True
                if (whole_only or integrity.whole(d)) and len(str(d[K])) != integrity.FULL:
                    out.append(k)
            elif signed:
                out.append(k)
        return out

    def _missing_files(self, present: set) -> list:
        """Data problems: a chained file this computer has seen (it keeps a copy of it) that is no longer in the project
        folder, with rows that are in no other file of the same kind. Not for a person no longer in the project, and
        never for a file this computer has not seen (so not for one still arriving on a new computer)."""
        H = integrity.H
        snapdir = self._local("snap")
        try:
            names = sorted(os.listdir(snapdir))
        except OSError:
            return []
        ids = {r["id"] for r in self.reviewers()}
        probs = []
        for n in names:
            sub, _, base = n.partition("__")
            if not base or sub not in integrity.CHAINED or not base.endswith(".jsonl"):
                continue
            rel = f"{sub}/{base}"
            if rel in present:
                continue
            stem = base.split("@")[0][:-len(".jsonl")] if "@" not in base else base.split("@")[0]
            if sub in ("decisions", "extraction", "rob", "certainty") and stem not in ("agreed", "outcomes") and stem not in ids:
                continue                              # someone no longer in the project: their files are not counted
            f = os.path.join(snapdir, n)
            seen = [d for d in self._cached("snap:" + f, f, lambda f=f: util.read_jsonl(f)) if isinstance(d, dict) and "repair" not in d]
            if not seen:
                continue
            others = set()                            # the same rows in another copy of the file (one the sync service made)
            for o in present:
                ob = o.partition("/")[2]
                if o.startswith(sub + "/") and (ob.startswith(stem + "@") or ob.startswith(stem + ".") or ob.startswith(stem + " ")):
                    others |= {self._content_key(d) for d in self._cached("chain:" + self.path(*o.split("/")), self.path(*o.split("/")),
                                                                          lambda o=o: util.read_jsonl(self.path(*o.split("/")))) if isinstance(d, dict)}
            lost = [d for d in seen if self._content_key(d) not in others]
            if not lost:
                continue
            desc = self._describe_file(rel)
            many = len(lost) != 1
            probs.append({"kind": "chain", "file": rel, "own": base.endswith(f"@{self.dev()}.jsonl"), "fixes": ["chain"],
                          "text": f"{desc[0].upper() + desc[1:]} ({rel}): the file is missing from the project folder, so "
                                  f"{len(lost):,} row{'s' if many else ''} this computer had seen{self._rows_named(lost)} "
                                  f"{'are' if many else 'is'} not counted. If it was deleted or renamed, put it back.",
                          "sig": integrity.label(["chain-missing", rel, [d.get(H) for d in lost]])})
        return probs

    def _describe_file(self, rel: str) -> str:
        """Whose file, in words: "Sam Patel's screening decisions"."""
        sub, _, base = rel.partition("/")
        who = base.split("@")[0]
        name = (self.reviewer(who) or {}).get("name") or who
        if sub == "decisions":
            return f"{name}'s screening decisions"
        if sub == "state":
            return "the agreed decisions"
        if sub in ("extraction", "rob", "certainty"):
            what = {"extraction": "extraction answers", "rob": "risk-of-bias judgements", "certainty": "certainty (GRADE) ratings"}[sub]
            if sub == "certainty" and who == "outcomes":
                return "the outcomes defined for rating certainty"
            return f"the agreed {what}" if who == "agreed" else f"{name}'s {what}"
        if sub == "fulltexts":
            return "the record of PDFs added" if base.startswith("index") else "the results of the free-copy search"
        if sub == "ai":
            return "the AI's answers"
        return rel

    def _rows_named(self, rows: list) -> str:
        ids = []
        for d in rows:
            if isinstance(d, dict):
                x = d.get("r") or d.get("s") or d.get("id")
                if x and x not in ids:
                    ids.append(str(x))
        return f" (record{'s' if len(ids) != 1 else ''} {', '.join(ids[:6])}{'…' if len(ids) > 6 else ''})" if ids else ""

    def restore_chained(self, user: str, rel: str) -> dict:
        """Put back a chained file from this computer's copy, keeping any later rows that follow on correctly. Rows
        that do not are kept aside (in state/_replaced) with the file as it was. Someone's own files are repaired by
        their own computer; the lead may also do it from here. {'rows', 'kept_aside'}"""
        self.require_member(user)
        if not re.fullmatch(r"(%s)/[^/\\]+\.jsonl" % "|".join(integrity.CHAINED), rel or ""):
            raise ProjectError("That is not one of the project's record files.")
        path = self.path(*rel.split("/"))
        own = os.path.basename(rel).endswith(f"@{self.dev()}.jsonl")
        if not own:
            self.require_lead(user)
        snap = self._local("snap", rel.replace("/", "__"))
        mirror = self._local("mirror", os.path.basename(rel) if rel.startswith("decisions/") else rel.replace("/", "__"))
        base = util.read_jsonl(mirror) if own and os.path.isfile(mirror) else util.read_jsonl(snap)
        old_ok = self._old_fingerprints()
        if not base or integrity.verify(base, old_ok):
            raise ProjectError("This computer has no intact copy of that file. Ask the person it belongs to to open ReviewDesk "
                               "on the computer they used (it repairs its own files), or use OneDrive's version history.")
        H = integrity.H
        prev = base[-1].get(H, "")
        later, have, aside = [], {d.get(H) for d in base if isinstance(d, dict) and isinstance(d.get(H), str)}, 0
        old_ok = old_ok and not any(integrity.whole(d) for d in base)
        for d in util.read_jsonl(path):        # rows added after this computer's copy, if they follow on correctly
            if not isinstance(d, dict) or (isinstance(d.get(H), str) and d[H] in have):
                continue
            if integrity.follows(prev, d, old_ok):
                later.append(d)
                prev = d[H]
                old_ok = old_ok and not integrity.whole(d)
            else:
                aside += 1
        with integrity._lock(path):
            if os.path.isfile(path):
                self._keep_replaced(path)
            util.write_text_atomic(path, self._jsonl(base + later))
        self._cache.clear(); self._memo.clear()
        self.audit(user, "put back a file from this computer's copy", {"file": rel, "rows": len(base) + len(later), "kept_aside": aside})
        return {"rows": len(base) + len(later), "kept_aside": aside}

    # ------------------------------------------------------------------------------------------------ the seal
    def write_seal(self, user: str, reason: str = "") -> None:
        """Lead only: fingerprint every record in a started set (with its set), every export and the question and
        criteria (see integrity.py). Records already sealed keep their first fingerprint. With a reason: seal again
        from the files as they are now (after the seal was lost)."""
        self.require_lead(user)
        try:
            cur = util.read_json(self.path("state", "seal.json"), {}) or {}
        except ValueError:
            cur = {}
        first = not cur
        st = self.state_ro()
        started = {k for k, v in st.get("sets", {}).items() if v.get("phase") not in (None, "not_started")}
        by = self.by_id()
        recs, sets = dict(cur.get("records") or {}), dict(cur.get("sets") or {})
        for k in started:
            for rid in st["sets"][k].get("order", []):
                if rid in by and rid not in recs:
                    recs[rid] = integrity.record_print(by[rid])
                if rid in by and rid not in sets:
                    sets[rid] = by[rid].get("set", "")
        imps = dict(cur.get("imports") or {})
        for m in self.imports():
            if m.get("sha256") and m["id"] in st.get("imports_order", []):
                imps.setdefault(m["id"], m["sha256"])
        s = self.settings_ro()
        seal = {"records": recs, "sets": sets, "imports": imps,
                "criteria": cur.get("criteria") or integrity.text_print(s.get("criteria")),
                "question": cur.get("question") or integrity.text_print(s.get("question")), "sealed": cur.get("sealed") or util.now(),
                "updated": util.now(), "by": user, "accepted": list(cur.get("accepted") or []),
                "resealed": [r for r in (cur.get("resealed") or []) if self._reseal_logged(r)]
                + ([{"t": util.now(), "by": user, "reason": reason}] if reason else [])}
        if reason:                      # its audit entry first: the data checks accept a reseal only with one, so a stop
            self.audit(user, "seal the records again", {"records": len(recs), "reason": reason})   # in between is not
        util.write_json_atomic(self.path("state", "seal.json"), seal)                             # taken for tampering
        self.backup("state", "seal.json")
        self._keep_seal_copy(seal)
        if first and not reason:
            self.audit(user, "seal the records", {"records": len(recs), "reason": reason})

    def _keep_seal_copy(self, seal: dict) -> None:
        try:
            os.makedirs(self._local(), exist_ok=True)
            util.write_json_atomic(self._local("seal.json"), seal)
        except OSError:
            pass

    @staticmethod
    def _merge_seals(a: dict, b: dict) -> dict:
        """Two versions of the seal (a restored copy and what this computer knows): first fingerprints win; additions
        from both are kept."""
        if not b:
            return a
        out = dict(a)
        for part in ("records", "sets", "imports"):
            out[part] = {**(b.get(part) or {}), **(a.get(part) or {})}
        for part, k in (("accepted", "sig"), ("resealed", "t")):
            seen, merged = set(), []
            for x in (a.get(part) or []) + (b.get(part) or []):
                if isinstance(x, dict) and x.get(k) not in seen:
                    seen.add(x.get(k))
                    merged.append(x)
            out[part] = merged
        out["updated"] = max(str(a.get("updated", "")), str(b.get("updated", "")), key=util.ts)     # as instants: the
        if a.get("sealed") and b.get("sealed"):                                                    # two may differ in offset
            out["sealed"] = min(str(a["sealed"]), str(b["sealed"]), key=util.ts)
        return out

    def _seal_copies(self) -> list:
        """[(name, seal)]: readable second versions of state/seal.json made by the sync service."""
        d = self.path("state")
        out = []
        try:
            names = sorted(n for n in os.listdir(d) if n != "seal.json" and n.startswith("seal") and n.endswith(".json")
                           and re.match(r"^[- (_]", n[4:]))
        except OSError:
            return out
        for n in names:
            try:
                other = util.read_json(os.path.join(d, n), {}) or {}
            except ValueError:
                continue                                  # a damaged version: left for the lead to choose
            if isinstance(other, dict) and isinstance(other.get("records"), dict):
                out.append((n, other))
        return out

    def _combine_seal_versions(self) -> None:
        """On the lead's computer: a second version of state/seal.json (the lead's two computers both added to it before
        the sync service caught up) is combined into the one in use. A seal only grows, and the first fingerprint of a
        record always wins, so combining can only add what the other computer sealed."""
        me = self.my_id()
        if not me or not self.is_lead(me):
            return
        d = self.path("state")
        for n, other in self._seal_copies():
            try:
                cur = util.read_json(self.path("state", "seal.json"), {}) or {}
            except ValueError:
                return                                    # the seal in use is damaged: the lead chooses
            merged = self._merge_seals(cur, other) if cur else other
            util.write_json_atomic(self.path("state", "seal.json"), merged)
            self.backup("state", "seal.json")
            keep = self.path("state", "_replaced")
            os.makedirs(keep, exist_ok=True)
            util._replace(os.path.join(d, n), util.unique_path(keep, n + "." + time.strftime("%Y%m%d-%H%M%S")))
            self._cache.clear()
            self._notice("sealmerge:" + n, f"state/{n} (a second version of the seal, from another computer) was combined "
                         "with state/seal.json.", ttl=1800, info=True)
            self.audit(me, "combine two versions of the seal", {"copy": n})

    def ever_sealed(self) -> bool:
        """Whether this project has had a seal (so a missing one is a problem, not a project from before seals)."""
        if os.path.isfile(self._local("seal.json")) or util.files_in(self._local("backups"), "state__seal.json.*"):
            return True
        for f in util.files_in(self.path("audit"), "*.jsonl"):
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    if '"seal the records' in fh.read():
                        return True
            except OSError:
                pass
        return False

    def reseal(self, user: str, reason: str) -> None:
        """Lead only: the seal is lost and no copy is left: seal again from the files as they are now, with the reason."""
        self.require_lead(user)
        reason = " ".join(str(reason or "").split())
        if len(reason) < 5:
            raise ProjectError("Say why the records can be sealed again as they are now (it is kept in the audit log).")
        self.write_seal(user, reason=reason[:500])
        self._memo.clear()

    def _reseal_logged(self, r: dict) -> bool:
        """A 'sealed again' entry in the seal was made in ReviewDesk: the audit log has it, by the person named, who is
        the lead or was the lead then (the role may have been handed over since: the audit log shows them doing what
        only the lead can do)."""
        if not isinstance(r, dict) or not r.get("by"):
            return False
        entries = self.audit_entries()
        if not self.is_lead(r["by"]) and not any(e.get("who") == r["by"] and e.get("action") in ("create project", "change settings")
                                                 for e in entries):
            return False
        return any(e.get("action") == "seal the records again" and e.get("who") == r.get("by")
                   and (e.get("details") or {}).get("reason") == r.get("reason") for e in entries)

    def _seal_change_ok(self, old: dict, new: dict) -> bool:
        """A seal may only grow: records, sets and exports added, changes recorded as correct, or a reseal (one the
        audit log has)."""
        real = [r for r in (new.get("resealed") or []) if self._reseal_logged(r)]
        if real and util.ts(real[-1].get("t", "")) > util.ts(old.get("updated", "")):
            return True
        if not (new.get("sealed") == old.get("sealed") or (new.get("sealed") and old.get("sealed")
                                                            and util.ts(new["sealed"]) < util.ts(old["sealed"]))) \
                or old.get("criteria") != new.get("criteria") or old.get("question") != new.get("question"):
            return False
        for part in ("records", "sets", "imports"):
            a, b = old.get(part) or {}, new.get(part) or {}
            if any(b.get(k) != v for k, v in a.items()):
                return False
        a, b = old.get("accepted") or [], new.get("accepted") or []
        return all(x in b for x in a)                   # (in any order: two computers' versions may have been combined)

    def integrity_report(self) -> dict:
        """Everything that differs from what ReviewDesk recorded: {"problems": [...], "accepted": [...], "checked",
        "sealed", "records_sealed"}. Each problem: kind, file, text, fixes (what can be done), sig (what it is, and
        the state of the file now), own (one of this computer's own files)."""
        def work():
            probs = []
            self._recheck_at = 0.0
            self._heal_own_files()               # this computer's own files are put right first
            self._combine_seal_versions()
            H = integrity.H
            for f in integrity.chained_files(self):
                rel = os.path.relpath(f, self.dir).replace(os.sep, "/")
                rows = self._cached("chain:" + f, f, lambda f=f: util.read_jsonl(f))
                bad, missing = self._check_chain(f, rows)
                if not bad and not missing:
                    continue
                own = os.path.basename(f).endswith(f"@{self.dev()}.jsonl")
                can = own or os.path.isfile(self._local("snap", rel.replace("/", "__")))
                bits = []
                if bad:
                    bits.append(f"{len(bad):,} row{'s' if len(bad) != 1 else ''} changed or added outside ReviewDesk"
                                + self._rows_named([rows[i] for i in bad]))
                if missing:
                    bits.append(f"{len(missing):,} row{'s' if len(missing) != 1 else ''} this computer had seen "
                                f"{'are' if len(missing) != 1 else 'is'} no longer there{self._rows_named(missing)}, so an "
                                "older copy was probably put back")
                desc = self._describe_file(rel)
                probs.append({"kind": "chain", "file": rel, "own": own, "fixes": ["chain"] if can else [],
                              "text": f"{desc[0].upper() + desc[1:]} ({rel}): " + "; ".join(bits) + ".",
                              "sig": integrity.label(["chain", rel, [rows[i].get(H) for i in bad if isinstance(rows[i], dict)],
                                                           [d.get(H) for d in missing]])})
            probs += self._missing_files({os.path.relpath(f, self.dir).replace(os.sep, "/") for f in integrity.chained_files(self)})
            st = self.state_ro()
            started = any(v.get("phase") not in (None, "not_started") for v in st.get("sets", {}).values())
            seal, seal_ok = {}, True
            try:
                seal = util.read_json(self.path("state", "seal.json"), {}) or {}
            except ValueError:
                seal_ok = False
                probs.append({"kind": "seal", "file": "state/seal.json", "fixes": self._seal_fixes(), "sig": "seal-damaged",
                              "text": "The record of what screening started from (state/seal.json) cannot be read: it is damaged."})
            mine = util.read_json(self._local("seal.json"), {}) if os.path.isfile(self._local("seal.json")) else {}
            if seal:                              # a second version not yet combined on the lead's computer counts too
                for _, other in self._seal_copies():
                    seal = self._merge_seals(seal, other)
            shared_seal = seal
            if seal_ok and not seal and started and self.ever_sealed():
                probs.append({"kind": "seal", "file": "state/seal.json", "fixes": self._seal_fixes(), "sig": "seal-missing",
                              "text": "The record of what screening started from (state/seal.json) is missing."})
            elif seal and mine and not self._seal_change_ok(mine, seal):
                probs.append({"kind": "seal", "file": "state/seal.json", "fixes": self._seal_fixes(),
                              "sig": integrity.label(["seal", seal]),
                              "text": "The record of what screening started from (state/seal.json) was changed outside "
                                      "ReviewDesk: it no longer matches this computer's copy of it."})
                seal = mine                       # check the records against what this computer knows to be right
            elif seal:                            # this computer's copy keeps only acceptances that were really made
                self._keep_seal_copy({**seal, "accepted": [a for a in (seal.get("accepted") or [])
                                                           if isinstance(a, dict) and self._logged(a)],
                                      "resealed": [r for r in (seal.get("resealed") or []) if self._reseal_logged(r)]})
                me = self.my_id()                 # records of a started set not sealed yet (ReviewDesk stopped between
                if me and self.is_lead(me):       # starting screening and sealing): sealed now, on the lead's computer
                    by0 = self.by_id()
                    if any(rid in by0 and rid not in (seal.get("records") or {})
                           for v in st.get("sets", {}).values() if v.get("phase") not in (None, "not_started")
                           for rid in v.get("order", [])):
                        self.write_seal(me)
                        seal = util.read_json(self.path("state", "seal.json"), {}) or {}
                        shared_seal = seal
            for rel in ("searches/worksheet.json", "extraction/form.json", MARKER):
                try:
                    self._saw_version(rel, util.read_json(self.path(*rel.split("/")), None))
                except (ValueError, OSError):    # damaged, or open in another program: looked at next time
                    pass
            if shared_seal:
                self._saw_version("state/seal.json", shared_seal)
            probs += self._older_than_seen()
            by = self.by_id()
            sealed = seal.get("records") or {}
            old_ok = self._old_fingerprints()
            changed = [rid for rid, h in sealed.items() if rid in by and not integrity.same(h, integrity.record_print(by[rid]), old_ok)]
            gone = [rid for rid in sealed if rid not in by]
            moved = [rid for rid, sname in (seal.get("sets") or {}).items() if rid in by and by[rid].get("set", "") != sname]
            known_ids = set((st.get("id_map") or {}).values())
            unknown = [r["id"] for r in self.records() if r.get("id") not in known_ids] if known_ids else []
            names = lambda xs: f"{', '.join(xs[:5])}{'…' if len(xs) > 5 else ''}"   # noqa: E731
            if changed:
                text, fixes = self._changed_records_words(changed, by, st, names)
                probs.append({"kind": "records", "file": "state/records.json", "fixes": fixes,
                              "sig": integrity.label(["records", sorted((r, integrity.shortened(integrity.record_print(by[r])))
                                                                        for r in changed)]),
                              "text": text})
            if gone:
                probs.append({"kind": "records", "file": "state/records.json", "fixes": ["backup"], "sig": integrity.label(["gone", sorted(gone)]),
                              "text": f"{len(gone):,} screened record{'s are' if len(gone) != 1 else ' is'} missing from the "
                                      f"records ({names(gone)})."})
            if moved:
                probs.append({"kind": "records", "file": "state/records.json", "fixes": ["backup"], "sig": integrity.label(["moved", sorted(moved)]),
                              "text": f"{len(moved):,} screened record{'s were' if len(moved) != 1 else ' was'} moved to another "
                                      f"set of records outside ReviewDesk ({names(moved)})."})
            if unknown:
                probs.append({"kind": "records", "file": "state/records.json", "fixes": ["backup"], "sig": integrity.label(["unknown", sorted(unknown)]),
                              "text": f"{len(unknown):,} record{'s were' if len(unknown) != 1 else ' was'} added to the records "
                                      f"outside ReviewDesk ({names(unknown)}): ReviewDesk never gave out "
                                      f"{'these IDs' if len(unknown) != 1 else 'this ID'}."})
            s = self.settings_ro()
            for what, words in (("criteria", "eligibility criteria"), ("question", "review question")):
                if seal.get(what):
                    after = [a for a in s.get("amendments", []) if a.get("what") == what]
                    expect = integrity.text_print(after[-1].get("after")) if after else seal[what]
                    if not integrity.same(expect, integrity.text_print(s.get(what)), old_ok and not after):
                        probs.append({"kind": "criteria", "what": what, "file": MARKER, "fixes": ["backup", "accept"],
                                      "sig": integrity.label(["criteria", what, s.get(what)]),
                                      "text": f"The {words} {'were' if what == 'criteria' else 'was'} changed outside "
                                              f"ReviewDesk: {'they do' if what == 'criteria' else 'it does'} not match "
                                              f"{'the last recorded amendment' if after else 'what screening started with'}."})
            hashes = integrity.FileHashes(self._local("filehashes.json"))
            sealed_imps = seal.get("imports") or {}
            for m in self.imports(include_missing=True):
                if m.get("_damaged"):
                    continue
                label = m.get("original_name") or m["filename"]
                if m["id"] in sealed_imps and m.get("sha256") != sealed_imps[m["id"]]:
                    probs.append({"kind": "import", "file": "imports/" + m["filename"], "fixes": ["accept"],
                                  "sig": integrity.label(["import-details", m["id"], m.get("sha256")]),
                                  "text": f"The details of the export {label} were changed outside ReviewDesk."})
                if m.get("_missing"):
                    if util.ts(m.get("added", "")) and time.time() - util.ts(m.get("added", "")) > 3600:
                        probs.append({"kind": "import", "file": "imports/" + m["filename"], "fixes": [],
                                      "sig": integrity.label(["import-missing", m["id"]]),
                                      "text": f"The export {label} (added by {m.get('added_by', '?')} on {str(m.get('added', ''))[:10]}) "
                                              "is missing from the imports folder. If it was deleted or renamed, put it back "
                                              "(OneDrive's recycle bin or version history)."})
                elif m.get("sha256") and hashes.of(m["_path"], legacy_text=True) != m["sha256"]:
                    probs.append({"kind": "import", "file": "imports/" + m["filename"], "fixes": ["accept"],
                                  "sig": integrity.label(["import", m["id"], hashes.of(m["_path"])]),
                                  "text": f"The export {label} was changed after it was added. The records already taken from "
                                          "it are unchanged; put the original back from OneDrive's version history."})
            pdfs = self._pdf_problems(hashes)
            if any(pr["kind"] == "chain" and pr["file"].startswith("fulltexts/index") for pr in probs):
                for pr in pdfs:
                    if pr["kind"] in ("pdf_back", "pdf"):
                        pr["text"] += (" The record of PDFs has also lost rows (above): put that back first, then check "
                                       "again; this may then go away.")
                        pr["fixes"] = [f for f in pr["fixes"] if f not in ("takeoff", "keep")]
            probs += pdfs
            hashes.save()
            fake = [r for r in (shared_seal.get("resealed") or []) if not self._reseal_logged(r)]
            if fake:
                probs.append({"kind": "seal", "file": "state/seal.json", "fixes": self._seal_fixes(),
                              "sig": integrity.label(["unlogged-reseal", fake]),
                              "text": "state/seal.json says the records were sealed again, but the audit log has no entry for "
                                      "it, so it was not done in ReviewDesk: the seal may have been changed outside ReviewDesk."})
            accepted = [a for a in (seal.get("accepted") or []) if isinstance(a, dict)]
            if accepted:                          # each must have its entry in the audit log, by the person named
                unlogged = [a for a in accepted if not self._logged(a)]
                if unlogged:
                    probs.append({"kind": "seal", "file": "state/seal.json", "fixes": self._seal_fixes(),
                                  "sig": integrity.label(["unlogged", [a.get("sig") for a in unlogged]]),
                                  "text": f"{len(unlogged):,} change{'s' if len(unlogged) != 1 else ''} in state/seal.json "
                                          "recorded as correct have no entry in the audit log, so they were not recorded in "
                                          "ReviewDesk: they are ignored."})
                accepted = [a for a in accepted if a not in unlogged]
            ok = {a.get("sig") for a in accepted}
            return {"problems": [pr for pr in probs if pr["sig"] not in ok], "accepted": accepted[-50:], "checked": util.now(),
                    "sealed": seal.get("sealed", ""), "records_sealed": len(seal.get("records") or {}),
                    "recheck": self._recheck_at}
        rep = p_memo(self, work)
        if rep.get("recheck") and time.time() > rep["recheck"]:      # something left alone while it arrived: look again
            self._memo.clear()
            rep = p_memo(self, work)
        from . import robplan                     # the risk-of-bias plan against its audit entries (read fresh each time:
        try:                                      # the audit files are not part of what the memo watches)
            pr = robplan.outside_change(self)
        except DamagedFile:
            pr = None
        if pr and pr["sig"] not in {a.get("sig") for a in rep.get("accepted") or []}:
            rep = {**rep, "problems": list(rep["problems"]) + [pr]}
        return rep

    def _changed_records_words(self, changed: list, by: dict, st: dict, names) -> tuple:
        """(text, fixes) for screened records whose text has changed. When they are exactly the records of one export
        that the screening state has not added (the lead added it on a second computer that had not caught up, and
        it gave out IDs already in use, and that computer's records were then put in use), say so: Remove duplicates
        puts the screened records back and gives that export's records IDs of their own; 'This change is correct'
        would attach the votes to the wrong records."""
        n = len(changed)
        plain = (f"The text of {n:,} record{'s' if n != 1 else ''} differs from what was screened ({names(changed)}).",
                 ["backup", "accept"])
        src = {str(by[r].get("first_source") or "").split("#")[0] for r in changed}
        if len(src) != 1:
            return plain
        iid = src.pop()
        if not iid or iid in (st.get("imports_order") or []):
            return plain
        if {r["id"] for r in by.values() if str(r.get("first_source") or "").split("#")[0] == iid} != set(changed):
            return plain
        m = next((x for x in self.imports() if x["id"] == iid), None)
        label = (m.get("original_name") or m["filename"]) if m else iid
        return (f"{n:,} screened record{'s' if n != 1 else ''} ({names(changed)}) now hold{'s' if n == 1 else ''} the "
                f"record{'s' if n != 1 else ''} of {label} instead, an export that Remove duplicates has not added here: "
                "it was probably added on the project lead's other computer before OneDrive had caught up, which gave "
                f"{'its records IDs' if n != 1 else 'its record an ID'} already in use. Choose Remove duplicates on the "
                "Import records page: it puts the screened records back as they were screened and gives that export's "
                f"records IDs of their own. Do not record this as a correct change: the votes would then count for the "
                f"wrong record{'s' if n != 1 else ''}.", ["backup"])

    def _logged(self, a: dict) -> bool:
        """An acceptance recorded in the seal was made in ReviewDesk: the audit log has it, by the person named."""
        logged = {((e.get("details") or {}).get("sig"), e.get("who")) for e in self.audit_entries()
                  if e.get("action") == "accept a difference found by the data checks"}
        return (a.get("sig"), a.get("by")) in logged

    def _seal_fixes(self) -> list:
        out = []
        if os.path.isfile(self._local("seal.json")):
            out.append("seal")
        if util.files_in(self._local("backups"), "state__seal.json.*"):
            out.append("backup")
        return out + ["reseal"]

    def restore_seal_copy(self, user: str) -> None:
        """Lead only: put back this computer's copy of state/seal.json."""
        self.require_lead(user)
        mine = self._local("seal.json")
        if not os.path.isfile(mine):
            raise ProjectError("This computer has no copy of the seal.")
        f = self.path("state", "seal.json")
        if os.path.isfile(f):
            self._keep_replaced(f)
        with open(mine, "rb") as fh:
            util.write_bytes_atomic(f, fh.read())
        self.backup("state", "seal.json")
        self._memo.clear()
        self.audit(user, "put back the seal from this computer's copy", {})

    def accept_problem(self, user: str, sig: str, reason: str) -> dict:
        """Lead only: record that a reported difference is known and correct (with the reason), so it is no longer
        reported. It is reported again if the file changes again. A change to the criteria or question made outside
        ReviewDesk is recorded as an amendment instead, so it is reported in the methods."""
        self.require_lead(user)
        reason = " ".join(str(reason or "").split())
        if len(reason) < 5:
            raise ProjectError("Say why this difference is correct (it is kept in the audit log).")
        pr = next((x for x in self.integrity_report()["problems"] if x["sig"] == sig), None)
        if not pr:
            raise ProjectError("That problem is no longer reported (it may have been fixed meanwhile). Check again.")
        if "accept" not in pr.get("fixes", []):
            raise ProjectError("This change cannot be recorded as correct: put the file back instead.")
        if pr["kind"] == "robplan":
            from . import robplan
            if pr.get("log_behind"):               # the plan's entry in the audit log is missing: log it again (no amendment)
                robplan.log_again(self, user, reason)
            else:
                robplan.accept_outside_change(self, user, reason)
        elif pr["kind"] == "criteria":
            s = self._read_shared((MARKER,), {}, MARKER, dict) or {}
            s.setdefault("amendments", []).append({"date": util.now(), "by": user, "what": pr["what"], "before": None,
                                                   "after": self.settings_ro().get(pr["what"]), "reason": reason[:500],
                                                   "outside_reviewdesk": True})
            util.write_json_atomic(self.path(MARKER), s)
            self._saw_version(MARKER, s)
            self.backup(MARKER)
        else:
            try:
                seal = util.read_json(self.path("state", "seal.json"), {}) or {}
            except ValueError:
                raise ProjectError("state/seal.json is damaged: restore it first.") from None
            seal.setdefault("accepted", []).append({"sig": sig, "kind": pr["kind"], "file": pr["file"], "text": pr["text"],
                                                     "by": user, "t": util.now(), "reason": reason[:500]})
            util.write_json_atomic(self.path("state", "seal.json"), seal)
            self.backup("state", "seal.json")
            self._keep_seal_copy(seal)
        self._cache.clear(); self._memo.clear()
        self.audit(user, "accept a difference found by the data checks", {"file": pr["file"], "problem": pr["text"],
                                                                           "reason": reason[:500], "sig": sig})
        return pr

    # ------------------------------------------------------------------------------------------------ PDFs
    def pdf_index(self) -> dict:
        """{record: latest entry} from every computer's PDF index."""
        out = {}
        for d in self._read_all(self.path("fulltexts", "index@*.jsonl"), "pdfidx"):
            out[d.get("r")] = d
        return out

    def _pdf_snap(self, rid: str, sha: str) -> str:
        return self._local("snap", "pdf", f"{rid}.{(sha or '')[:16]}.pdf")

    def _index_pdf(self, user: str, rid: str, raw: bytes, how: str) -> None:
        name = f"index@{self.dev()}.jsonl"
        integrity.append(self.path("fulltexts", name), {"t": util.now(), "r": rid, "sha": util.sha256_bytes(raw),
                         "bytes": len(raw), "by": user, "how": how}, mirror=self._local("mirror", "fulltexts__" + name))

    def _pdf_problems(self, hashes) -> list:
        probs = []
        idx = self.pdf_index()
        by = self.by_id()
        d = self.path("fulltexts")
        files = sorted(n for n in os.listdir(d) if os.path.isfile(os.path.join(d, n))) if os.path.isdir(d) else []
        pdfs = {n[:-4]: n for n in files if n.endswith(".pdf") and n[:-4] in by}
        # (a record's PDF is <its ID>.pdf exactly: R00001.PDF, put there by hand, is a file ReviewDesk does not use)
        stray = [n for n in files if n.lower().endswith(".pdf") and (n[:-4] not in by or not n.endswith(".pdf"))
                 and not n.startswith((".", "~"))]
        loose = [r for r in pdfs if r not in idx]
        if loose:
            done = {}                          # the latest "attach full text" entry for each record
            for e in self.audit_entries():
                if e.get("action") == "attach full text":
                    done[(e.get("details") or {}).get("record")] = e
            # A PDF added by version 1.0 (which did not record PDFs) is recorded now. One added by a later version whose
            # record has not arrived yet (the sync service copies the PDF, the audit entry and the record of PDFs
            # separately) is left alone for an hour, as an import still arriving is; after that it is reported.
            old = [r for r in loose if r in done and str(done[r].get("version") or "").startswith("1.0.")]
            recent = lambda e: not util.ts(e.get("t", "")) or time.time() - util.ts(e.get("t", "")) <= 3600   # noqa: E731
            arriving = {r for r in loose if r in done and r not in old and recent(done[r])}
            if arriving:                       # the data checks look again when the hour is up
                self._recheck_at = min([self._recheck_at or float("inf")] + [util.ts(done[r].get("t", "")) + 3600 for r in arriving])
            for rid in old:
                try:
                    with open(os.path.join(d, pdfs[rid]), "rb") as fh:
                        self._index_pdf(self.my_id() or "?", rid, fh.read(), "added with an earlier version of ReviewDesk")
                except OSError:
                    continue
            if old:
                idx = self.pdf_index()
            loose = [r for r in pdfs if r not in idx and r not in arriving]
        if loose:
            probs.append({"kind": "pdf_unrecorded", "file": "fulltexts", "records": loose, "fixes": ["adopt"],
                          "sig": integrity.label(["loose", [(r, hashes.of(os.path.join(d, pdfs[r]))) for r in loose]]),
                          "text": f"{len(loose):,} PDF{'s were' if len(loose) != 1 else ' was'} put in the fulltexts folder "
                                  f"outside ReviewDesk ({', '.join(loose[:5])}{'…' if len(loose) > 5 else ''}). Open "
                                  f"{'each' if len(loose) != 1 else 'it'} from its report's page to check it is the right "
                                  "paper, then choose Record them here."})
        if stray:
            probs.append({"kind": "pdf_stray", "file": "fulltexts", "names": stray, "fixes": ["stray"],
                          "sig": integrity.label(["stray", [(n, hashes.of(os.path.join(d, n))) for n in stray]]),
                          "text": f"{len(stray):,} file{'s' if len(stray) != 1 else ''} in the fulltexts folder "
                                  f"{'are' if len(stray) != 1 else 'is'} not used by ReviewDesk ({', '.join(stray[:4])}"
                                  f"{'…' if len(stray) > 4 else ''}). Move {'them' if len(stray) != 1 else 'it'} to the "
                                  "PDFs waiting to be placed, then place each on its report."})
        seen_sha = {}
        for rid, e in idx.items():
            if rid not in by:
                continue
            f = os.path.join(d, f"{rid}.pdf")
            snap_ok = os.path.isfile(self._pdf_snap(rid, e.get("sha", "")))
            if e.get("removed"):
                if os.path.isfile(f):
                    probs.append({"kind": "pdf_back", "file": f"fulltexts/{rid}.pdf", "record": rid, "fixes": ["takeoff", "keep"],
                                  "sig": integrity.label(["back", rid, hashes.of(f)]),
                                  "text": f"A PDF for {rid} is in the fulltexts folder, although its PDF was taken off "
                                          f"({str(e.get('t', ''))[:10]}, by {e.get('by', '?')})."})
                continue
            if not os.path.isfile(f):
                probs.append({"kind": "pdf", "file": f"fulltexts/{rid}.pdf", "record": rid, "fixes": ["pdf"] if snap_ok else [],
                              "sig": integrity.label(["pdf-missing", rid, e.get("sha")]),
                              "text": f"The PDF for {rid} is missing from the project folder."})
                continue
            now = hashes.of(f)
            if now != e.get("sha"):
                probs.append({"kind": "pdf", "file": f"fulltexts/{rid}.pdf", "record": rid,
                              "fixes": (["pdf"] if snap_ok else []) + ["keep"], "sig": integrity.label(["pdf", rid, now]),
                              "text": f"The PDF for {rid} was replaced outside ReviewDesk."})
                continue
            seen_sha.setdefault(now, []).append(rid)
            snap = self._pdf_snap(rid, now)
            if not snap_ok:                       # an intact PDF: keep this computer's own copy of this version of it
                try:
                    os.makedirs(os.path.dirname(snap), exist_ok=True)
                    with open(f, "rb") as fh:
                        util.write_bytes_atomic(snap, fh.read())
                except OSError:
                    pass
        for sha, rids in seen_sha.items():
            if len([r for r in rids if idx[r].get("same_as") not in rids]) > 1:     # placed twice without saying so
                probs.append({"kind": "pdf_duplicate", "file": "fulltexts", "fixes": ["accept"], "sig": integrity.label(["dup", sorted(rids), sha]),
                              "text": f"{', '.join(sorted(rids)[:-1])} and {sorted(rids)[-1]} have the same PDF. If one PDF really "
                                      "holds both reports, the lead can record that as correct; otherwise open the reports' "
                                      "pages and take the wrong one off."})
        return probs

    def adopt_pdfs(self, user: str, rid: str = "") -> int:
        """Record the PDFs put in the fulltexts folder by hand (or one PDF found there for a report), so they are
        checked from now on."""
        self.require_member(user)
        rep = self.integrity_report()["problems"]
        rids = [rid] if rid else next((x.get("records", []) for x in rep if x["kind"] == "pdf_unrecorded"), [])
        n = 0
        for r in rids:
            f = self.path("fulltexts", f"{r}.pdf")
            if r not in self.by_id() or not os.path.isfile(f):
                continue
            with open(f, "rb") as fh:
                raw = fh.read()
            self._index_pdf(user, r, raw, "found in the folder; kept by " + user)
            n += 1
        self._cache.clear(); self._memo.clear()
        if n:
            self.audit(user, "record PDFs found in the folder", {"pdfs": n, "records": rids[:50]})
        return n

    def take_off_found_pdf(self, user: str, rid: str) -> None:
        """A PDF that came back into the folder after it was taken off: take it off again (moved aside)."""
        self.require_member(user)
        f = self.path("fulltexts", f"{rid}.pdf")
        if rid not in self.by_id() or not os.path.isfile(f):
            raise ProjectError("That PDF is no longer there.")
        keep = self.path("fulltexts", "_replaced")
        os.makedirs(keep, exist_ok=True)
        util._replace(f, util.unique_path(keep, f"{rid}.pdf." + time.strftime("%Y%m%d-%H%M%S")))
        self._memo.clear()
        self.audit(user, "take off a PDF found in the folder", {"record": rid})

    def move_stray_files(self, user: str) -> int:
        """Files in the fulltexts folder that ReviewDesk does not use go to the PDFs waiting to be placed."""
        self.require_member(user)
        pr = next((x for x in self.integrity_report()["problems"] if x["kind"] == "pdf_stray"), None)
        n = 0
        wait = self.path("fulltexts", "_unmatched")
        os.makedirs(wait, exist_ok=True)
        for name in (pr or {}).get("names", []):
            src = self.path("fulltexts", name)
            if not os.path.isfile(src):
                continue
            dest = util.unique_path(wait, util.safe_filename(name, max_len=WAITING_NAME))   # short: see LONGEST_NAME
            util._replace(src, dest)
            util.write_json_atomic(dest + ".json", {"from": name, "by": user, "t": util.now(), "suggest": "",
                                                    "why": "found in the fulltexts folder"})
            n += 1
        self._memo.clear()
        if n:
            self.audit(user, "move files found in the fulltexts folder to the waiting list", {"files": n})
        return n

    def restore_pdf(self, user: str, rid: str) -> None:
        """Put back this computer's copy of the PDF the record of PDFs says the report has."""
        self.require_member(user)
        if rid not in self.by_id():
            raise ProjectError("No such record.")
        e = self.pdf_index().get(rid) or {}
        snap = self._pdf_snap(rid, e.get("sha", ""))
        if e.get("removed") or not os.path.isfile(snap):
            raise ProjectError("This computer has no copy of the PDF this report should have. Add it again on the report's "
                               "page, or use OneDrive's version history.")
        f = self.path("fulltexts", f"{rid}.pdf")
        if os.path.isfile(f):
            keep = self.path("fulltexts", "_replaced")
            os.makedirs(keep, exist_ok=True)
            util._replace(f, util.unique_path(keep, f"{rid}.pdf." + time.strftime("%Y%m%d-%H%M%S")))
        with open(snap, "rb") as fh:
            util.write_bytes_atomic(f, fh.read())
        self._memo.clear()
        self.audit(user, "put back a PDF from this computer's copy", {"record": rid})

    def decisions(self) -> dict:
        """{reviewer: [decision, ...]} for every person in the project, oldest first: the rows of one file in the order
        written, rows from a person's different computers interleaved by time (see written_order).
        Copies the sync service made of a file (e.g. CH@3f9a1c-LAPTOP.jsonl) are merged in; identical rows count once."""
        def load():
            self._heal_own_files()
            out = {}
            for r in self.reviewers():
                files = util.files_in(self.path("decisions"), f"{glob.escape(r['id'])}@*.jsonl")
                legacy = self.path("decisions", f"{r['id']}.jsonl")
                if os.path.isfile(legacy):
                    files.append(legacy)
                seen, rows = set(), []
                for n, f in enumerate(files):
                    def keyed(f=f):                      # worked out once for each version of a file, not at every vote
                        got = self._cached("dec:" + f, f, lambda: util.read_jsonl(f))
                        return [(t, k, d, self._content_key(d)) for t, k, d in written_order(got)]
                    for t, k, d, key in self._cached("deck:" + f, f, keyed):
                        if key not in seen:                  # the same row in two copies counts once, even if one copy
                            seen.add(key)                    # gave it a new fingerprint
                            rows.append((t, k, n, d))
                rows.sort(key=lambda x: x[:3])       # (the same order as before whenever each file's stamps run in order)
                out[r["id"]] = [d for *_, d in rows]
            return out
        return self.memo("decisions", load)

    def add_resolution(self, user: str, rec_id: str, stage: str, decision: str, reason: str = "", note: str = "",
                       study: str = "") -> dict:
        self.require_lead(user)
        d = {"t": util.now(), "r": rec_id, "s": stage, "d": decision, "by": user}
        if reason: d["reason"] = reason
        if note: d["note"] = note
        if study: d["study"] = study
        name = f"resolutions@{self.dev()}.jsonl"
        d = integrity.append(self.path("state", name), d, mirror=self._local("mirror", "state__" + name))
        self.audit(user, "resolve", {"record": rec_id, "stage": stage, "decision": decision})
        return d

    def resolutions(self) -> dict:
        """{(record, stage): latest resolution}"""
        def load():
            rows = self._read_all(self.path("state", "resolutions*.jsonl"), "res")
            out = {}
            for d in rows:
                out[(d["r"], d["s"])] = d
            return out
        return self.memo("resolutions", load)

    # ------------------------------------------------------------------------------------------------ worksheet and search log
    def worksheet(self) -> dict:
        return self._read_shared(("searches", "worksheet.json"), {"rev": 0}, "searches/worksheet.json") or {"rev": 0}

    def save_worksheet(self, user: str, ws: dict) -> dict:
        self.require_member(user)
        with self.lock:
            cur = self.worksheet()
            if int(ws.get("rev", -1)) != int(cur.get("rev", 0)):
                raise ProjectError("Someone else changed the worksheet since you opened it. Reload the page to see their "
                                   "changes, then make yours again.")
            ws["rev"] = int(cur.get("rev", 0)) + 1
            ws["saved_by"], ws["saved_at"] = user, util.now()
            util.write_json_atomic(self.path("searches", "worksheet.json"), ws)
            self.backup("searches", "worksheet.json")
            self.audit(user, "save search worksheet", {"rev": ws["rev"]})
            return ws

    def search_log(self) -> list:
        """Every person's entries; the latest version of each entry wins; deleted entries are dropped."""
        latest = {}
        for e in self._read_all(self.path("searches", "log-*.jsonl"), "log"):
            if not e.get("link"):                 # an imported file linked to its search later (link_import)
                latest[e["id"]] = e
        return sorted((e for e in latest.values() if not e.get("deleted")), key=lambda e: (e.get("date", ""), e.get("database", "")))

    def save_search(self, user: str, entry: dict) -> dict:
        self.require_member(user)
        entry = dict(entry)
        if not entry.get("deleted"):
            if not str(entry.get("database") or "").strip():
                raise ProjectError("Say which database or source was searched.")
            if not str(entry.get("date") or "").strip():
                raise ProjectError("Give the date the search was run.")
            if str(entry.get("date"))[:10] > util.today():
                raise ProjectError("The date searched is in the future: give the date the search was actually run.")
            if not (str(entry.get("string") or "").strip() or str(entry.get("results") or "").strip()):
                raise ProjectError("Record the search itself (paste the strategy) and the number of records it found.")
            res = str(entry.get("results") or "").replace(",", "").strip()
            if res and not res.isdigit():
                raise ProjectError("The number of records should be a whole number (for example 1234).")
        if not entry.get("id"):
            taken = {e.get("id") for e in self._read_all(self.path("searches", "log-*.jsonl"), "log")}
            entry["id"] = _unique_id(f"S-{clean_id(user)}-{util.today().replace('-', '')}", taken)
        else:
            owner = next((e for e in self.search_log() if e["id"] == entry["id"]), None)
            if owner and owner.get("by") != user and not self.is_lead(user):
                raise PermissionDenied("Only the person who recorded this search (or the lead) can change it.")
        entry["by"] = entry.get("by") or user
        entry["saved"] = util.now()
        entry["t"] = entry["saved"]
        util.append_jsonl(self.path("searches", f"log-{clean_id(user)}@{self.dev()}.jsonl"), entry)
        self.audit(user, "save search log entry", {"id": entry["id"], "database": entry.get("database")})
        return entry

    def link_import(self, user: str, iid: str, search_id: str) -> dict:
        """Link an imported file to its search in the search log after it was added (or unlink it, with search_id "").
        An import's own details file is never rewritten (imports are new files only): the link is a row in the linker's
        own search log file, and imports() applies the latest link for each file. The person who added the file, or the
        lead, may link it; linking changes no record, so it is allowed at any time."""
        self.require_member(user)
        m = next((x for x in self.imports(include_missing=True) if x["id"] == iid), None)
        if not m:
            raise ProjectError("That imported file is no longer in the project.")
        if m.get("added_by") != user and not self.is_lead(user):
            raise PermissionDenied("Only the person who added this file, or the project lead, can link it to a search.")
        search_id = str(search_id or "")
        e = next((x for x in self.search_log() if x["id"] == search_id), None) if search_id else None
        if search_id and not e:
            raise ProjectError("That search is not in the search log (it may have been deleted). Choose another, or record "
                               "the search first (Plan and log searches, Search log).")
        row = {"id": "link:" + iid, "link": iid, "search_id": search_id, "search_date": (e or {}).get("date", ""), "by": user,
               "saved": util.now()}
        row["t"] = row["saved"]
        util.append_jsonl(self.path("searches", f"log-{clean_id(user)}@{self.dev()}.jsonl"), row)
        self.audit(user, "link import to search", {"file": m["filename"], "search": search_id or "none",
                                                     "database": (e or {}).get("database", "")})
        return row

    def _with_links(self, imports: list) -> list:
        """The imports with the latest link to a search applied (link_import), where one was made after adding."""
        links = {}
        for r in self._read_all(self.path("searches", "log-*.jsonl"), "log"):
            if r.get("link"):
                links[r["link"]] = r                 # in time order: the latest wins
        for m in imports:
            ln = links.get(m.get("id"))
            if ln:
                m["search_id"], m["search_date"] = ln.get("search_id", ""), ln.get("search_date", "")
        return imports


README_TEXT = """This folder is a ReviewDesk project.

Open it with ReviewDesk (Start ReviewDesk), not by editing the files: the files record who decided what and
when, and the tool checks them. Each person writes only their own files here, so it is safe for everyone in
the team to have the project open at the same time through OneDrive, Teams or another synced folder.

  decisions/   each reviewer's screening decisions (one file per person)
  imports/     the database exports, exactly as downloaded
  fulltexts/   full-text PDFs, named by record ID
  state/       records, de-duplication and screening state (written by the project lead)
  searches/    the search worksheet and search log
  ai/          AI screening settings, prompts and answers (written by the project lead)
  audit/       a log of what each person did
"""


DB_PRIORITY = ("medline", "pubmed", "embase", "central", "cochrane", "cinahl", "psycinfo", "scopus", "web of science")


def online_only_message(n: int, platform: str = "") -> str:
    """What to tell someone whose copy of the project is partly online only, and how to fix it on their system."""
    platform = platform or sys.platform
    if platform.startswith("win"):
        fix = "in File Explorer, right-click the project folder and choose 'Always keep on this device'."
    elif platform == "darwin":
        fix = "in Finder, right-click the project folder and choose 'Always Keep on This Device'."
    else:
        fix = "set your sync service to keep the project folder downloaded on this computer."
    one = n == 1
    return (f"{util.plural(n, 'project file')} {'is' if one else 'are'} stored online only on this computer, so "
            f"ReviewDesk has to wait for the sync service to download {'it whenever it changes' if one else 'them whenever they change'}: "
            f"it is slower, and it cannot read {'it' if one else 'them'} when you are offline. To fix this, {fix}")


def db_rank(name: str) -> int:
    """Records from the first database read keep their title and details when duplicates merge, so the databases with
    the fullest records (MEDLINE, then Embase) are read first."""
    n = (name or "").lower()
    return next((i for i, k in enumerate(DB_PRIORITY) if k in n), len(DB_PRIORITY))


def check_values(s: dict) -> None:
    """Refuse settings that cannot work, with a message saying what is allowed."""
    sc = s.get("screening") or {}
    def num(k, lo, hi, label, what):
        v = sc.get(k)
        if v is None:
            return
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ProjectError(f"{label} must be a number {what}.")
        if not (lo <= v <= hi):
            raise ProjectError(f"{label} must be {what}.")
    num("sample_fraction", 0.05, 0.9, "The agreement sample", "between 5% and 90% of the records")
    num("kappa_threshold", 0.01, 1.0, "The kappa needed for single screening", "between 0.01 and 1 (for example 0.8; use a full stop for the decimal point)")
    num("ai_keep_rate_stop", 0.05, 1.0, "The AI stopping limit", "between 5% and 100% of the records")
    pair = [x for x in (sc.get("reviewers") or []) if x]
    if len(pair) == 2 and pair[0] == pair[1]:
        raise ProjectError("The first and second reviewers must be two different people. Choose another second reviewer "
                           "in the screening plan.")
    c = sc.get("calibration_size")
    if c is not None:
        try:
            c = int(c)
        except (TypeError, ValueError):
            raise ProjectError("The number of calibration records must be a whole number.")
        if not (0 <= c <= 1000):
            raise ProjectError("The number of calibration records must be between 0 (no calibration round) and 1,000.")


def backup_stamp(t: float) -> str:
    """The end of the name of a copy this computer keeps of a shared file: the moment in UTC, to the microsecond, after
    a 'u'. Copies named by earlier versions end in the local time to the second (20261025-013000), which repeats in the
    hour the clocks go back and could name two copies alike; a 'u' sorts after every digit, so sorting the names still
    puts every copy in the order it was made."""
    import datetime as dt
    d = dt.datetime.fromtimestamp(t, dt.timezone.utc)
    return f"u{d.year:04d}{d.month:02d}{d.day:02d}-{d.hour:02d}{d.minute:02d}{d.second:02d}-{d.microsecond:06d}"


def backup_when(name: str) -> str:
    """When a kept copy was made, in this computer's local time (YYYY-MM-DD HH:MM), from its name (either kind)."""
    stamp = name.rpartition(".")[2]
    if stamp.startswith("u"):
        import datetime as dt
        try:
            t = dt.datetime.strptime(stamp[1:16], "%Y%m%d-%H%M%S").replace(tzinfo=dt.timezone.utc).timestamp()
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))
        except ValueError:
            return stamp
    return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[9:11]}:{stamp[11:13]}"


def rule_history(before, now: str) -> list:
    """Every duplicate rule this project's duplicates have been removed with, oldest first: [{"rule", "version",
    "since"}]. The rule can change between versions of ReviewDesk, and records already being screened keep the merges
    made under the rule of their day, so the reports must be able to say which rules were used and from when, not
    only today's. before: the de-duplication record as it was (dedup.json), which until now kept only its last rule."""
    before = before if isinstance(before, dict) else {}
    hist = [dict(h) for h in before.get("rules") or [] if isinstance(h, dict) and h.get("rule")]
    if not hist and before.get("rule"):
        hist = [{"rule": before["rule"], "version": str(before.get("version") or ""), "since": ""}]
    if not hist or hist[-1]["rule"] != RULE_TEXT:
        hist.append({"rule": RULE_TEXT, "version": VERSION, "since": now if hist else ""})
    return hist


def written_order(rows: list) -> list:
    """[(time, position, row)] for the rows of one append-only file (leaving out repair notes and anything that is not
    a row), with each row's time made no earlier than the row before it. A file is written by one computer, in order,
    so the order written is the order things happened, even when the computer's clock was put back between two rows
    (Windows correcting a fast clock, a wrong time zone put right): a correction stamped earlier than the vote it
    corrects still comes after it. The time is used only to interleave different files (one person on two
    computers). When a file's stamps already run in order, the times are the stamps themselves, so the result is the
    same as sorting by stamp."""
    out, last = [], None
    for k, d in enumerate(rows):
        if not isinstance(d, dict) or "repair" in d:
            continue
        t = util.ts(d.get("t", ""))
        last = t if last is None or t > last else last
        out.append((last, k, d))
    return out


ID_DIGITS = (10_000_000, 99_999_999)


def _unique_id(stem: str, taken: set) -> str:
    """stem-NNNNNNNN with a random eight-digit number not already used on this computer. IDs made by earlier versions have
    four digits, and two files added by one person on one day on two computers could then draw the same one (about once
    in 9,000 pairs); with eight it is about once in 90 million, and process_imports refuses two files with one ID."""
    rnd = random.SystemRandom()
    for _ in range(40):
        x = f"{stem}-{rnd.randint(*ID_DIGITS)}"
        if x not in taken:
            return x
    k = 2
    while f"{x}-{k}" in taken:
        k += 1
    return f"{x}-{k}"


def clean_id(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", s or "").upper()[:8]


def _deep_copy(o):
    return copy.deepcopy(o)


def _merge(base: dict, changes: dict) -> None:
    for k, v in changes.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v


def _stamp(path: str, st) -> tuple:
    """A file's size and time, and when this process last wrote it (see util.WROTE): on a disk that keeps file times
    only to 2 seconds, two saves of the same length in one slot look the same by size and time alone."""
    return (st.st_mtime_ns, st.st_size, util.WROTE.get(os.path.normcase(os.path.abspath(path)), 0))


def _version(d) -> dict:
    """What one version of a shared file says about itself: its revision, when it was saved, on which computer and by
    whom ({} if it says nothing)."""
    if not isinstance(d, dict):
        return {}
    rev = d.get("rev")
    out = {"rev": rev if isinstance(rev, int) and not isinstance(rev, bool) else None,
           "at": str(d.get("saved_at") or d.get("updated") or d.get("saved") or d.get("processed") or ""),
           "on": str(d.get("saved_on") or d.get("updated_on") or ""),
           "by": str(d.get("saved_by") or d.get("updated_by") or d.get("by") or "")}
    return out if out["rev"] is not None or out["at"] else {}


def _newer(a: dict, b: dict) -> bool:
    """Version a is newer than version b: by revision where both have one, otherwise by the time saved."""
    if not a or not b:
        return False
    if a.get("rev") is not None and b.get("rev") is not None and a["rev"] != b["rev"]:
        return a["rev"] > b["rev"]
    return bool(a.get("at") and b.get("at")) and util.ts(a["at"]) > util.ts(b["at"])


def _same(a: dict, b: dict) -> bool:
    return bool(a) and bool(b) and all(a.get(k) == b.get(k) for k in ("rev", "at", "on", "by"))


def p_memo(p, fn):
    """The integrity report, reused until a project file (or an export or PDF) changes."""
    def listing(sub):
        try:
            return tuple(sorted((e.name, e.stat().st_mtime_ns, e.stat().st_size) for e in os.scandir(p.path(sub)) if e.is_file()))
        except OSError:
            return ()
    return p.memo(("integrity", listing("imports"), listing("fulltexts")), fn)
