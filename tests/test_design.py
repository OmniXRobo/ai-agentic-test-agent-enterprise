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


# ---- test plan, testing-type suites and test data in TFS -------------------------------------------


def _designed(c):
    run_id = c.post("/api/design/runs", json={"user_story_id": 118}).json()["run_id"]
    assert _wait(c, run_id)["status"] == "PASSED"
    return run_id


@pytest.mark.parametrize("legacy", [False, True])
def test_publish_creates_plan_suites_cases_and_test_data(paths, legacy):
    ado, llm = FakeAzureDevOps(legacy_test_api=legacy), FakeLLM()
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport(), ai_transport=llm.transport())
    with TestClient(app) as c:
        c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                "make_active": True})
        c.put("/api/ai/settings", json={"base_url": "http://127.0.0.1:8080/v1", "model": "m"})
        run_id = _designed(c)
        r = c.post(f"/api/design/runs/{run_id}/publish", json={"create_plan": True})
        assert r.status_code == 200, r.json()
        pub = r.json()
        plan_id = pub["plan_id"]
        assert pub["plan"]["created"] is True and ado.plans[plan_id]["name"] == "Shop - Sprint 4 - Test Plan"
        assert ado.plans[plan_id]["iteration"] == "Shop\\Sprint 4"
        assert "Scope: Checkout payment with saved cards" in ado.plans[plan_id]["description"]
        root = ado.plans[plan_id]["rootSuite"]["id"]
        children = [s for s in ado.suites[plan_id] if s["id"] != root]
        assert [s["name"] for s in children] == design_service.DEFAULT_SUITES
        suite_id = {s["name"]: s["id"] for s in children}

        tc1, tc2 = pub["created"]
        assert ado.suite_members[suite_id["Positive Testing"]] == [tc1["id"]]
        assert ado.suite_members[suite_id["Negative Testing"]] == [tc2["id"]]
        item = ado.items[tc1["id"]]
        assert item["System.Title"] == "Positive Testing - Pay with a valid saved card"
        assert ado.items[tc2["id"]]["System.Title"] == "Negative Testing - Expired saved card is rejected"
        assert "Positive Testing" in item["System.Tags"]
        assert "<h3>Test Data</h3>" in item["System.Description"] and "4111 1111 1111 1111" in item["System.Description"]
        assert "Valid Visa" in item["System.Description"] and "User Story #118" in item["System.Description"]
        assert item["Microsoft.VSTS.TCM.Parameters"] == '<parameters><param name="card" bind="default" /><param name="expiry" bind="default" /></parameters>'
        assert "<Table1><card>4111 1111 1111 1111</card><expiry>12/30</expiry></Table1>" in item["Microsoft.VSTS.TCM.LocalDataSource"]
        assert ("Microsoft.VSTS.Common.TestedBy-Reverse", 118) in ado.relations[tc1["id"]]

        # Publishing again reuses the plan and its suites.
        again = c.post(f"/api/design/runs/{run_id}/publish", json={"create_plan": True, "refs": ["TC1"]}).json()
        assert again["plan_id"] == plan_id and again["plan"]["created"] is False
        assert len(ado.suites[plan_id]) == 7
        assert len(ado.suite_members[suite_id["Positive Testing"]]) == 2


def test_publish_options(env):
    c, ado, _ = env
    run_id = _designed(c)
    assert c.post(f"/api/design/runs/{run_id}/publish", json={"suite_id": 8}).status_code == 400
    assert c.post(f"/api/design/runs/{run_id}/publish", json={"plan_id": 7, "create_plan": True}).status_code == 400
    # Existing plan, no default suites: cases go to the root suite; test data only in the Summary.
    pub = c.post(f"/api/design/runs/{run_id}/publish", json={
        "plan_id": 7, "default_suites": False, "data_as_parameters": False}).json()
    ids = [x["id"] for x in pub["created"]]
    assert ado.suite_members[70] == ids and len(ado.suites[7]) == 2
    assert "Microsoft.VSTS.TCM.Parameters" not in ado.items[ids[0]]
    assert "<h3>Test Data</h3>" in ado.items[ids[0]]["System.Description"]
    # Existing plan with default suites: the testing-type suites are added to it.
    pub = c.post(f"/api/design/runs/{run_id}/publish", json={"plan_id": 7, "data_in_summary": False}).json()
    assert len(ado.suites[7]) == 2 + len(design_service.DEFAULT_SUITES)
    assert "<h3>Test Data</h3>" not in ado.items[pub["created"][0]["id"]].get("System.Description", "")


def test_create_test_plan_endpoint(env):
    c, ado, _ = env
    r = c.post("/api/tfs/test-plans", json={"user_story_id": 118})
    assert r.status_code == 201, r.json()
    body = r.json()
    assert body["plan"]["name"] == "Shop - Sprint 4 - Test Plan"
    assert [s["name"] for s in body["suites"]] == design_service.DEFAULT_SUITES and all(s["created"] for s in body["suites"])
    again = c.post("/api/tfs/test-plans", json={"name": "Shop - Sprint 4 - Test Plan"}).json()
    assert again["plan"]["id"] == body["plan"]["id"] and not any(s["created"] for s in again["suites"])
    plans = c.get("/api/tfs/test-plans").json()["plans"]
    assert any(p["name"] == "Shop - Sprint 4 - Test Plan" for p in plans)


def test_write_test_data_to_tfs_replaces_the_section(env):
    c, ado, _ = env
    run_id = _designed(c)
    tc = c.post(f"/api/design/runs/{run_id}/publish", json={}).json()["created"][0]["id"]
    ado.items[tc]["System.Description"] = "<p>Customer has a saved card.</p>" + ado.items[tc]["System.Description"]
    row = c.get(f"/api/test-data?test_case_id={tc}").json()["items"][0]
    c.patch(f"/api/test-data/{row['id']}", json={"values": {"card": "5500 0000 0000 0004", "expiry": "11/31"}})
    r = c.post(f"/api/test-data/tfs/{tc}", json={})
    assert r.status_code == 200 and r.json()["datasets"] == 1
    desc = ado.items[tc]["System.Description"]
    assert desc.startswith("<p>Customer has a saved card.</p>")
    assert desc.count("<h3>Test Data</h3>") == 1 and "5500 0000 0000 0004" in desc and "4111" not in desc
    assert "<expiry>11/31</expiry>" in ado.items[tc]["Microsoft.VSTS.TCM.LocalDataSource"]
    assert c.post(f"/api/test-data/tfs/{tc}", json={"summary": False, "parameters": False}).status_code == 400


def test_test_data_xml_and_names():
    from qa_platform.design.models import TestCase, TestStep

    params, source = design_service.parameters_xml([{"name": "a", "values": {"user name": "<b>&", "2fa": True}}])
    assert '<param name="user_name"' in params and '<param name="p_2fa"' in params
    assert "<user_name>&lt;b&gt;&amp;</user_name><p_2fa>True</p_2fa>" in source
    assert design_service.parameters_xml([{"name": "none", "values": {}}]) is None
    html_ = design_service.test_data_html([], "Logged in")
    assert "No variable test data" in html_ and "Logged in" in html_
    case = TestCase(ref="TC1", title="Smoke Testing - Checkout works", category="smoke",
                    steps=[TestStep(action="a", expected="b")])
    assert design_service.titled(case) == "Smoke Testing - Checkout works"
    assert design_service.suite_for("edge") == "Boundary / Edge Case Testing"
    assert design_service.plan_name("Shop", "Shop\\Release 2\\Sprint 1") == "Shop - Sprint 1 - Test Plan"
    assert design_service.plan_name("Shop", "Shop") == "Shop - Test Plan"
