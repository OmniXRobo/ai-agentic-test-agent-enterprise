"""23.3.15: AI-Driven Automation no longer times out mapping the steps on a large element inventory.

The real ScriptStudio generation job runs against a stand-in TFS, a 290-element discovery on 5 pages and an AI server
simulated with httpx.MockTransport (no AI server needed)."""
import json
import os
import sys
import time
from pathlib import Path

import httpx
import pytest

APP = Path(os.environ.get("APP_DIR", Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(APP))

from qa_platform.automation.framework.focus import focus_inventory, keywords, known_selectors, case_keywords  # noqa: E402

BASE = "https://shop.test/"
STEPS_XML = (
    '<steps id="0" last="3">'
    '<step id="2" type="ValidateStep"><parameterizedString isformatted="true">Open the shopping cart and set the '
    'quantity of the product to the minimum quantity</parameterizedString><parameterizedString isformatted="true">'
    'The quantity is accepted</parameterizedString><description/></step>'
    '<step id="3" type="ValidateStep"><parameterizedString isformatted="true">Press Update cart</parameterizedString>'
    '<parameterizedString isformatted="true">The subtotal is recalculated</parameterizedString><description/></step>'
    '</steps>')


def discovery():
    pages = []
    names = {0: "home", 1: "products", 2: "cart", 3: "account", 4: "help"}
    for p in range(5):
        els = []
        for i in range(58):
            els.append({"selector": f"#{names[p]}-filler-{i}", "role": "link", "tag": "a",
                        "name": f"Footer item {i} {names[p]}"})
        if p == 2:
            els[10] = {"selector": "#qty-input", "role": "spinbutton", "tag": "input", "name": "Quantity"}
            els[11] = {"selector": "#update-cart", "role": "button", "tag": "button", "name": "Update cart"}
            els[12] = {"selector": "#subtotal", "role": "text", "tag": "span", "name": "Subtotal"}
        pages.append({"url": f"{BASE}{'' if p == 0 else names[p]}", "title": names[p].title(), "elements": els})
    return {"id": "DISC-1", "base_url": BASE, "pages": pages}


# ---- the focus on its own ------------------------------------------------------------------------


def _lines():
    from qa_platform.automation.framework.builder import Inventory
    return Inventory(discovery()).lines


def test_focus_keeps_every_page_and_the_matching_elements_only():
    words = case_keywords("Minimum quantity validation", [
        {"action": "Set the quantity to the minimum", "expected": "Accepted"},
        {"action": "Press Update cart", "expected": "The subtotal is recalculated"}], [])
    f = focus_inventory(_lines(), words, start_url=BASE, limit=60)
    assert f.total == 290 and f.pages == 5 and f.shown <= 60
    assert f.text.count("PAGE ") == 5
    for sel in ("#qty-input", "#update-cart", "#subtotal"):
        assert sel in f.text
    assert len(f.text) < len("\n".join(_lines())) / 3


def test_small_inventory_is_sent_whole_and_knowledge_selectors_come_first():
    lines = _lines()
    f = focus_inventory(lines, set(), limit=1000)
    assert f.shown == 290 and f.text == "\n".join(lines)
    known = "Step 1: Open the cart -> Cart / footer (#help-filler-57)\nPage Objects: CartPage"
    assert known_selectors(known) == ["#help-filler-57"]
    f = focus_inventory(lines, {"quantity"}, prefer=known_selectors(known), limit=5)
    assert "#help-filler-57" in f.text and "#qty-input" in f.text


def test_keywords_skip_filler_words_and_split_identifiers():
    assert keywords(["Verify the user clicks the AddToCart button"]) == {"add", "cart"}
    assert "quantity" in keywords(["#cart_quantity"])


# ---- the real generation job -----------------------------------------------------------------------


@pytest.fixture()
def rt(tmp_path, monkeypatch):
    monkeypatch.setenv("QA_HOME", str(tmp_path))
    from fastapi.testclient import TestClient
    from qa_platform.main import create_app
    from qa_platform.ai import gateway as gw
    monkeypatch.setattr(gw, "_sleep", lambda s: None)
    app = create_app()
    with TestClient(app):
        rt = app.state.runtime
        tfs = rt.studio.tfs
        monkeypatch.setattr(tfs, "detail", lambda i: {
            "id": 1427, "title": "Functional Testing - Minimum quantity validation passes when quantity is equal to "
                                 "minimum", "steps": STEPS_XML, "links": [], "iteration_path": "Shop\\Sprint 25"}
            if int(i) == 1427 else {"id": 1177, "title": "Customer manages product quantities", "description": "",
                                    "acceptance_criteria": "", "traceability": {"test_cases": [
                                        {"id": 1427, "title": "Minimum quantity"}]}})
        monkeypatch.setattr(tfs, "profile", lambda: {"project": "Shop"})
        monkeypatch.setattr(rt.studio.design, "list_test_data", lambda **k: [
            {"name": "minimum", "dataset": {"quantity": "1", "product": "Blue mug"}}])
        monkeypatch.setattr(rt.studio.automation.discovery, "latest_for", lambda *a, **k: discovery())
        monkeypatch.setattr(rt.studio.automation.discovery, "get", lambda *a, **k: discovery())
        rt.studio.knowledge = None
        yield rt


ANSWER = {"steps": [
    {"action": "goto", "value": BASE + "cart", "description": "Open the cart", "manual_step": 1},
    {"action": "fill", "selector": "#qty-input", "value": "{{DATA:quantity}}", "description": "Set the quantity",
     "manual_step": 1, "element_name": "quantity field"},
    {"action": "click", "selector": "#update-cart", "description": "Update the cart", "manual_step": 2,
     "element_name": "update cart button"},
    {"action": "expect_visible", "selector": "#subtotal", "description": "Subtotal shown", "manual_step": 2,
     "element_name": "subtotal"}],
    "test_data": {"quantity": "1"}, "covered_acceptance_criteria": [], "notes": []}


def run_job(rt, handler):
    rt.studio.ai.transport = httpx.MockTransport(handler)
    s = rt.settings.section("ai")
    rt.settings.update({"ai": {**s, "base_url": "http://127.0.0.1:8080/v1", "model": "local"}})
    from qa_platform.automation.framework.studio import JobRequest
    job = rt.studio.start_job(JobRequest(selections=[{"user_story_id": 1177, "test_case_ids": [1427]}],
                                         base_url=BASE))
    for _ in range(300):
        j = rt.studio.job(job)
        if j["status"] in ("COMPLETED", "FAILED", "STOPPED"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_mapping_call_is_small_has_the_generation_timeout_and_enforced_shape(rt):
    seen, timeouts = [], []

    def handler(req):
        if req.url.path.endswith("/props"):
            return httpx.Response(404)
        timeouts.append(req.extensions.get("timeout", {}).get("read"))
        body = json.loads(req.content)
        seen.append(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(ANSWER)},
                                                      "finish_reason": "stop"}]})

    j = run_job(rt, handler)
    item = j["items"][0] if "items" in j else j["stories"][0]["items"][0]
    log = "\n".join(item.get("log") or [])
    assert len(seen) == 1, log
    body = seen[0]
    prompt = body["messages"][1]["content"]
    assert "#qty-input" in prompt and "#update-cart" in prompt and "#subtotal" in prompt
    assert prompt.count("PAGE ") == 5
    assert prompt.count("-filler-") <= 57  # was 287
    assert body["max_tokens"] <= 1500  # was 6000
    assert body["response_format"].get("schema", {}).get("properties", {}).get("steps", {}).get("maxItems") == 10
    assert timeouts[0] == 600  # was 120
    assert "selectors on 5 page(s)" in log and "waiting up to 10 min" in log


def test_timeout_is_asked_again_with_fewer_elements(rt):
    calls = []

    def handler(req):
        if req.url.path.endswith("/props"):
            return httpx.Response(404)
        calls.append(json.loads(req.content))
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow", request=req)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(ANSWER)},
                                                      "finish_reason": "stop"}]})

    j = run_job(rt, handler)
    item = j["items"][0] if "items" in j else j["stories"][0]["items"][0]
    log = "\n".join(item.get("log") or [])
    assert len(calls) == 2, log
    first, second = (c["messages"][1]["content"].count("\n  ") for c in calls)
    assert second < first and second <= 25
    assert "Asking again with fewer elements" in log
