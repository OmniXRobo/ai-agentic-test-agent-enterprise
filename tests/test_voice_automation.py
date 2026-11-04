"""Milestone 6: AI-Driven Automation by voice, against the simulated TFS, design engines and Script Studio."""

import os
import sys
from pathlib import Path

import pytest

APP = Path(os.environ.get("APP_DIR", Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(APP))
sys.path.insert(0, str(Path(__file__).parent))

from test_voice_plans import Talk  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("QA_HOME", str(tmp_path))
    from fastapi.testclient import TestClient
    from qa_platform.main import create_app
    import fake_tfs
    app = create_app()
    with TestClient(app) as c:
        rt = app.state.runtime
        fake = fake_tfs.install(rt)
        fake_tfs.install_design(rt, fake)
        studio = fake_tfs.install_studio(rt, tmp_path)
        c.put("/api/voice/settings", json={"agent_command_name": "Captain", "phrase_style": "conversational", "address_as": "Boss"})
        yield c, fake, studio, rt


def saved_cases(t: Talk):
    t.final(t.say("create a new test plan for sprint 25"))
    t.final(t.say("select functional and regression"))
    t.final(t.say("generate test cases for sprint 25"))
    t.say("save the test cases")
    t.final(t.say("confirm"))


def test_ac14_plan_python_playwright_generate(env):
    c, fake, studio, rt = env
    t = Talk(c)
    saved_cases(t)
    t.say("show me more details")  # the plan is the focus again, not the saved cases
    r = t.say("Captain, select the current test plan, choose Python with Playwright, and generate the automation script.")
    assert r["status"] == "accepted"
    assert r["speak"].startswith("On it, Boss. I'm checking the selected test cases and automation parameters. I'm "
                                 "generating Python Playwright scripts for 5 test cases in Functional Testing and "
                                 "Regression Testing of Shop - Sprint 25 - Test Plan, for the application at https://shop.test/")
    status, line = t.final(r)
    assert status == "completed"
    assert line.startswith("5 Python Playwright scripts generated for 5 test cases")
    assert "passed the syntax and static checks before it was saved" in line and "Nothing has been run yet" in line
    action, req = studio.requests[-1]
    assert action == "generate" and req.base_url == "https://shop.test/"
    assert sorted(req.selections[0].test_case_ids) == sorted(t.say("show me more details")["context"]["saved_case_ids"])


def test_generate_for_these_and_for_one_suite(env):
    c, fake, studio, rt = env
    t = Talk(c)
    saved_cases(t)
    r = t.say("generate automation scripts for these test cases")
    assert "the 5 test cases you just saved" in r["say"]
    status, line = t.final(r)
    assert status == "completed"
    r = t.say("generate scripts for the regression suite")
    status, line = t.final(r)
    # every case of the regression suite already has a script: nothing regenerated behind the user's back
    assert status == "completed" and "0 Python Playwright scripts generated" in line and "already existed" in line


def test_one_generated_script_opens_its_code_and_failures_are_reported(env):
    c, fake, studio, rt = env
    t = Talk(c)
    studio.fail_generation = {107}
    t.say("explain test case 106")  # the current test case
    r = t.say("generate automation for test case 106")
    status, line = t.final(r)
    assert status == "completed" and line.startswith("1 Python Playwright script generated for test case 106.")
    assert "The code is open." in line
    assert t.say("show me more details")["context"]["script_id"] == "FWS-106"  # "this script" from now on
    r = t.say("generate automation for test case 107")
    status, line = t.final(r)
    assert status == "failed" and "The element inventory has no field for step 2" in line  # the real reason


def test_settings_change_and_read_back(env, monkeypatch):
    c, fake, studio, rt = env
    import qa_platform.voice.commands_automation as ca
    monkeypatch.setattr(ca, "browser_available", lambda name: (name != "firefox", "the Playwright Firefox browser is "
                                                                                  "not installed with this application"))
    t = Talk(c)
    r = t.say("Captain, change the browser to Chrome")
    assert r["status"] == "completed" and r["say"] == "Saved. Automation runs will now run in Google Chrome."
    assert rt.settings.section("playback")["browser"] == "chrome"
    r = t.say("set timeout to 30 seconds")
    assert r["say"] == "Saved. Automation runs will now wait up to 30 seconds for an element."
    r = t.say("set the timeout to 500 seconds")
    assert r["status"] == "unclear" and rt.settings.section("playback")["timeout_seconds"] == 30
    r = t.say("enable screenshots on failure")
    assert "already on" in r["say"]
    r = t.say("use firefox")
    assert r["status"] == "failed" and "not installed" in r["say"] and rt.settings.section("playback")["browser"] == "chrome"
    r = t.say("set retries to 2")
    assert r["status"] == "failed" and "no retry setting" in r["say"]
    r = t.say("generate selenium java scripts")
    assert r["status"] == "failed" and "Python with Playwright scripts only" in r["say"]
    r = t.say("what are the automation settings")
    assert "Runs use Google Chrome" in r["say"] and "30 seconds" in r["say"]
    # the same settings are what the Settings page reads and saves
    assert c.get("/api/settings").json()["settings"]["playback"]["timeout_seconds"] == 30
    assert c.patch("/api/settings", json={"playback": {"timeout_seconds": 0}}).status_code == 400


def test_validate_run_failure_and_regenerate(env, monkeypatch):
    c, fake, studio, rt = env
    from qa_platform.automation.framework import validator
    calls = []

    def fake_validate(root, files, *, test_rel=None, collect=True, collect_tests=None):
        calls.append(files)
        rep = validator.Report()
        rep.add("Python syntax", True, "2 file(s) parse.")
        rep.add("Page Object members", True, "All used.")
        return rep

    monkeypatch.setattr(validator, "validate", fake_validate)
    t = Talk(c)
    t.final(t.say("generate automation for test case 106"))
    r = t.say("validate the script")
    status, line = t.final(r)
    assert status == "completed" and line.startswith("The script of test case 106 passed all 2 checks")
    assert "it has not been run" in line and calls == [["tests/test_tc106.py", "test_data/tc106.json"]]

    studio.outcome[106] = "FAILED"
    r = t.say("run this test")
    status, line = t.final(r)
    assert status == "completed"  # the command completed; the test itself failed, and the reply says so
    assert line.startswith("Test case 106 failed at step 2, Click Sign in: Locator #sign-in was not found")
    assert "passed" not in line
    r = t.say("show me the failure")
    assert "Likely cause: the Sign in button changed its id" in r["say"] and "tab=results" in r["navigate"]

    r = t.say("fix the locator and regenerate")
    status, line = t.final(r)
    assert status == "completed" and "regenerated as version 2" in line and "It has not been run yet" in line
    studio.outcome[106] = "PASSED"
    status, line = t.final(t.say("run it again"))
    assert line.startswith("Test case 106 passed: 2 steps ran")


def test_run_environments_are_checked(env):
    c, fake, studio, rt = env
    t = Talk(c)
    t.final(t.say("generate automation for test case 105"))
    r = t.say("run this test in the test environment")  # no environment set: runs where it was generated, and says so
    status, line = t.final(r)
    assert "because no environment is set for it" in r["say"] and status == "completed"
    rt.automation.creds.save("Shop", "user", "tester", None, None, "UAT")
    n = len(studio.runs)
    r = t.say("run this test in the staging environment")
    assert r["status"] == "failed" and "set up for uat, not the staging environment" in r["say"] and len(studio.runs) == n
    r = t.say("run this test in the production environment")
    assert r["status"] == "needs_confirmation" and len(studio.runs) == n
    t.say("cancel")
    r = t.say("run test case 105 in the UAT environment")
    t.final(r)
    assert len(studio.runs) == n + 1


def test_runner_passes_settings_only_when_set(env, monkeypatch):
    """The framework keeps its own defaults unless a run setting was saved (no change for existing users)."""
    c, fake, studio, rt = env
    from qa_platform.automation.framework import runner as rmod
    seen = []

    def popen(cmd, **kw):
        seen.append(kw["env"])
        raise OSError("not started in this test")

    monkeypatch.setattr(rmod.subprocess, "Popen", popen)
    record = {"id": "FWS-1", "test_case_id": "105", "user_story_id": "9001", "test_case_title": "Login", "file":
              "tests/test_tc105.py", "data_file": "test_data/tc105.json", "base_url": "https://shop.test/",
              "acceptance_criteria": [], "flow_steps": [], "locators": []}
    run = lambda rid: rt.studio.runner.run(record, rid, workspace=rt.paths.workspace, env_extra={}, secrets=[],  # noqa: E731
                                           headless=True)
    first = run("RUN-T1")
    assert first["browser"] == "chromium"
    assert not {"BROWSER", "QA_TIMEOUT_MS", "QA_STEP_SCREENSHOTS"} & set(seen[0])  # nothing new on a default install
    rt.settings.update({"playback": {"browser": "msedge", "timeout_seconds": 30, "step_screenshots": False}})
    second = run("RUN-T2")
    assert second["browser"] == "msedge"
    assert seen[1]["BROWSER"] == "msedge" and seen[1]["QA_TIMEOUT_MS"] == "30000" and seen[1]["QA_STEP_SCREENSHOTS"] == "0"
