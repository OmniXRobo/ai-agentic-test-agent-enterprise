"""23.3.34: accounts, sign-in, roles (admin, tester, developer, manager) and Network access."""

from __future__ import annotations

import pytest

from qa_platform import auth
from qa_platform.api.auth import bind_host
from qa_platform.api.guard import refusal as guard_refusal
from qa_platform.auth import UserStore, hash_password, verify_password
from qa_platform.errors import QAError

BROWSER = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
ADMIN_PW = "Sky-Guard-2026"


def sign_in(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password}, headers=BROWSER)


def admin(client):
    """Signs in as admin and replaces the starting password."""
    assert sign_in(client, "admin", "admin").json()["user"]["must_change"] is True
    r = client.post("/api/auth/password", json={"current": "admin", "new": ADMIN_PW}, headers=BROWSER)
    assert r.status_code == 200, r.text


def make_user(client, username, role, password="Pass-word-1"):
    r = client.post("/api/auth/users", json={"username": username, "password": password, "role": role}, headers=BROWSER)
    assert r.status_code == 201, r.text
    return r.json()["user"]


def test_passwords_are_hashed_with_salt():
    h1, h2 = hash_password("Secret-123", iterations=1000), hash_password("Secret-123", iterations=1000)
    assert h1 != h2 and "Secret-123" not in h1 and h1.startswith("pbkdf2_sha256$1000$")
    assert verify_password("Secret-123", h1) and not verify_password("secret-123", h1)
    assert not verify_password("x", "garbage")


def test_browser_needs_to_sign_in_but_local_programs_do_not(client):
    assert client.get("/api/health").status_code == 200  # installer, launcher, PowerShell: unchanged
    r = client.get("/api/health", headers=BROWSER)
    assert r.status_code == 401 and r.json()["error_code"] == "AUTH_REQUIRED"
    assert client.get("/", headers=BROWSER).status_code == 200  # the page itself loads and shows the sign-in screen
    assert client.get("/api/branding", headers=BROWSER).status_code == 200


def test_first_sign_in_must_replace_the_starting_admin_password(client):
    r = sign_in(client, "admin", "admin")
    assert r.status_code == 200 and r.json()["user"]["role"] == "admin"
    assert client.get("/api/settings", headers=BROWSER).json()["error_code"] == "PASSWORD_CHANGE_REQUIRED"
    weak = client.post("/api/auth/password", json={"current": "admin", "new": "short"}, headers=BROWSER)
    assert weak.json()["error_code"] == "PASSWORD_TOO_SHORT"
    assert client.post("/api/auth/password", json={"current": "admin", "new": ADMIN_PW}, headers=BROWSER).status_code == 200
    assert client.get("/api/settings", headers=BROWSER).status_code == 200
    me = client.get("/api/auth/me", headers=BROWSER).json()["user"]
    assert me["must_change"] is False and me["username"] == "admin"
    client.post("/api/auth/logout", headers=BROWSER)
    assert sign_in(client, "admin", "admin").status_code == 401
    assert sign_in(client, "admin", ADMIN_PW).status_code == 200


def test_session_cookie_is_httponly_and_same_site_strict(client):
    r = sign_in(client, "admin", "admin")
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert "expires" not in cookie and "max-age" not in cookie  # ends with the browser


def test_tester_has_full_access_but_cannot_manage_users(client):
    admin(client)
    make_user(client, "tina", "tester")
    client.post("/api/auth/logout", headers=BROWSER)
    sign_in(client, "tina", "Pass-word-1")
    assert client.patch("/api/settings", json={"ui": {"theme": "light"}}, headers=BROWSER).status_code == 200
    assert client.post("/api/notifications/read", json={}, headers=BROWSER).status_code == 200
    r = client.get("/api/auth/users", headers=BROWSER)
    assert r.status_code == 403 and r.json()["error_code"] == "ADMIN_ONLY"
    assert client.put("/api/auth/network", json={"enabled": True}, headers=BROWSER).status_code == 403
    assert client.post("/api/app/exit", headers=BROWSER).status_code == 403


@pytest.mark.parametrize("role", ["developer", "manager"])
def test_developer_and_manager_are_view_only(client, role):
    admin(client)
    make_user(client, "viewer1", role)
    client.post("/api/auth/logout", headers=BROWSER)
    sign_in(client, "viewer1", "Pass-word-1")
    assert client.get("/api/settings", headers=BROWSER).status_code == 200
    assert client.get("/api/automation/scripts", headers=BROWSER).status_code == 200
    for method, path, body in (("PATCH", "/api/settings", {"ui": {"theme": "light"}}),
                               ("POST", "/api/automation/scripts", {"name": "x"}),
                               ("DELETE", "/api/automation/scripts/abc", None),
                               ("POST", "/api/schedules", {}),
                               ("PUT", "/api/voice/settings", {})):
        r = client.request(method, path, json=body, headers=BROWSER)
        assert r.status_code == 403 and r.json()["error_code"] == "VIEW_ONLY", (method, path)
    # Playing existing tests and marking notifications read pass the role check (they may still fail on their own).
    assert client.post("/api/notifications/read", json={}, headers=BROWSER).status_code == 200
    r = client.post("/api/automation/scripts/missing/run", json={}, headers=BROWSER)
    assert r.json().get("error_code") != "VIEW_ONLY"
    assert client.get("/api/auth/users", headers=BROWSER).status_code == 403


def test_view_only_voice_commands_cannot_change_anything(client):
    admin(client)
    make_user(client, "mgr", "manager")
    client.post("/api/auth/logout", headers=BROWSER)
    sign_in(client, "mgr", "Pass-word-1")
    talk = {"conversation_id": "conv-view-only-1", "source": "typed"}
    r = client.post("/api/voice/commands", json={**talk, "text": "Hami, create a new test plan for Sprint 25"},
                    headers=BROWSER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("status") == "failed" and "view only" in body.get("say", "").lower(), body
    r = client.post("/api/voice/commands", json={**talk, "text": "Hami, go to test suites"}, headers=BROWSER)
    assert r.json().get("status") == "completed"  # looking and navigating still work


def test_five_wrong_passwords_lock_the_account(client):
    for _ in range(auth.MAX_FAILURES):
        assert sign_in(client, "admin", "wrong-password").status_code == 401
    r = sign_in(client, "admin", "admin")
    assert r.status_code == 429 and r.json()["error_code"] == "ACCOUNT_LOCKED"


def test_admin_manages_accounts_and_the_last_admin_is_protected(client):
    admin(client)
    dev = make_user(client, "dev2", "developer")
    assert client.post("/api/auth/users", json={"username": "DEV2", "password": "Pass-word-1", "role": "tester"},
                       headers=BROWSER).status_code == 409  # names are not case sensitive
    assert client.post("/api/auth/users", json={"username": "bad name", "password": "Pass-word-1", "role": "tester"},
                       headers=BROWSER).json()["error_code"] == "USERNAME_INVALID"
    me_id = client.get("/api/auth/me", headers=BROWSER).json()["user"]["id"]
    assert client.delete(f"/api/auth/users/{me_id}", headers=BROWSER).json()["error_code"] == "OWN_ACCOUNT"
    assert client.patch(f"/api/auth/users/{me_id}", json={"role": "tester"}, headers=BROWSER).json()["error_code"] == "OWN_ACCOUNT"
    # role change, password reset and disable sign the person out
    r = client.patch(f"/api/auth/users/{dev['id']}", json={"role": "tester", "password": "New-pass-99"}, headers=BROWSER)
    assert r.json()["user"]["role"] == "tester"
    assert client.patch(f"/api/auth/users/{dev['id']}", json={"disabled": True}, headers=BROWSER).json()["user"]["disabled"]
    client.post("/api/auth/logout", headers=BROWSER)
    assert sign_in(client, "dev2", "New-pass-99").json()["error_code"] == "ACCOUNT_DISABLED"
    sign_in(client, "admin", ADMIN_PW)
    assert client.delete(f"/api/auth/users/{dev['id']}", headers=BROWSER).status_code == 200
    names = [u["username"] for u in client.get("/api/auth/users", headers=BROWSER).json()["users"]]
    assert names == ["admin"]


def test_last_admin_rules_in_the_store(rt):
    store = UserStore(rt.db, iterations=1000)
    admin_id = store.list()[0]["id"]
    with pytest.raises(QAError) as e:
        store.update(admin_id, role="tester")
    assert e.value.error_code == "LAST_ADMIN"
    with pytest.raises(QAError):
        store.delete(admin_id)
    second = store.create("boss", "Boss-pass-1", "admin")
    store.update(admin_id, role="manager")  # another admin exists now
    with pytest.raises(QAError):
        store.update(second["id"], disabled=True)


def test_sessions_end_after_idle_time(rt, monkeypatch):
    store = UserStore(rt.db, iterations=1000)
    user = store.authenticate("admin", "admin")
    token = store.start_session(user)
    assert store.session_user(token).username == "admin"
    real = auth.time.time
    monkeypatch.setattr(auth.time, "time", lambda: real() + auth.IDLE_SECONDS + 5)
    assert store.session_user(token) is None


def test_network_access_setting_and_links(client):
    admin(client)
    n = client.get("/api/auth/network", headers=BROWSER).json()
    assert n["enabled"] is False and n["active"] is False and n["links"][0].endswith(f":{n['port']}/")
    n = client.put("/api/auth/network", json={"enabled": True}, headers=BROWSER).json()
    assert n["enabled"] is True and n["restart_needed"] is True
    assert client.app.state.runtime.settings.get()["network"]["enabled"] is True


def test_bind_host_follows_network_access():
    assert bind_host({}) == "127.0.0.1"
    assert bind_host({"server": {"host": "127.0.0.1"}, "network": {"enabled": False}}) == "127.0.0.1"
    assert bind_host({"server": {"host": "0.0.0.0"}}) == "127.0.0.1"  # only the switch opens the network
    assert bind_host({"network": {"enabled": True}}) == "0.0.0.0"


def test_guard_accepts_this_pcs_name_only_with_network_access():
    by_name = {"host": "qa-pc01:8765", "origin": "http://qa-pc01:8765", "sec-fetch-site": "same-origin"}
    assert guard_refusal("GET", by_name)  # off: other names refused (DNS rebinding)
    assert guard_refusal("GET", by_name, network=True) is None
    assert guard_refusal("POST", by_name, network=True) is None
    # still only the dashboard page itself may change things
    assert guard_refusal("POST", {**by_name, "origin": "http://evil.example"}, network=True)
    assert guard_refusal("POST", {**by_name, "sec-fetch-site": "cross-site"}, network=True)


def test_closing_the_window_keeps_the_server_with_network_access(client):
    client.app.state.network_active = True
    assert client.get("/api/app/status").json()["closing_window_stops"] is False


def test_refusal_table():
    view = auth.User(5, "v", "", "developer")
    tester = auth.User(6, "t", "", "tester")
    assert auth.refusal(view, "GET", "/api/reports") is None
    assert auth.refusal(view, "POST", "/api/reports/r1/pdf") is None
    assert auth.refusal(view, "POST", "/api/performance/plans/p1/run") is None
    assert auth.refusal(view, "POST", "/api/reports")[0] == "VIEW_ONLY"
    assert auth.refusal(view, "DELETE", "/api/reports/r1")[0] == "VIEW_ONLY"
    assert auth.refusal(tester, "DELETE", "/api/reports/r1") is None
    assert auth.refusal(tester, "GET", "/api/auth/network") is None  # the link can be shown to anyone with access
    assert auth.refusal(tester, "GET", "/api/auth/users")[0] == "ADMIN_ONLY"
    forced = auth.User(7, "n", "", "admin", must_change=True)
    assert auth.refusal(forced, "GET", "/api/health")[0] == "PASSWORD_CHANGE_REQUIRED"
    assert auth.refusal(forced, "POST", "/api/auth/password") is None
