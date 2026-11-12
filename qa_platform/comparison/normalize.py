"""Data Normalization and Transformation Engine (deterministic, traceable).

Every value keeps its original text; ``normalize`` returns the normalized value and the list of steps that produced
it, so a report can say exactly why "AED 15,000.00" and "15000" were found equal. Two values are never treated as
equal because they *look* similar: equality is decided only on normalized values of the declared type, with the
configured tolerance, or through an approved reference mapping (for example 1 = Active).

Rules (per project, overridable per field):
  type            text | number | currency | percent | date | datetime | boolean | code
  date_formats    accepted input formats, tried in order (day-first by default: 12/11/2026 = 12 November 2026)
  day_first       True (default) or False for month-first sources
  timezone        +04:00 style offset of the source; datetimes are compared in UTC when both sides have one
  decimals        rounding before comparing numbers (None = exact)
  decimal_comma   True when the source writes 15.000,50
  tolerance       absolute numeric tolerance (default 0)
  tolerance_pct   relative numeric tolerance in percent
  date_tolerance_days
  case_sensitive  False by default for text
  null_values     texts that mean "no value" ("", "-", "N/A", "null", "—")
  reference       {source value: target value} approved code/description pairs
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

NULLS = {"", "-", "--", "—", "n/a", "na", "null", "none", "nil", "(blank)", "(empty)"}
CURRENCY = re.compile(r"(?i)\b(aed|usd|eur|gbp|sar|qar|kwd|omr|bhd|inr|egp|jod|dhs?|dirhams?)\b|[$€£₹¥]|د\.إ")
DATE_FORMATS_DAY_FIRST = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d-%b-%Y", "%d %b %Y", "%d-%B-%Y",
                          "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%Y/%m/%d", "%d/%m/%y", "%d-%b-%y", "%Y%m%d"]
DATE_FORMATS_MONTH_FIRST = ["%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%d %b %Y",
                            "%Y/%m/%d", "%m/%d/%y", "%Y%m%d"]
TIME_SUFFIXES = ["", " %H:%M", " %H:%M:%S", " %I:%M %p", " %I:%M:%S %p", "T%H:%M", "T%H:%M:%S", "T%H:%M:%S.%f"]
TRUE = {"true", "yes", "y", "1", "active", "enabled", "on", "checked", "✓", "✔"}
FALSE = {"false", "no", "n", "0", "inactive", "disabled", "off", "unchecked", "✗", "✘"}


def clean_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKC", text).replace(" ", " ").replace("​", "")
    return re.sub(r"\s+", " ", text).strip()


def is_null(value: Any, rules: dict[str, Any] | None = None) -> bool:
    if value is None:
        return True
    nulls = {n.lower() for n in (rules or {}).get("null_values", [])} or NULLS
    return clean_text(value).lower() in nulls


def parse_number(text: str, decimal_comma: bool = False) -> Decimal | None:
    t = clean_text(text)
    neg = t.startswith("(") and t.endswith(")")  # (1,234.00) accounting negative
    t = CURRENCY.sub("", t).replace("%", "").strip().strip("()").replace(" ", "")
    if t.endswith("-"):
        neg, t = True, t[:-1]
    if decimal_comma:
        t = t.replace(".", "").replace(",", ".")
    else:
        if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", t) or re.fullmatch(r"-?\d+(\.\d+)?", t):
            t = t.replace(",", "")
        elif re.fullmatch(r"-?\d+,\d{1,2}", t):  # 15000,50 with no thousands: a decimal comma
            t = t.replace(",", ".")
        else:
            t = t.replace(",", "")
    if t.startswith("+"):
        t = t[1:]
    if not re.fullmatch(r"-?\d+(\.\d+)?([eE][-+]?\d+)?", t):
        return None
    try:
        d = Decimal(t)
    except InvalidOperation:
        return None
    return -d if neg else d


def parse_datetime(text: str, rules: dict[str, Any] | None = None) -> tuple[datetime | date, bool] | None:
    """(value, has_time). Day-first unless rules say otherwise; ISO with an offset keeps its offset."""
    rules = rules or {}
    t = clean_text(text)
    if not t:
        return None
    iso = t.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        return (dt, True) if ("T" in t or ":" in t) else (dt.date(), False)
    except ValueError:
        pass
    formats = rules.get("date_formats") or (DATE_FORMATS_DAY_FIRST if rules.get("day_first", True)
                                            else DATE_FORMATS_MONTH_FIRST)
    for base in formats:
        for suffix in TIME_SUFFIXES:
            try:
                dt = datetime.strptime(t, base + suffix)
            except ValueError:
                continue
            return (dt, True) if suffix else (dt.date(), False)
    return None


def _offset(text: str | None) -> timezone | None:
    if not text:
        return None
    m = re.fullmatch(r"(?:UTC|GMT)?\s*([+-])(\d{1,2}):?(\d{2})?", text.strip(), re.I)
    if not m:
        return None
    delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
    return timezone(delta if m.group(1) == "+" else -delta)


def infer_type(values: list[Any]) -> str:
    """The narrowest type every non-empty sample fits (text when they disagree)."""
    samples = [clean_text(v) for v in values if not is_null(v)][:200]
    if not samples:
        return "text"
    if all(re.search(r"%\s*$", s) and parse_number(s) is not None for s in samples):
        return "percent"
    if all(parse_number(s) is not None for s in samples):
        if any(CURRENCY.search(s) for s in samples):
            return "currency"
        return "number"
    if all(CURRENCY.search(s) and parse_number(s) is not None for s in samples):
        return "currency"
    if all(parse_datetime(s) is not None for s in samples) and not all(re.fullmatch(r"\d+", s) for s in samples):
        return "datetime" if any(parse_datetime(s)[1] for s in samples) else "date"
    low = {s.lower() for s in samples}
    if low <= (TRUE | FALSE) and len(low) <= 2 and not low <= {"0", "1"}:
        return "boolean"
    return "text"


def normalize(value: Any, ftype: str = "text", rules: dict[str, Any] | None = None) -> tuple[Any, list[str]]:
    """(normalized value, steps). None means "no value". Unparseable input stays text and says so."""
    rules = rules or {}
    steps: list[str] = []
    original = "" if value is None else str(value)
    if is_null(value, rules):
        return None, [f"'{original}' is an empty / null value"] if original.strip() else ["empty"]
    text = clean_text(value)
    if text != original:
        steps.append("trimmed spaces / normalized Unicode")
    ref = rules.get("reference") or {}
    if ref:
        hit = ref.get(text) if text in ref else next((v for k, v in ref.items() if str(k).lower() == text.lower()), None)
        if hit is not None:
            steps.append(f"approved reference mapping '{text}' -> '{hit}'")
            text = clean_text(hit)
    if ftype in ("number", "currency", "percent"):
        num = parse_number(text, bool(rules.get("decimal_comma")))
        if num is None:
            steps.append(f"'{text}' is not a number; compared as text")
            return text, steps
        if CURRENCY.search(text):
            steps.append(f"removed currency '{CURRENCY.search(text).group(0)}'")
        if "," in text and not rules.get("decimal_comma"):
            steps.append("removed thousands separators")
        if ftype == "percent" and "%" in text:
            steps.append("removed % sign (value kept in percent points)")
        if rules.get("decimals") is not None:
            q = Decimal(1).scaleb(-int(rules["decimals"]))
            rounded = num.quantize(q, rounding=ROUND_HALF_UP)
            if rounded != num:
                steps.append(f"rounded to {rules['decimals']} decimals")
            num = rounded
        return (Decimal(int(num)) if num == num.to_integral() else num.normalize()), steps
    if ftype in ("date", "datetime"):
        parsed = parse_datetime(text, rules)
        if parsed is None:
            steps.append(f"'{text}' is not a recognised date; compared as text")
            return text, steps
        dt, has_time = parsed
        if isinstance(dt, datetime):
            tz = _offset(rules.get("timezone"))
            if dt.tzinfo is None and tz is not None:
                dt = dt.replace(tzinfo=tz)
                steps.append(f"source time zone {rules.get('timezone')}")
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc)
                steps.append("converted to UTC")
            if ftype == "date":
                steps.append("date part only")
                return dt.date().isoformat(), steps
            return dt.isoformat(), steps
        steps.append(f"parsed date '{text}' -> {dt.isoformat()}")
        return dt.isoformat(), steps
    if ftype == "boolean":
        low = text.lower()
        if low in TRUE:
            steps.append(f"'{text}' means true")
            return True, steps
        if low in FALSE:
            steps.append(f"'{text}' means false")
            return False, steps
        return text, steps + [f"'{text}' is not a yes/no value; compared as text"]
    if not rules.get("case_sensitive", False):
        if text != text.lower():
            steps.append("case ignored")
        text = text.lower()
    return text, steps


def equal(a: Any, b: Any, ftype: str, rules: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Compare two normalized values. (equal, how) where how explains a tolerance that was used."""
    rules = rules or {}
    if a is None and b is None:
        return True, "both empty"
    if a is None or b is None:
        return False, "one side is empty"
    if isinstance(a, Decimal) and isinstance(b, Decimal):
        diff = abs(a - b)
        if diff == 0:
            return True, "equal"
        tol = Decimal(str(rules.get("tolerance") or 0))
        if tol and diff <= tol:
            return True, f"within tolerance {tol}"
        pct = rules.get("tolerance_pct")
        if pct and max(abs(a), abs(b)) and diff / max(abs(a), abs(b)) * 100 <= Decimal(str(pct)):
            return True, f"within {pct}%"
        return False, f"differs by {a - b}"
    if ftype in ("date", "datetime") and isinstance(a, str) and isinstance(b, str) and rules.get("date_tolerance_days"):
        try:
            da, db = datetime.fromisoformat(a), datetime.fromisoformat(b)
            days = abs((da - db).total_seconds()) / 86400
            if days <= float(rules["date_tolerance_days"]):
                return True, f"within {rules['date_tolerance_days']} day(s)"
            return False, f"{days:.2f} days apart"
        except ValueError:
            pass
    return (a == b), ("equal" if a == b else "different")


def to_json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    return value
