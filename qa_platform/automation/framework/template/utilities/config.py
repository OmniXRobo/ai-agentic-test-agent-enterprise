"""Run settings for the automation framework.

Everything comes from environment variables with safe defaults, and every path is
relative to this workspace folder, so the framework runs from any folder on any
Windows PC (no user names or drive letters are stored anywhere).

    BASE_URL              application address (default: the one saved with each test's data)
    BROWSER               chromium (default), msedge, chrome, firefox or webkit
    HEADLESS              1 to run without showing the browser
    QA_VISUAL             1 to show the cursor, highlights and current action (default: on when not headless)
    QA_VISUAL_DELAY_MS    pause before each action while visual playback is on (default 350)
    QA_TIMEOUT_MS         default wait for an element (default 10000)
    QA_TRACE              1 to save a Playwright trace per test
    QA_VIDEO              1 to record a video per test
    QA_EVIDENCE_DIR       evidence folder (default: execution-results/<date-time>)
    QA_EVENTS_FILE        JSON-lines progress file read by the Enterprise QA Runtime (optional)
    QA_COMBINATION        run only the test data combination with this name
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


BROWSER = os.environ.get("BROWSER", "chromium").strip().lower() or "chromium"
HEADLESS = _flag("HEADLESS", False)
VISUAL = _flag("QA_VISUAL", not HEADLESS)
VISUAL_DELAY_MS = _int("QA_VISUAL_DELAY_MS", 350)
TIMEOUT_MS = _int("QA_TIMEOUT_MS", 10000)
TRACE = _flag("QA_TRACE", False)
VIDEO = _flag("QA_VIDEO", False)
EVENTS_FILE = os.environ.get("QA_EVENTS_FILE") or None
COMBINATION = os.environ.get("QA_COMBINATION") or None
RUN_ID = os.environ.get("QA_RUN_ID") or None

_evidence: Path | None = None


def base_url(default: str = "") -> str:
    return (os.environ.get("BASE_URL") or default or "").strip()


def evidence_dir() -> Path:
    """Folder for screenshots, traces and logs of this run (created on first use)."""
    global _evidence
    if _evidence is None:
        configured = os.environ.get("QA_EVIDENCE_DIR")
        if configured:
            _evidence = Path(configured)
        else:
            _evidence = ROOT / "execution-results" / datetime.now().strftime("%Y%m%d-%H%M%S")
        _evidence.mkdir(parents=True, exist_ok=True)
    return _evidence


def asset(name: str) -> str:
    """Text of a helper script in utilities/js (fingerprints, visual playback overlay)."""
    return (ROOT / "utilities" / "js" / name).read_text(encoding="utf-8")
