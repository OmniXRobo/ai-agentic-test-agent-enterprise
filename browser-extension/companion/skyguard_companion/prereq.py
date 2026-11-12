"""Prerequisite checker (also run by Install-SkyGuardAgent.ps1 and Check-Prerequisites.ps1).

Read-only: it reports what is present and what is missing; it changes nothing.
"""

from __future__ import annotations

import os
import platform
import socket
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .config import Config, Paths


def _browser_paths() -> dict[str, list[Path]]:
    pf = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
    roots = [Path(p) for p in pf if p]
    return {"Google Chrome": [r / "Google" / "Chrome" / "Application" / "chrome.exe" for r in roots],
            "Microsoft Edge": [r / "Microsoft" / "Edge" / "Application" / "msedge.exe" for r in roots]}


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def run_checks(paths: Paths | None = None) -> dict[str, Any]:
    paths = paths or Paths()
    cfg = Config(paths)
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, required: bool = True) -> None:
        checks.append({"check": name, "ok": bool(ok), "required": required, "detail": detail})

    win = sys.platform == "win32"
    build = 0
    if win:
        try:
            build = int(platform.version().split(".")[-1])
        except ValueError:
            build = 0
    add("Windows 11 (build 22000 or later)", win and build >= 22000,
        f"{platform.system()} {platform.release()} build {build or platform.version()}", required=False)
    add("Python 3.11 or later (bundled 3.13)", sys.version_info >= (3, 11), sys.version.split()[0])
    add("SQLite with WAL support", sqlite3.sqlite_version_info >= (3, 22), sqlite3.sqlite_version)
    found = {name: next((str(p) for p in cands if p.exists()), "") for name, cands in _browser_paths().items()}
    add("Chrome or Edge installed", any(found.values()) or not win,
        "; ".join(f"{k}: {v or 'not found'}" for k, v in found.items()))
    port = int(cfg.get("port"))
    add(f"Port {port} on 127.0.0.1", _port_free(port) or _running(port), "free" if _port_free(port) else
        ("used by the companion" if _running(port) else "in use by another program (set 'port' in config.json)"))
    root = paths.root
    llama = next((p for p in (root / "runtime" / "llama" / "llama-server.exe", root / "runtime" / "llama" /
                              "llama-server") if p.exists()), None)
    add("llama-server (bundled)", llama is not None, str(llama or "missing: runtime\\llama"), required=False)
    gguf = sorted((root / "models").glob("*.gguf")) if (root / "models").is_dir() else []
    add("Qwen model (.gguf)", bool(gguf) or cfg.get("llm")["mode"] in ("connect", "off"),
        str(gguf[0]) if gguf else "not installed: run Install-Models.ps1, or point llm.url at a running server",
        required=False)
    fw_lib = root / "runtime" / "faster-whisper" / "lib"
    add("Faster-Whisper packages (bundled)", (fw_lib / "faster_whisper").is_dir(), str(fw_lib), required=False)
    fw_model = root / "models" / "faster-whisper-small.en" / "model.bin"
    add("Faster-Whisper small.en model", fw_model.exists() or cfg.get("whisper")["mode"] in ("connect", "off"),
        str(fw_model) if fw_model.exists() else "not installed: run Install-Models.ps1 (voice is optional)",
        required=False)
    if win:
        try:
            from skyguard_agent.vault import DpapiBackend

            b = DpapiBackend(paths.vault)
            b.set("prereq-check", "x")
            ok = b.get("prereq-check") == "x"
            b.delete("prereq-check")
            add("Credential vault (Windows DPAPI, current user)", ok, "working" if ok else "not working")
        except Exception as exc:  # noqa: BLE001
            add("Credential vault (Windows DPAPI, current user)", False, type(exc).__name__)
    else:
        add("Credential vault (Windows DPAPI)", False, "not Windows: credentials need the OS keyring", required=False)
    try:
        probe = paths.data / ".write-test"
        probe.write_text("x")
        probe.unlink()
        add("Data folder writable (no administrator rights needed)", True, str(paths.data))
    except OSError as exc:
        add("Data folder writable (no administrator rights needed)", False, f"{paths.data}: {exc}")
    ok = all(c["ok"] for c in checks if c["required"])
    return {"ok": ok, "checks": checks}


def _running(port: int) -> bool:
    import urllib.request

    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as r:
            return r.status == 200
    except OSError:
        return False
