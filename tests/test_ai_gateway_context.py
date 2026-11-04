"""23.3.8: readable AI server errors, retries, context-window handling (no AI server needed)."""
import json
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.environ.get("QA_APP", "app"))
from qa_platform.ai import gateway as gw  # noqa: E402
from qa_platform.errors import QAError  # noqa: E402
from pydantic import BaseModel  # noqa: E402

gw._sleep = lambda s: None


class S:
    def __init__(self, **ai):
        self.d = {"ai": {"base_url": "http://127.0.0.1:8080/v1", "max_tokens": 4096, **ai}}

    def section(self, n):
        return self.d.get(n, {})


class Bus:
    def publish(self, e):
        pass


def make(responses, **ai):
    seen = []

    def handler(req):
        if req.url.path.endswith("/props"):
            return httpx.Response(404)
        body = json.loads(req.content)
        seen.append(body)
        r = responses.pop(0)
        return r(body) if callable(r) else r
    return gw.AIGateway(S(**ai), Bus(), transport=httpx.MockTransport(handler)), seen


def ok(text, finish="stop"):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}, "finish_reason": finish}]})


class P(BaseModel):
    status: str
    count: int


def test_400_names_the_server_message():
    g, _ = make([httpx.Response(400, json={"error": {"message": "Invalid 'messages': role 'tool' is not supported"}})] * 3)
    with pytest.raises(QAError) as e:
        g.chat([{"role": "user", "content": "hi"}])
    assert e.value.error_code == "AI_REQUEST_FAILED"
    assert "HTTP 400: Invalid 'messages'" in e.value.message


def test_llama_cpp_context_error_is_explained_and_learned():
    err = {"error": {"code": 400, "message": "the request exceeds the available context size, try increasing it",
                     "type": "exceed_context_size_error", "n_prompt_tokens": 5000, "n_ctx": 4096}}
    g, seen = make([httpx.Response(400, json=err)])
    with pytest.raises(QAError) as e:
        g.chat([{"role": "user", "content": "x" * 100}], json_mode=True)
    assert e.value.error_code == "AI_CONTEXT_EXCEEDED"
    assert "4096 tokens" in e.value.message and "-c 16384" in e.value.message
    assert len(seen) == 1  # no pointless retry without response_format
    assert g.context_window() == 4096


def test_vllm_style_max_tokens_too_large_is_retried_shorter():
    msg = ("This model's maximum context length is 8192 tokens. However, you requested 9000 tokens "
           "(6000 in the messages, 3000 in the completion).")
    g, seen = make([httpx.Response(400, json={"error": {"message": msg}}), ok("fine")])
    assert g.chat([{"role": "user", "content": "x"}], max_tokens=3000) == "fine"
    assert seen[1]["max_tokens"] == 8192 - 6000 - 32


def test_transient_503_is_retried():
    g, seen = make([httpx.Response(503, json={"error": {"message": "Loading model"}}), ok("READY")])
    assert g.chat([{"role": "user", "content": "x"}]) == "READY"
    assert len(seen) == 2


def test_unknown_field_rejected_is_dropped():
    g, seen = make([httpx.Response(400, json={"error": "unknown field chat_template_kwargs"}), ok("ok")])
    assert g.chat([{"role": "user", "content": "x"}]) == "ok"
    assert "chat_template_kwargs" in seen[0] and "chat_template_kwargs" not in seen[1]


def test_thinking_switch_can_be_turned_off():
    g, seen = make([ok("ok")])
    g.settings.d["ai"]["disable_thinking"] = False
    g.chat([{"role": "user", "content": "x"}])
    assert "chat_template_kwargs" not in seen[0]


def test_known_context_caps_max_tokens_and_refuses_hopeless_prompt():
    g, seen = make([ok("ok")], context_tokens=4096)
    g.chat([{"role": "user", "content": "x" * 6400}], max_tokens=4096)  # ~2000 tokens estimated
    assert seen[0]["max_tokens"] < 4096 - 1700
    with pytest.raises(QAError) as e:
        g.chat([{"role": "user", "content": "x" * 20000}])
    assert e.value.error_code == "AI_CONTEXT_EXCEEDED"
    assert len(seen) == 1  # refused before sending


def test_truncated_json_is_not_echoed_back():
    g, seen = make([ok('{"status": "ok", "cou', "length")])
    with pytest.raises(QAError) as e:
        g.complete_json("sys", "user", P)
    assert e.value.error_code == "AI_OUTPUT_TRUNCATED"
    assert len(seen) == 1


def test_invalid_json_is_repaired():
    g, seen = make([ok("not json"), ok('{"status": "ok", "count": 3}')])
    assert g.complete_json("sys", "user", P).count == 3
    assert seen[1]["messages"][-2]["content"] == "not json"


def test_long_invalid_reply_is_asked_again_without_echo():
    g, seen = make([ok("x" * 9000), ok('{"status": "ok", "count": 3}')])
    assert g.complete_json("sys", "user", P).count == 3
    assert len(seen[1]["messages"]) == 2 and "Important:" in seen[1]["messages"][1]["content"]


def test_schema_titles_are_dropped():
    g, seen = make([ok('{"status": "ok", "count": 3}')])
    g.complete_json("sys", "user", P)
    assert '"title"' not in seen[0]["messages"][0]["content"]
