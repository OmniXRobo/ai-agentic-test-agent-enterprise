"""Stage 8: analytics from recorded results, HTML/PDF reports, dashboard summary."""

from __future__ import annotations

import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from qa_platform.clock import now
from qa_platform.credentials import MemoryBackend
from qa_platform.main import create_app
from qa_platform.reports import charts
from qa_platform.storage.db import dumps
from tests.fake_ado import PAT, FakeAzureDevOps
from tests.test_automation import HAVE_PLAYWRIGHT, _login_script


def _ts(days_ago: int = 0, minutes: int = 0) -> str:
    return (now() - timedelta(days=days_ago, minutes=minutes)).isoformat(timespec="seconds")


def _result(rt, run_id, script_id, status, days_ago=0, minutes=0, test_case_id="1280", error=None):
    rt.db.execute("INSERT INTO execution_results (run_id, script_id, test_case_id, status, started_at, failed_step, error)"
                  " VALUES (?,?,?,?,?,?,?)", (run_id, script_id, test_case_id, status, _ts(days_ago, minutes),
                                             1 if status == "FAILED" else None, error))


def _perf(rt, run_id, plan_id, status, p95, err, users, days_ago=0, sla="PASS", requests=None):
    overall = {"samples": 100, "errors": int(err), "avg_ms": p95 / 2, "p90_ms": p95 * 0.9, "p95_ms": p95, "p99_ms": p95 * 1.2,
               "error_pct": err, "throughput": 20.0, "label": "TOTAL"}
    metrics = {"overall": overall, "requests": requests or [], "peak_threads": users,
               "timeline": [{"t": 0, "throughput": 10, "errors": 0, "avg_ms": 100, "threads": 1},
                            {"t": 30, "throughput": 20, "errors": 1, "avg_ms": 140, "threads": users}]}
    rt.db.execute("INSERT INTO perf_runs (run_id, plan_id, kind, name, status, started_at, folder, metrics, sla)"
                  " VALUES (?,?,?,?,?,?,?,?,?)", (run_id, plan_id, "load", "Checkout", status, _ts(days_ago), str(rt.paths.root),
                                                 dumps(metrics), dumps({"status": sla, "checks": [], "failed": []})))


def _req(label, p95, err=0.0):
    return {"label": label, "samples": 50, "errors": 0, "avg_ms": p95 / 2, "p90_ms": p95 * 0.9, "p95_ms": p95,
            "p99_ms": p95 * 1.1, "error_pct": err, "throughput": 10.0}


@pytest.fixture()
def env(paths):
    ado = FakeAzureDevOps()
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport())
    with TestClient(app) as c:
        c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                "make_active": True})
        yield c, ado, c.app.state.runtime


def test_empty_install_shows_no_invented_figures(client):
    s = client.get("/api/analytics/summary").json()
    assert s["functional"] == {"scripts_run": 0, "passed": 0, "failed": 0, "pass_rate": None}
    assert s["performance"]["runs"] == 0 and s["performance"]["sla_pass_rate"] is None
    assert s["coverage"]["tfs_connected"] is False and "automation_coverage" not in s["coverage"]
    assert s["acceptance_criteria"]["available"] is False and s["acceptance_criteria"]["reason"]
    f = client.get("/api/analytics/functional?days=7").json()
    assert f["executions"] == 0 and f["pass_rate"] is None and len(f["trend"]) == 7 and f["flaky"] == []
    p = client.get("/api/analytics/performance").json()
    assert p["runs"] == [] and p["max_users"] == 0


def test_functional_analytics_from_recorded_results(env):
    c, _ado, rt = env
    login = rt.automation.scripts.save(_login_script("http://shop"))
    other = _login_script("http://shop")
    other.name, other.test_case_id = "Checkout works", "1300"
    other = rt.automation.scripts.save(other)
    # login flips PASS/FAIL/PASS (flaky); checkout passes; one old run outside the window
    _result(rt, "RUN-A", login.id, "PASSED", days_ago=2)
    _result(rt, "RUN-B", login.id, "FAILED", days_ago=1, error="Verify welcome failed")
    _result(rt, "RUN-C", login.id, "PASSED", days_ago=0, minutes=30)
    _result(rt, "RUN-C", other.id, "PASSED", days_ago=0, minutes=30)
    _result(rt, "RUN-OLD", other.id, "FAILED", days_ago=40)
    rt.db.execute("INSERT INTO healing_events (timestamp, run_id, script_id, step_index, original_selector, healed_selector,"
                  " confidence, result, applied) VALUES (?,?,?,?,?,?,?,?,?)",
                  (_ts(1), "RUN-B", login.id, 6, "#login-btn", "#signin", 0.92, "healed", 1))
    f = c.get("/api/analytics/functional?days=14").json()
    assert f["executions"] == 4 and f["passed"] == 3 and f["pass_rate"] == 75.0 and f["failure_rate"] == 25.0
    assert sum(t["passed"] + t["failed"] for t in f["trend"]) == 4 and f["trend"][-1]["date"] == now().date().isoformat()
    assert [x["name"] for x in f["flaky"]] == ["Login works"] and f["flaky"][0]["flip_rate"] == 100.0
    assert f["healing"]["total"] == 1 and f["healing"]["by_script"][0]["applied"] == 1
    # coverage uses the live TFS test case count (one Test Case in the fake project)
    cov = f["coverage"]
    assert cov["tfs_connected"] and cov["tfs_test_cases"] == 1 and cov["automated_test_cases"] == 2
    assert cov["automation_coverage"] == 100.0 and cov["not_automated"] == 0
    s = c.get("/api/analytics/summary").json()
    assert s["functional"] == {"scripts_run": 2, "passed": 2, "failed": 0, "pass_rate": 100.0}  # latest result per script
    assert s["tfs"]["connected"] and s["tfs"]["requirement"] == 1
    assert s["acceptance_criteria"] == {"available": True, "criteria": 1, "stories_with_criteria": 1, "stories": 1}


def test_performance_analytics_trends_sla_and_transactions(client, rt):
    _perf(rt, "PERF-1", "PERF-AAAAAAAAAA", "PASSED", 400, 0.0, 20, days_ago=3, requests=[_req("Home", 300), _req("Pay", 500)])
    _perf(rt, "PERF-2", "PERF-AAAAAAAAAA", "FAILED", 2400, 4.0, 50, days_ago=1, sla="FAIL",
          requests=[_req("Home", 320), _req("Pay", 2600, 8.0)])
    _perf(rt, "PERF-3", "PERF-BBBBBBBBBB", "PASSED", 200, 0.0, 10, days_ago=0, sla="NOT_DEFINED")
    p = client.get("/api/analytics/performance?days=30").json()
    assert [r["run_id"] for r in p["runs"]] == ["PERF-1", "PERF-2", "PERF-3"]
    assert p["runs"][1]["p95_ms"] == 2400 and p["runs"][1]["peak_users"] == 50
    assert p["sla"] == {"pass": 1, "fail": 1, "not_defined": 1, "pass_rate": 50.0} and p["max_users"] == 50
    one = client.get("/api/analytics/performance?plan_id=PERF-AAAAAAAAAA").json()
    pay = next(t for t in one["transactions"] if t["label"] == "Pay")
    assert pay["p95_ms"] == 2600 and pay["previous_p95_ms"] == 500
    s = client.get("/api/analytics/summary").json()["performance"]
    assert s["runs"] == 3 and s["failed"] == 1 and s["sla_pass_rate"] == 50.0


def test_report_generation_html_pdf_events_and_lifecycle(env):
    c, _ado, rt = env
    script = _login_script("http://shop")
    script.name, script.test_case_id = "Checkout <b>flow</b>", "1280"
    script = rt.automation.scripts.save(script)
    _result(rt, "RUN-1", script.id, "FAILED", error="Pay button missing")
    _perf(rt, "PERF-1", "PERF-AAAAAAAAAA", "FAILED", 2400, 4.0, 50, sla="FAIL", requests=[_req("Pay", 2400, 4.0)])
    r = rt.reports.start({"scope": "both", "days": 7, "user_story_id": 118}, background=False)
    assert r["status"] == "ready" and r["has_html"], r
    assert r["summary"]["executions"] == 1 and r["summary"]["perf_runs"] == 1
    page = c.get(f"/api/reports/{r['id']}/html").text
    for section in ("Executive summary", "Executions per day", "User story 118: Checkout with saved card", "Pay with saved card",
                    "Checkout proceeds to payment", "Execution results", "Defects", "Self-healing",
                    "Performance test summary", "Load profile", "P95", "P99", "Transaction results", "SLA compliance",
                    "Performance defects", "AI analysis", "Active users"):
        assert section in page, section
    assert "Checkout &lt;b&gt;flow&lt;/b&gt;" in page and "<b>flow</b>" not in page  # names are escaped
    assert "Pay button missing" in page and "<svg" in page and "<script" not in page
    if HAVE_PLAYWRIGHT:
        assert r["has_pdf"]
        pdf = c.get(f"/api/reports/{r['id']}/pdf")
        assert pdf.headers["content-type"] == "application/pdf" and pdf.content[:4] == b"%PDF"
    events = [e["name"] for e in rt.db.query("SELECT name FROM events WHERE run_id = ?", (r["id"],))]
    assert "report.started" in events and "report.completed" in events
    assert any(n["title"] == "Report ready" for n in rt.notifications.list())
    assert [x["id"] for x in c.get("/api/reports").json()["items"]] == [r["id"]]
    assert c.delete(f"/api/reports/{r['id']}").json()["ok"]
    assert c.get(f"/api/reports/{r['id']}").status_code == 404
    assert not (rt.paths.reports / r["id"]).exists()


def test_report_without_tfs_still_renders_and_says_why(client, rt):
    r = rt.reports.start({"scope": "functional", "days": 30}, background=False)
    assert r["status"] == "ready"
    page = client.get(f"/api/reports/{r['id']}/html").text
    assert "Coverage against TFS is not shown" in page and "There are no automated tests in this scope." in page


def test_report_api_validation(client, rt):
    assert client.post("/api/reports", json={"scope": "everything"}).status_code == 422
    assert client.post("/api/reports", json={"days": 0}).status_code == 422
    assert client.get("/api/reports/RPT-missing/pdf").status_code == 404
    accepted = client.post("/api/reports", json={"scope": "performance", "days": 3})
    report = accepted.json()["report"]
    assert accepted.status_code == 202 and report["status"] in ("running", "ready")
    deadline = time.time() + 30
    while client.get(f"/api/reports/{report['id']}").json()["report"]["status"] == "running" and time.time() < deadline:
        time.sleep(0.1)
    assert client.get(f"/api/reports/{report['id']}").json()["report"]["status"] == "ready"


def test_interrupted_report_is_marked_failed_on_start(paths):
    from qa_platform.runtime import build_runtime
    first = build_runtime(paths, secret_backend=MemoryBackend())
    first.db.execute("INSERT INTO reports (id, title, scope, status, created_at) VALUES ('RPT-X', 'QA report', 'both', 'running', '2026-01-01')")
    first.close()
    again = build_runtime(paths, secret_backend=MemoryBackend())
    r = again.reports.get("RPT-X")
    assert r["status"] == "failed" and "Interrupted" in r["error"]
    again.close()


def test_charts_are_plain_svg_without_overlapping_labels():
    trend = [{"date": f"2026-01-{d:02d}", "passed": d % 3, "failed": d % 2} for d in range(1, 31)]
    svg = charts.stacked_bars(trend, [("passed", "Passed", "#16a34a"), ("failed", "Failed", "#dc2626")])
    assert svg.count('class="axis"') <= 10 and "Passed" in svg and "<script" not in svg
    runs = [{"started_at": "2026-01-02T10:00:00", "name": "A", "p95_ms": 120}, {"started_at": "2026-01-03T10:00:00", "name": "A"}]
    assert "No data" not in charts.run_chart(runs, "p95_ms", "P95", "#2563eb")
    assert "No data" in charts.timeline_chart([], "avg_ms", "Avg", "#2563eb")
