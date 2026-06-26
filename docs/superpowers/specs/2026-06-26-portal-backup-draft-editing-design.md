# Portal 데이터 백업 — Draft 배치 편집 (Phase 1) 설계

- 날짜: 2026-06-26
- 상태: 설계 승인 대기 (구현 전)
- 범위: DMS Portal operator "데이터 백업" 탭, **Phase 1만**
- 작성 배경: 현재 백업 탭은 배치를 한 번 생성하면 수정할 수 없고(요청 추가 API는 있으나 UI 없음), 승인은 배치 전체 일괄이며, sync 옵션 UI가 없다. 운영자가 배치를 교정/큐레이션할 수 있도록 단계적으로 확장한다.

## 1. 목표 / 비목표

**목표 (Phase 1 — `draft` 상태 배치 편집):**
1. 배치 메타 수정: `name`, `note`, `delete_enabled`.
2. 배치 전체 sync 옵션 편집 UI (생성·수정 양쪽).
3. 요청 추가 UI (기존 `addRequests` API 연결).
4. 개별 요청 경로(src/dst storage·path) 인라인 수정.
5. 개별 요청 행 삭제.

**비목표 (Phase 2/3로 분리):**
- 비-`draft` 배치 편집, 재미리보기, fingerprint 무효화.
- 항목별 상세 미리보기, 선택 승인, 단계적 승인.
- 실패 항목 재시도.

## 2. 제약 / 불변 원칙

- **DMS 백엔드(`src/dms/`) 무수정.** 포탈(BFF + 포탈 DB + SPA)만 변경. (CLAUDE.md 포탈 작업 규칙)
- 모든 편집은 **배치 `status == "draft"` 에서만** 허용. 비-draft는 `409`. draft에선 모든 요청이 `registered` 상태이므로 요청 단위 상태 분기가 불필요하다.
- `options`는 포탈 DB `backup_batches.options(jsonb)`에 저장되고 이미 `orchestrator.sync_body()`를 통해 DMS `POST /sync` 본문으로 전달된다 → 신규 배관 불필요, UI만 추가.
- `delete`는 현행 그대로 `delete_enabled` 컬럼/체크박스로 처리하고 `options` 에디터에서는 **제외**(중복/충돌 방지). `sync_body()`가 `delete_enabled`일 때 `options["delete"]=True`를 주입.
- 라이브 폴링(`BackupBatchDetail`)은 `previewing`/`running`에서만 동작 → draft 편집 화면과 충돌 없음.

## 3. 백엔드 (BFF) — `src/portal/backend/routers/backup.py`

### 신규 엔드포인트
| 메서드 | 경로 | 동작 | 상태코드 |
|---|---|---|---|
| `PATCH` | `/batches/{batch_id}` | 메타 부분수정 `{name?, note?, delete_enabled?, options?}` | 200 / 404 / 409 / 422 |
| `PATCH` | `/batches/{batch_id}/requests/{request_id}` | 요청 경로 수정 `{src_storage, src_path, dst_storage, dst_path}` | 200 / 404 / 409 / 422 |
| `DELETE` | `/batches/{batch_id}/requests/{request_id}` | 요청 행 삭제 | 200 / 404 / 409 |
| `POST` | `/batches/{batch_id}/requests` *(기존)* | CSV 일괄 추가 — UI만 연결 | 이미 draft-gated |

### 동작 규칙
- **공통 가드**: `get_batch` → 없으면 `404 batch_not_found`; `status != "draft"` → `409 batch_not_editable`(메시지에 현재 상태 포함).
- **`PATCH /batches/{id}`**
  - 새 Pydantic 모델 `BatchUpdate`: 전 필드 Optional. `name` 제공 시 `strip()` 후 빈값이면 `422`. `delete_enabled` bool.
  - 부분 업데이트: **제공된 필드만** 변경(미제공 필드는 불변). `null`을 "비우기"로 쓰지 않는다.
  - `options`는 **전체 교체 시맨틱**: 제공되면 기존 `options` 객체를 통째로 대체(UI가 큐레이트된 전체 옵션 객체를 전송). 키 검증은 DMS preview가 권위(UI가 큐레이트 필드만 생성하므로 미지의 키는 원천 차단).
- **`PATCH /batches/{id}/requests/{rid}`**
  - 본문은 기존 `BackupRequestIn`. `_normalize_requests([body])` 재사용 → `_clean_rel`(앞뒤 `/` 제거, `..`·널바이트 거부), storage 빈값 거부. 위반 시 `422`.
  - `get_request(rid)`로 존재 + `batch_id` 소유 확인(불일치/없음 → `404 request_not_found`).
- **`DELETE /batches/{id}/requests/{rid}`**
  - 소유 확인 후 삭제. 없으면 `404`.
- 에러는 기존 패턴대로 `HTTPException(status, detail)`; 프론트 `errMsg`가 409/422/503 매핑.

## 4. 포탈 DB — `src/portal/backend/db.py`

### 신규 메서드
- `update_batch(batch_id, *, name=None, note=None, delete_enabled=None, options=None) -> None`
  - 화이트리스트 컬럼만 동적 `SET`(제공된 것만), `options`→`Jsonb`, `updated_at=now()`. (값 미제공 = 변경 없음. `None`을 "비우기"로 쓰지 않음 — 부분수정 시맨틱.)
- `get_request(request_id) -> dict | None`
  - 소유(batch_id)·상태 확인용 단건 조회.
- `delete_request(batch_id, request_id) -> bool`
  - `WHERE id=%s AND batch_id=%s` 삭제, 영향행으로 성공 여부.
- 경로 수정은 기존 `update_request(request_id, src_storage=…, src_path=…, dst_storage=…, dst_path=…)` 재사용(plain 컬럼).

## 5. 프론트엔드 — `src/portal/frontend/`

### `src/api.ts` (`operatorApi.backup`)
- 신규 타입 `BatchUpdateInput { name?; note?; delete_enabled?; options?: Record<string, unknown> }`.
- `BackupBatch`에 `options?: Record<string, unknown>` 노출 확인(편집 프리필용).
- 신규 메서드:
  - `updateBatch(id, payload: BatchUpdateInput)` → `PATCH ${BK}/{id}`
  - `updateRequest(id, rid, req: BackupRequestInput)` → `PATCH ${BK}/{id}/requests/{rid}`
  - `deleteRequest(id, rid)` → `DELETE ${BK}/{id}/requests/{rid}`
  - (`addRequests` 기존)

### `BackupBatchForm.tsx` — create/edit 공용화
- props 확장: `mode: "create" | "edit"`, `initial?: BackupBatch`, `onSaved?`. (선례: `StorageMappingForm`)
- **sync 옵션 서브폼**(create·edit 공통): 접이식 "▸ sync 옵션".
  - 공통: `chmod`(text, placeholder `0750` / `D0750,F0640`), `chown`(text, `user:group`, ⚠️ "소유권 보존 무력화" 경고), `contents`(checkbox).
  - 고급(접기): `batch_files`(int), `bufsize`(int), `direct`/`open_noatime`/`quiet`(checkbox).
  - `delete`는 옵션 서브폼에 없음(별도 `--delete` 체크박스 유지). 프리필 시 기존 `options`에 `delete` 키가 있으면 무시.
  - chmod/chown은 DMS 정규식을 가볍게 미러해 즉시 피드백(권위 검증은 preview의 422).
- edit 모드: name/note/delete_enabled/options 프리필. 저장 = `updateBatch`(메타+옵션만). create 모드: 기존 동작 + options 포함해 생성.

### `BackupBatchDetail.tsx` — draft 전용 편집 surface
- `status === "draft"` 일 때만 노출:
  - 헤더: `[✎ 편집]`(→ `BackupBatchForm` edit 모달) · `[+ 요청]`(→ CSV 추가 모달, `addRequests`)
  - 각 요청 행: `[✎]`(경로 4필드 미니 모달 → `updateRequest`) · `[🗑]`(confirm → `deleteRequest`)
- 비-draft: 현행 읽기전용 모니터링 그대로(편집 컨트롤 미노출).
- **CSV 추가와 메타 저장 분리**: `[+ 요청]`은 독립 액션(즉시 `addRequests` + 새로고침), `[저장]`은 메타/옵션만 커밋 — 두 API 호출이 묶여 부분실패가 지저분해지는 것을 방지.
- 액션 후 `reload()`로 목록/카운트 갱신(기존 `act()` 헬퍼 패턴 재사용).

## 6. 검증 / 에러 처리
- 서버: `422`(경로 정규화 위반), `409`(비-draft 편집), `404`(배치/요청 없음).
- 프론트: 기존 `errMsg`(409/422/503 한국어 매핑) 재사용. 경로/옵션 입력은 제출 전 가벼운 클라이언트 검증.

## 7. 테스트 전략
포탈 전용 테스트 인프라가 없고 포탈 DB가 **Postgres 전용**(SQLite 폴백 없음)이므로:
- **백엔드 (신규 `tests/test_portal_backup_edit.py` 또는 포탈 테스트 모듈)**: 인메모리 **페이크 `Database`**(필요 메서드만 구현)로 라우터 동작 단위 검증 —
  - 경로 정규화 + `..`/널바이트 거부(`422`),
  - draft-only 가드(비-draft PATCH/DELETE → `409`),
  - 소유 불일치 요청(`404`),
  - 메타/옵션 PATCH 부분수정 라운드트립,
  - 요청 경로 수정·삭제.
- SQL 메서드(단순)는 코드리뷰로 커버; **Postgres 백킹 통합테스트는 후속 옵션**으로 명시.
- **프론트**: `npm run build`(tsc 타입체크) 통과 필수 + 테스트베드 수동 확인(draft 편집 → 미리보기 정상 진행).

## 8. 변경 파일 요약 (구현 체크리스트)
- `src/portal/backend/routers/backup.py` — `BatchUpdate` 모델 + 3 엔드포인트.
- `src/portal/backend/db.py` — `update_batch`, `get_request`, `delete_request`.
- `src/portal/frontend/src/api.ts` — `BatchUpdateInput` + 3 메서드, `BackupBatch.options`.
- `src/portal/frontend/src/interfaces/operator/backup/BackupBatchForm.tsx` — create/edit 공용 + sync 옵션 서브폼.
- `src/portal/frontend/src/interfaces/operator/backup/BackupBatchDetail.tsx` — draft 편집 컨트롤(헤더·행별), 경로 미니 모달.
- (선택) `.../backup/helpers.ts` — sync 옵션 필드 메타/검증 헬퍼.
- `tests/test_portal_backup_edit.py` — 페이크 DB 라우터 테스트.
- `install/6.dms-portal-data-backup.md` — 편집 기능/옵션 UI 문서 반영.

## 9. 후속 단계(맥락용, 본 스펙 범위 외)
- **Phase 2**: `preview_ready`(결정 대기)와 `approved`(실행 선택) 상태 분리 → 항목별 상세 + 선택/단계 승인. 오케스트레이터는 `approved`만 confirm.
- **Phase 3**: 미리보기/실패 항목 수정 시 해당 항목만 `registered`로 되돌려 재미리보기(fingerprint 무효화), 실패 일괄 재시도.
