# DMS 포탈 — 슬라이스 3 (스토리지 관리 + 감사 로그) 설계

2026-08-05. Phase 4 포탈의 세 번째 슬라이스. 상위 스펙 `2026-08-02-dms-clean-slate-design.md`
§8(포탈)의 하위 구현 문서. 슬라이스 1(일회성 sync)·2(배치성)는 구현·실증·배포 완료. 충돌 시 상위
스펙이 이긴다.

## 0. 배경 & 범위

현재 스토리지는 **읽기전용 목록**(슬라이스 1의 `/admin/storages`)이고, 등록은 seed-script로만 가능하다.
백엔드 CRUD API는 이미 존재한다: `GET/POST/PUT/DELETE /api/admin/storages`, `GET /api/admin/audit-log`.
이 슬라이스는 **운영자가 포탈에서 스토리지를 등록/수정/삭제/활성화**하는 UI를 붙이고, **감사 로그
화면**과 **사용중 삭제 가드**를 추가한다.

### 0.1 담는 것
- 스토리지 **CRUD** 프론트(등록·수정·활성/비활성 토글·삭제) — 기존 StoragesList 확장.
- **감사 로그 화면**(`/admin/audit`) — 스토리지 등록/수정/삭제 이력.
- **사용중 삭제 가드**(백엔드 신규) — 비종단 요청이 참조하는 스토리지 삭제 시 409 거부.

### 0.2 비목표
- 스토리지 status(Healthy/Degraded) 편집(리컨실러 소관, 읽기전용 표시).
- 이미지 빌드/롤아웃 UI, 정책/denylist 관리(별도 슬라이스).
- materialize 안 된 배치 Queued item이 참조하는 스토리지까지의 in-use 검사(엣지, §2 참조).

## 1. 화면 지도 (admin 트리)

| 화면 | 경로 | 내용 |
|---|---|---|
| 스토리지 관리 | `/admin/storages`(확장) | 목록 + "스토리지 등록" 버튼 + 행별 수정·활성토글·삭제 |
| 감사 로그 | `/admin/audit`(신규) | 스토리지 변경 이력(operation·target·actor·시각) |

- status(Healthy/Degraded/Unknown)는 읽기전용 StatusPill 표시.
- 삭제는 확인 다이얼로그를 거치고, 백엔드가 `409 storage_in_use`면 인라인 안내("사용 중 — 비활성화하세요").

## 2. 백엔드 (신규 = in-use 가드만; CRUD·audit-log API는 기존)

- **`RequestsRepository.active_referencing_storage(storage_name) -> bool`**(신규): 비종단 요청 중
  payload가 그 스토리지를 참조(scan/rm의 `storage`, sync의 `source_storage`/`destination_storage`)하는
  것이 있는지. 비종단 요청 수는 적으므로 로드 후 파이썬에서 판정.
- **`DELETE /api/admin/storages/{name}` 수정**: 삭제 전 `active_referencing_storage` 검사 → 참조 있으면
  `HTTPException(409, "storage_in_use")`; 없으면 기존 delete(감사로그 기록 유지).
- create/update/enable, audit-log 조회, `_validate`, `_audit`는 **기존 그대로**. `POST`는 `409
  storage_exists`/`422` 검증, `PUT`은 `404 storage_not_found`/`422`.
- **엣지(명시)**: materialize 전 배치 Queued item이 참조하는 스토리지는 아직 요청이 없어 이 검사에
  안 걸린다 — 드문 경우로 이번 슬라이스 범위 밖(비종단 요청 기준). 필요 시 후속에서 활성 배치 확장.

## 3. 프론트엔드 (슬라이스 1 C 디자인·컴포넌트·api 클라이언트·TanStack Query 재사용)

### 신규 훅
- `features/storages/useStorages.ts`(확장): 기존 `useStorages` + `useCreateStorage`·`useUpdateStorage`
  ·`useDeleteStorage`(mutation, 성공 시 `["storages"]` 무효화).
- `features/audit/useAudit.ts`: `useAuditLog()`(`GET /api/admin/audit-log`).

### 화면/컴포넌트
- **StoragesList**(확장): 상단 "스토리지 등록" 버튼 → `StorageDialog`(create). 행별 액션 —
  **수정**(StorageDialog edit), **활성/비활성 토글**(`useUpdateStorage`로 enabled 반전), **삭제**(확인
  다이얼로그 → `useDeleteStorage`). `409 storage_in_use`면 인라인 에러.
- **StorageDialog**(신규, Radix Dialog 폼): create=storage_name·mount_path·managed_root·backend_type;
  edit=storage_name 읽기전용 + mount_path·managed_root·backend_type·enabled. 제출→POST/PUT, 성공 시
  닫고 목록 무효화. `storage_exists`/검증 에러 인라인.
- **AuditLog**(신규, `/admin/audit`): 감사 이력 테이블. admin 전용.

### 배선/타입
- `router.tsx`: `/admin/audit` 라우트(RequireRole admin + AppShell).
- `AppShell.tsx`: admin 내비 "감사 로그"(`/admin/audit`) 추가.
- `lib/types.ts`: `Storage`에 `managed_root` 추가(슬라이스1 타입 누락분), `AuditEntry` 타입 추가.
- `lib/api.ts` reason_code 맵: `storage_exists`, `storage_in_use`("사용 중인 스토리지는 삭제할 수
  없습니다"), `storage_not_found` 추가.

## 4. 테스트

- **백엔드(pytest)**: `active_referencing_storage`(비종단 요청이 storage/source_storage/
  destination_storage 참조 → True; 종단만/무참조 → False), `DELETE` 409 storage_in_use(참조 시) +
  200(무참조 시), 기존 create/update/delete 테스트 green 유지.
- **프론트(vitest+MSW)**: StorageDialog create(POST body)·edit(PUT body), StoragesList 삭제(확인→
  DELETE)·409 storage_in_use 에러·활성 토글(PUT enabled), AuditLog 이력 렌더, router `/admin/audit`
  admin 접근.

## 5. 배포/실증 (구현 후)

dms 이미지 재빌드(d11) → dms-api 재배포(정적 SPA + 백엔드 in-use 가드). 컨트롤러/마이그레이션 변경
없음(스키마 불변, audit_log 테이블은 기존). 실증: 포탈에서 스토리지 등록→수정→활성토글→(참조 잡
있는 스토리지) 삭제 거부→(무참조) 삭제, 감사 로그 화면에 이력 표시.

## 6. 결정 기록
- 범위 = CRUD + 감사로그 뷰 + 사용중 삭제 가드(전체).
- 감사 로그 = **별도 `/admin/audit` 화면**.
- in-use 기준 = **비종단 요청 참조**(배치-Queued 엣지는 후속).
- 백엔드 신규 = in-use 가드 1건(active_referencing_storage + DELETE 409); 나머지는 프론트.
