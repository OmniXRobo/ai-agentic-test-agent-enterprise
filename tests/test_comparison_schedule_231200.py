"""23.12.0: Integration Comparison in Scheduled Runs and on its Agent Workflow card; confirmed mismatches become Defect
Review drafts (never published without approval). Plus the IPv6 / host:port parsing of API targets."""

from __future__ import annotations

import time

from qa_platform.apitesting import safety
from qa_platform.apitesting.client import build_base_url
from tests.test_integration_workspace_23800 import A_CSV, B_CSV, analyze, approve_all, new, save, upload_csv


def _comparison(client, mismatch: bool):
    pid, apps = new(client, "Order System", "Sales Portal")
    upload_csv(client, apps[0], A_CSV)
    upload_csv(client, apps[1], B_CSV.replace("83.33%", "85.00%") if mismatch else B_CSV)
    save(client, pid, approve_all(analyze(client, pid)["config"]))
    return pid


def _events(rt, agent_id):
    return [e for e in rt.bus.history() if e.get("agent_id") == agent_id] if hasattr(rt.bus, "history") else []


def test_comparison_is_schedulable_and_mismatches_become_review_drafts(client, rt):
    pid = _comparison(client, mismatch=True)
    items = client.get("/api/schedules/scripts").json()["items"]
    mine = [i for i in items if i["id"] == f"CMP:{pid}"]
    assert mine and mine[0]["page"] == "Integration Comparison"
    r = client.post("/api/schedule-runs", json={"script_ids": [f"CMP:{pid}"], "name": "Nightly comparison",
                                                "browsers": ["chromium"]})
    assert r.status_code in (200, 201, 202), r.text
    end = time.time() + 60
    while time.time() < end:
        hist = client.get("/api/schedules/history?limit=5").json()["items"]
        if hist and hist[0]["status"] not in ("RUNNING", "QUEUED"):
            break
        time.sleep(0.3)
    item = hist[0]["items"][0]
    assert item["status"] == "FAILED" and "mismatch" in item["error"] and item["mode"] == "comparison"
    assert "draft" in item["error"]
    drafts = client.get("/api/defects/drafts?source=comparison&status=open").json()
    rows = drafts.get("drafts") or drafts.get("items") or []
    assert rows and all(d["status"] in ("draft", "needs_review") for d in rows)
    assert all(not d.get("tfs_id") for d in rows)  # nothing is published without approval


def test_matching_comparison_passes_and_manual_run_reports_drafts(client, rt):
    pid = _comparison(client, mismatch=False)
    out = client.post(f"/api/comparison/workspace/{pid}/run").json()["run"]
    assert out["result"] in ("PASS", "PASS*", "ACCEPTED") and out["review_drafts"] == []
    pid2 = _comparison(client, mismatch=True)
    out = client.post(f"/api/comparison/workspace/{pid2}/run").json()["run"]
    assert out["result"] == "FAIL" and len(out["review_drafts"]) >= 1


def test_ipv6_and_host_port_targets():
    assert build_base_url({"protocol": "http", "host": "fd00::10", "port": 8080, "base_path": "/api"}) == "http://[fd00::10]:8080/api"
    assert build_base_url({"protocol": "https", "host": "10.1.2.3"}) == "https://10.1.2.3"
    assert safety.split_host_port("[fd00::10]:8443", "https") == ("fd00::10", 8443)
    pol = safety.Policy(["[fd00::10]:8443", "fd00:1::/64"], resolver=lambda h, p: [h])
    assert pol.check_url("https://[fd00::10]:8443/x")["port"] == 8443
    assert pol.check_url("http://[fd00:1::5]/x")["host"] == "fd00:1::5"
