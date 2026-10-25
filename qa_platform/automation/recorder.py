"""Guided Browser Automation: record a person's actions in a real browser."""

from __future__ import annotations

import queue
import threading
from typing import Any

from ..agents.base import BaseAgent
from ..errors import QAError
from ..events import names
from . import visual
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
                 headless: bool = False, credential_role: str | None = None,
                 playback: dict[str, Any] | None = None) -> None:
        self.agent = agent
        self.run_id = run_id
        self.start_url = start_url
        self.browser = browser
        self.headless = headless
        self.credential_role = credential_role
        self.playback = playback or {}
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
                    context.add_init_script(js("calendar"))
                    # The playback cursor shows what voice commands do in the recorded browser.
                    context.add_init_script(visual.overlay_config(self.playback))
                    context.add_init_script(js("overlay"))
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
                            reply.put(self._voice(page, arg) if cmd == "voice" else cmd_result(page, cmd, arg))
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

    # ---- voice commands during the recording -------------------------------------------------------

    def _voice(self, page: Any, p: dict[str, Any]) -> str:
        """Do one spoken browser action with the visible cursor. Clicks, entries and choices are recorded by the
        page recorder like the person's own; a date is recorded as a rule (Current Date +/- N days) computed at
        playback, never as the literal date."""
        action = p.get("action")
        delay = min(int(self.playback.get("visual_delay_ms", 350)), 400)
        if action == "scroll":
            page.mouse.wheel(0, 500 if p.get("value") != "up" else -500)
            return f"Scrolled {p.get('value') or 'down'}."
        if action == "press":
            page.keyboard.press(str(p.get("value") or "Enter"))
            return f"Pressed {p.get('value') or 'Enter'}."
        if action in ("select_date", "open_calendar"):
            named = p.get("target") if p.get("target") not in ("calendar", "date picker", "datepicker") else None
            field = find_field(page, named, date_like=True)
            name = field_name(field)
            if action == "open_calendar":
                page.evaluate("() => { window.__qaRecordPaused = true; }")
                try:
                    _visual_click(page, field, delay)
                    field.click(timeout=5000)
                finally:
                    page.evaluate("() => { window.__qaRecordPaused = false; }")
                return f"The calendar of {name} is open. Say which date to select, for example select tomorrow."
            days = int(p.get("days") or 0)
            rule = "{{TODAY" + (f"{days:+d}d" if days else "") + ":%Y-%m-%d}}"
            fp = field.evaluate("el => window.__qaFp.fingerprint(el)")
            page.evaluate("() => { window.__qaRecordPaused = true; }")
            try:
                from .dsl import ValueResolver

                how = visual.pick_date(page, field, ValueResolver().resolve(rule) or "", 10000, True, delay)
                page.wait_for_timeout(300)  # let the page's own change events pass while recording is paused
            finally:
                page.evaluate("() => { window.__qaRecordPaused = false; }")
            when = "Current Date" + (f" {'+' if days > 0 else '-'} {abs(days)} day{'s' if abs(days) != 1 else ''}" if days else "")
            with self._lock:
                self.steps.append({"action": "pick_date", "target": fp, "value": rule,
                                   "description": f"Pick date {when} in {name}"})
            self.agent.emit(names.AGENT_LOG, f"Recorded date rule {when} for {name} ({how})", run_id=self.run_id,
                            details={"steps": len(self.steps)})
            return f"{when} selected in {name}. Recorded as a rule, so playback uses the date of the day it runs."
        if action == "fill":
            field = find_field(page, p.get("target"))
            _visual_click(page, field, delay)
            field.fill(str(p.get("value") or ""))
            field.dispatch_event("change")
            return f"Entered {p.get('value')} in {field_name(field)}."
        if action == "select":
            field = find_field(page, p.get("target"), select=True)
            box = field.bounding_box()
            if box:
                visual.point(page, box, "type", field_name(field))
                visual.choose(page, field, box, str(p.get("value") or ""))
            try:
                field.select_option(label=str(p.get("value") or ""), timeout=3000)
            except Exception:
                field.select_option(value=str(p.get("value") or ""), timeout=3000)
            return f"Selected {p.get('value')} in {field_name(field)}."
        if action in ("click", "next_page"):
            target = "next" if action == "next_page" else str(p.get("target") or "")
            if target in ("dropdown",):
                el = find_field(page, None, select=True)
            else:
                el = find_clickable(page, target)
            _visual_click(page, el, delay)
            el.click(timeout=5000)
            return f"Clicked {target if action == 'click' else 'next page'}."
        raise QAError("VOICE_BROWSER_ACTION", "That browser action is not supported.")

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


# ---- finding what a spoken command names ---------------------------------------------------------

def _visible(locator: Any) -> Any | None:
    try:
        n = min(locator.count(), 10)
    except Exception:
        return None
    for i in range(n):
        el = locator.nth(i)
        try:
            if el.is_visible():
                return el
        except Exception:
            continue
    return None


def _visual_click(page: Any, el: Any, delay: int) -> None:
    try:
        box = el.bounding_box(timeout=3000)
        if box:
            visual.point(page, box, "click", field_name(el))
            page.wait_for_timeout(delay)
            visual.click(page, box)
    except Exception:
        pass  # the cursor is a convenience; the action itself still runs


def field_name(el: Any) -> str:
    try:
        return el.evaluate("""el => (el.labels && el.labels[0] && el.labels[0].innerText.trim()) || el.getAttribute('aria-label')
            || el.getAttribute('placeholder') || el.getAttribute('name') || el.id || (el.innerText || '').trim().slice(0, 40)
            || el.tagName.toLowerCase()""") or "the field"
    except Exception:
        return "the field"


def find_field(page: Any, name: str | None, *, date_like: bool = False, select: bool = False) -> Any:
    """The field a spoken command means: by its label, placeholder or name; else the last one used; else the only
    date field (for dates) or the first visible dropdown."""
    if name:
        n = str(name)
        candidates = [page.get_by_label(n, exact=False), page.get_by_placeholder(n, exact=False)]
        css = "select" if select else "input, textarea, select"
        safe = n.replace('"', "")
        candidates += [page.locator(f'{css}[name*="{safe}" i]'), page.locator(f'{css}[id*="{safe}" i]')]
        for c in candidates:
            el = _visible(c)
            if el:
                return el
        raise QAError("VOICE_FIELD_NOT_FOUND", f"I could not find a field called {n} on this page.", status=404)
    kind = "SELECT" if select else ""
    path = page.evaluate(f"""() => {{
        const f = window.__qaLastField;
        if (!f || !f.isConnected || !f.getClientRects().length || ('{kind}' && f.tagName !== '{kind}')) return null;
        const parts = [];
        for (let el = f; el && el.nodeType === 1 && el !== document.documentElement; el = el.parentElement) {{
            const same = el.parentElement ? [...el.parentElement.children].filter((c) => c.tagName === el.tagName) : [el];
            parts.unshift(el.tagName.toLowerCase() + (same.length > 1 ? `:nth-of-type(${{same.indexOf(el) + 1}})` : ""));
        }}
        return "html > " + parts.join(" > ");
    }}""")
    if path:
        return page.locator(path).first
    if date_like:
        el = _visible(page.locator('input[type=date], input[name*="date" i], input[id*="date" i], '
                                   'input[placeholder*="dd" i], input[placeholder*="date" i], input[class*="date" i]'))
        if el:
            return el
        raise QAError("VOICE_FIELD_NOT_FOUND", "Which date field? Click it first, or say the field name, "
                                               "for example select tomorrow in departure date.", status=404)
    el = _visible(page.locator("select")) if select else None
    if el:
        return el
    raise QAError("VOICE_FIELD_NOT_FOUND", "Which field? Click it first, or say its name.", status=404)


def find_clickable(page: Any, name: str) -> Any:
    if name == "next":
        for c in (page.get_by_role("link", name="Next"), page.get_by_role("button", name="Next"),
                  page.locator('a[rel=next], [aria-label*="next page" i], [aria-label="Next" i]'), page.get_by_text("Next", exact=True)):
            el = _visible(c)
            if el:
                return el
        raise QAError("VOICE_TARGET_NOT_FOUND", "There is no next page control on this page.", status=404)
    for c in (page.get_by_role("button", name=name), page.get_by_role("link", name=name), page.get_by_role("tab", name=name),
              page.get_by_role("menuitem", name=name), page.get_by_label(name, exact=False), page.get_by_text(name, exact=True),
              page.get_by_text(name)):
        el = _visible(c)
        if el:
            return el
    raise QAError("VOICE_TARGET_NOT_FOUND", f"I could not find {name} on this page.", status=404)
