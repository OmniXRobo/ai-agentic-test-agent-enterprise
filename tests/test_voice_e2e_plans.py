"""End-to-end by voice in the browser: wake, Test Plans, create a plan, Test Suites, select and uncheck suites,
with the assistant available throughout. TFS is simulated (fake_tfs.py); microphone, recognizer and voice are mocked."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect

sys.path.insert(0, str(Path(__file__).parent))
from test_voice_ui import FAKE_MEDIA, Harness, free_port, pw, browser  # noqa: E402,F401

APP = Path(os.environ.get("APP_DIR", Path(__file__).resolve().parents[1] / "app"))


@pytest.fixture(scope="module")
def tfs_server(tmp_path_factory):
    home = tmp_path_factory.mktemp("qahome-e2e")
    port = free_port()
    (home / "settings.json").write_text(json.dumps({"server": {"host": "127.0.0.1", "port": port},
        "voice": {"agent_command_name": "Captain", "reply_style": "plain", "stt_engine": "builtin", "phrase_style": "conversational"}}))
    proc = subprocess.Popen([sys.executable, str(Path(__file__).parent / "fake_tfs_server.py"), str(port)], cwd=APP,
                            env={**os.environ, "QA_HOME": str(home), "APP_DIR": str(APP)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            httpx.get(base + "/api/health", timeout=1); break
        except httpx.HTTPError:
            time.sleep(0.5)
    yield base
    proc.kill(); proc.wait(10)


def test_e2e_wake_plans_create_suites(browser, tfs_server):
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    ctx.add_init_script(FAKE_MEDIA)
    page = ctx.new_page()
    h = Harness(page, tfs_server)
    page.goto(tfs_server + "/#/dashboard")
    h.wait_state("standby")

    h.say("Captain")                                           # 1. wake
    assert "Yes, Boss. I'm listening." in h.spoken()
    h.say("go to test plans")                                  # 2. Test Plans
    page.wait_for_function("location.hash === '#/test-plans'")
    expect(page.locator("tr[data-plan]")).to_have_count(4)

    h.say("Captain, create a new test plan for Sprint 25")     # 3. create
    assert "On it, Boss. I'm preparing the test plan Shop - Sprint 25 - Test Plan." in h.spoken()
    page.wait_for_function("window.__spoken.some(s => s.startsWith('Done, Boss. The test plan Shop - Sprint 25 - Test Plan for Sprint 25 has been created.'))", timeout=10000)
    assert any("What would you like to do next?" in s for s in h.spoken())
    page.wait_for_function("/^#\\/test-plans\\?id=\\d+$/.test(location.hash)", timeout=8000)
    expect(page.locator("tr[data-plan].selected")).to_contain_text("Shop - Sprint 25 - Test Plan")  # the page shows it

    h.say("Captain, now go to test suites")                    # 4. Test Suites for the new plan
    page.wait_for_function("location.hash.startsWith('#/test-suites?plan=')")
    assert "Understood, Boss. I'm opening Test Suites for Shop - Sprint 25 - Test Plan." in h.spoken()
    expect(page.locator("#plan option:checked")).to_contain_text("Shop - Sprint 25 - Test Plan")

    h.say("Captain, select functional and regression, but uncheck performance")  # 5-6. select, deselect, save
    page.wait_for_function("window.__spoken.some(s => s.startsWith('Done, Boss. Your test suite selections have been saved'))", timeout=10000)
    page.wait_for_function("location.hash.includes('saved=1')", timeout=8000)
    expect(page.locator("#suites")).to_contain_text("Functional Testing")
    expect(page.locator("#suites")).to_contain_text("Regression Testing")
    expect(page.locator("#suites")).not_to_contain_text("Performance Testing")
    assert page.is_checked("input[name=type][value=functional]") and page.is_checked("input[name=type][value=regression]")
    assert not page.is_checked("input[name=type][value=performance]")

    h.say("Captain, show me more details")
    page.wait_for_function("location.hash.startsWith('#/test-plans?id=')")
    assert any("It has 2 suites: Functional Testing and Regression Testing." in s for s in h.spoken())
    h.wait_state("standby")                                    # 18. still available
    assert page.evaluate("window.__mic.gum") == 1               # one microphone session for the whole flow
    assert not h.errors, h.errors
    ctx.close()


def test_page_selection_is_the_voice_context(browser, tfs_server):
    ctx = browser.new_context()
    ctx.add_init_script(FAKE_MEDIA)
    page = ctx.new_page()
    h = Harness(page, tfs_server)
    page.goto(tfs_server + "/#/test-plans")
    h.wait_state("standby")
    page.click("tr[data-plan]:has-text('Checkout Regression')")   # chosen with the mouse
    page.wait_for_function("location.hash.startsWith('#/test-plans?id=')")
    h.say("Captain, which suites does it have")
    page.wait_for_function("window.__spoken.some(s => s.includes('Checkout Regression has no suites yet'))", timeout=8000)
    ctx.close()
