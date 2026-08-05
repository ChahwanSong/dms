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

test("admin can open batches list", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/batches", () => HttpResponse.json([])),
  );
  renderAt("/admin/batches");
  expect(await screen.findByRole("heading", { name: "배치 작업" })).toBeInTheDocument();
});

test("admin can open audit log", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/audit-log", () => HttpResponse.json([])),
  );
  renderAt("/admin/audit");
  expect(await screen.findByRole("heading", { name: "감사 로그" })).toBeInTheDocument();
});

test("admin can open policies list", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/policies", () => HttpResponse.json([])),
  );
  renderAt("/admin/policies");
  expect(await screen.findByRole("heading", { name: "정책" })).toBeInTheDocument();
});

test("admin can open denylist", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/identity-denylist", () => HttpResponse.json([])),
  );
  renderAt("/admin/denylist");
  expect(await screen.findByRole("heading", { name: "denylist" })).toBeInTheDocument();
});

test("admin can open control state", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/control-state", () =>
      HttpResponse.json({ maintenance: 0, drain: 0, reason: null, changed_by: null, changed_at: null })),
  );
  renderAt("/admin/control");
  expect(await screen.findByRole("heading", { name: "컨트롤 상태" })).toBeInTheDocument();
});

test("admin can open scan screen", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/user/storages", () => HttpResponse.json([])),
  );
  renderAt("/admin/scan");
  expect(await screen.findByRole("heading", { name: "scan 실행" })).toBeInTheDocument();
});

test("non-admin user can open my scan paths (not admin-gated)", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "alice", role: "user" })),
    http.get("/api/user/scan-paths", () => HttpResponse.json([])),
    http.get("/api/user/storages", () => HttpResponse.json([])),
  );
  renderAt("/scan-paths");
  expect(await screen.findByRole("heading", { name: "내 스캔 경로" })).toBeInTheDocument();
});

test("admin can open accounts list", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/accounts", () => HttpResponse.json([])),
  );
  renderAt("/admin/accounts");
  expect(await screen.findByRole("heading", { name: "계정" })).toBeInTheDocument();
});

test("admin can open nodes dashboard", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/nodes", () => HttpResponse.json([])),
  );
  renderAt("/admin/nodes");
  expect(await screen.findByRole("heading", { name: "노드" })).toBeInTheDocument();
});

test("user visiting /admin/policies is redirected to /jobs", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "alice", role: "user" })),
    http.get("/api/user/requests", () => HttpResponse.json([])),
  );
  renderAt("/admin/policies");
  expect(await screen.findByRole("heading", { name: "내 작업" })).toBeInTheDocument();
});
