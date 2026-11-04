"""23.3.16: Delete script on AI-Driven Automation > Script Inventory, Guided Browser Automation and Hybrid Test
Automation. A generated script is made by the real generation job (stand-in TFS and AI, as in 23.3.15)."""
import io
import json
import os
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

APP = Path(os.environ.get("APP_DIR", Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(APP))

from test_automation_focus_23315 import ANSWER, rt, run_job  # noqa: E402,F401  (fixture reused)


def ok_handler(req):
    if req.url.path.endswith("/props"):
        return httpx.Response(404)
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(ANSWER)}, "finish_reason": "stop"}]})


@pytest.fixture()
def client(rt):
    from fastapi.testclient import TestClient
    import gc
    from fastapi import FastAPI
    app = next(o for o in gc.get_objects() if isinstance(o, FastAPI) and getattr(o.state, "runtime", None) is rt)
    return TestClient(app)  # the app is already started by the rt fixture


def generated(rt):
    j = run_job(rt, ok_handler)
    assert j["status"] == "COMPLETED", j
    inv = [s for s in rt.studio.inventory() if s["framework"]]
    assert len(inv) == 1
    return inv[0]


def test_delete_generated_script_removes_it_from_inventory_zip_and_run(rt, client):
    s = generated(rt)
    ws = rt.studio.workspace()
    rec = rt.studio._record(s["id"])
    assert ws.exists(rec["file"]) and ws.exists(rec["data_file"])
    zipped = zipfile.ZipFile(io.BytesIO(client.get("/api/automation/studio/export").content)).namelist()
    assert f"automation-workspace/{rec['file']}" in zipped

    r = client.delete(f"/api/automation/studio/scripts/{s['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] and set(body["moved"]) == {rec["file"], rec["data_file"]}

    assert not [x for x in client.get("/api/automation/studio/inventory").json()["items"] if x["id"] == s["id"]]
    assert not ws.exists(rec["file"]) and not ws.exists(rec["data_file"])
    kept = ws.root / body["kept_in"]
    assert (kept / rec["file"]).is_file() and (kept / rec["data_file"]).is_file()
    names = zipfile.ZipFile(io.BytesIO(client.get("/api/automation/studio/export").content)).namelist()
    assert not any(".deleted" in n or n.endswith(Path(rec["file"]).name) for n in names)
    # deleted once only; the versions stay
    assert client.delete(f"/api/automation/studio/scripts/{s['id']}").status_code == 404
    assert rt.studio.versions(s["id"])
    # Play Selected refuses it
    runs = client.post("/api/automation/studio/run-many", json={"script_ids": [s["id"]]}).json()["runs"]
    assert runs[0]["run_id"] is None


def test_generating_the_same_test_case_again_after_delete_works(rt, client):
    s = generated(rt)
    client.delete(f"/api/automation/studio/scripts/{s['id']}")
    again = generated(rt)
    assert again["id"] != s["id"] and again["file_verified"]


def test_running_script_is_not_deleted(rt, client):
    s = generated(rt)
    rt.studio.db.execute("UPDATE framework_scripts SET last_result = 'RUNNING' WHERE id = ?", (s["id"],))
    r = client.delete(f"/api/automation/studio/scripts/{s['id']}")
    assert r.status_code == 409 and "running" in r.json()["message"]
    assert [x for x in rt.studio.inventory() if x["id"] == s["id"]]


def test_shared_data_file_is_kept_for_the_other_script(rt, client):
    s = generated(rt)
    rec = rt.studio._record(s["id"])
    rt.studio.db.execute("INSERT INTO framework_scripts (id, method, file, data_file, status, generated_at, updated_at)"
                         " VALUES ('FWS-OTHER00001', 'ai', 'tests/test_other.py', ?, 'GENERATED', 'x', 'x')",
                         (rec["data_file"],))
    body = client.delete(f"/api/automation/studio/scripts/{s['id']}").json()
    assert body["moved"] == [rec["file"]]
    assert rt.studio.workspace().exists(rec["data_file"])


@pytest.mark.parametrize("mode", ["guided", "hybrid"])
def test_delete_recorded_script(rt, client, mode):
    from qa_platform.automation.dsl import Script
    script = rt.automation.scripts.save(Script.model_validate({
        "id": "", "name": f"{mode} login", "mode": mode, "base_url": "https://shop.test/",
        "steps": [{"action": "goto", "value": "https://shop.test/"}]}))
    ids = lambda: [x["id"] for x in client.get("/api/automation/scripts").json()["items"]]  # noqa: E731
    assert script.id in ids()
    assert script.id in [x["id"] for x in rt.studio.inventory()]  # also listed in the Script Inventory
    r = client.delete(f"/api/automation/scripts/{script.id}")
    assert r.status_code == 200 and r.json()["deleted"]
    assert script.id not in ids() and script.id not in [x["id"] for x in rt.studio.inventory()]
    assert list((rt.automation.scripts.root / "_archived").glob(f"{script.id}-*"))
    assert client.delete(f"/api/automation/scripts/{script.id}").status_code == 404
