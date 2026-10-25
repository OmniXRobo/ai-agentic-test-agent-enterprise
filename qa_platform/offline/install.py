"""INSTALL_OFFLINE / ROLLBACK_OFFLINE: per-user install with automatic rollback. No administrator rights.

Layout of an install folder (default ``%LOCALAPPDATA%\\Programs\\AIAgenticTestAgent``)::

    app\\            the running version (the package's payload, plus INSTALLED.json)
    app.previous\\   the version it replaced, kept for ROLLBACK_OFFLINE.cmd
    data\\           settings, database, evidence, reports; never touched by install or rollback
    logs\\           install, rollback and validation reports
    START_QA_PLATFORM.cmd, VALIDATE_OFFLINE.cmd, ROLLBACK_OFFLINE.cmd

Install copies the payload next to the current version, checks it, switches folders, validates the installed copy
and switches back automatically if validation fails. Only one previous version is kept: the one before it is
removed after a successful install, when nothing points at it any more.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Callable

from ..clock import now, now_iso
from ..config import APP_ROOT
from . import manifest as mf

APP_NAME = "AI Agentic Test Agent Enterprise"
FOLDER_NAME = "AIAgenticTestAgent"
WINDOWS = sys.platform == "win32"
LAUNCHERS = ("START_QA_PLATFORM.cmd", "VALIDATE_OFFLINE.cmd", "ROLLBACK_OFFLINE.cmd")

# (app folder, data folder, quick) -> (passed, report text)
Validation = Callable[[Path, Path, bool], tuple[bool, str]]


class InstallError(Exception):
    pass


def default_target() -> Path:
    if WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Programs" / FOLDER_NAME
    return Path.home() / ".local" / "share" / FOLDER_NAME


def _stamp() -> str:
    return now().strftime("%Y%m%d-%H%M%S")


class Log:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._f = path.open("a", encoding="utf-8")

    def __call__(self, message: str) -> None:
        line = f"{now().strftime('%H:%M:%S')}  {message}"
        print(line, flush=True)
        self._f.write(line + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()


def subprocess_validation(app: Path, data: Path, quick: bool) -> tuple[bool, str]:
    """Validate a copy with its own bundled Python, the way VALIDATE_OFFLINE.cmd does."""
    python = app / "runtime" / "python" / ("python.exe" if WINDOWS else "bin/python3")
    if not python.exists():
        return False, f"{python} is missing."
    cmd = [str(python), "-E", "-s", "-m", "qa_platform.offline.validate", "--app", str(app), "--data", str(data)]
    if quick:
        cmd.append("--quick")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON") and k != "PLAYWRIGHT_BROWSERS_PATH"}
    out = subprocess.run(cmd, cwd=app, capture_output=True, text=True, timeout=600, env=env,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return out.returncode == 0, (out.stdout + out.stderr).strip()


def _dashboard_running(data: Path) -> int | None:
    try:
        port = int(json.loads((data / "settings.json").read_text(encoding="utf-8"))["server"]["port"])
    except (OSError, ValueError, KeyError, TypeError):
        port = 8765
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        if s.connect_ex(("127.0.0.1", port)) != 0:
            return None
    try:
        import httpx

        r = httpx.get(f"http://127.0.0.1:{port}/api/meta", timeout=3, trust_env=False)
        return port if r.status_code == 200 and "version" in r.text else None
    except Exception:
        return None


def _check_target(source: Path, target: Path) -> None:
    src, tgt = source.resolve(), target.resolve()
    if tgt == src or src in tgt.parents or tgt in src.parents:
        raise InstallError(f"Choose an install folder outside the package ({source}).")
    if WINDOWS and any(p.lower() in ("program files", "program files (x86)", "windows") for p in tgt.parts[1:2]):
        raise InstallError(f"{target} needs administrator rights. Use a folder in your profile, for example {default_target()}.")
    try:
        tgt.mkdir(parents=True, exist_ok=True)
        probe = tgt / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise InstallError(f"{target} is not writable for this user ({exc}).") from exc


def _write_launchers(target: Path) -> None:
    """Small launchers in the install folder that always run the current version."""
    (target / "START_QA_PLATFORM.cmd").write_text(
        '@echo off\r\nrem Starts the installed version. No administrator rights needed.\r\n'
        'call "%~dp0app\\START_QA_PLATFORM.cmd" %*\r\n', encoding="utf-8")
    (target / "VALIDATE_OFFLINE.cmd").write_text(
        '@echo off\r\nrem Checks the installed version without Internet.\r\n'
        'call "%~dp0app\\VALIDATE_OFFLINE.cmd" %*\r\n', encoding="utf-8")
    # Folder renames only: no program runs from app or app.previous while they are swapped. A running dashboard
    # keeps its files open, so the first move fails and nothing changes.
    (target / "ROLLBACK_OFFLINE.cmd").write_text(
        '@echo off\r\nrem Switches back to the previous version (app.previous). Your data folder is not changed.\r\n'
        'setlocal\r\ncd /d "%~dp0"\r\n'
        'if not exist "app.previous\\runtime\\python\\python.exe" (echo There is no previous version to roll back to. & (if not defined QA_NO_PAUSE pause) & exit /b 1)\r\n'
        'if exist "app.swap" (echo app.swap exists from an interrupted rollback. Rename it back to app or app.previous first. & (if not defined QA_NO_PAUSE pause) & exit /b 1)\r\n'
        'move "app" "app.swap" >nul || (echo The current version is in use. Close the dashboard window, then try again. & (if not defined QA_NO_PAUSE pause) & exit /b 1)\r\n'
        'move "app.previous" "app" >nul || (move "app.swap" "app" >nul & echo Rollback failed; nothing was changed. & (if not defined QA_NO_PAUSE pause) & exit /b 1)\r\n'
        'move "app.swap" "app.previous" >nul\r\n'
        'if not exist "logs" mkdir "logs"\r\n'
        'echo %DATE% %TIME% rolled back; the replaced version is now app.previous>>"logs\\rollback.log"\r\n'
        'echo Rollback complete. Run ROLLBACK_OFFLINE.cmd again to return to the newer version.\r\n'
        'call "app\\VALIDATE_OFFLINE.cmd" --quick\r\n'
        'endlocal\r\n', encoding="utf-8")


def _shortcuts(target: Path, log: Log) -> None:
    """Start Menu and Desktop shortcuts for this user. Best effort: the launchers work without them."""
    if not WINDOWS:
        return
    script = (
        "$s=New-Object -ComObject WScript.Shell;"
        "foreach($d in @([Environment]::GetFolderPath('Programs'),[Environment]::GetFolderPath('Desktop'))){"
        f"$l=$s.CreateShortcut((Join-Path $d '{APP_NAME}.lnk'));"
        f"$l.TargetPath='{target / 'START_QA_PLATFORM.cmd'}';$l.WorkingDirectory='{target}';$l.Save()}}"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                             text=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        log("Shortcuts created in the Start Menu and on the Desktop." if out.returncode == 0
            else f"Shortcuts were not created ({out.stderr.strip()[:200]}). Use START_QA_PLATFORM.cmd in {target}.")
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"Shortcuts were not created ({exc}). Use START_QA_PLATFORM.cmd in {target}.")


def install(source: Path, target: Path, *, validation: Validation = subprocess_validation,
            shortcuts: bool = True, prefill: dict[str, str] | None = None) -> int:
    source, target = source.resolve(), target.resolve()
    _check_target(source, target)
    stamp = _stamp()
    log = Log(target / "logs" / f"install-{stamp}.log")
    app, staged, previous = target / "app", target / "app.new", target / "app.previous"
    data = target / "data"
    old_previous: Path | None = None
    try:
        manifest = mf.load(source)
        if manifest is None:
            raise InstallError(f"{source} has no {mf.MANIFEST_NAME}; this is not an offline package.")
        log(f"Installing {APP_NAME} {manifest.get('version')} (built {manifest.get('built_at')}) into {target}")
        log("Checking the package files…")
        diff = mf.verify(source, manifest)
        if diff["missing"] or diff["changed"]:
            raise InstallError(f"The package is damaged: {len(diff['missing'])} files missing, {len(diff['changed'])} "
                               f"changed (for example {', '.join((diff['missing'] + diff['changed'])[:3])}). Copy it again.")
        port = _dashboard_running(data)
        if port:
            raise InstallError(f"The dashboard is running on port {port}. Close its window, then run the installer again.")

        if staged.exists():  # left over from an interrupted install of ours
            shutil.rmtree(staged)
        log(f"Copying {len(manifest['files'])} files…")
        shutil.copytree(source, staged, ignore=shutil.ignore_patterns("__pycache__", "data"))
        if mf.verify(staged, manifest)["changed"]:
            raise InstallError("The copied files do not match the package. Check free disk space and try again.")
        (staged / "INSTALLED.json").write_text(json.dumps(
            {"version": manifest.get("version"), "installed_at": now_iso(), "package_built_at": manifest.get("built_at")},
            indent=1), encoding="utf-8")
        data.mkdir(parents=True, exist_ok=True)

        log("Checking the new version before switching…")
        ok, report = validation(staged, data, True)
        if not ok:
            shutil.rmtree(staged, ignore_errors=True)
            raise InstallError("The new version did not pass its checks; nothing was changed.\n" + report)

        if app.exists():
            if previous.exists():
                old_previous = target / f"app.old-{stamp}"
                previous.rename(old_previous)
            try:
                app.rename(previous)
            except OSError as exc:
                raise InstallError(f"The current version could not be moved aside ({exc}). Close the dashboard and "
                                   "any window open in its folder, then try again.") from exc
            log(f"Kept the current version as {previous.name} for rollback.")
        staged.rename(app)

        log("Validating the installed version (Java, JMeter, Chromium, Python packages)…")
        ok, report = validation(app, data, False)
        (target / "logs" / f"validation-{stamp}.txt").write_text(report + "\n", encoding="utf-8")
        if not ok:
            log(report)
            log("Validation failed; switching back to the previous version.")
            _restore(target, old_previous, log)
            return 1
        log(report)
        if old_previous is not None:
            shutil.rmtree(old_previous, ignore_errors=True)
        _write_launchers(target)
        if prefill and any(prefill.values()):
            from ..first_run import prefill as write_prefill

            try:
                applied = write_prefill(data / "settings.json", **prefill)
                log(f"Pre-filled First-Run Setup ({', '.join(sorted(applied))}); the TFS PAT is entered there.")
            except Exception as exc:  # the install itself succeeded; the setup screen asks instead
                log(f"Setup values were not pre-filled ({getattr(exc, 'message', exc)}). Enter them in First-Run Setup.")
        if shortcuts:
            _shortcuts(target, log)
        log(f"Installed. Start it with {target / 'START_QA_PLATFORM.cmd'}")
        return 0
    except InstallError as exc:
        log(f"Install stopped: {exc}")
        if old_previous is not None and not previous.exists():
            old_previous.rename(previous)
        return 1
    finally:
        log.close()


def _restore(target: Path, old_previous: Path | None, log: Log) -> None:
    app, previous = target / "app", target / "app.previous"
    failed = target / f"app.failed-{_stamp()}"
    if app.exists():
        app.rename(failed)
    if previous.exists():
        previous.rename(app)
        log("The previous version is active again.")
    else:
        log("There was no previous version; nothing is installed now.")
    if old_previous is not None:
        old_previous.rename(previous)
    shutil.rmtree(failed, ignore_errors=True)


def rollback(target: Path, *, validation: Validation | None = None) -> int:
    target = target.resolve()
    log = Log(target / "logs" / f"rollback-{_stamp()}.log")
    app, previous, swap = target / "app", target / "app.previous", target / "app.swap"
    try:
        if not previous.is_dir():
            log("There is no previous version to roll back to.")
            return 1
        port = _dashboard_running(target / "data")
        if port:
            log(f"The dashboard is running on port {port}. Close its window, then roll back again.")
            return 1
        version = lambda p: json.loads((p / "INSTALLED.json").read_text(encoding="utf-8")).get("version", "?") \
            if (p / "INSTALLED.json").exists() else "?"  # noqa: E731
        log(f"Rolling back from {version(app)} to {version(previous)}…")
        if app.exists():
            app.rename(swap)
        previous.rename(app)
        if swap.exists():
            swap.rename(previous)  # kept, so the newer version can be restored by rolling back again
        if validation is not None:
            ok, report = validation(app, target / "data", True)
            log(report)
            if not ok:
                log("The restored version did not pass its checks. Run VALIDATE_OFFLINE.cmd for details.")
                return 1
        log("Rollback complete. Your data folder was not changed.")
        return 0
    finally:
        log.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=f"Install {APP_NAME} for this user, without administrator rights.")
    ap.add_argument("action", nargs="?", choices=("install", "rollback"), default="install")
    ap.add_argument("--source", type=Path, default=APP_ROOT, help="the package's payload folder")
    ap.add_argument("--target", type=Path, default=None, help=f"install folder (default {default_target()})")
    ap.add_argument("--no-shortcuts", action="store_true")
    # Optional: pre-fill First-Run Setup so the user only pastes the PAT (never given on a command line).
    ap.add_argument("--tfs-url", default="", help="TFS collection URL, e.g. https://tfs-server/tfs/DefaultCollection")
    ap.add_argument("--tfs-project", default="")
    ap.add_argument("--llm-model", default="", help="the Qwen .gguf model file")
    ap.add_argument("--llm-server", default="", help="another llama-server.exe (default: the bundled one)")
    ap.add_argument("--voice-model", default="", help="a Vosk model folder (default: the bundled one)")
    args = ap.parse_args(argv)
    target = args.target or default_target()
    if args.action == "rollback":
        return rollback(target)
    try:
        return install(args.source, target, shortcuts=not args.no_shortcuts,
                       prefill={"tfs_url": args.tfs_url, "tfs_project": args.tfs_project, "llm_model": args.llm_model,
                                "llm_server": args.llm_server, "voice_model": args.voice_model})
    except InstallError as exc:
        print(f"Install stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
