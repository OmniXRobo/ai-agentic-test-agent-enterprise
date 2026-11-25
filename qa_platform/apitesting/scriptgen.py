"""23.12.0: editable pytest + httpx scripts for API tests, and the checks that run before an edited script may run.

The generated file is self-contained (pytest and httpx only, both shipped with SkyGuard):

* BASE_URL and every secret come from environment variables (SKYGUARD_API_BASE_URL, SKYGUARD_API_PASSWORD,
  SKYGUARD_API_TOKEN, SKYGUARD_API_KEY ...). No secret is ever written into the file.
* One parametrized test per test case, with its assertions (status, headers, content type, JSONPath, schema subset,
  response time) and chained variables.
* TLS verification follows the application setting (on by default).

An edited script runs only after ``validate`` (syntax plus policy: allowed imports, no eval/exec, no programs, no
file deletion, no sockets) and, when SkyGuard runs it, with a network guard (sitecustomize) that lets it connect only
to the application's authorized targets.
"""

from __future__ import annotations

import ast
import json
import pprint
import re
from typing import Any

from ..automation import python_scripts

ALLOWED = (python_scripts.ALLOWED_IMPORTS - {"playwright", "openpyxl", "pypdf", "zipfile", "sys"}) | {"httpx", "requests", "ssl"}

HEADER = '''"""SkyGuard QA API tests: {title}
Application: {app} ({env})   Generated: {at}   Script version: {version}

Run:   pytest -q {file}
Settings come from environment variables; nothing secret is stored in this file:
  SKYGUARD_API_BASE_URL   base URL (default {base})
{secret_help}"""
import json
import os
import re
import time

import httpx
import pytest

BASE_URL = os.environ.get("SKYGUARD_API_BASE_URL", {base!r}).rstrip("/")
VERIFY_TLS = os.environ.get("SKYGUARD_API_VERIFY_TLS", {verify!r}) != "0"
TIMEOUT = float(os.environ.get("SKYGUARD_API_TIMEOUT", {timeout!r}))
'''

AUTH_CODE = {
    "none": "def auth_headers():\n    return {}, {}\n",
    "basic": ("def auth_headers():\n    user = os.environ.get(\"SKYGUARD_API_USERNAME\", {user!r})\n"
              "    pw = os.environ.get(\"SKYGUARD_API_PASSWORD\", \"\")\n"
              "    import base64\n"
              "    return {{\"Authorization\": \"Basic \" + base64.b64encode(f\"{{user}}:{{pw}}\".encode()).decode()}}, {{}}\n"),
    "bearer": ("def auth_headers():\n    return {{\"Authorization\": \"Bearer \" + os.environ.get(\"SKYGUARD_API_TOKEN\", \"\")}}, {{}}\n"),
    "api_key_header": ("def auth_headers():\n    return {{{name!r}: os.environ.get(\"SKYGUARD_API_KEY\", \"\")}}, {{}}\n"),
    "api_key_query": ("def auth_headers():\n    return {{}}, {{{name!r}: os.environ.get(\"SKYGUARD_API_KEY\", \"\")}}\n"),
    "oauth2": ("_TOKEN = {{}}\n\n\ndef auth_headers():\n    if not _TOKEN:\n"
               "        form = {{\"grant_type\": {grant!r}, \"client_id\": {client!r},\n"
               "                \"client_secret\": os.environ.get(\"SKYGUARD_API_CLIENT_SECRET\", \"\")}}\n"
               "        if {scope!r}:\n            form[\"scope\"] = {scope!r}\n"
               "        r = httpx.post({token_url!r} if {token_url!r}.startswith(\"http\") else BASE_URL + \"/\" + {token_url!r}.lstrip(\"/\"),\n"
               "                       data=form, verify=VERIFY_TLS, timeout=TIMEOUT)\n"
               "        r.raise_for_status()\n        _TOKEN[\"t\"] = r.json()[\"access_token\"]\n"
               "    return {{\"Authorization\": \"Bearer \" + _TOKEN[\"t\"]}}, {{}}\n"),
}

HELPERS = '''

# ---- assertion helpers (SkyGuard JSONPath subset and JSON Schema subset) -------------------------------------------
_TOK = re.compile(r"""\\.\\.([A-Za-z_$][\\w$-]*|\\*)|\\.([A-Za-z_$][\\w$-]*|\\*)|\\[(-?\\d+)\\]|\\[\\*\\]|\\[('[^']*'|"[^"]*")\\]""")


def jsonpath(doc, path):
    nodes, pos = [doc], 1
    while pos < len(path):
        m = _TOK.match(path, pos)
        assert m, f"Unsupported JSONPath {path!r}"
        nxt = []
        for n in nodes:
            if m.group(1) is not None:
                stack = [n]
                while stack:
                    cur = stack.pop(0)
                    if isinstance(cur, dict):
                        if m.group(1) == "*":
                            nxt.extend(cur.values())
                        elif m.group(1) in cur:
                            nxt.append(cur[m.group(1)])
                        stack.extend(cur.values())
                    elif isinstance(cur, list):
                        if m.group(1) == "*":
                            nxt.extend(cur)
                        stack.extend(cur)
            elif m.group(2) is not None:
                key = m.group(2)
                if key == "*":
                    nxt.extend(n.values() if isinstance(n, dict) else n if isinstance(n, list) else [])
                elif isinstance(n, dict) and key in n:
                    nxt.append(n[key])
                elif isinstance(n, list) and key == "length":
                    nxt.append(len(n))
            elif m.group(3) is not None:
                i = int(m.group(3))
                if isinstance(n, list) and -len(n) <= i < len(n):
                    nxt.append(n[i])
            elif m.group(4) is not None:
                key = m.group(4)[1:-1]
                if isinstance(n, dict) and key in n:
                    nxt.append(n[key])
            else:
                nxt.extend(n.values() if isinstance(n, dict) else n if isinstance(n, list) else [])
        nodes, pos = nxt, m.end()
    return nodes


_TYPES = {"string": str, "boolean": bool, "array": list, "object": dict}


def schema_errors(value, schema, path="$"):
    errs = []
    if not isinstance(schema, dict) or not schema:
        return errs
    if value is None and schema.get("nullable"):
        return errs
    t = schema.get("type")
    for tt in (t if isinstance(t, list) else [t] if t else []):
        pass
    if t:
        types = t if isinstance(t, list) else [t]
        ok = False
        for tt in types:
            if tt == "null" and value is None or tt == "integer" and isinstance(value, int) and not isinstance(value, bool) \\
                    or tt == "number" and isinstance(value, (int, float)) and not isinstance(value, bool) \\
                    or tt in _TYPES and isinstance(value, _TYPES[tt]) and not (tt != "boolean" and isinstance(value, bool)):
                ok = True
        if not ok:
            return [f"{path}: expected {t}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, dict):
        for k in schema.get("required") or []:
            if k not in value:
                errs.append(f"{path}.{k}: required property missing")
        for k, sub in (schema.get("properties") or {}).items():
            if k in value:
                errs += schema_errors(value[k], sub, f"{path}.{k}")
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(schema.get("properties") or {})
            if extra:
                errs.append(f"{path}: unexpected properties {sorted(extra)}")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, v in enumerate(value[:500]):
            errs += schema_errors(v, schema["items"], f"{path}[{i}]")
    if isinstance(value, str):
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errs.append(f"{path}: longer than {schema['maxLength']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errs.append(f"{path}: shorter than {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errs.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: above {schema['maximum']}")
    for sub in schema.get("allOf") or []:
        errs += schema_errors(value, sub, path)
    return errs[:20]


def status_ok(code, expected):
    for e in expected:
        s = str(e).lower()
        if s.endswith("xx") and str(code)[0] == s[0] or s.isdigit() and int(s) == code:
            return True
    return False


def check(op, actual, value):
    if op == "exists":
        return actual is not None
    if op == "equals":
        return actual == value or str(actual) == str(value)
    if op == "not_equals":
        return actual != value and str(actual) != str(value)
    if op == "contains":
        return value in actual if isinstance(actual, (list, dict)) else str(value) in str(actual)
    if op in ("gt", "gte", "lt", "lte"):
        a, b = float(actual), float(value)
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
    if op == "matches":
        return re.search(str(value), str(actual)) is not None
    if op == "not_empty":
        return actual not in (None, "", [], {})
    return True


def fill(value, variables):
    if isinstance(value, str):
        m = re.fullmatch(r"\\{\\{\\s*([\\w.-]+)\\s*\\}\\}", value.strip())
        if m and m.group(1) in variables:
            return variables[m.group(1)]
        return re.sub(r"\\{\\{\\s*([\\w.-]+)\\s*\\}\\}", lambda mm: str(variables.get(mm.group(1), mm.group(0))), value)
    if isinstance(value, dict):
        return {k: fill(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, variables) for v in value]
    return value


VARIABLES = {}


@pytest.fixture(scope="session")
def client():
    with httpx.Client(verify=VERIFY_TLS, timeout=TIMEOUT, follow_redirects=False) as c:
        yield c


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_api(client, case):
    if case.get("skip"):
        pytest.skip(case["skip"])
    path = fill(case["path"], VARIABLES)
    for k, v in fill(case.get("path_params") or {}, VARIABLES).items():
        path = path.replace("{" + k + "}", str(v))
    headers, query = ({}, {}) if case.get("auth") == "none" else auth_headers()
    headers = {**headers, **{h["name"]: str(fill(h["value"], VARIABLES)) for h in case.get("headers") or []}}
    query = {**query, **{q["name"]: fill(q["value"], VARIABLES) for q in case.get("query") or []}}
    body = fill(case.get("body"), VARIABLES)
    kw = {}
    if body is not None:
        if "json" in (case.get("content_type") or "application/json"):
            kw["json"] = body
        else:
            kw["content"] = body if isinstance(body, str) else json.dumps(body)
            headers.setdefault("Content-Type", case.get("content_type"))
    started = time.perf_counter()
    r = client.request(case["method"], BASE_URL + "/" + path.lstrip("/"), params=query, headers=headers, **kw)
    ms = (time.perf_counter() - started) * 1000
    data = None
    if "json" in r.headers.get("content-type", "") or r.text.lstrip()[:1] in ("{", "["):
        try:
            data = r.json()
        except ValueError:
            data = None
    for a in case["assertions"]:
        t = a["type"]
        if t == "status":
            assert status_ok(r.status_code, a["expected"]), f"status {r.status_code}, expected {a['expected']}"
        elif t == "content_type":
            assert a["expected"].lower() in r.headers.get("content-type", "").lower(), "content type " + r.headers.get("content-type", "")
        elif t == "header":
            v = r.headers.get(a["name"])
            assert v is not None and (a.get("value") in (None, "") or check(a.get("op") or "equals", v, a["value"])), f"header {a['name']}={v!r}"
        elif t == "body_contains":
            assert str(a["value"]) in r.text, f"body does not contain {a['value']!r}"
        elif t == "response_time":
            assert ms <= a["max_ms"], f"{ms:.0f} ms > {a['max_ms']} ms"
        elif t == "schema":
            errs = schema_errors(jsonpath(data, a["path"])[0] if a.get("path") else data, a["schema"])
            assert not errs, "; ".join(errs[:5])
        elif t == "jsonpath":
            found = jsonpath(data, a["path"])
            op = a.get("op") or "exists"
            if op == "not_exists":
                assert not found, f"{a['path']} exists"
            elif op == "all_equal":
                assert found and all(str(f) == str(a["value"]) for f in found), f"{a['path']}: {found[:5]}"
            elif op in ("sorted_asc", "sorted_desc"):
                assert found == sorted(found, reverse=op == "sorted_desc"), f"{a['path']} not sorted"
            else:
                assert found, f"{a['path']}: nothing found"
                assert check(op, found[0], a.get("value")), f"{a['path']} {op} {a.get('value')!r}: got {found[0]!r}"
    for x in case.get("extract") or []:
        found = jsonpath(data, x["path"])
        assert found, f"variable {x['name']}: nothing at {x['path']}"
        VARIABLES[x["name"]] = found[0]
'''


def _case(t: dict[str, Any]) -> dict[str, Any]:
    return {"id": f"{t.get('id', 'T')}-{re.sub(r'[^A-Za-z0-9]+', '_', t.get('name', ''))[:50]}",
            "name": t.get("name"), "method": t.get("method"), "path": t.get("path"),
            "path_params": t.get("path_params") or {}, "query": [q for q in t.get("query") or [] if not q.get("disabled")],
            "headers": [h for h in t.get("headers") or [] if not h.get("disabled")], "body": t.get("body"),
            "content_type": t.get("content_type"), "auth": "none" if t.get("auth") == "none" else "app",
            "assertions": t.get("assertions") or [], "extract": t.get("extract") or [],
            "skip": None if t.get("status") != "needs_review" else "Needs review in SkyGuard: " + (t.get("review_reason") or "")}


def generate(app: dict[str, Any], tests: list[dict[str, Any]], *, title: str, version: int, at: str,
             file_name: str, base_url: str) -> str:
    cfg = app.get("config") or {}
    auth = cfg.get("auth") or {"type": "none"}
    t = auth.get("type") or "none"
    if t == "api_key":
        key = "api_key_query" if auth.get("in") == "query" else "api_key_header"
        code = AUTH_CODE[key].format(name=auth.get("name") or "X-API-Key")
        helps = "  SKYGUARD_API_KEY        the API key"
    elif t == "basic":
        code = AUTH_CODE["basic"].format(user=auth.get("username") or "")
        helps = "  SKYGUARD_API_USERNAME / SKYGUARD_API_PASSWORD"
    elif t == "bearer":
        code = AUTH_CODE["bearer"].format()
        helps = "  SKYGUARD_API_TOKEN      the bearer token"
    elif t == "oauth2":
        code = AUTH_CODE["oauth2"].format(grant=auth.get("grant") or "client_credentials",
                                          client=auth.get("client_id") or "", scope=auth.get("scope") or "",
                                          token_url=auth.get("token_url") or "")
        helps = "  SKYGUARD_API_CLIENT_SECRET   the OAuth client secret"
    else:
        code = AUTH_CODE["none"]
        helps = "  (no authentication)" if t == "none" else f"  ({t} authentication: add the headers your API needs)"
    head = HEADER.format(title=title.replace('"""', "'"), app=app.get("name", "").replace('"""', "'"),
                         env=app.get("environment") or "", at=at, version=version, file=file_name, base=base_url,
                         secret_help=helps + "\n", verify="0" if (cfg.get("tls") or {}).get("verify") is False else "1",
                         timeout=str(max(1, int(cfg.get("timeout_ms") or 30000) // 1000)))
    cases = [_case(x) for x in tests]
    body = "\n\n" + code + "\n\nCASES = " + pprint.pformat(cases, width=110, sort_dicts=False) + "\n"
    src = head + body + HELPERS
    src = src.replace("    for tt in (t if isinstance(t, list) else [t] if t else []):\n        pass\n", "")
    return src


def validate(source: str) -> dict[str, Any]:
    """Syntax and policy check of an API test script (same rules as Guided Browser Python, with httpx/requests)."""
    res = python_scripts.validate(source, allowed=ALLOWED, browser=False)
    errors = list(res["errors"])
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
                if name == "open":
                    mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "mode"), None)
                    if isinstance(mode, ast.Constant) and any(c in str(mode.value) for c in "wax+"):
                        errors.append({"line": node.lineno, "column": node.col_offset + 1, "kind": "policy",
                                       "message": "Writing files is not allowed in an API test."})
    except SyntaxError:
        pass
    errors.sort(key=lambda e: (e["line"], e["column"]))
    return {"ok": not errors, "errors": errors[:50]}


SITECUSTOMIZE = '''"""SkyGuard network guard for API test scripts: connections only to the authorized targets of this application."""
import json
import os
import socket

_ALLOWED = json.loads(os.environ.get("SKYGUARD_ALLOWED_ENDPOINTS", "[]"))
_orig_connect = socket.socket.connect
_orig_create = socket.create_connection


def _allowed(address):
    try:
        host, port = address[0], int(address[1])
    except Exception:
        return False
    for h, p in _ALLOWED:
        if (h == host or h == "*") and (p == port or p == 0):
            return True
    return False


def _guard_connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6) and not _allowed(address):
        raise PermissionError(f"SkyGuard: {address[0]}:{address[1]} is not an authorized target of this API application")
    return _orig_connect(self, address)


socket.socket.connect = _guard_connect
'''


def dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)
