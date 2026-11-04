"""Visual playback helpers shared by the runtime runner and the recorder: the cursor overlay settings, the
synchronized move -> highlight -> click sequence, visible dropdown choices, and choosing a date in a calendar.

The overlay functions return Promises, and ``page.evaluate`` waits for them, so Playwright acts only after the
cursor has actually arrived. The framework template (``framework/template/utilities/base_page.py``) has the same
sequence for exported pytest projects, which must run without this package.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

MAX_MONTH_STEPS = 24


def overlay_config(playback: dict[str, Any]) -> str:
    """Init script with the playback cursor settings (added before overlay.js)."""
    cfg = {
        "speedMs": max(0, min(2000, int(playback.get("cursor_speed_ms", 450) or 0))),
        "highlight": bool(playback.get("highlight", True)),
        "sound": bool(playback.get("click_sound", True)),
        "volume": max(0.0, min(1.0, float(playback.get("click_volume", 0.35) or 0))),
    }
    return f"window.__qaOverlayConfig = {json.dumps(cfg)};"


def point(page: Any, box: dict[str, float], kind: str, label: str) -> None:
    page.evaluate("([b, k, l]) => window.__qaOverlay ? window.__qaOverlay.point(b.x, b.y, b.width, b.height, k, l) : null",
                  [box, kind, label[:60]])


def click(page: Any, box: dict[str, float]) -> None:
    page.evaluate("([x, y]) => window.__qaOverlay ? window.__qaOverlay.click(x, y) : null",
                  [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])


def choose(page: Any, locator: Any, box: dict[str, float], value: str) -> None:
    """Show a native <select>'s options and the cursor choosing ``value`` (by value or label)."""
    try:
        options = locator.evaluate("el => el.options ? [...el.options].map(o => [o.value, o.text.trim()]) : null")
    except Exception:
        return
    if not options:
        return
    index = next((i for i, (v, t) in enumerate(options) if v == value), None)
    if index is None:
        index = next((i for i, (_v, t) in enumerate(options) if t.lower() == (value or "").lower()), None)
    if index is None:
        return
    page.evaluate("([b, o, i]) => window.__qaOverlay ? window.__qaOverlay.choose(b.x, b.y, b.width, b.height, o, i) : null",
                  [box, [t for _v, t in options], index])


def parse_date(value: str) -> date:
    """Dates for pick_date are ISO (YYYY-MM-DD); runtime values such as {{TODAY+1d:%Y-%m-%d}} resolve to that."""
    m = re.match(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})", value or "")
    if not m:
        raise ValueError(f"pick_date needs a date as YYYY-MM-DD, got '{value}'.")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def typed_format(placeholder: str) -> str:
    """strftime format for typing a date into a field, from its placeholder (ISO if unknown).

    23.3.18: any order (yyyy-dd-mm in OrangeHRM, dd/mm/yyyy, mm-dd-yyyy, dd.mm.yyyy ...)."""
    from .date_rules import format_from_placeholder

    own = format_from_placeholder(placeholder)
    if own and any(k in own for k in ("%d", "%Y")):
        return own
    return "%Y-%m-%d"


def pick_date(page: Any, locator: Any, value: str, timeout: int, visual: bool, delay: int) -> str:
    """Choose a date the way a person would: open the calendar, page to the month, click the day.

    Native date inputs are filled directly. If no calendar can be read, the date is typed in the field's format.
    Returns how the date was set ("native", "calendar" or "typed").
    """
    target = parse_date(value)
    iso = target.isoformat()
    opener = locator
    if locator.evaluate("el => !['INPUT', 'TEXTAREA'].includes(el.tagName)"):
        # 23.3.18: the step points at the calendar icon (older recordings): the field is the input next to it
        near = locator.locator("xpath=ancestor-or-self::*[.//input[not(@type='hidden')]][1]//input[not(@type='hidden')]").first
        if near.count():
            locator = near
    kind = (locator.get_attribute("type", timeout=timeout) or "").lower()
    if kind == "date":
        locator.fill(iso, timeout=timeout)
        return "native"
    box = opener.bounding_box(timeout=timeout)
    if visual and box:
        click(page, box)
    opener.click(timeout=timeout)
    page.wait_for_timeout(300)
    for turn in range(MAX_MONTH_STEPS + 1):
        step = ""
        for wait in range(12):  # a slow page can take a moment to open or redraw the calendar
            try:
                step = page.evaluate("iso => window.__qaCal ? window.__qaCal.step(iso) : ''", iso)
            except Exception:
                step = ""
            if step:
                break
            if turn == 0 and wait == 4:  # the calendar closed again (a previous picker was still closing): reopen
                try:
                    opener.click(timeout=timeout)
                except Exception:
                    pass
            page.wait_for_timeout(200)
        if not step:
            break
        mark = page.locator('[data-qa-cal="target"]').first
        if visual:
            b = mark.bounding_box(timeout=timeout)
            if b:
                point(page, b, "click", "Next month" if step == "next" else "Previous month" if step == "prev" else f"Day {target.day}")
                page.wait_for_timeout(min(delay, 250))
                click(page, b)
        mark.click(timeout=timeout)
        if step == "day":
            page.evaluate("() => window.__qaCal && window.__qaCal.clear()")
            page.wait_for_timeout(300)  # let the calendar finish closing before the next step
            return "calendar"
        page.wait_for_timeout(200)
    # No readable calendar: type the date in the field's own format (from its placeholder).
    placeholder = locator.get_attribute("placeholder", timeout=timeout) or ""
    page.keyboard.press("Escape")
    text = target.strftime(typed_format(placeholder))
    locator.fill(text, timeout=timeout)
    try:  # the application reads the typed date when the field loses focus
        locator.dispatch_event("change")
        locator.press("Tab", timeout=2000)
    except Exception:
        pass
    return "typed"
