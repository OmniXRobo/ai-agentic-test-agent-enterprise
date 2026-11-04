"""23.3.8: suite test case generation against a simulated llama-server whose context window is too small for the
23.3.7 requests (the cause of "The AI server returned HTTP 400" after minutes of waiting)."""
import json
import os
import re
import sys

import httpx
import pytest

sys.path.insert(0, os.environ.get("QA_APP", "app"))
from qa_platform.ai import gateway as gw  # noqa: E402
from qa_platform.design import suite_cases as sc  # noqa: E402
from qa_platform.errors import QAError  # noqa: E402

gw._sleep = lambda s: None


def case(i, ac):
    return {"ref": f"TC{i}", "title": f"Case {i} for {ac} checks the login page shows the message", "ac_ids": [ac],
            "condition_ids": [f"{ac}.1"], "objective": "Verify " * 20,
            "preconditions": "The login page is open and the account standard_user exists.",
            "steps": [{"action": f"Enter 'standard_user' in the Username field step {n}.",
                       "expected": f"The Username field shows 'standard_user' and the page header reads Login {n}."}
                      for n in range(6)], "priority": 2, "risk": "medium", "risk_rationale": "Core flow " * 8,
            "scenario": "positive", "automation": "automatable", "gaps": ["Exact message text not stated in story."]}


class S:
    def __init__(self):
        self.d = {"ai": {"base_url": "http://127.0.0.1:8080/v1", "max_tokens": 4096}, "design": {}}

    def section(self, n):
        return self.d.get(n, {})


class Bus:
    def publish(self, e):
        pass


class Act:
    def progress(self, p, msg):
        pass


def run(props: bool, n_ctx: int):
    calls = []

    def handler(req):
        if req.url.path.endswith("/props"):
            return (httpx.Response(200, json={"default_generation_settings": {"n_ctx": n_ctx}}) if props
                    else httpx.Response(404))
        body = json.loads(req.content)
        prompt = sum(len(m["content"]) for m in body["messages"]) / 3.5
        calls.append((int(prompt), body["max_tokens"]))
        if prompt > n_ctx:
            return httpx.Response(400, json={"error": {
                "code": 400, "message": "the request exceeds the available context size, try increasing it",
                "type": "exceed_context_size_error", "n_prompt_tokens": int(prompt), "n_ctx": n_ctx}})
        sysmsg = body["messages"][0]["content"]
        k = int(re.search(r"at most (\d+) (?:test )?cases", body["messages"][-1]["content"] + sysmsg).group(1)) if re.search(r"at most \d", body["messages"][-1]["content"] + sysmsg) else 1
        acs = re.findall(r"^(AC\d+): ", body["messages"][1]["content"], re.M) or ["AC01"]
        out = json.dumps({"test_cases": [case(i + 1, acs[i % len(acs)]) for i in range(k)]})
        budget = min(body["max_tokens"], n_ctx - int(prompt))
        finish = "stop"
        if len(out) / 3.5 > budget:  # like llama-server: generation stops when the context is full
            out, finish = out[: int(budget * 3.5)], "length"
        return httpx.Response(200, json={"choices": [{"message": {"content": out}, "finish_reason": finish}],
                                         "usage": {"prompt_tokens": int(prompt),
                                                   "completion_tokens": int(len(out) / 3.5)}})

    g = gw.AIGateway(S(), Bus(), transport=httpx.MockTransport(handler))
    svc = sc.SuiteCaseService.__new__(sc.SuiteCaseService)
    svc.ai = g
    crit = [{"id": f"AC{n:02d}", "hash": str(n),
             "text": f"When the user enters a valid username and password number {n} the dashboard opens and shows "
                     "the welcome message"}
            for n in (3, 7, 8, 9, 10, 11, 12, 13, 14, 15)]
    story = {"id": 1177, "title": "Login", "state": "Active", "iteration": "Sprint 25", "rev": 3,
             "description": "As a user I want to log in so that I can see my dashboard. " * 60}
    svc.read_story = lambda sid: {"story": story, "criteria": crit,
                                  "existing": [{"id": 900 + i, "title": f"Existing case {i} for login"} for i in range(40)]}
    svc.impact = lambda sid, c: []
    return svc.generate_story(1177, {"testing_type": "functional"}, run_id="X", act=Act()), calls


@pytest.mark.parametrize("props", [True, False])
def test_small_context_generates_every_criterion(props):
    result, _ = run(props, 4096)
    assert result["test_cases"]
    assert not result["failed_criteria"]


def test_known_context_sends_requests_that_fit():
    result, calls = run(True, 4096)
    assert len(result["test_cases"]) >= 10
    assert all(p + m <= 4096 + 600 for p, m in calls)  # estimate margin


def test_large_context_keeps_two_criteria_per_call():
    result, calls = run(True, 16384)
    assert result["test_cases"] and not result["failed_criteria"]


def test_hopeless_context_stops_early_with_what_to_change():
    with pytest.raises(QAError) as e:
        run(True, 2048)
    assert e.value.error_code == "AI_CONTEXT_EXCEEDED"
    assert "2048 tokens" in e.value.message and "-c 16384" in e.value.message
