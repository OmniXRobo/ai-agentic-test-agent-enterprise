"""Managed local LLM: start, restart and stop the llama.cpp ``llama-server`` with a Qwen GGUF model on this PC.

The application starts the server only when asked (or at startup when "start with the application" is on), and
stops only the process it started itself: the PID it launched is remembered together with the executable, and a
stop request never touches a server someone else runs on the same port. Navigation and page loads never start,
restart or poll the server. Nothing is downloaded; the server and model are files already on this PC (the offline
package puts them in ``runtime\\ai``). No Administrator rights are needed.
"""

from __future__ import annotations

import json
import re
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
    "context_size": 8192,  # per request (per slot); llama-server gets context_size x parallel
    # 23.3.11: "auto" = 2 slots with a GPU, 1 on a CPU. With one slot llama-server keeps the previous request and
    # reads only the changed end of the next prompt; on a slow CPU reading the prompt is most of the time.
    "parallel": "auto",
    "gpu_layers": "auto",  # 23.3.9: "auto" = all layers on a GPU when llama-server reports one, 0 = CPU only
    "threads": 0,  # 0 = llama-server's own choice
    "extra_args": [],  # more llama-server arguments, e.g. ["--mlock"]
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
        self._basic = False
        self._died = False
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

    # ---- server arguments (23.3.9) -------------------------------------------------------------

    def _server_text(self, server: Path, *args: str) -> str:
        key = (str(server), server.stat().st_mtime if server.exists() else 0, args)
        if key not in _HELP_CACHE:
            try:
                out = subprocess.run([str(server), *args], capture_output=True, timeout=30, cwd=str(server.parent),
                                     stdin=subprocess.DEVNULL, **_no_window())
                _HELP_CACHE[key] = (out.stdout + out.stderr).decode("utf-8", "replace")
            except (OSError, subprocess.SubprocessError):
                _HELP_CACHE[key] = ""
        return _HELP_CACHE[key]

    def gpu_devices(self, server: Path) -> list[str]:
        """GPU devices this llama-server build can use (CUDA, Vulkan, SYCL, ROCm, Metal); empty for a CPU build."""
        if "--list-devices" not in self._server_text(server, "--help"):
            return []
        text = self._server_text(server, "--list-devices")
        return [line.strip() for line in text.splitlines()
                if re.search(r"\b(CUDA|Vulkan|SYCL|ROCm|HIP|Metal|OpenCL)\d*\s*:", line, re.I)]

    def server_args(self, server: Path, model: Path, cfg: dict[str, Any]) -> list[str]:
        """llama-server arguments: the context per request times the parallel slots, all layers on a GPU when one is
        found, and the speed options this llama-server build supports (an older build never gets an option it does
        not know, so it still starts)."""
        helptext = self._server_text(server, "--help")
        gpu = str(cfg.get("gpu_layers", "auto")).strip().lower()
        gpu_args: list[str] = []
        if gpu not in ("0", "", "none", "off", "cpu") and ("--n-gpu-layers" in helptext or "-ngl" in helptext):
            if gpu != "auto":
                gpu_args = ["-ngl", gpu]
            elif self.gpu_devices(server):
                gpu_args = ["-ngl", "99"]
        try:
            parallel = max(1, min(8, int(cfg.get("parallel"))))
        except (TypeError, ValueError):
            parallel = 2 if gpu_args else 1
        ctx = int(cfg["context_size"])
        args = [str(server), "-m", str(model), "--alias", str(cfg["alias"]), "--host", str(cfg["host"]),
                "--port", str(int(cfg["port"])), "-c", str(ctx * parallel), "-np", str(parallel)] + gpu_args
        if int(cfg.get("threads") or 0) > 0:
            args += ["-t", str(int(cfg["threads"]))]
        if "--jinja" in helptext and "--no-jinja" not in helptext:
            args += ["--jinja"]  # older builds: needed to switch a Qwen3 model's thinking off per request
        if "--reasoning-budget" in helptext:
            args += ["--reasoning-budget", "0"]  # no hidden <think> text: every token written is part of the answer
        if "--cache-reuse" in helptext:
            args += ["--cache-reuse", "256"]  # the instructions are the same in every call: read them once
        extra = cfg.get("extra_args") or []
        if isinstance(extra, list):
            args += [str(a) for a in extra]
        return args

    # ---- state -------------------------------------------------------------------------------

    def _url(self, cfg: dict[str, Any]) -> str:
        return f"http://{cfg['host']}:{int(cfg['port'])}/v1"

    def _answering(self, cfg: dict[str, Any], timeout: float = 1.5) -> bool:
        try:
            r = httpx.get(self._url(cfg) + "/models", timeout=timeout, trust_env=False)
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

    def basic_args(self, server: Path, model: Path, cfg: dict[str, Any]) -> list[str]:
        """The 23.3.8 arguments, used when llama-server does not start with the tuned ones."""
        return [str(server), "-m", str(model), "--alias", str(cfg["alias"]), "--host", str(cfg["host"]),
                "--port", str(int(cfg["port"])), "-c", str(int(cfg["context_size"])), "-np", "1"]

    def start(self, wait: bool = True, basic: bool = False) -> dict[str, Any]:
        cfg = self.config()
        with self._lock:
            if self._owned_pid():
                return self.status()
            if self._answering(cfg):
                raise QAError("LLM_PORT_IN_USE", f"Another AI server already answers on {cfg['host']}:{cfg['port']}. "
                                                 "It is not managed by this application, so it was left running. "
                                                 "Use it as is, or choose another port.", status=409)
            server, model = self._resolve(cfg)
            args = self.basic_args(server, model, cfg) if basic else self.server_args(server, model, cfg)
            self._basic = basic
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
            self._state, self._last_error, self._died = "STARTING", None, False
            self.pid_file.write_text(json.dumps({"pid": self._proc.pid, "executable": str(server), "model": str(model),
                                                 "started_at": self._started_at}), encoding="utf-8")
            log.info(f"Managed LLM started: pid {self._proc.pid}, {model.name} on {cfg['host']}:{cfg['port']}: "
                     + " ".join(args[1:]))
            # The AI settings point at the managed server.
            self.settings.update({"ai": {"base_url": self._url(cfg), "model": cfg["alias"]}})
        if wait:
            self._wait_ready(cfg)
            if self._state == "FAILED" and not basic and self._died:
                log.warning("llama-server stopped with the tuned arguments; starting it with the 23.3.8 arguments")
                return self.start(wait=True, basic=True)
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
                    self._died = True
                if not getattr(self, "_basic", True) and threading.current_thread().name == "llm-startup":
                    log.warning("llama-server stopped with the tuned arguments; starting it with the 23.3.8 arguments")
                    try:
                        self._state = "STOPPED"
                        self.start(wait=False, basic=True)
                    except Exception as exc:  # noqa: BLE001 - reported through the state
                        self._state, self._last_error = "FAILED", str(exc)
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


_HELP_CACHE: dict[tuple[Any, ...], str] = {}


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
