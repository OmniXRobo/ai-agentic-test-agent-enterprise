"""23.3.29: start the application when this Windows user signs in, so schedules keep running after a restart.

No administrator rights and no Windows service: a small command file is placed in this user's Startup folder
(%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup). It runs START_QA_PLATFORM.cmd minimised and without
opening the browser. Removing the file (here, or by hand) turns it off. Only the file this application wrote is
ever removed. There is no time server or GMT service: times are the PC's own clock.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from .config import APP_ROOT
from .errors import QAError

FILE_NAME = "QA Enterprise AI.cmd"
MARKER = "rem qa-enterprise-ai-startup"


def startup_dir() -> Path | None:
    """This user's Startup folder; QA_STARTUP_DIR replaces it (tests, or a managed PC)."""
    override = os.environ.get("QA_STARTUP_DIR")
    if override:
        return Path(override)
    if sys.platform != "win32":
        return None
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def launcher(app_dir: Path = APP_ROOT) -> Path:
    return app_dir / "START_QA_PLATFORM.cmd"


def _content(app_dir: Path) -> str:
    start = launcher(app_dir)
    lines = [
        "@echo off",
        MARKER,
        "rem Starts QA Enterprise AI when this Windows user signs in, so scheduled runs can start.",
        "rem Created from QA Enterprise AI > Performance Schedules. Delete this file to stop it.",
        'set "QA_NO_BROWSER=1"',
    ]
    pythonw = app_dir / "runtime" / "python" / "pythonw.exe"
    if pythonw.is_file():  # 23.3.32: the desktop start in the background, with no console window
        lines += [f'cd /d "{app_dir}"', f'start "" "{pythonw}" -E -s -m qa_platform.launcher --background']
    else:
        lines.append(f'start "QA Enterprise AI" /min cmd /c ""{start}""')
    return "\r\n".join(lines) + "\r\n"


def status(app_dir: Path = APP_ROOT) -> dict[str, Any]:
    folder = startup_dir()
    path = folder / FILE_NAME if folder else None
    ours = bool(path and path.is_file() and MARKER in path.read_text(encoding="utf-8", errors="replace"))
    return {
        "supported": folder is not None,
        "enabled": ours,
        "path": str(path) if path else None,
        "launcher": str(launcher(app_dir)),
        "launcher_exists": launcher(app_dir).is_file(),
        "note": ("Starts QA Enterprise AI minimised when you sign in to Windows (no administrator rights needed). "
                 "Schedules then run without opening the application by hand.") if folder else
                "Start at sign-in is available on Windows only.",
    }


def enable(app_dir: Path = APP_ROOT) -> dict[str, Any]:
    folder = startup_dir()
    if folder is None:
        raise QAError("STARTUP_UNSUPPORTED", "Start at Windows sign-in is available on Windows only.", status=409)
    if not launcher(app_dir).is_file():
        raise QAError("STARTUP_NO_LAUNCHER", f"{launcher(app_dir)} was not found, so there is nothing to start.",
                      status=409)
    path = folder / FILE_NAME
    if path.exists() and MARKER not in path.read_text(encoding="utf-8", errors="replace"):
        raise QAError("STARTUP_FILE_EXISTS", f"{path} exists and was not created by this application; it was left "
                      "as it is.", status=409)
    folder.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_content(app_dir).encode("utf-8"))
    return status(app_dir)


def disable(app_dir: Path = APP_ROOT) -> dict[str, Any]:
    folder = startup_dir()
    if folder is not None:
        path = folder / FILE_NAME
        if path.is_file() and MARKER in path.read_text(encoding="utf-8", errors="replace"):
            path.unlink()
    return status(app_dir)
