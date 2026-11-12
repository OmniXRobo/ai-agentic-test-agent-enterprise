"""Final patch, defects 1 and 3: mandatory TFS fields discovered from the project, filled safely or asked for, and
TF401320 rule errors reported by field instead of "HTTP 400"."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from qa_platform.errors import QAError
from qa_platform.main import create_app
from qa_platform.credentials import MemoryBackend
from tests.fake_ado import PAT, FakeAzureDevOps


@pytest.fixture()
def env(paths):
    ado = FakeAzureDevOps(severity=True)
    ado.required["Test Case"] = {"Custom.TestResult": ("Test Result", ["0 Not Started", "1 Passed", "2 Failed"])}
    ado.required["Bug"] = {"Custom.Environment": ("Environment", ["DEV", "SIT", "UAT"])}
    ado.rule_only["Bug"] = {"Custom.FoundIn": ("Found In", ["Build 23.4"])}
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport())
    with TestClient(app) as c:
        c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                "make_active": True})
        yield c, ado, c.app.state.runtime


def test_test_case_gets_not_started_result_automatically(env):
    _c, ado, rt = env
    out = rt.tfs.create("Test Case", {"title": "Login works"})
    item = ado.items[out["item"]["id"]]
    assert item["Custom.TestResult"] == "0 Not Started"
    assert {"ref": "Custom.TestResult", "name": "Test Result", "value": "0 Not Started", "source": "automatic"} \
        in out["fields_report"]["filled"]


def test_bug_asks_for_environment_then_uses_saved_default_and_rule_only_field(env):
    c, ado, rt = env
    with pytest.raises(QAError) as err:
        rt.tfs.create("Bug", {"title": "Checkout fails"})
    assert err.value.error_code == "TFS_FIELDS_REQUIRED"
    asked = err.value.details["fields"][0]
    assert asked["name"] == "Environment" and asked["allowed"] == ["DEV", "SIT", "UAT"]
    r = c.get("/api/tfs/preferences/validate", params={"type": "Bug"})
    assert r.status_code == 200 and r.json()["ready"] is False
    assert c.put("/api/tfs/preferences/defaults", json={"type": "Bug", "values": {"Custom.Environment": "uat"}}).status_code == 200
    assert c.get("/api/tfs/preferences/validate", params={"type": "Bug"}).json()["ready"] is True
    # Test Work Item Creation validates only: TFS checks the rules, nothing is saved
    before = len(ado.items)
    t = c.post("/api/tfs/preferences/test-create", json={"type": "Bug"})
    assert t.status_code == 200, t.text
    assert t.json()["saved"] is False and len(ado.items) == before and ado.validated
    out = rt.tfs.create("Bug", {"title": "Checkout fails"})
    item = ado.items[out["item"]["id"]]
    # "uat" matched the allowed "UAT"; the rule-only "Found In" was read from the TF401320 message and filled
    assert item["Custom.Environment"] == "UAT" and item["Custom.FoundIn"] == "Build 23.4"


def test_rule_error_with_no_safe_value_names_the_field(env):
    _c, ado, rt = env
    ado.rule_only["Task"] = {"Custom.Team": ("Team", ["Alpha", "Beta"])}
    with pytest.raises(QAError) as err:
        rt.tfs.create("Task", {"title": "Prepare data"})
    assert err.value.error_code in ("TFS_FIELDS_REQUIRED", "TFS_FIELD_RULE")
    assert "Team" in err.value.message and "HTTP 400" not in err.value.message
