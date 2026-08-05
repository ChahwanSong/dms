# DMS 포탈 — 슬라이스 6 (잡 제출 표면 완성: rm·scan 제출 + 스토리지 선택 + 특권 제출) 설계

2026-08-05. Phase 4 포탈의 여섯 번째 슬라이스. 상위 스펙 `2026-08-02-dms-clean-slate-design.md`
§8(포탈)·§5(preview→confirm, 실행 신원)의 하위 구현 문서. 슬라이스 1~5는 구현·실증·배포 완료.
충돌 시 상위 스펙이 이긴다.

## 0. 배경 & 범위

백엔드는 sync·rm·scan 3종 연산과 `owner_username` 특권 게이트를 모두 처리하는데
(`routes_requests.py:44-58`, `:66-75`) **포탈은 sync 하나만 제출한다.** rm은 포탈에서 아예
불가능하고, 관리자 scan 실행 화면도 없다.

게다가 `SubmitSync.tsx:26-29`가 스토리지명을 **자유 텍스트**로 받는다. 오타 한 번이면
`storage_missing`으로 거부되는데, 사용자용 스토리지 목록 API가 없어서(`routes_storages.py:6`이
라우터 전체에 `require_admin`) 드롭다운을 만들 수 없다.

### 0.1 담는 것

- **사용자용 스토리지 목록 API**(백엔드 신규) — `GET /api/user/storages`. 활성 스토리지만,
  **경로를 노출하지 않는** 최소 필드.
- **제출 화면 통합**(프론트) — 연산 선택(sync/rm) + 스토리지 드롭다운 + 연산별 필드·옵션.
- **rm 제출**(프론트) — `recursive` 필수 체크 + 파괴적 연산 경고. preview→confirm 게이트는
  기존 `ConfirmDialog`가 그대로 처리한다(백엔드가 이미 sync와 동일하게 태운다).
- **관리자 scan 실행 화면**(프론트) — storage·target·top_k 지정 제출. scan은 confirm 게이트가
  없다(`stepper.py:105`).
- **특권 제출 필드**(프론트) — `owner_username`을 **관리자에게만** 노출, `403
  privileged_not_authorized` 인라인 표시.

### 0.2 비목표

- 정책의 `default_priority`를 제출 기본값으로 적용하는 것(`routes_requests.py:21`이 `"mid"`
  하드코딩) — 별도 후속. 이번엔 사용자가 고른 값을 그대로 보낸다.
- 고급 sync 옵션(`chmod`·`chown`·`bufsize`·`batch_files`) — allowlist에는 있으나 폼에 노출하지
  않는다. 운영자가 쓰는 빈도 대비 UI 복잡도가 크다. 필요해지면 후속에서 "고급" 섹션으로.
- `user_scan_paths`(사용자 scan 경로 등록 → 서브트리 통계 조회) — 스펙 §8의 사용자 scan 모델
  전체는 별도 슬라이스.
- 배치 생성 폼의 스토리지 드롭다운 — 이 API를 재사용할 수 있으나 이번 범위 밖.

## 1. 화면 지도

| 화면 | 경로 | 변경 |
|---|---|---|
| 작업 제출 | `/jobs/new`(확장) | 연산 선택(sync/rm) + 드롭다운 + 연산별 옵션 + 특권 필드 |
| scan 실행 | `/admin/scan`(신규) | 관리자 전용 scan 제출 |

`/jobs/new`는 기존대로 로그인 사용자면 접근 가능하고, `/admin/scan`은 `RequireRole role="admin"`.

## 2. 백엔드 (신규는 사용자용 스토리지 목록 1건)

### 2.1 `GET /api/user/storages`

```
GET /api/user/storages -> [{storage_name, backend_type, status}]
```

- `require_user`(admin 포함 누구나). **활성(`enabled = 1`) 항목만** 반환한다.
- **경로를 노출하지 않는다** — `mount_path`·`managed_root`는 내부 배치 정보이고 드롭다운에
  필요 없다. `status_detail`(`ready_nodes=2/5` 같은 운영 내부 정보)도 제외한다.
- `status`는 포함한다 — 사용자가 Degraded 스토리지를 고르면 planner가 거부할 수 있으므로 미리
  보이는 편이 낫다. 선택 자체는 막지 않는다(어드미션은 planner의 몫, fail-closed).
- 정렬은 `storage_name` 오름차순(결정적).
- 새 라우트 모듈을 만들지 않고 **`routes_storages.py`에 추가**하되, 그 파일의 라우터는 전체가
  `require_admin`이므로 **별도 라우터 객체**(`user_router`)를 같은 파일에 두고 `app.py`에서 함께
  등록한다. admin 라우터의 의존성은 건드리지 않는다.

`StoragesRepository`에는 이미 목록 조회가 있으므로 재사용한다 — 리포지토리는 수정하지 않고
라우트에서 필드를 골라 담는다.

### 2.2 기존 그대로

`POST /api/user/requests`의 검증·특권 게이트·payload 생성, `routes_storages.py`의 admin CRUD,
도메인 검증(`validate_rm_target`·`validate_options`)은 **변경하지 않는다**. 프론트가 백엔드
계약에 맞추는 슬라이스다.

## 3. 프론트엔드

### 훅

- `features/storages/useUserStorages.ts` — `useUserStorages()`(`GET /api/user/storages`,
  쿼리 키 `["user-storages"]`).
- `features/jobs/useJobs.ts` 확장 — 기존 `useSubmitSync`를 **일반화**한
  `useSubmitRequest()`로 대체한다. 바디는 `{operation, ...연산별 필드, options, priority,
  owner_username?}`. 기존 호출부(`SubmitSync`)는 새 화면으로 대체되므로 남는 참조가 없다.

### 화면

- **SubmitJob**(`/jobs/new`, 기존 `SubmitSync` 대체)
  - 연산 선택: `sync` | `rm` (라디오 또는 select). 기본 `sync`.
  - **sync**: 소스 스토리지(드롭다운)·소스 경로, 목적지 스토리지(드롭다운)·목적지 경로,
    옵션 체크박스 `delete`·`contents`·`direct`·`quiet`.
  - **rm**: 스토리지(드롭다운)·대상 경로, 옵션 `recursive`(**필수, 기본 체크·해제 가능하나 해제
    시 제출 버튼 비활성**)·`stat`·`lite`·`quiet`. `stat`과 `lite`는 상호배타이므로 둘 다 체크되면
    제출을 막고 안내한다(백엔드도 422로 막지만 왕복을 줄인다).
  - **rm 경고**: 폼 상단에 파괴적 연산임을 명시하는 눈에 띄는 배너 — "삭제는 되돌릴 수 없습니다.
    미리보기에서 대상을 확인한 뒤 확인해야 실행됩니다." (preview→confirm이 실제 안전장치임을
    사용자에게 알린다.)
  - 우선순위 select(low/mid/high).
  - **특권 필드**: `me.role === "admin"`일 때만 "다른 사용자로 실행(owner_username)" 입력을
    노출한다. 비우면 보내지 않는다.
  - 제출 성공 시 기존과 같이 `/jobs/{request_id}`로 이동.
  - 스토리지 목록 로딩 중에는 드롭다운을 비활성화하고 "불러오는 중…", 에러면 메시지.
- **SubmitScan**(`/admin/scan`, 신규, admin 전용)
  - 스토리지(드롭다운)·대상 경로·`top_k`(숫자, 비우면 미전송)·`verbose`/`quiet`(상호배타),
    우선순위, 특권 필드.
  - scan은 confirm 게이트가 없으므로 제출 즉시 실행 경로에 오른다는 점을 한 줄로 안내한다.

### 배선/타입

- `router.tsx`: `/jobs/new` → `SubmitJob`, `/admin/scan` 신규(RequireRole admin).
- `AppShell.tsx`: admin 내비에 "scan 실행"(`/admin/scan`) 추가.
- `lib/types.ts`: `UserStorage {storage_name, backend_type, status}` 추가.
- `lib/api.ts` reason_code 맵 추가: `rm_recursive_required`("삭제는 재귀 옵션이 필요합니다"),
  `rm_root_forbidden`("관리 루트 자체는 삭제할 수 없습니다"), `unsafe_path`("경로가
  올바르지 않습니다"), `unknown_option`·`invalid_option`("옵션 값이 올바르지 않습니다"),
  `storage_missing`·`storage_disabled`·`storage_not_ready`, `missing_storage`·
  `missing_source_storage`·`missing_destination_storage`, `sync_destination_inside_source`,
  `invalid_owner_username`.

## 4. 테스트

- **백엔드(pytest)**: `GET /api/user/storages`가 비관리자 세션에 200을 주고 **경로 필드를 포함하지
  않음**(`mount_path`/`managed_root`/`status_detail` 부재를 명시 단언), 비활성 스토리지 제외,
  이름 오름차순, 비로그인 401. admin 라우터의 기존 동작 불변(회귀).
- **프론트(vitest+MSW)**: 연산 전환 시 필드가 바뀐다; sync 제출 바디; rm 제출 바디에
  `options.recursive: true`; `recursive` 해제 시 제출 비활성; `stat`+`lite` 동시 선택 시 제출
  차단; 드롭다운이 API 목록으로 채워진다; 특권 필드가 비관리자에게 **보이지 않고** 관리자에게
  보인다; `403 privileged_not_authorized` 인라인; scan 화면 제출 바디(top_k 포함/미포함).

## 5. 배포/실증 (구현 후)

마이그레이션 변경 없음 → migrate Job 재실행 불필요. 컨트롤러 변경도 없다(프론트 + API 라우트
1건) → **dms-api만 재배포**하면 되지만, 이미지가 하나이므로 관례대로 d15로 올리고 api·controller
둘 다 갱신한다.

실증: 포탈에서 스토리지 드롭다운이 실제 목록으로 채워지는지 → sync 제출이 기존과 동일하게
동작 → **rm 제출이 preview→confirm을 거쳐 실행**되는지 → 관리자 scan 실행 → 비관리자에게 특권
필드가 안 보이는지 → `GET /api/user/storages` 응답에 경로가 없는지.

## 6. 결정 기록

- 사용자용 스토리지 목록은 **경로를 노출하지 않는다**(`storage_name`·`backend_type`·`status`만).
- 활성 스토리지만 반환하되 **Degraded도 포함**한다 — 어드미션 판단은 planner의 몫.
- scan 제출은 **관리자 전용**(스펙 §8: 관리자가 scan 실행, 사용자는 등록 경로의 통계 조회).
- rm의 `recursive`는 폼에서 **필수**로 강제하고, 상호배타 옵션은 제출 전에 막는다.
- 고급 sync 옵션(chmod/chown/bufsize/batch_files)은 이번 폼에 넣지 않는다.
