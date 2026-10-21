"""Execution history: one row per run (functional, performance, diagnostics...)."""

from __future__ import annotations

from typing import Any

from .clock import now_iso
from .storage.db import Database, dumps, loads


class RunStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def start(self, run_id: str, kind: str, title: str, *, user_story_id: str | None = None,
              test_case_id: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO runs (run_id, kind, title, status, started_at, user_story_id, test_case_id)"
            " VALUES (?,?,?,?,?,?,?)",
            (run_id, kind, title, "RUNNING", now_iso(), user_story_id, test_case_id),
        )

    def finish(self, run_id: str, status: str, summary: dict[str, Any] | None = None) -> None:
        self.db.execute(
            "UPDATE runs SET status = ?, finished_at = ?, summary = ? WHERE run_id = ?",
            (status, now_iso(), dumps(summary or {}), run_id),
        )

    def get(self, run_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        if row:
            row["summary"] = loads(row["summary"])
        return row

    def list(self, kind: str | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        params: list[Any] = []
        where = ""
        if kind:
            where = "WHERE kind = ?"
            params.append(kind)
        # Summaries can be large; list views get the light columns only.
        return self.db.query(
            f"SELECT run_id, kind, title, status, started_at, finished_at, user_story_id, test_case_id"
            f" FROM runs {where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
            [*params, limit, max(0, int(offset))],
        )

    def mark_interrupted(self) -> int:
        cur = self.db.execute(
            "UPDATE runs SET status = 'FAILED', finished_at = ?, summary = ? WHERE status = 'RUNNING'",
            (now_iso(), dumps({"error": "Interrupted: the runtime stopped before this run finished."})),
        )
        return cur.rowcount

    def counts(self) -> dict[str, dict[str, int]]:
        rows = self.db.query("SELECT kind, status, COUNT(*) AS n FROM runs GROUP BY kind, status")
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["kind"], {})[r["status"]] = r["n"]
        return out
