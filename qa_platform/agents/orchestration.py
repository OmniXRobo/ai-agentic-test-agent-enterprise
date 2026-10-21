"""QA Orchestration Agent.

Stage 1 capability: real environment diagnostics for the Enterprise QA Runtime.
Every check inspects the actual machine; nothing is assumed.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import httpx

from ..config import Paths, Settings
from ..events import names
from ..runs import RunStore
from ..storage.db import Database
from .base import Activity, BaseAgent, new_run_id

Check = dict[str, Any]


def _check(name: str, status: str, detail: str, **extra: Any) -> Check:
    return {"name": name, "status": status, "detail": detail, **extra}


def _which_in(home: str, *candidates: str) -> str | None:
    if home:
        for c in candidates:
            p = Path(home) / "bin" / c
            if p.exists():
                return str(p)
    for c in candidates:
        found = shutil.which(c)
        if found:
            return found
    return None


class OrchestrationAgent(BaseAgent):
    agent_id = "orchestration"

    def __init__(self, bus, db: Database, settings: Settings, paths: Paths, runs: RunStore) -> None:
        super().__init__(bus)
        self.db = db
        self.settings = settings
        self.paths = paths
        self.runs = runs

    # ---- individual checks -------------------------------------------------

    def check_python(self) -> Check:
        v = sys.version_info
        ok = v >= (3, 10)
        return _check("Python runtime", "PASS" if ok else "FAIL",
                      f"Python {v.major}.{v.minor}.{v.micro} at {sys.executable}"
                      + ("" if ok else " (3.10 or newer is required)"))

    def check_data_dir(self) -> Check:
        probe = self.paths.root / ".write_probe"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return _check("Data directory", "PASS", f"Writable: {self.paths.root}")
        except OSError as exc:
            return _check("Data directory", "FAIL", f"Not writable: {self.paths.root} ({exc})")

    def check_database(self) -> Check:
        row = self.db.query_one("PRAGMA quick_check")
        result = next(iter(row.values())) if row else "no result"
        return _check("Database", "PASS" if result == "ok" else "FAIL", f"SQLite quick_check: {result}")

    def check_playwright(self) -> Check:
        if importlib.util.find_spec("playwright") is None:
            return _check("Playwright", "WARN", "Python package 'playwright' is not installed (needed from Stage 4).")
        browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        if browsers and browsers != "0":
            location = Path(browsers)
        elif sys.platform == "win32":
            location = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
        else:
            location = Path.home() / ".cache" / "ms-playwright"
        if location.exists() and any(location.iterdir()):
            return _check("Playwright", "PASS", f"Package installed; browsers at {location}")
        return _check("Playwright", "WARN", f"Package installed but no browser binaries found at {location}")

    def check_java(self) -> Check:
        java_home = self.settings.section("performance").get("java_home") or os.environ.get("JAVA_HOME", "")
        exe = _which_in(java_home, "java.exe", "java")
        if not exe:
            return _check("Java runtime", "WARN", "Java not found (required by JMeter from Stage 5).")
        try:
            out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=15)
            first = (out.stderr or out.stdout).strip().splitlines()
            version = next((ln for ln in first if "version" in ln), first[0] if first else "unknown")
            return _check("Java runtime", "PASS", f"{version} ({exe})")
        except (OSError, subprocess.SubprocessError) as exc:
            return _check("Java runtime", "FAIL", f"{exe} could not run: {exc}")

    def check_jmeter(self) -> Check:
        jmeter_home = self.settings.section("performance").get("jmeter_home") or os.environ.get("JMETER_HOME", "")
        exe = _which_in(jmeter_home, "jmeter.bat", "jmeter")
        if not exe:
            return _check("JMeter", "WARN", "JMeter not found (configure JMeter home; needed from Stage 5).")
        return _check("JMeter", "PASS", f"Found {exe}")

    def check_ai(self) -> Check:
        ai = self.settings.section("ai")
        base = (ai.get("base_url") or "").rstrip("/")
        if not base:
            return _check("On-Premise AI", "WARN", "No On-Premise AI server URL configured.")
        try:
            resp = httpx.get(f"{base}/models", timeout=5.0, trust_env=False)
        except httpx.HTTPError as exc:
            return _check("On-Premise AI", "FAIL", f"Server not reachable at {base}: {type(exc).__name__}",
                          event=names.AI_UNAVAILABLE)
        if resp.status_code != 200:
            return _check("On-Premise AI", "FAIL", f"{base}/models returned HTTP {resp.status_code}",
                          event=names.AI_UNAVAILABLE)
        try:
            models = [m.get("id") for m in resp.json().get("data", [])]
        except ValueError:
            models = []
        wanted = ai.get("model")
        if wanted and wanted not in models:
            return _check("On-Premise AI", "FAIL",
                          f"Server reachable, but model '{wanted}' is not loaded (loaded: {', '.join(models) or 'none'})")
        return _check("On-Premise AI", "PASS", f"Server reachable at {base}; models: {', '.join(models) or 'none reported'}")

    def check_tfs_profile(self) -> Check:
        tfs = self.settings.section("tfs")
        mode = tfs.get("active_mode", "online")
        profile = tfs.get(mode, {})
        url = profile.get("organization_url") or profile.get("collection_url")
        label = "Azure DevOps Online" if mode == "online" else "Local TFS / Azure DevOps Server"
        if not url or not profile.get("project"):
            return _check("TFS profile", "WARN", f"Active profile ({label}) has no server URL or project yet.")
        return _check("TFS profile", "PASS", f"Active profile: {label} at {url}, project {profile['project']}."
                      " Use TFS Connection > Test connection for the full server check.")

    # ---- run ---------------------------------------------------------------

    def checks(self) -> list[tuple[str, Callable[[], Check]]]:
        return [
            ("Checking Python runtime", self.check_python),
            ("Checking data directory", self.check_data_dir),
            ("Checking database", self.check_database),
            ("Checking Playwright", self.check_playwright),
            ("Checking Java runtime", self.check_java),
            ("Checking JMeter", self.check_jmeter),
            ("Checking On-Premise AI server", self.check_ai),
            ("Checking active TFS profile", self.check_tfs_profile),
        ]

    def run_diagnostics(self, run_id: str | None = None) -> dict[str, Any]:
        run_id = run_id or new_run_id("DIAG")
        self.runs.start(run_id, "diagnostics", "System diagnostics")
        results: list[Check] = []
        try:
            with self.activity("Running Enterprise QA Runtime diagnostics", run_id=run_id) as act:
                act.event(names.SYSTEM_DIAGNOSTICS_STARTED, "System diagnostics started")
                steps = self.checks()
                for index, (label, fn) in enumerate(steps):
                    act.progress(int(index * 100 / len(steps)), label)
                    results.append(self._safe(fn, label))
                    self._emit_side_events(act, results[-1])
                failed = [r for r in results if r["status"] == "FAIL"]
                summary = f"Diagnostics finished: {len(results) - len(failed)} of {len(results)} checks without failure"
                act.event(names.SYSTEM_DIAGNOSTICS_COMPLETED, summary, failed=len(failed), checks=results)
                act.complete(f"{self.name} PASSED: {len(failed)} check(s) failed" if failed
                             else f"{self.name} PASSED: all checks passed", failed=len(failed))
        except Exception as exc:
            self.runs.finish(run_id, "FAILED", {"checks": results, "error": str(exc)})
            raise
        status = "FAILED" if any(r["status"] == "FAIL" for r in results) else "PASSED"
        self.runs.finish(run_id, status, {"checks": results})
        return {"run_id": run_id, "status": status, "checks": results}

    @staticmethod
    def _safe(fn: Callable[[], Check], label: str) -> Check:
        try:
            return fn()
        except Exception as exc:  # a broken check is reported, not hidden
            return _check(label.replace("Checking ", "").capitalize(), "FAIL", f"Check crashed: {exc}")

    @staticmethod
    def _emit_side_events(act: Activity, result: Check) -> None:
        event = result.pop("event", None)
        if event:
            act.event(event, result["detail"])
