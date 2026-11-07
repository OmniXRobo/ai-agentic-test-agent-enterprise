"""23.3.34: Generate AI analysis report timed out after 2 minutes with the local Qwen model on a PC without a GPU.

The analysis (run, dashboard, performance) now gets the long-answer time limit (generation_timeout_seconds, 10 minutes
by default), a grammar that keeps the answer short, a smaller answer budget, and a shorter prompt (long Playwright
errors and self-healing records are cut)."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from qa_platform.credentials import MemoryBackend
from qa_platform.main import create_app
from tests.fake_llm import FakeLLM

REPLY = {"summary": "The checkout test failed at step 5.", "observations": ["Step 5 failed"], "issues": [],
         "recommendations": [], "undetermined": []}
LONG_ERROR = "Locator.click: Timeout 30000ms exceeded.\nCall log:\n" + "  - waiting for locator('#add')\n" * 400


@pytest.fixture()
def setup(paths):
    llm = FakeLLM()
    llm.extra["reviewing one automated browser test run"] = REPLY
    llm.extra["QA manager reviewing a QA dashboard"] = REPLY
    timeouts = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            timeouts.append(request.extensions.get("timeout", {}).get("read"))
        return llm.handle(request)

    app = create_app(paths, secret_backend=MemoryBackend(), ai_transport=httpx.MockTransport(handle))
    with TestClient(app) as c:
        c.put("/api/ai/settings", json={"base_url": "http://127.0.0.1:8080/v1", "model": "qwen2.5-7b-instruct-q4_k_m.gguf"})
        yield c, llm, timeouts


def _fake_run(rt, monkeypatch):
    steps = [{"index": i, "description": f"Step {i + 1}", "action": "click", "status": "PASSED", "duration_ms": 900}
             for i in range(17)]
    steps[4].update(status="FAILED", error=LONG_ERROR)
    steps[2]["healed"] = {"healing_id": 7, "step": 2, "original": "#old", "healed": "#new", "confidence": 0.91,
                          "candidates": [{"selector": f"#c{n}", "confidence": 0.5} for n in range(50)]}
    record = {"run_id": "RUN-1", "script_id": None, "status": "FAILED", "browser": "msedge", "base_url": "https://x.test",
              "test_case_id": None, "started_at": "2026-11-07T15:30:01Z", "finished_at": "2026-11-07T15:30:40Z",
              "failed_step": 4, "error": LONG_ERROR, "steps": steps}
    monkeypatch.setattr(rt.automation.runner, "results", lambda **kw: [record])


def test_run_analysis_uses_the_long_time_limit_a_short_answer_and_a_short_prompt(setup, monkeypatch):
    c, llm, timeouts = setup
    rt = c.app.state.runtime
    _fake_run(rt, monkeypatch)
    st = c.post("/api/ai-reports/run/RUN-1/analyze").json()
    assert st["analysis"]["summary"] == REPLY["summary"], st
    body = next(b for b in llm.calls if "reviewing one automated browser test run" in b["messages"][0]["content"])
    # 10 minutes (generation_timeout_seconds), not the 2 minutes of a short request
    assert timeouts[-1] == 600
    assert body["max_tokens"] <= 1400
    schema = body["response_format"]["schema"]
    assert schema["properties"]["observations"]["maxItems"] == 6
    assert schema["properties"]["recommendations"]["maxItems"] == 5
    facts = json.loads(body["messages"][1]["content"])
    assert len(facts["steps"]) == 17
    assert len(facts["error"]) <= 400 and len(facts["steps"][4]["error"]) <= 400
    assert facts["steps"][2]["self_healed"] == {"original": "#old", "healed": "#new", "confidence": 0.91}
    assert len(body["messages"][1]["content"]) < 4000


def test_the_time_limit_follows_the_setting_and_dashboard_uses_it(setup):
    c, _, timeouts = setup
    rt = c.app.state.runtime
    rt.settings.update({"ai": {"generation_timeout_seconds": 900}})
    assert c.post("/api/ai-reports/dashboard/current/analyze").json()["analysis"]
    assert timeouts[-1] == 900


def test_a_reply_longer_than_the_caps_is_still_accepted(setup, monkeypatch):
    """The caps are only for the server's grammar; a server that ignores it does not make the analysis fail."""
    c, llm, _ = setup
    rt = c.app.state.runtime
    _fake_run(rt, monkeypatch)
    llm.extra["reviewing one automated browser test run"] = {**REPLY, "observations": [f"Point {i}" for i in range(9)]}
    st = c.post("/api/ai-reports/run/RUN-1/analyze").json()
    assert len(st["analysis"]["observations"]) == 9
