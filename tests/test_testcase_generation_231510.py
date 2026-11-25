"""23.15.1: "Generate test cases with AI" on the Test Cases page stopped with "The On-Premise AI did not return valid
structured output" while the AI Test Design page worked with the same story and local model.

Cause: the AI Test Design page sends its JSON schema to llama-server (a grammar the model must follow); the Test Cases
page did not, so a small model answered in the prompt's plain-text example format (steps as strings) or with fields of
the wrong type, and the whole answer was refused. Now the Test Cases page sends the schema too, repairs JSON, maps
common shapes and checks each test case on its own."""

import json
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.environ.get("QA_APP", "app"))
from qa_platform.ai import gateway as gw  # noqa: E402
from qa_platform.ai.json_repair import close_json, parse_lenient  # noqa: E402
from qa_platform.design import models  # noqa: E402
from qa_platform.design import suite_cases as sc  # noqa: E402
from qa_platform.errors import QAError  # noqa: E402
from tests.fake_llm import FakeLLM  # noqa: E402
from tests.test_suite_cases import _generate, _suite, env  # noqa: E402,F401
from tests.test_suite_cases_context import Act, Bus, S  # noqa: E402

gw._sleep = lambda s: None

STORY = {"id": 1554, "title": "select 1 item", "state": "New", "iteration": "online store python\\Sprint 1", "rev": 1,
         "description": "As a customer I want to select one item so that I can buy it."}
CRIT = [{"id": "AC-01", "hash": "1", "text": "The customer can add one item to the cart and the cart shows 1"}]

# What a small local model writes without a grammar: the prompt's plain-text example, inside JSON.
PLAIN_TEXT_STYLE = """Sure, here are the test cases:
```json
{
  "test_cases": [
    {
      "ref": "TC1",
      "title": "Add one item to the cart shows 1 in the cart badge",
      "ac_ids": "AC-01",
      "condition_ids": "AC-01.1",
      "preconditions": ["The store home page is open", "The cart is empty"],
      "test_data": {"item": "Blue T-shirt"},
      "steps": [
        "1. Click the 'Blue T-shirt' product. => The product page opens.",
        "2. Click the Add to cart button. => The cart badge shows 1.",
      ],
      "priority": "High (1)",
      "risk": "High",
      "scenario": "Positive / happy path",
      "automation": True,
    },
    {
      "ref": "TC2",
      "title": "Cart badge after removing the item",
      "ac_ids": ["AC-01"],
      "steps": [
        {"step": "Open the cart page", "expected_result": "The cart lists 'Blue T-shirt'"},
        {"Action": "Click Remove", "Expected Result": "The cart badge shows 0"}
      ],
    }, // a comment a model sometimes writes
  ],
}
```"""


def service(handler, settings=None):
    g = gw.AIGateway(settings or S(), Bus(), transport=httpx.MockTransport(handler))
    svc = sc.SuiteCaseService.__new__(sc.SuiteCaseService)
    svc.ai = g
    svc.read_story = lambda sid: {"story": STORY, "criteria": CRIT, "existing": []}
    svc.impact = lambda sid, c: []
    return svc


def reply(content, finish="stop"):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish}]})


def chat_only(answers, calls):
    def handler(req):
        if not req.url.path.endswith("/chat/completions"):
            return httpx.Response(404)
        calls.append(json.loads(req.content))
        a = answers[min(len(calls), len(answers)) - 1]
        return reply(*a) if isinstance(a, tuple) else reply(a)
    return handler


# ---- lenient JSON ------------------------------------------------------------------------------------------------


def test_parse_lenient_repairs_what_small_models_write():
    assert parse_lenient('Here you go:\n```json\n{"a": [1, 2,],}\n```') == {"a": [1, 2]}
    assert parse_lenient('{"a": True, "b": None, "c": "True stays"}') == {"a": True, "b": None, "c": "True stays"}
    assert parse_lenient('{"a": 1, // note\n "b": "x // not a comment" /* c */}') == {"a": 1, "b": "x // not a comment"}
    assert parse_lenient("<think>plan</think>{“a”: 1}") == {"a": 1}
    assert parse_lenient('{"items": [{"x": 1}, {"x": 2}, {"x": 3') == {"items": [{"x": 1}, {"x": 2}]}
    with pytest.raises(ValueError):
        parse_lenient("I cannot help with that.")
    with pytest.raises(ValueError):
        parse_lenient('{"items": [{"x": 1}, {"x"', close=False)
    assert close_json('{"a": [1, 2') is None and close_json('{"a": [[1], [2') == '{"a": [[1]]}'


def test_complete_json_accepts_trailing_commas_and_comments():
    calls = []
    g = gw.AIGateway(S(), Bus(), transport=httpx.MockTransport(chat_only(['{"summary": "s", "requirements": [],}'], calls)))
    out = g.complete_json("sys", "user", models.RequirementsAnalysis)
    assert out.summary == "s" and len(calls) == 1  # no second (repair) call needed


# ---- answer shapes -----------------------------------------------------------------------------------------------


def test_case_set_from_plain_text_style_answer():
    cs, problems = sc.case_set_from_answer(parse_lenient(PLAIN_TEXT_STYLE))
    assert not problems
    one, two = cs.test_cases
    assert one.ac_ids == ["AC-01"] and one.condition_ids == ["AC-01.1"]
    assert [s.action for s in one.steps] == ["Click the 'Blue T-shirt' product.", "Click the Add to cart button."]
    assert one.steps[1].expected == "The cart badge shows 1."
    assert one.preconditions == "The store home page is open; The cart is empty"
    assert one.test_data == ["item = Blue T-shirt"]
    assert (one.priority, one.risk, one.scenario) == (1, "high", "positive")
    assert [(s.action, s.expected) for s in two.steps] == [("Open the cart page", "The cart lists 'Blue T-shirt'"),
                                                          ("Click Remove", "The cart badge shows 0")]


def test_one_bad_case_is_left_out_and_the_others_kept():
    data = {"testCases": [{"title": "No steps at all"},
                          {"name": "Good one", "steps": ["Open the cart => The cart page opens"]}],
            "not_applicable": ["AC-02: not a functional behaviour"], "clarifications": ["Which currency?"]}
    cs, problems = sc.case_set_from_answer(data)
    assert [c.title for c in cs.test_cases] == ["Good one"] and cs.test_cases[0].ref == "TC2"
    assert len(problems) == 1 and "test case 1" in problems[0]
    assert (cs.not_applicable[0].ac_id, cs.clarifications[0].question) == ("AC-02", "Which currency?")
    only, _ = sc.case_set_from_answer([{"title": "Top-level list", "steps": [{"action": "Open", "expected": "Shown"}]}])
    assert only.test_cases[0].title == "Top-level list"


def test_schema_sent_as_grammar_is_capped():
    schema = sc.case_set_schema(2)
    assert schema["properties"]["test_cases"]["maxItems"] == 2
    case = schema["$defs"]["SuiteTestCase"]
    assert case["properties"]["steps"]["maxItems"] == 8 and "steps" in case["required"]
    assert "description" not in json.dumps(schema)


# ---- the generation path of the Test Cases page ------------------------------------------------------------------


def test_generation_sends_the_schema_like_ai_test_design_and_reads_a_plain_text_style_answer():
    calls = []
    svc = service(chat_only([PLAIN_TEXT_STYLE], calls))
    result = svc.generate_story(1554, {"testing_type": "functional"}, run_id="X", act=Act(), cases_per_criterion=2)
    assert not result["failed_criteria"] and len(result["test_cases"]) == 2
    rf = calls[0]["response_format"]
    assert rf["type"] == "json_object" and rf["schema"]["properties"]["test_cases"]["maxItems"] == 2


def test_unusable_first_answer_is_asked_once_more_then_used():
    good = json.dumps({"test_cases": [{"title": "Add item", "ac_ids": ["AC-01"],
                                       "steps": [{"action": "Click Add to cart", "expected": "The badge shows 1"}]}]})
    calls = []
    svc = service(chat_only(["I will write the test cases now.", good], calls))
    result = svc.generate_story(1554, {"testing_type": "functional"}, run_id="X", act=Act(), cases_per_criterion=2)
    assert [c["ac_ids"] for c in result["test_cases"]] == [["AC-01"]]
    assert len(calls) == 2 and "Important: Your previous reply could not be used" in calls[1]["messages"][1]["content"]


def test_cut_off_answer_keeps_only_complete_cases():
    full = {"test_cases": [{"ref": f"TC{i}", "title": f"Case {i}", "ac_ids": ["AC-01"],
                            "steps": [{"action": f"Step {i}", "expected": f"Result {i}"}] * 2} for i in (1, 2, 3)]}
    text = json.dumps(full)
    cut = text[: text.index('"ref": "TC3"') + 30]
    calls = []
    svc = service(chat_only([(cut, "length")], calls))
    cs = svc._ask("suite-aware test case designer", "user", 3, max_tokens=1000)
    # the answer stopped inside case 3: the case written last before the cut is dropped too, so no case with
    # missing steps or fields is ever kept
    assert [c.title for c in cs.test_cases] == ["Case 1"]


def test_garbage_twice_still_fails_with_the_answer_in_the_details():
    calls = []
    svc = service(chat_only(["no json here", "still none"], calls))
    with pytest.raises(QAError) as e:
        svc._ask("suite-aware test case designer", "user", 2, max_tokens=500)
    assert e.value.error_code == "AI_OUTPUT_INVALID" and "still none" in e.value.details and len(calls) == 2


def test_test_cases_page_end_to_end_with_a_model_that_ignores_the_format(env):  # noqa: F811
    c, ado, llm = env

    def wrong_shapes(body):
        return {"testCases": [{"name": "Customer logs in with valid credentials", "ac_ids": "AC-01",
                               "preconditions": ["The login page is open"],
                               "steps": ["1. Enter 'standard_user' in the Username field. => The field shows it.",
                                         "2. Click the Login button. => The Products page opens."],
                               "priority": "P1", "automation": True}]}

    llm.extra["suite-aware test case designer"] = wrong_shapes
    _, result = _generate(c, _suite(c, "functional"), [201])
    cases = result["stories"][0]["test_cases"]
    assert cases and cases[0]["steps"][1]["expected"] == "The Products page opens."
    sent = [b for b in llm.calls if "suite-aware test case designer" in b["messages"][0]["content"]]
    assert sent and all(b["response_format"].get("schema") for b in sent)
