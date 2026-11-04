"""Run the app with the simulated TFS project (fake_tfs.py), for the browser tests. Usage: fake_tfs_server.py PORT"""
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, os.environ["APP_DIR"])
sys.path.insert(0, str(Path(__file__).parent))

import uvicorn  # noqa: E402

import fake_tfs  # noqa: E402
from qa_platform.main import create_app  # noqa: E402

app = create_app()
inner = app.router.lifespan_context


@asynccontextmanager
async def lifespan(a):
    async with inner(a):
        fake_tfs.install_design(a.state.runtime, fake_tfs.install(a.state.runtime))
        yield


app.router.lifespan_context = lifespan
uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
