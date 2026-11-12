"""The local AI model (Qwen through llama-server) and the local speech recognizer (Faster-Whisper).

Both are reused when they already run on this PC (for example SkyGuard's own servers), or started from the bundled
runtimes with the model files in models\\. They listen on 127.0.0.1 only. Nothing is downloaded at run time.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from skyguard_agent.llm import OpenAICompatibleLLM

log = logging.getLogger("companion.models")
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _no_window() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def _get_json(url: str, timeout: float = 2.0) -> Any:
    try:
        with _NO_PROXY.open(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "null")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _first(*paths: Path) -> Path | None:
    return next((p for p in paths if p and p.exists()), None)


class LocalLLM:
    """Qwen through an OpenAI-compatible llama-server on 127.0.0.1."""

    def __init__(self, cfg: dict[str, Any], root: Path, logs: Path) -> None:
        self.cfg = cfg
        self.root = root
        self.logs = logs
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self.client: OpenAICompatibleLLM | None = None
        self.state = "off" if cfg.get("mode") == "off" else "not started"
        self.detail = ""

    @property
    def name(self) -> str:
        return "qwen (local)"

    @property
    def available(self) -> bool:
        return self.client is not None and self.state == "ready"

    def chat(self, messages: list[dict[str, str]], **kw: Any) -> str:
        if not self.available:
            self.ensure()
        if not self.client:
            raise RuntimeError("The local AI model is not available.")
        return self.client.chat(messages, **kw)

    def _health(self, base: str) -> bool:
        root = base.rstrip("/").removesuffix("/v1")
        h = _get_json(root + "/health", 2.0)
        return isinstance(h, dict) and h.get("status") in ("ok", "no slot available")

    def files(self) -> tuple[Path | None, Path | None]:
        server = Path(self.cfg["server_path"]) if self.cfg.get("server_path") else _first(
            self.root / "runtime" / "llama" / "llama-server.exe", self.root / "runtime" / "llama" / "llama-server")
        model = Path(self.cfg["model_path"]) if self.cfg.get("model_path") else None
        if not model:
            folder = self.root / "models"
            found = sorted(folder.glob("*.gguf")) if folder.is_dir() else []
            model = found[0] if found else None
        return server, model

    def ensure(self) -> dict[str, Any]:
        with self._lock:
            mode = self.cfg.get("mode", "auto")
            if mode == "off":
                self.state, self.client = "off", None
                return self.status()
            if mode in ("auto", "connect") and self._health(self.cfg["url"]):
                self.client = OpenAICompatibleLLM(self.cfg["url"], model=self.cfg.get("model", "qwen"), timeout=180)
                self.state, self.detail = "ready", f"connected to {self.cfg['url']}"
                return self.status()
            if mode == "connect":
                self.state, self.client = "unavailable", None
                self.detail = f"no AI model answers at {self.cfg['url']}"
                return self.status()
            port = int(self.cfg.get("launch_port", 8091))
            url = f"http://127.0.0.1:{port}/v1"
            if self._proc and self._proc.poll() is None and self._health(url):
                self.state = "ready"
                return self.status()
            server, model = self.files()
            if not server or not model:
                self.state, self.client = "unavailable", None
                self.detail = ("the bundled llama-server or the Qwen model file is missing (run Install-Models.ps1, "
                               "or set llm.url to a running server)")
                return self.status()
            if not _port_free(port):
                self.state, self.detail = "unavailable", f"port {port} is in use"
                return self.status()
            args = [str(server), "-m", str(model), "--alias", self.cfg.get("model", "qwen"), "--host", "127.0.0.1",
                    "--port", str(port), "-c", str(int(self.cfg.get("context_size", 8192))), "-np", "1"]
            if int(self.cfg.get("threads") or 0) > 0:
                args += ["-t", str(int(self.cfg["threads"]))]
            out = open(self.logs / "llama-server.log", "ab")
            self._proc = subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                          cwd=str(server.parent), **_no_window())
            self.state = "starting"
            deadline = time.time() + float(self.cfg.get("startup_timeout_s", 180))
            while time.time() < deadline:
                if self._proc.poll() is not None:
                    self.state, self.detail = "unavailable", "llama-server stopped; see logs\\llama-server.log"
                    return self.status()
                if self._health(url):
                    self.client = OpenAICompatibleLLM(url, model=self.cfg.get("model", "qwen"), timeout=180)
                    self.state, self.detail = "ready", f"started bundled llama-server with {model.name}"
                    return self.status()
                time.sleep(1)
            self.state, self.detail = "unavailable", "llama-server did not become ready in time"
            return self.status()

    def status(self) -> dict[str, Any]:
        return {"state": self.state, "detail": self.detail, "mode": self.cfg.get("mode")}

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None


def _multipart(fields: dict[str, str], file_field: str, filename: str, data: bytes, content_type: str
               ) -> tuple[bytes, str]:
    boundary = "----sg" + secrets.token_hex(12)
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{filename}\"\r\n"
                 f"Content-Type: {content_type}\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class LocalWhisper:
    """Faster-Whisper through whisper_server.py (the same server SkyGuard uses) on 127.0.0.1."""

    def __init__(self, cfg: dict[str, Any], root: Path, logs: Path, python: str | None = None) -> None:
        self.cfg = cfg
        self.root = root
        self.logs = logs
        self.python = python or sys.executable
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self.url: str | None = None
        self.state = "off" if cfg.get("mode") == "off" else "not started"
        self.detail = ""

    def _ok(self, url: str) -> bool:
        h = _get_json(url.rstrip("/") + "/health", 2.0)
        return isinstance(h, dict) and h.get("status") == "ok"

    def files(self) -> tuple[Path | None, Path | None, Path | None]:
        script = _first(self.root / "app" / "whisper_server.py", Path(__file__).resolve().parents[1] / "whisper_server.py")
        lib = Path(self.cfg["lib_dir"]) if self.cfg.get("lib_dir") else self.root / "runtime" / "faster-whisper" / "lib"
        model = Path(self.cfg["model_dir"]) if self.cfg.get("model_dir") else self.root / "models" / "faster-whisper-small.en"
        return script, (lib if lib.is_dir() else None), (model if (model / "model.bin").exists() else None)

    def ensure(self) -> dict[str, Any]:
        with self._lock:
            mode = self.cfg.get("mode", "auto")
            if mode == "off":
                self.state = "off"
                return self.status()
            if mode in ("auto", "connect") and self._ok(self.cfg["url"]):
                self.url, self.state, self.detail = self.cfg["url"], "ready", f"connected to {self.cfg['url']}"
                return self.status()
            if mode == "connect":
                self.state, self.detail = "unavailable", f"no speech server answers at {self.cfg['url']}"
                return self.status()
            port = int(self.cfg.get("launch_port", 8792))
            url = f"http://127.0.0.1:{port}"
            if self._proc and self._proc.poll() is None:
                if self._ok(url):
                    self.url, self.state = url, "ready"
                return self.status()
            script, lib, model = self.files()
            if not (script and lib and model):
                self.state = "unavailable"
                self.detail = ("Faster-Whisper or its small.en model is missing (run Install-Models.ps1, or set "
                               "whisper.url to a running speech server)")
                return self.status()
            out = open(self.logs / "whisper-server.log", "ab")
            env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
            self._proc = subprocess.Popen([self.python, "-E", "-s", str(script), "--lib", str(lib), "--model",
                                           str(model), "--port", str(port)], stdout=out, stderr=subprocess.STDOUT,
                                          stdin=subprocess.DEVNULL, env=env, **_no_window())
            self.state, self.url = "loading", url
            return self.status()

    def transcribe(self, audio: bytes, content_type: str = "audio/wav") -> str:
        if self.state != "ready":
            self.ensure()
            deadline = time.time() + 60
            while self.state == "loading" and time.time() < deadline:
                if self.url and self._ok(self.url):
                    self.state = "ready"
                    break
                time.sleep(1)
        if self.state != "ready" or not self.url:
            raise RuntimeError("Speech recognition is not available: " + (self.detail or self.state))
        body, ctype = _multipart({"language": self.cfg.get("language", "en"), "temperature": "0"}, "file",
                                 "speech.wav", audio, content_type)
        req = urllib.request.Request(self.url.rstrip("/") + "/inference", data=body, method="POST",
                                     headers={"Content-Type": ctype})
        with _NO_PROXY.open(req, timeout=120) as r:
            return str(json.loads(r.read().decode("utf-8")).get("text", "")).strip()

    def status(self) -> dict[str, Any]:
        if self.state == "loading" and self.url and self._ok(self.url):
            self.state, self.detail = "ready", "bundled Faster-Whisper started"
        return {"state": self.state, "detail": self.detail, "mode": self.cfg.get("mode")}

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
