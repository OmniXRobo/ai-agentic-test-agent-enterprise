"""Transformation comparison: an expected message against the message a route produced (JSON, XML or text).

Messages are flattened into ``path -> value`` pairs (JSON: ``$.order.items[0].sku``; XML: ``/Order/Items/Item[1]/@sku``
and ``/Order/Customer/Name``) and compared field by field. Rules make business-equal values equal:

* ``ignore``: paths (``*`` wildcards) not compared, e.g. generated IDs and timestamps
* ``mappings``: expected path -> actual path when the field is renamed or moved by the transformation
* ``numeric_tolerance``: decimals compared with this absolute tolerance (precision / rounding)
* ``dates``: compare date / time values as instants, whatever their format (``2026-01-31`` = ``31/01/2026``)
* ``case_insensitive``, ``trim``: text comparison options
* ``ignore_order``: arrays compared as multisets
* ``ignore_extra``: fields only in the actual message are not differences

XML is parsed with defused settings: no DTD, no entity expansion, no network.
"""

from __future__ import annotations

import fnmatch
import json
import re
from datetime import datetime
from typing import Any

from ..errors import QAError

MAX_FIELDS = 20000
DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%d/%m/%Y %H:%M:%S", "%m/%d/%Y",
                "%d-%m-%Y", "%Y%m%d", "%d.%m.%Y", "%d-%b-%Y", "%Y/%m/%d")


def parse(value: Any, fmt: str = "auto") -> tuple[str, Any]:
    """(format, parsed) for a message given as text or as an already parsed value."""
    if not isinstance(value, (str, bytes)):
        return "json", value
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
    text = text.lstrip("﻿")
    s = text.strip()
    if fmt in ("json", "auto") and s[:1] in ("{", "["):
        try:
            return "json", json.loads(s)
        except ValueError:
            if fmt == "json":
                raise QAError("FUSE_COMPARE_PARSE", "The message is not valid JSON.", status=422) from None
    if fmt in ("xml", "auto") and s.startswith("<"):
        return "xml", _xml(s)
    if fmt in ("json", "xml"):
        raise QAError("FUSE_COMPARE_PARSE", f"The message is not {fmt.upper()}.", status=422)
    return "text", text


def _xml(s: str) -> Any:
    if re.search(r"<!DOCTYPE|<!ENTITY", s, re.I):
        raise QAError("FUSE_COMPARE_PARSE", "XML with a DTD or entities is not parsed (XXE protection).", status=422)
    import xml.etree.ElementTree as ET

    try:
        return ET.fromstring(s)
    except ET.ParseError as exc:
        raise QAError("FUSE_COMPARE_PARSE", f"The message is not well-formed XML ({exc}).", status=422) from exc


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def flatten(fmt: str, doc: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def walk_json(v: Any, path: str) -> None:
        if len(out) > MAX_FIELDS:
            raise QAError("FUSE_COMPARE_TOO_LARGE", "The message has too many fields to compare.", status=422)
        if isinstance(v, dict):
            if not v:
                out[path] = {}
            for k, x in v.items():
                walk_json(x, f"{path}.{k}" if re.match(r"^[A-Za-z_][A-Za-z0-9_-]*$", str(k)) else f"{path}['{k}']")
        elif isinstance(v, list):
            if not v:
                out[path] = []
            for i, x in enumerate(v):
                walk_json(x, f"{path}[{i}]")
        else:
            out[path] = v

    def walk_xml(el: Any, path: str) -> None:
        if len(out) > MAX_FIELDS:
            raise QAError("FUSE_COMPARE_TOO_LARGE", "The message has too many fields to compare.", status=422)
        for k, v in el.attrib.items():
            out[f"{path}/@{_local(k)}"] = v
        children = list(el)
        if not children:
            out[path] = (el.text or "").strip()
            return
        if el.text and el.text.strip():
            out[f"{path}/text()"] = el.text.strip()
        counts: dict[str, int] = {}
        for c in children:
            counts[_local(c.tag)] = counts.get(_local(c.tag), 0) + 1
        seen: dict[str, int] = {}
        for c in children:
            name = _local(c.tag)
            seen[name] = seen.get(name, 0) + 1
            walk_xml(c, f"{path}/{name}" + (f"[{seen[name]}]" if counts[name] > 1 else ""))

    if fmt == "json":
        walk_json(doc, "$")
    elif fmt == "xml":
        walk_xml(doc, "/" + _local(doc.tag))
    else:
        out["text"] = doc
    return out


def _as_date(v: Any) -> datetime | None:
    if not isinstance(v, str) or not (6 <= len(v) <= 40) or not re.search(r"\d", v):
        return None
    s = v.strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
        return d.replace(tzinfo=None) if d.tzinfo is None else d
    except ValueError:
        pass
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(v.strip(), f)
        except ValueError:
            continue
    return None


def _as_number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and re.match(r"^\s*-?\d+(\.\d+)?([eE][-+]?\d+)?\s*$", v):
        return float(v)
    return None


def equal(a: Any, b: Any, rules: dict[str, Any]) -> tuple[bool, str]:
    if a == b:
        return True, ""
    na, nb = _as_number(a), _as_number(b)
    if na is not None and nb is not None:
        tol = float(rules.get("numeric_tolerance") or 0)
        if abs(na - nb) <= tol + 1e-12:
            return True, ""
        return False, "number"
    if rules.get("dates", True):
        da, db = _as_date(a), _as_date(b)
        if da is not None and db is not None:
            try:
                if da == db or (da.tzinfo is None) != (db.tzinfo is None) and da.replace(tzinfo=None) == db.replace(tzinfo=None):
                    return True, ""
            except TypeError:
                pass
            return False, "date"
    if isinstance(a, str) and isinstance(b, str):
        x, y = a, b
        if rules.get("trim", True):
            x, y = x.strip(), y.strip()
        if rules.get("case_insensitive"):
            x, y = x.casefold(), y.casefold()
        if x == y:
            return True, ""
        import unicodedata

        if unicodedata.normalize("NFC", x) == unicodedata.normalize("NFC", y):
            return True, ""
        return False, "text"
    if type(a) is not type(b):
        return False, "type"
    return False, "value"


def _ignored(path: str, patterns: list[str]) -> bool:
    """A path is ignored when it, or one of its parents, matches a pattern."""
    return any(fnmatch.fnmatchcase(path, p) or path.startswith(p + ".") or path.startswith(p + "[")
               or path.startswith(p + "/") for p in patterns)


def _canonical(v: Any) -> Any:
    """Arrays sorted by their elements' content, so that order does not count (``ignore_order``)."""
    if isinstance(v, dict):
        return {k: _canonical(x) for k, x in v.items()}
    if isinstance(v, list):
        items = [_canonical(x) for x in v]
        return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, default=str, ensure_ascii=False))
    return v


def compare(expected: Any, actual: Any, *, fmt: str = "auto", rules: dict[str, Any] | None = None) -> dict[str, Any]:
    rules = rules or {}
    fe, de = parse(expected, fmt)
    fa, da = parse(actual, fmt)
    if fe == "text" or fa == "text":
        ok, why = equal(str(expected), str(actual), rules)
        return {"match": ok, "format": "text", "compared": 1,
                "differences": [] if ok else [{"path": "text", "expected": str(expected)[:500],
                                               "actual": str(actual)[:500], "kind": why or "different"}]}
    if fe != fa:
        return {"match": False, "format": f"{fe}/{fa}", "compared": 0,
                "differences": [{"path": "$", "expected": fe, "actual": fa, "kind": "format"}]}
    if rules.get("ignore_order") and fe == "json":
        de, da = _canonical(de), _canonical(da)
    e, a = flatten(fe, de), flatten(fa, da)
    ignore = [str(p) for p in rules.get("ignore") or []]
    mappings = {str(k): str(v) for k, v in (rules.get("mappings") or {}).items()}
    diffs: list[dict[str, Any]] = []
    used = set()
    for path, want in e.items():
        if _ignored(path, ignore):
            continue
        target = mappings.get(path, path)
        if target not in a:
            diffs.append({"path": path, "actual_path": target, "expected": want, "actual": None, "kind": "missing"})
            continue
        used.add(target)
        ok, why = equal(want, a[target], rules)
        if not ok:
            diffs.append({"path": path, "actual_path": target if target != path else None, "expected": want,
                          "actual": a[target], "kind": why or "different"})
    if not rules.get("ignore_extra"):
        mapped_targets = set(mappings.values())
        for path, got in a.items():
            if path in used or path in mapped_targets or _ignored(path, ignore):
                continue
            diffs.append({"path": path, "expected": None, "actual": got, "kind": "extra"})
    return {"match": not diffs, "format": fe, "compared": len(e), "differences": diffs[:500],
            "truncated": len(diffs) > 500}


__all__ = ["compare", "parse", "flatten", "equal"]
