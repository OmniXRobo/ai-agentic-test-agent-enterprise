from __future__ import annotations

import os
from pathlib import Path

# Patch tests look for the application in APP_DIR (in a patch zip it is the app/ folder; in this repository it is the root).
os.environ.setdefault("APP_DIR", str(Path(__file__).resolve().parents[1]))
# 23.3.32: the API accepts only this PC's own names; the test client calls itself "testserver".
os.environ.setdefault("QA_EXTRA_HOSTS", "testserver")
import pytest
from fastapi.testclient import TestClient

from qa_platform.config import Paths
from qa_platform.credentials import MemoryBackend
from qa_platform.main import create_app
from tests import site_server


@pytest.fixture()
def paths(tmp_path):
    return Paths(tmp_path / "data").ensure()


@pytest.fixture()
def client(paths):
    app = create_app(paths, secret_backend=MemoryBackend())
    with TestClient(app) as c:
        # 23.3.28: no PDF report after every load test run (each prints with Chromium); the report tests turn it on
        c.app.state.runtime.settings.update({"performance": {"auto_report": False}})
        yield c


@pytest.fixture()
def rt(client):
    return client.app.state.runtime


@pytest.fixture(scope="session")
def site():
    """The local test site (tests/site) served over HTTP."""
    server, url = site_server.start()
    yield url
    server.shutdown()
