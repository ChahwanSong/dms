import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { NodeMetricsSection } from "./NodeMetricsSection";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const METRICS = {
  window_hours: 24, start: "2026-08-09T00:00:00Z", end: "2026-08-10T00:00:00Z",
  nodes: [{
    node_name: "w1", reported_at: "2026-08-10T00:00:00Z", fresh: true,
    points: [
      { at: "2026-08-09T23:58:00Z", load1: 0.5, load5: 0.4, load15: 0.3,
        mem_used_pct: 50, net_rx_bps: null, net_tx_bps: null,
        disks: [{ storage_name: "s1", used_pct: 40 }] },
      { at: "2026-08-09T23:59:00Z", load1: 0.7, load5: 0.4, load15: 0.3,
        mem_used_pct: 55, net_rx_bps: 100, net_tx_bps: 10,
        disks: [{ storage_name: "s1", used_pct: 41 }] },
    ],
  }],
};

const NODES = [{
  node_name: "w1", reported_at: "2026-08-10T00:00:00Z", fresh: true,
  report: {
    mounts: [{ storage_name: "s1", mount_path: "/s1", status: "Ready" }],
    tools: [{ name: "dsync", status: "Ready" }, { name: "drm", status: "Missing" }],
    identities: [],
  },
}];

function renderSection(calls?: (string | null)[]) {
  server.use(
    http.get("/api/admin/metrics/nodes", ({ request }) => {
      calls?.push(new URL(request.url).searchParams.get("window"));
      return HttpResponse.json(METRICS);
    }),
    http.get("/api/admin/nodes", () => HttpResponse.json(NODES)),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><NodeMetricsSection /></QueryClientProvider>);
}

test("노드별 스파크라인과 신선도를 그린다", async () => {
  const { container } = renderSection();
  expect(await screen.findByText("w1")).toBeInTheDocument();
  expect(screen.getByText(/정상/)).toBeInTheDocument();
  // 값이 있는 시리즈는 path가 실제로 그려진다
  await waitFor(() =>
    expect(container.querySelectorAll("svg path").length).toBeGreaterThan(0));
});

test("기간 버튼이 window 파라미터로 재조회한다", async () => {
  const calls: (string | null)[] = [];
  renderSection(calls);
  await screen.findByText("w1");
  expect(calls[0]).toBe("24");           // 기본 24h
  await userEvent.click(screen.getByRole("button", { name: "1h" }));
  await waitFor(() => expect(calls).toContain("1"));
});

test("드릴다운에 스토리지별 디스크와 증거 스냅샷이 나온다", async () => {
  renderSection();
  await userEvent.click(await screen.findByRole("button", { name: "w1" }));
  expect(await screen.findByText("s1")).toBeInTheDocument();
  expect(screen.getByText(/마운트 1\/1/)).toBeInTheDocument();
  expect(screen.getByText(/도구 1\/2/)).toBeInTheDocument();
});

test("nodes가 비배열이어도 죽지 않는다", async () => {
  server.use(
    http.get("/api/admin/metrics/nodes",
             () => HttpResponse.json({ nodes: null })),
    http.get("/api/admin/nodes", () => HttpResponse.json(NODES)),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><NodeMetricsSection /></QueryClientProvider>);
  expect(await screen.findByText("노드/리소스")).toBeInTheDocument();
});
