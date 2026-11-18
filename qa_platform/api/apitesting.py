"""23.12.0: API Testing (Automation Studio > API Testing): /api/apitest/...

Same sign-in and roles as the rest of SkyGuard: view-only accounts can read; changes need Admin or Tester (refused
centrally and again in the service). Secrets are accepted only by the credential endpoint and are never returned.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from ..errors import QAError
from ..runtime import Runtime
from .deps import runtime

router = APIRouter(prefix="/api/apitest")
MAX_UPLOAD = 8 * 1024 * 1024


def svc(rt: Runtime = Depends(runtime)) -> Any:
    s = getattr(rt, "apitesting", None)
    if s is None:
        raise QAError("API_TESTING_UNAVAILABLE", "API Testing did not start on this PC (see the application log).",
                      status=503)
    return s


class AppIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    environment: str | None = Field(default=None, max_length=20)
    description: str | None = Field(default=None, max_length=2000)
    api_type: str | None = Field(default=None, max_length=40)
    config: dict[str, Any] | None = None
    targets: list[str] | None = Field(default=None, max_length=20)
    authorized: bool = False


class SecretIn(BaseModel):
    field: str = Field(max_length=120)
    value: str | None = Field(default=None, max_length=20000)
    role: str | None = Field(default=None, max_length=60)


class SpecIn(BaseModel):
    files: dict[str, str] | None = None
    main: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=2000)
    generate: bool = True
    options: dict[str, Any] | None = None


class ImportIn(BaseModel):
    kind: str = Field(max_length=20)
    text: str = Field(max_length=MAX_UPLOAD)


class TestIn(BaseModel):
    name: str | None = Field(default=None, max_length=250)
    method: str | None = Field(default=None, max_length=10)
    path: str | None = Field(default=None, max_length=2000)
    path_params: dict[str, Any] | None = None
    query: list[dict[str, Any]] | None = None
    headers: list[dict[str, Any]] | None = None
    body: Any = None
    content_type: str | None = Field(default=None, max_length=200)
    auth: str | None = Field(default=None, max_length=80)
    assertions: list[dict[str, Any]] | None = None
    extract: list[dict[str, Any]] | None = None
    data_rows: list[dict[str, Any]] | None = Field(default=None, max_length=500)
    timeout_ms: int | None = Field(default=None, ge=100, le=600000)
    description: str | None = Field(default=None, max_length=4000)
    status: str | None = Field(default=None, max_length=20)
    enabled: bool | None = None
    note: str | None = Field(default=None, max_length=300)


class SuiteIn(BaseModel):
    name: str = Field(max_length=120)
    test_ids: list[str] = Field(max_length=2000)
    chain: bool = False
    parallel: int = Field(default=1, ge=1, le=4)


class RunIn(BaseModel):
    test_ids: list[str] | None = Field(default=None, max_length=2000)
    suite_id: str | None = Field(default=None, max_length=40)
    confirm_state_changes: bool = False
    parallel: int | None = Field(default=None, ge=1, le=4)
    mode: str = Field(default="api", max_length=10)


class ScriptIn(BaseModel):
    source: str = Field(max_length=400_000)
    note: str | None = Field(default=None, max_length=300)


class GenScriptIn(BaseModel):
    test_ids: list[str] | None = None
    suite_id: str | None = None


class SuggestIn(BaseModel):
    behaviour: str = Field(min_length=5, max_length=3000)


# ---- applications ------------------------------------------------------------------------------------------------
@router.get("/apps")
def apps(s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "apps": s.apps()}


@router.post("/apps", status_code=201)
def create_app(body: AppIn, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "app": s.save_app(body.model_dump(exclude_none=True))}


@router.get("/apps/{app_id}")
def get_app(app_id: str, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "app": s.app(app_id), "suites": s.suites(app_id)}


@router.put("/apps/{app_id}")
def update_app(app_id: str, body: AppIn, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "app": s.save_app(body.model_dump(exclude_none=True), app_id)}


@router.delete("/apps/{app_id}")
def delete_app(app_id: str, s: Any = Depends(svc)) -> dict[str, Any]:
    s.delete_app(app_id)
    return {"ok": True}


@router.post("/apps/{app_id}/credentials")
def set_secret(app_id: str, body: SecretIn, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "app": s.set_secret(app_id, body.field, body.value, body.role)}


@router.post("/apps/{app_id}/certificates")
async def upload_cert(app_id: str, kind: str = Form(...), file: UploadFile = File(...), s: Any = Depends(svc)) -> dict[str, Any]:
    data = await file.read(1024 * 1024 + 1)
    return {"ok": True, "app": await run_in_threadpool(s.upload_certificate, app_id, kind, file.filename or "", data)}


@router.post("/apps/{app_id}/test-connection")
async def test_connection(app_id: str, path: str | None = None, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, **await run_in_threadpool(s.test_connection, app_id, path)}


@router.post("/apps/{app_id}/spec")
async def import_spec(app_id: str, body: SpecIn, s: Any = Depends(svc)) -> dict[str, Any]:
    if body.url:
        out = await run_in_threadpool(lambda: s.import_spec_url(app_id, body.url, generate=body.generate,
                                                                options=body.options))
    else:
        if not body.files or sum(len(v) for v in body.files.values()) > MAX_UPLOAD:
            raise QAError("API_SPEC_MISSING", "Upload the specification file(s), at most 8 MB together.", status=422)
        out = await run_in_threadpool(lambda: s.import_spec(app_id, body.files, body.main, generate=body.generate,
                                                            options=body.options))
    return {"ok": True, **out}


@router.post("/apps/{app_id}/generate")
async def generate(app_id: str, body: dict[str, Any] | None = None, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, **await run_in_threadpool(s.generate_tests, app_id, (body or {}).get("options"))}


@router.post("/apps/{app_id}/ai-suggest")
async def ai_suggest(app_id: str, body: SuggestIn, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, **await run_in_threadpool(s.ai_suggest, app_id, body.behaviour)}


@router.post("/apps/{app_id}/import")
async def import_other(app_id: str, body: ImportIn, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, **await run_in_threadpool(s.import_other, app_id, body.kind, body.text)}


@router.get("/apps/{app_id}/export")
def export(app_id: str, s: Any = Depends(svc)) -> JSONResponse:
    return JSONResponse(s.export_tests(app_id), headers={"Content-Disposition": f'attachment; filename="{app_id}-api-tests.json"'})


@router.get("/apps/{app_id}/audit")
def audit(app_id: str, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "audit": s.audit_log(app_id)}


# ---- tests and suites ----------------------------------------------------------------------------------------------
@router.get("/apps/{app_id}/tests")
def tests(app_id: str, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "tests": s.tests(app_id), "suites": s.suites(app_id)}


@router.post("/apps/{app_id}/tests", status_code=201)
def create_test(app_id: str, body: TestIn, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "test": s.create_test(app_id, body.model_dump(exclude_none=True, exclude={"note"}))}


@router.post("/tests/validate")
def validate_test(body: TestIn, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, **s.validate_test(body.model_dump(exclude_none=True))}


@router.get("/tests/{test_id}")
def get_test(test_id: str, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "test": s.test(test_id)}


@router.put("/tests/{test_id}")
def update_test(test_id: str, body: TestIn, s: Any = Depends(svc)) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=True, exclude={"note"})
    return {"ok": True, "test": s.update_test(test_id, data, body.note or "")}


@router.post("/tests/{test_id}/approve")
def approve_test(test_id: str, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "test": s.approve_test(test_id)}


@router.post("/tests/{test_id}/restore/{version}")
def restore_test(test_id: str, version: int, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "test": s.restore_test(test_id, version)}


@router.delete("/tests/{test_id}")
def delete_test(test_id: str, s: Any = Depends(svc)) -> dict[str, Any]:
    s.delete_test(test_id)
    return {"ok": True}


@router.post("/apps/{app_id}/suites", status_code=201)
def create_suite(app_id: str, body: SuiteIn, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "suite": s.save_suite(app_id, body.model_dump())}


@router.put("/suites/{suite_id}")
def update_suite(suite_id: str, body: SuiteIn, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "suite": s.save_suite(s.suite(suite_id)["app_id"], body.model_dump(), suite_id)}


@router.delete("/suites/{suite_id}")
def delete_suite(suite_id: str, s: Any = Depends(svc)) -> dict[str, Any]:
    s.delete_suite(suite_id)
    return {"ok": True}


# ---- runs --------------------------------------------------------------------------------------------------------
@router.post("/apps/{app_id}/run")
async def run(app_id: str, body: RunIn, s: Any = Depends(svc)) -> dict[str, Any]:
    out = await run_in_threadpool(lambda: s.run(app_id, test_ids=body.test_ids, suite_id=body.suite_id,
                                                confirm_state_changes=body.confirm_state_changes,
                                                parallel=body.parallel, mode=body.mode))
    return {"ok": True, **out}


@router.get("/runs")
def runs(app_id: str | None = None, limit: int = 50, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "runs": s.runs(app_id, max(1, min(limit, 500)))}


@router.get("/runs/{run_id}")
def run_detail(run_id: str, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "run": s.run_detail(run_id)}


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: str, s: Any = Depends(svc)) -> dict[str, Any]:
    return s.cancel(run_id)


@router.get("/runs/{run_id}/report.html")
def report(run_id: str, s: Any = Depends(svc)) -> HTMLResponse:
    return HTMLResponse(s.report_html(run_id), headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


@router.get("/results/{result_id}")
def result(result_id: int, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "result": s.result(result_id)}


@router.post("/results/{result_id}/rerun")
async def rerun(result_id: int, body: RunIn | None = None, s: Any = Depends(svc)) -> dict[str, Any]:
    out = await run_in_threadpool(s.rerun_result, result_id, bool(body and body.confirm_state_changes))
    return {"ok": True, **out}


# ---- Python script --------------------------------------------------------------------------------------------------
@router.get("/apps/{app_id}/script")
def script(app_id: str, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, **s.script(app_id)}


@router.post("/apps/{app_id}/script/generate")
def gen_script(app_id: str, body: GenScriptIn | None = None, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, **s.generate_script(app_id, (body or GenScriptIn()).test_ids, (body or GenScriptIn()).suite_id)}


@router.post("/scripts/validate")
def validate_script(body: ScriptIn) -> dict[str, Any]:
    from ..apitesting import scriptgen

    return {"ok": True, **scriptgen.validate(body.source)}


@router.put("/apps/{app_id}/script")
def save_script(app_id: str, body: ScriptIn, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, **s.save_script(app_id, body.source, body.note or "")}


@router.post("/apps/{app_id}/script/restore/{version}")
def restore_script(app_id: str, version: int, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, **s.restore_script(app_id, version)}


@router.post("/apps/{app_id}/script/run")
async def run_script(app_id: str, version: int | None = None, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "run": await run_in_threadpool(s.run_script, app_id, version)}


@router.get("/apps/{app_id}/script/download")
def download_script(app_id: str, version: int | None = None, s: Any = Depends(svc)) -> Any:
    from fastapi.responses import PlainTextResponse

    info = s.script(app_id)
    row = s._script_row(app_id, version)
    if not row:
        raise QAError("API_SCRIPT_MISSING", "Generate the Python script first.", status=404)
    return PlainTextResponse(row["source"], media_type="text/x-python",
                             headers={"Content-Disposition": f'attachment; filename="{info["file_name"]}"'})
