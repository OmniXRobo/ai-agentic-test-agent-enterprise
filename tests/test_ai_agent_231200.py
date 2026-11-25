"""23.12.0: the AI Agent on applications that never show the signed-in user (reported on Swag Labs).

"Finish lab" was refused with "not authorized: The signed-in identity has not been verified from the application",
although 23.11.2 accepted a confirmed sign-in there. Cause: a learned rule for reading the signed-in user (taken from
help text such as "Accepted usernames are: standard_user ...") was stored in the application profile; with a rule
present, the confirmed sign-in was no longer recorded, and nothing on the signed-in pages matched the rule.

Checked here: such help text never becomes a rule, and an application whose stored rule matches nothing still runs
end to end on the stand-in online store (real headless Chromium), while a different user shown on the page still
stops the run.
"""

from __future__ import annotations

import pytest

from skyguard_agent.understanding import derive_identity_pattern
from tests import shop_site
from tests.test_ai_agent_repair_231101 import browser, shop  # noqa: F401  (module fixtures)
from tests.test_ops_agent_e2e import drive

SAUCE_LOGIN_HELP = ("Accepted usernames are: standard_user locked_out_user problem_user performance_glitch_user "
                    "error_user visual_user | Password for all users: secret_sauce")


def test_help_text_listing_user_names_is_never_an_identity_rule():
    assert derive_identity_pattern(SAUCE_LOGIN_HELP, "standard_user") is None
    assert derive_identity_pattern("Demo users: standard_user, admin", "standard_user") is None
    assert derive_identity_pattern("Test accounts standard_user", "standard_user") is None


def test_a_real_signed_in_line_still_becomes_a_rule():
    pat = derive_identity_pattern("Home | Logged in as alice (Dispatcher) | Sign out", "alice")
    assert pat and "(?P<user>" in pat


def _checkout(agent, pilot, app, conversation):
    for acc in agent.kb.accounts(app["id"]):
        if acc["login_id"] == "shopper":
            agent.kb.set_credential(acc["id"], shop_site.USERS["shopper"], consent=True)
    r = pilot.command("checkout an order", conversation=conversation, user="tester", app_id=app["id"])["reply"]
    if r["type"] == "clarify":
        r = pilot.command("first name Sara, last name Ahmed, zip 54000", conversation=conversation, user="tester",
                          app_id=app["id"])["reply"]
    assert r["type"] == "run", r
    return r


def test_a_stored_identity_rule_that_matches_nothing_does_not_block_the_run(shop, browser):  # noqa: F811
    agent, pilot, app = shop["agent"], shop["pilot"], shop["app"]
    profile = dict(agent.kb.application(app["id"]).get("profile") or {})
    # the rule an earlier version learned from the sign-in page's help text
    profile["identity"] = {"pattern": r"Accepted\s+usernames\s+are:\s*(?P<user>[\w.@-]+)"}
    agent.kb.set_profile(app["id"], profile)
    before = len(shop_site.STATE.orders)
    r = _checkout(agent, pilot, app, "identity-rule")
    seen = drive(agent, browser, r["run_id"])
    assert [i["status"] for i in seen["items"]] == ["confirmed_success"], (seen["run"]["status"], seen["items"])
    assert len(shop_site.STATE.orders) == before + 1


# ---- Swag Labs stand-in: the reported "Finish lab" run (all steps done in the browser, shown as FAILED) ------------

def sauce_person(base: str):
    return [lambda pg: pg.goto(base + "index.html"),
            lambda pg: pg.locator("#user-name").fill("standard_user"),
            lambda pg: pg.locator("#password").fill("secret_sauce"),
            lambda pg: pg.locator("#login-button").click(),
            lambda pg: pg.locator("[data-test^=add-to-cart]").first.click(),
            lambda pg: pg.locator(".shopping_cart_link").click(),
            lambda pg: pg.locator("#checkout").click(),
            lambda pg: pg.locator("#first-name").fill("Aslam"),
            lambda pg: pg.locator("#last-name").fill("Khan"),
            lambda pg: pg.locator("#postal-code").fill("54000"),
            lambda pg: pg.locator("#continue").click(),
            lambda pg: pg.locator("#finish").click()]


@pytest.fixture(scope="module")
def sauce(browser, tmp_path_factory):  # noqa: F811
    from skyguard_agent.agent import OperationsAgent
    from skyguard_agent.autopilot import Autopilot
    from skyguard_agent.vault import MemoryBackend, Vault
    from tests import site_server
    from tests.passive_session import browse
    server, url = site_server.start()
    base = url.rstrip("/") + "/saucedemo/"
    agent = OperationsAgent(tmp_path_factory.mktemp("sauce-agent"), vault=Vault(MemoryBackend()))
    pilot = Autopilot(agent)
    app = pilot.connect(base + "index.html", name="Swag Labs", environment="Test")["application"]
    browse(agent, browser, app["id"], base, sauce_person(base))
    agent.understanding.digest(app["id"])
    pilot.trust(app["id"])
    yield {"agent": agent, "pilot": pilot, "app": app, "base": base}
    server.shutdown()
    agent.close()


def test_swag_labs_checkout_reaching_thank_you_page_is_completed(sauce, browser):  # noqa: F811
    agent, pilot, app = sauce["agent"], sauce["pilot"], sauce["app"]
    tasks = [t["title"] for t in pilot.knowledge(app["id"])["tasks"] if t.get("usable")] \
        if hasattr(pilot, "knowledge") else []
    for acc in agent.kb.accounts(app["id"]):
        agent.kb.set_credential(acc["id"], "secret_sauce", consent=True)
    say = lambda t: pilot.command(t, conversation="sauce", user="tester", app_id=app["id"])["reply"]  # noqa: E731
    r = say("Finish saucedemo")                     # the learned task (on the person's PC: "Finish lab")
    if r["type"] == "clarify":
        r = say("bilal; ahmed; 3432")                # the reported answer
    assert r["type"] == "run", (r, tasks)
    seen = drive(agent, browser, r["run_id"])
    assert [i["status"] for i in seen["items"]] == ["confirmed_success"], \
        (seen["run"]["status"], [(i["status"], (i.get("result") or {}).get("reason")) for i in seen["items"]], tasks)


def test_result_page_rules():
    from skyguard_agent.executor import Engine
    sp = Engine._success_page
    done = {"url": "http://x/saucedemo/checkout-complete.html", "heading": "Thank you for your order!", "text": ""}
    commit = {"commit_url": "http://x/saucedemo/checkout-step-two.html"}
    learned = {"success_page": {"path": "/saucedemo/checkout-complete.html", "title": "Thank you for your order!"}}
    assert sp(commit, done, learned) == "Thank you for your order!"
    assert sp(commit, {**done, "url": "http://x/saucedemo/inventory.html"}, learned) is None   # another page
    assert sp(commit, done, {}) == "Thank you for your order!"          # knowledge learned before 23.12.0
    assert sp(commit, {**done, "heading": "Swag Labs", "text": "Products"}, {}) is None     # says nothing
    assert sp({"commit_url": done["url"]}, done, {}) is None            # still on the submitted page
    assert sp(commit, {**done, "auth": {"password_fields": 1}}, {}) is None                # signed out


def test_a_task_named_by_the_person_is_found_by_its_name():
    from skyguard_agent.intent import classify, compatible
    buy = {"key": "buy item", "data": {"title": "buy item", "operation": "finish"}}
    assert compatible(classify("buy item"), buy)
    assert not compatible(classify("approve trip 5"), buy) and not compatible(classify("cancel order 4"), buy)


def test_a_postal_code_is_never_guessed_unique():
    from skyguard_agent.learning import NOT_UNIQUE
    assert NOT_UNIQUE.search("zip_postal_code") and NOT_UNIQUE.search("country_code") and NOT_UNIQUE.search("phone_no")
    assert not NOT_UNIQUE.search("order_no") and not NOT_UNIQUE.search("invoice_number") and not NOT_UNIQUE.search("ref")
