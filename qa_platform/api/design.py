"""AI runtime (On-Premise AI / Cloud AI) settings and diagnostics, AI test design and test data API."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from ..agents.base import new_run_id
from ..errors import QAError
from ..logging_setup import get_logger
from ..runtime import Runtime
from .deps import runtime

router = APIRouter(prefix="/api")
log = get_logger("api.design")


class AISettingsIn(BaseModel):
    mode: str | None = Field(default=None, pattern="^(on_premise|cloud)$")
    base_url: str | None = None
    model: str | None = None
    cloud_base_url: str | None = None
    cloud_model: str | None = None
    api_key: str | None = Field(default=None, max_length=4000, description="Cloud AI API key (Credential Manager)")
    clear_api_key: bool = False
    timeout_seconds: float | None = Field(default=None, ge=5, le=1800)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=256, le=32768)


def _ai_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if value and not value.lower().startswith(("http://", "https://")):
        raise QAError("AI_URL_INVALID", "The AI server URL must start with http:// or https://.")
    return value


@router.get("/ai/settings")
def ai_settings(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.ai.config()}


@router.put("/ai/settings")
def put_ai_settings(body: AISettingsIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True, exclude={"cloud_base_url", "cloud_model", "api_key", "clear_api_key"})
    if "base_url" in patch:
        patch["base_url"] = _ai_url(patch["base_url"])
    cloud: dict[str, Any] = {}
    if body.cloud_base_url is not None:
        cloud["base_url"] = _ai_url(body.cloud_base_url)
    if body.cloud_model is not None:
        cloud["model"] = body.cloud_model.strip()
    if cloud:
        patch["cloud"] = cloud
    if body.api_key:
        rt.ai.set_api_key(body.api_key.strip())
    elif body.clear_api_key:
        rt.ai.set_api_key(None)
    rt.settings.update({"ai": patch})
    return {"ok": True, **rt.ai.config()}


class ManagedLLMIn(BaseModel):
    server_path: str | None = Field(default=None, max_length=500)
    model_path: str | None = Field(default=None, max_length=500)
    alias: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[\w.:-]+$")
    host: str | None = Field(default=None, pattern=r"^(127\.0\.0\.1|localhost|0\.0\.0\.0|[\w.-]+)$", max_length=100)
    port: int | None = Field(default=None, ge=1024, le=65535)
    context_size: int | None = Field(default=None, ge=512, le=262144)
    startup_timeout_s: int | None = Field(default=None, ge=10, le=1800)
    auto_start: bool | None = None


@router.get("/ai/managed")
def managed_status(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.llm.status()}


@router.put("/ai/managed")
def managed_settings(body: ManagedLLMIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True)
    for key in ("server_path", "model_path"):
        if key in patch:
            patch[key] = patch[key].strip().strip('"')
    rt.settings.update({"ai": {"managed": patch}})
    return {"ok": True, **rt.llm.status()}


@router.post("/ai/managed/detect")
def managed_detect(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.llm.detect()}


@router.post("/ai/managed/{action}")
def managed_control(action: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    if action == "start":
        result = rt.llm.start(wait=False)
    elif action == "restart":
        rt.llm.stop()
        result = rt.llm.start(wait=False)
    elif action == "stop":
        result = rt.llm.stop()
    else:
        raise QAError("NOT_FOUND", "Unknown action.", status=404)
    return {"ok": True, **result}


@router.get("/ai/models")
def ai_models(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "models": rt.ai.models()}


@router.post("/ai/diagnostics")
def ai_diagnostics(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    result = rt.ai.diagnostics()
    rt.health.ai_tested(result)
    return result


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


# ---- suite-aware test cases (sprint -> user stories -> suite) --------------------------------


@router.get("/design/sprint-stories")
def sprint_stories(iteration: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "stories": rt.design.cases.sprint_stories(iteration)}


class SuiteRunIn(BaseModel):
    plan_id: int
    suite_id: int
    user_story_ids: list[int] = Field(min_length=1, max_length=50)
    testing_type: str | None = None
    max_cases: int = Field(default=8, ge=1, le=30)


@router.post("/design/suite-runs", status_code=202)
async def start_suite_run(body: SuiteRunIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    if _busy(rt):
        raise QAError("ALREADY_RUNNING", "A test design run is already in progress.", status=409)
    suite = rt.design.cases.suite_info(body.plan_id, body.suite_id, body.testing_type)  # fail fast, before the run
    run_id = new_run_id("TCGEN")
    rt.runs.start(run_id, "design", f"{suite['testing_type_name']} test cases for {len(body.user_story_ids)} user "
                  "stories", user_story_id=str(body.user_story_ids[0]))

    def work() -> None:
        try:
            rt.design.cases.run(body.plan_id, body.suite_id, body.user_story_ids, testing_type=body.testing_type,
                                max_cases=body.max_cases, run_id=run_id)
        except Exception:
            log.info("Suite test case generation failed", extra={"run_id": run_id})

    asyncio.get_running_loop().run_in_executor(None, work)
    return {"ok": True, "run_id": run_id, "testing_type": suite["testing_type_name"]}


@router.get("/design/suite-runs")
def suite_runs(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "runs": rt.design.cases.history()}


@router.get("/design/suite-runs/{run_id}")
def suite_run(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    run = rt.runs.get(run_id)
    if run is None:
        raise QAError("RUN_NOT_FOUND", f"Run {run_id} does not exist.", status=404, run_id=run_id)
    result = None
    if run["status"] != "RUNNING":
        try:
            result = rt.design.cases.get(run_id)
        except QAError:
            result = {"error": (run.get("summary") or {}).get("error")}
    return {"ok": True, "status": run["status"], "result": result}


class CaseEditIn(BaseModel):
    case: dict[str, Any]


@router.patch("/design/suite-runs/{run_id}/stories/{story_id}/cases/{ref}")
def edit_case(run_id: str, story_id: int, ref: str, body: CaseEditIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "story": rt.design.cases.edit_case(run_id, story_id, ref, body.case)}


class SuitePublishIn(BaseModel):
    selection: dict[str, list[str]] = Field(description="{user story id: [refs]}")
    reuse: dict[str, int] | None = Field(default=None, description="{\"<story id>:<ref>\": existing test case id}")


@router.post("/design/suite-runs/{run_id}/publish")
def publish_suite_run(run_id: str, body: SuitePublishIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    if not any(body.selection.values()):
        raise QAError("NOTHING_SELECTED", "Select at least one test case.")
    return {"ok": True, **rt.design.cases.publish(run_id, body.selection, body.reuse)}


@router.get("/design/traceability/{story_id}")
def traceability(story_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.design.cases.traceability(story_id)}


# ---- test data ------------------------------------------------------------------------


class TestDataIn(BaseModel):
    name: str | None = None
    values: dict[str, Any] | None = None
    scenario: str | None = None
    intended_valid: bool | None = None
    violated_rule: str | None = None
    expected_outcome: str | None = None
    ac_ids: list[str] | None = None


def _ids(value: str | None) -> list[int]:
    return [int(x) for x in (value or "").replace(";", ",").split(",") if x.strip().isdigit()]


@router.get("/test-data")
def test_data(
    test_case_id: int | None = None,
    test_case_ids: str | None = Query(default=None, description="Comma-separated TFS test case ids"),
    user_story_id: str | None = None,
    iteration: str | None = None,
    limit: int = Query(500, ge=1, le=5000),
    rt: Runtime = Depends(runtime),
) -> dict[str, Any]:
    ids = _ids(test_case_ids) + ([test_case_id] if test_case_id else [])
    return {"ok": True, "items": rt.design.datasets.list(iteration=iteration, user_story_id=user_story_id,
                                                         test_case_ids=ids or None, limit=limit)}


class GenerateDataIn(BaseModel):
    test_case_ids: list[int] | None = None
    user_story_id: int | None = None
    seed: int | None = Field(default=None, ge=0, le=2_000_000_000)
    max_rows: int = Field(default=12, ge=1, le=60)


@router.post("/test-data/generate", status_code=202)
async def generate_test_data(body: GenerateDataIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    ids = list(body.test_case_ids or [])
    if not ids and body.user_story_id:
        story = rt.tfs.detail(body.user_story_id)
        ids = [t["id"] for t in story["traceability"]["test_cases"]]
        ids += [r["tfs_test_case_id"] for r in rt.design.cases._trace(body.user_story_id)
                if r["tfs_test_case_id"] not in ids]
    if not ids:
        raise QAError("TEST_CASE_REQUIRED", "Select at least one test case, or a user story with linked test cases.")
    if any(s["status"] == "RUNNING" for s in rt.state.snapshot() if s["agent_id"] == "test_data"):
        raise QAError("ALREADY_RUNNING", "Test data is already being generated.", status=409)
    run_id = new_run_id("TDATA")
    rt.runs.start(run_id, "design", f"Test data for {len(ids)} test cases",
                  user_story_id=str(body.user_story_id) if body.user_story_id else None)

    def work() -> None:
        try:
            result = rt.design.datasets.generate(ids, seed=body.seed, max_rows=body.max_rows, run_id=run_id)
            rt.runs.finish(run_id, "PASSED", result)
        except Exception as exc:
            rt.runs.finish(run_id, "FAILED", {"error": getattr(exc, "message", None) or str(exc)})

    asyncio.get_running_loop().run_in_executor(None, work)
    return {"ok": True, "run_id": run_id, "test_case_ids": ids}


@router.get("/test-data/generate/{run_id}")
def generate_status(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    run = rt.runs.get(run_id)
    if run is None:
        raise QAError("RUN_NOT_FOUND", f"Run {run_id} does not exist.", status=404, run_id=run_id)
    return {"ok": True, "status": run["status"], "result": run.get("summary") if run["status"] != "RUNNING" else None}


@router.patch("/test-data/{data_id}")
def update_test_data(data_id: int, body: TestDataIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "item": rt.design.datasets.update(data_id, body.model_dump(exclude_none=True))}


@router.get("/test-data/{data_id}/history")
def test_data_history(data_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "history": rt.design.datasets.history(data_id)}


class TestDataToTfsIn(BaseModel):
    summary: bool = True
    parameters: bool = True
    target: str = Field(default="legacy", pattern="^(auto|legacy)$",
                        description="auto: the test data tab when the test case type has one, otherwise the Summary; "
                                    "legacy: the Summary and/or Parameter values as chosen")


@router.post("/test-data/tfs/{test_case_id}")
def test_data_to_tfs(test_case_id: int, body: TestDataToTfsIn | None = None,
                     rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    body = body or TestDataToTfsIn()
    if body.target == "auto":
        return {"ok": True, **rt.design.datasets.write_to_tfs(test_case_id)}
    if not (body.summary or body.parameters):
        raise QAError("NOTHING_SELECTED", "Choose the Summary, the Parameter values, or both.")
    return {"ok": True, **rt.design.write_test_data_to_tfs(test_case_id, summary=body.summary,
                                                            parameters=body.parameters)}


class DeleteDataIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=5000)


@router.post("/test-data/delete")
def delete_selected(body: DeleteDataIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "deleted": rt.design.datasets.delete(body.ids)}


@router.get("/test-data/export")
def export_test_data(
    fmt: str = Query("csv", pattern="^(csv|json)$"),
    ids: str | None = None,
    test_case_ids: str | None = None,
    user_story_id: str | None = None,
    iteration: str | None = None,
    rt: Runtime = Depends(runtime),
) -> Response:
    rows = rt.design.datasets.list(iteration=iteration, user_story_id=user_story_id,
                                   test_case_ids=_ids(test_case_ids) or None, limit=5000)
    wanted = set(_ids(ids))
    if wanted:
        rows = [r for r in rows if r["id"] in wanted]
    text, media = rt.design.datasets.export(rows, fmt)
    return Response(("\ufeff" + text) if fmt == "csv" else text, media_type=f"{media}; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="test-data.{fmt}"'})


class ImportDataIn(BaseModel):
    format: str = Field(pattern="^(csv|json)$")
    content: str = Field(max_length=10_000_000)
    test_case_id: int | None = None


@router.post("/test-data/import")
def import_test_data(body: ImportDataIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    try:
        return {"ok": True, **rt.design.datasets.import_rows(body.content, body.format, body.test_case_id)}
    except (ValueError, KeyError) as exc:
        raise QAError("IMPORT_INVALID", f"The file could not be read: {exc}") from exc


@router.get("/test-data/binding/{test_case_id}")
def binding(test_case_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.design.datasets.binding(test_case_id)}


@router.get("/test-data/coverage/{story_id}")
def data_coverage(story_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.design.datasets.coverage(story_id)}


@router.delete("/test-data/{data_id}")
def delete_test_data(data_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.design.delete_test_data(data_id)
    return {"ok": True}
