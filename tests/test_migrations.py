"""Schema migrations tests — TDD RED/GREEN."""
from dms.db import Database
from dms.migrations import migrate, ALL_TABLES
from dms.repositories import Repositories


def _table_names(db):
    rows = db.query("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {r["name"] for r in rows}


def _declared_type(db, table, column):
    rows = db.query(f"PRAGMA table_info({table})")
    return next(r["type"] for r in rows if r["name"] == column)


def test_count_columns_are_declared_bigint(tmp_path):
    # PostgreSQL의 INTEGER는 int4(최대 2147483647)다 -- 슬라이스 15의 runner가 실
    # 바이트를 채우기 시작한 뒤로 2GiB를 넘는 첫 sync에서 set_artifact UPDATE가
    # 22003(integer out of range)로 터진다. 그 예외는 stepper의 _finalize보다
    # 앞에서 나므로 잡이 Executing에 박힌 채 매 틱 재클레임되고 vcjob도 회수되지
    # 않는다. SQLite는 INTEGER가 동적 64비트라 이 증상이 절대 재현되지 않으므로,
    # 실제로 배포되는 방언을 지키는 유일한 수단으로 **선언형 자체**를 고정한다.
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    assert _declared_type(db, "data_jobs", "files_count") == "BIGINT"
    assert _declared_type(db, "data_jobs", "bytes_count") == "BIGINT"


class _FakeDb:
    """_widen_count_columns의 방언 분기만 보기 위한 최소 대역. 실 PostgreSQL을
    붙이지 않고도 "SQLite엔 ALTER를 안 친다 / int4일 때만 친다"를 고정한다 --
    이 저장소엔 PG 테스트 하니스가 없고, SQLite로는 좁은 컬럼을 만들 수도
    ALTER COLUMN TYPE을 쓸 수도 없어 실 경로를 재현할 방법이 없다."""

    def __init__(self, dialect, data_type):
        self.dialect = dialect
        self._data_type = data_type
        self.executed = []

    def query(self, sql, params=None):
        return [{"data_type": self._data_type}]

    def execute(self, sql, params=None):
        self.executed.append(sql)


def test_widen_count_columns_skips_sqlite():
    # SQLite는 INTEGER가 동적 64비트라 넓힐 게 없고, ALTER COLUMN TYPE 자체를
    # 지원하지 않는다 -- 여기서 SQL이 나가면 매 마이그레이션이 터진다.
    from dms.migrations import _widen_count_columns
    fake = _FakeDb("sqlite", "integer")
    _widen_count_columns(fake)
    assert fake.executed == []


def test_widen_count_columns_alters_int4_on_postgres():
    # 이미 배포된 d24 DB는 두 컬럼이 int4다 -- _ensure_columns는 "없는 컬럼 추가"만
    # 하므로 이 단계가 없으면 그 DB는 영영 int4에 머문다.
    from dms.migrations import _widen_count_columns
    fake = _FakeDb("postgresql", "integer")
    _widen_count_columns(fake)
    assert fake.executed == [
        "ALTER TABLE data_jobs ALTER COLUMN files_count TYPE BIGINT",
        "ALTER TABLE data_jobs ALTER COLUMN bytes_count TYPE BIGINT",
    ]


def test_widen_count_columns_is_idempotent_when_already_bigint():
    # 멱등성: 두 번째 마이그레이션은 아무것도 치지 않아야 한다. PostgreSQL은 같은
    # 타입으로의 ALTER TYPE도 받아 주지만 그때마다 ACCESS EXCLUSIVE 락을 잡으므로,
    # 컨트롤러가 뜰 때마다 도는 이 경로에선 현재 타입을 보고 건너뛴다.
    from dms.migrations import _widen_count_columns
    fake = _FakeDb("postgresql", "bigint")
    _widen_count_columns(fake)
    assert fake.executed == []


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
    # ALTER로 보강되는 경로도 CREATE TABLE 경로와 같은 선언형이어야 한다 --
    # 두 경로가 다른 타입으로 수렴하면 구형 DB만 int4 천장에 걸린다.
    assert _declared_type(db, "data_jobs", "files_count") == "BIGINT"
    assert _declared_type(db, "data_jobs", "bytes_count") == "BIGINT"


def test_migrate_adds_progress_to_existing_releases(db):
    # 구형 releases를 흉내: I6의 progress 컬럼을 빼고 재생성. 이 컬럼이 없으면
    # DaemonSet 회수 시계가 정체 대신 지속시간을 재던 옛 동작으로 되돌아간다 --
    # 그것도 조용히(SELECT *가 키를 안 주면 head["progress"]가 KeyError로 터진다).
    from dms.migrations import _column_exists, migrate
    db.execute("DROP TABLE releases")
    db.execute("""CREATE TABLE releases (id INTEGER PRIMARY KEY AUTOINCREMENT,
        component TEXT NOT NULL, image TEXT NOT NULL, tag TEXT NOT NULL,
        digest TEXT, state TEXT NOT NULL, reason_code TEXT, seq INTEGER,
        actor TEXT NOT NULL, applied_at TEXT NOT NULL)""")
    db.execute("""INSERT INTO releases (component, image, tag, state, seq, actor,
        applied_at) VALUES ('dms-agent', 'i', 't', 'Applying', 1, 'ops',
        '2026-01-01T00:00:00Z')""")
    migrate(db)
    assert _column_exists(db, "releases", "progress")

    # 보강된 행의 progress는 NULL이다 -- 회수 판정이 그것을 0으로 읽어야 한다.
    repos = Repositories(db)
    row = repos.releases.active()[0]
    assert row["progress"] is None
    repos.releases.note_progress(row["id"], progress=2, now="2030-01-01T00:00:00Z")
    after = repos.releases.get(row["id"])
    assert (after["progress"], after["applied_at"]) == (2, "2030-01-01T00:00:00Z")


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
