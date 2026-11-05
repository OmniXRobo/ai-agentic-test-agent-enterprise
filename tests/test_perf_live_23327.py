"""23.3.27: the live dashboard of a running test. Every figure comes from the results file JMeter is writing: the
unit tests feed a synthetic results file and check the sums, rolling percentiles, open seconds and planned users;
the real run polls the live endpoint while JMeter runs and checks what it shows against what the server received."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from qa_platform.performance import live as L
from qa_platform.performance.jtl import percentile
from tests import site_server
from tests.test_perf_workload_23326 import FINAL, _jmeter, _plan, _wait

HEADER = "timeStamp,elapsed,label,responseCode,responseMessage,threadName,success,failureMessage,bytes,allThreads\n"
T0 = 1_700_000_000_000


def row(t_ms: int, elapsed: int, label: str = "Home", ok: bool = True, threads: int = 2, code: str = "200",
        failure: str = "") -> str:
    return f"{T0 + t_ms},{elapsed},{label},{code},{'OK' if ok else 'Bad'},T 1-1,{'true' if ok else 'false'},{failure},10,{threads}\n"


def test_figures_come_from_the_results_file_only(tmp_path: Path):
    jtl = tmp_path / "results.jtl"
    m = L.LiveMetrics(jtl, started_ms=T0, transactions={"Sign in"}, window_s=10,
                      targets={"p95_ms": 250, "max_error_pct": 5, "p99_ms": None}, rate_target=4)
    m.poll()  # no file yet: nothing, and no error
    snap = m.snapshot(T0 + 500)
    assert snap["samples"] == 0 and snap["timeline"] == [] and snap["window"]["p95_ms"] is None
    assert snap["avg_ms"] is None and snap["requests"] == []

    lines = [HEADER]
    for sec in range(20):  # 2 requests per second, 100 and 200+sec ms; second 7 has no samples at all
        if sec == 7:
            continue
        lines += [row(sec * 1000 + 10, 100), row(sec * 1000 + 500, 200 + sec, label="Login")]
    lines += [row(3000, 900, label="Sign in")]  # a transaction: counted apart
    lines += [row(15_200, 50, label="Login", ok=False, code="500", failure="Internal error")]
    data = "".join(lines)
    jtl.write_text(data[:-7], encoding="utf-8")  # the last row is still being written: not read yet
    m.poll()
    assert m.samples == 38 and m.tx_samples == 1
    with jtl.open("a", encoding="utf-8") as fh:
        fh.write(data[-7:])
    m.poll()
    snap = m.snapshot(T0 + 21_000)
    assert snap["samples"] == 39 and snap["errors"] == 1 and snap["transaction_samples"] == 1
    assert snap["error_pct"] == round(100 / 39, 2) and snap["active_users"] == 2
    tl = {p["t"]: p for p in snap["timeline"]}
    assert snap["bucket_s"] == 1 and len(tl) == 20
    assert tl[7]["throughput"] == 0 and tl[7]["avg_ms"] == 0.0  # a second without samples shows as 0, not skipped
    assert tl[3]["throughput"] == 2 and tl[3]["avg_ms"] == 151.5  # the 900 ms transaction is not in it
    assert tl[15]["errors"] == 1 and tl[15]["throughput"] == 3
    assert not tl[10]["open"] and tl[19]["open"] and tl[14]["open"]  # the last 5 s still fill up
    assert tl[10]["p95_ms"] == 210  # nearest rank of [100, 210]

    w = snap["window"]  # requests that started in the 10 s before the newest sample ended (19.719 s)
    vals = sorted([100] * 10 + [200 + s for s in range(10, 20)] + [50])
    assert w["samples"] == len(vals) and w["throughput"] == round(len(vals) / 10, 2)
    assert w["p95_ms"] == percentile(vals, 95) and w["p50_ms"] == percentile(vals, 50)
    assert w["error_pct"] == round(100 / len(vals), 2)
    watch = {x["name"]: x for x in snap["watch"]}
    assert set(watch) == {"95th percentile", "Error rate", "Request rate target"}  # p99 had no target (None)
    assert watch["95th percentile"]["ok"] and watch["95th percentile"]["unit"] == "ms"
    assert watch["Error rate"]["ok"] and watch["Request rate target"]["ok"] is (w["throughput"] >= 0.95 * 4)
    reqs = {r["label"]: r for r in snap["requests"]}
    assert reqs["Sign in"]["transaction"] and reqs["Sign in"]["samples"] == 1 and reqs["Sign in"]["window_p95_ms"] is None
    assert reqs["Login"]["errors"] == 1 and reqs["Login"]["max_ms"] == 219 and reqs["Home"]["errors"] == 0
    assert snap["top_errors"] == [{"error": "500 Internal error", "count": 1}]
    assert snap["recent_errors"][0]["label"] == "Login" and snap["recent_errors"][0]["t"] == 15.2

    # closed seconds keep only their 95th percentile; the summary kept on the run is unchanged in shape
    assert m.secs[2].values is None and m.secs[2].p95 == 202 and m.secs[19].values is not None
    s = m.summary()
    assert set(s) == {"samples", "errors", "error_pct", "avg_ms", "threads", "recent_throughput"}
    assert s["samples"] == 39


def test_long_runs_are_merged_to_at_most_600_points(tmp_path: Path):
    jtl = tmp_path / "results.jtl"
    jtl.write_text(HEADER + "".join(row(sec * 1000, 100) for sec in range(0, 1500)), encoding="utf-8")
    m = L.LiveMetrics(jtl, started_ms=T0, window_s=30)
    m.poll()
    snap = m.snapshot(T0 + 1_500_000)
    assert snap["bucket_s"] == 3 and len(snap["timeline"]) == 500
    assert all(p["throughput"] == 1.0 for p in snap["timeline"])  # 3 requests in 3 s
    assert snap["window"]["samples"] == 30  # the window keeps the last 30 s of samples
    assert sum(1 for s in m.secs.values() if s.values is not None) <= L.OPEN_S + 2


def test_planned_users_follow_the_thread_groups():
    m = L.LiveMetrics(Path("x.jtl"), started_ms=0, planned=[
        {"users": 4, "ramp_up_s": 10, "delay_s": 0, "duration_s": 30},
        {"users": 2, "ramp_up_s": 0, "delay_s": 20, "duration_s": 10}])
    assert m.planned_users_at(0) == 0 and m.planned_users_at(5) == 2 and m.planned_users_at(15) == 4
    assert m.planned_users_at(25) == 6 and m.planned_users_at(30) == 0
    curve = m.planned_curve()
    assert curve[0] == [0.0, 0.0] and [10.0, 4.0] in curve and [20.0, 4.0] in curve and [20.0, 6.0] in curve
    assert curve[-1] == [30.0, 0.0]
    # a group whose duration depends on loop counts: no planned curve is drawn
    assert L.LiveMetrics(Path("x.jtl"), started_ms=0, planned=[
        {"users": 4, "ramp_up_s": 0, "delay_s": 0, "duration_s": 0}]).planned_curve() == []


def test_unknown_run_is_404(client):
    r = client.get("/api/performance/runs/PRUN-nope/live")
    assert r.status_code == 404 and r.json()["error_code"] == "PERF_RUN_NOT_FOUND"


def test_live_endpoint_while_jmeter_runs(client, site):
    rt = client.app.state.runtime
    _jmeter(rt)
    port = site.rsplit(":", 1)[1]
    plan, _ = _plan(client, port)
    # Think time, so the users do not open thousands of connections (see 23.3.26)
    view = client.get(f"/api/performance/plans/{plan['id']}/script").json()
    work = client.post("/api/performance/script/apply", json={"script": view["script"], "ops": [
        {"op": "add", "parent": "0/0", "type": "constant_timer", "values": {"delay_ms": 300}}]}).json()
    client.put(f"/api/performance/plans/{plan['id']}/script", json={"script": work["script"], "base_version": 1})
    client.put(f"/api/performance/plans/{plan['id']}/workload",
               json={"shape": "load", "users": 2, "ramp_up_s": 2, "hold_s": 14})
    full = client.get(f"/api/performance/plans/{plan['id']}").json()["plan"]
    full["sla"] = {"p95_ms": 5000, "max_error_pct": 1}
    assert client.put(f"/api/performance/plans/{plan['id']}", json=full).status_code == 200

    site_server.PERF_SEEN["hits"].clear()
    run_id = client.post(f"/api/performance/plans/{plan['id']}/run").json()["run_id"]
    first = client.get(f"/api/performance/runs/{run_id}/live").json()
    assert first["ok"] and first["run"]["run_id"] == run_id and first["run"]["state"] in ("QUEUED", "VALIDATING", "RUNNING")
    seen = None
    deadline = time.time() + 120
    while time.time() < deadline:
        data = client.get(f"/api/performance/runs/{run_id}/live").json()
        d = data["dashboard"]
        if data["run"]["state"] in FINAL:
            break
        if d and d["samples"] >= 10 and d["elapsed_s"] >= 6:
            seen = data
            break
        time.sleep(0.5)
    assert seen, "the dashboard showed no samples while JMeter ran"
    d = seen["dashboard"]
    assert seen["run"]["state"] in ("RUNNING", "STOPPING")
    assert d["planned_duration_s"] == 16 and 0 < d["progress_pct"] < 100
    assert d["active_users"] in (1, 2) and d["planned_users_now"] == 2
    assert d["planned_curve"][0] == [0.0, 0.0] and [2.0, 2.0] in d["planned_curve"]
    assert d["transaction_samples"] > 0  # "Sign in" is counted apart from the requests
    labels = {r["label"]: r for r in d["requests"]}
    assert {"Session", "Profile", "Sign in"} <= set(labels) and labels["Sign in"]["transaction"]
    assert d["samples"] == labels["Session"]["samples"] + labels["Profile"]["samples"]
    assert d["samples"] <= len(site_server.PERF_SEEN["hits"])  # never more than the server received
    assert d["errors"] == 0 and d["window"]["p95_ms"] > 0 and d["timeline"]
    assert {x["name"] for x in d["watch"]} == {"95th percentile", "Error rate"} and all(x["ok"] for x in d["watch"])
    if seen["generator"] is not None:
        assert "samples" in seen["generator"] and "flags" in seen["generator"]

    run = _wait(client, run_id)
    assert run["state"] == "COMPLETED", run.get("error")
    after = client.get(f"/api/performance/runs/{run_id}/live").json()
    assert after["run"]["state"] == "COMPLETED" and after["dashboard"] is None  # the page links to the full results
    assert run["metrics"]["overall"]["samples"] >= d["samples"]
    assert run_id not in rt.performance._dash


def test_early_in_a_run_the_rate_is_over_the_seconds_seen_not_the_whole_window(tmp_path: Path):
    jtl = tmp_path / "results.jtl"
    jtl.write_text(HEADER + row(0, 100) + row(1000, 100) + row(1500, 100) + row(1900, 100), encoding="utf-8")
    m = L.LiveMetrics(jtl, started_ms=T0, window_s=30)
    m.poll()
    w = m.snapshot(T0 + 2500)["window"]
    assert w["seconds"] == 2 and w["throughput"] == 2.0  # 4 requests in 2 s, not 4 in 30 s
    assert m.summary()["recent_throughput"] == 2.0


def test_a_queued_run_that_starts_while_read_has_no_position(client, monkeypatch):
    """The run row is read as QUEUED, then the queue shows it running (it started in between): no error."""
    rt = client.app.state.runtime
    rt.db.execute("INSERT INTO perf_runs (run_id, plan_id, kind, name, status, state, queued_at, started_at, folder)"
                  " VALUES ('PERF-race', 'p', 'load', 'Race', 'RUNNING', 'QUEUED', '2026-11-05T00:00:00', '', ?)", (str(Path("x")),))
    monkeypatch.setattr(rt.performance, "queue", lambda: [{"run_id": "PERF-race", "state": "RUNNING"}])
    r = client.get("/api/performance/runs/PERF-race/live")
    assert r.status_code == 200 and r.json()["run"]["position"] is None
