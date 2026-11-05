"""23.3.24: record a performance script from a real browser journey. Requests to other hosts and static files are left
out, values the server hands out (a CSRF form field, a JSON access token) are correlated, the typed password becomes
a saved secret that is never in the script, and the recorded script runs in JMeter with no errors."""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

from qa_platform.performance import recording as R
from qa_platform.performance.service import _prop_escape
from tests import site_server

FINAL = ("COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "SKIPPED")


def _wait(client, run_id: str, timeout: float = 180) -> dict:
    deadline = time.time() + timeout
    run = {}
    while time.time() < deadline:
        run = client.get(f"/api/performance/runs/{run_id}").json()["run"]
        if run["state"] in FINAL:
            return run
        time.sleep(0.3)
    raise AssertionError(f"{run_id} stayed {run.get('state')}")


def _playwright_ready() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def _record(rt, site: str):
    rec = rt.performance.start_recording(f"{site}/perf/login", allowed_hosts=["127.0.0.1"], headless=True)
    r = rt.performance.recorder
    assert rec["active"] and r.id == rec["id"]
    r.mark("Log in")
    r.command("fill", ["#user", "tester"])
    r.command("fill", ["#pass", site_server.PERF_PASSWORD])
    r.command("click", "#go")
    r.command("wait_text", "orders: 3")
    r.command("wait", 10000)
    return r


@pytest.mark.skipif(not _playwright_ready(), reason="Playwright is not installed")
def test_recording_correlates_tokens_keeps_password_secret_and_runs(client, site):
    rt = client.app.state.runtime
    r = _record(rt, site)
    # A second recording cannot start while one is open
    assert client.post("/api/performance/recordings", json={"url": f"{site}/perf/login"}).status_code == 409
    cur = client.get("/api/performance/recordings/current").json()["recording"]
    assert cur["id"] == r.id and "Log in" in [s["name"] for s in cur["steps"]] and "password" in cur["secret_fields"]
    stopped = client.post(f"/api/performance/recordings/{r.id}/stop").json()["recording"]
    assert not stopped["active"]

    pv = client.post(f"/api/performance/recordings/{r.id}/preview", json={}).json()
    report = pv["report"]
    corr = {c["field"]: c for c in report["correlations"]}
    assert corr["csrf"]["extractor"] == "boundary"  # the hidden form field of the login page
    token = next(c for c in report["correlations"] if c["extractor"] == "json")
    assert token["variable"] == "access_token" and token["expression"] == "$.data.access_token"
    assert report["secrets"] == ["password"]
    assert site_server.PERF_PASSWORD not in json.dumps(pv)  # the typed password never leaves the recorder
    kept = [q for q in pv["requests"] if q["id"] not in {x["id"] for x in report["excluded"]}]
    assert {("POST", "/perf/login"), ("POST", "/perf/api/token"), ("GET", "/perf/api/orders")} <= {
        (q["method"], urlparse(q["url"]).path) for q in kept}

    env = client.post("/api/performance/environments", json={"name": "Local", "allowed_hosts": ["127.0.0.1"],
                                                             "max_users": 2}).json()["environment"]
    sc = client.post("/api/performance/scenarios", json={"name": "Recorded", "environment_id": env["id"]}).json()["scenario"]
    plan = client.post(f"/api/performance/recordings/{r.id}/script",
                       json={"name": "Login journey", "scenario_id": sc["id"]}).json()["plan"]
    assert rt.performance.recorder is None  # the recording is dropped from memory once saved
    assert plan["secret_names"] == ["password"] and plan["recording"]["kept"] >= 3
    view = client.get(f"/api/performance/plans/{plan['id']}/script").json()
    assert view["secrets"] == [{"name": "password", "set": True, "usage": "${__P(password)}"}]
    assert site_server.PERF_PASSWORD not in view["script"] and "${__P(password)}" in view["script"]
    assert "${access_token}" in view["script"] and "JSONPostProcessor" in view["script"]
    assert not view["problems"], view["problems"]
    assert client.get(f"/api/performance/plans/{plan['id']}/secrets").json()["items"][0]["set"] is True

    # Shorten the test, then run it for real when JMeter is installed
    work = view["script"].replace('<stringProp name="ThreadGroup.duration">60</stringProp>',
                                  '<stringProp name="ThreadGroup.duration">5</stringProp>')
    client.put(f"/api/performance/plans/{plan['id']}/script", json={"script": work, "base_version": 1})
    if not rt.performance.environment()["ready"]:
        pytest.skip("JMeter/Java not installed (set JMETER_HOME to run)")
    run_id = client.post(f"/api/performance/scenarios/{sc['id']}/run").json()["run_id"]
    run = _wait(client, run_id)
    assert run["state"] == "COMPLETED", run.get("state_reason") or run.get("error")
    labels = {x["label"]: x for x in run["metrics"]["requests"]}
    assert "Log in" in labels  # the step is a transaction
    assert run["metrics"]["overall"]["samples"] > 0 and run["metrics"]["overall"]["error_pct"] == 0
    check = next(c for c in run["checks"]["checks"] if c["id"] == "secrets")
    assert check["status"] == "PASS"
    folder = Path(rt.paths.performance)
    leftovers = [p for p in folder.rglob("*") if p.is_file() and p.name == "secrets.properties"]
    assert not leftovers  # the secrets file is deleted when JMeter ends
    for p in folder.rglob("*"):
        if p.is_file() and p.suffix in (".jmx", ".json", ".log", ".jtl", ".csv"):
            assert site_server.PERF_PASSWORD not in p.read_text("utf-8", "replace"), p

    # Without the saved value the pre-run check fails and says where to enter it
    rt.secrets.delete("perfsecret", f"{plan['id']}.password")
    chk = client.post(f"/api/performance/scenarios/{sc['id']}/check").json()
    sec = next(c for c in chk["checks"] if c["id"] == "secrets")
    assert sec["status"] == "FAIL" and "password" in sec["detail"] and sec["fix"]
    client.put(f"/api/performance/plans/{plan['id']}/secrets/password", json={"value": site_server.PERF_PASSWORD})
    sec = next(c for c in client.post(f"/api/performance/scenarios/{sc['id']}/check").json()["checks"]
               if c["id"] == "secrets")
    assert sec["status"] == "PASS"


def _har_entry(url, method="GET", status=200, body="", mime="text/html", rtype="document", page="p1", post=None,
               headers=None, t="2026-11-05T10:00:00.000Z"):
    return {"pageref": page, "startedDateTime": t, "time": 20, "_resourceType": rtype,
            "request": {"method": method, "url": url, "headers": headers or [],
                        **({"postData": {"mimeType": "application/x-www-form-urlencoded", "text": post}} if post else {})},
            "response": {"status": status, "headers": [{"name": "Content-Type", "value": mime}],
                         "content": {"mimeType": mime, "text": body}}}


def test_har_import_filters_other_hosts_and_static_files_and_correlates(client):
    csrf = "q8Zr3kLm0PxYvT2bN7sW1aQe"
    har = {"log": {"pages": [{"id": "p1", "title": "Shop login", "startedDateTime": "2026-11-05T10:00:00.000Z"}],
                   "entries": [
        _har_entry("https://shop.example.test/login",
                   body=f'<form><input type="hidden" name="csrf_token" value="{csrf}"></form>'),
        _har_entry("https://shop.example.test/app.css", mime="text/css", rtype="stylesheet",
                   t="2026-11-05T10:00:00.100Z"),
        _har_entry("https://www.google-analytics.com/collect?v=1", rtype="xhr", mime="text/plain",
                   t="2026-11-05T10:00:00.200Z"),
        _har_entry("https://shop.example.test/login", method="POST", status=302, post=f"user=a&csrf_token={csrf}",
                   t="2026-11-05T10:00:01.000Z"),
    ]}}
    r = client.post("/api/performance/plans/import-har", json={"har": har, "allowed_hosts": ["shop.example.test"],
                                                               "filename": "shop.har"})
    assert r.status_code == 201, r.text
    plan = r.json()["plan"]
    rep = plan["recording"]
    assert rep["kept"] == 2 and plan["name"] == "Shop login" and plan["source"] == "HAR shop.har"
    reasons = " ".join(x["reason"] for x in rep["excluded"]).lower()
    assert "static" in reasons and "host" in reasons
    assert [c["field"] for c in rep["correlations"]] == ["csrf_token"]
    script = client.get(f"/api/performance/plans/{plan['id']}/script").json()["script"]
    assert "${csrf_token}" in script and "google-analytics" not in script and "app.css" not in script
    # Nothing to keep, or not a HAR: clear errors
    bad = client.post("/api/performance/plans/import-har", json={"har": har, "allowed_hosts": ["other.test"]})
    assert bad.status_code == 409 and bad.json()["error_code"] == "RECORDING_EMPTY"
    bad = client.post("/api/performance/plans/import-har", json={"har": {"x": 1}, "allowed_hosts": ["a"]})
    assert bad.status_code == 422 and bad.json()["error_code"] == "HAR_INVALID"


def test_boundary_extractor_emulation_and_properties_escaping():
    body = '<input type="hidden" name="csrf" value="abc123XYZ"><input name="x" value="1">'
    found = R._boundaries(body, "abc123XYZ")
    assert found and R.boundary_matches(body, found[0], found[1])[0] == "abc123XYZ"
    assert _prop_escape("a=b:c#d!e\\f") == "a\\=b\\:c\\#d\\!e\\\\f"
    assert _prop_escape(" lead\ttab\n") == "\\ lead\\ttab\\n"
    assert _prop_escape("pässwörd€") == "p\\u00e4ssw\\u00f6rd\\u20ac"


def test_recording_endpoints_without_a_recording(client):
    assert client.get("/api/performance/recordings/current").json()["recording"] is None
    assert client.post("/api/performance/recordings/REC-x/stop").status_code == 404
    assert client.post("/api/performance/recordings", json={"url": "file:///etc/passwd"}).status_code == 422
    r = client.put("/api/performance/plans/PERF-none/secrets/password", json={"value": "x"})
    assert r.status_code == 404
