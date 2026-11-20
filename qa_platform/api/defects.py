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
    assigned_to: str | None = Field(default=None, max_length=256)  # 23.13.0: "" clears it


class LinkIn(BaseModel):
    tfs_id: int


class DismissIn(BaseModel):
    reason: str = Field(default="", max_length=2000)


class ApproveIn(BaseModel):
    note: str = Field(default="", max_length=2000)


class PublishIn(BaseModel):
    fields: dict[str, Any] | None = None  # values for mandatory fields TFS Preferences could not complete
    allow_duplicate: bool = False


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


class ManualIn(BaseModel):
    source: str = Field(default="functional", max_length=40)
    module: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=3, max_length=250)
    severity: int = Field(default=2, ge=1, le=4)
    priority: int = Field(default=2, ge=1, le=4)
    repro_steps: str = Field(default="", max_length=20000)
    assigned_to: str | None = Field(default=None, max_length=256)
    run_id: str | None = Field(default=None, max_length=80)
    send_now: bool = False  # approve and send in one action (needs both permissions)


@router.post("/manual", status_code=201)
def manual(body: ManualIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """23.13.0: "Create TFS defect" on the module pages."""
    draft = rt.defects.manual_draft(source=body.source, module=body.module, title=body.title, severity=body.severity,
                                    priority=body.priority, repro_steps=body.repro_steps, assigned_to=body.assigned_to,
                                    run_id=body.run_id)
    if body.send_now:
        draft = rt.defects.create(draft["id"])
    return {"ok": True, "draft": draft}


@router.get("/assignees")
def assignees(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """23.13.0: the people of the TFS project, for the "Assign to" list of every kind of defect."""
    return {"ok": True, "people": rt.tfs.assignees()}


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
    """Approve and send in one explicit action (the person must be allowed to do both)."""
    return {"ok": True, "draft": rt.defects.create(draft_id, body.model_dump(exclude_none=True) if body else None)}


@router.post("/drafts/{draft_id}/link")
def link(draft_id: int, body: LinkIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "draft": rt.defects.link_existing(draft_id, body.tfs_id)}


@router.post("/drafts/{draft_id}/dismiss")
def dismiss(draft_id: int, body: DismissIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "draft": rt.defects.dismiss(draft_id, body.reason)}


# ---- 23.11.0: one Defect Review for AI-Driven, Guided Browser and Integration Comparison -------------------------


@router.post("/drafts/{draft_id}/approve")
def approve(draft_id: int, body: ApproveIn | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "draft": rt.defects.approve(draft_id, body.note if body else "")}


@router.post("/drafts/{draft_id}/reject")
def reject(draft_id: int, body: DismissIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "draft": rt.defects.reject(draft_id, body.reason)}


@router.post("/drafts/{draft_id}/publish")
def publish(draft_id: int, body: PublishIn | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    body = body or PublishIn()
    return {"ok": True, "draft": rt.defects.publish(draft_id, fields=body.fields, allow_duplicate=body.allow_duplicate)}


@router.post("/drafts/{draft_id}/retry")
def retry(draft_id: int, body: PublishIn | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "draft": rt.defects.retry(draft_id, fields=(body.fields if body else None))}


@router.post("/drafts/{draft_id}/ai-analyze")
def ai_analyze(draft_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "draft": rt.defects.ai_analyze(draft_id)}


@router.get("/drafts/{draft_id}/events")
def events(draft_id: int, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.defects.get(draft_id)
    return {"ok": True, "events": rt.defects.events(draft_id)}


@router.get("/drafts/{draft_id}/evidence/{index}")
def evidence(draft_id: int, index: int, rt: Runtime = Depends(runtime)) -> Any:
    """One evidence file of a draft (only files the draft itself lists; nothing else on disk can be read)."""
    from pathlib import Path

    from fastapi.responses import FileResponse

    from ..errors import QAError

    items = rt.defects.get(draft_id)["evidence"] or []
    if index < 0 or index >= len(items) or not Path(str(items[index].get("path") or "")).is_file():
        raise QAError("EVIDENCE_NOT_FOUND", "This evidence file is not available.", status=404)
    path = Path(items[index]["path"])
    inline = path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
    return FileResponse(path, filename=path.name, content_disposition_type="inline" if inline else "attachment")
