"""Schema migrations tests — TDD RED/GREEN."""
import pytest

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
        # (sql, params) 도 남긴다 -- executed 만으로는 어드바이저리 락 "키"가 무검증이라
        # unlock 키를 MIGRATE_LOCK_KEY+1 로 바꾸는 뮤테이션이 초록으로 통과했다.
        # 키가 어긋나면 상호배제 자체가 깨지므로 파라미터까지 고정해야 한다.
        self.calls = []
        self.fail_on = None      # 이 부분문자열을 담은 SQL 에서 예외를 던진다

    def query(self, sql, params=None):
        return [{"data_type": self._data_type}]

    def execute(self, sql, params=None):
        # 기록 후 예외 -- "시도는 했고 실패했다"를 호출자가 구분할 수 있어야 한다.
        self.executed.append(sql)
        self.calls.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("connection lost")


def test_migrate_pg_wraps_schema_in_advisory_lock(monkeypatch):
    # initContainer 도입으로 api/controller 가 동시에 migrate 를 돌린다 --
    # _ensure_columns 의 "확인 후 ALTER"가 경합하면 뒤쪽이 42701 로 죽는다(설계 §2.2).
    # 실 PG 하니스가 없으므로 "락 SQL 이 스키마 적용을 감싸는 순서" 자체를 고정한다.
    from dms import migrations
    fake = _FakeDb("postgresql", "bigint")
    monkeypatch.setattr(migrations, "_apply_migrations",
                        lambda db: fake.executed.append("SCHEMA"))
    migrations.migrate(fake)
    assert fake.executed == ["SELECT pg_advisory_lock(:k)", "SCHEMA",
                             "SELECT pg_advisory_unlock(:k)"]


def test_migrate_pg_releases_lock_on_exception(monkeypatch):
    # 세션 락은 커넥션이 살아 있는 한 남는다 -- 예외 경로에서 해제를 빼먹으면 같은
    # 커넥션을 재사용하는 다음 migrate 가 영원히 대기한다(설계 §5).
    from dms import migrations
    fake = _FakeDb("postgresql", "bigint")

    def boom(db):
        raise RuntimeError("column already exists")
    monkeypatch.setattr(migrations, "_apply_migrations", boom)
    with pytest.raises(RuntimeError):
        migrations.migrate(fake)
    assert fake.executed == ["SELECT pg_advisory_lock(:k)",
                             "SELECT pg_advisory_unlock(:k)"]


def test_migrate_lock_and_unlock_use_the_same_key(monkeypatch):
    # 락 키는 이 태스크의 핵심 제약이다: 롤링 배포 중 구/신 이미지가 동시에 migrate 를
    # 도는데 둘이 다른 키를 잡으면 서로를 배제하지 못해 락이 있으나 마나 해진다.
    # 순서만 보는 테스트는 키를 놓친다(unlock 키를 +1 로 바꿔도 초록이었다) --
    # lock·unlock 이 같은 MIGRATE_LOCK_KEY 를 쓰는지 파라미터로 못박는다.
    from dms import migrations
    fake = _FakeDb("postgresql", "bigint")
    monkeypatch.setattr(migrations, "_apply_migrations", lambda db: None)
    migrations.migrate(fake)
    assert fake.calls == [
        ("SELECT pg_advisory_lock(:k)", {"k": migrations.MIGRATE_LOCK_KEY}),
        ("SELECT pg_advisory_unlock(:k)", {"k": migrations.MIGRATE_LOCK_KEY}),
    ]
    # psycopg 는 2^63 이상을 numeric OID 로 보내고 numeric->bigint 암시적 캐스트가
    # 없어 pg_advisory_lock(numeric) does not exist 로 죽는다 -- 상한을 고정한다.
    assert 0 < migrations.MIGRATE_LOCK_KEY < 2 ** 63


def test_migrate_does_not_unlock_when_acquire_fails(monkeypatch):
    # 획득 실패는 그대로 올라가야 하고(스키마가 불확실한 채 앱이 뜨는 것보다 파드 실패가
    # 낫다, 설계 §4), 잡지도 않은 락을 해제해선 안 된다. acquire 를 try 안으로 옮기는
    # 리팩터가 들어오면 여기서 잡힌다.
    from dms import migrations
    fake = _FakeDb("postgresql", "bigint")
    fake.fail_on = "pg_advisory_lock"
    monkeypatch.setattr(migrations, "_apply_migrations",
                        lambda db: fake.executed.append("SCHEMA"))
    with pytest.raises(RuntimeError):
        migrations.migrate(fake)
    assert fake.executed == ["SELECT pg_advisory_lock(:k)"]


def test_migrate_sqlite_issues_no_advisory_lock(monkeypatch):
    # SQLite 에 pg_advisory_lock 을 치면 즉사한다 -- 방언 분기 자체를 고정한다.
    from dms import migrations
    fake = _FakeDb("sqlite", "integer")
    monkeypatch.setattr(migrations, "_apply_migrations",
                        lambda db: fake.executed.append("SCHEMA"))
    migrations.migrate(fake)
    assert fake.executed == ["SCHEMA"]


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


def test_submit_wait_column_and_covering_index_on_fresh_db(tmp_path):
    # CREATE 경로(신규 DB). BIGINT 선언은 files_count 와 같은 규약 --
    # 두 경로(CREATE/ALTER)가 같은 선언형으로 수렴해야 한다.
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    assert _declared_type(db, "data_jobs", "submit_wait_seconds") == "BIGINT"
    rows = db.query("SELECT name FROM sqlite_master WHERE type = 'index'")
    assert "idx_data_jobs_created" in {r["name"] for r in rows}


def test_migrate_backfills_submit_wait_from_transitions(db):
    # ALTER 경로(구형 DB) + one-shot 백필(설계 §2.3). PodGroup 은 잡 종료와 함께
    # 삭제되므로(설계 §1-1) 이력에서 소급할 수 있는 것은 이 DMS 내부 대기뿐이다.
    db.execute("DROP TABLE data_jobs")
    db.execute("""CREATE TABLE data_jobs (job_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL, operation TEXT NOT NULL, tool TEXT,
        storage_name TEXT, source_storage TEXT, destination_storage TEXT,
        source TEXT, destination TEXT, target TEXT, options TEXT NOT NULL,
        priority TEXT NOT NULL, state TEXT NOT NULL, reason_code TEXT,
        preview_fingerprint TEXT, preview_expires_at TEXT, volcano_job_ref TEXT,
        artifact_uri TEXT, result_summary TEXT, files_count BIGINT,
        bytes_count BIGINT, worker_pool TEXT, precondition TEXT,
        confirmed_fingerprint TEXT, phase_refs TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    for job_id, state in (("j-done", "Succeeded"), ("j-pending", "Pending")):
        db.execute("""INSERT INTO data_jobs (job_id, request_id, operation,
            options, priority, state, created_at, updated_at)
            VALUES (:j, 'r1', 'scan', '{}', 'mid', :st,
                    '2026-08-01T00:00:00Z', '2026-08-01T01:00:00Z')""",
                   {"j": job_id, "st": state})
    for from_s, to_s, at in ((None, "Pending", "2026-08-01T00:00:00Z"),
                             ("Pending", "Preflight", "2026-08-01T00:01:30Z"),
                             ("Preflight", "Succeeded", "2026-08-01T01:00:00Z")):
        db.execute("""INSERT INTO state_transitions (entity_kind, entity_id,
            from_state, to_state, actor, at)
            VALUES ('data_job', 'j-done', :f, :t, 'stepper', :at)""",
                   {"f": from_s, "t": to_s, "at": at})
    from dms.migrations import _column_exists, migrate
    migrate(db)
    assert _column_exists(db, "data_jobs", "submit_wait_seconds")
    waits = {r["job_id"]: r["submit_wait_seconds"]
             for r in db.query("SELECT job_id, submit_wait_seconds FROM data_jobs")}
    assert waits["j-done"] == 90         # 첫 비-Pending 전이(00:01:30) - created_at
    assert waits["j-pending"] is None    # 아직 Pending -- 백필 대상이 아니다(집계 제외)


def test_backfill_only_fills_null_rows(db):
    # migrate 는 파드 기동마다(initContainer) 재실행된다 -- 이미 채워진 값(런타임
    # write-once 포함)을 백필이 재계산해 덮으면 write-once 계약이 마이그레이션
    # 경로로 우회된다. NULL 만 채우는 멱등성이 계약이다.
    db.execute("""INSERT INTO data_jobs (job_id, request_id, operation, options,
        priority, state, submit_wait_seconds, created_at, updated_at)
        VALUES ('j1', 'r1', 'scan', '{}', 'mid', 'Succeeded', 7,
                '2026-08-01T00:00:00Z', '2026-08-01T01:00:00Z')""")
    db.execute("""INSERT INTO state_transitions (entity_kind, entity_id,
        from_state, to_state, actor, at)
        VALUES ('data_job', 'j1', 'Pending', 'Preflight', 'stepper',
                '2026-08-01T00:05:00Z')""")   # 재계산되면 300 이 된다
    migrate(db)
    row = db.query_one("SELECT submit_wait_seconds FROM data_jobs WHERE job_id = 'j1'")
    assert row["submit_wait_seconds"] == 7


def test_backfill_treats_zero_as_a_recorded_value(db):
    # 0 은 "기록됨"이지 "없음"이 아니다 -- 타임스탬프가 1초 해상도라 빠른 픽업은
    # 정당하게 0 을 남긴다. 백필 필터가 IS NULL 이 아니라 falsy 검사(= 0, NOT
    # submit_wait_seconds, COALESCE(...,0)=0)로 쓰이면 0 인 행이 매 재-migrate 마다
    # 재계산되어 write-once 가 깨진다. 위 테스트는 값 7 을 써서 이 버그를 못 잡으므로
    # 0 을 따로 고정한다.
    db.execute("""INSERT INTO data_jobs (job_id, request_id, operation, options,
        priority, state, submit_wait_seconds, created_at, updated_at)
        VALUES ('j0', 'r1', 'scan', '{}', 'mid', 'Succeeded', 0,
                '2026-08-01T00:00:00Z', '2026-08-01T01:00:00Z')""")
    db.execute("""INSERT INTO state_transitions (entity_kind, entity_id,
        from_state, to_state, actor, at)
        VALUES ('data_job', 'j0', 'Pending', 'Preflight', 'stepper',
                '2026-08-01T00:05:00Z')""")   # 재계산되면 300 이 된다
    migrate(db)
    row = db.query_one("SELECT submit_wait_seconds FROM data_jobs WHERE job_id = 'j0'")
    assert row["submit_wait_seconds"] == 0


def test_migrate_adds_artifact_base_columns_to_existing_control_state(db):
    # 구형 control_state 흉내(슬라이스 18): CREATE 경로와 _ensure_columns ALTER
    # 경로가 같은 스키마로 수렴해야 한다 -- 한쪽만 넣으면 기배포 DB 에서만 컬럼이
    # 없다(슬라이스 14 실 500 교훈, migrations.py 의 _ensure_columns 주석).
    db.execute("DROP TABLE control_state")
    db.execute("""CREATE TABLE control_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        maintenance INTEGER NOT NULL DEFAULT 0,
        drain INTEGER NOT NULL DEFAULT 0,
        reason TEXT, build_node_name TEXT, changed_by TEXT, changed_at TEXT)""")
    from dms.migrations import _column_exists, migrate
    migrate(db)
    for column in ("artifact_base_uri", "artifact_base_check_uri",
                   "artifact_base_check_ok", "artifact_base_check_reason",
                   "artifact_base_check_at"):
        assert _column_exists(db, "control_state", column), column
    # 싱글톤 시드가 살아 있고 새 컬럼은 NULL(미설정 = env 사용, 하위호환)이다
    row = db.query_one("SELECT * FROM control_state WHERE id = 1")
    assert row["artifact_base_uri"] is None
