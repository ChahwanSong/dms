# DMS 포탈 슬라이스 3 (스토리지 관리 + 감사 로그) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 운영자가 포탈에서 스토리지를 등록/수정/활성토글/삭제하고, 감사 로그 화면으로 변경 이력을 보며, 사용 중(비종단 요청이 참조)인 스토리지 삭제는 백엔드가 막는다.

**Architecture:** 백엔드 CRUD/audit-log API는 이미 존재하므로 신규 백엔드는 in-use 가드 하나(`active_referencing_storage` + DELETE 409)뿐. 나머지는 슬라이스 1의 C 디자인·컴포넌트·api 클라이언트·TanStack Query를 재사용한 프론트(StorageDialog 폼, StoragesList 확장, 감사 로그 화면, 내비/라우트).

**Tech Stack:** Python 3.11 / FastAPI / pytest (백엔드), React+Vite+TS / TanStack Query / Radix / Vitest+MSW (프론트, 슬라이스 1과 동일).

## Global Constraints

- 패키지 매니저 npm, Node 20. 프론트는 `frontend/`. 백엔드 테스트 **`.venv/bin/pytest`**(시스템 pytest 아님), 프론트 `cd frontend && npm test` / `npx tsc -b`. 기존 전체 스위트(백엔드 356 + 프론트 32) green 유지.
- 모든 fetch `credentials: 'include'`; API 베이스 동일 출처 `/api`; 스토리지 API는 전부 `require_admin`(401 미인증 / 403 non-admin).
- C 디자인(스펙 §8): 밝은 테마, 이모지 금지, status = dot 없는 solid soft pill. 기존 컴포넌트(Table/StatusPill/Card/Button/Dialog) 재사용.
- 백엔드 API(기존): `POST /api/admin/storages`(201; 409 `storage_exists`; 422 검증), `PUT /api/admin/storages/{name}`(404 `storage_not_found`; 422), `DELETE /api/admin/storages/{name}`(404), `GET /api/admin/audit-log?limit=`. storage_name은 불변(PUT은 mount_path/managed_root/backend_type/enabled만).
- in-use 기준 = **비종단 요청 참조**(scan/rm의 `storage`, sync의 `source_storage`/`destination_storage`). 배치-Queued 엣지는 범위 밖.
- reason_code→한글 메시지 맵에 없는 코드는 코드 원문 노출(조용한 실패 금지).
- 커밋 trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

```
src/dms/repositories/requests.py   # (수정) active_referencing_storage 추가
src/dms/api/routes_storages.py     # (수정) DELETE에 in-use 가드
frontend/src/lib/types.ts          # (수정) Storage에 managed_root, AuditEntry 추가
frontend/src/lib/api.ts            # (수정) reason_code 맵 3개 추가
frontend/src/features/storages/
  useStorages.ts                   # (수정) useCreate/Update/DeleteStorage 추가
  StorageDialog.tsx                # (신규) 등록/수정 Radix Dialog 폼
  StoragesList.tsx                 # (수정) 등록 버튼 + 행 액션(수정/토글/삭제)
frontend/src/features/audit/
  useAudit.ts  AuditLog.tsx        # (신규) 감사 로그 훅 + 화면
frontend/src/app/AppShell.tsx      # (수정) "감사 로그" 내비
frontend/src/app/router.tsx        # (수정) /admin/audit 라우트
```

실행 순서 = 1→2→3→4→5→6 (백엔드 1, 프론트 타입/훅 2, StorageDialog 3, StoragesList 4, AuditLog 5, 내비/라우트 6이 화면 import).

---

## Task 1: 백엔드 — in-use 삭제 가드

**Files:**
- Modify: `src/dms/repositories/requests.py`, `src/dms/api/routes_storages.py`
- Test: `tests/test_api_storages_inuse.py`

**Interfaces:**
- Produces: `RequestsRepository.active_referencing_storage(storage_name: str) -> bool` — 비종단 요청 중 payload가 그 스토리지를 참조하면 True. `DELETE /api/admin/storages/{name}` 는 참조 시 409 `storage_in_use`.

- [ ] **Step 1: 실패 테스트 — `tests/test_api_storages_inuse.py`**

```python
from dms.repositories import Repositories

def _admin(client):
    client.app.state.repos.accounts.create("admin","pw","admin",actor="t")
    client.post("/api/auth/login", json={"username":"admin","password":"pw"})

def _seed_storage(client):
    client.post("/api/admin/storages", json={"storage_name":"s1","mount_path":"/s1",
        "managed_root":"/s1/dms","backend_type":"cephfs"})

def test_delete_blocked_when_referenced(client):
    _admin(client); _seed_storage(client)
    # s1을 참조하는 비종단 scan 요청 생성
    client.app.state.repos.requests.create(operation="scan", requester_id="admin",
        actor="admin", resource_key="k1", payload={"storage":"s1","target":"a","options":{}},
        priority="mid")
    r = client.delete("/api/admin/storages/s1")
    assert r.status_code == 409 and r.json()["detail"] == "storage_in_use"

def test_delete_allowed_when_not_referenced(client):
    _admin(client)
    client.post("/api/admin/storages", json={"storage_name":"s2","mount_path":"/s2",
        "managed_root":"/s2/dms","backend_type":"cephfs"})
    r = client.delete("/api/admin/storages/s2")
    assert r.status_code == 200

def test_referencing_sync_storages(db):
    repos = Repositories(db)
    repos.requests.create(operation="sync", requester_id="a", actor="a", resource_key="k",
        payload={"source_storage":"src","source":"x","destination_storage":"dst",
                 "destination":"y","options":{}}, priority="mid")
    assert repos.requests.active_referencing_storage("src") is True
    assert repos.requests.active_referencing_storage("dst") is True
    assert repos.requests.active_referencing_storage("other") is False
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/pytest tests/test_api_storages_inuse.py -v` → FAIL (method/가드 없음).

- [ ] **Step 3: 구현 — `requests.py`에 `active_referencing_storage` 추가**

`requests.py`는 이미 `from ..domain import ... TERMINAL_REQUEST_STATES` 와 `from ..db import ... load_json` 을 import한다(기존 get/list가 load_json 사용). `find_active`(비종단 필터) 옆에 추가:

```python
    def active_referencing_storage(self, storage_name) -> bool:
        terminal = tuple(s.value for s in TERMINAL_REQUEST_STATES)
        placeholders = ", ".join(f":t{i}" for i in range(len(terminal)))
        params = {f"t{i}": v for i, v in enumerate(terminal)}
        rows = self._db.query(
            f"SELECT payload FROM requests WHERE state NOT IN ({placeholders})", params)
        for r in rows:
            p = load_json(r["payload"])
            if storage_name in (p.get("storage"), p.get("source_storage"),
                                p.get("destination_storage")):
                return True
        return False
```

- [ ] **Step 4: 구현 — `routes_storages.py` DELETE에 가드**

`delete_storage`를 in-use 검사 후 삭제하도록 수정:

```python
@router.delete("/api/admin/storages/{name}")
def delete_storage(name: str, request: Request,
                   identity: Identity = Depends(require_admin)):
    if request.app.state.repos.requests.active_referencing_storage(name):
        raise HTTPException(status_code=409, detail="storage_in_use")
    try:
        return request.app.state.repos.storages.delete(name, actor=identity.actor)
    except KeyError:
        raise HTTPException(status_code=404, detail="storage_not_found")
```

- [ ] **Step 5: 통과 + 회귀** — Run: `.venv/bin/pytest tests/test_api_storages_inuse.py tests/test_api_storages.py -v && .venv/bin/pytest -q`.

- [ ] **Step 6: 커밋**
```bash
git add src/dms/repositories/requests.py src/dms/api/routes_storages.py tests/test_api_storages_inuse.py
git commit -m "feat(api): block storage delete when referenced by active requests"
```

---

## Task 2: 프론트 — 타입 + reason 맵 + 스토리지/감사 훅

**Files:**
- Modify: `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`, `frontend/src/features/storages/useStorages.ts`
- Create: `frontend/src/features/audit/useAudit.ts`
- Test: `frontend/src/features/storages/useStorages.test.tsx`

**Interfaces:**
- Produces:
  - `types.ts`: `Storage`에 `managed_root: string` 추가; `AuditEntry { id: number; mutation_class: string; operation: string; target_key: string; actor: string; before_state: string | null; after_state: string | null; at: string }`.
  - `api.ts`: `REASON_MESSAGES`에 `storage_exists`·`storage_in_use`·`storage_not_found` 추가.
  - `useStorages.ts`: `useCreateStorage()`(mutation `StorageCreateBody`), `useUpdateStorage()`(mutation `{name, body: StorageUpdateBody}`), `useDeleteStorage()`(mutation `name: string`) — 성공 시 `["storages"]` 무효화. 타입 `StorageCreateBody`(storage_name·mount_path·managed_root·backend_type), `StorageUpdateBody`(mount_path·managed_root·backend_type·enabled) export.
  - `useAudit.ts`: `useAuditLog()`(`GET /api/admin/audit-log`).

- [ ] **Step 1: 실패 테스트 — `useStorages.test.tsx`**

```tsx
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { useCreateStorage } from "./useStorages";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());
test("useCreateStorage posts body", async () => {
  let body: any = null;
  server.use(http.post("/api/admin/storages", async ({ request }) => {
    body = await request.json(); return HttpResponse.json(body, { status: 201 }); }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => useCreateStorage(), { wrapper: ({ children }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  result.current.mutate({ storage_name: "s1", mount_path: "/s1", managed_root: "/s1/dms", backend_type: "cephfs" });
  await waitFor(() => expect(body).toMatchObject({ storage_name: "s1", backend_type: "cephfs" }));
});
```

- [ ] **Step 2: 실패 확인** — Run: `cd frontend && npx vitest run src/features/storages/useStorages.test.tsx` → FAIL.

- [ ] **Step 3: 구현**

`types.ts`: `Storage` 인터페이스에 `managed_root: string;` 한 줄 추가(다른 필드 유지). 그리고:
```ts
export interface AuditEntry {
  id: number; mutation_class: string; operation: string; target_key: string;
  actor: string; before_state: string | null; after_state: string | null; at: string;
}
```

`api.ts` `REASON_MESSAGES`에 추가:
```ts
  storage_exists: "이미 존재하는 스토리지입니다",
  storage_in_use: "사용 중인 스토리지는 삭제할 수 없습니다 (비활성화하세요)",
  storage_not_found: "스토리지를 찾을 수 없습니다",
```

`useStorages.ts`(기존 `useStorages` 유지, 아래 추가):
```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { Storage } from "../../lib/types";
export const useStorages = () =>
  useQuery({ queryKey: ["storages"], queryFn: () => apiGet<Storage[]>("/api/admin/storages") });
export interface StorageCreateBody { storage_name: string; mount_path: string; managed_root: string; backend_type: string; }
export interface StorageUpdateBody { mount_path: string; managed_root: string; backend_type: string; enabled: boolean; }
export const useCreateStorage = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (b: StorageCreateBody) => apiSend("POST", "/api/admin/storages", b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["storages"] }) });
};
export const useUpdateStorage = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (v: { name: string; body: StorageUpdateBody }) =>
    apiSend("PUT", `/api/admin/storages/${v.name}`, v.body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["storages"] }) });
};
export const useDeleteStorage = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (name: string) => apiSend("DELETE", `/api/admin/storages/${name}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["storages"] }) });
};
```

`useAudit.ts`(신규):
```ts
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { AuditEntry } from "../../lib/types";
export const useAuditLog = () =>
  useQuery({ queryKey: ["audit"], queryFn: () => apiGet<AuditEntry[]>("/api/admin/audit-log") });
```

- [ ] **Step 4: 통과 + 회귀** — Run: `cd frontend && npm test && npx tsc -b` → PASS.

- [ ] **Step 5: 커밋**
```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/features/storages/useStorages.ts frontend/src/features/storages/useStorages.test.tsx frontend/src/features/audit/useAudit.ts
git commit -m "feat(portal): storage CRUD + audit hooks, types, reason codes"
```

---

## Task 3: 프론트 — StorageDialog (등록/수정 폼)

**Files:**
- Create: `frontend/src/features/storages/StorageDialog.tsx`
- Test: `frontend/src/features/storages/StorageDialog.test.tsx`

**Interfaces:**
- Consumes: `useCreateStorage`/`useUpdateStorage`(Task 2), `Dialog`(components/ui), `Button`, `ApiError`, `Storage` 타입.
- Produces: `<StorageDialog mode="create" | "edit" storage?={Storage} trigger={ReactNode} />` — create=4필드 입력→POST; edit=storage_name 읽기전용 + mount_path/managed_root/backend_type/enabled→PUT. 성공 시 닫음.

- [ ] **Step 1: 실패 테스트 — `StorageDialog.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { StorageDialog } from "./StorageDialog";
import { Button } from "../../components/ui/Button";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());
function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}
test("create posts the four fields", async () => {
  let body: any = null;
  server.use(http.post("/api/admin/storages", async ({ request }) => {
    body = await request.json(); return HttpResponse.json(body, { status: 201 }); }));
  wrap(<StorageDialog mode="create" trigger={<Button>등록</Button>} />);
  await userEvent.click(screen.getByRole("button", { name: "등록" }));
  await userEvent.type(screen.getByLabelText("스토리지 이름"), "s1");
  await userEvent.type(screen.getByLabelText("마운트 경로"), "/s1");
  await userEvent.type(screen.getByLabelText("관리 루트"), "/s1/dms");
  await userEvent.type(screen.getByLabelText("백엔드"), "cephfs");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await screen.findByText(/./);
  expect(body).toEqual({ storage_name: "s1", mount_path: "/s1", managed_root: "/s1/dms", backend_type: "cephfs" });
});
```

- [ ] **Step 2: 실패 확인** — Run: `cd frontend && npx vitest run src/features/storages/StorageDialog.test.tsx` → FAIL.

- [ ] **Step 3: 구현 — `StorageDialog.tsx`**

```tsx
import { useState } from "react";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
import { useCreateStorage, useUpdateStorage } from "./useStorages";
import type { Storage } from "../../lib/types";
const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";
export function StorageDialog({ mode, storage, trigger }: {
  mode: "create" | "edit"; storage?: Storage; trigger: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(storage?.storage_name ?? "");
  const [mount, setMount] = useState(storage?.mount_path ?? "");
  const [root, setRoot] = useState(storage?.managed_root ?? "");
  const [backend, setBackend] = useState(storage?.backend_type ?? "");
  const [enabled, setEnabled] = useState(storage ? storage.enabled === 1 : true);
  const create = useCreateStorage(); const update = useUpdateStorage();
  const m = mode === "create" ? create : update;
  const submit = () => {
    if (mode === "create")
      create.mutate({ storage_name: name, mount_path: mount, managed_root: root, backend_type: backend },
        { onSuccess: () => setOpen(false) });
    else
      update.mutate({ name, body: { mount_path: mount, managed_root: root, backend_type: backend, enabled } },
        { onSuccess: () => setOpen(false) });
  };
  return (
    <Dialog open={open} onOpenChange={setOpen} title={mode === "create" ? "스토리지 등록" : "스토리지 수정"} trigger={trigger}>
      <form className="space-y-3 text-sm" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <label className="block">스토리지 이름
          <input aria-label="스토리지 이름" className={field} value={name} disabled={mode === "edit"}
                 onChange={(e) => setName(e.target.value)} /></label>
        <label className="block">마운트 경로
          <input aria-label="마운트 경로" className={field} value={mount} onChange={(e) => setMount(e.target.value)} /></label>
        <label className="block">관리 루트
          <input aria-label="관리 루트" className={field} value={root} onChange={(e) => setRoot(e.target.value)} /></label>
        <label className="block">백엔드
          <input aria-label="백엔드" className={field} value={backend} onChange={(e) => setBackend(e.target.value)} /></label>
        {mode === "edit" && (
          <label className="flex items-center gap-2"><input type="checkbox" checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)} /> 활성</label>)}
        {m.isError && <p className="text-bad">{(m.error as ApiError).message}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" type="button" onClick={() => setOpen(false)}>취소</Button>
          <Button type="submit" disabled={m.isPending}>저장</Button>
        </div>
      </form>
    </Dialog>
  );
}
```

- [ ] **Step 4: 통과 + 회귀** — Run: `cd frontend && npm test && npx tsc -b` → PASS.

- [ ] **Step 5: 커밋**
```bash
git add frontend/src/features/storages/StorageDialog.tsx frontend/src/features/storages/StorageDialog.test.tsx
git commit -m "feat(portal): storage create/edit dialog form"
```

---

## Task 4: 프론트 — StoragesList 확장 (등록·수정·토글·삭제)

**Files:**
- Modify: `frontend/src/features/storages/StoragesList.tsx`
- Test: `frontend/src/features/storages/StoragesList.test.tsx`

**Interfaces:**
- Consumes: `useStorages`/`useUpdateStorage`/`useDeleteStorage`(Task 2), `StorageDialog`(Task 3), Table/StatusPill/Button/Dialog/Card, `ApiError`.
- Produces: `<StoragesList/>` — "스토리지 등록" 버튼(StorageDialog create) + 행별 관리(작업)열: 수정(StorageDialog edit), 활성/비활성 토글(useUpdateStorage로 enabled 반전), 삭제(확인 다이얼로그→useDeleteStorage; 409 storage_in_use면 인라인 에러).

- [ ] **Step 1: 실패 테스트 — `StoragesList.test.tsx`(교체/확장)**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { StoragesList } from "./StoragesList";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());
const S = { storage_name:"cephfs", mount_path:"/cephfs", managed_root:"/cephfs/dms",
  backend_type:"ceph", enabled:1, status:"Healthy", status_detail:null };
function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><StoragesList /></QueryClientProvider>);
}
test("lists storages and shows manage actions", async () => {
  server.use(http.get("/api/admin/storages", () => HttpResponse.json([S])));
  wrap();
  expect(await screen.findByText("cephfs")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "스토리지 등록" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "삭제" })).toBeInTheDocument();
});
test("delete shows in-use error on 409", async () => {
  server.use(
    http.get("/api/admin/storages", () => HttpResponse.json([S])),
    http.delete("/api/admin/storages/cephfs", () => HttpResponse.json({ detail: "storage_in_use" }, { status: 409 })));
  wrap();
  await userEvent.click(await screen.findByRole("button", { name: "삭제" }));
  await userEvent.click(await screen.findByRole("button", { name: "삭제 확인" }));
  expect(await screen.findByText("사용 중인 스토리지는 삭제할 수 없습니다 (비활성화하세요)")).toBeInTheDocument();
});
```

- [ ] **Step 2: 실패 확인** — Run: `cd frontend && npx vitest run src/features/storages/StoragesList.test.tsx` → FAIL.

- [ ] **Step 3: 구현 — `StoragesList.tsx`**

```tsx
import { useState } from "react";
import { useStorages, useUpdateStorage, useDeleteStorage } from "./useStorages";
import { StorageDialog } from "./StorageDialog";
import { Table } from "../../components/ui/Table";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { ApiError } from "../../lib/api";
import type { Storage } from "../../lib/types";

function DeleteButton({ s }: { s: Storage }) {
  const [open, setOpen] = useState(false);
  const del = useDeleteStorage();
  return (
    <Dialog open={open} onOpenChange={setOpen} title="스토리지 삭제"
            trigger={<Button variant="ghost">삭제</Button>}>
      <p className="text-sm text-muted mb-3">{s.storage_name} 을(를) 삭제할까요?</p>
      {del.isError && <p className="text-bad text-sm mb-2">{(del.error as ApiError).message}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={() => setOpen(false)}>취소</Button>
        <Button onClick={() => del.mutate(s.storage_name, { onSuccess: () => setOpen(false) })}
                disabled={del.isPending}>삭제 확인</Button>
      </div>
    </Dialog>
  );
}

export function StoragesList() {
  const q = useStorages(); const update = useUpdateStorage();
  const toggle = (s: Storage) => update.mutate({ name: s.storage_name,
    body: { mount_path: s.mount_path, managed_root: s.managed_root, backend_type: s.backend_type, enabled: s.enabled !== 1 } });
  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">스토리지</h1>
        <StorageDialog mode="create" trigger={<Button>스토리지 등록</Button>} />
      </div>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : (
        <Table>
          <thead><tr className="text-muted"><th className="py-2">이름</th><th>백엔드</th><th>마운트</th><th>상태</th><th>활성</th><th>작업</th></tr></thead>
          <tbody>
            {(q.data ?? []).map((s) => (
              <tr key={s.storage_name} className="border-t border-black/5">
                <td className="py-2">{s.storage_name}</td><td>{s.backend_type}</td>
                <td className="text-muted">{s.mount_path}</td><td><StatusPill state={s.status} /></td>
                <td>{s.enabled === 1 ? "on" : "off"}</td>
                <td className="flex gap-2 py-2">
                  <StorageDialog mode="edit" storage={s} trigger={<Button variant="ghost">수정</Button>} />
                  <Button variant="ghost" onClick={() => toggle(s)}>{s.enabled === 1 ? "비활성화" : "활성화"}</Button>
                  <DeleteButton s={s} />
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </section>
  );
}
```

- [ ] **Step 4: 통과 + 회귀** — Run: `cd frontend && npm test && npx tsc -b` → PASS.

- [ ] **Step 5: 커밋**
```bash
git add frontend/src/features/storages/StoragesList.tsx frontend/src/features/storages/StoragesList.test.tsx
git commit -m "feat(portal): storages manage (create/edit/toggle/delete + in-use guard)"
```

---

## Task 5: 프론트 — 감사 로그 화면

**Files:**
- Create: `frontend/src/features/audit/AuditLog.tsx`
- Test: `frontend/src/features/audit/AuditLog.test.tsx`

**Interfaces:**
- Consumes: `useAuditLog`(Task 2), Table.
- Produces: `<AuditLog/>` — 감사 이력 테이블(시각·operation·target·actor).

- [ ] **Step 1: 실패 테스트 — `AuditLog.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { AuditLog } from "./AuditLog";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());
test("renders audit entries", async () => {
  server.use(http.get("/api/admin/audit-log", () => HttpResponse.json([
    { id:2, mutation_class:"storage", operation:"create", target_key:"cephfs",
      actor:"admin", before_state:null, after_state:"{}", at:"2026-08-05T00:00:00Z" }])));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><AuditLog /></QueryClientProvider>);
  expect(await screen.findByText("cephfs")).toBeInTheDocument();
  expect(screen.getByText("create")).toBeInTheDocument();
});
```

- [ ] **Step 2: 실패 확인** — Run: `cd frontend && npx vitest run src/features/audit/AuditLog.test.tsx` → FAIL.

- [ ] **Step 3: 구현 — `AuditLog.tsx`**

```tsx
import { useAuditLog } from "./useAudit";
import { Table } from "../../components/ui/Table";
export function AuditLog() {
  const q = useAuditLog();
  return (
    <section className="space-y-4">
      <h1 className="text-lg font-semibold">감사 로그</h1>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : (
        <Table>
          <thead><tr className="text-muted"><th className="py-2">시각</th><th>작업</th><th>대상</th><th>실행자</th></tr></thead>
          <tbody>
            {(q.data ?? []).map((e) => (
              <tr key={e.id} className="border-t border-black/5">
                <td className="py-2 text-muted">{e.at}</td><td>{e.operation}</td>
                <td>{e.target_key}</td><td className="text-muted">{e.actor}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </section>
  );
}
```

- [ ] **Step 4: 통과 + 회귀** — Run: `cd frontend && npm test && npx tsc -b` → PASS.

- [ ] **Step 5: 커밋**
```bash
git add frontend/src/features/audit/AuditLog.tsx frontend/src/features/audit/AuditLog.test.tsx
git commit -m "feat(portal): audit log screen"
```

---

## Task 6: 프론트 — 감사 로그 내비 + 라우트

**Files:**
- Modify: `frontend/src/app/AppShell.tsx`, `frontend/src/app/router.tsx`
- Test: `frontend/src/app/router.test.tsx`(테스트 1건 추가)

**Interfaces:**
- Consumes: `AuditLog`(Task 5), RequireRole/AppShell.
- Produces: admin 내비 "감사 로그"(`/admin/audit`); 라우트 `/admin/audit`(RequireRole admin + AppShell + AuditLog).

- [ ] **Step 1: 실패 테스트 — `router.test.tsx`에 추가**

```tsx
test("admin can open audit log", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor:"admin", role:"admin" })),
    http.get("/api/admin/audit-log", () => HttpResponse.json([])),
  );
  renderAt("/admin/audit");
  expect(await screen.findByRole("heading", { name: "감사 로그" })).toBeInTheDocument();
});
```
(기존 `router.test.tsx`의 `renderAt`/server 재사용.)

- [ ] **Step 2: 실패 확인** — Run: `cd frontend && npx vitest run src/app/router.test.tsx` → FAIL.

- [ ] **Step 3: 구현**

`AppShell.tsx`: admin 내비에 한 줄 추가(대시보드 링크 옆):
```tsx
        {isAdmin && <NavLink to="/admin/audit" className={linkCls}>감사 로그</NavLink>}
```

`router.tsx`: import + 라우트 추가:
```tsx
import { AuditLog } from "../features/audit/AuditLog";
```
```tsx
        <Route path="/admin/audit" element={<RequireRole role="admin"><AppShell><AuditLog /></AppShell></RequireRole>} />
```

- [ ] **Step 4: 통과 + 전체 회귀** — Run: `cd frontend && npm test && npx tsc -b` → PASS(전체 프론트). 이어 백엔드 `.venv/bin/pytest -q` green 확인.

- [ ] **Step 5: 커밋**
```bash
git add frontend/src/app/AppShell.tsx frontend/src/app/router.tsx frontend/src/app/router.test.tsx
git commit -m "feat(portal): audit log nav + route"
```

---

## Self-Review (작성자 체크)

**1. Spec coverage**
- §1 화면(스토리지 관리 확장 + 감사 로그) → Task 4/5/6. ✅
- §2 백엔드 in-use 가드(active_referencing_storage + DELETE 409) → Task 1. ✅
- §3 프론트(훅·StorageDialog·StoragesList·AuditLog·내비/라우트·타입·reason맵) → Task 2/3/4/5/6. ✅
- §4 테스트(백엔드 in-use + 프론트 CRUD/감사/라우트) → 각 Task. ✅

**2. Placeholder scan:** 모든 코드 단계 실제 코드. "적절히 처리" 없음. ✅

**3. Type consistency:** `active_referencing_storage`(T1) 시그니처 = T1 라우트 사용 일치. 프론트 훅명(useCreateStorage/useUpdateStorage/useDeleteStorage/useAuditLog)·타입(StorageCreateBody/StorageUpdateBody/AuditEntry, Storage+managed_root)이 T2 정의 = T3/4/5 사용 일치. reason_code(storage_exists/storage_in_use/storage_not_found) T2 = T4 사용. ✅

## 배포/실증 (구현 후 별도 ops)
전 Task green 후: dms 이미지 재빌드(d11)→dms-api 재배포(정적 SPA + in-use 가드). 컨트롤러/마이그레이션 변경 없음(스키마 불변). 실증: 포탈에서 스토리지 등록→수정→활성토글→(참조 잡 있는) 삭제 거부→(무참조) 삭제, 감사 로그 화면 이력 확인.
