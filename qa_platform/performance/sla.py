"""SLA evaluation against measured metrics."""

from __future__ import annotations

from typing import Any

from .models import Sla

_LIMITS = (
    ("avg_ms", "Average response time", "avg_ms", "ms", "max"),
    ("p90_ms", "90th percentile response time", "p90_ms", "ms", "max"),
    ("p95_ms", "95th percentile response time", "p95_ms", "ms", "max"),
    ("p99_ms", "99th percentile response time", "p99_ms", "ms", "max"),
    ("max_error_pct", "Error rate", "error_pct", "%", "max"),
)


def evaluate(sla: Sla, metrics: dict[str, Any]) -> dict[str, Any]:
    """Checks for the whole test and per request. Status is NOT_DEFINED when no SLA was set."""
    checks: list[dict[str, Any]] = []
    rows = [metrics["overall"], *metrics.get("requests", [])]
    for key, title, metric, unit, _ in _LIMITS:
        limit = getattr(sla, key)
        if limit is None:
            continue
        for row in rows:
            actual = row[metric]
            checks.append({"name": title, "scope": row["label"], "limit": limit, "actual": actual, "unit": unit,
                           "status": "PASS" if actual <= limit else "FAIL"})
    if sla.min_throughput is not None:
        actual = metrics["overall"]["throughput"]
        checks.append({"name": "Throughput", "scope": metrics["overall"]["label"], "limit": sla.min_throughput,
                       "actual": actual, "unit": "req/s", "status": "PASS" if actual >= sla.min_throughput else "FAIL"})
    if not checks:
        status = "NOT_DEFINED"
    else:
        status = "FAIL" if any(c["status"] == "FAIL" for c in checks) else "PASS"
    return {"status": status, "checks": checks, "failed": [c for c in checks if c["status"] == "FAIL"]}
