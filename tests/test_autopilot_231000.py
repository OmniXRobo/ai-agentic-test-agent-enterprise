"""23.10.0: the simple AI agent. Connect by URL, learn while a person browses (real headless Chromium against the
stand-in transport application), trust what was verified, then plain-language commands: relative dates, names,
routes, counts in words, one question for what is missing, one preview, bulk row actions on a selection, roles.
"""

from __future__ import annotations

import datetime as dt

import pytest
from playwright.sync_api import sync_playwright

from skyguard_agent.agent import OperationsAgent
from skyguard_agent.autopilot import (Autopilot, capitalised_names, next_key, resolve_date_words, suggest_name,
                                      words_to_numbers)
from skyguard_agent.vault import MemoryBackend, Vault
from tests import transport_site
from tests.passive_session import browse

TODAY = dt.date.today()
TOMORROW = (TODAY + dt.timedelta(days=1)).isoformat()


# ---- pure helpers ------------------------------------------------------------------------------------------------

def test_suggest_name():
    assert suggest_name("http://fleetops.corp.local/login") == "Fleetops"
    assert suggest_name("https://www.transport-portal.intranet/") == "Transport Portal"
    assert suggest_name("http://10.1.2.3:8080/tms/") == "Tms (10.1.2.3 port 8080)"
    assert suggest_name("http://x/", "FleetOps - Sign in") == "FleetOps"
    assert suggest_name("http://fleet.local/", "Login") == "Fleet"


def test_relative_dates_and_numbers():
    monday = dt.date(2026, 11, 16)
    assert resolve_date_words("create two trips for tomorrow", monday)[1] == "2026-11-17"
    assert resolve_date_words("today's trips", monday)[1] == "2026-11-16"
    assert resolve_date_words("the day after tomorrow", monday)[1] == "2026-11-18"
    assert resolve_date_words("next monday", monday)[1] == "2026-11-23"
    assert resolve_date_words("on friday", monday)[1] == "2026-11-20"
    assert resolve_date_words("on 2026-12-01", monday)[1] == "2026-12-01"
    assert resolve_date_words("in 3 days", monday)[1] == "2026-11-19"
    assert words_to_numbers("create ten trips") == "create 10 trips"
    assert words_to_numbers("create two trips for driver John") == "create 2 trips for driver John"


def test_names_and_next_key():
    assert capitalised_names("Create Aslam's trip from Dubai to Abu Dhabi") == ["Aslam", "Dubai", "Abu", "Dhabi"]
    assert "John" in capitalised_names("Create two trips for tomorrow for driver John")
    assert next_key(["T100", "T101", "T102"]) == "T103"
    assert next_key(["DXB009", "DXB010"]) == "DXB011"
    assert next_key([]) is None


# ---- learning, then commands --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def env(tmp_path_factory):
    server, base = transport_site.start()
    transport_site.STATE.reset()
    agent = OperationsAgent(tmp_path_factory.mktemp("auto"), vault=Vault(MemoryBackend()))
    pilot = Autopilot(agent)
    connected = pilot.connect(base + "/")
    assert connected["created"]
    app = connected["application"]
    assert pilot.connect(base + "/trips")["application"]["id"] == app["id"]  # same site, same application
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        browse(agent, browser, app["id"], base)
        agent.understanding.digest(app["id"])
        trusted = pilot.trust(app["id"])
        yield {"agent": agent, "pilot": pilot, "app": app, "base": base, "browser": browser, "trusted": trusted}
        browser.close()
    agent.close()
    server.shutdown()


def say(env, text, conv="c1"):
    return env["pilot"].command(text, conversation=conv, user="tester", app_id=env["app"]["id"])["reply"]


def test_learning_is_trusted_without_approval_steps(env):
    promoted = {(p["kind"], p["key"]) for p in env["trusted"]["promoted"]}
    assert ("workflow", "Create trip") in promoted and ("workflow", "Approve trip") in promoted
    assert ("workflow", "Authorize trip") in promoted
    assert sorted(env["trusted"]["accounts"]) == ["manager", "manager2", "operator"]
    k = env["pilot"].knowledge(env["app"]["id"])
    tasks = {t["title"]: t for t in k["tasks"]}
    assert tasks["Create trip"]["certainty"] == "verified" and tasks["Create trip"]["usable"]
    assert tasks["Authorize trip"]["certainty"] == "uncertain"  # seen once: usable, but labelled honestly
    assert tasks["Approve trip"]["consequential"] and not tasks["Create trip"]["consequential"]
    # AI guesses stay suggestions and are not enforced
    assert all(r["state"] != "Approved" for r in k["suggestions"])
    assert any("allowed_values" in (s["rule"] or "") or s for s in k["suggestions"])


def test_missing_password_is_asked_through_the_secure_form(env):
    r = say(env, "Create Ahmed's trip from Dubai to Abu Dhabi tomorrow at 14:00", conv="cred")
    assert r["type"] == "need_credential" and r["account"] == "operator"
    assert "Credential Manager" in r["text"]
    for user in ("operator", "manager", "manager2"):
        acc = next(a for a in env["agent"].kb.accounts(env["app"]["id"]) if a["login_id"] == user)
        env["agent"].kb.set_credential(acc["id"], transport_site.USERS[user][0], consent=True)
    r = say(env, "done", conv="cred")
    assert r["type"] == "run", r
    from tests.test_ops_agent_e2e import drive
    seen = drive(env["agent"], env["browser"], r["run_id"])
    v = seen["preview"]["records"][0]["values"]
    assert v["driver"] == "Ahmed Khan" and v["route"] == "Dubai to Abu Dhabi"
    assert v["departure_date"] == TOMORROW and v["departure_time"] == "14:00" and v["trip_no"] == "T103"
    assert [i["status"] for i in seen["items"]] == ["confirmed_success"]


def test_bulk_create_asks_once_for_what_is_missing(env):
    from tests.test_ops_agent_e2e import drive
    r = say(env, "Create two trips for tomorrow for driver Dina", conv="bulk")
    assert r["type"] == "clarify", r
    assert "Route" in r["text"] and "Departure time" in r["text"] and "Driver" not in r["missing"]
    r = say(env, "route Dubai to Sharjah, from 18:00 every 60 minutes", conv="bulk")
    assert r["type"] == "run", r
    assert r["consequential"] is False
    seen = drive(env["agent"], env["browser"], r["run_id"])
    assert seen["previews"] == 1  # one consolidated preview, not one per record
    recs = [x["values"] for x in seen["preview"]["records"]]
    assert [x["trip_no"] for x in recs] == ["T104", "T105"]
    assert {x["driver"] for x in recs} == {"Dina Yousef"} and {x["departure_date"] for x in recs} == {TOMORROW}
    assert [x["departure_time"] for x in recs] == ["18:00", "19:00"]
    assert len({x["bus"] for x in recs}) >= 1
    # partial failure: the application refuses the second trip (Dina is still driving the first one, a rule the
    # agent had not seen yet); the first stays done, the second is reported with the application's own reason
    assert [i["status"] for i in seen["items"]] == ["confirmed_success", "confirmed_failure"]
    assert "already assigned" in seen["items"][1]["result"]["reason"]
    assert seen["run"]["status"] == "partial"
    made = {t["trip_no"]: t for t in transport_site.STATE.trips}
    assert made["T104"]["driver"] == "Dina Yousef" and "T105" not in made


def test_show_pending_then_approve_the_selection_as_manager(env):
    from tests.test_ops_agent_e2e import drive
    r = say(env, "Show pending trips", conv="sel")
    assert r["type"] == "records" and r["status"] == "Planned"
    assert set(r["keys"]) == {"T102", "T103", "T104"}
    r = say(env, "Show pending trips for Dina", conv="sel")
    assert r["keys"] == ["T104"]
    r = say(env, "Approve the selected trips", conv="sel")
    assert r["type"] == "run", r
    assert r["consequential"] is True
    seen = drive(env["agent"], env["browser"], r["run_id"])
    assert seen["run"]["plan"]["stages"][0]["account"] == "manager"  # the role that was seen approving
    assert [i["status"] for i in seen["items"]] == ["confirmed_success"]
    made = {t["trip_no"]: t for t in transport_site.STATE.trips}
    assert made["T104"]["status"] == "Approved"
    assert made["T102"]["status"] == "Planned"


def test_authorize_all_approved_uses_live_status_and_the_right_role(env):
    from tests.test_ops_agent_e2e import drive
    r = say(env, "Authorize all approved trips", conv="auth")
    assert r["type"] == "run", r
    seen = drive(env["agent"], env["browser"], r["run_id"])
    keys = [x["values"]["trip_no"] for x in seen["preview"]["records"]]
    assert set(keys) == {"T101", "T104"}  # T101 seen approved while learning; T104 approved by the agent
    assert seen["run"]["plan"]["stages"][0]["account"] == "manager2"
    assert all(i["status"] == "confirmed_success" for i in seen["items"])
    made = {t["trip_no"]: t for t in transport_site.STATE.trips}
    assert all(made[k]["status"] == "Authorized" for k in keys)


def test_wrong_role_is_never_used(env):
    agent, app_id = env["agent"], env["app"]["id"]
    r = say(env, "Approve trip T102 as operator", conv="role")
    assert r["type"] == "error" and "role" in r["text"].lower()
    assert next(t for t in transport_site.STATE.trips if t["trip_no"] == "T102")["status"] == "Planned"
    assert agent  # nothing ran


def test_tasks_history_buckets(env):
    tasks = env["pilot"].tasks(env["app"]["id"])
    assert tasks and all("counts" in t for t in tasks)
    assert any(t["counts"].get("completed") == 2 for t in tasks)


def test_secrets_in_commands_are_withheld(env):
    r = say(env, "my password is Mgr#2026x please approve T102", conv="secret")
    assert r["type"] == "secret_intercepted"
    hist = env["agent"].history("secret")
    assert all("Mgr#2026x" not in h["text"] for h in hist)
