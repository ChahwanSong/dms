# DMS 슬라이스 7 (취소·타임아웃 정합성) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 취소가 거짓말하지 않고(실제로 클러스터 작업을 종료한 뒤에만 DB를 Cancelled로 기록), Pending 요청도 취소할 수 있으며, phase별 타임아웃이 실제로 집행되어 멈춘 잡이 무기한 Running으로 남지 않는다.

**Architecture:** 실행 경로를 고치는 슬라이스다. 순서가 중요하다 — 먼저 **터미널 가드**를 깔아 취소가 되돌려지지 않게 만들고(Task 1), 종료 로직을 **공용 헬퍼**로 뽑아(Task 2) 배치 취소(Task 3)와 요청 취소(Task 4)가 같은 규칙을 쓰게 한다. 타임아웃(Task 5)은 정책 값을 `JobSpec` → 매니페스트 `activeDeadlineSeconds` → `poll()`의 TIMED_OUT 매핑으로 잇는다.

**Tech Stack:** Python 3.11 / FastAPI / pytest · React 18 + Vite 5 + TS + TanStack Query v5 + Vitest · MSW 2

## Global Constraints

- 설계 문서 `docs/superpowers/specs/2026-08-05-dms-cancel-timeout-slice7-design.md`가 상위 규칙이다. 충돌 시 `2026-08-02-dms-clean-slate-design.md`가 이긴다.
- **거짓 취소 금지**(상위 스펙 §5): Volcano 잡/Pod **종료가 성공한 뒤에만** DB를 Cancelled로 기록한다. 종료가 실패하면 `500 cancel_failed`를 반환하고 **DB는 그대로 둔다**.
- 취소는 **요청을 종결하기 전에 잡을 먼저 조회·종료**한다(planner 경쟁 창을 좁힌다).
- 터미널 가드는 **조용한 멱등 무시**다 — 예외를 던지지 않고, 전이 기록도 남기지 않는다.
- 타임아웃 판정이 불확실하면 `FAILED`로 유지한다(오분류보다 보수적).
- 배치 item의 `Cancelled`는 성공도 실패도 아니다 — **카운터를 올리지 않는다**.
- 기존 `routes_jobs.cancel_job`의 외부 동작(상태 코드·reason 코드)은 바뀌지 않아야 한다.
- 백엔드 테스트는 `.venv/bin/python -m pytest`(plain `python3`는 이 환경에서 깨져 있다). 프론트는 `frontend/`에서 `npm test`, `npx tsc -b`.
- 커밋은 태스크 단위, 각 태스크는 테스트 GREEN 상태로 끝난다.

---

### Task 1: 터미널 가드

**Files:**
- Modify: `src/dms/repositories/data_jobs.py` (`set_job_state`)
- Test: `tests/test_data_jobs_terminal_guard.py` (신규)

**Interfaces:**
- Consumes: `TERMINAL_DATA_JOB_STATES` (`src/dms/domain.py`)
- Produces: 종단 잡에 대한 `set_job_state` 호출이 무해한 no-op. Task 3·4가 이 성질에 의존한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_data_jobs_terminal_guard.py`. 잡을 만드는 방법은 기존 `tests/test_repo_data_jobs.py`(또는 data_jobs 리포지토리를 쓰는 아무 테스트)를 열어 그 방식을 그대로 쓴다.

```python
from dms.domain import DataJobState


def test_terminal_job_state_is_not_overwritten(db):
    # ... 잡을 만들고 Cancelled 로 만든다 ...
    repos.data_jobs.set_job_state(jid, DataJobState.CANCELLED,
                                  reason_code="cancelled_by_user", actor="user")
    before = repos.data_jobs.job_transitions(jid)

    # 늦게 도착한 stepper 틱
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="stepper")

    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Cancelled"
    assert job["reason_code"] == "cancelled_by_user"
    # 일어나지 않은 전이는 기록하지 않는다
    assert repos.data_jobs.job_transitions(jid) == before


def test_non_terminal_transitions_still_work(db):
    # Pending -> Preflight 는 정상 동작하고 전이가 하나 늘어난다
    ...


def test_guard_applies_to_every_terminal_state(db):
    # Succeeded/Failed/TimedOut/Cancelled/Rejected/PreviewExpired 각각에 대해
    # 이후 전이 시도가 무시되는지 (TERMINAL_DATA_JOB_STATES 를 순회)
    ...
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_data_jobs_terminal_guard.py -v`
Expected: FAIL — 상태가 `Executing`으로 덮어써지고 전이가 하나 늘어난다

- [ ] **Step 3: 가드를 넣는다**

`src/dms/repositories/data_jobs.py`의 `set_job_state`에서, 현재 상태를 읽은 직후:

```python
            if DataJobState(current["state"]) in TERMINAL_DATA_JOB_STATES:
                # 종단 잡은 되돌리지 않는다. 취소 직후 늦게 도착한 stepper 틱이
                # Cancelled 를 덮어쓰고 고아 Volcano Job 을 만드는 경쟁을 막는다.
                # 예외 대신 조용히 무시한다 — 취소는 정상 동작이고 stepper 루프를
                # 한 잡 때문에 죽여선 안 된다. 일어나지 않은 전이는 기록도 안 한다.
                return
```

`TERMINAL_DATA_JOB_STATES`가 이 모듈에 import돼 있는지 확인하고 없으면 기존 import 줄에 추가한다.

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `.venv/bin/python -m pytest tests/test_data_jobs_terminal_guard.py -v` → PASS
Run: `.venv/bin/python -m pytest tests/ -q` → 전부 통과(기준선 447 + 신규). **여기서 깨지는 기존 테스트가 있다면 그 테스트가 "종단 잡을 다시 전이시키는" 동작에 의존했다는 뜻이다** — 테스트를 약화시키지 말고, 그 전제가 옳은지 판단해 보고서에 적고 전제를 명시적으로 바꾼다.

- [ ] **Step 5: 커밋**

```bash
git add src/dms/repositories/data_jobs.py tests/test_data_jobs_terminal_guard.py
git commit -m "fix(data-jobs): never transition out of a terminal job state"
```

---

### Task 2: 공용 종료 헬퍼

**Files:**
- Create: `src/dms/api/cancel.py`
- Modify: `src/dms/api/routes_jobs.py` (`cancel_job`이 헬퍼를 쓰도록)
- Test: `tests/test_api_cancel_helper.py` (신규)

**Interfaces:**
- Consumes: `ExecutionAdapter.terminate(ref)`, `job["phase_refs"]`, `TERMINAL_DATA_JOB_STATES`
- Produces: `terminate_job(adapter, job) -> None` — 비종단 잡의 모든 `phase_refs` ref를 terminate한다. 종단이면 아무 것도 하지 않는다. 실패 시 `ExecutionError`를 전파한다. Task 3·4가 쓴다.

- [ ] **Step 1: 헬퍼 테스트를 쓴다**

`tests/test_api_cancel_helper.py`:

```python
import pytest
from dms.api.cancel import terminate_job
from dms.execution import ExecutionError


class _Adapter:
    def __init__(self, fail_on=None):
        self.terminated = []
        self._fail_on = fail_on

    def terminate(self, ref):
        if ref == self._fail_on:
            raise ExecutionError("terminate_failed", ref)
        self.terminated.append(ref)


def test_terminates_every_phase_ref():
    job = {"state": "Executing",
           "phase_refs": {"preflight": "pod/a", "execution": "vcjob/b"}}
    a = _Adapter()
    terminate_job(a, job)
    assert sorted(a.terminated) == ["pod/a", "vcjob/b"]


def test_terminal_job_is_untouched():
    a = _Adapter()
    terminate_job(a, {"state": "Succeeded", "phase_refs": {"execution": "vcjob/b"}})
    assert a.terminated == []


def test_missing_or_empty_refs_are_skipped():
    a = _Adapter()
    terminate_job(a, {"state": "Executing", "phase_refs": None})
    terminate_job(a, {"state": "Executing", "phase_refs": {"execution": None}})
    assert a.terminated == []


def test_failure_propagates():
    a = _Adapter(fail_on="vcjob/b")
    with pytest.raises(ExecutionError):
        terminate_job(a, {"state": "Executing", "phase_refs": {"execution": "vcjob/b"}})
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_cancel_helper.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dms.api.cancel'`

- [ ] **Step 3: 헬퍼를 구현한다**

`src/dms/api/cancel.py` (신규):

```python
"""취소의 공통 규칙. 상위 스펙 §5: Volcano 잡 종료가 성공한 뒤에만 DB 를 Cancelled 로
기록한다 — 거짓 취소 금지. 그래서 종료는 DB 변경과 분리된 이 헬퍼가 담당하고, 호출자는
여기서 예외가 나오면 DB 를 건드리지 않고 실패를 보고한다."""
from ..domain import DataJobState, TERMINAL_DATA_JOB_STATES


def terminate_job(adapter, job) -> None:
    if DataJobState(job["state"]) in TERMINAL_DATA_JOB_STATES:
        return
    for ref in (job.get("phase_refs") or {}).values():
        if ref:
            adapter.terminate(ref)
```

- [ ] **Step 4: `cancel_job`이 헬퍼를 쓰게 한다**

`src/dms/api/routes_jobs.py`의 `cancel_job`에서 ref를 직접 순회하며 terminate하던 부분을
`terminate_job(adapter, job)` 호출로 바꾼다. **`already_terminal` 409 가드와 `cancel_failed`
500 매핑, 그 뒤의 DB 종결 로직은 그대로 둔다** — 외부 동작이 바뀌면 안 된다.

- [ ] **Step 5: 통과 + 회귀 확인**

Run: `.venv/bin/python -m pytest tests/test_api_cancel_helper.py -v` → PASS
Run: `.venv/bin/python -m pytest tests/ -q` → 전부 통과. 특히 `tests/test_api_jobs.py`의 cancel 테스트가 **손대지 않고** 통과해야 한다.

- [ ] **Step 6: 커밋**

```bash
git add src/dms/api/cancel.py src/dms/api/routes_jobs.py tests/test_api_cancel_helper.py
git commit -m "refactor(api): extract terminate_job cancel helper"
```

---

### Task 3: 배치 취소가 실제로 종료한다

**Files:**
- Modify: `src/dms/api/routes_batches.py` (`cancel_batch`)
- Test: `tests/test_api_batch_cancel.py` (신규)

**Interfaces:**
- Consumes: `terminate_job` (Task 2), `repos.batches.list_items`, `repos.data_jobs.list_jobs(request_id=)`, `repos.requests.finalize_from_job`, `request.app.state.execution_adapter`
- Produces: 진짜 취소. Task 6의 item 분기가 이것과 맞물린다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_batch_cancel.py`. 배치를 만들고 자식을 materialize하는 방법은 `tests/test_batch_orchestrator_scan.py`와 `tests/test_api_batches.py`를 열어 그 방식을 재사용한다(오케스트레이터 `run_once`를 한 번 돌려 materialize시키는 편이 실제에 가깝다).

검증할 것:

1. materialize된 자식이 있는 배치를 취소하면 어댑터의 `terminate`가 **각 phase ref로** 호출된다.
2. 자식 data_job이 `Cancelled`(reason `cancelled_by_batch`), 자식 request가 `Cancelled`가 된다.
3. 해당 item이 `Cancelled`가 된다.
4. 아직 Queued인 item도 `Cancelled`로 표시된다.
5. 배치가 `Cancelled`가 된다.
6. **`terminate`가 실패하면 `500 cancel_failed`이고, 배치·item·자식 잡·자식 요청의 상태가 호출 전과 동일하다**(거짓 취소 금지). 이 단언이 이 태스크의 핵심이다.
7. `Previewing`/`Running`이 아닌 배치는 여전히 `409 batch_not_cancelable`.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_batch_cancel.py -v`
Expected: FAIL — `terminate`가 호출되지 않고 자식 잡이 그대로다

- [ ] **Step 3: 구현한다**

`cancel_batch`를 다음 순서로 바꾼다. **종료를 전부 마친 뒤에 DB를 바꾼다**:

```python
    adapter = request.app.state.execution_adapter
    items = repo.list_items(batch_id)
    # 1) 먼저 종료한다. 하나라도 실패하면 DB 는 건드리지 않고 실패를 보고한다.
    child_jobs = []
    for it in items:
        rid = it.get("request_id")
        if not rid:
            continue
        for job in request.app.state.repos.data_jobs.list_jobs(request_id=rid):
            child_jobs.append((it, job))
    try:
        for _, job in child_jobs:
            terminate_job(adapter, job)
    except ExecutionError:
        raise HTTPException(status_code=500, detail="cancel_failed")
    # 2) 종료가 전부 성공한 뒤에만 기록한다.
    repos = request.app.state.repos
    for it, job in child_jobs:
        repos.data_jobs.set_job_state(job["job_id"], DataJobState.CANCELLED,
                                      reason_code="cancelled_by_batch",
                                      actor=identity.actor)
        repos.requests.finalize_from_job(job["request_id"], DataJobState.CANCELLED,
                                         reason_code="cancelled_by_batch",
                                         actor=identity.actor)
    for it in items:
        if it["status"] in ("Queued", "Materialized"):
            repo.set_item_status(batch_id, it["seq"], "Cancelled")
    repo.set_status(batch_id, "Cancelled")
    return {"status": "Cancelled"}
```

실제 필드명(`request_id` 컬럼이 item에 어떤 이름으로 있는지, `list_jobs`의 시그니처)은 리포지토리를 열어 확인하고 맞춘다. 필요한 import(`terminate_job`, `ExecutionError`, `DataJobState`)를 추가한다.

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `.venv/bin/python -m pytest tests/test_api_batch_cancel.py -v` → PASS
Run: `.venv/bin/python -m pytest tests/ -q` → 전부 통과

- [ ] **Step 5: 커밋**

```bash
git add src/dms/api/routes_batches.py tests/test_api_batch_cancel.py
git commit -m "fix(api): batch cancel terminates in-flight children before recording"
```

---

### Task 4: Pending 요청 취소

**Files:**
- Modify: `src/dms/api/routes_requests.py`
- Test: `tests/test_api_request_cancel.py` (신규)

**Interfaces:**
- Consumes: `terminate_job` (Task 2), `_owned_request` (`src/dms/api/routes_jobs.py`), `repos.data_jobs.list_jobs`, `repos.requests.set_state`
- Produces: `POST /api/user/requests/{request_id}:cancel`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_request_cancel.py`, 검증:

1. 잡이 없는 Pending 요청 → 200, 요청이 `Cancelled`, reason `cancelled_by_user`.
2. 비종단 잡이 있는 요청 → `terminate`가 호출되고 잡·요청 둘 다 `Cancelled`.
3. 이미 종단인 요청 → `409 already_terminal`, 상태 불변.
4. 타인 요청 → `404 request_not_found`(관리자는 200).
5. `terminate` 실패 → `500 cancel_failed`, 요청 상태 불변.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_request_cancel.py -v`
Expected: FAIL — 404/405 (라우트 없음)

- [ ] **Step 3: 구현한다**

`src/dms/api/routes_requests.py`에 추가한다. **잡을 먼저 종료하고, 그 다음 요청을 종결한다**:

```python
@router.post("/api/user/requests/{request_id}:cancel")
def cancel_request(request_id: str, request: Request,
                   identity: Identity = Depends(require_user)):
    repos = request.app.state.repos
    req = _owned_request(request, request_id, identity)
    if RequestState(req["state"]) in TERMINAL_REQUEST_STATES:
        raise HTTPException(status_code=409, detail="already_terminal")
    # planner 경쟁: 요청을 종결하기 전에 잡을 먼저 조회·종료해야 고아가 남지 않는다.
    jobs = repos.data_jobs.list_jobs(request_id=request_id)
    try:
        for job in jobs:
            terminate_job(request.app.state.execution_adapter, job)
    except ExecutionError:
        raise HTTPException(status_code=500, detail="cancel_failed")
    for job in jobs:
        repos.data_jobs.set_job_state(job["job_id"], DataJobState.CANCELLED,
                                      reason_code="cancelled_by_user",
                                      actor=identity.actor)
    repos.requests.set_state(request_id, RequestState.CANCELLED,
                             reason_code="cancelled_by_user", actor=identity.actor)
    return {"state": "Cancelled"}
```

`_owned_request`는 `routes_jobs.py`에 있으므로 import한다(순환 import가 생기면 `cancel.py`로 옮기고 양쪽에서 쓴다 — 실제로 돌려보고 판단할 것). 필요한 import(`RequestState`, `TERMINAL_REQUEST_STATES`, `DataJobState`, `terminate_job`, `ExecutionError`)를 추가한다. `set_state`의 실제 시그니처를 리포지토리에서 확인하고 맞춘다.

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `.venv/bin/python -m pytest tests/test_api_request_cancel.py -v` → PASS
Run: `.venv/bin/python -m pytest tests/ -q` → 전부 통과

- [ ] **Step 5: 커밋**

```bash
git add src/dms/api/routes_requests.py tests/test_api_request_cancel.py
git commit -m "feat(api): cancel a request, terminating any in-flight jobs first"
```

---

### Task 5: phase별 타임아웃 집행

**Files:**
- Modify: `src/dms/execution.py` (`JobSpec`에 `timeout_seconds`)
- Modify: `src/dms/stepper.py` (`_build_spec`, preview TIMED_OUT 매핑)
- Modify: `src/dms/execution_manifests.py` (`activeDeadlineSeconds`)
- Modify: `src/dms/execution_volcano.py` (deadline → TIMED_OUT 판정)
- Test: `tests/test_timeout_enforcement.py` (신규)

**Interfaces:**
- Consumes: `control.get_policy(tool)` → `preview_timeout_seconds`/`execution_timeout_seconds`
- Produces: `JobSpec.timeout_seconds`, 매니페스트 `activeDeadlineSeconds`, `poll()`의 `ExecStatus.TIMED_OUT`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_timeout_enforcement.py`. 네 층을 각각 검증한다:

1. **`_build_spec`**: 정책이 `preview_timeout_seconds=3600, execution_timeout_seconds=259200`일 때 phase `preview`/`preflight`/`exec_preflight` → 3600, `execution` → 259200. 정책이 없으면 `None`. (stepper 테스트가 spec을 얻는 방법은 기존 `tests/test_stepper*.py`를 참고하되, `_build_spec`을 직접 호출해도 된다.)
2. **매니페스트**: `timeout_seconds=120`인 spec으로 `build_volcano_job`/`build_preflight_pod`를 부르면 `activeDeadlineSeconds == 120`이 들어가고, `None`이면 그 키가 **없다**.
3. **deadline 판정**: 순수 함수(아래 Step 3에서 만드는 것)가 Pod `status.reason == "DeadlineExceeded"` → `TIMED_OUT`, 그 외 실패는 `FAILED`. VCJob도 같은 신호를 주면 `TIMED_OUT`, 불확실하면 `FAILED`.
4. **stepper preview 경로**: 어댑터가 `TIMED_OUT`을 반환하면 잡이 `TimedOut`으로 종단한다(현재는 `preview_failed`로 Failed).

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_timeout_enforcement.py -v`
Expected: FAIL (필드/키/함수 없음)

- [ ] **Step 3: 구현한다**

(a) `src/dms/execution.py`의 `JobSpec`에 필드를 추가한다. **dataclass 기본값이 있어야 기존 생성부가 안 깨진다**:

```python
    timeout_seconds: int | None = None
```

(b) `src/dms/stepper.py`의 `_build_spec`에서 정책을 읽어 넣는다:

```python
        policy = self._repos.control.get_policy(job["tool"])
        if policy is None:
            timeout = None
        elif phase == "execution":
            timeout = policy["execution_timeout_seconds"]
        else:
            timeout = policy["preview_timeout_seconds"]
```

그리고 `JobSpec(...)` 호출에 `timeout_seconds=timeout`을 넘긴다. `self._repos.control`이 stepper에서 접근 가능한지 확인하고, 아니면 생성자에 이미 들어와 있는 repos를 통해 접근한다.

(c) `src/dms/execution_manifests.py`: Volcano Job의 `spec`과 preflight Pod의 pod `spec`에 조건부로 넣는다. 두 곳 모두 dict를 만든 뒤:

```python
    if spec.timeout_seconds:
        job_spec["activeDeadlineSeconds"] = spec.timeout_seconds
```

같은 형태로, **값이 없으면 키 자체를 넣지 않는다**. `_build_nsync_job` 경로도 빠뜨리지 말 것.

(d) `src/dms/execution_volcano.py`: deadline 판정을 순수 함수로 뺀다:

```python
def _deadline_exceeded(obj) -> bool:
    """k8s 가 activeDeadlineSeconds 로 죽였는지. 확실하지 않으면 False —
    오분류(멀쩡한 실패를 TimedOut 으로)보다 보수적 유지가 낫다."""
    status = obj.get("status") or {}
    if status.get("reason") == "DeadlineExceeded":
        return True
    for cond in (status.get("conditions") or []):
        if cond.get("reason") == "DeadlineExceeded":
            return True
    return False
```

`_poll_pod`와 vcjob 폴링에서 `FAILED`로 판정되는 지점에 이 검사를 걸어 `TIMED_OUT`을 반환한다.

(e) `src/dms/stepper.py`의 `_poll_preview`: SUCCEEDED가 아닌 분기에서 `status == ExecStatus.TIMED_OUT`이면 `DataJobState.TIMED_OUT`(reason `preview_timed_out`)으로, 그 외에는 기존대로 `Failed`(`preview_failed`)로 종단한다.

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `.venv/bin/python -m pytest tests/test_timeout_enforcement.py -v` → PASS
Run: `.venv/bin/python -m pytest tests/ -q` → 전부 통과. 매니페스트 스냅샷을 비교하는 기존 테스트가 있으면 `activeDeadlineSeconds`가 **없는** 경우(정책 없음)에 기존과 동일해야 한다.

- [ ] **Step 5: 커밋**

```bash
git add src/dms/execution.py src/dms/stepper.py src/dms/execution_manifests.py src/dms/execution_volcano.py tests/test_timeout_enforcement.py
git commit -m "feat(execution): enforce per-phase timeouts and surface TimedOut"
```

---

### Task 6: 배치 item Cancelled 분기 + 프론트 요청 취소

**Files:**
- Modify: `src/dms/batch_orchestrator.py` (`_record_terminal`)
- Modify: `frontend/src/features/jobs/useJobs.ts` (`useCancelRequest`)
- Modify: `frontend/src/features/jobs/RequestDetail.tsx`
- Modify: `frontend/src/lib/api.ts` (reason 코드)
- Test: `tests/test_batch_cancelled_item.py` (신규)
- Test: `frontend/src/features/jobs/RequestDetail.test.tsx` (확장)

**Interfaces:**
- Consumes: Task 4의 `POST /api/user/requests/{id}:cancel`
- Produces: 취소가 실패 통계에 섞이지 않는 배치 집계, 요청 취소 버튼

- [ ] **Step 1: 백엔드 테스트를 쓴다**

`tests/test_batch_cancelled_item.py`: 자식 요청이 `Cancelled`로 끝난 배치 item이 **`Cancelled`** 상태가 되고 `failed` 카운터가 **오르지 않는다**. Rejected는 기존대로 `Rejected`+failed, Failed는 `Failed`+failed. 배치 완료 판정(`terminal == total`)이 여전히 동작해 배치가 Completed에 도달하는지도 확인한다.

- [ ] **Step 2: 실패를 확인하고 구현한다**

`_record_terminal`의 else 가지를 셋으로 나눈다:

```python
            elif req_state == RequestState.CANCELLED.value:
                # 취소는 성공도 실패도 아니다 — 카운터를 올리지 않는다.
                self._repos.batches.set_item_status(batch_id, item["seq"], "Cancelled",
                                                    reason_code=req_state)
```

(`set_item_status`의 실제 시그니처를 확인하고 맞춘다.) Rejected/그 밖 분기는 그대로 둔다.

Run: `.venv/bin/python -m pytest tests/test_batch_cancelled_item.py -v` → PASS
Run: `.venv/bin/python -m pytest tests/ -q` → 전부 통과

- [ ] **Step 3: 프론트 훅과 reason 코드를 추가한다**

`frontend/src/lib/api.ts`의 `REASON_MESSAGES`에 **아직 없는 것만** 추가한다:

```ts
  cancel_failed: "취소에 실패했습니다 — 실행 중인 작업을 종료하지 못했습니다",
  batch_not_cancelable: "취소할 수 없는 상태의 배치입니다",
  request_not_found: "요청을 찾을 수 없습니다",
  cancelled_by_user: "사용자가 취소했습니다",
  cancelled_by_batch: "배치 취소로 종료되었습니다",
```

`frontend/src/features/jobs/useJobs.ts`에 추가:

```ts
export function useCancelRequest(requestId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend("POST", `/api/user/requests/${requestId}:cancel`),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["request", requestId] });
      qc.invalidateQueries({ queryKey: ["requests"] });
      qc.invalidateQueries({ queryKey: ["request", requestId, "jobs"] });
    },
  });
}
```

- [ ] **Step 4: RequestDetail에 요청 취소 버튼을 붙인다**

요청이 **비종단이고 잡이 하나도 없을 때만** "요청 취소" 버튼을 요청 카드에 렌더한다(잡이 있으면 기존 잡 단위 취소 버튼이 그 일을 한다). 종단 판정은 `frontend/src/lib/jobState.ts`의 헬퍼를 쓰되, 요청 상태에 맞는 것이 없으면 파일 안에 작은 상수 배열(`["Succeeded","Failed","Rejected","Conflict","Cancelled"]`)로 판정한다. 에러는 `(cancel.error as ApiError).message`로 인라인 표시한다.

- [ ] **Step 5: 프론트 테스트를 확장한다**

`RequestDetail.test.tsx`에 추가: (a) 잡이 없는 `Pending` 요청에 "요청 취소" 버튼이 보이고, 누르면 `POST /api/user/requests/:id:cancel`이 호출된다; (b) 잡이 있으면 그 버튼이 **보이지 않는다**; (c) 종단 요청이면 보이지 않는다.

- [ ] **Step 6: 전체 확인**

Run(from `frontend/`): `npm test && npx tsc -b` → 전부 PASS, tsc 0

- [ ] **Step 7: 커밋**

```bash
git add src/dms/batch_orchestrator.py tests/test_batch_cancelled_item.py frontend/src/lib/api.ts frontend/src/features/jobs/useJobs.ts frontend/src/features/jobs/RequestDetail.tsx frontend/src/features/jobs/RequestDetail.test.tsx
git commit -m "feat: cancelled batch items are not failures; request cancel button"
```
