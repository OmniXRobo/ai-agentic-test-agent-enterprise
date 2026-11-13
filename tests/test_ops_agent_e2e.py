"""23.5.0: the Operations Agent end to end, in a real (headless) Chromium against the stand-in transport application.

Preview and confirmation, business rules before submitting, application rejections reported as confirmed failures,
verification in the application's own list, duplicate prevention on a second request, self-healing when the markup
changes, pause / manual action / resume when a control is missing, two accounts in one execution (Entry Operator
creates, Manager approves) with verified identity and role, a one-time code pause, and read-only discovery.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import sync_playwright

from skyguard_agent.agent import OperationsAgent
from skyguard_agent.drivers.playwright_driver import PlaywrightDriver
from skyguard_agent.vault import MemoryBackend, Vault
from tests import transport_site
from tests.ops_agent_fixtures import day, setup_fleetops


@pytest.fixture(scope="module")
def fleet():
    server, url = transport_site.start()
    yield url
    server.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture()
def env(fleet, browser, tmp_path):
    transport_site.STATE.reset()
    agent = OperationsAgent(tmp_path / "ops", vault=Vault(MemoryBackend()))
    setup = setup_fleetops(agent, fleet)
    yield agent, setup, fleet
    agent.close()


def drive(agent: OperationsAgent, browser: Any, run_id: str, *, confirm: bool = True,
          person=None, driver: PlaywrightDriver | None = None) -> dict[str, Any]:
    """Runs the execution in this thread; ``person`` acts in the browser while it is paused (manual intervention)."""
    drv = driver or PlaywrightDriver(browser)
    seen: dict[str, Any] = {"previews": 0, "pauses": []}

    def on_wait(d: PlaywrightDriver, run: dict[str, Any]) -> None:
        if run["status"] == "awaiting_confirmation":
            seen["previews"] += 1
            seen["preview"] = run["preview"]
            ready = run["preview"]["totals"].get("ready", 0)
            if confirm and ready:
                agent.engine.confirm(run["id"], run["preview_hash"], by="tester")
            else:  # the person cancels (or nothing in the preview could run)
                agent.engine.stop(run["id"], by="tester")
        elif run["status"] == "paused":
            pause = run["checkpoint"].get("pause") or {}
            seen["pauses"].append(pause.get("code"))
            if person and person(d, run, pause):
                agent.engine.resume(run["id"], by="tester", manual_events=list(d.manual_events))
            else:
                agent.engine.stop(run["id"], by="tester")

    try:
        done = drv.run(agent.engine, run_id, on_wait=on_wait, poll_s=0.1)
    finally:
        if driver is None:
            drv.close()
    seen["done"] = done
    seen["items"] = agent.engine.items(run_id)
    seen["run"] = agent.engine.run(run_id)
    return seen


def results(seen):
    return [(it["seq"], it["status"]) for it in seen["items"]]


def create_request(**extra):
    return {"application_id": None, **extra}


def test_batch_with_rules_rejections_and_verification(env, browser):
    agent, setup, _ = env
    d = day()
    out = agent.chat(f"create 4 trips on {d} from 06:00 every 60 minutes, routes: Dubai to Abu Dhabi, Abu Dhabi to "
                     "Dubai, buses: BUS-101, BUS-102, drivers: Ahmed Khan, Bilal Saeed, Ahmed Khan, Ahmed Khan, trip "
                     "numbers starting at DXB100 as Entry Operator (test)", user="tester")
    assert out["reply"]["type"] == "run", out["reply"]
    seen = drive(agent, browser, out["reply"]["run_id"])
    assert seen["previews"] >= 1
    statuses = dict(results(seen))
    # records 1 and 2 created and seen in the list; 3 blocked by the driver-overlap rule before submitting;
    # 4 refused by the application itself (bus already busy) and reported as such, not as a success
    assert statuses[1] == "confirmed_success" and statuses[2] == "confirmed_success"
    assert statuses[3] == "skipped"
    assert statuses[4] == "confirmed_failure"
    assert seen["run"]["status"] == "partial"
    assert [t["trip_no"] for t in transport_site.STATE.trips] == ["DXB100", "DXB101"]
    assert transport_site.STATE.posts == 2
    # every success has evidence from the application
    for it in seen["items"]:
        if it["status"] == "confirmed_success":
            assert it["result"].get("evidence") or it["result"].get("stages")


def test_nothing_runs_without_confirmation(env, browser):
    agent, setup, _ = env
    out = agent.chat(f"create 1 trip on {day()} at 07:00, route: Dubai to Sharjah, bus: BUS-103, driver: Carlos Diaz, "
                     "trip number DXB300 as Entry Operator (test)", user="tester")
    seen = drive(agent, browser, out["reply"]["run_id"], confirm=False)
    assert seen["previews"] >= 1
    assert transport_site.STATE.posts == 0 and not transport_site.STATE.trips
    with pytest.raises(Exception):
        agent.engine.confirm(out["reply"]["run_id"], "0" * 64, by="tester")


def test_repeated_request_does_not_create_duplicates(env, browser):
    agent, setup, _ = env
    text = (f"create 2 trips on {day()} from 08:00 every 240 minutes, route: Dubai to Sharjah, bus: BUS-103, driver: "
            "Carlos Diaz, trip numbers starting at DXB200 as Entry Operator (test)")
    first = drive(agent, browser, agent.chat(text, user="tester")["reply"]["run_id"])
    assert [s for _, s in results(first)] == ["confirmed_success", "confirmed_success"]
    second = drive(agent, browser, agent.chat(text, user="tester")["reply"]["run_id"])
    # the preview already shows both trips as existing in the application, so there is nothing to confirm
    preview = second["preview"]
    assert preview["totals"]["ready"] == 0
    assert all(any("exist" in f["message"] for f in r["findings"]) for r in preview["records"]), preview["records"]
    assert transport_site.STATE.posts == 2 and len(transport_site.STATE.trips) == 2


def test_self_healing_when_the_markup_changes(env, browser):
    agent, setup, fleet = env
    first = drive(agent, browser, agent.chat(
        f"create 1 trip on {day()} at 09:00, route: Dubai to Abu Dhabi, bus: BUS-101, driver: Dina Yousef, trip number "
        "DXB400 as Entry Operator (test)", user="tester")["reply"]["run_id"])
    assert results(first) == [(1, "confirmed_success")]
    transport_site.STATE.variant = 2  # new field names, a <div role=button> "Save Trip" instead of the input
    second = drive(agent, browser, agent.chat(
        f"create 1 trip on {day()} at 13:00, route: Abu Dhabi to Dubai, bus: BUS-102, driver: Dina Yousef, trip number "
        "DXB401 as Entry Operator (test)", user="tester")["reply"]["run_id"])
    assert results(second) == [(1, "confirmed_success")], [e["message"] for e in agent.engine.events(second["run"]["id"])]
    assert not second["pauses"]
    healed = [e for e in agent.engine.events(second["run"]["id"]) if e["kind"] == "heal"]
    assert healed, "the changed controls were found again by their fingerprints"
    assert transport_site.STATE.posts == 2


def test_missing_control_pauses_and_learns_from_the_person(env, browser):
    agent, setup, _ = env
    transport_site.STATE.variant = 3  # the save button is now a plain <span>Store</span>

    def person(drv, run, pause):
        if pause.get("code") not in ("control_not_found", "ambiguous"):
            return False
        drv.page.get_by_text("Store", exact=True).click()  # the person submits the form themselves
        drv.page.wait_for_load_state("domcontentloaded")
        drv.page.wait_for_timeout(300)
        return True

    seen = drive(agent, browser, agent.chat(
        f"create 1 trip on {day()} at 10:00, route: Dubai to Abu Dhabi, bus: BUS-103, driver: Bilal Saeed, trip number "
        "DXB500 as Entry Operator (test)", user="tester")["reply"]["run_id"], person=person)
    assert seen["pauses"] and seen["pauses"][0] in ("control_not_found", "ambiguous")
    assert results(seen) == [(1, "confirmed_success")]
    assert transport_site.STATE.posts == 1  # submitted once, by the person; the agent did not repeat it
    learned = agent.kb.locators(setup["app"]["id"])
    assert any(loc["label"] == "Save trip" for loc in learned)
    # next time the learned location is used without a pause
    nxt = drive(agent, browser, agent.chat(
        f"create 1 trip on {day()} at 16:00, route: Dubai to Abu Dhabi, bus: BUS-103, driver: Bilal Saeed, trip number "
        "DXB501 as Entry Operator (test)", user="tester")["reply"]["run_id"])
    assert results(nxt) == [(1, "confirmed_success")] and not nxt["pauses"]


def test_two_accounts_create_then_approve(env, browser):
    agent, setup, _ = env
    acc = setup["accounts"]
    run = agent.engine.create(app_id=setup["app"]["id"],
                              stages=[{"workflow": "Create trip", "account_id": acc["operator"]["id"]},
                                      {"workflow": "Approve trip", "account_id": acc["manager"]["id"]}],
                              records=[{"trip_no": "DXB600", "route": "Dubai to Sharjah", "date": day(), "time": "11:00",
                                        "bus": "BUS-101", "driver": "Carlos Diaz"}],
                              interface="test", by="tester")
    seen = drive(agent, browser, run["run"]["id"])
    assert results(seen) == [(1, "confirmed_success")], [e["message"] for e in agent.engine.events(run["run"]["id"])]
    trip = transport_site.STATE.trips[0]
    assert trip["status"] == "Approved" and trip["created_by"] == "operator"
    ids = [e for e in agent.engine.events(run["run"]["id"]) if e["kind"] == "session"]
    assert any("manager" in e["message"].lower() for e in ids)


def test_wrong_role_is_refused_before_anything_is_submitted(env, browser):
    agent, setup, _ = env
    acc = setup["accounts"]
    with pytest.raises(Exception):  # the Manager account's role is not one the Create trip workflow allows
        agent.engine.create(app_id=setup["app"]["id"],
                            stages=[{"workflow": "Create trip", "account_id": acc["manager"]["id"]}],
                            records=[{"trip_no": "DXB700", "route": "Dubai to Sharjah", "date": day(), "time": "11:00",
                                      "bus": "BUS-101", "driver": "Carlos Diaz"}], interface="test", by="tester")
    assert transport_site.STATE.posts == 0


def test_one_time_code_pauses_for_the_person(env, browser):
    agent, setup, _ = env

    def person(drv, run, pause):
        if pause.get("code") != "auth_otp":
            return False
        drv.page.locator("input[name=otp]").fill(transport_site.OTP_CODE)  # typed by the person, never stored
        drv.page.get_by_role("button", name="Verify").click()
        drv.page.wait_for_load_state("domcontentloaded")
        return True

    seen = drive(agent, browser, agent.chat(
        f"create 1 trip on {day()} at 12:00, route: Dubai to Abu Dhabi, bus: BUS-102, driver: Carlos Diaz, trip number "
        "DXB800 as OTP Operator (test)", user="tester")["reply"]["run_id"], person=person)
    assert "auth_otp" in seen["pauses"]
    assert results(seen) == [(1, "confirmed_success")]
    import json
    dump = json.dumps([seen["run"], agent.engine.events(seen["run"]["id"]), agent.audit.list(2000)])
    assert transport_site.OTP_CODE not in dump


def test_discovery_is_read_only(env, browser):
    agent, setup, _ = env
    view = agent.learning.start_discovery(setup["app"]["id"], account_id=setup["accounts"]["operator"]["id"],
                                          by="tester", interface="test", max_pages=6)
    seen = drive(agent, browser, view["run"]["id"])
    assert seen["run"]["status"] in ("completed", "partial"), seen["run"]["status"]
    assert transport_site.STATE.posts == 0 and not transport_site.STATE.trips
    screens = agent.kb.items(setup["app"]["id"], kind="screen")
    assert screens and all(s["state"] != "approved" for s in screens if s["source"] == "discovery")
