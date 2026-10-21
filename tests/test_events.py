from __future__ import annotations

import pytest

from qa_platform.agents.base import BaseAgent
from qa_platform.events import names
from qa_platform.events.bus import QAEvent


class _Exec(BaseAgent):
    agent_id = "test_execution"


def _status(rt, agent_id):
    return next(s for s in rt.state.snapshot() if s["agent_id"] == agent_id)


def test_agent_running_only_during_activity(rt):
    agent = _Exec(rt.bus)
    assert _status(rt, "test_execution")["status"] == "WAITING"
    with agent.activity("Running Test Case #1280", test_case_id="1280") as act:
        st = _status(rt, "test_execution")
        assert st["status"] == "RUNNING"
        act.progress(40, "Step 2 of 5")
        assert _status(rt, "test_execution")["progress"] == 40
        assert _status(rt, "test_execution")["activity"] == "Step 2 of 5"
        act.complete("Test Execution Agent PASSED")
    st = _status(rt, "test_execution")
    assert st["status"] == "PASSED"
    assert st["finished_at"] is not None
    # Only the active agent was ever RUNNING.
    assert all(s["status"] != "RUNNING" for s in rt.state.snapshot())


def test_agent_failure_marks_failed_and_reraises(rt):
    agent = _Exec(rt.bus)
    with pytest.raises(RuntimeError):
        with agent.activity("Running"):
            raise RuntimeError("locator not found")
    st = _status(rt, "test_execution")
    assert st["status"] == "FAILED"
    assert "locator not found" in st["activity"]


def test_unknown_event_name_rejected(rt):
    with pytest.raises(ValueError):
        rt.bus.publish(QAEvent(name="made.up"))


def test_event_details_are_redacted(rt):
    ev = rt.bus.publish(QAEvent(name=names.AGENT_LOG, details={"pat": "abc123secret", "url": "x"}))
    stored = rt.bus.recent(1)[0]
    assert stored["id"] == ev.id
    assert stored["details"]["pat"] == "***REDACTED***"
    assert stored["details"]["url"] == "x"


def test_state_restored_after_restart(paths):
    from qa_platform.runtime import build_runtime

    rt1 = build_runtime(paths)
    agent = _Exec(rt1.bus)
    with agent.activity("first"):
        pass
    # Simulate a crash mid-activity for another agent.
    rt1.bus.publish(QAEvent(name=names.AGENT_STARTED, agent_id="jmeter_load", status="RUNNING"))
    rt1.close()

    rt2 = build_runtime(paths)
    snap = {s["agent_id"]: s for s in rt2.state.snapshot()}
    assert snap["test_execution"]["status"] == "PASSED"
    assert snap["jmeter_load"]["status"] == "FAILED"
    assert "Interrupted" in snap["jmeter_load"]["activity"]
    rt2.close()


def test_notifications_from_events(rt):
    rt.bus.publish(QAEvent(name=names.EXECUTION_FAILED, activity="Checkout failed", run_id="RUN-1"))
    assert rt.notifications.unread_count() == 1
    note = rt.notifications.list()[0]
    assert note["severity"] == "error" and note["run_id"] == "RUN-1"
    rt.notifications.mark_read([note["id"]])
    assert rt.notifications.unread_count() == 0
