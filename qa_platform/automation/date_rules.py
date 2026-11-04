"""23.3.17: turn a recorded or generated date / time into a runtime rule, so the script stays valid on another day.

A value such as ``03/11/2026`` recorded on 3 Nov 2026 becomes ``{{TODAY:%d/%m/%Y}}`` (Current system date); a date
two days later becomes ``{{TODAY+2d:%d/%m/%Y}}``, a time such as ``10:30`` typed at 08:00 becomes
``{{NOW+2h+30min:%H:%M}}``. The format the person typed is kept. Dates more than a year away (a date of birth, a
contract start) stay as they were: they are rarely meant to move with the calendar, and Edit Script can still turn
them into a rule.

The rules are resolved from the system clock of the PC that runs the test, at the moment it runs (dsl.ValueResolver,
the Python export and the framework's utilities/runtime_values.py all read the same syntax).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Callable

from ..clock import now

MAX_DAYS = 366

DATE_FORMATS = ("%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y",
                "%d-%b-%Y", "%b %d, %Y", "%B %d, %Y", "%d %b, %Y")
DATETIME_FORMATS = ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M",
                    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %I:%M %p", "%m/%d/%Y %H:%M", "%d-%m-%Y %H:%M", "%d.%m.%Y %H:%M",
                    "%d-%b-%Y %H:%M", "%d %b %Y %H:%M")
TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p")

_HAS_YEAR = re.compile(r"(?<!\d)(19|20)\d\d(?!\d)")
_TIME_ONLY = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?\s*([AaPp][Mm])?$")
_RULE = re.compile(r"^\{\{\s*(TODAY|DATE|NOW)((?:\s*[+-]\s*\d+\s*(?:min|[dwmyh]))*)\s*(?::([^}]*))?\s*\}\}$")
_OFFSET = re.compile(r"([+-])\s*(\d+)\s*(min|[dwmyh])")
UNIT_WORDS = {"d": ("day", "days"), "w": ("week", "weeks"), "m": ("month", "months"), "y": ("year", "years"),
              "h": ("hour", "hours"), "min": ("minute", "minutes")}


def _parse(value: str, formats: tuple[str, ...]) -> tuple[datetime, str] | None:
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt), fmt
        except ValueError:
            continue
    return None


def _snap(minutes: int) -> int:
    """The clock moves while a person records: 1h59min or 2h01min was meant as 2 hours, 1 or 2 minutes as now."""
    hours = round(minutes / 60)
    return hours * 60 if abs(minutes - hours * 60) <= 2 else minutes


def _offsets(minutes: int, *, days_first: bool) -> str:
    sign = "+" if minutes >= 0 else "-"
    left = abs(minutes)
    parts = []
    if days_first and left >= 1440:
        parts.append(f"{sign}{left // 1440}d")
        left %= 1440
    if left >= 60:
        parts.append(f"{sign}{left // 60}h")
        left %= 60
    if left:
        parts.append(f"{sign}{left}min")
    return "".join(parts)


def format_from_placeholder(placeholder: str | None) -> str | None:
    """strftime format of a field from its placeholder: yyyy-dd-mm -> %Y-%d-%m, dd/mm/yyyy hh:mm -> %d/%m/%Y %H:%M.

    In "mm" after "hh:" the letters mean minutes; elsewhere months. None when the placeholder is not a format."""
    p = (placeholder or "").strip()
    if not re.fullmatch(r"(?i)[ydmhsa:/.\- ,]+", p) or not re.search(r"(?i)yy|dd|hh", p):
        return None
    out, i, low = "", 0, p.lower()
    while i < len(low):
        if low.startswith("yyyy", i):
            out, i = out + "%Y", i + 4
        elif low.startswith("yy", i):
            out, i = out + "%y", i + 2
        elif low.startswith("dd", i):
            out, i = out + "%d", i + 2
        elif low.startswith("hh", i):
            out, i = out + ("%I" if re.search(r"(?i)\b(a|am/pm|aa|tt)\b", p) else "%H"), i + 2
        elif low.startswith("mm", i):
            minutes = out.endswith(("%H:", "%I:"))
            out, i = out + ("%M" if minutes else "%m"), i + 2
        elif low.startswith("ss", i):
            out, i = out + "%S", i + 2
        elif low.startswith("am/pm", i):
            out, i = out + "%p", i + 5
        elif low.startswith("tt", i) or low.startswith("aa", i):
            out, i = out + "%p", i + 2
        elif low[i] == "a":
            out, i = out + "%p", i + 1
        else:
            out, i = out + p[i], i + 1
    return out if "%" in out else None


def detect(value: Any, clock: Callable[[], datetime] = now, placeholder: str | None = None) -> dict[str, str] | None:
    """The rule for a value that is a date, a date and time, or a time; None for anything else.

    ``placeholder`` (e.g. "yyyy-dd-mm") gives the field's own format and is tried first, so 2026-02-11 in an
    OrangeHRM field is 2 Nov, not 11 Feb. Returns {"rule", "label", "original"}."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.strip().split())
    if not text or len(text) > 40 or "{{" in text:
        return None
    current = clock().replace(second=0, microsecond=0, tzinfo=None)
    own = format_from_placeholder(placeholder)
    if own:
        try:
            parsed_own = datetime.strptime(text.upper() if "%p" in own else text, own)
        except ValueError:
            parsed_own = None
        if parsed_own is not None:
            has_date, has_time = any(k in own for k in ("%d", "%Y", "%y")), any(k in own for k in ("%H", "%I"))
            if has_date and not has_time:
                days = (parsed_own.date() - current.date()).days
                if abs(days) <= MAX_DAYS:
                    rule = "{{TODAY" + (f"{days:+d}d" if days else "") + f":{own}}}}}"
                    return {"rule": rule, "label": label(rule) or rule, "original": value}
                return None
            when = parsed_own if has_date else current.replace(hour=parsed_own.hour, minute=parsed_own.minute)
            minutes = int((when - current).total_seconds() // 60)
            if abs(minutes) > MAX_DAYS * 1440:
                return None
            minutes = _snap(minutes)
            rule = "{{NOW" + _offsets(minutes, days_first=has_date) + f":{own}}}}}"
            return {"rule": rule, "label": label(rule) or rule, "original": value}
    if _TIME_ONLY.match(text):
        parsed = _parse(text.upper(), TIME_FORMATS)
        if not parsed:
            return None
        t, fmt = parsed
        when = current.replace(hour=t.hour, minute=t.minute)
        minutes = int((when - current).total_seconds() // 60)
        minutes = _snap(minutes)
        rule = "{{NOW" + _offsets(minutes, days_first=False) + f":{fmt}}}}}"
        return {"rule": rule, "label": label(rule) or rule, "original": value}
    if not _HAS_YEAR.search(text):
        return None
    parsed = _parse(text, DATETIME_FORMATS)
    if parsed:
        dt, fmt = parsed
        minutes = int((dt - current).total_seconds() // 60)
        if abs(minutes) > MAX_DAYS * 1440:
            return None
        minutes = _snap(minutes)
        rule = "{{NOW" + _offsets(minutes, days_first=True) + f":{fmt}}}}}"
        return {"rule": rule, "label": label(rule) or rule, "original": value}
    parsed = _parse(text, DATE_FORMATS)
    if not parsed:
        return None
    d, fmt = parsed
    days = (d.date() - current.date()).days
    if abs(days) > MAX_DAYS:
        return None
    rule = "{{TODAY" + (f"{days:+d}d" if days else "") + f":{fmt}}}}}"
    return {"rule": rule, "label": label(rule) or rule, "original": value}


def label(rule: str | None) -> str | None:
    """'Current system date + 2 days (dd/mm/yyyy)' for {{TODAY+2d:%d/%m/%Y}}; None when it is not a date rule."""
    m = _RULE.match((rule or "").strip())
    if not m:
        return None
    kind, offsets, fmt = m.groups()
    base = "Current system time" if kind == "NOW" else "Current system date"
    if kind == "NOW" and fmt and any(k in fmt for k in ("%d", "%Y", "%m/")):
        base = "Current system date and time"
    parts = []
    for sign, n, unit in _OFFSET.findall(offsets or ""):
        one, many = UNIT_WORDS[unit]
        parts.append(f"{sign} {n} {one if n == '1' else many}")
    text = base + ("" if not parts else " " + " ".join(parts).replace("+ ", "+ ", 1))
    if fmt:
        text += f" ({pretty_format(fmt)})"
    return text


def pretty_format(fmt: str) -> str:
    return (fmt.replace("%d", "dd").replace("%m", "mm").replace("%Y", "yyyy").replace("%y", "yy")
            .replace("%H", "HH").replace("%I", "hh").replace("%M", "MM").replace("%S", "SS").replace("%p", "AM/PM")
            .replace("%b", "Mon").replace("%B", "Month"))


def is_rule(value: Any) -> bool:
    return isinstance(value, str) and bool(_RULE.match(value.strip()))


# Steps whose value is typed into the application (a check compares, so a fixed expected value stays).
VALUE_ACTIONS = {"fill", "type", "pick_date"}


def convert_steps(steps: list[dict[str, Any]], clock: Callable[[], datetime] = now) -> list[dict[str, Any]]:
    """Turn the fixed dates and times of typed values into rules, in place. Returns what changed."""
    changed = []
    for i, step in enumerate(steps):
        if step.get("action") not in VALUE_ACTIONS or step.get("original_value"):
            continue
        found = detect(step.get("value"), clock)
        if not found:
            continue
        step["original_value"] = step.get("value")
        step["value"] = found["rule"]
        changed.append({"index": i, **found})
    return changed


def suggestions(steps: list[Any], clock: Callable[[], datetime] = now) -> dict[int, dict[str, str]]:
    """Edit Script hints: steps with a fixed date or time that could be a rule (any action that types or checks)."""
    out: dict[int, dict[str, str]] = {}
    for i, step in enumerate(steps):
        action = step.get("action") if isinstance(step, dict) else step.action
        value = step.get("value") if isinstance(step, dict) else step.value
        if action in VALUE_ACTIONS | {"select", "expect_value", "expect_text"}:
            found = detect(value, clock)
            if found:
                out[i] = found
    return out


_DAY_CLICK = re.compile(r"^\s*click\s+(\d{1,2})\s*$", re.I)


def _day_of(step: Any) -> int | None:
    get = (lambda k: step.get(k)) if isinstance(step, dict) else (lambda k: getattr(step, k, None))
    if get("action") != "click":
        return None
    target = get("target") or {}
    text = target.get("text") if isinstance(target, dict) else getattr(target, "text", None)
    for candidate in (text, None):
        if candidate and re.fullmatch(r"\d{1,2}", candidate.strip()):
            n = int(candidate)
            return n if 1 <= n <= 31 else None
    m = _DAY_CLICK.match(get("description") or "")
    return int(m.group(1)) if m and 1 <= int(m.group(1)) <= 31 else None


def calendar_pairs(steps: list[Any]) -> list[dict[str, int]]:
    """23.3.18: older recordings of a date picker: a click that opened it, then "Click 2" (a day number).

    Edit Script offers to replace each pair with one pick_date step that has a rule."""
    out = []
    for i in range(len(steps) - 1):
        a, b = steps[i], steps[i + 1]
        action = a.get("action") if isinstance(a, dict) else a.action
        if action == "click" and _day_of(a) is None and _day_of(b) is not None:
            out.append({"open": i, "day": i + 1})
    return out


def convert_values(values: dict[str, Any], clock: Callable[[], datetime] = now) -> dict[str, dict[str, str]]:
    """Test data fields (AI-Driven combinations, imported rows): fixed dates and times become rules, in place."""
    changed: dict[str, dict[str, str]] = {}
    for key, value in list(values.items()):
        found = detect(value, clock)
        if found:
            values[key] = found["rule"]
            changed[key] = found
    return changed


def preview(rule: str, clock: Callable[[], datetime] = now) -> str:
    from .dsl import ValueResolver

    return ValueResolver(clock=clock).resolve(rule) or ""


__all__ = ["detect", "label", "convert_steps", "convert_values", "suggestions", "preview", "is_rule", "date",
           "timedelta"]
