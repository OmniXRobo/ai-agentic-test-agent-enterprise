"""23.15.0: Create TFS Defect from Vulnerability Scanning, Execution History (failed and stopped runs) and Performance
Results (passed or failed) answered "Request validation failed" (the page sent severity as a number, the API declared
a string). The defect now carries readable steps, expected, actual and evidence, the run's screenshots and result files
are attached, and "Approve and send now" really creates the bug in TFS."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from qa_platform.credentials import MemoryBackend
from qa_platform.main import create_app
from qa_platform.storage.db import dumps
from tests.fake_ado import PAT, FakeAzureDevOps

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00"
       b"\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7V\x8e\xfb\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.fixture()
def env(paths):
    ado = FakeAzureDevOps(severity=True)
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport())
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                     "make_active": True})
        assert r.status_code == 200, r.text
        yield c, ado, app.state.runtime


def _functional_run(rt, run_id="RUN-20261125-072622-A7FFEA", status="STOPPED"):
    folder = rt.paths.evidence / run_id
    folder.mkdir(parents=True, exist_ok=True)
    s1, s2 = folder / "step-01.png", folder / "step-02-failed.png"
    s1.write_bytes(PNG)
    s2.write_bytes(PNG)
    rt.db.execute("INSERT INTO runs (run_id, kind, title, status, started_at, finished_at, test_case_id, summary) "
                  "VALUES (?,?,?,?,?,?,?,?)", (run_id, "functional", "TC1520 Customer can review the order", status,
                                                "2026-11-25T07:26:22+04:00", "2026-11-25T07:26:25+04:00", "1520",
                                                dumps({"error": "Stopped by the user.", "steps": 2})))
    steps = [{"index": 0, "description": "Open https://shop.example/cart", "status": "PASSED", "screenshot": str(s1)},
             {"index": 1, "description": "Click Checkout", "status": "FAILED", "error": "Stopped by the user.",
              "screenshot": str(s2)}]
    rt.db.execute("INSERT INTO execution_results (run_id, script_id, test_case_id, status, started_at, browser, base_url,"
                  " failed_step, error, steps, evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (run_id, "FWS-122180DD9F", "1520", status, "2026-11-25T07:26:22+04:00", "msedge",
                   "https://shop.example", 1, "Stopped by the user.", dumps(steps),
                   dumps({"folder": str(folder), "screenshots": [str(s2)], "video": None})))
    return run_id


def test_severity_as_number_is_accepted_and_body_is_readable(env):
    c, ado, rt = env
    run_id = _functional_run(rt)
    prev = c.get(f"/api/test-actions/exec/{run_id}/defect").json()["preview"]
    text = prev["repro_text"]
    assert "&quot;" not in text and "{\"" not in text  # readable, not an escaped JSON dump
    assert "1. Open https://shop.example/cart [PASSED]" in text and "2. Click Checkout [FAILED]" in text
    assert "Expected result:" in text and "Actual result:" in text and "stopped" in text.lower()
    assert {a["name"] for a in prev["attachments"]} == {"step-01.png", "step-02-failed.png"}
    assert "_files" not in prev and str(rt.paths.evidence) not in json.dumps(prev)  # no local paths in the answer
    # exactly what the page sends: severity and priority as numbers (this was "Request validation failed")
    r = c.post(f"/api/test-actions/exec/{run_id}/defect", json={"title": prev["title"], "severity": 3, "priority": 3,
                                                                "repro_text": text, "attach": True})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["status"] in ("draft", "needs_review") and len(d["evidence"]) == 2
    assert "<ol>" in d["repro_steps"] and "<b>Expected result:</b>" in d["repro_steps"]
    # labels are accepted too; a wrong level gets a message that names the field
    bad = c.post(f"/api/test-actions/exec/{run_id}/defect", json={"severity": 9})
    assert bad.status_code == 422 and "severity" in bad.json()["message"]
    ok = c.post(f"/api/test-actions/exec/{run_id}/defect", json={"severity": "2 - High", "priority": "2"})
    assert ok.status_code == 201, ok.text


def test_send_now_creates_the_bug_in_tfs_with_attachments(env):
    c, ado, rt = env
    run_id = _functional_run(rt, "RUN-20261124-093231-C591B3", "FAILED")
    prev = c.get(f"/api/test-actions/exec/{run_id}/defect").json()["preview"]
    r = c.post(f"/api/test-actions/exec/{run_id}/defect", json={"title": prev["title"], "severity": 2, "priority": 2,
                                                                "repro_text": prev["repro_text"], "send_now": True})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["status"] == "created" and out["tfs_id"] in ado.items, out
    item = ado.items[out["tfs_id"]]
    assert "Click Checkout" in item["Microsoft.VSTS.TCM.ReproSteps"]
    assert set(ado.attachments) == {"step-01.png", "step-02-failed.png"}
    assert sorted(out["attachments"]) == ["step-01.png", "step-02-failed.png"]


def test_review_then_approve_then_publish_pushes_to_tfs(env):
    c, ado, rt = env
    run_id = _functional_run(rt, "RUN-20261125-000001-AAAAAA", "FAILED")
    d = c.post(f"/api/test-actions/exec/{run_id}/defect", json={"severity": 3, "priority": 3}).json()
    assert c.post(f"/api/defects/drafts/{d['id']}/approve", json={"note": "ok"}).status_code == 200
    r = c.post(f"/api/defects/drafts/{d['id']}/publish", json={"allow_duplicate": True})
    assert r.status_code == 200, r.text
    assert r.json()["draft"]["tfs_id"] in ado.items and ado.attachments


def _perf_run(rt, run_id, failed):
    folder = rt.paths.root / "perf" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "metrics.json").write_text("{}", encoding="utf-8")
    (folder / "qa-report.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    checks = [{"name": "95th percentile", "scope": "ALL REQUESTS", "limit": 50, "actual": 89, "unit": "ms",
               "status": "FAIL" if failed else "PASS"}]
    metrics = {"overall": {"samples": 1611, "errors": 0, "error_pct": 0, "avg_ms": 42, "p95_ms": 89, "throughput": 4.6,
                           "top_errors": []}, "requests": [{"label": "GET /", "avg_ms": 42, "p95_ms": 89, "error_pct": 0}],
               "peak_threads": 5}
    rt.db.execute("INSERT INTO perf_runs (run_id, plan_id, kind, name, status, started_at, finished_at, folder, metrics,"
                  " sla, config) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (run_id, "PLAN-1", "load", "https://www.saucedemo.com/", "COMPLETED", "2026-11-25T07:37:28+04:00",
                   "2026-11-25T07:43:24+04:00", str(folder), dumps(metrics),
                   dumps({"checks": checks, "failed": [c for c in checks if c["status"] == "FAIL"]}),
                   dumps({"environment": {"name": "online store", "base_url": "https://www.saucedemo.com/"}})))
    rt.db.execute("INSERT INTO runs (run_id, kind, title, status, started_at, summary) VALUES (?,?,?,?,?,?)",
                  (run_id, "performance", "Load test", "PASSED" if not failed else "FAILED",
                   "2026-11-25T07:37:28+04:00", dumps({})))


@pytest.mark.parametrize("failed", [False, True])
def test_performance_result_passed_or_failed_gets_a_defect_with_report(env, failed):
    c, ado, rt = env
    run_id = f"PERF-20261125-073728-9FDEC{int(failed)}"
    _perf_run(rt, run_id, failed)
    prev = c.get(f"/api/test-actions/exec/{run_id}/defect").json()
    assert prev["ok"], prev
    p = prev["preview"]
    assert p["source"] == "performance"
    assert "1611 samples" in p["repro_text"] or "95th percentile" in p["repro_text"]
    assert {"qa-report.pdf", "metrics.json"} <= {a["name"] for a in p["attachments"]}
    r = c.post(f"/api/test-actions/exec/{run_id}/defect", json={"severity": 3, "priority": 3, "send_now": True})
    assert r.status_code == 201, r.text
    assert r.json()["tfs_id"] in ado.items and "qa-report.pdf" in ado.attachments
