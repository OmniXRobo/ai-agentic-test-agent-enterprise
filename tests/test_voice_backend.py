"""Milestones 1-2 backend: settings, reply wording, verified navigation. Runs against the app in APP_DIR."""

import json
import os
import sys
from pathlib import Path

import pytest

APP = Path(os.environ.get("APP_DIR", Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(APP))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("QA_HOME", str(tmp_path))
    from fastapi.testclient import TestClient
    from qa_platform.main import create_app  # noqa: WPS433
    with TestClient(create_app()) as c:
        yield c


def test_defaults_wake_on_and_name_from_settings(client):
    s = client.get("/api/voice/settings").json()
    assert s["wake_listening"] is True  # AC01
    assert s["address_as"] == "Boss"
    assert s["follow_up_seconds"] == 8
    assert s["phrase_style"] == "conversational"
    assert s["agent_command_name"] == "Hami"  # unchanged default; the wake name is a setting (AC02)


def test_settings_validation_and_persistence(client, tmp_path):
    r = client.put("/api/voice/settings", json={"agent_command_name": "Captain", "wake_listening": False, "address_as": "  Chief  "})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent_command_name"] == "Captain" and body["wake_listening"] is False and body["address_as"] == "Chief"
    stored = json.loads((tmp_path / "settings.json").read_text())
    assert stored["voice"]["wake_listening"] is False  # survives restarts (AC28)
    assert client.put("/api/voice/settings", json={"address_as": "<script>"}).status_code == 422
    assert client.put("/api/voice/settings", json={"follow_up_seconds": 99}).status_code == 422
    assert client.put("/api/voice/settings", json={"phrase_style": "pirate"}).status_code == 422
    assert client.put("/api/voice/settings", json={"address_as": ""}).json()["address_as"] == ""


def test_voice_settings_do_not_touch_tfs_or_ai(client):
    before = client.get("/api/settings").json()["settings"]
    client.put("/api/voice/settings", json={"agent_command_name": "Captain", "wake_listening": True, "phrase_style": "conversational"})
    after = client.get("/api/settings").json()["settings"]
    for key in ("tfs", "ai", "server"):  # AC22, AC23
        assert before.get(key) == after.get(key)


def test_navigation_is_acknowledged_then_verified(client):
    client.put("/api/voice/settings", json={"agent_command_name": "Captain"})
    r = client.post("/api/voice/commands", json={"text": "Captain, go to the test plans page", "conversation_id": "conv-test-0001", "source": "speech"}).json()
    assert r["status"] == "completed" and r["navigate"] == "#/test-plans"
    assert r["verify"] == {"route": "test-plans", "label": "Test Plans"}  # the dashboard checks before claiming success (AC07)
    assert r["speak"] == "Understood, Boss. I'm opening Test Plans now."  # AC06
    back = client.post("/api/voice/commands", json={"text": "go back", "conversation_id": "conv-test-0001"}).json()
    assert back["navigate"] == "back" and back["verify"]["route"] == "back"


@pytest.mark.parametrize("phrase,route", [
    ("open test suites", "test-suites"), ("open test cases", "test-cases"), ("navigate to test data", "test-data"),
    ("open ai driven automation", "ai-automation"), ("go to guided browser automation", "guided-automation"),
    ("open acceptance criteria", "acceptance-criteria"), ("go to settings", "settings"), ("go back to the dashboard", "dashboard"),
])
def test_navigation_phrases(client, phrase, route):
    r = client.post("/api/voice/commands", json={"text": phrase, "conversation_id": "conv-test-0002"}).json()
    assert r["navigate"] == f"#/{route}", (phrase, r)


def test_older_styles_unchanged(client):
    client.put("/api/voice/settings", json={"phrase_style": "standard"})
    r = client.post("/api/voice/commands", json={"text": "open test plans", "conversation_id": "conv-test-0003"}).json()
    assert r["speak"] == "OK, I will do your task now. Test Plans opened."  # AC21
    client.put("/api/voice/settings", json={"phrase_style": "tactical"})
    r = client.post("/api/voice/commands", json={"text": "open test plans", "conversation_id": "conv-test-0013"}).json()
    assert r["speak"].startswith("Command received.")


def test_compose_conversational_wording():
    from qa_platform.voice.service import compose
    assert compose("accepted", "Generating.", "conversational", address="Boss") == "On it, Boss. Generating."
    assert compose("completed", "Report ready.", "conversational", background=True, address="Boss") == \
        "Done, Boss. Report ready. What would you like to do next?"
    assert compose("failed", "TFS is offline.", "conversational", background=True, address="Boss") == "Sorry, Boss. TFS is offline."
    assert compose("unclear", "Which plan?", "conversational", address="") == "One question. Which plan?"
    assert compose("needs_confirmation", "Delete it?", "conversational", address="Boss") == "Please confirm, Boss. Delete it?"


def test_unknown_command_asks_or_declines(client):
    r = client.post("/api/voice/commands", json={"text": "do something with the test case", "conversation_id": "conv-test-0004"}).json()
    assert r["status"] in ("unclear", "unsupported", "needs_input")  # never a guessed success (AC18)
    assert not r["speak"].startswith(("Understood", "Done"))


def test_bare_wake_name_alone_is_rejected_by_api(client):
    client.put("/api/voice/settings", json={"agent_command_name": "Captain"})
    r = client.post("/api/voice/commands", json={"text": "Captain", "conversation_id": "conv-test-0005"})
    assert r.status_code == 422  # the dashboard answers "Yes, Boss. I'm listening." itself and sends nothing


@pytest.mark.parametrize("phrase,route", [
    ("go back to the dashboard", "dashboard"), ("return to test plans", "test-plans"), ("take me to test suites", "test-suites"),
])
def test_named_page_after_back(client, phrase, route):
    r = client.post("/api/voice/commands", json={"text": phrase, "conversation_id": "conv-test-0006"}).json()
    assert r["navigate"] == f"#/{route}", (phrase, r)


@pytest.mark.parametrize("phrase", ["go back", "return to the previous page", "previous page", "back please"])
def test_previous_page(client, phrase):
    r = client.post("/api/voice/commands", json={"text": phrase, "conversation_id": "conv-test-0007"}).json()
    assert r["navigate"] == "back", (phrase, r)
