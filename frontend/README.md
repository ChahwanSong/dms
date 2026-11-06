# DMS 포탈 프론트엔드

Vite + React + TypeScript SPA(react-router · TanStack Query · Tailwind · Radix · lucide-react). 백엔드 API 는
`src/dms/api/` 패키지(FastAPI — 같은 프로세스가 빌드된 SPA 를 `DMS_STATIC_DIR` 에서 서빙한다; 이미지(`deploy/docker/Dockerfile.dms`)는
`dist` 를 `/app/static` 에 담는다). 저장소 규약은 [`CLAUDE.md`](../CLAUDE.md),
API·포탈의 불변식은 [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §7, 이미지 빌드·릴리스는 [`deploy/README.md`](../deploy/README.md) §8·§9.

```bash
npm install
npm run dev         # vite 개발 서버
npm run build       # tsc -b && vite build → dist/
npm test            # vitest run(단위·화면 테스트)
npm run test:watch  # vitest 감시 모드
npm run test:e2e    # 빌드 + e2e 타입체크 + playwright(실 브라우저 풀스택)
npx tsc -b          # 타입체크만
```

- **e2e**(`e2e/`, 수기 게이트 — CI 없음): 하네스(`e2e/harness/global-setup.ts`)가 빌드된 `dist/` 로 백엔드(`dms` CLI — 저장소의
  `.venv`, 워크트리면 본 저장소의 `.venv`)를 `127.0.0.1:8093` 에 띄우고 임시 sqlite·가짜 도구로 시드한다. `PYTHONPATH` 는 이 체크아웃의
  `src` 로 고정된다. 브라우저는 시스템 Chrome(`DMS_E2E_BROWSER_CHANNEL`, 기본 `chrome` — 다운로드하지 않는다), 직렬 실행·재시도 없음·
  `test.only` 금지(`playwright.config.ts`). 이미지 빌드 전 게이트는 deploy/README §8 「이미지 빌드 전 게이트」.

## 규약 (위반하면 배포나 보안이 깨진다 — 상세는 CLAUDE.md)

- **런타임은 airgap 이다** — 런타임에 읽는 폰트·아이콘·스크립트·이미지는 모두 번들에 넣는다(폰트 `@fontsource/*`, 아이콘 lucide-react·
  인라인 SVG). CDN `<link>`·외부 fetch 금지. 빌드 뒤 `dist/index.html` 에 외부 URL(`http://`·`https://`)이 0건인지 보는 것이 배포 게이트다.
- **사유 코드는 양쪽 등록** — 백엔드가 새 `reason_code` 를 내면 `src/lib/reasonCodes.json` 과 `src/lib/api.ts` 의 `REASON_MESSAGES` 둘 다에
  넣는다(양방향 계약 — `src/lib/reasonCodes.test.ts` ↔ 백엔드 `tests/test_reason_codes_coverage.py`).
- **비밀번호(와 k8s 클러스터 토큰)는 봉인 통로로만 보낸다** — `src/lib/passwordTransport.ts` 의 `postWithSealedPassword`(백엔드
  `_password_from`). `apiSend` 에 password 를 직접 실으면 그 경로만 평문이 되고 아무 테스트도 빨간불이 아니다. 봉인 상수는 백엔드
  `password_transport.py` 와 바이트 단위로 같아야 한다(대조 테스트).
- **null(모름) ≠ 0 ≠ 실패** — 서버가 null 로 준 칸을 0 이나 실패로 그리지 않는다.

## 의존성 권고(npm audit)

`npm audit --omit=dev`(브라우저에 실리는 운영 의존성)는 0건이어야 한다(2026-11-06 확인 — 0건). `npm audit` 전체에 남는 권고는 개발 도구
체인(vite·vitest·esbuild·postcss 계열 — 개발 서버·테스트 러너, 번들에 들어가지 않는다)뿐이다. 고치려면 vite·vitest 메이저 업그레이드가
필요하다 — 별도 작업으로 하고, `npm audit fix --force` 로 올리지 않는다(검증 안 된 메이저 교체가 테스트 하네스를 흔든다).
