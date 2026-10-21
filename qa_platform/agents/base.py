"""Base agent: the only way agent cards change state.

Usage::

    with agent.activity("Reading User Story #118", run_id=run_id, user_story_id="118") as act:
        act.progress(40, "Acceptance Criteria retrieved: 4")
        ...

Entering emits ``agent.started`` (RUNNING); leaving normally emits
``agent.completed`` (PASSED); an exception emits ``agent.failed`` (FAILED) and
is re-raised.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from ..clock import now
from ..events import names
from ..events.bus import EventBus, QAEvent
from ..logging_setup import get_logger
from .registry import get_agent


def new_run_id(prefix: str = "RUN") -> str:
    return f"{prefix}-{now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"


class Activity:
    def __init__(self, agent: "BaseAgent", run_id: str, context: dict[str, Any]) -> None:
        self.agent = agent
        self.run_id = run_id
        self.context = context
        self.result_activity: str | None = None
        self.result_details: dict[str, Any] = {}

    def progress(self, percent: int | None, activity: str, **details: Any) -> None:
        self.agent.emit(names.AGENT_PROGRESS, activity, run_id=self.run_id, progress=percent,
                        details=details, **self.context)

    def log(self, activity: str, **details: Any) -> None:
        self.agent.emit(names.AGENT_LOG, activity, run_id=self.run_id, details=details, **self.context)

    def event(self, name: str, activity: str, progress: int | None = None, **details: Any) -> None:
        """Emit a domain event (e.g. execution.step) attributed to this agent."""
        self.agent.emit(name, activity, run_id=self.run_id, progress=progress, details=details, **self.context)

    def complete(self, activity: str, **details: Any) -> None:
        """Set the completion message used when the block exits successfully."""
        self.result_activity = activity
        self.result_details = details


class BaseAgent:
    agent_id: str = ""

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.definition = get_agent(self.agent_id)
        self.log = get_logger(f"agent.{self.agent_id}")

    @property
    def name(self) -> str:
        return self.definition.name

    def emit(
        self,
        event_name: str,
        activity: str,
        *,
        run_id: str | None = None,
        status: str | None = None,
        progress: int | None = None,
        details: dict[str, Any] | None = None,
        test_case_id: str | None = None,
        user_story_id: str | None = None,
    ) -> QAEvent:
        event = self.bus.publish(
            QAEvent(
                name=event_name,
                agent_id=self.agent_id,
                agent_name=self.name,
                status=status,
                activity=activity,
                progress=progress,
                run_id=run_id,
                test_case_id=test_case_id,
                user_story_id=user_story_id,
                source=self.definition.source,
                details=details or {},
            )
        )
        self.log.info(activity, extra={"run_id": run_id, "agent": self.name, "operation": event_name,
                                       "test_case_id": test_case_id, "user_story_id": user_story_id})
        return event

    @contextmanager
    def activity(
        self,
        description: str,
        *,
        run_id: str | None = None,
        test_case_id: str | None = None,
        user_story_id: str | None = None,
        **details: Any,
    ) -> Iterator[Activity]:
        run_id = run_id or new_run_id()
        context = {"test_case_id": test_case_id, "user_story_id": user_story_id}
        act = Activity(self, run_id, context)
        self.emit(names.AGENT_STARTED, description, run_id=run_id, status="RUNNING", progress=0,
                  details=details, **context)
        try:
            yield act
        except Exception as exc:
            self.emit(names.AGENT_FAILED, f"{self.name} FAILED: {exc}", run_id=run_id, status="FAILED",
                      details={"error": str(exc), "error_type": type(exc).__name__}, **context)
            raise
        self.emit(names.AGENT_COMPLETED, act.result_activity or f"{self.name} PASSED", run_id=run_id,
                  status="PASSED", progress=100, details=act.result_details, **context)
