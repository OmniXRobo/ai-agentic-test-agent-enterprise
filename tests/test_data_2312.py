"""23.3.12: test data generation with a small local model (qwen2.5-1.5b on a CPU) that writes prose around its JSON,
breaks some rows, stops part way, or runs on a llama-server that refuses JSON schemas."""
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, os.environ.get("QA_APP", "app"))
from qa_platform.ai import gateway as gw  # noqa: E402
from qa_platform.design import data_agent as da  # noqa: E402
from qa_platform.errors import QAError  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_suite_cases_context import S, Bus, Act  # noqa: E402

gw._sleep = lambda s: None
SMOKE = {"key": "smoke", "name": "Smoke Testing", "data": "Minimal, dependable valid data for the critical workflow only."}


def row(n, valid=True):
    return {"name": f"Row {n}", "scenario": "valid" if valid else "invalid", "valid": valid,
            "breaks": None if valid else "Password required", "expected": f"Dashboard {n} opens",
            "ac": ["AC01"], "values": {"username": f"user{n}", "password": "{{SECRET:pw}}"}}


def service(replies, refuse_schema=False):
    sent = []

    def handler(req):
        if req.url.path.endswith("/props"):
            return httpx.Response(200, json={"default_generation_settings": {"n_ctx": 8192}, "total_slots": 1})
        body = json.loads(req.content)
        sent.append(body)
        if refuse_schema and "schema" in (body.get("response_format") or {}):
            return httpx.Response(400, json={"error": {"code": 400, "message": "JSON schema conversion failed"}})
        content, finish = replies.pop(0) if replies else ('{"rows":[]}', "stop")
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish}]})
    svc = da.TestDataService.__new__(da.TestDataService)
    svc.ai = gw.AIGateway(S(), Bus(), transport=httpx.MockTransport(handler))
    return svc, sent


def run(svc, max_rows=6):
    return svc._design_rows(1395, SMOKE, "Test case #1395: Login", max_rows, Act())


def test_schema_is_sent_and_rows_come_in_batches():
    first = {"fields": [{"name": "username", "type": "string"}], "rows": [row(1), row(2), row(3, False)],
             "gaps": ["Password rules not stated"]}
    svc, sent = service([(json.dumps(first), "stop"), (json.dumps({"rows": [row(4), row(5), row(6)]}), "stop")])
    d = run(svc)
    assert len(d.rows) == 6 and d.fields[0].name == "username" and d.gaps == ["Password rules not stated"]
    assert d.rows[2].intended_valid is False and d.rows[2].violated_rule == "Password required"
    schema = sent[0]["response_format"]["schema"]
    assert schema["properties"]["rows"]["maxItems"] == 3 and "fields" in schema["properties"]
    assert "fields" not in sent[1]["response_format"]["schema"]["properties"]
    assert "Use these fields in values: username" in sent[1]["messages"][1]["content"]
    assert all(b["max_tokens"] <= 1200 for b in sent)


def test_prose_fences_trailing_commas_and_one_bad_row_are_accepted():
    text = ("Here is the data:\n```json\n" + json.dumps({"rows": [row(1), {"name": "broken"}, row(2)]})[:-2]
            + ",],}\n```\nHope this helps!")
    svc, sent = service([(text, "stop")])
    d = run(svc, max_rows=3)
    assert [r.name for r in d.rows] == ["Row 1", "Row 2"]
    assert len(sent) == 1  # no second "repair" call


def test_cut_off_answer_keeps_complete_rows():
    full = json.dumps({"fields": [], "rows": [row(1), row(2), row(3)]})
    svc, sent = service([(full[: full.index('"Row 3"') + 20], "length")])
    d = run(svc, max_rows=3)
    assert [r.name for r in d.rows] == ["Row 1", "Row 2"]
    assert any("stopped" in g for g in d.gaps) or len(sent) == 1


def test_server_without_schema_support_falls_back_to_json_mode():
    svc, sent = service([(json.dumps({"rows": [row(1)]}), "stop")], refuse_schema=True)
    d = run(svc, max_rows=1)
    assert len(d.rows) == 1
    assert "schema" in sent[0]["response_format"] and sent[1]["response_format"] == {"type": "json_object"}


def test_nothing_usable_twice_fails_with_a_clear_error():
    svc, sent = service([("I cannot help with that.", "stop"), ("Sorry.", "stop")])
    with pytest.raises(QAError) as e:
        run(svc)
    assert e.value.error_code == "AI_OUTPUT_INVALID" and len(sent) == 2
    assert sent[1]["response_format"]["schema"]["properties"]["rows"]["maxItems"] == 1


def test_second_batch_failure_keeps_first_rows():
    svc, _ = service([(json.dumps({"rows": [row(1), row(2), row(3)]}), "stop"), ("garbage", "stop")])
    d = run(svc)
    assert len(d.rows) == 3 and any("stopped after 3 rows" in g for g in d.gaps)


def test_cloud_mode_never_gets_a_schema():
    svc, sent = service([(json.dumps({"rows": [row(1)]}), "stop")])
    svc.ai.settings.d["ai"]["mode"] = "cloud"
    svc.ai.settings.d["ai"]["cloud"] = {"base_url": "http://127.0.0.1:8080/v1", "model": "m"}
    try:
        svc.ai.chat_result([{"role": "user", "content": "x"}], json_mode=True, schema={"type": "object"})
    except QAError:
        pass
    assert all("schema" not in (b.get("response_format") or {}) for b in sent)
