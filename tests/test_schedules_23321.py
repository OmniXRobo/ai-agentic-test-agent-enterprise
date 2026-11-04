"""23.3.21: Scheduled Runs, parallel runs, several browsers, TFS test run outcomes, Outlook email reports."""
import functools
import json
import os
import socket
import sys
import threading
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

APP = Path(os.environ.get("APP_DIR", Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(APP))

from qa_platform.automation.dsl import Script, Step, Target  # noqa: E402
from qa_platform.automation.scheduler import describe, next_run  # noqa: E402

PAGE = """<!doctype html><html><body><h1>Shop</h1><button id="ok">OK</button>
<script>let t0 = Date.now();</script></body></html>"""


def test_next_run_rules():
    now = datetime(2026, 11, 4, 8, 0)  # a Wednesday
    assert next_run({"repeat": "once", "time": "09:30", "start_date": "2026-11-04"}, now) == datetime(2026, 11, 4, 9, 30)
    assert next_run({"repeat": "once", "time": "07:30", "start_date": "2026-11-04"}, now) is None
    assert next_run({"repeat": "daily", "time": "07:30", "start_date": "2026-11-01"}, now) == datetime(2026, 11, 5, 7, 30)
    assert next_run({"repeat": "weekdays", "time": "07:30"}, datetime(2026, 11, 6, 8, 0)) == datetime(2026, 11, 9, 7, 30)
    assert next_run({"repeat": "weekly", "days": [0, 3], "time": "18:00"}, now) == datetime(2026, 11, 5, 18, 0)
    assert next_run({"repeat": "daily", "time": "07:30", "end_date": "2026-11-04"}, now) is None
    assert next_run({"repeat": "hourly", "every_hours": 2, "time": "07:00", "start_date": "2026-11-04"}, now) == datetime(2026, 11, 4, 9, 0)
    assert describe({"repeat": "daily", "time": "07:30"}) == "Every day at 07:30 (continues)"


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


class FakeTfsHttp:
    def __init__(self):
        self.calls = []
        self.runs = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        if path.endswith("/_apis/test/points"):
            tc = body["pointsFilter"]["testcaseIds"][0]
            pts = [{"id": 9001, "testPlan": {"id": 77}, "suite": {"id": 78}, "testCase": {"id": str(tc)}}] if tc == 4242 else []
            return httpx.Response(200, json={"points": pts})
        if path.endswith("/_apis/test/runs") and request.method == "POST":
            rid = 600 + len(self.runs)
            self.runs[rid] = body
            return httpx.Response(200, json={"id": rid})
        if "/_apis/test/runs/" in path and path.endswith("/results") and request.method == "GET":
            return httpx.Response(200, json={"value": [{"id": 100000, "testPoint": {"id": "9001"}}]})
        return httpx.Response(200, json={"value": []})


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("QA_HOME", str(tmp_path))
    from fastapi.testclient import TestClient
    from qa_platform.credentials import MemoryBackend
    from qa_platform.main import create_app
    fake = FakeTfsHttp()
    app = create_app(secret_backend=MemoryBackend(), tfs_transport=httpx.MockTransport(fake))
    with TestClient(app) as c:
        rt = app.state.runtime
        rt.tfs.save_profile(rt.tfs.active_mode(), url="http://tfs.test/tfs/DefaultCollection", project="Shop", pat="x")
        yield c, rt, fake


def wait_batch(c, batch_id, timeout=120):
    for _ in range(int(timeout / 0.5)):
        items = c.get("/api/schedules/history").json()["items"]
        b = next((x for x in items if x["batch_id"] == batch_id), None)
        if b and b["status"] != "RUNNING":
            return b
        time.sleep(0.5)
    raise AssertionError("batch did not finish")


def test_schedule_page_flow_parallel_email_and_tfs(env, site):
    c, rt, fake = env
    ok = rt.automation.scripts.save(Script(id="SCR-00SCHED001", name="Open shop", mode="guided", base_url=site, test_case_id="4242",
                                           steps=[Step(action="goto", value=site),
                                                  Step(action="click", target=Target(selector="#ok"))]))
    bad = rt.automation.scripts.save(Script(id="SCR-00SCHED002", name="Broken", mode="hybrid", base_url=site, test_case_id="5151",
                                            steps=[Step(action="goto", value=site),
                                                   Step(action="click", target=Target(selector="#missing"), timeout_ms=1500)]))
    scripts = c.get("/api/schedules/scripts").json()["items"]
    assert {s["id"]: s["page"] for s in scripts} == {ok.id: "Guided Browser Automation", bad.id: "Hybrid Test Automation"}

    # Outlook email settings (SMTP, password kept in the vault)
    r = c.put("/api/email/settings", json={"mode": "smtp", "preset": "office365", "username": "qa@contoso.com",
                                          "sender": "qa@contoso.com", "secret": "app-pass"})
    cfg = r.json()["email"]
    assert cfg["host"] == "smtp.office365.com" and cfg["port"] == 587 and cfg["has_password"] and cfg["configured"]
    assert "app-pass" not in json.dumps(rt.settings.get())
    sent = []
    rt.mailer.transport = lambda cfg, msg: sent.append(msg)
    assert c.post("/api/email/test", json={"to": "lead@contoso.com"}).json()["sent"]
    assert sent[-1]["To"] == "lead@contoso.com"

    # a past time is refused; a daily schedule continues
    r = c.post("/api/schedules", json={"script_ids": [ok.id], "repeat": "once", "time": "00:00", "start_date": "2020-01-01"})
    assert r.status_code == 422
    r = c.post("/api/schedules", json={"name": "Nightly", "script_ids": [ok.id, bad.id], "repeat": "daily", "time": "23:59",
                                       "browsers": ["chromium"], "parallel": 2, "headless": True, "publish_tfs": True,
                                       "email": {"enabled": True, "to": "lead@contoso.com; qa@contoso.com", "on": "always"}})
    assert r.status_code == 201, r.text
    sch = r.json()["schedule"]
    assert sch["when"] == "Every day at 23:59 (continues)" and sch["next_run_at"].endswith("23:59:00")
    assert sch["script_names"] == ["Open shop", "Broken"]

    t0 = time.monotonic()
    batch_id = c.post(f"/api/schedules/{sch['id']}/run").json()["batch_id"]
    b = wait_batch(c, batch_id)
    assert {i["script_id"]: i["status"] for i in b["items"]} == {ok.id: "PASSED", bad.id: "FAILED"}, b["items"]
    assert b["status"] == "FAILED" and b["counts"] == {"PASSED": 1, "FAILED": 1}
    # TFS: test case 4242 is in plan 77 -> a plan run on point 9001 marked Passed; 5151 in no plan -> an automated run
    posted = [x for x in fake.calls if x[0] == "POST" and x[1].endswith("/_apis/test/runs")]
    assert posted[0][2]["plan"] == {"id": 77} and posted[0][2]["pointIds"] == [9001]
    patched = [x for x in fake.calls if x[0] == "PATCH" and x[1].endswith("/results")]
    assert patched[0][2][0]["outcome"] == "Passed"
    added = [x for x in fake.calls if x[0] == "POST" and x[1].endswith("/results")]
    assert added[0][2][0]["testCase"] == {"id": "5151"} and added[0][2][0]["outcome"] == "Failed"
    assert b["tfs"]["ok"] and len(b["tfs"]["runs"]) == 2
    # the email report
    assert b["email"]["ok"], b["email"]
    msg = sent[-1]
    assert msg["Subject"].startswith("[FAILED] Nightly: 1/2 passed")
    assert msg["To"] == "lead@contoso.com, qa@contoso.com"
    html = msg.get_body(("html",)).get_content()
    assert "Open shop" in html and "Broken" in html and "#missing" in html
    assert any(p.get_filename() and p.get_filename().endswith(".html") for p in msg.iter_attachments())
    got = c.get("/api/schedules").json()["items"][0]
    assert got["last_status"] == "FAILED" and got["last_batch"] == batch_id
    assert time.monotonic() - t0 < 120

    # Run now: one script in two browsers at the same time (both chromium-based here)
    r = c.post("/api/schedule-runs", json={"script_ids": [ok.id], "browsers": ["chromium", "msedge"], "parallel": 2,
                                           "headless": True})
    assert r.status_code == 202
    b = wait_batch(c, r.json()["batch_id"])
    assert [(i["browser"], i["status"] in ("PASSED", "ERROR", "FAILED")) for i in b["items"]] == [("chromium", True), ("msedge", True)]
    assert b["items"][0]["status"] == "PASSED"

    # pause (disable), edit and delete
    r = c.put(f"/api/schedules/{sch['id']}", json={"enabled": False})
    assert r.json()["schedule"]["enabled"] is False
    r = c.put(f"/api/schedules/{sch['id']}", json={"enabled": True, "repeat": "weekly", "days": [1, 3], "time": "06:15"})
    assert r.json()["schedule"]["when"] == "Every Tue, Thu at 06:15 (continues)"
    assert c.delete(f"/api/schedules/{sch['id']}").json()["ok"]
    assert c.get("/api/schedules").json()["items"] == []


def test_tick_runs_a_due_schedule_and_missed_runs_catch_up(env, site):
    c, rt, _ = env
    s = rt.automation.scripts.save(Script(id="SCR-00SCHED003", name="Tick", mode="guided", base_url=site,
                                          steps=[Step(action="goto", value=site)]))
    sch = c.post("/api/schedules", json={"script_ids": [s.id], "repeat": "daily", "time": "23:59", "headless": True}).json()["schedule"]
    real = rt.scheduler.clock
    rt.scheduler.clock = lambda: datetime.fromisoformat(sch["next_run_at"])  # it is 23:59 now
    started = rt.scheduler.tick()
    assert len(started) == 1
    b = wait_batch(c, started[0])
    assert b["status"] == "PASSED" and b["trigger"] == "schedule"
    nxt = c.get(f"/api/schedules/{sch['id']}").json()["schedule"]["next_run_at"]
    assert nxt > sch["next_run_at"]
    # the application was closed at the next time: when it starts again the missed run starts once
    from datetime import timedelta
    rt.scheduler.clock = lambda: datetime.fromisoformat(nxt) + timedelta(hours=3)
    rt.scheduler._catch_up()
    hist = c.get("/api/schedules/history").json()["items"]
    missed = next(h for h in hist if h["trigger"] == "missed")
    wait_batch(c, missed["batch_id"])
    rt.scheduler.clock = real


def test_parallel_runs_setting(env):
    c, rt, _ = env
    assert c.get("/api/schedules").json()["parallel_runs"] == 1
    assert c.put("/api/schedules-settings", json={"parallel_runs": 3}).json()["parallel_runs"] == 3
