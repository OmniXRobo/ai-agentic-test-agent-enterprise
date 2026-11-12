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
    # 10: Application Memory per application (versioned), Requirement Memory and Unified QA Knowledge
    """
    ALTER TABLE discoveries ADD COLUMN application TEXT;
    ALTER TABLE discoveries ADD COLUMN version INTEGER;
    ALTER TABLE discoveries ADD COLUMN knowledge_source TEXT;
    ALTER TABLE discoveries ADD COLUMN state TEXT;
    CREATE INDEX IF NOT EXISTS ix_disc_app ON discoveries(application, created_at);
    CREATE TABLE IF NOT EXISTS requirement_memory (
        id TEXT PRIMARY KEY,
        project TEXT NOT NULL,
        scope_key TEXT NOT NULL,
        created_at TEXT NOT NULL,
        scope TEXT NOT NULL,
        memory TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_reqmem_scope ON requirement_memory(project, scope_key);
    CREATE TABLE IF NOT EXISTS qa_knowledge (
        id TEXT PRIMARY KEY,
        application TEXT NOT NULL,
        run_id TEXT NOT NULL,
        discovery_id TEXT,
        requirement_memory_id TEXT,
        created_at TEXT NOT NULL,
        knowledge TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_qak_app ON qa_knowledge(application, created_at);
    """,
    # 11: testing type of each TFS test suite, suite-aware test case generations, acceptance-criterion
    #     traceability (application-level: TFS has no per-criterion links) and versioned test data rows
    """
    CREATE TABLE IF NOT EXISTS suite_types (
        project TEXT NOT NULL,
        plan_id INTEGER NOT NULL,
        suite_id INTEGER NOT NULL,
        testing_type TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (project, suite_id)
    );
    CREATE TABLE IF NOT EXISTS case_generations (
        run_id TEXT PRIMARY KEY,
        project TEXT NOT NULL,
        plan_id INTEGER,
        suite_id INTEGER,
        testing_type TEXT NOT NULL,
        story_ids TEXT NOT NULL,
        created_at TEXT NOT NULL,
        result TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ac_trace (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project TEXT NOT NULL,
        user_story_id TEXT NOT NULL,
        ac_id TEXT NOT NULL,
        ac_text TEXT NOT NULL,
        ac_hash TEXT NOT NULL,
        condition_ids TEXT,
        plan_id INTEGER,
        suite_id INTEGER,
        testing_type TEXT NOT NULL,
        tfs_test_case_id INTEGER NOT NULL,
        test_case_title TEXT,
        run_id TEXT,
        status TEXT NOT NULL DEFAULT 'current',
        review_reason TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_ac_trace_story ON ac_trace(project, user_story_id);
    CREATE INDEX IF NOT EXISTS ix_ac_trace_tc ON ac_trace(tfs_test_case_id);
    ALTER TABLE test_data ADD COLUMN dataset_key TEXT;
    ALTER TABLE test_data ADD COLUMN row_key TEXT;
    ALTER TABLE test_data ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE test_data ADD COLUMN scenario TEXT;
    ALTER TABLE test_data ADD COLUMN intended_valid INTEGER;
    ALTER TABLE test_data ADD COLUMN violated_rule TEXT;
    ALTER TABLE test_data ADD COLUMN expected_outcome TEXT;
    ALTER TABLE test_data ADD COLUMN ac_ids TEXT;
    ALTER TABLE test_data ADD COLUMN suite_id INTEGER;
    ALTER TABLE test_data ADD COLUMN testing_type TEXT;
    ALTER TABLE test_data ADD COLUMN iteration TEXT;
    ALTER TABLE test_data ADD COLUMN meta TEXT;
    ALTER TABLE test_data ADD COLUMN user_edited INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE test_data ADD COLUMN review_reason TEXT;
    CREATE INDEX IF NOT EXISTS ix_test_data_story ON test_data(user_story_id);
    CREATE TABLE IF NOT EXISTS test_data_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        test_data_id INTEGER NOT NULL,
        row_key TEXT,
        version INTEGER NOT NULL,
        change TEXT NOT NULL,
        snapshot TEXT NOT NULL,
        at TEXT NOT NULL
    );
    """,
    # 12 (23.3.22): performance scenarios and environments; execution state kept apart from the verdict; run queue
    """
    CREATE TABLE IF NOT EXISTS perf_environments (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        data TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS perf_scenarios (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        plan_id TEXT,
        environment_id TEXT,
        archived INTEGER NOT NULL DEFAULT 0,
        data TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_perf_scenarios_plan ON perf_scenarios(plan_id);
    ALTER TABLE perf_runs ADD COLUMN state TEXT;
    ALTER TABLE perf_runs ADD COLUMN verdict TEXT;
    ALTER TABLE perf_runs ADD COLUMN partial INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE perf_runs ADD COLUMN scenario_id TEXT;
    ALTER TABLE perf_runs ADD COLUMN environment_id TEXT;
    ALTER TABLE perf_runs ADD COLUMN trigger TEXT NOT NULL DEFAULT 'manual';
    ALTER TABLE perf_runs ADD COLUMN queued_at TEXT;
    ALTER TABLE perf_runs ADD COLUMN overrides TEXT;
    ALTER TABLE perf_runs ADD COLUMN config TEXT;
    ALTER TABLE perf_runs ADD COLUMN checks TEXT;
    ALTER TABLE perf_runs ADD COLUMN generator TEXT;
    ALTER TABLE perf_runs ADD COLUMN pid INTEGER;
    ALTER TABLE perf_runs ADD COLUMN state_reason TEXT;
    CREATE INDEX IF NOT EXISTS ix_perf_runs_state ON perf_runs(state);
    CREATE INDEX IF NOT EXISTS ix_perf_runs_scenario ON perf_runs(scenario_id);
    """,
    # 13 (23.3.23): saved versions of performance scripts (JMX), for the history, diff and restore
    """
    CREATE TABLE IF NOT EXISTS perf_plan_versions (
        plan_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        script TEXT NOT NULL,
        note TEXT,
        sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (plan_id, version)
    );
    """,
    # 14 (23.3.25): test data sets for performance scripts (secret cells are kept in the Credential Manager)
    """
    CREATE TABLE IF NOT EXISTS perf_datasets (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        columns TEXT NOT NULL,
        rows TEXT NOT NULL,
        secret_chunks INTEGER NOT NULL DEFAULT 0,
        version INTEGER NOT NULL DEFAULT 1,
        source TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    # 15 (23.3.28): the application build under test, recorded on each performance run (history filter, reports)
    """
    ALTER TABLE perf_runs ADD COLUMN build TEXT;
    CREATE INDEX IF NOT EXISTS ix_perf_runs_build ON perf_runs(build);
    CREATE INDEX IF NOT EXISTS ix_perf_runs_started ON perf_runs(started_at);
    """,
    # 16 (23.3.29): performance schedules. A job is one due time of one schedule; (schedule_id, job_key) is unique,
    # so a due time can never start two runs (two ticks, a restart, or two copies of the application)
    """
    CREATE TABLE IF NOT EXISTS perf_schedules (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        scenario_id TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        spec TEXT NOT NULL,
        next_run_at TEXT,
        last_run_id TEXT,
        last_job_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS perf_schedule_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        schedule_id TEXT NOT NULL,
        job_key TEXT NOT NULL,
        due_at TEXT,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        run_id TEXT,
        reason TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (schedule_id, job_key)
    );
    CREATE INDEX IF NOT EXISTS ix_perf_jobs_schedule ON perf_schedule_jobs(schedule_id, created_at);
    CREATE INDEX IF NOT EXISTS ix_perf_jobs_run ON perf_schedule_jobs(run_id);
    ALTER TABLE perf_runs ADD COLUMN schedule_id TEXT;
    CREATE INDEX IF NOT EXISTS ix_perf_runs_schedule ON perf_runs(schedule_id);
    """,
    # 17 (final patch 23.4.0): Application Data Comparison Agent. New tables only; nothing existing changes, so the
    # previous version keeps working on this database after a rollback (it ignores tables it does not know).
    """
    CREATE TABLE IF NOT EXISTS cmp_projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        owner TEXT NOT NULL,
        members TEXT NOT NULL DEFAULT '[]',
        settings TEXT NOT NULL DEFAULT '{}',
        archived INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS cmp_applications (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES cmp_projects(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        app_type TEXT NOT NULL,
        base_url TEXT,
        config TEXT NOT NULL DEFAULT '{}',
        environment TEXT,
        auth_method TEXT NOT NULL DEFAULT 'none',
        credential_role TEXT,
        description TEXT,
        connection_status TEXT,
        connection_detail TEXT,
        last_connected_at TEXT,
        profile_version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_cmp_apps_project ON cmp_applications(project_id);
    CREATE TABLE IF NOT EXISTS cmp_screens (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES cmp_projects(id) ON DELETE CASCADE,
        application_id TEXT NOT NULL REFERENCES cmp_applications(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        location TEXT,
        discovery TEXT NOT NULL DEFAULT '{}',
        fingerprint TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_cmp_screens_app ON cmp_screens(application_id, created_at);
    CREATE TABLE IF NOT EXISTS cmp_datasets (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES cmp_projects(id) ON DELETE CASCADE,
        application_id TEXT NOT NULL REFERENCES cmp_applications(id) ON DELETE CASCADE,
        screen_id TEXT,
        kind TEXT NOT NULL,
        method TEXT NOT NULL,
        status TEXT NOT NULL,
        filters TEXT NOT NULL DEFAULT '{}',
        stats TEXT NOT NULL DEFAULT '{}',
        aggregates TEXT NOT NULL DEFAULT '{}',
        checkpoint TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_cmp_datasets_app ON cmp_datasets(application_id, created_at);
    CREATE TABLE IF NOT EXISTS cmp_records (
        dataset_id TEXT NOT NULL REFERENCES cmp_datasets(id) ON DELETE CASCADE,
        seq INTEGER NOT NULL,
        record_key TEXT,
        page INTEGER,
        fields TEXT NOT NULL,
        PRIMARY KEY (dataset_id, seq)
    );
    CREATE INDEX IF NOT EXISTS ix_cmp_records_key ON cmp_records(dataset_id, record_key);
    CREATE TABLE IF NOT EXISTS cmp_mappings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL REFERENCES cmp_projects(id) ON DELETE CASCADE,
        version INTEGER NOT NULL,
        source_app TEXT NOT NULL,
        target_app TEXT NOT NULL,
        mappings TEXT NOT NULL,
        key_fields TEXT NOT NULL DEFAULT '{}',
        filter_map TEXT NOT NULL DEFAULT '[]',
        note TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (project_id, source_app, target_app, version)
    );
    CREATE TABLE IF NOT EXISTS cmp_evidence (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES cmp_projects(id) ON DELETE CASCADE,
        application_id TEXT,
        screen TEXT,
        kind TEXT NOT NULL,
        file TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        width INTEGER,
        height INTEGER,
        source TEXT NOT NULL,
        sequence INTEGER,
        ocr TEXT,
        notes TEXT,
        original_name TEXT,
        captured_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_cmp_evidence_project ON cmp_evidence(project_id, captured_at);
    CREATE TABLE IF NOT EXISTS cmp_aggregate_defs (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES cmp_projects(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        definition TEXT NOT NULL,
        approved INTEGER NOT NULL DEFAULT 0,
        approved_by TEXT,
        version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS cmp_executions (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES cmp_projects(id) ON DELETE CASCADE,
        mode TEXT NOT NULL,
        status TEXT NOT NULL,
        result TEXT,
        config TEXT NOT NULL DEFAULT '{}',
        summary TEXT NOT NULL DEFAULT '{}',
        detail TEXT,
        created_by TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_cmp_exec_project ON cmp_executions(project_id, started_at);
    CREATE TABLE IF NOT EXISTS cmp_discrepancies (
        id TEXT NOT NULL,
        execution_id TEXT NOT NULL REFERENCES cmp_executions(id) ON DELETE CASCADE,
        project_id TEXT NOT NULL,
        category TEXT NOT NULL,
        record_key TEXT,
        source_field TEXT,
        target_field TEXT,
        data TEXT NOT NULL,
        PRIMARY KEY (execution_id, id)
    );
    CREATE INDEX IF NOT EXISTS ix_cmp_disc_cat ON cmp_discrepancies(project_id, category);
    CREATE TABLE IF NOT EXISTS cmp_defects (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES cmp_projects(id) ON DELETE CASCADE,
        execution_id TEXT,
        discrepancy_id TEXT,
        status TEXT NOT NULL,
        title TEXT NOT NULL,
        severity INTEGER NOT NULL DEFAULT 3,
        priority INTEGER NOT NULL DEFAULT 3,
        data TEXT NOT NULL,
        investigation TEXT,
        tfs_id INTEGER,
        tfs_sync TEXT,
        comments TEXT NOT NULL DEFAULT '[]',
        history TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_cmp_defects_project ON cmp_defects(project_id, status);
    CREATE TABLE IF NOT EXISTS cmp_navigation (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        at TEXT NOT NULL,
        step TEXT NOT NULL,
        details TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_cmp_nav_session ON cmp_navigation(session_id, id);
    CREATE TABLE IF NOT EXISTS cmp_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        project_id TEXT,
        actor TEXT,
        action TEXT NOT NULL,
        target TEXT,
        details TEXT,
        prev_hash TEXT,
        hash TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_cmp_audit_project ON cmp_audit(project_id, id);
    CREATE TRIGGER IF NOT EXISTS cmp_audit_no_update BEFORE UPDATE ON cmp_audit
        BEGIN SELECT RAISE(ABORT, 'audit records cannot be changed'); END;
    CREATE TRIGGER IF NOT EXISTS cmp_audit_no_delete BEFORE DELETE ON cmp_audit
        BEGIN SELECT RAISE(ABORT, 'audit records cannot be deleted'); END;
    """,
    # 18 (final patch 23.4.0): the sign-in method of an application credential (form, windows/NTLM, basic, manual SSO)
    """
    ALTER TABLE app_credentials ADD COLUMN auth_method TEXT NOT NULL DEFAULT 'form';
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
