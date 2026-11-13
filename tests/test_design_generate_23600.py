"""23.6.0: AI Test Design "Generate" (DESIGN-* runs) on a slow local model.

Root cause of "On-Premise AI server did not answer in time": the design pipeline sent every acceptance criterion in one
request with the 120 s request timeout. These tests check the long generation timeout, the split into parts, the
stage named in every error, input validation and that a result is saved and can be opened again. The AI server is the
scripted FakeLLM (a protocol double); the generation logic under test is the real service code.
"""

from __future__ import annotations

import time

import httpx
import pytest
from fastapi.testclient import TestClient

from qa_platform.ai.gateway import AIGateway
from qa_platform.credentials import MemoryBackend
from qa_platform.main import create_app
from tests.fake_ado import PAT, FakeAzureDevOps
from tests.fake_llm import REPLIES, FakeLLM

BIG = 301


@pytest.fixture()
def env(paths, monkeypatch):
    from qa_platform.ai import gateway
    monkeypatch.setattr(gateway, "_sleep", lambda s: None)
    ado, llm = FakeAzureDevOps(), FakeLLM()
    ado.items[BIG] = {"System.Title": "Hotel booking", "System.WorkItemType": "User Story", "System.State": "Active",
                      "System.IterationPath": "Shop\\Sprint 4",
                      "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul>" + "".join(
                          f"<li>Criterion number {i}</li>" for i in range(1, 8)) + "</ul>"}
    ado.items[302] = {"System.Title": "Empty story", "System.WorkItemType": "User Story", "System.State": "New",
                      "System.IterationPath": "Shop\\Sprint 4"}
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport(), ai_transport=llm.transport())
    with TestClient(app) as c:
        c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                "make_active": True})
        c.put("/api/ai/settings", json={"base_url": "http://127.0.0.1:8080/v1", "model": "qwen2.5-7b-instruct-q4_k_m.gguf"})
        yield c, ado, llm


def _wait(c, run_id):
    for _ in range(400):
        body = c.get(f"/api/design/runs/{run_id}").json()
        if body["status"] != "RUNNING":
            return body
        time.sleep(0.05)
    raise AssertionError("design run did not finish")


def _run(c, story, **kw):
    r = c.post("/api/design/runs", json={"user_story_id": story, **kw})
    assert r.status_code in (200, 202), r.json()
    return _wait(c, r.json()["run_id"])


def _marker_calls(llm, marker):
    return [b for b in llm.calls if marker in b["messages"][0]["content"]]


def test_every_ai_call_uses_the_generation_timeout_and_schema(env, monkeypatch):
    c, _, _ = env
    seen = []
    real = AIGateway.complete_json

    def spy(self, system, user, model, **kw):
        seen.append(kw)
        return real(self, system, user, model, **kw)

    monkeypatch.setattr(AIGateway, "complete_json", spy)
    body = _run(c, 118)
    assert body["status"] == "PASSED", body
    assert len(seen) == 4
    assert all(kw["timeout"] == 600 and kw["enforce_schema"] is True for kw in seen)


def test_many_criteria_are_generated_in_parts_with_unique_ids(env):
    c, _, llm = env
    body = _run(c, BIG, max_cases=10)
    assert body["status"] == "PASSED", body
    res = body["result"]
    assert len(res["story"]["criteria"]) == 7
    assert len(_marker_calls(llm, "requirements analyst")) == 3  # 3 + 3 + 1 criteria per call
    for b in _marker_calls(llm, "requirements analyst"):
        assert b["messages"][1]["content"].count("- Criterion number") <= 3
    ids = [r["id"] for r in res["requirements"]["requirements"]]
    assert ids == [f"R{i}" for i in range(1, len(ids) + 1)] and len(ids) == 6
    refs = [t["ref"] for t in res["test_cases"]]
    assert len(refs) == len(set(refs)) and 1 < len(refs) <= 10
    for tc in res["test_cases"]:
        assert set(tc["requirement_ids"]) <= set(ids) and tc["requirement_ids"]
    assert {d["test_case_ref"] for d in res["test_data"]} <= set(refs)


def test_timeout_names_the_stage_and_what_to_do(env):
    c, _, llm = env

    def slow(body):
        raise httpx.ReadTimeout("model is busy")

    llm.extra["senior QA engineer"] = slow
    body = _run(c, 118)
    assert body["status"] == "FAILED"
    err = body["result"]["error"]
    assert "did not answer in time" in err and "test case generation" in err and "Generation timeout" in err
    assert body["result"]["requirements"]["requirements"]  # the finished stages are kept, nothing invented
    assert "test_cases" not in body["result"]


def test_malformed_output_fails_honestly_with_the_stage(env):
    c, _, llm = env
    llm.extra["test manager"] = {"nothing": "useful"}
    body = _run(c, 118)
    assert body["status"] == "FAILED"
    assert "did not return valid structured output" in body["result"]["error"]
    assert "Stage: test strategy" in body["result"]["error"]
    assert "plan" not in body["result"] and "test_cases" not in body["result"]


def test_empty_story_and_invalid_ids_are_rejected_clearly(env):
    c, _, llm = env
    body = _run(c, 302)
    assert body["status"] == "FAILED" and "nothing to design" in body["result"]["error"]
    assert not _marker_calls(llm, "requirements analyst")  # no AI call for an empty story
    for bad in (0, -5, "abc", None):
        assert c.post("/api/design/runs", json={"user_story_id": bad}).status_code == 422
    assert c.post("/api/design/runs", json={"user_story_id": 118, "max_cases": 0}).status_code == 422


def test_missing_story_fails_with_the_tfs_reason(env):
    c, _, _ = env
    body = _run(c, 999999)
    assert body["status"] == "FAILED" and body["result"]["error"]


def test_result_is_saved_and_reopens(env):
    c, _, _ = env
    body = _run(c, 118)
    run_id = body["run_id"] if "run_id" in body else body["result"]["run_id"]
    again = c.get(f"/api/design/runs/{run_id}").json()
    assert again["result"]["test_cases"] == body["result"]["test_cases"]
    runs = c.get("/api/design/stories/118").json()["runs"]
    assert any(r["run_id"] == run_id for r in runs)


def test_fake_llm_replies_are_unchanged():
    # guards the fixture the old tests depend on
    assert [t["ref"] for t in REPLIES["senior QA engineer"]["test_cases"]] == ["TC1", "TC2"]
