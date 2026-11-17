"""23.10.0: the simple AI Agent API (/api/ops/simple) inside SkyGuard: connect without a browser (learn=false),
commands, knowledge, tasks, the consequential-confirmation rule, view-only roles and the auto-trust setting."""

from __future__ import annotations

import pytest

from tests.test_auth_23334 import BROWSER, admin, make_user, sign_in


def connect(client, url="http://fleetops.corp.local/login"):
    r = client.post("/api/ops/simple/connect", json={"url": url, "learn": False})
    assert r.status_code == 201, r.text
    return r.json()


def test_connect_suggests_a_name_and_reuses_the_same_site(client):
    out = connect(client)
    assert out["created"] and out["application"]["name"] == "Fleetops" and out["session"] is None
    again = connect(client, "http://fleetops.corp.local/trips?page=2")
    assert not again["created"] and again["application"]["id"] == out["application"]["id"]
    bad = client.post("/api/ops/simple/connect", json={"url": "file:///C:/Windows/win.ini", "learn": False})
    assert bad.status_code == 400 and bad.json()["error_code"] == "URL_INVALID"
    bad = client.post("/api/ops/simple/connect", json={"url": "http://user:pw@fleetops.corp.local/", "learn": False})
    assert bad.status_code == 400


def test_status_knowledge_tasks_and_command(client):
    app = connect(client)["application"]
    st = client.get("/api/ops/simple/status").json()
    assert st["auto_trust"] is True and st["applications"][0]["id"] == app["id"] and st["watching"] == []
    k = client.get(f"/api/ops/simple/knowledge/{app['id']}").json()
    assert k["tasks"] == [] and k["rules"] == [] and k["records"] == 0
    assert client.get("/api/ops/simple/tasks").json()["tasks"] == []
    r = client.post("/api/ops/simple/command", json={"text": "Create two trips for tomorrow", "app_id": app["id"]})
    assert r.status_code == 200
    assert r.json()["reply"]["type"] == "no_workflows"


def test_a_password_in_a_command_is_withheld(client):
    app = connect(client)["application"]
    out = client.post("/api/ops/simple/command",
                      json={"text": "sign in as operator password is Op#2026x-secret", "app_id": app["id"]}).json()
    assert out["reply"]["type"] == "secret_intercepted"
    hist = client.get(f"/api/ops/chat/{out['conversation']}").text
    assert "Op#2026x-secret" not in hist


def test_view_only_roles_can_read_but_not_act(client):
    admin(client)
    make_user(client, "mgr", "manager")
    app = client.post("/api/ops/simple/connect", json={"url": "http://fleetops.corp.local/", "learn": False},
                      headers=BROWSER).json()["application"]
    client.post("/api/auth/logout", headers=BROWSER)
    assert sign_in(client, "mgr", "Pass-word-1").status_code == 200
    assert client.get(f"/api/ops/simple/knowledge/{app['id']}", headers=BROWSER).status_code == 200
    for path, body in (("/api/ops/simple/connect", {"url": "http://other.local/", "learn": False}),
                       ("/api/ops/simple/command", {"text": "show trips"}),
                       ("/api/ops/simple/explore", {"application_id": app["id"]})):
        assert client.post(path, json=body, headers=BROWSER).status_code == 403, path


def test_auto_trust_can_be_turned_off_by_an_admin(client, rt):
    assert rt.opsagent.agent.after_digest is not None
    r = client.put("/api/ops/config", json={"auto_trust": False})
    assert r.status_code == 200 and r.json()["config"]["auto_trust"] is False
    assert rt.opsagent.agent.after_digest is None
    client.put("/api/ops/config", json={"auto_trust": True})
    assert rt.opsagent.agent.after_digest is not None


def test_consequential_runs_need_the_confirmation_box(client, rt):
    from qa_platform.api.agent_simple import consequential
    assert consequential({"plan": {"stages": [{"workflow": "Approve trip", "title": "Approve trip"}]}})
    assert consequential({"plan": {"stages": [{"workflow": "Cancel booking", "title": "Cancel booking"}]}})
    assert not consequential({"plan": {"stages": [{"workflow": "Create trip", "title": "Create trip"}]}})


@pytest.mark.parametrize("path", ["/api/ops/simple/knowledge/nope", "/api/ops/simple/runs/nope/confirm"])
def test_unknown_ids_are_clean_errors(client, path):
    r = client.post(path, json={"preview_hash": "0" * 64}) if path.endswith("confirm") else client.get(path)
    assert r.status_code in (404, 400) and r.json()["error_code"]
