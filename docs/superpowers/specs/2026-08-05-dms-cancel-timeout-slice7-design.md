# DMS — 슬라이스 7 (취소·타임아웃 정합성) 설계

2026-08-05. 상위 스펙 `2026-08-02-dms-clean-slate-design.md` §5(Cancel, phase별 타임아웃)의 하위
구현 문서. 슬라이스 1~6은 구현·실증·배포 완료. 충돌 시 상위 스펙이 이긴다.

이 슬라이스는 **화면이 거의 없다.** 실행 경로(stepper·어댑터·오케스트레이터)를 고치는 슬라이스라
회귀 위험이 앞선 것들보다 크고, 그만큼 터미널 가드를 먼저 깔고 그 위에 나머지를 얹는다.

## 0. 배경 & 범위

상위 스펙 §5 Cancel은 **"Volcano 잡 종료가 성공한 뒤에만 DB를 Cancelled로 기록하고, 종료 실패
시 취소 실패로 보고한다 — 거짓 취소 금지"** 라고 못박는다. 그런데:

- **배치 취소가 거짓 취소다.** `routes_batches.cancel_batch`는 `batch_items` 행만 Cancelled로
  바꾸고 `execution_adapter`를 전혀 부르지 않는다. 이미 materialize된 자식 잡은 클러스터 자원을
  계속 쓰며 끝까지 진행한다. (잡 단위 취소 `routes_jobs.cancel_job`은 제대로 terminate한다.)
- **`set_job_state`에 터미널 가드가 없다.** 취소 직후 stepper 틱이 Cancelled를 Executing으로
  덮어쓰고 고아 Volcano Job을 만들 수 있다.
- **phase별 타임아웃이 집행되지 않는다.** 정책 행의 `preview_timeout_seconds`·
  `execution_timeout_seconds`는 저장만 되고 소비처가 0건이다. 매니페스트에
  `activeDeadlineSeconds`가 없고 `ExecStatus.TIMED_OUT`은 `poll()`이 절대 반환하지 않아
  **`DataJobState.TIMED_OUT`은 도달 불가능한 상태**다. 멈춘 MPI 잡이 무기한 Running으로 남는다.
- **Pending 요청은 취소할 수 없다.** `:cancel`은 data_job 단위에만 있고, planner가 잡을 emit하기
  전 요청은 취소 경로가 없다.
- **배치 item이 Cancelled를 Failed로 뭉갠다.** `_record_terminal`이 Succeeded/Rejected 외 전부
  "Failed"로 분류해 실패 카운터를 올린다 — 운영자가 취소한 것이 실패 통계에 섞인다.

### 0.1 담는 것

1. **터미널 가드**(`set_job_state`) — 종단 잡의 상태 전이를 거부한다.
2. **배치 취소의 실제 종료** — in-flight 자식의 Volcano Job/preflight Pod를 terminate하고
   자식 잡·요청을 Cancelled로 종결. 종료 실패 시 **취소 실패로 보고**한다.
3. **Pending 요청 취소** — `POST /api/user/requests/{id}:cancel`.
4. **phase별 타임아웃 집행** — 정책 값을 `JobSpec`으로 흘려 매니페스트
   `activeDeadlineSeconds`에 심고, `poll()`이 DeadlineExceeded를 `TIMED_OUT`으로 매핑한다.
5. **배치 item Cancelled 분기** — 취소를 실패로 세지 않는다.

### 0.2 비목표

- 종료된 Volcano Job/preflight Pod의 **GC**(`ttlSecondsAfterFinished` 또는 별도 루프) — 별도
  후속. 아티팩트는 남으므로 진단은 가능하다(슬라이스 5).
- 재시도(`DMS_JOB_MAX_ATTEMPTS`는 여전히 dead config) — 별도 슬라이스.
- 정책의 `default_priority` 적용 — 별도 후속.
- 배치 취소 시 **아직 materialize 안 된 Queued item**은 지금처럼 Cancelled로 표시만 한다(요청이
  없으니 종료할 것도 없다).

## 1. 백엔드

### 1.1 터미널 가드 (`repositories/data_jobs.py`)

`set_job_state`가 현재 상태를 이미 읽고 있으므로 그 자리에서 판정한다:

- 현재 상태가 `TERMINAL_DATA_JOB_STATES`에 있으면 **아무 것도 하지 않고 조용히 반환**한다
  (`finalize_from_job`이 이미 쓰는 멱등 패턴과 같다). 예외를 던지면 stepper 루프가 한 잡 때문에
  죽고, 취소는 정상 동작인데 경쟁만 늦게 도착한 것이므로 오류가 아니다.
- 단, **전이 기록도 남기지 않는다** — 일어나지 않은 전이를 기록하면 타임라인이 거짓말을 한다.

이 가드가 (2)의 전제다: 취소가 Cancelled를 쓴 뒤 stepper 틱이 도착해도 되돌릴 수 없다.

### 1.2 배치 취소의 실제 종료 (`api/routes_batches.py`)

`cancel_batch`를 다음으로 바꾼다:

1. 배치 상태 가드는 기존 그대로(`Previewing`/`Running`이 아니면 409 `batch_not_cancelable`).
2. item을 훑어 **Materialized**(= 실제 요청이 있는) 것들의 `request_id`로 자식 data_job을 찾는다.
3. 각 자식의 **비종단** 잡에 대해 `phase_refs`의 모든 ref를 `execution_adapter.terminate(ref)`.
   - 하나라도 `ExecutionError`면 **거기서 멈추고 `500 cancel_failed`**를 반환한다. 이미 종료한
     것은 그대로 두고, DB는 **아직 Cancelled로 바꾸지 않는다**(거짓 취소 금지). 운영자가 재시도할
     수 있다.
4. 종료가 전부 성공하면 각 자식 잡을 `Cancelled`(reason `cancelled_by_batch`)로, 각 자식 요청을
   `finalize_from_job(..., CANCELLED, ...)`로 종결하고, item을 Cancelled로 바꾼다.
5. Queued(미materialize) item은 Cancelled로 표시만 한다.
6. 배치를 Cancelled로.

`routes_jobs.cancel_job`의 로직과 같은 모양이므로, **공용 헬퍼**를 `src/dms/api/cancel.py`에
두고 양쪽이 쓴다: `terminate_job(adapter, job) -> None`(비종단이면 모든 ref terminate, 실패 시
`ExecutionError` 전파).

### 1.3 Pending 요청 취소 (`api/routes_requests.py`)

```
POST /api/user/requests/{request_id}:cancel
```

- 소유권: 본인 요청 또는 관리자(없으면 `404 request_not_found`) — `routes_jobs._owned_request`
  재사용.
- 요청이 이미 종단이면 `409 already_terminal`.
- 요청에 딸린 **비종단 data_job이 있으면** 그것들을 §1.2와 같은 헬퍼로 terminate한 뒤 Cancelled로
  종결한다(종료 실패 시 `500 cancel_failed`).
- 잡이 없으면(=Pending, planner 미도달) 요청만 `Cancelled`로 종결한다.
- 어느 경로든 `requests.set_state(..., CANCELLED, reason_code="cancelled_by_user")`.

**planner와의 경쟁**: planner가 같은 틱에 이 요청을 계획할 수 있다. planner는
`requests.claim_plannable()`류로 Pending만 집어가므로, 취소가 먼저 Cancelled를 쓰면 planner는
그 요청을 더 이상 보지 않는다. 반대 순서면 잡이 생기고, 그 잡은 위의 "비종단 잡" 경로로 종료된다.
어느 쪽이든 고아가 남지 않는다 — **다만 planner가 잡을 만든 직후 취소가 요청만 종결하는 창**이
있으므로, 취소는 **요청을 종결하기 전에 잡을 먼저 조회·종료**하는 순서로 구현한다.

### 1.4 phase별 타임아웃 집행

- `JobSpec`에 `timeout_seconds: int | None` 필드 추가(기본 `None`).
- `stepper._build_spec`이 그 잡의 도구 정책을 읽어 phase에 맞는 값을 넣는다:
  `preview`/`exec_preflight`/`preflight` → `preview_timeout_seconds`,
  `execution` → `execution_timeout_seconds`. 정책이 없거나 값이 NULL이면 `None`.
- `execution_manifests`: `activeDeadlineSeconds`를 심는다 — Volcano Job은 `spec`에,
  preflight Pod은 pod `spec`에. `None`이면 필드를 넣지 않는다(기존 매니페스트와 동일).
- `execution_volcano.poll()`: 종료된 대상이 **deadline 초과로 죽었는지** 판정해 `TIMED_OUT`을
  반환한다.
  - Pod: `status.reason == "DeadlineExceeded"`.
  - Volcano Job: `status.conditions`/`state.phase`에서 같은 신호를 찾되, 확실하지 않으면 기존대로
    `FAILED`로 둔다(오분류보다 보수적 유지가 낫다). 판정 로직은 순수 함수로 빼서 단위 테스트한다.
- stepper는 이미 `ExecStatus.TIMED_OUT` → `DataJobState.TIMED_OUT`을 처리한다(`_poll_execution`).
  preview 경로에도 같은 매핑을 넣는다(현재는 SUCCEEDED가 아니면 전부 `preview_failed`).

### 1.5 배치 item Cancelled 분기 (`batch_orchestrator.py`)

`_record_terminal`의 else 가지를 셋으로 나눈다:

- `Rejected` → item `Rejected`, failed 카운트 +1 (기존)
- `Cancelled` → item **`Cancelled`**, **카운터 증가 없음**(성공도 실패도 아니다)
- 그 밖(Failed/Conflict) → item `Failed`, failed +1 (기존)

배치 완료 판정은 `terminal == total`이라 카운터가 아니라 item 상태를 보므로, 카운터를 올리지
않아도 배치는 정상적으로 Completed에 도달한다.

## 2. 프론트엔드 (작다)

- `features/jobs/useJobs.ts`: `useCancelRequest(requestId)` 추가(`POST
  /api/user/requests/{id}:cancel`, 성공 시 `["request", id]`·`["requests"]` 무효화).
- `RequestDetail.tsx`: 요청이 **비종단인데 잡이 아직 없을 때** "요청 취소" 버튼을 노출한다.
  잡이 있으면 기존 잡 단위 취소 버튼이 그 역할을 한다.
- `lib/api.ts` reason 코드: `cancel_failed`("취소에 실패했습니다 — 실행 중인 작업을 종료하지
  못했습니다"), `batch_not_cancelable`, `request_not_found`, `cancelled_by_batch`,
  `cancelled_by_user`.

## 3. 테스트

- **터미널 가드**: 종단 잡에 `set_job_state`를 호출해도 상태·전이가 변하지 않는다(멱등).
- **배치 취소**: materialize된 자식이 있는 배치를 취소하면 어댑터의 `terminate`가 각 ref로
  호출되고, 자식 잡·요청이 Cancelled가 되며, item이 Cancelled가 된다. **`terminate`가 실패하면
  500이고 DB는 그대로**(거짓 취소 금지)라는 것을 명시 단언한다.
- **요청 취소**: 잡 없는 Pending 요청 → 200 + Cancelled; 잡 있는 요청 → terminate 호출 + 둘 다
  Cancelled; 이미 종단 → 409; 타인 요청 → 404.
- **타임아웃**: `_build_spec`이 phase별로 올바른 정책 값을 넣는다; 매니페스트에
  `activeDeadlineSeconds`가 들어가고 `None`이면 빠진다; deadline 판정 함수가 Pod/VCJob 신호를
  `TIMED_OUT`으로 매핑하고 그 외는 `FAILED`로 둔다; stepper가 preview 경로에서도 TIMED_OUT을
  전달한다.
- **배치 item 분기**: 자식이 Cancelled면 item이 Cancelled고 failed 카운터가 오르지 않는다.
- **프론트**: 잡 없는 비종단 요청에 "요청 취소" 버튼이 보이고 누르면 올바른 경로로 POST한다;
  잡이 있으면 안 보인다.

## 4. 배포/실증 (구현 후)

마이그레이션 변경 없음 → migrate Job 재실행 불필요. **stepper·오케스트레이터·어댑터가 모두
바뀌므로 controller 재배포가 필수**다. 이미지 d16으로 api·controller 모두 갱신.

실증:
- Pending 요청을 취소 → 즉시 Cancelled.
- 실행 중인 잡을 취소 → Volcano Job이 실제로 사라지고(`kubectl get vcjob`) DB가 Cancelled.
- 배치를 실행 중에 취소 → 자식 잡이 종료되고 item이 **Cancelled**(Failed 아님), 실패 카운터가
  오르지 않음.
- 타임아웃: rm/scan 정책의 `execution_timeout_seconds`를 아주 작게(예: 20초) 바꾸고 오래 걸리는
  잡을 띄워 `TimedOut`으로 종단하는지 — 끝나면 정책을 원복한다.

## 5. 결정 기록

- 터미널 가드는 **조용한 멱등 무시**(예외 아님) — 취소는 정상 동작이고 stepper 루프를 죽여선
  안 된다. 전이 기록도 남기지 않는다.
- 배치 취소는 **종료 성공 후에만** DB를 바꾼다. 실패하면 500 `cancel_failed`, DB 불변.
- 취소는 **요청을 종결하기 전에 잡을 먼저 종료**한다(planner 경쟁 창을 좁힌다).
- 타임아웃 판정이 불확실하면 `FAILED`로 유지한다(오분류보다 보수적).
- `activeDeadlineSeconds`는 **Volcano Job의 `spec`이 아니라 각 task의 파드 템플릿 `spec`에**
  넣는다. Volcano v1.15.0 CRD의 `Job.spec`에는 그 필드가 없어 API 서버가 조용히 잘라내기
  때문이다(그 상태로는 타임아웃이 전혀 걸리지 않는다). 파드 템플릿은 실제 PodSpec이라 살아남는다.
- vcjob의 deadline 판정은 `status.state.reason`/`.message`를 본다 — Volcano의
  `status.conditions[]` 항목에는 `reason` 필드 자체가 없다.
- **scan·rm의 실행 타임아웃 시드 기본값은 24h**다(초안 1h에서 상향). 데드라인이 실제로
  집행되면 1h는 대규모 dscan/drm을 중간에 죽이고, drm은 부분 삭제로 남는다. 운영자는
  포탈 `/admin/policies`에서 조정한다.
- 배치 item의 Cancelled는 **성공도 실패도 아니다** — 카운터를 올리지 않는다.
- Volcano Job/Pod GC는 이번 범위 밖.
