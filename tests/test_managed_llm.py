"""Managed local LLM: starts llama-server with the GGUF model, points the AI settings at it, and stops only the
process it started (a server someone else runs is never touched)."""

from __future__ import annotations

import socket
import subprocess
import sys
import time

import httpx
import pytest

from qa_platform.ai.managed import ManagedLLM
from qa_platform.config import Paths, Settings
from qa_platform.errors import QAError

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the fake llama-server is a POSIX script")

FAKE_SERVER = f"""#!{sys.executable}
import http.server, json, sys
args = sys.argv[1:]
port = int(args[args.index("--port") + 1]); alias = args[args.index("--alias") + 1]
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({{"data": [{{"id": alias}}]}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def llm(tmp_path, monkeypatch):
    ai_dir = tmp_path / "runtime-ai"
    (ai_dir / "bin").mkdir(parents=True)
    server = ai_dir / "bin" / "llama-server"
    server.write_text(FAKE_SERVER)
    server.chmod(0o755)
    (ai_dir / "Qwen2.5-7B-Instruct-Q4_K_M.gguf").write_bytes(b"GGUF")
    monkeypatch.setenv("QA_AI_DIR", str(ai_dir))
    paths = Paths(tmp_path / "data").ensure()
    settings = Settings(paths.settings_file)
    port = free_port()
    settings.update({"ai": {"managed": {"port": port, "startup_timeout_s": 20}}})
    m = ManagedLLM(settings, paths)
    yield m, settings, port
    m.close()


def test_detect_start_points_ai_settings_and_stop_only_own_process(llm):
    m, settings, port = llm
    found = m.detect()
    assert found["servers"] and found["models"][0]["name"].endswith(".gguf")
    st = m.start(wait=True)
    assert st["state"] == "RUNNING" and st["pid"]
    assert settings.section("ai")["base_url"] == f"http://127.0.0.1:{port}/v1"
    assert settings.section("ai")["model"] == "qwen"
    assert httpx.get(f"http://127.0.0.1:{port}/v1/models").json()["data"][0]["id"] == "qwen"
    assert m.start()["pid"] == st["pid"]  # already running: not started twice
    assert m.stop()["state"] == "STOPPED"
    time.sleep(0.3)
    with pytest.raises(httpx.HTTPError):
        httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=1)


def test_server_run_by_someone_else_is_left_alone(llm, tmp_path):
    m, _settings, port = llm
    other = subprocess.Popen([m.detect()["servers"][0], "--port", str(port), "--alias", "theirs"])
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=0.5)
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        assert m.status()["external_server"] is True
        with pytest.raises(QAError) as err:
            m.start()
        assert err.value.error_code == "LLM_PORT_IN_USE"
        m.stop()
        assert other.poll() is None  # still running
    finally:
        other.terminate()
        other.wait(5)


def test_missing_model_is_reported(llm, tmp_path):
    m, settings, _port = llm
    settings.update({"ai": {"managed": {"model_path": str(tmp_path / "nope.gguf")}}})
    with pytest.raises(QAError) as err:
        m.start()
    assert err.value.error_code == "LLM_MODEL_MISSING"
