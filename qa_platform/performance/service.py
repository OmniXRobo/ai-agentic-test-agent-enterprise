"""Performance Engineering and JMeter Load Testing agents.

Plans are stored in SQLite. Each run writes ``data/performance/{run_id}/`` with
the generated ``plan.jmx``, ``results.jtl``, ``jmeter.log`` and JMeter's own
HTML dashboard under ``report/``. Metrics and SLA checks are computed from the
JTL only; the optional AI analysis is given those numbers and nothing else.

23.3.22: scenarios own a plan (script), an environment with load limits and their
run history. Runs are queued and start one at a time on this PC. Each run has an
execution state (QUEUED, VALIDATING, RUNNING, STOPPING, COMPLETED, FAILED,
CANCELLED, INTERRUPTED, SKIPPED) and, separately, a verdict (PASS, FAIL,
NOT_SPECIFIED, INCONCLUSIVE). The run folder also holds ``config.json`` (the exact
effective configuration and tool versions) and ``generator.json`` (this PC's CPU,
memory and disk during the run).
"""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from .. import __version__
from ..agents.base import BaseAgent, new_run_id
from ..ai.gateway import AIGateway
from ..clock import now_iso
from ..config import Paths, Settings
from ..errors import QAError
from ..events import names
from ..events.bus import EventBus
from ..logging_setup import get_logger
from ..runs import RunStore
from ..storage.db import Database, dumps, loads
from . import generator, jtl, preflight, sla, tools
from . import script as jscript
from .jmx import build_jmx
from .models import (HttpRequest, PerfEnvironment, PerfPlan, PerfScenario, new_environment_id, new_plan_id,
                     new_scenario_id)

log = get_logger("performance")


def _dump(value: Any) -> str | None:
    return None if value is None else dumps(value)
STOP_PORTS = range(4445, 4456)  # JMeter's non-GUI shutdown listener (jmeterengine.nongui.port, maxport)
RUN_GRACE_S = 900  # execution timeout = the test's own duration + this


class PerformanceEngineeringAgent(BaseAgent):
    agent_id = "performance_engineering"


class JMeterLoadAgent(BaseAgent):
    agent_id = "jmeter_load"


class Bottleneck(BaseModel):
    area: str
    evidence: str = Field(description="The specific metric values that support this")
    confidence: str = Field(description="low, medium or high")


class PerfAnalysis(BaseModel):
    summary: str
    observations: list[str]
    suspected_bottlenecks: list[Bottleneck] = []
    recommendations: list[str] = []
    undetermined: list[str] = Field(default=[], description="Questions these results cannot answer")


KIND_TITLES = {"load": "Load test", "stress": "Stress test", "spike": "Spike test", "soak": "Soak test"}


class PerformanceService:
    def __init__(self, paths: Paths, settings: Settings, bus: EventBus, db: Database, runs: RunStore,
                 ai: AIGateway, discovery: Any) -> None:
        self.paths = paths
        self.settings = settings
        self.db = db
        self.runs = runs
        self.ai = ai
        self.discovery = discovery
        self.engineer = PerformanceEngineeringAgent(bus)
        self.jmeter = JMeterLoadAgent(bus)
        self._lock = threading.Lock()  # one JMeter run at a time on this PC (the load generator)
        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        self._stopping: dict[str, str] = {}  # run id -> graceful | emergency
        self._monitors: dict[str, generator.GeneratorMonitor] = {}
        self._live: dict[str, dict[str, Any]] = {}
        self._wake = threading.Event()
        self._closing = threading.Event()
        self._worker: threading.Thread | None = None

    # ---- plans -----------------------------------------------------------------------------

    def list_plans(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM perf_plans ORDER BY updated_at DESC")
        out = []
        for r in rows:
            plan = PerfPlan.model_validate(loads(r["plan"]))
            last = self.db.query_one("SELECT run_id, status, started_at FROM perf_runs WHERE plan_id = ?"
                                     " ORDER BY started_at DESC LIMIT 1", (r["id"],))
            out.append({"id": r["id"], "name": r["name"], "kind": r["kind"], "requests": len(plan.requests),
                        "script": plan.is_script, "script_version": plan.script_version,
                        "peak_users": plan.peak_users(),
                        "duration_s": plan.total_duration_s() if plan.script else plan.profile.duration_s,
                        "updated_at": r["updated_at"], "last_run": last})
        return out

    def get_plan(self, plan_id: str) -> PerfPlan:
        row = self.db.query_one("SELECT * FROM perf_plans WHERE id = ?", (plan_id,))
        if not row:
            raise QAError("PERF_PLAN_NOT_FOUND", f"Performance plan {plan_id} does not exist.", status=404)
        return PerfPlan.model_validate(loads(row["plan"]))

    def save_plan(self, plan: PerfPlan, *, link_scenario: bool = True, scenario_id: str | None = None,
                  note: str | None = None) -> PerfPlan:
        """Save a plan. A new plan joins `scenario_id`, or gets its own scenario so it shows under Scenarios.
        A script plan's JMX is normalized, summarized into `requests` and `profile`, and a new version is kept when
        the JMX changed. Saving never starts a run."""
        is_new = not plan.id
        ts = now_iso()
        if plan.script:
            self._summarize_script(plan)
        if not plan.id:
            plan.id = new_plan_id()
            plan.created_at = ts
        else:
            existing = self.db.query_one("SELECT created_at FROM perf_plans WHERE id = ?", (plan.id,))
            plan.created_at = existing["created_at"] if existing else ts
        plan.updated_at = ts
        self.db.execute(
            "INSERT INTO perf_plans (id, name, kind, plan, created_at, updated_at) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET name = excluded.name, kind = excluded.kind, plan = excluded.plan,"
            " updated_at = excluded.updated_at",
            (plan.id, plan.name, plan.kind, plan.model_dump_json(), plan.created_at, plan.updated_at))
        if plan.script:
            self._keep_version(plan, note)
        if scenario_id:
            sc = self.get_scenario(scenario_id)
            sc.plan_id = plan.id
            self.save_scenario(sc)
        elif is_new and link_scenario:
            self.save_scenario(PerfScenario(name=plan.name, plan_id=plan.id))
        return plan

    # ---- 23.3.23: scripts (JMX) ---------------------------------------------------------------------------

    @staticmethod
    def _summarize_script(plan: PerfPlan) -> None:
        """Normalize the JMX and mirror what it does into `requests` and `profile` (read-only for script plans)."""
        plan.script = jscript.normalize(plan.script or "")
        summary = jscript.analyze_cached(plan.script)["summary"]
        reqs = []
        for r in summary["requests"]:
            if r["host"] and r["url"].lower().startswith(("http://", "https://")):
                try:
                    reqs.append(HttpRequest(name=(r["name"] or r["url"])[:200], method=r["method"]
                                            if r["method"] in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD",
                                                               "OPTIONS") else "GET", url=r["url"]))
                except ValidationError:
                    continue
        plan.requests = reqs
        plan.profile.users = max(1, summary["peak_users"] or 1)
        plan.profile.duration_s = max(10, summary["total_duration_s"] or 10)
        plan.profile.ramp_up_s = max((g["ramp_up_s"] for g in summary["thread_groups"]), default=0)
        plan.profile.think_time_ms = 0  # think time lives in the script's timers

    def _keep_version(self, plan: PerfPlan, note: str | None) -> None:
        digest = jscript.sha256(plan.script or "")
        last = self.db.query_one("SELECT version, sha256 FROM perf_plan_versions WHERE plan_id = ?"
                                 " ORDER BY version DESC LIMIT 1", (plan.id,))
        if last and last["sha256"] == digest:
            if plan.script_version != last["version"]:
                plan.script_version = last["version"]
                self.db.execute("UPDATE perf_plans SET plan = ? WHERE id = ?", (plan.model_dump_json(), plan.id))
            return
        version = (last["version"] if last else 0) + 1
        self.db.execute("INSERT INTO perf_plan_versions (plan_id, version, script, note, sha256, created_at)"
                        " VALUES (?,?,?,?,?,?)", (plan.id, version, plan.script, (note or "")[:500], digest, now_iso()))
        plan.script_version = version
        self.db.execute("UPDATE perf_plans SET plan = ? WHERE id = ?", (plan.model_dump_json(), plan.id))

    def import_script(self, text: str, *, name: str = "", kind: str = "load", filename: str = "",
                      scenario_id: str | None = None) -> PerfPlan:
        """A plan from a JMX file (e.g. saved by the JMeter GUI). Every element is kept as it is, including the ones
        the editor has no form for. Importing never starts a run."""
        root = jscript.parse(text)
        if not name:
            tp = jscript._pairs(jscript._top(root))
            name = (tp[0][0].get("testname") if tp else "") or filename.rsplit(".", 1)[0] or "Imported script"
        plan = PerfPlan(name=name[:200], kind=kind if kind in ("load", "stress", "spike", "soak") else "load",  # type: ignore[arg-type]
                        script=jscript.dump(root), source=f"import {filename}".strip())
        saved = self.save_plan(plan, scenario_id=scenario_id, note=f"Imported {filename}".strip())
        self.engineer.emit(names.PERFORMANCE_GENERATION_STARTED,
                           f"Performance script imported: {saved.name} ({len(saved.requests)} requests)",
                           details={"plan_id": saved.id})
        return saved

    def convert_to_script(self, plan_id: str) -> PerfPlan:
        """Open a generated plan in the script editor: its JMX becomes the script. The form editor no longer applies."""
        plan = self.get_plan(plan_id)
        if plan.script:
            return plan
        plan.script = build_jmx(plan)
        return self.save_plan(plan, note="Converted from the generated plan")

    def script_view(self, plan_id: str) -> dict[str, Any]:
        plan = self.get_plan(plan_id)
        if not plan.script:
            raise QAError("PERF_NOT_A_SCRIPT", "This plan is not a script yet. Open it in the script editor first.",
                          status=409)
        return {"plan": plan.model_dump(exclude={"script"}), "script": plan.script, "version": plan.script_version,
                **jscript.analyze(plan.script)}

    def save_script(self, plan_id: str, text: str, *, base_version: int | None = None, note: str = "",
                    name: str | None = None, kind: str | None = None, sla_: dict[str, Any] | None = None) -> PerfPlan:
        plan = self.get_plan(plan_id)
        if base_version is not None and plan.script and base_version != plan.script_version:
            raise QAError("PERF_SCRIPT_CONFLICT", f"The script was saved elsewhere (now version {plan.script_version}). "
                          "Reload it before saving, so no change is lost.", status=409)
        plan.script = text
        if name:
            plan.name = name[:200]
        if kind in ("load", "stress", "spike", "soak"):
            plan.kind = kind  # type: ignore[assignment]
        if sla_ is not None:
            plan.sla = type(plan.sla).model_validate(sla_)
        return self.save_plan(plan, note=note or None)

    def script_versions(self, plan_id: str) -> list[dict[str, Any]]:
        self.get_plan(plan_id)
        return self.db.query("SELECT version, note, sha256, created_at, length(script) AS size FROM perf_plan_versions"
                             " WHERE plan_id = ? ORDER BY version DESC", (plan_id,))

    def script_version(self, plan_id: str, version: int) -> str:
        row = self.db.query_one("SELECT script FROM perf_plan_versions WHERE plan_id = ? AND version = ?",
                                (plan_id, version))
        if not row:
            raise QAError("PERF_VERSION_NOT_FOUND", f"Version {version} of {plan_id} does not exist.", status=404)
        return row["script"]

    def script_diff(self, plan_id: str, a: int, b: int) -> str:
        return jscript.diff(self.script_version(plan_id, a), self.script_version(plan_id, b), f"version {a}",
                            f"version {b}")

    def restore_version(self, plan_id: str, version: int) -> PerfPlan:
        plan = self.get_plan(plan_id)
        plan.script = self.script_version(plan_id, version)
        return self.save_plan(plan, note=f"Restored version {version}")

    def delete_plan(self, plan_id: str) -> None:
        self.get_plan(plan_id)
        self.db.execute("DELETE FROM perf_plans WHERE id = ?", (plan_id,))  # run results are kept
        self.db.execute("DELETE FROM perf_plan_versions WHERE plan_id = ?", (plan_id,))
        for r in self.db.query("SELECT id FROM perf_scenarios WHERE plan_id = ?", (plan_id,)):
            sc = self.get_scenario(r["id"])  # the scenario and its history stay, archived and without a script
            sc.plan_id, sc.archived = None, True
            self.save_scenario(sc)

    def plan_from_discovery(self, discovery_id: str, *, name: str, kind: str = "load",
                            include_pages: bool = True) -> PerfPlan:
        """Requests from what discovery actually observed: page URLs and GET API endpoints with a sample URL."""
        disc = self.discovery.get(discovery_id)
        requests: list[HttpRequest] = []
        seen: set[str] = set()
        if include_pages:
            for p in disc.get("pages", []):
                if p.get("error") or not p.get("status") or p["status"] >= 400 or p["url"] in seen:
                    continue
                seen.add(p["url"])
                requests.append(HttpRequest(name=f"Page: {p.get('title') or p['url']}"[:200], url=p["url"],
                                            expect_status="2\\d\\d|3\\d\\d"))
        skipped = []
        for e in disc.get("endpoints", []):
            if e["method"] != "GET" or e.get("has_body"):
                skipped.append(f"{e['method']} {e['endpoint']}")
                continue
            url = e.get("sample_url") or e["endpoint"]
            if url in seen:
                continue
            seen.add(url)
            requests.append(HttpRequest(name=f"API: GET {e['endpoint'].split('://', 1)[-1].split('/', 1)[-1]}"[:200],
                                        url=url, expect_status="2\\d\\d"))
        if not requests:
            raise QAError("DISCOVERY_NO_REQUESTS", "This discovery has no successful pages or GET endpoints to load test.",
                          status=409)
        plan = PerfPlan(name=name or disc["base_url"], kind=kind,  # type: ignore[arg-type]
                        requests=requests, source=f"discovery {discovery_id}")
        saved = self.save_plan(plan)
        self.engineer.emit(names.PERFORMANCE_GENERATION_STARTED,
                           f"Performance plan created from discovery: {len(requests)} requests"
                           + (f"; {len(skipped)} endpoints need a request body and were left out" if skipped else ""),
                           details={"plan_id": saved.id, "skipped": skipped})
        return saved

    # ---- environment (tools) ------------------------------------------------------------------------

    def environment(self) -> dict[str, Any]:
        java, jmeter = tools.find_java(self.settings), tools.find_jmeter(self.settings)
        return {"java": java, "java_version": tools.java_version(java) if java else None, "jmeter": jmeter,
                "jmeter_version": preflight.jmeter_version(jmeter) if jmeter else None,
                "ready": bool(java and jmeter), "generator_monitoring": generator.available()}

    # ---- target environments --------------------------------------------------------------------------

    def list_environments(self) -> list[PerfEnvironment]:
        rows = self.db.query("SELECT data FROM perf_environments ORDER BY name COLLATE NOCASE")
        return [PerfEnvironment.model_validate(loads(r["data"])) for r in rows]

    def get_environment(self, env_id: str) -> PerfEnvironment:
        row = self.db.query_one("SELECT data FROM perf_environments WHERE id = ?", (env_id,))
        if not row:
            raise QAError("PERF_ENV_NOT_FOUND", f"Environment {env_id} does not exist.", status=404)
        return PerfEnvironment.model_validate(loads(row["data"]))

    def save_environment(self, env: PerfEnvironment) -> PerfEnvironment:
        ts = now_iso()
        if not env.id:
            env.id, env.created_at = new_environment_id(), ts
        else:
            current = self.get_environment(env.id)
            env.created_at = current.created_at
        env.updated_at = ts
        self.db.execute("INSERT INTO perf_environments (id, name, data, created_at, updated_at) VALUES (?,?,?,?,?)"
                        " ON CONFLICT(id) DO UPDATE SET name = excluded.name, data = excluded.data,"
                        " updated_at = excluded.updated_at",
                        (env.id, env.name, env.model_dump_json(), env.created_at, env.updated_at))
        return env

    def delete_environment(self, env_id: str) -> None:
        self.get_environment(env_id)
        used = self.db.query_one("SELECT COUNT(*) AS n FROM perf_scenarios WHERE environment_id = ? AND archived = 0",
                                 (env_id,))["n"]
        if used:
            raise QAError("PERF_ENV_IN_USE", f"{used} scenario(s) use this environment. Choose another environment on "
                          "them first.", status=409)
        self.db.execute("DELETE FROM perf_environments WHERE id = ?", (env_id,))

    # ---- scenarios ---------------------------------------------------------------------------------------

    def _scenario_row(self, row: dict[str, Any]) -> dict[str, Any]:
        sc = PerfScenario.model_validate(loads(row["data"])).model_dump()
        plan_row = self.db.query_one("SELECT name, kind, plan FROM perf_plans WHERE id = ?", (sc["plan_id"],)) \
            if sc["plan_id"] else None
        plan = PerfPlan.model_validate(loads(plan_row["plan"])) if plan_row else None
        env_row = self.db.query_one("SELECT name FROM perf_environments WHERE id = ?", (sc["environment_id"],)) \
            if sc["environment_id"] else None
        last = self.db.query_one("SELECT run_id, state, verdict, partial, started_at FROM perf_runs WHERE scenario_id = ?"
                                 " ORDER BY COALESCE(queued_at, started_at) DESC LIMIT 1", (sc["id"],))
        sc.update(kind=plan.kind if plan else None, plan_name=plan.name if plan else None,
                  requests=len(plan.requests) if plan else 0, peak_users=plan.peak_users() if plan else None,
                  duration_s=(plan.total_duration_s() if plan.script else plan.profile.duration_s) if plan else None,
                  environment_name=env_row["name"] if env_row else None, last_run=last,
                  script_missing=bool(sc["plan_id"]) and plan is None)
        return sc

    def list_scenarios(self, *, q: str = "", kind: str | None = None, environment_id: str | None = None,
                       archived: bool = False) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM perf_scenarios WHERE archived = ? ORDER BY updated_at DESC",
                             (1 if archived else 0,))
        out = [self._scenario_row(r) for r in rows]
        if kind:
            out = [s for s in out if s["kind"] == kind]
        if environment_id:
            out = [s for s in out if s["environment_id"] == environment_id]
        if q.strip():
            words = q.lower().split()
            out = [s for s in out if all(w in " ".join(str(s.get(k) or "") for k in (
                "id", "name", "description", "project", "plan_name", "environment_name", "user_story_id",
                "test_suite_id", "acceptance_criteria")).lower() + " " + " ".join(s["tags"]).lower() for w in words)]
        return out

    def get_scenario(self, scenario_id: str) -> PerfScenario:
        row = self.db.query_one("SELECT data FROM perf_scenarios WHERE id = ?", (scenario_id,))
        if not row:
            raise QAError("PERF_SCENARIO_NOT_FOUND", f"Scenario {scenario_id} does not exist.", status=404)
        return PerfScenario.model_validate(loads(row["data"]))

    def scenario_detail(self, scenario_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM perf_scenarios WHERE id = ?", (scenario_id,))
        if not row:
            raise QAError("PERF_SCENARIO_NOT_FOUND", f"Scenario {scenario_id} does not exist.", status=404)
        return self._scenario_row(row)

    def save_scenario(self, sc: PerfScenario) -> PerfScenario:
        if sc.plan_id:
            self.get_plan(sc.plan_id)
        if sc.environment_id:
            self.get_environment(sc.environment_id)
        ts = now_iso()
        if not sc.id:
            sc.id, sc.created_at = new_scenario_id(), ts
        else:
            current = self.get_scenario(sc.id)
            sc.created_at = current.created_at
        sc.updated_at = ts
        self.db.execute(
            "INSERT INTO perf_scenarios (id, name, plan_id, environment_id, archived, data, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name = excluded.name, plan_id = excluded.plan_id,"
            " environment_id = excluded.environment_id, archived = excluded.archived, data = excluded.data,"
            " updated_at = excluded.updated_at",
            (sc.id, sc.name, sc.plan_id, sc.environment_id, 1 if sc.archived else 0, sc.model_dump_json(),
             sc.created_at, sc.updated_at))
        return sc

    def archive_scenario(self, scenario_id: str, archived: bool = True) -> PerfScenario:
        sc = self.get_scenario(scenario_id)
        sc.archived = archived
        return self.save_scenario(sc)

    def clone_scenario(self, scenario_id: str, name: str | None = None) -> PerfScenario:
        """A copy of the scenario with its own copy of the script, so editing one never changes the other."""
        src = self.get_scenario(scenario_id)
        copy = src.model_copy(deep=True)
        copy.id, copy.archived = "", False
        copy.name = (name or f"Copy of {src.name}")[:200]
        if src.plan_id and self.db.query_one("SELECT 1 FROM perf_plans WHERE id = ?", (src.plan_id,)):
            plan = self.get_plan(src.plan_id).model_copy(deep=True)
            plan.id, plan.name, plan.source = "", copy.name, f"clone of {src.plan_id}"
            copy.plan_id = self.save_plan(plan, link_scenario=False).id
        return self.save_scenario(copy)

    def _ensure_scenarios(self) -> int:
        """Every plan belongs to a scenario. Plans saved before 23.3.22 get one with the plan's name (nothing is lost)."""
        rows = self.db.query("SELECT p.id, p.name, p.created_at FROM perf_plans p WHERE NOT EXISTS"
                             " (SELECT 1 FROM perf_scenarios s WHERE s.plan_id = p.id)")
        for r in rows:
            sc = self.save_scenario(PerfScenario(name=r["name"], plan_id=r["id"],
                                                 description="Created from an existing performance plan."))
            self.db.execute("UPDATE perf_runs SET scenario_id = ? WHERE plan_id = ? AND scenario_id IS NULL",
                            (sc.id, r["id"]))
        return len(rows)

    # ---- pre-run checks ---------------------------------------------------------------------------------

    def _min_free_mb(self) -> int:
        return int(self.settings.section("performance").get("min_free_disk_mb") or preflight.DEFAULT_MIN_FREE_MB)

    def check_plan(self, plan: PerfPlan, env: PerfEnvironment | None) -> dict[str, Any]:
        checks, info = preflight.tool_checks(self.settings)
        if plan.script:
            try:
                analysis = jscript.analyze_cached(plan.script)
                problems, summary = analysis["problems"], analysis["summary"]
            except QAError as exc:
                problems, summary = [exc.message], None
            detail = " ".join(problems) or (
                f"Valid JMX: {len(summary['thread_groups'])} thread group(s), {len(summary['requests'])} HTTP "
                f"request(s), version {plan.script_version}." if summary else "")
            checks.append({"id": "jmx", "name": "JMeter script", "status": "FAIL" if problems else "PASS",
                           "detail": detail, "fix": "Fix it in the script editor." if problems else ""})
            if summary:
                checks += self._script_file_checks(summary)
        else:
            problems = preflight.validate_jmx(build_jmx(plan))
            checks.append({"id": "jmx", "name": "JMeter plan", "status": "FAIL" if problems else "PASS",
                           "detail": " ".join(problems) or f"Valid JMX: {len(plan.thread_groups())} thread group(s), "
                                                           f"{len(plan.requests)} request(s).", "fix": ""})
        if problems and plan.script:  # the load of a broken script cannot be sized: skip the target checks
            checks.append(preflight.disk_check(self.paths.performance, self._min_free_mb()))
            return {"status": preflight.summarize(checks), "checks": checks, "tools": info, "checked_at": now_iso()}
        checks.append(preflight.disk_check(self.paths.performance, self._min_free_mb()))
        checks += preflight.target_checks(plan, env)
        if not generator.available():
            checks.append({"id": "generator", "name": "Load generator monitoring", "status": "WARN",
                           "detail": "psutil is not installed, so CPU and memory of this PC will not be recorded.",
                           "fix": "Apply the 23.3.22 patch again; it installs psutil."})
        return {"status": preflight.summarize(checks), "checks": checks, "tools": info, "checked_at": now_iso()}

    @staticmethod
    def _script_file_checks(summary: dict[str, Any]) -> list[dict[str, Any]]:
        checks = []
        for f in summary["csv_files"]:
            path = Path(f)
            if "${" in f or not path.is_absolute() or not path.exists():
                checks.append({"id": "csv", "name": "Data file", "status": "FAIL",
                               "detail": f"The CSV data file {f} was not found"
                                         + (" (relative paths are read from the run folder)." if not path.is_absolute()
                                            else "."),
                               "fix": "Set the full path of the file in the CSV Data Set element."})
        if summary["listeners"]:
            checks.append({"id": "listeners", "name": "Listeners", "status": "PASS",
                           "detail": f"{len(summary['listeners'])} listener(s) in the script are switched off for the "
                                     "run; results are recorded by the run itself. The saved script is unchanged.",
                           "fix": ""})
        return checks

    def check_scenario(self, scenario_id: str) -> dict[str, Any]:
        sc = self.get_scenario(scenario_id)
        if not sc.plan_id:
            raise QAError("PERF_SCENARIO_NO_SCRIPT", "This scenario has no script yet. Create or choose one first.",
                          status=409)
        env = self.get_environment(sc.environment_id) if sc.environment_id else None
        return self.check_plan(self.get_plan(sc.plan_id), env)

    # ---- queue and execution ----------------------------------------------------------------------------

    def start(self) -> None:
        """Called once by the runtime: recover from an earlier shutdown, then start the queue worker."""
        self.recover()
        if self._worker and self._worker.is_alive():
            return
        self._closing.clear()
        self._worker = threading.Thread(target=self._work_queue, name="perf-queue", daemon=True)
        self._worker.start()

    def close(self, timeout: float = 20.0) -> None:
        """Application shutdown: queued runs stay queued, a running test is stopped and recorded as INTERRUPTED."""
        self._closing.set()
        self._wake.set()
        for run_id in list(self._procs):
            self.stop(run_id, mode="graceful")
        deadline = time.monotonic() + timeout
        while self._procs and time.monotonic() < deadline:
            time.sleep(0.2)

    def running(self) -> list[str]:
        return list(self._procs)

    def _work_queue(self) -> None:
        while not self._closing.is_set():
            self._wake.wait(2.0)
            self._wake.clear()
            while not self._closing.is_set():
                row = self.db.query_one("SELECT run_id FROM perf_runs WHERE state = 'QUEUED'"
                                        " ORDER BY queued_at, rowid LIMIT 1")
                if not row or not self._lock.acquire(blocking=False):
                    break
                try:
                    self._execute_run(row["run_id"])
                except Exception as exc:  # the worker must keep going
                    log.warning(f"Performance run {row['run_id']} ended with an error: {exc}")
                finally:
                    self._lock.release()

    def _resolve(self, plan_id: str, scenario_id: str | None, overrides: dict[str, Any] | None
                 ) -> tuple[PerfPlan, PerfScenario | None, PerfEnvironment | None]:
        plan = self.with_overrides(self.get_plan(plan_id), overrides)
        sc = self.get_scenario(scenario_id) if scenario_id else None
        if sc is None:
            row = self.db.query_one("SELECT id FROM perf_scenarios WHERE plan_id = ? AND archived = 0"
                                    " ORDER BY created_at LIMIT 1", (plan_id,))
            sc = self.get_scenario(row["id"]) if row else None
        env = self.get_environment(sc.environment_id) if sc and sc.environment_id else None
        return plan, sc, env

    def _create_run(self, run_id: str, plan: PerfPlan, sc: PerfScenario | None, env: PerfEnvironment | None,
                    trigger: str, overrides: dict[str, Any] | None) -> None:
        """The run's record and its snapshot of exactly what will run, taken when it is created."""
        folder = self.paths.performance / run_id
        ts = now_iso()
        config = {"plan": plan.model_dump(), "scenario": sc.model_dump() if sc else None,
                  "environment": env.model_dump() if env else None, "overrides": overrides or {},
                  "created_at": ts, "trigger": trigger}
        self.db.execute(
            "INSERT INTO perf_runs (run_id, plan_id, kind, name, status, state, started_at, queued_at, folder,"
            " scenario_id, environment_id, trigger, overrides, config) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, plan.id, plan.kind, plan.name, "QUEUED", "QUEUED", ts, ts, str(folder), sc.id if sc else None,
             env.id if env else None, trigger, dumps(overrides or {}), dumps(config)))

    def enqueue(self, plan_id: str, *, scenario_id: str | None = None, trigger: str = "manual",
                overrides: dict[str, Any] | None = None, run_id: str | None = None) -> str:
        """Queue a run. Runs start one at a time on this PC (the load generator), oldest first."""
        plan, sc, env = self._resolve(plan_id, scenario_id, overrides)
        run_id = run_id or new_run_id("PERF")
        self._create_run(run_id, plan, sc, env, trigger, overrides)
        self.jmeter.emit(names.PERFORMANCE_GENERATION_STARTED, f"Queued {KIND_TITLES[plan.kind]}: {plan.name}",
                         run_id=run_id, details={"state": "QUEUED", "link": f"#/perf-results?run={run_id}"})
        self._wake.set()
        return run_id

    def run_scenario(self, scenario_id: str, *, trigger: str = "manual") -> str:
        sc = self.get_scenario(scenario_id)
        if sc.archived:
            raise QAError("PERF_SCENARIO_ARCHIVED", "This scenario is archived. Restore it before running it.", status=409)
        if not sc.plan_id:
            raise QAError("PERF_SCENARIO_NO_SCRIPT", "This scenario has no script yet. Create or choose one first.",
                          status=409)
        return self.enqueue(sc.plan_id, scenario_id=sc.id, trigger=trigger)

    def run(self, plan_id: str, run_id: str, overrides: dict[str, Any] | None = None, *,
            scenario_id: str | None = None, trigger: str = "manual") -> dict[str, Any]:
        """Run a saved plan now and wait for it. `overrides` (kind, users, duration_s) apply to this run only; the
        saved plan is unchanged and the plan actually used is written to the run folder."""
        plan, sc, env = self._resolve(plan_id, scenario_id, overrides)
        if not self._lock.acquire(blocking=False):
            raise QAError("PERF_ALREADY_RUNNING", "A performance test is already running.", status=409)
        try:
            self._create_run(run_id, plan, sc, env, trigger, overrides)
            self._execute_run(run_id)
            return self.get_run(run_id)
        finally:
            self._lock.release()

    def cancel(self, run_id: str) -> bool:
        """Cancel a queued run (it never starts). A running test is stopped with stop()."""
        cur = self.db.execute("UPDATE perf_runs SET state = 'CANCELLED', status = 'FAILED', finished_at = ?,"
                              " state_reason = ? WHERE run_id = ? AND state = 'QUEUED'",
                              (now_iso(), "Cancelled before it started.", run_id))
        return cur.rowcount > 0

    def _set(self, run_id: str, **fields: Any) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE perf_runs SET {cols} WHERE run_id = ?", [*fields.values(), run_id])

    def _execute_run(self, run_id: str) -> None:
        row = self.db.query_one("SELECT * FROM perf_runs WHERE run_id = ?", (run_id,))
        if not row or row["state"] != "QUEUED":
            return
        config = loads(row["config"])
        plan = PerfPlan.model_validate(config["plan"])
        env = PerfEnvironment.model_validate(config["environment"]) if config.get("environment") else None
        folder = Path(row["folder"])
        folder.mkdir(parents=True, exist_ok=True)
        title = f"{KIND_TITLES[plan.kind]}: {plan.name}"
        self._set(run_id, state="VALIDATING", status="RUNNING", started_at=now_iso())
        self.runs.start(run_id, "performance", title)
        record: dict[str, Any] = {"state": "FAILED", "verdict": None, "partial": 0, "metrics": None, "sla": None,
                                  "error": None, "reason": None, "generator": None}
        try:
            check = self.check_plan(plan, env)
            self._set(run_id, checks=dumps(check))
            if check["status"] == "FAIL":
                failed = [c for c in check["checks"] if c["status"] == "FAIL"]
                record["error"] = "Pre-run checks failed: " + " ".join(c["detail"] for c in failed)
                record["reason"] = "Blocked by pre-run checks; no load was sent."
                self.jmeter.emit(names.PERFORMANCE_FAILED, f"{title} did not start: {record['error']}", run_id=run_id,
                                 details={"link": f"#/perf-results?run={run_id}", "state": "FAILED"})
                return
            self._write_snapshot(folder, plan, config, check)
            self._set(run_id, state="RUNNING")
            outcome = self._execute(plan, run_id, folder)
            record["metrics"], record["generator"] = outcome["metrics"], outcome["generator"]
            has_samples = bool(record["metrics"] and record["metrics"]["overall"]["samples"])
            if self._closing.is_set() and outcome["stopped"]:
                record["state"], record["reason"] = "INTERRUPTED", "The application was closed during the run."
            elif outcome["stopped"]:
                record["state"] = "CANCELLED"
                record["reason"] = ("Emergency stop: JMeter was ended at once." if outcome["stopped"] == "emergency"
                                    else "Stopped by the user; results up to the stop are kept.")
            elif outcome["error"]:
                record["state"], record["error"] = "FAILED", outcome["error"]
            else:
                record["state"] = "COMPLETED"
            if record["state"] != "COMPLETED" and has_samples:
                record["partial"] = 1
            if has_samples and record["state"] == "COMPLETED":
                record["sla"] = self._evaluate(plan, run_id, record["metrics"])
                record["verdict"] = {"PASS": "PASS", "FAIL": "FAIL"}.get(record["sla"]["status"], "NOT_SPECIFIED")
            elif has_samples:
                record["sla"] = sla.evaluate(plan.sla, record["metrics"])  # shown for reference, not a verdict
                record["verdict"] = "INCONCLUSIVE"
            if record["state"] == "FAILED":
                self.jmeter.emit(names.PERFORMANCE_FAILED, f"{title} could not complete: {record['error']}",
                                 run_id=run_id, details={"link": f"#/perf-results?run={run_id}", "state": "FAILED"})
            elif record["state"] != "COMPLETED":
                self.jmeter.emit(names.PERFORMANCE_COMPLETED, f"{title}: {record['state'].lower()}"
                                 + (" with partial results" if record["partial"] else " with no samples"),
                                 run_id=run_id, details={"link": f"#/perf-results?run={run_id}",
                                                         "state": record["state"]})
        except QAError as exc:
            record["error"] = exc.message
            self.jmeter.emit(names.PERFORMANCE_FAILED, f"{title} could not complete: {exc.message}", run_id=run_id,
                             details={"link": f"#/perf-results?run={run_id}", "state": "FAILED"})
        finally:
            ok = record["state"] == "COMPLETED" and record["verdict"] in ("PASS", "NOT_SPECIFIED")
            legacy = "PASSED" if ok else "FAILED"
            self._set(run_id, state=record["state"], verdict=record["verdict"], partial=record["partial"],
                      status=legacy, finished_at=now_iso(), metrics=_dump(record["metrics"]),
                      sla=_dump(record["sla"]), error=record["error"], state_reason=record["reason"],
                      generator=_dump(record["generator"]), pid=None)
            overall = (record["metrics"] or {}).get("overall", {})
            self.runs.finish(run_id, legacy, {
                "plan_id": plan.id, "error": record["error"], "sla": (record["sla"] or {}).get("status"),
                "state": record["state"], "verdict": record["verdict"], "partial": bool(record["partial"]),
                "samples": overall.get("samples"), "error_pct": overall.get("error_pct"),
                "p95_ms": overall.get("p95_ms"), "throughput": overall.get("throughput"),
            })

    def _write_snapshot(self, folder: Path, plan: PerfPlan, config: dict[str, Any], check: dict[str, Any]) -> None:
        """What exactly ran: the plan, the JMX, the scenario, environment, tools and versions."""
        listeners_off = 0
        if plan.script:
            jmx, listeners_off = jscript.for_run(plan.script)
            (folder / "script.jmx").write_text(plan.script, encoding="utf-8")  # as saved, listeners included
        else:
            jmx = build_jmx(plan)
        (folder / "plan.jmx").write_text(jmx, encoding="utf-8")
        (folder / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        groups = plan.thread_groups()
        effective = {
            **config,
            "application_version": __version__,
            "tools": check["tools"],
            "workload": {"test_type": plan.kind, "peak_concurrent_users": plan.peak_users(),
                         "thread_groups": [g.model_dump() for g in groups],
                         "total_duration_s": plan.total_duration_s(),
                         "think_time_ms": None if plan.script else plan.profile.think_time_ms,
                         "load_model": "closed (concurrent virtual users; not a request-rate target)"},
            "targets": plan.sla.model_dump(),
            "jmx_sha256": hashlib.sha256(jmx.encode("utf-8")).hexdigest(),
            "script": ({"version": plan.script_version, "sha256": jscript.sha256(plan.script),
                        "listeners_disabled_for_run": listeners_off} if plan.script else None),
            "pre_run_checks": check["status"],
        }
        (folder / "config.json").write_text(json.dumps(effective, indent=2, ensure_ascii=False, default=str),
                                            encoding="utf-8")

    def _execute(self, plan: PerfPlan, run_id: str, folder: Path) -> dict[str, Any]:
        """Run JMeter non-GUI. Returns metrics (also partial ones), how it ended and the generator summary."""
        title = f"{KIND_TITLES[plan.kind]}: {plan.name}"
        out: dict[str, Any] = {"metrics": None, "stopped": None, "error": None, "generator": None}
        java, jmeter = tools.find_java(self.settings), tools.find_jmeter(self.settings)
        with self.jmeter.activity(f"Running {title}", run_id=run_id) as act:
            act.event(names.PERFORMANCE_EXECUTION_STARTED,
                      f"JMeter non-GUI started: {plan.peak_users()} users, "
                      f"{plan.total_duration_s() or 'loop-based'}{'s' if plan.total_duration_s() else ''}, "
                      f"{len(plan.requests)} requests")
            cmd = [jmeter, "-n", "-t", str(folder / "plan.jmx"), "-l", "results.jtl", "-j", "jmeter.log",
                   "-Jjmeter.save.saveservice.output_format=csv", "-Jjmeter.save.saveservice.print_field_names=true",
                   "-e", "-o", "report"]
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if tools.WINDOWS else 0  # type: ignore[attr-defined]
            try:
                proc = subprocess.Popen(cmd, cwd=folder, env=tools.jmeter_env(java), stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, creationflags=flags)
            except OSError as exc:
                raise QAError("JMETER_START_FAILED", f"JMeter could not start: {exc}", status=500) from exc
            self._procs[run_id] = proc
            self._set(run_id, pid=proc.pid)
            monitor = generator.GeneratorMonitor(folder)
            monitor.start(proc.pid)
            self._monitors[run_id] = monitor
            output: list[str] = []
            reader = threading.Thread(target=lambda: output.extend(
                ln.decode("utf-8", "replace").rstrip() for ln in iter(proc.stdout.readline, b"")), daemon=True)  # type: ignore[union-attr]
            reader.start()
            total = plan.total_duration_s()
            total_s = total if total is not None else preflight.UNKNOWN_DURATION_TIMEOUT_S
            deadline = time.monotonic() + total_s + (RUN_GRACE_S if total is not None else 0)
            tail = jtl.LiveTail(folder / "results.jtl")
            started, last_emit = time.monotonic(), 0.0
            try:
                while proc.poll() is None:
                    if time.monotonic() > deadline:
                        self._kill(proc)
                        out["error"] = (f"JMeter did not finish within the test duration plus {RUN_GRACE_S // 60} "
                                        "minutes and was stopped (execution timeout)." if total is not None else
                                        f"The script's loops did not finish within "
                                        f"{preflight.UNKNOWN_DURATION_TIMEOUT_S // 3600} hours and JMeter was stopped "
                                        "(execution timeout).")
                        break
                    time.sleep(1.0)
                    now = time.monotonic()
                    if now - last_emit >= 5:
                        last_emit = now
                        live = tail.poll()
                        gen = monitor.latest()
                        live["elapsed_s"] = round(now - started)
                        live["generator"] = gen
                        self._live[run_id] = live
                        act.event(names.PERFORMANCE_SAMPLE_SUMMARY,
                                  f"{live['samples']} samples · avg {live['avg_ms']} ms · errors {live['error_pct']}%"
                                  f" · {live['threads']} active users"
                                  + (f" · generator CPU {gen['cpu_pct']}%" if gen else ""),
                                  progress=min(99, int((now - started) * 100 / max(total_s, 1))), **live)
            finally:
                self._procs.pop(run_id, None)
                self._live.pop(run_id, None)
                self._monitors.pop(run_id, None)
                reader.join(timeout=5)
                out["generator"] = monitor.stop()
            (folder / "console.log").write_text("\n".join(output), encoding="utf-8")
            (folder / "generator.json").write_text(json.dumps(out["generator"], indent=1), encoding="utf-8")
            out["stopped"] = self._stopping.pop(run_id, None)
            results = folder / "results.jtl"
            if results.exists() and results.stat().st_size > 0:
                act.progress(95, "Calculating metrics from the JTL results")
                metrics = jtl.parse_file(results)
                if metrics["overall"]["samples"]:
                    metrics["stopped_by_user"] = bool(out["stopped"])
                    metrics["exit_code"] = proc.returncode
                    metrics["report"] = (folder / "report" / "index.html").exists()
                    (folder / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
                    out["metrics"] = metrics
            if out["metrics"] is None and not out["error"] and not out["stopped"]:
                hint = next((ln for ln in reversed(output) if "error" in ln.lower()), output[-1] if output else "")
                out["error"] = ("JMeter ran but recorded no samples. Check the request URLs." if results.exists()
                                else "JMeter produced no results.") + (f" {hint[:300]}" if hint else "")
            if out["generator"].get("saturated"):
                act.event(names.PERFORMANCE_SAMPLE_SUMMARY, "Load generator saturated during the run: " + "; ".join(
                    f["message"] for f in out["generator"]["flags"]) + ". Response times may be distorted.")
            o = (out["metrics"] or {}).get("overall")
            if out["error"] and not o:
                raise QAError("JMETER_NO_RESULTS" if "result" in out["error"] or "sample" in out["error"]
                              else "JMETER_TIMEOUT", out["error"], status=500)
            act.complete(f"JMeter finished{' (stopped)' if out['stopped'] else ''}"
                         + (f": {o['samples']} samples, avg {o['avg_ms']} ms, p95 {o['p95_ms']} ms, errors "
                            f"{o['error_pct']}%" if o else ": no samples"))
        return out

    def _evaluate(self, plan: PerfPlan, run_id: str, metrics: dict[str, Any]) -> dict[str, Any]:
        with self.engineer.activity("Evaluating results against SLAs", run_id=run_id) as act:
            result = sla.evaluate(plan.sla, metrics)
            o = metrics["overall"]
            text = {"PASS": "all SLAs met", "FAIL": f"{len(result['failed'])} SLA checks failed",
                    "NOT_DEFINED": "target not specified"}[result["status"]]
            name = names.PERFORMANCE_FAILED if result["status"] == "FAIL" else names.PERFORMANCE_COMPLETED
            act.event(name, f"{KIND_TITLES[plan.kind]} \"{plan.name}\": {text} (p95 {o['p95_ms']} ms, "
                            f"{o['throughput']} req/s, errors {o['error_pct']}%)",
                      link=f"#/perf-results?run={run_id}", sla=result["status"])
            act.complete(f"SLA evaluation: {text}")
        return result

    @staticmethod
    def with_overrides(plan: PerfPlan, overrides: dict[str, Any] | None) -> PerfPlan:
        if not overrides:
            return plan
        if plan.script and any(overrides.get(k) for k in ("users", "duration_s", "kind")):
            raise QAError("PERF_SCRIPT_OVERRIDES", "Users and duration of a script are set on its thread groups. "
                          "Change them in the script editor.", status=409)
        profile = {k: overrides[k] for k in ("users", "duration_s") if overrides.get(k)}
        data = plan.model_dump()
        data["profile"].update(profile)
        if overrides.get("kind"):
            data["kind"] = overrides["kind"]
        try:
            return PerfPlan.model_validate(data)
        except ValidationError as exc:
            raise QAError("VALIDATION_FAILED", "Those load settings are outside the allowed range.", status=422,
                          details=str(exc)) from exc

    def stop(self, run_id: str, mode: str = "graceful") -> bool:
        """graceful: JMeter's StopTestNow, so results and the HTML report are written; ended after 30 s if it hangs.
        emergency: end the JMeter process tree at once (only the process this application started)."""
        proc = self._procs.get(run_id)
        if proc is None:
            return False
        self._stopping[run_id] = "emergency" if mode == "emergency" else self._stopping.get(run_id, "graceful")
        self._set(run_id, state="STOPPING")
        if mode == "emergency":
            self._kill(proc)
            return True
        for port in STOP_PORTS:  # ask JMeter to stop cleanly so results and the report are written
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.sendto(b"StopTestNow", ("127.0.0.1", port))
            except OSError:
                continue

        def force() -> None:
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self._kill(proc)

        threading.Thread(target=force, daemon=True).start()
        return True

    @staticmethod
    def _kill(proc: subprocess.Popen[bytes]) -> None:
        if tools.WINDOWS:  # jmeter.bat starts java as a child; end the whole tree
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            found = generator.find_run_process(proc.pid, Path(proc.args[3]).parent) if generator.available() else None
            if found is not None:
                generator.kill_tree(found)
            else:
                proc.kill()

    def live(self, run_id: str) -> dict[str, Any] | None:
        return self._live.get(run_id)

    # ---- recovery -----------------------------------------------------------------------------------

    def _backfill_states(self) -> None:
        """Runs recorded before 23.3.22 had one status for both the process and the SLA; split it."""
        for r in self.db.query("SELECT run_id, status, sla, error, metrics FROM perf_runs WHERE state IS NULL"):
            sla_status = (loads(r["sla"]) or {}).get("status") if r["sla"] else None
            verdict = {"PASS": "PASS", "FAIL": "FAIL", "NOT_DEFINED": "NOT_SPECIFIED"}.get(sla_status or "")
            if r["status"] == "RUNNING":
                state = "RUNNING"  # recover() turns it into INTERRUPTED, keeping any partial results
            elif r["status"] == "FAILED" and r["error"] and not verdict:
                state = "FAILED"
            else:
                state = "COMPLETED"
            self._set(r["run_id"], state=state, verdict=verdict if state == "COMPLETED" else None)

    def recover(self) -> dict[str, int]:
        """After a restart or crash: a run that was validating, running or stopping is INTERRUPTED, keeping any partial
        results; its JMeter is ended if it is still running (found by its run folder, never another Java process).
        Queued runs are SKIPPED: a load test never starts by itself after a restart."""
        self._backfill_states()
        created = self._ensure_scenarios()
        interrupted = skipped = killed = 0
        for r in self.db.query("SELECT run_id, folder, pid, plan_id FROM perf_runs WHERE state IN"
                               " ('VALIDATING','RUNNING','STOPPING')"):
            if r["run_id"] in self._procs:
                continue
            folder = Path(r["folder"])
            proc = generator.find_run_process(r["pid"], folder)
            if proc is not None:
                killed += generator.kill_tree(proc)
            metrics = None
            results = folder / "results.jtl"
            if results.exists() and results.stat().st_size > 0:
                try:
                    metrics = jtl.parse_file(results)
                    if not metrics["overall"]["samples"]:
                        metrics = None
                except (OSError, ValueError):
                    metrics = None
            if metrics:
                metrics["stopped_by_user"] = False
                (folder / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
            reason = "The application stopped while this run was in progress" + (
                "; its JMeter process was ended." if proc is not None else ".")
            self._set(r["run_id"], state="INTERRUPTED", status="FAILED", finished_at=now_iso(),
                      verdict="INCONCLUSIVE" if metrics else None, partial=1 if metrics else 0,
                      metrics=_dump(metrics), state_reason=reason, pid=None)
            self.runs.finish(r["run_id"], "FAILED", {"plan_id": r["plan_id"], "state": "INTERRUPTED",
                                                    "error": reason, "partial": bool(metrics)})
            interrupted += 1
        cur = self.db.execute("UPDATE perf_runs SET state = 'SKIPPED', status = 'FAILED', finished_at = ?,"
                              " state_reason = ? WHERE state = 'QUEUED'",
                              (now_iso(), "The application restarted before this run started. Start it again "
                                          "with Run now."))
        skipped = cur.rowcount
        if interrupted or skipped or killed or created:
            log.info(f"Performance recovery: {interrupted} interrupted, {skipped} skipped, {killed} JMeter processes "
                     f"ended, {created} scenarios created for existing plans")
        return {"interrupted": interrupted, "skipped": skipped, "killed": killed, "scenarios_created": created}

    # ---- results ------------------------------------------------------------------------------

    def list_runs(self, plan_id: str | None = None, kind: str | None = None, limit: int = 100, *,
                  scenario_id: str | None = None, environment_id: str | None = None, state: str | None = None,
                  verdict: str | None = None, trigger: str | None = None, q: str = "") -> list[dict[str, Any]]:
        clauses, params = [], []
        for col, val in (("plan_id", plan_id), ("kind", kind), ("scenario_id", scenario_id),
                         ("environment_id", environment_id), ("state", state), ("verdict", verdict)):
            if val:
                clauses.append(f"{col} = ?")
                params.append(val)
        if trigger == "manual":
            clauses.append("trigger IN ('manual', 'voice')")
        elif trigger:
            clauses.append("trigger = ?")
            params.append(trigger)
        if q.strip():
            clauses.append("(run_id LIKE ? OR name LIKE ?)")
            params += [f"%{q.strip()}%"] * 2
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(f"SELECT run_id, plan_id, scenario_id, environment_id, kind, name, status, state, verdict,"
                             f" partial, trigger, queued_at, started_at, finished_at, metrics, sla, error, state_reason"
                             f" FROM perf_runs {where} ORDER BY COALESCE(queued_at, started_at) DESC LIMIT ?",
                             [*params, limit])
        for r in rows:
            m = loads(r.pop("metrics")) if r.get("metrics") else None
            s = loads(r.pop("sla")) if r.get("sla") else None
            r["overall"] = m["overall"] if m else None
            r["sla_status"] = s["status"] if s else None
            r["partial"] = bool(r["partial"])
        return rows

    def queue(self) -> list[dict[str, Any]]:
        """Active and queued runs, the running one first."""
        rows = self.db.query("SELECT run_id, plan_id, scenario_id, kind, name, state, trigger, queued_at, started_at"
                             " FROM perf_runs WHERE state IN ('QUEUED','VALIDATING','RUNNING','STOPPING')"
                             " ORDER BY CASE state WHEN 'QUEUED' THEN 1 ELSE 0 END, queued_at, rowid")
        for i, r in enumerate(x for x in rows if x["state"] == "QUEUED"):
            r["position"] = i + 1
        for r in rows:
            r["live"] = self._live.get(r["run_id"])
        return rows

    def get_run(self, run_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM perf_runs WHERE run_id = ?", (run_id,))
        if not row:
            raise QAError("PERF_RUN_NOT_FOUND", f"Performance run {run_id} does not exist.", status=404)
        for key in ("metrics", "sla", "analysis", "config", "checks", "generator", "overrides"):
            row[key] = loads(row[key]) if row.get(key) else None
        row["partial"] = bool(row.get("partial"))
        row["state"] = row.get("state") or row["status"]
        folder = Path(row["folder"])
        row["files"] = sorted(p.name for p in folder.iterdir() if p.is_file()) if folder.exists() else []
        row["has_report"] = (folder / "report" / "index.html").exists()
        row["live"] = self._live.get(run_id)
        return row

    def run_file(self, run_id: str, relative: str) -> Path:
        row = self.get_run(run_id)
        root = Path(row["folder"]).resolve()
        target = (root / relative).resolve()
        if root not in target.parents or not target.is_file():
            raise QAError("PERF_FILE_NOT_FOUND", "That result file is not available.", status=404)
        return target

    # ---- AI analysis ---------------------------------------------------------------------------

    def analyze(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if not run["metrics"]:
            raise QAError("PERF_NO_METRICS", "This run has no metrics to analyze.", status=409)
        plan = self.get_plan(run["plan_id"]) if self.db.query_one(
            "SELECT 1 FROM perf_plans WHERE id = ?", (run["plan_id"],)) else None
        m = run["metrics"]
        facts = {
            "test_type": run["kind"], "duration_s": m["duration_s"], "peak_threads": m["peak_threads"],
            "load_profile": plan.profile.model_dump() if plan else None,
            "overall": {k: v for k, v in m["overall"].items() if k != "label"},
            "requests": [{k: r[k] for k in ("label", "samples", "error_pct", "avg_ms", "p90_ms", "p95_ms", "p99_ms",
                                              "max_ms", "throughput", "avg_latency_ms", "avg_connect_ms",
                                              "response_codes", "top_errors")} for r in m["requests"]],
            "timeline": m["timeline"][:: max(1, len(m["timeline"]) // 60)],
            "sla": run["sla"],
        }
        with self.engineer.activity("Analyzing performance results with the On-Premise AI", run_id=run_id) as act:
            analysis = self.ai.complete_json(
                "You are a performance engineer reviewing JMeter results. Use only the metrics provided. Every observation "
                "and suspected bottleneck must cite the metric values that support it. Do not invent causes such as "
                "database, CPU or memory problems unless the metrics directly show them; when the cause cannot be "
                "determined from these results, say so under 'undetermined' and suggest what to measure next.",
                json.dumps(facts, ensure_ascii=False), PerfAnalysis, max_tokens=3000)
            result = {**analysis.model_dump(), "created_at": now_iso(), "model": self.ai.config().get("model")}
            self.db.execute("UPDATE perf_runs SET analysis = ? WHERE run_id = ?", (dumps(result), run_id))
            act.complete("Performance analysis ready")
        return result

