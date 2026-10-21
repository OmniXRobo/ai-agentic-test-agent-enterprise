"""Agent runtime state derived only from published events.

Nothing here runs on a timer: a card is RUNNING exactly between the agent's
``agent.started`` and ``agent.completed``/``agent.failed`` events.
"""

from __future__ import annotations

import threading
from typing import Any

from ..agents.registry import AGENTS
from . import names
from .bus import QAEvent

TERMINAL = {names.AGENT_COMPLETED: "PASSED", names.AGENT_FAILED: "FAILED"}


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
                )
            elif event.name in TERMINAL:
                st.update(
                    status=TERMINAL[event.name],
                    activity=event.activity or st["activity"],
                    progress=100 if event.name == names.AGENT_COMPLETED else st["progress"],
                    finished_at=event.timestamp,
                )
            elif st["status"] == "RUNNING" and event.run_id == st["run_id"]:
                if event.activity:
                    st["activity"] = event.activity
                if event.progress is not None:
                    st["progress"] = event.progress
            if event.source:
                st["source"] = event.source
            st["last_event_id"] = event.id

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(self._state[a.id]) for a in AGENTS]

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
