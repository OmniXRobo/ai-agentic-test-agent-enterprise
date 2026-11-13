"""23.5.0: AI Application Learning & Operations Agent API in SkyGuard (/api/ops).

Registration, accounts with consent-only credential saving, chat secret interception (nothing of the message is stored,
logged, audited or sent to the model), view-only roles, admin-only configuration and the audit hash chain.
"""

from __future__ import annotations

import json

from tests.test_auth_23334 import BROWSER, admin, make_user, sign_in

PASSWORD = "Op#2026x-very-secret"


def register(client, **extra):
    r = client.post("/api/ops/applications", json={"name": "FleetOps", "base_url": "http://127.0.0.1:9/", **extra})
    assert r.status_code == 201, r.text
    return r.json()["application"]


def test_info_and_application_registration(client):
    info = client.get("/api/ops/info").json()
    assert info["ok"] and info["version"]
    app = register(client)
    assert app["mode"] == "testing"
    assert client.get("/api/ops/applications").json()["applications"][0]["id"] == app["id"]
    bad = client.post("/api/ops/applications", json={"name": "X", "base_url": "javascript:alert(1)"})
    assert bad.status_code == 400 and bad.json()["error_code"]


def test_credentials_need_consent_and_never_come_back(client, rt):
    app = register(client)
    acc = client.post("/api/ops/accounts", json={"application_id": app["id"], "label": "Entry Operator",
                                                 "login_id": "operator", "role_key": "operator"}).json()["account"]
    r = client.put(f"/api/ops/accounts/{acc['id']}/credential", json={"password": PASSWORD, "consent": False})
    assert r.status_code == 400 and r.json()["error_code"] == "CONSENT_REQUIRED"
    r = client.put(f"/api/ops/accounts/{acc['id']}/credential", json={"password": PASSWORD, "consent": True})
    assert r.status_code == 200 and r.json()["account"]["has_credential"] is True
    listing = client.get("/api/ops/accounts").text
    assert PASSWORD not in listing
    # not in the knowledge database file, the audit or SkyGuard's log
    db = (rt.paths.root / "opsagent" / "knowledge.sqlite3").read_bytes()
    assert PASSWORD.encode() not in db
    assert PASSWORD not in client.get("/api/ops/audit").text
    revoked = client.post(f"/api/ops/accounts/{acc['id']}/revoke").json()["account"]
    assert revoked["has_credential"] is False


def test_password_typed_in_chat_is_intercepted(client, rt):
    register(client)
    calls = []
    rt.opsagent.agent.llm = type("Spy", (), {"name": "spy", "available": True,
                                             "chat": lambda self, *a, **k: calls.append((a, k)) or "{}"})()
    for text in (f"log in as operator, password is {PASSWORD}", "my OTP is 482913",
                 "the driver code: 7788", f"user operator pass {PASSWORD}"):
        out = client.post("/api/ops/chat", json={"text": text}).json()
        assert out["reply"]["type"] == "secret_intercepted", text
        conv = out["conversation"]
        history = json.dumps(client.get(f"/api/ops/chat/{conv}").json())
        assert "482913" not in history and PASSWORD not in history and "7788" not in history
    assert not calls  # nothing reached the model
    audit = client.get("/api/ops/audit").text
    assert PASSWORD not in audit and "482913" not in audit
    assert any(e["action"] == "chat.secret_intercepted" for e in client.get("/api/ops/audit").json()["audit"])
    db = (rt.paths.root / "opsagent" / "knowledge.sqlite3").read_bytes()
    assert PASSWORD.encode() not in db and b"482913" not in db
    # ordinary talk about passwords is not blocked
    ok = client.post("/api/ops/chat", json={"text": "the password field is required on the login page"}).json()
    assert ok["reply"]["type"] != "secret_intercepted"


def test_knowledge_approval_and_audit_chain(client):
    app = register(client)
    e = client.post("/api/ops/knowledge", json={"application_id": app["id"], "kind": "rule", "key": "trip unique",
                                                "data": {"type": "unique", "field": "trip"}}).json()["entry"]
    assert e["state"] == "awaiting_approval"
    a = client.post(f"/api/ops/knowledge/{e['id']}/approve", json={}).json()["entry"]
    assert a["state"] == "approved"
    v = client.get("/api/ops/audit/verify").json()
    assert v["ok"] and v["valid"] is True


def test_view_only_roles_cannot_change_anything(client):
    admin(client)
    make_user(client, "mgr", "manager")
    register(client)
    client.post("/api/auth/logout", headers=BROWSER)
    assert sign_in(client, "mgr", "Pass-word-1").status_code == 200
    assert client.get("/api/ops/applications", headers=BROWSER).status_code == 200
    r = client.post("/api/ops/applications", json={"name": "Other", "base_url": "http://127.0.0.1:8/"}, headers=BROWSER)
    assert r.status_code == 403
    assert client.post("/api/ops/chat", json={"text": "help"}, headers=BROWSER).status_code == 403


def test_configuration_and_restore_are_admin_only(client):
    admin(client)
    make_user(client, "tess", "tester")
    client.post("/api/auth/logout", headers=BROWSER)
    sign_in(client, "tess", "Pass-word-1")
    assert client.put("/api/ops/config", json={"headless": True}, headers=BROWSER).status_code == 403
    assert client.post("/api/ops/admin/knowledge-restore", json={"name": "x"}, headers=BROWSER).status_code == 403
    assert client.post("/api/ops/applications", json={"name": "Mine", "base_url": "http://127.0.0.1:7/"},
                       headers=BROWSER).status_code == 201


def test_other_web_pages_are_refused(client):
    r = client.post("/api/ops/chat", json={"text": "help"},
                    headers={"Origin": "http://evil.example", "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
