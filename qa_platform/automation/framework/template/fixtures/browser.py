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
    instance = getattr(playwright_runtime, kind).launch(**options)
    yield instance
    instance.close()


@pytest.fixture()
def browser_context(browser: Any, request: pytest.FixtureRequest) -> Iterator[Any]:
    options: dict[str, Any] = {"viewport": {"width": 1366, "height": 768}, "ignore_https_errors": True}
    if config.VIDEO:
        options["record_video_dir"] = str(config.evidence_dir())
    context = browser.new_context(**options)
    context.add_init_script(config.asset("fingerprint.js"))
    if config.VISUAL:
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


@pytest.fixture()
def page(browser_context: Any) -> Iterator[Any]:
    instance = browser_context.new_page()
    instance.set_default_timeout(max(config.TIMEOUT_MS, 15000))
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
