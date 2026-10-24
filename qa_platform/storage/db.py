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
    # 4: automation execution results, self-healing evidence, application credentials, discovery
    """
    CREATE TABLE IF NOT EXISTS execution_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        script_id TEXT NOT NULL,
        test_case_id TEXT,
        user_story_id TEXT,
        status TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        browser TEXT,
        base_url TEXT,
        failed_step INTEGER,
        error TEXT,
        steps TEXT,
        evidence TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_exec_run ON execution_results(run_id);
    CREATE INDEX IF NOT EXISTS ix_exec_tc ON execution_results(test_case_id);
    CREATE TABLE IF NOT EXISTS healing_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        run_id TEXT NOT NULL,
        script_id TEXT NOT NULL,
        test_case_id TEXT,
        step_index INTEGER NOT NULL,
        page_url TEXT,
        original_selector TEXT NOT NULL,
        healed_selector TEXT,
        confidence REAL,
        candidates TEXT,
        screenshot TEXT,
        result TEXT NOT NULL,
        applied INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS ix_heal_script ON healing_events(script_id);
    CREATE TABLE IF NOT EXISTS app_credentials (
        id TEXT PRIMARY KEY,
        application TEXT NOT NULL,
        role TEXT NOT NULL,
        username TEXT NOT NULL,
        login_url TEXT,
        updated_at TEXT NOT NULL,
        UNIQUE (application, role)
    );
    CREATE TABLE IF NOT EXISTS discoveries (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        base_url TEXT NOT NULL,
        created_at TEXT NOT NULL,
        result TEXT NOT NULL
    );
    """,
    # 5: performance test plans and results
    """
    CREATE TABLE IF NOT EXISTS perf_plans (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        plan TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS perf_runs (
        run_id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        status TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        folder TEXT NOT NULL,
        metrics TEXT,
        sla TEXT,
        analysis TEXT,
        error TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_perf_runs_plan ON perf_runs(plan_id);
    """,
    # 6: defect drafts (Review Before Create) and their link to TFS defects
    """
    CREATE TABLE IF NOT EXISTS defect_drafts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        source_run_id TEXT NOT NULL,
        signature TEXT NOT NULL,
        status TEXT NOT NULL,
        category TEXT NOT NULL,
        product_defect INTEGER NOT NULL,
        title TEXT NOT NULL,
        severity INTEGER NOT NULL,
        priority INTEGER NOT NULL,
        repro_steps TEXT NOT NULL,
        facts TEXT NOT NULL,
        evidence TEXT NOT NULL,
        duplicates TEXT,
        occurrences TEXT NOT NULL,
        test_case_id TEXT,
        user_story_id TEXT,
        tfs_id INTEGER,
        note TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_drafts_sig ON defect_drafts(signature, status);
    """,
    # 7: voice commands (what was heard, how it was understood, what the agent answered)
    """
    CREATE TABLE IF NOT EXISTS voice_commands (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL,
        text TEXT NOT NULL,
        source TEXT NOT NULL,
        command TEXT,
        params TEXT,
        via TEXT,
        status TEXT NOT NULL,
        reply TEXT,
        final_reply TEXT,
        created_at TEXT NOT NULL,
        finished_at TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_voice_conv ON voice_commands(conversation_id, id);
    """,
    # 8: generated reports (files live under <data>/reports/<id>/)
    """
    CREATE TABLE IF NOT EXISTS reports (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        scope TEXT NOT NULL,
        params TEXT,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        finished_at TEXT,
        summary TEXT,
        error TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_reports_created ON reports(created_at);
    """,
    # 9: AI-Driven Automation framework (Page Object Model) scripts, versions, generation jobs;
    #    environment-specific application credentials; the Page Object locator a healing applies to
    """
    CREATE TABLE IF NOT EXISTS framework_scripts (
        id TEXT PRIMARY KEY,
        method TEXT NOT NULL,
        project TEXT,
        sprint TEXT,
        user_story_id TEXT,
        user_story_title TEXT,
        acceptance_criteria TEXT,
        test_case_id TEXT,
        test_case_title TEXT,
        file TEXT NOT NULL,
        data_file TEXT NOT NULL,
        application TEXT,
        environment TEXT,
        credential_role TEXT,
        base_url TEXT,
        status TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        generated_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        file_sha256 TEXT,
        components TEXT,
        notes TEXT,
        warnings TEXT,
        cred_username TEXT,
        job_id TEXT,
        manual_steps TEXT,
        last_result TEXT,
        last_run_id TEXT,
        last_run_at TEXT,
        archived INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS ix_fws_tc ON framework_scripts(test_case_id);
    CREATE TABLE IF NOT EXISTS framework_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        script_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        reason TEXT NOT NULL,
        kind TEXT NOT NULL,
        created_at TEXT NOT NULL,
        test_snapshot TEXT,
        data_snapshot TEXT,
        details TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_fwv_script ON framework_versions(script_id, version);
    CREATE TABLE IF NOT EXISTS framework_jobs (
        id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        finished_at TEXT,
        request TEXT NOT NULL,
        summary TEXT
    );
    CREATE TABLE IF NOT EXISTS framework_job_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL,
        position INTEGER NOT NULL,
        user_story_id TEXT,
        user_story_title TEXT,
        test_case_id TEXT NOT NULL,
        test_case_title TEXT,
        action TEXT NOT NULL,
        status TEXT NOT NULL,
        script_id TEXT,
        error_code TEXT,
        error TEXT,
        details TEXT,
        log TEXT,
        started_at TEXT,
        finished_at TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_fwji_job ON framework_job_items(job_id, position);
    ALTER TABLE healing_events ADD COLUMN component TEXT;
    CREATE TABLE app_credentials_new (
        id TEXT PRIMARY KEY,
        application TEXT NOT NULL,
        role TEXT NOT NULL,
        environment TEXT NOT NULL DEFAULT '',
        username TEXT NOT NULL,
        login_url TEXT,
        updated_at TEXT NOT NULL,
        UNIQUE (application, role, environment)
    );
    INSERT INTO app_credentials_new (id, application, role, environment, username, login_url, updated_at)
        SELECT id, application, role, '', username, login_url, updated_at FROM app_credentials;
    DROP TABLE app_credentials;
    ALTER TABLE app_credentials_new RENAME TO app_credentials;
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

    def execute(self, sql: str, params: Iterable[Any] | dict[str, Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params if isinstance(params, dict) else tuple(params))

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
