"""Performance testing API: plans, JMeter runs, results, SLA, AI analysis, tool settings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from ..errors import QAError
from ..logging_setup import get_logger
from ..performance.jmx import build_jmx
from ..performance.models import PerfEnvironment, PerfPlan, PerfScenario
from ..runtime import Runtime
from .deps import runtime

router = APIRouter(prefix="/api/performance")
log = get_logger("api.performance")


class ToolSettingsIn(BaseModel):
    jmeter_home: str | None = None
    java_home: str | None = None


class FromDiscoveryIn(BaseModel):
    discovery_id: str
    name: str = ""
    kind: str = Field(default="load", pattern="^(load|stress|spike|soak)$")
    include_pages: bool = True


@router.get("/environment")
def environment(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    perf = rt.settings.section("performance")
    return {"ok": True, **rt.performance.environment(), "jmeter_home": perf.get("jmeter_home", ""),
            "java_home": perf.get("java_home", "")}


@router.put("/environment")
def set_environment(body: ToolSettingsIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.settings.update({"performance": {k: (v or "").strip() for k, v in body.model_dump(exclude_none=True).items()}})
    return environment(rt)


@router.get("/plans")
def plans(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.performance.list_plans()}


@router.post("/plans", status_code=201)
def create_plan(body: PerfPlan, scenario_id: str | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """A new plan joins `scenario_id` when given, otherwise it gets its own scenario."""
    body.id = ""
    body.source = body.source or "manual"
    return {"ok": True, "plan": rt.performance.save_plan(body, scenario_id=scenario_id).model_dump()}


@router.post("/plans/from-discovery", status_code=201)
def plan_from_discovery(body: FromDiscoveryIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    plan = rt.performance.plan_from_discovery(body.discovery_id, name=body.name, kind=body.kind,
                                              include_pages=body.include_pages)
    return {"ok": True, "plan": plan.model_dump()}


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    plan = rt.performance.get_plan(plan_id)
    return {"ok": True, "plan": plan.model_dump(), "thread_groups": [g.model_dump() for g in plan.thread_groups()],
            "peak_users": plan.peak_users()}


@router.put("/plans/{plan_id}")
def update_plan(plan_id: str, body: PerfPlan, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    current = rt.performance.get_plan(plan_id)
    body.id = plan_id
    body.source = current.source
    return {"ok": True, "plan": rt.performance.save_plan(body).model_dump()}


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.performance.delete_plan(plan_id)
    return {"ok": True}


@router.get("/plans/{plan_id}/jmx", response_class=PlainTextResponse)
def plan_jmx(plan_id: str, rt: Runtime = Depends(runtime)) -> PlainTextResponse:
    plan = rt.performance.get_plan(plan_id)
    return PlainTextResponse(build_jmx(plan), media_type="application/xml",
                             headers={"Content-Disposition": f'attachment; filename="{plan_id}.jmx"'})


def _require_tools(rt: Runtime) -> None:
    env = rt.performance.environment()
    if not env["ready"]:
        missing = "Java" if not env["java"] else "JMeter"
        raise QAError(f"{missing.upper()}_NOT_FOUND", f"{missing} was not found. Set it under Performance tool settings.",
                      status=409)


@router.post("/plans/{plan_id}/run", status_code=202)
def run_plan(plan_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Queue the plan. Runs start one at a time on this PC; the run page shows the queue position."""
    rt.performance.get_plan(plan_id)
    _require_tools(rt)
    run_id = rt.performance.enqueue(plan_id, trigger="manual")
    return {"ok": True, "run_id": run_id, "state": "QUEUED"}


class StopIn(BaseModel):
    mode: str = Field(default="graceful", pattern="^(graceful|emergency)$")


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: str, body: StopIn | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    mode = (body or StopIn()).mode
    if rt.performance.stop(run_id, mode=mode):
        return {"ok": True, "mode": mode}
    if rt.performance.cancel(run_id):
        return {"ok": True, "mode": "cancelled"}
    raise QAError("RUN_NOT_ACTIVE", f"Run {run_id} is not queued or executing.", status=409, run_id=run_id)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    if not rt.performance.cancel(run_id):
        raise QAError("RUN_NOT_QUEUED", f"Run {run_id} is not waiting in the queue.", status=409, run_id=run_id)
    return {"ok": True}


@router.get("/queue")
def run_queue(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.performance.queue()}


@router.get("/runs")
def runs(plan_id: str | None = None, kind: str | None = None, scenario_id: str | None = None,
         environment_id: str | None = None, state: str | None = None, verdict: str | None = None,
         trigger: str | None = None, q: str = "", limit: int = Query(100, ge=1, le=1000),
         rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    items = rt.performance.list_runs(plan_id, kind, limit, scenario_id=scenario_id, environment_id=environment_id,
                                     state=state, verdict=verdict, trigger=trigger, q=q)
    return {"ok": True, "items": items, "running": rt.performance.running()}


# ---- 23.3.22: target environments ------------------------------------------------------------------------

@router.get("/environments")
def environments(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": [e.model_dump() for e in rt.performance.list_environments()]}


@router.post("/environments", status_code=201)
def create_environment(body: PerfEnvironment, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    body.id = ""
    return {"ok": True, "environment": rt.performance.save_environment(body).model_dump()}


@router.put("/environments/{env_id}")
def update_environment(env_id: str, body: PerfEnvironment, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    body.id = env_id
    return {"ok": True, "environment": rt.performance.save_environment(body).model_dump()}


@router.delete("/environments/{env_id}")
def delete_environment(env_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.performance.delete_environment(env_id)
    return {"ok": True}


# ---- 23.3.22: scenarios ----------------------------------------------------------------------------------

class CloneIn(BaseModel):
    name: str | None = None


class ArchiveIn(BaseModel):
    archived: bool = True


@router.get("/scenarios")
def scenarios(q: str = "", kind: str | None = None, environment_id: str | None = None, archived: bool = False,
              rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.performance.list_scenarios(q=q, kind=kind, environment_id=environment_id,
                                                               archived=archived)}


@router.post("/scenarios", status_code=201)
def create_scenario(body: PerfScenario, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    body.id, body.archived = "", False
    sc = rt.performance.save_scenario(body)
    return {"ok": True, "scenario": rt.performance.scenario_detail(sc.id)}


@router.get("/scenarios/{scenario_id}")
def get_scenario(scenario_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "scenario": rt.performance.scenario_detail(scenario_id),
            "runs": rt.performance.list_runs(scenario_id=scenario_id, limit=50)}


@router.put("/scenarios/{scenario_id}")
def update_scenario(scenario_id: str, body: PerfScenario, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    current = rt.performance.get_scenario(scenario_id)
    body.id, body.archived = scenario_id, current.archived
    rt.performance.save_scenario(body)
    return {"ok": True, "scenario": rt.performance.scenario_detail(scenario_id)}


@router.post("/scenarios/{scenario_id}/clone", status_code=201)
def clone_scenario(scenario_id: str, body: CloneIn | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    sc = rt.performance.clone_scenario(scenario_id, (body or CloneIn()).name)
    return {"ok": True, "scenario": rt.performance.scenario_detail(sc.id)}


@router.post("/scenarios/{scenario_id}/archive")
def archive_scenario(scenario_id: str, body: ArchiveIn | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.performance.archive_scenario(scenario_id, (body or ArchiveIn()).archived)
    return {"ok": True, "scenario": rt.performance.scenario_detail(scenario_id)}


@router.post("/scenarios/{scenario_id}/check")
def check_scenario(scenario_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Pre-run checks without running anything: tools, JMX, disk space, hosts and load limits."""
    return {"ok": True, **rt.performance.check_scenario(scenario_id)}


@router.post("/scenarios/{scenario_id}/run", status_code=202)
def run_scenario(scenario_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.performance.get_scenario(scenario_id)
    _require_tools(rt)
    run_id = rt.performance.run_scenario(scenario_id, trigger="manual")
    return {"ok": True, "run_id": run_id, "state": "QUEUED"}


@router.get("/runs/{run_id}")
def get_run(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "run": rt.performance.get_run(run_id)}


@router.post("/runs/{run_id}/analysis")
def analyze(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "analysis": rt.performance.analyze(run_id)}


@router.get("/runs/{run_id}/files/{path:path}")
def run_file(run_id: str, path: str, rt: Runtime = Depends(runtime)) -> FileResponse:
    return FileResponse(rt.performance.run_file(run_id, path))
