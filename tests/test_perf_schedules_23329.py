"""23.3.29: performance schedules. Due times in a time zone, one run per due time (also across a restart or a second
copy of the application), overlap and missed-run policies without a backlog, pinned or latest versions, the
maximum runtime, the scheduled PDF and email, and start at Windows sign-in (a file in this user's Startup folder)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from qa_platform import startup
from qa_platform.performance import schedules as S
from tests.test_perf_workload_23326 import _jmeter, _plan, _wait

UTC = timezone.utc


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


# ---- due times (pure) ----------------------------------------------------------------------------------------------

def test_due_times_follow_the_repeat_and_the_time_zone():
    dubai = {"repeat": "daily", "time": "07:30", "start_date": "2026-11-05", "timezone": "Asia/Dubai"}
    assert S.next_after(dubai, at("2026-11-05T00:00:00")) == at("2026-11-05T03:30:00")  # 07:30 in UTC+4
    assert S.next_after(dubai, at("2026-11-05T03:30:00")) == at("2026-11-06T03:30:00")  # strictly after
    once = {"repeat": "once", "time": "09:00", "start_date": "2026-11-07", "timezone": "UTC"}
    assert S.next_after(once, at("2026-11-06T00:00:00")) == at("2026-11-07T09:00:00")
    assert S.next_after(once, at("2026-11-07T09:00:00")) is None
    days = {"repeat": "days", "time": "18:00", "days": [0, 2], "start_date": "2026-11-05", "timezone": "UTC"}
    assert S.next_after(days, at("2026-11-05T12:00:00")) == at("2026-11-09T18:00:00")  # Thu -> next Monday
    assert S.next_after(days, at("2026-11-09T18:00:00")) == at("2026-11-11T18:00:00")  # Monday -> Wednesday
    fortnight = {"repeat": "weekly", "every_weeks": 2, "days": [4], "time": "06:00", "start_date": "2026-11-02",
                 "timezone": "UTC"}
    assert S.next_after(fortnight, at("2026-11-02T00:00:00")) == at("2026-11-06T06:00:00")
    assert S.next_after(fortnight, at("2026-11-06T06:00:00")) == at("2026-11-20T06:00:00")  # every other week
    every = {"repeat": "interval", "interval_minutes": 90, "time": "08:00", "start_date": "2026-11-05",
             "end_date": "2026-11-05", "timezone": "UTC"}
    assert S.next_after(every, at("2026-11-05T08:00:00")) == at("2026-11-05T09:30:00")
    assert S.next_after(every, at("2026-11-05T23:00:00")) is None  # past the end date
    assert S.describe(fortnight) == "Every 2 weeks on Fri at 06:00 (UTC)"
    assert S.describe(every) == "Every 90 minutes from 2026-11-05 08:00 (UTC) until 2026-11-05"
    assert S.describe({**dubai, "timezone": "system"}) == "Every day at 07:30"
    with pytest.raises(Exception):
        S.zone("Mars/Olympus")
    assert "UTC" in S.system_zone_name()


# ---- schedules without JMeter ---------------------------------------------------------------------------------------

@pytest.fixture()
def quiet(client):
    """The scheduler and run queue of the test application, with a fixed clock and no runs executed."""
    rt = client.app.state.runtime
    rt.perf_scheduler._stop.set()  # the 15-second loop no longer checks; the tests call tick()
    rt.performance._closing.set()  # queued runs stay queued
    rt.perf_scheduler.clock = lambda: at("2026-11-05T10:00:00")
    return rt


def _schedule(client, sc, **kw):
    body = {"scenario_id": sc["id"], "repeat": "daily", "time": "12:00", "timezone": "UTC", **kw}
    r = client.post("/api/performance/schedules", json=body)
    assert r.status_code == 201, r.json()
    return r.json()["schedule"]


def test_schedules_are_validated_paused_resumed_and_deleted(client, quiet):
    plan, sc = _plan(client, "1")
    s = _schedule(client, sc, name="Nightly")
    assert s["next_run_at"] == "2026-11-05T12:00:00+00:00" and s["enabled"] and s["when"] == "Every day at 12:00 (UTC)"
    assert s["overlap"] == "skip" and s["missed"] == "skip" and s["version_policy"] == "latest"
    assert s["report"] == {"pdf": True} and s["max_runtime_min"] == 60  # the script sets its own duration
    assert s["scenario_name"] == "Flow" and s["environment_name"] == "Local"
    bad = [({"repeat": "interval", "interval_minutes": 5}, "SCHEDULE_INTERVAL"),
           ({"repeat": "once", "start_date": "2026-11-01"}, "SCHEDULE_PAST"),
           ({"repeat": "days", "days": []}, "SCHEDULE_DAYS"),
           ({"time": "25:00"}, "SCHEDULE_TIME"),
           ({"timezone": "Mars/Olympus"}, "SCHEDULE_TIMEZONE"),
           ({"notify": {"email": {"enabled": True, "to": ""}}}, "SCHEDULE_EMAIL")]
    for extra, code in bad:
        r = client.post("/api/performance/schedules", json={"scenario_id": sc["id"], "repeat": "daily",
                                                             "time": "12:00", **extra})
        assert r.status_code == 422 and r.json()["error_code"] == code, (extra, r.json())
    r = client.post("/api/performance/schedules", json={"scenario_id": sc["id"], "time": "12:00", "overlap": "parallel"})
    assert r.status_code == 422  # one load generator: no parallel load runs
    # a workload of 10 minutes does not fit in a 5-minute maximum runtime
    client.put(f"/api/performance/plans/{plan['id']}/workload",
               json={"shape": "load", "users": 2, "ramp_up_s": 0, "hold_s": 600})
    r = client.post("/api/performance/schedules", json={"scenario_id": sc["id"], "time": "12:00",
                                                         "max_runtime_min": 5})
    assert r.status_code == 422 and r.json()["error_code"] == "SCHEDULE_MAX_RUNTIME"
    assert _schedule(client, sc)["max_runtime_min"] == 20  # default: the planned 10 minutes with a margin

    paused = client.post(f"/api/performance/schedules/{s['id']}/pause").json()["schedule"]
    assert not paused["enabled"] and paused["next_run_at"] is None
    quiet.perf_scheduler.clock = lambda: at("2026-11-05T13:00:00")
    resumed = client.post(f"/api/performance/schedules/{s['id']}/resume").json()["schedule"]
    assert resumed["next_run_at"] == "2026-11-06T12:00:00+00:00"  # times passed while paused are not run
    edited = client.put(f"/api/performance/schedules/{s['id']}", json={"repeat": "days", "days": [0, 4],
                                                                         "time": "06:15"}).json()["schedule"]
    assert edited["next_run_at"] == "2026-11-06T06:15:00+00:00" and edited["name"] == "Nightly"
    listed = client.get("/api/performance/schedules").json()
    assert {x["id"] for x in listed["items"]} >= {s["id"]} and "only while QA Enterprise AI runs" in listed["host"]["explain"]
    assert client.delete(f"/api/performance/schedules/{s['id']}").json()["ok"]
    assert client.get(f"/api/performance/schedules/{s['id']}").status_code == 404


def test_a_due_time_starts_one_run_even_across_two_copies(client, quiet):
    from qa_platform.performance.schedules import PerfScheduler

    plan, sc = _plan(client, "1")
    s = _schedule(client, sc, build="2.0.1")
    sched = quiet.perf_scheduler
    due = at("2026-11-05T12:00:30")
    out = sched.tick(due)
    assert [o["status"] for o in out] == ["QUEUED"]
    run_id = out[0]["run_id"]
    assert sched.tick(due) == []  # the next time is tomorrow now
    other = PerfScheduler(quiet.performance, clock=sched.clock)  # a second copy of the application on the same data
    quiet.db.execute("UPDATE perf_schedules SET next_run_at = ? WHERE id = ?", ("2026-11-05T12:00:00+00:00", s["id"]))
    assert other.tick(due) == []  # the job for 12:00 exists: no second run
    run = client.get(f"/api/performance/runs/{run_id}").json()["run"]
    assert run["trigger"] == "schedule" and run["build"] == "2.0.1" and run["state"] == "QUEUED"
    jobs = client.get(f"/api/performance/schedules/{s['id']}").json()["jobs"]
    assert len(jobs) == 1 and jobs[0]["run_id"] == run_id and jobs[0]["kind"] == "schedule"
    scheduled = client.get("/api/performance/runs?trigger=schedule").json()["items"]
    assert [r["run_id"] for r in scheduled] == [run_id]

    # overlap skip: tomorrow's time while today's run is still waiting
    nxt = sched.tick(at("2026-11-06T12:00:10"))
    assert nxt[0]["status"] == "SKIPPED" and "overlap policy: skip" in nxt[0]["reason"] and run_id in nxt[0]["reason"]
    # overlap queue: at most one waiting run of the schedule, never a backlog
    client.put(f"/api/performance/schedules/{s['id']}", json={"overlap": "queue"})
    later = sched.tick(at("2026-11-07T12:00:10"))
    assert later[0]["status"] == "SKIPPED" and "already waiting" in later[0]["reason"]
    client.post(f"/api/performance/runs/{run_id}/cancel")
    manual = quiet.performance.run_scenario(sc["id"])  # someone else's run
    queued = sched.tick(at("2026-11-08T12:00:10"))
    assert queued[0]["status"] == "QUEUED"  # queued behind the manual run
    ids = [r["run_id"] for r in client.get("/api/performance/queue").json()["items"]]
    assert ids.index(manual) < ids.index(queued[0]["run_id"])
    # Run now (the schedule's options, its next time unchanged); the API refuses a second while one is waiting
    assert client.post(f"/api/performance/schedules/{s['id']}/run").status_code == 409


def test_missed_times_are_recorded_and_run_once_at_most(client, quiet):
    plan, sc = _plan(client, "1")
    skip = _schedule(client, sc, name="Skip")
    once = _schedule(client, sc, name="Once", missed="run_once", overlap="queue")
    out = quiet.perf_scheduler.tick(at("2026-11-08T15:00:00"))  # closed for three days: 5th, 6th, 7th, 8th at 12:00
    assert [(o["status"], o.get("run_id") is not None) for o in out] == [("QUEUED", True)]
    j_skip = client.get(f"/api/performance/schedules/{skip['id']}").json()["jobs"]
    assert [j["status"] for j in j_skip] == ["MISSED"] * 4 and "No run took place" in j_skip[0]["reason"]
    j_once = client.get(f"/api/performance/schedules/{once['id']}").json()["jobs"]
    assert sorted(j["status"] for j in j_once) == ["MISSED", "MISSED", "MISSED", "QUEUED"]
    caught = next(j for j in j_once if j["status"] == "QUEUED")
    assert caught["kind"] == "catch_up" and caught["due_at"] == "2026-11-08T12:00:00+00:00"  # the latest only
    assert client.get(f"/api/performance/schedules/{skip['id']}").json()["schedule"]["next_run_at"] == \
        "2026-11-09T12:00:00+00:00"
    # every 15 minutes for ten days: 20 missed times recorded one by one, the rest counted, no run
    for x in (skip, once):
        client.post(f"/api/performance/schedules/{x['id']}/pause")
    many = _schedule(client, sc, repeat="interval", interval_minutes=15, time="00:00", start_date="2026-11-09")
    assert quiet.perf_scheduler.tick(at("2026-11-19T00:10:00")) == []
    jobs = client.get(f"/api/performance/schedules/{many['id']}").json()["jobs"]
    assert len(jobs) == S.MISSED_KEEP + 1 and any("more missed time(s)" in (j["reason"] or "") for j in jobs)
    notes = [n["title"] for n in quiet.notifications.list(50)]
    assert "Missed performance schedule: Skip" in notes


def test_a_restart_applies_the_missed_policy_to_runs_left_queued(client, quiet):
    plan, sc = _plan(client, "1")
    s = _schedule(client, sc)
    run_id = quiet.perf_scheduler.tick(at("2026-11-05T12:00:05"))[0]["run_id"]
    quiet.perf_scheduler.clock = lambda: at("2026-11-05T18:00:00")  # the application was closed meanwhile
    assert quiet.perf_scheduler.recover() == {"claimed": 0, "cancelled": 1}
    run = client.get(f"/api/performance/runs/{run_id}").json()["run"]
    assert run["state"] == "CANCELLED" and "missed-run policy: skip" in run["state_reason"]
    job = client.get(f"/api/performance/schedules/{s['id']}").json()["jobs"][0]
    assert job["status"] == "MISSED"
    # a job claimed when the application stopped, before its run was created
    quiet.db.execute("INSERT INTO perf_schedule_jobs (schedule_id, job_key, due_at, kind, status, created_at,"
                     " updated_at) VALUES (?, 'k', NULL, 'schedule', 'CLAIMED', 'x', 'x')", (s["id"],))
    assert quiet.perf_scheduler.recover()["claimed"] == 1


def test_pinned_versions_run_as_pinned_and_changed_data_is_not_run_silently(client, quiet):
    plan, sc = _plan(client, "1")
    client.put(f"/api/performance/plans/{plan['id']}/workload", json={"shape": "load", "users": 2, "ramp_up_s": 0,
                                                                      "hold_s": 30})
    s = _schedule(client, sc, version_policy="pinned")
    pinned = s["pinned"]
    assert pinned["script_version"] == 1 and pinned["workload"]["hold_s"] == 30 and pinned["datasets"] == {}
    # the script and the workload change after pinning
    view = client.get(f"/api/performance/plans/{plan['id']}/script").json()
    client.put(f"/api/performance/plans/{plan['id']}/script",
               json={"script": view["script"].replace("Shoppers", "Buyers"), "base_version": 1})
    client.put(f"/api/performance/plans/{plan['id']}/workload", json={"shape": "load", "users": 3, "ramp_up_s": 0,
                                                                      "hold_s": 40})
    run_id = quiet.perf_scheduler.tick(at("2026-11-05T12:00:05"))[0]["run_id"]
    cfg = json.loads(quiet.db.query_one("SELECT config FROM perf_runs WHERE run_id = ?", (run_id,))["config"])
    assert cfg["plan"]["script_version"] == 1 and "Shoppers" in cfg["plan"]["script"]
    assert cfg["plan"]["workload"]["hold_s"] == 30 and cfg["schedule"]["version_policy"] == "pinned"
    assert "script" not in cfg["overrides"]  # the script text is kept once, in the plan
    # latest: the newest saved version and workload
    client.put(f"/api/performance/schedules/{s['id']}", json={"version_policy": "latest"})
    client.post(f"/api/performance/runs/{run_id}/cancel")
    latest = quiet.perf_scheduler.tick(at("2026-11-06T12:00:05"))[0]["run_id"]
    cfg = json.loads(quiet.db.query_one("SELECT config FROM perf_runs WHERE run_id = ?", (latest,))["config"])
    assert cfg["plan"]["script_version"] == 2 and cfg["plan"]["workload"]["hold_s"] == 40
    client.post(f"/api/performance/runs/{latest}/cancel")
    # a pinned data set that changed (here: deleted) is not run; the job says why
    spec = json.loads(quiet.db.query_one("SELECT spec FROM perf_schedules WHERE id = ?", (s["id"],))["spec"])
    spec.update(version_policy="pinned", pinned={**pinned, "datasets": {"DS-GONE": 3}})
    quiet.db.execute("UPDATE perf_schedules SET spec = ? WHERE id = ?", (json.dumps(spec), s["id"]))
    out = quiet.perf_scheduler.tick(at("2026-11-07T12:00:05"))
    assert out[0]["status"] == "ERROR" and "DS-GONE was deleted" in out[0]["reason"]
    job = client.get(f"/api/performance/schedules/{s['id']}").json()["jobs"][0]
    assert job["status"] == "SKIPPED" and not job["run_id"]


def test_start_at_windows_sign_in_is_a_file_in_the_startup_folder(client, tmp_path, monkeypatch):
    folder = tmp_path / "Startup"
    monkeypatch.setenv("QA_STARTUP_DIR", str(folder))
    st = client.get("/api/performance/startup").json()["startup"]
    assert st["supported"] and not st["enabled"] and st["launcher_exists"]
    on = client.put("/api/performance/startup", json={"enabled": True}).json()["startup"]
    assert on["enabled"]
    data = (folder / startup.FILE_NAME).read_bytes()
    assert b"\r\n" in data and b"QA_NO_BROWSER=1" in data and startup.MARKER.encode() in data
    assert b"START_QA_PLATFORM.cmd" in data and b"/min" in data
    assert not client.put("/api/performance/startup", json={"enabled": False}).json()["startup"]["enabled"]
    assert not (folder / startup.FILE_NAME).exists()
    (folder / startup.FILE_NAME).write_text("@echo off\r\nrem someone else's file\r\n")
    r = client.put("/api/performance/startup", json={"enabled": True})
    assert r.status_code == 409 and r.json()["error_code"] == "STARTUP_FILE_EXISTS"
    client.put("/api/performance/startup", json={"enabled": False})
    assert (folder / startup.FILE_NAME).exists()  # only our own file is ever removed
    launcher = Path(startup.launcher()).read_bytes()
    assert b"if not defined QA_NO_BROWSER" in launcher


# ---- real JMeter runs ---------------------------------------------------------------------------------------------

def test_a_scheduled_run_with_its_pdf_email_and_maximum_runtime(client, site):
    rt = client.app.state.runtime
    _jmeter(rt)
    rt.perf_scheduler._stop.set()  # the tests call tick() with the time they need
    port = site.rsplit(":", 1)[1]
    plan, sc = _plan(client, port)
    view = client.get(f"/api/performance/plans/{plan['id']}/script").json()
    work = client.post("/api/performance/script/apply", json={"script": view["script"], "ops": [
        {"op": "add", "parent": "0/0", "type": "constant_timer", "values": {"delay_ms": 300}}]}).json()
    client.put(f"/api/performance/plans/{plan['id']}/script", json={"script": work["script"], "base_version": 1})
    client.put(f"/api/performance/plans/{plan['id']}/workload", json={"shape": "load", "users": 2, "ramp_up_s": 0,
                                                                      "hold_s": 10})
    sent = []
    rt.mailer.transport = lambda cfg, msg: sent.append(msg)
    client.put("/api/email/settings", json={"mode": "smtp", "preset": "office365", "username": "qa@contoso.com",
                                           "sender": "qa@contoso.com", "secret": "app-pass"})
    now = datetime.now(UTC).replace(microsecond=0)
    s = _schedule(client, sc, name="Nightly load", repeat="once", start_date=now.date().isoformat(),
                  time=(now + timedelta(minutes=2)).strftime("%H:%M"), version_policy="pinned",
                  notify={"email": {"enabled": True, "to": "lead@contoso.com", "on": "always"}})
    if s["next_run_at"] is None:
        pytest.skip("too close to midnight UTC")
    due = S._utc(s["next_run_at"])
    out = rt.perf_scheduler.tick(due + timedelta(seconds=5))
    run_id = out[0]["run_id"]
    run = _wait(client, run_id)
    assert run["state"] == "COMPLETED" and run["trigger"] == "schedule", run.get("error")
    deadline = time.time() + 120
    while time.time() < deadline and not sent:
        time.sleep(0.5)
    assert sent, "no email after the scheduled run"
    msg = sent[-1]
    assert msg["Subject"].startswith("[COMPLETED] Nightly load") and run_id in msg["Subject"]
    assert any(p.get_filename() == "qa-report.pdf" for p in msg.iter_attachments())
    assert (Path(run["folder"]) / "qa-report.pdf").is_file()
    job = client.get(f"/api/performance/schedules/{s['id']}").json()["jobs"][0]
    assert job["status"] == "COMPLETED" and job["run_id"] == run_id
    after = client.get(f"/api/performance/schedules/{s['id']}").json()["schedule"]
    assert after["next_run_at"] is None and after["finished"] and after["last"]["verdict"] in ("PASS", "NOT_SPECIFIED")

    # the maximum runtime: a run still going past it is stopped and its results are kept
    client.put(f"/api/performance/plans/{plan['id']}/workload", json={"shape": "load", "users": 2, "ramp_up_s": 0,
                                                                      "hold_s": 120})
    long = _schedule(client, sc, name="Long", max_runtime_min=3)
    rid = rt.perf_scheduler.run_now(long["id"])["run_id"]
    deadline = time.time() + 60
    while time.time() < deadline:
        r = client.get(f"/api/performance/runs/{rid}/live").json()
        if r["run"]["state"] == "RUNNING" and (r.get("dashboard") or {}).get("samples", 0) > 0:
            break
        time.sleep(0.5)
    rt.perf_scheduler._watchdog(datetime.now(UTC) + timedelta(minutes=4))
    run = _wait(client, rid)
    assert run["state"] == "CANCELLED" and "maximum runtime of 3 min" in run["state_reason"]
    assert run["partial"] and run["metrics"]["overall"]["samples"] > 0
