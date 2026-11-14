"""23.7.0: the agent learns FleetOps only by watching a person use it (no titles, no explanations), then understands
questions about it, and runs a task it learned once the person approves it.

The browsing session (tests/passive_session.py) is real: headless Chromium, the stand-in transport application, the
person's clicks and typing through Playwright, the agent's driver only watching. Nothing in the agent is told what a
trip, a status, a rule or a role is.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest
from playwright.sync_api import sync_playwright

from skyguard_agent.agent import OperationsAgent
from skyguard_agent.assistant import Assistant, canonical, is_question
from skyguard_agent.llm import CallableLLM
from skyguard_agent.understanding import Miner, derive_identity_pattern, field_label, path_template
from skyguard_agent.vault import MemoryBackend, Vault
from tests import transport_site
from tests.passive_session import browse, day


@pytest.fixture(scope="module")
def learned(tmp_path_factory):
    server, base = transport_site.start()
    transport_site.STATE.reset()
    agent = OperationsAgent(tmp_path_factory.mktemp("ops"), vault=Vault(MemoryBackend()))
    app = agent.kb.register_application("FleetOps", base + "/")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        stats = browse(agent, browser, app["id"], base)
        first = agent.understanding.digest(app["id"])
        yield {"agent": agent, "app": agent.kb.application(app["id"]), "base": base, "browser": browser,
               "stats": stats, "model": first["model"], "changes": first["changes"]}
        browser.close()
    agent.close()
    server.shutdown()


def flow(model: dict[str, Any], title: str) -> dict[str, Any]:
    return next(f for f in model["flows"] if f["title"] == title)


def ask(env: dict[str, Any], text: str) -> dict[str, Any]:
    return env["agent"].chat(text, app_id=env["app"]["id"], user="tester")["reply"]


# ---- what it worked out by itself ------------------------------------------------------------------------------------

def test_watching_recorded_pages_and_actions(learned):
    assert learned["stats"]["pages"] >= 25 and learned["stats"]["events"] >= 40
    assert learned["model"]["stats"]["sessions"] == 1


def test_screens_and_their_kinds(learned):
    kinds = {(s["path"], s["type"]) for s in learned["model"]["screens"]}
    assert ("/login", "sign-in") in kinds
    assert ("/trips/new", "form") in kinds
    assert ("/trips", "list") in kinds
    assert ("/trips/new", "refused") in kinds  # the Manager was refused the form
    trips = next(s for s in learned["model"]["screens"] if s["path"] == "/trips")
    assert trips["entity"] == "trip" and {"manager", "entry operator"} <= set(trips["roles"])
    assert {b["label"] for b in trips["row_actions"]} >= {"Approve", "Authorize"}


def test_create_flow_parameters_steps_and_verification(learned):
    f = flow(learned["model"], "Create trip")
    assert f["successes"] == 3 and f["failures"] == 3 and f["roles"] == ["entry operator"]
    names = [p["name"] for p in f["parameters"]]
    assert names == ["trip_no", "route", "departure_date", "departure_time", "bus", "driver"]  # form order
    types = {p["name"]: p["type"] for p in f["parameters"]}
    assert types["route"] == "select" and types["departure_date"] == "date" and types["departure_time"] == "time"
    assert f["steps"][0] == {"action": "navigate", "url": "/trips/new"}
    assert f["steps"][-1] == {"action": "click", "screen": "New trip", "target": "Save trip", "commit": True}
    assert f["verify"]["success_pattern"] == r"Trip\ {trip_no}\ created\ successfully\."
    assert f["exists_check"]["column"] == "Trip No" and f["exists_check"]["param"] == "trip_no"
    assert f["initial_status"] == "Planned"
    assert f["bookings_source"]["columns"] == {"bus": "Bus", "departure_date": "Date", "departure_time": "Time"}


def test_row_actions_on_existing_records(learned):
    a = flow(learned["model"], "Approve trip")
    assert a["kind"] == "row_action" and a["roles"] == ["manager"]
    assert a["steps"][1] == {"action": "open_row", "control": "Approve", "param": "trip_no", "commit": True}
    assert a["target"] == {"status_column": "Status", "requires_status": "Planned"}
    assert a["expected_status"] == "Approved"
    z = flow(learned["model"], "Authorize trip")
    assert z["target"]["requires_status"] == "Approved" and z["expected_status"] == "Authorized"
    assert z["roles"] == ["second-level manager"]


def test_status_lifecycle(learned):
    lc = next(x for x in learned["model"]["lifecycles"] if x["entity"] == "trip")
    assert lc["statuses"] == ["Planned", "Approved", "Authorized"]
    assert lc["initial"] == {"status": "Planned", "action": "Create trip", "role": "entry operator"}
    t = {(x["from"], x["to"]): x for x in lc["transitions"]}
    assert t[("Planned", "Approved")]["action"] == "Approve trip" and t[("Planned", "Approved")]["roles"] == ["manager"]
    assert t[("Approved", "Authorized")]["action"] == "Authorize trip"


def test_business_rules_from_the_applications_own_messages(learned):
    rules = {r["key"]: r for r in learned["model"]["rules"]}
    unique = rules["Create trip: unique trip_no"]
    assert unique["source"] == "application message" and "already exists" in unique["evidence"][0]
    overlap = rules["Create trip: no_overlap bus"]["rule"]
    assert overlap["type"] == "no_overlap" and overlap["date"] == "departure_date" and overlap["time"] == "departure_time"
    assert overlap["duration_minutes"] > 60  # BUS-101 was refused one hour after its first trip
    past = rules["Create trip: date departure_date"]["rule"]
    assert past["min_days"] == 0 and past["format"] == "YYYY-MM-DD"


def test_roles_identity_and_sign_in(learned):
    roles = {r["key"]: r for r in learned["model"]["roles"]}
    assert set(roles) == {"entry operator", "manager", "second-level manager"}
    assert roles["manager"]["users"] == ["manager"]
    assert any("cannot create trips" in x for x in roles["manager"]["refused"])
    assert roles["manager"]["offered_actions"] == ["Approve"]
    profile = learned["app"]["profile"]
    assert profile["login"]["username"] == "User ID" and profile["login"]["password"] == "Password"
    assert profile["login"]["url"].endswith("/login") and profile["login"]["url"].startswith("http")
    assert "(?P<user>" in profile["identity"]["pattern"] and "(?P<role>" in profile["identity"]["pattern"]


def test_findings_become_knowledge_awaiting_a_person(learned):
    kb, app_id = learned["agent"].kb, learned["app"]["id"]
    wf = {e["key"]: e for e in kb.items(app_id, "workflow")}
    assert wf["Create trip"]["state"] == "awaiting_approval" and wf["Create trip"]["source"] == "passive_learning"
    assert wf["Authorize trip"]["state"] == "discovered"  # seen once only
    assert not kb.approved_items(app_id, "workflow")  # nothing learned can run before a person approves it
    states = {e["key"]: e["state"] for e in kb.items(app_id, "rule")}
    assert states["Create trip: unique trip_no"] == "discovered"
    assert states["Create trip: time departure_time"] == "ai_suggested"
    assert kb.items(app_id, "lifecycle")[0]["data"]["statuses"] == ["Planned", "Approved", "Authorized"]
    # analysing again with nothing new proposes nothing again
    assert learned["agent"].understanding.digest(app_id)["changes"] == []


def test_passwords_are_never_observed(learned):
    db = learned["agent"].store.path
    con = sqlite3.connect(str(db))
    rows = [r[0] for r in con.execute("SELECT data FROM observations").fetchall()]
    con.close()
    blob = "\n".join(rows)
    for user, (pw, _role) in transport_site.USERS.items():
        assert pw not in blob, user
    secret_fields = [f for r in rows for f in (json.loads(r).get("fields") or []) if f.get("secret")]
    assert secret_fields and all(f.get("value") is None for f in secret_fields)


# ---- chat understands -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("question, expect", [
    ("what have you learned?", ["Create trip", "Planned → Approved → Authorized", "Entry Operator"]),
    ("how do I create a trip?", ["Open New trip", "Choose Route", "Press Save trip", "Trip No must be unique"]),
    ("who can approve trips?", ["'Approve trip' is done by Manager"]),
    ("what happens after a trip is approved?", ["After Approved", "Authorized", "Authorize trip"]),
    ("what statuses can a trip have?", ["Planned → Approved → Authorized"]),
    ("which rules apply to the trip number?", ["Trip No must be unique"]),
    ("is a bus allowed to be double booked?", ["cannot be booked twice"]),
    ("what can a manager do?", ["Approve trip", "cannot create trips"]),
    ("what is the status of T100?", ["T100 was Authorized"]),
    ("how many trips are planned?", ["1 trip was Planned"]),
    ("what information do I need to add a trip?", ["Departure date", "Driver: one of"]),
    ("where is the trip list?", ["Trips (/trips, list)"]),
])
def test_chat_answers_from_what_it_saw(learned, question, expect):
    r = ask(learned, question)
    assert r["type"] == "answer", r
    for e in expect:
        assert e in r["text"], (question, r["text"])


def test_unknown_shapes_fall_back_to_retrieved_facts_and_the_model_sees_them_as_data(learned):
    seen = {}

    def fake(messages, max_tokens=600, json_mode=False):
        seen["messages"] = messages
        return "Trips are created on the New trip screen by an Entry Operator."

    a = Assistant(learned["agent"].kb, learned["agent"].understanding, CallableLLM(fake))
    r = a.answer(learned["app"], "tell me something about drivers and buses")
    assert r["type"] == "answer" and r["topic"] == "retrieval" and r["facts"]
    assert "<untrusted_page_data>" in seen["messages"][1]["content"]
    plain = Assistant(learned["agent"].kb, learned["agent"].understanding, None).answer(
        learned["app"], "tell me something about drivers and buses")
    assert plain["text"].startswith("Here is what I learned")


def test_a_learned_task_needs_one_approval_then_runs_and_is_verified(learned):
    from tests.test_ops_agent_e2e import drive

    agent, app_id = learned["agent"], learned["app"]["id"]
    request = (f"add 1 trip T300 on {day(6)} at 10:00, route Dubai to Sharjah, bus BUS-102, driver Dina Yousef "
               "as Entry Operator")
    r = ask(learned, request)
    assert r["type"] == "needs_approval" and r["workflow"] == "Create trip"
    agent.kb.approve(r["entry_id"], by="tester")
    for e in agent.kb.items(app_id, "rule"):
        if e["state"] == "discovered":
            agent.kb.approve(e["id"], by="tester")
    acc = agent.kb.add_account(app_id, "Operator", "operator", role_key="Entry Operator")
    agent.kb.set_credential(acc["id"], transport_site.USERS["operator"][0], consent=True)
    r = ask(learned, request)
    assert r["type"] == "run", r
    seen = drive(agent, learned["browser"], r["run_id"])
    assert seen["previews"] == 1
    values = seen["preview"]["records"][0]["values"]
    assert values == {"trip_no": "T300", "route": "Dubai to Sharjah", "departure_date": day(6),
                      "departure_time": "10:00", "bus": "BUS-102", "driver": "Dina Yousef"}
    assert [i["status"] for i in seen["items"]] == ["confirmed_success"]
    assert any(t["trip_no"] == "T300" and t["status"] == "Planned" for t in transport_site.STATE.trips)
    # a double booking the application refused while it was watching is now caught before submitting
    r = ask(learned, f"add 1 trip T301 on {day(4)} at 08:30, route Dubai to Sharjah, bus BUS-103, driver Dina Yousef "
                     "as Entry Operator")
    seen = drive(agent, learned["browser"], r["run_id"], confirm=False)
    findings = seen["preview"]["records"][0]["findings"]
    assert any(f["type"] == "no_overlap" and f["enforced"] for f in findings), findings


# ---- the pieces on their own -----------------------------------------------------------------------------------------

def test_helpers():
    assert path_template("http://x/trips/T100/approve?x=1") == "/trips/{id}/approve"
    assert path_template("http://x/a/6f1c2b9e-1111-2222-3333-444455556666") == "/a/{id}"
    assert field_label({"name": "YYYY-MM-DD", "placeholder": "YYYY-MM-DD", "nearby": "Departure date"}) == "Departure date"
    assert field_label({"label": "Trip No", "nearby": "x"}) == "Trip No"
    pat = derive_identity_pattern("Hello, operator | Entry Operator | Logout", "operator")
    assert pat and "(?P<user>" in pat
    assert canonical("add two trips") == "create two trips"
    assert canonical("who authorises trips") == "who authorize trips"
    assert is_question("how do I create a trip") and is_question("trip statuses?")
    assert not is_question("could you create 3 trips on 2026-12-01")


def _page(url, heading, *, fields=(), buttons=(), messages=(), text="Signed in as ana (Agent)", **extra):
    return {"_t": "page", "url": url, "heading": heading, "title": heading, "text": text,
            "fields": [{"label": f, "tag": "input", "type": "text", "value": v} for f, v in fields],
            "buttons": [{"label": b} for b in buttons], "links": [], "tables": extra.get("tables", []),
            "messages": [{"kind": k, "text": t} for k, t in messages], "auth": {}}


def _ev(kind, url, label, value=None, tag="input", typ="text"):
    return {"_t": "event", "kind": kind, "url": url, "element": {"label": label, "tag": tag, "type": typ,
                                                                   "role": "button" if tag == "button" else None},
            "value": value, "secret": False}


def test_single_page_app_dialog_flow_and_non_commit_clicks():
    """A booking widget on one URL: '+' passenger buttons and a calendar do not end the task; 'Book now' does, and
    the confirmation text decides it succeeded."""
    url = "https://travel.example/booking"
    items = [_page(url, "Find a flight", fields=[("From", ""), ("To", ""), ("Departure", "")], buttons=["Book now"]),
             _ev("fill", url, "From", "DXB"), _ev("fill", url, "To", "LHR"),
             _ev("click", url, "Add passenger", tag="button", typ="button"),
             _page(url, "Find a flight", fields=[("From", "DXB"), ("To", "LHR"), ("Departure", "")], buttons=["Book now"]),
             _ev("fill", url, "Departure", "2026-12-24"),
             _ev("click", url, "Book now", tag="button", typ="submit"),
             _page(url, "Booking confirmed", messages=[("success", "Booking DXB-LHR confirmed")])]
    for i, it in enumerate(items):
        it["_id"], it["_session"] = i + 1, "s1"
    model = Miner({"name": "Travel"}).run(items)
    f = model["flows"][0]
    assert f["commit"] == "Book now" and f["successes"] == 1
    assert [p["field"] for p in f["parameters"]] == ["From", "To", "Departure"]
    assert {"action": "click", "screen": "Find a flight", "target": "Add passenger"} in f["steps"]
    assert model["roles"][0]["name"] == "Agent"


def test_api_routes(client):
    r = client.post("/api/ops/applications", json={"name": "FleetOps", "base_url": "http://127.0.0.1:9/"})
    app_id = r.json()["application"]["id"]
    v = client.get(f"/api/ops/understanding?app_id={app_id}").json()
    assert v["ok"] and v["model"] is None and v["journal"]["observations"] == 0
    assert client.get("/api/ops/watch").json()["sessions"] == []
    d = client.post(f"/api/ops/understanding/{app_id}/digest").json()
    assert d["ok"] and d["model"]["screens"] == []
    assert client.post(f"/api/ops/admin/journal-clear/{app_id}").json()["cleared"] == 0
