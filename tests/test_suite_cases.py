"""Test Plans / Test Suites CRUD, suite-aware AC-traceable test cases and suite-aware test data."""

from __future__ import annotations

import csv
import io
import json
import time

import pytest
from fastapi.testclient import TestClient

from qa_platform.credentials import MemoryBackend
from qa_platform.design import suite_cases, testing_types
from qa_platform.design.data_agent import check_rows, is_secret_field, secret_free
from qa_platform.design import models
from qa_platform.main import create_app
from tests.fake_ado import PAT, FakeAzureDevOps
from tests.fake_llm import FakeLLM


def _app(paths, *, mode="online", legacy=False):
    ado, llm = FakeAzureDevOps(legacy_test_api=legacy, hierarchy=True), FakeLLM()
    ado.items[204] = {"System.Title": "Admin approves orders", "System.WorkItemType": "User Story", "System.State": "Active",
                      "System.IterationPath": "Shop\\Sprint 4", "System.Description": "<p>Only admins approve.</p>",
                      "Microsoft.VSTS.Common.AcceptanceCriteria":
                          "<ul><li>AC1: Admin can approve an order and the customer is notified</li>"
                          "<li>AC2: Approval happens within an appropriate time</li></ul>"}
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport(), ai_transport=llm.transport())
    return app, ado, llm, mode


@pytest.fixture(params=[("online", False), ("offline", True)], ids=["online", "local-tfs-legacy-api"])
def env(paths, request):
    app, ado, llm, mode = _app(paths, mode=request.param[0], legacy=request.param[1])
    with TestClient(app) as c:
        c.put(f"/api/tfs/profiles/{mode}", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                 "make_active": True})
        c.put("/api/ai/settings", json={"base_url": "http://127.0.0.1:8080/v1", "model": "qwen-local.gguf"})
        yield c, ado, llm


def _wait(c, url):
    for _ in range(400):
        body = c.get(url).json()
        if body["status"] != "RUNNING":
            return body
        time.sleep(0.05)
    raise AssertionError("run did not finish")


# ---- test plans --------------------------------------------------------------------------------------------


def test_plan_create_with_and_without_sprint_update_delete(env):
    c, ado, _ = env
    r = c.post("/api/tfs/test-plans", json={"iteration": "Shop\\Sprint 4", "suite_types": testing_types.DEFAULT_CHECKED})
    assert r.status_code == 201, r.json()
    plan = r.json()["plan"]
    assert ado.plans[plan["id"]]["name"] == "Shop - Sprint 4 - Test Plan"
    assert ado.plans[plan["id"]]["iteration"] == "Shop\\Sprint 4"
    names = [s["name"] for s in ado.suites[plan["id"]] if s.get("parentSuite")]
    assert names == ["Functional Testing", "Smoke Testing", "Sanity Testing", "Regression Testing",
                     "Integration Testing", "End-to-End Testing"]

    loose = c.post("/api/tfs/test-plans", json={"name": "Release regression", "suite_types": []}).json()["plan"]
    assert "iteration" not in ado.plans[loose["id"]] or not ado.plans[loose["id"]]["iteration"]
    assert [s for s in ado.suites[loose["id"]] if s.get("parentSuite")] == []

    r = c.patch(f"/api/tfs/test-plans/{plan['id']}", json={"name": "Sprint 4 acceptance", "iteration": "",
                                                           "state": "Inactive", "description": "Scope: checkout"})
    assert r.status_code == 200, r.json()
    p = ado.plans[plan["id"]]
    assert (p["name"], p["iteration"], p["state"], p["description"]) == ("Sprint 4 acceptance", "Shop", "Inactive",
                                                                         "Scope: checkout")
    listed = {x["id"]: x for x in c.get("/api/tfs/test-plans").json()["plans"]}
    assert listed[plan["id"]]["name"] == "Sprint 4 acceptance"

    assert c.delete(f"/api/tfs/test-plans/{loose['id']}").json()["ok"] is True
    assert loose["id"] not in ado.plans
    assert loose["id"] not in {x["id"] for x in c.get("/api/tfs/test-plans").json()["plans"]}
    assert c.patch(f"/api/tfs/test-plans/{plan['id']}", json={"name": "  "}).status_code == 400


# ---- test suites ---------------------------------------------------------------------------------------------


def test_testing_types_list_eleven_with_first_six_default(env):
    c, _, _ = env
    types = c.get("/api/tfs/testing-types").json()["testing_types"]
    assert [t["name"] for t in types] == [
        "Functional Testing", "Smoke Testing", "Sanity Testing", "Regression Testing", "Integration Testing",
        "End-to-End Testing", "API Testing", "Database Testing", "UI and Usability Testing", "Performance Testing",
        "Security Testing"]
    assert [t["default"] for t in types] == [True] * 6 + [False] * 5


def test_suite_create_update_delete_keeps_testing_type(env):
    c, ado, _ = env
    r = c.post("/api/tfs/test-plans/7/suites", json={"testing_types": ["api", "security", "api"]})
    assert r.status_code == 201, r.json()
    made = r.json()["suites"]
    assert [(s["name"], s["testing_type"], s["created"]) for s in made] == [
        ("API Testing", "api", True), ("Security Testing", "security", True)]
    again = c.post("/api/tfs/test-plans/7/suites", json={"testing_types": ["api"]}).json()["suites"]
    assert again[0]["created"] is False and again[0]["id"] == made[0]["id"]  # reused, never duplicated

    sid = made[0]["id"]
    assert c.patch(f"/api/tfs/test-plans/7/suites/{sid}", json={"name": "Checkout API"}).status_code == 200
    suites = {s["id"]: s for s in c.get("/api/tfs/test-plans/7/suites").json()["suites"]}
    assert suites[sid]["name"] == "Checkout API" and suites[sid]["testing_type"] == "api"  # type survives the rename
    assert suites[8]["testing_type"] is None  # "Checkout" has no type yet
    c.patch("/api/tfs/test-plans/7/suites/8", json={"testing_type": "end_to_end"})
    assert {s["id"]: s for s in c.get("/api/tfs/test-plans/7/suites").json()["suites"]}[8]["testing_type"] == "end_to_end"

    assert c.delete(f"/api/tfs/test-plans/7/suites/{made[1]['id']}").json()["ok"] is True
    assert made[1]["id"] not in {s["id"] for s in ado.suites[7]}
    root = c.delete("/api/tfs/test-plans/7/suites/70")
    assert root.status_code == 409 and root.json()["error_code"] == "TFS_ROOT_SUITE"
    assert c.post("/api/tfs/test-plans/7/suites", json={"testing_types": ["nope"]}).json()["error_code"] == "TESTING_TYPE_INVALID"


# ---- acceptance criteria ids -----------------------------------------------------------------------------------


def test_criteria_ids_explicit_assigned_gherkin_and_stable():
    lines = suite_cases.criteria_lines("<ul><li>AC1: Pay by card</li><li>ac-02 - Refund within 5 days</li></ul>"
                                       "<p>Given a cart</p><p>When I pay</p><p>Then I see a receipt</p><li>Plain one</li>")
    assert lines == ["AC1: Pay by card", "ac-02 - Refund within 5 days", "Given a cart When I pay Then I see a receipt",
                     "Plain one"]
    crit = suite_cases.assign_ids(lines, [])
    assert [(x["id"], x["text"]) for x in crit] == [("AC1", "Pay by card"), ("AC-02", "Refund within 5 days"),
                                                    ("AC-01", "Given a cart When I pay Then I see a receipt"),
                                                    ("AC-03", "Plain one")]
    # A criterion inserted at the top keeps the others' ids; an edited one keeps the id it replaced.
    prev = [{"ac_id": "AC-01", "ac_hash": suite_cases._hash("Login works"), "ac_text": "Login works"},
            {"ac_id": "AC-02", "ac_hash": suite_cases._hash("Logout works"), "ac_text": "Logout works"}]
    again = suite_cases.assign_ids(["New first rule", "Login works", "Logout works now"], prev)
    assert [x["id"] for x in again] == ["AC-03", "AC-01", "AC-02"]


# ---- suite-aware generation -----------------------------------------------------------------------------------


def _suite(c, key):
    return c.post("/api/tfs/test-plans/7/suites", json={"testing_types": [key]}).json()["suites"][0]["id"]


def _generate(c, suite_id, stories):
    r = c.post("/api/design/suite-runs", json={"plan_id": 7, "suite_id": suite_id, "user_story_ids": stories})
    assert r.status_code == 202, r.json()
    body = _wait(c, f"/api/design/suite-runs/{r.json()['run_id']}")
    assert body["status"] == "PASSED", body
    return r.json()["run_id"], body["result"]


def test_sprint_stories_then_functional_generation_traceable_and_validated(env):
    c, ado, llm = env
    stories = c.get("/api/design/sprint-stories", params={"iteration": "Shop\\Sprint 4"}).json()["stories"]
    assert {s["id"] for s in stories} >= {201, 202, 204} and 203 not in {s["id"] for s in stories}

    run_id, result = _generate(c, _suite(c, "functional"), [201, 204])
    s201, s204 = result["stories"]
    assert [(x["id"], x["text"]) for x in s201["criteria"]] == [
        ("AC-01", "Customer can login successfully"), ("AC-02", "Customer sees an error for a wrong password")]
    assert [x["id"] for x in s204["criteria"]] == ["AC1", "AC2"]  # existing ids preserved

    cases = s201["test_cases"]
    assert all(tc["title"].startswith("Functional Testing - ") for tc in cases)
    assert all(tc["ac_ids"] for tc in cases)  # every case has a valid source link
    assert "AC-99" not in json.dumps(cases)  # invented criterion id removed
    # the reviewer step replaced the vague expected result found by the explicit rules
    assert s201["refined"] is True
    assert "works correctly" not in json.dumps(cases)
    assert any(m["system"].startswith("You are a test case reviewer") for m in
               [{"system": b["messages"][0]["content"]} for b in llm.calls])
    # duplicate of the existing linked test case #1268 is proposed for reuse
    dup = [d for d in s201["validation"]["duplicates"] if d["kind"] == "existing"]
    assert dup and dup[0]["duplicate_of"] == 1268
    # coverage judged per condition: AC1 of US204 has two conditions
    cov204 = {r["ac_id"]: r for r in s204["coverage"]}
    assert [x["id"] for x in cov204["AC1"]["conditions"]] == ["AC1.1", "AC1.2"]
    assert cov204["AC1"]["status"] == "Covered"
    assert "What does 'appropriate' mean?" in cov204["AC2"]["clarifications"]
    assert cov204["AC2"]["status"] == "Needs Clarification"  # a linked case does not make an unclear criterion covered
    meta = result["meta"]
    assert meta["agent_version"] == suite_cases.AGENT_VERSION and meta["prompt_version"] == suite_cases.PROMPT_VERSION
    assert meta["model"]["model"] == "qwen-local.gguf" and meta["requirement_revision"]["201"]["criteria_hash"]

    # publish: one new case, one reused existing case; criterion links stored by the application
    new_ref = next(tc["ref"] for tc in cases if tc["ref"] != dup[0]["ref"])
    r = c.post(f"/api/design/suite-runs/{run_id}/publish", json={
        "selection": {"201": [new_ref, dup[0]["ref"]]}, "reuse": {f"201:{dup[0]['ref']}": 1268}})
    assert r.status_code == 200, r.json()
    created = {x["action"]: x for x in r.json()["created"]}
    new_id = created["created"]["id"]
    item = ado.items[new_id]
    assert item["System.Title"].startswith("Functional Testing - ")
    assert "Action" not in item["System.Title"] and "<step id=\"2\"" in item["Microsoft.VSTS.TCM.Steps"]
    new_case = next(tc for tc in cases if tc["ref"] == new_ref)
    crit = {x["id"]: x["text"] for x in s201["criteria"]}
    assert all(f"{a}: {crit[a]}" in item["System.Description"] for a in new_case["ac_ids"])
    assert "Acceptance-criterion links are kept by the application" in item["System.Description"]
    assert ("Microsoft.VSTS.Common.TestedBy-Reverse", 201) in ado.relations[new_id]
    suite_id = r.json()["suite_id"]
    assert set(ado.suite_members[suite_id]) == {new_id, 1268}

    trace = c.get("/api/design/traceability/201").json()
    linked = {r["ac_id"]: r["by_testing_type"] for r in trace["matrix"]}
    assert any(x["id"] == 1268 for x in linked["AC-01"]["Functional Testing"])
    assert all(any(x["id"] == new_id for x in linked[a]["Functional Testing"]) for a in new_case["ac_ids"])
    assert trace["impact"] == []
    # publishing again does not create the case twice
    again = c.post(f"/api/design/suite-runs/{run_id}/publish", json={"selection": {"201": [new_ref]}}).json()
    assert again["created"][0]["action"] == "already published"


def test_different_suite_types_give_different_coverage(env):
    c, _, _ = env
    _, func = _generate(c, _suite(c, "functional"), [201])
    _, smoke = _generate(c, _suite(c, "smoke"), [201])
    _, perf = _generate(c, _suite(c, "performance"), [201])
    f, s, p = func["stories"][0], smoke["stories"][0], perf["stories"][0]
    assert len(s["test_cases"]) == 1 and len(f["test_cases"]) > 1
    assert s["test_cases"][0]["title"].startswith("Smoke Testing - ")
    assert s["limitations"] and "business-critical" in s["limitations"][0]
    assert {r["ac_id"]: r["status"] for r in p["coverage"]}["AC-02"] == "Not Applicable"
    assert any("No performance target" in g for g in p["test_cases"][0]["gaps"])
    assert {tc["title"] for tc in f["test_cases"]}.isdisjoint({tc["title"] for tc in s["test_cases"]})


def test_changed_criteria_flag_linked_cases_for_review_and_edits_are_kept(env):
    c, ado, _ = env
    run_id, result = _generate(c, _suite(c, "functional"), [201])
    ref = result["stories"][0]["test_cases"][1]["ref"]
    edited = c.patch(f"/api/design/suite-runs/{run_id}/stories/201/cases/{ref}", json={"case": {
        "title": "Wrong password shows an error", "steps": [{"action": "Enter a wrong password", "expected": "works correctly"}]}})
    assert edited.status_code == 200, edited.json()
    story = edited.json()["story"]
    tc = next(t for t in story["test_cases"] if t["ref"] == ref)
    assert tc["edited"] and tc["title"] == "Functional Testing - Wrong password shows an error"
    assert any(i["rule"] == "EXPECTED_RESULT" for i in story["validation"]["per_case"][ref])  # re-validated
    c.post(f"/api/design/suite-runs/{run_id}/publish", json={"selection": {"201": [ref]}})

    ado.items[201]["Microsoft.VSTS.Common.AcceptanceCriteria"] = (
        "<ul><li>Customer can login successfully</li><li>Customer sees an error for a wrong password after 3 tries</li></ul>")
    c.post("/api/tfs/cache/clear")
    trace = c.get("/api/design/traceability/201").json()
    assert [x["id"] for x in trace["criteria"]] == ["AC-01", "AC-02"]  # stable ids
    assert len(trace["impact"]) == 1 and trace["impact"][0]["ac_id"] == "AC-02"
    assert "changed" in trace["impact"][0]["reason"]
    row = next(r for r in trace["matrix"] if r["ac_id"] == "AC-02")
    assert row["by_testing_type"]["Functional Testing"][0]["status"] == "review"


def test_suite_without_testing_type_needs_one(env):
    c, _, _ = env
    r = c.post("/api/design/suite-runs", json={"plan_id": 7, "suite_id": 8, "user_story_ids": [201]})
    assert r.status_code == 400 and r.json()["error_code"] == "TESTING_TYPE_REQUIRED"
    r = c.post("/api/design/suite-runs", json={"plan_id": 7, "suite_id": 8, "user_story_ids": [201],
                                               "testing_type": "ui_usability"})
    assert r.status_code == 202
    _wait(c, f"/api/design/suite-runs/{r.json()['run_id']}")


def test_generation_survives_restart(paths):
    app, ado, llm, _ = _app(paths)
    with TestClient(app) as c:
        c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                "make_active": True})
        c.put("/api/ai/settings", json={"base_url": "http://127.0.0.1:8080/v1", "model": "m"})
        run_id, result = _generate(c, _suite(c, "functional"), [201])
        ref = result["stories"][0]["test_cases"][1]["ref"]
        c.post(f"/api/design/suite-runs/{run_id}/publish", json={"selection": {"201": [ref]}})
    app2 = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport(), ai_transport=llm.transport())
    with TestClient(app2) as c2:
        c2.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                 "make_active": True})
        body = c2.get(f"/api/design/suite-runs/{run_id}").json()
        assert body["result"]["stories"][0]["test_cases"][1]["published_id"]
        trace = c2.get("/api/design/traceability/201").json()
        assert any(r["by_testing_type"] for r in trace["matrix"])


# ---- test data -----------------------------------------------------------------------------------------------


def _published_case(c, key="functional"):
    run_id, result = _generate(c, _suite(c, key), [201])
    cases = result["stories"][0]["test_cases"]
    ref = cases[min(1, len(cases) - 1)]["ref"]
    pub = c.post(f"/api/design/suite-runs/{run_id}/publish", json={"selection": {"201": [ref]}}).json()
    return pub["created"][0]["id"]


def _gen_data(c, **body):
    r = c.post("/api/test-data/generate", json=body)
    assert r.status_code == 202, r.json()
    s = _wait(c, f"/api/test-data/generate/{r.json()['run_id']}")
    assert s["status"] == "PASSED", s
    return s["result"]["results"]


def test_generate_test_data_rules_traceability_and_filters(env):
    c, ado, _ = env
    tc = _published_case(c)
    res = _gen_data(c, test_case_ids=[tc], seed=42)[0]
    assert res["testing_type"] == "Functional Testing" and res["user_story_id"] == "201" and res["seed"] == 42
    rules = {i["rule"] for i in res["issues"]}
    assert {"SECRET", "SAFE_DESTINATION", "DUPLICATE_ROW", "VIOLATED_RULE", "SOURCE_LINK"} <= rules

    rows = c.get("/api/test-data", params={"iteration": "Shop\\Sprint 4", "user_story_id": "201",
                                           "test_case_ids": str(tc)}).json()["items"]
    assert len(rows) == 4  # duplicate dropped
    assert secret_free(rows)
    valid = next(r for r in rows if r["name"] == "Valid user")
    assert valid["dataset"]["password"] == "{{SECRET:password}}" and valid["dataset"]["email"] == "jane@example.com"
    assert valid["dataset"]["account_no"] == "007"  # leading zeros kept
    assert valid["dataset"]["start_date"] == "{{TODAY+3d:%d/%m/%Y}}"  # rule stored, preview resolved
    assert valid["meta"]["preview"]["start_date"] != "{{TODAY+3d:%d/%m/%Y}}"
    assert valid["row_key"] == f"DS-{tc}-functional-R01" and valid["version"] == 1
    assert valid["meta"]["agent_version"] and valid["meta"]["prompt_version"] and valid["meta"]["seed"] == 42
    wrong = next(r for r in rows if r["name"] == "Wrong password")
    assert wrong["intended_valid"] is False and wrong["dataset"]["password"] == "wrong-pass"
    assert wrong["violated_rule"] == "Password must match"
    empty = next(r for r in rows if r["name"] == "Empty username")
    assert empty["ac_ids"] == ["AC-01"] and empty["dataset"]["password"] is None
    assert c.get("/api/test-data", params={"iteration": "Shop\\Sprint 5"}).json()["items"] == []

    cov = c.get("/api/test-data/coverage/201").json()["matrix"]
    status = {r["ac_id"]: r["status"] for r in cov}
    assert status["AC-02"] in ("Data covered", "Missing data coverage") and status["AC-01"] == "Missing test case coverage"


def test_test_data_edit_versioning_regenerate_keeps_edits_and_delete_selected(env):
    c, _, _ = env
    tc = _published_case(c)
    _gen_data(c, test_case_ids=[tc])
    rows = c.get("/api/test-data", params={"test_case_id": tc}).json()["items"]
    valid = next(r for r in rows if r["name"] == "Valid user")
    bad = c.patch(f"/api/test-data/{valid['id']}", json={"values": {**valid["dataset"], "password": "Secret1!"}})
    assert bad.status_code == 400 and bad.json()["error_code"] == "SECRET_IN_TEST_DATA"
    ok = c.patch(f"/api/test-data/{valid['id']}", json={"values": {**valid["dataset"], "username": "edited_user"},
                                                        "expected_outcome": "Inventory with 6 products"}).json()["item"]
    assert ok["user_edited"] and ok["version"] == 2 and ok["dataset"]["username"] == "edited_user"
    assert c.get(f"/api/test-data/{valid['id']}/history").json()["history"][0]["change"] == "edited"

    res = _gen_data(c, test_case_ids=[tc])[0]
    assert res["version"] == 2 and res["kept_user_edited"] == 1
    after = c.get("/api/test-data", params={"test_case_id": tc}).json()["items"]
    kept = next(r for r in after if r["id"] == valid["id"])
    assert kept["dataset"]["username"] == "edited_user" and "edited by a user" in kept["review_reason"]
    assert len(after) == 4 + 1

    ids = [r["id"] for r in after if r["id"] != valid["id"]][:2]
    assert c.post("/api/test-data/delete", json={"ids": ids}).json()["deleted"] == 2
    assert {r["id"] for r in c.get("/api/test-data", params={"test_case_id": tc}).json()["items"]}.isdisjoint(ids)


def test_suite_aware_data_differs_by_type(env):
    c, _, _ = env
    perf_tc = _published_case(c, "performance")
    res = _gen_data(c, test_case_ids=[perf_tc])[0]
    assert res["testing_type"] == "Performance Testing" and res["scenarios"] == ["volume"]


def test_write_test_data_to_tfs_uses_test_data_tab_else_summary(env):
    c, ado, _ = env
    tc = _published_case(c)
    _gen_data(c, test_case_ids=[tc])
    r = c.post(f"/api/test-data/tfs/{tc}", json={"target": "auto"}).json()
    assert r["target"] == "Parameter values (test data tab)" and r["rows"] == 4
    assert "<parameters>" in ado.items[tc]["Microsoft.VSTS.TCM.Parameters"]
    assert "{{SECRET:password}}" in ado.items[tc]["Microsoft.VSTS.TCM.LocalDataSource"]
    assert "ValidPass123!" not in json.dumps(ado.items[tc])

    # A process template whose Test Case has no Parameters field: the data goes into the Summary.
    ado.test_case_parameters = False
    c.post("/api/tfs/cache/clear")
    r = c.post(f"/api/test-data/tfs/{tc}", json={"target": "auto"}).json()
    assert r["target"] == "Summary", r
    assert "<h3>Test Data</h3>" in ado.items[tc]["System.Description"]


def test_export_import_round_trip_and_formula_safety(env):
    c, _, _ = env
    tc = _published_case(c)
    _gen_data(c, test_case_ids=[tc])
    text = c.get("/api/test-data/export", params={"fmt": "csv", "test_case_ids": str(tc)}).text
    assert text.startswith("﻿")
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    formula = next(r for r in rows if r["name"] == "Formula")
    assert formula["value.username"].startswith("'=")  # CSV/Excel formula injection neutralised
    assert next(r for r in rows if r["name"] == "Valid user")["value.account_no"] == "007"
    assert next(r for r in rows if r["name"] == "Empty username")["value.password"] == "<null>"
    assert "ValidPass123!" not in text

    c.post("/api/test-data/delete", json={"ids": [r["id"] for r in c.get("/api/test-data", params={"test_case_id": tc}).json()["items"]]})
    assert c.post("/api/test-data/import", json={"format": "csv", "content": text.lstrip("﻿")}).json()["imported"] == 4
    back = {r["name"]: r for r in c.get("/api/test-data", params={"test_case_id": tc}).json()["items"]}
    assert back["Formula"]["dataset"]["username"] == '=HYPERLINK("x")'
    assert back["Valid user"]["dataset"]["account_no"] == "007"
    assert back["Empty username"]["dataset"]["password"] is None
    assert back["Wrong password"]["intended_valid"] is False

    js = c.get("/api/test-data/export", params={"fmt": "json", "test_case_ids": str(tc)}).json()
    assert js["format"] == "qa-test-data/1" and len(js["rows"]) == 4
    bad = {"rows": [{"tfs_test_case_id": tc, "name": "x", "intended_valid": True, "values": {"password": "plain"}}]}
    r = c.post("/api/test-data/import", json={"format": "json", "content": json.dumps(bad)})
    assert r.status_code == 400 and r.json()["error_code"] == "SECRET_IN_TEST_DATA"


def test_check_rows_unit():
    rows = [models.DataRow(name="a", expected_outcome="", values={"api_key": "abc", "mail": "x@corp.com"})]
    out, issues = check_rows(rows, set())
    assert out[0].values == {"api_key": "{{SECRET:api_key}}", "mail": "x@example.com"}
    assert {i["rule"] for i in issues} == {"SECRET", "SAFE_DESTINATION", "EXPECTED_OUTCOME"}
    assert is_secret_field("Password") and is_secret_field("otp_code") is True and not is_secret_field("username")
