"""AI-Driven Automation Script Studio API: selection, generation jobs, inventory, script workspace, Play."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..automation.framework.studio import JobRequest
from ..errors import QAError
from ..runtime import Runtime
from .deps import runtime

router = APIRouter(prefix="/api/automation/studio")


# ---- selection: Sprint -> User Stories -> Test Cases --------------------------------------


@router.get("/context")
def context(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.studio.selection_context(), "ai_runtime": rt.studio._ai_label()}


@router.get("/stories")
def stories(sprint: str | None = None, text: str | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.studio.stories(sprint, text)}


@router.get("/stories/{story_id}/test-cases")
def story_test_cases(story_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.studio.story_test_cases(story_id)}


class ExistingIn(BaseModel):
    test_case_ids: list[int] = Field(min_length=1, max_length=2000)


@router.post("/existing")
def existing(body: ExistingIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.studio.check_existing(body.test_case_ids)}


# ---- generation jobs ---------------------------------------------------------------------------


@router.post("/jobs", status_code=202)
def start_job(body: JobRequest, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "job_id": rt.studio.start_job(body)}


@router.get("/jobs")
def jobs(limit: int = Query(20, ge=1, le=200), rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.studio.jobs(limit)}


@router.get("/jobs/{job_id}")
def job(job_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "job": rt.studio.job(job_id)}


@router.post("/jobs/{job_id}/stop")
def stop_job(job_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.studio.job(job_id)
    if not rt.studio.stop_job(job_id):
        raise QAError("JOB_NOT_ACTIVE", f"Generation job {job_id} is not running.", status=409)
    return {"ok": True}


@router.post("/job-items/{item_id}/retry", status_code=202)
def retry_item(item_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "job_id": rt.studio.retry_item(item_id)}


# ---- inventory and one script --------------------------------------------------------------------


@router.get("/inventory")
def inventory(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.studio.inventory()}


@router.get("/export")
def export(rt: Runtime = Depends(runtime)) -> Response:
    return Response(rt.studio.export_zip(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="automation-workspace.zip"'})


@router.get("/scripts/{script_id}")
def script(script_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.studio.script(script_id)}


@router.delete("/scripts/{script_id}")
def archive(script_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.studio.archive(script_id)
    return {"ok": True}


class SourceIn(BaseModel):
    source: str = Field(max_length=500_000)
    reason: str | None = Field(default=None, max_length=200)


@router.put("/scripts/{script_id}/source")
def save_source(script_id: str, body: SourceIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.studio.save_source(script_id, body.source, body.reason)}


class DataIn(BaseModel):
    combinations: list[dict[str, Any]] = Field(min_length=1, max_length=500)
    fields: dict[str, Any] | None = None
    reason: str | None = Field(default=None, max_length=200)


@router.put("/scripts/{script_id}/data")
def save_data(script_id: str, body: DataIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.studio.save_data(script_id, body.combinations, body.fields, body.reason)}


class FlowIn(BaseModel):
    operations: list[dict[str, Any]] = Field(min_length=1, max_length=100)


@router.post("/scripts/{script_id}/flow")
def edit_flow(script_id: str, body: FlowIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.studio.edit_flow(script_id, body.operations)}


class ScriptCredentialIn(BaseModel):
    scope: str = Field(pattern="^(script|application|clear_script)$")
    username: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, max_length=500)
    application: str | None = Field(default=None, max_length=120)
    environment: str | None = Field(default=None, max_length=60)
    role: str | None = Field(default=None, max_length=60)


@router.put("/scripts/{script_id}/credentials")
def save_credentials(script_id: str, body: ScriptCredentialIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.studio.save_credentials(
        script_id, username=body.username, password=body.password, scope=body.scope, application=body.application,
        environment=body.environment, role=body.role)}


@router.get("/scripts/{script_id}/versions/{version}")
def version(script_id: str, version: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.studio.version_content(script_id, version)}


@router.get("/scripts/{script_id}/compare")
def compare(script_id: str, a: int, b: int | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.studio.compare(script_id, a, b)}


@router.post("/scripts/{script_id}/restore/{version}")
def restore(script_id: str, version: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.studio.restore(script_id, version)}


class RegenerateIn(BaseModel):
    base_url: str | None = None


@router.post("/scripts/{script_id}/regenerate", status_code=202)
def regenerate(script_id: str, body: RegenerateIn | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "job_id": rt.studio.regenerate(script_id, (body or RegenerateIn()).base_url)}


class PlayIn(BaseModel):
    combination: str | None = Field(default=None, max_length=80)
    headless: bool | None = None


@router.post("/scripts/{script_id}/run", status_code=202)
def run(script_id: str, body: PlayIn | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    body = body or PlayIn()
    return {"ok": True, "run_id": rt.studio.run(script_id, combination=body.combination, headless=body.headless)}


class PlayManyIn(BaseModel):
    script_ids: list[str] = Field(min_length=1, max_length=500)
    headless: bool | None = None


@router.post("/run-many", status_code=202)
def run_many(body: PlayManyIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "runs": rt.studio.run_many(body.script_ids, headless=body.headless)}


@router.get("/scripts/{script_id}/runs/{run_id}")
def run_result(script_id: str, run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "result": rt.studio.run_result(script_id, run_id)}


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    if not rt.studio.runner.stop(run_id):
        raise QAError("RUN_NOT_ACTIVE", f"Run {run_id} is not executing.", status=409, run_id=run_id)
    return {"ok": True}


@router.get("/scripts/{script_id}/results")
def results(script_id: str, limit: int = Query(20, ge=1, le=200), rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.studio.results(script_id, limit), "running": rt.studio.runner.running()}


@router.get("/scripts/{script_id}/logs")
def logs(script_id: str, run_id: str | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.studio.run_logs(script_id, run_id)}


# ---- shared components and healing ---------------------------------------------------------------


@router.get("/components")
def component(path: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.studio.component(path)}


class ComponentIn(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    source: str = Field(max_length=500_000)


@router.put("/components")
def save_component(body: ComponentIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.studio.save_component(body.path, body.source)}


@router.post("/healing/{healing_id}/apply")
def apply_healing(healing_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.studio.apply_healing(healing_id)}
