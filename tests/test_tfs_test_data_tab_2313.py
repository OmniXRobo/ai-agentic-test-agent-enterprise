"""23.3.13: test data is written to the test case's "Test Data" tab (custom fields) as well as the Summary."""
import contextlib
import os
import sys

import pytest

sys.path.insert(0, os.environ.get("QA_APP", "app"))
from qa_platform.design import data_agent as da  # noqa: E402
from qa_platform.design import service as ds  # noqa: E402
from qa_platform.design import suite_cases as sc  # noqa: E402
from qa_platform.design import tfs_test_data as tt  # noqa: E402
from qa_platform.errors import QAError  # noqa: E402

TAB = [{"referenceName": "Custom.TestDataID", "name": "Test Data ID"},
       {"referenceName": "Custom.TestData", "name": "Test Data"},
       {"referenceName": "System.Title", "name": "Title"}]


class Cache:
    def get_or_set(self, key, fn):
        return fn()


class Client:
    def __init__(self, fields, types):
        self.fields, self.types = fields, types

    def work_item_type_fields(self, name):
        return self.fields

    def request(self, method, path, **kw):
        return {"type": self.types.get(path.rsplit("/", 1)[-1], "string")}


class Tfs:
    def __init__(self, fields=TAB, types=None, refuse=()):
        self.client = Client(fields, types or {"Custom.TestData": "html", "Custom.TestDataID": "string"})
        self.meta_cache = Cache()
        self.refuse = set(refuse)
        self.updates, self.created = [], []
        self.refs = {f["referenceName"] for f in fields} | {"System.Description", "System.Title",
                                                             "Microsoft.VSTS.TCM.Steps", "System.Tags"}

    def _with_client(self, fn):
        return fn(self.client)

    def active_mode(self):
        return "cloud"

    def profile(self):
        return {"project": "p"}

    def detail(self, i):
        return {"id": i, "type": "Test Case", "description": "<p>Objective: x</p>", "extra_fields": {}}

    def type_field_refs(self, t):
        return self.refs

    def _check(self, keys):
        if self.refuse & set(keys):
            raise QAError("TFS_REQUEST_FAILED", "TFS request failed with HTTP 400.", status=502)

    def update(self, i, values):
        self._check(values)
        self.updates.append(values)
        return {"item": {}, "skipped_fields": []}

    def build_patch(self, t, values):
        refs = {"title": "System.Title", "description": "System.Description", "tags": "System.Tags",
                "priority": "x.p", "iteration_path": "x.i", "area_path": "x.a"}
        return [{"op": "add", "path": f"/fields/{refs.get(k, k)}", "value": v} for k, v in values.items() if v], []

    def create_raw(self, t, patch):
        self._check(p["path"].split("/")[-1] for p in patch)
        self.created.append({p["path"].split("/")[-1]: p["value"] for p in patch if p["path"].startswith("/fields/")})
        return {"id": 1395}

    def default_type(self, k):
        return "Test Case"

    def work_item_url(self, i):
        return f"http://tfs/{i}"

    def add_to_suite(self, *a):
        pass


class Act:
    def progress(self, *a):
        pass

    def complete(self, *a):
        pass


class Agent:
    @contextlib.contextmanager
    def activity(self, *a, **k):
        yield Act()


ROWS = [{"name": "Valid quantity", "dataset": {"product_name": "Wireless Headphones", "new_quantity": 2},
         "dataset_key": "DS-1395-functional", "version": 1, "scenario": "valid", "intended_valid": True,
         "expected_outcome": "Quantity shows 2"}]


def data_service(tfs):
    svc = da.TestDataService.__new__(da.TestDataService)
    svc.tfs = tfs
    svc.design = type("D", (), {"data": Agent()})()
    svc.list = lambda **k: [dict(r) for r in ROWS]
    return svc


def test_write_to_tfs_fills_the_test_data_tab_and_the_summary():
    tfs = Tfs()
    out = data_service(tfs).write_to_tfs(1395)
    v = tfs.updates[0]
    assert "Combination 1: Valid quantity" in v["Custom.TestData"] and "Wireless Headphones" in v["Custom.TestData"]
    assert v["Custom.TestDataID"] == "DS-1395-functional"
    assert "Test Data Matrix" in v["description"] and v["description"].startswith("<p>Objective: x</p>")
    assert "Test Data tab" in out["target"] and "Summary" in out["target"]


def test_without_the_tab_only_the_summary_is_written():
    tfs = Tfs(fields=[{"referenceName": "System.Title", "name": "Title"}])
    out = data_service(tfs).write_to_tfs(1395)
    assert set(tfs.updates[0]) == {"description"} and out["target"] == "Summary"


def test_plain_text_field_and_numeric_id_field():
    tfs = Tfs(types={"Custom.TestData": "plainText", "Custom.TestDataID": "integer"})
    data_service(tfs).write_to_tfs(1395)
    v = tfs.updates[0]
    assert "<" not in v["Custom.TestData"] and "Product name: Wireless Headphones" in v["Custom.TestData"]
    assert "Custom.TestDataID" not in v  # "DS-1395-functional" is not a number


def test_refused_tab_fields_still_write_the_summary():
    tfs = Tfs(refuse={"Custom.TestDataID"})
    out = data_service(tfs).write_to_tfs(1395)
    assert len(tfs.updates) == 1 and set(tfs.updates[0]) == {"description"}
    assert "Custom.TestDataID" in out["skipped_fields"] and "refused" in out["target"]


def test_design_write_test_data_also_fills_the_tab():
    tfs = Tfs()
    svc = ds.DesignService.__new__(ds.DesignService)
    svc.tfs, svc.data = tfs, Agent()
    svc.list_test_data = lambda **k: [{"name": "Data 1", "kind": "valid", "dataset": {"quantity": 2}}]
    svc.write_test_data_to_tfs(1395, summary=True, parameters=False)
    v = tfs.updates[0]
    assert "Quantity:</strong> 2" in v["Custom.TestData"] and v["Custom.TestDataID"] == "DS-1395" and "description" in v


def _publish(tfs):
    svc = sc.SuiteCaseService.__new__(sc.SuiteCaseService)
    svc.tfs = tfs
    svc.design = type("D", (), {"suite": Agent()})()
    case = {"ref": "TC1", "title": "Increase product quantity", "priority": 2, "ac_ids": ["AC02"],
            "steps": [{"action": "Click +", "expected": "Quantity is 2"}], "objective": "o", "risk": "medium",
            "scenario": "positive", "automation": "automatable",
            "test_data": ["product_name = 'Wireless Headphones'", "initial_quantity = 1", "new_quantity = 2"]}
    result = {"suite": {"id": 1371, "name": "Functional Testing", "testing_type_name": "Functional Testing"},
              "plan_id": 1368, "meta": {"agent_version": "a", "prompt_version": "p", "model": {"model": "m"}, "requirement_revision": {}},
              "stories": [{"story": {"id": 1177, "title": "Cart", "iteration": "S2", "rev": 2},
                           "criteria": [{"id": "AC02", "text": "Increase", "hash": "h"}], "test_cases": [case]}]}
    svc.get = lambda run_id: result
    svc._save = lambda r: None
    svc._trace_case = lambda *a: None
    return svc.publish("R", {"1177": ["TC1"]})


def test_new_test_cases_get_the_test_data_tab():
    tfs = Tfs()
    out = _publish(tfs)
    f = tfs.created[0]
    assert out["created"][0]["id"] == 1395
    assert "Product name:</strong> Wireless Headphones" in f["Custom.TestData"]
    assert f["Custom.TestDataID"] == "US1177-TC1"
    assert "Test data:" in f["System.Description"]  # the Summary as before


def test_new_test_case_is_created_once_without_the_tab_when_tfs_refuses_it():
    tfs = Tfs(refuse={"Custom.TestData"})
    _publish(tfs)
    assert len(tfs.created) == 1 and "Custom.TestData" not in tfs.created[0]


def test_rows_from_lines():
    rows = tt.rows_from_lines(["a = 1", "b: 'x'", "free text"], "Case")
    assert rows == [{"name": "Case", "values": {"a": "1", "b": "x", "Data 3": "free text"}}]
    assert tt.rows_from_lines([], "Case") == []
