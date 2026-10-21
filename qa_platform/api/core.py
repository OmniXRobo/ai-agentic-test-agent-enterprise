"""Core API: health, metadata, agents, events (incl. SSE), notifications, runs, settings."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import __version__
from ..agents.registry import AGENTS
from ..clock import now_iso
from ..errors import QAError
from ..events import names
from ..runtime import Runtime
from ..terminology import DISPLAY_NAMES
from .deps import runtime

router = APIRouter(prefix="/api")

SSE_HEARTBEAT_SECONDS = 15


@router.get("/health")
def health(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "version": __version__, "time": now_iso(), "data_root": str(rt.paths.root)}


@router.get("/meta")
def meta() -> dict[str, Any]:
    return {
        "ok": True,
        "version": __version__,
        "terminology": DISPLAY_NAMES,
        "agents": [a.to_dict() for a in AGENTS],
        "event_names": sorted(names.ALL_EVENTS),
    }


def _agents_payload(rt: Runtime) -> list[dict[str, Any]]:
    states = {s["agent_id"]: s for s in rt.state.snapshot()}
    return [{**a.to_dict(), **states[a.id]} for a in AGENTS]


@router.get("/agents")
def agents(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "agents": _agents_payload(rt)}


@router.get("/events")
def events(
    limit: int = Query(100, ge=1, le=1000),
    after_id: int | None = Query(None, ge=0),
    run_id: str | None = None,
    agent_id: str | None = None,
    rt: Runtime = Depends(runtime),
) -> dict[str, Any]:
    return {"ok": True, "events": rt.bus.recent(limit, after_id, run_id, agent_id)}


def _sse(message: dict[str, Any], event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {message['type']}\ndata: {json.dumps(message, ensure_ascii=False, default=str)}\n\n"


@router.get("/events/stream")
async def event_stream(request: Request, rt: Runtime = Depends(runtime)) -> StreamingResponse:
    """Server-Sent Events: a snapshot first, then live events. Resumes from Last-Event-ID."""
    queue = rt.bus.subscribe()
    last_id = request.headers.get("last-event-id")

    async def gen() -> AsyncIterator[str]:
        try:
            yield "retry: 3000\n\n"
            snapshot = {
                "type": "snapshot",
                "agents": _agents_payload(rt),
                "unread": rt.notifications.unread_count(),
            }
            yield _sse(snapshot)
            if last_id and last_id.isdigit():
                for ev in rt.bus.recent(500, after_id=int(last_id)):
                    yield _sse({"type": "event", "event": ev}, ev["id"])
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=SSE_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                event_id = message["event"]["id"] if message["type"] == "event" else None
                yield _sse(message, event_id)
        finally:
            rt.bus.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---- notifications -----------------------------------------------------------


class MarkRead(BaseModel):
    ids: list[int] | None = None


@router.get("/notifications")
def notifications(
    limit: int = Query(50, ge=1, le=500),
    unread_only: bool = False,
    rt: Runtime = Depends(runtime),
) -> dict[str, Any]:
    return {
        "ok": True,
        "notifications": rt.notifications.list(limit, unread_only),
        "unread": rt.notifications.unread_count(),
    }


@router.post("/notifications/read")
def mark_read(body: MarkRead, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    changed = rt.notifications.mark_read(body.ids)
    return {"ok": True, "changed": changed, "unread": rt.notifications.unread_count()}


# ---- runs / diagnostics -------------------------------------------------------


@router.get("/runs")
def runs(
    kind: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    rt: Runtime = Depends(runtime),
) -> dict[str, Any]:
    return {"ok": True, "runs": rt.runs.list(kind, limit, offset)}


@router.get("/runs/{run_id}")
def run_detail(run_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    run = rt.runs.get(run_id)
    if run is None:
        raise QAError("RUN_NOT_FOUND", f"Run {run_id} does not exist.", status=404, run_id=run_id)
    return {"ok": True, "run": run, "events": rt.bus.recent(1000, run_id=run_id)}


@router.post("/diagnostics/run", status_code=202)
async def run_diagnostics(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    from ..agents.base import new_run_id

    if any(s["agent_id"] == "orchestration" and s["status"] == "RUNNING" for s in rt.state.snapshot()):
        raise QAError("ALREADY_RUNNING", "System diagnostics are already running.", status=409,
                      agent="QA Orchestration Agent")
    run_id = new_run_id("DIAG")
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _diagnostics_safely, rt, run_id)
    return {"ok": True, "run_id": run_id}


def _diagnostics_safely(rt: Runtime, run_id: str) -> None:
    try:
        rt.orchestration.run_diagnostics(run_id)
    except Exception:
        # The agent already published agent.failed and logged; nothing else to do here.
        pass


# ---- dashboard -----------------------------------------------------------------


@router.get("/dashboard/summary")
def dashboard_summary(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Only data the runtime actually has. TFS-backed KPIs arrive with the TFS integration."""
    agents_state = rt.state.snapshot()
    return {
        "ok": True,
        "runs": rt.runs.counts(),
        "agents_running": sum(1 for s in agents_state if s["status"] == "RUNNING"),
        "unread_notifications": rt.notifications.unread_count(),
        "tfs": {"connected": False, "reason": "TFS integration is not installed yet (Stage 2)."},
    }


# ---- settings --------------------------------------------------------------------


@router.get("/settings")
def get_settings(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "settings": rt.settings.get()}


ALLOWED_SETTINGS_SECTIONS = {"ui", "tfs", "ai", "defects", "performance", "playback"}


@router.patch("/settings")
def patch_settings(patch: dict[str, Any], rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    unknown = set(patch) - ALLOWED_SETTINGS_SECTIONS
    if unknown:
        raise QAError("SETTINGS_INVALID", "Unknown settings sections.", details=sorted(unknown))
    theme = patch.get("ui", {}).get("theme")
    if theme is not None and theme not in ("light", "dark"):
        raise QAError("SETTINGS_INVALID", "Theme must be 'light' or 'dark'.")
    mode = patch.get("defects", {}).get("creation_mode")
    if mode is not None and mode not in ("review", "automatic", "disabled"):
        raise QAError("SETTINGS_INVALID", "Defect creation mode must be review, automatic or disabled.")
    try:
        return {"ok": True, "settings": rt.settings.update(patch)}
    except ValueError as exc:
        raise QAError("SECRET_IN_SETTINGS", str(exc)) from exc
