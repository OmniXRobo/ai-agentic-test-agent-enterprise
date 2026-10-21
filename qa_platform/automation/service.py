"""Automation Studio facade: scripts, execution, recording, discovery, AI generation, hybrid."""

from __future__ import annotations

import threading
from typing import Any

from ..agents.base import new_run_id
from ..ai.gateway import AIGateway
from ..config import Paths, Settings
from ..credentials import SecretStore
from ..errors import QAError
from ..events.bus import EventBus
from ..logging_setup import get_logger
from ..runs import RunStore
from ..storage.db import Database
from ..tfs.service import TfsService
from .discovery import AppDiscoveryAgent, Discovery
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
        self.discovery = Discovery(AppDiscoveryAgent(bus), db)
        self.generator = AutomationGenerator(AutomationEngineeringAgent(bus), ai, tfs)
        self.guided_agent = GuidedBrowserAgent(bus)
        self.recordings: dict[str, dict[str, Any]] = {}
        self.generated: dict[str, str] = {}  # generation run id -> script id
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
                                       credential_role=credential_role)
            self.recordings[run_id] = {"session": session, "name": name, "append_to": append_to, "insert_at": insert_at,
                                       "browser": browser, "credential_role": credential_role,
                                       "test_case_id": test_case_id, "application": application, "start_url": start_url}
            session.start()
        return run_id

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
                        headless: bool = True) -> str:
        run_id = new_run_id("DISC")
        self._background(run_id, lambda: self.discovery.crawl(run_id, base_url, max_pages=max_pages,
                                                              max_depth=max_depth, headless=headless))
        return run_id

    def start_generation(self, test_case_id: int, base_url: str, *, credential_role: str | None = None,
                         application: str | None = None, discovery_id: str | None = None,
                         headless: bool = True) -> str:
        run_id = new_run_id("GEN")
        self._background(run_id, lambda: self.generate(run_id, test_case_id, base_url, credential_role=credential_role,
                                                       application=application, discovery_id=discovery_id,
                                                       headless=headless))
        return run_id

    def generate(self, run_id: str, test_case_id: int, base_url: str, *, credential_role: str | None = None,
                 application: str | None = None, discovery_id: str | None = None, headless: bool = True) -> Script:
        """AI-driven generation in the caller's thread; saves and returns the new script."""
        disc = self.discovery.get(discovery_id) if discovery_id else self.discovery.latest_for(base_url)
        if disc is None:
            disc = self.discovery.crawl(run_id, base_url, max_pages=5, max_depth=1, headless=headless)
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
