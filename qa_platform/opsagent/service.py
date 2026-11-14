"""SkyGuard QA side of the Adaptive AI Application Learning & Operations Agent.

Reuses what SkyGuard already has instead of adding a second stack:
* the signed-in SkyGuard user (no second login) and its roles (view-only accounts cannot run or change anything),
* the AI gateway, i.e. the On-Premise Qwen model behind llama-server (no second model process),
* the Faster-Whisper / built-in speech recognizer of the Voice page (local, no cloud),
* Windows Credential Manager through SkyGuard's secret store (namespace ``opsagent``),
* the Browser Manager (the browser chosen in Preferences) and Playwright.

The knowledge database is its own SQLite file, ``<data>/opsagent/knowledge.sqlite3``, so the agent's migrations never
touch SkyGuard's main database and rollback of the patch leaves SkyGuard's data exactly as it was.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from skyguard_agent.agent import OperationsAgent
from skyguard_agent.llm import CallableLLM
from skyguard_agent.util import AgentError
from skyguard_agent.vault import SecretStoreBackend, Vault

from ..logging_setup import get_logger

log = get_logger("opsagent")

DEFAULTS = {"headless": False, "browser": None, "use_ai": True, "max_discovery_pages": 25}


class _BrowserJob:
    """One Playwright thread: an execution, a discovery or a guided-learning session (browser stays open on pause)."""

    def __init__(self, kind: str, ref: str) -> None:
        self.kind = kind
        self.ref = ref
        self.stop = threading.Event()
        self.finish = threading.Event()
        self.driver: Any = None
        self.thread: threading.Thread | None = None
        self.error: str | None = None
        self.result: Any = None


class OpsAgentService:
    def __init__(self, rt: Any) -> None:
        self.rt = rt
        self.folder = Path(rt.paths.root) / "opsagent"
        try:
            vault = Vault(SecretStoreBackend(rt.secrets)) if rt.secrets.available else Vault(None)
        except AgentError:
            vault = Vault(None)
        self.agent = OperationsAgent(self.folder, vault=vault, llm=self._llm(), transcriber=self._transcribe,
                                     interface="skyguard")
        self.jobs: dict[str, _BrowserJob] = {}
        self._lock = threading.Lock()

    # ---- reuse of SkyGuard components ------------------------------------------------------------------------

    def config(self) -> dict[str, Any]:
        return {**DEFAULTS, **(self.rt.settings.section("opsagent") or {})}

    def _llm(self) -> CallableLLM:
        ai = self.rt.ai

        def call(messages: list[dict[str, str]], *, max_tokens: int = 600, json_mode: bool = False) -> str:
            from ..errors import QAError

            try:
                return ai.chat(messages, json_mode=json_mode, max_tokens=max_tokens, timeout=180)
            except QAError as exc:
                raise AgentError("LLM_UNAVAILABLE", exc.message if hasattr(exc, "message") else str(exc), 503) from exc

        return CallableLLM(call, name=f"SkyGuard AI gateway ({ai.label})")

    def _transcribe(self, audio: bytes, content_type: str) -> str:
        return self.rt.voice.transcribe(audio, content_type)

    # ---- browser jobs ----------------------------------------------------------------------------------------

    def _launch(self, job: _BrowserJob, body: Any) -> None:
        from ..automation import browser as browser_manager
        from skyguard_agent.drivers.playwright_driver import PlaywrightDriver

        cfg = self.config()
        pw = browser_manager.start_playwright()
        browser = None
        try:
            browser = browser_manager.launch(pw, cfg.get("browser"), headless=bool(cfg.get("headless")))
            job.driver = PlaywrightDriver(browser, context_options=browser_manager.context_options(
                bool(cfg.get("headless"))), evidence_dir=self.folder / "evidence")
            job.result = body(job)
        except Exception as exc:  # reported on the run / session; the API never crashes
            job.error = str(getattr(exc, "message", exc))[:300]
            log.warning(f"Operations agent browser job {job.ref} ended: {job.error}")
            if job.kind in ("run", "discovery"):
                try:
                    run = self.agent.engine.run(job.ref)
                    if run["status"] not in ("completed", "partial", "failed", "stopped", "cancelled"):
                        self.agent.engine._pause(run, "browser_error", f"The browser could not be used: {job.error}")
                except Exception:
                    pass
        finally:
            try:
                if job.driver:
                    job.driver.close()
                if browser:
                    browser.close()
            finally:
                pw.stop()
                with self._lock:
                    if self.jobs.get(job.ref) is job:
                        self.jobs.pop(job.ref, None)

    def _start(self, kind: str, ref: str, body: Any) -> None:
        with self._lock:
            if ref in self.jobs:
                raise AgentError("ALREADY_RUNNING", "A browser is already working on this.", 409)
            job = _BrowserJob(kind, ref)
            self.jobs[ref] = job
        job.thread = threading.Thread(target=self._launch, args=(job, body), name=f"opsagent-{ref}", daemon=True)
        job.thread.start()

    def start_run(self, run_id: str) -> dict[str, Any]:
        """Drive an execution (or a discovery) in a SkyGuard-controlled browser."""
        engine = self.agent.engine
        run = engine.run(run_id)
        if run["status"] in ("completed", "partial", "failed", "stopped", "cancelled"):
            raise AgentError("RUN_FINISHED", "This execution has finished.", 409)
        kind = "discovery" if run["plan"].get("kind") == "discovery" else "run"
        self._start(kind, run_id, lambda job: job.driver.run(engine, run_id, stop=job.stop))
        return engine.view(run_id)

    def resume(self, run_id: str, by: str | None, note: str | None = None, choice: int | None = None) -> dict[str, Any]:
        job = self.jobs.get(run_id)
        events = list(job.driver.manual_events) if job and job.driver else []
        view = self.agent.engine.resume(run_id, by=by, manual_events=events, note=note, choice=choice)
        if not job:  # e.g. after a restart: open a browser again and continue from the checkpoint
            self.start_run(run_id)
        return view

    def stop(self, run_id: str, by: str | None) -> dict[str, Any]:
        view = self.agent.engine.stop(run_id, by=by)
        job = self.jobs.get(run_id)
        if job:
            job.stop.set()
        return view

    def browser_state(self, ref: str) -> dict[str, Any]:
        job = self.jobs.get(ref)
        return {"open": bool(job), "error": job.error if job else None}

    # ---- guided learning in a SkyGuard browser ------------------------------------------------------------------

    def start_guided(self, app_id: str, account_id: str | None, title: str, by: str | None) -> dict[str, Any]:
        session = self.agent.learning.start_guided(app_id, account_id=account_id, title=title, by=by)
        app = self.agent.kb.application(app_id)
        sid = session["id"]

        def body(job: _BrowserJob) -> None:
            drv = job.driver
            drv.perform({"type": "navigate", "url": app["base_url"]})
            drv.observe_user(True)
            while not job.finish.is_set() and not job.stop.is_set():
                job.finish.wait(1.0)
                events, drv.manual_events = drv.manual_events, []
                snap = drv.snapshot()
                if events:
                    self.agent.learning.add_events(sid, events, snap)
            events, drv.manual_events = drv.manual_events, []
            self.agent.learning.add_events(sid, events, drv.snapshot())

        self._start("guided", sid, body)
        return session

    def finish_guided(self, sid: str, by: str | None) -> dict[str, Any]:
        job = self.jobs.get(sid)
        if job:
            job.finish.set()
            if job.thread:
                job.thread.join(timeout=20)
        return self.agent.learning.finish_guided(sid, by=by, use_ai=bool(self.config().get("use_ai")))

    def cancel_guided(self, sid: str, by: str | None) -> dict[str, Any]:
        job = self.jobs.get(sid)
        if job:
            job.stop.set()
        return self.agent.learning.cancel_guided(sid, by=by)

    # ---- 23.7.0: learn while the person browses (passive learning in a SkyGuard browser) ---------------------------

    def start_watch(self, app_id: str, by: str | None) -> dict[str, Any]:
        """Open the application in a SkyGuard browser and learn from whatever the person does there, until they
        close the window or press Stop. The agent never clicks or types in this browser."""
        app = self.agent.kb.application(app_id)
        session = self.agent.journal.new_session(app["id"], interface="skyguard", by=by)
        sid = session["id"]

        def body(job: _BrowserJob) -> None:
            drv = job.driver
            drv.perform({"type": "navigate", "url": app["base_url"]})

            def record(events: list[dict[str, Any]], snap: dict[str, Any] | None) -> None:
                self.agent.observe(app["id"], sid, events, snap, by=by, interface="skyguard", use_ai=False)

            try:
                job.result = drv.watch(record, stop=job.stop)
            finally:
                self.agent.observe(app["id"], sid, [], None, by=by, interface="skyguard", final=True,
                                   use_ai=bool(self.config().get("use_ai")))

        self._start("watch", sid, body)
        return session

    def stop_watch(self, sid: str, by: str | None) -> dict[str, Any]:
        job = self.jobs.get(sid)
        if job:
            job.stop.set()
            if job.thread:
                job.thread.join(timeout=30)
        else:
            self.agent.journal.end_session(sid, by=by)
        row = self.agent.store.query_one("SELECT application_id FROM learning_sessions WHERE id = ?", (sid,))
        if not row:
            raise AgentError("LEARNING_NOT_FOUND", "That learning session does not exist.", 404)
        return self.agent.understanding_view(row["application_id"])

    def watching(self) -> list[str]:
        return [ref for ref, job in self.jobs.items() if job.kind == "watch"]

    def close(self) -> None:
        for job in list(self.jobs.values()):
            job.stop.set()
        for job in list(self.jobs.values()):
            if job.thread:
                job.thread.join(timeout=10)
        self.agent.close()
