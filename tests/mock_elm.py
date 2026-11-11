"""A stand-in for an OpenAI-compatible AI service (such as the University of Edinburgh's ELM), on this computer only.

It answers ``GET /v1/models`` with models named like ELM's University-hosted ones (their names start with the
prefixes in ``ai.LOCAL_PREFIXES``) and ``POST /v1/chat/completions`` with a reply in the form ReviewDesk reads
(``ai.parse_reply``): a JSON object with a decision, after the model's reasoning in ``<think>`` tags for the
reasoning model, as the real models reply.

Decisions are deterministic. Each call is decided, in this order, by:

* ``decide``: a function you set, called with a :class:`Call` (model, title, abstract, the whole request ...) and
  returning ``"INCLUDE"``, ``"MAYBE"``, ``"EXCLUDE"`` or ``None`` (to fall through);
* markers in the record text: ``AI:EXCLUDE``, ``AI:MAYBE`` or ``AI:INCLUDE`` anywhere in the title or abstract, or for
  one model only ``AI[Qwen]:EXCLUDE`` (the part in brackets is matched against the start of the model's name);
* ``default`` (``"INCLUDE"`` unless you change it).

Failures are switched on with :meth:`MockELM.fail` (every call, the next ``n`` calls, only one model, or only records
a function picks): ``"http500"``, ``"http400"`` (with a message naming a parameter), ``"429"`` (busy, with Retry-After),
``"timeout"`` (the answer comes after ``hang`` seconds), ``"drop"`` (the connection is closed without an answer),
``"unreadable"`` (prose with no decision in it), ``"cut"`` (the reply was cut off: finish_reason "length"),
``"think"`` (reasoning that never ends), ``"no_choices"`` and ``"not_json"`` (the body is not JSON). Every request is
counted and kept in :attr:`MockELM.calls`.

No real service or key is ever used: the stand-in listens on 127.0.0.1 only, and the key it is given is a test key.
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_A = "Qwen/Qwen3.5-397B-A17B-FP8"           # ReviewDesk's default pass A (a reasoning model)
MODEL_B = "meta-llama/Llama-3.3-70B-Instruct"    # ReviewDesk's default pass B
DEFAULT_MODELS = [MODEL_A, MODEL_B, "mistralai/Mistral-Small-4-24B-Instruct", "utter-project/EuroLLM-9B-Instruct"]
TEST_KEY = "test-key-not-a-real-key-0000"
LABELS = ("INCLUDE", "MAYBE", "EXCLUDE")
FAILURES = ("http500", "http400", "429", "timeout", "drop", "unreadable", "cut", "think", "no_choices", "not_json", "http401",
            "http404")


class Call:
    """One chat request as the stand-in saw it."""

    def __init__(self, body: dict, headers: dict):
        self.body = body
        self.headers = headers
        self.model = str(body.get("model", ""))
        msgs = body.get("messages") or []
        self.system = next((m.get("content", "") for m in msgs if m.get("role") == "system"), "")
        users = [m.get("content", "") for m in msgs if m.get("role") == "user"]
        self.user = users[0] if users else ""           # the record (a retry adds a second user message)
        self.retry = len(users) > 1
        self.title = _field(self.user, "Title")
        self.abstract = _field(self.user, "Abstract")
        m = re.search(r"Year: (.*?) \| Journal: (.*?) \| Publication type: (.*)", self.user)
        self.year, self.journal, self.pubtype = (m.group(1), m.group(2), m.group(3)) if m else ("", "", "")
        self.decision = ""                               # what was answered (or the failure)
        self.status = 200

    def text(self) -> str:
        return self.title + "\n" + self.abstract


def _field(user: str, name: str) -> str:
    m = re.search(rf"(?m)^{name}: (.*)$", user)
    return m.group(1).strip() if m else ""


class MockELM:
    def __init__(self, models: list | None = None, decide=None, default: str = "INCLUDE", key: str = TEST_KEY):
        self.models = list(models or DEFAULT_MODELS)
        self.decide = decide
        self.default = default
        self.key = key
        self.calls: list = []                 # every chat request, as a Call
        self.model_lists = 0                  # GET /models requests
        self.auth: list = []                  # every Authorization header received
        self.hang = 2.0                       # seconds a "timeout" answer takes
        self.retry_after = "3"                # what a 429 answer asks for
        self.latency = 0.0                    # seconds every answer takes
        self.reasoning = True                 # the reasoning model's reply starts with <think>...</think>
        self.refuse_json_mode = False         # answer 400 to response_format, as some services do
        self._fail: list = []                 # [{"mode", "left", "model", "when"}]
        self._lock = threading.Lock()
        self._httpd = None
        self.port = 0

    # ------------------------------------------------------------------------------------------- set-up
    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def start(self) -> "MockELM":
        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                mock._get(self)

            def do_POST(self):
                mock._post(self)

        class Server(ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                pass                                   # a connection the stand-in dropped on purpose: nothing to report

        self._httpd = Server(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    # ------------------------------------------------------------------------------------------- switches
    def fail(self, mode: str, n: int | None = None, model: str = "", when=None) -> None:
        """Make calls fail: every call (n=None) or the next n calls; only for models whose name starts with `model`;
        only for calls `when(call)` picks."""
        if mode not in FAILURES:
            raise ValueError(f"unknown failure {mode}")
        with self._lock:
            self._fail.append({"mode": mode, "left": n, "model": model, "when": when})

    def heal(self) -> None:
        """No more failures."""
        with self._lock:
            self._fail = []

    def reset(self) -> None:
        with self._lock:
            self.calls, self.auth, self.model_lists = [], [], 0

    # ------------------------------------------------------------------------------------------- counting
    def count(self, model: str = "", title: str = "") -> int:
        return sum(1 for c in self.calls if c.model.startswith(model) and (not title or c.title == title))

    def decisions(self, model: str = "") -> Counter:
        return Counter(c.decision for c in self.calls if c.model.startswith(model))

    def titles(self, model: str = "") -> set:
        return {c.title for c in self.calls if c.model.startswith(model)}

    # ------------------------------------------------------------------------------------------- answering
    def _failure_for(self, call: Call) -> str:
        with self._lock:
            for f in self._fail:
                if f["left"] is not None and f["left"] <= 0:
                    continue
                if f["model"] and not call.model.startswith(f["model"]):
                    continue
                if f["when"] is not None and not f["when"](call):
                    continue
                if f["left"] is not None:
                    f["left"] -= 1
                return f["mode"]
        return ""

    def decision_for(self, call: Call) -> str:
        if self.decide:
            d = self.decide(call)
            if d:
                return str(d).upper()
        text = call.text()
        for m in re.finditer(r"AI\[([^\]]+)\]:(INCLUDE|MAYBE|EXCLUDE)", text):
            if call.model.startswith(m.group(1)) or m.group(1).lower() in call.model.lower():
                return m.group(2)
        m = re.search(r"AI:(INCLUDE|MAYBE|EXCLUDE)", text)
        if m:
            return m.group(1)
        return self.default

    def _send(self, h, code: int, obj, extra: dict | None = None, raw: bytes | None = None) -> None:
        data = raw if raw is not None else json.dumps(obj).encode("utf-8")
        h.send_response(code)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            h.send_header(k, v)
        h.end_headers()
        h.wfile.write(data)

    def _authorised(self, h) -> bool:
        a = h.headers.get("Authorization") or ""
        with self._lock:
            self.auth.append(a)
        return a == "Bearer " + self.key

    def _get(self, h) -> None:
        if not self._authorised(h):
            return self._send(h, 401, {"error": {"message": "invalid key"}})
        if h.path.rstrip("/").endswith("/models"):
            with self._lock:
                self.model_lists += 1
            return self._send(h, 200, {"object": "list", "data": [{"id": m, "object": "model"} for m in self.models]})
        return self._send(h, 404, {"error": {"message": "not found"}})

    def _post(self, h) -> None:
        n = int(h.headers.get("Content-Length") or 0)
        raw = h.rfile.read(n) if n else b""
        if not h.path.rstrip("/").endswith("/chat/completions"):
            return self._send(h, 404, {"error": {"message": "not found"}})
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            return self._send(h, 400, {"error": {"message": "the request is not JSON"}})
        call = Call(body, dict(h.headers))
        with self._lock:
            self.calls.append(call)
        if not self._authorised(h):
            call.status, call.decision = 401, "http401"
            return self._send(h, 401, {"error": {"message": "invalid key"}})
        if call.model not in self.models:
            call.status, call.decision = 404, "http404"
            return self._send(h, 404, {"error": {"message": f"model {call.model} not found"}})
        if self.refuse_json_mode and "response_format" in body:
            call.status, call.decision = 400, "http400"
            return self._send(h, 400, {"error": {"message": "response_format is not supported for this model"}})
        if self.latency:
            time.sleep(self.latency)
        mode = self._failure_for(call)
        call.decision = mode or ""
        if mode == "http500":
            call.status = 500
            return self._send(h, 500, {"error": {"message": "internal error"}})
        if mode == "http400":
            call.status = 400
            return self._send(h, 400, {"error": {"message": "bad request: top_k is not a valid parameter"}})
        if mode == "http401":
            call.status = 401
            return self._send(h, 401, {"error": {"message": "invalid key"}})
        if mode == "http404":
            call.status = 404
            return self._send(h, 404, {"error": {"message": "no such model"}})
        if mode == "429":
            call.status = 429
            return self._send(h, 429, {"error": {"message": "rate limited"}}, {"Retry-After": self.retry_after})
        if mode == "drop":
            call.status = 0
            h.close_connection = True
            try:
                h.connection.shutdown(2)
            except OSError:
                pass
            return None
        if mode == "timeout":
            time.sleep(self.hang)
        if mode == "not_json":
            call.status = 200
            return self._send(h, 200, None, raw=b"<html>Service temporarily unavailable</html>")
        if mode == "no_choices":
            return self._send(h, 200, {"id": "x", "model": call.model, "choices": []})
        decision = self.decision_for(call)
        if not mode or mode == "timeout":
            call.decision = decision
        reason = {"INCLUDE": "every criterion appears to be met", "MAYBE": "the setting is unclear from the abstract",
                  "EXCLUDE": "not a prospective clinical evaluation"}[decision if decision in LABELS else "MAYBE"]
        answer = json.dumps({"decision": decision, "criterion": "none" if decision != "EXCLUDE" else "study design",
                             "reason": reason})
        finish = "stop"
        thinking = call.model.startswith(("Qwen/Qwen3", "mistralai/Mistral-Small-4")) and body.get("reasoning_effort") == "high"
        if mode == "unreadable":
            content = "I have read the record carefully and considered each of the criteria in turn."
        elif mode == "cut":
            content, finish = "<think>Let me go through the criteria one by one. First, the population", "length"
        elif mode == "think":
            content = "<think>Let me go through the criteria. The population is"
        elif thinking and self.reasoning:
            content = (f"<think>The record is about {call.title[:60]}. I check each criterion in turn; "
                       f"the decision is {decision}.</think>\n{answer}")
        else:
            content = answer
        return self._send(h, 200, {
            "id": f"chatcmpl-{len(self.calls)}", "object": "chat.completion", "model": call.model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": len(call.system.split()) + len(call.user.split()), "completion_tokens": 40}})
