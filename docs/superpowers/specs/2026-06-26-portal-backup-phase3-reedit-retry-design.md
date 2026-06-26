# Portal 데이터 백업 — Phase 3: 미리보기 후 재편집 + 재시도 설계

- 날짜: 2026-06-26
- 상태: 설계 승인됨 (구현 진행)
- 범위: DMS Portal operator "데이터 백업" 탭, **Phase 3**
- 선행: Phase 1·2 구현·배포(v44).

## 1. 목표 / 비목표
**목표**
1. 미리보기/실패 항목의 **경로 재편집** → 재미리보기.
2. 실패 항목 **재시도**(개별·일괄) → 재미리보기 → (재)승인.

**비목표**: DMS 백엔드 변경 없음(포탈만). 오케스트레이터 로직 변경 없음.

## 2. 핵심 메커니즘 — reset → 재미리보기
항목을 **`registered`로 되돌리면(reset)** 오케스트레이터의 기존 미리보기 단계가 자연히 재제출한다. 따라서 **오케스트레이터는 무변경**.
```
preview_ready / preview_failed / failed / cancelled / registered ──(재편집 또는 reset)──▶ registered
draft / previewed / done ──(미리보기/재미리보기)──▶ previewing → … → previewed
```
재미리보기는 기존 `succeeded` 항목을 건드리지 않고 `registered`만 재제출한다.

## 3. 백엔드 — `routers/backup.py`
- **PATCH `/batches/{id}/requests/{rid}` 일반화** (Phase 1 수정): 가드를 "배치=draft"에서 **"항목 상태가 편집가능"**으로 변경. 편집가능 = `{registered, preview_ready, preview_failed, failed, cancelled}`. 인플라이트(`preview_pending, approved, running`)·`succeeded`는 `409`. 편집 시 경로 갱신 + 항목을 **`registered`로 리셋**(dms_job_id/dms_request_id/fingerprint/preview/result/error 클리어). draft(=registered) 케이스는 하위호환.
- **신규 `:reset`** `POST /batches/{id}/requests:reset` 본문 `{request_ids?: int[], failed_only?: bool}`:
  - `failed_only` → `failed`+`preview_failed`를 `registered`로.
  - 아니면 `request_ids`의 **편집가능 상태**인 것만 `registered`로(인플라이트/succeeded는 무시).
  - 둘 다 없으면 `422`. 배치가 `previewing`이면 `409`(미리보기 중 reset 금지).
  - 반환 `{reset: n}`.
- **`:preview` 완화**: 허용 상태에 `done` 추가 → `{draft, previewed, done}`. `previewing`이면 그대로 `409`. (registered 0건이면 기존대로 `422`.)
- DELETE는 draft 전용 유지(범위 밖).

## 4. DB — `db.py`
- `edit_request_paths(request_id, row)` — 경로 4필드 갱신 + `state='registered'` + 부수필드 NULL.
- `reset_requests(batch_id, *, request_ids=None, failed_only=False) -> int` — 대상(failed_only면 failed/preview_failed; 아니면 request_ids)을 편집가능 상태인 것만 `registered`로 + 부수필드 NULL. rowcount.

## 5. 오케스트레이터
**무변경.** preview 단계가 `registered`를 재제출(신규 dms job → 새 fingerprint).

## 6. 프론트엔드
- `api.ts`: `resetRequests(id, {request_ids?|failed_only?})`. (`updateRequest`/`preview` 기존 재사용.)
- `BackupBatchDetail.tsx`:
  - 항목 행: **`재편집`**(편집가능 상태 → `BackupRequestEdit` 재사용, 저장 시 PATCH→리셋) · **`재시도`**(failed/preview_failed → reset 단건).
  - 헤더: **`실패 재시도`**(failed류 있을 때 일괄 reset) · **`재미리보기`**(status∈{previewed,done} & registered>0 → `:preview`).
  - 리셋 후 안내: "N개를 재시도 대기로 되돌렸습니다. '재미리보기'를 누르세요."
- `BackupRequestEdit`는 그대로 사용(이미 PATCH 호출). draft의 `수정`도 동일 컴포넌트.

## 7. 테스트
- **신규 `tests/test_portal_backup_phase3.py`**: PATCH 일반화(편집가능 상태 200+리셋, 인플라이트/succeeded 409), `:reset`(failed_only·request_ids·둘다없음 422·previewing 409), `:preview` done 허용.
- **기존 `tests/test_portal_backup_edit.py` 갱신**: `test_patch_request_non_draft_409`를 "인플라이트(running) 항목 → 409"로 변경(배치-draft가 아니라 항목 상태 기반). FakeDB에 `edit_request_paths` + seed 상태 지정 추가.
- 프론트: `npm run build`(tsc).
- 테스트베드: v45 빌드/배포 + E2E(미리보기 실패 유도→재편집→재미리보기→성공, 실패 일괄 재시도).

## 8. 변경 파일
- `src/portal/backend/routers/backup.py` — PATCH 가드 일반화, `:reset`, `:preview` 완화.
- `src/portal/backend/db.py` — `edit_request_paths`, `reset_requests`.
- `src/portal/frontend/src/api.ts` — `resetRequests`.
- `.../backup/BackupBatchDetail.tsx` — 재편집/재시도/실패 재시도/재미리보기.
- `tests/test_portal_backup_phase3.py` (신규) + `tests/test_portal_backup_edit.py` (갱신).
- `install/6.dms-portal-data-backup.md` — 재편집/재시도 절 추가.
