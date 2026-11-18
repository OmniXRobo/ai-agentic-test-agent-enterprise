"""23.12.0: API Testing module, end to end against a real local HTTP(S) API (tests/api_site.py).

Covers: application configuration and authorized targets, Test Connection diagnostics, OpenAPI 3 / Swagger 2 (JSON and
YAML, unresolved external references), cURL / Postman / Python imports (never executed), generated tests with
documented expectations, schema / negative / pagination / filtering checks, chains with variable extraction, every
authentication type, response time, timeout, connection failure, failure classification, Defect Review drafts, approval
and TFS publication with the real work item ID, duplicate prevention, scheduled API runs and notifications, secret
redaction, SSRF protections (unauthorized host, SkyGuard's own port, metadata address, redirects), state-changing
requests, the pytest + httpx script and the new agents."""

from __future__ import annotations

import json
import shutil
import socket
import time

import pytest
from fastapi.testclient import TestClient

from qa_platform import auth
from qa_platform.apitesting import importers, safety
from qa_platform.credentials import MemoryBackend
from qa_platform.errors import QAError
from qa_platform.main import create_app
from tests import api_site
from tests.fake_ado import PAT, FakeAzureDevOps

A = "/api/apitest"


@pytest.fixture()
def site():
    srv, url, st = api_site.start()
    yield url, st
    srv.shutdown()


@pytest.fixture()
def env(paths):
    ado = FakeAzureDevOps(severity=True)
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport())
    with TestClient(app, raise_server_exceptions=False) as c:
        c.app.state.runtime.settings.update({"performance": {"auto_report": False}})
        yield c, ado, app.state.runtime


def new_app(c, url, auth_cfg=None, **extra):
    body = {"name": extra.pop("name", "Orders API"), "environment": extra.pop("environment", "Test"),
            "config": {"base_url": url, "auth": auth_cfg or {"type": "none"}, **extra.pop("config", {})},
            "authorized": True, **extra}
    r = c.post(f"{A}/apps", json=body)
    assert r.status_code == 201, r.text
    return r.json()["app"]


def secret(c, aid, field, value, role=None):
    r = c.post(f"{A}/apps/{aid}/credentials", json={"field": field, "value": value, "role": role})
    assert r.status_code == 200, r.text
    return r.json()["app"]


def wait_run(c, rid, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        d = c.get(f"{A}/runs/{rid}").json()["run"]
        if d["status"] != "RUNNING":
            return d
        time.sleep(0.2)
    raise AssertionError(f"run {rid} did not finish")


def run(c, aid, **body):
    r = c.post(f"{A}/apps/{aid}/run", json=body)
    assert r.status_code == 200, r.text
    return wait_run(c, r.json()["run_id"])


def add_test(c, aid, **t):
    t.setdefault("name", f"{t.get('method', 'GET')} {t.get('path', '/')}")
    t.setdefault("assertions", [{"type": "status", "expected": ["200"]}])
    r = c.post(f"{A}/apps/{aid}/tests", json=t)
    assert r.status_code == 201, r.text
    test = r.json()["test"]
    if test["status"] == "needs_review":
        test = c.post(f"{A}/tests/{test['id']}/approve").json()["test"]
    return test


def by_name(d, text):
    return [x for x in d["results"] if text in x["test_name"]]


# ---- 1-8: configuration, connection, OpenAPI import, generation, run ------------------------------------------------

def test_openapi_import_generate_and_run_with_basic_auth_and_redaction(env, site, paths):
    c, ado, rt = env
    url, st = site
    st.auth = "basic"
    a = new_app(c, url, {"type": "basic", "username": api_site.BASIC[0]})
    assert a["credentials"]["app"]["password"] is False
    a = secret(c, a["id"], "password", api_site.BASIC[1])
    assert a["credentials"]["app"]["password"] is True
    assert api_site.BASIC[1] not in json.dumps(a)

    conn = c.post(f"{A}/apps/{a['id']}/test-connection").json()
    steps = {s["step"]: s for s in conn["steps"]}
    assert conn["ok"] is True
    assert steps["Authorized target"]["status"] == "PASS" and steps["TCP connection"]["status"] == "PASS"
    # a 404 at the base path is still a reachable server: no health endpoint is assumed
    assert "reachable" in steps["HTTP answer"]["detail"]

    r = c.post(f"{A}/apps/{a['id']}/spec", json={"url": url + "/openapi.json"})
    assert r.status_code == 200, r.text
    assert r.json()["spec"]["version"].startswith("OpenAPI 3") or "3.0" in r.json()["spec"]["version"]
    tests = c.get(f"{A}/apps/{a['id']}/tests").json()["tests"]
    kinds = {t["kind"] for t in tests}
    assert {"positive", "required_field", "data_type", "authentication", "boundary"} <= kinds
    assert any(t["kind"] == "pagination" for t in tests) and any(t["kind"] == "filtering" for t in tests)
    positive = [t for t in tests if t["kind"] == "positive"]
    assert all(any(x["type"] == "status" and x.get("source") == "documented" for x in t["assertions"]) for t in positive)
    assert any(x["type"] == "schema" for t in positive for x in t["assertions"])  # documented response schema
    assert any(t["destructive"] for t in tests)  # POST / DELETE are marked as changing data

    d = run(c, a["id"], confirm_state_changes=True)
    failed = [(x["test_name"], x["error"], [y["message"] for y in x["assertions"] if y.get("passed") is False])
              for x in d["results"] if x["status"] == "FAILED"]
    assert not failed, failed
    assert d["status"] == "PASSED" and d["counts"]["PASSED"] >= 10
    assert d["counts"]["total"] == sum(d["counts"][k] for k in ("PASSED", "FAILED", "SKIPPED", "BLOCKED"))

    # the Authorization header and the password are redacted everywhere SkyGuard keeps them
    first = d["results"][0]
    assert first["request"]["headers"]["Authorization"] == "[redacted]"
    dump = json.dumps(d) + json.dumps(c.get(f"{A}/apps/{a['id']}/audit").json())
    dump += (c.get(f"{A}/runs/{d['id']}/report.html").text + json.dumps(c.get(f"{A}/apps/{a['id']}/export").json()))
    token = __import__("base64").b64encode(f"{api_site.BASIC[0]}:{api_site.BASIC[1]}".encode()).decode()
    assert api_site.BASIC[1] not in dump and token not in dump
    db_file = next(paths.root.rglob("*.db"), None) or next(paths.root.rglob("*.sqlite*"), None)
    if db_file is not None:
        raw = db_file.read_bytes()
        assert api_site.BASIC[1].encode() not in raw and token.encode() not in raw
    for log in paths.root.rglob("*.log"):
        text = log.read_text(errors="replace")
        assert api_site.BASIC[1] not in text and token not in text


def test_application_needs_authorization_and_valid_target(env, site):
    c, *_ = env
    url, _st = site
    r = c.post(f"{A}/apps", json={"name": "X", "config": {"base_url": url}})
    assert r.status_code == 422
    r = c.post(f"{A}/apps", json={"name": "X", "config": {"base_url": "ftp://x"}, "authorized": True})
    assert r.status_code == 422
    r = c.post(f"{A}/apps", json={"name": "X", "config": {"base_url": "http://user:pw@host"}, "authorized": True})
    assert r.status_code == 422
    # TLS verification cannot be switched off for Production
    r = c.post(f"{A}/apps", json={"name": "X", "environment": "Production", "authorized": True,
                                 "config": {"base_url": url, "tls": {"verify": False}}})
    assert r.status_code == 422 and r.json()["error_code"] == "API_TLS_PRODUCTION"


def test_host_ip_and_port_configuration(env, site):
    c, *_ = env
    url, st = site
    port = int(url.rsplit(":", 1)[1])
    a = new_app(c, None, config={"protocol": "http", "host": "127.0.0.1", "port": port, "base_path": ""})
    assert a["base_url"] == f"http://127.0.0.1:{port}"
    add_test(c, a["id"], method="GET", path="/api/orders")
    d = run(c, a["id"])
    assert d["status"] == "PASSED", d["results"]


@pytest.mark.skipif(not socket.has_ipv6, reason="no IPv6 on this machine")
def test_ipv6_address(env):
    c, *_ = env
    try:
        srv, url, st = api_site.start("::1")
    except OSError:
        pytest.skip("IPv6 loopback not available")
    try:
        port = int(url.rsplit(":", 1)[1])
        a = new_app(c, None, config={"protocol": "http", "host": "::1", "port": port})
        assert a["base_url"] == f"http://[::1]:{port}"
        add_test(c, a["id"], method="GET", path="/api/orders")
        assert run(c, a["id"])["status"] == "PASSED"
    finally:
        srv.shutdown()


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is needed to make the test certificates")
def test_https_with_uploaded_ca_and_verification(env, tmp_path):
    c, *_ = env
    certs = api_site.make_cert(tmp_path / "pki")
    srv, url, st = api_site.start("127.0.0.1", tls=(certs["cert"], certs["key"]))
    try:
        a = new_app(c, url)
        add_test(c, a["id"], method="GET", path="/api/orders")
        d = run(c, a["id"])  # the private CA is not trusted yet: a clear TLS failure, not a pass
        assert d["status"] == "FAILED"
        res = d["results"][0]
        assert "certificate" in (res["error"] or "").lower() or "tls" in (res["error"] or "").lower()
        with open(certs["ca"], "rb") as fh:
            r = c.post(f"{A}/apps/{a['id']}/certificates", params={"kind": "ca_file", "name": "ca.pem"}, content=fh.read(),
                       headers={"Content-Type": "application/octet-stream"})
        assert r.status_code == 200, r.text
        steps = {s["step"]: s for s in c.post(f"{A}/apps/{a['id']}/test-connection").json()["steps"]}
        assert steps["TLS certificate"]["status"] == "PASS"
        assert run(c, a["id"])["status"] == "PASSED"
    finally:
        srv.shutdown()


# ---- authentication -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mode,cfg,field,value", [
    ("bearer", {"type": "bearer"}, "token", api_site.BEARER),
    ("api_key", {"type": "api_key", "name": "X-API-Key", "in": "header"}, "api_key", api_site.API_KEY),
    ("oauth", {"type": "oauth2", "client_id": api_site.OAUTH[0], "token_url": "/oauth/token"}, "client_secret", api_site.OAUTH[1]),
])
def test_authentication_types(env, site, mode, cfg, field, value):
    c, *_ = env
    url, st = site
    st.auth = mode
    if mode == "oauth":
        cfg = {**cfg, "token_url": url + "/oauth/token"}
    a = new_app(c, url, cfg)
    t = add_test(c, a["id"], method="GET", path="/api/orders")
    d = run(c, a["id"])  # no secret stored yet: BLOCKED, nothing is sent without credentials
    assert d["results"][0]["status"] == "BLOCKED" and not [r for r in st.requests if r["path"] == "/api/orders"]
    secret(c, a["id"], field, value)
    d = run(c, a["id"], test_ids=[t["id"]])
    assert d["status"] == "PASSED", d["results"]
    assert value not in json.dumps(d)
    if mode == "oauth":
        assert "oauth-" not in json.dumps(d["results"][0]["request"])  # the issued token is redacted too
    secret(c, a["id"], field, value + "-wrong")
    d = run(c, a["id"], test_ids=[t["id"]])
    res = d["results"][0]
    assert res["status"] == "FAILED" and res["classification"]["classification"] == "authentication"
    assert value + "-wrong" not in json.dumps(d)


def test_api_key_in_query_is_redacted_from_url(env, site):
    c, *_ = env
    url, st = site
    st.auth = "none"
    a = new_app(c, url, {"type": "api_key", "name": "apikey", "in": "query"})
    secret(c, a["id"], "api_key", "query-key-998877")
    add_test(c, a["id"], method="GET", path="/api/orders")
    d = run(c, a["id"])
    assert any(r["query"].get("apikey") == "query-key-998877" for r in st.requests)
    assert "query-key-998877" not in json.dumps(d)


def test_basic_auth_failure_and_role_authorization(env, site):
    c, *_ = env
    url, st = site
    st.auth = "basic"
    a = new_app(c, url, {"type": "basic", "username": "qa"},
                config={"roles": [{"name": "nobody", "auth": {"type": "none"}}]})
    secret(c, a["id"], "password", api_site.BASIC[1])
    ok = add_test(c, a["id"], method="GET", path="/api/orders")
    denied = add_test(c, a["id"], name="no credentials are refused", method="GET", path="/api/orders", auth="role:nobody",
                      assertions=[{"type": "status", "expected": ["401", "403"]}])
    d = run(c, a["id"], test_ids=[ok["id"], denied["id"]])
    assert d["status"] == "PASSED", d["results"]
    auths = [r["headers"].get("Authorization") for r in st.requests if r["path"] == "/api/orders"]
    assert None in auths and any(x and x.startswith("Basic ") for x in auths)  # the role sent no credentials


# ---- OpenAPI / Swagger / imports ---------------------------------------------------------------------------------

SWAGGER2_YAML = """
swagger: "2.0"
info: {title: Legacy Orders, version: "2"}
host: legacy.local:8080
basePath: /v2
schemes: [http]
paths:
  /orders/{id}:
    get:
      operationId: getOrder
      parameters:
        - {name: id, in: path, required: true, type: integer}
      responses:
        "200":
          description: ok
          schema: {$ref: "#/definitions/Order"}
        "404": {description: missing}
  /customers:
    get:
      responses:
        "200":
          description: ok
          schema: {$ref: "common.yaml#/definitions/Customer"}
        "500":
          description: x
          schema: {$ref: "https://schemas.example.com/error.json"}
definitions:
  Order:
    type: object
    required: [id]
    properties: {id: {type: integer}, total: {type: number}}
"""


def test_swagger2_yaml_with_local_multi_file_and_unresolved_external_reference():
    spec = importers.import_openapi({"legacy.yaml": SWAGGER2_YAML}, "legacy.yaml")
    assert spec["version"].startswith("Swagger 2") or spec["version"].startswith("2")
    assert spec["base_urls"] == ["http://legacy.local:8080/v2"]
    ops = {f"{e['method']} {e['path']}": e for e in spec["endpoints"]}
    assert ops["GET /orders/{id}"]["responses"]["200"]["schema"]["required"] == ["id"]
    assert any("common.yaml" in u for u in spec["unresolved"])
    assert any("schemas.example.com" in u for u in spec["unresolved"])  # reported, never downloaded
    common = "definitions:\n  Customer:\n    type: object\n    properties: {name: {type: string}}\n"
    spec2 = importers.import_openapi({"legacy.yaml": SWAGGER2_YAML, "common.yaml": common}, "legacy.yaml")
    assert not any("common.yaml" in u for u in spec2["unresolved"])
    cust = {f"{e['method']} {e['path']}": e for e in spec2["endpoints"]}["GET /customers"]
    assert cust["responses"]["200"]["schema"]["properties"]["name"]["type"] == "string"


def test_spec_upload_bad_documents_are_refused_clearly():
    with pytest.raises(QAError):
        importers.import_openapi({"x.json": "{not json"}, "x.json")
    with pytest.raises(QAError):
        importers.import_openapi({"x.yaml": "a: &a [*a, *a]\n" + "b: &b [*a, *a, *a, *a]\n" * 3}, "x.yaml")
    bomb = "a: &a ['x','x','x','x','x','x','x','x','x']\n" + "".join(
        f"{chr(98 + i)}: &{chr(98 + i)} [*{chr(97 + i)},*{chr(97 + i)},*{chr(97 + i)},*{chr(97 + i)},*{chr(97 + i)},*{chr(97 + i)},*{chr(97 + i)},*{chr(97 + i)},*{chr(97 + i)}]\n"
        for i in range(8))
    with pytest.raises(QAError):  # billion laughs
        importers.import_openapi({"x.yaml": "openapi: 3.0.0\n" + bomb}, "x.yaml")


def test_curl_postman_and_python_imports_keep_no_secrets_and_never_execute(env, site, tmp_path):
    c, *_ = env
    url, st = site
    a = new_app(c, url)
    marker = tmp_path / "pwned.txt"
    curl = (f"curl -X POST '{url}/api/orders' -H 'Content-Type: application/json' -H 'Authorization: Bearer leaked-tok-1' "
            f"-H 'Cookie: sid=leaked-cookie' -u admin:leaked-pass -d '{{\"customer\":\"A\",\"amount\":5}}' -k; "
            f"touch {marker}")
    r = c.post(f"{A}/apps/{a['id']}/import", json={"kind": "curl", "text": curl})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1
    dump = json.dumps(r.json())
    assert "leaked-tok-1" not in dump and "leaked-cookie" not in dump and "leaked-pass" not in dump
    postman = {"info": {"name": "c", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
               "item": [{"name": "List", "request": {"method": "GET", "url": {"raw": url + "/api/orders?page=1"},
                                                     "header": [{"key": "X-Api-Key", "value": "leaked-key-2"}]}}]}
    r = c.post(f"{A}/apps/{a['id']}/import", json={"kind": "postman", "text": json.dumps(postman)})
    assert r.status_code == 200 and r.json()["created"] == 1 and "leaked-key-2" not in json.dumps(r.json())
    py = f"import httpx, os\nos.system('touch {marker.as_posix()}')\nhttpx.get('{url}/api/orders/1')\n"
    r = c.post(f"{A}/apps/{a['id']}/import", json={"kind": "python", "text": py})
    assert r.status_code == 200 and r.json()["created"] == 1
    assert not marker.exists()
    assert not st.requests  # importing sends nothing
    tests = c.get(f"{A}/apps/{a['id']}/tests").json()["tests"]
    assert all(t["status"] == "needs_review" for t in tests)  # imported expectations are reviewed by a person


# ---- generation details, schema, negative, chain, timing ---------------------------------------------------------

def test_contract_violation_and_server_error_are_classified_and_drafted(env, site):
    c, ado, rt = env
    url, st = site
    a = new_app(c, url)
    contract = add_test(c, a["id"], name="broken contract", method="GET", path="/api/broken", query=[{"name": "mode", "value": "contract"}],
                        assertions=[{"type": "status", "expected": ["200"]},
                                    {"type": "schema", "schema": {"type": "object", "required": ["items", "total"],
                                                                  "properties": {"total": {"type": "integer"},
                                                                                 "items": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "integer"}}}}}}}])
    server = add_test(c, a["id"], name="server error", method="GET", path="/api/broken")
    d = run(c, a["id"], test_ids=[contract["id"], server["id"]])
    res = {x["test_name"]: x for x in d["results"]}
    assert res["broken contract"]["classification"]["classification"] == "api_contract"
    assert res["server error"]["classification"]["classification"] == "application_defect"
    assert all(x["draft_id"] for x in res.values())
    draft = c.get(f"/api/defects/drafts/{res['server error']['draft_id']}").json()["draft"]
    assert draft["source"] == "api" and draft["status"] in ("draft", "needs_review")
    assert "500" in json.dumps(draft)
    # the same failure again: no second draft (duplicate prevention)
    d2 = run(c, a["id"], test_ids=[server["id"]])
    assert d2["results"][0]["draft_id"] in (None, draft["id"])
    drafts = c.get("/api/defects/drafts?source=api&status=open").json()
    items = drafts.get("drafts") or drafts.get("items") or []
    mine = [x for x in items if x["source"] == "api"]
    assert len(mine) == 2, mine  # still the two drafts of the first run
    assert next(x for x in mine if x["id"] == draft["id"])["occurrences"] >= 2


def test_draft_is_published_to_tfs_only_after_approval_with_real_work_item(env, site):
    c, ado, rt = env
    r = c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                 "make_active": True})
    assert r.status_code == 200, r.text
    url, st = site
    a = new_app(c, url)
    t = add_test(c, a["id"], name="server error", method="GET", path="/api/broken")
    did = run(c, a["id"], test_ids=[t["id"]])["results"][0]["draft_id"]
    before = set(ado.items)
    r = c.post(f"/api/defects/drafts/{did}/publish")
    assert r.status_code == 409 and r.json()["error_code"] == "DEFECT_NOT_APPROVED" and set(ado.items) == before
    assert c.post(f"/api/defects/drafts/{did}/approve").status_code == 200
    r = c.post(f"/api/defects/drafts/{did}/publish")
    assert r.status_code == 200, r.text
    got = r.json()["draft"]
    assert got["status"] == "created" and got["tfs_id"] in ado.items and str(got["tfs_id"]) in got["tfs_url"]
    body = json.dumps(ado.items[got["tfs_id"]])
    assert "/api/broken" in body and "API Testing" in body
    notes = [n["type"] if "type" in n else n.get("event") for n in c.get("/api/notifications?limit=200").json()["notifications"]]
    assert notes  # API run and defect notifications are recorded


def test_chain_extracts_variables_and_blocks_when_missing(env, site):
    c, *_ = env
    url, st = site
    a = new_app(c, url, config={"state_changes": "allowed"})
    create = add_test(c, a["id"], name="create", method="POST", path="/api/orders", content_type="application/json",
                      body={"customer": "Chain Co", "amount": 12},
                      assertions=[{"type": "status", "expected": ["201"]}, {"type": "jsonpath", "path": "$.customer", "op": "equals", "value": "Chain Co"}],
                      extract=[{"name": "orderId", "from": "body", "path": "$.id"}])
    read = add_test(c, a["id"], name="read back", method="GET", path="/api/orders/{{orderId}}",
                    assertions=[{"type": "status", "expected": ["200"]}, {"type": "jsonpath", "path": "$.amount", "op": "equals", "value": 12}])
    suite = c.post(f"{A}/apps/{a['id']}/suites", json={"name": "create then read", "test_ids": [create["id"], read["id"]], "chain": True}).json()["suite"]
    d = run(c, a["id"], suite_id=suite["id"], confirm_state_changes=True)
    assert d["status"] == "PASSED", d["results"]
    oid = next(r for r in st.requests if r["method"] == "POST")
    assert any(r["path"].startswith("/api/orders/") and r["method"] == "GET" for r in st.requests), oid
    d = run(c, a["id"], test_ids=[read["id"]])  # alone: the variable is not defined anywhere
    assert d["results"][0]["status"] == "FAILED" and "orderId" in d["results"][0]["error"]
    # the step that sets it fails: the next step is BLOCKED, not sent
    c.put(f"{A}/tests/{create['id']}", json={"body": {"customer": "", "amount": 0}})
    sent = len(st.requests)
    d = run(c, a["id"], suite_id=suite["id"], confirm_state_changes=True)
    res = {x["test_name"]: x for x in d["results"]}
    assert res["create"]["status"] == "FAILED" and res["read back"]["status"] == "BLOCKED"
    assert len(st.requests) == sent + 1


def test_negative_pagination_filter_and_data_rows(env, site):
    c, *_ = env
    url, st = site
    a = new_app(c, url)
    neg = add_test(c, a["id"], name="invalid limit", method="GET", path="/api/orders", query=[{"name": "limit", "value": "0"}],
                   assertions=[{"type": "status", "expected": ["400"]}, {"type": "jsonpath", "path": "$.error", "op": "exists"}])
    page = add_test(c, a["id"], name="page size", method="GET", path="/api/orders", query=[{"name": "limit", "value": "2"}],
                    assertions=[{"type": "status", "expected": ["200"]}, {"type": "jsonpath", "path": "$.items.length", "op": "lte", "value": 2}])
    filt = add_test(c, a["id"], name="filter", method="GET", path="/api/orders", query=[{"name": "status", "value": "{{status}}"}],
                    data_rows=[{"status": "new"}, {"status": "approved"}],
                    assertions=[{"type": "status", "expected": ["200"]}, {"type": "jsonpath", "path": "$.items[*].status", "op": "all_equal", "value": "{{status}}"}])
    d = run(c, a["id"], test_ids=[neg["id"], page["id"], filt["id"]])
    assert d["status"] == "PASSED", [(x["test_name"], x["assertions"]) for x in d["results"] if x["status"] != "PASSED"]
    assert len(by_name(d, "filter")) == 2  # one result per data row


def test_response_time_timeout_and_connection_failure(env, site):
    c, *_ = env
    url, st = site
    a = new_app(c, url)
    fast = add_test(c, a["id"], name="fast enough", method="GET", path="/api/slow", query=[{"name": "ms", "value": "300"}],
                    assertions=[{"type": "status", "expected": ["200"]}, {"type": "response_time", "max_ms": 100}])
    slow = add_test(c, a["id"], name="times out", method="GET", path="/api/slow", query=[{"name": "ms", "value": "3000"}], timeout_ms=500)
    d = run(c, a["id"], test_ids=[fast["id"], slow["id"]])
    res = {x["test_name"]: x for x in d["results"]}
    assert res["fast enough"]["status"] == "FAILED"
    assert res["fast enough"]["classification"]["classification"] == "performance"
    assert res["times out"]["status"] == "FAILED" and "time" in (res["times out"]["error"] or "").lower()
    # a port where nothing listens: a connection failure, classified as environment
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead = s.getsockname()[1]
    s.close()
    b = new_app(c, f"http://127.0.0.1:{dead}", name="Dead")
    steps = c.post(f"{A}/apps/{b['id']}/test-connection").json()
    assert steps["ok"] is False and steps["steps"][-1]["status"] == "FAIL"
    add_test(c, b["id"], method="GET", path="/api/orders")
    d = run(c, b["id"])
    assert d["results"][0]["status"] == "FAILED"
    assert d["results"][0]["classification"]["classification"] == "environment"


# ---- security: targets, SSRF, state changes ------------------------------------------------------------------------

def test_policy_refuses_unauthorized_hosts_own_port_metadata_and_bad_schemes():
    pol = safety.Policy(["127.0.0.1:9000", "*.corp.local", "10.1.0.0/24"], own_port=8765)
    assert pol.check_url("http://127.0.0.1:9000/x")["port"] == 9000
    for bad in ("http://127.0.0.1:8765/api/users", "http://localhost:8765/api/settings", "http://169.254.169.254/latest/meta-data",
                "http://10.2.0.5/", "file:///etc/passwd", "gopher://127.0.0.1:9000/", "http://user:pw@127.0.0.1:9000/",
                "http://example.org/"):
        with pytest.raises(QAError):
            pol.check_url(bad)
    with pytest.raises(QAError):  # whole networks larger than /16 are not accepted as targets (no scanning)
        safety.normalize_target("10.0.0.0/8")


def test_redirect_to_unauthorized_host_is_blocked(env, site):
    c, *_ = env
    url, st = site
    a = new_app(c, url)
    t = add_test(c, a["id"], method="GET", path="/redirect", query=[{"name": "to", "value": "http://127.0.0.1:8765/api/users"}])
    d = run(c, a["id"], test_ids=[t["id"]])
    assert d["results"][0]["status"] in ("FAILED", "BLOCKED")
    assert "authorized" in json.dumps(d["results"][0]).lower()


def test_state_changing_tests_need_confirmation_and_never_run_on_production_from_schedule(env, site):
    c, *_ = env
    url, st = site
    a = new_app(c, url)
    post = add_test(c, a["id"], name="create", method="POST", path="/api/orders", content_type="application/json",
                    body={"customer": "X", "amount": 3}, assertions=[{"type": "status", "expected": ["201"]}])
    d = run(c, a["id"], test_ids=[post["id"]])
    assert d["results"][0]["status"] == "BLOCKED" and not [r for r in st.requests if r["method"] == "POST"]
    d = run(c, a["id"], test_ids=[post["id"]], confirm_state_changes=True)
    assert d["results"][0]["status"] == "PASSED"
    p = new_app(c, url, name="Prod", environment="Production", config={"state_changes": "allowed"})
    pt = add_test(c, p["id"], name="create", method="POST", path="/api/orders", content_type="application/json",
                  body={"customer": "X", "amount": 3}, assertions=[{"type": "status", "expected": ["201"]}])
    out = c.app.state.runtime.apitesting.run(p["id"], test_ids=[pt["id"]], trigger="schedule", wait=True, confirm_state_changes=True)
    assert out["results"][0]["status"] == "BLOCKED"


def test_view_only_user_cannot_change_or_confirm(env, site):
    c, *_ = env
    url, st = site
    svc = c.app.state.runtime.apitesting
    a = new_app(c, url)
    viewer = auth.User(9, "viewer", "Viewer", "developer")
    tok = auth.set_current_user(viewer)
    try:
        with pytest.raises(QAError):
            svc.save_app({"name": "Y", "config": {"base_url": url}, "authorized": True})
        with pytest.raises(QAError):
            svc.run(a["id"], confirm_state_changes=True)
    finally:
        auth.reset_current_user(tok)


# ---- Python script --------------------------------------------------------------------------------------------------

def test_generated_pytest_httpx_script_runs_without_secrets_in_the_file(env, site):
    c, *_ = env
    url, st = site
    st.auth = "bearer"
    a = new_app(c, url, {"type": "bearer"})
    secret(c, a["id"], "token", api_site.BEARER)
    add_test(c, a["id"], name="list orders", method="GET", path="/api/orders",
             assertions=[{"type": "status", "expected": ["200"]}, {"type": "jsonpath", "path": "$.total", "op": "gte", "value": 1}])
    s = c.post(f"{A}/apps/{a['id']}/script/generate", json={}).json()
    src = s["current"]["source"]
    assert "import httpx" in src and "SKYGUARD_API_TOKEN" in src and api_site.BEARER not in src
    assert c.post(f"{A}/scripts/validate", json={"source": src}).json()["ok"] is True
    for bad in ("import subprocess\n", "import os\nos.system('dir')\n", "eval('1')\n", "open('x', 'w').write('y')\n"):
        assert c.post(f"{A}/scripts/validate", json={"source": src + "\n" + bad}).json()["ok"] is False, bad
    r = c.put(f"{A}/apps/{a['id']}/script", json={"source": src.replace("SKYGUARD_API_TOKEN", "X") + f"\nTOKEN = '{api_site.BEARER}'\n"})
    assert r.status_code == 422  # a stored secret value is refused in a saved script
    edited = c.put(f"{A}/apps/{a['id']}/script", json={"source": src + "\n# reviewed\n", "note": "comment"})
    assert edited.status_code == 200 and edited.json()["current"]["version"] == 2 and edited.json()["current"]["edited"]
    r = c.post(f"{A}/apps/{a['id']}/script/run")
    assert r.status_code == 200, r.text
    out = r.json()["run"]
    assert out["status"] == "PASSED", out["summary"]
    assert api_site.BEARER not in json.dumps(out)
    restored = c.post(f"{A}/apps/{a['id']}/script/restore/1").json()
    assert restored["current"]["version"] == 3


# ---- Scheduled Runs, agents, Integration Comparison -----------------------------------------------------------------

def test_scheduled_api_run_reports_counts_and_notifies(env, site):
    c, *_ = env
    url, st = site
    a = new_app(c, url)
    add_test(c, a["id"], method="GET", path="/api/orders")
    items = c.get("/api/schedules/scripts").json()["items"]
    mine = [i for i in items if i["id"] == a["id"]]
    assert mine and mine[0]["page"] == "API Testing" and mine[0]["test_type"] == "API Testing"
    r = c.post("/api/schedule-runs", json={"script_ids": [a["id"]], "name": "API now", "browsers": ["chromium"], "mode": "smart"})
    assert r.status_code in (200, 201, 202), r.text
    end = time.time() + 60
    while time.time() < end:
        hist = c.get("/api/schedules/history?limit=5").json()["items"]
        if hist and hist[0]["status"] not in ("RUNNING", "QUEUED"):
            break
        time.sleep(0.3)
    b = hist[0]
    assert b["status"] == "PASSED", b
    item = b["items"][0]
    assert item["mode"] == "api" and item["browser"] is None and item["counts"]["PASSED"] == 1
    events = json.dumps(c.get("/api/notifications?limit=200").json())
    assert "Scheduled run started" in events


def test_new_agents_are_listed_once_with_real_availability(env):
    c, *_ = env
    agents = c.get("/api/agents").json()
    items = agents.get("agents") or agents.get("items") or agents
    ids = [x["id"] if isinstance(x, dict) else x for x in items]
    for want in ("ai_operations", "integration_comparison", "api_testing", "api_failure_analysis", "api_contract",
                 "scheduled_execution"):
        assert ids.count(want) == 1, want
    assert len(ids) == len(set(ids))
    api = next(x for x in items if x["id"] == "api_testing")
    assert api["available"] is True
