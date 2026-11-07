"""23.3.31: AI analysis reports (functional run, performance run, dashboard): analyze, edit, PDF, email."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..runtime import Runtime
from .deps import runtime

router = APIRouter(prefix="/api/ai-reports")


class EditIn(BaseModel):
    summary: str | None = Field(default=None, max_length=4000)
    observations: list[str] | None = Field(default=None, max_length=40)
    issues: list[str] | None = Field(default=None, max_length=40)
    recommendations: list[str] | None = Field(default=None, max_length=40)
    undetermined: list[str] | None = Field(default=None, max_length=40)
    suspected_bottlenecks: list[str] | None = Field(default=None, max_length=20)


class EmailIn(BaseModel):
    to: str = Field(min_length=3, max_length=2000)
    cc: str | None = Field(default=None, max_length=2000)
    subject: str = Field(min_length=1, max_length=250)
    body: str = Field(default="", max_length=20000)


def _svc(rt: Runtime) -> Any:
    return rt.ai_reports


@router.get("/{kind}/{item_id}")
def get_report(kind: str, item_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **_svc(rt).get(kind, item_id)}


@router.post("/{kind}/{item_id}/analyze")
def analyze(kind: str, item_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **_svc(rt).analyze(kind, item_id)}


@router.patch("/{kind}/{item_id}")
def edit(kind: str, item_id: str, body: EditIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **_svc(rt).save(kind, item_id, body.model_dump(exclude_none=True))}


@router.post("/{kind}/{item_id}/pdf")
def make_pdf(kind: str, item_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **_svc(rt).make_pdf(kind, item_id)}


@router.get("/{kind}/{item_id}/pdf")
def pdf(kind: str, item_id: str, download: bool = False, rt: Runtime = Depends(runtime)) -> FileResponse:
    svc = _svc(rt)
    return FileResponse(svc.pdf_file(kind, item_id), media_type="application/pdf", filename=svc.file_name(kind, item_id),
                        content_disposition_type="attachment" if download else "inline")


@router.get("/{kind}/{item_id}/email")
def email_draft(kind: str, item_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **_svc(rt).email_draft(kind, item_id)}


@router.post("/{kind}/{item_id}/email")
def send_email(kind: str, item_id: str, body: EmailIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **_svc(rt).send(kind, item_id, body.to, body.subject, body.body, cc=body.cc)}
