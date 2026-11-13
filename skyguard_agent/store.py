"""The persistent knowledge database: SQLite (WAL) with numbered migrations, the same technology SkyGuard QA uses.

Learning lives here, never in model weights, so it survives application, Windows and model restarts and a change of
LLM. SkyGuard keeps the file at ``<data>/opsagent/knowledge.sqlite3``; the standalone companion at
``%LOCALAPPDATA%\\SkyGuardAgent\\data\\knowledge.sqlite3`` (or, with SkyGuard integration on, SkyGuard's file).
No secret is ever written to this database: accounts hold a reference into the credential vault.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

MIGRATIONS: list[str] = [
    # 1: applications, accounts, versioned knowledge, locator memory, runs, chat, audit
    """
    CREATE TABLE IF NOT EXISTS applications (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        base_url TEXT NOT NULL,
        origins TEXT NOT NULL DEFAULT '[]',
        environment TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        app_version TEXT NOT NULL DEFAULT '',
        mode TEXT NOT NULL DEFAULT 'testing',
        profile TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'active',
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS accounts (
        id TEXT PRIMARY KEY,
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        label TEXT NOT NULL,
        login_id TEXT NOT NULL,
        role_key TEXT NOT NULL DEFAULT '',
        role_description TEXT NOT NULL DEFAULT '',
        environment TEXT NOT NULL DEFAULT '',
        owner TEXT NOT NULL DEFAULT '',
        credential_ref TEXT,
        consent_at TEXT,
        expires_at TEXT,
        auth_status TEXT NOT NULL DEFAULT 'not_verified',
        verified_identity TEXT,
        verified_role TEXT,
        verified_at TEXT,
        revoked INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (application_id, label)
    );
    CREATE TABLE IF NOT EXISTS knowledge (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        key TEXT NOT NULL,
        version INTEGER NOT NULL,
        state TEXT NOT NULL,
        data TEXT NOT NULL,
        source TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 0,
        app_version TEXT NOT NULL DEFAULT '',
        created_by TEXT,
        approver TEXT,
        approved_at TEXT,
        note TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (application_id, kind, key, version)
    );
    CREATE INDEX IF NOT EXISTS ix_knowledge_current ON knowledge(application_id, kind, key, version);
    CREATE INDEX IF NOT EXISTS ix_knowledge_state ON knowledge(state);
    CREATE TABLE IF NOT EXISTS locators (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        screen TEXT NOT NULL,
        label TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'control',
        fingerprint TEXT NOT NULL,
        alternatives TEXT NOT NULL DEFAULT '[]',
        confidence REAL NOT NULL DEFAULT 1,
        version INTEGER NOT NULL DEFAULT 1,
        history TEXT NOT NULL DEFAULT '[]',
        updated_at TEXT NOT NULL,
        UNIQUE (application_id, screen, label)
    );
    CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY,
        application_id TEXT NOT NULL,
        workflow_key TEXT,
        interface TEXT NOT NULL,
        status TEXT NOT NULL,
        request TEXT NOT NULL DEFAULT '{}',
        plan TEXT NOT NULL DEFAULT '{}',
        preview TEXT NOT NULL DEFAULT '{}',
        preview_hash TEXT,
        confirmed_by TEXT,
        confirmed_at TEXT,
        checkpoint TEXT NOT NULL DEFAULT '{}',
        summary TEXT NOT NULL DEFAULT '{}',
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        finished_at TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_runs_created ON runs(created_at);
    CREATE TABLE IF NOT EXISTS run_items (
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        seq INTEGER NOT NULL,
        status TEXT NOT NULL,
        record TEXT NOT NULL,
        validation TEXT NOT NULL DEFAULT '[]',
        result TEXT NOT NULL DEFAULT '{}',
        attempts INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (run_id, seq)
    );
    CREATE TABLE IF NOT EXISTS run_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        at TEXT NOT NULL,
        kind TEXT NOT NULL,
        message TEXT NOT NULL,
        details TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS ix_run_events ON run_events(run_id, id);
    CREATE TABLE IF NOT EXISTS learning_sessions (
        id TEXT PRIMARY KEY,
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        mode TEXT NOT NULL,
        status TEXT NOT NULL,
        data TEXT NOT NULL DEFAULT '{}',
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS chat (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL,
        at TEXT NOT NULL,
        speaker TEXT NOT NULL,
        text TEXT NOT NULL,
        interface TEXT NOT NULL,
        user TEXT,
        data TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS ix_chat_conv ON chat(conversation_id, id);
    CREATE TABLE IF NOT EXISTS audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        actor TEXT,
        interface TEXT,
        action TEXT NOT NULL,
        target TEXT,
        details TEXT NOT NULL DEFAULT '{}',
        prev_hash TEXT,
        hash TEXT NOT NULL
    );
    CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
        BEGIN SELECT RAISE(ABORT, 'audit records cannot be changed'); END;
    CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
        BEGIN SELECT RAISE(ABORT, 'audit records cannot be deleted'); END;
    """,
]


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=15)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=15000")
        self.migrate()

    @property
    def schema_version(self) -> int:
        row = self.query_one("SELECT MAX(version) AS v FROM schema_version")
        return int((row or {}).get("v") or 0)

    def migrate(self) -> None:
        with self._lock:
            self._conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = row["v"] or 0
            for index, script in enumerate(MIGRATIONS, start=1):
                if index > current:
                    self._conn.executescript("BEGIN;" + script + f"INSERT INTO schema_version VALUES ({index}); COMMIT;")

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def transaction(self) -> "_Tx":
        return _Tx(self)

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, tuple(params)).fetchall()]

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def backup_to(self, dest: Path | str) -> None:
        """A consistent copy while the database is in use (the online backup API includes the WAL)."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            target = sqlite3.connect(str(dest))
            try:
                self._conn.backup(target)
            finally:
                target.close()

    def integrity(self) -> str:
        row = self.query_one("PRAGMA integrity_check")
        return str(next(iter(row.values()))) if row else "unknown"

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class _Tx:
    def __init__(self, store: Store) -> None:
        self.store = store

    def __enter__(self) -> Store:
        self.store._lock.acquire()
        self.store._conn.execute("BEGIN IMMEDIATE")
        return self.store

    def __exit__(self, exc_type: Any, *_: Any) -> None:
        try:
            self.store._conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.store._lock.release()
