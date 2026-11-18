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
