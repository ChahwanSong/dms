"""Schema migrations tests — TDD RED/GREEN."""
from dms.db import Database
from dms.migrations import migrate, ALL_TABLES


def _table_names(db):
    rows = db.query("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {r["name"] for r in rows}


def test_migrate_creates_all_tables(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    names = _table_names(db)
    for table in ALL_TABLES:
        assert table in names, table
    assert "schema_migrations" in names


def test_migrate_is_idempotent(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    migrate(db)  # 두 번 돌려도 에러 없음
    assert len(ALL_TABLES) == 20


def test_migrate_adds_columns_to_existing_data_jobs(db):
    # 구형 data_jobs를 흉내: 컬럼을 빼고 재생성
    db.execute("DROP TABLE data_jobs")
    db.execute("""CREATE TABLE data_jobs (job_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
        operation TEXT NOT NULL, tool TEXT, storage_name TEXT, source_storage TEXT,
        destination_storage TEXT, source TEXT, destination TEXT, target TEXT,
        options TEXT NOT NULL, priority TEXT NOT NULL, state TEXT NOT NULL, reason_code TEXT,
        preview_fingerprint TEXT, preview_expires_at TEXT, volcano_job_ref TEXT,
        artifact_uri TEXT, result_summary TEXT, created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)""")
    from dms.migrations import migrate, _column_exists
    migrate(db)
    assert _column_exists(db, "data_jobs", "worker_pool")
    assert _column_exists(db, "data_jobs", "precondition")


def test_migrate_adds_stepper_columns_to_existing_data_jobs(db):
    # 구형 data_jobs를 흉내: stepper 컬럼을 빼고 재생성
    db.execute("DROP TABLE data_jobs")
    db.execute("""CREATE TABLE data_jobs (job_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
        operation TEXT NOT NULL, tool TEXT, storage_name TEXT, source_storage TEXT,
        destination_storage TEXT, source TEXT, destination TEXT, target TEXT,
        options TEXT NOT NULL, priority TEXT NOT NULL, state TEXT NOT NULL, reason_code TEXT,
        preview_fingerprint TEXT, preview_expires_at TEXT, volcano_job_ref TEXT,
        artifact_uri TEXT, result_summary TEXT, worker_pool TEXT, precondition TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    from dms.migrations import migrate, _column_exists
    migrate(db)
    assert _column_exists(db, "data_jobs", "confirmed_fingerprint")
    assert _column_exists(db, "data_jobs", "phase_refs")
