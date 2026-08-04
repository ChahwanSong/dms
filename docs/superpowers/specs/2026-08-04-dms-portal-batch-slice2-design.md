# DMS 포탈 — 슬라이스 2 (배치성: 대량 묶음) 설계

2026-08-04. Phase 4 포탈의 두 번째 슬라이스. 상위 스펙 `2026-08-02-dms-clean-slate-design.md`
§8(포탈)의 하위 구현 문서이며, 슬라이스 1(`2026-08-04-dms-portal-thin-slice-design.md`,
일회성 sync 전체 스택)의 후속이다. 충돌 시 상위 스펙이 이긴다. 슬라이스 1(백엔드 SPA 서빙 +
frontend/ + 세션 인증 + 일회성 sync preview→confirm + admin 화면)은 구현·테스트베드 실증 완료.

## 0. 배경 & 범위

### 0.1 배치성 = 대량 묶음(bulk), 시간 예약/반복 아님
DMS의 "배치성"은 cron/스케줄이 아니라 **여러 요청을 한 묶음으로 올려 한 번에 구동**하는 것이다
(legacy `backup_batches`/`scan_batches` 대응 — cron/스케줄러는 legacy에도 없었다). 자식 각각은
**기존 request→plan→run→result 파이프라인을 그대로 재사용**하고, 이 슬라이스가 새로 만드는 것은
그 위의 **grouping + 동시성 쓰로틀 + 집계 + 배치 단위 게이트** 레이어다.

### 0.2 이 슬라이스가 담는 것
- **scan 배치 + sync 배치** 둘 다 (한 슬라이스로).
- 부모 batch + 자식 batch_items, **CSV intake**(프론트에서 파싱).
- **운영자 지정 `max_concurrency`** 기반 쓰로틀(자식을 온디맨드 materialize).
- sync 배치의 **배치 단위 preview→confirm 게이트**.
- 종료 시 성공/실패 **집계** + 실패 자식 **수동 재실행**.
- 포탈 **배치 페이지 3화면**(목록·생성·상세) — 슬라이스 1의 disabled "배치 작업" 내비를 활성화.

### 0.3 비목표 (이 슬라이스에서 하지 않음)
- rm 배치(scan/sync만). 시간 예약/반복(cron) — DMS 범위 밖.
- 배치 자체의 우선순위 스케줄링(자식은 기존 priority 큐 사용).
- CSV multipart 업로드(파일 파싱은 프론트, API는 파싱된 items[] JSON 수신).
- 배치 결과 리포트 export, 배치 템플릿 저장.

## 1. 배치 라이프사이클

자식(batch_items)은 기존 파이프라인을 재사용하고, 배치는 자식 상태를 집계한다.

생성(`POST`) 시 배치는 **초기 활성 상태로 바로 시작**한다(별도 Draft/검토 단계 없음 — 생성 폼이
제출 전에 파싱 결과를 미리 보여준다): scan → `Running`, sync → `Previewing`.

- **scan 배치** (게이트 없음): `Running → Completed(succeeded N / failed M)`
- **sync 배치** (게이트 있음):
  `Previewing → PreviewReady → [운영자 배치 confirm] → Running → Completed(N/M)`
  - 자식 전원이 preview(dry-run) 완료(`ConfirmPending`) 또는 rejected면 배치 → `PreviewReady`.
  - 운영자가 **배치를 한 번 confirm** → 배치 `Running` → 오케스트레이터가 각 자식 job을 그 자식의
    `preview_fingerprint`로 confirm → 실행.
- **재실행**: `Completed` 배치에서 운영자가 **실패한 자식만** 재실행 → 해당 item을 `Queued`로 리셋
  (request_id 비움) → 배치 `Running` 복귀 → 오케스트레이터가 다시 materialize.
- **취소**: `Running`/`Previewing` 배치 취소 → 미착수(Queued) item은 `Cancelled`, 진행 중 자식은
  개별 `terminate`. 배치 → `Cancelled`.

배치 상태: `Previewing, PreviewReady, Running, Completed, Cancelled`.
batch_item 상태: `Queued, Materialized, Succeeded, Failed, Rejected, Cancelled`.

## 2. 데이터 모델 (legacy backup_batches/backup_requests 대응)

새 테이블 2개 + 기존 `requests`에 링크 컬럼 1개. **기존 파이프라인 무변경**(일회성 요청은 batch_id NULL).

### `batches` (부모)
```
batch_id TEXT PK, operation TEXT('scan'|'sync'), requester_id TEXT, actor TEXT,
status TEXT, max_concurrency INTEGER, options TEXT(JSON, 배치 전체 공통),
note TEXT, item_count INTEGER, succeeded_count INTEGER, failed_count INTEGER,
created_at TEXT, updated_at TEXT
```

### `batch_items` (자식 사양 = CSV 각 행 + 추적)
```
batch_id TEXT, seq INTEGER, payload TEXT(JSON: 자식 요청 페이로드),
status TEXT, request_id TEXT NULL, reason_code TEXT NULL,
created_at TEXT, updated_at TEXT,   PRIMARY KEY(batch_id, seq)
```

### `requests.batch_id TEXT NULL` (링크)
자식으로 materialize된 요청만 채워짐. 일회성 요청은 NULL.

핵심: **batch_items가 "계획 + 추적"의 원장**이고, 오케스트레이터가 이를 실제 `requests`로 쓰로틀하며
materialize한다. planner/stepper/placement는 materialize된 자식(평범한 Pending 요청)만 보므로 배치
동시성 제어는 batch_items를 통해서만 이뤄진다.

## 3. 배치 오케스트레이터 (신규 컨트롤러 루프)

planner/stepper처럼 `run_once()`를 `batch_orchestrator_interval_seconds`(기본 5s)로 반복. 활성
배치(`Previewing`/`Running`)마다:

1. **쓰로틀 materialize**: 활성(비종단) 자식 수를 세고, `max_concurrency − active`만큼 `Queued`
   batch_items를 `requests.create(operation, requester_id, actor, resource_key, payload, priority,
   batch_id=)`로 생성 → item을 `Materialized`+request_id로. resource_key는 도메인 규칙대로 payload로
   계산(자식마다 고유).
2. **집계**: materialize된 자식이 종단되면 item 상태(Succeeded/Failed/Rejected) 기록 +
   batches.succeeded_count/failed_count 갱신. 슬롯이 비면 다음 `Queued` release.
3. **sync 게이트**:
   - `Previewing`: 자식은 preview까지만 → `ConfirmPending` 대기(오케스트레이터가 아직 confirm 안 함).
     preview 자체도 max_concurrency로 쓰로틀. 모든 item이 ConfirmPending/rejected면 배치 →
     `PreviewReady`.
   - 운영자 배치 confirm → 배치 `Running` → 오케스트레이터가 ConfirmPending 자식을 **쓰로틀하며**
     그 자식의 fingerprint로 confirm(≤max_concurrency 동시 실행).
   - **preview 만료 엣지**: confirm 전 `preview_expires_at` 만료 자식은 item을 `Queued`로 리셋해
     re-preview(재materialize). (배치 자식엔 넉넉한 preview TTL을 쓰도록 설정 여지.)
4. **종료**: 전 item 종단 시 배치 `Completed`.
5. **재실행/취소**: 위 §1대로 batch_items를 리셋/취소하고 오케스트레이터가 반영.

컨트롤러에 이 루프를 추가(기존 planner/job-stepper/reconciler/retention 옆). 리스 기반 단일 리더에서
동작(기존 컨트롤러 패턴 동일).

## 4. API

모두 `require_admin`(운영자). 슬라이스 1 세션 인증 재사용.
- `POST /api/admin/batches` — 생성 `{operation, max_concurrency, options, note, items:[<payload>...]}`
  → `{batch_id, status}`. items 각 원소는 operation별 자식 페이로드(scan: `{storage,target}`,
  sync: `{source_storage,source,destination_storage,destination}`). options는 배치 공통(도메인
  `validate_options`로 검증). CSV→items[] 변환은 **프론트**에서. 생성 시 초기 상태는 scan→`Running`,
  sync→`Previewing`(§1). 빈 items는 422(`empty_batch`).
- `GET /api/admin/batches` — 목록(status·counts·created_at).
- `GET /api/admin/batches/{batch_id}` — 상세(배치 + items[] with status/request_id/reason).
- `POST /api/admin/batches/{batch_id}:confirm` — 배치 confirm(sync `PreviewReady`→`Running`).
  게이트: 배치가 PreviewReady가 아니면 409.
- `POST /api/admin/batches/{batch_id}:rerun-failed` — Failed item 리셋 + 배치 Running 복귀.
- `POST /api/admin/batches/{batch_id}:cancel` — 배치 취소.

거부/실패엔 기계가 읽는 reason_code(상위 스펙 §1): `batch_not_confirmable`, `empty_batch`,
`invalid_batch_operation`, `no_failed_items` 등.

## 5. 포탈 (배치 페이지, admin 트리)

슬라이스 1의 disabled "배치 작업 · 준비 중" 내비를 **활성 링크**로 교체(`/admin/batches`). admin 전용.
슬라이스 1 C 디자인·컴포넌트(Table/StatusPill/Card/Button/Dialog/Field, api 클라이언트, TanStack
Query, RequireRole) 재사용.

- **배치 목록** `/admin/batches`: operation·status pill·진행률(succeeded/failed/item_count)·생성일.
  "배치 생성" 버튼.
- **배치 생성** `/admin/batches/new`: operation 선택(scan/sync), **CSV 업로드 또는 붙여넣기 →
  파싱된 행 미리보기 테이블**(잘못된 행 표시), 공통 options·`max_concurrency`(숫자)·note 입력 →
  `POST /api/admin/batches`. 성공 시 배치 상세로 이동.
- **배치 상세** `/admin/batches/{id}`: 배치 status·진행률 + **자식 items 테이블**(seq·경로 요약·status
  pill·reason). 폴링(비종단 배치 동안 refetchInterval). 상태별 액션:
  - sync `PreviewReady` → **"배치 확인" 버튼**(Radix Dialog: 집계 preview 요약 + 확인) →
    `:confirm`.
  - `Completed`에 실패 있으면 **"실패분 재실행"** → `:rerun-failed`.
  - `Running`/`Previewing` → **취소** → `:cancel`.
- 신규 훅: `useBatches`, `useBatch(id)`(폴링), `useCreateBatch`, `useConfirmBatch`,
  `useRerunFailed`, `useCancelBatch`.
- **CSV 파싱**(프론트): 헤더 행 기준. scan: `storage,target`. sync:
  `source_storage,source,destination_storage,destination`. 빈 행/열 부족 행은 에러 표시하고 제출 차단.
  파싱은 순수 함수로 분리해 단위 테스트.

## 6. 테스트

- **백엔드(pytest)**: 배치 repo(batch+items 생성·조회), 마이그레이션(신규 2테이블 + requests.batch_id),
  **오케스트레이터 run_once**: (a) max_concurrency 쓰로틀 materialize(활성 ≤ 상한), (b) 자식 종단→집계
  counts, (c) 전원 preview→`PreviewReady` 전이, (d) 배치 confirm→ConfirmPending 자식 confirm,
  (e) 전 item 종단→`Completed`, (f) rerun-failed 리셋, (g) cancel. API 라우트(생성·목록·상세·confirm·
  rerun·cancel + 권한/409). CSV행→payload 도메인 검증.
- **프론트(vitest+MSW)**: CSV 파싱 순수함수(정상/오류행), 배치 목록, 생성 폼(파싱 미리보기→제출 body
  `{operation,max_concurrency,options,note,items}`), 배치 상세(items 테이블·진행률·폴링), 배치확인
  다이얼로그(PreviewReady), 재실행 버튼(Completed+실패), 내비 "배치 작업" 활성.
- **테스트베드 실증**(구현 후): 소형 scan 배치 + 소형 sync 배치 end-to-end(materialize 쓰로틀·집계·
  sync 배치 confirm·재실행 확인).

## 7. 결정 기록

- 배치 대상 = **scan + sync 둘 다**, **한 슬라이스**로.
- 실행 = **운영자 지정 max_concurrency** 쓰로틀, 자식 온디맨드 materialize(batch_items 원장).
- sync = **배치 단위 preview→confirm**(자식 전원 preview 후 1회 confirm, 실행은 쓰로틀).
- 실패 = 집계 종료 + **실패분 수동 재실행**.
- 자식은 **기존 request 파이프라인 재사용**, planner/stepper 무변경. 배치 로직은 새 오케스트레이터 루프.
- 포탈은 슬라이스 1 디자인·컴포넌트 재사용, "배치 작업" 내비 활성.
