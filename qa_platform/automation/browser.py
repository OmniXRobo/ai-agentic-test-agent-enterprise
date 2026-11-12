"""Browser Manager: the one place every module starts a browser (final patch).

Used by AI-Driven Automation, Guided Browser Automation, Application Discovery, Hybrid Automation, script recording
and playback, performance recording, AI analysis and the Data Comparison Agent. The browser comes from Preferences >
Browser (``playback.browser``: Chromium included, Microsoft Edge or Google Chrome); a caller may still ask for one
explicitly (a schedule's browser list, a voice command). It is never switched silently: when the chosen browser
cannot start, the error says which browser, why, and what to change.

Visible windows (recording, playback, debugging previews) open maximized; with two or more monitors they open on the
second monitor (Preferences > Browser > Monitor), otherwise on the primary one. Headless runs keep the fixed
1366 x 768 viewport, so screenshots and benchmarks are comparable between runs.
"""

from __future__ import annotations

import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import APP_ROOT
from ..errors import QAError

JS_DIR = Path(__file__).parent / "js"
WINDOWS = sys.platform == "win32"

DEFAULT_VIEWPORT = {"width": 1366, "height": 768}
NAMES = {"chromium": "Chromium (included)", "msedge": "Microsoft Edge", "chrome": "Google Chrome"}

# Preferences, set once by the runtime (configure); None in unit tests that never start the runtime.
_settings: Any = None


def configure(settings: Any) -> None:
    global _settings
    _settings = settings


def _playback() -> dict[str, Any]:
    try:
        return dict(_settings.section("playback")) if _settings is not None else {}
    except Exception:
        return {}


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


SUPPORTED = ("chromium", "msedge", "chrome")  # 23.3.32: Firefox and WebKit are not part of the installation


def preferred(requested: str | None = None) -> str:
    """The browser to use: an explicit request, else Preferences, else the included Chromium."""
    choice = requested or _playback().get("browser") or "chromium"
    return choice


# ---- monitors (Windows: EnumDisplayMonitors; no extra package) ----------------------------------------------------

def monitors() -> list[dict[str, Any]]:
    """Monitors as {index, x, y, width, height, primary}, primary first. Empty when unknown (no desktop)."""
    if not WINDOWS:
        return []
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD)]

        found: list[dict[str, Any]] = []
        proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                  wintypes.LPARAM)

        def callback(hmon, _hdc, _rect, _data):  # type: ignore[no-untyped-def]
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                w = info.rcWork
                found.append({"x": w.left, "y": w.top, "width": w.right - w.left, "height": w.bottom - w.top,
                              "primary": bool(info.dwFlags & 1)})
            return 1

        user32.EnumDisplayMonitors(None, None, proc(callback), 0)
    except Exception:
        return []
    found.sort(key=lambda m: (not m["primary"], m["x"], m["y"]))
    for i, m in enumerate(found, start=1):
        m["index"] = i
    return found


def placement(playback: dict[str, Any] | None = None) -> dict[str, Any]:
    """Where a visible browser window opens: maximized, on the monitor chosen in Preferences ("auto": the second
    monitor when there are two or more, else the primary one)."""
    pb = playback if playback is not None else _playback()
    maximize = pb.get("window_mode", "maximized") != "default"
    choice = str(pb.get("monitor", "auto") or "auto")
    screens = monitors()
    target = None
    if screens:
        if choice == "auto":
            target = screens[1] if len(screens) > 1 else screens[0]
        elif choice == "primary":
            target = screens[0]
        elif choice.isdigit():
            index = int(choice)
            # a monitor that is no longer connected: the primary one, never off screen
            target = next((m for m in screens if m["index"] == index), screens[0])
    return {"maximize": maximize, "monitor": target, "monitors": len(screens), "choice": choice}


def launch_args(headless: bool, playback: dict[str, Any] | None = None) -> list[str]:
    args = ["--autoplay-policy=no-user-gesture-required"]  # the playback cursor's quiet click sound
    if headless:
        return args
    where = placement(playback)
    mon = where["monitor"]
    if mon:
        args.append(f"--window-position={mon['x']},{mon['y']}")
        if not where["maximize"]:
            args.append(f"--window-size={min(1366, mon['width'])},{min(900, mon['height'])}")
    if where["maximize"]:
        args.append("--start-maximized")
    return args


def context_options(headless: bool, playback: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    """new_context options: a maximized visible window uses the window's real size (no fixed viewport); headless
    and non-maximized runs keep 1366 x 768."""
    pb = playback if playback is not None else _playback()
    opts: dict[str, Any] = dict(extra)
    if not headless and pb.get("window_mode", "maximized") != "default":
        opts.pop("viewport", None)
        opts["no_viewport"] = True
    else:
        opts.setdefault("viewport", dict(DEFAULT_VIEWPORT))
    return opts


# ---- launch and diagnostics -------------------------------------------------------------------------------------

def _known_paths(browser: str) -> list[Path]:
    if WINDOWS:
        roots = [os.environ.get(v) for v in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        rel = {"msedge": Path("Microsoft/Edge/Application/msedge.exe"),
               "chrome": Path("Google/Chrome/Application/chrome.exe")}.get(browser)
        return [Path(r) / rel for r in roots if r and rel]
    return [Path(p) for p in {"msedge": ["/opt/microsoft/msedge/msedge", "/usr/bin/microsoft-edge"],
                              "chrome": ["/opt/google/chrome/chrome", "/usr/bin/google-chrome"]}.get(browser, [])]


def installed_executable(browser: str, pw: Any = None) -> str | None:
    if browser == "chromium":
        try:
            if pw is not None:
                path = Path(pw.chromium.executable_path)
                return str(path) if path.exists() else None
        except Exception:
            return None
        return None
    return next((str(p) for p in _known_paths(browser) if p.exists()), None)


def classify_launch_error(browser: str, text: str) -> tuple[str, str]:
    """(reason, readable message with what to do) for a browser that did not start."""
    low = (text or "").lower()
    name = NAMES.get(browser, browser)
    if "executable doesn't exist" in low or "is not found at" in low or "distribution" in low and "not found" in low:
        if browser == "chromium":
            return ("not_installed", f"{name} is missing from this installation (runtime\\ms-playwright). Run "
                    "VALIDATE.cmd; if it reports Chromium missing, install the full package again. Or choose "
                    "Microsoft Edge in Preferences > Browser.")
        return ("not_installed", f"{name} is not installed on this PC. Install it, or choose another browser in "
                "Preferences > Browser.")
    if any(w in low for w in ("access is denied", "eacces", "eperm", "spawn unknown", "operation not permitted")):
        return ("blocked", f"Windows refused to start {name} (access denied). Antivirus or an application control "
                "policy (AppLocker) may block browsers started from the SkyGuard QA folder. Ask IT to allow it, or "
                "choose another browser in Preferences > Browser.")
    if "missing dependencies" in low or "error while loading shared libraries" in low:
        return ("dependencies", f"{name} needs system libraries that are missing on this PC.")
    if "timeout" in low:
        return ("timeout", f"{name} did not open within the time limit. The PC may be busy, or a policy may hold "
                "the browser at start-up. Close other browser windows and try again.")
    if "crash" in low or "closed" in low:
        return ("crashed", f"{name} closed while starting. Another program may be using its profile, or a policy "
                "closes it. Try again, or choose another browser in Preferences > Browser.")
    if "display" in low or "x server" in low:
        return ("no_desktop", f"{name} needs a desktop to open a visible window. Run headless instead.")
    return ("unknown", f"{name} did not start. See the details, or choose another browser in Preferences > Browser.")


def launch(pw: Any, browser: str | None = "chromium", headless: bool = False, *,
           playback: dict[str, Any] | None = None) -> Any:
    browser = preferred(browser)
    if browser not in SUPPORTED:
        raise QAError("BROWSER_NOT_INCLUDED", f"{browser} is not included in this installation. Choose Chromium, "
                      "Microsoft Edge or Google Chrome.", status=422)
    options: dict[str, Any] = {"headless": headless, "args": launch_args(headless, playback)}
    if browser in ("msedge", "chrome"):
        options["channel"] = browser
    try:
        return pw.chromium.launch(**options)
    except Exception as exc:
        text = str(exc)
        reason, message = classify_launch_error(browser, text)
        raise QAError("BROWSER_LAUNCH_FAILED", f"Could not start {browser}: {message}", status=503,
                      details={"browser": browser, "reason": reason, "error": text.splitlines()[0][:300] if text else "",
                               "executable": installed_executable(browser, pw),
                               "category": "browser_environment"}) from exc


def diagnose(browsers: list[str] | None = None, *, try_launch: bool = True) -> dict[str, Any]:
    """Preferences > Browser > Check browsers: for each browser, is it installed, does it start, which version."""
    out: list[dict[str, Any]] = []
    pw = None
    try:
        pw = start_playwright()
    except QAError as exc:
        return {"ok": False, "playwright": exc.message, "browsers": [], "monitors": monitors()}
    try:
        for b in browsers or list(SUPPORTED):
            row: dict[str, Any] = {"browser": b, "name": NAMES.get(b, b), "executable": installed_executable(b, pw)}
            if try_launch:
                try:
                    inst = launch(pw, b, headless=True)
                    row["version"] = inst.version
                    row["status"] = "PASS"
                    inst.close()
                except QAError as exc:
                    row["status"] = "FAIL"
                    row["message"] = exc.message
                    row["details"] = exc.details
            out.append(row)
    finally:
        try:
            pw.stop()
        except Exception:
            pass
    chosen = preferred(None)
    return {"ok": any(r.get("status") == "PASS" and r["browser"] == chosen for r in out), "selected": chosen,
            "browsers": out, "monitors": monitors(), "placement": {k: v for k, v in placement().items()
                                                                   if k != "monitors"}}


def default_headless(settings_value: Any) -> bool:
    if isinstance(settings_value, bool):
        return settings_value
    # Visual playback needs a desktop; on servers without one run headless.
    return not (sys.platform == "win32" or os.environ.get("DISPLAY"))


def version_key(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text or "")[:4])
