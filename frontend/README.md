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

전체 조사 기록: `.superpowers/sdd/2026-08-06-dms-portal-hygiene-slice12/task-5-report.md`
(gitignore 대상, 로컬 워크트리에만 존재).
