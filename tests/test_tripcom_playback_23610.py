"""23.6.1: Guided Browser Automation on Trip.com-style pages.

The tester's Trip.com recording failed at the city suggestion ("New Delhi - All airports India", selector
[data-testid="0"]), and the date cells and the passenger + / - buttons did not play back either. trip.com cannot be
reached from the build machine, so tests/site/tripcom is a stand-in built from Trip.com's real markup (generated class
names, index-numbered data-testid values, its two calendars, icon-only steppers). Each test records with real key
presses and clicks in headless Chromium, then plays the recording back on a fresh page."""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

APP = Path(os.environ.get("APP_DIR", Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(APP))

from qa_platform.automation import semantic  # noqa: E402
from qa_platform.automation.dsl import Script, Step, Target  # noqa: E402

MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _session(a, url):
    run_id = a.start_recording(url, name="Trip", headless=True)
    s = a.recording(run_id)["session"]
    for _ in range(100):
        try:
            s.command("fingerprint", "body", timeout=5)
            break
        except Exception:
            time.sleep(0.2)
    return run_id, s


def _pause(sec: float = 0.6) -> None:
    time.sleep(sec)


def _play(a, script_id, **kw):
    out = {}
    t = threading.Thread(target=lambda: out.update(rec=a.run_script(script_id, background=False, **kw)))
    t.start()
    t.join(400)
    return out["rec"]


def _flight_cell(d: date) -> str:
    return f'div[role=checkbox][aria-label^="{DAYS[d.weekday()]}, {MONTHS[d.month - 1]} {d.day}, {d.year},"]'


def _steps(rec):
    return [(s["index"], s["action"], s["status"], s.get("error"), s.get("chosen"), s.get("note"), s.get("duration_ms")) for s in rec["steps"]]


@pytest.fixture(scope="module")
def _chromium():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture()
def browser_page(_chromium):
    from qa_platform.automation.browser import js

    ctx = _chromium.new_context()
    ctx.add_init_script(js("components"))
    pg = ctx.new_page()
    yield pg
    ctx.close()


@pytest.fixture()
def dates():
    t = date.today()
    return t + timedelta(days=20), t + timedelta(days=26)


def record_flight_search(a, site, dep, ret):
    run_id, s = _session(a, f"{site}/tripcom/index.html")
    s.command("click", 'role=tab[name="Flights"]'); _pause()
    s.command("type", ('[data-testid="search_city_to0"]', "New Delhi")); _pause(1.2)
    s.command("click", '[data-testid="search_result_box"] [data-testid="0"]'); _pause()
    for d in (dep, ret):
        months_ahead = (d.year - date.today().year) * 12 + d.month - date.today().month
        for _ in range(max(0, months_ahead - 1)):
            s.command("click", 'role=button[name="Next month"]'); _pause(0.3)
        s.command("click", _flight_cell(d)); _pause()
    s.command("click", '[data-testid="flt_passenger_class"]'); _pause()
    s.command("click", ".Pz1oQw .LmN0rT:nth-child(2) .fbtn >> nth=1"); _pause()   # Children +
    s.command("click", ".Pz1oQw .LmN0rT:nth-child(1) .fbtn >> nth=1"); _pause()   # Adults +
    s.command("select", (".Pz1oQw select", "Business")); _pause()
    s.command("click", 'text="Done"'); _pause()
    s.command("click", '[data-testid="search_btn"]'); _pause(1.0)
    return a.stop_recording(run_id)


def test_record_and_play_trip_flight_search(rt, site, dates):
    a = rt.automation
    dep, ret = dates
    script = record_flight_search(a, site, dep, ret)
    script.steps.append(Step(action="expect_text", target=Target(selector="h2.results"),
                             value=f"Flights Dubai to New Delhi · {dep.isoformat()} to {ret.isoformat()} · "
                                   "2 adult, 1 child, 0 infant · Business"))
    a.scripts.save(script)
    print([(st.action, st.target.selector if st.target else None, st.value, st.semantic) for st in script.steps])
    rec = _play(a, script.id, headless=True)
    assert rec["status"] == "PASSED", (rec["error"], _steps(rec))


def _old_label(d: date, end: bool = False) -> str:
    """How Trip.com names a flight calendar day, with the price of the day the recording was made."""
    return (f"{DAYS[d.weekday()]}, {MONTHS[d.month - 1]} {d.day}, {d.year}, "
            + ("Select the end date you want" if end else "Select the start date you want") + ", The price is AED 1.65k")


def old_trip_recording(site: str, dep: date, ret: date) -> Script:
    """The tester's recording as 23.6.0 stored it: the suggestion click (selector [data-testid="0"]) comes before
    the typed text, plus a hover on the list and calendar days named with a price that has changed since."""
    T = Target
    steps = [
        Step(action="goto", value=f"{site}/tripcom/index.html"),
        Step(action="click", target=T(selector='role=tab[name="Flights"]', role="tab", name="Flights")),
        Step(action="hover", target=T(selector="#m-flight-poi-list > li")),
        Step(action="click", target=T(selector='[data-testid="0"]', test_id="0", tag="div",
                                      text="New Delhi - All airports India",
                                      description="New Delhi - All airports India")),
        Step(action="fill", target=T(selector='[data-testid="search_city_to0"]', tag="input", input_type="text",
                                     placeholder="Going to", name="Arrival airport or city"), value="New Delhi"),
        Step(action="click", target=T(selector=f'div[aria-label="{_old_label(dep)}"]')),
        Step(action="click", target=T(selector=f'div[aria-label="{_old_label(ret, end=True)}"]')),
        Step(action="click", target=T(selector="#paxText", text="1 Adult, Economy")),
        Step(action="click", target=T(selector="#portal > div > div:nth-of-type(2) > div:nth-of-type(2) > button:nth-of-type(2)")),
        Step(action="click", target=T(selector='role=button[name="Done"]')),
        Step(action="click", target=T(selector='[data-testid="search_btn"]', text="Search")),
        Step(action="expect_text", target=T(selector="h2.results"),
             value=f"Flights Dubai to New Delhi · {dep.isoformat()} to {ret.isoformat()} · 1 adult, 1 child, 0 infant"),
    ]
    return Script(id="SCR-TRIPOLD001", name="booking", mode="guided", steps=steps, browser="chromium",
                  base_url="https://trip.com")


def test_suggestion_context_of_an_older_recording(site, dates):
    script = old_trip_recording(site, *dates)
    ctx = semantic.suggestion_context(script, 3)
    assert ctx and ctx["typed"] == "New Delhi" and ctx["option"] == "New Delhi - All airports India" and ctx["after"]
    assert semantic.suggestion_context(script, 1) is None      # the Flights tab is not a suggestion
    assert semantic.suggestion_context(script, 10) is None     # nor is Search


def test_older_trip_recording_plays_back(rt, site, dates):
    """The failing run from the tester's screenshot (step 9, [data-testid="0"], best self-heal score 0.39)."""
    a = rt.automation
    a.scripts.save(old_trip_recording(site, *dates))
    rec = _play(a, "SCR-TRIPOLD001", headless=True)
    assert rec["status"] == "PASSED", (rec["error"], _steps(rec))
    by = {s["index"]: s for s in rec["steps"]}
    assert by[3]["chosen"] == "New Delhi - All airports India"
    assert "entered by the suggestion" in by[4]["note"]
    assert by[5]["semantic"]["date"] == dates[0].isoformat()


def _hotel_day(d: date, panel: int) -> str:
    return f'.c-calendar-month >> nth={panel} >> li[role=button]:has(span.day:text-is("{d.day}"))'


def test_record_and_play_trip_hotel_search(rt, site):
    """Hotels: the destination suggestion, the c-calendar (number-only days, icon-only arrows, greyed past days),
    the rooms / guests counters made of plain spans, and the child-age list that adding a child opens."""
    a = rt.automation
    t = date.today()
    first = date(t.year + (t.month // 12), t.month % 12 + 1, 1)       # next month: one press of the arrow
    start, end = first + timedelta(days=9), first + timedelta(days=12)
    run_id, s = _session(a, f"{site}/tripcom/index.html")
    s.command("type", ("#destinationInput", "Pari")); _pause(1.2)
    s.command("click", '.Dx7hN .r0w:has-text("Paris, Texas")'); _pause()
    s.command("click", "span.c-calendar-icon-next"); _pause(0.4)
    s.command("click", _hotel_day(start, 0)); _pause(0.4)
    s.command("click", _hotel_day(end, 0)); _pause(0.4)
    s.command("click", "#guestsBox"); _pause()
    s.command("click", ".Pz1oQw .LmN0rT:nth-child(3) .Hs5Ty >> nth=1"); _pause()   # Children +
    s.command("click", ".Pz1oQw .LmN0rT:nth-child(1) .Hs5Ty >> nth=1"); _pause()   # Rooms +
    s.command("click", ".Ag9Ks"); _pause()
    s.command("click", '.ag-o:text-is("5 years old")'); _pause()
    s.command("click", 'text="Done"'); _pause()
    s.command("click", "#hotelSearch"); _pause(1.0)
    script = a.stop_recording(run_id)
    got = [(st.action, (st.semantic or {}).get("intent"), st.value) for st in script.steps]
    print(got)
    assert ("choose_option", "choose_option", "Paris, Texas United States") in got
    assert sum(1 for g in got if g[1] == "set_count") == 2
    assert any(g[0] == "choose_option" and g[2] == "5 years old" for g in got)
    script.steps.append(Step(action="expect_text", target=Target(selector="h2.results"),
                             value=f"Hotels in Paris, Texas · {start.isoformat()} to {end.isoformat()} · 2 rooms · "
                                   "2 adults · 1 children (5)"))
    a.scripts.save(script)
    rec = _play(a, script.id, headless=True)
    print(_steps(rec))
    assert rec["status"] == "PASSED", (rec["error"], _steps(rec))


def test_counters_are_set_by_label_not_position(browser_page, site):
    from qa_platform.automation import components
    pg = browser_page
    pg.goto(f"{site}/tripcom/index.html")
    pg.click('role=tab[name="Flights"]')
    pg.click('[data-testid="flt_passenger_class"]')
    labels = [c["label"] for c in pg.evaluate("window.__sgComp.counters()")]
    assert labels == ["Adults", "Children", "Infants"]
    assert components.set_count(pg, "Children", 3)["presses"] == 3
    assert components.set_count(pg, "Children", 1)["presses"] == 2       # from whatever it shows now
    assert components.set_count(pg, "Adults", 1)["presses"] == 0
    with pytest.raises(components.ComponentError) as err:
        components.set_count(pg, "Infants", 7)                               # the + is disabled at 4
    assert err.value.code == "COUNT_LIMIT"
    with pytest.raises(components.ComponentError) as err:
        components.set_count(pg, "Pets", 1)
    assert err.value.code == "COUNTER_NOT_FOUND" and "Children (2-11 years)" in err.value.choices
    assert pg.evaluate("window.__trip.fChildren") == 1


def test_suggestion_rows_are_not_page_content(browser_page, site):
    """Rows are read from the pop-up drawn next to the field, never from page content below it, and the popular
    cities shown on focus (index-numbered like the results) are not taken for the typed results."""
    from qa_platform.automation import components
    pg = browser_page
    pg.goto(f"{site}/tripcom/index.html")
    pg.click('role=tab[name="Flights"]')
    field = pg.locator('[data-testid="search_city_to0"]')
    assert pg.evaluate("f => window.__sgComp.findOption('New Delhi - All airports India', f)",
                       field.element_handle())["status"] == "none"
    res = components.choose_option(pg, field, "Indira Gandhi International Airport DEL", typed="Delhi")
    assert res["verified"] and field.input_value() == "New Delhi (DEL)"


def test_hotel_calendar_skips_greyed_days_and_uses_the_arrow(browser_page, site):
    from qa_platform.automation import components
    pg = browser_page
    pg.goto(f"{site}/tripcom/index.html")
    t = date.today()
    if t.day > 1:
        with pytest.raises(components.ComponentError) as err:
            components.pick_date(pg, (t - timedelta(days=1)).isoformat(), role="start")
        assert err.value.code in ("DATE_DISABLED", "DATE_IN_PAST")
    far = t + timedelta(days=95)
    assert components.pick_date(pg, far.isoformat(), role="start", today=t)["verified"]
    assert pg.evaluate("window.__trip.in && window.__trip.in.toDateString()") == \
        pg.evaluate(f"new Date({far.year}, {far.month - 1}, {far.day}).toDateString()")


def test_semantic_upgrade_rewrites_an_older_suggestion_click(site, dates):
    script = old_trip_recording(site, *dates)
    new, changes = semantic.upgrade(script, today=date.today())
    acts = [(s.action, s.value) for s in new.steps[:5]]
    assert acts[3] == ("fill", "New Delhi") and acts[4] == ("choose_option", "New Delhi - All airports India"), acts
    assert new.steps[4].semantic["typed"] == "New Delhi"
    assert new.steps[4].target.selector == '[data-testid="search_city_to0"]'
    assert any(c["change"] == "suggestion click -> choose_option" for c in changes)
    assert script.steps[3].action == "click"   # the original is untouched (a new version is saved on apply)


def test_upgraded_recording_plays_back(rt, site, dates):
    a = rt.automation
    new, _ = semantic.upgrade(old_trip_recording(site, *dates), today=date.today())
    a.scripts.save(new)
    rec = _play(a, new.id, headless=True)
    assert rec["status"] == "PASSED", (rec["error"], _steps(rec))


def test_recording_without_the_typed_text_plays_back(rt, site, dates):
    """A recording where the typing was never captured: click the arrival box, then the suggestion. The suggestion's
    leading words are typed into the box's field and the row is chosen by its text."""
    a = rt.automation
    script = old_trip_recording(site, *dates)
    script.steps[4] = script.steps[3]
    script.steps[3] = Step(action="click", target=Target(selector='[data-testid="search_city_to0_wrapper"]',
                                                         test_id="search_city_to0_wrapper", text="Going to"))
    script.steps[2] = Step(action="wait", value="100")
    script.id = "SCR-TRIPOLD002"
    a.scripts.save(script)
    rec = _play(a, script.id, headless=True)
    assert rec["status"] == "PASSED", (rec["error"], _steps(rec))
    assert rec["steps"][4]["chosen"] == "New Delhi - All airports India"


@pytest.fixture()
def display():
    import shutil
    import subprocess
    if os.environ.get("DISPLAY"):
        yield os.environ["DISPLAY"]
        return
    if not shutil.which("Xvfb"):
        pytest.skip("needs a display (Xvfb) for a visible browser")
    proc = subprocess.Popen(["Xvfb", ":96", "-screen", "0", "1400x900x24"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    os.environ["DISPLAY"] = ":96"
    try:
        yield ":96"
    finally:
        os.environ.pop("DISPLAY", None)
        proc.terminate()


def test_older_trip_recording_plays_in_a_visible_browser_with_the_cursor(rt, site, dates, display):
    """As the tester runs it: Headless off, the visual cursor and highlight on (Guided Browser Automation > Run)."""
    a = rt.automation
    a.scripts.save(old_trip_recording(site, *dates))
    rt.settings.update({"playback": {"visual_cursor": True, "highlight": True, "visual_delay_ms": 150}})
    rec = _play(a, "SCR-TRIPOLD001", headless=False)
    assert rec["status"] == "PASSED", (rec["error"], _steps(rec))
    assert rec["steps"][3]["chosen"] == "New Delhi - All airports India"


# ---- 23.7.1: the tester's re-test of 14 Nov 2026 (script SCR-9D41509DEA) on Trip.com's current flight box --------
def user_trip_recording_20261114(site: str, dep: date, ret: date) -> Script:
    """Steps 6 to 15 of SCR-9D41509DEA exactly as the tester's 23.7.0 recorded them (the banner clicks 2 to 5 are
    left out: the stand-in has no banner). The departure was typed and never chosen from the list, the arrival
    suggestion is a click on [data-testid="0"], the end day's label holds that day's price, and the Adults "+" was
    recorded as a click on the whole Adults row (positional li)."""
    T = Target
    start = f"{DAYS[dep.weekday()]}, {MONTHS[dep.month - 1]} {dep.day}, {dep.year}, Select the start date you want"
    end = f"{DAYS[ret.weekday()]}, {MONTHS[ret.month - 1]} {ret.day}, {ret.year}, The price is AED 1.36k, Select the end date you want"
    d1, d2 = (dep - date.today()).days, (ret - date.today()).days
    steps = [
        Step(action="goto", value=f"{site}/tripcom/flights.html"),
        Step(action="click", target=T(selector='role=tab[name="Flights"]', role="tab", name="Flights", text="Flights",
                                      alternates=["#trip_searchBoxTabs > li:nth-of-type(2)"]), description="Click Flights"),
        Step(action="fill", target=T(selector='role=combobox[name="Departure airport or city"]', tag="input",
                                     role="combobox", name="Departure airport or city", input_type="text", test_id="search_city_from0",
                                     placeholder="Leaving from"),
             value="dubai international airport", description="Enter value in Departure airport or city"),
        Step(action="click", target=T(selector='div[aria-label="Arrival airport or city"]', role="group",
                                      name="Arrival airport or city", tag="div",
                                      alternates=['role=group[name="Arrival airport or city"]']),
             description="Click Arrival airport or city"),
        Step(action="fill", target=T(selector='role=combobox[name="Arrival airport or city"]', tag="input",
                                     role="combobox", name="Arrival airport or city", input_type="text", test_id="search_city_to0",
                                     placeholder="Going to"),
             value="del indira gandhi international airport", description="Enter value in Arrival airport or city"),
        Step(action="click", target=T(selector='[data-testid="0"]', test_id="0", tag="div", role="button",
                                      text="DEL Indira Gandhi International Airport New Delhi, India",
                                      name="DEL Indira Gandhi International Airport New Delhi, India",
                                      alternates=['role=button[name="DEL Indira Gandhi International Airport New Delhi, India"]',
                                                  "#m-flight-poi-list > div:nth-of-type(2) > ul > li > div:nth-of-type(1)"]),
             description="Click DEL Indira Gandhi International Airport New Delhi, India"),
        Step(action="pick_date", target=T(selector=f'role=checkbox[name="{start}"]', role="checkbox", name=start),
             value=f"{{{{TODAY+{d1}d:%Y-%m-%d}}}}", description="Choose the start date"),
        Step(action="pick_date", target=T(selector=f'div[aria-label="{end}"]', tag="div", name=end),
             value=f"{{{{TODAY+{d2}d:%Y-%m-%d}}}}", description="Choose the end date"),
        Step(action="click", target=T(selector='div[aria-label="1 adult, Economy"]', role="button",
                                      name="1 adult, Economy", text="1 adult, Economy",
                                      alternates=['role=button[name="1 adult, Economy"]']),
             description="Click 1 adult, Economy"),
        Step(action="click", target=T(selector="div > div:nth-of-type(2) > div > div:nth-of-type(3) > ul > li:nth-of-type(1)",
                                      tag="li", text="Adults 12+ years old 1"),
             description="Click Adults 12+ years old 1"),
        Step(action="click", target=T(selector='[data-testid="passengernum_cabin_search"]', test_id="passengernum_cabin_search",
                                      role="button", name="Search", text="Search",
                                      alternates=['role=button[name="Search"]']),
             description="Click Search"),
        Step(action="expect_text", target=T(selector="h2.results"),
             value=f"Flights Dubai to New Delhi · {dep.isoformat()} to {ret.isoformat()} · 2 adult, 0 child, 0 infant · Economy"),
    ]
    return Script(id="SCR-9D41509DEA", name="Recorded https://trip.com", mode="guided", steps=steps, browser="chromium",
                  base_url="https://trip.com")


def test_tester_recording_of_14_november_plays_back(rt, site):
    a = rt.automation
    t = date.today()
    dep, ret = t + timedelta(days=8), t + timedelta(days=14)
    a.scripts.save(user_trip_recording_20261114(site, dep, ret))
    rec = _play(a, "SCR-9D41509DEA", headless=True)
    print(_steps(rec))
    assert rec["status"] == "PASSED", (rec["error"], _steps(rec))


def _counter_script(site: str, sid: str, middle: list[Step]) -> Script:
    T = Target
    steps = [Step(action="goto", value=f"{site}/tripcom/flights.html"),
             Step(action="click", target=T(selector='div[aria-label="1 adult, Economy"]', role="button",
                                           name="1 adult, Economy", text="1 adult, Economy")),
             *middle,
             Step(action="expect_text", target=T(selector="#paxText"), value="3 adults, 1 child, Economy")]
    return Script(id=sid, name="counters", mode="guided", steps=steps, browser="chromium", base_url=site)


def test_set_count_step_written_by_hand(rt, site):
    """23.7.1: the step editor's set_count action (Value "Adults=3"), for fixing a recording without recording again."""
    a = rt.automation
    a.scripts.save(_counter_script(site, "SCR-SETCOUNT01", [Step(action="set_count", value="Adults=3"),
                                                            Step(action="set_count", value="Children: 1")]))
    rec = _play(a, "SCR-SETCOUNT01", headless=True)
    assert rec["status"] == "PASSED", (rec["error"], _steps(rec))


def test_ambiguous_older_counter_row_click_says_how_to_fix_it(rt, site):
    """A row click recorded at a number where both + and - work cannot be guessed: the run stops and names the fix."""
    a = rt.automation
    row = Step(action="click", target=Target(selector="ul > li:nth-of-type(2)", tag="li", text="Children 2-11 years old 1"))
    a.scripts.save(_counter_script(site, "SCR-SETCOUNT02", [Step(action="set_count", value="Children=1"), row]))
    rec = _play(a, "SCR-SETCOUNT02", headless=True)
    assert rec["status"] == "FAILED"
    assert "set_count" in rec["error"] and "Children=2" in rec["error"], rec["error"]


def test_exported_python_sets_counters_and_chooses_typed_suggestion(site, tmp_path):
    """The downloadable pytest file plays set_count steps (by hand or recorded as "Set Children to 1")."""
    import subprocess

    from qa_platform.automation import pytest_export

    script = _counter_script(site, "SCR-SETCOUNT03", [
        Step(action="set_count", value="Adults=3"),
        Step(action="click", target=Target(selector="span.qT7v >> nth=3"),
             semantic={"intent": "set_count", "label": "Children", "count": 1}),
    ])
    path = tmp_path / pytest_export.file_name(script)
    path.write_text(pytest_export.to_pytest(script), encoding="utf-8")
    env = {**os.environ, "HEADLESS": "1", "BROWSER": "chromium", "BASE_URL": site}
    out = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, env=env, timeout=180)
    assert out.returncode == 0, out.stdout + out.stderr


def test_tester_recording_of_14_november_plays_in_a_visible_browser(rt, site, display):
    """The same script, Headless off with the visual cursor and highlight, as the tester runs it."""
    a = rt.automation
    t = date.today()
    a.scripts.save(user_trip_recording_20261114(site, t + timedelta(days=8), t + timedelta(days=14)))
    rt.settings.update({"playback": {"visual_cursor": True, "highlight": True, "visual_delay_ms": 150}})
    rec = _play(a, "SCR-9D41509DEA", headless=False)
    assert rec["status"] == "PASSED", (rec["error"], _steps(rec))
    by = {s["index"]: s for s in rec["steps"]}
    assert by[2]["chosen"].startswith("DXB Dubai International Airport")
    assert by[9]["semantic"] == {"intent": "set_count", "label": "Adults", "count": 2, "verified": True}


def test_step_editor_keeps_semantic_actions_on_save():
    """The step editor's Action list holds every action a recording or Semantic upgrade writes (a step whose action
    was missing from it was saved as "goto")."""
    from qa_platform.automation.dsl import Action
    import typing

    rel = Path("web") / "js" / "pages" / "automation.js"
    js_src = (APP / rel if (APP / rel).is_file() else Path(__file__).resolve().parents[1] / rel).read_text(encoding="utf-8")
    listed = js_src.split("const ACTIONS = [", 1)[1].split("];", 1)[0]
    for action in ("choose_option", "set_count", "dismiss", "pick_date"):
        assert f'"{action}"' in listed, action
    assert "set_count" in typing.get_args(Action)
    assert "ACTIONS.includes(s.action) ? ACTIONS : [...ACTIONS, s.action]" in js_src


def test_record_and_play_departure_choice_on_the_current_flight_box(rt, site):
    """The tester's 23.7.1 recording on trip.com (run RUN-20261114-105553): typing the departure and clicking the
    DXB row is recorded as "Choose 'DXB Dubai International Airport ...' (typed ...)". Playing it failed with "was
    clicked but the field does not show it": the input matches role=combobox only while focused, and shows only
    "Dubai" afterwards (the code is in a label beside it)."""
    a = rt.automation
    run_id, s = _session(a, f"{site}/tripcom/flights.html")
    s.command("type", ('[data-testid="search_city_from0"]', "dubai international airport")); _pause(1.4)
    s.command("click", '[data-testid="search_result_box"] [data-testid="0"]'); _pause()
    s.command("type", ('[data-testid="search_city_to0"]', "del indira gandhi international airport")); _pause(1.4)
    s.command("click", '[data-testid="search_result_box"] [data-testid="0"]'); _pause()
    script = a.stop_recording(run_id)
    got = [(st.action, st.value) for st in script.steps]
    print(got, [st.target.selector for st in script.steps if st.target])
    assert ("choose_option", "DXB Dubai International Airport Dubai, United Arab Emirates") in got
    script.steps.append(Step(action="expect_value", target=Target(selector='[data-testid="search_city_from0"]'), value="Dubai"))
    script.steps.append(Step(action="expect_value", target=Target(selector='[data-testid="search_city_to0"]'), value="New Delhi"))
    a.scripts.save(script)
    rec = _play(a, script.id, headless=True)
    assert rec["status"] == "PASSED", (rec["error"], _steps(rec))


def test_choice_in_a_field_whose_locator_only_matches_while_focused(rt, site):
    """As recorded on trip.com: the field's locator is role=combobox[name=...], which matches only while the input
    has focus. The choice is checked on the element itself (pinned), and on what its group shows ("Dubai" plus the
    "DXB" label), not on a locator that no longer matches."""
    T = Target
    dep = T(selector='role=combobox[name="Departure airport or city"]', tag="input", role="combobox",
            name="Departure airport or city", placeholder="Leaving from", test_id="search_city_from0")
    option = "DXB Dubai International Airport Dubai, United Arab Emirates"
    steps = [Step(action="goto", value=f"{site}/tripcom/flights.html"),
             Step(action="fill", target=dep, value="dubai international airport"),
             Step(action="choose_option", target=dep, value=option,
                  semantic={"intent": "choose_option", "option": option, "typed": "dubai international airport",
                            "list": "suggestion", "option_selector": '[data-testid="0"]'}),
             Step(action="expect_value", target=T(selector='[data-testid="search_city_from0"]'), value="Dubai")]
    a = rt.automation
    a.scripts.save(Script(id="SCR-TRIPPIN001", name="pin", mode="guided", steps=steps, browser="chromium", base_url=site))
    rec = _play(a, "SCR-TRIPPIN001", headless=True)
    assert rec["status"] == "PASSED", (rec["error"], _steps(rec))
