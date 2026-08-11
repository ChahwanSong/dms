# 슬라이스 23 — 포탈 e2e 테스트 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 단위 테스트 228건이 구조적으로 못 보는 것 — ① 기하(계정 표 뭉개짐 9fbef86 ·사이드바 밀림 6bc2ecb 의 사각지대) ② 실 HTTP 왕복(세션 쿠키·SPA fallback) ③ 폴링 수렴 ④ 풀스택 부팅 — 만 잡는 실 브라우저 e2e 최소 세트(시나리오 6개 상한, 설계 §5 E1~E6)를 만든다. 실행 환경은 로컬 풀스택(tmp sqlite + migrate + api dist 서빙 + controller 1s 틱 + agent --once, 설계 §2.2), 도구는 승인된 신규 devDependency 1건 `@playwright/test`(설계 §2.3), 브라우저는 시스템 크롬 `channel:"chrome"`(다운로드 0). **앱 코드(백엔드·프론트) 변경 0** — data-testid 도 달지 않는다(설계 §3). 신설 사유 코드 0, 새 DB 테이블 0, 클러스터 무관(실증 §6-5 의 수기 콘솔 스니펫만 라이브 포탈을 **읽기 전용**으로 본다).

**Architecture:** 기반(설정) → 하네스 → 시나리오 순으로 쌓는다. (1) vitest include 명시 + `tsconfig.e2e.json` + `playwright.config.ts` + npm 스크립트 — e2e 스펙을 vitest 가 집어삼키는 함정(§1-4)을 **잠그고 그 잠금이 필요함을 RED 로 실측**한다. (2) `e2e/harness/` globalSetup 이 tmpdir sqlite → migrate → api(:8093, dist) → readyz 폴링 → controller(장수 프로세스, 1s 틱) → 시드(admin 부트스트랩·스토리지·긴 이메일 계정 3건) → `agent --once`(가짜 도구 4종 PATH) → 스토리지 Ready 폴링까지 부팅 스모크를 겸한다. **슬라이스 24 정정**: 시드 스토리지는 `"/"` 가 아니라(이제 422) tmpdir 실디렉터리 + `DMS_AGENT_MOUNTINFO_PATH` 가짜 mountinfo 로 마운트포인트를 위장한다. (3) 시나리오 5파일(E1/E2/E3/E4/E5+E6): 단언 재료는 URL·역할·기하·행 수·상태 값으로 제한(설계 §2.1 — 문구·사유 코드는 vitest 영토). e2e 의 TDD는 두 갈래다: **하네스·배선은 "아직 없는 상태"의 실측 RED**(ECONNREFUSED, vitest 가 spec 을 집는 오류), **불변식·단언의 이빨은 결함 재주입과 앱 뮤테이션의 실측 RED**(9fbef86/6bc2ecb 되돌리기, refetchInterval 무력화) — 후자 없이는 e2e 는 공허한 초록을 증명할 수단이 없다.

**Tech Stack:** `@playwright/test`(^1.62 — 유일한 신규 의존성, 사용자 승인), 시스템 Google Chrome 147(`/usr/bin/google-chrome` 실측), node v25.9.0, 백엔드는 본 저장소 venv(`/home/mason/dms-dev/dms/.venv`) + **PYTHONPATH 로 워크트리 src 강제**(아래 함정 참조). DB 는 tmp sqlite. CI 없음 — `npm run test:e2e` 수기 게이트가 사실이고(설계 §2.2), deploy/README 에 "이미지 빌드 전" 단계로 명문화한다.

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-08-11-dms-portal-e2e-slice23-design.md`. 플랜과 충돌하면 **설계가 이긴다**(단, 아래 「설계 §1 전제 재확인」의 정정은 실측으로 갱신한 사실이다 — 특히 §1-9 의 `"/"` 시드는 슬라이스 24 이후 **불가능**하다).
- **새 의존성은 `@playwright/test` 1건뿐**(사용자 승인). 그 외 새 npm/pip 의존성 **금지** — e2e 타입체크가 쓰는 `@types/node` 는 이미 전이 의존성으로 `frontend/node_modules/@types/node` 에 존재한다(실측). **직접 devDependency 로 추가하지 않는다**(금지 원칙 준수 — 위험은 「열린 질문」에 기록). 브라우저 다운로드도 0: `channel:"chrome"`. 크롬 부재 머신에서만 `npx playwright install chromium` 폴백(Task 7 의 README 문구).
- **새 DB 테이블·컬럼 금지** — 이 슬라이스는 스키마는커녕 백엔드 소스 자체를 1줄도 바꾸지 않는다(`tests/test_migrations.py` 의 `len(ALL_TABLES) == 20` 무접촉 초록이 증거).
- **신설 사유 코드 0 이어야 한다** — e2e 는 사유 문구를 단언하지 않으므로(설계 §2.1) `frontend/src/lib/reasonCodes.json`·`api.ts` REASON_MESSAGES 는 무변경이다. Task 8 이 `git diff` 로 실측 확인한다.
- **앱 코드 변경 0**(백엔드 `src/`·프론트 `frontend/src/`) — RED 실측용 결함 재주입·뮤테이션은 전부 **일시 변경 후 `git checkout -- <파일>` 원복**이고, 커밋에 절대 실리지 않는다.
- **null(모름)과 실패를 섞지 않는다. 0 은 정상값 — truthy 검사 금지.** E6 의 "요청 0건" 단언은 같은 필터가 **먼저 ≥1 을 세는 생존 증명**(phase A) 뒤에만 유효하다 — 필터가 아무것도 못 잡는 공허한 0 과 구분한다(설계 §4).
- **커밋은 pathspec 으로 한정**: 신규 파일만 `git add <파일>` 선행 후, 항상 `git commit -m "..." -- <경로들>`. `git add -A`·`git add .`·`git commit -a` **금지**(워크트리 공유 중 인덱스 섞임 사고).
- **origin push 금지, 브랜치 변경 금지**(현재 `worktree-dms-slice22plus`, main 과 동일 70561a8 에서 이어서). `docs/` 아래는 이 플랜 파일 외 생성·수정 금지(실증 후 BACKLOG 갱신은 플랜 밖 관례). **`deploy/README.md` 는 예외** — 설계 §2.2 가 게이트 명문화를 명시한다(Task 7). `deploy/k8s` 이미지 태그 무변경 — 이 슬라이스는 배포물이 없다.
- `legacy/` 아래는 읽기 전용 — 수정·이동·삭제·추가 금지, import 금지, 코드 복사 금지.
- **이 환경은 헤드리스다** — 브라우저 GUI 없음. Playwright 는 headless(기본값)로만 돌리고, `--headed`·`--ui` 를 검증 절차에 넣지 않는다.
- **클러스터 무관** — 테스트베드는 d35 로 건강하고 이 슬라이스는 클러스터를 만지지 않는다. 유일한 접점은 실증 §6-5 의 수기 콘솔 스니펫(라이브 포탈 **읽기 전용**, 배포자 몫).
- 백엔드 테스트 명령(이 워크트리 전용, `.venv` 는 워크트리 밖 공용):
  `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests -q`
  전체 스위트는 **포그라운드**, Bash timeout 900000ms. **기준선 1189 passed(2026-08-12 실측, 402s).**
- 프론트: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run`(**기준선 228 passed / 49 files**), 타입체크 `npx tsc -b`. node_modules 존재 실측.
- e2e(이 플랜이 정의한다): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npm run test:e2e` = `npm run build && tsc -p tsconfig.e2e.json && playwright test`. 단일 파일은 `npm run test:e2e -- e2e/03-layout.spec.ts`(npm 이 뒤 인자를 체인 마지막 명령에 붙인다).
- **PYTHONPATH 함정(신규 실측)**: 본 저장소 venv 의 `dms` 편집 설치는 `/home/mason/dms-dev/dms/src` 를 가리킨다 — PYTHONPATH 없이 `.venv/bin/dms` 를 실행하면 **본 저장소 코드**가 돈다(실측: `import dms` → 본 저장소 경로 / PYTHONPATH 지정 시 워크트리 경로). 하네스는 **모든 백엔드 자식 프로세스**에 `PYTHONPATH=<워크트리>/src` 를 강제한다. sys.path 에서 PYTHONPATH 가 site-packages(편집 설치)보다 앞서므로 이 오버라이드는 결정적이다.
- 주석은 **한국어**로 「왜」를 적는다.

## 설계 §1 전제 재확인 (2026-08-12, 코드 직접 실측)

설계는 2026-08-11 에 쓰였고 그 사이 슬라이스 22(4e13cda~beed7b2)·24(caea739~a41c9b9)가 들어갔다. 12개 항목 전부 재확인 — **정정 3건(그중 1건은 시드 전략을 바꾸는 파괴적 정정)**, 라인 드리프트 다수, 신규 발견 4건이다.

| 설계 §1 항목 | 재확인 결과 |
|---|---|
| 1. 두 결함의 실체와 수정 | ✓ 유지. 파일 경로만 정확히: `frontend/src/features/accounts/AccountsList.tsx`(td 안 div 만 flex, `:71-72`)·`frontend/src/app/AppShell.tsx`(`md:shrink-0` `:13`, `flex-1 min-w-0` `:35`) — `src/components/` 가 아니다. 표 래퍼 `overflow-x-auto` 는 `components/ui/Table.tsx:3` 그대로. 사이드바 폭 `md:w-60` = 15rem = 240px(L3 의 상수) |
| 2. 단위 테스트에 기하 없음 | ✓ 유지. `src/test/setup.ts:1-4` jest-dom+cleanup 뿐 |
| 3. e2e·CI 0 | ✓ 유지. package.json 에 playwright 계열 없음, `.github`/CI 없음, `.gitignore:13-14` 는 세션 도구 찌꺼기. `frontend/package-lock.json` 존재(설치는 lock 갱신 동반) |
| 4. vitest include 부재·tsc include:["src"] | ✓ 유지. `vite.config.ts:8-12` test 블록에 include 없음, `tsconfig.json:9`. 현 49파일 전부 `src/**/*.test.{ts,tsx}`(ts 5 + tsx 44, `*.spec.*` 0건 — find 실측) |
| 5. 풀스택 클러스터 불요 | ✓ 유지, 라인 드리프트: `execution_backend` 기본 "stub" `config.py:131,191`(설계 125,184), 필수 env 4종 `config.py:153-154`, `StubExecutionAdapter.poll` 미지 ref SUCCEEDED `execution.py:67-73`(동일), sqlite `db.py:71-77`(설계 38-44 — 슬라이스 22 재연결 코드로 밀림) |
| 6. dist 서빙+SPA fallback | ✓ 유지, 드리프트: `api/app.py:111-126`(설계 81-96 — 슬라이스 22 readyz 카운터·재연결 훅이 위에 쌓임). vite dev 서버가 이 코드를 가린다는 논지 그대로 |
| 7. 세션 쿠키·부트스트랩 | ✓ 유지, 드리프트: `dms_session` `app.py:54-55`(설계 44-45), 로그인 DB 검증 `routes_auth.py:33-40`(동일), 부트스트랩 POST /api/admin/accounts + x-admin-token `routes_auth.py:54-57`(동일) |
| 8. LDAP 없이 특권 종단 | ✓ 유지. resolver None → 일반 `ldap_not_configured`, session 인증 + requester ∈ {root, admin} 기본 특권(`config.py:125,183-185`), `identity.py` 특권 통과, `placement.py` 특권은 신원 검사 생략(`if not privileged and not _identity_ready`) |
| 9. 후보 노드·시드 재료 | **정정(파괴적)**: 골자(마운트포인트 필수 `agent/probes.py:36-38` not_a_mountpoint, which 발견→Ready `:52-69`, `agent --once` 2사이클 `agent/runner.py:104-107`, 이메일 무검증 INSERT `accounts.py:33-49`)는 유지. 그러나 **스토리지 `{mount:"/", root:"/"}` 특례는 슬라이스 24 가 제거했다** — `repositories/storages.py:15-27` 이 `"/"` 를 **명시 거부**(422 invalid_storage)하고 `and p != "/"` 예외 절도 삭제됐다. 시드는 §2.5-정정(아래) 방식으로 대체한다. **신규 제약 2건도 슬라이스 24 산물**: (a) `stepper._abs` 가 storage 결측에 fail-closed(`storage_missing_at_step` 종단) — e2e 시드가 storage 행을 반드시 만들어야 잡이 진행된다(시드가 만드니 충족되지만, 지우면 조용한 성공이 아니라 종단이다). (b) 스테퍼가 미지 tool 을 제출 전 종단(`unknown_tool`) — e2e 는 dscan 만 쓰므로 무관하나 전제로 기록한다 |
| 10. 컨트롤러 --once 함정 | ✓ 유지. `try_acquire_lease` 는 **동일 holder 면 만료 전에도 갱신**(`repositories/control.py:145-166` 실측) — 장수 프로세스 1개 + interval 1s 전략이 성립한다. holder `controller-<pid>` `cli.py:64`, 리스 max(interval×3,30) `controller.py:122-124` |
| 11. 프론트 폴링 | **정정(계측 지점)**: `useJobs.ts:8` 은 **요청 목록**(`/api/user/requests`) 3s **상시** 폴링 — 종단 중지가 없다. 종단 중지 콜백은 `useRequestJobs`(**상세 잡** `/api/user/requests/{id}/jobs`, **2s**, `useJobs.ts:17-20`)다 — 설계 §1-11 은 이 둘을 "잡 목록 3s + 종단 시 중지"로 뭉뚱그렸다. **E6 은 상세 잡 엔드포인트를 계측해야 한다**(E5 의 목록 수렴은 상시 3s 가 맞으므로 유지). 빌드 로그 3s 종단 중지 `useBuilds.ts:71`, 대시보드 5s `useMetrics.ts:19,26,33`, RequireRole 미인증→/login `app/RequireRole.tsx:6`, 미지 경로→/ `router.tsx:70`, 로그인 aria-label(사용자명/비밀번호) `Login.tsx:22,26` — 전부 유지. **신규 관찰**: `useRequestJobs` 는 jobs **빈 배열에도 폴링을 멈춘다**(`[].some(...)` = false → interval false) — 플래너 틱 전에 상세를 열면 잡이 리로드 없이 영영 안 뜬다. E6 이 이 함정을 밟지 않도록 **종단 후 진입**으로 설계를 구체화한다(아래 Task 6, 열린 질문 1) |
| 12. 실행기 재료 | ✓ 유지+갱신: `/usr/bin/google-chrome` = 147.0.7727.55(실측), node v25.9.0. npm 레지스트리 접근 가능(`npm view @playwright/test version` → 1.62.1 실측). `~/.cache/ms-playwright` 에 브라우저 번들 없음 → `channel:"chrome"` 가 필수 경로다 |

**추가 정정·신규 발견(설계 본문·수치):**
- §2.5 의 시드 스토리지 `{mount:"/", root:"/"}` 는 **실행 불가**(위 §1-9 정정). 대체: tmpdir 아래 실디렉터리를 만들고, `AgentSettings.mountinfo_path`(`DMS_AGENT_MOUNTINFO_PATH`, `config.py:209,236`)로 **가짜 mountinfo 파일**을 주입해 그 디렉터리를 마운트포인트로 위장한다. `parse_mountinfo` 는 각 줄의 5번째 공백 필드를 마운트포인트로 읽는다(`agent/probes.py:17-23` 실측) — 한 줄이면 충분하다. `"/"` 시드보다 오히려 결정적이다(머신 마운트 상태 무의존).
- §5 기준선 "백엔드 1131" → **1189 passed**(슬라이스 22·24 반영, 2026-08-12 실측 402s). 프론트 228/49 유지.
- **venv 편집 설치 함정**(Global Constraints 에 상술) — 설계에 없던 신규 발견. 하네스의 모든 백엔드 spawn 에 PYTHONPATH 필수.
- **resource_key 충돌**: 같은 requester 가 같은 storage+target+options 로 재제출하면 활성 요청과 Conflict 다 — E4/E5/E6 은 target 을 서로 다르게 쓴다(`e4-scan`/`e5-scan`/`e6-scan`). 설계에 없던 함정.
- **에이전트 리포트 신선도 300s**(`agent_report_stale_seconds` 기본): `agent --once` 는 setup 에서 1회다 — 스위트+디버깅이 300s 를 넘기면 플래너가 후보 0 으로 거부한다. 하네스가 API·컨트롤러에 `DMS_AGENT_REPORT_STALE_SECONDS=3600` 을 준다(읽는 쪽 설정이라 에이전트 재실행 불요).
- SubmitScan 은 제출 성공 시 **상세로 자동 이동**한다(`SubmitScan.tsx:48` `nav(\`/jobs/${r.request_id}\`)`) — E4 는 거기서 rid 를 URL 로 얻고, §3 의 "상세" 화면 순회도 이 지점에서 해결한다.
- 상태 표시는 `StatusPill` 이 **상태 문자열 그대로**("Succeeded")를 렌더한다(`StatusPill.tsx:15`) — E4/E5 의 상태 단언은 문구 번역이 아니라 상태 값이다(설계 §2.1 의 단언 재료 제한에 부합).

## 파일 구조

| 파일 | 책임 |
|---|---|
| `frontend/package.json`, `frontend/package-lock.json` (수정) | Task 1: `@playwright/test` devDep + `test:e2e` 스크립트 |
| `frontend/vite.config.ts` (수정) | Task 1: vitest `include: ["src/**/*.test.{ts,tsx}"]` 명시(§1-4 잠금) |
| `frontend/tsconfig.e2e.json` (신규) | Task 1: e2e/ + playwright.config.ts 타입체크(본 빌드와 분리) |
| `frontend/playwright.config.ts` (신규, Task 2 에서 harness 배선 추가) | Task 1: 러너 설정(workers:1, retries:0, channel:"chrome") |
| `.gitignore` (수정) | Task 1: `frontend/test-results/` |
| `frontend/e2e/harness/env.ts`, `global-setup.ts`, `global-teardown.ts` (신규) | Task 2: 풀스택 부팅·시드·정리(부팅 스모크 ④ 겸임) |
| `frontend/e2e/fixtures/bin/{dscan,dsync,nsync,drm}` (신규, +x) | Task 2: 배선 검증용 가짜 도구(which→Ready) |
| `frontend/e2e/helpers/session.ts` (신규) | Task 2: API 로그인 헬퍼(E1 외 파일용 — UI 로그인 재검증 금지) |
| `frontend/e2e/01-boot-session.spec.ts` (신규) | Task 2: E1 부팅+세션 |
| `frontend/e2e/02-spa-fallback.spec.ts` (신규) | Task 3: E2 딥링크·미지 경로 |
| `frontend/e2e/helpers/layout.ts` (신규) | Task 4: `assertLayoutSane`(L1~L4) + `assertTableOverflows`(전제 단언) |
| `frontend/e2e/03-layout.spec.ts` (신규) | Task 4: E3 화면 순회 × 2 뷰포트 |
| `frontend/e2e/04-job-flow.spec.ts` (신규) | Task 5: E4 잡 종단 흐름 |
| `frontend/e2e/05-polling.spec.ts` (신규) | Task 6: E5 폴링 수렴 + E6 폴링 중지 |
| `deploy/README.md` (수정) | Task 7: "이미지 빌드 전" e2e 게이트 명문화(설계 §2.2) |

---

### Task 0: 기준선 확인 (커밋 없음)

**Files:** 없음(검증만)

**Interfaces:** 이후 모든 태스크의 판정 기준(기준선 초록 + 실행기 재료 존재)을 만든다.

- [ ] **Step 1: 백엔드 기준선**

Run(포그라운드, Bash timeout 900000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests -q`
Expected: `1189 passed` (약 400s)

- [ ] **Step 2: 프론트 기준선 + 타입체크**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b`
Expected: `Tests  228 passed` / `Test Files  49 passed`, tsc 무출력 exit 0.

- [ ] **Step 3: 실행기 재료 실측**

Run: `/usr/bin/google-chrome --version && node --version && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -c "import dms; print(dms.__file__)"`
Expected: 크롬 버전(147.x 계열), v25.x, 그리고 **워크트리 경로**(`.../worktrees/dms-slice22plus/src/dms/__init__.py`)가 찍혀야 한다 — 본 저장소 경로가 나오면 PYTHONPATH 배선이 깨진 것이니 진행 금지.

---

### Task 1: 도구·설정 기반 — vitest 잠금 + tsconfig 분리 + Playwright 러너

**Files:**
- Modify: `frontend/package.json`, `frontend/package-lock.json`, `frontend/vite.config.ts`, `.gitignore`
- Create: `frontend/tsconfig.e2e.json`, `frontend/playwright.config.ts`

**Interfaces:**
- Produces: `npm run test:e2e` = `npm run build && tsc -p tsconfig.e2e.json && playwright test` — dist 최신화(설계 §2.5 명시 체인)와 e2e 타입체크가 러너보다 앞선다.
- **vitest 함정의 결정(설계 §1-4)**: e2e 파일명은 `*.spec.ts` 로 하되, **그것만으로는 안 되고**(vitest 기본 include 가 `**/*.{test,spec}.*`) `vite.config.ts` 에 `include: ["src/**/*.test.{ts,tsx}"]` 를 **명시**한다. 이 include 는 현 49파일 전부와 정확히 일치(ts 5 + tsx 44, `src/smoke.test.tsx` 포함 — `**` 는 0개 세그먼트도 매치)해 228 유지가 보장되고, e2e 는 디렉터리(`e2e/`)와 접미(`spec`) **양쪽**에서 벗어난다(이중 잠금). "include 를 좁히기만" 대신 "파일명 관례까지" 인 이유: 미래에 누가 e2e 파일을 `src/` 에 잘못 두더라도 `.spec.ts` 접미가 vitest include 밖이다.
- **tsc 함정의 결정(설계 §1-4)**: 본 `tsconfig.json` 은 `include:["src"]` 를 **유지**한다(본 빌드 `tsc -b` 가 e2e 타입 오류로 죽으면 안 되고, e2e 는 `@playwright/test`·node 타입이 필요해 타입 환경이 다르다). 별도 `tsconfig.e2e.json` 이 `e2e/` + `playwright.config.ts` 를 본다:

```json
{
  "extends": "./tsconfig.json",
  "compilerOptions": { "types": ["node"] },
  "include": ["e2e", "playwright.config.ts"]
}
```

  `types:["node"]` 로 본 설정의 `vitest/globals`·`jest-dom` 을 **끊는** 것이 요점 — e2e 파일에서 vitest 전역이 보이면 `@playwright/test` 의 `test/expect` 와 섞여 잘못된 코드가 타입 통과한다. `@types/node` 는 전이 의존으로 존재(실측, Global Constraints).
- `playwright.config.ts`(이 태스크에서는 globalSetup/Teardown **없이** — Task 2 가 배선):

```ts
import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  workers: 1, fullyParallel: false,   // 단일 sqlite 상태 공유 -- 병렬 금지(설계 §2.5)
  retries: 0,                         // 플레이크를 재시도로 숨기지 않는다
  timeout: 60_000,
  reporter: [["list"]],
  outputDir: "./test-results",
  use: {
    baseURL: "http://127.0.0.1:8093",
    channel: process.env.DMS_E2E_BROWSER_CHANNEL ?? "chrome",  // 시스템 크롬, 다운로드 0(§1-12)
    headless: true,                   // 이 개발 환경은 헤드리스다
    viewport: { width: 1280, height: 800 },
    trace: "retain-on-failure",
  },
});
```

- [ ] **Step 1: 의존성 설치(승인된 1건)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npm install --save-dev @playwright/test@^1.62.1 && npx playwright --version`
Expected: package.json/lock 갱신, `Version 1.62.x`. 레지스트리 불가(오프라인) 시: **중단하고 보고** — 이 의존성 없이 슬라이스는 진행 불가고, 다른 우회(수동 tarball 등)는 lock 무결성을 깨므로 하지 않는다.

- [ ] **Step 2: RED — vitest 가 e2e 스펙을 정말 집어삼키는지 실측(§1-4 함정의 증명)**

임시 파일 `frontend/e2e/00-placeholder.spec.ts` 생성:

```ts
import { test, expect } from "@playwright/test";
test("placeholder — 하네스 전 러너 배선 확인용(Task 1 에서 삭제)", () => {
  expect(1).toBe(1);
});
```

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run`
Expected: **RED** — vitest 가 `e2e/00-placeholder.spec.ts` 를 50번째 파일로 집어 실패한다(Playwright의 test() 가 vitest 러너 안에서 호출돼 오류 — 정확한 메시지는 기록). 이것이 include 명시가 필요한 **실측 근거**다.

- [ ] **Step 3: vitest include 명시 + 러너 설정**

`frontend/vite.config.ts` 의 test 블록에 추가:

```ts
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // e2e(Playwright, frontend/e2e/*.spec.ts)를 vitest가 집어삼키지 않도록 명시한다
    // -- vitest 기본 include 는 **/*.{test,spec}.* 라 spec 파일이 어디 있든 위험하다.
    // 현 49개 테스트 파일은 전부 이 글롭과 일치한다(ts 5 + tsx 44, 실측).
    include: ["src/**/*.test.{ts,tsx}"],
  },
```

`frontend/tsconfig.e2e.json`·`frontend/playwright.config.ts` 를 위 Interfaces 대로 생성. `frontend/package.json` scripts 에 추가:

```json
    "test:e2e": "npm run build && tsc -p tsconfig.e2e.json && playwright test"
```

`.gitignore` 의 "local tooling" 절에 `frontend/test-results/` 추가(트레이스 산출물 — 설계 §2.5).

- [ ] **Step 4: GREEN — 기존 49파일 무손상 + 러너 배선 확인**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b && npx tsc -p tsconfig.e2e.json && npx playwright test`
Expected: vitest `228 passed / 49 files`(placeholder 가 있는데도 — include 잠금의 직접 증명), tsc 둘 다 exit 0, playwright `1 passed`(placeholder).

- [ ] **Step 5: 뮤테이션으로 이빨 확인**

(a) placeholder 에 `const x: number = "s";` 한 줄 추가 → `npx tsc -b` 는 **초록**(본 빌드는 e2e 를 안 본다 — 분리의 증명), `npx tsc -p tsconfig.e2e.json` 은 **빨강**. 줄 제거.
(b) vite.config.ts 의 `include` 줄을 잠시 삭제 → `npx vitest run` 이 Step 2 의 RED 로 회귀(잠금이 실제로 하중을 받는다는 증명). 원복.
(c) placeholder 삭제 후 `npx playwright test` → **"no tests found" 로 비0 종료**(실측 기록) — "e2e 를 지웠는데 초록" 방지(설계 §4)가 러너 기본 동작으로 성립함을 확인.

- [ ] **Step 6: 커밋** (placeholder 는 이미 삭제됨 — 커밋에 넣지 않는다)

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git add frontend/tsconfig.e2e.json frontend/playwright.config.ts
git commit -m "chore(e2e): Playwright 러너 기반 — @playwright/test devDep, vitest include 잠금, e2e 전용 tsconfig" -- frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/tsconfig.e2e.json frontend/playwright.config.ts .gitignore
```

---

### Task 2: 하네스(풀스택 부팅·시드) + E1 부팅+세션

**Files:**
- Create: `frontend/e2e/harness/env.ts`, `frontend/e2e/harness/global-setup.ts`, `frontend/e2e/harness/global-teardown.ts`, `frontend/e2e/fixtures/bin/{dscan,dsync,nsync,drm}`, `frontend/e2e/helpers/session.ts`, `frontend/e2e/01-boot-session.spec.ts`
- Modify: `frontend/playwright.config.ts`(globalSetup/globalTeardown 두 줄 추가)

**Interfaces:**
- `harness/env.ts` — 공용 상수·탐색 로직:
  - `PORT = 8093`, `BASE_URL = "http://127.0.0.1:8093"`, 토큰 3종(`e2e-shared-token`/`e2e-admin-token`/`e2e-session-secret` — placeholder 검사(`CHANGE_ME`/`REPLACE_WITH_`)에 안 걸리는 값), `ADMIN = { username: "admin", password: "e2e-admin-pw" }` — **username 은 반드시 "admin"**: 기본 `privileged_requesters = {root, admin}` 에 들어 E4 의 특권 경로(§1-8)가 성립한다.
  - `repoRoot` = `frontend/` 의 부모(이 파일 기준 `../../..` 해석). `venvBin` 탐색: `<repoRoot>/.venv/bin/dms` → 없으면 `<repoRoot>/../../../.venv/bin/dms`(워크트리는 `.claude/worktrees/<이름>` 아래라 3단계 위가 본 저장소) → 둘 다 없으면 **throw**("dms CLI 부재", 설계 §4 — skip 금지). `PYTHONPATH = <repoRoot>/src` 를 **모든 spawn env 에** 넣는다(Global Constraints 의 편집 설치 함정).
- `harness/global-setup.ts` — 순서와 실패 규약(전 단계 실패 = throw = 러너 비0, **어떤 경우에도 skip 없음**):
  1. **포트 선점 검사**: :8093 에 TCP 연결이 되면 throw("이전 하네스 잔재") — 낡은 서버에 붙어 낡은 dist/DB 로 초록이 나는 오염을 부팅 시점에 차단한다.
  2. tmpdir(`fs.mkdtempSync(os.tmpdir()+"/dms-e2e-")`) 아래 `dms.db`·`storage/`(mkdir)·`mountinfo` 준비. mountinfo 내용은 한 줄: `36 25 0:32 / <tmp>/storage rw,relatime shared:1 - tmpfs tmpfs rw` — `parse_mountinfo` 가 5번째 필드를 마운트포인트로 읽는다(§1-9 정정. tmp 경로에 공백 없음 전제 — mkdtemp 가 보장).
  3. `dms migrate` (spawnSync, env: `DMS_DATABASE_URL=sqlite:///<dbPath>` — dbPath 가 절대경로라 결과 URL 은 슬래시 4개다, 토큰 3종, PYTHONPATH). 비0 종료 → throw(stderr 포함).
  4. `dms api` spawn(장수). env 추가: `DMS_API_HOST=127.0.0.1`(로컬 전용), `DMS_API_PORT=8093`, `DMS_STATIC_DIR=<repoRoot>/frontend/dist`(**빌드된 dist 서빙** — vite dev 서버가 spa_fallback 을 가리는 문제의 봉인, 설계 §1-6/§2.2), `DMS_AGENT_REPORT_STALE_SECONDS=3600`(신선도 함정). stdout/stderr 는 tmpdir 로 리다이렉트(실패 시 진단용).
  5. `/readyz` 200 폴링(간격 500ms, **상한 30s** — 초과 시 자식 로그 tail 을 담아 throw).
  6. `dms controller` spawn(장수 — `--once` 반복은 §1-10 의 리스 함정이라 금지). env 추가: `DMS_PLANNER_INTERVAL_SECONDS=1`, `DMS_STEPPER_INTERVAL_SECONDS=1`, `DMS_RECONCILE_INTERVAL_SECONDS=1`, `DMS_BATCH_ORCHESTRATOR_INTERVAL_SECONDS=1`, `DMS_AGENT_REPORT_STALE_SECONDS=3600`. (pod-gc/build-watcher/rollout/retention 은 기본값 유지 — 잡 전진 지연과 무관하고 1s 로 돌리면 틱마다 무의미한 쿼리만 는다. 설계 §2.5 의 "interval env 전부 1s" 의 목적은 잡 전진이다 — 목적으로 좁혀 적용.)
  7. 시드(node 내장 fetch — 브라우저 불요): (a) `POST /api/admin/accounts` + `x-admin-token` 으로 admin 계정 부트스트랩(§1-7). (b) `Authorization: Bearer <shared>`(Bearer = admin, `api/auth.py:46-48`)로 `POST /api/admin/storages` `{storage_name:"e2e-store", mount_path:"<tmp>/storage", managed_root:"<tmp>/storage", backend_type:"cephfs"}` — root==mount 는 검증 통과. (c) `POST /api/auth/signup` ×3: `e2ewide1..3`, 이메일 ≈120자(`long-…x96…-N@e2e.example.com` — 무검증 §1-9, 공백 없는 단일 토큰이라 줄바꿈 불가 → 표를 강제로 넓힌다, 설계 §2.4 시드). 각 응답 상태를 단언(201) — 시드 실패는 조용히 지나가면 E3 전제 단언에서야 터진다.
  8. `dms agent --once` spawnSync — env: `DMS_AGENT_API_URL=http://127.0.0.1:8093`, `DMS_SHARED_TOKEN`, `DMS_AGENT_NODE_NAME=e2e-node`, `DMS_AGENT_MOUNTINFO_PATH=<tmp>/mountinfo`, `PATH=<repoRoot>/frontend/e2e/fixtures/bin:` + 기존 PATH(which 가 가짜 4종을 발견→Ready, §1-9), PYTHONPATH. **비0 종료 → throw**(설계 §4). `--once` 는 2사이클(부트스트랩→실프로브)이라 1회로 충분 — storage 시드(7b)가 **먼저**여야 프로브 대상에 실린다(순서 불변).
  9. **부팅 스모크 마감 폴링**: `GET /api/user/storages`(Bearer) 에서 `e2e-store` 의 `status == "Ready"` 를 10s 상한 폴링 — 에이전트 리포트가 실렸고 **컨트롤러(reconciler 1s 틱)가 살아 있다**는 것까지 한 번에 증명된다. 실패 시 throw.
  10. 자식 PID·tmpdir 를 `process.env.DMS_E2E_API_PID / DMS_E2E_CTRL_PID / DMS_E2E_TMPDIR` 에 기록 — Playwright 의 globalSetup/Teardown 은 러너 메인 프로세스에서 돌아 env 가 전달된다.
- `harness/global-teardown.ts` — 설계 §4 규약: **종료 전 생존 확인**(`process.kill(pid, 0)`) — API·컨트롤러 중 하나라도 이미 죽어 있으면 SIGTERM·정리는 하되 마지막에 **throw 로 러너를 실패로 승격**한다(잡이 이미 끝난 뒤 죽은 경우 초록으로 새는 것을 막는다 — 부팅 스모크 주장 유지). 살아 있으면 SIGTERM → 3s 대기 → 잔존 시 SIGKILL → tmpdir 제거.
- `fixtures/bin/dscan` 등 4종(전부 동일 골격, **실행권한 +x**):

```sh
#!/bin/sh
# e2e 배선 검증용 가짜 도구다 -- 에이전트의 which 발견(→Ready)과 --version 프로브에
# 답하는 것이 전부고, 실제 잡 실행은 stub 어댑터가 대신한다(설계 §2.5). 도구 실측은
# 테스트베드의 몫이다.
echo "dscan (dms-e2e-fake) 0.0.0"
exit 0
```

- `helpers/session.ts`: `apiLogin(page)` — `page.request.post("/api/auth/login", {data: ADMIN})` 후 상태 200 단언. 브라우저 컨텍스트와 쿠키 저장소를 공유하므로 이후 `page.goto` 가 인증 상태다. **E1 외의 파일은 UI 로그인을 반복하지 않는다** — UI 로그인 검증은 E1 의 단일 책임이다(중복 검증은 느리고, E1 이 깨지면 전부 깨져 신호가 뭉개진다).
- `01-boot-session.spec.ts`(E1 — 실쿠키 왕복, msw 가 못 본 것 §1-7):
  - test 1 「미인증 보호 라우트 차단」: `page.goto("/admin/accounts")` → `expect(page).toHaveURL(/\/login$/)` + `getByLabel("사용자명")` visible.
  - test 2 「로그인→관리자 홈→로그아웃→재차단」: `/login` 에서 label 로 채우고(사용자명/비밀번호, §1-11) 「로그인」 클릭 → `toHaveURL(/\/admin\/dashboard$/)`(Home 이 admin 을 리다이렉트 — `router.tsx:31-33`) → `context.cookies()` 에 `dms_session` 존재 단언(실쿠키의 직접 증거) → 「로그아웃」 클릭 → `/login` 도착 → 재차 `goto("/admin/accounts")` → 다시 `/login`(세션이 서버에서 정말 죽었다).

- [ ] **Step 1: RED — 하네스 없이 스펙 먼저**

`01-boot-session.spec.ts` 를 먼저 작성하고 실행한다.
Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npm run build && npx playwright test e2e/01-boot-session.spec.ts`
Expected: **RED 2건** — `page.goto` 에서 `net::ERR_CONNECTION_REFUSED`(:8093 에 아무도 없다). 이것이 e2e 의 "실패하는 테스트 먼저"다 — 하네스가 없으면 한 걸음도 못 간다는 실측.

- [ ] **Step 2: 하네스 구현 + config 배선**

위 Interfaces 대로 harness/fixtures/helpers 생성, `chmod +x frontend/e2e/fixtures/bin/*`, `playwright.config.ts` 에 `globalSetup: "./e2e/harness/global-setup.ts"`, `globalTeardown: "./e2e/harness/global-teardown.ts"` 추가.

- [ ] **Step 3: GREEN**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npm run test:e2e`
Expected: E1 2건 passed. setup 로그에 readyz·storage Ready 폴링 통과가 남는다.

- [ ] **Step 4: 뮤테이션으로 이빨 확인 후 원복**

global-setup 의 admin 부트스트랩 호출(7a)을 잠시 주석 → 재실행 → E1 test 2 가 로그인 401 로 **빨강**(`/admin/dashboard` 도달 실패 — URL 단언이라 셀렉터 공허가 불가능한 모양임을 겸증명). 원복 후 Step 3 재확인.

- [ ] **Step 5: 회귀 확인 + 커밋**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b && npx tsc -p tsconfig.e2e.json`
Expected: 228/49·tsc 초록(e2e 신규 파일이 단위·본빌드에 무영향).

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git add frontend/e2e
git commit -m "feat(e2e): 풀스택 하네스(tmp sqlite+migrate+api dist+controller 1s+agent --once) + E1 부팅·세션 — 가짜 mountinfo 시드(슬라이스 24 '/' 거부 반영)" -- frontend/e2e frontend/playwright.config.ts
```

---

### Task 3: E2 — SPA fallback 딥링크 + 미지 경로

**Files:**
- Create: `frontend/e2e/02-spa-fallback.spec.ts`

**Interfaces:**
- Consumes: `apiLogin`, `app.py:119-126` 의 spa_fallback(§1-6 — dist 서빙이라 이 코드가 검사 대상에 **들어와 있다**), `router.tsx:70` 미지 경로 → `/` → Home → 관리자는 `/admin/dashboard`.
- Produces:
  - test 1 「딥링크 하드 내비게이션」: `apiLogin` → `const resp = await page.goto("/admin/accounts")`(하드 GET — 라우터가 아니라 서버가 응답) → `resp.status() === 200` + URL `/admin/accounts` 유지 + `getByRole("heading", { name: "계정" })` visible(spa_fallback 이 index.html 을 줬고 클라이언트 라우터가 화면을 복원했다).
  - test 2 「미지 경로 수렴」: `page.goto("/no/such/route")` → 최종 URL `/admin/dashboard`(§1-11 정정 없음 — `*` → `/` → Home 리다이렉트 체인).

- [ ] **Step 1: 스펙 작성**

위 계약대로 작성. 문구 단언은 heading 역할(landmark) 하나로 제한 — 설계 §2.1 의 단언 재료 규칙.

- [ ] **Step 2: RED — spa_fallback 뮤테이션으로 실측** (이 시나리오는 현행 코드에서 즉시 초록이 정상이므로, RED 는 뮤테이션이 만든다)

`src/dms/api/app.py` spa_fallback 의 마지막 줄 `return FileResponse(index_path)` 를 임시로 `return JSONResponse(status_code=404, content={"detail": "e2e-mutation"})` 로 교체(임시 — 커밋 금지).
Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npm run test:e2e -- e2e/02-spa-fallback.spec.ts`
Expected: **RED 2건** — test 1 은 `resp.status()` 404, test 2 도 index 를 못 받아 수렴 실패. 하네스가 API 를 매 실행 새로 spawn 하므로 백엔드 변경이 즉시 반영된다.
원복: `git checkout -- src/dms/api/app.py` 후 재실행 → **GREEN 2건**. (이 뮤테이션이 이 태스크의 지정 뮤테이션이다 — "vite dev 서버 위 e2e 는 이 회귀를 영원히 못 잡는다"던 §1-6 의 사각지대가 실제로 닫혔음의 증명.)

- [ ] **Step 3: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git add frontend/e2e/02-spa-fallback.spec.ts
git commit -m "feat(e2e): E2 SPA fallback 딥링크·미지 경로 — dist 서빙 경로의 spa_fallback 을 검사 대상으로" -- frontend/e2e/02-spa-fallback.spec.ts
```

---

### Task 4: `assertLayoutSane` 불변식 4종 + E3 화면 순회 (회귀 방어 본체)

**Files:**
- Create: `frontend/e2e/helpers/layout.ts`, `frontend/e2e/03-layout.spec.ts`

**Interfaces:**
- `helpers/layout.ts`:
  - `assertLayoutSane(page, opts?: { sidebarFixed?: boolean; minTableCells?: number })` — 설계 §2.4 의 L1~L4. 각 실패 메시지에 `[L1]`~`[L4]` 라벨을 담는다(실증 §6-1/2 의 "어느 불변식이 먼저 무는지" 기록용).
    - **L1** 문서 가로 오버플로 금지: `documentElement.scrollWidth <= clientWidth + 1`(±1 은 서브픽셀 반올림).
    - **L2** 표 셀 display 불변식: 모든 `td, th` 의 computed display == `"table-cell"` **그리고 셀 개수 ≥ `minTableCells ?? 0`** — 개수 하한이 없으면 셀렉터가 아무것도 못 찾아도 통과하는 공허한 초록이 된다(지시 6). 표가 있는 화면은 반드시 하한을 넘긴다.
    - **L3** 사이드바 폭 고정(기본 on, `sidebarFixed:false` 로 끔): `aside` boundingBox().width === 240(15rem, `AppShell.tsx:13` md:w-60 — Tailwind preflight 가 border-box 라 패딩 포함 240 이 맞다).
    - **L4** 한 줄 요소: `aside a`(nav 링크)와 `table button` 각각에 대해 `rect.height < 2 × parseFloat(computedStyle.lineHeight)` — 두 결함의 공통 증상(줄바꿈·세로 쪼개짐)의 그물.
  - `assertTableOverflows(page)` — **전제 단언(설계 §2.4 "검사의 반")**: 첫 `.overflow-x-auto` 래퍼의 `scrollWidth > clientWidth`. 실패 메시지는 `E2E_SEED_TOO_NARROW:` 접두로 시작 — 불변식 위반(`[L*]`)과 **다른 메시지**여서 "시드가 좁다"와 "레이아웃이 깨졌다"를 뭉개지 않는다(설계 §4).
- `03-layout.spec.ts`(E3): `apiLogin` 후 —
  - **1280×800(주)**: `/admin/dashboard`(minTableCells 0 — 표 없는 화면, L1/L3/L4 만 유효) → `/admin/accounts`(**`assertTableOverflows` 먼저**, 이어 `assertLayoutSane({minTableCells: 24})` — admin+와이드 3 = 4행×6열 = 24 ≥ 24, 헤더 th 6 이 여유) → `/jobs`(`{minTableCells: 4}` — 파일 순서상 아직 0행, 헤더 th 4) → `/admin/storages`(`{minTableCells: 5}` — e2e-store 1행) → `/admin/builds`(`{minTableCells: 1}` — 헤더만이어도 th 존재. 실행 후 실제 th 수로 조정).
  - **375×667(모바일 spot check)**: `/admin/accounts`(assertTableOverflows + `{sidebarFixed:false, minTableCells:24}`) → `/jobs`(`{sidebarFixed:false, minTableCells:4}`) — md: 분기 아래라 L3 제외, L1/L2/L4 만(설계 §3). 이동은 `page.setViewportSize`.
  - 화면 진입 후 안정화는 heading visible 대기로 한다(networkidle 은 폴링 앱에서 영원히 안 온다 — 함정 명시).

- [ ] **Step 1: 헬퍼 + 스펙 작성**

위 계약대로. minTableCells 실측치가 어긋나면(예: builds 헤더 열 수) **완화가 아니라 실측으로 조정**하고 값 근거를 주석에 남긴다.

- [ ] **Step 2: RED ① — 결함 A 재주입(9fbef86 되돌리기)으로 L2/L4 의 이빨 실측** (설계 §6-1 — "그때 있었으면 잡았다"의 직접 증명. 이 절차 없이는 불변식이 이빨이 있는지 증명할 방법이 없다)

`frontend/src/features/accounts/AccountsList.tsx` 를 임시로 다음 4점 되돌린다(9fbef86 diff 의 정확한 역):
1. `:39` `<tr className="text-muted whitespace-nowrap">` → `<tr className="text-muted">`
2. `:63` 상태 셀 `<td className="whitespace-nowrap">` → `<td>`
3. `:64` 등록일 셀 `<td className="text-muted whitespace-nowrap">` → `<td className="text-muted">`
4. `:71-72` 작업 셀의 `<td className="py-2">` + 안쪽 `<div className="flex items-center gap-2 whitespace-nowrap">` 구조를 **td 직접 flex** 로: `<td className="flex items-center gap-2 py-2">`(안쪽 div 제거, 닫는 `</div>` 도 제거)

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npm run test:e2e -- e2e/03-layout.spec.ts` (체인의 npm run build 가 재빌드한다)
Expected: **RED** — `/admin/accounts` 에서 `[L2]`(td computed display 가 table-cell 이 아니라 flex — 결함 A 의 구조 원인 직격). `[L4]` 동반 여부를 기록.
원복: `git checkout -- frontend/src/features/accounts/AccountsList.tsx`

- [ ] **Step 3: RED ② — 결함 B 재주입(6bc2ecb 되돌리기)으로 L1/L3/L4 의 이빨 실측** (설계 §6-2)

`frontend/src/app/AppShell.tsx` 를 임시로 2점 되돌린다:
1. `:13` aside className 에서 `md:shrink-0 ` 제거
2. `:35` `<div className="flex-1 min-w-0">` → `<div className="flex-1">`

Run: 동일 명령. Expected: **RED** — L1(문서 가로 오버플로)·L3(aside 폭 ≠ 240)·L4(nav 링크 줄바꿈) 중 최소 1건. **어느 불변식이 먼저 무는지 기록한다**(설계 §6-2 요구).
원복: `git checkout -- frontend/src/app/AppShell.tsx`

- [ ] **Step 4: GREEN — 클린 런**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npm run test:e2e -- e2e/03-layout.spec.ts`
Expected: 전부 passed — 특히 `assertTableOverflows` 전제 단언이 통과한다(와이드 시드가 실제로 표를 넘치게 한다는 증명. 만약 여기서 `E2E_SEED_TOO_NARROW` 가 나오면 **불변식을 완화하지 말고** global-setup 의 이메일 길이를 늘린다).

- [ ] **Step 5: 뮤테이션으로 공허 초록 방지의 이빨 확인 후 원복**

`helpers/layout.ts` 의 L2 셀렉터를 잠시 `"td.zzz, th.zzz"` 로 오염 → 재실행 → `/admin/accounts` 검사가 **셀 수 0 < minTableCells 로 빨강** — "셀렉터가 아무것도 못 찾으면 조용히 통과" 가 구조적으로 불가능함의 증명(지시 6 의 e2e 특화 요구). 원복 후 Step 4 재확인.

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git add frontend/e2e/helpers/layout.ts frontend/e2e/03-layout.spec.ts
git commit -m "feat(e2e): E3 레이아웃 불변식 L1~L4 + 전제 단언 — 결함 9fbef86·6bc2ecb 재주입 RED 실측 완료" -- frontend/e2e/helpers/layout.ts frontend/e2e/03-layout.spec.ts
```

---

### Task 5: E4 — 잡 종단 흐름 (UI 제출 → 새로고침 없이 SUCCEEDED)

**Files:**
- Create: `frontend/e2e/04-job-flow.spec.ts`

**Interfaces:**
- Consumes: `apiLogin`(admin — 특권 경로 §1-8: LDAP 없이 uid 0 통과, placement 신원 검사 생략), `/admin/scan` 폼(`SubmitScan.tsx` — `getByLabel("스토리지")` 는 네이티브 select 라 `selectOption("e2e-store")`, `getByLabel("대상 경로")`, 「제출」 버튼), 제출 성공 시 상세 자동 이동(전제 재확인 표), 요청 목록 3s 상시 폴링(§1-11 정정).
- Produces(test 1건):
  1. `/admin/scan` 진입 → 스토리지 `e2e-store` 선택, 대상 경로 **`e4-scan`**(상대경로 — scan target 은 `validate_relative_path`. E5/E6 과 겹치면 resource_key Conflict — 전제 재확인 표의 함정) → 제출 클릭.
  2. `expect(page).toHaveURL(/\/jobs\/([0-9a-f-]+)$/)` — 202 수리와 상세 이동을 URL 로 단언, 정규식 캡처로 `rid` 획득.
  3. 상세에서 `assertLayoutSane(page)` 1회 — 설계 §3 화면 목록의 "내 작업/상세" 순회 몫을 여기서 해결한다(E3 은 파일 순서상 요청이 없어 상세를 못 돈다 — 교차 파일 상태 공유보다 이 배치가 결정적이다).
  4. 사이드바 「내 작업」 클릭(클라이언트 내비 — 하드 리로드 아님) → `/jobs`.
  5. `row = page.getByRole("row").filter({ hasText: rid })` → `expect(row).toHaveCount(1)` → `expect(row).toContainText("Succeeded", { timeout: 30_000 })` — **page.reload() 호출 없음**: 상태 변화는 오직 3s 폴링이 나른다(planner 1s→stepper 1s×3~4틱 stub 종단이라 실측 수 초, 상한 30s 는 설계 §5).

- [ ] **Step 1: 스펙 작성**

위 계약대로. `page.reload()` 가 스펙에 없음을 리뷰 포인트로 주석에 명시("새로고침 없이"가 이 시나리오의 존재 이유다).

- [ ] **Step 2: RED — 에이전트 뮤테이션으로 실측** (현행 코드에서 즉시 초록이 정상이므로 RED 는 하네스 뮤테이션이 만든다)

`harness/global-setup.ts` 의 8단(agent --once)을 잠시 주석 — 9단(storage Ready 폴링)도 함께 주석(그대로면 setup 자체가 죽어 E4 까지 못 간다 — 이 뮤테이션은 "시나리오가 배선을 밟는가"를 겨냥하므로 setup 은 통과시켜야 한다).
Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npm run test:e2e -- e2e/04-job-flow.spec.ts`
Expected: **RED** — 신선 리포트가 없어 플래너가 후보 0 으로 거부(요청 Rejected)하거나 Pending 정체 → "Succeeded" 30s 타임아웃. e2e 가 에이전트 리포트→플래너 후보→스테퍼 전진의 **풀스택 배선을 실제로 밟는다**는 증명이다.
원복(주석 해제) 후 재실행 → **GREEN**.

- [ ] **Step 3: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git add frontend/e2e/04-job-flow.spec.ts
git commit -m "feat(e2e): E4 잡 종단 흐름 — UI scan 제출→특권 경로→stub 종단을 리로드 없이 목록 폴링으로 관측" -- frontend/e2e/04-job-flow.spec.ts
```

---

### Task 6: E5 폴링 수렴 + E6 폴링 중지

**Files:**
- Create: `frontend/e2e/05-polling.spec.ts`

**Interfaces:**
- Consumes: `apiLogin`, `page.request`(브라우저 컨텍스트와 세션 쿠키 공유 — 설계 §5 E5), 목록 3s 상시 폴링(`useJobs.ts:8`), 상세 잡 2s 종단 중지(`useJobs.ts:17-20` — **§1-11 정정: E6 의 계측 대상은 `/api/user/requests/{id}/jobs`** 다).
- Produces(test 2건):
  - **E5 「폴링 수렴」**: `/jobs` 를 연 채 → `page.request.post("/api/user/requests", { data: { operation:"scan", storage:"e2e-store", target:"e5-scan", options:{}, priority:"mid" } })` → status 202 단언, rid5 획득 → `expect(getByRole("row").filter({hasText: rid5})).toHaveCount(1, { timeout: 10_000 })` — **리로드 없이** 3s 폴링 1~2회가 새 행을 나른다(상한 10s, 설계 §5).
  - **E6 「폴링 중지」**: 
    1. `page.request.post`(target **`e6-scan`**) → rid6. 
    2. **API 측 폴링으로 종단 대기**(30s 상한): `page.request.get("/api/user/requests/"+rid6)` 의 state 가 Succeeded 가 될 때까지 — **종단 후 진입**이 설계 의도의 결정적 구현이다: `useRequestJobs` 는 잡 빈 배열에도 폴링을 멈추므로(§1-11 신규 관찰) 비종단 진입은 타이밍 경합(플래너 틱 전 도착 → 잡 영영 안 뜸)으로 플레이키하다. 설계 §6-4 "연속 20회 플레이크 0" 과 양립하는 유일한 형태다.
    3. 카운터 설치: `page.on("request", r => { if (new URL(r.url()).pathname === \`/api/user/requests/${rid6}/jobs\`) count++ })` — pathname **완전 일치**(목록 `/api/user/requests` 3s 상시 폴링과 절대 섞이지 않는다 — 설계 §5 "창·필터를 좁혀").
    4. `page.goto(\`/jobs/${rid6}\`)` → **phase A(생존 증명)**: `expect.poll(() => count).toBeGreaterThanOrEqual(1)` — 마운트 시 최초 fetch 가 반드시 이 필터에 잡힌다. **0 단언은 이 증명 뒤에만 유효하다**(0 은 정상값 — 필터 오타의 공허한 0 과 구분, Global Constraints).
    5. 잡 종단 표시 대기: `expect(page.getByText("Succeeded").first()).toBeVisible()`(이 텍스트는 **폴링되는 잡 쿼리**가 나른다 — 요청 쿼리는 마운트 1회 fetch 라 이 시점 이미 종단 값이다).
    6. 1s 정착 유예(`waitForTimeout(1000)`) — 종단 데이터를 나른 fetch 직후 비행 중이던 마지막 interval 요청이 소진될 여지(설계 §5 의 8s 창 앞에 붙는 플랜의 구체화 — 사유를 주석으로).
    7. `count = 0` 리셋 → `waitForTimeout(8000)` → `expect(count).toBe(0)` — 종단 중지 콜백의 실증(설계 §5 E6).

- [ ] **Step 1: 스펙 작성 후 GREEN 1차 확인**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npm run test:e2e -- e2e/05-polling.spec.ts`
Expected: 2건 passed(현행 앱이 옳으므로 — RED 는 Step 2/3 의 뮤테이션이 만든다).

- [ ] **Step 2: RED ① — E5 의 이빨: 목록 폴링 무력화**

`frontend/src/features/jobs/useJobs.ts:8` 의 `refetchInterval: 3000` 을 임시로 `refetchInterval: false` 로.
Run: 동일 명령(체인이 재빌드). Expected: **E5 빨강** — 새 행이 리로드 없인 영영 안 나타나 10s 타임아웃. "새로고침 없이"가 진짜 하중을 받는 단언임의 증명(첫 로드로도 통과하는 공허한 단언이 아니다 — 행은 제출 **후** 생겼다).
원복: `git checkout -- frontend/src/features/jobs/useJobs.ts` → 재실행 GREEN.

- [ ] **Step 3: RED ② — E6 의 이빨: 종단 중지 무력화**

`useJobs.ts:17-20` 의 `refetchInterval` 콜백을 임시로 상수 `refetchInterval: 2000` 으로(종단 중지 제거).
Run: 동일 명령. Expected: **E6 빨강** — 8s 창에 요청 ~4회가 잡혀 `expect(count).toBe(0)` 실패. 0 단언이 공허하지 않다는 직접 증명(phase A 생존 증명과 한 쌍).
원복: `git checkout -- frontend/src/features/jobs/useJobs.ts` → 재실행 GREEN.

- [ ] **Step 4: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git add frontend/e2e/05-polling.spec.ts
git commit -m "feat(e2e): E5 목록 폴링 수렴 + E6 상세 잡 폴링 종단 중지 — 필터 생존 증명 뒤의 0건 단언" -- frontend/e2e/05-polling.spec.ts
```

---

### Task 7: 수기 게이트 명문화 — deploy/README (설계 §2.2)

**Files:**
- Modify: `deploy/README.md`

**Interfaces:** 문서 1절 — CI 가 없으므로(§1-3) `npm run test:e2e` 가 **배포 전 수기 게이트**라는 사실을 배포 절차에 박는다. §8(포탈 빌드)과 §1(비상 빌드) **양쪽에서 보이는 위치**(두 절보다 앞 공통 위치 또는 §8 도입부)에 짧은 절을 추가한다:

- 제목 예: 「이미지 빌드 전 게이트: 로컬 풀스택 e2e」
- 내용(요지): ① 명령 `cd frontend && npm run test:e2e`(빌드 포함 한 방 — 설계 §2.2) ② 전제: dev 머신에 Google Chrome(`channel:"chrome"`), 부재 시 `npx playwright install chromium` 후 `DMS_E2E_BROWSER_CHANNEL=` 빈 값으로 실행 ③ 무엇을 잡는가 한 줄(기하·세션·SPA fallback·폴링·부팅 — 단위가 못 보는 것) ④ **CI 는 없다** — 이 게이트는 수기이고 CI 구축은 별도 슬라이스(설계 §7)임을 숨기지 않는다.

- [ ] **Step 1: 절 추가 + 확인**

Run: `grep -n "test:e2e" /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/deploy/README.md`
Expected: 신규 절에서 1건 이상 매치. (문서 태스크라 테스트·뮤테이션 없음 — 코드 0줄 변경이 이 태스크의 계약이다: `git diff --stat` 에 deploy/README.md 만 떠야 한다.)

- [ ] **Step 2: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "docs(deploy): 이미지 빌드 전 게이트로 로컬 풀스택 e2e 명문화 — CI 부재를 숨기지 않는다" -- deploy/README.md
```

---

### Task 8: 마감 검증 — 전체 스위트 + 불변 조항 (커밋 없음)

**Files:** 없음(검증만)

- [ ] **Step 1: e2e 클린 런**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npm run test:e2e`
Expected: E1~E6 전부 passed(6 시나리오 상한 유지 — 파일 5개·test 7±1건). 총 소요시간을 기록(실증 §6-3 의 예비 수치).

- [ ] **Step 2: 백엔드 전체 스위트 (무접촉 증명)**

Run(포그라운드, Bash timeout 900000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests -q`
Expected: **1189 passed** — 이 슬라이스는 백엔드를 1줄도 안 바꿨으므로 수치가 **정확히** 기준선과 같아야 한다(±1 도 조사 대상).

- [ ] **Step 3: 프론트 전체 + 타입체크 양쪽**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b && npx tsc -p tsconfig.e2e.json`
Expected: `228 passed / 49 files`(e2e 는 vitest 에 0건 — include 잠금의 최종 증명), tsc 둘 다 exit 0.

- [ ] **Step 4: 불변 조항 확인**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && git status --porcelain && git log --oneline -8 && git diff 70561a8 --stat -- src frontend/src deploy/k8s docs`
Expected: 작업 트리 clean. **`src/`·`frontend/src/`·`deploy/k8s` diff 0**(앱 코드 변경 0 + 태그 무변경 + 신설 사유 0 — reasonCodes.json/api.ts 무변경이 여기 포함), `docs/` 는 이 플랜 파일뿐. 커밋 7건(T1~T7).

---

## 플랜 이후: 실증 (설계 §6 — 로컬이 본체, 클러스터 무관)

이 슬라이스의 실증은 "검사가 그 결함을 정말 잡는가"다 — **전부 이 dev 머신 로컬**이고(헤드리스, 브라우저 GUI 불요), 클러스터·테스트베드는 §6-5 의 읽기 전용 수기 스니펫 외에 일절 건드리지 않는다.

1. **(§6-1) 결함 A 재주입**: Task 4 Step 2 의 절차를 실증 기록용으로 재실행 — 9fbef86 되돌린 빌드에서 E3 가 정확히 `[L2]`(±`[L4]`)로 빨강. 로그를 기록에 남기고 `git checkout` 원복. "그때 있었으면 잡았다"의 직접 증명 — 핵심.
2. **(§6-2) 결함 B 재주입**: Task 4 Step 3 재실행 — 6bc2ecb 되돌린 빌드에서 `[L1]`/`[L3]`/`[L4]` 중 **어느 불변식이 먼저 무는지** 기록. 원복.
3. **(§6-3) 클린 런 소요시간 실측**: `time npm run test:e2e`(빌드 포함)와, 빌드 재사용 후 `time npx playwright test`(빌드 제외 — 하네스는 매번 새로 뜬다) 각각 기록 — "수기 게이트로 감당 가능한가"의 숫자.
4. **(§6-4) 연속 20회 플레이크 0**: 빌드 1회 후 `for i in $(seq 20); do npx playwright test || { echo "FLAKE at $i"; break; }; done` — 1건이라도 실패하면 **그 시나리오를 고치거나 제거하고 사유를 기록**한다(retries:0 이므로 재시도 은폐 불가). 특히 E6 의 1s 정착 유예가 부족하면 유예를 늘리는 게 아니라 원인(비행 중 요청의 정체)을 트레이스로 확인한 뒤 조정한다.
5. **(§6-5) 라이브 포탈 수동 스니펫(배포자 몫, 읽기 전용)**: 테스트베드 포탈(d35)에 로그인한 브라우저 콘솔에서 L1~L4 등가 검사를 1회 수동 실행 — 예: `document.documentElement.scrollWidth - document.documentElement.clientWidth`(≤1), `[...document.querySelectorAll("td,th")].every(c => getComputedStyle(c).display === "table-cell")`, `document.querySelector("aside").getBoundingClientRect().width`(240), 계정 화면에서 실행. 현 라이브가 초록임을 확인(불변식의 실세계 유효성 — 자동화 대상 아님, 클러스터 상태 무변경).

실증 5건 통과 후 `docs/superpowers/BACKLOG.md`(15슬라이스째 e2e 0건 항목 종결 + 현황)를 별도 커밋으로 갱신한다(플랜 밖, 관례).

---

## Self-Review

**1. 스펙 커버리지**

| 설계 절 | 담당 |
|---|---|
| §1 실측 전제 12항 | 「설계 §1 전제 재확인」 표 — 전항 재실측. 정정 3건(§1-9 "/" 시드 불가·§1-11 계측 지점·기준선 1131→1189) + 신규 발견 4건(PYTHONPATH 함정·resource_key 충돌·신선도 300s·빈 배열 폴링 중지) |
| §2.1 4범주·역할 분담(문구 단언 금지·단언 재료 제한) | 전 시나리오 — URL·역할·기하·행 수·상태 값만. 상태 값("Succeeded")은 StatusPill 이 원문 렌더임을 실측해 재료 규칙 안에 둠 |
| §2.2 실 스택 로컬·dist 서빙·수기 게이트·README 명문화 | Task 2(하네스)·Task 7(README). msw 브라우저 모드/vite dev 기각 사유는 설계 그대로(플랜은 재론 안 함) |
| §2.3 @playwright/test 1건·channel:"chrome"·폴백 | Task 1(설치·config)·Task 7(폴백 문구). 대안 기각(§2.3)은 설계 확정 사항 |
| §2.4 불변식 L1~L4 + 전제 단언·시드 | Task 4(helpers/layout.ts — 전제 단언은 고유 메시지 접두로 구분) + Task 2 시드(와이드 이메일 3건) |
| §2.5 하네스 구조(파일 배치·include 명시·workers:1·retries:0·trace·globalSetup 순서·teardown) | Task 1(설정)·Task 2(하네스). **"/" 시드만 §1-9 정정에 따라 가짜 mountinfo 로 대체** — 설계의 목적(마운트포인트 충족)을 다른 수단으로 달성. interval env 는 "전부 1s" 를 잡 전진 관련 4종으로 좁힘(사유 명시) |
| §3 화면 순회·앱 코드 변경 0·testid 0 | E3(목록 화면들×2뷰포트) + E4(상세 1회) — 상세를 E4 로 옮긴 근거 명시(파일 독립성). 앱 코드 diff 0 은 Task 8 Step 4 가 실측 |
| §4 오류 처리(즉시 시끄럽게·skip 금지·컨트롤러 생존·no tests found·전제 단언 구분·locked 비은폐) | Task 2(setup throw 규약·teardown 승격)·Task 1 Step 5c(no tests found 실측)·Task 4(메시지 구분). sqlite locked 는 retries:0 이라 나면 그대로 빨강 — 은폐 장치 없음 |
| §5 E1~E6 | Task 2(E1)·3(E2)·4(E3)·5(E4)·6(E5/E6). E6 만 §1-11 정정(상세 잡 엔드포인트·종단 후 진입)으로 구체화 — 편차 사유를 Task 6 본문에 기록 |
| §6 실증 5항 | 「플랜 이후」 절 — 재주입 2건은 Task 4 의 RED 절차와 동일(실증은 기록 목적의 재실행) |
| §7 하지 않는 것 | CI·픽셀 회귀·크로스브라우저·LDAP/volcano e2e·단위 이관·시나리오 확장 — 어떤 태스크도 만들지 않는다 |

**2. 뮤테이션(이빨) 매트릭스** — 각 태스크에 내장: T1 include 삭제→vitest 재삼킴 RED / placeholder 타입오류→tsc 분리 증명 / no tests found 비0. T2 admin 시드 제거→E1 빨강. T3 spa_fallback 404 뮤테이션→E2 빨강(지정 뮤테이션 겸 RED). T4 결함 A/B 재주입→L2·L1/L3/L4 빨강 + L2 셀렉터 오염→minTableCells 빨강(공허 초록 방지). T5 agent 생략→E4 빨강. T6 refetchInterval 무력화 2건→E5·E6 각각 빨강.

**3. 이름·값 일관성** — 포트 8093(설계 §2.5)·스토리지 `e2e-store`·계정 `admin`(기본 특권 집합 조건)·target `e4-scan`/`e5-scan`/`e6-scan`(resource_key 상이)·노드 `e2e-node`·사이드바 240px(`md:w-60`)·뷰포트 1280×800/375×667·상한 30s(E4)/10s(E5)/8s 창(E6) — 각 정의처와 스펙이 동일 철자·수치다.

**알려진 위험 / 설계 대비 조정:**
- **§1-9 "/" 시드 → 가짜 mountinfo 대체는 설계에 없던 실측 정정이다** — 슬라이스 24 의 `"/"` 명시 거부(422)로 설계 원안은 실행 불가. 대체안은 `DMS_AGENT_MOUNTINFO_PATH` 라는 **기존** 에이전트 설정만 쓰고(하네스 밖 코드 무변경), 머신 마운트 상태 무의존이라 원안보다 결정적이다.
- **E6 의 "종단 후 진입" 은 설계 E6 의 보수적 구체화다** — 비종단 진입 관측은 `useRequestJobs` 의 빈 배열 중지(신규 관찰)와 잡 종단(~3s)의 경합이라 §6-4(20회 플레이크 0)와 양립 불가. 종단 중지 콜백의 이빨은 뮤테이션 RED(Task 6 Step 3)가 별도로 증명한다.
- **interval env "전부 1s" → 4종만 1s** — 목적(잡 전진) 기준으로 좁혔다. 전부 1s 로 해도 동작은 하나 retention/pod-gc 의 매초 쿼리는 트레이스 노이즈만 늘린다.
- **@types/node 전이 의존** — tsconfig.e2e 가 직접 선언 없이 참조한다(신규 의존성 금지 준수). npm 트리 변화로 사라지면 `tsc -p tsconfig.e2e.json` 이 즉시 시끄럽게 깨진다(조용한 회귀 아님) — 그때 devDep 승격을 별도 승인받는다(열린 질문 2).
- **시스템 크롬 버전이 흐른다** — 설계 §2.3 이 수기 스모크 수준에서 수용을 명시. `DMS_E2E_BROWSER_CHANNEL` 노브 + `playwright install chromium` 폴백을 README 에 남긴다.
- **minTableCells 의 구체 수치(24/4/5/1)는 1차 실측치** — 화면 열 수가 바뀌면 갱신될 값이고, 값의 존재 이유(공허 초록 방지)가 본질이다. 실행 중 어긋나면 실측으로 조정하고 근거 주석을 남긴다.
- **하네스 소요**: setup 은 migrate+api+controller+agent+시드로 실측 수 초~십수 초 예상, `npm run build` 가 최대 항목 — §6-3 이 숫자를 남긴다. 스위트 전체가 300s 를 넘어도 신선도는 3600s 설정으로 안전.

## 결정이 필요한 열린 질문

1. **`useRequestJobs` 의 빈 배열 폴링 중지**(§1-11 신규 관찰) — 플래너 틱 전에 상세를 열면 잡이 리로드 없이 영영 안 뜬다. 실사용에서도 제출 직후 상세 자동 이동(SubmitScan)이 정확히 이 창을 밟는다(프로덕션 플래너 10s 틱이라 창이 더 넓다). **이 슬라이스는 앱 코드 변경 0 원칙이라 고치지 않는다** — 포탈 위생 슬라이스 후보로 BACKLOG 에 올릴 것을 제안한다(`jobs === undefined || jobs.some(...)` 꼴이 최소 수정).
2. **@types/node 를 devDependency 로 승격할 것인가** — 지금은 전이 의존으로 충분하나 lock 재생성 시 깨질 수 있다. 깨지는 순간은 시끄러우므로(tsc 실패) 선제 승격은 신규 의존성 금지 원칙과 저울질해 사용자 판단에 맡긴다.
3. **e2e 게이트의 강제 수단이 없다** — CI 부재(설계 §1-3/§7)라 deploy/README 명문화가 전부다. 게이트 우회 배포를 막을 기술 수단(예: 빌드 제출 API 의 체크리스트 필드)은 별도 슬라이스 감이다.
4. **20회 연속 실증(§6-4)의 하네스 기동 비용** — 매회 풀 부팅(~십수 초×20)이 수 분대다. 허용 범위로 보나, 부담이면 "하네스 1회 기동 + playwright 반복" 모드(setup 재사용)를 만들 수 있다 — 단 그건 격리(빈 DB 전제)를 깨므로 시드 멱등화가 선행돼야 한다. 이번 실증은 정직하게 매회 풀 부팅으로 간다.
