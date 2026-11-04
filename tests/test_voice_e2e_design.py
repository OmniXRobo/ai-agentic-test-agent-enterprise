"""End-to-end by voice in the browser, milestone 5: generate test cases for the selected suites, review them on
Test Cases, save them, then generate test data. TFS and the AI design engines are simulated (fake_tfs.py)."""
import sys
from pathlib import Path

from playwright.sync_api import expect

sys.path.insert(0, str(Path(__file__).parent))
from test_voice_ui import FAKE_MEDIA, Harness, pw, browser  # noqa: E402,F401
from test_voice_e2e_plans import tfs_server  # noqa: E402,F401


def done(page, prefix, timeout=10000):
    page.wait_for_function(f"window.__spoken.some(s => s.startsWith({prefix!r}))", timeout=timeout)


def test_e2e_cases_then_data(browser, tfs_server):
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    ctx.add_init_script(FAKE_MEDIA)
    page = ctx.new_page()
    h = Harness(page, tfs_server)
    page.goto(tfs_server + "/#/dashboard")
    h.wait_state("standby")

    h.say("Captain, create a new test plan for Sprint 25")
    done(page, "Done, Boss. The test plan Shop - Sprint 25 - Test Plan")
    h.say("Captain, go to test suites")
    page.wait_for_function("location.hash.startsWith('#/test-suites?plan=')")
    h.say("Captain, select functional and regression")
    done(page, "Done, Boss. Your test suite selections have been saved")

    h.say("Captain, now generate test cases from the acceptance criteria")
    page.wait_for_function("location.hash.startsWith('#/test-cases?plan=') && location.hash.includes('&runs=')", timeout=8000)
    done(page, "Done, Boss. 8 test cases generated from the acceptance criteria of 2 user stories in Sprint 25")
    expect(page.locator("#view")).to_contain_text("Functional Testing case 1 for US9001")  # shown for review

    h.say("Captain, save the test cases")
    page.wait_for_function("window.__spoken.some(s => s.startsWith('Please confirm, Boss.'))", timeout=8000)
    h.say("confirm")
    done(page, "Done, Boss. 4 test cases created in TFS and 2 existing ones linked instead of duplicated.")

    h.say("Captain, generate test data for these test cases")
    page.wait_for_function("location.hash.startsWith('#/test-data?tc=')", timeout=8000)
    done(page, "Done, Boss. ")
    assert any("rows of test data generated for test cases" in s for s in h.spoken())
    expect(page.locator(".voice-note")).to_contain_text("Showing test cases #77")
    h.wait_state("standby")
    assert page.evaluate("window.__mic.gum") == 1
    assert not h.errors, h.errors
    ctx.close()
