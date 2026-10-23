"""On-Premise AI settings/diagnostics, AI test design and test data API."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..agents.base import new_run_id
from ..errors import QAError
from ..logging_setup import get_logger
from ..runtime import Runtime
from .deps import runtime

router = APIRouter(prefix="/api")
log = get_logger("api.design")


class AISettingsIn(BaseModel):
    base_url: str | None = None
    model: str | None = None
    timeout_seconds: float | None = Field(default=None, ge=5, le=1800)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=256, le=32768)


@router.get("/ai/settings")
def ai_settings(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.ai.config()}


@router.put("/ai/settings")
def put_ai_settings(body: AISettingsIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True)
    if "base_url" in patch:
        patch["base_url"] = patch["base_url"].strip().rstrip("/")
        if patch["base_url"] and not patch["base_url"].lower().startswith(("http://", "https://")):
            raise QAError("AI_URL_INVALID", "The AI server URL must start with http:// or https://.")
    rt.settings.update({"ai": patch})
    return {"ok": True, **rt.ai.config()}


@router.get("/ai/models")
def ai_models(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "models": rt.ai.models()}


@router.post("/ai/diagnostics")
def ai_diagnostics(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return rt.ai.diagnostics()


# ---- test design --------------------------------------------------------------------


class DesignIn(BaseModel):
    user_story_id: int
    max_cases: int = Field(default=12, ge=1, le=40)


class PublishIn(BaseModel):
    refs: list[str] | None = None
    plan_id: int | None = None
    suite_id: int | None = None
    create_plan: bool = False
    plan_name: str | None = Field(default=None, max_length=256)
    default_suites: bool = True
    data_in_summary: bool = True
    data_as_parameters: bool = True


def _busy(rt: Runtime) -> bool:
    ids = {"requirements", "test_planning", "test_case_design", "test_data", "test_suite_design"}
    return any(s["status"] == "RUNNING" for s in rt.state.snapshot() if s["agent_id"] in ids)


@router.post("/design/runs", status_code=202)
async def start_design(body: DesignIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    if _busy(rt):
        raise QAError("ALREADY_RUNNING", "A test design run is already in progress.", status=409)
    run_id = new_run_id("DESIGN")

    def work() -> None:
        try:
            rt.design.run(body.user_story_id, run_id, max_cases=body.max_cases)
        except Exception:
            log.info("Test design run failed", extra={"run_id": run_id})

    asyncio.get_running_loop().run_in_executor(None, work)
    return {"ok": True, "run_id": run_id}


@router.get("/design/runs/{run_id}")
def design_result(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    run = rt.runs.get(run_id)
    if run is None:
        raise QAError("RUN_NOT_FOUND", f"Run {run_id} does not exist.", status=404, run_id=run_id)
    result = None
    if run["status"] != "RUNNING":
        try:
            result = rt.design.get(run_id)
        except QAError:
            result = None
    return {"ok": True, "status": run["status"], "result": result}


@router.get("/design/stories/{user_story_id}")
def design_history(user_story_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "runs": rt.design.latest_for_story(user_story_id)}


@router.post("/design/runs/{run_id}/publish")
def publish(run_id: str, body: PublishIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    if body.suite_id is not None and body.plan_id is None:
        raise QAError("SUITE_INCOMPLETE", "Choose the test plan of that suite as well.")
    if body.create_plan and body.plan_id is not None:
        raise QAError("PLAN_CONFLICT", "Either create a new test plan or choose an existing one, not both.")
    return {"ok": True, **rt.design.publish(
        run_id, body.refs, body.plan_id, body.suite_id, create_plan=body.create_plan, plan_title=body.plan_name,
        default_suites=body.default_suites, data_in_summary=body.data_in_summary,
        data_as_parameters=body.data_as_parameters)}


# ---- test data ------------------------------------------------------------------------


class TestDataIn(BaseModel):
    name: str | None = None
    values: dict[str, Any] | None = None


@router.get("/test-data")
def test_data(
    test_case_id: int | None = None,
    user_story_id: str | None = None,
    limit: int = Query(500, ge=1, le=2000),
    rt: Runtime = Depends(runtime),
) -> dict[str, Any]:
    return {"ok": True, "items": rt.design.list_test_data(test_case_id, user_story_id, limit)}


@router.patch("/test-data/{data_id}")
def update_test_data(data_id: int, body: TestDataIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "item": rt.design.update_test_data(data_id, body.name, body.values)}


class TestDataToTfsIn(BaseModel):
    summary: bool = True
    parameters: bool = True


@router.post("/test-data/tfs/{test_case_id}")
def test_data_to_tfs(test_case_id: int, body: TestDataToTfsIn | None = None,
                     rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    body = body or TestDataToTfsIn()
    if not (body.summary or body.parameters):
        raise QAError("NOTHING_SELECTED", "Choose the Summary, the Parameter values, or both.")
    return {"ok": True, **rt.design.write_test_data_to_tfs(test_case_id, summary=body.summary,
                                                            parameters=body.parameters)}


@router.delete("/test-data/{data_id}")
def delete_test_data(data_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.design.delete_test_data(data_id)
    return {"ok": True}
