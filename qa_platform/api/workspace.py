"""Integration Comparison Workspace API (23.8.0). One workspace: applications, analysis, mapping, run, results."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from ..errors import QAError
from ..runtime import Runtime
from .comparison import _body
from .deps import runtime

router = APIRouter(prefix="/api/comparison/workspace")


def _ws(rt: Runtime) -> Any:
    svc = getattr(rt, "comparison", None)
    if svc is None:
        raise QAError("CMP_UNAVAILABLE", "The integration comparison is not available.", status=503)
    ws = getattr(svc, "workspace", None)
    if ws is None:
        from ..comparison.workspace import Workspace

        ws = svc.workspace = Workspace(svc)
    return ws


class CreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    applications: list[str] = Field(default_factory=lambda: ["Application A", "Application B"], min_length=0,
                                    max_length=12)


class AppIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    url: str | None = Field(default=None, max_length=500)
    login_url: str | None = Field(default=None, max_length=500)
    auth_method: Literal["none", "form", "basic", "windows", "ntlm", "manual"] | None = None
    username: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, max_length=500)
    screens_complete: bool | None = None


class CaptureIn(BaseModel):
    page_name: str | None = Field(default=None, max_length=120)
    steps: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


class AnalyzeIn(BaseModel):
    use_ai: bool = False
    reread: bool = False


class CorrectIn(BaseModel):
    evidence_id: str = Field(max_length=40)
    where: dict[str, Any]
    value: str = Field(max_length=200)


class ConfigIn(BaseModel):
    config: dict[str, Any]
    note: str | None = Field(default=None, max_length=500)


class DecisionIn(BaseModel):
    decision: Literal["valid_business_difference", "expected_calculation_difference", "false_positive",
                      "actual_defect", "investigate"]
    note: str | None = Field(default=None, max_length=1000)


@router.get("")
def comparisons(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": _ws(rt).comparisons()}


@router.post("")
def create(body: CreateIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **_ws(rt).create(body.name, body.applications)}


@router.get("/{pid}")
def get(pid: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **_ws(rt).get(pid)}


@router.post("/{pid}/applications")
def add_app(pid: str, body: AppIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "application": _ws(rt).add_app(pid, body.model_dump())}


@router.patch("/applications/{aid}")
def update_app(aid: str, body: AppIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "application": _ws(rt).update_app(aid, body.model_dump(exclude_unset=True))}


@router.delete("/applications/{aid}")
def remove_app(aid: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    _ws(rt).remove_app(aid)
    return {"ok": True}


@router.post("/applications/{aid}/screenshots")
async def screenshot(aid: str, request: Request, name: str = Query("screenshot.png", max_length=200),
                     rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    data = await _body(request, 15 * 1024 * 1024)
    return {"ok": True, "screenshot": _ws(rt).upload_screenshot(aid, data, name)}


@router.post("/applications/{aid}/file")
async def import_file(aid: str, request: Request, name: str = Query(..., max_length=200),
                      rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    data = await _body(request)
    return {"ok": True, "dataset": _ws(rt).import_file(aid, name, data)}


@router.post("/applications/{aid}/capture")
def capture(aid: str, body: CaptureIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **_ws(rt).capture(aid, body.model_dump())}


@router.delete("/applications/{aid}/inputs/{input_id}")
def remove_input(aid: str, input_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    _ws(rt).remove_input(aid, input_id)
    return {"ok": True}


@router.post("/{pid}/analyze")
def analyze(pid: str, body: AnalyzeIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "analysis": _ws(rt).analyze(pid, body.use_ai, body.reread)}


@router.post("/{pid}/correct")
def correct(pid: str, body: CorrectIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "screen": _ws(rt).correct(pid, body.evidence_id, body.where, body.value)}


@router.put("/{pid}/mapping")
def save_mapping(pid: str, body: ConfigIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "saved": _ws(rt).save_config(pid, body.config, body.note)}


@router.get("/{pid}/mapping/history")
def mapping_history(pid: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": _ws(rt).config_history(pid)}


@router.post("/{pid}/run")
def run(pid: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "run": _ws(rt).run(pid)}


@router.get("/runs/{exe}")
def get_run(exe: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "run": _ws(rt).execution(exe)}


@router.post("/runs/{exe}/rows/{row_id}/decision")
def decide(exe: str, row_id: str, body: DecisionIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "decision": _ws(rt).decide(exe, row_id, body.decision, body.note)}


@router.post("/runs/{exe}/rows/{row_id}/defect")
def defect(exe: str, row_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "defect": _ws(rt).create_defect(exe, row_id)}


@router.post("/runs/{exe}/rows/{row_id}/explain")
def explain(exe: str, row_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **_ws(rt).explain(exe, row_id)}


@router.get("/{pid}/decisions")
def decisions(pid: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    ws = _ws(rt)
    ws.svc.project(pid)
    return {"ok": True, "items": ws.decisions(pid)}


@router.get("/runs/{exe}/report.html")
def report_html(exe: str, rt: Runtime = Depends(runtime)) -> Response:
    return Response(_ws(rt).report_html(exe), media_type="text/html",
                    headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:"})


@router.get("/runs/{exe}/report.pdf")
def report_pdf(exe: str, rt: Runtime = Depends(runtime)) -> FileResponse:
    return FileResponse(_ws(rt).report_pdf(exe), media_type="application/pdf",
                        filename=f"integration-comparison-{exe}.pdf")


@router.get("/runs/{exe}/report.xlsx")
def report_xlsx(exe: str, rt: Runtime = Depends(runtime)) -> Response:
    return Response(_ws(rt).report_xlsx(exe),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="integration-comparison-{exe}.xlsx"'})
