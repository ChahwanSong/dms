# DMS Phase 3a — 잡 라이프사이클 코어 (planner/stepper/identity/confirm) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 요청이 planner 어드미션 → 도구/노드 자동 선택 → preflight → preview → confirm → execution → 결과까지 흐르는 **비블로킹 잡 상태머신 전체**를 만든다. 실행은 `ExecutionAdapter` 포트 뒤에 두고, 이 플랜에서는 **stub 어댑터**로 전 라이프사이클을 실동작·테스트 가능하게 한다 (실 Volcano 어댑터 + dms-job-runner는 Phase 3b).

**Architecture:** 스펙 §5 구현. planner와 job-stepper는 controller의 `run_once()` 루프. 잡 진행은 DB 상태머신을 한 스텝씩 전진(제출/폴링/파싱 중 하나)하고, 외부 세계 접촉은 전부 `ExecutionAdapter`(preflight/도구 잡 시작·폴링·종료·summary 읽기)와 `IdentityLookup`(LDAP) 포트 뒤에 있다. confirm/cancel은 API 상태 전이다.

**Tech Stack:** Phase 1+2 코드 위에 Python 3.11+, ldap3(optional extra), stub 어댑터.

## Global Constraints

- 스펙이 진실: `docs/superpowers/specs/2026-08-02-dms-clean-slate-design.md` §4·§5. legacy 코드 재사용 금지.
- 모든 런타임 SQL은 `src/dms/repositories/` 안에만. named param, SQLite/PG 호환.
- 모든 잡 상태 전이는 `state_transitions`(entity_kind='job')에 기록. 요청 터미널 매핑: Succeeded→Succeeded / Failed·TimedOut→Failed / Rejected·PreviewExpired→Rejected / Cancelled→Cancelled, 터미널 시 `results`에 reason_code와 summary 기록.
- fail-closed: LDAP 미설정/불능, 정책 부재, 스토리지 not-Ready, 빈 preview summary는 전부 명시적 사유 코드로 거부. 사유 코드는 snake_case (`missing_tool:<name>` 형식 예외 허용).
- 도구 선택: scan→dscan, rm→drm; sync는 양쪽 마운트 공존 노드 ≥1 → dsync, 아니면 role별 후보 각각 ≥1 → nsync, 둘 다 아니면 no_ready_sync_candidate. 정책 fan-out은 **상한**(min(적격, 상한)), 적격 0일 때만 거부. rm target·sync destination은 **writable 필수**.
- preview 지문: `"sha256:" + sha256(정렬 JSON)`, **빈 summary는 지문 없음 → confirm 불가**. confirm은 지문 일치 필수(불일치 409), TTL 만료 시 PreviewExpired.
- cancel: adapter 종료가 **성공한 뒤에만** Cancelled 기록 (거짓 취소 금지).
- run_as_root 요청은 admin identity만 제출 가능(403), denylist는 root 경로보다 먼저.
- 시각 주입: 모든 시간 판단 함수는 `now_iso` 파라미터 허용. 전체 테스트는 SQLite로 서비스 없이. 0 warnings.
- 커밋: conventional commit + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`, 태스크마다 커밋.

## Phase 1+2가 제공하는 인터페이스 (전제 — 변경은 명시된 곳만)

- `dms.db`: `Database`, `dump_json/load_json`, `utc_now_iso()`, `iso_plus(ts, seconds)`.
- `dms.domain`: `RequestState`, `DataJobState`(PENDING/PREFLIGHT/PREVIEW_RUNNING/CONFIRM_PENDING/EXECUTING/RUNNING/SUCCEEDED/FAILED/TIMED_OUT/CANCELLED/REJECTED/PREVIEW_EXPIRED), `TERMINAL_DATA_JOB_STATES`, `Operation`, `Tool`, `DomainValidationError`.
- `Repositories(db)` → `.requests`(create/get/list/set_state/find_active/record_result/transitions), `.storages`(get/list/set_status), `.accounts`, `.control`(get_policy(tool)/is_denied/register_probe_target/control_state/try_acquire_lease/audit_entries), `.agents`(fresh_reports/ingest/...).
- `policies` 행: {tool(scan/dsync/nsync/rm), max_nodes, procs_per_node, queue, default_priority, max_priority, preview_timeout_seconds, execution_timeout_seconds, enabled}.
- 에이전트 리포트 형식(§Phase2): `report["mounts"]` = [{storage_name, mount_path, exists, is_mountpoint, readable, writable, status(Ready/Missing), reason}], `report["tools"]` = [{name, status(Ready/Missing), path, version, reason}], `report["identities"]` = [{username, status(Ready/Missing), uid, gid, groups}].
- API: `create_app(settings, db)`, auth의 `Identity/require_user/require_admin`, 기존 라우터 4개.
- controller: `Loop`, `build_loops(settings, repos)`(현재 2루프 — 이번에 확장), `run_all_once`, `run_forever`.
- 스키마: `data_jobs`(job_id PK, request_id, operation, tool, storage_name, source_storage, destination_storage, source, destination, target, options TEXT, priority, state, reason_code, preview_fingerprint, preview_expires_at, volcano_job_ref TEXT, artifact_uri, result_summary TEXT, created_at, updated_at), `plans`(plan_id, request_id, job_id, state, created_at, updated_at), `runs`(run_id, plan_id, request_id, state, detail, started_at, finished_at). **스키마 변경 없음** — `volcano_job_ref`에 JSON `{"phase","ref","run_id","submitted_at"}`을 저장한다.

## File Structure

```
src/dms/config.py                  # (수정) LDAP/preview/pending/backend/interval knob
pyproject.toml                     # (수정) [project.optional-dependencies] ldap = ["ldap3>=2.9"]
src/dms/domain.py                  # (수정) summary_fingerprint 추가
src/dms/repositories/jobs.py       # JobsRepository (+plan/run 헬퍼) — data_jobs/plans/runs
src/dms/repositories/requests.py   # (수정) list_by_state 추가
src/dms/repositories/__init__.py   # (수정) .jobs 연결
src/dms/adapters/__init__.py
src/dms/adapters/execution.py      # ExecutionAdapter 프로토콜 + StubExecutionAdapter
src/dms/adapters/identity.py       # LdapIdentityLookup(주입 가능) + StubIdentityLookup
src/dms/identity.py                # resolve_identity 플로우 (denylist→root→LDAP→probe 등록)
src/dms/selection.py               # 도구/노드 자동 선택 (에이전트 증거 기반)
src/dms/planner.py                 # planner run_once (어드미션)
src/dms/stepper.py                 # job-stepper run_once + 스텝 상태머신
src/dms/api/routes_jobs.py         # 잡 상세/목록/confirm/cancel
src/dms/api/app.py                 # (수정) execution_adapter 파라미터 + 라우터 마운트
src/dms/controller.py              # (수정) planner/job-stepper 루프 추가
src/dms/cli.py                     # (수정) 어댑터/lookup 조립 (backend=stub)
tests/test_config_phase3.py
tests/test_repo_jobs.py
tests/test_adapter_identity.py
tests/test_identity_resolve.py
tests/test_selection.py
tests/test_planner.py
tests/test_adapter_stub.py
tests/test_stepper_submit.py
tests/test_stepper_terminal.py
tests/test_api_jobs.py
tests/test_controller.py           # (수정) 루프 4개로 확장
tests/test_lifecycle.py            # E2E (stub 어댑터)
```

---

### Task 1: 설정 확장 + ldap extra (`config.py`, `pyproject.toml`)

**Files:**
- Modify: `src/dms/config.py`
- Modify: `pyproject.toml`
- Test: `tests/test_config_phase3.py`

**Interfaces:**
- Consumes: 기존 `Settings`/`SettingsError`/`_is_placeholder`/`_parse_int`/`_SERVER_INT_KEYS`.
- Produces — `Settings`에 필드 추가 (전부 기본값 있음, 기존 필드 뒤에):
  - `ldap_uri: str | None = None` (`DMS_LDAP_URI`), `ldap_base_dn: str | None = None` (`DMS_LDAP_BASE_DN`), `ldap_bind_dn: str | None = None` (`DMS_LDAP_BIND_DN`), `ldap_bind_password: str | None = None` (`DMS_LDAP_BIND_PASSWORD`)
  - int: `preview_ttl_seconds=86400`(`DMS_PREVIEW_TTL_SECONDS`), `pending_timeout_seconds=300`(`DMS_PENDING_TIMEOUT_SECONDS`), `preflight_timeout_seconds=600`(`DMS_PREFLIGHT_TIMEOUT_SECONDS`), `planner_interval_seconds=5`(`DMS_PLANNER_INTERVAL_SECONDS`), `stepper_interval_seconds=5`(`DMS_STEPPER_INTERVAL_SECONDS`) — `_SERVER_INT_KEYS`에 5행 추가
  - `execution_backend: str = "stub"` (`DMS_EXECUTION_BACKEND`) — 허용값 {"stub", "volcano"} 외 SettingsError. **"volcano"는 Phase 3b에서 구현되며 3a에서는 cli가 명시 거부한다** (Task 12)
  - 검증: `ldap_uri`/`ldap_base_dn` 중 **하나만** 설정되면 `problems`에 "DMS_LDAP_URI and DMS_LDAP_BASE_DN must be set together" 추가 (반쪽 설정 fail-closed)
- pyproject: `[project.optional-dependencies]`에 `ldap = ["ldap3>=2.9"]` 추가

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_config_phase3.py
import pytest
from dms.config import Settings, SettingsError

VALID = {
    "DMS_DATABASE_URL": "sqlite:///tmp/dms.db",
    "DMS_SHARED_TOKEN": "tok",
    "DMS_ADMIN_TOKEN": "adm",
    "DMS_SESSION_SECRET": "sess",
}


def test_phase3_defaults():
    s = Settings.from_env(VALID)
    assert s.ldap_uri is None and s.ldap_base_dn is None
    assert s.preview_ttl_seconds == 86400
    assert s.pending_timeout_seconds == 300
    assert s.preflight_timeout_seconds == 600
    assert s.planner_interval_seconds == 5
    assert s.stepper_interval_seconds == 5
    assert s.execution_backend == "stub"


def test_ldap_pair_validation():
    s = Settings.from_env({**VALID, "DMS_LDAP_URI": "ldap://pkg-01",
                           "DMS_LDAP_BASE_DN": "dc=dms,dc=local"})
    assert s.ldap_uri == "ldap://pkg-01"
    with pytest.raises(SettingsError) as e:
        Settings.from_env({**VALID, "DMS_LDAP_URI": "ldap://pkg-01"})
    assert "DMS_LDAP_BASE_DN" in str(e.value)


def test_execution_backend_allowlist():
    assert Settings.from_env({**VALID, "DMS_EXECUTION_BACKEND": "volcano"}
                             ).execution_backend == "volcano"
    with pytest.raises(SettingsError) as e:
        Settings.from_env({**VALID, "DMS_EXECUTION_BACKEND": "docker"})
    assert "DMS_EXECUTION_BACKEND" in str(e.value)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_config_phase3.py -v`
Expected: FAIL — AttributeError/필드 없음

- [ ] **Step 3: 구현**

`_SERVER_INT_KEYS`에 추가:

```python
    ("DMS_PREVIEW_TTL_SECONDS", "preview_ttl_seconds", 86400),
    ("DMS_PENDING_TIMEOUT_SECONDS", "pending_timeout_seconds", 300),
    ("DMS_PREFLIGHT_TIMEOUT_SECONDS", "preflight_timeout_seconds", 600),
    ("DMS_PLANNER_INTERVAL_SECONDS", "planner_interval_seconds", 5),
    ("DMS_STEPPER_INTERVAL_SECONDS", "stepper_interval_seconds", 5),
```

`Settings` 필드 (기존 뒤에):

```python
    preview_ttl_seconds: int = 86400
    pending_timeout_seconds: int = 300
    preflight_timeout_seconds: int = 600
    planner_interval_seconds: int = 5
    stepper_interval_seconds: int = 5
    ldap_uri: str | None = None
    ldap_base_dn: str | None = None
    ldap_bind_dn: str | None = None
    ldap_bind_password: str | None = None
    execution_backend: str = "stub"
```

`from_env` 확장 (problems 수집부에, 단일 raise 전):

```python
        ldap_uri = environ.get("DMS_LDAP_URI") or None
        ldap_base_dn = environ.get("DMS_LDAP_BASE_DN") or None
        if bool(ldap_uri) != bool(ldap_base_dn):
            problems.append("DMS_LDAP_URI and DMS_LDAP_BASE_DN must be set together")
        backend = environ.get("DMS_EXECUTION_BACKEND", "stub")
        if backend not in ("stub", "volcano"):
            problems.append(f"DMS_EXECUTION_BACKEND must be stub|volcano: {backend!r}")
```

생성자 전달에 `ldap_uri=ldap_uri, ldap_base_dn=ldap_base_dn, ldap_bind_dn=environ.get("DMS_LDAP_BIND_DN") or None, ldap_bind_password=environ.get("DMS_LDAP_BIND_PASSWORD") or None, execution_backend=backend` 추가. pyproject `[project.optional-dependencies]`에 `ldap = ["ldap3>=2.9"]` 추가 후 `.venv/bin/pip install -q -e ".[test,ldap]"`.

- [ ] **Step 4: 통과 확인** — `.venv/bin/pytest tests/test_config_phase3.py tests/test_config.py tests/test_config_phase2.py -v` 후 `.venv/bin/pytest -q` 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/config.py pyproject.toml tests/test_config_phase3.py
git commit -m "feat: Phase 3a 설정 (LDAP, preview TTL, 실행 백엔드 allowlist)"
```

---

### Task 2: 잡 저장소 + summary 지문 (`repositories/jobs.py`, `domain.py`, `requests.py`)

**Files:**
- Create: `src/dms/repositories/jobs.py`
- Modify: `src/dms/domain.py` (summary_fingerprint), `src/dms/repositories/requests.py` (list_by_state), `src/dms/repositories/__init__.py` (`.jobs`)
- Test: `tests/test_repo_jobs.py`

**Interfaces:**
- Produces:
  - `dms.domain.summary_fingerprint(summary: dict | None) -> str | None` — 비어있지 않은 dict면 `"sha256:" + sha256(json.dumps(summary, sort_keys=True, separators=(",", ":")))` hex, 빈 dict/None이면 **None**
  - `JobsRepository(db)`:
    - `create(*, request_id, operation, priority, payload: dict, actor) -> str` — job_id(uuid4 hex). payload에서 storage_name/source_storage/destination_storage/source/destination/target/options를 컬럼에 매핑(없으면 None, options는 dump_json). 상태 `Pending`, 전이 기록(None→Pending, entity_kind='job'). **같은 트랜잭션에서 plans 행도 생성**(plan_id uuid4, state="active")
    - `get(job_id) -> dict | None` (options/result_summary/volcano_job_ref는 dict/None으로 역직렬화)
    - `get_by_request(request_id) -> dict | None`
    - `list(requester_id=None, limit=50) -> list[dict]` — requests와 join해 requester_id 필터, commit_order 역순 (컬럼에 requests.requester_id 포함)
    - `due_jobs(limit=20) -> list[dict]` — 비터미널 상태, updated_at 오름차순
    - `set_state(job_id, to_state: DataJobState, *, reason_code=None, actor, fields: dict | None = None)` — 상태+전이+임의 컬럼 갱신을 한 트랜잭션에. fields의 dict 값은 dump_json (result_summary/volcano_job_ref/options)
    - `update_fields(job_id, fields: dict)` — 전이 없는 컬럼 갱신 (ref 중간 갱신용)
    - `job_transitions(job_id) -> list[dict]`
    - `plan_of(request_id) -> dict | None`
    - `create_run(*, plan_id, request_id, detail) -> str` — run_id, state "Running", started_at=now
    - `finish_run(run_id, state: str)` — state("Succeeded"/"Failed"/"Cancelled"), finished_at=now
  - `RequestsRepository.list_by_state(state: RequestState, limit=50) -> list[dict]` — commit_order 오름차순(오래된 것 먼저)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_repo_jobs.py
from dms.domain import DataJobState, RequestState, summary_fingerprint
from dms.repositories import Repositories

PAYLOAD = {"storage": "s1", "target": "team/data", "options": {"summary_only": True},
           "owner_username": None}


def _request(repos, requester="alice", key="k1"):
    return repos.requests.create(operation="scan", requester_id=requester,
                                 actor=requester, resource_key=key,
                                 payload=PAYLOAD, priority="mid")


def test_summary_fingerprint():
    fp = summary_fingerprint({"files": 3})
    assert fp.startswith("sha256:") and len(fp) == 71
    assert summary_fingerprint({"b": 1, "a": 2}) == summary_fingerprint({"a": 2, "b": 1})
    assert summary_fingerprint({}) is None
    assert summary_fingerprint(None) is None


def test_create_job_with_plan_and_get(db):
    repos = Repositories(db)
    rid = _request(repos)
    jid = repos.jobs.create(request_id=rid, operation="scan", priority="mid",
                            payload={"storage_name": "s1", "target": "team/data",
                                     "options": {"summary_only": True}},
                            actor="planner")
    job = repos.jobs.get(jid)
    assert job["state"] == "Pending" and job["storage_name"] == "s1"
    assert job["options"] == {"summary_only": True}
    assert repos.jobs.get_by_request(rid)["job_id"] == jid
    plan = repos.jobs.plan_of(rid)
    assert plan["job_id"] == jid and plan["state"] == "active"
    ts = repos.jobs.job_transitions(jid)
    assert [(t["from_state"], t["to_state"]) for t in ts] == [(None, "Pending")]


def test_set_state_with_fields_and_due(db):
    repos = Repositories(db)
    rid = _request(repos)
    jid = repos.jobs.create(request_id=rid, operation="scan", priority="mid",
                            payload={"storage_name": "s1", "target": "a",
                                     "options": {}}, actor="planner")
    repos.jobs.set_state(jid, DataJobState.PREFLIGHT, actor="stepper",
                         fields={"tool": "dscan",
                                 "volcano_job_ref": {"phase": "preflight"}})
    job = repos.jobs.get(jid)
    assert job["state"] == "Preflight" and job["tool"] == "dscan"
    assert job["volcano_job_ref"] == {"phase": "preflight"}
    assert [j["job_id"] for j in repos.jobs.due_jobs()] == [jid]
    repos.jobs.set_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    assert repos.jobs.due_jobs() == []


def test_list_joins_requester_and_runs(db):
    repos = Repositories(db)
    rid = _request(repos, requester="alice")
    jid = repos.jobs.create(request_id=rid, operation="scan", priority="mid",
                            payload={"storage_name": "s1", "target": "a",
                                     "options": {}}, actor="planner")
    assert repos.jobs.list(requester_id="alice")[0]["job_id"] == jid
    assert repos.jobs.list(requester_id="bob") == []
    plan = repos.jobs.plan_of(rid)
    run_id = repos.jobs.create_run(plan_id=plan["plan_id"], request_id=rid,
                                   detail="preflight")
    repos.jobs.finish_run(run_id, "Succeeded")
    row = db.query_one("SELECT state, detail, finished_at FROM runs WHERE run_id = :r",
                       {"r": run_id})
    assert row["state"] == "Succeeded" and row["detail"] == "preflight"
    assert row["finished_at"] is not None


def test_list_by_state(db):
    repos = Repositories(db)
    r1 = _request(repos, key="a")
    r2 = _request(repos, key="b")
    repos.requests.set_state(r2, RequestState.PLANNED, actor="planner")
    pending = repos.requests.list_by_state(RequestState.PENDING)
    assert [r["request_id"] for r in pending] == [r1]
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/pytest tests/test_repo_jobs.py -v` → ImportError

- [ ] **Step 3: 구현**

`domain.py`에 추가:

```python
def summary_fingerprint(summary) -> str | None:
    if not summary:
        return None
    payload = json.dumps(summary, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
```

`requests.py`에 추가:

```python
    def list_by_state(self, state: RequestState, limit: int = 50) -> list[dict]:
        rows = self._db.query(
            """SELECT * FROM requests WHERE state = :s
               ORDER BY commit_order LIMIT :n""",
            {"s": RequestState(state).value, "n": limit})
        for row in rows:
            row["payload"] = load_json(row["payload"])
        return rows
```

```python
# src/dms/repositories/jobs.py
"""데이터 잡 저장소: data_jobs + plans + runs. 상태 전이는 state_transitions(entity='job')."""
import uuid

from ..db import Database, dump_json, load_json, utc_now_iso
from ..domain import DataJobState, TERMINAL_DATA_JOB_STATES

_JSON_FIELDS = ("options", "result_summary", "volcano_job_ref")
_PAYLOAD_COLUMNS = ("storage_name", "source_storage", "destination_storage",
                    "source", "destination", "target")


class JobsRepository:
    def __init__(self, db: Database):
        self._db = db

    def _record_transition(self, job_id, from_state, to_state, reason_code, actor, at):
        self._db.execute(
            """INSERT INTO state_transitions (entity_kind, entity_id, from_state,
                   to_state, reason_code, actor, at)
               VALUES ('job', :id, :f, :t, :r, :actor, :at)""",
            {"id": job_id, "f": from_state, "t": to_state, "r": reason_code,
             "actor": actor, "at": at})

    def create(self, *, request_id, operation, priority, payload: dict, actor) -> str:
        job_id = uuid.uuid4().hex
        plan_id = uuid.uuid4().hex
        now = utc_now_iso()
        columns = {c: payload.get(c) for c in _PAYLOAD_COLUMNS}
        with self._db.transaction():
            self._db.execute(
                """INSERT INTO data_jobs (job_id, request_id, operation, tool,
                       storage_name, source_storage, destination_storage, source,
                       destination, target, options, priority, state,
                       created_at, updated_at)
                   VALUES (:job, :req, :op, NULL, :storage_name, :source_storage,
                       :destination_storage, :source, :destination, :target,
                       :options, :pri, :state, :now, :now)""",
                {"job": job_id, "req": request_id, "op": operation,
                 **columns, "options": dump_json(payload.get("options") or {}),
                 "pri": priority, "state": DataJobState.PENDING.value, "now": now})
            self._record_transition(job_id, None, DataJobState.PENDING.value,
                                    None, actor, now)
            self._db.execute(
                """INSERT INTO plans (plan_id, request_id, job_id, state,
                       created_at, updated_at)
                   VALUES (:plan, :req, :job, 'active', :now, :now)""",
                {"plan": plan_id, "req": request_id, "job": job_id, "now": now})
        return job_id

    def _hydrate(self, row):
        if row is None:
            return None
        for field in _JSON_FIELDS:
            row[field] = load_json(row[field]) if row.get(field) else None
        if row["options"] is None:
            row["options"] = {}
        return row

    def get(self, job_id):
        return self._hydrate(self._db.query_one(
            "SELECT * FROM data_jobs WHERE job_id = :j", {"j": job_id}))

    def get_by_request(self, request_id):
        return self._hydrate(self._db.query_one(
            "SELECT * FROM data_jobs WHERE request_id = :r", {"r": request_id}))

    def list(self, requester_id=None, limit: int = 50) -> list[dict]:
        base = """SELECT d.*, r.requester_id FROM data_jobs d
                  JOIN requests r ON r.request_id = d.request_id"""
        if requester_id is None:
            rows = self._db.query(base + " ORDER BY r.commit_order DESC LIMIT :n",
                                  {"n": limit})
        else:
            rows = self._db.query(
                base + " WHERE r.requester_id = :req ORDER BY r.commit_order DESC"
                       " LIMIT :n",
                {"req": requester_id, "n": limit})
        return [self._hydrate(row) for row in rows]

    def due_jobs(self, limit: int = 20) -> list[dict]:
        terminal = tuple(s.value for s in TERMINAL_DATA_JOB_STATES)
        placeholders = ", ".join(f":t{i}" for i in range(len(terminal)))
        params = {f"t{i}": v for i, v in enumerate(terminal)}
        params["n"] = limit
        rows = self._db.query(
            f"""SELECT * FROM data_jobs WHERE state NOT IN ({placeholders})
                ORDER BY updated_at LIMIT :n""", params)
        return [self._hydrate(row) for row in rows]

    def _apply_fields(self, job_id, fields, now):
        if not fields:
            return
        sets, params = [], {"j": job_id, "now": now}
        for index, (column, value) in enumerate(fields.items()):
            key = f"v{index}"
            sets.append(f"{column} = :{key}")
            params[key] = dump_json(value) if column in _JSON_FIELDS and \
                isinstance(value, (dict, list)) else value
        self._db.execute(
            f"UPDATE data_jobs SET {', '.join(sets)}, updated_at = :now"
            " WHERE job_id = :j", params)

    def set_state(self, job_id, to_state: DataJobState, *, reason_code=None,
                  actor, fields: dict | None = None):
        now = utc_now_iso()
        with self._db.transaction():
            current = self._db.query_one(
                "SELECT state FROM data_jobs WHERE job_id = :j", {"j": job_id})
            if current is None:
                raise KeyError(job_id)
            self._db.execute(
                """UPDATE data_jobs SET state = :s, reason_code = :r,
                       updated_at = :now WHERE job_id = :j""",
                {"s": DataJobState(to_state).value, "r": reason_code,
                 "now": now, "j": job_id})
            self._apply_fields(job_id, fields, now)
            self._record_transition(job_id, current["state"],
                                    DataJobState(to_state).value,
                                    reason_code, actor, now)

    def update_fields(self, job_id, fields: dict):
        with self._db.transaction():
            self._apply_fields(job_id, fields, utc_now_iso())

    def job_transitions(self, job_id) -> list[dict]:
        return self._db.query(
            """SELECT * FROM state_transitions
               WHERE entity_kind = 'job' AND entity_id = :j ORDER BY id""",
            {"j": job_id})

    def plan_of(self, request_id):
        return self._db.query_one(
            "SELECT * FROM plans WHERE request_id = :r", {"r": request_id})

    def create_run(self, *, plan_id, request_id, detail) -> str:
        run_id = uuid.uuid4().hex
        self._db.execute(
            """INSERT INTO runs (run_id, plan_id, request_id, state, detail,
                   started_at)
               VALUES (:run, :plan, :req, 'Running', :detail, :now)""",
            {"run": run_id, "plan": plan_id, "req": request_id,
             "detail": detail, "now": utc_now_iso()})
        return run_id

    def finish_run(self, run_id, state: str):
        self._db.execute(
            """UPDATE runs SET state = :s, finished_at = :now
               WHERE run_id = :run""",
            {"s": state, "now": utc_now_iso(), "run": run_id})
```

`Repositories`에 `self.jobs = JobsRepository(db)` 추가.

- [ ] **Step 4: 통과 확인** — 대상 파일 + `.venv/bin/pytest -q` 전체 PASS
- [ ] **Step 5: Commit** — `feat: 잡 저장소 (data_jobs/plans/runs, summary 지문)`

---

### Task 3: identity lookup 어댑터 (`adapters/identity.py`)

**Files:**
- Create: `src/dms/adapters/__init__.py` (빈 파일), `src/dms/adapters/identity.py`
- Test: `tests/test_adapter_identity.py`

**Interfaces:**
- Produces:
  - `StubIdentityLookup(users: dict[str, dict])` — `.lookup(username) -> dict | None` (`{"username", "uid", "gid", "groups"}`)
  - `LdapIdentityLookup(settings, connection_factory=None)` — 같은 `.lookup`. ldap3로 `(&(objectClass=posixAccount)(uid=<escaped>))` 검색(uidNumber/gidNumber), 그룹은 `(&(objectClass=posixGroup)(memberUid=<escaped>))`의 cn 수집. username은 `ldap3.utils.conv.escape_filter_chars`로 이스케이프. 미발견 → None, 연결/검색 예외는 **전파**(호출측이 ldap_unavailable로 분류). `connection_factory()` 주입으로 테스트 (기본: ldap3.Connection(Server(uri), user=bind_dn, password=bind_password, auto_bind=True))
  - `build_identity_lookup(settings) -> LdapIdentityLookup | None` — ldap_uri 없으면 None

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_adapter_identity.py
import pytest
from dms.adapters.identity import (LdapIdentityLookup, StubIdentityLookup,
                                   build_identity_lookup)
from dms.config import Settings

VALID = {"DMS_DATABASE_URL": "sqlite:///x", "DMS_SHARED_TOKEN": "t",
         "DMS_ADMIN_TOKEN": "a", "DMS_SESSION_SECRET": "s"}


class FakeEntry:
    def __init__(self, attrs):
        self._attrs = attrs

    def __getitem__(self, name):
        class V:
            def __init__(self, value):
                self.value = value
        return V(self._attrs[name])


class FakeConn:
    def __init__(self, results):
        self._results = results  # filter substring -> entries list
        self.searches = []

    def search(self, base, flt, attributes=None):
        self.searches.append(flt)
        for key, entries in self._results.items():
            if key in flt:
                self.entries = entries
                return bool(entries)
        self.entries = []
        return False


def _settings(**extra):
    return Settings.from_env({**VALID, "DMS_LDAP_URI": "ldap://x",
                              "DMS_LDAP_BASE_DN": "dc=dms,dc=local", **extra})


def test_stub_lookup():
    stub = StubIdentityLookup({"alice": {"username": "alice", "uid": 10001,
                                         "gid": 10000, "groups": ["dmsusers"]}})
    assert stub.lookup("alice")["uid"] == 10001
    assert stub.lookup("ghost") is None


def test_ldap_lookup_found():
    conn = FakeConn({
        "posixAccount": [FakeEntry({"uidNumber": 10001, "gidNumber": 10000})],
        "posixGroup": [FakeEntry({"cn": "dmsusers"}), FakeEntry({"cn": "dev"})],
    })
    lookup = LdapIdentityLookup(_settings(), connection_factory=lambda: conn)
    out = lookup.lookup("alice")
    assert out == {"username": "alice", "uid": 10001, "gid": 10000,
                   "groups": ["dev", "dmsusers"]}
    assert "(uid=alice)" in conn.searches[0]


def test_ldap_lookup_not_found_and_escaping():
    conn = FakeConn({})
    lookup = LdapIdentityLookup(_settings(), connection_factory=lambda: conn)
    assert lookup.lookup("ghost") is None
    lookup.lookup("a*b")
    assert "a\\2ab" in conn.searches[-2] or "a\\2Ab" in conn.searches[-2]


def test_ldap_errors_propagate():
    def boom():
        raise ConnectionError("ldap down")

    lookup = LdapIdentityLookup(_settings(), connection_factory=boom)
    with pytest.raises(ConnectionError):
        lookup.lookup("alice")


def test_build_identity_lookup():
    assert build_identity_lookup(Settings.from_env(VALID)) is None
    assert isinstance(build_identity_lookup(_settings()), LdapIdentityLookup)
```

- [ ] **Step 2: 실패 확인** — ImportError
- [ ] **Step 3: 구현**

```python
# src/dms/adapters/identity.py
"""잡 실행 신원 조회. LDAP은 실시간 조회만(캐시/저장 없음, 스펙 §5)."""
from ..config import Settings


class StubIdentityLookup:
    def __init__(self, users: dict):
        self._users = users

    def lookup(self, username: str):
        return self._users.get(username)


class LdapIdentityLookup:
    def __init__(self, settings: Settings, connection_factory=None):
        self._settings = settings
        self._connection_factory = connection_factory or self._default_factory

    def _default_factory(self):
        import ldap3
        server = ldap3.Server(self._settings.ldap_uri)
        return ldap3.Connection(server, user=self._settings.ldap_bind_dn,
                                password=self._settings.ldap_bind_password,
                                auto_bind=True)

    def lookup(self, username: str):
        from ldap3.utils.conv import escape_filter_chars
        safe = escape_filter_chars(username)
        conn = self._connection_factory()
        found = conn.search(
            self._settings.ldap_base_dn,
            f"(&(objectClass=posixAccount)(uid={safe}))",
            attributes=["uidNumber", "gidNumber"])
        if not found or not conn.entries:
            return None
        entry = conn.entries[0]
        uid = int(entry["uidNumber"].value)
        gid = int(entry["gidNumber"].value)
        conn.search(self._settings.ldap_base_dn,
                    f"(&(objectClass=posixGroup)(memberUid={safe}))",
                    attributes=["cn"])
        groups = sorted(str(e["cn"].value) for e in conn.entries)
        return {"username": username, "uid": uid, "gid": gid, "groups": groups}


def build_identity_lookup(settings: Settings):
    if not settings.ldap_uri:
        return None
    return LdapIdentityLookup(settings)
```

(주의: `escape_filter_chars`는 ldap 미설치 환경에서 import 에러가 나므로 함수 내부 import — stub 백엔드만 쓰는 테스트가 ldap3 없이도 돌게. 단, 이 저장소 venv에는 Task 1에서 ldap extra를 설치했으므로 테스트는 실행된다.)

- [ ] **Step 4: 통과 확인** — 전체 PASS
- [ ] **Step 5: Commit** — `feat: identity lookup 어댑터 (LDAP posix 조회 + stub)`

---

### Task 4: identity resolve 플로우 (`identity.py`)

**Files:**
- Create: `src/dms/identity.py`
- Test: `tests/test_identity_resolve.py`

**Interfaces:**
- Consumes: `ControlRepository.is_denied/register_probe_target`, lookup(Task 3).
- Produces: `resolve_identity(control, lookup, *, requester_id: str, owner_username: str | None, run_as_root: bool) -> dict`
  - 반환: `{"ok": True, "identity": {"username", "uid", "gid", "groups", "privileged": bool}}` 또는 `{"ok": False, "reason": <code>}`
  - 순서 (스펙 §5): ① owner = (owner_username or requester_id).strip() ② denylist(requester, owner, groups=[]) → `identity_denied` ③ run_as_root → uid/gid 0 합성, privileged=True, LDAP 스킵 (**denylist가 root보다 먼저**) ④ lookup None → `ldap_not_configured` ⑤ lookup 예외 → `ldap_unavailable` ⑥ 결과 None → `ldap_identity_not_found` ⑦ LDAP 그룹 포함 denylist 재검사 → `identity_denied` ⑧ `register_probe_target(owner)` (fail-soft: 예외 무시) → ok

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_identity_resolve.py
from dms.adapters.identity import StubIdentityLookup
from dms.identity import resolve_identity
from dms.repositories import Repositories

ALICE = {"username": "alice", "uid": 10001, "gid": 10000, "groups": ["dmsusers"]}


def _control(db):
    return Repositories(db).control


def test_happy_path_registers_probe(db):
    control = _control(db)
    out = resolve_identity(control, StubIdentityLookup({"alice": ALICE}),
                           requester_id="alice", owner_username=None,
                           run_as_root=False)
    assert out["ok"] and out["identity"]["uid"] == 10001
    assert out["identity"]["privileged"] is False
    assert control.probe_targets(ttl_seconds=3600) == ["alice"]


def test_denylist_blocks_before_root(db):
    control = _control(db)
    control.deny("requester", "mallory", reason=None, actor="admin")
    out = resolve_identity(control, None, requester_id="mallory",
                           owner_username=None, run_as_root=True)
    assert out == {"ok": False, "reason": "identity_denied"}


def test_root_synthesis(db):
    out = resolve_identity(_control(db), None, requester_id="boss",
                           owner_username="victim", run_as_root=True)
    assert out["ok"] and out["identity"] == {
        "username": "victim", "uid": 0, "gid": 0, "groups": [],
        "privileged": True}


def test_ldap_not_configured_and_not_found_and_unavailable(db):
    control = _control(db)
    assert resolve_identity(control, None, requester_id="a", owner_username=None,
                            run_as_root=False)["reason"] == "ldap_not_configured"
    assert resolve_identity(control, StubIdentityLookup({}), requester_id="a",
                            owner_username=None,
                            run_as_root=False)["reason"] == "ldap_identity_not_found"

    class Boom:
        def lookup(self, username):
            raise ConnectionError("down")

    assert resolve_identity(control, Boom(), requester_id="a", owner_username=None,
                            run_as_root=False)["reason"] == "ldap_unavailable"


def test_group_denylist_after_lookup(db):
    control = _control(db)
    control.deny("group", "dmsusers", reason=None, actor="admin")
    out = resolve_identity(control, StubIdentityLookup({"alice": ALICE}),
                           requester_id="alice", owner_username=None,
                           run_as_root=False)
    assert out == {"ok": False, "reason": "identity_denied"}
```

- [ ] **Step 2: 실패 확인** — ImportError
- [ ] **Step 3: 구현**

```python
# src/dms/identity.py
"""잡 실행 신원 해석 플로우 (스펙 §5): denylist → root 특권 → LDAP → 그룹 denylist → 프로브 등록."""


def resolve_identity(control, lookup, *, requester_id: str,
                     owner_username: str | None, run_as_root: bool) -> dict:
    owner = (owner_username or requester_id).strip()
    if control.is_denied(requester=requester_id, owner=owner, groups=[]):
        return {"ok": False, "reason": "identity_denied"}
    if run_as_root:
        return {"ok": True, "identity": {"username": owner, "uid": 0, "gid": 0,
                                         "groups": [], "privileged": True}}
    if lookup is None:
        return {"ok": False, "reason": "ldap_not_configured"}
    try:
        resolved = lookup.lookup(owner)
    except Exception:
        return {"ok": False, "reason": "ldap_unavailable"}
    if resolved is None:
        return {"ok": False, "reason": "ldap_identity_not_found"}
    if control.is_denied(requester=requester_id, owner=owner,
                         groups=resolved.get("groups", [])):
        return {"ok": False, "reason": "identity_denied"}
    try:
        control.register_probe_target(owner)
    except Exception:
        pass  # fail-soft: 프로브 등록 실패가 잡을 막지 않는다 (게이트는 노드 증거가 담당)
    return {"ok": True, "identity": {**resolved, "privileged": False}}
```

- [ ] **Step 4: 통과 확인** — 전체 PASS
- [ ] **Step 5: Commit** — `feat: identity resolve 플로우 (denylist 최우선, root 합성, probe 등록)`

---

### Task 5: 도구/노드 자동 선택 (`selection.py`)

**Files:**
- Create: `src/dms/selection.py`
- Test: `tests/test_selection.py`

**Interfaces:**
- Consumes: 에이전트 fresh_reports 형식, policies 행.
- Produces:
  - `node_candidates(reports, *, storage_name, tool, username, require_writable, privileged) -> tuple[list[str], dict[str, str]]` — (적격 노드 오름차순, 노드별 탈락 사유). 판정 순서: 마운트 없음/`status != "Ready"` → `missing_target_mount`; require_writable이고 `writable`이 truthy 아님 → `target_mount_not_writable`; tools에 해당 tool이 Ready 아님 → `missing_tool:<tool>`; privileged 아니고 identities에 username이 Ready 아님 → `identity_not_ready_on_node`
  - `select_execution(job: dict, identity: dict, reports: list[dict], policy_for) -> dict`
    - `policy_for(tool_name) -> dict | None` (policies 행)
    - scan → dscan(storage_name, writable 불요) / rm → drm(storage_name, **writable 필수**)
    - sync: dsync 후보 = source_storage와 destination_storage **둘 다** 적격인 노드(dest는 writable 필수) — ≥1이면 dsync; 아니면 nsync: source 후보(src, dsync 불요 writable) ≥1 AND destination 후보(dst, writable) ≥1 → nsync; 둘 다 아니면 `{"ok": False, "reason": "no_ready_sync_candidate", "rejections": {...}}`
    - 정책: 행 없음 → `missing_policy`, enabled=0 → `policy_disabled`. cap: `nodes = eligible[:max_nodes]` (nsync는 각 role별로 cap)
    - 반환: `{"ok": True, "tool", "policy", "nodes": [...]}` 또는 nsync는 `{"ok": True, "tool": "nsync", "policy", "source_nodes": [...], "destination_nodes": [...]}`
    - 적격 0 (scan/rm/dsync 경로에서 nsync 폴백도 불가) → `{"ok": False, "reason": "no_eligible_nodes", "rejections"}` — 단 sync는 위의 `no_ready_sync_candidate`
    - identity 사유로만 전멸했는지 표시: `"identity_only": True/False` (모든 rejection 값이 identity_not_ready_on_node일 때 True — stepper의 pending 대기 판단용)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_selection.py
from dms.selection import node_candidates, select_execution

POLICIES = {
    "scan": {"tool": "scan", "max_nodes": 2, "procs_per_node": 4, "enabled": 1,
             "preview_timeout_seconds": None, "execution_timeout_seconds": 3600},
    "rm": {"tool": "rm", "max_nodes": 2, "procs_per_node": 4, "enabled": 1,
           "preview_timeout_seconds": 1800, "execution_timeout_seconds": 3600},
    "dsync": {"tool": "dsync", "max_nodes": 2, "procs_per_node": 4, "enabled": 1,
              "preview_timeout_seconds": 3600, "execution_timeout_seconds": 259200},
    "nsync": {"tool": "nsync", "max_nodes": 1, "procs_per_node": 4, "enabled": 1,
              "preview_timeout_seconds": 3600, "execution_timeout_seconds": 259200},
}


def _report(node, mounts, tools=("dscan", "dsync", "nsync", "drm"),
            identities=("alice",)):
    return {"node_name": node, "fresh": True, "report": {
        "mounts": [{"storage_name": s, "status": st, "writable": w}
                   for s, st, w in mounts],
        "tools": [{"name": t, "status": "Ready"} for t in tools],
        "identities": [{"username": u, "status": "Ready"} for u in identities],
    }}


ID = {"username": "alice", "privileged": False}


def test_node_candidates_reasons():
    reports = [
        _report("n1", [("s1", "Ready", True)]),
        _report("n2", [("s1", "Ready", False)]),
        _report("n3", [("s1", "Missing", False)]),
        _report("n4", [("s1", "Ready", True)], tools=("dsync",)),
        _report("n5", [("s1", "Ready", True)], identities=()),
    ]
    ok, rejections = node_candidates(reports, storage_name="s1", tool="dscan",
                                     username="alice", require_writable=True,
                                     privileged=False)
    assert ok == ["n1"]
    assert rejections == {"n2": "target_mount_not_writable",
                          "n3": "missing_target_mount",
                          "n4": "missing_tool:dscan",
                          "n5": "identity_not_ready_on_node"}


def test_privileged_skips_identity_check():
    reports = [_report("n1", [("s1", "Ready", True)], identities=())]
    ok, _ = node_candidates(reports, storage_name="s1", tool="dscan",
                            username="root", require_writable=False,
                            privileged=True)
    assert ok == ["n1"]


def test_scan_and_cap():
    reports = [_report(f"n{i}", [("s1", "Ready", False)]) for i in range(1, 4)]
    out = select_execution({"operation": "scan", "storage_name": "s1"}, ID,
                           reports, POLICIES.get)
    assert out["ok"] and out["tool"] == "dscan"
    assert out["nodes"] == ["n1", "n2"]  # max_nodes=2 cap


def test_rm_requires_writable():
    reports = [_report("n1", [("s1", "Ready", False)])]
    out = select_execution({"operation": "rm", "storage_name": "s1"}, ID,
                           reports, POLICIES.get)
    assert out == {"ok": False, "reason": "no_eligible_nodes",
                   "rejections": {"n1": "target_mount_not_writable"},
                   "identity_only": False}


def test_sync_prefers_dsync_when_comounted():
    reports = [
        _report("n1", [("src", "Ready", False), ("dst", "Ready", True)]),
        _report("n2", [("src", "Ready", False)]),
    ]
    out = select_execution({"operation": "sync", "source_storage": "src",
                            "destination_storage": "dst"}, ID, reports,
                           POLICIES.get)
    assert out["ok"] and out["tool"] == "dsync" and out["nodes"] == ["n1"]


def test_sync_falls_back_to_nsync():
    reports = [
        _report("n1", [("src", "Ready", False)]),
        _report("n2", [("src", "Ready", False)]),
        _report("n3", [("dst", "Ready", True)]),
    ]
    out = select_execution({"operation": "sync", "source_storage": "src",
                            "destination_storage": "dst"}, ID, reports,
                           POLICIES.get)
    assert out["ok"] and out["tool"] == "nsync"
    assert out["source_nodes"] == ["n1"]      # nsync max_nodes=1 cap (role별)
    assert out["destination_nodes"] == ["n3"]


def test_sync_no_candidates():
    out = select_execution({"operation": "sync", "source_storage": "src",
                            "destination_storage": "dst"}, ID, [], POLICIES.get)
    assert out["ok"] is False and out["reason"] == "no_ready_sync_candidate"


def test_policy_missing_and_disabled():
    reports = [_report("n1", [("s1", "Ready", False)])]
    out = select_execution({"operation": "scan", "storage_name": "s1"}, ID,
                           reports, lambda t: None)
    assert out == {"ok": False, "reason": "missing_policy"}
    disabled = {"scan": {**POLICIES["scan"], "enabled": 0}}
    out = select_execution({"operation": "scan", "storage_name": "s1"}, ID,
                           reports, disabled.get)
    assert out == {"ok": False, "reason": "policy_disabled"}


def test_identity_only_flag():
    reports = [_report("n1", [("s1", "Ready", False)], identities=())]
    out = select_execution({"operation": "scan", "storage_name": "s1"}, ID,
                           reports, POLICIES.get)
    assert out["ok"] is False and out["identity_only"] is True
```

- [ ] **Step 2: 실패 확인** — ImportError
- [ ] **Step 3: 구현**

```python
# src/dms/selection.py
"""도구/노드 자동 선택 (스펙 §5): 신선한 에이전트 증거만 사용, fan-out은 상한."""


def _mount_entry(report, storage_name):
    for mount in (report or {}).get("mounts", []):
        if mount.get("storage_name") == storage_name: