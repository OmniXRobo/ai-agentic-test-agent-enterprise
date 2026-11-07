"""23.3.32: Acceptance Criteria work items reach the AI with their title, description and acceptance criteria detail."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from qa_platform.credentials import MemoryBackend
from qa_platform.design.service import criterion_detail
from qa_platform.main import create_app
from tests.fake_ado import PAT, FakeAzureDevOps
from tests.fake_llm import FakeLLM
from tests.test_suite_cases import _generate, _suite

CHILD = "System.LinkTypes.Hierarchy-Forward"


def _ac_item(ado: FakeAzureDevOps, story: int, item_id: int) -> None:
    ado.extra_types.append("Acceptance Criteria")
    ado.items[item_id] = {
        "System.Title": "Customer can reset a forgotten password", "System.WorkItemType": "Acceptance Criteria",
        "System.State": "Active", "System.IterationPath": "Shop\\Sprint 4",
        "System.Description": "<p>The reset link is sent by email and expires after 30 minutes.</p>",
        "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>The link works only once</li></ul>",
        "System.History": "Discussion: should we also send an SMS?"}
    ado.relations.setdefault(story, []).append((CHILD, item_id))
    ado.relations.setdefault(item_id, []).append(("System.LinkTypes.Hierarchy-Reverse", story))


def _client(paths):
    ado, llm = FakeAzureDevOps(hierarchy=True), FakeLLM()
    _ac_item(ado, 201, 7001)
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport(), ai_transport=llm.transport())
    return app, ado, llm


def _prompts(llm) -> str:
    return json.dumps([m["content"] for b in llm.calls for m in b["messages"]])


def test_criterion_detail_is_description_and_criteria_without_the_title():
    link = {"title": "Pay", "description": "<p>Pay</p><p>Card only</p>", "acceptance_criteria": "<li>Visa</li>"}
    assert criterion_detail(link) == "Pay\nCard only\n- Visa"
    assert criterion_detail({"title": "Pay", "description": "<p>Pay</p>"}) == ""
    assert criterion_detail({"title": "Pay"}) == ""


def test_suite_generation_sends_ac_item_description_and_detail_not_discussion(paths):
    app, ado, llm = _client(paths)
    with TestClient(app) as c:
        c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                "make_active": True})
        c.put("/api/ai/settings", json={"base_url": "http://127.0.0.1:8080/v1", "model": "qwen-local.gguf"})
        _, result = _generate(c, _suite(c, "functional"), [201])
    crit = {x["text"]: x for x in result["stories"][0]["criteria"]}
    reset = crit["Customer can reset a forgotten password"]
    assert "expires after 30 minutes" in reset["detail"] and "works only once" in reset["detail"]
    sent = _prompts(llm)
    assert "expires after 30 minutes" in sent and "The link works only once" in sent
    assert "SMS" not in sent  # Discussion is never read
    # criteria from the story's own field are unchanged and carry no detail
    assert "detail" not in crit["Customer can login successfully"]


def test_ai_test_design_sends_ac_item_description_and_detail(paths):
    app, ado, llm = _client(paths)
    with TestClient(app) as c:
        c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                "make_active": True})
        c.put("/api/ai/settings", json={"base_url": "http://127.0.0.1:8080/v1", "model": "qwen-local.gguf"})
        run_id = c.post("/api/design/runs", json={"user_story_id": 201}).json()["run_id"]
        for _ in range(400):
            if c.get(f"/api/design/runs/{run_id}").json()["status"] != "RUNNING":
                break
            import time
            time.sleep(0.05)
    sent = _prompts(llm)
    assert "Customer can reset a forgotten password" in sent
    assert "expires after 30 minutes" in sent and "The link works only once" in sent
    assert "SMS" not in sent
