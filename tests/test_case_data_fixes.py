"""Test case generation for large stories (one AI call per few criteria), concrete steps, tags, several suites at
once, deleting and editing TFS test cases, and test data read from / written to the TFS 'Test Data' tab."""

from __future__ import annotations

import httpx

from qa_platform.design import models, suite_cases, testing_types
from qa_platform.design.data_agent import matrix_html, parse_matrix, parse_parameters
from tests.fake_llm import design_cases
from tests.test_suite_cases import _gen_data, _generate, _suite, _wait, env  # noqa: F401  (env is a fixture)

BIG_STORY = 118
CRITERIA = ["AC01: Customer can log in using valid credentials.",
            "AC02: Customer can view the available products after successful login.",
            "AC03: Successful login redirects the customer to the product inventory.",
            "AC04: Customer can add a product to the shopping cart.",
            "AC05: Customer can open the shopping cart and review selected products."]

TFS_MATRIX = ("<div><h3>Test Data Matrix</h3><p><b>Combination 1: Valid representative data</b><br><b>Username:</b> "
              "valid_user<br><b>Password:</b> ValidPass123!<br><b>Expected Result:</b> The Test Case behavior completes "
              "successfully with the supplied valid data.</p><p><b>Combination 2: Wrong password</b><br>"
              "<b>Username:</b> valid_user<br><b>Password:</b> nope<br><b>Expected Result:</b> An error message is "
              "shown.</p></div>")


def _big_story(ado):
    ado.items[BIG_STORY] = {
        "System.Title": "Customer completes an online purchase", "System.WorkItemType": "User Story",
        "System.State": "Active", "System.IterationPath": "Shop\\Sprint 4",
        "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul>" + "".join(f"<li>{c}</li>" for c in CRITERIA) + "</ul>"}
    ado.relations[BIG_STORY] = []


def _calls(llm, marker="suite-aware test case designer"):
    return [b for b in llm.calls if marker in b["messages"][0]["content"]]


def test_large_story_is_generated_a_few_criteria_per_call(env):
    c, ado, llm = env
    _big_story(ado)
    run_id, result = _generate(c, _suite(c, "functional"), [BIG_STORY])
    story = result["stories"][0]
    assert [x["id"] for x in story["criteria"]] == ["AC01", "AC02", "AC03", "AC04", "AC05"]
    calls = _calls(llm)
    assert len(calls) == 3  # 2 + 2 + 1 criteria
    for body in calls:  # each call only carries its own criteria and a generous timeout-free small answer budget
        assert body["max_tokens"] <= 4096
        listed = body["messages"][1]["content"].split("Acceptance criteria:\n", 1)[1].split("\n\n", 1)[0]
        assert 1 <= len(listed.splitlines()) <= 2
    refs = [tc["ref"] for tc in story["test_cases"]]
    assert refs == [f"TC{n}" for n in range(1, len(refs) + 1)]  # unique across calls
    assert {a for tc in story["test_cases"] for a in tc["ac_ids"]} == {"AC01", "AC02", "AC03", "AC04", "AC05"}
    assert all(r["status"] != "Not Covered" for r in story["coverage"])
    assert story["failed_criteria"] == []
    # the prompt asks for concrete steps and shows the required format
    system = calls[0]["messages"][0]["content"]
    assert "one action per step" in system and "Enter 'standard_user' in the Username field." in system


def test_timeout_retries_criteria_one_by_one_and_keeps_partial_results(env):
    c, ado, llm = env
    _big_story(ado)

    def slow(body):
        listed = body["messages"][1]["content"].split("Acceptance criteria:\n", 1)[1].split("\n\n", 1)[0]
        if len(listed.splitlines()) > 1 or listed.startswith("AC04"):
            raise httpx.ReadTimeout("model is busy")
        return design_cases(body)

    llm.extra["suite-aware test case designer"] = slow
    run_id, result = _generate(c, _suite(c, "functional"), [BIG_STORY])
    story = result["stories"][0]
    assert story["failed_criteria"] == ["AC04"]
    assert any("AC04" in n and "Generate again" in n for n in story["notes"])
    covered = {a for tc in story["test_cases"] for a in tc["ac_ids"]}
    assert covered == {"AC01", "AC02", "AC03", "AC05"}
    row = next(r for r in story["coverage"] if r["ac_id"] == "AC04")
    assert row["status"] == "Not Covered" and "did not finish" in row["reason"]
    # the generation timeout is used for these calls, not the short default
    assert c.get("/api/ai/settings").json()["generation_timeout_seconds"] == 600


def test_every_criterion_failing_fails_the_run_with_the_reason(env):
    c, ado, llm = env

    def down(body):
        raise httpx.ReadTimeout("model is busy")

    llm.extra["suite-aware test case designer"] = down
    r = c.post("/api/design/suite-runs", json={"plan_id": 7, "suite_id": _suite(c, "functional"), "user_story_ids": [201]})
    body = _wait(c, f"/api/design/suite-runs/{r.json()['run_id']}")
    assert body["status"] == "FAILED" and "did not answer in time" in body["result"]["error"]


def test_summary_steps_are_flagged_and_sent_back_for_review():
    crit = [{"id": "AC-01", "text": "Customer can complete the purchase", "hash": "x"}]
    case = models.SuiteTestCase(ref="TC1", title="Purchase", ac_ids=["AC-01"], steps=[
        models.TestStep(action="Complete the supported purchase workflow successfully.",
                        expected="The order confirmation page shows the order number.")])
    check = suite_cases.validate(models.SuiteCaseSet(test_cases=[case]), crit, suite_cases.decompose(crit),
                                 testing_types.get("functional"), [])
    messages = [i["message"] for i in check["per_case"]["TC1"]]
    assert any("summarises a whole flow" in m for m in messages) and any("Only one step" in m for m in messages)


def test_tags_name_the_testing_type_and_the_user_story(env):
    c, ado, _ = env
    run_id, result = _generate(c, _suite(c, "smoke"), [201])
    ref = result["stories"][0]["test_cases"][0]["ref"]
    pub = c.post(f"/api/design/suite-runs/{run_id}/publish", json={"selection": {"201": [ref]}}).json()
    tags = ado.items[pub["created"][0]["id"]]["System.Tags"].split("; ")
    assert tags[:3] == ["AI Generated", "Smoke Testing", "US201"]


def test_several_suites_generate_one_run_each(env):
    c, _, _ = env
    smoke, regression = _suite(c, "smoke"), _suite(c, "regression")
    r = c.post("/api/design/suite-runs", json={"plan_id": 7, "suite_ids": [smoke, regression], "user_story_ids": [201]})
    assert r.status_code == 202, r.json()
    runs = r.json()["runs"]
    assert [x["testing_type"] for x in runs] == ["Smoke Testing", "Regression Testing"]
    for x in runs:
        body = _wait(c, f"/api/design/suite-runs/{x['run_id']}")
        assert body["status"] == "PASSED"
        assert body["result"]["suite"]["id"] == x["suite_id"]
        assert all(tc["title"].startswith(x["testing_type"] + " - ") for tc in body["result"]["stories"][0]["test_cases"])
    r = c.post("/api/design/suite-runs", json={"plan_id": 7, "suite_ids": [], "user_story_ids": [201]})
    assert r.status_code == 400 and r.json()["error_code"] == "SUITE_REQUIRED"


def test_delete_and_edit_test_cases(env):
    c, ado, _ = env
    run_id, result = _generate(c, _suite(c, "functional"), [201])
    refs = [tc["ref"] for tc in result["stories"][0]["test_cases"][:2]]
    created = [x["id"] for x in c.post(f"/api/design/suite-runs/{run_id}/publish",
                                        json={"selection": {"201": refs}}).json()["created"]]
    # edit: steps written in the Steps format
    r = c.patch(f"/api/tfs/work-items/{created[0]}", json={"fields": {"title": "Functional Testing - Renamed", "steps": [
        {"action": "Open the login page", "expected": "Login form is shown"},
        {"action": "Click Login", "expected": "Products page is shown"}]}})
    assert r.status_code == 200, r.json()
    assert '<step id="3"' in ado.items[created[0]]["Microsoft.VSTS.TCM.Steps"]
    item = c.get(f"/api/tfs/work-items/{created[0]}").json()["item"]
    assert [s["action"] for s in item["step_list"]] == ["Open the login page", "Click Login"]

    # delete one, then several at once (one of them already gone)
    r = c.delete(f"/api/tfs/work-items/{created[0]}").json()
    assert r["result"] == "deleted permanently" and created[0] in ado.deleted_test_cases
    assert not any(x["id"] == created[0] for lst in c.get("/api/design/traceability/201").json()["matrix"]
                   for v in lst["by_testing_type"].values() for x in v)
    r = c.post("/api/tfs/work-items/delete", json={"ids": [created[1], created[0]]}).json()
    assert [d["id"] for d in r["deleted"]] == [created[1]] and r["failed"][0]["id"] == created[0]
    assert created[1] not in ado.items
    # a work item that is not a test case goes to the Recycle Bin
    assert c.delete("/api/tfs/work-items/1420").json()["result"] == "moved to the Recycle Bin"
    assert 1420 in ado.recycled


def test_matrix_format_round_trip_and_parameters():
    rows = parse_matrix(TFS_MATRIX)
    assert rows[0] == {"name": "Valid representative data", "values": {"Username": "valid_user", "Password": "ValidPass123!"},
                       "expected_outcome": "The Test Case behavior completes successfully with the supplied valid data.",
                       "violated_rule": None}
    assert rows[1]["name"] == "Wrong password"
    html_out = matrix_html([{"name": "Valid user", "dataset": {"user_name": "a"}, "expected_outcome": "OK"}], "Dataset DS-1-x v2")
    assert "<strong>Combination 1: Valid user</strong>" in html_out and "<strong>User name:</strong> a" in html_out
    back = parse_matrix(html_out)
    assert back[0]["values"] == {"User name": "a"} and back[0]["expected_outcome"] == "OK"
    assert parse_parameters("<NewDataSet><Table1><user>x</user><pin>1</pin></Table1></NewDataSet>")[0]["values"] == {
        "user": "x", "pin": "1"}


def _test_data_tab(ado):
    ado.work_item_fields["Test Case"] = [("Custom.AITestDataID", "Test Data ID"), ("Custom.TestData", "Test Data")]
    ado.items[1268]["Custom.TestData"] = TFS_MATRIX
    ado.items[1268]["System.Tags"] = "Functional Testing; US201"


def test_existing_test_data_is_loaded_from_the_tfs_test_data_tab(env):
    c, ado, _ = env
    _test_data_tab(ado)
    r = c.post("/api/test-data/sync", json={"user_story_ids": [201]}).json()
    res = {x["test_case_id"]: x for x in r["results"]}
    assert res[1268]["status"] == "imported" and res[1268]["rows"] == 2
    assert "Test Data field (Custom.TestData)" == res[1268]["source"]
    rows = c.get("/api/test-data", params={"user_story_id": "201"}).json()["items"]
    valid = next(x for x in rows if x["name"] == "Valid representative data")
    assert valid["dataset"] == {"Username": "valid_user", "Password": "{{SECRET:password}}"}  # never stored in plain text
    assert valid["testing_type"] == "functional" and valid["ac_ids"] == ["AC-01"]
    wrong = next(x for x in rows if x["name"] == "Wrong password")
    assert wrong["intended_valid"] is False and wrong["dataset"]["Password"] == "nope"
    # nothing changed in TFS: loading again adds nothing
    assert c.post("/api/test-data/sync", json={"test_case_ids": [1268]}).json()["results"][0]["status"] == "unchanged"
    assert len(c.get("/api/test-data", params={"test_case_id": 1268}).json()["items"]) == 2
    # coverage counts the TFS rows and the test case made outside the application
    cov = {x["ac_id"]: x for x in c.get("/api/test-data/coverage/201").json()["matrix"]}
    assert cov["AC-01"]["status"] == "Data covered" and cov["AC-01"]["test_cases"][0]["test_case_id"] == 1268


def test_generated_data_is_written_to_the_test_data_tab_as_a_matrix(env):
    c, ado, _ = env
    _test_data_tab(ado)
    _gen_data(c, test_case_ids=[1268])
    r = c.post("/api/test-data/tfs/1268", json={"target": "auto"}).json()
    assert r["target"] == "Test Data tab (Custom.TestData)"
    written = ado.items[1268]["Custom.TestData"]
    assert written.startswith("<h3>Test Data Matrix</h3>") and "<strong>Combination 1: " in written
    assert "Expected Result:" in written and "ValidPass123!" not in written
    assert ado.items[1268]["Custom.AITestDataID"].startswith("DS-1268-")
    # reading it back recognises the application's own dataset and does not import it twice
    c.post("/api/tfs/cache/clear")
    assert c.post("/api/test-data/sync", json={"test_case_ids": [1268]}).json()["results"][0]["status"] == "in sync"


def test_generate_for_several_user_stories(env):
    c, _, _ = env
    results = _gen_data(c, user_story_ids=[201, 202])
    assert {x["test_case_id"] for x in results} == {1268, 1269}
