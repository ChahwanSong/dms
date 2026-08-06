import { render, screen } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect, vi } from "vitest";
import { BuildDetail } from "./BuildDetail";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => { server.resetHandlers(); vi.useRealTimers(); });
afterAll(() => server.close());

const buildRow = (over: Record<string, unknown> = {}) => ({
  build_id: "b1", repo_url: "u", git_ref: "main", commit_sha: "deadbeefcafebabe",
  images: ["dms"], node_name: "dms-w1", state: "Succeeded", reason_code: null,
  tag: "b01234567", created_at: "2026-08-06T00:00:00Z", finished_at: "2026-08-06T00:10:00Z",
  ...over,
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/admin/builds/b1"]}>
        <Routes><Route path="/admin/builds/:buildId" element={<BuildDetail />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("로그가 렌더된다", async () => {
  server.use(
    http.get("/api/admin/builds/b1", () => HttpResponse.json(buildRow({ state: "Succeeded" }))),
    http.get("/api/admin/builds/b1/log", () => HttpResponse.json({ build_id: "b1", log: "hello build log\n" })),
  );
  renderPage();
  expect(await screen.findByText(/hello build log/)).toBeInTheDocument();
});

test("log가 null이면 흰 화면 대신 안내 문구를 보여준다", async () => {
  server.use(
    http.get("/api/admin/builds/b1", () => HttpResponse.json(buildRow({ state: "Running" }))),
    http.get("/api/admin/builds/b1/log", () => HttpResponse.json({ build_id: "b1", log: null })),
  );
  renderPage();
  expect(await screen.findByRole("heading", { name: "빌드 b1" })).toBeInTheDocument();
  expect(await screen.findByText("로그가 아직 없습니다")).toBeInTheDocument();
});

test("사유 코드를 한글 메시지로 보여주고 원시 코드는 노출하지 않는다", async () => {
  server.use(
    http.get("/api/admin/builds/b1", () => HttpResponse.json(buildRow({ state: "Failed", reason_code: "build_failed" }))),
    http.get("/api/admin/builds/b1/log", () => HttpResponse.json({ build_id: "b1", log: "boom\n" })),
  );
  renderPage();
  expect(await screen.findByText("빌드가 실패했습니다 — 로그를 확인하세요")).toBeInTheDocument();
  expect(screen.queryByText("build_failed")).not.toBeInTheDocument();
});

test("종단 빌드(Succeeded)는 로그를 더 폴링하지 않는다", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let logCalls = 0;
  server.use(
    http.get("/api/admin/builds/b1", () => HttpResponse.json(buildRow({ state: "Succeeded" }))),
    http.get("/api/admin/builds/b1/log", () => {
      logCalls += 1;
      return HttpResponse.json({ build_id: "b1", log: "done\n" });
    }),
  );
  renderPage();
  await screen.findByText(/done/);
  const callsAfterInitialLoad = logCalls;
  // 로그 폴링 주기(3000ms)의 3배를 넘겨도 추가 호출이 없어야 한다.
  await vi.advanceTimersByTimeAsync(9000);
  expect(logCalls).toBe(callsAfterInitialLoad);
});
