"""Stage 7: Voice Command Agent - intent analysis, follow-up context, confirmations, spoken results, speech server."""

from __future__ import annotations

import json
import re
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from qa_platform.credentials import MemoryBackend
from qa_platform.main import create_app
from qa_platform.performance.models import HttpRequest, LoadProfile, PerfPlan, Sla
from qa_platform.voice.intents import normalize, parse, words_to_numbers
from tests.fake_ado import PAT, FakeAzureDevOps
from tests.fake_llm import FakeLLM
from tests.test_automation import HAVE_PLAYWRIGHT, _login_script

needs_browser = pytest.mark.skipif(not HAVE_PLAYWRIGHT, reason="playwright is not installed")
CONV = "conv-test-0001"


# ---- intent analysis (pure) ------------------------------------------------------------------

@pytest.mark.parametrize("text,command,params", [
    ("Open dashboard", "open_page", {"route": "dashboard"}),
    ("Open user story 118", "open_work_item", {"kind": "user story", "id": 118}),
    ("Generate test cases for user story 118", "generate_test_cases", {"user_story_id": 118}),
    ("Generate automation for test case 1280", "generate_automation", {"test_case_id": 1280}),
    ("Generate automation for the smoke test", "generate_automation", {"name": "smoke"}),
    ("Play test case 1280", "run_test_case", {"test_case_id": 1280, "play": True}),
    ("Run test case 1280.", "run_test_case", {"test_case_id": 1280, "play": False}),
    ("Run all automated tests", "run_all_tests", {}),
    ("Start browser recording", "start_recording", {}),
    ("Open test data", "open_page", {"route": "test-data"}),
    ("Run performance test for checkout", "run_performance", {"name": "checkout"}),
    ("Run a 100 user load test for five minutes", "run_performance", {"kind": "load", "users": 100, "duration_s": 300}),
    ("Run a load test for checkout with 100 users for five minutes.", "run_performance",
     {"kind": "load", "users": 100, "duration_s": 300, "name": "checkout"}),
    ("Show failed tests", "show_failed", {}),
    ("Create defects for failed tests", "create_defects", {"source": "functional"}),
    ("Analyze the failure", "analyze_failure", {}),
    ("Run JMeter test", "run_performance", {}),
    ("Show performance report", "show_performance_report", {}),
    ("Stop the running test", "stop_run", {}),
    ("Open TFS connection settings", "open_page", {"route": "tfs-settings"}),
    ("run it again", "run_test_case", {"play": False}),
    ("yes", "confirm", {}),
    ("never mind", "cancel", {}),
])
def test_spec_examples_map_to_supported_commands(text, command, params):
    intent = parse(text)
    assert intent is not None and intent.command == command and intent.params == params


def test_unmapped_sentences_and_number_words():
    assert parse("order a pizza") is None and parse("") is None
    assert words_to_numbers("a hundred and fifty users for two hours") == "150 users for 2 hours"
    assert normalize("Open user-story #118!") == "open user story 118"


# ---- service against the fake TFS / LLM / speech server ----------------------------------------

class FakeWhisper:
    def __init__(self, text=" Open user story 118. "):
        self.text = text
        self.requests: list[httpx.Request] = []
        self.down = False

    def transport(self):
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        self.requests.append(request)
        if request.url.path.endswith(("/inference", "/audio/transcriptions")):
            return httpx.Response(200, json={"text": self.text})
        return httpx.Response(200, text="<html>whisper.cpp server</html>")


@pytest.fixture()
def env(paths):
    ado, llm, stt = FakeAzureDevOps(severity=True), FakeLLM(), FakeWhisper()
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport(), ai_transport=llm.transport(),
                     stt_transport=stt.transport())
    with TestClient(app) as c:
        c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                "make_active": True})
        c.put("/api/ai/settings", json={"base_url": "http://127.0.0.1:8080/v1", "model": "qwen2.5-7b-instruct-q4_k_m.gguf"})
        rt = c.app.state.runtime
        rt.settings.update({"playback": {"visual_delay_ms": 0, "headless": True}})
        rt.automation.creds.save("Shop", "admin", "alice", "s3cret-pass", None)
        yield c, ado, rt, stt


def say(c, text, **extra):
    r = c.post("/api/voice/commands", json={"text": text, "conversation_id": CONV, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def final(rt, command_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = rt.db.query_one("SELECT status, final_reply FROM voice_commands WHERE id = ?", (command_id,))
        if row["status"] != "running":
            return row
        time.sleep(0.1)
    raise AssertionError("voice command did not finish")


def spoken(rt, command_id):
    """Everything the agent said aloud in the background for one command, in order."""
    rows = rt.db.query("SELECT details FROM events WHERE agent_id = 'voice_command' ORDER BY id")
    out = []
    for r in rows:
        d = json.loads(r["details"] or "{}")
        if d.get("command_id") == command_id and d.get("speak"):
            out.append(d["speak"])
    return out


def test_follow_up_uses_the_story_in_context_and_reports_completion(env):
    c, ado, rt, _ = env
    r = say(c, "Open user story 118")
    assert r["status"] == "completed" and r["navigate"] == "#/user-stories?id=118"
    assert r["say"] == "User Story 118 opened: Checkout with saved card."
    r = say(c, "Generate the test cases")  # no number: the story just opened
    assert r["status"] == "accepted" and r["say"] == "Yes, I will generate the test cases for user story 118 now."
    done = final(rt, r["command_id"])
    assert done["status"] == "completed"
    assert done["final_reply"].startswith("Test case generation completed successfully. 2 test cases were generated")
    names_ = [e["name"] for e in rt.db.query("SELECT name FROM events WHERE name LIKE 'voice.command.%' ORDER BY id")]
    assert names_[-3:] == ["voice.command.received", "voice.command.accepted", "voice.command.completed"]


def test_missing_parameter_is_asked_for_and_page_is_context(env):
    c, ado, rt, _ = env
    r = say(c, "generate automation")
    assert r["status"] == "needs_input" and r["say"] == "Which test case? Say its number."
    r = say(c, "1280")  # the answer completes the earlier command
    assert r["via"] == "follow_up" and r["command"] == "generate_automation"
    assert "Test case 1280 is selected on AI-Driven Automation" in r["say"]  # no known application address
    r = c.post("/api/voice/commands", json={"text": "run this test case", "conversation_id": "conv-other-01",
                                            "page": {"route": "test-cases", "params": {"id": "1280"}}}).json()
    assert r["say"] == "There is no automated script for test case 1280 yet. Say generate automation for test case 1280 to create one."


def test_unsupported_without_ai_and_ai_fallback_never_invents_ids(env):
    c, ado, rt, _ = env
    r = say(c, "kick off case 1280 for me")  # no rule matches; the On-Premise AI maps it
    assert r["via"] == "ai" and r["command"] == "run_test_case"
    r = say(c, "kick off the checkout check")  # the AI answers 1280, which the user never said
    assert r["via"] == "ai" and r["status"] == "needs_input"
    c.put("/api/ai/settings", json={"base_url": ""})
    r = say(c, "order a pizza")
    assert r["status"] == "unsupported"
    assert r["say"] == "I cannot perform that instruction because no supported QA action is mapped to it."


def test_status_pages_and_stop(env):
    c, ado, rt, _ = env
    assert say(c, "stop the running test")["say"] == "Nothing is running right now."
    assert say(c, "show failed tests")["say"] == "There are no failed tests."
    assert say(c, "show performance report")["say"] == "There are no performance results yet."
    assert say(c, "what is running")["say"].startswith("No agent is running right now.")
    assert say(c, "yes")["say"] == "There is nothing waiting for your confirmation."
    hist = c.get(f"/api/voice/commands?conversation_id={CONV}").json()
    assert [h["text"] for h in hist["items"]][:2] == ["stop the running test", "show failed tests"]


@needs_browser
def test_voice_run_reports_failure_and_new_defect(env, site):
    c, ado, rt, _ = env
    c.put("/api/defects/mode", json={"creation_mode": "automatic"})
    script = _login_script(site)
    script.test_case_id = "1280"
    script.steps[-1].value, script.steps[-1].timeout_ms = "Welcome bob", 600
    rt.automation.scripts.save(script)
    r = say(c, "Run test case 1280")
    assert r["say"] == "Yes. I will run test case 1280 now." and r["navigate"] == "#/workflow"
    done = final(rt, r["command_id"])
    talk = spoken(rt, r["command_id"])
    assert talk and talk[0].startswith("The test execution failed at step 8") and "unexpected result" in talk[0], (done, talk)
    assert "I am checking for an existing functional defect." in talk
    assert done["final_reply"].startswith("I found no duplicate defect. A new functional defect ")
    tfs_id = int(re.search(r"functional defect (\d+) has been created", done["final_reply"]).group(1))
    assert ado.items[tfs_id]["System.WorkItemType"] == "Bug"
    # follow-up: "run it again" reruns the same script; the failure is recognised as the same defect
    r = say(c, "run it again")
    done = final(rt, r["command_id"])
    assert done["final_reply"] == f"Existing defect {tfs_id} was found and the new execution evidence was linked."


@needs_browser
def test_creating_defects_by_voice_needs_confirmation(env, site):
    c, ado, rt, _ = env
    script = _login_script(site)
    script.steps[-1].value, script.steps[-1].timeout_ms = "Welcome bob", 600
    script = rt.automation.scripts.save(script)
    run = rt.automation.run_script(script.id, background=False)
    deadline = time.time() + 15
    while time.time() < deadline and not rt.defects.list(status="open"):
        time.sleep(0.1)
    before = set(ado.items)
    r = say(c, "create defects for failed tests")
    assert r["status"] == "needs_confirmation" and "Say confirm" in r["say"] and set(ado.items) == before
    assert say(c, "cancel")["say"] == "Cancelled." and set(ado.items) == before
    say(c, "create defects for failed tests")
    r = say(c, "confirm")
    done = final(rt, r["command_id"])
    created = set(ado.items) - before
    assert len(created) == 1 and done["final_reply"] == f"Created defect {created.pop()} in TFS."
    assert run["status"] == "FAILED"


def test_local_speech_server_transcription(env):
    c, ado, rt, stt = env
    r = c.post("/api/voice/transcribe", content=b"RIFF....WAVE", headers={"content-type": "audio/wav"})
    assert r.status_code == 409 and r.json()["error_code"] == "VOICE_STT_NOT_CONFIGURED"
    assert c.put("/api/voice/settings", json={"stt_url": "ftp://x"}).status_code == 400
    c.put("/api/voice/settings", json={"stt_url": "http://127.0.0.1:8081/", "language": "en"})
    r = c.post("/api/voice/transcribe", content=b"RIFF....WAVE", headers={"content-type": "audio/wav"}).json()
    assert r["text"] == "Open user story 118."
    req = stt.requests[-1]
    assert str(req.url) == "http://127.0.0.1:8081/inference" and b'name="file"' in req.content and b"RIFF" in req.content
    c.put("/api/voice/settings", json={"stt_api": "openai", "stt_url": "http://127.0.0.1:8000/v1", "stt_model": "base.en"})
    c.post("/api/voice/transcribe", content=b"RIFF....WAVE", headers={"content-type": "audio/wav"})
    assert str(stt.requests[-1].url) == "http://127.0.0.1:8000/v1/audio/transcriptions" and b"base.en" in stt.requests[-1].content
    diag = c.post("/api/voice/diagnostics").json()["checks"]
    assert diag[0]["status"] == "PASS"
    stt.text = " [BLANK_AUDIO] "
    r = c.post("/api/voice/transcribe", content=b"RIFF....WAVE", headers={"content-type": "audio/wav"})
    assert r.status_code == 422 and r.json()["error_code"] == "VOICE_NOTHING_HEARD"
    stt.down = True
    r = c.post("/api/voice/transcribe", content=b"RIFF....WAVE", headers={"content-type": "audio/wav"})
    assert r.status_code == 502 and r.json()["error_code"] == "VOICE_STT_UNREACHABLE"


def test_load_test_by_voice_overrides_this_run_only(env, site):
    c, ado, rt, _ = env
    if not rt.performance.environment()["ready"]:
        pytest.skip("JMeter/Java not installed (set JMETER_HOME to run)")
    plan = rt.performance.save_plan(PerfPlan(
        name="Checkout", kind="load", requests=[HttpRequest(name="Checkout page", url=f"{site}/products.html")],
        profile=LoadProfile(users=1, ramp_up_s=1, duration_s=60, think_time_ms=200), sla=Sla(p95_ms=5000)))
    r = say(c, "Run a load test for checkout with 3 users for ten seconds")
    assert r["say"] == "Yes. I will prepare and run a 10-second Checkout load test with 3 virtual users."
    done = final(rt, r["command_id"], timeout=120)
    assert done["final_reply"].startswith("The performance test completed successfully. P95 response time was ")
    assert "The load test has started." in spoken(rt, r["command_id"])
    run = rt.performance.list_runs(plan_id=plan.id)[0]
    used = json.loads(rt.performance.run_file(run["run_id"], "plan.json").read_text())
    assert used["profile"]["users"] == 3 and used["profile"]["duration_s"] == 10
    assert rt.performance.get_plan(plan.id).profile.users == 1  # the saved plan is unchanged
