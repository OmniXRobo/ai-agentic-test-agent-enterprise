"""Reports and analytics API: dashboard summary, functional/performance analytics, HTML/PDF reports."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..runtime import Runtime
from .deps import runtime

router = APIRouter()

# Reports are self-contained documents: no scripts, styles inline, images as data URIs.
REPORT_CSP = {"Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'"}


class ReportIn(BaseModel):
    scope: str = Field(default="both", pattern="^(functional|performance|both)$")
    days: int = Field(default=30, ge=1, le=365)
    title: str | None = Field(default=None, max_length=150)
    user_story_id: int | None = Field(default=None, ge=1)
    perf_run_ids: list[str] | None = Field(default=None, max_length=5)


@router.get("/api/analytics/summary")
async def summary(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **(await run_in_threadpool(rt.analytics.summary))}


@router.get("/api/analytics/functional")
async def functional(days: int = Query(30, ge=1, le=365), rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **(await run_in_threadpool(rt.analytics.functional, days))}


@router.get("/api/analytics/performance")
def performance(days: int = Query(30, ge=1, le=365), plan_id: str | None = None,
                rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.analytics.performance(days, plan_id)}


@router.post("/api/reports", status_code=202)
def create(body: ReportIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    params = body.model_dump(exclude_none=True)
    if params.get("title"):
        params["title"] = params["title"].strip() or None
    return {"ok": True, "report": rt.reports.start({k: v for k, v in params.items() if v is not None})}


@router.get("/api/reports")
def list_reports(limit: int = Query(100, ge=1, le=500), rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.reports.list(limit)}


@router.get("/api/reports/{report_id}")
def get_report(report_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "report": rt.reports.get(report_id)}


@router.get("/api/reports/{report_id}/html")
def report_html(report_id: str, download: bool = False, rt: Runtime = Depends(runtime)) -> FileResponse:
    path = rt.reports.file(report_id, "html")
    return FileResponse(path, media_type="text/html", filename=f"{report_id}.html" if download else None,
                        content_disposition_type="attachment" if download else "inline", headers=REPORT_CSP)


@router.get("/api/reports/{report_id}/pdf")
def report_pdf(report_id: str, download: bool = False, rt: Runtime = Depends(runtime)) -> FileResponse:
    path = rt.reports.file(report_id, "pdf")
    return FileResponse(path, media_type="application/pdf", filename=f"{report_id}.pdf",
                        content_disposition_type="attachment" if download else "inline")


@router.delete("/api/reports/{report_id}")
def delete_report(report_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.reports.delete(report_id)
    return {"ok": True}
