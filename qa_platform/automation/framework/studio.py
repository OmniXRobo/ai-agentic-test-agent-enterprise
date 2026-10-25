"""AI-Driven Automation Script Studio.

Selection (Sprint -> User Stories -> Test Cases) -> generation jobs run by the
Automation Engineering Agent -> framework files (Page Objects, flows, tests, test
data) validated in a staging copy and saved atomically -> inventory -> view, edit,
Flow Logic, Test Data, Credentials, Date/Time rules, version history -> Play
(Test Execution Agent -> pytest + Playwright) with visual playback, self-healing
and evidence.

A script is GENERATED only when its .py file exists on disk with the content that
was validated; the inventory re-checks the file every time it is listed.
"""

from __future__ import annotations

import ast
import difflib
import io
import json
import re
import threading
import uuid
import zipfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ...agents.base import new_run_id
from ...ai.gateway import AIGateway
from ...clock import now_iso
from ...config import Paths, Settings
from ...credentials import APP_CREDENTIAL_NAMESPACE, SecretStore
from ...design.service import html_to_text, split_criteria
from ...errors import QAError
from ...events import names
from ...events.bus import EventBus
from ...logging_setup import get_logger
from ...runs import RunStore
from ...storage.db import Database, dumps, loads
from ...tfs.service import TfsService
from ..generator import AutomationEngineeringAgent, parse_tfs_steps
from . import codegen, registry, validator
from .builder import CaseContext, FrameworkBuilder, Inventory, MappedTest
from .runner import FrameworkRunner
from .workspace import FrameworkWorkspace, sha256

log = get_logger("automation.studio")

METHOD = "ai_pom"
METHOD_NAMES = {"ai_pom": "AI-Driven Automation", "ai": "AI-Driven Automation (step script)",
                "guided": "Guided Browser Automation", "discovery": "Application Discovery & Update",
                "hybrid": "Hybrid Test Automation", "manual": "Manual Playback"}
AI_ERRORS = {"AI_NOT_CONFIGURED", "AI_UNREACHABLE", "AI_TIMEOUT", "AI_REQUEST_FAILED", "AI_INVALID_RESPONSE"}
_SCRIPT_ID = re.compile(r"^FWS-[A-Z0-9]{10}$")
_JOB_ID = re.compile(r"^GEN-[0-9]{8}-[0-9]{6}-[A-F0-9]{6}$")

SYSTEM_PROMPT = (
    "You are the Automation Engineering Agent building a Page Object Model test automation framework with Python and "
    "Playwright. Map every manual test step of the Test Case to automation actions. Rules: start with a goto step to "
    "the page where the test begins. For every click/fill/select/check/expect on an element copy exactly one selector "
    "from the ELEMENT INVENTORY; never invent selectors. Add an expect step for each expected result you can verify. "
    "Give each step the number of the manual step it implements (manual_step) and a short element_name. Group steps "
    "that form a reusable business process (for example login, add product to cart, checkout) with the same "
    "business_flow name; reuse an EXISTING FLOW name when the steps do the same thing. Put test data values in "
    "test_data and refer to them as {{DATA:field}}; use {{CRED:role.username}} and {{CRED:role.password}} for login "
    "fields and never write a password. For relative dates use {{TODAY+7d:%d/%m/%Y}} or {{NOW+2h:%H:%M}}. List the "
    "ids of the Acceptance Criteria the test covers.")


class JobSelection(BaseModel):
    user_story_id: int | None = None
    test_case_ids: list[int] | None = Field(default=None, max_length=1000)


class JobRequest(BaseModel):
    selections: list[JobSelection] = Field(min_length=1, max_length=200)
    base_url: str
    application: str | None = None
    environment: str | None = None
    credential_role: str | None = None
    discovery_id: str | None = None
    on_existing: str = Field(default="skip", pattern="^(skip|regenerate|new_version)$")
    headless: bool = True


class ScriptStudio:
    def __init__(self, paths: Paths, settings: Settings, bus: EventBus, db: Database, runs: RunStore,
                 secrets: SecretStore, tfs: TfsService, ai: AIGateway, automation: Any, design: Any,
                 defects: Any = None) -> None:
        self.paths = paths
        self.settings = settings
        self.bus = bus
        self.db = db
        self.runs = runs
        self.secrets = secrets
        self.tfs = tfs
        self.ai = ai
        self.automation = automation
        self.design = design
        self.defects = defects
        self.knowledge: Any = None  # Unified QA Knowledge (set by the runtime)
        self.ws = FrameworkWorkspace(paths.workspace)
        self.agent: AutomationEngineeringAgent = automation.generator.agent
        self.creds = automation.creds
        self.runner = FrameworkRunner(paths, settings, db, runs, automation.runner.execution, automation.runner.healer,
                                      analyst=defects.analyst if defects else None, on_diagnosis=self._on_diagnosis)
        self._gen_lock = threading.Lock()
        self._write_lock = threading.RLock()
        self._stops: dict[str, threading.Event] = {}
        self._ready = False
        self.db.execute("UPDATE framework_jobs SET status = 'INTERRUPTED', finished_at = ? WHERE status IN"
                        " ('RUNNING', 'RESOLVING', 'WAITING')", (now_iso(),))
        self.db.execute("UPDATE framework_job_items SET status = 'STOPPED', error = 'Interrupted by a restart.'"
                        " WHERE status IN ('WAITING', 'RUNNING')")
        self.db.execute("UPDATE framework_scripts SET last_result = 'STOPPED' WHERE last_result = 'RUNNING'")

    def workspace(self) -> FrameworkWorkspace:
        if not self._ready:
            self.ws.ensure()
            self._ready = True
        return self.ws

    # ---- TFS selection ----------------------------------------------------------------------

    def selection_context(self) -> dict[str, Any]:
        meta = self.tfs.meta()
        return {"project": meta.get("project"), "sprints": meta.get("iterations", []),
                "mode": self.tfs.active_mode()}

    def stories(self, iteration: str | None, text: str | None = None) -> list[dict[str, Any]]:
        items = self.tfs.query(category="requirement", iteration=iteration or None, text=text or None, top=500)["items"]
        return [{**i, "sprint": _sprint(i.get("iteration_path"))} for i in items]

    def story_test_cases(self, story_id: int) -> dict[str, Any]:
        story = self.tfs.detail(story_id)
        cases = story["traceability"]["test_cases"]
        existing = self._existing_by_tc([str(c["id"]) for c in cases])
        return {
            "story": {"id": story["id"], "title": story["title"], "sprint": _sprint(story.get("iteration_path")),
                      "state": story.get("state")},
            "acceptance_criteria": _criteria(story),
            "test_cases": [{"id": c["id"], "title": c["title"], "state": c.get("state"),
                            "existing": existing.get(str(c["id"]), [])} for c in cases],
        }

    def _existing_by_tc(self, test_case_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        if not test_case_ids:
            return {}
        marks = ",".join("?" for _ in test_case_ids)
        rows = self.db.query(f"SELECT id, test_case_id, file, version, status, generated_at FROM framework_scripts"
                             f" WHERE archived = 0 AND test_case_id IN ({marks}) ORDER BY generated_at DESC", test_case_ids)
        out: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            r["file_exists"] = self.workspace().exists(r["file"])
            out.setdefault(str(r["test_case_id"]), []).append(r)
        return out

    def check_existing(self, test_case_ids: list[int]) -> dict[str, Any]:
        found = self._existing_by_tc([str(t) for t in test_case_ids])
        legacy = {str(s["test_case_id"]): s for s in self.automation.scripts.list() if s.get("test_case_id")}
        return {"existing": found, "legacy": {k: v for k, v in legacy.items() if int(k) in test_case_ids
                                              if k.isdigit()}}

    # ---- jobs -------------------------------------------------------------------------------

    def start_job(self, req: JobRequest, *, action: str = "generate", script_id: str | None = None) -> str:
        if not re.match(r"^https?://", req.base_url.strip()):
            raise QAError("URL_INVALID", "The application URL must start with http:// or https://.")
        self.workspace()
        job_id = new_run_id("GEN")
        self.db.execute("INSERT INTO framework_jobs (id, status, created_at, request, summary) VALUES (?,?,?,?,?)",
                        (job_id, "RESOLVING", now_iso(), req.model_dump_json(), dumps({"action": action})))
        self.runs.start(job_id, "automation", f"AI-Driven Automation: {len(req.selections)} selection(s)")
        stop = threading.Event()
        self._stops[job_id] = stop

        def work() -> None:
            try:
                self._run_job(job_id, req, stop, action, script_id)
            except Exception as exc:  # resolution failed: reported on the job
                log.info(f"Generation job {job_id} failed: {exc}")
                self.db.execute("UPDATE framework_jobs SET status = 'FAILED', finished_at = ?, summary = ? WHERE id = ?",
                                (now_iso(), dumps({"action": action, "error": getattr(exc, "message", str(exc)),
                                                   "error_code": getattr(exc, "error_code", "GENERATION_FAILED")}),
                                 job_id))
                self.runs.finish(job_id, "FAILED", {"error": getattr(exc, "message", str(exc))})
            finally:
                self._stops.pop(job_id, None)

        threading.Thread(target=work, name=job_id, daemon=True).start()
        return job_id

    def _resolve(self, job_id: str, req: JobRequest) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen: set[int] = set()
        for sel in req.selections:
            story = None
            if sel.user_story_id is not None:
                story = self.tfs.detail(sel.user_story_id)
                linked = [int(c["id"]) for c in story["traceability"]["test_cases"]]
                titles = {int(c["id"]): c["title"] for c in story["traceability"]["test_cases"]}
                ids = [int(t) for t in sel.test_case_ids] if sel.test_case_ids else linked
                if not ids:
                    raise QAError("STORY_NO_TEST_CASES",
                                  f"User Story #{sel.user_story_id} has no linked Test Cases in TFS.", status=409)
            else:
                ids, titles = [int(t) for t in sel.test_case_ids or []], {}
            for tc in ids:
                if tc in seen:
                    continue
                seen.add(tc)
                items.append({"user_story_id": str(story["id"]) if story else None,
                              "user_story_title": story["title"] if story else None,
                              "test_case_id": str(tc), "test_case_title": titles.get(tc)})
        if not items:
            raise QAError("NOTHING_SELECTED", "Select at least one User Story or Test Case.")
        return items

    def _run_job(self, job_id: str, req: JobRequest, stop: threading.Event, action: str,
                 script_id: str | None) -> None:
        resolved = self._resolve(job_id, req)
        for n, it in enumerate(resolved):
            self.db.execute(
                "INSERT INTO framework_job_items (job_id, position, user_story_id, user_story_title, test_case_id,"
                " test_case_title, action, status, script_id, log) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (job_id, n, it["user_story_id"], it["user_story_title"], it["test_case_id"], it["test_case_title"],
                 action, "WAITING", script_id, "[]"))
        self.db.execute("UPDATE framework_jobs SET status = 'WAITING' WHERE id = ?", (job_id,))
        with self._gen_lock:  # one generation job at a time; others wait their turn
            self.db.execute("UPDATE framework_jobs SET status = 'RUNNING' WHERE id = ?", (job_id,))
            cache: dict[str, Any] = {"stories": {}, "inventory": {}, "flows": None}
            for item in self.db.query("SELECT * FROM framework_job_items WHERE job_id = ? ORDER BY position", (job_id,)):
                if stop.is_set():
                    break
                self._process_item(job_id, item, req, cache, stop)
            if stop.is_set():
                self.db.execute("UPDATE framework_job_items SET status = 'STOPPED', error = 'Generation stopped by the"
                                " user.', finished_at = ? WHERE job_id = ? AND status = 'WAITING'", (now_iso(), job_id))
        summary = self.job(job_id)["summary"]
        status = "STOPPED" if stop.is_set() else ("FAILED" if summary["failed"] and not summary["completed"] else "COMPLETED")
        self.db.execute("UPDATE framework_jobs SET status = ?, finished_at = ?, summary = ? WHERE id = ?",
                        (status, now_iso(), dumps({**summary, "action": action}), job_id))
        self.runs.finish(job_id, "PASSED" if status == "COMPLETED" else status,
                         {"generated": summary["completed"], "failed": summary["failed"]})

    def stop_job(self, job_id: str) -> bool:
        ev = self._stops.get(job_id)
        if ev is None:
            return False
        ev.set()
        return True

    def job(self, job_id: str) -> dict[str, Any]:
        if not _JOB_ID.match(job_id or ""):
            raise QAError("JOB_ID_INVALID", f"Invalid generation job id '{job_id}'.", status=400)
        row = self.db.query_one("SELECT * FROM framework_jobs WHERE id = ?", (job_id,))
        if row is None:
            raise QAError("JOB_NOT_FOUND", f"Generation job {job_id} does not exist.", status=404)
        items = self.db.query("SELECT * FROM framework_job_items WHERE job_id = ? ORDER BY position", (job_id,))
        for it in items:
            it["details"] = loads(it["details"]) if it["details"] else None
            it["log"] = loads(it["log"]) if it["log"] else []
        done = {"GENERATED", "UPDATED", "UNCHANGED", "SKIPPED"}
        counts = {"total": len(items), "completed": sum(i["status"] in done for i in items),
                  "running": sum(i["status"] == "RUNNING" for i in items),
                  "waiting": sum(i["status"] == "WAITING" for i in items),
                  "failed": sum(i["status"] == "FAILED" for i in items),
                  "stopped": sum(i["status"] == "STOPPED" for i in items)}
        finished = counts["completed"] + counts["failed"] + counts["stopped"]
        counts["progress"] = int(finished * 100 / counts["total"]) if counts["total"] else 0
        stories: dict[str, dict[str, Any]] = {}
        for it in items:
            key = it["user_story_id"] or "-"
            s = stories.setdefault(key, {"user_story_id": it["user_story_id"], "user_story_title": it["user_story_title"],
                                         "total": 0, "generated": 0, "running": 0, "waiting": 0, "failed": 0,
                                         "stopped": 0})
            s["total"] += 1
            s["generated"] += it["status"] in done
            s["running"] += it["status"] == "RUNNING"
            s["waiting"] += it["status"] == "WAITING"
            s["failed"] += it["status"] == "FAILED"
            s["stopped"] += it["status"] == "STOPPED"
        summary = loads(row["summary"]) if row["summary"] else {}
        return {"id": job_id, "status": row["status"], "created_at": row["created_at"], "finished_at": row["finished_at"],
                "request": loads(row["request"]), "error": summary.get("error"), "error_code": summary.get("error_code"),
                "action": summary.get("action", "generate"), "summary": counts, "stories": list(stories.values()),
                "items": items}

    def jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT id FROM framework_jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            j = self.job(r["id"])
            out.append({k: j[k] for k in ("id", "status", "created_at", "finished_at", "summary", "action", "error")})
        return out

    def retry_item(self, item_id: int) -> str:
        item = self.db.query_one("SELECT * FROM framework_job_items WHERE id = ?", (item_id,))
        if item is None:
            raise QAError("JOB_ITEM_NOT_FOUND", f"Generation item {item_id} does not exist.", status=404)
        job = self.db.query_one("SELECT request FROM framework_jobs WHERE id = ?", (item["job_id"],))
        req = JobRequest.model_validate(loads(job["request"]))
        req.selections = [JobSelection(user_story_id=int(item["user_story_id"]) if item["user_story_id"] else None,
                                       test_case_ids=[int(item["test_case_id"])])]
        if self._existing_by_tc([item["test_case_id"]]):
            req.on_existing = "regenerate"
        return self.start_job(req)

    # ---- one Test Case ----------------------------------------------------------------------

    def _item_update(self, item_id: int, **fields: Any) -> None:
        if "details" in fields:
            fields["details"] = dumps(fields["details"]) if fields["details"] is not None else None
        if "log" in fields:
            fields["log"] = dumps(fields["log"])
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE framework_job_items SET {sets} WHERE id = ?", [*fields.values(), item_id])

    def _process_item(self, job_id: str, item: dict[str, Any], req: JobRequest, cache: dict[str, Any],
                      stop: threading.Event) -> None:
        tc = item["test_case_id"]
        lines: list[str] = []

        def note(text: str) -> None:
            lines.append(f"{now_iso()[11:19]} {text}")
            self._item_update(item["id"], log=lines)

        self._item_update(item["id"], status="RUNNING", started_at=now_iso())
        existing = self._existing_by_tc([tc]).get(tc, [])
        target: dict[str, Any] | None = None
        variant = 1
        if item["script_id"]:
            target = self._record(item["script_id"])
        elif existing:
            if req.on_existing == "skip":
                note(f"Existing automation found ({existing[0]['id']}); skipped as requested.")
                self._item_update(item["id"], status="SKIPPED", script_id=existing[0]["id"], finished_at=now_iso())
                return
            if req.on_existing == "regenerate":
                target = self._record(existing[0]["id"])
            else:
                variant = 1 + max(_variant_of(e["file"]) for e in existing)
        try:
            with self.agent.activity(f"Creating Playwright automation for Test Case #{tc}", run_id=job_id,
                                     test_case_id=tc, user_story_id=item["user_story_id"]) as act:
                act.event(names.AUTOMATION_GENERATION_STARTED, f"Automation Engineering Agent reading Test Case #{tc}")
                result = self._generate(job_id, item, req, cache, target, variant, act, note, stop)
                act.event(names.AUTOMATION_GENERATION_COMPLETED, result["message"], script_id=result["script_id"])
                act.complete(result["message"])
            self._item_update(item["id"], status=result["status"], script_id=result["script_id"],
                              details=result["details"], finished_at=now_iso(), test_case_title=result["title"])
        except QAError as exc:
            note(f"FAILED: {exc.message}")
            self._item_update(item["id"], status="STOPPED" if exc.error_code == "GENERATION_STOPPED" else "FAILED",
                              error_code=exc.error_code, error=exc.message,
                              details={"details": exc.details} if exc.details is not None else None,
                              finished_at=now_iso())
        except Exception as exc:  # never hide the real reason
            note(f"FAILED: {type(exc).__name__}: {exc}")
            log.exception(f"Automation generation for Test Case #{tc} failed")
            self._item_update(item["id"], status="FAILED", error_code="PYTHON_GENERATION_FAILED",
                              error=f"Python generation failed: {type(exc).__name__}: {exc}", finished_at=now_iso())

    def _knowledge_text(self, tc: str, application: str | None) -> str | None:
        if self.knowledge is None:
            return None
        try:
            found = self.knowledge.for_test_case(tc, application) or self.knowledge.for_test_case(tc)
        except Exception:
            return None
        if not found:
            return None
        lines = []
        for st in found["test_case"].get("steps", []):
            m = st.get("match")
            where = f"{m['page']}" + (f" / {m['element']} ({m['selector']})" if m and m.get("selector") else "") if m else "NOT FOUND"
            lines.append(f"Step {st['index']}: {st['action']} -> {where}")
        if found.get("page_objects"):
            lines.append("Page Objects: " + ", ".join(found["page_objects"]))
        for f in found.get("flows", [])[:5]:
            lines.append(f"Business flow: {f['name']}")
        return "\n".join(lines)

    def _ai_label(self) -> str:
        return self.ai.label()

    def _story(self, cache: dict[str, Any], story_id: str | None) -> dict[str, Any] | None:
        if not story_id:
            return None
        if story_id not in cache["stories"]:
            cache["stories"][story_id] = self.tfs.detail(int(story_id))
        return cache["stories"][story_id]

    def _inventory(self, cache: dict[str, Any], req: JobRequest, job_id: str, note: Any) -> Inventory:
        key = f"{req.base_url}|{req.discovery_id or ''}"
        if key not in cache["inventory"]:
            disc = self.automation.discovery.get(req.discovery_id) if req.discovery_id else \
                self.automation.discovery.latest_for(req.base_url, req.application)
            if disc is None:
                note(f"No element inventory for {req.base_url} yet; the Application Discovery Agent is discovering it.")
                disc = self.automation.discovery.crawl(job_id, req.base_url, max_pages=10, max_depth=2,
                                                       headless=req.headless, application=req.application,
                                                       credential_role=req.credential_role,
                                                       environment=req.environment)
            inventory = Inventory(disc)
            if not len(inventory):
                raise QAError("DISCOVERY_EMPTY", "No interactive elements were discovered on the application.", status=409)
            cache["inventory"][key] = inventory
            note(f"Element inventory: {len(inventory)} selectors on {len(inventory.pages)} page(s) (reused for every "
                 "Test Case of this job).")
        return cache["inventory"][key]

    def _generate(self, job_id: str, item: dict[str, Any], req: JobRequest, cache: dict[str, Any],
                  target: dict[str, Any] | None, variant: int, act: Any, note: Any, stop: threading.Event) -> dict[str, Any]:
        tc = item["test_case_id"]
        note(f"Reading Test Case #{tc}")
        case = self.tfs.detail(int(tc))
        steps = parse_tfs_steps(case.get("steps"))
        if not steps:
            raise QAError("TEST_CASE_NO_STEPS", f"Test Case #{tc} has no steps in TFS.", status=409)
        story_id = item["user_story_id"] or next((str(l["id"]) for l in case.get("links", [])
                                                  if l.get("relation") == "Tests"), None)
        story = self._story(cache, story_id)
        criteria = _criteria(story) if story else []
        note(f"Read {len(steps)} step(s) with expected results" + (f"; User Story #{story_id} with {len(criteria)} "
                                                                   f"Acceptance Criteria" if story else ""))
        datasets = [{"name": r["name"], "values": r["dataset"]} for r in
                    reversed(self.design.list_test_data(tfs_test_case_id=int(tc))) if isinstance(r["dataset"], dict)]
        note(f"Test Data: {len(datasets)} data set(s)")
        inventory = self._inventory(cache, req, job_id, note)
        reg = registry.scan(self.ws.root)
        note(f"Framework: {len(reg.page_objects)} Page Object(s), {len(reg.flows)} flow(s), {len(reg.tests)} test(s)"
             " checked for reuse")
        if stop.is_set():
            raise QAError("GENERATION_STOPPED", "Generation stopped by the user.")
        act.progress(30, f"Mapping {len(steps)} manual steps to {len(inventory)} discovered elements")
        manual = "\n".join(f"{i + 1}. {s['action']} => expected: {s['expected']}" for i, s in enumerate(steps))
        flows = "\n".join(f"- {f.name}: {f.doc}" for f in reg.flows.values()) or "(none yet)"
        ac_text = "\n".join(f"{c['id']}: {c['title']}" for c in criteria) or "(none recorded)"
        data_text = "\n".join(f"{d['name']}: {json.dumps(d['values'], ensure_ascii=False)}" for d in datasets) or "(none)"
        user = (f"User Story: {('US' + story_id + ' - ' + story['title']) if story else '(not linked)'}\n"
                f"Acceptance Criteria:\n{ac_text}\n\nTest Case: TC{tc} - {case['title']}\nStart URL: {req.base_url}\n"
                f"Credential role: {req.credential_role or '(choose a role name)'}\n\nManual test steps:\n{manual}\n\n"
                f"Test Data:\n{data_text}\n\nEXISTING FLOWS:\n{flows}\n\n"
                f"ELEMENT INVENTORY (selector | role | name):\n{inventory.text()}")
        known = self._knowledge_text(tc, req.application)
        if known:
            note("Unified QA Knowledge found for this Test Case (requirement to application mapping)")
            user += f"\n\nUNIFIED QA KNOWLEDGE (from Intelligent Discovery; prefer these mapped elements):\n{known}"
        note(f"Asking the {self._ai_label()} to map the steps")
        try:
            mapped = self.ai.complete_json(SYSTEM_PROMPT, user, MappedTest, max_tokens=6000)
        except QAError as exc:
            if exc.error_code in AI_ERRORS:
                raise QAError("AI_UNAVAILABLE", f"{self._ai_label()} unavailable: {exc.message}", status=503,
                              details=exc.details) from exc
            raise
        note(f"Mapped {len(mapped.steps)} automation step(s)")
        if stop.is_set():
            raise QAError("GENERATION_STOPPED", "Generation stopped by the user.")
        ctx = CaseContext(
            test_case_id=tc, test_case_title=case["title"], steps=steps, user_story_id=story_id,
            user_story_title=story["title"] if story else None, sprint=_sprint(case.get("iteration_path")),
            project=self.tfs.profile().get("project"), acceptance_criteria=criteria, datasets=datasets,
            base_url=req.base_url, application=req.application, environment=req.environment,
            credential_role=req.credential_role, variant=variant, generated_at=now_iso()[:16].replace("T", " "))
        act.progress(55, "Building Page Objects, flows and the test (reusing existing components)")
        with self._write_lock:
            build = FrameworkBuilder(reg, inventory, self.ws.read).build(ctx, mapped)
            changes = dict(build.changes)
            test_rel, data_rel = build.test_rel, build.data_rel
            if target:  # keep the script's files; merge the user's data edits
                changes = self._retarget(changes, build, target)
                test_rel, data_rel = target["file"], target["data_file"]
            for comp in build.components:
                note(f"{comp['type'].replace('_', ' ').title()} {comp['name']}: {comp['change']}"
                     + (f" ({', '.join(comp['added'])})" if comp["added"] else ""))
            for imp in build.impact:
                note(f"Impact analysis: {imp['component']} is used by {len(imp['affected_tests'])} test(s); "
                     f"{imp['risk']}")
            act.progress(70, "Validating Python syntax, imports, Page Objects, flows, fixtures and test data")
            affected = sorted({t for imp in build.impact for t in imp["affected_tests"]} - {test_rel})
            report = self._validate(changes, test_rel, affected)
            for check in report.checks:
                note(f"Validation - {check['name']}: {check['status']} {check['detail']}")
            if not report.ok:
                first = report.failures()[0]
                raise QAError(report.first_error_code(), f"Validation failed ({first['name']}): {first['detail']}",
                              status=422, details=report.checks)
            current = {rel: (self.ws.read(rel) if self.ws.exists(rel) else None) for rel in changes}
            changed = {rel: content for rel, content in changes.items() if current[rel] != content}
            if target and not changed:
                note("The script already matches the Test Case: nothing to update.")
                return {"status": "UNCHANGED", "script_id": target["id"], "title": case["title"],
                        "message": f"Automation for Test Case #{tc} is up to date",
                        "details": {"validation": report.checks, "components": build.components}}
            if stop.is_set():
                raise QAError("GENERATION_STOPPED", "Generation stopped by the user.")
            act.progress(85, f"Saving {len(changed)} file(s)")
            written = self._commit(changed)
            if not self.ws.exists(test_rel) or sha256(self.ws.path(test_rel).read_bytes()) != \
                    sha256(changes[test_rel].encode()):
                raise QAError("SCRIPT_WRITE_FAILED", f"{test_rel} was not found after saving.", status=500)
            note(f"File verified: {test_rel} ({len(changes[test_rel])} bytes)")
            record = self._register(target, ctx, req, build, test_rel, data_rel, job_id, report)
        status = "UPDATED" if target else "GENERATED"
        return {"status": status, "script_id": record["id"], "title": case["title"],
                "message": f"Playwright automation {status.lower()} for Test Case #{tc}: {Path(test_rel).name} "
                           f"(file verified)",
                "details": {"validation": report.checks, "components": build.components, "impact": build.impact,
                            "files": [w["path"] for w in written], "notes": build.notes, "file": test_rel,
                            "file_verified": True}}

    def _retarget(self, changes: dict[str, str], build: Any, target: dict[str, Any]) -> dict[str, str]:
        test_src = changes.pop(build.test_rel)
        data_src = changes.pop(build.data_rel)
        new_name, old_name = Path(build.data_rel).stem, Path(target["data_file"]).stem
        test_src = test_src.replace(f"pytest.mark.test_data({codegen.py_str(new_name)})",
                                    f"pytest.mark.test_data({codegen.py_str(old_name)})")
        data = json.loads(data_src)
        if self.ws.exists(target["data_file"]):
            try:
                old = json.loads(self.ws.read(target["data_file"]))
                generated = data["combinations"][0]["values"] if data.get("combinations") else {}
                combos = old.get("combinations") or data["combinations"]
                for combo in combos:
                    combo["values"] = {**generated, **(combo.get("values") or {})}
                data["combinations"] = combos
                data["fields"] = {**data.get("fields", {}), **(old.get("fields") or {})}
            except (ValueError, KeyError, TypeError):
                pass
        changes[target["file"]] = test_src
        changes[target["data_file"]] = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        return changes

    def _validate(self, changes: dict[str, str], test_rel: str | None, affected: list[str],
                  dependents: list[str] | None = None) -> validator.Report:
        """Validate the changed files (and the files that call them) in a staging copy of the workspace."""
        with self.workspace().staging(changes) as stage:
            files = list(dict.fromkeys([*changes, *(d for d in dependents or [] if (stage / d).is_file())]))
            return validator.validate(stage, files, test_rel=test_rel,
                                      collect_tests=([test_rel] if test_rel else []) + affected)

    @staticmethod
    def _dependents(reg: registry.Registry, names: list[str]) -> list[str]:
        """Flow modules and tests that call the given Page Objects or flows."""
        flows = {reg.flows[f].module for n in names if n in reg.page_objects for f in reg.flows_using(n)}
        tests = {t for n in names for t in reg.tests_using(n)}
        return sorted(flows) + sorted(tests)

    def _commit(self, changes: dict[str, str]) -> list[dict[str, Any]]:
        """Write every file atomically; if any write fails, put every file back as it was."""
        written: list[dict[str, Any]] = []
        try:
            for rel, content in changes.items():
                existed = self.ws.exists(rel)
                info = self.ws.write(rel, content, reason="AI-Driven Automation")
                info["existed"] = existed
                written.append(info)
        except QAError:
            for info in reversed(written):
                self.ws.restore_backup(info["path"], info["previous"] if info["existed"] else None)
            raise
        return written

    # ---- inventory records --------------------------------------------------------------------

    def _record(self, script_id: str) -> dict[str, Any]:
        if not _SCRIPT_ID.match(script_id or ""):
            raise QAError("SCRIPT_ID_INVALID", f"Invalid script id '{script_id}'.", status=400)
        row = self.db.query_one("SELECT * FROM framework_scripts WHERE id = ?", (script_id,))
        if row is None:
            raise QAError("SCRIPT_NOT_FOUND", f"Script {script_id} does not exist.", status=404)
        for key in ("acceptance_criteria", "components", "warnings", "manual_steps"):
            row[key] = loads(row[key]) if row[key] else []
        return row

    def _register(self, target: dict[str, Any] | None, ctx: CaseContext, req: JobRequest, build: Any, test_rel: str,
                  data_rel: str, job_id: str, report: validator.Report) -> dict[str, Any]:
        ts = now_iso()
        criteria = [c for c in ctx.acceptance_criteria if c["id"] in build.covered_acceptance_criteria]
        warnings = self._credential_warnings(req.application, req.environment, build.credential_role,
                                             target["id"] if target else None) if build.credential_role else []
        fields = {
            "project": ctx.project, "sprint": ctx.sprint, "user_story_id": ctx.user_story_id,
            "user_story_title": ctx.user_story_title, "acceptance_criteria": dumps(criteria),
            "test_case_id": ctx.test_case_id, "test_case_title": ctx.test_case_title, "file": test_rel,
            "data_file": data_rel, "application": req.application, "environment": req.environment,
            "credential_role": build.credential_role, "base_url": req.base_url, "status": "GENERATED",
            "updated_at": ts, "file_sha256": sha256(self.ws.path(test_rel).read_bytes()),
            "components": dumps(build.components), "notes": "\n".join(build.notes) or None,
            "warnings": dumps(warnings), "job_id": job_id, "manual_steps": dumps(ctx.steps),
        }
        if target:
            sid = target["id"]
            version = target["version"] + 1
            fields["version"] = version
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.db.execute(f"UPDATE framework_scripts SET {sets} WHERE id = ?", [*fields.values(), sid])
            self._snapshot(sid, version, "Regenerated / updated by the Automation Engineering Agent", "regenerated",
                           {"job_id": job_id, "validation": report.checks})
        else:
            sid = "FWS-" + uuid.uuid4().hex[:10].upper()
            fields.update({"id": sid, "method": METHOD, "version": 1, "generated_at": ts})
            cols = ", ".join(fields)
            self.db.execute(f"INSERT INTO framework_scripts ({cols}) VALUES ({', '.join('?' for _ in fields)})",
                            list(fields.values()))
            self._snapshot(sid, 1, "Original AI-generated script", "generated", {"job_id": job_id,
                                                                                  "validation": report.checks})
        return self._record(sid)

    def _snapshot(self, script_id: str, version: int, reason: str, kind: str, details: dict[str, Any] | None = None) -> None:
        rec = self._record(script_id)
        folder = self.ws.root / ".versions" / "scripts" / script_id
        folder.mkdir(parents=True, exist_ok=True)
        test_snap = data_snap = None
        if self.ws.exists(rec["file"]):
            path = folder / f"v{version:04d}.py"
            path.write_bytes(self.ws.path(rec["file"]).read_bytes())
            test_snap = path.relative_to(self.ws.root).as_posix()
        if self.ws.exists(rec["data_file"]):
            path = folder / f"v{version:04d}.json"
            path.write_bytes(self.ws.path(rec["data_file"]).read_bytes())
            data_snap = path.relative_to(self.ws.root).as_posix()
        self.db.execute("INSERT INTO framework_versions (script_id, version, reason, kind, created_at, test_snapshot,"
                        " data_snapshot, details) VALUES (?,?,?,?,?,?,?,?)",
                        (script_id, version, reason, kind, now_iso(), test_snap, data_snap, dumps(details or {})))

    def _new_version(self, rec: dict[str, Any], reason: str, kind: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
        version = rec["version"] + 1
        sha = sha256(self.ws.path(rec["file"]).read_bytes()) if self.ws.exists(rec["file"]) else None
        self.db.execute("UPDATE framework_scripts SET version = ?, updated_at = ?, file_sha256 = ? WHERE id = ?",
                        (version, now_iso(), sha, rec["id"]))
        self._snapshot(rec["id"], version, reason, kind, details)
        return self._record(rec["id"])

    def _credential_warnings(self, application: str | None, environment: str | None, role: str | None,
                             script_id: str | None) -> list[str]:
        if not role:
            return []
        src = self.credential_source(application, environment, role, script_id)
        if src["username"] and src["has_password"]:
            return []
        return [f"Application credentials missing for role '{role}'"
                + (f" of {application}" if application else "") + ": set them in Credentials before Play."]

    def inventory(self) -> list[dict[str, Any]]:
        ws = self.workspace()
        rows = self.db.query("SELECT * FROM framework_scripts WHERE archived = 0 ORDER BY generated_at DESC")
        out = []
        for r in rows:
            exists = ws.exists(r["file"])
            out.append({
                "id": r["id"], "method": r["method"], "method_name": METHOD_NAMES.get(r["method"], r["method"]),
                "user_story_id": r["user_story_id"], "user_story_title": r["user_story_title"],
                "test_case_id": r["test_case_id"], "test_case_title": r["test_case_title"], "sprint": r["sprint"],
                "script": Path(r["file"]).name, "file": r["file"], "generated_at": r["generated_at"],
                "updated_at": r["updated_at"], "version": r["version"],
                "status": r["status"] if exists else "FILE MISSING", "file_verified": exists,
                "last_result": r["last_result"], "last_run_id": r["last_run_id"], "last_run_at": r["last_run_at"],
                "warnings": loads(r["warnings"]) if r["warnings"] else [], "framework": True,
            })
        last = {x["script_id"]: x for x in self.db.query(
            "SELECT script_id, status, run_id, started_at FROM execution_results e WHERE id IN"
            " (SELECT MAX(id) FROM execution_results GROUP BY script_id)")}
        for s in self.automation.scripts.list():
            res = last.get(s["id"]) or {}
            out.append({
                "id": s["id"], "method": s["mode"], "method_name": METHOD_NAMES.get(s["mode"], s["mode"]),
                "user_story_id": s.get("user_story_id"), "user_story_title": None, "test_case_id": s.get("test_case_id"),
                "test_case_title": s["name"], "sprint": None, "script": f"{s['id']}.json", "file": None,
                "generated_at": s.get("updated_at"), "updated_at": s.get("updated_at"), "version": s.get("version"),
                "status": "SAVED", "file_verified": True, "last_result": res.get("status"),
                "last_run_id": res.get("run_id"), "last_run_at": res.get("started_at"), "warnings": [],
                "framework": False,
            })
        out.sort(key=lambda x: x.get("generated_at") or "", reverse=True)
        return out

    # ---- one script -----------------------------------------------------------------------------

    def script(self, script_id: str) -> dict[str, Any]:
        rec = self._record(script_id)
        ws = self.workspace()
        exists = ws.exists(rec["file"])
        source = ws.read(rec["file"]) if exists else None
        data = self._data(rec)
        role = (data.get("credentials") or {}).get("role") if data else rec["credential_role"]
        return {
            "script": {**rec, "file_verified": exists, "status": rec["status"] if exists else "FILE MISSING",
                       "method_name": METHOD_NAMES.get(rec["method"], rec["method"]), "file_name": Path(rec["file"]).name},
            "source": source, "data": data,
            "flow_logic": self.flow_logic(rec, source) if source else [],
            "credentials": self.credential_source(rec["application"], rec["environment"], role, rec["id"])
            if role else None,
            "versions": self.versions(script_id),
            "components": self.components_of(rec, source),
            "catalog": self.catalog(),
        }

    def catalog(self) -> dict[str, Any]:
        """Page Object methods and flows that a new Flow Logic step can call."""
        reg = registry.scan(self.workspace().root)
        return {
            "page_objects": [{"name": p.class_name, "module": p.module, "doc": p.doc,
                              "methods": [{"name": m.name, "params": m.params, "doc": m.doc} for m in p.methods.values()]}
                             for p in sorted(reg.page_objects.values(), key=lambda x: x.class_name)],
            "flows": [{"name": f.name, "module": f.module, "params": f.params[1:], "doc": f.doc}
                      for f in sorted(reg.flows.values(), key=lambda x: x.name)],
        }

    def _data(self, rec: dict[str, Any]) -> dict[str, Any] | None:
        if not self.ws.exists(rec["data_file"]):
            return None
        try:
            return json.loads(self.ws.read(rec["data_file"]))
        except ValueError:
            return None

    def components_of(self, rec: dict[str, Any], source: str | None) -> list[dict[str, Any]]:
        if not source:
            return []
        reg = registry.scan(self.ws.root)
        try:
            info = registry.parse_test(rec["file"], source)
        except SyntaxError:
            return []
        out = []
        flow_pages: set[str] = set()
        for name in sorted(info.flows):
            f = reg.flows.get(name)
            if f:
                flow_pages |= f.page_objects
                out.append({"type": "flow", "name": name, "module": f.module, "doc": f.doc,
                            "used_by": reg.tests_using(name)})
        for name in sorted(info.page_objects | flow_pages):
            p = reg.page_objects.get(name)
            if p:
                out.append({"type": "page_object", "name": name, "module": p.module, "doc": p.doc, "url": p.url,
                            "locators": len(p.locators), "methods": len(p.methods),
                            "used_by": reg.tests_using(name)})
        return out

    def flow_logic(self, rec: dict[str, Any], source: str) -> list[dict[str, Any]]:
        """The business steps of the test, with the page actions each step performs."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        reg = registry.scan(self.ws.root)
        imported = {a.asname or a.name: n.module for n in tree.body if isinstance(n, ast.ImportFrom) and n.module
                    for a in n.names}
        fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test")), None)
        if fn is None:
            return []
        instances = {t.id: n.value.func.id for n in fn.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                     and isinstance(n.value.func, ast.Name) for t in n.targets if isinstance(t, ast.Name)}
        out = []
        for node in fn.body:
            if not (isinstance(node, ast.With) and node.items and isinstance(node.items[0].context_expr, ast.Call)):
                continue
            call = node.items[0].context_expr
            if not (isinstance(call.func, ast.Name) and call.func.id == "step") or not call.args:
                continue
            title = call.args[0].value if isinstance(call.args[0], ast.Constant) else ast.unparse(call.args[0])
            kw = {k.arg: (k.value.value if isinstance(k.value, ast.Constant) else ast.unparse(k.value))
                  for k in call.keywords if k.arg}
            actions = []
            for stmt in node.body:
                for c in [n for n in ast.walk(stmt) if isinstance(n, ast.Call)]:
                    actions += self._describe_call(c, instances, imported, reg)
            out.append({"title": title, "checkpoint": bool(kw.get("checkpoint")), "tc_step": kw.get("tc_step"),
                        "lineno": node.lineno, "end_lineno": node.end_lineno, "actions": actions,
                        "code": [ast.unparse(s) for s in node.body]})
        return out

    def _describe_call(self, call: ast.Call, instances: dict[str, str], imported: dict[str, str],
                       reg: registry.Registry) -> list[str]:
        if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and \
                call.func.value.id in instances:
            cls = instances[call.func.value.id]
            p = reg.page_objects.get(cls)
            m = p.methods.get(call.func.attr) if p else None
            if call.func.attr == "open":
                return [f"Open {cls[:-4] if cls.endswith('Page') else cls} page ({p.url if p else ''})"]
            return [(m.doc.rstrip(".") if m and m.doc else call.func.attr.replace("_", " ").capitalize())
                    + (f" ({', '.join(ast.unparse(a) for a in call.args)})" if call.args else "")]
        if isinstance(call.func, ast.Name) and call.func.id in reg.flows and imported.get(call.func.id, "").startswith("flows"):
            f = reg.flows[call.func.id]
            out = []
            for cls, method in f.calls:
                p = reg.page_objects.get(cls)
                m = p.methods.get(method) if p else None
                out.append(f"Open {cls[:-4]} page" if method == "open" else
                           (m.doc.rstrip(".") if m and m.doc else method.replace("_", " ").capitalize()))
            return out
        return []

    # ---- editing ------------------------------------------------------------------------------

    def save_source(self, script_id: str, source: str, reason: str | None = None) -> dict[str, Any]:
        rec = self._record(script_id)
        if not source.strip():
            raise QAError("PYTHON_SYNTAX_INVALID", "The script is empty.", status=422)
        source = source.replace("\r\n", "\n")
        if not source.endswith("\n"):
            source += "\n"
        with self._write_lock:
            if self.ws.exists(rec["file"]) and self.ws.read(rec["file"]) == source:
                return {"saved": False, "script": self.script(script_id)}
            report = self._validate({rec["file"]: source}, rec["file"], [])
            if not report.ok:
                raise QAError(report.first_error_code(), "The script was not saved: " +
                              "; ".join(f"{c['name']}: {c['detail']}" for c in report.failures()), status=422,
                              details=report.checks)
            self._commit({rec["file"]: source})
            self._new_version(rec, reason or "Script edited", "edited", {"validation": report.checks})
        return {"saved": True, "validation": report.checks, "script": self.script(script_id)}

    def save_data(self, script_id: str, combinations: list[dict[str, Any]], fields: dict[str, Any] | None = None,
                  reason: str | None = None) -> dict[str, Any]:
        rec = self._record(script_id)
        data = self._data(rec) or {"test_case_id": rec["test_case_id"], "base_url": rec["base_url"],
                                   "credentials": None, "fields": {}, "combinations": []}
        clean = []
        names_seen = set()
        for n, combo in enumerate(combinations, start=1):
            name = str(combo.get("name") or f"Combination {n}").strip()[:80]
            if name in names_seen:
                raise QAError("TEST_DATA_INVALID", f"Two combinations are named '{name}'.", status=422)
            names_seen.add(name)
            values = combo.get("values") or {}
            if not isinstance(values, dict):
                raise QAError("TEST_DATA_INVALID", f"Combination '{name}' values must be fields and values.", status=422)
            for key, value in values.items():
                if isinstance(value, str) and "{{CRED:" in value:
                    raise QAError("TEST_DATA_INVALID", "Credentials are set in Credentials, not in Test Data.", status=422)
            clean.append({"name": name, "values": values})
        if not clean:
            raise QAError("TEST_DATA_INVALID", "Keep at least one combination.", status=422)
        data["combinations"] = clean
        if fields is not None:
            data["fields"] = fields
        content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        with self._write_lock:
            report = self._validate({rec["data_file"]: content}, rec["file"], [])
            if not report.ok:
                raise QAError(report.first_error_code(), "The test data was not saved: " +
                              "; ".join(f"{c['name']}: {c['detail']}" for c in report.failures()), status=422,
                              details=report.checks)
            self._commit({rec["data_file"]: content})
            self._new_version(rec, reason or "Test data changed", "data")
        return self.script(script_id)

    def edit_flow(self, script_id: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
        """Rename, move, remove or add business steps; the Python is rewritten and validated before saving."""
        rec = self._record(script_id)
        source = self.ws.read(rec["file"])
        tree = ast.parse(source)
        fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test")), None)
        if fn is None:
            raise QAError("FLOW_EDIT_INVALID", "The script has no test function.", status=422)
        lines = source.splitlines()
        blocks = [n for n in fn.body if isinstance(n, ast.With)]
        if not blocks:
            raise QAError("FLOW_EDIT_INVALID", "The script has no business steps to edit.", status=422)
        texts = ["\n".join(lines[b.lineno - 1:b.end_lineno]) for b in blocks]
        first, last = blocks[0].lineno, blocks[-1].end_lineno or blocks[-1].lineno
        order = list(range(len(blocks)))
        new_blocks: dict[int, str] = {}
        for op in operations:
            kind = op.get("op")
            index = int(op.get("index", -1))
            if kind != "add" and not 0 <= index < len(order):
                raise QAError("FLOW_EDIT_INVALID", f"There is no step {index + 1}.", status=422)
            if kind == "rename":
                title = str(op.get("title") or "").strip()
                if not title:
                    raise QAError("FLOW_EDIT_INVALID", "A step needs a title.", status=422)
                bi = order[index]
                text = new_blocks.get(bi, texts[bi]) if bi >= 0 else ""
                head, _, rest = text.partition("\n")
                head = re.sub(r'step\((?:"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')',
                              lambda m: f"step({codegen.py_str(title)}", head, count=1)
                new_blocks[bi] = head + ("\n" + rest if rest else "")
            elif kind == "move":
                to = int(op.get("to", index))
                if not 0 <= to < len(order):
                    raise QAError("FLOW_EDIT_INVALID", f"There is no position {to + 1}.", status=422)
                order.insert(to, order.pop(index))
            elif kind == "remove":
                order.pop(index)
            elif kind == "add":
                text = self._new_step_block(rec, op, source)
                key = -(len(new_blocks) + 1000)
                new_blocks[key] = text
                position = int(op.get("index", len(order)))
                order.insert(max(0, min(position, len(order))), key)
            else:
                raise QAError("FLOW_EDIT_INVALID", f"Unknown flow change '{kind}'.", status=422)
        body = [new_blocks.get(i, texts[i] if i >= 0 else "") for i in order]
        if not body:
            raise QAError("FLOW_EDIT_INVALID", "Keep at least one step.", status=422)
        new_source = "\n".join(lines[:first - 1] + "\n".join(body).splitlines() + lines[last:]) + "\n"
        new_source = self._ensure_imports(new_source, [op for op in operations if op.get("op") == "add"])
        return self.save_source(script_id, new_source, "Flow Logic edited")

    def _new_step_block(self, rec: dict[str, Any], op: dict[str, Any], source: str) -> str:
        title = str(op.get("title") or "").strip()
        call = str(op.get("call") or "").strip()  # "LoginPage.click_sign_in" or "flow:login"
        args = [str(a) for a in op.get("args") or []]
        if not title or not call:
            raise QAError("FLOW_EDIT_INVALID", "A new step needs a title and an action.", status=422)
        rendered = []
        for a in args:
            if a.startswith("data:"):
                rendered.append(f"data[{codegen.py_str(a[5:])}]")
            elif a in ("credentials.username", "credentials.password"):
                rendered.append(a)
            else:
                rendered.append(codegen.py_str(a))
        reg = registry.scan(self.ws.root)
        if call.startswith("flow:"):
            name = call[5:]
            if name not in reg.flows:
                raise QAError("FLOW_REFERENCE_INVALID", f"Flow '{name}' does not exist.", status=422)
            stmt = f"{name}({', '.join(['page', *rendered])})"
        else:
            cls, _, method = call.partition(".")
            p = reg.page_objects.get(cls)
            if p is None or (method not in p.methods and method not in registry.BASE_PAGE_METHODS):
                raise QAError("PAGE_OBJECT_REFERENCE_INVALID", f"{call} does not exist.", status=422)
            var = codegen.snake(cls[:-4] if cls.endswith("Page") else cls, "app") + "_page"
            stmt = f"{cls}(page).{method}({', '.join(rendered)})" if f"{var} = {cls}(page)" not in source else \
                f"{var}.{method}({', '.join(rendered)})"
        checkpoint = ", checkpoint=True" if op.get("checkpoint") else ""
        return f"    with step({codegen.py_str(title)}{checkpoint}):\n        {stmt}"

    def _ensure_imports(self, source: str, adds: list[dict[str, Any]]) -> str:
        if not adds:
            return source
        reg = registry.scan(self.ws.root)
        needed = []
        for op in adds:
            call = str(op.get("call") or "")
            if call.startswith("flow:") and call[5:] in reg.flows:
                f = reg.flows[call[5:]]
                needed.append(f"from {f.module[:-3].replace('/', '.')} import {f.name}")
            elif "." in call:
                cls = call.split(".")[0]
                if cls in reg.page_objects:
                    needed.append(f"from {reg.page_objects[cls].module[:-3].replace('/', '.')} import {cls}")
        lines = source.splitlines()
        missing = [n for n in dict.fromkeys(needed) if n not in lines]
        if missing:
            at = max((i for i, line in enumerate(lines) if line.startswith(("from ", "import "))), default=0)
            lines[at + 1:at + 1] = missing
        params_needed = [p for p, needle in (("data", "data["), ("credentials", "credentials.")) if needle in source]
        text = "\n".join(lines) + "\n"
        m = re.search(r"^def (test\w*)\(([^)]*)\):", text, re.M)
        if m:
            params = [p.strip() for p in m.group(2).split(",") if p.strip()]
            for p in params_needed:
                if p not in params:
                    params.append(p)
            text = text[:m.start()] + f"def {m.group(1)}({', '.join(params)}):" + text[m.end():]
        return text

    # ---- credentials ----------------------------------------------------------------------------

    def _script_secret_key(self, script_id: str) -> str:
        return f"script:{script_id.lower()}"

    def credential_source(self, application: str | None, environment: str | None, role: str | None,
                          script_id: str | None) -> dict[str, Any]:
        """Which credentials a script uses: its own, else the environment's, else the application's."""
        role = role or "user"
        rec = self._record(script_id) if script_id else None
        own_user = rec["cred_username"] if rec else None
        own_pass = self.secrets.has(APP_CREDENTIAL_NAMESPACE, self._script_secret_key(script_id)) if script_id else False
        shared = self.creds.find(application, role, environment)
        shared_pass = self.secrets.has(APP_CREDENTIAL_NAMESPACE, shared["id"]) if shared else False
        source = "script" if (own_user or own_pass) else (
            ("environment" if shared and shared.get("environment") else "application") if shared else None)
        return {
            "role": role, "application": application, "environment": environment, "source": source,
            "username": own_user or (shared["username"] if shared else None),
            "has_password": bool(own_pass or shared_pass), "script_override": bool(own_user or own_pass),
            "shared_id": shared["id"] if shared else None, "store_available": self.secrets.available,
        }

    def resolve_credentials(self, rec: dict[str, Any], role: str) -> tuple[str | None, str | None]:
        shared = self.creds.find(rec["application"], role, rec["environment"])
        username = rec["cred_username"] or (shared["username"] if shared else None)
        password = self.secrets.get(APP_CREDENTIAL_NAMESPACE, self._script_secret_key(rec["id"])) or (
            self.creds.password_of(shared["id"]) if shared else None)
        return username, password

    def save_credentials(self, script_id: str, *, username: str | None, password: str | None, scope: str,
                         application: str | None = None, environment: str | None = None,
                         role: str | None = None) -> dict[str, Any]:
        rec = self._record(script_id)
        if password and not self.secrets.available:
            raise QAError("CREDENTIAL_STORE_UNAVAILABLE", "The Windows Credential Manager is not available.", status=503)
        data = self._data(rec) or {}
        ref = data.get("credentials") or {}
        role = (role or ref.get("role") or rec["credential_role"] or "user").strip()
        application = (application if application is not None else rec["application"]) or None
        environment = (environment if environment is not None else rec["environment"]) or None
        changed_ref = (ref.get("role"), ref.get("application"), ref.get("environment")) != (role, application, environment)
        if scope == "script":
            self.db.execute("UPDATE framework_scripts SET cred_username = ? WHERE id = ?",
                            ((username or "").strip() or None, script_id))
            if password:
                self.secrets.set(APP_CREDENTIAL_NAMESPACE, self._script_secret_key(script_id), password)
        elif scope == "application":
            if not application:
                raise QAError("CREDENTIAL_INVALID", "Choose the application these credentials belong to.", status=422)
            current = self.creds.find(application, role, environment)
            user = (username or "").strip() or (current["username"] if current else "")
            if not user:
                raise QAError("CREDENTIAL_INVALID", "A username is required.", status=422)
            self.creds.save(application, role, user, password, current["login_url"] if current else None, environment)
        elif scope == "clear_script":
            self.db.execute("UPDATE framework_scripts SET cred_username = NULL WHERE id = ?", (script_id,))
            if self.secrets.available:
                self.secrets.delete(APP_CREDENTIAL_NAMESPACE, self._script_secret_key(script_id))
        else:
            raise QAError("CREDENTIAL_INVALID", "Scope must be script, application or clear_script.", status=422)
        self.db.execute("UPDATE framework_scripts SET application = ?, environment = ?, credential_role = ? WHERE id = ?",
                        (application, environment, role, script_id))
        if changed_ref and data:
            data["credentials"] = {"application": application, "environment": environment, "role": role}
            content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
            with self._write_lock:
                self._commit({rec["data_file"]: content})
        rec = self._record(script_id)
        warnings = self._credential_warnings(application, environment, role, script_id)
        self.db.execute("UPDATE framework_scripts SET warnings = ? WHERE id = ?", (dumps(warnings), script_id))
        self._new_version(rec, f"Credentials updated ({'this script' if scope == 'script' else scope.replace('_', ' ')})",
                          "credentials")
        return self.script(script_id)

    # ---- versions -------------------------------------------------------------------------------

    def versions(self, script_id: str) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT version, reason, kind, created_at, test_snapshot, data_snapshot, details FROM"
                             " framework_versions WHERE script_id = ? ORDER BY version", (script_id,))
        for r in rows:
            r["details"] = loads(r["details"]) if r["details"] else {}
        return rows

    def _version_row(self, script_id: str, version: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM framework_versions WHERE script_id = ? AND version = ? ORDER BY id DESC",
                                (script_id, int(version)))
        if row is None:
            raise QAError("SCRIPT_VERSION_NOT_FOUND", f"Version {version} of {script_id} does not exist.", status=404)
        return row

    def version_content(self, script_id: str, version: int) -> dict[str, Any]:
        self._record(script_id)
        row = self._version_row(script_id, version)
        return {"version": row["version"], "reason": row["reason"], "created_at": row["created_at"],
                "source": self.ws.read_backup(row["test_snapshot"]) if row["test_snapshot"] else None,
                "data": self.ws.read_backup(row["data_snapshot"]) if row["data_snapshot"] else None}

    def compare(self, script_id: str, a: int, b: int | None) -> dict[str, Any]:
        rec = self._record(script_id)
        left = self.version_content(script_id, a)
        if b is None:
            right = {"version": "current", "source": self.ws.read(rec["file"]) if self.ws.exists(rec["file"]) else "",
                     "data": self.ws.read(rec["data_file"]) if self.ws.exists(rec["data_file"]) else ""}
        else:
            right = self.version_content(script_id, b)

        def diff(x: str | None, y: str | None, name: str) -> list[str]:
            return list(difflib.unified_diff((x or "").splitlines(), (y or "").splitlines(),
                                             f"v{left['version']}/{name}", f"{right['version']}/{name}", lineterm=""))

        return {"left": left["version"], "right": right["version"],
                "script_diff": diff(left["source"], right["source"], Path(rec["file"]).name),
                "data_diff": diff(left["data"], right["data"], Path(rec["data_file"]).name)}

    def restore(self, script_id: str, version: int) -> dict[str, Any]:
        rec = self._record(script_id)
        content = self.version_content(script_id, version)
        changes = {}
        if content["source"] is not None:
            changes[rec["file"]] = content["source"]
        if content["data"] is not None:
            changes[rec["data_file"]] = content["data"]
        with self._write_lock:
            report = self._validate(changes, rec["file"], [])
            if not report.ok:
                raise QAError(report.first_error_code(), f"Version {version} cannot be restored: " +
                              "; ".join(f"{c['name']}: {c['detail']}" for c in report.failures()), status=422,
                              details=report.checks)
            self._commit(changes)
            self._new_version(rec, f"Restored version {version}", "restored")
        return self.script(script_id)

    def regenerate(self, script_id: str, base_url: str | None = None) -> str:
        rec = self._record(script_id)
        req = JobRequest(selections=[JobSelection(user_story_id=int(rec["user_story_id"]) if rec["user_story_id"] and
                                                  rec["user_story_id"].isdigit() else None,
                                                  test_case_ids=[int(rec["test_case_id"])])],
                         base_url=base_url or rec["base_url"], application=rec["application"],
                         environment=rec["environment"], credential_role=rec["credential_role"],
                         on_existing="regenerate")
        return self.start_job(req, action="regenerate", script_id=script_id)

    def archive(self, script_id: str) -> None:
        self._record(script_id)
        self.db.execute("UPDATE framework_scripts SET archived = 1, updated_at = ? WHERE id = ?", (now_iso(), script_id))

    # ---- shared components ------------------------------------------------------------------------

    def component(self, rel: str) -> dict[str, Any]:
        rel = self.workspace().rel(rel)
        if not rel.startswith(("page_objects/", "flows/")) or not rel.endswith(".py"):
            raise QAError("PROJECT_PATH_INVALID", "Only Page Objects and flows can be opened here.", status=400)
        reg = registry.scan(self.ws.root)
        names_in = [p.class_name for p in reg.page_objects.values() if p.module == rel] + \
            [f.name for f in reg.flows.values() if f.module == rel]
        affected = sorted({t for n in names_in for t in reg.tests_using(n)})
        scripts = {r["file"]: r["id"] for r in self.db.query("SELECT id, file FROM framework_scripts WHERE archived = 0")}
        return {"path": rel, "source": self.ws.read(rel), "components": names_in, "affected_tests": affected,
                "dependents": self._dependents(reg, names_in),
                "affected_scripts": [scripts[t] for t in affected if t in scripts],
                "history": self.ws.history(rel)}

    def save_component(self, rel: str, source: str) -> dict[str, Any]:
        """Edit a shared Page Object or flow: impact analysis, then every affected test must still collect."""
        info = self.component(rel)
        source = source.replace("\r\n", "\n")
        if not source.endswith("\n"):
            source += "\n"
        with self._write_lock:
            report = self._validate({info["path"]: source}, None, info["affected_tests"], info["dependents"])
            if not report.ok:
                raise QAError(report.first_error_code(), "Not saved: " + "; ".join(
                    f"{c['name']}: {c['detail']}" for c in report.failures()), status=422,
                              details={"checks": report.checks, "affected_tests": info["affected_tests"]})
            self._commit({info["path"]: source})
            for sid in info["affected_scripts"]:
                self._new_version(self._record(sid), f"Shared component {info['path']} changed", "component",
                                  {"component": info["path"]})
        return {**self.component(rel), "validation": report.checks}

    # ---- execution ---------------------------------------------------------------------------------

    def run(self, script_id: str, *, combination: str | None = None, headless: bool | None = None,
            background: bool = True) -> str | dict[str, Any]:
        rec = self._record(script_id)
        if not self.workspace().exists(rec["file"]):
            raise QAError("SCRIPT_FILE_MISSING", f"{rec['file']} does not exist; regenerate or restore the script.",
                          status=409)
        data = self._data(rec) or {}
        env: dict[str, str] = {}
        secrets: list[str] = []
        ref = data.get("credentials")
        if ref:
            role = ref.get("role") or rec["credential_role"] or "user"
            username, password = self.resolve_credentials(rec, role)
            if not username or not password:
                missing = ", ".join(n for n, v in (("username", username), ("password", password)) if not v)
                raise QAError("APP_CREDENTIALS_MISSING", f"Application credentials missing for role '{role}': {missing}. "
                              "Set them in Credentials.", status=409)
            env = {"QA_CRED_USERNAME": username, "QA_CRED_PASSWORD": password}
            secrets = [password]
        source = self.ws.read(rec["file"])
        locators = self._locator_index(source)
        run_id = new_run_id("RUN")
        payload = {**rec, "flow_steps": self.flow_logic(rec, source), "locators": locators}

        def finish(result: dict[str, Any]) -> None:
            self.db.execute("UPDATE framework_scripts SET last_result = ?, last_run_id = ?, last_run_at = ? WHERE id = ?",
                            (result["status"], run_id, result["started_at"], script_id))

        def work() -> dict[str, Any]:
            with self.automation._exec_lock:  # one browser run at a time keeps playback visible and evidence clean
                return self.runner.run(payload, run_id, workspace=self.ws.root, env_extra=env, secrets=secrets,
                                       combination=combination, headless=headless, on_finish=finish)

        self.db.execute("UPDATE framework_scripts SET last_result = 'RUNNING', last_run_id = ?, last_run_at = ? WHERE id = ?",
                        (run_id, now_iso(), script_id))
        if not background:
            return work()
        threading.Thread(target=self._safe(work, run_id), name=run_id, daemon=True).start()
        return run_id

    def run_many(self, script_ids: list[str], *, headless: bool | None = None) -> list[dict[str, Any]]:
        """Play Selected: every script runs separately (one at a time) and has its own result."""
        out = []
        for sid in script_ids:
            try:
                out.append({"script_id": sid, "run_id": self.run(sid, headless=headless)})
            except QAError as exc:
                out.append({"script_id": sid, "run_id": None, "error_code": exc.error_code, "error": exc.message})
        return out

    def run_result(self, script_id: str, run_id: str) -> dict[str, Any]:
        """The complete result of one run: steps, screenshots, failure analysis, healing, logs and trace."""
        rec = self._record(script_id)
        if not re.match(r"^RUN-[0-9A-Za-z-]+$", run_id or ""):
            raise QAError("RUN_ID_INVALID", f"Invalid run id '{run_id}'.", status=400)
        rows = self.automation.runner.results(script_id=script_id, run_id=run_id, limit=1)
        if not rows:
            raise QAError("RUN_NOT_FOUND", f"Run {run_id} of {script_id} does not exist.", status=404)
        path = self.paths.evidence / run_id / "result.json"
        result = rows[0]
        if path.is_file():
            try:
                result = {**result, **json.loads(path.read_text(encoding="utf-8"))}
            except ValueError:
                pass
        result["healing"] = [h for h in self.automation.runner.healing_events(script_id) if h["run_id"] == run_id]
        result["script"] = {k: rec[k] for k in ("id", "file", "test_case_id", "test_case_title", "user_story_id",
                                                "user_story_title", "acceptance_criteria", "sprint")}
        result["running"] = run_id in self.runner.running()
        return result

    @staticmethod
    def _safe(fn: Any, name: str) -> Any:
        def wrapper() -> None:
            try:
                fn()
            except Exception:
                log.info(f"{name} finished with an error (reported through events)")

        return wrapper

    def _locator_index(self, source: str) -> list[dict[str, Any]]:
        """Selector -> Page Object locator, so a healing record names the component it applies to."""
        reg = registry.scan(self.ws.root)
        return [{"selector": loc.primary(), "component": f"{p.class_name}.{name}", "module": p.module}
                for p in reg.page_objects.values() for name, loc in p.locators.items()]

    def results(self, script_id: str, limit: int = 20) -> list[dict[str, Any]]:
        self._record(script_id)
        return self.automation.runner.results(script_id=script_id, limit=limit)

    def run_logs(self, script_id: str, run_id: str | None = None) -> dict[str, Any]:
        rec = self._record(script_id)
        results = self.automation.runner.results(script_id=script_id, run_id=run_id, limit=1)
        out: dict[str, Any] = {"generation": [], "run": None}
        if rec["job_id"]:
            item = self.db.query_one("SELECT log, error, details FROM framework_job_items WHERE job_id = ? AND"
                                     " test_case_id = ? ORDER BY id DESC", (rec["job_id"], rec["test_case_id"]))
            if item:
                out["generation"] = loads(item["log"]) if item["log"] else []
        if results:
            r = results[0]
            path = Path((r.get("evidence") or {}).get("log") or "")
            text = path.read_text(encoding="utf-8", errors="replace")[-20000:] if path.is_file() and \
                self.paths.evidence.resolve() in path.resolve().parents else ""
            out["run"] = {"run_id": r["run_id"], "status": r["status"], "output": text}
        return out

    # ---- self-healing --------------------------------------------------------------------------------

    def _on_diagnosis(self, result: dict[str, Any], found: dict[str, Any]) -> None:
        """A likely application problem becomes a defect draft for Defect Review (never for automation issues)."""
        if self.defects is None or not found.get("likely_application_problem"):
            return
        from ...defects import analysis

        category = {"Network/API Failure": "navigation", "Application Performance Problem": "navigation"}.get(
            found["classification"], "assertion")
        product, severity, finding = analysis.CATEGORIES[category]
        rec = self._record(result["script_id"])
        what = found.get("failed_step_title") or "a step"
        passed = [s["description"] for s in result["steps"] if s.get("status") == "PASSED"]
        esc = analysis.html.escape
        repro = (f"<p><strong>Classification:</strong> {esc(found['classification'])} ({found['confidence']}% "
                 f"confidence). {esc(found['reason'])}</p>"
                 f"<p><strong>Steps to reproduce</strong> (automated test {esc(rec['file'])}):</p>"
                 + analysis._li([*passed, f"{what}  <- failed here"])
                 + (f"<p><strong>Expected:</strong> {esc(found['expected'])}</p>" if found.get("expected") else "")
                 + f"<p><strong>Actual:</strong> {esc(found.get('actual') or '')}</p>"
                 + "".join(f"<p>{esc(o)}</p>" for o in found.get("observed", []))
                 + f"<p><strong>Environment:</strong> {esc(result.get('base_url') or '')} · {esc(result['execution_mode'])}"
                   f" · run {esc(result['run_id'])}</p>")
        shots = [p for p in (found["evidence"].get("failure_screenshot"), found["evidence"].get("full_page_screenshot")) if p]
        draft = {
            "source": "functional", "source_run_id": result["run_id"],
            "signature": analysis.signature("framework", rec["id"], found.get("failed_step"), found["classification"]),
            "category": category, "product_defect": product, "title": f"TC{rec['test_case_id']} {rec['test_case_title']}: "
                                                                       f"{what} - {found['classification'].lower()}"[:250],
            "severity": severity, "priority": 2 if severity <= 2 else 3, "repro_steps": repro,
            "facts": {"error": found.get("error"), "failed_step": found.get("failed_step"), "finding": finding,
                      "classification": found["classification"], "confidence": found["confidence"],
                      "script_id": rec["id"], "base_url": result.get("base_url")},
            "evidence": [{"kind": "screenshot", "path": p} for p in shots],
            "test_case_id": rec["test_case_id"], "user_story_id": rec["user_story_id"],
        }
        threading.Thread(target=self._safe(lambda: self.defects.record_draft(draft), result["run_id"]),
                         daemon=True).start()

    def apply_healing(self, healing_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM healing_events WHERE id = ?", (healing_id,))
        if row is None:
            raise QAError("HEALING_NOT_FOUND", f"Healing event {healing_id} does not exist.", status=404)
        if row["result"] != "validated":
            raise QAError("HEALING_NOT_VALIDATED", "Only a healed locator whose run then passed can update the script.",
                          status=409)
        if row["applied"]:
            raise QAError("HEALING_ALREADY_APPLIED", "This healed locator was already applied.", status=409)
        if not row.get("component"):
            raise QAError("HEALING_STALE", "The Page Object locator of this healing is not known; update it manually.",
                          status=409)
        cls, _, name = row["component"].partition(".")
        reg = registry.scan(self.workspace().root)
        page = reg.page_objects.get(cls)
        loc = page.locators.get(name) if page else None
        if loc is None:
            raise QAError("HEALING_STALE", f"{row['component']} no longer exists; review the Page Object.", status=409)
        if loc.primary() != row["original_selector"]:
            raise QAError("HEALING_STALE", "The locator changed since this healing; review it manually.", status=409)
        alternates = [row["original_selector"]] + [a for a in loc.alternates if a != row["healed_selector"]]
        if loc.strategy in ("label", "placeholder"):
            alternates = alternates[1:]  # not a selector the browser understands on its own; the fingerprint stays
        plan = codegen.LocatorPlan(name, "css", row["healed_selector"], None, alternates[:5], loc.description,
                                   loc.fingerprint)
        source = self.ws.read(page.module)
        new_source = codegen.replace_lines(source, loc.lineno, loc.end_lineno, codegen.render_locator(plan))
        affected = reg.tests_using(cls)
        with self._write_lock:
            report = self._validate({page.module: new_source}, None, affected, self._dependents(reg, [cls]))
            if not report.ok:
                raise QAError(report.first_error_code(), "The healed locator was not applied: " + "; ".join(
                    c["detail"] for c in report.failures()), status=422, details=report.checks)
            self._commit({page.module: new_source})
            self.db.execute("UPDATE healing_events SET applied = 1 WHERE id = ?", (healing_id,))
            scripts = {r["file"]: r["id"] for r in self.db.query("SELECT id, file FROM framework_scripts WHERE archived = 0")}
            for test in affected:
                if test in scripts:
                    self._new_version(self._record(scripts[test]),
                                      f"Locator healed: {row['component']} -> {row['healed_selector']} "
                                      f"(confidence {row['confidence']:.2f})", "healed", {"healing_id": healing_id})
        return {"component": row["component"], "module": page.module, "affected_tests": affected,
                "healed_selector": row["healed_selector"]}

    # ---- export --------------------------------------------------------------------------------------

    def export_zip(self) -> bytes:
        ws = self.workspace()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(ws.root.rglob("*")):
                rel = path.relative_to(ws.root)
                if not path.is_file() or rel.parts[0] in (".versions", ".staging", "execution-results") or \
                        "__pycache__" in rel.parts or rel.name == ".framework.json" or rel.name.endswith(".tmp"):
                    continue
                zf.write(path, f"automation-workspace/{rel.as_posix()}")
        return buf.getvalue()


def _sprint(iteration_path: str | None) -> str | None:
    if not iteration_path:
        return None
    return re.split(r"[\\/]", iteration_path)[-1] or iteration_path


def _criteria(story: dict[str, Any]) -> list[dict[str, str]]:
    """Linked Acceptance Criteria work items, else the story's criteria field numbered AC<story>-<n>."""
    linked = story.get("traceability", {}).get("acceptance_criteria") or []
    if linked:
        return [{"id": f"AC{a['id']}", "title": a.get("title") or ""} for a in linked]
    items = split_criteria(html_to_text(story.get("acceptance_criteria")))
    return [{"id": f"AC{story['id']}-{n}", "title": text} for n, text in enumerate(items, start=1)]


def _variant_of(file: str) -> int:
    m = re.search(r"_v(\d+)\.py$", file or "")
    return int(m.group(1)) if m else 1
