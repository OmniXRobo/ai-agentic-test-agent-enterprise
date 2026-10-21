"""SQLite persistence (stdlib only, WAL mode, safe across threads).

Schema changes are applied as numbered migrations so older data files keep
loading after upgrades.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

MIGRATIONS: list[str] = [
    # 1: events, notifications
    """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        name TEXT NOT NULL,
        agent_id TEXT,
        agent_name TEXT,
        status TEXT,
        activity TEXT,
        progress INTEGER,
        run_id TEXT,
        test_case_id TEXT,
        user_story_id TEXT,
        source TEXT,
        details TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_events_run ON events(run_id);
    CREATE INDEX IF NOT EXISTS ix_events_agent ON events(agent_id, id);
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        kind TEXT NOT NULL,
        severity TEXT NOT NULL,
        title TEXT NOT NULL,
        message TEXT,
        run_id TEXT,
        link TEXT,
        event_id INTEGER,
        read INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS ix_notifications_read ON notifications(read, id);
    """,
    # 2: runs
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        title TEXT,
        status TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        user_story_id TEXT,
        test_case_id TEXT,
        summary TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_runs_started ON runs(started_at);
    """,
    # 3: AI test design results and test data
    """
    CREATE TABLE IF NOT EXISTS design_results (
        run_id TEXT PRIMARY KEY,
        user_story_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        result TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_design_story ON design_results(user_story_id);
    CREATE TABLE IF NOT EXISTS test_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_story_id TEXT,
        test_case_ref TEXT NOT NULL,
        tfs_test_case_id INTEGER,
        name TEXT NOT NULL,
        kind TEXT,
        dataset TEXT NOT NULL,
        source_run_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_test_data_tc ON test_data(tfs_test_case_id);
    """,
]


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            self._conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = row["v"] or 0
            for index, script in enumerate(MIGRATIONS, start=1):
                if index <= current:
                    continue
                self._conn.executescript("BEGIN;" + script + f"INSERT INTO schema_version VALUES ({index}); COMMIT;")

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(value: str | None) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}
