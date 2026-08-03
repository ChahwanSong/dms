"""전체 스키마. CREATE TABLE IF NOT EXISTS 선언 스크립트 — 스펙 §4 도메인 모델의 20개 테이블."""
from .db import Database, utc_now_iso

ALL_TABLES = (
    "requests", "plans", "runs", "results", "state_transitions",
    "data_jobs", "storages", "policies",
    "identity_denylist", "identity_probe_targets",
    "agent_reports", "agent_nodes",
    "accounts", "user_scan_paths",
    "builds", "releases",
    "component_leases", "control_state", "audit_log", "events",
)


def migrate(db: Database) -> None:
    auto_pk = ("INTEGER PRIMARY KEY AUTOINCREMENT" if db.dialect == "sqlite"
               else "BIGSERIAL PRIMARY KEY")
    stmts = [
        "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)",
        """CREATE TABLE IF NOT EXISTS requests (
            request_id TEXT PRIMARY KEY,
            commit_order INTEGER NOT NULL UNIQUE,
            operation TEXT NOT NULL,
            requester_id TEXT NOT NULL,
            actor TEXT NOT NULL,
            resource_key TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'mid',
            payload TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_requests_resource ON requests (resource_key, commit_order)",
        "CREATE INDEX IF NOT EXISTS idx_requests_requester ON requests (requester_id, commit_order)",
        "CREATE INDEX IF NOT EXISTS idx_requests_state ON requests (state, commit_order)",
        """CREATE TABLE IF NOT EXISTS plans (
            plan_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            job_id TEXT,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            state TEXT NOT NULL,
            detail TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT)""",
        """CREATE TABLE IF NOT EXISTS results (
            request_id TEXT PRIMARY KEY,
            terminal_state TEXT NOT NULL,
            reason_code TEXT,
            message TEXT,
            summary TEXT,
            completed_at TEXT NOT NULL)""",
        f"""CREATE TABLE IF NOT EXISTS state_transitions (
            id {auto_pk},
            entity_kind TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            from_state TEXT,
            to_state TEXT NOT NULL,
            reason_code TEXT,
            actor TEXT NOT NULL,
            at TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_transitions_entity ON state_transitions (entity_kind, entity_id, id)",
        """CREATE TABLE IF NOT EXISTS data_jobs (
            job_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            tool TEXT,
            storage_name TEXT,
            source_storage TEXT,
            destination_storage TEXT,
            source TEXT,
            destination TEXT,
            target TEXT,
            options TEXT NOT NULL,
            priority TEXT NOT NULL,
            state TEXT NOT NULL,
            reason_code TEXT,
            preview_fingerprint TEXT,
            preview_expires_at TEXT,
            volcano_job_ref TEXT,
            artifact_uri TEXT,
            result_summary TEXT,
            worker_pool TEXT,
            precondition TEXT,
            confirmed_fingerprint TEXT,
            phase_refs TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_data_jobs_state ON data_jobs (state, updated_at)",
        """CREATE TABLE IF NOT EXISTS storages (
            storage_name TEXT PRIMARY KEY,
            mount_path TEXT NOT NULL,
            managed_root TEXT NOT NULL,
            backend_type TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'Unknown',
            status_detail TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS policies (
            tool TEXT PRIMARY KEY,
            max_nodes INTEGER NOT NULL,
            procs_per_node INTEGER NOT NULL,
            queue TEXT NOT NULL DEFAULT 'dms-data',
            default_priority TEXT NOT NULL DEFAULT 'mid',
            max_priority TEXT NOT NULL DEFAULT 'high',
            preview_timeout_seconds INTEGER,
            execution_timeout_seconds INTEGER NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS identity_denylist (
            subject_type TEXT NOT NULL,
            subject TEXT NOT NULL,
            reason TEXT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (subject_type, subject))""",
        """CREATE TABLE IF NOT EXISTS identity_probe_targets (
            username TEXT PRIMARY KEY,
            last_requested_at TEXT NOT NULL)""",
        f"""CREATE TABLE IF NOT EXISTS agent_reports (
            id {auto_pk},
            node_name TEXT NOT NULL,
            report TEXT NOT NULL,
            reported_at TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_agent_reports_node ON agent_reports (node_name, reported_at)",
        "CREATE INDEX IF NOT EXISTS idx_agent_reports_at ON agent_reports (reported_at)",
        """CREATE TABLE IF NOT EXISTS agent_nodes (
            node_name TEXT PRIMARY KEY,
            report TEXT NOT NULL,
            reported_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS accounts (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            email TEXT,
            disabled INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL)""",
        f"""CREATE TABLE IF NOT EXISTS user_scan_paths (
            id {auto_pk},
            username TEXT NOT NULL,
            storage_name TEXT NOT NULL,
            path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (username, storage_name, path))""",
        """CREATE TABLE IF NOT EXISTS builds (
            build_id TEXT PRIMARY KEY,
            repo_url TEXT NOT NULL,
            git_ref TEXT NOT NULL,
            commit_sha TEXT,
            images TEXT,
            node_name TEXT NOT NULL,
            state TEXT NOT NULL,
            reason_code TEXT,
            log_uri TEXT,
            created_at TEXT NOT NULL,
            finished_at TEXT)""",
        f"""CREATE TABLE IF NOT EXISTS releases (
            id {auto_pk},
            component TEXT NOT NULL,
            image TEXT NOT NULL,
            tag TEXT NOT NULL,
            digest TEXT,
            state TEXT NOT NULL,
            actor TEXT NOT NULL,
            applied_at TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_releases_component ON releases (component, id)",
        """CREATE TABLE IF NOT EXISTS component_leases (
            component TEXT PRIMARY KEY,
            holder TEXT NOT NULL,
            expires_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS control_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            maintenance INTEGER NOT NULL DEFAULT 0,
            drain INTEGER NOT NULL DEFAULT 0,
            reason TEXT,
            changed_by TEXT,
            changed_at TEXT)""",
        f"""CREATE TABLE IF NOT EXISTS audit_log (
            id {auto_pk},
            mutation_class TEXT NOT NULL,
            operation TEXT NOT NULL,
            target_key TEXT NOT NULL,
            actor TEXT NOT NULL,
            before_state TEXT,
            after_state TEXT,
            at TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_log (mutation_class, target_key, id)",
        f"""CREATE TABLE IF NOT EXISTS events (
            id {auto_pk},
            request_id TEXT,
            component TEXT NOT NULL,
            severity TEXT NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT,
            payload TEXT,
            at TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_events_request ON events (request_id, id)",
    ]
    for stmt in stmts:
        db.execute(stmt)
    _ensure_columns(db)
    db.execute(
        """INSERT INTO schema_migrations (version, applied_at)
           SELECT :v, :at WHERE NOT EXISTS
             (SELECT 1 FROM schema_migrations WHERE version = :v)""",
        {"v": "0001-initial", "at": utc_now_iso()},
    )
    # control_state 싱글톤 행 시드
    db.execute(
        """INSERT INTO control_state (id, maintenance, drain)
           SELECT 1, 0, 0 WHERE NOT EXISTS (SELECT 1 FROM control_state WHERE id = 1)""",
    )


def _column_exists(db, table, column):
    if db.dialect == "sqlite":
        rows = db.query(f"PRAGMA table_info({table})")
        return any(r["name"] == column for r in rows)
    rows = db.query(
        """SELECT 1 AS x FROM information_schema.columns
           WHERE table_name = :t AND column_name = :c""",
        {"t": table, "c": column})
    return bool(rows)


def _ensure_columns(db):
    # 이미 마이그레이트된(구형) DB엔 CREATE TABLE IF NOT EXISTS가 컬럼을 못 채운다 —
    # schema_migrations를 넘어 실제 컬럼 존재를 확인하고 없으면 ALTER로 보강한다.
    for table, column, coltype in (
        ("data_jobs", "worker_pool", "TEXT"),
        ("data_jobs", "precondition", "TEXT"),
        ("data_jobs", "confirmed_fingerprint", "TEXT"),
        ("data_jobs", "phase_refs", "TEXT"),
    ):
        if not _column_exists(db, table, column):
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
