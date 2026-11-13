"""The local language model connection. The agent reuses the Qwen model SkyGuard already runs (llama.cpp
``llama-server`` with a Qwen GGUF, OpenAI-compatible API on 127.0.0.1). Learning is never written into the model:
the model only proposes (interprets a request, names a field, suggests a rule), and deterministic code validates
everything it says. Any OpenAI-compatible local server can replace it; knowledge is unaffected.

``OpenAICompatibleLLM`` ignores proxy settings and refuses non-local addresses unless explicitly allowed, so no
prompt leaves the PC by default.
"""

from __future__ import annotations

import ipaddress
import json
import re
import threading
import urllib.error
import urllib.request
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit

from .util import AgentError

_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


class LLM(Protocol):
    name: str

    def chat(self, messages: list[dict[str, str]], *, max_tokens: int = 600, json_mode: bool = False) -> str: ...


def extract_json(text: str) -> Any:
    text = _THINK.sub("", text or "").strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise ValueError("No JSON in the model reply.")
    start = min(starts)
    end = text.rfind("}" if text[start] == "{" else "]")
    if end <= start:
        raise ValueError("Unterminated JSON in the model reply.")
    return json.loads(text[start:end + 1])


def is_local_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class OpenAICompatibleLLM:
    """Chat completions against a local server; one request at a time per instance (no duplicate model load)."""

    def __init__(self, base_url: str, model: str = "qwen", *, timeout: float = 120.0, allow_remote: bool = False,
                 api_key: str | None = None) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise AgentError("LLM_URL_INVALID", "The AI server address must be an http(s) URL.")
        if not allow_remote and not is_local_url(base_url):
            raise AgentError("LLM_URL_NOT_LOCAL", "The AI server must run on this PC (127.0.0.1). Allow a network "
                             "address explicitly in the agent configuration if your organisation hosts it.", 400)
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/v1"):
            self.base_url += "/v1"
        self.model = model
        self.timeout = timeout
        self.api_key = api_key
        self.name = f"{model} @ {self.base_url}"
        self._lock = threading.Lock()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never through a proxy

    def _request(self, path: str, body: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method="POST" if data else "GET",
                                     headers={"Content-Type": "application/json"})
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with self._opener.open(req, timeout=timeout or self.timeout) as resp:
                return json.loads(resp.read(5_000_000).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise AgentError("LLM_HTTP_ERROR", f"The local AI server answered HTTP {exc.code}.", 502) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AgentError("LLM_UNAVAILABLE", f"The local AI server at {self.base_url} is not answering. Start "
                             "the local model (SkyGuard AI Runtime, or the companion's AI settings).", 503) from exc

    def health(self) -> dict[str, Any]:
        try:
            data = self._request("/models", timeout=5)
            models = [m.get("id") for m in (data.get("data") or []) if isinstance(m, dict)]
            return {"ok": True, "models": models}
        except AgentError as exc:
            return {"ok": False, "error": exc.message}

    def chat(self, messages: list[dict[str, str]], *, max_tokens: int = 600, json_mode: bool = False) -> str:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "max_tokens": max_tokens,
                                "temperature": 0.1, "stream": False}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        # Qwen3-style models: no hidden reasoning block (faster, and nothing unparsed)
        body["chat_template_kwargs"] = {"enable_thinking": False}
        with self._lock:
            data = self._request("/chat/completions", body)
        try:
            return str(data["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError) as exc:
            raise AgentError("LLM_BAD_REPLY", "The local AI server sent an answer in an unexpected format.", 502) from exc


class CallableLLM:
    """Adapter: wrap any function(messages, max_tokens, json_mode) -> str (SkyGuard's AI gateway, tests)."""

    def __init__(self, fn: Callable[..., str], name: str = "callable") -> None:
        self.fn = fn
        self.name = name

    def chat(self, messages: list[dict[str, str]], *, max_tokens: int = 600, json_mode: bool = False) -> str:
        return self.fn(messages, max_tokens=max_tokens, json_mode=json_mode)


class NoLLM:
    """No model configured: the agent still works with the deterministic parser and learned knowledge."""

    name = "none"

    def chat(self, messages: list[dict[str, str]], *, max_tokens: int = 600, json_mode: bool = False) -> str:
        raise AgentError("LLM_NOT_CONFIGURED", "No local AI model is configured.", 503)
