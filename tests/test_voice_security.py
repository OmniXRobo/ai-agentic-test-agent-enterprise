"""Milestone 9: voice permissions (AC25), privacy of transcripts and secrets, audit log, interruptions, session
isolation and timeouts. TFS is simulated (fake_tfs.py)."""

import logging
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
        c.put("/api/voice/settings", json={"agent_command_name": "Captain", "phrase_style": "conversational", "address_as": "Boss"})
        yield c, fake, rt


def role(c, name):
    assert c.put("/api/voice/settings", json={"voice_role": name}).status_code == 200


def test_ac25_viewer_and_tester_permissions(env):
    c, fake, rt = env
    t = Talk(c)
    assert c.get("/api/voice/settings").json()["voice_role"] == "full"  # default: the behaviour before this version
    role(c, "viewer")
    r = t.say("Captain, create a new test plan for Sprint 25")
    assert r["status"] == "failed" and "set to Viewer, which doesn't allow creating or changing test assets" in r["say"]
    assert not fake.writes
    assert t.say("find the test plan for sprint 23")["status"] == "completed"   # looking things up is allowed
    assert t.say("go to test suites")["status"] == "completed"                  # so is navigation
    assert t.say("what are the automation settings")["status"] == "completed"
    r = t.say("set timeout to 30 seconds")
    assert r["status"] == "failed" and "changing settings" in r["say"]
    assert "timeout_seconds" not in rt.settings.section("playback")

    role(c, "tester")
    pid = next(p["id"] for p in fake.plans.values() if p["name"] == "Shop - Sprint 23 - Test Plan")
    t.page = {"route": "test-suites", "params": {"plan": str(pid)}}
    t.final(t.say("select performance testing"))                                # creating is allowed
    r = t.say("delete the performance testing suite")
    assert r["status"] == "failed" and "doesn't allow deleting" in r["say"]
    assert any(s["name"] == "Performance Testing" for s in fake.suites[pid])

    role(c, "full")
    assert t.say("delete the performance testing suite")["status"] == "needs_confirmation"
    role(c, "viewer")                                                            # changed while the question waits
    r = t.say("confirm")
    assert r["status"] == "failed" and "Viewer" in r["say"]                     # checked again before it runs
    assert any(s["name"] == "Performance Testing" for s in fake.suites[pid])


def test_spoken_secrets_are_never_stored_or_logged(env, caplog):
    c, fake, rt = env
    t = Talk(c, "conv-sec-0001")
    caplog.set_level(logging.INFO)
    t.say("my password is Hunter2-secret")
    t.say("enter 4111 1111 1111 1111 in the card number")
    t.say("set the otp to 998877")
    audit = c.get("/api/voice/audit?format=csv")
    assert audit.status_code == 200 and audit.headers["content-type"].startswith("text/csv")
    body = audit.text + str(c.get("/api/voice/commands?conversation_id=conv-sec-0001").json())
    for secret in ("Hunter2-secret", "4111 1111 1111 1111", "998877"):
        assert secret not in body and secret not in caplog.text
    assert "my password is [hidden]" in body and "enter [hidden] in the card number" in body
    items = c.get("/api/voice/audit").json()["items"]
    assert items and {"created_at", "text", "command", "status", "reply"} <= set(items[0])


def test_privacy_controls(env):
    c, fake, rt = env
    t = Talk(c, "conv-sec-0002")
    c.put("/api/voice/settings", json={"keep_transcripts": False})
    t.say("find the test plan for sprint 23")
    row = rt.db.query_one("SELECT text, reply FROM voice_commands ORDER BY id DESC LIMIT 1")
    assert row["text"] == "(transcript not kept)" and row["reply"]           # what ran is still audited
    c.put("/api/voice/settings", json={"keep_transcripts": True, "transcript_days": 7})
    rt.db.execute("INSERT INTO voice_commands (conversation_id, text, source, status, created_at) VALUES (?,?,?,?,?)",
                  ("old", "an old command", "speech", "completed", "2020-01-01T10:00:00.000+00:00"))
    rt.voice._purged_at = -1e9
    t.say("show me more details")
    assert not rt.db.query("SELECT id FROM voice_commands WHERE conversation_id = 'old'")
    r = c.delete("/api/voice/history").json()
    assert r["ok"] and r["deleted"] >= 1
    assert c.put("/api/voice/settings", json={"voice_role": "admin"}).status_code == 422


def test_stop_repeat_continue_and_explain(env):
    c, fake, rt = env
    t = Talk(c, "conv-sec-0003")
    assert t.say("stop")["say"] == "Stopped. Nothing was running."
    t.say("find the test plan for sprint 23")
    first = t.say("show me more details")["say"]
    assert t.say("repeat that")["say"] == first
    assert t.say("say that again")["say"] == first                             # repeatable, not a duplicate
    r = t.say("what are you doing")
    assert r["say"].startswith("Nothing is running right now. The last thing I did: ")
    assert t.say("continue")["say"] == "There's nothing paused. Tell me what you'd like to do next."

    t.say("rename the test plan to Archive")
    assert t.say("continue")["say"].startswith("I'm waiting for your confirmation")
    r = t.say("wait")
    assert r["say"] == "Stopped. I dropped the question I was waiting on. Anything already saved stays saved."
    assert t.say("confirm")["say"] == "There is nothing waiting for your confirmation."
    assert fake.plans[min(fake.plans)]["name"] == "Shop - Sprint 23 - Test Plan"  # never renamed


def test_session_isolation(env):
    c, fake, rt = env
    a, b = Talk(c, "conv-sec-000a"), Talk(c, "conv-sec-000b")
    a.say("find the test plan for sprint 23")
    r = b.say("show me more details")
    assert r["context"]["test_plan_id"] is None and r["status"] != "completed" or "Sprint 23" not in r["say"]
    assert a.say("show me more details")["context"]["test_plan_id"] is not None


def test_generation_wait_has_a_time_limit(env):
    c, fake, rt = env
    from qa_platform.errors import QAError

    class Stuck:
        def job(self, jid):
            return {"id": jid, "status": "RUNNING", "items": []}

    rt.voice.studio = Stuck()
    with pytest.raises(QAError) as exc:
        rt.voice._wait_job("FWJ-1", limit=0.6)
    assert exc.value.error_code == "VOICE_JOB_TIMEOUT" and "stopped waiting" in exc.value.message


def test_a_spoken_password_is_never_typed(env):
    c, fake, rt = env
    calls = []

    class Session:
        active = True

    rt.automation.recordings["REC-T"] = {"session": Session(), "application": "Shop", "start_url": "https://shop.test/"}
    rt.automation.voice_action = lambda run_id, p: calls.append(p) or "done"
    t = Talk(c, "conv-sec-0004")
    r = t.say("enter hunter2 in the password field")
    assert r["status"] == "failed" and "I don't type passwords or codes said aloud" in r["say"] and not calls
    r = t.say("enter 90210 in the zip code")
    assert r["status"] == "completed" and calls[-1]["value"] == "90210"
    rt.automation.recordings.clear()
