"""INSTALL_OFFLINE / ROLLBACK_OFFLINE: per-user install with automatic rollback.

Layout of an install folder (default ``%LOCALAPPDATA%\\Programs\\AIAgenticTestAgent``)::

    app\\            the running version (the package's payload, plus INSTALLED.json)
    app.previous\\   the version it replaced, kept for ROLLBACK_OFFLINE.cmd
    data\\           settings, database, evidence, reports; never touched by install or rollback
    logs\\           install, rollback and validation reports
    START_QA_PLATFORM.cmd, VALIDATE_OFFLINE.cmd, ROLLBACK_OFFLINE.cmd

A first install (no ``app`` folder yet) copies the payload straight into ``app``: there is nothing to switch, so no
folder is renamed. 23.12.1: renaming a folder of thousands of files that were written seconds earlier failed on new
Windows 11 PCs with "[WinError 5] Access is denied" (Microsoft Defender or a company antivirus still scanning them).

An upgrade or repair backs up the data first, copies the payload next to the current version (``app.new``), checks it,
moves the current version aside and switches folders, validates the installed copy and switches back automatically if
validation fails. Every folder move is retried for about three minutes while Windows reports the folder in use; if the
new folder still cannot be renamed, its files are copied into place instead (copying only reads them). Only one
previous version is kept: the one before it is removed after a successful install, when nothing points at it any more.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Callable

from ..clock import now, now_iso
from ..config import APP_ROOT
from . import manifest as mf

APP_NAME = "SkyGuard QA"  # 23.3.32
OLD_SHORTCUT_NAMES = ("AI Agentic Test Agent Enterprise",)
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

    def write_only(self, text: str) -> None:
        self._f.write(text.rstrip() + "\n")
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


def programs_running_in(folder: Path) -> list[dict]:
    """23.3.32: programs started from a folder (Python, llama-server, Faster-Whisper, Java, Chromium...). Windows only:
    elsewhere a running program does not stop a folder from being renamed."""
    if not WINDOWS:
        return []
    pattern = str(folder.resolve()).replace("'", "''").rstrip("\\") + "\\*"
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '" + pattern + "' } | "
              "Select-Object ProcessId, Name, ExecutablePath | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                             text=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        found = json.loads(out.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    found = [found] if isinstance(found, dict) else list(found or [])
    return [p for p in found if isinstance(p, dict) and p.get("ProcessId") and int(p["ProcessId"]) != os.getpid()]


def stop_programs_in(folder: Path, log: Callable[[str], None]) -> int:
    """Stop every program running from the folder of the version being replaced (an older version could leave its
    local AI or speech server running after the dashboard closed). Returns how many were stopped."""
    running = programs_running_in(folder)
    if running:
        names = sorted({str(p.get("Name")) for p in running})
        log(f"Stopping {len(running)} program(s) still running from {folder.name}: {', '.join(names)}")
    for p in running:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(int(p["ProcessId"]))], capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass
    return len(running)


INCOMPLETE_MARKER = "INSTALL_NOT_FINISHED.txt"
# 23.12.1: waits between attempts to move a folder Windows reports in use (about three minutes in all). Antivirus
# scanning thousands of new files, the search indexer or a program that is closing release them on their own.
RETRY_DELAYS = (1, 1, 2, 2, 3, 3, 5, 5, 8, 10, 10, 15, 15, 20, 20, 30, 30)


def _retry(op: Callable[[], None], log: Callable[[str], None], what: str, *,
           delays: tuple[float, ...] | None = None, sleep: Callable[[float], None] = time.sleep,
           between: Callable[[int], None] | None = None) -> None:
    """Run a file-system operation, retrying while Windows reports the files in use. Raises the last OSError."""
    delays = RETRY_DELAYS if delays is None else delays
    for attempt in range(len(delays) + 1):
        try:
            op()
            if attempt:
                log(f"{what}: done after {attempt + 1} attempts.")
            return
        except OSError as exc:
            if attempt == len(delays):
                raise
            if attempt == 0:
                log(f"{what}: Windows reports the files in use ({exc.strerror or exc}). Waiting and trying again; "
                    "this is usually antivirus scanning the new files and can take a few minutes.")
            if between:
                between(attempt)
            sleep(delays[attempt])


def move_aside(src: Path, dst: Path, log: Callable[[str], None], *, attempts: int | None = None,
               sleep: Callable[[float], None] = time.sleep,
               stop: Callable[[Path, Callable[[str], None]], int] = stop_programs_in) -> None:
    """Rename a version folder, stopping its programs first and retrying while Windows still holds files open
    (a program that is closing, antivirus or the search indexer reading new files). Raises the last OSError."""
    stop(src, log)
    delays = RETRY_DELAYS if attempts is None else tuple(1 + i // 3 for i in range(max(attempts - 1, 0)))
    _retry(lambda: src.rename(dst), log, f"Moving {src.name} to {dst.name}", delays=delays, sleep=sleep,
           between=lambda attempt: stop(src, log) if attempt in (2, 5) else None)


def _copy_file(src: str, dst: str) -> str:
    """Copy one file, retrying a few times when antivirus has it locked for a moment."""
    for wait in (0.5, 1, 2, 4, 0):
        try:
            return shutil.copy2(src, dst)
        except PermissionError:
            if not wait:
                raise
            time.sleep(wait)
    return dst


def remove_tree(folder: Path, log: Callable[[str], None] | None = None, *, tries: int = 3,
                sleep: Callable[[float], None] = time.sleep) -> bool:
    """Delete a folder of ours, retrying briefly. Returns False (and leaves it) when Windows keeps files in it open."""
    for n in range(tries):
        if not folder.exists():
            return True
        shutil.rmtree(folder, ignore_errors=True)
        if not folder.exists():
            return True
        sleep(1 + n)
    if log:
        log(f"{folder.name} could not be removed yet (files still in use); it is removed by the next install.")
    return False


def switch_into_place(staged: Path, app: Path, log: Callable[[str], None], *,
                      sleep: Callable[[float], None] = time.sleep,
                      stop: Callable[[Path, Callable[[str], None]], int] = stop_programs_in) -> str:
    """Make the checked copy the running version. Renames the folder, retrying for about three minutes; if Windows
    still refuses, copies its files into place instead (copying only reads them, which antivirus allows). Returns
    "renamed" or "copied". Raises OSError when neither worked (``app`` is then removed again)."""
    try:
        move_aside(staged, app, log, sleep=sleep, stop=stop)
        return "renamed"
    except OSError as exc:
        log(f"{staged.name} still could not be renamed ({exc}). Copying its files into place instead.")
    try:
        shutil.copytree(staged, app, copy_function=_copy_file)
    except (OSError, shutil.Error) as exc:
        remove_tree(app, log)
        raise OSError(f"the new version could not be copied into place ({exc})") from exc
    remove_tree(staged, log)
    return "copied"


def backup_data(data: Path, backups: Path, label: str, log: Callable[[str], None]) -> Path | None:
    """Before an upgrade: copy the databases (SQLite online backup, consistent even while open) and settings.json.
    Evidence, reports and scripts are never changed by an install, so they are not copied."""
    items = [p for p in (data / "qa_platform.sqlite3", data / "opsagent" / "knowledge.sqlite3",
                         data / "settings.json") if p.is_file()]
    if not items:
        return None
    dest = backups / label
    dest.mkdir(parents=True, exist_ok=True)
    for p in items:
        out = dest / p.relative_to(data)
        out.parent.mkdir(parents=True, exist_ok=True)
        if p.suffix == ".sqlite3":
            src = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            dst = sqlite3.connect(out)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
        else:
            shutil.copy2(p, out)
    log(f"Backed up your data ({', '.join(str(p.relative_to(data)) for p in items)}) to {dest}")
    return dest


def _still_open_hint(target: Path) -> str:
    return ("SkyGuard QA's own programs were stopped, so something else still has a file open there (an Explorer, "
            f"command or editor window open in {target}, or antivirus still scanning). Close those windows, wait a "
            "minute, then run INSTALL.cmd again. Nothing was changed.")


IN_USE_HELP = (
    "What to do:\n"
    "  1. Wait two minutes, then double-click INSTALL.cmd again. It continues safely.\n"
    "  2. If it happens again, restart the PC and run INSTALL.cmd before opening anything else.\n"
    "  3. Still not working: ask IT to let Microsoft Defender or the company antivirus skip the folder\n"
    "     {target}\n"
    "     (it only needs to skip it while SkyGuard QA installs).")


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
        raise InstallError(f"{target} is a protected Windows folder. Use a folder in your profile, for example {default_target()}.")
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
        '@echo off\r\nrem Starts the installed version.\r\n'
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


def bundled_defaults(app: Path, settings_path: Path) -> list[str]:
    """23.3.32: point the settings at the AI model and the Faster-Whisper model inside the package, so First-Run
    Setup only asks for TFS. Settings already chosen (an upgrade, or values pre-filled by IT) are kept."""
    from ..config import Settings

    settings = Settings(settings_path)
    done: list[str] = []
    ai = settings.section("ai")
    managed = dict(ai.get("managed") or {})
    models = sorted(p for p in (app / "runtime" / "ai" / "models").glob("*.gguf") if "mmproj" not in p.name.lower())
    servers = sorted((app / "runtime" / "ai").rglob("llama-server.exe" if WINDOWS else "llama-server"))
    if models and servers and not (managed.get("model_path") and Path(managed["model_path"]).is_file()):
        managed.update({"model_path": str(models[0]), "server_path": str(servers[0]), "auto_start": True,
                        "alias": "qwen", "context_size": max(8192, int(managed.get("context_size") or 8192))})
        settings.update({"ai": {"mode": "on_premise", "model": "qwen", "managed": managed}})
        done.append(f"On-Premise AI: {models[0].name}, started with the application by the included llama-server.")
    fw = app / "runtime" / "faster-whisper"
    fw_models = sorted(p.parent.name for p in (fw / "models").glob("*/model.bin")) if (fw / "models").is_dir() else []
    voice = settings.section("voice")
    if fw_models and (fw / "lib").is_dir() and voice.get("stt_api") != "faster_whisper":
        port = int(voice.get("fw_port") or 8766)
        settings.update({"voice": {"stt_engine": "local", "stt_api": "faster_whisper",
                                   "stt_url": f"http://127.0.0.1:{port}", "fw_model": fw_models[0], "fw_port": port,
                                   "fw_auto_start": True}})
        done.append(f"Speech recognition: Faster-Whisper {fw_models[0]} on this PC.")
    playback = settings.section("playback")
    if not playback.get("browser"):
        from ..launcher import installed_browser

        if installed_browser("msedge"):  # 23.3.33: test runs and recordings use Edge unless another is chosen
            settings.update({"playback": {"browser": "msedge"}})
            done.append("Test runs and recordings: Microsoft Edge (change it under Settings > Preferences).")
    return done


def _shortcuts(target: Path, log: Log) -> None:
    """Start Menu and Desktop shortcuts for this user. Best effort: the launchers work without them.

    23.3.32: "SkyGuard QA" with the robot icon. It starts the application with pythonw (no console window) and opens
    the dashboard in its own window; START_QA_PLATFORM.cmd stays for troubleshooting."""
    if not WINDOWS:
        return
    app = target / "app"
    pythonw = app / "runtime" / "python" / "pythonw.exe"
    icon = app / "web" / "img" / "skyguard.ico"
    if pythonw.is_file():
        exe, args = str(pythonw), "-E -s -m qa_platform.launcher"
    else:
        exe, args = str(target / "START_QA_PLATFORM.cmd"), ""
    old = ",".join(f"'{n}.lnk'" for n in OLD_SHORTCUT_NAMES)
    script = (
        "$s=New-Object -ComObject WScript.Shell;"
        "foreach($d in @([Environment]::GetFolderPath('Programs'),[Environment]::GetFolderPath('Desktop'))){"
        f"foreach($o in @({old})){{$p=Join-Path $d $o; if(Test-Path $p){{Remove-Item $p -Force}}}};"
        f"$l=$s.CreateShortcut((Join-Path $d '{APP_NAME}.lnk'));"
        f"$l.TargetPath='{exe}';$l.Arguments='{args}';$l.WorkingDirectory='{app}';"
        f"$l.Description='{APP_NAME}: Every release, cleared for takeoff.';"
        + (f"$l.IconLocation='{icon},0';" if icon.is_file() else "")
        + "$l.Save()}"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                             text=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        log("Shortcuts 'SkyGuard QA' created in the Start Menu and on the Desktop." if out.returncode == 0
            else f"Shortcuts were not created ({out.stderr.strip()[:200]}). Use START_QA_PLATFORM.cmd in {target}.")
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"Shortcuts were not created ({exc}). Use START_QA_PLATFORM.cmd in {target}.")


def _leftovers(target: Path, log: Callable[[str], None]) -> None:
    """Folders an earlier, interrupted install of ours left behind (app.new, app.old-*, app.failed-*)."""
    for folder in [target / "app.new", *target.glob("app.new-*"), *target.glob("app.old-*"), *target.glob("app.failed-*")]:
        if folder.is_dir():
            log(f"Removing {folder.name}, left over from an earlier install that did not finish.")
            remove_tree(folder, log)


def _version_of(app: Path) -> str:
    try:
        return str(json.loads((app / "INSTALLED.json").read_text(encoding="utf-8")).get("version") or "?")
    except (OSError, ValueError):
        init = app / "qa_platform" / "__init__.py"
        try:
            for line in init.read_text(encoding="utf-8").splitlines():
                if line.startswith("__version__"):
                    return line.split("=", 1)[1].strip().strip("'\"")
        except OSError:
            pass
    return "?"


def install(source: Path, target: Path, *, validation: Validation = subprocess_validation,
            shortcuts: bool = True, prefill: dict[str, str] | None = None,
            sleep: Callable[[float], None] = time.sleep) -> int:
    source, target = source.resolve(), target.resolve()
    _check_target(source, target)
    stamp = _stamp()
    log = Log(target / "logs" / f"install-{stamp}.log")
    app, previous = target / "app", target / "app.previous"
    data = target / "data"
    old_previous: Path | None = None
    marker = target / INCOMPLETE_MARKER
    if marker.exists() and app.exists():  # our own first install that stopped half-way: start it again
        log("Removing the unfinished first installation left by an earlier attempt.")
        remove_tree(app, log)
    fresh = not app.exists()
    try:
        manifest = mf.load(source)
        if manifest is None:
            raise InstallError(f"{source} has no {mf.MANIFEST_NAME}; this is not an offline package.")
        version = manifest.get("version")
        if fresh:
            log(f"Installing {APP_NAME} {version} (built {manifest.get('built_at')}) into {target}")
        else:
            log(f"Upgrading {APP_NAME} {_version_of(app)} to {version} (built {manifest.get('built_at')}) in {target}")
        log("Checking the package files…")
        diff = mf.verify(source, manifest)
        if diff["missing"] or diff["changed"]:
            raise InstallError(f"The package is damaged: {len(diff['missing'])} files missing, {len(diff['changed'])} "
                               f"changed (for example {', '.join((diff['missing'] + diff['changed'])[:3])}). Copy it again.")
        port = _dashboard_running(data)
        if port:
            raise InstallError(f"The dashboard is running on port {port}. Close its window, then run the installer again.")
        _leftovers(target, log)
        data.mkdir(parents=True, exist_ok=True)

        # A first install copies straight into "app": nothing runs from there yet and there is nothing to switch, so
        # no folder of freshly written files has to be renamed (that rename is what antivirus blocked).
        staged = app if fresh else target / "app.new"
        if staged.exists():
            staged = target / f"app.new-{stamp}"  # app.new could not be removed above
        if fresh:
            marker.write_text("A first installation is in progress or did not finish. INSTALL.cmd starts it again.\n",
                              encoding="utf-8")
        else:
            backup_data(data, target / "backups", f"before-{version}-{stamp}", log)
        log(f"Copying {len(manifest['files'])} files…")
        shutil.copytree(source, staged, ignore=shutil.ignore_patterns("__pycache__", "data"), copy_function=_copy_file)
        if mf.verify(staged, manifest)["changed"]:
            if not fresh:
                remove_tree(staged, log)
            raise InstallError("The copied files do not match the package. Check free disk space and try again.")
        (staged / "INSTALLED.json").write_text(json.dumps(
            {"version": version, "installed_at": now_iso(), "package_built_at": manifest.get("built_at")},
            indent=1), encoding="utf-8")

        log("Checking the new version before switching…" if not fresh else "Checking the new version…")
        ok, report = validation(staged, data, True)
        if not ok:
            remove_tree(staged, log)
            raise InstallError("The new version did not pass its checks; nothing was changed.\n" + report)

        if not fresh:
            if previous.exists():
                old_previous = target / f"app.old-{stamp}"
                try:
                    move_aside(previous, old_previous, log, sleep=sleep)
                except OSError as exc:
                    old_previous = None
                    remove_tree(staged, log)
                    raise InstallError(f"The version before the current one could not be moved aside ({exc}). "
                                       f"{_still_open_hint(target)}") from exc
            try:
                move_aside(app, previous, log, sleep=sleep)
            except OSError as exc:
                remove_tree(staged, log)
                raise InstallError(f"The current version could not be moved aside ({exc}). "
                                   f"{_still_open_hint(target)}") from exc
            log(f"Kept the current version as {previous.name} for rollback.")
            try:
                how = switch_into_place(staged, app, log, sleep=sleep)
            except OSError as exc:
                log(f"The new version could not be put in place ({exc}). Switching back to the current version.")
                try:
                    move_aside(previous, app, log, sleep=sleep, stop=lambda f, lg: 0)
                    if old_previous is not None:
                        move_aside(old_previous, previous, log, sleep=sleep, stop=lambda f, lg: 0)
                        old_previous = None
                except OSError as exc2:
                    raise InstallError(f"Windows kept the install folder in use, and switching back also failed "
                                       f"({exc2}). Your version is in {previous}; rename it to 'app' after a restart. "
                                       "Your data was not changed.") from exc2
                raise InstallError("Windows kept the new files in use, so the new version could not be put in "
                                   "place. Your current version is unchanged.\n" + IN_USE_HELP.format(target=target)) from exc
            if how == "copied":
                log("The new version was copied into place.")

        log("Validating the installed version (Java, JMeter, Chromium, Python packages)…")
        ok, report = validation(app, data, False)
        (target / "logs" / f"validation-{stamp}.txt").write_text(report + "\n", encoding="utf-8")
        if not ok:
            log(report)
            if fresh:
                log("Validation failed; the incomplete installation is removed.")
                if remove_tree(app, log):
                    marker.unlink(missing_ok=True)
                return 1
            log("Validation failed; switching back to the previous version.")
            _restore(target, old_previous, log)
            return 1
        log(report)
        if old_previous is not None:
            remove_tree(old_previous, log)
        _write_launchers(target)
        marker.unlink(missing_ok=True)
        if prefill and any(prefill.values()):
            from ..first_run import prefill as write_prefill

            try:
                applied = write_prefill(data / "settings.json", **prefill)
                log(f"Pre-filled First-Run Setup ({', '.join(sorted(applied))}); the TFS PAT is entered there.")
            except Exception as exc:  # the install itself succeeded; the setup screen asks instead
                log(f"Setup values were not pre-filled ({getattr(exc, 'message', exc)}). Enter them in First-Run Setup.")
        try:
            for line in bundled_defaults(target / "app", data / "settings.json"):
                log(line)
        except Exception as exc:  # the install succeeded; the setup screen can still choose them
            log(f"The bundled AI and speech models were not set up ({exc}). Choose them in First-Run Setup.")
        if shortcuts:
            _shortcuts(target, log)
        log(f"Installed. Start it with {target / 'START_QA_PLATFORM.cmd'}")
        return 0
    except InstallError as exc:
        log(f"Install stopped: {exc}")
        if fresh and not app.exists():
            marker.unlink(missing_ok=True)
        if old_previous is not None and not previous.exists():
            old_previous.rename(previous)
        return 1
    except Exception as exc:  # never a traceback on screen: a plain message, and the details in the log
        log.write_only(traceback.format_exc())
        in_use = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (5, 32)
        log("Install stopped: " + (
            "Windows reported files in the install folder as in use (Access is denied).\n" + IN_USE_HELP.format(target=target)
            if in_use else f"{type(exc).__name__}: {exc}. Run INSTALL.cmd again; if it happens again, send the log "
                           f"file {log.path} to support."))
        if fresh and app.exists() and marker.exists():
            remove_tree(app, log)
        log(f"Details are in {log.path}")
        return 1
    finally:
        log.close()


def _restore(target: Path, old_previous: Path | None, log: Log) -> None:
    app, previous = target / "app", target / "app.previous"
    failed = target / f"app.failed-{_stamp()}"
    quiet = lambda f, lg: 0  # noqa: E731
    if app.exists():
        move_aside(app, failed, log, stop=stop_programs_in)
    if previous.exists():
        move_aside(previous, app, log, stop=quiet)
        log("The previous version is active again.")
    else:
        log("There was no previous version; nothing is installed now.")
    if old_previous is not None:
        move_aside(old_previous, previous, log, stop=quiet)
    remove_tree(failed, log)


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
        quiet = lambda f, lg: 0  # noqa: E731
        if app.exists():
            move_aside(app, swap, log)
        move_aside(previous, app, log, stop=quiet)
        if swap.exists():
            move_aside(swap, previous, log, stop=quiet)  # kept, so the newer version can be restored by rolling back again
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
    ap = argparse.ArgumentParser(description=f"Install {APP_NAME} for this user (first install, upgrade or repair).")
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
    except Exception as exc:  # before the log exists (for example the folder cannot be created): no traceback
        print(f"Install stopped: {type(exc).__name__}: {exc}\n" + IN_USE_HELP.format(target=target), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
