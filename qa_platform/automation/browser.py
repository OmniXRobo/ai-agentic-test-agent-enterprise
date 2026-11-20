"""Browser Manager: the one place every module starts a browser (final patch).

Used by AI-Driven Automation, Guided Browser Automation, Application Discovery, Hybrid Automation, script recording
and playback, performance recording, AI analysis and the Data Comparison Agent. The browser comes from Preferences >
Browser (``playback.browser``: Chromium included, Microsoft Edge or Google Chrome); a caller may still ask for one
explicitly (a schedule's browser list, a voice command). It is never switched silently: when the chosen browser
cannot start, the error says which browser, why, and what to change.

Visible windows (recording, playback, debugging previews) open maximized; with two or more monitors they open on the
second monitor (Preferences > Browser > Monitor), otherwise on the primary one. Headless runs keep the fixed
1366 x 768 viewport, so screenshots and benchmarks are comparable between runs.

23.9.0 window policy (one place for every module): ``new_context`` creates the browser context of every visible
browser and maximizes each of its windows through the browser itself (CDP ``Browser.setWindowBounds``,
``windowState: maximized``), because ``--start-maximized`` alone does not apply to windows of a new context in every
Chromium build. Maximized means the work area of the chosen monitor (the taskbar stays visible) at the monitor's own
DPI; the page viewport is the window (``no_viewport``), never a hardcoded size. The window is maximized once, when it
opens, so its size does not change during a recording. Preferences > Browser > Window: ``maximized`` (default),
``fixed`` (a fixed viewport, ``viewport_width`` x ``viewport_height``, for comparable screenshots) or ``default``.
Headless browsers are never maximized.
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
    """The browser to use: an explicit request, else Preferences, else the included Chromium.

    23.13.0: "chromium" was the built-in default of many callers (scripts, discovery, recordings), so on a PC where
    Microsoft Edge (or Chrome) is chosen in Preferences a request for "chromium" means "the default browser" and
    follows Preferences. A schedule or voice command that names Edge or Chrome is still honoured as asked."""
    pref = _playback().get("browser")
    if requested == "chromium" and pref in ("msedge", "chrome"):
        return pref
    return requested or pref or "chromium"


def fallback_order(first: str) -> list[str]:
    """23.13.0: browsers to try, in order, when ``first`` is not installed on this PC: Preferences, then Microsoft
    Edge (part of Windows 10 / 11), Google Chrome and the included Chromium. Only a browser that is NOT INSTALLED
    is replaced; a browser that is installed but fails (blocked, crashed) is reported as it is."""
    order = [first]
    for alt in (_playback().get("browser"), "msedge", "chrome", "chromium"):
        if alt in SUPPORTED and alt not in order:
            order.append(alt)
    return order


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
    maximize = window_mode(pb) == "maximized"
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


def window_mode(playback: dict[str, Any] | None = None) -> str:
    pb = playback if playback is not None else _playback()
    mode = str(pb.get("window_mode") or "maximized").lower()
    return mode if mode in ("maximized", "fixed", "default") else "maximized"


def fixed_viewport(playback: dict[str, Any] | None = None) -> dict[str, int]:
    pb = playback if playback is not None else _playback()
    try:
        w = max(320, min(int(pb.get("viewport_width") or DEFAULT_VIEWPORT["width"]), 7680))
        h = max(240, min(int(pb.get("viewport_height") or DEFAULT_VIEWPORT["height"]), 4320))
    except (TypeError, ValueError):
        return dict(DEFAULT_VIEWPORT)
    return {"width": w, "height": h}


def launch_args(headless: bool, playback: dict[str, Any] | None = None) -> list[str]:
    args = ["--autoplay-policy=no-user-gesture-required"]  # the playback cursor's quiet click sound
    if headless:
        return args
    where = placement(playback)
    mode = window_mode(playback)
    mon = where["monitor"]
    if mon:
        args.append(f"--window-position={mon['x']},{mon['y']}")
        if mode == "default":
            args.append(f"--window-size={min(1366, mon['width'])},{min(900, mon['height'])}")
    if mode == "maximized":
        args.append("--start-maximized")
    return args


def context_options(headless: bool, playback: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    """new_context options: a maximized visible window uses the window's real size (no fixed viewport); headless
    and non-maximized runs keep 1366 x 768."""
    mode = window_mode(playback)
    opts: dict[str, Any] = dict(extra)
    if not headless and mode == "maximized":
        opts.pop("viewport", None)
        opts["no_viewport"] = True  # the viewport is the maximized window, whatever the screen and DPI
    elif not headless and mode == "fixed":
        opts["viewport"] = fixed_viewport(playback)
    else:
        opts.setdefault("viewport", dict(DEFAULT_VIEWPORT))
    return opts


def maximize(page: Any, headless: bool = False, playback: dict[str, Any] | None = None) -> bool:
    """Maximize the window that shows ``page`` (Chromium, Edge, Chrome through CDP). Headless pages, the 'fixed'
    and 'default' window modes and windows already maximized are left as they are. Never raises."""
    if headless or window_mode(playback) != "maximized":
        return False
    try:
        cdp = page.context.new_cdp_session(page)
        try:
            win = cdp.send("Browser.getWindowForTarget")
            state = (win.get("bounds") or {}).get("windowState")
            if state != "maximized":
                if state in ("minimized", "fullscreen"):
                    cdp.send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": {"windowState": "normal"}})
                cdp.send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": {"windowState": "maximized"}})
                after = (cdp.send("Browser.getWindowForTarget").get("bounds") or {}).get("windowState")
                if after != "maximized":
                    # no window manager answered (kiosk session, some RDP shells): fill the monitor's work area,
                    # which excludes the taskbar, at the screen's own scale
                    area = page.evaluate("() => [screen.availLeft || 0, screen.availTop || 0, screen.availWidth, "
                                         "screen.availHeight]")
                    cdp.send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": {
                        "left": int(area[0]), "top": int(area[1]), "width": int(area[2]), "height": int(area[3]),
                        "windowState": "normal"}})
            return True
        finally:
            try:
                cdp.detach()
            except Exception:
                pass
    except Exception:
        return False  # a browser without CDP window control keeps --start-maximized


def new_context(browser: Any, headless: bool = False, playback: dict[str, Any] | None = None, **extra: Any) -> Any:
    """THE way every module opens a browser context (recording, playback, guided automation, learning, live capture,
    discovery, performance recording, Ops agent). Visible windows open maximized, popups and new tabs too."""
    ctx = browser.new_context(**context_options(headless, playback, **extra))
    if not headless and window_mode(playback) == "maximized":
        opened: set[int] = set()
        plain_new_page = ctx.new_page

        def new_page_maximized(*a: Any, **k: Any) -> Any:
            page = plain_new_page(*a, **k)
            opened.add(id(page))
            maximize(page, headless, playback)  # explicitly: an event handler may run too late for the first window
            return page

        def on_popup(page: Any) -> None:
            if id(page) not in opened:  # windows the page opens itself (target=_blank, window.open)
                maximize(page, headless, playback)

        try:
            ctx.new_page = new_page_maximized
        except AttributeError:  # pragma: no cover - a Playwright object that refuses attributes
            pass
        ctx.on("page", on_popup)
    return ctx


def new_page(browser: Any, headless: bool = False, playback: dict[str, Any] | None = None, **extra: Any) -> Any:
    """A context and its first page, maximized when visible."""
    ctx = new_context(browser, headless, playback, **extra)
    return ctx.new_page()


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


def _launch_one(pw: Any, browser: str, headless: bool, playback: dict[str, Any] | None) -> Any:
    options: dict[str, Any] = {"headless": headless, "args": launch_args(headless, playback)}
    if browser in ("msedge", "chrome"):
        options["channel"] = browser
    return pw.chromium.launch(**options)


# 23.13.0: the last browser that was used instead of the one asked for (shown in run logs and diagnostics)
last_fallback: dict[str, Any] = {}


def launch(pw: Any, browser: str | None = None, headless: bool = False, *,
           playback: dict[str, Any] | None = None, fallback: bool = True, exact: bool = False) -> Any:
    """Start a browser. 23.13.0: the default is the browser chosen in Preferences (not Chromium), and a browser that
    is not installed on this PC (for example the included Chromium on an office PC that only has Microsoft Edge) is
    replaced by the next installed one (Preferences, Edge, Chrome). The replacement is logged and kept in
    ``last_fallback``; nothing else is ever switched silently."""
    browser = (browser or preferred(None)) if exact else preferred(browser)
    if exact:
        fallback = False
    if browser not in SUPPORTED:
        raise QAError("BROWSER_NOT_INCLUDED", f"{browser} is not included in this installation. Choose Chromium, "
                      "Microsoft Edge or Google Chrome.", status=422)
    first_error: Exception | None = None
    tried: list[dict[str, str]] = []
    for candidate in (fallback_order(browser) if fallback else [browser]):
        if candidate != browser and candidate != "chromium" and not installed_executable(candidate, pw):
            continue  # not on this PC: do not even try
        try:
            inst = _launch_one(pw, candidate, headless, playback)
        except Exception as exc:  # noqa: BLE001 - classified below
            text = str(exc)
            reason, _msg = classify_launch_error(candidate, text)
            tried.append({"browser": candidate, "reason": reason})
            if first_error is None:
                first_error = exc
            if reason != "not_installed":
                break  # installed but blocked / crashed: report it, never hide it behind another browser
            continue
        if candidate != browser:
            import logging

            last_fallback.clear()
            last_fallback.update({"asked": browser, "used": candidate, "why": "not installed on this PC"})
            logging.getLogger("qa.browser").warning("%s is not installed on this PC; using %s instead",
                                                    NAMES.get(browser, browser), NAMES.get(candidate, candidate))
            try:
                setattr(inst, "_sg_used", candidate)
            except Exception:
                pass
        return inst
    text = str(first_error or "")
    reason, message = classify_launch_error(browser, text)
    if len(tried) > 1:
        message += " Also tried: " + ", ".join(f"{NAMES.get(t['browser'], t['browser'])} ({t['reason'].replace('_', ' ')})"
                                             for t in tried[1:]) + "."
    raise QAError("BROWSER_LAUNCH_FAILED", f"Could not start {NAMES.get(browser, browser)}: {message}", status=503,
                  details={"browser": browser, "reason": reason, "error": text.splitlines()[0][:300] if text else "",
                           "executable": installed_executable(browser, pw), "tried": tried,
                           "category": "browser_environment"}) from first_error


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
                    inst = launch(pw, b, headless=True, exact=True)
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
