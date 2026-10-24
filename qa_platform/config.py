"""Centralized configuration and filesystem layout.

All paths derive from the application root, so a portable copy works from any
folder without administrator rights and without a hard-coded user or drive.

Override the data location with the QA_HOME environment variable.
Secrets (TFS PATs, passwords) are never stored here; see secrets.py.
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = APP_ROOT / "web"


def data_root() -> Path:
    env = os.environ.get("QA_HOME")
    root = Path(env).expanduser() if env else APP_ROOT / "data"
    return root.resolve()


class Paths:
    """Resolved runtime directories. Created lazily on first use."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or data_root()
        self.logs = self.root / "logs"
        self.db_file = self.root / "qa_platform.sqlite3"
        self.settings_file = self.root / "settings.json"
        self.evidence = self.root / "evidence"
        self.scripts = self.root / "automation"
        self.performance = self.root / "performance"
        self.reports = self.root / "reports"
        # Generated Python Playwright framework (Page Objects, flows, tests, test data)
        self.workspace = self.root / "automation-workspace"

    def ensure(self) -> "Paths":
        for p in (self.root, self.logs, self.evidence, self.scripts, self.performance, self.reports):
            p.mkdir(parents=True, exist_ok=True)
        return self


DEFAULT_SETTINGS: dict[str, Any] = {
    "server": {"host": "127.0.0.1", "port": 8765},
    "ui": {"theme": "dark"},
    "tfs": {
        # "online" = Azure DevOps Online, "offline" = local TFS / Azure DevOps Server
        "active_mode": "online",
        "online": {"organization_url": "", "project": "", "api_version": "7.1"},
        "offline": {"collection_url": "", "project": "", "api_version": "6.0", "verify_tls": True},
    },
    "ai": {
        "mode": "on_premise",
        "base_url": "http://127.0.0.1:8080/v1",
        "model": "",
        "timeout_seconds": 120,
    },
    "defects": {"creation_mode": "review"},  # review | automatic | disabled
    "performance": {"jmeter_home": "", "java_home": ""},
    "playback": {"visual_cursor": True, "highlight": True},
    "voice": {
        # builtin = the bundled offline recognizer (Vosk model in runtime/speech, works offline);
        # local = a whisper.cpp / OpenAI-compatible speech server on this PC or network (works offline);
        # browser = the browser's own recognition (Chrome/Edge use an online service); off = typed commands only
        "stt_engine": "builtin",
        "stt_model_dir": "",
        "stt_url": "",
        "stt_api": "whisper_cpp",  # whisper_cpp (/inference) | openai (/audio/transcriptions)
        "stt_model": "",
        "language": "en",
        "speak_replies": True,
        "tts_voice": "",
        "tts_rate": 1.0,
        # radio = replies sound like a pilot on the radio (Windows voice through a radio filter, squelch, static)
        "reply_style": "radio",
        "radio_voice": "",
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


# Key names that hold secrets ("pat", "access_token", "db_password"...), but not "max_tokens".
SECRET_KEY_PATTERN = re.compile(r"(^|[_-])(pat|token|password|passwd|secret|api[_-]?key|apikey)$", re.I)


def _contains_secret_keys(data: Any) -> list[str]:
    found: list[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            if SECRET_KEY_PATTERN.search(k):
                found.append(k)
            found.extend(_contains_secret_keys(v))
    return found


class Settings:
    """JSON-backed settings with defaults, thread-safe read/update."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                stored = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                # Keep the unreadable file for inspection rather than overwriting it.
                backup = self.path.with_suffix(".corrupt.json")
                try:
                    self.path.replace(backup)
                except OSError:
                    pass
                stored = {}
            return _deep_merge(DEFAULT_SETTINGS, stored)
        return copy.deepcopy(DEFAULT_SETTINGS)

    def get(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def section(self, name: str) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data.get(name, {}))

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        bad = _contains_secret_keys(patch)
        if bad:
            raise ValueError(f"Secrets must not be stored in settings: {', '.join(sorted(set(bad)))}")
        with self._lock:
            self._data = _deep_merge(self._data, patch)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            tmp.replace(self.path)
            return copy.deepcopy(self._data)
