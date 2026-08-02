# DMS Phase 1 — 코어 백엔드 골격 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** DMS clean-slate 재구현의 1단계 — DB 계층(SQLite/PostgreSQL 호환), 전체 스키마, 도메인 모델과 검증 규칙, 저장소 계층, FastAPI 골격(인증·계정·스토리지 CRUD·잡 요청 제출/조회), CLI를 만든다.

**Architecture:** 스펙 `docs/superpowers/specs/2026-08-02-dms-clean-slate-design.md`를 따른다. 상태(DB)가 유일한 진실이고, 모든 SQL은 저장소 계층에 모으며 SQLite/PostgreSQL 양쪽에서 돈다. Phase 1은 요청을 받아 `Pending`으로 영속하는 데까지 — planner/job-stepper(Phase 3), 에이전트(Phase 2)는 이후 플랜.

**Tech Stack:** Python 3.11+, FastAPI, Starlette SessionMiddleware(서명 쿠키), sqlite3(stdlib)/psycopg 3, pytest.

## Global Constraints

- 스펙이 진실이다: `docs/superpowers/specs/2026-08-02-dms-clean-slate-design.md`. legacy(`legacy/`)와 다르면 스펙이 이긴다. legacy 코드 import/복사 금지 (읽기 전용 참고만).
- 모든 SQL은 `src/dms/repositories/` 안에만. SQLite/PostgreSQL 양쪽 호환 (named param `:name`, PG 전용 문법은 방언 분기).
- 전체 테스트는 서비스 없이 SQLite로 돈다: `pytest` 단독 실행 가능해야 한다.
- 설정은 env var `DMS_*`. 기동 시 검증하고, placeholder(`""`, `CHANGE_ME`, `REPLACE_WITH_*`)가 truthy로 게이트를 통과하는 구멍 금지.
- 모든 거부·실패에 기계가 읽는 사유 코드(snake_case 문자열). 조용한 실패 금지.
- 모든 상태 전이는 `state_transitions`에 기록. 모든 동기 변경(스토리지 등)은 `audit_log`에 before/after 기록.
- 타임스탬프는 UTC ISO-8601 TEXT(`2026-08-02T12:00:00Z` 형식), JSON 컬럼은 TEXT에 `json.dumps`.
- kubectl 서브프로세스 금지 (Phase 1에는 k8s 접근 자체가 없음).
- 커밋 메시지는 conventional commit 스타일 한국어 본문 허용, 각 태스크 완료 시 커밋.

## 전체 로드맵 (이 플랜은 Phase 1)

| Phase | 내용 | 플랜 |
|---|---|---|
| 1 | 코어 백엔드 골격 (이 문서) | 이 문서 |
| 2 | 노드 에이전트 + storage-reconciler + controller 루프 숙주 | Phase 1 완료 후 작성 |
| 3 | 잡 실행: planner, job-stepper, identity(LDAP), Volcano 어댑터, dms-job-runner, preview/confirm | Phase 2 완료 후 작성 |
| 4 | 이미지 빌드·배포 (registry, builds/releases, build-runner, 롤아웃) | Phase 3 완료 후 작성 |
| 5 | 포탈 SPA (사용자/관리자 인터페이스, 대시보드 2축) | Phase 3 완료 후 작성 가능 |
| 6 | 설치·부트스트랩 (이미지 3종 Dockerfile, 매니페스트, 설치 문서) | 마지막 |

## File Structure

```
pyproject.toml                     # 패키지 정의, 의존성, dms 엔트리포인트
src/dms/__init__.py
src/dms/config.py                  # Settings.from_env + 기동 검증
src/dms/db.py                      # Database: URL 접속, 방언 흡수, named param, utc_now_iso
src/dms/migrations.py              # 전체 스키마 (idempotent), migrate(db)
src/dms/domain.py                  # 상태 enum, 검증 규칙, 옵션 allowlist, fingerprint/resource_key
src/dms/repositories/__init__.py   # Repositories 집합 (조립 헬퍼)
src/dms/repositories/requests.py   # 요청 lifecycle + state_transitions + results
src/dms/repositories/storages.py   # 스토리지 CRUD + audit_log
src/dms/repositories/accounts.py   # 포탈 계정 (scrypt 해시)
src/dms/repositories/control.py    # policies, identity_denylist, control_state, component_leases, audit 조회
src/dms/api/__init__.py
src/dms/api/app.py                 # create_app 팩토리, 미들웨어, 라우터 마운트
src/dms/api/auth.py                # shared token / admin token / 세션 인증, require_role
src/dms/api/routes_auth.py         # signup/login/logout/me, 관리자 계정 생성
src/dms/api/routes_storages.py     # /api/admin/storages CRUD + audit 조회
src/dms/api/routes_requests.py     # /api/user/requests 제출·조회, /api/admin/requests
src/dms/cli.py                     # dms migrate / dms api
tests/conftest.py                  # db(tmp sqlite)/app/client 픽스처
tests/test_db.py
tests/test_migrations.py
tests/test_domain_states.py
tests/test_domain_paths.py
tests/test_domain_options.py
tests/test_config.py
tests/test_repo_requests.py
tests/test_repo_storages.py
tests/test_repo_accounts.py
tests/test_repo_control.py
tests/test_api_auth.py
tests/test_api_storages.py
tests/test_api_requests.py
tests/test_cli.py
```

---

### Task 1: 프로젝트 스캐폴딩

**Files:**
- Create: `pyproject.toml`
- Create: `src/dms/__init__.py`
- Create: `tests/conftest.py` (빈 파일로 시작)
- Test: `tests/test_smoke.py`

**Interfaces:**
- Consumes: 없음
- Produces: `dms` 패키지 (import 가능), `pip install -e ".[test]"`, `pytest` 실행 환경

- [ ] **Step 1: pyproject.toml 작성**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "dms"
version = "0.1.0"
description = "DMS - data management service (clean-slate)"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "itsdangerous>=2.1",
]

[project.optional-dependencies]
test = ["pytest>=8", "httpx>=0.27"]
postgres = ["psycopg[binary]>=3.1"]

[project.scripts]
dms = "dms.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: 패키지 뼈대와 smoke 테스트 작성**

`src/dms/__init__.py` 는 빈 파일. `tests/test_smoke.py`:

```python
def test_import():
    import dms  # noqa: F401
```

- [ ] **Step 3: 설치 후 테스트 실행**

Run: `pip install -e ".[test]" && pytest -q`
Expected: 1 passed

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml src/dms/__init__.py tests/
git commit -m "chore: 프로젝트 스캐폴딩 (패키지, pytest)"
```

---

### Task 2: DB 계층 (`db.py`)

**Files:**
- Create: `src/dms/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `Database.connect(url: str) -> Database` — `sqlite:///path`(및 `sqlite:///:memory:`), `postgresql://...` 지원
  - `db.dialect: str` — `"sqlite"` | `"postgresql"`
  - `db.execute(sql: str, params: dict | None = None) -> None` (autocommit)
  - `db.query(sql, params=None) -> list[dict]`, `db.query_one(sql, params=None) -> dict | None`
  - `db.transaction()` — 컨텍스트 매니저, 블록 안 `execute/query`는 한 트랜잭션
  - `utc_now_iso() -> str` — `2026-08-02T12:00:00Z` 형식
- SQL은 named param `:name`으로 쓴다. sqlite는 네이티브, postgres는 `%(name)s`로 변환.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_db.py
import re
from dms.db import Database, utc_now_iso


def test_sqlite_roundtrip(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    assert db.dialect == "sqlite"
    db.execute("CREATE TABLE t (a TEXT, b INTEGER)")
    db.execute("INSERT INTO t (a, b) VALUES (:a, :b)", {"a": "x", "b": 1})
    assert db.query("SELECT a, b FROM t WHERE a = :a", {"a": "x"}) == [{"a": "x", "b": 1}]
    assert db.query_one("SELECT b FROM t WHERE a = :a", {"a": "x"}) == {"b": 1}
    assert db.query_one("SELECT b FROM t WHERE a = :a", {"a": "none"}) is None


def test_transaction_rollback_on_error(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    db.execute("CREATE TABLE t (a TEXT PRIMARY KEY)")
    try:
        with db.transaction():
            db.execute("INSERT INTO t (a) VALUES (:a)", {"a": "x"})
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert db.query("SELECT a FROM t") == []


def test_utc_now_iso_format():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", utc_now_iso())
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dms.db'`

- [ ] **Step 3: 구현**

```python
# src/dms/db.py
"""단일 DB 접근 계층. SQL은 named param(:name)으로 쓰고 방언 차이는 여기서 흡수한다."""
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

_NAMED = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    def __init__(self, conn, dialect: str):
        self._conn = conn
        self.dialect = dialect
        self._lock = threading.Lock()
        self._in_txn = False

    @classmethod
    def connect(cls, url: str) -> "Database":
        if url.startswith("sqlite:///"):
            path = url[len("sqlite:///"):]
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.isolation_level = None  # 명시적 트랜잭션 제어
            conn.execute("PRAGMA foreign_keys = ON")
            return cls(conn, "sqlite")
        if url.startswith("postgresql://") or url.startswith("postgres://"):
            import psycopg
            from psycopg.rows import dict_row
            conn = psycopg.connect(url, row_factory=dict_row, autocommit=True)
            return cls(conn, "postgresql")
        raise ValueError(f"unsupported database url: {url}")

    def _adapt(self, sql: str) -> str:
        if self.dialect == "postgresql":
            return _NAMED.sub(r"%(\1)s", sql)
        return sql

    def execute(self, sql: str, params: dict | None = None) -> None:
        with self._lock:
            self._conn.execute(self._adapt(sql), params or {})

    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(self._adapt(sql), params or {})
            return [dict(r) for r in cur.fetchall()]

    def query_one(self, sql: str, params: dict | None = None) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def transaction(self):
        with self._lock:
            self._conn.execute("BEGIN")
        try:
            yield self
        except BaseException:
            with self._lock:
                self._conn.execute("ROLLBACK")
            raise
        else:
            with self._lock:
                self._conn.execute("COMMIT")


def dump_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def load_json(text):
    return json.loads(text) if text else None
```

(참고: psycopg autocommit 모드에서 `BEGIN`/`COMMIT`을 `execute`로 보내는 방식은 sqlite와 동일하게 동작한다. postgres 실환경 검증은 Phase 6 배포 시 수행 — Phase 1 테스트는 sqlite만으로 완결.)

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_db.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dms/db.py tests/test_db.py
git commit -m "feat: DB 접근 계층 (sqlite/postgres 방언 흡수, named param)"
```

---

### Task 3: 전체 스키마 마이그레이션 (`migrations.py`)

**Files:**
- Create: `src/dms/migrations.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Consumes: `Database` (Task 2)
- Produces: `migrate(db: Database) -> None` — idempotent, 스펙 §4의 20개 테이블 + `schema_migrations` 생성. 이후 모든 저장소가 이 스키마를 전제.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_migrations.py
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
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_migrations.py -v`
Expected: FAIL — `No module named 'dms.migrations'`

- [ ] **Step 3: 구현**

방언 분기는 auto-increment PK 하나뿐: sqlite `INTEGER PRIMARY KEY AUTOINCREMENT` / postgres `BIGSERIAL PRIMARY KEY`.

```python
# src/dms/migrations.py
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
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_migrations.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dms/migrations.py tests/test_migrations.py
git commit -m "feat: 전체 스키마 마이그레이션 (20 테이블, idempotent)"
```

---

### Task 4: 도메인 상태 모델 (`domain.py` 1/3)

**Files:**
- Create: `src/dms/domain.py`
- Test: `tests/test_domain_states.py`

**Interfaces:**
- Consumes: 없음
- Produces (이후 모든 태스크가 사용):
  - `RequestState(StrEnum)`: `Pending, Planned, Running, Succeeded, Failed, Rejected, Conflict, Cancelled` / `TERMINAL_REQUEST_STATES: frozenset[RequestState]`
  - `DataJobState(StrEnum)`: `Pending, Preflight, PreviewRunning, ConfirmPending, Executing, Running, Succeeded, Failed, TimedOut, Cancelled, Rejected, PreviewExpired` / `TERMINAL_DATA_JOB_STATES`
  - `Operation(StrEnum)`: `scan, sync, rm` / `Tool(StrEnum)`: `dscan, dsync, nsync, drm`
  - `PRIORITIES = ("low", "mid", "high")`, `PRIORITY_CLASS = {"low": "dms-low", "mid": "dms-mid", "high": "dms-high"}`
  - `ROLE_USER = "user"`, `ROLE_ADMIN = "admin"`
  - `class DomainValidationError(Exception)` — `reason_code: str`, `detail: str` 속성

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_domain_states.py
from dms.domain import (
    RequestState, TERMINAL_REQUEST_STATES,
    DataJobState, TERMINAL_DATA_JOB_STATES,
    Operation, Tool, PRIORITIES, PRIORITY_CLASS, DomainValidationError,
)


def test_request_terminal_states():
    assert TERMINAL_REQUEST_STATES == {
        RequestState.SUCCEEDED, RequestState.FAILED, RequestState.REJECTED,
        RequestState.CONFLICT, RequestState.CANCELLED,
    }
    assert RequestState.PENDING not in TERMINAL_REQUEST_STATES


def test_data_job_terminal_states():
    assert DataJobState.CONFIRM_PENDING not in TERMINAL_DATA_JOB_STATES
    assert {DataJobState.SUCCEEDED, DataJobState.FAILED, DataJobState.TIMED_OUT,
            DataJobState.CANCELLED, DataJobState.REJECTED,
            DataJobState.PREVIEW_EXPIRED} == TERMINAL_DATA_JOB_STATES


def test_enums_and_priority_map():
    assert Operation("sync") is Operation.SYNC
    assert Tool("nsync") is Tool.NSYNC
    assert PRIORITIES == ("low", "mid", "high")
    assert PRIORITY_CLASS["mid"] == "dms-mid"


def test_validation_error_carries_reason():
    err = DomainValidationError("unsafe_path", "leading slash")
    assert err.reason_code == "unsafe_path"
    assert "leading slash" in str(err)
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_domain_states.py -v`
Expected: FAIL — `No module named 'dms.domain'`

- [ ] **Step 3: 구현**

```python
# src/dms/domain.py
"""도메인 모델: 상태머신(스펙 §4), 검증 규칙, 옵션 allowlist. 이 모듈은 DB를 모른다."""
from enum import StrEnum


class RequestState(StrEnum):
    PENDING = "Pending"
    PLANNED = "Planned"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    REJECTED = "Rejected"
    CONFLICT = "Conflict"
    CANCELLED = "Cancelled"


TERMINAL_REQUEST_STATES = frozenset({
    RequestState.SUCCEEDED, RequestState.FAILED, RequestState.REJECTED,
    RequestState.CONFLICT, RequestState.CANCELLED,
})


class DataJobState(StrEnum):
    PENDING = "Pending"
    PREFLIGHT = "Preflight"
    PREVIEW_RUNNING = "PreviewRunning"
    CONFIRM_PENDING = "ConfirmPending"
    EXECUTING = "Executing"
    RUNNING = "Running"           # scan 실행 단계
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    TIMED_OUT = "TimedOut"
    CANCELLED = "Cancelled"
    REJECTED = "Rejected"
    PREVIEW_EXPIRED = "PreviewExpired"


TERMINAL_DATA_JOB_STATES = frozenset({
    DataJobState.SUCCEEDED, DataJobState.FAILED, DataJobState.TIMED_OUT,
    DataJobState.CANCELLED, DataJobState.REJECTED, DataJobState.PREVIEW_EXPIRED,
})


class Operation(StrEnum):
    SCAN = "scan"
    SYNC = "sync"
    RM = "rm"


class Tool(StrEnum):
    DSCAN = "dscan"
    DSYNC = "dsync"
    NSYNC = "nsync"
    DRM = "drm"


PRIORITIES = ("low", "mid", "high")
PRIORITY_CLASS = {"low": "dms-low", "mid": "dms-mid", "high": "dms-high"}

ROLE_USER = "user"
ROLE_ADMIN = "admin"


class DomainValidationError(Exception):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_domain_states.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dms/domain.py tests/test_domain_states.py
git commit -m "feat: 도메인 상태 모델 (요청/잡 상태머신, 우선순위 매핑)"
```

---

### Task 5: 경로 검증 규칙 (`domain.py` 2/3)

**Files:**
- Modify: `src/dms/domain.py` (함수 추가)
- Test: `tests/test_domain_paths.py`

**Interfaces:**
- Consumes: `DomainValidationError` (Task 4)
- Produces:
  - `validate_relative_path(path: str) -> str` — 정규화된 상대 경로 반환. 스펙 §4: managed_root 기준 storage-relative, 선행 `/`·`..`·NUL 금지. 위반 시 `DomainValidationError("unsafe_path", ...)`
  - `validate_sync_paths(source: str, destination: str) -> tuple[str, str]` — destination이 source와 같거나 하위면 `sync_destination_inside_source`
  - `validate_rm_target(target: str, options: dict) -> str` — managed_root 자체(빈 경로/`.`)면 `rm_root_forbidden`, `options.get("recursive") is not True`면 `rm_recursive_required`
  - `validate_owner_username(username: str) -> str` — POSIX 정규식 `[A-Za-z_][A-Za-z0-9._-]{0,63}` 불일치 시 `invalid_owner_username`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_domain_paths.py
import pytest
from dms.domain import (
    DomainValidationError, validate_relative_path, validate_sync_paths,
    validate_rm_target, validate_owner_username,
)


@pytest.mark.parametrize("bad", ["/abs/path", "a/../b", "..", "a/b\x00c", ""])
def test_unsafe_paths_rejected(bad):
    with pytest.raises(DomainValidationError) as e:
        validate_relative_path(bad)
    assert e.value.reason_code == "unsafe_path"


def test_valid_path_normalized():
    assert validate_relative_path("a/b/./c/") == "a/b/c"


@pytest.mark.parametrize("src,dst", [("a/b", "a/b"), ("a", "a/b/c")])
def test_sync_destination_inside_source_rejected(src, dst):
    with pytest.raises(DomainValidationError) as e:
        validate_sync_paths(src, dst)
    assert e.value.reason_code == "sync_destination_inside_source"


def test_sync_sibling_ok():
    assert validate_sync_paths("a/b", "a/c") == ("a/b", "a/c")


def test_rm_root_forbidden():
    with pytest.raises(DomainValidationError) as e:
        validate_rm_target(".", {"recursive": True})
    assert e.value.reason_code == "rm_root_forbidden"


def test_rm_requires_recursive():
    with pytest.raises(DomainValidationError) as e:
        validate_rm_target("a/b", {})
    assert e.value.reason_code == "rm_recursive_required"


def test_owner_username():
    assert validate_owner_username("alice_01") == "alice_01"
    with pytest.raises(DomainValidationError) as e:
        validate_owner_username("bad name!")
    assert e.value.reason_code == "invalid_owner_username"
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_domain_paths.py -v`
Expected: FAIL — `ImportError: cannot import name 'validate_relative_path'`

- [ ] **Step 3: 구현 (domain.py에 추가)**

```python
import posixpath
import re

_USERNAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9._-]{0,63}$")


def validate_relative_path(path: str) -> str:
    if not path or path.startswith("/") or "\x00" in path:
        raise DomainValidationError("unsafe_path", repr(path))
    normalized = posixpath.normpath(path)
    if normalized in (".", "..") or normalized.startswith("../") or "/../" in f"/{normalized}/":
        raise DomainValidationError("unsafe_path", repr(path))
    return normalized


def validate_sync_paths(source: str, destination: str) -> tuple[str, str]:
    src = validate_relative_path(source)
    dst = validate_relative_path(destination)
    if dst == src or dst.startswith(src + "/"):
        raise DomainValidationError("sync_destination_inside_source", f"{src} -> {dst}")
    return src, dst


def validate_rm_target(target: str, options: dict) -> str:
    if target in ("", "."):
        raise DomainValidationError("rm_root_forbidden", "managed_root itself")
    normalized = validate_relative_path(target)
    if options.get("recursive") is not True:
        raise DomainValidationError("rm_recursive_required", "options.recursive must be true")
    return normalized


def validate_owner_username(username: str) -> str:
    if not _USERNAME_RE.fullmatch(username):
        raise DomainValidationError("invalid_owner_username", repr(username))
    return username
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_domain_paths.py -v`
Expected: PASS (9 tests). `pytest -q` 전체도 통과 확인.

- [ ] **Step 5: Commit**

```bash
git add src/dms/domain.py tests/test_domain_paths.py
git commit -m "feat: 경로 검증 규칙 (storage-relative, sync/rm 안전 규칙)"
```

---

### Task 6: 옵션 allowlist + fingerprint + resource_key (`domain.py` 3/3)

**Files:**
- Modify: `src/dms/domain.py`
- Test: `tests/test_domain_options.py`

**Interfaces:**
- Consumes: Task 4·5의 심볼
- Produces:
  - `validate_options(operation: Operation, options: dict) -> dict` — allowlist + 타입/범위 검증 후 정규화 dict 반환. 미지 키는 `DomainValidationError("unknown_option", ...)`, 타입/범위 위반은 `invalid_option`
    - scan: `summary_only: bool`, `max_depth: int(1..64)`, `follow_symlinks: bool`, `one_file_system: bool`
    - sync: `delete: bool`, `batch_files: int(1..1_000_000)`, `contents: bool`, `direct: bool`, `open_noatime: bool`, `bufsize: int(4096..1_073_741_824)`, `quiet: bool`, `chmod: str`(mpifileutils 문법 `[DF]?[0-7]{1,4}` 콤마 목록), `chown: str`(`USER`, `:GROUP`, `USER:GROUP`)
    - rm: `recursive: bool`, `stat: bool`, `lite: bool`, `quiet: bool` (`stat`/`lite` 동시 지정 금지 → `invalid_option`)
  - `option_fingerprint(options: dict) -> str` — `sha256(sorted-json)` hex
  - `build_resource_key(operation, *, storage=None, source_storage=None, destination_storage=None, source=None, destination=None, target=None, fingerprint) -> str`
    - scan: `data.scan:{storage}:{target}:{fp}` / rm: `data.rm:{storage}:{target}:{fp}`
    - sync: `data.sync:{source_storage}:{source}:{destination_storage}:{destination}:{fp}`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_domain_options.py
import pytest
from dms.domain import (
    Operation, DomainValidationError, validate_options,
    option_fingerprint, build_resource_key,
)


def test_scan_options_ok():
    out = validate_options(Operation.SCAN, {"summary_only": True, "max_depth": 3})
    assert out == {"summary_only": True, "max_depth": 3}


def test_unknown_option_rejected():
    with pytest.raises(DomainValidationError) as e:
        validate_options(Operation.SCAN, {"command_line": "rm -rf /"})
    assert e.value.reason_code == "unknown_option"


@pytest.mark.parametrize("opts", [
    {"batch_files": 0}, {"bufsize": 100}, {"delete": "yes"},
    {"chmod": "999999"}, {"chown": "bad name"},
])
def test_sync_invalid_values(opts):
    with pytest.raises(DomainValidationError) as e:
        validate_options(Operation.SYNC, opts)
    assert e.value.reason_code == "invalid_option"


def test_sync_chmod_chown_ok():
    out = validate_options(Operation.SYNC, {"chmod": "D0750,F0640", "chown": "alice:dev"})
    assert out["chmod"] == "D0750,F0640"


def test_rm_stat_lite_exclusive():
    with pytest.raises(DomainValidationError) as e:
        validate_options(Operation.RM, {"recursive": True, "stat": True, "lite": True})
    assert e.value.reason_code == "invalid_option"


def test_fingerprint_is_order_insensitive():
    a = option_fingerprint({"x": 1, "y": 2})
    b = option_fingerprint({"y": 2, "x": 1})
    assert a == b and len(a) == 64


def test_resource_keys():
    fp = "f" * 64
    assert build_resource_key(Operation.SCAN, storage="s1", target="a/b", fingerprint=fp) \
        == f"data.scan:s1:a/b:{fp}"
    assert build_resource_key(
        Operation.SYNC, source_storage="s1", source="a", destination_storage="s2",
        destination="b", fingerprint=fp) == f"data.sync:s1:a:s2:b:{fp}"
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_domain_options.py -v`
Expected: FAIL — ImportError

- [ ] **Step 3: 구현 (domain.py에 추가)**

```python
import hashlib
import json

_CHMOD_ITEM_RE = re.compile(r"[DF]?[0-7]{1,4}$")
_CHOWN_RE = re.compile(r"([A-Za-z_][A-Za-z0-9._-]{0,63})?(:[A-Za-z_][A-Za-z0-9._-]{0,63})?$")

_BOOL = ("bool",)
_OPTION_SPECS: dict[Operation, dict[str, tuple]] = {
    Operation.SCAN: {
        "summary_only": _BOOL, "follow_symlinks": _BOOL, "one_file_system": _BOOL,
        "max_depth": ("int", 1, 64),
    },
    Operation.SYNC: {
        "delete": _BOOL, "contents": _BOOL, "direct": _BOOL,
        "open_noatime": _BOOL, "quiet": _BOOL,
        "batch_files": ("int", 1, 1_000_000),
        "bufsize": ("int", 4096, 1_073_741_824),
        "chmod": ("chmod",), "chown": ("chown",),
    },
    Operation.RM: {"recursive": _BOOL, "stat": _BOOL, "lite": _BOOL, "quiet": _BOOL},
}


def validate_options(operation: Operation, options: dict) -> dict:
    spec = _OPTION_SPECS[Operation(operation)]
    out: dict = {}
    for key, value in (options or {}).items():
        rule = spec.get(key)
        if rule is None:
            raise DomainValidationError("unknown_option", key)
        kind = rule[0]
        if kind == "bool":
            if not isinstance(value, bool):
                raise DomainValidationError("invalid_option", f"{key} must be bool")
        elif kind == "int":
            lo, hi = rule[1], rule[2]
            if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
                raise DomainValidationError("invalid_option", f"{key} must be int {lo}..{hi}")
        elif kind == "chmod":
            if not isinstance(value, str) or not all(
                    _CHMOD_ITEM_RE.fullmatch(p) for p in value.split(",")):
                raise DomainValidationError("invalid_option", f"bad chmod {value!r}")
        elif kind == "chown":
            if not isinstance(value, str) or not value or not _CHOWN_RE.fullmatch(value):
                raise DomainValidationError("invalid_option", f"bad chown {value!r}")
        out[key] = value
    if Operation(operation) is Operation.RM and out.get("stat") and out.get("lite"):
        raise DomainValidationError("invalid_option", "stat and lite are mutually exclusive")
    return out


def option_fingerprint(options: dict) -> str:
    payload = json.dumps(options or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def build_resource_key(operation, *, storage=None, source_storage=None,
                       destination_storage=None, source=None, destination=None,
                       target=None, fingerprint: str) -> str:
    op = Operation(operation)
    if op is Operation.SYNC:
        return f"data.sync:{source_storage}:{source}:{destination_storage}:{destination}:{fingerprint}"
    return f"data.{op.value}:{storage}:{target}:{fingerprint}"
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_domain_options.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dms/domain.py tests/test_domain_options.py
git commit -m "feat: 잡 옵션 allowlist, option fingerprint, resource_key"
```

---

### Task 7: 설정 (`config.py`)

**Files:**
- Create: `src/dms/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `@dataclass Settings`: `database_url: str`, `shared_token: str`, `admin_token: str`, `session_secret: str`, `api_host: str = "0.0.0.0"`, `api_port: int = 8080`
  - `Settings.from_env(environ: Mapping) -> Settings` — env 키: `DMS_DATABASE_URL`, `DMS_SHARED_TOKEN`, `DMS_ADMIN_TOKEN`, `DMS_SESSION_SECRET`, `DMS_API_HOST`, `DMS_API_PORT`
  - `class SettingsError(Exception)` — 문제를 **전부 모아** 한 번에 보고 (`problems: list[str]`)
  - placeholder 가드: 값이 비었거나 `CHANGE_ME`이거나 `REPLACE_WITH_`로 시작하면 invalid — truthy placeholder가 게이트를 통과하는 구멍 금지 (스펙 §3)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_config.py
import pytest
from dms.config import Settings, SettingsError

VALID = {
    "DMS_DATABASE_URL": "sqlite:///tmp/dms.db",
    "DMS_SHARED_TOKEN": "tok-abc",
    "DMS_ADMIN_TOKEN": "adm-xyz",
    "DMS_SESSION_SECRET": "sess-123",
}


def test_valid_env():
    s = Settings.from_env(VALID)
    assert s.database_url == "sqlite:///tmp/dms.db"
    assert s.api_port == 8080


def test_missing_and_placeholder_collected():
    env = dict(VALID)
    env.pop("DMS_DATABASE_URL")
    env["DMS_SHARED_TOKEN"] = "CHANGE_ME"
    env["DMS_ADMIN_TOKEN"] = "REPLACE_WITH_TOKEN"
    with pytest.raises(SettingsError) as e:
        Settings.from_env(env)
    text = str(e.value)
    assert "DMS_DATABASE_URL" in text
    assert "DMS_SHARED_TOKEN" in text
    assert "DMS_ADMIN_TOKEN" in text


def test_port_parsing():
    s = Settings.from_env({**VALID, "DMS_API_PORT": "9000"})
    assert s.api_port == 9000
    with pytest.raises(SettingsError):
        Settings.from_env({**VALID, "DMS_API_PORT": "not-a-number"})
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `No module named 'dms.config'`

- [ ] **Step 3: 구현**

```python
# src/dms/config.py
"""env 기반 설정. 기동 시 전부 검증하고, placeholder가 통과하는 구멍을 만들지 않는다."""
from dataclasses import dataclass
from typing import Mapping


class SettingsError(Exception):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def _is_placeholder(value: str | None) -> bool:
    return not value or value == "CHANGE_ME" or value.startswith("REPLACE_WITH_")


@dataclass(frozen=True)
class Settings:
    database_url: str
    shared_token: str
    admin_token: str
    session_secret: str
    api_host: str = "0.0.0.0"
    api_port: int = 8080

    @classmethod
    def from_env(cls, environ: Mapping) -> "Settings":
        problems: list[str] = []
        required = ("DMS_DATABASE_URL", "DMS_SHARED_TOKEN",
                    "DMS_ADMIN_TOKEN", "DMS_SESSION_SECRET")
        values: dict = {}
        for key in required:
            value = environ.get(key)
            if _is_placeholder(value):
                problems.append(f"{key} is missing or a placeholder")
            values[key] = value
        port_raw = environ.get("DMS_API_PORT", "8080")
        try:
            port = int(port_raw)
        except ValueError:
            problems.append(f"DMS_API_PORT is not an integer: {port_raw!r}")
            port = 0
        if problems:
            raise SettingsError(problems)
        return cls(
            database_url=values["DMS_DATABASE_URL"],
            shared_token=values["DMS_SHARED_TOKEN"],
            admin_token=values["DMS_ADMIN_TOKEN"],
            session_secret=values["DMS_SESSION_SECRET"],
            api_host=environ.get("DMS_API_HOST", "0.0.0.0"),
            api_port=port,
        )
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dms/config.py tests/test_config.py
git commit -m "feat: env 설정 로딩 + placeholder fail-closed 검증"
```

---

### Task 8: 요청 저장소 (`repositories/requests.py`)

**Files:**
- Create: `src/dms/repositories/__init__.py`
- Create: `src/dms/repositories/requests.py`
- Test: `tests/test_repo_requests.py`, `tests/conftest.py` 픽스처 추가

**Interfaces:**
- Consumes: `Database`(Task 2), `migrate`(Task 3), `RequestState`(Task 4)
- Produces:
  - `RequestsRepository(db)`:
    - `create(*, operation: str, requester_id: str, actor: str, resource_key: str, payload: dict, priority: str) -> str` — request_id(uuid4 hex) 반환. 트랜잭션 안에서 `commit_order = max+1` 할당, 상태 `Pending`, state_transitions에 `None -> Pending` 기록
    - `get(request_id) -> dict | None` (payload는 dict로 역직렬화)
    - `list(requester_id: str | None = None, limit: int = 50) -> list[dict]` (commit_order 역순)
    - `set_state(request_id, to_state: RequestState, *, reason_code=None, actor) -> None` — 상태 갱신 + 전이 기록
    - `find_active(resource_key) -> dict | None` — 비터미널 상태의 동일 resource_key 요청
    - `record_result(request_id, terminal_state, *, reason_code=None, message=None, summary: dict | None = None) -> None`
    - `transitions(request_id) -> list[dict]`
- `src/dms/repositories/__init__.py`의 `Repositories` 클래스가 이후 태스크에서 리포지토리들을 모은다: `Repositories(db)` → `.requests`, `.storages`(Task 9), `.accounts`(Task 10), `.control`(Task 11)

- [ ] **Step 1: conftest 픽스처 작성**

```python
# tests/conftest.py
import pytest
from dms.db import Database
from dms.migrations import migrate


@pytest.fixture
def db(tmp_path):
    database = Database.connect(f"sqlite:///{tmp_path}/test.db")
    migrate(database)
    return database
```

- [ ] **Step 2: 실패하는 테스트 작성**

```python
# tests/test_repo_requests.py
from dms.domain import RequestState
from dms.repositories.requests import RequestsRepository


def _create(repo, key="data.scan:s1:a:ff"):
    return repo.create(operation="scan", requester_id="alice", actor="alice",
                       resource_key=key, payload={"target": "a"}, priority="mid")


def test_create_and_get(db):
    repo = RequestsRepository(db)
    rid = _create(repo)
    row = repo.get(rid)
    assert row["state"] == "Pending"
    assert row["payload"] == {"target": "a"}
    assert row["commit_order"] == 1
    assert _create(repo, key="k2") != rid
    assert repo.get("nope") is None


def test_transitions_recorded(db):
    repo = RequestsRepository(db)
    rid = _create(repo)
    repo.set_state(rid, RequestState.PLANNED, actor="planner")
    repo.set_state(rid, RequestState.REJECTED, reason_code="storage_missing", actor="planner")
    ts = repo.transitions(rid)
    assert [(t["from_state"], t["to_state"]) for t in ts] == [
        (None, "Pending"), ("Pending", "Planned"), ("Planned", "Rejected")]
    assert ts[2]["reason_code"] == "storage_missing"


def test_find_active_excludes_terminal(db):
    repo = RequestsRepository(db)
    rid = _create(repo, key="dup")
    assert repo.find_active("dup")["request_id"] == rid
    repo.set_state(rid, RequestState.CANCELLED, actor="admin")
    assert repo.find_active("dup") is None


def test_record_result_and_list(db):
    repo = RequestsRepository(db)
    rid = _create(repo)
    repo.set_state(rid, RequestState.FAILED, reason_code="x", actor="stepper")
    repo.record_result(rid, RequestState.FAILED, reason_code="x", message="boom",
                       summary={"n": 1})
    assert repo.list(requester_id="alice")[0]["request_id"] == rid
    assert repo.list(requester_id="bob") == []
```

- [ ] **Step 3: 실패 확인**

Run: `pytest tests/test_repo_requests.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 4: 구현**

```python
# src/dms/repositories/requests.py
import uuid
from ..db import Database, dump_json, load_json, utc_now_iso
from ..domain import RequestState, TERMINAL_REQUEST_STATES


class RequestsRepository:
    def __init__(self, db: Database):
        self._db = db

    def create(self, *, operation, requester_id, actor, resource_key,
               payload: dict, priority: str) -> str:
        request_id = uuid.uuid4().hex
        now = utc_now_iso()
        with self._db.transaction():
            row = self._db.query_one("SELECT COALESCE(MAX(commit_order), 0) AS m FROM requests")
            order = row["m"] + 1
            self._db.execute(
                """INSERT INTO requests (request_id, commit_order, operation, requester_id,
                       actor, resource_key, priority, payload, state, created_at, updated_at)
                   VALUES (:id, :o, :op, :req, :actor, :key, :pri, :payload, :state, :now, :now)""",
                {"id": request_id, "o": order, "op": operation, "req": requester_id,
                 "actor": actor, "key": resource_key, "pri": priority,
                 "payload": dump_json(payload), "state": RequestState.PENDING.value,
                 "now": now},
            )
            self._record_transition(request_id, None, RequestState.PENDING, None, actor, now)
        return request_id

    def _record_transition(self, request_id, from_state, to_state, reason_code, actor, at):
        self._db.execute(
            """INSERT INTO state_transitions (entity_kind, entity_id, from_state,
                   to_state, reason_code, actor, at)
               VALUES ('request', :id, :f, :t, :r, :actor, :at)""",
            {"id": request_id,
             "f": from_state.value if from_state else None,
             "t": to_state.value, "r": reason_code, "actor": actor, "at": at},
        )

    def get(self, request_id) -> dict | None:
        row = self._db.query_one("SELECT * FROM requests WHERE request_id = :id",
                                 {"id": request_id})
        if row:
            row["payload"] = load_json(row["payload"])
        return row

    def list(self, requester_id=None, limit: int = 50) -> list[dict]:
        if requester_id is None:
            rows = self._db.query(
                "SELECT * FROM requests ORDER BY commit_order DESC LIMIT :n", {"n": limit})
        else:
            rows = self._db.query(
                """SELECT * FROM requests WHERE requester_id = :req
                   ORDER BY commit_order DESC LIMIT :n""",
                {"req": requester_id, "n": limit})
        for row in rows:
            row["payload"] = load_json(row["payload"])
        return rows

    def set_state(self, request_id, to_state: RequestState, *, reason_code=None, actor):
        now = utc_now_iso()
        current = self._db.query_one(
            "SELECT state FROM requests WHERE request_id = :id", {"id": request_id})
        if current is None:
            raise KeyError(request_id)
        with self._db.transaction():
            self._db.execute(
                "UPDATE requests SET state = :s, updated_at = :now WHERE request_id = :id",
                {"s": to_state.value, "now": now, "id": request_id})
            self._record_transition(request_id, RequestState(current["state"]),
                                    to_state, reason_code, actor, now)

    def find_active(self, resource_key) -> dict | None:
        terminal = tuple(s.value for s in TERMINAL_REQUEST_STATES)
        placeholders = ", ".join(f":t{i}" for i in range(len(terminal)))
        params = {f"t{i}": v for i, v in enumerate(terminal)}
        params["key"] = resource_key
        return self._db.query_one(
            f"""SELECT * FROM requests WHERE resource_key = :key
                AND state NOT IN ({placeholders})
                ORDER BY commit_order LIMIT 1""", params)

    def record_result(self, request_id, terminal_state, *, reason_code=None,
                      message=None, summary=None):
        self._db.execute(
            """INSERT INTO results (request_id, terminal_state, reason_code, message,
                   summary, completed_at)
               VALUES (:id, :s, :r, :m, :sum, :now)""",
            {"id": request_id, "s": RequestState(terminal_state).value, "r": reason_code,
             "m": message, "sum": dump_json(summary) if summary is not None else None,
             "now": utc_now_iso()})

    def transitions(self, request_id) -> list[dict]:
        return self._db.query(
            """SELECT * FROM state_transitions
               WHERE entity_kind = 'request' AND entity_id = :id ORDER BY id""",
            {"id": request_id})
```

`src/dms/repositories/__init__.py`:

```python
from ..db import Database
from .requests import RequestsRepository


class Repositories:
    """저장소 집합. API/컨트롤러는 이 객체 하나로 DB에 접근한다."""
    def __init__(self, db: Database):
        self.db = db
        self.requests = RequestsRepository(db)
```

- [ ] **Step 5: 통과 확인**

Run: `pytest tests/test_repo_requests.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add src/dms/repositories/ tests/test_repo_requests.py tests/conftest.py
git commit -m "feat: 요청 저장소 (lifecycle, 전이 기록, active 조회, 결과)"
```

---

### Task 9: 스토리지 저장소 + 감사 (`repositories/storages.py`)

**Files:**
- Create: `src/dms/repositories/storages.py`
- Modify: `src/dms/repositories/__init__.py` (`self.storages = StoragesRepository(db)` 추가)
- Test: `tests/test_repo_storages.py`

**Interfaces:**
- Consumes: `Database`, `DomainValidationError`
- Produces:
  - `StoragesRepository(db)`:
    - `create(*, storage_name, mount_path, managed_root, backend_type, actor) -> dict`
    - `update(storage_name, *, mount_path, managed_root, backend_type, enabled: bool, actor) -> dict` — 전체 필드 round-trip (부분 patch 없음, 스펙 legacy 계승)
    - `delete(storage_name, actor) -> dict` — 하드 삭제, 삭제된 행 반환
    - `get(storage_name) -> dict | None`, `list() -> list[dict]`
    - `set_status(storage_name, status, detail) -> None` (reconciler용, Phase 2)
  - 검증: `storage_name` 정규식 `[a-z0-9]([a-z0-9-]{0,62})`, `backend_type ∈ {cephfs, gpfs, wekafs}`, `mount_path`는 절대 경로, `managed_root`는 mount_path와 같거나 하위 절대 경로 — 위반 시 `DomainValidationError("invalid_storage", ...)`
  - 모든 변경은 `audit_log`에 `mutation_class='storage'`, before/after JSON 기록

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_repo_storages.py
import pytest
from dms.domain import DomainValidationError
from dms.repositories.storages import StoragesRepository

FIELDS = dict(storage_name="ceph-a", mount_path="/mnt/ceph",
              managed_root="/mnt/ceph/dms", backend_type="cephfs")


def test_create_get_list(db):
    repo = StoragesRepository(db)
    repo.create(**FIELDS, actor="admin")
    row = repo.get("ceph-a")
    assert row["managed_root"] == "/mnt/ceph/dms"
    assert row["enabled"] == 1 and row["status"] == "Unknown"
    assert [s["storage_name"] for s in repo.list()] == ["ceph-a"]


@pytest.mark.parametrize("bad", [
    {"managed_root": "/other/root"},          # mount_path 밖
    {"backend_type": "nfs"},                  # 미지원 백엔드
    {"mount_path": "relative/path"},          # 상대 경로
    {"storage_name": "Bad_Name"},             # 이름 규칙 위반
])
def test_invalid_fields_rejected(db, bad):
    repo = StoragesRepository(db)
    with pytest.raises(DomainValidationError) as e:
        repo.create(**{**FIELDS, **bad}, actor="admin")
    assert e.value.reason_code == "invalid_storage"


def test_update_and_delete_are_audited(db):
    repo = StoragesRepository(db)
    repo.create(**FIELDS, actor="admin")
    repo.update("ceph-a", mount_path="/mnt/ceph", managed_root="/mnt/ceph/dms2",
                backend_type="cephfs", enabled=False, actor="admin")
    assert repo.get("ceph-a")["enabled"] == 0
    deleted = repo.delete("ceph-a", actor="admin")
    assert deleted["storage_name"] == "ceph-a"
    assert repo.get("ceph-a") is None
    audit = db.query("SELECT operation, before_state, after_state FROM audit_log "
                     "WHERE mutation_class = 'storage' ORDER BY id")
    assert [a["operation"] for a in audit] == ["create", "update", "delete"]
    assert audit[0]["before_state"] is None
    assert audit[2]["after_state"] is None
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_repo_storages.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 구현**

```python
# src/dms/repositories/storages.py
import posixpath
import re
from ..db import Database, dump_json, utc_now_iso
from ..domain import DomainValidationError

_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}$")
_BACKENDS = ("cephfs", "gpfs", "wekafs")


def _validate(storage_name, mount_path, managed_root, backend_type):
    if not _NAME_RE.fullmatch(storage_name):
        raise DomainValidationError("invalid_storage", f"bad name {storage_name!r}")
    if backend_type not in _BACKENDS:
        raise DomainValidationError("invalid_storage", f"bad backend {backend_type!r}")
    for p in (mount_path, managed_root):
        if not p.startswith("/") or posixpath.normpath(p) != p.rstrip("/") and p != "/":
            raise DomainValidationError("invalid_storage", f"bad path {p!r}")
    mount = posixpath.normpath(mount_path)
    root = posixpath.normpath(managed_root)
    if root != mount and not root.startswith(mount + "/"):
        raise DomainValidationError("invalid_storage",
                                    "managed_root must be under mount_path")


class StoragesRepository:
    def __init__(self, db: Database):
        self._db = db

    def _audit(self, operation, target, before, after, actor):
        self._db.execute(
            """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                   before_state, after_state, at)
               VALUES ('storage', :op, :key, :actor, :b, :a, :at)""",
            {"op": operation, "key": target, "actor": actor,
             "b": dump_json(before) if before else None,
             "a": dump_json(after) if after else None, "at": utc_now_iso()})

    def create(self, *, storage_name, mount_path, managed_root, backend_type, actor):
        _validate(storage_name, mount_path, managed_root, backend_type)
        now = utc_now_iso()
        with self._db.transaction():
            self._db.execute(
                """INSERT INTO storages (storage_name, mount_path, managed_root,
                       backend_type, enabled, status, created_at, updated_at, updated_by)
                   VALUES (:n, :m, :r, :b, 1, 'Unknown', :now, :now, :actor)""",
                {"n": storage_name, "m": mount_path, "r": managed_root,
                 "b": backend_type, "now": now, "actor": actor})
            after = self.get(storage_name)
            self._audit("create", storage_name, None, after, actor)
        return after

    def update(self, storage_name, *, mount_path, managed_root, backend_type,
               enabled: bool, actor):
        _validate(storage_name, mount_path, managed_root, backend_type)
        before = self.get(storage_name)
        if before is None:
            raise KeyError(storage_name)
        with self._db.transaction():
            self._db.execute(
                """UPDATE storages SET mount_path = :m, managed_root = :r,
                       backend_type = :b, enabled = :e, updated_at = :now,
                       updated_by = :actor
                   WHERE storage_name = :n""",
                {"m": mount_path, "r": managed_root, "b": backend_type,
                 "e": 1 if enabled else 0, "now": utc_now_iso(),
                 "actor": actor, "n": storage_name})
            after = self.get(storage_name)
            self._audit("update", storage_name, before, after, actor)
        return after

    def delete(self, storage_name, actor):
        before = self.get(storage_name)
        if before is None:
            raise KeyError(storage_name)
        with self._db.transaction():
            self._db.execute("DELETE FROM storages WHERE storage_name = :n",
                             {"n": storage_name})
            self._audit("delete", storage_name, before, None, actor)
        return before

    def get(self, storage_name):
        return self._db.query_one(
            "SELECT * FROM storages WHERE storage_name = :n", {"n": storage_name})

    def list(self):
        return self._db.query("SELECT * FROM storages ORDER BY storage_name")

    def set_status(self, storage_name, status, detail=None):
        self._db.execute(
            """UPDATE storages SET status = :s, status_detail = :d, updated_at = :now
               WHERE storage_name = :n""",
            {"s": status, "d": detail, "now": utc_now_iso(), "n": storage_name})
```

`repositories/__init__.py`의 `Repositories.__init__`에 `self.storages = StoragesRepository(db)` 추가 (import 포함).

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_repo_storages.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dms/repositories/ tests/test_repo_storages.py
git commit -m "feat: 스토리지 저장소 (검증, 전체 round-trip 수정, 하드 삭제, 감사 기록)"
```

---

### Task 10: 계정 저장소 (`repositories/accounts.py`)

**Files:**
- Create: `src/dms/repositories/accounts.py`
- Modify: `src/dms/repositories/__init__.py` (`self.accounts` 추가)
- Test: `tests/test_repo_accounts.py`

**Interfaces:**
- Consumes: `Database`, `ROLE_USER`/`ROLE_ADMIN`
- Produces:
  - `AccountsRepository(db)`:
    - `create(username, password, role, email=None) -> None` — username 정규식 `[a-z0-9][a-z0-9._-]{0,63}` 위반 시 `DomainValidationError("invalid_username")`, 중복 시 `DomainValidationError("account_exists")`
    - `verify(username, password) -> str | None` — 성공 시 role 반환, 실패/disabled 시 None
    - `set_password(username, password) -> None`, `get(username) -> dict | None` (password_hash 제외), `list() -> list[dict]`
  - 해시: stdlib `hashlib.scrypt` — 저장 형식 `scrypt$16384$8$1$<salt_hex>$<hash_hex>`, 검증은 `hmac.compare_digest`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_repo_accounts.py
import pytest
from dms.domain import DomainValidationError, ROLE_ADMIN, ROLE_USER
from dms.repositories.accounts import AccountsRepository


def test_create_verify_roundtrip(db):
    repo = AccountsRepository(db)
    repo.create("alice", "pw-1", ROLE_USER, email="alice@corp.example")
    assert repo.verify("alice", "pw-1") == ROLE_USER
    assert repo.verify("alice", "wrong") is None
    assert repo.verify("nobody", "pw") is None
    row = repo.get("alice")
    assert row["role"] == ROLE_USER and "password_hash" not in row


def test_duplicate_and_invalid_username(db):
    repo = AccountsRepository(db)
    repo.create("bob", "pw", ROLE_ADMIN)
    with pytest.raises(DomainValidationError) as e:
        repo.create("bob", "pw2", ROLE_USER)
    assert e.value.reason_code == "account_exists"
    with pytest.raises(DomainValidationError) as e:
        repo.create("Bad User!", "pw", ROLE_USER)
    assert e.value.reason_code == "invalid_username"


def test_set_password(db):
    repo = AccountsRepository(db)
    repo.create("carol", "old", ROLE_USER)
    repo.set_password("carol", "new")
    assert repo.verify("carol", "old") is None
    assert repo.verify("carol", "new") == ROLE_USER
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_repo_accounts.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 구현**

```python
# src/dms/repositories/accounts.py
import hashlib
import hmac
import os
import re
from ..db import Database, utc_now_iso
from ..domain import DomainValidationError

_USERNAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}$")
_N, _R, _P = 16384, 8, 1


def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt_hex, hash_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                                n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


class AccountsRepository:
    def __init__(self, db: Database):
        self._db = db

    def create(self, username, password, role, email=None):
        if not _USERNAME_RE.fullmatch(username):
            raise DomainValidationError("invalid_username", repr(username))
        if self._db.query_one("SELECT 1 AS x FROM accounts WHERE username = :u",
                              {"u": username}):
            raise DomainValidationError("account_exists", username)
        self._db.execute(
            """INSERT INTO accounts (username, password_hash, role, email, created_at)
               VALUES (:u, :h, :r, :e, :now)""",
            {"u": username, "h": _hash_password(password), "r": role,
             "e": email, "now": utc_now_iso()})

    def verify(self, username, password) -> str | None:
        row = self._db.query_one(
            "SELECT password_hash, role, disabled FROM accounts WHERE username = :u",
            {"u": username})
        if not row or row["disabled"]:
            return None
        return row["role"] if _verify_password(password, row["password_hash"]) else None

    def set_password(self, username, password):
        self._db.execute("UPDATE accounts SET password_hash = :h WHERE username = :u",
                         {"h": _hash_password(password), "u": username})

    def get(self, username):
        row = self._db.query_one(
            """SELECT username, role, email, disabled, created_at
               FROM accounts WHERE username = :u""", {"u": username})
        return row

    def list(self):
        return self._db.query(
            "SELECT username, role, email, disabled, created_at FROM accounts "
            "ORDER BY username")
```

`Repositories`에 `self.accounts = AccountsRepository(db)` 추가.

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_repo_accounts.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dms/repositories/ tests/test_repo_accounts.py
git commit -m "feat: 포탈 계정 저장소 (scrypt 해시, role)"
```

---

### Task 11: 컨트롤 저장소 — 정책/denylist/컨트롤 상태/리스 (`repositories/control.py`)

**Files:**
- Create: `src/dms/repositories/control.py`
- Modify: `src/dms/repositories/__init__.py` (`self.control` 추가)
- Test: `tests/test_repo_control.py`

**Interfaces:**
- Consumes: `Database`
- Produces:
  - `ControlRepository(db)`:
    - 정책: `get_policy(tool) -> dict | None`, `upsert_policy(tool, *, max_nodes, procs_per_node, queue, default_priority, max_priority, preview_timeout_seconds, execution_timeout_seconds, enabled, actor)` — tool ∈ `{scan, dsync, nsync, rm}` 외에는 `DomainValidationError("invalid_policy")`. 변경은 audit_log(`mutation_class='policy'`)
    - denylist: `deny(subject_type, subject, reason, actor)` / `allow(subject_type, subject, actor)` / `is_denied(*, requester, owner, groups: list[str]) -> str | None` (대소문자 무관 매칭, 걸린 subject 반환). subject_type ∈ `{requester, owner, group}`. 변경은 audit_log(`mutation_class='denylist'`)
    - 컨트롤 상태: `control_state() -> dict`, `set_control_state(*, maintenance: bool, drain: bool, reason, actor)` (audit 포함)
    - 리스: `try_acquire_lease(component, holder, lease_seconds, now_iso: str | None = None) -> bool` — 만료 전이면 다른 holder 거부, 같은 holder는 갱신
- 테스트 주입을 위해 `now_iso` 파라미터 허용 (기본 `utc_now_iso()`)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_repo_control.py
import pytest
from dms.domain import DomainValidationError
from dms.repositories.control import ControlRepository


def test_policy_upsert_and_get(db):
    repo = ControlRepository(db)
    assert repo.get_policy("dsync") is None
    repo.upsert_policy("dsync", max_nodes=4, procs_per_node=8, queue="dms-data",
                       default_priority="mid", max_priority="high",
                       preview_timeout_seconds=3600, execution_timeout_seconds=259200,
                       enabled=True, actor="admin")
    assert repo.get_policy("dsync")["max_nodes"] == 4
    with pytest.raises(DomainValidationError):
        repo.upsert_policy("dcp", max_nodes=1, procs_per_node=1, queue="q",
                           default_priority="mid", max_priority="high",
                           preview_timeout_seconds=None, execution_timeout_seconds=60,
                           enabled=True, actor="admin")


def test_denylist_matching(db):
    repo = ControlRepository(db)
    repo.deny("requester", "Mallory", reason="incident", actor="admin")
    repo.deny("group", "blocked-team", reason=None, actor="admin")
    assert repo.is_denied(requester="mallory", owner="x", groups=[]) == "Mallory"
    assert repo.is_denied(requester="a", owner="b", groups=["Blocked-Team"]) == "blocked-team"
    assert repo.is_denied(requester="a", owner="b", groups=["ok"]) is None
    repo.allow("requester", "Mallory", actor="admin")
    assert repo.is_denied(requester="mallory", owner="x", groups=[]) is None


def test_control_state_roundtrip(db):
    repo = ControlRepository(db)
    assert repo.control_state()["maintenance"] == 0
    repo.set_control_state(maintenance=True, drain=False, reason="upgrade", actor="admin")
    st = repo.control_state()
    assert st["maintenance"] == 1 and st["reason"] == "upgrade"


def test_lease_semantics(db):
    repo = ControlRepository(db)
    assert repo.try_acquire_lease("planner", "h1", 30, now_iso="2026-08-02T10:00:00Z")
    assert not repo.try_acquire_lease("planner", "h2", 30, now_iso="2026-08-02T10:00:10Z")
    assert repo.try_acquire_lease("planner", "h1", 30, now_iso="2026-08-02T10:00:10Z")
    assert repo.try_acquire_lease("planner", "h2", 30, now_iso="2026-08-02T10:00:41Z")
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_repo_control.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 구현**

```python
# src/dms/repositories/control.py
from datetime import datetime, timedelta, timezone
from ..db import Database, dump_json, utc_now_iso
from ..domain import DomainValidationError

POLICY_TOOLS = ("scan", "dsync", "nsync", "rm")
DENY_SUBJECT_TYPES = ("requester", "owner", "group")


def _iso_plus(now_iso: str, seconds: int) -> str:
    base = datetime.strptime(now_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (base + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


class ControlRepository:
    def __init__(self, db: Database):
        self._db = db

    def _audit(self, mutation_class, operation, target, before, after, actor):
        self._db.execute(
            """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                   before_state, after_state, at)
               VALUES (:c, :op, :key, :actor, :b, :a, :at)""",
            {"c": mutation_class, "op": operation, "key": target, "actor": actor,
             "b": dump_json(before) if before else None,
             "a": dump_json(after) if after else None, "at": utc_now_iso()})

    # --- policies ---
    def get_policy(self, tool):
        return self._db.query_one("SELECT * FROM policies WHERE tool = :t", {"t": tool})

    def upsert_policy(self, tool, *, max_nodes, procs_per_node, queue,
                      default_priority, max_priority, preview_timeout_seconds,
                      execution_timeout_seconds, enabled, actor):
        if tool not in POLICY_TOOLS:
            raise DomainValidationError("invalid_policy", f"unknown tool {tool!r}")
        before = self.get_policy(tool)
        now = utc_now_iso()
        with self._db.transaction():
            self._db.execute("DELETE FROM policies WHERE tool = :t", {"t": tool})
            self._db.execute(
                """INSERT INTO policies (tool, max_nodes, procs_per_node, queue,
                       default_priority, max_priority, preview_timeout_seconds,
                       execution_timeout_seconds, enabled, updated_at, updated_by)
                   VALUES (:t, :mn, :pp, :q, :dp, :mp, :pt, :et, :e, :now, :actor)""",
                {"t": tool, "mn": max_nodes, "pp": procs_per_node, "q": queue,
                 "dp": default_priority, "mp": max_priority,
                 "pt": preview_timeout_seconds, "et": execution_timeout_seconds,
                 "e": 1 if enabled else 0, "now": now, "actor": actor})
            self._audit("policy", "upsert", tool, before, self.get_policy(tool), actor)

    # --- denylist ---
    def deny(self, subject_type, subject, reason, actor):
        if subject_type not in DENY_SUBJECT_TYPES:
            raise DomainValidationError("invalid_denylist_subject_type", subject_type)
        with self._db.transaction():
            self._db.execute(
                """INSERT INTO identity_denylist (subject_type, subject, reason,
                       created_by, created_at)
                   SELECT :t, :s, :r, :actor, :now
                   WHERE NOT EXISTS (SELECT 1 FROM identity_denylist
                                     WHERE subject_type = :t AND subject = :s)""",
                {"t": subject_type, "s": subject, "r": reason,
                 "actor": actor, "now": utc_now_iso()})
            self._audit("denylist", "deny", f"{subject_type}:{subject}", None,
                        {"subject_type": subject_type, "subject": subject}, actor)

    def allow(self, subject_type, subject, actor):
        with self._db.transaction():
            self._db.execute(
                "DELETE FROM identity_denylist WHERE subject_type = :t AND subject = :s",
                {"t": subject_type, "s": subject})
            self._audit("denylist", "allow", f"{subject_type}:{subject}",
                        {"subject_type": subject_type, "subject": subject}, None, actor)

    def is_denied(self, *, requester, owner, groups):
        rows = self._db.query("SELECT subject_type, subject FROM identity_denylist")
        lowered_groups = {g.lower() for g in groups}
        for row in rows:
            subject = row["subject"]
            kind = row["subject_type"]
            if kind == "requester" and subject.lower() == requester.lower():
                return subject
            if kind == "owner" and subject.lower() == owner.lower():
                return subject
            if kind == "group" and subject.lower() in lowered_groups:
                return subject
        return None

    # --- control state ---
    def control_state(self):
        return self._db.query_one("SELECT * FROM control_state WHERE id = 1")

    def set_control_state(self, *, maintenance, drain, reason, actor):
        before = self.control_state()
        with self._db.transaction():
            self._db.execute(
                """UPDATE control_state SET maintenance = :m, drain = :d, reason = :r,
                       changed_by = :actor, changed_at = :now WHERE id = 1""",
                {"m": 1 if maintenance else 0, "d": 1 if drain else 0,
                 "r": reason, "actor": actor, "now": utc_now_iso()})
            self._audit("control_state", "set", "control_state", before,
                        self.control_state(), actor)

    # --- leases ---
    def try_acquire_lease(self, component, holder, lease_seconds,
                          now_iso: str | None = None) -> bool:
        now = now_iso or utc_now_iso()
        expires = _iso_plus(now, lease_seconds)
        with self._db.transaction():
            row = self._db.query_one(
                "SELECT holder, expires_at FROM component_leases WHERE component = :c",
                {"c": component})
            if row is None:
                self._db.execute(
                    """INSERT INTO component_leases (component, holder, expires_at)
                       VALUES (:c, :h, :e)""",
                    {"c": component, "h": holder, "e": expires})
                return True
            if row["holder"] != holder and row["expires_at"] > now:
                return False
            self._db.execute(
                """UPDATE component_leases SET holder = :h, expires_at = :e
                   WHERE component = :c""",
                {"c": component, "h": holder, "e": expires})
            return True
```

`Repositories`에 `self.control = ControlRepository(db)` 추가.

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_repo_control.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dms/repositories/ tests/test_repo_control.py
git commit -m "feat: 컨트롤 저장소 (정책, denylist, 컨트롤 상태, 컴포넌트 리스)"
```

---

### Task 12: API 골격 + 인증 (`api/app.py`, `api/auth.py`)

**Files:**
- Create: `src/dms/api/__init__.py` (빈 파일)
- Create: `src/dms/api/app.py`
- Create: `src/dms/api/auth.py`
- Test: `tests/test_api_auth.py`, `tests/conftest.py`에 앱 픽스처 추가

**Interfaces:**
- Consumes: `Settings`(Task 7), `Repositories`(Task 8-11)
- Produces:
  - `create_app(settings: Settings, db: Database) -> FastAPI` — `app.state.repos = Repositories(db)`, `app.state.settings = settings`, SessionMiddleware(secret=settings.session_secret, 쿠키명 `dms_session`), `GET /healthz` → `{"status": "ok"}`
  - `api/auth.py`:
    - `current_identity(request) -> Identity` — `Identity = namedtuple("Identity", "actor role")`. 판별 순서: ① `Authorization: Bearer <shared_token>`이 `hmac.compare_digest`로 일치하면 role=admin, actor는 `x-dms-actor` 헤더(없으면 `"shared-token"`) ② 세션 쿠키에 `username`/`role` 있으면 그것 ③ 아니면 `HTTPException(401)`
    - `require_admin` / `require_user` — FastAPI dependency. `require_user`는 user와 admin 둘 다 통과, `require_admin`은 admin만 (아니면 403)
- 이후 라우트 태스크(13-15)는 이 dependency만 사용한다

- [ ] **Step 1: conftest에 픽스처 추가**

```python
# tests/conftest.py 에 추가
from fastapi.testclient import TestClient
from dms.config import Settings


@pytest.fixture
def settings():
    return Settings(database_url="unused", shared_token="tok-shared",
                    admin_token="tok-admin", session_secret="sess-secret")


@pytest.fixture
def client(db, settings):
    from dms.api.app import create_app
    return TestClient(create_app(settings, db))
```

- [ ] **Step 2: 실패하는 테스트 작성**

```python
# tests/test_api_auth.py

def test_healthz_is_public(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_admin_route_requires_auth(client):
    # /api/admin/storages 는 Task 14에서 생기므로 여기서는 보호 확인용 임시 라우트 대신
    # 아직 없는 경로는 401/404 어느 쪽도 될 수 있다 — 인증 자체는 /api/auth/me 로 검증한다.
    assert client.get("/api/auth/me").status_code == 401


def test_shared_token_grants_admin(client):
    r = client.get("/api/auth/me", headers={
        "Authorization": "Bearer tok-shared", "x-dms-actor": "ops-debug"})
    assert r.status_code == 200
    assert r.json() == {"actor": "ops-debug", "role": "admin"}


def test_wrong_token_rejected(client):
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
```

(`/api/auth/me`는 이 태스크에서 최소 구현으로 함께 만든다 — 로그인 라우트는 Task 13.)

- [ ] **Step 3: 실패 확인**

Run: `pytest tests/test_api_auth.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 4: 구현**

```python
# src/dms/api/auth.py
import hmac
from collections import namedtuple
from fastapi import HTTPException, Request

Identity = namedtuple("Identity", "actor role")


def current_identity(request: Request) -> Identity:
    settings = request.app.state.settings
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth[len("Bearer "):]
        if hmac.compare_digest(token, settings.shared_token):
            actor = request.headers.get("x-dms-actor", "shared-token")
            return Identity(actor=actor, role="admin")
        raise HTTPException(status_code=401, detail="invalid_token")
    session = request.session
    if session.get("username") and session.get("role"):
        return Identity(actor=session["username"], role=session["role"])
    raise HTTPException(status_code=401, detail="not_authenticated")


def require_user(request: Request) -> Identity:
    return current_identity(request)


def require_admin(request: Request) -> Identity:
    identity = current_identity(request)
    if identity.role != "admin":
        raise HTTPException(status_code=403, detail="admin_required")
    return identity
```

```python
# src/dms/api/app.py
from fastapi import Depends, FastAPI
from starlette.middleware.sessions import SessionMiddleware
from ..config import Settings
from ..db import Database
from ..repositories import Repositories
from .auth import Identity, require_user


def create_app(settings: Settings, db: Database) -> FastAPI:
    app = FastAPI(title="dms")
    app.state.settings = settings
    app.state.repos = Repositories(db)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret,
                       session_cookie="dms_session")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/api/auth/me")
    def me(identity: Identity = Depends(require_user)):
        return {"actor": identity.actor, "role": identity.role}

    return app
```

- [ ] **Step 5: 통과 확인**

Run: `pytest tests/test_api_auth.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add src/dms/api/ tests/test_api_auth.py tests/conftest.py
git commit -m "feat: FastAPI 골격 + 인증 (shared token, 세션, role dependency)"
```

---

### Task 13: 계정/세션 라우트 (`api/routes_auth.py`)

**Files:**
- Create: `src/dms/api/routes_auth.py`
- Modify: `src/dms/api/app.py` (라우터 mount, 기존 `/api/auth/me`를 라우터로 이동)
- Test: `tests/test_api_auth.py`에 추가

**Interfaces:**
- Consumes: `AccountsRepository`, `require_admin`, `Settings.admin_token`
- Produces 라우트:
  - `POST /api/auth/signup` `{username, password, email?}` → 201. **더미 이메일 인증** (스펙 §3: 인터페이스만 두고 코드 검증 없이 생성). role은 무조건 `user`
  - `POST /api/auth/login` `{username, password}` → 200 `{actor, role}`, 세션 설정(로그인 전 `session.clear()`). 실패 401
  - `POST /api/auth/logout` → 세션 클리어
  - `GET /api/auth/me` (Task 12에서 이동)
  - `POST /api/admin/accounts` `{username, password}` + 헤더 `x-admin-token` == settings.admin_token (`hmac.compare_digest`) → admin 계정 생성. 토큰 불일치 403. (운영 토큰 인증 — 세션/shared token과 무관)

- [ ] **Step 1: 실패하는 테스트 작성 (test_api_auth.py에 추가)**

```python
def test_signup_login_me_logout(client):
    assert client.post("/api/auth/signup", json={
        "username": "alice", "password": "pw1", "email": "alice@corp.example"
    }).status_code == 201
    assert client.post("/api/auth/login", json={
        "username": "alice", "password": "bad"}).status_code == 401
    r = client.post("/api/auth/login", json={"username": "alice", "password": "pw1"})
    assert r.json() == {"actor": "alice", "role": "user"}
    assert client.get("/api/auth/me").json()["actor"] == "alice"
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_duplicate_signup_409(client):
    client.post("/api/auth/signup", json={"username": "dup", "password": "x"})
    r = client.post("/api/auth/signup", json={"username": "dup", "password": "x"})
    assert r.status_code == 409
    assert r.json()["detail"] == "account_exists"


def test_admin_account_creation_requires_ops_token(client):
    assert client.post("/api/admin/accounts", json={
        "username": "boss", "password": "pw"}).status_code == 403
    assert client.post("/api/admin/accounts", json={
        "username": "boss", "password": "pw"},
        headers={"x-admin-token": "tok-admin"}).status_code == 201
    r = client.post("/api/auth/login", json={"username": "boss", "password": "pw"})
    assert r.json()["role"] == "admin"
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_api_auth.py -v`
Expected: 새 테스트 3개 FAIL (404)

- [ ] **Step 3: 구현**

```python
# src/dms/api/routes_auth.py
import hmac
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DomainValidationError, ROLE_ADMIN, ROLE_USER
from .auth import Identity, require_user

router = APIRouter()


class SignupBody(BaseModel):
    username: str
    password: str
    email: str | None = None


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/api/auth/signup", status_code=201)
def signup(body: SignupBody, request: Request):
    # 사내 메일 인증은 더미: email은 기록만 하고 검증 없이 계정 생성 (스펙 §3 인증)
    try:
        request.app.state.repos.accounts.create(
            body.username, body.password, ROLE_USER, email=body.email)
    except DomainValidationError as e:
        raise HTTPException(status_code=409 if e.reason_code == "account_exists" else 422,
                            detail=e.reason_code)
    return {"username": body.username}


@router.post("/api/auth/login")
def login(body: LoginBody, request: Request):
    role = request.app.state.repos.accounts.verify(body.username, body.password)
    if role is None:
        raise HTTPException(status_code=401, detail="invalid_credentials")
    request.session.clear()
    request.session["username"] = body.username
    request.session["role"] = role
    return {"actor": body.username, "role": role}


@router.post("/api/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"status": "ok"}


@router.get("/api/auth/me")
def me(identity: Identity = Depends(require_user)):
    return {"actor": identity.actor, "role": identity.role}


@router.post("/api/admin/accounts", status_code=201)
def create_admin_account(body: LoginBody, request: Request):
    supplied = request.headers.get("x-admin-token", "")
    if not hmac.compare_digest(supplied, request.app.state.settings.admin_token):
        raise HTTPException(status_code=403, detail="admin_token_required")
    try:
        request.app.state.repos.accounts.create(body.username, body.password, ROLE_ADMIN)
    except DomainValidationError as e:
        raise HTTPException(status_code=409 if e.reason_code == "account_exists" else 422,
                            detail=e.reason_code)
    return {"username": body.username, "role": ROLE_ADMIN}
```

`app.py`: 인라인 `/api/auth/me` 제거하고 `from .routes_auth import router as auth_router` + `app.include_router(auth_router)`.

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_api_auth.py -v`
Expected: PASS (7 tests — 기존 4 + 신규 3)

- [ ] **Step 5: Commit**

```bash
git add src/dms/api/ tests/test_api_auth.py
git commit -m "feat: 계정/세션 라우트 (더미 메일 가입, 로그인, 운영토큰 관리자 생성)"
```

---

### Task 14: 스토리지 라우트 (`api/routes_storages.py`)

**Files:**
- Create: `src/dms/api/routes_storages.py`
- Modify: `src/dms/api/app.py` (라우터 mount)
- Test: `tests/test_api_storages.py`

**Interfaces:**
- Consumes: `StoragesRepository`, `require_admin`
- Produces 라우트 (전부 admin 전용):
  - `GET /api/admin/storages` → list
  - `POST /api/admin/storages` `{storage_name, mount_path, managed_root, backend_type}` → 201
  - `PUT /api/admin/storages/{name}` `{mount_path, managed_root, backend_type, enabled}` — **전체 round-trip** (부분 patch 없음) → 200
  - `DELETE /api/admin/storages/{name}` — 하드 삭제, 삭제된 행 반환
  - `GET /api/admin/audit-log?limit=50` → audit_log 최신순
  - `DomainValidationError` → 422 `{"detail": reason_code}`, `KeyError` → 404

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_api_storages.py
ADMIN = {"Authorization": "Bearer tok-shared"}
BODY = {"storage_name": "ceph-a", "mount_path": "/mnt/ceph",
        "managed_root": "/mnt/ceph/dms", "backend_type": "cephfs"}


def test_requires_admin(client):
    assert client.get("/api/admin/storages").status_code == 401
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    assert client.get("/api/admin/storages").status_code == 403


def test_crud_flow(client):
    assert client.post("/api/admin/storages", json=BODY, headers=ADMIN).status_code == 201
    assert client.post("/api/admin/storages", json={
        **BODY, "managed_root": "/elsewhere"}, headers=ADMIN).status_code == 422
    rows = client.get("/api/admin/storages", headers=ADMIN).json()
    assert rows[0]["storage_name"] == "ceph-a"
    r = client.put("/api/admin/storages/ceph-a", json={
        "mount_path": "/mnt/ceph", "managed_root": "/mnt/ceph/dms",
        "backend_type": "cephfs", "enabled": False}, headers=ADMIN)
    assert r.json()["enabled"] == 0
    assert client.delete("/api/admin/storages/ceph-a",
                         headers=ADMIN).json()["storage_name"] == "ceph-a"
    assert client.put("/api/admin/storages/ceph-a", json={
        "mount_path": "/m", "managed_root": "/m", "backend_type": "cephfs",
        "enabled": True}, headers=ADMIN).status_code == 404
    audit = client.get("/api/admin/audit-log", headers=ADMIN).json()
    assert [a["operation"] for a in audit[:3]] == ["delete", "update", "create"]
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_api_storages.py -v`
Expected: FAIL (404 응답)

- [ ] **Step 3: 구현**

```python
# src/dms/api/routes_storages.py
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DomainValidationError
from .auth import Identity, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


class StorageCreate(BaseModel):
    storage_name: str
    mount_path: str
    managed_root: str
    backend_type: str


class StorageUpdate(BaseModel):
    mount_path: str
    managed_root: str
    backend_type: str
    enabled: bool


@router.get("/api/admin/storages")
def list_storages(request: Request):
    return request.app.state.repos.storages.list()


@router.post("/api/admin/storages", status_code=201)
def create_storage(body: StorageCreate, request: Request,
                   identity: Identity = Depends(require_admin)):
    try:
        return request.app.state.repos.storages.create(
            storage_name=body.storage_name, mount_path=body.mount_path,
            managed_root=body.managed_root, backend_type=body.backend_type,
            actor=identity.actor)
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)


@router.put("/api/admin/storages/{name}")
def update_storage(name: str, body: StorageUpdate, request: Request,
                   identity: Identity = Depends(require_admin)):
    try:
        return request.app.state.repos.storages.update(
            name, mount_path=body.mount_path, managed_root=body.managed_root,
            backend_type=body.backend_type, enabled=body.enabled,
            actor=identity.actor)
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    except KeyError:
        raise HTTPException(status_code=404, detail="storage_not_found")


@router.delete("/api/admin/storages/{name}")
def delete_storage(name: str, request: Request,
                   identity: Identity = Depends(require_admin)):
    try:
        return request.app.state.repos.storages.delete(name, actor=identity.actor)
    except KeyError:
        raise HTTPException(status_code=404, detail="storage_not_found")


@router.get("/api/admin/audit-log")
def audit_log(request: Request, limit: int = 50):
    return request.app.state.repos.db.query(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT :n", {"n": limit})
```

`app.py`에 `app.include_router(storages_router)` 추가.

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_api_storages.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dms/api/ tests/test_api_storages.py
git commit -m "feat: 스토리지 관리 라우트 (CRUD, 감사 로그 조회)"
```

---

### Task 15: 잡 요청 제출/조회 라우트 (`api/routes_requests.py`)

**Files:**
- Create: `src/dms/api/routes_requests.py`
- Modify: `src/dms/api/app.py` (라우터 mount)
- Test: `tests/test_api_requests.py`

**Interfaces:**
- Consumes: `RequestsRepository`, domain 검증 함수 전부(Task 5-6), `require_user`/`require_admin`
- Produces 라우트:
  - `POST /api/user/requests` → 202 `{request_id, state}`. body:
    - scan: `{operation: "scan", storage, target, options?, priority?}`
    - sync: `{operation: "sync", source_storage, source, destination_storage, destination, options?, priority?, owner_username?}`
    - rm: `{operation: "rm", storage, target, options?, priority?, owner_username?}`
    - 검증: 경로 규칙(Task 5) + 옵션 allowlist(Task 6) + priority ∈ PRIORITIES + owner_username 정규식. 위반 → 422 `{"detail": reason_code}`. **스토리지 존재/활성 검사는 하지 않는다** — 그것은 planner 어드미션(Phase 3, 스펙 §5)의 몫. 제출은 문법 검증만 하고 `Pending`으로 영속한다.
    - resource_key는 서버가 `build_resource_key(...)`로 계산. requester_id = 인증 actor
  - `GET /api/user/requests` → 자기 요청 목록 (admin은 전체) / `GET /api/user/requests/{id}` → 상세 + `transitions` (남의 요청이면 404 — 존재 노출 방지)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_api_requests.py
ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}


def _login(client, name):
    client.post("/api/auth/signup", json={"username": name, "password": "p"})
    client.post("/api/auth/login", json={"username": name, "password": "p"})


def test_submit_scan_and_poll(client):
    _login(client, "alice")
    r = client.post("/api/user/requests", json={
        "operation": "scan", "storage": "ceph-a", "target": "team/data",
        "options": {"summary_only": True}, "priority": "high"})
    assert r.status_code == 202
    rid = r.json()["request_id"]
    detail = client.get(f"/api/user/requests/{rid}").json()
    assert detail["state"] == "Pending"
    assert detail["operation"] == "scan"
    assert detail["transitions"][0]["to_state"] == "Pending"
    assert detail["resource_key"].startswith("data.scan:ceph-a:team/data:")


def test_validation_maps_to_422(client):
    _login(client, "bob")
    cases = [
        ({"operation": "scan", "storage": "s", "target": "/abs"}, "unsafe_path"),
        ({"operation": "rm", "storage": "s", "target": "a", "options": {}},
         "rm_recursive_required"),
        ({"operation": "sync", "source_storage": "s", "source": "a",
          "destination_storage": "s", "destination": "a/b"},
         "sync_destination_inside_source"),
        ({"operation": "scan", "storage": "s", "target": "a",
          "options": {"nope": 1}}, "unknown_option"),
        ({"operation": "scan", "storage": "s", "target": "a",
          "priority": "urgent"}, "invalid_priority"),
    ]
    for body, reason in cases:
        r = client.post("/api/user/requests", json=body)
        assert r.status_code == 422 and r.json()["detail"] == reason, body


def test_isolation_between_users_and_admin_sees_all(client):
    _login(client, "alice")
    rid = client.post("/api/user/requests", json={
        "operation": "scan", "storage": "s1", "target": "a"}).json()["request_id"]
    client.post("/api/auth/logout")
    _login(client, "eve")
    assert client.get(f"/api/user/requests/{rid}").status_code == 404
    assert client.get("/api/user/requests").json() == []
    assert client.get("/api/user/requests", headers=ADMIN).json()[0]["request_id"] == rid
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_api_requests.py -v`
Expected: FAIL (404)

- [ ] **Step 3: 구현**

```python
# src/dms/api/routes_requests.py
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import (
    DomainValidationError, Operation, PRIORITIES,
    build_resource_key, option_fingerprint, validate_options,
    validate_owner_username, validate_relative_path, validate_rm_target,
    validate_sync_paths,
)
from .auth import Identity, require_user

router = APIRouter()


class RequestBody(BaseModel):
    operation: str
    storage: str | None = None
    source_storage: str | None = None
    destination_storage: str | None = None
    target: str | None = None
    source: str | None = None
    destination: str | None = None
    options: dict = {}
    priority: str = "mid"
    owner_username: str | None = None


def _validated_payload(body: RequestBody) -> tuple[dict, str]:
    op = Operation(body.operation)
    if body.priority not in PRIORITIES:
        raise DomainValidationError("invalid_priority", body.priority)
    options = validate_options(op, body.options)
    if body.owner_username is not None:
        validate_owner_username(body.owner_username)
    fp = option_fingerprint(options)
    if op is Operation.SYNC:
        src, dst = validate_sync_paths(body.source or "", body.destination or "")
        key = build_resource_key(op, source_storage=body.source_storage, source=src,
                                 destination_storage=body.destination_storage,
                                 destination=dst, fingerprint=fp)
        payload = {"source_storage": body.source_storage, "source": src,
                   "destination_storage": body.destination_storage,
                   "destination": dst}
    elif op is Operation.RM:
        target = validate_rm_target(body.target or "", options)
        key = build_resource_key(op, storage=body.storage, target=target, fingerprint=fp)
        payload = {"storage": body.storage, "target": target}
    else:
        target = validate_relative_path(body.target or "")
        key = build_resource_key(op, storage=body.storage, target=target, fingerprint=fp)
        payload = {"storage": body.storage, "target": target}
    payload.update({"options": options, "owner_username": body.owner_username})
    return payload, key


@router.post("/api/user/requests", status_code=202)
def submit(body: RequestBody, request: Request,
           identity: Identity = Depends(require_user)):
    try:
        payload, resource_key = _validated_payload(body)
    except (DomainValidationError, ValueError) as e:
        reason = getattr(e, "reason_code", "invalid_operation")
        raise HTTPException(status_code=422, detail=reason)
    rid = request.app.state.repos.requests.create(
        operation=body.operation, requester_id=identity.actor, actor=identity.actor,
        resource_key=resource_key, payload=payload, priority=body.priority)
    return {"request_id": rid, "state": "Pending"}


@router.get("/api/user/requests")
def list_requests(request: Request, identity: Identity = Depends(require_user)):
    requester = None if identity.role == "admin" else identity.actor
    return request.app.state.repos.requests.list(requester_id=requester)


@router.get("/api/user/requests/{request_id}")
def get_request(request_id: str, request: Request,
                identity: Identity = Depends(require_user)):
    repo = request.app.state.repos.requests
    row = repo.get(request_id)
    if row is None or (identity.role != "admin"
                       and row["requester_id"] != identity.actor):
        raise HTTPException(status_code=404, detail="request_not_found")
    row["transitions"] = repo.transitions(request_id)
    return row
```

`app.py`에 `app.include_router(requests_router)` 추가.

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_api_requests.py -v` 후 `pytest -q` 전체
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/api/ tests/test_api_requests.py
git commit -m "feat: 잡 요청 제출/조회 라우트 (도메인 검증, 사용자 격리)"
```

---

### Task 16: CLI (`cli.py`)

**Files:**
- Create: `src/dms/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Settings.from_env`, `Database`, `migrate`, `create_app`
- Produces:
  - `dms migrate` — `Settings.from_env(os.environ)` → DB 접속 → `migrate()` 실행, 성공 시 `migrated` 출력, 설정 문제 시 exit 2에 문제 목록 stderr 출력
  - `dms api` — uvicorn으로 `create_app` 서빙 (host/port는 Settings)
  - `main(argv: list[str] | None = None) -> int` — 테스트에서 직접 호출 가능

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_cli.py
import pytest
from dms.cli import main
from dms.db import Database


def test_migrate_command(tmp_path, monkeypatch):
    monkeypatch.setenv("DMS_DATABASE_URL", f"sqlite:///{tmp_path}/cli.db")
    monkeypatch.setenv("DMS_SHARED_TOKEN", "t")
    monkeypatch.setenv("DMS_ADMIN_TOKEN", "a")
    monkeypatch.setenv("DMS_SESSION_SECRET", "s")
    assert main(["migrate"]) == 0
    db = Database.connect(f"sqlite:///{tmp_path}/cli.db")
    assert db.query_one("SELECT version FROM schema_migrations")["version"] == "0001-initial"


def test_migrate_fails_closed_on_bad_settings(monkeypatch, capsys):
    monkeypatch.delenv("DMS_DATABASE_URL", raising=False)
    monkeypatch.setenv("DMS_SHARED_TOKEN", "CHANGE_ME")
    monkeypatch.setenv("DMS_ADMIN_TOKEN", "a")
    monkeypatch.setenv("DMS_SESSION_SECRET", "s")
    assert main(["migrate"]) == 2
    err = capsys.readouterr().err
    assert "DMS_DATABASE_URL" in err and "DMS_SHARED_TOKEN" in err


def test_unknown_command(capsys):
    with pytest.raises(SystemExit):
        main(["frobnicate"])
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 구현**

```python
# src/dms/cli.py
import argparse
import os
import sys
from .config import Settings, SettingsError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="dms")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply database schema")
    sub.add_parser("api", help="run the API server")
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env(os.environ)
    except SettingsError as e:
        for problem in e.problems:
            print(f"settings error: {problem}", file=sys.stderr)
        return 2

    from .db import Database
    db = Database.connect(settings.database_url)

    if args.command == "migrate":
        from .migrations import migrate
        migrate(db)
        print("migrated")
        return 0

    if args.command == "api":
        import uvicorn
        from .api.app import create_app
        uvicorn.run(create_app(settings, db), host=settings.api_host,
                    port=settings.api_port)
        return 0

    return 2
```

- [ ] **Step 4: 통과 확인**

Run: `pytest -q` (전체 스위트)
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/cli.py tests/test_cli.py
git commit -m "feat: CLI (dms migrate / dms api)"
```

---

## Phase 1 완료 기준

- `pytest -q` 전체 통과 (서비스 없이 SQLite만으로).
- `DMS_*` env 4개를 채우고 `dms migrate && dms api` 하면 실서버가 뜨고, `/api/auth/signup → login → POST /api/user/requests → GET /api/user/requests/{id}` 플로우가 동작한다 (요청은 `Pending`에 머문다 — planner는 Phase 3).
- Phase 2 플랜 작성으로 이어진다 (controller 루프 숙주 + 에이전트 + storage-reconciler).
