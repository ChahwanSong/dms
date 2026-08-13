import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { Dashboard } from "./Dashboard";
import type { RequestRow } from "../../lib/types";

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
  plan_rejected: 7, plan_rejection_reasons: [],
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

// 최근 작업 픽스처 빌더 -- 표가 그리는 필드(요청자·payload·시각)를 전부 싣는다.
function reqRow(id: string, over: Partial<RequestRow> = {}): RequestRow {
  return {
    request_id: id, operation: "scan", state: "Pending", priority: "mid",
    requester_id: "alice", resource_key: "k",
    created_at: "2026-08-13T00:00:00Z", updated_at: "2026-08-13T00:30:00Z",
    payload: { storage: "ceph-a", target: "team/data" },
    ...over,
  };
}

// limit=200 전송 단언용 -- msw 는 쿼리스트링과 무관하게 경로를 매칭하므로,
// 실제로 어떤 URL 이 나갔는지는 핸들러가 직접 기록해야 보인다.
let requestsUrls: string[] = [];

function renderDash(overrides: Record<string, unknown> = {}) {
  requestsUrls = [];
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
    http.get("/api/user/requests", ({ request }) => {
      requestsUrls.push(request.url);
      return HttpResponse.json(overrides.requests ?? [
        { request_id: "r1", operation: "sync", state: "Executing", priority: "mid",
          created_at: "", updated_at: "", requester_id: "a", resource_key: "k",
          payload: {} }]);
    }),
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

test("계획 거부 타일은 results 집계(잡 통계 밖 구간)를 보여준다", async () => {
  // 계획 거부는 data_jobs 가 생기기 전의 종단이라 by_state 합산 어디에도 없다 --
  // 별도 필드(plan_rejected)가 타일의 유일한 원천이다.
  renderDash();
  const tile = (await screen.findByText("계획 거부(24h)")).parentElement!;
  await waitFor(() => expect(tile).toHaveTextContent("7"));
});

test("계획 거부 0건은 0 으로 표기한다(null≠0)", async () => {
  renderDash({ jobs: { ...JOB_METRICS, plan_rejected: 0 } });
  await waitFor(() => expect(screen.queryAllByText("불러오는 중…")).toHaveLength(0));
  const tile = screen.getByText("계획 거부(24h)").parentElement!;
  expect(tile).toHaveTextContent("0");
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

// ---- 최근 작업 카드 개편(2026-08-13): 요청자·작업내용·시각 + 검색 + 페이지네이션 ----

test("최근 작업 표가 요청자·작업내용·요청시간을 보여준다", async () => {
  renderDash({ requests: [
    reqRow("req-sync", {
      operation: "sync", requester_id: "alice", state: "Executing",
      created_at: "2026-08-12T09:00:00Z",
      payload: { source_storage: "ceph-a", source: "team/a",
                 destination_storage: "weka-b", destination: "team/b" } }),
    reqRow("req-scan", {
      operation: "scan", requester_id: "bob",
      created_at: "2026-08-12T08:00:00Z" }),
  ] });
  expect(await screen.findByText("req-sync")).toBeInTheDocument();
  expect(screen.getByText("alice")).toBeInTheDocument();
  // 작업내용 요약: sync 는 source → destination, scan/rm 은 storage:target.
  // 한 개의 템플릿 리터럴 = 한 개의 텍스트 노드(컴포넌트 카드와 같은 규율).
  expect(screen.getByText("sync · ceph-a:team/a → weka-b:team/b")).toBeInTheDocument();
  expect(screen.getByText("scan · ceph-a:team/data")).toBeInTheDocument();
  expect(screen.getByText("2026-08-12T09:00:00Z")).toBeInTheDocument();
});

test("완료시간은 종단 상태(Conflict 포함)에서만 updated_at, 비종단은 —", async () => {
  // updated_at 은 "마지막 전이 시각"일 뿐이라 비종단에서 완료시간으로 보여주면
  // 거짓말이다 -- 시각 문자열 자체의 존재/부재로 단언한다("—"는 화면 곳곳에 있다).
  renderDash({ requests: [
    reqRow("req-done", { state: "Succeeded", updated_at: "2026-08-10T11:11:11Z" }),
    reqRow("req-conflict", { state: "Conflict", updated_at: "2026-08-10T22:22:22Z" }),
    reqRow("req-live", { state: "Executing", updated_at: "2026-08-10T20:20:20Z" }),
  ] });
  expect(await screen.findByText("2026-08-10T11:11:11Z")).toBeInTheDocument();
  // Conflict 는 잡 종단 셋(TERMINAL_STATES)엔 없지만 요청 종단이다 --
  // REQUEST_TERMINAL_STATES 가 필요한 이유 그 자체.
  expect(screen.getByText("2026-08-10T22:22:22Z")).toBeInTheDocument();
  expect(screen.queryByText("2026-08-10T20:20:20Z")).toBeNull();
});

test("페이지네이션: 20건/페이지, 경계에서 버튼 비활성", async () => {
  const rows = Array.from({ length: 25 }, (_, i) =>
    reqRow(`req-${String(i + 1).padStart(2, "0")}`));
  renderDash({ requests: rows });
  expect(await screen.findByText("req-01")).toBeInTheDocument();
  expect(screen.getByText("req-20")).toBeInTheDocument();
  expect(screen.queryByText("req-21")).toBeNull();     // 21건째는 2페이지
  expect(screen.getByText("1 / 2 페이지")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "이전" })).toBeDisabled();

  await userEvent.click(screen.getByRole("button", { name: "다음" }));
  expect(screen.getByText("req-21")).toBeInTheDocument();
  expect(screen.getByText("req-25")).toBeInTheDocument();
  expect(screen.queryByText("req-01")).toBeNull();
  expect(screen.getByText("2 / 2 페이지")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
});

test("검색이 필터링하고 페이지를 1로 리셋한다", async () => {
  const rows = Array.from({ length: 25 }, (_, i) =>
    reqRow(`req-${String(i + 1).padStart(2, "0")}`,
           { requester_id: i % 2 === 0 ? "alice" : "bob" }));
  renderDash({ requests: rows });
  expect(await screen.findByText("req-01")).toBeInTheDocument();
  // 2페이지로 간 뒤 검색 -- 결과가 1페이지로 리셋되지 않으면 빈 화면이 된다.
  await userEvent.click(screen.getByRole("button", { name: "다음" }));
  expect(screen.getByText("req-21")).toBeInTheDocument();
  const input = screen.getByPlaceholderText("요청자·ID·작업·상태 검색");
  await userEvent.type(input, "req-03");
  expect(screen.getByText("req-03")).toBeInTheDocument();
  expect(screen.queryByText("req-21")).toBeNull();
  expect(screen.getByText("1 / 1 페이지")).toBeInTheDocument();
  // 요청자 검색: 대소문자 무시 부분일치.
  await userEvent.clear(input);
  await userEvent.type(input, "BOB");
  expect(screen.getByText("req-02")).toBeInTheDocument();
  expect(screen.queryByText("req-03")).toBeNull();     // alice 행은 사라진다
});

test("최근 작업 카드는 잡 통계 뒤(문서 맨 아래)에 온다", async () => {
  renderDash();
  const jobStats = await screen.findByText("잡 통계");
  const recent = await screen.findByText("최근 작업");
  // DOM 순서 단언 -- 큐 현황/잡 통계 테스트와 같은 방식.
  expect(jobStats.compareDocumentPosition(recent)
         & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("최근 작업은 limit=200 으로 요청한다", async () => {
  renderDash();
  await screen.findByText("최근 작업");
  await waitFor(() => expect(requestsUrls.length).toBeGreaterThan(0));
  expect(requestsUrls.some(
    (u) => new URL(u).searchParams.get("limit") === "200")).toBe(true);
});
