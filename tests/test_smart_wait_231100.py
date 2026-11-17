"""23.11.0: Smart Wait on a site with real delayed AJAX (dependent dropdown, table refresh with a processing box):
the runner and the exported pytest file use the same wait service and policy; bulk insert/remove/update with
duplicate prevention; a timeout is reported, not hidden; a failure after an uncertain wait is classified as timing."""

from __future__ import annotations

import os
import time
import subprocess
import sys

import pytest

from qa_platform.automation import pytest_export, smart_wait
from qa_platform.automation.dsl import Script, Step, Target
from tests import ajax_site
from tests.test_automation import HAVE_PLAYWRIGHT

needs_browser = pytest.mark.skipif(not HAVE_PLAYWRIGHT, reason="playwright is not installed")


@pytest.fixture(scope="module")
def ajax():
    server, url = ajax_site.start()
    yield url
    server.shutdown()


def _script(base, **over):
    steps = [
        Step(action="goto", value=base + "/?delay=1200"),
        Step(action="select", target=Target(selector="#country"), value="UAE"),
        Step(action="select", target=Target(selector="#city"), value="Dubai", timeout_ms=600),
        Step(action="click", target=Target(selector="#apply", description="Apply filter")),
        Step(action="expect_text", target=Target(selector="#orders"), value="Dubai", timeout_ms=600),
    ]
    return Script(id=over.pop("id", "SCR-WAIT000001"), name="Orders by city", base_url=base, steps=steps, **over)


def _run(rt, script):
    rt.automation.scripts.save(script)
    return rt.automation.run_script(script.id, headless=True, background=False)


@pytest.fixture()
def wrt(rt):
    rt.settings.update({"playback": {"visual_delay_ms": 0, "headless": True, "step_screenshots": False}})
    return rt


@needs_browser
def test_without_waits_the_ajax_steps_fail(wrt, ajax):
    run = _run(wrt, _script(ajax))
    assert run["status"] == "FAILED" and run["failed_step"] == 4  # the table had not refreshed yet


@needs_browser
def test_global_policy_waits_for_ajax_dropdown_and_table(wrt, ajax):
    script = _script(ajax, wait_policy={"enabled": True, "mode": "smart", "timeout_ms": 15000, "scope": "all"})
    run = _run(wrt, script)
    assert run["status"] == "PASSED", run["error"]
    after_country = run["steps"][1]["auto_wait"]
    assert after_country["ok"] and after_country["waited_ms"] >= 1000  # waited for the cities request
    after_apply = run["steps"][3]["auto_wait"]
    assert after_apply["ok"] and after_apply["waited_ms"] >= 1000  # request + processing box


@needs_browser
def test_bulk_insert_remove_update_without_duplicates(client, wrt, ajax):
    c = client
    wrt.automation.scripts.save(_script(ajax))
    sid = "SCR-WAIT000001"
    r = c.post(f"/api/automation/scripts/{sid}/waits", json={"op": "insert", "where": "dropdowns",
                                                            "wait": {"kind": "ajax", "timeout_ms": 15000}}).json()
    assert r["added"] == 2 and [s["action"] for s in r["script"]["steps"]][:5] == [
        "goto", "select", "wait_until", "select", "wait_until"]
    r = c.post(f"/api/automation/scripts/{sid}/waits", json={"op": "insert", "where": "all",
                                                            "wait": {"kind": "ajax"}}).json()
    assert r["skipped_existing"] == 2 and r["added"] == 3  # no second wait after the dropdowns
    # a table refresh wait after Apply filter: the prior table is captured before the click
    steps = r["script"]["steps"]
    i = next(n for n, s in enumerate(steps) if s["action"] == "click") + 1
    r = c.post(f"/api/automation/scripts/{sid}/waits", json={"op": "update", "where": "selected", "steps": [i],
                                                            "wait": {"kind": "table_refresh", "selector": "#orders"}})
    assert r.json()["updated"] == 1
    run = wrt.automation.run_script(sid, headless=True, background=False)
    assert run["status"] == "PASSED", run["error"]
    table_wait = run["steps"][i]["wait"]
    assert table_wait["kind"] == "table_refresh" and table_wait["ok"] and table_wait["waited_ms"] >= 1000
    # exported pytest file: same waits, same result outside SkyGuard
    src = c.get(f"/api/automation/scripts/{sid}/export?format=pytest").text
    assert "class SmartWait" in src and "sw.capture(page, {'kind': 'table_refresh'" in src
    r = c.post(f"/api/automation/scripts/{sid}/waits", json={"op": "remove", "where": "all"}).json()
    assert r["removed"] == 5 and not any(s["action"] == "wait_until" for s in r["script"]["steps"])
    bad = c.post(f"/api/automation/scripts/{sid}/waits", json={"op": "insert", "wait": {"kind": "teleport"}})
    assert bad.status_code == 400 and bad.json()["error_code"] == "WAIT_KIND_INVALID"


@needs_browser
def test_exported_pytest_waits_the_same_way(wrt, ajax, tmp_path):
    script = _script(ajax, wait_policy={"enabled": True, "mode": "smart_min", "timeout_ms": 15000, "minimum_ms": 200})
    path = tmp_path / pytest_export.file_name(script)
    path.write_text(pytest_export.to_pytest(script), encoding="utf-8")
    env = {**os.environ, "HEADLESS": "1"}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(path)], capture_output=True,
                       text=True, timeout=300, env=env, cwd=tmp_path)
    assert r.returncode == 0 and "1 passed" in r.stdout, r.stdout + r.stderr
    # and without the policy the exported test fails the same way the runner does
    plain = _script(ajax, id="SCR-WAIT000002")
    path2 = tmp_path / "test_plain.py"
    path2.write_text(pytest_export.to_pytest(plain), encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(path2)], capture_output=True,
                       text=True, timeout=300, env=env, cwd=tmp_path)
    assert r.returncode == 1


@needs_browser
def test_timeout_is_reported_and_next_failure_is_classified_timing(wrt, ajax):
    script = _script(ajax)
    script.steps.insert(1, Step(action="wait_until", wait={"kind": "element_visible", "selector": "#never",
                                                          "timeout_ms": 1000}))
    run = _run(wrt, script)
    w = run["steps"][1]
    assert w["status"] == "PASSED" and w["wait_uncertain"] and "Smart Wait timed out" in w["warning"]
    assert not w["wait"]["ok"] and any("#never" in x for x in w["wait"]["reasons"])
    assert run["status"] == "FAILED"
    assert wrt.defects.wait_for_automatic(run["run_id"], 15)
    row = wrt.db.query_one("SELECT id FROM defect_drafts WHERE source_run_id = ?", (run["run_id"],))
    d = wrt.defects.get(row["id"])
    assert d["classification"] == "timing" and d["facts"]["page_busy"], d["facts"]
    assert d["analysis"]["recommendation"] == "smart_wait"


def test_policy_and_spec_validation():
    assert smart_wait.policy_applies({"enabled": True}, 0, "click")
    assert not smart_wait.policy_applies({"enabled": True}, 0, "expect_text")
    assert not smart_wait.policy_applies({"enabled": True, "include_navigation": False}, 0, "goto")
    assert smart_wait.policy_applies({"enabled": True, "scope": "selected", "steps": [2]}, 2, "click")
    assert not smart_wait.policy_applies({"enabled": True, "scope": "selected", "steps": [2]}, 1, "click")
    assert smart_wait.clean_policy({"enabled": True})["timeout_ms"] == 30000  # default 30 s
    with pytest.raises(Exception):
        smart_wait.clean_spec({"kind": "element_visible"})  # needs a selector
    assert "SMART WAIT BEGIN" in smart_wait.embedded_source()


@needs_browser
def test_recorder_records_a_smart_wait_after_actions_that_load_data(wrt, ajax):
    """The recorder uses the same Smart Wait service: an action after which the page started AJAX requests gets an
    AJAX/Data Load wait after it, so the recording plays back on a slow day without a global policy."""
    auto = wrt.automation
    run_id = auto.start_recording(f"{ajax}/?delay=1200", name="Recorded orders", headless=True)
    session = auto.recording(run_id)["session"]
    session.command("select", ("#country", "UAE"))
    time.sleep(3)  # the tester waits for the cities, as a person would
    session.command("select", ("#city", "Dubai"))
    time.sleep(3)
    session.command("click", "#apply")
    time.sleep(3.5)
    session.add_assertion("#orders", "Dubai")
    script = auto.stop_recording(run_id)
    acts = [s.action for s in script.steps]
    assert acts == ["goto", "select", "wait_until", "select", "click", "wait_until", "expect_text"], acts
    waits = [s for s in script.steps if s.action == "wait_until"]
    assert all(w.wait["kind"] == "ajax" and w.wait.get("auto") for w in waits)
    assert "seen while recording" in waits[0].description
    for st in script.steps:  # the recorded timeouts are short, so only the recorded waits make playback pass
        if st.action in ("select", "expect_text"):
            st.timeout_ms = 600
    wrt.automation.scripts.save(script)
    run = wrt.automation.run_script(script.id, headless=True, background=False)
    assert run["status"] == "PASSED", run.get("error")


@needs_browser
def test_strict_wait_fails_the_step_when_it_times_out(client, wrt, ajax):
    """A wait marked strict (Smart Wait dialog: "Fail the step when the wait times out") stops the run at the wait."""
    assert smart_wait.clean_spec({"kind": "element_visible", "selector": "#x", "strict": True})["strict"] is True
    script = _script(ajax, id="SCR-WAITSTRICT")
    script.steps.insert(1, Step(action="wait_until", wait={"kind": "element_visible", "selector": "#never",
                                                          "timeout_ms": 1000, "strict": True}))
    run = _run(wrt, script)
    assert run["status"] == "FAILED" and run["failed_step"] == 1 and "Smart Wait timed out" in run["error"]
