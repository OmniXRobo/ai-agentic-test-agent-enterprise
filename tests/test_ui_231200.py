"""23.12.0 pages in a real browser (SkyGuard served in-process, Chromium through Playwright acting as the person):
Automation Studio > API Testing against the local Orders API (create the application, store its password, Test
Connection, import the OpenAPI document, run, open a result in the response viewer, generate the Python script and
use full screen), Scheduled Runs listing API Testing with its test type and execution modes, and the Agent Workflow
page with the new agents (same layout).

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
from tests import api_site  # noqa: E402
from tests.auth_helper import session_token  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    import uvicorn

    srv, url, st = api_site.start()
    st.auth = "basic"
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
    yield {"base": base, "api": url, "state": st, "rt": app.state.runtime}
    server.should_exit = True
    th.join(10)
    srv.shutdown()


@pytest.fixture(scope="module")
def shots(tmp_path_factory):
    d = Path(os.environ.get("SKYGUARD_UI_SHOTS") or tmp_path_factory.mktemp("shots"))
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture(scope="module")
def page(world):
    token = session_token(world["base"])
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 1000})
        ctx.add_cookies([{"name": COOKIE, "value": token, "url": world["base"] + "/"}])
        p = ctx.new_page()
        errors: list[str] = []
        p.on("pageerror", lambda e: errors.append(str(e)))
        p.on("dialog", lambda d: d.accept())  # the "tests that change data" confirmation: the person says OK
        p.errors = errors  # type: ignore[attr-defined]
        yield p
        b.close()


def test_api_testing_page_end_to_end(page, world, shots):
    page.goto(world["base"] + "/#/api-testing")
    expect(page.locator(".nav-link[data-route='api-testing']")).to_be_visible()
    page.click("#apNew")
    f = page.locator("#apForm")
    f.locator("[name=name]").fill("Orders API")
    f.locator("[name=base_url]").fill(world["api"])
    f.locator("[name=auth_type]").select_option("basic")
    f.locator("[name=auth_username]").fill(api_site.BASIC[0])
    f.locator("[name=authorized]").check()
    page.screenshot(path=str(shots / "231200-api-new-application.png"))
    f.locator("button[type=submit]").click()
    expect(page.locator("[data-sec=password]")).to_be_visible(timeout=15000)

    page.fill("[data-sec=password]", api_site.BASIC[1])
    page.click("[data-savesec=password]")
    expect(page.locator("label[data-secret=password] .pill.PASS")).to_have_text("stored", timeout=15000)
    assert api_site.BASIC[1] not in page.content()

    page.click("#apConn")
    out = page.locator("#apConnOut")
    expect(out).to_contain_text("Reachable", timeout=20000)
    expect(out).to_contain_text("TCP connection")
    page.screenshot(path=str(shots / "231200-api-test-connection.png"), full_page=True)

    page.click("[data-tab=import]")
    page.fill("#spUrl", world["api"] + "/openapi.json")
    page.click("#spUrlBtn")
    expect(page.locator("#tList table")).to_be_visible(timeout=20000)
    rows = page.locator("#tList tbody tr")
    assert rows.count() >= 10
    page.screenshot(path=str(shots / "231200-api-generated-tests.png"), full_page=True)

    page.click("#tRunAll")  # the dialog asking about tests that change data is accepted by the fixture
    detail = page.locator("#runDetail")
    expect(detail.locator(".pill").first).not_to_have_text("RUNNING", timeout=60000)
    expect(detail).to_contain_text("passed", timeout=60000)
    expect(detail.locator(".pill.PASS").first).to_be_visible()
    page.screenshot(path=str(shots / "231200-api-run-results.png"), full_page=True)

    detail.locator("[data-res]").first.click()
    modal = page.locator(".modal")
    expect(modal).to_contain_text("Assertions")
    expect(modal.locator(".jtree").first).to_be_visible()
    expect(modal).to_contain_text("[redacted]")  # the Authorization header
    assert api_site.BASIC[1] not in modal.inner_text()
    page.screenshot(path=str(shots / "231200-api-response-viewer.png"))
    modal.locator("[data-close]").click()

    page.click("[data-tab=script]")
    page.locator("#scCard [data-s=generate]").click()
    expect(page.locator("#scCard .pyed-hl")).to_contain_text("import httpx", timeout=15000)
    for tab in ("Flow Logic", "Credentials", "Components", "History"):
        page.locator(f"#scCard [data-st='{tab}']").click()
        expect(page.locator("#scBody")).not_to_be_empty()
    page.locator("#scCard [data-st='Edit Script']").click()
    expect(page.locator("#scSrc")).to_be_visible()
    page.locator("#scCard [data-full]").first.click()
    assert "pyed-full" in (page.locator("#scCard").get_attribute("class") or "")
    page.screenshot(path=str(shots / "231200-api-script-fullscreen.png"))
    page.keyboard.press("Escape")
    assert "pyed-full" not in (page.locator("#scCard").get_attribute("class") or "")
    assert not page.errors, page.errors  # type: ignore[attr-defined]


def test_scheduled_runs_lists_api_testing_with_modes(page, world, shots):
    page.goto(world["base"] + "/#/scheduled-runs")
    page.select_option("#fPage", "API Testing")
    expect(page.locator("#scriptList")).to_contain_text("Orders API: all API tests", timeout=15000)
    page.locator("#scriptList [data-sel]").first.check()
    page.click("#schBtn")
    form = page.locator("#schForm")
    expect(form).to_contain_text("run without a browser")
    page.screenshot(path=str(shots / "231200-scheduled-runs-api.png"))
    page.locator(".modal [data-close]").click()
    page.select_option("#fPage", "")
    assert not page.errors, page.errors  # type: ignore[attr-defined]


def test_agent_workflow_shows_new_agents_in_the_same_layout(page, world, shots):
    page.goto(world["base"] + "/#/workflow")
    for name in ("API Testing", "API Failure Analysis", "API Contract", "Integration Comparison", "Scheduled Execution"):
        expect(page.locator("body")).to_contain_text(name, timeout=15000)
    page.screenshot(path=str(shots / "231200-agent-workflow.png"), full_page=True)
    assert not page.errors, page.errors  # type: ignore[attr-defined]
