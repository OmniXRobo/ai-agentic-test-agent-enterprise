"""Guided Browser Automation: record a person's actions in a real browser."""

from __future__ import annotations

import queue
import threading
from typing import Any

from ..agents.base import BaseAgent
from ..errors import QAError
from ..events import names
from .browser import js, launch, start_playwright
from .dsl import Step, Target


class GuidedBrowserAgent(BaseAgent):
    agent_id = "guided_browser"


def merge_step(steps: list[dict[str, Any]], step: dict[str, Any]) -> None:
    """Collapse noise: repeated fills of one field keep the last value; exact repeats are dropped."""
    if steps:
        last = steps[-1]
        same_target = (last.get("target") or {}).get("selector") == (step.get("target") or {}).get("selector")
        if same_target and last["action"] == step["action"] == "fill":
            last["value"] = step.get("value")
            return
        if same_target and last["action"] == step["action"] and last.get("value") == step.get("value") \
                and step["action"] != "click":
            return
    steps.append(step)


class RecordingSession:
    def __init__(self, agent: GuidedBrowserAgent, run_id: str, start_url: str, *, browser: str = "chromium",
                 headless: bool = False, credential_role: str | None = None) -> None:
        self.agent = agent
        self.run_id = run_id
        self.start_url = start_url
        self.browser = browser
        self.headless = headless
        self.credential_role = credential_role
        self.steps: list[dict[str, Any]] = [{"action": "goto", "value": start_url, "description": f"Open {start_url}"}]
        self.error: str | None = None
        self._stop = threading.Event()
        self._done = threading.Event()
        self._commands: queue.Queue[tuple[str, Any, queue.Queue[Any]]] = queue.Queue()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._main, name=f"recorder-{run_id}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _on_record(self, _source: Any, step: dict[str, Any]) -> None:
        if not isinstance(step, dict) or "action" not in step:
            return
        target = step.get("target")
        if isinstance(target, dict) and target.get("input_type") == "password":
            target["text"] = None  # never keep a typed password in the fingerprint
        if step.get("value") == "__PASSWORD__":
            role = self.credential_role or "user"
            step["value"] = "{{CRED:" + role + ".password}}"
            step["description"] = f"Enter the {role} password (from saved credentials)"
        with self._lock:
            before = len(self.steps)
            merge_step(self.steps, step)
            added = len(self.steps) > before
        if added:
            t = step.get("target") or {}
            what = t.get("name") or t.get("text") or t.get("selector") or ""
            self.agent.emit(names.AGENT_LOG, f"Recorded {step['action']} on {what[:60]}", run_id=self.run_id,
                            details={"steps": len(self.steps)})

    def _main(self) -> None:
        try:
            with self.agent.activity(f"Recording browser session from {self.start_url}", run_id=self.run_id) as act:
                act.event(names.RECORDING_STARTED, "Browser recording started. Perform the test steps in the browser window.")
                pw = start_playwright()
                try:
                    browser = launch(pw, self.browser, headless=self.headless)
                    context = browser.new_context(viewport={"width": 1366, "height": 768}, ignore_https_errors=True)
                    context.expose_binding("__qaRecord", self._on_record)
                    context.add_init_script(js("fingerprint"))
                    context.add_init_script(js("recorder"))
                    page = context.new_page()
                    page.goto(self.start_url, wait_until="domcontentloaded", timeout=60000)
                    while not self._stop.is_set():
                        try:
                            cmd, arg, reply = self._commands.get(timeout=0.2)
                        except queue.Empty:
                            if page.is_closed():
                                break
                            continue
                        try:
                            reply.put(cmd_result(page, cmd, arg))
                        except Exception as exc:  # command failures are reported to the caller
                            reply.put(exc)
                    context.close()
                    browser.close()
                finally:
                    pw.stop()
                act.event(names.RECORDING_COMPLETED, f"Recording finished: {len(self.steps)} steps captured")
                act.complete(f"Recorded {len(self.steps)} steps")
        except Exception as exc:
            self.error = getattr(exc, "message", None) or str(exc).splitlines()[0][:300]
        finally:
            self._done.set()

    def command(self, cmd: str, arg: Any = None, timeout: float = 30.0) -> Any:
        """Run a Playwright command on the recording thread (used by tests and 'add assertion')."""
        if self._done.is_set():
            raise QAError("RECORDING_NOT_ACTIVE", "The recording session has ended.", status=409)
        reply: queue.Queue[Any] = queue.Queue()
        self._commands.put((cmd, arg, reply))
        result = reply.get(timeout=timeout)
        if isinstance(result, Exception):
            raise result
        return result

    def add_assertion(self, selector: str, text: str | None) -> None:
        fp = self.command("fingerprint", selector)
        step = {"action": "expect_text" if text else "expect_visible", "target": fp, "value": text}
        with self._lock:
            self.steps.append(step)

    def stop(self, timeout: float = 20.0) -> list[Step]:
        self._stop.set()
        self._done.wait(timeout)
        if self.error and len(self.steps) <= 1:
            raise QAError("RECORDING_FAILED", f"Recording failed: {self.error}", status=502)
        return self.to_steps()

    def to_steps(self) -> list[Step]:
        out = []
        with self._lock:
            raw = list(self.steps)
        for s in raw:
            target = Target.model_validate(s["target"]) if s.get("target") else None
            desc = s.get("description")
            if not desc and target:
                name = target.name or target.text or target.label or target.placeholder or target.selector
                desc = {"click": f"Click {name}", "fill": f"Enter value in {name}", "select": f"Select {s.get('value')} in {name}",
                        "check": f"Check {name}", "uncheck": f"Uncheck {name}", "press": f"Press {s.get('value')} in {name}",
                        "expect_text": f"Verify {name} shows {s.get('value')}", "expect_visible": f"Verify {name} is visible"
                        }.get(s["action"], s["action"])
            if target:
                target.description = target.description or (target.name or target.text or target.label or None)
            out.append(Step(action=s["action"], target=target, value=s.get("value"), description=desc))
        return out

    @property
    def active(self) -> bool:
        return not self._done.is_set()


def cmd_result(page: Any, cmd: str, arg: Any) -> Any:
    if cmd == "fingerprint":
        return page.locator(arg).first.evaluate("el => window.__qaFp.fingerprint(el)")
    if cmd == "click":
        page.locator(arg).first.click()
        return True
    if cmd == "fill":
        selector, value = arg
        page.locator(selector).first.fill(value)
        page.locator(selector).first.dispatch_event("change")
        return True
    if cmd == "select":
        selector, value = arg
        page.locator(selector).first.select_option(value)
        return True
    if cmd == "url":
        return page.url
    raise ValueError(f"Unknown recorder command {cmd}")
