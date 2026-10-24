"""Shared pytest configuration of the AI-Driven Automation framework.

Fixtures live in ``fixtures/``, reusable helpers in ``utilities/``, Page Objects in
``page_objects/``, business flows in ``flows/``, tests in ``tests/`` and their data in
``test_data/``. Run everything with ``python -m pytest`` from this folder, or one test
with ``python -m pytest tests/test_tc1268_customer_logs_in.py``.
"""

from __future__ import annotations

import pytest

from utilities import reporting

pytest_plugins = ["fixtures.browser", "fixtures.data"]


def _meta(item: pytest.Item) -> dict[str, str]:
    out = {}
    for mark in ("test_case", "user_story", "acceptance_criteria", "test_data"):
        marker = item.get_closest_marker(mark)
        if marker and marker.args:
            out[mark] = ",".join(str(a) for a in marker.args)
    return out


def pytest_runtest_setup(item: pytest.Item) -> None:
    combination = None
    callspec = getattr(item, "callspec", None)
    if callspec is not None and "data" in callspec.params:
        combination = str((callspec.params["data"] or {}).get("name") or "") or None
    reporting.begin_test(item.nodeid, combination, _meta(item))
    reporting.emit("test", status="RUNNING", name=item.name, **_meta(item))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> object:
    outcome = yield
    report = outcome.get_result()
    if report.when == "setup" and report.failed:
        shot = reporting.screenshot("setup-failed")
        reporting.emit("test", status="FAILED", phase="setup", error=_error(report), screenshot=shot)
    elif report.when == "call":
        shot = None
        if report.failed and not reporting.state.failure_screenshot:
            shot = reporting.screenshot("failure")
        reporting.emit("test", status="PASSED" if report.passed else ("SKIPPED" if report.skipped else "FAILED"),
                       error=_error(report) if report.failed else None, screenshot=shot,
                       duration_ms=int(report.duration * 1000))


def _error(report: pytest.TestReport) -> str:
    text = getattr(report, "longreprtext", "") or str(report.longrepr or "")
    lines = [line.strip()[1:].strip() for line in text.splitlines() if line.strip().startswith("E ")]
    if not lines:
        lines = [line.strip() for line in text.splitlines() if line.strip()][-1:]
    return reporting.mask(" ".join(lines[:3]))[:500]
