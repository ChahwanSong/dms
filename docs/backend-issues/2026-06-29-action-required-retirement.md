# DMS 백엔드 이슈: "조치 필요(action required)" 항목 은퇴(retirement) API 공백

- 작성일: 2026-06-29
- 상태: DESIGN ONLY (제안서) — 코드 변경 없음. DMS 백엔드 오너와 공동 구현 대상.
- **⚠️ 이후 변경(RM 제거) — 이 문서는 작성 시점 기록으로 보존한다.** RM(Resource Management)
  기능이 제거되면서 아래 분석의 일부 전제가 더 이상 유효하지 않다:
  - **경로 이동**: `:resolve`는 `POST /api/v1/operations/requests/{request_id}:resolve`로 옮겨졌다
    (구 `resource-management` 라우터는 삭제됨). 현재 스펙은
    [`../api/operations.md`](../api/operations.md) "Stuck Request 해소" 절.
  - **GAP 1**(stuck request 은퇴)은 **여전히 유효**하다 — RM과 무관하게 DM 요청도 같은 상태로
    굳는다. 위 새 경로 기준으로 읽는다.
  - **GAP 2**(`resources` 은퇴)는 다뤘던 대상(`filesystem` / `kubernetes_namespace_quota`
    ResourceKind, `filesystem.forget` / `kubernetes.namespace_quota.forget` OperationKind 제안)이
    **전부 사라져 무의미**해졌다. `kubernetes_quota_missing` / `filesystem_*` action-required
    항목도 더 이상 생성되지 않는다.
  - **GAP 3**(data_job aging/purge)·**GAP 4**(네이티브 ack)는 **여전히 유효**하다. GAP 4의
    fingerprint 기반 ack/unack은 이후 실제로 구현됐다(`/operations/action-required:ack`).
  - 본문에 나오는 `src/dms/api/routers/resource_management.py` 라인 참조는 그 파일이 삭제되어
    더 이상 유효하지 않다.
- 컨텍스트: 포탈(operator 콘솔)이 `operations` 읽기 전용 API로 `action_required` 목록을
  렌더링한다. 현재 이 목록에서 더 이상 유효하지 않거나(stale) 종료된 항목을 **영구히
  치울(retire) 방법이 부분적으로만 존재**한다. 본 문서는 그 공백을 정리한다.

---

## 0. 근본 사실: `action_required`는 매 호출마다 라이브 상태에서 재계산된다

`OperationalQueryService.action_required()`는 영속화된 알림 레코드를 읽지 않는다.
매 호출마다 라이브 상태(requests, storage_mappings, agent_reports, resources, data_jobs)에서
**처음부터 재계산**한다.

- `src/dms/query.py:43-125` — `action_required()` 본문. 구성 요소:
  - `request_attention`: `self.repository.list_action_required()` (`query.py:46`)
  - `storage_mapping_*` / readiness: `list_storage_mappings()` (`query.py:48-97`)
  - `agent_report_stale`: `list_agent_reports(...)` (`query.py:99-121`)
  - k8s quota / filesystem / data 항목: `query.py:122-124`의 세 서브메서드
- `src/dms/repositories/operational.py:221-241` — `list_action_required()`는 단순히
  `requests` 테이블을 `status IN (...)`로 SELECT한다. ack/dismiss 컬럼이나 필터가 없다.

**중요:** acknowledge / dismiss / snooze를 영속화하는 곳이 어디에도 없다.

- `diagnostic_events` 테이블(`src/dms/migrations.py:245-256`)의 컬럼은
  `event_id, correlation_id, component, severity, event_type, message, payload, created_at`뿐이다.
  `acknowledged_*` / `resolved_*` 같은 컬럼이 **없다**. (게다가 `action_required`는 애초에
  `diagnostic_events`를 읽지도 않는다 — 위 0절 참고.)

따라서 어떤 항목을 목록에서 치우는 유일한 방법은 **그 항목의 근본 라이브 상태를 바꾸는 것**이다.
근본 상태를 바꿀 API가 없으면 그 항목은 목록에 영구히 남는다. 아래 GAP들은 모두 이 한 문장의
구체적 사례다.

---

## GAP 1 — `request_attention`이 일부 상태에서 해소 불가 (stuck forever)

### Problem
`action_required`에 `request_attention`으로 노출되는 request 상태는 6가지인데
(`operational.py:222-229`):
`Blocked`, `StaleClaim`, `RecoveryNeeded`, `VerificationFailed`,
`UnknownAfterSideEffect`, `BackendApplyFailed`.

그런데 운영자가 수동으로 해소할 수 있는 엔드포인트 `requests/{id}:resolve`는 이 중
**2개만(`UnknownAfterSideEffect`, `BackendApplyFailed`)** 받는다. 나머지 4개
(`Blocked`, `StaleClaim`, `RecoveryNeeded`, `VerificationFailed`)는 resolve/cancel/abandon
경로가 전혀 없어 **목록에서 영구히 빠지지 않는다**.

### Current behavior + code refs
- `src/dms/api/routers/resource_management.py:572-638` — `resolve_request` 엔드포인트.
  - `RESOLVABLE_STATES = {UnknownAfterSideEffect, BackendApplyFailed}` (`:585-588`).
  - 그 외 상태는 `409`로 거절: `"request is in state '{X}'; only [...] can be resolved"` (`:614-618`).
- 상태 머신(`src/dms/domain.py:14-49`): `TERMINAL_LIFECYCLE_STATES`(`:39-49`)에는
  `BackendApplyFailed`만 종료 상태로 들어있고, `Blocked`/`StaleClaim`/`RecoveryNeeded`/
  `VerificationFailed`/`UnknownAfterSideEffect`는 **비종료(non-terminal)**다. 즉 이 4개는
  "비종료 + 해소불가" 조합이라 영구히 떠 있는다.
- `resolve_request`는 `update_request_status`만 호출(`resource_management.py:625`)하고
  연결된 **run/plan은 건드리지 않는다**. (state-machine 노트 참고)

### Proposed API shape
두 가지 선택지. (A)를 권장.

**(A) 기존 `:resolve` 확장 — `abandon` 해소를 4개 상태까지 허용**
```
POST /api/v1/resource-management/requests/{request_id}:resolve
body: { "resolution": "abandon", "reason": "<필수>" }
```
- `resolution: "abandon"`에 한해 허용 상태를 다음으로 확장:
  `{Blocked, StaleClaim, RecoveryNeeded, VerificationFailed, UnknownAfterSideEffect, BackendApplyFailed}`.
- `resolution: "succeeded"`는 부작용 검증이 어려운 신규 상태에서는 막아두는 편이 안전
  (특히 `Blocked`/`StaleClaim`은 side-effect 적용 여부가 불확실). 즉 신규 상태는 abandon 전용.
- 종료 상태로 전이: abandon → `Failed` (또는 신규 `Aborted` — 아래 노트).

**(B) 전용 엔드포인트 `:abandon`**
```
POST /api/v1/resource-management/requests/{request_id}:abandon
body: { "reason": "<필수>" }
response: { request_id, previous_status, resolved_to, actor, reason }
```
- 의미가 "운영자가 더는 진행하지 않기로 결정"임을 명확히 드러냄. `:resolve`의 succeeded/abandon
  이중 의미와 분리.

응답 형태는 기존 `resolve_request` 반환부(`resource_management.py:631-638`)를 그대로 재사용.

### State-machine / migration / compat
- **OperationKind 신설 불필요.** request 상태 전이일 뿐 새 작업 종류가 아니다.
- **run/lease 정합성(핵심).** `Blocked`/`StaleClaim`/`RecoveryNeeded`는 워커가 lease를 들고
  있거나 reaper(`mark_stale_runs`)가 처리 중일 수 있다. request만 `Failed`로 뒤집으면 고아 run이
  남는다. abandon 시 연결된 run도 종료(예: `Cancelled`/신규 `Aborted`)로 함께 전이시키거나,
  최소한 **활성 lease가 없을 때만** abandon을 허용해야 한다.
  (`stale_or_recovery_runs()` `query.py:710-718`가 동일 상태군의 run을 다룸 — 동일 정합성 축.)
- (선택) 신규 종료 상태 `Aborted`를 `LifecycleState` + `TERMINAL_LIFECYCLE_STATES`에 추가하면
  "운영자 포기"와 "시스템 실패(`Failed`)"를 구분 가능. 추가 시 migration 불필요(상태는 문자열),
  단 `Failed`만 기대하는 소비자(포탈/CLI 필터)와의 compat 확인 필요. 단순하게 가려면 `Failed` 재사용.
- 모든 전이는 `state_transitions`에 actor/reason과 함께 기록(기존 `update_request_status` 경로 유지).

### Priority: **High**
운영 막다른 길(operational dead-end). 4개 상태 중 하나에 빠진 request는 영구히 "조치 필요"로
남아 알림 신호를 오염시키고, 운영자가 손쓸 방법이 DB 직접 수정밖에 없다.

---

## GAP 2 — DB `resources` 레코드 은퇴(forget/decommission) API 부재

### Problem
`kubernetes_quota_missing` 항목의 `recommended_action`은 문자 그대로
**"...또는 검토 후 DMS 리소스 레코드 삭제"**를 권하지만, 그런 API가 **존재하지 않는다**.
실재하던 백엔드 스토리지가 사라진(예: 네임스페이스가 외부에서 제거됨) 경우, 부작용 없이
DB `resources` 행만 치울 방법이 없어 해당 항목이 영구히 목록에 남는다.

### Current behavior + code refs
- 권고 문구: `src/dms/query.py:1218-1219`
  ```
  if issue_type in {"kubernetes_quota_missing", "kubernetes_quota_db_only"}:
      return "recreate DMS-managed ResourceQuota or delete DMS resource record after review"
  ```
- 그러나 존재하는 delete 계열은 전부 **실제 부작용을 동반하는 작업 요청**이다:
  - `filesystem_delete` (`resource_management.py:139-154`, `202` + RM 워커가 FS 삭제)
  - `k8s_quota_delete` (`resource_management.py:309-326`, `202` + RM 워커가 ResourceQuota 삭제)
  이들은 백엔드가 아직 살아있다는 전제다. 이미 사라진 백엔드엔 부적합(삭제할 대상이 없음).
- 부작용 없이 행만 지우는 경로가 없다:
  - `resources` 테이블 정의 `src/dms/migrations.py:95-106`.
  - `src/dms/repositories/resources.py`에는 `upsert_resource` / `list_*` / `get_resource`만 있고
    `delete_resource`가 **없다**(`resources.py:23,86,100,139,203,254,324`).
  - `@router.delete`는 filesystem / k8s-quota(둘 다 부작용 작업) / storage-mapping / identity뿐.

### Proposed API shape
부작용이 **없는** 관리(admin) 동작이므로, request→plan→run 파이프라인을 타기보다
storage-mapping delete와 같은 **직접 admin 엔드포인트**가 더 깔끔하다.

```
DELETE /api/v1/resource-management/resources/{resource_kind}/{resource_key}
  ?confirm_backend_absent=true
body: { "reason": "<필수>" }
response: { "resource_kind": "...", "resource_key": "...", "forgotten": true }
```
- 의미: 백엔드 부작용 **없이** DB `resources` 행을 제거(= "잊는다/decommission").
- **가드레일(중요).** 라이브 백엔드가 살아있는데 실수로 forget 하면 drift를 숨길 수 있다.
  따라서 다음 중 하나를 강제:
  - 직전 check/audit 결과가 `Missing`(예: quota의 경우 diff status `Missing`)임을 서버가 확인, 또는
  - 명시적 `confirm_backend_absent=true` + 사유. 가능하면 라이브 확인을 시도해 실제로 부재인지 검증.
- 신규 repo 메서드 `delete_resource(resource_kind, resource_key)` 추가
  (`resources.py`, storage-mapping의 `delete_storage_mapping` `storage_mappings.py:244`와 동형).
- `observability.safe_record_event(...)`로 감사 이벤트 1건 기록(누가/언제/왜 forget).

### OperationKind
- 직접 admin 엔드포인트로 가면 **OperationKind 신설 불필요**.
- 만약 감사 일관성을 위해 request 파이프라인을 태우고 싶다면, 부작용 없는
  `kubernetes.namespace_quota.forget` / `filesystem.forget`를 신설(planner가 no-op plan,
  RM 워커가 DB 행만 제거). 다만 "부작용 없는 작업"이라는 예외 케이스를 워커에 들이는 비용이 있어
  **비권장**. 직접 엔드포인트 + observability 이벤트로 감사 추적이면 충분.

### State-machine / migration / compat
- 스키마 변경 불필요(행 삭제만). migration 없음.
- forget 후 동일 키로 재import/recreate가 자유롭도록 unique index(`uq_resources_kind_key`
  `migrations.py:106`)와 충돌하지 않음(행이 사라지므로).
- compat: 같은 리소스에 매달린 과거 requests/results는 그대로 둠(이력 보존). forget은 현재 상태
  레코드만 제거.

### Priority: **Medium**
권고 문구가 존재하는 동작을 API가 못 받는 "문서-구현 불일치". 빈도는 낮지만(백엔드가 외부에서
사라진 경우) 발생 시 DB 직접 수정 외 해법이 없다.

---

## GAP 3 — 종료된 data_job 레코드가 무기한 잔류 (aging/purge 정책 부재)

### Problem
`_data_management_action_required()`는 종료(terminal) 상태의 data_job을 **시간 윈도우 없이**
계속 노출한다. 몇 달 전 실패/취소된 잡도 영원히 "조치 필요"에 남는다(최근 1000건 한도 내).

> 참고: 현재 포탈 주도로 **per-job `DELETE /api/v1/data-management/jobs/{job_id}`**가 추가되는
> 중이다(개별 삭제). 본 GAP은 그와 **상호보완**되는 **보존/에이징 정책**을 제안한다 — 누가
> 포탈에서 클릭하지 않아도 목록이 **모든 소비자(CLI 등)에서** 자동으로 유한하게 유지되도록.

### Current behavior + code refs
- `src/dms/query.py:665-708` — `_data_management_action_required()`.
  - `self.repository.list_data_jobs(limit=1000)` 전수 순회(`:667`), 시간 컷오프 인자 없음.
  - 상태 필터는 메모리에서: `{PreflightFailed, Failed, TimedOut, Cancelled}` (`:674-680`).
    `updated_at` 기준 만료/제외 로직이 전혀 없다.
- `src/dms/repositories/data_jobs.py:210-252` — `list_data_jobs`는
  `ORDER BY data_jobs.updated_at DESC LIMIT ?`(`:247-248`)일 뿐, `updated_after` 같은 에이징
  필터가 없다. (즉 1000건을 넘겨야만 비로소 오래된 게 밀려나는 사실상의 무한 잔류.)
- 종료 data_job 상태 집합은 `TERMINAL_DATA_JOB_STATES`(`src/dms/domain.py:70-80`)로 이미 정의됨
  — 보존 정책의 대상 셋으로 재사용 가능.
- 현재 data-management 라우터에는 `:confirm`(`data_management.py:191`),
  `:cancel`(`:214`)만 있고 삭제/purge 엔드포인트는 없음(위 per-job DELETE가 추가 중).

### Proposed API shape (보존/에이징 — per-job 삭제와 보완)

**(3a) action_required 자동 제외(에이징) — 읽기 측**
- 신규 설정 `DMS_ACTION_REQUIRED_DATA_JOB_MAX_AGE_SECONDS`(`Settings.from_env`).
- `_data_management_action_required()`에서 `updated_at >= now - max_age`인 잡만 포함.
- 가능하면 DB 레벨로 내려 `list_data_jobs(..., updated_after=..., states=(...))` 인자 추가
  (메모리 후필터 → 인덱스 활용). 이렇게 하면 1000건 한도와 무관하게 항상 유한.
- **호환:** 이건 action_required에서 **숨기는 것**일 뿐, `GET /scan|/sync|/rm` 등 명시적 잡
  목록에는 여전히 보임(purge 전까지 이력 보존).

**(3b) 주기적 purge(영구 삭제) — 쓰기 측**
- 신규 설정 `DMS_DATA_JOB_RETENTION_SECONDS`(예: 90일).
- 옵션 1: 기존 `sanity-reconciler` 루프에 보존 스윕을 추가.
  옵션 2: 신규 CLI `dms data-retention --loop --interval N`.
- 동작: `state ∈ TERMINAL_DATA_JOB_STATES` AND `updated_at < now - retention`인 data_jobs를
  하드 삭제. 신규 repo 메서드 `delete_data_jobs_older_than(...)`/`delete_data_job(job_id)` 추가.
- 연관 레코드(plan/run/results/state_transitions, request) 동시 삭제 여부는 정책 결정 필요
  (아래 노트). 최소 범위로는 `data_jobs` 행만 지우고 request 이력은 보존.
- (선택) 수동 트리거 `POST /api/v1/data-management/jobs:purge?older_than_seconds=...`로
  운영자가 즉시 보존 스윕을 돌릴 수 있게.

### State-machine / migration / compat
- (3a)는 **migration 불필요**(`updated_at` 재사용). 동작은 "노출 억제"뿐이라 위험 낮음.
- (3b)는 스키마 변경은 없으나 **FK 정합성** 주의: `data_jobs.request_id → requests`
  (`migrations.py:190`). data_jobs만 지우고 request를 남기면 고아 request 발생 가능.
  → 권장: 보존 스윕은 data_jobs 행만 제거하되, request는 별도(혹은 동일) 보존 정책으로 다루고,
  하드 삭제 대신 **연쇄 삭제(plan/run/results 포함) 또는 보존**을 명시적으로 택일.
- OperationKind 신설 불필요(잡 실행이 아니라 GC).
- per-job DELETE와의 관계: per-job DELETE = 운영자 수동 개별 정리, 보존 정책 = 전역 자동 상한.
  둘 다 같은 `delete_data_job(s)` repo 경로를 공유하면 구현 중복이 없다.

### Priority: **Medium**(단일 항목 폭증 시 **Medium-High**)
per-job DELETE가 들어오면 포탈에서는 수동 정리가 가능해지지만, **CLI 등 다른 소비자**의 목록은
여전히 무한 증가한다. 전역 상한(에이징/purge)이 있어야 신호 대 잡음비가 유지된다.

---

## GAP 4 (선택) — 영속화된 네이티브 acknowledge/dismiss를 DMS가 제공할 것인가

### 배경
포탈은 지금 **포탈 측 dismiss 레이어**를 추가하는 중이다(포탈 DB에 "이 항목 숨김" 저장).
질문: DMS가 대신 **1급(first-class) acknowledge**(영속화 + 모든 소비자 공유)를 제공해야 하는가?

### 핵심 난점 — 항목에 안정적 ID가 없다
0절에서 보았듯 `action_required`는 매 호출 재계산되며, 항목에 **영속 식별자가 없다**.
ack를 영속화하려면 먼저 **결정적 issue key**가 필요하다. 다행히 각 항목은 이미
`issue_type` + 자원 식별자(`resource_key`/`storage_name`/`job_id`/`report_id`/`request_id`)를
들고 있으므로, 예: `issue_key = sha256(issue_type + ":" + primary_identity)`로 합성 가능.

### 제안(채택 시)
```
POST   /api/v1/operations/action-required/{issue_key}:acknowledge
       body: { "reason": "...", "snooze_seconds": <optional> }
DELETE /api/v1/operations/action-required/{issue_key}:acknowledge   # un-ack
```
- 신규 테이블 `action_acknowledgements(issue_key PK, issue_type, identity, acknowledged_by,
  acknowledged_at, snooze_until, reason, signature)` — **migration 필요**.
- `action_required()`가 ack 테이블과 조인해 ack/snooze된 항목을 필터(또는 `acknowledged: true`로
  표시만).
- **자동 해제(re-surface) 의미론**: 항목의 `signature`(예: 관련 reason/desired-vs-live 해시)가
  바뀌면 ack를 무효화해 다시 노출 → "고쳤다 착각한 채 영구 숨김" 방지.

### 트레이드오프 / 권고
- **포탈 측 dismiss (현재 진행)**: 빠르고 백엔드 무변경. 단, **소비자별 국소적**이라 CLI 등
  다른 소비자와 공유 안 됨. 또 근본 상태가 사라졌다가 다른 형태로 재발하면 dismiss가 어긋남.
- **DMS 네이티브 ack**: 모든 소비자 공유 + 일관 감사. 단, 안정적 issue key/서명 설계, 신규 테이블,
  재노출 의미론까지 복잡도 상승.
- **권고:** 단기는 포탈 측 dismiss로 충분. **여러 소비자가 ack 공유를 실제로 요구**하게 되면
  그때 DMS 네이티브 ack로 승격. 단, GAP 1~3는 ack로 가리는 게 아니라 **근본 상태를 실제로
  은퇴시키는 것**이 옳다(ack는 "신호 노이즈 억제", GAP 1~3은 "막다른 길 자체 제거" — 목적이 다름).

### Priority: **Low / Optional**

---

## 부록 — 검증된 코드 레퍼런스 요약

| 사실 | 위치 |
|---|---|
| `action_required()` 라이브 재계산 | `src/dms/query.py:43-125` |
| `request_attention` 소스(상태 6종) | `src/dms/repositories/operational.py:221-241` (상태 `:222-229`) |
| `diagnostic_events` 스키마(ack 컬럼 없음) | `src/dms/migrations.py:245-256` |
| `:resolve` 엔드포인트 / `RESOLVABLE_STATES`(2종만) | `src/dms/api/routers/resource_management.py:572-638` (`:585-588`) |
| `LifecycleState` / `TERMINAL_LIFECYCLE_STATES` | `src/dms/domain.py:14-49` |
| `kubernetes_quota_missing` 권고 문구 | `src/dms/query.py:1218-1219` |
| `resources` 테이블(삭제 메서드 없음) | `src/dms/migrations.py:95-106`, `src/dms/repositories/resources.py:23-324` |
| `_data_management_action_required()`(시간 윈도우 없음) | `src/dms/query.py:665-708` |
| `list_data_jobs`(에이징 필터 없음) | `src/dms/repositories/data_jobs.py:210-252` (`:247-248`) |
| `TERMINAL_DATA_JOB_STATES` | `src/dms/domain.py:70-80` |
| data-management 라우터(`:confirm`/`:cancel`만) | `src/dms/api/routers/data_management.py:191,214` |
| `OperationKind` 목록 | `src/dms/domain.py:96-122` |
