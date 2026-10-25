"""Execution of framework tests: Test Execution Agent -> Python Playwright (pytest) -> browser.

The test runs as a real pytest process in the workspace, exactly as it would on any
other PC. The framework's reporting writes progress records (steps, healing,
evidence) to a JSON-lines file; this runner follows that file live, so Agent
Workflow shows the current step while the browser plays the test, and stores the
result in Execution History with its evidence in the same shape as every other run
(failure analysis, reports and the history page read it unchanged).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ...clock import now_iso
from ...config import Paths, Settings
from ...events import names
from ...runs import RunStore
from ...storage.db import Database, dumps
from ..browser import default_headless, ensure_browsers_path
from ..runner import SelfHealingAgent, TestExecutionAgent
from . import diagnosis

RESULT_TAIL = 4000


class FrameworkRunner:
    def __init__(self, paths: Paths, settings: Settings, db: Database, runs: RunStore,
                 execution: TestExecutionAgent, healer: SelfHealingAgent, analyst: Any = None,
                 on_diagnosis: Callable[[dict[str, Any], dict[str, Any]], None] | None = None) -> None:
        self.paths = paths
        self.settings = settings
        self.db = db
        self.runs = runs
        self.execution = execution
        self.healer = healer
        self.analyst = analyst  # Failure & Quality Analysis Agent
        self.on_diagnosis = on_diagnosis
        self.processes: dict[str, subprocess.Popen[str]] = {}
        self.stopped: set[str] = set()

    def stop(self, run_id: str) -> bool:
        proc = self.processes.get(run_id)
        if proc is None:
            return False
        self.stopped.add(run_id)
        _terminate(proc)
        return True

    def running(self) -> list[str]:
        return list(self.processes)

    def run(self, record: dict[str, Any], run_id: str, *, workspace: Path, env_extra: dict[str, str],
            secrets: list[str], combination: str | None = None, headless: bool | None = None,
            on_finish: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        """Run one framework script. ``record`` is its inventory row; ``env_extra`` carries credentials."""
        playback = self.settings.section("playback")
        headless = default_headless(playback.get("headless")) if headless is None else headless
        visual = bool(playback.get("visual_cursor", True) or playback.get("highlight", True)) and not headless
        evidence_dir = self.paths.evidence / run_id
        evidence_dir.mkdir(parents=True, exist_ok=True)
        events_file = evidence_dir / "progress.jsonl"
        log_file = evidence_dir / "execution.log"
        sid, tc, us = record["id"], record.get("test_case_id"), record.get("user_story_id")
        label = f"Test Case #{tc}" if tc else record.get("test_case_title") or sid
        browser = "chromium"
        title = f"TC{tc} {record.get('test_case_title') or ''}".strip() + f" (Test Case #{tc})" if tc else sid
        self.runs.start(run_id, "functional", title, test_case_id=tc, user_story_id=us)
        started = now_iso()
        row_id = self.db.execute(
            "INSERT INTO execution_results (run_id, script_id, test_case_id, user_story_id, status, started_at, browser,"
            " base_url) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, sid, tc, us, "RUNNING", started, browser, record.get("base_url"))).lastrowid
        result: dict[str, Any] = {
            "run_id": run_id, "script_id": sid, "test_case_id": tc, "user_story_id": us, "status": "RUNNING",
            "started_at": started, "browser": browser, "base_url": record.get("base_url"), "steps": [],
            "evidence": {"folder": str(evidence_dir), "screenshots": [], "video": None, "trace": [],
                         "log": str(log_file), "progress": str(events_file)},
            "failed_step": None, "error": None, "healed": [], "tests": [], "diagnostics": None, "failure_analysis": None,
            "execution_mode": "Background / Headless" if headless else "Visible Browser", "headless": headless,
            "traceability": {"user_story_id": us, "acceptance_criteria": record.get("acceptance_criteria") or [],
                             "test_case_id": tc, "script": record.get("file"), "test_data": record.get("data_file")},
        }
        mask = _masker(secrets)
        state = _Follow(self, result, record, run_id, mask)
        env = {k: v for k, v in os.environ.items() if not k.startswith("QA_")}
        ensure_browsers_path()
        if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
            env["PLAYWRIGHT_BROWSERS_PATH"] = os.environ["PLAYWRIGHT_BROWSERS_PATH"]
        env.update({
            "QA_RUN_ID": run_id, "QA_EVENTS_FILE": str(events_file), "QA_EVIDENCE_DIR": str(evidence_dir),
            "HEADLESS": "1" if headless else "0", "QA_VISUAL": "1" if visual else "0",
            "QA_VISUAL_DELAY_MS": str(int(playback.get("visual_delay_ms", 350))),
            "QA_CURSOR_SPEED_MS": str(int(playback.get("cursor_speed_ms", 450))),
            "QA_HIGHLIGHT": "1" if playback.get("highlight", True) else "0",
            "QA_CLICK_SOUND": "1" if playback.get("click_sound", True) else "0",
            "QA_CLICK_VOLUME": str(float(playback.get("click_volume", 0.35))),
            "QA_TRACE": "1" if playback.get("trace") else "0", "QA_VIDEO": "1" if playback.get("video") else "0",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
        })
        if record.get("base_url"):
            env["BASE_URL"] = record["base_url"]
        if combination:
            env["QA_COMBINATION"] = combination
        env.update(env_extra)
        try:
            with self.execution.activity(f"Running {label}", run_id=run_id, test_case_id=tc, user_story_id=us,
                                         script_id=sid) as act:
                state.act = act
                act.event(names.EXECUTION_STARTED, f"Started {label}: Automation Engineering Agent -> Test Execution "
                                                   f"Agent -> Python Playwright -> {browser}")
                cmd = [sys.executable, "-m", "pytest", record["file"], "-p", "no:cacheprovider", "-q", "-rN",
                       "--junitxml", str(evidence_dir / "junit.xml")]
                with open(log_file, "w", encoding="utf-8") as log:
                    kwargs: dict[str, Any] = {"cwd": str(workspace), "env": env, "stdout": log,
                                              "stderr": subprocess.STDOUT, "text": True}
                    if sys.platform == "win32":
                        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
                    else:
                        kwargs["start_new_session"] = True
                    proc = subprocess.Popen(cmd, **kwargs)
                    self.processes[run_id] = proc
                    while proc.poll() is None:
                        state.follow(events_file)
                        time.sleep(0.2)
                    state.follow(events_file)
                if run_id in self.stopped:
                    _wait_group_gone(proc.pid)  # the browser may outlive pytest by a moment
                code = proc.returncode
                output = mask(log_file.read_text(encoding="utf-8", errors="replace"))
                log_file.write_text(output, encoding="utf-8")
                if run_id in self.stopped:
                    result["status"] = "STOPPED"
                    result["error"] = "Stopped by the user."
                elif code == 0 and result["tests"] and all(t["status"] == "PASSED" for t in result["tests"]):
                    result["status"] = "PASSED"
                else:
                    result["status"] = "FAILED"
                    result["error"] = state.first_error() or _pytest_error(output, code)
                if result["status"] == "FAILED":
                    self._diagnose(result, record, run_id, evidence_dir)
                if result["status"] != "PASSED":
                    raise _RunFailed(result["error"] or "The test failed.")
                act.event(names.EXECUTION_COMPLETED, f"{label} PASSED" +
                          (f" after self-healing {len(result['healed'])} locator(s)" if result["healed"] else ""),
                          link=f"#/history?run={run_id}")
                act.complete(f"PASSED: {label}")
        except _RunFailed:
            failed = result["failed_step"]
            where = f" at step {failed + 1}" if failed is not None else ""
            analysis = result.get("failure_analysis") or {}
            self.execution.emit(names.EXECUTION_FAILED, f"{label} {result['status']}{where}: {result['error']}",
                                run_id=run_id, test_case_id=tc, user_story_id=us,
                                details={"link": f"#/history?run={run_id}", "failed_step": failed, "script_id": sid,
                                         "classification": analysis.get("classification")})
        except Exception as exc:  # the process could not start
            result["status"] = "ERROR"
            result["error"] = mask(f"The test could not run: {exc}")
            self.execution.emit(names.EXECUTION_FAILED, f"{label} could not run: {result['error']}", run_id=run_id,
                                test_case_id=tc, user_story_id=us, details={"link": f"#/history?run={run_id}"})
        finally:
            self.processes.pop(run_id, None)
            self.stopped.discard(run_id)
            result["finished_at"] = now_iso()
            if result["status"] == "PASSED" and result["healed"]:
                self.db.execute("UPDATE healing_events SET result = 'validated' WHERE run_id = ? AND result = 'healed'",
                                (run_id,))
            result["duration_ms"] = _duration_ms(result["started_at"], result["finished_at"])
            (evidence_dir / "result.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
            self.db.execute(
                "UPDATE execution_results SET status = ?, finished_at = ?, failed_step = ?, error = ?, steps = ?,"
                " evidence = ? WHERE id = ?",
                (result["status"], result["finished_at"], result["failed_step"], result["error"],
                 dumps(result["steps"]), dumps(result["evidence"]), row_id))
            self.runs.finish(run_id, result["status"], {
                "script_id": sid, "failed_step": result["failed_step"], "error": result["error"],
                "steps": len(result["steps"]), "healed": len(result["healed"]), "tests": len(result["tests"]),
            })
            if on_finish:
                on_finish(result)
        return result


    def _diagnose(self, result: dict[str, Any], record: dict[str, Any], run_id: str, evidence_dir: Path) -> None:
        """Failure & Quality Analysis Agent: classify the failure from the run's evidence."""
        rows = self.db.query("SELECT * FROM healing_events WHERE run_id = ?", (run_id,))
        for row in rows:
            try:
                row["candidates"] = json.loads(row["candidates"] or "[]")
            except ValueError:
                row["candidates"] = []

        def work(act: Any = None) -> dict[str, Any]:
            if act:
                act.progress(30, "Reading the failed step, expected result, page state, console and network evidence")
            found = diagnosis.diagnose(result, record, rows)
            (evidence_dir / "failure-analysis.json").write_text(json.dumps(found, indent=2, default=str),
                                                                encoding="utf-8")
            if act:
                act.complete(f"Failure analysis: {found['classification']} ({found['confidence']}% confidence)")
            return found

        try:
            if self.analyst is None:
                found = work()
            else:
                with self.analyst.activity(f"Diagnosing failed run of Test Case #{record.get('test_case_id')}",
                                           run_id=run_id, test_case_id=record.get("test_case_id"),
                                           user_story_id=record.get("user_story_id")) as act:
                    found = work(act)
            result["failure_analysis"] = found
        except Exception as exc:  # the diagnosis never hides the run's own result
            result["failure_analysis"] = {"classification": diagnosis.UNKNOWN, "confidence": 0,
                                          "reason": f"The failure analysis could not complete: {exc}"}
            return
        if self.on_diagnosis:
            try:
                self.on_diagnosis(result, found)
            except Exception as exc:
                self.execution.log(f"Defect draft for {run_id} not recorded: {exc}", run_id=run_id)


class _RunFailed(Exception):
    pass


class _Follow:
    """Applies progress records as they arrive: steps, healing, evidence, test results."""

    def __init__(self, runner: FrameworkRunner, result: dict[str, Any], record: dict[str, Any], run_id: str,
                 mask: Callable[[str], str]) -> None:
        self.runner = runner
        self.result = result
        self.record = record
        self.run_id = run_id
        self.mask = mask
        self.offset = 0
        self.act: Any = None
        self.steps: dict[tuple[str | None, int], dict[str, Any]] = {}
        self.healing_started: dict[tuple[str | None, int], Any] = {}

    def follow(self, path: Path) -> None:
        if not path.exists():
            return
        with open(path, "r", encoding="utf-8") as fh:
            fh.seek(self.offset)
            chunk = fh.read()
            if not chunk.endswith("\n"):
                chunk = chunk[:chunk.rfind("\n") + 1]
            self.offset += len(chunk.encode("utf-8"))
        for line in chunk.splitlines():
            try:
                self.apply(json.loads(line))
            except (ValueError, KeyError, TypeError):
                continue

    def _label(self, rec: dict[str, Any], text: str) -> str:
        combo = rec.get("combination")
        multi = len({t.get("combination") for t in self.result["tests"]}) > 1 or (combo and combo != "Combination 1"
                                                                                 and combo != "default")
        return self.mask(f"[{combo}] {text}" if combo and multi else text)

    def apply(self, rec: dict[str, Any]) -> None:
        kind = rec.get("kind")
        r = self.result
        key = (rec.get("test"), int(rec.get("index", rec.get("step", -1)) or 0))
        if kind == "test":
            entry = next((t for t in r["tests"] if t["test"] == rec.get("test")), None)
            if entry is None:
                entry = {"test": rec.get("test"), "combination": rec.get("combination"), "status": "RUNNING"}
                r["tests"].append(entry)
            if rec.get("status") != "RUNNING":
                entry["status"] = rec.get("status")
                entry["error"] = self.mask(rec.get("error") or "") or None
            if rec.get("screenshot"):
                r["evidence"]["screenshots"].append(rec["screenshot"])
            if rec.get("status") == "FAILED" and rec.get("phase") == "setup" and r["error"] is None:
                r["error"] = self.mask(rec.get("error") or "Test setup failed.")
        elif kind == "step":
            entry = self.steps.get(key)
            if entry is None:
                entry = {"index": len(r["steps"]), "action": "step", "description": self._label(rec, rec.get("title") or ""),
                         "status": "RUNNING", "tc_step": rec.get("tc_step"), "checkpoint": rec.get("checkpoint"),
                         "combination": rec.get("combination"), "started_at": rec.get("ts"),
                         "expected": self._expected(rec.get("tc_step"))}
                self.steps[key] = entry
                r["steps"].append(entry)
                total = max(len(self.record.get("flow_steps") or []), 1)
                if self.act:
                    self.act.event(names.EXECUTION_STEP, f"Step {entry['index'] + 1}: {entry['description']}",
                                   progress=min(95, int((key[1]) * 100 / total)), step=entry["index"])
            if rec.get("status") in ("PASSED", "FAILED"):
                entry["status"] = rec["status"]
                entry["duration_ms"] = rec.get("duration_ms")
                entry["finished_at"] = rec.get("ts")
                entry["actual"] = self._actual(entry, rec)
                if rec.get("full_page"):
                    entry["full_page"] = rec["full_page"]
                    r["evidence"]["screenshots"].append(rec["full_page"])
                if rec.get("screenshot"):
                    entry["screenshot"] = rec["screenshot"]
                    r["evidence"]["screenshots"].append(rec["screenshot"])
                if rec["status"] == "FAILED":
                    entry["error"] = self.mask(rec.get("error") or "")
                    if r["failed_step"] is None:
                        r["failed_step"] = entry["index"]
        elif kind == "action":
            entry = self.steps.get(key)
            if entry is not None:
                entry.setdefault("actions", []).append({"action": rec.get("action"),
                                                        "description": self.mask(rec.get("description") or ""),
                                                        "locator": rec.get("locator")})
        elif kind == "healing_started":
            step = self.steps.get(key)
            label = rec.get("element") or rec.get("original")
            act = self.runner.healer.activity(f"Recovering locator for {label}", run_id=self.run_id,
                                              test_case_id=self.record.get("test_case_id"),
                                              user_story_id=self.record.get("user_story_id"))
            h = act.__enter__()
            h.event(names.SELFHEALING_STARTED, f"Original locator failed: {rec.get('original')}")
            h.progress(40, "Inspecting the page DOM for candidate elements")
            self.healing_started[key] = (act, h, step)
        elif kind == "healing":
            self._healing(rec, key)
        elif kind == "evidence":
            if rec.get("screenshot"):
                r["evidence"]["screenshots"].append(rec["screenshot"])
            if rec.get("trace"):
                r["evidence"]["trace"].append(rec["trace"])
            if rec.get("video"):
                r["evidence"]["video"] = rec["video"]
        elif kind == "diagnostics":
            r["diagnostics"] = {k: rec.get(k) for k in ("url", "title", "page_messages", "console", "network", "step")}
        elif kind == "locator" and self.act:
            self.act.log(self.mask(f"{rec.get('note')}: {rec.get('used')}"))

    def _healing(self, rec: dict[str, Any], key: tuple[str | None, int]) -> None:
        r = self.result
        started = self.healing_started.pop(key, None)
        step = self.steps.get(key)
        healed = rec.get("result") == "healed"
        component = self._component(rec.get("original"))
        event = {
            "timestamp": now_iso(), "run_id": self.run_id, "script_id": self.record["id"],
            "test_case_id": self.record.get("test_case_id"), "step_index": step["index"] if step else max(key[1], 0),
            "page_url": rec.get("page_url"), "original_selector": rec.get("original") or "",
            "healed_selector": rec.get("healed"), "confidence": float(rec.get("confidence") or 0.0),
            "candidates": dumps(rec.get("candidates") or []), "screenshot": rec.get("screenshot"),
            "result": "healed" if healed else "failed", "component": component,
        }
        event_id = self.runner.db.execute(
            "INSERT INTO healing_events (timestamp, run_id, script_id, test_case_id, step_index, page_url,"
            " original_selector, healed_selector, confidence, candidates, screenshot, result, component)"
            " VALUES (:timestamp,:run_id,:script_id,:test_case_id,:step_index,:page_url,:original_selector,"
            ":healed_selector,:confidence,:candidates,:screenshot,:result,:component)", event).lastrowid
        if rec.get("screenshot"):
            r["evidence"]["screenshots"].append(rec["screenshot"])
        if healed:
            info = {"healing_id": event_id, "step": event["step_index"], "original": event["original_selector"],
                    "healed": event["healed_selector"], "confidence": event["confidence"], "component": component}
            r["healed"].append(info)
            if step:
                step["healed"] = info
        if started:
            act, h, _ = started
            try:
                if healed:
                    h.event(names.SELFHEALING_COMPLETED, f"Locator healed: {rec.get('healed')} (confidence "
                                                         f"{event['confidence']:.2f})", **{"healing_id": event_id})
                    h.complete(f"Self-healing recovered {rec.get('element')} (confidence {event['confidence']:.2f})")
                    act.__exit__(None, None, None)
                else:
                    reason = rec.get("reason") or f"No confident alternative locator (threshold {rec.get('threshold', 0.62)})."
                    try:
                        raise RuntimeError(reason)
                    except RuntimeError as exc:
                        act.__exit__(type(exc), exc, exc.__traceback__)
            except RuntimeError:
                pass
            if not healed:
                self.runner.healer.emit(names.SELFHEALING_FAILED, f"Self-healing failed: {rec.get('original')}",
                                        run_id=self.run_id, test_case_id=self.record.get("test_case_id"))

    def _component(self, selector: str | None) -> str | None:
        """Page Object locator (Class.NAME) the selector belongs to, from the script's components."""
        for comp in self.record.get("locators") or []:
            if comp.get("selector") == selector:
                return comp.get("component")
        return None

    def _expected(self, tc_step: Any) -> str | None:
        """Expected result of the TFS Test Case step(s) a framework step automates."""
        manual = self.record.get("manual_steps") or []
        numbers = [int(n) for n in str(tc_step or "").split(",") if n.strip().isdigit()]
        found = [manual[n - 1].get("expected") for n in numbers if 0 < n <= len(manual) and manual[n - 1].get("expected")]
        return self.mask(" / ".join(found)) if found else None

    def _actual(self, entry: dict[str, Any], rec: dict[str, Any]) -> str:
        if rec["status"] == "FAILED":
            return self.mask(rec.get("error") or "The step failed.")
        actions = entry.get("actions") or []
        checks = [a["description"] for a in actions if (a.get("action") or "").startswith("expect")]
        if checks:
            return "As expected: " + "; ".join(checks)
        return "Done: " + "; ".join(a["description"] for a in actions) if actions else "Step completed."

    def first_error(self) -> str | None:
        for step in self.result["steps"]:
            if step.get("status") == "FAILED" and step.get("error"):
                return f"{step['description']}: {step['error']}"
        for test in self.result["tests"]:
            if test.get("status") == "FAILED" and test.get("error"):
                return test["error"]
        return None


def _duration_ms(start: str | None, end: str | None) -> int | None:
    from datetime import datetime

    try:
        return int((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000)
    except (TypeError, ValueError):
        return None


def _masker(secrets: list[str]) -> Callable[[str], str]:
    def mask(text: str) -> str:
        if not isinstance(text, str):
            return text
        for secret in secrets:
            if secret:
                text = text.replace(secret, "***")
        return text

    return mask


def _pytest_error(output: str, code: int | None) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    errors = [line[1:].strip() for line in lines if line.startswith("E ")]
    if errors:
        return " ".join(errors[:3])[:500]
    if code == 5:
        return "pytest found no test to run (check the test data combination)."
    return (lines[-1] if lines else f"pytest exited with code {code}.")[:500]


def _terminate(proc: subprocess.Popen[str]) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=30)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass


def _wait_group_gone(pid: int, timeout: float = 8.0) -> None:
    """After Stop, wait for every process of the run (pytest, browser) to exit; force it after ``timeout``."""
    if sys.platform == "win32":
        return  # taskkill /T /F has already ended the whole tree
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            os.killpg(pid, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass
