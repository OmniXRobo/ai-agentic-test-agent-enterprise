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
from . import healing
from .browser import default_headless, js, launch, start_playwright
from .dsl import describe, NEEDS_TARGET, Script, Step, ValueResolver
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

    def stop(self, run_id: str) -> bool:
        ev = self.stop_events.get(run_id)
        if ev:
            ev.set()
            return True
        return False

    def running(self) -> list[str]:
        return list(self.stop_events)

    # ---- main entry ----------------------------------------------------------------

    def run(self, script_id: str, run_id: str, *, dataset: dict[str, Any] | None = None,
            headless: bool | None = None) -> dict[str, Any]:
        script = self.scripts.get(script_id)
        playback = self.settings.section("playback")
        visual = bool(playback.get("visual_cursor", True) or playback.get("highlight", True))
        headless = default_headless(playback.get("headless")) if headless is None else headless
        agent = self.playback if script.mode in ("guided", "manual") else self.execution
        evidence_dir = self.paths.evidence / run_id
        evidence_dir.mkdir(parents=True, exist_ok=True)
        stop = threading.Event()
        self.stop_events[run_id] = stop
        tc, us = script.test_case_id, script.user_story_id
        title = f"{script.name}" + (f" (Test Case #{tc})" if tc else "")
        self.runs.start(run_id, "functional", title, test_case_id=tc, user_story_id=us)
        resolver = ValueResolver(
            data=dataset, credentials=lambda role, field: self.creds.lookup(script.application, role, field),
            unique=run_id[-6:])
        record: dict[str, Any] = {
            "run_id": run_id, "script_id": script.id, "test_case_id": tc, "user_story_id": us,
            "status": "RUNNING", "started_at": now_iso(), "browser": script.browser, "base_url": script.base_url,
            "steps": [], "evidence": {"folder": str(evidence_dir), "screenshots": [], "video": None},
            "failed_step": None, "error": None, "healed": [],
        }
        row_id = self.db.execute(
            "INSERT INTO execution_results (run_id, script_id, test_case_id, user_story_id, status, started_at, browser,"
            " base_url) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, script.id, tc, us, "RUNNING", record["started_at"], script.browser, script.base_url)).lastrowid
        label = f"Test Case #{tc}" if tc else script.name
        try:
            with agent.activity(f"Running {label}", run_id=run_id, test_case_id=tc, user_story_id=us,
                                script_id=script.id) as act:
                act.event(names.EXECUTION_STARTED, f"Started {label} in {script.browser}")
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
        pw = start_playwright()
        browser = context = page = None
        try:
            browser = launch(pw, script.browser, headless=headless)
            ctx_opts: dict[str, Any] = {"viewport": {"width": 1366, "height": 768}, "ignore_https_errors": True}
            if video:
                ctx_opts["record_video_dir"] = str(evidence_dir)
            context = browser.new_context(**ctx_opts)
            context.add_init_script(js("fingerprint"))
            if visual:
                context.add_init_script(js("overlay"))
            page = context.new_page()
            page.set_default_timeout(15000)
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
                    self._step(page, script, step, index, act, resolver, record, evidence_dir, visual, delay)
                    entry["status"] = "PASSED"
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

    def _step(self, page: Any, script: Script, step: Step, index: int, act: Any, resolver: ValueResolver,
              record: dict[str, Any], evidence_dir: Path, visual: bool, delay: int) -> None:
        value = resolver.resolve(step.value)
        timeout = step.timeout_ms
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
        if step.action == "expect_url":
            from playwright.sync_api import expect

            expect(page).to_have_url(re.compile(re.escape(value or "")), timeout=timeout)
            return
        if step.action == "screenshot":
            shot = self._shot(page, evidence_dir, f"step-{index + 1:02d}-{re.sub(r'[^A-Za-z0-9_-]', '_', value or 'shot')}")
            if shot:
                record["evidence"]["screenshots"].append(shot)
            return
        if step.action == "press" and step.target is None:
            page.keyboard.press(value or "Enter")
            return
        if step.action not in NEEDS_TARGET and step.action != "press":
            raise StepFailed(index, f"Unsupported action {step.action}.")
        if step.target is None:
            raise StepFailed(index, f"Step {index + 1} ({step.action}) has no target.")

        locator = self._locate(page, step, index, script, act, record, evidence_dir, visual)
        kind = "assert" if step.action.startswith("expect") else VISUAL_KIND.get(step.action, "click")
        box = None
        if visual and step.action != "expect_hidden":
            try:
                locator.scroll_into_view_if_needed(timeout=timeout)
                box = locator.bounding_box(timeout=timeout)
                if box:
                    page.evaluate("([b, k, l]) => window.__qaOverlay && window.__qaOverlay.point(b.x, b.y, b.width, b.height, k, l)",
                                  [box, kind, (step.target.description or step.action)[:60]])
                    page.wait_for_timeout(delay)
            except Exception:
                box = None
        from playwright.sync_api import expect

        a = step.action
        if a == "click":
            if box:
                page.evaluate("([x, y]) => window.__qaOverlay && window.__qaOverlay.click(x, y)",
                              [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])
            locator.click(timeout=timeout)
        elif a == "dblclick":
            locator.dblclick(timeout=timeout)
        elif a in ("fill", "type"):
            locator.fill(value or "", timeout=timeout)
        elif a == "select":
            try:
                locator.select_option(value=value, timeout=timeout)
            except Exception:
                locator.select_option(label=value, timeout=timeout)
        elif a == "check":
            locator.check(timeout=timeout)
        elif a == "uncheck":
            locator.uncheck(timeout=timeout)
        elif a == "hover":
            locator.hover(timeout=timeout)
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
        if visual:
            try:
                page.wait_for_timeout(min(delay, 250))
                page.evaluate("window.__qaOverlay && window.__qaOverlay.clear()")
            except Exception:
                pass  # the page may have navigated

    # ---- locating and healing --------------------------------------------------------

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
        loc = self._try(page, target.selector, step.timeout_ms, state)
        if loc is not None or step.action == "expect_hidden":
            return loc if loc is not None else page.locator(target.selector).first
        for alt in target.alternates:
            loc = self._try(page, alt, 1500, state)
            if loc is not None:
                act.log(f"Primary locator failed; recorded alternate worked: {_short(alt)}", step=index)
                return loc
        healed = self._heal(page, step, index, script, act, record, evidence_dir, visual)
        if healed is None:
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
                result = "healed" if verified is not None else "failed"
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
                    raise RuntimeError(f"No confident alternative locator (threshold {healing.THRESHOLD:.2f}).{best}")
                info = {"healing_id": event_id, "step": index, "original": target.selector,
                        "healed": chosen["selector"], "confidence": chosen["confidence"]}
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
        return saved
