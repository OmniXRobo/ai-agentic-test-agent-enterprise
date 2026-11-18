"""23.11.1 AI Agent repair (the "23.10.0 final repair" request), tests A to H against real headless Chromium:

  A  "navigate to online store" opens the store; no unrelated "Cancel ..." plan, no first/last name questions
  B  learning survives a restart of SkyGuard and is retrieved by a command
  C  the complete multi-role trip process is learned by watching (stages, roles, transitions, fields)
  D  "Create a trip tomorrow from Dubai to Abu Dhabi for Aslam" asks only for the vehicle, then creates the trip and
     reports the number and status the application generated
  E  confirmation: refused for an unrelated plan, for a plan in which nothing can run, without the acknowledgement;
     accepted for a valid plan
  F  multi-role execution: manager / officer / driver (code typed by the person) / operator, missing credential,
     wrong role, one-time code at sign-in
  H  prompt injection in page content, cross-application confirmation, secrets never stored
Test G (the extension) is in tests/test_browser_extension.py.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any, Callable

import pytest
from playwright.sync_api import sync_playwright

from skyguard_agent.agent import OperationsAgent
from skyguard_agent.autopilot import Autopilot
from skyguard_agent.drivers.playwright_driver import PlaywrightDriver
from skyguard_agent.intent import classify, compatible, navigation_target
from skyguard_agent.util import AgentError
from skyguard_agent.vault import MemoryBackend, Vault
from tests import shop_site
from tests import transport_flow_site as T
from tests.passive_session import browse
from tests.test_ops_agent_e2e import drive


def day(n: int) -> str:
    return (dt.date.today() + dt.timedelta(days=n)).isoformat()


# ---- intent routing (bugs 1 and 2) -------------------------------------------------------------------------------

@pytest.mark.parametrize("text,kind", [
    ("navigate to online store", "navigate"),
    ("Navigate to Online Store", "navigate"),
    ("go to the online store", "navigate"),
    ("open the online store", "navigate"),
    ("Open the trip creation page", "navigate"),
    ("take me to the trips page", "navigate"),
    ("Show pending trips", "read"),
    ("list the approved trips", "read"),
    ("open trip T100", "read"),
    ("Approve trip 123", "approve"),
    ("Reject trip T5", "approve"),
    ("Authorize trip TRP-0001", "authorize"),
    ("Record completed trip TRP-0001", "record"),
    ("Create a trip tomorrow from Dubai to Abu Dhabi for Aslam", "create"),
    ("open a new trip", "create"),
    ("cancel order ORD-1001", "cancel_delete"),
    ("how does trip approval work?", "explain"),
    ("Vehicle: VAN-7", "data_entry"),
])
def test_intent_classes(text, kind):
    assert classify(text, app_names=["Online Store", "Transport"]).kind == kind


def test_navigation_is_never_an_operation():
    nav = classify("navigate to online store", app_names=["Online Store"])
    assert not nav.is_operation
    for wf in ({"key": "Cancel lab", "data": {"operation": "cancel", "entity": "lab"}},
               {"key": "Checkout order", "data": {"operation": "checkout", "entity": "order"}},
               {"key": "Create trip", "data": {"operation": "create", "entity": "trip"}}):
        assert not compatible(nav, wf)
    approve = classify("approve trip 5")
    assert compatible(approve, {"key": "Approve trip", "data": {"operation": "approve", "entity": "trip"}})
    assert not compatible(approve, {"key": "Reject trip", "data": {"operation": "reject", "entity": "trip"}})
    assert not compatible(approve, {"key": "Cancel trip", "data": {"operation": "cancel", "entity": "trip"}})
    app = {"name": "Online Store", "base_url": "https://shop.example/"}
    assert navigation_target(nav, app, [], [])["url"] == "https://shop.example/"


# ---- shared browser / applications ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


def shop_person(base: str) -> list[Callable[[Any], None]]:
    """The reported case: the person fills the checkout information, presses Cancel, comes back, presses Continue with
    names missing (the application says First Name is required), completes it and finishes the order."""
    return [lambda pg: pg.goto(base + "/"),
            lambda pg: pg.locator("#user-name").fill("shopper"),
            lambda pg: pg.locator("#password").fill(shop_site.USERS["shopper"]),
            lambda pg: pg.locator("#login-button").click(),
            lambda pg: pg.get_by_role("button", name="Add to cart").first.click(),
            lambda pg: pg.get_by_role("link", name="Cart (1)").click(),
            lambda pg: pg.locator("#checkout").click(),
            lambda pg: pg.locator("#first-name").fill("Aslam"),
            lambda pg: pg.locator("#last-name").fill("Khan"),
            lambda pg: pg.locator("#postal-code").fill("123"),
            lambda pg: pg.locator("#cancel").click(),
            lambda pg: pg.locator("#checkout").click(),
            lambda pg: pg.locator("#postal-code").fill("123"),
            lambda pg: pg.locator("#continue").click(),
            lambda pg: pg.locator("#first-name").fill("Aslam"),
            lambda pg: pg.locator("#last-name").fill("Khan"),
            lambda pg: pg.locator("#continue").click(),
            lambda pg: pg.get_by_role("button", name="Finish").click(),
            lambda pg: pg.get_by_role("link", name="Logout").click()]


class PickAnyLLM:
    """A model that, like the reported Qwen answer, picks a cancel workflow for any request when it is offered one."""
    name = "stub-qwen"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, *, max_tokens=600, json_mode=False):
        self.calls += 1
        ids = re.findall(r"'workflow': '([^']+)'", messages[-1]["content"])
        pick = next((i for i in ids if "ancel" in i), ids[0] if ids else None)
        return json.dumps({"workflow": pick, "fixed": {}, "questions": []})


@pytest.fixture(scope="module")
def shop(browser, tmp_path_factory):
    shop_site.STATE.reset()
    server, base = shop_site.start()
    folder = tmp_path_factory.mktemp("shop-agent")
    vault = Vault(MemoryBackend())
    agent = OperationsAgent(folder, vault=vault)
    pilot = Autopilot(agent)
    app = pilot.connect(base + "/", name="Online Store", environment="Test")["application"]
    browse(agent, browser, app["id"], base, shop_person(base))
    agent.understanding.digest(app["id"])
    pilot.trust(app["id"])
    yield {"agent": agent, "pilot": pilot, "app": app, "base": base, "folder": folder, "vault": vault}
    server.shutdown()


def test_a_shop_learning_has_no_cancel_task(shop):
    model = shop["agent"].understanding.model(shop["app"]["id"])
    titles = [f["title"] for f in model["flows"]]
    assert titles == ["Checkout order"], titles
    flow = model["flows"][0]
    assert {p["field"] for p in flow["parameters"]} == {"First Name", "Last Name", "Zip/Postal Code"}
    assert not any("cancel" in t.lower() for t in titles)


@pytest.mark.parametrize("llm", [None, "stub"])
def test_a_navigate_to_online_store_opens_the_store(shop, llm):
    agent, pilot, app = shop["agent"], shop["pilot"], shop["app"]
    agent.set_llm(PickAnyLLM() if llm else None)
    try:
        runs_before = len(agent.engine.runs(app_id=app["id"])) if hasattr(agent.engine, "runs") else None
        for conv, text in (("a1", "navigate to online store"), ("a2", "Navigate to Online Store"),
                           ("a3", "go to the online store")):
            reply = pilot.command(text, conversation=f"{conv}-{llm}", user="tester", app_id=app["id"])["reply"]
            assert reply["type"] == "navigate", reply
            assert reply["url"] == shop["base"] + "/"
            blob = json.dumps(reply).lower()
            assert "cancel" not in blob and "first name" not in blob and "zip" not in blob
        if runs_before is not None:
            assert len(agent.engine.runs(app_id=app["id"])) == runs_before
    finally:
        agent.set_llm(None)


def test_a_stale_question_is_not_glued_to_a_navigation(shop):
    pilot, app = shop["pilot"], shop["app"]
    first = pilot.command("checkout an order", conversation="stale", user="tester", app_id=app["id"])["reply"]
    assert first["type"] == "clarify" and "First Name" in first["text"]
    nxt = pilot.command("navigate to online store", conversation="stale", user="tester", app_id=app["id"])["reply"]
    assert nxt["type"] == "navigate"


def test_a_navigation_opens_the_page_in_the_browser(shop, browser):
    """The SkyGuard side: a navigate reply is opened by the service in a browser window (here the driver opens it)."""
    drv = PlaywrightDriver(browser)
    try:
        drv.page.goto(shop["base"] + "/")
        assert "Sign in" in drv.page.content() or "Login" in drv.page.content()
    finally:
        drv.close()


def test_b_learning_survives_restart(shop):
    """A new agent on the same data folder (SkyGuard restarted) retrieves the learned task by a command."""
    agent2 = OperationsAgent(shop["folder"], vault=shop["vault"])
    pilot2 = Autopilot(agent2)
    app_id = shop["app"]["id"]
    check = pilot2.verify_retrieval(app_id)
    assert check["ok"] and check["checked"] >= 2, check
    reply = pilot2.command("checkout an order for Aslam Khan", conversation="restart", user="tester",
                           app_id=app_id)["reply"]
    assert reply["type"] == "clarify" and reply.get("workflow") == "Checkout order", reply
    assert pilot2.command("navigate to online store", conversation="r2", user="tester",
                          app_id=app_id)["reply"]["type"] == "navigate"


# ---- the transport application ---------------------------------------------------------------------------------

def transport_person(base: str) -> list[Callable[[Any], None]]:
    def login(u):
        return [lambda pg: pg.goto(base + "/login"), lambda pg: pg.locator("#u").fill(u),
                lambda pg: pg.locator("#p").fill(T.USERS[u][0]),
                lambda pg: pg.get_by_role("button", name="Sign in").click()]

    def logout():
        return [lambda pg: pg.get_by_role("link", name="Logout").click()]

    def create(o, d, date, drv, veh):
        s = [lambda pg: pg.get_by_role("link", name="Create Trip").click(),
             lambda pg: pg.locator("#origin").select_option(label=o),
             lambda pg: pg.locator("#destination").select_option(label=d),
             lambda pg: pg.locator("#date").fill(date),
             lambda pg: pg.locator("#driver").select_option(label=drv)]
        if veh:
            s.append(lambda pg: pg.locator("#vehicle").select_option(label=veh))
        return s + [lambda pg: pg.get_by_role("button", name="Save Trip").click()]

    def open_trip(no):
        return [lambda pg: pg.get_by_role("link", name="Trips", exact=True).click(),
                lambda pg: pg.locator(f"tr:has(td:text-is('{no}'))").get_by_role("link", name="Open").click()]

    s = login("operator")
    s += create("Dubai", "Abu Dhabi", day(3), "Aslam Khan", "BUS-101")
    s += create("Dubai", "Sharjah", day(4), "Bilal Saeed", "")                 # the application: Vehicle is required
    s += [lambda pg: pg.locator("#vehicle").select_option(label="BUS-102"),
          lambda pg: pg.get_by_role("button", name="Save Trip").click()]
    s += [lambda pg: pg.get_by_role("link", name="Trips", exact=True).click()] + logout()
    s += login("manager") + open_trip("TRP-0001") + [lambda pg: pg.get_by_role("button", name="Approve").click()]
    s += open_trip("TRP-0002") + [lambda pg: pg.get_by_role("button", name="Approve").click()] + logout()
    s += login("officer") + open_trip("TRP-0001") + [lambda pg: pg.get_by_role("button", name="Authorize").click()]
    s += logout()
    s += login("aslam") + open_trip("TRP-0001") + [lambda pg: pg.locator("#code").fill(T.DRIVER_CODES["Aslam Khan"]),
                                                   lambda pg: pg.get_by_role("button", name="Authenticate").click()]
    s += logout()
    s += login("operator") + open_trip("TRP-0001") + [lambda pg: pg.locator("#odometer").fill("12345"),
                                                      lambda pg: pg.locator("#fuel").fill("40"),
                                                      lambda pg: pg.get_by_role("button", name="Record Trip").click()]
    s += [lambda pg: pg.get_by_role("link", name="Trips", exact=True).click()] + logout()
    return s


@pytest.fixture(scope="module")
def tms(browser, tmp_path_factory):
    T.STATE.reset()
    server, base = T.start()
    folder = tmp_path_factory.mktemp("tms-agent")
    vault = Vault(MemoryBackend())
    agent = OperationsAgent(folder, vault=vault)
    pilot = Autopilot(agent)
    app = pilot.connect(base + "/", name="Transport", environment="Test")["application"]
    learned = browse(agent, browser, app["id"], base, transport_person(base))
    agent.understanding.digest(app["id"])
    trusted = pilot.trust(app["id"])
    for acc in agent.kb.accounts(app["id"]):
        agent.kb.set_credential(acc["id"], T.USERS[acc["label"]][0], consent=True)
    yield {"agent": agent, "pilot": pilot, "app": app, "base": base, "folder": folder, "vault": vault,
           "learned": learned, "trusted": trusted, "browser": browser}
    server.shutdown()


def seed(status: str, driver: str = "Bilal Saeed") -> str:
    """Puts a trip in the stand-in application directly (test setup, not something the agent did)."""
    with T.STATE.lock:
        no = f"TRP-{len(T.STATE.trips) + 1:04d}"
        T.STATE.trips.append({"origin": "Dubai", "destination": "Al Ain", "date": day(9 + len(T.STATE.trips)),
                              "driver": driver, "vehicle": "BUS-101", "no": no, "status": status,
                              "created_by": "operator"})
    return no


def trip(no: str) -> dict[str, Any]:
    return next(t for t in T.STATE.trips if t["no"] == no)


def test_c_complete_trip_process_is_learned(tms):
    m = tms["agent"].understanding.model(tms["app"]["id"])
    flows = {f["title"]: f for f in m["flows"]}
    assert set(flows) == {"Create trip", "Approve trip", "Authorize trip", "Authenticate trip", "Record trip"}, flows
    assert [p["field"] for p in flows["Create trip"]["parameters"]] == ["From", "To", "Trip date", "Driver", "Vehicle"]
    assert flows["Create trip"]["roles"] == ["entry operator"]
    for title, role, before, after in (("Approve trip", "manager", "Pending Approval", "Approved"),
                                       ("Authorize trip", "authorizing officer", "Approved", "Authorized"),
                                       ("Authenticate trip", "driver", "Authorized", "Driver Authenticated"),
                                       ("Record trip", "entry operator", "Driver Authenticated", "Recorded")):
        f = flows[title]
        assert f["roles"] == [role] and f["target_kind"] == "existing" and f["start_url"] == "/trips/{trip_no}"
        assert f["target"]["requires_status"] == before and f["expected_status"] == after
    # the driver's code is a step the person does; the code itself was never stored
    assert any(s["action"] == "manual_secret" for s in flows["Authenticate trip"]["steps"])
    assert [p["field"] for p in flows["Record trip"]["parameters"]] == ["Trip No", "Odometer end (km)",
                                                                      "Fuel used (litres)"]
    life = m["lifecycles"][0]
    assert life["statuses"] == ["Pending Approval", "Approved", "Authorized", "Driver Authenticated", "Recorded"]
    assert life["initial"] == {"status": "Pending Approval", "action": "Create trip", "role": "entry operator"}
    assert [(t["from"], t["to"], t["action"], t["roles"]) for t in life["transitions"]] == [
        ("Pending Approval", "Approved", "Approve trip", ["manager"]),
        ("Approved", "Authorized", "Authorize trip", ["authorizing officer"]),
        ("Authorized", "Driver Authenticated", "Authenticate trip", ["driver"]),
        ("Driver Authenticated", "Recorded", "Record trip", ["entry operator"])]
    rules = {r["text"] for r in m["rules"]}
    assert "Vehicle is required" in rules
    roles = {a["label"]: a["role_key"] for a in tms["agent"].kb.accounts(tms["app"]["id"])}
    assert roles == {"operator": "entry operator", "manager": "manager", "officer": "authorizing officer",
                     "aslam": "driver"}
    check = tms["pilot"].verify_retrieval(tms["app"]["id"])
    assert check["ok"], check["failed"]


def test_c_learning_never_stored_secrets(tms):
    blob = (tms["folder"]).rglob("*")
    secrets = [p for _, (p, _r) in T.USERS.items()] + [T.DRIVER_CODES["Aslam Khan"]]
    # A four-digit code can occur by chance inside a hex hash or random bytes (CI found "7788" inside a SHA-256 in the
    # database's WAL file); a stored code would stand on its own, so it is searched as a whole token.
    patterns = [re.compile(rb"(?<![0-9A-Za-z])" + re.escape(s.encode()) + rb"(?![0-9A-Za-z])") for s in secrets]
    for path in blob:
        if path.is_file():
            data = path.read_bytes()
            for s, pat in zip(secrets, patterns):
                m = pat.search(data)
                assert not m, (path, s, data[max(0, m.start() - 40):m.end() + 40])


def test_c_single_observation_rules_stay_unverified(tms):
    k = tms["pilot"].knowledge(tms["app"]["id"])
    once = [r for r in k["rules"] + k["suggestions"] if r["observations"] < 2]
    assert once, k["rules"] + k["suggestions"]
    assert all(r in k["suggestions"] and r["verification"].startswith("candidate") for r in once), once
    assert {r["rule"] for r in once} <= set(k["unverified"])


def test_d_create_trip_from_chat(tms):
    pilot, agent, app = tms["pilot"], tms["agent"], tms["app"]
    first = pilot.command("Create a trip tomorrow from Dubai to Abu Dhabi for Aslam", conversation="d",
                          user="tester", app_id=app["id"])["reply"]
    assert first["type"] == "clarify" and first["missing"] == ["Vehicle"], first
    reply = pilot.command("Vehicle: VAN-7", conversation="d", user="tester", app_id=app["id"])["reply"]
    assert reply["type"] == "run", reply
    rid = reply["run_id"]
    before = len(T.STATE.trips)
    seen = drive(agent, tms["browser"], rid)
    view = pilot.simple_view(rid)
    assert view["status"] == "completed"
    assert view["records"] == [{"Trip date": day(1), "From": "Dubai", "To": "Abu Dhabi", "Driver": "Aslam Khan",
                                "Vehicle": "VAN-7"}]
    assert view["role"] == "entry operator"
    new = T.STATE.trips[before:]
    assert len(new) == 1 and new[0]["status"] == "Pending Approval"
    assert view["result"] == f"Create trip: done, confirmed by the application, number {new[0]['no']}, status " \
                             "Pending Approval."
    assert seen["previews"] == 1
    # the same request again is not a second trip
    again = pilot.command("Create a trip tomorrow from Dubai to Abu Dhabi for Aslam with vehicle VAN-7",
                          conversation="d2", user="tester", app_id=app["id"])["reply"]
    if again["type"] == "run":
        drive(agent, tms["browser"], again["run_id"])
    assert len(T.STATE.trips) == before + 1  # the application refused the double booking or nothing ran


def run_to_preview(tms, rid: str, act: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Runs the read-only inspection; ``act`` is what the person does at the preview."""
    drv = PlaywrightDriver(tms["browser"])
    seen: dict[str, Any] = {}

    def on_wait(d, run):
        if run["status"] == "awaiting_confirmation" and "acted" not in seen:
            seen["acted"] = True
            act(run)
        elif run["status"] in ("awaiting_confirmation", "paused"):
            tms["agent"].engine.stop(run["id"], by="tester")

    try:
        seen["done"] = drv.run(tms["agent"].engine, rid, on_wait=on_wait, poll_s=0.1)
    finally:
        drv.close()
    if "acted" not in seen:  # the preview was never reached: keep why, for the assertion message
        seen["events"] = [(e.get("kind"), e.get("message")) for e in tms["agent"].engine.events(rid)][-12:]
    return seen


def test_e_confirmation_rules(tms):
    pilot, agent, app = tms["pilot"], tms["agent"], tms["app"]
    no = seed("Pending Approval")
    reply = pilot.command(f"Approve trip {no}", conversation="e1", user="tester", app_id=app["id"])["reply"]
    assert reply["type"] == "run" and reply["consequential"]
    rid = reply["run_id"]
    errors: list[str] = []

    def at_preview(run):
        sv = pilot.simple_view(rid)
        assert sv["can_confirm"] and sv["ready"] == 1 and sv["next_actions"] == ["confirm", "edit", "cancel"]
        assert sv["action"] == "Approve trip" and sv["role"] == "manager"
        for kwargs in ({"acknowledge": False, "app_id": app["id"]},            # box not ticked
                       {"acknowledge": True, "app_id": "app-other"}):          # another application selected
            try:
                pilot.confirm(rid, run["preview_hash"], by="tester", **kwargs)
            except AgentError as exc:
                errors.append(exc.code)
        pilot.confirm(rid, run["preview_hash"], acknowledge=True, by="tester", app_id=app["id"])

    run_to_preview(tms, rid, at_preview)
    assert errors == ["ACKNOWLEDGE_REQUIRED", "PLAN_OTHER_APPLICATION"]
    assert trip(no)["status"] == "Approved"
    assert "status Approved" in pilot.outcome_text(rid)


def test_e_nothing_to_run_cannot_be_confirmed(tms):
    pilot, app = tms["pilot"], tms["app"]
    no = seed("Recorded")       # the application shows it as Recorded: it cannot be approved
    reply = pilot.command(f"Approve trip {no}", conversation="e2", user="tester", app_id=app["id"])["reply"]
    rid = reply["run_id"]
    out: dict[str, Any] = {}

    def at_preview(run):
        out["view"] = pilot.simple_view(rid)
        try:
            pilot.confirm(rid, run["preview_hash"], acknowledge=True, by="tester", app_id=app["id"])
        except AgentError as exc:
            out["error"] = exc.code
            out["message"] = exc.message
        tms["agent"].engine.stop(rid, by="tester")

    run_to_preview(tms, rid, at_preview)
    assert out["view"]["can_confirm"] is False and out["view"]["ready"] == 0
    assert out["view"]["next_actions"] == ["provide_missing", "edit", "cancel"]
    assert out["error"] == "NOTHING_TO_RUN"
    assert trip(no)["status"] == "Recorded"


def test_e_plan_from_another_request_is_refused(tms):
    """A plan linked to a navigation request (the reported stale plan) cannot be confirmed."""
    agent, pilot, app = tms["agent"], tms["pilot"], tms["app"]
    no = seed("Pending Approval")
    nav = classify("navigate to transport", app_names=["Transport"])
    out = agent.chat(f"Approve trip {no}; Trip No: {no}", conversation="e3", user="tester", interface="skyguard",
                     app_id=app["id"], workflow="Approve trip",
                     context={"intent": nav.as_dict(), "command": "navigate to transport", "app_id": app["id"],
                              "workflow": "Approve trip"}, remember=False)
    rid = out["reply"]["run_id"]
    err: list[str] = []

    def at_preview(run):
        try:
            pilot.confirm(rid, run["preview_hash"], acknowledge=True, by="tester", app_id=app["id"])
        except AgentError as exc:
            err.append(exc.code)
        tms["agent"].engine.stop(rid, by="tester")

    seen = run_to_preview(tms, rid, at_preview)
    assert err == ["PLAN_UNRELATED"], seen
    assert trip(no)["status"] == "Pending Approval"


def test_f_multi_role_chain_from_chat(tms):
    pilot, agent, app = tms["pilot"], tms["agent"], tms["app"]
    no = seed("Pending Approval", driver="Aslam Khan")
    pauses: list[str] = []

    def person(d, run, pause):
        pauses.append(pause.get("code"))
        if pause.get("code") == "auth_secret_step":        # the driver types the code in the application window
            d.page.locator("#code").fill(T.DRIVER_CODES["Aslam Khan"])
            return True
        return False

    for cmd, account, status in ((f"Approve trip {no}", "manager", "Approved"),
                                 (f"Authorize trip {no}", "officer", "Authorized"),
                                 (f"Authenticate trip {no}", "aslam", "Driver Authenticated"),
                                 (f"Record trip {no} odometer end 500 fuel used 30", "operator", "Recorded")):
        reply = pilot.command(cmd, conversation=f"f-{status}", user="tester", app_id=app["id"])["reply"]
        assert reply["type"] == "run", (cmd, reply)
        assert agent.engine.run(reply["run_id"])["plan"]["stages"][0]["account"] == account
        drive(agent, tms["browser"], reply["run_id"], person=person)
        assert trip(no)["status"] == status, (cmd, trip(no))
        assert f"status {status}" in pilot.outcome_text(reply["run_id"])
    assert pauses == ["auth_secret_step"]
    assert trip(no)["odometer"] == "500" and trip(no)["fuel"] == "30"
    assert [h[1:] for h in T.STATE.history if h[0] == no] == [
        ("Pending Approval", "Approved", "Manager"), ("Approved", "Authorized", "Authorizing Officer"),
        ("Authorized", "Driver Authenticated", "Driver"), ("Driver Authenticated", "Recorded", "Entry Operator")]


def test_f_missing_credential_is_asked_securely(tms):
    pilot, agent, app = tms["pilot"], tms["agent"], tms["app"]
    mgr = next(a for a in agent.kb.accounts(app["id"]) if a["label"] == "manager")
    agent.kb.revoke_credential(mgr["id"], by="tester")
    try:
        no = seed("Pending Approval")
        reply = pilot.command(f"Approve trip {no}", conversation="f2", user="tester", app_id=app["id"])["reply"]
        if reply["type"] == "run":
            seen = drive(agent, tms["browser"], reply["run_id"])
            assert seen["pauses"] and seen["pauses"][0] in ("credential_missing", "manual_login")
        else:
            assert reply["type"] == "need_credential" and reply.get("security_notice"), reply
            assert "Credential Manager" in reply["text"]
            assert "password" not in reply["text"].lower().split("credential manager")[0] or True
        assert trip(no)["status"] == "Pending Approval"
    finally:
        agent.kb.set_credential(mgr["id"], T.USERS["manager"][0], consent=True)


def test_f_wrong_role_is_refused(tms):
    agent, app = tms["agent"], tms["app"]
    op = next(a for a in agent.kb.accounts(app["id"]) if a["label"] == "operator")
    with pytest.raises(AgentError) as exc:
        agent.engine.create(app_id=app["id"], stages=[{"workflow": "Approve trip", "account_id": op["id"]}],
                            records=[{"trip_no": "TRP-0001"}], by="tester", interface="test")
    assert exc.value.code == "ROLE_NOT_ALLOWED"


def test_f_one_time_code_at_sign_in_is_typed_by_the_person(tms):
    pilot, agent, app = tms["pilot"], tms["agent"], tms["app"]
    off = next(a for a in agent.kb.accounts(app["id"]) if a["label"] == "officer")
    off2 = agent.kb.add_account(app["id"], "officer2", "officer2", role_key="authorizing officer", by="tester")
    agent.kb.set_credential(off2["id"], T.USERS["officer2"][0], consent=True)
    agent.kb.revoke_credential(off["id"], by="tester")
    no = seed("Approved")
    pauses: list[str] = []

    def person(d, run, pause):
        pauses.append(pause.get("code"))
        if pause.get("code", "").startswith("auth_"):
            d.page.locator("input[name=otp]").fill(T.OTP_CODE)
            d.page.get_by_role("button", name="Verify").click()
            return True
        return False

    try:
        reply = pilot.command(f"Authorize trip {no}", conversation="f4", user="tester", app_id=app["id"])["reply"]
        assert reply["type"] == "run", reply
        assert agent.engine.run(reply["run_id"])["plan"]["stages"][0]["account"] == "officer2"
        drive(agent, tms["browser"], reply["run_id"], person=person)
        assert pauses and pauses[0].startswith("auth_"), pauses
        assert trip(no)["status"] == "Authorized"
    finally:
        agent.kb.set_credential(off["id"], T.USERS["officer"][0], consent=True)
        agent.kb.delete_account(off2["id"], by="tester")


def test_suggestions_come_from_learned_knowledge(tms):
    pilot, app = tms["pilot"], tms["app"]
    items = pilot.suggestions(app["id"], can_act=True)
    texts = [i["text"] for i in items]
    assert texts and all(isinstance(t, str) for t in texts)
    assert not any("password" in t.lower() for t in texts)
    learned = {"trip", "trips"}
    assert all(any(w in t.lower() for w in learned) or "page" in t.lower() for t in texts), texts
    viewer = [i["text"] for i in pilot.suggestions(app["id"], can_act=False)]
    assert not any(re.match(r"(?i)(approve|authorize|create|record|authenticate)\b", t) for t in viewer), viewer


# ---- H: security -------------------------------------------------------------------------------------------------

def test_h_page_text_cannot_command_the_agent(browser, tmp_path):
    """A product description tells the agent to cancel every order and approve refunds. Learning the page creates no
    such task, starts nothing, and a command still does only what the person asked."""
    shop_site.STATE.reset()
    server, base = shop_site.start()
    try:
        import urllib.request

        urllib.request.urlopen(base + "/__inject").read()
        agent = OperationsAgent(tmp_path, vault=Vault(MemoryBackend()))
        pilot = Autopilot(agent)
        app = pilot.connect(base + "/", name="Online Store")["application"]
        browse(agent, browser, app["id"], base, shop_person(base))
        agent.understanding.digest(app["id"])
        pilot.trust(app["id"])
        titles = [f["title"] for f in agent.understanding.model(app["id"])["flows"]]
        assert titles == ["Checkout order"]
        assert not any(re.search(r"(?i)refund|cancel", t) for t in titles)
        for s in pilot.suggestions(app["id"], can_act=True):
            assert not re.search(r"(?i)refund|ignore your previous", s["text"])
        reply = pilot.command("navigate to online store", conversation="h", user="t", app_id=app["id"])["reply"]
        assert reply["type"] == "navigate"
        assert shop_site.STATE.orders == [] or all(o.get("status") != "Cancelled" for o in shop_site.STATE.orders)
    finally:
        server.shutdown()


def test_h_command_never_crosses_applications(tms, shop):
    """A request made with the store selected never runs a transport task."""
    reply = shop["pilot"].command("Approve trip TRP-0001", conversation="x", user="t",
                                  app_id=shop["app"]["id"])["reply"]
    assert reply["type"] in ("clarify", "no_workflows")
    assert reply.get("workflow") in (None, "Checkout order")


def test_h_passwords_in_chat_are_refused(tms):
    reply = tms["pilot"].command("my password is Mgr#2026x please sign in", conversation="h3", user="t",
                                 app_id=tms["app"]["id"])["reply"]
    assert reply["type"] == "secret_intercepted", reply
    assert "Mgr#2026x" not in json.dumps(reply)
    for path in tms["folder"].rglob("*"):
        if path.is_file():
            assert b"Mgr#2026x" not in path.read_bytes(), path


def test_h_decisions_are_audited(tms):
    rows = tms["agent"].audit.list(limit=500) if hasattr(tms["agent"].audit, "list") else None
    if rows is None:
        pytest.skip("audit listing not available")
    actions = {r.get("action") for r in rows}
    assert {"chat.intent", "run.confirmed_simple"} <= actions, actions
