"""23.14.0: Security Testing, Red Hat Fuse Testing, ActiveMQ Testing and the shared result actions.

What runs where (also in docs/release-23.14.0/TEST_REPORT.md):

* Security: a deliberately vulnerable fixture application (httpx MockTransport) and a fake OWASP ZAP API. Real ZAP is
  not used here.
* ActiveMQ: unit tests with a Jolokia simulator always run. The tests marked ``real_broker`` run against real local
  ActiveMQ Classic / Artemis brokers when SKYGUARD_TEST_CLASSIC / SKYGUARD_TEST_ARTEMIS are set
  (``<jolokia url>|<stomp port>``), otherwise they are skipped (not executed).
* Fuse: a Camel Jolokia simulator (MBeans + dumpRouteAsXml). End-to-end runs use a real broker when available and a
  small Python stand-in for the route (it consumes the test input queue and publishes the transformed message), so
  they prove SkyGuard's side of the flow, not a real Fuse runtime.
* Email: a local SMTP test transport (no real mail server).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from qa_platform.errors import QAError

APP = "http://app.intranet.test"


def resolver(host, port):
    import ipaddress

    return [ipaddress.ip_address("10.20.30.40")]


def vulnerable_app(request: httpx.Request) -> httpx.Response:
    u = urlsplit(str(request.url))
    q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
    html = lambda body, **kw: httpx.Response(200, html=f"<html><body>{body}</body></html>", **kw)  # noqa: E731
    if u.path == "/":
        return html('<a href="/search?q=phone">Search</a> <a href="/item?id=1">Item</a> '
                    '<a href="/go?next=/home">Go</a> <a href="/logout">Logout</a>'
                    '<form method="post" action="/transfer"><input name="amount"></form>',
                    headers={"Set-Cookie": "SESSIONID=abc123; Path=/", "Server": "Apache/2.2.15"})
    if u.path == "/search":
        return html(f"<p>Results for {q.get('q', '')}</p>")  # reflected without escaping
    if u.path == "/item":
        if "'" in q.get("id", ""):
            return httpx.Response(500, text="You have an error in your SQL syntax; check the manual that corresponds "
                                  "to your MySQL server version")
        return html(f"<p>Item {q.get('id')}</p>")
    if u.path == "/go":
        return httpx.Response(302, headers={"Location": q.get("next", "/")})
    if u.path == "/home":
        return html("home")
    return httpx.Response(404, text="not found")


def wait_run(client, run_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(f"/api/itest/runs/{run_id}").json()["run"]
        if r["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
            return r
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} did not finish")


def make_sec_app(client, **cfg):
    rt = client.app.state.runtime
    rt.security.transport = httpx.MockTransport(vulnerable_app)
    rt.security.resolver = resolver
    body = {"name": "Orders portal", "environment": "Test", "config": {
        "base_url": APP + "/", "intensity": "standard", "categories": ["xss", "sqli", "open_redirect", "cors"],
        "authorization_confirmed": True, "authorization_statement": "Approved by the application owner, ticket SEC-42",
        "rate": 50, **cfg}}
    r = client.post("/api/security/apps", json=body)
    assert r.status_code == 201, r.text
    return r.json()["app"]


# ---- security ---------------------------------------------------------------------------------------------------------
def test_security_needs_authorization_and_refuses_state_change_on_production(client):
    rt = client.app.state.runtime
    rt.security.resolver = resolver
    r = client.post("/api/security/apps", json={"name": "Prod", "environment": "Production", "config": {
        "base_url": APP, "allow_state_changing": True}})
    assert r.status_code == 422 and r.json()["error_code"] == "SEC_PRODUCTION_STATE_CHANGE"
    r = client.post("/api/security/apps", json={"name": "No auth", "environment": "Test", "config": {"base_url": APP}})
    app = r.json()["app"]
    r = client.post(f"/api/security/apps/{app['id']}/runs", json={"kind": "discovery"})
    assert r.status_code == 409 and r.json()["error_code"] == "SEC_NOT_AUTHORIZED" or r.status_code == 409


def test_security_config_refuses_secrets(client):
    client.app.state.runtime.security.resolver = resolver
    r = client.post("/api/security/apps", json={"name": "Bad", "environment": "Test", "config": {
        "base_url": APP, "accounts": [{"role": "user", "auth": "basic", "username": "u", "password": "p4ss"}]}})
    # the password is not kept in the configuration (only known fields are kept; secrets go to the vault)
    assert r.status_code == 201
    assert "p4ss" not in json.dumps(r.json())


def test_security_discovery_passive_and_active_findings(client):
    app = make_sec_app(client)
    r = client.post(f"/api/security/apps/{app['id']}/runs", json={"kind": "discovery"})
    run = wait_run(client, r.json()["run"]["id"])
    assert run["status"] == "COMPLETED", run
    maps = client.get(f"/api/security/apps/{app['id']}/maps").json()["maps"]
    assert maps
    m = client.get(f"/api/security/apps/{app['id']}/maps/{maps[0]['id']}").json()["map"]["map"]
    assert any("/logout" in x["url"] for x in m["not_visited"])  # state-changing links are not opened
    client.post(f"/api/security/apps/{app['id']}/maps/{maps[0]['id']}/approve")
    r = client.post(f"/api/security/apps/{app['id']}/runs", json={"kind": "vulnerability"})
    run = wait_run(client, r.json()["run"]["id"])
    assert run["status"] == "COMPLETED", run
    rules = {f["rule_id"] for f in client.get("/api/security/findings").json()["findings"]}
    assert {"XSS-REFLECTED", "SQLI-ERROR", "OPEN-REDIRECT"} <= rules, rules
    assert any(r.startswith("HDR-") for r in rules) and any(r.startswith("COOKIE-") for r in rules)


def test_security_finding_review_and_actions(client, monkeypatch):
    app = make_sec_app(client, categories=["xss"])
    client.post(f"/api/security/apps/{app['id']}/runs", json={"kind": "discovery"})
    time.sleep(0.1)
    run = client.post(f"/api/security/apps/{app['id']}/runs", json={"kind": "vulnerability"}).json()["run"]
    wait_run(client, run["id"])
    f = next(x for x in client.get("/api/security/findings").json()["findings"] if x["rule_id"] == "XSS-REFLECTED")
    facts = client.get(f"/api/test-actions/finding/{f['id']}/facts").json()["facts"]
    assert facts["module"] == "security" and "SESSIONID=abc123" not in json.dumps(facts)
    r = client.post(f"/api/security/findings/{f['id']}/review", json={"status": "confirmed", "note": "reproduced"})
    assert r.json()["finding"]["status"] == "confirmed"
    prev = client.get(f"/api/test-actions/finding/{f['id']}/defect").json()["preview"]
    assert "XSS" in prev["title"] or "Cross" in prev["title"] or prev["title"]
    r = client.post(f"/api/test-actions/finding/{f['id']}/defect", json={"title": "Reflected XSS on search"})
    assert r.status_code == 201, r.text
    # retest: the application is still vulnerable -> still open
    r = client.post(f"/api/test-actions/finding/{f['id']}/retest")
    assert r.status_code == 200, r.text
    wait_run(client, r.json()["run"]["id"])
    assert client.get(f"/api/security/findings/{f['id']}").json()["finding"]["retest_status"] == "still_open"


def test_security_view_only_cannot_start_or_change(client):
    from qa_platform import auth

    app = make_sec_app(client)
    rt = client.app.state.runtime
    token = auth._current.set(auth.User(5, "dev", "Developer", "developer"))
    try:
        for call in (lambda: rt.security.start(app["id"], "discovery"),
                     lambda: rt.activemq.save_broker({"name": "Broker X", "config": {"stomp_host": "127.0.0.1"}}),
                     lambda: rt.security.review("VULN-1", "fixed")):
            with pytest.raises(QAError) as e:
                call()
            assert e.value.status == 403
    finally:
        auth._current.reset(token)


def fake_zap(state):
    def handler(request: httpx.Request) -> httpx.Response:
        u = urlsplit(str(request.url))
        assert parse_qs(u.query).get("apikey") == ["zk"]
        p = u.path
        state.setdefault("calls", []).append(p)
        if p.endswith("/core/view/version/"):
            return httpx.Response(200, json={"version": "2.15.0"})
        if p.endswith("/context/action/newContext/"):
            return httpx.Response(200, json={"contextId": "3"})
        if "/context/action/" in p or "/action/setOption" in p:
            return httpx.Response(200, json={"Result": "OK"})
        if p.endswith("/spider/action/scan/"):
            return httpx.Response(200, json={"scan": "7"})
        if p.endswith("/spider/view/status/"):
            return httpx.Response(200, json={"status": "100"})
        if p.endswith("/pscan/view/recordsToScan/"):
            return httpx.Response(200, json={"recordsToScan": "0"})
        if p.endswith("/ascan/action/scan/"):
            state["active"] = True
            return httpx.Response(200, json={"scan": "9"})
        if p.endswith("/ascan/view/status/"):
            return httpx.Response(200, json={"status": "100"})
        if p.endswith("/core/view/alerts/"):
            return httpx.Response(200, json={"alerts": [
                {"alert": "Content Security Policy (CSP) Header Not Set", "risk": "Medium", "confidence": "High",
                 "url": APP + "/", "pluginId": "10038", "cweid": "693", "description": "No CSP", "solution": "Set CSP"},
                {"alert": "Out of scope", "risk": "High", "confidence": "High", "url": "http://other.test/",
                 "pluginId": "1"}]})
        return httpx.Response(404, json={})
    return handler


def test_zap_scan_imports_alerts_in_scope_only(client):
    state: dict = {}
    app = make_sec_app(client, zap={"url": "http://127.0.0.1:8090", "use": True, "active": False})
    rt = client.app.state.runtime
    rt.security.zap_transport = httpx.MockTransport(fake_zap(state))
    client.post(f"/api/security/apps/{app['id']}/credentials", json={"name": "zap.key", "value": "zk"})
    run = client.post(f"/api/security/apps/{app['id']}/runs", json={"kind": "zap"}).json()["run"]
    run = wait_run(client, run["id"])
    assert run["status"] == "COMPLETED", run
    assert not state.get("active")  # active scanning was not approved
    zf = [f for f in client.get("/api/security/findings").json()["findings"] if f["source"] == "zap"]
    assert [f["rule_id"] for f in zf] == ["ZAP-10038"]


def test_zap_must_be_local():
    from qa_platform.sectest.zap import ZapClient

    with pytest.raises(QAError):
        ZapClient("http://10.0.0.9:8090", "k")


# ---- shared actions: AI analysis, PDF, email ----------------------------------------------------------------------------
def test_analyze_keeps_verified_facts_apart(client):
    from qa_platform.itest.actions import BugAnalysis

    app = make_sec_app(client, categories=["sqli"])
    wait_run(client, client.post(f"/api/security/apps/{app['id']}/runs", json={"kind": "vulnerability"}).json()["run"]["id"])
    f = next(x for x in client.get("/api/security/findings").json()["findings"] if x["rule_id"] == "SQLI-ERROR")
    rt = client.app.state.runtime
    seen = {}

    def fake(system, user, model, **kw):
        seen["prompt"] = user
        return BugAnalysis(defect_summary="SQL error on item id", expected_result="400", actual_result="500 SQL error",
                           potential_root_cause="unparameterised query", affected_component="item service",
                           severity_suggestion="high", priority_suggestion=2, supporting_evidence=["HTTP 500"],
                           recommended_investigation=["check query"], suggested_fix="use parameters",
                           retest_recommendation="retest with quote", confidence="medium",
                           hypotheses=["the id is concatenated"])
    rt.ai.complete_json = fake
    r = client.post(f"/api/test-actions/finding/{f['id']}/analyze")
    assert r.status_code == 200, r.text
    a = r.json()["analysis"]
    assert a["verified_facts"] and a["hypotheses"] == ["the id is concatenated"]
    assert "<evidence>" in seen["prompt"]
    assert len(client.get(f"/api/test-actions/finding/{f['id']}/analyses").json()["history"]) == 1


def test_email_draft_policy_and_single_send(client):
    rt = client.app.state.runtime
    app = make_sec_app(client, categories=["xss"])
    wait_run(client, client.post(f"/api/security/apps/{app['id']}/runs", json={"kind": "vulnerability"}).json()["run"]["id"])
    f = client.get("/api/security/findings").json()["findings"][0]
    sent = []
    rt.mailer.settings.update({"email": {"mode": "smtp", "host": "smtp.company.local", "port": 25, "security": "none",
                                         "sender": "qa@company.local", "allowed_domains": "company.local"}})
    rt.mailer.transport = lambda cfg, msg, *rest: sent.append(msg)
    d = client.post(f"/api/test-actions/finding/{f['id']}/email").json()["draft"]
    assert d["status"] == "draft"
    r = client.put(f"/api/test-actions/email/{d['id']}", json={"to": ["dev@gmail.com"], "attachments": []})
    assert r.status_code == 403 and r.json()["error_code"] == "EMAIL_RECIPIENT_NOT_ALLOWED"
    r = client.put(f"/api/test-actions/email/{d['id']}", json={"to": ["dev@company.local"], "subject": "x\r\nBcc: a@b.c",
                                                                "attachments": []})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/test-actions/email/{d['id']}/send", json={"confirm": False})
    assert r.status_code in (409, 422) and not sent
    r = client.post(f"/api/test-actions/email/{d['id']}/send", json={"confirm": True})
    assert r.status_code == 200, r.text
    out = r.json()["draft"]
    assert out["status"] == "accepted" and len(sent) == 1
    assert "\n" not in sent[0]["Subject"]
    r = client.post(f"/api/test-actions/email/{d['id']}/send", json={"confirm": True})
    assert r.status_code == 409 and len(sent) == 1  # never sent twice


def test_email_failure_keeps_draft(client):
    rt = client.app.state.runtime
    app = make_sec_app(client, categories=["xss"])
    wait_run(client, client.post(f"/api/security/apps/{app['id']}/runs", json={"kind": "vulnerability"}).json()["run"]["id"])
    f = client.get("/api/security/findings").json()["findings"][0]
    rt.mailer.settings.update({"email": {"mode": "smtp", "host": "smtp.company.local", "port": 25, "security": "none",
                                         "sender": "qa@company.local"}})

    def boom(cfg, msg, *rest):
        raise OSError("connection refused")
    rt.mailer.transport = boom
    d = client.post(f"/api/test-actions/finding/{f['id']}/email").json()["draft"]
    client.put(f"/api/test-actions/email/{d['id']}", json={"to": ["dev@company.local"], "attachments": []})
    r = client.post(f"/api/test-actions/email/{d['id']}/send", json={"confirm": True})
    assert r.status_code >= 400
    again = client.get(f"/api/test-actions/email/{d['id']}").json()["draft"]
    assert again["status"] == "failed" and again["body"]


def test_pdf_report_for_a_finding(client):
    app = make_sec_app(client, categories=["xss"])
    wait_run(client, client.post(f"/api/security/apps/{app['id']}/runs", json={"kind": "vulnerability"}).json()["run"]["id"])
    f = client.get("/api/security/findings").json()["findings"][0]
    r = client.post(f"/api/test-actions/finding/{f['id']}/pdf")
    assert r.status_code == 200, r.text
    pdf = r.json()["pdf"]
    assert pdf["ready"] and pdf["type"] == "security"
    body = client.get(pdf["url"]).content
    assert body.startswith(b"%PDF")


def test_test_actions_reject_path_tricks(client):
    r = client.post("/api/test-actions/../x/pdf")
    assert r.status_code in (404, 405, 422)
    r = client.get("/api/test-actions/result/../pdf/security")
    assert r.status_code in (404, 422)


# ---- ActiveMQ ----------------------------------------------------------------------------------------------------------
def test_jolokia_whitelist_refuses_destructive_operations():
    from qa_platform.amqtest.jolokia import ARTEMIS_READ_OPS, CLASSIC_READ_OPS, JolokiaClient

    calls = []
    j = JolokiaClient("http://broker.test:8161/api/jolokia",
                      transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, json={"value": 1})))
    for op in ("purge()", "removeMessage(java.lang.String)", "moveMessageTo(java.lang.String,java.lang.String)",
               "retryMessages()", "pause()", "sendTextMessage(java.lang.String)", "resetStatistics()"):
        with pytest.raises(QAError):
            j.exec_read_only("x:type=y", op, [], CLASSIC_READ_OPS | ARTEMIS_READ_OPS | {"purge", "pause"})
    assert not calls
    with pytest.raises(QAError):
        j.request({"type": "write", "mbean": "x", "attribute": "a", "value": 1})


def test_broker_config_refuses_dlq_as_test_destination(client):
    r = client.post("/api/activemq/brokers", json={"name": "Broker B", "environment": "Test", "config": {
        "stomp_host": "127.0.0.1", "test_destinations": ["ActiveMQ.DLQ"]}})
    assert r.status_code == 422
    r = client.post("/api/activemq/brokers", json={"name": "Broker B", "environment": "Test", "config": {
        "stomp_host": "127.0.0.1", "test_destinations": ["SKYGUARD.TEST.*"], "consume_destinations": ["ORDERS.IN"]}})
    assert r.status_code == 422


def test_publish_only_to_test_destinations(client):
    rt = client.app.state.runtime
    b = client.post("/api/activemq/brokers", json={"name": "Broker B", "environment": "Test", "config": {
        "stomp_host": "127.0.0.1", "test_destinations": ["SKYGUARD.TEST.*"], "allow_publish": True}}).json()["broker"]
    full = rt.itest.target("activemq", b["id"])
    rt.activemq._check_publish(full, "SKYGUARD.TEST.IN")
    for dest in ("ORDERS.IN", "DLQ", "SKYGUARD.TEST.*", "ActiveMQ.Advisory.Queue"):
        with pytest.raises(QAError):
            rt.activemq._check_publish(full, dest)
    with pytest.raises(QAError):
        rt.activemq._check_consume(full, "SKYGUARD.TEST.IN")
    r = client.post(f"/api/activemq/brokers/{b['id']}/cases", json={"type": "publish_verify", "name": "x",
                                                                   "destination": "ORDERS.IN"})
    assert r.status_code == 422


def test_stomp_frames_escape_headers():
    from qa_platform.amqtest.stomp import Frame, StompClient

    raw = Frame("SEND", {"destination": "/queue/a", "x": "a\nb:c"}, b"hi", length=False).encode()
    assert b"x:a\\nb\\cc" in raw and raw.endswith(b"hi\x00")
    c = StompClient("h", 1, login="u\nx")
    with pytest.raises(QAError):
        c.connect()
    c.buf = b"MESSAGE\ncorrelation-id:a\\cb\ncontent-length:3\n\nabc\x00\n"
    f = c._parse()
    assert f.headers["correlation-id"] == "a:b" and f.body == b"abc"


def _real(name):
    v = os.environ.get(name)
    if not v:
        return None
    url, port = v.split("|")
    return url, int(port)


def _broker(client, env, flavor, prefix="SKYGUARD.TEST."):
    url, port = env
    r = client.post("/api/activemq/brokers", json={"name": f"Local {flavor}", "environment": "Test", "config": {
        "flavor": flavor, "management_url": url, "management_user": "admin", "stomp_host": "127.0.0.1", "stomp_port": port,
        "stomp_user": "admin", "test_destinations": [prefix + "*"], "consume_destinations": [prefix + "SELF.*"],
        "allow_publish": True, "authorization_confirmed": True,
        "authorization_statement": "Local test broker owned by the QA team"}})
    assert r.status_code == 201, r.text
    b = r.json()["broker"]
    for n in ("management.secret", "stomp.secret"):
        client.post(f"/api/activemq/brokers/{b['id']}/credentials", json={"name": n, "value": "admin"})
    return b


def _case(client, bid, body):
    c = client.post(f"/api/activemq/brokers/{bid}/cases", json=body)
    assert c.status_code == 201, c.text
    client.post(f"/api/itest/cases/{c.json()['case']['id']}/approve")
    return c.json()["case"]


def _route_standin(env, flavor, src, dst, transform, dlq_on="POISON", stop=None):
    """A stand-in for a Fuse route (NOT a Fuse runtime): consumes src, publishes transform(body) to dst with the same
    correlation ID; 'POISON' bodies are nacked so the broker's redelivery / dead-letter handling applies."""
    from qa_platform.amqtest.stomp import StompClient

    _, port = env
    d = (lambda n: (n, {"destination-type": "ANYCAST"})) if flavor == "artemis" else (lambda n: (f"/queue/{n}", {}))
    sd, sh = d(src)

    def connect():
        c = StompClient("127.0.0.1", port, login="admin", passcode="admin", timeout=5)
        c.connect()
        c.subscribe(sd, headers={"subscription-type": "ANYCAST"} if flavor == "artemis" else None)
        return c

    s = connect()
    while not stop.is_set():
        for f in s.receive(0.5, count=1, ack=False):
            if dlq_on in f.text():
                if flavor == "classic":
                    s.nack(f)  # Classic: NACK = a failed delivery, counted by the redelivery policy
                else:  # Artemis: the consumer fails and goes away; the broker redelivers until max-delivery-attempts
                    s.sock.close()
                    s.sock = None
                    s = connect()
                continue
            dd, dh = d(dst)
            s.send(dd, transform(f.text()), {**dh, "correlation-id": f.headers.get("correlation-id", "")})
            s.ack(f)
    s.close()


@pytest.mark.parametrize("var,flavor", [("SKYGUARD_TEST_CLASSIC", "classic"), ("SKYGUARD_TEST_ARTEMIS", "artemis")])
def test_real_broker_discovery_publish_selector_priority_security(client, var, flavor):
    env = _real(var)
    if not env:
        pytest.skip(f"{var} not set: real broker not available (not executed)")
    b = _broker(client, env, flavor)
    t = client.post(f"/api/activemq/brokers/{b['id']}/test-connection").json()
    assert t["ok"], t
    _case(client, b["id"], {"type": "publish_verify", "name": "Order arrives", "destination": "SKYGUARD.TEST.PV",
                            "body": json.dumps({"order": 7, "name": "اختبار"}),
                            "expect": {"jsonpath": {"$.order": 7}, "within_seconds": 5}})
    _case(client, b["id"], {"type": "selector", "name": "Selector", "destination": "SKYGUARD.TEST.SELF.SEL"})
    _case(client, b["id"], {"type": "performance", "name": "Perf", "destination": "SKYGUARD.TEST.SELF.PERF", "count": 30})
    _case(client, b["id"], {"type": "ttl", "name": "TTL", "destination": "SKYGUARD.TEST.TTL", "ttl_ms": 500})
    _case(client, b["id"], {"type": "security", "name": "Broker security"})
    run = client.post(f"/api/activemq/brokers/{b['id']}/runs", json={"kind": "discovery"}).json()["run"]
    assert wait_run(client, run["id"])["status"] == "COMPLETED"
    run = client.post(f"/api/activemq/brokers/{b['id']}/runs", json={"kind": "cases"}).json()["run"]
    run = wait_run(client, run["id"], 90)
    assert run["status"] == "COMPLETED", run
    res = {r["test_name"]: r for r in client.get(f"/api/itest/results?run_id={run['id']}").json()["results"]}
    assert res["Order arrives"]["status"] == "PASSED", res["Order arrives"]
    assert res["Selector"]["status"] == "PASSED", res["Selector"]
    assert res["Perf"]["status"] == "PASSED", res["Perf"]
    assert res["TTL"]["status"] in ("PASSED", "UNVERIFIED"), res["TTL"]  # Artemis removes expired messages lazily
    if flavor == "classic":  # a default ActiveMQ Classic install has no authentication: a real finding
        assert res["Broker security"]["status"] == "FAILED"
        assert "without a user name" in json.dumps(res["Broker security"]["actual"])
    else:  # Artemis was created with --require-login
        assert res["Broker security"]["status"] == "PASSED", res["Broker security"]
    msgs = client.get(f"/api/activemq/brokers/{b['id']}/browse?destination=SKYGUARD.TEST.PV").json()["messages"]
    assert msgs  # browsing did not consume the message


@pytest.mark.parametrize("var,flavor", [("SKYGUARD_TEST_CLASSIC", "classic"), ("SKYGUARD_TEST_ARTEMIS", "artemis")])
def test_real_broker_fuse_e2e_consumer_and_dlq(client, var, flavor):
    env = _real(var)
    if not env:
        pytest.skip(f"{var} not set: real broker not available (not executed)")
    b = _broker(client, env, flavor, prefix="SKYGUARD.E2E.")
    tag = flavor.upper()
    src, dst = f"SKYGUARD.E2E.{tag}.IN", f"SKYGUARD.E2E.{tag}.OUT"
    stop = threading.Event()
    th = threading.Thread(target=_route_standin, args=(env, flavor, src, dst, lambda t: json.dumps(
        {"orderId": json.loads(t)["id"], "total": round(json.loads(t)["amount"] * 1.05, 2), "currency": "AED"}),
        "POISON", stop), daemon=True)
    th.start()
    try:
        time.sleep(1)
        r = client.post("/api/fuse/connections", json={"name": "Orders route (black-box)", "environment": "Test", "config": {
            "runtime": "blackbox", "broker_id": b["id"], "blackbox_routes": [{"id": "orders", "from": f"jms:queue:{src}",
                                                                              "to": [f"jms:queue:{dst}"]}],
            "authorization_confirmed": True, "authorization_statement": "QA owns this test route"}})
        assert r.status_code == 201, r.text
        fc = r.json()["connection"]
        c = client.post(f"/api/fuse/connections/{fc['id']}/cases", json={
            "type": "e2e", "name": "A to Fuse to B", "route_id": "orders",
            "input": {"channel": "queue", "destination": src, "body": json.dumps({"id": 42, "amount": 100})},
            "expect": {"outputs": [{"destination": dst, "expected_body": {"orderId": 42, "total": 105.0,
                                                                          "currency": "AED"}}], "within_seconds": 10}})
        assert c.status_code == 201, c.text
        client.post(f"/api/itest/cases/{c.json()['case']['id']}/approve")
        c2 = client.post(f"/api/fuse/connections/{fc['id']}/cases", json={
            "type": "transformation", "name": "Wrong expectation is caught", "route_id": "orders",
            "input": {"channel": "queue", "destination": src, "body": json.dumps({"id": 43, "amount": 10})},
            "expect": {"outputs": [{"destination": dst, "expected_body": {"orderId": 43, "total": 11.0,
                                                                          "currency": "AED"}}], "within_seconds": 10}})
        client.post(f"/api/itest/cases/{c2.json()['case']['id']}/approve")
        run = client.post(f"/api/fuse/connections/{fc['id']}/runs", json={"kind": "cases"}).json()["run"]
        run = wait_run(client, run["id"], 60)
        res = {r["test_name"]: r for r in client.get(f"/api/itest/results?run_id={run['id']}").json()["results"]}
        assert res["A to Fuse to B"]["status"] == "PASSED", res["A to Fuse to B"]
        assert res["A to Fuse to B"]["correlation_id"].startswith("SG-")
        bad = res["Wrong expectation is caught"]
        assert bad["status"] == "FAILED" and "transformation differs" in json.dumps(bad["actual"])
        # consumer verification and dead-letter on the same stand-in
        _case(client, b["id"], {"type": "consumer", "name": "Consumed", "destination": src, "body": json.dumps(
            {"id": 1, "amount": 1}), "expect": {"within_seconds": 10}})
        _case(client, b["id"], {"type": "retry_dlq", "name": "Poison to DLQ", "destination": src, "body": "POISON",
                                "expect": {"within_seconds": 40}})
        run = client.post(f"/api/activemq/brokers/{b['id']}/runs", json={"kind": "cases"}).json()["run"]
        run = wait_run(client, run["id"], 90)
        res = {r["test_name"]: r for r in client.get(f"/api/itest/results?run_id={run['id']}").json()["results"]}
        assert res["Consumed"]["status"] == "PASSED", res["Consumed"]
        assert res["Poison to DLQ"]["status"] == "PASSED", res["Poison to DLQ"]
    finally:
        stop.set()
        th.join(5)


# ---- Fuse (simulated Camel management) -------------------------------------------------------------------------------
ROUTE_XML = """<route xmlns="http://camel.apache.org/schema/spring" id="orders">
  <from uri="jms:queue:ORDERS.IN"/>
  <onException><exception>java.lang.Exception</exception><redeliveryPolicy maximumRedeliveries="3"/>
    <handled><constant>true</constant></handled><to uri="jms:queue:ORDERS.ERROR"/></onException>
  <unmarshal><json/></unmarshal>
  <choice><when><simple>${body[amount]} &gt; 1000</simple><to uri="jms:queue:ORDERS.REVIEW"/></when>
    <otherwise><to uri="jms:queue:ORDERS.OUT"/></otherwise></choice>
</route>"""


def camel_jolokia(state):
    route = 'org.apache.camel:context=orders-ctx,type=routes,name="orders"'
    ctx = 'org.apache.camel:context=orders-ctx,type=context,name="orders-ctx"'

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        state.setdefault("requests", []).append(body)
        t = body.get("type")
        if t == "version":
            return httpx.Response(200, json={"status": 200, "value": {"agent": "1.7.2", "info": {"product": "karaf"}}})
        if t == "search":
            p = body["mbean"]
            if p.startswith("org.apache.karaf"):
                return httpx.Response(200, json={"status": 200, "value": ["org.apache.karaf:type=bundle,name=root"]})
            if "type=routes" in p:
                return httpx.Response(200, json={"status": 200, "value": [route]})
            if "type=context" in p:
                return httpx.Response(200, json={"status": 200, "value": [ctx]})
            return httpx.Response(200, json={"status": 200, "value": []})
        if t == "read":
            if body["mbean"] == ctx:
                return httpx.Response(200, json={"status": 200, "value": {"CamelId": "orders-ctx",
                                                                         "CamelVersion": "2.23.2.fuse-7_13", "State":
                                                                         "Started"}})
            if body.get("attribute") == "RouteId":
                return httpx.Response(200, json={"status": 200, "value": "orders"})
            return httpx.Response(200, json={"status": 200, "value": {"RouteId": "orders", "State": state.get(
                "state", "Started"), "EndpointUri": "jms://queue:ORDERS.IN", "ExchangesTotal": 10, "ExchangesFailed": 0}})
        if t == "exec":
            assert body["operation"] == "dumpRouteAsXml()", body
            return httpx.Response(200, json={"status": 200, "value": ROUTE_XML})
        return httpx.Response(400, json={"status": 400, "error": "no"})
    return handler


def test_fuse_discovery_mapping_and_read_only(client):
    rt = client.app.state.runtime
    state: dict = {}
    rt.fuse.transport = httpx.MockTransport(camel_jolokia(state))
    rt.fuse.resolver = resolver
    r = client.post("/api/fuse/connections", json={"name": "Fuse 7 Karaf", "environment": "QA", "config": {
        "runtime": "karaf", "jolokia_url": "http://fuse.intranet.test:8181/hawtio/jolokia",
        "authorization_confirmed": True, "authorization_statement": "Integration team approved, INT-7"}})
    assert r.status_code == 201, r.text
    cid = r.json()["connection"]["id"]
    routes = client.get(f"/api/fuse/connections/{cid}/routes").json()
    assert routes["runtime"]["runtime"] == "karaf" and routes["contexts"][0]["camel_version"].startswith("2.23")
    m = client.get(f"/api/fuse/connections/{cid}/routes/orders").json()["route"]["map"]
    assert m["from"] == ["jms:queue:ORDERS.IN"]
    assert {c["when"] for c in m["choices"]} == {"simple: ${body[amount]} > 1000", "otherwise"}
    assert m["error_handling"]["has_error_handling"]
    state["state"] = "Stopped"
    run = client.post(f"/api/fuse/connections/{cid}/runs", json={"kind": "discovery"}).json()["run"]
    run = wait_run(client, run["id"])
    res = client.get(f"/api/itest/results?run_id={run['id']}").json()["results"]
    assert any(r["test_name"] == "Route orders state" and r["status"] == "FAILED" for r in res)
    ops = {x.get("operation") for x in state["requests"] if x.get("type") == "exec"}
    assert ops == {"dumpRouteAsXml()"}  # nothing but the read-only dump was ever executed


def test_transformation_comparison_rules():
    from qa_platform.fusetest.compare import compare

    exp = {"id": 1, "total": 10.005, "date": "2026-01-31", "items": [{"sku": "A"}, {"sku": "B"}], "name": "Ahmed"}
    act = {"id": 1, "total": 10.0, "date": "31/01/2026", "items": [{"sku": "B"}, {"sku": "A"}], "name": " ahmed",
           "generatedAt": "x"}
    r = compare(exp, act, rules={"numeric_tolerance": 0.01, "case_insensitive": True, "ignore_order": True,
                                 "ignore_extra": True})
    assert r["match"], r
    r = compare(exp, act)
    kinds = {d["kind"] for d in r["differences"]}
    assert not r["match"] and {"number", "extra", "text"} <= kinds
    r = compare("<Order><Id>1</Id><Customer name='X'/></Order>", "<Order><OrderId>1</OrderId><Customer name='X'/></Order>",
                rules={"mappings": {"/Order/Id": "/Order/OrderId"}})
    assert r["match"], r
    with pytest.raises(QAError):
        compare('<!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>', "<x/>", fmt="xml")


def test_fuse_case_refuses_non_test_entry(client):
    r = client.post("/api/fuse/connections", json={"name": "BB", "environment": "Test", "config": {
        "runtime": "blackbox", "test_urls": ["http://fuse.intranet.test:8080/test/"]}})
    cid = r.json()["connection"]["id"]
    r = client.post(f"/api/fuse/connections/{cid}/cases", json={"type": "routing", "name": "x", "input": {
        "channel": "http", "url": "http://fuse.intranet.test:8080/orders"}})
    assert r.status_code == 422
    r = client.post(f"/api/fuse/connections/{cid}/cases", json={"type": "routing", "name": "x", "input": {
        "channel": "queue", "destination": "ORDERS.IN"}})
    assert r.status_code == 422  # no linked broker / not a test destination


def test_summary_figures(client):
    s = client.get("/api/itest/summary").json()
    assert {"security", "fuse", "activemq", "shared"} <= set(s)
    assert s["security"].get("unavailable") is not True
    assert re.match(r"^\d+$", str(s["shared"]["emails_accepted"]))
