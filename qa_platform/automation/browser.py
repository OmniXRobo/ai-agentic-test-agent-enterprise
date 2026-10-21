"""Playwright browser helpers shared by the runner, recorder and discovery."""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import APP_ROOT
from ..errors import QAError

JS_DIR = Path(__file__).parent / "js"


@lru_cache(maxsize=None)
def js(name: str) -> str:
    return (JS_DIR / f"{name}.js").read_text(encoding="utf-8")


def ensure_browsers_path() -> None:
    """Prefer browsers bundled with the portable package (runtime/ms-playwright)."""
    bundled = APP_ROOT / "runtime" / "ms-playwright"
    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ and bundled.exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled)


def start_playwright() -> Any:
    ensure_browsers_path()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise QAError("PLAYWRIGHT_MISSING", "Playwright for Python is not installed.", status=503) from exc
    return sync_playwright().start()


def launch(pw: Any, browser: str = "chromium", headless: bool = False) -> Any:
    kind = "chromium" if browser in ("chromium", "msedge", "chrome") else browser
    options: dict[str, Any] = {"headless": headless}
    if browser in ("msedge", "chrome"):
        options["channel"] = browser
    try:
        return getattr(pw, kind).launch(**options)
    except Exception as exc:
        raise QAError("BROWSER_LAUNCH_FAILED", f"Could not start {browser}.", status=503,
                      details=str(exc).splitlines()[0][:300]) from exc


def default_headless(settings_value: Any) -> bool:
    if isinstance(settings_value, bool):
        return settings_value
    # Visual playback needs a desktop; on servers without one run headless.
    return not (sys.platform == "win32" or os.environ.get("DISPLAY"))
