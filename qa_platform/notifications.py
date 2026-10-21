"""Notification service, fed by the event bus.

Unread count is the number of rows with read = 0; nothing is synthesized.
"""

from __future__ import annotations

from typing import Any

from .events import names
from .events.bus import EventBus, QAEvent
from .storage.db import Database

# event name -> (kind, severity, title)
RULES: dict[str, tuple[str, str, str]] = {
    names.EXECUTION_COMPLETED: ("execution", "success", "Test execution finished"),
    names.EXECUTION_FAILED: ("execution", "error", "Functional test failed"),
    names.PERFORMANCE_COMPLETED: ("performance", "success", "JMeter test completed"),
    names.PERFORMANCE_FAILED: ("performance", "error", "Performance test failed"),
    names.DEFECT_CREATED: ("defect", "info", "Defect created"),
    names.DEFECT_DUPLICATE_FOUND: ("defect", "warning", "Existing defect detected"),
    names.DEFECT_FAILED: ("defect", "error", "Defect creation failed"),
    names.AUTOMATION_GENERATION_COMPLETED: ("automation", "success", "Automation generation completed"),
    names.SELFHEALING_COMPLETED: ("selfhealing", "warning", "Self-healing occurred"),
    names.REPORT_COMPLETED: ("report", "success", "Report ready"),
    names.REPORT_FAILED: ("report", "error", "Report failed"),
    names.AI_UNAVAILABLE: ("system", "error", "On-Premise AI unavailable"),
    names.TFS_UNAVAILABLE: ("system", "error", "TFS connection unavailable"),
    names.SYSTEM_DIAGNOSTICS_COMPLETED: ("system", "info", "System diagnostics finished"),
}


class NotificationService:
    def __init__(self, db: Database, bus: EventBus) -> None:
        self.db = db
        self.bus = bus
        bus.add_listener(self._on_event)

    def _on_event(self, event: QAEvent) -> None:
        rule = RULES.get(event.name)
        if rule is None:
            return
        kind, severity, title = rule
        if event.name == names.SYSTEM_DIAGNOSTICS_COMPLETED and event.details.get("failed"):
            severity = "warning"
        self.create(kind, severity, title, event.activity, run_id=event.run_id, event_id=event.id,
                    link=event.details.get("link"))

    def create(
        self,
        kind: str,
        severity: str,
        title: str,
        message: str | None = None,
        *,
        run_id: str | None = None,
        event_id: int | None = None,
        link: str | None = None,
    ) -> dict[str, Any]:
        from .clock import now_iso

        ts = now_iso()
        cur = self.db.execute(
            "INSERT INTO notifications (timestamp, kind, severity, title, message, run_id, link, event_id)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (ts, kind, severity, title, message, run_id, link, event_id),
        )
        note = {
            "id": cur.lastrowid, "timestamp": ts, "kind": kind, "severity": severity, "title": title,
            "message": message, "run_id": run_id, "link": link, "event_id": event_id, "read": 0,
        }
        self.bus.broadcast({"type": "notification", "notification": note, "unread": self.unread_count()})
        return note

    def list(self, limit: int = 50, unread_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE read = 0" if unread_only else ""
        limit = max(1, min(int(limit), 500))
        return self.db.query(f"SELECT * FROM notifications {where} ORDER BY id DESC LIMIT ?", (limit,))

    def unread_count(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM notifications WHERE read = 0")
        return int(row["n"]) if row else 0

    def mark_read(self, ids: list[int] | None = None) -> int:
        if ids:
            placeholders = ",".join("?" for _ in ids)
            cur = self.db.execute(f"UPDATE notifications SET read = 1 WHERE id IN ({placeholders})", ids)
        else:
            cur = self.db.execute("UPDATE notifications SET read = 1 WHERE read = 0")
        self.bus.broadcast({"type": "unread", "unread": self.unread_count()})
        return cur.rowcount
