# Portal 데이터 백업 — Phase 2: 항목별 상세 + 선택·단계 승인 + 항목 취소 설계

- 날짜: 2026-06-26
- 상태: 설계 승인됨 (구현 진행)
- 범위: DMS Portal operator "데이터 백업" 탭, **Phase 2**
- 선행: Phase 1(초안 편집) `feat/portal-backup-draft-editing`에 구현·배포(v43)됨.

## 1. 목표 / 비목표

**목표**
1. 미리보기 후 **항목별 상세**(파일/디렉터리/바이트/에러/도구) 표시.
2. **선택 승인** — `preview_ready` 항목 중 고른 것만 실행.
3. **단계적 승인** — 일부 실행 후 남은 항목을 추가 승인. 운영자가 **마감(close)** 하면 남은 미승인 항목 제외하고 배치 완료.
4. **항목별 취소** — 비종료 요청 하나만 취소(배치는 유지). (배치 전체 취소는 기존 유지.)

**비목표**
- 라이브 DMS job 상세 조회(저장된 preview 메트릭만 표시 — 후속).
- DMS 백엔드(`src/dms/`) 변경 — 전부 포탈 측.

## 2. 핵심 설계 — 상태 분리

요청 상태에 **`approved`**(운영자가 실행 선택)를 추가, `preview_ready`(결정 대기)와 분리한다. 오케스트레이터는 **`approved`만** confirm한다.

**요청 상태기계**
```
registered → preview_pending → preview_ready ──(approve 선택)──▶ approved ──(confirm)──▶ running ──▶ succeeded│failed
                                     │                              │                       │
                          preview_failed(종료)      ──(취소/마감 제외)─┴───────────────────────┴──▶ cancelled(종료)
```
종료: `succeeded, failed, preview_failed, cancelled`.

**배치 상태기계** (`draft→previewing→previewed→running→done`, +`cancelled`)
- `:approve`로 선택 `preview_ready`→`approved`, 배치 `running`.
- 오케스트레이터가 `approved`→`running`→종료까지 구동. **approved+running 소진 시**: `preview_ready`가 남아 있으면 배치를 **다시 `previewed`로**(추가 승인 대기), 없으면 `done`.
- `:close`(마감)로 남은 `preview_ready`→`cancelled` 제외 후, 진행 항목 소진되면 `done`.

## 3. 백엔드 — `routers/backup.py`

### 변경: `POST /batches/{id}:approve`
- 본문(선택) `{ "request_ids"?: int[], "all"?: bool }`. 본문 없음/`all` → 모든 `preview_ready` 승인(기존 동작 호환).
- 가드: 배치 status ∈ {`previewed`, `running`}(아니면 409). 단계 승인을 위해 `running`에서도 허용.
- `db.approve_requests(id, request_ids)` → preview_ready 중 해당(또는 전체)을 `approved`로. 0건이면 422.
- 배치 → `running`. 반환 `{status, approved: n}`.

### 신규: `POST /batches/{id}:close`
- 가드: status ∈ {`previewed`, `running`}.
- `db.exclude_preview_ready(id)` → 남은 `preview_ready`→`cancelled`(n건).
- advance: 남은 `approved`/`running`이 0이면 배치 `done`, 아니면 그대로(오케스트레이터가 마무리). 반환 `{status, excluded: n}`.

### 신규: `POST /batches/{id}/requests/{rid}:cancel`
- 요청이 배치 소속·비종료여야 함(아니면 404/409).
- `db.cancel_request(id, rid)` → 해당 요청 `cancelled`, `dms_job_id` 반환.
- `dms_job_id` 있으면 best-effort `dms.cancel_job(actor=mtls:<user>)`.
- 배치 status 변경 안 함(오케스트레이터가 자연 advance). 반환 `{cancelled: true, dms_cancelled: 0|1}`.

### 기존 유지
- `:cancel`(배치 전체), `:preview`, draft 편집(Phase 1) 그대로.

## 4. 포탈 DB — `db.py`
- `approve_requests(batch_id, request_ids: list[int] | None) -> int` — `UPDATE … SET state='approved' WHERE batch_id=%s AND state='preview_ready' [AND id = ANY(%s)]`; rowcount.
- `exclude_preview_ready(batch_id) -> int` — `… SET state='cancelled' WHERE batch_id=%s AND state='preview_ready'`; rowcount.
- `cancel_request(batch_id, request_id) -> tuple[bool, str|None]` — `… SET state='cancelled' WHERE id=%s AND batch_id=%s AND state NOT IN (종료) RETURNING dms_job_id`; (changed, dms_job_id).
- `requests_in_states` 등 기존 재사용.

## 5. 오케스트레이터 — `orchestrator.py` `_drive_execute`
- confirm 대상: `preview_ready` → **`approved`** 로 교체(`list_requests(state="approved")`).
- 배치 advance:
  ```
  counts = batch_state_counts(bid)
  if not counts.get("approved") and not counts.get("running"):
      if counts.get("preview_ready"):
          set_batch_status(bid, "previewed")   # 추가 승인 대기로 복귀
      else:
          set_batch_status(bid, "done")
  ```
- `_drive_preview`는 변경 없음. `active_batches`(previewing/running)도 그대로 — `:approve`가 배치를 `running`으로 올리므로 오케스트레이터가 구동.
- **알려진 경미한 레이스**(문서화): 오케스트레이터가 마지막 running을 종료시키며 `done`으로 내리는 순간 운영자가 `:approve`하면 한 항목이 잠깐 떠 있을 수 있음. `:approve`가 항상 배치를 `running`으로 올리므로 다음 사이클/다음 승인에 자가 치유.

## 6. 프론트엔드
- `api.ts`: `approve(id, opts?: {request_ids?: number[]; all?: boolean})`(본문 옵션화), 신규 `close(id)`, `cancelRequest(id, rid)`.
- `helpers.ts`: `REQUEST_STATE`에 `approved`(라벨 "승인됨", cls `san-degraded`) 추가; `STATE_ORDER`에 `preview_ready`와 `running` 사이 삽입.
- `BackupBatchDetail.tsx`:
  - **선택 승인**: status ∈ {previewed, running}이고 `preview_ready`가 있으면 그 행에 체크박스 + 헤더 `선택 승인(N)`·`전체 승인`. (기존 "승인 후 실행"을 이 모델로 대체.)
  - **마감 버튼**: `preview_ready`가 남아 있으면 노출 → `close` (확인 다이얼로그).
  - **항목별 취소**: 비종료(preview_ready/approved/running) 행에 `취소`(확인).
  - **항목별 상세**: 행 펼침/팝오버로 저장된 preview 메트릭(파일·디렉터리·바이트·에러·도구) 표시.
  - `--delete` 배치는 선택/전체 승인 시 기존처럼 확인 다이얼로그.
- draft(Phase 1) 컨트롤과 공존(상태별 노출).

## 7. 마이너 결정(승인됨)
- 제외/취소 항목 종료상태 = **`cancelled` 재사용**(마감 제외·명시 취소 공통). 새 상태 안 늘림.
- 항목별 상세 = **저장된 메트릭만**(라이브 DMS 조회는 후속).

## 8. 테스트
- **백엔드(페이크 DB)**: `:approve` 선택/전체/0건422/비-previewed·running 409, `:close` 제외+done전이, `/requests/{rid}:cancel` 단건·dms_job_id·종료항목 거부. (`tests/test_portal_backup_phase2.py`)
- **오케스트레이터(단위)**: approved-only confirm, approved+running 소진 후 preview_ready 남으면 previewed 복귀/없으면 done. 페이크 DMS+DB.
- **프론트**: `npm run build`(tsc).
- **테스트베드**: v44 빌드→배포→E2E(선택 승인→일부 실행→추가 승인→마감, 항목 취소).

## 9. 변경 파일
- `src/portal/backend/routers/backup.py` — `:approve` 본문, `:close`, `/requests/{rid}:cancel`.
- `src/portal/backend/db.py` — approve_requests / exclude_preview_ready / cancel_request.
- `src/portal/backend/orchestrator.py` — `_drive_execute` approved-only + previewed 복귀.
- `src/portal/frontend/src/api.ts` — approve 옵션화 + close + cancelRequest.
- `.../backup/helpers.ts` — approved 상태/순서.
- `.../backup/BackupBatchDetail.tsx` — 선택/전체 승인·마감·항목 취소·항목 상세.
- `tests/test_portal_backup_phase2.py` (+ 오케스트레이터 테스트).
- `install/6.dms-portal-data-backup.md` — §4.3 승인 모델 갱신 + 항목 취소/마감.
