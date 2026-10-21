from __future__ import annotations

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
