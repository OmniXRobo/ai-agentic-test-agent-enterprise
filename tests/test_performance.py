"""Stage 5: JMeter plans, JTL metrics, SLA evaluation and (when JMeter is installed) real non-GUI runs."""

from __future__ import annotations

import csv
import io
import threading
import time
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from qa_platform.credentials import MemoryBackend
from qa_platform.main import create_app
from qa_platform.performance import jtl, sla, tools
from qa_platform.performance.jmx import build_jmx
from qa_platform.performance.models import HttpRequest, LoadProfile, PerfPlan, Sla
from tests.fake_llm import FakeLLM

HEADER = "timeStamp,elapsed,label,responseCode,responseMessage,threadName,dataType,success,failureMessage,bytes,sentBytes,grpThreads,allThreads,URL,Latency,IdleTime,Connect"


def _jtl(rows):
    buf = io.StringIO()
    buf.write(HEADER + "\n")
    w = csv.writer(buf)
    for ts, elapsed, label, code, ok in rows:
        w.writerow([ts, elapsed, label, code, "OK" if ok else "Server Error", "Users 1-1", "text", str(ok).lower(),
                    "" if ok else "Test failed: code expected to match", 1000, 100, 1, 2, "http://x/" + label, elapsed - 5, 0, 3])
    return buf.getvalue()


def _plan(base="http://127.0.0.1:1", kind="load", **profile):
    return PerfPlan(name="Shop", kind=kind, requests=[
        HttpRequest(name="Login page", url=f"{base}/login.html", expect_status="2\\d\\d"),
        HttpRequest(name="Item API", url=f"{base}/api/items/1", headers={"Accept": "application/json"},
                    expect_text="Item 1"),
        HttpRequest(name="Create item", method="POST", url=f"{base}/api/items", body='{"name": "a & b"}'),
    ], profile=LoadProfile(**profile), sla=Sla(p95_ms=500, max_error_pct=1))


def test_profiles_turn_into_plain_thread_groups():
    stress = _plan(kind="stress", users=10, steps=4, duration_s=400).thread_groups()
    assert [g.users for g in stress] == [3, 3, 2, 2] and [g.delay_s for g in stress] == [0, 100, 200, 300]
    assert all(g.delay_s + g.duration_s == 400 for g in stress)
    spike = _plan(kind="spike", users=5, spike_users=40, spike_at_s=60, spike_duration_s=30, duration_s=180)
    assert [(g.users, g.delay_s, g.duration_s) for g in spike.thread_groups()] == [(5, 0, 180), (40, 60, 30)]
    assert spike.peak_users() == 45
    with pytest.raises(ValueError):
        _plan(kind="spike", spike_at_s=170, spike_duration_s=30, duration_s=180)
    with pytest.raises(ValueError):
        HttpRequest(name="x", url="ftp://nope")


def test_jmx_is_valid_xml_with_samplers_assertions_and_escaping():
    root = ET.fromstring(build_jmx(_plan(kind="stress", users=4, steps=2)))
    groups = root.findall(".//ThreadGroup")
    assert len(groups) == 2
    samplers = root.findall(".//HTTPSamplerProxy")
    assert len(samplers) == 6
    first = samplers[0]
    props = {p.get("name"): p.text for p in first.iter("stringProp")}
    assert props["HTTPSampler.domain"] == "127.0.0.1" and props["HTTPSampler.port"] == "1"
    assert props["HTTPSampler.path"] == "/login.html" and props["HTTPSampler.method"] == "GET"
    post = samplers[2]
    assert any(p.text == '{"name": "a & b"}' for p in post.iter("stringProp"))
    assert len(root.findall(".//ResponseAssertion")) == 4 and root.findall(".//HeaderManager")
    assert root.find(".//ConstantTimer") is not None


def test_jtl_metrics_and_percentiles():
    rows = [(1000 + i * 100, 100 + i, "A", "200", True) for i in range(100)]
    rows += [(1050 + i * 100, 50, "B", "500" if i < 5 else "200", i >= 5) for i in range(100)]
    m = jtl.compute(csv.DictReader(io.StringIO(_jtl(rows))))
    a = next(r for r in m["requests"] if r["label"] == "A")
    assert a["samples"] == 100 and a["p50_ms"] == 149 and a["p95_ms"] == 194 and a["p99_ms"] == 198 and a["max_ms"] == 199
    b = next(r for r in m["requests"] if r["label"] == "B")
    assert b["errors"] == 5 and b["error_pct"] == 5.0 and b["response_codes"] == {"200": 95, "500": 5}
    assert b["top_errors"][0]["count"] == 5
    o = m["overall"]
    assert o["samples"] == 200 and o["label"] == jtl.TOTAL and 19 < o["throughput"] < 21
    assert m["timeline"] and sum(p["errors"] for p in m["timeline"]) == 5


def test_sla_evaluation():
    metrics = {"overall": {"label": "ALL", "avg_ms": 120, "p90_ms": 180, "p95_ms": 210, "p99_ms": 400,
                           "error_pct": 0.5, "throughput": 30},
               "requests": [{"label": "A", "avg_ms": 300, "p90_ms": 450, "p95_ms": 600, "p99_ms": 900,
                             "error_pct": 0, "throughput": 10}]}
    r = sla.evaluate(Sla(p95_ms=500, max_error_pct=1, min_throughput=20), metrics)
    assert r["status"] == "FAIL" and [(c["scope"], c["name"]) for c in r["failed"]] == [("A", "95th percentile response time")]
    assert sla.evaluate(Sla(), metrics)["status"] == "NOT_DEFINED"
    assert sla.evaluate(Sla(p95_ms=1000), metrics)["status"] == "PASS"


def test_live_tail_reads_only_complete_lines(tmp_path):
    path = tmp_path / "r.jtl"
    text = _jtl([(1000, 10, "A", "200", True), (1100, 20, "A", "500", False)])
    path.write_text(text[:-5])  # last row incomplete
    tail = jtl.LiveTail(path)
    assert tail.poll()["samples"] == 1
    path.write_text(text)
    live = tail.poll()
    assert live["samples"] == 2 and live["errors"] == 1


@pytest.fixture()
def client(paths):
    llm = FakeLLM()
    app = create_app(paths, secret_backend=MemoryBackend(), ai_transport=llm.transport())
    with TestClient(app) as c:
        c.put("/api/ai/settings", json={"base_url": "http://127.0.0.1:8080/v1", "model": "qwen2.5-7b-instruct-q4_k_m.gguf"})
        yield c


def test_plan_api_crud_and_validation(client):
    body = _plan().model_dump()
    r = client.post("/api/performance/plans", json=body)
    assert r.status_code == 201
    pid = r.json()["plan"]["id"]
    detail = client.get(f"/api/performance/plans/{pid}").json()
    assert detail["peak_users"] == 10 and detail["thread_groups"][0]["duration_s"] == 300
    body["profile"]["users"] = 25
    assert client.put(f"/api/performance/plans/{pid}", json=body).json()["plan"]["profile"]["users"] == 25
    assert "<jmeterTestPlan" in client.get(f"/api/performance/plans/{pid}/jmx").text
    bad = client.post("/api/performance/plans", json={**body, "requests": []})
    assert bad.status_code == 422 and bad.json()["error_code"] == "VALIDATION_FAILED"
    assert client.get("/api/performance/plans/PERF-NOPE").json()["error_code"] == "PERF_PLAN_NOT_FOUND"
    assert client.delete(f"/api/performance/plans/{pid}").json()["ok"]


def test_run_reports_missing_jmeter(client, monkeypatch):
    monkeypatch.setattr(tools, "find_jmeter", lambda settings: None)
    pid = client.post("/api/performance/plans", json=_plan().model_dump()).json()["plan"]["id"]
    r = client.post(f"/api/performance/plans/{pid}/run")
    assert r.status_code == 409 and r.json()["error_code"] == "JMETER_NOT_FOUND"


def test_plan_from_discovery_uses_observed_requests(client):
    rt = client.app.state.runtime
    disc = {"base_url": "http://shop", "created_at": "x", "diff": None, "pages": [
        {"url": "http://shop/", "status": 200, "title": "Home", "elements": [], "forms": []},
        {"url": "http://shop/broken", "status": 500, "title": "Err", "elements": [], "forms": []},
    ], "endpoints": [
        {"method": "GET", "endpoint": "http://shop/api/items/{id}", "sample_url": "http://shop/api/items/7", "has_body": False},
        {"method": "POST", "endpoint": "http://shop/api/orders", "sample_url": "http://shop/api/orders", "has_body": True},
    ]}
    from qa_platform.storage.db import dumps
    rt.db.execute("INSERT INTO discoveries (id, run_id, base_url, created_at, result) VALUES (?,?,?,?,?)",
                  ("DISC-X", "DISC-X", "http://shop", "2026-01-01", dumps(disc)))
    plan = client.post("/api/performance/plans/from-discovery", json={"discovery_id": "DISC-X", "kind": "stress"}).json()["plan"]
    assert [r["url"] for r in plan["requests"]] == ["http://shop/", "http://shop/api/items/7"]
    assert plan["kind"] == "stress" and plan["source"] == "discovery DISC-X"




def _jmeter_available(rt) -> bool:
    env = rt.performance.environment()
    return env["ready"]


def test_real_jmeter_run_metrics_sla_report_and_analysis(client, site):
    rt = client.app.state.runtime
    if not _jmeter_available(rt):
        pytest.skip("JMeter/Java not installed (set JMETER_HOME to run)")
    plan = PerfPlan(name="Local site", kind="load", requests=[
        HttpRequest(name="Login page", url=f"{site}/login.html", expect_status="200"),
        HttpRequest(name="Item API", url=f"{site}/api/items/5", expect_text="Item 5"),
        HttpRequest(name="Missing page", url=f"{site}/nope.html"),
    ], profile=LoadProfile(users=3, ramp_up_s=1, duration_s=10, think_time_ms=200), sla=Sla(max_error_pct=1, p95_ms=2000))
    pid = rt.performance.save_plan(plan).id
    run = rt.performance.run(pid, "PERF-20260101-000000-TEST01")
    assert run["status"] == "FAILED"  # the missing page returns 404 -> error rate SLA fails
    m = run["metrics"]
    labels = {r["label"]: r for r in m["requests"]}
    assert labels["Login page"]["error_pct"] == 0 and labels["Item API"]["error_pct"] == 0
    assert labels["Missing page"]["error_pct"] == 100 and labels["Missing page"]["response_codes"] == {"404": labels["Missing page"]["samples"]}
    assert run["sla"]["status"] == "FAIL"
    assert any(c["scope"] == "Missing page" and c["name"] == "Error rate" for c in run["sla"]["failed"])
    assert run["has_report"] and "results.jtl" in run["files"] and "plan.jmx" in run["files"]
    assert client.get(f"/api/performance/runs/{run['run_id']}/files/report/index.html").status_code == 200
    assert client.get(f"/api/performance/runs/{run['run_id']}/files/../../../etc/passwd").status_code == 404
    analysis = client.post(f"/api/performance/runs/{run['run_id']}/analysis").json()["analysis"]
    assert analysis["undetermined"] and rt.performance.get_run(run["run_id"])["analysis"]["summary"]
    hist = rt.runs.get(run["run_id"])
    assert hist["kind"] == "performance" and hist["status"] == "FAILED"
    # The failed SLA becomes a performance defect draft (default mode: Review Before Create).
    deadline = time.time() + 10
    while time.time() < deadline and not rt.db.query("SELECT id FROM defect_drafts WHERE source_run_id = ?", (run["run_id"],)):
        time.sleep(0.1)
    draft = rt.defects.list(source="performance")[0]
    assert draft["category"] == "performance_errors" and draft["status"] == "draft"


def test_real_jmeter_stop(client, site):
    rt = client.app.state.runtime
    if not _jmeter_available(rt):
        pytest.skip("JMeter/Java not installed (set JMETER_HOME to run)")
    plan = PerfPlan(name="Stop me", requests=[HttpRequest(name="Home", url=f"{site}/index.html")],
                    profile=LoadProfile(users=2, ramp_up_s=1, duration_s=600, think_time_ms=100))
    pid = rt.performance.save_plan(plan).id
    run_id = "PERF-20260101-000000-STOP01"
    out = {}
    t = threading.Thread(target=lambda: out.update(rt.performance.run(pid, run_id)))
    t.start()
    for _ in range(200):
        if run_id in rt.performance.running() and (rt.paths.performance / run_id / "results.jtl").exists():
            break
        time.sleep(0.1)
    time.sleep(3)
    assert rt.performance.stop(run_id)
    t.join(timeout=120)
    assert not t.is_alive()
    assert out["metrics"]["stopped_by_user"] is True and out["metrics"]["overall"]["samples"] > 0
