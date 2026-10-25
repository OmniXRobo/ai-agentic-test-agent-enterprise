"""Agent runtime state derived only from published events.

Nothing here runs on a timer: a card is RUNNING exactly between the agent's
``agent.started`` and ``agent.completed``/``agent.failed`` events.

``status`` keeps its original values (WAITING, RUNNING, PASSED, FAILED). The
normalized ``state`` shown on the runtime cards is derived from it:

* RUNNING   - between agent.started and its end event
* PAUSED    - running, but waiting for the tester (agent.paused until agent.resumed)
* WAITING   - queued: another agent of the same pipeline run is working and this one comes later
* COMPLETED - finished successfully less than COMPLETED_HOLD_SECONDS ago
* FAILED    - the last run failed (until the agent starts again)
* READY     - idle and available
"""

from __future__ import annotations

import threading
from typing import Any

from ..agents.registry import AGENTS
from ..clock import now, parse_iso
from . import names
from .bus import QAEvent

TERMINAL = {names.AGENT_COMPLETED: "PASSED", names.AGENT_FAILED: "FAILED"}
COMPLETED_HOLD_SECONDS = 30

# Agents that one run passes through in this order (they share the run ID).
PIPELINES: tuple[tuple[str, ...], ...] = (
    ("requirements", "test_planning", "test_case_design", "test_data"),
    ("jmeter_load", "performance_engineering"),
)


def derive_state(st: dict[str, Any], all_states: dict[str, dict[str, Any]], at: Any = None) -> dict[str, Any]:
    """Normalized card state for one agent: state, last_result and what a queued agent waits for."""
    status = st.get("status")
    last = {"PASSED": "COMPLETED", "FAILED": "FAILED"}.get(status or "")
    if status == "RUNNING":
        return {"state": "PAUSED" if st.get("paused") else "RUNNING", "last_result": None, "waiting_for": None}
    for pipeline in PIPELINES:
        if st["agent_id"] not in pipeline:
            continue
        mine = pipeline.index(st["agent_id"])
        for other in pipeline[:mine]:
            o = all_states.get(other) or {}
            if o.get("status") == "RUNNING" and o.get("run_id") and o.get("run_id") != st.get("run_id"):
                return {"state": "WAITING", "last_result": last, "waiting_for": other}
    if status == "FAILED":
        return {"state": "FAILED", "last_result": "FAILED", "waiting_for": None}
    if status == "PASSED" and st.get("finished_at"):
        try:
            age = ((at or now()) - parse_iso(st["finished_at"])).total_seconds()
        except (TypeError, ValueError):
            age = COMPLETED_HOLD_SECONDS
        if age < COMPLETED_HOLD_SECONDS:
            return {"state": "COMPLETED", "last_result": "COMPLETED", "waiting_for": None}
    return {"state": "READY", "last_result": last, "waiting_for": None}


class AgentStateManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, dict[str, Any]] = {
            a.id: {
                "agent_id": a.id,
                "status": "WAITING",
                "activity": None,
                "progress": None,
                "run_id": None,
                "source": a.source,
                "started_at": None,
                "finished_at": None,
                "paused": False,
                "last_event_id": None,
            }
            for a in AGENTS
        }

    def apply(self, event: QAEvent) -> None:
        if not event.agent_id or event.agent_id not in self._state:
            return
        with self._lock:
            st = self._state[event.agent_id]
            if event.name == names.AGENT_STARTED:
                st.update(
                    status="RUNNING",
                    activity=event.activity,
                    progress=event.progress,
                    run_id=event.run_id,
                    started_at=event.timestamp,
                    finished_at=None,
                    paused=False,
                )
            elif event.name in TERMINAL:
                st.update(
                    status=TERMINAL[event.name],
                    activity=event.activity or st["activity"],
                    progress=100 if event.name == names.AGENT_COMPLETED else st["progress"],
                    finished_at=event.timestamp,
                    paused=False,
                )
            elif st["status"] == "RUNNING" and event.run_id == st["run_id"]:
                if event.name == names.AGENT_PAUSED:
                    st["paused"] = True
                elif event.name == names.AGENT_RESUMED:
                    st["paused"] = False
                if event.activity:
                    st["activity"] = event.activity
                if event.progress is not None:
                    st["progress"] = event.progress
            if event.source:
                st["source"] = event.source
            st["last_event_id"] = event.id

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            states = {k: dict(v) for k, v in self._state.items()}
        at = now()
        return [{**states[a.id], **derive_state(states[a.id], states, at)} for a in AGENTS]

    def restore(self, last_lifecycle_events: list[dict[str, Any]]) -> None:
        """Rebuild state at startup from each agent's last persisted lifecycle event.

        An agent whose last event is ``agent.started`` was cut off by a shutdown,
        so it is shown as FAILED (interrupted) rather than RUNNING forever.
        """
        for row in last_lifecycle_events:
            st = self._state.get(row.get("agent_id") or "")
            if st is None:
                continue
            with self._lock:
                st["run_id"] = row.get("run_id")
                st["last_event_id"] = row.get("id")
                if row["name"] == names.AGENT_STARTED:
                    st.update(
                        status="FAILED",
                        activity="Interrupted: the runtime stopped before this agent finished.",
                        started_at=row.get("timestamp"),
                    )
                else:
                    st.update(
                        status=TERMINAL.get(row["name"], "WAITING"),
                        activity=row.get("activity"),
                        progress=row.get("progress"),
                        finished_at=row.get("timestamp"),
                    )
