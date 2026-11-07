"""23.3.32: the desktop start of SkyGuard QA, with no console window.

The Desktop and Start Menu shortcuts run ``runtime\\python\\pythonw.exe -E -s -m qa_platform.launcher``:

1. The server runs inside this process. pythonw has no console, and every program it starts (pytest, JMeter, Java,
   llama-server, the speech server, PowerShell) is started without a window too.
2. The dashboard opens in its own app window: the included Chromium in app mode with its own profile, so it never
   mixes with the user's Chrome or Edge.
3. When that window closes, everything this application started is stopped: test runs and their browsers, load
   tests, the local AI, the speech server and the server itself. When a schedule is active the application keeps
   running in the background instead, so the schedule still runs; double-clicking the icon opens the window again,
   and Exit in the dashboard stops everything.
4. A Windows job object ties every process started from here to this process: whatever happens, nothing it started
   outlives it.

``--background`` (start at Windows sign-in) starts the server without a window. Errors go to logs\\start.log and a
message box. START_QA_PLATFORM.cmd still starts the application with a visible console, for troubleshooting.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent.parent
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008
WIN = sys.platform == "win32"


# ---- environment ------------------------------------------------------------------------------------------------


def prepare_environment(app_dir: Path = APP_DIR) -> Path:
    """What START_QA_PLATFORM.cmd sets: the data folder, the included browsers, and no network for model lookups."""
    installed = (app_dir / "INSTALLED.json").exists()
    data = (app_dir.parent / "data") if installed else (app_dir / "data")
    os.environ.setdefault("QA_HOME", str(data))
    browsers = app_dir / "runtime" / "ms-playwright"
    if browsers.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")  # Faster-Whisper loads its model from a folder, never online
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    return Path(os.environ["QA_HOME"])


def hide_child_windows() -> None:
    """Every console program started from this process starts without a window (pythonw has no console to share).
    A program that asks for its own console or runs detached is left as it asked."""
    if not WIN or getattr(subprocess.Popen, "_qa_hidden", False):
        return
    original = subprocess.Popen.__init__

    def init(self: Any, *args: Any, **kwargs: Any) -> None:
        flags = int(kwargs.get("creationflags") or 0)
        if not flags & (CREATE_NEW_CONSOLE | DETACHED_PROCESS):
            kwargs["creationflags"] = flags | CREATE_NO_WINDOW
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = init  # type: ignore[method-assign]
    subprocess.Popen._qa_hidden = True  # type: ignore[attr-defined]


def kill_children_with_this_process() -> bool:
    """Put this process in a job object that ends every process in it when this process ends (Windows only)."""
    if not WIN:
        return False
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return False
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
        return False
    ok = bool(k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(k32.GetCurrentProcess())))
    globals()["_JOB"] = job  # kept open for the life of this process; Windows closes it when the process ends
    return ok


def show_error(title: str, text: str) -> None:
    if WIN:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    else:
        print(f"{title}: {text}", file=sys.stderr)


# ---- the app window ---------------------------------------------------------------------------------------------


def find_window_browser(app_dir: Path = APP_DIR) -> list[str] | None:
    """The included Chromium, otherwise Microsoft Edge or Google Chrome, as an app window program."""
    roots = [Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])] if os.environ.get("PLAYWRIGHT_BROWSERS_PATH") else []
    roots.append(app_dir / "runtime" / "ms-playwright")
    for root in roots:
        for exe in sorted(root.glob("chromium-*/chrome-win*/chrome.exe"), reverse=True):
            return [str(exe)]
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        for rel in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"):
            if base and (Path(base) / rel).is_file():
                return [str(Path(base) / rel)]
    return None


def window_command(browser: list[str], url: str, profile: Path) -> list[str]:
    return [*browser, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
            "--disable-features=Translate,MediaRouter", "--disable-background-networking", "--disable-sync",
            "--window-size=1440,900"]


# ---- the server -------------------------------------------------------------------------------------------------


def configured_port(data: Path) -> int:
    try:
        return int(json.loads((data / "settings.json").read_text(encoding="utf-8"))["server"]["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return 8765


def _local(port: int, path: str, method: str = "GET", timeout: float = 2.0) -> dict[str, Any] | None:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=b"{}" if method == "POST" else None,
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never through a proxy
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except Exception:
        return None


class Launcher:
    """Runs the server in this process and owns the app window."""

    def __init__(self, data: Path, port: int, *, background: bool = False) -> None:
        self.data, self.port, self.background = data, port, background
        self.url = f"http://127.0.0.1:{port}/"
        self.server: Any = None
        self.window: subprocess.Popen[bytes] | None = None
        self.lock = threading.Lock()
        self.exiting = threading.Event()

    # The dashboard asks for these through /api/app/... (see qa_platform.api.app_control).
    def open_window(self) -> bool:
        with self.lock:
            if self.window is not None and self.window.poll() is None:
                return self._reopen()
            browser = find_window_browser()
            if not browser:
                import webbrowser
                webbrowser.open(self.url)  # no app window available: the default browser, closed by Exit only
                return False
            profile = self.data / "window-profile"
            profile.mkdir(parents=True, exist_ok=True)
            self.window = subprocess.Popen(window_command(browser, self.url, profile))
            threading.Thread(target=self._watch, args=(self.window,), name="app-window", daemon=True).start()
            return True

    def _reopen(self) -> bool:
        browser = find_window_browser()
        if browser:  # the running Chromium opens one more app window and this command ends at once
            subprocess.Popen(window_command(browser, self.url, self.data / "window-profile"))
        return True

    def _watch(self, proc: subprocess.Popen[bytes]) -> None:
        proc.wait()
        with self.lock:
            if self.window is not proc:
                return
            self.window = None
        if self.exiting.is_set():
            return
        if self.keep_running():
            self.log("The window was closed; a schedule is active, so SkyGuard QA keeps running in the background.")
            return
        self.log("The window was closed; stopping SkyGuard QA and everything it started.")
        self.exit()

    def keep_running(self) -> bool:
        from .api.app_control import active_schedules

        try:
            return active_schedules(self.server.config.app.state.runtime) > 0
        except Exception:
            return False

    def exit(self) -> None:
        self.exiting.set()
        with self.lock:
            if self.window is not None and self.window.poll() is None:
                self.window.terminate()
        if self.server is not None:
            self.server.should_exit = True

    def log(self, text: str) -> None:
        logs = self.data / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / "start.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")

    def run(self) -> int:
        import uvicorn

        from .config import Paths
        from .main import create_app

        app = create_app(Paths(self.data))
        app.state.launcher = self
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning",
                                                    log_config=None))
        if not self.background:
            def open_when_ready() -> None:
                for _ in range(600):
                    if self.exiting.is_set():
                        return
                    if self.server.started:
                        self.open_window()
                        return
                    time.sleep(0.1)
                self.log("The server did not start within 60 seconds; see the application log.")

            threading.Thread(target=open_when_ready, name="open-window", daemon=True).start()
        self.log(f"Starting SkyGuard QA on {self.url} ({'background' if self.background else 'window'}).")
        try:
            self.server.run()
        except SystemExit:  # uvicorn ends this way when it cannot listen on the port
            pass
        if not self.server.started:
            self.exiting.set()
            raise RuntimeError(f"Port {self.port} on this PC is used by another program. Close that program, or set "
                               "another port in data\\settings.json (server, port).")
        self.log("SkyGuard QA stopped.")
        return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if sys.stdout is None or sys.stderr is None:  # pythonw: no console, so nothing may write to one
        sys.stdout = sys.stdout or open(os.devnull, "w", encoding="utf-8")
        sys.stderr = sys.stderr or open(os.devnull, "w", encoding="utf-8")
    background = "--background" in args
    data = prepare_environment()
    port = configured_port(data)
    running = _local(port, "/api/health")
    if running is not None:
        # Already running (for example in the background for a schedule): ask it to show its window.
        if not background:
            _local(port, "/api/app/window", method="POST", timeout=10)
        return 0
    hide_child_windows()
    kill_children_with_this_process()
    console_python = Path(sys.executable).with_name("python.exe")
    if Path(sys.executable).name.lower() == "pythonw.exe" and console_python.is_file():
        # Child Pythons (pytest runs, the speech server) need real output streams; they start hidden anyway.
        sys.executable = str(console_python)
    try:
        return Launcher(data, port, background=background).run()
    except Exception as exc:  # pythonw has nowhere to print: log it and say so
        try:
            Launcher(data, port).log(f"Start failed: {exc!r}")
        finally:
            show_error("SkyGuard QA could not start", f"{exc}\n\nDetails: {data / 'logs' / 'start.log'}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
