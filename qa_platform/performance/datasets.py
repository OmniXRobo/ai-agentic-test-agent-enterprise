"""23.3.25: test data for performance scripts. A data set is a table (named, typed columns and rows of text) that a
script reads with a JMeter CSV Data Set Config. Values are always kept as text, exactly as entered or imported, so
leading zeros ("00123"), Unicode and spaces are never changed. Columns of type "secret" (passwords, tokens) are
kept in the Windows Credential Manager, never in the database, exports or run results.

This module is pure: parsing, type checks, CSV writing and the row generator. Storage is in the service."""

from __future__ import annotations

import csv
import io
import random
import re
import uuid
from datetime import date, timedelta
from typing import Any

from ..errors import QAError

TYPES = {
    "text": "Text (kept exactly, also leading zeros)",
    "integer": "Whole number",
    "number": "Number",
    "email": "Email address",
    "date": "Date (YYYY-MM-DD)",
    "boolean": "true / false",
    "secret": "Secret (password, token: kept in the Credential Manager)",
}
MAX_ROWS = 100_000
MAX_COLUMNS = 60
MAX_VALUE = 4000
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,59}$")
SECRET_HINT = re.compile(r"pass(word)?|pwd|secret|token|api[_-]?key|pin$", re.I)
_INT = re.compile(r"^[+-]?\d+$")
_NUM = re.compile(r"^[+-]?(\d+([.]\d*)?|[.]\d+)([eE][+-]?\d+)?$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BOOL = {"true", "false"}


def _err(message: str, code: str = "DATASET_INVALID", status: int = 422, details: Any = None) -> QAError:
    return QAError(code, message, status=status, details=details)


def check_columns(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clean column definitions: [{name, type}]. Names become JMeter variable names, so they must be usable as one."""
    if not columns:
        raise _err("A data set needs at least one column.")
    if len(columns) > MAX_COLUMNS:
        raise _err(f"A data set can have at most {MAX_COLUMNS} columns.")
    out, seen = [], set()
    for c in columns:
        name = str((c or {}).get("name") or "").strip()
        if not NAME.match(name):
            raise _err(f"The column name \"{name}\" cannot be used as a JMeter variable. Use letters, digits and _ "
                       "(starting with a letter or _), at most 60 characters.")
        if name.lower() in seen:
            raise _err(f"The column \"{name}\" appears twice.")
        seen.add(name.lower())
        ctype = str(c.get("type") or "text")
        if ctype not in TYPES:
            raise _err(f"Unknown column type {ctype} for \"{name}\".")
        out.append({"name": name, "type": ctype})
    return out


def type_ok(ctype: str, value: str) -> bool:
    if value == "":
        return True  # empty cells are reported separately
    if ctype == "integer":
        return bool(_INT.match(value))
    if ctype == "number":
        return bool(_NUM.match(value))
    if ctype == "email":
        return bool(_EMAIL.match(value))
    if ctype == "date":
        if not _DATE.match(value):
            return False
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True
    if ctype == "boolean":
        return value.lower() in _BOOL
    return True


def infer_type(name: str, values: list[str]) -> tuple[str, str | None]:
    """The column type that fits all values, and a note (e.g. leading zeros kept as text)."""
    if SECRET_HINT.search(name):
        return "secret", "Looks like a password or token, so it is kept as a secret."
    vals = [v for v in values if v != ""]
    if not vals:
        return "text", None
    if all(_INT.match(v) for v in vals):
        if any(len(v.lstrip("+-")) > 1 and v.lstrip("+-").startswith("0") for v in vals):
            return "text", "Has leading zeros, so it is kept as text (00123 stays 00123)."
        return "integer", None
    for t in ("number", "boolean", "date", "email"):
        if all(type_ok(t, v) for v in vals):
            if t == "number" and any(v.lstrip("+-").startswith("0") and len(v.split(".")[0].lstrip("+-")) > 1
                                     for v in vals):
                return "text", "Has leading zeros, so it is kept as text."
            return t, None
    return "text", None


def _decode(data: bytes) -> tuple[str, str]:
    for enc, label in (("utf-8-sig", "UTF-8"), ("utf-16", "UTF-16")):
        try:
            if enc == "utf-16" and not data[:2] in (b"\xff\xfe", b"\xfe\xff"):
                continue
            return data.decode(enc), label
        except UnicodeDecodeError:
            continue
    return data.decode("cp1252", "replace"), "Windows-1252 (the file was not UTF-8: check accented letters)"


def parse_csv(data: bytes | str, *, delimiter: str | None = None) -> dict[str, Any]:
    """Columns (with inferred types) and rows from a CSV file with a header line. Every value stays text."""
    if isinstance(data, bytes):
        text, encoding = _decode(data)
    else:
        text, encoding = data.lstrip("﻿"), "UTF-8"
    if not text.strip():
        raise _err("The file is empty.")
    sample = text[:20000]
    if delimiter is None:
        first = sample.splitlines()[0] if sample.splitlines() else ""
        counts = {d: first.count(d) for d in (",", ";", "\t", "|")}
        delimiter = max(counts, key=lambda d: counts[d]) if max(counts.values()) else ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        table = [row for row in reader]
    except csv.Error as exc:
        raise _err(f"The file could not be read as CSV: {exc}") from exc
    while table and not any(c.strip() for c in table[-1]):
        table.pop()  # trailing empty lines
    if not table:
        raise _err("The file is empty.")
    header = [h.strip() for h in table[0]]
    body = table[1:]
    if len(body) > MAX_ROWS:
        raise _err(f"The file has {len(body)} rows; at most {MAX_ROWS} are supported.")
    bad = [i + 2 for i, r in enumerate(body) if len(r) != len(header) and any(c.strip() for c in r)]
    if bad:
        raise _err(f"These lines do not have {len(header)} values like the header: "
                   f"{', '.join(map(str, bad[:10]))}{' …' if len(bad) > 10 else ''}.")
    rows = [list(r) for r in body if len(r) == len(header)]
    notes, columns = [], []
    for i, h in enumerate(header):
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", h) or f"column{i + 1}"
        if not re.match(r"^[A-Za-z_]", name):
            name = "_" + name
        if name != h:
            notes.append(f"Column \"{h}\" is named {name} (a JMeter variable name).")
        ctype, note = infer_type(name, [r[i] for r in rows])
        if note:
            notes.append(f"{name}: {note}")
        columns.append({"name": name[:60], "type": ctype})
    columns = check_columns(columns)
    return {"columns": columns, "rows": rows, "encoding": encoding,
            "delimiter": {"\t": "tab"}.get(delimiter, delimiter), "notes": notes}


def validate(columns: list[dict[str, Any]], rows: list[list[str]]) -> list[dict[str, Any]]:
    """Problems with the values: wrong type, line breaks, too long. Secret cells are checked without showing them."""
    out = []
    for ci, c in enumerate(columns):
        wrong, breaks, long, empty = [], [], [], []
        for ri, r in enumerate(rows):
            v = r[ci] if ci < len(r) else ""
            if v is None:  # a secret cell without a saved value
                empty.append(ri + 1)
                continue
            if not type_ok(c["type"], v):
                wrong.append(ri + 1)
            if "\n" in v or "\r" in v:
                breaks.append(ri + 1)
            if len(v) > MAX_VALUE:
                long.append(ri + 1)
            if v == "":
                empty.append(ri + 1)
        rows_txt = lambda xs: ", ".join(map(str, xs[:8])) + (" …" if len(xs) > 8 else "")  # noqa: E731
        if wrong:
            out.append({"column": c["name"], "level": "FAIL", "rows": wrong[:50],
                        "message": f"{c['name']}: {len(wrong)} value(s) are not {TYPES[c['type']].split(' (')[0].lower()}"
                                   f" (rows {rows_txt(wrong)})."})
        if breaks:
            out.append({"column": c["name"], "level": "FAIL", "rows": breaks[:50],
                        "message": f"{c['name']}: line breaks in rows {rows_txt(breaks)} (JMeter reads one row per "
                                   "line)."})
        if long:
            out.append({"column": c["name"], "level": "FAIL", "rows": long[:50],
                        "message": f"{c['name']}: values longer than {MAX_VALUE} characters in rows {rows_txt(long)}."})
        if empty:
            out.append({"column": c["name"], "level": "WARN", "rows": empty[:50],
                        "message": f"{c['name']}: empty in rows {rows_txt(empty)}"
                                   + (" (no secret value saved)." if c["type"] == "secret" else ".")})
    return out


def to_csv(columns: list[str], rows: list[list[str]]) -> str:
    """UTF-8 CSV text with a header line; quotes only where needed (JMeter reads it with quoted data on)."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(columns)
    w.writerows(rows)
    return buf.getvalue()


# ---- row generator (rules, no AI) ---------------------------------------------------------------------------------

RULES = {
    "sequence": "Prefix and a running number, e.g. user0001",
    "email": "Email address with a running number",
    "range": "Whole number between a minimum and a maximum",
    "decimal": "Number with 2 decimals between a minimum and a maximum",
    "list": "One of a list of values",
    "date": "Date between two dates",
    "uuid": "Random unique id",
    "fixed": "The same value in every row",
    "empty": "Leave empty (enter secrets yourself)",
}


def generate(columns: list[dict[str, Any]], rules: dict[str, dict[str, Any]], count: int, *, start: int = 1,
             seed: int | None = None) -> list[list[str]]:
    """Rows from a rule per column. The same seed gives the same rows."""
    if count < 1 or count > 10_000:
        raise _err("Generate between 1 and 10,000 rows at a time.")
    rnd = random.Random(seed)
    out = []
    for n in range(start, start + count):
        row = []
        for c in columns:
            r = rules.get(c["name"]) or {"rule": "empty" if c["type"] == "secret" else "sequence"}
            row.append(_value(r, c, n, rnd))
        out.append(row)
    return out


def _value(r: dict[str, Any], c: dict[str, Any], n: int, rnd: random.Random) -> str:
    rule = r.get("rule") or "sequence"
    if rule == "sequence":
        width = int(r.get("width") or 4)
        return f"{r.get('prefix', c['name'])}{str(n).zfill(max(1, min(width, 12)))}"
    if rule == "email":
        domain = str(r.get("domain") or "example.test").lstrip("@")
        return f"{r.get('prefix') or 'user'}{str(n).zfill(int(r.get('width') or 4))}@{domain}"
    if rule == "range":
        lo, hi = int(r.get("min") or 1), int(r.get("max") or 100)
        return str(rnd.randint(min(lo, hi), max(lo, hi)))
    if rule == "decimal":
        lo, hi = float(r.get("min") or 0), float(r.get("max") or 100)
        return f"{rnd.uniform(min(lo, hi), max(lo, hi)):.2f}"
    if rule == "list":
        items = [s.strip() for s in str(r.get("values") or "").split(",") if s.strip()]
        if not items:
            raise _err(f"{c['name']}: enter the values of the list, separated by commas.")
        return rnd.choice(items)
    if rule == "date":
        try:
            a = date.fromisoformat(str(r.get("from") or date.today().isoformat()))
            b = date.fromisoformat(str(r.get("to") or (a + timedelta(days=30)).isoformat()))
        except ValueError as exc:
            raise _err(f"{c['name']}: dates are written YYYY-MM-DD.") from exc
        a, b = min(a, b), max(a, b)
        return (a + timedelta(days=rnd.randint(0, (b - a).days))).isoformat()
    if rule == "uuid":
        return str(uuid.UUID(int=rnd.getrandbits(128), version=4))
    if rule == "fixed":
        return str(r.get("value") or "")
    if rule == "empty":
        return ""
    raise _err(f"Unknown rule {rule} for {c['name']}.")
