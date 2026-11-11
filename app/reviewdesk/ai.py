"""The AI second screener.

Every title and abstract is read twice, by two language models from different developers, each with a
differently framed prompt built from the review's own eligibility criteria. A record counts as 'kept by the
AI' unless both models say EXCLUDE; a failed call or an unreadable answer counts as kept.

The AI never excludes a record. Its only use is behind single-reviewer screening: any record the single
reviewer excludes that the AI would keep is screened by the second reviewer. Its accuracy against the two
reviewers' agreement sample is reported, and if it keeps more than the stopping limit of all records the
step is dropped and both reviewers screen everything.

Calls go to the OpenAI-compatible service the lead sets under My settings (for example the University of
Edinburgh's ELM, whose University-hosted models keep data in University data centres; with ELM, models hosted
outside the University are refused). Each call sends the review question, the eligibility criteria and the
record's title, abstract, year, journal and publication type. The key is kept in the user's own settings file,
never in the project folder.
"""
from __future__ import annotations

import http.client
import json
import os
import random
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import integrity, stats, userconfig, util
from .project import Project, ProjectError

ENGINE = "ReviewDesk AI screening 1.0"
LABELS = ("INCLUDE", "MAYBE", "EXCLUDE")
RANK = {"EXCLUDE": 0, "MAYBE": 1, "INCLUDE": 2}
LOCAL_PREFIXES = ("Qwen/", "meta-llama/", "mistralai/", "utter-project/")     # ELM's University-hosted models
ELM_URL = "https://elm.edina.ac.uk/api/v1"
REASONING_MODELS = ("Qwen/Qwen3", "mistralai/Mistral-Small-4")

DEFAULT_CONFIG = {
    "base_url": "",                       # empty: the user's own setting (My settings)
    "passes": {
        "A": {"model": "Qwen/Qwen3.5-397B-A17B-FP8", "reasoning": "high", "temperature": None, "max_tokens": 16384},
        "B": {"model": "meta-llama/Llama-3.3-70B-Instruct", "reasoning": "none", "temperature": 0.0, "max_tokens": 600},
    },
    "concurrency": 4,
    "allow_closed_models": False,
}

PASS_A = """You are screening records for a systematic review at the title-and-abstract stage. You are one of two independent reviewers.

{criteria}

HOW TO DECIDE
Go through the criteria in the order given. For each one, decide whether the title and abstract show that it is met, show that it is clearly not met, or leave it unclear.
- INCLUDE: the title and abstract show, or strongly suggest, that every criterion is met.
- MAYBE: no criterion clearly fails, but at least one is unclear. This is the right answer when information is missing.
- EXCLUDE: at least one criterion clearly fails on what is written. Never exclude only because information is missing.
If you are torn between two answers, choose the more inclusive one. A study wrongly excluded is lost to the review; a study wrongly included costs a minute at full-text review.

Reply with one JSON object and nothing else:
{{"decision": "INCLUDE" or "MAYBE" or "EXCLUDE", "criterion": "the criterion that decided it, or none", "reason": "at most 20 words"}}"""

PASS_B = """You are the second, independent reviewer for a systematic review at the title-and-abstract stage. Another reviewer is screening the same records separately, and you will not see their decisions. Your job is to protect the review from missed studies: a relevant study that both reviewers exclude is lost for good.

{criteria}

HOW TO DECIDE
First ask what could connect this record to the review question. Then look for a clear, written reason to exclude it.
- EXCLUDE only when the title or abstract states something that rules the record out under the criteria above. Missing information is never a reason to exclude.
- INCLUDE when the record clearly meets the criteria.
- MAYBE in every other case.

Reply with one JSON object and nothing else:
{{"decision": "INCLUDE" or "MAYBE" or "EXCLUDE", "criterion": "the criterion that decided it, or none", "reason": "at most 20 words"}}"""

USER = """Record to screen
Title: {title}
Year: {year} | Journal: {journal} | Publication type: {pubtype}
Abstract: {abstract}"""


# ================================================================================================ prompts
def _texts(v) -> list:
    """A list of non-empty lines of text from a setting (anything else in it is left out)."""
    return [x for x in (v if isinstance(v, list) else []) if isinstance(x, str) and x.strip()]


def criteria_text(settings: dict) -> str:
    c = settings.get("criteria") if isinstance(settings.get("criteria"), dict) else {}
    q = settings.get("question") if isinstance(settings.get("question"), str) and settings.get("question") else settings.get("title")
    lines = ["REVIEW QUESTION", (q if isinstance(q, str) else "").strip(), ""]
    if _texts(c.get("include")):
        lines += ["INCLUDE (every point must be met)"] + [f"- {x}" for x in _texts(c["include"])] + [""]
    if _texts(c.get("exclude")):
        lines += ["EXCLUDE (any one point rules a record out)"] + [f"- {x}" for x in _texts(c["exclude"])] + [""]
    if _texts(c.get("rules")):
        lines += ["RULES FOR BORDERLINE CASES"] + [f"{i}. {x}" for i, x in enumerate(_texts(c["rules"]), 1)] + [""]
    return "\n".join(lines).strip()


def prompts(p: Project) -> dict:
    """The prompts in use. Once the set-up is fixed (when the agreement rule is applied) they are the fixed ones,
    so a later edit to the criteria cannot quietly void the answers the method relied on."""
    fixed = config(p).get("fixed_prompts")
    if fixed:
        return dict(fixed)
    crit = criteria_text(p.settings_ro())
    return {"A": PASS_A.format(criteria=crit), "B": PASS_B.format(criteria=crit)}


def user_msg(r: dict) -> str:
    ab = " ".join((r.get("abstract") or "").split())
    if len(ab) > 6000:
        ab = ab[:6000] + " [abstract truncated]"
    return USER.format(title=r.get("title") or "(no title)", year=r.get("year") or "not given",
                       journal=r.get("journal") or "not given", pubtype=r.get("pubtype") or "not given",
                       abstract=ab or "No abstract available.")


def record_hash(r: dict) -> str:
    return util.sha256_text(user_msg(r))[:16]


def fingerprint(system: str) -> str:
    return util.sha256_text(system + "\n----\n" + USER)[:16]


# ================================================================================================ configuration
def config(p: Project) -> dict:
    c = json.loads(json.dumps(DEFAULT_CONFIG))
    f = p.path("ai", "config.json")
    saved = json.loads(json.dumps(p._cached("ai_config", f, lambda: p._read_shared(("ai", "config.json"), {}, "ai/config.json") or {})))
    for k, v in (saved.items() if isinstance(saved, dict) else ()):
        if k == "passes":
            for ps, pv in (v.items() if isinstance(v, dict) else ()):
                if isinstance(pv, dict):
                    c["passes"].setdefault(ps, {}).update(pv)
        else:
            c[k] = v
    return _usable(c)


def _usable(c: dict) -> dict:
    """The set-up with any value of the wrong kind put back to its default. Earlier versions stored whatever was sent,
    so a project's config.json may hold, say, a model that is not text; every page that reads it must still open."""
    for ps, dflt in DEFAULT_CONFIG["passes"].items():
        pc = c["passes"].setdefault(ps, dict(dflt))
        if not isinstance(pc.get("model"), str):
            pc["model"] = ""                  # no usable model: the AI page asks for one (models_missing)
        if not isinstance(pc.get("reasoning"), str):
            pc["reasoning"] = dflt["reasoning"]
        t = pc.get("temperature")
        if t is not None and (isinstance(t, bool) or not isinstance(t, (int, float))):
            pc["temperature"] = dflt["temperature"]
        m = pc.get("max_tokens")
        if m is not None and (isinstance(m, bool) or not isinstance(m, int) or m < 1):
            pc["max_tokens"] = dflt["max_tokens"]
    n = c.get("concurrency")
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 16:
        c["concurrency"] = DEFAULT_CONFIG["concurrency"]
    if not isinstance(c.get("allow_closed_models"), bool):
        c["allow_closed_models"] = False
    fp = c.get("fixed_prompts")
    if fp is not None and not (isinstance(fp, dict) and all(isinstance(fp.get(ps), str) for ps in ("A", "B"))):
        c.pop("fixed_prompts", None)          # (then the prompts are made from the criteria, as before they were fixed)
    return c


def save_config(p: Project, user: str, changes: dict) -> dict:
    p.require_lead(user)
    c = config(p)
    if c.get("locked") and any(k in changes for k in ("passes", "fixed_prompts", "locked")):
        raise ProjectError("The AI set-up was fixed when its results were first used, so it cannot change now. "
                           "(A change is a protocol amendment: record why, report it (PRISMA 2020 item 24c), and start a new AI run.)")
    if "passes" in changes and not c.get("locked") and _sample_done(p):
        new = json.loads(json.dumps(c["passes"]))
        for ps, pv in changes["passes"].items():
            new.setdefault(ps, {}).update(pv)
        if new != c["passes"]:
            raise ProjectError("The agreement sample is finished, so the AI models and settings are now fixed: the AI is "
                               "checked against that sample with the set-up it was run with. (If the AI step cannot be "
                               "completed, drop it when you apply the sample decision; it is reported as a deviation.)")
    if changes.get("locked") and not c.get("locked"):
        changes = dict(changes)
        changes["fixed_prompts"] = prompts(p)
    for k, v in changes.items():
        if k == "passes":
            for ps, pv in v.items():
                c["passes"].setdefault(ps, {}).update(pv)
        else:
            c[k] = v
    util.write_json_atomic(p.path("ai", "config.json"), c)
    p.backup("ai", "config.json")
    p.audit(user, "change AI settings", {"keys": sorted(changes)})
    return c


def models_missing(c: dict) -> str:
    """Why the AI cannot run with this set-up ('' when both passes have a model). The same words are shown on the AI
    page and the Overview before anyone tries (readiness.py)."""
    gone = [ps for ps in ("A", "B") if not str(((c.get("passes") or {}).get(ps) or {}).get("model") or "").strip()]
    if not gone:
        return ""
    return (f"Choose the model for {' and '.join('pass ' + ps for ps in gone)} (AI second screener page, Set-up) and save "
            "the set-up: the AI cannot screen a record without one, and the method needs two models from different "
            "developers.")


def _sample_done(p: Project) -> bool:
    """True once both reviewers have finished the main agreement sample (or the sample decision has been made)."""
    sv = p.state_ro().get("sets", {}).get("main", {})
    if not sv.get("sample"):
        return False
    if sv.get("phase") == "screening":
        return True
    from .screening import agreement
    return agreement(p, "main", "sample")["complete"]


def settings_for(pass_cfg: dict) -> dict:
    model = pass_cfg["model"]
    st = {"reasoning_effort": None, "temperature": pass_cfg.get("temperature"), "top_p": None, "top_k": None,
          "max_tokens": int(pass_cfg.get("max_tokens") or 800), "json_mode": True}
    eff = pass_cfg.get("reasoning") or "none"
    if model.startswith(REASONING_MODELS):
        st["reasoning_effort"] = "high" if eff == "high" else "none"
        if eff == "high":
            st["json_mode"] = False
            st["max_tokens"] = max(st["max_tokens"], 16384)
            if st["temperature"] is None and model.startswith("Qwen/"):
                st.update({"temperature": 0.6, "top_p": 0.95, "top_k": 20})    # Qwen's recommended thinking-mode sampling
    if st["temperature"] is None and st["reasoning_effort"] != "high":
        st["temperature"] = 0.0
    return st


def sig(st: dict) -> str:
    return json.dumps({k: st[k] for k in ("reasoning_effort", "temperature", "top_p", "top_k")}, sort_keys=True)


# ================================================================================================ the endpoint
class SetupError(Exception):
    pass


class Stopped(Exception):
    """The person pressed Stop."""


class Plain(ValueError):
    """A problem with one call, already in words a reviewer can read (it becomes the answer's reason)."""


def log_detail(where: str, detail) -> str:
    """Keep the technical detail of a problem in ReviewDesk's error log (errors.jsonl in its own folder on this
    computer), never on screen. Returns the log's path, for messages that point to it."""
    path = os.path.join(userconfig.data_dir(), "errors.jsonl")
    try:
        util.append_jsonl(path, {"t": util.now(), "error": redact(f"AI service, {where}: {detail}")[:4000]})
    except Exception:                                   # noqa: BLE001, S110 - a log that cannot be written must not stop the run
        pass
    return path


def http_words(base: str, code: int) -> str:
    """What an HTTP error from the AI service means, in words (the code goes to the error log)."""
    if code in (401, 403):
        return "The AI service refused the request. Check the key under My settings."
    if code == 404:
        return (f"The AI service at {base} did not recognise the request. Check the address under My settings: use the exact "
                "address in the service's own instructions.")
    if code == 429:
        return "The AI service is busy. Try again in a few minutes."
    if code >= 500:
        return f"The AI service at {base} had a problem of its own. Try again later."
    return f"The AI service at {base} did not accept the request. Check the address and the key under My settings."


class Unreachable(RuntimeError):
    """No answer after the retries."""


def endpoint() -> tuple[str, str]:
    """The AI service's address and key. REVIEWDESK_AI_URL in the environment, if set, takes the place of the address
    saved under My settings; ELM_API_KEY is used only when no key is saved. Either way the address must pass
    address_problem (checked again in _request before the key is sent)."""
    cfg = userconfig.load()
    key = (userconfig.ai_key() or os.environ.get("ELM_API_KEY") or "").strip()
    base = str(cfg.get("elm_url") or "").strip().rstrip("/")
    if os.environ.get("REVIEWDESK_AI_URL"):
        base = os.environ["REVIEWDESK_AI_URL"].strip().rstrip("/")
    return base, key


def _host(base: str) -> tuple:
    """(scheme, host name, 'user@' part if any) of a service address; ('', '', '') if it is not an address."""
    try:
        u = urllib.parse.urlsplit((base or "").strip())
        return (u.scheme or "").lower(), (u.hostname or "").lower(), u.netloc.rpartition("@")[0] if "@" in u.netloc else ""
    except ValueError:
        return "", "", ""


def is_elm(base: str) -> bool:
    """True for the University of Edinburgh's ELM service, whose University-hosted models are the only ones allowed.
    (By the address's host name: an address that merely begins with ELM's is another service.)"""
    scheme, host, _ = _host(base)
    return scheme == "https" and host == urllib.parse.urlsplit(ELM_URL).hostname


def address_problem(base: str) -> str:
    """'' if the key may be sent to this service address; otherwise why not, in plain words. The key goes only over
    https (so it cannot be read on the way), except to this computer itself. The host is read the way the request
    will read it: 'http://localhost:1@example.org/' is example.org, not this computer, so an address with a
    user name or '@' before the host is refused outright."""
    base = (base or "").strip()
    scheme, host, user = _host(base)
    if not host or scheme not in ("http", "https") or re.search(r"[\s\\]|[\x00-\x1f]", base):
        return "That does not look like a service address. It should look like https://service.example.org/v1."
    if user or "@" in base.split("//", 1)[-1].split("/", 1)[0]:
        return ("The service address has an '@' before the service's name, which can send the key to a different place "
                "from the one it appears to name. Use the exact address in the service's own instructions.")
    if scheme == "http" and host not in ("127.0.0.1", "localhost"):
        return "The service address should start with https:// (the key would otherwise be sent unencrypted)."
    return ""


def need_endpoint() -> tuple[str, str]:
    """The service address and key, or a plain message saying what is missing."""
    base, key = endpoint()
    if not base:
        raise SetupError("No AI service address is set. Add your service's address under My settings.")
    if not key:
        raise SetupError("No AI key is set. Add your AI service's API key under My settings.")
    why = address_problem(base)
    if why:
        raise SetupError(why + (" (This address comes from REVIEWDESK_AI_URL, set on this computer: change or remove "
                                "it there.)" if os.environ.get("REVIEWDESK_AI_URL") else " Correct it under My settings."))
    return base, key


def problem_text(base: str, e: Exception) -> str:
    """Why a service could not be reached, in words: a certificate problem gets its own fix."""
    if isinstance(e, ssl.SSLCertVerificationError) or isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
        return (f"Could not check {base}'s security certificate. On a Mac, open the Python folder in Applications and "
                "double-click 'Install Certificates.command', then try again.")
    log_detail(base, e)
    return (f"ReviewDesk could not reach the AI service at {base}. Check the address under My settings, and your internet "
            "connection (or VPN).")


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Requests to the AI service carry the key, and Python passes a request's headers on when it follows a
    redirect, even to another site or to a plain http address. So a redirect is never followed."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        where = urllib.parse.urlsplit(newurl).netloc or "another address"
        raise SetupError(f"The AI service address sends requests on to {where}. ReviewDesk does not follow it, so the "
                         "key is never sent anywhere else. Check the service address under My settings: use the exact "
                         "address in the service's own instructions.")


_AI_OPENER = urllib.request.build_opener(_NoRedirects)          # (uses the computer's web proxy, if one is set)
# A service on this computer itself (the only place the key may go over plain http) is reached directly, never through
# a web proxy: the key would otherwise cross the network to the proxy unencrypted.
_AI_OPENER_HERE = urllib.request.build_opener(_NoRedirects, urllib.request.ProxyHandler({}))


MAX_REPLY = 8 * 1024 * 1024          # a model's answer or a list of models is a few kilobytes


def _request(base: str, key: str, path: str, body: dict | None = None, timeout: int = 180, whole: float = 0,
             stopped=None) -> dict:
    """One request. timeout: the longest wait for any one piece of the answer; whole: the longest the whole call may
    take, answer included (default: timeout). stopped: asked between pieces of the answer (Stop raises Stopped)."""
    why = address_problem(base)              # checked when the address is saved, and again here: the key never goes
    if why:                                  # to an address this check refuses, whatever is in the settings file
        raise SetupError(why)
    t0 = time.monotonic()
    data = json.dumps(body).encode() if body is not None else None
    # (S310: the address is one address_problem, just above, allows: https, or http to this computer itself)
    req = urllib.request.Request(base + path, data=data, method="POST" if body is not None else "GET",  # noqa: S310
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                          "User-Agent": "ReviewDesk"})
    opener = _AI_OPENER_HERE if _host(base)[0] == "http" else _AI_OPENER
    with opener.open(req, timeout=timeout) as r:
        try:
            raw = util.read_reply(r, MAX_REPLY, max(1.0, (whole or timeout) - (time.monotonic() - t0)), stopped)
        except util.ReplyStopped:
            raise Stopped() from None
        except util.ReplyTooBig as e:
            raise ValueError(f"unusable reply from the AI service: {e}")       # counted as a failed call, like any unreadable reply
    return json.loads(raw.decode("utf-8"))


def redact(s: str) -> str:
    _, key = endpoint()
    s = str(s)
    if key and len(key) > 6:
        s = s.replace(key, "[key removed]")
    return re.sub(r"(Bearer\s+)\S+|\belm-[A-Za-z0-9]{4,}-[A-Za-z0-9]{6,}", lambda m: (m.group(1) or "") + "[key removed]", s)


def list_models() -> dict:
    base, key = need_endpoint()
    try:
        r = _request(base, key, "/models", None, timeout=60)
    except urllib.error.HTTPError as e:
        log_detail(base + "/models", f"HTTP {e.code}")
        raise SetupError(http_words(base, e.code))
    except (urllib.error.URLError, socket.timeout, ssl.SSLError) as e:
        raise SetupError(problem_text(base, e))
    except http.client.RemoteDisconnected as e:    # answered nothing at all, and closed the connection
        log_detail(base + "/models", repr(e))
        raise SetupError(f"{base} closed the connection without answering. Check the service address under My settings "
                         "(use the exact address in the service's own instructions), and your internet connection (or VPN).")
    except (ValueError, http.client.HTTPException) as e:   # not JSON, not HTTP, or far larger or slower than a list of models
        log_detail(base + "/models", repr(e))
        raise SetupError(f"The address {base} answered, but not as an AI service does: the reply was not a list of models. "
                         "Check the service address under My settings: use the exact address in the service's own "
                         "instructions.")
    except OSError as e:                       # the connection failed in some other way
        raise SetupError(problem_text(base, e))
    ms = _model_ids(r)
    elm = is_elm(base)
    return {"models": ms, "endpoint": base, "elm": elm,
            "local": [m for m in ms if m.startswith(LOCAL_PREFIXES)] if elm else []}


def _model_ids(r) -> list:
    """The model names in a service's list of models (anything else in the reply is left out)."""
    data = r.get("data") if isinstance(r, dict) else None
    return sorted({str(m["id"]) for m in (data if isinstance(data, list) else []) if isinstance(m, dict)
                   and isinstance(m.get("id"), str) and m["id"].strip()})


def check_models_offered(base: str, key: str, wanted: list, stopped=None) -> None:
    """Refuse to start if the service does not offer a chosen model: every call would fail, and a failed call counts
    as 'kept', so the run would only waste time. If the service cannot list its models, carry on."""
    try:
        offered = set(_model_ids(_request(base, key, "/models", None, timeout=60, stopped=stopped)))
    except (SetupError, Stopped):          # the address sends requests elsewhere: say so before the run starts
        raise
    except Exception:                      # noqa: BLE001 - listing is optional for some services
        return
    missing = [m for m in wanted if offered and m not in offered]
    if missing:
        raise ProjectError(f"Your AI service does not offer {' or '.join(missing)}. Choose two of its models on the AI second "
                           "screener page (Check the connection lists them), then save the set-up.")


def parse_reply(text: str):
    """The decision in a reply, or None. Reasoning before </think> is ignored; conflicting decisions are read as
    the most inclusive; loose prose can never produce EXCLUDE."""
    t = text or ""
    if "</think>" in t:
        t = t.rsplit("</think>", 1)[1]
    elif "<think>" in t:
        return None
    found = []
    for m in re.finditer(r"\{[^{}]*\}", t):
        try:
            d = json.loads(m.group(0))
        except ValueError:
            continue
        if isinstance(d, dict):
            dec = str(d.get("decision", "")).strip().upper()
            if dec in LABELS:
                found.append((dec, str(d.get("criterion", ""))[:80], " ".join(str(d.get("reason", "")).split())[:240]))
    prose = [x.upper() for x in re.findall(r'decision"?\s*(?:is|was|[:=])?\s*"?\**\s*(INCLUDE|MAYBE)\b', t, flags=re.I)]
    best = max(found, key=lambda x: RANK[x[0]]) if found else None
    if prose and (best is None or RANK[max(prose, key=RANK.get)] > RANK[best[0]]):
        return (max(prose, key=RANK.get), "", "reply held more than one decision; read as the most inclusive")
    return best


BUSY = "the service said it was busy (429)"


class Caller:
    # Up to five tries per call. Between them ReviewDesk waits about 1, 2, 4 and then 8 seconds (a second or less more,
    # at random), or, when a busy service (429) says how long to wait, that long but never more than 60 seconds; so a
    # busy service is asked again for up to about four minutes. Each try may take at most CALL_SECONDS. Eight failed
    # calls in a row stop the whole run (Runner._work).
    ATTEMPTS = 5
    CALL_SECONDS = 300    # the longest one request may take, the answer included (a reasoning model's answer is slow)

    def __init__(self, base: str, key: str, model: str, system: str, st: dict, stopped=lambda: False):
        self.base, self.key, self.model, self.system, self.st = base, key, model, system, dict(st)
        self.lock = threading.Lock()
        self.adaptations: list = []
        self.stopped = stopped

    def _wait(self, sec: float) -> None:
        """Sleep, waking at once if the run is stopped."""
        end = time.time() + sec
        while time.time() < end:
            if self.stopped():
                raise Stopped()
            time.sleep(min(0.25, max(0.0, end - time.time())))

    def _body(self, messages, max_tokens):
        st = self.st
        b = {"model": self.model, "messages": messages, "max_tokens": max_tokens}
        if st["temperature"] is not None: b["temperature"] = st["temperature"]
        if st["top_p"] is not None: b["top_p"] = st["top_p"]
        if st["top_k"] is not None: b["top_k"] = st["top_k"]
        if st["json_mode"]: b["response_format"] = {"type": "json_object"}
        if st["reasoning_effort"]: b["reasoning_effort"] = st["reasoning_effort"]
        return b

    def call(self, messages, max_tokens, attempts: int = 0):
        attempts = attempts or self.ATTEMPTS
        last = ""
        for attempt in range(attempts):
            if self.stopped():
                raise Stopped()
            try:
                t0 = time.time()
                r = _request(self.base, self.key, "/chat/completions", self._body(messages, max_tokens), timeout=240,
                             whole=self.CALL_SECONDS, stopped=self.stopped)
                return r, time.time() - t0
            except urllib.error.HTTPError as e:
                msg = ""
                try:
                    msg = e.read(65536).decode("utf-8", "replace")
                except Exception:                  # noqa: BLE001, S110 - no readable message: the status code says enough
                    pass
                if e.code == 400:
                    m = msg.lower()
                    with self.lock:
                        if "reasoning" in m and self.st["reasoning_effort"]:
                            log_detail(self.model, msg)
                            raise SetupError(f"{self.model} did not accept the reasoning setting. Change this model's "
                                             "settings on the AI second screener page, then try again.")
                        if "response_format" in m and self.st["json_mode"]:
                            self.st["json_mode"] = False; self.adaptations.append("JSON mode not accepted: plain text parsed"); continue
                        if "top_k" in m and self.st["top_k"] is not None:
                            self.st["top_k"] = None; self.adaptations.append("top_k not accepted: server default used"); continue
                        if "temperature" in m and self.st["temperature"] is not None:
                            log_detail(self.model, msg)
                            raise SetupError(f"{self.model} did not accept a temperature of {self.st['temperature']}. Change "
                                             "this model's settings on the AI second screener page, then try again.")
                    log_detail(self.model, f"HTTP 400: {msg}")
                    raise Plain("the service did not accept the request")
                if e.code in (401, 403):
                    raise SetupError("The AI service refused the key. Check the key under My settings.")
                if e.code == 404:
                    raise SetupError(f"The model {self.model} was not found on this service.")
                if e.code == 429:
                    wait = None
                    try:
                        wait = float(e.headers.get("Retry-After"))
                    except (TypeError, ValueError):
                        pass
                    last = BUSY
                    if attempt < attempts - 1:
                        self._wait(min(60, wait if wait else 2 ** attempt) + random.random())
                    continue
                last = "the service returned an error"
                log_detail(self.model, f"HTTP {e.code}")
                if attempt < attempts - 1:
                    self._wait(min(30, 2 ** attempt) + random.random())
            except (urllib.error.URLError, socket.timeout, ConnectionError, TimeoutError) as e:
                last = "the service could not be reached"
                log_detail(self.base, getattr(e, "reason", e))
                if attempt < attempts - 1:
                    self._wait(min(30, 2 ** attempt) + random.random())
        raise Unreachable(f"no answer after {attempts} attempts: {last}")

    def screen(self, rec: dict, attempts: int = 0) -> dict:
        try:
            return self._screen(rec, attempts)
        except (SetupError, Stopped):
            raise
        except Exception as e:                        # fail safe: an unanswered record counts as kept
            if isinstance(e, Unreachable):
                why = f"Unreachable: {e}"
            elif isinstance(e, Plain):
                why = f"Error: {e}"
            else:                                     # the detail goes to the error log, not into the answer
                log_detail(self.model, f"{type(e).__name__}: {e}")
                why = "Error: the reply could not be read"
            return {"decision": "ERROR", "criterion": "", "reason": redact(why)[:240], "latency": 0}

    def _screen(self, rec: dict, attempts: int = 0) -> dict:
        base_msgs = [{"role": "system", "content": self.system}, {"role": "user", "content": user_msg(rec)}]
        messages, budget = base_msgs, self.st["max_tokens"]
        usage, lat, raw, cut, served = Counter(), 0.0, "", False, ""
        for turn in range(2):
            r, sec = self.call(messages, budget, attempts)
            lat += sec
            ch = (r.get("choices") or [None])[0]
            if not ch:
                raise Plain("the reply had no answer in it")
            served = r.get("model") or served
            u = r.get("usage") or {}
            usage["prompt"] += u.get("prompt_tokens", 0) or 0
            usage["completion"] += u.get("completion_tokens", 0) or 0
            raw = ((ch.get("message") or {}).get("content") or "")
            cut = ch.get("finish_reason") == "length"
            got = None if cut else parse_reply(raw)
            if got:
                return {"decision": got[0], "criterion": got[1], "reason": got[2], "usage": dict(usage),
                        "served": served, "latency": round(lat, 2), "retried": turn > 0}
            if cut:
                messages, budget = base_msgs, min(budget * 2, 32768)
            else:
                messages = base_msgs + [{"role": "assistant", "content": raw[-2000:]},
                                        {"role": "user", "content": "Reply with the JSON object only, in the format asked for."}]
        return {"decision": "UNPARSED", "criterion": "", "reason": "reply was cut off" if cut else "reply could not be read",
                "usage": dict(usage), "served": served, "latency": round(lat, 2), "raw": redact(raw[-400:])}


# ================================================================================================ answers on disk
def _cache_path(p: Project, ps: str) -> str:
    return p.path("ai", f"answers_{ps}@{p.dev()}.jsonl")


def current_keys(p: Project) -> dict:
    """The (model, prompt fingerprint, settings) that answers must carry to count, per pass."""
    c = config(p)
    pr = prompts(p)
    return {ps: (c["passes"][ps]["model"], fingerprint(pr[ps]), sig(settings_for(c["passes"][ps]))) for ps in ("A", "B")}


def answers(p: Project) -> dict:
    """{pass: {record: answer}} for answers made with the current set-up and the current text of each record.
    Shared: do not change the result."""
    return p.memo("ai_answers", lambda: _answers(p))


def _answers(p: Project) -> dict:
    keys = current_keys(p)
    by = p.by_id()
    rh = {}
    out = {}
    for ps in ("A", "B"):
        rows = p._read_all(p.path("ai", f"answers_{ps}*.jsonl"), "ai" + ps)
        cur = {}
        model, fp, sg = keys[ps]
        for d in rows:
            if d.get("model") == model and d.get("prompt") == fp and d.get("settings") == sg and d.get("decision") in LABELS + ("ERROR", "UNPARSED"):
                rec = by.get(d.get("id"))
                if not rec:
                    continue
                if d["id"] not in rh:
                    rh[d["id"]] = record_hash(rec)
                if d.get("rh") != rh[d["id"]]:
                    continue
                if d["decision"] in LABELS or d["id"] not in cur:
                    cur[d["id"]] = d
        out[ps] = cur
    return out


def combined(p: Project) -> dict:
    """{record: 'keep' | 'exclude'} for records both passes have answered (failed answers count as keep).
    Shared: do not change the result."""
    def work():
        a = answers(p)
        return {rid: "exclude" if a["A"][rid]["decision"] == "EXCLUDE" and a["B"][rid]["decision"] == "EXCLUDE" else "keep"
                for rid in set(a["A"]) & set(a["B"])}
    return p.memo("ai_combined", work)


def coverage(p: Project, set_name: str | None = None) -> dict:
    """done: both models answered. failed: a call was made but no answer could be read (counts as kept; a new
    run tries these again). missing: not yet sent to one or both models."""
    return dict(p.memo(("ai_coverage", set_name), lambda: _coverage(p, set_name)))


def _coverage(p: Project, set_name: str | None) -> dict:
    a = answers(p)
    recs = [r for r in p.records() if set_name is None or r["set"] == set_name]
    ok = lambda ps, rid: rid in a[ps] and a[ps][rid]["decision"] in LABELS
    tried = lambda rid: rid in a["A"] and rid in a["B"]
    done = sum(1 for r in recs if ok("A", r["id"]) and ok("B", r["id"]))
    answered = sum(1 for r in recs if tried(r["id"]))
    return {"records": len(recs), "done": done, "failed": answered - done, "missing": len(recs) - answered}


# ================================================================================================ running
class Runner:
    """One AI run at a time per project, in a background thread."""
    _runs: dict = {}

    @classmethod
    def status(cls, p: Project) -> dict:
        r = cls._runs.get(p.dir)
        if r:
            return dict(r["status"])
        last = util.read_json(p._local("ai_run.json"), {}) or {}
        if last.get("running"):          # ReviewDesk closed (or the computer slept) while a run was going
            return {"running": False, "interrupted": True, "label": last.get("label", ""), "started": last.get("started", ""),
                    "message": "The last AI run was interrupted (ReviewDesk was closed or the computer went to sleep). The "
                               "answers it got are saved; run it again to finish the rest."}
        return {"running": False}

    @classmethod
    def _remember(cls, p: Project, status: dict) -> None:
        try:
            os.makedirs(os.path.dirname(p._local("ai_run.json")), exist_ok=True)
            util.write_json_atomic(p._local("ai_run.json"), {k: status.get(k) for k in ("running", "label", "started", "finished")})
        except OSError:
            pass

    STOPPING = "Stopping: the calls already under way finish first (this can take a few minutes)."

    @classmethod
    def stop(cls, p: Project) -> None:
        r = cls._runs.get(p.dir)
        if r:
            r["stop"] = True
            if r["status"].get("running") and not str(r["status"].get("message") or "").startswith("Stopped"):
                r["status"]["message"] = cls.STOPPING

    @staticmethod
    def _say(run: dict, message: str) -> None:
        """Show how the run is going, unless Stop has been pressed (then 'Stopping…' stays until it has stopped)."""
        if not run["stop"]:
            run["status"]["message"] = message

    _start_lock = threading.Lock()

    @classmethod
    def start(cls, p: Project, user: str, ids: list, label: str) -> dict:
        p.require_lead(user)
        # The run is recorded before anything slow happens (asking the service for its models can take a minute), and
        # the check and the record are one step: a second click, or a second tab, can never start a second run.
        with cls._start_lock:
            cur = cls._runs.get(p.dir)
            if cur and cur["status"].get("running"):
                raise ProjectError("An AI run is already going.")
            run = {"stop": False, "status": {"running": True, "label": label, "total": 0, "done": 0, "errors": 0,
                                             "started": util.now(), "message": "Starting", "user": user}}
            cls._runs[p.dir] = run
        try:
            try:
                base, key = need_endpoint()
            except SetupError as e:
                raise ProjectError(str(e))
            c = config(p)
            if models_missing(c):                  # every call would fail, and a failed call counts as kept
                raise ProjectError(models_missing(c))
            for ps in ("A", "B"):
                m = c["passes"][ps]["model"]
                if is_elm(base) and not m.startswith(LOCAL_PREFIXES) and not c.get("allow_closed_models"):
                    raise ProjectError(f"{m} is not a University-hosted model: it would send records outside the University "
                                       "and use the paid budget. Choose a University-hosted model (AI second screener page, "
                                       "Models).")
            if not is_elm(base):
                try:
                    check_models_offered(base, key, [c["passes"]["A"]["model"], c["passes"]["B"]["model"]],
                                         stopped=lambda: run["stop"])
                except Stopped:
                    raise ProjectError("The AI run was stopped before it started. Nothing was sent.") from None
            if run["stop"]:
                raise ProjectError("The AI run was stopped before it started. Nothing was sent.")
        except BaseException:
            with cls._start_lock:                  # not started: the page shows the last run again, as before
                if cls._runs.get(p.dir) is run:
                    if cur:
                        cls._runs[p.dir] = cur
                    else:
                        cls._runs.pop(p.dir, None)
            raise
        cls._remember(p, run["status"])
        t = threading.Thread(target=cls._work, args=(p, user, ids, label, run, base, key, c), daemon=True)
        t.start()
        return run["status"]

    @classmethod
    def _work(cls, p, user, ids, label, run, base, key, c):
        S = run["status"]
        logged = False
        files: dict = {}
        try:
            pr = prompts(p)
            by = p.by_id()
            recs = [by[i] for i in ids if i in by]
            keys = current_keys(p)
            have = answers(p)
            os.makedirs(p.path("ai", "prompts"), exist_ok=True)
            callers = {}
            for ps in ("A", "B"):
                st = settings_for(c["passes"][ps])
                util.write_text_atomic(p.path("ai", "prompts", f"pass_{ps}_{keys[ps][1]}.txt"),
                                       pr[ps] + "\n\n---- user message template ----\n" + USER + "\n")
                callers[ps] = Caller(base, key, c["passes"][ps]["model"], pr[ps], st, stopped=lambda: run["stop"])
            todo = [(ps, r) for r in recs for ps in ("A", "B")
                    if not (r["id"] in have[ps] and have[ps][r["id"]]["decision"] in LABELS)]
            S.update({"total": len(todo), "done": 0})
            cls._say(run, f"{util.plural(len(todo), 'call')} to make")
            # what this run actually sends: records with no answer yet, and calls that try a failed answer again
            # (records both models had already answered are checked but not sent)
            p.audit(user, "AI run started", {"label": label, "records_checked": len(recs),
                                             "records_sent": len({r["id"] for _, r in todo}), "calls": len(todo),
                                             "retries": sum(1 for ps, r in todo if r["id"] in have[ps]),
                                             "models": {ps: c["passes"][ps]["model"] for ps in "AB"},
                                             "prompts": {ps: keys[ps][1] for ps in "AB"}, "endpoint": base})
            logged = True
            lock = threading.Lock()
            for ps in ("A", "B"):
                util.ensure_newline(_cache_path(p, ps))
            for ps in ("A", "B"):              # closed whatever happens (the finally below)
                files[ps] = open(_cache_path(p, ps), "a", encoding="utf-8")
            chain = {ps: integrity.last_hash(_cache_path(p, ps)) for ps in ("A", "B")}    # each answer is fingerprinted
            streak = [0]

            def save(ps, r, res):
                res.update({"id": r["id"], "rh": record_hash(r), "pass": ps, "model": keys[ps][0], "prompt": keys[ps][1],
                            "settings": keys[ps][2], "when": util.now(), "engine": ENGINE})
                res["t"] = res["when"]
                with lock:
                    res.pop(integrity.H, None)
                    res[integrity.H] = integrity.row_hash(chain[ps], res)
                    res[integrity.K] = integrity.sign(res)
                    chain[ps] = res[integrity.H]
                    files[ps].write(json.dumps(res, ensure_ascii=False) + "\n")
                    files[ps].flush()
                    os.fsync(files[ps].fileno())

            # one test call per pass first, so a wrong key, a wrong model or no connection stops the run at once
            for ps in ("A", "B"):
                first = next((r for q, r in todo if q == ps), None)
                if first is None:
                    continue
                cls._say(run, f"Testing pass {ps} ({c['passes'][ps]['model']})")
                try:
                    res = callers[ps].screen(first, attempts=2)
                except Stopped:
                    break
                if res["decision"] == "ERROR":
                    why = res["reason"].split(": ", 1)[-1]
                    if res["reason"].startswith("Unreachable") and res["reason"].endswith(BUSY):
                        raise SetupError("The AI service is busy: it asked ReviewDesk to wait and try again later. Try again "
                                         "in a few minutes. Nothing else was sent.")
                    if res["reason"].startswith("Unreachable"):
                        raise SetupError(f"ReviewDesk could not reach the AI service at {base}. Check the address under My "
                                         "settings, and your internet connection (or VPN), then try again. Nothing else was "
                                         "sent.")
                    raise SetupError(f"Pass {ps} ({c['passes'][ps]['model']}): the first request to the AI service failed "
                                     f"({why}). Check this model and its settings on the AI second screener page, then try "
                                     "again.")
                save(ps, first, res)
                S["done"] += 1
                if res["decision"] in ("ERROR", "UNPARSED"):          # an unreadable answer to the test call counts too
                    S["errors"] += 1
                todo = [(q, r) for q, r in todo if not (q == ps and r is first)]
            cls._say(run, "Running")

            def work(ps, r):
                if run["stop"]:
                    return
                try:
                    try:
                        res = callers[ps].screen(r)
                    except Stopped:
                        return
                    save(ps, r, res)
                except BaseException:
                    # The service refused the key or the model, or an answer could not be saved: no further call is
                    # made (each would fail the same way, or be paid for and lost). The calls queued see the flag.
                    run["stop"] = True
                    raise
                with lock:
                    S["done"] += 1
                    if res["decision"] in ("ERROR", "UNPARSED"):
                        S["errors"] += 1
                    streak[0] = streak[0] + 1 if res["decision"] == "ERROR" else 0
                    if streak[0] >= 8 and not run["stop"]:
                        run["stop"] = True
                        log_detail(base, f"8 calls in a row failed; last: {res['reason']}")
                        S["message"] = ("Stopped: the AI service stopped answering (8 calls in a row failed). The answers so "
                                        "far are saved; run it again later to finish.")

            with ThreadPoolExecutor(max_workers=max(1, int(c.get("concurrency") or 4))) as ex:
                futs = [ex.submit(work, ps, r) for ps, r in todo]
                for f in as_completed(futs):
                    f.result()
            S["message"] = "Stopped" if run["stop"] and "Stopped" not in S.get("message", "") else S.get("message") if run["stop"] else "Finished"
            p.audit(user, "AI run ended", {"label": label, "done": S["done"], "errors": S["errors"], "stopped": run["stop"],
                                           "adaptations": {ps: callers[ps].adaptations for ps in "AB"}})
        except SetupError as e:
            S["message"] = str(e)
            S["error"] = True
        except Exception as e:                                     # never leave the run looking alive
            import traceback
            path = log_detail(base, traceback.format_exc())
            S["message"] = (f"The AI run stopped unexpectedly ({type(e).__name__}). The answers it got are saved; run it "
                            f"again to finish. The details are in the error log ({path}).")
            S["error"] = True
        finally:
            for fh in files.values():
                try:
                    fh.close()
                except OSError:
                    pass
            if logged and S.get("error"):                          # the run started but did not finish: say so
                try:
                    p.audit(user, "AI run ended", {"label": label, "done": S["done"], "errors": S["errors"], "stopped": True,
                                                   "problem": redact(S["message"])[:300]})
                except Exception:                                  # noqa: BLE001, S110 - the run's own message says what happened
                    pass
            S["running"] = False
            S["finished"] = util.now()
            cls._remember(p, S)


# ================================================================================================ checking the AI
def reference_sample(p: Project, set_name: str = "main") -> dict:
    """The two reviewers' outcome on the agreement sample: True if either sent the record forward."""
    from .screening import FWD, latest, votes
    st = p.state_ro().get("sets", {}).get(set_name, {})
    sample = st.get("sample", [])
    from .screening import plan as _plan
    a, b = (_plan(p, set_name)["reviewers"] + ["", ""])[:2]
    V = votes(p)
    out = {}
    for r in sample:
        va, vb = latest(V, "ta", r, a), latest(V, "ta", r, b)
        if va and vb:
            out[r] = va["d"] in FWD or vb["d"] in FWD
    return out


def validate(p: Project, set_name: str = "main") -> dict:
    """The AI's accuracy against the agreement sample, over the sample records both models answered (a failed or
    missing answer is left out of the figures; in screening it counts as kept)."""
    a = answers(p)
    comb = combined(p)
    ref = reference_sample(p, set_name)
    recs = [r["id"] for r in p.records() if r["set"] == set_name]
    ok = lambda ps, r: a[ps].get(r, {}).get("decision") in LABELS
    answered = [r for r in ref if ok("A", r) and ok("B", r)]
    pairs = [(ref[r], comb.get(r, "keep") == "keep") for r in answered]
    out = {"reference": "the two reviewers' own votes on the agreement sample (a record counts as sent on if either "
                        "reviewer voted Include or Maybe, whatever was decided for it afterwards)",
           "combined": stats.accuracy(pairs), "sample_records": len(ref), "sample_answered": len(answered)}
    if len(answered) < len(ref):
        k = len(ref) - len(answered)
        out["coverage_note"] = (f"{k:,} of the {len(ref):,} sample records {'has' if k == 1 else 'have'} no readable answer from "
                                f"both models and {'is' if k == 1 else 'are'} left out of these figures (in screening "
                                f"{'it counts' if k == 1 else 'they count'} as kept).")
    for ps in ("A", "B"):
        pp = [(ref[r], a[ps][r]["decision"] != "EXCLUDE") for r in ref if ok(ps, r)]
        out[ps] = stats.accuracy(pp)
    out["failed"] = sum(1 for r in recs if (r in a["A"] and not ok("A", r)) or (r in a["B"] and not ok("B", r)))
    sent = [r for r in recs if r in comb]          # answered by both models (a failed answer counts as kept)
    kept = sum(1 for r in sent if comb[r] == "keep")
    out["keep_rate"] = kept / len(sent) if sent else None
    out["kept"], out["records"], out["not_sent"] = kept, len(sent), len(recs) - len(sent)
    both = [r for r in recs if r in a["A"] and r in a["B"]]
    out["passes_disagree"] = sum(1 for r in both if (a["A"][r]["decision"] == "EXCLUDE") != (a["B"][r]["decision"] == "EXCLUDE"))
    out["passes_compared"] = len(both)
    c = config(p)
    keys = current_keys(p)
    out["models"] = {ps: c["passes"][ps]["model"] for ps in "AB"}
    out["served"] = {ps: dict(Counter(x.get("served", "") for x in a[ps].values())) for ps in "AB"}
    out["prompts"] = {ps: keys[ps][1] for ps in "AB"}
    out["settings"] = {ps: settings_for(c["passes"][ps]) for ps in "AB"}
    out["missed"] = [r for r in ref if ref[r] and comb.get(r, "keep") == "exclude"]
    return out


def visible(p: Project, user: str) -> bool:
    """Per-record AI answers are shown only to the lead, and only once the agreement sample is finished, so that
    nobody's screening is influenced by them."""
    if not p.is_lead(user):
        return False
    from .screening import agreement
    st = p.state_ro().get("sets", {}).get("main", {})
    if not st.get("sample"):
        return st.get("phase") == "screening"
    return agreement(p, "main", "sample")["complete"]


def known_check(p: Project) -> list:
    """Which of the project's known eligible studies are in the records, and (if run) what the AI said."""
    by = p.by_id()
    from .dedup import tkey
    idx = {}
    for r in by.values():
        if r.get("pmid"): idx.setdefault("p" + r["pmid"], r["id"])
        if r.get("doi"): idx.setdefault("d" + r["doi"].lower(), r["id"])
        idx.setdefault("t" + tkey(r["title"])[:100], r["id"])
    a = answers(p)
    out = []
    ks = p.settings_ro().get("known_studies")
    for k in [k for k in (ks if isinstance(ks, list) else []) if isinstance(k, dict)]:
        k = {f: str(k[f]) for f in ("doi", "pmid", "title") if isinstance(k.get(f), (str, int)) and str(k[f]).strip()}
        hit = idx.get("p" + str(k.get("pmid", ""))) if k.get("pmid") else None
        hit = hit or (idx.get("d" + (k.get("doi") or "").lower()) if k.get("doi") else None)
        hit = hit or (idx.get("t" + tkey(k.get("title"))[:100]) if k.get("title") else None)
        row = {"known": k, "record": hit}
        if hit:
            row["A"] = a["A"].get(hit, {}).get("decision")
            row["B"] = a["B"].get(hit, {}).get("decision")
            row["reasons"] = {ps: a[ps].get(hit, {}).get("reason", "") for ps in "AB"}
        out.append(row)
    return out
