"""System status for the command-center status strip: TFS, On-Premise AI, Playwright, speech.

Every value is a real check, cached so that pages never trigger checks of their own:
the strip asks once when the dashboard opens and again only after ``TTL_SECONDS``
or when the person presses refresh. A connection test run from TFS Connection or
On-Premise AI settings updates the cache straight away.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from .clock import now_iso
from .errors import QAError
from .logging_setup import get_logger

log = get_logger("health")

TTL_SECONDS = 300.0


def _item(key: str, label: str, status: str, value: str, detail: str) -> dict[str, Any]:
    return {"key": key, "label": label, "status": status, "value": value, "detail": detail, "checked_at": now_iso()}


def _failed_detail(result: dict[str, Any]) -> str:
    failed = next((c for c in result.get("checks") or [] if c.get("status") == "FAIL"), None)
    return f"{failed['name']}: {failed['detail']}" if failed else ""


class HealthService:
    def __init__(self, tfs: Any, ai: Any, orchestration: Any, voice: Any) -> None:
        self.tfs, self.ai, self.orchestration, self.voice = tfs, ai, orchestration, voice
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def remember(self, key: str, item: dict[str, Any]) -> None:
        with self._lock:
            self._cache[key] = (time.monotonic(), item)

    def _cached(self, key: str, fn: Callable[[], dict[str, Any]], force: bool) -> dict[str, Any]:
        with self._lock:
            hit = self._cache.get(key)
        if hit and not force and time.monotonic() - hit[0] < TTL_SECONDS:
            return hit[1]
        try:
            item = fn()
        except Exception as exc:  # a status light must never break the dashboard
            log.exception("Status check %s failed", key)
            item = _item(key, key.upper(), "FAIL", "Error", f"The check itself failed ({type(exc).__name__}).")
        self.remember(key, item)
        return item

    # ---- individual checks -------------------------------------------------------------------

    def check_tfs(self) -> dict[str, Any]:
        p = self.tfs.profile()
        label = "TFS " + ("Online" if p["mode"] == "online" else "Offline")
        if not p.get("url") or not p.get("project"):
            return _item("tfs", label, "NOT_CONFIGURED", "Not set up", "No server URL or project in the active TFS profile.")
        try:
            self.tfs.projects()
        except QAError as exc:
            return _item("tfs", label, "FAIL", "Unavailable", exc.message)
        return _item("tfs", label, "PASS", "Connected", f"{p['label']} · {p['project']}")

    def tfs_tested(self, result: dict[str, Any]) -> None:
        """Called after a full TFS connection test, so the strip shows its outcome."""
        p = self.tfs.profile()
        label = "TFS " + ("Online" if p["mode"] == "online" else "Offline")
        if result.get("mode") and result["mode"] != p["mode"]:
            return  # a test of the other profile does not change the active connection's light
        ok = bool(result.get("ok"))
        reason = _failed_detail(result)
        self.remember("tfs", _item("tfs", label, "PASS" if ok else "FAIL", "Connected" if ok else "Unavailable",
                                   reason or (f"{p['label']} · {p['project']}" if ok else "Connection test failed.")))

    def check_ai(self) -> dict[str, Any]:
        cfg = self.ai.config()
        if not cfg.get("base_url"):
            return _item("ai", "Local AI", "NOT_CONFIGURED", "Not set up", "Set the On-Premise AI server URL.")
        try:
            models = self.ai.models(timeout=3)
        except QAError as exc:
            return _item("ai", "Local AI", "FAIL", "Offline", exc.message)
        wanted = cfg.get("model") or ""
        if wanted and models and wanted not in models:
            return _item("ai", "Local AI", "WARN", "Model missing", f"{wanted} is not loaded; the server has {', '.join(models[:3])}.")
        return _item("ai", "Local AI", "PASS", "Ready", wanted or (models[0] if models else "Server answering"))

    def ai_tested(self, result: dict[str, Any]) -> None:
        ok = bool(result.get("ok"))
        self.remember("ai", _item("ai", "Local AI", "PASS" if ok else "FAIL", "Ready" if ok else "Offline",
                                  "Diagnostics passed." if ok else _failed_detail(result) or "Diagnostics failed."))

    def check_playwright(self) -> dict[str, Any]:
        c = self.orchestration.check_playwright()
        return _item("playwright", "Playwright", c["status"], "Armed" if c["status"] == "PASS" else "Check", c["detail"])

    def check_speech(self) -> dict[str, Any]:
        cfg = self.voice.config()
        engine = cfg.get("stt_engine")
        if not cfg.get("voice_enabled", True) or engine == "off":
            return _item("speech", "Speech", "NOT_CONFIGURED", "Off", "Voice input is off; typed commands still work.")
        if engine == "builtin":
            status, detail = self.voice.offline.status()
            return _item("speech", "Speech", status, "Offline" if status == "PASS" else "Check", detail)
        if engine == "local":
            return _item("speech", "Speech", "PASS" if cfg.get("stt_url") else "NOT_CONFIGURED",
                         "Local server" if cfg.get("stt_url") else "Not set up", cfg.get("stt_url") or "Set the speech server URL.")
        return _item("speech", "Speech", "WARN", "Browser", "Browser recognition needs Internet in Chrome and Edge.")

    def summary(self, force: bool = False) -> dict[str, Any]:
        checks = (("tfs", self.check_tfs), ("ai", self.check_ai), ("playwright", self.check_playwright),
                  ("speech", self.check_speech))
        items = [self._cached(key, fn, force) for key, fn in checks]
        overall = "FAIL" if any(i["status"] == "FAIL" for i in items) else \
            "WARN" if any(i["status"] in ("WARN", "NOT_CONFIGURED") for i in items) else "PASS"
        return {"items": items, "overall": overall, "ttl_seconds": TTL_SECONDS}
