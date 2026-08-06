import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect, vi } from "vitest";
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

test("admin can open builds screen", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/control-state", () =>
      HttpResponse.json({ maintenance: 0, drain: 0, reason: null, build_node_name: "dms-w1",
                          changed_by: null, changed_at: null })),
    http.get("/api/admin/builds", () => HttpResponse.json([])),
  );
  renderAt("/admin/builds");
  expect(await screen.findByRole("heading", { name: "빌드" })).toBeInTheDocument();
});

test("admin can open build detail", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/builds/b1", () =>
      HttpResponse.json({ build_id: "b1", repo_url: "u", git_ref: "main", commit_sha: "deadbeef",
                          images: ["dms"], node_name: "dms-w1", state: "Succeeded", reason_code: null,
                          tag: "b01234567", created_at: "2026-08-06T00:00:00Z",
                          finished_at: "2026-08-06T00:10:00Z" })),
    http.get("/api/admin/builds/b1/log", () => HttpResponse.json({ build_id: "b1", log: "ok\n" })),
  );
  renderAt("/admin/builds/b1");
  expect(await screen.findByRole("heading", { name: "빌드 b1" })).toBeInTheDocument();
});

test("user visiting /admin/policies is redirected to /jobs", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "alice", role: "user" })),
    http.get("/api/user/requests", () => HttpResponse.json([])),
  );
  renderAt("/admin/policies");
  expect(await screen.findByRole("heading", { name: "내 작업" })).toBeInTheDocument();
});

// /admin/nodes는 NodesList가 이미 `q.data ?? []`로 null 페이로드를 방어해서 죽지
// 않는다(슬라이스 9의 교훈이 이미 반영돼 있다). 실제로 렌더를 죽이는 조합은 요청
// 상세다 -- RequestDetail은 `data.transitions`가 배열이라고 가정하고 방어 없이
// `transitions[transitions.length - 1]`에 접근한다(null이면 TypeError).
test("기능 화면이 크래시해도 사이드바가 살아 있다", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "alice", role: "user" })),
    http.get("/api/user/requests/r1", () => HttpResponse.json({
      request_id: "r1", operation: "sync", requester_id: "alice", state: "Failed",
      created_at: "2026-08-05T00:00:00Z", updated_at: "2026-08-05T00:01:30Z",
      transitions: null,
    })),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json([])),
  );
  renderAt("/jobs/r1");
  expect(await screen.findByText("화면을 표시하지 못했습니다")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "내 작업" })).toBeInTheDocument();
});

// 이 테스트가 이 태스크의 요점이다 -- AppShell이 key 없이 ErrorBoundary를 마운트하면
// 모든 보호 라우트가 같은 컴포넌트 타입・같은 트리 위치라 한 번 에러 상태에 빠진 뒤
// 다른 화면으로 이동해도 React가 같은 인스턴스를 재사용해서 영원히 갇힌다.
// key={pathname}이 있어야 경로가 바뀔 때 경계가 스스로 초기화된다.
test("다른 경로로 이동하면 경계가 스스로 풀린다", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "alice", role: "user" })),
    http.get("/api/user/requests/r1", () => HttpResponse.json({
      request_id: "r1", operation: "sync", requester_id: "alice", state: "Failed",
      created_at: "2026-08-05T00:00:00Z", updated_at: "2026-08-05T00:01:30Z",
      transitions: null,
    })),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json([])),
    http.get("/api/user/requests", () => HttpResponse.json([])),
  );
  renderAt("/jobs/r1");
  expect(await screen.findByText("화면을 표시하지 못했습니다")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("link", { name: "내 작업" }));

  expect(await screen.findByRole("heading", { name: "내 작업" })).toBeInTheDocument();
  expect(screen.queryByText("화면을 표시하지 못했습니다")).not.toBeInTheDocument();
});
