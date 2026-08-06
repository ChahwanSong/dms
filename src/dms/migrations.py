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
            updated_at TEXT NOT NULL,
            batch_id TEXT)""",
        "CREATE INDEX IF NOT EXISTS idx_requests_resource ON requests (resource_key, commit_order)",
        "CREATE INDEX IF NOT EXISTS idx_requests_requester ON requests (requester_id, commit_order)",
        "CREATE INDEX IF NOT EXISTS idx_requests_state ON requests (state, commit_order)",
        """CREATE TABLE IF NOT EXISTS batches (
            batch_id TEXT PRIMARY KEY,
            operation TEXT NOT NULL,
            requester_id TEXT NOT NULL,
            actor TEXT NOT NULL,
            status TEXT NOT NULL,
            max_concurrency INTEGER NOT NULL,
            options TEXT NOT NULL,
            note TEXT,
            item_count INTEGER NOT NULL DEFAULT 0,
            succeeded_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_batches_status ON batches (status, created_at)",
        """CREATE TABLE IF NOT EXISTS batch_items (
            batch_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL,
            request_id TEXT,
            reason_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (batch_id, seq))""",
        "CREATE INDEX IF NOT EXISTS idx_batch_items_status ON batch_items (batch_id, status)",
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
            -- seq는 개념적으로 NOT NULL UNIQUE(단조 증가 생성 순서, requests.commit_order와
            -- 같은 용도)여야 하지만 여기 걸지 않는다: 신규 DB는 이 CREATE TABLE로,
            -- 기존(구형) DB는 _ensure_columns의 ALTER TABLE ADD COLUMN으로 채워지는데
            -- SQLite의 ALTER TABLE ADD COLUMN은 UNIQUE/PRIMARY KEY 제약을 아예 허용하지
            -- 않고, NOT NULL도 NULL이 아닌 DEFAULT 없이는 못 붙인다 -- 두 경로가 같은
            -- 스키마로 수렴해야 하므로 제약은 애플리케이션(create()의 MAX(seq)+1)에 둔다.
            seq INTEGER,
            repo_url TEXT NOT NULL,
            git_ref TEXT NOT NULL,
            commit_sha TEXT,
            images TEXT,
            node_name TEXT NOT NULL,
            state TEXT NOT NULL,
            reason_code TEXT,
            log_uri TEXT,
            log_text TEXT,
            created_at TEXT NOT NULL,
            finished_at TEXT)""",
        f"""CREATE TABLE IF NOT EXISTS releases (
            id {auto_pk},
            component TEXT NOT NULL,
            image TEXT NOT NULL,
            tag TEXT NOT NULL,
            digest TEXT,
            state TEXT NOT NULL,
            reason_code TEXT,
            -- seq: 배치 안 적용 순서(전역 단조 증가). builds.seq와 같은 이유로
            -- 제약은 여기 걸지 않고 create_batch()의 MAX(seq)+1이 지킨다.
            seq INTEGER,
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
            build_node_name TEXT,
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
        # purge(prune_events)와 시간순 조회는 request_id로 좁혀지지 않으므로
        # (request_id, id) 인덱스가 커버하지 못한다 -- at 단독 인덱스가 필요하다.
        "CREATE INDEX IF NOT EXISTS idx_events_at ON events (at)",
    ]
    for stmt in stmts:
        db.execute(stmt)
    _ensure_columns(db)
    # requests.batch_id는 CREATE TABLE(신규 DB) 또는 _ensure_columns의 ALTER(구형 DB)로
    # 보강된 뒤에만 존재가 보장되므로, 이 인덱스는 그 이후에 생성한다.
    db.execute("CREATE INDEX IF NOT EXISTS idx_requests_batch ON requests (batch_id)")
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
    # 도구별 기본 정책 시드 (스펙 §5 "phase별 타임아웃은 정책 행에서"). 멱등하며
    # 기존 행은 절대 덮어쓰지 않는다 — 운영자가 포탈에서 고친 값을 마이그레이션이
    # 되돌리면 안 된다. 행이 없으면 planner가 missing_policy로 전부 거부한다.
    now = utc_now_iso()
    for tool, max_nodes, preview_timeout, execution_timeout in (
        # 모든 도구가 같은 기본값을 쓴다: preview 12h(43200) / execution 24h(86400).
        # 초안의 1h/30m 은 activeDeadlineSeconds 가 실제로 걸리지 않던 시절의 값이라,
        # 데드라인이 진짜로 발동하게 된 뒤로는 대규모 작업을 중간에 죽인다(drm 은 부분
        # 삭제로 남는다). preview 는 dry-run 이지만 대상 트리가 크면 오래 걸리고,
        # preflight/exec_preflight 파드의 데드라인으로도 쓰인다.
        # 운영자가 포탈 /admin/policies 에서 언제든 조정한다.
        ("scan", 4, 43200, 86400),
        ("dsync", 8, 43200, 86400),
        ("nsync", 8, 43200, 86400),
        ("rm", 4, 43200, 86400),
    ):
        db.execute(
            """INSERT INTO policies (tool, max_nodes, procs_per_node, queue,
                   default_priority, max_priority, preview_timeout_seconds,
                   execution_timeout_seconds, enabled, updated_at, updated_by)
               SELECT :t, :mn, 8, 'dms-data', 'mid', 'high', :pt, :et, 1, :now,
                      'migration-seed'
               WHERE NOT EXISTS (SELECT 1 FROM policies WHERE tool = :t)""",
            {"t": tool, "mn": max_nodes, "pt": preview_timeout,
             "et": execution_timeout, "now": now})


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
        ("requests", "batch_id", "TEXT"),
        ("control_state", "build_node_name", "TEXT"),
        ("builds", "log_text", "TEXT"),
        ("builds", "seq", "INTEGER"),
        ("releases", "reason_code", "TEXT"),
        ("releases", "seq", "INTEGER"),
    ):
        if not _column_exists(db, table, column):
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
