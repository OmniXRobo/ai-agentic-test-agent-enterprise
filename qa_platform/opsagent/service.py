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
from skyguard_agent.autopilot import Autopilot
from skyguard_agent.llm import CallableLLM
from skyguard_agent.util import AgentError
from skyguard_agent.vault import SecretStoreBackend, Vault

from ..logging_setup import get_logger

log = get_logger("opsagent")

DEFAULTS = {"headless": False, "browser": None, "use_ai": True, "max_discovery_pages": 25, "auto_trust": True}


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
        self.browser: str | None = None  # 23.10.0: Edge or Chrome chosen on Connect & Learn
        self.app_id: str | None = None
        self.navigate: list[str] = []  # 23.11.1: pages the person asked to open (handled in the browser's thread)
        self.url: str | None = None  # the page the window shows now
        # 23.13.0: the chat browser. Tasks of the AI Agent chat run one after the other in this one window, signed in
        # once, until the person asks for a new window or closes it.
        self.queue: list[tuple[str, Any]] = []
        self.session: dict[str, Any] = {}  # who is signed in in this window (the engine's verified session)
        self.busy: str | None = None  # the run executing in it now
        self.created = 0.0


class _CardReporter:
    """23.12.0: the AI Application Learning & Operations Agent card on Agent Workflow reports the agent's real work:
    RUNNING while a learning window or an execution is open, PAUSED when the execution waits for the person,
    COMPLETED or FAILED with the execution's own outcome."""

    LABELS = {"run": "Executing", "discovery": "Exploring", "watch": "Learning", "guided": "Learning (guided)",
              "view": "Opening"}

    def __init__(self, bus: Any) -> None:
        from ..agents.base import BaseAgent
        from ..events import names

        class AiOperationsAgent(BaseAgent):
            agent_id = "ai_operations"

        self.names = names
        self.agent = AiOperationsAgent(bus) if bus is not None else None

    def started(self, job: Any, what: str) -> None:
        if self.agent is not None and job.kind != "view":
            self.agent.emit(self.names.AGENT_STARTED, f"{self.LABELS.get(job.kind, job.kind)}: {what}", run_id=job.ref,
                            status="RUNNING", progress=0)

    def ended(self, job: Any, status: str | None, reason: str | None) -> None:
        if self.agent is None or job.kind == "view":
            return
        n = self.names
        label = self.LABELS.get(job.kind, job.kind)
        if job.error:
            self.agent.emit(n.AGENT_FAILED, f"{label} stopped: {job.error}", run_id=job.ref, status="FAILED",
                            details={"error": job.error})
        elif status in ("completed",):
            self.agent.emit(n.AGENT_COMPLETED, f"{label} completed and verified", run_id=job.ref, status="PASSED",
                            progress=100)
        elif status in ("failed", "partial", "stopped", "cancelled"):
            self.agent.emit(n.AGENT_FAILED, f"{label} {status}" + (f": {reason}" if reason else ""), run_id=job.ref,
                            status="FAILED", details={"error": reason or status})
        elif status:  # paused / awaiting the person: the card says so instead of claiming success
            self.agent.emit(n.AGENT_PAUSED, f"{label} paused: {reason or status}", run_id=job.ref)
        else:
            self.agent.emit(n.AGENT_COMPLETED, f"{label} finished", run_id=job.ref, status="PASSED", progress=100)


class OpsAgentService:
    def __init__(self, rt: Any) -> None:
        self.rt = rt
        self.card = _CardReporter(getattr(rt, "bus", None))
        self.folder = Path(rt.paths.root) / "opsagent"
        try:
            vault = Vault(SecretStoreBackend(rt.secrets)) if rt.secrets.available else Vault(None)
        except AgentError:
            vault = Vault(None)
        self.agent = OperationsAgent(self.folder, vault=vault, llm=self._llm(), transcriber=self._transcribe,
                                     interface="skyguard")
        self.jobs: dict[str, _BrowserJob] = {}
        self._lock = threading.Lock()
        # 23.13.0: runs executing in a chat browser: run id -> (that window's job, the run's own stop event)
        self.hosted: dict[str, tuple[_BrowserJob, threading.Event]] = {}
        self._views = 0
        # 23.10.0: the simple agent (four tabs). Learning by watching is trusted automatically unless an admin turns
        # it off in Advanced settings ("auto_trust": false brings back one approval per learned task).
        self.pilot = Autopilot(self.agent, auto_trust=bool(self.config().get("auto_trust", True)))

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

        label = ai.label() if callable(getattr(ai, "label", None)) else getattr(ai, "label", "")
        return CallableLLM(call, name=f"SkyGuard AI gateway ({label})")

    def to_english(self, text: str) -> tuple[str, bool]:
        """23.13.0: an Arabic command (typed or spoken) in English, by the local AI (word list when it is not
        running). English text is returned unchanged."""
        from skyguard_agent.translate import has_arabic, to_english

        if not has_arabic(text):
            return text, False
        llm = None if self.agent.learning.llm is None else self.agent.llm
        return to_english(text, llm)

    def _transcribe(self, audio: bytes, content_type: str) -> str:
        return self.rt.voice.transcribe(audio, content_type)

    # ---- browser jobs ----------------------------------------------------------------------------------------

    def _launch(self, job: _BrowserJob, body: Any) -> None:
        from ..automation import browser as browser_manager
        from skyguard_agent.drivers.playwright_driver import PlaywrightDriver

        cfg = self.config()
        pw = browser_manager.start_playwright()
        self.card.started(job, job.app_id or job.ref)
        browser = None
        try:
            browser = browser_manager.launch(pw, job.browser or cfg.get("browser"), headless=bool(cfg.get("headless")))
            headless = bool(cfg.get("headless"))
            job.driver = PlaywrightDriver(browser, context_options={}, evidence_dir=self.folder / "evidence",
                                          context_factory=lambda **o: browser_manager.new_context(browser, headless, **o))
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
                status = reason = None
                if job.kind in ("run", "discovery"):
                    try:
                        run = self.agent.engine.run(job.ref)
                        status = run.get("status")
                        pause = (run.get("checkpoint") or {}).get("pause") or {}
                        reason = pause.get("message") or (run.get("summary") or {}).get("error")
                    except Exception:
                        pass
                try:
                    self.card.ended(job, status, reason)
                except Exception:  # the card never breaks the agent
                    pass

    def _start(self, kind: str, ref: str, body: Any, browser: str | None = None,
               app_id: str | None = None) -> _BrowserJob:
        import time as _time

        with self._lock:
            if ref in self.jobs:
                raise AgentError("ALREADY_RUNNING", "A browser is already working on this.", 409)
            job = _BrowserJob(kind, ref)
            job.browser = browser
            job.app_id = app_id
            job.created = _time.monotonic()
            self.jobs[ref] = job
        job.thread = threading.Thread(target=self._launch, args=(job, body), name=f"opsagent-{ref}", daemon=True)
        job.thread.start()
        return job

    def start_run(self, run_id: str, *, chat: bool = False, new_window: bool = False) -> dict[str, Any]:
        """Drive an execution (or a discovery) in a SkyGuard-controlled browser.

        23.13.0 ``chat``: a task asked for in the AI Agent chat runs in the chat browser of its application (the window
        the chat already opened, signed in once), not in a new window per request. ``new_window``: the person asked
        for a new window."""
        engine = self.agent.engine
        run = engine.run(run_id)
        if run["status"] in ("completed", "partial", "failed", "stopped", "cancelled"):
            raise AgentError("RUN_FINISHED", "This execution has finished.", 409)
        kind = "discovery" if run["plan"].get("kind") == "discovery" else "run"
        if chat and kind == "run" and self.config().get("one_browser", True):
            job = self.chat_browser(run["application_id"] if "application_id" in run else run["plan"]["app_id"],
                                    new=new_window)
            if run_id in self.hosted:
                return engine.view(run_id)
            ev = threading.Event()
            self.hosted[run_id] = (job, ev)
            job.queue.append(("run", (run_id, ev)))
            return engine.view(run_id)
        self._start(kind, run_id, lambda job: job.driver.run(engine, run_id, stop=job.stop))
        return engine.view(run_id)

    def _job_for(self, run_id: str) -> _BrowserJob | None:
        job = self.jobs.get(run_id)
        if job is None and run_id in self.hosted:
            job = self.hosted[run_id][0]
        return job

    def resume(self, run_id: str, by: str | None, note: str | None = None, choice: int | None = None) -> dict[str, Any]:
        job = self._job_for(run_id)
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
        if run_id in self.hosted:  # only this task stops; the chat browser stays open
            self.hosted[run_id][1].set()
        return view

    def browser_state(self, ref: str) -> dict[str, Any]:
        job = self._job_for(ref)
        return {"open": bool(job), "error": job.error if job else None,
                "shared": ref in self.hosted}

    # ---- 23.13.0: one chat browser per application -------------------------------------------------------------

    def chat_browser(self, app_id: str, *, new: bool = False, url: str | None = None) -> _BrowserJob:
        """The open chat browser of this application, or a new one (maximized, on ``url`` or the application's
        address). It stays open until the person closes it, asks for a new window, or SkyGuard stops."""
        app = self.agent.kb.application(app_id)
        with self._lock:
            mine = [j for j in self.jobs.values() if j.kind == "view" and j.app_id == app["id"] and not j.stop.is_set()]
        if mine and not new:
            return max(mine, key=lambda j: j.created)
        self._views += 1
        ref = f"view-{app['id']}" + (f"-{self._views}" if mine else "")
        while ref in self.jobs:
            self._views += 1
            ref = f"view-{app['id']}-{self._views}"
        start = url or app["base_url"]
        engine = self.agent.engine

        def body(job: _BrowserJob) -> None:
            drv = job.driver
            drv.perform({"type": "navigate", "url": start})
            job.url = start
            tick = self._tick(job)
            while not job.stop.is_set():
                ctx = drv.contexts.get(drv.current)
                if ctx is None or not [pg for pg in ctx.pages if not pg.is_closed()]:
                    break  # the person closed the window
                if self._pages_follow(drv, ctx):
                    pass
                tick(drv)
                while job.queue and not job.stop.is_set():
                    what, arg = job.queue.pop(0)
                    if what == "run":
                        self._run_hosted(job, engine, *arg)
                drv._idle(0.5)
            for what, arg in job.queue:  # the window was closed with tasks still waiting: they pause, never vanish
                if what == "run":
                    self._orphan(arg[0])
            job.queue.clear()

        return self._start("view", ref, body, app_id=app["id"])

    @staticmethod
    def _pages_follow(drv: Any, ctx: Any) -> bool:
        """The person opened a new tab in the chat browser: the agent follows it (as the learning window does)."""
        try:
            open_pages = [pg for pg in ctx.pages if not pg.is_closed()]
            if open_pages and drv.pages.get(drv.current) is not open_pages[-1] and \
                    (drv.pages.get(drv.current) is None or drv.pages[drv.current].is_closed()):
                drv.pages[drv.current] = open_pages[-1]
                return True
        except Exception:
            pass
        return False

    def _run_hosted(self, job: _BrowserJob, engine: Any, run_id: str, ev: threading.Event) -> None:
        """One task in the chat browser: it starts signed in as the window already is (no second sign-in for the
        same account) and switches account in the same window when the task needs another one."""
        job.busy = run_id
        try:
            if job.session:
                try:
                    engine.seed_session(run_id, job.session)
                except Exception:
                    pass
            self.card.started(job, run_id)
            job.driver.run(engine, run_id, stop=ev, isolated=False)
            try:
                sess = (engine.run(run_id).get("checkpoint") or {}).get("session") or {}
                if sess.get("account_id"):
                    job.session = dict(sess)
            except Exception:
                pass
        except Exception as exc:  # noqa: BLE001 - the window stays open for the next request
            log.warning(f"Chat browser task {run_id} ended: {exc}")
            try:
                run = engine.run(run_id)
                if run["status"] not in ("completed", "partial", "failed", "stopped", "cancelled"):
                    engine._pause(run, "browser_error", f"The browser could not be used: {str(exc)[:200]}")
            except Exception:
                pass
        finally:
            job.busy = None
            self.hosted.pop(run_id, None)

    def _orphan(self, run_id: str) -> None:
        self.hosted.pop(run_id, None)
        try:
            run = self.agent.engine.run(run_id)
            if run["status"] not in ("completed", "partial", "failed", "stopped", "cancelled"):
                self.agent.engine._pause(run, "browser_closed", "The chat browser was closed before this task ran. "
                                                                 "Press Resume to open it again.")
        except Exception:
            pass

    def sign_in(self, app_id: str, account_id: str, by: str | None) -> dict[str, Any]:
        """23.13.0: "log in as manager": sign in to the application in the chat browser with that account (its saved
        password from Windows Credential Manager), verified like any task's sign-in, and stay signed in."""
        view = self.agent.engine.create_sign_in(app_id=app_id, account_id=account_id, interface="skyguard", by=by)
        self.start_run(view["run"]["id"], chat=True)
        return view

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

    def start_watch(self, app_id: str, by: str | None, browser: str | None = None) -> dict[str, Any]:
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
                job.result = drv.watch(record, stop=job.stop, on_tick=self._tick(job))
            finally:
                self.agent.observe(app["id"], sid, [], None, by=by, interface="skyguard", final=True,
                                   use_ai=bool(self.config().get("use_ai")))

        self._start("watch", sid, body, browser=browser, app_id=app["id"])
        return session

    # ---- 23.11.1: open a page the person asked for -------------------------------------------------------------

    @staticmethod
    def _tick(job: _BrowserJob) -> Any:
        def tick(drv: Any) -> None:
            while job.navigate:
                url = job.navigate.pop(0)
                try:
                    drv.page.goto(url)
                except Exception as exc:  # reported in the status; the window stays open
                    job.error = str(exc)[:200]
            try:
                job.url = drv.page.url
            except Exception:
                pass
        return tick

    def open_page(self, app_id: str, url: str, by: str | None = None, *, new_window: bool = False) -> dict[str, Any]:
        """Open a page of a registered application: in its learning window when one is open, else in the chat
        browser (23.13.0: one window per application, reused by every request until the person asks for a new
        window). Only the application's own addresses (its site, including its own subdomains) are opened.
        Read-only: nothing is clicked or typed."""
        from skyguard_agent.knowledge import same_site

        app = self.agent.kb.application(app_id)
        if not same_site(url, app):
            raise AgentError("URL_REFUSED", "That address is not part of this application; it was not opened.", 409)
        self.agent.audit.record("chat.navigate", actor=by, interface="skyguard", target=app["id"],
                                details={"url": url})
        if not new_window:
            job = next((j for j in self.jobs.values() if j.kind == "watch" and j.app_id == app["id"]), None)
            if job:
                job.navigate.append(url)
                return {"ok": True, "window": "learning", "url": url}
            with self._lock:
                mine = [j for j in self.jobs.values() if j.kind == "view" and j.app_id == app["id"]
                        and not j.stop.is_set()]
            if mine:
                max(mine, key=lambda j: j.created).navigate.append(url)
                return {"ok": True, "window": "existing", "url": url}
        self.chat_browser(app["id"], new=new_window, url=url)
        return {"ok": True, "window": "new", "url": url}

    def current_url(self, app_id: str) -> str | None:
        """The page the person has open for this application in a SkyGuard window (for contextual suggestions)."""
        for job in self.jobs.values():
            if job.app_id == app_id and job.kind in ("watch", "view") and job.url:
                return job.url
        row = self.agent.store.query_one("SELECT url FROM observations WHERE application_id = ? AND kind = 'page' "
                                         "ORDER BY id DESC LIMIT 1", (app_id,))
        return row["url"] if row else None

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

    def connect_and_learn(self, url: str, name: str | None, by: str | None, learn: bool = True,
                          browser: str | None = None, environment: str | None = None) -> dict[str, Any]:
        """23.10.0: one button. The application for this address (created if new) and, unless asked otherwise, a
        maximized browser on it that learns from what the person does."""
        res = self.pilot.connect(url, name=name, by=by, environment=environment)
        app = res["application"]
        session = None
        if learn:
            running = [ref for ref, job in self.jobs.items() if job.kind == "watch"
                       and self._session_app(ref) == app["id"]]
            session = {"id": running[0], "reused": True} if running else self.start_watch(app["id"], by, browser)
        return {**res, "session": session}

    def _session_app(self, sid: str) -> str | None:
        row = self.agent.store.query_one("SELECT application_id FROM learning_sessions WHERE id = ?", (sid,))
        return row["application_id"] if row else None

    def watching(self) -> list[str]:
        return [ref for ref, job in self.jobs.items() if job.kind == "watch"]

    def close(self) -> None:
        for job in list(self.jobs.values()):
            job.stop.set()
        for job in list(self.jobs.values()):
            if job.thread:
                job.thread.join(timeout=10)
        self.agent.close()
