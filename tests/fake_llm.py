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
}


class FakeLLM:
    def __init__(self, models=("qwen2.5-7b-instruct-q4_k_m.gguf",), bad_json_once=False):
        self.models = list(models)
        self.calls = []
        self.down = False
        self.bad_json_once = bad_json_once

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
            for marker, reply in REPLIES.items():
                if marker in system:
                    if self.bad_json_once:
                        self.bad_json_once = False
                        return self._reply("Sure! Here you go: {not json")
                    return self._reply("```json\n" + json.dumps(reply) + "\n```")
            return self._reply("{}")
        return httpx.Response(404, json={"error": "no route"})

    @staticmethod
    def _reply(content):
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})
