"""Automation Studio facade: scripts, execution, recording, discovery, AI generation, hybrid."""

from __future__ import annotations

import threading
from typing import Any

from ..agents.base import new_run_id
from ..ai.gateway import AIGateway
from ..clock import now_iso
from ..config import Paths, Settings
from ..credentials import SecretStore
from ..errors import QAError
from ..events.bus import EventBus
from ..logging_setup import get_logger
from ..runs import RunStore
from ..storage.db import Database
from ..tfs.service import TfsService
from .discovery import AppDiscoveryAgent, Discovery, DiscoveryJob, DiscoveryStopped
from .dsl import Script, Step, new_script_id
from .generator import AutomationEngineeringAgent, AutomationGenerator
from .recorder import GuidedBrowserAgent, RecordingSession
from .runner import ManualPlaybackAgent, Runner, SelfHealingAgent, TestExecutionAgent
from .store import CredentialProfiles, ScriptStore

log = get_logger("automation")


class AutomationService:
    def __init__(self, paths: Paths, settings: Settings, bus: EventBus, db: Database, runs: RunStore,
                 secrets: SecretStore, tfs: TfsService, ai: AIGateway) -> None:
        self.settings = settings
        self.scripts = ScriptStore(paths.scripts)
        self.creds = CredentialProfiles(db, secrets)
        self.runner = Runner(paths, settings, db, runs, self.scripts, self.creds, TestExecutionAgent(bus),
                             ManualPlaybackAgent(bus), SelfHealingAgent(bus))
        self.discovery = Discovery(AppDiscoveryAgent(bus), db, self.creds, secrets)
        self.discovery_jobs: dict[str, DiscoveryJob] = {}
        self.generator = AutomationGenerator(AutomationEngineeringAgent(bus), ai, tfs)
        self.guided_agent = GuidedBrowserAgent(bus)
        self.recordings: dict[str, dict[str, Any]] = {}
        self.generated: dict[str, str] = {}  # generation run id -> script id
        self.generated_many: dict[str, list[str]] = {}  # story generation run id -> script ids
        self.tfs = tfs
        self._lock = threading.Lock()
        self._exec_lock = threading.Lock()

    def _background(self, name: str, fn: Any) -> None:
        def wrapper() -> None:
            try:
                fn()
            except Exception:
                log.info(f"{name} finished with an error (reported through events)")

        threading.Thread(target=wrapper, name=name, daemon=True).start()

    # ---- execution ---------------------------------------------------------------------

    def run_script(self, script_id: str, *, dataset: dict[str, Any] | None = None, headless: bool | None = None,
                   background: bool = True) -> str | dict[str, Any]:
        self.scripts.get(script_id)  # validate before starting
        run_id = new_run_id("RUN")

        def work() -> dict[str, Any]:
            with self._exec_lock:  # one browser run at a time keeps playback visible and evidence clean
                return self.runner.run(script_id, run_id, dataset=dataset, headless=headless)

        if not background:
            return work()
        self._background(run_id, work)
        return run_id

    def run_many(self, script_ids: list[str], headless: bool | None = None) -> list[str]:
        ids = []
        for sid in script_ids:
            ids.append(self.run_script(sid, headless=headless))  # type: ignore[arg-type]
        return ids

    # ---- recording (Guided Browser Automation / Hybrid) ---------------------------------

    def start_recording(self, start_url: str, *, name: str | None = None, append_to: str | None = None,
                        insert_at: int | None = None, browser: str = "chromium", headless: bool = False,
                        credential_role: str | None = None, test_case_id: str | None = None,
                        application: str | None = None) -> str:
        with self._lock:
            if any(r["session"].active for r in self.recordings.values()):
                raise QAError("RECORDING_ACTIVE", "A browser recording is already in progress.", status=409)
            if append_to:
                self.scripts.get(append_to)
            run_id = new_run_id("REC")
            session = RecordingSession(self.guided_agent, run_id, start_url, browser=browser, headless=headless,
                                       credential_role=credential_role, playback=self.settings.section("playback"))
            self.recordings[run_id] = {"session": session, "name": name, "append_to": append_to, "insert_at": insert_at,
                                       "browser": browser, "credential_role": credential_role,
                                       "test_case_id": test_case_id, "application": application, "start_url": start_url}
            session.start()
        return run_id

    def voice_action(self, run_id: str, params: dict[str, Any]) -> str:
        """A spoken browser action in the active recording (click, fill, select, select a date, scroll, ...)."""
        session: RecordingSession = self.recording(run_id)["session"]
        return str(session.command("voice", dict(params), timeout=90))

    def recording(self, run_id: str) -> dict[str, Any]:
        rec = self.recordings.get(run_id)
        if not rec:
            raise QAError("RECORDING_NOT_FOUND", f"Recording {run_id} does not exist.", status=404)
        return rec

    def recording_status(self, run_id: str) -> dict[str, Any]:
        rec = self.recording(run_id)
        s: RecordingSession = rec["session"]
        return {"run_id": run_id, "active": s.active, "error": s.error,
                "steps": [st.model_dump() for st in s.to_steps()]}

    def stop_recording(self, run_id: str) -> Script:
        rec = self.recording(run_id)
        steps = rec["session"].stop()
        if rec["append_to"]:
            script = self.scripts.get(rec["append_to"])
            new_steps = [s for s in steps if s.action != "goto"] if script.steps else steps
            at = len(script.steps) if rec["insert_at"] is None else max(0, min(rec["insert_at"], len(script.steps)))
            script.steps[at:at] = new_steps
            if script.mode == "ai":
                script.mode = "hybrid"
            saved = self.scripts.save(script, reason=f"recorded {len(new_steps)} steps ({run_id})")
        else:
            script = Script(id=new_script_id(), name=rec["name"] or f"Recorded {rec['start_url']}", mode="guided",
                            base_url=rec["start_url"], browser=rec["browser"], steps=steps,
                            credential_role=rec["credential_role"], test_case_id=rec["test_case_id"],
                            application=rec["application"])
            saved = self.scripts.save(script, reason=f"recorded ({run_id})")
        self.recordings.pop(run_id, None)
        return saved

    # ---- discovery and AI generation -----------------------------------------------------

    def start_discovery(self, base_url: str, max_pages: int = 15, max_depth: int = 3,
                        headless: bool = True, **options: Any) -> str:
        """Interactive discovery job: it can pause for input and resume in the same browser session.

        ``options`` are passed to ``Discovery.crawl`` (application, credential_role, environment, explore,
        knowledge_source, allow_destructive). ``work`` replaces the plain crawl, for example to run the
        Requirements Intelligence Agent first; it receives the job and returns the discovery result.
        """
        work = options.pop("work", None)
        run_id = new_run_id("DISC")
        job = DiscoveryJob(run_id, {"base_url": base_url, "max_pages": max_pages, "max_depth": max_depth,
                                    "headless": headless, **options})
        job.set_state("PREPARING", "Preparing discovery")
        with self._lock:
            self.discovery_jobs[run_id] = job
            for old in [k for k, j in self.discovery_jobs.items() if not j.active][:-20]:
                self.discovery_jobs.pop(old, None)  # keep the last finished jobs only

        def crawl(j: DiscoveryJob) -> dict[str, Any]:
            return self.discovery.crawl(run_id, base_url, max_pages=max_pages, max_depth=max_depth,
                                        headless=headless, job=j, **options)

        def run() -> None:
            try:
                result = (work or crawl)(job)
                job.discovery_id = result.get("id")
                job.summary = result.get("summary")
                job.set_state("COMPLETED", "Discovery completed")
            except DiscoveryStopped:
                job.set_state("STOPPED", "Stopped by the tester")
            except QAError as exc:
                job.error = exc.message
                job.set_state("FAILED", exc.message)
                raise
            except Exception as exc:
                job.error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:300] if str(exc) else ''}"
                job.set_state("FAILED", job.error)
                raise
            finally:
                job.finished_at = now_iso()

        self._background(run_id, run)
        return run_id

    def discovery_job(self, run_id: str) -> DiscoveryJob:
        job = self.discovery_jobs.get(run_id)
        if job is None:
            raise QAError("DISCOVERY_JOB_NOT_FOUND", f"Discovery {run_id} is not running in this session.", status=404)
        return job

    def start_generation(self, test_case_id: int, base_url: str, *, credential_role: str | None = None,
                         application: str | None = None, discovery_id: str | None = None,
                         headless: bool = True) -> str:
        run_id = new_run_id("GEN")
        self._background(run_id, lambda: self.generate(run_id, test_case_id, base_url, credential_role=credential_role,
                                                       application=application, discovery_id=discovery_id,
                                                       headless=headless))
        return run_id

    def start_story_generation(self, user_story_id: int, base_url: str, *, credential_role: str | None = None,
                               application: str | None = None, discovery_id: str | None = None,
                               headless: bool = True) -> tuple[str, list[int]]:
        """One script per Test Case linked to the user story, one after the other."""
        story = self.tfs.detail(user_story_id)
        ids = [int(t["id"]) for t in story["traceability"]["test_cases"]]
        if not ids:
            raise QAError("STORY_NO_TEST_CASES", f"User Story #{user_story_id} has no linked Test Cases in TFS.",
                          status=409)
        run_id = new_run_id("GEN")
        self.generated_many[run_id] = []

        def work() -> None:
            # The first test case discovers the application; the others reuse that discovery.
            for tc in ids:
                try:
                    script = self.generate(run_id, tc, base_url, credential_role=credential_role,
                                           application=application, discovery_id=discovery_id, headless=headless)
                    self.generated_many[run_id].append(script.id)
                except Exception:
                    log.info(f"Automation for Test Case #{tc} was not created (reported through events)")

        self._background(run_id, work)
        return run_id, ids

    def generate(self, run_id: str, test_case_id: int, base_url: str, *, credential_role: str | None = None,
                 application: str | None = None, discovery_id: str | None = None, headless: bool = True) -> Script:
        """AI-driven generation in the caller's thread; saves and returns the new script."""
        disc = self.discovery.get(discovery_id) if discovery_id else self.discovery.latest_for(base_url, application)
        if disc is None:
            disc = self.discovery.crawl(run_id, base_url, max_pages=5, max_depth=1, headless=headless,
                                        application=application, credential_role=credential_role)
        script = self.generator.generate(run_id, test_case_id=test_case_id, base_url=base_url, discovery=disc,
                                         credential_role=credential_role, application=application)
        saved = self.scripts.save(script, reason=f"AI-generated ({run_id})")
        self.generated[run_id] = saved.id
        return saved

    def create_from_discovery(self, discovery_id: str, page_url: str, selectors: list[str], name: str) -> Script:
        disc = self.discovery.get(discovery_id)
        page = next((p for p in disc["pages"] if p["url"] == page_url), None)
        if page is None:
            raise QAError("DISCOVERY_PAGE_NOT_FOUND", "That page is not in this discovery.", status=404)
        by_sel = {e["selector"]: e for e in page.get("elements", [])}
        steps = [Step(action="goto", value=page_url, description=f"Open {page.get('title') or page_url}")]
        from .dsl import Target

        for sel in selectors:
            el = by_sel.get(sel)
            if not el:
                continue
            target = Target.model_validate({**el, "description": el.get("name") or el.get("text") or sel})
            textual = el.get("role") == "textbox" or el.get("tag") in ("input", "textarea")
            if el.get("tag") == "select":
                steps.append(Step(action="select", target=target, value="", description=f"Select a value in {target.description}"))
            elif textual and el.get("input_type") not in ("checkbox", "radio", "submit", "button"):
                steps.append(Step(action="fill", target=target, value="", description=f"Enter value in {target.description}"))
            else:
                steps.append(Step(action="click", target=target, description=f"Click {target.description}"))
        script = Script(id=new_script_id(), name=name or f"Discovered {page_url}", mode="discovery", base_url=page_url,
                        steps=steps)
        return self.scripts.save(script, reason=f"created from discovery {discovery_id}")
