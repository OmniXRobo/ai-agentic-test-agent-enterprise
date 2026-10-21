from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from qa_platform.config import Paths
from qa_platform.main import create_app


@pytest.fixture()
def paths(tmp_path):
    return Paths(tmp_path / "data").ensure()


@pytest.fixture()
def client(paths):
    app = create_app(paths)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def rt(client):
    return client.app.state.runtime
