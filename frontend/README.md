# DMS 포탈 프론트엔드

Vite + React + TypeScript SPA(react-router · TanStack Query · Tailwind · Radix · lucide-react). 백엔드 API 는
`src/dms/api/` 패키지(FastAPI — 같은 프로세스가 빌드된 SPA 를 `DMS_STATIC_DIR` 에서 서빙한다; 이미지(`deploy/docker/Dockerfile.dms`)는
`dist` 를 `/app/static` 에 담는다). 저장소 규약은 [`CLAUDE.md`](../CLAUDE.md),
API·포탈의 불변식은 [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §7, 이미지 빌드·릴리스는 [`deploy/README.md`](../deploy/README.md) §8·§9.

```bash
npm install
npm run dev         # vite 개발 서버
npm run build       # tsc -b && vite build → dist/
npm run build:image # 이미지 빌드용(Dockerfile.dms) — 앱 코드만 타입 검사(tsconfig.build.json) + vite build. 이미지 빌드는 힙을
                    #   NODE_OPTIONS 1024MB 로 고정하고 빌드 파드 상한은 2Gi 다 — 2026-11-23 d37 은 1Gi(힙 약 505MB)에서 tsc -b 가 heap OOM
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
- **비밀번호와 비밀번호가 아닌 비밀은 봉인 통로로만 보낸다** — `src/lib/passwordTransport.ts` 하나에 짝이 둘이다:
  - 비밀번호: `postWithSealedPassword`(본문 `password_enc`) ↔ 백엔드 `routes_auth._password_from`.
  - 비밀번호가 아닌 비밀: `sendWithSealedSecret` — k8s 클러스터 토큰은 `features/k8s/useK8s.ts`(본문 `token_enc` ↔ `routes_k8s._token_from`),
    메일 릴레이 토큰은 `features/mail/useMailSettings.ts`(본문 `relay_token_enc` ↔ `routes_mail_settings._token_from`).
  - 공통: `apiSend` 에 password·토큰을 직접 실으면 그 경로만 평문이 되고 아무 테스트도 빨간불이 아니다. 봉인 상수는 백엔드
    `password_transport.py` 와 바이트 단위로 같아야 한다(대조 테스트).
- **상태 설명은 서버 상태와 같은 키** — 서버에 요청·잡·배치 상태를 더하거나 빼면 `src/lib/stateHelp.json` 도 맞춘다(백엔드
  `tests/test_state_help_contract.py` 가 RequestState·DataJobState·배치 상태 집합과 키를 대조 — 2026-11-19 배치 `Deleting` 이 그 예).
- **null(모름) ≠ 0 ≠ 실패** — 서버가 null 로 준 칸을 0 이나 실패로 그리지 않는다.
- **본문 폭·가운데는 셸이 정한다** — `app/AppShell.tsx` 의 `main`(최대 90rem = 1440, 사이드바 오른쪽 영역 가운데 — 2026-11-26)이 본문 열이다.
  화면(루트 section·카드)은 `mx-auto` 로 혼자 가운데로 가지 않고 폭 상한(`max-w-*`)만 둔다 — 기준선은 본문 열의 왼쪽 하나다(2026-08-16
  「한 기준선」 결정; vitest BuildForm·BuildHistory, e2e 03 「본문 열」(L5 `assertMainColumn`)이 본다). `main`·조상에 `overflow-*` 를 넣으면
  제출 위저드 하단 액션 바의 sticky 가 죽는다.
- **제출 화면은 단계별이다**(2026-11-26 — 단일 작업 4단계·배치 생성 5단계, 골격 `components/form/Wizard.tsx`, 규칙 전문은 그 머리 주석):
  - 입력 칸 Enter 는 (그 단계가 유효하면) 다음 단계로 갈 뿐 **제출하지 않는다** — 검색 칸(`data-wizard-enter="stay"`)·datalist 칸은 단계도 옮기지
    않고, 체크박스·라디오는 막기만 한다(브라우저는 체크박스 Enter 로도 `<form>` 을 제출한다).
  - 셸이 그리는 버튼과 화면이 셸 안에 넘기는 버튼은 **모두 `type="button"`** — `Button` 에 type 기본값이 없고 단일 화면은 `<form>` 안이라 type 없는
    버튼은 submit 이다(검토 「수정」이 form 을 제출하고 Enter 암묵 제출의 기본 버튼이 된다). vitest `test/wizard.ts` 의 `buttonsWithoutType` 이 본다.
  - **제출 더블클릭 그물**: 「다음」과 제출(「제출」·「배치 생성」)은 하단 바의 같은 자리·같은 `<Button>`(key 고정)이고, 바가 제출 onClick 에서
    `event.detail > 1`(같은 연속 클릭의 두 번째 이후)을 무시한다 — 마지막 앞 단계의 「다음」 더블클릭이 막 그려진 제출에 떨어지지 않게. vitest 는 노드
    동일성으로, e2e 04 는 실 브라우저 dblclick 으로 본다. 시간 잠금은 쓰지 않는다. 화면 쪽 방어(`handleSubmit` 단계 가드 `current !== "review"`)도 둔다.
  - 「다음」은 `aria-disabled`(막힌 채 누르면 이유 줄 강조 + 첫 `aria-invalid`/`data-wizard-nudge` 칸으로 포커스), 제출은 `disabled`. 단계 카드에
    aria-label 을 달지 않고(Playwright `getByLabel` 부분 일치), 하단 바에 z-index 를 주지 않는다(Radix 덮개 위로 올라온다).
  - 단계 게이트를 모두 합치면 제출 게이트다 — 식은 화면 밖 순수 함수 `features/jobs/submitGates.ts`·`features/batches/batchGates.ts`(옛 blocked·
    hints 의 식·문장 그대로 + 단계 태그)이고 잎 조합 전수 시험이 지킨다. 새 판정을 위저드에 넣지 마라. 제출 바디(`k8sBody`·`buildBody`)도 위저드와
    무관하다(바디 `toEqual` 단언이 그물).
  - 옛 한 장 시트 부품(`SubmitLayout`·`SummaryPanel`·`FormSection`)은 지웠다. `components/form/SubmitLayout.tsx` 에는 단계 본문·검토 카드가 다시
    쓰는 `FieldRow`·`SummaryList`·`SummaryRow`·`OptionTokens`·`SubmitHints` 만 남는다(파일 이름은 이력 — 머리 주석). 시트·카드·바 어디에도
    `<aside>` 를 쓰지 않는다(e2e L3 가 첫 aside = 사이드바로 잰다).
- **sync 진행률은 작업 상세에서, 진행 중에만 읽는다**(2026-11-26): `features/jobs/useArtifacts.useJobProgress` 는 요청 상세 잡 카드의 진행 중
  실행·미리보기 행(`progressModel.showsProgress` — dsync·nsync, 그 단계 ref 있음)만 부른다. 간격은 5초, `available:false`·오류가 연속 3회(`PROGRESS_SLOW_AFTER_MISSES`)면 15초(그 경로는 서버가
  매번 base 점검을 돈다 — 성공 한 번이면 다시 센다), 숨긴 탭은 멈추고 다시 보이면 한 번 읽는다. 잡 목록·배치 화면에서 /progress 를 부르지 마라(사용자 결정 — `JobsList.test`·
  `BatchDetail.test` 가 0건 고정). 키 없음 = 모름(0 으로 그리지 않는다), 남은 시간과 「남은」·「left」 글자는 쓰지 않는다, 진행 값은 표시 전용이다.
  한 번 받은 스냅숏은 같은 잡·단계에서 놓지 않는다(`progressModel.nextHeld` — 그 뒤의 `available:false`·오류도 마지막 스냅숏을 그리고 나이는 그
  스냅숏 기준, 「이전 잡 이미지」 문구는 성공을 한 번도 못 받았을 때만, 2026-11-27).
  계약은 `src/lib/progressContract.json` — 런타임 번들에 싣지 않고 시험이 간격(`poll_ms`)·phase·예 스냅숏을 잇는다(백엔드 러너·API 시험도 같은 파일).

## 의존성 권고(npm audit)

`npm audit --omit=dev`(브라우저에 실리는 운영 의존성)는 0건이어야 한다(2026-11-06 확인 — 0건). `npm audit` 전체에 남는 권고는 개발 도구
체인(vite·vitest·esbuild·postcss 계열 — 개발 서버·테스트 러너, 번들에 들어가지 않는다)뿐이다. 고치려면 vite·vitest 메이저 업그레이드가
필요하다 — 별도 작업으로 하고, `npm audit fix --force` 로 올리지 않는다(검증 안 된 메이저 교체가 테스트 하네스를 흔든다).
