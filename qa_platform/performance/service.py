"""Performance Engineering and JMeter Load Testing agents.

Plans are stored in SQLite. Each run writes ``data/performance/{run_id}/`` with
the generated ``plan.jmx``, ``results.jtl``, ``jmeter.log`` and JMeter's own
HTML dashboard under ``report/``. Metrics and SLA checks are computed from the
JTL only; the optional AI analysis is given those numbers and nothing else.
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..agents.base import BaseAgent
from ..ai.gateway import AIGateway
from ..clock import now_iso
from ..config import Paths, Settings
from ..errors import QAError
from ..events import names
from ..events.bus import EventBus
from ..logging_setup import get_logger
from ..runs import RunStore
from ..storage.db import Database, dumps, loads
from . import jtl, sla, tools
from .jmx import build_jmx
from .models import HttpRequest, PerfPlan, new_plan_id

log = get_logger("performance")
STOP_PORTS = range(4445, 4456)  # JMeter's non-GUI shutdown listener (jmeterengine.nongui.port, maxport)


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
        self._lock = threading.Lock()
        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        self._stopping: set[str] = set()

    # ---- plans -----------------------------------------------------------------------------

    def list_plans(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM perf_plans ORDER BY updated_at DESC")
        out = []
        for r in rows:
            plan = PerfPlan.model_validate(loads(r["plan"]))
            last = self.db.query_one("SELECT run_id, status, started_at FROM perf_runs WHERE plan_id = ?"
                                     " ORDER BY started_at DESC LIMIT 1", (r["id"],))
            out.append({"id": r["id"], "name": r["name"], "kind": r["kind"], "requests": len(plan.requests),
                        "peak_users": plan.peak_users(), "duration_s": plan.profile.duration_s,
                        "updated_at": r["updated_at"], "last_run": last})
        return out

    def get_plan(self, plan_id: str) -> PerfPlan:
        row = self.db.query_one("SELECT * FROM perf_plans WHERE id = ?", (plan_id,))
        if not row:
            raise QAError("PERF_PLAN_NOT_FOUND", f"Performance plan {plan_id} does not exist.", status=404)
        return PerfPlan.model_validate(loads(row["plan"]))

    def save_plan(self, plan: PerfPlan) -> PerfPlan:
        ts = now_iso()
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
        return plan

    def delete_plan(self, plan_id: str) -> None:
        self.get_plan(plan_id)
        self.db.execute("DELETE FROM perf_plans WHERE id = ?", (plan_id,))  # run results are kept

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

    # ---- environment ------------------------------------------------------------------------------

    def environment(self) -> dict[str, Any]:
        java, jmeter = tools.find_java(self.settings), tools.find_jmeter(self.settings)
        return {"java": java, "java_version": tools.java_version(java) if java else None, "jmeter": jmeter,
                "ready": bool(java and jmeter)}

    # ---- execution ------------------------------------------------------------------------------

    def running(self) -> list[str]:
        return list(self._procs)

    def run(self, plan_id: str, run_id: str) -> dict[str, Any]:
        plan = self.get_plan(plan_id)
        if not self._lock.acquire(blocking=False):
            raise QAError("PERF_ALREADY_RUNNING", "A performance test is already running.", status=409)
        folder = self.paths.performance / run_id
        try:
            folder.mkdir(parents=True, exist_ok=True)
            title = f"{KIND_TITLES[plan.kind]}: {plan.name}"
            self.runs.start(run_id, "performance", title)
            self.db.execute("INSERT INTO perf_runs (run_id, plan_id, kind, name, status, started_at, folder)"
                            " VALUES (?,?,?,?,?,?,?)", (run_id, plan.id, plan.kind, plan.name, "RUNNING", now_iso(),
                                                       str(folder)))
            record: dict[str, Any] = {"status": "FAILED", "metrics": None, "sla": None, "error": None}
            try:
                record["metrics"] = self._execute(plan, run_id, folder)
                record["sla"] = self._evaluate(plan, run_id, record["metrics"])
                record["status"] = "FAILED" if record["sla"]["status"] == "FAIL" else "PASSED"
            except QAError as exc:
                record["error"] = exc.message
                self.jmeter.emit(names.PERFORMANCE_FAILED, f"{title} could not complete: {exc.message}", run_id=run_id,
                                 details={"link": f"#/perf-results?run={run_id}"})
            finally:
                self.db.execute("UPDATE perf_runs SET status = ?, finished_at = ?, metrics = ?, sla = ?, error = ?"
                                " WHERE run_id = ?", (record["status"], now_iso(), dumps(record["metrics"]),
                                                      dumps(record["sla"]), record["error"], run_id))
                overall = (record["metrics"] or {}).get("overall", {})
                self.runs.finish(run_id, record["status"], {
                    "plan_id": plan.id, "error": record["error"], "sla": (record["sla"] or {}).get("status"),
                    "samples": overall.get("samples"), "error_pct": overall.get("error_pct"),
                    "p95_ms": overall.get("p95_ms"), "throughput": overall.get("throughput"),
                })
            return self.get_run(run_id)
        finally:
            self._lock.release()

    def _execute(self, plan: PerfPlan, run_id: str, folder: Path) -> dict[str, Any]:
        title = f"{KIND_TITLES[plan.kind]}: {plan.name}"
        with self.jmeter.activity(f"Running {title}", run_id=run_id) as act:
            java, jmeter = tools.find_java(self.settings), tools.find_jmeter(self.settings)
            if not java:
                raise QAError("JAVA_NOT_FOUND", "Java was not found. Set the Java home in Performance settings.", status=409)
            if not jmeter:
                raise QAError("JMETER_NOT_FOUND", "JMeter was not found. Set the JMeter home in Performance settings.",
                              status=409)
            (folder / "plan.jmx").write_text(build_jmx(plan), encoding="utf-8")
            (folder / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
            act.event(names.PERFORMANCE_EXECUTION_STARTED,
                      f"JMeter non-GUI started: {plan.peak_users()} users, {plan.profile.duration_s}s, "
                      f"{len(plan.requests)} requests")
            cmd = [jmeter, "-n", "-t", "plan.jmx", "-l", "results.jtl", "-j", "jmeter.log",
                   "-Jjmeter.save.saveservice.output_format=csv", "-Jjmeter.save.saveservice.print_field_names=true",
                   "-e", "-o", "report"]
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if tools.WINDOWS else 0  # type: ignore[attr-defined]
            try:
                proc = subprocess.Popen(cmd, cwd=folder, env=tools.jmeter_env(java), stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, creationflags=flags)
            except OSError as exc:
                raise QAError("JMETER_START_FAILED", f"JMeter could not start: {exc}", status=500) from exc
            self._procs[run_id] = proc
            output: list[str] = []
            reader = threading.Thread(target=lambda: output.extend(
                ln.decode("utf-8", "replace").rstrip() for ln in iter(proc.stdout.readline, b"")), daemon=True)  # type: ignore[union-attr]
            reader.start()
            total_s = max(g.delay_s + g.duration_s for g in plan.thread_groups())
            deadline = time.monotonic() + total_s + 900
            tail = jtl.LiveTail(folder / "results.jtl")
            started, last_emit = time.monotonic(), 0.0
            try:
                while proc.poll() is None:
                    if time.monotonic() > deadline:
                        self._kill(proc)
                        raise QAError("JMETER_TIMEOUT", "JMeter did not finish in the expected time and was stopped.",
                                      status=504)
                    time.sleep(1.0)
                    now = time.monotonic()
                    if now - last_emit >= 5:
                        last_emit = now
                        live = tail.poll()
                        elapsed = now - started
                        act.event(names.PERFORMANCE_SAMPLE_SUMMARY,
                                  f"{live['samples']} samples · avg {live['avg_ms']} ms · errors {live['error_pct']}%"
                                  f" · {live['threads']} active users",
                                  progress=min(99, int(elapsed * 100 / max(total_s, 1))), **live)
            finally:
                self._procs.pop(run_id, None)
                reader.join(timeout=5)
            (folder / "console.log").write_text("\n".join(output), encoding="utf-8")
            stopped = run_id in self._stopping
            self._stopping.discard(run_id)
            results = folder / "results.jtl"
            if not results.exists() or results.stat().st_size == 0:
                hint = next((ln for ln in reversed(output) if "error" in ln.lower()), output[-1] if output else "")
                raise QAError("JMETER_NO_RESULTS", "JMeter produced no results." + (f" {hint[:300]}" if hint else ""),
                              status=500)
            act.progress(95, "Calculating metrics from the JTL results")
            metrics = jtl.parse_file(results)
            if metrics["overall"]["samples"] == 0:
                raise QAError("JMETER_NO_SAMPLES", "JMeter ran but recorded no samples. Check the request URLs.",
                              status=500)
            metrics["stopped_by_user"] = stopped
            metrics["exit_code"] = proc.returncode
            metrics["report"] = (folder / "report" / "index.html").exists()
            (folder / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
            o = metrics["overall"]
            act.complete(f"JMeter finished{' (stopped)' if stopped else ''}: {o['samples']} samples, "
                         f"avg {o['avg_ms']} ms, p95 {o['p95_ms']} ms, errors {o['error_pct']}%")
        return metrics

    def _evaluate(self, plan: PerfPlan, run_id: str, metrics: dict[str, Any]) -> dict[str, Any]:
        with self.engineer.activity("Evaluating results against SLAs", run_id=run_id) as act:
            result = sla.evaluate(plan.sla, metrics)
            o = metrics["overall"]
            text = {"PASS": "all SLAs met", "FAIL": f"{len(result['failed'])} SLA checks failed",
                    "NOT_DEFINED": "no SLAs defined"}[result["status"]]
            name = names.PERFORMANCE_FAILED if result["status"] == "FAIL" else names.PERFORMANCE_COMPLETED
            act.event(name, f"{KIND_TITLES[plan.kind]} \"{plan.name}\": {text} (p95 {o['p95_ms']} ms, "
                            f"{o['throughput']} req/s, errors {o['error_pct']}%)",
                      link=f"#/perf-results?run={run_id}", sla=result["status"])
            act.complete(f"SLA evaluation: {text}")
        return result

    def stop(self, run_id: str) -> bool:
        proc = self._procs.get(run_id)
        if proc is None:
            return False
        self._stopping.add(run_id)
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
            proc.kill()

    # ---- results ------------------------------------------------------------------------------

    def list_runs(self, plan_id: str | None = None, kind: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses, params = [], []
        if plan_id:
            clauses.append("plan_id = ?")
            params.append(plan_id)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(f"SELECT run_id, plan_id, kind, name, status, started_at, finished_at, metrics, sla, error"
                             f" FROM perf_runs {where} ORDER BY started_at DESC LIMIT ?", [*params, limit])
        for r in rows:
            m = loads(r.pop("metrics")) if r.get("metrics") else None
            s = loads(r.pop("sla")) if r.get("sla") else None
            r["overall"] = m["overall"] if m else None
            r["sla_status"] = s["status"] if s else None
        return rows

    def get_run(self, run_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM perf_runs WHERE run_id = ?", (run_id,))
        if not row:
            raise QAError("PERF_RUN_NOT_FOUND", f"Performance run {run_id} does not exist.", status=404)
        for key in ("metrics", "sla", "analysis"):
            row[key] = loads(row[key]) if row.get(key) else None
        folder = Path(row["folder"])
        row["files"] = sorted(p.name for p in folder.iterdir() if p.is_file()) if folder.exists() else []
        row["has_report"] = (folder / "report" / "index.html").exists()
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

