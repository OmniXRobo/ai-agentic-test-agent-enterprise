"""Playback cursor and pick_date in a real browser: the cursor arrives before Playwright acts, a native <select>
choice is shown, and dates are chosen through the page's own calendar (paging months), a native date input, or by
typing in the field's format when there is no calendar."""

from __future__ import annotations

import time
from datetime import date, timedelta

import pytest

from qa_platform.automation import visual
from qa_platform.automation.browser import js
from qa_platform.automation.dsl import Script, Step, ValueResolver, describe

try:
    import playwright  # noqa: F401

    HAVE_PLAYWRIGHT = True
except ImportError:  # pragma: no cover
    HAVE_PLAYWRIGHT = False

needs_browser = pytest.mark.skipif(not HAVE_PLAYWRIGHT, reason="playwright is not installed")

# A small date picker like the ones in business applications: a text field (dd/mm/yyyy) that opens a calendar
# with a "Month YYYY" heading, previous/next buttons and day buttons, plus days of the neighbouring months.
PAGE = """<!doctype html><html><body style="font-family:sans-serif">
<label>Travel date <input id="d" placeholder="dd/mm/yyyy" autocomplete="off"></label>
<div id="cal" class="datepicker" style="display:none;border:1px solid #888;padding:6px;width:240px">
  <div><button type="button" aria-label="Previous month" id="prev">&lt;</button>
       <span class="datepicker-title" id="title"></span>
       <button type="button" aria-label="Next month" id="next">&gt;</button></div>
  <table><tbody id="days"></tbody></table>
</div>
<label>Native <input id="n" type="date"></label>
<label>Plain <input id="p" placeholder="mm/dd/yyyy"></label>
<select id="s"><option value="">Choose…</option><option value="eco">Economy</option><option value="bus">Business</option></select>
<script>
const M = ["January","February","March","April","May","June","July","August","September","October","November","December"];
let y = new Date().getFullYear(), m = new Date().getMonth();
const pad = (n) => String(n).padStart(2, "0");
function draw() {
  document.getElementById("title").textContent = M[m] + " " + y;
  const first = new Date(y, m, 1).getDay(), count = new Date(y, m + 1, 0).getDate(), prevCount = new Date(y, m, 0).getDate();
  let html = "<tr>";
  for (let i = 0; i < first; i++) html += `<td class="day old">${prevCount - first + i + 1}</td>`;
  for (let d = 1; d <= count; d++) {
    html += `<td><button type="button" class="day" data-d="${d}">${d}</button></td>`;
    if ((first + d) % 7 === 0) html += "</tr><tr>";
  }
  for (let i = 1; i <= 6; i++) html += `<td class="day new">${i}</td>`;
  document.getElementById("days").innerHTML = html + "</tr>";
}
document.getElementById("d").addEventListener("click", () => { document.getElementById("cal").style.display = "block"; draw(); });
document.getElementById("prev").onclick = () => { m--; if (m < 0) { m = 11; y--; } draw(); };
document.getElementById("next").onclick = () => { m++; if (m > 11) { m = 0; y++; } draw(); };
document.getElementById("days").addEventListener("click", (e) => {
  const d = e.target.dataset.d; if (!d) return;
  document.getElementById("d").value = pad(d) + "/" + pad(m + 1) + "/" + y;
  document.getElementById("cal").style.display = "none";
});
</script></body></html>"""


@pytest.fixture()
def page():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context()
        context.add_init_script(js("calendar"))
        context.add_init_script(visual.overlay_config({"cursor_speed_ms": 300, "click_sound": False}))
        context.add_init_script(js("overlay"))
        pg = context.new_page()
        pg.set_content(PAGE)
        yield pg
        browser.close()


@needs_browser
@pytest.mark.parametrize("days", [1, 0, 45, -40])
def test_pick_date_uses_the_calendar_and_pages_months(page, days):
    target = date.today() + timedelta(days=days)
    how = visual.pick_date(page, page.locator("#d"), target.isoformat(), 5000, True, 50)
    assert how == "calendar"
    assert page.input_value("#d") == target.strftime("%d/%m/%Y")
    assert page.locator("[data-qa-cal]").count() == 0


@needs_browser
def test_pick_date_native_and_typed_fallback(page):
    target = date.today() + timedelta(days=3)
    assert visual.pick_date(page, page.locator("#n"), target.isoformat(), 5000, False, 0) == "native"
    assert page.input_value("#n") == target.isoformat()
    assert visual.pick_date(page, page.locator("#p"), target.isoformat(), 5000, False, 0) == "typed"
    assert page.input_value("#p") == target.strftime("%m/%d/%Y")


@needs_browser
def test_cursor_arrives_before_the_action_and_dropdown_choice_is_shown(page):
    box = page.locator("#s").bounding_box()
    started = time.monotonic()
    visual.point(page, box, "type", "Cabin")
    assert time.monotonic() - started >= 0.1  # evaluate waited for the glide (cursor speed 300 ms, scaled by distance)
    visual.choose(page, page.locator("#s"), box, "Business")
    page.locator("#s").select_option(label="Business")
    assert page.input_value("#s") == "bus"


def test_relative_date_rule_resolves_at_playback_time():
    step = Step(action="pick_date", value="{{TODAY+1d:%Y-%m-%d}}", target={"selector": "#d", "description": "Travel date"})
    assert describe(step) == "Pick date {{TODAY+1d:%Y-%m-%d}} in Travel date"
    assert ValueResolver().resolve(step.value) == (date.today() + timedelta(days=1)).isoformat()
    Script(id="SCR-T", name="t", steps=[step])  # pick_date is a valid DSL action
