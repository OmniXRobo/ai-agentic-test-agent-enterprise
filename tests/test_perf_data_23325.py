"""23.3.25: test data for performance scripts. CSV import keeps leading zeros and Unicode, password columns are kept
as secrets (never shown, exported or left in a run folder), values are checked against column types, a data set is
bound to request fields (JSON, form, query, header), the pre-run checks catch missing columns and bad values, and a
real JMeter run logs every user in with their own row."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from qa_platform.performance import datasets as D
from qa_platform.performance import script as S
from tests import site_server

FINAL = ("COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "SKIPPED")

CSV = ("username,password,account_id,display_name\n"
       "user001,Pässwörd1!,00123,Zoë Müller\n"
       "user002,\"p,a\"\"ss2\",00456,東京 太郎\n"
       "user003,pass 3,07890,O'Brien\n")


def _wait(client, run_id: str, timeout: float = 180) -> dict:
    deadline = time.time() + timeout
    run = {}
    while time.time() < deadline:
        run = client.get(f"/api/performance/runs/{run_id}").json()["run"]
        if run["state"] in FINAL:
            return run
        time.sleep(0.3)
    raise AssertionError(f"{run_id} stayed {run.get('state')}")


def jmx(port: str, users: int = 3, duration: int = 5) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3"><hashTree>
<TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="Data driven"/><hashTree>
<ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="Users">
  <stringProp name="ThreadGroup.on_sample_error">continue</stringProp>
  <elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" testclass="LoopController">
    <stringProp name="LoopController.loops">-1</stringProp><boolProp name="LoopController.continue_forever">false</boolProp></elementProp>
  <stringProp name="ThreadGroup.num_threads">{users}</stringProp><stringProp name="ThreadGroup.ramp_time">0</stringProp>
  <boolProp name="ThreadGroup.scheduler">true</boolProp><stringProp name="ThreadGroup.duration">{duration}</stringProp>
  <stringProp name="ThreadGroup.delay">0</stringProp></ThreadGroup><hashTree>
  <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="Login">
    <boolProp name="HTTPSampler.postBodyRaw">true</boolProp>
    <elementProp name="HTTPsampler.Arguments" elementType="Arguments"><collectionProp name="Arguments.arguments">
      <elementProp name="" elementType="HTTPArgument"><boolProp name="HTTPArgument.always_encode">false</boolProp>
        <stringProp name="Argument.value">{{"username": "user001", "password": "x", "display_name": "Zoë Müller", "remember": true}}</stringProp>
        <stringProp name="Argument.metadata">=</stringProp></elementProp></collectionProp></elementProp>
    <stringProp name="HTTPSampler.domain">127.0.0.1</stringProp><stringProp name="HTTPSampler.port">{port}</stringProp>
    <stringProp name="HTTPSampler.protocol">http</stringProp><stringProp name="HTTPSampler.path">/perf/api/login</stringProp>
    <stringProp name="HTTPSampler.method">POST</stringProp><stringProp name="HTTPSampler.contentEncoding">UTF-8</stringProp>
  </HTTPSamplerProxy><hashTree>
    <HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="Headers"><collectionProp name="HeaderManager.headers">
      <elementProp name="" elementType="Header"><stringProp name="Header.name">Content-Type</stringProp><stringProp name="Header.value">application/json; charset=UTF-8</stringProp></elementProp>
      <elementProp name="" elementType="Header"><stringProp name="Header.name">X-Account</stringProp><stringProp name="Header.value">1</stringProp></elementProp>
    </collectionProp></HeaderManager><hashTree/>
  </hashTree>
  <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="Account">
    <elementProp name="HTTPsampler.Arguments" elementType="Arguments"><collectionProp name="Arguments.arguments"/></elementProp>
    <stringProp name="HTTPSampler.domain">127.0.0.1</stringProp><stringProp name="HTTPSampler.port">{port}</stringProp>
    <stringProp name="HTTPSampler.protocol">http</stringProp><stringProp name="HTTPSampler.path">/perf/api/account?id=1&amp;view=full</stringProp>
    <stringProp name="HTTPSampler.method">GET</stringProp>
  </HTTPSamplerProxy><hashTree/>
</hashTree></hashTree></hashTree></jmeterTestPlan>"""


def _import(client, text: str = CSV, **kw) -> dict:
    r = client.post("/api/performance/datasets/import", json={"csv": text, "filename": "users.csv", **kw})
    assert r.status_code == 201, r.text
    return r.json()["dataset"]


# ---- data sets -------------------------------------------------------------------------------------------------

def test_csv_import_keeps_leading_zeros_unicode_and_hides_secrets(client):
    ds = _import(client)
    types = {c["name"]: c["type"] for c in ds["columns"]}
    assert types == {"username": "text", "password": "secret", "account_id": "text", "display_name": "text"}
    assert any("leading zeros" in n for n in ds["import"]["notes"])
    assert [r["values"] for r in ds["rows"]] == [["user001", None, "00123", "Zoë Müller"],
                                                ["user002", None, "00456", "東京 太郎"],
                                                ["user003", None, "07890", "O'Brien"]]
    assert all(ds["secret_set"][str(r["id"])] == ["password"] for r in ds["rows"])
    page = json.dumps(client.get(f"/api/performance/datasets/{ds['id']}").json(), ensure_ascii=False)
    assert "Pässwörd1!" not in page and 'p,a"ss2' not in page  # secret values never leave the store
    assert "Pässwörd1!" not in json.dumps(client.get("/api/performance/datasets").json(), ensure_ascii=False)
    exported = client.get(f"/api/performance/datasets/{ds['id']}/csv")
    assert exported.headers["content-type"].startswith("text/csv")
    text = exported.content.decode("utf-8-sig")
    assert text.splitlines()[1] == "user001,,00123,Zoë Müller" and "Pässwörd" not in text
    # Windows-1252 files (saved by older Excel) are read too, and say so
    import base64
    legacy = "name;city\nRené;Köln\n".encode("cp1252")
    ds2 = _import(client, "base64:" + base64.b64encode(legacy).decode())
    assert ds2["rows"][0]["values"] == ["René", "Köln"] and "1252" in ds2["import"]["encoding"]
    assert ds2["import"]["delimiter"] == ";"


def test_types_are_checked_and_edits_keep_secrets(client, rt):
    ds = client.post("/api/performance/datasets", json={
        "name": "Orders", "columns": [{"name": "qty", "type": "integer"}, {"name": "email", "type": "email"},
                                      {"name": "day", "type": "date"}, {"name": "pin", "type": "secret"}],
        "rows": [["2", "a@example.test", "2026-11-05", "1234"], ["two", "nope", "2026-02-30", None],
                 ["3", "b@example.test", "2026-11-06", "multi\nline"]]}).json()["dataset"]
    msgs = " ".join(p["message"] for p in ds["problems"])
    assert "qty: 1 value(s) are not whole number (rows 2)" in msgs
    assert "email: 1 value(s)" in msgs and "day: 1 value(s)" in msgs
    assert "pin: empty in rows 2 (no secret value saved)" in msgs
    # Edit: rename a column and change a value; a secret cell sent as null keeps its saved value
    rows = [{"id": r["id"], "values": list(r["values"])} for r in ds["rows"]]
    rows[1]["values"][0] = "5"
    rows[2]["values"][3] = "4321"
    cols = [{"name": "quantity", "type": "integer", "from": "qty"}] + [
        {"name": c["name"], "type": c["type"], "from": c["name"]} for c in ds["columns"][1:]]
    r = client.put(f"/api/performance/datasets/{ds['id']}", json={"name": "Orders", "columns": cols, "rows": rows,
                                                                   "base_version": ds["version"]})
    assert r.status_code == 200, r.text
    ds2 = r.json()["dataset"]
    assert ds2["columns"][0]["name"] == "quantity" and ds2["rows"][1]["values"][0] == "5"
    m = rt.performance.datasets.materialize(ds["id"], ["pin", "quantity"])
    assert m["text"].splitlines() == ["pin,quantity", "1234,2", ",5", "4321,3"] and m["has_secrets"]
    assert m["info"]["sha256"] is None  # no fingerprint of secret data in config.json
    # A stale save is refused
    again = client.put(f"/api/performance/datasets/{ds['id']}", json={"name": "Orders", "columns": cols,
                                                                       "rows": rows, "base_version": ds["version"]})
    assert again.status_code == 409 and again.json()["error_code"] == "DATASET_CONFLICT"
    bad = client.post("/api/performance/datasets", json={"name": "x", "columns": [{"name": "1st"}]})
    assert bad.status_code == 422


def test_generator_rules_append_and_ai_without_a_model(client):
    ds = client.post("/api/performance/datasets", json={"name": "Gen", "columns": [
        {"name": "user", "type": "text"}, {"name": "mail", "type": "email"}, {"name": "age", "type": "integer"},
        {"name": "plan", "type": "text"}, {"name": "password", "type": "secret"}]}).json()["dataset"]
    rules = {"user": {"rule": "sequence", "prefix": "load", "width": 5},
             "mail": {"rule": "email", "prefix": "load", "domain": "example.test"},
             "age": {"rule": "range", "min": 18, "max": 90}, "plan": {"rule": "list", "values": "basic, pro"},
             "password": {"rule": "fixed", "value": "Load-Pass-1"}}
    out = client.post(f"/api/performance/datasets/{ds['id']}/generate", json={"rules": rules, "count": 25,
                                                                              "seed": 7}).json()["dataset"]
    assert out["rows_total"] == 25 and out["rows"][0]["values"][:2] == ["load00001", "load0001@example.test"]
    assert not [p for p in out["problems"] if p["level"] == "FAIL"]
    assert all(r["values"][4] is None for r in out["rows"]) and all(out["secret_set"].values())
    more = client.post(f"/api/performance/datasets/{ds['id']}/generate", json={"rules": rules, "count": 5}).json()
    assert more["dataset"]["rows_total"] == 30 and more["dataset"]["rows"][25]["values"][0] == "load00026"
    r = client.post(f"/api/performance/datasets/{ds['id']}/generate-ai", json={"count": 5})
    assert r.status_code in (502, 503) and r.json()["error_code"] != "INTERNAL_ERROR"  # no AI model in tests


def test_ai_rows_are_checked_against_the_column_types(client, rt):
    class FakeAI:
        def complete_json(self, system, user, model, **kw):
            assert "example.test" in system and "secret" not in json.loads(user)["columns"].__repr__()
            return model(rows=[["ana", "31"], ["bo", "not a number"], ["cy", "45"]])

        def config(self):
            return {"model": "fake"}
    rt.performance.datasets.ai = FakeAI()
    ds = client.post("/api/performance/datasets", json={"name": "AI", "columns": [
        {"name": "name"}, {"name": "age", "type": "integer"}, {"name": "token", "type": "secret"}]}).json()["dataset"]
    out = client.post(f"/api/performance/datasets/{ds['id']}/generate-ai", json={"count": 3,
                                                                                 "description": "customers"}).json()
    assert out["dataset"]["ai"] == {"added": 2, "rejected": 1, "model": "fake"}
    assert [r["values"][:2] for r in out["dataset"]["rows"]] == [["ana", "31"], ["cy", "45"]]


def test_csv_parser_edge_cases():
    with pytest.raises(Exception) as e:
        D.parse_csv("a,b\n1,2\n3\n")
    assert "line" in str(e.value.message).lower() and "3" in e.value.message
    p = D.parse_csv("First Name,Zip\nAnn,01234\n\n")
    assert [c["name"] for c in p["columns"]] == ["First_Name", "Zip"] and p["columns"][1]["type"] == "text"
    assert D.infer_type("amount", ["1.50", "2"])[0] == "number" and D.infer_type("id", ["7", "12"])[0] == "integer"
    assert D.to_csv(["a"], [['he said "hi", ok']]) == 'a\n"he said ""hi"", ok"\n'


# ---- binding -------------------------------------------------------------------------------------------------

def test_bind_json_form_query_and_header_fields():
    text = jmx("1")
    fields = S.fields(text)
    by = {(f["sampler"], f["kind"], f["key"]): f for f in fields}
    assert by[("Login", "json", "$.username")]["value"] == "user001"
    assert by[("Login", "json", "$.remember")]["value"] == "true"
    assert by[("Login", "header", "X-Account")]["value"] == "1"
    assert by[("Account", "query", "id")]["value"] == "1"
    login, account = by[("Login", "json", "$.username")]["node"], by[("Account", "query", "id")]["node"]
    out = S.apply(text, [
        {"op": "add", "parent": "0/0", "type": "csv_dataset",
         "values": {"dataset_id": "DS-ABCD1234", "name": "Users", "variables": ["username", "password", "flag"]}},
        {"op": "bind", "node": "0/0/1", "kind": "json", "key": "$.username", "var": "username"},
        {"op": "bind", "node": "0/0/1", "kind": "json", "key": "$.remember", "var": "flag", "raw": True},
        {"op": "bind", "node": "0/0/2", "kind": "query", "key": "id", "var": "account_id"},
        {"op": "bind", "node": "0/0/1", "kind": "header", "key": "x-account", "var": "account_id"}])
    assert login == "0/0/0" and account == "0/0/1"  # before the data set was added in front of them
    script = out["script"]
    assert '"username": ${__groovy(groovy.json.JsonOutput.toJson(vars.get(\'username\')))}' in script and '"remember": ${flag}' in script
    assert "/perf/api/account?id=${__urlencode(${account_id})}&amp;view=full" in script
    assert "<stringProp name=\"Header.value\">${account_id}</stringProp>" in script
    ds = S.data_sets(S.parse(script))
    assert ds[0]["dataset_id"] == "DS-ABCD1234" and ds[0]["variables"] == ["username", "password", "flag"]
    assert ds[0]["scope"] == "Users" and ds[0]["recycle"] is True and not out["problems"]
    # A form body (as recorded) is bound with URL encoding; values with ${...} stay whole
    form = S.apply(script, [{"op": "set", "node": "0/0/1", "body": "csrf=${__urlencode(${csrf})}&user=a%40b&pw=1"},
                            {"op": "bind", "node": "0/0/1", "kind": "form", "key": "user", "var": "username"}])
    assert "csrf=${__urlencode(${csrf})}&amp;user=${__urlencode(${username})}&amp;pw=1" in form["script"]
    assert {f["key"]: f["value"] for f in S.fields(form["script"]) if f["kind"] == "form"}["user"] == "${__urlencode(${username})}"
    with pytest.raises(Exception):
        S.apply(script, [{"op": "bind", "node": "0/0/1", "kind": "json", "key": "$.nope.deeper", "var": "x"}])


def test_pre_run_checks_and_a_real_jmeter_run_with_one_row_per_user(client, site):
    rt = client.app.state.runtime
    port = site.rsplit(":", 1)[1]
    ds = _import(client)
    env = client.post("/api/performance/environments", json={"name": "Local", "allowed_hosts": ["127.0.0.1"],
                                                             "max_users": 3}).json()["environment"]
    sc = client.post("/api/performance/scenarios", json={"name": "Data", "environment_id": env["id"]}).json()["scenario"]
    plan = client.post(f"/api/performance/plans/import?scenario_id={sc['id']}",
                       json={"jmx": jmx(port), "filename": "data.jmx"}).json()["plan"]
    view = client.get(f"/api/performance/plans/{plan['id']}/script").json()
    cols = ["username", "password", "account_id", "display_name"]
    ops = [{"op": "add", "parent": "0/0", "type": "csv_dataset",
            "values": {"dataset_id": ds["id"], "name": "Users", "variables": cols + ["missing_col"],
                       "share": "all", "recycle": False}}]
    work = client.post("/api/performance/script/apply", json={"script": view["script"], "ops": ops}).json()
    client.put(f"/api/performance/plans/{plan['id']}/script", json={"script": work["script"], "base_version": 1})
    chk = client.post(f"/api/performance/scenarios/{sc['id']}/check").json()
    data = next(c for c in chk["checks"] if c["id"] == "dataset")
    assert data["status"] == "FAIL" and "missing_col" in data["detail"] and chk["status"] == "FAIL"

    # Fix: the right columns, recycle on; bind the fields through the API the editor uses
    ops[0]["values"].update(variables=cols, recycle=True)
    work = client.post("/api/performance/script/apply", json={"script": view["script"], "ops": ops}).json()
    f = client.post("/api/performance/script/fields", json={"script": work["script"]}).json()
    assert f["data_sets"][0]["dataset_id"] == ds["id"]
    node = {x["sampler"]: x["node"] for x in f["fields"]}
    binds = [{"op": "bind", "node": node["Login"], "kind": "json", "key": f"$.{k}", "var": k}
             for k in ("username", "password", "display_name")]
    binds.append({"op": "bind", "node": node["Account"], "kind": "query", "key": "id", "var": "account_id"})
    work = client.post("/api/performance/script/apply", json={"script": work["script"], "ops": binds}).json()
    assert not work["problems"]
    client.put(f"/api/performance/plans/{plan['id']}/script", json={"script": work["script"], "base_version": 2})
    chk = client.post(f"/api/performance/scenarios/{sc['id']}/check").json()
    data = next(c for c in chk["checks"] if c["id"] == "dataset")
    assert data["status"] == "PASS" and "deleted when JMeter ends" in data["detail"], data
    assert not [c for c in chk["checks"] if c["id"] == "csv"]  # the data set file is not "missing"
    used = client.get(f"/api/performance/datasets/{ds['id']}").json()["dataset"]["used_by"]
    assert used == [{"id": plan["id"], "name": "Data driven"}]
    assert client.delete(f"/api/performance/datasets/{ds['id']}").status_code == 409  # in use

    if not rt.performance.environment()["ready"]:
        pytest.skip("JMeter/Java not installed (set JMETER_HOME to run)")
    for v in site_server.PERF_SEEN.values():
        v.clear()
    run_id = client.post(f"/api/performance/scenarios/{sc['id']}/run").json()["run_id"]
    run = _wait(client, run_id)
    assert run["state"] == "COMPLETED", run.get("state_reason") or run.get("error")
    labels = {r["label"]: r for r in run["metrics"]["requests"]}
    assert labels["Login"]["error_pct"] == 0 and labels["Account"]["error_pct"] == 0, labels
    # Every user logged in with their own row: the secret password, Unicode names and leading-zero ids arrived intact
    assert site_server.PERF_SEEN["logins"] == {"user001", "user002", "user003"}
    assert site_server.PERF_SEEN["names"] == {"Zoë Müller", "東京 太郎", "O'Brien"}
    assert site_server.PERF_SEEN["accounts"] == {"00123", "00456", "07890"}
    cfg = json.loads(client.get(f"/api/performance/runs/{run_id}/files/config.json").text)
    td = cfg["test_data"][0]
    assert td["id"] == ds["id"] and td["rows"] == 3 and td["secret_columns"] == ["password"]
    assert td["kept_in_run_folder"] is False and td["sha256"] is None
    folder = Path(rt.paths.performance)
    assert not [p for p in folder.rglob("*.csv") if p.parent.name == "datasets"]  # deleted: it held passwords
    for p in folder.rglob("*"):
        if p.is_file() and p.suffix in (".jmx", ".json", ".log", ".jtl", ".csv", ".txt"):
            body = p.read_text("utf-8", "replace")
            assert "Pässwörd1!" not in body and 'p,a"ss2' not in body, p


def test_data_without_secrets_is_kept_with_the_run(client, site, rt):
    if not rt.performance.environment()["ready"]:
        pytest.skip("JMeter/Java not installed (set JMETER_HOME to run)")
    port = site.rsplit(":", 1)[1]
    ds = _import(client, "account_id\n00123\n00456\n")
    plan = client.post("/api/performance/plans/import", json={"jmx": jmx(port, users=1, duration=3)}).json()["plan"]
    script = client.get(f"/api/performance/plans/{plan['id']}/script").json()["script"]
    work = client.post("/api/performance/script/apply", json={"script": script, "ops": [
        {"op": "add", "parent": "0/0", "type": "csv_dataset", "values": {"dataset_id": ds["id"],
                                                                        "variables": ["account_id"]}},
        {"op": "bind", "node": "0/0/2", "kind": "query", "key": "id", "var": "account_id"}]}).json()
    client.put(f"/api/performance/plans/{plan['id']}/script", json={"script": work["script"]})
    run_id = client.post(f"/api/performance/plans/{plan['id']}/run", json={}).json()["run_id"]
    run = _wait(client, run_id)
    assert run["state"] == "COMPLETED"
    assert {r["label"]: r for r in run["metrics"]["requests"]}["Account"]["error_pct"] == 0
    kept = client.get(f"/api/performance/runs/{run_id}/files/datasets/{ds['id']}.csv")
    assert kept.status_code == 200 and kept.text == "account_id\n00123\n00456\n"
    cfg = json.loads(client.get(f"/api/performance/runs/{run_id}/files/config.json").text)
    assert cfg["test_data"][0]["kept_in_run_folder"] is True and len(cfg["test_data"][0]["sha256"]) == 64
