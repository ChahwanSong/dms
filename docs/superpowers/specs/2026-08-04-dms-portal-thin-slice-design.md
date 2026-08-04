# DMS 포탈 — 슬라이스 1 (일회성 전체 스택) 설계

2026-08-04. 이 문서는 Phase 4 포탈의 **첫 번째 얇은 전체 슬라이스(thin full slice)**를 정의한다.
상위 설계 스펙 `2026-08-02-dms-clean-slate-design.md` §8(포탈)·§9(모니터링)의 하위 구현 문서이며,
그 문서와 충돌하면 상위 스펙이 이긴다. 백엔드(Phase 1~3c)는 완료·테스트베드 실증되어 있고,
이 슬라이스는 그 위에 프론트엔드 스택을 처음으로 세워 **전 경로를 한 바퀴 증명**하는 것이 목표다.

## 0. 범위

### 0.1 이 슬라이스가 증명하는 것
인증·세션 · role 라우팅 · 데이터 페치/폴링 · **preview→confirm 게이트** · C 디자인 시스템 ·
dms-api 정적 서빙까지 **전체 스택 한 바퀴**. "얇게(thin)"는 각 영역에서 화면 수를 최소로 두되,
쓰기 경로(일회성 sync)를 포함해 "전체(full)"를 만족한다는 뜻이다.

### 0.2 쓰기 경로 = 일회성 sync
쓰기 경로는 **dsync 단건 요청**으로 한다. 이유: scan은 읽기전용이라 백엔드가 preview→confirm
게이트를 건너뛴다(`stepper.py`: `scan은 바로 execution`). sync/rm만 preview(dry-run)→confirm
(fingerprint)을 거치므로, DMS의 시그니처 안전장치를 UI로 증명하려면 sync여야 한다.

### 0.3 비목표 (이 슬라이스에서 하지 않음)
- **배치성(대량 묶음) 작업** — 별도 슬라이스 2. 내비에 자리(disabled)만 확보한다. §8 참조.
- 스토리지 등록/수정 폼, 작업 상세 드릴다운 심화, 노드 상세, 정책/denylist 관리 UI.
- 서버측 통계 집계 엔드포인트(대시보드 지표는 이 슬라이스에선 클라이언트 집계).
- 차트 라이브러리(지표는 숫자 타일). e2e(Playwright).

## 1. 화면 지도

| 트리 | 화면 | 주요 API |
|---|---|---|
| 공통 | 로그인 | `POST /api/auth/login`, `GET /api/auth/me`, `POST /api/auth/logout` |
| 공통 | 앱 셸(사이드바 내비 role 분기, 상단바 사용자/로그아웃, 반응형) | — |
| **User** | 내 작업 목록 (+ 행 상세 패널) | `GET /api/user/requests`, `GET /api/user/requests/{id}`, `GET /api/user/requests/{id}/jobs` |
| **User** | 작업 제출 (sync) → preview→confirm→exec | `POST /api/user/requests`, `POST /api/user/jobs/{id}:confirm`, `POST /api/user/jobs/{id}:cancel` |
| **Admin** | 스토리지 목록 | `GET /api/admin/storages` |
| **Admin** | 개요 대시보드 (지표 타일 + 노드 상태 + 최근 작업) | `GET /api/admin/nodes` + requests 목록 클라이언트 집계 |
| **Admin** | 배치 작업 — **자리만 확보, disabled("다음 예정")** | — (슬라이스 2) |

- role은 **user/admin 둘뿐**이다(백엔드 `ROLE_USER`/`ROLE_ADMIN`). legacy·본 문서의 "운영자
  (operator)"는 우리 **admin**을 가리킨다 — 별도 role이 아니다.
- 대시보드는 **admin 전용**이다. 일반 user에게 노출하지 않는다.
- 지표 타일(실행 중/대기/오늘 성공/실패)은 전용 통계 엔드포인트가 아직 없어 **requests 목록을
  프론트에서 집계**한다(admin은 전체 조회). 노드 상태만 전용 API `/api/admin/nodes` 사용.
  슬라이스 후속에서 서버측 집계(§9 확장)로 대체 가능.

## 2. 아키텍처 — 레포 구조 · 빌드 · 서빙

### 2.1 레포 구조 (repo 루트에 `frontend/` 신설, `src/`의 형제)
```
frontend/
  index.html
  vite.config.ts        # dev: /api → http://localhost:8000 프록시
  package.json          # npm
  tailwind.config.ts    # C 디자인 토큰
  src/
    main.tsx            # React Router + TanStack QueryClientProvider
    app/                # AppShell, 라우트 트리, role 가드, AuthContext
    lib/api.ts          # 타입드 fetch 래퍼 (credentials:'include')
    components/ui/       # Radix 기반 Button/Card/Table/StatusPill/MetricTile/Dialog/Field
    features/
      auth/  jobs/  storages/  dashboard/   # 화면 + TanStack Query 훅
```

### 2.2 빌드 & 서빙
- **개발**: `vite dev`(프론트 5173) + `/api` 프록시 → 로컬 dms-api(8000). 백엔드 무변경.
- **프로덕션**: `vite build` → `frontend/dist/`. **dms-api에 `StaticFiles` 마운트 추가**(현재
  `app.py`에 없음). 라우팅 규칙:
  - `/api/*`, `/docs`, `/openapi.json` → 기존 라우터.
  - 그 외 경로 → `dist/`에서 정적 서빙, 파일 미매칭이면 **SPA fallback으로 `index.html`** 반환
    (클라이언트 라우팅 지원). 마운트는 라우터 include **뒤에** 등록해 API 우선순위를 보장한다.
- **Docker**: `deploy/docker/Dockerfile`(dms-api)을 **멀티스테이지**로 — `node:20` 스테이지에서
  `npm ci && vite build` → `dist/`를 python 런타임 이미지로 COPY. 별도 웹서버/컨테이너 없이
  단일 dms-api가 API+정적을 동시 서빙한다.

### 2.3 기술 스택
React + Vite + TypeScript · React Router · TanStack Query(서버 상태) · Tailwind + Radix(headless
primitives) · Vitest + Testing Library + MSW(테스트). 상태관리 라이브러리 별도 없음(서버 상태는
TanStack Query, 로컬 UI 상태는 React state/context).

## 3. 인증 · 세션 · 라우팅

- 포탈 로그인은 **username/password → accounts repo 검증 → 세션 쿠키**다(LDAP 아님 — LDAP는
  잡 신원 해석 전용). 백엔드에 login/signup/logout/me가 이미 있다.
- 모든 요청은 `credentials:'include'`. JS는 토큰을 다루지 않는다.
- **부트스트랩**: 앱 마운트 시 `GET /api/auth/me` → 200이면 `{actor, role}`을 AuthContext에
  저장·셸 렌더, 401이면 `/login`.
- **로그인/로그아웃**: `login` 성공 → `me` 무효화·재조회 → 역할별 기본 화면. `logout` → 쿼리
  캐시 클리어 → `/login`.
- **전역 401**: API 래퍼가 401을 만나면 AuthContext 무효화 + `/login` 리다이렉트(세션 만료 대응).
- **라우팅(React Router)**: public `/login`; protected는 셸 하위 전부. admin 전용 라우트
  (`/admin/storages`, `/admin/dashboard`)는 non-admin 접근 시 user 기본(`/jobs`)으로 리다이렉트,
  사이드바 항목도 role로 필터. 기본 랜딩: user→`/jobs`, admin→`/admin/dashboard`.

## 4. sync 쓰기 흐름 (preview→confirm 게이트)

1. **제출** — 폼(source storage+path · destination storage+path · options[--delete 등] ·
   priority) → `POST /api/user/requests {operation:"sync", ...}` → `{request_id, state:"Pending"}`.
   제출 후 해당 요청 상세로 이동.
2. **폴링** — `GET /api/user/requests/{id}/jobs`를 TanStack Query `refetchInterval`로 폴링(잡이
   비종단 상태인 동안만; 종단이면 폴링 중단). 상태 흐름 Pending → (planning) → **ConfirmPending**.
3. **확인 게이트** — 잡이 `ConfirmPending`이면 Radix Dialog로 preview(dry-run) 요약 +
   `preview_fingerprint` + 만료(`preview_expires_at`) 카운트다운 표시 → **"확인"** 클릭 시
   `POST /api/user/jobs/{job_id}:confirm {fingerprint}`. fingerprint는 잡이 준 값을 그대로 에코해
   서버가 재검증한다(사용자가 preview를 본 뒤 동일 fingerprint 재전송 = 게이트 통과 의미).
4. **실행/종료** — Executing → Succeeded/Failed. 비종단 동안 **취소** 버튼
   (`POST /api/user/jobs/{job_id}:cancel`) 노출.

에러 매핑(§6 참조): `fingerprint_mismatch`·`preview_expired`·`not_confirmable`·`already_terminal`
→ 안내 후 재조회.

## 5. 디자인 시스템 (C / Soft SaaS)

- **토큰(Tailwind)**: bg `#f6f6f3`(warm-neutral) · surface white · accent violet `#6d5efc` ·
  text/muted 계열 · radius(카드 `rounded-xl`) · soft shadow. **밝은 테마 단일**.
- **상태 표기**: solid soft pill(텍스트만, 선행 dot 없음), **green=정상 / red=비정상**, 진행 중은
  violet/amber 중립. 예: ok `bg #e7f7ee / text #067647`.
- **스펙 §8 지침 준수**: 이모지 금지, 좌측 보더 액센트 박스 금지, "dot+text 라운드 배지" 금지.
- **`components/ui/` 최소 세트**: `Button` · `Card` · `Table` · `StatusPill` · `MetricTile` ·
  `Dialog`(Radix) · `Field/Input/Select`(Radix Select) · `AppShell`(Sidebar+Topbar).
- **반응형**: 브레인스토밍 C 목업의 미디어쿼리 재사용 — 좁은 폭(≤760px)에서 사이드바→상단
  드로어, 지표 2열, 테이블 가로 스크롤 컨테이너.

## 6. 데이터 흐름 (API 클라이언트 + TanStack Query)

- **`lib/api.ts`**: 타입드 fetch 래퍼. `credentials:'include'`, `!ok`이면 `detail`(reason_code)을
  파싱해 throw → 에러 토스트/인라인 메시지. 401은 전역 핸들러로.
- **queryKeys**: 리소스별(`['auth','me']`, `['requests']`, `['request', id, 'jobs']`,
  `['storages']`, `['nodes']`). 잡/요청 목록은 비종단 잡이 있으면 `refetchInterval` 폴링.
- **mutations**: submit/confirm/cancel/login/logout → 성공 시 관련 queryKey 무효화.
- **reason_code → 한글 메시지 맵** (초기 세트, 백엔드 사유 코드 기준):

  | reason_code | 메시지(예) |
  |---|---|
  | `invalid_credentials` | 사용자명 또는 비밀번호가 올바르지 않습니다 |
  | `fingerprint_mismatch` | 미리보기가 변경되었습니다. 다시 확인해 주세요 |
  | `preview_expired` | 미리보기가 만료되었습니다. 다시 제출해 주세요 |
  | `not_confirmable` / `already_terminal` | 이미 처리된 작업입니다 |
  | `privileged_not_authorized` | 권한 있는 요청자가 아닙니다 |
  | `resource_conflict` | 동일 대상에 진행 중인 작업이 있습니다 |
  | `no_eligible_nodes` / `no_ready_sync_candidate` | 실행 가능한 노드가 없습니다 |

  맵에 없는 코드는 코드 원문을 그대로 노출(조용한 실패 금지, 상위 스펙 §1).

## 7. 테스트

- **프론트(Vitest + Testing Library, API는 MSW 모킹)**: 로그인 폼(성공→me), 역할 가드(user가
  admin 라우트 접근→리다이렉트), sync 제출 폼 검증, **확인 다이얼로그**(fingerprint 에코·만료
  처리·mismatch 에러), StatusPill 매핑, 대시보드 지표 렌더.
- **백엔드(pytest)**: 추가한 **StaticFiles + SPA fallback** 마운트가 미매칭 non-`/api` 경로에
  `index.html`을 주고 `/api/*`는 라우터로 가는지 1건.
- e2e(Playwright)는 이 슬라이스에서 보류.

## 8. 로드맵 — 슬라이스 2: 배치성(대량 묶음)

이 슬라이스에는 넣지 않지만, 내비에 자리를 확보하고 여기에 방향만 기록한다.

- **의미**: DMS의 "배치성"은 시간 예약/반복이 아니라 **대량 묶음(bulk)** 이다 — 여러 요청(주로
  CSV로 수백~수천 건)을 한 묶음으로 올려 **한 번에** 끝까지 구동. legacy에도 cron/스케줄러는
  없었고, 재실행은 운영자 수동 `:rerun`/`:rescan` 버튼뿐이었다.
- **legacy 참고 모델**: 부모 `backup_batches`/`scan_batches` + 자식 요청(`backup_requests` 등),
  일회성 라이프사이클(draft→previewing→running→done), 운영자 트리 UI(CSV 업로드·name·note·
  priority·node_count). 날짜/cron 입력 없음.
- **우리 백엔드에서 net-new**: 스케줄러가 아니라 — batch 부모/자식 fan-out + 배치 상태 집계 +
  CSV/목록 intake. 기존 단건 요청 머신(request→plan→run)을 자식으로 재사용하는 grouping 레이어.
- **포탈**: 운영자 트리의 배치 작업 페이지(목록·생성 CSV 업로드·배치 상세·진행률).
- 시간 예약/반복(nightly scan, 주기적 mirror)은 legacy에 선례가 없는 완전 net-new이며, 필요 시
  또 다른 별도 슬라이스로 다룬다(이 문서 범위 밖).

## 9. 결정 기록 (요약)

- 시각 방향 = **C / Soft SaaS**. 슬라이싱 = **얇은 전체 슬라이스**. 스타일 = **Tailwind + Radix**.
- 쓰기 경로 = **일회성 sync**(scan은 confirm 게이트 없음). 대시보드 = **admin 전용**.
- 배치성 = **대량 묶음**, **슬라이스 2**로 분리(이번엔 내비 자리만 확보).
- 서빙 = 단일 dms-api 정적 서빙(멀티스테이지 Docker) + dev vite 프록시. 레포 = `frontend/` 신설.
