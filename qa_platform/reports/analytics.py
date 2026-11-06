"""Analytics computed from recorded results only.

Functional figures come from execution_results, healing_events and defect_drafts;
coverage uses the live TFS test case count when TFS is reachable and says so
when it is not. Performance figures come from perf_runs. Days are local dates
of the system clock.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

from ..clock import now, parse_iso
from ..design.service import html_to_text, split_criteria
from ..errors import QAError
from ..storage.db import Database, loads
from ..tfs.service import TfsService

MAX_ROWS = 20000


def _day(ts: str | None) -> date | None:
    try:
        return parse_iso(ts).astimezone().date() if ts else None
    except ValueError:
        return None


def _window(days: int) -> tuple[date, list[date]]:
    today = now().date()
    start = today - timedelta(days=max(1, days) - 1)
    return start, [start + timedelta(days=i) for i in range((today - start).days + 1)]


def _rate(part: int, whole: int) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


class AnalyticsService:
    def __init__(self, db: Database, tfs: TfsService, automation: Any) -> None:
        self.db = db
        self.tfs = tfs
        self.automation = automation

    # ---- shared --------------------------------------------------------------------------------

    def tfs_counts(self) -> dict[str, Any]:
        """Live TFS counts, or the reason they are unavailable (never an estimate)."""
        try:
            return {"connected": True, **self.tfs.kpis()}
        except QAError as exc:
            return {"connected": False, "reason": exc.message}
        except Exception as exc:  # analytics must still render if TFS answers unexpectedly
            return {"connected": False, "reason": f"TFS figures could not be loaded ({type(exc).__name__})."}

    def acceptance_criteria(self) -> dict[str, Any]:
        """Acceptance criteria of the project's user stories, counted the way test design reads them: the criteria
        written in each story's Acceptance Criteria field plus criteria kept as their own work items (23.3.30; before,
        only the field was counted, so projects that keep criteria as linked work items showed nothing)."""
        try:
            from ..tfs.hierarchy import HierarchyService
            cfg = HierarchyService(self.tfs).config()
            data = self.tfs.acceptance_criteria_items(cfg["requirement_types"] or self.tfs.types_for("requirement"),
                                                      cfg["acceptance_criteria_types"])
        except QAError as exc:
            return {"available": False, "reason": exc.message}
        except Exception as exc:
            return {"available": False, "reason": f"Acceptance criteria could not be loaded ({type(exc).__name__})."}
        in_field = {sid: len(split_criteria(html_to_text(v))) for sid, v in data["stories"].items() if v}
        in_field = {sid: n for sid, n in in_field.items() if n}
        linked = {sid for it in data["items"] for sid in it["stories"]}
        with_criteria = set(in_field) | linked
        return {"available": True, "criteria": sum(in_field.values()) + len(data["items"]),
                "in_field": sum(in_field.values()), "work_items": len(data["items"]),
                "stories_with_criteria": len(with_criteria), "stories": len(data["stories"])}

    def automated_test_cases(self) -> set[str]:
        return {str(s["test_case_id"]) for s in self.automation.scripts.list() if s.get("test_case_id")}

    def latest_results(self) -> dict[str, dict[str, Any]]:
        """Latest finished result per script."""
        rows = self.db.query("SELECT script_id, test_case_id, status, started_at, run_id FROM execution_results"
                             " WHERE status IN ('PASSED','FAILED') ORDER BY started_at DESC LIMIT ?", (MAX_ROWS,))
        latest: dict[str, dict[str, Any]] = {}
        for r in rows:
            latest.setdefault(r["script_id"], r)
        return latest

    def coverage(self, tfs: dict[str, Any] | None = None) -> dict[str, Any]:
        tfs = tfs if tfs is not None else self.tfs_counts()
        automated = self.automated_test_cases()
        scripts = self.automation.scripts.list()
        out: dict[str, Any] = {"scripts": len(scripts), "automated_test_cases": len(automated),
                               "tfs_connected": tfs.get("connected", False), "tfs_reason": tfs.get("reason")}
        if tfs.get("connected") and "testcase" in tfs:
            total = int(tfs["testcase"])
            out.update({"tfs_test_cases": total, "not_automated": max(0, total - len(automated)),
                        "automation_coverage": _rate(min(len(automated), total), total)})
        return out

    # ---- dashboard -----------------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        tfs = self.tfs_counts()
        latest = self.latest_results()
        passed = sum(r["status"] == "PASSED" for r in latest.values())
        perf = self.db.query("SELECT status, sla FROM perf_runs WHERE status IN ('PASSED','FAILED')")
        sla = Counter((loads(r["sla"]) or {}).get("status") for r in perf if r["sla"])
        test_data = self.db.query_one("SELECT COUNT(*) AS n FROM test_data")["n"]
        open_drafts = self.db.query_one("SELECT COUNT(*) AS n FROM defect_drafts WHERE status IN ('draft','needs_review')")["n"]
        return {
            "tfs": tfs,
            "acceptance_criteria": self.acceptance_criteria() if tfs.get("connected") and "requirement" in tfs else
            {"available": False, "reason": tfs.get("reason") or "The TFS project has no user story type."},
            "coverage": self.coverage(tfs),
            "test_data": test_data,
            "functional": {"scripts_run": len(latest), "passed": passed, "failed": len(latest) - passed,
                           "pass_rate": _rate(passed, len(latest))},
            "performance": {"runs": len(perf), "failed": sum(r["status"] == "FAILED" for r in perf),
                            "sla_pass": sla.get("PASS", 0), "sla_fail": sla.get("FAIL", 0),
                            "sla_pass_rate": _rate(sla.get("PASS", 0), sla.get("PASS", 0) + sla.get("FAIL", 0))},
            "open_defect_drafts": open_drafts,
            "insights": self.insights(tfs),
        }

    def insights(self, tfs: dict[str, Any]) -> dict[str, Any]:
        """Dashboard statistics (23.3.14): test data spread, defects, executions and runs. Recorded data only;
        a part that cannot be read is left out (the dashboard then shows "—"), never estimated."""
        out: dict[str, Any] = {}
        try:
            rows = self.db.query("SELECT tfs_test_case_id, test_case_ref, kind FROM test_data LIMIT ?", (MAX_ROWS,))
            per_tc = Counter(str(r["tfs_test_case_id"] or r["test_case_ref"]) for r in rows)
            with_tfs = {str(r["tfs_test_case_id"]) for r in rows if r["tfs_test_case_id"]}
            td: dict[str, Any] = {"rows": len(rows), "test_cases": len(per_tc), "tfs_test_cases_with_data": len(with_tfs),
                                  "valid": sum(r["kind"] == "valid" for r in rows),
                                  "invalid": sum(r["kind"] == "invalid" for r in rows),
                                  "top": [{"test_case": k, "rows": v} for k, v in per_tc.most_common(8)]}
            if tfs.get("connected") and "testcase" in tfs:
                td["tfs_test_cases_without_data"] = max(0, int(tfs["testcase"]) - len(with_tfs))
            out["test_data"] = td
        except Exception:  # an older database layout must not break the dashboard
            pass
        try:
            drafts = Counter(r["status"] for r in self.db.query("SELECT status FROM defect_drafts LIMIT ?", (MAX_ROWS,)))
            out["defect_drafts"] = dict(drafts)
        except Exception:
            pass
        if tfs.get("connected") and "open_defects" in tfs:
            try:
                total = self.tfs.count("bug")
                out["defects"] = {"total": total, "open": int(tfs["open_defects"]), "closed": max(0, total - int(tfs["open_defects"]))}
            except Exception:
                pass
        try:
            ex = self.db.query("SELECT status, started_at FROM execution_results WHERE status IN ('PASSED','FAILED')"
                               " ORDER BY started_at DESC LIMIT ?", (MAX_ROWS,))
            start, axis = _window(7)
            trend = {d: {"passed": 0, "failed": 0} for d in axis}
            for r in ex:
                d = _day(r["started_at"])
                if d in trend:
                    trend[d]["passed" if r["status"] == "PASSED" else "failed"] += 1
            passed = sum(r["status"] == "PASSED" for r in ex)
            out["executions"] = {"total": len(ex), "passed": passed, "failed": len(ex) - passed,
                                 "trend": [{"date": d.isoformat(), **v} for d, v in trend.items()]}
        except Exception:
            pass
        try:
            kinds: dict[str, dict[str, int]] = defaultdict(lambda: {"passed": 0, "failed": 0, "other": 0})
            # The run ID prefix says what the run did (TCGEN, TDATA, GEN, RUN, REC, ...); the kind is the fallback.
            for r in self.db.query("SELECT kind, status, run_id FROM runs ORDER BY started_at DESC LIMIT ?", (MAX_ROWS,)):
                key = "passed" if r["status"] == "PASSED" else "failed" if r["status"] == "FAILED" else "other"
                prefix = str(r["run_id"] or "").split("-", 1)[0]
                kinds[prefix if prefix.isalpha() and prefix.isupper() else (r["kind"] or "other")][key] += 1
            out["runs_by_kind"] = sorted(({"kind": k, **v} for k, v in kinds.items()),
                                         key=lambda x: -(x["passed"] + x["failed"] + x["other"]))
        except Exception:
            pass
        return out

    # ---- functional ----------------------------------------------------------------------------

    def functional(self, days: int = 30) -> dict[str, Any]:
        start, axis = _window(days)
        rows = [r for r in self.db.query(
            "SELECT run_id, script_id, test_case_id, status, started_at FROM execution_results"
            " WHERE status IN ('PASSED','FAILED') ORDER BY started_at LIMIT ?", (MAX_ROWS,)) if (_day(r["started_at"]) or start) >= start]
        names = {s["id"]: s["name"] for s in self.automation.scripts.list()}
        trend = {d: {"passed": 0, "failed": 0} for d in axis}
        by_script: dict[str, list[str]] = defaultdict(list)
        for r in rows:
            d = _day(r["started_at"])
            if d in trend:
                trend[d]["passed" if r["status"] == "PASSED" else "failed"] += 1
            by_script[r["script_id"]].append(r["status"])
        passed = sum(r["status"] == "PASSED" for r in rows)

        # Flaky: both outcomes in the window; flip rate = status changes between consecutive runs.
        flaky = []
        for sid, statuses in by_script.items():
            if len(set(statuses)) > 1:
                flips = sum(a != b for a, b in zip(statuses, statuses[1:]))
                flaky.append({"script_id": sid, "name": names.get(sid, sid), "runs": len(statuses),
                              "passed": statuses.count("PASSED"), "failed": statuses.count("FAILED"),
                              "flip_rate": round(100 * flips / (len(statuses) - 1), 1)})
        flaky.sort(key=lambda f: (-f["flip_rate"], -f["runs"]))

        heal_rows = [h for h in self.db.query(
            "SELECT script_id, result, applied, timestamp FROM healing_events ORDER BY id LIMIT ?", (MAX_ROWS,))
            if (_day(h["timestamp"]) or start) >= start]
        heal_trend = {d: {"healed": 0, "failed": 0} for d in axis}
        heal_by_script: dict[str, Counter] = defaultdict(Counter)
        for h in heal_rows:
            d = _day(h["timestamp"])
            key = "healed" if h["result"] in ("healed", "validated") else "failed"
            if d in heal_trend:
                heal_trend[d][key] += 1
            heal_by_script[h["script_id"]][key] += 1
            if h.get("applied"):
                heal_by_script[h["script_id"]]["applied"] += 1

        drafts = [d for d in self.db.query("SELECT source, status, created_at, tfs_id FROM defect_drafts LIMIT ?", (MAX_ROWS,))
                  if (_day(d["created_at"]) or start) >= start]
        defect_trend = {d: {"functional": 0, "performance": 0} for d in axis}
        for d in drafts:
            day = _day(d["created_at"])
            if day in defect_trend:
                defect_trend[day][d["source"]] += 1

        return {
            "days": days, "from": start.isoformat(), "to": axis[-1].isoformat(),
            "coverage": self.coverage(),
            "executions": len(rows), "passed": passed, "failed": len(rows) - passed,
            "pass_rate": _rate(passed, len(rows)), "failure_rate": _rate(len(rows) - passed, len(rows)),
            "trend": [{"date": d.isoformat(), **v} for d, v in trend.items()],
            "flaky": flaky[:20],
            "healing": {"total": len(heal_rows), "healed": sum(v["healed"] for v in heal_by_script.values()),
                        "trend": [{"date": d.isoformat(), **v} for d, v in heal_trend.items()],
                        "by_script": sorted(({"script_id": k, "name": names.get(k, k), **dict(v)} for k, v in heal_by_script.items()),
                                            key=lambda x: -(x.get("healed", 0) + x.get("failed", 0)))[:20]},
            "defects": {"drafts": len(drafts), "in_tfs": sum(1 for d in drafts if d["tfs_id"]),
                        "trend": [{"date": d.isoformat(), **v} for d, v in defect_trend.items()]},
        }

    # ---- performance ---------------------------------------------------------------------------

    def performance(self, days: int = 30, plan_id: str | None = None) -> dict[str, Any]:
        start, _axis = _window(days)
        q = "SELECT run_id, plan_id, kind, name, status, started_at, metrics, sla FROM perf_runs WHERE metrics IS NOT NULL"
        params: list[Any] = []
        if plan_id:
            q += " AND plan_id = ?"
            params.append(plan_id)
        rows = [r for r in self.db.query(q + " ORDER BY started_at LIMIT ?", [*params, MAX_ROWS])
                if (_day(r["started_at"]) or start) >= start]
        runs = []
        for r in rows:
            m, s = loads(r["metrics"]) or {}, loads(r["sla"]) if r["sla"] else None
            o = m.get("overall") or {}
            runs.append({"run_id": r["run_id"], "plan_id": r["plan_id"], "name": r["name"], "kind": r["kind"],
                         "status": r["status"], "started_at": r["started_at"], "sla": (s or {}).get("status"),
                         "p90_ms": o.get("p90_ms"), "p95_ms": o.get("p95_ms"), "p99_ms": o.get("p99_ms"),
                         "avg_ms": o.get("avg_ms"), "throughput": o.get("throughput"), "error_pct": o.get("error_pct"),
                         "samples": o.get("samples"), "peak_users": m.get("peak_threads"),
                         "requests": m.get("requests") or []})
        sla = Counter(r["sla"] for r in runs if r["sla"])
        plans: dict[str, dict[str, Any]] = {}
        for r in runs:
            p = plans.setdefault(r["plan_id"], {"plan_id": r["plan_id"], "name": r["name"], "runs": 0, "max_users": 0})
            p["runs"] += 1
            p["max_users"] = max(p["max_users"], r["peak_users"] or 0)
            p["name"] = r["name"]
        transactions = []
        if plan_id and runs:
            last = runs[-1]
            prev = runs[-2] if len(runs) > 1 else None
            prev_by = {q["label"]: q for q in (prev or {}).get("requests", [])}
            for t in last["requests"]:
                before = prev_by.get(t["label"])
                transactions.append({"label": t["label"], "samples": t["samples"], "p95_ms": t["p95_ms"], "p99_ms": t["p99_ms"],
                                     "error_pct": t["error_pct"], "throughput": t["throughput"],
                                     "previous_p95_ms": before["p95_ms"] if before else None})
        for r in runs:
            r.pop("requests")
        return {
            "days": days, "plan_id": plan_id, "runs": runs,
            "sla": {"pass": sla.get("PASS", 0), "fail": sla.get("FAIL", 0), "not_defined": sla.get("NOT_DEFINED", 0),
                    "pass_rate": _rate(sla.get("PASS", 0), sla.get("PASS", 0) + sla.get("FAIL", 0))},
            "max_users": max((r["peak_users"] or 0 for r in runs), default=0),
            "plans": sorted(plans.values(), key=lambda p: p["name"].lower()),
            "transactions": transactions,
        }
