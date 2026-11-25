"""23.12.0: API assertions. Each one returns what was expected, what the response showed and why it passed or failed."""

from __future__ import annotations

import re
from typing import Any

from . import jsonpath, schema as schema_mod

OPS = ("equals", "not_equals", "exists", "not_exists", "contains", "not_contains", "gt", "gte", "lt", "lte", "matches",
       "type", "length", "all_equal", "sorted_asc", "sorted_desc", "not_empty")
TYPES = ("status", "header", "content_type", "jsonpath", "body_contains", "schema", "response_time")


def status_ok(code: int, expected: list[Any]) -> bool:
    for e in expected or []:
        s = str(e).strip().lower()
        if re.fullmatch(r"[1-5]xx", s) and str(code).startswith(s[0]):
            return True
        if s.isdigit() and int(s) == code:
            return True
        m = re.fullmatch(r"(\d{3})-(\d{3})", s)
        if m and int(m.group(1)) <= code <= int(m.group(2)):
            return True
    return False


def _cmp(op: str, actual: Any, value: Any) -> bool:
    if op == "equals":
        if isinstance(actual, (dict, list)) or isinstance(value, (dict, list)):
            return actual == value
        if actual is None or value is None:
            return actual is None and value in (None, "null")
        if isinstance(actual, bool) or isinstance(value, bool):
            return str(actual).lower() == str(value).lower()
        a, b = _num(actual), _num(value)
        if a is not None and b is not None:
            return a == b
        return str(actual) == str(value)
    if op == "not_equals":
        return not _cmp("equals", actual, value)
    if op == "contains":
        if isinstance(actual, list):
            return any(_cmp("equals", a, value) for a in actual)
        if isinstance(actual, dict):
            return str(value) in actual
        return str(value) in str(actual)
    if op == "not_contains":
        return not _cmp("contains", actual, value)
    if op in ("gt", "gte", "lt", "lte"):
        a, b = _num(actual), _num(value)
        if a is None or b is None:
            return False
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
    if op == "matches":
        try:
            return re.search(str(value)[:500], str(actual)[:20000]) is not None
        except re.error:
            return False
    if op == "type":
        return schema_mod._kind(actual) == str(value) or (str(value) == "number" and schema_mod._kind(actual) == "integer")
    if op == "length":
        return hasattr(actual, "__len__") and len(actual) == int(value)
    if op == "not_empty":
        return actual not in (None, "", [], {})
    return False


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def evaluate(a: dict[str, Any], resp: dict[str, Any], *, spec_root: Any = None) -> dict[str, Any]:
    t = a.get("type")
    out = {"type": t, "description": describe(a), "source": a.get("source") or "user", "passed": False,
           "expected": None, "actual": None, "message": ""}
    if resp.get("error"):
        out.update(message=f"No response: {resp['error']}")
        return out
    code = resp.get("status")
    body = resp.get("json") if resp.get("is_json") else resp.get("text")
    try:
        if t == "status":
            exp = a.get("expected") or []
            exp = exp if isinstance(exp, list) else [exp]
            out.update(expected=exp, actual=code, passed=status_ok(int(code), exp))
            out["message"] = "" if out["passed"] else f"Expected status {', '.join(map(str, exp))}, got {code}."
        elif t == "header":
            name = str(a.get("name") or "").lower()
            actual = next((v for k, v in (resp.get("headers") or {}).items() if k.lower() == name), None)
            op = a.get("op") or ("exists" if a.get("value") in (None, "") else "equals")
            passed = actual is not None if op == "exists" else actual is None if op == "not_exists" else \
                (actual is not None and _cmp(op, actual, a.get("value")))
            out.update(expected=f"{a.get('name')} {op} {a.get('value') if a.get('value') is not None else ''}".strip(),
                       actual=actual, passed=passed)
            out["message"] = "" if passed else f"Header {a.get('name')}: expected {op} {a.get('value')!r}, got {actual!r}."
        elif t == "content_type":
            actual = next((v for k, v in (resp.get("headers") or {}).items() if k.lower() == "content-type"), "") or ""
            exp = str(a.get("expected") or "").lower()
            passed = actual.lower().split(";")[0].strip() == exp.split(";")[0].strip() or (exp and exp in actual.lower())
            out.update(expected=exp, actual=actual, passed=bool(passed))
            out["message"] = "" if passed else f"Expected content type {exp}, got {actual or 'none'}."
        elif t == "body_contains":
            text = resp.get("text") or ""
            out.update(expected=a.get("value"), actual=f"{len(text)} characters", passed=str(a.get("value")) in text)
            out["message"] = "" if out["passed"] else f"The body does not contain {a.get('value')!r}."
        elif t == "response_time":
            limit = int(a.get("max_ms") or 0)
            ms = int(resp.get("time_ms") or 0)
            out.update(expected=f"≤ {limit} ms", actual=f"{ms} ms", passed=limit <= 0 or ms <= limit)
            out["message"] = "" if out["passed"] else f"Response took {ms} ms; the limit is {limit} ms."
        elif t == "schema":
            if not resp.get("is_json"):
                out.update(expected="JSON matching the schema", actual=resp.get("content_type") or "not JSON")
                out["message"] = "The response is not JSON, so the schema cannot be checked."
                return out
            res = schema_mod.validate_at(body, a.get("schema") or {}, a.get("path"), root=spec_root)
            unresolved = res.get("unresolved") or []
            out.update(expected="matches the documented schema", actual="matches" if res["valid"] else
                       f"{len(res['errors'])} difference(s)", passed=res["valid"], schema_errors=res["errors"][:20],
                       unchecked=res.get("unchecked"), unresolved=unresolved)
            if unresolved:
                out["message"] = "Not checked: the schema refers to unavailable definitions " + ", ".join(unresolved[:3])
                out["inconclusive"] = True
            elif not res["valid"]:
                out["message"] = "; ".join(f"{e['path']}: {e['message']}" for e in res["errors"][:5])
        elif t == "jsonpath":
            if not resp.get("is_json"):
                out["message"] = "The response is not JSON."
                return out
            path, op, value = a.get("path") or "$", a.get("op") or "exists", a.get("value")
            found = jsonpath.find(body, path)
            if op == "exists":
                passed, actual = bool(found), (found[0] if found else None)
            elif op == "not_exists":
                passed, actual = not found, (found[0] if found else None)
            elif op == "all_equal":
                passed, actual = bool(found) and all(_cmp("equals", f, value) for f in found), found[:10]
            elif op in ("sorted_asc", "sorted_desc"):
                vals = [f for f in found if f is not None]
                try:
                    passed = vals == sorted(vals, reverse=op == "sorted_desc")
                except TypeError:
                    passed = False
                actual = vals[:10]
            else:
                multi = jsonpath.is_multi(path)
                actual = found if multi else (found[0] if found else None)
                passed = bool(found) and _cmp(op, actual, value)
            out.update(expected=f"{path} {op} {value!r}" if op not in ("exists", "not_exists", "sorted_asc", "sorted_desc")
                       else f"{path} {op}", actual=_trim(actual), passed=passed)
            out["message"] = "" if passed else (f"{path}: nothing found." if not found and op not in ("not_exists",) else
                                                f"{path}: expected {op} {value!r}, got {_trim(actual)!r}.")
        else:
            out["message"] = f"Unknown assertion type {t}."
    except jsonpath.JSONPathError as exc:
        out.update(message=str(exc), invalid=True)
    except (TypeError, ValueError) as exc:
        out.update(message=f"The assertion could not be evaluated: {exc}", invalid=True)
    return out


def _trim(v: Any) -> Any:
    s = repr(v)
    if len(s) <= 300:
        return v
    return s[:297] + "..."


def describe(a: dict[str, Any]) -> str:
    t = a.get("type")
    if t == "status":
        exp = a.get("expected")
        return f"Status is {', '.join(map(str, exp if isinstance(exp, list) else [exp]))}"
    if t == "header":
        return f"Header {a.get('name')} {a.get('op') or 'equals'} {a.get('value') or ''}".strip()
    if t == "content_type":
        return f"Content type is {a.get('expected')}"
    if t == "body_contains":
        return f"Body contains {a.get('value')!r}"
    if t == "response_time":
        return f"Responds within {a.get('max_ms')} ms"
    if t == "schema":
        return "Body matches the schema" + (f" at {a['path']}" if a.get("path") else "")
    if t == "jsonpath":
        return f"{a.get('path')} {a.get('op') or 'exists'}" + (f" {a.get('value')!r}" if a.get("value") is not None else "")
    return str(t)


def validate_assertion(a: dict[str, Any]) -> list[str]:
    errs = []
    t = a.get("type")
    if t not in TYPES:
        errs.append(f"Unknown assertion type {t!r}.")
    if t == "jsonpath":
        try:
            jsonpath.parse(a.get("path") or "")
        except jsonpath.JSONPathError as exc:
            errs.append(str(exc))
        if (a.get("op") or "exists") not in OPS:
            errs.append(f"Unknown JSONPath operator {a.get('op')!r}.")
    if t == "status" and not a.get("expected"):
        errs.append("A status assertion needs the expected status (200, 201, 4xx ...).")
    if t == "response_time" and int(a.get("max_ms") or 0) <= 0:
        errs.append("A response-time assertion needs a limit in milliseconds.")
    if t == "header" and not a.get("name"):
        errs.append("A header assertion needs the header name.")
    return errs
