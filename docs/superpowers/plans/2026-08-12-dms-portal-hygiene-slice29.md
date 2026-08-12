# 슬라이스 29 — 포탈 위생 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** §2.2 의 남은 유일 포탈 🔴 와 §2.2·§2.5 위생 잔여를 각자 정직한 크기로 닫는다 — **프론트 전용**(백엔드 `src/dms/` 무접촉, 스키마·API 계약 무변경). **항목 1 — 로그아웃 URL 결함**: `useLogout` 의 `onSettled: qc.clear()`(useAuth.ts:21) 가 `me` 쿼리를 **제거**하는데, 제거된 쿼리의 관찰자는 마지막 결과를 든 채 재조회가 안 걸려 `RequireRole` 이 401 을 볼 기회가 없다 → 로그아웃 후 URL 이 /admin/dashboard 에 그대로 남는다(슬라이스 23 E1 실측 30s — 실은 "다음 내비게이션까지 무기한"이고, 대시보드 5s 폴링조차 멈춘다: clear() 가 관찰자 타이머까지 죽인다). 세션 자체는 정상 파기되므로(하드 내비게이션 시 재차단 — e2e 가 그것만 단언) 보안 결함이 아니라 UX 결함이다. 수정은 **qc.clear() 유지 + AppShell 의 명시 nav("/login")** — `useAuth.ts` 는 한 줄도 안 바꾼다(아래 Architecture 에 세 방향 비교와 무한 루프 회피 논리). 고친 뒤 e2e E1 에 "로그아웃 클릭 → /login 도달"을 **계약으로 추가 고정**한다(현 E1 은 세션 파기만 단언하므로 수정 자체는 e2e 무영향 — 실측). **항목 2 — poll_failed 문구 일반화**: api.ts:129 "빌드 상태를 확인하지 못했습니다"는 빌드 전용 문구인데 슬라이스 25 가 잡 로그 조회 list 실패(execution_volcano.py:283 → routes_artifacts.py:145 의 409)에도 이 코드를 재사용해 잡 로그 탭에 빌드 문구가 뜬다. "상태를 확인하지 못했습니다"로 일반화 — **reasonCodes.json 무접촉**(코드 불변, 문구만), 계약 테스트는 키 존재만 양방향 검사라 문구 무관(실측). **항목 3 — useDenylist URL 미인코딩**: useDenylist.ts:9·15 가 `${v.subject}` 를 그대로 URL 에 넣는다. `#` 이 든 subject 는 fragment 로 잘려 **다른 대상에 PUT/DELETE 가 나간다**(wrong-target — 해제(DELETE)가 엉뚱한 항목을 지울 수 있는 실결함), `?` 는 쿼리로 흡수된다. encodeURIComponent 적용. **항목 4 — 테스트 부채 보강(테스트만, 앱 코드 무변경)**: 실측으로 4건이 유효 — jobState 의 PreviewExpired/Planning/Scheduled 매핑 무단언, BatchDetail 확인/취소-POST 단언이 waitFor 밖(플레이키), Sparkline NaN/Infinity 무단언(앱 코드는 슬라이스 26 통합분이 이미 `Number.isFinite` 로 거른다 — 테스트만 부재), by_state 비배열 테스트가 null 만 줘서 `Array.isArray` 와 `?? []` 를 구분 못함. **제외 1건**: PolicyDialog tool 필드 aria-label — 속성은 없으나 `<label>도구 <input/></label>` 감싸기 라벨로 접근 가능한 이름이 이미 있어(RED 를 만들 수 없다) **해소됨 판정**. 새 npm 의존성 0, 새 사유 코드 0, reasonCodes.json 무변경, 백엔드 무변경.

**Architecture:** 항목 1 의 방향 결정 — 세 후보를 실측으로 가른다. **(A) qc.clear() 유지 + 명시 nav(채택)**: AppShell 의 로그아웃 버튼을 `logout.mutate(undefined, { onSettled: () => nav("/login", { replace: true }) })` 로 바꾼다. 근거 ① `useAuth.test.ts` 가 "logout clears the entire cache **even when the request fails**"를 Router 없이(wrapper 는 QueryClientProvider 뿐) 박제하고 있다 — nav 를 `useLogout` 훅 안에 넣으면 useNavigate 가 Router 밖에서 던져 이 테스트가 깨진다. 수정 위치는 **AppShell(컴포넌트 레벨) 단독**이고 useLogout 사용처는 전 코드베이스에서 AppShell 1곳뿐(실측). ② 같은 모양의 선례가 이미 프로덕션에서 돈다: Login.tsx 의 `login.mutate(..., { onSuccess: () => nav("/") })` — 훅-레벨 콜백(qc.clear)이 먼저, mutate-레벨 콜백(nav)이 나중이라는 TanStack v5 순서까지 동일 경로로 검증돼 있다. ③ **무한 루프가 구조적으로 불가능하다**: /login 은 쿼리 관찰자가 0 이다(Login.tsx 는 useMutation 뿐 — 실측). 슬라이스 26 이 Home me.isError 에서 겪은 루프 계열(dms:unauthorized → me 무효화 → 재조회 401 → …)은 **401 응답을 내는 쿼리가 살아 있어야** 돈다 — /login 도착 즉시 API 호출이 0 이므로 발화 자체가 없다. 네트워크 왕복 추가도 0. **(B) invalidate 로 재조회 유도(기각)**: clear 를 invalidate(me) 로 바꾸면 me 재조회 → 401 → RequireRole 리다이렉트가 되긴 하지만, ① 캐시 전체 소거 계약(교차사용자 누수 방지의 절반 — useAuth.test 박제)이 깨지고 ② 401 왕복 + "관찰자가 언마운트되어야 끊기는" 루프 그물(AuthContext 주석의 조건 — 정확히 슬라이스 26 계열) 위에 UX 를 얹는 꼴이라, me 관찰자를 오류 상태로 유지하는 라우트가 하나라도 생기면 루프가 재개방된다. ③ 재조회 중엔 이전 data 가 남아 오류가 착지할 때까지 관리자 화면이 잔류한다. **(C) setQueryData(me, null)(기각)**: clear 뒤 setQueryData 는 me 쿼리 엔트리를 **재생성**하는데, 제거된 쿼리에 묶여 있던 RequireRole 관찰자가 재생성 엔트리에 재결합하는지는 TanStack 내부 수명주기다 — 바로 그 수명주기의 미묘함("관찰자 마운트 중 clear")이 이 버그의 원인이므로 같은 층에 더 얹지 않는다. **nav 는 onSuccess 가 아니라 onSettled**: 로그아웃 POST 가 실패해도 캐시는 이미 비워졌으므로(훅 onSettled — 실패 시에도, 박제됨) 관리자 화면에 남는 것은 "데이터 없는 동결 화면"이라는 최악이다 — 사용자 의도(떠나기)대로 /login 으로 보낸다. 서버 세션이 실제로 살아남은 희귀 케이스는 다음 로그인/하드 내비게이션이 진실을 복원한다(열린 질문 2). SPA nav vs location 하드 내비게이션: 하드 쪽이 더 방탄이지만 jsdom 에서 단언 불가·SPA 상태 불필요 폐기라, 캐시가 이미 빈 SPA nav 로 같은 종착 상태를 얻는다(하드 경로는 e2e E1 의 기존 page.goto 단언이 별도로 지킨다). 항목 3 의 실측 등급: 공백·비ASCII 는 fetch 의 URL 파서가 스스로 %인코딩해 무해(구분 사례가 아니다) — 구분 사례는 `#`(fragment 절단 → wrong-target)와 `?`(쿼리 흡수)다. `/` 는 인코딩해도 못 살린다: ASGI 서버가 라우팅 전에 %2F 를 디코드해 경로가 갈라져 404 다 — wrong-target 이 "깨끗한 실패"로 바뀌는 것까지가 이 수정의 정직한 범위(열린 질문 1). 항목 4 는 전부 **즉시 초록이 정상인 회귀 그물**(앱 코드가 이미 옳다)이라 RED 는 각 Step 의 뮤테이션이 담당한다 — 슬라이스 27·28 의 "회귀 방지 그물" 관례.

**Tech Stack:** React 18 + TanStack Query v5 + react-router v7 + msw v2 + Playwright(전부 기존 — 신규 npm 0). 백엔드·DB·deploy 무접촉(이미지 태그 범프는 「플랜 이후」의 배포자 몫). 배포는 제어면 `dms` d39→**d40** 만 — 프론트 dist 가 제어면 이미지에 COPY 되므로 프론트 변경도 이미지 재빌드가 필요하다. 스키마·API 계약 무변경이라 migrate 재실행 불요.

## Global Constraints

- **설계 문서 없음** — 이 슬라이스의 「왜」는 BACKLOG §2.2(로그아웃 🔴·poll_failed)·§2.5(슬라이스 1~4·14 잔여)와 이 플랜의 「전제 재확인」이 담는다. 플랜과 코드 실측이 충돌하면 실측이 이긴다.
- **프론트 전용.** `src/dms/`·`tests/`(백엔드)·`deploy/k8s`·`legacy/` 무접촉. `reasonCodes.json` 무변경(항목 2 는 api.ts 문구만). 새 npm 의존성 금지.
- **무한 루프 금지 검증**: 로그아웃 수정은 반드시 "me 재조회 횟수 불변" 단언을 가진 테스트로 루프 부재를 증명한다(Task 1 Step 1 — 슬라이스 26 Home me.isError 의 교훈). null/실패 구분 유지, 0·빈문자열 truthy 검사 금지(기존 코드 관례).
- **커밋은 pathspec 으로 한정한다**: 항상 `git commit -m "..." -- <경로들>`. `git add` 계열 **금지**(워크트리 공유 중 인덱스 섞임 사고 — BACKLOG §2.6). 커밋 메시지 말미에 반드시:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01NxyaMVBiYCAHbZaHcYJGBq`
- **뮤테이션 원복에 `git checkout` 금지** — 뮤테이션 전 `cp <파일> /tmp/slice29-<파일명>.bak` 으로 사본을 뜨고, 확인 후 `cp` 로 되돌린다.
- **origin push 금지, 브랜치 변경 금지**(현재 `worktree-dms-slice22plus`, HEAD 39e334c = 슬라이스 28 완료). `docs/` 아래는 이 플랜 파일 외 생성·수정 금지(실증 후 BACKLOG 갱신은 플랜 밖 관례).
- 프론트 명령: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run`(**기준선 257 passed — 2026-08-12 실측 완료**)·`npx tsc -b`(무출력 exit 0 — 실측 완료). e2e `npm run test:e2e`(**9 passed**) — **로그아웃 e2e(01-boot-session.spec.ts)를 건드리는 Task 1 은 반드시 e2e 를 돌린다.** e2e 하네스는 :8093 로컬 풀스택(본 저장소 공용 `.venv` 의 dms CLI + 워크트리 PYTHONPATH — harness/env.ts)이라 클러스터 불요.
- 백엔드는 확인만: Task 5 에서 `git diff` 로 `src/`·`tests/`·`deploy/` 무접촉을 검증한다(백엔드 스위트 재실행 불요 — 한 파일도 안 바뀌므로).
- 주석은 **한국어**로 「왜」를 적는다.

## 전제 재확인 (2026-08-12, 코드 직접 실측)

과제가 제시한 사전 조사 전부를 코드로 재확인했다 — 유효 7건, 해소·정정 2건, 추가 발견 5건.

| 전제 | 재확인 결과 |
|---|---|
| 1. useAuth.ts:21 `onSettled: qc.clear()` → 로그아웃 후 URL 잔류, e2e 는 세션 파기만 단언 | ✓ 전부 유지. e2e 주석(01-boot-session.spec.ts:33-39)이 30s 실측을 박제하고 "이 사실을 단언으로 굳히지 않는다"고 명시 — **수정해도 e2e 무영향**의 실측 근거. 추가: useLogout 사용처는 AppShell.tsx:38 **단 1곳**, `useAuth.test.ts` 는 **Router 없이** 훅을 렌더(wrapperFor = QueryClientProvider 뿐) → nav 를 훅에 넣으면 그 테스트가 깨진다 — 수정 위치(AppShell)의 결정 근거 |
| 2. api.ts:129 poll_failed "빌드 상태를 확인하지 못했습니다" + 슬라이스 25 재사용 | ✓ 위치 정확. 재사용 경로 실측: `execution_volcano.py:283`(list 예외 → poll_failed) → `routes_artifacts.py:145-147`(409 detail) → 잡 로그 탭. **문구 문자열은 저장소 전체에서 api.ts:129 한 곳** — 어떤 테스트도 이 문구를 단언하지 않고, reasonCodes.test.ts 양방향 계약은 키 존재만 검사(문구 무관) → 문구 변경은 계약 안전 |
| 3. useDenylist.ts:9·15 미인코딩 | ✓. 등급 실측: `#` → fragment 절단 → **wrong-target PUT/DELETE**(예: subject "grp#1" 의 해제가 ".../grp" 로 나간다), `?` → 쿼리 흡수. 공백·비ASCII 는 fetch URL 파서가 자동 %인코딩해 이미 무해 — 테스트 재료로 못 쓴다. `/` 는 인코딩해도 백엔드 404(ASGI 가 라우팅 전 %2F 디코드) — 열린 질문 1 |
| 4a. jobState.test 의 PreviewExpired/Planning/Scheduled 누락 | ✓ 유효 — pillVariant 단언은 Succeeded/Failed/Rejected/Cancelled/Executing/ConfirmPending/Pending(+Ready·Running 못박기)뿐. PreviewExpired 는 isTerminal 로만, Planning/Scheduled 는 전무(jobState.test.ts 전문 실측). jobState.ts:9-10 은 셋 다 다룬다 |
| 4b. PolicyDialog tool 필드 aria-label 없음 | **해소됨 판정으로 제외.** aria-label 속성은 tool 필드에만 없다(다른 7개 필드는 있음 — PolicyDialog.tsx:46-67). 그러나 tool 필드는 `<label className="block">도구 <input ... disabled /></label>`(:43-44) 감싸기 라벨이라 접근 가능한 이름 "도구"가 **이미 있다** — getByLabelText("도구") 가 지금도 해석된다. RED 를 만들 수 없는(즉시 초록) 항목이라 결함 부재. 속성 통일은 취향 문제로 남긴다(열린 질문 5) |
| 4c. BatchDetail 확인-POST 단언이 waitFor 밖 | ✓ 유효 — BatchDetail.test.tsx:28(`expect(confirmed).toBe(true)`)·:39(cancel 동형). userEvent.click 이 fetch 착지를 보장하지 않아 플레이키 위험 |
| 4d. Sparkline NaN/Infinity 무검증 | **반쯤 낡음 — 테스트만 유효.** 앱 코드는 이미 `Number.isFinite` 로 거른다(Sparkline.tsx:9 filter·:17 선 절단·:39 circle validIdx — 슬라이스 26 의 1점 circle 코드와 한 몸으로 통합돼 있다). 남은 것은 **테스트 부재**뿐 — sparklinePath·circle 어느 테스트도 NaN/Infinity 를 안 준다(Sparkline.test.tsx 전문 실측). 백로그 문구 "무검증"은 코드 기준으론 정정 대상(BACKLOG 갱신 시 기록) |
| 4e. by_state:null 테스트가 Array.isArray 와 ?? [] 를 구분 못함 | ✓ 유효 — Dashboard.test.tsx:84·JobStatsSection.test.tsx:71 둘 다 `by_state: null` 만 줘서 `null ?? []` 구현도 통과한다. 앱은 Array.isArray(Dashboard.tsx:41, JobStatsSection.tsx:10 `asArray`) — 비배열 truthy(`{}`·`"oops"`)를 줘야 두 구현이 갈라진다 |
| 5. 기준선 | vitest **257 passed**·`tsc -b` 무출력 exit 0 — 이 플랜 작성 시점에 실측 완료. e2e 9 는 Task 0 에서 재확인 |
| 6. 배포 태그 | 실측: 제어면 `dms` 5곳 전부 **d39**(30-migrate-job.yaml:25 / 40-api.yaml:67·84 / 41-controller.yaml:35·52), `dms-agent` d35, `DMS_JOB_IMAGE` d35. 이 슬라이스는 프론트 전용 → 제어면 **d40** 만, 에이전트·잡 이미지 무접촉 |

**추가 발견(과제 지시에 없던 것):**

- **"30초"의 실체** — 대시보드 폴링은 5s 인데 왜 30s 잔류였나: `qc.clear()` 는 쿼리와 함께 관찰자의 refetchInterval 타이머까지 죽여 **폴링 자체가 멈춘다**. 화면은 다음 내비게이션/요청까지 무기한 동결이고(E1 주석의 "다음 내비게이션 때 일어난다"와 일치), 30s 는 관찰 창의 길이였을 뿐이다. 함의: clear 이후 "자동으로 재조회가 일어나 401 을 볼" 통로가 **하나도 없다** — 명시 nav 가 필요한 구조적 이유.
- **/login 은 쿼리 관찰자 0** — Login.tsx 는 useMutation(login)뿐, useMe 없음(실측). nav 도착 즉시 API 호출이 0 이므로 dms:unauthorized 가 발화할 재료 자체가 없다 — 루프 원천 차단의 실측 근거이자, Task 1 테스트의 "me 재조회 횟수 불변" 단언이 성립하는 이유.
- **mutate-레벨 콜백 선례** — Login.tsx 가 `login.mutate(..., { onSuccess: () => nav("/") })` + 훅-레벨 `onSuccess: qc.clear()` 조합을 이미 프로덕션에서 쓴다(훅 콜백 먼저 → mutate 콜백 나중). 로그아웃을 같은 모양으로 만들면 새 패턴이 아니라 기존 패턴의 대칭 적용이다.
- **AuthContext 의 401 처리는 응답이 있어야 발화** — `dms:unauthorized` 는 api.ts request() 의 401 분기에서만 dispatch 된다(api.ts:216, 403 비발화는 계약 테스트로 박제). 로그아웃 POST 자체는 200/204 라 이벤트가 없다.
- **라이브 접점** — 포탈은 NodePort 30080(45-api-nodeport.yaml). 「플랜 이후」 실증에서 사용.

## 파일 구조

| 파일 | 책임 |
|---|---|
| `frontend/src/app/AppShell.tsx` (수정) | Task 1: 로그아웃 버튼에 mutate-레벨 `onSettled: nav("/login")` |
| `frontend/src/app/router.test.tsx` (수정) | Task 1: 로그아웃 → /login 도달 + me 재조회 횟수 불변(루프 부재) |
| `frontend/e2e/01-boot-session.spec.ts` (수정) | Task 1: E1 에 "클릭 즉시 /login 도달" 단언 추가(버그 박제 주석 교체) |
| `frontend/src/lib/api.ts` (수정) | Task 2: poll_failed 문구 일반화(문구만 — 키·JSON 무변경) |
| `frontend/src/lib/api.test.ts` (수정) | Task 2: poll_failed 문구 핀(빌드 전용 문구 금지) |
| `frontend/src/features/denylist/useDenylist.ts` (수정) | Task 3: subject_type·subject encodeURIComponent |
| `frontend/src/features/denylist/DenylistList.test.tsx` (수정) | Task 3: `#` PUT·`?` DELETE 인코딩 단언 2건 |
| `frontend/src/lib/jobState.test.ts` (수정) | Task 4a: PreviewExpired/Planning/Scheduled 매핑 단언 |
| `frontend/src/features/batches/BatchDetail.test.tsx` (수정) | Task 4c: confirm/cancel POST 단언을 waitFor 안으로 |
| `frontend/src/components/ui/Sparkline.test.tsx` (수정) | Task 4d: NaN/Infinity 절단·circle 좌표 단언 |
| `frontend/src/features/dashboard/Dashboard.test.tsx` (수정) | Task 4e: by_state 비배열 truthy({}) 생존 단언 |
| `frontend/src/features/dashboard/JobStatsSection.test.tsx` (수정) | Task 4e: by_state 비배열 truthy("oops") 생존 단언 |

---

### Task 0: 기준선 확인 (커밋 없음)

**Files:** 없음(검증만)

**Interfaces:** 이후 모든 태스크의 판정 기준(기준선 초록)을 만든다.

- [ ] **Step 1: 프론트·e2e 기준선**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b && npm run test:e2e`
Expected: vitest `257 passed`, tsc 무출력 exit 0, e2e `9 passed`. 여기 빨강이면 이 슬라이스 밖의 문제다 — 진행 전에 보고. (e2e 는 :8093 이 비어 있어야 한다 — 하네스가 점유 시 스스로 실패한다.)

---

### Task 1: 로그아웃 — qc.clear() 유지 + AppShell 명시 nav (+ e2e 계약 고정)

**Files:**
- Modify: `frontend/src/app/AppShell.tsx`
- Modify: `frontend/src/app/router.test.tsx`
- Modify: `frontend/e2e/01-boot-session.spec.ts`

**Interfaces:**
- Produces: 로그아웃 클릭 → (훅 onSettled 로 캐시 전체 소거, 기존 그대로) → mutate-레벨 onSettled 로 `/login` 으로 SPA 이동(replace). POST 실패 시에도 이동(사용자 의도는 "떠나기"이고 캐시는 이미 비어 관리자 화면 잔류가 최악이다).
- Consumes: `useNavigate`(react-router — AppShell 은 항상 Router 안에서 렌더된다: 앱은 BrowserRouter, 테스트는 MemoryRouter), `useLogout`(무변경 — useAuth.ts 무접촉).
- **함정 명시 3건**: ① nav 를 `useLogout` 훅에 넣지 마라 — `useAuth.test.ts` 가 Router 없이 훅을 렌더해 useNavigate 가 던진다(기존 테스트 2건 즉사). ② 콜백 순서는 훅(clear) → mutate(nav)지만, 뒤집혀도 안전하게 설계돼 있다(/login 은 관찰자 0 이라 nav 후 clear 도 무해) — 순서에 기대는 코드를 쓰지 마라. ③ e2e E1 의 기존 주석(:33-39)은 이 버그의 박제다 — 고치면서 주석을 안 바꾸면 코드와 주석이 서로 거짓말한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/src/app/router.test.tsx` — 상단 import 에 `import userEvent from "@testing-library/user-event";` 추가 후, 파일 끝에:

```tsx
test("로그아웃 클릭은 즉시 로그인 화면으로 이동한다 -- me 재조회 루프 없이", async () => {
  // 슬라이스 29(§2.2 🔴): qc.clear() 는 관찰자 타이머까지 죽여 "자동 재조회로 401 을
  // 보게 되는" 통로가 없다 -- 명시 nav 가 유일한 전환 수단이다. me 호출 횟수 불변
  // 단언이 루프 부재 증명이다: /login 은 쿼리 관찰자가 0(Login 은 mutation 뿐)이라
  // dms:unauthorized -> me 무효화 -> 재조회 401 루프(슬라이스 26 계열)가 발화할
  // 재료 자체가 없어야 한다.
  let meCalls = 0;
  let alive = true;
  server.use(
    http.get("/api/auth/me", () => {
      meCalls += 1;
      return alive
        ? HttpResponse.json({ actor: "admin", role: "admin" })
        : HttpResponse.json({ detail: "not_authenticated" }, { status: 401 });
    }),
    http.post("/api/auth/logout", () => {
      alive = false;
      return new HttpResponse(null, { status: 204 });
    }),
    http.get("/api/admin/identity-denylist", () => HttpResponse.json([])),
  );
  renderAt("/admin/denylist");
  await screen.findByRole("heading", { name: "denylist" });
  const callsBeforeLogout = meCalls;

  await userEvent.click(screen.getByRole("button", { name: "로그아웃" }));

  // 클릭 즉시(하드 내비게이션 없이) 로그인 화면 -- 이것이 이번 수정의 계약이다.
  expect(await screen.findByRole("button", { name: "로그인" })).toBeInTheDocument();
  // 루프 부재: 정착 창을 두고도 me 재조회가 한 번도 안 일어난다.
  await new Promise((r) => setTimeout(r, 150));
  expect(meCalls).toBe(callsBeforeLogout);
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run src/app/router.test.tsx`
Expected: 신규 1건 FAIL — `findByRole("button", { name: "로그인" })` 타임아웃(현행은 clear 후 화면이 denylist 에 동결 — 정확히 실측된 버그). 기존 테스트 전부 PASS.

- [ ] **Step 3: AppShell 을 고친다**

`frontend/src/app/AppShell.tsx` — import 를 `import { NavLink, useLocation, useNavigate } from "react-router-dom";` 로 바꾸고, 컴포넌트에 `const nav = useNavigate();` 추가, 로그아웃 버튼(:38)을 다음으로 교체:

```tsx
          {/* 슬라이스 29(§2.2 🔴): qc.clear()(useLogout onSettled, 유지)는 me 쿼리를
              제거해 관찰자 재조회·폴링이 전부 멈춘다 -- RequireRole 이 401 을 볼
              통로가 없어 명시 nav 가 유일한 전환 수단이다. nav 가 훅이 아니라 여기
              (컴포넌트)에 있는 이유: useAuth.test 는 Router 없이 훅을 렌더한다.
              onSettled 인 이유: POST 실패여도 캐시는 이미 비어(훅 onSettled, 실패
              시에도 -- 박제됨) 관리자 화면 잔류가 최악이다 -- 떠나려는 의도대로
              보낸다. /login 은 쿼리 관찰자 0 이라 재조회 루프가 성립하지 않는다. */}
          <button className="text-sm text-accent"
                  onClick={() => logout.mutate(undefined,
                    { onSettled: () => nav("/login", { replace: true }) })}>로그아웃</button>
```

- [ ] **Step 4: 통과를 확인한다 (vitest 전체 + tsc)**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b`
Expected: `258 passed`(257 + 1), tsc 무출력 exit 0. 특히 `useAuth.test.ts` 2건(캐시 소거 계약 — useAuth.ts 무접촉의 증거)과 `AuthContext.test.tsx`·Home 401 테스트(루프 그물 기존분)가 그대로 초록.

- [ ] **Step 5: e2e 계약 고정 + 실행**

`frontend/e2e/01-boot-session.spec.ts` — :33-39 의 버그 박제 주석 단락("여기서 「클릭 직후 /login 으로 튄다」를 단언하지 않는 이유는 실측이다: … 서버 세션이 죽었다는 쪽이다.")을 다음으로 교체하고, `expect((await logoutDone).status()).toBe(200);` 바로 아래에 URL 단언을 추가:

```ts
    // 로그아웃 왕복이 실제로 200 으로 끝난 것을 재료로 삼는다.
    //
    // 슬라이스 29 가 「클릭 즉시 /login 도달」을 계약으로 만들었다(AppShell 의
    // 명시 nav -- qc.clear() 는 관찰자 재조회·폴링까지 멈춰 자동 전환 통로가
    // 없다는 실측이 근거). 아래 URL 단언이 그 계약이고, 서버 세션 파기는 그
    // 다음의 하드 내비게이션 단언이 별도로 지킨다 -- 두 계약은 독립이다.
    const logoutDone = page.waitForResponse(
      (r) => r.url().endsWith("/api/auth/logout") && r.request().method() === "POST");
    await page.getByRole("button", { name: "로그아웃" }).click();
    expect((await logoutDone).status()).toBe(200);
    await expect(page).toHaveURL(/\/login$/);
```

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npm run test:e2e`
Expected: `9 passed`(테스트 수 불변 — E1 강화이지 신설이 아니다). E1 이 빨개지면 수정이 실 브라우저에서 안 먹는 것 — 진행 중단·보고.

- [ ] **Step 6: 뮤테이션으로 이빨 확인 후 원복**

`cp src/app/AppShell.tsx /tmp/slice29-AppShell.tsx.bak` 후: 로그아웃 버튼의 mutate 인자를 원래 `logout.mutate()` 로 되돌린다(nav 제거 — "고치기 전" 상태의 정확한 모형) → `npx vitest run src/app/router.test.tsx` 에서 신규 테스트만 RED(로그인 버튼 타임아웃), 기존 라우팅 테스트는 초록 — 기존 그물이 이 결함을 못 보던 것의 실증. `cp /tmp/slice29-AppShell.tsx.bak src/app/AppShell.tsx` 로 원복, Step 4 재확인(vitest 만 — e2e 재실행 불요, 파일 무변경).

- [ ] **Step 7: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "$(cat <<'EOF'
fix(portal): 로그아웃 클릭 즉시 /login 이동 — qc.clear() 유지 + AppShell 명시 nav(관찰자 동결이라 자동 전환 통로 없음), e2e E1 에 URL 계약 고정

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NxyaMVBiYCAHbZaHcYJGBq
EOF
)" -- frontend/src/app/AppShell.tsx frontend/src/app/router.test.tsx frontend/e2e/01-boot-session.spec.ts
```

---

### Task 2: poll_failed 문구 일반화 (api.ts 문구만 — reasonCodes.json 무접촉)

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/api.test.ts`

**Interfaces:**
- Produces: `REASON_MESSAGES.poll_failed = "상태를 확인하지 못했습니다"` — 빌드 폴링과 잡 로그 조회 409 양쪽에서 읽히는 문구.
- **계약 확인(실측 완료)**: 옛 문구는 저장소 전체에서 api.ts:129 한 곳이라 문구를 단언하는 테스트가 없고, reasonCodes.test.ts 양방향 계약은 키 존재만 본다 — 키·JSON 무변경이므로 계약 테스트 무영향. 새 핀 테스트가 "빌드 전용 문구 금지"라는 「왜」를 박제한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/src/lib/api.test.ts` — `describe("reasonText", ...)` 블록 끝에:

```ts
  it("poll_failed 는 빌드 전용 문구가 아니다 -- 잡 로그 조회 409 도 같은 코드를 재사용한다", () => {
    // 슬라이스 25 가 vcjob 로그 list 실패(execution_volcano.py -> routes_artifacts.py
    // 409)에 poll_failed 를 재사용했다("사유 코드 신설 0" 방침) -- 문구에 "빌드"가
    // 들어가면 잡 로그 탭에서 거짓말이 된다. 공유 코드의 문구는 문맥 중립이어야 한다.
    expect(reasonText("poll_failed")).toBe("상태를 확인하지 못했습니다");
    expect(reasonText("poll_failed")).not.toMatch(/빌드/);
  });
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run src/lib/api.test.ts`
Expected: 신규 1건 FAIL(현행 "빌드 상태를 확인하지 못했습니다" ≠ 기대 문구, /빌드/ 매치). 기존 전부 PASS.

- [ ] **Step 3: 문구를 고친다**

`frontend/src/lib/api.ts:129` 를:

```ts
  // 빌드 폴링과 잡 로그 조회 409(슬라이스 25 재사용)가 같은 코드를 낸다 --
  // 공유 코드의 문구는 문맥 중립이어야 한다("빌드" 금지, §2.2).
  poll_failed: "상태를 확인하지 못했습니다",
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b`
Expected: `259 passed`(258 + 1), tsc 무출력. `reasonCodes.test.ts` 2건 초록 = 키·JSON 무접촉의 실측 확인.

- [ ] **Step 5: 뮤테이션으로 이빨 확인 후 원복**

`cp src/lib/api.ts /tmp/slice29-api.ts.bak` 후: 문구를 "빌드 상태를 확인하지 못했습니다"로 되돌린다 → 신규 테스트만 RED. `cp /tmp/slice29-api.ts.bak src/lib/api.ts` 로 원복, Step 4 재확인.

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "$(cat <<'EOF'
fix(portal): poll_failed 문구 일반화 — 빌드·잡 로그가 공유하는 코드라 문맥 중립 문구로(키·reasonCodes.json 무변경)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NxyaMVBiYCAHbZaHcYJGBq
EOF
)" -- frontend/src/lib/api.ts frontend/src/lib/api.test.ts
```

---

### Task 3: useDenylist URL 인코딩 — wrong-target PUT/DELETE 봉쇄

**Files:**
- Modify: `frontend/src/features/denylist/useDenylist.ts`
- Modify: `frontend/src/features/denylist/DenylistList.test.tsx`

**Interfaces:**
- Produces: PUT/DELETE 경로 세그먼트에 `encodeURIComponent(v.subject_type)`·`encodeURIComponent(v.subject)`.
- **테스트 재료 선택의 「왜」**: 공백·비ASCII 는 fetch 의 URL 파서가 자동 %인코딩해 현행도 통과한다(구분 못함). `#` 은 fragment 로 잘려 **경로가 짧아지고**(".../grp#1" → ".../grp" — 다른 대상), `?` 는 쿼리로 흡수된다 — 이 둘만이 RED 를 만든다.
- **한계 명시**: subject 에 `/` 가 들면 인코딩(%2F)해도 백엔드가 404 다(ASGI 가 라우팅 전에 경로를 디코드 — 열린 질문 1). 이 수정의 정직한 범위는 "wrong-target → 깨끗한 실패"까지다. msw 핸들러는 `:subject` 파라미터(디코드된 값)와 원시 pathname 양쪽을 단언해 인코딩 사실 자체를 본다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/src/features/denylist/DenylistList.test.tsx` — import 에 `waitFor` 추가(`@testing-library/react`), 파일 끝에:

```tsx
test("subject 의 # 는 인코딩되어 정확한 대상에 PUT 된다 -- fragment 절단 금지", async () => {
  // 미인코딩이면 fetch 가 "#1" 을 fragment 로 버려 ".../group/grp" 에 PUT 된다
  // (wrong-target). 공백·비ASCII 는 URL 파서가 자동 인코딩해 재료가 못 된다 --
  // # 만이 현행과 수정본을 가른다.
  let seenSubject = "";
  let seenPath = "";
  server.use(
    http.get("/api/admin/identity-denylist", () => HttpResponse.json([])),
    http.put("/api/admin/identity-denylist/:type/:subject", ({ params, request }) => {
      seenSubject = String(params.subject);
      seenPath = new URL(request.url).pathname;
      return HttpResponse.json({ subject_type: "group", subject: "grp#1", reason: null });
    }));
  wrap();
  await screen.findByText("등재된 대상이 없습니다");
  await userEvent.click(screen.getByRole("button", { name: "대상 추가" }));
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "대상 유형" }), "group");
  await userEvent.type(screen.getByRole("textbox", { name: "대상" }), "grp#1");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await waitFor(() => expect(seenSubject).toBe("grp#1"));
  expect(seenPath).toBe("/api/admin/identity-denylist/group/grp%231");
});

test("subject 의 ? 는 인코딩되어 정확한 대상에 DELETE 된다 -- 쿼리 흡수 금지", async () => {
  // 미인코딩이면 "?y" 가 쿼리로 흡수돼 ".../requester/x" 가 지워진다 --
  // 해제(DELETE)의 wrong-target 은 엉뚱한 차단을 푸는 실사고다.
  let seenSubject = "";
  server.use(
    http.get("/api/admin/identity-denylist", () => HttpResponse.json(
      [{ subject_type: "requester", subject: "x?y", reason: null }])),
    http.delete("/api/admin/identity-denylist/:type/:subject", ({ params }) => {
      seenSubject = String(params.subject);
      return new HttpResponse(null, { status: 204 });
    }));
  wrap();
  await screen.findByText("x?y");
  const row = screen.getByText("x?y").closest("tr")!;
  await userEvent.click(within(row).getByRole("button", { name: "해제" }));
  await userEvent.click(await screen.findByRole("button", { name: "해제 확인" }));
  await waitFor(() => expect(seenSubject).toBe("x?y"));
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run src/features/denylist/DenylistList.test.tsx`
Expected: 신규 2건 FAIL — PUT 은 `seenSubject === "grp"`(fragment 절단), DELETE 는 `"x"`(쿼리 흡수) — waitFor 타임아웃으로 RED. 기존 3건(wheel/alice — 인코딩 불변 문자) PASS: 기존 그물이 이 결함에 눈멀었던 것의 실증.

- [ ] **Step 3: useDenylist 를 고친다**

`frontend/src/features/denylist/useDenylist.ts` — :9·:15 의 URL 조립을 교체(파일이 작으니 두 mutationFn 만 정확히):

```ts
export const useDeny = () => {
  const qc = useQueryClient();
  // subject 는 사용자 입력이다 -- #(fragment 절단)·?(쿼리 흡수)가 경로를 바꿔
  // 다른 대상에 PUT/DELETE 가 나가는 wrong-target 이 실결함이라 세그먼트 단위로
  // 인코딩한다. subject_type 은 select 고정값이지만 규칙을 한 벌로 유지한다.
  return useMutation({ mutationFn: (v: { subject_type: string; subject: string; reason: string | null }) =>
    apiSend("PUT", `/api/admin/identity-denylist/${encodeURIComponent(v.subject_type)}/${encodeURIComponent(v.subject)}`, { reason: v.reason }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["denylist"] }) });
};
export const useAllow = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (v: { subject_type: string; subject: string }) =>
    apiSend("DELETE", `/api/admin/identity-denylist/${encodeURIComponent(v.subject_type)}/${encodeURIComponent(v.subject)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["denylist"] }) });
};
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b`
Expected: `261 passed`(259 + 2), tsc 무출력. 기존 wheel/alice 테스트 초록 = 인코딩 불변 문자의 무회귀 증거.

- [ ] **Step 5: 뮤테이션으로 이빨 확인 후 원복**

`cp src/features/denylist/useDenylist.ts /tmp/slice29-useDenylist.ts.bak` 후: `encodeURIComponent(v.subject)` 한쪽(useAllow)만 `v.subject` 로 되돌린다 → DELETE 테스트만 RED, PUT 테스트 초록 — 두 mutationFn 이 각자 그물을 가진 것의 확인. `cp /tmp/slice29-useDenylist.ts.bak src/features/denylist/useDenylist.ts` 로 원복, Step 4 재확인.

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "$(cat <<'EOF'
fix(portal): denylist PUT/DELETE 경로 세그먼트 encodeURIComponent — #(fragment)·?(쿼리)의 wrong-target 봉쇄

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NxyaMVBiYCAHbZaHcYJGBq
EOF
)" -- frontend/src/features/denylist/useDenylist.ts frontend/src/features/denylist/DenylistList.test.tsx
```

---

### Task 4: 테스트 부채 4건 보강 (테스트 파일만 — 앱 코드 무변경)

**Files:**
- Modify: `frontend/src/lib/jobState.test.ts`
- Modify: `frontend/src/features/batches/BatchDetail.test.tsx`
- Modify: `frontend/src/components/ui/Sparkline.test.tsx`
- Modify: `frontend/src/features/dashboard/Dashboard.test.tsx`
- Modify: `frontend/src/features/dashboard/JobStatsSection.test.tsx`

**Interfaces:** 네 항목 모두 **즉시 초록이 정상인 회귀 그물**이다(앱 코드가 이미 옳다 — 전제 재확인 4a·4c·4d·4e). RED 는 각 Step 의 뮤테이션이 담당한다. 이 태스크에서 `frontend/src` 의 비테스트 파일이 한 줄이라도 바뀌면 그건 실수다(뮤테이션 원복 누락 — Step 6 이 검증).

- [ ] **Step 1 (4a): jobState 잔여 상태 매핑**

`frontend/src/lib/jobState.test.ts` — 파일 끝에:

```ts
test("잔여 상태 매핑(슬라이스 1~4 부채): PreviewExpired=bad, Planning/Scheduled=busy", () => {
  // PreviewExpired 는 isTerminal 로만 단언돼 있었고 배지색은 무그물이었다.
  // Planning/Scheduled 는 jobState.ts:10 이 다루는데 단언이 전무했다.
  expect(pillVariant("PreviewExpired")).toBe("bad");
  expect(pillVariant("Planning")).toBe("busy");
  expect(pillVariant("Scheduled")).toBe("busy");
});
```

Run: `npx vitest run src/lib/jobState.test.ts` → 신규 포함 전부 PASS(그물 — 즉시 초록이 정상).
뮤테이션: `cp src/lib/jobState.ts /tmp/slice29-jobState.ts.bak` 후 :10 의 busy 배열에서 `"Planning"` 제거 → 신규 테스트만 RED("neutral" ≠ "busy") → `cp` 원복 → 재실행 초록.

- [ ] **Step 2 (4c): BatchDetail POST 단언을 waitFor 안으로**

`frontend/src/features/batches/BatchDetail.test.tsx` — import 에 `waitFor` 추가(`@testing-library/react`), :28 을 `await waitFor(() => expect(confirmed).toBe(true));` 로, :39 를 `await waitFor(() => expect(cancelled).toBe(true));` 로 교체. 이유 주석 한 줄: `// userEvent.click 은 fetch 착지를 보장하지 않는다 -- 단언을 waitFor 로 감싸 플레이키를 없앤다.`

Run: `npx vitest run src/features/batches/BatchDetail.test.tsx` → 전부 PASS.
뮤테이션(그물 이빨 — 앱 코드 쪽): `cp src/features/batches/BatchDetail.tsx /tmp/slice29-BatchDetail.tsx.bak` 후 :25 의 `onClick={() => confirm.mutate()}` 를 `onClick={() => {}}` 로 → confirm 테스트가 waitFor 타임아웃으로 RED(waitFor 로 감싼 뒤에도 그물이 무디지 않다는 증명) → `cp` 원복 → 재실행 초록.

- [ ] **Step 3 (4d): Sparkline NaN/Infinity**

`frontend/src/components/ui/Sparkline.test.tsx` — `describe("sparklinePath")` 끝에 1건, `describe("Sparkline")` 끝에 1건:

```tsx
  it("NaN/Infinity 는 null 과 같은 절단이다 -- 좌표 문자열에 NaN 이 새지 않는다", () => {
    // 메트릭 파이프라인이 0/0 이나 오버플로를 흘리면 path d="...NaN..." 이 되어
    // SVG 가 통째로 안 그려진다 -- Number.isFinite 필터(슬라이스 26 통합분)의 그물.
    expect(sparklinePath([0, NaN, 10], 100, 20)).toBe("M0,20M100,0");
    expect(sparklinePath([Infinity, -Infinity], 100, 20)).toBe("");
  });
```

```tsx
  it("NaN 옆의 유효점 1개도 circle 로 그린다 -- 좌표는 path 와 같은 step 공식", () => {
    // [NaN, 7]: step = 120/(2-1) = 120, 유효점 인덱스 1 -> cx 120, span 0 -> 중앙선.
    const { container } = render(<Sparkline values={[NaN, 7]} />);
    expect(container.querySelector("circle")!.getAttribute("cx")).toBe("120");
    expect(container.querySelector("circle")!.getAttribute("cy")).toBe("16");
  });
```

Run: `npx vitest run src/components/ui/Sparkline.test.tsx` → 전부 PASS.
뮤테이션: `cp src/components/ui/Sparkline.tsx /tmp/slice29-Sparkline.tsx.bak` 후 :17 의 `if (v === null || !Number.isFinite(v))` 를 `if (v === null)` 로 → NaN 절단 테스트 RED(d 에 NaN 좌표가 샌다) → `cp` 원복 → 재실행 초록.

- [ ] **Step 4 (4e): by_state 비배열 truthy — Array.isArray 와 ?? [] 의 구분 사례**

`frontend/src/features/dashboard/Dashboard.test.tsx` — 기존 ":null" 테스트(:83-87) **아래**에:

```tsx
test("잡 통계가 비배열 truthy({})로 와도 죽지 않는다 -- null 사례는 ?? [] 도 통과시킨다", async () => {
  // 기존 by_state:null 테스트는 `x ?? []` 구현도 초록으로 만들었다(null 은 nullish).
  // {} 는 Array.isArray 가드만 걸러낸다 -- 프록시/구버전 API 가 객체를 흘리는
  // 경우의 실 구분 사례다.
  renderDash({ jobs: { by_state: {} } });
  const running = (await screen.findByText("실행 중")).parentElement!;
  expect(running).toHaveTextContent("0");
});
```

`frontend/src/features/dashboard/JobStatsSection.test.tsx` — 기존 ":null" 테스트(:70-73) 아래에:

```tsx
test("by_state 가 비배열 truthy(\"oops\")여도 죽지 않는다 -- asArray 의 구분 사례", async () => {
  renderSection({ by_state: "oops" });
  expect(await screen.findByText("잡 통계")).toBeInTheDocument();
});
```

Run: `npx vitest run src/features/dashboard` → 전부 PASS.
뮤테이션: `cp src/features/dashboard/JobStatsSection.tsx /tmp/slice29-JobStatsSection.tsx.bak` 후 :10 의 `asArray` 를 `(v ?? []) as T[]` 반환으로 → JobStatsSection 신규 테스트 RED(문자열에 .filter/.map 이 없어 크래시), **기존 :null 테스트는 초록 유지** — 기존 그물이 이 뮤테이션에 눈멀었다는 실증(백로그 §2.5 문구의 재현). `cp` 원복 → 재실행 초록. (Dashboard.tsx:41 쪽은 같은 성질이라 뮤테이션 1건으로 족하다 — 신규 테스트 자체는 양쪽 다 두었다.)

- [ ] **Step 5: 전체 통과 확인**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b`
Expected: `266 passed`(261 + 5: 4a 1 + 4d 2 + 4e 2 — 4c 는 기존 테스트 수리라 수 불변), tsc 무출력.

- [ ] **Step 6: 앱 코드 무변경 검증 + 커밋**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && git status --porcelain -- frontend/src`
Expected: 변경 파일이 **테스트 5개뿐**(jobState.test.ts / BatchDetail.test.tsx / Sparkline.test.tsx / Dashboard.test.tsx / JobStatsSection.test.tsx). 비테스트 파일이 보이면 뮤테이션 원복 누락 — cp 백업으로 되돌리고 재확인.

```bash
git commit -m "$(cat <<'EOF'
test(portal): §2.5 부채 4건 보강 — jobState 잔여 상태, BatchDetail waitFor, Sparkline NaN/Infinity, by_state 비배열 truthy(앱 코드 무변경)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NxyaMVBiYCAHbZaHcYJGBq
EOF
)" -- frontend/src/lib/jobState.test.ts frontend/src/features/batches/BatchDetail.test.tsx frontend/src/components/ui/Sparkline.test.tsx frontend/src/features/dashboard/Dashboard.test.tsx frontend/src/features/dashboard/JobStatsSection.test.tsx
```

---

### Task 5: 마감 검증 — 프론트 전체 + e2e + 불변 조항 (커밋 없음)

**Files:** 없음(검증만)

- [ ] **Step 1: 프론트·e2e 전체**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b && npm run test:e2e`
Expected: vitest **266 passed**(257 + T1 1 + T2 1 + T3 2 + T4 5 — 근사가 아니라 정확치다. 어긋나면 신규 수를 재계산하되 **failed 0 이 본질**), tsc 무출력 exit 0, e2e **9 passed**(E1 강화 포함).

- [ ] **Step 2: 계약·불변 조항 확인**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && git status --porcelain && git log --oneline -5 && git diff HEAD~4 --stat -- src tests deploy legacy docs`
Expected: 작업 트리 clean(커밋 4건 + 이 플랜 파일 외 잔여물 없음), **`src/`·`tests/`·`deploy/`·`legacy/` diff 공란**(프론트 전용의 최종 증거 — 스키마·API 계약 무변경 → migrate 불요 판단이 여기서 확정된다), 커밋 4건의 대상이 정확히 12파일(T1 3 + T2 2 + T3 2 + T4 5). `reasonCodes.json` 무변경 확인: `git diff HEAD~4 -- frontend/src/lib/reasonCodes.json` 이 빈 출력.

---

## 플랜 이후: 배포·실증 (별도 ops, 플랜 태스크 밖)

플랜 실행이 끝나면 배포자가 테스트베드에서 수행한다(슬라이스 12~28 관례). **매니페스트-우선**: 태그를 먼저 bump→커밋하고 그 커밋에서 빌드한다. 프론트 dist 는 제어면 이미지에 COPY 되므로 **프론트만 바뀌어도 `dms` 이미지 재빌드가 필요하다**. **스키마·API 계약 무변경이므로 migrate Job 재실행 불요**(initContainer migrate 는 no-op). 에이전트(d35)·잡 이미지(d35) 무접촉.

**1. 태그 범프 커밋 + 빌드 + apply**

```bash
# (a) 매니페스트 범프 -- 제어면 dms d39→d40 5곳만(30-migrate-job.yaml:25 /
#     40-api.yaml:67,84 / 41-controller.yaml:35,52). 50-agent-daemonset.yaml·
#     DMS_JOB_IMAGE 는 d35 유지(무접촉).
git commit -m "deploy(k8s): 제어면 d40 (슬라이스 29 포탈 위생)" -- deploy/k8s
# main 병합·push 후 그 커밋에서(빌드 파드는 GitHub 에서 clone 한다):

# (b) 빌드 파드 -- images=["dms"], DMS_BUILD_TAG="d40". 로그에서
#     DMS_COMMIT_SHA=<범프 커밋> 과 push 성공 확인 후 빌드 파드 삭제.

# (c) 제어면 apply(20-config.yaml 무변경 -- 이번 슬라이스는 설정 키 0):
kubectl apply -f deploy/k8s/40-api.yaml -f deploy/k8s/41-controller.yaml
kubectl -n dms rollout status deploy/dms-api deploy/dms-controller
```

**2. 항목 1 실증 — 로그아웃 → /login 도달 (라이브 포탈, NodePort 30080)**

```bash
# 판단(과제 질문): curl 세션 왕복으로는 **대체 불가**다 -- 이 결함/수정의 실체는
# 클라이언트 사이드 라우팅(SPA 의 URL 전환)이고 curl 은 JS 를 실행하지 않는다.
# curl 이 증명할 수 있는 것(서버 세션 파기)은 결함 이전부터 정상이었다.
#
# (a) 1차(서버면, curl): 세션 왕복으로 d40 에서도 서버 거동 무회귀 확인 --
curl -si -c /tmp/s29.jar -X POST http://<노드IP>:30080/api/auth/login \
  -H 'Content-Type: application/json' -d '{"username":"<admin>","password":"<pw>"}' | head -1   # 200
curl -si -b /tmp/s29.jar -X POST http://<노드IP>:30080/api/auth/logout | head -1                # 200
curl -si -b /tmp/s29.jar http://<노드IP>:30080/api/auth/me | head -1                            # 401
#
# (b) 2차(클라이언트면, 헤드리스 Playwright 1회성 스크립트): frontend/ 의 기존
#     playwright 로 라이브 포탈을 연다(신규 의존성 0) -- 로그인 → /admin/dashboard
#     도달 → 로그아웃 클릭 → **URL 이 /login 으로 바뀌는 것**을 단언. 스크립트는
#     /tmp 에 두고 실행 후 삭제(저장소 무접촉). 노드IP:30080 이 개발 호스트에서
#     닿지 않으면: e2e E1(같은 dist 를 :8093 실백엔드로 검증, Task 1 Step 5 통과)
#     + (a) 를 합쳐 갈음하고 그 사실을 BACKLOG 에 정직하게 기록한다 --
#     이미지의 dist 와 e2e 의 dist 가 같은 커밋 산물이라는 것이 갈음의 근거다.
```

**3. 항목 2·3 실증 — 문구·인코딩 스모크**

```bash
# poll_failed 문구: 라이브 재현은 레지스트리/스케줄러 장애 유도가 필요해 과하다 --
# 포탈 아무 화면에서 오류 없이 로드되는 것 + vitest 문구 핀으로 갈음(문구는 vitest
# 영토 -- e2e 설계 §2.1 관례). denylist: 포탈에서 대상 추가("smoke-s29") → 목록
# 표시 → 해제 -- 인코딩 불변 문자의 무회귀를 라이브로 1회 확인(특수문자 실증은
# msw 테스트가 계약 -- 라이브 LDAP subject 에 #/? 를 실제로 등재하면 지우는
# 사람이 고생한다).
```

**4. 무회귀 스모크**

```bash
# 포탈 주요 화면(대시보드·잡·릴리스) 로드 무오류, 로그인→로그아웃→재로그인 1왕복.
# events 에 새 error 이벤트가 없는지 확인.
```

실증 통과 후 `docs/superpowers/BACKLOG.md` 갱신(슬라이스 29 완료 기록 + §2.2 로그아웃 🔴 해소·poll_failed 해소 + §2.5 슬라이스 1~4·14 잔여 중 4건 해소·PolicyDialog aria-label 항목은 **해소됨 판정**(감싸기 라벨로 접근 가능한 이름 존재)·Sparkline "무검증" 문구는 **코드 기준 정정**(슬라이스 26 이 이미 Number.isFinite — 남은 건 테스트였고 이번에 닫음))을 별도 커밋으로 — 플랜 밖 관례.

---

## Self-Review

**1. 과제 커버리지**

| 과제 항목 | 담당 |
|---|---|
| 1: 로그아웃 수정 방향 설계(3안 비교) | Architecture — (A) clear 유지+명시 nav 채택. (B) invalidate 기각: 캐시 소거 계약 파기 + "관찰자 언마운트가 유일한 루프 종결자" 그물 위에 UX 를 얹는 구조(슬라이스 26 계열 재개방 위험). (C) setQueryData 기각: "관찰자 마운트 중 clear" — 버그를 만든 바로 그 수명주기 층에 의존 추가 |
| 1: 무한 루프 방지의 테스트 증명 | Task 1 Step 1 — me 호출 횟수 불변 단언(정착 창 150ms 포함). 구조 근거: /login 은 쿼리 관찰자 0(실측) → dms:unauthorized 발화 재료 없음 |
| 1: e2e 무영향 + 계약 추가 고정 판단 | 실측: E1 은 세션 파기만 단언(주석이 명시) → 수정 자체는 무영향. **고정한다** — E1 에 URL 단언 1줄 추가(테스트 수 9 불변), 버그 박제 주석을 계약 주석으로 교체(Task 1 Step 5) |
| 2: poll_failed 일반화 + 계약 테스트 영향 확인 | Task 2 — 실측: 문구는 api.ts:129 한 곳, 계약 테스트는 키만 검사 → 안전. reasonCodes.json 무접촉. 핀 테스트가 "빌드" 재유입을 막는다 |
| 3: useDenylist 인코딩 | Task 3 — #/? 가 유일한 구분 재료라는 실측 위에 RED 2건. `/` 한계는 정직하게 기록(열린 질문 1) |
| 4: 테스트 부채 실측 선별 | 유효 4건(4a·4c·4d·4e) → Task 4. **제외**: PolicyDialog aria-label(감싸기 라벨로 접근명 존재 — RED 불가 = 결함 부재). 4d 는 "코드는 이미 옳고 테스트만 부재"로 등급 정정 |
| 범위 판단(부풀리기 금지) | 앱 코드 변경은 정확히 3파일(AppShell·api.ts 문구 1줄·useDenylist 2줄) — 나머지는 전부 테스트. 백엔드·deploy·JSON 무접촉을 Task 5 Step 2 가 diff 로 강제 |
| 배포·실증(d39→d40·migrate 불요 확인·curl 대체 판단) | 「플랜 이후」 — dist COPY 때문에 프론트만 바뀌어도 재빌드 필요 명시. migrate 불요는 Task 5 의 src/ diff 공란이 확정. curl 대체 **불가** 판단(SPA 라우팅은 JS) + 헤드리스 Playwright 1회성 스크립트/갈음 규칙 |

**2. 뮤테이션(이빨) 매트릭스** — T1: AppShell nav 제거(수정 전 상태의 정확한 모형) → 신규 테스트만 RED, 기존 라우팅 테스트 초록(기존 그물의 맹점 실증). T2: 문구를 "빌드…"로 원상 → 핀 테스트만 RED. T3: useAllow 쪽 인코딩만 제거 → DELETE 테스트만 RED(두 mutationFn 각자 그물). T4: 4a jobState busy 에서 Planning 제거 / 4c BatchDetail confirm onClick 무력화 / 4d Number.isFinite 절단 제거 / 4e asArray 를 `?? []` 로 — 각각 해당 신규 테스트만 RED, 특히 4e 는 **기존 :null 테스트가 초록으로 남는 것**이 백로그 지적("구분 못함")의 재현이다. 전부 cp 백업/원복(git checkout 금지).

**3. 타입·이름 일관성** — 로그아웃 경로 문자열 `"/login"` 은 router.tsx 의 Route path·RequireRole Navigate 와 동일 철자. 테스트 셀렉터 `{ name: "로그아웃" }`/`{ name: "로그인" }` 은 AppShell:38·Login:38 의 실 버튼 텍스트와 동일(e2e E1 도 같은 셀렉터 기사용). poll_failed 새 문구는 Task 2 의 api.ts 와 핀 테스트 동일 철자. denylist msw 파라미터 `:type/:subject` 와 단언값 `grp#1`·`grp%231`·`x?y` 는 Step 1/3 동일 철자. 신규 테스트 이름 7건은 각 Step 과 뮤테이션 절 동일 지칭. 픽스처·헬퍼(renderAt·wrap·renderDash·renderSection·ENTRIES)는 기존 파일 것 재사용(신설 0).

**알려진 위험 / 판단:**
- **routes(경로) 하드코딩** — nav("/login") 은 router.tsx 의 리터럴과 중복이다(상수화 후보). 기존 코드도 RequireRole·Navigate 가 같은 리터럴을 쓰므로 이 슬라이스에서 새 규약을 만들지 않는다(위생 슬라이스가 컨벤션 신설로 부풀지 않게).
- **정착 창 150ms 단언** — "루프 없음"의 완전 증명은 불가능하고(무한을 관찰할 수 없다) 150ms 는 msw 왕복 수 회 분량의 실용 창이다. 구조 근거(/login 관찰자 0)가 본질이고 테스트는 그 회귀 그물이다.
- **로그아웃 POST 실패 시에도 /login 이동(onSettled)** — 서버 세션이 살아남는 희귀 케이스에서 /login 이 "거짓 로그아웃"처럼 보일 수 있으나, 대안(오류 배너+잔류)은 캐시가 이미 빈 동결 화면이라 더 나쁘다. 다음 로그인/하드 내비게이션이 진실을 복원한다 — 열린 질문 2 로 기록.
- **e2e E1 강화의 플레이크 면** — toHaveURL 은 Playwright 자동 재시도 단언이라 SPA 전환 지연에 안전하다. E1 이 이미 URL 정규식 단언을 2회 쓰고 있어 새 패턴이 아니다.
- **`{}`·`"oops"` 를 API 타입에 안 맞게 주입** — msw 는 타입을 강제하지 않고(HttpResponse.json(unknown)), 테스트의 목적 자체가 "타입 밖 응답 생존"이다. tsc 는 테스트 파일의 overrides 인자(`Record<string, unknown>`/`unknown`)로 이미 허용(기존 :null 테스트와 같은 통로).

## 결정이 필요한 열린 질문

1. **subject 에 `/` 가 든 대상은 인코딩해도 404 다** — ASGI 서버가 라우팅 전에 %2F 를 디코드해 경로 세그먼트가 갈라진다(백엔드 `{subject}` 는 세그먼트 매치). wrong-target 이 "깨끗한 실패"로 바뀌는 것까지가 이번 범위. 근본 해결은 백엔드 경로 재설계(body 기반 또는 `:path` 컨버터)라 범위 밖 — LDAP uid/그룹명에 슬래시가 실재하는지 운영 확인 후 판단.
2. **로그아웃 POST 실패 시 /login 이동이 최선인가** — onSettled(무조건 이동) vs onSuccess(성공만 이동 + 실패 배너). 전자를 채택했으나 운영에서 "로그아웃 눌렀는데 세션이 살아 있었다" 불만이 나오면 재고.
3. **poll_failed 의 문맥별 문구 분화** — "상태를 확인하지 못했습니다"는 중립이지만 빌드/잡 로그 어느 쪽도 특정하지 않는다. 문맥별 문구는 사유 코드 분화(poll_failed vs log_poll_failed)가 필요해 "사유 코드 신설 0" 방침 재검토 사안 — 별도 결정.
4. **/login 의 관찰자-0 전제는 미래에 깨질 수 있다** — 누군가 Login 에 useMe(자동 리다이렉트용)를 넣으면 루프 계열이 재개방된다. Task 1 의 me 횟수 단언이 로그아웃 경로는 지키지만 /login 진입 일반은 안 지킨다. "로그인 화면은 쿼리를 관찰하지 않는다"를 컨벤션으로 박을지(주석 vs 테스트) 다음 위생 슬라이스에서.
5. **PolicyDialog tool 필드의 aria-label 속성 통일** — 접근명은 이미 있으나(감싸기 라벨) 파일 내 다른 7개 필드는 명시 aria-label 이라 스타일이 갈린다. 기능 결함이 아니므로 제외했다 — 통일하려면 1줄이고, 그때 테스트는 여전히 불필요하다(거동 무변경).
