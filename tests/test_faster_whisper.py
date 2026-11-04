"""Patch 23.3.7: Faster-Whisper as a local service started by the application, and an honest voice check.

The real model is not part of the tests: a stand-in ``faster_whisper`` package (same API: WhisperModel.transcribe
returning segments) is put in a temporary runtime\\faster-whisper\\lib, so the real server script, the real process
start, the HTTP requests, the settings and the checks are all exercised.
"""

from __future__ import annotations

import io
import json
import socket
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from qa_platform.credentials import MemoryBackend
from qa_platform.main import create_app
from qa_platform.voice import whisper_server
from qa_platform.voice.fw_service import FasterWhisperService

FAKE_FW = '''
__version__ = "1.2.1-test"
import os
from types import SimpleNamespace

class WhisperModel:
    def __init__(self, path, device="cpu", compute_type="int8", cpu_threads=0, local_files_only=False):
        if not os.path.isfile(os.path.join(path, "model.bin")):
            raise RuntimeError("no model")
        self.path = path
        self.calls = 0

    def transcribe(self, audio, language=None, initial_prompt=None, **kw):
        loud = float(abs(audio).max()) if len(audio) else 0.0
        if loud < 0.01:
            return iter([]), SimpleNamespace(language=language)
        text = "Captain, open Test Plans." if loud > 0.2 else "quiet words"
        seg = SimpleNamespace(text=" " + text, no_speech_prob=0.01, avg_logprob=-0.1)
        return iter([seg]), SimpleNamespace(language=language)

def decode_audio(f, sampling_rate=16000):
    raise RuntimeError("only WAV in the tests")
'''


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wav(seconds: float = 1.0, amplitude: float = 0.5, rate: int = 16000, channels: int = 1) -> bytes:
    import math

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(int(rate * seconds)):
            v = int(amplitude * 32767 * math.sin(2 * math.pi * 440 * i / rate))
            frames += v.to_bytes(2, "little", signed=True) * channels
        w.writeframes(bytes(frames))
    return buf.getvalue()


@pytest.fixture()
def fw_home(tmp_path, monkeypatch):
    home = tmp_path / "faster-whisper"
    lib = home / "lib" / "faster_whisper"
    lib.mkdir(parents=True)
    (lib / "__init__.py").write_text(FAKE_FW, encoding="utf-8")
    (home / "lib" / "ctranslate2.py").write_text('__version__ = "4.8.4-test"\n', encoding="utf-8")
    for name in ("small.en", "base.en"):
        m = home / "models" / name
        m.mkdir(parents=True)
        for f in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
            (m / f).write_text("{}", encoding="utf-8")
    monkeypatch.setenv("QA_FASTER_WHISPER_DIR", str(home))
    return home


@pytest.fixture()
def app_client(tmp_path, fw_home):
    from qa_platform.config import Paths

    paths = Paths(tmp_path / "data").ensure()
    port = free_port()
    (paths.settings_file).write_text(json.dumps({"voice": {
        "stt_engine": "local", "stt_api": "faster_whisper", "stt_url": "http://127.0.0.1:8766", "fw_port": port,
        "agent_command_name": "Captain", "agent_display_name": "Captain", "address_as": "Boss",
        "phrase_style": "conversational"}}), encoding="utf-8")
    app = create_app(paths, secret_backend=MemoryBackend())
    with TestClient(app) as c:
        yield c
    # the application stopped: so did the server it started
    assert FasterWhisperService.probe(port, 1) is None


# ---- the server script ----------------------------------------------------------------------------------------

def test_wav_decoding_resamples_and_mixes_to_16k_mono():
    a = whisper_server.decode_audio(wav(1.0, 0.5, rate=48000, channels=2))
    assert a.dtype.name == "float32" and abs(len(a) - 16000) <= 1 and 0.45 < float(abs(a).max()) <= 0.51


def test_multipart_parsing_without_cgi():
    req = httpx.Request("POST", "http://x/inference", data={"language": "en", "prompt": "Captain"},
                        files={"file": ("a.wav", b"RIFFdata", "audio/wav")})
    body = req.read()
    fields, upload = whisper_server.parse_multipart(req.headers["content-type"], body)
    assert fields == {"language": "en", "prompt": "Captain"} and upload == b"RIFFdata"


# ---- the service, started by the application --------------------------------------------------------------------

def test_starts_with_the_application_and_recognises_speech(app_client):
    rt = app_client.app.state.runtime
    url = rt.voice.fw.ensure_ready(timeout=30)
    st = app_client.get("/api/voice/faster-whisper").json()
    assert st["state"] == "RUNNING" and st["model"] == "small.en" and set(st["models"]) == {"small.en", "base.en"}
    assert st["health"]["engine"] == "faster-whisper" and st["health"]["faster_whisper"] == "1.2.1-test"
    assert rt.voice.config()["stt_url"] == url  # settings point at the service
    r = app_client.post("/api/voice/transcribe", content=wav(1.0, 0.5), headers={"content-type": "audio/wav"})
    assert r.status_code == 200 and r.json()["text"] == "Captain, open Test Plans."
    # silence is "nothing heard", never invented text
    r = app_client.post("/api/voice/transcribe", content=wav(1.0, 0.0), headers={"content-type": "audio/wav"})
    assert r.status_code == 422 and "VOICE_NOTHING_HEARD" in r.text
    # whisper.cpp-compatible answers for other tools
    assert httpx.get(url + "/health").json()["status"] == "ok"
    assert httpx.get(url + "/").status_code == 200
    assert httpx.post(url + "/nope", content=b"x").status_code == 404
    # the voice check and the SPEECH badge
    checks = app_client.post("/api/voice/diagnostics", json={}).json()["checks"]
    fw = next(c for c in checks if c["name"] == "Faster-Whisper speech recognition")
    assert fw["status"] == "PASS" and "small.en" in fw["detail"] and "recognised a test recording" in fw["detail"]
    speech = rt.health.check_speech()
    assert speech["status"] == "PASS" and speech["value"] == "Faster-Whisper"


def test_vocabulary_prompt_carries_the_wake_name(app_client):
    rt = app_client.app.state.runtime
    prompt = rt.voice.vocabulary_prompt(rt.voice.config())
    assert prompt.startswith("Captain, open Test Plans.") and "Test Suites" in prompt


def test_changing_the_model_restarts_the_service(app_client):
    rt = app_client.app.state.runtime
    rt.voice.fw.ensure_ready(timeout=30)
    r = app_client.put("/api/voice/settings", json={"fw_model": "base.en"})
    assert r.status_code == 200 and r.json()["fw_model"] == "base.en"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        st = rt.voice.fw.status()
        if st["state"] == "RUNNING" and (st["health"] or {}).get("model") == "base.en":
            break
        time.sleep(0.3)
    else:
        pytest.fail(f"not restarted with base.en: {rt.voice.fw.status()}")
    # wake name, address and wording are untouched
    cfg = rt.voice.config()
    assert (cfg["agent_command_name"], cfg["address_as"], cfg["phrase_style"]) == ("Captain", "Boss", "conversational")


class _NotFound(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_POST = do_GET

    def log_message(self, *a):
        pass


def test_a_foreign_program_on_the_port_is_left_alone_and_another_port_is_used(tmp_path, fw_home):
    from qa_platform.config import Paths, Settings

    other = HTTPServer(("127.0.0.1", free_port()), _NotFound)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    try:
        paths = Paths(tmp_path / "d").ensure()
        settings = Settings(paths.settings_file)
        settings.update({"voice": {"stt_engine": "local", "stt_api": "faster_whisper", "fw_port": other.server_port}})
        fw = FasterWhisperService(settings, paths.logs)
        try:
            fw.start()
            url = fw.ensure_ready(timeout=30)
            port = settings.section("voice")["fw_port"]
            assert port != other.server_port and url == f"http://127.0.0.1:{port}"
            assert settings.section("voice")["stt_url"] == url
        finally:
            fw.close()
        assert httpx.get(f"http://127.0.0.1:{other.server_port}/").status_code == 404  # still running
    finally:
        other.shutdown()


def test_not_installed_is_reported_clearly(tmp_path, monkeypatch):
    from qa_platform.config import Paths, Settings

    monkeypatch.setenv("QA_FASTER_WHISPER_DIR", str(tmp_path / "missing"))
    paths = Paths(tmp_path / "d").ensure()
    settings = Settings(paths.settings_file)
    fw = FasterWhisperService(settings, paths.logs)
    monkeypatch.setattr(FasterWhisperService, "homes", lambda self: [tmp_path / "missing"])
    st = fw.status()
    assert st["installed"] is False and st["state"] == "STOPPED"
    with pytest.raises(Exception) as e:
        fw.start()
    assert "not installed" in str(e.value.message)


# ---- the false PASS (23.3.6 showed "answered HTTP 404" as PASS) ---------------------------------------------------

@pytest.mark.parametrize("answer, status, words", [
    (httpx.Response(404, text="Not Found"), "FAIL", "HTTP 404: there is no speech recognition at this address"),
    (httpx.Response(200, text="<html>hello</html>"), "FAIL", "not with JSON"),
    (httpx.Response(200, json={"ok": True}), "FAIL", "without a \"text\" field"),
    (httpx.Response(200, json={"text": ""}), "PASS", "recognised a test recording"),
])
def test_other_speech_servers_are_checked_with_a_real_recording(tmp_path, answer, status, words):
    from qa_platform.config import Paths

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return answer

    paths = Paths(tmp_path / "data").ensure()
    paths.settings_file.write_text(json.dumps({"voice": {"stt_engine": "local", "stt_api": "whisper_cpp",
                                                         "stt_url": "http://127.0.0.1:8766"}}), encoding="utf-8")
    app = create_app(paths, secret_backend=MemoryBackend(), stt_transport=httpx.MockTransport(handler))
    with TestClient(app) as c:
        checks = c.post("/api/voice/diagnostics", json={}).json()["checks"]
        local = next(x for x in checks if x["name"] == "Local speech server")
        assert local["status"] == status and words in local["detail"], local
        assert seen and seen[0] == "/inference"  # the real recognition endpoint, not "/"
        badge = c.app.state.runtime.health.check_speech()
        assert badge["status"] == status


def test_unreachable_server_fails(tmp_path):
    from qa_platform.config import Paths

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    paths = Paths(tmp_path / "data").ensure()
    paths.settings_file.write_text(json.dumps({"voice": {"stt_engine": "local", "stt_api": "whisper_cpp",
                                                         "stt_url": "http://127.0.0.1:8766"}}), encoding="utf-8")
    app = create_app(paths, secret_backend=MemoryBackend(), stt_transport=httpx.MockTransport(handler))
    with TestClient(app) as c:
        checks = c.post("/api/voice/diagnostics", json={}).json()["checks"]
        assert next(x for x in checks if x["name"] == "Local speech server")["status"] == "FAIL"


def test_server_script_is_shipped_next_to_the_service():
    assert Path(whisper_server.__file__).name == "whisper_server.py"


def test_stopping_right_after_starting_leaves_no_server_behind(app_client):
    """The service is started in the background when the application starts; an immediate stop must still stop it."""
    rt = app_client.app.state.runtime
    port = rt.voice.fw.config()["port"]
    rt.voice.fw.close()
    time.sleep(1.5)
    assert FasterWhisperService.probe(port, 1) is None
    with pytest.raises(Exception):
        rt.voice.fw.start()


def test_a_port_that_cannot_be_bound_is_skipped(tmp_path, fw_home):
    """WinError 10013: Windows reserved the port (nothing listens there, but binding fails). Another port is used."""
    from qa_platform.config import Paths, Settings

    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))  # bound, not listening: connecting fails and binding fails, like a reserved port
    taken = blocker.getsockname()[1]
    try:
        paths = Paths(tmp_path / "d").ensure()
        settings = Settings(paths.settings_file)
        settings.update({"voice": {"stt_engine": "local", "stt_api": "faster_whisper", "fw_port": taken}})
        fw = FasterWhisperService(settings, paths.logs)
        try:
            url = fw.ensure_ready(timeout=30)
            port = settings.section("voice")["fw_port"]
            assert port != taken and url == f"http://127.0.0.1:{port}" == settings.section("voice")["stt_url"]
        finally:
            fw.close()
    finally:
        blocker.close()


def test_when_the_server_itself_cannot_listen_another_port_is_tried(tmp_path, fw_home, monkeypatch):
    """The laptop case: the check in the application said the port was usable, the server then got WinError 10013."""
    from qa_platform.config import Paths, Settings
    from qa_platform.voice import fw_service

    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    taken = blocker.getsockname()[1]
    real = fw_service.can_bind
    monkeypatch.setattr(fw_service, "can_bind", lambda p: True if p == taken else real(p))
    try:
        paths = Paths(tmp_path / "d").ensure()
        settings = Settings(paths.settings_file)
        settings.update({"voice": {"stt_engine": "local", "stt_api": "faster_whisper", "fw_port": taken}})
        fw = FasterWhisperService(settings, paths.logs)
        try:
            url = fw.ensure_ready(timeout=30)
            port = settings.section("voice")["fw_port"]
            assert port != taken and url == f"http://127.0.0.1:{port}" == settings.section("voice")["stt_url"]
            assert fw.status()["state"] == "RUNNING"
        finally:
            fw.close()
    finally:
        blocker.close()
