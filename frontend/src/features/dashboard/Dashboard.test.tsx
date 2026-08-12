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
      verdict: "applied", detail: null,
      manifest_image: "pkg-01:5000/dms-agent:dev6" },        // live 와 일치
    { component: "dms-api", kind: "Deployment", workload: "dms-api",
      image: null, ready: null, desired: null, verdict: null, detail: null,
      manifest_image: null },                                 // 동봉 없음
  ],
  job_image: { live: null, manifest: null },
};

function renderDash(overrides: Record<string, unknown> = {}) {
  server.use(
    http.get("/api/admin/metrics/jobs",
             () => HttpResponse.json(overrides.jobs ?? JOB_METRICS)),
    http.get("/api/admin/metrics/infra",
             () => HttpResponse.json(overrides.infra ?? INFRA)),
    http.get("/api/admin/metrics/queue",
             () => HttpResponse.json(overrides.queue ??
               { queue: { name: "dms-data", state: "Open" }, podgroups: [] })),
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

test("잡 통계가 비배열 truthy({})로 와도 죽지 않는다 -- null 사례는 ?? [] 도 통과시킨다", async () => {
  // 기존 by_state:null 테스트는 `x ?? []` 구현도 초록으로 만들었다(null 은 nullish).
  // {} 는 Array.isArray 가드만 걸러낸다 -- 프록시/구버전 API 가 객체를 흘리는
  // 경우의 실 구분 사례다.
  renderDash({ jobs: { by_state: {} } });
  // KPI 타일은 로딩 첫 렌더에도 0 으로 그려져 즉시 단언은 데이터 착지 전에
  // 초록으로 끝난다 -- 로딩 문구 3곳(Queue/NodeMetrics/JobStats)의 소거를
  // 기다려 관찰 창을 착지 뒤로 민다. 크래시하면 트리째 언마운트라 타일도 사라진다.
  await waitFor(() => expect(screen.queryAllByText("불러오는 중…")).toHaveLength(0));
  const running = screen.getByText("실행 중").parentElement!;
  expect(running).toHaveTextContent("0");
});

const DRIFTED = {
  components: [
    { component: "dms-agent", kind: "DaemonSet", workload: "dms-agent",
      image: "pkg-01:5000/dms-agent:dev7", ready: 5, desired: 5,
      verdict: "applied", detail: null,
      manifest_image: "pkg-01:5000/dms-agent:dev6" },
    // 라이브는 있는데 동봉 매니페스트만 null -- 여기서 비교하면 "추측"이다(설계 §4).
    // 이 행이 없으면 무가드 구현(image !== manifest_image)도 통과해버린다.
    { component: "dms-api", kind: "Deployment", workload: "dms-api",
      image: "pkg-01:5000/dms:d23", ready: 2, desired: 2,
      verdict: "applied", detail: null, manifest_image: null },
  ],
  job_image: { live: "pkg-01:5000/dms-mpifileutils:job4",
               manifest: "pkg-01:5000/dms-mpifileutils:job5" },
};

test("라이브가 동봉 매니페스트와 다르면 드리프트 배지와 되돌림 경고를 낸다", async () => {
  renderDash({ infra: DRIFTED });
  expect(await screen.findByText("드리프트")).toBeInTheDocument();
  // 매니페스트 null 인 dms-api 는 라이브가 있어도 무배지 -- 배지는 정확히 1개다
  expect(screen.getAllByText("드리프트")).toHaveLength(1);
  expect(screen.getByText(
    "매니페스트 pkg-01:5000/dms-agent:dev6 — 다음 kubectl apply가 이 태그로 되돌립니다",
  )).toBeInTheDocument();
  expect(screen.getByText(
    "잡 이미지 pkg-01:5000/dms-mpifileutils:job4 · 매니페스트 pkg-01:5000/dms-mpifileutils:job5 — 다음 kubectl apply가 매니페스트 값으로 되돌립니다",
  )).toBeInTheDocument();
});

test("일치하거나 매니페스트가 null이면 아무 배지도 내지 않는다", async () => {
  // 기본 INFRA: dms-agent 일치 + dms-api null + job_image null -- 전부 무배지(설계 §3/§4)
  renderDash();
  expect(await screen.findByText("dms-agent")).toBeInTheDocument();
  expect(screen.queryByText("드리프트")).toBeNull();
  expect(screen.queryByText(/되돌립니다/)).toBeNull();
});

test("큐 현황 카드가 잡 통계 앞에 뜬다", async () => {
  renderDash();
  const queueCard = await screen.findByText("큐 현황");
  const jobStats = await screen.findByText("잡 통계");
  // DOM 순서 단언(설계 §3: 「잡 통계」 앞 자립형 카드)
  expect(queueCard.compareDocumentPosition(jobStats)
         & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});
