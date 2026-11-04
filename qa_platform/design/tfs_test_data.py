"""23.3.13: the test case's "Test Data" tab in TFS / Azure DevOps.

A project can add a custom "Test Data" field (and a "Test Data ID" field) to its Test Case type and show them on a
"Test Data" tab. When the type has them, the application writes the test data there as well as in the Summary:
when test cases are created by AI Test Design, and when test data is written to TFS. Nothing changes for a type
without them. A field the server refuses (for example a "Test Data ID" that only takes numbers) is left out and the
rest is written.
"""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import quote

from ..errors import QAError
from ..logging_setup import get_logger

log = get_logger("design.tfs_test_data")

DATA_FIELD_NAME = re.compile(r"test\s*_?data", re.I)
_TEXT_TYPES = ("string", "plaintext", "html", "")


def _label(key: str) -> str:
    text = re.sub(r"[_]+", " ", str(key)).strip()
    return text[:1].upper() + text[1:] if text else str(key)


def matrix_html(rows: list[dict[str, Any]], marker: str | None = None) -> str:
    """Rows -> the 'Test Data Matrix' layout used in the project's test cases (one Combination per row)."""
    e = html.escape
    parts = ["<h3>Test Data Matrix</h3>"]
    for n, r in enumerate(rows, start=1):
        vals = r["dataset"] if isinstance(r.get("dataset"), dict) else (r.get("values") or {})
        lines = [f"<strong>Combination {n}: {e(str(r.get('name') or r.get('scenario') or 'Data set'))}</strong>"]
        lines += [f"<strong>{e(_label(k))}:</strong> {e('' if v is None else str(v))}" for k, v in vals.items()]
        if r.get("intended_valid") is False and r.get("violated_rule"):
            lines.append(f"<strong>Violated rule:</strong> {e(r['violated_rule'])}")
        if r.get("expected_outcome"):
            lines.append(f"<strong>Expected Result:</strong> {e(r['expected_outcome'])}")
        parts.append("<p>" + "<br>".join(lines) + "</p>")
    if marker:
        parts.append(f"<p><em>{e(marker)}</em></p>")
    return "".join(parts)


def matrix_text(rows: list[dict[str, Any]], marker: str | None = None) -> str:
    """The same matrix as plain text, for a Test Data field that is not an HTML field."""
    out = []
    for n, r in enumerate(rows, start=1):
        vals = r["dataset"] if isinstance(r.get("dataset"), dict) else (r.get("values") or {})
        out.append(f"Combination {n}: {r.get('name') or r.get('scenario') or 'Data set'}")
        out += [f"{_label(k)}: {'' if v is None else v}" for k, v in vals.items()]
        if r.get("intended_valid") is False and r.get("violated_rule"):
            out.append(f"Violated rule: {r['violated_rule']}")
        if r.get("expected_outcome"):
            out.append(f"Expected Result: {r['expected_outcome']}")
        out.append("")
    if marker:
        out.append(marker)
    return "\n".join(out).strip()


def rows_from_lines(lines: list[Any], name: str) -> list[dict[str, Any]]:
    """AI Test Design's test data lines ("product_name = 'Wireless Headphones'") as one data set."""
    values: dict[str, Any] = {}
    for n, line in enumerate(lines, start=1):
        text = str(line).strip()
        m = re.match(r"^([^=:]{1,60}?)\s*[=:]\s*(.*)$", text)
        if m:
            values[m.group(1).strip()] = m.group(2).strip().strip("'\"")
        elif text:
            values[f"Data {n}"] = text
    return [{"name": name, "values": values}] if values else []


def _field_type(tfs: Any, ref: str) -> str:
    def load() -> str:
        try:
            info = tfs._with_client(lambda c: c.request("GET", f"_apis/wit/fields/{quote(ref, safe='')}"))
            return str((info or {}).get("type") or "").lower()
        except QAError:
            return ""
    return tfs.meta_cache.get_or_set(("field-type", tfs.active_mode(), tfs.profile()["project"], ref), load)


def tab_fields(tfs: Any, type_name: str) -> dict[str, str | None]:
    """{"data": reference name of the Test Data field, "data_type", "id": the Test Data ID field, "id_type"}; the
    values are None when the type has no such field (or the fields cannot be read)."""
    def load() -> list[dict[str, Any]]:
        fields = tfs._with_client(lambda c: c.work_item_type_fields(type_name))
        return [f for f in fields if not f["referenceName"].startswith(("System.", "Microsoft.VSTS."))
                and DATA_FIELD_NAME.search(f"{f.get('name') or ''} {f['referenceName']}")]
    try:
        key = ("test-data-fields", tfs.active_mode(), tfs.profile()["project"], type_name)
        fields = tfs.meta_cache.get_or_set(key, load)
    except QAError as exc:
        log.warning(f"Test Data fields of '{type_name}' could not be read: {exc.message}")
        return {"data": None, "data_type": None, "id": None, "id_type": None}
    is_id = lambda f: re.search(r"\bid\b|_?id$", (f.get("name") or f["referenceName"]).strip(), re.I)  # noqa: E731
    exact = [f for f in fields if re.sub(r"\W", "", (f.get("name") or "").lower()) == "testdata"]
    data = next(iter(exact), None) or next((f for f in fields if not is_id(f)), None)
    ident = next((f for f in fields if is_id(f)), None)
    out: dict[str, str | None] = {"data": None, "data_type": None, "id": None, "id_type": None}
    if data:
        out["data"], out["data_type"] = data["referenceName"], _field_type(tfs, data["referenceName"])
    if ident:
        out["id"], out["id_type"] = ident["referenceName"], _field_type(tfs, ident["referenceName"])
    return out


def tab_values(tfs: Any, type_name: str, rows: list[dict[str, Any]], data_id: str | None,
               marker: str | None = None) -> dict[str, Any]:
    """The field values that put ``rows`` on the Test Data tab ({} when the type has no Test Data field)."""
    tab = tab_fields(tfs, type_name)
    if not tab["data"] or not rows:
        return {}
    values: dict[str, Any] = {tab["data"]: matrix_html(rows, marker) if tab["data_type"] in ("html", "")
                              else matrix_text(rows, marker)}
    if tab["id"] and data_id:
        if tab["id_type"] in _TEXT_TYPES:
            values[tab["id"]] = data_id
        elif tab["id_type"] == "integer" and str(data_id).isdigit():
            values[tab["id"]] = int(data_id)
    return values


def write_with_fallback(write: Any, values: dict[str, Any], optional: list[str]) -> tuple[Any, list[str]]:
    """Call ``write(values)``; when the server refuses the request (HTTP 400) and optional fields were included,
    write again without them. Returns (result, fields left out)."""
    try:
        return write(values), []
    except QAError as exc:
        extra = [k for k in optional if k in values]
        if exc.error_code != "TFS_REQUEST_FAILED" or not extra:
            raise
        log.warning(f"TFS refused the Test Data tab fields {extra} ({exc.message}); writing without them")
        return write({k: v for k, v in values.items() if k not in extra}), extra
