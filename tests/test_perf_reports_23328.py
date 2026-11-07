"""23.3.28: performance reports (run, comparison, consolidated), comparability warnings, run history by build and
date, and defect drafts with what is needed to reproduce and judge them. The reports are read back from the PDF."""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

from qa_platform.defects import analysis
from qa_platform.reports import perf_report as pr
from tests.test_perf_workload_23326 import _jmeter, _plan, _wait

HEADER = "timeStamp,elapsed,label,responseCode,responseMessage,threadName,success,failureMessage,bytes,allThreads\n"


def test_planned_periods_follow_the_thread_groups():
    stress = [{"name": f"Step {i + 1}", "users": 2, "ramp_up_s": 0, "delay_s": i * 10, "duration_s": 30 - i * 10}
              for i in range(3)]
    w = pr.planned_windows(stress)
    assert [(x["start_s"], x["end_s"], x["users_to"], x["name"]) for x in w] == [
        (0.0, 10.0, 2.0, "2 users"), (10.0, 20.0, 4.0, "4 users"), (20.0, 30.0, 6.0, "6 users")]
    load = pr.planned_windows([{"name": "Users", "users": 1, "ramp_up_s": 5, "delay_s": 0, "duration_s": 20}])
    assert load[0]["ramp"] and load[0]["name"] == "Ramp 0 → 1 users" and load[1]["name"] == "1 user"
    assert "steps 3" in pr.shape_text({"shape": "stress", "users": 2, "steps": 3, "step_s": 60, "ramp_up_s": 0,
                                       "spike_users": 50})
    assert "spike" not in pr.shape_text({"shape": "stress", "users": 2, "steps": 3, "step_s": 60, "spike_users": 50})


def test_phase_figures_are_read_from_the_results_file(tmp_path: Path):
    jtl = tmp_path / "results.jtl"
    t0 = 1_700_000_000_000
    rows = [f"{t0 + s * 1000},{100 + s},Home,200,OK,T,true,,1,1\n" for s in range(10)]
    rows += [f"{t0 + 10_000 + s * 500},{300},Home,500,Bad,T,{'false' if s % 4 == 0 else 'true'},boom,1,2\n" for s in range(20)]
    rows += [f"{t0 + 3000},900,Sign in,200,OK,T,true,,1,1\n"]  # a transaction: left out
    jtl.write_text(HEADER + "".join(rows), encoding="utf-8")
    ph = pr.phase_stats(jtl, [{"start_s": 0, "end_s": 10, "name": "a", "ramp": False, "users_from": 1, "users_to": 1},
                              {"start_s": 10, "end_s": 20, "name": "b", "ramp": False, "users_from": 2, "users_to": 2}],
                        {"Sign in"})
    a, b = ph["windows"]
    assert a["samples"] == 10 and a["errors"] == 0 and a["p95_ms"] == 109 and a["throughput"] == 1.0
    assert b["samples"] == 20 and b["errors"] == 5 and b["error_pct"] == 25.0 and b["avg_ms"] == 300.0
    assert b["covered_s"] == 9.8 and b["throughput"] == round(20 / 9.8, 2)  # up to the last sample, not the plan
    assert not pr.phase_stats(tmp_path / "missing.jtl", [], set())["available"]


def _item(run_id, **cfg):
    base = {"workload": {"peak_concurrent_users": 5, "total_duration_s": 60, "load_model": "closed",
                         "shape": {"shape": "load"}}, "environment": {"name": "QA"}, "jmx_sha256": "a",
            "script": {"version": 1}, "targets": {"p95_ms": 500}, "application_version": "23.3.28",
            "tools": {"jmeter_version": "5.6.3"}}
    base.update(cfg)
    return {"run": {"run_id": run_id, "kind": "load", "plan_id": "P1", "state": "COMPLETED", "partial": False,
                    "metrics": {"duration_s": 60}}, "config": base, "build": cfg.get("build_", "1.0"), "generator": {}}


def test_comparability_names_what_differs():
    same = pr.comparability(_item("A"), _item("B"))
    assert same == []
    diff = pr.comparability(_item("A"), _item("B", environment={"name": "Prod"}, jmx_sha256="b", script={"version": 2},
                                              build_="2.0", targets={"p95_ms": 400},
                                              workload={"peak_concurrent_users": 10, "total_duration_s": 60,
                                                        "load_model": "closed", "shape": {"shape": "load"}}))
    levels = {d["text"].split(" (")[0].split(":")[0]: d["level"] for d in diff}
    assert levels["Different environments"] == "blocks"
    assert levels["The executed JMX differs"] == "caution" and levels["Different peak users"] == "caution"
    assert levels["Different application builds"] == "info" and levels["Different performance targets"] == "info"
    partial = _item("B")
    partial["run"].update(state="CANCELLED", partial=True)
    assert any(d["level"] == "blocks" and "did not complete" in d["text"] for d in pr.comparability(_item("A"), partial))
    other = _item("B")
    other["run"]["plan_id"] = "P2"
    assert pr.comparability(_item("A"), other)[0]["text"].startswith("Different script records, but the JMX")


def test_defect_draft_carries_reproduction_details(tmp_path: Path):
    (tmp_path / "config.json").write_text(json.dumps({
        "environment": {"name": "QA", "base_url": "https://qa.example.com"}, "build": "2.14.0",
        "script": {"version": 4}, "test_data": [{"name": "Users", "version": 3}],
        "workload": {"peak_concurrent_users": 20, "total_duration_s": 600, "load_model": "closed (users)",
                     "thread_groups": [{"name": "Users", "users": 20, "ramp_up_s": 60, "delay_s": 0, "duration_s": 600}]}}),
        encoding="utf-8")
    run = {"run_id": "PERF-1", "plan_id": "PERF-AAAA", "kind": "load", "name": "Checkout", "folder": str(tmp_path),
           "started_at": "2026-01-01T10:00:00.000+00:00", "finished_at": "2026-01-01T10:10:00.000+00:00",
           "metrics": {"peak_threads": 20, "overall": {"samples": 900, "avg_ms": 900, "p95_ms": 2400, "error_pct": 12,
                                                       "throughput": 12, "top_errors": [{"error": "500 Server Error",
                                                                                         "count": 108}]},
                       "requests": [{"label": "Pay", "samples": 300, "error_pct": 30, "avg_ms": 1500, "p95_ms": 3100,
                                     "p99_ms": 4000}]},
           "sla": {"status": "FAIL", "failed": [
               {"name": "95th percentile response time", "scope": "Pay", "limit": 1500, "actual": 3100, "unit": "ms",
                "status": "FAIL"},
               {"name": "Error rate", "scope": "ALL REQUESTS", "limit": 1, "actual": 12, "unit": "%", "status": "FAIL"}]}}
    d = analysis.performance_draft(run, None)
    r = d["repro_steps"]
    for text in ("Environment and build:", "QA (https://qa.example.com); build 2.14.0", "20 concurrent users at peak",
                 "script version 4", "Users version 3", "Affected transaction or request:</strong> Pay",
                 "Expected against actual", "at most 1500 ms", "3100 ms", "PERF-1", "qa-report.pdf", "results.jtl",
                 "Suggested severity 1", "not determined"):
        assert text in r, text
    assert d["severity"] == 1 and d["priority"] == 2 and "12% of requests failed" in d["facts"]["severity_rationale"]
    assert d["facts"]["build"] == "2.14.0" and d["facts"]["affected"] == ["Pay"]
    sev, why = analysis.severity_for("performance_latency", [run["sla"]["failed"][0]])
    assert sev == 2 and "2.1 times the limit" in why
    sev, why = analysis.severity_for("performance_latency", [{**run["sla"]["failed"][0], "actual": 1800}])
    assert sev == 3 and "misses the limit by 20%" in why


SECTIONS = ("Executive summary", "Objective and scope", "Target environment and application build",
            "Script, data and workload", "Actual load and duration", "Performance targets and acceptance criteria",
            "Transaction and request results", "Charts", "Telemetry", "Expected-workload behaviour",
            "Baseline comparison", "Findings", "AI recommendations (analysis, not measurements)", "Defect references",
            "Result files", "Limitations and missing information", "Execution state and performance verdict")


def _wait_file(path: Path, timeout: float = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return True
        time.sleep(0.5)
    return False


def test_reports_history_and_defects_from_real_runs(client, site):
    rt = client.app.state.runtime
    rt.settings.update({"performance": {"auto_report": True}})  # the default; the test fixture turns it off
    _jmeter(rt)
    port = site.rsplit(":", 1)[1]
    plan, sc = _plan(client, port)
    current = rt.performance.get_scenario(sc["id"]).model_dump()
    client.put(f"/api/performance/scenarios/{sc['id']}", json={**current, "description": "Checkout must hold 2 users"})
    view = client.get(f"/api/performance/plans/{plan['id']}/script").json()
    work = client.post("/api/performance/script/apply", json={"script": view["script"], "ops": [
        {"op": "add", "parent": "0/0", "type": "constant_timer", "values": {"delay_ms": 300}}]}).json()
    client.put(f"/api/performance/plans/{plan['id']}/script", json={"script": work["script"], "base_version": 1})
    client.put(f"/api/performance/plans/{plan['id']}/workload", json={"shape": "load", "users": 2, "ramp_up_s": 0,
                                                                      "hold_s": 10})
    full = client.get(f"/api/performance/plans/{plan['id']}").json()["plan"]
    full["sla"] = {"p95_ms": 5000, "max_error_pct": 1}
    client.put(f"/api/performance/plans/{plan['id']}", json=full)
    rt.performance.save_script_secret(plan["id"], "api_key", "Sup3rSecretValue-91")

    # Run 1 with a build; the report is made after the run without being asked
    r1 = client.post(f"/api/performance/scenarios/{sc['id']}/run", json={"build": "2.0.1"}).json()["run_id"]
    run1 = _wait(client, r1)
    assert run1["state"] == "COMPLETED" and run1["verdict"] == "PASS" and run1["build"] == "2.0.1", run1.get("error")
    pdf = Path(run1["folder"]) / "qa-report.pdf"
    assert _wait_file(pdf), "the run report was not made after the run"
    text = pr.plain(pr.pdf_text(pdf))  # letters and digits: punctuation read back from a PDF depends on the font
    for s in SECTIONS + (r1, "Load test", "2.0.1", "Checkout must hold 2 users", "UTC", "PASS", "Completed",
                         "No earlier completed run", "plan.jmx", "results.jtl"):
        assert pr.plain(s) in text, s
    assert pr.plain("Sup3rSecretValue-91") not in text
    assert pr.pdf_pages(pdf) >= 3

    # History by build and date
    today = date.today().isoformat()
    ids = lambda **q: [r["run_id"] for r in client.get("/api/performance/runs", params=q).json()["items"]]  # noqa: E731
    assert ids(build="2.0.1") == [r1] and ids(build="9.9") == []
    assert r1 in ids(date_from=today) and r1 not in ids(date_to=(date.today() - timedelta(days=2)).isoformat())
    assert client.get("/api/performance/runs", params={"date_from": "soon"}).status_code == 422

    # Run 2: the environment's build is used; the report compares it with run 1
    env = client.get("/api/performance/environments").json()["items"][0]
    client.put(f"/api/performance/environments/{env['id']}", json={**env, "build": "2.0.2"})
    r2 = client.post(f"/api/performance/scenarios/{sc['id']}/run").json()["run_id"]
    run2 = _wait(client, r2)
    assert run2["state"] == "COMPLETED" and run2["build"] == "2.0.2"
    assert _wait_file(Path(run2["folder"]) / "qa-report.pdf")
    text2 = pr.plain(pr.pdf_text(Path(run2["folder"]) / "qa-report.pdf"))
    assert pr.plain(f"Previous comparable run: {r1}") in text2 and pr.plain("Different application builds") in text2

    cmp = client.post("/api/performance/compare", json={"run_ids": [r1, r2]}).json()
    assert cmp["comparable"] and [r["build"] for r in cmp["runs"]] == ["2.0.1", "2.0.2"]
    assert [d["level"] for d in cmp["pairs"][0]["differences"]] == ["info"]
    assert {p["label"] for p in cmp["per_request"]} >= {"Session", "Profile"}
    rep = client.post("/api/performance/reports", json={"kind": "comparison", "run_ids": [r1, r2]}).json()["report"]
    assert rep["status"] == "ready" and rep["scope"] == "perf_comparison" and rep["has_pdf"], rep
    cpdf = client.get(f"/api/reports/{rep['id']}/pdf")
    assert cpdf.status_code == 200 and cpdf.content[:4] == b"%PDF"
    ctext = pr.plain(pr.pdf_text(rt.reports.file(rep["id"], "pdf")))
    for s in ("Comparability", "Results side by side", "Per request, 95th percentile", r1, r2, "Limitations"):
        assert pr.plain(s) in ctext, s
    cons = client.post("/api/performance/reports", json={"kind": "consolidated", "run_ids": [r1, r2]}).json()["report"]
    assert cons["has_pdf"] and cons["summary"]["pages"] >= 4
    assert client.post("/api/performance/reports", json={"kind": "comparison", "run_ids": [r1]}).status_code == 422

    # Run 3 misses a target: a defect draft, referenced in the report made after it
    full = client.get(f"/api/performance/plans/{plan['id']}").json()["plan"]
    full["sla"] = {"p95_ms": 0.5}
    client.put(f"/api/performance/plans/{plan['id']}", json=full)
    r3 = client.post(f"/api/performance/scenarios/{sc['id']}/run", json={"build": "2.0.3"}).json()["run_id"]
    run3 = _wait(client, r3)
    assert run3["verdict"] == "FAIL"
    assert _wait_file(Path(run3["folder"]) / "qa-report.pdf")
    drafts = [rt.defects.get(d["id"]) for d in rt.defects.list(source="performance") if d["source_run_id"] == r3]
    assert drafts and "build 2.0.3" in drafts[0]["repro_steps"] and drafts[0]["facts"]["severity_rationale"]
    text3 = pr.plain(pr.pdf_text(Path(run3["folder"]) / "qa-report.pdf"))
    refs = text3.split(pr.plain("Defect references"))[-1]
    assert pr.plain(drafts[0]["title"])[:40] in refs and pr.plain("Target not met") in text3

    # Made again on request; a stored secret in the printed text stops the PDF being saved
    again = client.post(f"/api/performance/runs/{r1}/report").json()
    assert again["pdf"] and again["pages"] >= 3 and again["error"] is None
    assert client.get(f"/api/performance/runs/{r1}/report").content[:4] == b"%PDF"
    assert b"Executive summary" in client.get(f"/api/performance/runs/{r1}/report?format=html").content
    rt.performance.save_script_secret(plan["id"], "api_key", "Checkout must hold 2 users")
    r4 = client.post(f"/api/performance/scenarios/{sc['id']}/run").json()["run_id"]
    run4 = _wait(client, r4)
    assert _wait_file(Path(run4["folder"]) / "qa-report.json")  # the automatic attempt has finished
    leak = client.post(f"/api/performance/runs/{r4}/report").json()
    assert not leak["pdf"] and "secret" in leak["error"]
    assert not (Path(client.get(f"/api/performance/runs/{r4}").json()["run"]["folder"]) / "qa-report.pdf").exists()


def test_text_read_from_a_pdf_with_ligatures_still_matches():
    """Some fonts print "fi" and "fl" as one ligature character; the read-back text and the secret check still match."""
    assert pr.plain("Result ﬁles · Session ﬂow") == "resultfilessessionflow"
    assert pr.secret_scan("token: abﬁcd123", ["abficd123"]) == ["abficd123"]
    assert "font-variant-ligatures: none" in pr.EXTRA_CSS


def test_background_reports_end_before_the_database_closes(client):
    """Reports are made one at a time, and closing the application waits for the one being made."""
    reports = client.app.state.runtime.reports
    reports.performance.settings.update({"performance": {"auto_report": True}})
    made = []

    def slow(run_id, pdf=True):
        time.sleep(0.3)
        made.append(run_id)
    reports.perf_run_report = slow
    reports.on_perf_finished("PERF-a", "PASS")
    reports.on_perf_finished("PERF-b", "PASS")
    time.sleep(0.05)
    reports.close()
    assert len(made) == 1  # the one being made finished; the waiting one was skipped (it can be made again)
    assert not reports._report_threads
    reports.on_perf_finished("PERF-c", "PASS")  # after closing: nothing starts
    assert "PERF-c" not in made and not reports._report_threads
