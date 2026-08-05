# DMS 슬라이스 10 (운영 안정화 묶음) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 슬라이스 4~9가 남긴 후속을 한 번에 정리한다 — 클러스터 잔해 GC, 정책 기본 우선순위 적용, phase 라벨 버그, PreviewReady 배치 취소, 로그 tail 일관성, 배치 리포지토리 결함 둘, dead config 제거, 감사 화면 컬럼, 의존성 취약점.

**Architecture:** 새 기능은 GC 하나뿐이다(Volcano Job은 매니페스트 `ttlSecondsAfterFinished`, preflight Pod은 컨트롤러 GC 루프). 나머지는 기존 코드가 제대로 동작하게 만드는 국소 수정이다.

**Tech Stack:** Python 3.11 / FastAPI / pytest · React 18 + Vite 5 + TS + Vitest · MSW 2

## Global Constraints

- 설계 문서 `docs/superpowers/specs/2026-08-06-dms-ops-hardening-slice10-design.md`가 상위 규칙이다.
- **GC는 종단 잡만 대상으로 한다.** 비종단 잡의 파드를 지우면 stepper가 그것을 실패로 오인한다 — 이것이 이 슬라이스에서 가장 위험한 부분이다.
- **자동 재시도는 구현하지 않는다.** 스펙에 근거가 없고 실패한 `rm`/`sync`의 자동 재실행은 파괴적이다. `DMS_JOB_MAX_ATTEMPTS`는 **제거**한다.
- **유지보수 중 `BatchOrchestrator` 정지는 하지 않는다** — 슬라이스 4 최종 리뷰가 명시적으로 반대한 결정을 유지한다.
- 기존 컨트롤러 루프의 리스(`component_leases`)·주기 관례를 그대로 따른다.
- 한국어 UI 문자열. 이모지 금지.
- 백엔드 테스트는 `.venv/bin/python -m pytest`(plain `python3`는 이 환경에서 깨져 있다). 프론트는 `frontend/`에서 `npm test`, `npx tsc -b`.
- 커밋은 태스크 단위, 각 태스크는 테스트 GREEN 상태로 끝난다.

---

### Task 1: 국소 수정 5건 (라벨 · PreviewReady · tail · batches.list · 트랜잭션)

작고 서로 독립적인 수정들을 한 태스크로 묶는다. 각각 테스트를 붙인다.

**Files:**
- Modify: `src/dms/execution_manifests.py` (phase 라벨)
- Modify: `src/dms/api/routes_batches.py` (PreviewReady 허용)
- Modify: `src/dms/api/artifacts.py` (`tail_lines` 추출)
- Modify: `src/dms/api/routes_artifacts.py` (로그 tail이 공용 헬퍼 사용)
- Modify: `src/dms/repositories/batches.py` (`list()` options 복원, `reset_failed_items` 트랜잭션)
- Test: `tests/test_ops_hardening_small.py` (신규) + 기존 테스트 확장

**Interfaces:**
- Produces: `artifacts.tail_lines(text, n) -> str` — Task 없음(내부 공용)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_ops_hardening_small.py`:

```python
def test_exec_preflight_pod_carries_its_own_phase_label():
    # build_preflight_pod 로 exec_preflight spec 을 만들면 라벨이 "exec_preflight" 여야 한다.
    # (기존 preflight 는 그대로 "preflight")
    ...


def test_tail_lines_does_not_split_on_carriage_return():
    from dms.api.artifacts import MAX_TAIL_LINES, tail_lines
    text = "a\rb\rc\nsecond"
    assert tail_lines(text, 1) == "second"
    assert tail_lines("x\n" * 10, 3) == "x\nx\nx"
    # 클램프
    assert tail_lines("y\n" * 5, MAX_TAIL_LINES + 100) == ("y\n" * 5).rstrip("\n")


def test_batches_list_hydrates_options(db):
    # batches.create 로 options 를 넣고 list() 가 dict 를 주는지 (raw JSON 문자열이 아니라)
    ...


def test_reset_failed_items_is_transactional(db):
    # 정상 경로가 여전히 동작하는지 (트랜잭션으로 감싼 뒤 회귀가 없음을 확인)
    ...
```

`...` 부분은 실제 시그니처를 읽고 채운다. `build_preflight_pod`의 인자와 `batches.create`/`reset_failed_items`의 시그니처는 소스에서 확인할 것.

PreviewReady 취소는 `tests/test_api_batch_cancel.py`에 케이스를 **추가**한다(그 파일이 배치+자식 픽스처를 이미 갖고 있다): `PreviewReady` 상태의 배치를 취소하면 200이고 자식이 종료된다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_ops_hardening_small.py tests/test_api_batch_cancel.py -v`
Expected: 신규 케이스들이 FAIL

- [ ] **Step 3: 다섯 군데를 고친다**

1. `src/dms/execution_manifests.py`의 preflight Pod 라벨 `"dms.io/phase": "preflight"` → `spec.phase`.
2. `src/dms/api/routes_batches.py`의 `cancel_batch` 상태 가드에 `"PreviewReady"` 추가.
3. `src/dms/api/artifacts.py`에 `tail_lines(text, n)`를 추출한다 — `read_artifact`의 tail 로직(`split("\n")`, 끝 개행 처리, `MAX_TAIL_LINES` 클램프)을 그대로 옮기고 `read_artifact`가 그것을 호출하게 한다.
4. `src/dms/api/routes_artifacts.py`의 `get_job_logs`가 `log.splitlines()[-tail:]` 대신 `tail_lines(log, tail)`을 쓴다.
5. `src/dms/repositories/batches.py`: `list()`가 `get`/`list_active`와 같은 방식으로 `options`를 `load_json`하고, `reset_failed_items`를 `with self._db.transaction():`으로 감싼다.

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `.venv/bin/python -m pytest tests/ -q` → 전부 통과 (기준선 552 + 신규)

- [ ] **Step 5: 커밋**

```bash
git add src/dms/execution_manifests.py src/dms/api/routes_batches.py src/dms/api/artifacts.py src/dms/api/routes_artifacts.py src/dms/repositories/batches.py tests/test_ops_hardening_small.py tests/test_api_batch_cancel.py
git commit -m "fix: phase label, PreviewReady cancel, shared tail, batch list/reset"
```

---

### Task 2: dead config 제거 (`DMS_JOB_MAX_ATTEMPTS`)

**Files:**
- Modify: `src/dms/config.py`
- Modify: `deploy/k8s/20-config.yaml`
- Test: 기존 config 테스트 확인/갱신

**Interfaces:**
- Produces: 없음 (제거)

- [ ] **Step 1: 소비처가 정말 없는지 확인한다**

Run: `grep -rn "max_attempts\|MAX_ATTEMPTS" src/ tests/ deploy/`
`config.py`, `20-config.yaml`, 그리고 config 테스트 말고 다른 소비처가 나오면 **중단하고 보고한다** — 그 경우 이 태스크의 전제가 틀린 것이다.

- [ ] **Step 2: 제거한다**

`src/dms/config.py`에서 `("DMS_JOB_MAX_ATTEMPTS", "job_max_attempts", 3)` 매핑과 `job_max_attempts: int = 3` 필드를 지운다. `deploy/k8s/20-config.yaml`에서 `DMS_JOB_MAX_ATTEMPTS: "3"` 줄을 지운다.

주석을 한 줄 남긴다 — 왜 없는지가 다음 사람에게 필요한 정보다. `config.py`의 적당한 위치에:

```python
# 재시도 설정은 두지 않는다: 상위 스펙에 재시도 요구가 없고, 실패한 rm/sync 를 자동으로
# 재실행하는 것은 파괴적이다. 재실행은 배치 :rerun-failed 와 사용자 재제출로 한다.
```

- [ ] **Step 3: 테스트를 고친다**

config 테스트가 그 필드를 단언하고 있으면 그 단언을 지운다(설정이 사라졌으므로 정당하다). 어떤 테스트를 고쳤는지 보고서에 적는다.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/ -q` → 전부 통과

- [ ] **Step 5: 커밋**

```bash
git add src/dms/config.py deploy/k8s/20-config.yaml tests/
git commit -m "chore(config): drop unused DMS_JOB_MAX_ATTEMPTS"
```

---

### Task 3: 정책 `default_priority` 적용

**Files:**
- Modify: `src/dms/api/routes_requests.py`
- Modify: `src/dms/batch_orchestrator.py` (`_materialize`의 하드코딩)
- Test: `tests/test_default_priority.py` (신규)

**Interfaces:**
- Consumes: `repos.control.get_policy(tool)`
- Produces: 클라이언트가 priority를 생략하면 정책의 `default_priority`가 쓰인다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_default_priority.py`, 검증:

1. `priority`를 **보내지 않은** scan 제출이 `scan` 정책의 `default_priority`를 받는다(정책을 `low`로 바꿔두고 확인).
2. `priority`를 **보낸** 제출은 그 값이 이긴다(정책이 `low`여도 `high`를 보내면 `high`).
3. 정책이 없으면 `mid`로 폴백한다.
4. rm은 `rm` 정책, sync는 **`dsync` 정책**을 쓴다(설계 결정 — 제출 시점에 도구가 미정이라 dsync를 대표로 삼는다).
5. 배치가 materialize한 자식 요청도 같은 규칙을 따른다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_default_priority.py -v`
Expected: FAIL — 항상 `mid`

- [ ] **Step 3: 구현한다**

`src/dms/api/routes_requests.py`:

- `RequestBody.priority`의 기본값을 `str | None = None`으로 바꾼다.
- `submit`에서 payload를 만들기 **전에** 유효 priority를 정한다:

```python
_OP_POLICY = {"scan": "scan", "rm": "rm", "sync": "dsync"}


def resolve_priority(repos, operation: str, requested: str | None) -> str:
    # 클라이언트가 명시하면 그 값이 이긴다. 생략하면 정책의 기본값, 그것도 없으면 mid.
    # sync 는 제출 시점에 도구(dsync/nsync)가 정해지지 않으므로 dsync 정책을 대표로 읽는다.
    if requested is not None:
        return requested
    policy = repos.control.get_policy(_OP_POLICY.get(operation, ""))
    return (policy or {}).get("default_priority") or "mid"
```

- `_validated_payload`의 `if body.priority not in PRIORITIES` 검증은 **해결된 값**에 대해 수행해야 한다 — 해결을 검증보다 먼저 하고, 해결된 값을 `requests.create(priority=...)`에 넘긴다. `_validated_payload`가 `body.priority`를 직접 보고 있으면 그 부분을 해결된 값으로 넘기도록 시그니처를 조정한다(파일을 읽고 최소 변경으로).
- `src/dms/batch_orchestrator.py`의 `_materialize`가 `priority="mid"` 하드코딩이므로 같은 헬퍼를 쓴다(배치의 operation을 넘긴다).

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `.venv/bin/python -m pytest tests/ -q` → 전부 통과. **기존 제출 테스트가 priority를 명시하고 있으면 동작이 안 바뀐다** — 안 바뀌는지 확인하고, 바뀐 게 있으면 그 이유를 보고서에 적는다.

- [ ] **Step 5: 커밋**

```bash
git add src/dms/api/routes_requests.py src/dms/batch_orchestrator.py tests/test_default_priority.py
git commit -m "feat(api): apply the policy default_priority when unspecified"
```

---

### Task 4: Volcano Job TTL

**Files:**
- Modify: `src/dms/config.py` (`vcjob_ttl_seconds`)
- Modify: `src/dms/execution.py` (`JobSpec`에 필드 추가)
- Modify: `src/dms/stepper.py` (`_build_spec`이 값을 넣는다)
- Modify: `src/dms/execution_manifests.py` (Job spec에 `ttlSecondsAfterFinished`)
- Test: `tests/test_vcjob_ttl.py` (신규)

**Interfaces:**
- Produces: 매니페스트의 `spec.ttlSecondsAfterFinished`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

- `ttl_seconds=3600`인 spec으로 `build_volcano_job`/`_build_nsync_job`을 부르면 **Job의 `spec`**에 `ttlSecondsAfterFinished == 3600`이 들어간다. (task 템플릿이 아니라 Job spec이다 — v1.15.0 CRD의 `Job.spec` 허용 필드에 이것이 **포함**돼 있다. 슬라이스 7에서 `activeDeadlineSeconds`가 pruning된 것과 대비되는 지점이다.)
- `ttl_seconds`가 `None`/0이면 그 키가 **없다**.
- preflight Pod에는 이 필드를 넣지 않는다(파드에는 존재하지 않는 필드다).
- `_build_spec`이 설정값을 spec에 넣는다.

- [ ] **Step 2: 실패를 확인한다** → FAIL

- [ ] **Step 3: 구현한다**

- `config.py`: `("DMS_VCJOB_TTL_SECONDS", "vcjob_ttl_seconds", 86400)` 매핑과 `vcjob_ttl_seconds: int = 86400` 필드를 기존 스타일대로 추가한다.
- `execution.py`의 `JobSpec`에 `ttl_seconds: int | None = None`(기본값 필수 — 기존 생성부가 깨지면 안 된다).
- `stepper._build_spec`이 `ttl_seconds=self._settings.vcjob_ttl_seconds`를 넣는다.
- `execution_manifests`의 **Volcano Job 두 빌더**에서 `if spec.ttl_seconds: job["spec"]["ttlSecondsAfterFinished"] = spec.ttl_seconds`.
- `deploy/k8s/20-config.yaml`에 `DMS_VCJOB_TTL_SECONDS: "86400"`을 추가한다.

- [ ] **Step 4: 통과 + 회귀 확인** → 전부 통과. 매니페스트 스냅샷 테스트가 있으면 TTL 없는 경우에 기존과 동일해야 한다.

- [ ] **Step 5: 커밋**

```bash
git add src/dms/config.py src/dms/execution.py src/dms/stepper.py src/dms/execution_manifests.py deploy/k8s/20-config.yaml tests/test_vcjob_ttl.py
git commit -m "feat(execution): set ttlSecondsAfterFinished on Volcano jobs"
```

---

### Task 5: preflight Pod GC 루프

**Files:**
- Create: `src/dms/pod_gc.py`
- Modify: `src/dms/config.py` (`pod_gc_after_seconds`, `pod_gc_interval_seconds`)
- Modify: `src/dms/controller.py` (루프 등록)
- Modify: `src/dms/repositories/data_jobs.py` (종단 잡 조회 메서드)
- Modify: `deploy/k8s/20-config.yaml`
- Test: `tests/test_pod_gc.py` (신규)

**Interfaces:**
- Consumes: `execution_adapter.terminate(ref)`(멱등, 404 삼킴), `data_jobs`
- Produces: `PodGarbageCollector.run_once() -> dict`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_pod_gc.py`, 검증:

1. **종단** 잡의 `pod/`·`pods/` ref가 종료된다.
2. **비종단 잡은 절대 대상이 아니다** — 이 슬라이스에서 가장 중요한 단언이다.
3. 종단이 된 지 `after_seconds` 미만이면 아직 대상이 아니다.
4. `vcjob/` ref는 대상이 아니다(Volcano TTL이 처리한다).
5. `terminate`가 예외를 던져도 루프가 죽지 않고 나머지를 계속 처리한다.
6. `run_once`가 처리 건수를 반환한다.

- [ ] **Step 2: 실패를 확인한다** → `ModuleNotFoundError`

- [ ] **Step 3: 구현한다**

`src/dms/repositories/data_jobs.py`에 조회를 추가한다 — 종단 상태이고 `updated_at`이 임계보다 오래된 잡을 최신순 `limit`건. 실제 컬럼명과 기존 쿼리 스타일은 파일을 읽고 맞춘다. `TERMINAL_DATA_JOB_STATES`를 파라미터로 펼친다(다른 리포지토리가 쓰는 방식을 따를 것).

`src/dms/pod_gc.py`:

```python
"""종단 잡이 남긴 preflight Pod 을 지운다. Volcano Job 은 ttlSecondsAfterFinished 가
처리하지만 베어 Pod 에는 TTL 이 없다.

**종단 잡만** 대상으로 한다 — 비종단 잡의 파드를 지우면 stepper 가 그것을 실패로 오인한다."""
import logging

logger = logging.getLogger(__name__)


class PodGarbageCollector:
    def __init__(self, repos, execution_adapter, *, after_seconds: int, limit: int = 200):
        self._repos = repos
        self._exec = execution_adapter
        self._after = after_seconds
        self._limit = limit

    def run_once(self, *, now_iso: str | None = None) -> dict:
        deleted = 0
        for job in self._repos.data_jobs.terminal_jobs_older_than(
                self._after, limit=self._limit, now_iso=now_iso):
            for ref in (job.get("phase_refs") or {}).values():
                if not ref or not str(ref).startswith(("pod/", "pods/")):
                    continue
                try:
                    self._exec.terminate(ref)
                    deleted += 1
                except Exception as exc:
                    logger.warning("pod gc failed ref=%s: %s", ref, exc)
        return {"deleted": deleted}
```

`src/dms/controller.py`의 `build_loops`에 다른 루프와 같은 형태로 등록한다(실행 어댑터가 `build_loops`에 이미 들어오는지 확인하고, 없으면 시그니처를 최소로 확장한다). 설정 두 개를 `config.py`와 `20-config.yaml`에 추가한다: `DMS_POD_GC_AFTER_SECONDS`(기본 3600), `DMS_POD_GC_INTERVAL_SECONDS`(기본 600).

- [ ] **Step 4: 통과 + 회귀 확인** → 전부 통과. 컨트롤러 루프 목록을 단언하는 기존 테스트가 있으면 새 루프를 반영해 갱신한다.

- [ ] **Step 5: 커밋**

```bash
git add src/dms/pod_gc.py src/dms/config.py src/dms/controller.py src/dms/repositories/data_jobs.py deploy/k8s/20-config.yaml tests/test_pod_gc.py
git commit -m "feat(controller): garbage-collect preflight pods of terminal jobs"
```

---

### Task 6: 프론트 — 감사 클래스 컬럼 + 의존성

**Files:**
- Modify: `frontend/src/features/audit/AuditLog.tsx`
- Modify: `frontend/src/features/audit/AuditLog.test.tsx`
- Modify: `frontend/package.json`, `frontend/package-lock.json`

**Interfaces:**
- Consumes: `AuditEntry.mutation_class`(타입에 이미 있다)

- [ ] **Step 1: 감사 컬럼을 추가한다**

`AuditLog.tsx`의 표 맨 앞에 "클래스" 컬럼을 넣고 `e.mutation_class`를 렌더한다. 테스트에 그 값이 보이는지 단언을 추가한다.

- [ ] **Step 2: 테스트 확인**

Run(from `frontend/`): `npx vitest run src/features/audit && npx tsc -b` → PASS

- [ ] **Step 3: 의존성 취약점 해소**

Run(from `frontend/`): `npm audit --omit=dev`로 현재 상태를 기록하고, `npm audit fix`(필요하면 `react-router`/`react-router-dom`을 명시적으로 올린다)를 적용한다. **메이저 업그레이드가 필요하면 하지 말고 보고한다** — 라우팅은 앱 전체가 쓰므로 별도 슬라이스로 다뤄야 한다.

- [ ] **Step 4: 전체 프론트 확인**

Run: `npm test && npx tsc -b` → 전부 PASS, tsc 0. 라우팅 관련 테스트(`router.test.tsx`)가 특히 통과해야 한다.
Run: `npm audit --omit=dev` → 남은 취약점을 보고서에 기록한다.

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/features/audit frontend/package.json frontend/package-lock.json
git commit -m "feat(portal): audit class column; bump vulnerable router dep"
```
