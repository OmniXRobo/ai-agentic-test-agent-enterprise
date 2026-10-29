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


class AIGateway:
    def __init__(self, settings: Settings, bus: EventBus, transport: httpx.BaseTransport | None = None,
                 secrets: SecretStore | None = None) -> None:
        self.settings = settings
        self.bus = bus
        self.transport = transport
        self.secrets = secrets
        self._lock = threading.Lock()
        self._last_unavailable = 0.0

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

    def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False, max_tokens: int | None = None,
             timeout: float | None = None) -> str:
        cfg = self.config()
        body: dict[str, Any] = {
            "messages": messages,
            "temperature": cfg["temperature"],
            "max_tokens": max_tokens or cfg["max_tokens"],
            "stream": False,
        }
        if cfg["model"]:
            body["model"] = cfg["model"]
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        started = time.monotonic()
        # One request at a time: a single local model serves requests sequentially anyway.
        with self._lock, self._client(timeout or cfg["timeout_seconds"]) as c:
            try:
                resp = c.post("/chat/completions", json=body)
            except httpx.TimeoutException as exc:
                self._unavailable(f"{cfg['label']} did not answer in time.")
                raise QAError("AI_TIMEOUT", f"The {cfg['label']} server did not answer in time.", status=504) from exc
            except httpx.HTTPError as exc:
                self._unavailable(f"{cfg['label']} server not reachable at {cfg['base_url']}.")
                raise QAError("AI_UNREACHABLE", f"{cfg['label']} server not reachable at {cfg['base_url']}.",
                              status=502, details=str(exc)) from exc
            if resp.status_code == 400 and json_mode:
                # Some servers do not support response_format; retry without it.
                body.pop("response_format", None)
                resp = c.post("/chat/completions", json=body)
        if resp.status_code in (401, 403):
            raise QAError("AI_REQUEST_FAILED", f"The {cfg['label']} server rejected the request (HTTP "
                          f"{resp.status_code}): check the API key.", status=502, details=resp.text[:500])
        if resp.status_code != 200:
            raise QAError("AI_REQUEST_FAILED", f"The AI server returned HTTP {resp.status_code}.", status=502,
                          details=resp.text[:500])
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as exc:
            raise QAError("AI_INVALID_RESPONSE", "The AI server returned an unexpected response.", status=502) from exc
        log.info(f"AI completion in {time.monotonic() - started:.1f}s", extra={"operation": "ai.chat"})
        return content or ""

    def complete_json(self, system: str, user: str, model: type[T], *, max_tokens: int | None = None,
                      timeout: float | None = None) -> T:
        schema = json.dumps(model.model_json_schema(), separators=(",", ":"))
        messages = [
            {"role": "system", "content": f"{system}\n\nReply with one JSON object only, matching this JSON schema:\n{schema}"},
            {"role": "user", "content": user},
        ]
        reply = self.chat(messages, json_mode=True, max_tokens=max_tokens, timeout=timeout)
        try:
            return model.model_validate(extract_json(reply))
        except (ValueError, ValidationError) as first_error:
            messages += [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": f"That reply was not valid for the schema ({str(first_error)[:400]}). "
                                            "Reply again with only the corrected JSON object."},
            ]
            reply = self.chat(messages, json_mode=True, max_tokens=max_tokens, timeout=timeout)
            try:
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

    def _diag_result(self, cfg: dict[str, Any], checks: list[dict[str, str]]) -> dict[str, Any]:
        ok = all(c["status"] == "PASS" for c in checks)
        if not ok:
            failed = next(c for c in checks if c["status"] == "FAIL")
            self._unavailable(f"{cfg['label']}: {failed['name']} failed. {failed['detail']}")
        return {"ok": ok, "mode": cfg["mode"], "label": cfg["label"], "base_url": cfg["base_url"], "model": cfg["model"],
                "checks": checks}
