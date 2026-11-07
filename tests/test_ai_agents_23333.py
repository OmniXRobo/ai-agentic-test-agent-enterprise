"""23.3.33: the Local AI light says "Starting" while the model loads (not a cached "Offline"), and an old failure no
longer makes an agent look down."""

from __future__ import annotations

from datetime import timedelta

from qa_platform.clock import now, now_iso
from qa_platform.errors import QAError
from qa_platform.events.state import AgentStateManager, derive_state
from qa_platform.health import HealthService


class _AI:
    def __init__(self):
        self.up = False

    def config(self):
        return {"base_url": "http://127.0.0.1:8080/v1", "model": "qwen"}

    def models(self, timeout=3):
        if not self.up:
            raise QAError("AI_UNAVAILABLE", "connection refused")
        return ["qwen"]


class _LLM:
    state = "STARTING"

    def status(self):
        return {"state": self.state}


def test_local_ai_light_while_the_model_loads_then_ready():
    ai, llm = _AI(), _LLM()
    health = HealthService(None, ai, None, None)
    health.llm = llm
    item = health._cached("ai", health.check_ai, False)
    assert (item["status"], item["value"]) == ("WARN", "Starting")
    ai.up = True  # loaded: the next request checks again instead of answering from a 5-minute cache
    assert health._cached("ai", health.check_ai, False)["value"] == "Ready"


def test_local_ai_offline_when_the_server_is_not_starting():
    ai, llm = _AI(), _LLM()
    llm.state = "FAILED"
    health = HealthService(None, ai, None, None)
    health.llm = llm
    assert health.check_ai()["value"] == "Offline"
    health.remember("ai", health.check_ai())
    health.forget("ai")  # what the managed server's state listener does when loading ends
    ai.up = True
    assert health._cached("ai", health.check_ai, False)["value"] == "Ready"


def test_failed_agent_is_ready_again_after_ten_minutes():
    t = now()
    failed = {"agent_id": "test_execution", "status": "FAILED", "finished_at": now_iso()}
    assert derive_state(failed, {}, t)["state"] == "FAILED"
    later = derive_state(failed, {}, t + timedelta(minutes=11))
    assert later == {"state": "READY", "last_result": "FAILED", "waiting_for": None}


def test_agent_interrupted_by_an_old_shutdown_is_ready():
    m = AgentStateManager()
    old = (now() - timedelta(days=2)).isoformat()
    m.restore([{"agent_id": "test_case_design", "name": "agent.started", "run_id": "R1", "id": 1, "timestamp": old}])
    card = next(a for a in m.snapshot() if a["agent_id"] == "test_case_design")
    assert card["state"] == "READY" and card["last_result"] == "FAILED"
