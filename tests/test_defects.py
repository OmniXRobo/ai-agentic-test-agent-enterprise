"""Stage 6: failure analysis, duplicate detection, defect creation modes, functional and performance defects."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from qa_platform.credentials import MemoryBackend
from qa_platform.defects import analysis
from qa_platform.defects.service import similarity
from qa_platform.main import create_app
from qa_platform.storage.db import dumps
from tests.fake_ado import PAT, FakeAzureDevOps
from tests.test_automation import HAVE_PLAYWRIGHT, _login_script

needs_browser = pytest.mark.skipif(not HAVE_PLAYWRIGHT, reason="playwright is not installed")


def _execution(**over):
    base = {"run_id": "RUN-20260101-000000-AAAAAA", "script_id": "SCR-ABCDEFGHIJ", "test_case_id": "101",
            "user_story_id": "118", "status": "FAILED", "failed_step": 2, "error": "Verify welcome: expected 'Welcome bob'",
            "browser": "chromium", "base_url": "http://shop", "started_at": "2026-01-01T10:00:00+04:00",
            "steps": [{"index": 0, "description": "Open login", "status": "PASSED"},
                      {"index": 1, "description": "Click Sign in", "status": "PASSED"},
                      {"index": 2, "description": "Verify welcome", "status": "FAILED", "screenshot": "/x/step-03-failed.png"}],
            "evidence": {"screenshots": ["/x/step-03-failed.png"]}}
    base.update(over)
    return base


SCRIPT = {"id": "SCR-ABCDEFGHIJ", "name": "Login works", "steps": [
    {"action": "goto", "value": "http://shop/login"},
    {"action": "click", "target": {"selector": "#login-btn", "description": "Sign in"}},
    {"action": "expect_text", "target": {"selector": "#welcome", "description": "Welcome"}, "value": "Welcome bob"}]}


def test_classification_states_observations_not_causes():
    d = analysis.functional_draft(_execution(), SCRIPT, [])
    assert d["category"] == "assertion" and d["product_defect"] and d["severity"] == 3
    assert "Welcome bob" in d["repro_steps"] and "not determined" in d["repro_steps"]
    assert "Open login" in d["repro_steps"] and d["evidence"][0]["path"].endswith("step-03-failed.png")
    loc = analysis.functional_draft(_execution(failed_step=1, error="Element not found: Sign in"), SCRIPT,
                                    [{"step_index": 1, "result": "failed", "original_selector": "#login-btn"}])
    assert loc["category"] == "locator" and not loc["product_defect"]
    setup = analysis.functional_draft(_execution(failed_step=1, error="No saved credential 'password' for role 'admin'."), SCRIPT, [])
    assert setup["category"] == "test_setup" and not setup["product_defect"]
    nav = analysis.functional_draft(_execution(failed_step=0, error="net::ERR_CONNECTION_REFUSED"), SCRIPT, [])
    assert nav["category"] == "navigation" and nav["severity"] == 2
    # the same failure in another run has the same signature
    assert analysis.functional_draft(_execution(run_id="RUN-20260102-000000-BBBBBB"), SCRIPT, [])["signature"] == d["signature"]


def test_performance_draft_from_failed_sla():
    run = {"run_id": "PERF-1", "plan_id": "PERF-AAAAAAAAAA", "kind": "load", "name": "Checkout", "folder": "/p",
           "started_at": "2026-01-01", "metrics": {"overall": {"samples": 500, "avg_ms": 900, "p95_ms": 2400,
                                                               "error_pct": 0.2, "throughput": 12, "top_errors": []}},
           "sla": {"status": "FAIL", "failed": [{"name": "95th percentile response time", "scope": "Pay", "limit": 1500,
                                                  "actual": 2400, "unit": "ms", "status": "FAIL"}]}}
    d = analysis.performance_draft(run, {"profile": {"users": 50, "ramp_up_s": 60, "duration_s": 600, "think_time_ms": 1000}})
    assert d["category"] == "performance_latency" and "2400 ms exceeds 1500 ms (Pay)" in d["title"]
    assert "50 users" in d["repro_steps"]
    run["sla"]["failed"].append({"name": "Error rate", "scope": "ALL REQUESTS", "limit": 1, "actual": 7.5, "unit": "%", "status": "FAIL"})
    assert analysis.performance_draft(run, None)["category"] == "performance_errors"
    assert analysis.performance_draft({**run, "sla": {"status": "PASS", "failed": []}}, None) is None


def test_similarity_scores_and_reasons():
    draft = {"title": "Login works: unexpected result at 'Verify welcome'", "repro_steps": "<p>Element #welcome</p>",
             "facts": {"selector": "#welcome"}, "test_case_id": "101"}
    same = {"title": "Login works: unexpected result at 'Verify welcome'", "repro_steps": "seen on #welcome",
            "linked_test_cases": ["101"]}
    other = {"title": "Checkout button unresponsive", "repro_steps": "", "linked_test_cases": []}
    s1, reasons = similarity(draft, same)
    assert s1 >= 0.85 and any("same element" in r for r in reasons) and any("Test Case #101" in r for r in reasons)
    assert similarity(draft, other)[0] < 0.6


@pytest.fixture()
def env(paths):
    ado = FakeAzureDevOps(severity=True)
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport())
    with TestClient(app) as c:
        c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                "make_active": True})
        rt = c.app.state.runtime
        rt.settings.update({"playback": {"visual_delay_ms": 0, "headless": True}})
        rt.automation.creds.save("Shop", "admin", "alice", "s3cret-pass", None)
        yield c, ado, rt


def _failing_script(rt, base, expect="Welcome bob"):
    script = _login_script(base)
    script.steps[-1].value = expect
    script.steps[-1].timeout_ms = 600
    return rt.automation.scripts.save(script)


def _wait_draft(rt, run_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = rt.db.query("SELECT id FROM defect_drafts WHERE occurrences LIKE ?", (f"%{run_id}%",))
        if rows:
            time.sleep(0.3)  # let duplicate check / automatic creation finish
            return rt.defects.get(rows[0]["id"])
        time.sleep(0.1)
    return None


@needs_browser
def test_review_mode_drafts_then_creates_real_defect_with_evidence(env, site):
    c, ado, rt = env
    script = _failing_script(rt, site)
    run = rt.automation.run_script(script.id, headless=True, background=False)
    assert run["status"] == "FAILED"
    draft = _wait_draft(rt, run["run_id"])
    assert draft and draft["status"] == "draft" and draft["category"] == "assertion"
    assert draft["duplicates"] == []  # the only open bug is unrelated
    assert "s3cret" not in draft["repro_steps"] and not ado.attachments  # nothing sent to TFS yet
    r = c.post(f"/api/defects/drafts/{draft['id']}/create", json={"severity": 2}).json()
    assert r["ok"], r
    created = r["draft"]
    item = ado.items[created["tfs_id"]]
    assert item["System.WorkItemType"] == "Bug" and item["Microsoft.VSTS.Common.Severity"] == "2 - High"
    assert "Welcome bob" in item["Microsoft.VSTS.TCM.ReproSteps"] and "QA-Automation" in item["System.Tags"]
    rels = dict((rel, t) for rel, t in ado.relations[created["tfs_id"]] if rel == "System.LinkTypes.Related")
    assert rels  # linked to the failing test case
    assert any(name.endswith("-failed.png") for name in ado.attachments)
    # The same failure again: one more occurrence and a comment on the TFS defect, not a new draft.
    run2 = rt.automation.run_script(script.id, headless=True, background=False)
    again = _wait_draft(rt, run2["run_id"])
    assert again["id"] == draft["id"] and len(again["occurrences"]) == 2
    assert any(run2["run_id"] in cmt for cmt in ado.items[created["tfs_id"]].get("_comments", []))
    assert c.post(f"/api/defects/drafts/{draft['id']}/create").json()["error_code"] == "DEFECT_ALREADY_CREATED"


@needs_browser
def test_automatic_mode_skips_likely_duplicates_and_links(env, site):
    c, ado, rt = env
    c.put("/api/defects/mode", json={"creation_mode": "automatic"})
    # filed earlier from the same failure, so the title matches
    ado.items[1500] = {"System.Title": "Login works: unexpected result - Verify #welcome shows 'Welcome bob'",
                       "System.WorkItemType": "Bug", "System.State": "Active",
                       "Microsoft.VSTS.TCM.ReproSteps": "The #welcome message is wrong"}
    script = _failing_script(rt, site)
    run = rt.automation.run_script(script.id, headless=True, background=False)
    draft = _wait_draft(rt, run["run_id"])
    assert draft["duplicates"][0]["id"] == 1500 and draft["duplicates"][0]["score"] >= 0.85
    assert draft["status"] == "draft" and "likely duplicate of #1500" in draft["note"]
    linked = c.post(f"/api/defects/drafts/{draft['id']}/link", json={"tfs_id": 1500}).json()["draft"]
    assert linked["status"] == "linked" and ado.items[1500]["_comments"]


@needs_browser
def test_automatic_mode_creates_product_defects_only(env, site):
    c, ado, rt = env
    c.put("/api/defects/mode", json={"creation_mode": "automatic"})
    run = rt.automation.run_script(_failing_script(rt, site).id, headless=True, background=False)
    draft = _wait_draft(rt, run["run_id"])
    assert draft["status"] == "created" and draft["tfs_id"] in ado.items
    # a test setup problem (no saved credential) is never filed automatically
    rt.automation.creds.delete("shop|admin")
    run2 = rt.automation.run_script(_failing_script(rt, site, "Welcome alice").id, headless=True, background=False)
    d2 = _wait_draft(rt, run2["run_id"])
    assert d2["category"] == "test_setup" and d2["status"] == "needs_review" and d2["tfs_id"] is None


@needs_browser
def test_disabled_mode_makes_no_drafts_but_manual_analysis_works(env, site):
    c, ado, rt = env
    c.put("/api/defects/mode", json={"creation_mode": "disabled"})
    before = set(ado.items)
    run = rt.automation.run_script(_failing_script(rt, site).id, headless=True, background=False)
    assert _wait_draft(rt, run["run_id"], timeout=3) is None
    d = c.post("/api/defects/analyze", json={"kind": "functional", "run_id": run["run_id"]}).json()["draft"]
    assert d["category"] == "assertion" and d["status"] == "draft"
    dismissed = c.post(f"/api/defects/drafts/{d['id']}/dismiss", json={"reason": "Expected text changed by design"}).json()["draft"]
    assert dismissed["status"] == "dismissed" and "changed by design" in dismissed["note"]
    assert not c.get("/api/defects/drafts?status=open").json()["items"] and set(ado.items) == before  # nothing filed


def test_missing_fields_are_skipped_and_issue_type_detected(paths):
    ado = FakeAzureDevOps(bug_type="Issue", severity=False)
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport())
    with TestClient(app) as c:
        c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                "make_active": True})
        rt = c.app.state.runtime
        rt.db.execute(
            "INSERT INTO perf_runs (run_id, plan_id, kind, name, status, started_at, folder, metrics, sla)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            ("PERF-20260101-000000-ABCDEF", "PERF-AAAAAAAAAA", "load", "Checkout", "FAILED", "2026-01-01", str(paths.root),
             dumps({"overall": {"samples": 10, "avg_ms": 50, "p95_ms": 90, "error_pct": 20.0, "throughput": 3, "top_errors": [{"error": "500 Server Error", "count": 2}]}}),
             dumps({"status": "FAIL", "failed": [{"name": "Error rate", "scope": "ALL REQUESTS", "limit": 1, "actual": 20.0, "unit": "%", "status": "FAIL"}]})))
        d = c.post("/api/defects/analyze", json={"kind": "performance", "run_id": "PERF-20260101-000000-ABCDEF"}).json()["draft"]
        assert d["category"] == "performance_errors" and d["source"] == "performance"
        created = c.post(f"/api/defects/drafts/{d['id']}/create").json()["draft"]
        item = ado.items[created["tfs_id"]]
        assert item["System.WorkItemType"] == "Issue" and "Microsoft.VSTS.Common.Severity" not in item
        assert "Microsoft.VSTS.Common.Severity" in created["note"]  # reported as skipped
        assert "Performance" in item["System.Tags"]
        listing = c.get("/api/defects/drafts", params={"source": "performance"}).json()["items"]
        assert listing[0]["status"] == "created"
        assert c.get("/api/defects/drafts/999").json()["error_code"] == "DEFECT_DRAFT_NOT_FOUND"
        assert c.put("/api/defects/mode", json={"creation_mode": "yolo"}).status_code == 422
