"""TFS hierarchy-aware intelligent discovery: scope selection, Requirement Memory, mapping, coverage and gaps."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from qa_platform.credentials import MemoryBackend
from qa_platform.knowledge.mapping import AppIndex, map_requirements, tokens
from qa_platform.knowledge.requirements import classify_step, expected_pages
from qa_platform.main import create_app
from tests.fake_ado import PAT, FakeAzureDevOps


@pytest.fixture()
def ado():
    return FakeAzureDevOps(hierarchy=True)


@pytest.fixture()
def kc(paths, ado):
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport())
    with TestClient(app) as c:
        r = c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                    "make_active": True})
        assert r.status_code == 200
        yield c


def _resolve(c, option, **body):
    r = c.post("/api/tfs/hierarchy/resolve", json={"option": option, **body})
    assert r.status_code == 200, r.text
    return r.json()["scope"]


def _wait(c, run_id, states, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = c.get(f"/api/automation/discovery-jobs/{run_id}").json()["job"]
        if job["state"] in states:
            return job
        time.sleep(0.2)
    raise AssertionError(f"not {states}: {job['state']}")


# ---- hierarchy -------------------------------------------------------------------------------------


def test_hierarchy_levels_come_from_the_process_and_load_lazily(kc, ado):
    cfg = kc.get("/api/tfs/hierarchy/config").json()
    assert cfg["epic_types"] == ["Epic"] and cfg["feature_types"] == ["Feature"]
    assert cfg["requirement_types"] == ["User Story"] and cfg["test_types"] == ["Test Case"]
    features = kc.get("/api/tfs/hierarchy/items", params={"level": "feature"}).json()["items"]
    assert [(f["label"], f["title"], f["has_children"]) for f in features] == [("F100", "Online Purchase", True),
                                                                              ("F101", "Returns", True)]
    # Expanding a Feature loads its User Stories; expanding a User Story loads its Acceptance Criteria and Test Cases.
    kids = kc.get("/api/tfs/hierarchy/children/100").json()["items"]
    assert [k["label"] for k in kids] == ["US201", "US202"]
    story = kc.get("/api/tfs/hierarchy/children/201").json()["items"]
    assert [(k["level"], k["label"]) for k in story] == [("ac", "AC201-1"), ("ac", "AC201-2"), ("testcase", "TC1268")]
    # Search by id prefix and by title.
    assert [x["id"] for x in kc.get("/api/tfs/hierarchy/items", params={"level": "story", "text": "US202"}).json()["items"]] == [202]
    assert [x["id"] for x in kc.get("/api/tfs/hierarchy/items", params={"level": "feature", "text": "return"}).json()["items"]] == [101]
    # Already loaded nodes are cached for the session; Refresh reads TFS again.
    before = len(ado.requests)
    kc.get("/api/tfs/hierarchy/children/100")
    assert len(ado.requests) == before
    kc.post("/api/tfs/hierarchy/refresh")
    kc.get("/api/tfs/hierarchy/children/100")
    assert len(ado.requests) > before


def test_projects_without_epics_still_work(paths):
    ado = FakeAzureDevOps()  # User Stories and Test Cases only
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport())
    with TestClient(app) as c:
        c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                "make_active": True})
        cfg = c.get("/api/tfs/hierarchy/config").json()
        assert cfg["epic_types"] == [] and cfg["feature_types"] == []
        assert c.get("/api/tfs/hierarchy/items", params={"level": "epic"}).json()["items"] == []
        scope = _resolve(c, "stories", ids=["118"])
        assert scope["counts"] == {"epic": 0, "feature": 0, "story": 1, "ac": 1, "testcase": 1}


def test_feature_resolves_child_stories_and_their_descendants(kc):
    scope = _resolve(kc, "features", ids=["100"])
    assert scope["counts"] == {"epic": 0, "feature": 1, "story": 2, "ac": 4, "testcase": 2}
    assert scope["context"] == ["10"]  # the Epic is kept for traceability, not in scope
    assert set(scope["active"]) == {"100", "201", "202", "AC201-1", "AC201-2", "AC202-1", "AC202-2", "1268", "1269"}
    assert scope["items"]["201"]["parent"] == "100" and scope["items"]["100"]["parent"] == "10"
    multi = _resolve(kc, "features", ids=["100", "101"])
    assert multi["counts"]["feature"] == 2 and multi["counts"]["story"] == 3


def test_epic_scope_resolves_features_and_all_descendants(kc):
    scope = _resolve(kc, "epics", ids=["10"])
    assert scope["counts"] == {"epic": 1, "feature": 2, "story": 3, "ac": 5, "testcase": 3}
    assert scope["context"] == [] and scope["roots"] == ["10"]


def test_sprint_and_all_user_stories_scopes(kc):
    sprint = _resolve(kc, "current_sprint", iteration="Shop\\Sprint 4")
    stories = {k for k in sprint["active"] if sprint["items"][k]["level"] == "story"}
    assert stories == {"118", "201", "202"}  # nothing from Sprint 5
    assert "203" not in sprint["items"] and "100" in sprint["context"]
    assert _resolve(kc, "all_stories", iteration="Shop\\Sprint 4", stories_in="sprint")["counts"]["story"] == 3
    assert _resolve(kc, "all_stories", stories_in="project")["counts"]["story"] == 4
    r = kc.post("/api/tfs/hierarchy/resolve", json={"option": "current_sprint"})
    assert r.status_code == 400 and r.json()["error_code"] == "HIERARCHY_SPRINT_REQUIRED"


def test_entire_project_is_flagged_as_large(kc):
    scope = _resolve(kc, "entire_project")
    assert scope["large"] is True and scope["counts"]["epic"] == 1 and scope["counts"]["story"] == 4


def test_child_only_acceptance_criterion_keeps_parent_context(kc):
    scope = _resolve(kc, "acceptance_criteria", ids=["AC201-1"])
    assert scope["active"] == ["AC201-1"]
    assert scope["context"] == ["10", "100", "201"]
    assert scope["counts"] == {"epic": 0, "feature": 0, "story": 0, "ac": 1, "testcase": 0}
    assert scope["items"]["AC201-1"]["parent"] == "201"
    multi = _resolve(kc, "acceptance_criteria", ids=["AC201-1", "AC202-1"])
    assert multi["counts"]["ac"] == 2 and "AC202-2" not in multi["active"]


def test_test_case_selection_resolves_its_story_and_feature(kc):
    scope = _resolve(kc, "test_cases", ids=["1268", "1269"])
    assert scope["active"] == ["1268", "1269"]
    assert set(scope["context"]) == {"10", "100", "201", "202"}
    assert scope["items"]["1268"]["parent"] == "201"


def test_mixed_selection_is_deduplicated_and_children_can_be_excluded(kc, ado):
    before = len(ado.requests)
    scope = _resolve(kc, "custom", ids=["100", "201", "1268", "AC201-1"], excluded=["202"])
    assert scope["counts"] == {"epic": 0, "feature": 1, "story": 1, "ac": 2, "testcase": 1}
    assert len(scope["active"]) == len(set(scope["active"]))
    assert "202" not in scope["items"] and "1269" not in scope["items"]
    reads = [r for r in ado.requests[before:] if r.url.path.endswith("/_apis/wit/workitems")]
    ids_read = [i for r in reads for i in r.url.params["ids"].split(",")]
    assert len(ids_read) == len(set(ids_read))  # every work item read once


def test_requirement_extraction_helpers():
    assert classify_step("Enter Username") == {"kind": "field", "target": "Username"}
    assert classify_step("Click the Login button") == {"kind": "click", "target": "Login"}
    assert classify_step("Open the application") == {"kind": "navigate", "target": ""}
    assert classify_step('Type "standard_user" in the username field')["target"] == "standard_user"
    assert expected_pages("Inventory page is displayed", "Back on the Cart page") == ["Inventory", "Cart"]
    assert tokens("Customer can log in successfully") == ["login"]
    assert tokens("User Name") == ["username"]


def test_mapping_coverage_and_gaps_on_a_discovery():
    disc = {"pages": [
        {"url": "https://s/", "name": "LoginPage", "title": "Shop", "auth": "login", "headings": [], "elements": [
            {"selector": "#user-name", "name": "Username", "placeholder": "Username", "role": "textbox", "tag": "input", "input_type": "text"},
            {"selector": "#password", "name": "Password", "role": "textbox", "tag": "input", "input_type": "password"},
            {"selector": "#login-button", "name": "Login", "role": "button", "tag": "input", "input_type": "submit"}]},
        {"url": "https://s/inventory.html", "name": "InventoryPage", "title": "Shop", "headings": ["Products"], "elements": [
            {"selector": "#add", "name": "Add to cart", "role": "button", "tag": "button"}]},
        {"url": "https://s/about.html", "name": "AboutPage", "title": "About us", "headings": [], "elements": []}],
        "flows": [{"name": "Login → Inventory", "pages": ["LoginPage", "InventoryPage"]}]}
    memory = {"option": "features", "user_stories": [{"id": 201, "label": "US201", "title": "Customer logs in", "active": True,
                                                       "parent": None, "children": ["AC201-1", "AC201-2", "1268"]}],
              "acceptance_criteria": [
                  {"id": "AC201-1", "label": "AC201-1", "title": "Customer can login successfully", "text": "Customer can login successfully", "active": True, "parent": "201", "children": []},
                  {"id": "AC201-2", "label": "AC201-2", "title": "Customer can apply a discount coupon", "text": "Customer can apply a discount coupon", "active": True, "parent": "201", "children": []}],
              "test_cases": [{"id": 1268, "label": "TC1268", "title": "Login", "active": True, "parent": "201", "children": [], "steps": [
                  {"index": 1, "action": "Open the application", "expected": "Login page is displayed", "kind": "navigate", "target": ""},
                  {"index": 2, "action": "Enter Username", "expected": "", "kind": "field", "target": "Username"},
                  {"index": 3, "action": "Click Login", "expected": "Products are displayed", "kind": "click", "target": "Login"},
                  {"index": 4, "action": "Enter Promo Code", "expected": "", "kind": "field", "target": "Promo Code"}]}]}
    m = map_requirements(memory, disc)
    assert m["acceptance_criteria"]["AC201-1"]["mapped"] is True
    assert {x["page"] for x in m["acceptance_criteria"]["AC201-1"]["mapped_to"]} >= {"LoginPage"}
    assert m["acceptance_criteria"]["AC201-2"]["mapped"] is False
    tc = m["test_cases"]["1268"]
    assert [s["match"]["selector"] if s["match"] and s["match"]["selector"] else (s["match"] or {}).get("page") for s in tc["steps"]] == \
        ["LoginPage", "#user-name", "#login-button", None]
    assert tc["steps"][2]["expected_match"]["page"] == "InventoryPage"
    assert tc["coverage"] == 83  # 5 of 6 parts (steps + expected results)
    story = m["tree"][0]
    assert story["label"] == "US201" and story["coverage"] == round((100 + 0 + 83) / 3)
    kinds = {(g["kind"], g.get("label")) for g in m["requirement_gaps"]}
    assert ("acceptance_criterion", "AC201-2") in kinds and ("test_step", "TC1268") in kinds
    # Partial scope: the unmapped About page is out-of-scope discovery, not an application gap.
    assert [g["page"] for g in m["out_of_scope"]] == ["AboutPage"] and m["application_gaps"] == []
    whole = map_requirements({**memory, "option": "entire_project"}, disc)
    assert [g["page"] for g in whole["application_gaps"]] == ["AboutPage"]
    assert AppIndex(disc).match("Password", "field")["selector"] == "#password"


# ---- end to end ------------------------------------------------------------------------------------


def test_tfs_requirements_only_builds_memory_without_opening_the_application(kc):
    r = kc.post("/api/knowledge/discoveries", json={"source": "tfs", "scope": {"option": "features", "ids": ["100"]}})
    assert r.status_code == 202, r.text
    job = _wait(kc, r.json()["run_id"], {"COMPLETED", "FAILED"})
    assert job["state"] == "COMPLETED", job["error"]
    states = [h["state"] for h in job["history"]]
    assert ["READING_REQUIREMENTS", "RESOLVING_HIERARCHY", "BUILDING_REQUIREMENT_MEMORY", "COMPLETED"] == \
        [s for s in states if s in ("READING_REQUIREMENTS", "RESOLVING_HIERARCHY", "BUILDING_REQUIREMENT_MEMORY", "COMPLETED")]
    assert "OPENING_APPLICATION" not in states and job["discovery_id"] is None
    mem = kc.get(f"/api/knowledge/requirement-memory/{job['requirement_memory_id']}").json()["memory"]
    assert [s["label"] for s in mem["user_stories"]] == ["US201", "US202"]
    assert [f["id"] for f in mem["features"]] == [100] and mem["epics"][0]["active"] is False
    tc = next(t for t in mem["test_cases"] if t["id"] == 1268)
    assert [s["action"] for s in tc["steps"]] == ["Open the application", "Enter Username", "Enter Password", "Click Login"]
    assert tc["steps"][3]["expected"] == "Products are displayed" and tc["tests_requirement"] == 201
    assert {"Username", "Password"} <= set(mem["expected"]["fields"]) and "Login" in mem["expected"]["pages"]
    # Running the same scope again updates the same memory instead of creating a duplicate.
    r2 = kc.post("/api/knowledge/discoveries", json={"source": "tfs", "scope": {"option": "features", "ids": ["100"]}})
    job2 = _wait(kc, r2.json()["run_id"], {"COMPLETED", "FAILED"})
    assert job2["requirement_memory_id"] == job["requirement_memory_id"]
    items = kc.get("/api/knowledge/requirement-memory").json()["items"]
    assert len(items) == 1 and items[0]["version"] == 2


def test_tfs_plus_application_maps_requirements_to_the_discovered_shop(kc, site):
    rt = kc.app.state.runtime
    rt.automation.creds.save("shop", "standard", "standard_user", "secret_sauce", None)
    r = kc.post("/api/knowledge/discoveries", json={
        "source": "tfs_app", "base_url": f"{site}/shop/index.html", "max_pages": 8, "max_depth": 2,
        "application": "shop", "credential_role": "standard", "scope": {"option": "features", "ids": ["100"]}})
    assert r.status_code == 202, r.text
    job = _wait(kc, r.json()["run_id"], {"COMPLETED", "FAILED"}, timeout=240)
    assert job["state"] == "COMPLETED", job["error"]
    order = [h["state"] for h in job["history"]]
    for a, b in [("READING_REQUIREMENTS", "BUILDING_REQUIREMENT_MEMORY"), ("BUILDING_REQUIREMENT_MEMORY", "OPENING_APPLICATION"),
                 ("OPENING_APPLICATION", "AUTHENTICATING"), ("AUTHENTICATING", "MAPPING_REQUIREMENTS"),
                 ("MAPPING_REQUIREMENTS", "ANALYZING_GAPS"), ("ANALYZING_GAPS", "COMPLETED")]:
        assert order.index(a) < order.index(b), (a, b, order)
    k = kc.get(f"/api/knowledge/{job['knowledge_id']}").json()["knowledge"]
    assert k["application"] == "shop" and k["discovery_id"] == job["discovery_id"]
    acs = k["acceptance_criteria"]
    assert acs["AC201-1"]["mapped"] is True and acs["AC202-1"]["mapped"] is True  # login, add product to cart
    assert acs["AC202-2"]["mapped"] is False  # no discount coupon in the application
    tc = k["test_cases"]["1268"]
    assert [s["match"]["selector"] for s in tc["steps"][1:]] == ['[data-test="username"]', '[data-test="password"]',
                                                                '[data-test="login-button"]']
    feature = k["tree"][0]["children"][0] if k["tree"][0]["level"] == "epic" else k["tree"][0]
    assert feature["label"] == "F100" and 0 < feature["coverage"] < 100
    assert "LoginPage" in feature["application_areas"] and "InventoryPage" in feature["application_areas"]
    assert any(g["label"] == "AC202-2" for g in k["requirement_gaps"])
    assert k["coverage"]["by_level"]["ac"]["items"] == 4
    # The Automation Engineering Agent sees this knowledge when it generates TC1268.
    text = rt.studio._knowledge_text("1268", "shop")
    assert 'Step 2: Enter Username -> LoginPage / Username ([data-test="username"])' in text
    assert "Business flow: Login" in text
    assert kc.get("/api/knowledge", params={"application": "shop"}).json()["items"][0]["id"] == k["id"]


def test_intelligent_discovery_validation(kc):
    r = kc.post("/api/knowledge/discoveries", json={"source": "tfs_app", "scope": {"option": "features", "ids": ["100"]}})
    assert r.status_code == 400 and r.json()["error_code"] == "URL_INVALID"
    r = kc.post("/api/knowledge/discoveries", json={"source": "tfs"})
    assert r.status_code == 400 and r.json()["error_code"] == "HIERARCHY_SELECTION_EMPTY"
    r = kc.post("/api/knowledge/discoveries", json={"source": "tfs", "scope": {"option": "features", "ids": []}})
    assert r.status_code == 400 and r.json()["error_code"] == "HIERARCHY_SELECTION_EMPTY"
