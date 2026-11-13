"""Universal web component handlers (23.6.0).

One interface for every kind of control, behind which each component type has its own interaction strategy:

    classify()            what the element is (native select, custom / portal dropdown, autocomplete, calendar day,
                          date field, container ...), read from roles, ARIA state and structure (js/components.js)
    pick_date()           calendars and date ranges: open the right date field when needed, page to the month, click
                          the day whose *full date* matches (never just the day number), skip disabled days, verify
    choose_option()       native selects with select_option(); custom, searchable, portal and multi-select dropdowns
                          by opening them and clicking the option with that text; verify the value is shown
    commit_autocomplete() after typing into an autocomplete: choose the suggestion that is exactly what was typed
                          (or the only one that starts with it); never a guess between several
    dismiss()             "click on the page background" to close a popup, done safely (Escape + blur), never a
                          click at the middle of the page
    settle()              state-aware waiting: until the DOM is quiet and no spinner is shown, with a bound

Used by the Guided Browser Automation runner (playback), the recorder (semantic steps) and the Operations Agent's
Playwright driver. Every click is Playwright's trusted click on the element js/components.js marked.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Callable

from .browser import js

MAX_MONTH_STEPS = 30
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MONTH_RE = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
             r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
TARGET = '[data-sg-target="1"]'


class ComponentError(Exception):
    """A control could not be used confidently. ``code`` says why; ``choices`` lists what a person can pick."""

    def __init__(self, code: str, message: str, *, choices: list[str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.choices = choices or []


# ---- text understanding (Python twin of the JS parser, for steps recorded before 23.6.0) -------------------------

def _iso(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def date_in_text(text: str | None) -> str | None:
    """A full date named in a label such as "Sunday, November 15, 2026, Select the start date you want" or
    "Sun Nov 15 2026", as YYYY-MM-DD. Day / month order is never guessed: only ISO and month-name forms count."""
    s = str(text or "")
    if not s:
        return None
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", s)
    if m:
        return _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(rf"\b{_MONTH_RE}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", s, re.I)
    if m:
        return _iso(int(m.group(3)), _MONTHS.index(m.group(1).lower()[:3]) + 1, int(m.group(2)))
    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH_RE}\.?,?\s+(\d{{4}})\b", s, re.I)
    if m:
        return _iso(int(m.group(3)), _MONTHS.index(m.group(2).lower()[:3]) + 1, int(m.group(1)))
    return None


def date_role(text: str | None) -> str | None:
    """'start' (check-in, departure) or 'end' (check-out, return) from a calendar cell's label, if it says so."""
    t = str(text or "")
    if re.search(r"end date|check.?out|\breturn|until|inbound", t, re.I):
        return "end"
    if re.search(r"start date|check.?in|depart|outbound", t, re.I):
        return "start"
    return None


_DYNAMIC = [
    re.compile(r",?\s*the price is [^,]*", re.I),
    re.compile(r"\b(?:aed|usd|eur|gbp|inr|sar|qar|\$|€|£|₹)\s?[\d.,]+\s*[km]?\b", re.I),
    re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:k|m)\b", re.I),
    re.compile(r"\b\d+\s*(?:left|seats? left|rooms? left)\b", re.I),
]


def stable_text(text: str | None) -> str:
    """A label without the parts that change between visits (prices, "3 left" counters)."""
    t = str(text or "")
    for rx in _DYNAMIC:
        t = rx.sub(" ", t)
    t = re.sub(r"\s+,", ",", " ".join(t.split()))
    return re.sub(r",{2,}", ",", t).strip(" ,")


# ---- page plumbing -----------------------------------------------------------------------------------------------

def ensure(page: Any) -> None:
    """js/components.js is added to every page by the runner; this also covers pages opened before that."""
    try:
        if page.evaluate("() => !!window.__sgComp"):
            return
    except Exception:
        return
    try:
        page.evaluate(js("components"))
    except Exception:
        pass


def settle(page: Any, quiet_ms: int = 300, max_ms: int = 4000) -> str:
    """Wait until the page is quiet: no DOM changes for ``quiet_ms`` and no busy indicator, at most ``max_ms``."""
    ensure(page)
    why = "quiet"
    try:
        res = page.evaluate("([q, m]) => window.__sgComp.settle(q, m)", [quiet_ms, max_ms])
        why = (res or {}).get("why", "quiet")
        for _ in range(int(max_ms / 250)):
            if not page.evaluate("() => window.__sgComp.busy()"):
                break
            page.wait_for_timeout(250)
    except Exception:
        why = "navigated"
    return why


def classify(locator: Any, timeout: int = 5000) -> dict[str, Any]:
    try:
        ensure(locator.page)
        return locator.evaluate("el => window.__sgComp ? window.__sgComp.classify(el) : {kind: 'unknown'}",
                                timeout=timeout) or {"kind": "unknown"}
    except Exception:
        return {"kind": "unknown"}


def _click_marked(page: Any, timeout: int, pointer: Callable[[Any, str], None] | None, label: str) -> None:
    mark = page.locator(TARGET).first
    if pointer:
        try:
            pointer(mark, label)
        except Exception:
            pass
    mark.click(timeout=timeout)
    try:
        page.evaluate("() => window.__sgComp && window.__sgComp.clear()")
    except Exception:
        pass


def _field_shows(page: Any, field: Any, iso: str) -> bool:
    try:
        text = field.evaluate("el => window.__sgComp.shownNear(el)")
        return bool(page.evaluate("([t, d]) => window.__sgComp.showsDate(t, d)", [text, iso]))
    except Exception:
        return False


# ---- calendars and date ranges ------------------------------------------------------------------------------------

def pick_date(page: Any, iso: str, *, field: Any = None, role: str | None = None, timeout: int = 10000,
              pointer: Callable[[Any, str], None] | None = None, today: date | None = None) -> dict[str, Any]:
    """Choose ``iso`` (YYYY-MM-DD) in whatever calendar the page uses.

    ``field`` is the date field that opens the calendar (check-in, departure ...), when known; ``role`` ('start' /
    'end') finds it when it is not. Returns {"how", "verified"}; raises ComponentError when the date is in the past,
    disabled, or the calendar cannot be read confidently."""
    want = date.fromisoformat(iso)
    if today and want < today:
        raise ComponentError("DATE_IN_PAST", f"{want:%d %B %Y} is in the past; the application will not offer it. "
                                             "Use a date rule such as Current date + N days.")
    ensure(page)
    if field is not None:
        kind = classify(field).get("kind")
        if kind == "date_input":  # a native <input type=date>: the browser takes the ISO value
            field.fill(iso, timeout=timeout)
            return {"how": "native", "verified": field.input_value(timeout=timeout) == iso}
    opened = False
    for turn in range(MAX_MONTH_STEPS + 1):
        res: dict[str, Any] = {}
        for wait in range(10):  # a calendar can take a moment to render after the field is clicked
            res = page.evaluate("d => window.__sgComp.findDay(d)", iso) or {}
            if res.get("status") != "none":
                break
            if not opened:
                opened = True
                if field is None:
                    found = page.evaluate("r => window.__sgComp.markDateField(r)", role or "")
                    if found.get("status") == "found":
                        _click_marked(page, timeout, pointer, f"Open the {role or ''} date".replace("  ", " "))
                    elif found.get("status") == "ambiguous":
                        raise ComponentError("DATE_FIELD_AMBIGUOUS", "Several date fields could open this calendar.",
                                             choices=found.get("fields") or [])
                else:
                    if pointer:
                        pointer(field, "Open the calendar")
                    field.click(timeout=timeout)
                settle(page, 250, 2500)
                continue
            page.wait_for_timeout(250)
        status = res.get("status")
        if status == "day":
            _click_marked(page, timeout, pointer, f"{want:%d %b %Y}")
            settle(page, 250, 2500)
            return {"how": "calendar", "verified": _verify_day(page, iso, field)}
        if status in ("next", "prev"):
            _click_marked(page, timeout, pointer, "Next month" if status == "next" else "Previous month")
            settle(page, 200, 2000)
            continue
        if status == "disabled":
            raise ComponentError("DATE_DISABLED", f"{want:%d %B %Y} is shown but cannot be chosen "
                                                  f"(not available: {res.get('label') or 'disabled day'}).")
        if status == "missing":
            raise ComponentError("DATE_NOT_SHOWN", f"The calendar shows the month of {want:%d %B %Y} but not that day.")
        if status == "no_nav":
            raise ComponentError("CALENDAR_NO_NAV", f"The calendar does not show {want:%B %Y} and has no "
                                                    f"{res.get('direction')} month control I can recognise.")
        break
    raise ComponentError("CALENDAR_NOT_FOUND", "No open calendar could be read on the page"
                         + ("" if field is not None or role else " and no date field was recognised") + ".")


def _verify_day(page: Any, iso: str, field: Any) -> bool:
    """The day is shown as chosen in the calendar, or a field shows the date, or (date-range calendars) the calendar
    moved on to the next choice. A click alone is not proof."""
    try:
        state = page.evaluate("d => window.__sgComp.daySelected(d)", iso) or {}
        if state.get("selected"):
            return True
    except Exception:
        state = {}
    if field is not None and _field_shows(page, field, iso):
        return True
    try:  # any date field on the page now shows it (the calendar closed and wrote the date)
        shown = page.evaluate(
            "d => [...document.querySelectorAll('input,button,[role=button],[role=combobox],div,span')]"
            ".filter(e => e.children.length <= 4 && e.getClientRects().length)"
            ".some(e => window.__sgComp.showsDate((e.value || '') + ' ' + (e.innerText || '').slice(0, 120), d))", iso)
        return bool(shown)
    except Exception:
        return False


# ---- dropdowns and autocompletes ----------------------------------------------------------------------------------

def choose_option(page: Any, field: Any, text: str, *, timeout: int = 10000,
                  pointer: Callable[[Any, str], None] | None = None) -> dict[str, Any]:
    """Choose ``text`` in a native or custom dropdown; verify the field then shows it."""
    ensure(page)
    info = classify(field)
    kind = info.get("kind")
    if kind in ("native_select", "native_multiselect"):
        try:
            field.select_option(label=text, timeout=timeout)
        except Exception:
            try:
                field.select_option(value=text, timeout=timeout)
            except Exception:
                opts = field.evaluate("e => [...e.options].map(o => o.text.trim())")
                hits = [o for o in opts if o.lower() == text.lower()] or [o for o in opts if text.lower() in o.lower()]
                if len(hits) != 1:
                    raise ComponentError("OPTION_NOT_FOUND" if not hits else "OPTION_AMBIGUOUS",
                                         f"'{text}' is not one option of this list.", choices=opts[:20])
                field.select_option(label=hits[0], timeout=timeout)
        shown = field.evaluate("el => window.__sgComp.fieldText(el)")
        return {"how": "native", "chosen": shown, "verified": text.lower() in str(shown).lower()}
    # custom dropdown: open it (unless already open), type to filter when it is searchable, click the option
    res = page.evaluate("([t, f]) => window.__sgComp.findOption(t, f)", [text, None])
    if res.get("status") != "found":
        if pointer:
            pointer(field, "Open the list")
        field.click(timeout=timeout)
        settle(page, 250, 3000)
        res = _find_option(page, field, text)
        if res.get("status") == "none" and kind in ("autocomplete", "combobox", "textbox"):
            field.fill(text, timeout=timeout)
            settle(page, 300, 4000)
            res = _find_option(page, field, text)
    if res.get("status") == "ambiguous":
        raise ComponentError("OPTION_AMBIGUOUS", f"Several options match '{text}'.", choices=res.get("options") or [])
    if res.get("status") != "found":
        raise ComponentError("OPTION_NOT_FOUND", f"'{text}' is not in the list"
                             + (f" (options: {', '.join(res.get('options')[:6])})" if res.get("options") else "") + ".",
                             choices=res.get("options") or [])
    chosen = res.get("text") or text
    _click_marked(page, timeout, pointer, chosen[:40])
    settle(page, 250, 3000)
    return {"how": "custom", "chosen": chosen, "verified": _shows_choice(field, text, chosen)}


def _find_option(page: Any, field: Any, text: str) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for attempt in range(16):  # options can load asynchronously
        res = field.evaluate("(el, t) => window.__sgComp.findOption(t, el)", text) or {}
        if res.get("status") in ("found", "ambiguous"):
            return res
        if res.get("status") == "none" and res.get("count") and attempt >= 4 and \
                not page.evaluate("() => window.__sgComp.busy()"):
            return res  # the list is there, has finished loading, and does not have it
        page.wait_for_timeout(250)
    return res


def _shows_choice(field: Any, wanted: str, chosen: str) -> bool:
    try:
        near = str(field.evaluate("el => window.__sgComp.shownNear(el)")).lower()
    except Exception:
        return False
    first = chosen.split("\n")[0].strip().lower()
    return bool(wanted.lower() in near or (first and first in near))


def commit_autocomplete(page: Any, field: Any, value: str, *, timeout: int = 4000,
                        pointer: Callable[[Any, str], None] | None = None) -> dict[str, Any] | None:
    """After a value was typed into an autocomplete: if a suggestion list opened, choose the suggestion that is the
    typed value. Returns None when there is no suggestion list (a plain text field), {"chosen"} when one was chosen,
    or raises OPTION_AMBIGUOUS when several suggestions fit (never a guess)."""
    if not value:
        return None
    ensure(page)
    kind = classify(field).get("kind")
    if kind not in ("autocomplete", "combobox", "textbox", "date_field"):
        return None
    settle(page, 300, timeout)
    res = field.evaluate("(el, t) => window.__sgComp.findOption(t, el)", value) or {}
    if res.get("status") == "none" and not res.get("count") and kind in ("autocomplete", "combobox"):
        page.wait_for_timeout(600)  # suggestions loaded from the server
        res = field.evaluate("(el, t) => window.__sgComp.findOption(t, el)", value) or {}
    if res.get("status") == "found":
        chosen = res.get("text") or value
        _click_marked(page, timeout, pointer, chosen[:40])
        settle(page, 300, 3000)
        return {"chosen": chosen, "tier": res.get("tier")}
    if res.get("status") == "ambiguous" and kind in ("autocomplete", "combobox"):
        raise ComponentError("OPTION_AMBIGUOUS", f"Several suggestions match '{value}'.", choices=res.get("options") or [])
    return None


def dismiss(page: Any) -> str:
    """Close an open popup the way "clicking on the page background" meant to: Escape, then move focus away."""
    try:
        before = page.evaluate("() => window.__sgComp ? window.__sgComp.overlays() : null")
        page.keyboard.press("Escape")
        page.evaluate("() => { const a = document.activeElement; if (a && a !== document.body && a.blur) a.blur(); }")
        settle(page, 200, 1500)
        after = page.evaluate("() => window.__sgComp ? window.__sgComp.overlays() : null")
        return "closed" if before and after and (after["popups"] < before["popups"]) else "no popup"
    except Exception:
        return "page changed"


def is_container(locator: Any) -> bool:
    try:
        return bool(locator.evaluate("el => window.__sgComp ? window.__sgComp.isContainer(el) : false"))
    except Exception:
        return False
