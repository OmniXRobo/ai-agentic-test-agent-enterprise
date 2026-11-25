"""Browser fixtures: one browser per session, a fresh context and page per test.

The visual playback overlay and the element fingerprint helper are added to every
page at run time; nothing is written into the application under test.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

import pytest

from utilities import config, reporting


def _browsers_path() -> None:
    """Prefer browsers shipped next to the platform (runtime/ms-playwright) when they exist."""
    import os
    from pathlib import Path

    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    for parent in config.ROOT.parents:
        for bundled in (Path(parent) / "runtime" / "ms-playwright", Path(parent) / "app" / "runtime" / "ms-playwright"):
            if bundled.is_dir():
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled)
                return


@pytest.fixture(scope="session")
def playwright_runtime() -> Iterator[Any]:
    _browsers_path()
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    yield pw
    pw.stop()


@pytest.fixture(scope="session")
def browser(playwright_runtime: Any) -> Iterator[Any]:
    name = config.BROWSER
    kind = "chromium" if name in ("chromium", "msedge", "chrome") else name
    options: dict[str, Any] = {"headless": config.HEADLESS}
    if name in ("msedge", "chrome"):
        options["channel"] = name
    if kind == "chromium":
        options["args"] = ["--autoplay-policy=no-user-gesture-required"]  # the cursor's quiet click sound
        options["args"] += [a for a in config.WINDOW_ARGS if a not in options["args"]]
    try:
        instance = getattr(playwright_runtime, kind).launch(**options)
    except Exception as exc:  # 23.13.0: a browser that is not installed here -> Edge, then Chrome, then Chromium
        if "executable doesn't exist" not in str(exc).lower() and "is not found at" not in str(exc).lower() \
                and "not found" not in str(exc).lower():
            raise
        instance = None
        for alt in ("msedge", "chrome", "chromium"):
            if alt == name:
                continue
            alt_opts = {**options, "channel": alt} if alt != "chromium" else \
                {k: v for k, v in options.items() if k != "channel"}
            try:
                instance = playwright_runtime.chromium.launch(**alt_opts)
                print(f"{name} is not installed on this PC; using {alt} instead")
                break
            except Exception:
                continue
        if instance is None:
            raise
    yield instance
    instance.close()


@pytest.fixture()
def browser_context(browser: Any, request: pytest.FixtureRequest) -> Iterator[Any]:
    options: dict[str, Any] = ({"no_viewport": True} if config.MAXIMIZED else {"viewport": {"width": 1366, "height": 768}})
    options["ignore_https_errors"] = True
    if config.VIDEO:
        options["record_video_dir"] = str(config.evidence_dir())
    context = browser.new_context(**options)
    context.add_init_script(config.asset("fingerprint.js"))
    context.add_init_script(config.asset("calendar.js"))
    if config.VISUAL:
        context.add_init_script(config.overlay_config())
        context.add_init_script(config.asset("overlay.js"))
    if config.TRACE:
        context.tracing.start(screenshots=True, snapshots=True, sources=False)
    yield context
    if config.TRACE:
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.name)[:80]
        path = config.evidence_dir() / f"trace-{name}.zip"
        try:
            context.tracing.stop(path=str(path))
            reporting.emit("evidence", name="trace", trace=str(path))
        except Exception:
            pass
    context.close()


def maximize(page: Any) -> None:
    """Visible runs: maximize the window through the browser (CDP), filling the monitor's work area when no window
    manager answers. Headless and fixed-viewport runs are left as they are."""
    if config.HEADLESS or not config.MAXIMIZED:
        return
    try:
        cdp = page.context.new_cdp_session(page)
        win = cdp.send("Browser.getWindowForTarget")
        cdp.send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": {"windowState": "maximized"}})
        if (cdp.send("Browser.getWindowForTarget").get("bounds") or {}).get("windowState") != "maximized":
            a = page.evaluate("() => [screen.availLeft || 0, screen.availTop || 0, screen.availWidth, screen.availHeight]")
            cdp.send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": {
                "left": int(a[0]), "top": int(a[1]), "width": int(a[2]), "height": int(a[3]), "windowState": "normal"}})
        cdp.detach()
    except Exception:
        pass  # --start-maximized still applies


@pytest.fixture()
def page(browser_context: Any) -> Iterator[Any]:
    instance = browser_context.new_page()
    maximize(instance)
    instance.set_default_timeout(max(config.TIMEOUT_MS, 15000))
    _listen(instance)
    reporting.state.page = instance
    yield instance
    video = None
    try:
        video = instance.video.path() if instance.video else None
    except Exception:
        video = None
    reporting.state.page = None
    if video:
        reporting.emit("evidence", name="video", video=str(video))


def _listen(page: Any) -> None:
    """Keep console errors and failed requests of the test for the failure diagnosis."""
    def console(msg: Any) -> None:
        if msg.type == "error" and len(reporting.state.console) < 50:
            reporting.state.console.append(reporting.mask(msg.text)[:300])

    def page_error(err: Any) -> None:
        if len(reporting.state.console) < 50:
            reporting.state.console.append(reporting.mask(f"Uncaught: {err}")[:300])

    def failed(request: Any) -> None:
        if len(reporting.state.network) < 50:
            reporting.state.network.append(f"{request.method} {request.url} failed: {request.failure}"[:300])

    def response(resp: Any) -> None:
        if resp.status >= 400 and len(reporting.state.network) < 50:
            reporting.state.network.append(f"{resp.request.method} {resp.url} -> HTTP {resp.status}"[:300])

    page.on("console", console)
    page.on("pageerror", page_error)
    page.on("requestfailed", failed)
    page.on("response", response)
