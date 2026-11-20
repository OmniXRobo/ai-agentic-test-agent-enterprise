"""23.13.0 pages in a real browser (SkyGuard served in-process, Chromium through Playwright acting as the person):
the Dashboard (charts first, the AI analysis at the bottom, clickable slices and days), the module report bar on the
testing pages, the Users form (email, TFS project and personal PAT are required; HTTPS card) and the Guided Browser
editor tabs.

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

    from qa_platform.automation.dsl import Script, Step, Target, new_script_id

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
    rt = app.state.runtime
    script = rt.automation.scripts.save(Script(
        id=new_script_id(), name="Book a flight", mode="guided", base_url="https://booking.example.ae",
        steps=[Step(action="goto", value="https://booking.example.ae/"),
               Step(action="click", target=Target(selector="#search", text="Search", tag="button"))]))
    yield {"base": base, "rt": rt, "script": script.id}
    server.should_exit = True
    th.join(10)


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
        p.errors = errors  # type: ignore[attr-defined]
        yield p
        b.close()


def test_dashboard_charts_first_ai_last(page, world, shots):
    page.goto(world["base"] + "/#/dashboard")
    expect(page.locator(".dash-charts")).to_be_visible(timeout=20000)
    charts_y = page.locator(".dash-charts").bounding_box()["y"]
    ai_y = page.locator(".ai-dash").bounding_box()["y"]
    kpi_y = page.locator(".grid.kpis").first.bounding_box()["y"]
    assert charts_y < kpi_y < ai_y
    assert page.locator(".dash-charts .card").count() >= 10
    page.screenshot(path=str(shots / "231300-dashboard.png"), full_page=True)
    assert not page.errors


def test_module_bar_on_testing_pages_only(page, world, shots):
    for route in ("api-testing", "analytics", "perf-load", "guided-automation", "integration-comparison"):
        page.goto(world["base"] + f"/#/{route}")
        bar = page.locator("#moduleBar")
        expect(bar).to_be_visible(timeout=15000)
        for ma in ("ai", "defect", "email"):
            expect(bar.locator(f"[data-ma={ma}]")).to_be_visible()
    page.goto(world["base"] + "/#/api-testing")
    page.locator("#moduleBar [data-ma=defect]").click()
    form = page.locator("#defForm")
    expect(form.locator("[name=assigned_to]")).to_be_visible()
    expect(form.locator("[name=send_now]")).to_be_attached()
    page.screenshot(path=str(shots / "231300-module-defect.png"))
    form.locator("[data-close]").click()
    page.goto(world["base"] + "/#/users")
    expect(page.locator("#moduleBar")).to_be_hidden(timeout=15000)
    assert not page.errors


def test_users_form_requires_email_project_and_pat(page, world, shots):
    page.goto(world["base"] + "/#/users")
    f = page.locator("#newUser")
    expect(f).to_be_visible(timeout=15000)
    for name in ("email", "tfs_project", "tfs_pat"):
        assert f.locator(f"[name={name}]").get_attribute("required") is not None
    expect(page.locator("#httpsForm")).to_be_visible(timeout=15000)
    page.screenshot(path=str(shots / "231300-users.png"), full_page=True)
    assert not page.errors


def test_guided_editor_tabs(page, world, shots):
    page.goto(world["base"] + f"/#/guided-automation?script={world['script']}")
    tabs = page.locator("#scriptTabs")
    expect(tabs).to_be_visible(timeout=20000)
    labels = tabs.locator("[data-tab]").all_inner_texts()
    for want in ("All", "View Script", "Edit Script", "Flow Logic", "Test Data", "Credentials", "Components",
                 "History", "Result", "Evidence", "Logs"):
        assert want in labels
    tabs.locator("[data-tab=credentials]").click()
    expect(page.locator("[data-sec=credentials]")).to_be_visible()
    expect(page.locator("#pyCard")).to_be_hidden()
    tabs.locator("[data-tab=all]").click()
    expect(page.locator("#pyCard")).to_be_visible()
    hist = page.locator("h3", has_text="Version history")
    py_y = page.locator("#pyCard").bounding_box()["y"]
    assert hist.bounding_box()["y"] > py_y  # version history is at the bottom
    page.screenshot(path=str(shots / "231300-guided-tabs.png"), full_page=True)
    assert not page.errors
