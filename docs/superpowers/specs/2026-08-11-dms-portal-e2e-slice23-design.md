# 슬라이스 23 — 포탈 e2e 테스트 설계

슬라이스 18~21 에서 포탈 결함 2건이 **라이브에서만** 드러났다: 계정 표 뭉개짐
(td 를 flex 컨테이너로 쓴 것, 수정 9fbef86)과 사이드바 밀림(AppShell flex 자식의
min-w-0 부재, 수정 6bc2ecb). 그동안 단위 테스트 228건은 전부 초록이었다. 이
슬라이스는 **단위 테스트가 구조적으로 못 보는 것만** 잡는 실 브라우저 e2e 최소
세트를 만든다. 15슬라이스째 e2e 0건(BACKLOG.md:449)의 종결이다.

## 1. 실측으로 확인한 전제

1. **두 결함의 실체**: 계정 표는 td 자체가 flex 라 표 레이아웃 계산에서 빠졌었고
   (수정 후 셀 안 div 만 flex — `AccountsList.tsx` 현재), 사이드바는 flex 자식
   기본 `min-width:auto` 로 넓은 표가 레이아웃을 밀어냈다(수정: `md:shrink-0`
   `AppShell.tsx:13`, `flex-1 min-w-0` `:35`). 표 래퍼는 `overflow-x-auto`
   (`components/ui/Table.tsx:3`). 두 수정 커밋 모두 "라이브에서 관측"을 명시했고
   기존 단언은 무수정 통과했다 — 문구만 보는 테스트는 못 잡는 결함이었다.
2. **단위 테스트에는 기하를 볼 수단 자체가 없다.** jsdom 은 레이아웃 엔진이 없고,
   Tailwind CSS 는 `main.tsx:7` 의 index.css import 로만 로드된다 — 테스트 셋업
   (`src/test/setup.ts:1-4`)은 jest-dom+cleanup 뿐이라 클래스는 그냥 문자열이다.
   `frontend/src` 전체에 getBoundingClientRect/scrollWidth/offsetWidth 단언 0건
   (grep 실측), `toHaveClass` 1건(`NodesList.test.tsx:70`)도 기하가 아니다.
3. **e2e·CI 둘 다 0 이다.** package.json 에 playwright 계열 없음, 저장소 루트에
   `.github`/`.gitlab-ci.yml` 없음(실측). `.gitignore:13-14` 의 `.playwright-*` 는
   세션 도구 찌꺼기 무시 항목이지 저장소 의존성이 아니다.
4. **vitest 설정에 include 가 없다**(`vite.config.ts:8-12`) — vitest 기본 include
   는 `**/*.{test,spec}.*` 라 e2e 스펙 파일을 아무 데나 두면 vitest 가 집어
   터진다. 현 49개 테스트 파일은 전부 `src/` 아래 `*.test.*`(`*.spec.*` 0건,
   find 실측). tsc 는 `include:["src"]`(tsconfig.json)라 `e2e/` 를 안 본다.
5. **풀스택이 클러스터 없이 돈다**: `execution_backend` 기본 "stub"
   (`config.py:125,184`), `StubExecutionAdapter.poll` 은 미지 ref 에 SUCCEEDED
   (`execution.py:67-73`)라 프로세스가 갈라져도(API·컨트롤러 별개) 잡이 진행된다.
   sqlite 지원(`db.py:38-44`; timeout 미지정 → 파이썬 기본 busy 5s). 필수 env 는
   4종뿐(`config.py:146-147`).
6. **API 가 dist 를 직접 서빙하고 SPA fallback 을 가진다**(`api/app.py:81-96`) —
   배포도 같은 경로다(`deploy/k8s/40-api.yaml:91` DMS_STATIC_DIR). vite dev
   서버는 자체 fallback 이 있어 이 코드를 **가린다** — dev 서버 위 e2e 는
   spa_fallback 회귀를 영원히 못 잡는다.
7. **세션은 서명 쿠키**(dms_session, `app.py:44-45`), 로그인은 DB 계정 검증
   (`routes_auth.py:33-40`), 관리자 부트스트랩은 POST /api/admin/accounts +
   x-admin-token(`routes_auth.py:56-57`). msw 단위 테스트는 이 쿠키 왕복을 한
   번도 실제로 돈 적이 없다.
8. **LDAP 없이 잡을 종단까지 미는 길이 있다**: LDAP env 미설정이면 resolver 는
   None(`identity_ldap.py:44-46`) → 일반 잡은 `ldap_not_configured` Rejected
   (`identity.py:77-78`). 그러나 session 인증 + 요청자 ∈ 기본
   privileged_requesters {root, admin}(`config.py` from_env 기본값) 이면 uid 0
   특권 통과(`identity.py:60-76`)고, `eligible_nodes` 도 privileged 는 노드 신원
   검사를 생략한다(`placement.py:51-52`).
9. **후보 노드가 되려면 에이전트 리포트가 필요하다**(planner 5단계,
   `planner.py:166-180`): 신선 리포트에서 mount status Ready — **마운트포인트
   필수**(`agent/probes.py:36-38` not_a_mountpoint) — 와 tool Ready(which 로
   발견되면 Ready, 버전 실패는 fail-soft, `probes.py:52-69`). `dms agent --once`
   는 2사이클(부트스트랩 수신 → 실프로브, `agent/runner.py:104-107`)이라 1회
   호출로 스토리지 프로브까지 간다. 스토리지 `{mount:"/", root:"/"}` 는 검증을
   통과하고("/" 특례 + root==mount, `repositories/storages.py:16-22`) "/" 는
   항상 마운트포인트다. 이메일은 무검증(`accounts.py:32-44`) — 긴 시드 가능.
10. **컨트롤러 `--once` 반복은 함정이다**: holder=`controller-<pid>`(`cli.py:59`),
    lease 는 max(interval×3, 30s)(`controller.py:100-107`) — 새 pid 로 30초 안에
    재실행하면 전 루프가 skipped_lease 로 **조용히 아무것도 안 한다**. 대신
    interval env(`config.py:11-31`)를 1초로 준 장수 프로세스 하나를 돌려야 한다.
11. **프론트 폴링**: 잡 목록 3s + 종단 시 중지 콜백(`useJobs.ts:8,17`), 빌드 상세
    3s 종단 중지(`useBuilds.ts:71`), 대시보드 5s(`useMetrics.ts:19,26,33`).
    RequireRole 미인증 → /login(`RequireRole.tsx:6`), 미지 경로 → /
    (`router.tsx:70`). 로그인 입력은 aria-label 보유(`Login.tsx:23,27`) —
    testid 추가 없이 role/label 셀렉터로 충분하다.
12. **실행기 재료(dev 머신 실측)**: `/usr/bin/google-chrome` 존재, node v25.9.0 —
    Playwright `channel:"chrome"` 이 브라우저 바이너리 다운로드 없이 돈다.

## 2. 핵심 결정

### 2.1 e2e 가 잡는 것 — 4범주, 그 밖은 전부 vitest 몫

① 기하(레이아웃 붕괴 — §1-1/2 의 사각지대) ② 실 HTTP 왕복(세션 쿠키·SPA
fallback — §1-6/7 의 사각지대) ③ 폴링 수렴(실 네트워크로 화면이 따라오는가)
④ 풀스택 부팅(migrate→api→controller→agent 가 빈 DB 에서 뜨는가 — 지금은
테스트베드 수기 실증만이 본다). **역할 분담 규칙**: e2e 는 문구·사유 코드·분기
렌더를 단언하지 않는다 — 그건 vitest+msw 228건의 영토고, 옮기면 느리고
플레이키해질 뿐이다. e2e 단언 재료는 URL·역할(landmark)·기하·행 수로 제한한다.
시나리오는 6개 상한(§5) — 추가하려면 "단위가 구조적으로 못 잡는가"를 먼저 심사.

### 2.2 실행 환경 — 실 스택 로컬, 클러스터 불요, 수기 실행

tmp sqlite + `dms migrate` + `dms api`(빌드된 dist 서빙) + `dms controller`
(1초 틱, §1-10) + `dms agent --once`(§1-9). msw 브라우저 모드를 쓰지 않는 이유:
결함 2건은 CSS×실브라우저 문제라 서버 모킹과 무관하고, 이 저장소는 stub 백엔드
덕에 **실 API 가 공짜**다(§1-5) — 모킹은 프론트-백 계약 표류를 영원히 못 잡는다.
vite dev 서버 대신 dist 서빙인 이유는 §1-6 — spa_fallback 이 검사 대상에
들어온다. **CI 는 없다(§1-3), 그래서 수기 실행이 사실이다**: `npm run test:e2e`
한 방이고, deploy/README 배포 절차의 "이미지 빌드 전" 단계로 명문화한다(매니페
스트-우선 배포의 게이트 하나 추가). CI 구축 자체는 §7 로 미룬다 — 숨기지 않고.

### 2.3 도구 — `@playwright/test` devDependency 1건 신설 (금지 원칙의 예외 신청)

새 의존성 금지 원칙의 예외를 신청한다. 이유: 기하 단언에는 실 렌더링 엔진이
필수인데(§1-2) 저장소의 어떤 기존 의존성도 브라우저를 못 띄운다. 대안 검토 —
**vitest browser mode**: provider 로 playwright/webdriverio 를 어차피 요구해
의존성 절감이 없고, 컴포넌트 하네스 지향이라 다중 프로세스(webServer) 오케스트
레이션이 없다 → 기각. **원시 CDP 스크립트**: 러너·단언·트레이스 재발명 → 기각.
**스크린샷 픽셀 비교**: 폰트 렌더링 머신 편차로 플레이키하고, 기준 이미지 갱신이
"실패를 기준으로 승격"하는 관례를 만들며, 깨져도 무엇이 깨졌는지 말하지 않는다
→ 기각 — §2.4 의 기하 불변식이 결정적이고 자기설명적이다. 브라우저는
`channel:"chrome"`(§1-12)로 다운로드 0. 시스템 크롬 버전이 흐른다는 한계는 수기
스모크 수준에서 수용하고, 부재 시 `npx playwright install chromium` 폴백을
README 에 적는다.

### 2.4 레이아웃 붕괴를 잡는 불변식 4종 — 공용 헬퍼 `assertLayoutSane(page)`

두 결함을 **실제로 잡았을** 검사만 넣는다:

- **L1 문서 가로 오버플로 금지**: `documentElement.scrollWidth ≤ clientWidth+1`.
  넓은 표가 min-width:auto 사슬로 페이지를 넓히는 형태(결함 B 의 밀림)를 잡는다.
- **L2 표 셀 display 불변식**: 모든 td/th 의 computed display == `table-cell`.
  결함 A 의 구조 원인(td 가 flex 컨테이너로 이탈)을 직격한다 — 기하 이전의
  DOM 불변식이라 플레이크 0.
- **L3 사이드바 폭 고정**: ≥768px 뷰포트에서 aside 폭 == 240px(15rem,
  `AppShell.tsx:13`). 결함 B 의 쪼그라듦 형태를 직격한다.
- **L4 한 줄 요소**: nav 링크와 표 안 버튼의 높이 < 2×line-height. 두 결함의
  공통 증상(버튼 글자 세로 쪼개짐, 메뉴 줄바꿈)을 증상 층위에서 잡는 그물이다.

**전제 단언이 검사의 반이다**: 이 불변식들은 표가 실제로 컨테이너보다 넓을 때만
의미가 있다. 검사 대상 페이지에서 표 래퍼가 정말 넘치는지(`wrapper.scrollWidth >
clientWidth`)를 먼저 단언한다 — 시드가 좁아져 불변식이 공갈이 되는 순간을 검사
자체가 시끄럽게 알린다(조용한 실패 금지). 시드: ~120자 이메일 계정 3건(§1-9,
무검증) 등 넓은 데이터를 하네스가 심는다.

### 2.5 하네스 구조 — 파일 배치·격리·시드

- `frontend/e2e/*.spec.ts` + `frontend/playwright.config.ts`. vitest 에
  `include: ["src/**/*.test.{ts,tsx}"]` 를 **명시**한다(§1-4 함정 잠금 — 현
  49파일 전부 매치라 228 유지). tsc 는 이미 e2e/ 를 안 본다(§1-4).
- `workers:1`, `fullyParallel:false` — 단일 sqlite 상태 공유라 병렬 금지.
  `retries:0` — 플레이크를 재시도로 숨기지 않는다. `trace:"retain-on-failure"`,
  산출물 디렉터리는 .gitignore 추가.
- globalSetup: tmpdir DB → `dms migrate` → `dms api`(고정 포트 8093,
  DMS_STATIC_DIR=dist) /readyz 폴링 → `dms controller`(interval env 전부 1s,
  §1-10) → 시드(admin 부트스트랩 토큰 §1-7, 스토리지 `{mount:"/", root:"/"}`,
  긴 이메일 계정들) → `dms agent --once`(PATH 앞에 `e2e/fixtures/bin` — dscan·
  dsync·nsync·drm 4개 가짜 실행파일, §1-9: which 발견이면 Ready). 가짜 도구는
  배선 검증용임을 파일 머리 주석에 명시한다 — 도구 실측은 테스트베드 몫.
- globalTeardown: 자식 프로세스 종료 + tmpdir 제거. `npm run test:e2e` =
  `npm run build && playwright test`(dist 최신화가 전제라 명시 체인).

## 3. 화면

앱 코드 변경 0 이 목표다 — 셀렉터용 data-testid 도 달지 않는다(role/label 로
충분, §1-11). e2e 가 순회·검사하는 화면: 로그인 → 관리자 홈 리다이렉트
(/admin/dashboard) → 계정(넓은 표 스트레스의 본진, 결함 A/B 의 현장) → 내
작업/상세(폴링) → 스토리지 → 빌드 목록. 각 화면 진입·안정화 후
`assertLayoutSane` 을 일괄 적용한다(§2.4). 뷰포트는 1280×800(주) + 375×667
(모바일 spot check — md: 분기 아래에서 L3 는 건너뛰고 L1/L2/L4 만).

## 4. 오류 처리

- /readyz 30초 폴링 실패·`dms` CLI 부재·agent --once 비0 종료 → 하네스가
  **즉시 시끄럽게 실패**한다. 조건 미충족 시 skip 처리를 금지한다 — 스킵된
  e2e 는 0건 e2e 와 같다.
- 컨트롤러 자식이 도중 죽으면: teardown 이 종료 전 생존을 확인하고, 이미 죽어
  있으면 러너를 실패로 승격한다 — 잡이 이미 끝난 뒤 죽은 경우 초록으로 새는
  것을 막는다(부팅 스모크 주장 유지).
- 시나리오 0건이면 Playwright 가 "no tests found" 로 비0 종료한다 — "e2e 를
  지웠는데 초록" 방지.
- §2.4 전제 단언 실패는 불변식 위반과 구분되는 고유 메시지를 낸다 — "시드가
  좁다"와 "레이아웃이 깨졌다"를 뭉개지 않는다.
- sqlite 경합: API 폴링×컨트롤러 틱은 파이썬 기본 busy 5s(§1-5) 안에서 풀린다.
  그래도 새는 "database is locked" 500 은 시나리오를 빨갛게 만든다 — 재시도로
  덮지 않는다(retries:0 인 이유이기도 하다).

## 5. 테스트

- **E1 부팅+세션**: 미인증 /admin/accounts → /login 리다이렉트(§1-11) → UI
  로그인 → 관리자 홈 /admin/dashboard 도착 → 로그아웃 → 보호 라우트 재차단.
  실쿠키 왕복 — msw 가 못 본 것(§1-7).
- **E2 SPA fallback 딥링크**: 로그인 후 `page.goto("/admin/accounts")` 하드
  내비게이션 — `spa_fallback`(§1-6)이 index.html 을 주고 클라이언트 라우터가
  화면을 복원. 미지 경로는 / 로(§1-11).
- **E3 레이아웃 불변식**: 넓은 시드로 §3 화면 순회 × 2 뷰포트 — 전제 단언 +
  L1~L4. 두 결함의 회귀 방어 본체.
- **E4 잡 종단 흐름**: admin 으로 scan 을 UI 폼 제출 → 202 → 목록 상태가
  **새로고침 없이** SUCCEEDED 까지(planner→stepper 실틱, 특권 경로 §1-8,
  후보 노드 §1-9). 상한 30s.
- **E5 폴링 수렴**: 목록을 연 채 Playwright request(세션 쿠키 공유)로 두 번째
  요청 제출 → 3s 폴링(§1-11)이 새 행을 리로드 없이 표시. 상한 10s.
- **E6 폴링 중지**: 종단 표시 후 8s 창에서 해당 목록 엔드포인트 요청 0건
  (`page.on("request")` URL 필터 카운트) — 종단 중지 콜백(§1-11)의 실증.
  0 이 정상값인 단언이다 — 창·필터를 좁혀 다른 폴링과 섞지 않는다.
- 기존 스위트: vitest include 명시(§2.5) 후 프론트 228/49 유지, 백엔드 1131
  무접촉 — e2e 는 기존 테스트를 한 건도 옮기지 않는다.

## 6. 실증 (테스트베드)

이 슬라이스의 실증은 "검사가 그 결함을 정말 잡는가"다 — 로컬 결함 재주입이 본체.

1. **결함 A 재주입**: 9fbef86 의 AccountsList 수정을 로컬 revert 한 빌드에서
   E3 가 정확히 L2(와 L4)에서 빨강 — "그때 있었으면 잡았다"의 직접 증명. 핵심.
2. **결함 B 재주입**: 6bc2ecb 의 AppShell 두 클래스 제거 → E3 가 L1/L3/L4 에서
   빨강. 어느 불변식이 먼저 무는지 기록한다.
3. 클린 런 전체 초록 + 총 소요시간 실측(빌드 포함/제외 각각) — "수기 게이트로
   감당 가능한가"의 숫자를 남긴다.
4. 연속 20회 실행 플레이크 0. 1건이라도 플레이키면 그 시나리오를 고치거나
   제거하고 사유를 기록한다 — 플레이키 e2e 를 남겨 두는 것이 최악의 결말이다.
5. 라이브 테스트베드 포탈에서 L1~L4 를 콘솔 스니펫으로 1회 수동 실행 — 현
   라이브가 초록임을 확인(불변식의 실세계 유효성 검증, 자동화 대상은 아니다).

## 7. 이 슬라이스에서 하지 않는 것

- **CI 파이프라인 구축** — 저장소에 CI 자체가 없다(§1-3). e2e 는 배포 전 수기
  게이트로 시작하고, CI 는 러너 상주·브라우저 캐시·시크릿 관리가 얽힌 별도
  슬라이스 감이다. "CI 에서 돈다"고 주장하지 않는다.
- 스크린샷/픽셀 회귀(§2.3 에서 기각·사유 명시), webkit/firefox 크로스브라우저,
  모바일 심층(뷰포트 spot check 까지만).
- 실 클러스터(volcano)·LDAP 대상 e2e — 시점·환경 민감이라 테스트베드 수기
  실증의 영토로 유지한다. e2e 의 특권 경로 우회(§1-8)가 그 경계 표식이다.
- 기존 단위 테스트의 e2e 이관 — §2.1 의 "문구 단언 금지" 규칙이 경계다.
- 시각 디자인 단언(색·폰트·간격), 성능 측정, 접근성 감사(L4 이상의 a11y).
- 시나리오 확장 — 6개 상한 유지. 추가는 §2.1 심사를 통과한 것만, 별도 슬라이스.
