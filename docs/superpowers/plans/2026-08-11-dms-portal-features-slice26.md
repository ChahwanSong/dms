# 슬라이스 26 — 포탈 기능 잔여 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 포탈의 네 구멍을 닫는다. (a) **아티팩트 다운로드** — 지금 포탈은 256KB 꼬리 텍스트 열람뿐이라 전체 파일·바이너리를 얻을 수단이 없다(설계 §1-1). 새 라우트 `GET /api/user/jobs/{job_id}/artifacts/{phase}/{name}/download` 가 **검사한 fd 그대로** 스트림한다 — read_artifact 와 같은 봉쇄 사슬(단일 open `O_NOFOLLOW|O_NONBLOCK` → fstat `S_ISREG` → `/proc/self/fd` 봉쇄), fstat 시점 size 만큼만, 헤더 3종(octet-stream·attachment·nosniff), 상한 초과는 413 `artifact_too_large`(신설 사유 코드 **정확히 1종**, 새 설정 키 **정확히 1개** `DMS_ARTIFACT_DOWNLOAD_MAX_BYTES` 기본 256MiB). (b) **FAST-FOLLOW 6건**(전부 미해소 실측) + **슬라이스 23 e2e 가 찾은 StoragesList flex td 결함 1건 편입**(근거는 §1 재확인). (c) **고급 sync 옵션 폼** — 백엔드는 완비(`open_noatime`/`batch_files`/`bufsize`/`chmod`/`chown`), 프론트 타입·폼만 없다. (d) **Sparkline 유효점 1개** — 점을 그린다, "—" 로 뭉개지 않는다. 새 pip/npm 의존성 0, **새 DB 테이블·컬럼 0(스키마 무접촉)**, 러너·에이전트 무접촉.

**Architecture:** 백엔드(설정 키 → 스트림 원천 → 라우트+사유 코드) → 프론트 소비(다운로드 링크) → 독립 프론트 4건(고급 옵션 / 배지+flex td / 오류 처리 2건 / Sparkline) 순으로 쌓는다. 다운로드의 심장은 **artifacts.py 의 봉쇄 사슬을 함수로 승격해 뷰와 다운로드가 공유**하는 것이다: `open_artifact_stream()` 이 검사를 끝낸 `(fd, size)` 를 돌려주고, `read_artifact` 는 그 위에 재구축(동작 불변 — 기존 테스트 전체가 회귀 그물), 다운로드는 그 fd 를 제너레이터(64KiB 청크, try/finally close)에 넘긴다. **경로 문자열은 두 번 해석되지 않는다.** 413 판정은 봉쇄 통과 **뒤에만**(존재·크기 오라클 금지), open/봉쇄 실패는 전부 뷰 라우트와 **body 까지 동일한** 404 다. 프론트를 크게 바꾸는 슬라이스라 슬라이스 23 e2e 9건의 영향을 파일 단위로 판정했다(§1 재확인 마지막 항) — 수정 필요는 `03-layout.spec.ts` 의 `knownNonTableCells: 1` 제거 **1곳**뿐이고, 그 제거가 flex td 수리의 e2e-RED 다.

**Tech Stack:** Python 표준 라이브러리 + FastAPI `StreamingResponse`(설치본 starlette 1.3.1 — 동기 제너레이터를 청크마다 `anyio.to_thread.run_sync(next, it)` 로 위임함을 재실측, §1 재확인 4). 프론트는 React + tanstack-query 기존 체인. e2e 는 슬라이스 23 하네스 그대로.

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-08-11-dms-portal-features-slice26-design.md`. 플랜과 충돌하면 **설계가 이긴다**(단, 아래 「설계 §1 전제 재확인」의 정정은 이 플랜이 2026-08-12 에 코드로 재실측한 사실이다 — 특히 슬라이스 25 가 routes_artifacts.py 를 크게 바꿨고, 슬라이스 23 e2e 가 결함 2건을 새로 등록했다).
- **새 pip/npm 의존성 금지. 새 DB 테이블·컬럼 금지**(이 슬라이스는 스키마 무접촉 — `tests/test_migrations.py` 무접촉 초록이 증거). **신설 사유 코드는 `artifact_too_large` 정확히 1종** — `frontend/src/lib/reasonCodes.json` 과 `api.ts` REASON_MESSAGES **양쪽**에 같은 커밋으로 등록한다(양방향 계약: `reasonCodes.test.ts` + `tests/test_reason_codes_coverage.py` 의 AST 추출이 `HTTPException(..., detail="artifact_too_large")` 리터럴을 잡는다). **reasonCodes.json 은 항목 추가만 — 재포맷 금지**(배열 한 줄 삽입, diff 최소).
- **새 설정 키는 정확히 1개** `DMS_ARTIFACT_DOWNLOAD_MAX_BYTES` — `config.py`(`_SERVER_INT_KEYS` 관례)와 `deploy/k8s/20-config.yaml` **양쪽**(운영 단일 진실 — 코드 기본값만 있고 config 에 없으면 운영자가 존재를 모른다).
- **다운로드 TOCTOU 불변식(이 슬라이스의 심장, 각각 뮤테이션으로 증명한다)**: ① 검사한 fd 그대로 스트림 — 경로 재해석 금지. ② fstat 시점 size 만큼만 전송(스트림 중 파일이 자라도 응답 불변, 줄면 조기 EOF 로 정직하게 실패 — 0 채움 금지). ③ 413 은 봉쇄·소유권 통과 **뒤에만**(순서 역전은 크기 오라클). ④ open/봉쇄 실패는 전부 404 `artifact_not_found`, 뷰 라우트 404 와 **body 까지 동일**(오라클 유지). ⑤ 제너레이터 예외·절단 시 fd 누수 금지 — try/finally close 를 명시 `close()` 테스트로 못 박는다(starlette 는 body_iterator 를 명시적으로 close 하지 않는다 — §1 재확인 4 의 신규 실측).
- **null(모름)과 실패·0 을 섞지 않는다**: 0 바이트 아티팩트는 정상 다운로드(빈 파일)다 — 404 가 아니다. 고급 옵션의 "미입력" 판정은 빈 문자열 기준이지 truthy 검사가 아니다(0·"" 함정). Sparkline 1점은 실측값이지 결측("—")이 아니다.
- **커밋은 pathspec 으로 한정**: 신규 파일만 `git add <파일>` 선행 후, 항상 `git commit -m "..." -- <경로들>`. `git add -A`·`git add .`·`git commit -a` **금지**.
- **뮤테이션 원복에 `git checkout` 금지**(과거 자기 편집까지 날아간 사고). 뮤테이션 전에 `mkdir -p /tmp/slice26-mut && cp <파일> /tmp/slice26-mut/` 로 사본을 뜨고, 확인 후 `cp /tmp/slice26-mut/<파일명> <파일>` 로 되돌린다.
- **origin push 금지, 브랜치 변경 금지**(현재 `worktree-dms-slice22plus`, HEAD 9598d4b = origin/main). **플랜 태스크에서 `deploy/k8s` 의 이미지 태그 변경 금지**(d37 범프는 「플랜 이후: 배포·실증」의 첫 단계) — 단 **`20-config.yaml` 의 새 키 1줄은 예외**(Task 1, 값·태그 무변경). `docs/` 아래는 이 플랜 파일 외 생성·수정 금지(실증 후 BACKLOG 갱신은 플랜 밖 관례).
- `legacy/` 아래는 읽기 전용 — 수정·이동·삭제·추가 금지, import 금지, 코드 복사 금지.
- 백엔드 테스트 명령(이 워크트리 전용, `.venv` 는 워크트리 밖 공용):
  `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest <대상> -q`
  전체 스위트는 **포그라운드**, Bash timeout 900000ms. **기준선 1233 passed(~426s).**
- 프론트: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run`(**기준선 232 passed / 49 files** — 2026-08-12 재실측), 타입체크 `npx tsc -b`.
- e2e: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npm run test:e2e`(**기준선 9 passed**, ~25s, dist 빌드 포함). 단일 파일은 `npm run test:e2e -- e2e/03-layout.spec.ts`.
- 주석은 **한국어**로 「왜」를 적는다.

## 설계 §1 전제 재확인 (2026-08-12, 코드 직접 실측)

설계는 2026-08-11 에 쓰였고 그 사이 슬라이스 22·24·23·25 가 들어갔다. 10개 항목 전부 재확인 — **결론 요지는 전부 유지**된다. 정정은 라인 드리프트 다수 + 버전 정정 1건(starlette) + 신규 사실 3건(슬라이스 25 의 routes_artifacts 개편, 슬라이스 23 의 신규 결함 2건, e2e 회귀 그물의 존재)이다.

| 설계 §1 항목 | 재확인 결과 |
|---|---|
| 1. 열람은 256KB 꼬리 전용 | ✓ 유지. `MAX_BYTES` `artifacts.py:34`, lseek 꼬리 `:204-207`(lseek `:205`), 강제 디코드 `:212`, JobViewer `<pre>` `JobViewer.tsx:87` — 전부 설계 라인 그대로. `scan_report_too_large` 503 은 `routes_scan_paths.py:143-146`(raise `:146`) ✓. 전체 파일 획득 수단 부재 ✓ |
| 2. artifacts.py 보안 불변식 | ✓ 전부 유지, 라인 무드리프트: 머리주석 `:5-8`, 단일 open `:189`, fstat S_ISREG `:196-197`, fd 봉쇄 `:202`, open 실패 뭉개기 `:190-193`, FIFO+스레드풀 주석 `:183-188`, 하드링크 한계 `:69-77`, `MAX_ENTRIES` `:37`·`MAX_SCAN` `:41`. **단 404 통일 지점은 드리프트**: `routes_artifacts.py:40-44` → **`:42-46`**(슬라이스 25 가 `json` import 와 /logs 개편을 얹어 밀렸다) |
| 3. 세션 쿠키·소유권 | ✓ 유지, 드리프트: `dms_session` 은 `api/app.py:44-45` → **`:55`**(슬라이스 22 가 위에 쌓임 — 슬라이스 23 플랜의 정정과 동일). `_owned_job` `routes_jobs.py:24-33` ✓ 그대로(admin 전체 열람 포함) |
| 4. Starlette 동기 제너레이터 스레드풀 | **결론 유지, 버전 정정**: 설치본은 0.52.1 이 아니라 **starlette 1.3.1 / fastapi 0.141.1** 이다. `StreamingResponse.__init__` 이 동기 iterable 을 `iterate_in_threadpool` 로 감싸고, 그것이 청크마다 `anyio.to_thread.run_sync(_next, it)` 를 부름을 소스로 재실측 — 느린 클라이언트가 스레드를 점유하지 않는다는 §1-2 대비 논지 그대로. **신규 실측**: `stream_response` 는 body_iterator 를 명시적으로 close **하지 않는다** — 절단 시 동기 제너레이터의 finally 는 async-gen 파이널라이즈→GC 에 얹힌다. 설계 §4 의 "GC 에 얹히는 경우까지 테스트로 못 박는다"가 코드 사실로 확정됐다(Task 2 의 명시 close() 테스트가 그 처방) |
| 5. 아티팩트 삭제 코드 없음 | ✓ 유지. `src/dms/` 전체에서 unlink/remove 는 `artifact_base.py:78`(validate 자기 프로브) 하나뿐 — 재grep 확인. 삭제·보존 UI 는 §7 그대로 |
| 6. 고급 sync 옵션 백엔드 완비 | ✓ 유지. `_OPTION_SPECS` SYNC `domain.py:124-130` ✓ 그대로(`open_noatime` `:126`, `batch_files` 1..1,000,000 `:127`, `bufsize` 4096..1GiB `:128`, chmod/chown `:129`), 정규식 `:112-113` ✓(`_CHMOD_ITEM_RE` 는 콤마 항목별 fullmatch `:150-153`, `_CHOWN_RE` `:154-156`). 플래그 매핑 `execution_manifests.py:9-13` ✓. **auto-chown 억제는 드리프트**: `:69-71` → **`:75-77`**(`"chown" in (spec.options or {})` `:75`). 프론트 bool 4종 `SubmitJob.tsx:78` ✓ 정확, `SubmitBody.options` 의 string 배제 `useJobs.ts:28` ✓ 정확 |
| 7. 배치는 scan\|sync | ✓ 유지. `validate_batch` `domain.py:222-224` ✓, `BatchCreate.tsx:20` options `{}` ✓ — §7(자르는 것) 전제 유효 |
| 8. FAST-FOLLOW 7건 중 1건 해소·6건 실재 | ✓ 유지 — **6건 전부 여전히 미해소, 어느 것도 그 사이 고쳐지지 않았다**(전건 재실측). 해소된 RequestDetail 로딩 상태 `:112-119` ✓ 그대로. ① `StoragesList.tsx:48` StatusPill 무 variant ✓(Ready/Degraded 둘 다 neutral), `reconciler.py:20-27` 는 **Ready/Degraded/Unknown 3종**(설계 표기 :19-27 — Unknown 도 있다. §2.4-1 의 "그 외→neutral" 이 Unknown 을 담당), `planner.py:149` Degraded 에도 잡 전송 ✓ 정확. ② api.ts 401 중복은 **`:201-209` vs `:210-217` 로 드리프트**(사유 코드 증가로 밀림, 구조 동일). ③ `Login.tsx:30` ✓ 정확. ④ 잡 취소 오류 미표시 `RequestDetail.tsx:187-192` ✓(요청 취소 오류는 `:164-166` 에서 표시 ✓), ConfirmDialog 무 reset ✓(닫기 `:25`) — **같은 파일 StoragesList.tsx:14-16 의 DeleteButton useEffect reset 이 정확한 선례다**. ⑤ Home `router.tsx:29-34` ✓ 정확, `RequireRole.tsx:6` isError→/login ✓. ⑥ 무효화 중복 `useJobs.ts:43-46,53-56,64-67` ✓ 정확 |
| 9. Sparkline 유효점 1개 | ✓ 유지. **경로 정정**: `frontend/src/components/ui/Sparkline.tsx`(설계 표기 「components/Sparkline」). "—" 폴백 `:33-34`, step=0 `:13`, bare M `:24`, `NodeMetricsSection.tsx:38` 렌더 — 전부 그대로. 기존 `Sparkline.test.tsx:11` 이 결측 구간 path 를 그대로 단언한다 — `sparklinePath` 무변경(컴포넌트 분기) 결정과 정합 |
| 10. nosniff 0건·위협 표면 | ✓ 유지. `src/`·`frontend/src/` 에서 nosniff 0건 재grep. 요청자가 phase 디렉터리 소유자라는 위협 모델(§1-2 머리주석) 불변 |

**추가 정정·판단(설계 본문·수치·슬라이스 23·25 이후의 신규 사실):**

- **슬라이스 25 가 routes_artifacts.py 를 크게 바꿨다**: `json` import, `_render_log`/`_archived_entries` 신설, `/logs` 가 라이브 우선·박제 폴백(`:97-125`)이 됐다. 새 다운로드 라우트는 이 파일 끝에 얹히고, 필요한 import(`MAX_BYTES` 등)는 이미 있으며 `fastapi.responses.StreamingResponse` 만 추가다. `_base()`(`:14-19`)와 `_owned_job` 재사용 ✓.
- **슬라이스 23 신규 결함 2건과 §2.4 목록의 겹침 판정**: 둘 다 설계 §2.4 의 6건과 **겹치지 않는 별건**이다(설계가 굳은 뒤 e2e 가 찾았다, BACKLOG §2.2 🔴 2건).
  - **`StoragesList.tsx:50` flex td → 이 슬라이스에 편입한다(판단).** 근거 3개: ① Task 6 이 어차피 같은 파일의 두 줄 위(`:48`)를 고친다 — 별도 슬라이스로 미루면 같은 파일을 두 번 연다. ② 9fbef86 이 계정 표에서 걷어낸 것과 동일 구조라 수리 형태가 이미 검증돼 있다(td 안 div 로 flex 이동). ③ e2e `03-layout.spec.ts:45` 가 `knownNonTableCells: 1` 로 **정확 개수**를 단언한다(`layout.ts:135` `.toBe()`) — 고치면 0 이 되어 e2e 가 "이 줄을 지우라"고 빨개진다(BACKLOG 명시: "고칠 때 그 인자도 함께 지울 것"). **수리와 e2e 인자 제거는 같은 커밋이어야 한다**(Task 6).
  - **로그아웃 URL 결함(useLogout 의 `qc.clear()`)은 범위 밖 유지(판단).** 근거: 설계의 닫힌 6건 목록 밖이고, 수리는 쿼리 캐시 수명주기(clear vs invalidate vs 명시 nav)의 별도 결정이 필요하며, e2e E1 은 이 동작을 계약으로 굳히지 않아 지금 안 고쳐도 아무것도 빨개지지 않는다. BACKLOG 에 남는다(열린 질문 3).
- **설계 §3 "StoragesList/대시보드" 표기 정정**: 대시보드에는 스토리지 상태 배지가 **없다**(Dashboard.tsx 의 StatusPill 은 요청 상태·롤아웃 verdict 뿐 — 실측). 배지 색 변화는 `StoragesList.tsx:48` 한 곳이다. SubmitJob 의 StoragePicker 는 상태를 텍스트로 병기(`:25-27`)하며 배지가 아니다 — 무접촉.
- **뷰 라우트의 아티팩트 URL 은 encode 없이 조립된다**(`useArtifacts.ts:12`) — NAME_RE(`[A-Za-z0-9._-]+`) 상 실해는 없으나, 새 다운로드 href 는 설계 §5 대로 `encodeURIComponent` 를 명시한다(방어 관례).
- **e2e 9건 영향, 파일 단위 판정(핵심 신규 사실 — 슬라이스 23 의 e2e 가 이제 회귀 그물이다)**:
  - `01-boot-session.spec.ts`(E1): Login.tsx 변경은 오류 분기 렌더뿐(라벨·버튼 불변), 로그아웃 흐름 무접촉 → **무영향**.
  - `02-spa-fallback.spec.ts`(E2): 라우트 표 무변경, Home 변경은 isError 분기 추가뿐(정상 경로 불변) → **무영향**.
  - `03-layout.spec.ts`(E3): **유일한 수정 지점** — flex td 수리로 `knownNonTableCells: 1` 을 지워야 한다(위 판단). 대시보드의 Sparkline circle 은 L1~L4(문서 오버플로·td display·aside 폭·한줄 요소) 어디에도 안 걸린다. SubmitJob 의 `<details>` 는 E3 순회 화면(`/jobs/new` 미방문) 밖이다 → **인자 제거 1곳 외 무영향**.
  - `04-job-flow.spec.ts`(E4): SubmitScan 무접촉(고급 옵션은 sync 전용 = SubmitJob.tsx), RequestDetail 변경은 오류시에만 렌더 → **무영향**.
  - `05-polling.spec.ts`(E5/E6): useRequestJobs 의 refetchInterval 무변경, 무효화 dedup 은 mutation 경로(E5/E6 은 mutation 을 안 탄다) → **무영향**.
  - 기대값: Task 6 이후 **9 passed 유지**(시나리오 수 불변).
- §5 기준선 "백엔드 1131 / 프론트 228(49)" → **백엔드 1233 passed(~426s) / 프론트 232 passed·49 files / e2e 9 passed**(슬라이스 22·24·23·25 반영, 2026-08-12 실측 — 프론트는 이 세션에서 직접 재실행 확인).
- 배포 태그 현황: **dms d36 / dms-agent d35 / dms-mpifileutils(잡) d35**(매니페스트 실측). 이 슬라이스는 러너·에이전트·스키마 무접촉 → **`dms` 만 d37**, migrate Job 재실행 불요(스키마 무변경 — 근거는 `tests/test_migrations.py` 무접촉 초록. initContainer 의 migrate 는 어차피 no-op 으로 돈다).
- 바이트 포맷터: `humanBytes` 는 NodesList·JobStatsSection 에 **국소 사본 관례**로 존재한다(JobStatsSection.tsx:25-36 의 주석이 관례 근거 — "공용 모듈은 이르다"). Task 4 는 같은 관례로 JobViewer 국소 사본(+KiB 단위)을 만든다.

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/dms/config.py`, `deploy/k8s/20-config.yaml`, `tests/test_config.py` (수정) | Task 1: `DMS_ARTIFACT_DOWNLOAD_MAX_BYTES` — `_SERVER_INT_KEYS`+필드+운영 config |
| `src/dms/api/artifacts.py` (수정), `tests/test_artifacts_stream.py` (신규) | Task 2: `open_artifact_stream`/`stream_artifact_fd` + read_artifact 재구축 |
| `src/dms/api/routes_artifacts.py`, `frontend/src/lib/reasonCodes.json`, `frontend/src/lib/api.ts` (수정), `tests/test_api_artifact_download.py` (신규) | Task 3: 다운로드 라우트 + `artifact_too_large` 양쪽 등록 |
| `frontend/src/features/jobs/JobViewer.tsx`, `JobViewer.test.tsx` (수정) | Task 4: 다운로드 링크(크기)·truncated 안내 |
| `frontend/src/features/jobs/useJobs.ts`, `SubmitJob.tsx`, `SubmitJob.test.tsx` (수정) | Task 5: 고급 sync 옵션 폼 + options 타입 확장 |
| `frontend/src/lib/jobState.ts`, `jobState.test.ts`, `frontend/src/features/storages/StoragesList.tsx`, `StoragesList.test.tsx`, `frontend/e2e/03-layout.spec.ts` (수정) | Task 6: `storagePillVariant` + flex td 수리 + e2e 인자 제거 |
| `frontend/src/lib/api.ts`, `api.test.ts`, `frontend/src/features/auth/Login.tsx`, `Login.test.tsx` (수정) | Task 7: 401 분기 통합 + Login 무가드 캐스트 |
| `frontend/src/features/jobs/RequestDetail.tsx`, `RequestDetail.test.tsx`, `ConfirmDialog.tsx`, `ConfirmDialog.test.tsx`, `frontend/src/app/router.tsx`, `router.test.tsx`, `frontend/src/features/jobs/useJobs.ts`, `useJobs.test.ts`(있으면) (수정) | Task 8: 취소 오류 표시·reset·Home isError·무효화 dedup |
| `frontend/src/components/ui/Sparkline.tsx`, `Sparkline.test.tsx` (수정) | Task 9: 유효점 1개 → circle |

---

### Task 0: 기준선 확인 (커밋 없음)

**Files:** 없음(검증만)

- [ ] **Step 1: 백엔드 기준선** — Run(포그라운드, timeout 900000ms): 위 백엔드 명령으로 `tests` 전체. Expected: `1233 passed`(~426s). 다르면 진행 전에 보고.
- [ ] **Step 2: 프론트 기준선 + 타입체크** — `npx vitest run && npx tsc -b`. Expected: `232 passed / 49 files`, tsc 무출력 exit 0.
- [ ] **Step 3: e2e 기준선** — `npm run test:e2e`. Expected: `9 passed`. 여기 빨강이면 이 슬라이스 밖의 문제다.

---

### Task 1: 설정 키 — `DMS_ARTIFACT_DOWNLOAD_MAX_BYTES` (config.py + 20-config.yaml 양쪽)

**Files:** Modify: `src/dms/config.py`, `deploy/k8s/20-config.yaml`, `tests/test_config.py`

**Interfaces:**
- Produces: `Settings.artifact_download_max_bytes: int = 268435456`(256MiB). `_SERVER_INT_KEYS`(`config.py:9`) 튜플에 `("DMS_ARTIFACT_DOWNLOAD_MAX_BYTES", "artifact_download_max_bytes", 268435456)` — `from_env` 의 `**extra` 가 자동 배선한다(관례). **함정**: 튜플에만 넣고 dataclass 필드를 빼먹으면 `**extra` 가 TypeError 로 기동 실패하고, 필드만 넣으면 env 가 무시된다 — `test_config_phase3c.py:37,53` 의 기존 주석이 정확히 이 두 사고를 다룬다. 양쪽 다 테스트로 고정한다.
- `20-config.yaml`: `DMS_ARTIFACT_BASE_URI`(`:27`) 근처에 `DMS_ARTIFACT_DOWNLOAD_MAX_BYTES: "268435456"` + 주석("다운로드 상한. 뷰 256KB 와 별개. sparse 초대형 파일 공격을 여기서 끊는다 — 413 artifact_too_large").

- [ ] **Step 1(RED)**: `tests/test_config.py` 끝에 2건 추가 — `test_artifact_download_max_bytes_default`(`from_env` 최소 env → 필드 == 268435456), `test_artifact_download_max_bytes_env_override`(env `"1048576"` → 1048576). 실행 → 2건 FAIL(`unexpected keyword` 또는 AttributeError).
- [ ] **Step 2(GREEN)**: config.py 두 곳 + 20-config.yaml 1줄. `pytest tests/test_config.py tests/test_config_phase3c.py -q` 전부 PASS.
- [ ] **Step 3(뮤테이션)**: 사본 후 `_SERVER_INT_KEYS` 항목만 삭제 → override 테스트 RED(기본값만 계속 쓰이는 조용한 회귀의 재현). `cp` 원복, 재확인.
- [ ] **Step 4: 커밋**
```bash
git commit -m "feat(config): DMS_ARTIFACT_DOWNLOAD_MAX_BYTES 256MiB — _SERVER_INT_KEYS·20-config 양쪽(운영 단일 진실), 다운로드 상한 자리" -- src/dms/config.py deploy/k8s/20-config.yaml tests/test_config.py
```

---

### Task 2: artifacts.py — 스트림 원천 `open_artifact_stream` + `stream_artifact_fd` (TOCTOU 불변식의 본체)

**Files:** Modify: `src/dms/api/artifacts.py` / Create: `tests/test_artifacts_stream.py`

**Interfaces:**
- `open_artifact_stream(base, job_id, phase, name, max_bytes: int | None) -> tuple[int, int]` — **read_artifact 와 같은 순서·같은 함수의 봉쇄 사슬**: `resolve_artifact_path`(화이트리스트) → 단일 `os.open(O_RDONLY|O_NOFOLLOW|O_NONBLOCK)`(실패 전부 `ArtifactError("artifact_not_found")` — errno 비유출) → `os.fstat` `S_ISREG`(FIFO 는 O_NONBLOCK 덕에 블록 없이 여기서 탈락) → `_assert_contained(/proc/self/fd realpath)` → **그 뒤에만** `max_bytes is not None and size > max_bytes` 면 close + `ArtifactError("artifact_too_large", str(size))` → `(fd, size)` 반환. 반환 전 어떤 실패든 열린 fd 는 닫는다. **검사 순서가 계약이다**: 크기 검사가 봉쇄보다 앞서면 404/413 갈림이 봉쇄 밖 파일의 존재·크기 오라클이 된다.
- `read_artifact` 를 이 함수 위에 재구축한다: `fd, size = open_artifact_stream(base, job_id, phase, name, max_bytes=None)` 후 기존 lseek 꼬리·`_read_capped`·디코드 로직 유지 — **동작 불변**이고 기존 `test_artifacts_paths.py`·`test_api_artifacts.py` 전체가 회귀 그물이다. 이것이 "다운로드가 뷰와 같은 코드 경로를 공유한다"의 실체다(한쪽만 고치는 드리프트 구조적 차단).
- `stream_artifact_fd(fd, size, chunk=DOWNLOAD_CHUNK)` — 동기 제너레이터. `try:` remaining=size 에서 `os.read(fd, min(chunk, remaining))` 루프, 빈 read(조기 EOF=truncate)면 중단(**0 채움 금지**), size 초과 전송 금지(append 무시). `finally: os.close(fd)`. `DOWNLOAD_CHUNK = 64 * 1024` 모듈 상수. fd 소유권은 제너레이터가 진다 — 호출자는 반환 즉시 손을 뗀다.

- [ ] **Step 1(RED)**: `tests/test_artifacts_stream.py` 신규 — 기존 `test_artifacts_paths.py` 의 셋업 관례(tmp_path 에 `<jid>/<phase>` 구성)를 따른다. 테스트 목록과 핵심 단언:
  - `test_open_stream_returns_fd_and_fstat_size` — 정상 파일 → fd 로 `os.read` 가 내용과 일치, size == 실크기.
  - `test_open_stream_symlink_name_is_not_found` — 이름이 바깥 파일 심링크 → `artifact_not_found`(ELOOP 뭉개기).
  - `test_open_stream_fifo_returns_promptly_not_found` — `os.mkfifo` → 시간 상한(예: 2s) 안에 `artifact_not_found`(`test_artifacts_paths.py:209` 의 기존 FIFO 테스트와 같은 시간 재기 방식).
  - `test_open_stream_swapped_phase_dir_is_not_found_not_too_large`(**순서 계약의 핵심**) — phase 디렉터리를 **max_bytes 보다 큰** 바깥 파일이 든 디렉터리로 심링크 스왑 → 기대는 `artifact_not_found` 지 **절대 `artifact_too_large` 가 아니다**(413 이 먼저면 봉쇄 밖 파일의 크기가 새는 오라클).
  - `test_open_stream_too_large_is_rejected_and_fd_closed` — cap+1 크기 정규 파일, `max_bytes=cap` → `artifact_too_large`, 그리고 `os.listdir("/proc/self/fd")` 개수가 호출 전과 같다(fd 누수 없음).
  - `test_zero_byte_artifact_streams_empty_not_error` — 0 바이트 파일 → `(fd, 0)` 정상 반환, 제너레이터는 즉시 종료·빈 바이트(0 은 정상값이다 — 404 로 뭉개지 않는다).
  - `test_stream_sends_exactly_fstat_size_when_file_grows` — open 후 파일에 append → 소비 결과가 **정확히 원본 size 바이트**(뒤에 붙은 것 미전송).
  - `test_stream_truncate_causes_early_honest_eof` — open 후 truncate → 소비 결과가 size 미만·잘린 내용 그대로(0 채움 없음, 무한 루프 없음).
  - `test_stream_closes_fd_on_completion` / `test_stream_closes_fd_on_early_close` — 완주 후·1청크 소비 후 `gen.close()` 후 각각 `pytest.raises(OSError): os.fstat(fd)`(EBADF). **후자가 클라이언트 절단의 단위 모델이다**(§1 재확인 4: starlette 는 close 를 안 해준다 — finally 가 유일한 방어).
  실행 → 전부 FAIL(`AttributeError: open_artifact_stream`).
- [ ] **Step 2(GREEN)**: artifacts.py 에 두 함수 추가 + read_artifact 재구축. `pytest tests/test_artifacts_stream.py tests/test_artifacts_paths.py tests/test_api_artifacts.py tests/test_api_scan_path_stats.py -q` 전부 PASS(뒤 세 파일 = 재구축 무회귀 그물).
- [ ] **Step 3(뮤테이션 — 3건 각각, 사본→편집→확인→cp 원복)**:
  (a) `stream_artifact_fd` 의 `finally: os.close(fd)` 제거 → fd close 테스트 2건 RED(조용한 fd 누수의 실증).
  (b) 제너레이터의 remaining 캡 제거(EOF 까지 읽기) → grow 테스트 RED(사용자가 자기 파일을 키워 응답을 무한히 늘리는 그 공격).
  (c) `open_artifact_stream` 에서 크기 검사를 `_assert_contained` **앞**으로 이동 → swapped-phase-dir 테스트 RED(오라클 개방의 실증 — 이 뮤테이션이 이 태스크의 지정 뮤테이션이다).
- [ ] **Step 4: 커밋**
```bash
git add tests/test_artifacts_stream.py
git commit -m "feat(artifacts): open_artifact_stream/stream_artifact_fd — 뷰와 같은 fd 봉쇄 사슬 공유, 413 은 봉쇄 뒤에만, fstat size 캡·try/finally close" -- src/dms/api/artifacts.py tests/test_artifacts_stream.py
```

---

### Task 3: 다운로드 라우트 + 사유 코드 `artifact_too_large` 양쪽 등록

**Files:** Modify: `src/dms/api/routes_artifacts.py`, `frontend/src/lib/reasonCodes.json`, `frontend/src/lib/api.ts` / Create: `tests/test_api_artifact_download.py`

**Interfaces:**
- 라우트: `GET /api/user/jobs/{job_id}/artifacts/{phase}/{name}/download`(`require_user` + `_owned_job` — 기존 4-세그먼트 뷰 라우트와 경로 충돌 없음). 본문 계약:
  - `open_artifact_stream(_base(request), job_id, phase, name, max_bytes=request.app.state.settings.artifact_download_max_bytes)`.
  - `ArtifactError` 매핑은 **뷰 라우트(`:42-47`)와 동일 골격 + 413 한 줄**: `artifact_too_large` → `HTTPException(413, detail="artifact_too_large")`(리터럴 직서 — AST 커버리지 테스트가 이 리터럴을 추출한다), `artifact_not_found`/`artifact_forbidden` → **404 `artifact_not_found`(뷰와 body 동일)**, 그 외(`invalid_phase` 등) → 422(뷰와 동일 — 화이트리스트 거부는 공개 지식이라 오라클이 아니다).
  - 성공: `StreamingResponse(stream_artifact_fd(fd, size), media_type="application/octet-stream", headers={"Content-Length": str(size), "Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff"})` — NAME_RE 가 헤더 인젝션을 구성상 차단(`artifacts.py:32`), 셋이 함께 stored-XSS 경로를 닫는다(설계 §2.2). inline 표시 절대 금지. tail 파라미터 없음(다운로드는 전체가 정의다).
- `reasonCodes.json`: `"artifact_not_found"` 가 있는 줄 블록(`:24` 부근)에 `"artifact_too_large"` **항목 추가만**(재포맷 금지). `api.ts` REASON_MESSAGES: `artifact_too_large: "파일이 다운로드 상한을 넘습니다 — 관리자에게 문의하세요"` (artifact 계열 `:63` 부근). **같은 커밋** — 양방향 계약 테스트 조건.

- [ ] **Step 1(RED)**: `tests/test_api_artifact_download.py` 신규 — `test_api_artifacts.py` 의 `_client`/`_login`/`_confirmpending_job` 골격을 그대로 가져온다(모듈 복붙 관례). 테스트 목록:
  - `test_download_streams_exact_bytes_including_binary` — `bytes(range(256)) * 64`(16KB, NUL 포함) 파일 → `r.content` 완전 일치(디코드 오염 없음) + `r.headers["content-length"] == str(len)`.
  - `test_download_headers_are_pinned` — 헤더 3종 정확값(`application/octet-stream`, `attachment; filename="stdout.log"`, `nosniff`).
  - `test_download_404_body_is_identical_to_view_404`(**오라클 계약**) — 없는 이름과 바깥 심링크 이름 각각에 대해, 다운로드 404 의 `(status_code, r.json())` 이 뷰 라우트 404 와 **완전 동일**.
  - `test_download_fifo_is_404` — mkfifo → 404(라우트 계층 재확인).
  - `test_download_too_large_is_413_artifact_too_large` — `Settings(artifact_download_max_bytes=1024)` 로 앱 구성(작은 cap — 테스트에서 256MiB 파일을 만들지 않는다), 1025바이트 파일 → 413 + `detail == "artifact_too_large"`.
  - `test_download_at_cap_is_200` — 정확히 1024바이트 → 200(경계 off-by-one 고정).
  - `test_zero_byte_download_is_200_empty` — 0 바이트 → 200, `content-length: 0`, 빈 body.
  - `test_view_route_is_unchanged` — 같은 파일에 뷰 GET → 기존 JSON 형태(content/truncated) 그대로(설계 §5 "뷰 라우트 무변경").
  - `test_not_owner_is_404` — 타 사용자 로그인 → 404 `job_not_found`(`_owned_job` 재사용 확인).
  실행 → 라우트 부재로 404/405 계열 FAIL. **프론트 계약 RED 도 함께 실측**: 백엔드에 리터럴이 생기기 전이라 `pytest tests/test_reason_codes_coverage.py -q` 는 아직 초록이어야 하고(신규 리터럴 없음), 라우트 구현 후 json 미등록 상태로 돌리면 빨강이 되는지 Step 2 에서 순서로 확인한다.
- [ ] **Step 2(GREEN + 계약 순서 실측)**: ① 라우트만 먼저 구현하고 `pytest tests/test_reason_codes_coverage.py -q` → **RED**(json 미등록 — 계약이 무는 이빨의 실측). ② reasonCodes.json + api.ts 등록 → 백엔드 `pytest tests/test_api_artifact_download.py tests/test_api_artifacts.py tests/test_reason_codes_coverage.py -q` 전부 PASS, 프론트 `npx vitest run src/lib/reasonCodes.test.ts` PASS(REASON_MESSAGES 양방향).
- [ ] **Step 3(뮤테이션)**: 사본 후 404 매핑의 detail 을 `"download_not_found"` 로 변경 → 오라클 동일성 테스트 RED(404 가 갈리는 순간이 곧 오라클이라는 실증). 겸사 `X-Content-Type-Options` 헤더 삭제 → 헤더 테스트 RED. `cp` 원복, 재확인.
- [ ] **Step 4: 커밋**
```bash
git add tests/test_api_artifact_download.py
git commit -m "feat(api): 아티팩트 다운로드 라우트 — 검사한 fd 그대로 스트림, 헤더 3종, 404 는 뷰와 body 동일(오라클), 413 artifact_too_large 양쪽 등록" -- src/dms/api/routes_artifacts.py frontend/src/lib/reasonCodes.json frontend/src/lib/api.ts tests/test_api_artifact_download.py
```

---

### Task 4: JobViewer — 다운로드 링크(크기)·truncated 안내

**Files:** Modify: `frontend/src/features/jobs/JobViewer.tsx`, `frontend/src/features/jobs/JobViewer.test.tsx`

**Interfaces:**
- 아티팩트 탭 콘텐츠 블록(`:80-89`) 상단에 `<a href={\`/api/user/jobs/${jobId}/artifacts/${encodeURIComponent(phase)}/${encodeURIComponent(name)}/download\`} download>` — `<a href>` 네비게이션에도 세션 쿠키가 실리므로(§1-3, `app.py:55`) fetch/blob 불요. 라벨 「다운로드 (크기)」 — 크기는 목록 `entries` 에서 phase+name 일치 항목의 `size`(`artifacts.py:138` 가 이미 준다). `humanBytes` 국소 사본(KiB·B 포함, `JobStatsSection.tsx:25-36` 관례 주석 인용) — **0 바이트는 "0 B"**(0 은 정상값, "—" 는 null 전용).
- `truncated` 배지(`:82-86`) 옆에 「전체는 다운로드로 받으세요」 — 256KB 꼬리와 전체 파일의 관계를 화면이 말한다(설계 §3).
- 오류 시 `<a>` 는 브라우저가 JSON 오류 본문으로 이동할 수 있다 — 설계 §4 가 명시한 수용 트레이드오프(fetch+blob 는 상한 크기까지 메모리 버퍼링이라 더 나쁘다). 주석으로 남긴다.

- [ ] **Step 1(RED)**: JobViewer.test.tsx — 기존 msw 핸들러의 artifacts 목록에 size 를 실은 채: ① 링크 href 정확값(+`download` 속성 존재) ② 라벨에 크기 병기 ③ truncated 응답이면 안내 문구 visible, 아니면 absent. 실행 → 3건 FAIL.
- [ ] **Step 2(GREEN)**: 구현. `npx vitest run src/features/jobs/JobViewer.test.tsx && npx tsc -b` PASS.
- [ ] **Step 3(뮤테이션)**: 사본 후 href 를 뷰 경로(`/download` 접미 제거)로 → href 테스트 RED(뷰 JSON 을 "다운로드"로 내미는 회귀의 실증). `cp` 원복.
- [ ] **Step 4: 커밋**
```bash
git commit -m "feat(portal): 아티팩트 다운로드 링크(크기 병기) + truncated 시 전체 다운로드 안내 — a href 로 쿠키 동승, encodeURIComponent" -- frontend/src/features/jobs/JobViewer.tsx frontend/src/features/jobs/JobViewer.test.tsx
```

---

### Task 5: 고급 sync 옵션 폼 — 노출만, 검증은 서버가 최종 심판

**Files:** Modify: `frontend/src/features/jobs/useJobs.ts`, `frontend/src/features/jobs/SubmitJob.tsx`, `frontend/src/features/jobs/SubmitJob.test.tsx`

**Interfaces:**
- `SubmitBody.options: Record<string, boolean | number | string>`(`useJobs.ts:28`) — string 개방이 chmod/chown 을 싣는 유일한 관문이다.
- SubmitJob sync 분기에 `<details>` 접힘 「고급 옵션」(기본 접힘 — 기존 동선 불변): `open_noatime` 체크박스(§1-6 누락 복구), `batch_files`(1..1,000,000), `bufsize`(**바이트** 단위 4096..1,073,741,824 — 단위를 라벨에 명기), `chmod`/`chown` 텍스트. 상태는 문자열 필드(`batchFiles:"", bufsize:"", chmod:"", chown:""` + `openNoatime:false`)로 들고, **빈 문자열일 때만 생략**(truthy 검사 금지 — `.trim() === ""` 판정. 0 같은 범위 밖 값은 "미입력"이 아니라 클라이언트 검증 오류다). 숫자 2종은 `Number()` 변환 후 number 로 전송.
- 클라이언트 미러(즉답용, 서버 422 `invalid_option` 이 최종 — 문구 기등록 `api.ts:82`): chmod `^[DF]?[0-7]{1,4}(,[DF]?[0-7]{1,4})*$`, chown `^([A-Za-z_][A-Za-z0-9._-]{0,63})?(:[A-Za-z_][A-Za-z0-9._-]{0,63})?$` 이면서 비어 있지 않음, 숫자 범위 2종 — `domain.py:112-113,127-128` 의 미러임을 주석에 적는다. 위반 시 `blocked` 에 합류 + 필드별 문구.
- **함정 캡션을 폼에 그대로 적는다**(설계 §2.5): "chown 을 지정하면 자동 chown 이 꺼집니다(`execution_manifests.py:75-77`). 비특권 사용자가 타인 소유를 지정하면 도구가 chown 권한이 없어 **데이터는 복사되고 잡은 Failed 로 끝납니다**."

- [ ] **Step 1(RED)**: SubmitJob.test.tsx — ① 고급 옵션 전부 미입력 제출 → `options` 에 5종 키 부재(기존 checkedOptions 회귀 겸) ② `open_noatime` 체크 → `options.open_noatime === true` ③ chmod `"D770,F660"`·chown `"alice:proj"` 입력 → 문자열 그대로 전송 ④ bufsize `"4096"` → number `4096` 전송 ⑤ chmod `"999x"` → 제출 차단+오류 문구 ⑥ bufsize `"100"`(범위 밖) → 차단. msw 로 request body 를 캡처해 단언한다. 실행 → FAIL 다수(타입 오류 포함 — `tsc` 도 RED 재료다).
- [ ] **Step 2(GREEN)**: 타입 확장 + 폼 구현. `npx vitest run src/features/jobs/SubmitJob.test.tsx && npx tsc -b` PASS.
- [ ] **Step 3(뮤테이션)**: 사본 후 빈 값 생략 로직 제거(chmod 를 항상 전송) → ① 테스트 RED(빈 문자열 chmod 가 서버 422 를 유발하는 그 회귀 — `_CHOWN_RE` 와 달리 chmod 는 빈 문자열이 fullmatch 를 통과 못 한다). `cp` 원복.
- [ ] **Step 4: 커밋**
```bash
git commit -m "feat(portal): 고급 sync 옵션 폼(open_noatime·batch_files·bufsize·chmod·chown) — 빈 값 생략, 서버 미러 검증, auto-chown 함정 캡션" -- frontend/src/features/jobs/useJobs.ts frontend/src/features/jobs/SubmitJob.tsx frontend/src/features/jobs/SubmitJob.test.tsx
```

---

### Task 6: 스토리지 배지 색 + flex td 수리 + e2e `knownNonTableCells` 제거 (같은 커밋)

**Files:** Modify: `frontend/src/lib/jobState.ts`, `frontend/src/lib/jobState.test.ts`, `frontend/src/features/storages/StoragesList.tsx`, `frontend/src/features/storages/StoragesList.test.tsx`, `frontend/e2e/03-layout.spec.ts`

**Interfaces:**
- `jobState.ts` 에 `storagePillVariant(status)` 신설(빌드 전용 `buildPillVariant` `:18-23` 선례·같은 주석 골격): `Ready→"ok"`, `Degraded→"busy"`(황색 주의 — planner 가 Degraded 에도 잡을 보내므로(`planner.py:149`) "죽음"이 아니라 "주의"가 정직하다), 그 외(**Unknown 포함** — `reconciler.py:21`)→`"neutral"`. **공유 `pillVariant` 는 무접촉**(M5 관례 — 잡/요청 배지가 바뀌면 안 된다).
- `StoragesList.tsx:48`: `<StatusPill state={s.status} variant={storagePillVariant(s.status)} />`(StatusPill 은 variant prop 을 이미 받는다 `StatusPill.tsx:11`).
- `StoragesList.tsx:50` flex td 수리(§1 재확인의 편입 판단): `<td className="flex gap-2 py-2">` → `<td className="py-2"><div className="flex items-center gap-2 whitespace-nowrap">…버튼 3개…</div></td>` — 9fbef86 이 계정 표(`AccountsList.tsx:71-72`)에서 쓴 그 수리 형태.
- `e2e/03-layout.spec.ts:38-45`: 결함 주석 블록을 "슬라이스 26 이 수리했다" 한 줄로 갈고 `{ minTableCells: 12, knownNonTableCells: 1 }` → `{ minTableCells: 12 }`. `helpers/layout.ts` 는 무접촉(인자는 미래 회귀용으로 남는다).

- [ ] **Step 1(RED — vitest)**: jobState.test.ts 에 `storagePillVariant` 매핑 4건(Ready/Degraded/Unknown/임의 문자열) + **공유 매핑 불변 단언**(`pillVariant("Ready") === "neutral"` — 누가 공유 매핑에 Ready 를 추가하는 잘못을 막는 못). StoragesList.test.tsx 에 Ready 행 배지 `text-ok`·Degraded 행 `text-busy` 클래스 단언 + **작업 셀 구조 단언**(작업 td 의 computed/className 에 flex 부재 — td 안 div 가 flex). 실행 → 신규 전부 FAIL.
- [ ] **Step 2(RED — e2e, 공허 초록 방지의 핵심)**: **코드 수리 전에** e2e 인자만 먼저 지우고 `npm run test:e2e -- e2e/03-layout.spec.ts` → **RED**(`badCells 1개(기대 0개)` — e2e 가 수리를 강제한다는 실측. 이것이 이 태스크의 e2e-TDD 다).
- [ ] **Step 3(GREEN)**: jobState.ts·StoragesList.tsx 구현 → `npx vitest run && npx tsc -b` PASS, `npm run test:e2e` **9 passed**(E3 포함 — 인자 제거 상태로 초록 = 수리 완료의 이중 증명).
- [ ] **Step 4(뮤테이션)**: 사본 후 ① `storagePillVariant` 의 Degraded 분기를 neutral 로 → vitest RED(부분 장애가 정상과 같은 색이 되는 그 결함의 재현). ② StoragesList 의 td 를 flex 로 재주입 → e2e 03 RED(기대 0 에 1 — e2e 이빨의 실증). 각각 `cp` 원복, Step 3 재확인.
- [ ] **Step 5: 커밋**
```bash
git commit -m "feat(portal): 스토리지 배지 storagePillVariant(Ready=ok/Degraded=busy) + StoragesList flex td 수리 — e2e knownNonTableCells 인자 동시 제거(슬라이스 23 결함 상환)" -- frontend/src/lib/jobState.ts frontend/src/lib/jobState.test.ts frontend/src/features/storages/StoragesList.tsx frontend/src/features/storages/StoragesList.test.tsx frontend/e2e/03-layout.spec.ts
```

---

### Task 7: api.ts 401 분기 통합 + Login 무가드 캐스트

**Files:** Modify: `frontend/src/lib/api.ts`, `frontend/src/lib/api.test.ts`, `frontend/src/features/auth/Login.tsx`, `frontend/src/features/auth/Login.test.tsx`

**Interfaces:**
- `request()` 의 두 분기(`:201-209` / `:210-217`)를 **단일 `!res.ok` 블록**으로: detail 파싱 1회(비 JSON 폴백 `http_<status>` — 401 이면 `http_401`), 그 뒤 `if (res.status === 401) window.dispatchEvent(new CustomEvent("dms:unauthorized"))`, throw `ApiError(res.status, code, reasonText(code))`. **계약**: ① 파싱 코드가 한 벌(문구 드리프트 구조 차단) ② `dms:unauthorized` 는 **401 에만** 발화(AuthContext 소비자 계약) ③ 인그레스 401(비 JSON) → `http_401` 유지(`api.ts:113-115` 주석의 그 경위).
- `Login.tsx:30`: `login.error instanceof ApiError ? login.error.message : "로그인 요청에 실패했습니다 — 네트워크 상태를 확인하세요"` — fetch 네트워크 단절은 `TypeError` 로 reject 되어 영어 원문("Failed to fetch")이 노출되던 결함.

- [ ] **Step 1(RED)**: api.test.ts — ① 401+JSON detail → 그 코드로 ApiError + 이벤트 1회 발화 ② 401+비 JSON → `http_401` + 발화 ③ **403 응답 → 이벤트 미발화**(통합 후 조건 소실을 막는 못 — 지금도 초록이어야 하고, 통합이 이 계약을 지켰는지 재확인용) ④ 422 detail 파싱이 401 경로와 같은 코드 경로임은 구현 후 뮤테이션으로 확인. Login.test.tsx — mutationFn 이 `TypeError("Failed to fetch")` 로 reject → 일반 문구 렌더(영어 원문 부재 단언). 실행 → Login 신규 FAIL(③ 은 즉시 PASS 가 맞다 — 고정 가드).
- [ ] **Step 2(GREEN)**: 구현. `npx vitest run src/lib/api.test.ts src/features/auth/Login.test.tsx src/app/AuthContext.test.tsx && npx tsc -b` PASS(AuthContext = 이벤트 소비자 회귀 그물).
- [ ] **Step 3(뮤테이션)**: 사본 후 dispatch 를 401 조건 없이 `!res.ok` 전체에서 발화 → ③ RED(403 에서 로그인 화면로 튕기는 오작동의 실증). `cp` 원복.
- [ ] **Step 4: 커밋**
```bash
git commit -m "fix(portal): api.ts 401 분기 통합(파싱 한 벌·발화는 401 만) + Login instanceof ApiError 가드 — 영어 원문 노출 제거" -- frontend/src/lib/api.ts frontend/src/lib/api.test.ts frontend/src/features/auth/Login.tsx frontend/src/features/auth/Login.test.tsx
```

---

### Task 8: 잡 취소 오류 표시·ConfirmDialog reset·Home isError·무효화 dedup

**Files:** Modify: `frontend/src/features/jobs/RequestDetail.tsx`, `RequestDetail.test.tsx`, `ConfirmDialog.tsx`, `ConfirmDialog.test.tsx`, `frontend/src/app/router.tsx`, `router.test.tsx`, `frontend/src/features/jobs/useJobs.ts`

**Interfaces:**
- **잡 취소 오류**(`RequestDetail.tsx:187-192`): 취소 버튼 아래 `{cancel.isError && cancel.variables === j.job_id && <p className="text-bad text-sm mt-1">{(cancel.error as ApiError).message}</p>}` — `cancel.variables`(마지막 mutate 인자 = jobId)로 **해당 잡 카드에만** 렌더. 409 `cancel_failed` 문구는 기등록(`api.ts:96`).
- **ConfirmDialog reset**: `useEffect(() => { if (!open) confirm.reset(); }, [open])` — 같은 저장소의 `StoragesList.tsx:14-16` DeleteButton 선례와 동일한 이유 주석(Radix 는 "취소" 의 setOpen(false) 에 onOpenChange 를 안 태운다 → 낡은 지문 만료 오류가 재오픈에 남는다).
- **Home isError**(`router.tsx:29-34`): `if (me.isError) return <오류 화면>` — 문구("세션 확인에 실패했습니다 — 서버 오류이거나 네트워크 문제일 수 있습니다") + 「다시 시도」 버튼(`me.refetch()`). **리다이렉트 없음** — 로그인된 관리자가 일시 500 을 "세션 만료"로 오독하고 재로그인하는 그 시나리오(§2.4-5)를 끊는다. `Button` import 추가.
- **무효화 dedup**(`useJobs.ts:43-46,53-56,64-67`): 세 훅에서 `["request", id, "jobs"]` 무효화 줄 제거 + 주석 1줄("`["request", id]` 무효화가 접두 매칭으로 jobs 쿼리를 이미 포함한다 — tanstack 기본 partial matching"). 동작 불변이 계약이다.

- [ ] **Step 1(RED)**: RequestDetail.test.tsx — 잡 2개 요청에서 한 잡의 취소를 409 `cancel_failed` 로 실패시키고 ① 그 잡 카드에 문구 visible ② **다른 잡 카드에는 부재**. ConfirmDialog.test.tsx — 확인 409 후 닫기→재열기 → 오류 문구 부재. router.test.tsx — `/api/auth/me` 500 에서 `/` 진입 → 오류 문구 렌더 + `/login`·`/jobs` 리다이렉트 없음(문구 단언 + pathname 유지). 실행 → 신규 FAIL.
- [ ] **Step 2(GREEN)**: 구현 4건. `npx vitest run && npx tsc -b` 전부 PASS — **무효화 dedup 은 신규 테스트 없이 기존 스위트 초록 유지가 계약이다**(confirm/cancel 후 목록 갱신을 단언하는 기존 테스트들이 접두 매칭의 실증 그물).
- [ ] **Step 3(뮤테이션)**: 사본 후 ① `cancel.variables === j.job_id` 한정 제거 → "다른 카드 부재" 테스트 RED(모든 잡에 오류가 도배되는 회귀). ② `["request", id]` 무효화까지 제거(dedup 과잉) → confirm/cancel 후 갱신을 단언하는 기존 테스트 RED(접두 매칭이 실제 하중을 받고 있음의 실증 — "한 줄만 고치는 함정"의 역방향 증명). 각각 `cp` 원복.
- [ ] **Step 4: 커밋**
```bash
git commit -m "fix(portal): 잡 취소 실패 문구(해당 카드 한정)·ConfirmDialog 닫힘 reset·Home me.isError 오류+재시도(재로그인 오독 차단)·무효화 접두 중복 제거" -- frontend/src/features/jobs/RequestDetail.tsx frontend/src/features/jobs/RequestDetail.test.tsx frontend/src/features/jobs/ConfirmDialog.tsx frontend/src/features/jobs/ConfirmDialog.test.tsx frontend/src/app/router.tsx frontend/src/app/router.test.tsx frontend/src/features/jobs/useJobs.ts
```

---

### Task 9: Sparkline — 유효점 1개는 점으로

**Files:** Modify: `frontend/src/components/ui/Sparkline.tsx`, `frontend/src/components/ui/Sparkline.test.tsx`

**Interfaces:**
- `sparklinePath` 는 **무변경**(기존 테스트 `:7-17` 전부 그대로 — 결측 pen 끊기 계약 유지). 컴포넌트에서 분기: 유효점(`v !== null && Number.isFinite(v)`) 이 **정확히 1개**면 그 좌표에 `<circle cx={r2(i * step)} cy={height / 2} r={1.5} fill="currentColor" />` 를 렌더 — x 는 path 와 같은 step 공식(`values.length > 1 ? width/(values.length-1) : 0`), y 는 span 0 → norm 0.5 → 중앙선(path 의 `:23` 과 같은 규칙). "—" 로 접지 않는 이유를 주석에: **첫 리포트 1점은 실측값이지 결측이 아니다** — 0 과 null 을 뭉개지 않는 원칙의 SVG 판.
- 유효점 0개 → 기존 "—" 유지, ≥2개 → 기존 path 만(circle 없음).

- [ ] **Step 1(RED)**: Sparkline.test.tsx — ① `[7]` → circle cx 0·cy 16(height 32 기준) ② `[null, 7, null]` → cx 60(인덱스 1 × step 60) ③ `[null, null]` → "—" 유지 ④ `[1, 2]` → path 만·circle 부재. RTL `container.querySelector("circle")` 로 단언. 실행 → ①② FAIL(빈 SVG), ③④ 즉시 PASS(고정 가드).
- [ ] **Step 2(GREEN)**: 구현. `npx vitest run src/components/ui/Sparkline.test.tsx src/features/dashboard/NodeMetricsSection.test.tsx && npx tsc -b` PASS.
- [ ] **Step 3(뮤테이션)**: 사본 후 circle 분기 제거 → ①② RED. `cp` 원복.
- [ ] **Step 4: 커밋**
```bash
git commit -m "fix(portal): Sparkline 유효점 1개는 circle — 첫 리포트 실측값을 결측('—')으로 뭉개지 않는다, sparklinePath 무변경" -- frontend/src/components/ui/Sparkline.tsx frontend/src/components/ui/Sparkline.test.tsx
```

---

### Task 10: 마감 검증 — 전체 스위트 + 불변 조항 (커밋 없음)

- [ ] **Step 1: 백엔드 전체**(포그라운드, timeout 900000ms): Expected **1233 + 신규(≈25±) passed, failed 0**(수가 어긋나면 재계산하되 failed 0 이 본질). `tests/test_migrations.py` 무접촉 초록 = 스키마 무변경 보증.
- [ ] **Step 2: 프론트 + 타입 + e2e**: `npx vitest run && npx tsc -b && npm run test:e2e`. Expected: `232 + 신규 passed`(49+0 files — 신규 테스트는 전부 기존 파일), tsc exit 0, **e2e 9 passed**(시나리오 수 불변 — 줄었으면 공허 초록 조사).
- [ ] **Step 3: 불변 조항**: `git status --porcelain`(clean) + `git log --oneline -12`(커밋 9건: T1~T9) +
  `git diff 9598d4b --stat -- deploy/k8s frontend/src/lib/reasonCodes.json legacy docs` — `deploy/k8s` 는 **20-config.yaml 1파일**(태그 무변경), reasonCodes.json 은 **1~2줄 diff**(항목 추가만 — 재포맷이면 되돌려 다시), `legacy/` 0, `docs/` 는 이 플랜 파일뿐.
  `git diff 9598d4b --stat -- src/dms/agent src/dms_job_runner src/dms/migrations.py` — **전부 0**(러너·에이전트·스키마 무접촉의 실측).

---

## 플랜 이후: 배포·실증 (설계 §6 — 별도 ops, 플랜 태스크 밖)

**범프 판단**: 이 슬라이스는 프론트 + API 라우트 + 설정 키 1개다. 러너(`src/dms_job_runner/`)·에이전트(`src/dms/agent/`)·스키마 무접촉 → **`dms` 이미지만 d36→d37**, `DMS_JOB_IMAGE`(20-config.yaml:22)·`dms-agent`(50-agent-daemonset.yaml:72)는 d35 유지. **migrate Job 재실행 불요**(스키마 무변경 — Task 10 Step 3 이 실측. initContainer 의 migrate 가 어차피 no-op 으로 돌아 이중 안전). **매니페스트-우선**: 태그 범프 커밋(30-migrate-job.yaml:25 / 40-api.yaml:67,84 / 41-controller.yaml:35,52) → main 병합·push → **그 커밋에서** 클러스터 내 빌드(슬라이스 24·25 실적의 `build_build_pod` 방식 — pkg-01 SSH 불가). DB·파일 조작은 **API 파드 python / cephfs 마운트 파드**(슬라이스 24·25 실적, 노드 ssh 불가). 되돌릴 수 있는 조작만, 원복까지.

**0. 태그 범프 + 빌드 + apply**

```bash
git commit -m "deploy(k8s): 제어면 dms d37 (슬라이스 26 포탈 기능 잔여 — 아티팩트 다운로드·FAST-FOLLOW)" -- deploy/k8s
# main 병합·push 후 그 커밋에서, 슬라이스 25 실증 §0-(b) 의 build_build_pod 스니펫을
# images=["dms"], DMS_BUILD_TAG=d37 로 재사용(빌드 노드 인터넷 개방 전제 동일).
# 로그에서 DMS_COMMIT_SHA==범프 커밋 확인 후:
kubectl apply -f deploy/k8s/20-config.yaml -f deploy/k8s/40-api.yaml -f deploy/k8s/41-controller.yaml
kubectl -n dms rollout status deploy/dms-api deploy/dms-controller
# 새 키 반영 확인(운영 단일 진실):
kubectl -n dms exec deploy/dms-api -c api -- printenv DMS_ARTIFACT_DOWNLOAD_MAX_BYTES   # 268435456
```

이하 `PORTAL=<포탈 base URL — 슬라이스 24·25 실증과 동일 창구>`, 세션은 `curl -c /tmp/s26.cookies -X POST $PORTAL/api/auth/login -H 'Content-Type: application/json' -d '{"username":"<u>","password":"<p>"}'` 로 만든 쿠키 항아리를 `-b /tmp/s26.cookies` 로 재사용.

**1. (§6-1) 실 scan 리포트 다운로드 — sha256 일치 + 뷰 공존**

```bash
# 실 scan 잡(jid) 하나 확보 후:
curl -b /tmp/s26.cookies -D /tmp/h1 -o /tmp/dl.json "$PORTAL/api/user/jobs/<jid>/artifacts/execution/dscan-report.json/download"
grep -i -e content-length -e content-disposition -e x-content-type-options -e content-type /tmp/h1
# 기대: application/octet-stream / attachment; filename="dscan-report.json" / nosniff, CL == wc -c
wc -c /tmp/dl.json && sha256sum /tmp/dl.json
# cephfs 마운트 파드에서 원본: sha256sum /cephfs/dms/artifacts/<jid>/execution/dscan-report.json → 일치
# 뷰 공존: 같은 파일 GET(무 /download) → JSON content/truncated 그대로(256KB 꼬리 경로 불변)
```

**2. (§6-2) 위협 모델 재현(핵심) — 요청자 소유 phase 디렉터리에 직접 심는다**

```bash
# cephfs 마운트 파드 안에서(자기 잡 jid 의 execution 디렉터리):
cd /cephfs/dms/artifacts/<jid>/execution
ln -s /etc/passwd evil-link.txt ; mkfifo evil-fifo.log ; truncate -s 10G evil-sparse.bin
# ① 심링크 → 404 (본문이 뷰 404 와 동일한 {"detail":"artifact_not_found"})
curl -b /tmp/s26.cookies -sw '%{http_code}\n' "$PORTAL/api/user/jobs/<jid>/artifacts/execution/evil-link.txt/download"
# ② FIFO → 404 **즉답**(블록 없음 — time 으로 실측, 수 초 내):
time curl -b /tmp/s26.cookies -sw '%{http_code}\n' --max-time 10 ".../evil-fifo.log/download"
# ③ sparse 10G → 413 {"detail":"artifact_too_large"} (헤더 전 판정 — 절단이 아니라 명시 거부)
curl -b /tmp/s26.cookies -sw '%{http_code}\n' ".../evil-sparse.bin/download"
# ④ 목록에는 ①② 가 아예 안 뜬다(③ 은 정규 파일이라 뜬다 — 크기가 보인다):
curl -b /tmp/s26.cookies -s "$PORTAL/api/user/jobs/<jid>/artifacts" | python3 -m json.tool
# 원복: rm evil-link.txt evil-fifo.log evil-sparse.bin
```

**3. (§6-3) 256KB 초과 파일 — 뷰는 꼬리, 다운로드는 전체**

```bash
# 마운트 파드에서 1MiB 파일 생성(dd if=/dev/urandom … bs=1M count=1) → 포탈 화면에서
# 「뒷부분만 표시」 배지 + 「전체는 다운로드로 받으세요」 확인 → 다운로드 sha256 == 원본.
# §1-1 의 scan_report_too_large(503) 상황에서 운영자가 리포트를 손에 넣는 경로가 생겼다.
```

**4. (§6-4) chmod/chown 실측 — 캡션의 함정이 실제임을 기록**

```bash
# (a) chmod=D770,F660 실 sync 제출(고급 옵션 폼) → 성공 후 마운트 파드에서
#     find <목적지> -maxdepth 1 -exec stat -c '%a %n' {} \; → 디렉터리 770·파일 660.
# (b) 비특권 요청자로 chown 에 타인(root:root) 지정 → 데이터는 복사되고 잡은
#     Failed(execution_failed) — 폼 캡션이 경고한 반쪽 실패의 재현. 기록 후 목적지 정리.
```

**5. (§6-5) Degraded 황색 + Sparkline 1점**

```bash
# (a) Degraded: 노드 ssh 가 불가하므로 "일부 노드 마운트 사망" 대신 등가 유발 —
#     마운트 파드로 /cephfs/dms/slice26-notmount 디렉터리(마운트포인트 아님)를 만들고
#     그 경로로 스토리지 임시 등록 → 다음 에이전트 보고+리컨사일에서 status=Degraded
#     (no_ready_mounts) → 스토리지 화면 배지가 **황색(busy)** 인지, 기존 Ready 가
#     처음으로 **초록(ok)** 인지 실측. 원복: 임시 스토리지 삭제 + 디렉터리 제거.
#     (reconciler 의 Degraded 는 ready<total 과 ready=0 둘 다다 — 배지 실증으로 충분. 정직 기록)
# (b) Sparkline 1점: 결정적 유발은 "새 노드의 첫 리포트"뿐이다. 노드 추가가 없으면
#     API 파드 python 으로 특정 노드의 agent_reports 를 최신 1건만 남기고 삭제 →
#     대시보드 노드 메트릭에 점(circle)이 보이는지 30s 창(다음 보고 전) 안에 실측.
#     삭제된 행은 복원 불가한 순수 텔레메트리(보존 30d 대상)다 — 수용 여부는 열린 질문 4,
#     거부되면 "노드 추가 시 기회 실증"으로 남기고 vitest 계약(Task 9)으로 갈음한다.
```

**6. (§6-6) `/api/auth/me` 차단 → 재로그인 오독 차단 확인**

```bash
# 브라우저 devtools Network 조건에서 /api/auth/me 를 차단(또는 오프라인) → $PORTAL/ 진입
# → 로그인 화면이 아니라 "세션 확인 실패 + 다시 시도" 가 뜨는지. 차단 해제 → 재시도
# 버튼으로 복귀(재로그인 없이). §2.4-5 의 오독 시나리오가 닫혔다.
```

실증 6건 통과 후 `docs/superpowers/BACKLOG.md`(§2.2 의 다운로드·FAST-FOLLOW 6건·flex td 🔴·Sparkline 항목 종결 + 로그아웃 URL 🔴 은 잔존 명시)를 별도 커밋으로 갱신한다(플랜 밖, 관례).

---

## Self-Review

**1. 스펙 커버리지**

| 설계 절 | 담당 |
|---|---|
| §1 전제 10항 | 「설계 §1 전제 재확인」 — 전항 재실측. 정정: starlette 1.3.1(결론 유지+미 close 신규 실측), routes_artifacts 슬라이스 25 개편, 라인 드리프트(404 통일 :42-46·쿠키 :55·401 :201-217·auto-chown :75-77), Sparkline 경로, 대시보드 무배지, 기준선 1233/232/9 |
| §2.1 범위 4건 | T2~4(다운로드)·T6~8(FAST-FOLLOW)·T5(고급 옵션)·T9(Sparkline). **+판단 1건**: 슬라이스 23 의 flex td 를 T6 에 편입(근거 3개 명시), 로그아웃 URL 은 범위 밖 유지 |
| §2.2 같은 fd 검사·스트림, 64KiB·try/finally, 오라클, 헤더 3종 | T2(원천+뮤테이션 3종)·T3(라우트+오라클 body 동일성) |
| §2.3 뷰 256KB 유지·별도 상한 413·봉쇄 뒤 판정 | T1(키)·T2(순서 계약 테스트)·T3(413·경계 테스트). rate limit 부재는 설계 명시 한계 그대로 §7 |
| §2.4 FAST-FOLLOW 6건 | ① T6(storagePillVariant) ② T7(401 통합) ③ T7(Login) ④ T8(취소 오류+reset) ⑤ T8(Home isError) ⑥ T8(dedup). 해소된 로딩 상태는 코드 무변경 — BACKLOG 정정만(실증 후) |
| §2.5 고급 옵션 폼·빈 값 생략·미러 검증·함정 캡션 | T5 |
| §2.6 Sparkline 1점 circle·sparklinePath 무변경 | T9 |
| §2.7 사유 코드 1종 양쪽 | T3(같은 커밋, 계약 RED 순서 실측 포함) |
| §3 화면 | T4(링크·truncated 안내)·T5(접힘·캡션)·T6(배지 — 대시보드 정정)·T8(취소 문구·Home/Login) |
| §4 오류 처리(404 오라클·413 예외·조기 EOF 표면화·a href 트레이드오프·fd close) | T2·T3·T4 주석 — 각각 테스트/뮤테이션에 대응 |
| §5 테스트 목록 | 각 Task Step 1 이 1:1 이상. 기준선 정정 반영 |
| §6 실증 6항 | 「플랜 이후」 — d37 단독 범프·migrate 불요 근거·클러스터 내 빌드·마운트 파드·curl 실측(바이트·헤더 3종·413). §6-5 만 ssh 불가 제약으로 등가 유발로 대체(정직 기록+열린 질문 4) |
| §7 하지 않는 것 | 삭제·보존, 배치 CSV, rm 배치, Range/zip/rate limit, nosniff 전역화, 배치 고급 옵션 — 어떤 태스크도 만들지 않는다. 로그아웃 URL 결함도 명시적 보류 |

**2. 뮤테이션(이빨) 매트릭스** — T1 키 등록 삭제→override RED. T2 ⓐfinally 제거→fd RED ⓑ캡 제거→grow RED ⓒ**순서 역전→오라클 RED(지정)**. T3 404 detail 변조→동일성 RED·nosniff 삭제→헤더 RED. T4 href 변조→RED. T5 생략 로직 제거→RED. T6 Degraded→neutral→vitest RED·**flex 재주입→e2e RED(공허 초록 방지)**·수리 전 인자 제거→e2e RED(e2e-TDD). T7 무조건 발화→403 RED. T8 variables 한정 제거→RED·무효화 과잉 제거→기존 스위트 RED. T9 circle 제거→RED.

**3. 이름·값 일관성** — `DMS_ARTIFACT_DOWNLOAD_MAX_BYTES`/`artifact_download_max_bytes`/268435456(config·yaml·테스트), `artifact_too_large`(라우트 리터럴·json·REASON_MESSAGES·413 테스트), `open_artifact_stream`/`stream_artifact_fd`/`DOWNLOAD_CHUNK`(artifacts.py·routes·두 테스트 파일), `storagePillVariant`(jobState·StoragesList·테스트), URL `/artifacts/{phase}/{name}/download`(라우트·JobViewer href·실증 curl) — 동일 철자.

**알려진 위험 / 설계 대비 조정:**
- **flex td 편입은 설계 밖 범위 추가다** — 슬라이스 23 이 설계 확정 후 등록한 결함이고, e2e 의 정확 개수 단언이 "고치는 슬라이스가 인자를 지운다"는 상환 구조를 이미 깔아 놨다. 편입 비용은 두 줄이고, 안 하면 같은 파일을 두 번 여는 슬라이스가 하나 더 생긴다.
- **`read_artifact` 재구축은 동작 불변 리팩터링이다** — 기존 아티팩트 테스트 전체(paths·api·scan_path_stats)가 그물이고 Task 2 Step 2 가 그걸 명시 실행한다. 공유하지 않으면 봉쇄 사슬 두 벌이 드리프트한다(설계 "같은 코드 경로" 요구의 이행 방식).
- **413 과 404 의 갈림 자체는 자기 잡 디렉터리 안에서만 관측된다** — `_owned_job` 이 앞에 있고, 봉쇄 실패는 404 로 합류한다(순서 계약 테스트가 고정). 상한 이하 반복 다운로드의 대역폭 소진은 설계 §2.3 명시 한계 그대로 미해결(rate limit §7).
- **Content-Length 와 실제 전송의 불일치는 truncate 시에만 발생한다** — 헤더는 이미 나가 정정 불가, 클라이언트에겐 실패한 다운로드(설계가 선택한 정직한 실패). 0 채움 금지를 뮤테이션이 아닌 truncate 테스트가 직접 고정한다.
- **`cancel.variables` 는 마지막 mutate 의 인자다** — 잡 A 실패 직후 잡 B 취소를 누르면 A 의 오류 표시는 사라진다(variables 가 B 로 바뀜). 오류가 "가장 최근 시도"에 붙는 동작이라 수용 — 카드별 독립 오류를 원하면 mutation 을 카드 컴포넌트로 내려야 하는데 이 슬라이스 감이 아니다.
- **고급 옵션 미러 정규식은 서버의 사본이다** — 어긋나면 서버 422 가 최종 심판(설계 §2.5)이고, 폼 하단의 기존 `submit.isError` 경로가 그 문구를 이미 렌더한다. 미러는 즉답 UX 용일 뿐이다.
- **전체 수치 기대(백엔드 +≈25, 프론트 +≈30)는 근사 명시** — 어긋나면 재계산하되 failed 0 이 판정 기준.

## 결정이 필요한 열린 질문

1. **`artifact_too_large` 문구** — 제안 "파일이 다운로드 상한을 넘습니다 — 관리자에게 문의하세요". 상한값(256MiB)을 문구에 박지 않았다(설정 가변이라 거짓말이 될 수 있다). 값 병기를 원하면 별도 판단.
2. **기본 상한 256MiB 의 적정성** — dscan-report 실측 최대치 대비 여유가 얼마인지 운영 데이터가 없다. 키가 이미 config 에 있으므로 조정은 재배포 없이 config+재시작으로 가능 — 실증 1·3 에서 실 리포트 크기를 기록해 두는 것을 권한다.
3. **로그아웃 URL 결함(BACKLOG 🔴)** — 이 슬라이스가 안 고친다(§1 재확인의 판단). `qc.clear()` → 명시 nav 또는 `resetQueries` 의 결정이 필요한 별건 — 포탈 위생 슬라이스 후보로 유지하는 데 동의하는지.
4. **실증 5-(b) 의 agent_reports 행 삭제** — 텔레메트리(보존 30d)라 실해는 없으나 복원 불가 조작이다. 거부되면 Sparkline 1점 실증은 "새 노드 추가 시 기회 실증"으로 남기고 단위 테스트 계약으로 갈음한다.
5. **다운로드 `<a href>` 의 오류 UX** — 404/413 때 브라우저가 JSON 본문으로 이동할 수 있는 트레이드오프를 설계 §4 대로 수용했다. 실증 중 실제 불편이 관측되면(목록이 죽은 항목을 안 보여줘 창이 좁다는 전제가 깨지면) fetch 기반 사전 HEAD 검사 같은 보강은 후속 판단.
