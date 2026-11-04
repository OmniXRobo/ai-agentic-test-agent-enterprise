"""AI gateway: On-Premise AI or Cloud AI behind one OpenAI-compatible interface.

QA Application -> AI Gateway -> On-Premise LLM server (OpenAI-compatible API, e.g.
llama.cpp ``llama-server`` with a Qwen GGUF model, or Ollama) -> local model, or
-> a Cloud AI endpoint that speaks the same chat-completions API (no provider SDK,
so the platform is not tied to one vendor).

* On-Premise AI never downloads models and never calls the Internet: requests
  ignore proxy environment variables and go only to the configured base URL.
* Cloud AI sends the API key (kept in the Windows Credential Manager, never in
  settings) as a Bearer token and honours the PC's proxy settings.
* Structured output: ``complete_json`` validates the reply against a pydantic
  model and asks the model once to repair invalid output. There is no
  non-AI fallback that would produce made-up content.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..config import Settings
from ..credentials import SecretStore
from ..errors import QAError
from ..events import names
from ..events.bus import EventBus, QAEvent
from ..logging_setup import get_logger

log = get_logger("ai")

T = TypeVar("T", bound=BaseModel)

AI_SECRET_NAMESPACE = "ai"
CLOUD_KEY = "cloud_api_key"
MODES = {"on_premise": "On-Premise AI", "cloud": "Cloud AI"}

_sleep = time.sleep  # the retry wait (tests replace it)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """Parse JSON from a model reply that may include fences or prose around it."""
    text = text.strip()
    # Reasoning models (e.g. Qwen3) may emit a <think> block first.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise ValueError("No JSON found in the model reply.")
    start = min(starts)
    closer = "}" if text[start] == "{" else "]"
    end = text.rfind(closer)
    if end <= start:
        raise ValueError("Unterminated JSON in the model reply.")
    return json.loads(text[start:end + 1])


_CONTEXT_WORDS = re.compile(r"exceed_context_size|exceeds? the available context|context (?:length|size|window)|"
                            r"maximum context|too many tokens|prompt is too long|n_ctx|context overflow|"
                            r"keep the first \d+ tokens", re.I)


def _error_detail(resp: httpx.Response) -> str:
    """The server's own error text from an OpenAI-compatible error body (llama.cpp, vLLM, LM Studio, Ollama)."""
    try:
        data = resp.json()
    except ValueError:
        return re.sub(r"\s+", " ", resp.text or "").strip()[:300]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        msg = err.get("message") or err.get("type") or ""
    elif isinstance(err, str):
        msg = err
    elif isinstance(data, dict):
        msg = data.get("message") or data.get("detail") or ""
    else:
        msg = ""
    if not isinstance(msg, str):
        msg = json.dumps(msg)[:300]
    return re.sub(r"\s+", " ", msg).strip()[:300]


def _is_context_error(detail: str) -> bool:
    return bool(detail and _CONTEXT_WORDS.search(detail))


def _context_numbers(resp: httpx.Response, detail: str) -> tuple[int, int | None]:
    """(context size, prompt tokens) from the error body when the server reports them, else 0 / None."""
    n_ctx, used = 0, None
    try:
        err = resp.json().get("error")
        if isinstance(err, dict):
            n_ctx = int(err.get("n_ctx") or 0)
            used = int(err["n_prompt_tokens"]) if err.get("n_prompt_tokens") else None
    except (ValueError, AttributeError, TypeError):
        pass
    if not n_ctx:
        m = re.search(r"(?:maximum context length|context (?:length|size|window))(?: is| of)?\s*(\d{3,7})", detail, re.I)
        n_ctx = int(m.group(1)) if m else 0
    if used is None:
        m = re.search(r"(\d{2,7}) tokens in the messages|prompt (?:has|contains|is) (\d{2,7}) tokens|"
                      r"\((\d{2,7}) in the messages", detail, re.I)
        if m:
            used = int(next(g for g in m.groups() if g))
    return n_ctx, used


def _compact_schema(obj: Any) -> Any:
    """The JSON schema without its generated "title" entries: the same rules in fewer prompt tokens."""
    if isinstance(obj, dict):
        return {k: _compact_schema(v) for k, v in obj.items() if not (k == "title" and isinstance(v, str))}
    if isinstance(obj, list):
        return [_compact_schema(v) for v in obj]
    return obj


class AIGateway:
    def __init__(self, settings: Settings, bus: EventBus, transport: httpx.BaseTransport | None = None,
                 secrets: SecretStore | None = None) -> None:
        self.settings = settings
        self.bus = bus
        self.transport = transport
        self.secrets = secrets
        self._cv = threading.Condition()
        self._active = 0
        self._slots = 0
        self._shared_ctx_overflow = False
        self._last_unavailable = 0.0
        self._ctx_key: tuple[str, str, str] | None = None
        self._n_ctx = 0
        self._ctx_probed: float | None = None

    def mode(self) -> str:
        mode = self.settings.section("ai").get("mode") or "on_premise"
        return mode if mode in MODES else "on_premise"

    def label(self) -> str:
        return MODES[self.mode()]

    def has_api_key(self) -> bool:
        return bool(self.secrets and self.secrets.available and self.secrets.has(AI_SECRET_NAMESPACE, CLOUD_KEY))

    def config(self) -> dict[str, Any]:
        ai = self.settings.section("ai")
        cloud = ai.get("cloud") or {}
        mode = self.mode()
        active = cloud if mode == "cloud" else ai
        return {
            "mode": mode,
            "label": MODES[mode],
            "base_url": (active.get("base_url") or "").rstrip("/"),
            "model": active.get("model") or "",
            "timeout_seconds": float(ai.get("timeout_seconds") or 120),
            # Test case and test data generation write long answers; a local CPU model needs minutes per call.
            "generation_timeout_seconds": float(ai.get("generation_timeout_seconds") or 600),
            "temperature": float(ai.get("temperature", 0.2)),
            "max_tokens": int(ai.get("max_tokens", 4096)),
            "on_premise": {"base_url": (ai.get("base_url") or "").rstrip("/"), "model": ai.get("model") or ""},
            "cloud": {"base_url": (cloud.get("base_url") or "").rstrip("/"), "model": cloud.get("model") or "",
                      "has_api_key": self.has_api_key()},
        }

    def set_api_key(self, value: str | None) -> None:
        if self.secrets is None or not self.secrets.available:
            raise QAError("CREDENTIAL_STORE_UNAVAILABLE", "The Windows Credential Manager is not available.", status=503)
        if value:
            self.secrets.set(AI_SECRET_NAMESPACE, CLOUD_KEY, value)
        else:
            self.secrets.delete(AI_SECRET_NAMESPACE, CLOUD_KEY)

    def _client(self, timeout: float) -> httpx.Client:
        cfg = self.config()
        if not cfg["base_url"]:
            raise QAError("AI_NOT_CONFIGURED", f"No {cfg['label']} server URL is configured.", status=409)
        headers = {}
        if cfg["mode"] == "cloud":
            key = self.secrets.get(AI_SECRET_NAMESPACE, CLOUD_KEY) if self.has_api_key() else None
            if not key:
                raise QAError("AI_NOT_CONFIGURED", "No Cloud AI API key is saved.", status=409)
            headers["Authorization"] = f"Bearer {key}"
        return httpx.Client(base_url=cfg["base_url"], timeout=timeout, trust_env=cfg["mode"] == "cloud",
                            transport=self.transport, headers=headers)

    def _unavailable(self, reason: str) -> None:
        now = time.monotonic()
        if now - self._last_unavailable < 300:
            return
        self._last_unavailable = now
        self.bus.publish(QAEvent(name=names.AI_UNAVAILABLE, activity=reason, source="ai.gateway"))

    # ---- raw calls -------------------------------------------------------------

    def models(self, timeout: float = 10.0) -> list[str]:
        with self._client(timeout) as c:
            try:
                resp = c.get("/models")
            except httpx.HTTPError as exc:
                raise QAError("AI_UNREACHABLE", f"{self.label()} server not reachable: {type(exc).__name__}.",
                              status=502, details=str(exc)) from exc
        if resp.status_code != 200:
            raise QAError("AI_REQUEST_FAILED", f"/models returned HTTP {resp.status_code}.", status=502)
        try:
            return [m.get("id") for m in resp.json().get("data", []) if m.get("id")]
        except ValueError as exc:
            raise QAError("AI_INVALID_RESPONSE", "The AI server returned a non-JSON model list.", status=502) from exc

    # ---- context window ----------------------------------------------------------------------------------

    def context_window(self) -> int:
        """The model's context size in tokens, or 0 when it is not known.

        Taken from ``ai.context_tokens`` in the settings when set, otherwise learned from the server: llama.cpp's
        ``/props`` (per-slot n_ctx, so ``-np`` is accounted for) or the n_ctx an earlier HTTP 400 reported."""
        ai = self.settings.section("ai")
        try:
            fixed = int(ai.get("context_tokens") or 0)
        except (TypeError, ValueError):
            fixed = 0
        if fixed > 0:
            return fixed
        cfg = self.config()
        key = (cfg["mode"], cfg["base_url"], cfg["model"])
        if self._ctx_key != key:
            self._ctx_key, self._n_ctx, self._ctx_probed, self._slots = key, 0, None, 0
            self._shared_ctx_overflow = False
        if self._n_ctx:
            return self._n_ctx
        if cfg["mode"] == "on_premise" and cfg["base_url"] and (self._ctx_probed is None or time.monotonic() - self._ctx_probed > 600):
            self._ctx_probed = time.monotonic()
            self._n_ctx, self._slots = self._probe_context(cfg["base_url"])
        return self._n_ctx

    def _probe_context(self, base_url: str) -> tuple[int, int]:
        """(per-request context, parallel slots) from llama-server's /props; (0, 0) when not reported."""
        root = re.sub(r"/v1$", "", base_url)
        try:
            with httpx.Client(timeout=5.0, trust_env=False, transport=self.transport) as c:
                resp = c.get(root + "/props")
            if resp.status_code != 200:
                return 0, 0
            data = resp.json()
            n = int((data.get("default_generation_settings") or {}).get("n_ctx") or data.get("n_ctx") or 0)
            slots = int(data.get("total_slots") or 0)
            return max(n, 0), max(slots, 0)
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return 0, 0

    def parallel_requests(self) -> int:
        """How many requests may run at the same time (23.3.9).

        A CPU spends most of the time of an answer reading the model from memory, so llama-server writes two answers
        in parallel slots almost as fast as one. Limited by ``ai.parallel`` (default 2) and, for an On-Premise server,
        by the slots it reports (one when it reports none)."""
        try:
            wanted = max(1, min(8, int(self.settings.section("ai").get("parallel") or 2)))
        except (TypeError, ValueError):
            wanted = 2
        if self.mode() == "cloud":
            return wanted
        self.context_window()
        n_ctx = self._n_ctx
        if self._shared_ctx_overflow or (n_ctx and n_ctx < 8192):
            # A small context, or slots that share one context and overflowed: one request at a time.
            return 1
        return max(1, min(wanted, self._slots or 1))

    def _acquire(self) -> None:
        limit = self.parallel_requests()
        with self._cv:
            while self._active >= limit:
                self._cv.wait(timeout=5)
                limit = self.parallel_requests()
            self._active += 1

    def _release(self) -> None:
        with self._cv:
            self._active -= 1
            self._cv.notify_all()

    @staticmethod
    def estimate_tokens(messages: list[dict[str, str]] | str) -> int:
        """A deliberately high estimate (about 3.2 characters per token for English, JSON and code)."""
        if isinstance(messages, str):
            return int(len(messages) / 3.2) + 1
        return sum(int(len(m.get("content") or "") / 3.2) + 8 for m in messages) + 16

    # ---- raw chat ----------------------------------------------------------------------------------------

    def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False, max_tokens: int | None = None,
             timeout: float | None = None) -> str:
        return self.chat_result(messages, json_mode=json_mode, max_tokens=max_tokens, timeout=timeout)[0]

    def chat_result(self, messages: list[dict[str, str]], *, json_mode: bool = False, max_tokens: int | None = None,
                    timeout: float | None = None, schema: dict[str, Any] | None = None) -> tuple[str, str]:
        """Returns (content, finish_reason). finish_reason "length" means the answer was cut off.

        * The answer budget is reduced so prompt + answer fit the model's context window when it is known; a prompt
          that cannot fit is refused before it is sent (AI_CONTEXT_EXCEEDED), not after minutes of waiting.
        * HTTP 429/5xx and dropped connections are retried twice (after 3 s and 8 s).
        * Every error names what the server said, e.g. "HTTP 400: the request exceeds the available context size".
        """
        cfg = self.config()
        want = int(max_tokens or cfg["max_tokens"])
        n_ctx = self.context_window()
        prompt_est = self.estimate_tokens(messages)
        if n_ctx:
            # The estimate is high on purpose; 0.85 of it is about the real token count. The answer limit never goes
            # past what is left of the context (a larger one makes newer llama-server builds fail with HTTP 500).
            room = n_ctx - int(prompt_est * 0.85) - 32
            if room < min(want, 256):
                raise self._context_error(cfg, n_ctx, int(prompt_est * 0.85) + min(want, 256), None)
            want = min(want, room)
        body: dict[str, Any] = {
            "messages": messages,
            "temperature": cfg["temperature"],
            "max_tokens": want,
            "stream": False,
        }
        if cfg["model"]:
            body["model"] = cfg["model"]
        if json_mode:
            body["response_format"] = {"type": "json_object"}
            if schema and cfg["mode"] == "on_premise" and self.settings.section("ai").get("constrained_json", True):
                # 23.3.12: llama-server turns the schema into a grammar, so even a small model can only write JSON
                # of this shape (and stops when the object is complete). A server that does not support it answers
                # HTTP 400 and the request is sent again with plain JSON mode.
                body["response_format"] = {"type": "json_object", "schema": schema}
        if cfg["mode"] == "on_premise" and self.settings.section("ai").get("disable_thinking", True):
            # Qwen3 and other reasoning models otherwise spend the answer budget on a <think> block.
            body["chat_template_kwargs"] = {"enable_thinking": False}
        started = time.monotonic()
        # As many requests at a time as the server has slots (one for a server that reports none).
        self._acquire()
        try:
            c = self._client(timeout or cfg["timeout_seconds"])
        except BaseException:
            self._release()
            raise
        try:
            with c:
                resp = self._exchange(c, cfg, body)
        finally:
            self._release()
        if resp.status_code in (401, 403):
            raise QAError("AI_REQUEST_FAILED", f"The {cfg['label']} server rejected the request (HTTP "
                          f"{resp.status_code}): check the API key.", status=502, details=resp.text[:500])
        if resp.status_code != 200:
            detail = _error_detail(resp)
            log.warning(f"AI server HTTP {resp.status_code}: {resp.text[:2000]}", extra={"operation": "ai.chat"})
            raise QAError("AI_REQUEST_FAILED", f"The AI server returned HTTP {resp.status_code}"
                          + (f": {detail}" if detail else "."), status=502, details=resp.text[:1000])
        try:
            choice = resp.json()["choices"][0]
            content = choice["message"]["content"]
            finish = str(choice.get("finish_reason") or "")
            usage = resp.json().get("usage") or {}
            timings = resp.json().get("timings") or {}
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise QAError("AI_INVALID_RESPONSE", "The AI server returned an unexpected response.", status=502,
                          details=resp.text[:500]) from exc
        if finish == "length" and not self._n_ctx and cfg["mode"] == "on_premise":
            # Stopped before max_tokens: the context window is full. Remember its size for the next requests.
            try:
                used = int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
                if used and int(usage.get("completion_tokens") or 0) < body["max_tokens"] - 8:
                    self._n_ctx = used
            except (TypeError, ValueError):
                pass
        speed = ""
        if isinstance(timings, dict) and timings.get("predicted_per_second"):
            # llama-server: how fast it read the prompt (cached tokens are not read again) and wrote the answer
            speed = (f", read {timings.get('prompt_n', '?')} new prompt tokens at "
                     f"{float(timings.get('prompt_per_second') or 0):.0f}/s ({timings.get('cache_n', 0)} cached), wrote "
                     f"{timings.get('predicted_n', '?')} tokens at {float(timings['predicted_per_second']):.1f}/s")
        log.info(f"AI completion in {time.monotonic() - started:.1f}s (prompt ~{prompt_est} tokens, max_tokens "
                 f"{body['max_tokens']}, finish {finish or '-'}{speed})", extra={"operation": "ai.chat"})
        return content or "", finish

    def _exchange(self, c: httpx.Client, cfg: dict[str, Any], body: dict[str, Any]) -> httpx.Response:
        """POST the request; on HTTP 400 adjust it once per known cause (context, response_format, unknown field)."""
        resp = self._post(c, cfg, body)
        for _ in range(3):
            if resp.status_code == 500 and _is_context_error(_error_detail(resp)):
                # llama-server (newer builds): the answer filled the context while it was being written, often
                # because parallel slots share one context ("Context size has been exceeded."). Never retried as
                # it is: the caller asks again with a shorter request, and later requests go one at a time.
                self._shared_ctx_overflow = True
                raise QAError("AI_OUTPUT_TRUNCATED", f"The {cfg['label']} server ran out of context while writing the "
                              f"answer ({_error_detail(resp)})", status=502, details=resp.text[:500])
            if resp.status_code != 400:
                break
            detail = _error_detail(resp)
            if _is_context_error(detail):
                n, used = _context_numbers(resp, detail)
                if n:
                    self._n_ctx = n
                if n and used and n - used >= 256 and body["max_tokens"] > n - used - 32:
                    # vLLM / LM Studio style: prompt + max_tokens > context. Ask for a shorter answer once.
                    body["max_tokens"] = n - used - 32
                    resp = self._post(c, cfg, body)
                    continue
                raise self._context_error(cfg, n, used, detail)
            if "schema" in (body.get("response_format") or {}):
                log.info(f"AI server refused the JSON schema ({detail}); using plain JSON mode",
                         extra={"operation": "ai.chat"})
                body["response_format"] = {"type": "json_object"}
            elif "response_format" in body:
                body.pop("response_format")  # some servers do not support response_format
            elif "chat_template_kwargs" in body:
                body.pop("chat_template_kwargs")  # some servers reject unknown fields
            else:
                break
            resp = self._post(c, cfg, body)
        return resp

    def _post(self, c: httpx.Client, cfg: dict[str, Any], body: dict[str, Any]) -> httpx.Response:
        for attempt, wait in enumerate((3, 8, None)):
            try:
                resp = c.post("/chat/completions", json=body)
            except httpx.TimeoutException as exc:
                self._unavailable(f"{cfg['label']} did not answer in time.")
                raise QAError("AI_TIMEOUT", f"The {cfg['label']} server did not answer in time.", status=504) from exc
            except httpx.HTTPError as exc:
                if wait is not None and not isinstance(exc, httpx.UnsupportedProtocol):
                    log.info(f"AI request failed ({type(exc).__name__}); retrying in {wait}s",
                             extra={"operation": "ai.chat"})
                    _sleep(wait)
                    continue
                self._unavailable(f"{cfg['label']} server not reachable at {cfg['base_url']}.")
                raise QAError("AI_UNREACHABLE", f"{cfg['label']} server not reachable at {cfg['base_url']}.",
                              status=502, details=str(exc)) from exc
            if (resp.status_code in (408, 429, 500, 502, 503, 504) and wait is not None
                    and not _is_context_error(_error_detail(resp))):
                log.info(f"AI server HTTP {resp.status_code} ({_error_detail(resp)}); retrying in {wait}s",
                         extra={"operation": "ai.chat"})
                _sleep(wait)
                continue
            return resp
        raise AssertionError("unreachable")

    def _context_error(self, cfg: dict[str, Any], n_ctx: int, used: int | None, detail: str | None) -> QAError:
        size = f"{n_ctx} tokens" if n_ctx else "too small"
        need = f" (the request needs about {used} tokens)" if used else ""
        return QAError(
            "AI_CONTEXT_EXCEEDED",
            f"The request does not fit the {cfg['label']} model's context window ({size}){need}. The application "
            "retries with smaller requests; if it still fails, start the AI server with a larger context, e.g. "
            "llama-server -c 16384 (with -np N the context is shared between N slots).",
            status=502, details=detail)

    # ---- structured output -------------------------------------------------------------------------------

    def complete_json(self, system: str, user: str, model: type[T], *, max_tokens: int | None = None,
                      timeout: float | None = None, enforce_schema: dict[str, Any] | bool | None = None) -> T:
        """``enforce_schema`` (23.3.15): True (the model's schema) or a schema of its own is also sent to the AI server,
        which then only lets the model write JSON of that shape and stops when the object is complete (llama-server
        grammar; a server that does not support it gets plain JSON mode)."""
        compact = _compact_schema(model.model_json_schema())
        schema = json.dumps(compact, separators=(",", ":"))
        grammar = compact if enforce_schema is True else (enforce_schema or None)
        messages = [
            {"role": "system", "content": f"{system}\n\nReply with one JSON object only, matching this JSON schema:\n{schema}"},
            {"role": "user", "content": user},
        ]
        reply, finish = self.chat_result(messages, json_mode=True, max_tokens=max_tokens, timeout=timeout,
                                         schema=grammar)
        try:
            return model.model_validate(extract_json(reply))
        except (ValueError, ValidationError) as first_error:
            if finish == "length":
                # Cut off at the answer limit: sending the half answer back only overflows the context (HTTP 400).
                raise QAError("AI_OUTPUT_TRUNCATED", f"The {self.label()} answer was cut off before it was complete "
                              "(answer limit or context window reached).", status=502,
                              details=str(first_error)[:300]) from first_error
            reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.S).strip()
            note = (f"That reply was not valid for the schema ({str(first_error)[:400]}). "
                    "Reply again with only the corrected JSON object.")
            repair = messages + [{"role": "assistant", "content": reply}, {"role": "user", "content": note}]
            n_ctx = self.context_window()
            budget = int(max_tokens or self.config()["max_tokens"])
            fits = (self.estimate_tokens(repair) + min(budget, 1024) <= n_ctx) if n_ctx else len(reply) <= 6000
            if not fits:  # ask again from the start instead of echoing a long answer
                repair = [messages[0], {"role": "user", "content": f"{user}\n\nImportant: {note}"}]
            try:
                reply, _ = self.chat_result(repair, json_mode=True, max_tokens=max_tokens, timeout=timeout,
                                            schema=grammar)
                return model.model_validate(extract_json(reply))
            except (ValueError, ValidationError) as exc:
                raise QAError("AI_OUTPUT_INVALID", f"The {self.label()} did not return valid structured output.",
                              status=502, details=str(exc)[:500]) from exc

    # ---- diagnostics -----------------------------------------------------------

    def diagnostics(self) -> dict[str, Any]:
        cfg = self.config()
        checks: list[dict[str, str]] = []

        def add(name: str, status: str, detail: str) -> None:
            checks.append({"name": name, "status": status, "detail": detail})

        def skip(after: str) -> None:
            for name in ("Server Reachability", "Model Loaded", "Completion", "Structured JSON Output"):
                if not any(c["name"] == name for c in checks):
                    add(name, "SKIP", f"Not run because {after} failed.")

        if not cfg["base_url"]:
            add("Configuration", "FAIL", f"No {cfg['label']} server URL is configured.")
            skip("configuration")
            return self._diag_result(cfg, checks)
        host = httpx.URL(cfg["base_url"]).host
        local = host in ("127.0.0.1", "localhost", "::1") or host.startswith(("10.", "192.168.", "172."))
        if cfg["mode"] == "cloud":
            if not cfg["cloud"]["has_api_key"]:
                add("Configuration", "FAIL", "No Cloud AI API key is saved.")
                skip("configuration")
                return self._diag_result(cfg, checks)
            add("Configuration", "PASS", f"Cloud AI endpoint {cfg['base_url']} with a saved API key.")
        else:
            add("Configuration", "PASS", f"Server {cfg['base_url']} ({'local/on-premise network' if local else 'non-local host: check it is on-premise'}).")
        try:
            loaded = self.models()
            add("Server Reachability", "PASS", f"Models endpoint answered; models: {', '.join(loaded) or 'none reported'}.")
        except QAError as exc:
            add("Server Reachability", "FAIL", exc.message)
            skip("server reachability")
            return self._diag_result(cfg, checks)
        if cfg["model"] and loaded and cfg["model"] not in loaded:
            add("Model Loaded", "FAIL", f"Configured model '{cfg['model']}' is not loaded. Loaded: {', '.join(loaded)}.")
            skip("model check")
            return self._diag_result(cfg, checks)
        add("Model Loaded", "PASS", f"Using '{cfg['model'] or (loaded[0] if loaded else 'server default')}'.")
        n_ctx = self.context_window()
        if n_ctx and self._slots:
            add("Parallel Requests", "PASS", f"{self.parallel_requests()} at a time (the server has {self._slots} "
                "slot(s); ai.parallel in settings.json sets the most).")
        if n_ctx:
            add("Context Window", "PASS", f"{n_ctx} tokens per request" + (
                " (small: test case generation sends one acceptance criterion per AI call; -c 16384 is better)"
                if n_ctx < 8192 else "."))
        try:
            t0 = time.monotonic()
            reply = self.chat([{"role": "user", "content": "Reply with the single word: READY"}], max_tokens=64,
                              timeout=min(cfg["timeout_seconds"], 120))
            ok = "READY" in reply.upper()
            add("Completion", "PASS" if ok else "FAIL",
                f"Answered in {time.monotonic() - t0:.1f}s" + ("" if ok else f" but not as instructed: {reply[:80]!r}"))
        except QAError as exc:
            add("Completion", "FAIL", exc.message)
            skip("completion")
            return self._diag_result(cfg, checks)

        try:
            add("Generation Speed", "PASS", self.speed_test(cfg))
        except QAError as exc:
            add("Generation Speed", "PASS", f"Not measured: {exc.message}")

        class _Probe(BaseModel):
            status: str
            count: int

        try:
            probe = self.complete_json("You are a diagnostics probe.", 'Return status "ok" and count 3.', _Probe,
                                       max_tokens=128)
            ok = probe.status.lower() == "ok" and probe.count == 3
            add("Structured JSON Output", "PASS" if ok else "FAIL",
                "Valid JSON returned." if ok else f"JSON parsed but content was wrong: {probe.model_dump()}")
        except QAError as exc:
            add("Structured JSON Output", "FAIL", exc.message)
        return self._diag_result(cfg, checks)

    def speed_test(self, cfg: dict[str, Any] | None = None) -> str:
        """Measure how fast the model writes (tokens per second), the number that decides how long test case
        generation takes. One test case is about 450 tokens."""
        cfg = cfg or self.config()
        body: dict[str, Any] = {"messages": [{"role": "user", "content": "Write the numbers from 1 to 60 separated by "
                                                                          "commas, nothing else."}],
                                "max_tokens": 120, "temperature": 0, "stream": False}
        if cfg["model"]:
            body["model"] = cfg["model"]
        if cfg["mode"] == "on_premise" and self.settings.section("ai").get("disable_thinking", True):
            body["chat_template_kwargs"] = {"enable_thinking": False}
        self._acquire()
        try:
            with self._client(min(cfg["timeout_seconds"], 180)) as c:
                t0 = time.monotonic()
                resp = self._exchange(c, cfg, body)
                elapsed = time.monotonic() - t0
        finally:
            self._release()
        if resp.status_code != 200:
            raise QAError("AI_REQUEST_FAILED", f"HTTP {resp.status_code}: {_error_detail(resp)}", status=502)
        data = resp.json()
        timings = data.get("timings") or {}
        tokens = int((data.get("usage") or {}).get("completion_tokens") or timings.get("predicted_n") or 0)
        per_s = float(timings.get("predicted_per_second") or (tokens / elapsed if elapsed and tokens else 0))
        if not per_s:
            return f"Answered in {elapsed:.1f}s (the server did not report token counts)."
        per_case = 450 / per_s
        verdict = ("fast" if per_s >= 20 else "usable" if per_s >= 10 else
                   "slow: use the GPU (-ngl 99) or a smaller model, see PATCH_23.3.9.md")
        return (f"{per_s:.1f} tokens/s ({verdict}); one test case takes about {per_case:.0f}s to write, "
                f"{self.parallel_requests()} written at a time.")

    def _diag_result(self, cfg: dict[str, Any], checks: list[dict[str, str]]) -> dict[str, Any]:
        ok = all(c["status"] == "PASS" for c in checks)
        if not ok:
            failed = next(c for c in checks if c["status"] == "FAIL")
            self._unavailable(f"{cfg['label']}: {failed['name']} failed. {failed['detail']}")
        return {"ok": ok, "mode": cfg["mode"], "label": cfg["label"], "base_url": cfg["base_url"], "model": cfg["model"],
                "checks": checks}
