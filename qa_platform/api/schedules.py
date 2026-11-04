"""23.3.21: Scheduled Runs (Automation Studio) and the Outlook email settings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..runtime import Runtime
from .deps import runtime

router = APIRouter(prefix="/api")


class EmailIn(BaseModel):
    enabled: bool = False
    to: str | None = Field(default=None, max_length=2000)
    cc: str | None = Field(default=None, max_length=2000)
    on: str = "always"
    attach_screenshots: bool = True


class ScheduleIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    script_ids: list[str] | None = None
    repeat: str | None = None
    time: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    days: list[int] | None = None
    every_hours: int | None = Field(default=None, ge=1, le=24)
    browsers: list[str] | None = None
    parallel: int | None = Field(default=None, ge=1, le=8)
    headless: bool | None = None
    data_rows: str | None = None
    catch_up: bool | None = None
    publish_tfs: bool | None = None
    enabled: bool | None = None
    email: EmailIn | None = None


class RunNowIn(BaseModel):
    script_ids: list[str] = Field(min_length=1)
    browsers: list[str] = ["chromium"]
    parallel: int = Field(default=1, ge=1, le=8)
    headless: bool = False
    data_rows: str = "first"
    publish_tfs: bool = False
    email: EmailIn | None = None
    name: str | None = Field(default=None, max_length=120)


class RunSettingsIn(BaseModel):
    parallel_runs: int = Field(ge=1, le=8)


class EmailSettingsIn(BaseModel):
    mode: str | None = None
    preset: str | None = None
    host: str | None = Field(default=None, max_length=200)
    port: int | None = Field(default=None, ge=1, le=65535)
    security: str | None = None
    username: str | None = Field(default=None, max_length=200)
    sender: str | None = Field(default=None, max_length=200)
    sender_name: str | None = Field(default=None, max_length=120)
    default_to: str | None = Field(default=None, max_length=2000)
    verify_tls: bool | None = None
    secret: str | None = Field(default=None, max_length=500)  # the SMTP password / app password (kept in the vault)
    clear_secret: bool = False


class TestEmailIn(BaseModel):
    to: str | None = None


def _body(model: BaseModel) -> dict[str, Any]:
    data = model.model_dump(exclude_none=True)
    if "email" in data and isinstance(data["email"], dict):
        data["email"] = {k: v for k, v in data["email"].items() if v is not None}
    return data


@router.get("/schedules")
def list_schedules(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.scheduler.list(), "active": list(rt.scheduler.active.values()),
            "parallel_runs": rt.automation._exec_lock.default(), "email_configured": rt.mailer.configured()}


@router.get("/schedules/scripts")
def schedule_scripts(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.scheduler.scripts()}


@router.get("/schedules/history")
def schedule_history(schedule_id: str | None = None, limit: int = 50, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.scheduler.history(schedule_id, max(1, min(limit, 300)))}


@router.post("/schedules", status_code=201)
def create_schedule(body: ScheduleIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "schedule": rt.scheduler.create(_body(body))}


@router.get("/schedules/{schedule_id}")
def get_schedule(schedule_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "schedule": rt.scheduler.get(schedule_id), "history": rt.scheduler.history(schedule_id, 20)}


@router.put("/schedules/{schedule_id}")
def update_schedule(schedule_id: str, body: ScheduleIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "schedule": rt.scheduler.update(schedule_id, _body(body))}


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.scheduler.delete(schedule_id)
    return {"ok": True}


@router.post("/schedules/{schedule_id}/run", status_code=202)
def run_schedule(schedule_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "batch_id": rt.scheduler.run_now(schedule_id, trigger="manual")}


@router.post("/schedule-runs", status_code=202)
def run_now(body: RunNowIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Run scripts now with batch options (browsers, at the same time, data rows, TFS, email)."""
    data = _body(body)
    data["name"] = data.get("name") or "Run now"
    for sid in data["script_ids"]:
        rt.scheduler.script_info(sid)
    return {"ok": True, "batch_id": rt.scheduler.run_batch(data, trigger="manual")}


@router.put("/schedules-settings")
def run_settings(body: RunSettingsIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.settings.update({"automation": {"parallel_runs": body.parallel_runs}})
    return {"ok": True, "parallel_runs": rt.automation._exec_lock.default()}


@router.get("/email/settings")
def email_settings(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "email": rt.mailer.config()}


@router.put("/email/settings")
def save_email_settings(body: EmailSettingsIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    values = body.model_dump(exclude_none=True, exclude={"secret", "clear_secret"})
    return {"ok": True, "email": rt.mailer.save(values, password=body.secret, clear_password=body.clear_secret)}


@router.post("/email/test")
def test_email(body: TestEmailIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.mailer.send_test(body.to)}
