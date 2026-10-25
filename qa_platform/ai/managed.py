"""Managed local LLM: start, restart and stop the llama.cpp ``llama-server`` with a Qwen GGUF model on this PC.

The application starts the server only when asked (or at startup when "start with the application" is on), and
stops only the process it started itself: the PID it launched is remembered together with the executable, and a
stop request never touches a server someone else runs on the same port. Navigation and page loads never start,
restart or poll the server. Nothing is downloaded; the server and model are files already on this PC (the offline
package puts them in ``runtime\\ai``). No Administrator rights are needed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from ..clock import now_iso
from ..config import APP_ROOT, Paths, Settings
from ..errors import QAError
from ..logging_setup import get_logger

log = get_logger("ai.managed")

DEFAULTS: dict[str, Any] = {
    "server_path": "",  # llama-server(.exe); blank = found automatically
    "model_path": "",  # .gguf file; blank = found automatically
    "alias": "qwen",  # model name the server reports (what the AI settings use)
    "host": "127.0.0.1",
    "port": 8080,
    "context_size": 8192,
    "startup_timeout_s": 180,
    "auto_start": False,
}
SERVER_NAMES = ("llama-server.exe", "llama-server")


def _no_window() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


class ManagedLLM:
    def __init__(self, settings: Settings, paths: Paths) -> None:
        self.settings = settings
        self.paths = paths
        self._lock = threading.RLock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._started_at: str | None = None
        self._last_error: str | None = None
        self._state = "STOPPED"  # STOPPED | STARTING | RUNNING | FAILED
        self.pid_file = paths.root / "managed-llm.json"
        self.log_file = paths.logs / "llama-server.log"

    # ---- settings and discovery --------------------------------------------------------------

    def config(self) -> dict[str, Any]:
        cfg = {**DEFAULTS, **(self.settings.section("ai").get("managed") or {})}
        return cfg

    def search_folders(self) -> list[Path]:
        """Where the server and models are looked for: next to the application and in the data folder."""
        env = os.environ.get("QA_AI_DIR")
        folders = [Path(env)] if env else []
        folders += [APP_ROOT / "runtime" / "ai", APP_ROOT.parent / "runtime" / "ai", self.paths.root / "ai"]
        return [f for f in folders if f.is_dir()]

    def detect(self) -> dict[str, Any]:
        servers: list[str] = []
        models: list[dict[str, Any]] = []
        for folder in self.search_folders():
            for name in SERVER_NAMES:
                servers += [str(p) for p in folder.rglob(name) if p.is_file()]
            models += [{"path": str(p), "name": p.name, "size_mb": round(p.stat().st_size / 1048576)}
                       for p in sorted(folder.rglob("*.gguf")) if p.is_file() and "mmproj" not in p.name.lower()]
        return {"servers": sorted(set(servers)), "models": models, "folders": [str(f) for f in self.search_folders()]}

    def _resolve(self, cfg: dict[str, Any]) -> tuple[Path, Path]:
        found = self.detect() if not (cfg["server_path"] and cfg["model_path"]) else {"servers": [], "models": []}
        server = Path(cfg["server_path"]) if cfg["server_path"] else (Path(found["servers"][0]) if found["servers"] else None)
        model = Path(cfg["model_path"]) if cfg["model_path"] else (Path(found["models"][0]["path"]) if found["models"] else None)
        if not server or not server.is_file():
            raise QAError("LLM_SERVER_MISSING", "llama-server was not found. Choose its file under Managed Local LLM, "
                                                "or use Auto Detect after installing the offline package with the AI runtime.",
                          status=409)
        if not model or not model.is_file() or model.suffix.lower() != ".gguf":
            raise QAError("LLM_MODEL_MISSING", "No GGUF model file was found. Choose the Qwen .gguf file under Managed Local LLM.",
                          status=409)
        return server, model

    # ---- state -------------------------------------------------------------------------------

    def _url(self, cfg: dict[str, Any]) -> str:
        return f"http://{cfg['host']}:{int(cfg['port'])}/v1"

    def _answering(self, cfg: dict[str, Any], timeout: float = 1.5) -> bool:
        try:
            r = httpx.get(self._url(cfg) + "/models", timeout=timeout)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def _owned_pid(self) -> int | None:
        """PID of a server this application started earlier (also after a restart of the application)."""
        if self._proc is not None and self._proc.poll() is None:
            return self._proc.pid
        try:
            saved = json.loads(self.pid_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        pid = int(saved.get("pid") or 0)
        return pid if pid and _alive(pid, saved.get("executable", "")) else None

    def status(self) -> dict[str, Any]:
        cfg = self.config()
        with self._lock:
            pid = self._owned_pid()
            if self._state == "STARTING":
                state = "STARTING"
            elif pid:
                state = "RUNNING"
            elif self._state == "FAILED":
                state = "FAILED"
            else:
                state = "STOPPED"
            if not pid and self.pid_file.exists() and self._state != "STARTING":
                self.pid_file.unlink(missing_ok=True)
        external = not pid and state != "STARTING" and self._answering(cfg, 0.8)
        return {"state": state, "pid": pid, "url": self._url(cfg), "alias": cfg["alias"], "started_at": self._started_at,
                "external_server": external, "error": self._last_error if state == "FAILED" else None,
                "log_tail": self.log_tail() if state in ("FAILED", "STARTING") else "", "config": cfg}

    def log_tail(self, lines: int = 12) -> str:
        try:
            return "\n".join(self.log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    # ---- control -----------------------------------------------------------------------------

    def start(self, wait: bool = True) -> dict[str, Any]:
        cfg = self.config()
        with self._lock:
            if self._owned_pid():
                return self.status()
            if self._answering(cfg):
                raise QAError("LLM_PORT_IN_USE", f"Another AI server already answers on {cfg['host']}:{cfg['port']}. "
                                                 "It is not managed by this application, so it was left running. "
                                                 "Use it as is, or choose another port.", status=409)
            server, model = self._resolve(cfg)
            args = [str(server), "-m", str(model), "--alias", str(cfg["alias"]), "--host", str(cfg["host"]),
                    "--port", str(int(cfg["port"])), "-c", str(int(cfg["context_size"]))]
            self.paths.logs.mkdir(parents=True, exist_ok=True)
            out = open(self.log_file, "wb")  # noqa: SIM115 - handed to the child process
            try:
                self._proc = subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                              cwd=str(server.parent), **_no_window())
            except OSError as exc:
                out.close()
                self._state, self._last_error = "FAILED", f"llama-server could not be started: {exc}"
                raise QAError("LLM_START_FAILED", self._last_error, status=502) from exc
            out.close()
            self._started_at = now_iso()
            self._state, self._last_error = "STARTING", None
            self.pid_file.write_text(json.dumps({"pid": self._proc.pid, "executable": str(server), "model": str(model),
                                                 "started_at": self._started_at}), encoding="utf-8")
            log.info(f"Managed LLM started: pid {self._proc.pid}, {model.name} on {cfg['host']}:{cfg['port']}")
            # The AI settings point at the managed server.
            self.settings.update({"ai": {"base_url": self._url(cfg), "model": cfg["alias"]}})
        if wait:
            self._wait_ready(cfg)
        else:
            threading.Thread(target=self._wait_ready, args=(cfg,), name="llm-startup", daemon=True).start()
        return self.status()

    def _wait_ready(self, cfg: dict[str, Any]) -> None:
        deadline = time.monotonic() + float(cfg["startup_timeout_s"])
        proc = self._proc
        while time.monotonic() < deadline:
            if proc is None or proc.poll() is not None:
                with self._lock:
                    self._state = "FAILED"
                    self._last_error = "llama-server stopped while loading the model. See the log below."
                    self.pid_file.unlink(missing_ok=True)
                return
            if self._answering(cfg, 2):
                with self._lock:
                    self._state = "RUNNING"
                return
            time.sleep(1)
        with self._lock:
            self._state = "FAILED"
            self._last_error = f"The model did not finish loading within {cfg['startup_timeout_s']} seconds."
        self.stop()

    def stop(self) -> dict[str, Any]:
        """Stop the server this application started. Any other process is never touched."""
        with self._lock:
            pid = self._owned_pid()
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait(timeout=5)
            elif pid:
                _terminate(pid)
            self._proc = None
            self.pid_file.unlink(missing_ok=True)
            if self._state != "FAILED":
                self._state = "STOPPED"
            if pid:
                log.info(f"Managed LLM stopped (pid {pid})")
        return self.status()

    def restart(self) -> dict[str, Any]:
        self.stop()
        self._state = "STOPPED"
        return self.start()

    def close(self) -> None:
        """Called when the application shuts down: stops only the server it started in this session."""
        if self._proc is not None and self._proc.poll() is None:
            self.stop()


def _alive(pid: int, executable: str) -> bool:
    """True if ``pid`` is running and is the llama-server this application started (not a reused PID)."""
    exe_name = Path(executable).name.lower()
    if sys.platform == "win32":
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                 text=True, timeout=5, **_no_window()).stdout.lower()
        except (OSError, subprocess.SubprocessError):
            return False
        return exe_name in out
    try:
        parts = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[:2]
    except OSError:
        return False
    return any(Path(p.decode(errors="replace")).name.lower() == exe_name for p in parts)


def _terminate(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15, **_no_window())
    else:
        import signal

        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
