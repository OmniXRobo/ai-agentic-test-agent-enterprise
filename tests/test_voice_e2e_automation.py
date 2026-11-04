"""End-to-end by voice in the browser, milestone 6: automation run settings said aloud are the ones the Settings page
shows. TFS is simulated (fake_tfs.py); microphone, recognizer and voice are mocked."""
import sys
from pathlib import Path

from playwright.sync_api import expect

sys.path.insert(0, str(Path(__file__).parent))
from test_voice_ui import FAKE_MEDIA, Harness, pw, browser  # noqa: E402,F401
from test_voice_e2e_plans import tfs_server  # noqa: E402,F401


def test_e2e_run_settings_by_voice(browser, tfs_server):
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    ctx.add_init_script(FAKE_MEDIA)
    page = ctx.new_page()
    h = Harness(page, tfs_server)
    page.goto(tfs_server + "/#/ai-automation")
    h.wait_state("standby")
    h.say("Captain, set timeout to 25 seconds")
    page.wait_for_function("window.__spoken.some(s => s.includes('wait up to 25 seconds for an element'))", timeout=8000)
    h.say("Captain, disable step screenshots")
    page.wait_for_function("window.__spoken.some(s => s.includes('take screenshots only on failure'))", timeout=8000)
    h.say("Captain, what are the automation settings")
    page.wait_for_function("location.hash === '#/settings'", timeout=8000)
    expect(page.locator("input[name=timeout_seconds]")).to_have_value("25")
    assert not page.is_checked("input[name=step_screenshots]")
    assert any("Scripts are Python with Playwright." in s for s in h.spoken())
    h.wait_state("standby")
    assert not h.errors, h.errors
    ctx.close()
