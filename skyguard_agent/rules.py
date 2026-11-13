"""Deterministic business-rule engine.

Rules are learned (guided learning, discovery, the user, or a model suggestion) and stored as knowledge. Only
*approved* rules block an operation; rules still waiting for approval are reported as warnings. The engine knows
nothing about transport: "a driver cannot have overlapping trips" is a generic ``no_overlap`` rule on whatever
resource field the application uses.

Rule types
  required        {"field"}
  pattern         {"field", "regex"}
  allowed_values  {"field", "values": [...]}    (or "source": "options": the options learned for the field)
  date            {"field", "format"?, "min_days"?, "max_days"?}         e.g. planned trips must be in the future
  time            {"field"}
  range           {"field", "min"?, "max"?}
  unique          {"field"}                       within the batch and against keys the application already has
  no_overlap      {"resource", "date", "time"?, "duration_minutes"?, "end_time"?}
                  one resource (driver, vehicle) cannot be booked twice at overlapping times
  depends         {"field", "when_field", "when_value"}   field required when another has a value
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Iterable

from .util import norm

DATE_FORMATS = {"YYYY-MM-DD": "%Y-%m-%d", "DD/MM/YYYY": "%d/%m/%Y", "MM/DD/YYYY": "%m/%d/%Y",
                "DD-MM-YYYY": "%d-%m-%Y", "DD.MM.YYYY": "%d.%m.%Y", "YYYY/MM/DD": "%Y/%m/%d"}
RULE_TYPES = ("required", "pattern", "allowed_values", "date", "time", "range", "unique", "no_overlap", "depends")


def parse_date(value: Any, fmt: str | None = None) -> _dt.date | None:
    text = str(value or "").strip()
    if not text:
        return None
    formats = [DATE_FORMATS.get(fmt or "", fmt)] if fmt else list(DATE_FORMATS.values())
    for f in formats:
        if not f:
            continue
        try:
            return _dt.datetime.strptime(text, f).date()
        except ValueError:
            continue
    return None


def parse_time(value: Any) -> _dt.time | None:
    text = str(value or "").strip().upper()
    for f in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p"):
        try:
            return _dt.datetime.strptime(text, f).time()
        except ValueError:
            continue
    return None


def validate_rule(rule: dict[str, Any]) -> list[str]:
    """Problems with a rule definition itself (checked before a rule can be approved)."""
    problems = []
    t = rule.get("type")
    if t not in RULE_TYPES:
        return [f"Unknown rule type {t!r}."]
    need = {"no_overlap": ("resource", "date"), "depends": ("field", "when_field")}.get(t, ("field",))
    problems += [f"The rule needs '{k}'." for k in need if not rule.get(k)]
    if t == "pattern":
        try:
            re.compile(str(rule.get("regex") or ""))
        except re.error as exc:
            problems.append(f"The pattern is not a valid regular expression: {exc}.")
        if len(str(rule.get("regex") or "")) > 300:
            problems.append("The pattern is too long.")
    return problems


def _finding(rule: dict[str, Any], field: str | None, message: str, enforced: bool) -> dict[str, Any]:
    severity = (rule.get("severity") or "error") if enforced else "warning"
    return {"rule": rule.get("name") or rule.get("type"), "type": rule.get("type"), "field": field,
            "severity": severity, "message": rule.get("message") or message, "enforced": enforced}


def check_record(record: dict[str, Any], rules: Iterable[tuple[dict[str, Any], bool]], *,
                 today: _dt.date | None = None, options: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    """Single-record rules. ``rules`` holds (rule, enforced) pairs."""
    today = today or _dt.date.today()
    out: list[dict[str, Any]] = []
    for rule, enforced in rules:
        t = rule.get("type")
        f = rule.get("field")
        v = record.get(f) if f else None
        empty = v in (None, "") or (isinstance(v, str) and not v.strip())
        if t == "required" and empty:
            out.append(_finding(rule, f, f"{f} is required.", enforced))
        elif t == "depends":
            other = record.get(rule.get("when_field"))
            want = rule.get("when_value")
            if empty and other not in (None, "") and (want is None or norm(other) == norm(want)):
                out.append(_finding(rule, f, f"{f} is required when {rule['when_field']} is set.", enforced))
        elif empty:
            continue
        elif t == "pattern":
            try:
                if not re.fullmatch(str(rule.get("regex")), str(v)):
                    out.append(_finding(rule, f, f"{f} '{v}' does not have the expected format.", enforced))
            except re.error:
                out.append(_finding(rule, f, "The format rule is invalid.", False))
        elif t == "allowed_values":
            allowed = rule.get("values") or (options or {}).get(f) or []
            if allowed and norm(v) not in {norm(a) for a in allowed}:
                out.append(_finding(rule, f, f"{f} '{v}' is not one of the values the application offers.", enforced))
        elif t == "date":
            d = parse_date(v, rule.get("format"))
            if not d:
                out.append(_finding(rule, f, f"{f} '{v}' is not a valid date.", enforced))
                continue
            if rule.get("min_days") is not None and d < today + _dt.timedelta(days=int(rule["min_days"])):
                out.append(_finding(rule, f, f"{f} {v} is too early (must be on or after "
                                             f"{today + _dt.timedelta(days=int(rule['min_days']))}).", enforced))
            if rule.get("max_days") is not None and d > today + _dt.timedelta(days=int(rule["max_days"])):
                out.append(_finding(rule, f, f"{f} {v} is too far ahead.", enforced))
        elif t == "time":
            if not parse_time(v):
                out.append(_finding(rule, f, f"{f} '{v}' is not a valid time (HH:MM).", enforced))
        elif t == "range":
            try:
                num = float(v)
            except (TypeError, ValueError):
                out.append(_finding(rule, f, f"{f} '{v}' is not a number.", enforced))
                continue
            if rule.get("min") is not None and num < float(rule["min"]):
                out.append(_finding(rule, f, f"{f} {v} is below {rule['min']}.", enforced))
            if rule.get("max") is not None and num > float(rule["max"]):
                out.append(_finding(rule, f, f"{f} {v} is above {rule['max']}.", enforced))
    return out


def _interval(record: dict[str, Any], rule: dict[str, Any]) -> tuple[_dt.datetime, _dt.datetime] | None:
    d = parse_date(record.get(rule.get("date")), rule.get("format"))
    if not d:
        return None
    t = parse_time(record.get(rule.get("time"))) if rule.get("time") else _dt.time(0, 0)
    if t is None:
        return None
    start = _dt.datetime.combine(d, t)
    if rule.get("end_time") and parse_time(record.get(rule["end_time"])):
        end = _dt.datetime.combine(d, parse_time(record.get(rule["end_time"])))  # type: ignore[arg-type]
        if end <= start:
            end += _dt.timedelta(days=1)
    else:
        end = start + _dt.timedelta(minutes=int(rule.get("duration_minutes") or 60))
    return start, end


def check_batch(records: list[dict[str, Any]], rules: list[tuple[dict[str, Any], bool]], *,
                existing_keys: dict[str, set[str]] | None = None,
                existing_bookings: list[dict[str, Any]] | None = None,
                done: set[int] | None = None, today: _dt.date | None = None,
                options: dict[str, list[str]] | None = None) -> dict[int, list[dict[str, Any]]]:
    """Findings per record index for the whole batch (cross-record rules included).

    existing_keys: field -> values the application already has (from its own list screen).
    existing_bookings: records already in the application (for no_overlap), each with the rule's fields.
    done: indexes already committed (they count as existing for the others, never re-flagged themselves).
    """
    existing_keys = existing_keys or {}
    done = done or set()
    findings = {i: check_record(r, [(ru, en) for ru, en in rules if ru.get("type") not in ("unique", "no_overlap")],
                                today=today, options=options) for i, r in enumerate(records)}
    for rule, enforced in rules:
        if rule.get("type") == "unique":
            f = rule.get("field")
            seen: dict[str, int] = {}
            taken = {norm(x) for x in existing_keys.get(f, set())}
            for i, r in enumerate(records):
                v = norm(r.get(f))
                if not v:
                    continue
                if i not in done and v in taken:
                    findings[i].append(_finding(rule, f, f"{f} {r.get(f)} already exists in the application.",
                                                enforced))
                elif v in seen and i not in done:
                    findings[i].append(_finding(rule, f, f"{f} {r.get(f)} is used twice in this batch (record "
                                                         f"{seen[v] + 1}).", enforced))
                seen.setdefault(v, i)
        elif rule.get("type") == "no_overlap":
            res = rule.get("resource")
            booked: list[tuple[str, _dt.datetime, _dt.datetime, str]] = []
            for b in existing_bookings or []:
                iv = _interval(b, rule)
                if iv and b.get(res):
                    booked.append((norm(b.get(res)), iv[0], iv[1], "the application"))
            order = sorted(range(len(records)), key=lambda i: (i not in done, i))
            for i in order:
                r = records[i]
                iv = _interval(r, rule)
                who = norm(r.get(res))
                if not iv or not who:
                    continue
                clash = next((b for b in booked if b[0] == who and iv[0] < b[2] and b[1] < iv[1]), None)
                if clash and i not in done:
                    findings[i].append(_finding(rule, res, f"{res} {r.get(res)} is already assigned from "
                                                           f"{clash[1]:%Y-%m-%d %H:%M} to {clash[2]:%H:%M} "
                                                           f"({clash[3]}).", enforced))
                else:
                    booked.append((who, iv[0], iv[1], f"record {i + 1} of this batch"))
    return findings


def blocking(findings: list[dict[str, Any]]) -> bool:
    return any(f["severity"] == "error" and f["enforced"] for f in findings)
