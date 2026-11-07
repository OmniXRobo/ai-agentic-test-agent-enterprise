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
from . import visual as visual_fx
from .browser import default_headless, js, launch, start_playwright
from . import files as filekit
from .dsl import describe, FILE_CHECKS, NEEDS_TARGET, Script, Step, ValueResolver
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
            headless: bool | None = None, data_label: str | None = None, browser: str | None = None) -> dict[str, Any]:
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
            ctx_opts: dict[str, Any] = {"viewport": {"width": 1366, "height": 768}, "ignore_https_errors": True,
                                        "accept_downloads": True}
            if video:
                ctx_opts["record_video_dir"] = str(evidence_dir)
            context = browser.new_context(**ctx_opts)
            context.add_init_script(js("fingerprint"))
            context.add_init_script(js("calendar"))
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
                    self._step(page, script, step, index, act, resolver, record, evidence_dir, visual, delay)
                    entry["status"] = "PASSED"
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

        locator = self._locate(page, step, index, script, act, record, evidence_dir, visual)
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
            locator.fill(value or "", timeout=timeout)
        elif a == "select":
            if box:
                visual_fx.choose(page, locator, box, value or "")
            try:
                locator.select_option(value=value, timeout=timeout)
            except Exception:
                locator.select_option(label=value, timeout=timeout)
        elif a == "check":
            if box:
                visual_fx.click(page, box)
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
        if target.frames:  # self-healing reads the page's own elements; inside an iframe only alternates are tried
            raise StepFailed(index, f"Element not found in the frame: {target.description or target.selector} "
                                    f"(selector {_short(target.selector)}).")
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
