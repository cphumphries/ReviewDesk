"""Helpers for tests (and the beta test) that use ReviewDesk the way its page does.

* :class:`Client`: talks to a running ReviewDesk over HTTP with the session token, sending ``X-Project`` on every change
  as the page does, and ``as`` (who the page was opened as) with every vote.
* :class:`LocalServer`: ReviewDesk's own server, in this process, with its own settings folder (``REVIEWDESK_HOME``).
* :class:`ServerProcess`: ReviewDesk started as a separate program with its own settings folder: another computer.
* :func:`on_computer`: run ReviewDesk's code here as if on another computer (its own settings folder).
* :class:`FakeOneDrive`: several computers' copies of one project folder, synced the way OneDrive syncs them (including
  the second version OneDrive keeps, named ``<file>-<COMPUTER>.<ext>``, when two computers changed a file before it caught up).
* :class:`TruthMap`: which generated work (``realistic.Work``) is behind each of ReviewDesk's records.
* :class:`SimReviewer`: a reviewer who screens, assesses full texts, extracts and judges risk of bias through the API, from
  the truth, with seeded slips.
"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import http.client
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import zipfile
from xml.dom import minidom

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

import realistic  # noqa: E402


class ApiError(Exception):
    def __init__(self, status: int, data: dict, path: str = ""):
        self.status, self.data, self.path = status, data, path
        super().__init__(f"{status} {path}: {data.get('error') if isinstance(data, dict) else data}")

    @property
    def message(self) -> str:
        return (self.data or {}).get("error", "") if isinstance(self.data, dict) else str(self.data)


NO_PROJECT_CHECK = ("/api/project/open", "/api/project/create", "/api/project/close", "/api/user")


class Client:
    """ReviewDesk's API as its page uses it."""

    def __init__(self, port: int, token: str, label: str = ""):
        self.port, self.token, self.label = port, token, label
        self.project_key = ""
        self.me = ""

    # ------------------------------------------------------------------------------------------- plumbing
    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None, params: dict | None = None,
                token: bool = True, timeout: float = 120) -> tuple:
        if params:
            path += ("&" if "?" in path else "?") + urllib.parse.urlencode(params)
        h = {"Host": f"127.0.0.1:{self.port}"}
        if token:
            h["X-Token"] = self.token
        h.update(headers or {})
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            c.request(method, path, body=body, headers=h)
            r = c.getresponse()
            data = r.read()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, data
        finally:
            c.close()

    def _json(self, status, data, path):
        try:
            obj = json.loads(data.decode("utf-8")) if data else {}
        except ValueError:
            obj = {"error": data[:300].decode("utf-8", "replace")}
        if status != 200:
            raise ApiError(status, obj, path)
        return obj

    def get(self, path: str, **params):
        st, _, data = self.request("GET", path, params={k: v for k, v in params.items() if v is not None})
        return self._json(st, data, path)

    def post(self, path: str, body: dict | None = None, **params):
        h = {"Content-Type": "application/json"}
        if self.project_key and path.split("?")[0] not in NO_PROJECT_CHECK:
            h["X-Project"] = self.project_key
        st, _, data = self.request("POST", path, json.dumps(body or {}).encode("utf-8"), h, params=params or None)
        out = self._json(st, data, path)
        if path in ("/api/project/open", "/api/project/create") and (out.get("project") or {}).get("key"):
            self.project_key = out["project"]["key"]
            self.me = out["project"].get("me", "")
        return out

    def upload(self, path: str, data: bytes, **params):
        h = {"Content-Type": "application/octet-stream"}
        if self.project_key:
            h["X-Project"] = self.project_key
        st, _, out = self.request("POST", path, data, h, params={k: v for k, v in params.items() if v is not None})
        return self._json(st, out, path)

    def download(self, kind: str) -> tuple:
        """(status, headers, bytes) of /download/<kind>."""
        return self.request("GET", "/download/" + kind)

    def error(self, method: str, path: str, body=None, **params) -> ApiError:
        """The refusal a request gets (fails the test if it is not refused)."""
        try:
            self.post(path, body, **params) if method == "POST" else self.get(path, **params)
        except ApiError as e:
            return e
        raise AssertionError(f"{method} {path} was not refused")

    # ------------------------------------------------------------------------------------------- the project
    def create_project(self, parent: str, name: str = "AI triage review", title: str = "AI in emergency department triage: "
                       "prospective clinical evaluations", lead_name: str = "Dr Colin Hawthorne", lead_id: str = "CH") -> dict:
        os.makedirs(parent, exist_ok=True)
        out = self.post("/api/project/create", {"parent": parent, "name": name, "title": title, "lead_name": lead_name,
                                                "lead_id": lead_id})
        return out

    def open_project(self, path: str) -> dict:
        return self.post("/api/project/open", {"path": path})

    def be(self, who: str) -> dict:
        """Choose who is at this computer (as the person menu at the top right does)."""
        out = self.post("/api/me", {"reviewer": who})
        self.me = who
        return out

    def summary(self) -> dict:
        return self.get("/api/app")["project"]

    def settings(self, **changes) -> dict:
        return self.post("/api/settings", {"changes": changes})

    def decide(self, record: str, stage: str, decision: str, reason: str = "", note: str = "", study: str = "",
               finish: bool = True) -> dict:
        """Cast a vote. finish: for a verification sample ("vs"), say as the page does (after asking) that the vote on
        its last record is meant to finish it; without it the server refuses that one vote."""
        return self.post("/api/decide", {"record": record, "stage": stage, "decision": decision, "reason": reason, "note": note,
                                         "study": study, "as": self.me, **({"finish": True} if stage == "vs" and finish else {})})

    def resolve(self, record: str, stage: str, decision: str, reason: str = "", note: str = "", study: str = "") -> dict:
        return self.post("/api/resolve", {"record": record, "stage": stage, "decision": decision, "reason": reason, "note": note,
                                          "study": study, "as": self.me})

    def queue(self, task: str, set_name: str = "main") -> list:
        return self.get("/api/queue", task=task, set=set_name)["items"]

    def tasks(self) -> list:
        return self.get("/api/tasks")["tasks"]

    def task(self, task: str, set_name: str = "main") -> dict | None:
        return next((t for t in self.tasks() if t["task"] == task and t["set"] in (set_name, "all")), None)

    def overview(self) -> dict:
        return self.get("/api/overview")

    def phase(self, set_name: str = "main") -> str:
        return self.overview()["sets"].get(set_name, {}).get("phase", "")

    def add_import(self, data: bytes, filename: str, database: str, set_name: str = "main", platform: str = "",
                   search_id: str = "", search_date: str = "", part: str = "", notes: str = "") -> dict:
        return self.upload("/api/imports/upload", data, filename=filename, set=set_name, database=database, platform=platform,
                           search_id=search_id, search_date=search_date, part=part, notes=notes)

    def process(self) -> dict:
        return self.post("/api/imports/process", {})

    def run_ai(self, scope: str = "all", wait: float = 120) -> dict:
        self.post("/api/ai/run", {"scope": scope})
        return self.wait_ai(wait)

    def wait_ai(self, wait: float = 120) -> dict:
        end = time.time() + wait
        while time.time() < end:
            st = self.get("/api/ai")["status"]
            if not st.get("running"):
                return st
            time.sleep(0.05)
        raise AssertionError("the AI run did not finish")


# ================================================================================================ servers
class LocalServer:
    """ReviewDesk's server in this process, with its own settings folder. (Only one at a time: the server keeps what
    it knows in one place for the whole program, as each person's ReviewDesk does on their own computer.)"""

    def __init__(self, home: str):
        from reviewdesk import server
        self.home = home
        os.makedirs(home, exist_ok=True)
        self._old = os.environ.get("REVIEWDESK_HOME")
        os.environ["REVIEWDESK_HOME"] = home
        self.httpd, self.url = server.serve(0)
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]
        self.token = self.url.split("t=")[1]
        self.app = server.APP

    def activate(self) -> "LocalServer":
        """Make this the ReviewDesk in use again (after another test started its own)."""
        from reviewdesk import server
        server.APP = self.app
        os.environ["REVIEWDESK_HOME"] = self.home
        return self

    def client(self, label: str = "") -> Client:
        return Client(self.port, self.token, label)

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._old is not None:
            os.environ["REVIEWDESK_HOME"] = self._old


class ServerProcess:
    """ReviewDesk started as its own program, with its own settings folder: someone else's computer."""

    def __init__(self, home: str, log: str = "", env: dict | None = None):
        os.makedirs(home, exist_ok=True)
        self.home = home
        self.url_file = os.path.join(home, "url.txt")
        if os.path.exists(self.url_file):
            os.remove(self.url_file)
        self.log_path = log or os.path.join(home, "server.log")
        e = dict(os.environ, REVIEWDESK_HOME=home, REVIEWDESK_URL_FILE=self.url_file, REVIEWDESK_NO_REOPEN="1",
                 PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        e.update(env or {})
        self._log = open(self.log_path, "w", encoding="utf-8")
        code = ("import sys; sys.path.insert(0, %r); sys.path.insert(0, %r); import unique_ids; unique_ids.apply(); "
                "from reviewdesk.main import main; main(['--port', '0', '--no-browser'])" % (APP, HERE))
        self.proc = subprocess.Popen([sys.executable, "-c", code], env=e, stdout=self._log, stderr=subprocess.STDOUT,
                                     cwd=home)
        end = time.time() + 30
        while time.time() < end:
            if os.path.exists(self.url_file) and read_text(self.url_file).strip():
                break
            if self.proc.poll() is not None:
                raise RuntimeError("ReviewDesk did not start: " + self.log())
            time.sleep(0.05)
        else:
            raise RuntimeError("ReviewDesk did not start in time: " + self.log())
        url = read_text(self.url_file).strip()
        u = urllib.parse.urlsplit(url)
        self.port = u.port
        self.token = urllib.parse.parse_qs(u.query)["t"][0]

    def client(self, label: str = "") -> Client:
        return Client(self.port, self.token, label)

    def log(self) -> str:
        try:
            self._log.flush()
            with open(self.log_path, encoding="utf-8", errors="replace") as f:
                return f.read()[-4000:]
        except OSError:
            return ""

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self._log.close()


@contextlib.contextmanager
def on_computer(home: str):
    """ReviewDesk's code run here acts as on the computer whose settings folder is `home` (its device ID, its own
    copies of files, who the person at it is)."""
    old = os.environ.get("REVIEWDESK_HOME")
    os.environ["REVIEWDESK_HOME"] = home
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("REVIEWDESK_HOME", None)
        else:
            os.environ["REVIEWDESK_HOME"] = old


class Desk:
    """One person at one computer, using ReviewDesk's Project directly (no server): `with desk:` or desk.do(fn)."""

    def __init__(self, home: str, folder: str, who: str):
        from reviewdesk import userconfig
        from reviewdesk.project import Project
        self.home, self.folder, self.who = home, folder, who
        with on_computer(home):
            self.p = Project(folder)
            userconfig.set_identity(self.p.dir, who, self.p.key())
            self.dev = self.p.dev()

    def __enter__(self):
        self._cm = on_computer(self.home)
        self._cm.__enter__()
        return self.p

    def __exit__(self, *exc):
        self._cm.__exit__(*exc)

    def do(self, fn, *a, **kw):
        with on_computer(self.home):
            return fn(*a, **kw)

    def reopen(self):
        """ReviewDesk started again on this computer (a new Project: nothing remembered in memory)."""
        from reviewdesk.project import Project
        with on_computer(self.home):
            self.p = Project(self.folder)
        return self.p


@contextlib.contextmanager
def earlier_version(version: str = "1.3.0"):
    """While this is open, ReviewDesk in this process writes as a version before 1.3.1 did: only the start of each
    fingerprint and signature (integrity.py), and that version's number in the project file and the audit log. What it
    writes is what the real 1.3.0 wrote, byte for byte, which is how the projects of that time can be tested without it."""
    from reviewdesk import integrity, project, versions
    names = ((integrity, "row_hash"), (integrity, "sign"), (integrity, "record_print"), (integrity, "text_print"),
             (project, "VERSION"), (versions, "VERSION"), (versions, "NEEDS"))
    kept = [getattr(m, n) for m, n in names]
    row_hash, sign, record_print, text_print = kept[:4]
    integrity.row_hash = lambda prev, row: row_hash(prev, row)[:integrity.OLD_ROW]
    integrity.sign = lambda row: sign(row)[:integrity.OLD_PRINT]
    integrity.record_print = lambda r: record_print(r)[:integrity.OLD_PRINT]
    integrity.text_print = lambda x: text_print(x)[:integrity.OLD_PRINT]
    project.VERSION = versions.VERSION = versions.NEEDS = version
    try:
        yield
    finally:
        for (m, n), v in zip(names, kept):
            setattr(m, n, v)


# ================================================================================================ OneDrive
class FakeOneDrive:
    """One project folder, with a copy on each computer, synced as OneDrive syncs: a file changed on one computer is
    uploaded and then downloaded by the others; a file deleted is deleted everywhere; when two computers changed the same
    file before either had synced, the one that syncs second keeps its copy beside the other, renamed with its computer's
    name (state.json becomes state-DESKTOP-CH.json), as OneDrive does, and receives the other version under the name."""

    def __init__(self, root: str, computers: dict):
        """computers: {computer name: folder of its copy}"""
        self.root = root
        self.copies = dict(computers)
        self.cloud: dict = {}                              # relative path -> (bytes, version)
        self.base: dict = {c: {} for c in computers}       # computer -> {path: (version it has, sha of that version)}
        self.version = 0
        self.log: list = []
        for d in self.copies.values():
            os.makedirs(d, exist_ok=True)

    @staticmethod
    def _skip(rel: str) -> bool:
        name = os.path.basename(rel)
        return name.startswith((".tmp-", "~")) or ".placing-" in name or name.endswith(".odsync")

    def _scan(self, comp: str) -> dict:
        out = {}
        d = self.copies[comp]
        for dirpath, _, files in os.walk(d):
            for f in files:
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, d).replace(os.sep, "/")
                if not self._skip(rel):
                    with open(full, "rb") as fh:
                        out[rel] = fh.read()
        return out

    @staticmethod
    def _picked(rel: str, only, skip=()) -> bool:
        return (only is None or any(rel.startswith(o) for o in only)) and not any(rel.startswith(s) for s in skip)

    def _conflict_copy(self, comp: str, rel: str, data: bytes) -> str:
        stem, ext = os.path.splitext(rel)
        copy = f"{stem}-{comp}{ext}"
        self.version += 1
        self.cloud[copy] = (data, self.version)
        self.base[comp][copy] = (self.version, sha(data))
        full = os.path.join(self.copies[comp], *rel.split("/"))
        os.replace(full, os.path.join(self.copies[comp], *copy.split("/")))
        self.base[comp].pop(rel, None)
        self.log.append(("conflict", comp, rel, copy))
        return copy

    def upload(self, comp: str, only=None, skip=()) -> list:
        """Send this computer's changes (to those files only, if `only` is given). Returns the second versions made."""
        made = []
        local = self._scan(comp)
        base = self.base[comp]
        for rel, data in sorted(local.items()):
            if not self._picked(rel, only, skip):
                continue
            b, cloud = base.get(rel), self.cloud.get(rel)
            if b and sha(data) == b[1]:
                continue                                   # not changed here
            if cloud and cloud[0] == data:
                base[rel] = (cloud[1], sha(data))
                continue
            if cloud and (b is None or b[0] < cloud[1]):   # changed here, and elsewhere since this computer last synced
                made.append(self._conflict_copy(comp, rel, data))
                continue
            self.version += 1
            self.cloud[rel] = (data, self.version)
            base[rel] = (self.version, sha(data))
            self.log.append(("up", comp, rel))
        for rel in list(base):                             # deleted or moved away here
            if rel not in local and rel not in made and self._picked(rel, only, skip):
                if rel in self.cloud and base[rel][0] == self.cloud[rel][1]:
                    del self.cloud[rel]
                    self.log.append(("delete", comp, rel))
                del base[rel]
        return made

    def download(self, comp: str, only=None, skip=()) -> list:
        """Receive what the others sent (those files only, if `only` is given; never those in `skip`)."""
        got = []
        base = self.base[comp]
        local = self._scan(comp)
        for rel, (data, ver) in sorted(self.cloud.items()):
            if not self._picked(rel, only, skip):
                continue
            b = base.get(rel)
            if b and b[0] == ver and rel in local:
                continue
            if rel in local and local[rel] == data:
                base[rel] = (ver, sha(data))
                continue
            if rel in local and (b is None or sha(local[rel]) != b[1]):
                continue                                   # changed here and not sent yet: upload() settles it
            full = os.path.join(self.copies[comp], *rel.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full + ".odsync", "wb") as fh:
                fh.write(data)
            os.replace(full + ".odsync", full)
            base[rel] = (ver, sha(data))
            got.append(rel)
        for rel in list(base):                             # deleted elsewhere
            if rel not in self.cloud and self._picked(rel, only, skip):
                if rel in local and sha(local[rel]) == base[rel][1]:
                    os.remove(os.path.join(self.copies[comp], *rel.split("/")))
                del base[rel]
        return got

    def sync(self, *comps, skip=()) -> list:
        """Everyone named (everyone, if nobody is) sends their changes, then receives the others'. Returns the second
        versions made."""
        comps = comps or tuple(self.copies)
        made = []
        for c in comps:
            made += self.upload(c, skip=skip)
        for c in comps:
            self.download(c, skip=skip)
        return made


# ================================================================================================ the truth
class TruthMap:
    """Which generated work is behind each record in a project folder (read from the folder, as files on disk)."""

    def __init__(self, review: "realistic.Review", folder: str):
        self.review, self.folder = review, folder
        self.refresh()

    def refresh(self) -> "TruthMap":
        by_name = {e.name: e for e in self.review.exports}
        self.imports = {}
        idir = os.path.join(self.folder, "imports")
        for f in os.listdir(idir):
            if f.endswith(".meta.json"):
                m = read_json(os.path.join(idir, f))
                if m.get("original_name") in by_name:
                    self.imports[m["id"]] = by_name[m["original_name"]]
        self.records = read_json(os.path.join(self.folder, "state", "records.json"))
        self.by_id = {r["id"]: r for r in self.records}
        dd = read_json(os.path.join(self.folder, "state", "dedup.json"))
        by_wid = self.review.by_wid()
        self.work = {}
        self.rids: dict = {}
        for r in self.records:
            iid, k = r["first_source"].split("#")
            e = self.imports.get(iid)
            w = by_wid.get(e.wids[int(k)]) if e else None
            self.work[r["id"]] = w
            if w:
                self.rids.setdefault(w.wid, set()).add(r["id"])
        for mg in dd.get("merges", []):
            if mg.get("from_import") in self.imports:
                w = by_wid.get(self.imports[mg["from_import"]].wids[mg["from_index"]])
                if w:
                    self.rids.setdefault(w.wid, set()).add(mg["kept"])
        return self

    def of(self, rid: str):
        return self.work.get(rid)

    def rid(self, wid: str) -> str:
        got = sorted(self.rids.get(wid, ()))
        return got[0] if got else ""


# ================================================================================================ a simulated reviewer
class SimReviewer:
    """A reviewer working through the API from the truth, with seeded slips (realistic.Errors). Everything they did is
    kept in .log: (stage, record, decision, wrong)."""

    def __init__(self, client: Client, who: str, truth: TruthMap, errors: "realistic.Errors | None" = None,
                 forced: dict | None = None, max_wrong: dict | None = None):
        self.c, self.who, self.truth = client, who, truth
        self.errors = errors or realistic.Errors(who, 0, {"cal": 0, "ta": 0, "ft": 0})
        self.forced = dict(forced or {})        # {(stage, record): decision} slips planned by the test
        self.max_wrong = dict(max_wrong or {})  # {task: how many slips at most}
        self.log: list = []
        self.wrong_count: dict = {}

    def _ensure(self):
        if self.c.me != self.who:
            self.c.be(self.who)

    def ta_choice(self, rid: str, stage: str, task: str) -> tuple:
        w = self.truth.of(rid)
        right = ("maybe" if w.hard else "include") if w.ta == "include" else "exclude"
        if (stage, rid) in self.forced:
            d = self.forced[(stage, rid)]
            return d, (d in ("include", "maybe")) != (right in ("include", "maybe"))
        wrong = self.errors.wrong(stage, rid)
        if wrong and self.wrong_count.get(task, 0) >= self.max_wrong.get(task, 10 ** 9):
            wrong = False
        if wrong:
            self.wrong_count[task] = self.wrong_count.get(task, 0) + 1
            return ("exclude" if right != "exclude" else "maybe"), True
        return right, False

    def screen(self, task: str, set_name: str = "main", stop_after: int | None = None) -> list:
        """Vote on every record still to do in a task (calibration, sample, screen, second)."""
        self._ensure()
        stage = "cal" if task == "calibration" else "ta"
        done = []
        for it in self.c.queue(task, set_name):
            if it["done"]:
                continue
            if stop_after is not None and len(done) >= stop_after:
                break
            d, wrong = self.ta_choice(it["id"], stage, task)
            self.c.decide(it["id"], stage, d)
            self.log.append((stage, it["id"], d, wrong))
            done.append(it["id"])
        return done

    @staticmethod
    def right_ft(w) -> tuple:
        """What the truth says at full text: (decision, reason, study)."""
        right = w.ft or "exclude"
        reason = w.ft_reason or ""
        if right == "exclude" and not w.ft:          # sent on by mistake at title and abstract
            reason = {"review": "Wrong publication type", "editorial": "Wrong publication type", "comment": "Wrong publication type",
                      "erratum": "Wrong publication type", "protocol": "Wrong publication type"}.get(w.kind, "Wrong study design")
        return right, (reason if right == "exclude" else ""), (w.study_label if right == "include" else "")

    def ft_choice(self, rid: str) -> tuple:
        """(decision, reason, study, wrong)"""
        w = self.truth.of(rid)
        if ("ft", rid) in self.forced:
            d, reason = self.forced[("ft", rid)]
            return d, reason, (w.study_label if d == "include" else ""), d != (w.ft or "exclude")
        right, reason, study = self.right_ft(w)
        if self.errors.wrong("ft", rid) and right in ("include", "exclude") and \
                self.wrong_count.get("fulltext", 0) < self.max_wrong.get("fulltext", 10 ** 9):
            self.wrong_count["fulltext"] = self.wrong_count.get("fulltext", 0) + 1
            if right == "include":
                return "exclude", "Wrong outcomes", "", True
            return "include", "", w.study_label or f"{w.first_author} {w.year}", True
        return right, reason, study, False

    def fulltext(self) -> list:
        self._ensure()
        done = []
        for it in self.c.queue("fulltext", "all"):
            if it["done"]:
                continue
            d, reason, study, wrong = self.ft_choice(it["id"])
            self.c.decide(it["id"], "ft", d, reason=reason, study=study)
            self.log.append(("ft", it["id"], (d, reason, study), wrong))
            done.append(it["id"])
        return done


    # ------------------------------------------------------------------------------------------- extraction and risk of bias
    def extract(self, key: str, answers: dict, where: str = "", finish: bool = True) -> dict:
        """As the first extractor (or, for the items both extract, either): give answers {item: value}, then finish."""
        self._ensure()
        for f, v in answers.items():
            self.c.post("/api/extract/value", {"key": key, "f": f, "v": v, "pg": where})
        return self.c.post("/api/extract/done", {"key": key, "done": True}) if finish else {}

    def check(self, key: str, answers: dict | None = None, corrections: dict | None = None) -> dict:
        """As the second extractor, once the first has finished: extract the items both extract (answers), correct any
        checked item that is wrong (corrections), then finish, confirming the answers checked are the ones seen."""
        self._ensure()
        for f, v in {**(answers or {}), **(corrections or {})}.items():
            self.c.post("/api/extract/value", {"key": key, "f": f, "v": v})
        seen = self.c.get("/api/extract/study", key=key)["state"]["seen"]
        return self.c.post("/api/extract/done", {"key": key, "done": True, "seen": seen})

    def judge(self, key: str, aid: str, tool: str, pick, support: str = "", finish: bool = True) -> dict:
        """Judge every domain of an assessment with pick(domain code) -> judgement, then finish."""
        from reviewdesk import review as _review
        self._ensure()
        t = _review.TOOLS[tool]
        for code, _ in t["domains"]:
            self.c.post("/api/rob/judge", {"key": key, "a": aid, "d": code, "j": pick(code), "q": support})
        return self.c.post("/api/rob/done", {"key": key, "a": aid, "done": True}) if finish else {}


# ================================================================================================ checking downloads
def read_csv(data: bytes) -> list:
    """Rows of a downloaded CSV (it starts with a byte-order mark, for Excel)."""
    assert data[:3] == b"\xef\xbb\xbf", "the CSV should start with a byte-order mark so Excel reads it as UTF-8"
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline="")))


FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def formula_cells(rows: list) -> list:
    """Cells (after the header row) a spreadsheet would run as a formula."""
    return [(i, j, v) for i, row in enumerate(rows[1:], 1) for j, v in enumerate(row) if v[:1] in FORMULA_START]


def check_xlsx(data: bytes) -> dict:
    """A workbook that opens: a valid zip whose every XML part is well formed, with no formulas in it. Returns
    {sheet name: [[cell text, ...], ...]}."""
    import re
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.testzip() is None
    names = z.namelist()
    for need in ("[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml"):
        assert need in names, need
    for n in names:
        minidom.parseString(z.read(n))                      # every part is well-formed XML
        assert b"<f>" not in z.read(n) and b"<f " not in z.read(n), f"{n} holds a formula"
    wb = minidom.parseString(z.read("xl/workbook.xml"))
    sheets = [s.getAttribute("name") for s in wb.getElementsByTagName("sheet")]
    out = {}
    for i, name in enumerate(sheets, 1):
        doc = minidom.parseString(z.read(f"xl/worksheets/sheet{i}.xml"))
        rows = []
        for row in doc.getElementsByTagName("row"):
            cells = []
            for c in row.getElementsByTagName("c"):
                t = c.getElementsByTagName("t")
                v = c.getElementsByTagName("v")
                cells.append("".join(x.firstChild.data if x.firstChild else "" for x in t) if t else
                             (v[0].firstChild.data if v and v[0].firstChild else ""))
            rows.append(cells)
        out[name] = rows
        for row in doc.getElementsByTagName("v"):
            txt = row.firstChild.data if row.firstChild else ""
            assert re.fullmatch(r"-?\d+(\.\d+)?([eE][-+]?\d+)?", txt), f"sheet {name}: {txt!r} is not a number Excel can read"
    return out


def check_svg(data: bytes):
    """An SVG that parses as XML."""
    doc = minidom.parseString(data)
    assert doc.documentElement.tagName == "svg"
    return doc


def svg_text(data: bytes) -> str:
    doc = check_svg(data)
    return "\n".join("".join(n.data for n in t.childNodes if n.nodeType == n.TEXT_NODE) for t in doc.getElementsByTagName("text"))


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def copytree(src: str, dst: str) -> None:
    shutil.copytree(src, dst)


# ================================================================================================ the reference review
def reference_plan(**plan) -> dict:
    """The registered screening plan of the reference review (anything given replaces the matching part)."""
    sc = {"reviewers": ["NH", "CH"], "calibration_size": 100, "mode": "sample_then_single", "sample_fraction": 0.2,
          "kappa_threshold": 0.8, "single_reviewer": "NH", "second_reviewer": "CH", "ai_second_screener": True,
          "ai_keep_rate_stop": 0.5, "conflicts_at_title_abstract": "forward"}
    sc.update(plan)
    return sc


# The reference review's risk-of-bias plan, as its protocol gives it: RoB 2 for randomised trials (the cluster version
# for cluster trials), ROBINS-I V2 for studies with concurrent controls, ROBINS-I (2016) as the Cochrane Handbook (25.5)
# describes for uncontrolled before-after and interrupted time series studies, and PROBAST+AI for accuracy.
REFERENCE_DESIGNS = ("rct", "cluster_rct", "nrsi", "before_after", "its", "prediction")


def rob_plan(*ids, extra: list | None = None, tools: dict | None = None) -> dict:
    """A risk-of-bias plan of standard designs (each with its recommended tool), plus any extra designs."""
    from reviewdesk import robtools
    std = {d: (n, t) for d, n, t in robtools.STANDARD_DESIGNS}
    return {"designs": [{"id": d, "name": std[d][0], "tool": std[d][1]} for d in (ids or REFERENCE_DESIGNS)] + list(extra or []),
            "tools": dict(tools or {})}


def record_plan(p, user: str = "CH", plan: dict | None = None, reason: str = "") -> dict:
    """Record a risk-of-bias plan straight into a project (the lead's own computer)."""
    from reviewdesk import robplan
    return robplan.save_plan(p, user, plan or rob_plan(), reason)


def reference_project(c: Client, parent: str, name: str = "AI triage review", screening: dict | None = None,
                      plan: dict | bool = True, **settings) -> str:
    """A new project set up as the (invented) reference review plans it: CH (Dr Colin Hawthorne) the lead, NH (Nadine Hayes)
    the reviewer, with its risk-of-bias plan recorded (plan=False leaves it out). Returns the project folder. The client
    is left as CH."""
    out = c.create_project(parent, name)
    folder = out["project"]["path"]
    c.me = "CH"
    changes = {"question": realistic.QUESTION, "criteria": realistic.CRITERIA, "short_title": "AI triage",
               "reviewers": [{"id": "CH", "name": "Dr Colin Hawthorne", "role": "lead"},
                             {"id": "NH", "name": "Nadine Hayes", "role": "reviewer"}],
               "screening": screening if screening is not None else reference_plan(),
               "fulltext": {"mode": "dual"}, "registers": realistic.REGISTERS,
               "registration": {"registry": "PROSPERO", "id": "CRD420261234567", "date": "2026-09-01", "url": ""}}
    changes.update(settings)
    c.settings(**changes)
    if plan:
        c.post("/api/rob/plan", {"plan": plan if isinstance(plan, dict) else rob_plan()})
    return folder


def log_and_import(c: Client, review, sets=("main",), log: bool = True) -> dict:
    """Log each search (as the searcher does, with the number of records it found) and add its export files, linked to
    it. Returns {import ID: realistic.Export}."""
    out = {}
    entries = {}
    if log:
        for e in review.searches():
            if e["set"] not in sets:
                continue
            body = {k: v for k, v in e.items() if not k.startswith("_") and k != "set"}
            saved = c.post("/api/searchlog", body)
            entries[(e["set"], e["_db"])] = saved["id"]
    for ex in review.exports:
        if ex.set not in sets:
            continue
        m = c.add_import(ex.data, ex.name, ex.database, set_name=ex.set, platform=ex.platform,
                         search_id=entries.get((ex.set, ex.database), ""), search_date=ex.date, part=ex.part)
        out[m["id"]] = ex
    return out


def screen_all(c: Client, who: str, decide, task: str, set_name: str = "main", stage: str = "") -> list:
    """Vote on every record of a task with decide(record ID) -> decision. Returns the record IDs."""
    if c.me != who:
        c.be(who)
    stage = stage or ("cal" if task == "calibration" else "ft" if task == "fulltext" else "ta")
    done = []
    for it in c.queue(task, set_name):
        if not it["done"]:
            got = decide(it["id"])
            if isinstance(got, tuple):
                c.decide(it["id"], stage, got[0], reason=got[1] if len(got) > 1 else "", study=got[2] if len(got) > 2 else "")
            else:
                c.decide(it["id"], stage, got)
            done.append(it["id"])
    return done


def ris(*recs: dict) -> bytes:
    """A small RIS file from records given as dicts (title, authors [list], year, journal, doi, pmid, pubtype, abstract,
    language), for tests that need exact records."""
    out = []
    for r in recs:
        out.append("TY  - " + r.get("ty", "JOUR"))
        out.append("TI  - " + r.get("title", ""))
        for a in r.get("authors", []):
            out.append("AU  - " + a)
        for tag, k in (("PY", "year"), ("JO", "journal"), ("DO", "doi"), ("M3", "pubtype"), ("AB", "abstract"), ("LA", "language"),
                       ("VL", "volume"), ("SP", "pages")):
            if r.get(k):
                out.append(f"{tag}  - {r[k]}")
        if r.get("pmid"):
            out.append(f"AN  - PMID: {r['pmid']}")
        out.append("ER  - ")
        out.append("")
    return "\n".join(out).encode("utf-8")


def tiny_records(n: int, seed: int = 1, prefix: str = "") -> list:
    """n distinct, readable records (title, authors, year, journal, DOI, abstract)."""
    import random
    r = random.Random(seed)
    words = ["triage", "acuity", "waiting", "nurses", "algorithm", "admission", "sepsis", "children", "older", "ambulance", "crowding",
             "deterioration", "pain", "fracture", "stroke", "chest", "fever", "asthma", "frailty", "equity", "Māori", "rural",
             "telehealth", "observation", "handover", "boarding", "discharge", "lactate", "troponin", "imaging"]
    out, seen = [], set()
    while len(out) < n:
        w = r.sample(words, 4)
        t = f"{prefix}{w[0].capitalize()} and {w[1]} in {w[2]} {w[3]}: study {len(out) + 1} of emergency care"
        if t in seen:
            continue
        seen.add(t)
        k = len(out) + 1
        out.append({"title": t, "authors": [f"{r.choice(['Ngata', 'Fraser', 'Patel', 'Okafor', 'Kim'])}, A{k}", "Smith, J"],
                    "year": str(2020 + k % 6), "journal": "Emergency Medicine Journal", "doi": f"10.1136/emj-test-{seed}-{k:04d}",
                    "abstract": f"Background: {t}. Methods: a study of {k * 10} patients."})
    return out


# ================================================================================================ files, as another program would touch them
def read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def write_bytes(path: str, data: bytes, append: bool = False) -> None:
    with open(path, "ab" if append else "wb") as f:
        f.write(data)


def read_text(path: str) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def write_json(path: str, obj) -> None:
    write_text(path, json.dumps(obj, ensure_ascii=False))


def read_json(path: str):
    return json.loads(read_text(path))
