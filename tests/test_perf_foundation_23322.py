"""23.3.22: performance scenarios, environments with load limits, run queue, execution state vs verdict, pre-run
checks, run snapshot, load-generator monitoring and recovery after a restart (real JMeter when it is installed)."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from qa_platform.performance import generator, jtl, preflight, tools
from qa_platform.performance.jmx import build_jmx
from qa_platform.performance.models import HttpRequest, LoadProfile, PerfEnvironment, PerfPlan, Sla
from qa_platform.storage.db import dumps

FINAL = ("COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "SKIPPED")


def _plan(base: str, name: str = "Shop", users: int = 2, duration: int = 10, sla: Sla | None = None,
          think: int = 200) -> dict:
    return PerfPlan(name=name, kind="load", requests=[
        HttpRequest(name="Login page", url=f"{base}/login.html", expect_status="200"),
        HttpRequest(name="Item API", url=f"{base}/api/items/5", expect_text="Item 5"),
    ], profile=LoadProfile(users=users, ramp_up_s=1, duration_s=duration, think_time_ms=think),
        sla=sla or Sla()).model_dump()


def _jmeter(rt) -> bool:
    return rt.performance.environment()["ready"]


def _wait(client, run_id: str, states=FINAL, timeout: float = 180) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/performance/runs/{run_id}").json()["run"]
        if run["state"] in states:
            return run
        time.sleep(0.3)
    raise AssertionError(f"{run_id} stayed {run['state']}")


def _wait_samples(rt, run_id: str, timeout: float = 60) -> None:
    path = rt.paths.performance / run_id / "results.jtl"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists() and path.stat().st_size > 400:
            return
        time.sleep(0.2)
    raise AssertionError("JMeter wrote no samples")


# ---- scenarios and environments ------------------------------------------------------------------------------

def test_scenarios_create_edit_clone_archive_search(client):
    env = client.post("/api/performance/environments", json={
        "name": "QA", "base_url": "http://127.0.0.1:1", "allowed_hosts": ["http://127.0.0.1:1/x", "127.0.0.1"],
        "max_users": 20}).json()["environment"]
    assert env["allowed_hosts"] == ["127.0.0.1"] and env["id"].startswith("ENV-")

    # A plan saved on its own gets its own scenario, so it is never lost from the Scenarios page.
    plan = client.post("/api/performance/plans", json=_plan("http://127.0.0.1:1")).json()["plan"]
    auto = client.get("/api/performance/scenarios").json()["items"]
    assert [s["plan_id"] for s in auto] == [plan["id"]] and auto[0]["name"] == "Shop"

    r = client.post("/api/performance/scenarios", json={
        "name": "Login Search Logout", "description": "Peak hour journey", "project": "Shop",
        "environment_id": env["id"], "user_story_id": "1234", "acceptance_criteria": "p95 under 2 s",
        "tags": ["checkout"]})
    assert r.status_code == 201
    sc = r.json()["scenario"]
    assert sc["plan_id"] is None and sc["environment_name"] == "QA" and sc["kind"] is None
    # Create the scenario's script from the scenario.
    p2 = client.post(f"/api/performance/plans?scenario_id={sc['id']}", json=_plan("http://127.0.0.1:1", "LSL")).json()["plan"]
    detail = client.get(f"/api/performance/scenarios/{sc['id']}").json()["scenario"]
    assert detail["plan_id"] == p2["id"] and detail["kind"] == "load" and detail["requests"] == 2
    assert len(client.get("/api/performance/scenarios").json()["items"]) == 2  # no extra scenario for p2

    upd = client.put(f"/api/performance/scenarios/{sc['id']}", json={**detail, "description": "Evening peak"}).json()
    assert upd["scenario"]["description"] == "Evening peak" and upd["scenario"]["plan_id"] == p2["id"]

    clone = client.post(f"/api/performance/scenarios/{sc['id']}/clone", json={}).json()["scenario"]
    assert clone["name"] == "Copy of Login Search Logout" and clone["plan_id"] not in (None, p2["id"])
    assert clone["environment_id"] == env["id"] and clone["user_story_id"] == "1234"

    found = client.get("/api/performance/scenarios", params={"q": "checkout evening"}).json()["items"]
    assert sorted(s["id"] for s in found) == sorted([sc["id"], clone["id"]])  # the copy keeps tags and description
    assert [s["id"] for s in client.get("/api/performance/scenarios", params={"q": "copy of"}).json()["items"]] == [clone["id"]]
    assert len(client.get("/api/performance/scenarios", params={"environment_id": env["id"]}).json()["items"]) == 2

    client.post(f"/api/performance/scenarios/{sc['id']}/archive", json={"archived": True})
    assert sc["id"] not in [s["id"] for s in client.get("/api/performance/scenarios").json()["items"]]
    assert [s["id"] for s in client.get("/api/performance/scenarios?archived=true").json()["items"]] == [sc["id"]]
    assert client.post(f"/api/performance/scenarios/{sc['id']}/run").json()["error_code"] in (
        "PERF_SCENARIO_ARCHIVED", "JMETER_NOT_FOUND", "JAVA_NOT_FOUND")
    client.post(f"/api/performance/scenarios/{sc['id']}/archive", json={"archived": False})

    # An environment used by a scenario cannot be deleted.
    assert client.delete(f"/api/performance/environments/{env['id']}").json()["error_code"] == "PERF_ENV_IN_USE"
    # Deleting a script keeps its scenario (archived, without a script) and the run history.
    client.delete(f"/api/performance/plans/{plan['id']}")
    gone = client.get(f"/api/performance/scenarios/{auto[0]['id']}").json()["scenario"]
    assert gone["archived"] and gone["plan_id"] is None


def test_pre_run_checks_block_hosts_and_load_outside_the_environment(client, site):
    env = client.post("/api/performance/environments", json={
        "name": "Demo", "allowed_hosts": ["www.saucedemo.com"], "max_users": 1, "max_duration_s": 60,
        "third_party": True}).json()["environment"]
    sc = client.post("/api/performance/scenarios", json={"name": "Wrong host", "environment_id": env["id"]}).json()["scenario"]
    client.post(f"/api/performance/plans?scenario_id={sc['id']}", json=_plan(site, users=3, duration=120))
    check = client.post(f"/api/performance/scenarios/{sc['id']}/check").json()
    by = {c["id"]: c for c in check["checks"]}
    assert check["status"] == "FAIL"
    assert by["hosts"]["status"] == "FAIL" and "127.0.0.1" in by["hosts"]["detail"]
    assert by["users"]["status"] == "FAIL" and "limit for Demo is 1" in by["users"]["detail"]
    assert by["duration"]["status"] == "FAIL" and by["third_party"]["status"] == "WARN"
    assert by["jmx"]["status"] == "PASS" and by["disk"]["status"] == "PASS" and by["plugins"]["status"] == "PASS"

    rt = client.app.state.runtime
    if not _jmeter(rt):
        return
    run_id = client.post(f"/api/performance/scenarios/{sc['id']}/run").json()["run_id"]
    run = _wait(client, run_id)
    assert run["state"] == "FAILED" and run["verdict"] is None and run["status"] == "FAILED"
    assert "Pre-run checks failed" in run["error"] and run["checks"]["status"] == "FAIL"
    assert "results.jtl" not in run["files"] and "plan.jmx" not in run["files"]  # nothing was sent


def test_jmx_validation_and_versions():
    assert preflight.validate_jmx("<nope") and "not valid XML" in preflight.validate_jmx("<nope")[0]
    assert "no thread group" in " ".join(preflight.validate_jmx("<jmeterTestPlan><hashTree><TestPlan/></hashTree></jmeterTestPlan>"))
    good = build_jmx(PerfPlan(name="x", requests=[HttpRequest(name="a", url="http://h/")]))
    assert preflight.validate_jmx(good) == []
    assert preflight.java_major('openjdk version "17.0.20" 2026-07-15') == 17
    assert preflight.java_major('java version "1.8.0_401"') == 8
    no_env = preflight.target_checks(PerfPlan(name="x", requests=[HttpRequest(name="a", url="http://h/")],
                                              profile=LoadProfile(users=60)), None)
    assert {c["id"]: c["status"] for c in no_env} == {"environment": "WARN", "users": "FAIL"}
    ok = preflight.target_checks(PerfPlan(name="x", requests=[HttpRequest(name="a", url="http://H/")]),
                                 PerfEnvironment(name="E", allowed_hosts=["h"], max_users=10))
    assert {c["id"]: c["status"] for c in ok} == {"hosts": "PASS", "users": "PASS"}


# ---- upgrade and recovery ----------------------------------------------------------------------------------

def test_existing_plans_and_runs_are_upgraded_and_restart_recovers(client, tmp_path):
    rt = client.app.state.runtime
    db = rt.db
    plan = PerfPlan(id="PERF-OLD0000001", name="Old plan", requests=[HttpRequest(name="a", url="http://h/")])
    db.execute("INSERT INTO perf_plans (id, name, kind, plan, created_at, updated_at) VALUES (?,?,?,?,?,?)",
               (plan.id, plan.name, plan.kind, plan.model_dump_json(), "2026-01-01", "2026-01-01"))
    folder = tmp_path / "perf"
    folder.mkdir()
    rows = [("R-PASS", "PASSED", {"status": "PASS", "checks": []}, None),
            ("R-SLAFAIL", "FAILED", {"status": "FAIL", "checks": []}, None),
            ("R-ERR", "FAILED", None, "JMeter was not found."),
            ("R-NODEF", "PASSED", {"status": "NOT_DEFINED", "checks": []}, None),
            ("R-STUCK", "RUNNING", None, None)]
    for run_id, status, sla, err in rows:
        db.execute("INSERT INTO perf_runs (run_id, plan_id, kind, name, status, started_at, folder, sla, error)"
                   " VALUES (?,?,?,?,?,?,?,?,?)", (run_id, plan.id, "load", "Old plan", status, "2026-01-01",
                                                   str(folder / run_id), dumps(sla) if sla else None, err))
    # A run that was running with JMeter results on disk, and one waiting in the queue.
    (folder / "R-LIVE").mkdir()
    header = "timeStamp,elapsed,label,responseCode,responseMessage,threadName,dataType,success,failureMessage,bytes,sentBytes,grpThreads,allThreads,URL,Latency,IdleTime,Connect\n"
    (folder / "R-LIVE" / "results.jtl").write_text(header + "1000,50,a,200,OK,t,text,true,,10,10,1,1,http://h/,40,0,1\n"
                                                   "2000,70,a,200,OK,t,text,true,,10,10,1,1,http://h/,60,0,1\n")
    for run_id, state in (("R-LIVE", "RUNNING"), ("R-WAIT", "QUEUED")):
        db.execute("INSERT INTO perf_runs (run_id, plan_id, kind, name, status, state, started_at, queued_at, folder)"
                   " VALUES (?,?,?,?,?,?,?,?,?)", (run_id, plan.id, "load", "Old plan", "RUNNING", state,
                                                   "2026-01-02", "2026-01-02", str(folder / run_id)))
    out = rt.performance.recover()
    assert out["interrupted"] == 2 and out["skipped"] == 1 and out["scenarios_created"] == 1
    got = {r["run_id"]: r for r in rt.performance.list_runs(limit=50)}
    assert (got["R-PASS"]["state"], got["R-PASS"]["verdict"]) == ("COMPLETED", "PASS")
    assert (got["R-SLAFAIL"]["state"], got["R-SLAFAIL"]["verdict"]) == ("COMPLETED", "FAIL")
    assert (got["R-ERR"]["state"], got["R-ERR"]["verdict"]) == ("FAILED", None)
    assert (got["R-NODEF"]["state"], got["R-NODEF"]["verdict"]) == ("COMPLETED", "NOT_SPECIFIED")
    assert got["R-STUCK"]["state"] == "INTERRUPTED" and not got["R-STUCK"]["partial"]
    live = rt.performance.get_run("R-LIVE")
    assert live["state"] == "INTERRUPTED" and live["partial"] and live["verdict"] == "INCONCLUSIVE"
    assert live["metrics"]["overall"]["samples"] == 2 and "application stopped" in live["state_reason"]
    assert got["R-WAIT"]["state"] == "SKIPPED" and "Run now" in got["R-WAIT"]["state_reason"]
    sc = rt.performance.list_scenarios()
    assert [s["plan_id"] for s in sc] == [plan.id] and sc[0]["last_run"]["run_id"]
    assert all(r["scenario_id"] == sc[0]["id"] for r in rt.performance.list_runs(limit=50))
    assert rt.performance.recover() == {"interrupted": 0, "skipped": 0, "killed": 0, "scenarios_created": 0}


def test_restart_ends_only_the_jmeter_process_of_an_interrupted_run(client, site):
    """Simulates a crash: JMeter is left running for a run marked RUNNING. On the next start, recovery finds that
    JMeter by its run folder, ends it and keeps its partial results. A process that is not the run's is left alone."""
    rt = client.app.state.runtime
    if not _jmeter(rt) or not generator.available():
        pytest.skip("JMeter/Java or psutil not installed")
    plan = PerfPlan.model_validate(_plan(site, name="Crash", duration=300))
    run_id = "PERF-20260101-000000-CRASH1"
    folder = rt.paths.performance / run_id
    folder.mkdir(parents=True)
    (folder / "plan.jmx").write_text(build_jmx(plan), encoding="utf-8")
    jmeter = tools.find_jmeter(rt.settings)
    proc = subprocess.Popen([jmeter, "-n", "-t", str(folder / "plan.jmx"), "-l", "results.jtl", "-j", "jmeter.log",
                             "-Jjmeter.save.saveservice.output_format=csv"], cwd=folder,
                            env=tools.jmeter_env(tools.find_java(rt.settings)), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    bystander = subprocess.Popen(["sleep", "60"])
    try:
        rt.db.execute("INSERT INTO perf_runs (run_id, plan_id, kind, name, status, state, started_at, folder, pid)"
                      " VALUES (?,?,?,?,?,?,?,?,?)", (run_id, "PERF-X", "load", "Crash", "RUNNING", "RUNNING",
                                                      "2026-01-01", str(folder), proc.pid))
        # a wrong pid (another process) must never be ended
        rt.db.execute("INSERT INTO perf_runs (run_id, plan_id, kind, name, status, state, started_at, folder, pid)"
                      " VALUES (?,?,?,?,?,?,?,?,?)", ("PERF-OTHER", "PERF-X", "load", "Other", "RUNNING", "RUNNING",
                                                      "2026-01-01", str(rt.paths.performance / "PERF-OTHER"),
                                                      bystander.pid))
        _wait_samples(rt, run_id)
        time.sleep(2)
        out = rt.performance.recover()
        assert out["interrupted"] == 2 and out["killed"] >= 1
        proc.wait(timeout=20)
        assert bystander.poll() is None  # untouched
        run = rt.performance.get_run(run_id)
        assert run["state"] == "INTERRUPTED" and run["partial"] and run["metrics"]["overall"]["samples"] > 0
        assert "JMeter process was ended" in run["state_reason"]
    finally:
        for p in (proc, bystander):
            if p.poll() is None:
                p.kill()


# ---- real runs: queue, states, verdicts, snapshot, generator, stops ------------------------------------------

def test_queue_snapshot_generator_and_verdicts(client, site):
    rt = client.app.state.runtime
    if not _jmeter(rt):
        pytest.skip("JMeter/Java not installed (set JMETER_HOME to run)")
    env = client.post("/api/performance/environments", json={"name": "Local", "allowed_hosts": ["127.0.0.1"],
                                                             "max_users": 5}).json()["environment"]
    sc = client.post("/api/performance/scenarios", json={"name": "No targets", "environment_id": env["id"]}).json()["scenario"]
    client.post(f"/api/performance/plans?scenario_id={sc['id']}", json=_plan(site, duration=10))
    sc2 = client.post("/api/performance/scenarios", json={"name": "Strict", "environment_id": env["id"]}).json()["scenario"]
    client.post(f"/api/performance/plans?scenario_id={sc2['id']}",
                json=_plan(site, name="Strict", duration=10, sla=Sla(p95_ms=0.001)))
    sc3 = client.post("/api/performance/scenarios", json={"name": "Never", "environment_id": env["id"]}).json()["scenario"]
    client.post(f"/api/performance/plans?scenario_id={sc3['id']}", json=_plan(site, name="Never", duration=10))

    first = client.post(f"/api/performance/scenarios/{sc['id']}/run").json()
    second = client.post(f"/api/performance/scenarios/{sc2['id']}/run").json()["run_id"]
    third = client.post(f"/api/performance/scenarios/{sc3['id']}/run").json()["run_id"]
    assert first["state"] == "QUEUED"
    _wait(client, first["run_id"], states=("RUNNING",), timeout=60)
    queue = client.get("/api/performance/queue").json()["items"]
    assert [q["run_id"] for q in queue] == [first["run_id"], second, third]
    assert [q.get("position") for q in queue] == [None, 1, 2]
    assert client.post(f"/api/performance/runs/{third}/cancel").json()["ok"]

    done = _wait(client, first["run_id"])
    assert done["state"] == "COMPLETED" and done["verdict"] == "NOT_SPECIFIED" and done["status"] == "PASSED"
    assert not done["partial"] and done["scenario_id"] == sc["id"] and done["environment_id"] == env["id"]
    assert {"plan.jmx", "plan.json", "config.json", "generator.json", "results.jtl"} <= set(done["files"])
    cfg = json.loads(client.get(f"/api/performance/runs/{done['run_id']}/files/config.json").text)
    assert cfg["tools"]["jmeter_version"].startswith("5.6") and cfg["workload"]["peak_concurrent_users"] == 2
    assert cfg["environment"]["name"] == "Local" and cfg["scenario"]["id"] == sc["id"] and len(cfg["jmx_sha256"]) == 64
    assert "not a request-rate target" in cfg["workload"]["load_model"]
    assert done["checks"]["status"] in ("PASS", "WARN")
    gen = done["generator"]
    if generator.available():
        assert gen["available"] and gen["samples"] and "cpu_max_pct" in gen["stats"] and isinstance(gen["saturated"], bool)
    else:
        assert gen["available"] is False

    strict = _wait(client, second)
    assert strict["state"] == "COMPLETED" and strict["verdict"] == "FAIL" and strict["status"] == "FAILED"
    assert rt.performance.get_run(third)["state"] == "CANCELLED"
    hist = client.get("/api/performance/runs", params={"scenario_id": sc2["id"]}).json()["items"]
    assert [h["run_id"] for h in hist] == [second]
    assert [h["run_id"] for h in client.get("/api/performance/runs", params={"verdict": "FAIL"}).json()["items"]] == [second]


@pytest.mark.parametrize("mode", ["graceful", "emergency"])
def test_stop_keeps_partial_results_and_inconclusive_verdict(client, site, mode):
    rt = client.app.state.runtime
    if not _jmeter(rt):
        pytest.skip("JMeter/Java not installed (set JMETER_HOME to run)")
    plan = client.post("/api/performance/plans", json=_plan(site, name="Stop", duration=600, sla=Sla(p95_ms=5000))).json()["plan"]
    run_id = client.post(f"/api/performance/plans/{plan['id']}/run").json()["run_id"]
    _wait(client, run_id, states=("RUNNING",), timeout=60)
    _wait_samples(rt, run_id)
    time.sleep(2)
    assert client.post(f"/api/performance/runs/{run_id}/stop", json={"mode": mode}).json()["mode"] == mode
    run = _wait(client, run_id, timeout=90)
    assert run["state"] == "CANCELLED" and run["partial"] and run["verdict"] == "INCONCLUSIVE"
    assert run["metrics"]["overall"]["samples"] > 0 and run["sla"]["status"] == "PASS"  # shown, but not a verdict
    assert ("Emergency" in run["state_reason"]) == (mode == "emergency")
    assert client.post(f"/api/performance/runs/{run_id}/stop", json={}).json()["error_code"] == "RUN_NOT_ACTIVE"


def test_closing_the_application_interrupts_a_running_test(client, site):
    rt = client.app.state.runtime
    if not _jmeter(rt):
        pytest.skip("JMeter/Java not installed (set JMETER_HOME to run)")
    plan = client.post("/api/performance/plans", json=_plan(site, name="Close", duration=600)).json()["plan"]
    run_id = client.post(f"/api/performance/plans/{plan['id']}/run").json()["run_id"]
    _wait(client, run_id, states=("RUNNING",), timeout=60)
    _wait_samples(rt, run_id)
    rt.performance.close(timeout=40)
    run = rt.performance.get_run(run_id)
    assert run["state"] == "INTERRUPTED" and run["partial"] and "application was closed" in run["state_reason"]
    assert not rt.performance.running()


def test_generator_monitor_flags_saturation(tmp_path, monkeypatch):
    if not generator.available():
        pytest.skip("psutil not installed")
    mon = generator.GeneratorMonitor(tmp_path)
    monkeypatch.setattr(generator.psutil, "cpu_percent", lambda *_: 97.0)
    for _ in range(generator.SUSTAIN):
        mon.samples.append(mon.sample())
    summary = mon.summary()
    assert summary["saturated"] and summary["flags"][0]["id"] == "cpu" and summary["stats"]["cpu_max_pct"] == 97.0
    assert generator.find_run_process(None, tmp_path) is None
    assert jtl.TOTAL  # module import kept
