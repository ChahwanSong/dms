# DMS 포탈 프론트엔드

Vite + React + TypeScript SPA. 백엔드 API는 `src/dms/api.py` (repo root), 배포는
`deploy/README.md` 참고.

```bash
npm install
npm run dev      # vite dev server
npm run build    # tsc -b && vite build
npm test         # vitest run
npx tsc -b       # 타입체크만
npm audit        # 아래 "알려진 advisory" 먼저 읽을 것
```

## 알려진 advisory — `GHSA-qwww-vcr4-c8h2` (react-router, high) — 열려 있음, 의도적

슬라이스 12 Task 5(`react-router-dom` 6 → 7 업그레이드, 커밋 `9696c67`)에서
`npm audit`에 걸려 있던 moderate 3건(`GHSA-wrjc-x8rr-h8h6`,
`GHSA-337j-9hxr-rhxg`, `GHSA-jjmj-jmhj-qwj2` — 전부 range `6.0.0-7.17.0`)을
`react-router-dom@7.18.2`로 해소했다. 그런데 그 직후 `npm audit`에 **새 high가
나타났다**:

```
react-router  7.12.0 - 8.2.0
Severity: high
React Router: RSC Mode CSRF Bypass Allows Action Execution Before 400 Response
https://github.com/advisories/GHSA-qwww-vcr4-c8h2
```

**이 advisory는 의도적으로 미해결 상태로 남겨뒀다.** 이유:

1. **이 앱에는 실질 영향이 없다.** 공식 advisory 원문: *"This only affects your
   application if you are using the unstable RSC APIs."* 이 프론트엔드는
   `BrowserRouter`/`Routes`/`Route`/`Navigate`/`NavLink`/`Link`/`useParams`/
   `useNavigate`/`useLocation`만 쓰는 순수 클라이언트 SPA다 — `createBrowserRouter`
   (데이터 라우터), RSC, `loader`/`action`, `unstable_*` API를 전혀 쓰지 않는다
   (`grep -rn "createBrowserRouter\|RSC\|unstable_\|loader:\|action:" src/` →
   매치 없음, 재확인 가능).

2. **다운그레이드가 해법이 아니다.** `npm audit fix --force`는 `react-router-dom
   @7.11.0` 설치를 제안하는데, 이건 원래 취약 범위(`6.0.0 - 7.17.0`) **안**이라
   위 moderate 3건(오픈 리다이렉트 등)이 그대로 되살아난다. 두 취약 범위가
   `7.12.0`~`7.17.0`에서 겹치지 않고 오히려 이어져서, **현재 두 그룹 모두를
   피하는 `react-router-dom` 버전이 존재하지 않는다** — 수정본(`>=8.3.0`)이
   아직 릴리스되지 않았다(`npm view react-router-dom dist-tags` 기준 latest는
   `7.18.2`).

**재점검 조건**: `react-router-dom@8.3.0` 이상이 릴리스되면 업그레이드하고
`npm audit`으로 이 항목이 사라지는지 확인한 뒤 이 절을 지울 것. 그 전까지
`npm audit fix --force`를 이 패키지에 대해 실행하지 말 것 — 위 1)에서 설명한
대로 더 명백히 이 앱에 해당하는 취약점 3건을 되살린다.

## 알려진 advisory — vite/vitest/esbuild 체인(moderate 3, high 1, critical 1) — 범위 밖

`npm audit`을 지금 돌리면 위 react-router 항목 외에 7건 중 나머지 5건이 더 뜬다:
`esbuild`(moderate, 개발 서버가 임의 origin의 요청을 받아주는 문제),
`vite`(moderate 2건 + high 1건, 옵티마이즈드 의존성 `.map` 경로 순회 등),
`vite-node`/`@vitest/mocker`(moderate, vite 취약점의 전이), `vitest`(critical,
Vitest UI 서버가 켜져 있을 때 임의 파일을 읽고 실행할 수 있는 문제 —
`GHSA-5xrq-8626-4rwp`).

**전부 개발 도구 체인(빌드 서버·테스트 러너)이고 배포되는 프로덕션 번들에는
들어가지 않는다** — `npm run build`(`tsc -b && vite build`) 산출물이 아니라
`npm run dev`/`vitest`를 실행하는 로컬 개발 환경에서만 노출 표면이 있다.
critical로 표시된 `vitest` 건도 Vitest의 `--ui` 서버가 실제로 켜져 있어야
성립하는데, 이 프로젝트는 그 모드를 쓰지 않는다(`package.json`의 `test`/
`test:watch` 스크립트 어디에도 `--ui`가 없다). 고치려면 `vite`/`vitest`
메이저 업그레이드(현재 `vite@5`/`vitest@2` → `vite@8`/`vitest@3` 이상)가
필요한데, 이는 슬라이스 12 설계 §7이 명시적으로 범위 밖으로 둔 semver-major
하네스 교체다. `npm audit fix --force`를 여기 실행하지 말 것 — 검증되지
않은 메이저 업그레이드로 테스트 하네스 전체가 흔들릴 수 있다.
