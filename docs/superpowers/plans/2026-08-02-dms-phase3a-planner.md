# DMS Phase 3a — Planner 경로 (요청 → 계획된 data_job) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `Pending` 요청을 planner가 어드미션 게이트(스토리지 준비·중복·정책)를 거쳐 실행 신원 해석, 도구 자동 선택, 후보 노드·fan-out 산정까지 마친 **계획된 data_job**으로 바꾼다. 실행(preflight/preview/confirm/execution)과 실 LDAP·Volcano 어댑터는 Phase 3b/3c.

**Architecture:** 스펙 §5(데이터 잡 실행)의 planner 부분. planner는 재시작 가능한 `run_once()` 루프로, `requests`(Pending)를 읽어 `plans` + `data_jobs`(Pending) 행을 emit한다. 외부 사실(마운트·도구·신원)은 DB에 캐시된 신선한 에이전트 증거로만 판단하고, LDAP identity 해석은 주입된 resolver(3a에선 stub, 3b에서 live)로 한다. 모든 거부는 사유 코드와 함께 요청을 `Rejected`/`Conflict`로 종결한다.

**Tech Stack:** Phase 1·2 코드 위에 Python 3.11+, stdlib only (LDAP 라이브러리는 3b).

## Global Constraints

- 스펙이 진실: `docs/superpowers/specs/2026-08-02-dms-clean-slate-design.md` §5. legacy 재사용 금지 (읽기 전용 참고만).
- 모든 런타임 SQL은 `src/dms/repositories/` 안에만 (스키마 DDL은 `migrations.py`). named param `:name`, SQLite/PG 호환.
- **fail-closed**: 확인할 수 없는 외부 사실(스토리지 준비, 신원, 도구, 후보 노드)은 진행 금지 → 사유 코드와 함께 `Rejected`. 조용한 실패 금지.
- **uid 하한 없음** (스펙 §5, legacy MIN_UID/MIN_GID 제거).
- **denylist가 최우선** — privileged 경로보다 먼저 평가. 대소문자 무관 (control repo가 소문자 정규화 저장).
- 모든 상태 전이는 `state_transitions`에 기록 (`entity_kind` = `request` 또는 `data_job`).
- 사유 코드는 snake_case. 시각은 `dms.db.utc_now_iso()` (UTC ISO-8601 `...Z`). JSON 컬럼은 TEXT + `dump_json`/`load_json`.
- 전체 테스트는 서비스 없이 SQLite로 (`.venv/bin/pytest` 단독, 0 warnings — pytest는 filterwarnings=error).
- 커밋: conventional commit + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`, 태스크마다 커밋.

## Phase 1·2가 제공하는 인터페이스 (전제 — 변경 금지)

- `dms.db`: `Database`, `.execute/.query/.query_one/.transaction()`, `dump_json/load_json/utc_now_iso/iso_plus`.
- `dms.domain`: `RequestState`(Pending/Planned/Running/Succeeded/Failed/Rejected/Conflict/Cancelled), `TERMINAL_REQUEST_STATES`, `DataJobState`(Pending/Preflight/PreviewRunning/ConfirmPending/Executing/Running/터미널), `Operation`(scan/sync/rm), `Tool`(dscan/dsync/nsync/drm), `PRIORITIES`, `PRIORITY_CLASS`, `DomainValidationError`.
- `dms.repositories.Repositories(db)` → `.requests .storages .accounts .control .agents .db`.
- `RequestsRepository`: `.create(...)`, `.get(id)`(payload는 dict), `.list(requester_id=None)`, `.set_state(id, RequestState, *, reason_code=None, actor)`, `.find_active(resource_key)`(비터미널 최오래 1건), `.record_result(id, terminal_state, *, reason_code, message, summary)`, `.transitions(id)`.
- `StoragesRepository`: `.get(name)`, `.list()` — 컬럼 `storage_name/mount_path/managed_root/backend_type/enabled(0|1)/status/status_detail`.
- `ControlRepository`: `.get_policy(tool)` (컬럼 `tool/max_nodes/procs_per_node/queue/default_priority/max_priority/preview_timeout_seconds/execution_timeout_seconds/enabled`), `.is_denied(*, requester, owner, groups)`(걸린 subject 또는 None), `.register_probe_target(username)`, `.try_acquire_lease(component, holder, lease_seconds)`, `.audit_entries(limit)`.
- `AgentsRepository`: `.fresh_reports(*, stale_seconds, now_iso=None)` → `[{node_name, reported_at, fresh, report}]`. report는 `{node_name, probed_at, mounts:[{storage_name, mount_path, status, reason, readable, writable}], tools:[{name, status}], identities:[{username, status, uid, gid, groups}], os:{...}}`.
- `dms.config.Settings`(frozen dataclass, `_parse_int`, `_SERVER_INT_KEYS`, `_is_placeholder`), `SettingsError(problems)`.
- `dms.controller`: `Loop`, `build_loops(settings, repos)`, `run_all_once`, `run_forever`.
- `dms.api.app.create_app(settings, db)`; `dms.api.auth`: `Identity(actor, role)`, `require_user`, `require_admin`.
- 스키마(존재): `data_jobs`(위 Phase1 컬럼), `plans(plan_id/request_id/job_id/state/created_at/updated_at)`, `events`.

## File Structure

```
src/dms/migrations.py                 # (수정) data_jobs에 worker_pool TEXT, precondition TEXT 컬럼 추가
src/dms/config.py                     # (수정) planner_interval_seconds + AGENT stale 재사용, 특권 설정
src/dms/repositories/data_jobs.py     # DataJobsRepository: plan/job emit, 상태 전이, 조회
src/dms/repositories/__init__.py      # (수정) .data_jobs 연결
src/dms/identity.py                   # ResolvedIdentity, IdentityResolver(프로토콜), StubIdentityResolver, resolve_job_identity, IdentityRejected
src/dms/placement.py                  # select_tool_and_candidates, resolve_fanout (순수 함수)
src/dms/planner.py                    # Planner.run_once (어드미션 + emit) — 통합
src/dms/api/routes_policies.py        # /api/admin/policies CRUD
src/dms/api/routes_denylist.py        # /api/admin/identity-denylist CRUD
src/dms/api/app.py                    # (수정) 라우터 2개 마운트 + app.state.identity_resolver
src/dms/controller.py                 # (수정) build_loops에 planner 루프
tests/test_repo_data_jobs.py
tests/test_identity.py
tests/test_placement.py
tests/test_planner.py
tests/test_api_policies.py
tests/test_api_denylist.py
tests/test_controller_planner.py
```

---

### Task 1: data_jobs 스키마 확장 + DataJobsRepository

**Files:**
- Modify: `src/dms/migrations.py` (data_jobs CREATE TABLE에 컬럼 2개 추가)
- Create: `src/dms/repositories/data_jobs.py`
- Modify: `src/dms/repositories/__init__.py`
- Test: `tests/test_repo_data_jobs.py`

**Interfaces:**
- Consumes: `Database`, `dump_json/load_json/utc_now_iso`, `DataJobState`, `TERMINAL_DATA_JOB_STATES`.
- Produces:
  - migrations: data_jobs에 `worker_pool TEXT`, `precondition TEXT` 컬럼 (CREATE TABLE 문에 추가 — greenfield, 모든 DB는 fresh migrate).
  - `DataJobsRepository(db)`:
    - `create_plan(request_id: str, *, actor: str) -> str` — plan_id(uuid hex), state `Planned`, plan 상태 전이 기록 (entity_kind `plan`, None→Planned)
    - `create_job(request_id: str, plan_id: str, *, operation: str, priority: str, storage_name=None, source_storage=None, destination_storage=None, source=None, destination=None, target=None, options: dict, tool: str, worker_pool: dict, precondition: dict, actor: str) -> str` — job_id(uuid hex), state `Pending`, data_job 상태 전이 기록 (None→Pending), 그리고 plan.job_id를 이 job_id로 갱신. 한 트랜잭션.
    - `get_job(job_id) -> dict | None` — options/worker_pool/precondition/result_summary/volcano_job_ref를 dict로 역직렬화
    - `list_jobs(*, request_id=None, limit=50) -> list[dict]` (created_at DESC, 역직렬화 포함)
    - `set_job_state(job_id, to_state: DataJobState, *, reason_code=None, actor) -> None` — 상태 갱신 + 전이 기록, 같은 트랜잭션
    - `job_transitions(job_id) -> list[dict]`

- [ ] **Step 1: 환경 셋업 + 실패 테스트**

먼저 venv: `python3 -m venv .venv && .venv/bin/pip install -q -e ".[test]"` 후 `.venv/bin/pytest -q`로 기존 139 passed 확인.

```python
# tests/test_repo_data_jobs.py
from dms.domain import DataJobState
from dms.repositories import Repositories


def _repos(db):
    return Repositories(db)


def _mk_request(repos):
    return repos.requests.create(
        operation="scan", requester_id="alice", actor="alice",
        resource_key="data.scan:s1:a:ff", payload={"storage": "s1", "target": "a"},
        priority="mid")


def test_create_plan_and_job_links(db):
    repos = _repos(db)
    rid = _mk_request(repos)
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    job_id = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name="s1",
        target="a", options={"summary_only": True}, tool="dscan",
        worker_pool={"tool": "dscan", "candidates": {"primary": ["n1"]}},
        precondition={"job_id": "x"}, actor="planner")
    job = repos.data_jobs.get_job(job_id)
    assert job["state"] == "Pending"
    assert job["tool"] == "dscan"
    assert job["options"] == {"summary_only": True}
    assert job["worker_pool"]["candidates"]["primary"] == ["n1"]
    plan = db.query_one("SELECT job_id, state FROM plans WHERE plan_id = :p", {"p": plan_id})
    assert plan["job_id"] == job_id and plan["state"] == "Planned"


def test_job_state_transitions_recorded(db):
    repos = _repos(db)
    rid = _mk_request(repos)
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    job_id = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name="s1",
        target="a", options={}, tool="dscan", worker_pool={}, precondition={},
        actor="planner")
    repos.data_jobs.set_job_state(job_id, DataJobState.PREFLIGHT, actor="stepper")
    repos.data_jobs.set_job_state(job_id, DataJobState.REJECTED,
                                 reason_code="posix_permission_denied", actor="stepper")
    ts = repos.data_jobs.job_transitions(job_id)
    assert [(t["from_state"], t["to_state"]) for t in ts] == [
        (None, "Pending"), ("Pending", "Preflight"), ("Preflight", "Rejected")]
    assert ts[2]["reason_code"] == "posix_permission_denied"
    assert repos.data_jobs.get_job(job_id)["reason_code"] == "posix_permission_denied"


def test_list_jobs_by_request(db):
    repos = _repos(db)
    rid = _mk_request(repos)
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    job_id = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name="s1",
        target="a", options={}, tool="dscan", worker_pool={}, precondition={},
        actor="planner")
    assert [j["job_id"] for j in repos.data_jobs.list_jobs(request_id=rid)] == [job_id]
    assert repos.data_jobs.list_jobs(request_id="nope") == []
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_repo_data_jobs.py -v`
Expected: FAIL — `AttributeError: ... data_jobs` (Repositories에 아직 없음)

- [ ] **Step 3: 구현**

migrations.py의 data_jobs CREATE TABLE에서 `result_summary TEXT,` 다음 줄에 컬럼 추가:
```
            result_summary TEXT,
            worker_pool TEXT,
            precondition TEXT,
            created_at TEXT NOT NULL,
```

```python
# src/dms/repositories/data_jobs.py
"""data_jobs + plans 저장소: planner가 emit하고 stepper(3b)가 전진시키는 잡 레코드."""
import uuid
from ..db import Database, dump_json, load_json, utc_now_iso
from ..domain import DataJobState, RequestState

_JSON_COLUMNS = ("options", "worker_pool", "precondition", "result_summary",
                 "volcano_job_ref")


class DataJobsRepository:
    def __init__(self, db: Database):
        self._db = db

    def _record_transition(self, entity_kind, entity_id, from_state, to_state,
                           reason_code, actor, at):
        self._db.execute(
            """INSERT INTO state_transitions (entity_kind, entity_id, from_state,
                   to_state, reason_code, actor, at)
               VALUES (:k, :id, :f, :t, :r, :actor, :at)""",
            {"k": entity_kind, "id": entity_id,
             "f": from_state.value if from_state is not None else None,
             "t": to_state.value, "r": reason_code, "actor": actor, "at": at})

    def create_plan(self, request_id, *, actor) -> str:
        plan_id = uuid.uuid4().hex
        now = utc_now_iso()
        with self._db.transaction():
            self._db.execute(
                """INSERT INTO plans (plan_id, request_id, job_id, state,
                       created_at, updated_at)
                   VALUES (:p, :r, NULL, 'Planned', :now, :now)""",
                {"p": plan_id, "r": request_id, "now": now})
            self._record_transition("plan", plan_id, None,
                                    RequestState.PLANNED, None, actor, now)
        return plan_id

    def create_job(self, request_id, plan_id, *, operation, priority,
                   storage_name=None, source_storage=None, destination_storage=None,
                   source=None, destination=None, target=None, options: dict, tool,
                   worker_pool: dict, precondition: dict, actor) -> str:
        job_id = uuid.uuid4().hex
        now = utc_now_iso()
        with self._db.transaction():
            self._db.execute(
                """INSERT INTO data_jobs (job_id, request_id, operation, tool,
                       storage_name, source_storage, destination_storage, source,
                       destination, target, options, priority, state, worker_pool,
                       precondition, created_at, updated_at)
                   VALUES (:j, :r, :op, :tool, :sn, :ss, :ds, :src, :dst, :tgt,
                       :opts, :pri, :state, :wp, :pre, :now, :now)""",
                {"j": job_id, "r": request_id, "op": operation, "tool": tool,
                 "sn": storage_name, "ss": source_storage, "ds": destination_storage,
                 "src": source, "dst": destination, "tgt": target,
                 "opts": dump_json(options), "pri": priority,
                 "state": DataJobState.PENDING.value, "wp": dump_json(worker_pool),
                 "pre": dump_json(precondition), "now": now})
            self._db.execute(
                "UPDATE plans SET job_id = :j, updated_at = :now WHERE plan_id = :p",
                {"j": job_id, "now": now, "p": plan_id})
            self._record_transition("data_job", job_id, None,
                                    DataJobState.PENDING, None, actor, now)
        return job_id

    def _hydrate(self, row):
        if row is None:
            return None
        for col in _JSON_COLUMNS:
            if col in row:
                row[col] = load_json(row[col])
        return row

    def get_job(self, job_id):
        return self._hydrate(self._db.query_one(
            "SELECT * FROM data_jobs WHERE job_id = :j", {"j": job_id}))

    def list_jobs(self, *, request_id=None, limit=50):
        if request_id is None:
            rows = self._db.query(
                "SELECT * FROM data_jobs ORDER BY created_at DESC, job_id DESC LIMIT :n",
                {"n": limit})
        else:
            rows = self._db.query(
                """SELECT * FROM data_jobs WHERE request_id = :r
                   ORDER BY created_at DESC, job_id DESC LIMIT :n""",
                {"r": request_id, "n": limit})
        return [self._hydrate(r) for r in rows]

    def set_job_state(self, job_id, to_state: DataJobState, *, reason_code=None, actor):
        now = utc_now_iso()
        with self._db.transaction():
            current = self._db.query_one(
                "SELECT state FROM data_jobs WHERE job_id = :j", {"j": job_id})
            if current is None:
                raise KeyError(job_id)
            self._db.execute(
                """UPDATE data_jobs SET state = :s, reason_code = :rc, updated_at = :now
                   WHERE job_id = :j""",
                {"s": to_state.value, "rc": reason_code, "now": now, "j": job_id})
            self._record_transition("data_job", job_id, DataJobState(current["state"]),
                                    to_state, reason_code, actor, now)

    def job_transitions(self, job_id):
        return self._db.query(
            """SELECT * FROM state_transitions
               WHERE entity_kind = 'data_job' AND entity_id = :j ORDER BY id""",
            {"j": job_id})
```

`src/dms/repositories/__init__.py`: `from .data_jobs import DataJobsRepository` + `self.data_jobs = DataJobsRepository(db)`.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_repo_data_jobs.py tests/test_migrations.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/migrations.py src/dms/repositories/ tests/test_repo_data_jobs.py
git commit -m "feat: DataJobsRepository (plan/job emit, 잡 상태 전이, worker_pool 컬럼)"
```

---

### Task 2: 실행 신원 모델 + resolver (`identity.py` 1/2)

**Files:**
- Create: `src/dms/identity.py`
- Test: `tests/test_identity.py`

**Interfaces:**
- Consumes: 없음 (순수 모듈, DB 모름).
- Produces:
  - `@dataclass(frozen=True) ResolvedIdentity`: `username: str`, `uid: int`, `gid: int`, `groups: tuple[str, ...]`, `privileged: bool`
  - `class IdentityResolver(Protocol)`: `resolve(self, username: str) -> ResolvedIdentity | None` — 없으면 None, 조회 불가(백엔드 장애)면 예외
  - `class IdentityUnavailable(Exception)` — LDAP 장애용 (resolver가 raise)
  - `class StubIdentityResolver`: `__init__(self, users: dict[str, ResolvedIdentity], *, unavailable: bool = False)`; `resolve` — unavailable면 `IdentityUnavailable`, users에 있으면 반환, 없으면 None
  - `class IdentityRejected(Exception)`: `reason_code: str`, `detail: str` (도메인 거부용)

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_identity.py
import pytest
from dms.identity import (
    IdentityRejected, IdentityUnavailable, ResolvedIdentity, StubIdentityResolver)


def test_resolved_identity_is_frozen():
    ident = ResolvedIdentity("alice", 10001, 10000, ("dmsusers",), False)
    assert ident.uid == 10001 and ident.groups == ("dmsusers",)
    with pytest.raises(Exception):
        ident.uid = 0  # frozen


def test_stub_resolver_hit_miss_unavailable():
    ident = ResolvedIdentity("alice", 10001, 10000, ("dmsusers",), False)
    r = StubIdentityResolver({"alice": ident})
    assert r.resolve("alice") is ident
    assert r.resolve("ghost") is None
    down = StubIdentityResolver({}, unavailable=True)
    with pytest.raises(IdentityUnavailable):
        down.resolve("alice")


def test_identity_rejected_carries_reason():
    err = IdentityRejected("identity_denied", "mallory")
    assert err.reason_code == "identity_denied" and "mallory" in str(err)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_identity.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 구현**

```python
# src/dms/identity.py
"""실행 신원 모델. LDAP 조회는 주입된 resolver 뒤에 있고, 이 모듈은 오케스트레이션만 한다."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ResolvedIdentity:
    username: str
    uid: int
    gid: int
    groups: tuple[str, ...]
    privileged: bool


class IdentityUnavailable(Exception):
    """resolver 백엔드(LDAP)가 조회 불가 — fail-closed 대상."""


class IdentityResolver(Protocol):
    def resolve(self, username: str) -> "ResolvedIdentity | None":
        ...


class StubIdentityResolver:
    def __init__(self, users: dict, *, unavailable: bool = False):
        self._users = users
        self._unavailable = unavailable

    def resolve(self, username: str):
        if self._unavailable:
            raise IdentityUnavailable(username)
        return self._users.get(username)


class IdentityRejected(Exception):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_identity.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/identity.py tests/test_identity.py
git commit -m "feat: 실행 신원 모델 (ResolvedIdentity, resolver 프로토콜, stub)"
```

---

### Task 3: 신원 해석 오케스트레이션 (`identity.py` 2/2)

**Files:**
- Modify: `src/dms/identity.py`
- Test: `tests/test_identity.py`에 추가

**Interfaces:**
- Consumes: Task 2 심볼, `ControlRepository`(`.is_denied`, `.register_probe_target`).
- Produces:
  - `resolve_job_identity(control, resolver, *, requester_id: str, owner_username: str | None, allow_privileged: bool, privileged_requesters: frozenset[str]) -> ResolvedIdentity` — 순서(스펙 §5):
    1. `owner = (owner_username or requester_id).strip()`
    2. denylist 1차: `control.is_denied(requester=requester_id, owner=owner, groups=[])` → 걸리면 `IdentityRejected("identity_denied", subject)`
    3. privileged: `allow_privileged and owner in privileged_requesters` → `ResolvedIdentity(owner, 0, 0, (), True)` 반환 (LDAP·uid floor 우회)
    4. `resolver is None` → `IdentityRejected("ldap_not_configured")`
    5. `resolver.resolve(owner)` 예외(`IdentityUnavailable`) → `IdentityRejected("ldap_unavailable", str(exc)[:200])`
    6. 결과 None → `IdentityRejected("ldap_identity_not_found", owner)`
    7. denylist 2차 (LDAP가 준 groups로): 걸리면 `IdentityRejected("identity_denied", subject)`
    8. `control.register_probe_target(owner)` → `ResolvedIdentity(owner, uid, gid, groups, False)` 반환
  - **uid 하한 검사 없음.**

- [ ] **Step 1: 실패 테스트 (test_identity.py에 추가)**

```python
from dms.identity import resolve_job_identity
from dms.repositories.control import ControlRepository

ALICE = ResolvedIdentity("alice", 10001, 10000, ("dmsusers",), False)


def _control(db):
    return ControlRepository(db)


def test_resolve_normal_registers_probe(db):
    control = _control(db)
    resolver = StubIdentityResolver({"alice": ALICE})
    out = resolve_job_identity(control, resolver, requester_id="alice",
                               owner_username=None, allow_privileged=False,
                               privileged_requesters=frozenset())
    assert out == ALICE and out.privileged is False
    assert control.probe_targets(ttl_seconds=3600) == ["alice"]


def test_owner_username_override(db):
    control = _control(db)
    bob = ResolvedIdentity("bob", 10002, 10000, (), False)
    out = resolve_job_identity(control, StubIdentityResolver({"bob": bob}),
                               requester_id="admin", owner_username="  bob  ",
                               allow_privileged=False, privileged_requesters=frozenset())
    assert out.username == "bob"


def test_denylist_blocks_before_privileged(db):
    control = _control(db)
    control.deny("owner", "root-op", reason="incident", actor="admin")
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(control, StubIdentityResolver({}),
                             requester_id="root-op", owner_username=None,
                             allow_privileged=True,
                             privileged_requesters=frozenset({"root-op"}))
    assert e.value.reason_code == "identity_denied"


def test_privileged_path_synthesizes_root(db):
    control = _control(db)
    out = resolve_job_identity(control, None, requester_id="ops",
                               owner_username="victim", allow_privileged=True,
                               privileged_requesters=frozenset({"victim"}))
    assert out.privileged and out.uid == 0 and out.gid == 0


def test_ldap_not_configured_and_unavailable_and_missing(db):
    control = _control(db)
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(control, None, requester_id="alice", owner_username=None,
                             allow_privileged=False, privileged_requesters=frozenset())
    assert e.value.reason_code == "ldap_not_configured"
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(control, StubIdentityResolver({}, unavailable=True),
                             requester_id="alice", owner_username=None,
                             allow_privileged=False, privileged_requesters=frozenset())
    assert e.value.reason_code == "ldap_unavailable"
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(control, StubIdentityResolver({}), requester_id="ghost",
                             owner_username=None, allow_privileged=False,
                             privileged_requesters=frozenset())
    assert e.value.reason_code == "ldap_identity_not_found"


def test_denylist_second_pass_on_groups(db):
    control = _control(db)
    control.deny("group", "dmsusers", reason=None, actor="admin")
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(control, StubIdentityResolver({"alice": ALICE}),
                             requester_id="alice", owner_username=None,
                             allow_privileged=False, privileged_requesters=frozenset())
    assert e.value.reason_code == "identity_denied"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_identity.py -v`
Expected: 새 테스트 FAIL — ImportError

- [ ] **Step 3: 구현 (identity.py에 추가)**

```python
def resolve_job_identity(control, resolver, *, requester_id, owner_username,
                         allow_privileged, privileged_requesters) -> ResolvedIdentity:
    owner = (owner_username or requester_id).strip()
    denied = control.is_denied(requester=requester_id, owner=owner, groups=[])
    if denied:
        raise IdentityRejected("identity_denied", denied)
    if allow_privileged and owner in privileged_requesters:
        return ResolvedIdentity(owner, 0, 0, (), True)
    if resolver is None:
        raise IdentityRejected("ldap_not_configured")
    try:
        resolved = resolver.resolve(owner)
    except IdentityUnavailable as exc:
        raise IdentityRejected("ldap_unavailable", str(exc)[:200])
    if resolved is None:
        raise IdentityRejected("ldap_identity_not_found", owner)
    denied = control.is_denied(requester=requester_id, owner=owner,
                               groups=list(resolved.groups))
    if denied:
        raise IdentityRejected("identity_denied", denied)
    control.register_probe_target(owner)
    return ResolvedIdentity(owner, resolved.uid, resolved.gid,
                            tuple(resolved.groups), False)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_identity.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/identity.py tests/test_identity.py
git commit -m "feat: 신원 해석 오케스트레이션 (denylist 최우선, privileged root, probe 등록)"
```

---

### Task 4: 도구 선택 + 후보 노드 (`placement.py` 1/2)

**Files:**
- Create: `src/dms/placement.py`
- Test: `tests/test_placement.py`

**Interfaces:**
- Consumes: `Operation`, `Tool` (`dms.domain`).
- Produces (순수 함수, 신선 리포트 리스트를 인자로 받음):
  - `eligible_nodes(fresh_reports: list[dict], storage_name: str, *, tool: str, owner: str, privileged: bool, require_writable: bool) -> tuple[list[str], dict[str, str]]` — 각 노드에 대해: 해당 storage의 mount가 있고 status Ready여야 함(`missing_target_mount`), require_writable이면 mount `writable is False`는 탈락(`target_mount_read_only`), tool이 node의 tools에 Ready로 있어야 함(`missing_tool:<tool>`), privileged 아니면 owner의 identity가 Ready여야 함(`identity_not_ready_on_node`). 반환: (적격 노드 정렬 리스트, {node: 첫 탈락사유}). node_name 오름차순.
  - `select_tool_and_candidates(operation: str, fresh_reports, *, storage_name=None, source_storage=None, destination_storage=None, owner: str, privileged: bool) -> dict` — 결과 `{tool, candidates, rejections}`:
    - scan: tool `dscan`, candidates `{"primary": [...]}` (storage_name, writable 불필요)
    - rm: tool `drm`, candidates `{"primary": [...]}` (storage_name, writable 필요)
    - sync: source·destination 마운트를 **모두 가진 노드**(양쪽 eligible의 교집합, tool은 dsync 기준) ≥1이면 tool `dsync`, candidates `{"primary": 교집합}`. 아니면 source(nsync)·destination(nsync writable) 각각 ≥1이면 tool `nsync`, candidates `{"source": [...], "destination": [...]}`. 둘 다 아니면 `IdentityRejected`가 아니라 `PlacementError("no_ready_sync_candidate")` 또는 scan/rm의 `no_eligible_nodes`.
  - `class PlacementError(Exception)`: `reason_code`, `detail`. 후보 0이면 raise (scan/rm: `no_eligible_nodes`; sync: `no_ready_sync_candidate`).
  - `rejections`는 디버깅용 노드별 사유 맵 (planner가 이벤트에 기록).

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_placement.py
import pytest
from dms.placement import PlacementError, eligible_nodes, select_tool_and_candidates


def _report(node, *, mounts, tools=("dscan", "dsync", "nsync", "drm"),
            identities=("alice",)):
    return {"node_name": node,
            "report": {
                "mounts": mounts,
                "tools": [{"name": t, "status": "Ready"} for t in tools],
                "identities": [{"username": u, "status": "Ready"} for u in identities]}}


def _mount(name, *, status="Ready", writable=True):
    return {"storage_name": name, "mount_path": f"/mnt/{name}",
            "status": status, "writable": writable}


def test_eligible_nodes_filters_and_reasons():
    reports = [
        _report("n1", mounts=[_mount("s1")]),
        _report("n2", mounts=[_mount("s1", status="Missing")]),
        _report("n3", mounts=[_mount("s1")], tools=("dsync",)),      # dscan 없음
        _report("n4", mounts=[_mount("s1")], identities=("bob",)),   # alice identity 없음
    ]
    ok, reasons = eligible_nodes(reports, "s1", tool="dscan", owner="alice",
                                 privileged=False, require_writable=False)
    assert ok == ["n1"]
    assert reasons["n2"] == "missing_target_mount"
    assert reasons["n3"] == "missing_tool:dscan"
    assert reasons["n4"] == "identity_not_ready_on_node"


def test_require_writable_rejects_ro_mount():
    reports = [_report("n1", mounts=[_mount("s1", writable=False)])]
    ok, reasons = eligible_nodes(reports, "s1", tool="drm", owner="alice",
                                 privileged=False, require_writable=True)
    assert ok == [] and reasons["n1"] == "target_mount_read_only"


def test_privileged_skips_identity_check():
    reports = [_report("n1", mounts=[_mount("s1")], identities=())]
    ok, _ = eligible_nodes(reports, "s1", tool="dscan", owner="root",
                           privileged=True, require_writable=False)
    assert ok == ["n1"]


def test_select_scan_and_rm():
    reports = [_report("n1", mounts=[_mount("s1")])]
    scan = select_tool_and_candidates("scan", reports, storage_name="s1",
                                      owner="alice", privileged=False)
    assert scan["tool"] == "dscan" and scan["candidates"]["primary"] == ["n1"]
    rm = select_tool_and_candidates("rm", reports, storage_name="s1",
                                    owner="alice", privileged=False)
    assert rm["tool"] == "drm"


def test_select_sync_dsync_when_colocated():
    reports = [_report("n1", mounts=[_mount("src"), _mount("dst")])]
    out = select_tool_and_candidates("sync", reports, source_storage="src",
                                     destination_storage="dst", owner="alice",
                                     privileged=False)
    assert out["tool"] == "dsync" and out["candidates"]["primary"] == ["n1"]


def test_select_sync_nsync_when_disjoint():
    reports = [
        _report("n1", mounts=[_mount("src")]),
        _report("n2", mounts=[_mount("dst")]),
    ]
    out = select_tool_and_candidates("sync", reports, source_storage="src",
                                     destination_storage="dst", owner="alice",
                                     privileged=False)
    assert out["tool"] == "nsync"
    assert out["candidates"]["source"] == ["n1"]
    assert out["candidates"]["destination"] == ["n2"]


def test_no_candidates_raise():
    with pytest.raises(PlacementError) as e:
        select_tool_and_candidates("scan", [], storage_name="s1", owner="alice",
                                   privileged=False)
    assert e.value.reason_code == "no_eligible_nodes"
    reports = [_report("n1", mounts=[_mount("src")])]  # dst 없음
    with pytest.raises(PlacementError) as e:
        select_tool_and_candidates("sync", reports, source_storage="src",
                                   destination_storage="dst", owner="alice",
                                   privileged=False)
    assert e.value.reason_code == "no_ready_sync_candidate"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_placement.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 구현**

```python
# src/dms/placement.py
"""도구 선택 + 후보 노드 산정. 신선한 에이전트 증거(리포트)만으로 판단하는 순수 함수."""


class PlacementError(Exception):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


def _mount_for(report, storage_name):
    for mount in report.get("mounts", []):
        if mount.get("storage_name") == storage_name:
            return mount
    return None


def _tool_ready(report, tool):
    return any(t.get("name") == tool and t.get("status") == "Ready"
               for t in report.get("tools", []))


def _identity_ready(report, owner):
    return any(i.get("username") == owner and i.get("status") == "Ready"
               for i in report.get("identities", []))


def eligible_nodes(fresh_reports, storage_name, *, tool, owner, privileged,
                   require_writable):
    ok, reasons = [], {}
    for entry in fresh_reports:
        node = entry["node_name"]
        report = entry.get("report") or {}
        mount = _mount_for(report, storage_name)
        if mount is None or mount.get("status") != "Ready":
            reasons[node] = "missing_target_mount"
            continue
        if require_writable and mount.get("writable") is False:
            reasons[node] = "target_mount_read_only"
            continue
        if not _tool_ready(report, tool):
            reasons[node] = f"missing_tool:{tool}"
            continue
        if not privileged and not _identity_ready(report, owner):
            reasons[node] = "identity_not_ready_on_node"
            continue
        ok.append(node)
    return sorted(ok), reasons


def select_tool_and_candidates(operation, fresh_reports, *, storage_name=None,
                               source_storage=None, destination_storage=None,
                               owner, privileged):
    if operation == "scan":
        nodes, rej = eligible_nodes(fresh_reports, storage_name, tool="dscan",
                                    owner=owner, privileged=privileged,
                                    require_writable=False)
        if not nodes:
            raise PlacementError("no_eligible_nodes", storage_name)
        return {"tool": "dscan", "candidates": {"primary": nodes}, "rejections": rej}
    if operation == "rm":
        nodes, rej = eligible_nodes(fresh_reports, storage_name, tool="drm",
                                    owner=owner, privileged=privileged,
                                    require_writable=True)
        if not nodes:
            raise PlacementError("no_eligible_nodes", storage_name)
        return {"tool": "drm", "candidates": {"primary": nodes}, "rejections": rej}
    if operation == "sync":
        src_dsync, rej_s = eligible_nodes(fresh_reports, source_storage, tool="dsync",
                                          owner=owner, privileged=privileged,
                                          require_writable=False)
        dst_dsync, rej_d = eligible_nodes(fresh_reports, destination_storage,
                                          tool="dsync", owner=owner,
                                          privileged=privileged, require_writable=True)
        colocated = sorted(set(src_dsync) & set(dst_dsync))
        rejections = {"source": rej_s, "destination": rej_d}
        if colocated:
            return {"tool": "dsync", "candidates": {"primary": colocated},
                    "rejections": rejections}
        src_n, _ = eligible_nodes(fresh_reports, source_storage, tool="nsync",
                                  owner=owner, privileged=privileged,
                                  require_writable=False)
        dst_n, _ = eligible_nodes(fresh_reports, destination_storage, tool="nsync",
                                  owner=owner, privileged=privileged,
                                  require_writable=True)
        if src_n and dst_n:
            return {"tool": "nsync",
                    "candidates": {"source": src_n, "destination": dst_n},
                    "rejections": rejections}
        raise PlacementError("no_ready_sync_candidate")
    raise PlacementError("invalid_operation", operation)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_placement.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dms/placement.py tests/test_placement.py
git commit -m "feat: 도구 자동 선택 + 후보 노드 산정 (dsync/nsync, 노드별 탈락 사유)"
```

---

### Task 5: 정책 fan-out 산정 (`placement.py` 2/2)

**Files:**
- Modify: `src/dms/placement.py`
- Test: `tests/test_placement.py`에 추가

**Interfaces:**
- Consumes: Task 4 모듈.
- Produces:
  - `TOOL_TO_POLICY = {"dscan": "scan", "drm": "rm", "dsync": "dsync", "nsync": "nsync"}`
  - `resolve_fanout(policy: dict | None, candidates: dict, *, priority: str) -> dict` — policy 없음/비활성이면 `PlacementError("missing_policy"|"policy_disabled")`. 반환 `{node_count, process_count, queue, priority_class}`:
    - dsync/scan/rm(primary): `node_count = min(len(primary), policy["max_nodes"])`, `process_count = node_count * policy["procs_per_node"]`
    - nsync(source/destination): `source_count = min(len(source), max_nodes)`, `destination_count = min(len(destination), max_nodes)`, `process_count = (source_count + destination_count) * procs_per_node`, node_count는 합. 반환에 `source_count`/`destination_count` 포함.
    - priority: 요청 priority가 정책 `max_priority`를 초과하면 max로 clamp(`PRIORITIES` 인덱스 비교). `priority_class = PRIORITY_CLASS[clamped]`, queue = `policy["queue"]`.

- [ ] **Step 1: 실패 테스트 (추가)**

```python
from dms.placement import TOOL_TO_POLICY, resolve_fanout

POLICY = {"max_nodes": 3, "procs_per_node": 8, "queue": "dms-data",
          "default_priority": "mid", "max_priority": "high",
          "execution_timeout_seconds": 3600, "enabled": 1}


def test_tool_to_policy_map():
    assert TOOL_TO_POLICY == {"dscan": "scan", "drm": "rm",
                              "dsync": "dsync", "nsync": "nsync"}


def test_fanout_primary_clamps_to_max():
    out = resolve_fanout(POLICY, {"primary": ["n1", "n2", "n3", "n4", "n5"]},
                         priority="mid")
    assert out["node_count"] == 3 and out["process_count"] == 24
    assert out["queue"] == "dms-data" and out["priority_class"] == "dms-mid"


def test_fanout_uses_all_when_below_max():
    out = resolve_fanout(POLICY, {"primary": ["n1"]}, priority="mid")
    assert out["node_count"] == 1 and out["process_count"] == 8


def test_fanout_nsync_roles():
    out = resolve_fanout(POLICY, {"source": ["n1", "n2", "n3", "n4"],
                                  "destination": ["n5", "n6"]}, priority="low")
    assert out["source_count"] == 3 and out["destination_count"] == 2
    assert out["node_count"] == 5 and out["process_count"] == 40
    assert out["priority_class"] == "dms-low"


def test_priority_clamped_to_policy_max():
    capped = {**POLICY, "max_priority": "mid"}
    out = resolve_fanout(capped, {"primary": ["n1"]}, priority="high")
    assert out["priority_class"] == "dms-mid"


def test_missing_and_disabled_policy():
    import pytest
    from dms.placement import PlacementError
    with pytest.raises(PlacementError) as e:
        resolve_fanout(None, {"primary": ["n1"]}, priority="mid")
    assert e.value.reason_code == "missing_policy"
    with pytest.raises(PlacementError) as e:
        resolve_fanout({**POLICY, "enabled": 0}, {"primary": ["n1"]}, priority="mid")
    assert e.value.reason_code == "policy_disabled"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_placement.py -v`
Expected: 새 테스트 FAIL — ImportError

- [ ] **Step 3: 구현 (placement.py에 추가)**

```python
from .domain import PRIORITIES, PRIORITY_CLASS

TOOL_TO_POLICY = {"dscan": "scan", "drm": "rm", "dsync": "dsync", "nsync": "nsync"}


def _clamp_priority(requested, policy_max):
    if PRIORITIES.index(requested) <= PRIORITIES.index(policy_max):
        return requested
    return policy_max


def resolve_fanout(policy, candidates, *, priority):
    if policy is None:
        raise PlacementError("missing_policy")
    if not policy.get("enabled"):
        raise PlacementError("policy_disabled")
    max_nodes = policy["max_nodes"]
    per_node = policy["procs_per_node"]
    clamped = _clamp_priority(priority, policy["max_priority"])
    common = {"queue": policy["queue"], "priority_class": PRIORITY_CLASS[clamped]}
    if "primary" in candidates:
        node_count = min(len(candidates["primary"]), max_nodes)
        return {**common, "node_count": node_count,
                "process_count": node_count * per_node}
    source_count = min(len(candidates["source"]), max_nodes)
    destination_count = min(len(candidates["destination"]), max_nodes)
    return {**common, "source_count": source_count,
            "destination_count": destination_count,
            "node_count": source_count + destination_count,
            "process_count": (source_count + destination_count) * per_node}
```

(`PRIORITIES`/`PRIORITY_CLASS` import는 파일 상단 import 블록에 정리.)

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_placement.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/placement.py tests/test_placement.py
git commit -m "feat: 정책 fan-out 산정 (상한 clamp, nsync role별, priority clamp)"
```

---

### Task 6: 설정 확장 (`config.py`)

**Files:**
- Modify: `src/dms/config.py`
- Test: `tests/test_config_phase3.py`

**Interfaces:**
- Consumes: 기존 `Settings`, `_parse_int`, `_SERVER_INT_KEYS`, `_is_placeholder`, `SettingsError`.
- Produces:
  - `Settings`에 필드 추가: `planner_interval_seconds: int = 10` (`DMS_PLANNER_INTERVAL_SECONDS`, `_SERVER_INT_KEYS`에 추가), `allow_privileged_requesters: bool = False` (`DMS_ALLOW_PRIVILEGED_REQUESTERS`, `"true"`/`"1"`만 True), `privileged_requesters: frozenset[str] = frozenset()` (`DMS_PRIVILEGED_REQUESTERS`, 콤마 구분, 공백 trim, 빈 항목 제거)
  - `_parse_bool(environ, key, default) -> bool`, `_parse_csv_set(environ, key) -> frozenset[str]` 모듈 헬퍼

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_config_phase3.py
from dms.config import Settings

VALID = {"DMS_DATABASE_URL": "sqlite:///tmp/dms.db", "DMS_SHARED_TOKEN": "tok",
         "DMS_ADMIN_TOKEN": "adm", "DMS_SESSION_SECRET": "sess"}


def test_planner_defaults():
    s = Settings.from_env(VALID)
    assert s.planner_interval_seconds == 10
    assert s.allow_privileged_requesters is False
    assert s.privileged_requesters == frozenset()


def test_privileged_settings_parsed():
    s = Settings.from_env({**VALID, "DMS_ALLOW_PRIVILEGED_REQUESTERS": "true",
                           "DMS_PRIVILEGED_REQUESTERS": " ops , backup , ",
                           "DMS_PLANNER_INTERVAL_SECONDS": "5"})
    assert s.allow_privileged_requesters is True
    assert s.privileged_requesters == frozenset({"ops", "backup"})
    assert s.planner_interval_seconds == 5


def test_allow_privileged_false_for_other_values():
    s = Settings.from_env({**VALID, "DMS_ALLOW_PRIVILEGED_REQUESTERS": "yes"})
    assert s.allow_privileged_requesters is False  # "true"/"1"만 True
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_config_phase3.py -v`
Expected: FAIL — AttributeError

- [ ] **Step 3: 구현 (config.py 수정)**

`_SERVER_INT_KEYS` 튜플에 항목 추가:
```python
    ("DMS_PLANNER_INTERVAL_SECONDS", "planner_interval_seconds", 10),
```
`Settings` dataclass에 필드 추가 (기존 int 필드들 뒤):
```python
    planner_interval_seconds: int = 10
    allow_privileged_requesters: bool = False
    privileged_requesters: frozenset = frozenset()
```
헬퍼 + `from_env`에서 사용:
```python
def _parse_bool(environ, key, default=False):
    value = environ.get(key)
    if value is None:
        return default
    return value.strip().lower() in ("true", "1")


def _parse_csv_set(environ, key):
    raw = environ.get(key, "")
    return frozenset(item.strip() for item in raw.split(",") if item.strip())
```
`from_env`의 `return cls(...)`에 추가 (int extra dict 이후):
```python
            allow_privileged_requesters=_parse_bool(
                environ, "DMS_ALLOW_PRIVILEGED_REQUESTERS"),
            privileged_requesters=_parse_csv_set(environ, "DMS_PRIVILEGED_REQUESTERS"),
```
(`planner_interval_seconds`는 `_SERVER_INT_KEYS` extra dict로 자동 포함.)

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_config_phase3.py tests/test_config.py tests/test_config_phase2.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/config.py tests/test_config_phase3.py
git commit -m "feat: planner 주기 + 특권 요청자 설정"
```

---

### Task 7: Planner (`planner.py`) — 어드미션 + emit 통합

**Files:**
- Create: `src/dms/planner.py`
- Test: `tests/test_planner.py`

**Interfaces:**
- Consumes: `Repositories`, `resolve_job_identity`/`IdentityRejected`(identity), `select_tool_and_candidates`/`resolve_fanout`/`PlacementError`/`TOOL_TO_POLICY`(placement), `RequestState`, `Settings`.
- Produces:
  - `Planner(repos, resolver, *, settings)` — resolver는 `IdentityResolver | None`; settings에서 `agent_report_stale_seconds`, `allow_privileged_requesters`, `privileged_requesters` 사용.
  - `.run_once(limit: int = 50, *, now_iso: str | None = None) -> dict[str, str]` — Pending 요청을 commit_order 순으로 처리, `{request_id: outcome}` 반환 (outcome: `planned`/`rejected:<reason>`/`conflict`). 각 요청:
    1. `find_active(resource_key)`가 **자기보다 앞선(commit_order 작은) 비터미널 요청**이면 → 요청 `Conflict` + result(reason `resource_conflict`). (자기 자신이 유일하면 진행.)
    2. 필요 스토리지 목록(scan/rm: [storage], sync: [source, dest]) 각각: `repos.storages.get(name)` 없음 → `Rejected(storage_missing)`; `enabled==0` → `Rejected(storage_disabled)`; `status not in {"Ready","Degraded"}` → `Rejected(storage_not_ready)`.
    3. `resolve_job_identity(...)` → `IdentityRejected` 시 `Rejected(e.reason_code)`.
    4. `fresh = repos.agents.fresh_reports(stale_seconds=..., now_iso=now_iso)`; `select_tool_and_candidates(...)` → `PlacementError` 시 `Rejected(e.reason_code)`.
    5. `policy = repos.control.get_policy(TOOL_TO_POLICY[tool])`; `resolve_fanout(policy, candidates, priority=req.priority)` → `PlacementError` 시 `Rejected(e.reason_code)`.
    6. 성공: `plan_id = create_plan(...)`; `worker_pool = {tool, identity(asdict), candidates, **fanout}`; `precondition = {job_id 없음→create_job이 채움, requester_id, owner, normalized paths}`; `create_job(...)`; `requests.set_state(Planned)`. outcome `planned`.
  - 거부/충돌 시 `requests.set_state(터미널)` + `requests.record_result(...)` (한 요청의 실패가 다음 요청을 막지 않음 — 요청별 try/except, 예상 못한 예외는 로그 후 그 요청만 스킵).
  - identity를 dict로 저장: `{"username","uid","gid","groups"(list),"privileged"}`.

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_planner.py
import pytest
from dms.identity import ResolvedIdentity, StubIdentityResolver
from dms.planner import Planner
from dms.repositories import Repositories

NOW = "2026-08-02T10:00:00Z"
ALICE = ResolvedIdentity("alice", 10001, 10000, ("dmsusers",), False)


class _Settings:
    agent_report_stale_seconds = 300
    allow_privileged_requesters = False
    privileged_requesters = frozenset()


def _seed_storage(repos, name="s1", status="Ready"):
    repos.storages.create(storage_name=name, mount_path=f"/mnt/{name}",
                          managed_root=f"/mnt/{name}/dms", backend_type="cephfs",
                          actor="admin")
    repos.storages.set_status(name, status, "ready_nodes=1")


def _seed_policy(repos, tool="scan"):
    repos.control.upsert_policy(tool, max_nodes=3, procs_per_node=8, queue="dms-data",
                                default_priority="mid", max_priority="high",
                                preview_timeout_seconds=3600,
                                execution_timeout_seconds=3600, enabled=True,
                                actor="admin")


def _seed_report(repos, node="n1", storage="s1", user="alice"):
    repos.agents.ingest(node, {
        "node_name": node,
        "mounts": [{"storage_name": storage, "mount_path": f"/mnt/{storage}",
                    "status": "Ready", "writable": True}],
        "tools": [{"name": t, "status": "Ready"}
                  for t in ("dscan", "dsync", "nsync", "drm")],
        "identities": [{"username": user, "status": "Ready"}]},
        reported_at="2026-08-02T09:59:00Z")


def _scan_request(repos, requester="alice", key="data.scan:s1:a:ff"):
    return repos.requests.create(
        operation="scan", requester_id=requester, actor=requester,
        resource_key=key, payload={"storage": "s1", "target": "a",
                                   "options": {}, "owner_username": None},
        priority="mid")


def _planner(repos, resolver=None):
    return Planner(repos, resolver or StubIdentityResolver({"alice": ALICE}),
                   settings=_Settings())


def test_happy_path_plans_scan(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos)
    result = _planner(repos).run_once(now_iso=NOW)
    assert result[rid] == "planned"
    assert repos.requests.get(rid)["state"] == "Planned"
    jobs = repos.data_jobs.list_jobs(request_id=rid)
    assert len(jobs) == 1
    job = jobs[0]
    assert job["tool"] == "dscan" and job["state"] == "Pending"
    assert job["worker_pool"]["candidates"]["primary"] == ["n1"]
    assert job["worker_pool"]["identity"]["uid"] == 10001
    assert job["worker_pool"]["process_count"] == 8


def test_storage_missing_disabled_not_ready(db):
    repos = Repositories(db)
    _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:storage_missing"
    assert repos.requests.get(rid)["state"] == "Rejected"

    # storage가 존재하지만 status가 Unknown이면 not_ready
    _seed_storage(repos, status="Unknown")
    rid2 = _scan_request(repos, key="data.scan:s1:b:ff")
    assert _planner(repos).run_once(now_iso=NOW)[rid2] == "rejected:storage_not_ready"


def test_conflict_on_prior_active(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    first = _scan_request(repos, key="dup")
    second = _scan_request(repos, key="dup")
    result = _planner(repos).run_once(now_iso=NOW)
    assert result[first] == "planned"
    assert result[second] == "conflict"
    assert repos.requests.get(second)["state"] == "Conflict"


def test_identity_rejection(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos)
    planner = _planner(repos, resolver=StubIdentityResolver({}))  # alice 없음
    assert planner.run_once(now_iso=NOW)[rid] == "rejected:ldap_identity_not_found"


def test_missing_policy_rejects(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_report(repos)  # 정책 없음
    rid = _scan_request(repos)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:missing_policy"


def test_no_candidates_when_no_fresh_report(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos)  # 리포트 없음
    rid = _scan_request(repos)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:no_eligible_nodes"


def test_sync_selects_nsync(db):
    repos = Repositories(db)
    _seed_storage(repos, "src"); _seed_storage(repos, "dst")
    _seed_policy(repos, "nsync")
    repos.agents.ingest("n1", {"node_name": "n1",
        "mounts": [{"storage_name": "src", "mount_path": "/mnt/src",
                    "status": "Ready", "writable": True}],
        "tools": [{"name": "nsync", "status": "Ready"},
                  {"name": "dsync", "status": "Ready"}],
        "identities": [{"username": "alice", "status": "Ready"}]},
        reported_at="2026-08-02T09:59:00Z")
    repos.agents.ingest("n2", {"node_name": "n2",
        "mounts": [{"storage_name": "dst", "mount_path": "/mnt/dst",
                    "status": "Ready", "writable": True}],
        "tools": [{"name": "nsync", "status": "Ready"},
                  {"name": "dsync", "status": "Ready"}],
        "identities": [{"username": "alice", "status": "Ready"}]},
        reported_at="2026-08-02T09:59:00Z")
    rid = repos.requests.create(operation="sync", requester_id="alice", actor="alice",
        resource_key="data.sync:src:a:dst:b:ff",
        payload={"source_storage": "src", "source": "a",
                 "destination_storage": "dst", "destination": "b",
                 "options": {}, "owner_username": None}, priority="mid")
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "planned"
    job = repos.data_jobs.list_jobs(request_id=rid)[0]
    assert job["tool"] == "nsync"
    assert job["worker_pool"]["candidates"]["source"] == ["n1"]
    assert job["worker_pool"]["candidates"]["destination"] == ["n2"]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_planner.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 구현**

```python
# src/dms/planner.py
"""planner: Pending 요청을 어드미션 게이트를 거쳐 계획된 data_job으로 emit하는 루프 본체."""
import sys
from dataclasses import asdict

from .domain import Operation, RequestState
from .identity import IdentityRejected, resolve_job_identity
from .placement import (
    PlacementError, TOOL_TO_POLICY, resolve_fanout, select_tool_and_candidates)


def _required_storages(operation, payload):
    if operation == Operation.SYNC.value:
        return [payload["source_storage"], payload["destination_storage"]]
    return [payload["storage"]]


class Planner:
    def __init__(self, repos, resolver, *, settings):
        self._repos = repos
        self._resolver = resolver
        self._settings = settings

    def run_once(self, limit: int = 50, *, now_iso=None) -> dict:
        pending = self._repos.db.query(
            """SELECT request_id FROM requests WHERE state = :s
               ORDER BY commit_order LIMIT :n""",
            {"s": RequestState.PENDING.value, "n": limit})
        results = {}
        for row in pending:
            rid = row["request_id"]
            try:
                results[rid] = self._plan_one(rid, now_iso)
            except Exception as exc:  # 한 요청 실패가 다음을 막지 않는다
                print(f"planner error on {rid}: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
        return results

    def _reject(self, rid, reason):
        self._repos.requests.set_state(rid, RequestState.REJECTED,
                                       reason_code=reason, actor="planner")
        self._repos.requests.record_result(rid, RequestState.REJECTED,
                                            reason_code=reason)
        return f"rejected:{reason}"

    def _plan_one(self, rid, now_iso):
        req = self._repos.requests.get(rid)
        payload = req["payload"]
        # 1. conflict: 앞선 비터미널 동일 resource_key
        prior = self._repos.requests.find_active(req["resource_key"])
        if prior is not None and prior["commit_order"] < req["commit_order"]:
            self._repos.requests.set_state(rid, RequestState.CONFLICT,
                                           reason_code="resource_conflict",
                                           actor="planner")
            self._repos.requests.record_result(rid, RequestState.CONFLICT,
                                                reason_code="resource_conflict")
            return "conflict"
        # 2. storage admission
        for name in _required_storages(req["operation"], payload):
            storage = self._repos.storages.get(name)
            if storage is None:
                return self._reject(rid, "storage_missing")
            if not storage["enabled"]:
                return self._reject(rid, "storage_disabled")
            if storage["status"] not in ("Ready", "Degraded"):
                return self._reject(rid, "storage_not_ready")
        # 3. identity
        try:
            identity = resolve_job_identity(
                self._repos.control, self._resolver,
                requester_id=req["requester_id"],
                owner_username=payload.get("owner_username"),
                allow_privileged=self._settings.allow_privileged_requesters,
                privileged_requesters=self._settings.privileged_requesters)
        except IdentityRejected as exc:
            return self._reject(rid, exc.reason_code)
        # 4. tool + candidates
        fresh = self._repos.agents.fresh_reports(
            stale_seconds=self._settings.agent_report_stale_seconds, now_iso=now_iso)
        try:
            placement = select_tool_and_candidates(
                req["operation"], fresh, storage_name=payload.get("storage"),
                source_storage=payload.get("source_storage"),
                destination_storage=payload.get("destination_storage"),
                owner=identity.username, privileged=identity.privileged)
        except PlacementError as exc:
            return self._reject(rid, exc.reason_code)
        # 5. policy fan-out
        policy = self._repos.control.get_policy(TOOL_TO_POLICY[placement["tool"]])
        try:
            fanout = resolve_fanout(policy, placement["candidates"],
                                    priority=req["priority"])
        except PlacementError as exc:
            return self._reject(rid, exc.reason_code)
        # 6. emit
        identity_dict = {**asdict(identity), "groups": list(identity.groups)}
        worker_pool = {"tool": placement["tool"], "identity": identity_dict,
                       "candidates": placement["candidates"], **fanout}
        precondition = {"requester_id": req["requester_id"],
                        "owner": identity.username, "operation": req["operation"]}
        plan_id = self._repos.data_jobs.create_plan(rid, actor="planner")
        self._repos.data_jobs.create_job(
            rid, plan_id, operation=req["operation"], priority=req["priority"],
            storage_name=payload.get("storage"),
            source_storage=payload.get("source_storage"),
            destination_storage=payload.get("destination_storage"),
            source=payload.get("source"), destination=payload.get("destination"),
            target=payload.get("target"), options=payload.get("options", {}),
            tool=placement["tool"], worker_pool=worker_pool,
            precondition=precondition, actor="planner")
        self._repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
        return "planned"
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_planner.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/planner.py tests/test_planner.py
git commit -m "feat: Planner (어드미션 게이트 + tool/identity/후보 해석 + data_job emit)"
```

---

### Task 8: 정책 관리 API (`api/routes_policies.py`)

**Files:**
- Create: `src/dms/api/routes_policies.py`
- Modify: `src/dms/api/app.py`
- Test: `tests/test_api_policies.py`

**Interfaces:**
- Consumes: `require_admin`, `repos.control.get_policy/upsert_policy`, `POLICY_TOOLS`.
- Produces (admin 전용):
  - `GET /api/admin/policies` → 4개 도구(scan/dsync/nsync/rm)의 정책 목록 (없는 도구는 제외, tool 오름차순)
  - `GET /api/admin/policies/{tool}` → 단건, 없으면 404 `policy_not_found`
  - `PUT /api/admin/policies/{tool}` body `{max_nodes, procs_per_node, queue?, default_priority?, max_priority?, preview_timeout_seconds?, execution_timeout_seconds, enabled}` → upsert, 200. 잘못된 tool → 422 `invalid_policy`. 필드 검증(pydantic): max_nodes/procs_per_node ≥1, execution_timeout_seconds ≥1, priority ∈ PRIORITIES.

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_api_policies.py
ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}
BODY = {"max_nodes": 3, "procs_per_node": 8, "queue": "dms-data",
        "default_priority": "mid", "max_priority": "high",
        "preview_timeout_seconds": 3600, "execution_timeout_seconds": 3600,
        "enabled": True}


def test_policies_require_admin(client):
    assert client.get("/api/admin/policies").status_code == 401
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    assert client.get("/api/admin/policies").status_code == 403


def test_policy_crud(client):
    assert client.put("/api/admin/policies/dsync", json=BODY,
                      headers=ADMIN).status_code == 200
    assert client.get("/api/admin/policies/dsync",
                      headers=ADMIN).json()["max_nodes"] == 3
    listed = client.get("/api/admin/policies", headers=ADMIN).json()
    assert [p["tool"] for p in listed] == ["dsync"]
    assert client.get("/api/admin/policies/scan",
                      headers=ADMIN).status_code == 404
    assert client.put("/api/admin/policies/dcp", json=BODY,
                      headers=ADMIN).status_code == 422
    assert client.put("/api/admin/policies/scan",
                      json={**BODY, "max_nodes": 0},
                      headers=ADMIN).status_code == 422
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_api_policies.py -v`
Expected: FAIL (404)

- [ ] **Step 3: 구현**

```python
# src/dms/api/routes_policies.py
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from ..domain import DomainValidationError, PRIORITIES
from ..repositories.control import POLICY_TOOLS
from .auth import Identity, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


class PolicyBody(BaseModel):
    max_nodes: int = Field(ge=1)
    procs_per_node: int = Field(ge=1)
    queue: str = "dms-data"
    default_priority: str = "mid"
    max_priority: str = "high"
    preview_timeout_seconds: int | None = None
    execution_timeout_seconds: int = Field(ge=1)
    enabled: bool = True


@router.get("/api/admin/policies")
def list_policies(request: Request):
    control = request.app.state.repos.control
    out = [control.get_policy(t) for t in sorted(POLICY_TOOLS)]
    return [p for p in out if p is not None]


@router.get("/api/admin/policies/{tool}")
def get_policy(tool: str, request: Request):
    policy = request.app.state.repos.control.get_policy(tool)
    if policy is None:
        raise HTTPException(status_code=404, detail="policy_not_found")
    return policy


@router.put("/api/admin/policies/{tool}")
def put_policy(tool: str, body: PolicyBody, request: Request,
               identity: Identity = Depends(require_admin)):
    if body.default_priority not in PRIORITIES or body.max_priority not in PRIORITIES:
        raise HTTPException(status_code=422, detail="invalid_priority")
    try:
        request.app.state.repos.control.upsert_policy(
            tool, max_nodes=body.max_nodes, procs_per_node=body.procs_per_node,
            queue=body.queue, default_priority=body.default_priority,
            max_priority=body.max_priority,
            preview_timeout_seconds=body.preview_timeout_seconds,
            execution_timeout_seconds=body.execution_timeout_seconds,
            enabled=body.enabled, actor=identity.actor)
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    return request.app.state.repos.control.get_policy(tool)
```

`app.py`에 `from .routes_policies import router as policies_router` + `app.include_router(policies_router)`.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_api_policies.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/api/ tests/test_api_policies.py
git commit -m "feat: 정책 관리 API (도구별 fan-out/queue/priority/timeout CRUD)"
```

---

### Task 9: denylist 관리 API (`api/routes_denylist.py`)

**Files:**
- Create: `src/dms/api/routes_denylist.py`
- Modify: `src/dms/api/app.py`
- Test: `tests/test_api_denylist.py`

**Interfaces:**
- Consumes: `require_admin`, `repos.control.deny/allow`, `repos.db`(목록 조회는 control에 메서드 추가), `DENY_SUBJECT_TYPES`.
- Produces:
  - `ControlRepository.list_denylist() -> list[dict]` 메서드 추가 (control.py) — `subject_type, subject` 오름차순
  - `GET /api/admin/identity-denylist` → 목록
  - `PUT /api/admin/identity-denylist/{subject_type}/{subject}` body `{reason?}` → deny, 201. subject_type ∉ {requester,owner,group} → 422 `invalid_denylist_subject_type`
  - `DELETE /api/admin/identity-denylist/{subject_type}/{subject}` → allow, 200

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_api_denylist.py
ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}


def test_denylist_require_admin(client):
    assert client.get("/api/admin/identity-denylist").status_code == 401


def test_denylist_crud(client):
    r = client.put("/api/admin/identity-denylist/requester/Mallory",
                   json={"reason": "incident"}, headers=ADMIN)
    assert r.status_code == 201
    listed = client.get("/api/admin/identity-denylist", headers=ADMIN).json()
    assert listed == [{"subject_type": "requester", "subject": "mallory",
                       "reason": "incident"}]
    assert client.put("/api/admin/identity-denylist/badtype/x",
                      json={}, headers=ADMIN).status_code == 422
    assert client.delete("/api/admin/identity-denylist/requester/mallory",
                         headers=ADMIN).status_code == 200
    assert client.get("/api/admin/identity-denylist", headers=ADMIN).json() == []
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_api_denylist.py -v`
Expected: FAIL (404)

- [ ] **Step 3: 구현**

`control.py`에 메서드 추가:
```python
    def list_denylist(self):
        return self._db.query(
            """SELECT subject_type, subject, reason FROM identity_denylist
               ORDER BY subject_type, subject""")
```

```python
# src/dms/api/routes_denylist.py
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DomainValidationError
from ..repositories.control import DENY_SUBJECT_TYPES
from .auth import Identity, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


class DenyBody(BaseModel):
    reason: str | None = None


@router.get("/api/admin/identity-denylist")
def list_denylist(request: Request):
    return request.app.state.repos.control.list_denylist()


@router.put("/api/admin/identity-denylist/{subject_type}/{subject}", status_code=201)
def deny(subject_type: str, subject: str, body: DenyBody, request: Request,
         identity: Identity = Depends(require_admin)):
    if subject_type not in DENY_SUBJECT_TYPES:
        raise HTTPException(status_code=422, detail="invalid_denylist_subject_type")
    try:
        request.app.state.repos.control.deny(subject_type, subject, body.reason,
                                             identity.actor)
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    return {"subject_type": subject_type, "subject": subject.lower()}


@router.delete("/api/admin/identity-denylist/{subject_type}/{subject}")
def allow(subject_type: str, subject: str, request: Request,
          identity: Identity = Depends(require_admin)):
    request.app.state.repos.control.allow(subject_type, subject, identity.actor)
    return {"subject_type": subject_type, "subject": subject.lower()}
```

`app.py`에 `from .routes_denylist import router as denylist_router` + `app.include_router(denylist_router)`.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_api_denylist.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/api/ src/dms/repositories/control.py tests/test_api_denylist.py
git commit -m "feat: identity denylist 관리 API (deny/allow, 목록)"
```

---

### Task 10: controller 배선 + app resolver + planner 루프

**Files:**
- Modify: `src/dms/controller.py` (build_loops에 planner 루프)
- Modify: `src/dms/api/app.py` (`app.state.identity_resolver = None` 배선)
- Test: `tests/test_controller_planner.py`

**Interfaces:**
- Consumes: `Planner`(planner), `Settings`.
- Produces:
  - `build_loops(settings, repos)` 시그니처는 유지하되, planner 루프를 리스트 **맨 앞**에 추가. planner는 resolver가 필요 — `build_loops(settings, repos, *, identity_resolver=None)` 로 확장(기본 None). Loop `planner`(settings.planner_interval_seconds), fn = `lambda: Planner(repos, identity_resolver, settings=settings).run_once()`.
  - `run_forever(settings, repos, holder, *, sleep=..., identity_resolver=None)` 도 resolver 전달.
  - `cli.py`의 controller 분기는 `build_loops(settings, repos)` 호출 — resolver 없이(3a: live LDAP 미구현, None). 즉 3a 운영에서 planner는 non-privileged 요청을 `ldap_not_configured`로 거부하고, privileged 경로만 계획한다. (테스트는 resolver 주입.)
  - `app.py`: `app.state.identity_resolver = None` 설정 (3b에서 live resolver 배선). 없어도 기존 라우트 무영향.

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_controller_planner.py
from dms.controller import build_loops, run_all_once
from dms.identity import ResolvedIdentity, StubIdentityResolver
from dms.repositories import Repositories


class _Settings:
    agent_report_stale_seconds = 300
    reconcile_interval_seconds = 30
    retention_interval_seconds = 3600
    planner_interval_seconds = 10
    agent_report_retention_days = 30
    allow_privileged_requesters = False
    privileged_requesters = frozenset()


def test_planner_loop_registered_first(db):
    loops = build_loops(_Settings(), Repositories(db))
    assert loops[0].name == "planner"
    assert loops[0].interval_seconds == 10
    assert {l.name for l in loops} == {"planner", "storage-reconciler", "retention"}


def test_planner_loop_runs_end_to_end(db):
    repos = Repositories(db)
    repos.storages.create(storage_name="s1", mount_path="/mnt/s1",
                          managed_root="/mnt/s1/dms", backend_type="cephfs",
                          actor="admin")
    repos.storages.set_status("s1", "Ready", "ready_nodes=1")
    repos.control.upsert_policy("scan", max_nodes=3, procs_per_node=8,
                                queue="dms-data", default_priority="mid",
                                max_priority="high", preview_timeout_seconds=3600,
                                execution_timeout_seconds=3600, enabled=True,
                                actor="admin")
    repos.agents.ingest("n1", {"node_name": "n1",
        "mounts": [{"storage_name": "s1", "mount_path": "/mnt/s1",
                    "status": "Ready", "writable": True}],
        "tools": [{"name": "dscan", "status": "Ready"}],
        "identities": [{"username": "alice", "status": "Ready"}]})
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="data.scan:s1:a:ff",
        payload={"storage": "s1", "target": "a", "options": {},
                 "owner_username": None}, priority="mid")
    resolver = StubIdentityResolver(
        {"alice": ResolvedIdentity("alice", 10001, 10000, ("dmsusers",), False)})
    loops = build_loops(_Settings(), repos, identity_resolver=resolver)
    run_all_once(loops, repos, holder="h1")
    assert repos.requests.get(rid)["state"] == "Planned"
    assert repos.data_jobs.list_jobs(request_id=rid)[0]["tool"] == "dscan"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_controller_planner.py -v`
Expected: FAIL (planner 루프 없음 / build_loops resolver 인자 없음)

- [ ] **Step 3: 구현**

`controller.py` 수정:
```python
from .planner import Planner  # 상단 import 추가

def build_loops(settings, repos, *, identity_resolver=None):
    return [
        Loop("planner", settings.planner_interval_seconds,
             lambda: Planner(repos, identity_resolver, settings=settings).run_once()),
        Loop("storage-reconciler", settings.reconcile_interval_seconds,
             lambda: reconcile_storages_once(
                 repos, stale_seconds=settings.agent_report_stale_seconds)),
        Loop("retention", settings.retention_interval_seconds,
             lambda: prune_agent_reports_once(
                 repos, retention_days=settings.agent_report_retention_days)),
    ]


def run_forever(settings, repos, holder, *, sleep=time.sleep, identity_resolver=None):
    loops = build_loops(settings, repos, identity_resolver=identity_resolver)
    # ...기존 next_due 로직 그대로...
```

`app.py`의 `create_app`에 `app.state.identity_resolver = None` 한 줄 추가 (repos 설정 근처).

`cli.py`의 controller 분기는 그대로(`build_loops(settings, repos)` / `run_forever(settings, repos, holder)`) — resolver 기본 None.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_controller_planner.py tests/test_controller.py tests/test_cli.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/controller.py src/dms/api/app.py tests/test_controller_planner.py
git commit -m "feat: controller에 planner 루프 배선 (resolver 주입점, 3a는 None)"
```

---

## Phase 3a 완료 기준

- `.venv/bin/pytest -q` 전체 통과 (서비스 없이, 0 warnings).
- 수동 검증(privileged 경로, LDAP 없이): `dms migrate` → `dms api` 기동 → 관리자 토큰으로 스토리지·정책 등록 → `DMS_ALLOW_PRIVILEGED_REQUESTERS=true DMS_PRIVILEGED_REQUESTERS=ops` 로 controller 기동 → (에이전트가 붙어 fresh 리포트가 있고) requester_id=ops인 요청 제출 → planner가 `Planned` data_job(tool 선택됨)을 emit. non-privileged 요청은 `ldap_not_configured`로 `Rejected`(3b에서 live LDAP 붙으면 해소).
- **Phase 3b로 이어짐**: job-stepper(비블로킹 스텝 머신) + 실행 어댑터 인터페이스·stub + preview/confirm/cancel API + job 상세 API. **Phase 3c**: live LDAP(ldap3) 어댑터 + live Volcano 어댑터 + dms-job-runner (테스트베드 실증).
