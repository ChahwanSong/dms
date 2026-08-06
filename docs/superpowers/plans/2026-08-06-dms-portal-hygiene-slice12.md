# 슬라이스 12 — 포탈 위생과 진단 가능성 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 운영자가 포탈에서 보는 실패 사유를 한국어로 만들고, 한 화면의 크래시가 SPA 전체를 죽이지 않게 하며, 지금 stderr로만 새는 진단 정보를 요청 단위로 붙잡는다.

**Architecture:** 프론트는 `reasonText()` 한 곳으로 번역을 모으고(복합 코드 `prefix:suffix` 처리 포함), 2단 ErrorBoundary를 건다. 백엔드는 전이가 남지 않는 다섯 곳의 예외 삼킴 지점에서만 `events`에 기록하고(업무 트랜잭션 밖, 절대 예외를 올리지 않음), 감사 actor는 API 경계에서만 `token:` 접두로 구분한다.

**Tech Stack:** Python 3.11 / FastAPI / SQLite+PostgreSQL 양립 SQL, React 18 + Vite + TS + Tailwind + TanStack Query v5 + Vitest/Testing Library/MSW 2, react-router-dom 7.

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-08-06-dms-portal-hygiene-slice12-design.md`. 충돌하면 설계가 이긴다.
- `legacy/` 아래는 읽기 전용 — 수정·이동·삭제·추가 금지, import 금지, **코드 복사 금지**.
- 롤아웃(§7 나머지)과 모니터링 대시보드(§9)는 **범위 밖** — 슬라이스 13·14.
- SQL은 `:named` 파라미터만. `?`·`%s` 금지. dialect 분기는 꼭 필요한 곳만.
- 기존 테이블에 컬럼/인덱스를 더할 땐 `migrations.py`의 `stmts` 리스트에 넣고, **컬럼**이라면 `_ensure_columns`에도 넣는다.
- **`ApiError.code`는 원시 백엔드 코드를 그대로 유지한다** — `ScanPaths.tsx`가 `err.code === "no_covering_scan"`로 분기한다. 번역되는 것은 표시 문구뿐이다.
- **미매핑 코드는 원시 코드를 그대로 표시한다.** `frontend/src/lib/api.test.ts`가 이 폴백을 단언한다 — "알 수 없는 오류" 같은 일반 문구로 바꾸지 마라.
- **`Identity.actor`에 접두를 붙이지 마라.** 에이전트 노드 인증, 특권 요청자 판정, `requester_id` → LDAP/denylist 해석, 스캔 경로 소유권, 자기잠금 가드가 그 값에 의존한다.
- 이벤트 기록은 **절대 예외를 호출자에게 올리지 않고, 업무 트랜잭션에 참여하지 않는다.** 진단 기록 실패가 상태 전이를 롤백하거나 루프 틱을 죽이면 안 된다.
- **전이가 남는 것은 이벤트로 쓰지 않는다.** admin 뮤테이션도 쓰지 않는다(`audit_log` 채널이다).
- 프론트 UI 문자열은 한국어. 컴포넌트에 에러 문자열 하드코딩 금지 — 전부 `reasonText()`/`REASON_MESSAGES` 경유.
- 프론트 테스트는 파일마다 자체 MSW `setupServer` + `listen`/`resetHandlers`/`close`, 핸들러 경로는 상대경로.
- `src/test/setup.ts`에 전역 `console.error` mock을 넣지 마라 — 경계 테스트에서 국소적으로 stub한다.
- 백엔드 테스트: `.venv/bin/python -m pytest` (`python`은 PATH에 없다). 전체 스위트는 약 3.5분 — **포그라운드**로 Bash `timeout` 400000ms. 백그라운드+Monitor 조합 금지.
- 프론트: `cd frontend && npx vitest run`, 타입체크 `npx tsc -b`.
- **origin으로 push 금지.** 커밋만 한다.
- 주석은 한국어로 "왜"를 적는다.

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `frontend/src/lib/api.ts` (수정) | `REASON_MESSAGES` 확장 + `reasonText()` 신설 |
| `frontend/src/lib/reasonCodes.test.ts` (신규) | 백엔드 코드 목록 ↔ 매핑 커버리지 |
| `frontend/src/features/jobs/Timeline.tsx`, `jobs/RequestDetail.tsx`, `builds/BuildDetail.tsx` (수정) | `reasonText()` 경유 |
| `src/dms/batch_orchestrator.py`, `src/dms/repositories/requests.py` (수정) | 배치 항목에 진짜 사유 전파 |
| `frontend/src/features/batches/BatchDetail.tsx` (수정) | 사유 열 번역 |
| `frontend/src/app/ErrorBoundary.tsx` (신규) | 클래스 경계 |
| `frontend/src/app/router.tsx`, `AppShell.tsx` (수정) | 2단 마운트 |
| `frontend/package.json` (수정) | react-router-dom 7 |
| `src/dms/repositories/observability.py` (신규) | `record_event` — 절대 안 던진다 |
| `src/dms/planner.py`, `stepper.py`, `repositories/data_jobs.py` (수정) | 삼킴 지점에서 이벤트 기록 |
| `src/dms/migrations.py` (수정) | `idx_events_at` |
| `src/dms/retention.py`, `controller.py` (수정) | 이벤트 purge |
| `src/dms/api/routes_requests.py` (수정) | 요청 상세에 이벤트 |
| `src/dms/api/auth.py` + admin 라우트 10곳 (수정) | `audit_actor()` |

---

### Task 1: `reasonText()` 헬퍼와 매핑 확장

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/lib/reasonCodes.test.ts`
- Test: `frontend/src/lib/api.test.ts` (기존, 확장)

**Interfaces:**
- Produces: `export function reasonText(code: string | null | undefined): string`
  - `null`/`undefined`/`""` → `""`
  - 매핑 있으면 한국어
  - 매핑 없고 `:` 없으면 원시 코드 그대로
  - `prefix:suffix` 이고 prefix 매핑이 있으면 `"<한국어> (suffix)"`
  - `prefix:suffix` 이고 prefix 매핑이 없으면 원시 전체
- `REASON_MESSAGES` export는 **유지**한다 (`BuildsPage.tsx`가 직접 참조하고 `api.test.ts`가 단언한다).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/src/lib/api.test.ts` 끝에 추가:

```ts
import { reasonText } from "./api";

describe("reasonText", () => {
  it("빈 값은 빈 문자열", () => {
    expect(reasonText(null)).toBe("");
    expect(reasonText(undefined)).toBe("");
    expect(reasonText("")).toBe("");
  });

  it("매핑된 코드는 한국어", () => {
    expect(reasonText("identity_denied")).toBe("차단 목록에 있는 신원입니다");
  });

  it("매핑 없는 코드는 원시 코드 그대로", () => {
    // api.test.ts 가 이미 단언하는 폴백 규칙 -- 일반 문구로 바꾸지 않는다
    expect(reasonText("totally_unknown_code")).toBe("totally_unknown_code");
  });

  it("복합 코드는 접두를 번역하고 접미를 병기한다", () => {
    // stepper.py 가 f"preflight_submit_failed:{exc.reason_code}" 를 만든다 --
    // 정확 일치 조회로는 영원히 매핑되지 않는다
    expect(reasonText("preflight_submit_failed:submit_failed"))
      .toBe("사전 점검을 시작하지 못했습니다 (submit_failed)");
  });

  it("접두도 매핑 없으면 원시 전체", () => {
    expect(reasonText("nope:whatever")).toBe("nope:whatever");
  });
});
```

`frontend/src/lib/reasonCodes.test.ts` 신규 — 커버리지 고정:

```ts
import { describe, it, expect } from "vitest";
import { REASON_MESSAGES } from "./api";

// 백엔드가 실제로 낼 수 있는 사유 코드. 문자열 조립(f"prefix:{...}") 때문에 정적
// 추출이 완전할 수 없어 사람이 유지하는 목록을 둔다 -- 새 코드를 추가하면서 매핑을
// 빠뜨리면 이 테스트가 빨간불이 되는 것이 목적이다.
const BACKEND_CODES = [
  // planner / identity / placement
  "identity_denied", "ldap_not_configured", "ldap_unavailable",
  "ldap_identity_not_found", "missing_policy", "policy_disabled",
  "no_eligible_nodes", "no_ready_sync_candidate", "resource_conflict",
  "requester_disabled", "unsafe_path",
  // stepper / controller
  "orphan_recovery", "preflight_failed", "execution_failed", "empty_preview",
  "preview_timed_out", "preview_failed", "execution_recheck_failed",
  "preview_expired", "build_timeout", "build_failed",
  // 복합 접두
  "preflight_submit_failed", "execution_submit_failed",
  "preview_submit_failed", "execution_recheck_submit_failed",
  // HTTP detail
  "not_authenticated", "admin_required", "admin_token_required", "invalid_token",
  "account_exists", "invalid_username", "job_not_found", "batch_not_found",
  "batch_not_confirmable", "no_failed_items", "no_preview_fingerprint",
  "empty_batch", "invalid_batch_operation", "invalid_max_concurrency",
  "invalid_storage", "invalid_node_name", "agent_node_identity_mismatch",
  "terminate_failed", "invalid_job_id", "invalid_batch", "invalid_phase",
  "log_ref_not_found", "log_not_available", "artifact_not_found",
  "account_disabled", "maintenance_mode", "scan_admin_only",
  "privileged_not_authorized", "fingerprint_mismatch", "not_confirmable",
  "already_terminal", "invalid_credentials", "invalid_policy",
  "invalid_priority", "invalid_denylist_subject_type", "policy_not_found",
  "storage_exists", "storage_in_use", "storage_not_found", "node_not_found",
  "build_node_not_set", "build_in_progress", "unknown_image", "invalid_git_ref",
  "build_not_found", "submit_failed", "poll_failed", "unknown_build_node",
  "invalid_build_ref",
];

describe("REASON_MESSAGES 커버리지", () => {
  it("백엔드가 내는 모든 코드에 한국어 매핑이 있다", () => {
    const missing = BACKEND_CODES.filter((c) => !(c in REASON_MESSAGES));
    expect(missing).toEqual([]);
  });

  it("죽은 키가 없다 -- 백엔드가 내지 않는 코드는 두지 않는다", () => {
    const allowed = new Set([...BACKEND_CODES, "http_401", "http_422", "http_500", "http_503"]);
    const dead = Object.keys(REASON_MESSAGES).filter((k) => !allowed.has(k));
    expect(dead).toEqual([]);
  });
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run src/lib/api.test.ts src/lib/reasonCodes.test.ts`
Expected: FAIL — `reasonText` 미존재, 매핑 누락 다수, 죽은 키 `invalid_priority_value`

- [ ] **Step 3: 매핑을 확장하고 죽은 키를 지운다**

`frontend/src/lib/api.ts`의 `REASON_MESSAGES`에서 `invalid_priority_value` 를 삭제하고,
Step 1의 `BACKEND_CODES` 중 없는 것들을 전부 추가한다. 한국어 문구는 기존 항목들의
어투(존댓말, 운영자가 다음에 할 일을 알 수 있게)를 따른다. 예:

```ts
  identity_denied: "차단 목록에 있는 신원입니다",
  ldap_not_configured: "LDAP이 설정되지 않았습니다",
  ldap_unavailable: "LDAP에 연결할 수 없습니다",
  ldap_identity_not_found: "LDAP에서 사용자를 찾을 수 없습니다",
  missing_policy: "해당 도구의 정책이 없습니다",
  policy_disabled: "해당 도구의 정책이 비활성 상태입니다",
  preflight_failed: "사전 점검에 실패했습니다",
  execution_failed: "실행에 실패했습니다",
  preview_failed: "미리보기에 실패했습니다",
  preview_timed_out: "미리보기가 제한 시간을 넘겼습니다",
  empty_preview: "대상이 없습니다",
  execution_recheck_failed: "실행 직전 재점검에 실패했습니다",
  orphan_recovery: "컨트롤러 재시작으로 상태를 복구했습니다",
  preflight_submit_failed: "사전 점검을 시작하지 못했습니다",
  execution_submit_failed: "실행을 시작하지 못했습니다",
  preview_submit_failed: "미리보기를 시작하지 못했습니다",
  execution_recheck_submit_failed: "실행 직전 재점검을 시작하지 못했습니다",
  not_authenticated: "로그인이 필요합니다",
  admin_required: "관리자 권한이 필요합니다",
  admin_token_required: "관리자 토큰이 필요합니다",
  invalid_token: "토큰이 올바르지 않습니다",
```

나머지도 같은 방식으로 전부 채운다. `BACKEND_CODES`에 있는데 매핑이 없으면 Step 5에서
테스트가 잡는다.

- [ ] **Step 4: `reasonText()` 를 만든다**

`frontend/src/lib/api.ts`의 `REASON_MESSAGES` 바로 아래:

```ts
/** 사유 코드를 사용자에게 보일 문구로. 매핑이 없으면 원시 코드를 그대로 돌려준다
 *  -- 영문 코드라도 보이는 편이 "알 수 없는 오류"보다 진단에 쓸모 있다.
 *  stepper 는 f"{prefix}:{ExecutionError.reason_code}" 형태의 복합 코드를 만들므로
 *  정확 일치 조회만으로는 영원히 매핑되지 않는다 -- 접두를 번역하고 접미를 병기한다. */
export function reasonText(code: string | null | undefined): string {
  if (!code) return "";
  const direct = REASON_MESSAGES[code];
  if (direct) return direct;
  const sep = code.indexOf(":");
  if (sep > 0) {
    const prefix = REASON_MESSAGES[code.slice(0, sep)];
    if (prefix) return `${prefix} (${code.slice(sep + 1)})`;
  }
  return code;
}
```

그리고 `request<T>` 안의 `ApiError` 생성 두 곳을 `reasonText(code)` 로 바꾼다
(`ApiError`의 두 번째 인자 `code`는 **원시 코드 그대로** 둔다).

- [ ] **Step 5: 통과를 확인한다**

Run: `cd frontend && npx vitest run src/lib/` 그리고 `npx tsc -b`
Expected: PASS, 타입 에러 0

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/lib
git commit -m "feat(portal): 사유 코드 번역 헬퍼와 매핑 커버리지 테스트"
```

---

### Task 2: 렌더 지점을 `reasonText()` 로 통일

**Files:**
- Modify: `frontend/src/features/jobs/Timeline.tsx`
- Modify: `frontend/src/features/jobs/RequestDetail.tsx`
- Modify: `frontend/src/features/builds/BuildDetail.tsx`
- Test: `frontend/src/features/jobs/RequestDetail.test.tsx` (기존, 갱신 필요)

**Interfaces:**
- Consumes: `reasonText` from `../../lib/api` (Task 1).

**주의:** `RequestDetail.test.tsx`가 지금 `no_eligible_nodes` 같은 **원시 코드가 화면에
보인다**고 단언하고 있다. 번역하면 그 단언이 깨진다 — 같은 커밋에서 한국어 문구로 갱신해라.
테스트가 깨지는 것이 정상이고, 그것이 이 태스크가 실제로 동작한다는 증거다.

**범위 밖:** `DenylistList.tsx`의 `e.reason`, `NodesList.tsx`의 `m.reason`/`t.reason`,
`ControlStatePage.tsx`의 유지보수 사유는 **사람이 쓴 자유 문구**다. 절대 `reasonText()`로
감싸지 마라.

- [ ] **Step 1: 기존 테스트를 RED로 만든다**

`frontend/src/features/jobs/RequestDetail.test.tsx`에서 `no_eligible_nodes` 를 단언하는
곳들을 한국어 문구 `"실행 가능한 노드가 없습니다"` 로 바꾼다.

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run src/features/jobs/RequestDetail.test.tsx`
Expected: FAIL — 화면에는 아직 원시 코드가 보인다

- [ ] **Step 3: 렌더 지점을 고친다**

`Timeline.tsx`:

```tsx
{t.reason_code && <span className="text-bad">{reasonText(t.reason_code)}</span>}
```

`RequestDetail.tsx`:

```tsx
{j.reason_code && <p className="text-bad text-sm mt-1">{reasonText(j.reason_code)}</p>}
```

`BuildDetail.tsx` — 인라인 `REASON_MESSAGES[b.reason_code] ?? b.reason_code` 를
`reasonText(b.reason_code)` 로 바꾼다 (`—` 폴백은 유지).

- [ ] **Step 4: 통과를 확인한다**

Run: `cd frontend && npx vitest run` 그리고 `npx tsc -b`
Expected: 전체 PASS

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/features
git commit -m "feat(portal): 사유 코드를 한국어로 표시"
```

---

### Task 3: 배치 항목에 진짜 사유를 전파

**Files:**
- Modify: `src/dms/batch_orchestrator.py`
- Modify: `src/dms/repositories/requests.py`
- Modify: `frontend/src/features/batches/BatchDetail.tsx`
- Test: `tests/test_batch_orchestrator.py` (기존 파일명은 `ls tests | grep -i batch` 로 확인)

**Interfaces:**
- Produces: `RequestsRepository.last_reason_code(request_id) -> str | None`
  — 그 요청의 마지막 비-Pending 전이의 `reason_code`. 없으면 `None`.

지금 `batch_orchestrator`는 `reason_code=req_state` 로 **상태값**(`"Cancelled"`/`"Rejected"`/
`"Failed"`)을 넣는다. 그 값은 바로 옆 StatusPill과 완전히 중복이라 「사유」 열이 아무것도
알려주지 않는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

배치 테스트 파일에 추가 (픽스처는 그 파일의 기존 패턴을 따라라):

```python
def test_batch_item_carries_the_requests_real_reason_not_its_state(repos):
    # 지금은 reason_code 에 "Rejected" 같은 상태값이 들어가 StatusPill 과 중복된다.
    # 운영자가 알아야 하는 것은 "왜" 거절됐는가다.
    batch_id, req_id = _batch_with_one_request(repos)          # 파일의 기존 헬퍼를 쓴다
    repos.requests.set_state(req_id, RequestState.REJECTED,
                             reason_code="identity_denied", actor="planner")
    BatchOrchestrator(repos, settings=_settings()).run_once()
    item = repos.batches.list_items(batch_id)[0]
    assert item["status"] == "Rejected"
    assert item["reason_code"] == "identity_denied"     # 상태값이 아니라 진짜 사유
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_batch_orchestrator.py -q`
Expected: FAIL — `"Rejected" != "identity_denied"`

- [ ] **Step 3: 저장소에 조회를 더한다**

`src/dms/repositories/requests.py`:

```python
    def last_reason_code(self, request_id: str) -> "str | None":
        """그 요청이 종단으로 간 이유. 배치 항목이 상태값 대신 이것을 들고 있어야
        운영자가 화면에서 '왜'를 알 수 있다."""
        row = self._db.query_one(
            """SELECT reason_code FROM state_transitions
               WHERE entity_kind = 'request' AND entity_id = :r
                 AND to_state <> 'Pending' AND reason_code IS NOT NULL
               ORDER BY id DESC LIMIT 1""",
            {"r": request_id})
        return row["reason_code"] if row else None
```

`entity_kind` 값은 실제 저장된 문자열을 `state_transitions` 삽입 지점에서 확인해 맞춰라.

- [ ] **Step 4: orchestrator 가 그것을 쓰게 한다**

`src/dms/batch_orchestrator.py`의 두 `set_item_status(..., reason_code=req_state)` 를
`reason_code=self._repos.requests.last_reason_code(item["request_id"])` 로 바꾼다.
요청 id를 얻는 실제 표현은 그 함수의 지역 변수를 보고 맞춰라.

- [ ] **Step 5: 프론트 사유 열을 번역한다**

`frontend/src/features/batches/BatchDetail.tsx`:

```tsx
<td className="text-bad text-xs">{reasonText(it.reason_code)}</td>
```

`reasonText`는 `null`에 `""`를 돌려주므로 기존 `?? ""` 는 필요 없다.

- [ ] **Step 6: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_batch_orchestrator.py -q` 그리고
`cd frontend && npx vitest run src/features/batches`
Expected: PASS

- [ ] **Step 7: 전체 스위트**

Run: `.venv/bin/python -m pytest -q`
Expected: 전부 PASS

- [ ] **Step 8: 커밋**

```bash
git add src/dms frontend/src/features/batches tests
git commit -m "fix(batches): 항목 사유에 상태값 대신 진짜 사유 코드를 전파"
```

---

### Task 4: 앱 전역 ErrorBoundary

**Files:**
- Create: `frontend/src/app/ErrorBoundary.tsx`
- Create: `frontend/src/app/ErrorBoundary.test.tsx`
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/app/AppShell.tsx`

**Interfaces:**
- Produces: `export class ErrorBoundary extends React.Component<{children: React.ReactNode}, {error: Error | null}>`

**핵심 설계:** 안쪽 경계에 **`useLocation().pathname` 을 key 로** 준다. 없으면 AppShell은
모든 보호 라우트에서 같은 컴포넌트 타입·같은 트리 위치라, 한 번 에러 상태에 빠지면
다른 화면으로 이동해도 **영원히 갇힌다.**

데이터 라우터(`createBrowserRouter`)를 쓰지 않으므로 라우트 단위 `errorElement`는 선택지가
아니다. 새 의존성을 추가하지 마라 — 직접 작성한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/src/app/ErrorBoundary.test.tsx`:

```tsx
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ErrorBoundary } from "./ErrorBoundary";

function Boom(): JSX.Element { throw new Error("boom"); }

afterEach(() => vi.restoreAllMocks());

describe("ErrorBoundary", () => {
  it("자식이 던지면 폴백을 보여준다", () => {
    // React 가 경계에서 잡은 에러를 콘솔로 다시 뱉는다 -- 테스트 출력만 조용히 시키고
    // 전역 setup 은 건드리지 않는다(다른 곳의 진짜 경고가 묻히면 안 된다)
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<ErrorBoundary><Boom /></ErrorBoundary>);
    expect(screen.getByText("화면을 표시하지 못했습니다")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다시 시도" })).toBeInTheDocument();
  });

  it("던지지 않으면 자식을 그대로 렌더한다", () => {
    render(<ErrorBoundary><p>정상</p></ErrorBoundary>);
    expect(screen.getByText("정상")).toBeInTheDocument();
  });

  it("다시 시도를 누르면 경계가 초기화된다", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    let boom = true;
    function Maybe() { if (boom) throw new Error("boom"); return <p>회복</p>; }
    render(<ErrorBoundary><Maybe /></ErrorBoundary>);
    boom = false;
    await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(screen.getByText("회복")).toBeInTheDocument();
  });
});
```

`frontend/src/app/router.test.tsx`에 추가 — **내비게이션 생존**을 고정한다:

```tsx
  it("기능 화면이 크래시해도 사이드바가 살아 있다", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    // 노드 목록 응답을 렌더가 죽는 형태로 만든다
    server.use(http.get("/api/admin/nodes", () => HttpResponse.json(null)));
    renderAt("/admin/nodes");
    expect(await screen.findByText("화면을 표시하지 못했습니다")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "내 작업" })).toBeInTheDocument();
  });
```

이 테스트의 MSW 핸들러·헬퍼 이름은 `router.test.tsx`의 기존 패턴에 맞춰라. 응답이
렌더를 실제로 죽이지 않으면(방어 코드가 이미 있으면) 다른 화면을 골라라 — **실제로
크래시하는 조합을 찾아 쓰는 것이 이 테스트의 요점이다.**

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run src/app/`
Expected: FAIL — `Failed to resolve import "./ErrorBoundary"`

- [ ] **Step 3: 경계를 만든다**

`frontend/src/app/ErrorBoundary.tsx`:

```tsx
import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props { children: ReactNode }
interface State { error: Error | null }

/** 렌더 중 던진 예외를 잡아 화면 하나만 대체한다. 이것이 없으면 느슨한 백엔드
 *  페이로드 하나가 SPA 전체를 흰 화면으로 만든다(슬라이스 9 에서 실제로 겪었다). */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 서버로 보내지 않는다 -- 수집기가 없다. 콘솔이 유일한 단서다.
    console.error("render crash:", error, info.componentStack);
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    return (
      <section className="space-y-3">
        <h1 className="text-lg font-semibold">화면을 표시하지 못했습니다</h1>
        <p className="text-muted text-sm">
          이 화면을 그리는 중 오류가 발생했습니다. 다시 시도하거나 다른 화면으로 이동하세요.
        </p>
        <button
          className="rounded-lg border border-black/10 px-3 py-2 text-sm"
          onClick={() => this.setState({ error: null })}
        >
          다시 시도
        </button>
      </section>
    );
  }
}
```

- [ ] **Step 4: 2단으로 마운트한다**

`router.tsx` — `<Routes>` 를 감싼다:

```tsx
        <ErrorBoundary>
          <Routes>
            {/* ... 기존 라우트 ... */}
          </Routes>
        </ErrorBoundary>
```

`AppShell.tsx` — `{children}` 을 감싸되 **경로를 key 로** 준다:

```tsx
import { useLocation } from "react-router-dom";
// ...
  const { pathname } = useLocation();
// ...
        {/* key 가 없으면 AppShell 은 모든 보호 라우트에서 같은 위치의 같은 컴포넌트라
            한 번 에러 상태에 빠지면 화면을 옮겨도 풀리지 않는다. */}
        <ErrorBoundary key={pathname}>{children}</ErrorBoundary>
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd frontend && npx vitest run` 그리고 `npx tsc -b`
Expected: 전체 PASS

`ScanPaths.tsx`와 `ScanPaths.test.tsx`에 "경계가 없다"는 취지의 주석이 있으면 지워라.

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/app frontend/src/features
git commit -m "feat(portal): 앱 전역 ErrorBoundary (경로 key 로 자동 해제)"
```

---

### Task 5: react-router 6 → 7

**Files:**
- Modify: `frontend/package.json`, `frontend/package-lock.json`

`npm audit` 실측: moderate 2건(`GHSA-wrjc-x8rr-h8h6`, `GHSA-337j-9hxr-rhxg`),
영향 범위 `6.0.0 - 7.17.0`, **6.x 에 수정본 없음** → `>= 7.18.0` 메이저 업그레이드가 유일한 수정.

이 앱은 데이터 라우터·loader·splat 을 쓰지 않고, 사용 중인 API(`BrowserRouter`/`Routes`/
`Route`/`Navigate`/`NavLink`/`Link`/`useParams`/`useNavigate`/`useLocation`)는 v7 에서 그대로다.
현실적 여파는 `v7_startTransition` 이 MemoryRouter 테스트 타이밍을 바꾸는 것 하나다.

- [ ] **Step 1: 현재 상태를 기록한다**

Run: `cd frontend && npx vitest run 2>&1 | tail -5` 그리고 `npm audit 2>&1 | tail -20`
업그레이드 전 테스트 수와 advisory 목록을 보고서에 적어라 — 비교 기준이다.

- [ ] **Step 2: 업그레이드한다**

Run: `cd frontend && npm install react-router-dom@^7.18.0`

- [ ] **Step 3: 타입체크와 테스트**

Run: `cd frontend && npx tsc -b` 그리고 `npx vitest run`
Expected: 타입 에러 0, **Step 1과 같은 테스트 수가 전부 PASS**

깨지면 고쳐라. `v7_startTransition` 타이밍이 원인이면 테스트의 `findBy*`/`waitFor` 사용을
점검해라 — 동기 `getBy*` 로 즉시 단언하던 곳이 취약하다. **테스트를 약화시켜(단언 삭제,
`skip`) 통과시키지 마라.**

- [ ] **Step 4: advisory 가 사라졌는지 확인한다**

Run: `cd frontend && npm audit 2>&1 | tail -20`
Expected: react-router 관련 advisory 없음. vite/vitest/esbuild dev 전용 advisory는 남아 있어도 된다(범위 밖).

- [ ] **Step 5: 커밋**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src
git commit -m "chore(portal): react-router 7 업그레이드 (moderate advisory 2건 해소)"
```

---

### Task 6: `events` 활성화 — 기록 경로

**Files:**
- Create: `src/dms/repositories/observability.py`
- Modify: `src/dms/repositories/__init__.py`
- Modify: `src/dms/migrations.py`
- Modify: `src/dms/planner.py`, `src/dms/stepper.py`, `src/dms/repositories/data_jobs.py`
- Modify: `src/dms/retention.py`, `src/dms/controller.py`, `src/dms/config.py`
- Test: `tests/test_observability.py`

**Interfaces:**
- Produces:
  - `ObservabilityRepository.record_event(*, component, severity, event_type, message=None, payload=None, request_id=None) -> None`
    — **절대 예외를 올리지 않는다.** 실패하면 조용히 삼킨다.
  - `.events_for_request(request_id, limit=100) -> list[dict]` — `payload`는 dict로 복원
  - `.prune_events(cutoff, batch_size=5000) -> int`
  - `prune_events_once(repos, *, retention_days, now_iso=None, batch_size=5000) -> int` in `retention.py`
  - 설정 `DMS_EVENT_RETENTION_DAYS` → `event_retention_days` (기본 30, `_SERVER_INT_KEYS`)
  - 마이그레이션에 `CREATE INDEX IF NOT EXISTS idx_events_at ON events (at)`

**기록할 다섯 곳 — 전이가 남지 않는 실패만.** 전이가 남는 것은 절대 쓰지 마라(중복 노이즈 + 핫 경로 쓰기 2배):

| 지점 | component | event_type | severity |
|---|---|---|---|
| `planner.py` 항목별 예외 삼킴 | `planner` | `plan_error` | `error` |
| `stepper.py` 항목별 예외 삼킴 | `stepper` | `step_error` | `error` |
| `stepper.py` best-effort terminate 의 `ExecutionError` 삼킴 | `stepper` | `terminate_failed` | `warning` |
| `data_jobs.py` 종단 가드가 전이를 버리는 지점 | `stepper` | `terminal_guard_skip` | `info` |
| `stepper.py` 아티팩트 요약을 못 읽어 sentinel 로 덮는 지점 | `stepper` | `summary_unreadable` | `warning` |

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_observability.py`:

```python
import pytest
from dms.db import Database
from dms.migrations import migrate
from dms.repositories import Repositories


@pytest.fixture
def repos(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    return Repositories(db)


def test_record_and_read_back_with_payload(repos):
    repos.observability.record_event(
        component="planner", severity="error", event_type="plan_error",
        message="boom", payload={"exc": "KeyError"}, request_id="r1")
    rows = repos.observability.events_for_request("r1")
    assert len(rows) == 1
    assert rows[0]["component"] == "planner"
    assert rows[0]["event_type"] == "plan_error"
    assert rows[0]["payload"] == {"exc": "KeyError"}   # dict 로 복원된다


def test_record_event_never_raises(repos):
    # 진단 기록 실패가 업무 경로를 죽이면 안 된다 -- 이것이 이 저장소의 유일한 계약이다.
    class _Boom:
        def execute(self, *a, **k): raise RuntimeError("db down")
        def query(self, *a, **k): raise RuntimeError("db down")
        def query_one(self, *a, **k): raise RuntimeError("db down")
    from dms.repositories.observability import ObservabilityRepository
    ObservabilityRepository(_Boom()).record_event(
        component="planner", severity="error", event_type="x")   # 예외가 나오면 실패


def test_events_are_scoped_to_the_request(repos):
    repos.observability.record_event(component="planner", severity="error",
                                     event_type="a", request_id="r1")
    repos.observability.record_event(component="planner", severity="error",
                                     event_type="b", request_id="r2")
    assert [e["event_type"] for e in repos.observability.events_for_request("r1")] == ["a"]


def test_null_request_id_is_allowed_and_excluded_from_request_scope(repos):
    repos.observability.record_event(component="stepper", severity="info",
                                     event_type="loop_tick")
    assert repos.observability.events_for_request("r1") == []


def test_prune_events_removes_only_old_rows(repos):
    repos.observability.record_event(component="planner", severity="error",
                                     event_type="old", request_id="r1")
    assert repos.observability.prune_events("2999-01-01T00:00:00Z") == 1
    assert repos.observability.events_for_request("r1") == []
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_observability.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dms.repositories.observability'`

- [ ] **Step 3: 저장소를 만든다**

`src/dms/repositories/observability.py`:

```python
"""진단 이벤트. state_transitions 가 담지 못하는 것 -- **일어나지 않은 전이** -- 만 기록한다.

계약이 하나 있다: record_event 는 절대 예외를 올리지 않는다. 이것은 진단 채널이고,
진단 기록 실패가 상태 전이를 롤백하거나 컨트롤러 루프 틱을 죽이면 본말이 전도된다."""
import logging

from ..db import Database, dump_json, load_json, utc_now_iso

logger = logging.getLogger(__name__)


class ObservabilityRepository:
    def __init__(self, db: Database):
        self._db = db

    def record_event(self, *, component, severity, event_type, message=None,
                     payload=None, request_id=None) -> None:
        # 업무 트랜잭션 밖에서 단독 INSERT 한다 -- 호출자의 트랜잭션에 참여하면
        # 진단 실패가 업무 변경을 되돌린다.
        try:
            self._db.execute(
                """INSERT INTO events (request_id, component, severity, event_type,
                       message, payload, at)
                   VALUES (:r, :c, :s, :t, :m, :p, :at)""",
                {"r": request_id, "c": component, "s": severity, "t": event_type,
                 "m": message, "p": dump_json(payload) if payload is not None else None,
                 "at": utc_now_iso()})
        except Exception as exc:
            logger.warning("record_event failed type=%s: %s", event_type, exc)

    def events_for_request(self, request_id: str, limit: int = 100) -> list[dict]:
        rows = self._db.query(
            """SELECT id, request_id, component, severity, event_type, message,
                      payload, at
               FROM events WHERE request_id = :r ORDER BY id ASC LIMIT :n""",
            {"r": request_id, "n": limit})
        out = []
        for row in rows:
            e = dict(row)
            e["payload"] = load_json(e.get("payload"))
            out.append(e)
        return out

    def prune_events(self, cutoff: str, batch_size: int = 5000) -> int:
        rows = self._db.query(
            "SELECT id FROM events WHERE at < :c ORDER BY id ASC LIMIT :n",
            {"c": cutoff, "n": batch_size})
        if not rows:
            return 0
        ids = {f"i{n}": r["id"] for n, r in enumerate(rows)}
        placeholders = ", ".join(f":{k}" for k in ids)
        self._db.execute(f"DELETE FROM events WHERE id IN ({placeholders})", ids)
        return len(rows)
```

`repositories/__init__.py`에 `self.observability = ObservabilityRepository(db)` 를 등록해라.

`migrations.py`의 `stmts` 에 시간 인덱스를 더해라 — purge 와 시간순 조회가 지금은
`(request_id, id)` 인덱스로 커버되지 않는다:

```python
        "CREATE INDEX IF NOT EXISTS idx_events_at ON events (at)",
```

- [ ] **Step 4: 다섯 지점을 배선한다**

`planner.py`의 항목별 `except Exception` 블록에서 stderr 출력 **다음에** 추가:

```python
                self._repos.observability.record_event(
                    component="planner", severity="error", event_type="plan_error",
                    message=f"{type(exc).__name__}: {exc}"[:500], request_id=rid)
```

`stepper.py`의 항목별 `except Exception` 블록에도 같은 방식으로 (`component="stepper"`,
`event_type="step_error"`). `request_id` 는 `job` 에서 얻어라.

`stepper.py`의 best-effort terminate `except ExecutionError` 에서:

```python
        except ExecutionError as exc:
            # best-effort -- 잡은 이미 종단이라 더 기록할 상태가 없다. 그래도 고아
            # 리소스가 남았을 수 있으니 진단 채널에는 남긴다.
            self._repos.observability.record_event(
                component="stepper", severity="warning", event_type="terminate_failed",
                message=exc.reason_code, payload={"ref": ref},
                request_id=job.get("request_id"))
```

`data_jobs.py`의 종단 가드 `return` 직전에도 같은 방식으로 (`event_type="terminal_guard_skip"`,
`severity="info"`). **여기서는 `self._db` 만 있고 repos 가 없다** — `ObservabilityRepository(self._db)`
를 그 자리에서 만들어 쓰거나, `DataJobsRepository.__init__` 에 선택적 협력자를 받게 해라.
어느 쪽이든 저장소 규약(`__init__(self, db)`)을 깨지 않는 방법을 골라 근거를 보고서에 적어라.

`stepper.py` 의 아티팩트 요약 sentinel 지점도 같은 방식으로
(`event_type="summary_unreadable"`, `severity="warning"`).

- [ ] **Step 5: purge 루프를 더한다**

`src/dms/retention.py`:

```python
def prune_events_once(repos: Repositories, *, retention_days: int,
                      now_iso: str | None = None, batch_size: int = 5000) -> int:
    now = now_iso or utc_now_iso()
    cutoff = iso_plus(now, -retention_days * 86400)
    return repos.observability.prune_events(cutoff, batch_size=batch_size)
```

`config.py` 에 `DMS_EVENT_RETENTION_DAYS` → `event_retention_days` (기본 30) 를
`_SERVER_INT_KEYS` 와 dataclass 필드에 더하고, `controller.py` 의 기존 `retention` 루프
람다에서 `prune_agent_reports_once` 와 **함께** 호출해라 (새 루프를 만들지 마라 — 같은
성격의 배치 삭제다).

- [ ] **Step 6: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_observability.py tests/test_planner.py tests/test_stepper.py -q`
(파일명은 `ls tests` 로 확인)
Expected: PASS

- [ ] **Step 7: 전체 스위트**

Run: `.venv/bin/python -m pytest -q`
Expected: 전부 PASS

- [ ] **Step 8: 커밋**

```bash
git add src/dms tests/test_observability.py
git commit -m "feat(observability): 전이가 남지 않는 실패를 events 로 기록"
```

---

### Task 7: 요청 상세에 이벤트 노출

**Files:**
- Modify: `src/dms/api/routes_requests.py`
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/features/jobs/RequestDetail.tsx`
- Test: `tests/test_api_requests.py` (파일명 확인), `frontend/src/features/jobs/RequestDetail.test.tsx`

**Interfaces:**
- Consumes: `repos.observability.events_for_request` (Task 6).
- Produces: `GET /api/user/requests/{request_id}` 응답에 `events: [...]` 추가.

응답 필드는 `transitions` 옆에 붙인다. 소유권 검사는 그 엔드포인트의 기존 검사를 그대로
타므로 **추가 인가 로직을 넣지 마라** — 다만 리뷰가 확인할 수 있게 남의 요청 이벤트가
새지 않는 테스트를 하나 둬라.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

백엔드:

```python
def test_request_detail_carries_events(admin_client, repos):
    rid = _pending_request(repos)                 # 그 파일의 기존 헬퍼
    repos.observability.record_event(
        component="planner", severity="error", event_type="plan_error",
        message="boom", request_id=rid)
    body = admin_client.get(f"/api/user/requests/{rid}").json()
    assert [e["event_type"] for e in body["events"]] == ["plan_error"]
    assert body["events"][0]["message"] == "boom"


def test_request_detail_events_are_scoped(admin_client, repos):
    rid = _pending_request(repos)
    repos.observability.record_event(component="planner", severity="error",
                                     event_type="other", request_id="someone-else")
    assert admin_client.get(f"/api/user/requests/{rid}").json()["events"] == []
```

프론트 (`RequestDetail.test.tsx`) — MSW 응답에 `events`를 넣고 화면에 뜨는지:

```tsx
  it("진단 이벤트를 보여준다", async () => {
    // events 는 transitions 와 달리 "일어나지 않은 전이"를 담는다 -- 이것이 안 보이면
    // 운영자는 stderr 를 뒤져야 한다
    renderDetail({ events: [{ id: 1, component: "planner", severity: "error",
                              event_type: "plan_error", message: "boom",
                              payload: null, at: "2026-08-06T00:00:00Z" }] });
    expect(await screen.findByText("plan_error")).toBeInTheDocument();
    expect(screen.getByText("boom")).toBeInTheDocument();
  });
```

기존 픽스처/헬퍼 이름에 맞춰라.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_requests.py -q` 및
`cd frontend && npx vitest run src/features/jobs/RequestDetail.test.tsx`
Expected: 둘 다 FAIL

- [ ] **Step 3: API에 붙인다**

`routes_requests.py`의 요청 상세 핸들러에서 `row["transitions"]` 를 채우는 곳 옆에:

```python
    row["events"] = repos.observability.events_for_request(request_id)
```

- [ ] **Step 4: 타입과 화면**

`frontend/src/lib/types.ts`:

```ts
export interface DiagEvent {
  id: number; component: string; severity: string; event_type: string;
  message: string | null; payload: unknown; at: string;
}
```

`RequestDetail`의 요청 타입에 `events?: DiagEvent[]` 를 더하고, 타임라인 아래에 카드 하나로
렌더한다. **`Array.isArray` 로 방어**하고, 비어 있으면 카드 자체를 그리지 마라(노이즈).
`severity`에 따라 색을 달리하되 기존 `text-bad`/`text-muted` 유틸리티만 쓴다.

- [ ] **Step 5: 통과를 확인한다**

Run: `.venv/bin/python -m pytest -q` 및 `cd frontend && npx vitest run` 및 `npx tsc -b`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add src/dms frontend/src tests
git commit -m "feat(portal): 요청 상세에 진단 이벤트 표시"
```

---

### Task 8: 감사 actor 구분

**Files:**
- Modify: `src/dms/api/auth.py`
- Modify: `src/dms/api/routes_control.py`, `routes_policies.py`, `routes_denylist.py`, `routes_accounts.py`, `routes_storages.py`, `routes_builds.py`
- Test: `tests/test_api_auth.py`(기존), `tests/test_api_control.py`(기존)

**Interfaces:**
- Produces:
  - `Identity` 에 `auth: str` 필드 (`"session"` | `"token"`)
  - `audit_actor(identity) -> str` — 세션이면 `identity.actor`, 토큰이면 `f"token:{identity.actor}"`

**절대 하지 마라:** `Identity.actor` 자체에 접두를 붙이는 것. 에이전트 노드 인증
(`routes_agent.py`), 특권 요청자 판정(`routes_requests.py` 의 `settings.privileged_requesters`),
`requester_id` → LDAP/denylist 해석, 스캔 경로 소유권, 계정 자기잠금 가드가 전부 그 값을 쓴다.

`/api/auth/me` 는 **원시 actor** 를 그대로 돌려준다 (프론트 헤더 표시, 기존 테스트).

적용 라우트: 감사 로그를 쓰는 admin 뮤테이션만 — control-state, policies, denylist(deny/allow),
accounts(role/disabled/create), storages(create/update/delete), builds(submit).
`routes_requests`/`routes_batches`/`routes_jobs`/`routes_scan_paths`/`routes_agent` 는
**그대로 둔다** (감사 로그를 쓰지 않거나 actor 의미가 다르다).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_token_auth_audit_actor_is_prefixed(admin_client_token, repos):
    # 공유 토큰은 스크립트다. 사람 admin 과 감사 로그에서 구분되지 않으면
    # "누가 정책을 바꿨나"에 답할 수 없다.
    admin_client_token.put("/api/admin/control-state",
                           json={"maintenance": False, "drain": False, "reason": None})
    entry = repos.control.audit_entries(limit=1)[0]
    assert entry["actor"].startswith("token:")


def test_session_auth_audit_actor_is_bare(admin_client, repos):
    admin_client.put("/api/admin/control-state",
                     json={"maintenance": False, "drain": False, "reason": None})
    entry = repos.control.audit_entries(limit=1)[0]
    assert ":" not in entry["actor"]


def test_reserved_prefix_in_actor_header_is_rejected(client):
    # 접두가 서버 소유의 출처 표식이 아니게 되면 의미가 없다
    r = client.put("/api/admin/control-state",
                   headers={"Authorization": f"Bearer {TOKEN}",
                            "x-dms-actor": "token:alice"},
                   json={"maintenance": False, "drain": False, "reason": None})
    assert r.status_code == 401


def test_auth_me_still_returns_the_raw_actor(admin_client_token):
    body = admin_client_token.get("/api/auth/me").json()
    assert ":" not in body["actor"]
```

픽스처 이름은 `tests/test_api_auth.py` / `tests/test_api_control.py` 의 실제 패턴에 맞춰라.
공유 토큰 클라이언트 픽스처가 없으면 그 파일들의 방식대로 하나 만들어라.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_auth.py tests/test_api_control.py -q`
Expected: FAIL

- [ ] **Step 3: `auth.py` 를 고친다**

`Identity` 에 `auth` 필드를 더하고(기본값을 줘서 기존 생성자 호출을 깨지 마라),
두 반환 지점에서 각각 `"session"` / `"token"` 을 넣는다.

`x-dms-actor` 가 `token:` 으로 시작하면 `401 invalid_actor` 로 거절한다.
헤더가 없을 때의 기본값(`shared-token`)은 그대로 둬서 `token:shared-token` 이 되게 한다 —
빈 `token:` 이 나오면 안 된다.

에이전트가 보내는 `x-dms-actor: node:<name>` 은 `token:` 으로 시작하지 않으므로 영향 없다.

```python
def audit_actor(identity: Identity) -> str:
    """감사 로그에 쓸 actor. 공유 토큰 호출은 사람이 아니라 스크립트이므로
    출처를 표시한다. Identity.actor 자체는 건드리지 않는다 -- 그 값은 에이전트 인증,
    특권 판정, LDAP/denylist 해석, 소유권 검사가 쓴다."""
    return identity.actor if identity.auth == "session" else f"token:{identity.actor}"
```

- [ ] **Step 4: admin 뮤테이션 라우트를 바꾼다**

위 목록의 라우트에서 저장소로 넘기는 `actor=identity.actor` 를 `actor=audit_actor(identity)` 로 바꾼다.

- [ ] **Step 5: 통과를 확인한다**

Run: `.venv/bin/python -m pytest -q`
Expected: 전부 PASS. `tests/test_api_auth.py` 가 `/api/auth/me` 에 대해 원시 actor 를
단언하고 있으면 그대로 통과해야 한다 — 깨진다면 `/api/auth/me` 를 잘못 건드린 것이다.

- [ ] **Step 6: 커밋**

```bash
git add src/dms/api tests
git commit -m "feat(audit): 토큰 인증 actor 를 token: 접두로 구분"
```

---

## Self-Review

**1. 스펙 커버리지**

| 설계 절 | 담당 태스크 |
|---|---|
| §2 `reasonText()`, 매핑 확장, 복합 코드, 죽은 키, 커버리지 테스트 | Task 1 |
| §2 렌더 지점 통일 | Task 2 |
| §3 배치 항목 사유 의미 수정 | Task 3 |
| §4 ErrorBoundary 2단 + pathname key | Task 4 |
| §7 react-router 6→7 | Task 5 |
| §5 events 기록 5지점 + purge + 시간 인덱스 | Task 6 |
| §5 요청 상세 노출 | Task 7 |
| §6 감사 actor 구분 | Task 8 |
| §8 범위 밖 | Global Constraints 에 명시 |
| §9 실증 | 플랜 실행 후 별도 수행 |

**2. 플레이스홀더 점검** — "적절히 처리한다" 류 없음. 코드 단계마다 실제 코드가 있다.
Task 4 Step 1의 "실제로 크래시하는 조합을 찾아 쓰라"와 Task 6 Step 4의
`data_jobs.py` 협력자 주입 방식은 의도적으로 구현자 판단에 맡긴 지점이며, 각각 근거를
보고서에 적도록 요구했다.

**3. 타입 일관성** — `reasonText`는 Task 1에서 정의하고 2·3·7이 같은 이름으로 쓴다.
`ObservabilityRepository`의 메서드 이름(`record_event`/`events_for_request`/`prune_events`)은
Task 6에서 정의하고 6·7이 그대로 쓴다. `audit_actor`/`Identity.auth`는 Task 8 안에서 닫힌다.
`DiagEvent` 타입 필드는 `events_for_request`가 돌려주는 컬럼과 1:1이다.

**알려진 위험:** Task 5의 react-router 메이저 업그레이드가 MemoryRouter 기반 테스트
~25개의 타이밍을 바꿀 수 있다. 합격 조건을 "Step 1과 같은 테스트 수가 전부 통과"로 못박아
두었고, 테스트를 약화시켜 통과시키는 것을 명시적으로 금지했다.
