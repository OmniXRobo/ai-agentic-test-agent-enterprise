"""TFS / Azure DevOps API: profiles, connection test, explorer, work items, test management."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..automation.generator import parse_tfs_steps
from ..design import models, testing_types
from ..design.service import DEFAULT_SUITES, plan_name, steps_xml
from ..errors import QAError
from ..runtime import Runtime
from .deps import runtime

router = APIRouter(prefix="/api/tfs")


class ProfileIn(BaseModel):
    url: str | None = None
    project: str | None = None
    api_version: str | None = None
    verify_tls: bool | None = None
    pat: str | None = Field(default=None, description="Write-only. Stored in the OS credential store.")
    make_active: bool = False


class WorkItemIn(BaseModel):
    type: str | None = None
    fields: dict[str, Any]


@router.get("/profiles")
def get_profiles(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.tfs.profiles()}


@router.put("/profiles/{mode}")
def put_profile(mode: Literal["online", "offline"], body: ProfileIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    result = rt.tfs.save_profile(mode, url=body.url, project=body.project, api_version=body.api_version,
                                 verify_tls=body.verify_tls, pat=body.pat, make_active=body.make_active)
    return {"ok": True, **result}


@router.delete("/profiles/{mode}/pat")
def delete_pat(mode: Literal["online", "offline"], rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.tfs.clear_pat(mode)
    return {"ok": True, **rt.tfs.profiles()}


@router.post("/test/{mode}")
def test_connection(mode: Literal["online", "offline"], rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    result = rt.tfs.test_connection(mode)
    rt.health.tfs_tested(result)
    return {**result, "ok": result["ok"]}


@router.get("/projects")
def projects(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "projects": rt.tfs.projects()}


@router.get("/meta")
def meta(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.tfs.meta()}


@router.get("/work-items")
def work_items(
    category: Literal["requirement", "bug", "testcase", "testplan", "testsuite", "task"] | None = None,
    type: str | None = None,
    state: str | None = None,
    iteration: str | None = None,
    assigned_to: str | None = None,
    q: str | None = None,
    top: int = Query(200, ge=1, le=1000),
    rt: Runtime = Depends(runtime),
) -> dict[str, Any]:
    result = rt.tfs.query(category=category, type_name=type, state=state, iteration=iteration,
                          assigned_to=assigned_to, text=q, top=top)
    return {"ok": True, **result}


@router.get("/work-items/{item_id}")
def work_item(item_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    item = rt.tfs.detail(item_id)
    return {"ok": True, "item": {**item, "step_list": parse_tfs_steps(item.get("steps"))}}


@router.post("/work-items", status_code=201)
def create_work_item(body: WorkItemIn, category: str | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    type_name = body.type or (rt.tfs.default_type(category) if category else None)
    if not type_name:
        raise QAError("TFS_TYPE_REQUIRED", "Give a work item type or a category.")
    return {"ok": True, **rt.tfs.create(type_name, _with_steps(body.fields))}


@router.patch("/work-items/{item_id}")
def update_work_item(item_id: int, body: WorkItemIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.tfs.update(item_id, _with_steps(body.fields))}


def _with_steps(fields: dict[str, Any]) -> dict[str, Any]:
    """'steps': [{action, expected}] -> the test case's Steps field."""
    steps = fields.pop("steps", None)
    if isinstance(steps, list):
        rows = [models.TestStep(action=str(s.get("action") or "").strip(), expected=str(s.get("expected") or "").strip())
                for s in steps if isinstance(s, dict) and str(s.get("action") or "").strip()]
        if not rows:
            raise QAError("STEPS_REQUIRED", "A test case needs at least one step with an action.")
        fields["Microsoft.VSTS.TCM.Steps"] = steps_xml(rows)
    return fields


@router.delete("/work-items/{item_id}")
def delete_work_item(item_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    result = rt.tfs.delete(item_id)
    rt.design.cases.forget_case(item_id)
    return {"ok": True, **result}


class DeleteItemsIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)


@router.post("/work-items/delete")
def delete_work_items(body: DeleteItemsIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Delete several work items; each one is reported, so one failure does not hide the others."""
    deleted, failed = [], []
    for i in dict.fromkeys(body.ids):
        try:
            deleted.append(rt.tfs.delete(i))
            rt.design.cases.forget_case(i)
        except QAError as exc:
            failed.append({"id": i, "error": exc.message})
    return {"ok": not failed, "deleted": deleted, "failed": failed}


@router.get("/test-plans")
def test_plans(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "plans": rt.tfs.test_plans()}


class TestPlanIn(BaseModel):
    name: str | None = Field(default=None, max_length=256)
    user_story_id: int | None = None
    area_path: str | None = None
    iteration: str | None = Field(default=None, description="Sprint path; empty or missing = no sprint")
    description: str | None = Field(default=None, max_length=20000)
    default_suites: bool = True
    suite_types: list[str] | None = Field(default=None, description="Testing types to create as suites "
                                                                    "(see /api/tfs/testing-types)")


@router.post("/test-plans", status_code=201)
def create_test_plan(body: TestPlanIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Reuse or create a test plan, linked to a sprint or not, optionally with one suite per testing type."""
    area, iteration = body.area_path, (body.iteration or "").strip() or None
    if body.user_story_id:
        story = rt.tfs.detail(body.user_story_id)
        area, iteration = area or story.get("area_path"), iteration or story.get("iteration_path")
    name = (body.name or "").strip() or plan_name(rt.tfs.profile()["project"], iteration)
    if not name:
        raise QAError("PLAN_NAME_REQUIRED", "Give the test plan a name or a User Story.")
    plan = rt.tfs.ensure_test_plan(name, area_path=area, iteration=iteration, description=body.description)
    if body.suite_types is not None:
        suites = rt.design.cases.create_suites(plan["id"], body.suite_types) if body.suite_types else []
    else:
        suites = rt.tfs.ensure_suites(plan["id"], DEFAULT_SUITES, plan.get("root_suite_id")) if body.default_suites else []
    return {"ok": True, "plan": plan, "suites": suites}


class TestPlanUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=256)
    iteration: str | None = Field(default=None, description="New sprint path; empty string = no sprint")
    area_path: str | None = None
    state: str | None = Field(default=None, pattern="^(Active|Inactive)$")
    description: str | None = Field(default=None, max_length=20000)


@router.patch("/test-plans/{plan_id}")
def update_test_plan(plan_id: int, body: TestPlanUpdate, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "plan": rt.tfs.update_test_plan(plan_id, name=body.name, iteration=body.iteration,
                                                        area_path=body.area_path, state=body.state,
                                                        description=body.description)}


@router.delete("/test-plans/{plan_id}")
def delete_test_plan(plan_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Deletes the test plan (with its suites) in TFS. Test case work items are not deleted."""
    rt.tfs.delete_test_plan(plan_id)
    rt.design.cases.forget_plan(plan_id)
    return {"ok": True, "deleted": plan_id}


@router.get("/testing-types")
def list_testing_types() -> dict[str, Any]:
    return {"ok": True, "testing_types": testing_types.public()}


class SuitesIn(BaseModel):
    testing_types: list[str] = Field(min_length=1)
    parent_suite_id: int | None = None
    name_prefix: str | None = Field(default=None, max_length=120)


@router.post("/test-plans/{plan_id}/suites", status_code=201)
def create_suites(plan_id: int, body: SuitesIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """One static suite per checked testing type; a suite with the same name under the same parent is reused."""
    return {"ok": True, "suites": rt.design.cases.create_suites(plan_id, body.testing_types, body.parent_suite_id,
                                                               body.name_prefix)}


class SuiteUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=256)
    testing_type: str | None = None


@router.patch("/test-plans/{plan_id}/suites/{suite_id}")
def update_suite(plan_id: int, suite_id: int, body: SuiteUpdate, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    if body.name is None and body.testing_type is None:
        raise QAError("TFS_NOTHING_TO_UPDATE", "Nothing to change on this suite.")
    suite: dict[str, Any] = {"id": suite_id}
    if body.name is not None:
        suite = rt.tfs.update_test_suite(plan_id, suite_id, body.name)
    if body.testing_type:
        rt.design.cases.set_suite_type(plan_id, suite_id, body.testing_type)
        suite["testing_type"] = body.testing_type
    return {"ok": True, "suite": suite}


@router.delete("/test-plans/{plan_id}/suites/{suite_id}")
def delete_suite(plan_id: int, suite_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Deletes the suite in TFS. Its test case work items stay in the project."""
    rt.tfs.delete_test_suite(plan_id, suite_id)
    rt.design.cases.forget_suite(suite_id)
    return {"ok": True, "deleted": suite_id}


@router.get("/test-plans/{plan_id}/suites")
def test_suites(plan_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "suites": rt.design.cases.suites(plan_id)}


@router.get("/test-plans/{plan_id}/suites/{suite_id}/test-cases")
def suite_test_cases(plan_id: int, suite_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "test_cases": rt.tfs.suite_test_cases(plan_id, suite_id)}


@router.post("/cache/clear")
def clear_cache(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.tfs.invalidate()
    return {"ok": True}
