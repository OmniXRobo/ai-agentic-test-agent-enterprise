"""Test data and credential fixtures (the test data layer).

Each test names its data file with ``@pytest.mark.test_data("tc1268")``; the file
``test_data/tc1268.json`` holds one or more combinations, and the test runs once per
combination. Values may contain runtime rules such as ``{{TODAY+7d:%d/%m/%Y}}``,
resolved from the system clock when the test runs.

Credentials are never stored in the data files. The file only says which
application, environment and role the test logs in as; the username and password
come from the Enterprise QA Runtime (Application Credentials, or the script's own
credentials) or, when the test runs on its own, from environment variables named
after the role, for example STANDARD_USER_USERNAME and STANDARD_USER_PASSWORD.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import pytest

from utilities import config, reporting
from utilities.runtime_values import resolve_all


def load(name: str) -> dict[str, Any]:
    path = config.ROOT / "test_data" / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Test data file test_data/{name}.json does not exist.")
    return json.loads(path.read_text(encoding="utf-8"))


def _data_name(node: Any) -> str | None:
    marker = node.get_closest_marker("test_data")
    return str(marker.args[0]) if marker and marker.args else None


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "data" not in metafunc.fixturenames:
        return
    name = _data_name(metafunc.definition)
    combos: list[dict[str, Any]] = [{"name": "default", "values": {}}]
    if name:
        combos = load(name).get("combinations") or combos
    if config.COMBINATION:
        combos = [c for c in combos if c.get("name") == config.COMBINATION] or combos[:0]
    metafunc.parametrize("data", combos, ids=[str(c.get("name") or f"combination {i + 1}") for i, c in enumerate(combos)],
                         indirect=True)


class TestData(dict):
    """The values of one combination, with runtime rules already resolved."""

    def __getitem__(self, key: str) -> Any:
        if key not in self:
            raise KeyError(f"Test data field '{key}' is missing from this combination.")
        return super().__getitem__(key)


@pytest.fixture()
def data(request: pytest.FixtureRequest) -> TestData:
    combo = getattr(request, "param", None) or {"name": "default", "values": {}}
    name = _data_name(request.node)
    info = load(name) if name else {}
    reporting.state.base_url = info.get("base_url") or ""
    return TestData(resolve_all(dict(combo.get("values") or {})))


@dataclass
class Credentials:
    application: str | None
    environment: str | None
    role: str | None
    username: str
    password: str


def _env_name(role: str, field: str) -> str:
    return re.sub(r"\W+", "_", f"{role}_{field}").upper()


@pytest.fixture()
def credentials(request: pytest.FixtureRequest) -> Credentials:
    name = _data_name(request.node)
    ref = (load(name).get("credentials") if name else None) or {}
    role = ref.get("role") or "user"
    username = os.environ.get("QA_CRED_USERNAME") or os.environ.get(_env_name(role, "username"))
    password = os.environ.get("QA_CRED_PASSWORD") or os.environ.get(_env_name(role, "password"))
    missing = [n for n, v in (("username", username), ("password", password)) if not v]
    if missing:
        pytest.fail(f"Application credentials missing for role '{role}': {', '.join(missing)}. Set them in the "
                    f"Automation Studio (Credentials) or set {_env_name(role, 'username')} / "
                    f"{_env_name(role, 'password')}.", pytrace=False)
    reporting.register_secret(password)
    return Credentials(ref.get("application"), ref.get("environment"), role, str(username), str(password))
