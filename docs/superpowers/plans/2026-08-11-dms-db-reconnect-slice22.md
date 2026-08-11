# 슬라이스 22 — DB 커넥션 재연결 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 2026-08-11 라이브 사건(API 파드 90분 `0/1 Running` 방치 — DB 커넥션 객체만 죽고 TCP 는 정상, 재연결 부재로 전 요청 영구 실패, `/healthz` 가 DB 를 안 봐서 kubelet 무반응)의 재발을 구조로 막는다. (1) `Database` 에 방언별 죽음 판정(postgresql 은 psycopg 예외 클래스 + `closed` **이중 게이트**, sqlite 는 재연결 없음)과 `_run()` 단일 지점의 **정확히 1회, 백오프 없는** 재시도를 넣는다. (2) 트랜잭션 경계: BEGIN(yield 전) 죽음만 재시도 허용, yield 이후는 전면 금지 — 죽은 커넥션에 ROLLBACK 을 또 날려 원 예외를 가리는 현행 버그(`db.py:77`)를 함께 고친다. (3) 방치 탈출: `/readyz` 연속 `DMS_READYZ_EXIT_FAILURES`(기본 30 ≈ 5분, 0=비활성)회 실패 시 SIGTERM 자기 종료. (4) 관측: stderr 로그·readyz 200 본문 `reconnects` 카운터·events `db_reconnected` 영속 1건 — 조용한 재연결 금지. 컨트롤러는 BEGIN 재시도로 **무크래시 같은-틱 복구**가 되고, 기존 crash-restart 는 규약(주석)으로 승격된 안전망으로 남는다.

**Architecture:** 아래에서 위로 쌓는다. (1) `db.py` — `connect()` 가 URL 을 `_url` 로 보관(현행은 버려서 재연결 자체가 불가능), 커넥션 생성을 `_open()` 정적 메서드로 분리(connect/재연결이 같은 분기 — 갈라지면 재연결된 커넥션만 row_factory/PRAGMA 를 잃는다), `execute`/`query` 를 `_run()` 한 곳으로 모아 죽음 판정→`_reconnect()`→1회 재시도. `_txn_depth` 카운터가 트랜잭션 안 재시도를 봉쇄한다. (2) 같은 파일의 `transaction()` 경계 재작성(BEGIN 은 `_run` 경유로 재시도 획득, 실패 경로는 죽음이면 ROLLBACK 생략+재연결+원 예외 re-raise, COMMIT 죽음은 재시도 금물 — 유실을 성공으로 위장한다). (3) `wiring.wire_reconnect_event` 훅을 api(`create_app`)/controller(`cli.py`) 양쪽에 배선해 events 영속 흔적. (4) `config.py`+`app.py`+`20-config.yaml` — readyz 본문 카운터와 연속 실패 자기 종료(`exit_fn` 주입으로 테스트). (5) `controller.py` 리스 획득이 per-loop try **밖**인 것을 의도적 crash-restart 경로로 주석 승격 + 무크래시/전파 양쪽 테스트. 화면은 **무변경**(신설 사유 코드 0, 실패 표면 불변 — 설계 §3).

**Tech Stack:** Python 3.11 표준 라이브러리(`sqlite3`·`threading`·`signal`·`os`·`sys`·`time`), psycopg 는 기존 optional dependency 의 lazy import 그대로(새 의존성 0). **venv 에 psycopg 가 없음을 실측**(`ModuleNotFoundError`) — 테스트는 psycopg 예외 계층의 페이크 모듈을 `sys.modules` 에 주입한다(`test_migrations.py` `_FakeDb` 선례의 확장). DB 스키마 무변경, 프론트 무변경.

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-08-11-dms-db-reconnect-slice22-design.md`. 플랜과 충돌하면 **설계가 이긴다**.
- **새 pip/npm 의존성 금지.** 실 PG 테스트 하니스는 이 저장소에 없다(`tests/test_migrations.py` `_FakeDb` 선례) — psycopg 죽음은 페이크 모듈 + 페이크 커넥션으로, sqlite 죽음은 실 DB 의 `conn.close()` 후 사용으로 재현한다(Task 1).
- **sqlite 경로는 예외 처리 포함 완전 무변경이 계약이다**(설계 §2.1/§4) — 죽음 판정이 sqlite 방언에서 항상 False 라 재연결 코드가 영원히 안 탄다. 로컬·CI 전체가 이 위에 있다.
- **DB 스키마 무변경, 신설 사유 코드 0** — `frontend/src/lib/reasonCodes.json`/`api.ts` 를 건드리지 않는다(계약 테스트가 무변경으로 초록이어야 한다).
- **커밋은 pathspec 으로 한정한다**: 신규 파일만 `git add <파일>` 선행 후, 항상 `git commit -m "..." -- <경로들>` 형태로 커밋한다. `git add -A`·`git add .`·`git commit -a` **금지** — 워크트리 공유 중 인덱스 섞임 사고가 있었다.
- **origin 으로 push 금지, 브랜치 변경 금지, `deploy/k8s` 의 이미지 태그 변경 금지**(배포는 호출자가 한다). `docs/` 아래는 이 플랜 파일 외 생성·수정 금지.
- `legacy/` 아래는 읽기 전용 — 수정·이동·삭제·추가 금지, import 금지, 코드 복사 금지.
- 백엔드 테스트 명령(이 워크트리 전용, `.venv` 는 워크트리 밖 공용):
  `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest <대상> -q`
  전체 스위트는 **포그라운드**로 Bash timeout 900000ms. **기준선 1137 passed(2026-08-11 실측, 372s).**
- 프론트(이 슬라이스 코드 무변경 — 기준선 유지 확인만): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run`(**기준선 228 passed / 49 files**), 타입체크 `npx tsc -b`. node_modules 존재 실측 — `npm ci` 불필요(실행이 깨질 때만 `npm ci --prefer-offline --no-audit --no-fund`).
- 주석은 **한국어**로 「왜」를 적는다.

## 실측 고정값 (코드 직접 확인)

| 항목 | 값 |
|---|---|
| Database 현행 | 단일 커넥션 + RLock(`src/dms/db.py:31-34`), `connect()` 가 URL 미보관(`:36-50`) — **지금 구조로는 재연결 자체가 불가능**. `execute`/`query`/`query_one`(`:57-68`)에 예외 처리·재시도 0건. sqlite `isolation_level=None`(`:42`)·pg `autocommit=True`(`:48`) — `transaction()` 밖은 문장 단위 autocommit(재시도 단위가 문장 1개) |
| transaction 현행 버그 | BEGIN/COMMIT/ROLLBACK 수동(`db.py:70-80`), 실패 경로가 죽은 커넥션에 ROLLBACK 재송(`:77`) — 커넥션 사망 시 새 OperationalError 가 **원 예외를 가린다**(설계 §1-2) |
| psycopg 예외 계층 | 3.3.4 실증(설계 §1-3): Operational/Programming ⊂ DatabaseError, InterfaceError 는 Error 직속. **DiskFull·ConnectionTimeout·AdminShutdown 전부 OperationalError 하위** — 클래스만 보면 디스크 가득참까지 재시도. 죽음 판정엔 `Connection.closed`(pgconn BAD) 교집합이 필요 |
| sqlite 예외 계층 | 3.45.1 실증(설계 §1-4): 문법 오류·no-such-table 둘 다 `OperationalError`(죽음 신호 불가), 닫힌 커넥션 사용은 `ProgrammingError`("Cannot operate on a closed database"), `closed` 속성 없음 |
| **venv 에 psycopg 없음** | `/home/mason/dms-dev/dms/.venv/bin/python -c "import psycopg"` → `ModuleNotFoundError`(2026-08-11 실측) — 죽음 판정 테스트는 `sys.modules["psycopg"]` 페이크 주입이 **필수**다(선택이 아니다) |
| readyz/healthz | readyz 가 `SELECT 1` 실패 시 503(`src/dms/api/app.py:51-60`), healthz 는 무조건 200(`:47-49`). 프로브: readiness `/readyz` 10s·liveness `/healthz` 30s(`deploy/k8s/40-api.yaml:101-110`), failureThreshold 기본 3. api replicas 1. `tests/test_api_auth.py:5-8` 이 readyz 본문 `{"status":"ok"}` 완전일치 단언 — **Task 4 가 이 단언을 바꾼다** |
| 컨트롤러 생존 기전 | `run_all_once` 의 `try_acquire_lease`(`src/dms/controller.py:103-105`)가 per-loop try(`:109-115`) **밖**, `run_forever` while 에도 예외 처리 없음(`:126-133`). 리스가 `transaction()` 을 연다(`src/dms/repositories/control.py:149`) — BEGIN 죽음 = 프로세스 크래시 = 재시작이 새 커넥션(우연히 올바른 crash-restart, 설계 §1-6). 컨트롤러엔 프로브 전무(`41-controller.yaml` — HTTP 서버 없음) |
| cli 배선 | `Database.connect(settings.database_url)`(`src/dms/cli.py:38`), controller 경로 `repos = Repositories(db)`(`:58`), api 경로는 `create_app(settings, db)`(`:49`) |
| record_event 계약 | 절대 예외를 올리지 않는다(`src/dms/repositories/observability.py:16-29`, 자체 try/except + 트랜잭션 밖 단독 INSERT). `Repositories(db).observability`(`src/dms/repositories/__init__.py:29`). events 테이블은 기존 스키마(`migrations.py:342`)·기존 retention(controller `_retention_step`, `controller.py:51-55`) 그대로 |
| 설정 관례 | `_SERVER_INT_KEYS` 튜플(`config.py:9-50`)에 넣으면 `from_env` 의 `**extra`(`:160-161,171`)가 배선 — 필드 추가 + 튜플 추가 두 곳이면 끝. `tests/test_config.py` 의 `VALID` dict 관례(`:4-9`), 선례 `test_build_preflight_timeout_default_and_env_override`(`:62-67`) |
| 20-config.yaml | 마지막 데이터 키가 `DMS_ROLLOUT_TIMEOUT_SECONDS: "600"`(`:127`) — 새 키는 그 뒤에 새 절로 붙인다 |
| 테스트 관례 | conftest `db`(sqlite+migrate)/`settings`/`client`(`tests/conftest.py:8-24`). `_FakeDb` 대역 선례(`tests/test_migrations.py:32-56`). 컨트롤러 테스트는 `run_all_once`/`Loop` 직접 호출(`tests/test_controller.py:77-101`) |
| 이중 적용 창의 실측 한정 | 트랜잭션 밖 단독 문장은 idempotent UPDATE·진단 INSERT 위주(설계 §1-7, `observability.py:16-29` 등), 업무 INSERT 는 `transaction()` 안(28곳) — §2.3 이 재시도 금지 |
| 기준선 | 백엔드 **1137 passed**(실측), 프론트 **228 passed / 49 files**(무변경 유지 대상) |

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/dms/db.py` (수정) | Task 1: `_url` 보관·`_open` 분리·`_run`(이중 게이트 죽음 판정 + 1회 재시도)·`_reconnect`(카운터·stderr·훅)·`_txn_depth`. Task 2: `transaction()` 경계(BEGIN 재시도·죽음 시 ROLLBACK 생략·COMMIT 비재시도) |
| `tests/test_db_reconnect.py` (신규) | 페이크 psycopg 모듈 + 페이크 커넥션 대역, 단독 문장/트랜잭션 경계/sqlite 무변경/훅 전 분기 |
| `src/dms/wiring.py` (수정) | `wire_reconnect_event(db, repos)` — events `db_reconnected` 훅(api/controller 공용) |
| `src/dms/api/app.py` (수정) | Task 3: 훅 배선. Task 4: `create_app(..., exit_fn=None)`·readyz 200 본문 카운터·연속 실패 자기 종료 |
| `src/dms/cli.py` (수정) | controller 경로 훅 배선 |
| `src/dms/config.py`, `deploy/k8s/20-config.yaml` (수정) | `DMS_READYZ_EXIT_FAILURES`(기본 30, 0=비활성) 양쪽 |
| `src/dms/controller.py` (수정) | 리스 획득 crash-restart 규약 승격 **주석**(코드 동작 무변경) |
| `tests/test_api_auth.py`, `tests/test_api_readyz.py`(신규), `tests/test_config.py`, `tests/test_cli.py`, `tests/test_controller.py` (수정/신규) | 각 계층 계약 |

---

### Task 0: 기준선 확인 (커밋 없음)

**Files:** 없음(검증만)

**Interfaces:** 없음 — 이후 모든 태스크의 판정 기준(기준선 초록)만 만든다.

- [ ] **Step 1: 백엔드 기준선**

Run(포그라운드, Bash timeout 900000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests -q`
Expected: `1137 passed`

- [ ] **Step 2: 프론트 기준선 (이 슬라이스는 프론트 무변경 — 마감 때 같은 수치를 다시 확인한다)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b`
Expected: `Test Files  49 passed`, `Tests  228 passed`, tsc 무출력 exit 0. (node_modules 존재 실측 — 실행이 깨질 때만 `npm ci --prefer-offline --no-audit --no-fund` 후 재시도.)

---

### Task 1: Database 단독 문장 재연결 — 이중 게이트 판정 + `_run` 1회 재시도

**Files:**
- Modify: `src/dms/db.py`
- Create: `tests/test_db_reconnect.py`

**Interfaces:**
- Consumes: 기존 `Database.connect` 호출부 전체(시그니처 무변경), psycopg lazy import 관례(`db.py:46`).
- Produces (Task 2~5 가 이 이름·모양을 그대로 쓴다):
  - `Database._url: str | None` — `connect()` 만 채운다. 직접 생성(테스트 더블 관례)은 None = 죽음 처리 자체를 하지 않는다.
  - `Database._open(url) -> (conn, dialect)` — **staticmethod**. connect/재연결 공용 분기.
  - `Database._connection_is_dead(exc) -> bool` — postgresql 전용 이중 게이트(§2.1). sqlite 는 항상 False.
  - `Database._run(sql, params) -> cursor` — execute/query 단일 실행 지점, 죽음이면 `_reconnect` 후 같은 문장 정확히 1회 재시도(§2.2). `_txn_depth > 0` 이면 즉시 전파.
  - `Database._reconnect(cause)` — 구 커넥션 close(실패 무시) → `_open` 재수행. 실패는 **원 예외(cause)를** chain 해 전파(§4).
  - `Database.reconnect_count: int`, `last_reconnect_at: str | None` — §2.6 관측(readyz 본문이 읽는다).
  - `Database.on_reconnect: Callable | None` — 재연결 성공 직후 1회 훅(Task 3 이 record_event 를 단다). 훅 예외는 무해, 재귀는 1단 차단.
  - `Database._txn_depth: int` — 이 태스크는 계수만 유지(트랜잭션 의미론은 Task 2).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_db_reconnect.py` (신규 파일 전체):

```python
"""슬라이스 22: Database 재연결(설계 §2.1/§2.2).

실 PG 하니스가 없는 저장소다(tests/test_migrations.py 의 _FakeDb 선례). 게다가
venv 에 psycopg 자체가 없다(ModuleNotFoundError 실측) -- 죽음 판정의 isinstance
게이트를 통과시키려면 psycopg 예외 계층의 페이크 **모듈**을 sys.modules 에
주입해 db.py 의 lazy `import psycopg` 를 가로채야 한다(선택이 아니라 필수).
커넥션 대역은 "전송 실패 + closed=True"(pgconn status BAD)를 스크립트한다 --
이번 사건 계열(이미 죽은 커넥션에 전송 시도 -> 즉시 실패)의 최소 재현이다.
sqlite 는 실 DB 로 "재연결이 **없는지**"를 고정한다(§2.1: OperationalError 가
문법 오류를 포함해 죽음 신호로 쓸 수 없고, in-process 라 죽음 모드도 없다)."""
import sqlite3
import sys
import types

import pytest

from dms.db import Database


# psycopg 3 예외 계층의 최소 대역(3.3.4 실증 §1-3: Operational/Programming 은
# DatabaseError 하위, InterfaceError 는 Error 직속). DiskFull 도 OperationalError
# 하위라는 함정이 "클래스 + closed 이중 게이트"의 존재 이유다.
class FakePsycopgError(Exception):
    pass


class FakeInterfaceError(FakePsycopgError):
    pass


class FakeDatabaseError(FakePsycopgError):
    pass


class FakeOperationalError(FakeDatabaseError):
    pass


class FakeProgrammingError(FakeDatabaseError):
    pass


def _fake_psycopg_module():
    mod = types.ModuleType("psycopg")
    mod.Error = FakePsycopgError
    mod.InterfaceError = FakeInterfaceError
    mod.DatabaseError = FakeDatabaseError
    mod.OperationalError = FakeOperationalError
    mod.ProgrammingError = FakeProgrammingError
    return mod


class _Cursor:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def fetchall(self):
        return self._rows


class _FakePgConn:
    """psycopg Connection 대역. fail_on 의 부분문자열을 담은 SQL 을 만나면 그
    항목을 소진하고 예외를 던진 뒤 closed=True 로 넘어간다(mark_closed=False 면
    DiskFull 처럼 "살아있는 커넥션의 서버 오류"가 된다). executed 가 재시도
    횟수의 증거, raised 가 원 예외 보존(identity) 검증의 증거다."""

    def __init__(self, fail_on=(), exc=None, mark_closed=True, rows=()):
        self.executed = []
        self.raised = []
        self.closed = False
        self.close_calls = 0
        self.fail_on = list(fail_on)
        self._exc = exc or (lambda: FakeOperationalError("connection lost"))
        self._mark_closed = mark_closed
        self._rows = rows

    def execute(self, sql, params=None):
        self.executed.append(sql)
        for i, mark in enumerate(self.fail_on):
            if mark in sql:
                del self.fail_on[i]
                if self._mark_closed:
                    self.closed = True
                error = self._exc()
                self.raised.append(error)
                raise error
        return _Cursor(self._rows)

    def close(self):
        self.close_calls += 1
        self.closed = True


def _pg_db(monkeypatch, conn, next_conns=()):
    """직접 생성 + _url 부여 + _open 을 대역 팩토리로 치환한 postgresql Database.
    재연결이 일어나면 next_conns 에서 차례로 새 커넥션을 꺼내 쓴다."""
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg_module())
    db = Database(conn, "postgresql")
    db._url = "postgresql://test-only"
    pool = iter(next_conns)
    monkeypatch.setattr(Database, "_open",
                        staticmethod(lambda url: (next(pool), "postgresql")))
    return db


# ---- §2.2 단독 문장: 재연결 + 정확히 1회 재시도 ----

def test_connect_stores_the_url_for_reconnection(tmp_path):
    # 현행 connect() 는 URL 을 버려서 재연결 자체가 불가능했다(§1-1) -- 보관이
    # 이 슬라이스 전체의 선행 조건이다.
    url = f"sqlite:///{tmp_path}/t.db"
    assert Database.connect(url)._url == url


def test_dead_connection_standalone_execute_reconnects_and_retries_once(monkeypatch):
    old = _FakePgConn(fail_on=["INSERT"])
    new = _FakePgConn()
    db = _pg_db(monkeypatch, old, [new])
    db.execute("INSERT INTO t (a) VALUES (:a)", {"a": 1})
    assert old.executed == ["INSERT INTO t (a) VALUES (%(a)s)"]   # 원 시도 1회
    assert new.executed == ["INSERT INTO t (a) VALUES (%(a)s)"]   # 같은 문장 재시도 1회
    assert old.close_calls == 1          # 구 커넥션은 close 시도된다
    assert db.reconnect_count == 1
    assert db.last_reconnect_at is not None


def test_query_reconnects_through_the_same_single_helper(monkeypatch):
    # execute/query 가 _run 한 곳으로 모였는지(§2.2) -- 한쪽만 재연결되면 다른
    # 쪽이 사건을 재현한다.
    old = _FakePgConn(fail_on=["SELECT"])
    new = _FakePgConn(rows=[{"x": 1}])
    db = _pg_db(monkeypatch, old, [new])
    assert db.query("SELECT 1 AS x") == [{"x": 1}]
    assert db.reconnect_count == 1


def test_interface_error_with_closed_connection_also_counts_as_death(monkeypatch):
    # InterfaceError 는 Error 직속(§1-3) -- Operational 만 잡으면 이 절반을 놓친다.
    old = _FakePgConn(fail_on=["SELECT"],
                      exc=lambda: FakeInterfaceError("connection is closed"))
    new = _FakePgConn(rows=[{"x": 1}])
    db = _pg_db(monkeypatch, old, [new])
    assert db.query("SELECT 1 AS x") == [{"x": 1}]
    assert db.reconnect_count == 1


def test_retry_failure_propagates_and_stops_at_exactly_two_attempts(monkeypatch):
    # 재시도 1회·백오프 없음(§2.2): RLock 이 전 스레드를 직렬화하므로 락 안
    # 대기는 API 전체 정지다. 지속 장애는 readyz 503 + 자기 종료(§2.4)의 몫이다.
    old = _FakePgConn(fail_on=["SELECT"])
    new = _FakePgConn(fail_on=["SELECT"])   # 재연결 후에도 죽는 지속 장애
    db = _pg_db(monkeypatch, old, [new])
    with pytest.raises(FakeOperationalError):
        db.query("SELECT 1 AS x")
    assert len(old.executed) == 1 and len(new.executed) == 1   # 1+1, 루프 없음
    assert db.reconnect_count == 1   # 재연결은 성공했고, 재시도 실패가 그대로 전파됐다


def test_operational_error_on_a_live_connection_is_not_retried(monkeypatch):
    # DiskFull·ConnectionTimeout·AdminShutdown 은 전부 OperationalError 하위다
    # (§1-3) -- closed 게이트가 없으면 디스크 가득참까지 재시도한다.
    conn = _FakePgConn(fail_on=["INSERT"], mark_closed=False)
    db = _pg_db(monkeypatch, conn, [])
    with pytest.raises(FakeOperationalError):
        db.execute("INSERT INTO t (a) VALUES (1)")
    assert len(conn.executed) == 1
    assert db.reconnect_count == 0


def test_programming_error_is_not_retried_even_when_closed(monkeypatch):
    # 문법 오류는 ProgrammingError 계열(§1-3) -- 클래스 게이트가 거른다. closed
    # 여도 마찬가지: 죽음 판정은 두 게이트의 교집합이다.
    conn = _FakePgConn(fail_on=["SELEC"],
                       exc=lambda: FakeProgrammingError("syntax error"))
    db = _pg_db(monkeypatch, conn, [])
    with pytest.raises(FakeProgrammingError):
        db.execute("SELEC 1")
    assert db.reconnect_count == 0


def test_directly_constructed_database_never_reconnects(monkeypatch):
    # connect() 를 거치지 않은 인스턴스(_url=None, 테스트 더블 관례)는 재연결
    # 재료가 없다 -- 죽음 처리 없이 원 예외 전파가 계약이다.
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg_module())
    conn = _FakePgConn(fail_on=["SELECT"])
    db = Database(conn, "postgresql")
    with pytest.raises(FakeOperationalError):
        db.query("SELECT 1")
    assert db.reconnect_count == 0


def test_reconnect_failure_propagates_the_original_exception_chained(monkeypatch):
    # §4: DB 가 완전히 죽어 재연결도 안 되면 -- 원 예외가 주인공이고 재연결
    # 실패는 __cause__ 로만 남는다. readyz 는 어느 쪽이든 503 으로 정직하다.
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg_module())
    conn = _FakePgConn(fail_on=["SELECT"])
    db = Database(conn, "postgresql")
    db._url = "postgresql://test-only"

    def down(url):
        raise RuntimeError("pg still down")

    monkeypatch.setattr(Database, "_open", staticmethod(down))
    with pytest.raises(FakeOperationalError) as e:
        db.query("SELECT 1")
    assert e.value is conn.raised[-1]                     # 원 예외 보존
    assert isinstance(e.value.__cause__, RuntimeError)    # 재연결 실패는 chain 으로
    assert db.reconnect_count == 0


def test_reconnect_leaves_a_stderr_line(monkeypatch, capsys):
    # §2.6: 조용한 재연결 금지 -- "DB 가 자주 끊긴다"는 상류 문제가 숨는다.
    # kubectl logs 가 1차 창구다.
    old = _FakePgConn(fail_on=["SELECT"])
    db = _pg_db(monkeypatch, old, [_FakePgConn()])
    db.query("SELECT 1 AS x")
    err = capsys.readouterr().err
    assert "db reconnected dialect=postgresql cause=FakeOperationalError" in err
    assert "count=1" in err


def test_on_reconnect_hook_fires_once_and_hook_errors_are_harmless(monkeypatch):
    # §2.6 훅(Task 3 이 record_event 를 단다). record_event 는 예외를 안 올리는
    # 계약이지만, 훅이 어긴다 해도 재연결 성공을 뒤집으면 안 된다(관측 < 복구).
    old = _FakePgConn(fail_on=["INSERT"])
    db = _pg_db(monkeypatch, old, [_FakePgConn()])
    calls = []

    def hook():
        calls.append(1)
        raise RuntimeError("hook boom")

    db.on_reconnect = hook
    db.execute("INSERT INTO t (a) VALUES (1)")
    assert calls == [1]
    assert db.reconnect_count == 1


# ---- §2.1 sqlite: 재연결 없음(완전 무변경)이 계약 ----

def test_sqlite_closed_connection_error_propagates_without_reconnect(tmp_path):
    # sqlite 죽음 재현 경로: conn.close() 후 사용 -> ProgrammingError("Cannot
    # operate on a closed database", 3.45.1 실증 §1-4). 이는 코드 버그다 --
    # 재연결로 숨기지 않고 전파한다.
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    db._conn.close()
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute("SELECT 1")
    assert db.reconnect_count == 0


def test_sqlite_syntax_error_propagates_without_reconnect(tmp_path):
    # sqlite3.OperationalError 는 문법 오류를 포함한다(§1-4) -- 죽음 신호로 쓸
    # 수 없다. 재시도 없이 그대로 전파가 계약이다.
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    with pytest.raises(sqlite3.OperationalError):
        db.execute("SELEC 1")
    assert db.reconnect_count == 0
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_db_reconnect.py -q`
Expected: 13건 전부 FAIL/ERROR — `_pg_db` 를 쓰는 테스트는 `AttributeError: <class 'dms.db.Database'> has no attribute '_open'`(monkeypatch 는 없는 속성을 못 바꾼다), `test_connect_stores_the_url_...` 는 `AttributeError: 'Database' object has no attribute '_url'`, sqlite 2건과 `test_directly_constructed_...` 는 예외 자체는 전파되나 `assert db.reconnect_count == 0` 에서 `AttributeError: 'Database' object has no attribute 'reconnect_count'`.

- [ ] **Step 3: db.py 를 고친다**

**(1)** 파일 머리 import 블록(`import json` ~ `from datetime import ...`)을 다음으로 교체:

```python
import json
import re
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
```

**(2)** `class Database:` 전체(`__init__` 부터 `transaction` 끝까지)를 다음으로 교체 (`dump_json`/`load_json` 은 그대로 둔다):

```python
class Database:
    def __init__(self, conn, dialect: str):
        self._conn = conn
        self.dialect = dialect
        self._lock = threading.RLock()
        # 재연결(슬라이스 22)의 선행 조건 -- connect() 만 채운다. 직접 생성된
        # 인스턴스(테스트 더블 관례)는 None = 죽음 처리 자체를 하지 않는다.
        self._url: str | None = None
        # 트랜잭션 깊이. > 0 이면 _run 은 재연결도 재시도도 하지 않는다(§2.3):
        # 트랜잭션 중간 재연결은 서버가 커넥션 소멸과 함께 폐기한 앞 문장들 위에
        # 뒷문장만 새 커넥션에 다시 적용하는 것 -- 부분 적용을 "만들어내는" 동작이다.
        self._txn_depth = 0
        # 관측(§2.6): /readyz 200 본문과 stderr 로그가 읽는다.
        self.reconnect_count = 0
        self.last_reconnect_at: str | None = None
        # 재연결 성공 직후 1회 불리는 훅 -- wiring 이 record_event 를 단다(§2.6).
        self.on_reconnect = None
        self._in_reconnect_hook = False

    @classmethod
    def connect(cls, url: str) -> "Database":
        conn, dialect = cls._open(url)
        db = cls(conn, dialect)
        db._url = url  # 재연결의 유일한 재료 -- 기존 코드는 URL 을 버렸다(§1-1)
        return db

    @staticmethod
    def _open(url: str):
        """URL -> (conn, dialect). connect() 와 _reconnect() 가 **같은 분기**를
        쓴다 -- 갈라지면 재연결된 커넥션만 row_factory/PRAGMA 를 잃는다."""
        if url.startswith("sqlite:///"):
            path = url[len("sqlite:///"):]
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.isolation_level = None  # 명시적 트랜잭션 제어
            conn.execute("PRAGMA foreign_keys = ON")
            return conn, "sqlite"
        if url.startswith("postgresql://") or url.startswith("postgres://"):
            import psycopg
            from psycopg.rows import dict_row
            return (psycopg.connect(url, row_factory=dict_row, autocommit=True),
                    "postgresql")
        raise ValueError(f"unsupported database url: {url}")

    def _adapt(self, sql: str) -> str:
        if self.dialect == "postgresql":
            return _NAMED.sub(r"%(\1)s", sql)
        return sql

    def _connection_is_dead(self, exc) -> bool:
        """죽음 판정(§2.1) -- postgresql 은 예외 클래스 + closed **이중 게이트**.

        클래스만 보면 DiskFull·ConnectionTimeout·AdminShutdown(전부
        OperationalError 하위, psycopg 3.3.4 실증)까지 재시도하고, closed 만
        보면 잡을 예외 범위가 없다 -- 교집합이 정확하다. 문법 오류는
        ProgrammingError 계열이라 첫 게이트에서 걸러진다. sqlite 는
        OperationalError 가 문법 오류·no-such-table 을 포함하고(3.45.1 실증)
        in-process 라 죽음 모드 자체가 없다 -- 항상 False(기존 동작 완전 무변경).
        _url 이 없으면(직접 생성) 재연결 재료가 없다 -- 역시 False."""
        if self.dialect != "postgresql" or self._url is None:
            return False
        import psycopg  # 게이트 통과 시점에만 필요 -- sqlite 경로는 영원히 안 든다
        if not isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
            return False
        return bool(getattr(self._conn, "closed", False))

    def _run(self, sql: str, params: dict | None):
        """execute/query 의 단일 실행 지점(§2.2). 죽음이면 재연결 후 같은 문장을
        **정확히 1회** 재시도한다(백오프 없음): RLock 이 전 스레드를 직렬화하므로
        락 안 대기는 API 전체 정지다. 지속 장애는 재시도로 이길 문제가 아니라
        readyz 503 과 자기 종료(§2.4)의 몫이다. 트랜잭션 안(_txn_depth > 0)은
        재시도 금지(§2.3)."""
        with self._lock:
            adapted = self._adapt(sql)
            try:
                return self._conn.execute(adapted, params or {})
            except Exception as exc:
                if self._txn_depth > 0 or not self._connection_is_dead(exc):
                    raise
                self._reconnect(cause=exc)
                return self._conn.execute(adapted, params or {})

    def _reconnect(self, cause) -> None:
        """구 커넥션 close(실패 무시) 후 _open 재수행. 실패는 재연결 예외가 아니라
        **원 예외(cause)를** chain 해 전파한다(§4) -- 원 오류가 가려지면 진단이
        흐려지고, readyz 는 어느 쪽이든 503 으로 정직하다."""
        started = time.monotonic()
        try:
            self._conn.close()
        except Exception:
            pass  # 이미 죽은 커넥션 -- close 실패에 정보가 없다
        try:
            self._conn = type(self)._open(self._url)[0]
        except Exception as exc:
            print(f"db reconnect failed dialect={self.dialect} "
                  f"cause={type(cause).__name__}: {exc}", file=sys.stderr)
            raise cause from exc
        self.reconnect_count += 1
        self.last_reconnect_at = utc_now_iso()
        # 조용한 재연결 금지(§2.6): 재연결이 조용하면 "DB 가 자주 끊긴다"는
        # 상류 문제가 숨는다. kubectl logs 가 1차 창구다.
        print(f"db reconnected dialect={self.dialect} "
              f"cause={type(cause).__name__} "
              f"elapsed_ms={int((time.monotonic() - started) * 1000)} "
              f"count={self.reconnect_count}", file=sys.stderr)
        if self.on_reconnect is not None and not self._in_reconnect_hook:
            self._in_reconnect_hook = True  # 훅의 INSERT 중 또 죽어도 연쇄는 1단
            try:
                self.on_reconnect()
            except Exception as exc:  # 훅은 관측이다 -- 재연결 성공을 못 뒤집는다
                print(f"db reconnect hook failed: {exc}", file=sys.stderr)
            finally:
                self._in_reconnect_hook = False

    def execute(self, sql: str, params: dict | None = None) -> None:
        self._run(sql, params)

    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        with self._lock:  # fetchall 까지 같은 락 -- 커서는 커넥션과 한 몸이다
            cur = self._run(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def query_one(self, sql: str, params: dict | None = None) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def transaction(self):
        # 경계 의미론(BEGIN 재시도·죽음 시 ROLLBACK 생략)은 Task 2 -- 여기서는
        # 깊이 계수만 유지해 "트랜잭션 안 문장은 재시도 금지"(§2.3)를 먼저 세운다.
        with self._lock:
            self._conn.execute("BEGIN")
            self._txn_depth += 1
            try:
                yield self
            except BaseException:
                self._txn_depth -= 1
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._txn_depth -= 1
                self._conn.execute("COMMIT")
```

- [ ] **Step 4: 통과를 확인한다 (기존 DB 계층 회귀 포함)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_db_reconnect.py tests/test_db.py tests/test_migrations.py tests/test_events_outside_transaction.py tests/test_repo_control.py -q`
Expected: 전부 PASS (sqlite 경로·트랜잭션 의미론·`_TxTrackingDB` 스파이·리스 트랜잭션이 무변경이어야 한다)

- [ ] **Step 5: 커밋 (pathspec 관례)**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git add tests/test_db_reconnect.py
git commit -m "feat(db): 단독 문장 재연결 — psycopg 이중 게이트(클래스+closed)·정확히 1회 재시도·카운터/로그/훅" -- src/dms/db.py tests/test_db_reconnect.py
```

---

### Task 2: 트랜잭션 경계 — BEGIN 만 재시도, yield 이후 전면 금지

**Files:**
- Modify: `src/dms/db.py`(`transaction()` 만)
- Modify: `tests/test_db_reconnect.py`, `tests/test_controller.py`

**Interfaces:**
- Consumes: Task 1 의 `_run`/`_connection_is_dead`/`_reconnect`/`_txn_depth`.
- Produces (Task 5 의 컨트롤러 규약이 이 위에 선다):
  - `transaction()` — BEGIN 은 `_run` 경유(depth 0 이라 죽음 시 재연결+1회 재시도, §2.3). yield 이후 실패: 죽음이면 **ROLLBACK 생략 + `_reconnect` + 원 예외 re-raise**, 살아있으면 현행대로 ROLLBACK 후 전파(§4). COMMIT 죽음: **재시도 금물**(새 커넥션의 COMMIT 은 빈 트랜잭션 no-op "성공" = 유실 위장) — 재연결만 하고 전파.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

**(1)** `tests/test_db_reconnect.py` — 파일 끝에 추가:

```python
# ---- §2.3 트랜잭션 경계: BEGIN 이후는 절대 재시도하지 않는다 ----

def test_death_after_yield_is_never_retried_and_preserves_the_original_exception(monkeypatch):
    old = _FakePgConn(fail_on=["UPDATE"])
    new = _FakePgConn()
    db = _pg_db(monkeypatch, old, [new])
    with pytest.raises(FakeOperationalError) as e:
        with db.transaction():
            db.execute("UPDATE t SET a = 1")
    assert e.value is old.raised[-1]   # 원 예외 "그 객체"가 보존된다
    # 죽은 문장은 구 커넥션에서 1회만 시도됐고 새 커넥션에서 재실행되지 않았다 --
    # 서버는 BEGIN 이후 문장들을 커넥션 소멸과 함께 폐기했으므로, 재실행은
    # 부분 적용을 "만들어내는" 동작이다(§2.3).
    assert old.executed == ["BEGIN", "UPDATE t SET a = 1"]
    assert not any("UPDATE" in sql for sql in new.executed)
    # 죽은 커넥션에 ROLLBACK 을 또 치지 않는다 -- 새 OperationalError 가 원
    # 예외를 가리는 현행 버그(§1-2)의 수정점. 서버는 커넥션 소멸 시점에
    # 트랜잭션을 폐기하므로 생략이 PG 의미론상 안전하다.
    assert not any("ROLLBACK" in sql for sql in old.executed)
    # 재연결은 됐다 -- 달라지는 건 "그 다음" 호출이 성공한다는 것뿐이다.
    assert db.reconnect_count == 1
    db.execute("INSERT INTO t (a) VALUES (1)")
    assert new.executed == ["INSERT INTO t (a) VALUES (1)"]


def test_begin_death_is_retried_once_and_the_transaction_proceeds(monkeypatch):
    # BEGIN(yield 전)은 아직 아무것도 적용하지 않았다 -- 유일한 재시도 허용
    # 지점(§2.3). 컨트롤러 리스 획득이 정확히 여기서 죽는다(§2.5).
    old = _FakePgConn(fail_on=["BEGIN"])
    new = _FakePgConn()
    db = _pg_db(monkeypatch, old, [new])
    with db.transaction():
        db.execute("INSERT INTO t (a) VALUES (1)")
    assert old.executed == ["BEGIN"]
    assert new.executed == ["BEGIN", "INSERT INTO t (a) VALUES (1)", "COMMIT"]
    assert db.reconnect_count == 1


def test_live_connection_error_inside_transaction_still_rolls_back(monkeypatch):
    # 살아있는 커넥션의 업무 예외(제약 위반류)는 현행 유지(§4): ROLLBACK 후 전파.
    conn = _FakePgConn()
    db = _pg_db(monkeypatch, conn, [])
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.execute("INSERT INTO t (a) VALUES (1)")
            raise RuntimeError("business rule")
    assert conn.executed == ["BEGIN", "INSERT INTO t (a) VALUES (1)", "ROLLBACK"]
    assert db.reconnect_count == 0


def test_commit_death_is_not_retried_on_the_new_connection(monkeypatch):
    # COMMIT 재시도는 유실을 성공으로 위장한다: 서버가 트랜잭션을 폐기한 뒤 새
    # 커넥션의 COMMIT 은 빈 트랜잭션의 no-op "성공"이다(§2.3 yield 이후 금지의
    # 극단). 정직하게 던지고, 다음 호출자를 위한 재연결만 한다.
    old = _FakePgConn(fail_on=["COMMIT"])
    new = _FakePgConn()
    db = _pg_db(monkeypatch, old, [new])
    with pytest.raises(FakeOperationalError):
        with db.transaction():
            db.execute("INSERT INTO t (a) VALUES (1)")
    assert old.executed == ["BEGIN", "INSERT INTO t (a) VALUES (1)", "COMMIT"]
    assert new.executed == []            # 새 커넥션엔 아무것도 다시 치지 않았다
    assert db.reconnect_count == 1


def test_txn_depth_is_restored_so_the_next_standalone_statement_can_retry(monkeypatch):
    # 실패한 트랜잭션이 깊이 카운터를 새면(leak) 이후 모든 단독 문장이 "트랜잭션
    # 안"으로 오판돼 재연결이 영구히 꺼진다 -- 복원을 행동으로 고정한다.
    old = _FakePgConn(fail_on=["UPDATE"])
    new = _FakePgConn(fail_on=["INSERT"])
    newer = _FakePgConn()
    db = _pg_db(monkeypatch, old, [new, newer])
    with pytest.raises(FakeOperationalError):
        with db.transaction():
            db.execute("UPDATE t SET a = 1")
    db.execute("INSERT INTO t (a) VALUES (1)")   # 깊이 0 복원 -> 재시도가 산다
    assert newer.executed == ["INSERT INTO t (a) VALUES (1)"]
    assert db.reconnect_count == 2
```

**(2)** `tests/test_controller.py` — 파일 머리 import 를 다음으로 교체:

```python
import pytest

from dms.build_runner import StubBuildRunner
from dms.controller import Loop, build_loops, run_all_once
from dms.repositories import Repositories
from dms.rollout_runner import StubRolloutRunner
```

파일 끝에 추가:

```python
def test_lease_begin_death_recovers_in_the_same_tick_without_crash(db, monkeypatch):
    """슬라이스 22 §2.5: 사건 때 컨트롤러는 리스 BEGIN 죽음 -> 크래시 -> 재시작
    으로 살아났다(RESTARTS +1). 재연결이 들어가면 같은 죽음이 **무크래시 같은
    틱**에서 복구돼야 한다(재시작으로 잃던 루프 한 바퀴 + 파드 기동 시간 소거).
    죽음 판정은 방언별(§2.1)이라 sqlite 실 DB 로는 판정 함수를 monkeypatch 해
    마커 예외를 죽음으로 인식시킨다 -- 게이트만 우회할 뿐, 기전 전체(재연결 ->
    BEGIN 재시도 -> 리스 획득 -> 루프 실행)는 실 sqlite 파일 재접속으로 통과한다."""

    class _Dead(Exception):
        pass

    class _DiesOnFirstBegin:
        def __init__(self, real):
            self._real = real
            self.died = False

        def execute(self, sql, params=None):
            if sql == "BEGIN" and not self.died:
                self.died = True
                raise _Dead("connection lost")
            return self._real.execute(sql, params)

        def close(self):
            self._real.close()

    db._conn = _DiesOnFirstBegin(db._conn)
    monkeypatch.setattr(db, "_connection_is_dead",
                        lambda exc: isinstance(exc, _Dead))
    repos = Repositories(db)
    ticks = []
    result = run_all_once([Loop("solo", 30, lambda: ticks.append(1))], repos,
                          holder="h1")
    assert result == {"solo": "ok"}   # 예외 없음 = RESTARTS 불변의 단위 등가물
    assert ticks == [1]               # 같은 틱에서 루프까지 실행됐다
    assert db.reconnect_count == 1
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_db_reconnect.py tests/test_controller.py -q`
Expected: 신규 6건 중 5건 FAIL / 1건 PASS —
- `test_death_after_yield_...`: `assert old.executed == ["BEGIN", "UPDATE t SET a = 1"]` 실패 — 실제로는 끝에 `"ROLLBACK"` 이 더 붙어 있다(현행 코드가 죽은 커넥션에 ROLLBACK 을 또 친다).
- `test_begin_death_...`: `FakeOperationalError` 전파로 실패(BEGIN 이 raw `self._conn.execute` 라 재시도가 없다).
- `test_commit_death_...`: `assert db.reconnect_count == 1` 실패(0 — COMMIT 죽음에 재연결이 없다).
- `test_txn_depth_...`: `assert newer.executed == [...]` 실패(재연결이 없어 old 커넥션에서 INSERT 가 그냥 성공한다).
- `test_lease_begin_death_...`(test_controller.py): `_Dead: connection lost` 전파로 ERROR(BEGIN 재시도 미구현 = 크래시 경로 그대로).
- `test_live_connection_error_...` 는 현행 동작 고정 가드라 **즉시 PASS 가 맞다**.

- [ ] **Step 3: transaction() 을 경계 의미론으로 교체한다**

`src/dms/db.py` — Task 1 이 만든 `transaction()` 전체를 다음으로 교체:

```python
    @contextmanager
    def transaction(self):
        with self._lock:
            # BEGIN(yield 전)은 아직 아무것도 적용하지 않았다 -- 트랜잭션에서
            # 유일하게 재연결 + 1회 재시도가 허용되는 지점이다(§2.3). _run 은
            # 이 시점 _txn_depth == 0 이라 단독 문장과 같은 규칙을 탄다.
            # 컨트롤러 리스 획득이 정확히 여기서 죽으므로(§1-6) 이 허용이
            # 컨트롤러 무크래시 같은-틱 복구(§2.5)의 실체다.
            self._run("BEGIN", None)
            self._txn_depth += 1
            try:
                yield self
            except BaseException as exc:
                self._txn_depth -= 1
                if self._connection_is_dead(exc):
                    # 죽은 커넥션에 ROLLBACK 을 또 치면 새 OperationalError 가
                    # 원 예외를 가린다(§1-2 현행 버그). 서버는 커넥션 소멸
                    # 시점에 트랜잭션을 폐기하므로 생략이 PG 의미론상 안전하다.
                    # 재연결만 해서 다음 호출자가 산 커넥션을 받게 하고, 원
                    # 예외를 그대로 올린다 -- 호출자는 지금과 동일하게 실패를
                    # 보고, 달라지는 건 "그 다음" 호출이 성공한다는 것뿐이다.
                    self._reconnect(cause=exc)
                else:
                    # 살아있는 커넥션의 업무 예외(제약 위반 등): 현행 유지(§4).
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._txn_depth -= 1
                try:
                    self._conn.execute("COMMIT")
                except Exception as exc:
                    if self._connection_is_dead(exc):
                        # COMMIT 재시도는 금물: 서버가 트랜잭션을 폐기한 뒤 새
                        # 커넥션의 COMMIT 은 빈 트랜잭션의 no-op "성공"이라
                        # 유실을 성공으로 위장한다(§2.3 yield 이후 금지의 극단).
                        # 다음 호출자를 위한 재연결만 하고 정직하게 던진다.
                        self._reconnect(cause=exc)
                    raise
```

- [ ] **Step 4: 통과를 확인한다 (트랜잭션 소비처 광역 회귀)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_db_reconnect.py tests/test_db.py tests/test_controller.py tests/test_events_outside_transaction.py tests/test_repo_control.py tests/test_repo_requests.py tests/test_observability.py -q`
Expected: 전부 PASS (sqlite 트랜잭션 롤백·스레드 배제·리스·record_event 경로 전부 무변경이어야 한다)

- [ ] **Step 5: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "feat(db): 트랜잭션 경계 — BEGIN 만 재시도, yield 이후 금지·죽은 커넥션 ROLLBACK 생략·원 예외 보존" -- src/dms/db.py tests/test_db_reconnect.py tests/test_controller.py
```

---

### Task 3: 관측 배선 — events `db_reconnected` 훅을 api/controller 양쪽에

**Files:**
- Modify: `src/dms/wiring.py`, `src/dms/api/app.py`, `src/dms/cli.py`
- Modify: `tests/test_db_reconnect.py`, `tests/test_api_auth.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 1 의 `Database.on_reconnect`/`reconnect_count`, `record_event` 의 "절대 예외를 올리지 않는다" 계약(`observability.py:16-29`), `Repositories(db).observability`.
- Produces:
  - `wiring.wire_reconnect_event(db, repos) -> None` — `db.on_reconnect` 에 `record_event(component="db", severity="warning", event_type="db_reconnected", message="dialect=... count=N")` 를 단다. api(`create_app`)와 controller(`cli.py`)가 **같은 함수**를 쓴다.
  - 새 테이블·새 사유 코드 0 — 기존 events 스키마·기존 retention 그대로(§2.6).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

**(1)** `tests/test_db_reconnect.py` — 파일 끝에 추가:

```python
# ---- §2.6 영속 흔적: events db_reconnected (훅 배선) ----

def test_wire_reconnect_event_writes_a_db_reconnected_event(db):
    # "얼마나 자주 끊기는가"를 SQL 로 셀 수 있는 유일한 영속 흔적(§2.6). 새
    # 테이블·새 사유 코드 없이 기존 events + 기존 retention 을 그대로 쓴다.
    # record_event 는 절대 예외를 올리지 않으므로(observability 계약) 재연결
    # 직후 재실패에도 안전하다.
    from dms.repositories import Repositories
    from dms.wiring import wire_reconnect_event
    wire_reconnect_event(db, Repositories(db))
    db.reconnect_count = 3           # 메시지가 카운터·방언을 나르는지까지 본다
    db.on_reconnect()
    row = db.query_one(
        "SELECT component, severity, event_type, message FROM events")
    assert row == {"component": "db", "severity": "warning",
                   "event_type": "db_reconnected", "message": "dialect=sqlite count=3"}
```

**(2)** `tests/test_api_auth.py` — 파일 끝에 추가:

```python
def test_create_app_wires_the_reconnect_event_hook(client, db):
    # 슬라이스 22 §2.6: client 픽스처가 create_app 을 이미 통과했다 -- 훅이
    # 배선되어 실제 events 행을 남기는지 행동으로 고정한다(배선 회귀 가드).
    db.on_reconnect()
    row = db.query_one("SELECT component, event_type FROM events")
    assert row == {"component": "db", "event_type": "db_reconnected"}
```

**(3)** `tests/test_cli.py` — 파일 끝에 추가:

```python
def test_controller_once_wires_the_reconnect_event_hook(tmp_path, monkeypatch):
    # 슬라이스 22 §2.6: api(create_app)와 같은 훅이 controller 경로에도 배선되는지.
    # main() 이 만드는 Database 는 밖에서 못 잡으므로 wiring 함수 호출 자체를
    # 스파이한다 -- cli 는 wire_reconnect_event 를 .wiring 모듈에서 호출 시점에
    # 읽기 때문에 monkeypatch 가 통한다.
    monkeypatch.setenv("DMS_DATABASE_URL", f"sqlite:///{tmp_path}/c.db")
    monkeypatch.setenv("DMS_SHARED_TOKEN", "t")
    monkeypatch.setenv("DMS_ADMIN_TOKEN", "a")
    monkeypatch.setenv("DMS_SESSION_SECRET", "s")
    assert main(["migrate"]) == 0
    import dms.wiring as wiring
    real = wiring.wire_reconnect_event
    wired = []
    monkeypatch.setattr(wiring, "wire_reconnect_event",
                        lambda db, repos: wired.append(real(db, repos)))
    assert main(["controller", "--once"]) == 0
    assert len(wired) == 1
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_db_reconnect.py tests/test_api_auth.py tests/test_cli.py -q`
Expected: 신규 3건 FAIL — `test_wire_reconnect_event_...` 가 `ImportError: cannot import name 'wire_reconnect_event' from 'dms.wiring'`, `test_create_app_wires_...` 가 `TypeError: 'NoneType' object is not callable`(훅 미배선), `test_controller_once_wires_...` 가 `AttributeError: <module 'dms.wiring'> has no attribute 'wire_reconnect_event'`. 나머지 기존 테스트는 PASS.

- [ ] **Step 3: wiring.py 에 훅 함수를 만든다**

`src/dms/wiring.py` — 파일 끝에 추가:

```python
def wire_reconnect_event(db, repos) -> None:
    """슬라이스 22 §2.6: 재연결 성공의 영속 흔적 1건. record_event 는 절대
    예외를 올리지 않는 계약(observability.py)이라 재연결 직후 재실패에도
    안전하고, 트랜잭션 밖 단독 INSERT 라 업무 변경을 되돌릴 수도 없다.
    api(create_app)와 controller(cli)가 이 함수 하나를 같이 쓴다 -- 두 곳이
    각자 훅을 만들면 이벤트 모양이 갈라져 SQL 집계가 깨진다."""
    def _record():
        repos.observability.record_event(
            component="db", severity="warning", event_type="db_reconnected",
            message=f"dialect={db.dialect} count={db.reconnect_count}")
    db.on_reconnect = _record
```

- [ ] **Step 4: create_app 과 cli controller 경로에 배선한다**

**(1)** `src/dms/api/app.py` — import 블록의

```python
from ..wiring import (build_build_runner, build_execution_adapter,
                     build_identity_resolver, build_queue_reader,
                     build_rollout_runner)
```

을 다음으로 교체:

```python
from ..wiring import (build_build_runner, build_execution_adapter,
                     build_identity_resolver, build_queue_reader,
                     build_rollout_runner, wire_reconnect_event)
```

그리고 `app.state.queue_reader = build_queue_reader(settings)` 바로 아래에 추가:

```python
    # 슬라이스 22 §2.6: 재연결 성공의 영속 흔적(events.db_reconnected) 훅.
    wire_reconnect_event(db, app.state.repos)
```

**(2)** `src/dms/cli.py` — controller 분기의 import

```python
        from .wiring import (build_build_runner, build_execution_adapter,
                             build_identity_resolver, build_rollout_runner)
```

을 다음으로 교체:

```python
        from .wiring import (build_build_runner, build_execution_adapter,
                             build_identity_resolver, build_rollout_runner,
                             wire_reconnect_event)
```

`repos = Repositories(db)` 바로 아래에 추가:

```python
        # 슬라이스 22 §2.6: 컨트롤러도 재연결 흔적을 남긴다(api 와 같은 훅).
        wire_reconnect_event(db, repos)
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_db_reconnect.py tests/test_api_auth.py tests/test_cli.py tests/test_controller.py tests/test_observability.py -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "feat(observability): 재연결 영속 흔적 — events db_reconnected 훅을 api/controller 에 배선" -- src/dms/wiring.py src/dms/api/app.py src/dms/cli.py tests/test_db_reconnect.py tests/test_api_auth.py tests/test_cli.py
```

---

### Task 4: readyz — 본문 카운터 + 연속 실패 자기 종료 (`DMS_READYZ_EXIT_FAILURES`)

**Files:**
- Modify: `src/dms/config.py`, `src/dms/api/app.py`, `deploy/k8s/20-config.yaml`
- Modify: `tests/test_config.py`, `tests/test_api_auth.py`
- Create: `tests/test_api_readyz.py`

**Interfaces:**
- Consumes: Task 1 의 `reconnect_count`/`last_reconnect_at`, `_SERVER_INT_KEYS` 배선 관례(`config.py:9-50`, `**extra` `:160-171`), 기존 readyz(`app.py:51-60`), 프로브 주기 10s(`40-api.yaml:101-105`).
- Produces:
  - `Settings.readyz_exit_failures: int = 30`(`DMS_READYZ_EXIT_FAILURES`, 0=비활성).
  - `create_app(settings, db, exit_fn=None)` — 기본 `exit_fn` 은 `os.kill(os.getpid(), signal.SIGTERM)`(uvicorn graceful 종료 → 컨테이너 종료 → restartPolicy 재시작). 주입은 테스트용.
  - `/readyz` 200 본문 `{"status":"ok","reconnects":N,"last_reconnect_at":...}`(§2.6 — kubelet 은 상태 코드만 보므로 프로브 무영향). 503 본문은 기존 `{"status":"degraded"}` 그대로.
  - 연속 실패 카운터: 실패마다 +1, **성공 시 0 리셋**, `limit > 0 and n >= limit` 에서 stderr 사유 후 `exit_fn()`.
  - `20-config.yaml` 에 `DMS_READYZ_EXIT_FAILURES: "30"`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

**(1)** `tests/test_config.py` — 파일 끝에 추가:

```python
def test_readyz_exit_failures_default_and_env_override():
    # 슬라이스 22 §2.4: 연속 readyz 실패 자기 종료 임계(10s 프로브 기준 30 ≈ 5분,
    # 0=비활성). _SERVER_INT_KEYS 튜플에만 넣으면 from_env 의 **extra 가
    # 배선한다 -- 필드/키 양쪽이 실제로 이어졌는지 고정(빌드 프리플라이트 선례).
    assert Settings.from_env(VALID).readyz_exit_failures == 30
    assert Settings.from_env(
        {**VALID, "DMS_READYZ_EXIT_FAILURES": "0"}).readyz_exit_failures == 0
```

**(2)** `tests/test_api_auth.py` — 기존

```python
def test_readyz_ok(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
```

을 다음으로 교체:

```python
def test_readyz_ok(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    # 슬라이스 22 §2.6: kubelet 은 상태 코드만 본다 -- 본문 카운터는 운영자용
    # (curl 한 번으로 재연결 빈도를 본다). 프로브 계약 무영향.
    assert r.json() == {"status": "ok", "reconnects": 0, "last_reconnect_at": None}
```

**(3)** `tests/test_api_readyz.py` (신규 파일 전체):

```python
"""슬라이스 22 §2.4: 연속 readyz 실패 자기 종료. exit_fn 주입으로 SIGTERM 없이
검증한다 -- 기본 exit_fn(os.kill SIGTERM)의 실 발화는 실증 §6-3(iptables
REJECT -> RESTARTS +1)이 담당한다. 기각 대안 요약: liveness DB 직결은 90s 발화
+ CrashLoopBackOff 백오프가 DB 복귀 후 회복을 늦추고(replicas 1 이라 백오프
동안 API 0대), 현상 유지는 90분 방치 사건의 재발이다 -- 이미 readiness 503 으로
Service 에서 빠져 있어 종료로 잃는 가용성이 0 이라는 것이 채택 근거다."""
from fastapi.testclient import TestClient

from dms.api.app import create_app
from dms.config import Settings


def _flaky(db, monkeypatch):
    """db.query_one 을 스위치 달린 대역으로 -- readyz 의 SELECT 1 만 조작한다."""
    state = {"fail": False}
    real = db.query_one

    def query_one(sql, params=None):
        if state["fail"]:
            raise RuntimeError("db down")
        return real(sql, params)

    monkeypatch.setattr(db, "query_one", query_one)
    return state


def _client(db, exit_calls, threshold):
    settings = Settings(database_url="unused", shared_token="tok-shared",
                        admin_token="tok-admin", session_secret="sess-secret",
                        readyz_exit_failures=threshold)
    return TestClient(create_app(settings, db,
                                 exit_fn=lambda: exit_calls.append(1)))


def test_consecutive_failures_reach_the_limit_and_self_terminate(db, monkeypatch, capsys):
    calls = []
    state = _flaky(db, monkeypatch)
    client = _client(db, calls, threshold=3)
    state["fail"] = True
    codes = [client.get("/readyz").status_code for _ in range(3)]
    assert codes == [503, 503, 503]   # 상태 코드·503 본문은 기존 그대로(프로브 계약)
    assert calls == [1]               # 정확히 임계 도달 시 1회
    assert "self-terminating" in capsys.readouterr().err   # 종료 사유가 로그에 남는다


def test_a_success_resets_the_counter(db, monkeypatch):
    # 리셋이 없으면 "가끔 한 번씩 실패"가 몇 시간에 걸쳐 누적돼 멀쩡한 파드를
    # 죽인다 -- 임계는 어디까지나 **연속** 실패다(§2.4).
    calls = []
    state = _flaky(db, monkeypatch)
    client = _client(db, calls, threshold=3)
    state["fail"] = True
    client.get("/readyz")
    client.get("/readyz")                             # 연속 2 (임계 3 미만)
    state["fail"] = False
    assert client.get("/readyz").status_code == 200   # 성공 -> 카운터 0
    state["fail"] = True
    client.get("/readyz")
    client.get("/readyz")                             # 다시 연속 2
    assert calls == []   # 리셋이 없었다면 누적 4·5번째에서 이미 발화했다


def test_zero_disables_self_termination(db, monkeypatch):
    # DMS_READYZ_EXIT_FAILURES=0 은 명시적 비활성(§2.4) -- 운영자가 장치를 끄고
    # 관찰만 하고 싶을 때의 탈출구다.
    calls = []
    state = _flaky(db, monkeypatch)
    client = _client(db, calls, threshold=0)
    state["fail"] = True
    for _ in range(10):
        assert client.get("/readyz").status_code == 503
    assert calls == []
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_config.py tests/test_api_auth.py tests/test_api_readyz.py -q`
Expected: 신규/수정 5건 FAIL — `test_readyz_exit_failures_...` 가 `AttributeError: 'Settings' object has no attribute 'readyz_exit_failures'`, `test_readyz_ok` 가 본문 불일치(`{"status": "ok"} != {...reconnects...}`), `tests/test_api_readyz.py` 3건이 `TypeError: Settings.__init__() got an unexpected keyword argument 'readyz_exit_failures'`. 나머지 PASS.

- [ ] **Step 3: config.py 에 키를 배선한다**

**(1)** `_SERVER_INT_KEYS` 의 마지막 항목(`DMS_ROLLOUT_TIMEOUT_SECONDS` 튜플) **바로 아래**에 추가:

```python
    # 슬라이스 22 §2.4: /readyz 연속 실패 N회에 API 파드 SIGTERM 자기 종료
    # (0=비활성). readiness 프로브 10s 주기 기준 30회 ≈ 5분. liveness DB 직결은
    # 기각했다: 90s 만에 발화해 DB 순단에도 전 파드가 재시작되고 CrashLoopBackOff
    # 지수 백오프(최대 5분)가 DB 복귀 **후의** 회복을 늦춘다(replicas 1 이라
    # 백오프 동안 API 0대). 재연결(§2.2)이 실패한 채 5분이면 파드 교체가 옳고,
    # 이미 readiness 503 으로 Service 에서 빠져 있어 잃는 가용성이 0 이다.
    ("DMS_READYZ_EXIT_FAILURES", "readyz_exit_failures", 30),
```

**(2)** `Settings` dataclass 의 `rollout_timeout_seconds: int = 600` **바로 아래**에 추가:

```python
    readyz_exit_failures: int = 30
```

- [ ] **Step 4: app.py 의 readyz 를 고친다**

**(1)** 파일 머리 `import os` 를 다음으로 교체:

```python
import os
import signal
import sys
```

**(2)** `def create_app(settings: Settings, db: Database) -> FastAPI:` 를 다음으로 교체:

```python
def create_app(settings: Settings, db: Database, exit_fn=None) -> FastAPI:
```

**(3)** `/healthz`·`/readyz` 블록 전체(`@app.get("/healthz")` 부터 readyz 의 `return {"status": "ok"}` 까지)를 다음으로 교체:

```python
    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    # 슬라이스 22 §2.4: 연속 readyz 실패 N회(기본 30 ≈ 10s 프로브로 5분)면
    # SIGTERM 자기 종료. 재연결(db.py §2.2)이 들어간 뒤의 readyz 실패는
    # "재연결까지 실패"다 -- 그 상태 5분이면 파드 교체가 옳고(사건의 실측된
    # 유일한 처방이 파드 삭제였다), 이미 readiness 503 으로 Service 에서 빠져
    # 있어 종료로 잃는 가용성이 0 이다. uvicorn 은 SIGTERM 에 graceful 종료하고
    # Deployment 가 재시작한다. exit_fn 주입은 테스트용이다.
    exit_ = exit_fn or (lambda: os.kill(os.getpid(), signal.SIGTERM))
    readyz_failures = {"n": 0}  # GIL 하 단일 int 증가 -- 락 불요(설계 §4)

    @app.get("/readyz")
    def readyz():
        # liveness(/healthz)와 달리 실제 의존성(DB)에 쿼리를 날려 readiness를 검증한다 —
        # DB가 죽으면 인증 게이트를 포함한 거의 모든 요청이 500이 되므로, readiness
        # probe가 이를 감지해 Service에서 이 파드를 빼야 한다.
        try:
            db.query_one("SELECT 1 AS x")
        except Exception:
            readyz_failures["n"] += 1
            limit = settings.readyz_exit_failures
            if limit > 0 and readyz_failures["n"] >= limit:
                # SIGTERM 뒤에도 프로브가 몇 번 더 올 수 있으나 카운터는 이미
                # 임계 초과라 무해하다(§4). 사유를 stderr 에 남긴다 -- kubectl
                # logs 의 마지막 줄들이 이 사건의 1차 증거다.
                print(f"readyz failed {readyz_failures['n']} consecutive times "
                      f"(limit={limit}) -- self-terminating with SIGTERM",
                      file=sys.stderr)
                exit_()
            return JSONResponse(status_code=503, content={"status": "degraded"})
        readyz_failures["n"] = 0
        # 슬라이스 22 §2.6: kubelet 은 상태 코드만 본다 -- 본문 카운터는
        # 운영자용이다(curl 한 번으로 재연결 빈도를 본다).
        return {"status": "ok", "reconnects": db.reconnect_count,
                "last_reconnect_at": db.last_reconnect_at}
```

- [ ] **Step 5: 20-config.yaml 에 키를 추가한다**

`deploy/k8s/20-config.yaml` — `DMS_ROLLOUT_TIMEOUT_SECONDS: "600"` **바로 아래**에 추가:

```yaml

  # --- DB 커넥션 재연결 (슬라이스 22) ---
  # /readyz 연속 실패 N회에 API 파드가 SIGTERM 으로 자기 종료한다(0=비활성).
  # readiness 프로브 10s 주기(40-api.yaml) 기준 30회 ≈ 5분. 재연결까지 실패한
  # 상태가 5분이면 파드 교체가 옳다(2026-08-11 90분 방치 사건의 자동 탈출).
  # 이미 readiness 503 으로 Service 에서 빠져 있어 종료로 잃는 가용성은 0 이고,
  # DB 장기 다운 시 ~5분 주기 재시작 루프의 RESTARTS 증가는 그 자체가 사건에
  # 없던 신호다. 컨트롤러엔 이 장치가 없다 -- crash-restart 가 동등물이다(§2.5).
  DMS_READYZ_EXIT_FAILURES: "30"
```

- [ ] **Step 6: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_config.py tests/test_api_auth.py tests/test_api_readyz.py tests/test_api_spa.py tests/test_cli.py -q`
Expected: 전부 PASS (create_app 시그니처 확장은 기본값이라 기존 호출부 무변경, spa/cli 는 회귀 확인)

- [ ] **Step 7: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git add tests/test_api_readyz.py
git commit -m "feat(api): readyz 자기 종료 — 연속 DMS_READYZ_EXIT_FAILURES 회 실패 시 SIGTERM + 본문 reconnects 카운터" -- src/dms/config.py src/dms/api/app.py deploy/k8s/20-config.yaml tests/test_config.py tests/test_api_auth.py tests/test_api_readyz.py
```

---

### Task 5: 컨트롤러 — crash-restart 규약 승격(주석) + 지속 장애 전파 가드

**Files:**
- Modify: `src/dms/controller.py`(주석만 — 코드 동작 무변경)
- Modify: `tests/test_controller.py`

**Interfaces:**
- Consumes: Task 2 가 이미 넣은 무크래시 같은-틱 복구 테스트(BEGIN 재시도의 실증), `run_all_once` 의 리스 획득이 per-loop try 밖인 현행 구조(`controller.py:103-108`).
- Produces: "리스 획득 실패 = 의도적 crash-restart 경로" 규약 — 주석(리팩터링 금지 문서화) + 전파를 고정하는 테스트(try 안으로 옮기는 리팩터가 들어오면 여기서 잡힌다).

- [ ] **Step 1: 규약 테스트를 쓴다 (즉시 PASS 가 맞다 — 현행 동작의 고정 가드)**

`tests/test_controller.py` — 파일 끝에 추가:

```python
def test_persistent_lease_death_still_crashes_the_controller(db, monkeypatch):
    # 슬라이스 22 §2.5 안전망 보존: 재연결조차 소용없는 지속 장애는 지금처럼
    # per-loop try **밖**에서 전파돼 프로세스가 죽고(crash-restart), Deployment
    # 재시작이 새 커넥션을 얻는다. 컨트롤러엔 HTTP 헬스가 없으므로 이것이 api
    # 자기 종료(§2.4)의 동등물이다. 이 테스트는 "리스 획득을 try 안으로 옮기는"
    # 리팩터링을 금지하는 규약의 집행부다 -- 옮기면 지속 장애가 error 결과로
    # 접혀 "조용히 도는 정지"가 된다(90분 방치 사건의 컨트롤러판).
    def boom(component, holder, lease_seconds, now_iso=None):
        raise RuntimeError("db unreachable")

    repos = Repositories(db)
    monkeypatch.setattr(repos.control, "try_acquire_lease", boom)
    with pytest.raises(RuntimeError):
        run_all_once([Loop("solo", 30, lambda: None)], repos, holder="h1")
```

- [ ] **Step 2: 즉시 PASS 를 확인한다 (RED 없음 — 현행 고정 가드임을 명시)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_controller.py -q`
Expected: 전부 PASS (신규 1건 포함 — 이 테스트는 결함 재현이 아니라 규약 고정이다. 일부러 깨보려면 `run_all_once` 의 리스 획득을 try 안으로 옮겨 보라 — 즉시 FAIL 한다.)

- [ ] **Step 3: controller.py 에 규약 주석을 단다**

`src/dms/controller.py` — `run_all_once` 의

```python
    for loop in loops:
        acquired = repos.control.try_acquire_lease(
```

을 다음으로 교체:

```python
    for loop in loops:
        # 슬라이스 22 §2.5 규약: 리스 획득은 **의도적으로** per-loop try 밖이다.
        # 여기의 DB 죽음(transaction 의 BEGIN)은 재연결 + 1회 재시도(db.py §2.3)
        # 로 같은 틱에서 복구되고, 재연결조차 실패하는 지속 장애면 예외가 그대로
        # 전파돼 프로세스가 죽는다 -- 컨트롤러엔 HTTP 헬스가 없으므로 이
        # crash-restart 가 api 자기 종료(§2.4)의 동등물이다. try 안으로 옮기는
        # 리팩터링 금지: 옮기면 지속 장애가 "error 결과로 접혀 조용히 도는
        # 정지"가 된다(test_persistent_lease_death_still_crashes_the_controller
        # 가 이 규약의 집행부다).
        acquired = repos.control.try_acquire_lease(
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_controller.py tests/test_controller_planner.py tests/test_controller_stepper.py tests/test_controller_batch.py tests/test_cli.py -q`
Expected: 전부 PASS (주석만 바뀌었다 — 하나라도 깨지면 주석 이상을 건드린 것이다)

- [ ] **Step 5: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "feat(controller): 리스 획득 crash-restart 규약 승격(주석) + 지속 장애 전파 가드 테스트" -- src/dms/controller.py tests/test_controller.py
```

---

### Task 6: 마감 검증 — 전체 스위트 + 프론트 기준선 무변경 (커밋 없음)

**Files:** 없음(검증만)

**Interfaces:** 없음 — 슬라이스 전체의 완료 판정.

- [ ] **Step 1: 백엔드 전체 스위트**

Run(포그라운드, Bash timeout 900000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests -q`
Expected: **1164 passed**(기준선 1137 + 이 플랜 신규 27: Task1 13 + Task2 6 + Task3 3 + Task4 4 + Task5 1 — 근사치다. 수가 다르면 신규 테스트 수를 다시 세되, **failed 0 이 본질**이다)

- [ ] **Step 2: 프론트 기준선 무변경 확인 (이 플랜은 frontend/ 를 한 글자도 안 바꿨다)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b`
Expected: `Test Files  49 passed`, `Tests  228 passed`, tsc 무출력 exit 0 — Task 0 과 동일 수치.

- [ ] **Step 3: 계약·불변 조항 확인**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && git status --porcelain && git log --oneline -5`
Expected: 작업 트리 clean(커밋 5건 외 잔여물 없음), `frontend/`·`docs/`(이 플랜 파일 제외)·`legacy/`·이미지 태그 무변경. 스키마 무변경은 Step 1 의 `test_migrations*` 초록이 이미 보증한다.

---

## 플랜 이후: 배포·실증 (설계 §6 — 별도 ops, 플랜 밖)

플랜 실행(Task 0/6 검증 + Task 1~5 커밋 5건)이 끝나면 컨트롤러가 테스트베드에서 수행한다 — 플랜 태스크가 아니다(슬라이스 12~21 과 동일 관례). 서버(api/controller) 코드만 바뀌었고 에이전트·프론트는 무변경이다 — `dms` 이미지만 범프한다(`install/docker/Dockerfile.testbed` 로 빌드 — deploy/Dockerfile 은 kubectl 이 없다). DB 는 pkg-01 의 postgres(10.10.10.30:5432). **전부 되돌릴 수 있는 조작만 쓴다.**

1. `kubectl apply -f deploy/k8s/20-config.yaml`(신규 키 `DMS_READYZ_EXIT_FAILURES: "30"` 포함) → `dms` 이미지 빌드·푸시 → 40/41 태그 범프·apply(태그 결정은 배포자 몫 — 플랜은 태그 불변, 에이전트 DaemonSet(50) 불변).
2. (§6-1, **핵심**) **API 재연결**: pkg-01 에서 `SELECT pid, client_addr, state FROM pg_stat_activity WHERE datname='dms';` 로 API 파드 IP 의 backend 를 식별 → `SELECT pg_terminate_backend(<pid>);`(1개 backend 종료는 비파괴·되돌림 불요). 직후 포탈 아무 화면(또는 `curl /api/...`) — **첫 요청부터 200**, RESTARTS 불변, 로그에 `db reconnected` 한 줄, `/readyz` 본문 `reconnects` 증가, events 에 `db_reconnected` 1건. 사건 재현 조건(커넥션만 죽고 TCP 는 정상)과 동형이다.
3. (§6-2) **컨트롤러 무크래시 복구**: 컨트롤러 backend 를 같은 방법으로 종료 → **RESTARTS 불변**(사건 때는 +1 이었다 — 대조가 증거다), `component_leases` 의 `expires_at` 이 계속 전진(루프 생존), 재연결 로그 확인.
4. (§6-3, **핵심**) **자기 종료 탈출**: pkg-01 에서 `iptables -I INPUT -p tcp -s <API파드IP> --dport 5432 -j REJECT` → readyz 503 누적 → 약 30×10s ≈ 5분 후 RESTARTS +1 + `self-terminating` 종료 사유 로그 → `iptables -D INPUT -p tcp -s <API파드IP> --dport 5432 -j REJECT` 로 **즉시 제거**(CrashLoopBackOff 성장 방지) → 파드 Ready 복귀. 90분 방치 사건의 자동 탈출이 이것으로 증명된다.
5. (§6-4) **중복 미적용 스모크**: 2번 직후 제출한 요청 1건이 DB 에 정확히 1행인지 확인(재시도 이중 적용 창의 스모크). 트랜잭션 중간 죽음은 라이브에서 타이밍이 비결정적이라 §5 단위 테스트(Task 2)가 담당한다 — 여기 적어 숨기지 않는다.
6. 실증 통과 후 `BACKLOG.md`(373-404 의 사건 기록 — "원인 불명" 기록은 유지한다: 이 슬라이스는 원인과 무관한 복구 장치다)를 별도 커밋으로 갱신한다.

## Self-Review

**1. 스펙 커버리지**

| 설계 절 | 담당 태스크 |
|---|---|
| §1 실측 전제(단일 커넥션+RLock·URL 미보관, ROLLBACK 재송 버그, psycopg/sqlite 예외 계층, readyz/healthz·프로브, 컨트롤러 생존 기전, 단독 문장 성격, psycopg optional dep) | 실측 고정값 표 + 각 태스크 근거 주석 |
| §2.1 죽음 판정 — 방언별, pg 는 클래스+closed 이중 게이트, sqlite 재연결 없음 | Task 1(`_connection_is_dead` + 게이트별 테스트 4종 + sqlite 2종) |
| §2.2 `_run` 한 곳·`_url` 보관·정확히 1회 재시도·백오프 없음·이중 적용 창 정직 명기 | Task 1(재시도 1+1 고정 테스트) + 실측 표(창 한정 근거) + 플랜 이후 5(스모크) |
| §2.3 `_txn_depth`·yield 이후 전면 금지·BEGIN 만 재시도·죽음 시 ROLLBACK 생략·원 예외 보존 | Task 2(경계 전 분기 5종 + 깊이 복원) |
| §2.4 liveness 절충 — healthz 유지 + 연속 N회 readyz 실패 SIGTERM(기본 30, 0=비활성) | Task 4(config/app/20-config + 임계·리셋·비활성 테스트) |
| §2.5 컨트롤러 같은-틱 복구 + crash-restart 규약 승격 | Task 2(무크래시 통합 테스트 — BEGIN 재시도의 RED/GREEN 을 실 sqlite 로) + Task 5(주석 + 전파 가드) |
| §2.6 관측 3곳 — stderr·readyz 본문·events 1건 | Task 1(stderr·카운터) + Task 3(events 훅 양쪽 배선) + Task 4(readyz 본문) |
| §3 화면 무변경 | 어떤 태스크도 frontend/ 를 건드리지 않음 — Task 6 이 기준선 228/49 로 확인 |
| §4 오류 처리(판정 밖 전파, 재연결 실패는 원 예외 chain, 카운터 단순 int, 살아있는 커넥션 ROLLBACK 유지, sqlite 완전 무변경) | Task 1(chain 테스트)·Task 2(live ROLLBACK)·Task 4(카운터)·Task 1(sqlite) |
| §5 테스트 목록 | Task 1~5 각 Step 1 이 설계 §5 의 항목을 1:1 이상으로 덮는다(기준선 실측 1137 로 갱신) |
| §6 실증 | 플랜 이후 절(관례 — 플랜 태스크 아님) |
| §7 하지 않는 것(커넥션 풀, 다회 재시도/백오프, liveness DB 직결, 컨트롤러 HTTP 헬스, sqlite 재연결, 대시보드 패널, 원인 규명, 신설 사유 코드 0) | 어떤 태스크도 만들지 않음 — 새 의존성 0·스키마 무변경·frontend 무변경 |

**2. 플레이스홀더 점검** — "TBD"/"적절히"/코드 없는 스텝 없음. 신규 테스트 파일 2개 전문, db.py 교체 코드 전문(Task 1 클래스 전체 + Task 2 transaction 전체), wiring/app/cli/config/20-config 교체 전후 코드, 반복 실행 명령 전문 수록. 다른 태스크 참조는 Interfaces 시그니처로만 한다.

**3. 타입 일관성** — `_url`/`_open`/`_connection_is_dead`/`_run`/`_reconnect`/`_txn_depth`/`reconnect_count`/`last_reconnect_at`/`on_reconnect` 는 Task 1 이 정의하고 Task 2(transaction)·Task 3(wiring 훅)·Task 4(readyz 본문)·Task 2 컨트롤러 테스트(monkeypatch 대상)가 같은 철자로 쓴다. `wire_reconnect_event` 는 Task 3 이 정의하고 app/cli/테스트 3곳이 같은 철자. `readyz_exit_failures`/`DMS_READYZ_EXIT_FAILURES` 는 config·app·20-config·테스트가 동일 철자. events 필드(component="db", severity="warning", event_type="db_reconnected")는 wiring 과 테스트 2곳이 동일 철자다.

**알려진 위험 / 설계 대비 조정:**
- **venv 에 psycopg 가 없다(실측)** — 설계 §5 는 "가짜 psycopg 커넥션 주입"만 말했지만, isinstance 게이트가 실 psycopg 예외 타입을 요구하므로 **페이크 모듈의 `sys.modules` 주입**까지 필요했다. db.py 의 `import psycopg` 가 lazy(호출 시점)라 `monkeypatch.setitem` 이 통하고, sqlite 방언은 게이트 첫 줄에서 반환해 psycopg 를 영원히 import 하지 않는다 — 로컬·CI 안전.
- **closed 게이트는 `getattr(self._conn, "closed", False)`** — 속성 없는 커넥션이면 죽음 아님(전파)으로 접힌다. 안전한 방향(재시도 과소)이며, 실 psycopg Connection 은 항상 `closed` 를 가진다.
- **COMMIT 죽음 처리(재시도 금물 + 재연결 + 전파)는 설계에 명시 문장이 없다** — §2.3 "yield 이후 전면 금지"를 COMMIT 까지 일관 적용한 해석이다. 재시도하면 새 커넥션의 빈 COMMIT 이 "성공"해 커밋 유실을 위장하므로 금지가 유일하게 정직하다. 전용 테스트로 고정.
- **훅 재귀 1단 차단(`_in_reconnect_hook`)과 훅 예외 무해화는 설계 미지정 방어다** — record_event 는 예외를 안 올리는 계약이지만, 훅이 임의 콜러블인 이상 재연결 성공(복구)이 관측(훅)에 인질로 잡히면 안 된다.
- **severity="warning" 선택** — 설계 §2.6 은 component/event_type 만 지정했다. 복구 성공이므로 error 는 과하고, 기존 이벤트들이 error/warning 계열을 쓴다 — warning 으로 통일했다.
- **readyz 503 본문은 기존 `{"status":"degraded"}` 그대로** — 설계 §2.6 은 200 본문 확장만 말한다. 표면 최소화.
- **`test_readyz_ok`(기존)와 test_controller import 블록의 단언·형태를 바꿨다** — 각각 본문 확장(§2.6)과 pytest 사용의 직접 결과이며 Task 4/Task 2 가 근거 주석과 함께 교체한다.
- **컨트롤러 무크래시 테스트는 sqlite 실 DB 에 판정 함수 monkeypatch** — 방언 게이트만 우회하고, 재연결(_open 재수행: 실 sqlite 파일 재접속)→BEGIN 재시도→리스 획득→루프 실행의 기전 전체는 진짜로 돈다. pg 판정 자체는 Task 1 의 이중 게이트 단위 테스트가 별도로 고정한다.
- **Task 5 의 전파 가드는 RED 없이 즉시 PASS 다** — 결함 재현이 아니라 현행(의도된) 동작의 규약 고정이며, 플랜에 그 사실을 명시했다(슬라이스 21 의 "현행 고정 가드" 선례).
- **기본 exit_fn(os.kill SIGTERM)은 단위 테스트하지 않는다** — 3줄 람다이고, 실 발화는 실증 §6-3(iptables → RESTARTS +1)이 증명한다.
- **전체 수치 기대(1164/228)는 근사 명시** — 신규 27을 셌지만, 수가 어긋나면 재계산하되 failed 0 이 판정 기준이다.
