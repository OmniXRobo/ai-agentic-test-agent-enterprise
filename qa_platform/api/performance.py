"""Performance testing API: plans, JMeter runs, results, SLA, AI analysis, tool settings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from ..errors import QAError
from ..logging_setup import get_logger
from ..performance import script as jscript
from ..performance.jmx import build_jmx
from ..performance.models import PerfEnvironment, PerfPlan, PerfScenario
from ..runtime import Runtime
from .deps import runtime

router = APIRouter(prefix="/api/performance")
log = get_logger("api.performance")


class ToolSettingsIn(BaseModel):
    jmeter_home: str | None = None
    java_home: str | None = None


class FromDiscoveryIn(BaseModel):
    discovery_id: str
    name: str = ""
    kind: str = Field(default="load", pattern="^(load|stress|spike|soak)$")
    include_pages: bool = True


class ImportIn(BaseModel):
    jmx: str = Field(min_length=1)
    filename: str = ""
    name: str = ""
    kind: str = Field(default="load", pattern="^(load|stress|spike|soak)$")


class ScriptIn(BaseModel):
    script: str = Field(min_length=1)


class ApplyIn(BaseModel):
    script: str = Field(min_length=1)
    ops: list[dict[str, Any]] = []


class NodeIn(BaseModel):
    script: str = Field(min_length=1)
    node: str


class SaveScriptIn(BaseModel):
    script: str = Field(min_length=1)
    base_version: int | None = None
    note: str = ""
    name: str | None = None
    kind: str | None = Field(default=None, pattern="^(load|stress|spike|soak)$")
    sla: dict[str, Any] | None = None


@router.get("/environment")
def environment(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    perf = rt.settings.section("performance")
    return {"ok": True, **rt.performance.environment(), "jmeter_home": perf.get("jmeter_home", ""),
            "java_home": perf.get("java_home", "")}


@router.put("/environment")
def set_environment(body: ToolSettingsIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.settings.update({"performance": {k: (v or "").strip() for k, v in body.model_dump(exclude_none=True).items()}})
    return environment(rt)


@router.get("/plans")
def plans(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.performance.list_plans()}


@router.post("/plans", status_code=201)
def create_plan(body: PerfPlan, scenario_id: str | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """A new plan joins `scenario_id` when given, otherwise it gets its own scenario."""
    body.id = ""
    body.source = body.source or "manual"
    return {"ok": True, "plan": rt.performance.save_plan(body, scenario_id=scenario_id).model_dump()}


@router.post("/plans/from-discovery", status_code=201)
def plan_from_discovery(body: FromDiscoveryIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    plan = rt.performance.plan_from_discovery(body.discovery_id, name=body.name, kind=body.kind,
                                              include_pages=body.include_pages)
    return {"ok": True, "plan": plan.model_dump()}


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    plan = rt.performance.get_plan(plan_id)
    return {"ok": True, "plan": plan.model_dump(), "thread_groups": [g.model_dump() for g in plan.thread_groups()],
            "peak_users": plan.peak_users()}


@router.put("/plans/{plan_id}")
def update_plan(plan_id: str, body: PerfPlan, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    current = rt.performance.get_plan(plan_id)
    body.id = plan_id
    body.source = current.source
    if current.script:  # the form cannot change a script; only its name, type and targets
        body.script, body.script_version = current.script, current.script_version
    return {"ok": True, "plan": rt.performance.save_plan(body).model_dump()}


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.performance.delete_plan(plan_id)
    return {"ok": True}


@router.get("/plans/{plan_id}/jmx", response_class=PlainTextResponse)
def plan_jmx(plan_id: str, rt: Runtime = Depends(runtime)) -> PlainTextResponse:
    plan = rt.performance.get_plan(plan_id)
    return PlainTextResponse(plan.script or build_jmx(plan), media_type="application/xml",
                             headers={"Content-Disposition": f'attachment; filename="{plan_id}.jmx"'})


# ---- 23.3.23: script editor ----------------------------------------------------------------------------------------

@router.post("/plans/import", status_code=201)
def import_plan(body: ImportIn, scenario_id: str | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Import a JMX file. Elements the editor has no form for are kept unchanged. Nothing is run."""
    plan = rt.performance.import_script(body.jmx, name=body.name, kind=body.kind, filename=body.filename,
                                        scenario_id=scenario_id)
    return {"ok": True, "plan": plan.model_dump(exclude={"script"})}


@router.post("/plans/{plan_id}/script/convert")
def convert_plan(plan_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    plan = rt.performance.convert_to_script(plan_id)
    return {"ok": True, "plan": plan.model_dump(exclude={"script"})}


@router.get("/plans/{plan_id}/script")
def get_script(plan_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.performance.script_view(plan_id), "templates": jscript.templates()}


@router.put("/plans/{plan_id}/script")
def save_script(plan_id: str, body: SaveScriptIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Save the script as a new version. Saving never starts a run."""
    plan = rt.performance.save_script(plan_id, body.script, base_version=body.base_version, note=body.note,
                                      name=body.name, kind=body.kind, sla_=body.sla)
    return {"ok": True, **rt.performance.script_view(plan.id)}


@router.post("/script/analyze")
def analyze_script(body: ScriptIn) -> dict[str, Any]:
    """Validate JMX text (the Raw JMX view): the tree, what it does and any problems. Nothing is saved."""
    return {"ok": True, "script": jscript.normalize(body.script), **jscript.analyze(body.script)}


@router.post("/script/apply")
def apply_script(body: ApplyIn) -> dict[str, Any]:
    """Apply editor operations to a working copy of the script. Nothing is saved until PUT .../script."""
    return {"ok": True, **jscript.apply(body.script, body.ops)}


@router.post("/script/node")
def script_node(body: NodeIn) -> dict[str, Any]:
    return {"ok": True, "node": jscript.node_details(body.script, body.node)}


@router.get("/plans/{plan_id}/versions")
def script_versions(plan_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.performance.script_versions(plan_id)}


@router.get("/plans/{plan_id}/versions/{version}", response_class=PlainTextResponse)
def script_version(plan_id: str, version: int, rt: Runtime = Depends(runtime)) -> PlainTextResponse:
    return PlainTextResponse(rt.performance.script_version(plan_id, version), media_type="application/xml",
                             headers={"Content-Disposition": f'attachment; filename="{plan_id}-v{version}.jmx"'})


@router.get("/plans/{plan_id}/diff")
def script_diff(plan_id: str, a: int, b: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "diff": rt.performance.script_diff(plan_id, a, b)}


@router.post("/plans/{plan_id}/versions/{version}/restore")
def restore_version(plan_id: str, version: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    plan = rt.performance.restore_version(plan_id, version)
    return {"ok": True, "version": plan.script_version}


# ---- 23.3.24: browser recording, HAR import, saved secrets --------------------------------------------------------

class RecordIn(BaseModel):
    url: str = Field(min_length=1, pattern=r"^https?://")
    allowed_hosts: list[str] = []
    include_static: bool = False
    auto_steps: bool = True
    browser: str = Field(default="chromium", pattern="^(chromium|msedge|chrome|firefox)$")


class StepIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class RecordPreviewIn(BaseModel):
    allowed_hosts: list[str] = []
    include_static: bool | None = None
    exclude_ids: list[int] = []


class RecordScriptIn(RecordPreviewIn):
    name: str = ""
    kind: str = Field(default="load", pattern="^(load|stress|spike|soak)$")
    scenario_id: str | None = None


class HarIn(BaseModel):
    har: dict[str, Any]
    name: str = ""
    kind: str = Field(default="load", pattern="^(load|stress|spike|soak)$")
    allowed_hosts: list[str] = Field(min_length=1)
    include_static: bool = False
    filename: str = ""
    scenario_id: str | None = None


class SecretIn(BaseModel):
    value: str = Field(min_length=1)


@router.post("/recordings", status_code=201)
def start_recording(body: RecordIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Open a browser and record the requests of what the tester does in it. Nothing is load tested."""
    return {"ok": True, "recording": rt.performance.start_recording(
        body.url, allowed_hosts=body.allowed_hosts, include_static=body.include_static, auto_steps=body.auto_steps,
        browser=body.browser)}


@router.get("/recordings/current")
def current_recording(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "recording": rt.performance.recording_view()}


@router.post("/recordings/{rec_id}/step")
def recording_step(rec_id: str, body: StepIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "recording": rt.performance.recording_step(rec_id, body.name)}


@router.post("/recordings/{rec_id}/stop")
def stop_recording(rec_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "recording": rt.performance.stop_recording(rec_id)}


@router.post("/recordings/{rec_id}/preview")
def recording_preview(rec_id: str, body: RecordPreviewIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """What the script would contain: kept and left-out requests, correlated values and secrets. Nothing is saved."""
    return {"ok": True, **rt.performance.recording_preview(rec_id, allowed_hosts=body.allowed_hosts,
                                                            include_static=body.include_static,
                                                            exclude_ids=body.exclude_ids)}


@router.post("/recordings/{rec_id}/script", status_code=201)
def recording_script(rec_id: str, body: RecordScriptIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Save the recording as a script (1 user, 60 s). Nothing is run."""
    plan = rt.performance.create_from_recording(rec_id, name=body.name, kind=body.kind, scenario_id=body.scenario_id,
                                                allowed_hosts=body.allowed_hosts, include_static=body.include_static,
                                                exclude_ids=body.exclude_ids)
    return {"ok": True, "plan": plan.model_dump(exclude={"script"})}


@router.delete("/recordings/{rec_id}")
def discard_recording(rec_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.performance.discard_recording(rec_id)
    return {"ok": True}


@router.post("/plans/import-har", status_code=201)
def import_har(body: HarIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    plan = rt.performance.import_har(body.har, name=body.name, kind=body.kind, allowed_hosts=body.allowed_hosts,
                                     include_static=body.include_static, scenario_id=body.scenario_id,
                                     filename=body.filename)
    return {"ok": True, "plan": plan.model_dump(exclude={"script"})}


@router.get("/plans/{plan_id}/secrets")
def plan_secrets(plan_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Names of the script's secrets and whether a value is saved. Values are never returned."""
    return {"ok": True, "items": rt.performance.script_secrets(plan_id)}


@router.put("/plans/{plan_id}/secrets/{name}")
def save_plan_secret(plan_id: str, name: str, body: SecretIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.performance.save_script_secret(plan_id, name, body.value)}


# ---- 23.3.25: test data sets and bindings ---------------------------------------------------------------------------

class ColumnIn(BaseModel):
    name: str
    type: str = "text"
    from_: str | None = Field(default=None, alias="from")


class DatasetIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    columns: list[ColumnIn] = Field(min_length=1)
    rows: list[list[str | None]] = []


class DatasetRowIn(BaseModel):
    id: int | None = None
    values: list[str | None] = []


class DatasetUpdateIn(BaseModel):
    name: str = ""
    description: str = ""
    columns: list[ColumnIn] = Field(min_length=1)
    rows: list[DatasetRowIn] = []
    base_version: int | None = None


class CsvIn(BaseModel):
    csv: str = Field(min_length=1)  # the file's text; base64:... for bytes (other encodings than UTF-8)
    filename: str = ""
    name: str = ""
    mode: str = Field(default="create", pattern="^(create|replace|append)$")


class GenerateIn(BaseModel):
    rules: dict[str, dict[str, Any]] = {}
    count: int = Field(default=10, ge=1, le=10000)
    replace: bool = False
    seed: int | None = None


class AiRowsIn(BaseModel):
    description: str = ""
    count: int = Field(default=10, ge=1, le=50)


def _csv_bytes(text: str) -> bytes | str:
    if text.startswith("base64:"):
        import base64
        try:
            return base64.b64decode(text[7:], validate=True)
        except ValueError as exc:
            raise QAError("VALIDATION_FAILED", "The file could not be read.", status=422) from exc
    return text


@router.get("/datasets")
def list_datasets(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.performance.datasets.list()}


@router.post("/datasets", status_code=201)
def create_dataset(body: DatasetIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    cols = [c.model_dump(exclude={"from_"}) for c in body.columns]
    return {"ok": True, "dataset": rt.performance.datasets.create(body.name, cols, body.rows,
                                                                  description=body.description)}


@router.post("/datasets/import", status_code=201)
def import_dataset(body: CsvIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "dataset": rt.performance.datasets.import_csv(_csv_bytes(body.csv), name=body.name,
                                                                      filename=body.filename)}


@router.get("/datasets/{ds_id}")
def get_dataset(ds_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """The data set; secret cells are never returned (secret_set says which rows have a value)."""
    return {"ok": True, "dataset": rt.performance.datasets.view(ds_id)}


@router.put("/datasets/{ds_id}")
def update_dataset(ds_id: str, body: DatasetUpdateIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    cols = [{"name": c.name, "type": c.type, "from": c.from_} for c in body.columns]
    return {"ok": True, "dataset": rt.performance.datasets.update(
        ds_id, name=body.name, description=body.description, columns=cols,
        rows=[r.model_dump() for r in body.rows], base_version=body.base_version)}


@router.delete("/datasets/{ds_id}")
def delete_dataset(ds_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.performance.datasets.delete(ds_id)
    return {"ok": True}


@router.post("/datasets/{ds_id}/import")
def import_into_dataset(ds_id: str, body: CsvIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    mode = body.mode if body.mode != "create" else "replace"
    return {"ok": True, "dataset": rt.performance.datasets.import_csv(_csv_bytes(body.csv), ds_id=ds_id, mode=mode,
                                                                      filename=body.filename)}


@router.get("/datasets/{ds_id}/csv", response_class=PlainTextResponse)
def export_dataset(ds_id: str, rt: Runtime = Depends(runtime)) -> PlainTextResponse:
    """The data set as UTF-8 CSV. Secret columns are exported empty."""
    text, filename = rt.performance.datasets.export_csv(ds_id)
    return PlainTextResponse("\ufeff" + text, media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/datasets/{ds_id}/generate")
def generate_rows(ds_id: str, body: GenerateIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "dataset": rt.performance.datasets.generate(ds_id, body.rules, body.count,
                                                                    replace=body.replace, seed=body.seed)}


@router.post("/datasets/{ds_id}/generate-ai")
def generate_ai_rows(ds_id: str, body: AiRowsIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "dataset": rt.performance.datasets.generate_ai(ds_id, body.description, body.count)}


@router.post("/script/fields")
def script_fields(body: ScriptIn) -> dict[str, Any]:
    """Request values of a working copy that can be bound to data columns, and the data sets it reads."""
    return {"ok": True, "fields": jscript.fields(body.script),
            "data_sets": jscript.data_sets(jscript.parse(body.script))}


def _require_tools(rt: Runtime) -> None:
    env = rt.performance.environment()
    if not env["ready"]:
        missing = "Java" if not env["java"] else "JMeter"
        raise QAError(f"{missing.upper()}_NOT_FOUND", f"{missing} was not found. Set it under Performance tool settings.",
                      status=409)


@router.post("/plans/{plan_id}/run", status_code=202)
def run_plan(plan_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Queue the plan. Runs start one at a time on this PC; the run page shows the queue position."""
    rt.performance.get_plan(plan_id)
    _require_tools(rt)
    run_id = rt.performance.enqueue(plan_id, trigger="manual")
    return {"ok": True, "run_id": run_id, "state": "QUEUED"}


class StopIn(BaseModel):
    mode: str = Field(default="graceful", pattern="^(graceful|emergency)$")


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: str, body: StopIn | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    mode = (body or StopIn()).mode
    if rt.performance.stop(run_id, mode=mode):
        return {"ok": True, "mode": mode}
    if rt.performance.cancel(run_id):
        return {"ok": True, "mode": "cancelled"}
    raise QAError("RUN_NOT_ACTIVE", f"Run {run_id} is not queued or executing.", status=409, run_id=run_id)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    if not rt.performance.cancel(run_id):
        raise QAError("RUN_NOT_QUEUED", f"Run {run_id} is not waiting in the queue.", status=409, run_id=run_id)
    return {"ok": True}


@router.get("/queue")
def run_queue(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.performance.queue()}


@router.get("/runs")
def runs(plan_id: str | None = None, kind: str | None = None, scenario_id: str | None = None,
         environment_id: str | None = None, state: str | None = None, verdict: str | None = None,
         trigger: str | None = None, q: str = "", limit: int = Query(100, ge=1, le=1000),
         rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    items = rt.performance.list_runs(plan_id, kind, limit, scenario_id=scenario_id, environment_id=environment_id,
                                     state=state, verdict=verdict, trigger=trigger, q=q)
    return {"ok": True, "items": items, "running": rt.performance.running()}


# ---- 23.3.22: target environments ------------------------------------------------------------------------

@router.get("/environments")
def environments(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": [e.model_dump() for e in rt.performance.list_environments()]}


@router.post("/environments", status_code=201)
def create_environment(body: PerfEnvironment, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    body.id = ""
    return {"ok": True, "environment": rt.performance.save_environment(body).model_dump()}


@router.put("/environments/{env_id}")
def update_environment(env_id: str, body: PerfEnvironment, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    body.id = env_id
    return {"ok": True, "environment": rt.performance.save_environment(body).model_dump()}


@router.delete("/environments/{env_id}")
def delete_environment(env_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.performance.delete_environment(env_id)
    return {"ok": True}


# ---- 23.3.22: scenarios ----------------------------------------------------------------------------------

class CloneIn(BaseModel):
    name: str | None = None


class ArchiveIn(BaseModel):
    archived: bool = True


@router.get("/scenarios")
def scenarios(q: str = "", kind: str | None = None, environment_id: str | None = None, archived: bool = False,
              rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.performance.list_scenarios(q=q, kind=kind, environment_id=environment_id,
                                                               archived=archived)}


@router.post("/scenarios", status_code=201)
def create_scenario(body: PerfScenario, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    body.id, body.archived = "", False
    sc = rt.performance.save_scenario(body)
    return {"ok": True, "scenario": rt.performance.scenario_detail(sc.id)}


@router.get("/scenarios/{scenario_id}")
def get_scenario(scenario_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "scenario": rt.performance.scenario_detail(scenario_id),
            "runs": rt.performance.list_runs(scenario_id=scenario_id, limit=50)}


@router.put("/scenarios/{scenario_id}")
def update_scenario(scenario_id: str, body: PerfScenario, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    current = rt.performance.get_scenario(scenario_id)
    body.id, body.archived = scenario_id, current.archived
    rt.performance.save_scenario(body)
    return {"ok": True, "scenario": rt.performance.scenario_detail(scenario_id)}


@router.post("/scenarios/{scenario_id}/clone", status_code=201)
def clone_scenario(scenario_id: str, body: CloneIn | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    sc = rt.performance.clone_scenario(scenario_id, (body or CloneIn()).name)
    return {"ok": True, "scenario": rt.performance.scenario_detail(sc.id)}


@router.post("/scenarios/{scenario_id}/archive")
def archive_scenario(scenario_id: str, body: ArchiveIn | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.performance.archive_scenario(scenario_id, (body or ArchiveIn()).archived)
    return {"ok": True, "scenario": rt.performance.scenario_detail(scenario_id)}


@router.post("/scenarios/{scenario_id}/check")
def check_scenario(scenario_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Pre-run checks without running anything: tools, JMX, disk space, hosts and load limits."""
    return {"ok": True, **rt.performance.check_scenario(scenario_id)}


@router.post("/scenarios/{scenario_id}/run", status_code=202)
def run_scenario(scenario_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.performance.get_scenario(scenario_id)
    _require_tools(rt)
    run_id = rt.performance.run_scenario(scenario_id, trigger="manual")
    return {"ok": True, "run_id": run_id, "state": "QUEUED"}


@router.get("/runs/{run_id}")
def get_run(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "run": rt.performance.get_run(run_id)}


@router.post("/runs/{run_id}/analysis")
def analyze(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "analysis": rt.performance.analyze(run_id)}


@router.get("/runs/{run_id}/files/{path:path}")
def run_file(run_id: str, path: str, rt: Runtime = Depends(runtime)) -> FileResponse:
    return FileResponse(rt.performance.run_file(run_id, path))
