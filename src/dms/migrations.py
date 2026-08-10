"""전체 스키마. CREATE TABLE IF NOT EXISTS 선언 스크립트 — 스펙 §4 도메인 모델의 20개 테이블."""
from .db import Database, iso_epoch, utc_now_iso

ALL_TABLES = (
    "requests", "plans", "runs", "results", "state_transitions",
    "data_jobs", "storages", "policies",
    "identity_denylist", "identity_probe_targets",
    "agent_reports", "agent_nodes",
    "accounts", "user_scan_paths",
    "builds", "releases",
    "component_leases", "control_state", "audit_log", "events",
)


# PostgreSQL 어드바이저리 락 키(임의 32비트 상수 "DMS\x10" = 1145918224). migrate()
# 전체를 직렬화하는 전역 락이라 값 자체에 의미는 없다 -- 이 저장소의 유일한 어드바이저리
# 락 사용처라 충돌도 없다. 바꾸면 구/신 이미지가 서로 다른 락을 잡아 경합이 부활한다.
#
# 반드시 2**63 미만을 유지할 것. pg_advisory_lock의 인자는 bigint인데, psycopg는
# 2**63 이상의 파이썬 int를 numeric OID로 보낸다. numeric -> bigint는 암시적 캐스트가
# 없어 "더 큰 값이니 더 안전하겠지"라며 64비트로 넓히는 순간 런타임에
# `function pg_advisory_lock(numeric) does not exist`로 죽는다 -- 그 시점이 하필
# initContainer라 모든 파드가 기동에 실패한다. 32비트로 충분하다(중복이 없으므로).
MIGRATE_LOCK_KEY = 0x444D5310


def migrate(db: Database) -> None:
    """스키마 적용 진입점(시그니처 불변 -- cli.py 와 테스트 conftest 가 그대로 쓴다).

    initContainer 도입(슬라이스 16 설계 §2.2)으로 api·controller 두 파드가 동시에
    이걸 돌린다. _ensure_columns 가 "존재 확인 후 ALTER"(비 IF NOT EXISTS)라 동시
    실행이면 뒤쪽이 42701(column already exists)로 죽는다 -- pg_advisory_lock 으로
    전 구간을 직렬화한다. psycopg 연결은 autocommit(db.py)이라 락이 트랜잭션 경계와
    무관하게 세션에 붙는다. SQLite 는 로컬 단일 파일(개발·테스트 전용)이라 no-op.
    락 획득 실패(연결 단절 등)는 그대로 예외로 올라가 initContainer 를 실패시킨다 --
    스키마가 불확실한 채 앱이 뜨는 것보다 낫다(설계 §4). one-shot Job
    (30-migrate-job.yaml)도 같은 경로를 지나므로 함께 안전해진다."""
    locked = db.dialect == "postgresql"
    if locked:
        db.execute("SELECT pg_advisory_lock(:k)", {"k": MIGRATE_LOCK_KEY})
    try:
        _apply_migrations(db)
    finally:
        if locked:
            # 예외 경로에서도 반드시 해제(설계 §5) -- 세션 락은 커넥션이 살아 있는 한
            # 남아, 같은 커넥션의 다음 migrate 를 영원히 기다리게 한다.
            db.execute("SELECT pg_advisory_unlock(:k)", {"k": MIGRATE_LOCK_KEY})


def _apply_migrations(db: Database) -> None:
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
            batch_id TEXT,
            -- 슬라이스 19: 요청을 만든 인증 방식(session/token). planner 가 특권 승격을
            -- session 요청에만 허용하는 심층 방어에 쓴다(설계 §2.2-2). 기존 행/기본은
            -- token(비특권) -- 승격은 명시적으로 session 일 때만.
            auth_method TEXT)""",
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
            -- files/bytes 파이프라인(슬라이스 14 설계 §2.3): set_artifact가
            -- result_summary의 "files"/"bytes"를 typed 컬럼으로 승격한다. 슬라이스 15의
            -- runner가 mpifileutils 출력을 파싱해 실제로 이 키를 채운다 -- files는
            -- "항목(items)" 의미로 통일했고(dsync/nsync Items, drm Removed N items,
            -- dscan total_entries), scan/rm은 설계상 bytes가 없어 NULL이다.
            -- BIGINT여야 한다: PostgreSQL의 INTEGER는 int4(최대 2147483647)라
            -- 2GiB를 넘는 첫 sync에서 set_artifact UPDATE가 22003(integer out of
            -- range)로 터진다 -- 그 예외는 stepper의 _finalize 앞에서 나므로 잡이
            -- Executing에 박힌 채 틱마다 재클레임되고 vcjob이 영영 GC되지 않는다.
            -- (SQLite는 INTEGER가 동적 64비트라 증상이 안 잡힌다. BIGINT도 SQLite에선
            --  INTEGER affinity로 같게 동작한다.)
            files_count BIGINT,
            bytes_count BIGINT,
            worker_pool TEXT,
            precondition TEXT,
            confirmed_fingerprint TEXT,
            phase_refs TEXT,
            -- 제출 대기(슬라이스 17 설계 §2.3/§2.4). **무엇을 재는가**:
            -- created_at -> 첫 비-Pending 전이(= set_job_state 가 실제로 불린 시각)
            -- 까지의 초. 그 사이에는 스테퍼 틱 간격이 통째로 들어간다 -- 즉 이 값은
            -- **DMS 내부 픽업 지연**이지 **Volcano 큐 대기가 아니다**. 진짜 큐 대기는
            -- 살아 있는 PodGroup 라이브 뷰에만 있고(잡이 끝나면 PodGroup 이 삭제된다),
            -- 그것을 이력으로 남기려면 후속 슬라이스가 필요하다(설계 §7).
            -- 컬럼명을 submit_wait_seconds 로 둔 이유가 이것이다: 화면 라벨
            -- "제출 대기"와 스키마가 같은 것을 말해야 한다(queue_wait_seconds 는
            -- 슬라이스 14 가 붙였던 바로 그 오해를 스키마에 다시 심는 이름이었다).
            -- set_job_state 가 그 엣지에서 한 번만 쓴다(write-once). NULL = 기록 전
            -- (아직 Pending/백필 불가분/시각 오염) -- 집계에서 제외하고 제외 건수를
            -- 표면화한다. BIGINT 는 files_count 와 같은 규약(두 경로 동일 선언형).
            submit_wait_seconds BIGINT,
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
            -- progress: 마지막으로 관찰된 DaemonSet updated_number_scheduled.
            -- RolloutWatcher는 틱마다 새로 생성되므로(controller.build_loops의
            -- 람다) "지난 틱에 몇 노드까지 갔나"를 인스턴스에 담아 둘 수 없다 --
            -- 회수 시계를 정체 기준으로 재려면 이 값이 행에 남아야 한다.
            progress INTEGER,
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
            -- 아티팩트 base(슬라이스 18 설계 §2.1). NULL = 미설정 -> env
            -- (DMS_ARTIFACT_BASE_URI) 사용 -- 기존 배포는 동작이 바뀌지 않는다
            -- (시드 불필요, 하위호환). ConfigMap 에 두지 않는 근거는
            -- 20-config.yaml 이 build_node_name 으로 이미 명문화한 그것:
            -- 운영자가 포탈에서 바꾸는 값을 ConfigMap 에 두면 재적용마다 되돌아간다.
            artifact_base_uri TEXT,
            -- 컨트롤러 관점 검증(설계 §2.4c): 컨트롤러는 read_summary 로 실제
            -- 읽기를 하는 유일한 프로세스라, 마운트가 없으면 "SUCCEEDED 인데
            -- 요약이 없는" 조용한 실패가 난다(stepper 의 summary_unavailable) --
            -- 그 실패를 사전에 화면에 보이게 컨트롤러가 주기 기록을 남긴다.
            -- check_uri 를 함께 남기는 이유: 값 변경 직후 "옛 base 의 결과"를
            -- "새 base 실패"로 오독하지 않도록, 화면이 check_uri != effective 를
            -- "확인 대기 중"으로 구분한다(설계 §4: 모름과 실패를 뭉개지 않는다).
            artifact_base_check_uri TEXT,
            artifact_base_check_ok INTEGER,
            artifact_base_check_reason TEXT,
            artifact_base_check_at TEXT,
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
    _widen_count_columns(db)
    # requests.batch_id는 CREATE TABLE(신규 DB) 또는 _ensure_columns의 ALTER(구형 DB)로
    # 보강된 뒤에만 존재가 보장되므로, 이 인덱스는 그 이후에 생성한다.
    db.execute("CREATE INDEX IF NOT EXISTS idx_requests_batch ON requests (batch_id)")
    # submit_wait_seconds 는 CREATE(신규) 또는 _ensure_columns(구형)로 보강된 뒤에만
    # 존재하므로 이 인덱스도 그 이후다(idx_requests_batch 와 같은 이유).
    # (created_at, submit_wait_seconds) 커버링: 제출 대기 집계 2쿼리가 인덱스만 읽고,
    # 덤으로 기존 created_at BETWEEN 집계 7개(repositories/metrics.py)가 풀스캔에서
    # 레인지 스캔이 된다 -- 이 슬라이스는 읽기 비용의 순증이 아니라 순감이다(설계 §2.3).
    # 인덱스 이름은 선두 컬럼(created_at) 기준이라 컬럼 개명과 무관하게 그대로 둔다.
    db.execute("CREATE INDEX IF NOT EXISTS idx_data_jobs_created"
               " ON data_jobs (created_at, submit_wait_seconds)")
    _backfill_submit_wait(db)
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


def _widen_count_columns(db):
    # 이미 배포된 DB(d24)는 files_count/bytes_count가 int4로 만들어져 있다 --
    # _ensure_columns는 "없는 컬럼 추가"만 하지 타입을 바꾸지 않으므로 그 DB는
    # CREATE TABLE의 BIGINT 선언을 영영 못 받는다. PostgreSQL의 int4 천장은
    # 2147483647이라 2GiB를 넘는 첫 sync에서 set_artifact UPDATE가 22003으로
    # 터지고, 그 예외가 stepper의 _finalize보다 앞이라 잡이 Executing에 박힌 채
    # 매 틱 재클레임 -> step_error 이벤트 폭주 + vcjob 미회수로 이어진다.
    # 따라서 기존 DB도 여기서 한 번 넓혀 준다.
    if db.dialect != "postgresql":
        return  # SQLite는 INTEGER가 동적 64비트라 넓힐 게 없다(ALTER COLUMN TYPE 미지원이기도 하다).
    for column in ("files_count", "bytes_count"):
        # 멱등성: PostgreSQL은 같은 타입으로의 ALTER TYPE도 허용하지만 매번
        # ACCESS EXCLUSIVE 락을 잡는다 -- migrate는 배포마다 재실행되는 one-shot
        # Job(30-migrate-job.yaml)이라 매 배포가 락을 잡으면 안 되니
        # 현재 타입을 먼저 보고 int4일 때만 친다(그래서 두 번째 실행은 no-op).
        rows = db.query(
            """SELECT data_type FROM information_schema.columns
               WHERE table_name = 'data_jobs' AND column_name = :c""",
            {"c": column})
        if rows and rows[0]["data_type"] == "integer":
            db.execute(f"ALTER TABLE data_jobs ALTER COLUMN {column} TYPE BIGINT")


def _ensure_columns(db):
    # 이미 마이그레이트된(구형) DB엔 CREATE TABLE IF NOT EXISTS가 컬럼을 못 채운다 —
    # schema_migrations를 넘어 실제 컬럼 존재를 확인하고 없으면 ALTER로 보강한다.
    for table, column, coltype in (
        ("data_jobs", "worker_pool", "TEXT"),
        ("data_jobs", "precondition", "TEXT"),
        ("data_jobs", "confirmed_fingerprint", "TEXT"),
        ("data_jobs", "phase_refs", "TEXT"),
        # BIGINT: CREATE TABLE 경로와 같은 선언형이어야 한다(위 int4 천장 주석 참고).
        # SQLite에선 INTEGER와 affinity가 같아 기존 DB에 아무 변화가 없다.
        ("data_jobs", "files_count", "BIGINT"),
        ("data_jobs", "bytes_count", "BIGINT"),
        # 슬라이스 17 제출 대기 -- 기배포 DB 는 CREATE 를 다시 안 탄다(슬라이스 14 의
        # 실 500 교훈: 양쪽에 넣지 않으면 라이브에서만 컬럼이 없다).
        ("data_jobs", "submit_wait_seconds", "BIGINT"),
        ("requests", "batch_id", "TEXT"),
        # 슬라이스 19: 기배포 DB 는 CREATE 를 다시 안 탄다 -- 양쪽에 넣지 않으면
        # planner 의 req["auth_method"] 가 라이브에서만 없다(슬라이스 14 교훈).
        ("requests", "auth_method", "TEXT"),
        ("control_state", "build_node_name", "TEXT"),
        # 슬라이스 18 아티팩트 base -- 기배포 DB 는 CREATE 를 다시 안 탄다(위
        # submit_wait_seconds 와 같은 이유: 양쪽에 넣지 않으면 라이브에서만 없다).
        ("control_state", "artifact_base_uri", "TEXT"),
        ("control_state", "artifact_base_check_uri", "TEXT"),
        ("control_state", "artifact_base_check_ok", "INTEGER"),
        ("control_state", "artifact_base_check_reason", "TEXT"),
        ("control_state", "artifact_base_check_at", "TEXT"),
        ("builds", "log_text", "TEXT"),
        ("builds", "seq", "INTEGER"),
        ("releases", "reason_code", "TEXT"),
        ("releases", "seq", "INTEGER"),
        ("releases", "progress", "INTEGER"),
    ):
        if not _column_exists(db, table, column):
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def _backfill_submit_wait(db):
    """submit_wait_seconds one-shot 백필(설계 §2.3). state_transitions 에서 잡별
    Pending -> 첫 전이 시각을 찾아 created_at 과의 차를 채운다. NULL 행만 채우므로
    멱등이다 -- migrate 는 파드 기동마다(initContainer) 재실행되고, 이미 채워진
    값을 재계산하면 write-once 계약이 마이그레이션 경로로 우회된다(0 도 채워진
    값이다 -- 필터가 IS NULL 이지 = 0 이 아닌 이유). 잔여 NULL 은 아직 Pending 인
    잡뿐이라(조인이 걸러 낸다) 재실행 비용은 무시할 수준이다.
    시각 산술은 SQL 로 이식 불가(julianday=SQLite, EXTRACT=PG 전용) -- 파이썬에서
    빼고 행별 UPDATE 를 친다. 마이그레이션 1회 경로라 허용한다(폴링 경로가 아니고,
    PG 어드바이저리 락 안이라 동시 기동과도 경합하지 않는다)."""
    rows = db.query(
        """SELECT d.job_id, d.created_at, MIN(t.at) AS picked_at
           FROM data_jobs d
           JOIN state_transitions t
             ON t.entity_kind = 'data_job' AND t.entity_id = d.job_id
                AND t.from_state = 'Pending'
           WHERE d.submit_wait_seconds IS NULL
           GROUP BY d.job_id, d.created_at""")
    for row in rows:
        try:
            wait = int(iso_epoch(row["picked_at"]) - iso_epoch(row["created_at"]))
        except (TypeError, ValueError):
            continue          # 시각이 깨진 행은 NULL 로 남긴다(집계 제외 -- 설계 §4)
        if wait < 0:
            continue          # 시계 역행/오염 -- 값을 지어내느니 NULL 이 정직하다
        db.execute(
            "UPDATE data_jobs SET submit_wait_seconds = :w WHERE job_id = :j",
            {"w": wait, "j": row["job_id"]})
