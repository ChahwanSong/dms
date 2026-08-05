# DMS 포탈 — 슬라이스 4 (운영 제어 콘솔: 정책 · denylist · 컨트롤 상태) 설계

2026-08-05. Phase 4 포탈의 네 번째 슬라이스. 상위 스펙 `2026-08-02-dms-clean-slate-design.md`
§8(포탈)·§5(어드미션·타임아웃·denylist)의 하위 구현 문서. 슬라이스 1(일회성 sync)·2(배치성)
·3(스토리지 관리)은 구현·실증·배포 완료. 충돌 시 상위 스펙이 이긴다.

## 0. 배경 & 범위

슬라이스 3 설계 §0.2가 "정책/denylist 관리 → 별도 슬라이스"로 파킹한 항목이다. 상위 스펙
§8이 관리자 인터페이스로 지목한 것 중 **정책 · denylist · 컨트롤 상태(유지보수/드레인)** 셋이
포탈에 없다. 현재 운영 경로는 Bearer 토큰 curl(정책·denylist) 또는 **DB 직접 UPDATE**
(control_state — API 자체가 없음)뿐이다.

세 개를 하나의 "운영 제어" 콘솔로 묶는 이유: 셋 다 admin 전용 단일 리소스 편집이고, 셋 다
`ControlRepository`가 소유하며 audit_log에 기록된다. 화면·훅·테스트 패턴이 동일해 한 슬라이스로
자른다.

### 0.1 담는 것

- **정책 기본 시드**(백엔드 신규) — `migrations`가 4개 도구 정책 행을 멱등 시드. 지금은
  `policies`가 0행이라 신규 설치 시 모든 요청이 `missing_policy`로 Rejected된다
  (`placement.py:107`; 시드되는 건 `control_state` 싱글톤뿐 — `migrations.py:245-248`).
- **컨트롤 상태 API**(백엔드 신규) — `GET/PUT /api/admin/control-state`. 리포지토리
  `set_control_state`(`control.py:91-103`)는 이미 있고 라우트만 없다.
- **maintenance 소비처 정의**(백엔드 신규) — 현재 `drain`만 `stepper.py:26`에서 읽히고
  `maintenance`는 소비처가 0건이라 토글해도 아무 일도 없다.
- **denylist 평가 순서 교정**(백엔드 신규) — 스펙 §5는 "denylist가 최우선 kill-switch,
  privileged 경로보다 먼저 평가"라고 못박는데, `identity.py:45`의 1차 검사가 `groups=[]`라
  **group으로만 등재된 대상이 특권 경로로 통과**한다.
- **화면 3개**(프론트 신규) — `/admin/policies`, `/admin/denylist`, `/admin/control`.

### 0.2 비목표

- 정책의 `default_priority`를 제출 기본값으로 실제 적용하는 것(현재 `routes_requests.py:21`이
  `"mid"` 하드코딩) — 제출 표면을 손대는 슬라이스 6의 범위.
- phase별 타임아웃의 **집행**(activeDeadlineSeconds/TIMED_OUT 판정) — 슬라이스 7. 이번 슬라이스는
  정책 행에 **유효한 값이 존재하도록** 시드·편집만 보장한다.
- 계정 관리, 노드 대시보드, 빌드·릴리스(§7), 시계열 대시보드(§9) — 각각 별도 슬라이스.

## 1. 화면 지도 (admin 트리)

| 화면 | 경로 | 내용 |
|---|---|---|
| 정책 | `/admin/policies`(신규) | 도구 4행 테이블 + 행별 수정 다이얼로그 |
| denylist | `/admin/denylist`(신규) | 목록 + 추가(타입·대상·사유) + 해제 |
| 컨트롤 상태 | `/admin/control`(신규) | 유지보수·드레인 토글 + 사유, 현재 상태·변경자·변경시각 |

모두 admin 전용(`RequireRole role="admin"` + 백엔드 `require_admin`). 슬라이스 1의 C/Soft-SaaS
디자인·`Table`·`Dialog`·`Button`·TanStack Query 패턴을 그대로 재사용한다.

## 2. 백엔드

### 2.1 정책 기본 시드 (migrations)

`migrate()`의 control_state 시드 옆에, 4개 도구 행을 **멱등**(`WHERE NOT EXISTS`) 삽입한다.
기존 행이 있으면 절대 덮어쓰지 않는다 — 실증 환경 d11에는 이미 curl로 넣은 정책이 있고, 운영자
편집값을 마이그레이션이 되돌리면 안 된다.

기본값(스펙 §5 "phase별 타임아웃은 정책 행에서" 문단의 값 그대로):

| tool | max_nodes | procs_per_node | queue | default_priority | max_priority | preview_timeout | execution_timeout |
|---|---|---|---|---|---|---|---|
| scan | 4 | 8 | dms-data | mid | high | NULL | 3600 (1h) |
| dsync | 8 | 8 | dms-data | mid | high | 3600 (1h) | 259200 (3d) |
| nsync | 8 | 8 | dms-data | mid | high | 3600 (1h) | 259200 (3d) |
| rm | 4 | 8 | dms-data | mid | high | 1800 (30m) | 3600 (1h) |

- `preview_timeout_seconds`는 scan만 NULL(스펙상 scan은 preview 게이트가 없다 —
  `stepper.py:105`).
- `max_nodes`/`procs_per_node`는 스펙에 기본값 규정이 없으므로 테스트베드(워커 5노드) 기준의
  보수적 값을 쓰고, 운영자가 화면에서 조정한다. 이 값은 `placement.resolve_fanout`의 상한일 뿐
  요청이 그만큼 쓰는 것은 아니다.
- `updated_by`는 `"migration-seed"`로 기록해 운영자 편집분과 구분한다.

### 2.2 컨트롤 상태 API (routes_control.py 신규)

```
GET  /api/admin/control-state   -> {maintenance, drain, reason, changed_by, changed_at}
PUT  /api/admin/control-state   body {maintenance: bool, drain: bool, reason: str|null}
```

- `require_admin`, `ControlRepository.set_control_state(...)` 위임 — audit before/after는
  리포지토리가 이미 기록한다(`control.py:99-102`, mutation_class `control_state`).
- PUT은 갱신 후 현재 상태를 반환한다(정책 PUT과 동일 계약).
- `reason`은 자유 텍스트(선택). 유지보수·드레인을 켤 때 이유를 남기게 하는 것이 목적이다.

### 2.3 maintenance 소비처

`maintenance = 1`이면 **신규 요청·배치 제출을 거부**한다:

- `POST /api/user/requests` → `503 maintenance_mode`
- `POST /api/admin/batches` → `503 maintenance_mode`

이유: 이름 그대로 "유지보수 창"이고, `drain`(진행 중인 것을 더 전진시키지 않음)과 역할이
겹치지 않는다 — drain은 스텝 정지, maintenance는 유입 차단. 이미 진행 중인 잡은 건드리지 않는다.

**관리자도 동일하게 차단한다**(예외 없음). 콘솔에서 끄는 경로(`PUT /api/admin/control-state`)는
제출 경로가 아니므로 절대 잠기지 않는다 — 락아웃이 생기지 않는다.

maintenance가 막는 것은 위 두 제출 엔드포인트뿐이다. 이미 접수된 배치는 `BatchOrchestrator`가
설정된 동시성만큼 계속 항목을 물질화하고 stepper도 계속 파드를 제출한다 — 둘 다 `drain`만
확인하기 때문이다. 클러스터 작업을 완전히 멈추려면 maintenance와 drain을 함께 켜야 한다.

### 2.4 denylist 평가 순서 교정 (identity.py)

현재: `is_denied(..., groups=[])` → 특권 단축 반환 → LDAP 해석 → `is_denied(..., groups=실제)`.
따라서 **group으로만 등재된 특권 요청자는 차단되지 않는다** — 스펙 §5 위반.

교정: 1차 검사 **전에** group 규칙이 존재하는지 묻고, 존재할 때만 특권 경로에서도 그룹을
해석한다.

- `ControlRepository.has_group_denies() -> bool`(신규, 단순 count 쿼리).
- `resolve_job_identity`: group 규칙이 있으면 특권 단축 전에 `resolver.resolve(owner)`로 그룹을
  얻어 `is_denied`에 넘긴다. 해석 실패(`ldap_unavailable`)는 기존과 같이 fail-closed.
- group 규칙이 **없으면** 동작·비용이 지금과 완전히 동일하다(특권 경로는 LDAP 없이 통과).
  denylist에 group 항목이 하나도 없는 것이 일반적이므로 회귀 위험이 낮다.

### 2.5 기존 그대로

정책 CRUD(`routes_policies.py`), denylist CRUD(`routes_denylist.py`), `ControlRepository`의
정책·denylist 메서드, audit 기록은 **변경하지 않는다**. reason_code 계약도 유지:
`invalid_priority`(422), `policy_not_found`(404), `invalid_denylist_subject_type`(422),
`invalid_policy`(422).

## 3. 프론트엔드 (슬라이스 1·3 컴포넌트·api 클라이언트·TanStack Query 재사용)

### 훅

- `features/policies/usePolicies.ts` — `usePolicies()`(GET 목록), `useUpsertPolicy()`
  (PUT `/api/admin/policies/{tool}`, 성공 시 `["policies"]` 무효화).
- `features/denylist/useDenylist.ts` — `useDenylist()`, `useDeny()`(PUT), `useAllow()`(DELETE),
  각각 `["denylist"]` 무효화.
- `features/control/useControlState.ts` — `useControlState()`, `useSetControlState()`(PUT,
  `["control-state"]` 무효화).

### 화면

- **PoliciesList** — 도구·max_nodes·procs_per_node·queue·기본/최대 우선순위·preview/execution
  타임아웃·활성 컬럼. 행별 "수정" → `PolicyDialog`. 타임아웃은 초 그대로 두지 않고 사람이 읽는
  형식(`3600s (1h)`)을 병기한다.
- **PolicyDialog** — `tool`은 읽기 전용, 나머지 필드 편집. 우선순위는 low/mid/high 선택.
  `preview_timeout_seconds`는 비우면 NULL. `422 invalid_priority` 인라인 표시.
- **DenylistList** — subject_type·subject·사유 테이블, "추가" 버튼 → `DenyDialog`
  (타입 선택 requester/owner/group + 대상 + 사유), 행별 "해제"(확인 다이얼로그 → DELETE).
  백엔드가 subject를 소문자로 정규화하므로 화면도 소문자 표시임을 안내한다.
- **ControlState** — 유지보수/드레인 두 토글 + 사유 입력 + 저장. 현재 상태와 `changed_by`
  ·`changed_at` 표시. 켜져 있을 때 눈에 띄는 경고 배너(유지보수=신규 제출 차단, 드레인=진행 정지)
  로 부작용을 명시한다.

### 배선/타입

- `router.tsx`: `/admin/policies`, `/admin/denylist`, `/admin/control` (RequireRole admin).
- `AppShell.tsx`: admin 내비에 "정책", "denylist", "컨트롤 상태" 추가.
- `lib/types.ts`: `Policy`, `DenyEntry`, `ControlState` 추가.
- `lib/api.ts` reason_code 맵: `maintenance_mode`("유지보수 중입니다 — 새 작업 제출이 일시
  중단되었습니다"), `invalid_policy`, `invalid_denylist_subject_type`, `policy_not_found` 추가.

## 4. 테스트

- **백엔드(pytest)**: 시드 멱등성(migrate 2회 → 4행 유지, 기존 편집값 불변), 시드 값이 스펙
  기본값과 일치, 시드 후 `resolve_fanout`이 `missing_policy`를 던지지 않음; control-state
  GET/PUT + audit 기록; maintenance=1일 때 요청·배치 제출 503, 0이면 통과; drain은 제출을
  막지 않음; `has_group_denies` + group-only denylist가 특권 요청자를 차단(교정 전엔 통과했음을
  드러내는 테스트), group 규칙이 없으면 특권 경로가 LDAP 없이 동작.
- **프론트(vitest+MSW)**: PolicyDialog PUT body, 우선순위 422 표시, preview 타임아웃 빈값→null;
  DenylistList 추가(PUT)·해제(DELETE 확인); ControlState 토글 PUT body + 경고 배너; router
  세 경로 admin 접근.

## 5. 배포/실증 (구현 후)

마이그레이션 변경이 있으므로 **migrate Job 재실행 필요**(슬라이스 3과 다른 점). dms 이미지
재빌드(d12) → migrate Job 재실행 → dms-api·dms-controller 재배포.

실증: 포탈에서 정책 4행 확인(시드) → scan 정책 수정·반영 확인 → denylist에 requester 추가 후
그 사용자의 제출이 `identity_denied`로 막히는지 → 해제 후 통과 → 유지보수 켜고 제출이 503인지
→ 끄고 통과 → 드레인 켜고 stepper가 전진을 멈추는지 → 감사 로그에 policy/denylist/control_state
변경이 남는지.

## 6. 결정 기록

- 세 리소스(정책·denylist·컨트롤 상태)를 **한 슬라이스**로 묶는다 — 소유 리포지토리·권한·패턴이
  동일.
- 정책 시드는 **멱등, 기존 행 미변경**, `updated_by="migration-seed"`.
- `maintenance` = **신규 제출 차단**(admin 포함), `drain` = 기존 stepper 정지. 콘솔 자체는 차단 대상
  아님.
- denylist group 평가는 **group 규칙이 존재할 때만** 특권 경로에서 LDAP 그룹을 해석해 교정한다
  (규칙 없으면 기존 경로·비용 그대로).
- 타임아웃 **집행**은 이번 범위 밖(슬라이스 7) — 이번엔 값의 존재·편집만 보장.
