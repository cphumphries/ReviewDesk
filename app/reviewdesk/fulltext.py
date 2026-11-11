"""Full texts: where each PDF is kept, finding free copies, the list of those still missing, and adding PDFs in bulk.
ReviewDesk needs no other program for any of this: a browser is enough.

Finding free copies. For each report at full text without a PDF, ReviewDesk asks, in turn, Unpaywall, Europe PMC
(PubMed Central's open-access copies), OpenAlex and Semantic Scholar for a legal open-access PDF, by DOI, PubMed ID or
(when a record has neither) title, and downloads the first one that really is a PDF. A link that leads only to a web
page is kept, so a person can open it. Nothing behind a login is attempted: subscription papers are fetched by a
person, through the library links on each report. A search never replaces a PDF already there.

Adding PDFs in bulk. Any number of PDFs (a folder of downloads, say) can be added at once. A PDF is placed on a
report only when the PDF itself says which report it is: the record ID in its file name, its own DOI in its details,
or the DOI (or PubMed ID) printed first in it together with the report's title near its start. Anything less (a
title alone, a DOI cited in a reference list) is a suggestion: the PDF waits in a list for a person to check and
place. A report that already has a PDF is never overwritten without asking.
"""
from __future__ import annotations

import csv
import http.client
import io
import ipaddress
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from difflib import SequenceMatcher

from . import VERSION, integrity, userconfig, util
from .dedup import tkey
from .parsers import DOI_RX, clean_doi
from .project import WAITING_NAME, Project, ProjectError

MAX_PDF = 100 * 1024 * 1024
MAX_JSON = 8 * 1024 * 1024           # a source's answer about one report is a few kilobytes
PDF_SECONDS = 300                    # the longest one PDF may take to arrive
UA = f"ReviewDesk/{VERSION} (systematic review screening software)"


def pdf_path(p: Project, rid: str) -> str:
    if not re.fullmatch(r"R\d{5,}", str(rid)):        # record IDs only, so no other file can ever be named
        raise ProjectError("That is not a record ID.")
    return p.path("fulltexts", f"{rid}.pdf")


def has_pdf(p: Project, rid: str) -> bool:
    return os.path.isfile(pdf_path(p, rid))


def _index_path(p: Project) -> tuple:
    name = f"index@{p.dev()}.jsonl"
    return p.path("fulltexts", name), p._local("mirror", "fulltexts__" + name)


def present(p: Project, rid: str) -> bool:
    """The report has a PDF: in the folder, or recorded as added (on another computer, not synced here yet)."""
    e = p.pdf_index().get(rid)
    return has_pdf(p, rid) or bool(e and not e.get("removed"))


def save_pdf(p: Project, user: str, rid: str, raw: bytes, how: str = "added by hand", source: str = "", auto: bool = False,
             same_as: str = "") -> str:
    """auto: found by the search, so never put in place of a PDF that is there (or has been added elsewhere)."""
    p.require_member(user)
    if rid not in p.by_id():
        raise ProjectError("That record is not in the project.")
    if raw[:5] != b"%PDF-" and b"%PDF-" not in raw[:1024]:
        raise ProjectError("That file is not a PDF.")
    if len(raw) > MAX_PDF:
        raise ProjectError("That PDF is larger than 100 MB.")
    dest = pdf_path(p, rid)
    if auto and present(p, rid):
        raise ProjectError(f"{rid} already has a PDF.")
    if os.path.isfile(dest):                      # the one it replaces is kept, never deleted
        keep = p.path("fulltexts", "_replaced")
        os.makedirs(keep, exist_ok=True)
        util._replace(dest, util.unique_path(keep, f"{rid}.pdf." + time.strftime("%Y%m%d-%H%M%S")))
    util.write_bytes_atomic(dest, raw)
    sha = util.sha256_bytes(raw)
    path, mirror = _index_path(p)
    integrity.append(path, {"t": util.now(), "r": rid, "sha": sha, "bytes": len(raw), "by": user, "how": how,
                            **({"source": source} if source else {}), **({"same_as": same_as} if same_as else {})}, mirror=mirror)
    try:                                          # this computer's own copy, to put back if the shared one is lost
        snap = p._pdf_snap(rid, sha)
        os.makedirs(os.path.dirname(snap), exist_ok=True)
        util.write_bytes_atomic(snap, raw)
    except OSError:
        pass
    p.audit(user, "attach full text", {"record": rid, "bytes": len(raw), "how": how})
    return sha


def remove_pdf(p: Project, user: str, rid: str, sha: str = "") -> None:
    """Take a wrong PDF off a record (it is moved aside, not deleted). sha: only if it is still that PDF."""
    p.require_member(user)
    dest = pdf_path(p, rid)
    if not os.path.isfile(dest):
        raise ProjectError("That record has no PDF.")
    if sha and util.sha256_file(dest) != sha:
        raise ProjectError(f"{rid} has had another PDF put on it since; nothing was taken off. Open its page to check it.")
    keep = p.path("fulltexts", "_replaced")
    os.makedirs(keep, exist_ok=True)
    util._replace(dest, util.unique_path(keep, f"{rid}.pdf." + time.strftime("%Y%m%d-%H%M%S")))
    path, mirror = _index_path(p)
    integrity.append(path, {"t": util.now(), "r": rid, "removed": True, "by": user}, mirror=mirror)
    p.audit(user, "remove full text", {"record": rid})


def links(p: Project, rec: dict) -> dict:
    prefix = p.settings_ro()["fulltext"].get("library_prefix") or ""
    doi_url = f"https://doi.org/{rec['doi']}" if rec.get("doi") else ""
    q = urllib.parse.quote(rec.get("title") or "")
    found = search_results(p).get(rec["id"], {})
    return {"doi": util.web_url(doi_url), "library": util.web_url(prefix + doi_url) if (prefix and doi_url) else "",
            "pubmed": f"https://pubmed.ncbi.nlm.nih.gov/{urllib.parse.quote(str(rec['pmid']))}/" if rec.get("pmid") else "",
            "scholar": f"https://scholar.google.com/scholar?q={q}" if q else "", "url": util.web_url(rec.get("url")),
            "found": util.web_url(found.get("url", "")) if found.get("status") == "link_only" else ""}


# ================================================================================================ open-access sources
def _base(name: str, default: str) -> str:
    """The address of a source (tests point them all at a stand-in with REVIEWDESK_OA_BASE)."""
    b = os.environ.get("REVIEWDESK_OA_BASE")
    return (b.rstrip("/") + "/" + name) if b else default


_STOP = threading.local()            # the Stop flag of the search this thread is running (Fetcher.start)


def _stopped() -> bool:
    f = getattr(_STOP, "flag", None)
    return bool(f and f())


def _check_stop() -> None:
    if _stopped():
        raise util.ReplyStopped()


def _get_json(url: str, timeout: int = 30):
    _check_stop()
    # (S310: only the search services' own https addresses are asked, or the tests' stand-in: see _base)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})  # noqa: S310
    with _OPENER.open(req, timeout=timeout) as r:
        try:
            raw = util.read_reply(r, MAX_JSON, timeout * 2, _stopped)    # one minute in all
        except util.ReplyTooBig as e:
            raise ValueError(str(e))             # handled like any other unreadable reply from a source
    return json.loads(raw.decode("utf-8", "replace"))


def unpaywall(doi: str, email: str) -> list:
    j = _get_json(_base("unpaywall", "https://api.unpaywall.org/v2") + "/" + urllib.parse.quote(doi) + "?email=" + urllib.parse.quote(email))
    locs = [(j or {}).get("best_oa_location") or {}] + list((j or {}).get("oa_locations") or [])
    out = []
    for loc in locs:
        for k in ("url_for_pdf", "url"):
            if loc.get(k) and loc[k] not in out:
                out.append(loc[k])
    return out


def europepmc(doi: str = "", pmid: str = "") -> dict:
    """{'pmcid', 'doi', 'pmid', 'urls'} for an article in Europe PMC (open-access full text when it has a PMC copy)."""
    q = f'DOI:"{doi}"' if doi else f"EXT_ID:{pmid} AND SRC:MED"
    j = _get_json(_base("europepmc", "https://www.ebi.ac.uk/europepmc/webservices/rest") + "/search?format=json&resultType=core&query="
                  + urllib.parse.quote(q))
    res = ((j or {}).get("resultList") or {}).get("result") or []
    if not res:
        return {}
    r = res[0]
    urls = []
    pmcid = r.get("pmcid") or ""
    if pmcid and (r.get("isOpenAccess") == "Y" or r.get("inEPMC") == "Y"):
        urls.append(_base("europepmc-render", "https://europepmc.org/backend/ptpmcrender.fcgi") + f"?accid={pmcid}&blobtype=pdf")
    for u in (((r.get("fullTextUrlList") or {}).get("fullTextUrl")) or []):
        if u.get("availabilityCode") in ("OA", "F") and u.get("documentStyle") == "pdf" and u.get("url"):
            urls.append(u["url"])
    return {"pmcid": pmcid, "doi": (r.get("doi") or "").lower(), "pmid": r.get("pmid") or "", "urls": urls}


def openalex(doi: str = "", title: str = "", year: str = "", email: str = "") -> dict:
    base = _base("openalex", "https://api.openalex.org")
    mail = ("&mailto=" + urllib.parse.quote(email)) if email else ""
    if doi:
        try:
            works = [_get_json(base + "/works/doi:" + urllib.parse.quote(doi) + ("?" + mail[1:] if mail else ""))]
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {}
            raise
    else:
        t = re.sub(r"[^\w\s]", " ", title)[:200]
        j = _get_json(base + "/works?per-page=5&filter=title.search:" + urllib.parse.quote(t) + mail)
        works = (j or {}).get("results") or []
        want = tkey(title)
        works = [w for w in works if SequenceMatcher(None, tkey(w.get("title") or "")[:200], want[:200]).ratio() >= 0.9
                 and _near_year(w.get("publication_year"), year)]
    if not works:
        return {}
    w = works[0]
    urls = []
    for loc in [w.get("best_oa_location") or {}] + list(w.get("locations") or []):
        if loc and loc.get("pdf_url") and loc.get("is_oa", True) and loc["pdf_url"] not in urls:
            urls.append(loc["pdf_url"])
    landing = ((w.get("best_oa_location") or {}).get("landing_page_url")) or ""
    return {"doi": clean_doi(w.get("doi") or ""), "urls": urls, "landing": landing}


def _near_year(found, year) -> bool:
    """A work's year is within one of the record's, or one of them has no year that can be read as a number."""
    a, b = re.search(r"\d{4}", str(found or "")), re.search(r"\d{4}", str(year or ""))
    return not (a and b) or abs(int(a.group(0)) - int(b.group(0))) <= 1


def semantic_scholar(doi: str) -> list:
    j = _get_json(_base("s2", "https://api.semanticscholar.org/graph/v1") + "/paper/DOI:" + urllib.parse.quote(doi)
                  + "?fields=openAccessPdf")
    u = ((j or {}).get("openAccessPdf") or {}).get("url")
    return [u] if u else []


def public_address(url: str) -> bool:
    """True if a web address leads to the public internet. Addresses come from search services, so one pointing at
    this computer or the local network (a router, a printer, an intranet page) is never fetched."""
    if os.environ.get("REVIEWDESK_OA_BASE"):            # tests point every service at a stand-in on this computer
        return True
    host = urllib.parse.urlsplit(url).hostname
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    try:
        return bool(infos) and all(ipaddress.ip_address(i[4][0].split("%")[0]).is_global for i in infos)
    except ValueError:
        return False


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to another public web address."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not util.web_url(newurl) or not public_address(newurl):
            raise urllib.error.URLError("redirect to an address that is not on the public internet")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _public_connection(address, *args, **kwargs):
    """Connect, then check the address actually reached: a name can give a public address when it is checked and a
    local one a moment later, when the connection is made."""
    sock = socket.create_connection(address, *args, **kwargs)
    if os.environ.get("REVIEWDESK_OA_BASE"):
        return sock
    try:
        public = ipaddress.ip_address(sock.getpeername()[0].split("%")[0]).is_global
    except (OSError, ValueError, IndexError):
        public = False
    if not public:
        sock.close()
        raise OSError("not an address on the public internet")
    return sock


def _guarded(cls):
    class Guarded(cls):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._create_connection = _public_connection
    return Guarded


_GUARDED = {http.client.HTTPConnection: _guarded(http.client.HTTPConnection),
            http.client.HTTPSConnection: _guarded(http.client.HTTPSConnection)}


def _guard_works() -> bool:
    """The check of the address connected to, and the way a web proxy is told apart, rest on two names inside Python's
    own http.client and urllib (present in Python 3.9 to 3.14). If a later Python drops either, the check would be
    skipped without a sound, so the search for free copies is refused instead (fetch_pdf never runs)."""
    try:
        conn = http.client.HTTPConnection("127.0.0.1")
        return (callable(getattr(conn, "_create_connection", None))
                and "_create_connection" in http.client.HTTPConnection.connect.__code__.co_names
                and "_tunnel_host" in urllib.request.Request.set_proxy.__code__.co_names)
    except Exception:                         # noqa: BLE001 - anything unexpected: refuse, never fetch unchecked
        return False


GUARD_WORKS = _guard_works()
GUARD_MISSING = ("This version of Python does not let ReviewDesk check where a download would come from, so it does not "
                 "search for free copies with it. Add the PDFs by hand (Full texts, Add PDFs), or start ReviewDesk with "
                 "Python 3.9 to 3.14.")


class _CheckConnected:
    """Check the address connected to, except when the connection is to the organisation's web proxy (set up on the
    computer), which then connects on ReviewDesk's behalf."""
    def do_open(self, http_class, req, **kw):
        if not (getattr(req, "_tunnel_host", None) or req.has_proxy()):
            http_class = _GUARDED.get(http_class, http_class)
        return super().do_open(http_class, req, **kw)


class _PublicHTTP(_CheckConnected, urllib.request.HTTPHandler):
    pass


class _PublicHTTPS(_CheckConnected, urllib.request.HTTPSHandler):
    pass


def _build_opener():
    return urllib.request.build_opener(_PublicRedirects, _PublicHTTP, _PublicHTTPS)


_OPENER = _build_opener()


def fetch_pdf(url: str) -> bytes:
    """The PDF at an address, or b'' if what comes back is not a PDF (a web page, a log-in screen). Only public web
    addresses are followed: an address from a search service could otherwise point at a file on this computer or a
    device on the local network."""
    if not GUARD_WORKS or not util.web_url(url) or not public_address(url):
        return b""
    _check_stop()
    # (S310: a public web address only, http or https: the checks just above)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 " + UA,  # noqa: S310
                                               "Accept": "application/pdf,*/*;q=0.5"})
    with _OPENER.open(req, timeout=60) as r:
        head = r.read(1024)
        if b"%PDF-" not in head:
            return b""
        try:          # at most 100 MB in all (save_pdf's limit), and five minutes; Stop is looked at between pieces
            rest = util.read_reply(r, MAX_PDF - len(head), PDF_SECONDS, _stopped)
        except util.ReplyTooBig:
            return b""
    return head + rest


def find_free_copy(rec: dict, email: str) -> dict:
    """Try each source in turn: {'status': 'saved'|'link_only'|'not_found', 'source', 'url', 'pdf': bytes, 'tried'}."""
    tried, links_only = [], []
    doi, pmid = (rec.get("doi") or "").lower(), rec.get("pmid") or ""
    cands = []                                          # (source, url)

    def add(src, urls):
        for u in urls:
            u = util.web_url(u)
            if u and u not in [c[1] for c in cands]:
                cands.append((src, u))
    steps = []
    if not doi:                                         # find the DOI first, from the PubMed ID or the title
        steps.append(("Europe PMC", lambda: europepmc(pmid=pmid) if pmid else {}))
    for name, fn in steps:
        try:
            got = fn()
            tried.append(name)
            if got.get("doi"):
                doi = got["doi"]
            add(name, got.get("urls", []))
        except util.ReplyStopped:
            raise
        except Exception:
            tried.append(name + " (no answer)")
    if not doi and not pmid and rec.get("title"):
        try:
            got = openalex(title=rec["title"], year=rec.get("year", ""), email=email)
            tried.append("OpenAlex (by title)")
            doi = got.get("doi") or ""
            add("OpenAlex", got.get("urls", []))
            if util.web_url(got.get("landing")):
                links_only.append(util.web_url(got["landing"]))
        except util.ReplyStopped:
            raise
        except Exception:
            tried.append("OpenAlex (no answer)")
    sources = []
    if doi and email:
        sources.append(("Unpaywall", lambda: unpaywall(doi, email)))
    if doi or pmid:
        sources.append(("Europe PMC", lambda: europepmc(doi=doi, pmid=pmid).get("urls", [])))
    if doi:
        sources.append(("OpenAlex", lambda: openalex(doi=doi, email=email).get("urls", [])))
        sources.append(("Semantic Scholar", lambda: semantic_scholar(doi)))
    for name, fn in sources:
        try:
            add(name, fn())
            tried.append(name)
        except util.ReplyStopped:
            raise
        except Exception:
            tried.append(name + " (no answer)")
    for src, u in cands:
        try:
            data = fetch_pdf(u)
        except util.ReplyStopped:
            raise
        except Exception:
            data = b""
        if data:
            return {"status": "saved", "source": src, "url": u, "pdf": data, "tried": tried}
        links_only.append(u)
    if links_only:
        return {"status": "link_only", "source": "", "url": links_only[0], "tried": tried}
    if tried and all("(no answer)" in t for t in tried):
        return {"status": "no_answer", "source": "", "url": "", "tried": tried}
    return {"status": "not_found", "source": "", "url": "", "tried": tried}


def search_results(p: Project) -> dict:
    """{record: the latest look-up result}, from every computer. A look-up that reached no service does not hide an
    earlier result."""
    out = {}
    for d in p._read_all(p.path("fulltexts", "search@*.jsonl"), "ftsearch"):
        if d.get("status") == "no_answer" and d.get("r") in out:
            continue
        out[d.get("r")] = d
    return out


class Fetcher:
    """One search for free copies at a time per project (on any computer: each running search leaves a note in the
    project folder), in the background; it can be stopped."""
    _runs: dict = {}
    STALE = 120                       # seconds: a note older than this is from a search that has ended

    @classmethod
    def status(cls, p: Project) -> dict:
        r = cls._runs.get(p.dir)
        if not r:
            return {"running": False}
        out = {k: v for k, v in r.items() if k != "stop"}
        out["stopped"] = bool(r.get("stop")) and not r.get("running")
        return out

    @classmethod
    def stop(cls, p: Project) -> None:
        r = cls._runs.get(p.dir)
        if r:
            r["stop"] = True
            if r.get("running"):
                r["message"] = "Stopping: the report being looked up now is left unfinished."

    @staticmethod
    def _note(p: Project) -> str:
        return p.path("fulltexts", "_running", f"search@{p.dev()}.json")

    @classmethod
    def running_elsewhere(cls, p: Project) -> str:
        """Who is running a search on another computer now ('' if nobody)."""
        d = p.path("fulltexts", "_running")
        if not os.path.isdir(d):
            return ""
        for n in os.listdir(d):
            f = os.path.join(d, n)
            if f == cls._note(p) or not n.endswith(".json"):
                continue
            try:
                note = util.read_json(f, {}) or {}
            except ValueError:
                continue
            if time.time() - util.ts(note.get("beat", "")) < cls.STALE:
                return note.get("by", "someone")
        return ""

    _start_lock = threading.Lock()

    @classmethod
    def start(cls, p: Project, user: str, ids: list) -> dict:
        p.require_member(user)
        if not GUARD_WORKS:
            raise ProjectError(GUARD_MISSING)
        email = str(userconfig.load().get("unpaywall_email") or "").strip()
        if not email or "@" not in email:
            raise ProjectError("Add your email address under My settings first: Unpaywall and OpenAlex ask for one with each "
                               "look-up.")
        st = {"running": True, "total": 0, "done": 0, "found": 0, "links": 0, "no_answer": 0, "skipped": 0,
              "started": util.now(), "stop": False, "message": ""}
        with cls._start_lock:                     # checked and recorded in one step: one search at a time
            cur = cls._runs.get(p.dir)
            if cur and cur.get("running"):
                raise ProjectError("A search for free copies is already running.")
            cls._runs[p.dir] = st
        try:
            other = cls.running_elsewhere(p)
            if other:
                raise ProjectError(f"{(p.reviewer(other) or {}).get('name') or other} is running a search for free copies on "
                                   "another computer. Wait until it has finished (the results appear here as the files sync).")
            by = p.by_id()
            todo = [i for i in ids if isinstance(i, str) and i in by and not present(p, i)]
        except BaseException:
            with cls._start_lock:
                if cls._runs.get(p.dir) is st:
                    if cur:
                        cls._runs[p.dir] = cur
                    else:
                        cls._runs.pop(p.dir, None)
            raise
        st["total"] = len(todo)
        name = f"search@{p.dev()}.jsonl"
        note = cls._note(p)

        def beat():
            try:
                os.makedirs(os.path.dirname(note), exist_ok=True)
                util.write_json_atomic(note, {"by": user, "beat": util.now(), "done": st["done"], "total": st["total"]})
            except OSError:
                pass

        def work():
            _STOP.flag = lambda: st["stop"]
            try:
                beat()
                for rid in todo:
                    if st["stop"]:
                        break
                    if present(p, rid):                  # added meanwhile (by hand, or on another computer)
                        st["done"] += 1
                        st["skipped"] += 1
                        continue
                    try:
                        res = find_free_copy(by[rid], email)
                    except util.ReplyStopped:            # Stop pressed during a look-up or a download: nothing recorded
                        break
                    if res["status"] == "saved" and pdf_owner(p, util.sha256_bytes(res["pdf"])):
                        res["status"] = "link_only"     # the same file as another report's PDF: kept as a link only
                        st["links"] += 1
                    if res["status"] == "saved":
                        try:
                            save_pdf(p, user, rid, res["pdf"], how="found automatically", source=f"{res['source']}: {res['url']}",
                                     auto=True)
                            st["found"] += 1
                        except ProjectError:
                            if present(p, rid):
                                res["status"] = "skipped"   # a PDF arrived while it was being looked up: it is kept
                                st["skipped"] += 1
                            else:                       # refused (too large, say): kept as a link for a person to open,
                                res["status"] = "link_only"     # so that later searches do not download it again
                                st["links"] += 1
                    elif res["status"] == "link_only":
                        st["links"] += 1
                    elif res["status"] == "no_answer":
                        st["no_answer"] += 1
                    if res["status"] != "skipped":
                        integrity.append(p.path("fulltexts", name), {"t": util.now(), "r": rid, "status": res["status"],
                                                                     "source": res["source"], "url": res["url"], "tried": res["tried"]},
                                         mirror=p._local("mirror", "fulltexts__" + name))
                    st["done"] += 1
                    if st["done"] >= 5 and st["no_answer"] == st["done"]:
                        st["message"] = ("The services could not be reached: check this computer is online (and not "
                                         "blocked by a firewall), then try again.")
                        break
                    beat()
                    time.sleep(0.3)
            finally:
                _STOP.flag = None
                if st["stop"] and str(st.get("message") or "").startswith("Stopping"):
                    st["message"] = ""
                st["running"] = False
                st["finished"] = util.now()
                try:
                    os.remove(note)
                except OSError:
                    pass
                p.audit(user, "search for free full texts", {"sought": len(todo), "found": st["found"], "links": st["links"],
                                                            "no_answer": st["no_answer"], "stopped": st["stop"]})

        threading.Thread(target=work, daemon=True).start()
        return cls.status(p)


# ================================================================================================ missing PDFs
FIELDS = ("title", "authors", "year", "journal", "volume", "issue", "pages", "doi", "pmid", "pubtype", "url")


def missing(p: Project, ids: list) -> list:
    """Reports at full text with no PDF yet, with what has been tried and where to look."""
    by, found = p.by_id(), search_results(p)
    return [{"id": r, **{k: by[r].get(k, "") for k in FIELDS}, "search": found.get(r, {}), "links": links(p, by[r])}
            for r in ids if r in by and not has_pdf(p, r)]


def _ris_type(pubtype: str) -> str:
    t = (pubtype or "").lower()
    return "CONF" if ("conference" in t or "proceeding" in t or "congress" in t) else "JOUR"


def missing_ris(p: Project, ids: list) -> bytes:
    """The missing reports as RIS, for any reference manager. Each carries its ReviewDesk record ID (in its notes),
    and a PDF saved with that ID in its name is placed on it exactly."""
    out = io.StringIO()
    for m in missing(p, ids):
        out.write(f"TY  - {_ris_type(m.get('pubtype'))}\n")
        out.write(f"TI  - {util.one_line(m.get('title'))}\n")
        for au in [util.one_line(a) for a in (m.get("authors") or "").split(";") if a.strip()]:
            out.write(f"AU  - {au}\n")
        for tag, k in (("PY", "year"), ("JO", "journal"), ("VL", "volume"), ("IS", "issue"), ("DO", "doi")):
            if m.get(k):
                out.write(f"{tag}  - {util.one_line(m[k])}\n")
        pages = util.one_line(m.get("pages"))
        if pages:
            sp, _, ep = pages.partition("-")
            out.write(f"SP  - {sp.strip()}\n" + (f"EP  - {ep.strip()}\n" if ep.strip() else ""))
        if m.get("doi"):
            out.write(f"UR  - https://doi.org/{util.one_line(m['doi'])}\n")
        if m.get("pmid"):
            out.write(f"AN  - PMID:{util.one_line(m['pmid'])}\n")
        out.write(f"CN  - ReviewDesk {m['id']}\nN1  - ReviewDesk record {m['id']}\nER  - \n\n")
    return out.getvalue().encode("utf-8")


SEARCH_WORDS = {"not_found": "none found", "link_only": "web page only", "no_answer": "services not reached",
                "": "not searched"}


def missing_csv(p: Project, ids: list) -> bytes:
    """The missing reports as a spreadsheet, with everything an inter-library loan request asks for."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Record", "Authors", "Year", "Title", "Journal", "Volume", "Issue", "Pages", "DOI", "PMID",
                "Free copy search", "Web page found", "Through the library"])
    for m in missing(p, ids):
        s = m["search"]
        w.writerow([util.csv_safe(x) for x in [m["id"], m.get("authors", ""), m.get("year", ""), m["title"], m.get("journal", ""),
                                              m.get("volume", ""), m.get("issue", ""), m.get("pages", ""), m.get("doi", ""),
                                              m.get("pmid", ""), SEARCH_WORDS.get(s.get("status", ""), s.get("status", "")),
                                              s.get("url", "") if s.get("status") == "link_only" else "", m["links"].get("library", "")]])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


# ================================================================================================ what is inside a PDF
_STREAM = re.compile(rb"stream\r?\n")
MAX_PDF_TEXT = 48 * 1024 * 1024      # the most text unpacked from one PDF's streams (an article's is a few megabytes)
MAX_SHOWN_SOURCE = 24 * 1024 * 1024  # the most page-drawing code read for its shown text
MAX_SHOWN_STEPS = 6_000_000          # and the most strings, brackets and escapes in it (a long book has about a million)


def pdf_text_bits(raw: bytes, limit: int = 6 * 1024 * 1024) -> str:
    """Searchable text from a PDF without any PDF library: the file itself (metadata, links) and every compressed
    stream it holds (page text in simple fonts, compressed link annotations, XMP metadata), in file order."""
    raw = raw[:limit]
    parts = [raw.decode("latin-1")]
    pos, room = 0, MAX_PDF_TEXT             # a file of pictures, or one made to, could unpack to thousands of megabytes
    while room > 0:
        m = _STREAM.search(raw, pos)
        if not m:
            break
        start = m.end()
        end = raw.find(b"endstream", start)
        if end < 0:
            break
        chunk = raw[start:end]
        try:
            text = zlib.decompressobj().decompress(chunk, min(4 * 1024 * 1024, room)).decode("latin-1")
            parts.append(text)
            room -= len(text)
        except zlib.error:
            pass
        pos = end + 9
        if len(parts) > 4000:
            break
    return "\n".join(parts)


_LIG = {0x0B: "ff", 0x0C: "fi", 0x0D: "fl", 0x0E: "ffi", 0x0F: "ffl",      # TeX fonts' ligatures (OT1 and T1)
        0x1B: "ff", 0x1C: "fi", 0x1D: "fl", 0x1E: "ffi", 0x1F: "ffl"}
_ESC = {"n": "\n", "r": "", "t": " ", "b": "", "f": ""}
# Reading these is done by stepping through the text, never by one pattern that can be made to start again at every
# character: a PDF with thousands of unfinished text objects or strings would otherwise take minutes to read.
_BT = re.compile(r"(?<![A-Za-z])BT(?![A-Za-z])")
_ET = re.compile(r"(?<![A-Za-z])ET(?![A-Za-z])")
_MARK = re.compile(r"[(<\[\]]")            # where a string, a hexadecimal string or an array starts or ends
_IN_STRING = re.compile(r"[()\\]")
_HEX_END = re.compile(r"[^0-9A-Fa-f\s]")


def _tokens(text: str, steps: list):
    """The literal strings '(…)', hexadecimal strings '<…>' and array brackets in a piece of page-drawing code, in
    order. A literal string may hold balanced brackets and escaped ones. steps[0] is how much more reading is allowed
    (counted down here): unfinished strings and brackets by the million would otherwise cost minutes."""
    pos, n, unclosed = 0, len(text), 0
    while steps[0] > 0:
        steps[0] -= 1
        m = _MARK.search(text, pos)
        if not m:
            return
        i, ch = m.start(), m.group(0)
        if ch in "[]":
            yield ch
            pos = i + 1
        elif ch == "(":
            depth, j, before = 1, i + 1, steps[0]
            while depth and steps[0] > 0:
                steps[0] -= 1
                k = _IN_STRING.search(text, j)
                if not k:
                    break                          # never closed
                c = k.group(0)
                if c == "\\":
                    j = k.start() + 2              # an escaped character, whatever it is
                    continue
                depth += 1 if c == "(" else -1
                j = k.start() + 1
            if depth or j > n:                     # not a string: read on from the next character, a few times at most
                unclosed += 1
                if before - steps[0] > 2000:       # a long way read for nothing: it counts four times over
                    steps[0] -= 4 * (before - steps[0])
                if unclosed > 20:
                    return
                pos = i + 1
                continue
            yield text[i:j]
            pos = j
        else:                                      # "<": a hexadecimal string, unless it opens a dictionary ("<<")
            k = None if text.startswith("<<", i) else _HEX_END.search(text, i + 1)
            if k and k.group(0) == ">":
                yield text[i:k.start() + 1]
                pos = k.start() + 1
            else:
                pos = i + 1


def _without_dicts(text: str) -> str:
    """The text with each dictionary '<< … >>' replaced by a space (as re.sub(r'<<.*?>>', ' ') would, in one pass)."""
    if "<<" not in text:
        return text
    out, pos = [], 0
    while True:
        a = text.find("<<", pos)
        if a < 0:
            break
        b = text.find(">>", a + 2)
        if b < 0:
            break
        out.append(text[pos:a])
        out.append(" ")
        pos = b + 2
    out.append(text[pos:])
    return "".join(out)


def _literal(s: str) -> str:
    """A PDF literal string's text: escapes undone (octal codes too), TeX ligature codes spelt out."""
    def esc(m):
        x = m.group(1)
        if x[0] in "01234567":
            return chr(int(x, 8) & 0xFF)
        if x in "\r\n":
            return ""
        return _ESC.get(x, x)
    s = re.sub(r"\\([0-7]{1,3}|.)", esc, s, flags=re.S)
    return _decoded(s.encode("latin-1", "replace"))


def _decoded(b: bytes) -> str:
    if b[:2] == b"\xfe\xff":
        try:
            return b[2:].decode("utf-16-be")
        except UnicodeDecodeError:
            pass
    return b.replace(b"\x00", b"").decode("latin-1").translate(_LIG)


def _pdf_strings(text: str) -> str:
    """Text shown in the page streams: the strings inside text objects (BT … ET), in order, so link annotations and
    other strings in the file are left out. Literal and hexadecimal strings are read; the pieces of a TJ array (a word
    or a DOI set with kerning, as publishers often do) are joined without spaces. Text in fonts with their own codes
    (most CID fonts) cannot be read this way."""
    out, size, read, pos = [], 0, 0, 0
    steps = [MAX_SHOWN_STEPS]
    while size <= 2_000_000 and read <= MAX_SHOWN_SOURCE and steps[0] > 0:
        b = _BT.search(text, pos)
        if not b:
            break
        e = _ET.search(text, b.end())
        if not e:
            break                                         # an unfinished text object: no later one can finish either
        o = text[b.end():e.start()]
        read += len(o)
        s = _strings_in(_without_dicts(o), steps)         # not marked-content properties (they repeat the text)
        out.append(s)
        size += len(s)
        pos = e.end()
    return " ".join(out)


def _strings_in(text: str, steps: list | None = None) -> str:
    out, piece, in_array = [], [], False
    for t in _tokens(text, steps if steps is not None else [MAX_SHOWN_STEPS]):
        if t == "[":
            in_array, piece = True, []
            continue
        if t == "]":
            if in_array and piece:
                out.append("".join(piece))
            in_array, piece = False, []
            continue
        if t[0] == "(":
            s = _literal(t[1:-1])
        else:
            h = re.sub(r"\s", "", t[1:-1])
            try:
                s = _decoded(bytes.fromhex(h + "0" * (len(h) % 2)))
            except ValueError:
                continue
        (piece if in_array else out).append(s)
    return " ".join(out)


def pdf_identifiers(raw: bytes) -> dict:
    """What a PDF says about itself: {'meta_doi' (from its metadata), 'dois' and 'pmids' in order of appearance (the
    shown text first, then the rest of the file, e.g. links), 'shown_dois' (those in its shown text), 'first_doi_at'
    (characters into the shown text of the first DOI; very large if it is only elsewhere), 'title' (from its metadata),
    'text' (its shown text, normalised for matching titles), 'head' (the start of its shown text)}."""
    raw_text = pdf_text_bits(raw)
    # strings elsewhere in the file (link addresses, details) may spell characters as octal codes: \057 for /
    text = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8) & 0xFF), raw_text)
    meta_doi = ""
    # every part of these patterns has a fixed limit: an open-ended one made a crafted PDF (the same few characters
    # repeated thousands of times, never closed) take time that grew with the square of its size, stalling every page
    for pat in (r"<prism:doi>\s{0,50}([^<\s]{1,300})", r"<crossmark:DOI>\s{0,50}([^<\s]{1,300})",
                r"<dc:identifier>\s{0,50}(?:doi:)?\s{0,50}(10\.[^<\s]{1,300})",
                r"/doi\s{0,50}\(([^)]{1,300})\)", r"pdfx:doi[^>]{0,200}>\s{0,50}([^<\s]{1,300})"):
        m = re.search(pat, text, flags=re.I)
        if m and clean_doi(m.group(1)):
            meta_doi = clean_doi(m.group(1))
            break
    shown = _pdf_strings(raw_text)
    plain = shown + " " + text                 # the shown text first: its order is the page order
    dois, shown_dois, first_at = [], [], 10 ** 9
    for m in DOI_RX.finditer(plain):
        d = clean_doi(m.group(0))
        if not d:
            continue
        if m.start() < len(shown) and d not in shown_dois:
            shown_dois.append(d)
        if d not in dois:
            if not dois:
                first_at = m.start() if m.start() < len(shown) else 10 ** 9
            dois.append(d)
    pmids = []
    for m in re.finditer(r"(?i)\bPMID:?\s*(\d{6,9})\b", plain):
        if m.group(1) not in pmids:
            pmids.append(m.group(1))
    title = ""
    at = text.find("<dc:title>")                       # (looked for in the 4,000 characters after it: a file with
    if at >= 0:                                        # thousands of these and no end would otherwise take minutes)
        m = re.search(r"<dc:title>.*?<rdf:li[^>]*>(.*?)</rdf:li>", text[at:at + 4000], flags=re.S)
        if m:
            title = re.sub(r"<[^>]+>", " ", m.group(1)).strip()
    if not title:
        m = re.search(r"/Title\s*\(((?:[^()\\]|\\.){4,400})\)", raw_text)
        if m:
            title = _literal(m.group(1))
    counts = []
    for k, m in enumerate(re.finditer(r"/Type\s*/Pages\b", text)):             # the page tree's count of pages
        if k >= 2000:
            break
        a, b = text.rfind("<<", max(0, m.start() - 20000), m.start()), text.find(">>", m.end(), m.end() + 20000)
        c = re.search(r"/Count\s+(\d+)", text[a:b] if a >= 0 and b > 0 else "")
        if c:
            counts.append(int(c.group(1)))
    pages = max(counts) if counts else len(re.findall(r"/Type\s*/Page(?![A-Za-z])", text))
    return {"dois": dois[:200], "shown_dois": shown_dois[:200], "meta_doi": meta_doi, "pmids": pmids[:20], "pages": pages,
            "first_doi_at": first_at, "title": " ".join(title.split())[:400], "text": tkey(shown)[:400000],
            "head": " ".join(shown.split())[:3000]}


# ================================================================================================ matching a PDF to a record
# Words that, before the paper's title, show the PDF is about that paper rather than the paper itself: a letter, a
# correction, a reply, an editorial or commentary, a summary, a supplement, or a box naming a related or key study.
ABOUT = re.compile(r"\bre\s*:|\b(?:in reply to|reply to|in response to|response to|comment on|commentary on|commentary|"
                   r"editorial|to the editor|letter to the editor|we read|read with interest|"
                   r"in this issue|this issue|colleagues|the study by|study by|correction to|correction:|erratum|"
                   r"corrigendum|retraction|expression of concern|authors?'? reply|reply|response|correction|"
                   r"correspondence(?!\s*(?:to|:|address|author))|research summary|visual abstract|graphical abstract|"
                   r"infographic|evidence summary|summary of|synopsis|"
                   r"journal club|digest|abstracted from|source\s*:|related articles?|see also|linked (?:article|paper|"
                   r"research|study)|key stud(?:y|ies)|box \d|"
                   # supplements, appendices and protocols published with a paper: their banner comes before its title
                   r"suppl[a-z]*|supporting information|supporting data|appendi(?:x|ces)|protocol|online content|"
                   r"online[- ]only|supplied by|e(?:methods|tables?|figures?|appendix|references)|"
                   # a paper's peer-review history
                   r"peer[- ]review|provisional|reviewer comments|decision letter|rebuttal)\b", re.I)
# The very next thing printed after the paper's title: a supplement or appendix labelled under the title it belongs
# to ("Supplementary material", "Online appendix"). Only a label there counts: papers mention their supplements
# ("+ Supplemental content", "…given in the Supplementary Appendix") further on.
AFTER = re.compile(r"^[\s\W\d]{0,6}(?:online\s+)?(?:suppl[a-z]*\s+(?:materials?|appendix|appendices|information|files?|"
                   r"data|methods|tables?|figures?)|supporting\s+(?:information|data)|appendi(?:x|ces)|"
                   r"e(?:methods|tables?|figures?|appendix))\b", re.I)
# A journal citation naming a supplement issue, e.g. "26(Suppl 2):145": not a supplement to a paper.
SUPPL_ISSUE = re.compile(r"\(\s*suppl[a-z]*\.?[^)]{0,12}\)|\bsuppl(?:ement)?\.?\s*\d+\s*[):,]", re.I)


def about_another(head: str) -> bool:
    """The PDF is about a paper rather than the paper itself (a letter "Re: …", a correction, a comment, a reply, a
    supplement or appendix): these print such words and then the paper's title, and often its DOI. head: the shown
    text before the paper's title."""
    return bool(ABOUT.search(SUPPL_ISSUE.sub(" ", head or "")))


def _key_index(s: str):
    keys, idx = [], []
    for i, ch in enumerate(s):
        for c in tkey(ch):
            keys.append(c)
            idx.append(i)
    return "".join(keys), idx


SURE_WITHIN = 400              # a paper's own title comes first, after at most a journal's header (characters)


def _title_at(rec_title: str, head_key, within: int = 600) -> int:
    """Where the record's title starts in the PDF's head (characters), if it is among the first things printed in it
    (its own title, not one in a reference list); -1 if not. head_key: _key_index(head)."""
    tk = tkey(rec_title)[:80]
    key, idx = head_key
    if len(tk) < 20 or not key:
        return -1
    at = key.find(tk)
    return idx[at] if 0 <= at < within else -1


def _title_near_start(rec_title: str, text: str, within: int = 600) -> bool:
    tk = tkey(rec_title)[:80]
    if len(tk) < 20 or not text:
        return False
    at = text.find(tk)
    return 0 <= at < within


def match_pdf(p: Project, raw: bytes, filename: str, candidates: list) -> dict:
    """Which report a PDF belongs to: {'record', 'how', 'sure'}, or {'record': '', 'mention': id} when it is about a
    report without being it. 'sure' only when the PDF itself says so: the record ID as its file name, its own DOI in
    its metadata, or the report's DOI (or PubMed ID) anywhere in it together with the report's title among the first
    things printed in it, and nothing before that title saying it is a letter, correction, comment or supplement
    about that paper. Anything less is a suggestion for a person to check."""
    by = p.by_id()
    cand = [r for r in candidates if r in by]
    base = os.path.basename(filename)
    m = re.fullmatch(r"(R\d{5,})(?:[ _-].*)?\.pdf", base, flags=re.I)
    if m and m.group(1).upper() in cand:
        return {"record": m.group(1).upper(), "how": "record ID in the file name", "sure": True}
    ids = pdf_identifiers(raw)
    hk = _key_index(ids["head"])

    def about(rid):
        title = by[rid].get("title", "")
        at = _title_at(title, hk)
        pre = ids["head"][:at] if at >= 0 else ids["head"][:300]
        if about_another(pre) or (at > 0 and re.search(r"[\"“”‘’'«(\[]\s*$", pre)):    # or its title quoted
            return True
        if at < 0:
            return False
        key, idx = hk                            # the end of the whole title, then what is printed right under it
        k_end = min(len(idx) - 1, key.find(tkey(title)[:80]) + len(tkey(title)) - 1)
        after = ids["head"][idx[k_end] + 1: idx[k_end] + 120] if k_end >= 0 else ""
        return bool(AFTER.search(after))

    def own_title_differs(rid):
        """The PDF's own title (in its details, and printed at its top) is not the report's: it is another paper."""
        mt = tkey(ids["title"])
        if len(mt) < 30 or tkey(ids["head"][:600]).find(mt[:60]) < 0:
            return False
        return SequenceMatcher(None, mt[:200], tkey(by[rid].get("title"))[:200]).ratio() < 0.6

    def about_it(rid, how):
        t = (by[rid].get("title") or "")[:60]
        return {"record": "", "mention": rid, "sure": False,
                "how": f"it looks like a letter, editorial, comment, summary, correction or supplement about {rid} ({t}…), "
                       f"or another paper that names it; found by {how}"}

    def short(rid, how):
        """One or two pages: a letter, reply, correction, summary or visual abstract, more often than the paper."""
        return {"record": "", "mention": rid, "sure": False,
                "how": f"it names {rid} ({how}) but has only {ids['pages']} page{'s' if ids['pages'] != 1 else ''}: check "
                       "whether it is the paper itself, or a letter, reply, correction or summary about it"}

    doi_of = {(by[r].get("doi") or "").lower(): r for r in cand if by[r].get("doi")}
    if ids["meta_doi"]:
        if ids["meta_doi"] in doi_of:
            rid = doi_of[ids["meta_doi"]]
            if about(rid):
                return about_it(rid, "the DOI in the PDF's own details")
            if 0 < ids["pages"] <= 2:
                return short(rid, "the DOI in its own details")
            return {"record": rid, "how": "the DOI in the PDF's own details", "sure": True}
        mention = next((doi_of[d] for d in ids["dois"] if d in doi_of), "")
        return {"record": "", "how": f"its own DOI ({ids['meta_doi']}) is not a report at full text"
                + (f"; it mentions {mention}" if mention else ""), "sure": False, "mention": mention}
    # the report's own DOI, the first in it, with its title among the first things printed in it. The first DOI
    # printed counts (a paper prints its own before any it cites, if only in a footer of its first page); with none
    # printed, the first in the rest of the file (a link to itself)
    first, where = "", ""
    if ids["shown_dois"]:
        first, where = ids["shown_dois"][0], ("printed" if ids["first_doi_at"] < 8000 else "far")
    elif ids["dois"]:
        first, where = ids["dois"][0], "in a link"
    pm_of = {by[r].get("pmid"): r for r in cand if by[r].get("pmid")}
    for x, of, word, pl in ((first, doi_of, "DOI", where), (ids["pmids"][0] if ids["pmids"] else "", pm_of, "PubMed ID", "printed")):
        rid = of.get(x) if x else None
        if not rid or _title_at(by[rid].get("title", ""), hk) < 0:
            continue
        how = f"its {word} ({'in a link in it' if pl == 'in a link' else 'the first printed in it'}) and its title at the start"
        if about(rid):
            return about_it(rid, how)
        if own_title_differs(rid):
            return {"record": "", "mention": rid, "sure": False,
                    "how": f"its own title ({ids['title'][:60]}…) is not that of {rid}, which it names"}
        if pl == "far":
            return {"record": rid, "how": how + ", but the DOI is a long way into it", "sure": False}
        if _title_at(by[rid].get("title", ""), hk, SURE_WITHIN) < 0:
            return {"record": rid, "how": how + ", but other text comes before the title: check it is the paper itself",
                    "sure": False}
        if 0 < ids["pages"] <= 2:
            return short(rid, f"its {word} and title")
        return {"record": rid, "how": how, "sure": True}
    note = ""
    starts = [r for r in cand if _title_at(by[r].get("title", ""), hk) >= 0]
    # two reports with the same title (an abstract and the full paper): the one still without a PDF, and not one whose
    # DOI should be in it but is not
    starts.sort(key=lambda r: (present(p, r), bool(by[r].get("doi") and by[r]["doi"].lower() not in ids["dois"])))
    if starts:                                   # its title is at the start, but its DOI is not the first in it
        rid = starts[0]
        if about(rid):
            return about_it(rid, "its title")
        d = (by[rid].get("doi") or "").lower()
        return {"record": rid, "how": "its title at the start (" + ("its DOI is in it, but not first" if d and d in ids["dois"]
                                                                    else "its DOI is not in it" if d else
                                                                    "the report has no DOI to check") + ")", "sure": False}
    if ids["dois"]:
        first = ids["dois"][0]
        if first in doi_of and first in ids["shown_dois"][:1]:
            rid = doi_of[first]
            if about(rid):
                return about_it(rid, "the DOI printed first in it")
            return {"record": rid, "how": "the DOI printed first in it (its title is not at the start)", "sure": False}
        mention = next((doi_of[d] for d in ids["dois"] if d in doi_of), "")
        if mention and first in doi_of and len(ids["text"]) < 100:
            return {"record": mention, "sure": False,
                    "how": "its DOI, the only thing that could be read in it (its text is in a form ReviewDesk cannot read)"}
        if mention:
            return {"record": "", "how": f"it is another paper that mentions {mention}", "sure": False, "mention": mention}
        note = f"the first DOI in it ({first}) is not a report at full text"
    # titles: never certain on their own (a review or an editorial can share most of a title, and file names are cut short)
    stem = re.sub(r"\.pdf$", "", base, flags=re.I)
    guesses = [g for g in (ids["title"], stem.split(" - ", 2)[-1], stem) if len(tkey(g)) >= 20]
    best, score = "", 0.0
    for r in cand:
        tk = tkey(by[r].get("title"))
        if len(tk) < 20:
            continue
        if _title_near_start(by[r].get("title", ""), ids["text"]):
            s = 0.95
        elif any(len(tkey(g)) >= 20 and tk.startswith(tkey(g)) for g in guesses[1:]):
            s = 0.9                              # a file name holding the start of the title (names are often cut short)
        else:
            s = max([SequenceMatcher(None, tkey(g)[:200], tk[:200]).ratio() for g in guesses] or [0])
        if s > score:
            best, score = r, s
    if best and score >= 0.85:
        if score == 0.95 and about(best):
            return about_it(best, "its title")
        return {"record": best, "how": f"its title ({round(100 * score)}% alike)", "sure": False}
    return {"record": "", "how": note, "sure": False}


# ================================================================================================ PDFs waiting to be placed
def _waiting_dir(p: Project) -> str:
    return p.path("fulltexts", "_unmatched")


def _still_waiting(d: str, f: str) -> bool:
    """The file is still in the waiting folder (from the folder's list, which works even where the file's own path is
    too long for Windows to open it)."""
    try:
        return os.path.basename(f) in os.listdir(d)
    except OSError:
        return os.path.isfile(f)


def hold_pdf(p: Project, user: str, raw: bytes, filename: str, why: str, suggest: str = "") -> str:
    """Keep a PDF that could not be placed with confidence until a person assigns it."""
    d = _waiting_dir(p)
    os.makedirs(d, exist_ok=True)
    base = os.path.basename(filename) or "file.pdf"
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    # a short name (the name it came with is kept in its details): see project.LONGEST_NAME
    name = util.unique_path(d, util.safe_filename(base, max_len=WAITING_NAME))
    util.write_bytes_atomic(name, raw)
    util.write_json_atomic(name + ".json", {"from": os.path.basename(filename), "by": user, "t": util.now(), "why": why,
                                            "suggest": suggest})
    return os.path.basename(name)


def waiting(p: Project) -> list:
    d = _waiting_dir(p)
    out = []
    if os.path.isdir(d):
        for n in sorted(os.listdir(d)):
            if n.lower().endswith(".pdf"):
                meta = util.read_json(os.path.join(d, n + ".json"), {}) or {}
                out.append({"name": n, **meta})
    return out


def pdf_owner(p: Project, sha: str, but: str = "") -> str:
    """The report whose PDF this is already (by its fingerprint), other than `but` ('' if none)."""
    for rid, e in p.pdf_index().items():
        if rid != but and not e.get("removed") and e.get("sha") == sha and has_pdf(p, rid):
            return rid
    return ""


def assign_waiting(p: Project, user: str, name: str, rid: str, replace: bool = False, same_ok: bool = False) -> None:
    p.require_member(user)
    d = _waiting_dir(p)
    f = os.path.join(d, os.path.basename(name))
    claim = f + f".placing-{p.dev()}"
    try:
        os.replace(f, claim)                      # taken by this computer: a second click (or tab) finds it gone
    except OSError as e:
        if not _still_waiting(d, f):
            raise ProjectError("That file is no longer waiting (someone may have placed or removed it already).") from None
        raise ProjectError(util.file_problem(e, p.dir) or f"The PDF could not be placed ({e.strerror or e}). Nothing was "
                           "changed: it is still waiting.") from e
    try:
        with open(claim, "rb") as fh:
            raw = fh.read()
        if present(p, rid) and not replace:
            raise ProjectError(f"{rid} already has a PDF. Choose Replace if this one is the right one.")
        other = pdf_owner(p, util.sha256_bytes(raw), but=rid)
        if other and not same_ok:
            raise ProjectError(f"This PDF is already the PDF of {other}. If it really holds both reports, place it again "
                               "and confirm.")
        save_pdf(p, user, rid, raw, how="placed by hand from added files", same_as=other if same_ok else "")
    except Exception:
        try:
            os.replace(claim, f)
        except OSError:
            pass
        raise
    for x in (claim, f + ".json"):               # it is now the record's PDF: the waiting copy is not needed
        try:
            os.remove(x)
        except OSError:
            pass


def discard_waiting(p: Project, user: str, name: str, audit: bool = True) -> None:
    """Take a PDF off the waiting list and out of the project folder (the person who added it still has their own
    copy)."""
    p.require_member(user)
    d = _waiting_dir(p)
    f = os.path.join(d, os.path.basename(name))
    if not _still_waiting(d, f):
        raise ProjectError("That file is no longer waiting (someone may have placed or removed it already).")
    try:
        os.remove(f)
    except FileNotFoundError:
        pass
    except OSError as e:                          # open in another program, say: it stays on the list
        raise ProjectError(util.file_problem(e, p.dir) or f"The PDF could not be removed ({e.strerror or e}). It is still "
                           "waiting.") from e
    try:
        os.remove(f + ".json")
    except OSError:
        pass
    if audit:
        p.audit(user, "remove an added PDF from the waiting list", {"file": name})


def add_many(p: Project, user: str, raw: bytes, filename: str, candidates: list) -> dict:
    """One file of a bulk addition: placed on its report (only when the PDF itself says which it is), or kept waiting
    with the reason, or refused."""
    p.require_member(user)
    if raw[:5] != b"%PDF-" and b"%PDF-" not in raw[:1024]:
        return {"file": filename, "result": "refused", "why": "not a PDF"}
    if len(raw) > MAX_PDF:
        return {"file": filename, "result": "refused", "why": "larger than 100 MB"}
    sha = util.sha256_bytes(raw)
    owner = pdf_owner(p, sha)
    if owner:
        return {"file": filename, "result": "already", "record": owner, "why": f"already the PDF of {owner}"}
    m = match_pdf(p, raw, filename, candidates)
    rid = m["record"]
    title = (p.by_id().get(rid) or {}).get("title", "")
    if rid and m["sure"] and not present(p, rid):
        sha = save_pdf(p, user, rid, raw, how="added in bulk: " + m["how"])
        return {"file": filename, "result": "placed", "record": rid, "how": m["how"], "title": title, "sha": sha}
    if not rid and not m.get("mention"):          # nothing in it points to a report: not kept in the shared folder
        return {"file": filename, "result": "refused", "why": "nothing in it matches a report at full text"
                + (f" ({m['how']})" if m["how"] else "") + ", so it was not added. If it is one, add it on that report's page."}
    if rid and present(p, rid):
        why = f"{rid} already has a PDF; this one matches it by {m['how']}"
    elif rid:
        why = f"probably {rid} (matched by {m['how']}): check it is the right paper"
    elif m["how"]:
        why = m["how"][0].upper() + m["how"][1:]
    else:
        why = "No report found for it"
    suggest = rid or ""                          # a paper it only mentions is named in the reason, not chosen for you
    name = hold_pdf(p, user, raw, filename, why, suggest=suggest)
    return {"file": filename, "result": "waiting", "record": suggest, "how": m["how"], "why": why, "name": name}
