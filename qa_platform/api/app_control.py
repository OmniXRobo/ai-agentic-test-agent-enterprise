"""23.3.32: the app window and Exit, for an application started from the desktop icon (qa_platform.launcher)."""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, Depends, Request

from ..errors import QAError
from ..runtime import Runtime
from .deps import runtime

router = APIRouter(prefix="/api/app")


def active_schedules(rt: Runtime) -> int:
    """Enabled automation and performance schedules: while there are any, closing the window keeps the server."""
    n = 0
    if rt.scheduler is not None:
        n += sum(1 for s in rt.scheduler.list() if s.get("enabled", True) and s.get("next_run_at"))
    if rt.perf_scheduler is not None:
        n += sum(1 for s in rt.perf_scheduler.list() if s.get("enabled") and s.get("next_run_at"))
    return n


@router.get("/status")
def status(request: Request, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    launcher = getattr(request.app.state, "launcher", None)
    network = bool(getattr(request.app.state, "network_active", False))
    return {"desktop": launcher is not None, "active_schedules": active_schedules(rt), "network_active": network,
            "closing_window_stops": launcher is not None and active_schedules(rt) == 0 and not network}


@router.post("/window")
def window(request: Request) -> dict[str, Any]:
    launcher = getattr(request.app.state, "launcher", None)
    if launcher is None:
        raise QAError("APP_NOT_DESKTOP", "SkyGuard QA was started from a command window; open "
                      "http://127.0.0.1 in a browser.", status=409)
    return {"opened": launcher.open_window()}


@router.post("/exit")
def exit_app(request: Request) -> dict[str, Any]:
    launcher = getattr(request.app.state, "launcher", None)
    if launcher is None:
        raise QAError("APP_NOT_DESKTOP", "SkyGuard QA was started from a command window; close that window to stop "
                      "it.", status=409)
    threading.Timer(0.5, launcher.exit).start()  # answer first, then stop
    return {"stopping": True}
