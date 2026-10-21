"""Failure & Quality Analysis: turn recorded evidence into a defect draft.

Classification is rule-based on what the run recorded (failed step, action,
error text, self-healing attempts, SLA checks). It states what was observed
and never asserts a cause the evidence does not show.
"""

from __future__ import annotations

import hashlib
import html
import re
from typing import Any

# category -> (is it likely a product defect?, default severity 1-4, plain-language finding)
CATEGORIES: dict[str, tuple[bool, int, str]] = {
    "assertion": (True, 3, "The application did not show the expected result."),
    "navigation": (True, 2, "A page did not load or respond in time."),
    "interaction": (True, 3, "An action on the page could not be completed."),
    "locator": (False, 3, "The element could not be found with the recorded locator or a confident alternative. "
                          "This can be a UI change (test maintenance) or a missing element (defect); review the screenshot."),
    "test_setup": (False, 4, "The test could not run because its own setup is incomplete (credentials or test data)."),
    "performance_errors": (True, 2, "Requests failed under load beyond the allowed error rate."),
    "performance_latency": (True, 3, "Response times exceeded the agreed service levels under load."),
    "performance_throughput": (True, 3, "Throughput stayed below the agreed service level."),
}

_VOLATILE = re.compile(r"\b(RUN|PERF|DISC|GEN|REC)-[\w-]+|\d{2,}|0x[0-9a-f]+", re.I)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", _VOLATILE.sub("#", (text or "").lower())).strip()


def signature(*parts: Any) -> str:
    return hashlib.sha1("|".join(normalize(str(p)) for p in parts).encode()).hexdigest()[:16]


def classify_step_failure(step: dict[str, Any] | None, error: str, healing_failed: bool) -> str:
    text = (error or "").lower()
    action = (step or {}).get("action", "")
    if "no saved credential" in text or "test data field" in text or "credential reference" in text \
            or "has no steps" in text or "no url for goto" in text:
        return "test_setup"
    if healing_failed or "element not found" in text:
        return "locator"
    if action.startswith("expect"):
        return "assertion"
    if action == "goto" or "net::" in text or "navigation" in text or "page.goto" in text:
        return "navigation"
    return "interaction"


def _li(items: list[str]) -> str:
    return "<ol>" + "".join(f"<li>{html.escape(i)}</li>" for i in items) + "</ol>"


def functional_draft(execution: dict[str, Any], script: dict[str, Any], healing: list[dict[str, Any]]) -> dict[str, Any]:
    """Draft for a failed Playwright run. `execution` is an execution_results row."""
    steps = script.get("steps", [])
    failed_index = execution.get("failed_step")
    failed = steps[failed_index] if failed_index is not None and failed_index < len(steps) else None
    run_steps = execution.get("steps") or []
    failed_entry = next((s for s in run_steps if s.get("index") == failed_index), None)
    error = execution.get("error") or (failed_entry or {}).get("error") or "The run failed."
    heal_failed = any(h["result"] == "failed" and h["step_index"] == failed_index for h in healing)
    category = classify_step_failure(failed, error, heal_failed)
    product, severity, finding = CATEGORIES[category]
    if failed_index == 0 or (failed and failed.get("action") == "goto"):
        severity = min(severity, 2)  # nothing after the first step could be tested
    target = (failed or {}).get("target") or {}
    what = (failed_entry or {}).get("description") or (failed or {}).get("description") or f"step {failed_index + 1 if failed_index is not None else '?'}"
    tc = execution.get("test_case_id")
    title = (f"{script.get('name', 'Automated test')}: {what} failed" if category != "assertion"
             else f"{script.get('name', 'Automated test')}: unexpected result - {what}")
    passed = [s.get("description") or s.get("action") for s in run_steps if s.get("status") == "PASSED"]
    repro = (
        f"<p><strong>Observed:</strong> {html.escape(finding)}</p>"
        f"<p><strong>Steps to reproduce</strong> (from automated script {html.escape(script.get('id', ''))}):</p>"
        + _li([*passed, f"{what}  ← failed here"])
        + f"<p><strong>Error:</strong> {html.escape(error)}</p>"
        + (f"<p><strong>Element:</strong> {html.escape(target.get('description') or target.get('selector') or '')}</p>"
           if target else "")
        + (f"<p><strong>Expected:</strong> {html.escape(str(failed.get('value')))}</p>"
           if failed and failed.get("action", "").startswith("expect") and failed.get("value") else "")
        + f"<p><strong>Environment:</strong> {html.escape(execution.get('base_url') or script.get('base_url') or '')} · "
          f"{html.escape(execution.get('browser') or '')} · run {html.escape(execution.get('run_id', ''))}"
          f" at {html.escape(execution.get('started_at') or '')}</p>"
        + "<p><strong>Root cause:</strong> not determined from the automated evidence.</p>"
    )
    healing_facts = [{k: h.get(k) for k in ("step_index", "original_selector", "healed_selector", "confidence", "result")}
                     for h in healing if h.get("step_index") == failed_index]
    shots = [p for p in ((failed_entry or {}).get("screenshot"),) if p] or (execution.get("evidence") or {}).get("screenshots", [])[-1:]
    return {
        "source": "functional",
        "source_run_id": execution["run_id"],
        "signature": signature("functional", script.get("id"), failed_index, category, target.get("selector")),
        "category": category,
        "product_defect": product,
        "title": title[:250],
        "severity": severity,
        "priority": 2 if severity <= 2 else 3,
        "repro_steps": repro,
        "facts": {"error": error, "failed_step": failed_index, "action": (failed or {}).get("action"),
                  "selector": target.get("selector"), "finding": finding, "healing": healing_facts,
                  "script_id": script.get("id"), "base_url": execution.get("base_url")},
        "evidence": [{"kind": "screenshot", "path": p} for p in shots],
        "test_case_id": tc,
        "user_story_id": execution.get("user_story_id"),
    }


def performance_draft(run: dict[str, Any], plan: dict[str, Any] | None) -> dict[str, Any] | None:
    """Draft for a performance run whose SLA failed. Returns None when no SLA failed."""
    sla = run.get("sla") or {}
    failed = sla.get("failed") or []
    if not failed:
        return None
    names_ = {c["name"] for c in failed}
    if "Error rate" in names_:
        category = "performance_errors"
    elif "Throughput" in names_ and len(names_) == 1:
        category = "performance_throughput"
    else:
        category = "performance_latency"
    product, severity, finding = CATEGORIES[category]
    worst = max(failed, key=lambda c: (c["actual"] / c["limit"]) if c["name"] != "Throughput" and c["limit"] else 0)
    o = (run.get("metrics") or {}).get("overall", {})
    profile = (plan or {}).get("profile") or {}
    title = (f"Performance: {run['name']} - {worst['name'].lower()} {worst['actual']} {worst['unit']} "
             f"exceeds {worst['limit']} {worst['unit']} ({worst['scope']})")
    rows = "".join(f"<tr><td>{html.escape(c['name'])}</td><td>{html.escape(c['scope'])}</td><td>{c['limit']} {html.escape(c['unit'])}</td>"
                   f"<td>{c['actual']} {html.escape(c['unit'])}</td></tr>" for c in failed)
    top_errors = o.get("top_errors") or []
    repro = (
        f"<p><strong>Observed:</strong> {html.escape(finding)}</p>"
        f"<p><strong>Test:</strong> {html.escape(run['kind'])} test '{html.escape(run['name'])}' (plan {html.escape(run['plan_id'])}), "
        f"{profile.get('users', '?')} users, ramp-up {profile.get('ramp_up_s', '?')} s, duration {profile.get('duration_s', '?')} s, "
        f"think time {profile.get('think_time_ms', '?')} ms. Run {html.escape(run['run_id'])} at {html.escape(run.get('started_at') or '')}.</p>"
        f"<table><tr><th>SLA</th><th>Request</th><th>Limit</th><th>Actual</th></tr>{rows}</table>"
        f"<p><strong>Overall:</strong> {o.get('samples')} samples, avg {o.get('avg_ms')} ms, p95 {o.get('p95_ms')} ms, "
        f"errors {o.get('error_pct')}%, {o.get('throughput')} req/s.</p>"
        + ("<p><strong>Most frequent errors:</strong></p>" + _li([f"{e['error']} ({e['count']})" for e in top_errors[:5]])
           if top_errors else "")
        + "<p><strong>Root cause:</strong> not determined from the load test results; server-side measurements are needed.</p>"
    )
    return {
        "source": "performance",
        "source_run_id": run["run_id"],
        "signature": signature("performance", run["plan_id"], category, worst["scope"], worst["name"]),
        "category": category,
        "product_defect": product,
        "title": title[:250],
        "severity": severity,
        "priority": 2 if severity <= 2 else 3,
        "repro_steps": repro,
        "facts": {"failed_checks": failed, "overall": {k: o.get(k) for k in ("samples", "avg_ms", "p95_ms", "error_pct", "throughput")},
                  "finding": finding, "plan_id": run["plan_id"]},
        "evidence": [{"kind": "file", "path": f"{run['folder']}/metrics.json"}],
        "test_case_id": None,
        "user_story_id": None,
    }
