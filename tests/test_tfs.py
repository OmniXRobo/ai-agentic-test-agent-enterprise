from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from qa_platform.credentials import MemoryBackend
from qa_platform.main import create_app
from tests.fake_ado import PAT, FakeAzureDevOps


@pytest.fixture()
def ado():
    return FakeAzureDevOps()


@pytest.fixture()
def tclient(paths, ado):
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport())
    with TestClient(app) as c:
        yield c


def _configure(c, pat=PAT):
    r = c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop",
                                                "pat": pat, "make_active": True})
    assert r.status_code == 200, r.text
    return r.json()


def test_pat_is_write_only_and_not_in_settings(tclient, paths):
    body = _configure(tclient)
    assert body["profiles"]["online"]["has_pat"] is True
    assert PAT not in tclient.get("/api/tfs/profiles").text
    assert PAT not in paths.settings_file.read_text()
    assert PAT not in tclient.get("/api/settings").text


def test_unconfigured_profile_reports_configuration_failure(tclient):
    r = tclient.post("/api/tfs/test/offline").json()
    assert r["ok"] is False
    assert r["checks"][0]["name"] == "Configuration" and r["checks"][0]["status"] == "FAIL"


def test_connection_test_all_pass(tclient):
    _configure(tclient)
    r = tclient.post("/api/tfs/test/online").json()
    assert r["ok"] is True, r
    assert [c["name"] for c in r["checks"]] == ["Server Reachability", "Authentication", "Organization/Collection",
                                                "Project", "Work Item API", "Test Management API"]


def test_connection_test_bad_pat_fails_authentication(tclient):
    _configure(tclient, pat="wrong")
    r = tclient.post("/api/tfs/test/online").json()
    auth = next(c for c in r["checks"] if c["name"] == "Authentication")
    assert auth["status"] == "FAIL" and "sign-in" in auth["detail"]
    assert next(c for c in r["checks"] if c["name"] == "Project")["status"] == "SKIP"
    assert tclient.get("/api/notifications").json()["unread"] >= 1  # TFS connection unavailable


def test_unreachable_server_returns_structured_error(tclient, ado):
    _configure(tclient)
    ado.unreachable = True
    r = tclient.get("/api/tfs/work-items?category=requirement")
    assert r.status_code == 502 and r.json()["error_code"] == "TFS_UNREACHABLE"


def test_query_by_category_and_search(tclient):
    _configure(tclient)
    stories = tclient.get("/api/tfs/work-items?category=requirement").json()
    assert [i["id"] for i in stories["items"]] == [118]
    hit = tclient.get("/api/tfs/work-items?q=%23118").json()
    assert [i["id"] for i in hit["items"]] == [118]
    hit = tclient.get("/api/tfs/work-items?q=payment").json()
    assert [i["id"] for i in hit["items"]] == [1280]


def test_detail_has_traceability(tclient):
    _configure(tclient)
    item = tclient.get("/api/tfs/work-items/118").json()["item"]
    assert item["acceptance_criteria"]
    assert [t["id"] for t in item["traceability"]["test_cases"]] == [1280]
    assert [d["id"] for d in item["traceability"]["defects"]] == [1420]


def test_bug_category_resolves_real_type_name(paths):
    ado = FakeAzureDevOps(bug_type="Defect")
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport())
    with TestClient(app) as c:
        _configure(c)
        meta = c.get("/api/tfs/meta").json()
        assert meta["categories"]["bug"]["default"] == "Defect"
        r = c.post("/api/tfs/work-items?category=bug", json={"fields": {"title": "Pay fails", "repro_steps": "1. x",
                                                                         "severity": "2 - High"}})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["item"]["type"] == "Defect"
        # Severity is not in this process template: skipped, not fatal.
        assert "Microsoft.VSTS.Common.Severity" in body["skipped_fields"]
        assert ado.items[body["item"]["id"]]["Microsoft.VSTS.TCM.ReproSteps"] == "1. x"


def test_update_and_cache_invalidation(tclient, ado):
    _configure(tclient)
    assert tclient.get("/api/tfs/work-items/1280").json()["item"]["state"] == "Design"
    r = tclient.patch("/api/tfs/work-items/1280", json={"fields": {"state": "Ready"}})
    assert r.status_code == 200
    assert tclient.get("/api/tfs/work-items/1280").json()["item"]["state"] == "Ready"


def test_test_plans_and_suites(tclient):
    _configure(tclient)
    plans = tclient.get("/api/tfs/test-plans").json()["plans"]
    assert plans[0]["name"] == "Sprint 4 Plan"
    suites = tclient.get("/api/tfs/test-plans/7/suites").json()["suites"]
    assert [s["name"] for s in suites] == ["Sprint 4 Plan", "Checkout"]
    assert suites[1]["parent_id"] == 70 and plans[0]["root_suite_id"] == 70


def test_dashboard_kpis_from_tfs(tclient):
    _configure(tclient)
    tfs = tclient.get("/api/dashboard/summary").json()["tfs"]
    assert tfs["connected"] is True
    assert tfs["kpis"] == {"requirement": 1, "testcase": 1, "open_defects": 1}


def test_requests_are_cached(tclient, ado):
    _configure(tclient)
    tclient.get("/api/tfs/work-items?category=requirement")
    n = len(ado.requests)
    tclient.get("/api/tfs/work-items?category=requirement")
    assert len(ado.requests) == n
