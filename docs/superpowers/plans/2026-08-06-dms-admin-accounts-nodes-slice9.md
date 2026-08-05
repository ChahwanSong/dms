# DMS 포탈 슬라이스 9 (계정 관리 + 노드 대시보드) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 관리자가 포탈에서 계정의 역할을 바꾸고 비활성화할 수 있으며(비활성화는 **기존 세션까지 즉시 끊는다**), 노드의 신선도·마운트·도구·디스크 상태를 한 화면에서 본다.

**Architecture:** 계정은 리포지토리에 `set_role`/`set_disabled`(감사 기록 포함)를 더하고 admin 라우트를 얹는다. 그 위에 **세션 인증 경로가 매 요청 계정을 재확인**하도록 `auth.py`를 고쳐 비활성화·강등이 즉시 효력을 갖게 한다. 노드는 백엔드 신규가 0건이고 화면만 만든다.

**Tech Stack:** Python 3.11 / FastAPI / pytest · React 18 + Vite 5 + TS + TanStack Query v5 + Vitest · MSW 2

## Global Constraints

- 설계 문서 `docs/superpowers/specs/2026-08-06-dms-admin-accounts-nodes-slice9-design.md`가 상위 규칙이다. 충돌 시 `2026-08-02-dms-clean-slate-design.md`가 이긴다.
- **비활성화는 기존 세션까지 즉시 끊어야 한다.** 그렇지 않으면 버튼이 거짓말이다. 이것이 이 슬라이스의 핵심 단언이다.
- **역할은 세션 쿠키가 아니라 계정 행에서 읽는다** — 강등이 즉시 반영된다.
- **자기 잠금 금지**: 요청자가 자기 계정을 강등하거나 비활성화하면 `409 cannot_lock_self`.
- 계정 응답에 **`password_hash`가 절대 포함되지 않는다** — 테스트가 부재를 명시 단언한다.
- Bearer 공유 토큰 경로는 계정과 무관하다 — 계정 재확인을 하지 않는다(기존 동작 유지).
- 계정 **생성·삭제 UI는 만들지 않는다**(비밀번호 흐름·참조 무결성). 비활성화로 갈음한다.
- 노드는 **백엔드 신규 0건** — `routes_nodes.py`와 `agents` 리포지토리를 건드리지 않는다.
- 차트 라이브러리를 도입하지 않는다 — 표로 렌더한다.
- 한국어 UI 문자열. 이모지 금지. 슬라이스 1의 `Card`/`Table`/`Button`/`Dialog` 재사용.
- 백엔드 테스트는 `.venv/bin/python -m pytest`(plain `python3`는 이 환경에서 깨져 있다). 프론트는 `frontend/`에서 `npm test`, `npx tsc -b`.
- 커밋은 태스크 단위, 각 태스크는 테스트 GREEN 상태로 끝난다.

---

### Task 1: `set_role` / `set_disabled` 리포지토리 메서드

**Files:**
- Modify: `src/dms/repositories/accounts.py`
- Test: `tests/test_repo_accounts_admin.py` (신규)

**Interfaces:**
- Consumes: `Database`, `utc_now_iso`, `dump_json`, `DomainValidationError`
- Produces: `AccountsRepository.set_role(username, role, *, actor)`, `.set_disabled(username, disabled, *, actor)` — 둘 다 없는 계정에 `KeyError`, 감사 로그에 before/after 기록. Task 2가 쓴다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_repo_accounts_admin.py`. 계정 생성 방식은 기존 `tests/`에서 `accounts.create`를 쓰는 테스트를 열어 그대로 따른다.

```python
import pytest
from dms.repositories import Repositories


def _mk(repos, name="alice", role="user"):
    repos.accounts.create(name, "pw", role, actor="admin")


def test_set_role_updates_and_audits(db):
    repos = Repositories(db)
    _mk(repos)
    repos.accounts.set_role("alice", "admin", actor="ops")
    assert repos.accounts.get("alice")["role"] == "admin"
    rows = db.query(
        "SELECT * FROM audit_log WHERE mutation_class = 'account' AND operation = 'role'")
    assert len(rows) == 1
    assert rows[0]["target_key"] == "alice"
    assert rows[0]["actor"] == "ops"
    assert rows[0]["before_state"] is not None and rows[0]["after_state"] is not None


def test_set_disabled_updates_and_audits(db):
    repos = Repositories(db)
    _mk(repos)
    repos.accounts.set_disabled("alice", True, actor="ops")
    assert repos.accounts.get("alice")["disabled"] == 1
    # 비활성 계정은 로그인 검증을 통과하지 못한다
    assert repos.accounts.verify("alice", "pw") is None
    repos.accounts.set_disabled("alice", False, actor="ops")
    assert repos.accounts.verify("alice", "pw") == "user"
    rows = db.query(
        "SELECT * FROM audit_log WHERE mutation_class = 'account' AND operation = 'disabled'")
    assert len(rows) == 2


def test_missing_account_raises(db):
    repos = Repositories(db)
    with pytest.raises(KeyError):
        repos.accounts.set_role("nope", "admin", actor="ops")
    with pytest.raises(KeyError):
        repos.accounts.set_disabled("nope", True, actor="ops")


def test_invalid_role_rejected(db):
    from dms.domain import DomainValidationError
    repos = Repositories(db)
    _mk(repos)
    with pytest.raises(DomainValidationError) as e:
        repos.accounts.set_role("alice", "superuser", actor="ops")
    assert e.value.reason_code == "invalid_role"


def test_list_never_exposes_password_hash(db):
    repos = Repositories(db)
    _mk(repos)
    for row in repos.accounts.list():
        assert "password_hash" not in row
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_repo_accounts_admin.py -v`
Expected: FAIL — `AttributeError: 'AccountsRepository' object has no attribute 'set_role'`

- [ ] **Step 3: 구현한다**

`src/dms/repositories/accounts.py`에 추가한다. `create`가 audit을 직접 INSERT하는 방식을 그대로 따른다(같은 파일 안의 기존 코드를 보고 컬럼·파라미터 스타일을 맞출 것):

```python
    def _audit_account(self, operation, username, before, after, actor, now):
        self._db.execute(
            """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                   before_state, after_state, at)
               VALUES ('account', :op, :u, :actor, :b, :a, :at)""",
            {"op": operation, "u": username, "actor": actor,
             "b": dump_json(before), "a": dump_json(after), "at": now})

    def set_role(self, username, role, *, actor):
        if role not in (ROLE_USER, ROLE_ADMIN):
            raise DomainValidationError("invalid_role", repr(role))
        with self._db.transaction():
            before = self.get(username)
            if before is None:
                raise KeyError(username)
            self._db.execute("UPDATE accounts SET role = :r WHERE username = :u",
                             {"r": role, "u": username})
            self._audit_account("role", username, before, self.get(username),
                                actor, utc_now_iso())

    def set_disabled(self, username, disabled, *, actor):
        with self._db.transaction():
            before = self.get(username)
            if before is None:
                raise KeyError(username)
            self._db.execute("UPDATE accounts SET disabled = :d WHERE username = :u",
                             {"d": 1 if disabled else 0, "u": username})
            self._audit_account("disabled", username, before, self.get(username),
                                actor, utc_now_iso())
```

`ROLE_USER`/`ROLE_ADMIN`을 `..domain`에서 import한다(이미 import돼 있는지 확인).

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `.venv/bin/python -m pytest tests/test_repo_accounts_admin.py -v` → PASS
Run: `.venv/bin/python -m pytest tests/ -q` → 전부 통과 (기준선 528 + 신규)

- [ ] **Step 5: 커밋**

```bash
git add src/dms/repositories/accounts.py tests/test_repo_accounts_admin.py
git commit -m "feat(repo): account role and disabled mutations with audit"
```

---

### Task 2: 계정 관리 API

**Files:**
- Create: `src/dms/api/routes_accounts.py`
- Modify: `src/dms/api/app.py`
- Test: `tests/test_api_admin_accounts.py` (신규)

**Interfaces:**
- Consumes: Task 1의 리포지토리 메서드, `accounts.list()`, `require_admin`
- Produces: `GET /api/admin/accounts`, `PUT /api/admin/accounts/{username}/role`, `PUT /api/admin/accounts/{username}/disabled`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_admin_accounts.py`. 인증 관례는 `tests/test_api_policies.py`를 따른다(admin Bearer `{"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}`, 비관리자 세션은 signup+login). 검증:

1. 비관리자 → 403, 비로그인 → 401.
2. 목록에 계정이 나오고 **`password_hash`가 없다**(각 행의 키 집합을 단언).
3. 역할 변경 → 200, 반영됨.
4. 비활성 토글 → 200, 반영됨.
5. 없는 계정 → `404 account_not_found`.
6. 잘못된 역할 → `422 invalid_role`.
7. **자기 잠금**: 세션으로 로그인한 관리자가 자기 자신을 강등/비활성화 → `409 cannot_lock_self`, 상태 불변.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_admin_accounts.py -v`
Expected: FAIL — 404 (라우트 없음)

- [ ] **Step 3: 구현한다**

`src/dms/api/routes_accounts.py` (신규). `routes_policies.py`의 구조를 따른다:

```python
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DomainValidationError
from .auth import Identity, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


class RoleBody(BaseModel):
    role: str


class DisabledBody(BaseModel):
    disabled: bool


def _guard_self(identity: Identity, username: str) -> None:
    # 마지막 관리자가 스스로를 잠가 포탈에서 잠기는 사고를 막는다.
    if identity.actor == username:
        raise HTTPException(status_code=409, detail="cannot_lock_self")


@router.get("/api/admin/accounts")
def list_accounts(request: Request):
    return request.app.state.repos.accounts.list()


@router.put("/api/admin/accounts/{username}/role")
def set_role(username: str, body: RoleBody, request: Request,
             identity: Identity = Depends(require_admin)):
    _guard_self(identity, username)
    try:
        request.app.state.repos.accounts.set_role(username, body.role,
                                                  actor=identity.actor)
    except KeyError:
        raise HTTPException(status_code=404, detail="account_not_found")
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    return request.app.state.repos.accounts.get(username)


@router.put("/api/admin/accounts/{username}/disabled")
def set_disabled(username: str, body: DisabledBody, request: Request,
                 identity: Identity = Depends(require_admin)):
    _guard_self(identity, username)
    try:
        request.app.state.repos.accounts.set_disabled(username, body.disabled,
                                                      actor=identity.actor)
    except KeyError:
        raise HTTPException(status_code=404, detail="account_not_found")
    return request.app.state.repos.accounts.get(username)
```

`src/dms/api/app.py`의 기존 등록 블록(**SPA 캐치올보다 앞**)에 추가한다.

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `.venv/bin/python -m pytest tests/test_api_admin_accounts.py -v` → PASS
Run: `.venv/bin/python -m pytest tests/ -q` → 전부 통과

- [ ] **Step 5: 커밋**

```bash
git add src/dms/api/routes_accounts.py src/dms/api/app.py tests/test_api_admin_accounts.py
git commit -m "feat(api): admin account list, role, and disable"
```

---

### Task 3: 세션 인증이 계정을 재확인한다

**Files:**
- Modify: `src/dms/api/auth.py` (`current_identity`의 세션 분기)
- Test: `tests/test_auth_session_recheck.py` (신규)

**Interfaces:**
- Consumes: `request.app.state.repos.accounts.get(username)`
- Produces: 비활성화·강등이 **기존 세션에 즉시 반영**

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_auth_session_recheck.py`:

```python
ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}


def _login(client, name="alice", pw="pw"):
    client.post("/api/auth/signup", json={"username": name, "password": pw})
    r = client.post("/api/auth/login", json={"username": name, "password": pw})
    assert r.status_code == 200


def test_disabling_kills_an_existing_session(client):
    _login(client)
    assert client.get("/api/auth/me").status_code == 200          # 세션 살아있음
    client.put("/api/admin/accounts/alice/disabled", json={"disabled": True},
               headers=ADMIN)
    r = client.get("/api/auth/me")                                 # 같은 세션으로
    assert r.status_code == 401
    assert r.json()["detail"] == "account_disabled"


def test_role_change_takes_effect_on_the_existing_session(client):
    _login(client)
    # user 는 admin 라우트에 403
    assert client.get("/api/admin/policies").status_code == 403
    client.put("/api/admin/accounts/alice/role", json={"role": "admin"}, headers=ADMIN)
    assert client.get("/api/admin/policies").status_code == 200    # 승격 즉시 반영
    client.put("/api/admin/accounts/alice/role", json={"role": "user"}, headers=ADMIN)
    assert client.get("/api/admin/policies").status_code == 403    # 강등도 즉시


def test_bearer_token_path_is_unaffected(client):
    # 공유 토큰은 계정과 무관하다 — 존재하지 않는 actor 로도 동작한다
    assert client.get("/api/admin/policies", headers=ADMIN).status_code == 200


def test_deleted_account_session_is_rejected(client, db):
    _login(client)
    db.execute("DELETE FROM accounts WHERE username = 'alice'")
    assert client.get("/api/auth/me").status_code == 401
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_auth_session_recheck.py -v`
Expected: 1·2·4번 FAIL — 비활성화/강등/삭제 후에도 세션이 그대로 동작한다

- [ ] **Step 3: 구현한다**

`src/dms/api/auth.py`의 `current_identity` 세션 분기를 설계 §2.2의 코드로 바꾼다. **Bearer 분기는 손대지 않는다.**

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `.venv/bin/python -m pytest tests/test_auth_session_recheck.py -v` → PASS
Run: `.venv/bin/python -m pytest tests/ -q` → 전부 통과. **여기서 깨지는 기존 테스트가 있다면 그 테스트가 "계정 없이 세션만 심어서" 인증을 통과하던 것이다** — 테스트를 약화시키지 말고, 실제 계정을 만들도록 픽스처를 고치고 보고서에 적는다.

- [ ] **Step 5: 커밋**

```bash
git add src/dms/api/auth.py tests/test_auth_session_recheck.py
git commit -m "fix(auth): re-check the account on every session-authenticated request"
```

---

### Task 4: 프론트 타입 · reason 코드 · 훅

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/features/accounts/useAccounts.ts`
- Create: `frontend/src/features/nodes/useNodes.ts`
- Test: `frontend/src/features/accounts/useAccounts.test.tsx` (신규)

**Interfaces:**
- Consumes: `apiGet`/`apiSend`, Task 2의 엔드포인트, 기존 `GET /api/admin/nodes`·`/api/admin/nodes/{name}/reports`
- Produces: `Account`·`NodeInfo`·`NodeReport` 타입과 훅. Task 5·6이 쓴다.

- [ ] **Step 1: 타입을 추가한다**

`frontend/src/lib/types.ts`. **기존 `Node` 타입이 이미 있다**(`{node_name, reported_at, fresh, report: unknown}`) — 그것을 확장해 `report`를 구조화하되, 기존 소비자(대시보드)가 깨지지 않는지 확인한다. 깨질 것 같으면 새 이름(`NodeInfo`)으로 추가한다.

```ts
export interface Account {
  username: string; role: string; email: string | null;
  disabled: number; created_at: string;
}
export interface NodeMount {
  storage_name: string; mount_path: string; status: string;
  exists?: boolean; is_mountpoint?: boolean; readable?: boolean; reason?: string | null;
}
export interface NodeTool {
  name: string; status: string; path?: string; version?: string; reason?: string | null;
}
export interface NodeDisk { storage_name: string; total_bytes: number; used_bytes: number }
export interface NodeReportBody {
  node_name?: string; probed_at?: string;
  mounts?: NodeMount[]; tools?: NodeTool[];
  os?: { disks?: NodeDisk[] } & Record<string, unknown>;
  identities?: unknown[];
}
export interface NodeInfo {
  node_name: string; reported_at: string; fresh: boolean; report: NodeReportBody;
}
export interface NodeReport { reported_at: string; report: NodeReportBody }
```

- [ ] **Step 2: reason 코드를 추가한다**

`frontend/src/lib/api.ts`의 `REASON_MESSAGES`에 **없는 것만**:

```ts
  account_not_found: "계정을 찾을 수 없습니다",
  invalid_role: "역할 값이 올바르지 않습니다",
  cannot_lock_self: "자기 계정의 역할 변경·비활성화는 할 수 없습니다",
  account_disabled: "계정이 비활성화되었습니다",
  node_not_found: "노드를 찾을 수 없습니다",
```

- [ ] **Step 3: 훅을 만든다**

`frontend/src/features/accounts/useAccounts.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { Account } from "../../lib/types";
export const useAccounts = () =>
  useQuery({ queryKey: ["accounts"], queryFn: () => apiGet<Account[]>("/api/admin/accounts") });
export const useSetRole = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (v: { username: string; role: string }) =>
    apiSend("PUT", `/api/admin/accounts/${v.username}/role`, { role: v.role }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }) });
};
export const useSetDisabled = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (v: { username: string; disabled: boolean }) =>
    apiSend("PUT", `/api/admin/accounts/${v.username}/disabled`, { disabled: v.disabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }) });
};
```

`frontend/src/features/nodes/useNodes.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { NodeInfo, NodeReport } from "../../lib/types";
export const useNodes = () =>
  useQuery({ queryKey: ["nodes"], queryFn: () => apiGet<NodeInfo[]>("/api/admin/nodes"),
             refetchInterval: 10000 });
export const useNodeReports = (name: string, enabled: boolean) =>
  useQuery({ queryKey: ["node-reports", name],
             queryFn: () => apiGet<NodeReport[]>(`/api/admin/nodes/${name}/reports`),
             enabled });
```

- [ ] **Step 4: 훅 테스트를 쓴다**

`useAccounts.test.tsx` — `frontend/src/features/policies/usePolicies.test.tsx`의 구조를 따른다. 최소 3개: 목록 반환; 역할 변경 PUT body가 `{role}`; 토글 PUT body가 `{disabled}`.

- [ ] **Step 5: 테스트·타입체크**

Run(from `frontend/`): `npx vitest run src/features/accounts && npx tsc -b` → PASS, tsc 0. `tsc`가 기존 `Node` 타입 소비자에서 실패하면 그 소비자를 새 타입에 맞춘다(대시보드가 `report`를 `unknown`으로 쓰고 있었다면 그대로 둬도 된다).

- [ ] **Step 6: 전체 확인 후 커밋**

Run: `npm test` → 전부 PASS

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/features/accounts frontend/src/features/nodes
git commit -m "feat(portal): account and node types, reason codes, and hooks"
```

---

### Task 5: 계정 관리 화면

**Files:**
- Create: `frontend/src/features/accounts/AccountsList.tsx`
- Test: `frontend/src/features/accounts/AccountsList.test.tsx` (신규)

**Interfaces:**
- Consumes: Task 4의 훅, `useMe`(`frontend/src/features/auth/useAuth.ts`), `Table`·`Button`, `ApiError`
- Produces: `AccountsList` (Task 6의 라우트가 마운트)

- [ ] **Step 1: 화면을 만든다**

`AccountsList.tsx` — `StoragesList.tsx`의 레이아웃·상태 처리 관례를 따른다.

- 제목 "계정". 로딩 "불러오는 중…", 에러는 `(q.error as ApiError).message`.
- `Table` 컬럼: 사용자명 / 역할 / 이메일 / 상태 / 등록일 / 작업.
- 역할: `<select aria-label={`${username} 역할`}>`(user/admin) — 변경 즉시 `useSetRole().mutate`.
- 상태: `disabled === 1`이면 "비활성", 아니면 "활성". 작업 열에 토글 버튼("비활성화"/"활성화") → `useSetDisabled().mutate({username, disabled: row.disabled !== 1})`.
- **자기 행**: `useMe()`의 `data?.actor === row.username`이면 select와 토글 버튼을 **`disabled`** 로 만들고, 옆에 "자기 계정은 변경할 수 없습니다"를 작게 표시한다(서버도 409로 막지만 왕복을 줄인다).
- mutation 에러는 표 아래에 **한 번만** 렌더한다(행마다 반복하지 않는다 — 슬라이스 4에서 겪은 문제).

- [ ] **Step 2: 테스트를 쓴다**

`AccountsList.test.tsx` — `PoliciesList.test.tsx`의 MSW 구조를 따른다. `/api/auth/me`도 스텁해야 한다. 최소 5개:

1. 목록 렌더(사용자명·역할·상태).
2. 역할 변경 시 PUT `/api/admin/accounts/:u/role` body가 `{role:"admin"}`.
3. 토글 시 PUT `/api/admin/accounts/:u/disabled` body가 `{disabled:true}`.
4. **자기 행의 컨트롤이 비활성**이다(`/api/auth/me`가 그 사용자를 반환할 때).
5. `409 cannot_lock_self` 응답 시 한국어 메시지가 보인다.

- [ ] **Step 3: 테스트·타입체크**

Run(from `frontend/`): `npx vitest run src/features/accounts && npx tsc -b` → PASS, tsc 0

- [ ] **Step 4: 커밋**

```bash
git add frontend/src/features/accounts
git commit -m "feat(portal): admin accounts screen"
```

---

### Task 6: 노드 대시보드 + 배선

**Files:**
- Create: `frontend/src/features/nodes/NodesList.tsx`
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/app/AppShell.tsx`
- Test: `frontend/src/features/nodes/NodesList.test.tsx` (신규)
- Modify: `frontend/src/app/router.test.tsx`

**Interfaces:**
- Consumes: Task 4의 `useNodes`·`useNodeReports`, `AccountsList`(Task 5), `Card`·`Table`·`Button`
- Produces: `/admin/accounts`·`/admin/nodes` 라우트와 내비

- [ ] **Step 1: 화면을 만든다**

`NodesList.tsx` 요건:

- 제목 "노드". 로딩·에러 상태.
- 목록 `Table`: 노드 / 신선도 / 마지막 리포트 / 마운트 / 도구.
  - 신선도: `fresh`면 "fresh", 아니면 **`text-bad`로 "stale"** — planner 어드미션이 신선도에 의존하므로 눈에 띄어야 한다.
  - 마운트 요약: `Ready n/m`(status가 `Ready`인 개수 / 전체). 도구도 같은 형식.
  - 행에 "상세" 버튼 → 선택된 노드만 상세를 렌더한다.
- 상세(선택된 노드):
  - 마운트 표: 스토리지 / 마운트 경로 / 상태 / 사유.
  - 도구 표: 이름 / 상태 / 버전 / 사유.
  - 디스크 표: 스토리지 / 사용 / 전체 / 사용률(%) — `used_bytes`·`total_bytes`로 계산하고 `total_bytes`가 0이면 `—`.
  - `identities`는 배열이 비어 있지 않을 때만 렌더한다.
  - "최근 리포트" 버튼 → `useNodeReports(name, true)`로 **지연 로드**해 `reported_at` 목록을 표로.
- 바이트는 사람이 읽는 형식으로(파일 안에 작은 헬퍼: TiB/GiB/MiB 단위, 소수점 1자리).

- [ ] **Step 2: 배선**

`router.tsx`에 `/admin/accounts`(`AccountsList`)와 `/admin/nodes`(`NodesList`)를 **캐치올 앞**에 `RequireRole role="admin"`으로 추가한다. `AppShell.tsx`의 admin 링크에 "계정"·"노드"를 추가한다.

- [ ] **Step 3: 테스트를 쓴다**

`NodesList.test.tsx`, 최소 5개:

1. 목록 렌더(노드명·마운트 요약 `Ready 1/2` 형식).
2. stale 노드가 "stale"로 표시된다.
3. "상세"를 누르면 마운트·도구·디스크 표가 보인다.
4. **"최근 리포트"를 누르기 전에는 reports 요청이 0건**이다(카운터 단언).
5. 누르면 이력이 보인다.

`router.test.tsx`에 두 라우트가 admin 세션에서 렌더되는 케이스를 추가한다(필요한 MSW 핸들러 포함).

- [ ] **Step 4: 전체 테스트·타입체크**

Run(from `frontend/`): `npm test && npx tsc -b` → 전부 PASS, tsc 0

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/features/nodes frontend/src/app/router.tsx frontend/src/app/AppShell.tsx frontend/src/app/router.test.tsx
git commit -m "feat(portal): node dashboard, accounts/nodes routes and nav"
```
