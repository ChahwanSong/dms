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
      // 마지막 점이 최대가 아니게(0.6 < 0.7, 52 < 55) -- "현재 · 최대" 병기가
      // 같은 값의 반복이 아니라 실제로 둘을 구분함을 단언할 수 있는 형상.
      { at: "2026-08-09T23:59:30Z", load1: 0.6, load5: 0.4, load15: 0.3,
        mem_used_pct: 52, net_rx_bps: 2048, net_tx_bps: 20,
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
    os: { cpu_count: 2 },
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

test("현재값 · 창 내 최대값을 병기한다", async () => {
  renderSection();
  await screen.findByText("w1");
  // 메모리: 마지막 점 52%, 창 최대 55% -- 현재와 최대가 구분된다
  expect(screen.getByText("52% · 최대 55%")).toBeInTheDocument();
  // load1: 마지막 0.6, 최대 0.7
  expect(screen.getByText("0.6 · 최대 0.7")).toBeInTheDocument();
  // 네트워크는 humanBytes 표기 + /s -- 수신 2048 B/s = 2.0 KiB/s
  expect(screen.getByText("2.0 KiB/s · 최대 2.0 KiB/s")).toBeInTheDocument();
  expect(screen.getByText("20 B/s · 최대 20 B/s")).toBeInTheDocument();
});

test("상한 라벨: 메모리는 100%, load1 은 리포트의 코어 수", async () => {
  renderSection();
  await screen.findByText("w1");
  expect(screen.getAllByText("상한 100%").length).toBeGreaterThan(0);
  expect(screen.getByText("코어 2")).toBeInTheDocument();
});

test("cpu_count 가 없거나 오염된 구형 리포트는 코어 라벨 없이 창 최대 폴백", async () => {
  // 스키마리스 리포트 방어: 문자열 "2" 같은 오염값을 숫자 상한으로 뭉개면
  // 거짓 기준선이 생긴다 -- null(모름)로 두고 라벨을 아예 내리지 않는다.
  server.use(
    http.get("/api/admin/metrics/nodes", () => HttpResponse.json(METRICS)),
    http.get("/api/admin/nodes", () => HttpResponse.json([
      { ...NODES[0], report: { ...NODES[0].report, os: { cpu_count: "2" } } },
    ])),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={qc}><NodeMetricsSection /></QueryClientProvider>);
  await screen.findByText("w1");
  expect(screen.queryByText(/코어/)).not.toBeInTheDocument();
  // 폴백에서도 load1 차트 자체는 그려진다(창 최대 스케일)
  await waitFor(() =>
    expect(container.querySelectorAll("svg path").length).toBeGreaterThan(0));
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
