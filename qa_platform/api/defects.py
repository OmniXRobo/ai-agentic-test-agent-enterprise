"""Defect Intelligence API: failure analysis, drafts, duplicate check, create/link/dismiss, creation mode."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..runtime import Runtime
from .deps import runtime

router = APIRouter(prefix="/api/defects")


class AnalyzeIn(BaseModel):
    kind: str = Field(pattern="^(functional|performance)$")
    run_id: str


class DraftIn(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=250)
    severity: int | None = Field(default=None, ge=1, le=4)
    priority: int | None = Field(default=None, ge=1, le=4)
    repro_steps: str | None = None


class LinkIn(BaseModel):
    tfs_id: int


class DismissIn(BaseModel):
    reason: str = ""


class ModeIn(BaseModel):
    creation_mode: str = Field(pattern="^(review|automatic|disabled)$")


@router.get("/mode")
def get_mode(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "creation_mode": rt.defects.mode}


@router.put("/mode")
def set_mode(body: ModeIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.settings.update({"defects": {"creation_mode": body.creation_mode}})
    return {"ok": True, "creation_mode": rt.defects.mode}


@router.post("/analyze")
def analyze(body: AnalyzeIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "draft": rt.defects.analyze(body.kind, body.run_id)}


@router.get("/drafts")
def drafts(source: str | None = None, status: str | None = None, limit: int = Query(200, ge=1, le=1000),
           rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.defects.list(source, status, limit), "creation_mode": rt.defects.mode}


@router.get("/drafts/{draft_id}")
def draft(draft_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "draft": rt.defects.get(draft_id)}


@router.patch("/drafts/{draft_id}")
def update_draft(draft_id: int, body: DraftIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "draft": rt.defects.update(draft_id, body.model_dump(exclude_none=True))}


@router.post("/drafts/{draft_id}/duplicates")
def recheck(draft_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.defects.check_duplicates(draft_id)
    return {"ok": True, "draft": rt.defects.get(draft_id)}


@router.post("/drafts/{draft_id}/create")
def create(draft_id: int, body: DraftIn | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "draft": rt.defects.create(draft_id, body.model_dump(exclude_none=True) if body else None)}


@router.post("/drafts/{draft_id}/link")
def link(draft_id: int, body: LinkIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "draft": rt.defects.link_existing(draft_id, body.tfs_id)}


@router.post("/drafts/{draft_id}/dismiss")
def dismiss(draft_id: int, body: DismissIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "draft": rt.defects.dismiss(draft_id, body.reason)}
