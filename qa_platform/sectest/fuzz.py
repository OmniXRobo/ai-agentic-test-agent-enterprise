"""Controlled fuzz testing: boundary values, missing fields, wrong types, encoding variations and malformed JSON /
XML, a bounded number of cases per target (default 30) inside the run's request budget. GET parameters are fuzzed by
default; request bodies (POST / PUT / PATCH) only when the target allows state-changing tests and is not Production.
Nothing destructive is sent (no deletes, no large floods): disruptive testing needs explicit authorization and an
isolated environment, which this module does not do on its own."""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Callable, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..itest.common import CONTROL
from . import checks

checks.RULES.update({
    "FUZZ-5XX": ("Server error on malformed input", "Input Validation", "A04:2021 Insecure Design; API8:2023; WSTG-INPV", "medium"),
    "FUZZ-TIMEOUT": ("Request timed out on malformed input", "Input Validation", "A04:2021; API4:2023", "low"),
    "FUZZ-SCHEMA": ("Response does not match its schema for unusual input", "Input Validation", "API8:2023", "low"),
    "FUZZ-ACCEPTED": ("Invalid input accepted without validation", "Input Validation", "A04:2021; WSTG-BUSL-01", "low"),
})

BOUNDARY = ["", " ", "0", "-1", "2147483648", "-2147483649", "9223372036854775808", "1e308", "NaN", "null",
            "A" * 2048, "0.000000001"]
ENCODING = ["اختبار", "测试✓", "%00", "%2525", "‮txt.exe", "\\u0000", "<>&\"'", "Ω≈ç√", "﻿bom"]
MALFORMED_JSON = ['{"a": ', '[1, 2', '{"a": 1}}', "{'a': 1}", '{"a": NaN}', ""]
MALFORMED_XML = ["<r><a></r>", "<?xml version='1.0'?><r>", "<r>&undefined;</r>", "not xml"]


def _wrong_types(value: Any) -> list[Any]:
    if isinstance(value, bool):
        return ["true", 1, None]
    if isinstance(value, (int, float)):
        return [str(value), "abc", None, [value], {"v": value}, True]
    if isinstance(value, str):
        return [12345, None, [value], {"v": value}, False]
    if isinstance(value, list):
        return ["x", {}, None]
    if isinstance(value, dict):
        return ["x", [], None]
    return ["x"]


def json_cases(template: Any, limit: int) -> Iterator[tuple[str, Any]]:
    """(label, body) cases for a JSON object template; bodies are Python values, malformed ones are strings."""
    n = 0
    if isinstance(template, dict):
        for k in list(template)[:15]:
            body = copy.deepcopy(template)
            body.pop(k)
            yield f"missing field '{k}'", body
            n += 1
            for w in _wrong_types(template[k])[:3]:
                body = copy.deepcopy(template)
                body[k] = w
                yield f"'{k}' as {type(w).__name__}", body
                n += 1
            for b in (BOUNDARY[0], BOUNDARY[10], ENCODING[0], ENCODING[4]):
                body = copy.deepcopy(template)
                body[k] = b
                yield f"'{k}' = {b[:12]!r}", body
                n += 1
            if n >= limit:
                return
    for m in MALFORMED_JSON:
        yield f"malformed JSON {m[:10]!r}", ("RAW", m)


def param_cases(name: str, limit: int) -> Iterator[tuple[str, str]]:
    for v in (BOUNDARY + ENCODING)[:limit]:
        yield f"{name} = {v[:14]!r}", v


def _with(url: str, name: str, value: str) -> str:
    p = urlsplit(url)
    q = [(k, value if k == name else v) for k, v in parse_qsl(p.query, keep_blank_values=True)]
    if name not in [k for k, _ in q]:
        q.append((name, value))
    return urlunsplit(p._replace(query=urlencode(q)))


def classify(ex: dict[str, Any], *, schema: Any = None, expect_rejection: bool = False) -> tuple[str, str | None, str]:
    """(status, rule id or None, detail) for one fuzz response."""
    st = int(ex.get("status") or 0)
    if ex.get("error", "").startswith("timeout"):
        return "FAILED", "FUZZ-TIMEOUT", "timed out"
    if st >= 500:
        return "FAILED", "FUZZ-5XX", f"HTTP {st}"
    if checks.STACK.search(ex.get("body") or ""):
        return "FAILED", "ERR-STACK", f"HTTP {st} with a stack trace"
    if schema is not None and 200 <= st < 300 and "json" in (ex.get("content_type") or ""):
        from ..apitesting.schema import validate

        try:
            res = validate(json.loads(ex.get("body") or "null"), schema)
        except ValueError:
            return "FAILED", "FUZZ-SCHEMA", "the response is not JSON"
        if not res.get("valid", True):
            return "FAILED", "FUZZ-SCHEMA", "; ".join(str(e) for e in (res.get("errors") or [])[:3])
    if expect_rejection and 200 <= st < 300:
        return "UNVERIFIED", "FUZZ-ACCEPTED", f"HTTP {st}: accepted (check that the value is valid for the business)"
    return "PASSED", None, f"HTTP {st}"


def run_fuzz(client: Any, *, run_id: str, app_map: dict[str, Any], operations: list[dict[str, Any]],
             allow_bodies: bool, per_target: int, record: Callable[..., None]) -> None:
    targets: list[tuple[str, str]] = []
    for page in app_map.get("pages") or []:
        for k, _v in parse_qsl(urlsplit(page["url"]).query, keep_blank_values=True):
            targets.append((page["url"], k))
    seen = set()
    for url, name in targets[:15]:
        key = (urlsplit(url).path, name)
        if key in seen:
            continue
        seen.add(key)
        for label, value in param_cases(name, per_target):
            CONTROL.point(run_id)
            ex = client.get(_with(url, name, value), follow=False)
            status, rule, detail = classify(ex)
            _record(record, f"Fuzz {urlsplit(url).path}: {label}", status, rule, url, name, detail, client, ex)
    for op in operations[:10]:
        method = (op.get("method") or "GET").upper()
        url = op.get("url") or ""
        if method != "GET" and not allow_bodies:
            record(f"Fuzz {method} {urlsplit(url).path}", "BLOCKED", "fuzz",
                   actual="Body fuzzing needs a non-production target that allows state-changing tests")
            continue
        template = op.get("json")
        schema = op.get("response_schema")
        if template is not None:
            for label, body in json_cases(template, per_target):
                CONTROL.point(run_id)
                if isinstance(body, tuple) and body[0] == "RAW":
                    ex = client.request(method, url, content=body[1], headers={"Content-Type": "application/json"},
                                        follow=False, state_changing_ok=True)
                else:
                    ex = client.request(method, url, json_body=body, follow=False, state_changing_ok=method != "GET")
                status, rule, detail = classify(ex, schema=schema, expect_rejection=label.startswith(("missing", "'")))
                _record(record, f"Fuzz {method} {urlsplit(url).path}: {label}", status, rule, url, None, detail,
                        client, ex)
        elif op.get("xml") is not None:
            for m in MALFORMED_XML:
                CONTROL.point(run_id)
                ex = client.request(method, url, content=m, headers={"Content-Type": "application/xml"}, follow=False,
                                    state_changing_ok=True)
                status, rule, detail = classify(ex)
                _record(record, f"Fuzz {method} {urlsplit(url).path}: malformed XML {m[:12]!r}", status, rule, url,
                        None, detail, client, ex)


def _record(record: Callable[..., None], name: str, status: str, rule: str | None, url: str, param: str | None,
            detail: str, client: Any, ex: dict[str, Any]) -> None:
    if rule and status == "FAILED":
        record(name, status, "fuzz", finding=checks.finding(rule, url, f"{name}: {detail}", param=param,
                                                            confidence="medium", extra={"exchange": client.evidence(ex, 500)}))
    else:
        record(name, status, "fuzz", expected="Handled without a server error", actual=detail,
               evidence={"status": ex.get("status")})


__all__ = ["run_fuzz", "json_cases", "param_cases", "classify"]
_ = re
