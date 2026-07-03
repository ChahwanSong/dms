# 데이터 Sync 탭 설계 (Portal operator)

작성: 2026-07-03 · 상태: 승인됨(구현 착수)

## 목적 / 범위

운영자 콘솔에 **데이터 Sync** 탭 추가. **단발성**(one-shot) `data.sync` 복사 작업을
작성·추적한다. 데이터 백업(배치·재실행·오케스트레이션)과 달리 **잡이 곧 최상위 단위**이며
**배치·재실행이 없다**. DMS 백엔드는 수정하지 않는다(순수 DMS API 클라이언트).

- 상단 = 요청 작성 폼(소스 → 목적지 1:1).
- 하단 = 요청된 sync 작업 리스트(정보/상태/액션), **무한 스크롤**.
- 뮤테이션이므로 DMS의 **preview → 승인(confirm) → 실행** 흐름을 반영.

명칭 확정: 최초 "데이터 이동"으로 논의했으나, DMS엔 전용 move가 없고 sync(복사)/rm(삭제)만
있어 **복사 전용**으로 결정 → 탭 이름을 **"데이터 Sync"** 로 정정. (원본 삭제는 옵션으로 제공;
아래 참조.)

## 결정 사항

1. **연산**: `data.sync`(dsync/nsync). DMS 전용 move 없음.
2. **옵션**: 백업과 **동일한 전체 옵션 세트**를 선택 가능하게 제공 — 백업의 `SyncOptionsFields`
   (그룹 `sync`: contents/direct/open_noatime/quiet/batch_files/bufsize, 그룹 `ownership`:
   chmod/chown) + **`delete_enabled` 체크박스**(`--delete`). 즉 조작자가 요청별로 원본 삭제까지
   선택 가능. (하드코딩 "복사 전용" 아님.)
3. **구동 방식(B안)**: 경량 **백그라운드 오케스트레이터 루프**(백업/스캔 패턴 일치, 단일 writer,
   크래시 내성). on-read 방식(A안)이 아니라 루프가 상태를 전진시킨다.
4. **목록**: **무한 스크롤**(offset 페이징 + IntersectionObserver). 액티비티 요청(전체)에 구현한
   패턴 재사용. 포탈 DB 기반이라 페이지드 DB 읽기로 깔끔.

## 아키텍처

### 백엔드 (`src/portal/backend/`)
- **DB** (`db.py` DDL, 스키마 `portal`): 테이블 `sync_jobs`
  - `id (uuid/serial pk)`, `dms_job_id`, `dms_request_id`, `requester_id`, `owner_username`,
    `src_storage`, `src_path`, `dst_storage`, `dst_path`, `options (jsonb)`, `delete_enabled (bool)`,
    `memo`, `submitted_by`, `state`, `preview_summary (jsonb)`, `preview_fingerprint`, `reason`,
    `created_at`, `updated_at`.
  - 메서드: `create_sync_job(...)`, `list_sync_jobs(limit, offset)`(최신순), `count_sync_jobs()`,
    `get_sync_job(id)`, `set_sync_job_state(...)`, `approve_sync_job(id)`, `sync_jobs_in_states(...)`,
    `delete_sync_job(id)`.
- **오케스트레이터** (`sync_orchestrator.py` 신규, 스캔 루프 패턴): 단일 asyncio 루프.
  - `preview_pending`(제출됨) → `get_sync_job` 폴링 → ConfirmPending면 프리뷰 요약·fingerprint
    캡처 → `preview_ready`; 프리뷰 실패면 `preview_failed`.
  - `preview_ready` + `approved` → `confirm_job(fingerprint)` → `running`.
  - `running` → 폴링 → `succeeded`/`failed`.
  - 프리뷰 타임아웃 백스톱(`backup_preview_timeout_seconds` 재사용).
  - actor = `mtls:<operator>`(백업/스캔 동일).
- **라우터** (`routers/syncjob.py` 신규, prefix `/api/operator/sync-jobs`, operator 게이트):
  - `POST /` 작성 = `submit_sync` 후 `sync_jobs` 행 저장(`preview_pending`+dms ids).
  - `GET /?offset&limit` 목록(최신순, `{items, total}` — 무한 스크롤).
  - `GET /{id}` 단건.
  - `POST /{id}:approve` 승인 플래그(루프가 confirm).
  - `POST /{id}:cancel` = `cancel_job` + 상태.
  - `DELETE /{id}` = terminal이면 `delete_data_job` + 행 삭제.
  - `GET /{id}/logs` = `get_data_job_logs` 프록시.
- **DMS 클라이언트**: 기존 `submit_sync`/`get_sync_job`/`confirm_job`/`cancel_job`/
  `delete_data_job`/`get_data_job_logs` **재사용**(신규 메서드 0).
- **app.py**: 오케스트레이터 start/stop 배선 + 라우터 등록(백업/스캔과 동일 패턴).

### 프론트 (`src/portal/frontend/src/interfaces/operator/sync/` 신규)
- **SyncJobForm** (상단): 소스(스토리지+경로)·목적지(스토리지+경로)·요청자/소유자·메모 +
  **백업과 동일한 옵션 UI**(`SyncOptionsFields` 두 그룹 재사용 + `delete_enabled` 체크박스) +
  "Sync 요청" 버튼. 스토리지 목록은 백업 폼과 동일 소스(DMS storage mappings)에서.
- **SyncJobList** (하단, **무한 스크롤**): 행별 대상(src → dst)·소유자·상태 pill(프리뷰중/확인대기/
  실행중/성공/실패/취소)·프리뷰 요약(파일수·용량, ready 시)·시각·액션. **재실행 없음**.
  액션: `preview_ready`=승인/취소, `running`=취소, terminal=상세·로그/삭제. IntersectionObserver
  센티넬로 다음 페이지 append + 진행 중이면 주기 폴링으로 상태 갱신.
- **재사용**: `backup/SyncOptionsFields`, `JobDetailModal`(상세/로그), 경로/friendlyError 헬퍼.
- **배선**: `api.ts`에 `operatorApi.sync.*`, `OperatorApp`에 nav `{key:"sync", label:"데이터 Sync"}`.

## 데이터 흐름

작성 → `submit_sync`(DMS 프리뷰) → 루프가 `preview_ready`(+요약/fingerprint) → 조작자 **승인** →
루프가 `confirm_job` → DMS 실제 sync 실행 → 루프 폴링 → 완료. 프론트는 진행 중일 때 수 초 폴링.

## 에러 / 경계

- 경로 규칙: managed_root 상대·목적지≠소스·**목적지 부모 존재 필수**(DMS 프리뷰가 검증 → BFF가
  4xx 전달, friendlyError 매핑).
- 프리뷰 타임아웃 백스톱으로 멈춘 프리뷰 방지.
- DMS 오류는 `DmsApiError` → `HTTPException`으로 전달(상태/detail 그대로).

## 테스트

- 백엔드 pytest: 오케스트레이터(preview→ready·승인→confirm→terminal·cancel·타임아웃),
  라우터(작성/목록offset/승인/취소/삭제/로그 인증 게이트), db 메서드. 페이크 DMS 클라이언트 +
  `test_portal_scan.py`/`test_portal_backup_*` 패턴.
- 프론트 tsc/build.
- 라이브 E2E(테스트베드 cephfs): 실 sync 작성 → preview → 승인 → 완료, 무한 스크롤 페이징.

## 비목표 (v1)

- 재실행/배치(백업이 담당).
- CSV 대량 입력(백업이 담당; 필요 시 후속).
