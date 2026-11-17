"""Extension and companion 1.2.0 (SkyGuard 23.10.0): the simple agent through the companion, the consequential
confirmation box, and Record for Integration Comparison (converted by the companion, imported in SkyGuard)."""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_companion_security import call, comp, paired  # noqa: F401,F811 - fixture

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "browser-extension" / "extension"


def ev(kind, url, **el):
    value = el.pop("value", None)
    secret = el.pop("secret", False)
    return {"kind": kind, "url": url, "value": value, "secret": secret, "element": {"secret": secret, **el}}


B = "http://rep.local"
EVENTS = [
    ev("fill", f"{B}/login", tag="input", label="User", value="analyst"),
    ev("fill", f"{B}/login", tag="input", label="Password", value="[secret]", secret=True),
    ev("click", f"{B}/login", tag="button", name="Sign in"),
    ev("click", f"{B}/", tag="a", name="Reports", href="/reports"),
    ev("click", f"{B}/reports", tag="a", name="Orders Summary", href="/reports/orders"),
    ev("select", f"{B}/reports/orders", tag="select", label="Region", id="rg", value="North"),
    ev("click", f"{B}/reports/orders", tag="button", name="Search"),
    ev("click", f"{B}/reports/orders?region=North&page=1", tag="a", name="2"),
    ev("click", f"{B}/reports/orders?region=North&page=2", tag="button", name="Delete selected"),
    ev("click", f"{B}/reports/orders?region=North&page=2", tag="a", name="SO-1020", href="/orders/SO-1020",
       table={"cells": ["SO-1020", "Customer 20"], "headers": ["Order No", "Customer"]}),
    {"kind": "navigate", "url": f"{B}/orders/SO-1020", "element": {}},
]


def test_120_features_are_still_there():
    # the version itself is checked in test_extension_130.py (1.3.0 keeps every 1.2.0 feature)
    js = (EXT / "background.js").read_text() + (EXT / "sidepanel.js").read_text()
    for needle in ("cmp-record-start", "cmp-record-stop", "/v1/comparison/recording", "/v1/simple/connect",
                   "ackBox", "need_credential"):
        assert needle in js, needle


def test_simple_agent_through_the_companion(comp):  # noqa: F811
    c, base, _ = comp
    auth, dev = paired(comp)
    st, out = call(base, "POST", "/v1/simple/connect", {"url": "http://fleetops.corp.local/login"}, headers=auth)
    assert st == 200 and out["created"] and out["application"]["name"] == "Fleetops", out
    st, again = call(base, "POST", "/v1/simple/connect", {"url": "http://fleetops.corp.local/trips"}, headers=auth)
    assert not again["created"] and again["application"]["id"] == out["application"]["id"]
    assert call(base, "POST", "/v1/simple/connect", {"url": "file:///C:/x"}, headers=auth)[0] == 400
    aid = out["application"]["id"]
    st, k = call(base, "GET", f"/v1/simple/knowledge/{aid}", headers=auth)
    assert st == 200 and k["tasks"] == [] and k["records"] == 0
    assert call(base, "GET", "/v1/simple/knowledge/nope", headers=auth)[0] == 404
    st, t = call(base, "GET", "/v1/simple/tasks", headers=auth)
    assert st == 200 and t["tasks"] == []
    # chat goes through the simple agent: nothing learned yet, and a password is still withheld
    st, r = call(base, "POST", "/v1/chat", {"text": "Create two trips for tomorrow", "app_id": aid}, headers=auth)
    assert st == 200 and r["reply"]["type"] == "no_workflows", r
    st, r = call(base, "POST", "/v1/chat", {"text": "password is Op#2026x-secret", "app_id": aid}, headers=auth)
    assert r["reply"]["type"] == "secret_intercepted"
    # viewers can read but not connect
    call(base, "POST", "/v1/users", {"username": "viewer3", "password": "Viewer-pass-3", "role": "viewer"}, headers=auth)
    tok = call(base, "POST", "/v1/auth/login", {"username": "viewer3", "password": "Viewer-pass-3"}, headers=dev)[1]
    viewer = {**dev, "Authorization": "Bearer " + tok["token"]}
    assert call(base, "GET", f"/v1/simple/knowledge/{aid}", headers=viewer)[0] == 200
    assert call(base, "POST", "/v1/simple/connect", {"url": "http://x.local/"}, headers=viewer)[0] == 403
    assert call(base, "POST", "/v1/comparison/recording", {"events": []}, headers=viewer)[0] == 403


def test_consequential_runs_need_the_acknowledgement(comp):  # noqa: F811
    c, base, _ = comp
    auth, _ = paired(comp)
    confirmed = []
    plans = {"r1": {"plan": {"stages": [{"workflow": "Authorize trip", "title": "Authorize trip"}]}},
             "r2": {"plan": {"stages": [{"workflow": "Create trip", "title": "Create trip"}]}}}
    c.agent.engine.run = lambda rid: plans[rid]
    c.agent.engine.confirm = lambda rid, h, **kw: confirmed.append(rid) or {"run": {"id": rid}}
    st, out = call(base, "POST", "/v1/runs/r1/confirm", {"preview_hash": "0" * 64}, headers=auth)
    assert st == 409 and out["error_code"] == "ACKNOWLEDGE_REQUIRED" and confirmed == []
    assert call(base, "POST", "/v1/runs/r1/confirm", {"preview_hash": "0" * 64, "acknowledge": True},
                headers=auth)[0] == 200
    assert call(base, "POST", "/v1/runs/r2/confirm", {"preview_hash": "0" * 64}, headers=auth)[0] == 200
    assert confirmed == ["r1", "r2"]


def test_record_for_integration_comparison(comp):  # noqa: F811
    c, base, _ = comp
    auth, _ = paired(comp)
    tables = [{"index": 0, "headers": ["Order No", "Customer"], "rows": [["SO-1016", "Customer 16"]] * 8}]
    st, out = call(base, "POST", "/v1/comparison/recording",
                   {"events": EVENTS, "start_url": f"{B}/login", "final_url": f"{B}/orders/SO-1020",
                    "tables": tables}, headers=auth)
    assert st == 200, out
    rec = out["recording"]
    assert rec["format"] == "skyguard-navigation-recording/1"
    assert [s.get("text") or s.get("label") for s in rec["steps"]] == ["Reports", "Orders Summary", "Region", "Search"]
    assert rec["filters"][0] == {"label": "Region", "control": "select", "value": "North", "type": "select"}
    assert rec["pagination_seen"] and rec["detail"]["url_template"] == f"{B}/orders/{{key}}"
    assert rec["tables"][0]["row_count"] == 8 and len(rec["tables"][0]["rows"]) == 5
    assert "analyst" not in json.dumps(rec["steps"]) and "[secret]" not in json.dumps(rec)
    assert any("read-only" in d["why"] for d in rec["dropped"])
    assert call(base, "POST", "/v1/comparison/recording", {"events": "x"}, headers=auth)[0] == 400
    audit = call(base, "GET", "/v1/audit", headers=auth)[1]["audit"]
    assert any(a["action"] == "comparison.recording" for a in audit)


def test_skyguard_imports_the_extension_recording(client):
    from skyguard_agent import navrec

    rec = {"format": navrec.FORMAT, **navrec.events_to_recording(EVENTS, start_url=f"{B}/login")}
    W = "/api/comparison/workspace"
    pid = client.post(W, json={"name": "Orders", "applications": ["Sales reports", "ERP"]}).json()
    aid = pid["applications"][0]["id"]
    client.patch(f"{W}/applications/{aid}", json={"url": B + "/", "auth_method": "none"})
    r = client.post(f"{W}/applications/{aid}/guided/import", json={"recording": rec})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["steps_text"] == "click Reports\nclick Orders Summary\nselect Region = North\nclick Search"
    assert out["auth_method"] == "manual" and out["saved"]["source"] == "browser-extension"
    g = client.get(f"{W}/applications/{aid}/guided").json()
    assert g["saved"]["detail"]["url_template"] == f"{B}/orders/{{key}}"
    # filters still editable without recording again
    up = client.put(f"{W}/applications/{aid}/guided", json={"filter_values": {"Region": "South"}}).json()
    assert "select Region = South" in up["steps_text"]
    # refused: a step that changes data, a secret, another site, not a recording
    bad = dict(rec, steps=rec["steps"] + [{"action": "click", "text": "Delete selected"}])
    assert client.post(f"{W}/applications/{aid}/guided/import", json={"recording": bad}).json()["error_code"] \
        == "WS_STEP_CHANGES_DATA"
    bad = dict(rec, steps=rec["steps"] + [{"action": "fill", "label": "Password", "value": "[secret]"}])
    assert client.post(f"{W}/applications/{aid}/guided/import", json={"recording": bad}).status_code == 422
    bad = dict(rec, detail={"url_template": "http://evil.example/{key}", "key_column": "Order No"})
    r = client.post(f"{W}/applications/{aid}/guided/import", json={"recording": bad})
    assert r.status_code == 422 and r.json()["error_code"] == "WS_RECORDING_OTHER_SITE"
    bad = dict(rec, steps=[{"action": "eval", "text": "x"}])
    assert client.post(f"{W}/applications/{aid}/guided/import", json={"recording": bad}).status_code == 422
    assert client.post(f"{W}/applications/{aid}/guided/import",
                       json={"recording": {"format": "other/9", "steps": []}}).status_code == 422
