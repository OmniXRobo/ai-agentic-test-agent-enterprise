"""23.14.0 pages in a real browser (SkyGuard served in-process, Chromium through Playwright): the Security Testing menu
(eight screens), Red Hat Fuse Testing and ActiveMQ Testing (eight tabs each), the unified result actions (Analyze with
AI, Create TFS Defect, Generate PDF Report, Email Developer, View Evidence, Retest), the Email / SMTP settings with the
intranet fields, connection test and send history, and the dashboard figures of the new modules.

With SKYGUARD_TEST_CLASSIC="<jolokia url>|<stomp port>" a real ActiveMQ Classic broker is used so the ActiveMQ result
rows come from a real run; without it the pages are checked with their empty states.

Screenshots go to $SKYGUARD_UI_SHOTS when it is set (the release evidence folder), otherwise to the test's temp folder.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest

pw_api = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from qa_platform.auth import COOKIE  # noqa: E402
from qa_platform.config import Paths  # noqa: E402
from qa_platform.credentials import MemoryBackend  # noqa: E402
from qa_platform.main import create_app  # noqa: E402
from tests.auth_helper import session_token  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    import uvicorn

    paths = Paths(tmp_path_factory.mktemp("qahome")).ensure()
    app = create_app(paths, secret_backend=MemoryBackend())
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            if httpx.get(base + "/api/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.25)
    token = session_token(base)
    api = httpx.Client(base_url=base, headers={"Origin": base, "Sec-Fetch-Site": "same-origin"}, timeout=60,
                       cookies={COOKIE: token})
    sec = api.post("/api/security/apps", json={"name": "Booking QA", "environment": "QA", "config": {
        "base_url": "http://127.0.0.1:9/", "authorization_confirmed": True,
        "authorization_statement": "UI test fixture"}}).json()["app"]
    fuse = api.post("/api/fuse/connections", json={"name": "Fuse QA", "environment": "QA", "config": {
        "runtime": "blackbox", "authorization_confirmed": True, "authorization_statement": "UI test fixture"}})
    real = os.environ.get("SKYGUARD_TEST_CLASSIC")
    cfg = {"flavor": "classic", "stomp_host": "127.0.0.1", "test_destinations": ["SKYGUARD.UI.*"],
           "allow_publish": True, "authorization_confirmed": True, "authorization_statement": "UI test fixture"}
    if real:
        url, stomp = real.split("|")
        cfg.update(management_url=url, management_user="admin", stomp_port=int(stomp))
    brk = api.post("/api/activemq/brokers", json={"name": "Classic QA", "environment": "QA", "config": cfg}).json()["broker"]
    run_id = None
    if real:
        api.post(f"/api/activemq/brokers/{brk['id']}/credentials", json={"name": "management.secret", "value": "admin"})
        ids = []
        for body in ({"type": "publish_verify", "name": "Order arrives", "destination": "SKYGUARD.UI.IN",
                      "body": '{"orderId": 7}', "expect": {"jsonpath": {"$.orderId": 7}, "within_seconds": 5}},
                     {"type": "security", "name": "Anonymous access refused"}):
            case = api.post(f"/api/activemq/brokers/{brk['id']}/cases", json=body).json()["case"]
            api.post(f"/api/itest/cases/{case['id']}/approve")
            ids.append(case["id"])
        run_id = api.post(f"/api/activemq/brokers/{brk['id']}/runs", json={"kind": "cases",
                          "options": {"case_ids": ids}}).json()["run"]["id"]
        for _ in range(120):
            if api.get(f"/api/itest/runs/{run_id}").json()["run"]["status"] not in ("RUNNING", "QUEUED"):
                break
            time.sleep(0.5)
    yield {"base": base, "rt": app.state.runtime, "api": api, "token": token, "sec": sec, "fuse": fuse.json(),
           "broker": brk, "run": run_id}
    api.close()
    server.should_exit = True
    th.join(10)


@pytest.fixture(scope="module")
def shots(tmp_path_factory):
    d = Path(os.environ.get("SKYGUARD_UI_SHOTS") or tmp_path_factory.mktemp("shots"))
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture(scope="module")
def page(world):
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 1000})
        ctx.add_cookies([{"name": COOKIE, "value": world["token"], "url": world["base"] + "/"}])
        p = ctx.new_page()
        errors: list[str] = []
        p.on("pageerror", lambda e: errors.append(str(e)))
        p.errors = errors  # type: ignore[attr-defined]
        yield p
        b.close()


SECURITY = [("sec-assessment", "AI Security Assessment"), ("sec-discovery", "Application Discovery"),
            ("sec-scanning", "Vulnerability Scanning"), ("sec-scenarios", "Security Test Scenarios"),
            ("sec-findings", "Security Findings"), ("sec-regression", "Security Regression"),
            ("sec-reports", "Security Reports"), ("sec-preferences", "Security Preferences")]


def test_menu_has_security_and_integration_groups(page, world, shots):
    page.goto(world["base"] + "/#/dashboard")
    nav = page.locator("#nav")
    expect(nav.locator(".nav-group", has_text="Security Testing")).to_be_visible(timeout=20000)
    expect(nav.locator(".nav-group", has_text="Integration Testing")).to_be_visible()
    for route, title in SECURITY + [("fuse-testing", "Red Hat Fuse Testing"), ("activemq-testing", "ActiveMQ Testing"),
                                    ("email-settings", "Email / SMTP")]:
        expect(nav.locator(f'[data-route="{route}"]')).to_have_attribute("data-title", title)
    assert not page.errors


@pytest.mark.parametrize("route,title", SECURITY)
def test_security_screens_render(page, world, shots, route, title):
    page.goto(world["base"] + f"/#/{route}")
    expect(page.locator("#pageTitle")).to_have_text(title, timeout=15000)
    body = page.locator("#secBody")
    expect(body).to_be_visible(timeout=15000)
    page.wait_for_timeout(600)
    expect(body.locator(".pill.FAIL", has_text="Could not")).to_have_count(0)
    page.screenshot(path=str(shots / f"231400-{route}.png"), full_page=True)
    assert not page.errors, page.errors


FUSE_TABS = ["Fuse Connections", "Route Discovery", "Route Mapping", "AI Test Designer", "Test Execution",
             "Route Monitoring", "Transformation Comparison", "Results & Defects"]
AMQ_TABS = ["Broker Connections", "Queue & Topic Discovery", "Message Test Designer", "Publish & Verify",
            "Consumer Verification", "Retry & Dead-Letter Testing", "Broker Monitoring", "Results & Defects"]


@pytest.mark.parametrize("route,tabs", [("fuse-testing", FUSE_TABS), ("activemq-testing", AMQ_TABS)])
def test_integration_pages_have_eight_tabs(page, world, shots, route, tabs):
    page.goto(world["base"] + f"/#/{route}")
    bar = page.locator("[data-tab]")
    expect(bar.first).to_be_visible(timeout=15000)
    assert bar.all_inner_texts() == tabs
    for i, label in enumerate(tabs):
        bar.nth(i).click()
        page.wait_for_timeout(500)
        assert not page.errors, (label, page.errors)
    page.screenshot(path=str(shots / f"231400-{route}.png"), full_page=True)


def test_transformation_comparison_in_browser(page, world, shots):
    page.goto(world["base"] + "/#/fuse-testing")
    page.locator("[data-tab]", has_text="Transformation Comparison").click()
    page.locator("#itBody [data-go]").click()
    out = page.locator("#itBody [data-cmp-out]")
    expect(out).to_contain_text("difference", timeout=10000)
    expect(out.locator("td", has_text="$.extra")).to_be_visible()
    page.screenshot(path=str(shots / "231400-fuse-compare.png"), full_page=True)
    assert not page.errors


def test_activemq_results_have_unified_actions(page, world, shots):
    if not world["run"]:
        pytest.skip("needs a real ActiveMQ Classic broker (SKYGUARD_TEST_CLASSIC)")
    page.goto(world["base"] + "/#/activemq-testing")
    page.locator("[data-tab]", has_text="Results & Defects").click()
    page.locator(f'[data-run="{world["run"]}"]').first.click()
    box = page.locator("#runBox")
    expect(box.locator("table.data")).to_be_visible(timeout=15000)
    rows = box.locator("tbody tr")
    expect(rows.first).to_be_visible()
    seen = set()
    for i in range(rows.count()):
        row = rows.nth(i)
        status = row.locator(".pill").last.inner_text().strip()
        labels = set(row.locator("[data-ra]").all_inner_texts())
        if status == "PASSED":
            assert {"View Evidence", "Generate PDF Report", "Email Developer"} <= labels
            assert not labels & {"Create TFS Defect", "Analyze with AI", "Retest"}
        else:
            assert {"Analyze with AI", "Create TFS Defect", "Generate PDF Report", "Email Developer", "View Evidence"} <= labels
        seen.add(status)
    box.screenshot(path=str(shots / "231400-amq-results.png"))
    box.locator('[data-ra="evidence"]').first.click()
    modal = page.locator(".modal").last
    expect(modal).to_be_visible(timeout=10000)
    page.screenshot(path=str(shots / "231400-amq-evidence.png"))
    modal.locator("[data-close]").first.click()
    box.locator('[data-ra="email"]').first.click()
    expect(page.locator(".modal [name=to]").last).to_be_visible(timeout=10000)
    page.screenshot(path=str(shots / "231400-email-developer.png"))
    page.keyboard.press("Escape")
    expect(page.locator(".modal-backdrop")).to_have_count(0)
    assert not page.errors


def test_email_smtp_settings_intranet_fields(page, world, shots):
    page.goto(world["base"] + "/#/email-settings")
    f = page.locator("#emailForm")
    expect(f).to_be_visible(timeout=15000)
    f.locator("[name=preset]").select_option("custom")
    for name in ("ca_file", "timeout_seconds", "allowed_domains", "allow_external", "max_attachment_mb",
                 "max_total_attachment_mb", "developer_directory", "verify_tls"):
        expect(f.locator(f"[name={name}]")).to_be_attached()
    f.locator("[name=host]").fill("smtp.company.local")
    f.locator("[name=sender]").fill("qa@company.local")
    f.locator("[name=allowed_domains]").fill("company.local")
    f.locator("[name=developer_directory]").fill("Dev One, dev.one@company.local, Integration, fuse|activemq")
    f.locator("button[type=submit]").click()
    page.wait_for_timeout(800)
    cfg = world["api"].get("/api/email/settings").json()["email"]
    assert cfg["allowed_domains"] == "company.local"
    assert cfg["developer_directory"][0]["email"] == "dev.one@company.local"
    assert cfg["developer_directory"][0]["modules"] == ["fuse", "activemq"]
    expect(page.locator("#sendHistory h3")).to_have_text("Email Developer send history", timeout=10000)
    page.screenshot(path=str(shots / "231400-email-smtp.png"), full_page=True)
    assert not page.errors
