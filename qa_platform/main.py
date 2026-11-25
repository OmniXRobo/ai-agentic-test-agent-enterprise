"""FastAPI application factory and entry point."""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import opsagent as opsagent_api
from .api import agent_simple as agent_simple_api
from .api import ai_reports, app_control, aura as aura_api, auth as auth_api, automation, core, defects, design, guard, knowledge, perf_schedules, performance, reports, schedules, setup, studio, tfs, voice
from .config import WEB_ROOT, Paths
from .errors import install_error_handlers
from .logging_setup import configure_logging, get_logger
from .auth import UserStore
from .runtime import build_runtime

log = get_logger("main")


REQUEST_THREADS = 100  # 23.14.1: worker threads for API requests (the default of 40 filled up behind slow TFS calls)


def _widen_thread_pool() -> None:
    """23.14.1: requests that wait for TFS (each up to 30 s) no longer use up the threads every other page needs.

    Every API request that is not async runs on one of these threads. With a slow TFS server, the Dashboard, the
    status strip and the TFS pages held all 40 default threads, so even pages that never touch TFS (the AI analysis
    panel, notifications) and Connection Test waited in the queue and showed "Loading..." for a long time."""
    try:
        import anyio.to_thread

        limiter = anyio.to_thread.current_default_thread_limiter()
        if limiter.total_tokens < REQUEST_THREADS:
            limiter.total_tokens = REQUEST_THREADS
    except Exception as exc:  # noqa: BLE001 - never stop the server for this
        log.warning(f"Could not widen the request thread pool: {exc}")


def create_app(paths: Paths | None = None, **runtime_options: Any) -> FastAPI:
    paths = (paths or Paths()).ensure()
    configure_logging(paths.logs)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        rt = build_runtime(paths, **runtime_options)
        _widen_thread_pool()
        rt.bus.bind_loop(asyncio.get_running_loop())
        app.state.runtime = rt
        app.state.users = UserStore(rt.db)  # 23.3.34: accounts and sign-ins
        rt.tfs.people = app.state.users  # 23.13.0: a personal TFS project per account
        from . import https_setup  # 23.13.0: the friendly HTTPS name is a name of this PC for the request checks
        _https = https_setup.config(rt.settings.get())
        app.state.https_hosts = {_https["hostname"]} if _https["enabled"] else set()
        # 23.3.34: the server listens on the office network only when Network access was on as it started.
        app.state.network_active = auth_api.network_enabled(rt.settings)
        log.info(f"Enterprise QA Runtime {__version__} started; data at {paths.root}")
        try:
            yield
        finally:
            log.info("Enterprise QA Runtime shutting down")
            rt.close()

    app = FastAPI(title="AI Agentic Test Agent Enterprise", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    install_error_handlers(app)
    from .api import agent_control as agent_control_api
    app.include_router(agent_control_api.router)  # 23.14.0: Stop / Pause / Resume on the agent cards (before /api/agents/...)
    app.include_router(core.router)
    app.include_router(tfs.router)
    app.include_router(design.router)
    app.include_router(studio.router)
    app.include_router(automation.router)
    app.include_router(knowledge.router)
    app.include_router(perf_schedules.router)  # 23.3.29: before /api/performance/{...} catch-alls
    app.include_router(performance.router)
    app.include_router(defects.router)
    app.include_router(voice.router)
    app.include_router(reports.router)
    app.include_router(ai_reports.router)  # 23.3.31
    app.include_router(setup.router)
    app.include_router(schedules.router)  # 23.3.21
    app.include_router(app_control.router)  # 23.3.32: app window and Exit
    app.include_router(auth_api.router)  # 23.3.34: sign-in, Users, Network access
    app.include_router(aura_api.router)  # final patch: AURA learning memory
    from .api import comparison as comparison_api

    from .api import workspace as workspace_api

    # 23.8.0: the Integration Comparison Workspace (before the older routes: "/api/comparison/workspace/..." )
    app.include_router(workspace_api.router)
    app.include_router(comparison_api.router)  # final patch: Application Data Comparison Agent
    app.include_router(opsagent_api.router)  # 23.5.0: Adaptive AI Application Learning & Operations Agent
    app.include_router(agent_simple_api.router)  # 23.10.0: the simple AI Agent (four tabs)
    from .api import apitesting as apitesting_api

    app.include_router(apitesting_api.router)  # 23.12.0: API Testing
    from .api import integration as integration_api
    from .api import security as security_api
    from .api import test_actions as test_actions_api

    app.include_router(security_api.router)  # 23.14.0: Security Testing
    app.include_router(integration_api.router)  # 23.14.0: Red Hat Fuse and ActiveMQ testing
    app.include_router(test_actions_api.router)  # 23.14.0: Analyze with AI, TFS defect, PDF, Email Developer, retest
    opsagent_api.install(app)
    auth_api.install(app)  # 23.3.34: every API request needs an account allowed to make it (runs after the guard)
    guard.install(app)  # 23.3.32: only the dashboard itself may use the API

    @app.middleware("http")
    async def revalidate_pages(request, call_next):  # type: ignore[no-untyped-def]
        # Browsers keep ES modules they loaded before; make them ask again (unchanged files answer 304).
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    # The page loads its scripts and styles from /r<version>/..., so after an update or patch every module has a new
    # address and no browser can keep running the previous version's pages.
    asset_prefix = f"r{__version__}"

    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    def index() -> HTMLResponse:
        page = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        page = re.sub(r'(href|src)="((?:css|js|img)/)', rf'\1="{asset_prefix}/\2', page)
        return HTMLResponse(page)

    app.mount(f"/{asset_prefix}", StaticFiles(directory=WEB_ROOT), name="web-versioned")
    app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web")
    return app


def run() -> None:
    import uvicorn

    paths = Paths().ensure()
    from .config import Settings

    settings = Settings(paths.settings_file)
    server = settings.section("server")
    uvicorn.run(create_app(paths), host=auth_api.bind_host(settings.get()), port=int(server.get("port", 8765)),
                log_level="warning")


if __name__ == "__main__":
    run()
