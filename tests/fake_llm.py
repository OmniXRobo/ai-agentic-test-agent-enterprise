"""A scripted OpenAI-compatible LLM server double for tests (test fixture only)."""

from __future__ import annotations

import json

import httpx

REPLIES = {
    "requirements analyst": {
        "summary": "Pay with a saved card at checkout.",
        "requirements": [
            {"id": "R1", "text": "Customer can pay with a saved card", "acceptance_criterion": "Pay with saved card",
             "kind": "functional", "risk": "high"},
            {"id": "R2", "text": "Expired saved cards are rejected", "kind": "functional", "risk": "medium"},
        ],
        "ambiguities": ["Maximum number of saved cards is not stated."],
    },
    "test manager": {
        "scope": "Checkout payment with saved cards", "approach": "Risk-based functional testing",
        "test_types": ["functional", "negative"], "risks": ["Payment gateway"], "suite_name": "Checkout - Saved card",
    },
    "senior QA engineer": {
        "test_cases": [
            {"ref": "TC1", "title": "Pay with a valid saved card", "priority": 1, "category": "positive",
             "requirement_ids": ["R1"], "steps": [{"action": "Open cart and click Checkout", "expected": "Payment page opens"},
                                                   {"action": "Select saved card and pay", "expected": "Order confirmation shown"}]},
            {"ref": "TC2", "title": "Expired saved card is rejected", "priority": 2, "category": "negative",
             "requirement_ids": ["R2"], "steps": [{"action": "Pay with expired saved card", "expected": "Card expired error shown"}]},
        ]
    },
    "test data engineer": {
        "datasets": [
            {"test_case_ref": "TC1", "name": "Valid Visa", "kind": "valid", "values": {"card": "4111 1111 1111 1111", "expiry": "12/30"}},
            {"test_case_ref": "TC2", "name": "Expired card", "kind": "invalid", "values": {"card": "4111 1111 1111 1111", "expiry": "01/20"}},
            {"test_case_ref": "TC9", "name": "Orphan", "values": {}},
        ]
    },
    "diagnostics probe": {"status": "ok", "count": 3},
    "performance engineer reviewing JMeter results": {
        "summary": "All requests stayed fast under the tested load.",
        "observations": ["p95 stayed below the SLA for every request"],
        "suspected_bottlenecks": [],
        "recommendations": ["Repeat with more users to find the saturation point"],
        "undetermined": ["Server-side resource usage was not measured"],
    },
    # Voice intent fallback: always the same answer, so tests can check that an ID the user
    # did not say is dropped.
    "spoken request to exactly one command": {"command": "run_test_case", "test_case_id": 1280},
    "Playwright automation engineer": {
        "steps": [
            {"action": "goto", "value": "login.html", "description": "Open the sign-in page"},
            {"action": "fill", "selector": "#username", "value": "{{CRED:admin.username}}", "description": "Enter user name"},
            {"action": "fill", "selector": "#password", "value": "{{CRED:admin.password}}", "description": "Enter password"},
            {"action": "click", "selector": "#login-btn", "description": "Click Sign in"},
            {"action": "expect_text", "selector": "#welcome", "value": "Welcome alice", "description": "Welcome shown"},
            {"action": "click", "selector": "#made-up-button", "description": "Invented element"},
            {"action": "teleport", "description": "Unsupported action"},
        ],
        "notes": [],
    },
}


class FakeLLM:
    def __init__(self, models=("qwen2.5-7b-instruct-q4_k_m.gguf",), bad_json_once=False):
        self.models = list(models)
        self.calls = []
        self.down = False
        self.bad_json_once = bad_json_once
        self.extra = {}  # marker -> reply or callable(body) -> reply, checked before REPLIES

    def transport(self):
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": m} for m in self.models]})
        if request.url.path.endswith("/chat/completions"):
            body = json.loads(request.content)
            self.calls.append(body)
            system = body["messages"][0]["content"]
            if body["messages"][0]["role"] == "user":
                return self._reply("READY")
            for marker, reply in {**self.extra, **REPLIES}.items():
                if marker in system:
                    if callable(reply):  # a reply that depends on the request (e.g. which Test Case)
                        reply = reply(body)
                    if self.bad_json_once:
                        self.bad_json_once = False
                        return self._reply("Sure! Here you go: {not json")
                    return self._reply("```json\n" + json.dumps(reply) + "\n```")
            return self._reply("{}")
        return httpx.Response(404, json={"error": "no route"})

    @staticmethod
    def _reply(content):
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})


# ---- suite-aware test cases and test data (replies depend on the suite's testing type) -----------------

import re as _re  # noqa: E402


def _criteria(body):
    user = body["messages"][1]["content"]
    block = user.split("Acceptance criteria:\n", 1)[1].split("\n\n", 1)[0]
    return [(m.group(1), m.group(2)) for m in _re.finditer(r"^([A-Z][\w.-]*\d[\w.-]*): (.+)$", block, _re.M)]


def _type(body):
    return _re.search(r"(?:for|designer for|reviewer for) ([A-Za-z /-]+? Testing)", body["messages"][0]["content"]).group(1)


def decompose(body):
    out = []
    for ac, text in _criteria(body):
        parts = [p.strip() for p in _re.split(r"\band\b", text) if p.strip()] if " and " in text else [text]
        out.append({"ac_id": ac, "conditions": [{"id": f"{ac}.{n}", "text": p} for n, p in enumerate(parts, 1)],
                    "clarification": "What does 'appropriate' mean?" if "appropriate" in text.lower() else None})
    return {"criteria": out, "business_rules": [], "gaps": ["Lockout limit is not stated."]}


def _step(a, e):
    return {"action": a, "expected": e}


def design_cases(body):
    t, crit = _type(body), _criteria(body)
    if t == "Smoke Testing":
        ac = crit[0][0]
        return {"test_cases": [{"ref": "TC1", "title": "Critical login path is available", "ac_ids": [ac],
                                "condition_ids": [f"{ac}.1"], "objective": "Build health", "priority": 1, "risk": "high",
                                "steps": [_step("Open the login page", "Login form with Username and Password fields is shown"),
                                          _step("Log in as {{SECRET:standard_user}}", "Product inventory page is shown")]}]}
    if t == "Performance Testing":
        return {"test_cases": [{"ref": "TC1", "title": "Login response time under concurrent users", "ac_ids": [crit[0][0]],
                                "scenario": "performance", "automation": "partial",
                                "steps": [_step("Run 50 concurrent logins", "Response time is recorded for every login")]}],
                "not_applicable": [{"ac_id": ac, "reason": "Not a performance-relevant behaviour"} for ac, _ in crit[1:]]}
    cases = []
    for n, (ac, text) in enumerate(crit, 1):
        cases.append({"ref": f"TC{n}", "title": f"Verify {text}", "ac_ids": [ac], "condition_ids": [f"{ac}.1"],
                      "objective": f"Prove {ac}", "preconditions": "The login page is open.",
                      "test_data": ["username = standard_user"], "priority": 2, "risk": "medium",
                      "risk_rationale": "Core journey", "scenario": "negative" if "error" in text else "positive",
                      "steps": [_step("Enter 'standard_user' in the username field.", "Username field displays 'standard_user'."),
                                _step("Click the 'Login' button.", "works correctly" if n == 1 else "An error message 'Wrong password' is shown")]})
    cases.append({"ref": f"TC{len(crit) + 1}", "title": "Customer logs in successfully", "ac_ids": [crit[0][0], "AC-99"],
                  "steps": [_step("Log in", "Inventory page is shown")]})
    return {"test_cases": cases, "clarifications": []}


def review_cases(body):
    user = body["messages"][1]["content"]
    data = json.loads(user.split("Test cases:\n", 1)[1].split("\n\nIssues:", 1)[0])
    for tc in data["test_cases"]:
        for s in tc["steps"]:
            if s["expected"] == "works correctly":
                s["expected"] = "Customer is redirected to the product inventory page."
    # cover every condition of every criterion (the review fixes partial coverage)
    conds = _re.findall(r"^([A-Z][\w-]*\d+\.\d+) \(", user, _re.M)
    for tc in data["test_cases"]:
        tc["condition_ids"] = sorted(set(tc["condition_ids"]) | {c for c in conds if c.rsplit(".", 1)[0] in tc["ac_ids"]})
    return data


def design_data(body):
    t = _type(body)
    rows = [
        {"name": "Valid user", "scenario": "valid", "intended_valid": True, "expected_outcome": "Inventory page is shown",
         "ac_ids": ["AC-01"], "values": {"username": "valid_user", "password": "ValidPass123!", "email": "jane@gmail.com",
                                          "account_no": "007", "start_date": "{{TODAY+3d:%d/%m/%Y}}"}},
        {"name": "Wrong password", "scenario": "invalid", "intended_valid": False, "violated_rule": "Password must match",
         "expected_outcome": "Error 'Wrong password' is shown", "ac_ids": ["AC-02"],
         "values": {"username": "valid_user", "password": "wrong-pass", "email": "jane@example.com", "account_no": "007"}},
        {"name": "Empty username", "scenario": "empty", "intended_valid": False, "expected_outcome": "Username is required",
         "ac_ids": ["AC-01", "AC-77"], "values": {"username": "", "password": None}},
        {"name": "Formula", "scenario": "special_characters", "intended_valid": False, "violated_rule": "Text only",
         "expected_outcome": "Rejected", "values": {"username": "=HYPERLINK(\"x\")", "password": "{{SECRET:password}}"}},
        {"name": "Valid user copy", "scenario": "valid", "intended_valid": True, "expected_outcome": "Inventory page is shown",
         "ac_ids": ["AC-01"], "values": {"username": "valid_user", "password": "ValidPass123!", "email": "jane@gmail.com",
                                          "account_no": "007", "start_date": "{{TODAY+3d:%d/%m/%Y}}"}},
    ]
    if t == "Performance Testing":
        rows = [{"name": "1000 unique users", "scenario": "volume", "intended_valid": True,
                 "expected_outcome": "Response times are recorded", "values": {"users": 1000, "username": "user_{{UNIQUE}}"}}]
    return {"fields": [{"name": "username", "type": "string", "required": True, "source": "specified"},
                       {"name": "password", "type": "secret", "required": True, "source": "specified"}],
            "rows": rows, "gaps": ["Maximum username length is not stated."],
            "not_applicable": ["Volume data: not needed for functional checks"] if t != "Performance Testing" else []}


REPLIES.update({
    "acceptance-criteria decomposition analyst": decompose,
    "suite-aware test case designer": design_cases,
    "test case reviewer": review_cases,
    "suite-aware test data designer": design_data,
})
