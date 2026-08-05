# DMS 포탈 슬라이스 6 (잡 제출 표면 완성) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 포탈에서 sync뿐 아니라 **rm도 제출**할 수 있고, 스토리지는 **오타 불가능한 드롭다운**으로 고르며, 관리자는 **scan 실행**과 **다른 사용자 명의(owner_username) 제출**을 할 수 있다.

**Architecture:** 백엔드 신규는 사용자용 스토리지 목록 API 1건뿐(`GET /api/user/storages`, 경로 미노출). 나머지는 프론트 — `SubmitSync`를 연산 선택형 `SubmitJob`으로 대체하고, 관리자 전용 `SubmitScan`을 추가한다. rm/sync의 preview→confirm은 백엔드가 이미 동일하게 태우므로 기존 `ConfirmDialog`를 재사용한다.

**Tech Stack:** Python 3.11 / FastAPI / pytest · React 18 + Vite 5 + TS + Tailwind + Radix + TanStack Query v5 + Vitest · Testing Library · MSW 2

## Global Constraints

- 설계 문서 `docs/superpowers/specs/2026-08-05-dms-portal-submit-surface-slice6-design.md`가 상위 규칙이다. 충돌 시 `2026-08-02-dms-clean-slate-design.md`가 이긴다.
- **`GET /api/user/storages`는 경로를 노출하지 않는다** — `storage_name`, `backend_type`, `status` 세 필드만. `mount_path`·`managed_root`·`status_detail`은 절대 포함하지 않는다.
- 활성(`enabled = 1`) 스토리지만 반환하되 **Degraded도 포함**한다(어드미션 판단은 planner의 몫).
- `routes_storages.py`의 **기존 admin 라우터 의존성을 건드리지 않는다** — 같은 파일에 별도 `user_router`를 둔다.
- `POST /api/user/requests`의 검증·특권 게이트·payload 생성은 **변경하지 않는다**. 프론트가 계약에 맞춘다.
- rm은 파괴적이다: 폼에서 `options.recursive`를 **필수**로 강제하고, 상호배타 옵션(rm `stat`+`lite`, scan `verbose`+`quiet`)은 제출 전에 막는다.
- 특권 필드(`owner_username`)는 **관리자에게만** 노출한다.
- scan 제출은 **관리자 전용** 화면에서만 한다.
- 한국어 UI 문자열. 이모지 금지. 왼쪽 점 상태 뱃지 금지. 슬라이스 1의 `Card`/`Button`/`Dialog` 재사용.
- 백엔드 테스트는 `.venv/bin/python -m pytest`(plain `python3`는 이 환경에서 깨져 있다). 프론트는 `frontend/`에서 `npm test`, `npx tsc -b`.
- 커밋은 태스크 단위, 각 태스크는 테스트 GREEN 상태로 끝난다.

---

### Task 1: 사용자용 스토리지 목록 API

**Files:**
- Modify: `src/dms/api/routes_storages.py` (별도 `user_router` 추가)
- Modify: `src/dms/api/app.py` (라우터 등록)
- Test: `tests/test_api_user_storages.py` (신규)

**Interfaces:**
- Consumes: `StoragesRepository.list()` (`src/dms/repositories/storages.py:91`, `SELECT *`를 `storage_name` 오름차순으로 반환), `require_user` (`src/dms/api/auth.py`)
- Produces: `GET /api/user/storages -> [{storage_name, backend_type, status}]`. Task 2의 훅이 쓴다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_user_storages.py`. 스토리지를 만드는 방법은 `tests/test_api_storages.py`를 열어 그 방식을 그대로 따른다(관리자 헤더 `{"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}`로 POST 하거나 리포지토리 직접 호출 — 그 파일이 하는 대로).

```python
ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}
FORBIDDEN_FIELDS = ("mount_path", "managed_root", "status_detail")


def _login_user(client):
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})


def test_requires_login(client):
    assert client.get("/api/user/storages").status_code == 401


def test_returns_minimal_fields_without_paths(client, db):
    # 스토리지 2개를 만든다 (기존 test_api_storages.py 방식 그대로)
    ...
    _login_user(client)
    rows = client.get("/api/user/storages").json()
    assert rows, "활성 스토리지가 있어야 한다"
    for r in rows:
        assert set(r) == {"storage_name", "backend_type", "status"}
        for f in FORBIDDEN_FIELDS:
            assert f not in r


def test_excludes_disabled_storages(client, db):
    # 하나를 enabled=0 으로 만든 뒤(admin PUT 사용) 목록에서 빠지는지
    ...


def test_sorted_by_name(client, db):
    # zz, aa 순서로 만들어도 aa, zz 로 나온다
    ...


def test_admin_can_also_read(client):
    # 관리자 Bearer 로도 200
    assert client.get("/api/user/storages", headers=ADMIN).status_code == 200
```

`...` 부분은 `tests/test_api_storages.py`의 실제 생성 방식으로 채운다. **필드 집합을 정확히 단언하는 것이 이 태스크의 핵심이다** — 경로가 새면 즉시 실패해야 한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_user_storages.py -v`
Expected: FAIL — 404 (라우트 없음)

- [ ] **Step 3: 라우트를 구현한다**

`src/dms/api/routes_storages.py`에 추가한다. **기존 `router` 정의와 그 의존성은 손대지 않는다.** 파일 상단 import에 `require_user`를 더하고, 파일 끝에 다음을 둔다:

```python
# 사용자용 읽기 전용 목록. 제출 폼 드롭다운이 유일한 소비자다 — 경로(mount_path/
# managed_root)와 운영 내부 정보(status_detail)는 담지 않는다. 비활성 스토리지는
# 고를 수 없어야 하므로 제외하고, Degraded는 남긴다(어드미션 판단은 planner의 몫).
user_router = APIRouter()


@user_router.get("/api/user/storages")
def list_user_storages(request: Request, identity: Identity = Depends(require_user)):
    rows = request.app.state.repos.storages.list()
    return [{"storage_name": r["storage_name"], "backend_type": r["backend_type"],
             "status": r["status"]}
            for r in rows if r["enabled"] == 1]
```

`src/dms/api/app.py`에서 기존 `storages_router` 등록 옆에 `user_router`도 등록한다. import 이름이 충돌하지 않도록 `from .routes_storages import router as storages_router, user_router as user_storages_router` 같은 형태로 기존 스타일에 맞춘다(파일의 실제 import 방식을 보고 따를 것). **SPA 캐치올보다 앞이어야 한다** — 기존 등록 블록 안에 넣으면 된다.

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_user_storages.py -v`
Expected: PASS

- [ ] **Step 5: 회귀 확인**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 전부 통과(기준선 440 + 신규). 특히 `tests/test_api_storages.py`가 그대로 통과해야 한다 — admin 라우터를 건드리지 않았다는 증거다.

- [ ] **Step 6: 커밋**

```bash
git add src/dms/api/routes_storages.py src/dms/api/app.py tests/test_api_user_storages.py
git commit -m "feat(api): user-facing storage list without path fields"
```

---

### Task 2: 프론트 타입 · reason 코드 · 훅

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/features/storages/useUserStorages.ts`
- Modify: `frontend/src/features/jobs/useJobs.ts` (`useSubmitSync` → `useSubmitRequest`)
- Test: `frontend/src/features/storages/useUserStorages.test.tsx` (신규)

**Interfaces:**
- Consumes: `apiGet`/`apiSend` (`frontend/src/lib/api.ts`), Task 1의 엔드포인트, `POST /api/user/requests`
- Produces: `UserStorage` 타입, `useUserStorages()`, `useSubmitRequest()`. Task 3·4가 쓴다.

- [ ] **Step 1: 타입을 추가한다**

`frontend/src/lib/types.ts`에 기존 스타일로:

```ts
export interface UserStorage { storage_name: string; backend_type: string; status: string }
```

- [ ] **Step 2: reason 코드를 추가한다**

`frontend/src/lib/api.ts`의 `REASON_MESSAGES`에 추가한다(제출 경로에서 실제로 나올 수 있는 것들):

```ts
  rm_recursive_required: "삭제는 재귀 옵션이 필요합니다",
  rm_root_forbidden: "관리 루트 자체는 삭제할 수 없습니다",
  unsafe_path: "경로가 올바르지 않습니다",
  unknown_option: "지원하지 않는 옵션입니다",
  invalid_option: "옵션 값이 올바르지 않습니다",
  invalid_priority_value: "우선순위 값이 올바르지 않습니다",
  storage_missing: "등록되지 않은 스토리지입니다",
  storage_disabled: "비활성 스토리지입니다",
  storage_not_ready: "스토리지가 준비되지 않았습니다",
  missing_storage: "스토리지를 선택하세요",
  missing_source_storage: "소스 스토리지를 선택하세요",
  missing_destination_storage: "목적지 스토리지를 선택하세요",
  sync_destination_inside_source: "목적지가 소스 하위 경로일 수 없습니다",
  invalid_owner_username: "사용자명이 올바르지 않습니다",
  invalid_operation: "지원하지 않는 연산입니다",
```

**주의:** `invalid_priority`는 이미 슬라이스 4에서 추가돼 있다 — 중복 키를 만들지 말고, 없는 것만 추가한다. 위 목록에서 이미 있는 키는 건너뛴다.

- [ ] **Step 3: 사용자 스토리지 훅을 만든다**

`frontend/src/features/storages/useUserStorages.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { UserStorage } from "../../lib/types";
export const useUserStorages = () =>
  useQuery({ queryKey: ["user-storages"],
             queryFn: () => apiGet<UserStorage[]>("/api/user/storages") });
```

- [ ] **Step 4: 제출 훅을 일반화한다**

`frontend/src/features/jobs/useJobs.ts`에서 `SyncBody`/`useSubmitSync`를 다음으로 **대체**한다(기존 이름은 남기지 않는다 — 유일한 호출부인 `SubmitSync.tsx`가 Task 3에서 사라진다):

```ts
export interface SubmitBody {
  operation: "sync" | "rm" | "scan";
  storage?: string; target?: string;
  source_storage?: string; source?: string;
  destination_storage?: string; destination?: string;
  options: Record<string, boolean | number>;
  priority: string;
  owner_username?: string;
}
export const useSubmitRequest = () =>
  useMutation({
    mutationFn: (b: SubmitBody) =>
      apiSend<{ request_id: string; state: string }>("POST", "/api/user/requests", b),
  });
```

- [ ] **Step 5: 훅 테스트를 쓴다**

`frontend/src/features/storages/useUserStorages.test.tsx` — `frontend/src/features/policies/usePolicies.test.tsx`의 구조(renderHook + MSW + `retry: false`)를 따른다. 목록이 그대로 반환되는지 확인한다.

- [ ] **Step 6: 테스트·타입체크**

Run(from `frontend/`): `npx vitest run src/features/storages && npx tsc -b`
Expected: PASS. **`tsc`가 `SubmitSync.tsx`의 `useSubmitSync` 참조로 실패할 수 있다** — 그건 정상이며 Task 3에서 그 파일을 대체하면서 해소된다. 이 태스크에서는 `SubmitSync.tsx`가 새 훅을 쓰도록 최소 수정해 `tsc`를 통과시킨다(연산 `"sync"`를 명시해 넘기는 한 줄 변경).

- [ ] **Step 7: 전체 테스트 확인 후 커밋**

Run: `npm test && npx tsc -b` → 전부 PASS, tsc 0

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/features/storages/useUserStorages.ts frontend/src/features/storages/useUserStorages.test.tsx frontend/src/features/jobs/useJobs.ts frontend/src/features/jobs/SubmitSync.tsx
git commit -m "feat(portal): user storage hook and generalized submit hook"
```

---

### Task 3: 제출 화면 통합 (sync + rm)

**Files:**
- Create: `frontend/src/features/jobs/SubmitJob.tsx`
- Delete: `frontend/src/features/jobs/SubmitSync.tsx`
- Modify: `frontend/src/app/router.tsx` (`/jobs/new` → `SubmitJob`)
- Test: `frontend/src/features/jobs/SubmitJob.test.tsx` (신규)
- Delete: `frontend/src/features/jobs/SubmitSync.test.tsx`

**Interfaces:**
- Consumes: `useSubmitRequest`·`SubmitBody` (Task 2), `useUserStorages` (Task 2), `useMe` (`frontend/src/features/auth/useAuth.ts`), `Card`·`Button`, `ApiError`
- Produces: `SubmitJob` (Task 5의 라우트가 이미 가리키게 됨)

- [ ] **Step 1: 화면을 만든다**

`SubmitJob.tsx` 요건. 기존 `SubmitSync.tsx`의 폼 마크업(`field` 클래스 상수, `Card`, `grid grid-cols-2 gap-3` 레이아웃)을 그대로 계승한다.

- 상단에 연산 select: `aria-label="연산"`, 값 `sync`|`rm`, 기본 `sync`.
- **스토리지 드롭다운 컴포넌트**를 파일 안에 작은 헬퍼로 둔다:

```tsx
function StoragePicker({ label, value, onChange, storages, loading }: {
  label: string; value: string; onChange: (v: string) => void;
  storages: UserStorage[]; loading: boolean;
}) {
  return (
    <label className="text-sm">{label}
      <select aria-label={label} className={field} value={value} disabled={loading}
              onChange={(e) => onChange(e.target.value)}>
        <option value="">{loading ? "불러오는 중…" : "선택하세요"}</option>
        {storages.map((s) => (
          <option key={s.storage_name} value={s.storage_name}>
            {s.storage_name} ({s.status})
          </option>
        ))}
      </select>
    </label>
  );
}
```

- **sync 필드**: 소스 스토리지(picker)·소스 경로, 목적지 스토리지(picker)·목적지 경로. 옵션 체크박스 `delete`·`contents`·`direct`·`quiet` (각 `aria-label`은 그 이름 그대로).
- **rm 필드**: 스토리지(picker)·대상 경로. 옵션 체크박스 `recursive`(기본 **체크됨**)·`stat`·`lite`·`quiet`.
- **rm 경고 배너**: 연산이 `rm`일 때 폼 상단에 `text-bad`로 "삭제는 되돌릴 수 없습니다. 미리보기에서 대상을 확인한 뒤 확인해야 실행됩니다."
- **제출 차단 규칙**(버튼 `disabled`):
  - 제출 중(`submit.isPending`)
  - `rm`인데 `recursive`가 해제됨 → 버튼 비활성 + "재귀 옵션이 필요합니다" 안내
  - `rm`인데 `stat`과 `lite`가 동시 체크됨 → 버튼 비활성 + "stat과 lite는 함께 쓸 수 없습니다" 안내
- 우선순위 select(기존 그대로).
- **특권 필드**: `useMe()`의 `data?.role === "admin"`일 때만 `aria-label="다른 사용자로 실행"` 텍스트 입력을 렌더. 값이 비어 있으면 바디에 `owner_username`을 **넣지 않는다**.
- 제출 바디: 연산에 따라 필드를 골라 담고, 옵션은 **체크된 것만** `true`로 담는다(체크 안 된 불리언을 `false`로 보내지 않는다 — allowlist는 통과하지만 payload를 지저분하게 만든다).
- 성공 시 `nav(\`/jobs/${r.request_id}\`)`.
- `submit.isError`면 `(submit.error as ApiError).message` 인라인.

- [ ] **Step 2: 라우트를 바꾸고 옛 화면을 지운다**

`frontend/src/app/router.tsx`의 `/jobs/new`가 `SubmitJob`을 렌더하도록 import와 element를 바꾼다. `SubmitSync.tsx`와 `SubmitSync.test.tsx`를 삭제한다(`git rm`).

- [ ] **Step 3: 테스트를 쓴다**

`SubmitJob.test.tsx`. `frontend/src/features/jobs/SubmitSync.test.tsx`를 지우기 전에 열어 MSW·렌더 패턴을 그대로 계승한다. `/api/auth/me`와 `/api/user/storages` 핸들러가 필요하다. 최소 7개:

1. 드롭다운이 API 목록으로 채워진다(옵션에 스토리지 이름이 보인다).
2. 연산을 `rm`으로 바꾸면 목적지 필드가 사라지고 대상 경로가 나타난다.
3. sync 제출 바디가 정확하다(캡처해서 `toEqual`).
4. rm 제출 바디에 `options.recursive === true`가 들어간다.
5. `recursive`를 해제하면 제출 버튼이 비활성이다.
6. `stat`+`lite` 동시 체크 시 제출 버튼이 비활성이다.
7. 특권 필드가 **비관리자에게 보이지 않고** 관리자에게 보인다(`/api/auth/me`를 role별로 스텁해 두 케이스).

- [ ] **Step 4: 테스트·타입체크**

Run(from `frontend/`): `npx vitest run src/features/jobs && npx tsc -b`
Expected: PASS, tsc 0

- [ ] **Step 5: 전체 확인 후 커밋**

Run: `npm test` → 전부 PASS

```bash
git add frontend/src/features/jobs/SubmitJob.tsx frontend/src/features/jobs/SubmitJob.test.tsx frontend/src/app/router.tsx
git rm frontend/src/features/jobs/SubmitSync.tsx frontend/src/features/jobs/SubmitSync.test.tsx
git commit -m "feat(portal): unified submit screen with rm support and storage pickers"
```

---

### Task 4: 관리자 scan 실행 화면

**Files:**
- Create: `frontend/src/features/jobs/SubmitScan.tsx`
- Test: `frontend/src/features/jobs/SubmitScan.test.tsx` (신규)

**Interfaces:**
- Consumes: `useSubmitRequest`·`useUserStorages`·`useMe` (Task 2), `Card`·`Button`, `ApiError`
- Produces: `SubmitScan` (Task 5의 라우트가 마운트)

- [ ] **Step 1: 화면을 만든다**

`SubmitScan.tsx` — `SubmitJob.tsx`의 `StoragePicker`·`field` 스타일을 그대로 쓴다(중복을 피하려면 `SubmitJob.tsx`에서 `StoragePicker`를 `export`하고 여기서 import한다).

- 제목 "scan 실행".
- 스토리지(picker)·대상 경로.
- `top_k` 숫자 입력(`aria-label="top_k"`) — **비우면 옵션에서 제외**한다. 값이 있으면 `Number(...)`로 담는다.
- 체크박스 `verbose`·`quiet` — 동시 체크 시 제출 비활성 + "verbose와 quiet은 함께 쓸 수 없습니다".
- 우선순위 select.
- 특권 필드(관리자 전용 화면이므로 항상 노출).
- 안내 한 줄: "scan은 미리보기 확인 단계가 없습니다 — 제출하면 바로 실행됩니다."
- 제출 성공 시 `/jobs/{request_id}`로 이동.

- [ ] **Step 2: 테스트를 쓴다**

`SubmitScan.test.tsx`, 최소 4개:

1. 드롭다운이 채워진다.
2. 제출 바디가 `{operation:"scan", storage, target, options:{}, priority}` 형태다(top_k 비움).
3. `top_k`에 5를 넣으면 `options.top_k === 5`(문자열 `"5"`가 아니라 숫자여야 한다).
4. `verbose`+`quiet` 동시 체크 시 제출 비활성.

- [ ] **Step 3: 테스트·타입체크**

Run(from `frontend/`): `npx vitest run src/features/jobs && npx tsc -b`
Expected: PASS, tsc 0

- [ ] **Step 4: 커밋**

```bash
git add frontend/src/features/jobs/SubmitScan.tsx frontend/src/features/jobs/SubmitScan.test.tsx frontend/src/features/jobs/SubmitJob.tsx
git commit -m "feat(portal): admin scan submission screen"
```

---

### Task 5: 라우트 · 내비 배선 + ConfirmDialog 연산 반영

**Files:**
- Modify: `frontend/src/app/router.tsx` (`/admin/scan` 추가)
- Modify: `frontend/src/app/AppShell.tsx` (admin 내비 "scan 실행")
- Modify: `frontend/src/features/jobs/ConfirmDialog.tsx` (제목을 연산에 맞춘다)
- Modify: `frontend/src/app/router.test.tsx`
- Modify: `frontend/src/features/jobs/ConfirmDialog.test.tsx`

**Interfaces:**
- Consumes: `SubmitScan` (Task 4), `RequireRole`, `AppShell`, `DataJob.operation`
- Produces: `/admin/scan` 라우트와 내비 링크

- [ ] **Step 1: 라우트를 추가한다**

`router.tsx`에서 `/admin/audit` 등 admin 라우트 옆, 캐치올(`path="*"`) **앞**에:

```tsx
        <Route path="/admin/scan" element={<RequireRole role="admin"><AppShell><SubmitScan /></AppShell></RequireRole>} />
```

- [ ] **Step 2: 내비를 추가한다**

`AppShell.tsx`의 admin 링크 목록에:

```tsx
        {isAdmin && <NavLink to="/admin/scan" className={linkCls}>scan 실행</NavLink>}
```

- [ ] **Step 3: ConfirmDialog 제목을 연산에 맞춘다**

`ConfirmDialog.tsx`는 제목이 `"sync 미리보기 확인"`으로 하드코딩돼 있는데 rm도 이 게이트를 지난다. `job.operation`을 써서 `` `${job.operation} 미리보기 확인` ``으로 바꾼다. 트리거 버튼 라벨("미리보기 확인")은 그대로 둔다 — 기존 테스트가 그 이름으로 찾는다.

- [ ] **Step 4: 테스트를 확장한다**

- `router.test.tsx`: 기존 admin 라우트 테스트와 같은 형태로 `/admin/scan`이 admin 세션에서 렌더되는지 추가한다(필요한 MSW 핸들러: `/api/auth/me`, `/api/user/storages`).
- `ConfirmDialog.test.tsx`: `operation: "rm"`인 잡에 대해 제목이 `"rm 미리보기 확인"`인지 단언하는 케이스를 추가한다.

- [ ] **Step 5: 전체 테스트·타입체크**

Run(from `frontend/`): `npm test && npx tsc -b`
Expected: 전부 PASS, tsc 0

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/app/router.tsx frontend/src/app/AppShell.tsx frontend/src/app/router.test.tsx frontend/src/features/jobs/ConfirmDialog.tsx frontend/src/features/jobs/ConfirmDialog.test.tsx
git commit -m "feat(portal): scan route and nav, operation-aware confirm dialog"
```
