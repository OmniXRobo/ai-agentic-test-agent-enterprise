"""Faster-Whisper speech recognition, run by the application as a local service.

When Speech recognition is the local speech server with the server type "Faster-Whisper on this PC", the application
starts ``whisper_server.py`` with its own Python when it starts, keeps it running, restarts it if it stopped, and
stops it again when the application stops. The server listens on 127.0.0.1 only (port 8766 by default; the next
free port if another program uses it) and works without Internet: the packages and the model are folders on this PC,
installed by patch 23.3.7 in ``runtime\\faster-whisper``:

  runtime\\faster-whisper\\lib\\               faster-whisper, ctranslate2, onnxruntime, av, numpy, tokenizers ...
  runtime\\faster-whisper\\models\\small.en\\  model.bin, config.json, tokenizer.json, vocabulary.txt

Only a server this application started (or an earlier start of it left running) is ever stopped: a process is
recognised by the answer of its own /health, never by its port alone.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from ..clock import now_iso
from ..config import APP_ROOT, Settings
from ..errors import QAError
from ..logging_setup import get_logger

log = get_logger("voice.faster_whisper")

ENGINE = "faster-whisper"
API = "faster_whisper"  # the stt_api value that selects this service
DEFAULT_PORT = 8766
PORT_RANGE = range(8766, 8786)
# Windows can reserve whole port blocks (Hyper-V, WSL, Docker: "netsh interface ipv4 show excludedportrange
# protocol=tcp"); binding there fails with WinError 10013 although nothing listens. Try these next, then any free port.
FALLBACK_PORTS = list(PORT_RANGE) + list(range(18766, 18786)) + list(range(28766, 28776))
PREFERRED_MODELS = ("small.en", "distil-small.en", "base.en", "medium.en", "small", "base", "distil-large-v3",
                    "large-v3-turbo", "tiny.en")
SERVER_SCRIPT = Path(__file__).with_name("whisper_server.py")
MODEL_FILES = ("model.bin", "config.json")


def can_bind(port: int) -> bool:
    """True if this process may listen on 127.0.0.1:port (free, and not in a range Windows reserved).

    Tried both plainly and with SO_REUSEADDR, the option Python's HTTP server uses: on Windows that one is refused
    (WinError 10013) when another program already holds the port, even where a plain bind looks fine."""
    for reuse in (False, True):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if reuse:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                return False
    return True


def free_port(avoid: int | set[int]) -> int | None:
    bad = avoid if isinstance(avoid, set) else {avoid}
    for p in FALLBACK_PORTS:
        if p not in bad and FasterWhisperService.probe(p, 0.5) is None and can_bind(p):
            return p
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:  # last resort: a port Windows hands out
        try:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])
        except OSError:
            return None


def _no_window() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def is_model(folder: Path) -> bool:
    return folder.is_dir() and all((folder / f).is_file() for f in MODEL_FILES) and \
        any((folder / f).is_file() for f in ("tokenizer.json", "vocabulary.txt", "vocabulary.json"))


class FasterWhisperService:
    def __init__(self, settings: Settings, logs: Path | None = None) -> None:
        self.settings = settings
        self.data_root = settings.path.parent
        self.logs = logs or self.data_root / "logs"
        self.pid_file = self.data_root / "faster-whisper.json"
        self.log_file = self.logs / "faster-whisper.log"
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.RLock()
        self._started_at: str | None = None
        self._last_error = ""
        self._closed = False  # the application is stopping: never start a server after that

    # ---- settings and files ------------------------------------------------------------------

    def config(self) -> dict[str, Any]:
        v = self.settings.section("voice")
        return {"model": str(v.get("fw_model") or "small.en"), "port": int(v.get("fw_port") or DEFAULT_PORT),
                "compute_type": str(v.get("fw_compute_type") or "int8"), "threads": int(v.get("fw_threads") or 0),
                "beam_size": int(v.get("fw_beam_size") or 5), "language": str(v.get("language") or "en"),
                "auto_start": v.get("fw_auto_start", True) is not False}

    def selected(self) -> bool:
        v = self.settings.section("voice")
        return v.get("stt_engine") == "local" and v.get("stt_api") == API

    def homes(self) -> list[Path]:
        env = os.environ.get("QA_FASTER_WHISPER_DIR")
        out = [Path(env)] if env else []
        return out + [APP_ROOT / "runtime" / "faster-whisper", APP_ROOT.parent / "runtime" / "faster-whisper",
                      self.data_root / "faster-whisper"]

    def home(self) -> Path | None:
        for h in self.homes():
            if (h / "lib" / "faster_whisper").is_dir():
                return h
        return None

    def models(self) -> list[str]:
        names: list[str] = []
        for h in self.homes():
            folder = h / "models"
            if folder.is_dir():
                names += [p.name for p in sorted(folder.iterdir()) if is_model(p)]
        return list(dict.fromkeys(names))

    def model_dir(self, name: str | None = None) -> Path | None:
        wanted = name or self.config()["model"]
        if wanted and (Path(wanted).is_absolute()) and is_model(Path(wanted)):
            return Path(wanted)
        installed = self.models()
        order = [wanted] + [m for m in PREFERRED_MODELS if m != wanted] + installed
        for m in order:
            if m in installed:
                for h in self.homes():
                    if is_model(h / "models" / m):
                        return h / "models" / m
        return None

    def url(self, port: int | None = None) -> str:
        return f"http://127.0.0.1:{port or self.config()['port']}"

    # ---- probing -----------------------------------------------------------------------------

    @staticmethod
    def probe(port: int, timeout: float = 1.5) -> dict[str, Any] | None:
        """/health of whatever listens on the port: ours ({engine: faster-whisper, ...}), {"foreign": code} or None."""
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=min(timeout, 1.0)):
                pass
        except OSError:
            return None
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=timeout, trust_env=False)
        except httpx.HTTPError as exc:
            return {"foreign": 0, "detail": type(exc).__name__}
        try:
            body = r.json()
        except ValueError:
            body = {}
        if isinstance(body, dict) and body.get("engine") == ENGINE:
            return body
        return {"foreign": r.status_code}

    def _owned_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ---- control -----------------------------------------------------------------------------

    def start(self) -> dict[str, Any]:
        """Start the server unless it already runs. Returns at once; the model loads in the background."""
        with self._lock:
            cfg = self.config()
            if self._closed:
                raise QAError("FW_CLOSED", "The application is stopping.", status=503)
            if self._owned_alive():
                return self.status()
            home = self.home()
            if not home:
                self._last_error = ("Faster-Whisper is not installed in runtime\\faster-whisper. Run the 23.3.7 patch "
                                    "(APPLY_PATCH.ps1) again.")
                raise QAError("FW_NOT_INSTALLED", self._last_error, status=409)
            model = self.model_dir()
            if not model:
                self._last_error = ("No Faster-Whisper model is installed in runtime\\faster-whisper\\models. Run the "
                                    "23.3.7 patch with -ModelZip <the faster-whisper model zip>, or with Internet.")
                raise QAError("FW_MODEL_MISSING", self._last_error, status=409)
            port = cfg["port"]
            found = self.probe(port)
            if found and "foreign" not in found:
                if Path(str(found.get("model_path") or "")).name == model.name or found.get("model") == model.name:
                    self._sync_url(port)
                    return self.status()  # ours, left from an earlier start of the application
                self._terminate(int(found.get("pid") or 0))  # ours, but another model: replace it
                time.sleep(1)
            if (found and "foreign" in found) or (not found and not can_bind(port)):
                why = (f"answers HTTP {found['foreign']} (another program)" if found else
                       "cannot be used (reserved by Windows or held by another program)")
                free = free_port(port)
                if free is None:
                    self._last_error = f"Port {port} {why} and no free port was found."
                    raise QAError("FW_PORT_IN_USE", self._last_error, status=409)
                log.warning(f"Port {port} {why}; using port {free}")
                port = free
                self.settings.update({"voice": {"fw_port": port}})
            self.logs.mkdir(parents=True, exist_ok=True)
            tried: set[int] = set()
            for _attempt in range(8):
                args = [sys.executable, "-E", "-s", str(SERVER_SCRIPT), "--lib", str(home / "lib"), "--model",
                        str(model), "--host", "127.0.0.1", "--port", str(port), "--compute-type", cfg["compute_type"],
                        "--threads", str(cfg["threads"]), "--beam-size", str(cfg["beam_size"]),
                        "--language", cfg["language"].split("-")[0] or "en", "--log", str(self.log_file)]
                try:
                    self._proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                  stderr=subprocess.DEVNULL, cwd=str(home), **_no_window())
                except OSError as exc:
                    self._last_error = f"The Faster-Whisper server could not be started: {exc}"
                    raise QAError("FW_START_FAILED", self._last_error, status=502) from exc
                if self._listening(port):
                    break
                # It could not listen there (WinError 10013/10048: reserved by Windows or held by another program).
                tried.add(port)
                nxt = free_port(tried)
                log.warning(f"Faster-Whisper could not listen on port {port}; trying port {nxt}")
                if nxt is None:
                    self._last_error = f"No port could be used for Faster-Whisper (tried {sorted(tried)})."
                    raise QAError("FW_PORT_IN_USE", self._last_error, status=409)
                port = nxt
            else:
                self._last_error = f"Faster-Whisper could not listen on any port (tried {sorted(tried)})."
                raise QAError("FW_PORT_IN_USE", self._last_error, status=409)
            self._started_at = now_iso()
            self._last_error = ""
            self.pid_file.write_text(json.dumps({"pid": self._proc.pid, "port": port, "model": str(model),
                                                 "started_at": self._started_at}), encoding="utf-8")
            self._sync_url(port)
            log.info(f"Faster-Whisper server started: pid {self._proc.pid}, {model.name} on 127.0.0.1:{port}")
            return self.status()

    def _listening(self, port: int, timeout: float = 20.0) -> bool:
        """Wait until the new server accepts connections (True) or exits because it cannot use the port (False)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc is None or self._proc.poll() is not None:
                return False
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    pass
                time.sleep(0.5)  # a server that could not bind exits at once; whoever answered was someone else
                return self._proc.poll() is None
            except OSError:
                time.sleep(0.2)
        return self._proc is not None and self._proc.poll() is None  # slow PC: still running, assume it is fine

    def _sync_url(self, port: int) -> None:
        """Voice settings point at the running service (only while Faster-Whisper is the chosen server type)."""
        v = self.settings.section("voice")
        patch: dict[str, Any] = {}
        if int(v.get("fw_port") or DEFAULT_PORT) != port:
            patch["fw_port"] = port
        if v.get("stt_api") == API and (v.get("stt_url") or "").rstrip("/") != self.url(port):
            patch["stt_url"] = self.url(port)
        if patch:
            self.settings.update({"voice": patch})

    def start_in_background(self) -> None:
        def run() -> None:
            try:
                self.start()
            except QAError as exc:
                log.warning(f"Faster-Whisper not started: {exc.message}")
            except Exception as exc:  # the application still starts; Check Voice Setup shows why
                self._last_error = f"{type(exc).__name__}: {exc}"
                log.warning(f"Faster-Whisper not started: {self._last_error}")

        threading.Thread(target=run, name="faster-whisper-start", daemon=True).start()

    def ensure_ready(self, timeout: float = 120.0) -> str:
        """The URL of a running server with its model loaded; starts it when needed. Raises a clear QAError."""
        port = self.config()["port"]
        found = self.probe(port)
        if not found or "foreign" in found:
            self.start()
            port = self.config()["port"]
        deadline = time.monotonic() + timeout
        while True:
            found = self.probe(port, 3)
            if found and found.get("status") == "ok":
                # The server can answer before the start that launched it has pointed the voice settings at it
                # (it waits 0.5 s to be sure it kept the port): point them now, so callers never see the old URL.
                self._sync_url(port)
                return self.url(port)
            if found and found.get("status") == "error":
                raise QAError("FW_MODEL_FAILED", f"Faster-Whisper could not load the speech model: {found.get('error')}",
                              status=502)
            if self._proc is not None and self._proc.poll() is not None:
                raise QAError("FW_STOPPED", "The Faster-Whisper server stopped while starting. " + self.log_tail(4),
                              status=502)
            if time.monotonic() > deadline:
                raise QAError("FW_LOADING", "The speech model is still loading. Try again in a few seconds.", status=503)
            time.sleep(0.5)

    def status(self) -> dict[str, Any]:
        cfg = self.config()
        found = self.probe(cfg["port"], 2)
        ours = bool(found) and "foreign" not in (found or {})
        state = (found or {}).get("status") if ours else ("STARTING" if self._owned_alive() else "STOPPED")
        model = self.model_dir()
        return {"installed": self.home() is not None, "home": str(self.home() or ""), "models": self.models(),
                "model": model.name if model else "", "model_path": str(model or ""), "url": self.url(cfg["port"]),
                "port": cfg["port"], "state": {"ok": "RUNNING", "loading": "LOADING", "error": "FAILED"}.get(
                    str(state), str(state)), "health": found if ours else None,
                "foreign_server": (found or {}).get("foreign") if found and not ours else None,
                "pid": (found or {}).get("pid") if ours else None, "started_at": self._started_at,
                "error": (found or {}).get("error") if ours and state == "error" else self._last_error,
                "selected": self.selected(), "log_file": str(self.log_file)}

    def log_tail(self, lines: int = 8) -> str:
        try:
            return "\n".join(self.log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    @staticmethod
    def _terminate(pid: int) -> None:
        if not pid:
            return
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15, **_no_window())
        else:
            import signal

            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

    def stop(self) -> dict[str, Any]:
        """Stop the Faster-Whisper server (only ours: identified by its own /health answer)."""
        with self._lock:
            if self._owned_alive():
                assert self._proc is not None
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait(timeout=5)
            else:
                found = self.probe(self.config()["port"], 1.5)
                if found and "foreign" not in found:
                    self._terminate(int(found.get("pid") or 0))
            self._proc = None
            self.pid_file.unlink(missing_ok=True)
        return self.status()

    def restart(self) -> dict[str, Any]:
        self.stop()
        time.sleep(0.5)
        return self.start()

    def close(self) -> None:
        """Called when the application stops: stops the server this application started.

        Taking the lock waits for a start that is still in progress, so a server started at the last moment is
        stopped too and no later start can happen."""
        with self._lock:
            self._closed = True
            if self._owned_alive():
                try:
                    self.stop()
                except Exception:  # never block the shutdown
                    pass
