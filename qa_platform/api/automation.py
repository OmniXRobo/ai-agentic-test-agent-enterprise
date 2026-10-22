"""Automation Studio API: scripts, execution, healing, recording, discovery, AI generation, credentials."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from ..automation import pytest_export
from ..automation.dsl import Script, Step, new_script_id, to_playwright_python
from ..clock import now_iso
from ..errors import QAError
from ..runtime import Runtime
from ..storage.db import loads
from .deps import runtime

router = APIRouter(prefix="/api/automation")


def _url(value: str | None, field: str = "URL") -> str:
    value = (value or "").strip()
    if not value.lower().startswith(("http://", "https://")):
        raise QAError("URL_INVALID", f"The {field} must start with http:// or https://.")
    return value


# ---- scripts ---------------------------------------------------------------------------


class ScriptIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    mode: str = "manual"
    base_url: str | None = None
    test_case_id: str | None = None
    user_story_id: str | None = None
    application: str | None = None
    credential_role: str | None = None
    browser: str = "chromium"
    steps: list[Step] = []
    notes: str | None = None


@router.get("/scripts")
def list_scripts(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.automation.scripts.list()}


@router.post("/scripts", status_code=201)
def create_script(body: ScriptIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    script = Script.model_validate({**body.model_dump(), "id": new_script_id()})
    return {"ok": True, "script": rt.automation.scripts.save(script, reason="created").model_dump()}


def _datasets_for(rt: Runtime, script: Script) -> list[dict[str, Any]]:
    if not script.test_case_id or not str(script.test_case_id).isdigit():
        return []
    rows = rt.design.list_test_data(tfs_test_case_id=int(script.test_case_id))
    return [{"name": r["name"], "values": r["dataset"]} for r in reversed(rows) if isinstance(r["dataset"], dict)]


@router.get("/scripts/{script_id}")
def get_script(script_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    script = rt.automation.scripts.get(script_id)
    return {"ok": True, "script": script.model_dump(), "python": pytest_export.to_pytest(script, _datasets_for(rt, script)),
            "python_file": pytest_export.file_name(script), "versions": rt.automation.scripts.versions(script_id)}


@router.put("/scripts/{script_id}")
def update_script(script_id: str, body: ScriptIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    current = rt.automation.scripts.get(script_id)
    script = Script.model_validate({**body.model_dump(), "id": script_id, "version": current.version,
                                    "created_at": current.created_at})
    return {"ok": True, "script": rt.automation.scripts.save(script, reason="edited").model_dump()}


@router.delete("/scripts/{script_id}")
def delete_script(script_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.automation.scripts.delete(script_id)
    return {"ok": True, "archived": True}


@router.post("/scripts/{script_id}/restore/{version}")
def restore_script(script_id: str, version: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "script": rt.automation.scripts.restore(script_id, version).model_dump()}


@router.get("/scripts/{script_id}/export", response_class=PlainTextResponse)
def export_script(script_id: str, format: str = Query("pytest", pattern="^(pytest|playwright)$"),
                  rt: Runtime = Depends(runtime)) -> PlainTextResponse:
    """``pytest``: standalone pytest + Playwright test with the test data; ``playwright``: plain Playwright script."""
    script = rt.automation.scripts.get(script_id)
    if format == "playwright":
        return PlainTextResponse(to_playwright_python(script), headers={
            "Content-Disposition": f'attachment; filename="{script_id}.py"'})
    return PlainTextResponse(pytest_export.to_pytest(script, _datasets_for(rt, script)), headers={
        "Content-Disposition": f'attachment; filename="{pytest_export.file_name(script)}"'})


class BundleIn(BaseModel):
    script_ids: list[str] | None = Field(default=None, max_length=500)
    user_story_id: str | None = None
    test_case_ids: list[str] | None = Field(default=None, max_length=500)


@router.post("/export-bundle")
def export_bundle(body: BundleIn, rt: Runtime = Depends(runtime)) -> Response:
    """A zip of pytest files (one per script) with pytest.ini, requirements.txt, README.txt and RUN_TESTS.cmd."""
    if body.script_ids:
        scripts = [rt.automation.scripts.get(sid) for sid in body.script_ids]
    else:
        wanted_tcs = {str(t) for t in body.test_case_ids or []}
        scripts = []
        for item in rt.automation.scripts.list():
            if (body.user_story_id and str(item.get("user_story_id") or "") == str(body.user_story_id)) or \
                    (wanted_tcs and str(item.get("test_case_id") or "") in wanted_tcs):
                scripts.append(rt.automation.scripts.get(item["id"]))
    if not scripts:
        raise QAError("NO_SCRIPTS", "No automation scripts match. Create the automation first.", status=404)
    data = pytest_export.bundle([(s, _datasets_for(rt, s)) for s in scripts], now_iso())
    name = f"automated-tests-US{body.user_story_id}.zip" if body.user_story_id else "automated-tests.zip"
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---- execution ---------------------------------------------------------------------------


class RunIn(BaseModel):
    test_data_id: int | None = None
    headless: bool | None = None


class RunManyIn(BaseModel):
    script_ids: list[str] = Field(min_length=1, max_length=200)
    headless: bool | None = None


def _dataset(rt: Runtime, data_id: int | None) -> dict[str, Any] | None:
    if data_id is None:
        return None
    row = rt.db.query_one("SELECT dataset FROM test_data WHERE id = ?", (data_id,))
    if row is None:
        raise QAError("TEST_DATA_NOT_FOUND", f"Test data {data_id} does not exist.", status=404)
    values = loads(row["dataset"])
    return values if isinstance(values, dict) else None


@router.post("/scripts/{script_id}/run", status_code=202)
def run_script(script_id: str, body: RunIn | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    body = body or RunIn()
    run_id = rt.automation.run_script(script_id, dataset=_dataset(rt, body.test_data_id), headless=body.headless)
    return {"ok": True, "run_id": run_id}


@router.post("/run-many", status_code=202)
def run_many(body: RunManyIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    for sid in body.script_ids:
        rt.automation.scripts.get(sid)
    return {"ok": True, "run_ids": rt.automation.run_many(body.script_ids, headless=body.headless)}


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    if not rt.automation.runner.stop(run_id):
        raise QAError("RUN_NOT_ACTIVE", f"Run {run_id} is not executing.", status=409, run_id=run_id)
    return {"ok": True}


@router.get("/results")
def results(run_id: str | None = None, test_case_id: str | None = None, script_id: str | None = None,
            limit: int = Query(100, ge=1, le=1000), rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.automation.runner.results(run_id, test_case_id, limit, script_id),
            "running": rt.automation.runner.running()}


@router.get("/evidence")
def evidence(path: str, rt: Runtime = Depends(runtime)) -> FileResponse:
    root = rt.paths.evidence.resolve()
    target = Path(path).resolve()
    if root not in target.parents or not target.is_file():
        raise QAError("EVIDENCE_NOT_FOUND", "That evidence file is not available.", status=404)
    return FileResponse(target)


# ---- self-healing ------------------------------------------------------------------------


@router.get("/healing")
def healing(script_id: str | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.automation.runner.healing_events(script_id)}


@router.post("/healing/{healing_id}/apply")
def apply_healing(healing_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "script": rt.automation.runner.apply_healing(healing_id).model_dump()}


# ---- recording ------------------------------------------------------------------------------


class RecordIn(BaseModel):
    start_url: str
    name: str | None = None
    append_to: str | None = None
    insert_at: int | None = Field(default=None, ge=0)
    browser: str = "chromium"
    headless: bool = False
    credential_role: str | None = None
    test_case_id: str | None = None
    application: str | None = None


class AssertIn(BaseModel):
    selector: str = Field(min_length=1)
    text: str | None = None


@router.post("/recordings", status_code=202)
def start_recording(body: RecordIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    run_id = rt.automation.start_recording(
        _url(body.start_url, "start URL"), name=body.name, append_to=body.append_to, insert_at=body.insert_at,
        browser=body.browser, headless=body.headless, credential_role=body.credential_role,
        test_case_id=body.test_case_id, application=body.application)
    return {"ok": True, "run_id": run_id}


@router.get("/recordings/{run_id}")
def recording_status(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.automation.recording_status(run_id)}


@router.post("/recordings/{run_id}/assert")
def recording_assert(run_id: str, body: AssertIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.automation.recording(run_id)["session"].add_assertion(body.selector, body.text)
    return {"ok": True, **rt.automation.recording_status(run_id)}


@router.post("/recordings/{run_id}/stop")
def stop_recording(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "script": rt.automation.stop_recording(run_id).model_dump()}


# ---- discovery and AI generation ---------------------------------------------------------


class DiscoveryIn(BaseModel):
    base_url: str
    max_pages: int = Field(default=15, ge=1, le=200)
    max_depth: int = Field(default=3, ge=0, le=10)
    headless: bool = True


class FromDiscoveryIn(BaseModel):
    page_url: str
    selectors: list[str] = Field(min_length=1)
    name: str = ""


class GenerateIn(BaseModel):
    test_case_id: int | None = None
    user_story_id: int | None = Field(default=None, description="Create automation for every linked Test Case")
    base_url: str
    credential_role: str | None = None
    application: str | None = None
    discovery_id: str | None = None
    headless: bool = True


@router.post("/discoveries", status_code=202)
def start_discovery(body: DiscoveryIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    run_id = rt.automation.start_discovery(_url(body.base_url, "application URL"), body.max_pages, body.max_depth,
                                           body.headless)
    return {"ok": True, "run_id": run_id}


@router.get("/discoveries")
def list_discoveries(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.automation.discovery.list()}


@router.get("/discoveries/{discovery_id}")
def get_discovery(discovery_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "discovery": rt.automation.discovery.get(discovery_id)}


@router.post("/discoveries/{discovery_id}/scripts", status_code=201)
def script_from_discovery(discovery_id: str, body: FromDiscoveryIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    script = rt.automation.create_from_discovery(discovery_id, body.page_url, body.selectors, body.name)
    return {"ok": True, "script": script.model_dump()}


@router.post("/generate", status_code=202)
def generate(body: GenerateIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    if (body.test_case_id is None) == (body.user_story_id is None):
        raise QAError("GENERATE_TARGET", "Give a Test Case ID or a User Story ID.")
    if body.user_story_id is not None:
        run_id, ids = rt.automation.start_story_generation(
            body.user_story_id, _url(body.base_url, "application URL"), credential_role=body.credential_role,
            application=body.application, discovery_id=body.discovery_id, headless=body.headless)
        return {"ok": True, "run_id": run_id, "test_case_ids": ids}
    run_id = rt.automation.start_generation(body.test_case_id, _url(body.base_url, "application URL"),
                                            credential_role=body.credential_role, application=body.application,
                                            discovery_id=body.discovery_id, headless=body.headless)
    return {"ok": True, "run_id": run_id}


@router.get("/generate/{run_id}")
def generation_status(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    run = rt.runs.get(run_id)
    many = rt.automation.generated_many.get(run_id)
    return {"ok": True, "script_id": rt.automation.generated.get(run_id), "script_ids": many,
            "status": run["status"] if run else None}


# ---- application credentials ---------------------------------------------------------------


class CredentialIn(BaseModel):
    application: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=60)
    username: str = Field(min_length=1, max_length=200)
    password: str | None = None
    login_url: str | None = None


@router.get("/credentials")
def credentials(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.automation.creds.list(), "store_available": rt.secrets.available}


@router.put("/credentials")
def save_credential(body: CredentialIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    if body.password and not rt.secrets.available:
        raise QAError("CREDENTIAL_STORE_UNAVAILABLE", "The Windows Credential Manager is not available.", status=503)
    item = rt.automation.creds.save(body.application, body.role, body.username, body.password, body.login_url)
    return {"ok": True, "item": item}


@router.delete("/credentials/{cid}")
def delete_credential(cid: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.automation.creds.delete(cid)
    return {"ok": True}
