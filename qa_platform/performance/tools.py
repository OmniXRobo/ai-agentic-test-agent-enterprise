"""Locate Java and JMeter: settings, the portable package's runtime folder, environment, PATH."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..config import APP_ROOT, Settings

WINDOWS = sys.platform == "win32"


def _in_home(home: str | Path | None, names: tuple[str, ...]) -> str | None:
    if not home:
        return None
    for name in names:
        p = Path(home) / "bin" / name
        if p.exists():
            return str(p)
    return None


def _bundled(prefix: str) -> Path | None:
    runtime = APP_ROOT / "runtime"
    if not runtime.exists():
        return None
    for child in sorted(runtime.iterdir()):
        if child.is_dir() and child.name.lower().startswith(prefix):
            return child
    return None


def find_java(settings: Settings) -> str | None:
    names = ("java.exe",) if WINDOWS else ("java",)
    perf = settings.section("performance")
    for home in (perf.get("java_home"), _bundled("jre"), _bundled("jdk"), os.environ.get("JAVA_HOME")):
        found = _in_home(home, names)
        if found:
            return found
    return shutil.which("java")


def find_jmeter(settings: Settings) -> str | None:
    names = ("jmeter.bat",) if WINDOWS else ("jmeter", "jmeter.sh")
    perf = settings.section("performance")
    for home in (perf.get("jmeter_home"), _bundled("apache-jmeter"), _bundled("jmeter"), os.environ.get("JMETER_HOME")):
        found = _in_home(home, names)
        if found:
            return found
    return shutil.which("jmeter")


def java_version(java: str) -> str | None:
    try:
        out = subprocess.run([java, "-version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = (out.stderr or out.stdout).strip().splitlines()
    return next((ln for ln in lines if "version" in ln), lines[0] if lines else None)


def jmeter_env(java: str | None) -> dict[str, str]:
    env = dict(os.environ)
    if java:
        bin_dir = Path(java).parent
        env["JAVA_HOME"] = str(bin_dir.parent)
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    return env
