"""AURA API (final patch): status, settings, knowledge, activity, backup, restore, export, import, reset."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..aura.service import KINDS
from ..clock import now
from ..errors import QAError
from ..runtime import Runtime
from .deps import runtime

router = APIRouter(prefix="/api/aura")


class SettingsIn(BaseModel):
    enabled: bool | None = None
    memory_path: str | None = Field(default=None, max_length=240)
    retention_days: int | None = Field(default=None, ge=0, le=3650)


class PathIn(BaseModel):
    path: str = Field(min_length=3, max_length=240)


class ApproveIn(BaseModel):
    approved: bool = True


class RestoreIn(BaseModel):
    file: str = Field(min_length=10, max_length=80)


class ResetIn(BaseModel):
    application: str | None = Field(default=None, max_length=200)
    confirm: str = Field(max_length=260)


def _aura(rt: Runtime) -> Any:
    if getattr(rt, "aura", None) is None:
        raise QAError("AURA_UNAVAILABLE", "AURA is not available.", status=503)
    return rt.aura


@router.get("/status")
def status(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    a = _aura(rt)
    return {"ok": True, **a.stats(), "config": a.config(), "all_kinds": list(KINDS)}


@router.put("/settings")
def save_settings(body: SettingsIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    a = _aura(rt)
    if body.memory_path is not None:
        a.use_folder(body.memory_path)
    if body.retention_days is not None:
        rt.settings.update({"aura": {"retention_days": body.retention_days}})
    if body.enabled is not None:
        a.set_enabled(body.enabled)
    return {"ok": True, **a.stats(), "config": a.config()}


@router.post("/check-path")
def check_path(body: PathIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    path = _aura(rt).check_folder(body.path)
    import shutil

    return {"ok": True, "path": str(path), "free_mb": shutil.disk_usage(path).free // 1048576,
            "has_memory": (path / "aura_memory.sqlite3").exists()}


@router.post("/migrate")
def migrate(body: PathIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **_aura(rt).migrate(body.path)}


@router.get("/entries")
def entries(kind: str | None = None, application: str | None = None, q: str | None = Query(None, max_length=200),
            all_versions: bool = False, limit: int = Query(200, ge=1, le=1000),
            rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    if kind and kind not in KINDS:
        raise QAError("AURA_KIND_INVALID", f"Unknown kind '{kind}'.")
    return {"ok": True, "items": _aura(rt).entries(kind=kind, application=application, q=q,
                                                    latest_only=not all_versions, limit=limit)}


@router.get("/history")
def history(kind: str, application: str, key: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "versions": _aura(rt).history(kind, application, key)}


@router.post("/entries/{entry_id}/approve")
def approve(entry_id: int, body: ApproveIn, request: Request, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    from ..auth import current_user

    user = current_user()
    return {"ok": True, "entry": _aura(rt).approve(entry_id, body.approved, by=getattr(user, "username", None))}


@router.get("/activity")
def activity(limit: int = Query(100, ge=1, le=1000), rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": _aura(rt).activity(limit)}


@router.get("/applications")
def applications(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": _aura(rt).applications()}


@router.get("/integrity")
def integrity(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **_aura(rt).integrity()}


@router.post("/backup")
def backup(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **_aura(rt).backup("manual backup")}


@router.get("/backups")
def backups(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": _aura(rt).backups()}


@router.post("/restore")
def restore(body: RestoreIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **_aura(rt).restore(body.file)}


@router.get("/export")
def export(application: str | None = None, rt: Runtime = Depends(runtime)) -> Response:
    data = _aura(rt).export(application)
    name = f"aura-export-{now().strftime('%Y%m%d-%H%M%S')}.zip"
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/import")
async def import_zip(request: Request, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    content = await request.body()
    return {"ok": True, **_aura(rt).import_zip(content)}


@router.post("/reset")
def reset(body: ResetIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **_aura(rt).reset(body.application, body.confirm)}
