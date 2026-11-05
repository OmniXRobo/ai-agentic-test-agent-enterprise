"""23.3.26: workload shapes (load, stress, spike, soak) built from plain thread groups, a request-rate model that paces
a fixed user pool with the built-in Precise Throughput Timer (users are never equated with requests per second), and
the one-user validation run that checks status, assertions and correlations before a load test. The saved script is
never changed by either, saving a workload never starts a run, and real JMeter runs prove the shapes and the rate."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from qa_platform.performance import script as S
from qa_platform.performance import workload as W
from qa_platform.performance.models import Workload
from tests import site_server

FINAL = ("COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "SKIPPED")


def _wait(client, run_id: str, timeout: float = 240) -> dict:
    deadline = time.time() + timeout
    run = {}
    while time.time() < deadline:
        run = client.get(f"/api/performance/runs/{run_id}").json()["run"]
        if run["state"] in FINAL:
            return run
        time.sleep(0.3)
    raise AssertionError(f"{run_id} stayed {run.get('state')}")


def jmx(port: str, json_path: str = "$.session", loops: str = "-1", scheduler: str = "false") -> str:
    """Session request (extracts the id) then a profile request that needs it, inside a transaction."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3"><hashTree>
<TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="Session flow"/><hashTree>
<ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="Shoppers">
  <stringProp name="ThreadGroup.on_sample_error">continue</stringProp>
  <elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" testclass="LoopController">
    <stringProp name="LoopController.loops">{loops}</stringProp><boolProp name="LoopController.continue_forever">false</boolProp></elementProp>
  <stringProp name="ThreadGroup.num_threads">2</stringProp><stringProp name="ThreadGroup.ramp_time">0</stringProp>
  <boolProp name="ThreadGroup.scheduler">{scheduler}</boolProp><stringProp name="ThreadGroup.duration"></stringProp>
  <stringProp name="ThreadGroup.delay"></stringProp></ThreadGroup><hashTree>
  <TransactionController guiclass="TransactionControllerGui" testclass="TransactionController" testname="Sign in">
    <boolProp name="TransactionController.includeTimers">false</boolProp><boolProp name="TransactionController.parent">false</boolProp>
  </TransactionController><hashTree>
  <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="Session">
    <elementProp name="HTTPsampler.Arguments" elementType="Arguments"><collectionProp name="Arguments.arguments"/></elementProp>
    <stringProp name="HTTPSampler.domain">127.0.0.1</stringProp><stringProp name="HTTPSampler.port">{port}</stringProp>
    <stringProp name="HTTPSampler.protocol">http</stringProp><stringProp name="HTTPSampler.path">/perf/api/session</stringProp>
    <stringProp name="HTTPSampler.method">POST</stringProp>
  </HTTPSamplerProxy><hashTree>
    <JSONPostProcessor guiclass="JSONPostProcessorGui" testclass="JSONPostProcessor" testname="Session id">
      <stringProp name="JSONPostProcessor.referenceNames">sid</stringProp><stringProp name="JSONPostProcessor.jsonPathExprs">{json_path}</stringProp>
      <stringProp name="JSONPostProcessor.match_numbers">1</stringProp><stringProp name="JSONPostProcessor.defaultValues">NOT_FOUND</stringProp>
    </JSONPostProcessor><hashTree/>
  </hashTree>
  <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="Profile">
    <elementProp name="HTTPsampler.Arguments" elementType="Arguments"><collectionProp name="Arguments.arguments"/></elementProp>
    <stringProp name="HTTPSampler.domain">127.0.0.1</stringProp><stringProp name="HTTPSampler.port">{port}</stringProp>
    <stringProp name="HTTPSampler.protocol">http</stringProp><stringProp name="HTTPSampler.path">/perf/api/profile?session=${{sid}}</stringProp>
    <stringProp name="HTTPSampler.method">GET</stringProp>
  </HTTPSamplerProxy><hashTree>
    <ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" testname="Is 200">
      <collectionProp name="Asserion.test_strings"><stringProp name="49586">200</stringProp></collectionProp>
      <stringProp name="Assertion.test_field">Assertion.response_code</stringProp><boolProp name="Assertion.assume_success">false</boolProp>
      <intProp name="Assertion.test_type">8</intProp></ResponseAssertion><hashTree/>
  </hashTree>
  </hashTree>
</hashTree></hashTree></hashTree></jmeterTestPlan>"""


def _plan(client, port: str, env: dict | None = None, **kw) -> tuple[dict, dict]:
    env = env or client.post("/api/performance/environments", json={
        "name": "Local", "allowed_hosts": ["127.0.0.1"], "max_users": 20}).json()["environment"]
    sc = client.post("/api/performance/scenarios", json={"name": "Flow", "environment_id": env["id"]}).json()["scenario"]
    plan = client.post(f"/api/performance/plans/import?scenario_id={sc['id']}",
                       json={"jmx": jmx(port, **kw), "filename": "flow.jmx"}).json()["plan"]
    return plan, sc


# ---- shapes (pure) -------------------------------------------------------------------------------------------------

def test_shapes_are_plain_thread_groups_with_the_planned_users_over_time():
    stress = Workload(shape="stress", users=5, steps=3, step_s=60, ramp_up_s=10)
    assert [(g["users"], g["delay_s"], g["duration_s"]) for g in W.groups(stress)] == [(5, 0, 180), (5, 60, 120),
                                                                                      (5, 120, 60)]
    assert W.peak(stress) == 15 and W.duration(stress) == 180
    assert [0.0, 0.0] == W.curve(stress)[0] and W.curve(stress)[-1] == [180, 0.0]
    spike = Workload(shape="spike", users=5, ramp_up_s=10, hold_s=100, spike_users=20, spike_at_s=50, spike_s=20)
    assert W.peak(spike) == 25 and W.duration(spike) == 110
    assert W.check(Workload(shape="spike", hold_s=60, ramp_up_s=0, spike_at_s=50, spike_s=20))  # ends too late
    assert W.check(Workload(shape="stress", model="rate", target_rps=5))  # a rate only with steady shapes
    assert W.check(Workload(shape="load", model="rate"))  # no target
    soak = W.preview(Workload(shape="soak", hold_s=600))
    assert any("hours" in n for n in soak["notes"])
    assert "not a request-rate target" in W.describe(Workload(shape="load"))
    assert "achieved rate drops" in W.describe(Workload(shape="load", model="rate", target_rps=3))


def test_apply_copies_each_thread_group_per_phase_and_keeps_its_requests():
    text = jmx("8000")
    out, info = W.apply(text, Workload(shape="stress", users=4, steps=2, step_s=30, ramp_up_s=5))
    s = S.summarize(S.parse(out))
    assert [(g["name"], g["users"], g["delay_s"], g["duration_s"]) for g in s["thread_groups"]] == [
        ("Shoppers · Step 1", 4, 0, 60), ("Shoppers · Step 2", 4, 30, 30)]
    assert [r["name"] for r in s["requests"]] == ["Session", "Profile", "Session", "Profile"]
    assert all(g["scheduler"] and g["forever"] for g in s["thread_groups"])
    assert not S.problems(S.parse(out))
    out, info = W.apply(text, Workload(shape="load", model="rate", target_rps=5, users=3, ramp_up_s=0, hold_s=30))
    assert "<PreciseThroughputTimer" in out and "<value>300.0</value>" in out  # 5/s = 300 per 60 s period
    assert info["thread_groups"][0]["rate_rps"] == 5
    assert W.apply(text, Workload())[0] == text  # "as in the script": unchanged
    # Two main groups: users split by the users each has in the script, the rate too
    two = S.apply(text, [{"op": "duplicate", "node": "0/0"},
                         {"op": "set", "node": "0/1", "name": "Browsers", "props": {"ThreadGroup.num_threads": "6"}}])
    two = two["script"]
    out, info = W.apply(two, Workload(shape="load", model="rate", target_rps=8, users=8))
    assert [(g["users"], g["rate_rps"]) for g in info["thread_groups"]] == [(2, 2.0), (6, 6.0)]


def test_validation_copy_is_one_user_one_iteration_with_correlation_checks():
    out, info = W.for_validation(jmx("8000", loops="-1", scheduler="true"))
    s = S.summarize(S.parse(out))
    assert [(g["users"], g["loops"], g["scheduler"]) for g in s["thread_groups"]] == [(1, 1, False)]
    assert "TestPlan.serialize_threadgroups\">true" in out
    assert info["correlation_checks"] == {"Session": ["sid"]}
    assert "<JSR223Assertion" in out and "groovy" in out
    # A throughput timer (from a workload) is switched off: it would pace the single user
    shaped, _ = W.apply(jmx("8000"), Workload(shape="load", model="rate", target_rps=1, users=1))
    out, info = W.for_validation(shaped)
    assert info["throughput_timers_off"] == 1


# ---- API, checks and real JMeter runs ------------------------------------------------------------------------------

def test_saving_a_workload_changes_no_script_version_and_starts_nothing(client, site):
    port = site.rsplit(":", 1)[1]
    plan, sc = _plan(client, port)
    before = client.get("/api/performance/runs").json()["items"]
    r = client.put(f"/api/performance/plans/{plan['id']}/workload",
                   json={"shape": "stress", "users": 3, "steps": 3, "step_s": 20, "ramp_up_s": 5})
    assert r.status_code == 200, r.text
    assert r.json()["plan"]["kind"] == "stress" and r.json()["plan"]["script_version"] == 1
    assert r.json()["preview"]["peak_users"] == 9
    assert client.get("/api/performance/runs").json()["items"] == before
    view = client.get(f"/api/performance/plans/{plan['id']}/script").json()
    assert view["version"] == 1 and view["plan"]["workload"]["shape"] == "stress"
    assert "Step 1" not in view["script"]  # the saved script is unchanged
    lst = next(p for p in client.get("/api/performance/plans").json()["items"] if p["id"] == plan["id"])
    assert lst["peak_users"] == 9 and lst["duration_s"] == 60
    # Load limit of the environment applies to the shape's peak
    big = client.put(f"/api/performance/plans/{plan['id']}/workload",
                     json={"shape": "load", "users": 50, "ramp_up_s": 0, "hold_s": 30})
    assert big.status_code == 200
    chk = client.post(f"/api/performance/scenarios/{sc['id']}/check").json()
    assert next(c for c in chk["checks"] if c["id"] == "users")["status"] == "FAIL"
    assert next(c for c in chk["checks"] if c["id"] == "validated")["status"] == "WARN"
    bad = client.put(f"/api/performance/plans/{plan['id']}/workload", json={"shape": "spike", "hold_s": 30,
                                                                           "ramp_up_s": 0, "spike_at_s": 25})
    assert bad.status_code == 422 and "spike must end" in bad.json()["message"], bad.json()
    prev = client.post("/api/performance/workload/preview", json={"workload": {"shape": "load", "model": "rate"}})
    assert prev.json()["problems"]


def _jmeter(rt):
    if not rt.performance.environment()["ready"]:
        pytest.skip("JMeter/Java not installed (set JMETER_HOME to run)")


def test_validation_run_finds_a_missing_correlation_then_passes(client, site):
    rt = client.app.state.runtime
    _jmeter(rt)
    port = site.rsplit(":", 1)[1]
    plan, sc = _plan(client, port, json_path="$.wrong")
    site_server.PERF_SEEN["hits"].clear()
    run_id = client.post(f"/api/performance/plans/{plan['id']}/validate").json()["run_id"]
    run = _wait(client, run_id)
    assert run["state"] == "COMPLETED" and run["verdict"] is None, run.get("error")
    assert run["name"].endswith("(one-user validation)")
    v = run["metrics"]["validation"]
    assert v["status"] == "FAIL" and v["requests"] == 2 and v["correlation_failures"] == 1
    session = next(x for x in v["samples"] if x["label"] == "Session")
    assert not session["ok"] and "sid not found" in session["failure"]
    profile = next(x for x in v["samples"] if x["label"] == "Profile")
    assert profile["code"] == "401" and not profile["ok"]
    assert next(x for x in v["samples"] if x["label"] == "Sign in")["transaction"]
    assert site_server.PERF_SEEN["hits"] == ["/perf/api/session", "/perf/api/profile"]  # one user, once
    cfg = json.loads((Path(run["folder"]) / "config.json").read_text(encoding="utf-8"))
    assert cfg["validation_run"] and cfg["workload"]["peak_concurrent_users"] == 1
    plan_now = client.get(f"/api/performance/plans/{plan['id']}").json()["plan"]
    assert plan_now["validation"]["status"] == "FAIL" and "samples" not in plan_now["validation"]
    assert plan_now["mode"] == "test"
    saved = client.get(f"/api/performance/plans/{plan['id']}/script").json()["script"]
    assert "JSR223Assertion" not in saved  # the checks were in the run's copy only

    # Fix the JSON path: a new version, validated again
    client.put(f"/api/performance/plans/{plan['id']}/script",
               json={"script": saved.replace("$.wrong", "$.session"), "base_version": 1})
    run = _wait(client, client.post(f"/api/performance/plans/{plan['id']}/validate").json()["run_id"])
    v = run["metrics"]["validation"]
    assert v["status"] == "PASS" and v["failed"] == 0 and v["iteration_s"] > 0, v
    chk = client.post(f"/api/performance/scenarios/{sc['id']}/check").json()
    ok = next(c for c in chk["checks"] if c["id"] == "validated")
    assert ok["status"] == "PASS" and "Version 2 passed" in ok["detail"]


def test_stress_shape_and_request_rate_target_in_real_runs(client, site):
    rt = client.app.state.runtime
    _jmeter(rt)
    port = site.rsplit(":", 1)[1]
    plan, sc = _plan(client, port)
    # Think time, so the users do not open thousands of connections (the test server closes each one, and on
    # Windows the closed ports stay reserved for minutes, which later tests would run out of)
    view = client.get(f"/api/performance/plans/{plan['id']}/script").json()
    work = client.post("/api/performance/script/apply", json={"script": view["script"], "ops": [
        {"op": "add", "parent": "0/0", "type": "constant_timer", "values": {"delay_ms": 300}}]}).json()
    client.put(f"/api/performance/plans/{plan['id']}/script", json={"script": work["script"], "base_version": 1})
    # Stress: 1 user, then 2, then 3 (steps of 10 s)
    client.put(f"/api/performance/plans/{plan['id']}/workload",
               json={"shape": "stress", "users": 1, "steps": 3, "step_s": 10, "ramp_up_s": 0})
    run = _wait(client, client.post(f"/api/performance/plans/{plan['id']}/run").json()["run_id"])
    assert run["state"] == "COMPLETED", run.get("error")
    assert run["metrics"]["peak_threads"] == 3
    cfg = json.loads((Path(run["folder"]) / "config.json").read_text(encoding="utf-8"))
    assert [g["name"] for g in cfg["workload"]["applied"]["thread_groups"]] == [
        "Shoppers · Step 1", "Shoppers · Step 2", "Shoppers · Step 3"]
    assert cfg["workload"]["total_duration_s"] == 30

    # Rate: 3 users paced to 6 requests per second (transaction samples are extra rows in the results)
    client.put(f"/api/performance/plans/{plan['id']}/workload",
               json={"shape": "load", "model": "rate", "target_rps": 6, "users": 3, "ramp_up_s": 0, "hold_s": 20})
    site_server.PERF_SEEN["hits"].clear()
    started = time.time()
    run = _wait(client, client.post(f"/api/performance/plans/{plan['id']}/run").json()["run_id"])
    assert run["state"] == "COMPLETED", run.get("error")
    hits = len(site_server.PERF_SEEN["hits"])
    assert 6 * 20 * 0.75 <= hits <= 6 * 20 * 1.15, hits  # the server saw about 120 requests in 20 s
    assert time.time() - started >= 19
    rate = run["metrics"]["rate"]
    assert rate["target_rps"] == 6 and rate["users"] == 3
    assert 4.5 <= rate["achieved_rps"] <= 7.5 and rate["reached"] in (True, False), rate  # transactions left out
    cfg = json.loads((Path(run["folder"]) / "config.json").read_text(encoding="utf-8"))
    assert "Precise Throughput Timer" in cfg["workload"]["load_model"]
    assert "PreciseThroughputTimer" in (Path(run["folder"]) / "plan.jmx").read_text(encoding="utf-8")
