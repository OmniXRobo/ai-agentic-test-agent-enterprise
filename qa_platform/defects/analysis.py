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
    # final patch: the browser chosen in Preferences could not start; nothing on the application was tested
    "browser_environment": (False, 4, "The browser could not start on this PC (browser configuration), so the "
                                      "application was not tested. This is not an application defect."),
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
    if "could not start" in text or "browser_launch_failed" in text or "browser is not installed" in text:
        return "browser_environment"
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


def _run_config(run: dict[str, Any]) -> dict[str, Any]:
    """The run's effective configuration: config.json in the run folder, else what was stored at queue time."""
    import json
    from pathlib import Path
    try:
        path = Path(run.get("folder") or "") / "config.json"
        if run.get("folder") and path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return run.get("config") or {}


def _local(ts: str | None) -> str:
    if not ts:
        return "?"
    try:
        from ..clock import parse_iso
        d = parse_iso(ts).astimezone()
        off = d.strftime("%z")
        return f"{d.strftime('%Y-%m-%d %H:%M:%S')} (UTC{off[:3]}:{off[3:]})"
    except (ValueError, ImportError):
        return ts


def severity_for(category: str, failed: list[dict[str, Any]]) -> tuple[int, str]:
    """Suggested severity (1 critical to 4 low) with the reason, from the failed targets only."""
    def ratio(c: dict[str, Any]) -> float:
        if c["name"] == "Throughput":
            return (c["limit"] / c["actual"]) if c["actual"] else 99.0
        return (c["actual"] / c["limit"]) if c["limit"] else 99.0
    worst = max(failed, key=ratio)
    if category == "performance_errors":
        err = next(c for c in failed if c["name"] == "Error rate")
        if err["actual"] >= 10:
            return 1, (f"Critical: {err['actual']}% of requests failed ({err['scope']}), against an allowed "
                       f"{err['limit']}%; many users would see failures.")
        return 2, (f"High: {err['actual']}% of requests failed ({err['scope']}), above the allowed {err['limit']}%; "
                   "some users would see failures.")
    r = ratio(worst)
    if r >= 2:
        return 2, (f"High: {worst['name'].lower()} ({worst['scope']}) is {r:.1f} times the limit "
                   f"({worst['actual']} {worst['unit']} against {worst['limit']} {worst['unit']}), while requests succeed.")
    return 3, (f"Medium: {worst['name'].lower()} ({worst['scope']}) misses the limit by {abs(r - 1) * 100:.0f}% "
               f"({worst['actual']} {worst['unit']} against {worst['limit']} {worst['unit']}); requests succeed.")


def performance_draft(run: dict[str, Any], plan: dict[str, Any] | None) -> dict[str, Any] | None:
    """Draft for a performance run whose targets failed. Returns None when no target failed. 23.3.28: the draft
    carries the environment and build, the workload to reproduce it, the affected transaction, expected against
    actual, the run's times, the result files and a suggested severity with its reason."""
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
    product, _default, finding = CATEGORIES[category]
    severity, rationale = severity_for(category, failed)
    worst = max(failed, key=lambda c: (c["actual"] / c["limit"]) if c["name"] != "Throughput" and c["limit"] else 0)
    metrics = run.get("metrics") or {}
    o = metrics.get("overall", {})
    cfg = _run_config(run)
    env = cfg.get("environment") or {}
    build = run.get("build") or cfg.get("build") or ""
    wl = cfg.get("workload") or {}
    profile = (plan or {}).get("profile") or {}
    esc = html.escape
    title = (f"Performance: {run['name']} - {worst['name'].lower()} {worst['actual']} {worst['unit']} "
             f"exceeds {worst['limit']} {worst['unit']} ({worst['scope']})")
    rows = "".join(f"<tr><td>{esc(c['name'])}</td><td>{esc(c['scope'])}</td>"
                   f"<td>{'at least' if c['name'] == 'Throughput' else 'at most'} {c['limit']} {esc(c['unit'])}</td>"
                   f"<td>{c['actual']} {esc(c['unit'])}</td></tr>" for c in failed)
    affected = sorted({c["scope"] for c in failed if c["scope"] != "ALL REQUESTS"}) or ["all requests"]
    req = {r["label"]: r for r in metrics.get("requests") or []}
    affected_rows = "".join(
        f"<tr><td>{esc(lb)}</td><td>{req[lb]['samples']}</td><td>{req[lb]['error_pct']}%</td><td>{req[lb]['avg_ms']} ms</td>"
        f"<td>{req[lb]['p95_ms']} ms</td><td>{req[lb]['p99_ms']} ms</td></tr>" for lb in affected if lb in req)
    if wl:
        workload = (f"{wl.get('peak_concurrent_users', '?')} concurrent users at peak, planned duration "
                    f"{wl.get('total_duration_s') or 'set by loop counts'}{' s' if wl.get('total_duration_s') else ''}; "
                    f"{wl.get('load_model') or ''}")
        groups = "".join(f"<li>{esc(g['name'])}: {g['users']} users, ramp-up {g['ramp_up_s']} s, start {g['delay_s']} s, "
                         f"runs {g['duration_s']} s</li>" for g in wl.get("thread_groups") or [])
    else:
        workload = (f"{profile.get('users', '?')} users, ramp-up {profile.get('ramp_up_s', '?')} s, duration "
                    f"{profile.get('duration_s', '?')} s, think time {profile.get('think_time_ms', '?')} ms")
        groups = ""
    script = cfg.get("script") or {}
    data = cfg.get("test_data") or []
    top_errors = o.get("top_errors") or []
    folder = run.get("folder") or ""
    files = [f"{folder}/{n}" for n in ("qa-report.pdf", "results.jtl", "metrics.json", "config.json", "plan.jmx",
                                       "jmeter.log")] + [f"{folder}/report/index.html"]
    repro = (
        f"<p><strong>Summary:</strong> {esc(finding)} {esc(title)}.</p>"
        f"<p><strong>Environment and build:</strong> {esc(env.get('name') or 'not set')}"
        f"{' (' + esc(env.get('base_url')) + ')' if env.get('base_url') else ''}; build "
        f"{esc(build) if build else 'not recorded'}.</p>"
        f"<p><strong>Reproduction workload:</strong> {esc(run['kind'])} test '{esc(run['name'])}' "
        f"(plan {esc(run['plan_id'])}{', script version ' + str(script.get('version')) if script.get('version') else ''}), "
        f"{esc(workload)}.</p>" + (f"<ul>{groups}</ul>" if groups else "")
        + (f"<p>Test data: {esc('; '.join(str(d.get('name')) + ' version ' + str(d.get('version')) for d in data))}.</p>"
           if data else "")
        + f"<p><strong>Affected transaction or request:</strong> {esc(', '.join(affected))}.</p>"
        + (f"<table><tr><th>Request</th><th>Samples</th><th>Errors</th><th>Avg</th><th>95th</th><th>99th</th></tr>"
           f"{affected_rows}</table>" if affected_rows else "")
        + f"<p><strong>Expected against actual:</strong></p>"
        f"<table><tr><th>Target</th><th>Scope</th><th>Expected</th><th>Actual</th></tr>{rows}</table>"
        f"<p><strong>Overall:</strong> {o.get('samples')} samples, avg {o.get('avg_ms')} ms, p95 {o.get('p95_ms')} ms, "
        f"errors {o.get('error_pct')}%, {o.get('throughput')} samples/s, peak {metrics.get('peak_threads', '?')} users.</p>"
        + ("<p><strong>Most frequent errors:</strong></p>" + _li([f"{e['error']} ({e['count']})" for e in top_errors[:5]])
           if top_errors else "")
        + f"<p><strong>Run:</strong> {esc(run['run_id'])}, started {esc(_local(run.get('started_at')))}, ended "
        f"{esc(_local(run.get('finished_at')))}.</p>"
        + "<p><strong>Supporting charts and results:</strong> the run report (charts of response time, throughput, "
          "errors and users), the JMeter results and the JMeter HTML dashboard, in the run folder:</p>"
        + _li(files)
        + f"<p><strong>Suggested severity {severity}:</strong> {esc(rationale)}</p>"
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
                  "finding": finding, "plan_id": run["plan_id"], "severity_rationale": rationale,
                  "environment": env.get("name"), "build": build or None, "affected": affected,
                  "workload": workload, "started_at": run.get("started_at"), "finished_at": run.get("finished_at")},
        # attached to the TFS bug when it is created (the report PDF once it exists); the rest are referenced above
        "evidence": [{"kind": "file", "path": f"{folder}/qa-report.pdf"}, {"kind": "file", "path": f"{folder}/metrics.json"}],
        "test_case_id": None,
        "user_story_id": None,
    }
