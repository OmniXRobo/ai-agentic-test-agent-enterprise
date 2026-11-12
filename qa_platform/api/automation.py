"""Automation Studio API: scripts, execution, healing, recording, discovery, AI generation, credentials."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

import base64
import binascii

from ..automation import date_rules, pytest_export
from ..automation import files as filekit
from ..automation.dsl import DataTable, Script, Step, new_script_id, to_playwright_python
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


def _all_datasets(rt: Runtime, script: Script) -> list[dict[str, Any]]:
    """TFS test data of the Test Case, then the rows imported from Excel / CSV (23.3.17: one test per row)."""
    out = _datasets_for(rt, script)
    if script.data_table and script.data_table.rows:
        n = len(script.data_table.rows)
        out += [{"name": f"row {i + 1} of {n}", "values": row} for i, row in enumerate(script.data_table.rows)]
    return out


@router.get("/scripts/{script_id}")
def get_script(script_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    script = rt.automation.scripts.get(script_id)
    return {"ok": True, "script": script.model_dump(), "python": pytest_export.to_pytest(script, _all_datasets(rt, script)),
            "python_file": pytest_export.file_name(script), "versions": rt.automation.scripts.versions(script_id),
            "files": rt.automation.scripts.list_files(script_id),
            "date_suggestions": {str(k): v for k, v in date_rules.suggestions(script.steps).items()},
            "calendar_pairs": date_rules.calendar_pairs(script.steps),
            "converted": rt.automation.last_conversions.pop(script_id, [])}


@router.put("/scripts/{script_id}")
def update_script(script_id: str, body: ScriptIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    current = rt.automation.scripts.get(script_id)
    script = Script.model_validate({**body.model_dump(), "id": script_id, "version": current.version,
                                    "created_at": current.created_at})
    script.data_table = current.data_table  # imported test data is changed with its own endpoints (23.3.17)
    return {"ok": True, "script": rt.automation.scripts.save(script, reason="edited").model_dump()}


# ---- 23.3.17: files for upload steps -------------------------------------------------------------


class FileIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(max_length=70_000_000)


def _decode(content: str) -> bytes:
    try:
        return base64.b64decode(content.split(",", 1)[1] if content.startswith("data:") else content, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise QAError("FILE_UNREADABLE", "The file could not be read.", status=422) from exc


@router.get("/scripts/{script_id}/files")
def list_files(script_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.automation.scripts.get(script_id)
    return {"ok": True, "items": rt.automation.scripts.list_files(script_id)}


@router.post("/scripts/{script_id}/files", status_code=201)
def add_file(script_id: str, body: FileIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    saved = rt.automation.scripts.save_file(script_id, body.name, _decode(body.content_base64))
    return {"ok": True, "file": saved, "items": rt.automation.scripts.list_files(script_id)}


@router.delete("/scripts/{script_id}/files/{name}")
def delete_file(script_id: str, name: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.automation.scripts.delete_file(script_id, name)
    return {"ok": True, "items": rt.automation.scripts.list_files(script_id)}


# ---- 23.3.17: data-driven runs from Excel / CSV -----------------------------------------------------


class DataImportIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(max_length=70_000_000)
    sheet: str | None = None
    dynamic_dates: bool = True
    preview: bool = False


def _data_fields(script: Script) -> list[str]:
    import re

    found: list[str] = []
    for step in script.steps:
        for f in re.findall(r"\{\{\s*DATA:([^}]+?)\s*\}\}", step.value or ""):
            if f not in found:
                found.append(f)
    return found


@router.post("/scripts/{script_id}/data-table")
def import_data_table(script_id: str, body: DataImportIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    script = rt.automation.scripts.get(script_id)
    table = filekit.read_table(body.name, _decode(body.content_base64), body.sheet)
    converted = 0
    if body.dynamic_dates:
        for row in table["rows"]:
            converted += len(date_rules.convert_values(row))
    used = _data_fields(script)
    missing = [f for f in used if f not in table["columns"]]
    result = {"ok": True, "columns": table["columns"], "rows": table["rows"][:50], "row_count": len(table["rows"]),
              "sheet": table["sheet"], "sheets": table["sheets"], "dates_converted": converted,
              "fields_used": used, "fields_missing": missing}
    if body.preview:
        return result
    script.data_table = DataTable(source=filekit.safe_name(body.name), sheet=table["sheet"], imported_at=now_iso(),
                                  columns=table["columns"], rows=table["rows"])
    saved = rt.automation.scripts.save(script, reason=f"test data imported from {script.data_table.source} "
                                                      f"({len(table['rows'])} rows)")
    return {**result, "script": saved.model_dump()}


@router.delete("/scripts/{script_id}/data-table")
def remove_data_table(script_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    script = rt.automation.scripts.get(script_id)
    script.data_table = None
    return {"ok": True, "script": rt.automation.scripts.save(script, reason="imported test data removed").model_dump()}


class DataRunIn(BaseModel):
    rows: list[int] | None = Field(default=None, max_length=1000)  # 0-based; empty = every row
    headless: bool | None = None


@router.post("/scripts/{script_id}/data-runs", status_code=202)
def run_data_rows(script_id: str, body: DataRunIn | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    body = body or DataRunIn()
    return {"ok": True, **rt.automation.run_data_rows(script_id, body.rows, headless=body.headless)}


@router.get("/scripts/{script_id}/data-runs")
def data_runs(script_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.automation.scripts.get(script_id)
    return {"ok": True, "items": rt.automation.data_run_status(script_id)}


@router.post("/data-runs/{batch_id}/stop")
def stop_data_rows(batch_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    if not rt.automation.stop_data_rows(batch_id):
        raise QAError("RUN_NOT_ACTIVE", f"{batch_id} is not running.", status=409)
    return {"ok": True}


class RulePreviewIn(BaseModel):
    values: list[str] = Field(max_length=200)


@router.post("/date-rules/preview")
def preview_rules(body: RulePreviewIn) -> dict[str, Any]:
    """What each rule gives now on this PC's clock (the browser's own clock may differ)."""
    out = []
    for v in body.values:
        try:
            out.append({"value": v, "now": date_rules.preview(v), "label": date_rules.label(v)})
        except Exception as exc:  # e.g. a DATA field: shown as is
            out.append({"value": v, "now": v, "label": None, "error": str(exc)[:200]})
    return {"ok": True, "items": out}


@router.delete("/scripts/{script_id}")
def delete_script(script_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.automation.scripts.delete(script_id)
    return {"ok": True, "archived": True, "deleted": True, "id": script_id}


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
    return PlainTextResponse(pytest_export.to_pytest(script, _all_datasets(rt, script)), headers={
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
    data = pytest_export.bundle([(s, _all_datasets(rt, s)) for s in scripts], now_iso(),
                                files={s.id: rt.automation.scripts.files_dir(s.id) for s in scripts})
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
    dataset = _dataset(rt, body.test_data_id)
    if dataset is None:  # 23.3.17: Run without a TFS data set uses the first imported Excel / CSV row
        table = rt.automation.scripts.get(script_id).data_table
        if table and table.rows:
            dataset = dict(table.rows[0])
    run_id = rt.automation.run_script(script_id, dataset=dataset, headless=body.headless)
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


class ApplyAllIn(BaseModel):
    script_id: str | None = None
    run_id: str | None = None


@router.post("/healing/apply-all")
def apply_all_healing(body: ApplyAllIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """23.3.21: apply every healed locator whose run then passed (one script, one run, or all), newest first; one
    per step, so an older healing of the same step is left alone. Each one that cannot be applied says why."""
    where, params = ["result = 'validated'", "(applied IS NULL OR applied = 0)"], []
    if body.script_id:
        where.append("script_id = ?")
        params.append(body.script_id)
    if body.run_id:
        where.append("run_id = ?")
        params.append(body.run_id)
    rows = rt.db.query(f"SELECT id, script_id, step_index, original_selector FROM healing_events WHERE {' AND '.join(where)}"
                       " ORDER BY id DESC", params)
    applied, skipped, seen = [], [], set()
    for row in rows:
        key = (row["script_id"], row["step_index"], row["original_selector"])
        if key in seen:
            skipped.append({"id": row["id"], "step": row["step_index"] + 1, "reason": "a newer healing of this step was applied"})
            continue
        seen.add(key)
        try:
            if str(row["script_id"] or "").startswith("FWS-"):
                rt.studio.apply_healing(row["id"])
            else:
                rt.automation.runner.apply_healing(row["id"])
            applied.append({"id": row["id"], "script_id": row["script_id"], "step": row["step_index"] + 1})
        except QAError as exc:
            skipped.append({"id": row["id"], "step": row["step_index"] + 1, "reason": exc.message})
    return {"ok": True, "applied": applied, "skipped": skipped}


@router.post("/healing/{healing_id}/apply")
def apply_healing(healing_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    row = rt.db.query_one("SELECT script_id FROM healing_events WHERE id = ?", (healing_id,))
    if row and str(row["script_id"] or "").startswith("FWS-"):  # framework script: update its Page Object
        return {"ok": True, "framework": rt.studio.apply_healing(healing_id)}
    return {"ok": True, "script": rt.automation.runner.apply_healing(healing_id).model_dump()}


class ConfirmHealingIn(BaseModel):
    selector: str | None = Field(default=None, max_length=2000)  # None = reject every candidate


@router.post("/healing/{healing_id}/confirm")
def confirm_healing(healing_id: int, body: ConfirmHealingIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Final patch (defect 7): a person picks the element for a low-confidence healing, or rejects them all."""
    return {"ok": True, **rt.automation.runner.confirm_healing(healing_id, body.selector)}


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
    start_paused: bool | None = None  # 23.3.20: None = paused for Record more steps, recording for a new script
    replace: int = Field(default=0, ge=0, le=500)
    replay: bool = False


class ControlIn(BaseModel):
    action: str = Field(pattern="^(record|pause|position|pick_text|pick_visible|pick_off|dialog_ok|dialog_cancel)$")
    insert_at: int | None = Field(default=None, ge=0)
    replace: int | None = Field(default=None, ge=0, le=500)


class AssertIn(BaseModel):
    selector: str = Field(min_length=1)
    text: str | None = None


@router.post("/recordings", status_code=202)
def start_recording(body: RecordIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    run_id = rt.automation.start_recording(
        _url(body.start_url, "start URL"), name=body.name, append_to=body.append_to, insert_at=body.insert_at,
        browser=body.browser, headless=body.headless, credential_role=body.credential_role,
        test_case_id=body.test_case_id, application=body.application, start_paused=body.start_paused,
        replace=body.replace, replay=body.replay)
    return {"ok": True, "run_id": run_id}


@router.post("/recordings/{run_id}/control")
def recording_control(run_id: str, body: ControlIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """23.3.20: Record / Pause / change where the next recorded steps go."""
    rt.automation.recording_control(run_id, body.action, insert_at=body.insert_at, replace=body.replace)
    return {"ok": True, **rt.automation.recording_status(run_id)}


@router.get("/recordings/{run_id}")
def recording_status(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.automation.recording_status(run_id)}


@router.post("/recordings/{run_id}/assert")
def recording_assert(run_id: str, body: AssertIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.automation.recording(run_id)["session"].add_assertion(body.selector, body.text)
    return {"ok": True, **rt.automation.recording_status(run_id)}


@router.post("/recordings/{run_id}/stop")
def stop_recording(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    script = rt.automation.stop_recording(run_id)
    return {"ok": True, "script": script.model_dump(), "converted": rt.automation.last_conversions.get(script.id, []),
            "summary": rt.automation.last_summary.pop(script.id, None),
            "files": rt.automation.scripts.list_files(script.id)}


# ---- discovery and AI generation ---------------------------------------------------------


class DiscoveryIn(BaseModel):
    base_url: str
    max_pages: int = Field(default=15, ge=1, le=200)
    max_depth: int = Field(default=3, ge=0, le=10)
    headless: bool = True
    application: str | None = Field(default=None, max_length=120)
    credential_role: str | None = Field(default=None, max_length=60)
    environment: str | None = Field(default=None, max_length=60)
    explore: bool = True
    allow_destructive: bool = False


class DiscoveryInputIn(BaseModel):
    values: dict[str, str] = {}
    save_static: bool = False


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
    run_id = rt.automation.start_discovery(
        _url(body.base_url, "application URL"), body.max_pages, body.max_depth, body.headless,
        application=body.application, credential_role=body.credential_role, environment=body.environment,
        explore=body.explore, allow_destructive=body.allow_destructive)
    return {"ok": True, "run_id": run_id}


@router.get("/discoveries")
def list_discoveries(application: str | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.automation.discovery.list(application=application)}


@router.get("/discovery-applications")
def discovery_applications(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.automation.discovery.applications()}


@router.get("/discovery-jobs/{run_id}")
def discovery_job(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "job": rt.automation.discovery_job(run_id).view()}


@router.post("/discovery-jobs/{run_id}/input")
def discovery_input(run_id: str, body: DiscoveryInputIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    job = rt.automation.discovery_job(run_id)
    job.answer(body.values, body.save_static)
    return {"ok": True}


@router.post("/discovery-jobs/{run_id}/stop")
def discovery_stop(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    if not rt.automation.discovery_job(run_id).stop():
        raise QAError("DISCOVERY_NOT_ACTIVE", f"Discovery {run_id} is not running.", status=409)
    return {"ok": True}


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
    environment: str | None = Field(default=None, max_length=60)
    auth_method: Literal["form", "windows", "basic", "manual", "none"] | None = None


@router.get("/credentials")
def credentials(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.automation.creds.list(), "store_available": rt.secrets.available}


@router.put("/credentials")
def save_credential(body: CredentialIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    if body.password and not rt.secrets.available:
        raise QAError("CREDENTIAL_STORE_UNAVAILABLE", "The Windows Credential Manager is not available.", status=503)
    if body.login_url and not body.login_url.lower().startswith(("http://", "https://")):
        raise QAError("CREDENTIAL_INVALID", "The login URL must start with http:// or https://.")
    item = rt.automation.creds.save(body.application, body.role, body.username, body.password, body.login_url,
                                    body.environment, body.auth_method)
    return {"ok": True, "item": item}


@router.delete("/credentials/{cid}")
def delete_credential(cid: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.automation.creds.delete(cid)
    return {"ok": True}


# ---- final patch: Browser Manager (Preferences > Browser) ---------------------------------------------------------

@router.get("/browsers/check")
def check_browsers(launch_test: bool = True, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Which browsers are installed and start on this PC, the monitors found and where windows open."""
    from ..automation import browser as browser_manager

    browser_manager.configure(rt.settings)
    return {"ok": True, **browser_manager.diagnose(try_launch=launch_test)}
