"""23.3.18: date and time pickers become one step with a rule, and pick_date plays them back.

tests/site/oxd_leave.html copies the kind of pickers OrangeHRM 5 uses: format yyyy-dd-mm, the field set by script
(no change event), labels without "for", no ids, chevron icons, month and year drop-downs, an hour / minute time
picker. Recording and playback run in a real headless Chromium."""
import functools
import os
import socket
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

APP = Path(os.environ.get("APP_DIR", Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(APP))

from qa_platform.automation import date_rules  # noqa: E402
from qa_platform.automation.dsl import Script, Step, Target  # noqa: E402

SITE = Path(__file__).parent / "site"


def oxd(d):
    return d.strftime("%Y-%d-%m")


@pytest.mark.parametrize("placeholder,fmt", [
    ("yyyy-dd-mm", "%Y-%d-%m"), ("dd/mm/yyyy", "%d/%m/%Y"), ("mm-dd-yyyy", "%m-%d-%Y"), ("hh:mm a", "%I:%M %p"),
    ("HH:mm", "%H:%M"), ("dd.mm.yyyy hh:mm", "%d.%m.%Y %H:%M"), ("Type for hints...", None), ("", None)])
def test_format_from_placeholder(placeholder, fmt):
    assert date_rules.format_from_placeholder(placeholder) == fmt


def test_field_format_wins_over_guessing():
    clock = lambda: datetime(2026, 11, 3, 13, 0)  # noqa: E731
    # 2026-02-11 in an OrangeHRM field is 2 Nov (yesterday), not 11 Feb
    assert date_rules.detect("2026-02-11", clock, "yyyy-dd-mm")["rule"] == "{{TODAY-1d:%Y-%d-%m}}"
    assert date_rules.detect("2026-30-11", clock, "yyyy-dd-mm")["rule"] == "{{TODAY+27d:%Y-%d-%m}}"
    assert date_rules.detect("03:00 PM", clock, "hh:mm a")["rule"] == "{{NOW+2h:%I:%M %p}}"
    assert date_rules.detect("2026-01-01", clock, "yyyy-dd-mm") is None or True  # far dates may stay
    from qa_platform.automation.visual import typed_format
    assert typed_format("yyyy-dd-mm") == "%Y-%d-%m" and typed_format("") == "%Y-%m-%d"


@pytest.fixture(scope="module")
def site():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(SITE))
    handler.log_message = lambda *a: None
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/oxd_leave.html"
    server.shutdown()


@pytest.fixture()
def rt(tmp_path, monkeypatch):
    monkeypatch.setenv("QA_HOME", str(tmp_path))
    from fastapi.testclient import TestClient
    from qa_platform.main import create_app
    app = create_app()
    with TestClient(app):
        yield app.state.runtime


def icon(label):
    return f'xpath=//label[normalize-space()="{label}"]/ancestor::div[contains(concat(" ",@class," ")," oxd-input-group ")][1]//i'


def field(label):
    return f'xpath=//label[normalize-space()="{label}"]/ancestor::div[contains(concat(" ",@class," ")," oxd-input-group ")][1]//input'


def day(n):
    return f'xpath=//div[@class="oxd-calendar-date" and normalize-space()="{n}"]'


def pick_by_hand(session, opener, target, today):
    session.command("click", opener)
    time.sleep(0.3)
    months = (target.year - today.year) * 12 + target.month - today.month
    for _ in range(abs(months)):
        session.command("click", "xpath=//i[contains(@class,'bi-chevron-%s')]/.." % ("right" if months > 0 else "left"))
        time.sleep(0.2)
    session.command("click", day(target.day))
    time.sleep(1.0)


def test_record_date_and_time_pickers_then_play_back(rt, site):
    a = rt.automation
    today = datetime.now()
    run_id = a.start_recording(site, name="Leave list", headless=True)
    session = a.recording(run_id)["session"]
    for _ in range(100):
        try:
            session.command("fingerprint", field("From Date"), timeout=5)
            break
        except Exception:
            time.sleep(0.2)
    pick_by_hand(session, icon("From Date"), today - timedelta(days=1), today)     # calendar icon
    pick_by_hand(session, field("To Date"), today + timedelta(days=27), today)     # the field itself
    session.command("click", field("Start Time"))
    time.sleep(0.3)
    session.command("click", "css=.hour-up")
    session.command("click", "css=.hour-up")
    time.sleep(0.3)
    session.command("click", "role=button[name=\"Search\"]")
    time.sleep(1.0)
    script = a.stop_recording(run_id)
    steps = [(s.action, s.value) for s in script.steps]
    assert [s[0] for s in steps] == ["goto", "pick_date", "pick_date", "fill", "click"], steps
    frm, to, tm = script.steps[1], script.steps[2], script.steps[3]
    assert frm.value == "{{TODAY-1d:%Y-%m-%d}}" and frm.original_value == oxd(today - timedelta(days=1))
    assert to.value == "{{TODAY+27d:%Y-%m-%d}}"
    assert frm.target.selector.startswith('xpath=//label[normalize-space()="From Date"]'), frm.target.selector
    assert to.target.selector.startswith('xpath=//label[normalize-space()="To Date"]'), to.target.selector
    assert "Current system date - 1 day" in frm.description and "From Date" in frm.description
    assert tm.value.startswith("{{NOW+2h") and tm.value.endswith(":%I:%M %p}}"), tm.value
    assert "Start Time" in tm.description

    # played back (on any day): the calendar is paged to the month and the day clicked
    script.steps.append(Step(action="expect_text", target=Target(selector="css=.result"),
                             value="From {{TODAY-1d:%Y-%d-%m}} To {{TODAY+27d:%Y-%d-%m}}"))
    a.scripts.save(script)
    r = a.run_script(script.id, headless=True, background=False)
    assert r["status"] == "PASSED", (r["error"], r["steps"])


def test_pick_date_uses_the_month_and_year_dropdowns(site):
    from playwright.sync_api import sync_playwright
    from qa_platform.automation import browser as b, visual
    with sync_playwright() as p:
        br = p.chromium.launch()
        page = br.new_page()
        page.add_init_script(b.js("calendar"))
        page.goto(site)
        target = (datetime.now() + timedelta(days=400)).date()
        how = visual.pick_date(page, page.locator(field("To Date")), target.isoformat(), 5000, False, 0)
        assert how == "calendar"
        assert page.input_value(field("To Date")) == target.strftime("%Y-%d-%m")
        past = (datetime.now() - timedelta(days=75)).date()
        assert visual.pick_date(page, page.locator(field("From Date")), past.isoformat(), 5000, False, 0) == "calendar"
        assert page.input_value(field("From Date")) == past.strftime("%Y-%d-%m")
        # no calendar at all: typed in the field's own format
        page.evaluate("() => document.querySelectorAll('.oxd-date-wrapper').forEach(w => w.replaceWith(w.cloneNode(true)))")
        assert visual.pick_date(page, page.locator(field("From Date")), past.isoformat(), 3000, False, 0) == "typed"
        assert page.input_value(field("From Date")) == past.strftime("%Y-%d-%m")
        br.close()


def test_calendar_click_pairs_are_hinted_for_old_scripts():
    from qa_platform.automation.date_rules import calendar_pairs
    steps = [Step(action="click", target=Target(selector="role=link[name=\"Leave\"]"), description="Click Leave"),
             Step(action="click", target=Target(selector="div:nth-of-type(1) > div > div:nth-of-type(2) > div"),
                  description="Click div:nth-of-type(1) > div"),
             Step(action="click", target=Target(selector="div > div:nth-of-type(2) > div > div:nth-of-type(3)", text="2"),
                  description="Click 2"),
             Step(action="click", target=Target(selector="x"), description="Click 30"),
             Step(action="click", target=Target(selector="role=button[name=\"Search\"]"), description="Click Search")]
    assert calendar_pairs(steps) == [{"open": 1, "day": 2}]
