"""23.3.23: performance script editor. JMX import keeps every element, tree edits (transactions, extractors,
assertions, timers), raw JMX validation, versions with diff and restore, pre-run checks on scripts, and a real JMeter
run of an imported script with a working correlation."""

from __future__ import annotations

import json
import time
from xml.etree import ElementTree as ET

import pytest

from qa_platform.performance import script as S
from qa_platform.performance.jmx import build_jmx
from qa_platform.performance.models import HttpRequest, LoadProfile, PerfPlan

FINAL = ("COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "SKIPPED")


def gui_jmx(port: str = "1", users: int = 2, duration: int = 8, extra: str = "") -> str:
    """A script laid out the way the JMeter 5.6.3 GUI saves it, with elements the editor has no form for."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
  <hashTree>
    <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="Shop journey">
      <stringProp name="TestPlan.comments">Saved by the JMeter GUI</stringProp>
      <boolProp name="TestPlan.functional_mode">false</boolProp>
      <elementProp name="TestPlan.user_defined_variables" elementType="Arguments" guiclass="ArgumentsPanel" testclass="Arguments" testname="User Defined Variables">
        <collectionProp name="Arguments.arguments">
          <elementProp name="host" elementType="Argument">
            <stringProp name="Argument.name">host</stringProp>
            <stringProp name="Argument.value">127.0.0.1</stringProp>
            <stringProp name="Argument.metadata">=</stringProp>
          </elementProp>
          <elementProp name="port" elementType="Argument">
            <stringProp name="Argument.name">port</stringProp>
            <stringProp name="Argument.value">${{__P(port,{port})}}</stringProp>
            <stringProp name="Argument.metadata">=</stringProp>
          </elementProp>
        </collectionProp>
      </elementProp>
    </TestPlan>
    <hashTree>
      <!-- kept: a comment in the file -->
      <ConfigTestElement guiclass="HttpDefaultsGui" testclass="ConfigTestElement" testname="HTTP Request Defaults">
        <elementProp name="HTTPsampler.Arguments" elementType="Arguments" guiclass="HTTPArgumentsPanel" testclass="Arguments" testname="User Defined Variables">
          <collectionProp name="Arguments.arguments"/>
        </elementProp>
        <stringProp name="HTTPSampler.domain">${{host}}</stringProp>
        <stringProp name="HTTPSampler.port">${{port}}</stringProp>
        <stringProp name="HTTPSampler.protocol">http</stringProp>
      </ConfigTestElement>
      <hashTree/>
      <ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="Shoppers">
        <stringProp name="ThreadGroup.on_sample_error">continue</stringProp>
        <elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" testclass="LoopController" testname="Loop Controller">
          <stringProp name="LoopController.loops">-1</stringProp>
          <boolProp name="LoopController.continue_forever">false</boolProp>
        </elementProp>
        <stringProp name="ThreadGroup.num_threads">{users}</stringProp>
        <stringProp name="ThreadGroup.ramp_time">1</stringProp>
        <boolProp name="ThreadGroup.scheduler">true</boolProp>
        <stringProp name="ThreadGroup.duration">{duration}</stringProp>
        <stringProp name="ThreadGroup.delay"></stringProp>
        <boolProp name="ThreadGroup.same_user_on_next_iteration">true</boolProp>
      </ThreadGroup>
      <hashTree>
        <TransactionController guiclass="TransactionControllerGui" testclass="TransactionController" testname="Browse">
          <boolProp name="TransactionController.includeTimers">false</boolProp>
        </TransactionController>
        <hashTree>
          <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="Inventory">
            <stringProp name="HTTPSampler.path">/api/inventory</stringProp>
            <boolProp name="HTTPSampler.follow_redirects">true</boolProp>
            <stringProp name="HTTPSampler.method">GET</stringProp>
            <boolProp name="HTTPSampler.use_keepalive">true</boolProp>
            <boolProp name="HTTPSampler.postBodyRaw">false</boolProp>
            <elementProp name="HTTPsampler.Arguments" elementType="Arguments" guiclass="HTTPArgumentsPanel" testclass="Arguments" testname="User Defined Variables">
              <collectionProp name="Arguments.arguments"/>
            </elementProp>
          </HTTPSamplerProxy>
          <hashTree>
            <JSONPostProcessor guiclass="JSONPostProcessorGui" testclass="JSONPostProcessor" testname="First item id">
              <stringProp name="JSONPostProcessor.referenceNames">item_id</stringProp>
              <stringProp name="JSONPostProcessor.jsonPathExprs">$[0].id</stringProp>
              <stringProp name="JSONPostProcessor.match_numbers">1</stringProp>
              <stringProp name="JSONPostProcessor.defaultValues">NOT_FOUND</stringProp>
            </JSONPostProcessor>
            <hashTree/>
            <JSR223PostProcessor guiclass="TestBeanGUI" testclass="JSR223PostProcessor" testname="Note">
              <stringProp name="scriptLanguage">groovy</stringProp>
              <stringProp name="parameters"></stringProp>
              <stringProp name="filename"></stringProp>
              <stringProp name="cacheKey">true</stringProp>
              <stringProp name="script">// a &lt; b &amp;&amp; "quotes"
vars.put("seen", "1")</stringProp>
            </JSR223PostProcessor>
            <hashTree/>
          </hashTree>
          <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="Item">
            <stringProp name="HTTPSampler.path">/api/items/${{item_id}}</stringProp>
            <stringProp name="HTTPSampler.method">GET</stringProp>
            <boolProp name="HTTPSampler.postBodyRaw">false</boolProp>
            <elementProp name="HTTPsampler.Arguments" elementType="Arguments" guiclass="HTTPArgumentsPanel" testclass="Arguments" testname="User Defined Variables">
              <collectionProp name="Arguments.arguments"/>
            </elementProp>
          </HTTPSamplerProxy>
          <hashTree/>
        </hashTree>
        {extra}
      </hashTree>
      <ResultCollector guiclass="ViewResultsFullVisualizer" testclass="ResultCollector" testname="View Results Tree">
        <boolProp name="ResultCollector.error_logging">false</boolProp>
        <objProp>
          <name>saveConfig</name>
          <value class="SampleSaveConfiguration">
            <time>true</time>
            <latency>true</latency>
          </value>
        </objProp>
        <stringProp name="filename"></stringProp>
      </ResultCollector>
      <hashTree/>
    </hashTree>
  </hashTree>
</jmeterTestPlan>
"""


def _canon(text: str) -> str:
    return ET.canonicalize(text, strip_text=True)


def _ids(nodes, out=None):
    out = {} if out is None else out
    for n in nodes:
        out[n["name"]] = n["id"]
        _ids(n["children"], out)
    return out


def _wait(client, run_id: str, timeout: float = 180) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/performance/runs/{run_id}").json()["run"]
        if run["state"] in FINAL:
            return run
        time.sleep(0.3)
    raise AssertionError(f"{run_id} stayed {run['state']}")


# ---- import, round trip, summary -------------------------------------------------------------------------------

def test_import_keeps_every_element_and_reads_what_the_script_does(client):
    text = gui_jmx(port="8081")
    r = client.post("/api/performance/plans/import", json={"jmx": text, "filename": "shop.jmx"})
    assert r.status_code == 201, r.text
    plan = r.json()["plan"]
    assert plan["name"] == "Shop journey" and plan["script_version"] == 1 and plan["source"] == "import shop.jmx"
    view = client.get(f"/api/performance/plans/{plan['id']}/script").json()
    # Nothing is lost: same elements, attributes and values (layout aside), including the comment and the
    # JSR223 script text with its special characters.
    assert _canon(view["script"]) == _canon(text)
    assert "<!-- kept: a comment in the file -->" in view["script"]
    exported = client.get(f"/api/performance/plans/{plan['id']}/jmx").text
    assert exported == view["script"]

    s = view["summary"]
    assert [r["url"] for r in s["requests"]] == ["http://127.0.0.1:8081/api/inventory",
                                                  "http://127.0.0.1:8081/api/items/${item_id}"]
    assert s["requests"][0]["transaction"] == "Browse" and s["hosts"] == ["127.0.0.1"] and not s["unresolved_urls"]
    assert s["peak_users"] == 2 and s["duration_known"] and s["total_duration_s"] == 8
    assert s["listeners"] == ["View Results Tree"] and view["problems"] == []
    names = _ids(view["tree"])
    assert {"Shop journey", "HTTP Request Defaults", "Shoppers", "Browse", "Inventory", "First item id", "Note", "Item",
            "View Results Tree"} <= set(names)
    note = client.post("/api/performance/script/node", json={"script": view["script"], "node": names["Note"]}).json()["node"]
    assert note["category"] == "post" and any(p["name"] == "script" and 'a < b && "quotes"' in p["value"] for p in note["props"])
    # The plan list and scenarios show the script's load
    row = next(p for p in client.get("/api/performance/plans").json()["items"] if p["id"] == plan["id"])
    assert row["script"] and row["requests"] == 2 and row["peak_users"] == 2
    sc = next(s for s in client.get("/api/performance/scenarios").json()["items"] if s["plan_id"] == plan["id"])
    assert sc["peak_users"] == 2 and sc["duration_s"] == 8 and row["duration_s"] == 8


def test_invalid_or_unsafe_jmx_is_refused(client):
    bad = client.post("/api/performance/plans/import", json={"jmx": "<jmeterTestPlan><hashTree>", "filename": "x.jmx"})
    assert bad.status_code == 422 and "not valid XML" in bad.json()["message"]
    dtd = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><jmeterTestPlan><hashTree/></jmeterTestPlan>'
    assert client.post("/api/performance/plans/import", json={"jmx": dtd}).status_code == 422
    other = client.post("/api/performance/plans/import", json={"jmx": "<project><target/></project>"})
    assert other.status_code == 422 and "not a JMeter test plan" in other.json()["message"]
    r = client.post("/api/performance/script/analyze", json={"script": "<jmeterTestPlan"})
    assert r.status_code == 422


def test_plugins_and_endless_loops_are_problems_and_block_the_run(client):
    plugin = ('<kg.apc.jmeter.threads.UltimateThreadGroup guiclass="kg.apc.jmeter.threads.UltimateThreadGroupGui" '
              'testclass="kg.apc.jmeter.threads.UltimateThreadGroup" testname="Ultimate"/><hashTree/>')
    text = gui_jmx().replace("<!-- kept: a comment in the file -->", plugin)
    plan = client.post("/api/performance/plans/import", json={"jmx": text}).json()["plan"]
    view = client.get(f"/api/performance/plans/{plan['id']}/script").json()
    assert any("plugins" in p and "UltimateThreadGroup" in p for p in view["problems"])
    assert any(n["plugin"] for n in view["tree"][0]["children"])
    sc = next(s for s in client.get("/api/performance/scenarios").json()["items"] if s["plan_id"] == plan["id"])
    check = client.post(f"/api/performance/scenarios/{sc['id']}/check").json()
    assert check["status"] == "FAIL" and next(c for c in check["checks"] if c["id"] == "jmx")["status"] == "FAIL"

    forever = gui_jmx().replace('<boolProp name="ThreadGroup.scheduler">true</boolProp>',
                                '<boolProp name="ThreadGroup.scheduler">false</boolProp>')
    a = client.post("/api/performance/script/analyze", json={"script": forever}).json()
    assert any("loops forever" in p for p in a["problems"]) and not a["summary"]["duration_known"]


# ---- editing ---------------------------------------------------------------------------------------------------

def test_tree_edits_versions_diff_and_restore(client):
    plan = client.post("/api/performance/plans/import", json={"jmx": gui_jmx(port="8081")}).json()["plan"]
    pid = plan["id"]
    view = client.get(f"/api/performance/plans/{pid}/script").json()
    v1, ids = view["script"], _ids(view["tree"])
    tg = ids["Shoppers"]
    ops = [
        {"op": "add", "parent": tg, "type": "transaction", "values": {"name": "Checkout", "parent": True}},
        {"op": "set", "node": tg, "props": {"ThreadGroup.num_threads": "3"}},
        {"op": "set", "node": ids["Item"], "name": "Item details"},
        {"op": "add", "parent": ids["Item"], "type": "response_assertion",
         "values": {"field": "text", "type": "contains", "pattern": "Item 4"}},
        {"op": "add", "parent": ids["Item"], "type": "duration_assertion", "values": {"duration_ms": 3000}},
        {"op": "add", "parent": ids["Inventory"], "type": "regex_extractor",
         "values": {"variable": "price", "expression": '"price": ([0-9.]+)'}},
        {"op": "add", "parent": tg, "type": "uniform_timer", "values": {"delay_ms": 300, "range_ms": 200}},
        {"op": "set", "node": ids["Note"], "enabled": False},
    ]
    r = client.post("/api/performance/script/apply", json={"script": v1, "ops": ops})
    assert r.status_code == 200, r.text
    work = r.json()
    names = _ids(work["tree"])
    assert {"Checkout", "Item details", "Response assertion", "Duration assertion", "Extract price",
            "Random think time"} <= set(names)
    assert work["summary"]["peak_users"] == 3 and work["problems"] == []
    note = client.post("/api/performance/script/node", json={"script": work["script"], "node": names["Note"]}).json()["node"]
    assert note["enabled"] is False
    # An HTTP request added inside the new transaction, then moved up above "Browse"
    r = client.post("/api/performance/script/apply", json={"script": work["script"], "ops": [
        {"op": "add", "parent": names["Checkout"], "type": "http_request",
         "values": {"name": "Cart", "method": "POST", "url": "/cart", "body": '{"id": ${item_id}}'}},
        {"op": "move", "node": names["Checkout"], "direction": "up"}]}).json()
    names = _ids(r["tree"])
    assert names["Checkout"] < names["Browse"] and r["selected"] == names["Checkout"]
    cart = client.post("/api/performance/script/node", json={"script": r["script"], "node": names["Cart"]}).json()["node"]
    assert cart["body"] == '{"id": ${item_id}}' and cart["url"] == "/cart"
    assert [q["url"] for q in r["summary"]["requests"] if q["name"] == "Cart"] == ["http://127.0.0.1:8081/cart"]
    # Placement rules: a thread group only under the test plan, a request not directly under the plan
    bad = client.post("/api/performance/script/apply", json={"script": r["script"], "ops": [
        {"op": "add", "parent": names["Cart"], "type": "thread_group"}]})
    assert bad.status_code == 422
    assert client.post("/api/performance/script/apply", json={"script": r["script"], "ops": [
        {"op": "delete", "node": "0"}]}).status_code == 422

    # The edits are a working copy until saved
    assert client.get(f"/api/performance/plans/{pid}/script").json()["version"] == 1
    saved = client.put(f"/api/performance/plans/{pid}/script", json={
        "script": r["script"], "base_version": 1, "note": "Checkout and assertions", "sla": {"p95_ms": 1500}}).json()
    assert saved["version"] == 2 and saved["plan"]["sla"]["p95_ms"] == 1500
    stale = client.put(f"/api/performance/plans/{pid}/script", json={"script": v1, "base_version": 1})
    assert stale.status_code == 409
    # Saving the same text again does not make a new version
    same = client.put(f"/api/performance/plans/{pid}/script", json={"script": r["script"], "base_version": 2}).json()
    assert same["version"] == 2

    versions = client.get(f"/api/performance/plans/{pid}/versions").json()["items"]
    assert [v["version"] for v in versions] == [2, 1] and versions[0]["note"] == "Checkout and assertions"
    d = client.get(f"/api/performance/plans/{pid}/diff", params={"a": 1, "b": 2}).json()["diff"]
    assert '+        <stringProp name="ThreadGroup.num_threads">3</stringProp>' in d
    assert '-        <stringProp name="ThreadGroup.num_threads">2</stringProp>' in d
    assert 'testname="Item details"' in d and d.startswith("--- version 1")
    assert client.get(f"/api/performance/plans/{pid}/versions/1").text == v1

    rest = client.post(f"/api/performance/plans/{pid}/versions/1/restore").json()
    assert rest["version"] == 3
    now = client.get(f"/api/performance/plans/{pid}/script").json()
    assert now["script"] == v1 and now["summary"]["peak_users"] == 2
    assert client.get(f"/api/performance/plans/{pid}/versions").json()["items"][0]["note"] == "Restored version 1"


def test_raw_jmx_edit_header_and_variable_forms(client):
    plan = client.post("/api/performance/plans/import", json={"jmx": gui_jmx(port="8081")}).json()["plan"]
    view = client.get(f"/api/performance/plans/{plan['id']}/script").json()
    raw = view["script"].replace(">127.0.0.1<", ">shop.example.test<")
    a = client.post("/api/performance/script/analyze", json={"script": raw}).json()
    assert a["summary"]["hosts"] == ["shop.example.test"]
    ids = _ids(a["tree"])
    r = client.post("/api/performance/script/apply", json={"script": a["script"], "ops": [
        {"op": "add", "parent": ids["Shoppers"], "type": "header_manager",
         "values": {"headers": [{"name": "Accept", "value": "application/json"}]}},
        {"op": "set", "node": "0", "variables": [{"name": "host", "value": "127.0.0.1"},
                                                 {"name": "port", "value": "9090"}]}]}).json()
    names = _ids(r["tree"])
    hm = client.post("/api/performance/script/node", json={"script": r["script"], "node": names["Headers"]}).json()["node"]
    assert hm["headers"] == [{"name": "Accept", "value": "application/json"}]
    tp = client.post("/api/performance/script/node", json={"script": r["script"], "node": "0"}).json()["node"]
    assert tp["variables"][1] == {"name": "port", "value": "9090"} and tp["comments"] == "Saved by the JMeter GUI"
    assert r["summary"]["requests"][0]["url"] == "http://127.0.0.1:9090/api/inventory"
    # A config element goes first in its parent, before the requests it applies to
    assert names["Headers"].endswith("/0")


def test_generated_plan_opens_in_the_script_editor(client):
    p = client.post("/api/performance/plans", json=PerfPlan(name="Gen", requests=[
        HttpRequest(name="Home", url="http://127.0.0.1:8081/index.html")], profile=LoadProfile(users=4, duration_s=60)
    ).model_dump()).json()["plan"]
    before = client.get(f"/api/performance/plans/{p['id']}/jmx").text
    conv = client.post(f"/api/performance/plans/{p['id']}/script/convert").json()["plan"]
    assert conv["script_version"] == 1 and conv["requests"][0]["url"] == "http://127.0.0.1:8081/index.html"
    view = client.get(f"/api/performance/plans/{p['id']}/script").json()
    assert _canon(view["script"]) == _canon(before) and view["summary"]["peak_users"] == 4
    assert view["summary"]["total_duration_s"] == 60 and view["version"] == 1
    # The old form can still rename it and set targets, but cannot replace the script
    full = client.get(f"/api/performance/plans/{p['id']}").json()["plan"]
    upd = client.put(f"/api/performance/plans/{p['id']}", json={**full, "name": "Gen 2", "script": None,
                                                                  "requests": [{"name": "x", "url": "http://evil.test/"}]})
    assert upd.status_code == 200
    after = client.get(f"/api/performance/plans/{p['id']}/script").json()
    assert after["plan"]["name"] == "Gen 2" and after["script"] == view["script"]
    assert [r["host"] for r in after["summary"]["requests"]] == ["127.0.0.1"]
    # Run-time overrides of users or duration do not apply to scripts
    rt = client.app.state.runtime
    with pytest.raises(Exception) as exc:
        rt.performance.enqueue(p["id"], overrides={"users": 50})
    assert "script editor" in str(exc.value)


# ---- pre-run checks on scripts -----------------------------------------------------------------------------------

def test_script_checks_hosts_variables_duration_and_data_files(client):
    env = client.post("/api/performance/environments", json={"name": "QA", "allowed_hosts": ["127.0.0.1"],
                                                             "max_users": 5, "max_duration_s": 600}).json()["environment"]

    def scenario(text: str) -> dict:
        sc = client.post("/api/performance/scenarios", json={"name": "S", "environment_id": env["id"]}).json()["scenario"]
        client.post(f"/api/performance/plans/import?scenario_id={sc['id']}", json={"jmx": text})
        return client.post(f"/api/performance/scenarios/{sc['id']}/check").json()

    by_id = lambda chk: {c["id"]: c for c in chk["checks"]}  # noqa: E731
    ok = by_id(scenario(gui_jmx()))
    assert ok["hosts"]["status"] == "PASS" and ok["users"]["status"] == "PASS" and ok["jmx"]["status"] == "PASS"
    assert ok["listeners"]["status"] == "PASS" and "switched off" in ok["listeners"]["detail"]

    # The host comes from a variable: the value decides, and a host outside the environment blocks the run
    outside = by_id(scenario(gui_jmx().replace(">127.0.0.1<", ">www.saucedemo.com<")))
    assert outside["hosts"]["status"] == "FAIL" and "www.saucedemo.com" in outside["hosts"]["detail"]
    # A host only known at run time cannot be checked against the allowlist
    unknown = scenario(gui_jmx().replace("<stringProp name=\"Argument.value\">127.0.0.1</stringProp>",
                                         "<stringProp name=\"Argument.value\">${__P(target)}</stringProp>"))
    assert by_id(unknown)["hosts_unresolved"]["status"] == "FAIL" and unknown["status"] == "FAIL"
    # Load above the environment's limit, with the user count in a property default
    heavy = by_id(scenario(gui_jmx(users=1).replace('<stringProp name="ThreadGroup.num_threads">1</stringProp>',
                                                    '<stringProp name="ThreadGroup.num_threads">${__P(users,40)}</stringProp>')))
    assert heavy["users"]["status"] == "FAIL" and "40" in heavy["users"]["detail"]
    # A loop count instead of a duration: unknown length, and the environment has a duration limit
    loops = gui_jmx().replace('<boolProp name="ThreadGroup.scheduler">true</boolProp>',
                              '<boolProp name="ThreadGroup.scheduler">false</boolProp>').replace(
        '<stringProp name="LoopController.loops">-1</stringProp>', '<stringProp name="LoopController.loops">5</stringProp>')
    assert by_id(scenario(loops))["duration_unknown"]["status"] == "FAIL"
    # A CSV data file that is not there
    csv = ('<CSVDataSet guiclass="TestBeanGUI" testclass="CSVDataSet" testname="Users CSV">'
           '<stringProp name="filename">users.csv</stringProp><stringProp name="variableNames">u,p</stringProp>'
           '</CSVDataSet><hashTree/>')
    assert by_id(scenario(gui_jmx(extra=csv)))["csv"]["status"] == "FAIL"


def test_disabled_elements_are_left_out_and_listeners_switched_off_for_runs():
    text = gui_jmx(port="8081")
    root = S.parse(text)
    ids = _ids(S.tree(root))
    out = S.apply(text, [{"op": "set", "node": ids["Browse"], "enabled": False}])
    assert out["summary"]["requests"] == []  # everything under a disabled controller is skipped, as in JMeter
    node = next(n for n in out["tree"][0]["children"][1]["children"] if n["name"] == "Browse")
    assert node["children"][0]["active"] is False and node["children"][0]["enabled"] is True
    run_copy, n = S.for_run(text)
    assert n == 1 and 'testname="View Results Tree" enabled="false"' in run_copy
    assert 'testname="View Results Tree" enabled="false"' not in S.normalize(text)
    assert S.java_hash("200") == 49586 and S.java_hash("Item 4") == -2094796153


def test_generated_jmx_round_trips_without_change():
    plan = PerfPlan(name="t & <x>", requests=[HttpRequest(name="Home", url="https://www.saucedemo.com/?a=1&b=2",
                                                          expect_status="2\\d\\d", headers={"X-A": "1"})])
    text = build_jmx(plan)
    once = S.normalize(text)
    assert S.normalize(once) == once and _canon(once) == _canon(text)
    assert S.analyze(once)["summary"]["requests"][0]["url"] == "https://www.saucedemo.com/?a=1&b=2"


# ---- a real JMeter run of an imported, edited script -------------------------------------------------------------

def test_imported_script_runs_in_jmeter_with_correlation(client, site):
    rt = client.app.state.runtime
    if not rt.performance.environment()["ready"]:
        pytest.skip("JMeter/Java not installed (set JMETER_HOME to run)")
    port = site.rsplit(":", 1)[1]
    env = client.post("/api/performance/environments", json={"name": "Local", "allowed_hosts": ["127.0.0.1"],
                                                             "max_users": 2}).json()["environment"]
    sc = client.post("/api/performance/scenarios", json={"name": "Imported", "environment_id": env["id"]}).json()["scenario"]
    plan = client.post(f"/api/performance/plans/import?scenario_id={sc['id']}",
                       json={"jmx": gui_jmx(port=port, users=1, duration=6), "filename": "shop.jmx"}).json()["plan"]
    view = client.get(f"/api/performance/plans/{plan['id']}/script").json()
    ids = _ids(view["tree"])
    # Check that the correlated id reached the next request, and add think time
    work = client.post("/api/performance/script/apply", json={"script": view["script"], "ops": [
        {"op": "add", "parent": ids["Item"], "type": "response_assertion",
         "values": {"field": "text", "type": "contains", "pattern": "Item 4"}},
        {"op": "add", "parent": ids["Shoppers"], "type": "constant_timer", "values": {"delay_ms": 300}}]}).json()
    client.put(f"/api/performance/plans/{plan['id']}/script", json={"script": work["script"], "base_version": 1,
                                                                     "note": "Assert the correlated item"})
    run_id = client.post(f"/api/performance/scenarios/{sc['id']}/run").json()["run_id"]
    run = _wait(client, run_id)
    assert run["state"] == "COMPLETED", run.get("state_reason") or run.get("error")
    labels = {r["label"]: r for r in run["metrics"]["requests"]}
    assert {"Inventory", "Item", "Browse"} <= set(labels)  # the transaction is reported as its own label
    assert labels["Item"]["samples"] > 0 and labels["Item"]["error_pct"] == 0  # the extracted id was used
    cfg = json.loads(client.get(f"/api/performance/runs/{run_id}/files/config.json").text)
    assert cfg["script"]["version"] == 2 and cfg["script"]["listeners_disabled_for_run"] == 1
    assert cfg["workload"]["peak_concurrent_users"] == 1 and cfg["workload"]["total_duration_s"] == 6
    ran = client.get(f"/api/performance/runs/{run_id}/files/plan.jmx").text
    saved = client.get(f"/api/performance/runs/{run_id}/files/script.jmx").text
    assert 'testname="View Results Tree" enabled="false"' in ran and 'enabled="false"' not in saved
    assert next(c for c in run["checks"]["checks"] if c["id"] == "jmx")["status"] == "PASS"
