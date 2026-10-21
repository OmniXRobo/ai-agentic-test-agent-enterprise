"""TFS / Azure DevOps API: profiles, connection test, explorer, work items, test management."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

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
    return {"ok": True, "item": rt.tfs.detail(item_id)}


@router.post("/work-items", status_code=201)
def create_work_item(body: WorkItemIn, category: str | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    type_name = body.type or (rt.tfs.default_type(category) if category else None)
    if not type_name:
        from ..errors import QAError

        raise QAError("TFS_TYPE_REQUIRED", "Give a work item type or a category.")
    return {"ok": True, **rt.tfs.create(type_name, body.fields)}


@router.patch("/work-items/{item_id}")
def update_work_item(item_id: int, body: WorkItemIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.tfs.update(item_id, body.fields)}


@router.get("/test-plans")
def test_plans(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "plans": rt.tfs.test_plans()}


@router.get("/test-plans/{plan_id}/suites")
def test_suites(plan_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "suites": rt.tfs.test_suites(plan_id)}


@router.get("/test-plans/{plan_id}/suites/{suite_id}/test-cases")
def suite_test_cases(plan_id: int, suite_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "test_cases": rt.tfs.suite_test_cases(plan_id, suite_id)}


@router.post("/cache/clear")
def clear_cache(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.tfs.invalidate()
    return {"ok": True}
