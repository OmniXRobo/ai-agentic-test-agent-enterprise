"""Performance testing API: plans, JMeter runs, results, SLA, AI analysis, tool settings."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from ..agents.base import new_run_id
from ..errors import QAError
from ..logging_setup import get_logger
from ..performance.jmx import build_jmx
from ..performance.models import PerfPlan
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
def create_plan(body: PerfPlan, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    body.id = ""
    body.source = body.source or "manual"
    return {"ok": True, "plan": rt.performance.save_plan(body).model_dump()}


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


@router.post("/plans/{plan_id}/run", status_code=202)
async def run_plan(plan_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.performance.get_plan(plan_id)
    if rt.performance.running():
        raise QAError("PERF_ALREADY_RUNNING", "A performance test is already running.", status=409)
    env = rt.performance.environment()
    if not env["ready"]:
        missing = "Java" if not env["java"] else "JMeter"
        raise QAError(f"{missing.upper()}_NOT_FOUND", f"{missing} was not found. Set it under Performance tool settings.",
                      status=409)
    run_id = new_run_id("PERF")

    def work() -> None:
        try:
            rt.performance.run(plan_id, run_id)
        except Exception:
            log.info("Performance run ended with an error", extra={"run_id": run_id})

    asyncio.get_running_loop().run_in_executor(None, work)
    return {"ok": True, "run_id": run_id}


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    if not rt.performance.stop(run_id):
        raise QAError("RUN_NOT_ACTIVE", f"Run {run_id} is not executing.", status=409, run_id=run_id)
    return {"ok": True}


@router.get("/runs")
def runs(plan_id: str | None = None, kind: str | None = None, limit: int = Query(100, ge=1, le=1000),
         rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.performance.list_runs(plan_id, kind, limit), "running": rt.performance.running()}


@router.get("/runs/{run_id}")
def get_run(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "run": rt.performance.get_run(run_id)}


@router.post("/runs/{run_id}/analysis")
def analyze(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "analysis": rt.performance.analyze(run_id)}


@router.get("/runs/{run_id}/files/{path:path}")
def run_file(run_id: str, path: str, rt: Runtime = Depends(runtime)) -> FileResponse:
    return FileResponse(rt.performance.run_file(run_id, path))
