"""23.11.2: answers to "I still need: ..." (reported on Swag Labs: the learned checkout needs First Name, Last Name and
Zip/Postal Code; the answer "First Name; muhammad" was ignored and all three fields were asked again).

The answer is read field by field, in the person's own form, kept across turns, and only what is still missing is
asked again. Tested on the parser and end to end on the stand-in online store (real headless Chromium), up to the
order placed with the answered values.
"""

from __future__ import annotations

import pytest

from skyguard_agent.autopilot import field_aliases, parse_answer
from tests import shop_site
from tests.test_ai_agent_repair_231101 import browser, shop  # noqa: F401  (module fixtures)
from tests.test_ops_agent_e2e import drive

CHECKOUT = [{"label": "First Name", "name": "firstName"}, {"label": "Last Name", "name": "lastName"},
            {"label": "Zip/Postal Code", "name": "postalCode"}]


# ---- the parser --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("First Name; muhammad", {"First Name": "muhammad"}),          # the reported answer
    ("first name muhammad", {"First Name": "muhammad"}),
    ("First Name: Muhammad", {"First Name": "Muhammad"}),
    ("first name is Muhammad", {"First Name": "Muhammad"}),
    ("First name: Muhammad, last name Ali, zip 12345",
     {"First Name": "Muhammad", "Last Name": "Ali", "Zip/Postal Code": "12345"}),
    ("last name is Ali and postal code 54000", {"Last Name": "Ali", "Zip/Postal Code": "54000"}),
    ("zip code 75500", {"Zip/Postal Code": "75500"}),
    ("Muhammad; Ali; 12345", {"First Name": "Muhammad", "Last Name": "Ali", "Zip/Postal Code": "12345"}),
    ("pre pare it", {}),                                            # only pushes the request on: no value
    ("prepare it please", {}),
    ("muhammad ali", {}),                                           # two words for three fields: nothing is guessed
    ("my first name is Sara and my last name is Ahmed and the zip code is 54000",
     {"First Name": "Sara", "Last Name": "Ahmed", "Zip/Postal Code": "54000"}),
    ("first name Mary Ann, last name Lee", {"First Name": "Mary Ann", "Last Name": "Lee"}),
])
def test_answers_in_the_persons_own_form(text, expected):
    assert parse_answer(text, CHECKOUT) == (expected, [])


def test_one_asked_field_takes_the_whole_answer():
    assert parse_answer("muhammad", CHECKOUT[:1]) == ({"First Name": "muhammad"}, [])
    assert parse_answer("54000", CHECKOUT[2:]) == ({"Zip/Postal Code": "54000"}, [])
    assert parse_answer("go ahead", CHECKOUT[:1]) == ({}, [])


def test_a_value_named_for_another_field_is_never_taken_for_the_asked_one():
    # only First Name is asked, but the person names the last name (a correction): it goes to Last Name
    assert parse_answer("last name Ali", CHECKOUT[:1], CHECKOUT) == ({"Last Name": "Ali"}, [])
    assert parse_answer("muhammad", CHECKOUT[:1], CHECKOUT) == ({"First Name": "muhammad"}, [])


def test_a_list_field_takes_only_one_of_its_options():
    vehicle = [{"label": "Vehicle", "name": "vehicle", "options": ["BUS-101", "BUS-102", "VAN-7"]}]
    assert parse_answer("van-7", vehicle) == ({"Vehicle": "VAN-7"}, [])
    found, problems = parse_answer("Vehicle XYZ", vehicle)
    assert found == {} and "not one of the Vehicle values" in problems[0]


def test_field_aliases():
    assert {"zip/postal code", "zip", "postal code", "zip code"} <= set(field_aliases("Zip/Postal Code", "postalCode"))
    assert "odometer end" in field_aliases("Odometer end (km)")


# ---- end to end on the stand-in online store ---------------------------------------------------------------------

def test_partial_answers_are_kept_and_only_the_rest_is_asked(shop, browser):  # noqa: F811
    agent, pilot, app = shop["agent"], shop["pilot"], shop["app"]

    def say(text):
        return pilot.command(text, conversation="missing-info", user="tester", app_id=app["id"])["reply"]

    r = say("checkout an order")
    assert r["type"] == "clarify" and set(r["missing"]) == {"First Name", "Last Name", "Zip/Postal Code"}, r

    r = say("First Name; muhammad")                                 # the reported answer
    assert r["type"] == "clarify" and set(r["missing"]) == {"Last Name", "Zip/Postal Code"}, r
    assert r["text"].startswith("Got it: First Name = muhammad.") and r["received"] == {"First Name": "muhammad"}

    r = say("pre pare it")                                          # nothing new: only what is still missing
    assert r["type"] == "clarify" and set(r["missing"]) == {"Last Name", "Zip/Postal Code"}, r

    r = say("last name Ali")
    assert r["type"] == "clarify" and r["missing"] == ["Zip/Postal Code"], r

    for acc in agent.kb.accounts(app["id"]):                        # the shopper's saved password (secure form)
        if acc["login_id"] == "shopper":
            agent.kb.set_credential(acc["id"], shop_site.USERS["shopper"], consent=True)
    before = len(shop_site.STATE.orders)
    r = say("54000")                                                # only one field asked: the value alone
    assert r["type"] == "run", r
    seen = drive(agent, browser, r["run_id"])
    values = seen["preview"]["records"][0]["values"]
    assert set(values.values()) >= {"muhammad", "Ali", "54000"}, values
    assert [i["status"] for i in seen["items"]] == ["confirmed_success"], (seen["run"]["status"], seen["preview"].get("totals"), seen["preview"]["records"][0], seen["pauses"], seen["items"][0].get("result"))
    assert len(shop_site.STATE.orders) == before + 1


def test_answers_are_not_offered_as_suggestions(shop):  # noqa: F811
    pilot, app = shop["pilot"], shop["app"]
    texts = [s["text"] for s in pilot.suggestions(app["id"], conversation="missing-info")]
    assert "First Name; muhammad" not in texts and "pre pare it" not in texts and "54000" not in texts, texts


def test_a_new_request_still_replaces_the_question(shop):  # noqa: F811
    pilot, app = shop["pilot"], shop["app"]
    first = pilot.command("checkout an order", conversation="replace", user="t", app_id=app["id"])["reply"]
    assert first["type"] == "clarify"
    pilot.command("first name Sara", conversation="replace", user="t", app_id=app["id"])
    nav = pilot.command("navigate to online store", conversation="replace", user="t", app_id=app["id"])["reply"]
    assert nav["type"] == "navigate"
    again = pilot.command("checkout an order", conversation="replace", user="t", app_id=app["id"])["reply"]
    assert set(again["missing"]) == {"First Name", "Last Name", "Zip/Postal Code"}  # a new request starts fresh


# ---- applications that never display the signed-in user (Swag Labs) -------------------------------------------------

def _authz(session, roles=(), mode="testing"):
    from skyguard_agent import authz
    wf = {"id": "w1", "state": "approved", "data": {"roles": list(roles)}}
    return authz.check(plan={}, stage={"account_id": "a1", "workflow_entry_id": "w1"}, session=session, workflow=wf,
                       run={"confirmed_at": __import__("datetime").datetime.now().astimezone().isoformat(),
                            "preview_hash": "h", "confirmed_hash": "h"}, record={}, mode=mode)


def test_a_confirmed_sign_in_is_enough_only_for_unrestricted_operations():
    confirmed = {"account_id": "a1", "verified": False, "sign_in_confirmed": True}
    assert _authz(confirmed) == []
    assert any("role" in p for p in _authz(confirmed, roles=["approver"]))           # roles still need verification
    assert any("identity" in p for p in _authz(confirmed, roles=["approver"]))
    assert any("identity" in p for p in _authz(confirmed, mode="production"))
    assert any("identity" in p for p in _authz({"account_id": "a1", "verified": False}))  # nothing confirmed
    assert any("not the account" in p for p in _authz({**confirmed, "account_id": "other"}))
