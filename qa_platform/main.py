"""FastAPI application factory and entry point."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import core, tfs
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
