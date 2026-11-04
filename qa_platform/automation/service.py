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


class ExecSlots:
    """23.3.21: how many browser runs play at the same time. Default 1 (Settings > automation.parallel_runs, 1 to 8);
    a scheduled or Run now batch can ask for more. Used as ``with slots:`` (default) or ``with slots.hold(n):``."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cond = threading.Condition()
        self.active = 0
        self._held = threading.local()

    def default(self) -> int:
        try:
            return max(1, min(8, int((self.settings.section("automation") or {}).get("parallel_runs") or 1)))
        except (TypeError, ValueError):
            return 1

    def acquire(self, limit: int | None = None) -> None:
        lim = max(1, min(8, int(limit or self.default())))
        with self._cond:
            while self.active >= lim:
                self._cond.wait(1.0)
            self.active += 1

    def release(self) -> None:
        with self._cond:
            self.active = max(0, self.active - 1)
            self._cond.notify_all()

    def __enter__(self) -> "ExecSlots":
        self.acquire(getattr(self._held, "limit", None))
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()

    def limit_for_thread(self, limit: int | None) -> None:
        """Runs started from this thread use this limit (a batch with its own 'at the same time')."""
        self._held.limit = limit


def merge_segments(original: list[Step], segments: list[dict[str, Any]]) -> tuple[list[Step], list[str]]:
    """23.3.20: put recorded segments into a script. ``at`` and ``replace`` count the script's steps as they were
    when recording started, so several insert points and replaced steps in one session never shift each other.
    Segments at the same position keep the order they were recorded in."""
    n = len(original)
    replaced: set[int] = set()
    summary: list[str] = []
    for seg in segments:
        r = int(seg.get("replace") or 0)
        at = n if seg.get("at") is None else max(0, min(int(seg["at"]), n))
        seg["at"] = at
        replaced.update(range(at, min(n, at + r)))
        k = len(seg["steps"])
        if r:
            what = f"step {at + 1}" if r == 1 else f"steps {at + 1} to {at + r}"
            summary.append(f"replaced {what} with {k} recorded step{'s' if k != 1 else ''}")
        elif k:
            summary.append(f"inserted {k} step{'s' if k != 1 else ''} at step {at + 1}")
    out: list[Step] = []
    for i in range(n + 1):
        for seg in segments:
            if seg["at"] == i:
                out += seg["steps"]
        if i < n and i not in replaced:
            out.append(original[i])
    return out, summary


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
        self._exec_lock = ExecSlots(settings)  # 23.3.21: one run at a time unless parallel runs are allowed
        self.batch_stops: dict[str, threading.Event] = {}
        self.last_conversions: dict[str, list[dict[str, Any]]] = {}  # 23.3.17: dates made dynamic by the last recording
        self.last_summary: dict[str, list[str]] = {}  # 23.3.20: where the last recording put its steps

    def _background(self, name: str, fn: Any) -> None:
        def wrapper() -> None:
            try:
                fn()
            except Exception:
                log.info(f"{name} finished with an error (reported through events)")

        threading.Thread(target=wrapper, name=name, daemon=True).start()

    # ---- execution ---------------------------------------------------------------------

    def run_script(self, script_id: str, *, dataset: dict[str, Any] | None = None, headless: bool | None = None,
                   background: bool = True, browser: str | None = None, data_label: str | None = None,
                   run_id: str | None = None) -> str | dict[str, Any]:
        self.scripts.get(script_id)  # validate before starting
        run_id = run_id or new_run_id("RUN")

        def work() -> dict[str, Any]:
            with self._exec_lock:  # one browser run at a time unless parallel runs are allowed (23.3.21)
                return self.runner.run(script_id, run_id, dataset=dataset, headless=headless, browser=browser,
                                       data_label=data_label)

        if not background:
            return work()
        self._background(run_id, work)
        return run_id

    def run_data_rows(self, script_id: str, rows: list[int] | None = None, *, headless: bool | None = None) -> dict[str, Any]:
        """23.3.17: one run per row of the script's imported Excel / CSV data (all rows, or the chosen ones)."""
        script = self.scripts.get(script_id)
        table = script.data_table
        if table is None or not table.rows:
            raise QAError("TEST_DATA_MISSING", "Import an Excel or CSV file in Edit Script > Test data first.", status=409)
        chosen = list(range(len(table.rows))) if not rows else sorted({int(r) for r in rows})
        bad = [r + 1 for r in chosen if not 0 <= r < len(table.rows)]
        if bad:
            raise QAError("TEST_DATA_ROW_INVALID", f"Row {bad[0]} is not in the data ({len(table.rows)} rows).", status=422)
        batch_id = new_run_id("DDR")
        items = [{"row": r, "run_id": new_run_id("RUN"), "label": f"row {r + 1} of {len(table.rows)}"} for r in chosen]
        stop = threading.Event()
        self.batch_stops[batch_id] = stop

        def work() -> None:
            try:
                for item in items:  # in row order, one browser at a time
                    if stop.is_set():
                        break
                    with self._exec_lock:
                        self.runner.run(script_id, item["run_id"], dataset=dict(table.rows[item["row"]]),
                                        headless=headless, data_label=item["label"])
            finally:
                self.batch_stops.pop(batch_id, None)

        self._background(batch_id, work)
        batch = {"batch_id": batch_id, "started_at": now_iso(), "source": table.source, "rows": items}
        self.scripts.add_data_run(script_id, batch)
        return batch

    def stop_data_rows(self, batch_id: str) -> bool:
        ev = self.batch_stops.get(batch_id)
        if not ev:
            return False
        ev.set()
        for run_id in list(self.runner.running()):
            self.runner.stop(run_id)
        return True

    def data_run_status(self, script_id: str) -> list[dict[str, Any]]:
        out = []
        for batch in self.scripts.data_runs(script_id)[:10]:
            ids = [i["run_id"] for i in batch.get("rows", [])]
            found = {}
            if ids:
                marks = ",".join("?" * len(ids))
                for row in self.runner.db.query(f"SELECT run_id, status, error, failed_step, started_at, finished_at"
                                                f" FROM execution_results WHERE run_id IN ({marks})", tuple(ids)):
                    found[row["run_id"]] = dict(row)
            active = batch["batch_id"] in self.batch_stops
            rows = [{**i, **{k: v for k, v in (found.get(i["run_id"]) or {"status": "QUEUED" if active else "NOT RUN"})
                             .items() if k != "run_id"}} for i in batch.get("rows", [])]
            counts: dict[str, int] = {}
            for r in rows:
                counts[r["status"]] = counts.get(r["status"], 0) + 1
            out.append({**batch, "rows": rows, "counts": counts, "active": active})
        return out

    def run_many(self, script_ids: list[str], headless: bool | None = None) -> list[str]:
        ids = []
        for sid in script_ids:
            ids.append(self.run_script(sid, headless=headless))  # type: ignore[arg-type]
        return ids

    # ---- recording (Guided Browser Automation / Hybrid) ---------------------------------

    def start_recording(self, start_url: str, *, name: str | None = None, append_to: str | None = None,
                        insert_at: int | None = None, browser: str = "chromium", headless: bool = False,
                        credential_role: str | None = None, test_case_id: str | None = None,
                        application: str | None = None, start_paused: bool | None = None, replace: int = 0,
                        replay: bool = False) -> str:
        """23.3.20: Record more steps opens the browser without recording (start_paused defaults to True there);
        replace re-records that many steps from insert_at; replay plays the steps before insert_at first."""
        with self._lock:
            if any(r["session"].active for r in self.recordings.values()):
                raise QAError("RECORDING_ACTIVE", "A browser recording is already in progress.", status=409)
            script = self.scripts.get(append_to) if append_to else None
            run_id = new_run_id("REC")
            n = len(script.steps) if script else None
            at = None if n is None else (n if insert_at is None else max(0, min(int(insert_at), n)))
            paused = bool(script) if start_paused is None else bool(start_paused)
            replay_to = at if (script and replay) else 0
            session = RecordingSession(self.guided_agent, run_id, start_url, browser=browser, headless=headless,
                                       credential_role=credential_role, playback=self.settings.section("playback"),
                                       files_dir=self.scripts.root / "_recording" / run_id,
                                       start_paused=paused, script_steps=n, insert_at=at, replace=replace,
                                       replay=self._replayer(script, run_id) if replay_to else None,
                                       replay_to=replay_to or 0)
            self.recordings[run_id] = {"session": session, "name": name, "append_to": append_to, "insert_at": insert_at,
                                       "browser": browser, "credential_role": credential_role,
                                       "test_case_id": test_case_id, "application": application, "start_url": start_url}
            session.start()
        return run_id

    def _replayer(self, script: Script, run_id: str) -> Any:
        """23.3.20: plays a script's first steps in the recording browser (with its first data row and saved
        credentials), so recording starts on the screen where the new steps belong."""
        from .dsl import ValueResolver
        from ..events import names

        def replay(page: Any, act: Any, upto: int) -> None:
            table = script.data_table
            row = dict(table.rows[0]) if table is not None and table.rows else None
            resolver = ValueResolver(data=row, credentials=lambda role, field: self.creds.lookup(script.application, role, field),
                                     unique=run_id[-6:])
            evidence = self.scripts.root / "_recording" / run_id / "_replay"
            evidence.mkdir(parents=True, exist_ok=True)
            record: dict[str, Any] = {"steps": [], "evidence": {"screenshots": [], "downloads": []}, "healed": []}
            page.set_default_timeout(15000)
            for i, step in enumerate(script.steps[:upto]):
                act.event(names.AGENT_LOG, f"Playing step {i + 1}/{upto}: {resolver.mask(step.description or step.action)}")
                record["steps"].append({"index": i})
                try:
                    self.runner._step(page, script, step, i, act, resolver, record, evidence, False, 0)
                except Exception as exc:
                    if step.optional:
                        continue
                    msg = getattr(exc, "message", None) or str(exc).splitlines()[0][:200]
                    raise QAError("REPLAY_FAILED", f"step {i + 1} ({resolver.mask(msg)}).") from exc
            try:
                page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass

        return replay

    def recording_control(self, run_id: str, action: str, insert_at: int | None = None,
                          replace: int | None = None) -> dict[str, Any]:
        """23.3.20: Record / Pause / change where the next steps go."""
        session: RecordingSession = self.recording(run_id)["session"]
        return session.control(action, insert_at=insert_at, replace=replace)

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
        segs = s.segment_steps()
        with s._lock:
            bar = s.bar_state()
        return {"run_id": run_id, "active": s.active, "error": s.error, **bar, "count": bar["steps"],
                "append": rec["append_to"] is not None,
                "steps": [st.model_dump() for seg in segs for st in seg["steps"]],
                "segments": [{"at": g["at"], "replace": g["replace"], "steps": [st.model_dump() for st in g["steps"]]}
                             for g in segs]}

    def stop_recording(self, run_id: str) -> Script:
        rec = self.recording(run_id)
        session: RecordingSession = rec["session"]
        steps = session.stop()
        if rec["append_to"]:
            script = self.scripts.get(rec["append_to"])
            segs = session.segment_steps()
            if not script.steps and segs and segs[0]["url"] and not any(s.action == "goto" for s in segs[0]["steps"]):
                url = segs[0]["url"]
                segs[0]["steps"].insert(0, Step(action="goto", value=url, description=f"Open {url}"))
            script.steps, summary = merge_segments(script.steps, segs)
            if not summary:  # nothing recorded: the script stays as it was
                saved = self.scripts.get(rec["append_to"])
            else:
                if script.mode == "ai":
                    script.mode = "hybrid"
                saved = self.scripts.save(script, reason=f"recorded: {'; '.join(summary)} ({run_id})")
            self.last_summary[saved.id] = summary
        else:
            script = Script(id=new_script_id(), name=rec["name"] or f"Recorded {rec['start_url']}", mode="guided",
                            base_url=rec["start_url"], browser=rec["browser"], steps=steps,
                            credential_role=rec["credential_role"], test_case_id=rec["test_case_id"],
                            application=rec["application"])
            saved = self.scripts.save(script, reason=f"recorded ({run_id})")
        if session.files_dir is not None:
            self.scripts.adopt_files(saved.id, session.files_dir)
        self.last_conversions[saved.id] = session.converted
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
