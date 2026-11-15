"""23.7.2: AI-Driven Automation writes every Test Case step, from the sign-in to the last step.

Before 23.7.2 the whole Test Case went to the AI in one request; a small local model (Qwen2.5-coder 3B) answered
with only "open the start page", the answer was saved as it was, and the script for a 6-step purchase was one
``open()`` call. These tests run the real generation job (TFS, discovery and the AI server are stand-ins) against a
local copy of the saucedemo.com shop (tests/site/saucedemo: same ids, data-test attributes, pages and texts), with the
AI answering exactly that one-step mapping, then run the generated scripts with pytest + Playwright.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from qa_platform.automation.framework.builder import CaseContext, FrameworkBuilder, Inventory, MappedTest
from qa_platform.automation.framework.registry import Registry
from qa_platform.automation.framework.steps import extract_precondition, plan_action, plan_precondition, plan_steps

TC1532 = [("Navigate to the online store", "User is redirected to the SauceDemo website"),
          ("Log in", "User is logged in successfully"),
          ("Browse products", "User can browse products and view product details"),
          ("Add products to the cart", "Products are added to the cart"),
          ("Review the order", "User can review the order and proceed to checkout"),
          ("Complete checkout", "User successfully completes the checkout process")]
TC1523 = [("Enter 'John Doe' in the First Name field.", "The First Name field shows 'John Doe'."),
          ("Enter 'Doe' in the Last Name field.", "The Last Name field shows 'Doe'."),
          ("Enter '123 Main St' in the Address field.", "The Address field shows '123 Main St'."),
          ("Enter 'Anytown' in the City field.", "The City field shows 'Anytown'."),
          ("Enter 'CA' in the State field.", "The State field shows 'CA'."),
          ("Enter '12345' in the Postal Code field.", "The Postal Code field shows '12345'."),
          ("Enter 'john.doe@example.com' in the Email field.", "The Email field shows 'john.doe@example.com'."),
          ("Enter '555-555-5555' in the Phone field.", "The Phone field shows '555-555-5555'.")]
DESC1523 = ("<div>User Story: US118 Customer completes an online purchase</div><div>Objective: Verify that a customer "
            "can enter the required checkout information on the checkout page.</div><div>Precondition: The customer is "
            "logged in and is on the checkout page.</div><div>Priority / risk: P2 / medium</div>")
# What the 3B model answered for TC1532 (the script the user received had only this step).
ONE_STEP = {"steps": [{"action": "goto", "value": "https://www.saucedemo.com/", "description": "Navigate to the online "
                       "store", "manual_step": 1}], "test_data": {}, "covered_acceptance_criteria": [], "notes": []}


def xml(steps):
    body = "".join(f'<step id="{n}" type="ValidateStep"><parameterizedString isformatted="true">{a}'
                   f'</parameterizedString><parameterizedString isformatted="true">{e}</parameterizedString>'
                   f'<description/></step>' for n, (a, e) in enumerate(steps, 2))
    return f'<steps id="0" last="{len(steps) + 1}">{body}</steps>'


# ---- reading the steps ----------------------------------------------------------------------------------------


def test_every_step_of_both_test_cases_is_understood():
    planned = plan_steps([{"action": a, "expected": e} for a, e in TC1532])
    assert [p.number for p in planned] == [1, 2, 3, 4, 5, 6]
    assert [p.intents[0].kind for p in planned] == ["goto", "login", "view", "add_to_cart", "open_cart", "complete"]
    assert [c.kind for p in planned for c in p.checks] == ["navigated", "navigated", "visible", "changed", "navigated",
                                                         "success"]
    planned = plan_steps([{"action": a, "expected": e} for a, e in TC1523], extract_precondition(DESC1523))
    assert planned[0].number == 0 and [i.kind for i in planned[0].intents] == ["goto", "login", "reach"]
    assert planned[0].intents[2].target == "checkout"
    assert [(p.intents[0].kind, p.intents[0].target, p.intents[0].value) for p in planned[1:3]] == [
        ("fill", "First Name", "John Doe"), ("fill", "Last Name", "Doe")]
    assert all(p.checks[0].kind == "value" for p in planned[1:])


@pytest.mark.parametrize("text,kind,target,value", [
    ("Click the Login button", "click", "Login", None),
    ("Enter valid username and password", "login", "", None),
    ('Select "Price (low to high)" from the sort dropdown', "select", "sort", "Price (low to high)"),
    ("Proceed to checkout", "reach", "checkout", None),
    ("Open the shopping cart", "open_cart", "cart", None),
    ('Fill First Name with "Ann"', "fill", "First Name", "Ann"),
    ("Add 'Sauce Labs Backpack' to the cart", "add_to_cart", "cart", "Sauce Labs Backpack"),
    ("Place the order", "complete", "order", None),
    ("Go to https://shop.example/login.", "goto", "", "https://shop.example/login"),
])
def test_step_phrasings(text, kind, target, value):
    intent = plan_action(text)[0]
    assert (intent.kind, intent.target, intent.value) == (kind, target, value)


def test_sentences_with_several_actions_are_left_for_the_ai():
    assert plan_action("Choose a region and a travel date a week ahead") == []
    assert plan_precondition("None") == [] and plan_precondition("The user is logged in.")[0].kind == "login"


def test_a_one_step_answer_can_no_longer_become_the_whole_script():
    """The builder keeps every Test Case step: a step the mapping left out is a flagged step, never dropped."""
    ctx = CaseContext(test_case_id="1532", test_case_title="User completes an online purchase",
                      steps=[{"action": a, "expected": e} for a, e in TC1532], base_url="https://www.saucedemo.com/")
    build = FrameworkBuilder(Registry(), Inventory({"pages": []}), lambda rel: "").build(
        ctx, MappedTest.model_validate(ONE_STEP))
    test = build.changes[build.test_rel]
    for n, (action, _) in enumerate(TC1532, start=1):
        assert f'with step("{action}"' in test and f"tc_step={n})" in test
    assert test.count("not_on_application(") == 5
    assert sum("is not automated" in n for n in build.notes) == 5


# ---- the real generation job -----------------------------------------------------------------------------------


@pytest.fixture()
def shop(rt, site):
    base = site.rstrip("/") + "/saucedemo/"
    tfs = rt.studio.tfs
    cases = {1532: ("Functional Testing - User completes an online purchase", TC1532, ""),
             1523: ("Functional Testing - Customer can enter the required checkout information", TC1523, DESC1523)}

    def detail(i):
        i = int(i)
        if i in cases:
            title, steps, desc = cases[i]
            return {"id": i, "title": title, "steps": xml(steps), "description": desc, "iteration_path": "online store\\Sprint 1",
                    "links": [{"id": 118, "relation": "Tests"}]}
        return {"id": 118, "title": "Customer completes an online purchase", "description": "", "acceptance_criteria": "",
                "traceability": {"acceptance_criteria": [{"id": 119, "title": "AC01 - Customer can log in using valid "
                                                          "credentials."}],
                                 "test_cases": [{"id": 1532, "title": cases[1532][0]},
                                                {"id": 1523, "title": cases[1523][0]}]}}

    tfs.detail = detail
    tfs.profile = lambda: {"project": "online store python"}
    rt.studio.design.list_test_data = lambda **k: []
    # Discovery saw only the sign-in page (everything else is behind it), like the user's inventory.
    login_page = {"url": base, "title": "Swag Labs", "elements": [
        {"selector": "#user-name", "tag": "input", "role": "textbox", "name": "Username", "placeholder": "Username",
         "input_type": "text", "test_id": "username", "element_id": "user-name"},
        {"selector": "#password", "tag": "input", "name": "Password", "placeholder": "Password", "input_type": "password",
         "test_id": "password", "element_id": "password"},
        {"selector": "#login-button", "tag": "input", "role": "button", "name": "Login", "input_type": "submit",
         "test_id": "login-button", "element_id": "login-button"}]}
    disc = {"id": "DISC-SAUCE", "base_url": base, "pages": [login_page]}
    rt.studio.automation.discovery.latest_for = lambda *a, **k: disc
    rt.studio.automation.discovery.get = lambda *a, **k: disc
    rt.studio.knowledge = None
    calls = []

    def handler(req):
        if req.url.path.endswith("/props"):
            return httpx.Response(404)
        calls.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(ONE_STEP)},
                                                      "finish_reason": "stop"}]})

    rt.studio.ai.transport = httpx.MockTransport(handler)
    s = rt.settings.section("ai")
    rt.settings.update({"ai": {**s, "base_url": "http://127.0.0.1:8080/v1", "model": "qwen2.5-coder-3b-instruct-q4_k_m.gguf"}})
    from qa_platform.automation.discovery import default_application
    rt.studio.creds.save(default_application(base), "user", "standard_user", "secret_sauce", None)
    return {"rt": rt, "base": base, "calls": calls}


def generate(shop, tc):
    from qa_platform.automation.framework.studio import JobRequest
    rt = shop["rt"]
    job = rt.studio.start_job(JobRequest(selections=[{"user_story_id": 118, "test_case_ids": [tc]}], base_url=shop["base"],
                                         on_existing="regenerate"))
    for _ in range(1200):
        j = rt.studio.job(job)
        if j["status"] in ("COMPLETED", "FAILED", "STOPPED"):
            item = j["items"][0] if "items" in j else j["stories"][0]["items"][0]
            assert item["status"] in ("GENERATED", "UPDATED"), "\n".join(item.get("log") or []) + str(item.get("error"))
            return item, rt.studio.script(item["script_id"])
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_tc1532_is_automated_from_login_to_the_order_confirmation_and_passes(shop):
    item, detail = generate(shop, 1532)
    log = "\n".join(item.get("log") or [])
    src = detail["source"]
    assert "6 of 6 Test Case step(s) automated" in log, log
    assert not shop["calls"], "every step was understood: no AI request needed"
    steps = [s["title"] for s in detail["flow_logic"]]
    assert steps == [a for a, _ in TC1532]
    assert [s["tc_step"] for s in detail["flow_logic"]] == [1, 2, 3, 4, 5, 6]
    for needed in ("login_page.login(credentials.username, credentials.password)", 'inventory_page.expect_url("inventory.html")',
                   "inventory_page.click_add_sauce_labs_backpack()", "inventory_page.click_shopping_cart()",
                   "cart_page.click_checkout()", "checkout_step_one_page.enter_checkout_information(",
                   "checkout_step_two_page.click_finish()", "checkout_complete_page.expect_complete_header_text("):
        assert needed in src, src
    assert "not_on_application" not in src
    ws = shop["rt"].studio.ws
    login = ws.read("page_objects/login_page.py")
    assert "def login(self, username, password):" in login and "class LoginPage(BasePage):" in login
    data = json.loads(ws.read(detail["script"]["data_file"]))
    values = data["combinations"][0]["values"]
    assert values["complete_header_expected"] == "Thank you for your order!"
    assert values["shopping_cart_badge_expected"] == "1"
    assert data["credentials"]["role"] == "user"

    result = shop["rt"].studio.run(item["script_id"], headless=True, background=False)
    assert result["status"] == "PASSED", (result.get("error"), result.get("steps"))
    assert [s["status"] for s in result["steps"]] == ["PASSED"] * 6


def test_tc1523_sets_up_its_precondition_and_flags_the_fields_saucedemo_does_not_have(shop):
    item, detail = generate(shop, 1523)
    log = "\n".join(item.get("log") or [])
    src = detail["source"]
    assert "Precondition: The customer is logged in and is on the checkout page" in log
    flow = detail["flow_logic"]
    assert flow[0]["title"] == "Precondition: The customer is logged in and is on the checkout page"
    for needed in ("login_page.login(", "inventory_page.click_add_sauce_labs_backpack()", "inventory_page.click_shopping_cart()",
                   "cart_page.click_checkout()", "checkout_step_one_page.expect_url('checkout-step-one.html')"):
        assert needed in "\n".join(flow[0]["code"]), flow[0]["code"]
    assert [s["tc_step"] for s in flow[1:]] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert 'checkout_step_one_page.enter_first_name(data["first_name"])' in src
    assert 'checkout_step_one_page.expect_first_name_value(data["first_name"])' in src
    assert 'checkout_step_one_page.enter_postal_code(data["postal_code"])' in src
    flagged = {s["tc_step"]: s for s in flow if any(a.startswith("NOT AUTOMATED") for a in s["actions"])}
    assert sorted(flagged) == [3, 4, 5, 7, 8]
    assert "no 'Address' field" in flagged[3]["actions"][0] and "First Name, Last Name, Zip/Postal Code" in flagged[3]["actions"][0]
    assert not shop["calls"], "the missing fields are not sent to the AI to be mapped onto some other field"
    data = json.loads(shop["rt"].studio.ws.read(detail["script"]["data_file"]))
    assert data["combinations"][0]["values"] == {"first_name": "John Doe", "last_name": "Doe", "postal_code": "12345"}

    result = shop["rt"].studio.run(item["script_id"], headless=True, background=False)
    assert result["status"] == "PASSED", (result.get("error"), result.get("steps"))
    statuses = [s["status"] for s in result["steps"]]
    assert statuses == ["PASSED", "PASSED", "PASSED", "NOT_AUTOMATED", "NOT_AUTOMATED", "NOT_AUTOMATED", "PASSED",
                        "NOT_AUTOMATED", "NOT_AUTOMATED"]


def test_without_credentials_the_login_is_flagged_with_what_to_do(shop):
    rt = shop["rt"]
    from qa_platform.automation.discovery import default_application
    rt.studio.creds.delete(rt.studio.creds.key(default_application(shop["base"]), "user"))
    item, detail = generate(shop, 1532)
    log = "\n".join(item.get("log") or [])
    assert "needs Application Credentials for role 'user'" in log
