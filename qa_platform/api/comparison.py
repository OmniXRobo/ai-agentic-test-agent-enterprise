"""Application Data Comparison Agent API (final patch)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from ..errors import QAError
from ..runtime import Runtime
from .deps import runtime

router = APIRouter(prefix="/api/comparison")
MAX_UPLOAD = 50 * 1024 * 1024


def _svc(rt: Runtime) -> Any:
    if getattr(rt, "comparison", None) is None:
        raise QAError("CMP_UNAVAILABLE", "The Data Comparison Agent is not available.", status=503)
    return rt.comparison


async def _body(request: Request, limit: int = MAX_UPLOAD) -> bytes:
    length = int(request.headers.get("content-length") or 0)
    if length > limit:
        raise QAError("CMP_UPLOAD_TOO_LARGE", f"Uploads can be at most {limit // 1048576} MB.", status=413)
    data = b""
    async for chunk in request.stream():
        data += chunk
        if len(data) > limit:
            raise QAError("CMP_UPLOAD_TOO_LARGE", f"Uploads can be at most {limit // 1048576} MB.", status=413)
    return data


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    members: list[str] = Field(default_factory=list, max_length=100)
    settings: dict[str, Any] = Field(default_factory=dict)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    members: list[str] | None = Field(default=None, max_length=100)
    settings: dict[str, Any] | None = None
    archived: bool | None = None


class AppIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    app_type: Literal["web", "desktop", "rdp", "screenshot", "file"]
    base_url: str | None = Field(default=None, max_length=500)
    login_url: str | None = Field(default=None, max_length=500)
    auth_method: Literal["none", "form", "basic", "windows", "ntlm", "manual"] = "none"
    username: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, max_length=500)
    environment: str | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=1000)
    allowed_hosts: list[str] | None = Field(default=None, max_length=20)
    username_selector: str | None = Field(default=None, max_length=300)
    password_selector: str | None = Field(default=None, max_length=300)
    submit_selector: str | None = Field(default=None, max_length=300)
    process: str | None = Field(default=None, max_length=120)
    title_pattern: str | None = Field(default=None, max_length=200)
    timeout_ms: int | None = Field(default=None, ge=1000, le=300000)


class Step(BaseModel):
    action: Literal["goto", "click", "fill", "select", "check", "press", "wait", "wait_for", "tab"]
    selector: str | None = Field(default=None, max_length=500)
    text: str | None = Field(default=None, max_length=200)
    label: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=500)
    value: Any = None
    by: str | None = None
    key: str | None = Field(default=None, max_length=40)
    seconds: float | None = Field(default=None, ge=0, le=30)
    filter: str | None = Field(default=None, max_length=120)
    exact: bool | None = None


class DiscoverIn(BaseModel):
    steps: list[Step] = Field(default_factory=list, max_length=100)
    name: str | None = Field(default=None, max_length=120)


class ExtractIn(BaseModel):
    kind: Literal["table", "kpis", "charts", "dashboard"] = "table"
    steps: list[Step] = Field(default_factory=list, max_length=100)
    target: dict[str, Any] = Field(default_factory=dict)
    key_field: str | None = Field(default=None, max_length=200)
    screen_id: str | None = None
    screen_name: str | None = Field(default=None, max_length=120)
    screenshots: bool = True
    table_index: int | None = Field(default=None, ge=0, le=100)


class MappingSuggestIn(BaseModel):
    source_dataset: str
    target_dataset: str
    use_ai: bool = False


class MappingIn(BaseModel):
    source_app: str
    target_app: str
    mappings: list[dict[str, Any]] = Field(max_length=500)
    key_fields: dict[str, str] = Field(default_factory=dict)
    filter_map: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    note: str | None = Field(default=None, max_length=500)


class DefinitionIn(BaseModel):
    definition: dict[str, Any]


class ApproveIn(BaseModel):
    approved: bool = True


class RunIn(BaseModel):
    source_dataset: str
    target_dataset: str
    mode: Literal["record", "aggregate", "hybrid"] = "record"
    definitions: list[str] | None = None
    mappings: list[dict[str, Any]] | None = None
    key_fields: dict[str, str] | None = None
    filter_map: list[dict[str, Any]] | None = None
    use_ai: bool = False


class DefectPatch(BaseModel):
    status: str | None = None
    comment: str | None = Field(default=None, max_length=4000)
    severity: int | None = Field(default=None, ge=1, le=4)
    priority: int | None = Field(default=None, ge=1, le=4)
    title: str | None = Field(default=None, max_length=250)


class TfsIn(BaseModel):
    area_path: str | None = None
    iteration_path: str | None = None
    assigned_to: str | None = None
    force: bool = False


class AggregatesIn(BaseModel):
    kpis: list[dict[str, Any]] | None = None
    charts: list[dict[str, Any]] | None = None


class FiltersIn(BaseModel):
    filters: dict[str, Any]


class AnnotateIn(BaseModel):
    boxes: list[dict[str, Any]] = Field(max_length=200)


# ---- projects ----------------------------------------------------------------------------------------------------

@router.get("/projects")
def projects(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": _svc(rt).projects()}


@router.post("/projects")
def create_project(body: ProjectIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "project": _svc(rt).create_project(body.name, body.description, body.members, body.settings)}


@router.get("/projects/{pid}")
def project(pid: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    s = _svc(rt)
    return {"ok": True, "project": s.project(pid), "applications": s.applications(pid), "datasets": s.datasets(pid),
            "definitions": s.definitions(pid), "executions": s.executions(pid)}


@router.patch("/projects/{pid}")
def update_project(pid: str, body: ProjectPatch, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "project": _svc(rt).update_project(pid, **body.model_dump())}


@router.get("/projects/{pid}/dashboard")
def dashboard(pid: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **_svc(rt).dashboard(pid)}


@router.get("/projects/{pid}/audit")
def audit(pid: str, limit: int = Query(500, ge=1, le=5000), rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    s = _svc(rt)
    return {"ok": True, "items": s.audit_trail(pid, limit), "chain": s.verify_audit()}


# ---- applications ------------------------------------------------------------------------------------------------

@router.post("/projects/{pid}/applications")
def add_app(pid: str, body: AppIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "application": _svc(rt).save_application(pid, body.model_dump())}


@router.put("/applications/{aid}")
def save_app(aid: str, body: AppIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    s = _svc(rt)
    pid = s._app_row(aid)["project_id"]
    return {"ok": True, "application": s.save_application(pid, body.model_dump(), aid)}


@router.delete("/applications/{aid}")
def delete_app(aid: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    _svc(rt).delete_application(aid)
    return {"ok": True}


@router.post("/applications/{aid}/test")
def test_app(aid: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **_svc(rt).test_connection(aid)}


@router.post("/applications/{aid}/discover")
def discover(aid: str, body: DiscoverIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "screen": _svc(rt).discover(aid, [s.model_dump(exclude_none=True) for s in body.steps],
                                                    body.name)}


@router.get("/applications/{aid}/screens")
def screens(aid: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": _svc(rt).screens(aid)}


@router.get("/windows")
def windows(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    from ..comparison import desktop

    if not desktop.available():
        return {"ok": True, "available": False, "items": []}
    return {"ok": True, "available": True, "items": desktop.windows()}


@router.get("/ocr")
def ocr_engines(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    from ..comparison import ocr

    eng = ocr.engines(rt.settings)
    return {"ok": True, "windows": eng["windows"], "tesseract": bool(eng["tesseract"])}


# ---- datasets ----------------------------------------------------------------------------------------------------

@router.post("/applications/{aid}/extract")
def extract(aid: str, body: ExtractIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    spec = body.model_dump()
    spec["steps"] = [s.model_dump(exclude_none=True) for s in body.steps]
    return {"ok": True, **_svc(rt).start_extraction(aid, spec)}


@router.get("/jobs/{job}")
def job(job: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    j = _svc(rt).jobs.get(job)
    if j is None:
        raise QAError("CMP_JOB_NOT_FOUND", "Job not found.", status=404)
    return {"ok": True, **j}


@router.post("/jobs/{job}/cancel")
def cancel(job: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    _svc(rt).cancel(job)
    return {"ok": True}


@router.post("/applications/{aid}/screenshot")
async def screenshot(aid: str, request: Request, name: str = Query("screenshot.png", max_length=200),
                     kind: Literal["table", "kpis"] = "table", key_field: str | None = None,
                     dataset_id: str | None = None, sequence: int | None = Query(None, ge=1, le=10000),
                     rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    data = await _body(request, 15 * 1024 * 1024)
    return {"ok": True, **_svc(rt).upload_screenshot(aid, data, {"kind": kind, "key_field": key_field,
                                                                 "sequence": sequence}, name, dataset_id)}


@router.post("/applications/{aid}/import")
async def import_file(aid: str, request: Request, name: str = Query(..., max_length=200),
                      key_field: str | None = None, sheet: str | None = None,
                      rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    data = await _body(request)
    return {"ok": True, "dataset": _svc(rt).import_file(aid, name, data, {"key_field": key_field, "sheet": sheet})}


@router.get("/datasets/{ds}")
def dataset(ds: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "dataset": _svc(rt).dataset(ds)}


@router.get("/datasets/{ds}/records")
def records(ds: str, page: int = Query(1, ge=1), size: int = Query(100, ge=1, le=1000),
            q: str | None = Query(None, max_length=80), rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **_svc(rt).records_page(ds, page, size, q)}


@router.put("/datasets/{ds}/aggregates")
def set_aggregates(ds: str, body: AggregatesIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "dataset": _svc(rt).set_dataset_aggregates(ds, body.model_dump(exclude_none=True))}


@router.put("/datasets/{ds}/filters")
def set_filters(ds: str, body: FiltersIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "dataset": _svc(rt).set_dataset_filters(ds, body.filters)}


# ---- evidence ----------------------------------------------------------------------------------------------------

@router.get("/projects/{pid}/evidence")
def evidence(pid: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": _svc(rt).evidence_list(pid)}


@router.get("/evidence/{eid}")
def evidence_file(eid: str, annotated: bool = False, rt: Runtime = Depends(runtime)) -> FileResponse:
    path = _svc(rt).evidence_file(eid, annotated)
    media = {".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml"}[path.suffix]
    return FileResponse(path, media_type=media, headers={"Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'",
                                                         "X-Content-Type-Options": "nosniff"})


@router.post("/evidence/{eid}/annotate")
def annotate(eid: str, body: AnnotateIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "file": _svc(rt).annotate(eid, body.boxes)}


# ---- mapping -----------------------------------------------------------------------------------------------------

@router.post("/mapping/suggest")
def suggest(body: MappingSuggestIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **_svc(rt).suggest_mapping(body.source_dataset, body.target_dataset, body.use_ai)}


@router.put("/projects/{pid}/mapping")
def save_mapping(pid: str, body: MappingIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "mapping": _svc(rt).save_mapping(pid, body.source_app, body.target_app, body.mappings,
                                                         body.key_fields, body.filter_map, body.note)}


@router.get("/projects/{pid}/mapping")
def get_mapping(pid: str, source_app: str, target_app: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    s = _svc(rt)
    s.project(pid)
    return {"ok": True, "mapping": s.latest_mapping(pid, source_app, target_app), "history": s.mapping_history(pid)}


# ---- aggregate definitions ---------------------------------------------------------------------------------------

@router.get("/projects/{pid}/definitions")
def definitions(pid: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": _svc(rt).definitions(pid)}


@router.post("/projects/{pid}/definitions")
def add_definition(pid: str, body: DefinitionIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "definition": _svc(rt).save_definition(pid, body.definition)}


@router.put("/projects/{pid}/definitions/{did}")
def save_definition(pid: str, did: str, body: DefinitionIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "definition": _svc(rt).save_definition(pid, body.definition, did)}


@router.post("/projects/{pid}/definitions/{did}/approve")
def approve_definition(pid: str, did: str, body: ApproveIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "definition": _svc(rt).approve_definition(pid, did, body.approved)}


@router.post("/definitions/suggest")
def suggest_definitions(body: MappingSuggestIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": _svc(rt).suggest_definitions(body.source_dataset, body.target_dataset)}


# ---- executions and reports --------------------------------------------------------------------------------------

@router.post("/projects/{pid}/run")
def run(pid: str, body: RunIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "execution": _svc(rt).run(pid, body.model_dump())}


@router.get("/executions/{exe}")
def execution(exe: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "execution": _svc(rt).execution(exe)}


@router.get("/executions/{exe}/report.html")
def report_html(exe: str, rt: Runtime = Depends(runtime)) -> Response:
    return Response(_svc(rt).report_html(exe), media_type="text/html",
                    headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:"})


@router.get("/executions/{exe}/report.pdf")
def report_pdf(exe: str, rt: Runtime = Depends(runtime)) -> FileResponse:
    path = _svc(rt).report_pdf(exe)
    return FileResponse(path, media_type="application/pdf", filename=f"comparison-{exe}.pdf")


@router.get("/executions/{exe}/report.xlsx")
def report_xlsx(exe: str, rt: Runtime = Depends(runtime)) -> Response:
    return Response(_svc(rt).report_xlsx(exe),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="comparison-{exe}.xlsx"'})


@router.get("/executions/{exe}/evidence.zip")
def evidence_zip(exe: str, rt: Runtime = Depends(runtime)) -> Response:
    return Response(_svc(rt).evidence_package(exe), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="comparison-{exe}-evidence.zip"'})


# ---- defect review center ----------------------------------------------------------------------------------------

@router.get("/projects/{pid}/defects")
def defects(pid: str, status: str | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": _svc(rt).defects(pid, status)}


@router.get("/defects/{did}")
def defect(did: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "defect": _svc(rt).defect(did)}


@router.patch("/defects/{did}")
def update_defect(did: str, body: DefectPatch, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "defect": _svc(rt).update_defect(did, **body.model_dump())}


@router.post("/defects/{did}/investigate")
def investigate(did: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "defect": _svc(rt).investigate(did)}


@router.post("/defects/{did}/tfs")
def create_tfs(did: str, body: TfsIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "defect": _svc(rt).create_in_tfs(did, body.area_path, body.iteration_path, body.assigned_to,
                                                         body.force)}


@router.post("/tfs-sync")
def tfs_sync(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **_svc(rt).sync_queue()}
