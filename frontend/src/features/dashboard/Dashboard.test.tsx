import { render, screen, waitFor } from "@testing-library/react";
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

const JOB_METRICS = {
  window_hours: 24, bucket: "hour",
  by_state: [
    { state: "Executing", count: 3 }, { state: "Pending", count: 2 },
    { state: "Succeeded", count: 20 }, { state: "Failed", count: 4 },
    { state: "TimedOut", count: 1 },
  ],
  by_tool: [], by_storage: [], by_requester: [], failure_reasons: [],
  throughput: [], duration_histogram: [], files_total: null, bytes_total: null,
};

const INFRA = {
  components: [
    { component: "dms-agent", kind: "DaemonSet", workload: "dms-agent",
      image: "pkg-01:5000/dms-agent:dev6", ready: 5, desired: 5,
      verdict: "applied", detail: null },
    { component: "dms-api", kind: "Deployment", workload: "dms-api",
      image: null, ready: null, desired: null, verdict: null, detail: null },
  ],
};

function renderDash(overrides: Record<string, unknown> = {}) {
  server.use(
    http.get("/api/admin/metrics/jobs",
             () => HttpResponse.json(overrides.jobs ?? JOB_METRICS)),
    http.get("/api/admin/metrics/infra", () => HttpResponse.json(INFRA)),
    http.get("/api/admin/metrics/nodes",
             () => HttpResponse.json({ window_hours: 24, start: "", end: "", nodes: [] })),
    http.get("/api/user/requests", () => HttpResponse.json([
      { request_id: "r1", operation: "sync", state: "Executing", priority: "mid",
        created_at: "", updated_at: "", requester_id: "a", resource_key: "k",
        payload: {} }])),
    http.get("/api/admin/nodes", () => HttpResponse.json([
      { node_name: "w1", reported_at: "2026-08-09T00:00:00Z", fresh: true,
        report: {} }])),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><Dashboard /></MemoryRouter>
    </QueryClientProvider>);
}

test("KPI 타일은 요청 목록 즉석 계산이 아니라 잡 통계 집계를 쓴다", async () => {
  // 옛 스텁은 페이지네이션 상한(50건)에 걸려 총계가 거짓이 됐다(설계 §4.1)
  renderDash();
  const running = (await screen.findByText("실행 중")).parentElement!;
  await waitFor(() => expect(running).toHaveTextContent("3"));
  expect(screen.getByText("대기").parentElement).toHaveTextContent("2");
  expect(screen.getByText("성공(24h)").parentElement).toHaveTextContent("20");
  expect(screen.getByText("실패(24h)").parentElement).toHaveTextContent("5"); // Failed+TimedOut
});

test("컴포넌트 카드가 이미지·ready·판정을 보여주고 null은 —", async () => {
  renderDash();
  expect(await screen.findByText("dms-agent")).toBeInTheDocument();
  expect(screen.getByText("pkg-01:5000/dms-agent:dev6")).toBeInTheDocument();
  expect(screen.getByText("5/5")).toBeInTheDocument();
  expect(screen.getByText("applied")).toBeInTheDocument();
  expect(screen.getByText("—/—")).toBeInTheDocument();   // observe 강등된 dms-api
});

test("잡 통계가 비배열로 와도 죽지 않는다", async () => {
  renderDash({ jobs: { by_state: null } });
  const running = (await screen.findByText("실행 중")).parentElement!;
  expect(running).toHaveTextContent("0");
});
