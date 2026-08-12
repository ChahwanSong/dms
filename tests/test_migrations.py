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
    # 슬라이스 27: 死物 runs 제거(20 -> 19) -- 이 저장소 최초의 파괴적 마이그레이션.
    assert len(ALL_TABLES) == 19


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


def test_migrate_adds_auth_method_to_existing_requests(db):
    # 구형 requests 를 흉내: auth_method 컬럼을 빼고 재생성. 한쪽(CREATE)만 넣으면
    # 기배포 DB 는 이 컬럼이 없어 planner 의 req["auth_method"] 조회가 라이브에서만
    # 터진다(슬라이스 14 의 실 500 교훈).
    db.execute("DROP TABLE requests")
    db.execute("""CREATE TABLE requests (request_id TEXT PRIMARY KEY,
        commit_order INTEGER NOT NULL UNIQUE, operation TEXT NOT NULL,
        requester_id TEXT NOT NULL, actor TEXT NOT NULL, resource_key TEXT NOT NULL,
        priority TEXT NOT NULL DEFAULT 'mid', payload TEXT NOT NULL, state TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, batch_id TEXT)""")
    from dms.migrations import migrate, _column_exists
    migrate(db)
    assert _column_exists(db, "requests", "auth_method")


def test_sched_wait_columns_and_covering_index_on_fresh_db(tmp_path):
    # CREATE 경로(신규 DB). 슬라이스 20: submit_wait_seconds 선례의 이중 경로
    # 규약 그대로 -- 선언형(BIGINT/TEXT)이 CREATE 와 ALTER 두 경로에서 같아야
    # 한다(다르면 기배포 DB 만 다른 타입으로 굳는다). 인덱스는 집계 2쿼리
    # (repositories/metrics.py)의 커버링이다.
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    assert _declared_type(db, "data_jobs", "exec_submitted_at") == "TEXT"
    assert _declared_type(db, "data_jobs", "sched_wait_seconds") == "BIGINT"
    rows = db.query("SELECT name FROM sqlite_master WHERE type = 'index'")
    assert "idx_data_jobs_created_sched" in {r["name"] for r in rows}


def test_migrate_adds_sched_wait_columns_without_backfill(db):
    # ALTER 경로(기배포 DB) + **백필 부재 자체가 계약이다**(설계 §2.5): sched_wait
    # 의 원천(Volcano 가 잡을 Running 으로 올린 시각)은 state_transitions 어디에도
    # 없다 -- 전이 행이 온전히 남아 있어도 과거 잡은 NULL 이어야 한다. 나중에
    # 누군가 _backfill_submit_wait 를 본떠 전이 행에서 값을 "지어내는" 백필을
    # 추가하면 이 테스트가 잡는다(NULL = excluded 로 화면에 표면화되는 것이 정직).
    db.execute("DROP TABLE data_jobs")
    db.execute("""CREATE TABLE data_jobs (job_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL, operation TEXT NOT NULL, tool TEXT,
        storage_name TEXT, source_storage TEXT, destination_storage TEXT,
        source TEXT, destination TEXT, target TEXT, options TEXT NOT NULL,
        priority TEXT NOT NULL, state TEXT NOT NULL, reason_code TEXT,
        preview_fingerprint TEXT, preview_expires_at TEXT, volcano_job_ref TEXT,
        artifact_uri TEXT, result_summary TEXT, files_count BIGINT,
        bytes_count BIGINT, worker_pool TEXT, precondition TEXT,
        confirmed_fingerprint TEXT, phase_refs TEXT, submit_wait_seconds BIGINT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    db.execute("""INSERT INTO data_jobs (job_id, request_id, operation, options,
        priority, state, created_at, updated_at)
        VALUES ('j-old', 'r1', 'scan', '{}', 'mid', 'Succeeded',
                '2026-08-01T00:00:00Z', '2026-08-01T01:00:00Z')""")
    # 과거 잡의 전이 이력(제출 시점의 Preflight→Running 포함)이 전부 남아 있어도
    # -- Running "전이 시각"은 DMS 상태 기계의 시각이지 Volcano 스케줄 시각이
    # 아니다. 여기서 소급하면 없는 사실을 지어내는 것이다.
    for from_s, to_s, at in ((None, "Pending", "2026-08-01T00:00:00Z"),
                             ("Pending", "Preflight", "2026-08-01T00:01:00Z"),
                             ("Preflight", "Running", "2026-08-01T00:02:00Z"),
                             ("Running", "Succeeded", "2026-08-01T01:00:00Z")):
        db.execute("""INSERT INTO state_transitions (entity_kind, entity_id,
            from_state, to_state, actor, at)
            VALUES ('data_job', 'j-old', :f, :t, 'stepper', :at)""",
                   {"f": from_s, "t": to_s, "at": at})
    from dms.migrations import _column_exists, migrate
    migrate(db)
    assert _column_exists(db, "data_jobs", "exec_submitted_at")
    assert _column_exists(db, "data_jobs", "sched_wait_seconds")
    row = db.query_one("""SELECT exec_submitted_at, sched_wait_seconds
                          FROM data_jobs WHERE job_id = 'j-old'""")
    assert row["exec_submitted_at"] is None    # 앵커도 소급하지 않는다
    assert row["sched_wait_seconds"] is None   # 과거 잡은 전부 NULL(excluded 로 표면화)


def test_migrate_adds_diag_logs_to_existing_data_jobs(db):
    # 슬라이스 25: 기배포 DB 는 CREATE TABLE IF NOT EXISTS 를 다시 안 탄다 --
    # _ensure_columns 쪽을 빼먹으면 라이브에서만 컬럼이 없어, 첫 실패 종단의
    # 박제 UPDATE 가 500 을 낸다(슬라이스 14 files_count 실 사고의 재현 시나리오).
    # 구형 흉내는 슬라이스 20 까지의 전 컬럼을 갖춘 모양이다.
    db.execute("DROP TABLE data_jobs")
    db.execute("""CREATE TABLE data_jobs (job_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL, operation TEXT NOT NULL, tool TEXT,
        storage_name TEXT, source_storage TEXT, destination_storage TEXT,
        source TEXT, destination TEXT, target TEXT, options TEXT NOT NULL,
        priority TEXT NOT NULL, state TEXT NOT NULL, reason_code TEXT,
        preview_fingerprint TEXT, preview_expires_at TEXT, volcano_job_ref TEXT,
        artifact_uri TEXT, result_summary TEXT, files_count BIGINT,
        bytes_count BIGINT, worker_pool TEXT, precondition TEXT,
        confirmed_fingerprint TEXT, phase_refs TEXT, submit_wait_seconds BIGINT,
        exec_submitted_at TEXT, sched_wait_seconds BIGINT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    from dms.migrations import _column_exists, migrate
    migrate(db)
    assert _column_exists(db, "data_jobs", "diag_logs")
    # 선언형도 CREATE 경로와 같아야 한다 -- 다르면 기배포 DB 만 다른 타입으로 굳는다.
    assert _declared_type(db, "data_jobs", "diag_logs") == "TEXT"


def test_diag_logs_is_declared_in_the_create_table_block():
    # 신규 DB 에선 CREATE 누락도 _ensure_columns 가 흡수해 버려 PRAGMA 로는 두
    # 경로를 구분할 수 없다 -- "양쪽 선언" 규약(files_count 이후 전 컬럼의 관례)의
    # CREATE 쪽은 소스 자체로 고정하는 수밖에 없다. 이 테스트가 없으면 CREATE 쪽
    # 누락은 어떤 테스트로도 안 잡힌다.
    import inspect
    import re
    from dms import migrations
    src = inspect.getsource(migrations._apply_migrations)
    block = re.search(r'CREATE TABLE IF NOT EXISTS data_jobs.*?"""', src, re.S)
    assert block is not None
    # 단순 substring 검사로는 부족하다: 블록 안 주석이 archive_diag_logs 를
    # 언급하므로 선언 줄만 지워도 통과해 버린다(검사하는 척하는 단언). SQL 주석을
    # 걷어낸 뒤 **선언 자체**를 본다 -- 선언형(TEXT)까지 함께 고정해 ALTER 경로와의
    # 패리티도 소스에서 못 박는다.
    sql = re.sub(r"--[^\n]*", "", block.group(0))
    assert re.search(r"^\s*diag_logs\s+TEXT\s*,\s*$", sql, re.M)


def test_all_tables_is_nineteen_tables_not_columns():
    # len(ALL_TABLES) 는 **테이블** 수 계약이다(슬라이스 25 가 20 으로 박제,
    # 슬라이스 27 의 runs 제거로 19). 늘든 줄든 명시 결정 없이는 여기가 먼저
    # 빨개진다 -- "새 테이블 0·삭제는 결정된 것만"의 그물.
    assert len(ALL_TABLES) == 19


def test_migrate_drops_runs_from_existing_db(db):
    # 슬라이스 27: 기배포 DB 경로. 빈 DB 는 CREATE 목록에서 빠진 것으로 끝나지만
    # 기존 DB 는 이미 만들어진 runs 를 DROP 이 지워야 한다 -- 두 경로는 다른
    # 코드다(슬라이스 25 diag_logs 의 양쪽 규약과 같은 교훈, 방향만 삭제).
    # IF NOT EXISTS 인 이유: RED 단계(현행 코드)에선 db 픽스처의 migrate 가 이미
    # runs 를 만들어 두었고, GREEN 단계에선 없다 -- 양 단계 모두에서 "구형 DB 에
    # runs 가 있는" 전제를 성립시킨 뒤 재-migrate 로 삭제를 판정한다.
    db.execute("""CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, request_id TEXT NOT NULL,
        state TEXT NOT NULL, detail TEXT, started_at TEXT NOT NULL,
        finished_at TEXT)""")
    migrate(db)
    assert "runs" not in _table_names(db)


def test_fresh_migrate_never_creates_runs(tmp_path):
    # 신규 DB 경로: CREATE 목록에서 빠졌으니 애초에 안 생긴다(DROP IF EXISTS 는
    # 여기서 no-op -- 멱등). ALL_TABLES 부재도 함께 고정한다 --
    # test_migrate_creates_all_tables 는 부분집합 검사라 "목록에서 빼먹음"은
    # 잡아도 "목록에 남아 있음"은 못 잡는다.
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    assert "runs" not in _table_names(db)
    assert "runs" not in ALL_TABLES


def test_drop_runs_executes_after_the_create_loop():
    # CREATE 문 삭제 후엔 DROP 의 위치가 기능 테스트로는 구분되지 않는다(어디
    # 있든 지금은 runs 가 없다). 순서 계약의 실질은 미래다: 부활 CREATE 가
    # 실수(머지·롤백)로 되돌아와도 "생성 후 삭제"는 '없음'으로 수렴하고, 역순이면
    # 매 migrate 가 부활시킨다 -- 제거 결정이 조용히 뒤집힌다.
    # CREATE 리터럴 위치 비교로는 부족하다: stmts 리스트 **정의**와 실행 **루프**
    # 사이에 DROP 을 끼우면 텍스트로는 CREATE 들보다 뒤인데 실행은 앞이다.
    # 그래서 실행 루프(for stmt in stmts) 대비 위치를 계약으로 건다(소스 계약
    # 테스트는 슬라이스 25 test_diag_logs_is_declared... 와 같은 계열의 타협).
    import inspect

    from dms import migrations
    src = inspect.getsource(migrations._apply_migrations)
    assert "DROP TABLE IF EXISTS runs" in src
    assert src.index("for stmt in stmts") < src.index("DROP TABLE IF EXISTS runs")


def test_migrate_creates_exactly_the_expected_tables(tmp_path):
    # 슬라이스 30(BACKLOG §2.4, 슬라이스 27 발견): ALL_TABLES(19)는 스펙 §4 도메인
    # 모델 한정이라 batches·batch_items·schema_migrations 3개가 목록 밖이다(실 DB
    # 22 테이블). len==19 그물과 부분집합 검사(test_migrate_creates_all_tables)는
    # "목록 안"만 지켜, 그 셋이 실수로 지워져도 못 잡는다. 실제 CREATE 산출물
    # 전체를 기대 집합과 **양방향 등식**으로 대조한다: 왼쪽만 크면 미결정 새
    # 테이블, 오른쪽만 크면 실수 삭제 -- 어느 쪽도 명시 결정 없이는 통과 못 한다
    # (runs 제거가 그랬듯, 파괴적 변경은 결정 + 테스트 갱신이 함께 가야 한다).
    # sqlite_sequence 는 AUTOINCREMENT 가 만드는 sqlite 내부 테이블이라 제외한다
    # (실측: 신규 migrate 직후 sqlite_master 에 이미 있다).
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    actual = {n for n in _table_names(db) if not n.startswith("sqlite_")}
    assert actual == set(ALL_TABLES) | {"batches", "batch_items",
                                        "schema_migrations"}


def test_migrate_creates_exactly_the_expected_indexes(tmp_path):
    # 슬라이스 30(BACKLOG §2.5 슬라이스 1~4 부채): idx_requests_batch 가 CREATE
    # 되는데 어떤 테스트도 단언하지 않았다 -- 인덱스명 단언은 idx_data_jobs_created
    # 계열 2건뿐(전 테스트 실측). 인덱스는 지워져도 기능 테스트가 전부 초록인 채
    # (풀스캔) 성능만 조용히 침몰하는 부류라 존재 단언이 유일한 그물이고, 개별
    # 이름 추가는 두더지잡기라 테이블 전수와 같은 등식으로 16개 전부를 고정한다.
    # sqlite_autoindex_* 는 PK/UNIQUE 의 내부 산물이라 제외한다.
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    rows = db.query("SELECT name FROM sqlite_master WHERE type = 'index'")
    actual = {r["name"] for r in rows if not r["name"].startswith("sqlite_")}
    assert actual == {
        "idx_requests_resource", "idx_requests_requester", "idx_requests_state",
        "idx_requests_batch", "idx_batches_status", "idx_batch_items_status",
        "idx_transitions_entity", "idx_data_jobs_state", "idx_data_jobs_created",
        "idx_data_jobs_created_sched", "idx_agent_reports_node",
        "idx_agent_reports_at", "idx_releases_component", "idx_audit_target",
        "idx_events_request", "idx_events_at",
    }


def _ensure_pairs():
    # _ensure_columns 의 (table, column, type) 목록은 함수 안 리터럴이라 import 로
    # 닿을 수 없다 -- 소스 정규식으로 뽑는다(모듈 상수 승격은 소스 무접촉 원칙
    # 밖: 소스 계약 추출은 슬라이스 25/27 계열의 타협이다).
    import inspect
    import re

    from dms import migrations
    src = inspect.getsource(migrations._ensure_columns)
    return re.findall(r'\("(\w+)", "(\w+)", "(\w+)"\)', src)


# 초기 배포(v1) 스키마의 컬럼 -- **역사적 사실이라 불변이다**(이 파일의 구형 재생성
# 픽스처들과 현재 소스에서 실측·검산). 이후 태어난 모든 컬럼은 CREATE(신규 DB)와
# _ensure_columns(기배포 DB) 양쪽에 있어야 한다는 이중 경로 규약(슬라이스 14 실
# 500 교훈)의 기준선. 여기 컬럼을 추가하는 것은 "그 컬럼은 ensure 없이도 모든
# 배포에 존재한다"는 주장이므로, 새 컬럼을 넣으려고 이 집합을 늘리면 안 된다.
_V1_COLUMNS = {
    "requests": {"request_id", "commit_order", "operation", "requester_id",
                 "actor", "resource_key", "priority", "payload", "state",
                 "created_at", "updated_at"},
    "data_jobs": {"job_id", "request_id", "operation", "tool", "storage_name",
                  "source_storage", "destination_storage", "source",
                  "destination", "target", "options", "priority", "state",
                  "reason_code", "preview_fingerprint", "preview_expires_at",
                  "volcano_job_ref", "artifact_uri", "result_summary",
                  "created_at", "updated_at"},
    "control_state": {"id", "maintenance", "drain", "reason", "changed_by",
                      "changed_at"},
    "builds": {"build_id", "repo_url", "git_ref", "commit_sha", "images",
               "node_name", "state", "reason_code", "log_uri", "created_at",
               "finished_at"},
    "releases": {"id", "component", "image", "tag", "digest", "state", "actor",
                 "applied_at"},
}


def test_every_post_v1_column_rides_both_migration_paths(tmp_path):
    # 일반 그물(BACKLOG §2.5 "ALTER 경로 일반 회귀 커버리지 갭" -- 슬라이스 14 가
    # 파킹했고 그 파킹이 실제 프로덕션 500 을 냈다): 컬럼별 구형 재생성 테스트는
    # 이미 태어난 컬럼만 지킨다 -- CREATE 에만 넣고 _ensure_columns 를 잊는 미래
    # 컬럼은 신규 DB(테스트 전부)에선 멀쩡하고 기배포 DB(라이브)에서만 없다.
    # 등식: 현재 전체 컬럼 = v1 ∪ ensure 목록. 정상 진화(양쪽 추가)는 v1 무수정
    # 으로 통과하고, 규율 위반과 무결정 삭제만 빨개진다.
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    pairs = _ensure_pairs()
    assert len(pairs) == 23      # 추출 자기 검증 -- 0 매치면 등식이 공허해진다
    for table, v1 in _V1_COLUMNS.items():
        cols = {r["name"] for r in db.query(f"PRAGMA table_info({table})")}
        ensured = {c for t, c, _ in pairs if t == table}
        assert cols == v1 | ensured, table


def test_ensure_columns_types_match_create_declarations(tmp_path):
    # 선언형 패리티의 일반화: files_count 계열(BIGINT/int4 실 사고)과 diag_logs 만
    # 컬럼별 형 단언이 있었다. 신규 DB 의 선언형은 CREATE 산출물이므로 ensure
    # 목록의 형과 전 컬럼 대조하면 두 경로가 같은 형으로 수렴함이 고정된다 --
    # 다르면 기배포 DB 만 다른 형으로 굳는다(int4 천장 사고의 일반형).
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    pairs = _ensure_pairs()
    # 추출 자기 검증을 이 테스트에도 둔다 -- 위 등식 테스트가 지워지거나 홀로
    # 실행될 때 정규식이 0 매치면 아래 루프가 공허하게 통과하기 때문이다.
    assert len(pairs) == 23
    for table, column, coltype in pairs:
        assert _declared_type(db, table, column) == coltype, (table, column)
