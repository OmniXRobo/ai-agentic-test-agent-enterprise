"""Playwright driver: SkyGuard-controlled browser sessions for the agent.

Each application account gets its own isolated browser context (separate cookies and storage), so sequential account
switching never mixes sessions. Input goes through Playwright's trusted input (fill / select_option / click with
actionability checks), the same events a person produces, so the application's own validation always runs; values
are never assigned to the DOM directly. On a pause the browser and page stay open for the person to work in.

Playwright is a SkyGuard dependency already; this module is imported only where Playwright exists.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

JS = (Path(__file__).resolve().parents[1] / "js" / "agent_dom.js").read_text(encoding="utf-8")


class PlaywrightDriver:
    def __init__(self, browser: Any, *, context_options: dict[str, Any] | None = None,
                 evidence_dir: Path | None = None) -> None:
        self.browser = browser
        self.context_options = context_options or {}
        self.evidence_dir = evidence_dir
        self.contexts: dict[str, Any] = {}
        self.pages: dict[str, Any] = {}
        self.current = "default"
        self._frames: list[Any] = []
        self.manual_events: list[dict[str, Any]] = []
        self._observing = False
        self._open("default")

    # ---- contexts ------------------------------------------------------------------------------------------------

    def _open(self, name: str) -> Any:
        if name not in self.contexts:
            ctx = self.browser.new_context(**self.context_options)
            ctx.add_init_script(JS)
            ctx.expose_binding("__sgReport", lambda source, event: self._on_event(event))
            self.contexts[name] = ctx
            self.pages[name] = ctx.new_page()
        self.current = name
        return self.pages[name]

    @property
    def page(self) -> Any:
        return self.pages[self.current]

    def _on_event(self, event: Any) -> None:
        if isinstance(event, dict) and self._observing:
            self.manual_events.append(event)

    def observe_user(self, on: bool = True) -> None:
        """Record the person's own clicks and entries (guided learning, manual intervention)."""
        self._observing = on
        for frame in self.page.frames:
            try:
                frame.evaluate("on => on ? window.__sgAgent.observe(e => window.__sgReport && window.__sgReport(e)) "
                               ": window.__sgAgent.stopObserving()", on)
            except Exception:
                pass

    # ---- observation ---------------------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        page = self.page
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        merged: dict[str, Any] | None = None
        self._frames = []
        for idx, frame in enumerate(page.frames):
            try:
                if not frame.evaluate("() => !!window.__sgAgent"):
                    frame.evaluate(JS)
                snap = frame.evaluate("p => window.__sgAgent.snapshot({prefix: p})", str(idx))
            except Exception:
                continue
            self._frames.append(frame)
            while len(self._frames) <= idx:
                self._frames.append(None)
            self._frames[idx] = frame
            if merged is None:
                merged = snap
            else:
                merged["elements"] += snap.get("elements", [])
                merged["tables"] += snap.get("tables", [])
                merged["messages"] += snap.get("messages", [])
                merged["text"] = (merged.get("text") or "") + "\n" + (snap.get("text") or "")[:1000]
                for k in ("password_fields", "otp_fields"):
                    merged["auth"][k] = merged["auth"].get(k, 0) + (snap.get("auth") or {}).get(k, 0)
                merged["auth"]["captcha"] = merged["auth"].get("captcha") or (snap.get("auth") or {}).get("captcha")
        if self._observing:
            self.observe_user(True)
        return merged or {"url": page.url, "title": "", "elements": [], "tables": [], "messages": [], "auth": {}}

    def _element(self, handle: str) -> Any:
        prefix, _, _ = str(handle).partition(":")
        frame = self._frames[int(prefix)] if prefix.isdigit() and int(prefix) < len(self._frames) else None
        if frame is None:
            return None
        h = frame.evaluate_handle("h => window.__sgAgent.element(h)", handle)
        return h.as_element()

    # ---- actions -------------------------------------------------------------------------------------------------

    def perform(self, action: dict[str, Any]) -> dict[str, Any]:
        t = action.get("type")
        page = self.page
        try:
            if t == "navigate":
                page.goto(action["url"], wait_until="domcontentloaded", timeout=30000)
                self._settle()
                return {"ok": True}
            if t == "snapshot":
                if action.get("wait_ms"):
                    page.wait_for_timeout(min(int(action["wait_ms"]), 15000))
                return {"ok": True}
            if t == "switch_context":
                page = self._open(str(action.get("account_id") or "default"))
                if action.get("url"):
                    page.goto(action["url"], wait_until="domcontentloaded", timeout=30000)
                    self._settle()
                return {"ok": True}
            el = self._element(action.get("handle", ""))
            if el is None:
                return {"ok": False, "error": "STALE", "message": "The element is no longer on the page."}
            if not el.is_enabled():
                return {"ok": False, "error": "DISABLED"}
            if t == "fill":
                if not el.is_editable():
                    return {"ok": False, "error": "READONLY"}
                el.fill(str(action.get("value") or ""), timeout=10000)
                valid = el.evaluate("e => e.validity ? {ok: e.validity.valid, m: e.validationMessage} : {ok: true}")
                return {"ok": True, "invalid": not valid.get("ok", True), "validation": valid.get("m")}
            if t == "select":
                value = str(action.get("value") or "")
                try:
                    chosen = el.select_option(label=value, timeout=5000)
                except Exception:
                    try:
                        chosen = el.select_option(value=value, timeout=5000)
                    except Exception:
                        opts = el.evaluate("e => [...e.options].map(o => o.text.trim())")
                        match = [o for o in opts if value.lower() in o.lower()]
                        if len(match) != 1:
                            return {"ok": False, "error": "OPTION_NOT_FOUND", "options": opts[:50]}
                        chosen = el.select_option(label=match[0], timeout=5000)
                return {"ok": bool(chosen)}
            if t == "check":
                el.set_checked(bool(action.get("value") in (True, "true", "1", "yes", "on")), timeout=5000)
                return {"ok": True}
            if t == "click":
                el.click(timeout=10000)
                self._settle()
                return {"ok": True}
            return {"ok": False, "error": "UNKNOWN_ACTION"}
        except Exception as exc:  # the page changed under us, a timeout, a closed dialog
            text = str(exc).splitlines()[0][:200]
            code = "TIMEOUT" if "Timeout" in text else "STALE" if ("detached" in text or "not attached" in text) else "ERROR"
            return {"ok": False, "error": code, "message": text}

    def _settle(self) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass

    def screenshot(self, name: str) -> str | None:
        if not self.evidence_dir:
            return None
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        path = self.evidence_dir / f"{name}.png"
        try:
            self.page.screenshot(path=str(path), mask=[self.page.locator("input[type=password]")])
            return str(path)
        except Exception:
            return None

    # ---- the loop ------------------------------------------------------------------------------------------------

    def run(self, engine: Any, run_id: str, *, stop: threading.Event | None = None,
            resumed: threading.Event | None = None, on_directive: Callable[[dict[str, Any]], None] | None = None,
            on_wait: Callable[["PlaywrightDriver", dict[str, Any]], None] | None = None, poll_s: float = 0.5, max_steps: int = 20000) -> dict[str, Any]:
        """Drive one execution until it finishes, waiting (browser open) while it is paused or unconfirmed."""
        obs: dict[str, Any] = {"snapshot": self.snapshot(), "capabilities": {"isolated_contexts": True}}
        for _ in range(max_steps):
            if stop is not None and stop.is_set():
                break
            directive = engine.next(run_id, obs)
            if on_directive:
                on_directive(directive)
            if "done" in directive:
                return directive["done"]
            if "wait" in directive:
                if directive["wait"] == "paused":
                    self.screenshot(f"{run_id}-pause-{int(time.time())}")
                    self.manual_events = []
                    self.observe_user(True)
                while True:
                    if stop is not None and stop.is_set():
                        return {"status": "detached"}
                    run = engine.run(run_id)
                    if run["status"] not in ("paused", "awaiting_confirmation", "planning"):
                        break
                    if on_wait:  # runs in the browser's own thread (a person's actions in tests, UI hooks)
                        on_wait(self, run)
                    self._idle(poll_s)
                self.observe_user(False)
                obs = {"snapshot": self.snapshot(), "capabilities": {"isolated_contexts": True}}
                continue
            action = directive["action"]
            result = self.perform(action)
            obs = {"snapshot": self.snapshot(), "result": result, "capabilities": {"isolated_contexts": True}}
        return {"status": engine.run(run_id)["status"]}

    def _idle(self, seconds: float) -> None:
        """Wait while letting Playwright deliver the page's events (the person's clicks and typing are reported
        through an exposed binding, which is only dispatched while Playwright is waiting)."""
        try:
            self.page.wait_for_timeout(int(seconds * 1000))
        except Exception:
            time.sleep(seconds)

    def close(self) -> None:
        for ctx in list(self.contexts.values()):
            try:
                ctx.close()
            except Exception:
                pass
        self.contexts.clear()
        self.pages.clear()
