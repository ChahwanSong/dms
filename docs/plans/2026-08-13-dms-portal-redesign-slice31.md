# 슬라이스 31 — DS Cloud 디자인 언어로 포탈 개편 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **문서 관례:** `docs/plans/` 는 이 슬라이스에서 신설한다. 플랜 파일은 실행이 끝나면
> 삭제해도 된다 — git 이력이 보존한다. 현재 동작의 진실은 코드가 말한다(CLAUDE.md 드리프트 규칙).

**Goal:** DMS 포탈을 사내 DS Cloud 포탈의 디자인 언어(흰 톱바+네이비 브랜드 블록,
보더 중심 구획, 파랑 액센트 #1a56db, 위저드 스테퍼, 하단 액션 바)로 개편한다.
포탈 제목은 **"AI Storage Portal"**, 사이드바는 최상위 3메뉴(DMS / NAS / Monitoring),
현 21화면 전부 DMS 하위 4그룹(작업/스토리지/운영/관리)으로 재배치한다. NAS·Monitoring
은 "준비 중" 자리만 판다. SubmitJob 은 재사용 가능한 위저드 프레임(Stepper + 스텝
상태 + BottomActionBar) 위에 4스텝으로 개편한다. **유연성이 1급 요구사항**이다:
메뉴는 데이터 배열, 색·질감은 전부 tailwind 토큰, 위저드는 SubmitJob 비종속 프레임.
새 의존성은 승인된 2건만(`@fontsource/noto-sans-kr`, `lucide-react`). 백엔드·
reasonCodes 무접촉, 라우트 URL 전부 유지, e2e 사이드바 240px 유지.

**Architecture:** 전 화면이 이미 hex 0 으로 tailwind 토큰만 쓴다(실측) — 팔레트 스왑
(Task 1)만으로 전면 리스킨이 되고, 화면 파일은 토큰 값 변화를 그대로 받는다.
`rounded-lg` 가 버튼·인풋 전부에 쓰이므로 theme `borderRadius.lg` 오버라이드(8px→6px)
로 화면 무접촉 질감 조정이 가능하다. 셸(Task 2)은 TopBar 를 전역 상단으로 올리고
사이드바를 `src/app/navigation.ts` 데이터 배열에서 렌더한다 — 항목 추가·이동이 배열
한 줄이다. 브레드크럼도 같은 배열 + 상세 라우트 메타에서 파생한다(단일 소스).
위저드(Task 4)는 `components/wizard/` 프레임(Stepper·BottomActionBar 조립 + 스텝
전이)과 SubmitJob 의 도메인 로직(검증·바디 조립 — **전부 기존 코드 이식, 무변경**)을
분리해 배치 생성 등이 나중에 프레임만 얹을 수 있게 한다. 기하 계약(e2e L1~L4)은
전부 보존: aside 240px(`md:w-60 md:shrink-0`), `min-w-0` 오버플로 배선, td 는
table-cell, 사이드바 링크는 `leading-6` 으로 한 줄 계약(아래 L4 산수 참조).

**Tech Stack:** React 18 + tailwind 3 + vite 5(기존). 신규는 승인 2건만:
`@fontsource/noto-sans-kr`(셀프호스팅, weight 400/500/700 만 import — CDN 금지),
`lucide-react`(트리셰이킹되는 라인 아이콘). 그 외 새 의존성 금지.
`@radix-ui/react-select` 는 **사용처 0 실측** — 이번에도 도입하지 않는다(Select 는
기존 native `<select>` + `field` 클래스 패턴 유지; 의존성 제거는 범위 밖, 기록만).

## Global Constraints

- **범위 격리**: `frontend/` 와 이 플랜 파일만 수정한다. 백엔드(`src/`, `tests/`)·
  `frontend/src/lib/reasonCodes.json`·`api.ts` REASON_MESSAGES **무접촉**.
  `deploy/k8s` 태그 bump 는 「플랜 이후: 배포·실증」의 첫 단계다(플랜 태스크에서 금지).
  `legacy/` 읽기 전용.
- **라우트 URL 전부 유지**(e2e 가 URL 단언). 신규 라우트(/nas, /monitoring)는 추가만.
- **화면 콘텐츠 텍스트·aria-label·role 불변** — 셸·스타일만 바꾼다. 예외는 SubmitJob
  (위저드화 — 그 화면 테스트는 함께 개편하되 **제출 바디 `toEqual` 단언은 원문 보존**).
- **기하 계약**: 사이드바 240px(`SIDEBAR_WIDTH_PX` 무접촉이 목표 — 바꾸면 같은 커밋에
  상수 갱신), td 에 flex 금지, `aside a` 는 한 줄(L4 산수는 Task 2), 새 셸 마크업에
  `overflow-x-auto` 클래스 금지(아래 전제 재확인 #3), 셸에 h1 추가 금지(#4).
- **클래스명 계약**: `text-ok`/`text-busy`/`text-bad`/`text-accent` 등 클래스**명**은
  유지하고 tailwind **값**만 바꾼다(vitest 가 클래스명 단언). "정상=초록" 의미 체계
  유지 — 액센트만 파랑.
- **hex 금지**: 화면 파일(`src/**/*.tsx`)에 raw hex 를 새로 넣지 않는다. 색은 전부
  `tailwind.config.ts` 토큰 경유(Task 6 grep 게이트).
- **커밋은 pathspec**: 항상 `git commit -m "..." -- <경로들>`. `git add -A`·`git add .`·
  `git commit -a` 금지(워크트리 공유 인덱스 사고 — CLAUDE.md). **신규 파일만**
  `git add <파일>` 선행. 커밋 트레일러는 실행 세션의 하네스 지시를 따른다(이 플랜
  작성 세션 기준: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` +
  `Claude-Session: https://claude.ai/code/session_01NxyaMVBiYCAHbZaHcYJGBq`).
- **뮤테이션 원복에 `git checkout` 금지** — `cp <파일> /tmp/slice31-<이름>.bak` 사본을
  뜨고 `cp` 로 되돌린다.
- **origin push 금지, 브랜치 변경 금지**(현재 `worktree-dms-slice22plus`).
- 프론트 명령(전부 `frontend/` 에서): `npx vitest run`(기준선 **266 passed / 49 files**,
  ~7s 실측) · `npx tsc -b` · `npm run test:e2e`(기준선 **9 passed**, ~25s+빌드 —
  CI 없음, 수기 게이트. 시스템 크롬·dist 서빙 하네스·workers 1).
- 주석은 한국어로 「왜」를 적는다.

## 전제 재확인 (2026-08-13, 코드 직접 실측)

과제가 준 제약 목록 외에 실측으로 추가 발견한 계약·함정. **플랜과 실측이 충돌하면
실측이 이긴다.**

| # | 실측 | 귀결 |
|---|---|---|
| 1 | **로그아웃 버튼 접근성 이름 "로그아웃"** 을 e2e 01(`getByRole("button", { name: "로그아웃" })`)과 `router.test.tsx:230` 둘 다 단언. onSettled → `nav("/login")` 전환 계약도 router.test:205 가 단언 | TopBar 로 옮겨도 이름·동작·주석 원문 보존. 스크린샷의 "Logout" 영문 표기는 열린 질문(기본: 한국어 유지) |
| 2 | **`StoragePicker`·`field` 를 `SubmitScan.tsx:5`·`ScanPaths.tsx:6` 이 SubmitJob 에서 import** | 위저드화 전에 `features/jobs/formFields.tsx` 로 이사(Task 3). 임포터 2곳은 import 줄만 변경 — 화면 콘텐츠 무접촉이라 기존 테스트 그대로 초록 |
| 3 | **`assertTableOverflows` 는 문서의 첫 `.overflow-x-auto` 를 잰다**(`layout.ts:190` `.first()`) | 새 셸(TopBar·사이드바·브레드크럼)에 `overflow-x-auto` 클래스를 절대 넣지 않는다 — 첫 매칭이 표 래퍼가 아니게 되는 순간 전제 단언이 엉뚱한 걸 재고 영원히 빨갛다 |
| 4 | **e2e `visit()` 는 `heading level: 1`** 을 단언(03-layout). 화면들이 h1 을 소유하고 셸엔 h1 이 없다 | 셸(브랜드 블록·브레드크럼·페이지 타이틀)에 h1 을 추가하지 않는다 — 이중 h1 은 `level:1` name 매칭을 흐린다. 브랜드는 div, 브레드크럼은 nav |
| 5 | **L4 산수**: `aside a` 높이 < 2×line-height. 현행 text-sm 은 line-height 20px → 링크 높이 < 40px 이어야 한다. DS 스펙의 항목 높이 ~44px 는 **이대로면 위반** | 사이드바 링크에 `leading-6`(24px) 명시 → 한계 48px. `py-2.5`(10px×2) + 24px = 44px 로 DS 높이와 L4 를 동시에 만족. 아이콘은 16px 인라인이라 높이에 무영향 |
| 6 | **h1 23개가 전부 `text-lg font-semibold`(19) 또는 `text-lg font-semibold mb-4`(4) 로 균일**(실측 grep). CSS 전역 규칙은 클래스 특이도에 진다 | 페이지 타이틀 격상(~28px bold)은 전역 CSS 로 불가 — Task 5 에서 grep 기반 기계적 클래스 스왑(스타일만, 텍스트·role 불변) |
| 7 | **raw hex 화면 파일 0건**(실측) — 전 화면이 토큰 경유 | 팔레트 스왑만으로 전면 리스킨. 단 인풋 보더는 `border-black/10` 인라인이 산재 — `field` 클래스(한 곳)와 토큰 보더는 잡되, 화면 산재분은 다음 반복 |
| 8 | **`@radix-ui/react-select` 의존성은 있으나 사용처 0**(grep 실측) | 도입하지 않는다. BACKLOG 에 의존성 정리 후보로 기록(이 슬라이스 범위 밖) |
| 9 | **`package-lock.json` 존재, Dockerfile.dms 가 `npm ci`** | 의존성 추가 시 lockfile 을 같은 커밋에 포함해야 이미지 빌드가 같은 트리를 본다 |
| 10 | **메모리의 "install/docker/Dockerfile.testbed" 지침은 이 저장소에 해당 없음** — `install/docker/` 부재, `deploy/docker/Dockerfile.dms` 가 kubectl v1.34.6 을 직접 설치(실측) | 배포 절은 `deploy/docker/Dockerfile.dms` (context = repo root) |
| 11 | vitest include 는 `src/**/*.test.{ts,tsx}` — 신규 테스트는 src 아래면 자동 포함. e2e 는 5 스펙 파일 9 테스트 | 신규 테스트 파일 배치 자유. e2e 파일 수 증설 불요 |
| 12 | Login 의 h1 "DMS 로그인", index.html `<title>DMS</title>` 은 **어느 테스트도 단언하지 않는다**(grep 실측) | 브랜딩 갱신 가능 — 단 콘텐츠 텍스트 규율의 예외라 열린 질문으로 사용자 확인 |

## 파일 구조

| 파일 | 책임 |
|---|---|
| `frontend/package.json`·`package-lock.json` (수정) | Task 1: 승인 의존성 2건 추가 |
| `frontend/tailwind.config.ts` (수정) | Task 1: DS 팔레트·radius·그림자 토큰 |
| `frontend/src/index.css`·`main.tsx` (수정) | Task 1: 폰트 import(400/500/700)·body 폰트 스택 |
| `frontend/src/theme.test.ts` (신설) | Task 1: 토큰 존재 계약(팔레트 키 삭제 방지 그물) |
| `frontend/src/app/navigation.ts`(+`.test.ts`) (신설) | Task 2: 메뉴 데이터(3 최상위/4그룹)·상세 라우트 메타·브레드크럼 파생 함수 |
| `frontend/src/app/TopBar.tsx` (신설) | Task 2: 톱바(브랜드 블록·사용자명·로그아웃) — AppShell 헤더 이식 |
| `frontend/src/app/Breadcrumb.tsx` (신설) | Task 2: 라우트 → 브레드크럼(nav, h1 아님) |
| `frontend/src/app/AppShell.tsx` (수정) | Task 2: TopBar 전역 상단 + 데이터 렌더 사이드바. `min-w-0`·`shrink-0` 계약 보존 |
| `frontend/src/app/AppShell.test.tsx` (신설) | Task 2: 데이터→렌더·adminOnly 필터·그룹 기본 펼침 |
| `frontend/src/features/placeholder/PlaceholderPage.tsx` (신설) | Task 2: NAS·Monitoring "준비 중" |
| `frontend/src/app/router.tsx` (수정) | Task 2: /nas·/monitoring 라우트 추가(기존 라우트 무변경) |
| `frontend/src/components/ui/Button.tsx` (수정) | Task 3: solid/outline/ghost 3계층(기존 variant 호환 유지) |
| `frontend/src/features/jobs/formFields.tsx` (신설) | Task 3: `field`·`StoragePicker` 이사(SubmitScan·ScanPaths import 갱신) |
| `frontend/src/components/ui/{InfoPanel,InfoCard,Stepper}.tsx`(+test) (신설) | Task 3: DS 서피스·스테퍼 |
| `frontend/src/components/wizard/{Wizard.tsx,BottomActionBar.tsx}`(+test) (신설) | Task 4: 재사용 위저드 프레임 |
| `frontend/src/features/jobs/SubmitJob.tsx`(+test 개편) | Task 4: 4스텝 위저드 적용(도메인 로직 이식) |
| `frontend/src/features/**`(h1 클래스만)·`Login.tsx`·`index.html` (수정) | Task 5: 타이틀 격상·로그인 정돈·탭 타이틀 |

---

### Task 0: 기준선 확인 (커밋 없음)

**Files:** 없음(검증만)

- [ ] **Step 1: vitest·tsc 기준선**

Run: `cd frontend && npx vitest run && npx tsc -b`
Expected: **266 passed (49 files)**, tsc 무출력 exit 0. 다르면 진행 전 보고.

- [ ] **Step 2: e2e 기준선 (셸을 갈아엎기 전 마지막 초록 실측)**

Run: `cd frontend && npm run test:e2e`
Expected: **9 passed**. 여기 빨강이면 이 슬라이스 밖의 문제다 — 진행 전 보고.

---

### Task 1: 토큰·폰트 — DS 팔레트 스왑 + @fontsource + radius/그림자

**Files:** `package.json`·`package-lock.json`, `tailwind.config.ts`, `src/index.css`,
`src/main.tsx`, `src/theme.test.ts`(신설)

**Interfaces:** 화면 무접촉 전면 리스킨. 클래스명(`text-ok` 등)은 전부 그대로,
값만 바뀐다. 순수 시각 변화는 테스트로 못 박을 수 없다 — 이 Task 의 그물은
(a) 토큰 존재 계약 테스트, (b) 기존 266 초록 유지, (c) 빌드 산출물 실측, (d) 눈이다.
**정직 고지: 색 값 자체의 RED 는 없다.**

- [ ] **Step 1: 의존성 설치(승인 2건만)**

Run: `cd frontend && npm install @fontsource/noto-sans-kr lucide-react`
Expected: lockfile 갱신. 실패(사내망 registry 차단)면 **중단하고 보고** — 우회 금지.
설치 후 실측: `ls node_modules/@fontsource/noto-sans-kr/files | wc -l` 와 400/500/700
css 존재 확인. 한글 폰트는 unicode-range 로 쪼개져 브라우저가 필요한 조각만 받는다
— dist 용량은 Step 5 에서 실측한다.

- [ ] **Step 2: 토큰 존재 계약 테스트 (RED)**

`src/theme.test.ts` 신설 — tailwind.config 를 import 해 신설 토큰 키가 존재하는지
단언한다(누군가 토큰을 지우면 화면의 해당 클래스가 **조용히** 미생성 CSS 가 되는
것을 막는 그물):

```ts
import config from "../tailwind.config";
// 슬라이스 31: 화면은 hex 를 모른다(전부 토큰 경유) -- 토큰이 지워지면 tailwind 는
// 클래스를 조용히 생성하지 않아 화면이 무색으로 깨진다. 존재를 여기서 못 박는다.
const colors = (config.theme?.extend?.colors ?? {}) as Record<string, string>;
for (const key of ["accent", "accenthover", "navy", "infobg", "panel", "line",
                   "ok", "okbg", "bad", "badbg", "busy", "busybg",
                   "canvas", "surface", "ink", "muted"]) {
  test(`토큰 ${key} 가 팔레트에 있다`, () => expect(colors[key]).toBeTruthy());
}
```

Run: `npx vitest run src/theme.test.ts` → Expected: **RED** (accenthover·navy·infobg·
panel·line 부재).

- [ ] **Step 3: 팔레트·radius·그림자 스왑 (GREEN)**

`tailwind.config.ts`:

```ts
colors: {
  canvas: "#f5f6f8",            // 페이지 배경 = DS panel 회색(카드가 보더+흰색으로 뜬다)
  surface: "#ffffff", ink: "#333333", muted: "#888888",
  accent: "#1a56db", accenthover: "#1749b8",   // DS primary 블루
  navy: "#0d2b88",                             // 톱바 브랜드 블록
  infobg: "#eef4ff",                           // 연파랑 안내 카드
  panel: "#f5f6f8",                            // 회색 안내 패널
  line: "#e0e2e6",                             // 1px 구분선(그림자 대신 보더 구획)
  ok: "#067647", okbg: "#e7f7ee",              // "정상=초록" 의미 체계 유지
  bad: "#b42318", badbg: "#fee4e2",
  busy: "#1a56db", busybg: "#eef4ff",          // busy 만 보라→파랑(액센트 계열)
},
borderRadius: {
  card: "0.5rem",   // 카드 12px→8px
  // rounded-lg 는 버튼·인풋 전부에 이미 쓰인다(실측) -- 기본 8px 를 6px 로
  // 오버라이드하면 화면 무접촉으로 DS 질감(버튼·인풋 6px)이 된다.
  lg: "0.375rem",
},
boxShadow: { soft: "0 1px 2px rgba(16,24,40,.05)" },  // 그림자 최소화(보더 중심)
```

Run: `npx vitest run src/theme.test.ts` → GREEN.

- [ ] **Step 4: 폰트 도입 (셀프호스팅, CDN 금지)**

`src/main.tsx` 상단(JS import 라 CSS `@import` 순서 함정이 없다):

```ts
// 슬라이스 31: 사내망·CSP 안전을 위해 셀프호스팅(@fontsource) -- CDN 링크 금지.
// weight 는 400/500/700 만: 한글은 unicode-range 조각이라 실제 전송은 필요분만이다.
import "@fontsource/noto-sans-kr/400.css";
import "@fontsource/noto-sans-kr/500.css";
import "@fontsource/noto-sans-kr/700.css";
```

`src/index.css` body: `font-family: "Noto Sans KR", ui-sans-serif, system-ui, sans-serif;`

- [ ] **Step 5: 검증 + 용량 실측**

Run: `npx vitest run && npx tsc -b && npm run build && du -sh dist && find dist -name "*.woff2" | wc -l`
Expected: 266+16 passed, tsc 초록, 빌드 성공. dist 용량과 woff2 개수를 **기록**한다
(3 weight 한글 조각이라 수 MB 증가 예상 — 이미지 크기에 실리므로 수치를 남긴다.
10MB 를 크게 넘으면 보고 후 weight 축소 검토).

- [ ] **Step 6: 뮤테이션 1건**

`cp tailwind.config.ts /tmp/slice31-tailwind.bak` → 팔레트에서 `navy` 줄 삭제 →
`npx vitest run src/theme.test.ts` **RED 확인** → `cp /tmp/slice31-tailwind.bak tailwind.config.ts` 원복 → GREEN 재확인.

- [ ] **Step 7: 커밋**

신규 파일 add 선행: `git add frontend/src/theme.test.ts`
`git commit -m "feat(portal): DS 팔레트·radius·폰트 토큰 스왑 (슬라이스 31 T1)" -- frontend/package.json frontend/package-lock.json frontend/tailwind.config.ts frontend/src/index.css frontend/src/main.tsx frontend/src/theme.test.ts`

---

### Task 2: 셸 — TopBar + 데이터 사이드바 + Breadcrumb + placeholder

**Files:** `src/app/navigation.ts`(+test 신설), `src/app/TopBar.tsx`(신설),
`src/app/Breadcrumb.tsx`(신설), `src/app/AppShell.tsx`(수정), `src/app/AppShell.test.tsx`(신설),
`src/features/placeholder/PlaceholderPage.tsx`(신설), `src/app/router.tsx`(수정)

**Interfaces:**
- 메뉴는 **데이터가 진실**이다. 항목 추가·이동 = `navigation.ts` 배열 한 줄.
- 지킬 계약(전제 재확인 #1·3·4·5 + 과제 제약): aside 240px(`md:w-60 md:shrink-0`),
  본문 `flex-1 min-w-0`(원 주석 이식 — 6bc2ecb 재발 방지), 그룹 **기본 펼침**(접기
  기능은 허용), 그룹 헤더는 `<a>` 아님(`aside a` 셀렉터 오염 금지), 셸에
  `overflow-x-auto`·h1 금지, 로그아웃 버튼 이름·onSettled nav 원문 이식.

- [ ] **Step 1: navigation.ts 데이터 설계 + 테스트 (RED)**

`src/app/navigation.ts`:

```ts
import type { LucideIcon } from "lucide-react";
export interface NavItem { path: string; label: string; icon: LucideIcon; adminOnly?: boolean }
export interface NavGroup { label: string; items: NavItem[]; adminOnly?: boolean }
export interface NavSection {           // 최상위: DMS(그룹들) / NAS·Monitoring(자리)
  label: string; icon: LucideIcon;
  groups?: NavGroup[];                  // DMS
  path?: string;                        // placeholder 단일 링크(/nas, /monitoring)
}
export const NAVIGATION: NavSection[] = [ /* DMS 4그룹 + NAS + Monitoring */ ];
// 사이드바 밖 상세 라우트 → 브레드크럼 부모 매핑(react-router matchPath 패턴)
export const DETAIL_ROUTES = [
  { pattern: "/jobs/:requestId", label: "요청 상세", parent: "/jobs" },
  { pattern: "/admin/batches/new", label: "배치 생성", parent: "/admin/batches" },
  { pattern: "/admin/batches/:batchId", label: "배치 상세", parent: "/admin/batches" },
  { pattern: "/admin/builds/:buildId", label: "빌드 상세", parent: "/admin/builds" },
] as const;
export function breadcrumbFor(pathname: string): { label: string; path?: string }[] { /* HOME > 섹션 > 그룹 > 항목 [> 상세] */ }
```

그룹 구성(사용자 확정 IA — 항목 라벨은 기존 사이드바 문구 그대로):
- 작업: 내 작업 /jobs · 작업 제출 /jobs/new · 내 스캔 경로 /scan-paths · scan 실행 /admin/scan(adminOnly)
- 스토리지(adminOnly): 스토리지 /admin/storages · 노드 /admin/nodes · 아티팩트 경로 /admin/artifact-base
- 운영(adminOnly): 대시보드 /admin/dashboard · 배치 작업 /admin/batches · 빌드 /admin/builds · 릴리스 /admin/releases · 컨트롤 상태 /admin/control
- 관리(adminOnly): 계정 /admin/accounts · 정책 /admin/policies · denylist /admin/denylist · 감사 로그 /admin/audit

`navigation.test.ts` (RED — 파일 부재로 import 실패가 첫 RED):
경로 전수(기존 사이드바 15링크 전부 데이터에 존재), adminOnly 표시가 기존 게이트와
일치, `breadcrumbFor("/jobs/abc")` = HOME>DMS>작업>내 작업>요청 상세,
`breadcrumbFor("/admin/storages")` = HOME>DMS>스토리지>스토리지, 미지 경로는 HOME 만.

- [ ] **Step 2: AppShell.test.tsx (RED 계속)**

msw 로 me(user/admin) 물려 AppShell 렌더:
① user 는 작업 그룹 3링크만·admin 전용 그룹 부재 ② admin 은 15링크 전부
③ 그룹은 **기본 펼침**(모든 링크가 클릭 없이 보인다 — e2e·router.test 안전망)
④ NAS·Monitoring 링크 존재 ⑤ 로그아웃 버튼 이름 "로그아웃" 존재
⑥ 그룹 접기 토글: 클릭 시 해당 그룹 링크 사라짐, 재클릭 복원.

- [ ] **Step 3: 구현 (GREEN)**

- `TopBar.tsx`: 흰색 h-14, 좌측 네이비 브랜드 블록(`bg-navy text-white` **div**,
  "AI Storage Portal") + 우측 `사용자명 · 역할` + 로그아웃 **필 버튼**(`rounded-full`
  아웃라인). 기존 AppShell 헤더의 useLogout·onSettled nav 코드와 「왜」 주석 원문 이식.
- `AppShell.tsx` 재구성:

```tsx
<div className="min-h-full flex flex-col">
  <TopBar />
  <div className="flex-1 md:flex">
    {/* md:w-60(240px)=e2e L3 상수. shrink-0·min-w-0 주석은 원문 이식(재발 방지 근거) */}
    <aside className="md:w-60 md:shrink-0 bg-surface md:border-r md:border-line p-3">
      {/* NAVIGATION.map(...) — 그룹 헤더는 <button>(aside a 셀렉터 밖), 항목은 NavLink */}
    </aside>
    <div className="flex-1 min-w-0">
      <main className="p-5"><Breadcrumb /><ErrorBoundary key={pathname}>{children}</ErrorBoundary></main>
    </div>
  </div>
</div>
```

- 사이드바 링크 클래스(L4 산수 — 전제 재확인 #5를 주석으로 박는다):

```tsx
// L4(e2e layout.ts): 링크 높이 < 2×line-height. text-sm(20px)이면 한계 40px 라
// DS 의 44px 항목이 위반이다 -- leading-6(24px)으로 한계를 48px 로 올리고
// py-2.5(10px×2)+24px=44px 로 DS 높이와 L4 를 동시에 만족시킨다.
const linkCls = ({ isActive }) =>
  `flex items-center gap-2 rounded-lg px-3 py-2.5 text-sm leading-6 ${
    isActive ? "bg-infobg text-accent font-medium" : "text-ink hover:bg-panel"}`;
```

- 활성 스타일: 구 `bg-accent text-white` → DS 풍 `bg-infobg text-accent`(연파랑 배경).
- `Breadcrumb.tsx`: `<nav aria-label="breadcrumb">`, HOME 은 `text-accent` 링크("/"),
  나머지 회색, `>` 구분. **h1 아님**.
- `PlaceholderPage.tsx`: h1(해당 화면 소유 — 셸 아님) + "준비 중입니다" InfoPanel 풍 문구.
- `router.tsx`: `/nas`·`/monitoring` 라우트 추가(RequireRole+AppShell). 기존 라우트 무변경.

- [ ] **Step 4: 검증 (GREEN + 회귀)**

Run: `npx vitest run && npx tsc -b`
Expected: 신규 포함 전부 초록. **router.test 18건이 진짜 회귀 그물**이다(AppRouter
전체 렌더 — 사이드바가 깨지면 여기가 빨개진다).

- [ ] **Step 5: e2e 실측 (기하 계약)**

Run: `npm run test:e2e`
Expected: 9 passed — L3 240px(aside 폭 무변경), L4(leading-6 산수), 01 로그아웃,
04 사이드바 "내 작업" 클릭. 빨강이면 **상수 완화가 아니라 셸 수리**가 기본 방향이다
(폭을 바꾸는 결정을 했다면 SIDEBAR_WIDTH_PX 를 같은 커밋에서 갱신).

- [ ] **Step 6: 뮤테이션 1건 (데이터→렌더 증명)**

`cp src/app/navigation.ts /tmp/slice31-nav.bak` → "노드" 항목 한 줄 삭제 →
`npx vitest run src/app` **RED 확인**(경로 전수·15링크 단언이 문다 — 메뉴가 데이터의
함수임의 증명) → 원복 → GREEN.

- [ ] **Step 7: 커밋**

신규 add: `git add frontend/src/app/navigation.ts frontend/src/app/navigation.test.ts frontend/src/app/TopBar.tsx frontend/src/app/Breadcrumb.tsx frontend/src/app/AppShell.test.tsx frontend/src/features/placeholder/PlaceholderPage.tsx`
`git commit -m "feat(portal): DS 셸 — TopBar·데이터 사이드바(3최상위/4그룹)·브레드크럼 (슬라이스 31 T2)" -- frontend/src/app frontend/src/features/placeholder`

---

### Task 3: 컴포넌트 — Button 3계층·fields 이사·InfoPanel·InfoCard·Stepper·BottomActionBar

**Files:** `src/components/ui/Button.tsx`(수정), `src/features/jobs/formFields.tsx`(신설),
`src/features/jobs/SubmitScan.tsx`·`src/features/scanpaths/ScanPaths.tsx`(import 줄만),
`src/components/ui/{InfoPanel,InfoCard,Stepper}.tsx`(+test 신설),
`src/components/wizard/BottomActionBar.tsx`(+test 신설), `src/components/ui/Card.tsx`(수정)

**Interfaces:**
- Button: `variant?: "primary" | "outline" | "ghost"` — **기존 값 2종 호환 유지**
  (primary=solid 파랑, ghost=회색 아웃라인=취소 용도 유지, outline=파랑 아웃라인 신설).
  기존 사용처는 무수정으로 새 팔레트를 받는다.
- `field`·`StoragePicker` 는 `formFields.tsx` 로 이사(전제 재확인 #2). SubmitJob 은
  Task 4 에서 위저드가 되므로 **먼저** 옮겨 임포터의 결합을 끊는다. aria-label·
  옵션 문구 등 렌더 결과 불변 — SubmitScan·ScanPaths 테스트가 무수정 초록이어야 한다.
- Stepper: `steps: { id, label }[]` + `current: number` + `onNavigate?`. 활성=파랑
  채움+흰 숫자, 완료=체크 또는 파랑 테두리, 비활성=흰 바탕 회색 테두리, `>` 구분.
  li/button 렌더(h1·a 금지 — aside 밖이지만 규율 통일).
- BottomActionBar: `cancel`(좌) / `help?`(중앙 회색) / `actions`(우: outline·solid) 슬롯.
  상단 `border-t border-line`. **위저드 비종속**(단독 화면도 쓸 수 있게 components/wizard 에
  두되 props 만으로 동작).
- InfoPanel(회색 `bg-panel`)·InfoCard(연파랑 `bg-infobg`): 단순 서피스 + role 없음.
- Card: `shadow-soft` 중심 → `border border-line` 추가(보더 구획) — Card 는 한 곳이라
  전 화면 일괄 반영.

- [ ] **Step 1: 테스트 먼저 (RED)** — Stepper(활성/완료/비활성 표시·클릭 내비),
  BottomActionBar(3 슬롯 렌더·버튼 disabled 전달), Button(variant 3종 클래스 분기 —
  클래스명 존재만, 색 값 단언 금지), InfoPanel/InfoCard smoke.
- [ ] **Step 2: 구현 (GREEN)** — 위 인터페이스대로. formFields 이사 + 임포터 2곳
  import 줄 갱신.
- [ ] **Step 3: 검증** — `npx vitest run && npx tsc -b` 전부 초록(특히 SubmitScan·
  ScanPaths·SubmitJob 기존 테스트 **무수정 초록** = 이사가 무해했다는 증거).
- [ ] **Step 4: 뮤테이션 1건** — Stepper 활성 판정을 `i === current` → `i === current + 1`
  로 오염(`cp` 백업) → Stepper 테스트 RED → 원복 GREEN.
- [ ] **Step 5: 커밋** — 신규 add 후
  `git commit -m "feat(portal): DS 컴포넌트 — Button 3계층·Stepper·액션바·서피스, form 필드 이사 (슬라이스 31 T3)" -- frontend/src/components frontend/src/features/jobs/formFields.tsx frontend/src/features/jobs/SubmitScan.tsx frontend/src/features/scanpaths/ScanPaths.tsx`

---

### Task 4: 위저드 프레임 + SubmitJob 4스텝 적용

**Files:** `src/components/wizard/Wizard.tsx`(+test 신설),
`src/features/jobs/SubmitJob.tsx`(개편), `src/features/jobs/SubmitJob.test.tsx`(개편)

**Interfaces:**
- **프레임은 도메인을 모른다**: `Wizard` 는 Stepper+콘텐츠+BottomActionBar 조립과
  스텝 전이만 소유한다. 배치 생성 등이 나중에 그대로 얹는 게 성공 조건이다.

```tsx
export interface WizardStep { id: string; label: string }
export function Wizard(props: {
  steps: WizardStep[]; current: number; onNavigate: (i: number) => void;
  canNext?: boolean;                    // 스텝 국소 검증(기본 true)
  onCancel: () => void; help?: React.ReactNode;
  submitLabel: string; submitDisabled?: boolean; onSubmit: () => void;
  children: React.ReactNode;            // 현재 스텝 콘텐츠
})
// 마지막 스텝에서만 submit 버튼(solid), 그 전엔 "다음"(solid)+"이전"(outline).
// Enter 제출 유출 금지: form 소유는 호출자 쪽이고 프레임 버튼은 type="button",
// 제출 버튼만 명시 onClick(handleSubmit) -- 초반 스텝 Enter 가 제출로 새지 않는다.
```

- **SubmitJob 4스텝** (현 폼 실측 기반 — 도메인 로직·aria-label·문구 전부 이식, 무변경):
  1. **연산**: sync/rm 선택 + rm 경고(기존 문구를 InfoCard 로) —
     연산 전환 시 필드 초기화 정책도 현행 유지(상태 `f` 는 위저드 밖 단일 useState).
  2. **대상**: sync=소스/목적지 스토리지·경로 4필드 / rm=스토리지·대상 경로.
     storagesQ.isError 문구는 이 스텝에 노출(제출 차단은 기존 `blocked` 그대로).
  3. **옵션**: 연산별 체크박스 + sync 고급 옵션(details 유지 또는 섹션 평탄화 —
     aria-label 불변) + 우선순위 + (admin) 특권 실행 필드.
     `recursiveMissing`·`statLiteConflict`·`advancedError` 는 이 스텝에서 보이고
     **"다음" 을 비활성**(canNext) — 기존 문구 그대로.
  4. **확인·제출**: 제출 바디 요약(연산·경로·옵션·우선순위 — InfoPanel) + rm 이면
     경고 재노출. BottomActionBar 우측 solid "제출"(이름 불변), `submitDisabled={blocked}`.
- `blocked` 산식·`syncOptions()`·`handleSubmit` 바디 조립은 **원문 이식**(제출 계약
  불변 — 서버·e2e 와의 접점이다). SubmitScan 은 위저드화하지 않는다(e2e 04 가 밟는
  화면 — 단일 폼 유지).

- [ ] **Step 1: Wizard 프레임 테스트 (RED)** — 스텝 전이(다음/이전), canNext=false 면
  다음 비활성, 마지막 스텝에서만 제출 버튼, submitDisabled 전달, 취소 콜백.
- [ ] **Step 2: Wizard 구현 (GREEN)**
- [ ] **Step 3: SubmitJob.test 개편 (RED)** — 기존 14건을 위저드 동선으로 다시 쓴다.
  **제출 바디 `toEqual` 단언 5건은 원문 보존**(sync 기본/rm recursive/고급 5종 생략/
  open_noatime/chmod·chown/batch_files·bufsize — 리디자인이 전송 계약을 안 건드렸다는
  증거). 헬퍼 `goToOptions()`·`goToConfirm()` 로 "다음" 클릭 동선 공통화. 신규:
  스텝 3 오류 시 "다음" 비활성, 스텝 4 요약에 rm 경고 노출.
- [ ] **Step 4: SubmitJob 구현 (GREEN)** — `npx vitest run src/features/jobs && npx tsc -b`.
- [ ] **Step 5: 전체 검증** — `npx vitest run`(전 파일) + `npm run test:e2e` 9 passed
  (04 는 SubmitScan 경로라 무접촉이 기대값 — 빨강이면 formFields 이사 부작용을 의심).
- [ ] **Step 6: 뮤테이션 1건** — SubmitJob 의 제출 가드에서 `if (blocked) return;` 제거
  (`cp` 백업) → "recursive 해제 시 제출 비활성"·"stat+lite 차단" 테스트 RED →
  원복 GREEN. (프레임이 아니라 도메인 가드를 문다 — 위저드화로 가드가 증발하지
  않았음의 증명.)
- [ ] **Step 7: 커밋** — 신규 add 후
  `git commit -m "feat(portal): 재사용 위저드 프레임 + SubmitJob 4스텝 (슬라이스 31 T4)" -- frontend/src/components/wizard frontend/src/features/jobs/SubmitJob.tsx frontend/src/features/jobs/SubmitJob.test.tsx`

---

### Task 5: 화면 잔여 정리 — 타이틀 격상·로그인·탭 타이틀 (스타일만)

**Files:** `src/features/**`(h1 클래스만 23곳), `src/features/auth/Login.tsx`,
`index.html`

**Interfaces:** 전면 재배치는 다음 반복이다. 여기선 (a) DS 페이지 타이틀(~28px bold),
(b) 로그인 화면 정돈, (c) 브라우저 탭 타이틀만. **텍스트·aria-label·role 불변.**
정직 고지: 스타일 전용이라 RED 없음 — 게이트는 기존 스위트 무수정 초록 + 눈.

- [ ] **Step 1: h1 격상(기계적)** — 실측 grep: `text-lg font-semibold`(19)·
  `text-lg font-semibold mb-4`(4). 각 h1 의 클래스만 `text-2xl font-bold [mb-4→mb-5]`
  로 스왑(내용 무변경). Run: `npx vitest run` — **무수정 초록이어야 한다**(클래스
  단언은 status 계열뿐임을 실측했다. 빨강이면 해당 파일은 되돌리고 보고).
- [ ] **Step 2: 로그인 정돈** — Card 위에 네이비 브랜드 블록 + "AI Storage Portal"
  표기(div — h1 은 기존 "DMS 로그인" 유지, 개명은 열린 질문 #2 확정 전 금지).
  버튼·인풋은 이미 토큰을 받으므로 배치 여백만.
- [ ] **Step 3: 탭 타이틀** — `index.html` `<title>AI Storage Portal</title>`
  (무단언 실측 — 전제 재확인 #12).
- [ ] **Step 4: 검증** — `npx vitest run && npx tsc -b` 전부 초록.
- [ ] **Step 5: 커밋** —
  `git commit -m "style(portal): 페이지 타이틀 격상·로그인 정돈·탭 타이틀 (슬라이스 31 T5)" -- frontend/src/features frontend/index.html`

---

### Task 6: 마감 — 전체 게이트 + 무접촉 증명

**Files:** 없음(검증만 — 필요시 수리)

- [ ] **Step 1: 전체 스위트**

Run: `cd frontend && npx vitest run && npx tsc -b && npm run test:e2e`
Expected: vitest **290±**(266 + 신규 — 실측치를 기록), tsc 초록, e2e **9 passed**.
e2e 코드 diff 는 0 이 목표다(240px 유지·링크 한 줄·URL 불변이면 무접촉이 성립).
바꿨다면 어느 단언을 왜 바꿨는지 커밋 메시지에 명시.

- [ ] **Step 2: 무접촉·규율 증명 (grep 게이트)**

```
git diff origin/main --stat -- . ':!frontend' ':!docs/plans'   # → 출력 0 (백엔드·배포 무접촉)
git diff origin/main -- frontend/src/lib/reasonCodes.json      # → 출력 0
grep -rn "#[0-9a-fA-F]\{3,6\}" frontend/src --include="*.tsx" | grep -v "\.test\." # → 0건 (hex 금지)
grep -rn "fonts.googleapis\|fonts.gstatic\|cdn" frontend/src frontend/index.html   # → 0건 (CDN 금지)
grep -c "overflow-x-auto" frontend/src/app/*.tsx               # → 0 (전제 재확인 #3)
```

- [ ] **Step 3: 수동 확인 준비** — `npm run build` 성공 + dist 용량 기록. 시각 판정은
  배포 후 사용자 눈이 한다(아래 절).

---

## 플랜 이후: 배포·실증 (플랜 태스크 아님 — 순서가 계약이다)

1. **매니페스트-우선**: `deploy/k8s` 의 `dms:d41` 5곳(30-migrate-job.yaml:25,
   40-api.yaml:67·84, 41-controller.yaml:35·52)을 **d42** 로 bump 하고 커밋
   (`git commit -- deploy/k8s`). 에이전트 d35 무접촉.
2. **그 커밋에서** 빌드: `podman build -f deploy/docker/Dockerfile.dms -t pkg-01:5000/dms:d42 .`
   (context = repo root. Dockerfile 이 deploy/k8s 를 COPY — 순서가 바뀌면 드리프트
   배지가 거짓말한다). push 후 api·controller 롤아웃. **migrate 재실행 불요**(DB 무접촉
   — 태그 bump 는 단일 진실 유지용).
3. **사용자 눈 실증**: 포트포워딩 http://192.168.75.215:8080 으로 사용자가 직접 확인
   — 톱바·사이드바 4그룹·브레드크럼·위저드·색감. **디자인은 사용자 승인이 실증이다.**
   근사 색값은 "보면서 조정" 이 확정 결정이므로, 피드백 → 토큰 값만 수정 → 재배포
   루프를 전제한다(화면 무접촉 조정이 이 슬라이스가 구조로 보장하는 것).
4. 승인 후 관례대로 CHANGELOG·BACKLOG 갱신(+ BACKLOG 에 기록: radix-select 미사용
   의존성 정리 후보, 인라인 `border-black/10` 산재 → `border-line` 이행, 사용자/운영자
   화면 분리 대비 — NAVIGATION 데이터가 이미 섹션 단위라 분리를 막지 않는다).

## 열린 질문 (사용자 확인 필요 — 기본값으로 진행 가능)

1. **로그아웃 표기**: 테스트 계약(접근성 이름 "로그아웃")상 기본은 한국어 유지.
   스크린샷대로 영문 "Logout" 을 원하면 e2e 01 + router.test 를 같은 커밋에서 갱신
   (또는 aria-label 유지 + 표시만 영문). 기본: **한국어 유지**.
2. **로그인 h1 "DMS 로그인"** 을 "AI Storage Portal" 로 개명할지(무단언 실측 — 가능).
   기본: 유지(브랜드 블록만 추가).
3. **NAS·Monitoring placeholder URL**: `/nas`·`/monitoring` 로 신설 예정. 다른 경로
   선호가 있으면 지정.
4. **페이지 배경**: canvas 를 DS panel 회색(#f5f6f8)으로 제안(카드가 보더+흰색으로
   뜬다). DS 본문처럼 순백을 원하면 토큰 한 줄 조정.
5. **busy 색**: 보라 → 파랑(#1a56db) 스왑 제안 — 액센트와 동일 계열이라 "진행 중"
   배지가 링크·버튼과 시각적으로 겹치면 한 단계 밝은 파랑으로 조정.
