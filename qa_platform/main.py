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
from .api import ai_reports, app_control, aura as aura_api, auth as auth_api, automation, core, defects, design, guard, knowledge, perf_schedules, performance, reports, schedules, setup, studio, tfs, voice
from .config import WEB_ROOT, Paths
from .errors import install_error_handlers
from .logging_setup import configure_logging, get_logger
from .auth import UserStore
from .runtime import build_runtime

log = get_logger("main")


def create_app(paths: Paths | None = None, **runtime_options: Any) -> FastAPI:
    paths = (paths or Paths()).ensure()
    configure_logging(paths.logs)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        rt = build_runtime(paths, **runtime_options)
        rt.bus.bind_loop(asyncio.get_running_loop())
        app.state.runtime = rt
        app.state.users = UserStore(rt.db)  # 23.3.34: accounts and sign-ins
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

    app.include_router(comparison_api.router)  # final patch: Application Data Comparison Agent
    app.include_router(opsagent_api.router)  # 23.5.0: Adaptive AI Application Learning & Operations Agent
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
