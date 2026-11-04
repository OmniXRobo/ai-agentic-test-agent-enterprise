"""23.3.20: Record more steps opens the browser without recording; Record / Pause / Resume (on the page and in the
browser's own bar); steps go in at several insert positions or replace steps; the earlier steps can be played first.
Runs in a real headless Chromium."""
import functools
import os
import socket
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

APP = Path(os.environ.get("APP_DIR", Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(APP))

from qa_platform.automation.dsl import Script, Step, Target  # noqa: E402
from qa_platform.automation.service import merge_segments  # noqa: E402

PAGE = """<!doctype html><html><body><h1>Shop</h1>
<div id="log"></div>
%s
<script>document.querySelectorAll("button").forEach(b => b.addEventListener("click",
  () => { document.getElementById("log").textContent += b.id + " "; }));</script></body></html>
""" % "".join(f'<button id="{i}">{i.upper()}</button>' for i in ["b1", "b2", "b3", "x1", "x2", "x3", "x4", "x5"])


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("site")
    (root / "shop.html").write_text(PAGE, encoding="utf-8")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a: None
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/shop.html"
    server.shutdown()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("QA_HOME", str(tmp_path))
    from fastapi.testclient import TestClient
    from qa_platform.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c, app.state.runtime


def click(sid):
    return Step(action="click", target=Target(selector=f"#{sid}"), description=f"Click {sid}")


def wait_state(c, run_id, state, timeout=30):
    for _ in range(int(timeout / 0.2)):
        s = c.get(f"/api/automation/recordings/{run_id}").json()
        if s["state"] == state:
            return s
        time.sleep(0.2)
    raise AssertionError(f"state is {s['state']}, not {state}: {s.get('note')}")


def ids(steps):
    return [(s["target"] or {}).get("selector", s["action"]) if isinstance(s, dict) else
            (s.target.selector if s.target else s.action) for s in steps]


def test_merge_segments_counts_original_positions():
    orig = [Step(action="goto", value="u"), click("b1"), click("b2"), click("b3")]
    segs = [{"at": 2, "replace": 0, "steps": [click("x1")]}, {"at": 4, "replace": 0, "steps": [click("x3")]},
            {"at": 1, "replace": 1, "steps": [click("x4")]}, {"at": 2, "replace": 0, "steps": [click("x5")]}]
    out, summary = merge_segments(orig, segs)
    assert ids(out) == ["goto", "#x4", "#x1", "#x5", "#b2", "#b3", "#x3"]
    assert summary == ["inserted 1 step at step 3", "inserted 1 step at step 5",
                       "replaced step 2 with 1 recorded step", "inserted 1 step at step 3"]


def test_record_more_steps_opens_without_recording_then_record_pause_insert_replace(client, site):
    c, rt = client
    a = rt.automation
    script = a.scripts.save(Script(id="SCR-0000AAAA20", name="Shop", mode="guided", base_url=site, browser="chromium",
                                   steps=[Step(action="goto", value=site), click("b1"), click("b2"), click("b3")]))
    r = c.post("/api/automation/recordings", json={"start_url": site, "append_to": script.id, "insert_at": 2,
                                                   "replay": True, "headless": True})
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    session = a.recording(run_id)["session"]
    s = wait_state(c, run_id, "ready")
    assert "Steps 1 to 2 were played" in s["note"], s["note"]
    log = session.command("text", "#log")
    assert log.strip() == "b1", log  # step 2 (Click b1) was played; step 1 opened the page

    # browsing before Record is not recorded
    session.command("click", "#x2")
    time.sleep(0.5)
    assert wait_state(c, run_id, "ready")["count"] == 0

    s = c.post(f"/api/automation/recordings/{run_id}/control", json={"action": "record"}).json()
    assert s["state"] == "recording" and s["where"] == "insert at step 3"
    session.command("click", "#x1")
    time.sleep(0.6)
    c.post(f"/api/automation/recordings/{run_id}/control", json={"action": "pause"})
    session.command("click", "#x2")  # paused: free browsing
    time.sleep(0.6)
    # changing the position while recording is refused, while paused it is fine
    s = c.post(f"/api/automation/recordings/{run_id}/control", json={"action": "position", "insert_at": 4}).json()
    assert s["where"] == "insert at step 5" and s["state"] == "paused"
    # Resume from the bar inside the browser window; the bar's own click is not recorded
    session.command("click", "#__qa_rec_host button.rec")
    s = wait_state(c, run_id, "recording")
    session.command("click", "#x3")
    time.sleep(0.6)
    session.command("click", "#__qa_rec_host button[data-a=pause]")
    wait_state(c, run_id, "paused")
    bad = c.post(f"/api/automation/recordings/{run_id}/control",
                 json={"action": "record", "insert_at": 1, "replace": 9})
    assert bad.status_code == 422, bad.text
    s = c.post(f"/api/automation/recordings/{run_id}/control",
               json={"action": "record", "insert_at": 1, "replace": 1}).json()
    assert s["where"] == "replace step 2"
    session.command("click", "#x4")
    time.sleep(0.6)
    s = c.get(f"/api/automation/recordings/{run_id}").json()
    assert [(g["at"], g["replace"], ids(g["steps"])) for g in s["segments"]] == [
        (2, 0, ["#x1"]), (4, 0, ["#x3"]), (1, 1, ["#x4"])], s["segments"]

    out = c.post(f"/api/automation/recordings/{run_id}/stop", json={}).json()
    assert ids(out["script"]["steps"]) == ["goto", "#x4", "#x1", "#b2", "#b3", "#x3"], out["script"]["steps"]
    assert out["summary"] == ["inserted 1 step at step 3", "inserted 1 step at step 5",
                              "replaced step 2 with 1 recorded step"]


def test_nothing_recorded_keeps_the_script(client, site):
    c, rt = client
    a = rt.automation
    script = a.scripts.save(Script(id="SCR-0000AAAA21", name="Shop", mode="ai", base_url=site,
                                   steps=[Step(action="goto", value=site), click("b1")]))
    run_id = c.post("/api/automation/recordings", json={"start_url": site, "append_to": script.id,
                                                        "headless": True}).json()["run_id"]
    session = a.recording(run_id)["session"]
    wait_state(c, run_id, "ready")
    for _ in range(50):
        try:
            session.command("click", "#x1", timeout=5)
            break
        except Exception:
            time.sleep(0.2)
    time.sleep(0.5)
    assert c.post(f"/api/automation/recordings/{run_id}/assert", json={"selector": "#x1"}).status_code == 409
    out = c.post(f"/api/automation/recordings/{run_id}/stop", json={}).json()
    assert out["summary"] == [] and ids(out["script"]["steps"]) == ["goto", "#b1"] and out["script"]["mode"] == "ai"


def test_new_script_records_at_once_and_pauses(client, site):
    c, rt = client
    a = rt.automation
    run_id = c.post("/api/automation/recordings", json={"start_url": site, "name": "New", "headless": True}).json()["run_id"]
    session = a.recording(run_id)["session"]
    assert wait_state(c, run_id, "recording")["where"] == "new script"
    for _ in range(50):
        try:
            session.command("click", "#x1", timeout=5)
            break
        except Exception:
            time.sleep(0.2)
    time.sleep(0.5)
    c.post(f"/api/automation/recordings/{run_id}/control", json={"action": "pause"})
    session.command("click", "#x2")
    time.sleep(0.5)
    assert c.post(f"/api/automation/recordings/{run_id}/control",
                  json={"action": "record", "insert_at": 0}).status_code == 422
    c.post(f"/api/automation/recordings/{run_id}/control", json={"action": "record"})
    session.command("click", "#x3")
    time.sleep(0.5)
    out = c.post(f"/api/automation/recordings/{run_id}/stop", json={}).json()
    assert ids(out["script"]["steps"]) == ["goto", "#x1", "#x3"], out["script"]["steps"]
