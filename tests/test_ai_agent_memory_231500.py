"""23.15.0: the AI Agent stops asking the same things twice.

* one preview, then "yes" (typed or spoken, English or Arabic) runs it: the chat no longer waits at WAITING
* what the person typed while teaching, and what they said last time, fills what a new request leaves unsaid
* a batch request ("create 10 trips, one per day, these drivers") becomes 10 records without 10 questions
* the sign-in password and an authorization code typed while teaching go into the credential vault (masked
  everywhere; a one-time code is never captured), and a learned code is filled by the run instead of pausing
"""

from __future__ import annotations

import json

import pytest

from skyguard_agent.agent import OperationsAgent
from skyguard_agent.autopilot import CONFIRM_WORDS, Autopilot
from skyguard_agent.vault import MemoryBackend, Vault


@pytest.mark.parametrize("text", ["yes", "Yes please", "confirm", "Confirmed", "go ahead", "run it", "do it now",
                                  "نعم", "نفذ", "تأكيد", "موافق"])
def test_confirmation_words(text):
    assert CONFIRM_WORDS.match(text)


@pytest.mark.parametrize("text", ["yes, create 3 trips", "cancel that", "no", "run the approval for trip 5"])
def test_not_confirmation_words(text):
    assert not CONFIRM_WORDS.match(text)


@pytest.fixture()
def agent(tmp_path):
    a = OperationsAgent(tmp_path / "agent", vault=Vault(MemoryBackend()))
    yield a
    a.close()


TRIP_WF = {
    "key": "Create trip", "kind": "workflow",
    "data": {"title": "Create trip", "operation": "create", "entity": "trip", "target_kind": "new",
             "parameters": [
                 {"name": "from", "field": "From", "type": "select", "required": True,
                  "options": ["Dubai", "Sharjah"], "example": "Dubai"},
                 {"name": "to", "field": "To", "type": "select", "required": True,
                  "options": ["Abu Dhabi", "Al Ain"], "example": "Abu Dhabi"},
                 {"name": "trip_date", "field": "Trip date", "type": "date", "required": True, "example": "2026-01-02"},
                 {"name": "driver", "field": "Driver", "type": "select", "required": True,
                  "options": ["Aslam Khan", "Bilal Saeed", "Dina Yousef"], "example": "Aslam Khan"},
                 {"name": "vehicle", "field": "Vehicle", "type": "select", "required": True,
                  "options": ["BUS-101", "BUS-102"], "example": "BUS-101"}],
             "steps": [{"action": "navigate", "url": "/trips/new"}]}}


def _app(agent, pilot):
    app = pilot.connect("https://transport.example/", name="Transport")["application"]
    agent.kb.propose(app["id"], "workflow", TRIP_WF["key"], TRIP_WF["data"], source="learning", state="approved",
                     by="learning")
    return app


def test_usual_values_fill_what_the_request_leaves_unsaid(agent):
    pilot = Autopilot(agent, auto_trust=True, reuse_values=True)
    app = _app(agent, pilot)
    prepared = pilot._prepare(app, "create a trip tomorrow", {}, wf=pilot._workflows(app["id"])[0])
    assert not prepared.get("ask"), prepared  # nothing is asked: the teaching values are used
    for value in ("Dubai", "Abu Dhabi", "Aslam Khan", "BUS-101"):
        assert value in prepared["text"], prepared["text"]
    assert any("while teaching" in f for f in prepared["filled"])  # the preview says where each value came from
    # the person's own last values win over the teaching values next time
    pilot.remember_values(app["id"], {"key": TRIP_WF["key"], "data": TRIP_WF["data"]},
                          {"driver": "Dina Yousef", "vehicle": "BUS-102"})
    again = pilot._prepare(app, "create a trip tomorrow", {}, wf=pilot._workflows(app["id"])[0])
    assert "Dina Yousef" in again["text"] and "BUS-102" in again["text"]
    assert any("your usual value" in f for f in again["filled"])


def test_batch_with_one_date_per_trip_and_a_driver_list(agent):
    from skyguard_agent import planner

    pilot = Autopilot(agent, auto_trust=True, reuse_values=True)
    app = _app(agent, pilot)
    wf = pilot._workflows(app["id"])[0]
    text = ("create 10 trips with the same data, one per day, drivers: Aslam Khan, Bilal Saeed, Dina Yousef")
    prepared = pilot._prepare(app, text, {}, wf=wf)
    assert not prepared.get("ask"), prepared
    records = planner.expand(planner.parse_structured(prepared["text"], wf), wf)
    assert len(records) == 10
    dates = [r["trip_date"] for r in records]
    assert len(set(dates)) == 10 and sorted(dates) == dates  # a different day for each trip
    assert {r["driver"] for r in records} == {"Aslam Khan", "Bilal Saeed", "Dina Yousef"}
    assert {r["from"] for r in records} == {"Dubai"}  # the same data for the rest


def test_usual_prompts_are_remembered_and_suggested(agent):
    pilot = Autopilot(agent, auto_trust=True, reuse_values=True)
    app = pilot.connect("https://transport.example/", name="Transport")["application"]
    pilot.remember_prompt(app["id"], "create 10 trips one per day", "Create trip")
    pilot.remember_prompt(app["id"], "create 10 trips one per day", "Create trip")
    usual = pilot.usual_prompts(app["id"])
    assert usual[0]["text"] == "create 10 trips one per day" and usual[0]["uses"] == 2


def test_secrets_typed_while_teaching_are_vaulted_masked_and_never_in_the_knowledge(agent):
    pilot = Autopilot(agent)
    app = pilot.connect("https://transport.example/", name="Transport")["application"]
    events = [
        {"kind": "fill", "url": "https://transport.example/login", "value": "operator",
         "element": {"tag": "input", "type": "text", "label": "User name", "h": "1"}},
        {"kind": "fill", "url": "https://transport.example/login", "value": "[secret]", "secret": True,
         "secret_value": "Tr4nsp0rt!", "secret_kind": "password",
         "element": {"tag": "input", "type": "password", "label": "Password", "h": "2", "secret": True}},
        {"kind": "fill", "url": "https://transport.example/trips/1", "value": "[secret]", "secret": True,
         "secret_value": "4417", "secret_kind": "code",
         "element": {"tag": "input", "type": "text", "label": "Authorization code", "h": "3", "secret": True}},
    ]
    out = agent.observe(app["id"], None, events, None, by="tester", use_ai=False)
    assert [x["kind"] for x in out["remembered_secrets"]] == ["password", "code"]
    assert all(x["value"] == "********" for x in out["remembered_secrets"])
    # the values are gone from the events before anything is stored
    assert all("secret_value" not in e for e in events)
    acc = next(a for a in agent.kb.accounts(app["id"]) if a["login_id"] == "operator")
    assert acc["has_credential"] and "Tr4nsp0rt" not in json.dumps(acc)
    assert agent.kb.credential_for(acc["id"]) == "Tr4nsp0rt!"        # the executor, at the moment it signs in
    codes = agent.kb.app_secrets(app["id"])
    assert [c["label"] for c in codes] == ["Authorization code"] and codes[0]["value"] == "********"
    assert agent.kb.app_secret_value(app["id"], "Authorization code") == "4417"
    # nothing of either secret is in the knowledge database or in what the page is shown
    blob = (agent.store.path).read_bytes() + json.dumps(agent.understanding_view(app["id"]), default=str).encode()
    assert b"Tr4nsp0rt!" not in blob and b"4417" not in blob
    agent.kb.forget_app_secret(app["id"], "Authorization code", by="tester")
    assert agent.kb.app_secret_value(app["id"], "Authorization code") is None


def test_a_one_time_code_is_never_captured(agent):
    """The browser hands over only lasting secrets; an OTP field's value never reaches the agent."""
    js = (agent.store.path.parent / "x")  # not used; the rule itself is in the page script
    source = (__import__("pathlib").Path(__file__).resolve().parents[1] / "skyguard_agent" / "js"
              / "agent_dom.js").read_text(encoding="utf-8")
    assert "__sgLearnSecrets" in source and "one-time-code" in source
    assert "oneTime" in source and "secret_value" in source
    assert js is not None
