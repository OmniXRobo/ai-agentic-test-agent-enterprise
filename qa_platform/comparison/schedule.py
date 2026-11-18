"""23.12.0: Integration Comparison on Agent Workflow and in Scheduled Runs, and its review drafts.

* The Integration Comparison Agent card shows each comparison run (RUNNING, then COMPLETED or FAILED with the
  comparison's own result).
* Scheduled Runs can play a saved comparison (``CMP:<comparison id>``). Its result is PASS/FAIL from the
  deterministic comparison: a confirmed mismatch fails the scheduled item; a comparison that could not conclude
  (INCOMPLETE / NEEDS_REVIEW) is reported as such, never as a pass.
* Every confirmed mismatch (result FAIL) of a run becomes a Defect Review draft (at most 25 per run), with the
  application names, record key, expected and actual values, rule, filters and evidence that the comparison
  recorded. Nothing is published to TFS without a person's approval.
"""

from __future__ import annotations

from typing import Any

from ..agents.base import BaseAgent
from ..clock import now_iso
from ..errors import QAError
from ..logging_setup import get_logger

log = get_logger("comparison.schedule")
MAX_DRAFTS = 25


class IntegrationComparisonAgent(BaseAgent):
    agent_id = "integration_comparison"


def workspace(rt: Any) -> Any:
    svc = getattr(rt, "comparison", None)
    if svc is None:
        raise QAError("CMP_UNAVAILABLE", "The integration comparison is not available.", status=503)
    ws = getattr(svc, "workspace", None)
    if ws is None:
        from .workspace import Workspace

        ws = svc.workspace = Workspace(svc)
    return ws


def run_with_card(rt: Any, pid: str, label: str | None = None, *, drafts: bool = True) -> dict[str, Any]:
    ws = workspace(rt)
    agent = IntegrationComparisonAgent(rt.bus)
    proj = ws.svc.project(pid)
    with agent.activity(f"Comparing {proj['name']}" + (f" ({label})" if label else "")) as act:
        out = ws.run(pid, label)
        summary = out.get("summary") or {}
        act.progress(80, f"Result {out.get('result')}: {summary.get('failed', 0)} mismatch(es), "
                         f"{summary.get('warnings', 0)} warning(s)")
        made = auto_drafts(rt, ws, out["id"]) if drafts else []
        out["review_drafts"] = made
        if out.get("result") == "FAIL":
            raise _ComparisonFailed(out, f"Comparison {proj['name']}: {summary.get('failed', 0)} confirmed mismatch(es)"
                                         + (f", {len(made)} draft(s) in Defect Review" if made else ""))
        act.complete(f"Comparison {proj['name']}: {out.get('result')}")
    return out


class _ComparisonFailed(Exception):
    """Ends the agent card as FAILED with the comparison's result; the caller still gets the execution."""

    def __init__(self, execution: dict[str, Any], message: str) -> None:
        super().__init__(message)
        self.execution = execution


def run(rt: Any, pid: str, label: str | None = None, *, drafts: bool = True) -> dict[str, Any]:
    try:
        return run_with_card(rt, pid, label, drafts=drafts)
    except _ComparisonFailed as exc:
        return exc.execution


def auto_drafts(rt: Any, ws: Any, exe: str) -> list[int]:
    defects = getattr(rt, "defects", None)
    if defects is None or defects.mode == "disabled":
        return []
    e = ws.execution(exe)
    made: list[int] = []
    for row in [r for r in (e.get("detail") or {}).get("rows") or [] if r.get("result") == "FAIL"][:MAX_DRAFTS]:
        try:
            d = ws.create_defect(exe, row["id"])
            draft = ws.svc.review_draft(d["id"])
            made.append(draft["id"])
        except Exception as exc:  # one row must not stop the others
            log.warning(f"Review draft for comparison row {row.get('id')} not made: {exc}")
    return made


class ComparisonScheduleProvider:
    prefixes = ("CMP:",)

    def __init__(self, rt: Any) -> None:
        self.rt = rt

    def handles(self, sid: str) -> bool:
        return sid.startswith(self.prefixes)

    def info(self, sid: str) -> dict[str, Any]:
        ws = workspace(self.rt)
        pid = sid[4:]
        proj = ws.svc.project(pid)
        return {"id": sid, "name": f"Integration Comparison: {proj['name']}", "kind": "comparison",
                "page": "Integration Comparison", "application": None, "rows": 0, "test_type": "Integration Comparison",
                "mode": "comparison", "mode_reason": "Integration Comparison reads its applications as configured "
                                                     "(captured data, files or the guided recording)."}

    def items(self) -> list[dict[str, Any]]:
        try:
            ws = workspace(self.rt)
        except QAError:
            return []
        return [self.info(f"CMP:{c['id']}") for c in ws.comparisons() if c.get("workspace")]

    def run(self, sid: str, run_id: str, options: dict[str, Any]) -> dict[str, Any]:
        started = now_iso()
        try:
            out = run(self.rt, sid[4:], label=f"scheduled {run_id}")
        except QAError as exc:
            return {"status": "ERROR", "error": exc.message, "started_at": started, "finished_at": now_iso()}
        result = out.get("result")
        s = out.get("summary") or {}
        status = "PASSED" if result in ("PASS", "PASS*", "ACCEPTED") else "FAILED"
        err = None if status == "PASSED" else (f"Result {result}: {s.get('failed', 0)} mismatch(es), {s.get('warnings', 0)} "
                                               f"warning(s)" + (f", {len(out.get('review_drafts') or [])} draft(s) for review"
                                                                 if out.get("review_drafts") else ""))
        return {"status": status, "error": err, "started_at": started, "finished_at": now_iso(),
                "link": "#/integration-comparison"}
