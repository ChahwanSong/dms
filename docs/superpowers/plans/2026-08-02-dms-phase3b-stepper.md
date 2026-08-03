# DMS Phase 3b — Job-Stepper + preview/confirm/cancel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 3a가 emit한 `Pending` data_job을 job-stepper 루프가 **비블로킹 스텝 모델**로 전진시킨다 — preflight → (sync/rm) preview → confirm 대기 → execution → 터미널. 실행은 주입된 **ExecutionAdapter**(3b는 stub, 3c에서 live Volcano) 뒤에 있고, 요청자는 상태를 폴링하며 sync/rm은 preview 지문을 confirm한다. cancel은 실행 중지 후 기록.

**Architecture:** 스펙 §5의 실행 부분. job-stepper는 controller의 재시작 가능한 `run_once()` 루프로, "진행할 차례인 잡"을 리스로 원자적으로 잡아 **한 스텝만** 수행하고 놓는다(MPI 잡이 도는 동안 아무도 블로킹 안 함). 잡의 phase별 외부 참조(preflight/preview/execution ref)와 지문은 전부 DB(`data_jobs`)에 있어, 어느 스텝에서 죽어도 다음 루프가 이어간다. 실행 자체는 `ExecutionAdapter` 프로토콜 뒤 — 3b는 결정적 stub으로 전 라이프사이클을 SQLite만으로 검증한다.

**Tech Stack:** Phase 1·2·3a 코드 위에 Python 3.11+, stdlib only (실 Volcano/LDAP은 3c).

## Global Constraints

- 스펙이 진실: `docs/superpowers/specs/2026-08-02-dms-clean-slate-design.md` §5. legacy 재사용 금지 (읽기 전용 참고만).
- 모든 런타임 SQL은 `src/dms/repositories/` 안에만 (스키마 DDL은 `migrations.py`). named param `:name`, SQLite/PG 호환.
- **비블로킹**: 스텝 하나는 잡 제출 / Volcano 상태 폴링 / 아티팩트 파싱 중 **하나만** 하고 반환. 어떤 스텝도 잡 완료를 기다리지 않는다.
- **preview→confirm 게이트(sync/rm 필수)**: preview는 `--dryrun`, summary sha256 지문 계산. **빈 summary는 지문 없음 → confirm 불가**. confirm은 요청자가 지문 제시(불일치 409). preview TTL(기본 24h) 만료 → `PreviewExpired`. root(privileged)도 게이트 우회 불가.
- **fail-closed**: preflight 실패 → `Rejected`(사유 코드), 실행 실패 → `Failed`/`TimedOut`. cancel은 **실행 중지 성공 후에만** DB를 `Cancelled`로 기록(거짓 취소 금지).
- **execution 전 preflight 재검증** (confirm 사이에 신원/후보 변화 가능).
- 모든 상태 전이는 `state_transitions`(entity_kind `data_job`/`request`)에 기록. 잡 터미널 시 요청도 종결(Succeeded→Succeeded, Failed/TimedOut→Failed, Cancelled→Cancelled, PreviewExpired→Rejected) + `results` 기록.
- 사유 코드 snake_case. 시각 `utc_now_iso`/`iso_plus`. JSON은 `dump_json`/`load_json`.
- **SECURITY (Phase 3a 이월 BLOCKER)**: 특권 root 잡 제출은 API 경계에서 인가돼야 한다. 이 플랜의 Task 9(요청 제출 특권 게이트)가 이를 닫는다 — 스펙 §5 "포탈 관리자 인터페이스에서만 제출 가능".
- 전체 테스트는 서비스 없이 SQLite로 (`.venv/bin/pytest` 단독, 0 warnings — filterwarnings=error).
- 커밋: conventional commit + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`, 태스크마다 커밋.

## Phase 1·2·3a가 제공하는 인터페이스 (전제 — 변경 금지)

- `dms.domain`: `DataJobState`(Pending/Preflight/PreviewRunning/ConfirmPending/Executing/Running/Succeeded/Failed/TimedOut/Cancelled/Rejected/PreviewExpired), `TERMINAL_DATA_JOB_STATES`, `RequestState`, `Operation`.
- `DataJobsRepository`(`.data_jobs`): `get_job(job_id)`(options/worker_pool/precondition/result_summary/volcano_job_ref dict 역직렬화), `list_jobs(*, request_id=None, limit)`, `set_job_state(job_id, DataJobState, *, reason_code=None, actor)`, `job_transitions(job_id)`. **컬럼**: job_id/request_id/operation/tool/storage_name/source_storage/destination_storage/source/destination/target/options/priority/state/reason_code/preview_fingerprint/preview_expires_at/volcano_job_ref/artifact_uri/result_summary/worker_pool/precondition/created_at/updated_at.
- `RequestsRepository`(`.requests`): `get`, `set_state`, `record_result`, `list_pending`, `transitions`.
- `ControlRepository`(`.control`): `try_acquire_lease(component, holder, lease_seconds)`, `is_denied`, `register_probe_target`, `control_state()`(maintenance/drain 0|1).
- `AgentsRepository`(`.agents`): `fresh_reports(*, stale_seconds, now_iso=None)`.
- `dms.identity`: `resolve_job_identity(control, resolver, *, requester_id, owner_username, allow_privileged, privileged_requesters) -> ResolvedIdentity`(username/uid/gid/groups tuple/privileged), `IdentityRejected(reason_code)`.
- `dms.placement`: `select_tool_and_candidates(...)`, `PlacementError`.
- `dms.config.Settings`: `agent_report_stale_seconds`, `allow_privileged_requesters`, `privileged_requesters`, `planner_interval_seconds`, `_SERVER_INT_KEYS`, `_parse_int`.
- `dms.controller`: `Loop`, `build_loops(settings, repos, *, identity_resolver=None)`, `run_all_once`, `run_forever`.
- `dms.api.app.create_app`; `dms.api.auth`: `Identity(actor, role)`, `require_user`, `require_admin`. `app.state.repos`, `app.state.settings`, `app.state.identity_resolver`.
- `worker_pool` dict(planner가 채움): `{tool, identity{username,uid,gid,groups(list),privileged}, candidates{primary|source,destination}, rejections, node_count, process_count, queue, priority_class, (nsync: source_count/destination_count)}`.

## File Structure

```
src/dms/migrations.py                 # (수정) data_jobs에 confirmed_fingerprint TEXT, phase_refs TEXT 컬럼 (ALTER fallback로)
src/dms/execution.py                  # ExecutionAdapter 프로토콜, ExecStatus, StubExecutionAdapter, ExecutionError
src/dms/repositories/data_jobs.py     # (수정) 스텝퍼용 조회/갱신 메서드 (claim_steppable, set_phase_ref, set_preview, set_confirmed, set_artifact)
src/dms/repositories/requests.py      # (수정) finalize_from_job (잡 터미널 → 요청 종결 매핑)
src/dms/stepper.py                    # JobStepper.run_once (비블로킹 스텝 머신)
src/dms/api/routes_jobs.py            # 잡 상세 조회 + confirm + cancel
src/dms/api/routes_requests.py        # (수정) 특권 게이트 (owner_username + privileged 인가)
src/dms/api/app.py                    # (수정) app.state.execution_adapter, routes_jobs 마운트
src/dms/config.py                     # (수정) stepper 주기 + preview TTL knob
src/dms/controller.py                 # (수정) build_loops에 job-stepper 루프
tests/test_execution.py
tests/test_repo_data_jobs_stepper.py
tests/test_repo_requests_finalize.py
tests/test_stepper_scan.py
tests/test_stepper_sync.py
tests/test_stepper_cancel.py
tests/test_api_jobs.py
tests/test_api_requests_privileged.py
tests/test_controller_stepper.py
```

## 스텝퍼 상태머신 (이 플랜의 핵심 — 각 run_once가 한 잡을 한 스텝 전진)

```
Pending    → [preflight 제출]         → Preflight   (phase_refs.preflight = ref)
Preflight  → [poll]  Running          → (그대로, 다음 루프)
                     Succeeded         → scan: [exec 제출] Running / sync,rm: [preview 제출] PreviewRunning
                     Failed            → Rejected (preflight_failed:<reason>)
PreviewRunning → [poll] Running        → (그대로)
                        Succeeded       → 지문 계산: 있으면 ConfirmPending(preview_expires_at=now+TTL), 없으면 Rejected(empty_preview)
                        Failed          → Failed (preview_failed)
ConfirmPending → (스텝퍼 관여 안 함 — confirm API 대기). 단 preview_expires_at 지나면 → PreviewExpired
Executing(sync/rm, confirm 후) → [preflight 재검증] 실패 → Rejected(execution_recheck_failed) / 통과 → [exec 제출] (phase_refs.execution=ref) 상태 유지 Executing
Executing → [poll]  Running            → (그대로)
                    Succeeded           → Succeeded (+ artifact summary 저장)
                    Failed/TimedOut     → Failed/TimedOut
Running(scan exec) → [poll] 동일하게 Succeeded/Failed/TimedOut
```

- "진행할 차례인 잡" = 상태 ∈ {Pending, Preflight, PreviewRunning, Executing, Running} (ConfirmPending·터미널 제외). ConfirmPending은 만료 스윕에서만 처리.
- 한 run_once는 한 잡에 대해 위 화살표 **한 개**만 수행(제출 또는 poll 또는 파싱). Volcano가 도는 동안엔 poll이 Running을 반환 → 상태 불변 → 다음 루프.

---

### Task 1: data_jobs 스키마 확장 (스텝퍼 컬럼)

**Files:**
- Modify: `src/dms/migrations.py`
- Test: `tests/test_migrations.py`에 추가

**Interfaces:**
- Consumes: 기존 `_column_exists`/`_ensure_columns`(Phase 3a가 추가).
- Produces: data_jobs에 컬럼 2개 — `confirmed_fingerprint TEXT`(confirm 시 요청자가 제시한 지문 저장), `phase_refs TEXT`(JSON: `{"preflight": ref, "preview": ref, "execution": ref}`). CREATE TABLE 문에도 추가하고 `_ensure_columns`의 튜플에도 추가(fresh는 CREATE, 구형은 ALTER — Phase 3a 패턴 그대로).

- [ ] **Step 1: 환경 셋업 + 실패 테스트**

먼저 venv: `python3 -m venv .venv && .venv/bin/pip install -q -e ".[test]"` 후 `.venv/bin/pytest -q`로 기존 184 passed 확인.

```python
# tests/test_migrations.py 에 추가
def test_migrate_adds_stepper_columns_to_existing_data_jobs(db):
    from dms.migrations import migrate, _column_exists
    db.execute("ALTER TABLE data_jobs DROP COLUMN confirmed_fingerprint") \
        if _column_exists(db, "data_jobs", "confirmed_fingerprint") else None
    # 구형 흉내: 컬럼 없는 상태에서 migrate가 추가
    migrate(db)
    assert _column_exists(db, "data_jobs", "confirmed_fingerprint")
    assert _column_exists(db, "data_jobs", "phase_refs")
```

(주의: SQLite 3.35+ 만 `DROP COLUMN` 지원. 안전하게는 아래처럼 구형 테이블 재생성 방식을 쓴다 — Phase 3a의 `test_migrate_adds_columns_to_existing_data_jobs` 패턴을 따라, data_jobs를 confirmed_fingerprint/phase_refs 없이 재생성한 뒤 migrate 호출. 구현자는 Phase 3a 테스트를 참고해 동일 방식으로 작성하라.)

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_migrations.py -v`
Expected: FAIL — 컬럼 없음

- [ ] **Step 3: 구현**

migrations.py의 data_jobs CREATE TABLE에서 `precondition TEXT,` 다음에:
```
            precondition TEXT,
            confirmed_fingerprint TEXT,
            phase_refs TEXT,
```
`_ensure_columns`의 튜플에 추가:
```python
        ("data_jobs", "worker_pool", "TEXT"),
        ("data_jobs", "precondition", "TEXT"),
        ("data_jobs", "confirmed_fingerprint", "TEXT"),
        ("data_jobs", "phase_refs", "TEXT"),
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_migrations.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/migrations.py tests/test_migrations.py
git commit -m "feat: data_jobs에 스텝퍼 컬럼 (confirmed_fingerprint, phase_refs)"
```

---

### Task 2: 실행 어댑터 (`execution.py`)

**Files:**
- Create: `src/dms/execution.py`
- Test: `tests/test_execution.py`

**Interfaces:**
- Consumes: 없음 (순수 모듈, DB 모름).
- Produces:
  - `class ExecStatus(StrEnum)`: `PENDING="Pending"`, `RUNNING="Running"`, `SUCCEEDED="Succeeded"`, `FAILED="Failed"`, `TIMED_OUT="TimedOut"`
  - `@dataclass(frozen=True) JobSpec`: `job_id: str`, `phase: str`(preflight|preview|execution), `operation: str`, `tool: str`, `dryrun: bool`, `identity: dict`, `paths: dict`(source/destination/target 등), `options: dict`, `candidates: dict`, `process_count: int`, `queue: str`, `priority_class: str`, `artifact_base: str`
  - `class ExecutionError(Exception)`: `reason_code`, `detail`
  - `class ExecutionAdapter(Protocol)`:
    - `submit(spec: JobSpec) -> str` — 외부 참조(ref) 반환. 실패 시 `ExecutionError`
    - `poll(ref: str) -> ExecStatus`
    - `read_summary(ref: str) -> dict | None` — 완료된 잡의 summary(빈 dict/None 가능)
    - `terminate(ref: str) -> None` — 멱등, 실패 시 `ExecutionError`
  - `class StubExecutionAdapter`: 결정적 테스트용. `__init__(self)` — 내부 상태 dict. 메서드:
    - `submit(spec)` — ref = `f"stub-{spec.phase}-{spec.job_id}"` 반환, 내부에 `{ref: {"status": PENDING, "summary": None, "terminated": False, "spec": spec}}` 기록
    - `poll(ref)` — 스크립트된 상태 반환. `script(ref, statuses: list[ExecStatus])`로 다음 poll들의 반환열을 주입; 기본은 즉시 SUCCEEDED. terminate된 ref는 FAILED.
    - `read_summary(ref)` — `set_summary(ref, summary)`로 주입한 값(기본 `{"files": 0, "bytes": 0}`)
    - `terminate(ref)` — 존재하면 terminated=True; 없는 ref도 no-op(멱등). `fail_terminate(ref)`로 표시된 ref는 `ExecutionError` raise
    - 테스트 헬퍼: `script(ref, statuses)`, `set_summary(ref, summary)`, `fail_submit(job_phase)`(다음 submit이 그 phase면 ExecutionError), `fail_terminate(ref)`, `submitted_specs() -> list[JobSpec]`

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_execution.py
import pytest
from dms.execution import (
    ExecStatus, ExecutionError, JobSpec, StubExecutionAdapter)


def _spec(phase="preflight", job_id="j1", dryrun=False):
    return JobSpec(job_id=job_id, phase=phase, operation="scan", tool="dscan",
                   dryrun=dryrun, identity={"uid": 10001}, paths={"target": "a"},
                   options={}, candidates={"primary": ["n1"]}, process_count=8,
                   queue="dms-data", priority_class="dms-mid",
                   artifact_base="file:///art")


def test_submit_returns_ref_and_records_spec():
    a = StubExecutionAdapter()
    ref = a.submit(_spec(phase="preview"))
    assert ref == "stub-preview-j1"
    assert a.submitted_specs()[0].phase == "preview"


def test_poll_scripted_sequence_then_default():
    a = StubExecutionAdapter()
    ref = a.submit(_spec())
    a.script(ref, [ExecStatus.RUNNING, ExecStatus.RUNNING, ExecStatus.SUCCEEDED])
    assert [a.poll(ref) for _ in range(3)] == [
        ExecStatus.RUNNING, ExecStatus.RUNNING, ExecStatus.SUCCEEDED]


def test_poll_default_is_succeeded():
    a = StubExecutionAdapter()
    ref = a.submit(_spec())
    assert a.poll(ref) == ExecStatus.SUCCEEDED


def test_read_summary_default_and_override():
    a = StubExecutionAdapter()
    ref = a.submit(_spec())
    assert a.read_summary(ref) == {"files": 0, "bytes": 0}
    a.set_summary(ref, {"files": 5})
    assert a.read_summary(ref) == {"files": 5}


def test_terminate_is_idempotent_and_marks_failed():
    a = StubExecutionAdapter()
    ref = a.submit(_spec())
    a.terminate(ref)
    a.terminate(ref)  # 멱등
    a.terminate("nonexistent")  # no-op
    assert a.poll(ref) == ExecStatus.FAILED


def test_fail_submit_and_fail_terminate():
    a = StubExecutionAdapter()
    a.fail_submit("execution")
    with pytest.raises(ExecutionError):
        a.submit(_spec(phase="execution"))
    ref = a.submit(_spec(phase="preflight"))
    a.fail_terminate(ref)
    with pytest.raises(ExecutionError):
        a.terminate(ref)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_execution.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 구현**

```python
# src/dms/execution.py
"""실행 어댑터 경계. 잡 제출/폴링/아티팩트 읽기/종료를 추상화. 3b는 결정적 stub, 3c는 live Volcano."""
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ExecStatus(StrEnum):
    PENDING = "Pending"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    TIMED_OUT = "TimedOut"


@dataclass(frozen=True)
class JobSpec:
    job_id: str
    phase: str
    operation: str
    tool: str
    dryrun: bool
    identity: dict
    paths: dict
    options: dict
    candidates: dict
    process_count: int
    queue: str
    priority_class: str
    artifact_base: str


class ExecutionError(Exception):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


class ExecutionAdapter(Protocol):
    def submit(self, spec: JobSpec) -> str: ...
    def poll(self, ref: str) -> ExecStatus: ...
    def read_summary(self, ref: str) -> "dict | None": ...
    def terminate(self, ref: str) -> None: ...


class StubExecutionAdapter:
    def __init__(self):
        self._jobs = {}
        self._scripts = {}
        self._summaries = {}
        self._fail_submit_phase = None
        self._fail_terminate_refs = set()
        self._submitted = []

    def submit(self, spec: JobSpec) -> str:
        if self._fail_submit_phase is not None and spec.phase == self._fail_submit_phase:
            raise ExecutionError("submit_failed", spec.phase)
        ref = f"stub-{spec.phase}-{spec.job_id}"
        self._jobs[ref] = {"terminated": False}
        self._submitted.append(spec)
        return ref

    def poll(self, ref: str) -> ExecStatus:
        if self._jobs.get(ref, {}).get("terminated"):
            return ExecStatus.FAILED
        queue = self._scripts.get(ref)
        if queue:
            return queue.pop(0)
        return ExecStatus.SUCCEEDED

    def read_summary(self, ref: str):
        return self._summaries.get(ref, {"files": 0, "bytes": 0})

    def terminate(self, ref: str) -> None:
        if ref in self._fail_terminate_refs:
            raise ExecutionError("terminate_failed", ref)
        if ref in self._jobs:
            self._jobs[ref]["terminated"] = True

    # --- test helpers ---
    def script(self, ref, statuses):
        self._scripts[ref] = list(statuses)

    def set_summary(self, ref, summary):
        self._summaries[ref] = summary

    def fail_submit(self, phase):
        self._fail_submit_phase = phase

    def fail_terminate(self, ref):
        self._fail_terminate_refs.add(ref)

    def submitted_specs(self):
        return list(self._submitted)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_execution.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/execution.py tests/test_execution.py
git commit -m "feat: 실행 어댑터 경계 (프로토콜, JobSpec, 결정적 stub)"
```

---

### Task 3: 스텝퍼용 저장소 메서드 (`data_jobs.py` 확장)

**Files:**
- Modify: `src/dms/repositories/data_jobs.py`
- Test: `tests/test_repo_data_jobs_stepper.py`

**Interfaces:**
- Consumes: 기존 DataJobsRepository, `DataJobState`, `iso_plus`.
- Produces:
  - `claim_steppable(*, limit: int = 10) -> list[dict]` — 상태 ∈ {Pending, Preflight, PreviewRunning, Executing, Running}인 잡을 `updated_at` 오름차순으로 반환(dict 역직렬화). PG에선 `FOR UPDATE SKIP LOCKED`(방언 분기), sqlite에선 일반 SELECT. **핵심**: 이 메서드가 스텝퍼가 잡을 고르는 유일한 경로.
  - `set_phase_ref(job_id, phase: str, ref: str) -> None` — phase_refs JSON에 `{phase: ref}` 병합(한 트랜잭션 read-modify-write)
  - `set_preview(job_id, *, fingerprint: str | None, expires_at: str | None, artifact_uri: str | None) -> None` — preview_fingerprint/preview_expires_at/artifact_uri 갱신
  - `set_confirmed(job_id, fingerprint: str) -> None` — confirmed_fingerprint 저장
  - `set_artifact(job_id, *, artifact_uri: str | None, result_summary: dict | None) -> None`
  - `list_confirmable(job_id) -> dict | None` — get_job 별칭 (confirm API용, 명시적 이름)
  - `expire_previews(*, now_iso: str) -> list[str]` — ConfirmPending이고 preview_expires_at < now인 잡을 `PreviewExpired`로 전이(+ 전이 기록), 전이된 job_id 리스트 반환. actor `stepper`.

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_repo_data_jobs_stepper.py
from dms.domain import DataJobState
from dms.repositories import Repositories


def _job(repos, state="Pending"):
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
                                resource_key="k", payload={}, priority="mid")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s1", target="a", options={}, tool="dscan",
        worker_pool={}, precondition={}, actor="planner")
    if state != "Pending":
        repos.data_jobs.set_job_state(jid, DataJobState(state), actor="test")
    return jid


def test_claim_steppable_selects_active_states(db):
    repos = Repositories(db)
    j_pending = _job(repos, "Pending")
    j_preflight = _job(repos, "Preflight")
    j_confirm = _job(repos, "ConfirmPending")   # 제외
    j_done = _job(repos, "Succeeded")            # 제외
    ids = {j["job_id"] for j in repos.data_jobs.claim_steppable()}
    assert j_pending in ids and j_preflight in ids
    assert j_confirm not in ids and j_done not in ids


def test_set_phase_ref_merges(db):
    repos = Repositories(db)
    jid = _job(repos)
    repos.data_jobs.set_phase_ref(jid, "preflight", "ref-pf")
    repos.data_jobs.set_phase_ref(jid, "execution", "ref-ex")
    assert repos.data_jobs.get_job(jid)["phase_refs"] == {
        "preflight": "ref-pf", "execution": "ref-ex"}


def test_set_preview_and_confirmed(db):
    repos = Repositories(db)
    jid = _job(repos)
    repos.data_jobs.set_preview(jid, fingerprint="sha256:abc",
                                expires_at="2026-08-03T10:00:00Z",
                                artifact_uri="file:///art/j")
    repos.data_jobs.set_confirmed(jid, "sha256:abc")
    job = repos.data_jobs.get_job(jid)
    assert job["preview_fingerprint"] == "sha256:abc"
    assert job["confirmed_fingerprint"] == "sha256:abc"
    assert job["preview_expires_at"] == "2026-08-03T10:00:00Z"


def test_set_artifact(db):
    repos = Repositories(db)
    jid = _job(repos)
    repos.data_jobs.set_artifact(jid, artifact_uri="file:///art/j",
                                 result_summary={"files": 3})
    job = repos.data_jobs.get_job(jid)
    assert job["artifact_uri"] == "file:///art/j"
    assert job["result_summary"] == {"files": 3}


def test_expire_previews(db):
    repos = Repositories(db)
    jid = _job(repos, "ConfirmPending")
    repos.data_jobs.set_preview(jid, fingerprint="f", expires_at="2026-08-02T09:00:00Z",
                                artifact_uri=None)
    expired = repos.data_jobs.expire_previews(now_iso="2026-08-02T10:00:00Z")
    assert expired == [jid]
    assert repos.data_jobs.get_job(jid)["state"] == "PreviewExpired"
    # 만료 안 된 것은 그대로
    j2 = _job(repos, "ConfirmPending")
    repos.data_jobs.set_preview(j2, fingerprint="f", expires_at="2026-08-02T11:00:00Z",
                                artifact_uri=None)
    assert repos.data_jobs.expire_previews(now_iso="2026-08-02T10:00:00Z") == []
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_repo_data_jobs_stepper.py -v`
Expected: FAIL — AttributeError

- [ ] **Step 3: 구현 (data_jobs.py에 메서드 추가)**

```python
    _STEPPABLE_STATES = ("Pending", "Preflight", "PreviewRunning", "Executing", "Running")

    def claim_steppable(self, *, limit: int = 10):
        placeholders = ", ".join(f":s{i}" for i in range(len(self._STEPPABLE_STATES)))
        params = {f"s{i}": v for i, v in enumerate(self._STEPPABLE_STATES)}
        params["n"] = limit
        suffix = " FOR UPDATE SKIP LOCKED" if self._db.dialect == "postgresql" else ""
        rows = self._db.query(
            f"""SELECT * FROM data_jobs WHERE state IN ({placeholders})
                ORDER BY updated_at LIMIT :n{suffix}""", params)
        return [self._hydrate(r) for r in rows]

    def set_phase_ref(self, job_id, phase, ref):
        now = utc_now_iso()
        with self._db.transaction():
            row = self._db.query_one(
                "SELECT phase_refs FROM data_jobs WHERE job_id = :j", {"j": job_id})
            refs = load_json(row["phase_refs"]) or {}
            refs[phase] = ref
            self._db.execute(
                "UPDATE data_jobs SET phase_refs = :p, updated_at = :now WHERE job_id = :j",
                {"p": dump_json(refs), "now": now, "j": job_id})

    def set_preview(self, job_id, *, fingerprint, expires_at, artifact_uri):
        self._db.execute(
            """UPDATE data_jobs SET preview_fingerprint = :f, preview_expires_at = :e,
                   artifact_uri = COALESCE(:a, artifact_uri), updated_at = :now
               WHERE job_id = :j""",
            {"f": fingerprint, "e": expires_at, "a": artifact_uri,
             "now": utc_now_iso(), "j": job_id})

    def set_confirmed(self, job_id, fingerprint):
        self._db.execute(
            "UPDATE data_jobs SET confirmed_fingerprint = :f, updated_at = :now WHERE job_id = :j",
            {"f": fingerprint, "now": utc_now_iso(), "j": job_id})

    def set_artifact(self, job_id, *, artifact_uri, result_summary):
        self._db.execute(
            """UPDATE data_jobs SET artifact_uri = COALESCE(:a, artifact_uri),
                   result_summary = :s, updated_at = :now WHERE job_id = :j""",
            {"a": artifact_uri,
             "s": dump_json(result_summary) if result_summary is not None else None,
             "now": utc_now_iso(), "j": job_id})

    def list_confirmable(self, job_id):
        return self.get_job(job_id)

    def expire_previews(self, *, now_iso):
        rows = self._db.query(
            """SELECT job_id FROM data_jobs
               WHERE state = :s AND preview_expires_at IS NOT NULL
                 AND preview_expires_at < :now""",
            {"s": DataJobState.CONFIRM_PENDING.value, "now": now_iso})
        expired = []
        for row in rows:
            self.set_job_state(row["job_id"], DataJobState.PREVIEW_EXPIRED,
                               reason_code="preview_expired", actor="stepper")
            expired.append(row["job_id"])
        return expired
```

(`iso_plus`는 이 태스크에서 불필요 — expires_at는 인자로 받음. `load_json`/`dump_json`/`utc_now_iso`는 이미 import됨.)

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_repo_data_jobs_stepper.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/repositories/data_jobs.py tests/test_repo_data_jobs_stepper.py
git commit -m "feat: 스텝퍼용 data_jobs 메서드 (claim, phase ref, preview/confirm, preview 만료)"
```

---

### Task 4: 요청 종결 매핑 (`requests.py` 확장)

**Files:**
- Modify: `src/dms/repositories/requests.py`
- Test: `tests/test_repo_requests_finalize.py`

**Interfaces:**
- Consumes: 기존 RequestsRepository, `DataJobState`, `RequestState`.
- Produces:
  - `finalize_from_job(request_id, job_state: DataJobState, *, reason_code=None, summary=None, actor) -> None` — 잡 터미널 상태를 요청 종결로 매핑 후 set_state + record_result:
    - `Succeeded` → `RequestState.SUCCEEDED`
    - `Failed`/`TimedOut` → `RequestState.FAILED`
    - `Cancelled` → `RequestState.CANCELLED`
    - `Rejected`/`PreviewExpired` → `RequestState.REJECTED`
    - 그 외(비터미널) → `ValueError`
  - 이미 종결된 요청이면 no-op (idempotent — 스텝퍼 재시작 대비): set_state 전에 현재 상태가 터미널이면 건너뜀.

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_repo_requests_finalize.py
import pytest
from dms.domain import DataJobState, RequestState
from dms.repositories import Repositories


def _req(repos):
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
                                resource_key="k", payload={}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="stepper")
    return rid


@pytest.mark.parametrize("job_state,expected", [
    (DataJobState.SUCCEEDED, "Succeeded"),
    (DataJobState.FAILED, "Failed"),
    (DataJobState.TIMED_OUT, "Failed"),
    (DataJobState.CANCELLED, "Cancelled"),
    (DataJobState.REJECTED, "Rejected"),
    (DataJobState.PREVIEW_EXPIRED, "Rejected"),
])
def test_finalize_maps_states(db, job_state, expected):
    repos = Repositories(db)
    rid = _req(repos)
    repos.requests.finalize_from_job(rid, job_state, reason_code="rc",
                                     summary={"n": 1}, actor="stepper")
    assert repos.requests.get(rid)["state"] == expected
    result = db.query_one("SELECT terminal_state, reason_code FROM results WHERE request_id = :r",
                          {"r": rid})
    assert result["terminal_state"] == expected and result["reason_code"] == "rc"


def test_finalize_nonterminal_raises(db):
    repos = Repositories(db)
    rid = _req(repos)
    with pytest.raises(ValueError):
        repos.requests.finalize_from_job(rid, DataJobState.PREFLIGHT, actor="stepper")


def test_finalize_is_idempotent(db):
    repos = Repositories(db)
    rid = _req(repos)
    repos.requests.finalize_from_job(rid, DataJobState.SUCCEEDED, actor="stepper")
    # 두 번째 호출은 no-op (이미 터미널)
    repos.requests.finalize_from_job(rid, DataJobState.FAILED, actor="stepper")
    assert repos.requests.get(rid)["state"] == "Succeeded"
    assert len(db.query("SELECT request_id FROM results WHERE request_id = :r", {"r": rid})) == 1
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_repo_requests_finalize.py -v`
Expected: FAIL — AttributeError

- [ ] **Step 3: 구현 (requests.py에 추가)**

```python
from ..domain import DataJobState  # 상단 import에 추가

    _JOB_TO_REQUEST = {
        DataJobState.SUCCEEDED: RequestState.SUCCEEDED,
        DataJobState.FAILED: RequestState.FAILED,
        DataJobState.TIMED_OUT: RequestState.FAILED,
        DataJobState.CANCELLED: RequestState.CANCELLED,
        DataJobState.REJECTED: RequestState.REJECTED,
        DataJobState.PREVIEW_EXPIRED: RequestState.REJECTED,
    }

    def finalize_from_job(self, request_id, job_state, *, reason_code=None,
                          summary=None, actor):
        target = self._JOB_TO_REQUEST.get(DataJobState(job_state))
        if target is None:
            raise ValueError(f"non-terminal job state: {job_state}")
        current = self._db.query_one(
            "SELECT state FROM requests WHERE request_id = :id", {"id": request_id})
        if current is None:
            raise KeyError(request_id)
        if RequestState(current["state"]) in TERMINAL_REQUEST_STATES:
            return  # idempotent
        self.set_state(request_id, target, reason_code=reason_code, actor=actor)
        self.record_result(request_id, target, reason_code=reason_code, summary=summary)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_repo_requests_finalize.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/repositories/requests.py tests/test_repo_requests_finalize.py
git commit -m "feat: 잡 터미널 → 요청 종결 매핑 (finalize_from_job, idempotent)"
```

---

### Task 5: 설정 확장 (`config.py`)

**Files:**
- Modify: `src/dms/config.py`
- Test: `tests/test_config_phase3b.py`

**Interfaces:**
- Consumes: 기존 `_SERVER_INT_KEYS`, `Settings`.
- Produces: `_SERVER_INT_KEYS`에 추가 + Settings 필드 추가:
  - `stepper_interval_seconds: int = 5` (`DMS_STEPPER_INTERVAL_SECONDS`)
  - `preview_ttl_seconds: int = 86400` (`DMS_PREVIEW_TTL_SECONDS`)
  - `artifact_base_uri: str = "file:///artifacts/dms"` (`DMS_ARTIFACT_BASE_URI`, 문자열 — `_SERVER_INT_KEYS` 아님, 별도 처리)

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_config_phase3b.py
from dms.config import Settings

VALID = {"DMS_DATABASE_URL": "sqlite:///tmp/dms.db", "DMS_SHARED_TOKEN": "tok",
         "DMS_ADMIN_TOKEN": "adm", "DMS_SESSION_SECRET": "sess"}


def test_stepper_defaults():
    s = Settings.from_env(VALID)
    assert s.stepper_interval_seconds == 5
    assert s.preview_ttl_seconds == 86400
    assert s.artifact_base_uri == "file:///artifacts/dms"


def test_stepper_overrides():
    s = Settings.from_env({**VALID, "DMS_STEPPER_INTERVAL_SECONDS": "2",
                           "DMS_PREVIEW_TTL_SECONDS": "3600",
                           "DMS_ARTIFACT_BASE_URI": "file:///cephfs/dms/artifacts"})
    assert s.stepper_interval_seconds == 2
    assert s.preview_ttl_seconds == 3600
    assert s.artifact_base_uri == "file:///cephfs/dms/artifacts"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_config_phase3b.py -v`
Expected: FAIL — AttributeError

- [ ] **Step 3: 구현**

`_SERVER_INT_KEYS`에 추가:
```python
    ("DMS_STEPPER_INTERVAL_SECONDS", "stepper_interval_seconds", 5),
    ("DMS_PREVIEW_TTL_SECONDS", "preview_ttl_seconds", 86400),
```
Settings 필드 추가 (기존 필드 뒤):
```python
    stepper_interval_seconds: int = 5
    preview_ttl_seconds: int = 86400
    artifact_base_uri: str = "file:///artifacts/dms"
```
`from_env`의 `return cls(...)`에 추가:
```python
            artifact_base_uri=environ.get("DMS_ARTIFACT_BASE_URI",
                                          "file:///artifacts/dms"),
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_config_phase3b.py tests/test_config.py tests/test_config_phase2.py tests/test_config_phase3.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/config.py tests/test_config_phase3b.py
git commit -m "feat: stepper 주기 + preview TTL + artifact base 설정"
```

---

### Task 6: JobStepper — scan 경로 (`stepper.py` 1/2)

**Files:**
- Create: `src/dms/stepper.py`
- Test: `tests/test_stepper_scan.py`

**Interfaces:**
- Consumes: `Repositories`, `ExecutionAdapter`/`JobSpec`/`ExecStatus`/`ExecutionError`, `DataJobState`, `Settings`.
- Produces:
  - `JobStepper(repos, execution_adapter, *, settings)`.
  - `.run_once() -> dict[str, str]` — `claim_steppable()`로 잡들을 얻어 각각 `_step_one(job)` 한 스텝. `{job_id: outcome}` 반환(outcome: 상태 문자열 또는 `error:<Type>`). control_state가 drain이면 조기 반환 `{}`. 요청별 try/except(한 잡 실패가 다음 잡 안 막음, stderr 로그).
  - `_step_one(job) -> str` — 잡 state에 따라:
    - **Pending**: preflight JobSpec(dryrun=False, phase="preflight") submit → `set_phase_ref(preflight)` + `set_job_state(Preflight)`. submit ExecutionError → `Rejected(preflight_submit_failed)`. 반환 "Preflight".
    - **Preflight**: `poll(phase_refs["preflight"])`. Running → "Preflight"(불변). Succeeded → scan이면 execution submit(dryrun=False, phase="execution") → `set_phase_ref(execution)` + `set_job_state(Running)` 반환 "Running". Failed → `Rejected(preflight_failed)`.
    - **Running**(scan exec): `poll(phase_refs["execution"])`. Running → 불변. Succeeded → `read_summary` → `set_artifact` + `set_job_state(Succeeded)` + `repos.requests.finalize_from_job(Succeeded, summary)`. Failed → Failed + finalize. TimedOut → TimedOut + finalize.
  - Task 6은 **scan 경로만** (Pending→Preflight→Running→Succeeded/Failed). sync/rm preview/confirm/execution 분기는 Task 7.
  - JobSpec 구성 헬퍼 `_build_spec(job, phase, dryrun)`: worker_pool에서 identity/candidates/process_count/queue/priority_class, paths는 operation별(scan/rm: {target, storage}, sync: {source, source_storage, destination, destination_storage}), artifact_base = settings.artifact_base_uri.

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_stepper_scan.py
from dms.execution import ExecStatus, StubExecutionAdapter
from dms.repositories import Repositories
from dms.stepper import JobStepper


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()


def _scan_job(repos):
    from dms.domain import RequestState
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k", payload={"storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s1", target="a", options={}, tool="dscan",
        worker_pool={"tool": "dscan", "identity": {"uid": 10001, "gid": 10000,
            "username": "alice", "groups": [], "privileged": False},
            "candidates": {"primary": ["n1"]}, "process_count": 8,
            "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def _stepper(repos, adapter):
    return JobStepper(repos, adapter, settings=_Settings())


def test_pending_to_preflight_submits(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    adapter.script(f"stub-preflight-{jid}", [ExecStatus.RUNNING])  # preflight 아직 안 끝남
    _stepper(repos, adapter).run_once()
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Preflight"
    assert job["phase_refs"]["preflight"] == f"stub-preflight-{jid}"
    assert adapter.submitted_specs()[0].phase == "preflight"
    assert adapter.submitted_specs()[0].dryrun is False


def test_full_scan_lifecycle_to_succeeded(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()  # 모든 poll 기본 Succeeded
    adapter.set_summary(f"stub-execution-{jid}", {"files": 42, "bytes": 1000})
    stepper = _stepper(repos, adapter)
    stepper.run_once()   # Pending → Preflight (submit)
    assert repos.data_jobs.get_job(jid)["state"] == "Preflight"
    stepper.run_once()   # Preflight poll Succeeded → Running (exec submit)
    assert repos.data_jobs.get_job(jid)["state"] == "Running"
    stepper.run_once()   # Running poll Succeeded → Succeeded
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Succeeded"
    assert job["result_summary"] == {"files": 42, "bytes": 1000}
    assert repos.requests.get(rid)["state"] == "Succeeded"


def test_preflight_failure_rejects(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()  # → Preflight
    adapter.script(f"stub-preflight-{jid}", [ExecStatus.FAILED])
    stepper.run_once()  # Preflight poll Failed → Rejected
    assert repos.data_jobs.get_job(jid)["state"] == "Rejected"
    assert repos.requests.get(rid)["state"] == "Rejected"


def test_execution_failure(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()  # Preflight
    stepper.run_once()  # Running (preflight succeeded)
    adapter.script(f"stub-execution-{jid}", [ExecStatus.FAILED])
    stepper.run_once()  # Running poll Failed → Failed
    assert repos.data_jobs.get_job(jid)["state"] == "Failed"
    assert repos.requests.get(rid)["state"] == "Failed"


def test_drain_stops_stepping(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    repos.control.set_control_state(maintenance=False, drain=True, reason="x", actor="admin")
    result = _stepper(repos, StubExecutionAdapter()).run_once()
    assert result == {}
    assert repos.data_jobs.get_job(jid)["state"] == "Pending"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_stepper_scan.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 구현**

```python
# src/dms/stepper.py
"""job-stepper: 계획된 data_job을 비블로킹 스텝으로 전진시키는 루프 본체. 실행은 어댑터 뒤."""
import sys

from .domain import DataJobState
from .execution import ExecStatus, ExecutionError, JobSpec


class JobStepper:
    def __init__(self, repos, execution_adapter, *, settings):
        self._repos = repos
        self._exec = execution_adapter
        self._settings = settings

    def run_once(self) -> dict:
        control = self._repos.control.control_state()
        if control and control["drain"]:
            return {}
        results = {}
        for job in self._repos.data_jobs.claim_steppable():
            jid = job["job_id"]
            try:
                results[jid] = self._step_one(job)
            except Exception as exc:
                print(f"stepper error on {jid}: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                results[jid] = f"error:{type(exc).__name__}"
        return results

    def _build_spec(self, job, phase, dryrun):
        wp = job["worker_pool"] or {}
        op = job["operation"]
        if op == "sync":
            paths = {"source": job["source"], "source_storage": job["source_storage"],
                     "destination": job["destination"],
                     "destination_storage": job["destination_storage"]}
        else:
            paths = {"target": job["target"], "storage": job["storage_name"]}
        return JobSpec(
            job_id=job["job_id"], phase=phase, operation=op, tool=job["tool"],
            dryrun=dryrun, identity=wp.get("identity", {}), paths=paths,
            options=job["options"] or {}, candidates=wp.get("candidates", {}),
            process_count=wp.get("process_count", 1), queue=wp.get("queue", "dms-data"),
            priority_class=wp.get("priority_class", "dms-mid"),
            artifact_base=self._settings.artifact_base_uri)

    def _finalize(self, job, job_state, *, reason_code=None, summary=None):
        self._repos.data_jobs.set_job_state(job["job_id"], job_state,
                                            reason_code=reason_code, actor="stepper")
        self._repos.requests.finalize_from_job(
            job["request_id"], job_state, reason_code=reason_code, summary=summary,
            actor="stepper")

    def _step_one(self, job) -> str:
        state = job["state"]
        jid = job["job_id"]
        if state == DataJobState.PENDING.value:
            return self._submit_preflight(job)
        if state == DataJobState.PREFLIGHT.value:
            return self._poll_preflight(job)
        if state == DataJobState.RUNNING.value:
            return self._poll_execution(job)
        # PreviewRunning / Executing 는 Task 7
        return state

    def _submit_preflight(self, job):
        jid = job["job_id"]
        try:
            ref = self._exec.submit(self._build_spec(job, "preflight", dryrun=False))
        except ExecutionError as exc:
            self._finalize(job, DataJobState.REJECTED,
                           reason_code=f"preflight_submit_failed:{exc.reason_code}")
            return "Rejected"
        self._repos.data_jobs.set_phase_ref(jid, "preflight", ref)
        self._repos.data_jobs.set_job_state(jid, DataJobState.PREFLIGHT, actor="stepper")
        return "Preflight"

    def _poll_preflight(self, job):
        jid = job["job_id"]
        ref = (job["phase_refs"] or {}).get("preflight")
        status = self._exec.poll(ref)
        if status in (ExecStatus.PENDING, ExecStatus.RUNNING):
            return "Preflight"
        if status == ExecStatus.SUCCEEDED:
            # scan: 바로 execution. (sync/rm preview는 Task 7)
            if job["operation"] == "scan":
                return self._submit_execution(job, DataJobState.RUNNING)
            return self._submit_preview(job)  # Task 7에서 구현
        self._finalize(job, DataJobState.REJECTED, reason_code="preflight_failed")
        return "Rejected"

    def _submit_execution(self, job, running_state):
        jid = job["job_id"]
        try:
            ref = self._exec.submit(self._build_spec(job, "execution", dryrun=False))
        except ExecutionError as exc:
            self._finalize(job, DataJobState.FAILED,
                           reason_code=f"execution_submit_failed:{exc.reason_code}")
            return "Failed"
        self._repos.data_jobs.set_phase_ref(jid, "execution", ref)
        self._repos.data_jobs.set_job_state(jid, running_state, actor="stepper")
        return running_state.value

    def _poll_execution(self, job):
        ref = (job["phase_refs"] or {}).get("execution")
        status = self._exec.poll(ref)
        if status in (ExecStatus.PENDING, ExecStatus.RUNNING):
            return job["state"]
        if status == ExecStatus.SUCCEEDED:
            summary = self._exec.read_summary(ref)
            self._repos.data_jobs.set_artifact(job["job_id"], artifact_uri=None,
                                               result_summary=summary)
            self._finalize(job, DataJobState.SUCCEEDED, summary=summary)
            return "Succeeded"
        target = (DataJobState.TIMED_OUT if status == ExecStatus.TIMED_OUT
                  else DataJobState.FAILED)
        self._finalize(job, target, reason_code="execution_failed")
        return target.value

    def _submit_preview(self, job):
        # Task 7에서 구현 — placeholder가 아니라 Task 7이 채운다
        raise NotImplementedError("preview path implemented in Task 7")
```

(주의: `_submit_preview`는 Task 7이 완성한다. Task 6의 테스트는 scan만 다루므로 `_submit_preview`가 호출되지 않는다. Task 7이 이 메서드를 실제 구현으로 교체.)

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_stepper_scan.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/stepper.py tests/test_stepper_scan.py
git commit -m "feat: JobStepper scan 경로 (preflight→execution→종결, 비블로킹 스텝)"
```

---

### Task 7: JobStepper — sync/rm preview·confirm·execution 경로 (`stepper.py` 2/2)

**Files:**
- Modify: `src/dms/stepper.py`
- Test: `tests/test_stepper_sync.py`

**Interfaces:**
- Consumes: Task 6의 JobStepper, `iso_plus`, `hashlib`.
- Produces:
  - `_summary_fingerprint(summary: dict) -> str | None` (모듈 함수) — 빈 dict/None이면 None, 아니면 `"sha256:" + sha256(sorted-json)`.
  - `_submit_preview(job)` 실제 구현 — preview JobSpec(dryrun=True, phase="preview") submit → `set_phase_ref(preview)` + `set_job_state(PreviewRunning)`. 반환 "PreviewRunning". submit 실패 → `Failed(preview_submit_failed)`.
  - `_step_one`에 상태 분기 추가: **PreviewRunning** → `_poll_preview(job)`, **Executing** → `_poll_or_submit_execution(job)`.
  - `_poll_preview(job)` — poll preview ref. Running → 불변. Succeeded → `read_summary` → 지문 계산. 지문 None → `Rejected(empty_preview)`. 지문 있으면 `set_preview(fingerprint, expires_at=iso_plus(now, preview_ttl), artifact_uri)` + `set_job_state(ConfirmPending)`. Failed → `Failed(preview_failed)`.
  - confirm 후 잡은 API가 `Executing`으로 전이(Task 8). **Executing** 상태의 스텝: phase_refs에 execution ref가 **없으면** = confirm 직후 → preflight 재검증(3b stub: 재검증도 어댑터 preflight submit+즉시 판단은 복잡하므로, **간소화**: execution을 바로 submit. 재검증 로직은 3c에서 실 preflight로). execution ref가 **있으면** poll(Task 6의 `_poll_execution`과 동일 로직 재사용). 즉:
    - `_poll_or_submit_execution(job)`: phase_refs["execution"] 없으면 `_submit_execution(job, DataJobState.EXECUTING)` (Executing 유지), 있으면 `_poll_execution(job)`.
  - **주의**: Task 6의 `_poll_execution`은 `job["state"]`를 그대로 반환하므로 Executing에서도 재사용 가능(Running/Executing 둘 다 처리). scan은 Running, sync/rm은 Executing으로 도달 — `_poll_execution`이 state-agnostic이어야 한다(이미 그렇게 작성됨).

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_stepper_sync.py
from dms.domain import DataJobState, RequestState
from dms.execution import ExecStatus, StubExecutionAdapter
from dms.repositories import Repositories
from dms.stepper import JobStepper, _summary_fingerprint


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()


def _sync_job(repos):
    rid = repos.requests.create(operation="sync", requester_id="alice", actor="alice",
        resource_key="k", payload={"source_storage": "src", "source": "a",
        "destination_storage": "dst", "destination": "b"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid",
        source_storage="src", source="a", destination_storage="dst", destination="b",
        options={}, tool="dsync",
        worker_pool={"tool": "dsync", "identity": {"uid": 10001, "gid": 10000,
            "username": "alice", "groups": [], "privileged": False},
            "candidates": {"primary": ["n1"]}, "process_count": 8,
            "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def _stepper(repos, adapter):
    return JobStepper(repos, adapter, settings=_Settings())


def test_summary_fingerprint():
    assert _summary_fingerprint({}) is None
    assert _summary_fingerprint(None) is None
    fp = _summary_fingerprint({"files": 3, "bytes": 9})
    assert fp.startswith("sha256:") and len(fp) == 71  # "sha256:" + 64


def test_sync_reaches_confirm_pending_with_fingerprint(db):
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    adapter.set_summary(f"stub-preview-{jid}", {"files": 3, "bytes": 9})
    stepper = _stepper(repos, adapter)
    stepper.run_once()  # Pending → Preflight
    stepper.run_once()  # Preflight succeeded → PreviewRunning (preview submit, dryrun)
    assert repos.data_jobs.get_job(jid)["state"] == "PreviewRunning"
    preview_spec = [s for s in adapter.submitted_specs() if s.phase == "preview"][0]
    assert preview_spec.dryrun is True
    stepper.run_once()  # PreviewRunning succeeded → ConfirmPending
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "ConfirmPending"
    assert job["preview_fingerprint"].startswith("sha256:")
    assert job["preview_expires_at"] is not None
    # ConfirmPending은 스텝퍼가 더 안 건드림
    stepper.run_once()
    assert repos.data_jobs.get_job(jid)["state"] == "ConfirmPending"


def test_empty_preview_rejects(db):
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    adapter.set_summary(f"stub-preview-{jid}", {})  # 빈 summary → 지문 없음
    stepper = _stepper(repos, adapter)
    stepper.run_once(); stepper.run_once(); stepper.run_once()
    assert repos.data_jobs.get_job(jid)["state"] == "Rejected"
    assert repos.data_jobs.get_job(jid)["reason_code"] == "empty_preview"


def test_confirmed_job_executes(db):
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    adapter.set_summary(f"stub-preview-{jid}", {"files": 3})
    stepper = _stepper(repos, adapter)
    stepper.run_once(); stepper.run_once(); stepper.run_once()  # → ConfirmPending
    # confirm을 흉내: 상태를 Executing으로, confirmed_fingerprint 저장
    fp = repos.data_jobs.get_job(jid)["preview_fingerprint"]
    repos.data_jobs.set_confirmed(jid, fp)
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    stepper.run_once()  # Executing, exec ref 없음 → execution submit
    assert repos.data_jobs.get_job(jid)["state"] == "Executing"
    assert [s for s in adapter.submitted_specs() if s.phase == "execution"]
    stepper.run_once()  # Executing poll Succeeded → Succeeded
    assert repos.data_jobs.get_job(jid)["state"] == "Succeeded"
    assert repos.requests.get(rid)["state"] == "Succeeded"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_stepper_sync.py -v`
Expected: FAIL — ImportError(`_summary_fingerprint`) / NotImplementedError

- [ ] **Step 3: 구현 (stepper.py 수정)**

```python
import hashlib
import json  # 상단 import에 추가
from .db import iso_plus, utc_now_iso  # 추가


def _summary_fingerprint(summary):
    if not summary:
        return None
    payload = json.dumps(summary, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
```

`_step_one`에 분기 추가 (Running 분기 뒤):
```python
        if state == DataJobState.PREVIEW_RUNNING.value:
            return self._poll_preview(job)
        if state == DataJobState.EXECUTING.value:
            return self._poll_or_submit_execution(job)
```

`_submit_preview` 교체(NotImplementedError 제거):
```python
    def _submit_preview(self, job):
        jid = job["job_id"]
        try:
            ref = self._exec.submit(self._build_spec(job, "preview", dryrun=True))
        except ExecutionError as exc:
            self._finalize(job, DataJobState.FAILED,
                           reason_code=f"preview_submit_failed:{exc.reason_code}")
            return "Failed"
        self._repos.data_jobs.set_phase_ref(jid, "preview", ref)
        self._repos.data_jobs.set_job_state(jid, DataJobState.PREVIEW_RUNNING,
                                            actor="stepper")
        return "PreviewRunning"
```

메서드 추가:
```python
    def _poll_preview(self, job):
        jid = job["job_id"]
        ref = (job["phase_refs"] or {}).get("preview")
        status = self._exec.poll(ref)
        if status in (ExecStatus.PENDING, ExecStatus.RUNNING):
            return "PreviewRunning"
        if status == ExecStatus.SUCCEEDED:
            summary = self._exec.read_summary(ref)
            fingerprint = _summary_fingerprint(summary)
            if fingerprint is None:
                self._finalize(job, DataJobState.REJECTED, reason_code="empty_preview")
                return "Rejected"
            expires = iso_plus(utc_now_iso(), self._settings.preview_ttl_seconds)
            artifact = f"{self._settings.artifact_base_uri}/{jid}"
            self._repos.data_jobs.set_preview(jid, fingerprint=fingerprint,
                                              expires_at=expires, artifact_uri=artifact)
            self._repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING,
                                                actor="stepper")
            return "ConfirmPending"
        self._finalize(job, DataJobState.FAILED, reason_code="preview_failed")
        return "Failed"

    def _poll_or_submit_execution(self, job):
        refs = job["phase_refs"] or {}
        if "execution" not in refs:
            return self._submit_execution(job, DataJobState.EXECUTING)
        return self._poll_execution(job)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_stepper_sync.py tests/test_stepper_scan.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/stepper.py tests/test_stepper_sync.py
git commit -m "feat: JobStepper sync/rm 경로 (preview 지문, confirm 대기, execution)"
```

---

### Task 8: 잡 상세 + confirm + cancel API (`api/routes_jobs.py`)

**Files:**
- Create: `src/dms/api/routes_jobs.py`
- Modify: `src/dms/api/app.py` (라우터 마운트 + `app.state.execution_adapter`)
- Test: `tests/test_api_jobs.py`

**Interfaces:**
- Consumes: `require_user`, `repos.requests`, `repos.data_jobs`, `app.state.execution_adapter`(cancel이 terminate 호출), `DataJobState`, `TERMINAL_DATA_JOB_STATES`.
- Produces (요청자 격리 — user는 자기 요청의 잡만, admin은 전체):
  - `GET /api/user/requests/{request_id}/jobs` → 그 요청의 잡 목록. 남의 요청이면 404. 각 잡에 state/tool/reason_code/preview_fingerprint/artifact_uri/result_summary/transitions 포함.
  - `POST /api/user/jobs/{job_id}:confirm` body `{fingerprint}` → 잡이 `ConfirmPending`이어야(아니면 409 `not_confirmable`). 요청자 일치(아니면 404). preview_expires_at 만료 검사(만료면 잡을 `PreviewExpired`로, 409 `preview_expired`). fingerprint가 preview_fingerprint와 불일치 → 409 `fingerprint_mismatch`. preview_fingerprint가 없으면(빈 preview) 409 `no_preview_fingerprint`. 통과 → `set_confirmed` + `set_job_state(Executing)` → 200 `{state: "Executing"}`.
  - `POST /api/user/jobs/{job_id}:cancel` → 잡이 비터미널이어야(터미널이면 409 `already_terminal`). 요청자 일치(아니면 404). **실행 중지 먼저**: phase_refs의 모든 ref에 `execution_adapter.terminate(ref)` 호출 — 하나라도 `ExecutionError`면 500 `cancel_failed`(DB 안 바꿈, 거짓 취소 금지). 전부 성공 → `set_job_state(Cancelled)` + `finalize_from_job(Cancelled)` → 200 `{state: "Cancelled"}`.
- `app.state.execution_adapter = StubExecutionAdapter()` 기본(3c에서 live로 교체). controller와 API가 각자 어댑터 인스턴스를 갖는다(공유 상태 없음 — stub은 in-memory라 cancel의 terminate가 controller stub에 반영 안 되지만, 실 환경에선 Volcano가 공유 상태. 3b 테스트는 API와 stepper가 같은 adapter를 공유하도록 주입).

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_api_jobs.py
from dms.domain import DataJobState, RequestState
from dms.execution import StubExecutionAdapter


def _login(client, name="alice"):
    client.post("/api/auth/signup", json={"username": name, "password": "p"})
    client.post("/api/auth/login", json={"username": name, "password": "p"})


def _confirmpending_job(app_repos, requester="alice"):
    repos = app_repos
    rid = repos.requests.create(operation="sync", requester_id=requester, actor=requester,
        resource_key="k", payload={"source_storage": "src", "source": "a",
        "destination_storage": "dst", "destination": "b"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid",
        source_storage="src", source="a", destination_storage="dst", destination="b",
        options={}, tool="dsync", worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_preview(jid, fingerprint="sha256:abc",
        expires_at="2099-01-01T00:00:00Z", artifact_uri="file:///art/j")
    repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING, actor="stepper")
    return rid, jid


def test_list_jobs_isolation(client):
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos, requester="alice")
    _login(client, "eve")
    assert client.get(f"/api/user/requests/{rid}/jobs").status_code == 404
    client.post("/api/auth/logout")
    _login(client, "alice")
    jobs = client.get(f"/api/user/requests/{rid}/jobs").json()
    assert jobs[0]["job_id"] == jid and jobs[0]["state"] == "ConfirmPending"


def test_confirm_happy_and_mismatch(client):
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    _login(client, "alice")
    assert client.post(f"/api/user/jobs/{jid}:confirm",
                       json={"fingerprint": "sha256:wrong"}).status_code == 409
    r = client.post(f"/api/user/jobs/{jid}:confirm", json={"fingerprint": "sha256:abc"})
    assert r.status_code == 200 and r.json()["state"] == "Executing"
    assert repos.data_jobs.get_job(jid)["confirmed_fingerprint"] == "sha256:abc"


def test_confirm_not_confirmpending_409(client):
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    _login(client, "alice")
    assert client.post(f"/api/user/jobs/{jid}:confirm",
                       json={"fingerprint": "sha256:abc"}).status_code == 409


def test_cancel_terminates_then_records(client):
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    repos.data_jobs.set_phase_ref(jid, "execution", "ref-ex")
    adapter = client.app.state.execution_adapter
    _login(client, "alice")
    r = client.post(f"/api/user/jobs/{jid}:cancel")
    assert r.status_code == 200 and r.json()["state"] == "Cancelled"
    assert repos.requests.get(rid)["state"] == "Cancelled"


def test_cancel_terminated_ref_reports_failure_not_false_cancel(client):
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    repos.data_jobs.set_phase_ref(jid, "execution", "ref-boom")
    client.app.state.execution_adapter.fail_terminate("ref-boom")
    _login(client, "alice")
    r = client.post(f"/api/user/jobs/{jid}:cancel")
    assert r.status_code == 500 and r.json()["detail"] == "cancel_failed"
    # 거짓 취소 금지 — 상태 그대로
    assert repos.data_jobs.get_job(jid)["state"] == "Executing"
```

주의: `client` 픽스처가 `app.state.execution_adapter`에 `StubExecutionAdapter`를 노출해야 한다. conftest의 client 픽스처는 create_app을 쓰므로 create_app이 기본 stub을 세팅하면 된다. `client.app`으로 접근 — TestClient는 `.app` 속성을 제공한다.

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_api_jobs.py -v`
Expected: FAIL (404)

- [ ] **Step 3: 구현**

```python
# src/dms/api/routes_jobs.py
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DataJobState, TERMINAL_DATA_JOB_STATES
from ..db import utc_now_iso
from ..execution import ExecutionError
from .auth import Identity, require_user

router = APIRouter()


class ConfirmBody(BaseModel):
    fingerprint: str


def _owned_request(request, request_id, identity):
    req = request.app.state.repos.requests.get(request_id)
    if req is None or (identity.role != "admin"
                       and req["requester_id"] != identity.actor):
        raise HTTPException(status_code=404, detail="request_not_found")
    return req


def _owned_job(request, job_id, identity):
    repos = request.app.state.repos
    job = repos.data_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    req = repos.requests.get(job["request_id"])
    if req is None or (identity.role != "admin"
                       and req["requester_id"] != identity.actor):
        raise HTTPException(status_code=404, detail="job_not_found")
    return job


@router.get("/api/user/requests/{request_id}/jobs")
def list_jobs(request_id: str, request: Request,
              identity: Identity = Depends(require_user)):
    _owned_request(request, request_id, identity)
    repos = request.app.state.repos
    jobs = repos.data_jobs.list_jobs(request_id=request_id)
    for job in jobs:
        job["transitions"] = repos.data_jobs.job_transitions(job["job_id"])
    return jobs


@router.post("/api/user/jobs/{job_id}:confirm")
def confirm_job(job_id: str, body: ConfirmBody, request: Request,
                identity: Identity = Depends(require_user)):
    repos = request.app.state.repos
    job = _owned_job(request, job_id, identity)
    if job["state"] != DataJobState.CONFIRM_PENDING.value:
        raise HTTPException(status_code=409, detail="not_confirmable")
    if not job["preview_fingerprint"]:
        raise HTTPException(status_code=409, detail="no_preview_fingerprint")
    if job["preview_expires_at"] and job["preview_expires_at"] < utc_now_iso():
        repos.data_jobs.set_job_state(job_id, DataJobState.PREVIEW_EXPIRED,
                                      reason_code="preview_expired", actor=identity.actor)
        repos.requests.finalize_from_job(job["request_id"],
                                         DataJobState.PREVIEW_EXPIRED,
                                         reason_code="preview_expired",
                                         actor=identity.actor)
        raise HTTPException(status_code=409, detail="preview_expired")
    if body.fingerprint != job["preview_fingerprint"]:
        raise HTTPException(status_code=409, detail="fingerprint_mismatch")
    repos.data_jobs.set_confirmed(job_id, body.fingerprint)
    repos.data_jobs.set_job_state(job_id, DataJobState.EXECUTING, actor=identity.actor)
    return {"state": "Executing"}


@router.post("/api/user/jobs/{job_id}:cancel")
def cancel_job(job_id: str, request: Request,
               identity: Identity = Depends(require_user)):
    repos = request.app.state.repos
    job = _owned_job(request, job_id, identity)
    if DataJobState(job["state"]) in TERMINAL_DATA_JOB_STATES:
        raise HTTPException(status_code=409, detail="already_terminal")
    adapter = request.app.state.execution_adapter
    refs = [r for r in (job["phase_refs"] or {}).values() if r]
    try:
        for ref in refs:
            adapter.terminate(ref)
    except ExecutionError:
        raise HTTPException(status_code=500, detail="cancel_failed")
    repos.data_jobs.set_job_state(job_id, DataJobState.CANCELLED,
                                  reason_code="cancelled_by_user", actor=identity.actor)
    repos.requests.finalize_from_job(job["request_id"], DataJobState.CANCELLED,
                                     reason_code="cancelled_by_user", actor=identity.actor)
    return {"state": "Cancelled"}
```

주의: `utc_now_iso_safe` import는 존재하지 않으니 제거하고 `from ..db import utc_now_iso`만 쓴다. (구현자는 위 import 줄에서 `utc_now_iso_safe`를 지울 것 — 실수 방지 표기.)

`app.py`: `from .execution import StubExecutionAdapter` + `from .routes_jobs import router as jobs_router`. create_app에서 `app.state.execution_adapter = StubExecutionAdapter()` (repos 설정 근처) + `app.include_router(jobs_router)`.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_api_jobs.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/api/ tests/test_api_jobs.py
git commit -m "feat: 잡 상세/confirm/cancel API (지문 게이트, 취소 시 실행 중지 우선)"
```

---

### Task 9: 요청 제출 특권 게이트 (`routes_requests.py`) — SECURITY

**Files:**
- Modify: `src/dms/api/routes_requests.py`
- Test: `tests/test_api_requests_privileged.py`

**Interfaces:**
- Consumes: 기존 submit 라우트, `require_user`, `settings.allow_privileged_requesters`/`privileged_requesters`.
- Produces: 요청 제출 시 특권 인가 게이트 (스펙 §5, Phase 3a 이월 BLOCKER 해소):
  - `owner_username`이 요청자(`identity.actor`)와 **다른** 경우(= 남의 신원으로 실행 요청 = 특권 의도)에만 인가 검사. 조건: `identity.role == "admin"` **AND** `settings.allow_privileged_requesters` **AND** `identity.actor in settings.privileged_requesters`. 하나라도 불만족 → **403 `privileged_not_authorized`**.
  - `owner_username`이 없거나 요청자 자신과 같으면(자기 데이터) 게이트 통과 (기존 동작).
  - 이 게이트는 planner의 identity 해석(requester_id 기준 특권)과 이중 방어를 이룬다: API가 "이 요청자가 owner를 바꿀 자격이 있나"를 막고, planner가 "requester가 privileged allowlist인가"로 root를 합성.

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_api_requests_privileged.py
import pytest
from dms.config import Settings


def _client_with(db, **overrides):
    from fastapi.testclient import TestClient
    from dms.api.app import create_app
    base = {"DMS_DATABASE_URL": "unused", "DMS_SHARED_TOKEN": "tok-shared",
            "DMS_ADMIN_TOKEN": "tok-admin", "DMS_SESSION_SECRET": "sess", **overrides}
    settings = Settings.from_env(base)
    return TestClient(create_app(settings, db))


SCAN = {"operation": "scan", "storage": "s1", "target": "a"}


def test_owner_self_is_allowed(db):
    client = _client_with(db)
    client.post("/api/auth/signup", json={"username": "alice", "password": "p"})
    client.post("/api/auth/login", json={"username": "alice", "password": "p"})
    # owner_username 없음 → 자기 데이터 → 202
    assert client.post("/api/user/requests", json=SCAN).status_code == 202
    # owner_username == 자신 → 202
    assert client.post("/api/user/requests",
                       json={**SCAN, "owner_username": "alice"}).status_code == 202


def test_user_cannot_submit_for_other_owner(db):
    client = _client_with(db)
    client.post("/api/auth/signup", json={"username": "mallory", "password": "p"})
    client.post("/api/auth/login", json={"username": "mallory", "password": "p"})
    r = client.post("/api/user/requests", json={**SCAN, "owner_username": "victim"})
    assert r.status_code == 403 and r.json()["detail"] == "privileged_not_authorized"


def test_admin_operator_with_flag_can_submit_for_other(db):
    client = _client_with(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="true",
                          DMS_PRIVILEGED_REQUESTERS="ops")
    # 관리자 계정 생성(운영 토큰) + 로그인
    client.post("/api/admin/accounts", json={"username": "ops", "password": "p"},
                headers={"x-admin-token": "tok-admin"})
    client.post("/api/auth/login", json={"username": "ops", "password": "p"})
    r = client.post("/api/user/requests", json={**SCAN, "owner_username": "victim"})
    assert r.status_code == 202


def test_admin_not_in_allowlist_denied(db):
    client = _client_with(db, DMS_ALLOW_PRIVILEGED_REQUESTERS="true",
                          DMS_PRIVILEGED_REQUESTERS="someone-else")
    client.post("/api/admin/accounts", json={"username": "ops", "password": "p"},
                headers={"x-admin-token": "tok-admin"})
    client.post("/api/auth/login", json={"username": "ops", "password": "p"})
    r = client.post("/api/user/requests", json={**SCAN, "owner_username": "victim"})
    assert r.status_code == 403
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_api_requests_privileged.py -v`
Expected: FAIL — 특권 요청이 403 대신 202

- [ ] **Step 3: 구현 (routes_requests.py의 submit 핸들러 수정)**

submit 핸들러에서, `_validated_payload` 호출 전/후 상관없이 요청 생성 **전에** 게이트 추가. body.owner_username과 identity를 비교:
```python
    # 특권 게이트 (스펙 §5): owner_username이 요청자와 다르면 특권 의도 → 인가 필요
    owner = body.owner_username
    if owner is not None and owner != identity.actor:
        settings = request.app.state.settings
        authorized = (identity.role == "admin"
                      and settings.allow_privileged_requesters
                      and identity.actor in settings.privileged_requesters)
        if not authorized:
            raise HTTPException(status_code=403, detail="privileged_not_authorized")
```
(submit 핸들러 시그니처에 `request: Request`가 이미 있고 `identity`도 있으니 그대로 사용. `HTTPException`은 이미 import됨.)

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_api_requests_privileged.py tests/test_api_requests.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/api/routes_requests.py tests/test_api_requests_privileged.py
git commit -m "fix(security): 요청 제출 특권 게이트 (owner_username != 요청자면 admin+allowlist 인가)"
```

---

### Task 10: controller 배선 (job-stepper 루프 + preview 만료)

**Files:**
- Modify: `src/dms/controller.py`
- Modify: `src/dms/api/app.py` (이미 Task 8에서 execution_adapter 세팅됨 — 확인만)
- Test: `tests/test_controller_stepper.py`

**Interfaces:**
- Consumes: `JobStepper`(stepper), `StubExecutionAdapter`(3b 기본), `Settings`.
- Produces:
  - `build_loops(settings, repos, *, identity_resolver=None, execution_adapter=None)` — execution_adapter 기본은 `StubExecutionAdapter()`(controller 자체 인스턴스). planner 루프 **뒤, storage-reconciler 앞**에 job-stepper 루프 추가. Loop `job-stepper`(settings.stepper_interval_seconds), fn = `_stepper_step`:
    - `_stepper_step()`: `JobStepper(repos, execution_adapter, settings=settings).run_once()` + `repos.data_jobs.expire_previews(now_iso=utc_now_iso())` 둘 다 수행(preview 만료 스윕 포함).
  - `run_forever(settings, repos, holder, *, sleep, identity_resolver=None, execution_adapter=None)` 도 전달.
  - cli.py의 controller 분기는 그대로(execution_adapter 기본 stub). 3c에서 live Volcano 어댑터로 교체.
  - 최종 loop 순서: planner, job-stepper, storage-reconciler, retention.

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_controller_stepper.py
from dms.controller import build_loops, run_all_once
from dms.execution import StubExecutionAdapter
from dms.domain import RequestState
from dms.repositories import Repositories


class _Settings:
    agent_report_stale_seconds = 300
    reconcile_interval_seconds = 30
    retention_interval_seconds = 3600
    planner_interval_seconds = 10
    stepper_interval_seconds = 5
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    agent_report_retention_days = 30
    allow_privileged_requesters = False
    privileged_requesters = frozenset()


def test_stepper_loop_registered_second(db):
    loops = build_loops(_Settings(), Repositories(db))
    assert [l.name for l in loops] == [
        "planner", "job-stepper", "storage-reconciler", "retention"]
    assert loops[1].interval_seconds == 5


def test_stepper_loop_advances_pending_job(db):
    repos = Repositories(db)
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k", payload={"storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s1", target="a", options={}, tool="dscan",
        worker_pool={"identity": {"uid": 1}, "candidates": {"primary": ["n1"]},
                     "process_count": 8, "queue": "dms-data",
                     "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    loops = build_loops(_Settings(), repos, execution_adapter=StubExecutionAdapter())
    run_all_once(loops, repos, holder="h1")  # job-stepper 루프가 Pending → Preflight
    assert repos.data_jobs.get_job(jid)["state"] == "Preflight"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_controller_stepper.py -v`
Expected: FAIL (stepper 루프 없음 / execution_adapter 인자 없음)

- [ ] **Step 3: 구현**

`controller.py` 수정:
```python
from .stepper import JobStepper  # 상단
from .execution import StubExecutionAdapter
from .db import utc_now_iso


def build_loops(settings, repos, *, identity_resolver=None, execution_adapter=None):
    adapter = execution_adapter if execution_adapter is not None else StubExecutionAdapter()

    def _stepper_step():
        JobStepper(repos, adapter, settings=settings).run_once()
        repos.data_jobs.expire_previews(now_iso=utc_now_iso())

    return [
        Loop("planner", settings.planner_interval_seconds,
             lambda: Planner(repos, identity_resolver, settings=settings).run_once()),
        Loop("job-stepper", settings.stepper_interval_seconds, _stepper_step),
        Loop("storage-reconciler", settings.reconcile_interval_seconds,
             lambda: reconcile_storages_once(
                 repos, stale_seconds=settings.agent_report_stale_seconds)),
        Loop("retention", settings.retention_interval_seconds,
             lambda: prune_agent_reports_once(
                 repos, retention_days=settings.agent_report_retention_days)),
    ]


def run_forever(settings, repos, holder, *, sleep=time.sleep, identity_resolver=None,
                execution_adapter=None):
    loops = build_loops(settings, repos, identity_resolver=identity_resolver,
                        execution_adapter=execution_adapter)
    # ...기존 next_due 로직 그대로...
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_controller_stepper.py tests/test_controller.py tests/test_controller_planner.py tests/test_cli.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/controller.py tests/test_controller_stepper.py
git commit -m "feat: controller에 job-stepper 루프 + preview 만료 스윕 배선"
```

---

## Phase 3b 완료 기준

- `.venv/bin/pytest -q` 전체 통과 (서비스 없이, 0 warnings).
- 전 라이프사이클이 stub 실행 어댑터로 SQLite만으로 검증됨: scan(preflight→execution→Succeeded), sync/rm(preflight→preview→ConfirmPending→confirm API→Executing→Succeeded), 빈 preview 거부, 지문 불일치 409, cancel 시 terminate 우선(실패 시 거짓 취소 금지), preview 만료.
- **SECURITY**: 요청 제출 특권 게이트가 owner_username 위조를 막음(Phase 3a 이월 BLOCKER 해소) + planner의 requester 기준 특권 해석과 이중 방어.
- **Phase 3c로 이어짐**: live `ExecutionAdapter`(kubernetes Python 클라이언트로 Volcano native Job 제출/폴링, launcher/worker gang, nsync role 분리, artifact 읽기, terminate) + live `IdentityResolver`(ldap3) + `dms-job-runner`(잡 이미지 내 Python: hostfile/SSH 배리어/identity 물질화/mpirun/summary). **테스트베드 실증**은 여기서 처음 가능.
