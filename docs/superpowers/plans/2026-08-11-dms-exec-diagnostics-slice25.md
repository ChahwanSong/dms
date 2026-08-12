# 슬라이스 25 — 실행 단계 진단 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 실행 단계 진단의 두 구멍 — ① vcjob(launcher) 로그를 read_log 가 명시적으로 거절해(409 `log_not_available`) 러너가 아티팩트를 쓰기 전에 죽는 실패의 유일한 증거를 볼 수 없고, ② 그 유일한 사본(파드 로그)을 pod GC 86400s·vcjob TTL 86400 이 파괴한다 — 를 닫는다. (1) `read_log("vcjob/…")` 를 라벨 셀렉터(`volcano.sh/job-name=`)로 열고 반환 계약을 `(pod, log, waiting_reason)` 3-튜플로 확장, (2) 실패 종단 4경로(preflight_failed / execution_failed·TIMED_OUT / preview_failed·preview_timed_out / execution_recheck_failed)에서 스테퍼가 로그를 `data_jobs.diag_logs`(새 컬럼 1개, write-once, 파드당 16KB·항목 4·총 ≤64KB)에 박제, (3) `/logs` 라우트가 라이브 우선·박제 폴백(`source: "live"|"archived"`), (4) 실패 잡도 summary 가 있으면 `set_artifact`(returncode 카드·아티팩트 URI 표면화). 새 pip/npm 의존성 0, 새 테이블 0(**컬럼 1개**), 새 사유 코드 0(`poll_failed` 재사용 — json/REASON_MESSAGES **무변경**). pod GC·vcjob TTL 값은 무변경 — 이제 "라이브 열람 여유 창"일 뿐이며 20-config.yaml 의 "유일한 사본" 주석을 사실에 맞게 갱신한다.

**Architecture:** 스키마 → 어댑터 → 리포지토리 → 스테퍼 → 라우트 → 화면 순으로 쌓는다. (1) `migrations.py` — `diag_logs TEXT` 를 CREATE TABLE 과 `_ensure_columns` **양쪽**에(슬라이스 14 실 500 교훈). (2) `execution_volcano.read_log` vcjob 분기 — `list_pod_briefs`(구현은 이미 있음, Protocol 에만 없음)로 launcher 항상 + Failed 파드만, per-pod 실패는 기존 None 접기, **list 호출 예외는 `poll_failed` 409**(403 이 "로그 없음"으로 렌더된 사고의 교훈을 반복하지 않는다). (3) `data_jobs.archive_diag_logs` — `WHERE diag_logs IS NULL` write-once(`mark_exec_submitted` 선례) + 다행 조회 4곳(list_jobs·claim_steppable·terminal_jobs_older_than·succeeded_scans)을 명시 컬럼 목록으로 바꿔 diag_logs 제외(builds I2 선례; get_job 단행은 SELECT * 유지 — /logs 폴백이 쓴다). (4) `stepper._finalize(diag=(phase, ref))` — **박제 → set_job_state 순서가 계약**: 박제 후 크래시하면 다음 틱이 finalize 를 재시도하고(IS NULL 이 중복을 막는다), 역순이면 종단 잡은 다시 스텝되지 않아 박제 기회가 영영 없다. 박제 실패는 `diag_archive_failed` 이벤트로 표면화하되 종단 전이를 막지 않는다. (5) `/logs` — 라이브 빈 목록/전항목 null 이면 박제 폴백, 깨진 diag JSON 은 폴백 포기+경고(지어내지 않는다). (6) JobViewer — 구조 무변경, execution 탭이 처음으로 내용을 갖는다(waiting_reason 병기·archived 캡션·잘림 배지 재사용).

**Tech Stack:** Python 3.11 표준 라이브러리(신규 import 는 routes_artifacts 의 `json` 뿐), FastAPI 라우트, 프론트는 types.ts + JobViewer.tsx(+테스트). DB 는 새 컬럼 1개 — `tests/test_migrations.py` 의 `len(ALL_TABLES) == 20` 은 **테이블 수**라 컬럼 추가에 안 걸린다(Task 1 이 실측 확인).

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-08-11-dms-exec-diagnostics-slice25-design.md`. 플랜과 충돌하면 **설계가 이긴다**(단, 아래 「설계 §1 전제 재확인」의 정정은 이 플랜이 실측으로 갱신한 사실이다 — 특히 슬라이스 24 가 stepper 를 크게 바꿨다).
- **새 pip/npm 의존성 금지. 새 DB 테이블 금지 — 컬럼은 `data_jobs.diag_logs` 정확히 1개.** 컬럼은 CREATE TABLE(`migrations.py:133` 블록)과 `_ensure_columns`(`:453-491`) **양쪽**에 넣는다 — 슬라이스 14 의 실 프로덕션 500(`column "files_count" does not exist`)이 한쪽만 넣은 대가였다. `tests/test_migrations.py::test_migrate_is_idempotent` 의 `len(ALL_TABLES) == 20` 은 테이블 수 단언이라 그대로 초록이어야 한다.
- **신설 사유 코드 0 이어야 한다** — `frontend/src/lib/reasonCodes.json`·`api.ts` REASON_MESSAGES 는 **무변경**이다(`poll_failed`(json:32)·`log_not_available`(json:24) 재사용, 실측 기등록). Task 8 이 `git diff` 로 실측 확인한다. 만약 구현 중 새 리터럴이 필요해지면 그것은 설계 위반이니 중단하고 보고한다. (JSON 재포맷 금지 원칙은 이 슬라이스에선 "아예 안 건드린다"로 충족된다.)
- **null(모름)과 실패를 섞지 않는다. 이 슬라이스의 심장이다**: ① 빈 로그(`""`)는 정상값이고(§1-3 의 launcher 실측) null(로그를 얻을 수 없음)과 다르다 — 화면·박제 어디서도 `if (log)` 같은 truthy 검사 금지, 반드시 `log === null`/`is None`. ② **전 항목 log=None 이어도 박제한다** — "박제 시점에 이미 없었다"는 사실 자체가 진단이다. ③ waiting_reason(왜 없는지)은 별 채널이다 — null 을 합성 문자열로 뭉개지 않는다. ④ 라이브 빈 목록(`[]`)도 폴백 조건이다(0 항목은 "파드 전멸"이라는 정보다).
- **로그 상한은 테스트로 고정한다**: 파드당 꼬리 16KB(`truncated: true`)·항목 최대 4(launcher 우선)·총 ≤64KB = builds `LOG_TEXT_MAX` 와 동일 총량. 상한 없는 박제는 DB 를 부풀린다 — `DIAG_MAX_ENTRIES * DIAG_TAIL_BYTES == LOG_TEXT_MAX` 를 단언하는 테스트가 Task 4 에 있다.
- **커밋은 pathspec 으로 한정한다**: 신규 파일만 `git add <파일>` 선행 후, 항상 `git commit -m "..." -- <경로들>` 형태. `git add -A`·`git add .`·`git commit -a` **금지**(워크트리 공유 중 인덱스 섞임 사고).
- **origin push 금지, 브랜치 변경 금지(현재 `worktree-dms-slice22plus`, HEAD 63c2cbd = origin/main), 플랜 태스크에서 `deploy/k8s` 의 이미지 태그 변경 금지**(d36 범프는 「플랜 이후: 배포·실증」의 첫 단계). 단 **`deploy/k8s/20-config.yaml` 의 주석 갱신은 예외**다 — 설계 §2.3 이 `:74-77` "유일한 사본" 주석이 사실이 아니게 되므로 함께 갱신하라고 명시한다(Task 6, 값 무변경·주석만). `docs/` 아래는 이 플랜 파일 외 생성·수정 금지(실증 후 BACKLOG 갱신은 플랜 밖 관례).
- `legacy/` 아래는 읽기 전용 — 수정·이동·삭제·추가 금지, import 금지, 코드 복사 금지.
- 백엔드 테스트 명령(이 워크트리 전용, `.venv` 는 워크트리 밖 공용):
  `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest <대상> -q`
  전체 스위트는 **포그라운드**, Bash timeout 900000ms. **기준선 1189 passed(402s).**
- 프론트: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run`(**기준선 228 passed / 49 files**), 타입체크 `npx tsc -b`.
- **e2e(슬라이스 23 신설)**: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npm run test:e2e`(**기준선 9 passed**, ~30s, dist 빌드 포함). **영향 판단(실측)**: e2e 시나리오 E1~E6 은 `/logs` 도 JobViewer 로그 탭도 건드리지 않고(`grep -rn "logs\|JobViewer" frontend/e2e/*.spec.ts` → 0건), 이 슬라이스의 API 변경은 전부 가산적(새 필드)이다 — **e2e 수정 불요, 9 passed 유지가 기대값**이다. 단 e2e 는 앱 코드를 못 바꾸는 계약이 아니므로(슬라이스 23 한정 계약), 만에 하나 빨개지면 고쳐도 된다 — Task 8 이 게이트다.
- 주석은 **한국어**로 「왜」를 적는다.

## 설계 §1 전제 재확인 (2026-08-12, 코드 직접 실측)

설계는 2026-08-11 에 쓰였고 그 사이 슬라이스 22(4e13cda~beed7b2)·24(caea739~a41c9b9)·23(904cb1d~63c2cbd)이 들어갔다. 12개 항목 전부 재확인 — **결론 요지는 전부 유지**되고, 정정은 라인 드리프트 다수 + 구조 변화 1건(슬라이스 24 의 stepper 개편) + 판단 필요 신규 사실 2건이다.

| 설계 §1 항목 | 재확인 결과 |
|---|---|
| 1. read_log 의 vcjob 명시 거절·409 매핑 | ✓ 유지. 거절 `execution_volcano.py:247-249`(주석 244-246 — 설계 표기 243-249 그대로), 409 매핑 `routes_artifacts.py:58-61`. Volcano 파드 명명(`<vcjob>-<task>-<index>`)·launcher replicas=1 은 라이브 실측 항목이라 코드로 재검증 불가 — 슬라이스 24 실증(d35)에서 반증 없음, 유지 |
| 2. Aborted vcjob 은 Failed 파드만 잔존 | 라이브 실측 전제 — 코드 무관, 유지. 워커 라벨(`volcano.sh/job-name`) 실측은 §6-4 에서 마감(설계 지시 그대로) |
| 3. launcher 파드 로그 비어 있음·러너 출력 삼킴 | ✓ 유지, 라인 드리프트: `capture_output` 은 `runner.py:168-169`(설계 149-150), 아티팩트 쓰기 `:100-106`(설계 81-87), 프리플라이트 마커 `execution_manifests.py:262-297` `_preflight_script`(설계 267-291) — **슬라이스 24 가 러너 머리에 층3 allowlist(`runner.py:14-44`)를 추가해 밀렸다**. 신규 사실: 층3 거부(`:36-44`)는 stderr 마커+summary.json 을 쓰고 exit 1 — 이 실패는 summary 아티팩트가 **있다**(§2.4 의 수혜 경로) |
| 4. launcher 무라벨·volcano 자기 라벨·list_pod_briefs 존재/Protocol 부재 | ✓ 유지. launcher 템플릿 metadata 없음(`execution_manifests.py:343-347` nsync·`:385-392` primary — 설계 337-341/379-386 표기 드리프트), 워커만 `_worker_task_metadata`(`:238-241`, 설계 232-235). `list_pod_briefs` 는 `execution_volcano.py:400-425` **그대로**(무드리프트, phase·waiting_reason 포함), `K8sClient` Protocol(`:61-67`)에 없음 ✓ |
| 5. vcjob TTL 부착·집행 미실측 | ✓ 유지. `DMS_VCJOB_TTL_SECONDS` 는 `config.py:24`(int 키)·`:136`(필드, 설계 표기 130), `20-config.yaml:69`, `_apply_ttl`(`execution_manifests.py:223-229`, 설계 217-223). pod_gc 는 `pod/`·`pods/` ref 만(`pod_gc.py:31`) ✓. 집행 실측은 §6-4 그대로 남는다 |
| 6. 프리플라이트 실패 사본은 파드 로그 하나·stepper 는 generic 접기 | ✓ 유지, **최대 정정 — 슬라이스 24 stepper 개편으로 전 라인 이동**: preflight_failed 는 `:165` → **`stepper.py:236`**. 실패 종단 4경로 현행: preflight_failed `:236` / execution_failed·TIMED_OUT `:293-295` / preview_timed_out `:334`·preview_failed `:336` / execution_recheck_failed `:365`. `_finalize` 는 `:119-124`. **신규 구조 2건**: ① `_step_one` 이 층1 가드+`_dispatch`(`:174-208`)로 분리 — 이 슬라이스에 유리하다(트리거는 각 poll 함수의 finalize 호출에 `diag=` 인자만 얹으면 되고 `_dispatch` 는 무접촉). ② 슬라이스 24 가 종단 경로 2건을 추가했다: `unknown_tool`·`storage_missing_at_step`(`_fail_closed` `:155-172`) — **박제 비대상으로 판정한다**(근거는 아래 「추가 정정」) |
| 7. 실패 잡 아티팩트 라우트 동작·set_artifact 성공 경로만 | ✓ 유지, 드리프트: 성공 경로 set_artifact 는 `stepper.py:287-290`(설계 216-220), preview 는 set_preview `:327-329`(설계 257-258). `routes_artifacts.py:12-17,27,38` ✓. 남는 구멍 2건(러너 도달 전 실패·실패 잡 summary 미기록) 그대로 |
| 8. execution 로그 탭 기렌더·409 문구 | ✓ 유지. `JobViewer.tsx:46-53`(phase_refs 탭)·`api.ts:66` ✓. null 문구는 `JobViewer.tsx:102-104` ✓ |
| 9. builds 박제 선례·write-once 선례 | ✓ 유지. `LOG_TEXT_MAX` `builds.py:10`, finish 꼬리 자르기 `:130-141`, 목록 I2 `:93-103`, `mark_exec_submitted` IS NULL `data_jobs.py:222-225`(설계 222-225 ✓) |
| 10. data_jobs 전부 SELECT * | ✓ 유지: get_job `:96-98`·succeeded_scans `:100-115`·list_jobs `:117-127`·claim_steppable `:193-201`·terminal_jobs_older_than `:305-327` 전부 설계 라인 그대로. **슬라이스 24 신규**: `terminal_jobs_with_live_request`(`:329-351`, LIMIT 200+오래된순)도 다행 조회지만 **SELECT 가 `d.job_id, d.request_id, d.state` 3컬럼 명시**라 diag_logs 를 실을 수 없다 — **변경 불요**(과제 지시의 확인 항목, 실측 완료) |
| 11. 취소는 파드 즉시 삭제 | ✓ 유지. `api/cancel.py:7-12`(호출자 `routes_jobs.py:71-87`) — Cancelled 는 박제 원본이 없다(§7 유지) |
| 12. metrics 합계는 Succeeded 만 | ✓ 유지. `repositories/metrics.py:145-147`(설계 144-147, 1행 드리프트) — 실패 잡에 typed 카운트가 실려도(§2.4) 집계 무오염 |

**추가 정정·판단(설계 본문·수치·슬라이스 24 이후의 신규 사실):**

- **슬라이스 24 신설 종단 경로 2건(`unknown_tool`·`storage_missing_at_step`)은 박제 비대상이다.** 근거: ① Pending 에서 종단되면 파드가 생성된 적이 없다 — 설계 §2.2 의 "submit 실패 계열은 파드가 없어 대상이 아니다"와 같은 축. ② 진행 중(Executing/Running) 종단이라도 `_fail_closed` 는 finalize **전에** 모든 phase_refs 를 best-effort terminate 한다(`stepper.py:163-170`) — 박제를 얹으려면 terminate 앞에 읽기를 끼워야 하는데, 이 두 경로의 증거는 파드 로그가 아니라 **DB 행 자체**(변조된 tool 값·사라진 storage 행)와 전용 이벤트다. 파드 로그는 정상 실행 중이던 잡의 로그라 실패 원인을 담지 않는다. ③ 슬라이스 24 실증 §6-3 이 이 경로의 진단 충분성(사유 코드+파드 0건)을 이미 증명했다. Task 4 가 이 판정을 테스트로 박제한다(`test_fail_closed_paths_do_not_archive`).
- **PreviewExpired·Cancelled 도 비대상**(설계 §7 그대로): expire_previews(`data_jobs.py:292-303`)와 cancel 은 stepper 실패 관측 경로가 아니고, cancel 은 원본을 즉시 지운다.
- §5 기준선 "백엔드 1131·프론트 228" → **백엔드 1189 passed(402s)·프론트 228/49·e2e 9**(슬라이스 22·24·23 반영, 2026-08-12 실측).
- **`migrations.py` 는 설계 표기와 라인까지 일치한다**(CREATE data_jobs `:133`, `_ensure_columns` `:453-491`) — 슬라이스 22~24 가 스키마 무변경이었기 때문. 컬럼 삽입 위치는 `sched_wait_seconds`(`:202`)와 `created_at`(`:203`) 사이.
- **`ExecutionAdapter` Protocol(`execution.py:46`)의 read_log 시그니처도 3-튜플로 갱신해야 한다**(설계는 스텁 `:84-85` 만 지목했지만 Protocol 주석이 2-튜플로 남으면 계약 문서가 거짓말한다). `set_log` 헬퍼(`:94-95`)는 pass-through 라 테스트가 3-튜플을 넣으면 끝.
- **`tests/test_api_job_logs.py::test_vcjob_ref_409_log_not_available` 은 이 슬라이스로 의미가 뒤집힌다** — vcjob 이 읽히게 되므로 무회귀 목록이 아니라 개편 대상이다(Task 2 가 "미지 prefix 방어" 테스트로 대체하고, Task 6 이 vcjob 라이브/박제 응답을 고정한다).
- 배포 태그: 설계 시점 이후 슬라이스 24 가 **세 이미지 전부 d35** 로 맞췄다(BACKLOG §0). 이 슬라이스는 러너(`dms_job_runner`)·에이전트 무접촉이므로 **`dms` 이미지만 d36** — `DMS_JOB_IMAGE`(20-config.yaml:22)·`dms-agent`(50-agent-daemonset.yaml:72)는 d35 유지. `tests/test_manifest_tags.py` 는 접두사만 단언하므로 부분 범프에 안 걸린다(실측).
- `poll_failed` 의 현행 문구는 "빌드 상태를 확인하지 못했습니다"(`api.ts:128`) — 빌드 문맥이지만 설계가 "새 사유 코드 0건, 재사용"을 명시해 그대로 둔다(열린 질문 1).

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/dms/migrations.py` (수정) | Task 1: `diag_logs TEXT` — CREATE TABLE + `_ensure_columns` 양쪽 |
| `tests/test_migrations.py` (수정) | Task 1: ALTER 경로 + CREATE 블록 선언 계약 |
| `src/dms/execution_volcano.py` (수정) | Task 2: Protocol `list_pod_briefs` + read_log vcjob 분기 + 3-튜플 + poll_failed |
| `src/dms/execution.py` (수정) | Task 2: Protocol 시그니처 + 스텁 3-튜플(클러스터 없이 초록) |
| `src/dms/api/routes_artifacts.py` (수정) | Task 2: 3-튜플 언팩(1줄). Task 6: source live/archived·waiting_reason·truncated·corrupt 방어 |
| `tests/test_execution_read_log.py` (수정) | Task 2: vcjob 선별·셀렉터·poll_failed·3-튜플 계약 |
| `src/dms/repositories/data_jobs.py` (수정) | Task 3: `archive_diag_logs`(write-once) + 다행 4곳 명시 컬럼 목록 |
| `tests/test_repo_diag_logs.py` (신규) | Task 3: write-once·JSON 형태·다행 제외·컬럼 패리티 계약 |
| `src/dms/stepper.py` (수정) | Task 4: 상한 상수·`_archive_diag`·`_finalize(diag=)`·4경로 배선. Task 5: 실패 잡 summary/artifact |
| `tests/test_stepper_diag_archive.py` (신규) | Task 4: 4경로 박제·상한·순서 계약·이벤트·비대상 판정 |
| `tests/test_stepper_artifact_uri.py` (수정) | Task 5: 실패 잡 set_artifact 유/무 계약 |
| `tests/test_api_job_logs.py` (수정) | Task 2(prefix 방어 대체)·Task 6(live/archived 응답 계약) |
| `deploy/k8s/20-config.yaml` (수정, 주석만) | Task 6: `:74-77` "유일한 사본" 주석 → 박제 이후의 사실로 |
| `frontend/src/lib/types.ts`, `frontend/src/features/jobs/JobViewer.tsx` (수정) | Task 7: source/waiting_reason/truncated 렌더 |
| `frontend/src/features/jobs/JobViewer.test.tsx` (수정) | Task 7: waiting_reason 병기·archived 캡션·잘림 배지·빈 로그 정상값 |

---

### Task 0: 기준선 확인 (커밋 없음)

**Files:** 없음(검증만)

**Interfaces:** 이후 모든 태스크의 판정 기준(기준선 초록)을 만든다.

- [ ] **Step 1: 백엔드 기준선**

Run(포그라운드, Bash timeout 900000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests -q`
Expected: `1189 passed` (약 400s)

- [ ] **Step 2: 프론트 기준선 + 타입체크**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b`
Expected: `Tests  228 passed` / `Test Files  49 passed`, tsc 무출력 exit 0.

- [ ] **Step 3: e2e 기준선**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npm run test:e2e`
Expected: `9 passed`(약 30s). 여기 빨강이면 이 슬라이스 밖의 문제다 — 진행 전에 보고.

---

### Task 1: 스키마 — `data_jobs.diag_logs` (CREATE + `_ensure_columns` 양쪽)

**Files:**
- Modify: `src/dms/migrations.py`
- Modify: `tests/test_migrations.py`

**Interfaces:**
- Produces: `data_jobs.diag_logs TEXT NULL` — JSON `{"phase", "at", "entries": [{"pod", "log", "truncated"}]}` 를 담는다. 빈 DB(CREATE)와 기배포 DB(`_ensure_columns` ALTER) 두 경로가 같은 선언형(TEXT)으로 수렴한다. Task 3 의 `archive_diag_logs` 와 Task 6 의 /logs 폴백이 이 컬럼을 소비한다.
- **함정 명시**: 이 저장소에서 컬럼 추가는 과거 실 프로덕션 500 을 냈다(`column "files_count" does not exist` — 슬라이스 14). 빈 DB 생성 경로(CREATE)와 기존 DB 업그레이드 경로(ALTER)는 **다른 코드**다. 한쪽만 넣었을 때 빨개지는 테스트: `_ensure_columns` 누락 → 아래 `test_migrate_adds_diag_logs_to_existing_data_jobs` 가 RED(이것이 실 500 의 재현이다). CREATE 누락 → 신규 DB 에서도 `_ensure_columns` 가 흡수해 PRAGMA 로는 구분 불가하므로 **소스 선언 계약 테스트를 새로 만든다**(아래 `test_diag_logs_is_declared_in_the_create_table_block`).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_migrations.py` — 파일 끝에 추가:

```python
def test_migrate_adds_diag_logs_to_existing_data_jobs(db):
    # 슬라이스 25: 기배포 DB 는 CREATE TABLE IF NOT EXISTS 를 다시 안 탄다 --
    # _ensure_columns 쪽을 빼먹으면 라이브에서만 컬럼이 없어, 첫 실패 종단의
    # 박제 UPDATE 가 500 을 낸다(슬라이스 14 files_count 실 사고의 재현 시나리오).
    # 구형 흉내는 슬라이스 20 까지의 전 컬럼을 갖춘 모양이다.
    db.execute("DROP TABLE data_jobs")
    db.execute("""CREATE TABLE data_jobs (job_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL, operation TEXT NOT NULL, tool TEXT,
        storage_name TEXT, source_storage TEXT, destination_storage TEXT,
        source TEXT, destination TEXT, target TEXT, options TEXT NOT NULL,
        priority TEXT NOT NULL, state TEXT NOT NULL, reason_code TEXT,
        preview_fingerprint TEXT, preview_expires_at TEXT, volcano_job_ref TEXT,
        artifact_uri TEXT, result_summary TEXT, files_count BIGINT,
        bytes_count BIGINT, worker_pool TEXT, precondition TEXT,
        confirmed_fingerprint TEXT, phase_refs TEXT, submit_wait_seconds BIGINT,
        exec_submitted_at TEXT, sched_wait_seconds BIGINT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    from dms.migrations import _column_exists, migrate
    migrate(db)
    assert _column_exists(db, "data_jobs", "diag_logs")
    assert _declared_type(db, "data_jobs", "diag_logs") == "TEXT"


def test_diag_logs_is_declared_in_the_create_table_block():
    # 신규 DB 에선 CREATE 누락도 _ensure_columns 가 흡수해 버려 PRAGMA 로는 두
    # 경로를 구분할 수 없다 -- "양쪽 선언" 규약(files_count 이후 전 컬럼의 관례)의
    # CREATE 쪽은 소스 자체로 고정하는 수밖에 없다. 이 테스트가 없으면 CREATE 쪽
    # 누락은 어떤 테스트로도 안 잡힌다.
    import inspect
    import re
    from dms import migrations
    src = inspect.getsource(migrations._apply_migrations)
    block = re.search(r'CREATE TABLE IF NOT EXISTS data_jobs.*?"""', src, re.S)
    assert block is not None
    assert "diag_logs" in block.group(0)


def test_all_tables_is_still_twenty_tables_not_columns():
    # len(ALL_TABLES) == 20 은 **테이블** 수 계약이다 -- diag_logs 는 컬럼이라
    # 여기 안 걸린다는 사실 자체를 명시로 남긴다(새 테이블 0 이 이 슬라이스의
    # 계약이고, 이 단언이 그 계약의 그물이다).
    assert len(ALL_TABLES) == 20
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_migrations.py -q`
Expected: FAIL 2건 / PASS 나머지 — `test_migrate_adds_diag_logs_...` 는 `assert _column_exists(...)` 에서, `test_diag_logs_is_declared_...` 는 `assert "diag_logs" in ...` 에서. `test_all_tables_is_still_twenty_...` 는 현행 고정 가드라 **즉시 PASS 가 맞다**.

- [ ] **Step 3: migrations.py 를 고친다**

**(1)** CREATE TABLE data_jobs 블록 — `sched_wait_seconds BIGINT,`(`:202`) 줄과 `created_at TEXT NOT NULL,`(`:203`) 줄 **사이**에 추가:

```sql
            -- 슬라이스 25(실행 단계 진단): 실패 종단 시 스테퍼가 파드 로그를 박제하는
            -- 자리. JSON {"phase", "at", "entries": [{"pod", "log", "truncated"}]} --
            -- 파드당 꼬리 16KB, 항목 최대 4, 총 <=64KB(builds.LOG_TEXT_MAX 와 같은
            -- 총량, stepper 상수와 계약 테스트가 강제). write-once 는 SQL 술어
            -- (IS NULL, archive_diag_logs)가 강제한다. NULL = 박제 없음(성공 종단/
            -- 배포 전 종단/박제 실패 -- 백필하지 않는다, 설계 §7). 다행 조회
            -- (list_jobs 등 4곳)는 이 컬럼을 절대 싣지 않는다 -- 5초 폴링에 최대
            -- 50x64KB 가 실리는 builds I2 의 그 문제를 재발시키지 않기 위해서다.
            diag_logs TEXT,
```

**(2)** `_ensure_columns` 의 튜플 — `("data_jobs", "sched_wait_seconds", "BIGINT"),`(`:471`) 줄 **바로 아래**에 추가:

```python
        # 슬라이스 25 진단 로그 박제 -- 기배포 DB 는 CREATE 를 다시 안 탄다(슬라이스
        # 14 의 실 500 교훈: 양쪽에 넣지 않으면 라이브에서만 컬럼이 없다).
        ("data_jobs", "diag_logs", "TEXT"),
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_migrations.py tests/test_migrations_batch.py tests/test_migrations_policy_seed.py tests/test_repo_data_jobs.py -q`
Expected: 전부 PASS (`test_migrate_is_idempotent` 의 `len(ALL_TABLES) == 20` 무접촉 초록 = 새 테이블 0 보증).

- [ ] **Step 5: 뮤테이션으로 이빨 확인 후 원복**

(a) `_ensure_columns` 에서 `("data_jobs", "diag_logs", "TEXT"),` 를 삭제 → `test_migrate_adds_diag_logs_to_existing_data_jobs` 가 RED(실 500 시나리오의 재현). (b) CREATE 블록에서 `diag_logs TEXT,` 를 삭제 → `test_diag_logs_is_declared_in_the_create_table_block` 가 RED(기능 테스트는 전부 초록인 채 — ensure 가 흡수하기 때문 — 소스 계약만이 잡는다는 것이 확인된다). 각각 확인 후 원복, Step 4 재확인.

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "feat(migrations): data_jobs.diag_logs TEXT — CREATE·_ensure_columns 양쪽(슬라이스 14 실 500 교훈), 진단 로그 박제 자리" -- src/dms/migrations.py tests/test_migrations.py
```

---

### Task 2: 어댑터 — `read_log` vcjob 분기 + 3-튜플 계약 + `poll_failed`

**Files:**
- Modify: `src/dms/execution_volcano.py`, `src/dms/execution.py`
- Modify: `src/dms/api/routes_artifacts.py`(언팩 1줄), `tests/test_execution_read_log.py`, `tests/test_api_job_logs.py`

**Interfaces:**
- Consumes: `list_pod_briefs`(구현 기존재 `execution_volcano.py:400-425` — name·phase·waiting_reason), `ExecutionError`.
- Produces (Task 4·6 이 소비한다):
  - `K8sClient` Protocol 에 `def list_pod_briefs(self, namespace: str, label_selector: str) -> list: ...` 추가.
  - `read_log(ref) -> list[tuple[str, str | None, str | None]]` — `(pod, log, waiting_reason)`. pod/pods 경로는 `waiting_reason=None`. vcjob 경로: 셀렉터 `volcano.sh/job-name=<name>`(ref 에 이름이 있어 vcjob GET 불요)로 브리프를 얻고 **launcher(이름에 `-launcher-` 포함) 항상 + 그 외는 phase == "Failed" 만**, launcher 가 목록 앞. per-pod 실패는 기존 계약 그대로 `(pod, None, waiting_reason)` 접기+경고. **list 호출 자체의 예외는 `ExecutionError("poll_failed", …)`**.
  - `StubExecutionAdapter.read_log` 기본값 `[(ref, "", None)]` — 클러스터 없이 초록(로컬·CI), `set_log` 는 3-튜플 pass-through.
  - 미지 prefix(`pod`/`pods`/`vcjob` 외)는 여전히 `log_not_available` 409 — 방어로만 남는다(문구 무변경).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

**(1)** `tests/test_execution_read_log.py` — `_FakeK8s` 를 다음으로 교체(브리프 능력 추가):

```python
class _FakeK8s:
    def __init__(self):
        self._logs = {}
        self._fail_pods = set()
        self._briefs = {}          # label_selector -> list[brief]
        self._briefs_error = None
        self.asked_selectors = []

    def read_pod_log(self, name, namespace):
        if name in self._fail_pods:
            raise RuntimeError("pod not found")
        return self._logs.get(name, "")

    def list_pod_briefs(self, namespace, label_selector):
        self.asked_selectors.append(label_selector)
        if self._briefs_error is not None:
            raise self._briefs_error
        return self._briefs.get(label_selector, [])

    def set_log(self, name, text):
        self._logs[name] = text

    def fail_log(self, name):
        self._fail_pods.add(name)

    def set_briefs(self, selector, briefs):
        self._briefs[selector] = briefs

    def fail_briefs(self, exc):
        self._briefs_error = exc
```

**(2)** 기존 4개 테스트의 2-튜플 단언을 3-튜플로 갱신(pod/pods 경로는 waiting_reason=None):

```python
assert a.read_log("pod/p1") == [("p1", "hello log", None)]
assert a.read_log("pods/p1,p2") == [("p1", "log1", None), ("p2", "log2", None)]
assert a.read_log("pods/p1,p2") == [("p1", "log1", None), ("p2", None, None)]   # missing/trace 2곳
```

**(3)** `test_read_log_rejects_vcjob_ref` 를 **미지 prefix 방어**로 교체하고, 파일 끝에 vcjob 분기 테스트를 추가:

```python
def test_read_log_rejects_unknown_prefix():
    # vcjob 은 이제 열렸다(슬라이스 25) -- 409 log_not_available 은 미지 prefix
    # 방어로만 남는다(설계 §2.5, 문구 무변경).
    a = _adapter(_FakeK8s())
    with pytest.raises(ExecutionError) as exc_info:
        a.read_log("widget/j1")
    assert exc_info.value.reason_code == "log_not_available"


# ---- 슬라이스 25 §2.1: vcjob 로그 -- launcher 항상 + Failed 파드만 ----

_SEL = "volcano.sh/job-name=dms-sync-execution-abc"


def _vcjob_k8s():
    k8s = _FakeK8s()
    k8s.set_briefs(_SEL, [
        {"name": "dms-sync-execution-abc-worker-0", "phase": "Succeeded",
         "waiting_reason": None},
        {"name": "dms-sync-execution-abc-worker-1", "phase": "Failed",
         "waiting_reason": None},
        {"name": "dms-sync-execution-abc-launcher-0", "phase": "Failed",
         "waiting_reason": None},
    ])
    k8s.set_log("dms-sync-execution-abc-launcher-0", "Traceback ...")
    k8s.set_log("dms-sync-execution-abc-worker-1", "worker died")
    return k8s


def test_vcjob_read_log_selects_launcher_first_then_failed_workers_only():
    # 성공 워커의 sshd 로그는 노이즈고(설계 §1-2 에 따라 대개 이미 없다), 남는
    # 파드는 실패 원인 파드다. launcher 가 앞이어야 박제 상한(항목 4, Task 4)이
    # 잘라도 launcher 가 산다.
    k8s = _vcjob_k8s()
    out = _adapter(k8s).read_log("vcjob/dms-sync-execution-abc")
    assert out == [
        ("dms-sync-execution-abc-launcher-0", "Traceback ...", None),
        ("dms-sync-execution-abc-worker-1", "worker died", None),
    ]
    # 셀렉터는 ref 의 이름으로 조립된다 -- vcjob GET 이 없어도 된다(설계 §2.1).
    assert k8s.asked_selectors == [_SEL]


def test_vcjob_launcher_is_included_even_when_succeeded():
    # Completed 잡도 launcher-0 은 잔존한다(설계 §1-1 실측). 진행 중/성공 launcher
    # 의 라이브 tail 이 이 분기로 공짜다 -- phase 로 launcher 를 거르면 안 된다.
    k8s = _FakeK8s()
    k8s.set_briefs(_SEL, [
        {"name": "dms-sync-execution-abc-launcher-0", "phase": "Succeeded",
         "waiting_reason": None},
    ])
    k8s.set_log("dms-sync-execution-abc-launcher-0", "")
    out = _adapter(k8s).read_log("vcjob/dms-sync-execution-abc")
    assert out == [("dms-sync-execution-abc-launcher-0", "", None)]   # 빈 로그는 정상값


def test_vcjob_waiting_reason_rides_alongside_null_log():
    # ImagePullBackOff 파드는 로그가 없다 -- "없다"(null)와 "왜 없는지"
    # (waiting_reason)는 별 채널이다. null 을 합성 문자열로 뭉개지 않는다(설계 §2.1).
    k8s = _FakeK8s()
    k8s.set_briefs(_SEL, [
        {"name": "dms-sync-execution-abc-launcher-0", "phase": "Pending",
         "waiting_reason": "ImagePullBackOff"},
    ])
    k8s.fail_log("dms-sync-execution-abc-launcher-0")
    out = _adapter(k8s).read_log("vcjob/dms-sync-execution-abc")
    assert out == [("dms-sync-execution-abc-launcher-0", None, "ImagePullBackOff")]


def test_vcjob_list_failure_raises_poll_failed_instead_of_empty():
    # per-pod 실패(null 접기)와 조회 계층 실패는 다르다: RBAC 403 이 "로그 없음"
    # 으로 렌더된 사고(execution_volcano.py:393-398 교훈)를 반복하지 않는다 --
    # list 예외는 409 로 표면화한다(설계 §2.1).
    k8s = _FakeK8s()
    k8s.fail_briefs(RuntimeError("forbidden"))
    with pytest.raises(ExecutionError) as exc_info:
        _adapter(k8s).read_log("vcjob/dms-sync-execution-abc")
    assert exc_info.value.reason_code == "poll_failed"


def test_stub_adapter_read_log_is_three_tuple():
    a = StubExecutionAdapter()
    assert a.read_log("pod/p1") == [("pod/p1", "", None)]
    a.set_log("pod/p1", [("p1", "custom log", None)])
    assert a.read_log("pod/p1") == [("p1", "custom log", None)]
```

(기존 `test_stub_adapter_read_log` 는 위 3-튜플판으로 **교체**한다.)

**(4)** `tests/test_api_job_logs.py` — `test_vcjob_ref_409_log_not_available` 를 다음으로 교체(미지 prefix 방어 + 주석 갱신):

```python
def test_unknown_prefix_ref_409_log_not_available(client):
    # 슬라이스 25 가 vcjob 로그를 열었다 -- 409 log_not_available 은 알 수 없는
    # ref prefix 방어로만 남는다(설계 §2.5). 실 어댑터로 그 방어를 고정한다.
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "execution", "widget/j1")
    client.app.state.execution_adapter = _volcano_adapter()
    _login(client, "alice")
    r = client.get(f"/api/user/jobs/{jid}/logs", params={"phase": "execution"})
    assert r.status_code == 409
    assert r.json()["detail"] == "log_not_available"
```

그리고 이 파일의 `set_log(...)` 3곳(2-튜플)을 3-튜플로 갱신한다: `[("p1", "hello preflight log", None)]`, `[("p2", "recheck failed: dst full", None)]`, `[("p1", big_log, None)]`.

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_execution_read_log.py tests/test_api_job_logs.py -q`
Expected: 다수 FAIL — 3-튜플 단언들이 2-튜플 반환에 어긋나고, `test_vcjob_read_log_selects_...` 계열은 현행 코드가 `ExecutionError(log_not_available)` 를 던져 FAIL, `test_unknown_prefix_ref_409...` 는 즉시 PASS 가 맞다(현행도 거절 경로다).

- [ ] **Step 3: 코드를 고친다**

**(1)** `src/dms/execution.py` — Protocol(`:46`)과 스텁(`:84-85`)을:

```python
    def read_log(self, ref: str) -> "list[tuple[str, str | None, str | None]]": ...
```

```python
    def read_log(self, ref):
        # 3-튜플 (pod, log, waiting_reason) -- 실 어댑터와 같은 계약(설계 §4).
        # 스텁은 클러스터가 없으므로 waiting_reason 을 알 수 없다 -- None.
        return self._logs.get(ref, [(ref, "", None)])
```

**(2)** `src/dms/execution_volcano.py` — `K8sClient` Protocol(`:61-67`)에 추가:

```python
    def list_pod_briefs(self, namespace: str, label_selector: str) -> list: ...
```

**(3)** `read_log`(`:243-260`)를 다음으로 교체:

```python
    def read_log(self, ref):
        """(pod, log, waiting_reason) 목록. log=None 은 "얻을 수 없었다"(파드 소실/
        미기동)고, waiting_reason 은 왜 없는지의 별 채널이다(ImagePullBackOff 류)
        -- null 을 합성 문자열로 뭉개지 않는다(설계 §2.1). 빈 문자열은 정상값이다
        (launcher 는 대개 비어 있다 -- §1-3)."""
        prefix, name = ref.split("/", 1)
        if prefix == "vcjob":
            return self._read_vcjob_logs(name)
        if prefix not in ("pod", "pods"):
            # 슬라이스 25 로 vcjob 이 열렸다 -- 이 거절은 미지 prefix 방어로만 남는다.
            raise ExecutionError("log_not_available", prefix)
        out = []
        for pod in name.split(","):
            try:
                out.append((pod, self._k8s.read_pod_log(pod, self._namespace), None))
            except Exception as exc:
                # 파드가 이미 GC됐거나 아직 로그가 없다 — 그 항목만 비우고 계속한다.
                # 다만 RBAC 거부·설정 오류·프로그래밍 버그도 여기로 떨어져 "GC됐다"와
                # 똑같이 렌더된다. 반환 계약은 그대로 두고 흔적만 남긴다.
                logger.warning("read_pod_log failed pod=%s: %s", pod, exc)
                out.append((pod, None, None))
        return out

    def _read_vcjob_logs(self, name):
        """vcjob 파드는 Volcano 자기 라벨(volcano.sh/job-name=<vcjob>)로 찾는다 --
        launcher 는 dms.io 라벨이 없고(§1-4), ref 에 이름이 있어 vcjob GET 도
        불요하다. launcher(이름 접미 -launcher-, §1-1 의 결정적 명명) 항상 +
        그 외는 Failed 만: 성공 워커의 sshd 로그는 노이즈고 남는 파드는 실패
        원인 파드다(§1-2). launcher 가 앞 -- 박제 상한(항목 4)이 잘라도
        launcher 가 살아야 한다. per-pod 실패는 pod 경로와 같은 null 접기지만,
        **list 호출 자체의 예외는 poll_failed 로 던진다**: 403 이 "로그 없음"으로
        렌더되는 사고(:393-398 교훈)를 조회 계층에서 반복하지 않는다."""
        try:
            briefs = self._k8s.list_pod_briefs(
                self._namespace, f"volcano.sh/job-name={name}")
        except Exception as exc:
            raise ExecutionError("poll_failed", str(exc)[:200]) from exc
        launchers = [b for b in briefs if "-launcher-" in b["name"]]
        failed = [b for b in briefs
                  if "-launcher-" not in b["name"] and b.get("phase") == "Failed"]
        out = []
        for brief in [*launchers, *failed]:
            pod = brief["name"]
            try:
                out.append((pod, self._k8s.read_pod_log(pod, self._namespace),
                            brief.get("waiting_reason")))
            except Exception as exc:
                logger.warning("read_pod_log failed pod=%s: %s", pod, exc)
                out.append((pod, None, brief.get("waiting_reason")))
        return out
```

**(4)** `src/dms/api/routes_artifacts.py:63` — `for pod, log in entries:` → `for pod, log, _waiting_reason in entries:` (응답 형태는 Task 6 에서 확장한다 — 여기서는 언팩만 맞춰 기존 계약 유지).

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_execution_read_log.py tests/test_api_job_logs.py tests/test_execution.py tests/test_execution_volcano.py tests/test_k8s_read_pod_log.py tests/test_reason_codes_coverage.py -q`
Expected: 전부 PASS. `test_reason_codes_coverage.py` 초록 = `poll_failed`·`log_not_available` 리터럴이 기등록 json 에 이미 있다는 계약 확인(신설 0).

- [ ] **Step 5: 뮤테이션으로 이빨 확인 후 원복**

(a) `_read_vcjob_logs` 의 Failed 필터를 지워 전 파드 포함(`failed = [b for b in briefs if "-launcher-" not in b["name"]]`) → `test_vcjob_read_log_selects_launcher_first_then_failed_workers_only` 가 Succeeded 워커 포함으로 RED. (b) `raise ExecutionError("poll_failed", ...)` 를 `return []` 로 → `test_vcjob_list_failure_raises_poll_failed_...` RED(조회 실패가 "파드 전멸"로 위장되는 바로 그 사고). 각각 확인 후 원복, Step 4 재확인.

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "feat(execution): read_log vcjob 개방 — volcano.sh/job-name 셀렉터, launcher 우선+Failed 만, 3-튜플(waiting_reason), list 예외는 poll_failed" -- src/dms/execution_volcano.py src/dms/execution.py src/dms/api/routes_artifacts.py tests/test_execution_read_log.py tests/test_api_job_logs.py
```

---

### Task 3: 리포지토리 — `archive_diag_logs`(write-once) + 다행 조회 diag 제외

**Files:**
- Modify: `src/dms/repositories/data_jobs.py`
- Create: `tests/test_repo_diag_logs.py`

**Interfaces:**
- Consumes: Task 1 의 `diag_logs` 컬럼, `dump_json`/`utc_now_iso`(기존 import).
- Produces (Task 4·6 이 소비한다):
  - `archive_diag_logs(job_id, *, phase, entries) -> None` — `UPDATE ... SET diag_logs = :d WHERE job_id = :j AND diag_logs IS NULL`. JSON `{"phase": phase, "at": <now>, "entries": entries}`. `updated_at` 은 건드리지 않는다(`mark_exec_submitted` 와 같은 근거 — 클레임 순서·GC 나이에 끼어들 이유가 없다).
  - 모듈 상수 `_ROW_COLUMNS_SANS_DIAG`(diag_logs 를 뺀 전 컬럼 29개의 SQL 목록) — **다행 조회 4곳**(list_jobs 2쿼리·claim_steppable·terminal_jobs_older_than·succeeded_scans)의 `SELECT *` 를 이것으로 교체. `get_job`(단행)은 `SELECT *` 유지 — /logs 폴백이 diag_logs 를 그걸로 읽는다. `terminal_jobs_with_live_request` 는 이미 3컬럼 명시(§1-10 정정)라 무접촉.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_repo_diag_logs.py` (신규 파일 전체):

```python
"""슬라이스 25 §2.2: 진단 로그 박제의 저장 계약.

파드 로그의 유일 사본은 시한부다(pod GC·vcjob TTL 86400) -- 실패 종단 시점에
DB 로 박제하되, ① write-once(재시도 멱등의 근거), ② 다행 조회는 이 컬럼(최대
64KB/행)을 절대 싣지 않는다(builds I2: 5초 폴링 x 50행 x 64KB = 3.2MB 왕복 사고의
재발 방지) -- 이 두 계약을 이 파일이 고정한다."""
import json

from dms.domain import DataJobState
from dms.repositories import Repositories


def _job(repos, *, key, state=None):
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key=key, payload={"storage": "s1", "target": "a"}, priority="mid")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s1", target="a", options={}, tool="dscan", worker_pool={},
        precondition={}, actor="planner")
    if state is not None:
        repos.data_jobs.set_job_state(jid, state, actor="test")
    return rid, jid


_ENTRIES = [{"pod": "p-launcher-0", "log": "Traceback ...", "truncated": False}]


def test_archive_stores_phase_at_and_entries(db):
    repos = Repositories(db)
    _rid, jid = _job(repos, key="k1")
    repos.data_jobs.archive_diag_logs(jid, phase="execution", entries=_ENTRIES)
    raw = repos.data_jobs.get_job(jid)["diag_logs"]
    doc = json.loads(raw)                      # get_job 은 원문 TEXT 를 준다(폴백이 직접 파싱)
    assert doc["phase"] == "execution"
    assert doc["entries"] == _ENTRIES
    assert doc["at"]                           # 박제 시각 -- "언제의 사본인지"가 남는다


def test_archive_stores_all_null_entries_honestly(db):
    # 전 항목 log=None 이어도 저장한다 -- "박제 시점에 이미 없었다"는 사실 자체가
    # 진단이다(설계 §2.2). 모름을 뭉개지 않는다.
    repos = Repositories(db)
    _rid, jid = _job(repos, key="k-null")
    repos.data_jobs.archive_diag_logs(jid, phase="preflight",
        entries=[{"pod": "p1", "log": None, "truncated": False}])
    doc = json.loads(repos.data_jobs.get_job(jid)["diag_logs"])
    assert doc["entries"][0]["log"] is None


def test_archive_is_write_once(db):
    # 박제 후 크래시 -> 다음 틱 finalize 재시도(Task 4 의 순서 계약)가 두 번째
    # 박제를 시도한다 -- IS NULL 술어가 첫 사본을 지킨다(mark_exec_submitted 선례).
    repos = Repositories(db)
    _rid, jid = _job(repos, key="k2")
    repos.data_jobs.archive_diag_logs(jid, phase="execution", entries=_ENTRIES)
    repos.data_jobs.archive_diag_logs(jid, phase="execution",
        entries=[{"pod": "attacker", "log": "overwrite", "truncated": False}])
    doc = json.loads(repos.data_jobs.get_job(jid)["diag_logs"])
    assert doc["entries"] == _ENTRIES          # 두 번째 호출은 무변경


def test_multi_row_queries_never_carry_diag_logs_but_get_job_does(db):
    # builds I2 의 그 문제: 큰 컬럼을 다행 조회에 얹으면 5초 폴링마다 최대
    # 50x64KB=3.2MB 가 왕복한다(설계 §1-10). 4곳 전부에서 부재를 고정한다.
    repos = Repositories(db)
    rid, jid = _job(repos, key="k3")                        # Pending -- claim 대상
    _rid2, jid2 = _job(repos, key="k4", state=DataJobState.SUCCEEDED)
    repos.data_jobs.archive_diag_logs(jid2, phase="execution", entries=_ENTRIES)
    assert "diag_logs" in repos.data_jobs.get_job(jid2)     # 단행(/logs 폴백)만 싣는다
    multi = {
        "list_jobs": repos.data_jobs.list_jobs(),
        "list_jobs(request)": repos.data_jobs.list_jobs(request_id=rid),
        "claim_steppable": repos.data_jobs.claim_steppable(),
        "succeeded_scans": repos.data_jobs.succeeded_scans("s1"),
        "terminal_older": repos.data_jobs.terminal_jobs_older_than(
            0, now_iso="2099-01-01T00:00:00Z"),
    }
    for name, rows in multi.items():
        assert rows, name                                    # 공허한 통과 방지 -- 행이 실제로 있다
        assert all("diag_logs" not in r for r in rows), name


def test_column_parity_pins_future_columns(db):
    # 미래에 컬럼을 추가하고 _ROW_COLUMNS_SANS_DIAG 갱신을 잊으면, 다행 조회만
    # 그 컬럼이 조용히 빠진다(SELECT * 시절엔 없던 사고 유형) -- get_job 과의
    # 차집합이 정확히 {diag_logs} 임을 계약으로 고정해 그 누락을 잡는다.
    repos = Repositories(db)
    _rid, jid = _job(repos, key="k5")
    job = repos.data_jobs.get_job(jid)
    row = repos.data_jobs.list_jobs()[0]
    assert set(job) - set(row) == {"diag_logs"}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_repo_diag_logs.py -q`
Expected: 5건 전부 FAIL — 앞 3건은 `AttributeError: ... 'archive_diag_logs'`, 뒤 2건은 `SELECT *` 가 diag_logs 를 싣고 있어 `assert all("diag_logs" not in r ...)`/차집합 단언에서.

- [ ] **Step 3: data_jobs.py 를 고친다**

**(1)** `_KNOWN_COMPONENTS`(`:18`) 아래(모듈 레벨)에 추가:

```python
# 슬라이스 25 §2.2: 다행 조회가 diag_logs(행당 최대 64KB)를 절대 싣지 않기 위한
# 명시 컬럼 목록 -- builds.list 의 I2 선례(5초 폴링 x 50행 x 64KB = 3.2MB 왕복).
# get_job(단행)만 SELECT * 로 diag_logs 를 포함한다(/logs 박제 폴백이 그걸 읽는다).
# 새 컬럼을 추가할 때는 migrations 양쪽 + 이 목록까지 세 곳이다 --
# tests/test_repo_diag_logs.py 의 컬럼 패리티 계약이 누락을 잡는다.
_ROW_COLUMNS_SANS_DIAG = (
    "job_id, request_id, operation, tool, storage_name, source_storage, "
    "destination_storage, source, destination, target, options, priority, state, "
    "reason_code, preview_fingerprint, preview_expires_at, volcano_job_ref, "
    "artifact_uri, result_summary, files_count, bytes_count, worker_pool, "
    "precondition, confirmed_fingerprint, phase_refs, submit_wait_seconds, "
    "exec_submitted_at, sched_wait_seconds, created_at, updated_at")
```

**(2)** 다행 4곳의 `SELECT *` 교체(각각 f-string 으로):
- `succeeded_scans`(`:110-114`): `SELECT {_ROW_COLUMNS_SANS_DIAG} FROM data_jobs ...`
- `list_jobs`(`:117-127`) 두 쿼리 모두.
- `claim_steppable`(`:198-200`).
- `terminal_jobs_older_than`(`:323-326`).

**(3)** `mark_exec_submitted`(`:214-225`) **아래**에 추가:

```python
    def archive_diag_logs(self, job_id, *, phase, entries) -> None:
        """실패 종단 진단 로그 박제(슬라이스 25 §2.2). write-once 는 SQL 술어
        (diag_logs IS NULL)가 강제한다 -- 박제 후 크래시하면 다음 틱의 finalize
        재시도가 다시 부르지만 첫 사본은 불변이다(mark_exec_submitted 선례).
        updated_at 은 건드리지 않는다: 같은 틱의 뒤따르는 set_job_state 가 어차피
        시각을 찍고, 박제가 클레임 순서(ORDER BY updated_at)·GC 나이에 끼어들
        이유가 없다(같은 선례). 상한(파드당 16KB·항목 4)은 호출자(stepper)의
        몫이다 -- 이 계층은 저장만 한다."""
        payload = dump_json({"phase": phase, "at": utc_now_iso(),
                             "entries": entries})
        self._db.execute(
            """UPDATE data_jobs SET diag_logs = :d
               WHERE job_id = :j AND diag_logs IS NULL""",
            {"d": payload, "j": job_id})
```

- [ ] **Step 4: 통과를 확인한다 (다행 조회 소비자 광역 회귀)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_repo_diag_logs.py tests/test_repo_data_jobs.py tests/test_repo_data_jobs_stepper.py tests/test_api_jobs.py tests/test_pod_gc.py tests/test_api_scan_path_stats.py tests/test_stepper_scan.py tests/test_stepper_sync.py tests/test_recover_orphans.py -q`
Expected: 전부 PASS — pod_gc(terminal_jobs_older_than 의 phase_refs)·scan_path_stats(succeeded_scans 의 target/artifact_uri)·stepper(claim_steppable 의 전 필드)·잡 목록 라우트가 명시 목록 치환의 무회귀 안전망이다. 하나라도 KeyError 면 `_ROW_COLUMNS_SANS_DIAG` 에서 컬럼이 빠진 것이다.

- [ ] **Step 5: 뮤테이션으로 이빨 확인 후 원복**

(a) `archive_diag_logs` 의 `AND diag_logs IS NULL` 을 삭제 → `test_archive_is_write_once` RED(두 번째 호출이 덮어쓴다). (b) `list_jobs` 첫 쿼리만 `SELECT *` 로 되돌림 → `test_multi_row_queries_never_carry_...` 와 `test_column_parity_...` RED. 각각 확인 후 원복, Step 4 재확인.

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git add tests/test_repo_diag_logs.py
git commit -m "feat(repo): archive_diag_logs write-once(IS NULL) + 다행 조회 4곳 diag_logs 제외(명시 컬럼, builds I2 선례) — 컬럼 패리티 계약" -- src/dms/repositories/data_jobs.py tests/test_repo_diag_logs.py
```

---

### Task 4: 스테퍼 — 실패 종단 4경로 박제(`_finalize(diag=)`) + 상한 + 순서 계약

**Files:**
- Modify: `src/dms/stepper.py`
- Create: `tests/test_stepper_diag_archive.py`

**Interfaces:**
- Consumes: Task 2 의 3-튜플 `read_log`, Task 3 의 `archive_diag_logs`, `record_event` 관례, `builds.LOG_TEXT_MAX`(계약 테스트에서만 import).
- Produces:
  - 모듈 상수 `DIAG_TAIL_BYTES = 16 * 1024`, `DIAG_MAX_ENTRIES = 4` — `DIAG_MAX_ENTRIES * DIAG_TAIL_BYTES == LOG_TEXT_MAX`(64KB) 가 계약.
  - `_diag_entry(pod, log) -> dict` — 바이트 단위 꼬리 16KB + `truncated`, None 은 None 그대로(`{"pod", "log": None, "truncated": False}`).
  - `JobStepper._archive_diag(job, phase, ref)` — read_log → 항목 상한(어댑터가 launcher 앞이므로 `[:DIAG_MAX_ENTRIES]` 로 launcher 보존) → `archive_diag_logs`. **어떤 예외도 삼키고** `record_event(warning, "diag_archive_failed")` 로 표면화 — 한 잡의 로그 때문에 종단 전이가 막히면 잡이 낀다(설계 §4).
  - `_finalize(job, job_state, *, reason_code=None, summary=None, diag=None)` — `diag=(phase, ref)` 가 있으면 **set_job_state 보다 먼저** 박제(순서가 계약 — 역순이면 종단 잡은 다시 스텝되지 않아 박제 기회가 영영 없다).
  - 배선 4곳: `_poll_preflight`(`:236`) `diag=("preflight", ref)` / `_poll_execution`(`:295`) `diag=("execution", ref)` / `_poll_preview`(`:334`,`:336`) `diag=("preview", ref)` / `_poll_or_submit_execution`(`:365`) `diag=("exec_preflight", refs["exec_preflight"])`. **`_fail_closed`(unknown_tool·storage_missing_at_step)와 submit 실패 계열·성공 경로는 배선하지 않는다**(§1 재확인의 판정 — 테스트로 박제).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_stepper_diag_archive.py` (신규 파일 전체):

```python
"""슬라이스 25 §2.2: 실패 종단 시 파드 로그를 diag_logs 에 박제한다.

파드가 남아 있어도 시한부다(pod GC 86400·vcjob TTL 86400) -- 스테퍼가 실패
종단을 관측하는 순간이 로그가 확실히 존재하는 마지막 지점이므로 거기서 박제한다.
순서가 계약이다: 박제 -> set_job_state. 박제 후 크래시하면 다음 틱이 finalize 를
재시도하고(IS NULL 이 중복을 막는다), 역순이면 종단 잡은 다시 스텝되지 않아 박제
기회가 영영 사라진다."""
import json

from dms.domain import DataJobState, RequestState
from dms.execution import ExecStatus, ExecutionError, StubExecutionAdapter
from dms.repositories import Repositories
from dms.repositories.builds import LOG_TEXT_MAX
from dms.stepper import DIAG_MAX_ENTRIES, DIAG_TAIL_BYTES, JobStepper


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 86400


def _seed_storage(repos, name):
    if repos.storages.get(name) is None:
        repos.storages.create(storage_name=name, mount_path=f"/{name}",
                              managed_root=f"/{name}/dms", backend_type="cephfs",
                              actor="test")


def _scan_job(repos, *, tool="dscan", storage="s1", key=None):
    _seed_storage(repos, storage)
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key=key or f"k-{tool}", payload={"storage": storage, "target": "a"},
        priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name=storage, target="a", options={}, tool=tool,
        worker_pool={"tool": tool, "identity": {"uid": 10001, "gid": 10000,
            "username": "alice", "groups": [], "privileged": False},
            "candidates": {"primary": ["n1"]}, "process_count": 8,
            "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def _sync_job(repos):
    _seed_storage(repos, "src")
    _seed_storage(repos, "dst")
    rid = repos.requests.create(operation="sync", requester_id="alice", actor="alice",
        resource_key="k-sync", payload={"source_storage": "src", "source": "a",
        "destination_storage": "dst", "destination": "b"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid",
        source_storage="src", source="a", destination_storage="dst", destination="b",
        options={}, tool="dsync",
        worker_pool={"tool": "dsync", "identity": {"uid": 10001, "gid": 10000,
            "username": "alice", "groups": [], "privileged": False},
            "candidates": {"primary": ["n1"]}, "process_count": 8,
            "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def _stepper(repos, adapter):
    return JobStepper(repos, adapter, settings=_Settings())


def _diag(repos, jid):
    raw = repos.data_jobs.get_job(jid)["diag_logs"]
    return None if raw is None else json.loads(raw)


# ---- 실패 종단 4경로 각각이 박제한다 ----

def test_preflight_failure_archives_the_pod_log(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()                                   # Pending -> Preflight
    ref = f"stub-preflight-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_log(ref, [("p1", "DMS_PREFLIGHT_REASON=target_not_readable", None)])
    stepper.run_once()
    assert repos.data_jobs.get_job(jid)["state"] == "Rejected"
    doc = _diag(repos, jid)
    assert doc["phase"] == "preflight"
    assert doc["entries"] == [{"pod": "p1",
        "log": "DMS_PREFLIGHT_REASON=target_not_readable", "truncated": False}]


def test_execution_failure_archives_launcher_log(db):
    # 러너 도달 전 실패(파이썬 트레이스백)의 유일한 증거가 여기 남는다 -- 이
    # 슬라이스의 존재 이유다(설계 서두).
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()                                   # Pending -> Preflight
    stepper.run_once()                                   # Preflight ok -> Running
    ref = f"stub-execution-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_log(ref, [("j-launcher-0", "Traceback (most recent call last) ...", None)])
    stepper.run_once()
    assert repos.data_jobs.get_job(jid)["state"] == "Failed"
    doc = _diag(repos, jid)
    assert doc["phase"] == "execution"
    assert doc["entries"][0]["pod"] == "j-launcher-0"
    assert "Traceback" in doc["entries"][0]["log"]


def test_preview_timeout_archives_with_preview_phase(db):
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()                                   # Pending -> Preflight
    ref = f"stub-preview-{jid}"
    adapter.script(ref, [ExecStatus.TIMED_OUT])
    adapter.set_log(ref, [("pv-launcher-0", "", None)])
    stepper.run_once()                                   # Preflight ok -> PreviewRunning
    stepper.run_once()                                   # Preview TIMED_OUT
    assert repos.data_jobs.get_job(jid)["state"] == "TimedOut"
    doc = _diag(repos, jid)
    assert doc["phase"] == "preview"
    assert doc["entries"][0]["log"] == ""                # 빈 로그는 정상값 -- null 이 아니다


def test_recheck_failure_archives_exec_preflight_phase(db):
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    adapter.set_summary(f"stub-preview-{jid}", {"files": 3, "bytes": 9})
    stepper = _stepper(repos, adapter)
    stepper.run_once()                                   # Pending -> Preflight
    stepper.run_once()                                   # Preflight ok -> PreviewRunning
    stepper.run_once()                                   # Preview ok -> ConfirmPending
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    stepper.run_once()                                   # exec_preflight 제출
    ref = f"stub-exec_preflight-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_log(ref, [("re-pf", "DMS_PREFLIGHT_REASON=source_not_readable", None)])
    stepper.run_once()
    assert repos.data_jobs.get_job(jid)["state"] == "Rejected"
    doc = _diag(repos, jid)
    assert doc["phase"] == "exec_preflight"
    assert "source_not_readable" in doc["entries"][0]["log"]


# ---- 상한·정직성·격리 ----

def test_caps_four_entries_and_16kb_tails_total_64kb(db):
    # 상한이 없으면 DB 가 부푼다 -- 파드당 16KB 꼬리 + 항목 4(launcher 우선,
    # 어댑터가 앞에 놓는다) = 총 64KB, builds LOG_TEXT_MAX 와 같은 총량(설계 §2.2).
    assert DIAG_MAX_ENTRIES * DIAG_TAIL_BYTES == LOG_TEXT_MAX
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()
    ref = f"stub-preflight-{jid}"
    big = "x" * (DIAG_TAIL_BYTES + 1000) + "TAIL-MARKER"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_log(ref, [(f"p{i}", big, None) for i in range(6)])
    stepper.run_once()
    doc = _diag(repos, jid)
    assert len(doc["entries"]) == DIAG_MAX_ENTRIES
    assert [e["pod"] for e in doc["entries"]] == ["p0", "p1", "p2", "p3"]  # 앞 우선
    for e in doc["entries"]:
        assert e["truncated"] is True
        assert len(e["log"].encode()) <= DIAG_TAIL_BYTES
        assert e["log"].endswith("TAIL-MARKER")           # 머리가 아니라 꼬리를 남긴다
    assert len(json.dumps(doc).encode()) <= LOG_TEXT_MAX + 4096  # 봉투(키·pod명) 여유


def test_all_null_logs_still_archived(db):
    # "박제 시점에 이미 없었다"는 사실 자체가 진단이다 -- 저장을 건너뛰면
    # /logs 폴백이 "박제 자체가 없었다"와 구분할 수 없게 된다(설계 §2.2).
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()
    ref = f"stub-preflight-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_log(ref, [("p1", None, None)])
    stepper.run_once()
    doc = _diag(repos, jid)
    assert doc is not None
    assert doc["entries"] == [{"pod": "p1", "log": None, "truncated": False}]


class _LogRaisingAdapter(StubExecutionAdapter):
    def read_log(self, ref):
        raise ExecutionError("poll_failed", "apiserver down")


def test_archive_failure_records_event_and_still_finalizes(db):
    # 박제 실패가 종단 전이를 막으면 잡이 낀다 -- 조용한 실패도 금지라 이벤트로
    # 표면화한다(설계 §4).
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = _LogRaisingAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()
    adapter.script(f"stub-preflight-{jid}", [ExecStatus.FAILED])
    stepper.run_once()
    assert repos.data_jobs.get_job(jid)["state"] == "Rejected"       # 종단은 됐다
    assert _diag(repos, jid) is None
    kinds = [e["event_type"] for e in repos.observability.events_for_request(rid)]
    assert "diag_archive_failed" in kinds


def test_success_terminal_does_not_archive(db):
    # 성공 잡의 로그는 아티팩트가 이미 영구 사본이다(설계 §7).
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()
    stepper.run_once()                                   # Preflight ok -> Running
    stepper.run_once()                                   # execution SUCCEEDED
    assert repos.data_jobs.get_job(jid)["state"] == "Succeeded"
    assert _diag(repos, jid) is None


def test_fail_closed_paths_do_not_archive(db):
    # 슬라이스 24 신설 종단(unknown_tool 등)은 박제 비대상이다: Pending 종단은
    # 파드가 없고, 진행 중 종단은 _fail_closed 가 refs 를 회수하는 경로라 증거가
    # 파드 로그가 아니라 DB 행 자체다(플랜 §1 재확인의 판정을 계약으로 박제).
    repos = Repositories(db)
    rid, jid = _scan_job(repos, tool="dwalk", key="k-fc")
    adapter = StubExecutionAdapter()
    _stepper(repos, adapter).run_once()
    assert repos.data_jobs.get_job(jid)["state"] == "Rejected"
    assert _diag(repos, jid) is None


# ---- 순서 계약: 박제 -> set_job_state ----

def test_crash_between_archive_and_state_write_replays_idempotently(db):
    """순서가 뒤집히면(종단 먼저) 크래시 창에서 박제 기회가 영영 사라진다 --
    박제가 먼저면 다음 틱 재폴링이 finalize 를 재시도하고 IS NULL 이 중복을
    막는다(설계 §2.2). set_job_state 를 1회 실패시키는 크래시 주입으로 그 순서를
    직접 증명한다."""
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()
    ref = f"stub-preflight-{jid}"
    adapter.script(ref, [ExecStatus.FAILED, ExecStatus.FAILED])   # 두 틱 다 실패 관측
    adapter.set_log(ref, [("p1", "first copy", None)])
    original = repos.data_jobs.set_job_state
    state = {"raised": False}

    def crash_once(job_id, to_state, **kwargs):
        from dms.domain import TERMINAL_DATA_JOB_STATES
        if (job_id == jid and to_state in TERMINAL_DATA_JOB_STATES
                and not state["raised"]):
            state["raised"] = True
            raise RuntimeError("crash after archive, before state write")
        return original(job_id, to_state, **kwargs)

    repos.data_jobs.set_job_state = crash_once
    stepper.run_once()                                   # 박제됨 + 종단 전이는 크래시
    assert repos.data_jobs.get_job(jid)["state"] == "Preflight"   # 아직 비종단
    first = _diag(repos, jid)
    assert first is not None                             # 박제가 먼저였다 -- 순서의 증거
    adapter.set_log(ref, [("p1", "second copy", None)])  # 재시도 시점의 로그는 달라졌다
    stepper.run_once()                                   # 재시도: finalize 완주
    assert repos.data_jobs.get_job(jid)["state"] == "Rejected"
    assert _diag(repos, jid)["entries"] == first["entries"]       # 첫 사본 불변(write-once)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_stepper_diag_archive.py -q`
Expected: `ImportError: cannot import name 'DIAG_MAX_ENTRIES'` 로 수집 단계 전체 FAIL(상수 미존재). `test_success_terminal_...`·`test_fail_closed_...` 는 구현 후 즉시 PASS 가 맞는 고정 가드다.

- [ ] **Step 3: stepper.py 를 고친다**

**(1)** `_summary_fingerprint`(`:14-18`) 아래 모듈 레벨에 추가:

```python
# 슬라이스 25 §2.2: 진단 로그 박제 상한. 파드당 꼬리 16KB x 항목 4 = 총 64KB --
# builds.LOG_TEXT_MAX(64KB)와 같은 총량 규약이다(계약 테스트가 곱을 고정한다).
# 상한 없는 박제는 다행 조회에서 이미 격리했더라도(리포지토리 몫) DB 자체를
# 부풀린다 -- 꼬리를 남기는 이유는 트레이스백·실패 사유가 끝에 몰리기 때문.
DIAG_TAIL_BYTES = 16 * 1024
DIAG_MAX_ENTRIES = 4


def _diag_entry(pod, log):
    """박제 항목 하나. log=None(얻을 수 없었다)은 None 그대로 저장한다 --
    "박제 시점에 이미 없었다"는 사실 자체가 진단이다. 빈 문자열은 정상값이라
    truthy 검사를 쓰지 않는다(설계 §4). 꼬리 자르기는 바이트 기준이고 경계의
    깨진 코드포인트는 replace 로 강등한다(/logs 라우트의 MAX_BYTES 관례)."""
    if log is None:
        return {"pod": pod, "log": None, "truncated": False}
    raw = log.encode()
    if len(raw) > DIAG_TAIL_BYTES:
        return {"pod": pod,
                "log": raw[-DIAG_TAIL_BYTES:].decode("utf-8", errors="replace"),
                "truncated": True}
    return {"pod": pod, "log": log, "truncated": False}
```

**(2)** `_finalize`(`:119-124`)를 다음으로 교체:

```python
    def _finalize(self, job, job_state, *, reason_code=None, summary=None, diag=None):
        # 슬라이스 25 §2.2: diag=(phase, ref) 가 오면 종단 전이 **전에** 박제한다.
        # 순서가 계약이다 -- 박제 후 크래시하면 잡이 비종단으로 남아 다음 틱이
        # finalize 를 재시도하고(archive 는 IS NULL 이 중복을 막는다), 역순이면
        # 종단 잡은 다시 스텝되지 않아 박제 기회가 영영 사라진다.
        if diag is not None:
            self._archive_diag(job, *diag)
        self._repos.data_jobs.set_job_state(job["job_id"], job_state,
                                            reason_code=reason_code, actor="stepper")
        self._repos.requests.finalize_from_job(
            job["request_id"], job_state, reason_code=reason_code, summary=summary,
            actor="stepper")

    def _archive_diag(self, job, phase, ref):
        """실패 종단 시점 파드 로그 박제(설계 §2.2). 어댑터가 launcher 를 앞에
        놓으므로 [:DIAG_MAX_ENTRIES] 상한이 잘라도 launcher 가 산다. 박제 실패는
        finalize 를 막지 않는다 -- 한 잡의 로그 때문에 종단 전이가 막히면 잡이
        낀다 -- 대신 이벤트로 표면화한다(조용한 실패 금지, 설계 §4)."""
        try:
            raw = self._exec.read_log(ref)
            entries = [_diag_entry(pod, log)
                       for pod, log, _wr in raw[:DIAG_MAX_ENTRIES]]
            self._repos.data_jobs.archive_diag_logs(job["job_id"], phase=phase,
                                                    entries=entries)
        except Exception as exc:
            self._repos.observability.record_event(
                component="stepper", severity="warning",
                event_type="diag_archive_failed",
                message=f"{type(exc).__name__}: {exc}"[:500],
                payload={"phase": phase, "ref": ref},
                request_id=job.get("request_id"))
```

**(3)** 실패 종단 4경로에 `diag=` 를 얹는다(각 함수의 지역변수 `ref` 가 이미 손에 있다):
- `_poll_preflight`(`:236`): `self._finalize(job, DataJobState.REJECTED, reason_code="preflight_failed", diag=("preflight", ref))`
- `_poll_execution`(`:295`): `self._finalize(job, target, reason_code="execution_failed", diag=("execution", ref))`
- `_poll_preview`(`:334`): `self._finalize(job, DataJobState.TIMED_OUT, reason_code="preview_timed_out", diag=("preview", ref))` / (`:336`): `self._finalize(job, DataJobState.FAILED, reason_code="preview_failed", diag=("preview", ref))`
- `_poll_or_submit_execution`(`:365`): `self._finalize(job, DataJobState.REJECTED, reason_code="execution_recheck_failed", diag=("exec_preflight", refs["exec_preflight"]))`

(submit 실패 계열 4곳·`empty_preview`·`_fail_closed`·성공 경로는 **무접촉** — 파드가 없거나(제출 전) 원본이 파괴 중이거나(§1 재확인 판정) 영구 사본이 이미 있다.)

- [ ] **Step 4: 통과를 확인한다 (스테퍼 광역 회귀)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_stepper_diag_archive.py tests/test_stepper_scan.py tests/test_stepper_sync.py tests/test_stepper_enrich.py tests/test_stepper_fail_closed.py tests/test_stepper_artifact_uri.py tests/test_timeout_enforcement.py tests/test_controller_stepper.py tests/test_recover_orphans.py tests/test_vcjob_ttl.py -q`
Expected: 전부 PASS — 기존 실패 경로 테스트들(reason_code·상태 전이 단언)이 `diag=` 얹기의 무회귀 안전망이다.

- [ ] **Step 5: 뮤테이션으로 이빨 확인 후 원복**

(a) `_finalize` 에서 `if diag is not None:` 블록을 `set_job_state` **뒤**로 옮김(순서 역전) → `test_crash_between_archive_and_state_write_...` 가 RED: 크래시 틱에서 `first is not None` 단언 실패(박제가 아직 없다) — "역순이면 박제 기회가 영영 없다"의 단위 증명. (b) `raw[:DIAG_MAX_ENTRIES]` 를 `raw` 로 → `test_caps_four_entries_...` RED. (c) `_diag_entry` 의 꼬리를 머리로(`raw[:DIAG_TAIL_BYTES]`) → 같은 테스트의 `endswith("TAIL-MARKER")` RED. 각각 확인 후 원복, Step 4 재확인.

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git add tests/test_stepper_diag_archive.py
git commit -m "feat(stepper): 실패 종단 4경로 diag_logs 박제 — 박제→종단 순서 계약, 16KB×4=64KB 상한, diag_archive_failed 표면화, fail_closed·성공은 비대상" -- src/dms/stepper.py tests/test_stepper_diag_archive.py
```

---

### Task 5: 스테퍼 §2.4 — 실패 잡의 summary·artifact_uri 표면화

**Files:**
- Modify: `src/dms/stepper.py`
- Modify: `tests/test_stepper_artifact_uri.py`

**Interfaces:**
- Consumes: `read_summary`(어댑터), `set_artifact`(`data_jobs.py:270-287` — files/bytes 승격 포함), 성공 경로 형식(`stepper.py:287-290`).
- Produces: `JobStepper._failed_summary(ref) -> dict | None` — read_summary 를 try/except 로 감싸 예외를 None(모름)으로 접는다(실패 잡의 보강은 best-effort — 예외가 step_error 루프를 만들면 안 된다). execution/preview 의 FAILED/TIMED_OUT 경로에서 값이 있으면 `set_artifact(job_id, artifact_uri=f"{base}/{jid}", result_summary=summary)` — 성공 경로와 동일 형식. **None 이면 기록하지 않는다 — 지어내지 않는다.** metrics 오염 없음(`metrics.py:145-147` 의 SUM 은 `state = 'Succeeded'` 필터 — §1-12). preflight/recheck 실패는 대상 아님(프리플라이트 파드는 아티팩트를 쓰지 않는다). 화면은 0줄 — `RequestDetail.tsx:182-183` 의 artifact_uri 렌더가 이미 조건부다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_stepper_artifact_uri.py` — 파일 끝에 추가:

```python
# ---- 슬라이스 25 §2.4: 실패 잡도 summary 가 있으면 표면화한다 ----

def test_failed_execution_with_summary_records_artifact_and_summary(db):
    # 러너는 도구 비0 종료에도 stdout/stderr/summary 를 쓰고 나서 exit 한다
    # (설계 §1-7) -- returncode 가 카드에 떠야 "왜 실패했나"의 첫 단서가 보인다.
    # 집계는 오염되지 않는다: metrics 합계는 state='Succeeded' 만 센다(§1-12).
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()                                   # Pending -> Preflight
    stepper.run_once()                                   # Preflight ok -> Running
    ref = f"stub-execution-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_summary(ref, {"returncode": 2, "files": None, "bytes": None})
    stepper.run_once()
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Failed"
    assert job["artifact_uri"] == f"file:///art/{jid}"
    assert job["result_summary"] == {"returncode": 2, "files": None, "bytes": None}


class _NoSummaryAdapter(StubExecutionAdapter):
    # 스텁 기본값은 어떤 ref 에도 {"files": 0, "bytes": 0} 를 준다 -- "요약이
    # 없다"(러너 도달 전 실패)를 재현하려면 None 을 명시로 돌려줘야 한다.
    def read_summary(self, ref):
        return None


def test_failed_execution_without_summary_records_nothing(db):
    # None 은 "모른다"다 -- 지어내지 않는다(설계 §2.4). artifact_uri 를 여기서
    # 합성하면 포탈이 존재하지 않는 아티팩트 디렉터리를 가리킨다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = _NoSummaryAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()
    stepper.run_once()
    adapter.script(f"stub-execution-{jid}", [ExecStatus.FAILED])
    stepper.run_once()
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Failed"
    assert job["artifact_uri"] is None
    assert job["result_summary"] is None


def test_preview_failure_with_summary_records_artifact(db):
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()                                   # Pending -> Preflight
    ref = f"stub-preview-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_summary(ref, {"returncode": 1, "files": None, "bytes": None})
    stepper.run_once()                                   # Preflight ok -> PreviewRunning
    stepper.run_once()                                   # Preview FAILED
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Failed"
    assert job["artifact_uri"] == f"file:///art/{jid}"
    assert job["result_summary"] == {"returncode": 1, "files": None, "bytes": None}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_stepper_artifact_uri.py -q`
Expected: 신규 3건 중 2건 FAIL — `test_failed_execution_with_summary_...`·`test_preview_failure_with_summary_...` 가 `assert job["artifact_uri"] == ...` 에서 실제 `None`(현행은 성공 경로만 기록한다). `test_failed_execution_without_summary_...` 는 현행 고정 가드라 **즉시 PASS 가 맞다**.

- [ ] **Step 3: stepper.py 를 고친다**

**(1)** `_archive_diag` 아래에 추가:

```python
    def _surface_failed_artifact(self, job, ref):
        """실패 잡의 summary 표면화(설계 §2.4). 러너는 도구 비0 종료에도
        summary.json 을 쓰고 exit 하므로(§1-7) returncode 가 카드에 뜬다.
        read_summary 예외는 None(모름)으로 접는다 -- 실패 잡의 보강은 best-effort
        고, 여기서 던지면 run_once 의 step_error 루프(매 틱 재시도)에 낀다.
        None 이면 기록하지 않는다 -- artifact_uri 를 지어내면 포탈이 존재하지
        않는 디렉터리를 가리킨다. metrics 는 오염되지 않는다(합계가
        state='Succeeded' 필터 -- §1-12)."""
        try:
            summary = self._exec.read_summary(ref)
        except Exception:
            summary = None
        if summary is not None:
            self._repos.data_jobs.set_artifact(
                job["job_id"],
                artifact_uri=f"{self._artifact_base()}/{job['job_id']}",
                result_summary=summary)
```

**(2)** `_poll_execution` 의 실패 꼬리(`:293-295`, Task 4 반영 후) — `target = ...` 줄과 `self._finalize(...)` 줄 사이에 `self._surface_failed_artifact(job, ref)` 삽입.

**(3)** `_poll_preview` 의 두 실패 경로(TIMED_OUT·FAILED, Task 4 반영 후) — 각 `self._finalize(...)` **앞**에 `self._surface_failed_artifact(job, ref)` 삽입.

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_stepper_artifact_uri.py tests/test_stepper_scan.py tests/test_stepper_sync.py tests/test_stepper_diag_archive.py tests/test_timeout_enforcement.py tests/test_repo_metrics.py -q`
Expected: 전부 PASS (`test_repo_metrics.py` 가 §1-12 집계 무오염의 안전망).

- [ ] **Step 5: 뮤테이션으로 이빨 확인 후 원복**

`_poll_execution` 의 `self._surface_failed_artifact(job, ref)` 를 삭제 → `test_failed_execution_with_summary_...` RED. 확인 후 원복, Step 4 재확인.

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "feat(stepper): 실패 잡 summary·artifact_uri 표면화(§2.4) — 성공 경로 동일 형식, None 은 미기록, metrics 무오염" -- src/dms/stepper.py tests/test_stepper_artifact_uri.py
```

---

### Task 6: `/logs` 라우트 — 라이브 우선·박제 폴백(`source`) + 20-config 주석

**Files:**
- Modify: `src/dms/api/routes_artifacts.py`
- Modify: `tests/test_api_job_logs.py`
- Modify: `deploy/k8s/20-config.yaml`(주석만 — 이미지 태그·값 무변경)

**Interfaces:**
- Consumes: Task 2 의 3-튜플 read_log, `_owned_job`(get_job → SELECT * 라 diag_logs 동봉 — 추가 쿼리 0), `tail_lines`/`MAX_BYTES`.
- Produces: `GET /api/user/jobs/{id}/logs?phase=` 응답 계약(Task 7 이 소비):
  - 라이브 성공: `{"phase", "ref", "source": "live", "entries": [{"pod", "log", "waiting_reason"}]}`.
  - **라이브가 빈 목록이거나 전 항목 log=None** 이고, `diag_logs` 가 있고 그 `phase` 가 요청 phase 와 같으면: `{"source": "archived", "entries": [{"pod", "log", "truncated"}]}`(tail 파라미터는 박제 로그에도 적용).
  - diag JSON 이 깨져 있으면 폴백을 포기하고 라이브 결과를 그대로 반환 + `record_event(warning, "diag_logs_corrupt")` — 깨진 사본으로 응답을 지어내지 않는다(설계 §4).
  - 404 `log_ref_not_found`(ref 없음)·422 `invalid_phase`·409(`poll_failed`/`log_not_available`) 기존 계약 유지.
- 종단 잡 로그는 불변이라 두 소스의 내용 충돌은 없다. admin 은 `_owned_job` 의 role 우회(`routes_jobs.py:30-31`)로 이미 전 잡 열람 — 별도 라우트 없음.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_job_logs.py` — **(1)** 기존 3개 응답 단언에 `source`·`waiting_reason` 을 반영:

```python
# test_get_preflight_log_returns_entries:
    assert body["source"] == "live"
    assert body["entries"] == [{"pod": "p1", "log": "hello preflight log",
                                "waiting_reason": None}]
# test_exec_preflight_log_is_reachable:
    assert body["entries"] == [{"pod": "p2", "log": "recheck failed: dst full",
                                "waiting_reason": None}]
```

**(2)** 파일 끝에 추가:

```python
# ---- 슬라이스 25 §2.5: 라이브 우선, 박제 폴백 ----

def _archived(client, jid, phase="execution", entries=None):
    client.app.state.repos.data_jobs.archive_diag_logs(jid, phase=phase,
        entries=entries if entries is not None else [
            {"pod": "j-launcher-0", "log": "Traceback ...", "truncated": True}])


def test_dead_pods_fall_back_to_archived_copy(client):
    # 파드 소실(라이브 전 항목 null) + 박제 존재 -> archived. 항목별 truncated 가
    # 실려야 화면이 잘림 배지를 그린다(설계 §3).
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "execution", "vcjob/j1")
    client.app.state.execution_adapter.set_log("vcjob/j1", [("j-launcher-0", None, None)])
    _archived(client, jid)
    _login(client, "alice")
    r = client.get(f"/api/user/jobs/{jid}/logs", params={"phase": "execution"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "archived"
    assert body["entries"] == [{"pod": "j-launcher-0", "log": "Traceback ...",
                                "truncated": True}]


def test_empty_live_list_falls_back_to_archived(client):
    # vcjob TTL 로 파드가 전멸하면 라이브는 빈 목록이다 -- 0 항목도 폴백 조건
    # (설계 §2.5). 빈 목록과 "전 항목 null"을 다르게 취급할 이유가 없다.
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "execution", "vcjob/j1")
    client.app.state.execution_adapter.set_log("vcjob/j1", [])
    _archived(client, jid)
    _login(client, "alice")
    body = client.get(f"/api/user/jobs/{jid}/logs",
                      params={"phase": "execution"}).json()
    assert body["source"] == "archived"


def test_live_wins_when_any_log_is_present(client):
    # 라이브가 하나라도 실체를 주면 박제를 쓰지 않는다 -- 진행 중 잡의 launcher
    # 라이브 tail 이 이 경로다(설계 §2.1 "공짜").
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "execution", "vcjob/j1")
    client.app.state.execution_adapter.set_log(
        "vcjob/j1", [("j-launcher-0", "live tail", None)])
    _archived(client, jid)
    _login(client, "alice")
    body = client.get(f"/api/user/jobs/{jid}/logs",
                      params={"phase": "execution"}).json()
    assert body["source"] == "live"
    assert body["entries"][0]["log"] == "live tail"


def test_no_archive_keeps_the_null_live_contract(client):
    # 파드 소실 + 박제 없음(배포 전 종단 잡, §7 "백필하지 않는다") -- 기존 null
    # 계약 그대로. null 을 지어낸 문구로 채우지 않는다.
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "preflight", "pod/p1")
    client.app.state.execution_adapter.set_log("pod/p1", [("p1", None, None)])
    _login(client, "alice")
    body = client.get(f"/api/user/jobs/{jid}/logs",
                      params={"phase": "preflight"}).json()
    assert body["source"] == "live"
    assert body["entries"] == [{"pod": "p1", "log": None, "waiting_reason": None}]


def test_archived_phase_mismatch_stays_live(client):
    # 박제는 실패한 그 phase 하나뿐이다 -- 다른 phase 요청에 그 사본을 내밀면
    # 사람을 오도한다(preflight 로그 자리에 execution 로그).
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "preflight", "pod/p1")
    client.app.state.execution_adapter.set_log("pod/p1", [("p1", None, None)])
    _archived(client, jid, phase="execution")
    _login(client, "alice")
    body = client.get(f"/api/user/jobs/{jid}/logs",
                      params={"phase": "preflight"}).json()
    assert body["source"] == "live"


def test_corrupt_diag_json_returns_live_and_records_event(client, db):
    # 깨진 사본으로 응답을 지어내지 않는다(설계 §4) -- 라이브 결과 그대로 +
    # 경고 이벤트. 조용한 강등 금지. client 픽스처는 conftest 의 같은 db 로
    # 조립되므로(conftest.py:22-24) db 인자로 직접 오염시킬 수 있다 --
    # 리포지토리는 깨진 값을 만들 수 없다(archive 가 dump_json 을 쓴다).
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "execution", "vcjob/j1")
    client.app.state.execution_adapter.set_log("vcjob/j1", [("p", None, None)])
    db.execute(
        "UPDATE data_jobs SET diag_logs = '{broken' WHERE job_id = :j", {"j": jid})
    _login(client, "alice")
    body = client.get(f"/api/user/jobs/{jid}/logs",
                      params={"phase": "execution"}).json()
    assert body["source"] == "live"
    events = repos.observability.events_for_request(rid)
    assert "diag_logs_corrupt" in [e["event_type"] for e in events]


def test_archived_entries_respect_tail_param(client):
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "execution", "vcjob/j1")
    client.app.state.execution_adapter.set_log("vcjob/j1", [])
    _archived(client, jid, entries=[
        {"pod": "p", "log": "l1\nl2\nl3", "truncated": False}])
    _login(client, "alice")
    body = client.get(f"/api/user/jobs/{jid}/logs",
                      params={"phase": "execution", "tail": 1}).json()
    assert body["source"] == "archived"
    assert body["entries"][0]["log"] == "l3"
```

(주의: `test_corrupt_diag_json_...` 의 `client.app.state.db` 배선은 conftest 의 app 조립을 먼저 확인하고, 없으면 `client.app.state.repos` 가 쥔 db 핸들 접근 관례를 기존 테스트에서 찾아 그대로 쓴다 — 이 저장소 API 테스트들이 `client.app.state.repos.<repo>._db` 대신 어떤 창구를 쓰는지 `grep -n "db.execute" tests/test_api_*.py` 로 실측해 맞출 것. 직접 UPDATE 한 줄이 요점이고 창구는 부차다.)

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_api_job_logs.py -q`
Expected: 신규 7건 + 갱신 2건 FAIL — 현행 응답에 `source`·`waiting_reason` 키가 없고(`KeyError`/단언 불일치), 폴백이 없어 archived 계열이 전부 라이브 null 로 온다.

- [ ] **Step 3: routes_artifacts.py 를 고친다**

`get_job_logs`(`:48-70`)를 다음으로 교체(파일 머리에 `import json` 추가):

```python
def _render_log(log, tail):
    """라이브·박제 공통의 표시 상한: tail 줄 수 + MAX_BYTES 바이트 캡."""
    if log is None:
        return None
    if tail is not None:
        log = tail_lines(log, tail)
    if len(log.encode()) > MAX_BYTES:
        log = log.encode()[-MAX_BYTES:].decode("utf-8", errors="replace")
    return log


def _archived_entries(request, job, phase, tail):
    """diag_logs 박제 사본에서 요청 phase 의 항목을 꺼낸다. 없음/불일치는 None.
    깨진 JSON 은 폴백 포기 + 경고 이벤트 -- 깨진 사본으로 응답을 지어내지 않는다
    (설계 §4). get_job 이 SELECT * 라 diag_logs 가 job 행에 이미 실려 있다 --
    추가 쿼리가 없다."""
    raw = job.get("diag_logs")
    if raw is None:
        return None
    try:
        doc = json.loads(raw)
        entries = doc["entries"]
        archived_phase = doc["phase"]
    except (ValueError, TypeError, KeyError):
        request.app.state.repos.observability.record_event(
            component="api", severity="warning", event_type="diag_logs_corrupt",
            message=f"job={job['job_id']}", request_id=job.get("request_id"))
        return None
    if archived_phase != phase:
        return None
    return [{"pod": e.get("pod"), "log": _render_log(e.get("log"), tail),
             "truncated": bool(e.get("truncated"))} for e in entries]


@router.get("/api/user/jobs/{job_id}/logs")
def get_job_logs(job_id: str, request: Request, phase: str = Query(default="preflight"),
                 tail: int | None = Query(default=None, ge=1),
                 identity: Identity = Depends(require_user)):
    job = _owned_job(request, job_id, identity)
    if phase not in PHASES:
        raise HTTPException(status_code=422, detail="invalid_phase")
    ref = (job["phase_refs"] or {}).get(phase)
    if not ref:
        raise HTTPException(status_code=404, detail="log_ref_not_found")
    try:
        entries = request.app.state.execution_adapter.read_log(ref)
    except ExecutionError as e:
        # 미지 prefix(log_not_available)와 조회 계층 실패(poll_failed) -- null
        # (파드 소실)과 409(조회 오류)를 뭉개지 않는다(설계 §4).
        raise HTTPException(status_code=409, detail=e.reason_code)
    live = [{"pod": pod, "log": _render_log(log, tail), "waiting_reason": wr}
            for pod, log, wr in entries]
    # 슬라이스 25 §2.5: 라이브 우선, 박제 폴백. 폴백 조건은 "빈 목록 또는 전 항목
    # log=None" -- 빈 문자열은 정상값이라 폴백 조건이 아니다(truthy 검사 금지).
    # 종단 잡 로그는 불변이라 두 소스의 내용 충돌은 없다.
    exhausted = not live or all(e["log"] is None for e in live)
    if exhausted:
        archived = _archived_entries(request, job, phase, tail)
        if archived is not None:
            return {"phase": phase, "ref": ref, "source": "archived",
                    "entries": archived}
    return {"phase": phase, "ref": ref, "source": "live", "entries": live}
```

- [ ] **Step 4: 20-config.yaml 주석을 사실로 되돌린다 (값·태그 무변경)**

`deploy/k8s/20-config.yaml:74-77` 의 문단(`# AFTER_SECONDS는 위 ... 유일한 사본을 지워버린다`)을 다음으로 교체:

```yaml
  # AFTER_SECONDS는 위 DMS_VCJOB_TTL_SECONDS와 같은 86400으로 맞춘다. 슬라이스 25
  # 부터 실패 종단 잡의 파드 로그(DMS_PREFLIGHT_REASON 마커·러너 트레이스백)는
  # 스테퍼가 data_jobs.diag_logs 에 박제하므로(파드당 16KB·총 64KB) 이 창은 더는
  # "유일한 사본"의 수명이 아니다 -- 라이브 로그 열람의 여유 창일 뿐이다. 값을
  # 줄일 이유도 없다: 박제는 실패 종단 잡만 덮고, 진행 중·성공 잡의 라이브
  # 열람은 여전히 이 창에 산다.
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_api_job_logs.py tests/test_api_artifacts.py tests/test_rbac_contract.py tests/test_manifest_tags.py -q`
Expected: 전부 PASS (`test_manifest_tags.py` 초록 = 주석 변경이 태그·값을 안 건드렸다는 보증).

- [ ] **Step 6: 뮤테이션으로 이빨 확인 후 원복**

(a) 폴백 조건 `all(e["log"] is None ...)` 을 `any(...)` 로 → `test_live_wins_when_any_log_is_present` RED(라이브 실체가 있는데 archived 로 강등). (b) `_archived_entries` 의 corrupt except 에서 `return None` 대신 `entries = []; archived_phase = phase` 로 계속 진행 → `test_corrupt_diag_json_...` RED(깨진 사본으로 응답을 지어내는 바로 그 사고). 각각 확인 후 원복, Step 5 재확인.

- [ ] **Step 7: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "feat(api): /logs 라이브 우선·박제 폴백(source=live|archived) — waiting_reason·truncated, 깨진 diag 는 라이브+경고, 20-config '유일 사본' 주석 정정" -- src/dms/api/routes_artifacts.py tests/test_api_job_logs.py deploy/k8s/20-config.yaml
```

---

### Task 7: 화면 — JobViewer 로그 탭(구조 무변경) + 타입

**Files:**
- Modify: `frontend/src/lib/types.ts`, `frontend/src/features/jobs/JobViewer.tsx`
- Modify: `frontend/src/features/jobs/JobViewer.test.tsx`

**Interfaces:**
- Consumes: Task 6 응답 계약. `reasonCodes.json`·`api.ts` **무변경**(신설 사유 0).
- Produces:
  - `JobLogs`(`types.ts:40-42`) 확장: `source: "live" | "archived"`, entries 에 `waiting_reason?: string | null`·`truncated?: boolean`.
  - JobViewer 로그 렌더(`:92-111` 블록): ① archived 응답이면 캡션 "잡 종료 시점에 저장된 사본 — 파드당 마지막 16KB". ② 항목 `truncated` 면 기존 "뒷부분만 표시" 배지(아티팩트 뷰 `:82-86` 과 같은 마크업 재사용). ③ `log === null` 이고 `waiting_reason` 있으면 "파드 로그 없음 — {waiting_reason}", waiting_reason 없으면 기존 문구 유지. ④ **빈 문자열 로그는 `<pre>` 로 그대로 렌더** — `=== null` 비교만 쓴다(truthy 금지, §1-3 의 빈 launcher 로그가 정상값이다).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/src/features/jobs/JobViewer.test.tsx` — 파일 끝에 추가:

```tsx
test("log tab pairs a null log with its waiting_reason when present", async () => {
  // null(로그 없음)과 "왜 없는지"(waiting_reason)는 별 채널이다 -- 병기는 하되
  // null 을 합성 문자열로 뭉개지 않는다(백엔드 계약). ImagePullBackOff 파드는
  // 로그가 생기기 전에 죽는 대표 사례다.
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)),
    http.get("/api/user/jobs/j1/logs", () =>
      HttpResponse.json({ phase: "preflight", ref: "pod/p1", source: "live",
        entries: [{ pod: "p1", log: null, waiting_reason: "ImagePullBackOff" }] })),
  );
  wrap();
  await userEvent.click(await screen.findByRole("button", { name: "preflight 로그" }));
  expect(await screen.findByText("파드 로그 없음 — ImagePullBackOff")).toBeInTheDocument();
});

test("archived response shows the caption and per-entry truncation badge", async () => {
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)),
    http.get("/api/user/jobs/j1/logs", () =>
      HttpResponse.json({ phase: "execution", ref: "vcjob/j1", source: "archived",
        entries: [{ pod: "j-launcher-0", log: "Traceback tail", truncated: true }] })),
  );
  wrap({ execution: "vcjob/j1" });
  await userEvent.click(await screen.findByRole("button", { name: "execution 로그" }));
  expect(await screen.findByText("Traceback tail")).toBeInTheDocument();
  expect(screen.getByText("잡 종료 시점에 저장된 사본 — 파드당 마지막 16KB")).toBeInTheDocument();
  expect(screen.getByText("뒷부분만 표시")).toBeInTheDocument();
});

test("an empty-string log renders as content, not as the pod-gone message", async () => {
  // 빈 로그는 정상값이다(launcher 는 대개 비어 있다) -- truthy 검사로 null 문구를
  // 내면 "로그가 없다"와 "빈 로그"가 뭉개진다.
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)),
    http.get("/api/user/jobs/j1/logs", () =>
      HttpResponse.json({ phase: "preflight", ref: "pod/p1", source: "live",
        entries: [{ pod: "p1", log: "", waiting_reason: null }] })),
  );
  wrap();
  await userEvent.click(await screen.findByRole("button", { name: "preflight 로그" }));
  await screen.findByText("p1");
  expect(screen.queryByText("파드 로그를 더 이상 조회할 수 없습니다")).not.toBeInTheDocument();
});
```

(기존 `log null` 테스트(`:115-125`)는 payload 에 source 가 없다 — **그대로 둔다**: 구형 응답 형태에도 죽지 않는다는 회귀 가드를 겸한다.)

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run src/features/jobs/JobViewer.test.tsx`
Expected: 신규 3건 중 2건 FAIL — waiting_reason 병기·archived 캡션이 없다. 빈 문자열 테스트는 현행도 `=== null` 비교라 **즉시 PASS 가 맞다**(고정 가드).

- [ ] **Step 3: 타입·컴포넌트를 고친다**

**(1)** `frontend/src/lib/types.ts:40-42`:

```ts
export interface JobLogs {
  phase: string; ref: string;
  // 슬라이스 25: live = 지금 파드에서 읽음, archived = 실패 종단 시점의 박제 사본.
  source: "live" | "archived";
  entries: {
    pod: string; log: string | null;
    waiting_reason?: string | null;   // live 전용 -- 로그가 없는 "이유"의 별 채널
    truncated?: boolean;              // archived 전용 -- 파드당 16KB 꼬리 잘림
  }[];
}
```

**(2)** `JobViewer.tsx` 의 로그 렌더 블록(`:97-109`, `logs.data ? (...)` 내부)을 다음으로 교체:

```tsx
            <div className="space-y-3">
              {logs.data.source === "archived" && (
                <span className="inline-block text-xs text-muted border border-black/10 rounded px-2 py-0.5">
                  잡 종료 시점에 저장된 사본 — 파드당 마지막 16KB
                </span>
              )}
              {logs.data.entries.map((e) => (
                <div key={e.pod}>
                  <p className="text-xs font-medium">{e.pod}</p>
                  {e.truncated && (
                    <span className="inline-block text-xs text-muted border border-black/10 rounded px-2 py-0.5 mb-2">
                      뒷부분만 표시
                    </span>
                  )}
                  {e.log === null ? (
                    <p className="text-muted text-sm">
                      {e.waiting_reason
                        ? `파드 로그 없음 — ${e.waiting_reason}`
                        : "파드 로그를 더 이상 조회할 수 없습니다"}
                    </p>
                  ) : (
                    <pre className="overflow-x-auto text-xs whitespace-pre-wrap">{e.log}</pre>
                  )}
                </div>
              ))}
            </div>
```

(빈 로그(`""`)는 `=== null` 을 통과하지 못해 `<pre>` 로 그대로 그려진다 — 정상값. `e.waiting_reason` 의 truthy 검사는 여기선 안전하다: 빈 문자열 waiting_reason 은 어댑터가 만들지 않는다(`",".join(...) or None`).)

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b`
Expected: `231 passed`(228+3) / 49 files, tsc 무출력 exit 0. 기존 409 문구 테스트(`:103-113`)·null 문구 테스트(`:115-125`)가 초록 = 회귀 없음(설계 §5 "기존 409 문구 회귀").

- [ ] **Step 5: 뮤테이션으로 이빨 확인 후 원복**

archived 캡션 `<span>` 블록을 삭제 → `archived response shows the caption...` RED. 확인 후 원복, Step 4 재확인.

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "feat(portal): execution 로그 탭에 내용 — waiting_reason 병기·archived 캡션·잘림 배지(구조 무변경), 빈 로그는 정상값" -- frontend/src/lib/types.ts frontend/src/features/jobs/JobViewer.tsx frontend/src/features/jobs/JobViewer.test.tsx
```

---

### Task 8: 마감 검증 — 전체 스위트 + 프론트 + e2e + 불변 조항 (커밋 없음)

**Files:** 없음(검증만)

- [ ] **Step 1: 백엔드 전체 스위트**

Run(포그라운드, Bash timeout 900000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests -q`
Expected: **약 1222 passed**(기준선 1189 + 신규 약 33: T1 3 + T2 ~10 + T3 5 + T4 9 + T5 3 + T6 ~9 중 일부는 기존 갱신 — 근사치다. 수가 다르면 신규 수를 다시 세되 **failed 0 이 본질**이다). `test_migrations.py` 의 `len(ALL_TABLES) == 20` 초록 = 새 테이블 0 보증.

- [ ] **Step 2: 프론트 전체 + 타입체크 + e2e**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b && npm run test:e2e`
Expected: `231 passed`/49 files, tsc 무출력, e2e `9 passed`(영향 판단대로 무수정 초록이어야 한다 — 빨개지면 어느 시나리오가 왜 깨졌는지 판단 후 e2e 를 고쳐도 된다: 무변경 계약은 슬라이스 23 한정이었다).

- [ ] **Step 3: 계약·불변 조항 확인**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && git status --porcelain && git log --oneline -8 && git diff HEAD~7 --stat -- frontend/src/lib/reasonCodes.json frontend/src/lib/api.ts deploy/k8s`
Expected: 작업 트리 clean(커밋 7건 외 잔여물 없음), `reasonCodes.json`·`api.ts` **diff 0**(신설 사유 0 의 증거), `deploy/k8s` 는 20-config.yaml 주석 diff 만(태그 무변경), `legacy/`·`docs/`(이 플랜 파일 외) 무변경.

---

## 플랜 이후: 배포·실증 (설계 §6 — 별도 ops, 플랜 태스크 밖)

플랜 실행이 끝나면 배포자가 테스트베드에서 수행한다(슬라이스 12~24 관례). **매니페스트-우선**: 태그를 먼저 bump→커밋하고 **그 커밋에서** 빌드한다(Dockerfile.dms 가 `deploy/k8s` 를 이미지에 COPY — 순서가 바뀌면 드리프트 배지). 현재 태그 **세 이미지 전부 d35** → 이 슬라이스는 **`dms` 만 d36**. **범프 판단**: 러너(`src/dms_job_runner/`)·에이전트(`src/dms/agent/`) 무접촉이므로 `DMS_JOB_IMAGE`(20-config.yaml:22)와 `dms-agent`(50-agent-daemonset.yaml:72)는 **d35 유지** — 잡 이미지를 같이 올리면 빌드만 느려지고 얻는 게 없다. **스키마가 바뀌므로 migrate 재실행이 필수다**(슬라이스 16 교훈: `set image` 는 migrate 를 재실행하지 않는다 — 아래 apply 순서가 그 처방이다). DB 조작·확인은 **API 파드 안 python**(`kubectl -n dms exec deploy/dms-api -c api -- python`)으로 한다(pkg-01 psql 접근 불가). 되돌릴 수 있는 조작만, 원복까지.

**0. 태그 범프 커밋 + 클러스터 내 빌드 + migrate + apply**

이 세션은 pkg-01 에 SSH 가 안 되므로 `deploy/docker/build-and-push.sh`(비상용 실물) 대신 **빌드 파드를 직접 만들어 클러스터에서 빌드한다**(슬라이스 24 실적 — `build_build_pod` 에 태그만 지정). 전제 2건: ① 빌드 파드는 GitHub 에서 clone 하므로 **범프 커밋이 origin(main)에 push 되어 있어야 한다**(배포자 몫 — 플랜 태스크의 push 금지와 별개인 배포 절차다), ② 빌드 노드에 운영자가 인터넷을 연다(deploy/README §8-3, 노드는 `control-state` 의 `build_node_name`).

```bash
# (a) 매니페스트 범프 -- 5곳: 30-migrate-job.yaml:25 / 40-api.yaml:67,84 /
#     41-controller.yaml:35,52 의 dms d35→d36. 20-config.yaml(DMS_JOB_IMAGE)·
#     50-agent-daemonset.yaml 은 d35 유지(위 판단).
git commit -m "deploy(k8s): 제어면 d36 (슬라이스 25 실행 단계 진단 — diag_logs 박제·vcjob 로그)" -- deploy/k8s
# main 병합·push 후 그 커밋에서:
COMMIT=$(git rev-parse HEAD)

# (b) 빌드 파드 생성 -- build_build_pod 로 매니페스트를 만들고 태그를 d36 으로 강제:
PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src \
/home/mason/dms-dev/dms/.venv/bin/python - <<'EOF' > /tmp/build-d36.json
import json, uuid
from dms.build_manifests import build_build_pod
pod = build_build_pod(build_id=uuid.uuid4().hex,
    repo_url="https://github.com/ChahwanSong/dms.git", git_ref="main",
    images=["dms"], node="<build_node_name -- GET /api/admin/control-state 로 확인>",
    namespace="dms", registry="pkg-01:5000",
    builder_image="pkg-01:5000/buildah:stable", timeout_seconds=7200)
for env in pod["spec"]["containers"][0]["env"]:
    if env["name"] == "DMS_BUILD_TAG":
        env["value"] = "d36"        # build_tag(build_id) 대신 배포 태그 직접 지정
print(json.dumps(pod))
EOF
kubectl apply -f /tmp/build-d36.json
kubectl -n dms logs -f "$(python -c 'import json;print(json.load(open("/tmp/build-d36.json"))["metadata"]["name"])')"
# 기대: "DMS_COMMIT_SHA=<위 COMMIT>" 확인(엉뚱한 커밋 빌드 방지) ... "DMS_BUILD_OK"
# 빌드 파드 정리: kubectl -n dms delete -f /tmp/build-d36.json

# (c) **migrate 먼저** -- Job 은 immutable 이라 delete 후 재적용:
kubectl -n dms delete job dms-migrate --ignore-not-found
kubectl apply -f deploy/k8s/30-migrate-job.yaml
kubectl -n dms wait --for=condition=complete job/dms-migrate --timeout=300s

# (d) 제어면 apply + 수렴(initContainer 의 migrate 재실행이 이중 안전망):
kubectl apply -f deploy/k8s/20-config.yaml -f deploy/k8s/40-api.yaml -f deploy/k8s/41-controller.yaml
kubectl -n dms rollout status deploy/dms-api deploy/dms-controller

# (e) **기존 DB 업그레이드 경로 검증** -- 빈 DB(CREATE)와 다른 코드가 실제로 먹었는지:
kubectl -n dms exec deploy/dms-api -c api -- python -c "
import os
from dms.db import Database
db = Database.connect(os.environ['DMS_DATABASE_URL'])
print(db.query(\"SELECT column_name, data_type FROM information_schema.columns\"
              \" WHERE table_name = 'data_jobs' AND column_name = 'diag_logs'\"))"
# 기대: [{'column_name': 'diag_logs', 'data_type': 'text'}] -- 빈 목록이면 migrate 미적용, 진행 금지.
```

**1. (§6-1 변형) 라이브 vcjob 로그 + confirm 후 실패의 박제.** 설계 §6-1 의 "러너 크래시 유도"(소스 권한 회수)는 exec_preflight 재검증이 먼저 잡는다 — 그것 자체가 박제 4경로 중 하나(`execution_recheck_failed`)라 **결정적으로 실증 가능**하고, 러너 트레이스백 실증은 기회 실증으로 남긴다(열린 질문 2):

```bash
# (a) 라이브: 큰 디렉터리 scan 제출(수 분 소요) -> 진행 중 잡 상세의 execution 로그 탭
#     -> launcher 항목이 뜨는지(source=live -- 빈 로그여도 파드명이 보이면 성공).
#     슬라이스 5 이후 처음으로 execution 탭이 409 가 아니다.
# (b) 박제(execution_recheck_failed): dsync 잡 제출(소스 /cephfs/dms/slice25-src,
#     사전 준비: ssh dms-w1 'sudo mkdir -p /cephfs/dms/slice25-src && echo x | sudo tee /cephfs/dms/slice25-src/f1 && sudo chown -R <uid>:<gid> /cephfs/dms/slice25-src')
#     -> preview 통과, ConfirmPending 에서 **confirm 하기 전에** 소스 읽기 회수:
ssh dms-w1 'sudo chmod 000 /cephfs/dms/slice25-src'
#     -> 포탈 confirm -> 다음 틱 exec_preflight 실패 -> Rejected(execution_recheck_failed)
# (c) 판정: diag_logs 에 exec_preflight 마커가 박제됐는지(API 파드 python):
kubectl -n dms exec deploy/dms-api -c api -- python -c "
import os
from dms.db import Database
db = Database.connect(os.environ['DMS_DATABASE_URL'])
print(db.query_one(\"SELECT diag_logs FROM data_jobs WHERE job_id = '<jid>'\"))"
# 기대: {"phase": "exec_preflight", ..., "log": "...DMS_PREFLIGHT_REASON=source_not_readable..."}
# (d) 원복: ssh dms-w1 'sudo chmod 755 /cephfs/dms/slice25-src && sudo rm -rf /cephfs/dms/slice25-src'
```

**2. (§6-2) 프리플라이트 실패 → 박제 → 파드 삭제 후 archived 폴백 — 핵심 실증.**

```bash
# (a) 요청자가 못 읽는 target 준비(root 700):
ssh dms-w1 'sudo mkdir -p /cephfs/dms/slice25-noread && sudo chmod 700 /cephfs/dms/slice25-noread'
# (b) 포탈에서 그 target 으로 scan 제출 -> Rejected(preflight_failed).
#     로그 탭: source=live, DMS_PREFLIGHT_REASON=target_not_readable 확인.
# (c) diag_logs 박제 확인(위 (1c)와 같은 python 창구 -- phase=preflight, 같은 마커).
# (d) pod GC 모사 -- preflight 파드 수동 삭제(종단 잡의 파드라 어차피 GC 대상):
kubectl -n dms delete pod "dms-preflight-<jid 앞 12자>-preflight-<node>"
# (e) 판정: 같은 로그 탭 재조회 -> 캡션 "잡 종료 시점에 저장된 사본 — 파드당 마지막 16KB"
#     + 같은 마커(source=archived). 화면이 안 되면 curl 로:
#     curl -b <세션> ".../api/user/jobs/<jid>/logs?phase=preflight" -> "source": "archived"
# (f) 원복: ssh dms-w1 'sudo rmdir /cephfs/dms/slice25-noread'
```

**3. (§6-3) 도구 비0 종료: 빈 launcher 로그의 정직한 박제 + returncode 카드(§2.4).**

```bash
# (a) 드릴 준비 -- 요청자 소유 디렉터리 안에 root 소유 파일(drm 이 못 지운다):
ssh dms-w1 'sudo mkdir -p /cephfs/dms/slice25-rm-drill && echo x | sudo tee /cephfs/dms/slice25-rm-drill/rootfile && sudo chown <uid>:<gid> /cephfs/dms/slice25-rm-drill'
# (b) 포탈에서 rm 제출(target=slice25-rm-drill, recursive) -> preview -> confirm -> Failed(execution_failed).
# (c) 판정 3종: ① execution 로그 탭 -- launcher 항목이 **빈 로그**로 뜬다(§1-3 실측의
#     재현 -- 빈 것은 정상값, "조회할 수 없습니다"가 아니다). 종단 후 diag_logs 에도
#     빈 문자열("log": "")이 정직하게 박제됐는지 (1c) 창구로 확인.
#     ② 아티팩트 탭 stderr.log 에 drm 의 실패 출력. ③ 잡 카드에 returncode(§2.4 --
#     result_summary 가 실패 잡에 처음으로 실린다)와 아티팩트 URI.
# (d) 대시보드 files_total 이 이 실패 잡의 카운트로 오염되지 않았는지(§1-12) 눈확인.
# (e) 원복: ssh dms-w1 'sudo rm -rf /cephfs/dms/slice25-rm-drill'
```

**4. (§6-4) TTL 집행 확인 + 워커 라벨 실측(§1-4 마감).**

```bash
# (a) 위 (3)의 Failed 잡의 vcjob 이름·생성 시각 기록:
kubectl -n dms get vcjob -l "dms.io/job-id=<jid>" -o wide
# (b) 진행 중(또는 방금 종단한) vcjob 의 **워커** 파드에 volcano.sh/job-name 라벨이
#     실제로 붙는지(§1-4 는 launcher 만 실측했다):
kubectl -n dms get pods -l "volcano.sh/job-name=<vcjob명>" --show-labels
# 기대: launcher-0 + worker-N 전부 걸린다 -- read_log 셀렉터가 워커 실패도 줍는 근거.
# (c) 86400s(1일) 경과 후: vcjob·파드가 실제로 사라졌는지 관측(Volcano TTL 집행의
#     첫 실측), 사라진 뒤 그 잡의 /logs 가 source=archived 로 여전히 열리는지.
kubectl -n dms get vcjob <vcjob명> ; kubectl -n dms get pods -l "volcano.sh/job-name=<vcjob명>"
```

**5. (§6-5) 잔해 수동 정리와 열람 유지 — 정리 가능 판정의 근거.**

```bash
# (a) 이 배포 **이후** 종단된 실패 잡(위 2·3)의 vcjob/파드를 수동 삭제(운영자 정리 모사)
#     -> /logs 가 source=archived 로 유지되는지. 유지되면 "정리해도 진단을 잃지
#     않는다"가 성립 -- TTL 없는 구세대 잔해 정리 판단의 근거가 된다.
# (b) 이 배포 **이전** 종단 잡(diag_logs NULL -- §7 "백필하지 않는다")의 로그 탭이
#     기존 null 문구 그대로인지 -- 잃을 사본이 애초에 없었음을 화면이 정직하게
#     말한다. 구세대 잔해의 실제 일괄 정리는 운영 판단이라 여기서 자동화하지 않는다.
```

실증 5건 통과 후 `docs/superpowers/BACKLOG.md`(슬라이스 25 완료 기록 + §2.3 의 "pod GC 가 유일 사본 파괴" 항목 해소)를 별도 커밋으로 갱신한다(플랜 밖, 관례).

---

## Self-Review

**1. 스펙 커버리지**

| 설계 절 | 담당 |
|---|---|
| §1 실측 전제 12항 | 「설계 §1 전제 재확인」 — 전항 재실측. 정정: stepper 4경로 현행 라인(슬라이스 24 개편), 러너/매니페스트 라인 드리프트, 기준선 1189/228/e2e 9, terminal_jobs_with_live_request 는 3컬럼이라 무접촉, 슬라이스 24 신설 종단 2건 박제 비대상 판정 |
| §2.1 vcjob read_log(Protocol·셀렉터·launcher+Failed·3-튜플·poll_failed·라이브 tail) | Task 2 |
| §2.2 diag_logs(양쪽 마이그레이션·상한·4경로·박제→종단 순서·write-once·이벤트·다행 4곳 제외) | Task 1(스키마) + Task 3(리포지토리) + Task 4(스테퍼) |
| §2.3 GC·TTL 무변경 + 주석 갱신 | Task 6 Step 4(주석만) — 어떤 태스크도 GC/TTL 값을 만지지 않는다 |
| §2.4 실패 잡 summary·artifact_uri | Task 5 (화면 0줄 — RequestDetail 조건부 렌더 기존재) |
| §2.5 /logs 라이브 우선·박제 폴백·source·409 방어 유지 | Task 6 |
| §3 화면(캡션·잘림 배지·waiting_reason 병기·빈 로그 정직) | Task 7 |
| §4 오류 처리(박제 실패 이벤트·null vs 409·깨진 JSON·스텁 3-튜플) | Task 4(이벤트)·2(poll_failed/스텁)·6(corrupt) |
| §5 테스트 목록 | 각 Task Step 1 이 1:1 이상 커버. 기준선 갱신(1189/228/e2e 9) |
| §6 실증 5항 | 「플랜 이후」 — d36 판단(제어면만)·클러스터 내 빌드·migrate 순서·API 파드 python 창구·원복 포함. §6-1 은 결정적 변형(execution_recheck)으로 대체하고 정직하게 기록 |
| §7 하지 않는 것 | Cancelled/submit 계열/fail_closed/성공 잡 미박제(테스트로 고정), 백필 없음(실증 5-b), 마커 파싱 승격·스트리밍·이벤트 payload 로그 본문 전부 부재 |

**2. 뮤테이션(이빨) 매트릭스** — T1 ensure 삭제→ALTER 테스트 RED / CREATE 삭제→소스 계약 RED. T2 Failed 필터 제거→선별 RED / poll_failed→빈 목록→409 테스트 RED. T3 IS NULL 삭제→write-once RED / SELECT * 복원→제외·패리티 RED. T4 **박제↔종단 순서 역전→크래시 재생 테스트 RED**(과제 지시의 핵심 뮤테이션) / 상한 제거→cap RED / 꼬리→머리→TAIL-MARKER RED. T5 set_artifact 제거→summary RED. T6 all→any→라이브 우선 RED / corrupt 계속 진행→corrupt RED. T7 캡션 삭제→vitest RED.

**3. 타입·이름 일관성** — `archive_diag_logs(job_id, *, phase, entries)` 는 Task 3 정의·Task 4(스테퍼)·Task 6(테스트 헬퍼)가 동일 철자. `DIAG_TAIL_BYTES`/`DIAG_MAX_ENTRIES` 는 stepper 모듈 레벨(Task 4 테스트가 import). 3-튜플 `(pod, log, waiting_reason)` 은 execution.py Protocol·스텁·volcano·routes 전부 동일. JSON 키 `{"phase","at","entries"}`/`{"pod","log","truncated"}` 는 Task 3 저장·Task 6 파싱·Task 7 렌더 동일. 이벤트 타입 `diag_archive_failed`(stepper)·`diag_logs_corrupt`(api) 는 각 정의처·테스트 동일 철자.

**알려진 위험 / 설계 대비 조정:**
- **슬라이스 24 신설 종단 2건을 박제 비대상으로 판정**했다(§1 재확인 표의 근거 3개) — 설계엔 없던 경로라 판단이 필요했고, `test_fail_closed_paths_do_not_archive` 가 판정을 계약으로 고정한다. 후속에서 대상으로 바꾸려면 `_fail_closed` 의 terminate-먼저 구조를 재설계해야 한다.
- **`test_diag_logs_is_declared_in_the_create_table_block` 은 소스 텍스트 검사다** — 신규 DB 에선 CREATE 누락을 `_ensure_columns` 가 흡수해 기능 테스트로는 구분 불가능하다는 실측이 근거다. 이 저장소의 AST 계약 테스트(reason codes)와 같은 계열의 타협이다.
- **`_ROW_COLUMNS_SANS_DIAG` 는 29컬럼 수기 목록**이다 — 미래 컬럼 추가 시 세 곳(CREATE·ensure·목록)이 되지만, 컬럼 패리티 계약(`set(get_job) - set(row) == {"diag_logs"}`)이 누락을 즉시 잡는다.
- **archived 응답에 박제 시각(`at`)을 싣지 않았다** — 설계 §2.5 의 응답 계약(source·waiting_reason·truncated)을 최소로 유지. JSON 에는 저장돼 있어 후속에서 캡션에 올리는 것은 필드 하나다.
- **라이브 vcjob 응답의 항목 수 상한은 없다**(최대 워커 수 = max_nodes 8, 항목당 MAX_BYTES 캡은 기존과 동일) — 상한 4 는 박제(DB)에만 건다. 설계 §2.2 의 상한 요구가 DB 팽창 방지이기 때문이다.
- **전체 수치 기대(≈1222·231)는 근사 명시** — 어긋나면 재계산하되 failed 0 이 판정 기준.

## 결정이 필요한 열린 질문

1. **`poll_failed` 의 화면 문구가 "빌드 상태를 확인하지 못했습니다"다**(`api.ts:128`) — 로그 조회 409 에도 이 문구가 보인다. 설계가 "새 사유 코드 0건·poll_failed 재사용·문구 무변경"을 명시해 그대로 두었다. 문구 일반화("상태를 확인하지 못했습니다")는 reasonCodes 위생 슬라이스 감이다.
2. **§6-1 의 "러너 크래시(트레이스백)" 실증에 결정적 유도 수단이 없다** — confirm 후 소스 권한 회수는 exec_preflight 재검증이 먼저 잡는다(그 자체가 박제 4경로 중 하나라 실증 가치는 유지). 러너 트레이스백 박제는 단위 테스트(`test_execution_failure_archives_launcher_log`)가 계약을 고정하고, 실 클러스터에선 자연 발생 시 diag_logs 를 확인하는 기회 실증으로 남긴다. 결정적 재현이 꼭 필요하면 잡 이미지의 runner 를 일부러 깨는 실험 태그가 필요한데, 그건 d36 범위 밖이다.
3. **진행 중 잡의 execution 로그 탭은 이제 라이브 k8s 조회를 만든다**(탭을 연 사용자당 list+log N회, 폴링은 아니다 — useJobLogs 는 refetchInterval 이 없다). 운영에서 부담이 관측되면 후속에서 캐시/스로틀을 판단한다.
4. **diag_logs 의 보존 기한이 없다**(잡 행과 함께 산다) — 잡 행 자체의 보존 정책이 아직 없으므로(BACKLOG) 이 슬라이스는 따로 만들지 않았다. 잡 retention 슬라이스가 생기면 diag_logs 는 공짜로 따라간다.
