"""23.3.9: parallel AI calls, tuned llama-server arguments with a safe fallback, speed check."""
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import httpx

sys.path.insert(0, os.environ.get("QA_APP", "app"))
from qa_platform.ai import gateway as gw  # noqa: E402
from qa_platform.ai import managed as mg  # noqa: E402
from qa_platform.design import suite_cases as sc  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_suite_cases_context import S, Bus, Act, case  # noqa: E402

gw._sleep = lambda s: None


def server(slots, n_ctx=8192, delay=0.3, shared_overflow=False):
    state = {"active": 0, "peak": 0, "calls": 0, "errors": 0}
    lock = threading.Lock()

    def handler(req):
        if req.url.path.endswith("/props"):
            return httpx.Response(200, json={"default_generation_settings": {"n_ctx": n_ctx}, "total_slots": slots})
        body = json.loads(req.content)
        with lock:
            state["active"] += 1
            state["calls"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(delay)
        with lock:
            overflow = shared_overflow and state["active"] > 1
            state["active"] -= 1
        if overflow:  # newer llama-server with slots sharing one context (kv-unified)
            state["errors"] += 1
            return httpx.Response(500, json={"error": {"code": 500, "message": "Context size has been exceeded.",
                                                       "type": "server_error"}})
        sysmsg = body["messages"][0]["content"]
        if "numbers from 1 to 60" in body["messages"][-1]["content"]:
            return httpx.Response(200, json={"choices": [{"message": {"content": "1,2,3"}, "finish_reason": "stop"}],
                                             "usage": {"completion_tokens": 110},
                                             "timings": {"predicted_per_second": 4.2, "predicted_n": 110}})
        k = int(re.search(r"at most (\d+) (?:test )?cases", body["messages"][-1]["content"] + sysmsg).group(1)) if re.search(r"at most \d", body["messages"][-1]["content"] + sysmsg) else 1
        acs = re.findall(r"^(AC\d+): ", body["messages"][1]["content"], re.M) or ["AC01"]
        out = json.dumps({"test_cases": [case(i + 1, acs[i % len(acs)]) for i in range(k)]})
        return httpx.Response(200, json={"choices": [{"message": {"content": out}, "finish_reason": "stop"}]})
    return httpx.MockTransport(handler), state


def service(transport):
    g = gw.AIGateway(S(), Bus(), transport=transport)
    svc = sc.SuiteCaseService.__new__(sc.SuiteCaseService)
    svc.ai = g
    crit = [{"id": f"AC{n:02d}", "hash": str(n), "text": f"Criterion {n}: the user sees the welcome message number {n}"}
            for n in range(1, 9)]
    story = {"id": 1177, "title": "Login", "state": "Active", "iteration": "Sprint 25", "rev": 3, "description": "Log in."}
    svc.read_story = lambda sid: {"story": story, "criteria": crit, "existing": []}
    svc.impact = lambda sid, c: []
    return svc


def test_two_slots_run_two_calls_at_a_time_and_keep_order():
    transport, state = server(slots=2)
    r = service(transport).generate_story(1177, {"testing_type": "functional"}, run_id="X", act=Act())
    assert state["peak"] == 2
    acs = [c["ac_ids"][0] for c in r["test_cases"]]
    first_seen = list(dict.fromkeys(acs))
    assert first_seen == sorted(first_seen) and len(first_seen) == 8  # criterion order, whatever finished first
    assert [c["ref"] for c in r["test_cases"]] == [f"TC{i}" for i in range(1, len(acs) + 1)]


def test_one_slot_server_gets_one_call_at_a_time():
    transport, state = server(slots=1)
    service(transport).generate_story(1177, {"testing_type": "functional"}, run_id="X", act=Act())
    assert state["peak"] == 1


def test_speed_check_reports_tokens_per_second():
    transport, _ = server(slots=2, delay=0)
    g = gw.AIGateway(S(), Bus(), transport=transport)
    text = g.speed_test()
    assert "4.2 tokens/s" in text and "slow" in text and "107s" in text


class _Paths:
    def __init__(self, root):
        self.root = root
        self.logs = root / "logs"


def _llm(tmp_path, helptext, devices=""):
    llm = mg.ManagedLLM(S(), _Paths(tmp_path))
    llm._server_text = lambda server, *a: devices if a == ("--list-devices",) else helptext
    return llm


def test_server_args_new_build_with_gpu(tmp_path):
    help_new = "--list-devices -ngl, --n-gpu-layers N --jinja, --no-jinja --reasoning-budget N --cache-reuse N"
    llm = _llm(tmp_path, help_new, "Available devices:\n  Vulkan0: Intel(R) Iris(R) Xe Graphics (8000 MiB)")
    args = llm.server_args(Path("llama-server.exe"), Path("m.gguf"), {**mg.DEFAULTS})
    s = " ".join(args)
    assert "-c 16384 -np 2" in s and "-ngl 99" in s and "--reasoning-budget 0" in s and "--cache-reuse 256" in s
    assert "--jinja" not in args  # already the default in this build


def test_server_args_old_cpu_build_gets_only_known_options(tmp_path):
    llm = _llm(tmp_path, "-ngl, --n-gpu-layers N --jinja")
    args = llm.server_args(Path("llama-server.exe"), Path("m.gguf"), {**mg.DEFAULTS, "parallel": 1})
    s = " ".join(args)
    assert "-c 8192 -np 1" in s and "-ngl" not in s and "--jinja" in args
    assert "--reasoning-budget" not in s and "--cache-reuse" not in s


def test_cpu_only_setting_never_offloads(tmp_path):
    llm = _llm(tmp_path, "--list-devices -ngl", "CUDA0: NVIDIA RTX")
    args = llm.server_args(Path("s"), Path("m.gguf"), {**mg.DEFAULTS, "gpu_layers": 0})
    assert "-ngl" not in args


def test_small_context_runs_one_call_at_a_time():
    transport, state = server(slots=2, n_ctx=4096)
    service(transport).generate_story(1177, {"testing_type": "functional"}, run_id="X", act=Act())
    assert state["peak"] == 1


def test_shared_context_overflow_is_written_again_one_at_a_time():
    transport, state = server(slots=2, n_ctx=16384, shared_overflow=True)
    r = service(transport).generate_story(1177, {"testing_type": "functional"}, run_id="X", act=Act())
    assert 1 <= state["errors"] <= 2  # only the first pair: then one at a time, so no repeat of the overflow
    assert not r["failed_criteria"] and len({c["ac_ids"][0] for c in r["test_cases"]}) == 8
    first = list(dict.fromkeys(c["ac_ids"][0] for c in r["test_cases"]))
    assert first == sorted(first)


def test_answer_limit_never_exceeds_the_context_left():
    seen = []

    def handler(req):
        if req.url.path.endswith("/props"):
            return httpx.Response(200, json={"default_generation_settings": {"n_ctx": 2304}, "total_slots": 1})
        seen.append(json.loads(req.content)["max_tokens"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})
    g = gw.AIGateway(S(), Bus(), transport=httpx.MockTransport(handler))
    g.chat([{"role": "user", "content": "x" * 5500}], max_tokens=2800)  # ~1700 tokens estimated
    assert seen[0] <= 2304 - int(1700 * 0.85) and seen[0] < 2800


def test_calls_share_the_prompt_until_the_criterion_23311():
    bodies = []

    def handler(req):
        if req.url.path.endswith("/props"):
            return httpx.Response(200, json={"default_generation_settings": {"n_ctx": 8192}, "total_slots": 1})
        body = json.loads(req.content)
        bodies.append(body["messages"])
        acs = re.findall(r"^(AC\d+): ", body["messages"][1]["content"], re.M)
        out = json.dumps({"test_cases": [case(1, acs[0])]})
        return httpx.Response(200, json={"choices": [{"message": {"content": out}, "finish_reason": "stop"}]})
    svc = service(httpx.MockTransport(handler))
    svc.ai.settings.d["design"] = {"criteria_per_call": 1}
    svc.generate_story(1177, {"testing_type": "functional"}, run_id="X", act=Act())
    first, second = bodies[0], bodies[1]
    assert first[0]["content"] == second[0]["content"]  # the instructions and schema are identical
    a, b = first[1]["content"], second[1]["content"]
    common = len(os.path.commonprefix([a, b]))
    assert a[:common].endswith("Write test cases now only for these acceptance criteria:\nAC0")
    assert common > len(a) * 0.6


def test_cpu_server_gets_one_slot_and_gpu_two(tmp_path):
    cpu = _llm(tmp_path, "--list-devices -ngl", "Available devices:")
    assert "-np 1" in " ".join(cpu.server_args(Path("s"), Path("m.gguf"), {**mg.DEFAULTS}))
    gpu = _llm(tmp_path, "--list-devices -ngl", "CUDA0: NVIDIA RTX 3050")
    assert "-c 16384 -np 2 -ngl 99" in " ".join(gpu.server_args(Path("s"), Path("m.gguf"), {**mg.DEFAULTS}))
