"""Steps, evidence and progress reporting.

``with step("Enter username"):`` marks one business step of a test. Each step is
reported (RUNNING / PASSED / FAILED with its duration), a screenshot is taken when
it fails and, for checkpoints, when it passes. When the Enterprise QA Runtime runs
the test it reads these reports live (QA_EVENTS_FILE) to drive Agent Workflow,
Execution History and the evidence of the run. Secrets registered with
``register_secret`` never reach a report, a log or a file name.
"""

from __future__ import annotations

import json
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from . import config


class _State:
    def __init__(self) -> None:
        self.page: Any = None
        self.test: dict[str, Any] = {}
        self.step_index = -1
        self.step_title: str | None = None
        self.secrets: list[str] = []
        self.base_url = ""
        self.failure_screenshot: str | None = None
        self.target: Any = None  # the element the current step last used (highlighted on failure)
        self.console: list[str] = []  # browser console errors of the current test
        self.network: list[str] = []  # failed requests and HTTP errors of the current test


state = _State()
_lock = threading.Lock()


def register_secret(value: str | None) -> None:
    if value and value not in state.secrets:
        state.secrets.append(value)


def mask(value: Any) -> Any:
    if isinstance(value, str):
        for secret in state.secrets:
            value = value.replace(secret, "***")
    return value


def emit(kind: str, **fields: Any) -> None:
    """Append one progress record for the Enterprise QA Runtime (no-op when run standalone)."""
    if not config.EVENTS_FILE:
        return
    record = {"ts": datetime.now().astimezone().isoformat(timespec="milliseconds"), "kind": kind,
              "test": state.test.get("nodeid"), "combination": state.test.get("combination")}
    record.update({k: mask(v) for k, v in fields.items()})
    with _lock, open(config.EVENTS_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def begin_test(nodeid: str, combination: str | None, meta: dict[str, Any]) -> None:
    state.test = {"nodeid": nodeid, "combination": combination, **meta}
    state.failure_screenshot = None
    state.step_index = -1
    state.step_title = None
    state.target = None
    state.console = []
    state.network = []


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", mask(name))[:80]


def screenshot(name: str, *, full_page: bool = False, highlight: Any = None) -> str | None:
    """Screenshot of the current page into the run's screenshots folder; returns its path.

    ``highlight`` is a Playwright locator to outline in red first (the failing element).
    """
    page = state.page
    if page is None:
        return None
    try:
        page.evaluate("window.__qaOverlay && window.__qaOverlay.clear()")
    except Exception:
        pass
    outlined = _outline(page, highlight) if highlight is not None else False
    prefix = ""
    if state.test.get("combination"):
        prefix = _safe(state.test["combination"]) + "-"
    folder = config.evidence_dir() / "screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{prefix}{_safe(name)}.png"
    try:
        page.screenshot(path=str(path), full_page=full_page)
        return str(path)
    except Exception:
        return None
    finally:
        if outlined:
            try:
                page.evaluate("window.__qaOverlay && window.__qaOverlay.clear()")
            except Exception:
                pass


def _outline(page: Any, element: Any) -> bool:
    try:
        box = element.bounding_box(timeout=500)
        if not box:
            return False
        if not page.evaluate("!!window.__qaOverlay"):
            page.add_script_tag(content=config.asset("overlay.js"))
        page.evaluate("([b, l]) => window.__qaOverlay.point(b.x, b.y, b.width, b.height, 'fail', l)",
                      [box, "Failed here"])
        return True
    except Exception:
        return False


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", mask(text).lower()).strip("_")[:40] or "step"


def show_status(text: str) -> None:
    """Current executing agent and action, shown in the browser during visual playback."""
    if not config.VISUAL or state.page is None:
        return
    try:
        state.page.evaluate("t => window.__qaOverlay && window.__qaOverlay.status(t)", mask(text))
    except Exception:
        pass


def current_label(default: str = "") -> str:
    return state.step_title or default


@contextmanager
def step(title: str, checkpoint: bool = False, tc_step: int | str | None = None) -> Iterator[None]:
    """One business step of the test; ``tc_step`` is the TFS Test Case step (or steps) it automates."""
    state.step_index += 1
    index = state.step_index
    state.step_title = title
    state.target = None
    name = f"step_{index + 1:03d}_{_slug(title)}"
    emit("step", index=index, title=title, status="RUNNING", checkpoint=checkpoint, tc_step=tc_step)
    show_status(f"Test Execution Agent - Step {index + 1}: {title}")
    started = time.monotonic()
    try:
        yield
    except BaseException as exc:
        shot = screenshot(f"{name}_FAILED", highlight=state.target)
        full = screenshot(f"{name}_FAILED_full", full_page=True)
        state.failure_screenshot = shot or state.failure_screenshot
        message = str(exc).strip().splitlines()[0][:400] if str(exc).strip() else type(exc).__name__
        emit("step", index=index, title=title, status="FAILED", error=f"{type(exc).__name__}: {message}",
             duration_ms=int((time.monotonic() - started) * 1000), screenshot=shot, full_page=full, tc_step=tc_step)
        raise
    else:
        shot = screenshot(name) if config.STEP_SCREENSHOTS or checkpoint else None
        emit("step", index=index, title=title, status="PASSED", duration_ms=int((time.monotonic() - started) * 1000),
             screenshot=shot, tc_step=tc_step)
    finally:
        state.step_title = None


def action(kind: str, description: str, **fields: Any) -> None:
    """One browser action inside a step (for logs)."""
    emit("action", step=state.step_index, action=kind, description=description, **fields)


def checkpoint(name: str) -> str | None:
    """Evidence screenshot at a configured checkpoint."""
    shot = screenshot(f"checkpoint-{name}")
    emit("evidence", step=state.step_index, name=name, screenshot=shot)
    return shot
