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
from .api import automation, core, defects, design, knowledge, performance, reports, setup, studio, tfs, voice
from .config import WEB_ROOT, Paths
from .errors import install_error_handlers
from .logging_setup import configure_logging, get_logger
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
    app.include_router(performance.router)
    app.include_router(defects.router)
    app.include_router(voice.router)
    app.include_router(reports.router)
    app.include_router(setup.router)
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

    server = Settings(paths.settings_file).section("server")
    uvicorn.run(create_app(paths), host=server.get("host", "127.0.0.1"), port=int(server.get("port", 8765)),
                log_level="warning")


if __name__ == "__main__":
    run()
