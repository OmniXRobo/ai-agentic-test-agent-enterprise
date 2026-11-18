"""23.11.0: Integration Comparison -> TFS defect creation (it answered "Unexpected Server Error") and the one Defect
Review and TFS publishing service: approval, permissions, mandatory fields, reconciliation of a lost response,
server errors and retry, redaction, real work item ID and URL."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from qa_platform.credentials import MemoryBackend
from qa_platform.main import create_app
from tests.fake_ado import PAT, FakeAzureDevOps
from tests.test_integration_workspace_23800 import A_CSV, B_CSV, W, analyze, approve_all, new, rows, run, save, upload_csv


@pytest.fixture()
def env(paths):
    ado = FakeAzureDevOps(severity=True)
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport())
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                     "make_active": True})
        assert r.status_code == 200, r.text
        yield c, ado, app.state.runtime


def _cmp_defect(c):
    pid, apps = new(c, "Order System", "Sales Portal")
    upload_csv(c, apps[0], A_CSV)
    upload_csv(c, apps[1], B_CSV.replace("83.33%", "85.00%"))
    cfg = analyze(c, pid)["config"]
    save(c, pid, approve_all(cfg))
    r = run(c, pid)
    bad = rows(r, "Order Completion (calculated)", "FAIL")
    return c.post(f"{W}/runs/{r['id']}/rows/{bad[0]['id']}/defect").json()["defect"]


def _bugs(ado):
    return {i: f for i, f in ado.items.items() if f.get("System.WorkItemType") == "Bug" and i >= 2000}


def test_comparison_defect_is_created_in_tfs_with_real_id_and_url(env):
    """Before the fix: 500 Unexpected server error (KeyError 'source'; QAError has no attribute 'code')."""
    c, ado, rt = env
    d = _cmp_defect(c)
    r = c.post(f"/api/comparison/defects/{d['id']}/tfs", json={"force": True})
    assert r.status_code == 200, r.text
    out = r.json()["defect"]
    tfs_id = out["tfs_id"]
    assert tfs_id in ado.items and out["status"] == "created_in_tfs"
    assert out["tfs_url"] == f"https://dev.azure.com/acme/Shop/_workitems/edit/{tfs_id}"
    item = ado.items[tfs_id]
    body = item["Microsoft.VSTS.TCM.ReproSteps"]
    for text in ("Order System", "Sales Portal", "Order Completion", "Confirmed mismatch", "Expected", "Actual",
                 "Likely application defect"):
        assert text in body, text
    assert "SkyGuard-" + out["correlation_id"] in item["System.Tags"]
    draft = c.get(f"/api/defects/drafts/{out['review_draft']}").json()["draft"]
    assert draft["status"] == "created" and draft["status_label"] == "Published to TFS"
    assert draft["source"] == "comparison" and draft["classification"] == "application_defect"
    assert draft["published_at"] and draft["approved_by"]
    actions = [e["action"] for e in c.get(f"/api/defects/drafts/{draft['id']}/events").json()["events"]]
    assert actions[:1] == ["drafted"] and "approved" in actions and actions[-1] == "published"
    # the same defect again: refused, nothing created twice
    again = c.post(f"/api/comparison/defects/{d['id']}/tfs", json={"force": True})
    assert again.status_code == 409 and again.json()["error_code"] == "CMP_DEFECT_IN_TFS"
    assert len(_bugs(ado)) == 1


def test_possible_duplicate_and_unreachable_tfs_are_clear_answers_not_500(env):
    c, ado, rt = env
    d = _cmp_defect(c)
    ado.items[1600] = {"System.Title": d["title"], "System.WorkItemType": "Bug", "System.State": "Active"}
    r = c.post(f"/api/comparison/defects/{d['id']}/tfs", json={})
    assert r.status_code == 409 and r.json()["error_code"] == "CMP_POSSIBLE_DUPLICATE"
    ado.unreachable = True
    r = c.post(f"/api/comparison/defects/{d['id']}/tfs", json={"force": True})
    assert r.status_code == 200 and r.json()["defect"]["queued"] is True  # kept for later, nothing lost
    draft = rt.comparison.review_draft(d["id"])
    assert draft["status"] == "publication_failed" and draft["correlation_id"] in draft["note"]
    ado.unreachable = False
    r = c.post(f"/api/defects/drafts/{draft['id']}/retry")
    assert r.status_code == 200, r.text
    assert c.get(f"/api/comparison/defects/{d['id']}").json()["defect"]["tfs_id"] == r.json()["draft"]["tfs_id"]


def test_lost_response_is_reconciled_not_created_twice(env):
    c, ado, rt = env
    d = _cmp_defect(c)
    draft = c.post(f"/api/comparison/defects/{d['id']}/review").json()["draft"]
    assert c.post(f"/api/defects/drafts/{draft['id']}/approve").status_code == 200
    ado.lose_next_create = True  # TFS creates the item, the answer never arrives
    r = c.post(f"/api/defects/drafts/{draft['id']}/publish")
    assert r.status_code == 200, r.text
    got = r.json()["draft"]
    assert got["status"] == "created" and got["reconciled"] is True
    assert len(_bugs(ado)) == 1 and got["tfs_id"] in ado.items


def test_server_error_keeps_draft_with_correlation_id_and_retry_works(env):
    c, ado, rt = env
    d = _cmp_defect(c)
    draft = c.post(f"/api/comparison/defects/{d['id']}/review").json()["draft"]
    c.post(f"/api/defects/drafts/{draft['id']}/approve")
    ado.fail_next_create = 500
    r = c.post(f"/api/defects/drafts/{draft['id']}/publish")
    assert r.status_code >= 400 and r.json()["error_code"] != "INTERNAL_ERROR"
    corr = r.json()["details"]["correlation_id"]
    assert corr in r.json()["message"]
    kept = c.get(f"/api/defects/drafts/{draft['id']}").json()["draft"]
    assert kept["status"] == "publication_failed" and kept["status_label"] == "Publication Failed" and not _bugs(ado)
    r = c.post(f"/api/defects/drafts/{draft['id']}/retry")
    assert r.status_code == 200 and r.json()["draft"]["correlation_id"] == corr and len(_bugs(ado)) == 1


def test_never_published_without_approval_and_view_only_cannot(env):
    c, ado, rt = env
    d = _cmp_defect(c)
    draft = c.post(f"/api/comparison/defects/{d['id']}/review").json()["draft"]
    r = c.post(f"/api/defects/drafts/{draft['id']}/publish")
    assert r.status_code == 409 and r.json()["error_code"] == "DEFECT_NOT_APPROVED" and not _bugs(ado)
    # approval is withdrawn by an edit: what is sent is what was approved
    c.post(f"/api/defects/drafts/{draft['id']}/approve")
    edited = c.patch(f"/api/defects/drafts/{draft['id']}", json={"title": "Order Completion differs between apps"})
    assert edited.json()["draft"]["status"] == "needs_review"
    assert c.post(f"/api/defects/drafts/{draft['id']}/publish").json()["error_code"] == "DEFECT_NOT_APPROVED"
    # a role without the permission (configured) cannot approve or publish
    rt.settings.update({"defects": {"approver_roles": ["tester"], "publisher_roles": ["tester"]}})
    r = c.post(f"/api/defects/drafts/{draft['id']}/approve")
    assert r.status_code == 403 and r.json()["error_code"] == "DEFECT_NOT_ALLOWED"
    assert not _bugs(ado)


def test_mandatory_field_from_tfs_preferences_is_asked_not_invented(env):
    c, ado, rt = env
    ado.required["Bug"] = {"Custom.FoundIn": ("Found In", ["Release 1", "Release 2"])}
    d = _cmp_defect(c)
    draft = c.post(f"/api/comparison/defects/{d['id']}/review").json()["draft"]
    c.post(f"/api/defects/drafts/{draft['id']}/approve")
    r = c.post(f"/api/defects/drafts/{draft['id']}/publish")
    assert r.status_code >= 400 and not _bugs(ado), r.text
    r = c.post(f"/api/defects/drafts/{draft['id']}/retry", json={"fields": {"Custom.FoundIn": "Release 2"}})
    assert r.status_code == 200, r.text
    assert ado.items[r.json()["draft"]["tfs_id"]]["Custom.FoundIn"] == "Release 2"


def test_secrets_are_redacted_from_triage_and_events():
    from qa_platform.defects import triage

    out = triage.redact({"error": "login failed password=hunter2 token: abc.def", "cookie": "sid=1",
                         "tb": "Authorization: Bearer eyJabcdefghijk.eyJabcdefghijk.sig"})
    text = str(out)
    assert "hunter2" not in text and "sid=1" not in text and "eyJabcdefghijk" not in text
    r = triage.rules_classify("comparison", {"result": "INCOMPLETE"})
    assert r["classification"] == "inconclusive"
    r = triage.rules_classify("comparison", {"result": "NEEDS_REVIEW", "record_key": "SO-1", "rule_status": "draft"})
    assert r["classification"] == "inconclusive" and any("not approved" in e for e in r["evidence"])
    assert triage.rules_classify("browser_environment", {"error": "net::ERR_CONNECTION_REFUSED"})["classification"] \
        == "environment"
    assert triage.rules_classify("interaction", {"error": "element is not attached to the DOM", "action": "click"})[
        "recommendation"] == "smart_wait"


def test_ai_view_is_validated_and_cannot_invent_a_class():
    from qa_platform.defects import triage

    class FakeAI:
        def __init__(self, answer):
            self.answer = answer

        def complete_json(self, system, user, model, **kw):
            assert "hunter2" not in user  # redacted before it reaches the model
            return model.model_validate(self.answer)

    facts = {"error": "Locator expected to contain text 'Welcome' password=hunter2", "action": "expect_text"}
    rules = triage.rules_classify("assertion", facts)
    good = triage.ai_classify(FakeAI({"classification": "timing", "confidence": 0.99, "reasons": ["slow"],
                                      "recommendation": "smart_wait"}), facts, rules)
    assert good["classification"] == "timing" and good["confidence"] == 0.95 and good["ai"]["used"]
    bad = triage.ai_classify(FakeAI({"classification": "cosmic_rays", "confidence": 0.9}), facts, rules)
    assert bad["classification"] == rules["classification"] and not bad["ai"]["used"]
    off = triage.ai_classify(None, facts, rules)
    assert off["classification"] == rules["classification"] and "not configured" in off["ai"]["reason"]
