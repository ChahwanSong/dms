# DMS 포탈 슬라이스 1 (일회성 sync 전체 스택) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** DMS 포탈의 첫 얇은 전체 슬라이스를 세운다 — 세션 로그인, role 라우팅, 내 작업 목록, 일회성 sync 제출(preview→confirm 게이트), 스토리지 목록, 운영 대시보드를 C/Soft-SaaS 디자인으로 구현하고 dms-api가 정적 서빙한다.

**Architecture:** `frontend/`(Vite+React+TS SPA)를 신설한다. 개발은 vite dev가 `/api`를 dms-api(8000)로 프록시하고, 프로덕션은 `vite build` 산출물(`frontend/dist/`)을 dms-api가 `StaticFiles`+SPA fallback으로 서빙한다(멀티스테이지 `Dockerfile.dms`). 서버 상태는 TanStack Query, 라우팅은 React Router, UI는 Tailwind + Radix.

**Tech Stack:** React 18, Vite 5, TypeScript 5, React Router 6, TanStack Query 5, Tailwind CSS 3, Radix UI primitives, Vitest + Testing Library + MSW 2. 백엔드는 FastAPI(기존), pytest(기존).

## Global Constraints

- 패키지 매니저 **npm**, Node **20**. 프론트 코드는 전부 `frontend/` 아래. `src/`(백엔드)와 형제.
- 모든 fetch는 **`credentials: 'include'`**. JS는 인증 토큰을 저장·전송하지 않는다(세션 쿠키만).
- API 베이스는 **동일 출처 `/api`**. 개발은 vite 프록시, 프로덕션은 dms-api가 같은 출처로 서빙.
- **role은 `user`/`admin` 둘뿐.** admin 전용: `/admin/storages`, `/admin/dashboard`. "운영자"=admin.
- 디자인(C/Soft-SaaS, 스펙 §8): 밝은 테마 단일, **이모지 금지**, 좌측보더 액센트 박스 금지,
  상태 배지는 **dot 없는 solid soft pill**. 토큰(정확값): bg `#f6f6f3`, surface `#ffffff`,
  accent violet `#6d5efc`, text `#1c1d22`, muted `#5b6070`, ok `text #067647 / bg #e7f7ee`,
  bad `text #b42318 / bg #fee4e2`, busy `text #5b52d6 / bg #ecebff`. 카드 radius `rounded-xl`.
- **green=정상 / red=비정상**, 진행 중은 violet(busy) 중립.
- 종단 잡 상태(폴링 중단 기준): `Succeeded`, `Failed`, `Rejected`, `Cancelled`, `PreviewExpired`.
- 사유 코드→한글 맵에 없는 코드는 **코드 원문 그대로 노출**(조용한 실패 금지).
- 커밋은 자주. 각 Task 끝에서 1회 커밋. 커밋 메시지 trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

```
frontend/
  index.html                 # SPA 엔트리
  package.json               # deps + scripts (dev/build/test)
  vite.config.ts             # /api 프록시 + vitest 설정
  tsconfig.json
  tailwind.config.ts         # C 디자인 토큰
  postcss.config.js
  src/
    main.tsx                 # Router + QueryClientProvider 부트스트랩
    index.css                # tailwind 지시자 + base
    test/setup.ts            # vitest + testing-library + MSW 서버
    test/msw.ts              # MSW 핸들러 팩토리
    lib/
      api.ts                 # fetch 래퍼 + reason_code 맵 + ApiError
      types.ts               # API 응답 타입
      jobState.ts            # 종단 상태 판정 + status→pill variant
    app/
      AuthContext.tsx        # 현재 신원 {actor, role} 컨텍스트
      queryClient.ts         # QueryClient 인스턴스
      router.tsx             # 라우트 트리 + role 가드
      AppShell.tsx           # 사이드바+상단바 레이아웃
      RequireRole.tsx        # 라우트 role 가드
    components/ui/
      Button.tsx  Card.tsx  Table.tsx  StatusPill.tsx  MetricTile.tsx
      Dialog.tsx  Field.tsx
    features/
      auth/     Login.tsx  useAuth.ts
      jobs/     JobsList.tsx  RequestDetail.tsx  SubmitSync.tsx  ConfirmDialog.tsx  useJobs.ts
      storages/ StoragesList.tsx  useStorages.ts
      dashboard/ Dashboard.tsx  useDashboard.ts
```

백엔드 변경(최소): `src/dms/api/app.py`(StaticFiles 마운트), `deploy/docker/Dockerfile.dms`(멀티스테이지), 새 테스트 `tests/test_api_spa.py`.

---

## Task 1: 백엔드 — SPA 정적 서빙 + fallback 마운트

dms-api가 `/api/*`·`/healthz`·`/docs`는 라우터로, 그 외 경로는 빌드된 SPA(`frontend/dist/`)에서 서빙하고 미매칭 경로는 `index.html`을 반환하도록 한다(클라이언트 라우팅).

**Files:**
- Modify: `src/dms/api/app.py`
- Create: `tests/test_api_spa.py`

**Interfaces:**
- Produces: `create_app(settings, db)` 는 `settings.static_dir`(옵션)가 있으면 SPA를 마운트한다.
  `Settings`에 `static_dir: str | None = None` 필드 추가(없으면 마운트 생략 → 기존 API 테스트 무영향).

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_api_spa.py`

```python
from pathlib import Path
from dms.db import Database
from dms.migrations import migrate
from dms.config import Settings
from dms.api.app import create_app
from fastapi.testclient import TestClient


def _client(tmp_path, static_dir):
    db = Database.connect(f"sqlite:///{tmp_path}/spa.db")
    migrate(db)
    settings = Settings(database_url="unused", shared_token="t", admin_token="a",
                        session_secret="s", static_dir=str(static_dir))
    return TestClient(create_app(settings, db))


def test_spa_root_serves_index(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>DMS</title>")
    client = _client(tmp_path, dist)
    r = client.get("/")
    assert r.status_code == 200
    assert "DMS" in r.text


def test_spa_unknown_path_falls_back_to_index(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("INDEX")
    client = _client(tmp_path, dist)
    r = client.get("/admin/dashboard")     # 클라이언트 라우트, 서버엔 없음
    assert r.status_code == 200
    assert r.text == "INDEX"


def test_api_route_not_shadowed_by_spa(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("INDEX")
    client = _client(tmp_path, dist)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_no_static_dir_means_no_mount(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/nomount.db")
    migrate(db)
    settings = Settings(database_url="unused", shared_token="t", admin_token="a",
                        session_secret="s")   # static_dir 미지정
    client = TestClient(create_app(settings, db))
    assert client.get("/").status_code == 404   # 마운트 없음 → 404
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_api_spa.py -v`
Expected: FAIL — `Settings`에 `static_dir` 없음 / SPA 미마운트로 `/`가 404.

- [ ] **Step 3: `Settings`에 `static_dir` 필드 추가** — `src/dms/config.py`

`Settings` 데이터클래스에 `static_dir: str | None = None` 필드를 추가한다(기본 None). `from_env`에서
`static_dir=environ.get("DMS_STATIC_DIR")` 로 읽는다(없으면 None).

- [ ] **Step 4: `create_app`에 SPA 마운트 추가** — `src/dms/api/app.py`

라우터 `include_router` **뒤에**(API 우선순위 보장) 아래를 추가한다:

```python
import os
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles

# ... include_router(...) 들 뒤 ...

    static_dir = settings.static_dir
    if static_dir and os.path.isdir(static_dir):
        assets = os.path.join(static_dir, "assets")
        if os.path.isdir(assets):
            app.mount("/assets", StaticFiles(directory=assets), name="assets")
        index_path = os.path.join(static_dir, "index.html")

        @app.get("/{full_path:path}")
        def spa_fallback(full_path: str) -> Response:
            # /api·/healthz·/docs·/openapi.json 는 이미 위 라우터가 처리했다.
            candidate = os.path.normpath(os.path.join(static_dir, full_path))
            if (candidate.startswith(os.path.abspath(static_dir))
                    and os.path.isfile(candidate)):
                return FileResponse(candidate)
            return FileResponse(index_path)
```

캐치올 `GET /{full_path:path}`는 라우터 include 뒤에 등록되므로 구체 API 라우트가 우선한다.
`candidate` 경로 이탈(`..`) 방지를 위해 `static_dir` 접두 검사를 둔다.

- [ ] **Step 5: 테스트 통과 확인 + 전체 회귀**

Run: `pytest tests/test_api_spa.py -v && pytest -q`
Expected: PASS (신규 4건) + 기존 테스트 전부 통과(static_dir 미지정 시 마운트 없음).

- [ ] **Step 6: 커밋**

```bash
git add src/dms/config.py src/dms/api/app.py tests/test_api_spa.py
git commit -m "feat(api): serve built SPA with fallback when DMS_STATIC_DIR set"
```

---

## Task 2: 프론트엔드 스캐폴드 (Vite + Tailwind + Vitest)

`frontend/`를 생성하고 빌드·테스트 파이프라인을 세운다. 이 Task의 산출물은 "npm test와 npm build가 도는 빈 앱".

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`,
  `frontend/tailwind.config.ts`, `frontend/postcss.config.js`, `frontend/index.html`,
  `frontend/src/main.tsx`, `frontend/src/index.css`, `frontend/src/test/setup.ts`
- Test: `frontend/src/smoke.test.tsx`

**Interfaces:**
- Produces: npm scripts `dev`/`build`/`test`. Tailwind 토큰 클래스(`bg-canvas`, `text-ink`,
  `text-muted`, `bg-surface`, `text-accent`, `rounded-card`, `shadow-soft`).

- [ ] **Step 1: `package.json` 작성**

```json
{
  "name": "dms-portal",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "@radix-ui/react-dialog": "^1.1.2",
    "@radix-ui/react-select": "^2.1.2",
    "@tanstack/react-query": "^5.59.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.27.0"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.5.0",
    "@testing-library/react": "^16.0.1",
    "@testing-library/user-event": "^14.5.2",
    "@types/react": "^18.3.11",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.2",
    "autoprefixer": "^10.4.20",
    "jsdom": "^25.0.1",
    "msw": "^2.4.9",
    "postcss": "^8.4.47",
    "tailwindcss": "^3.4.13",
    "typescript": "^5.6.2",
    "vite": "^5.4.8",
    "vitest": "^2.1.2"
  }
}
```

- [ ] **Step 2: `vite.config.ts`(프록시 + vitest)**

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
  build: { outDir: "dist" },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
```

- [ ] **Step 3: `tailwind.config.ts`(C 토큰) + `postcss.config.js` + `index.css`**

```ts
// tailwind.config.ts
import type { Config } from "tailwindcss";
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#f6f6f3", surface: "#ffffff", ink: "#1c1d22",
        muted: "#5b6070", accent: "#6d5efc",
        ok: "#067647", okbg: "#e7f7ee",
        bad: "#b42318", badbg: "#fee4e2",
        busy: "#5b52d6", busybg: "#ecebff",
      },
      borderRadius: { card: "0.75rem" },
      boxShadow: { soft: "0 1px 2px rgba(16,24,40,.04), 0 4px 16px rgba(16,24,40,.06)" },
    },
  },
  plugins: [],
} satisfies Config;
```

```js
// postcss.config.js
export default { plugins: { tailwindcss: {}, autoprefixer: {} } };
```

```css
/* index.css */
@tailwind base;
@tailwind components;
@tailwind utilities;
html, body, #root { height: 100%; }
body { @apply bg-canvas text-ink; font-family: ui-sans-serif, system-ui, sans-serif; }
```

- [ ] **Step 4: `index.html` + `main.tsx`(빈 부트스트랩)**

```html
<!-- index.html -->
<!doctype html>
<html lang="ko"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>DMS</title></head>
<body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body></html>
```

```tsx
// main.tsx
import React from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
createRoot(document.getElementById("root")!).render(
  <React.StrictMode><div>DMS Portal</div></React.StrictMode>,
);
```

- [ ] **Step 5: `tsconfig.json` + 테스트 setup**

```json
{
  "compilerOptions": {
    "target": "ES2020", "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"], "module": "ESNext",
    "moduleResolution": "bundler", "jsx": "react-jsx", "strict": true,
    "noEmit": true, "skipLibCheck": true, "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src"]
}
```

```ts
// src/test/setup.ts
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
afterEach(() => cleanup());
```

- [ ] **Step 6: 스모크 테스트 작성 + 실행**

```tsx
// src/smoke.test.tsx
import { render, screen } from "@testing-library/react";
import { test, expect } from "vitest";

test("renders a heading", () => {
  render(<h1>DMS Portal</h1>);
  expect(screen.getByRole("heading", { name: "DMS Portal" })).toBeInTheDocument();
});
```

Run: `cd frontend && npm install && npm test && npm run build`
Expected: 스모크 테스트 PASS, `dist/` 생성.

- [ ] **Step 7: 커밋**

```bash
git add frontend/ && printf 'node_modules/\ndist/\n' > frontend/.gitignore && git add frontend/.gitignore
git commit -m "chore(portal): scaffold vite+react+tailwind+vitest frontend"
```

---

## Task 3: UI 프리미티브 (StatusPill 외)

C 토큰 위에 최소 컴포넌트 세트를 만든다. TDD 대상은 상태→pill variant 매핑(도메인 규칙).

**Files:**
- Create: `frontend/src/lib/jobState.ts`, `frontend/src/components/ui/StatusPill.tsx`,
  `Button.tsx`, `Card.tsx`, `Table.tsx`, `MetricTile.tsx`
- Test: `frontend/src/lib/jobState.test.ts`, `frontend/src/components/ui/StatusPill.test.tsx`

**Interfaces:**
- Produces: `TERMINAL_STATES: Set<string>`; `isTerminal(state: string): boolean`;
  `pillVariant(state: string): "ok" | "bad" | "busy" | "neutral"`;
  `<StatusPill state={string} />`.

- [ ] **Step 1: 실패 테스트 — `jobState.test.ts`**

```ts
import { isTerminal, pillVariant, TERMINAL_STATES } from "./jobState";
import { test, expect } from "vitest";

test("terminal states", () => {
  ["Succeeded", "Failed", "Rejected", "Cancelled", "PreviewExpired"]
    .forEach((s) => expect(isTerminal(s)).toBe(true));
  expect(isTerminal("Executing")).toBe(false);
  expect(TERMINAL_STATES.has("Succeeded")).toBe(true);
});

test("pill variant mapping: green=ok, red=bad, violet=busy", () => {
  expect(pillVariant("Succeeded")).toBe("ok");
  expect(pillVariant("Failed")).toBe("bad");
  expect(pillVariant("Rejected")).toBe("bad");
  expect(pillVariant("Cancelled")).toBe("bad");
  expect(pillVariant("Executing")).toBe("busy");
  expect(pillVariant("ConfirmPending")).toBe("busy");
  expect(pillVariant("Pending")).toBe("neutral");
});
```

- [ ] **Step 2: 실패 확인** — Run: `cd frontend && npx vitest run src/lib/jobState.test.ts` → FAIL(모듈 없음).

- [ ] **Step 3: 구현 — `jobState.ts`**

```ts
export const TERMINAL_STATES = new Set([
  "Succeeded", "Failed", "Rejected", "Cancelled", "PreviewExpired",
]);
export const isTerminal = (s: string) => TERMINAL_STATES.has(s);

export type PillVariant = "ok" | "bad" | "busy" | "neutral";
export function pillVariant(state: string): PillVariant {
  if (state === "Succeeded") return "ok";
  if (["Failed", "Rejected", "Cancelled", "PreviewExpired"].includes(state)) return "bad";
  if (["Executing", "ConfirmPending", "Planning", "Scheduled"].includes(state)) return "busy";
  return "neutral";
}
```

- [ ] **Step 4: StatusPill 테스트 — `StatusPill.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import { StatusPill } from "./StatusPill";
import { test, expect } from "vitest";

test("renders label and ok styling, no leading dot", () => {
  const { container } = render(<StatusPill state="Succeeded" />);
  expect(screen.getByText("Succeeded")).toBeInTheDocument();
  expect(container.querySelector(".text-ok")).not.toBeNull();
  // dot 금지: 자식은 텍스트 노드만
  expect(container.querySelectorAll("span[aria-hidden]").length).toBe(0);
});
```

- [ ] **Step 5: 구현 — `StatusPill.tsx` (+ Button/Card/Table/MetricTile)**

```tsx
// StatusPill.tsx
import { pillVariant } from "../../lib/jobState";
const CLS = {
  ok: "text-ok bg-okbg", bad: "text-bad bg-badbg",
  busy: "text-busy bg-busybg", neutral: "text-muted bg-canvas",
} as const;
export function StatusPill({ state }: { state: string }) {
  return (
    <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-semibold ${CLS[pillVariant(state)]}`}>
      {state}
    </span>
  );
}
```

```tsx
// Button.tsx — forwardRef so Radix `asChild` (e.g. Dialog.Trigger) can attach its
// ref; without it Radix's focus-return-to-trigger breaks and React warns.
import { forwardRef } from "react";
type Props = React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" };
export const Button = forwardRef<HTMLButtonElement, Props>(function Button(
  { variant = "primary", className = "", ...p }, ref) {
  const base = "inline-flex items-center justify-center rounded-lg px-3 py-2 text-sm font-medium disabled:opacity-50";
  const v = variant === "primary" ? "bg-accent text-white" : "bg-surface text-ink border border-black/10";
  return <button ref={ref} className={`${base} ${v} ${className}`} {...p} />;
});
```

```tsx
// Card.tsx
export function Card({ className = "", ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={`bg-surface rounded-card shadow-soft p-5 ${className}`} {...p} />;
}
```

```tsx
// Table.tsx — 가로 스크롤 컨테이너 포함
export function Table({ children }: { children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm text-left">{children}</table>
    </div>
  );
}
```

```tsx
// MetricTile.tsx
export function MetricTile({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="bg-surface rounded-card shadow-soft p-4">
      <div className="text-muted text-xs">{label}</div>
      <div className="text-2xl font-semibold mt-1">{value}</div>
    </div>
  );
}
```

- [ ] **Step 6: 통과 확인 + 커밋**

Run: `cd frontend && npm test`  → PASS
```bash
git add frontend/src/lib/jobState.ts frontend/src/lib/jobState.test.ts frontend/src/components/ui/
git commit -m "feat(portal): status/job-state helpers and base UI primitives"
```

---

## Task 4: API 클라이언트 + reason_code 맵

`lib/api.ts`는 타입드 fetch 래퍼다. 에러 응답의 `detail`(reason_code)을 `ApiError`로 던지고,
401은 별도로 표시한다. 화면 훅들이 여기에만 의존한다.

**Files:**
- Create: `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`, `frontend/src/test/msw.ts`
- Test: `frontend/src/lib/api.test.ts`

**Interfaces:**
- Produces: `apiGet<T>(path): Promise<T>`; `apiSend<T>(method, path, body?): Promise<T>`;
  `class ApiError { status: number; code: string; message: string }`(message=한글 매핑);
  `REASON_MESSAGES: Record<string,string>`. 타입: `Me`, `RequestRow`, `RequestDetail`,
  `DataJob`, `Storage`, `Node`(아래 `types.ts`).

- [ ] **Step 1: `types.ts` 작성** (백엔드 컬럼 기준)

```ts
export type Role = "user" | "admin";
export interface Me { actor: string; role: Role }
// state_transitions rows come back as SELECT * → from_state/to_state/reason_code/actor/at
export interface Transition {
  from_state: string | null; to_state: string;
  reason_code?: string | null; actor?: string; at: string;
}
export interface RequestRow {
  request_id: string; operation: string; requester_id: string; resource_key: string;
  priority: string; state: string; created_at: string; updated_at: string;
  payload: Record<string, unknown>;   // backend load_json's it → object, NOT a JSON string
}
export interface RequestDetail extends RequestRow { transitions: Transition[] }
export interface DataJob {
  job_id: string; request_id: string; operation: string; state: string;
  reason_code: string | null; preview_fingerprint: string | null;
  preview_expires_at: string | null;
  result_summary: unknown;             // JSON column, hydrated → string | object | null
  transitions: Transition[];
}
export interface Storage {
  storage_name: string; mount_path: string; backend_type: string;
  enabled: number; status: string; status_detail: string | null;
}
// backend list_nodes returns `fresh` (NOT stale) — inverted meaning
export interface Node { node_name: string; reported_at: string; fresh: boolean; report: unknown }
```

- [ ] **Step 2: 실패 테스트 — `api.test.ts`** (MSW로 성공/에러/401 검증)

```ts
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect, vi } from "vitest";
import { apiGet, apiSend, ApiError } from "./api";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("apiGet returns json", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json({ actor: "alice", role: "user" })));
  await expect(apiGet("/api/auth/me")).resolves.toEqual({ actor: "alice", role: "user" });
});

test("error maps reason_code to korean message", async () => {
  server.use(http.post("/api/user/jobs/j1:confirm",
    () => HttpResponse.json({ detail: "fingerprint_mismatch" }, { status: 409 })));
  await expect(apiSend("POST", "/api/user/jobs/j1:confirm", { fingerprint: "x" }))
    .rejects.toMatchObject({ status: 409, code: "fingerprint_mismatch",
      message: "미리보기가 변경되었습니다. 다시 확인해 주세요" });
});

test("unknown reason_code falls back to raw code", async () => {
  server.use(http.get("/api/x", () => HttpResponse.json({ detail: "weird_thing" }, { status: 400 })));
  await expect(apiGet("/api/x")).rejects.toMatchObject({ code: "weird_thing", message: "weird_thing" });
});

test("401 dispatches auth-expired event", async () => {
  const spy = vi.fn();
  window.addEventListener("dms:unauthorized", spy);
  server.use(http.get("/api/auth/me", () => HttpResponse.json({ detail: "x" }, { status: 401 })));
  await expect(apiGet("/api/auth/me")).rejects.toBeInstanceOf(ApiError);
  expect(spy).toHaveBeenCalledOnce();
});
```

- [ ] **Step 3: 실패 확인** — Run: `cd frontend && npx vitest run src/lib/api.test.ts` → FAIL.

- [ ] **Step 4: 구현 — `api.ts`**

```ts
export const REASON_MESSAGES: Record<string, string> = {
  invalid_credentials: "사용자명 또는 비밀번호가 올바르지 않습니다",
  fingerprint_mismatch: "미리보기가 변경되었습니다. 다시 확인해 주세요",
  preview_expired: "미리보기가 만료되었습니다. 다시 제출해 주세요",
  not_confirmable: "이미 처리된 작업입니다",
  already_terminal: "이미 처리된 작업입니다",
  privileged_not_authorized: "권한 있는 요청자가 아닙니다",
  resource_conflict: "동일 대상에 진행 중인 작업이 있습니다",
  no_eligible_nodes: "실행 가능한 노드가 없습니다",
  no_ready_sync_candidate: "실행 가능한 노드가 없습니다",
};

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "include",
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent("dms:unauthorized"));
    throw new ApiError(401, "unauthorized", REASON_MESSAGES.invalid_credentials);
  }
  if (!res.ok) {
    let code = `http_${res.status}`;
    try { code = (await res.json()).detail ?? code; } catch { /* noop */ }
    throw new ApiError(res.status, code, REASON_MESSAGES[code] ?? code);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const apiGet = <T>(path: string) => request<T>("GET", path);
export const apiSend = <T>(method: string, path: string, body?: unknown) =>
  request<T>(method, path, body);
```

- [ ] **Step 5: 통과 확인 + 커밋**

Run: `cd frontend && npm test` → PASS
```bash
git add frontend/src/lib/api.ts frontend/src/lib/types.ts frontend/src/lib/api.test.ts frontend/src/test/msw.ts
git commit -m "feat(portal): typed api client with reason-code mapping and 401 handling"
```

(참고: `src/test/msw.ts`는 이후 화면 테스트가 공유할 `setupServer()` 팩토리 — 이 Task에서 빈
`export const server = setupServer()`로 만들어 두고 화면 테스트에서 핸들러를 `server.use`로 주입.)

---

## Task 5: 인증 — AuthContext + Login 화면

세션 부트스트랩(`/api/auth/me`), 로그인/로그아웃, 전역 401 처리, 로그인 폼을 만든다.

**Files:**
- Create: `frontend/src/app/AuthContext.tsx`, `frontend/src/app/queryClient.ts`,
  `frontend/src/features/auth/useAuth.ts`, `frontend/src/features/auth/Login.tsx`
- Test: `frontend/src/features/auth/Login.test.tsx`

**Interfaces:**
- Consumes: `apiGet`, `apiSend`, `Me`(Task 4).
- Produces: `useMe(): {data?: Me, isLoading, isError}`; `useLogin()`(mutation, `{username,password}`);
  `useLogout()`(mutation); `<Login />`. `AuthProvider`는 `dms:unauthorized` 이벤트 수신 시
  `['auth','me']` 쿼리를 무효화한다.

- [ ] **Step 1: 실패 테스트 — `Login.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { Login } from "./Login";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderLogin() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><Login /></QueryClientProvider>);
}

test("submits credentials and shows error on 401", async () => {
  server.use(http.post("/api/auth/login",
    () => HttpResponse.json({ detail: "invalid_credentials" }, { status: 401 })));
  renderLogin();
  await userEvent.type(screen.getByLabelText("사용자명"), "alice");
  await userEvent.type(screen.getByLabelText("비밀번호"), "bad");
  await userEvent.click(screen.getByRole("button", { name: "로그인" }));
  expect(await screen.findByText("사용자명 또는 비밀번호가 올바르지 않습니다")).toBeInTheDocument();
});
```

- [ ] **Step 2: 실패 확인** — Run: `cd frontend && npx vitest run src/features/auth/Login.test.tsx` → FAIL.

- [ ] **Step 3: 구현 — `queryClient.ts`, `useAuth.ts`, `AuthContext.tsx`, `Login.tsx`**

```ts
// queryClient.ts
import { QueryClient } from "@tanstack/react-query";
export const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: 5000 } },
});
```

```ts
// useAuth.ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { Me } from "../../lib/types";

export const useMe = () =>
  useQuery({ queryKey: ["auth", "me"], queryFn: () => apiGet<Me>("/api/auth/me") });

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { username: string; password: string }) =>
      apiSend<Me>("POST", "/api/auth/login", b),
    // clear ALL cached data on login, not just me — otherwise the previous
    // user's jobs/storages/nodes queries linger and the new user briefly sees
    // them (cross-user leak). me is refetched fresh when the shell mounts.
    onSuccess: () => qc.clear(),
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend("POST", "/api/auth/logout"),
    // onSettled (not onSuccess): clear the client cache even if the logout
    // POST fails — the user intends to log out; never leave stale session data.
    onSettled: () => qc.clear(),
  });
}
```

```tsx
// AuthContext.tsx — 전역 401 → me 무효화
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  useEffect(() => {
    // session invalidated (any 401) → drop ALL cached authorized data, not just
    // me, so no stale user data lingers. RequireRole (Task 6) redirects to
    // /login on the me error, which unmounts the me observer and bounds refetch.
    const h = () => qc.clear();
    window.addEventListener("dms:unauthorized", h);
    return () => window.removeEventListener("dms:unauthorized", h);
  }, [qc]);
  return <>{children}</>;
}
```

```tsx
// Login.tsx
import { useState } from "react";
import { useLogin } from "./useAuth";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ApiError } from "../../lib/api";

export function Login() {
  const [username, setU] = useState(""); const [password, setP] = useState("");
  const login = useLogin();
  return (
    <div className="min-h-full grid place-items-center p-6">
      <Card className="w-full max-w-sm">
        <h1 className="text-lg font-semibold mb-4">DMS 로그인</h1>
        <form onSubmit={(e) => { e.preventDefault(); login.mutate({ username, password }); }}
              className="space-y-3">
          <label className="block text-sm">사용자명
            <input aria-label="사용자명" className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2"
                   value={username} onChange={(e) => setU(e.target.value)} />
          </label>
          <label className="block text-sm">비밀번호
            <input aria-label="비밀번호" type="password" className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2"
                   value={password} onChange={(e) => setP(e.target.value)} />
          </label>
          {login.isError && (
            <p className="text-bad text-sm">{(login.error as ApiError).message}</p>
          )}
          <Button type="submit" className="w-full" disabled={login.isPending}>로그인</Button>
        </form>
      </Card>
    </div>
  );
}
```

- [ ] **Step 4: 통과 확인 + 커밋**

Run: `cd frontend && npm test` → PASS
```bash
git add frontend/src/app/AuthContext.tsx frontend/src/app/queryClient.ts frontend/src/features/auth/
git commit -m "feat(portal): session auth context, hooks, and login screen"
```

---

## Task 6: 앱 셸 + 라우팅 + role 가드

인증 부트스트랩으로 셸/로그인을 가르고, role별 내비와 라우트 가드를 세운다. 배치 작업 항목은
disabled로 자리만 둔다.

**Files:**
- Create: `frontend/src/app/AppShell.tsx`, `frontend/src/app/RequireRole.tsx`,
  `frontend/src/app/router.tsx`
- Modify: `frontend/src/main.tsx`(Provider+Router 부트스트랩)
- Test: `frontend/src/app/router.test.tsx`

**Interfaces:**
- Consumes: `useMe`(Task 5), `queryClient`, `AuthProvider`.
- Produces: `<AppRouter />`(라우트 트리); `<RequireRole role="admin">`; `<AppShell>`.
  라우트: `/login`, `/jobs`, `/jobs/new`, `/jobs/:requestId`, `/admin/storages`, `/admin/dashboard`.
  기본 랜딩 user→`/jobs`, admin→`/admin/dashboard`.

- [ ] **Step 1: 실패 테스트 — `router.test.tsx`** (미인증→로그인, user가 admin 라우트→리다이렉트)

```tsx
import { render, screen } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { AppRouter } from "./router";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}><AppRouter /></MemoryRouter>
    </QueryClientProvider>,
  );
}

test("unauthenticated shows login", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json({ detail: "x" }, { status: 401 })));
  renderAt("/jobs");
  expect(await screen.findByRole("button", { name: "로그인" })).toBeInTheDocument();
});

test("user visiting admin route is redirected to /jobs", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "alice", role: "user" })),
    http.get("/api/user/requests", () => HttpResponse.json([])),
  );
  renderAt("/admin/dashboard");
  expect(await screen.findByRole("heading", { name: "내 작업" })).toBeInTheDocument();
});
```

- [ ] **Step 2: 실패 확인** — Run: `cd frontend && npx vitest run src/app/router.test.tsx` → FAIL.

- [ ] **Step 3: 구현 — `RequireRole.tsx`, `AppShell.tsx`, `router.tsx`**

```tsx
// RequireRole.tsx
import { Navigate } from "react-router-dom";
import { useMe } from "../features/auth/useAuth";
export function RequireRole({ role, children }: { role?: "admin"; children: React.ReactNode }) {
  const me = useMe();
  if (me.isLoading) return <div className="p-6 text-muted">불러오는 중…</div>;
  if (me.isError || !me.data) return <Navigate to="/login" replace />;
  if (role === "admin" && me.data.role !== "admin") return <Navigate to="/jobs" replace />;
  return <>{children}</>;
}
```

```tsx
// AppShell.tsx
import { NavLink } from "react-router-dom";
import { useMe, useLogout } from "../features/auth/useAuth";
const linkCls = ({ isActive }: { isActive: boolean }) =>
  `block rounded-lg px-3 py-2 text-sm ${isActive ? "bg-accent text-white" : "text-ink hover:bg-black/5"}`;
export function AppShell({ children }: { children: React.ReactNode }) {
  const me = useMe(); const logout = useLogout(); const isAdmin = me.data?.role === "admin";
  return (
    <div className="min-h-full md:flex">
      <aside className="md:w-60 md:min-h-full bg-surface md:shadow-soft p-3 space-y-1">
        <div className="px-3 py-2 font-semibold">DMS</div>
        <NavLink to="/jobs" className={linkCls}>내 작업</NavLink>
        <NavLink to="/jobs/new" className={linkCls}>작업 제출</NavLink>
        {isAdmin && <NavLink to="/admin/storages" className={linkCls}>스토리지</NavLink>}
        {isAdmin && <NavLink to="/admin/dashboard" className={linkCls}>대시보드</NavLink>}
        <span className="block rounded-lg px-3 py-2 text-sm text-muted opacity-50 cursor-not-allowed"
              aria-disabled="true" title="다음 예정">배치 작업 · 준비 중</span>
      </aside>
      <div className="flex-1">
        <header className="flex items-center justify-between px-5 h-14 bg-surface shadow-soft">
          <div className="text-sm text-muted">{me.data?.actor} · {me.data?.role}</div>
          <button className="text-sm text-accent" onClick={() => logout.mutate()}>로그아웃</button>
        </header>
        <main className="p-5">{children}</main>
      </div>
    </div>
  );
}
```

```tsx
// router.tsx
import { Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "./AuthContext";
import { AppShell } from "./AppShell";
import { RequireRole } from "./RequireRole";
import { useMe } from "../features/auth/useAuth";
import { Login } from "../features/auth/Login";
import { JobsList } from "../features/jobs/JobsList";
import { SubmitSync } from "../features/jobs/SubmitSync";
import { RequestDetail } from "../features/jobs/RequestDetail";
import { StoragesList } from "../features/storages/StoragesList";
import { Dashboard } from "../features/dashboard/Dashboard";

function Home() {
  const me = useMe();
  if (me.isLoading) return <div className="p-6 text-muted">불러오는 중…</div>;
  if (me.data?.role === "admin") return <Navigate to="/admin/dashboard" replace />;
  return <Navigate to="/jobs" replace />;
}

export function AppRouter() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<Home />} />
        <Route path="/jobs" element={<RequireRole><AppShell><JobsList /></AppShell></RequireRole>} />
        <Route path="/jobs/new" element={<RequireRole><AppShell><SubmitSync /></AppShell></RequireRole>} />
        <Route path="/jobs/:requestId" element={<RequireRole><AppShell><RequestDetail /></AppShell></RequireRole>} />
        <Route path="/admin/storages" element={<RequireRole role="admin"><AppShell><StoragesList /></AppShell></RequireRole>} />
        <Route path="/admin/dashboard" element={<RequireRole role="admin"><AppShell><Dashboard /></AppShell></RequireRole>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
```

`main.tsx`를 Provider+BrowserRouter로 교체:

```tsx
import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { queryClient } from "./app/queryClient";
import { AppRouter } from "./app/router";
import "./index.css";
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter><AppRouter /></BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
```

> 주: 이 Task의 router 테스트는 Task 7~11의 화면 컴포넌트를 import한다. 순서상 먼저 각 화면의
> **빈 스텁**(예: `export function JobsList(){return <h1>내 작업</h1>}`)을 만들고 진행하거나,
> subagent-driven 실행 시 Task 7~11을 먼저 구현한 뒤 이 Task의 통합 테스트를 통과시켜도 된다.
> 스텁으로 시작하면 각 후속 Task가 스텁을 실제 구현으로 대체한다.

- [ ] **Step 4: 통과 확인 + 커밋**

Run: `cd frontend && npm test` → PASS
```bash
git add frontend/src/app/ frontend/src/main.tsx frontend/src/features/*/*.tsx
git commit -m "feat(portal): app shell, routing, and role guards"
```

---

## Task 7: 내 작업 목록 + 상세

`GET /api/user/requests`로 목록, `refetchInterval`로 비종단 잡 폴링, 행 상세는
`GET /api/user/requests/{id}` + `.../jobs`.

**Files:**
- Create/replace: `frontend/src/features/jobs/JobsList.tsx`, `RequestDetail.tsx`,
  `frontend/src/features/jobs/useJobs.ts`
- Test: `frontend/src/features/jobs/JobsList.test.tsx`

**Interfaces:**
- Consumes: `apiGet`, 타입 `RequestRow`/`RequestDetail`/`DataJob`, `StatusPill`, `Table`, `isTerminal`.
- Produces: `useRequests()`; `useRequest(id)`; `useRequestJobs(id)`; `<JobsList/>`; `<RequestDetail/>`.
  `useRequestJobs`는 반환 잡 중 비종단이 있으면 `refetchInterval: 2000`, 전부 종단이면 `false`.

- [ ] **Step 1: 실패 테스트 — `JobsList.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { JobsList } from "./JobsList";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("lists requests with status pill", async () => {
  server.use(http.get("/api/user/requests", () => HttpResponse.json([
    { request_id: "r1", operation: "sync", state: "Succeeded", priority: "mid",
      created_at: "2026-08-04T00:00:00Z", updated_at: "", requester_id: "alice",
      resource_key: "k", payload: {} },
  ])));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><JobsList /></MemoryRouter></QueryClientProvider>);
  expect(await screen.findByText("r1")).toBeInTheDocument();
  expect(screen.getByText("Succeeded")).toBeInTheDocument();
});
```

- [ ] **Step 2: 실패 확인** — Run: `cd frontend && npx vitest run src/features/jobs/JobsList.test.tsx` → FAIL.

- [ ] **Step 3: 구현 — `useJobs.ts`, `JobsList.tsx`, `RequestDetail.tsx`**

```ts
// useJobs.ts
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import { isTerminal } from "../../lib/jobState";
import type { RequestRow, RequestDetail, DataJob } from "../../lib/types";

export const useRequests = () =>
  useQuery({ queryKey: ["requests"], queryFn: () => apiGet<RequestRow[]>("/api/user/requests"),
            refetchInterval: 3000 });

export const useRequest = (id: string) =>
  useQuery({ queryKey: ["request", id], queryFn: () => apiGet<RequestDetail>(`/api/user/requests/${id}`) });

export const useRequestJobs = (id: string) =>
  useQuery({
    queryKey: ["request", id, "jobs"],
    queryFn: () => apiGet<DataJob[]>(`/api/user/requests/${id}/jobs`),
    refetchInterval: (q) => {
      const jobs = q.state.data as DataJob[] | undefined;
      return jobs && jobs.some((j) => !isTerminal(j.state)) ? 2000 : false;
    },
  });
```

```tsx
// JobsList.tsx
import { Link } from "react-router-dom";
import { useRequests } from "./useJobs";
import { Table } from "../../components/ui/Table";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";

export function JobsList() {
  const q = useRequests();
  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">내 작업</h1>
        <Link to="/jobs/new"><Button>작업 제출</Button></Link>
      </div>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : (
        <Table>
          <thead><tr className="text-muted"><th className="py-2">요청</th><th>작업</th><th>상태</th><th>생성</th></tr></thead>
          <tbody>
            {(q.data ?? []).map((r) => (
              <tr key={r.request_id} className="border-t border-black/5">
                <td className="py-2"><Link className="text-accent" to={`/jobs/${r.request_id}`}>{r.request_id}</Link></td>
                <td>{r.operation}</td><td><StatusPill state={r.state} /></td>
                <td className="text-muted">{r.created_at}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </section>
  );
}
```

```tsx
// RequestDetail.tsx (상세 + 잡 목록; 확인 게이트는 Task 9에서 ConfirmDialog 삽입)
import { useParams } from "react-router-dom";
import { useRequest, useRequestJobs } from "./useJobs";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";

export function RequestDetail() {
  const { requestId = "" } = useParams();
  const req = useRequest(requestId); const jobs = useRequestJobs(requestId);
  return (
    <section className="space-y-4">
      <h1 className="text-lg font-semibold">요청 {requestId}</h1>
      <Card>
        <div className="flex items-center gap-3">
          <StatusPill state={req.data?.state ?? "…"} />
          <span className="text-muted text-sm">{req.data?.operation}</span>
        </div>
      </Card>
      <div className="space-y-2">
        {(jobs.data ?? []).map((j) => (
          <Card key={j.job_id}>
            <div className="flex items-center justify-between">
              <span className="text-sm">{j.job_id}</span><StatusPill state={j.state} />
            </div>
            {j.reason_code && <p className="text-bad text-sm mt-1">{j.reason_code}</p>}
          </Card>
        ))}
      </div>
    </section>
  );
}
```

- [ ] **Step 4: 통과 확인 + 커밋**

Run: `cd frontend && npm test` → PASS
```bash
git add frontend/src/features/jobs/useJobs.ts frontend/src/features/jobs/JobsList.tsx frontend/src/features/jobs/RequestDetail.tsx frontend/src/features/jobs/JobsList.test.tsx
git commit -m "feat(portal): my-jobs list and request detail with status polling"
```

---

## Task 8: sync 제출 폼

source/destination(storage+path) + options + priority를 받아 `POST /api/user/requests`. 성공 시 상세로 이동.

**Files:**
- Create/replace: `frontend/src/features/jobs/SubmitSync.tsx`
- Modify: `frontend/src/features/jobs/useJobs.ts`(useSubmitSync 추가), `useStorages.ts`(Task 10 산출; 없으면 임시 인라인)
- Test: `frontend/src/features/jobs/SubmitSync.test.tsx`

**Interfaces:**
- Consumes: `apiSend`, `useStorages`(Task 10; storage 셀렉트 옵션 — 이 Task를 먼저 하면
  `useStorages`를 `useJobs.ts`에 임시로 `apiGet<Storage[]>("/api/admin/storages")` 인라인해도 되나,
  일반 user는 admin API 접근 불가 → **셀렉트 대신 storage 이름 텍스트 입력**으로 구현**한다**).
- Produces: `useSubmitSync()`(mutation, body `{source_storage,source,destination_storage,destination,options,priority}`);
  `<SubmitSync/>`. 성공 시 `navigate('/jobs/'+request_id)`.

> 결정: 슬라이스 1에서 storage 목록 API는 admin 전용이므로, **user용 제출 폼은 storage를 텍스트로
> 입력**한다(백엔드가 존재/권한 검증). 셀렉트 드롭다운은 user용 storage 목록 API가 생기는 후속 슬라이스에서.

- [ ] **Step 1: 실패 테스트 — `SubmitSync.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { SubmitSync } from "./SubmitSync";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("submits sync request and navigates to detail", async () => {
  let received: any = null;
  server.use(http.post("/api/user/requests", async ({ request }) => {
    received = await request.json();
    return HttpResponse.json({ request_id: "r9", state: "Pending" }, { status: 202 });
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}>
    <MemoryRouter initialEntries={["/jobs/new"]}>
      <Routes>
        <Route path="/jobs/new" element={<SubmitSync />} />
        <Route path="/jobs/:id" element={<h1>요청 r9</h1>} />
      </Routes>
    </MemoryRouter></QueryClientProvider>);
  await userEvent.type(screen.getByLabelText("소스 스토리지"), "cephfs");
  await userEvent.type(screen.getByLabelText("소스 경로"), "a/b");
  await userEvent.type(screen.getByLabelText("목적지 스토리지"), "cephfs-secondary");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "c/d");
  await userEvent.click(screen.getByRole("button", { name: "제출" }));
  expect(await screen.findByRole("heading", { name: "요청 r9" })).toBeInTheDocument();
  expect(received).toMatchObject({ operation: "sync", source_storage: "cephfs",
    source: "a/b", destination_storage: "cephfs-secondary", destination: "c/d" });
});
```

- [ ] **Step 2: 실패 확인** — Run: `cd frontend && npx vitest run src/features/jobs/SubmitSync.test.tsx` → FAIL.

- [ ] **Step 3: 구현 — `useSubmitSync`(useJobs.ts에 추가) + `SubmitSync.tsx`**

```ts
// useJobs.ts 에 추가
import { useMutation } from "@tanstack/react-query";
import { apiSend } from "../../lib/api";
export interface SyncBody {
  source_storage: string; source: string;
  destination_storage: string; destination: string;
  options: Record<string, boolean | number>; priority: string;
}
export const useSubmitSync = () =>
  useMutation({
    mutationFn: (b: SyncBody) =>
      apiSend<{ request_id: string; state: string }>("POST", "/api/user/requests",
        { operation: "sync", ...b }),
  });
```

```tsx
// SubmitSync.tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSubmitSync } from "./useJobs";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";

const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";

export function SubmitSync() {
  const nav = useNavigate(); const submit = useSubmitSync();
  const [f, setF] = useState({ ss: "", sp: "", ds: "", dp: "", del: false, priority: "mid" });
  const on = (k: string) => (e: any) =>
    setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  return (
    <Card className="max-w-xl">
      <h1 className="text-lg font-semibold mb-4">작업 제출 · sync</h1>
      <form className="space-y-3" onSubmit={(e) => {
        e.preventDefault();
        submit.mutate(
          { source_storage: f.ss, source: f.sp, destination_storage: f.ds, destination: f.dp,
            options: f.del ? { delete: true } : {}, priority: f.priority },
          { onSuccess: (r) => nav(`/jobs/${r.request_id}`) });
      }}>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm">소스 스토리지<input aria-label="소스 스토리지" className={field} value={f.ss} onChange={on("ss")} /></label>
          <label className="text-sm">소스 경로<input aria-label="소스 경로" className={field} value={f.sp} onChange={on("sp")} /></label>
          <label className="text-sm">목적지 스토리지<input aria-label="목적지 스토리지" className={field} value={f.ds} onChange={on("ds")} /></label>
          <label className="text-sm">목적지 경로<input aria-label="목적지 경로" className={field} value={f.dp} onChange={on("dp")} /></label>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={f.del} onChange={on("del")} /> --delete (목적지 여분 삭제)
        </label>
        <label className="text-sm block">우선순위
          <select aria-label="우선순위" className={field} value={f.priority} onChange={on("priority")}>
            <option value="low">low</option><option value="mid">mid</option><option value="high">high</option>
          </select>
        </label>
        {submit.isError && <p className="text-bad text-sm">{(submit.error as ApiError).message}</p>}
        <Button type="submit" disabled={submit.isPending}>제출</Button>
      </form>
    </Card>
  );
}
```

- [ ] **Step 4: 통과 확인 + 커밋**

Run: `cd frontend && npm test` → PASS
```bash
git add frontend/src/features/jobs/SubmitSync.tsx frontend/src/features/jobs/useJobs.ts frontend/src/features/jobs/SubmitSync.test.tsx
git commit -m "feat(portal): sync submit form"
```

---

## Task 9: 확인 게이트 (ConfirmDialog) + 취소

잡이 `ConfirmPending`이면 preview/fingerprint/만료를 Radix Dialog로 보이고, 확인 시
`:confirm`, 진행 중이면 `:cancel`.

**Files:**
- Create: `frontend/src/features/jobs/ConfirmDialog.tsx`, `frontend/src/components/ui/Dialog.tsx`
- Modify: `frontend/src/features/jobs/useJobs.ts`(useConfirmJob/useCancelJob),
  `frontend/src/features/jobs/RequestDetail.tsx`(ConfirmPending 시 다이얼로그·취소 버튼)
- Test: `frontend/src/features/jobs/ConfirmDialog.test.tsx`

**Interfaces:**
- Consumes: `apiSend`, `DataJob`.
- Produces: `useConfirmJob()`(`{jobId,fingerprint}` → POST `:confirm`);
  `useCancelJob()`(`jobId` → POST `:cancel`); `<ConfirmDialog job={DataJob} />`.
  성공/에러 시 `['request', requestId, 'jobs']` 무효화.

- [ ] **Step 1: 실패 테스트 — `ConfirmDialog.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const job = { job_id: "j1", request_id: "r1", operation: "sync", state: "ConfirmPending",
  reason_code: null, preview_fingerprint: "abc123", preview_expires_at: "2099-01-01T00:00:00Z",
  result_summary: "3 files, 12 MiB", transitions: [] };

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

test("shows fingerprint and posts it on confirm", async () => {
  let body: any = null;
  server.use(http.post("/api/user/jobs/j1:confirm", async ({ request }) => {
    body = await request.json();
    return HttpResponse.json({ state: "Executing" });
  }));
  wrap(<ConfirmDialog job={job as any} />);
  await userEvent.click(screen.getByRole("button", { name: "미리보기 확인" }));
  expect(await screen.findByText(/abc123/)).toBeInTheDocument();
  expect(screen.getByText(/3 files/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "확인" }));
  await screen.findByText(/./); // flush
  expect(body).toEqual({ fingerprint: "abc123" });
});

test("shows error message on fingerprint mismatch", async () => {
  server.use(http.post("/api/user/jobs/j1:confirm",
    () => HttpResponse.json({ detail: "fingerprint_mismatch" }, { status: 409 })));
  wrap(<ConfirmDialog job={job as any} />);
  await userEvent.click(screen.getByRole("button", { name: "미리보기 확인" }));
  await userEvent.click(screen.getByRole("button", { name: "확인" }));
  expect(await screen.findByText("미리보기가 변경되었습니다. 다시 확인해 주세요")).toBeInTheDocument();
});
```

- [ ] **Step 2: 실패 확인** — Run: `cd frontend && npx vitest run src/features/jobs/ConfirmDialog.test.tsx` → FAIL.

- [ ] **Step 3: 구현 — `Dialog.tsx`, mutations, `ConfirmDialog.tsx`**

```tsx
// components/ui/Dialog.tsx (Radix wrapper)
import * as D from "@radix-ui/react-dialog";
export function Dialog({ trigger, title, children, open, onOpenChange }: {
  trigger: React.ReactNode; title: string; children: React.ReactNode;
  open?: boolean; onOpenChange?: (o: boolean) => void;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Trigger asChild>{trigger}</D.Trigger>
      <D.Portal>
        <D.Overlay className="fixed inset-0 bg-black/30" />
        <D.Content className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 bg-surface rounded-card shadow-soft p-5 w-full max-w-md">
          <D.Title className="text-base font-semibold mb-3">{title}</D.Title>
          {children}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
```

```ts
// useJobs.ts 에 추가
import { useQueryClient } from "@tanstack/react-query";
export function useConfirmJob(requestId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { jobId: string; fingerprint: string }) =>
      apiSend("POST", `/api/user/jobs/${v.jobId}:confirm`, { fingerprint: v.fingerprint }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["request", requestId, "jobs"] }),
  });
}
export function useCancelJob(requestId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => apiSend("POST", `/api/user/jobs/${jobId}:cancel`),
    onSettled: () => qc.invalidateQueries({ queryKey: ["request", requestId, "jobs"] }),
  });
}
```

```tsx
// ConfirmDialog.tsx
import { useState } from "react";
import type { DataJob } from "../../lib/types";
import { useConfirmJob } from "./useJobs";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";

export function ConfirmDialog({ job }: { job: DataJob }) {
  const [open, setOpen] = useState(false);
  const confirm = useConfirmJob(job.request_id);
  return (
    <Dialog open={open} onOpenChange={setOpen} title="sync 미리보기 확인"
            trigger={<Button>미리보기 확인</Button>}>
      <div className="space-y-2 text-sm">
        <p className="text-muted">아래 dry-run 결과를 확인하고 실행하세요.</p>
        <pre className="bg-canvas rounded-lg p-3 whitespace-pre-wrap">{
          job.result_summary == null ? "(요약 없음)"
            : typeof job.result_summary === "string" ? job.result_summary
            : JSON.stringify(job.result_summary, null, 2)
        }</pre>
        <p className="text-muted">지문(fingerprint): <code>{job.preview_fingerprint}</code></p>
        <p className="text-muted">만료: {job.preview_expires_at}</p>
        {confirm.isError && <p className="text-bad">{(confirm.error as ApiError).message}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" onClick={() => setOpen(false)}>닫기</Button>
          <Button disabled={confirm.isPending || !job.preview_fingerprint}
                  onClick={() => confirm.mutate(
                    { jobId: job.job_id, fingerprint: job.preview_fingerprint! },
                    { onSuccess: () => setOpen(false) })}>확인</Button>
        </div>
      </div>
    </Dialog>
  );
}
```

`RequestDetail.tsx`의 잡 카드에 상태 분기 추가: `j.state === "ConfirmPending"`이면
`<ConfirmDialog job={j} />`, 비종단이면 취소 버튼(`useCancelJob`).

- [ ] **Step 4: 통과 확인 + 커밋**

Run: `cd frontend && npm test` → PASS
```bash
git add frontend/src/components/ui/Dialog.tsx frontend/src/features/jobs/ConfirmDialog.tsx frontend/src/features/jobs/useJobs.ts frontend/src/features/jobs/RequestDetail.tsx frontend/src/features/jobs/ConfirmDialog.test.tsx
git commit -m "feat(portal): preview->confirm gate dialog and job cancel"
```

---

## Task 10: 스토리지 목록 (admin)

`GET /api/admin/storages` 표.

**Files:**
- Create/replace: `frontend/src/features/storages/StoragesList.tsx`, `useStorages.ts`
- Test: `frontend/src/features/storages/StoragesList.test.tsx`

**Interfaces:**
- Consumes: `apiGet`, `Storage`, `Table`, `StatusPill`.
- Produces: `useStorages()`; `<StoragesList/>`.

- [ ] **Step 1: 실패 테스트 — `StoragesList.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { StoragesList } from "./StoragesList";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("lists storages", async () => {
  server.use(http.get("/api/admin/storages", () => HttpResponse.json([
    { storage_name: "cephfs", mount_path: "/cephfs", backend_type: "cephfs",
      enabled: 1, status: "Healthy", status_detail: null },
  ])));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><StoragesList /></QueryClientProvider>);
  expect(await screen.findByText("cephfs")).toBeInTheDocument();
  expect(screen.getByText("Healthy")).toBeInTheDocument();
});
```

- [ ] **Step 2: 실패 확인** — Run: `cd frontend && npx vitest run src/features/storages/StoragesList.test.tsx` → FAIL.

- [ ] **Step 3: 구현 — `useStorages.ts`, `StoragesList.tsx`**

```ts
// useStorages.ts
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { Storage } from "../../lib/types";
export const useStorages = () =>
  useQuery({ queryKey: ["storages"], queryFn: () => apiGet<Storage[]>("/api/admin/storages") });
```

```tsx
// StoragesList.tsx
import { useStorages } from "./useStorages";
import { Table } from "../../components/ui/Table";
import { StatusPill } from "../../components/ui/StatusPill";
export function StoragesList() {
  const q = useStorages();
  return (
    <section className="space-y-4">
      <h1 className="text-lg font-semibold">스토리지</h1>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : (
        <Table>
          <thead><tr className="text-muted"><th className="py-2">이름</th><th>백엔드</th><th>마운트</th><th>상태</th></tr></thead>
          <tbody>
            {(q.data ?? []).map((s) => (
              <tr key={s.storage_name} className="border-t border-black/5">
                <td className="py-2">{s.storage_name}</td><td>{s.backend_type}</td>
                <td className="text-muted">{s.mount_path}</td><td><StatusPill state={s.status} /></td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </section>
  );
}
```

- [ ] **Step 4: 통과 확인 + 커밋**

Run: `cd frontend && npm test` → PASS
```bash
git add frontend/src/features/storages/
git commit -m "feat(portal): admin storages list"
```

---

## Task 11: 운영 대시보드 (admin)

지표 타일(requests 클라이언트 집계) + 노드 상태(`/api/admin/nodes`) + 최근 작업.

**Files:**
- Create/replace: `frontend/src/features/dashboard/Dashboard.tsx`, `useDashboard.ts`
- Test: `frontend/src/features/dashboard/Dashboard.test.tsx`

**Interfaces:**
- Consumes: `useRequests`(Task 7), `apiGet`, `Node`, `MetricTile`, `StatusPill`, `isTerminal`.
- Produces: `useNodes()`; `<Dashboard/>`. 지표: 실행중(비종단)·대기(Pending)·성공(Succeeded)·실패(Failed).

- [ ] **Step 1: 실패 테스트 — `Dashboard.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { Dashboard } from "./Dashboard";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("aggregates request metrics and lists nodes", async () => {
  server.use(
    http.get("/api/user/requests", () => HttpResponse.json([
      { request_id: "r1", operation: "sync", state: "Executing", priority: "mid",
        created_at: "", updated_at: "", requester_id: "a", resource_key: "k", payload: {} },
      { request_id: "r2", operation: "sync", state: "Succeeded", priority: "mid",
        created_at: "", updated_at: "", requester_id: "a", resource_key: "k", payload: {} },
    ])),
    http.get("/api/admin/nodes", () => HttpResponse.json([
      { node_name: "w1", reported_at: "2026-08-04T00:00:00Z", fresh: true, report: {} },
    ])),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><Dashboard /></MemoryRouter></QueryClientProvider>);
  expect(await screen.findByText("w1")).toBeInTheDocument();
  // 실행 중 타일 값 1
  const running = screen.getByText("실행 중").closest("div")!;
  expect(running).toHaveTextContent("1");
});
```

- [ ] **Step 2: 실패 확인** — Run: `cd frontend && npx vitest run src/features/dashboard/Dashboard.test.tsx` → FAIL.

- [ ] **Step 3: 구현 — `useDashboard.ts`, `Dashboard.tsx`**

```ts
// useDashboard.ts
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { Node } from "../../lib/types";
export const useNodes = () =>
  useQuery({ queryKey: ["nodes"], queryFn: () => apiGet<Node[]>("/api/admin/nodes"),
            refetchInterval: 5000 });
```

```tsx
// Dashboard.tsx
import { useRequests } from "../jobs/useJobs";
import { useNodes } from "./useDashboard";
import { isTerminal } from "../../lib/jobState";
import { MetricTile } from "../../components/ui/MetricTile";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";

export function Dashboard() {
  const reqs = useRequests(); const nodes = useNodes();
  const rs = reqs.data ?? [];
  const running = rs.filter((r) => !isTerminal(r.state)).length;
  const pending = rs.filter((r) => r.state === "Pending").length;
  const ok = rs.filter((r) => r.state === "Succeeded").length;
  const failed = rs.filter((r) => r.state === "Failed").length;
  return (
    <section className="space-y-5">
      <h1 className="text-lg font-semibold">대시보드</h1>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricTile label="실행 중" value={running} />
        <MetricTile label="대기" value={pending} />
        <MetricTile label="성공" value={ok} />
        <MetricTile label="실패" value={failed} />
      </div>
      <div className="grid md:grid-cols-2 gap-4">
        <Card>
          <h2 className="font-medium mb-3">노드 상태</h2>
          <ul className="space-y-2 text-sm">
            {(nodes.data ?? []).map((n) => (
              <li key={n.node_name} className="flex items-center justify-between">
                <span>{n.node_name}</span>
                <StatusPill state={n.fresh ? "Succeeded" : "Failed"} />
              </li>
            ))}
          </ul>
        </Card>
        <Card>
          <h2 className="font-medium mb-3">최근 작업</h2>
          <ul className="space-y-2 text-sm">
            {rs.slice(0, 6).map((r) => (
              <li key={r.request_id} className="flex items-center justify-between">
                <span>{r.request_id} · {r.operation}</span><StatusPill state={r.state} />
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </section>
  );
}
```

- [ ] **Step 4: 통과 확인 + 커밋**

Run: `cd frontend && npm test` → PASS
```bash
git add frontend/src/features/dashboard/
git commit -m "feat(portal): admin operations dashboard"
```

---

## Task 12: 프로덕션 서빙 — 멀티스테이지 Dockerfile

`Dockerfile.dms`를 멀티스테이지로 바꿔 `vite build` 산출물을 이미지에 넣고, dms-api가
`DMS_STATIC_DIR`로 서빙하게 한다.

**Files:**
- Modify: `deploy/docker/Dockerfile.dms`
- Modify: `deploy/k8s/40-api.yaml`(env `DMS_STATIC_DIR` 추가) — 파일 존재 시. 없으면 20-config에 키 추가.

**Interfaces:**
- Consumes: Task 1의 `DMS_STATIC_DIR` 서빙, Task 2~11의 `frontend/` 빌드.
- Produces: 이미지 `/app/static/`에 SPA, `DMS_STATIC_DIR=/app/static`.

- [ ] **Step 1: `Dockerfile.dms` 상단에 web 빌드 스테이지 추가**

기존 `FROM python:3.11-slim-bookworm` **앞에** 추가:

```dockerfile
# --- web build stage ---
FROM node:20-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* /web/
RUN npm ci || npm install
COPY frontend/ /web/
RUN npm run build   # -> /web/dist
```

- [ ] **Step 2: 런타임 스테이지에 dist COPY + env 기본값**

`COPY src /app/src` 뒤에 추가:

```dockerfile
COPY --from=web /web/dist /app/static
ENV DMS_STATIC_DIR=/app/static
```

- [ ] **Step 3: k8s api Deployment에 env 노출**(파일 있으면)

`deploy/k8s/40-api.yaml`의 api 컨테이너 env에 `- name: DMS_STATIC_DIR` `value: /app/static` 추가.
(이미지 ENV로도 충분하지만 명시.)

- [ ] **Step 4: 빌드 검증** (테스트베드 pkg-01의 podman)

Run(컨텍스트=repo 루트): `podman build -f deploy/docker/Dockerfile.dms -t dms:portal-slice1 .`
Expected: web 스테이지 `npm run build` 성공, 최종 이미지에 `/app/static/index.html` 존재
(`podman run --rm dms:portal-slice1 sh -c 'ls /app/static/index.html'`).

> 이 단계는 로컬 도커/포드만이 없으면 테스트베드에서 수행한다(메모리 [[dms-testbed]] 참조).
> 단위 테스트로 대체 불가한 통합 검증이므로, 실패 시 `npm ci` 락파일/노드 버전부터 확인.

- [ ] **Step 5: 커밋**

```bash
git add deploy/docker/Dockerfile.dms deploy/k8s/40-api.yaml
git commit -m "build(deploy): multi-stage image bundles portal SPA served by dms-api"
```

---

## Self-Review (작성자 체크)

**1. Spec coverage**
- §1 화면 지도 로그인/셸/내작업/제출/스토리지/대시보드/배치자리 → Task 5/6/7/8·9/10/11/6(배치 disabled). ✅
- §2 아키텍처(frontend/·정적서빙·멀티스테이지·프록시) → Task 2/1/12. ✅
- §3 인증/라우팅/전역401 → Task 5/6. ✅
- §4 preview→confirm → Task 9. ✅
- §5 디자인 토큰/StatusPill/반응형 → Task 2/3, 셸 `md:` 반응형 Task 6. ✅
- §6 API클라이언트/reason_code/폴링 → Task 4/7/9. ✅
- §7 테스트(프론트 6종 + 백엔드 SPA fallback) → 각 Task 테스트 + Task 1. ✅
- §8 배치 로드맵 → 코드 아님(내비 자리만, Task 6). ✅

**2. Placeholder scan:** 모든 코드 단계에 실제 코드 포함. "적절히 처리" 류 없음. Task 6의 화면
import 순서 주의는 스텁 지침으로 구체화. ✅

**3. Type consistency:** `Me/RequestRow/RequestDetail/DataJob/Storage/Node`(Task 4 types.ts)를
전 Task가 그대로 사용. `apiGet/apiSend/ApiError`(Task 4), `useRequests/useRequestJobs`(Task 7),
`useConfirmJob(requestId)`·`useCancelJob(requestId)`(Task 9) 시그니처 일치. `pillVariant/isTerminal`
(Task 3) 재사용. `DMS_STATIC_DIR`/`static_dir`(Task 1)→Task 12 일치. ✅

## 실행 순서 주의
Task 6(router)의 통합 테스트는 Task 7~11 화면을 import한다. **권장 실행 순서**: 1 → 2 → 3 → 4 →
5 → (7 → 8 → 9 → 10 → 11) → 6 → 12. 즉 화면들을 먼저 구현하고 마지막에 셸/라우터로 묶는다.
(subagent-driven이면 6의 스텁을 먼저 두는 대신 이 순서를 따르는 편이 깔끔하다.)
