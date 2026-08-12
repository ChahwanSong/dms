# 슬라이스 12 — 포탈 위생과 진단 가능성 설계

denylist 실증 중에 발견한 결함 하나에서 출발한다: 차단된 요청을 포탈에서 열면
`identity_denied` 라는 **영문 원시 코드**가 그대로 보인다. 조사해 보니 이것은 한 코드의
매핑 누락이 아니라 **렌더 지점 자체가 번역을 거치지 않는** 구조적 문제였고, 같은 성격의
이연 항목들(ErrorBoundary, `events` 테이블, 감사 actor, router 취약점)과 묶어 처리한다.

---

## 1. 실측으로 확인한 전제

| 사실 | 확인 방법 |
|---|---|
| 차단된 요청이 `Rejected` / `identity_denied` 로 기록된다 | 테스트베드 실증(포탈 API) |
| 백엔드 사유 코드 59개 중 **22개가 `REASON_MESSAGES` 미매핑** | 소스 grep + 매핑 대조 스크립트 |
| `reason_code` 를 렌더하는 3곳이 **매핑을 아예 안 거친다** | `Timeline.tsx:10`, `RequestDetail.tsx:112`, `BatchDetail.tsx:38` |
| `BuildDetail.tsx:35` 만 올바른 패턴(`REASON_MESSAGES[x] ?? x`)을 쓴다 | 슬라이스 11에서 리뷰가 강제한 결과 |
| stepper 가 **복합 코드** `prefix:suffix` 를 만든다 | `stepper.py:127,156,196,245` |
| 배치 항목의 `reason_code` 에 **RequestState 값**이 들어간다 | `batch_orchestrator.py:51,55` |
| react-router 6.30.x 에 moderate 2건, **6.x 에 수정본 없음** | `npm audit` 실측 |
| `events` 테이블은 스키마만 있고 쓰는 코드가 0줄 | grep |

**조사가 틀렸던 것 하나:** "`GET /api/admin/audit-log` 이 무인증"이라는 보고가 있었으나,
`routes_storages.py:6` 의 라우터 선언이 `dependencies=[Depends(require_admin)]` 를 걸고 있고
실 클러스터에서 인증 없이 호출하면 **401** 이다. 이 슬라이스 범위에 넣지 않는다.

---

## 2. 사유 코드 표시 — `reasonText()` 하나로 모은다

지금은 `REASON_MESSAGES[x] ?? x` 가 `api.ts` 두 곳과 `BuildDetail` 한 곳에 흩어져 있고,
정작 가장 많이 보이는 세 곳은 그것조차 없다. 인라인으로 또 세 번 복붙하는 대신
**`reasonText(code)` 하나**를 만든다. 근거는 두 가지다:

1. **복합 코드**를 순수 Record 조회로는 절대 매핑할 수 없다.
   `preflight_submit_failed:submit_failed` 같은 값은 정확 일치가 영원히 실패한다.
   `reasonText` 는 `:` 로 나눠 접두를 번역하고 접미는 괄호로 덧붙인다.
2. 미매핑 시 **원시 코드를 그대로** 돌려주는 폴백 규칙을 한 곳에서만 정하게 된다
   (`api.test.ts` 가 이 폴백을 이미 단언하고 있다 — 일반 문구로 바꾸면 안 된다).

적용 지점: `Timeline.tsx`, `RequestDetail.tsx`, `BatchDetail.tsx`, `BuildDetail.tsx`,
그리고 `api.ts` 의 `ApiError` 생성 두 곳.

**`ApiError.code` 는 건드리지 않는다.** `ScanPaths.tsx:52` 가 `err.code === "no_covering_scan"`
로 분기하므로, 번역되는 것은 표시 문구뿐이다.

매핑에 추가할 코드(실측 목록):

- stepper/controller 잡 코드: `orphan_recovery`, `preflight_failed`, `execution_failed`,
  `empty_preview`, `preview_timed_out`, `preview_failed`, `execution_recheck_failed`
- planner/identity/placement 거절: `identity_denied`, `ldap_not_configured`,
  `ldap_unavailable`, `ldap_identity_not_found`, `missing_policy`, `policy_disabled`
- 복합 접두 4개: `preflight_submit_failed`, `execution_submit_failed`,
  `preview_submit_failed`, `execution_recheck_submit_failed`
- HTTP detail 코드: `not_authenticated`, `admin_required`, `admin_token_required`,
  `invalid_token`, `account_exists`, `invalid_username`, `job_not_found`,
  `batch_not_found`, `batch_not_confirmable`, `no_failed_items`,
  `no_preview_fingerprint`, `empty_batch`, `invalid_batch_operation`,
  `invalid_max_concurrency`, `invalid_storage`, `invalid_node_name`,
  `agent_node_identity_mismatch`, `terminate_failed`, `invalid_job_id`, `invalid_batch`

죽은 키 `invalid_priority_value` 는 삭제한다 (백엔드가 내지 않는다).

**커버리지 테스트를 둔다.** 백엔드가 낼 수 있는 코드 목록을 체크인해 두고
`Object.keys(REASON_MESSAGES)` 와 대조한다 — 새 코드가 생겼는데 매핑을 빠뜨리면
빨간불이 나야 한다. 목록을 사람이 유지하는 것이 약점이지만, 정적 추출은 문자열 조립
때문에 완전할 수 없으므로 **명시적 목록 + 테스트**가 현실적인 최선이다.

## 3. 배치 항목 사유 — 백엔드 의미를 고친다

`BatchDetail` 의 「사유」 열은 지금 `Cancelled`/`Rejected`/`Failed` 만 보여준다.
`batch_orchestrator` 가 `reason_code=req_state` 로 **상태값**을 넣고 있기 때문이고,
그 값은 바로 옆 StatusPill 과 완전히 중복이다. `reasonText()` 로 감싸도 무의미하다.

고친다: 배치 항목이 종단으로 갈 때 **그 요청의 진짜 `reason_code`** 를 전파한다.
요청의 마지막 비-Pending 전이에서 읽는다. 없으면 `NULL` 로 두고 화면은 `—` 를 보인다
(상태값을 중복 표시하느니 비우는 편이 낫다).

## 4. ErrorBoundary — 2단으로 건다

지금 SPA 어디에도 error boundary 가 없다. 슬라이스 9 에서 배열 아닌 페이로드 하나가
**화면 전체를 흰 화면**으로 만든 사고가 있었고, 그때는 해당 컴포넌트만 방어했다.

의존성을 새로 추가하지 않고(직접 작성 ~30줄) 두 곳에 마운트한다:

- **바깥**: `AppRouter` 의 `<Routes>` 를 감싼다. `/login` 과 `/` 는 AppShell 밖이라
  안쪽만으로는 못 덮는다.
- **안쪽**: `AppShell` 의 `{children}` 을 감싸되 **`useLocation().pathname` 을 key 로** 준다.
  기능 화면 하나가 죽어도 사이드바와 로그아웃이 살아 있고, 다른 화면으로 이동하면
  경계가 스스로 풀린다. key 가 없으면 AppShell 은 모든 보호 라우트에서 같은 컴포넌트
  타입·같은 위치이므로 **에러 상태에 영원히 갇힌다** — 이 key 가 설계의 핵심이다.

데이터 라우터(`createBrowserRouter`)를 쓰지 않으므로 라우트 단위 `errorElement` 는
선택지가 아니다. 클래스 컴포넌트(`getDerivedStateFromError` + `componentDidCatch`)여야 한다.

테스트는 던지는 컴포넌트를 렌더해 폴백과 **내비게이션 생존**을 함께 단언한다.
`console.error` 는 테스트 안에서 국소적으로 stub 한다(전역 setup 을 오염시키지 않는다).

## 5. `events` 테이블 — 좁게 활성화한다

폐기 대신 활성화한다. `state_transitions` 와 중복이 아니기 때문이다 —
**전이가 일어나지 않은 실패**가 이 시스템에서 가장 진단하기 어려운데, 그것들이 지금
전부 stderr 로만 나간다:

- `planner.py` 의 항목별 예외 삼킴
- `stepper.py` 의 항목별 예외 삼킴
- `data_jobs.py` 종단 가드가 의도적으로 아무것도 기록하지 않는 지점
- `stepper.py` 의 best-effort terminate 가 `ExecutionError` 를 삼키는 지점
- 아티팩트 요약을 못 읽어 sentinel 로 덮는 지점

규칙:

- **전이가 남는 것은 이벤트로 쓰지 않는다** (중복 노이즈 + 핫 경로 쓰기 2배).
- **admin 뮤테이션도 쓰지 않는다** — 그것은 `audit_log` 채널이고 이미 배선돼 있다.
- 이벤트 기록은 **절대 예외를 올리지 않고, 업무 트랜잭션에 참여하지 않는다.**
  진단 기록 실패가 상태 전이를 롤백하거나 루프 틱을 죽이면 안 된다.
- `component` 는 NOT NULL 이므로 호출자가 반드시 넘긴다. 어휘는 기존 actor 와 맞춘다
  (`planner`, `stepper`, `api`).
- `payload` 는 `dump_json`/`load_json` 을 쓴다.

노출: `GET /api/user/requests/{request_id}` 응답에 `transitions` 옆으로 요청 범위
이벤트를 붙인다(`idx_events_request` 인덱스 하나로 끝난다). 포탈 요청 상세에 표시한다.

**증가 제한을 함께 낸다.** 지금 테이블에는 보존 정책도 시간 인덱스도 없다.
기존 `retention` 루프에 이벤트 purge 를 추가하고 `at` 인덱스를 만든다.
보존 경로 없이 반쯤 활성화하는 것은 폐기보다 나쁘다.

## 6. 감사 actor 구분 — API 경계에서만

지금 공유 토큰으로 호출하면 감사 행의 actor 가 `shared-token`(또는 `x-dms-actor` 값)이라,
사람 admin 과 스크립트를 구분할 수 없다.

**`Identity.actor` 자체는 절대 접두를 붙이지 않는다.** 그 값은 에이전트 노드 인증,
특권 요청자 판정, `requester_id` → LDAP/denylist 해석, 스캔 경로 소유권, 자기잠금 가드에
쓰인다 — 여기에 손대면 에이전트 수집과 잡 신원 해석이 깨진다.

대신 `Identity` 에 `auth` 필드(`"session"` | `"token"`)를 더하고, `audit_actor(identity)`
헬퍼가 세션이면 그대로, 토큰이면 `token:<actor>` 를 돌려준다. **감사 로그를 쓰는 admin
뮤테이션 라우트에서만** 그 헬퍼를 쓴다. 저장소에는 인증 지식을 넣지 않는다.

- 헤더 없는 토큰 호출의 기본값이 `shared-token` 이므로 `token:shared-token` 이 된다 —
  빈 `token:` 이 나오지 않게 한다.
- 들어온 `x-dms-actor` 가 이미 `token:` 으로 시작하면 **거절한다.** 아니면 접두가
  서버 소유의 출처 표식이 아니게 된다.
- `/api/auth/me` 는 원시 actor 를 그대로 돌려준다(프론트 헤더 표시와 기존 테스트).
- 마이그레이션도 백필도 없다. 계정명에 `:` 가 금지돼 있어 과거 행과 충돌하지 않는다.

## 7. react-router 6 → 7

`npm audit` 실측: moderate 2건(`GHSA-wrjc-x8rr-h8h6` 오픈 리다이렉트,
`GHSA-337j-9hxr-rhxg` SSR 하이드레이션). 영향 범위 `6.0.0 - 7.17.0` 이고 **6.x 에 수정본이
없다** — 실제 수정은 `react-router-dom >= 7.18.0` 메이저 업그레이드뿐이다.

이 앱에서는 위험이 낮다: 데이터 라우터도 loader 도 splat 도 쓰지 않고, 사용 중인 API
(`BrowserRouter`/`Routes`/`Route`/`Navigate`/`NavLink`/`Link`/`useParams`/`useNavigate`/
`useLocation`)는 v7 에서 그대로다. 현실적인 여파는 `v7_startTransition` 이 MemoryRouter
기반 테스트의 타이밍을 바꾸는 것 하나이므로, **전체 프론트 테스트가 초록불인 것을
합격 조건으로 삼는다.**

vite/vitest/esbuild 의 dev 전용 advisory 는 범위 밖이다 — 둘 다 semver-major 하네스
교체가 필요하다.

---

## 8. 이 슬라이스에서 하지 않는 것

- 롤아웃(§7 나머지) → 슬라이스 13.
- 모니터링 대시보드(§9) → 슬라이스 14.
- 일반 목적 이벤트 버스, 상태 전이의 이벤트 미러링.
- vite/vitest 메이저 업그레이드.

---

## 9. 실증 (테스트베드)

1. denylist 로 요청을 차단하고 포탈 요청 상세를 열어 **한국어 사유**가 보이는지
   (실증의 출발점이었던 바로 그 화면).
2. 복합 코드가 접두 번역 + 접미 병기로 보이는지.
3. 렌더 크래시를 유발해 ErrorBoundary 폴백이 뜨고 **사이드바가 살아 있는지**,
   다른 화면으로 이동하면 복구되는지.
4. 공유 토큰으로 admin 뮤테이션을 하고 감사 행의 actor 가 `token:` 접두를 갖는지,
   세션 admin 은 맨 이름인지.
5. `x-dms-actor: token:x` 가 거절되는지.
6. 이벤트가 실제로 쌓이는지 — 스텁 어댑터 실패를 유발하거나 존재하지 않는 경로로
   preflight 를 실패시켜 요청 상세에 이벤트가 붙는지.
7. 이벤트 purge 가 도는지.
8. 업그레이드된 라우터에서 포탈 전 화면이 여전히 뜨는지, `npm audit` 에 react-router
   advisory 가 사라졌는지.
