"""Application Discovery & Update: authenticated, interactive exploration and versioned Application Memory."""

from __future__ import annotations

import time

import pytest

from qa_platform.automation.discovery import (business_flows, default_application, diff_memory, is_destructive,
                                              page_name)
from qa_platform.credentials import APP_CREDENTIAL_NAMESPACE


def _wait(client, run_id, states, timeout=90):
    deadline = time.monotonic() + timeout
    job = None
    while time.monotonic() < deadline:
        job = client.get(f"/api/automation/discovery-jobs/{run_id}").json()["job"]
        if job["state"] in states:
            return job
        time.sleep(0.2)
    raise AssertionError(f"discovery did not reach {states}; last state {job and job['state']}")


def _start(client, url, **body):
    r = client.post("/api/automation/discoveries", json={"base_url": url, "max_pages": 10, "max_depth": 3,
                                                          "headless": True, **body})
    assert r.status_code == 202, r.text
    return r.json()["run_id"]


def _paths(result):
    return {p["url"].rsplit("/", 1)[-1] for p in result["pages"] if not p.get("error")}


# ---- unit ----------------------------------------------------------------------------------------


def test_destructive_actions_page_names_and_application_names():
    for text in ("Delete", "Remove", "Approve", "Submit Payment", "Transfer", "Terminate", "Cancel Order", "Publish",
                 "Finish", "Reset App State"):
        assert is_destructive(text), text
    for text in ("Login", "Checkout", "Add to cart", "Open Menu", "Continue Shopping", "Cancel"):
        assert not is_destructive(text), text
    assert page_name("https://x/inventory.html", "Swag Labs") == "InventoryPage"
    assert page_name("https://x/checkout-step-one.html", None) == "CheckoutStepOnePage"
    assert page_name("https://x/", "Swag Labs", "login") == "LoginPage"
    assert page_name("https://x/", "Dashboard") == "DashboardPage" and page_name("https://x/", None) == "HomePage"
    assert default_application("https://www.saucedemo.com/") == "saucedemo.com"


def test_memory_diff_detects_pages_fields_locators_apis_and_workflows():
    old = {"pages": [
        {"url": "https://a/login", "forms": [{"fields": [{"name": "user", "type": "text", "required": True}]}],
         "elements": [{"selector": "#login", "test_id": "login-button", "role": "button", "name": "Login"}]},
        {"url": "https://a/reports", "forms": [], "elements": []}],
        "endpoints": [{"method": "GET", "endpoint": "https://a/api/old"}],
        "flows": [{"name": "Login → Home", "pages": ["LoginPage", "HomePage"]}]}
    new = {"pages": [
        {"url": "https://a/login", "forms": [{"fields": [{"name": "user", "type": "email", "required": True},
                                                         {"name": "tenant", "type": "text", "required": True}]}],
         "elements": [{"selector": "[data-test=\"login-button\"]", "test_id": "login-button", "role": "button",
                       "name": "Login"}]},
        {"url": "https://a/cart", "forms": [], "elements": []}],
        "endpoints": [{"method": "GET", "endpoint": "https://a/api/new"}],
        "flows": [{"name": "Login → Home", "pages": ["LoginPage", "CartPage"]}, {"name": "Login → Cart", "pages": []}]}
    d = diff_memory(old, new, "DISC-1", 1)
    assert d["pages_added"] == ["https://a/cart"] and d["pages_removed"] == ["https://a/reports"]
    assert d["fields_added"] == ["a/login :: tenant"] and d["fields_changed"] == ["a/login :: user"]
    assert d["locators_changed"] == [{"element": "a/login :: login-button", "old": "#login",
                                      "new": "[data-test=\"login-button\"]"}]
    assert d["endpoints_added"] == ["GET https://a/api/new"] and d["endpoints_removed"] == ["GET https://a/api/old"]
    assert d["workflows_added"] == ["Login → Cart"] and d["workflows_changed"] == ["Login → Home"]
    assert d["total_changes"] == 9 and d["compared_version"] == 1


def test_business_flows_follow_navigation_from_the_login_page():
    pages = [{"url": "https://a/", "name": "LoginPage", "auth": "login"},
             {"url": "https://a/inventory", "name": "InventoryPage", "via": "authentication"},
             {"url": "https://a/cart", "name": "CartPage"}, {"url": "https://a/checkout", "name": "CheckoutPage"}]
    edges = [{"from": "https://a/inventory", "to": "https://a/cart", "via": "Cart"},
             {"from": "https://a/cart", "to": "https://a/checkout", "via": "Checkout"}]
    flows = business_flows(pages, edges)
    assert flows[0]["pages"] == ["LoginPage", "InventoryPage", "CartPage", "CheckoutPage"]
    assert flows[0]["name"] == "Login → Inventory → Cart → Checkout"


# ---- end to end (Playwright, local test shop) ------------------------------------------------------


def test_saved_credentials_get_past_login_and_explore_script_navigation(rt, site):
    rt.automation.creds.save("shop", "standard", "standard_user", "secret_sauce", None)
    result = rt.automation.discovery.crawl("DISC-20261025-000001-AAAAAA", f"{site}/shop/index.html", max_pages=10,
                                           max_depth=3, headless=True, application="shop", credential_role="standard")
    auth = result["authentication"]
    assert auth["status"] == "AUTHENTICATED" and auth["type"] == "form" and auth["inputs_requested"] == []
    assert auth["credential"] == {"application": "shop", "role": "standard", "environment": None}
    # Pages behind the login, reached through script-driven links and buttons (no href).
    assert {"index.html", "inventory.html", "cart.html", "checkout-step-one.html"} <= _paths(result)
    assert any(p.endswith("inventory-item.html?id=4") for p in _paths(result))
    names = {p["name"] for p in result["pages"]}
    assert {"LoginPage", "InventoryPage", "CartPage", "CheckoutStepOnePage"} <= names
    assert result["summary"]["elements"] > 30 and result["summary"]["menus"] >= 1
    assert [e["method"] for e in result["endpoints"]] == ["GET"] and result["endpoints"][0]["endpoint"].endswith("/api/inventory")
    # Menu items revealed by the burger button are inventoried, logout and destructive controls are never clicked.
    inv = next(p for p in result["pages"] if p["name"] == "InventoryPage")
    assert any(e.get("revealed_by") == "Open Menu" for e in inv["elements"])
    skipped = {(s["element"], s["reason"].split(" ")[0]) for s in result["skipped_actions"]}
    assert ("Logout", "logout") in skipped and ("Reset App State", "destructive") in skipped
    assert ("Remove", "destructive") in skipped
    assert any(e.get("destructive") for p in result["pages"] for e in p["elements"])
    assert result["flows"] and result["flows"][0]["pages"][:2] == ["LoginPage", "InventoryPage"]
    assert result["version"] == 1 and result["diff"] is None
    # Update: the second discovery is version 2 of the same application memory and nothing changed.
    again = rt.automation.discovery.crawl("DISC-20261025-000002-BBBBBB", f"{site}/shop/index.html", max_pages=10,
                                          max_depth=3, headless=True, application="shop", credential_role="standard")
    assert again["version"] == 2 and again["diff"]["compared_version"] == 1
    assert again["diff"]["pages_added"] == [] and again["diff"]["pages_removed"] == []
    listed = rt.automation.discovery.list(application="shop")
    assert [d["version"] for d in listed] == [2, 1] and listed[0]["authentication"] == "AUTHENTICATED"
    # Memories of different applications stay apart.
    other = rt.automation.discovery.crawl("DISC-20261025-000003-CCCCCC", f"{site}/index.html", max_pages=3,
                                          max_depth=1, headless=True, application="Brochure")
    assert other["version"] == 1 and other["diff"] is None


def test_missing_credentials_pause_and_proceed_resumes_the_same_session(client, rt, site):
    run_id = _start(client, f"{site}/shop/index.html", application="shop")
    job = _wait(client, run_id, {"WAITING_FOR_INPUT"})
    req = job["request"]
    assert req["kind"] == "credentials" and [f["key"] for f in req["fields"]] == ["username", "password"]
    assert [f["secret"] for f in req["fields"]] == [False, True] and req["static_allowed"] is True
    # Missing values are refused; the agent keeps waiting.
    r = client.post(f"/api/automation/discovery-jobs/{run_id}/input", json={"values": {"username": "mfa_user"}})
    assert r.status_code == 400 and r.json()["error_code"] == "DISCOVERY_INPUT_MISSING"
    r = client.post(f"/api/automation/discovery-jobs/{run_id}/input",
                    json={"values": {"username": "mfa_user", "password": "secret_sauce"}, "save_static": True})
    assert r.status_code == 200
    # MFA: the code is runtime-only and cannot be saved.
    job = _wait(client, run_id, {"WAITING_FOR_INPUT"})
    req = job["request"]
    assert req["kind"] == "otp" and req["page"] == "Two-Factor Authentication"
    assert req["fields"][0]["runtime_only"] is True and req["static_allowed"] is False
    client.post(f"/api/automation/discovery-jobs/{run_id}/input",
                json={"values": {req["fields"][0]["key"]: "123456"}, "save_static": True})
    job = _wait(client, run_id, {"COMPLETED", "FAILED"})
    assert job["state"] == "COMPLETED", job["error"]
    states = [h["state"] for h in job["history"]]
    assert states.index("WAITING_FOR_INPUT") < states.index("RESUMING") < states.index("COMPLETED")
    result = rt.automation.discovery.get(job["discovery_id"])
    auth = result["authentication"]
    assert auth["status"] == "AUTHENTICATED" and auth["type"] == "form+otp"
    assert auth["runtime_inputs"] == ["Authentication Code"]
    # The browser session continued after each pause: pages behind both screens were discovered.
    assert {"otp.html", "inventory.html", "cart.html"} <= _paths(result)
    # Static credentials went to Application Credentials (password in the credential store); the code was never stored.
    saved = rt.automation.creds.list()
    assert [(c["application"], c["role"], c["username"], c["has_password"]) for c in saved] == [("shop", "user", "mfa_user", True)]
    stored = rt.secrets._backend._data  # MemoryBackend
    assert "123456" not in stored.values() and "secret_sauce" in stored.values()
    # Typed values never reach events or the stored result.
    activities = " ".join(e["activity"] or "" for e in rt.db.query("SELECT activity FROM events WHERE run_id = ?", (run_id,)))
    assert "secret_sauce" not in activities and "123456" not in activities
    assert "secret_sauce" not in str(result) and "123456" not in str(result)


def test_rejected_password_asks_again_and_cancel_stops_without_changing_memory(client, rt, site):
    run_id = _start(client, f"{site}/shop/index.html", application="shop")
    _wait(client, run_id, {"WAITING_FOR_INPUT"})
    client.post(f"/api/automation/discovery-jobs/{run_id}/input",
                json={"values": {"username": "standard_user", "password": "wrong"}})
    job = _wait(client, run_id, {"WAITING_FOR_INPUT"})
    # Wait for the second request (the first one was answered).
    deadline = time.monotonic() + 30
    while "rejected" not in (job["request"] or {}).get("page", "") and time.monotonic() < deadline:
        time.sleep(0.2)
        job = client.get(f"/api/automation/discovery-jobs/{run_id}").json()["job"]
    assert "rejected" in job["request"]["page"] and "do not match" in job["request"]["message"]
    assert client.post(f"/api/automation/discovery-jobs/{run_id}/stop").status_code == 200
    job = _wait(client, run_id, {"STOPPED", "FAILED", "COMPLETED"})
    assert job["state"] == "STOPPED" and job["discovery_id"] is None
    assert rt.automation.discovery.list() == []
    assert client.post(f"/api/automation/discovery-jobs/{run_id}/stop").status_code == 409


def test_additional_login_field_is_asked_and_static_value_reused(client, rt, site):
    rt.automation.creds.save("shop", "standard", "standard_user", "secret_sauce", None)
    url = f"{site}/shop/index.html?tenant=1"
    run_id = _start(client, url, application="shop", credential_role="standard")
    job = _wait(client, run_id, {"WAITING_FOR_INPUT"})
    assert [f["label"] for f in job["request"]["fields"]] == ["Tenant"]
    client.post(f"/api/automation/discovery-jobs/{run_id}/input",
                json={"values": {"tenant": "acme"}, "save_static": True})
    job = _wait(client, run_id, {"COMPLETED", "FAILED"})
    assert job["state"] == "COMPLETED"
    assert rt.secrets.get(APP_CREDENTIAL_NAMESPACE, "shop||field|tenant") == "acme"
    # Next discovery: nothing to ask.
    run2 = _start(client, url, application="shop", credential_role="standard", max_pages=3)
    job = _wait(client, run2, {"COMPLETED", "FAILED", "WAITING_FOR_INPUT"})
    assert job["state"] == "COMPLETED"
    assert rt.automation.discovery.get(job["discovery_id"])["authentication"]["status"] == "AUTHENTICATED"


def test_captcha_pauses_for_manual_verification(client, site):
    run_id = _start(client, f"{site}/shop/captcha.html", application="shop")
    job = _wait(client, run_id, {"WAITING_FOR_INPUT", "COMPLETED", "FAILED"})
    assert job["state"] == "WAITING_FOR_INPUT"
    assert job["request"]["kind"] == "captcha" and job["request"]["title"] == "Manual Verification Required"
    assert "Show Browser" in job["request"]["message"]  # headless: it cannot be completed here
    client.post(f"/api/automation/discovery-jobs/{run_id}/stop")
    assert _wait(client, run_id, {"STOPPED"})["state"] == "STOPPED"


def test_non_interactive_discovery_never_hangs_on_a_login_page(rt, site):
    result = rt.automation.discovery.crawl("DISC-20261025-000004-DDDDDD", f"{site}/shop/index.html", max_pages=3,
                                           max_depth=1, headless=True, application="shop")
    assert result["authentication"]["status"] == "INPUT_REQUIRED"
    assert [p["name"] for p in result["pages"]] == ["LoginPage"]


def test_discovery_api_validation(client):
    assert client.get("/api/automation/discovery-jobs/DISC-NOPE").status_code == 404
    r = client.post("/api/automation/discovery-jobs/DISC-NOPE/input", json={"values": {}})
    assert r.status_code == 404
    assert client.get("/api/automation/discovery-applications").json()["items"] == []


@pytest.mark.parametrize("explore", [False])
def test_link_only_discovery_still_works(rt, site, explore):
    result = rt.automation.discovery.crawl("DISC-20261025-000005-EEEEEE", f"{site}/index.html", max_pages=6,
                                           max_depth=2, headless=True, explore=explore)
    assert {"index.html", "login.html", "products.html"} <= _paths(result)
    assert result["application"] == "127.0.0.1:" + site.rsplit(":", 1)[1]
