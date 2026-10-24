"""Runtime value rules for test data, resolved from the Windows system clock when a test runs.

Recorded dates are never stored as literals when the test expects a relative date:

    {{TODAY}}                      current system date (format %Y-%m-%d)
    {{TODAY+7d:%d/%m/%Y}}          today + 7 days, any strftime format
    {{TODAY-1d}}                   today - 1 day
    {{NOW:%H:%M}}                  current system time
    {{NOW+2h:%H:%M}}               current time + 2 hours
    {{NOW+2h+30min:%H:%M}}         current time + 2 hours 30 minutes
    {{TODAY+1m}} / {{TODAY+1y}}    + 1 month / + 1 year (end of month kept valid)
    {{RANDOM:6}}                   6 random digits
    {{UNIQUE}}                     a token unique to this run
    {{DATA:field}}                 another field of the same combination

Units: d days, w weeks, m months, y years, h hours, min minutes.
"""

from __future__ import annotations

import calendar
import random
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable

TOKEN = re.compile(r"\{\{\s*([A-Z]+)((?:[+-]\d+(?:min|[dwmyh]))*)(?::([^}]*))?\s*\}\}")
OFFSET = re.compile(r"([+-]\d+)(min|[dwmyh])")
UNIQUE = uuid.uuid4().hex[:8]


def _add_months(base: datetime, months: int) -> datetime:
    month = base.month - 1 + months
    year = base.year + month // 12
    month = month % 12 + 1
    return base.replace(year=year, month=month, day=min(base.day, calendar.monthrange(year, month)[1]))


def shift(base: datetime, offsets: str) -> datetime:
    for amount_text, unit in OFFSET.findall(offsets or ""):
        amount = int(amount_text)
        if unit == "d":
            base = base + timedelta(days=amount)
        elif unit == "w":
            base = base + timedelta(weeks=amount)
        elif unit == "h":
            base = base + timedelta(hours=amount)
        elif unit == "min":
            base = base + timedelta(minutes=amount)
        elif unit == "m":
            base = _add_months(base, amount)
        elif unit == "y":
            base = _add_months(base, amount * 12)
    return base


def resolve(value: Any, data: dict[str, Any] | None = None, clock: Callable[[], datetime] = datetime.now) -> Any:
    """Resolve the rules in one value; values that are not text are returned unchanged."""
    if not isinstance(value, str) or "{{" not in value:
        return value
    data = data or {}

    def one(m: re.Match[str]) -> str:
        kind, offsets, arg = m.group(1), m.group(2), m.group(3)
        if kind in ("TODAY", "DATE"):
            return shift(clock(), offsets).strftime(arg or "%Y-%m-%d")
        if kind == "NOW":
            return shift(clock(), offsets).strftime(arg or "%Y-%m-%d %H:%M")
        if kind == "RANDOM":
            n = int(arg) if arg and arg.isdigit() else 6
            return "".join(random.choice("0123456789") for _ in range(max(1, min(n, 32))))
        if kind == "UNIQUE":
            return UNIQUE
        if kind == "DATA":
            if arg not in data:
                raise KeyError(f"Test data field '{arg}' is not in this combination.")
            return str(resolve(data[arg], {k: v for k, v in data.items() if k != arg}, clock))
        return m.group(0)

    return TOKEN.sub(one, value)


def resolve_all(values: dict[str, Any], clock: Callable[[], datetime] = datetime.now) -> dict[str, Any]:
    return {key: resolve(value, values, clock) for key, value in values.items()}
