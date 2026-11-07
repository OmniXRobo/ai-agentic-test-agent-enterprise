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
    # 23.3.34: Network access (Settings > Users): colleagues open http://<this PC>:<port>/ and sign in. Off = this PC only.
    "network": {"enabled": False},
    "ui": {"theme": "dark", "brand_name": "SkyGuard QA", "brand_tagline": "Every release, cleared for takeoff.",
           "logo_path": "",  # 23.3.30: product name, tagline and logo (Preferences)
           # 23.3.35: the desktop icon opens the dashboard as a browser tab with an address bar ("browser") or as its
           # own app window without one ("app"); Microsoft Edge first either way
           "open_as": "browser"},
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
        "generation_timeout_seconds": 600,
    },
    "defects": {"creation_mode": "review"},  # review | automatic | disabled
    "performance": {"jmeter_home": "", "java_home": "", "min_free_disk_mb": 1024},
    # Visible playback: the tactical cursor moves to each element, the element is highlighted, then Playwright acts.
    "playback": {"visual_cursor": True, "highlight": True, "cursor_speed_ms": 450, "click_sound": True,
                 "click_volume": 0.35},
    "voice": {
        # builtin = the bundled offline recognizer (Vosk model in runtime/speech, works offline);
        # local = a whisper.cpp / OpenAI-compatible speech server on this PC or network (works offline);
        # browser = the browser's own recognition (Chrome/Edge use an online service); off = typed commands only
        "stt_engine": "builtin",
        "stt_model_dir": "",
        "stt_url": "",
        # whisper_cpp (/inference) | openai (/audio/transcriptions) |
        # faster_whisper (23.3.7: the Faster-Whisper server this application starts itself, see voice/fw_service.py)
        "stt_api": "whisper_cpp",
        "fw_model": "small.en",  # a folder in runtime/faster-whisper/models
        "fw_port": 8766,  # 127.0.0.1 only; the next free port is used if another program has it
        "fw_compute_type": "int8",
        "fw_threads": 0,  # 0 = CTranslate2 decides
        "fw_beam_size": 5,
        "fw_auto_start": True,
        "stt_model": "",
        "language": "en",
        "speak_replies": True,
        "tts_voice": "",
        "tts_rate": 1.0,
        # radio = replies sound like a pilot on the radio (Windows voice through a radio filter, squelch, static)
        "reply_style": "radio",
        "radio_voice": "",
        # Voice & Command Agent: names are settings, never code. The display name may be in any script (e.g. Arabic);
        # the command name is what the recognizer listens for and the voice says, in Latin letters.
        "agent_display_name": "HAMI",
        "agent_command_name": "Hami",
        "voice_enabled": True,  # microphone input on/off (typed commands always work)
        "listen_mode": "push_to_talk",  # push_to_talk | wake_name (hands-free: say the command name first)
        # Global voice agent (23.3): listen for the command name on every page, started automatically when the
        # dashboard opens (the browser still asks for the microphone). Off = press the mic to talk.
        "wake_listening": True,
        "follow_up_seconds": 8,  # after "<name>." alone (or a question from the agent) the next sentence needs no name
        "address_as": "Boss",
        # 23.3.4: test applications where the agent may submit forms, pay, delete... without asking first (names as in
        # Application Credentials). Empty = always ask before a consequential action.
        "approved_test_apps": [],
        # 23.3.6: what the voice agent may do (viewer | tester | full) and how long transcripts are kept (0 = keep)
        "voice_role": "full",
        "keep_transcripts": True,
        "transcript_days": 0,  # how the agent addresses you ("Yes, Boss. I'm listening."); empty = no form of address
        # standard ("OK, I will do your task now.") | tactical ("Command received.") |
        # conversational ("Understood, Boss. I'm opening Test Plans now.")
        "phrase_style": "conversational",
        "radio_intensity": 0.5,  # 0 = clean voice .. 1 = strong radio processing
        "wireless_tone": True,  # radio-open and radio-close beeps
        "static_volume": 0.25,  # wireless/static texture under the voice
        "ambience_enabled": False,  # low tactical command-center ambience
        "ambience_volume": 0.2,
        "voice_volume": 0.9,
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
