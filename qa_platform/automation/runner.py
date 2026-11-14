"""Test execution with visual playback, self-healing and evidence."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from ..agents.base import BaseAgent
from ..clock import now_iso
from ..config import Paths, Settings
from ..errors import QAError
from ..events import names
from ..runs import RunStore
from ..storage.db import Database, dumps, loads
from . import components
from . import healing
from . import semantic
from . import visual as visual_fx
from .browser import context_options, default_headless, js, launch, start_playwright
from . import files as filekit
from .dsl import describe, FILE_CHECKS, NEEDS_TARGET, Script, Step, Target, ValueResolver
from .store import CredentialProfiles, ScriptStore


class TestExecutionAgent(BaseAgent):
    agent_id = "test_execution"


class ManualPlaybackAgent(BaseAgent):
    agent_id = "manual_playback"


class SelfHealingAgent(BaseAgent):
    agent_id = "self_healing"


class StepFailed(Exception):
    def __init__(self, index: int, message: str) -> None:
        super().__init__(message)
        self.index = index
        self.message = message


class CommitBlocked(StepFailed):
    """23.6.0: the step would pay, purchase or finalise a booking without the person's explicit permission."""


# 23.6.0: controls that commit money or a booking. "Checkout" and "Search" are not commitments.
COMMIT_RE = re.compile(r"\b(pay now|pay\b|purchase|buy now|place (?:your )?order|complete (?:booking|purchase|payment|"
                       r"order)|confirm (?:booking|and pay|payment|purchase|reservation)|book now|reserve now|"
                       r"finali[sz]e (?:booking|payment))", re.I)


SET_COUNT_RE = re.compile(r"^\s*(.+?)\s*(?:=|:|\bto\b)\s*(\d{1,3})\s*$", re.I)
VISUAL_KIND = {"fill": "type", "type": "type", "select": "type", "press": "type"}


def _short(sel: str) -> str:
    return sel if len(sel) <= 70 else sel[:67] + "..."


class Runner:
    def __init__(self, paths: Paths, settings: Settings, db: Database, runs: RunStore, scripts: ScriptStore,
                 creds: CredentialProfiles, execution: TestExecutionAgent, playback: ManualPlaybackAgent,
                 healer: SelfHealingAgent) -> None:
        self.paths = paths
        self.settings = settings
        self.db = db
        self.runs = runs
        self.scripts = scripts
        self.creds = creds
        self.execution = execution
        self.playback = playback
        self.healer = healer
        self.stop_events: dict[str, threading.Event] = {}
        # 23.6.0: a visible run started by a person pauses (browser kept open) instead of failing when a control
        # cannot be recognised confidently; the person acts in the browser and answers here.
        self.interventions: dict[str, dict[str, Any]] = {}

    def stop(self, run_id: str) -> bool:
        ev = self.stop_events.get(run_id)
        if ev:
            ev.set()
            pending = self.interventions.get(run_id)
            if pending:
                pending["answer"] = "stop"
                pending["_event"].set()
            return True
        return False

    # ---- 23.6.0: human intervention -------------------------------------------------------------

    def intervention(self, run_id: str) -> dict[str, Any] | None:
        info = self.interventions.get(run_id)
        return {k: v for k, v in info.items() if not k.startswith("_")} if info else None

    def answer_intervention(self, run_id: str, action: str, choice: str | None = None) -> dict[str, Any]:
        info = self.interventions.get(run_id)
        if info is None:
            raise QAError("RUN_NOT_PAUSED", f"Run {run_id} is not waiting for you.", status=409, run_id=run_id)
        if action not in info["options"]:
            raise QAError("INTERVENTION_ACTION_INVALID", f"Choose one of: {', '.join(info['options'])}.", status=422)
        if action == "choose" and choice not in (info.get("choices") or []):
            raise QAError("INTERVENTION_CHOICE_INVALID", "Pick one of the options listed for this step.", status=422)
        info["answer"], info["choice"] = action, choice
        info["_event"].set()
        return {"run_id": run_id, "answer": action}

    def _intervene(self, page: Any, act: Any, run_id: str, index: int, desc: str, reason: str, *,
                   options: list[str], choices: list[str] | None = None, kind: str = "component",
                   stop: threading.Event | None = None, minutes: float = 15.0) -> tuple[str, str | None]:
        """Pause the run with the browser open until the person answers (or the time limit / Stop)."""
        ev = threading.Event()
        info = {"run_id": run_id, "step": index, "description": desc, "reason": reason, "options": options,
                "choices": choices or [], "kind": kind, "since": now_iso(), "answer": None, "choice": None,
                "_event": ev}
        self.interventions[run_id] = info
        act.event(names.AGENT_PAUSED, f"Paused at step {index + 1}: {reason}", step=index,
                  intervention={k: v for k, v in info.items() if not k.startswith("_")})
        deadline = time.monotonic() + minutes * 60
        try:
            while not ev.is_set():
                if stop is not None and stop.is_set():
                    info["answer"] = "stop"
                    break
                if time.monotonic() > deadline:
                    info["answer"] = "timeout"
                    break
                try:  # keep Playwright serving the page while the person works in it
                    page.wait_for_timeout(250)
                except Exception:
                    time.sleep(0.25)
        finally:
            self.interventions.pop(run_id, None)
        answer = info["answer"] or "stop"
        act.event(names.AGENT_RESUMED, {"continue": "Continuing: the person completed the step in the browser",
                                        "retry": "Trying the step again", "skip": "Step skipped by the person",
                                        "allow": "The person allowed this commitment once",
                                        "choose": f"The person chose '{info.get('choice')}'",
                                        "timeout": "No answer in time; the run stops",
                                        "stop": "Stopped by the person"}.get(answer, answer), step=index)
        return answer, info.get("choice")

    def running(self) -> list[str]:
        return list(self.stop_events)

    # ---- main entry ----------------------------------------------------------------

    def run(self, script_id: str, run_id: str, *, dataset: dict[str, Any] | None = None,
            headless: bool | None = None, data_label: str | None = None, browser: str | None = None,
            interactive: bool = False) -> dict[str, Any]:
        script = self.scripts.get(script_id)
        playback = self.settings.section("playback")
        # 23.3.21: a schedule or voice command can ask for another browser for this run. 23.3.35: otherwise Play uses
        # the browser chosen under Settings > Preferences (Microsoft Edge when the installer found it), not the browser
        # the script happened to be recorded in, so one setting decides where every test opens.
        chosen = browser or playback.get("browser")
        if chosen:
            script = script.model_copy(update={"browser": chosen})
        visual = bool(playback.get("visual_cursor", True) or playback.get("highlight", True))
        headless = default_headless(playback.get("headless")) if headless is None else headless
        agent = self.playback if script.mode in ("guided", "manual") else self.execution
        evidence_dir = self.paths.evidence / run_id
        evidence_dir.mkdir(parents=True, exist_ok=True)
        stop = threading.Event()
        self.stop_events[run_id] = stop
        tc, us = script.test_case_id, script.user_story_id
        title = f"{script.name}" + (f" (Test Case #{tc})" if tc else "") + (f" - {data_label}" if data_label else "")
        self.runs.start(run_id, "functional", title, test_case_id=tc, user_story_id=us)
        resolver = ValueResolver(
            data=dataset, credentials=lambda role, field: self.creds.lookup(script.application, role, field),
            unique=run_id[-6:])
        record: dict[str, Any] = {
            "run_id": run_id, "script_id": script.id, "test_case_id": tc, "user_story_id": us,
            "status": "RUNNING", "started_at": now_iso(), "browser": script.browser, "base_url": script.base_url,
            "steps": [], "evidence": {"folder": str(evidence_dir), "screenshots": [], "video": None, "downloads": []},
            "failed_step": None, "error": None, "healed": [], "data_row": data_label,
        }
        row_id = self.db.execute(
            "INSERT INTO execution_results (run_id, script_id, test_case_id, user_story_id, status, started_at, browser,"
            " base_url) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, script.id, tc, us, "RUNNING", record["started_at"], script.browser, script.base_url)).lastrowid
        label = (f"Test Case #{tc}" if tc else script.name) + (f" ({data_label})" if data_label else "")
        try:
            with agent.activity(f"Running {label}", run_id=run_id, test_case_id=tc, user_story_id=us,
                                script_id=script.id) as act:
                act.event(names.EXECUTION_STARTED, f"Started {label} in {script.browser}")
                playback = {**playback, "_interactive": bool(interactive and not headless
                                                             and playback.get("pause_for_help", True))}
                self._execute(script, act, resolver, record, evidence_dir, visual, playback, headless, stop)
                act.event(names.EXECUTION_COMPLETED, f"{label} PASSED"
                          + (f" after self-healing {len(record['healed'])} locator(s)" if record["healed"] else ""),
                          link=f"#/history?run={run_id}")
                act.complete(f"PASSED: {label}")
            record["status"] = "PASSED"
        except StepFailed as exc:
            record["status"] = "FAILED"
            record["failed_step"] = exc.index
            record["error"] = resolver.mask(exc.message)
            agent.emit(names.EXECUTION_FAILED, f"{label} FAILED at step {exc.index + 1}: {record['error']}",
                       run_id=run_id, test_case_id=tc, user_story_id=us,
                       details={"link": f"#/history?run={run_id}", "failed_step": exc.index, "script_id": script.id})
        except QAError as exc:
            record["status"] = "FAILED"
            record["error"] = exc.message
            agent.emit(names.EXECUTION_FAILED, f"{label} could not run: {exc.message}", run_id=run_id,
                       test_case_id=tc, user_story_id=us, details={"link": f"#/history?run={run_id}"})
        finally:
            self.stop_events.pop(run_id, None)
            record["finished_at"] = now_iso()
            if record["status"] == "PASSED" and record["healed"]:
                self.db.execute("UPDATE healing_events SET result = 'validated' WHERE run_id = ? AND result = 'healed'",
                                (run_id,))
            (evidence_dir / "execution.json").write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
            self.db.execute(
                "UPDATE execution_results SET status = ?, finished_at = ?, failed_step = ?, error = ?, steps = ?,"
                " evidence = ? WHERE id = ?",
                (record["status"], record["finished_at"], record["failed_step"], record["error"],
                 dumps(record["steps"]), dumps(record["evidence"]), row_id))
            self.runs.finish(run_id, record["status"], {
                "script_id": script.id, "failed_step": record["failed_step"], "error": record["error"],
                "steps": len(script.steps), "healed": len(record["healed"]),
            })
        return record

    # ---- execution -------------------------------------------------------------------

    def _execute(self, script: Script, act: Any, resolver: ValueResolver, record: dict[str, Any],
                 evidence_dir: Path, visual: bool, playback: dict[str, Any], headless: bool,
                 stop: threading.Event) -> None:
        if not script.steps:
            raise StepFailed(0, "The script has no steps.")
        delay = int(playback.get("visual_delay_ms", 350))
        video = bool(playback.get("video", False))
        step_shots = bool(playback.get("step_screenshots", True))  # 23.3.31
        pw = start_playwright()
        browser = context = page = None
        try:
            browser = launch(pw, script.browser, headless=headless)
            # final patch: a visible run opens maximized on the monitor chosen in Preferences (Browser Manager)
            ctx_opts: dict[str, Any] = context_options(headless, ignore_https_errors=True, accept_downloads=True)
            if video:
                ctx_opts["record_video_dir"] = str(evidence_dir)
            # final patch: an application that signs in with Windows (NTLM) or Basic authentication
            if script.credential_role and resolver.credentials and \
                    resolver.credentials(script.credential_role, "auth_method") in ("windows", "basic"):
                user = resolver.credentials(script.credential_role, "username")
                pwd = resolver.credentials(script.credential_role, "password")
                if user and pwd:
                    ctx_opts["http_credentials"] = {"username": user, "password": pwd}
            context = browser.new_context(**ctx_opts)
            context.add_init_script(js("fingerprint"))
            context.add_init_script(js("calendar"))
            context.add_init_script(js("components"))  # 23.6.0: universal component recognition
            if visual:
                context.add_init_script(visual_fx.overlay_config(playback))
                context.add_init_script(js("overlay"))
            page = context.new_page()
            page.set_default_timeout(15000)
            # 23.3.21: tabs a click opens, in the order they open; a step's ``tab`` picks one (0 = the first tab)
            pages = [page]
            context.on("page", lambda p: pages.append(p))
            total = len(script.steps)
            for index, step in enumerate(script.steps):
                if stop.is_set():
                    raise StepFailed(index, "Stopped by the user.")
                desc = resolver.mask(describe(step))
                act.event(names.EXECUTION_STEP, f"Step {index + 1}/{total}: {desc}", progress=int(index * 100 / total),
                          step=index)
                started = time.monotonic()
                entry: dict[str, Any] = {"index": index, "action": step.action, "description": desc, "status": "RUNNING"}
                record["steps"].append(entry)
                try:
                    page = self._tab(pages, step, page)
                    outcome = self._step_with_help(page, script, step, index, act, resolver, record, evidence_dir,
                                                   visual, delay, desc, stop, playback)
                    entry["status"] = outcome or "PASSED"
                    if step_shots:  # 23.3.31: the page after each step, shown next to the step in Recent Runs
                        shot = self._shot(page, evidence_dir, f"step-{index + 1:02d}")
                        if shot:
                            entry["screenshot"] = shot
                except StepFailed:
                    entry["status"] = "FAILED"
                    raise
                except Exception as exc:  # Playwright errors become a failed step with evidence
                    entry["status"] = "SKIPPED" if step.optional else "FAILED"
                    message = resolver.mask(str(exc).splitlines()[0][:400])
                    entry["error"] = message
                    shot = self._shot(page, evidence_dir, f"step-{index + 1:02d}-failed")
                    if shot:
                        entry["screenshot"] = shot
                        record["evidence"]["screenshots"].append(shot)
                    if not step.optional:
                        raise StepFailed(index, f"{desc}: {message}") from exc
                finally:
                    entry["duration_ms"] = int((time.monotonic() - started) * 1000)
            final = self._shot(page, evidence_dir, "final")
            if final:
                record["evidence"]["screenshots"].append(final)
        finally:
            if page is not None and record["status"] == "RUNNING" and record["steps"] and \
                    record["steps"][-1].get("status") == "FAILED" and not record["steps"][-1].get("screenshot"):
                shot = self._shot(page, evidence_dir, f"step-{record['steps'][-1]['index'] + 1:02d}-failed")
                if shot:
                    record["steps"][-1]["screenshot"] = shot
                    record["evidence"]["screenshots"].append(shot)
            try:
                if page is not None and page.video:
                    video_path = page.video.path()
                else:
                    video_path = None
                if context is not None:
                    context.close()
                if video_path:
                    record["evidence"]["video"] = str(video_path)
                if browser is not None:
                    browser.close()
            finally:
                pw.stop()

    @staticmethod
    def _tab(pages: list[Any], step: Step, current: Any) -> Any:
        """23.3.21: the tab the step was recorded in. A tab a click opened may take a moment to appear."""
        if step.tab is None:
            return pages[0] if pages and not pages[0].is_closed() else current
        deadline = time.monotonic() + step.timeout_ms / 1000
        while len(pages) <= step.tab and time.monotonic() < deadline:
            current.wait_for_timeout(200)
        if len(pages) <= step.tab:
            raise RuntimeError(f"Tab {step.tab + 1} did not open (only {len(pages)} tab(s)).")
        page = pages[step.tab]
        try:
            page.wait_for_load_state("domcontentloaded", timeout=step.timeout_ms)
            page.bring_to_front()
        except Exception:
            pass
        page.set_default_timeout(15000)
        return page

    def _shot(self, page: Any, folder: Path, name: str) -> str | None:
        try:
            page.evaluate("window.__qaOverlay && window.__qaOverlay.clear()")
        except Exception:
            pass
        path = folder / f"{name}.png"
        try:
            page.screenshot(path=str(path), full_page=False)
            return str(path)
        except Exception:
            return None

    def _step_with_help(self, page: Any, script: Script, step: Step, index: int, act: Any, resolver: ValueResolver,
                        record: dict[str, Any], evidence_dir: Path, visual: bool, delay: int, desc: str,
                        stop: threading.Event, playback: dict[str, Any]) -> str | None:
        """23.6.0: run one step; in a visible run started by a person, a step that cannot be done confidently pauses
        with the browser open (continue after doing it yourself, retry, skip, pick one of the offered choices, allow
        a payment / booking commitment once, or stop) instead of closing everything."""
        interactive = bool(playback.get("_interactive"))
        allow_commit = False
        while True:
            try:
                return self._step(page, script, step, index, act, resolver, record, evidence_dir, visual, delay,
                                  playback=playback, allow_commit=allow_commit)
            except Exception as exc:  # noqa: BLE001 - every failure gets the same treatment below
                if not interactive or stop.is_set() or step.action == "goto":
                    raise
                commit = isinstance(exc, CommitBlocked)
                reason = getattr(exc, "message", None) or str(exc).splitlines()[0][:300]
                choices = list(getattr(exc, "choices", []) or [])[:15]
                options = (["allow", "skip", "stop"] if commit else
                           (["choose"] if choices else []) + ["continue", "retry", "skip", "stop"])
                shot = self._shot(page, evidence_dir, f"step-{index + 1:02d}-paused")
                if shot:
                    record["evidence"]["screenshots"].append(shot)
                    record["steps"][-1]["screenshot"] = shot
                answer, choice = self._intervene(
                    page, act, act.run_id, index, desc, resolver.mask(reason) or reason, options=options,
                    choices=choices, kind="commit" if commit else "component", stop=stop,
                    minutes=float(playback.get("pause_minutes", 15)))
                record["steps"][-1].setdefault("interventions", []).append(
                    {"reason": resolver.mask(reason), "answer": answer, "choice": choice, "at": now_iso()})
                if answer == "retry":
                    continue
                if answer == "allow":
                    allow_commit = True
                    continue
                if answer == "continue":
                    record["steps"][-1]["note"] = "done by the person in the browser"
                    return "MANUAL"
                if answer == "skip":
                    return "SKIPPED"
                if answer == "choose" and choice:
                    components.ensure(page)
                    res = page.evaluate("t => window.__sgComp.findOption(t, null)", choice) or {}
                    if res.get("status") == "found":
                        page.locator(components.TARGET).first.click(timeout=step.timeout_ms)
                        components.settle(page)
                        record["steps"][-1]["note"] = f"'{choice}' chosen by the person"
                        return "PASSED"
                    continue  # the list closed meanwhile: try the step again
                raise StepFailed(index, f"{desc}: {resolver.mask(reason)}"
                                 + (" (no answer within the time limit)" if answer == "timeout" else "")) from exc

    def _step(self, page: Any, script: Script, step: Step, index: int, act: Any, resolver: ValueResolver,
              record: dict[str, Any], evidence_dir: Path, visual: bool, delay: int, *,
              playback: dict[str, Any] | None = None, allow_commit: bool = False) -> str | None:
        playback = playback or {}
        value = resolver.resolve(step.value)
        timeout = step.timeout_ms
        meaning = semantic.infer(step)
        if step.action == "dismiss" or meaning.get("intent") == "dismiss":  # 23.6.0: never a click mid-page
            how = components.dismiss(page)
            act.event(names.EXECUTION_STEP, f"Closed the open pop-up ({how})", step=index)
            return None
        if meaning.get("intent") == "set_count":  # 23.6.1: the + / - of a counter, played as "set it to N"
            return self._set_count(page, step, index, act, record, meaning, visual, delay)
        if step.action == "set_count":  # 23.7.1: a step written by hand: Value "Adults=2" (or "Adults: 2", "Adults to 2")
            m = SET_COUNT_RE.match(value or "")
            if not m:
                raise StepFailed(index, f"set_count needs a Value such as Adults=2 (the counter's label, then the "
                                        f"number); got '{value}'.")
            return self._set_count(page, step, index, act, record, {"label": m.group(1), "count": int(m.group(2))},
                                   visual, delay)
        if index in record.get("_done_by_choice", {}) and step.action in ("fill", "type"):
            # 23.6.1: the suggestion chosen in an earlier step already entered this field's value
            by = record["_done_by_choice"][index]
            act.event(names.EXECUTION_STEP, f"Step {index + 1}: the value was entered by the suggestion chosen in "
                                            f"step {by + 1}", step=index)
            record["steps"][-1]["note"] = f"entered by the suggestion chosen in step {by + 1}"
            return None
        if step.action == "click" and not step.semantic and step.target is not None:
            sug = semantic.suggestion_context(script, index)
            if sug is not None:
                here = self._quick(page, step.target, 800)
                if here is not None and components.classify(here).get("kind") == "container" and \
                        self._form_container(here):
                    sug = None  # a click on a whole form: handled (skipped) below
            if sug is None:
                sug = self._suggestion_after_field_click(page, script, step, index)
            if sug is not None:
                done = self._choose_suggestion(page, script, step, index, act, record, sug, visual, delay)
                if done:
                    return None
            if sug is None and self._counter_row_click(page, script, step, index, act, record, visual, delay):
                return None
        if step.action == "click" and meaning.get("intent") == "pick_date":
            # 23.6.0: a recorded click on a calendar day plays as "choose this date", whatever the calendar's HTML
            return self._choose_date(page, step, index, act, record, meaning["date"], meaning, None, visual, delay)
        if step.action == "hover":  # 23.6.0: a hover only prepares the next step; it never fails the run
            loc = self._quick(page, step.target, 3000) if step.target else None
            if loc is None:
                act.event(names.EXECUTION_STEP, f"Step {index + 1}: the hover target is not on the page; a hover only "
                                                "prepares the next step, so the run continues", step=index)
                return "SKIPPED"
            try:
                loc.hover(timeout=3000)
            except Exception:
                act.event(names.EXECUTION_STEP, f"Step {index + 1}: hover not possible; continuing", step=index)
                return "SKIPPED"
            return None
        if step.action == "goto":
            url = value or script.base_url
            if not url:
                raise StepFailed(index, "No URL for goto step.")
            if not re.match(r"^[a-z]+://", url) and script.base_url:
                url = script.base_url.rstrip("/") + "/" + url.lstrip("/")
            page.goto(url, timeout=max(timeout, 30000), wait_until="domcontentloaded")
            return
        if step.action == "wait":
            page.wait_for_timeout(int(value or 1000))
            return
        if step.action == "dialog":  # 23.3.21: the next pop-up (alert / confirm / prompt) gets OK or Cancel
            how, _, text = (value or "accept").partition(":")

            def answer(d: Any, how: str = how, text: str = text) -> None:
                act.event(names.EXECUTION_STEP, f"Pop-up \"{d.message[:120]}\": {'Cancel' if how == 'dismiss' else 'OK'}",
                          step=index)
                d.dismiss() if how == "dismiss" else (d.accept(text) if text else d.accept())

            page.once("dialog", answer)
            return
        if step.action == "expect_url":
            from playwright.sync_api import expect

            expect(page).to_have_url(re.compile(re.escape(value or "")), timeout=timeout)
            return
        if step.action == "screenshot":
            shot = self._shot(page, evidence_dir, f"step-{index + 1:02d}-{re.sub(r'[^A-Za-z0-9_-]', '_', value or 'shot')}")
            if shot:
                record["evidence"]["screenshots"].append(shot)
            return
        if step.action in FILE_CHECKS:
            self._file_check(step, index, value or "", record, act)
            return
        if step.action == "press" and step.target is None:
            page.keyboard.press(value or "Enter")
            return
        if step.action not in NEEDS_TARGET and step.action != "press":
            raise StepFailed(index, f"Unsupported action {step.action}.")
        if step.target is None:
            raise StepFailed(index, f"Step {index + 1} ({step.action}) has no target.")

        if step.action in ("pick_date", "choose_option"):
            # the field moved, or the calendar / list is already open: the component handler finds what it needs
            # 23.6.1: a recorded calendar cell (its label names the date and a price that changes) is looked for
            # briefly; the calendar handler finds the day by its date anyway
            cell = step.action == "pick_date" and bool(components.date_in_text(step.target.selector))
            located = self._quick(page, step.target, 800 if cell else min(timeout, 4000))
            if step.action == "pick_date":
                iso = visual_fx.parse_date(value or "").isoformat()
                if located is not None and components.classify(located).get("kind") in (
                        "textbox", "date_input", "autocomplete", "combobox") and \
                        located.evaluate("el => ['INPUT', 'TEXTAREA'].includes(el.tagName)"):
                    how = visual_fx.pick_date(page, located, value or "", timeout, visual, delay)
                    act.event(names.EXECUTION_STEP, f"Date {value} chosen ({how})", step=index)
                    return None
                field = located if located is not None and components.classify(located).get("kind") in (
                    "date_field", "date_input", "textbox", "autocomplete", "combobox") else None  # 23.6.1: never a day
                return self._choose_date(page, step, index, act, record, iso, meaning, field, visual, delay)
            return self._choose(page, step, index, act, record, value or "", located, visual, delay, script=script)

        locator = self._locate(page, step, index, script, act, record, evidence_dir, visual)
        if step.action == "click":
            kind_info = components.classify(locator)
            if kind_info.get("kind") == "container" and self._form_container(locator):
                # 23.6.0: older recordings stored a click on a whole search form (the clicked suggestion had no role
                # of its own); clicking the middle of a form would hit an arbitrary control, so the step is skipped
                act.event(names.EXECUTION_STEP, f"Step {index + 1} pointed at a whole form, not one control; "
                                                "skipped (the value is chosen by the field's own step)", step=index)
                record["steps"][-1]["note"] = "form container click skipped"
                return "SKIPPED"
            self._commit_guard(step, kind_info, index, playback, allow_commit)
        kind = "assert" if step.action.startswith("expect") else VISUAL_KIND.get(step.action, "click")
        box = None
        if visual and step.action != "expect_hidden":
            try:
                locator.scroll_into_view_if_needed(timeout=timeout)
                box = locator.bounding_box(timeout=timeout)
                if box:
                    # move (the Promise resolves when the cursor has arrived) -> highlight dwell -> act
                    visual_fx.point(page, box, kind, step.target.description or step.action)
                    page.wait_for_timeout(delay)
            except Exception:
                box = None
        from playwright.sync_api import expect

        a = step.action
        if a == "click":
            if box:
                visual_fx.click(page, box)
            locator.click(timeout=timeout)
        elif a == "right_click":  # 23.3.21
            if box:
                visual_fx.click(page, box)
            locator.click(button="right", timeout=timeout)
        elif a == "drag_to":  # 23.3.21: value = where it is dropped (a selector in the same frame)
            dest = self._scope(page, step.target).locator(value or "").first
            locator.drag_to(dest, timeout=timeout)
        elif a == "pick_date":
            how = visual_fx.pick_date(page, locator, value or "", timeout, bool(box), delay)
            act.event(names.EXECUTION_STEP, f"Date {value} chosen ({how})", step=index)
        elif a == "dblclick":
            if box:
                visual_fx.click(page, box)
            locator.dblclick(timeout=timeout)
        elif a in ("fill", "type"):
            field_kind = components.classify(locator).get("kind")
            popups_before = self._popups(page)
            locator.fill(value or "", timeout=timeout)
            if value and field_kind in ("autocomplete", "combobox", "textbox") and not self._next_chooses(script, index):
                # 23.6.0: typing into an autocomplete only offers suggestions; the value counts once one is chosen
                appeared = field_kind in ("autocomplete", "combobox")
                if not appeared:
                    page.wait_for_timeout(200)
                    appeared = self._popups(page) > popups_before
                if appeared:
                    chosen = components.commit_autocomplete(page, locator, value, pointer=self._pointer(page, visual, delay))
                    if chosen:
                        act.event(names.EXECUTION_STEP, f"Suggestion '{chosen['chosen'][:80]}' chosen for '{value}'",
                                  step=index)
                        record["steps"][-1]["chosen"] = chosen["chosen"][:200]
        elif a == "select":
            if box:
                visual_fx.choose(page, locator, box, value or "")
            if components.classify(locator).get("kind") in ("native_select", "native_multiselect", "unknown"):
                try:
                    locator.select_option(value=value, timeout=timeout)
                except Exception:
                    locator.select_option(label=value, timeout=timeout)
            else:  # 23.6.0: a custom dropdown recorded as a select: open it and click the option
                res = components.choose_option(page, locator, value or "", timeout=timeout)
                if not res.get("verified"):
                    raise components.ComponentError("NOT_VERIFIED", f"'{value}' was clicked but the list does not show it.")
        elif a == "check":
            if box:
                visual_fx.click(page, box)
            locator.check(timeout=timeout)
        elif a == "uncheck":
            locator.uncheck(timeout=timeout)
        elif a == "press":
            locator.press(value or "Enter", timeout=timeout)
        elif a == "expect_visible":
            expect(locator).to_be_visible(timeout=timeout)
        elif a == "expect_hidden":
            expect(locator).to_be_hidden(timeout=timeout)
        elif a == "expect_text":
            expect(locator).to_contain_text(value or "", timeout=timeout)
        elif a == "expect_value":
            expect(locator).to_have_value(value or "", timeout=timeout)
        elif a == "upload_file":
            paths = self._upload_paths(script, step, value, index)
            how = self._upload(page, locator, paths, timeout, box)
            act.event(names.EXECUTION_STEP, f"Uploaded {', '.join(p.name for p in paths)} ({how})", step=index)
        elif a == "download_file":
            saved = self._download(page, locator, value, timeout, evidence_dir, box)
            record["evidence"]["downloads"].append(str(saved))
            record["steps"][-1]["download"] = str(saved)
            act.event(names.EXECUTION_STEP, f"Downloaded {saved.name} ({saved.stat().st_size:,} bytes)", step=index)
        if visual:
            try:
                page.wait_for_timeout(min(delay, 250))
                page.evaluate("window.__qaOverlay && window.__qaOverlay.clear()")
            except Exception:
                pass  # the page may have navigated
        if a in ("click", "dblclick", "fill", "type", "select", "check", "uncheck", "press", "right_click"):
            components.settle(page, 200, int(playback.get("settle_max_ms", 3000)))  # 23.6.0: state-aware wait
        return None

    # ---- 23.6.0: component handlers ---------------------------------------------------------------------

    @staticmethod
    def _pointer(page: Any, visual: bool, delay: int) -> Any:
        if not visual:
            return None

        def point(loc: Any, label: str) -> None:
            box = loc.bounding_box(timeout=3000)
            if box:
                visual_fx.point(page, box, "click", label)
                page.wait_for_timeout(min(delay, 250))
                visual_fx.click(page, box)
        return point

    @staticmethod
    def _popups(page: Any) -> int:
        try:
            components.ensure(page)
            return int(page.evaluate("() => window.__sgComp.popups().length"))
        except Exception:
            return 0

    @staticmethod
    def _form_container(locator: Any) -> bool:
        try:
            return bool(locator.evaluate(
                "el => el.querySelectorAll('input:not([type=hidden]),select,textarea,[role=combobox]').length >= 1"))
        except Exception:
            return False

    @staticmethod
    def _next_chooses(script: Script, index: int) -> bool:
        """The recording itself chooses the suggestion in the next step (a choose_option, or a click on an option)."""
        if index + 1 >= len(script.steps):
            return False
        nxt = script.steps[index + 1]
        if nxt.action == "choose_option":
            return True
        if nxt.action == "click" and nxt.target is not None:
            role = (nxt.target.role or "") + " " + nxt.target.selector + " " + " ".join(nxt.target.alternates)
            if "option" in role or "autowhatever" in role or "suggest" in role.lower():
                return True
            return semantic.suggestion_context(script, index + 1) is not None  # 23.6.1
        return False

    @staticmethod
    def _fingerprint_selectors(target: Any) -> list[str]:
        """23.7.1: stable attributes recorded with the element, tried after its locators. Trip.com's city inputs match
        role=combobox[name=...] only while they have focus, so the recorded locator failed on every run and needed
        self-healing (which found [data-testid="search_city_from0"] each time)."""
        out: list[str] = []
        q = lambda v: str(v).replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
        if target.test_id and not re.fullmatch(r"\d+(-\d+)*", str(target.test_id)):  # "0", "1-2": a row's position
            out.append(f'[data-testid="{q(target.test_id)}"]')
        if target.element_id and not re.search(r"\d{3,}|[:]", str(target.element_id)):
            out.append(f'[id="{q(target.element_id)}"]')
        tag = target.tag or ""
        if tag in ("input", "textarea", "select") and target.name:
            out.append(f'{tag}[aria-label="{q(target.name)}"]')
        if tag in ("input", "textarea") and target.placeholder:
            out.append(f'{tag}[placeholder="{q(target.placeholder)}"]')
        return [o for o in out if o != target.selector and o not in target.alternates]

    @staticmethod
    def _quick(page: Any, target: Any, timeout: int) -> Any | None:
        scope = Runner._scope(page, target)
        for sel in [target.selector, *target.alternates, *Runner._fingerprint_selectors(target)]:
            try:
                loc = scope.locator(sel)
                loc.first.wait_for(state="visible", timeout=timeout if sel == target.selector else 800)
                return loc.first
            except Exception:
                continue
        return None

    def _commit_guard(self, step: Step, info: dict[str, Any], index: int, playback: dict[str, Any],
                      allow: bool) -> None:
        """A payment or booking-finalisation click needs its own explicit permission (never part of a normal run)."""
        if allow or step.commit_ok:
            return
        unattended = not playback.get("_interactive")
        if unattended and not playback.get("guard_commits_unattended", False):
            return
        words = " ".join(str(x or "") for x in (info.get("text"), info.get("label"),
                                                step.target.name if step.target else "", step.description))
        if COMMIT_RE.search(words):
            raise CommitBlocked(index, f"This step would {COMMIT_RE.search(words).group(0).lower()} (a payment or "
                                       "booking commitment). SkyGuard does not do that without your explicit "
                                       "permission for this step.")

    def _choose_date(self, page: Any, step: Step, index: int, act: Any, record: dict[str, Any], iso: str,
                     meaning: dict[str, Any], field: Any, visual: bool, delay: int) -> str | None:
        from ..clock import now as clock_now

        role = meaning.get("role") or (step.semantic or {}).get("role")
        res = components.pick_date(page, iso, field=field, role=role, timeout=step.timeout_ms,
                                   pointer=self._pointer(page, visual, delay), today=clock_now().date())
        if not res.get("verified"):
            raise components.ComponentError("NOT_VERIFIED", f"{iso} was clicked in the calendar but neither the "
                                                            "calendar nor a date field shows it as chosen.")
        act.event(names.EXECUTION_STEP, f"Date {iso} chosen" + (f" as the {role} date" if role else "")
                  + f" ({res['how']}, verified)", step=index)
        record["steps"][-1]["semantic"] = {"intent": "pick_date", "date": iso, "role": role, "verified": True}
        return None

    def _choose(self, page: Any, step: Step, index: int, act: Any, record: dict[str, Any], text: str,
                field: Any, visual: bool, delay: int, script: Script | None = None) -> str | None:
        sem = step.semantic or {}
        typed = sem.get("typed")
        if not typed and script is not None and index > 0 and step.target is not None:
            # the step before typed into this field: that is the text that brings the suggestions back
            prev = script.steps[index - 1]
            if prev.action in ("fill", "type") and prev.target is not None and \
                    prev.target.selector == step.target.selector and prev.value and "{{" not in prev.value:
                typed = prev.value
        if field is None:
            components.ensure(page)
            res = page.evaluate("t => window.__sgComp.findOption(t, null)", text) or {}
            if res.get("status") != "found" and sem.get("option_selector"):  # 23.6.1: the recorded row, if it is open
                try:
                    loc = page.locator(sem["option_selector"])
                    if loc.count() == 1 and loc.first.is_visible():
                        loc.first.click(timeout=step.timeout_ms)
                        components.settle(page)
                        act.event(names.EXECUTION_STEP, f"'{text}' chosen in the open list (recorded row)", step=index)
                        return None
                except Exception:
                    pass
            if res.get("status") != "found":
                raise components.ComponentError(
                    "OPTION_NOT_FOUND" if res.get("status") != "ambiguous" else "OPTION_AMBIGUOUS",
                    f"Neither the list field nor an open list with '{text}' was found.", choices=res.get("options"))
            page.locator(components.TARGET).first.click(timeout=step.timeout_ms)
            components.settle(page)
            act.event(names.EXECUTION_STEP, f"'{res.get('text') or text}' chosen in the open list", step=index)
            return None
        res = components.choose_option(page, field, text, timeout=step.timeout_ms,
                                       pointer=self._pointer(page, visual, delay), typed=typed,
                                       list_kind=sem.get("list"), option_selector=sem.get("option_selector"))
        if not res.get("verified"):
            raise components.ComponentError("NOT_VERIFIED", f"'{text}' was clicked but the field does not show it.")
        act.event(names.EXECUTION_STEP, f"'{res['chosen'][:80]}' chosen ({res['how']}, verified)", step=index)
        record["steps"][-1]["chosen"] = res["chosen"][:200]
        return None

    def _choose_suggestion(self, page: Any, script: Script, step: Step, index: int, act: Any,
                           record: dict[str, Any], sug: dict[str, Any], visual: bool, delay: int) -> bool:
        """23.6.1: a recorded click on a suggestion row (recordings made before 23.6.1): type into the field the
        suggestion belongs to and choose the row by its text. False when the field is not there either (the click
        then runs as recorded, with self-healing)."""
        field = self._quick(page, sug["field"], min(step.timeout_ms, 4000))
        if field is None:
            return False
        typed = sug["typed"]
        try:
            res = components.choose_option(page, field, sug["option"], timeout=step.timeout_ms,
                                           pointer=self._pointer(page, visual, delay), typed=typed,
                                           option_selector=step.target.selector if step.target else None)
        except components.ComponentError as exc:
            act.log(f"Step {index + 1}: the suggestion '{sug['option'][:60]}' was not found after typing "
                    f"'{typed[:40]}' ({exc.message}); trying the recorded click", step=index)
            return False
        if not res.get("verified"):
            raise components.ComponentError("NOT_VERIFIED", f"'{sug['option']}' was clicked but the field does not "
                                                            "show it.")
        if sug.get("after"):
            record.setdefault("_done_by_choice", {})[sug["fill_index"]] = index
        act.event(names.EXECUTION_STEP, f"Suggestion '{res['chosen'][:80]}' chosen after typing '{typed[:40]}' "
                                        f"({res['how']}, verified)", step=index)
        record["steps"][-1]["chosen"] = res["chosen"][:200]
        record["steps"][-1]["semantic"] = {"intent": "choose_option", "option": sug["option"], "typed": typed,
                                           "verified": True}
        return True

    def _suggestion_after_field_click(self, page: Any, script: Script, step: Step, index: int) -> dict[str, Any] | None:
        """23.6.1: a recording whose typing was never captured: "click the arrival field", then "click New Delhi - All
        airports India". When the clicked row is not on the page and the step before clicked a text field (or a box
        holding exactly one), the row is treated as that field's suggestion and its leading words are typed."""
        text = semantic.option_text(step)
        if not text or len(text) > 120 or step.target is None:
            return None
        prev = index - 1
        while prev >= 0 and script.steps[prev].action == "hover":
            prev -= 1
        if prev < 0 or script.steps[prev].action != "click" or script.steps[prev].target is None:
            return None
        row = self._quick(page, step.target, 1500)
        if row is not None:
            try:  # the row is there and is the recorded one (an index test id such as "0" can be another list's row)
                shown = str(row.evaluate("el => window.__sgComp ? window.__sgComp.rowText(el) : el.innerText", timeout=2000))
            except Exception:
                shown = ""
            if semantic.same_text(shown, text):
                return None
        box = self._quick(page, script.steps[prev].target, 1500)
        if box is None:
            return None
        try:
            sel = box.evaluate("""el => {
                const ok = (i) => i.tagName === 'TEXTAREA' || (i.tagName === 'INPUT' && !['button','submit','reset','image',
                    'checkbox','radio','file','hidden','password'].includes(i.type));
                const f = ok(el) ? el : (() => { const all = [...el.querySelectorAll('input,textarea')].filter(ok);
                    return all.length === 1 ? all[0] : null; })();
                return f && window.__qaFp ? window.__qaFp.fingerprint(f).selector : null; }""")
        except Exception:
            sel = None
        if not sel:
            return None
        return {"field": Target(selector=sel), "typed": "", "option": text, "fill_index": -1, "after": False}

    def _counter_row_click(self, page: Any, script: Script, step: Step, index: int, act: Any,
                           record: dict[str, Any], visual: bool, delay: int) -> bool:
        """23.7.1: recordings made before 23.6.1 kept a press of a counter's icon-only + as a click on the counter's
        whole row ("Click Adults 12+ years old 1", positional li). Clicking the row does nothing, so the passengers
        never changed. The row's text names the counter and the number it showed when it was pressed; when exactly
        one of + and - could have been pressed at that number (Trip.com disables - at 1 adult), the step plays as
        "set Adults to 2". Otherwise it stops and says how to write the step, never guessing."""
        m = re.match(r"^(.*?\D)\s*(\d{1,2})$", semantic.option_text(step))
        if not m:
            return False
        text, shown = m.group(1).strip(), int(m.group(2))
        if not text or len(text) > 80:
            return False
        counters: list[dict[str, Any]] = []
        prev = script.steps[index - 1] if index > 0 else None
        rounds = 8 if prev is not None and prev.action == "click" else 1  # the pop-up the step before opened
        for _ in range(rounds):
            try:
                components.ensure(page)
                counters = page.evaluate("() => window.__sgComp.counters().map(c => ({label: c.label, sub: c.sub}))")
            except Exception:
                counters = []
            if counters:
                break
            page.wait_for_timeout(250)
        words = semantic._words(text)
        hits = [c for c in counters if semantic._words(c["label"]) and
                words[:len(semantic._words(c["label"]))] == semantic._words(c["label"])]
        if len(hits) != 1:
            return False
        here = self._quick(page, step.target, 600)
        if here is not None:
            try:
                if here.evaluate("el => !!(window.__sgComp && window.__sgComp.stepperOf(el))"):
                    return False  # the recorded element is the + or - itself: an ordinary click presses it
            except Exception:
                pass
        label = hits[0]["label"]
        state = page.evaluate("l => window.__sgComp.counter(l, 0)", label) or {}
        page.evaluate("() => window.__sgComp.clear()")
        if state.get("value") == shown and state.get("minusDisabled") and not state.get("plusDisabled"):
            count, which = shown + 1, "+"
        elif state.get("value") == shown and state.get("plusDisabled") and not state.get("minusDisabled"):
            count, which = shown - 1, "-"
        else:
            raise components.ComponentError(
                "COUNTER_AMBIGUOUS",
                f"Step {index + 1} was recorded by an older version as a click on the whole '{label}' row (showing "
                f"{shown}), so it does not say whether + or - was pressed. Change this step to the action set_count "
                f"with the Value {label}={shown + 1} (or the number you want), or record it again.",
                choices=[f"{label}={shown + 1}", f"{label}={max(0, shown - 1)}"])
        res = components.set_count(page, label, count, timeout=step.timeout_ms, pointer=self._pointer(page, visual, delay))
        act.event(names.EXECUTION_STEP, f"Step {index + 1}: the click on the '{label}' row (older recording) was its "
                                        f"{which} (the other control is disabled at {shown}); {label} set to {count} "
                                        f"({res['presses']} press{'es' if res['presses'] != 1 else ''}, verified)",
                  step=index)
        record["steps"][-1]["semantic"] = {"intent": "set_count", "label": label, "count": count, "verified": True}
        record["steps"][-1]["note"] = f"older recording: row click played as {label} {which}"
        return True

    def _set_count(self, page: Any, step: Step, index: int, act: Any, record: dict[str, Any],
                   meaning: dict[str, Any], visual: bool, delay: int) -> str | None:
        """23.6.1: "Set Children to 1": press the counter's + / - until it shows the recorded number."""
        label, count = str(meaning.get("label") or ""), int(meaning.get("count") or 0)
        res = components.set_count(page, label, count, timeout=step.timeout_ms,
                                   pointer=self._pointer(page, visual, delay))
        act.event(names.EXECUTION_STEP, f"{res['label']} set to {count} (from {res['from']}, "
                                        f"{res['presses']} press{'es' if res['presses'] != 1 else ''}, verified)",
                  step=index)
        record["steps"][-1]["semantic"] = {"intent": "set_count", "label": res["label"], "count": count,
                                           "verified": True}
        return None

    # ---- 23.3.17: upload and download -----------------------------------------------------

    def _upload_paths(self, script: Script, step: Step, value: str | None, index: int) -> list[Path]:
        names_ = [n.strip() for n in re.split(r"[;\n]", value or "") if n.strip()]
        if not names_:
            raise StepFailed(index, "Upload step: no file. Attach the file in Edit Script > Files and put its name "
                                    "in Value.")
        folder = self.scripts.files_dir(script.id)
        out = []
        for name in names_:
            candidate = Path(name)
            if not candidate.is_absolute():
                candidate = folder / filekit.safe_name(name)
            if not candidate.is_file():
                raise StepFailed(index, f"Upload step: the file {name} is not attached to this script "
                                        f"(Edit Script > Files) and is not a full path on this PC.")
            out.append(candidate)
        return out

    @staticmethod
    def _upload(page: Any, locator: Any, paths: list[Path], timeout: int, box: Any) -> str:
        files = [str(p) for p in paths]
        is_input = locator.evaluate("el => el.tagName === 'INPUT' && el.type === 'file'")
        if is_input:
            locator.set_input_files(files, timeout=timeout)
            return "file field"
        # A button or drop zone that opens the file chooser: answer the chooser with the files.
        with page.expect_file_chooser(timeout=timeout) as chooser:
            if box:
                visual_fx.click(page, box)
            locator.click(timeout=timeout)
        chooser.value.set_files(files)
        return "file chooser"

    @staticmethod
    def _download(page: Any, locator: Any, value: str | None, timeout: int, evidence_dir: Path, box: Any) -> Path:
        with page.expect_download(timeout=max(timeout, 30000)) as info:
            if box:
                visual_fx.click(page, box)
            locator.click(timeout=timeout)
        download = info.value
        failure = download.failure()
        if failure:
            raise RuntimeError(f"The download failed: {failure}")
        folder = evidence_dir / "downloads"
        folder.mkdir(parents=True, exist_ok=True)
        name = filekit.safe_name(value or download.suggested_filename or "download")
        target = folder / name
        n = 2
        while target.exists():
            target = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
            n += 1
        download.save_as(str(target))
        return target

    def _file_check(self, step: Step, index: int, value: str, record: dict[str, Any], act: Any) -> None:
        downloads = record["evidence"].get("downloads") or []
        if not downloads:
            raise StepFailed(index, f"{describe(step)}: no file was downloaded before this step. Add a "
                                    "'download_file' step first.")
        path = Path(downloads[-1])
        if step.action == "expect_file_name":
            if filekit.normalize(value) not in filekit.normalize(path.name):
                raise StepFailed(index, f"The downloaded file is {path.name}, its name does not contain '{value}'.")
            return
        if step.action == "expect_file_text":
            text = filekit.file_text(path)
            missing = [v for v in (x.strip() for x in value.split("||")) if v and filekit.normalize(v) not in
                       filekit.normalize(text)]
            if missing:
                sample = " ".join(text.split())[:160]
                raise StepFailed(index, f"{path.name} does not contain '{missing[0]}'."
                                        + (f" It starts with: {sample}" if sample else " No text was found in it."))
            act.event(names.EXECUTION_STEP, f"{path.name} contains '{value}'", step=index)
            return
        sheet, cell, expected = filekit.parse_cell_check(value)
        actual = filekit.excel_cell(path.read_bytes(), cell, sheet)
        if not filekit.same_value(actual, expected):
            raise StepFailed(index, f"{path.name} {(sheet + '!') if sheet else ''}{cell} is '{actual}', "
                                    f"expected '{expected}'.")
        act.event(names.EXECUTION_STEP, f"{path.name} {cell} = '{actual}'", step=index)

    # ---- locating and healing --------------------------------------------------------

    @staticmethod
    def _scope(page: Any, target: Any) -> Any:
        """23.3.21: the page, or the iframe(s) the element is in."""
        root = page
        for sel in (getattr(target, "frames", None) or []):
            root = root.frame_locator(sel)
        return root

    def _try(self, page: Any, selector: str, timeout: int, state: str) -> Any | None:
        try:
            loc = page.locator(selector)
            loc.first.wait_for(state=state, timeout=timeout)
            return loc.first
        except Exception:
            return None

    def _locate(self, page: Any, step: Step, index: int, script: Script, act: Any, record: dict[str, Any],
                evidence_dir: Path, visual: bool) -> Any:
        target = step.target
        assert target is not None
        state = "hidden" if step.action == "expect_hidden" else ("attached" if step.action == "expect_text" else "visible")
        scope = self._scope(page, target)
        loc = self._try(scope, target.selector, step.timeout_ms, state)
        if loc is not None or step.action == "expect_hidden":
            return loc if loc is not None else scope.locator(target.selector).first
        for alt in target.alternates:
            loc = self._try(scope, alt, 1500, state)
            if loc is not None:
                act.log(f"Primary locator failed; recorded alternate worked: {_short(alt)}", step=index)
                return loc
        for alt in self._fingerprint_selectors(target):  # 23.7.1: recorded test id, id, label, placeholder
            loc = self._try(scope, alt, 1500, state)
            if loc is not None and scope.locator(alt).count() == 1:
                act.log(f"Primary locator failed; the element's recorded {_short(alt)} worked", step=index)
                return loc
        # final patch: a locator correction a person approved earlier (AURA memory) is tried before self-healing.
        # It is only a candidate: the element must still be there and visible, and the step must still pass.
        from .. import aura

        try:
            known = aura.current().approved_locator(script.application, target.selector) if aura.current() else None
        except Exception:  # noqa: BLE001 - memory lookups never break a run
            known = None
        if known and known not in target.alternates:
            loc = self._try(scope, known, 2000, state)
            if loc is not None:
                act.log(f"Primary locator failed; AURA's approved correction worked: {_short(known)}", step=index)
                return loc
        if target.frames:  # self-healing reads the page's own elements; inside an iframe only alternates are tried
            raise StepFailed(index, f"Element not found in the frame: {target.description or target.selector} "
                                    f"(selector {_short(target.selector)}).")
        healed = self._heal(page, step, index, script, act, record, evidence_dir, visual)
        if healed is None:
            pending = [n for n in record.get("needs_confirmation", []) if n["step"] == index]
            if pending:
                raise StepFailed(index, f"Element not found: {target.description or target.selector} "
                                        f"(selector {_short(target.selector)}); self-healing found only low-confidence "
                                        f"candidates. Confirm one under Self-healing (#{pending[-1]['healing_id']}).")
            raise StepFailed(index, f"Element not found: {target.description or target.selector} "
                                    f"(selector {_short(target.selector)}); self-healing found no confident match.")
        return healed

    def _heal(self, page: Any, step: Step, index: int, script: Script, act: Any, record: dict[str, Any],
              evidence_dir: Path, visual: bool) -> Any | None:
        target = step.target
        assert target is not None
        run_id = act.run_id
        label = target.description or _short(target.selector)
        try:
            with self.healer.activity(f"Recovering locator for step {index + 1}: {label}", run_id=run_id,
                                      test_case_id=script.test_case_id, user_story_id=script.user_story_id) as h:
                h.event(names.SELFHEALING_STARTED, f"Original locator failed: {_short(target.selector)}")
                h.progress(30, "Analyzing the page DOM for candidate elements")
                try:
                    candidates = page.evaluate("window.__qaFp ? window.__qaFp.candidates() : []")
                except Exception:
                    candidates = []
                ranked = healing.rank(target, candidates, exclude={target.selector})
                h.progress(60, f"Scored {len(ranked)} candidate elements")
                chosen = healing.choose(ranked)
                verified = None
                if chosen:
                    verified = self._try(page, chosen["selector"], 2000, "visible")
                    if verified is not None:
                        try:
                            if page.locator(chosen["selector"]).count() != 1:
                                verified = None
                        except Exception:
                            verified = None
                shot = None
                if verified is not None and visual:
                    try:
                        box = verified.bounding_box()
                        if box:
                            page.evaluate("([b]) => window.__qaOverlay && window.__qaOverlay.point(b.x, b.y, b.width, b.height, 'heal', 'Self-healed locator')", [box])
                    except Exception:
                        pass
                path = evidence_dir / f"healing-step-{index + 1:02d}.png"
                try:
                    page.screenshot(path=str(path))
                    shot = str(path)
                except Exception:
                    shot = None
                # Final patch (defect 7): close but not confident (or two look equally right) -> a person decides.
                review = healing.review(ranked) if verified is None else None
                result = "healed" if verified is not None else ("needs_confirmation" if review else "failed")
                event = {
                    "timestamp": now_iso(), "run_id": run_id, "script_id": script.id, "test_case_id": script.test_case_id,
                    "step_index": index, "page_url": page.url, "original_selector": target.selector,
                    "healed_selector": chosen["selector"] if verified is not None else None,
                    "confidence": chosen["confidence"] if chosen else (ranked[0]["confidence"] if ranked else 0.0),
                    "candidates": dumps([{k: c[k] for k in ("selector", "confidence", "signals")} for c in ranked[:5]]),
                    "screenshot": shot, "result": result,
                }
                event_id = self.db.execute(
                    "INSERT INTO healing_events (timestamp, run_id, script_id, test_case_id, step_index, page_url,"
                    " original_selector, healed_selector, confidence, candidates, screenshot, result)"
                    " VALUES (:timestamp,:run_id,:script_id,:test_case_id,:step_index,:page_url,:original_selector,"
                    ":healed_selector,:confidence,:candidates,:screenshot,:result)", event).lastrowid
                if shot:
                    record["evidence"]["screenshots"].append(shot)
                if verified is None:
                    best = f" Best candidate {ranked[0]['selector']} scored {ranked[0]['confidence']:.2f}." if ranked else ""
                    if review:
                        h.event(names.SELFHEALING_FAILED, f"Step {index + 1}: low-confidence candidates need your "
                                f"confirmation (healing #{event_id})", healing_id=event_id, needs_confirmation=True)
                        record.setdefault("needs_confirmation", []).append({"healing_id": event_id, "step": index,
                                                                          "original": target.selector})
                        raise RuntimeError(f"Low-confidence locator: confirm a candidate in Self-Healing "
                                           f"(healing #{event_id}).{best}")
                    raise RuntimeError(f"No confident alternative locator (threshold {healing.THRESHOLD:.2f}).{best}")
                info = {"healing_id": event_id, "step": index, "original": target.selector,
                        "healed": chosen["selector"], "confidence": chosen["confidence"]}
                from .. import aura  # final patch: AURA keeps the self-healing history (not approved until applied)

                aura.learn("healing", script.application, f"locator:{target.selector}", {
                    "original": target.selector, "healed": chosen["selector"], "confidence": chosen["confidence"],
                    "page_url": event["page_url"], "script_id": script.id, "step": index,
                    "signals": chosen.get("signals")}, source="self_healing", agent="self_healing")
                record["healed"].append(info)
                record["steps"][-1]["healed"] = info
                h.event(names.SELFHEALING_COMPLETED,
                        f"Locator healed for step {index + 1}: {_short(chosen['selector'])} (confidence {chosen['confidence']:.2f})",
                        **info)
                h.complete(f"Self-healing recovered {label} (confidence {chosen['confidence']:.2f})")
                return verified
        except RuntimeError as exc:
            self.healer.emit(names.SELFHEALING_FAILED, f"Self-healing failed for step {index + 1}: {exc}", run_id=run_id,
                             test_case_id=script.test_case_id)
            return None

    # ---- history -------------------------------------------------------------------------

    def results(self, run_id: str | None = None, test_case_id: str | None = None, limit: int = 100,
                script_id: str | None = None) -> list[dict[str, Any]]:
        clauses, params = [], []
        if script_id:
            clauses.append("script_id = ?")
            params.append(script_id)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if test_case_id:
            clauses.append("test_case_id = ?")
            params.append(test_case_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(f"SELECT * FROM execution_results {where} ORDER BY id DESC LIMIT ?", [*params, limit])
        for r in rows:
            r["steps"] = loads(r["steps"]) if r["steps"] else []
            r["evidence"] = loads(r["evidence"]) if r["evidence"] else {}
        return rows

    def healing_events(self, script_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        where, params = ("WHERE script_id = ?", [script_id]) if script_id else ("", [])
        rows = self.db.query(f"SELECT * FROM healing_events {where} ORDER BY id DESC LIMIT ?", [*params, limit])
        for r in rows:
            r["candidates"] = loads(r["candidates"]) if r["candidates"] else []
        return rows

    def confirm_healing(self, healing_id: int, selector: str | None) -> dict[str, Any]:
        """A person reviewed a low-confidence healing: pick one of its candidates (the script is updated and AURA
        stores the choice as an approved correction), or pass no selector to reject them all."""
        row = self.db.query_one("SELECT * FROM healing_events WHERE id = ?", (healing_id,))
        if row is None:
            raise QAError("HEALING_NOT_FOUND", f"Healing event {healing_id} does not exist.", status=404)
        if row["result"] != "needs_confirmation":
            raise QAError("HEALING_NOT_PENDING", "This healing event is not waiting for confirmation.", status=409)
        if not selector:
            self.db.execute("UPDATE healing_events SET result = 'rejected' WHERE id = ?", (healing_id,))
            return {"healing_id": healing_id, "result": "rejected"}
        offered = [c.get("selector") for c in (loads(row["candidates"]) if row["candidates"] else [])]
        if selector not in offered:
            raise QAError("HEALING_CANDIDATE_UNKNOWN", "Pick one of the candidates recorded for this step.", status=422)
        script = self.scripts.get(row["script_id"])
        step = script.steps[row["step_index"]] if row["step_index"] < len(script.steps) else None
        if step is None or step.target is None or step.target.selector != row["original_selector"]:
            raise QAError("HEALING_STALE", "The script changed since this healing; review the step manually.", status=409)
        step.target.alternates = [row["original_selector"]] + [a for a in step.target.alternates if a != selector]
        step.target.selector = selector
        self.scripts.save(script, reason=f"self-healing #{healing_id} confirmed by a reviewer")
        self.db.execute("UPDATE healing_events SET result = 'confirmed', healed_selector = ?, applied = 1 WHERE id = ?",
                        (selector, healing_id))
        from .. import aura

        aura.learn("approved_correction", script.application, f"locator:{row['original_selector']}", {
            "original": row["original_selector"], "healed": selector, "confidence": row["confidence"],
            "healing_id": healing_id, "script_id": script.id, "confirmed": True}, source="self_healing.confirm",
            approved=True, agent="self_healing")
        return {"healing_id": healing_id, "result": "confirmed", "selector": selector, "script_id": script.id}

    def apply_healing(self, healing_id: int) -> Script:
        row = self.db.query_one("SELECT * FROM healing_events WHERE id = ?", (healing_id,))
        if row is None:
            raise QAError("HEALING_NOT_FOUND", f"Healing event {healing_id} does not exist.", status=404)
        if row["result"] != "validated":
            raise QAError("HEALING_NOT_VALIDATED", "Only a healed locator whose run then passed can update the script.",
                          status=409)
        if row["applied"]:
            raise QAError("HEALING_ALREADY_APPLIED", "This healed locator was already applied.", status=409)
        script = self.scripts.get(row["script_id"])
        step = script.steps[row["step_index"]] if row["step_index"] < len(script.steps) else None
        if step is None or step.target is None or step.target.selector != row["original_selector"]:
            raise QAError("HEALING_STALE", "The script changed since this healing; review the step manually.", status=409)
        step.target.alternates = [row["original_selector"]] + [a for a in step.target.alternates
                                                               if a != row["healed_selector"]]
        step.target.selector = row["healed_selector"]
        saved = self.scripts.save(script, reason=f"self-healing #{healing_id} applied (confidence {row['confidence']:.2f})")
        self.db.execute("UPDATE healing_events SET applied = 1 WHERE id = ?", (healing_id,))
        from .. import aura  # final patch: a person applied it, so AURA stores it as an approved correction

        aura.learn("approved_correction", script.application, f"locator:{row['original_selector']}", {
            "original": row["original_selector"], "healed": row["healed_selector"], "confidence": row["confidence"],
            "healing_id": healing_id, "script_id": script.id}, source="self_healing.apply", approved=True,
            agent="self_healing")
        return saved
