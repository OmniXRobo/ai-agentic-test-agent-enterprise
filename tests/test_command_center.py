"""Command Center rules: agent card states come only from runtime events, the voice registry never lets the local
AI invent a command, an ID or a page, and the status strip reports real, cached checks."""

from __future__ import annotations

from datetime import timedelta

from qa_platform.clock import now, now_iso
from qa_platform.events.state import derive_state
from qa_platform.voice import registry


def st(agent_id, status, **kw):
    return {"agent_id": agent_id, "status": status, **kw}


def test_card_states_are_derived_from_events():
    t = now()
    running = st("requirements", "RUNNING", run_id="R1")
    assert derive_state(running, {})["state"] == "RUNNING"
    assert derive_state({**running, "paused": True}, {})["state"] == "PAUSED"
    # A later agent of the same pipeline, while an earlier one works on a newer run, is WAITING for it.
    waiting = derive_state(st("test_case_design", "IDLE", run_id="R0"), {"requirements": running})
    assert waiting == {"state": "WAITING", "last_result": None, "waiting_for": "requirements"}
    done = st("test_execution", "PASSED", finished_at=now_iso())
    assert derive_state(done, {}, t)["state"] == "COMPLETED"
    assert derive_state(done, {}, t + timedelta(seconds=31)) == {"state": "READY", "last_result": "COMPLETED", "waiting_for": None}
    assert derive_state(st("test_execution", "FAILED"), {})["state"] == "FAILED"
    assert derive_state(st("reporting", "IDLE"), {})["state"] == "READY"


def test_registry_only_accepts_registered_commands_and_traceable_parameters():
    assert registry.validate("format_disk", {}, "format the disk") is None
    assert registry.validate("confirm", {}, "yes") is None  # not offered to the AI
    assert registry.validate("run_test_case", {"test_case_id": 1280}, "run the checkout test") == {}
    assert registry.validate("run_test_case", {"test_case_id": 1280}, "run test case 1280") == {"test_case_id": 1280}
    assert registry.validate("open_page", {"page": "/etc/passwd"}, "open the passwords page") == {}
    assert registry.validate("open_page", {"page": "execution history"}, "show execution history") == {"page": "history"}
    assert registry.validate("select_item", {"position": 2}, "pick the second one") == {"position": 2}
    assert registry.validate("run_performance", {"duration_s": 300}, "load test for five minutes") == {"duration_s": 300}
    assert registry.validate("search_work_items", {"text": "checkout"}, "find stories about login") == {}
    destructive = {c.id for c in registry.COMMANDS if c.destructive}
    assert {"create_defects", "save_test_cases", "delete_test_data", "archive_script", "switch_tfs"} <= destructive
    catalog = registry.ai_catalog()
    assert "run_test_case" in catalog and "browser_action" not in catalog and "confirm:" not in catalog


def test_health_summary_is_cached_and_reports_real_checks(client):
    first = client.get("/api/health/summary").json()
    keys = [i["key"] for i in first["items"]]
    assert keys == ["tfs", "ai", "playwright", "speech"]
    assert {i["status"] for i in first["items"]} <= {"PASS", "FAIL", "WARN", "NOT_CONFIGURED", "RUNNING"}
    again = client.get("/api/health/summary").json()
    assert [i["checked_at"] for i in again["items"]] == [i["checked_at"] for i in first["items"]]  # from the cache
    assert first["ttl_seconds"] == 300
