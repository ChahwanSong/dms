# 데이터 백업 — 배치 상세 항목 관리 강화 (설계)

날짜: 2026-06-28 · 범위: 포탈(`src/portal/`)만, DMS 무관.

## 목표
각 배치 상세 탭에서 요청(항목)을 풍부하게 관리한다: 체크박스 선택+일괄 작업,
쉬운 행 추가/삭제, CSV 템플릿/다운로드/업로드(전체 교체), "재편집"→"편집" 라벨.

## 확정 결정 (사용자)
1. **편집 범위**: 진행 중(`previewing`/`running`)이 *아닌* 배치(draft·previewed·done·cancelled)에서
   항목 추가/삭제/편집/CSV 허용. 추가·편집 항목은 `registered`가 되어 재미리보기 필요.
   진행 중 배치는 항목 변경 차단(in-flight 보호).
2. **CSV 업로드 = 전체 교체(replace)**. 업로드 시 확인 다이얼로그(기존 N개 대체 경고).

## 기능
1. **선택 + 일괄 작업**: 모든 행 체크박스 + 헤더 전체선택. 선택 시 일괄 바 — 선택 항목의
   상태에 맞는 동작만 활성: **승인**(preview_ready) · **재시도**(preview_failed/failed/cancelled/preview_ready→registered) ·
   **삭제**(진행중 아닌 항목) · **취소**(in-flight 항목; running 중에도 허용).
2. **편집**: 기존 "재편집" 버튼 라벨을 "편집"으로. 동작 동일(경로 수정 → registered 리셋).
3. **행 추가**: "+ 행 추가" — 출발/대상 경로 입력으로 1건 append(registered).
4. **행 삭제**: 행별 "삭제" + 일괄 삭제.
5. **CSV**: 템플릿 다운로드 · 현재 항목 다운로드 · 업로드(전체 교체).

## 백엔드 (포탈 BFF, TDD)
라우트(`routers/backup.py`):
- `POST /batches/{id}/requests:add` — body `[BackupRequestIn]`; 배치가 previewing/running이면 409;
  `db.add_requests`(registered)로 append. 응답 `{added}`.
- `POST /batches/{id}/requests:delete` — body `{request_ids}`; 각 항목이 in-flight(preview_pending/
  approved/running)면 제외 또는 409; `db.delete_requests`. 응답 `{deleted}`.
- `POST /batches/{id}/requests:cancel` — body `{request_ids}`; 선택 항목 중 비terminal을 cancelled로,
  live dms_job_id는 best-effort DMS cancel. 응답 `{cancelled, dms_cancelled}`.
- `PUT /batches/{id}/requests`(replace) — 가드를 draft 전용 → **진행중 아닌 상태**로 완화
  (CSV 전체 교체용). previewing/running이면 409.
- 기존 `:approve`(ids), `requests:reset`(ids), `requests/{id}:cancel` 재사용.

DB(`db.py`):
- `delete_requests(batch_id, ids) -> int` 신규(in-flight 제외 삭제, RETURNING count).
- `add_requests`(재사용). `replace_requests`(재사용; 가드는 라우트에서).
- bulk cancel by ids: `cancel_requests`에 `request_ids` 옵션 추가(없으면 전체 — 기존 동작 유지).

가드 헬퍼: `_require_mutable_batch`(존재 + status ∉ {previewing, running} else 409).

## 프론트 (`BackupBatchDetail.tsx` + `helpers.ts` + `api.ts`)
- 선택 상태를 모든 행으로 확장(기존 preview_ready 한정 제거). 헤더 전체선택 체크박스.
- 일괄 작업 바: 선택 개수 + 상태별 활성 버튼(승인/재시도/삭제/취소).
- "+ 행 추가" 입력행(출발/대상 path) → `add`.
- CSV 바: 템플릿/현재 다운로드(기존 `rowsToCsv` 재사용) · 업로드(parse → replace, confirm).
- "재편집"→"편집".
- api.ts: `addRequests/deleteRequests/cancelRequests(ids)` 추가.
- 편집 가능 여부 `mutable = status ∉ {previewing, running}`로 게이트.

## 검증
- 백엔드 pytest: add/delete/replace-relax/bulk-cancel 라우트 + 가드(409) — fake DB.
- 디자인 리뷰 라운드(frontend-design) + 라이브 브라우저 동작 검증 라운드.

## 비목표
- 진행 중(previewing/running) 배치의 항목 변경(차단).
- DMS 소스 변경(없음).
