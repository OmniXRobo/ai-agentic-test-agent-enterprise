"""Milestone 5: test cases and test data by voice, against the simulated TFS and design engines (fake_tfs.py)."""

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
        c.put("/api/voice/settings", json={"agent_command_name": "Captain", "phrase_style": "conversational", "address_as": "Boss"})
        yield c, fake


def plan_with_suites(t: Talk):
    t.final(t.say("create a new test plan for sprint 25"))
    t.final(t.say("select functional and regression"))


def test_ac12_generate_from_acceptance_criteria_for_the_selected_suites(env):
    c, fake = env
    t = Talk(c)
    plan_with_suites(t)
    r = t.say("Captain, now generate test cases from the acceptance criteria")
    assert r["status"] == "accepted"
    assert r["speak"].startswith("On it, Boss. I'm checking the acceptance criteria of 2 user stories in Sprint 25 and preparing "
                                 "Functional Testing and Regression Testing test cases.")
    assert r["navigate"].startswith("#/test-cases?plan=") and "&runs=" in r["navigate"]
    status, line = t.final(r)
    assert status == "completed"
    assert line.startswith("8 test cases generated from the acceptance criteria of 2 user stories in Sprint 25: "
                           "4 Functional Testing and 4 Regression Testing.")
    assert "2 possible duplicates found." in line and "2 cases have errors to fix before saving." in line
    assert "Say save the test cases" in line
    assert not fake.published  # waits for review: nothing saved yet
    assert len(fake.generated) == 2


def test_save_needs_confirmation_and_reports_real_counts(env):
    c, fake = env
    t = Talk(c)
    plan_with_suites(t)
    t.final(t.say("generate test cases for sprint 25"))
    r = t.say("save the test cases")
    assert r["status"] == "needs_confirmation" and "cases with errors are held back" in r["say"]
    status, line = t.final(t.say("confirm"))
    assert status == "completed"
    assert line == ("4 test cases created in TFS and 2 existing ones linked instead of duplicated. "
                    "2 cases with errors were not saved; fix them on Test Cases.")
    assert len(fake.published) == 2


def test_generate_without_plan_or_suites_asks(env):
    c, fake = env
    t = Talk(c)
    r = t.say("generate test cases")
    assert r["status"] == "unclear" and "For which sprint or test plan" in r["say"]
    t.final(t.say("create a new test plan for sprint 25"))
    r = t.say("generate test cases")
    assert r["status"] == "unclear" and "has no test suites yet" in r["say"]
    r = t.say("generate test cases for sprint 26")
    assert r["status"] == "unclear" and "There is no test plan for Sprint 26 yet" in r["say"]
    assert not fake.generated


def test_old_user_story_generation_still_used_when_a_story_is_the_context(env, monkeypatch):
    c, fake = env
    t = Talk(c)
    t.page = {"route": "user-stories", "params": {"id": "118"}}
    called = {}
    from qa_platform.voice.model import Outcome
    from qa_platform.voice.service import VoiceService

    def old_path(self, conv, intent):
        called["story"] = conv.user_story_id
        return Outcome("completed", "old path")

    monkeypatch.setattr(VoiceService, "_cmd_generate_test_cases", old_path)
    r = t.say("generate test cases")
    assert called.get("story") == 118 and r["say"] == "old path"


def test_explain_and_open_test_case(env):
    c, fake = env
    t = Talk(c)
    r = t.say("explain test case 106")
    assert r["say"] == ("Test case 106: Login with wrong password. It has 2 steps. It starts with: Open the login page. "
                        "The expected result is: The dashboard opens.")
    r = t.say("explain this test case")
    assert r["say"].startswith("Test case 106")
    r = t.say("explain test case 999")
    assert r["status"] == "failed"


def test_ac13_test_data_for_named_cases_with_volume(env):
    c, fake = env
    t = Talk(c)
    r = t.say("Captain, generate test data for test cases 105, 106 and 107")
    assert r["status"] == "accepted" and r["navigate"] == "#/test-data?tc=105,106,107"
    assert r["speak"] == "On it, Boss. I'm checking test cases 105, 106 and 107 and generating up to 12 rows each."
    status, line = t.final(r)
    assert status == "completed" and line.startswith("9 rows of test data generated for test cases 105, 106 and 107")
    assert "1 gap noted" in line and "say save this test data" in line
    assert fake.data_calls == [([105, 106, 107], 12)]
    r = t.say("generate five more combinations")  # "the current test case" and the requested volume
    t.final(r)
    assert fake.data_calls[-1] == ([105], 5)


def test_test_data_limits_are_said_not_hidden(env):
    c, fake = env
    t = Talk(c)
    t.say("explain test case 105")
    r = t.say("generate only negative test data")
    assert "can't be limited to negative rows yet" in r["say"]
    r = t.say("generate test data for test case 555")
    assert r["status"] == "failed" and "couldn't find test case 555" in r["say"]
    t2 = Talk(c, "conv-design-0009")
    r = t2.say("generate test data")
    assert r["status"] == "needs_input"
    r = t2.say("105 and 107")  # the answer to "for which test cases?"
    assert r["status"] == "accepted" and "105 and 107" in r["say"]


def test_saved_cases_become_these_test_cases(env):
    c, fake = env
    t = Talk(c)
    plan_with_suites(t)
    t.final(t.say("generate test cases for sprint 25"))
    t.say("save the test cases")
    t.final(t.say("confirm"))
    saved = t.say("show me more details")["context"]["saved_case_ids"]
    assert len(saved) == 5 and 77 in saved  # 4 created plus the one existing case both runs linked to
    r = t.say("generate test data for these test cases")
    assert r["status"] == "accepted"
    status, line = t.final(r)
    assert status == "completed"
    assert fake.data_calls[-1][0] == saved  # the saved ids are the ones sent to the data engine
