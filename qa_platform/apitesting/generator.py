"""23.12.0: API test generation from what is documented.

Rules that keep generated tests honest:

* Expected statuses come from the specification. A positive test expects the first documented 2xx status. A
  negative test (missing required field, wrong type, out of range, missing parameter, no credentials) expects a
  documented 4xx status of that operation; when none is documented the test is created as **Needs review** with
  the usual status class written in, and it does not run until a person approves it.
* An undocumented endpoint (added by hand or from cURL) gets a status-class check only, marked Needs review: one
  observed response is never turned into an expected result.
* Schema assertions use the documented response schema; content-type checks use the documented media type.
* Response-time checks use the limit set on the application (a target you choose, not a documented value).
"""

from __future__ import annotations

import copy
import re
from typing import Any

from .safety import is_state_changing

PAGE_PARAMS = {"page", "page_number", "pagenumber", "offset", "skip", "cursor", "start"}
SIZE_PARAMS = {"limit", "size", "page_size", "pagesize", "per_page", "perpage", "count", "top", "$top", "max"}
SORT_PARAMS = {"sort", "sort_by", "sortby", "order_by", "orderby", "$orderby"}
NEG_STATUSES = ("400", "422")


def sample(schema: Any, depth: int = 0, name: str = "") -> Any:
    """A valid value for a schema: example, default, first enum value, else a value of the right type and range."""
    if not isinstance(schema, dict) or depth > 8:
        return "sample"
    for key in ("example", "default"):
        if key in schema and schema[key] is not None:
            return copy.deepcopy(schema[key])
    if schema.get("enum"):
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    for comb in ("allOf",):
        if schema.get(comb):
            merged: dict[str, Any] = {}
            for s in schema[comb]:
                v = sample(s, depth + 1, name)
                if isinstance(v, dict):
                    merged.update(v)
            return merged
    for comb in ("oneOf", "anyOf"):
        if schema.get(comb):
            return sample(schema[comb][0], depth + 1, name)
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "string")
    if t is None:
        t = "object" if "properties" in schema else "array" if "items" in schema else "string"
    if t == "object":
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        out = {}
        for k, sub in props.items():
            if isinstance(sub, dict) and sub.get("readOnly"):
                continue
            if k in required or len(out) < 12:
                out[k] = sample(sub, depth + 1, k)
        return out
    if t == "array":
        n = max(1, int(schema.get("minItems") or 1))
        return [sample(schema.get("items") or {}, depth + 1, name) for _ in range(min(n, 3))]
    if t == "integer":
        return int(_in_range(schema, 1))
    if t == "number":
        return float(_in_range(schema, 1.5))
    if t == "boolean":
        return True
    fmt = schema.get("format")
    value = {"date": "2026-01-15", "date-time": "2026-01-15T10:00:00Z", "email": "qa.tester@example.test",
             "uuid": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "ipv4": "192.0.2.10", "uri": "https://example.test/item",
             "time": "10:00:00"}.get(fmt or "")
    if value is None:
        value = f"SkyGuard {name or 'value'}"[: int(schema.get("maxLength") or 40)]
        min_len = int(schema.get("minLength") or 0)
        if len(value) < min_len:
            value = (value + "x" * min_len)[:max(min_len, len(value))]
    return value


def _in_range(schema: dict[str, Any], default: float) -> float:
    lo, hi = schema.get("minimum"), schema.get("maximum")
    if isinstance(schema.get("exclusiveMinimum"), (int, float)) and not isinstance(schema.get("exclusiveMinimum"), bool):
        lo = schema["exclusiveMinimum"] + 1
    elif schema.get("exclusiveMinimum") is True and lo is not None:
        lo = lo + 1
    if lo is not None and hi is not None:
        return lo if lo == hi else (lo + hi) // 2 if isinstance(lo, int) and isinstance(hi, int) else (lo + hi) / 2
    if lo is not None:
        return lo
    if hi is not None:
        return min(hi, default)
    return default


def wrong_type(schema: dict[str, Any]) -> Any:
    t = schema.get("type")
    if t in ("integer", "number", "boolean"):
        return "not-a-number"
    if t == "array":
        return "not-a-list"
    if t == "object":
        return "not-an-object"
    return 12345


def _documented(ep: dict[str, Any], prefix: str) -> list[str]:
    return sorted(s for s in (ep.get("responses") or {}) if s.startswith(prefix) or s.upper() == f"{prefix}XX")


def _status_assert(statuses: list[str], fallback_class: str) -> dict[str, Any]:
    exact = [int(s) for s in statuses if s.isdigit()]
    if exact:
        return {"type": "status", "expected": exact, "source": "documented"}
    cls = next((s for s in statuses if s.upper().endswith("XX")), None)
    return {"type": "status", "expected": [(cls or fallback_class).lower()], "source": "documented" if cls else "assumed"}


def _base_request(ep: dict[str, Any]) -> dict[str, Any]:
    path_params, query, headers = {}, [], []
    for p in ep.get("parameters") or []:
        value = p.get("example") if p.get("example") is not None else sample(p.get("schema") or {}, name=p["name"])
        if p["in"] == "path":
            path_params[p["name"]] = value
        elif p["in"] == "query" and p.get("required"):
            query.append({"name": p["name"], "value": value})
        elif p["in"] == "header" and p.get("required") and p["name"].lower() not in ("authorization", "cookie"):
            headers.append({"name": p["name"], "value": value})
    rb = ep.get("request_body")
    body = None
    ctype = None
    if rb:
        ctype = rb.get("content_type") or "application/json"
        body = copy.deepcopy(rb["example"]) if rb.get("example") is not None else sample(rb.get("schema") or {})
    return {"method": ep["method"], "path": ep["path"], "path_params": path_params, "query": query,
            "headers": headers, "body": body, "content_type": ctype}


def _success_assertions(ep: dict[str, Any], app: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    ok = _documented(ep, "2")
    out = [_status_assert(ok, "2xx")]
    documented = bool(ok)
    if ok:
        resp = ep["responses"].get(next((s for s in ok if s.isdigit()), ok[0])) or {}
        if resp.get("schema"):
            out.append({"type": "schema", "schema": resp["schema"], "source": "documented"})
        if resp.get("content_type"):
            out.append({"type": "content_type", "expected": resp["content_type"].split(";")[0], "source": "documented"})
    limit = int((app.get("config") or {}).get("response_time_ms") or 0)
    if limit > 0:
        out.append({"type": "response_time", "max_ms": limit, "source": "application target"})
    return out, documented


def _negative(ep: dict[str, Any], base: dict[str, Any], kind: str, name: str, mutate: Any,
              want: tuple[str, ...] = NEG_STATUSES) -> dict[str, Any]:
    req = copy.deepcopy(base)
    mutate(req)
    documented = [s for s in _documented(ep, "4") if s in want or s.upper() == "4XX"]
    status = _status_assert(documented, "4xx")
    test = {**req, "name": name, "kind": kind, "assertions": [status], "auth": "app",
            "status": "ready" if status["source"] == "documented" else "needs_review",
            "review_reason": None if status["source"] == "documented" else
            f"No {'/'.join(want)} response is documented for {ep['method']} {ep['path']}; approve or change the "
            "expected status before this test runs."}
    err = ep["responses"].get(documented[0]) if documented else None
    if err and err.get("schema"):
        test["assertions"].append({"type": "schema", "schema": err["schema"], "source": "documented error schema"})
    return test


def generate(app: dict[str, Any], endpoints: list[dict[str, Any]], *, options: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    opts = {"positive": True, "negative": True, "boundary": True, "auth": True, "pagination": True,
            "filtering": True, "sorting": True, **(options or {})}
    tests: list[dict[str, Any]] = []
    roles = [r for r in (app.get("config") or {}).get("roles") or [] if r.get("name")]
    for ep in endpoints:
        op = f"{ep['method']} {ep['path']}"
        base = _base_request(ep)
        success, documented = _success_assertions(ep, app)
        common = {"operation": op, "origin": "openapi" if ep.get("responses") else "manual",
                  "destructive": is_state_changing(ep["method"]), "tags": ep.get("tags") or []}
        if opts["positive"]:
            tests.append({**common, **copy.deepcopy(base), "name": f"{op}: documented success", "kind": "positive",
                          "assertions": success, "auth": "app",
                          "status": "ready" if documented else "needs_review",
                          "review_reason": None if documented else
                          "No success status is documented; 2xx is assumed. Approve or set the expected result."})
        if not opts["negative"]:
            continue
        rb = ep.get("request_body") or {}
        schema = rb.get("schema") or {}
        props = schema.get("properties") or {}
        if isinstance(base.get("body"), dict):
            for field in list(schema.get("required") or [])[:10]:
                tests.append({**common, **_negative(ep, base, "required_field", f"{op}: missing required field {field}",
                                                   lambda r, f=field: r["body"].pop(f, None))})
            for field, sub in list(props.items())[:10]:
                if not isinstance(sub, dict) or not sub.get("type") or sub.get("readOnly"):
                    continue
                tests.append({**common, **_negative(ep, base, "data_type", f"{op}: wrong type for {field}",
                                                   lambda r, f=field, s=sub: r["body"].__setitem__(f, wrong_type(s)))})
                if sub.get("enum"):
                    tests.append({**common, **_negative(ep, base, "invalid_input", f"{op}: {field} outside its allowed values",
                                                       lambda r, f=field: r["body"].__setitem__(f, "SKYGUARD-NOT-ALLOWED"))})
                if opts["boundary"]:
                    tests.extend({**common, **t} for t in _boundaries(ep, base, op, field, sub, success, documented))
        elif rb.get("required") and base.get("body") is not None:
            tests.append({**common, **_negative(ep, base, "required_field", f"{op}: request body missing",
                                               lambda r: r.__setitem__("body", None))})
        for p in ep.get("parameters") or []:
            if p.get("required") and p["in"] == "query":
                tests.append({**common, **_negative(ep, base, "missing_parameter", f"{op}: missing query parameter {p['name']}",
                                                   lambda r, n=p["name"]: r.__setitem__("query", [q for q in r["query"] if q["name"] != n]))})
            if p["in"] in ("query", "path") and isinstance(p.get("schema"), dict) and p["schema"].get("type") in ("integer", "number"):
                tests.append({**common, **_negative(ep, base, "invalid_input", f"{op}: {p['name']} is not a number",
                                                   lambda r, pp=p: _set_param(r, pp, "not-a-number"))})
        if opts["auth"] and ep.get("security") and any(ep["security"]):
            tests.append({**common, **_negative(ep, base, "authentication", f"{op}: rejected without credentials",
                                               lambda r: None, want=("401", "403")), "auth": "none"})
            for role in roles:
                allowed = role.get("allowed_operations")
                if isinstance(allowed, list) and op not in allowed:
                    t = _negative(ep, base, "authorization", f"{op}: refused for role {role['name']}", lambda r: None,
                                  want=("403", "401", "404"))
                    tests.append({**common, **t, "auth": f"role:{role['name']}"})
        if ep["method"] == "GET":
            qnames = {p["name"].lower(): p for p in ep.get("parameters") or [] if p["in"] == "query"}
            size = next((qnames[n] for n in qnames if n in SIZE_PARAMS), None)
            page = next((qnames[n] for n in qnames if n in PAGE_PARAMS), None)
            array_path = _array_path(ep)
            if opts["pagination"] and size is not None and array_path:
                t = copy.deepcopy(base)
                t["query"] = [q for q in t["query"] if q["name"] != size["name"]] + [{"name": size["name"], "value": 1}]
                if page is not None and page["name"] not in [q["name"] for q in t["query"]]:
                    first = 0 if page["name"].lower() in ("offset", "skip", "start") else 1
                    t["query"].append({"name": page["name"], "value": first})
                tests.append({**common, **t, "name": f"{op}: page size {size['name']}=1 returns at most one item",
                              "kind": "pagination", "auth": "app", "status": "ready" if documented else "needs_review",
                              "review_reason": None if documented else "No success status is documented.",
                              "assertions": [success[0], {"type": "jsonpath", "path": f"{array_path}.length", "op": "lte",
                                                          "value": 1, "source": "documented parameter"}]})
            for qn, p in qnames.items():
                sch = p.get("schema") or {}
                if opts["filtering"] and sch.get("enum") and array_path and qn not in SIZE_PARAMS | PAGE_PARAMS | SORT_PARAMS:
                    item_schema = _array_items(ep) or {}
                    if p["name"] in (item_schema.get("properties") or {}):
                        t = copy.deepcopy(base)
                        value = sch["enum"][0]
                        t["query"] = [q for q in t["query"] if q["name"] != p["name"]] + [{"name": p["name"], "value": value}]
                        tests.append({**common, **t, "name": f"{op}: filter {p['name']}={value} returns only matching items",
                                      "kind": "filtering", "auth": "app", "status": "ready" if documented else "needs_review",
                                      "review_reason": None if documented else "No success status is documented.",
                                      "assertions": [success[0], {"type": "jsonpath", "path": f"{array_path}[*].{p['name']}",
                                                                  "op": "all_equal", "value": value,
                                                                  "source": "documented filter"}]})
                if opts["sorting"] and qn in SORT_PARAMS and array_path:
                    item_schema = _array_items(ep) or {}
                    field = (sch.get("enum") or [None])[0] or next(iter(item_schema.get("properties") or {}), None)
                    if field and field.lstrip("-+") in (item_schema.get("properties") or {}):
                        t = copy.deepcopy(base)
                        t["query"] = [q for q in t["query"] if q["name"] != p["name"]] + [{"name": p["name"], "value": field}]
                        f = field.lstrip("-+")
                        tests.append({**common, **t, "name": f"{op}: sorted by {field}", "kind": "sorting", "auth": "app",
                                      "status": "needs_review",
                                      "review_reason": "The sort direction a value means is not documented; confirm "
                                                       "ascending before this runs.",
                                      "assertions": [success[0], {"type": "jsonpath", "path": f"{array_path}[*].{f}",
                                                                  "op": "sorted_desc" if field.startswith("-") else "sorted_asc",
                                                                  "source": "assumed"}]})
    for t in tests:
        t.setdefault("path_params", {})
        t.setdefault("query", [])
        t.setdefault("headers", [])
        t.setdefault("extract", [])
        t["destructive"] = is_state_changing(t["method"])
    return tests


def _set_param(req: dict[str, Any], p: dict[str, Any], value: Any) -> None:
    if p["in"] == "path":
        req["path_params"][p["name"]] = value
    else:
        req["query"] = [q for q in req["query"] if q["name"] != p["name"]] + [{"name": p["name"], "value": value}]


def _boundaries(ep: dict[str, Any], base: dict[str, Any], op: str, field: str, sub: dict[str, Any],
                success: list[dict[str, Any]], documented: bool) -> list[dict[str, Any]]:
    out = []
    t = sub.get("type")
    edges: list[tuple[str, Any, bool]] = []
    if t in ("integer", "number"):
        step = 1 if t == "integer" else 0.01
        if sub.get("minimum") is not None and sub.get("exclusiveMinimum") is not True:
            edges += [("at minimum", sub["minimum"], True), ("below minimum", sub["minimum"] - step, False)]
        if sub.get("maximum") is not None and sub.get("exclusiveMaximum") is not True:
            edges += [("at maximum", sub["maximum"], True), ("above maximum", sub["maximum"] + step, False)]
    if t == "string":
        if sub.get("maxLength") is not None:
            n = int(sub["maxLength"])
            edges += [("at maximum length", "x" * n, True), ("over maximum length", "x" * (n + 1), False)]
        if sub.get("minLength"):
            n = int(sub["minLength"])
            edges += [("at minimum length", "x" * n, True), ("under minimum length", "x" * (n - 1), False)]
    for label, value, valid in edges:
        name = f"{op}: {field} {label} ({value if not isinstance(value, str) else str(len(value)) + ' characters'})"
        if valid:
            req = copy.deepcopy(base)
            req["body"][field] = value
            out.append({**req, "name": name, "kind": "boundary", "auth": "app", "assertions": [success[0]],
                        "status": "ready" if documented else "needs_review",
                        "review_reason": None if documented else "No success status is documented."})
        else:
            out.append(_negative(ep, base, "boundary", name, lambda r, v=value: r["body"].__setitem__(field, v)))
    return out


def _array_items(ep: dict[str, Any]) -> dict[str, Any] | None:
    ok = _documented(ep, "2")
    resp = ep["responses"].get(ok[0]) if ok else None
    sch = (resp or {}).get("schema") or {}
    if sch.get("type") == "array":
        return sch.get("items") or {}
    for k, v in (sch.get("properties") or {}).items():
        if isinstance(v, dict) and v.get("type") == "array":
            return v.get("items") or {}
    return None


def _array_path(ep: dict[str, Any]) -> str | None:
    ok = _documented(ep, "2")
    resp = ep["responses"].get(ok[0]) if ok else None
    sch = (resp or {}).get("schema") or {}
    if sch.get("type") == "array":
        return "$"
    for k, v in (sch.get("properties") or {}).items():
        if isinstance(v, dict) and v.get("type") == "array":
            return f"$.{k}" if re.match(r"^[A-Za-z_][\w-]*$", k) else f"$['{k}']"
    return None


def manual_endpoint_test(ep: dict[str, Any], app: dict[str, Any], origin: str = "manual") -> dict[str, Any]:
    """One test for an endpoint without a specification: a 2xx class check, marked Needs review."""
    limit = int((app.get("config") or {}).get("response_time_ms") or 0)
    assertions = [{"type": "status", "expected": ["2xx"], "source": "assumed"}]
    if limit:
        assertions.append({"type": "response_time", "max_ms": limit, "source": "application target"})
    return {"name": ep.get("name") or f"{ep['method']} {ep['path']}", "kind": "manual",
            "operation": f"{ep['method']} {ep['path']}", "origin": origin, "method": ep["method"], "path": ep["path"],
            "path_params": ep.get("path_params") or {}, "query": ep.get("query") or [], "headers": ep.get("headers") or [],
            "body": ep.get("body"), "content_type": ep.get("content_type"), "auth": "app", "assertions": assertions,
            "extract": [], "status": "needs_review", "destructive": is_state_changing(ep["method"]),
            "review_reason": "No specification: the expected result is not documented. Set or approve the expected "
                             "status and checks before relying on this test."}
