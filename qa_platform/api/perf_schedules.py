"""23.3.29: performance schedules, their job history, the scheduler host, and start at Windows sign-in."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import startup
from ..runtime import Runtime
from .deps import runtime

router = APIRouter(prefix="/api/performance")


class EmailIn(BaseModel):
    enabled: bool = False
    to: str | list[str] | None = None
    cc: str | list[str] | None = None
    on: str = Field(default="always", pattern="^(always|failures)$")


class NotifyIn(BaseModel):
    email: EmailIn = EmailIn()


class ReportIn(BaseModel):
    pdf: bool = True


class ScheduleIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    scenario_id: str | None = None
    repeat: str | None = Field(default=None, pattern="^(once|daily|days|weekly|interval)$")
    time: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    days: list[int] | None = None
    every_weeks: int | None = Field(default=None, ge=1, le=52)
    interval_minutes: int | None = None
    timezone: str | None = Field(default=None, max_length=64)
    version_policy: str | None = Field(default=None, pattern="^(latest|pinned)$")
    repin: bool = False
    max_runtime_min: int | None = None
    overlap: str | None = Field(default=None, pattern="^(skip|queue)$")
    missed: str | None = Field(default=None, pattern="^(skip|run_once)$")
    report: ReportIn | None = None
    notify: NotifyIn | None = None
    build: str | None = Field(default=None, max_length=120)
    enabled: bool | None = None


def _body(body: ScheduleIn) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=True)
    if body.repin:
        data["repin"] = True
    return data


@router.get("/schedules")
def list_schedules(scenario_id: str | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.perf_scheduler.list(scenario_id), "host": rt.perf_scheduler.host()}


@router.post("/schedules", status_code=201)
def create_schedule(body: ScheduleIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "schedule": rt.perf_scheduler.create(_body(body))}


@router.get("/schedules/host")
def schedule_host(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "host": rt.perf_scheduler.host()}


@router.get("/schedules/jobs")
def all_jobs(limit: int = 100, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.perf_scheduler.jobs(None, max(1, min(500, limit)))}


@router.get("/schedules/{sid}")
def get_schedule(sid: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "schedule": rt.perf_scheduler.get(sid), "jobs": rt.perf_scheduler.jobs(sid)}


@router.put("/schedules/{sid}")
def update_schedule(sid: str, body: ScheduleIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "schedule": rt.perf_scheduler.update(sid, _body(body))}


@router.post("/schedules/{sid}/pause")
def pause_schedule(sid: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "schedule": rt.perf_scheduler.set_enabled(sid, False)}


@router.post("/schedules/{sid}/resume")
def resume_schedule(sid: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "schedule": rt.perf_scheduler.set_enabled(sid, True)}


@router.post("/schedules/{sid}/run", status_code=202)
def run_schedule_now(sid: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.perf_scheduler.run_now(sid)}


@router.delete("/schedules/{sid}")
def delete_schedule(sid: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.perf_scheduler.delete(sid)
    return {"ok": True}


class StartupIn(BaseModel):
    enabled: bool


@router.get("/startup")
def startup_status() -> dict[str, Any]:
    return {"ok": True, "startup": startup.status()}


@router.put("/startup")
def set_startup(body: StartupIn) -> dict[str, Any]:
    return {"ok": True, "startup": startup.enable() if body.enabled else startup.disable()}
