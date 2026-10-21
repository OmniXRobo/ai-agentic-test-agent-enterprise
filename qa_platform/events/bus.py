"""Central runtime event bus.

``publish`` is thread-safe: agents running in worker threads (Playwright,
JMeter monitors) and async request handlers can both call it. Every event is
persisted, applied to the agent state manager, turned into a notification when
relevant, and fanned out to live subscribers (SSE clients).
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from ..clock import now_iso
from ..logging_setup import get_logger, redact
from ..storage.db import Database, dumps, loads
from . import names

log = get_logger("events")

STATUSES = ("WAITING", "RUNNING", "PASSED", "FAILED")


@dataclass
class QAEvent:
    name: str
    agent_id: str | None = None
    agent_name: str | None = None
    status: str | None = None
    activity: str | None = None
    progress: int | None = None
    run_id: str | None = None
    test_case_id: str | None = None
    user_story_id: str | None = None
    source: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=now_iso)
    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Listener = Callable[[QAEvent], None]


class EventBus:
    def __init__(self, db: Database, history_limit: int = 20000) -> None:
        self.db = db
        self.history_limit = history_limit
        self._listeners: list[Listener] = []
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._sub_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._publish_count = 0

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def add_listener(self, listener: Listener) -> None:
        """Synchronous in-process listener (state manager, notifications)."""
        self._listeners.append(listener)

    def publish(self, event: QAEvent) -> QAEvent:
        if event.name not in names.ALL_EVENTS:
            raise ValueError(f"Unknown event name: {event.name}")
        if event.status is not None and event.status not in STATUSES:
            raise ValueError(f"Unknown status: {event.status}")
        if event.progress is not None:
            event.progress = max(0, min(100, int(event.progress)))
        event.details = redact(event.details or {})
        cur = self.db.execute(
            "INSERT INTO events (timestamp, name, agent_id, agent_name, status, activity, progress, run_id,"
            " test_case_id, user_story_id, source, details) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event.timestamp,
                event.name,
                event.agent_id,
                event.agent_name,
                event.status,
                event.activity,
                event.progress,
                event.run_id,
                event.test_case_id,
                event.user_story_id,
                event.source,
                dumps(event.details),
            ),
        )
        event.id = cur.lastrowid
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:  # a broken listener must not break publishing
                log.exception("Event listener failed", extra={"operation": event.name})
        self._fan_out({"type": "event", "event": event.to_dict()})
        self._publish_count += 1
        if self._publish_count % 500 == 0:
            self._trim()
        return event

    def broadcast(self, message: dict[str, Any]) -> None:
        """Send a non-event message (e.g. notification) to live subscribers."""
        self._fan_out(message)

    def _fan_out(self, message: dict[str, Any]) -> None:
        with self._sub_lock:
            subscribers = list(self._subscribers)
        if not subscribers:
            return
        loop = self._loop
        for queue in subscribers:
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(self._offer, queue, message)
            else:
                self._offer(queue, message)

    @staticmethod
    def _offer(queue: asyncio.Queue[dict[str, Any]], message: dict[str, Any]) -> None:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            # Slow client: drop oldest so the stream stays live.
            try:
                queue.get_nowait()
                queue.put_nowait(message)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    def subscribe(self, maxsize: int = 1000) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        with self._sub_lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._sub_lock:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        with self._sub_lock:
            return len(self._subscribers)

    def recent(
        self,
        limit: int = 100,
        after_id: int | None = None,
        run_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if after_id is not None:
            clauses.append("id > ?")
            params.append(after_id)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = max(1, min(int(limit), 1000))
        rows = self.db.query(f"SELECT * FROM events {where} ORDER BY id DESC LIMIT ?", [*params, limit])
        for row in rows:
            row["details"] = loads(row["details"])
        rows.reverse()
        return rows

    def _trim(self) -> None:
        self.db.execute(
            "DELETE FROM events WHERE id <= (SELECT MAX(id) FROM events) - ?",
            (self.history_limit,),
        )
