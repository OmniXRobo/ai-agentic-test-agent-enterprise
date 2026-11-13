"""23.6.0: universal web component recognition, semantic playback of older recordings, pause for help, and the
payment / booking guard. Real headless Chromium against the local fixture pages in tests/site/components (a travel
search app and a page of custom widgets, with generated, duplicate and changing ids)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

APP = Path(os.environ.get("APP_DIR", Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(APP))

from qa_platform.automation import components, semantic  # noqa: E402
from qa_platform.automation.browser import js  # noqa: E402
from qa_platform.automation.dsl import Script, Step, Target  # noqa: E402

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def long_label(d: date, end: bool = False, price: bool = False) -> str:
    """The way a hotel calendar names its days (as in the recording the tester sent)."""
    return (f"{DAYS[d.weekday()]}, {MONTHS[d.month - 1]} {d.day}, {d.year}" + (", The price is AED 1.35k" if price else "")
            + (", Select the end date you want" if end else ", Select the start date you want"))


def short_label(d: date) -> str:
    return f"{DAYS[d.weekday()][:3]} {MONTHS[d.month - 1][:3]} {d.day:02d} {d.year}"


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture()
def page(browser, site):
    ctx = browser.new_context()
    ctx.add_init_script(js("components"))
    pg = ctx.new_page()
    yield pg
    ctx.close()


# ---- text understanding ----------------------------------------------------------------------------------------

def test_dates_and_roles_in_labels():
    assert components.date_in_text("Sunday, November 15, 2026, Select the start date you want") == "2026-11-15"
    assert components.date_in_text("Friday, November 27, 2026, The price is AED 1.35k, Select the end date you want") \
        == "2026-11-27"
    assert components.date_in_text("Sun Nov 15 2026") == "2026-11-15"
    assert components.date_in_text("15 November 2026") == "2026-11-15"
    assert components.date_in_text("05/11/2026") is None  # day / month order is never guessed
    assert components.date_in_text("February 30, 2026") is None
    assert components.date_role("..., Select the end date you want") == "end"
    assert components.date_role("Check-in") == "start"
    assert components.stable_text("Friday, November 27, 2026, The price is AED 1.35k, Select") == \
        "Friday, November 27, 2026, Select"


def test_semantic_inference_of_an_older_recording():
    steps = [
        Step(action="click", target=Target(selector='div[aria-label="Sunday, November 15, 2026, Select the start date you want"]')),
        Step(action="click", target=Target(selector='role=checkbox[name="Saturday, November 21, 2026, Select the end date you want"]')),
        Step(action="click", target=Target(selector="html > body")),
        Step(action="hover", target=Target(selector="div > div:nth-of-type(3)")),
        Step(action="click", target=Target(selector="#searchBoxCon")),
    ]
    s = Script(id="SCR-ABCDEF1234", name="x", steps=steps)
    assert semantic.infer(steps[0]) | {} == {**semantic.infer(steps[0])}
    assert semantic.infer(steps[0])["date"] == "2026-11-15" and semantic.infer(steps[0])["role"] == "start"
    assert semantic.infer(steps[1])["role"] == "end"
    assert semantic.infer(steps[2])["intent"] == "dismiss"
    assert semantic.infer(steps[3])["intent"] == "prepare"
    assert semantic.infer(steps[4]) == {}
    upgraded, changes = semantic.upgrade(s, today=date(2026, 11, 13))
    assert [c["change"] for c in changes] == ["calendar click -> pick_date", "calendar click -> pick_date",
                                             "page click -> dismiss", "hover -> optional"]
    assert upgraded.steps[0].action == "pick_date" and upgraded.steps[0].value == "2026-11-15"
    assert s.steps[0].action == "click"  # the original is not changed (saved as a new version only on apply)
    _, later = semantic.upgrade(s, today=date(2026, 11, 20))
    assert later[0]["change"] == "date in the past"


# ---- component recognition and interaction ---------------------------------------------------------------------

def test_classification_without_ids(page, site):
    page.goto(f"{site}/components/widgets.html")
    kinds = {name: components.classify(page.locator(sel))["kind"] for name, sel in {
        "native": "select[name=country]", "date": "input[type=date]", "react": ".css-mohuvp-input",
        "mui": ".MuiSelect-select", "bootstrap": ".dropdown-toggle", "container": "section >> nth=0",
        "page": "body"}.items()}
    assert kinds == {"native": "native_select", "date": "date_input", "react": "autocomplete", "mui": "dropdown",
                     "bootstrap": "dropdown", "container": "container", "page": "page"}


@pytest.mark.parametrize("field,option,shown", [
    ("select[name=country]", "India", "India"),                     # native <select>
    (".MuiSelect-select", "Suite", "Suite"),                         # Material UI style, list in a portal
    (".ant-select-selector", "Medium", "Medium"),                    # Ant Design style
    (".dropdown-toggle", "Rating", "Sort by Rating"),                # Bootstrap dropdown
    (".css-13cymwt-control", "Lisbon", "Lisbon"),                    # React Select style (generated classes)
    (".multi", "Gym", "Gym"),                                        # multi-select with options loaded after 600 ms
])
def test_dropdowns(page, site, field, option, shown):
    page.goto(f"{site}/components/widgets.html")
    res = components.choose_option(page, page.locator(field).first, option)
    assert res["verified"], res
    assert shown in page.locator(field).first.evaluate("el => window.__sgComp.shownNear(el)")


def test_searchable_dropdown_filters_by_typing(page, site):
    page.goto(f"{site}/components/widgets.html")
    field = page.locator(".css-mohuvp-input")
    field.fill("lo")  # London, Los Angeles, Lisbon? no: "lo" matches London and Los Angeles and Lisbon? (no: lisbon)
    with pytest.raises(components.ComponentError) as err:
        components.commit_autocomplete(page, field, "lo")
    assert err.value.code == "OPTION_AMBIGUOUS" and "London" in err.value.choices  # never a guess
    field.fill("London")
    assert components.commit_autocomplete(page, field, "London")["chosen"] == "London"
    assert "London" in page.locator(".css-1dimb5e-singleValue").inner_text()


def test_unknown_option_lists_what_is_there(page, site):
    page.goto(f"{site}/components/widgets.html")
    with pytest.raises(components.ComponentError) as err:
        components.choose_option(page, page.locator(".MuiSelect-select"), "Penthouse")
    assert err.value.code == "OPTION_NOT_FOUND" and "Deluxe" in err.value.choices


def test_modal_iframe_and_shadow_dom(page, site):
    page.goto(f"{site}/components/widgets.html")
    page.locator(".open-modal").click()
    res = components.choose_option(page, page.get_by_role("dialog", name="Filters").get_by_role("button", name="Stars"),
                                   "4 stars")
    assert res["verified"]
    frame = page.frame_locator("iframe[title=Inner]")
    assert components.choose_option(page, frame.locator("select[name=meal]"), "Half board")["verified"]
    assert components.choose_option(page, page.locator("#shadow-host select"), "EUR")["verified"]


def test_duplicate_ids_are_told_apart_by_context(page, site):
    page.goto(f"{site}/components/widgets.html")
    assert page.locator("#same").count() == 2  # the same id twice: an id alone cannot identify the control
    page.get_by_role("dialog", name="Second").get_by_role("button", name="Continue").click()
    assert page.locator("#log").inner_text().strip() == "second"


def test_native_date_and_number_only_calendar_with_month_navigation(page, site):
    page.goto(f"{site}/components/widgets.html")
    want = (date.today() + timedelta(days=95)).isoformat()
    assert components.pick_date(page, want, field=page.locator("input[type=date]"))["how"] == "native"
    assert page.locator("input[type=date]").input_value() == want
    res = components.pick_date(page, want, field=page.locator(".dob"), today=date.today())
    assert res == {"how": "calendar", "verified": True}
    assert page.locator(".dob .val").inner_text() == want


def test_past_and_disabled_dates_are_refused(page, site):
    page.goto(f"{site}/components/travel.html")
    with pytest.raises(components.ComponentError) as err:
        components.pick_date(page, (date.today() - timedelta(days=2)).isoformat(), role="start", today=date.today())
    assert err.value.code == "DATE_IN_PAST"
    if date.today().day > 1:  # an earlier day of this month is drawn, but disabled
        page.locator("[data-k=in]").click()
        with pytest.raises(components.ComponentError) as err:
            components.pick_date(page, date.today().replace(day=1).isoformat())
        assert err.value.code == "DATE_DISABLED"


def test_hotel_date_range_two_panels(page, site):
    page.goto(f"{site}/components/travel.html")
    start, end = date.today() + timedelta(days=33), date.today() + timedelta(days=36)
    # the same day number in two panels: the full date decides
    assert components.pick_date(page, start.isoformat(), role="start", today=date.today())["verified"]
    assert components.pick_date(page, end.isoformat(), role="end", today=date.today())["verified"]
    assert page.locator("[data-k=in] .val").inner_text() == f"{start.day} {MONTHS[start.month - 1][:3]} {start.year}"
    assert page.locator("[data-k=out] .val").inner_text() == f"{end.day} {MONTHS[end.month - 1][:3]} {end.year}"


# ---- playback: an older recording of the hotel and flight searches --------------------------------------------

def _play(a, script_id, **kw):
    """Play in another thread: the module's own Playwright (sync API) owns this thread's event loop."""
    out = {}
    t = threading.Thread(target=lambda: out.update(rec=a.run_script(script_id, background=False, **kw)))
    t.start()
    t.join(300)
    return out["rec"]


def _travel_script(site: str, start: date, end: date, dep: date, ret: date) -> Script:
    T = Target
    steps = [
        Step(action="goto", value=f"{site}/components/travel.html"),
        Step(action="hover", target=T(selector="div > div:nth-of-type(1) > div > div:nth-of-type(3)")),
        Step(action="click", target=T(selector="#searchBoxCon", description="Paris France Paris, Texas United States")),
        Step(action="fill", target=T(selector='input[placeholder="Where to?"]', placeholder="Where to?"), value="Paris"),
        Step(action="click", target=T(selector=f'div[aria-label="{long_label(start)}"]')),
        Step(action="click", target=T(selector=f'div[aria-label="{long_label(end, end=True, price=True)}"]')),
        Step(action="click", target=T(selector='role=button[name="1 room, 2 adults, 0 children"]')),
        Step(action="click", target=T(selector='[aria-label="Add Rooms"]')),
        Step(action="click", target=T(selector='text="Done"')),
        Step(action="click", target=T(selector='[data-form=hotels] >> role=button[name="Search"]')),
        Step(action="expect_text", target=T(selector="h2.results"), value="Hotels in Paris"),
        Step(action="expect_text", target=T(selector="h2.results"), value=f"{start.isoformat()} to {end.isoformat()}"),
        Step(action="expect_text", target=T(selector="h2.results"), value="2 rooms"),
        Step(action="click", target=T(selector='a[aria-label="Test Travel"]')),
        Step(action="click", target=T(selector='role=tab[name="Flights"]')),
        Step(action="click", target=T(selector="html > body")),
        Step(action="hover", target=T(selector='role=combobox[name="Departure airport or city"]')),
        Step(action="fill", target=T(selector='role=combobox[name="Departure airport or city"]'), value="Dubai"),
        Step(action="click", target=T(selector="html > body")),
        Step(action="fill", target=T(selector='role=combobox[name="Arrival airport or city"]'), value="New Delhi"),
        Step(action="click", target=T(selector=f'role=gridcell[name="{short_label(dep)}"]')),
        Step(action="click", target=T(selector=f'role=gridcell[name="{short_label(ret)}"]')),
        Step(action="click", target=T(selector='role=button[name="1 adult, Economy"]')),
        Step(action="choose_option", target=T(selector='role=button[name="Cabin class"]'), value="Business"),
        Step(action="click", target=T(selector='text="Done"')),
        Step(action="click", target=T(selector="[data-form=flights] .go")),
        Step(action="expect_text", target=T(selector="h2.results"),
             value=f"Flights Dubai to New Delhi · {dep.isoformat()} to {ret.isoformat()} · 1 adult, Business"),
        Step(action="click", target=T(selector='role=button[name="Select this fare"]')),
        Step(action="click", target=T(selector='role=button[name="Book now"]', name="Book now")),
    ]
    return Script(id="SCR-TRAVEL0001", name="Travel searches", mode="guided", steps=steps, browser="chromium",
                  base_url=f"{site}/components/travel.html")


@pytest.fixture()
def travel_dates():
    t = date.today()
    return t + timedelta(days=30), t + timedelta(days=33), t + timedelta(days=45), t + timedelta(days=50)


def test_older_recording_of_hotel_and_flight_search_plays_back(rt, site, travel_dates):
    a = rt.automation
    a.scripts.save(_travel_script(site, *travel_dates))
    rec = _play(a, "SCR-TRAVEL0001", headless=True)
    by = {s["index"]: s for s in rec["steps"]}
    assert rec["status"] == "PASSED", (rec["error"], [(s["index"], s["status"], s.get("error")) for s in rec["steps"]])
    assert by[1]["status"] == "SKIPPED"          # hover target gone: a hover only prepares the next step
    assert by[2]["status"] == "SKIPPED"          # a click on the whole search form is not replayed
    assert by[3]["chosen"].startswith("Paris")   # the autocomplete suggestion was chosen, not just typed
    assert by[4]["semantic"] == {"intent": "pick_date", "date": travel_dates[0].isoformat(), "role": "start",
                                 "verified": True}
    assert by[5]["semantic"]["role"] == "end" and by[5]["semantic"]["verified"]
    assert by[20]["semantic"]["date"] == travel_dates[2].isoformat()
    assert by[23]["chosen"] == "Business"


def test_booking_commitment_is_guarded_when_asked(rt, site, travel_dates):
    a = rt.automation
    a.scripts.save(_travel_script(site, *travel_dates))
    rt.settings.update({"playback": {"guard_commits_unattended": True}})
    rec = _play(a, "SCR-TRAVEL0001", headless=True)
    assert rec["status"] == "FAILED" and rec["failed_step"] == 28
    assert "booking commitment" in rec["error"]


def test_a_changed_page_is_not_guessed(rt, site, travel_dates):
    """A recorded date that the calendar does not offer any more (in the past) fails clearly, it is not replaced."""
    a = rt.automation
    script = _travel_script(site, *travel_dates)
    past = date.today() - timedelta(days=3)
    script.steps[4] = Step(action="click", target=Target(selector=f'div[aria-label="{long_label(past)}"]'))
    a.scripts.save(script)
    rec = _play(a, "SCR-TRAVEL0001", headless=True)
    assert rec["status"] == "FAILED" and rec["failed_step"] == 4
    assert "in the past" in rec["error"]


def test_semantic_upgrade_endpoint_keeps_the_old_version(client, site, travel_dates):
    rt = client.app.state.runtime
    rt.automation.scripts.save(_travel_script(site, *travel_dates))
    r = client.post("/api/automation/scripts/SCR-TRAVEL0001/semantic-upgrade").json()
    assert r["applied"] is False and len(r["changes"]) >= 6
    r = client.post("/api/automation/scripts/SCR-TRAVEL0001/semantic-upgrade?apply=true").json()
    assert r["applied"] and r["version"] == 2
    s = rt.automation.scripts.get("SCR-TRAVEL0001")
    assert s.steps[4].action == "pick_date" and s.steps[4].semantic["role"] == "start"
    assert rt.automation.scripts.versions("SCR-TRAVEL0001")[-1]["reason"].startswith("semantic upgrade")


# ---- pause for help in a visible run ---------------------------------------------------------------------------

@pytest.fixture()
def display():
    if os.environ.get("DISPLAY"):
        yield os.environ["DISPLAY"]
        return
    if not shutil.which("Xvfb"):
        pytest.skip("needs a display (Xvfb) for a visible browser")
    proc = subprocess.Popen(["Xvfb", ":97", "-screen", "0", "1400x900x24"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    os.environ["DISPLAY"] = ":97"
    try:
        yield ":97"
    finally:
        os.environ.pop("DISPLAY", None)
        proc.terminate()


def test_visible_run_pauses_for_help_then_resumes(rt, site, travel_dates, display):
    a = rt.automation
    script = _travel_script(site, *travel_dates)
    # an autocomplete value with several matching suggestions: the agent asks instead of guessing
    script.steps[3] = Step(action="fill", target=Target(selector='input[placeholder="Where to?"]'), value="Par")
    script.steps = script.steps[:13]
    script.steps[10] = Step(action="expect_text", target=Target(selector="h2.results"), value="Hotels in Paris, Texas")
    a.scripts.save(script)
    rt.settings.update({"playback": {"visual_cursor": False, "highlight": False}})
    run_id = a.run_script("SCR-TRAVEL0001", headless=False, interactive=True)
    pending = None
    for _ in range(200):
        pending = a.runner.intervention(run_id)
        if pending:
            break
        time.sleep(0.2)
    assert pending and pending["step"] == 3 and "choose" in pending["options"]
    assert {"Paris France", "Paris, Texas United States"} <= set(pending["choices"])
    a.runner.answer_intervention(run_id, "choose", "Paris, Texas United States")
    for _ in range(300):
        res = a.runner.results(run_id=run_id)
        if res and res[0]["status"] != "RUNNING":
            break
        time.sleep(0.2)
    assert res[0]["status"] == "PASSED", res[0]["error"]
    step3 = res[0]["steps"][3]
    assert step3["interventions"][0]["answer"] == "choose" and step3["status"] == "PASSED"
