# 슬라이스 32 — 배치 작업 생성 개편 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **문서 관례:** 플랜 파일은 실행이 끝나면 삭제해도 된다 — git 이력이 보존한다.
> 현재 동작의 진실은 코드가 말한다(CLAUDE.md 드리프트 규칙). **플랜과 실측이
> 충돌하면 실측이 이긴다.**

**Goal:** 배치 생성(BatchCreate)을 legacy 수준의 실행 제어로 개편한다(사용자 승인 스코프):
(A) 배치 레벨 실행 제어 — 연산별 옵션 입력(scan=top_k·verbose·quiet / sync=단건
위저드와 동일 셋), 배치 우선순위(priority), 배치 노드 수(node_count, 정책 max_nodes
와 min-캡). (B) 단일 스토리지 강제 + 입력 UX — 배치 레벨 스토리지 드롭다운(행은
경로만), 인라인 테이블·CSV 붙여넣기·파일 업로드·CSV 내보내기 4방식. (C) 재실행 —
`POST /api/admin/batches/{id}:rescan`(종결 item 전체를 Queued 리셋, 성장 모니터링
용도). **스코프 제외**: legacy scan 옵션 4개(summary_only/follow_symlinks/
one_file_system/max_depth — 신규 dscan 에 플래그 없음, `src/dms/domain.py:117-122`
주석 실측), PV 경로 빌더(storages 에 PV 메타데이터 없음 — `src/dms/migrations.py:209-219`
근방 storages 스키마 실측), 선택 실행/held 파킹(즉시 실행 모델과 충돌 — 별도 설계).

**Architecture:** items API 스키마는 유지한다(각 item 에 storage 필드 포함 — 프론트가
배치 레벨 선택으로 조립). 서버는 `validate_batch`(`src/dms/domain.py:222-228`)에
동질성 검증을 추가해 혼합을 `batch_storage_mixed` 로 거부한다. priority·node_count 는
**batches 테이블 새 컬럼**으로 저장한다(orchestrator 가 batch 행을 읽는 구조 실측 —
`src/dms/repositories/batches.py:44-50` SELECT * → `src/dms/batch_orchestrator.py:62-73`
`_materialize`). options 에 싣는 대안은 기각: batch options 는 item 별
`build_data_payload`→`validate_options` allowlist(`src/dms/domain.py:135-162`)를 타서
`unknown_option` 422 가 되고, options 는 도구 CLI 플래그로 렌더된다
(`src/dms/execution_manifests.py:17-35`) — 실행 제어값이 도구 플래그로 새면 안 된다.
node_count 는 `_materialize` 가 자식 request **payload** 에 실어 planner 가
`resolve_fanout` 직전(`src/dms/planner.py:182-188`)에 읽고 **min(정책 max_nodes,
요청값)** 으로 캡한다(placement.py 의 fanout 산식 `src/dms/placement.py:113-131`).
payload 는 신뢰 경계(DB 무검증 INSERT 전제) — planner 는 fail-closed 로 방어한다
(값이 있는데 비정상이면 `invalid_node_count` reject, 없으면(None) 정책값 — null≠0).
프론트는 슬라이스 31 이 배치 생성용으로 만들어 둔 위저드 프레임
(`frontend/src/components/wizard/Wizard.tsx:8-11` 주석이 명시)에 4스텝으로 얹는다.

## Global Constraints (제약 전파)

- **DB 가 신뢰 경계다**: planner 의 payload 파싱은 방어적으로(fail-closed reject —
  stepper 층1 `unknown_tool` 관례). API 층 검증을 통과한 값만 정상 경로다.
- **null(모름) ≠ 0**: priority/node_count 미지정은 NULL(=정책 기본)이다. truthy 검사
  금지 — `is not None` 명시 비교(`batch.get("node_count") is not None`).
- **사유 코드 3중 등록**: 백엔드 리터럴(키워드 `reason_code=`/`detail=` 또는
  `DomainValidationError("...")` 생성자 첫 위치 리터럴만 AST 추출 —
  `tests/test_reason_codes_coverage.py`) + `frontend/src/lib/reasonCodes.json` +
  `frontend/src/lib/api.ts` REASON_MESSAGES(양방향 계약 `reasonCodes.test.ts`).
  json 과 REASON_MESSAGES 는 **같은 태스크에서** 함께 고쳐야 vitest 가 초록이다.
- **새 컬럼은 CREATE TABLE + `_ensure_columns` 양쪽**: batches CREATE 는
  `src/dms/migrations.py:75-88`, `_ensure_columns` 튜플 목록은 `migrations.py:477-513`
  (현재 batches 항목 0건). 컬럼 추가는 테이블·인덱스 전수 열거(`test_migrations.py`)에
  안 걸린다 — RED 는 `tests/test_migrations_batch.py` 의 부분집합 단언(:12-14)에 새
  컬럼을 추가해 만든다.
- **airgap**: 런타임 외부 리소스 금지. 파일 업로드는 FileReader 로컬 읽기, CSV
  내보내기는 Blob+`URL.createObjectURL` 다운로드(아래 전제 #10 — clipboard API 금지).
- **e2e L1~L4 불변식**: e2e 는 배치 화면을 방문하지 않는다(전제 #9) — 게이트는
  9 passed 무변경. 새 마크업에 문서 첫 `.overflow-x-auto` 를 가로챌 래퍼를 만들지
  않는다(표 래퍼 Table.tsx 경유만).
- **text-ok/busy/bad 클래스 계약**: 오류 문구는 `text-bad`, 안내는 `text-muted` —
  기존 클래스명 유지(값은 토큰).
- **PYTHONPATH 절대경로**: 백엔드 테스트는 반드시
  `PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src
  /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/... -q`
  (venv 편집설치는 본 저장소 src 를 가리킨다 — CLAUDE.md 함정).
- **git 은 pathspec 커밋**: `git commit -m "..." -- <경로들>`. `git add` 는 신규
  파일에만. 배포 커밋·태그 bump 는 「플랜 이후」 절 — 태스크에서 금지. 커밋은
  오케스트레이터 지시에 따른다.
- `legacy/` 읽기 전용. 러너(`src/dms_job_runner/`)·에이전트(`src/dms/agent/`) 무접촉
  (T11 grep 게이트로 증명 — 배포가 제어면만인 근거).

## 전제 재확인 (2026-08-13, 코드 직접 실측 — 함정 목록)

| # | 실측 | 귀결 |
|---|---|---|
| 1 | `BatchBody`(`src/dms/api/routes_batches.py:13-18`)는 operation/max_concurrency/options/note/items 뿐. options 는 이미 item 별 `build_data_payload(body.operation, options=body.options, **item)` 로 검증된다(:26-27) | 옵션은 **배선만** 하면 된다(프론트 입력란 + 그대로 전송). priority/node_count 는 필드 신설 |
| 2 | batches CREATE TABLE(`migrations.py:75-88`)에 priority/node_count 없음. `_ensure_columns`(:477-513)에 batches 항목 0건. orchestrator 는 batch **행**을 읽는다(`batches.py:44-50` SELECT *) | **새 컬럼 2개 필요**(priority TEXT, node_count INTEGER) — CREATE+_ensure_columns 양쪽. options 대안 기각(Architecture 절 근거) |
| 3 | `_materialize`(`batch_orchestrator.py:62-73`)가 `resolve_priority(self._repos, batch["operation"], None)`(:65) — 항상 정책 기본. `resolve_priority`(`domain.py:213-219`)는 requested None→정책 default_priority→mid 폴백을 이미 구현(계약: `tests/test_default_priority.py`) | priority 전달은 한 인자: `batch.get("priority")`. 미지정 배치는 **기존 동작 그대로**(정책 기본) |
| 4 | `resolve_fanout`(`placement.py:113-131`)은 정책 max_nodes 로만 캡. `tests/test_placement.py:135-165` 는 전부 키워드 호출 | `requested_node_count=None` 키워드 추가는 기존 테스트 무수정 초록. sync 는 max_nodes 가 **면당** 상한이므로 요청값도 면당 min-캡(의미 자리 동일) |
| 5 | planner 의 payload 소비는 `payload.get(...)`(`planner.py:129·169-173·205-209`). 깨진 payload 는 run_once 격리(:63-70)로 Pending 잔류+plan_error 이벤트. 거부 관례는 `_reject`(:73-80) reason_code | fail-closed 선택: `payload.get("node_count")` 가 **있는데** 비int(bool 포함)·<1 이면 `invalid_node_count` reject. 없으면 정책값. 조용한 무시(fail-open)는 기각 — 변조 증거를 삼키지 않는다 |
| 6 | `tests/test_domain_batch.py:4-15` 가 `build_data_payload` 반환 payload 를 **정확 일치**(`==`)로 고정 | node_count 를 `build_data_payload` 에 주입하면 단건 제출 payload 가 전부 바뀌어 이 테스트가 깨진다 — 주입은 `_materialize` 에서 build 후 `payload["node_count"] = ...` 로만 |
| 7 | 사유 코드 계약: AST 추출기는 `detail=`/`reason_code=` **키워드 리터럴** + 예외 생성자 첫 위치 리터럴만(`test_reason_codes_coverage.py` extract_reason_code_literals). `reasonCodes.test.ts` 는 json↔REASON_MESSAGES 양방향(누락·죽은 키 모두 빨강). `invalid_priority` 는 이미 양쪽 등록(json:29, api.ts:60) | 신규 3코드(`batch_storage_mixed`·`invalid_node_count`·`batch_not_rescannable`)는 json+REASON_MESSAGES 동시 등록. 백엔드는 `DomainValidationError("...")` 생성자 리터럴 또는 `detail="..."` 키워드로 발화(추출 그물 안). planner `self._reject(rid, "invalid_node_count")` 는 위치 인자라 추출 밖 — domain 쪽 생성자 리터럴이 커버(T1) |
| 8 | 기존 계약 테스트: `BatchCreate.test.tsx:12-29`(CSV textarea 동선+행별 storage 바디), `csv.test.ts:1-21`(연산별 고정 헤더), `test_api_batches.py:6-69`(items 는 이미 전부 동질 — 동질성 추가에도 초록 유지), `BatchDetail.test.tsx`(버튼 3종), orchestrator 테스트는 repo.create 키워드 호출(기본값 추가에 호환) | BatchCreate.test·csv.test 는 **전면 갱신**(새 계약으로). test_api_batches 는 추가만. repo/orchestrator 시그니처는 키워드 기본값으로 하위호환 |
| 9 | e2e 에 배치 화면 없음(`frontend/e2e/` 전수 grep — 유일한 매치는 harness env `DMS_BATCH_ORCHESTRATOR_INTERVAL_SECONDS`, `global-setup.ts:218`. `03-layout.spec.ts:27-57` 방문 화면에 배치 없음) | e2e 게이트 = **9 passed 무변경**. 배치 화면 개편이 e2e 를 깨면 그건 셸/공용 컴포넌트 회귀다 |
| 10 | 운영 포탈은 http(비 localhost, 예: http://192.168.75.215:8080) = **비보안 컨텍스트 → `navigator.clipboard` 부재** | CSV 내보내기는 클립보드 금지 — Blob+`URL.createObjectURL` 다운로드(.csv). airgap 무관(전부 로컬) |
| 11 | scan 단건 화면(`SubmitScan.tsx:36-46·69-88`)이 top_k/verbose/quiet+priority 를 이미 노출(dscan 실측 전부). sync 단건(`SubmitJob.tsx:36-48`)은 클라이언트 미러(CHMOD_RE·CHOWN_RE·intFieldError)를 **파일 내부에** 보유 | 배치 옵션 스텝은 두 화면을 미러. 미러 정규식·범위검사는 `features/jobs/optionRules.ts` 로 추출해 공유(중복 미러 발산 방지 — 슬라이스 31 T3 formFields 이사 선례). SubmitJob 은 import 줄만 변경, 기존 테스트 무수정 초록이 게이트 |
| 12 | 위저드 프레임은 배치 생성 재사용을 명시 설계(`Wizard.tsx:8-11`), SubmitJob 4스텝 관례 확립(`SubmitJob.tsx:55-60`) | **위저드로 간다**(단일 폼 확장 기각). 근거: ① 관례 일치(연산→대상→옵션→확인) ② 「지금 개발단계에서 조금씩 계속 수정될 예정」— 스텝 단위 격리라 입력 방식·옵션이 늘어도 해당 스텝만 갈린다 ③ 프레임은 도메인 비종속으로 이미 테스트됨 |
| 13 | `validate_batch`(`domain.py:222-228`)의 max_concurrency 는 `>=1` 만 — **상한 없음**. 거대값이면 orchestrator 가 전 item 을 한 틱에 materialize(슬롯 산식 `batch_orchestrator.py:101·109`) | 이 참에 상한 64 를 넣는다(기존 `invalid_max_concurrency` 재사용 — 신규 코드 불요). UI input max=64. 기존 테스트 최대값 5 라 초록 유지 |
| 14 | `:rerun-failed` 선례: route `routes_batches.py:65-76`, repo `reset_failed_items`(`batches.py:85-94`, failed 카운터 **감산**). 거짓 취소 방지 선례는 cancel(:90-117) — 종료 성공 후에만 DB 기록. item 종단 집합은 `_ITEM_TERMINAL`(`batch_orchestrator.py:18`) | `:rescan` 가드는 **배치 상태 ∈ {Completed, Cancelled}** (종단 배치 ⇒ 전 item 종단 ⇒ 살아있는 자식 없음 — 종료할 것이 없어 cancel 선례의 "실행면 먼저" 단계가 공집합으로 성립). 비종단 배치에서 허용하면 활성 자식과 리셋 item 이 충돌(resource_conflict·이중 실행) — fail-closed 거부 `batch_not_rescannable`(409). 카운터는 감산이 아니라 **0 리셋**(전 item 재시작) |
| 15 | 취소된 배치의 item 은 cancel 이 전부 종단화(:118-134). 재실행 시 새 request 는 같은 resource_key 라도 이전이 전부 종단이라 `find_active` conflict(:130-137) 없음 | Cancelled 배치도 rescan 허용 가능(전체 재실행 의미). PreviewReady 는 자식 ConfirmPending(활성)이라 제외 — 취소 후 rescan 동선 안내 |
| 16 | 스토리지 목록: `/api/user/storages`(`routes_storages.py:95-100`)는 enabled 전체 반환 — SubmitJob/SubmitScan 이 쓰는 `useUserStorages`+`StoragePicker`(`formFields.tsx:11-30`) 재사용 가능 | 배치 화면도 동일 훅·피커 재사용(신규 API 불요) |
| 17 | 프론트 훅 `_action`(`useBatches.ts:19-26`)이 `:confirm`/`:rerun-failed`/`:cancel` 을 동사 하나로 일반화 | `useRescanBatch = _action(id, "rescan")` 한 줄. BatchDetail 버튼 패턴은 상태 조건부 렌더(`BatchDetail.tsx:24-28`) |

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/dms/domain.py` (수정) | T1: validate_batch 확장(동질성·mc 상한·priority·node_count 검증) |
| `frontend/src/lib/reasonCodes.json`·`frontend/src/lib/api.ts` (수정) | T1·T6: 신규 사유 코드 3건 양쪽 등록 |
| `src/dms/migrations.py`·`src/dms/repositories/batches.py` (수정) | T2: batches.priority/node_count 컬럼(CREATE+_ensure_columns)·repo create/reset_all_items |
| `src/dms/api/routes_batches.py` (수정) | T3: BatchBody 필드·검증 배선 / T6: `:rescan` 라우트 |
| `src/dms/batch_orchestrator.py` (수정) | T4: _materialize priority 전달·payload node_count 주입 |
| `src/dms/placement.py`·`src/dms/planner.py` (수정) | T5: requested_node_count min-캡·payload 방어 파싱 |
| `tests/test_domain_batch.py`·`test_migrations_batch.py`·`test_repo_batches.py`·`test_api_batches.py`·`test_batch_orchestrator_scan.py`·`test_placement.py`·`test_planner.py` (수정) | 각 태스크 RED |
| `frontend/src/lib/csv.ts`(+`csv.test.ts`) (재설계) | T7: 경로 전용 파서(scan 1열/sync 2열, 헤더 유연)+serialize |
| `frontend/src/features/jobs/optionRules.ts` (신설), `SubmitJob.tsx`(import 줄만) | T8: 옵션 미러 추출 공유 |
| `frontend/src/features/batches/BatchCreate.tsx`(+test 전면 개편) | T9: 4스텝 위저드·입력 4방식·옵션/우선순위/노드수 |
| `frontend/src/features/batches/{BatchDetail.tsx,useBatches.ts}`(+test), `frontend/src/lib/types.ts` (수정) | T10: rescan 버튼·훅·타입 |

---

### Task 0: 기준선 확인 (커밋 없음)

- [ ] **Step 1**: 백엔드 전체 —
  `PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests -q`
  Expected: ~1293 passed(실측치 기록). 빨강이면 진행 전 보고.
- [ ] **Step 2**: `cd frontend && npx vitest run && npx tsc -b` — 기준선 기록(349+).
- [ ] **Step 3**: `cd frontend && npm run test:e2e` — **9 passed**(수기 게이트, CI 없음).

---

### Task 1: 도메인 — validate_batch 확장 + 사유 코드 등록

**Files:** `src/dms/domain.py`, `tests/test_domain_batch.py`,
`frontend/src/lib/reasonCodes.json`, `frontend/src/lib/api.ts`

**Interfaces:** `validate_batch(operation, max_concurrency, items, *, priority=None,
node_count=None)` — 기존 3위치 인자 호출(routes_batches.py:25, test_domain_batch.py)
하위호환. 추가 규칙:
- scan: `{(i or {}).get("storage") for i in items}` 가 2종 이상이면
  `DomainValidationError("batch_storage_mixed", ...)`. sync: `(source_storage,
  destination_storage)` 짝 기준 동일 검사. (누락 storage 는 이후 item 별
  build_data_payload 의 `missing_storage` 가 잡는다 — 순서 유지.)
- max_concurrency: 기존 `<1` 검사에 `>64` 추가(전제 #13, 코드는 기존
  `invalid_max_concurrency` 재사용).
- priority: `is not None` 이고 `PRIORITIES`(`domain.py:61`) 밖이면
  `DomainValidationError("invalid_priority", priority)`.
- node_count: `is not None` 이고 (비int·bool·<1·>1024) 면
  `DomainValidationError("invalid_node_count")`. 실제 상한은 planner 의 min(정책) —
  1024 는 API 위생 상한일 뿐임을 주석으로.

- [ ] **Step 1 (RED)**: `tests/test_domain_batch.py` 에 추가 —
  scan 혼합 storage → `batch_storage_mixed` / sync 혼합 짝 → 동일 / 동질 items 통과 /
  `max_concurrency=65` → `invalid_max_concurrency` / `priority="urgent"` →
  `invalid_priority` / `priority=None`·`"high"` 통과 / `node_count=0`·`True`·`"4"` →
  `invalid_node_count` / `node_count=None`·`4` 통과. `pytest.raises` 에서
  `exc.value.reason_code` 를 단언(코드 리터럴 고정).
  Run(이하 백엔드 Run 은 전부 PYTHONPATH 접두 포함):
  `... -m pytest tests/test_domain_batch.py -q` → RED.
- [ ] **Step 2 (GREEN)**: domain.py 구현. 주석은 한국어로 「왜」(단일 스토리지 강제
  근거: legacy 운영 관례 — 행별 혼합은 오입력 신호).
- [ ] **Step 3**: 사유 코드 등록 — `reasonCodes.json` 에 `batch_storage_mixed`·
  `invalid_node_count` 추가(batch 묶음 근처 :19-23), `api.ts` REASON_MESSAGES
  (batch 절 :30-44 근처)에 한국어 매핑(예: "배치의 스토리지가 섞여 있습니다 — 한
  배치는 하나의 스토리지만" / "노드 수 값이 올바르지 않습니다").
  Run: `... -m pytest tests/test_domain_batch.py tests/test_reason_codes_coverage.py -q`
  + `cd frontend && npx vitest run src/lib` → 전부 GREEN.
- [ ] **Step 4**: 회귀 — `... -m pytest tests/test_api_batches.py tests/test_batch_orchestrator_scan.py tests/test_batch_orchestrator_sync.py -q` (기존 동질 items 라 초록 유지 확인).
- [ ] **Step 5**: 커밋(pathspec) — `src/dms/domain.py tests/test_domain_batch.py frontend/src/lib/reasonCodes.json frontend/src/lib/api.ts`

---

### Task 2: 스키마·repo — batches.priority/node_count + reset_all_items

**Files:** `src/dms/migrations.py`, `src/dms/repositories/batches.py`,
`tests/test_migrations_batch.py`, `tests/test_repo_batches.py`

- [ ] **Step 1 (RED)**: `test_migrations_batch.py` — 신규 DB 컬럼 단언(:12-14 집합에
  `priority`,`node_count` 추가) + **구형 DB 경로**: batches 를 두 컬럼 없이 CREATE 한
  뒤 `migrate(db)` 가 ALTER 로 보강하는지(:20-27 의 requests.batch_id 패턴 복제).
  `test_repo_batches.py` — `create(..., priority="high", node_count=4)` 저장 후
  `get` 으로 회수, 미지정 create 는 둘 다 None(**null≠0** — `is None` 단언).
  `reset_all_items`: Succeeded/Failed/Cancelled 섞인 종단 item 들이 전부 Queued+
  request_id/reason_code NULL, succeeded_count/failed_count 가 **0**, 반환값 = 리셋 수,
  비종단(Materialized) item 은 건드리지 않음.
- [ ] **Step 2 (GREEN)**:
  - migrations: CREATE TABLE batches(:75-88)에 `priority TEXT`,`node_count INTEGER`
    + `_ensure_columns` 튜플(:477-513)에 `("batches","priority","TEXT")`,
    `("batches","node_count","INTEGER")` — 슬라이스 14 실 500 교훈 주석 관례 유지.
  - repo `create`(:10-29): 키워드 `priority=None, node_count=None` 추가, INSERT 컬럼
    확장. `reset_all_items(batch_id) -> int`: 트랜잭션 안에서
    `status IN ('Succeeded','Failed','Rejected','Cancelled')`(= `_ITEM_TERMINAL`) 인
    seq 를 `reset_item_to_queued`(:71-72)로 돌리고 카운터를 0 으로 UPDATE(감산 아님 —
    전체 재시작이라 절대값이 진실).
- [ ] **Step 3**: `... -m pytest tests/test_migrations_batch.py tests/test_repo_batches.py tests/test_migrations.py -q` → GREEN(전수 열거 그물 포함).
- [ ] **Step 4**: 커밋 — `src/dms/migrations.py src/dms/repositories/batches.py tests/test_migrations_batch.py tests/test_repo_batches.py`

---

### Task 3: API — BatchBody.priority/node_count 배선

**Files:** `src/dms/api/routes_batches.py`, `tests/test_api_batches.py`

- [ ] **Step 1 (RED)**: `test_api_batches.py` 추가 —
  ① `priority:"high", node_count:4` 포함 202 + repo `get` 으로 저장 확인
  ② `priority:"urgent"` → 422 `invalid_priority` ③ `node_count:0` → 422
  `invalid_node_count` ④ scan items 혼합 storage → 422 `batch_storage_mixed`
  ⑤ sync items 혼합 짝 → 동일 ⑥ `max_concurrency:65` → 422
  ⑦ 미지정 시 202 + priority/node_count 가 None(기존 바디 무수정 호환 — 옵션 필드).
- [ ] **Step 2 (GREEN)**: `BatchBody` 에 `priority: str | None = None`,
  `node_count: int | None = None`(:13-18). `validate_batch(body.operation,
  body.max_concurrency, body.items, priority=body.priority, node_count=body.node_count)`
  (:25). `repos.batches.create(..., priority=body.priority, node_count=body.node_count)`
  (:31-34).
- [ ] **Step 3**: `... -m pytest tests/test_api_batches.py tests/test_api_batch_cancel.py -q` → GREEN.
- [ ] **Step 4**: 커밋 — `src/dms/api/routes_batches.py tests/test_api_batches.py`

---

### Task 4: 오케스트레이터 — 자식 request 로 전달

**Files:** `src/dms/batch_orchestrator.py`, `tests/test_batch_orchestrator_scan.py`

- [ ] **Step 1 (RED)**: `test_batch_orchestrator_scan.py` 추가 —
  ① `create(..., priority="high")` 배치 run_once → materialize 된 자식
  `requests.get(rid)["priority"] == "high"` ② `node_count=4` 배치 → 자식
  `payload["node_count"] == 4` ③ 미지정 배치 → 자식 payload 에 `"node_count"` 키
  **부재** + priority 는 기존 경로(정책 없음 폴백 "mid") — 기존 테스트(:11-22) 형식
  재사용.
- [ ] **Step 2 (GREEN)**: `_materialize`(:62-73) 두 줄 —
  `resolve_priority(self._repos, batch["operation"], batch.get("priority"))`(:65) /
  build 후 `if batch.get("node_count") is not None: payload["node_count"] = batch["node_count"]`
  (**null≠0 주석** + 전제 #6: build_data_payload 주입 금지 이유 주석 — 단건 payload
  계약·resource_key 불변).
- [ ] **Step 3**: `... -m pytest tests/test_batch_orchestrator_scan.py tests/test_batch_orchestrator_sync.py tests/test_batch_cancelled_item.py tests/test_default_priority.py tests/test_domain_batch.py -q` → GREEN
  (test_domain_batch 재실행 = payload 정확 일치 단언이 안 깨졌다는 증거).
- [ ] **Step 4**: 커밋 — `src/dms/batch_orchestrator.py tests/test_batch_orchestrator_scan.py`

---

### Task 5: planner·placement — min(정책, 요청) 캡 + 방어 파싱

**Files:** `src/dms/placement.py`, `src/dms/planner.py`, `tests/test_placement.py`,
`tests/test_planner.py`

- [ ] **Step 1 (RED)**: `test_placement.py` — `resolve_fanout(POLICY, {...},
  priority="mid", requested_node_count=2)` 에서 node_count = min(후보수, 정책
  max_nodes, 2) / 요청값 > 정책이면 정책이 이긴다 / `None` 이면 기존과 동일 /
  sync 중첩 후보에서 면당 캡 적용. `test_planner.py` — payload 에 `node_count` 실은
  요청이 계획되면 `worker_pool["node_count"]` 캡 반영·candidates 슬라이스(:191-196)
  축소 / payload `node_count="8"`(변조 시뮬레이션: repo create 로 직접 삽입) →
  `rejected:invalid_node_count` + results 기록(`set_state_with_result` 경로).
- [ ] **Step 2 (GREEN)**:
  - `resolve_fanout`(:113-131): `*, priority, requested_node_count=None` —
    `if requested_node_count is not None: max_nodes = min(max_nodes, requested_node_count)`
    (주석: 요청은 정책을 **줄일 수만** 있다 — DB 변조로도 정책 초과 불가. sync 는
    면당 상한 의미 자리라 동일 적용).
  - planner `_plan_one` step 6(:182-188) 직전: `requested = payload.get("node_count")`
    → 있는데 비int/bool/<1 이면 `self._reject(rid, "invalid_node_count")`(fail-closed
    — stepper 층1 관례 주석), 아니면 `resolve_fanout(..., requested_node_count=requested)`.
- [ ] **Step 3**: `... -m pytest tests/test_placement.py tests/test_planner.py tests/test_controller_planner.py tests/test_execution_manifests.py -q` → GREEN
  (execution_manifests: process_count/node_count 파생 산식(:153-157) 회귀 확인).
- [ ] **Step 4 (뮤테이션 1건)**: min 캡을 `max` 로 오염(`cp src/dms/placement.py
  /tmp/slice32-placement.bak` 백업 — **git checkout 원복 금지, 사본 원복**) →
  test_placement RED 확인 → 원복 GREEN.
- [ ] **Step 5**: 커밋 — `src/dms/placement.py src/dms/planner.py tests/test_placement.py tests/test_planner.py`
- **스코프 판단(근거 명기)**: 단건 제출(SubmitJob/SubmitScan)에는 node_count 를 이번에
  **노출하지 않는다**. 근거: ① 성장 모니터링 유스케이스는 배치다 ② planner 측 구현은
  payload 범용이라 이후 단건 노출은 routes_requests `_validated_payload` + UI 한 필드
  배선뿐(구조 부채 없음) ③ 스코프 최소 원칙. BACKLOG 후보로 기록.

---

### Task 6: `:rescan` — 전체 재실행 API

**Files:** `src/dms/api/routes_batches.py`, `tests/test_api_batches.py`,
`frontend/src/lib/reasonCodes.json`, `frontend/src/lib/api.ts`

- [ ] **Step 1 (RED)**: `test_api_batches.py` —
  ① Completed 배치(repo 로 item Succeeded/Failed 만들고 set_status) `:rescan` →
  200 `{"status":"Running","requeued":n}` + 전 item Queued + 카운터 0/0
  ② sync Completed 배치 → `{"status":"Previewing"}` ③ Running 배치 → 409
  `batch_not_rescannable`(**활성 자식 이중 실행 방지** — 전제 #14 주석)
  ④ PreviewReady → 409 ⑤ Cancelled → 200(전제 #15) ⑥ 404 배치 → `batch_not_found`
  ⑦ maintenance 모드 거부(기존 `reject_when_maintenance` 관례 — :rerun-failed :67 동일).
- [ ] **Step 2 (GREEN)**: `routes_batches.py` 에 `:rerun-failed`(:65-76) 아래
  `@router.post("/api/admin/batches/{batch_id}:rescan")` —
  `reject_when_maintenance` → 404 가드 → `if b["status"] not in ("Completed","Cancelled"):
  raise HTTPException(status_code=409, detail="batch_not_rescannable")`(키워드 리터럴 —
  AST 추출 그물 안) → `n = repo.reset_all_items(batch_id)` →
  `status = "Running" if b["operation"] == Operation.SCAN.value else "Previewing"`
  (create_batch :30 과 같은 분기) → `repo.set_status` → 반환. 주석: 성공 item 포함
  전체 리셋 이유(성장 모니터링 — 같은 대상 재스캔), 종단 배치 한정 이유(거짓 취소
  방지 선례 정합 — 종료할 실행면이 없음을 가드로 보장).
- [ ] **Step 3**: `batch_not_rescannable` 을 json+REASON_MESSAGES 등록("재실행할 수
  없는 상태의 배치입니다" 류). Run: `... -m pytest tests/test_api_batches.py
  tests/test_reason_codes_coverage.py -q` + `cd frontend && npx vitest run src/lib` → GREEN.
- [ ] **Step 4**: 커밋 — `src/dms/api/routes_batches.py tests/test_api_batches.py frontend/src/lib/reasonCodes.json frontend/src/lib/api.ts`

---

### Task 7: csv.ts 재설계 — 경로 전용 파서 + 내보내기

**Files:** `frontend/src/lib/csv.ts`, `frontend/src/lib/csv.test.ts`

**Interfaces:** 기존 「연산별 고정 헤더」 계약(`csv.ts:1-4`)은 폐기 — 스토리지가 배치
레벨로 올라가 CSV 는 경로만 나른다.

```ts
export interface ScanRow { target: string }
export interface SyncRow { source: string; destination: string }
export function parseItemsCsv(operation: "scan"|"sync", text: string):
  { rows: (ScanRow|SyncRow)[]; errors: string[] }
// scan: 행당 1열(target). sync: 행당 2열(source,destination — 콤마 구분).
// 헤더 유연: 첫 줄 셀이 전부 알려진 헤더 토큰(대소문자 무시: target|path /
// source,destination)이면 스킵, 아니면 데이터로 취급. 빈 줄 무시, 셀 trim.
// 열 수 불일치·빈 셀은 "N행: ..." 오류로 수집(조용한 드랍 금지 — 기존 계약 유지).
export function serializeItemsCsv(operation: "scan"|"sync", rows): string
// 내보내기: 헤더 포함(target / source,destination) — 재붙여넣기 왕복이 성립해야 한다.
```

- [ ] **Step 1 (RED)**: `csv.test.ts` 전면 재작성 — scan 1열(헤더 유/무 양쪽) /
  sync 2열 / 헤더 토큰 대소문자 / 빈 줄 무시 / 열 수·빈 셀 오류(행 번호 포함) /
  빈 입력 오류 / **왕복**: `parse(serialize(rows)) == rows` / 경로에 콤마가 든 scan
  행은 오류(1열 계약 — 침묵 분할 금지).
- [ ] **Step 2 (GREEN)**: 구현. `parseBatchCsv` 는 삭제(사용처는 BatchCreate 뿐 —
  T9 에서 함께 갈린다. tsc 가 남은 참조를 잡는다).
- [ ] **Step 3**: `cd frontend && npx vitest run src/lib/csv.test.ts` → GREEN.
  (`npx tsc -b` 는 BatchCreate 가 새 파서로 갈아탈 T9 전까지 빨갈 수 있다 — T7·T9 를
  같은 흐름에서 연속 실행하고, 중간 커밋은 T9 완료 후 함께 한다.)

---

### Task 8: 옵션 미러 추출 — optionRules.ts (SubmitJob 무손상)

**Files:** `frontend/src/features/jobs/optionRules.ts`(신설),
`frontend/src/features/jobs/SubmitJob.tsx`(import 줄만)

- [ ] **Step 1**: `optionRules.ts` 신설 — `CHMOD_RE`·`CHOWN_RE`·`intFieldError`
  (`SubmitJob.tsx:36-48` 원문 이동, domain.py:112-113·127-128 미러 주석 보존).
  SubmitJob 은 import 로 교체(렌더·산식 무변경).
- [ ] **Step 2 (게이트)**: `npx vitest run src/features/jobs && npx tsc -b` —
  **SubmitJob.test 무수정 초록** = 이사가 무해했다는 증거(슬라이스 31 T3 선례).
- [ ] **Step 3**: 커밋(T7 결과물 포함 가능 시점이면 함께) —
  `git add frontend/src/features/jobs/optionRules.ts` 후
  `git commit -- frontend/src/features/jobs frontend/src/lib/csv.ts frontend/src/lib/csv.test.ts`

---

### Task 9: BatchCreate 위저드 — 4스텝·입력 4방식·실행 제어

**Files:** `frontend/src/features/batches/BatchCreate.tsx`(전면 개편),
`frontend/src/features/batches/BatchCreate.test.tsx`(전면 개편),
`frontend/src/features/batches/useBatches.ts`(CreateBatchBody 확장)

**UI 설계 (위저드 — 전제 #12 근거):** STEPS = 연산 → 대상·항목 → 실행 제어 → 확인·제출.
폼 상태는 위저드 밖 단일 useState(SubmitJob 관례, `SubmitJob.tsx:67-70`).

1. **연산**: scan/sync 선택(기존 select 유지).
2. **대상·항목**: 배치 레벨 스토리지 — scan 은 `StoragePicker` 1개, sync 는
   소스/목적지 짝(`formFields.tsx` 재사용, `useUserStorages` — 전제 #16). 항목 입력은
   탭 3개 + 내보내기:
   - **테이블 편집**(기본): 행 = scan 경로 1칸 / sync source·destination 2칸,
     행 추가/삭제 버튼(aria-label "행 추가"/"N행 삭제").
   - **CSV 붙여넣기**: textarea + "테이블에 반영" 버튼 → `parseItemsCsv` → rows 교체,
     오류는 `text-bad` 목록(기존 "N행: ..." 문구 관례).
   - **파일 업로드**: `<input type="file" accept=".csv,.txt">` → FileReader.readAsText
     → 동일 파서 경로(airgap 무관 — 로컬 읽기만).
   - **CSV 다운로드**: `serializeItemsCsv` → Blob → `URL.createObjectURL` 앵커
     다운로드(`batch-items.csv`). **clipboard API 금지**(전제 #10 — http 비보안
     컨텍스트에서 부재. 주석으로 박는다).
   canNext: rows ≥ 1 + 파스 오류 0 + 스토리지 선택됨.
3. **실행 제어**: 연산별 옵션(scan = top_k/verbose/quiet — SubmitScan.tsx:69-88 미러,
   verbose·quiet 상충 즉답 / sync = delete·contents·direct·quiet + 고급
   open_noatime·batch_files·bufsize·chmod·chown — SubmitJob 옵션 스텝 미러,
   `optionRules.ts` 재사용) + 우선순위 select(**"(정책 기본)" 빈 옵션이 기본값** —
   빈값이면 바디에서 생략 = 기존 동작) + 노드 수 input(빈값 = 생략 = 정책값, 1..64
   즉답 검증 — 서버 1024 위생 상한의 보수적 부분집합) + 동시 실행 상한(min 1 max 64)
   + 메모. canNext: 옵션 국소 오류 없음(SubmitJob 의 optionsInvalid 관례,
   `SubmitJob.tsx:90-93`).
4. **확인·제출**: InfoPanel 요약(연산·스토리지·항목 수·옵션 JSON·우선순위·노드 수·
   동시 상한) — **제출 바디와 같은 함수에서 파생**(화면 거짓말 금지 주석 관례,
   `SubmitJob.tsx:292-293`). 제출 성공 시 `nav(/admin/batches/{id})` 유지.

바디 조립(제출 시 items 조립 — API 스키마 유지):
```ts
items = op === "scan"
  ? rows.map((r) => ({ storage, target: r.target }))
  : rows.map((r) => ({ source_storage: srcStorage, source: r.source,
                       destination_storage: dstStorage, destination: r.destination }));
body = { operation, max_concurrency, options, note: note || null, items,
         ...(priority !== "" && { priority }),
         ...(nodeCount.trim() !== "" && { node_count: Number(nodeCount) }) };
```
`CreateBatchBody`(`useBatches.ts:12-15`)에 `priority?: string; node_count?: number` 추가.

- [ ] **Step 1 (RED)**: `BatchCreate.test.tsx` 전면 재작성(msw 로
  `/api/user/storages`·`POST /api/admin/batches` 물림) —
  ① scan: 스토리지 선택 + 테이블 2행 입력 → 다음×2 → 제출 → 바디
  `{operation:"scan", items:[{storage:"s1",target:"a"},{storage:"s1",target:"b"}],
  options:{}, ...}` + priority/node_count **키 부재**(미지정 = 생략 계약)
  ② scan 옵션: top_k=100·quiet 체크 + 우선순위 high + 노드 수 4 →
  바디 `options:{top_k:100,quiet:true}, priority:"high", node_count:4`
  ③ sync: CSV 붙여넣기 "source,destination\na,b" 반영 → 바디 짝 조립 확인
  ④ CSV 오류 행이 있으면 "다음" 비활성 ⑤ verbose+quiet 상충 문구 + 다음 비활성
  ⑥ 파일 업로드: File 객체 주입 → 테이블 반영(jsdom FileReader 동작 확인)
  ⑦ 제출 성공 내비게이션(기존 계약 유지).
- [ ] **Step 2 (GREEN)**: 구현(위 스케치). Wizard/Stepper/InfoPanel/StoragePicker/
  optionRules 재사용 — 신규 공용 컴포넌트 없음.
- [ ] **Step 3**: `npx vitest run src/features/batches && npx tsc -b` → GREEN.
- [ ] **Step 4 (뮤테이션 1건)**: items 조립에서 배치 레벨 storage 대신 첫 행만 쓰도록
  오염(`cp` 백업 — git checkout 원복 금지) → 바디 단언 RED → 원복 GREEN(조립이
  계약임의 증명).
- [ ] **Step 5**: 커밋 — `frontend/src/features/batches frontend/src/lib/csv.ts frontend/src/lib/csv.test.ts` (T7 미커밋분 포함)

---

### Task 10: BatchDetail — 전체 재실행 버튼 + 타입

**Files:** `frontend/src/features/batches/BatchDetail.tsx`, `useBatches.ts`,
`BatchDetail.test.tsx`, `frontend/src/lib/types.ts`

- [ ] **Step 1 (RED)**: `BatchDetail.test.tsx` 추가 — status Completed 에서 "전체
  재실행" 버튼 노출 + 클릭 시 `POST /api/admin/batches/b1:rescan` 발사(기존 waitFor
  플레이키 방지 주석 관례 :29-31) / Cancelled 에서도 노출 / Running 에선 부재.
- [ ] **Step 2 (GREEN)**: `useRescanBatch = _action(id, "rescan")`(`useBatches.ts:27-29`
  한 줄 추가). BatchDetail 버튼 행(:24-28)에
  `{(b?.status === "Completed" || b?.status === "Cancelled") && <Button ...>전체 재실행</Button>}`
  — 기존 "실패분 재실행"(Completed+failed>0) 은 유지(공존 허용: 실패만 vs 전체).
  `types.ts` Batch(:83-87)에 `priority?: string | null; node_count?: number | null`
  (옵션 필드 — 기존 fixture 무수정 컴파일).
- [ ] **Step 3**: `npx vitest run src/features/batches && npx tsc -b` → GREEN.
- [ ] **Step 4**: 커밋 — `frontend/src/features/batches frontend/src/lib/types.ts`

---

### Task 11: 마감 게이트 — 전체 스위트 + 무접촉 증명

- [ ] **Step 1**: 백엔드 전체 —
  `PYTHONPATH=.../src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests -q`
  (오케스트레이터가 돌린다. 이 슬라이스의 직접 대상: test_domain_batch ·
  test_migrations_batch · test_migrations · test_repo_batches · test_api_batches ·
  test_api_batch_cancel · test_batch_cancelled_item · test_batch_orchestrator_scan/sync ·
  test_placement · test_planner · test_controller_planner · test_default_priority ·
  test_reason_codes_coverage · test_execution_manifests). Expected: T0 기준선 + 신규
  전부 초록.
- [ ] **Step 2**: `cd frontend && npx vitest run && npx tsc -b && npm run test:e2e` —
  vitest 기준선+신규, tsc 무출력, e2e **9 passed 무변경**(전제 #9).
- [ ] **Step 3 (무접촉 grep 게이트)**:
  ```
  git diff --stat -- src/dms_job_runner src/dms/agent deploy   # → 0 (러너·에이전트·배포 무접촉)
  git diff --stat -- legacy                                     # → 0
  grep -rn "navigator.clipboard" frontend/src                   # → 0 (전제 #10)
  ```

---

## 플랜 이후: 배포·실증 (플랜 태스크 아님 — 순서가 계약이다)

1. **제어면만 d47** — 러너(`src/dms_job_runner/`)·에이전트(`src/dms/agent/`) 무변경을
   T11 grep 으로 증명했으므로: `deploy/k8s` 의 `dms:d46` **5곳만** d47 bump
   (`30-migrate-job.yaml:25`, `40-api.yaml:67·84`, `41-controller.yaml:35·52`).
   에이전트 `50-agent-daemonset.yaml:72`(dms-agent:d46)·잡 이미지(mpifileutils)는
   **무접촉**. 매니페스트-우선: bump 커밋(`git commit -- deploy/k8s`) 후 **그 커밋에서**
   클러스터 내 빌드(build_build_pod 방식) → push → 적용.
2. **migrate 필수**: batches 새 컬럼 2개 — 30-migrate-job 이 `_ensure_columns` ALTER 를
   태운다(구형 DB 경로가 T2 에서 테스트됨). migrate 완료 후 api·controller 롤아웃.
3. **실증 시나리오**: 배치 생성 위저드로 scan 배치(단일 스토리지·CSV 붙여넣기·
   top_k·priority=high·node_count=2) 제출 → BatchDetail 에서 자식 우선순위/노드 수
   반영 확인(잡 상세 worker_pool) → 완료 후 "전체 재실행" → 전 item 재큐잉 확인.
4. 승인 후 CHANGELOG·BACKLOG 갱신(BACKLOG 후보: 단건 제출 node_count 노출,
   BatchesList 에 priority/node_count 컬럼 표시, held 파킹/선택 실행 별도 설계).

## 열린 질문 (기본값으로 진행 가능)

1. **max_concurrency·node_count 상한 64**: 임의 위생값이다(정책 max_nodes 가 실제
   캡). 운영 선호가 다르면 숫자만 조정(검증·테스트 각 1곳).
2. **rescan 버튼 문구 "전체 재실행"**: ":rescan" 동사와 화면 문구의 번역 — "재스캔"
   을 선호하면 sync 배치(재프리뷰)와 문구가 어긋나므로 "전체 재실행" 을 기본으로 했다.
3. **Cancelled 배치 rescan 허용**: 기본 허용(전제 #15). 운영상 Completed 만 원하면
   가드 한 줄 축소.
