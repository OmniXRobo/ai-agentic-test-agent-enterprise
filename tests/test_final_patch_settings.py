"""Final patch: preference validation (sidebar, browser placement), sign-in method on saved logins, the browser check,
and AURA (never stores secrets, backup/export/import, confirmed reset)."""

from __future__ import annotations

import io
import zipfile


def test_settings_validation(client):
    assert client.patch("/api/settings", json={"ui": {"sidebar_collapsed": True}}).status_code == 200
    assert client.patch("/api/settings", json={"ui": {"sidebar_collapsed": "yes"}}).status_code in (400, 422)
    assert client.patch("/api/settings", json={"playback": {"window_mode": "maximized", "monitor": 1}}).status_code == 200
    assert client.patch("/api/settings", json={"playback": {"monitor": 99}}).status_code in (400, 422)
    assert client.patch("/api/settings", json={"playback": {"window_mode": "fullscreen"}}).status_code in (400, 422)
    assert client.patch("/api/settings", json={"comparison": {"browser": "msedge", "headless": False}}).status_code == 200
    assert client.patch("/api/settings", json={"comparison": {"tesseract_path": "C:/evil.exe"}}).status_code in (400, 422)


def test_saved_login_keeps_sign_in_method_and_never_returns_password(client):
    r = client.put("/api/automation/credentials", json={"application": "Portal", "role": "default", "username": "qa.tester",
                                                         "password": "Pw-123-secret", "login_url": "https://portal.local/login",
                                                         "auth_method": "windows"})
    assert r.status_code in (200, 201), r.text
    items = client.get("/api/automation/credentials").json()["items"]
    mine = next(c for c in items if c["application"] == "Portal")
    assert mine["auth_method"] == "windows" and mine["has_password"] and "Pw-123-secret" not in str(items)
    bad = client.put("/api/automation/credentials", json={"application": "Portal", "role": "x", "username": "u",
                                                           "auth_method": "kerberos-magic"})
    assert bad.status_code in (400, 422)
    bad_url = client.put("/api/automation/credentials", json={"application": "Portal", "role": "x", "username": "u",
                                                               "login_url": "javascript:alert(1)"})
    assert bad_url.status_code in (400, 422)


def test_browser_check_reports_without_switching(client):
    r = client.get("/api/automation/browsers/check", params={"launch_test": False})
    assert r.status_code == 200
    body = r.json()
    assert "browsers" in body and "monitors" in body


def test_aura_never_stores_secrets_and_backup_export_reset(client, rt):
    from qa_platform import aura

    svc = aura.current()
    assert svc is not None
    aura.learn("page_structure", "Portal", "/login", {"fields": [{"name": "username"}, {"name": "password", "value": "Pw-123-secret"}],
                                                        "headers": {"Authorization": "Bearer abc.def.ghi"}},
               source="test")
    aura.learn("page_structure", "Portal", "/home", {"title": "Home", "token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc"},
               source="test")
    entries = client.get("/api/aura/entries", params={"application": "Portal"}).json()["items"]
    text = str(entries)
    assert entries and "Pw-123-secret" not in text and "Bearer" not in text and "eyJhbGci" not in text
    assert client.get("/api/aura/integrity").json()["ok"] is True
    b = client.post("/api/aura/backup")
    assert b.status_code == 200
    z = client.get("/api/aura/export")
    assert z.status_code == 200 and zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert "Pw-123-secret" not in z.content.decode("latin-1")
    assert client.post("/api/aura/reset", json={"confirm": "yes"}).status_code == 409
    assert client.post("/api/aura/reset", json={"application": "Portal", "confirm": "RESET Portal"}).status_code == 200
    assert not client.get("/api/aura/entries", params={"application": "Portal"}).json()["items"]
    imp = client.post("/api/aura/import", content=z.content)
    assert imp.status_code == 200, imp.text
    assert client.get("/api/aura/entries", params={"application": "Portal"}).json()["items"]
