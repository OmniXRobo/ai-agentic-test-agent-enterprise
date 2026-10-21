from __future__ import annotations

import time

from qa_platform.agents.registry import AGENTS


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_agents_endpoint_lists_all_agents_waiting(client):
    data = client.get("/api/agents").json()
    assert len(data["agents"]) == len(AGENTS) == 18
    assert {a["status"] for a in data["agents"]} == {"WAITING"}
    assert len({a["robot"] for a in data["agents"]}) == 18  # every agent has its own robot


def test_unknown_api_route_returns_structured_json(client):
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert body["ok"] is False and body["error_code"] == "NOT_FOUND"


def test_validation_error_is_structured(client):
    r = client.get("/api/events?limit=0")
    assert r.status_code == 422
    assert r.json()["error_code"] == "VALIDATION_FAILED"


def test_missing_run_is_structured(client):
    r = client.get("/api/runs/RUN-NOPE")
    assert r.status_code == 404
    assert r.json()["error_code"] == "RUN_NOT_FOUND"


def test_settings_reject_secrets_and_bad_values(client):
    r = client.patch("/api/settings", json={"tfs": {"online": {"pat": "x"}}})
    assert r.status_code == 400 and r.json()["error_code"] == "SECRET_IN_SETTINGS"
    r = client.patch("/api/settings", json={"ui": {"theme": "purple"}})
    assert r.status_code == 400
    r = client.patch("/api/settings", json={"ui": {"theme": "light"}})
    assert r.status_code == 200 and r.json()["settings"]["ui"]["theme"] == "light"
    assert client.get("/api/settings").json()["settings"]["ui"]["theme"] == "light"


def test_diagnostics_run_produces_real_events_and_history(client):
    r = client.post("/api/diagnostics/run")
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    for _ in range(100):
        run = client.get(f"/api/runs/{run_id}")
        if run.status_code == 200 and run.json()["run"]["status"] != "RUNNING":
            break
        time.sleep(0.1)
    detail = run.json()
    names = [c["name"] for c in detail["run"]["summary"]["checks"]]
    assert "Python runtime" in names and "Database" in names
    events = [e["name"] for e in detail["events"]]
    assert events[0] == "agent.started" and events[-1] == "agent.completed"
    orch = next(a for a in client.get("/api/agents").json()["agents"] if a["id"] == "orchestration")
    assert orch["status"] == "PASSED"
    assert client.get("/api/notifications").json()["unread"] >= 1


def test_dashboard_summary_has_no_fabricated_tfs_numbers(client):
    s = client.get("/api/dashboard/summary").json()
    assert s["tfs"]["connected"] is False


def test_static_dashboard_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "AI Agentic Test Agent" in r.text
