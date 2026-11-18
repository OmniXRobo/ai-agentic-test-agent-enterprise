"""Editable Python tests (23.11.0): the "Python Test (Pytest + Playwright)" of a Guided Browser script.

The Python file is generated from the recorded steps, as before. A person allowed to (``automation.python_roles``,
default Admin and Tester) can edit it, validate it, format it and save it; every save is a version that can be
restored. Once edited, the saved version is the one SkyGuard runs and the one that is downloaded; the recorded steps
and the TFS links of the script stay as they are. Regenerating from the steps replaces the edits only after the user
confirms.

Safety: a script is parsed (syntax errors with line and column) and checked before it is saved or run. Only the
modules a browser test needs can be imported, and calls that run programs, open network sockets, load native code,
evaluate code or delete files are refused. This check is a guard against mistakes and obvious misuse, not a sandbox:
only people with the permission can save and run Python, and the test runs with the rights of the Windows user. It runs in its own Python process (pytest) in the run's evidence folder,
with a time limit. On failure a screenshot and the Playwright trace are kept (no trace when the test typed a
credential, because a trace records typed values) and a defect draft is made for the Defect Review.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

from .. import auth
from ..clock import now_iso
from ..errors import QAError
from ..events import names
from ..logging_setup import get_logger
from ..agents.base import new_run_id
from . import pytest_export

log = get_logger("automation.python")

ALLOWED_IMPORTS = {
    "__future__", "base64", "calendar", "collections", "csv", "dataclasses", "datetime", "decimal", "enum", "fnmatch",
    "functools", "hashlib", "io", "itertools", "json", "logging", "math", "openpyxl", "os", "os.path", "pathlib",
    "playwright", "pypdf", "pytest", "random", "re", "statistics", "string", "sys", "textwrap", "time", "typing",
    "unicodedata", "urllib", "urllib.parse", "uuid", "zipfile", "xml", "xml.etree", "xml.etree.ElementTree",
}
BLOCKED_CALLS = {
    "eval", "exec", "compile", "__import__", "breakpoint", "input",
    "os.system", "os.popen", "os.remove", "os.unlink", "os.rmdir", "os.removedirs", "os.rename", "os.replace",
    "os.kill", "os.startfile", "os.chmod", "os.chown", "os.putenv", "os.fork", "os.forkpty",
    "shutil.rmtree", "shutil.move", "pathlib.Path.unlink", "importlib.import_module",
}
BLOCKED_PREFIXES = ("os.exec", "os.spawn", "os.posix_spawn")
MAX_SOURCE = 400_000


def validate(source: str, allowed: set[str] | None = None, browser: bool = True) -> dict[str, Any]:
    """{ok, errors: [{line, column, message, kind}]}. kind is "syntax" or "policy". 23.12.0: ``allowed`` and
    ``browser`` let API Testing check its scripts with its own import list (httpx, no Playwright)."""
    allowed = ALLOWED_IMPORTS if allowed is None else allowed
    ok_pkgs = ("playwright",) if browser else ()
    errors: list[dict[str, Any]] = []
    if len(source) > MAX_SOURCE:
        return {"ok": False, "errors": [{"line": 1, "column": 1, "kind": "policy",
                                         "message": f"The script is larger than {MAX_SOURCE // 1000} KB."}]}
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"ok": False, "errors": [{"line": exc.lineno or 1, "column": exc.offset or 1, "kind": "syntax",
                                         "message": f"{exc.__class__.__name__}: {exc.msg}"}]}

    def name_of(node: ast.AST) -> str:
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts))

    for node in ast.walk(tree):
        line, col = getattr(node, "lineno", 1), getattr(node, "col_offset", 0) + 1
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in allowed and alias.name.split(".")[0] not in ok_pkgs:
                    errors.append({"line": line, "column": col, "kind": "policy",
                                   "message": f"import {alias.name} is not allowed in a SkyGuard test."})
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level or (mod not in allowed and mod.split(".")[0] not in ok_pkgs):
                errors.append({"line": line, "column": col, "kind": "policy",
                               "message": f"from {mod or '.'} import ... is not allowed in a SkyGuard test."})
        elif isinstance(node, ast.Call):
            called = name_of(node.func)
            if called in BLOCKED_CALLS or called.startswith(BLOCKED_PREFIXES):
                errors.append({"line": line, "column": col, "kind": "policy",
                               "message": f"{called}() is not allowed in a SkyGuard test."})
        elif isinstance(node, ast.Attribute) and name_of(node) in ("sys.modules", "sys.settrace", "sys.setprofile",
                                                                  "os.environ.clear"):
            errors.append({"line": line, "column": col, "kind": "policy",
                           "message": f"{name_of(node)} is not allowed in a SkyGuard test."})
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__") and \
                node.attr not in ("__name__", "__file__", "__init__", "__doc__"):
            errors.append({"line": line, "column": col, "kind": "policy",
                           "message": f"Access to {node.attr} is not allowed in a SkyGuard test."})
    errors.sort(key=lambda e: (e["line"], e["column"]))
    return {"ok": not errors, "errors": errors[:50]}


def format_source(source: str) -> str:
    """Tabs to four spaces, no trailing spaces, at most two blank lines, one final newline. The code is not
    rewritten (no formatter is bundled offline); a syntax error is left for Validate to show."""
    lines = [ln.expandtabs(4).rstrip() for ln in source.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out: list[str] = []
    blank = 0
    for ln in lines:
        blank = blank + 1 if not ln else 0
        if blank <= 2:
            out.append(ln)
    return "\n".join(out).strip("\n") + "\n"


CONFTEST = '''"""SkyGuard: evidence of a failed test (screenshot and Playwright trace). Written by SkyGuard for this run."""
import os

import pytest

_DIR = os.path.dirname(os.path.abspath(__file__))
_TRACE = os.environ.get("SKYGUARD_TRACE") == "1"


@pytest.fixture(autouse=True)
def _skyguard_evidence(request):
    page = request.getfixturevalue("page") if "page" in request.fixturenames else None
    if page is not None and _TRACE:
        try:
            page.context.tracing.start(screenshots=True, snapshots=True)
        except Exception:
            pass
    yield
    rep = getattr(request.node, "rep_call", None)
    if page is None:
        return
    name = "".join(c if c.isalnum() else "_" for c in request.node.name)[:80]
    failed = rep is not None and rep.failed
    if failed:
        try:
            page.screenshot(path=os.path.join(_DIR, f"failure_{name}.png"), full_page=False)
        except Exception:
            pass
    if _TRACE:
        try:
            page.context.tracing.stop(path=os.path.join(_DIR, f"trace_{name}.zip") if failed else None)
        except Exception:
            pass


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call":
        item.rep_call = rep
'''


class PythonScripts:
    def __init__(self, automation: Any, db: Any, paths: Any, settings: Any, bus_agent: Any) -> None:
        self.automation = automation
        self.db = db
        self.paths = paths
        self.settings = settings
        self.agent = bus_agent
        self.datasets: Callable[[Any], list[dict[str, Any]]] = lambda script: []  # set by the API (TFS test data)
        self.defects: Any = None
        self._lock = threading.Lock()

    # ---- permissions ---------------------------------------------------------------------------------------------

    def _actor(self, what: str) -> str:
        user = auth.current_user() or auth.LOCAL_PROGRAM
        roles = set(self.settings.section("automation").get("python_roles") or auth.FULL_ROLES)
        if user.role not in roles or user.view_only:
            raise QAError("PYTHON_NOT_ALLOWED", f"Your account ({auth.ROLES.get(user.role, user.role)}) may not {what} "
                          "Python tests.", status=403)
        return user.username

    # ---- versions --------------------------------------------------------------------------------------------------

    def state(self, script_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM script_python_state WHERE script_id = ?", (script_id,))
        return row or {"script_id": script_id, "active_version": None, "edited": 0}

    def is_edited(self, script_id: str) -> bool:
        return bool(self.state(script_id).get("edited"))

    def generated(self, script_id: str) -> str:
        script = self.automation.scripts.get(script_id)
        return pytest_export.to_pytest(script, self.datasets(script))

    def current(self, script_id: str) -> dict[str, Any]:
        """The Python that runs and is downloaded: the saved edit, or the one generated from the steps."""
        script = self.automation.scripts.get(script_id)
        st = self.state(script_id)
        if st.get("edited") and st.get("active_version"):
            row = self.db.query_one("SELECT * FROM script_python WHERE script_id = ? AND version = ?",
                                    (script_id, st["active_version"]))
            if row:
                return {"source": row["source"], "edited": True, "version": row["version"],
                        "steps_version": row["steps_version"], "steps_changed": row["steps_version"] != script.version,
                        "file_name": pytest_export.file_name(script), "sha256": row["sha256"]}
        src = pytest_export.to_pytest(script, self.datasets(script))
        return {"source": src, "edited": False, "version": None, "steps_version": script.version, "steps_changed": False,
                "file_name": pytest_export.file_name(script), "sha256": hashlib.sha256(src.encode()).hexdigest()}

    def versions(self, script_id: str) -> list[dict[str, Any]]:
        return self.db.query("SELECT version, sha256, steps_version, note, created_by, created_at FROM script_python "
                             "WHERE script_id = ? ORDER BY version DESC", (script_id,))

    def _store(self, script_id: str, source: str, note: str, by: str, edited: bool) -> dict[str, Any]:
        script = self.automation.scripts.get(script_id)
        with self._lock:
            row = self.db.query_one("SELECT MAX(version) AS v FROM script_python WHERE script_id = ?", (script_id,))
            version = int((row or {}).get("v") or 0) + 1
            ts = now_iso()
            self.db.execute("INSERT INTO script_python (script_id, version, source, sha256, steps_version, note,"
                            " created_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
                            (script_id, version, source, hashlib.sha256(source.encode()).hexdigest(), script.version,
                             note[:300], by, ts))
            self.db.execute("INSERT INTO script_python_state (script_id, active_version, edited, updated_at) VALUES"
                            " (?,?,?,?) ON CONFLICT(script_id) DO UPDATE SET active_version = excluded.active_version,"
                            " edited = excluded.edited, updated_at = excluded.updated_at",
                            (script_id, version, int(edited), ts))
        return self.current(script_id)

    def save(self, script_id: str, source: str, note: str = "") -> dict[str, Any]:
        by = self._actor("edit")
        source = source.replace("\r\n", "\n")
        result = validate(source)
        if not result["ok"]:
            first = result["errors"][0]
            raise QAError("PYTHON_INVALID", f"Line {first['line']}, column {first['column']}: {first['message']}",
                          status=422, details=result)
        return self._store(script_id, source, note or "edited", by, True)

    def restore(self, script_id: str, version: int) -> dict[str, Any]:
        by = self._actor("edit")
        row = self.db.query_one("SELECT source FROM script_python WHERE script_id = ? AND version = ?",
                                (script_id, int(version)))
        if not row:
            raise QAError("PYTHON_VERSION_NOT_FOUND", f"Python version {version} does not exist.", status=404)
        return self._store(script_id, row["source"], f"restored v{version}", by, True)

    def revert(self, script_id: str) -> dict[str, Any]:
        """Use the Python generated from the recorded steps again (the edits stay in the history)."""
        by = self._actor("edit")
        self.automation.scripts.get(script_id)
        self.db.execute("INSERT INTO script_python_state (script_id, active_version, edited, updated_at) VALUES"
                        " (?,NULL,0,?) ON CONFLICT(script_id) DO UPDATE SET active_version = NULL, edited = 0,"
                        " updated_at = excluded.updated_at", (script_id, now_iso()))
        log.info(f"Python of {script_id} reverted to the generated test by {by}")
        return self.current(script_id)

    def regenerate(self, script_id: str, confirm: bool = False) -> dict[str, Any]:
        by = self._actor("edit")
        if self.is_edited(script_id) and not confirm:
            raise QAError("PYTHON_EDITED", "This Python test was edited by hand. Regenerating from the recorded steps "
                          "replaces those edits (they stay in Version History). Confirm to continue.", status=409)
        return self._store(script_id, self.generated(script_id), "regenerated from the recorded steps", by, False)

    # ---- run -------------------------------------------------------------------------------------------------------

    def run(self, script_id: str, *, headless: bool | None = None, background: bool = True,
            run_id: str | None = None) -> str | dict[str, Any]:
        self._actor("run")
        cur = self.current(script_id)
        check = validate(cur["source"])
        if not check["ok"]:
            first = check["errors"][0]
            raise QAError("PYTHON_INVALID", f"Line {first['line']}: {first['message']}", status=422, details=check)
        run_id = run_id or new_run_id("RUN")
        if not background:
            return self._run(script_id, run_id, cur, headless)
        threading.Thread(target=lambda: self._safe_run(script_id, run_id, cur, headless), daemon=True,
                         name=f"python-{run_id}").start()
        return run_id

    def _safe_run(self, *args: Any) -> None:
        try:
            self._run(*args)
        except Exception:
            log.exception("Python test run failed to start")

    def _env(self, script: Any, source: str, headless: bool) -> tuple[dict[str, str], list[str]]:
        env = {k: v for k, v in os.environ.items() if not k.upper().endswith(("_PASSWORD", "_TOKEN", "_SECRET"))}
        env.update({"HEADLESS": "1" if headless else "0", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"})
        if script.base_url:
            env["BASE_URL"] = script.base_url
        secrets: list[str] = []
        for role, field in set(pytest_export._CRED.findall(source)):
            value = self.automation.creds.lookup(script.application, role, field)
            if value:
                env[pytest_export._env_name(role, field)] = value
                if field != "username":
                    secrets.append(value)
        env["SKYGUARD_TRACE"] = "0" if secrets else "1"
        return env, secrets

    def _run(self, script_id: str, run_id: str, cur: dict[str, Any], headless: bool | None) -> dict[str, Any]:
        from .browser import default_headless

        script = self.automation.scripts.get(script_id)
        playback = self.settings.section("playback")
        headless = default_headless(playback.get("headless")) if headless is None else headless
        folder = self.paths.evidence / run_id
        folder.mkdir(parents=True, exist_ok=True)
        test_file = folder / cur["file_name"]
        test_file.write_text(cur["source"], encoding="utf-8")
        (folder / "conftest.py").write_text(CONFTEST, encoding="utf-8")
        env, secrets = self._env(script, cur["source"], headless)
        typed_secret = bool(secrets)
        tc, us = script.test_case_id, script.user_story_id
        label = (f"Test Case #{tc}" if tc else script.name) + " (Python)"
        started = now_iso()
        row_id = self.db.execute(
            "INSERT INTO execution_results (run_id, script_id, test_case_id, user_story_id, status, started_at, browser,"
            " base_url) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, script.id, tc, us, "RUNNING", started, script.browser, script.base_url)).lastrowid
        self.automation.runner.runs.start(run_id, "functional", label, test_case_id=tc, user_story_id=us)
        self.agent.emit(names.EXECUTION_STARTED, f"Started {label}", run_id=run_id, test_case_id=tc)
        timeout = int(self.settings.section("automation").get("python_timeout_seconds") or 900)
        junit = folder / "junit.xml"
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(folder),
               f"--junitxml={junit}", "-o", "junit_family=xunit2", str(test_file)]
        try:
            proc = subprocess.run(cmd, cwd=str(folder), env=env, capture_output=True, text=True, timeout=timeout,
                                  encoding="utf-8", errors="replace")
            output, code = (proc.stdout or "") + (proc.stderr or ""), proc.returncode
        except subprocess.TimeoutExpired as exc:
            output, code = f"{exc.stdout or ''}\nThe test ran longer than {timeout} seconds and was stopped.", -1
        for value in secrets:  # nothing used as a secret is kept in the log
            output = output.replace(value, "****")
        (folder / "pytest-output.txt").write_text(output, encoding="utf-8")
        cases = self._cases(junit)
        status = "PASSED" if code == 0 and cases and all(c["status"] == "PASSED" for c in cases) else "FAILED"
        failed = next((c for c in cases if c["status"] == "FAILED"), None)
        shots = sorted(str(p) for p in folder.glob("failure_*.png"))
        traces = sorted(str(p) for p in folder.glob("trace_*.zip"))
        detail = self._failure_detail(failed, cur["source"], cur["file_name"], output) if status == "FAILED" else {}
        evidence = {"folder": str(folder), "screenshots": shots, "video": None, "downloads": [],
                    "log": str(folder / "pytest-output.txt"), "python_version": cur["version"],
                    "python_sha256": cur["sha256"], "trace": traces[0] if traces else None,
                    "traceback": detail.get("traceback"), "dataset": (failed or {}).get("dataset")}
        if status == "FAILED" and typed_secret:
            evidence["trace_note"] = "No Playwright trace was kept: this test typed a password or another secret."
        steps = [{"index": i, "description": f"{c['name']}" + (f" [{c['dataset']}]" if c.get("dataset") else ""),
                  "status": c["status"], "error": c.get("message")} for i, c in enumerate(cases)]
        error = detail.get("error") or (None if status == "PASSED" else (output.strip().splitlines() or ["The test failed."])[-1][:400])
        finished = now_iso()
        self.db.execute("UPDATE execution_results SET status = ?, finished_at = ?, failed_step = ?, error = ?, steps = ?,"
                        " evidence = ? WHERE id = ?", (status, finished, detail.get("step_index"), error,
                                                       json.dumps(steps), json.dumps(evidence), row_id))
        self.automation.runner.runs.finish(run_id, status, {"script_id": script.id, "python": True, "error": error})
        record = {"run_id": run_id, "script_id": script.id, "status": status, "error": error, "steps": steps,
                  "evidence": evidence, "failed_step": detail.get("step_index"), "python_version": cur["version"],
                  "started_at": started, "finished_at": finished, "output": output[-4000:]}
        if status == "PASSED":
            self.agent.emit(names.EXECUTION_COMPLETED, f"{label} PASSED", run_id=run_id, test_case_id=tc,
                            details={"link": f"#/history?run={run_id}"})
        else:
            self.agent.emit(names.EXECUTION_FAILED, f"{label} FAILED: {error}", run_id=run_id, test_case_id=tc,
                            details={"link": f"#/history?run={run_id}", "script_id": script.id, "python": True})
            self._draft(script, record, detail, cur)
        return record

    @staticmethod
    def _cases(junit: Path) -> list[dict[str, Any]]:
        if not junit.is_file():
            return []
        try:
            root = ET.parse(junit).getroot()
        except ET.ParseError:
            return []
        out = []
        for case in root.iter("testcase"):
            name = case.get("name") or "test"
            m = re.match(r"^(.*?)\[(.*)\]$", name)
            fail = case.find("failure")
            if fail is None:
                fail = case.find("error")
            out.append({"name": m.group(1) if m else name, "dataset": m.group(2) if m else None,
                        "status": "FAILED" if fail is not None else ("SKIPPED" if case.find("skipped") is not None
                                                                     else "PASSED"),
                        "message": (fail.get("message") or "")[:600] if fail is not None else None,
                        "traceback": (fail.text or "")[-6000:] if fail is not None else None})
        return out

    @staticmethod
    def _failure_detail(case: dict[str, Any] | None, source: str, file_name: str, output: str) -> dict[str, Any]:
        tb = (case or {}).get("traceback") or output[-6000:]
        lines = source.splitlines()
        line_no = None
        for m in re.finditer(rf"{re.escape(file_name)}:(\d+)", tb):
            line_no = int(m.group(1))  # the last frame in the test file
        step_desc, step_index = None, None
        if line_no:
            for i in range(min(line_no, len(lines)) - 1, -1, -1):
                m = re.match(r"\s*# Step (\d+): (.*)$", lines[i])
                if m:
                    step_index, step_desc = int(m.group(1)) - 1, m.group(2)
                    break
        msg = ((case or {}).get("message") or "").strip() or (tb.strip().splitlines() or [""])[-1]
        return {"traceback": tb, "line": line_no, "code": lines[line_no - 1].strip() if line_no and
                line_no <= len(lines) else None, "step": step_desc, "step_index": step_index,
                "error": msg.splitlines()[0][:400] if msg else None}

    def _draft(self, script: Any, record: dict[str, Any], detail: dict[str, Any], cur: dict[str, Any]) -> None:
        if self.defects is None or self.defects.mode == "disabled":
            return
        from ..defects import analysis, triage

        tb = triage.redact(detail.get("traceback") or "", 6000)
        err = triage.redact(detail.get("error") or record.get("error") or "", 600)
        category = ("assertion" if re.search(r"AssertionError|expect\(|to_(have|contain|be)_", tb) else "interaction")
        if re.search(r"\b(SyntaxError|NameError|AttributeError|TypeError|ImportError|IndentationError)\b", tb):
            category = "interaction"
        product, severity, finding = analysis.CATEGORIES[category]
        where = f"line {detail['line']}" if detail.get("line") else "the test"
        what = detail.get("step") or (f"code at {where}")
        facts = {"error": err, "traceback": tb, "action": "expect_text" if category == "assertion" else None,
                 "failed_step": detail.get("step_index"), "failed_step_description": what,
                 "script_id": script.id, "script_version": f"steps v{script.version}, Python v{cur['version'] or 'generated'}",
                 "python_line": detail.get("line"), "python_code": detail.get("code"), "run_id": record["run_id"],
                 "test_case_id": script.test_case_id, "dataset": record["evidence"].get("dataset"),
                 "browser": script.browser, "base_url": script.base_url, "finding": finding,
                 "trace_note": record["evidence"].get("trace_note")}
        esc = analysis.html.escape
        repro = (f"<p><strong>Observed:</strong> {esc(finding)}</p>"
                 f"<p><strong>Python test</strong> {esc(cur['file_name'])} (script {esc(script.id)}, "
                 f"{esc(facts['script_version'])}) failed at {esc(where)}"
                 + (f": <code>{esc(detail['code'])}</code>" if detail.get("code") else "") + "</p>"
                 + (f"<p><strong>Step:</strong> {esc(detail['step'])}</p>" if detail.get("step") else "")
                 + f"<p><strong>Error:</strong> {esc(err)}</p>"
                 + f"<p><strong>Environment:</strong> {esc(script.base_url or '')} · {esc(script.browser)} · run "
                   f"{esc(record['run_id'])}" + (f" · data set {esc(str(facts['dataset']))}" if facts["dataset"] else "")
                 + "</p><p><strong>Root cause:</strong> not determined from the automated evidence.</p>")
        evidence = [{"kind": "screenshot", "path": p} for p in record["evidence"]["screenshots"]]
        if record["evidence"].get("trace"):
            evidence.append({"kind": "trace", "path": record["evidence"]["trace"]})
        evidence.append({"kind": "log", "path": record["evidence"]["log"]})
        draft = {"source": "python", "source_run_id": record["run_id"],
                 "signature": analysis.signature("python", script.id, detail.get("line"), category, err[:80]),
                 "category": category, "product_defect": product, "severity": severity,
                 "priority": 2 if severity <= 2 else 3,
                 "title": f"{script.name}: {what} failed"[:250], "repro_steps": repro, "facts": facts,
                 "evidence": evidence, "test_case_id": script.test_case_id, "user_story_id": script.user_story_id}
        try:
            self.defects.record_draft(draft)
        except Exception:
            log.exception("The defect draft of a Python test run could not be saved")
