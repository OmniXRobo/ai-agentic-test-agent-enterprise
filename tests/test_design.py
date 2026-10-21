from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from qa_platform.ai.gateway import extract_json
from qa_platform.credentials import MemoryBackend
from qa_platform.design import service as design_service
from qa_platform.main import create_app
from tests.fake_ado import PAT, FakeAzureDevOps
from tests.fake_llm import FakeLLM


@pytest.fixture()
def env(paths):
    ado, llm = FakeAzureDevOps(), FakeLLM()
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport(), ai_transport=llm.transport())
    with TestClient(app) as c:
        c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                "make_active": True})
        c.put("/api/ai/settings", json={"base_url": "http://127.0.0.1:8080/v1", "model": "qwen2.5-7b-instruct-q4_k_m.gguf"})
        yield c, ado, llm


def _wait(c, run_id):
    for _ in range(200):
        body = c.get(f"/api/design/runs/{run_id}").json()
        if body["status"] != "RUNNING":
            return body
        time.sleep(0.05)
    raise AssertionError("design run did not finish")


def test_extract_json_variants():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('<think>hmm</think> Result: {"a": [1, 2]} done') == {"a": [1, 2]}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_ai_diagnostics_pass(env):
    c, _, _ = env
    r = c.post("/api/ai/diagnostics").json()
    assert r["ok"] is True, r
    assert [x["name"] for x in r["checks"]] == ["Configuration", "Server Reachability", "Model Loaded", "Completion",
                                                "Structured JSON Output"]


def test_ai_diagnostics_model_not_loaded(env):
    c, _, llm = env
    llm.models = ["other.gguf"]
    r = c.post("/api/ai/diagnostics").json()
    model = next(x for x in r["checks"] if x["name"] == "Model Loaded")
    assert r["ok"] is False and model["status"] == "FAIL" and "other.gguf" in model["detail"]


def test_ai_unreachable_is_structured(env):
    c, _, llm = env
    llm.down = True
    r = c.get("/api/ai/models")
    assert r.status_code == 502 and r.json()["error_code"] == "AI_UNREACHABLE"


def test_full_design_run_drives_agents_and_publishes(env):
    c, ado, _ = env
    run_id = c.post("/api/design/runs", json={"user_story_id": 118}).json()["run_id"]
    body = _wait(c, run_id)
    assert body["status"] == "PASSED", body
    result = body["result"]
    assert result["story"]["criteria"] == ["Pay with saved card"]
    assert [t["ref"] for t in result["test_cases"]] == ["TC1", "TC2"]
    assert len(result["test_data"]) == 2  # orphan dataset for unknown TC9 dropped

    agents = {a["id"]: a for a in c.get("/api/agents").json()["agents"]}
    for aid in ("requirements", "test_planning", "test_case_design", "test_data"):
        assert agents[aid]["status"] == "PASSED"
    assert not any(a["status"] == "RUNNING" for a in agents.values())
    feed = [e["activity"] for e in c.get(f"/api/events?run_id={run_id}&limit=500").json()["events"]]
    assert any("reading User Story #118" in (a or "") for a in feed)
    assert "Acceptance Criteria retrieved: 1" in feed

    pub = c.post(f"/api/design/runs/{run_id}/publish", json={"plan_id": 7, "suite_id": 8}).json()
    ids = [x["id"] for x in pub["created"]]
    assert len(ids) == 2
    new = ado.items[ids[0]]
    assert new["System.WorkItemType"] == "Test Case"
    assert "<steps" in new["Microsoft.VSTS.TCM.Steps"] and "Payment page opens" in new["Microsoft.VSTS.TCM.Steps"]
    assert ("Microsoft.VSTS.Common.TestedBy-Reverse", 118) in ado.relations[ids[0]]
    assert ado.suite_cases == ids

    data = c.get(f"/api/test-data?test_case_id={ids[0]}").json()["items"]
    assert data[0]["name"] == "Valid Visa" and data[0]["dataset"]["expiry"] == "12/30"


def test_invalid_json_is_repaired_once(env, paths):
    c, _, llm = env
    llm.bad_json_once = True
    run_id = c.post("/api/design/runs", json={"user_story_id": 118}).json()["run_id"]
    assert _wait(c, run_id)["status"] == "PASSED"


def test_design_fails_honestly_when_ai_down(env):
    c, _, llm = env
    llm.down = True
    run_id = c.post("/api/design/runs", json={"user_story_id": 118}).json()["run_id"]
    body = _wait(c, run_id)
    assert body["status"] == "FAILED"
    assert "not reachable" in body["result"]["error"]
    assert "test_cases" not in body["result"]
    agents = {a["id"]: a for a in c.get("/api/agents").json()["agents"]}
    assert agents["requirements"]["status"] == "FAILED"


def test_steps_xml_escapes():
    from qa_platform.design.models import TestStep

    xml = design_service.steps_xml([TestStep(action="Type <b> & go", expected="ok")])
    assert '<steps id="0" last="2">' in xml and "&lt;b&gt;" not in xml.replace("&amp;lt;b&amp;gt;", "")
    assert "<b>" not in xml


def test_ai_settings_accept_max_tokens_but_not_secrets(env):
    c, _, _ = env
    r = c.put("/api/ai/settings", json={"max_tokens": 2048})
    assert r.status_code == 200 and r.json()["max_tokens"] == 2048
    r = c.patch("/api/settings", json={"ai": {"api_key": "x"}})
    assert r.status_code == 400
