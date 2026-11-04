"""Milestones 3-4: test plans and test suites by voice, against a simulated TFS project (fake_tfs.py)."""

import os
import sys
import time
from pathlib import Path

import pytest

APP = Path(os.environ.get("APP_DIR", Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(APP))
sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("QA_HOME", str(tmp_path))
    from fastapi.testclient import TestClient
    from qa_platform.main import create_app
    import fake_tfs
    app = create_app()
    with TestClient(app) as c:
        fake = fake_tfs.install(app.state.runtime)
        c.put("/api/voice/settings", json={"agent_command_name": "Captain", "phrase_style": "conversational", "address_as": "Boss"})
        yield c, fake


class Talk:
    def __init__(self, client, conv="conv-plans-0001"):
        self.c, self.conv, self.page = client, conv, {"route": "dashboard", "params": {}}

    def say(self, text):
        r = self.c.post("/api/voice/commands", json={"text": text, "conversation_id": self.conv, "source": "speech", "page": self.page})
        assert r.status_code == 200, r.text
        body = r.json()
        if body.get("navigate") and body["navigate"] != "back":
            route, _, q = body["navigate"].lstrip("#/").partition("?")
            self.page = {"route": route, "params": dict(x.split("=", 1) for x in q.split("&") if "=" in x)}
        return body

    def final(self, reply, timeout=10):
        """Wait for a background command and return (status, spoken line)."""
        end = time.time() + timeout
        while time.time() < end:
            items = self.c.get(f"/api/voice/commands?conversation_id={self.conv}").json()["items"]
            it = next(i for i in items if i["id"] == reply["command_id"])
            if it["status"] in ("completed", "failed"):
                return it["status"], it["final_reply"]
            time.sleep(0.05)
        raise AssertionError("command did not finish")


def test_ac09_find_by_sprint_and_open(env):
    c, fake = env
    t = Talk(c)
    r = t.say("Captain, find the test plan for Sprint 23")
    assert r["status"] == "completed" and r["navigate"].startswith("#/test-plans?id=")
    assert r["speak"].startswith("Understood, Boss. I've found the test plan for Sprint 23")
    assert r["context"]["test_plan_name"] == "Shop - Sprint 23 - Test Plan"


def test_ac18_two_matches_ask_which_then_select(env):
    c, fake = env
    t = Talk(c)
    r = t.say("find the test plan for sprint 24")
    assert r["status"] == "unclear" and "I found 2 test plans for Sprint 24" in r["say"] and "Which one" in r["say"]
    assert r["context"]["test_plan_id"] is None  # never guessed
    r = t.say("the second one")
    assert r["status"] == "completed" and r["context"]["test_plan_id"] is not None
    assert r["navigate"] == f"#/test-plans?id={r['context']['test_plan_id']}"


def test_find_by_id_name_state_and_missing(env):
    c, fake = env
    t = Talk(c)
    pid = next(p["id"] for p in fake.plans.values() if p["name"] == "Checkout Regression")
    assert t.say(f"open test plan {pid}")["context"]["test_plan_id"] == pid
    assert t.say("search test plans for checkout")["context"]["test_plan_id"] == pid
    r = t.say("show inactive test plans")
    assert r["status"] == "completed" and "Sprint 23" in r["say"]
    r = t.say("find the test plan for sprint 99")
    assert r["status"] == "failed" and r["speak"].startswith("Sorry, Boss. I couldn't find a test plan for Sprint 99")


def test_ac10_ac17_create_then_open_it_then_suites_then_details(env):
    c, fake = env
    t = Talk(c)
    r = t.say("Captain, create a new test plan for Sprint 25")
    assert r["status"] == "accepted" and r["speak"] == "On it, Boss. I'm preparing the test plan Shop - Sprint 25 - Test Plan."
    status, line = t.final(r)
    assert status == "completed" and "has been created" in line
    created = [w for w in fake.writes if w[0] == "create_plan"]
    assert created == [("create_plan", created[0][1], "Shop - Sprint 25 - Test Plan", "Shop\\Sprint 25")]  # really in TFS
    pid = created[0][1]
    r = t.say("open it")  # "it" = the plan just created
    assert r["navigate"] == f"#/test-plans?id={pid}"
    r = t.say("now go to test suites")
    assert r["navigate"] == f"#/test-suites?plan={pid}" and "for Shop - Sprint 25 - Test Plan" in r["say"]
    r = t.say("Select Functional and Regression, but uncheck Performance.")
    assert r["status"] == "accepted" and "adding Functional Testing and Regression Testing" in r["say"]
    status, line = t.final(r)
    assert status == "completed" and line.startswith("Your test suite selections have been saved")
    names = [s["name"] for s in fake.suites[pid] if s["parent_id"]]
    assert names == ["Functional Testing", "Regression Testing"]  # Performance not added
    r = t.say("show me more details")
    assert "It has 2 suites: Functional Testing and Regression Testing" in r["say"]
    assert r["context"]["suite_selection"] == {"functional": True, "regression": True, "performance": False}


def test_ac26_repeated_create_is_not_executed_twice(env):
    c, fake = env
    t = Talk(c)
    r1 = t.say("create a new test plan for sprint 26")
    r2 = t.say("create a new test plan for sprint 26")  # recognizer delivered it twice
    assert r2.get("duplicate") is True and "not doing it twice" in r2["say"]
    t.final(r1)
    assert len([w for w in fake.writes if w[0] == "create_plan"]) == 1
    # Later the same request reuses the plan instead of making a second one.
    time.sleep(5.1)
    r3 = t.say("create a new test plan for sprint 26")
    status, line = t.final(r3)
    assert "already exists" in line and len([w for w in fake.writes if w[0] == "create_plan"]) == 1


def test_create_with_unknown_or_ambiguous_sprint_asks(env):
    c, fake = env
    t = Talk(c)
    r = t.say("create a new test plan for sprint 77")
    assert r["status"] == "needs_input" and "can't find Sprint 77" in r["say"]
    r = t.say("create a new test plan for sprint 3")
    assert r["status"] == "needs_input" and "matches 2 sprints" in r["say"]
    assert not [w for w in fake.writes if w[0] == "create_plan"]


def test_ac11_batch_select_uncheck_existing_and_delete_with_confirmation(env):
    c, fake = env
    t = Talk(c)
    pid = next(p["id"] for p in fake.plans.values() if p["name"] == "Shop - Sprint 23 - Test Plan")
    t.page = {"route": "test-suites", "params": {"plan": str(pid)}}  # the plan shown on the page is the context
    r = t.say("Select Functional, Integration and Regression Testing, and uncheck Performance Testing.")
    t.final(r)
    r = t.say("select performance testing")
    t.final(r)
    r = t.say("uncheck the performance testing suite")
    assert r["status"] == "completed" and "unchecking did not delete it" in r["say"]  # never deletes silently
    assert any(s["name"] == "Performance Testing" for s in fake.suites[pid])
    r = t.say("delete the performance testing suite")
    assert r["status"] == "needs_confirmation" and r["speak"].startswith("Please confirm, Boss. This deletes the Performance Testing suite")
    r = t.say("confirm")
    status, line = t.final(r)
    assert status == "completed" and not any(s["name"] == "Performance Testing" for s in fake.suites[pid])
    r = t.say("which suites does it have")
    assert "Functional Testing, Integration Testing and Regression Testing" in r["say"]


def test_save_suites_and_no_plan_asks(env):
    c, fake = env
    t = Talk(c, "conv-plans-0002")
    r = t.say("select regression and integration")
    assert r["status"] == "unclear" and "Which test plan" in r["say"]  # never the wrong plan
    assert not fake.writes
    r = t.say("update the test suites")
    assert r["status"] == "unclear"


def test_ac19_tfs_failure_is_reported(env):
    c, fake = env
    t = Talk(c)
    fake.fail_next = "create_plan"
    r = t.say("create a new test plan for sprint 25")
    status, line = t.final(r)
    assert status == "failed" and "could not be created" in line and "not reachable" in line


def test_update_plan_needs_confirmation_and_is_verified(env):
    c, fake = env
    t = Talk(c)
    t.say("find the test plan for sprint 23")
    r = t.say("rename the test plan to Sprint 23 Archive")
    assert r["status"] == "needs_confirmation"
    assert fake.plans[min(fake.plans)]["name"] == "Shop - Sprint 23 - Test Plan"  # nothing changed yet
    status, line = t.final(t.say("yes"))
    assert status == "completed" and fake.plans[min(fake.plans)]["name"] == "Sprint 23 Archive"  # capitals kept
    r = t.say("set the test plan to active")
    t.say("cancel")
    assert fake.plans[min(fake.plans)]["state"] == "Inactive"


def test_context_follows_the_page(env):
    c, fake = env
    t = Talk(c)
    a, b = sorted(fake.plans)[:2]
    t.page = {"route": "test-plans", "params": {"id": str(a)}}
    assert t.say("show me more details")["context"]["test_plan_id"] == a
    t.page = {"route": "test-suites", "params": {"plan": str(b)}}  # the user picked another plan on the page
    r = t.say("list the suites")
    assert r["context"]["test_plan_id"] == b


def test_old_commands_keep_their_meaning():
    from qa_platform.voice import intents
    assert intents.parse("create the test plan").command == "generate_test_plan"
    assert intents.parse("create the test plan for user story 118").params == {"user_story_id": 118}
    assert intents.parse("open test plans").command == "open_page"
    assert intents.parse("select the second one").command == "select_item"
    assert intents.parse("select electronics").command == "browser_action"
    assert intents.parse("click login").command == "browser_action"
