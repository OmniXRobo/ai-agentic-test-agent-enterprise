"""Where the companion keeps its files, and its settings (per Windows user, no administrator rights).

    %LOCALAPPDATA%\\SkyGuardAgent\\                 (SKYGUARD_AGENT_HOME overrides it)
        app\\                  the companion, the agent core, whisper_server.py
        runtime\\python\\       Python 3.13 (bundled)
        runtime\\llama\\        llama-server (bundled, CPU)
        runtime\\faster-whisper\\lib\\   Faster-Whisper packages (bundled)
        models\\               qwen GGUF and faster-whisper small.en (copied by Install-Models.ps1, checksum-verified)
        extension\\            the unpacked extension (for Load unpacked)
        data\\                 config.json, users.json, knowledge.sqlite3, vault\\, backups\\, logs\\
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

DEFAULT_PORT = 8790
# The extension's fixed ID (from the "key" in its manifest). An organisation that publishes the extension through
# the Chrome Web Store, Edge Add-ons or its own policy adds that ID here (config.json "extension_ids").
DEFAULT_EXTENSION_IDS = ["fmiiaecpdgbhhhbbkkfhnjfkbmllladb"]

DEFAULTS: dict[str, Any] = {
    "port": DEFAULT_PORT,
    "extension_ids": DEFAULT_EXTENSION_IDS,
    "token_minutes": 15,  # access tokens are short-lived; the extension refreshes them while in use
    "session_hours": 8,  # after this the user signs in again
    "llm": {
        # "auto": use a local Qwen server that is already running (SkyGuard's on 8080), else start the bundled
        # llama-server with the model in models\\; "connect": only the URL; "launch": always the bundled server;
        # "off": no AI (deterministic parsing only)
        "mode": "auto", "url": "http://127.0.0.1:8080/v1", "model": "qwen", "launch_port": 8091,
        "server_path": "", "model_path": "", "context_size": 8192, "threads": 0, "startup_timeout_s": 180,
    },
    "whisper": {
        "mode": "auto", "url": "http://127.0.0.1:8766", "launch_port": 8792, "model_dir": "", "lib_dir": "",
        "language": "en",
    },
    "max_discovery_pages": 25,
}


def home() -> Path:
    env = os.environ.get("SKYGUARD_AGENT_HOME")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or (str(Path.home() / ".local" / "share") if sys.platform != "win32" else "")
    return Path(base or Path.home()) / "SkyGuardAgent"


class Paths:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or home())
        self.data = self.root / "data"
        self.logs = self.data / "logs"
        self.vault = self.data / "vault"
        self.app = self.root / "app"
        self.runtime = self.root / "runtime"
        self.models = self.root / "models"
        self.config_file = self.data / "config.json"
        self.users_file = self.data / "users.json"
        self.devices_file = self.data / "devices.json"
        self.pairing_file = self.data / "pairing-code.txt"

    def ensure(self) -> "Paths":
        for p in (self.data, self.logs, self.vault):
            p.mkdir(parents=True, exist_ok=True)
        return self


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


class Config:
    def __init__(self, paths: Paths) -> None:
        self.paths = paths
        self._lock = threading.Lock()
        self.data = _merge(DEFAULTS, self._read())

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.paths.config_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            stored = _merge(self._read(), changes)
            tmp = self.paths.config_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(stored, indent=2), encoding="utf-8")
            tmp.replace(self.paths.config_file)
            self.data = _merge(DEFAULTS, stored)
            return self.data

    def extension_origins(self) -> set[str]:
        return {f"chrome-extension://{i}" for i in self.data.get("extension_ids") or [] if i and "@" not in i}
