import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { Dashboard, artifactBaseWarning } from "./Dashboard";

// 「최근 작업」 카드는 제거됐다(2026-08-23 사용자 결정 -- 전체 작업 화면과 중복).
// 그 카드의 표·검색·페이지네이션 테스트도 함께 걷어냈다.
const server = setupServer(
  http.get("/api/user/storages", () => HttpResponse.json([])));
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
  throughput: [], duration_histogram: [], sync_items_total: null, sync_bytes_total: null, rm_items_total: null,
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

test("실패(24h) 는 Failed·TimedOut 만 -- 거부·취소·만료는 더하지 않는다(잡 통계 분해의 「실패」 열과 같은 정의)", async () => {
  // d192: 서버 분해 표가 실패 = Failed+TimedOut, 거부·취소·만료를 따로 센다 -- KPI 가 종단−성공을 실패로 세면 두 숫자가 어긋난다.
  renderDash({ jobs: { ...JOB_METRICS, by_state: [...JOB_METRICS.by_state,
    { state: "Rejected", count: 6 }, { state: "Cancelled", count: 2 }, { state: "PreviewExpired", count: 3 }] } });
  const failed = (await screen.findByText("실패(24h)")).parentElement!;
  await waitFor(() => expect(screen.getByText("성공(24h)").parentElement).toHaveTextContent("20"));
  expect(failed).toHaveTextContent(/^실패\(24h\)5$/);
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

test("잡 이미지 소스가 db(릴리스 오버라이드)면 되돌림 대신 우선을 말한다", async () => {
  // 슬라이스 35: DB 오버라이드는 apply 로 되돌아가지 않는다 -- env 시절 문구를
  // 그대로 쓰면 거짓 경고가 된다.
  renderDash({ infra: { ...DRIFTED,
    job_image: { ...DRIFTED.job_image, source: "db" } } });
  expect(await screen.findByText(/릴리스 오버라이드가 우선이라/)).toBeInTheDocument();
  expect(screen.queryByText(/매니페스트 값으로 되돌립니다/)).not.toBeInTheDocument();
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

// ---- 아티팩트 경로 점검 경고 줄(2026-11-23 사고 -- API 파드가 공유 스토리지 미마운트 노드에 떠 작업 출력이 「없음」으로만 보였다) ----
const WITH_CTRL = {
  ...INFRA,
  components: [...INFRA.components,
    { component: "dms-controller", kind: "Deployment", workload: "dms-controller",
      image: "pkg-01:5000/dms:d200", ready: 1, desired: 1, verdict: "applied", detail: null, manifest_image: null }],
};

test("artifactBaseWarning: 실패만 경고 -- 모름(null)·확인 대기는 실패가 아니다, 다른 API 파드 실패 수는 따로", () => {
  const ok = { api: { ok: true, reason: null, node_name: "n1", instance: "a" }, api_pods_failing: 0,
               controller: { pending: false, ok: true, reason: null, node_name: "n2" } };
  expect(artifactBaseWarning("dms-api", ok)).toBeNull();
  expect(artifactBaseWarning("dms-controller", ok)).toBeNull();
  expect(artifactBaseWarning("dms-api", undefined)).toBeNull();                        // 옛 서버
  expect(artifactBaseWarning("dms-api", { ...ok, api: null })).toBeNull();              // 모름
  expect(artifactBaseWarning("dms-api", { ...ok, api: { ...ok.api, ok: null } })).toBeNull();
  expect(artifactBaseWarning("dms-controller",
    { ...ok, controller: { pending: true, ok: null, reason: null, node_name: null } })).toBeNull();
  expect(artifactBaseWarning("dms-api", { ...ok, api: { ...ok.api, ok: false, reason: null, node_name: null } }))
    .toBe("API 파드(노드 모름)가 아티팩트 경로를 읽지 못합니다 — 작업 출력·scan 리포트가 보이지 않을 수 있습니다");
  expect(artifactBaseWarning("dms-api", { ...ok, api_pods_failing: 2 }))
    .toBe("다른 API 파드 2개가 아티팩트 경로 점검에 실패했습니다 — 그 파드로 간 조회는 작업 출력이 보이지 않을 수 있습니다");
  // 시작 게이트 대기(서비스 밖) 파드뿐이면 「그 파드로 간 조회」가 아니라 「롤아웃이 멈춤」(dms-k8s e25a2dfb d40 실증 이식)
  expect(artifactBaseWarning("dms-api", { ...ok, api_pods_failing: 1, api_pods_failing_waiting: 1 }))
    .toBe("새 API 파드 1개가 아티팩트 경로 점검에 실패해 Ready 를 기다립니다(서비스 밖 — 옛 파드가 계속 서비스) — 롤아웃이 멈춰 "
      + "있으니 그 노드의 공유 스토리지 마운트를 확인하세요");
  // 서비스 중인 실패 파드가 하나라도 있으면 그 수로 조회 경고
  expect(artifactBaseWarning("dms-api", { ...ok, api_pods_failing: 3, api_pods_failing_waiting: 1 }))
    .toBe("다른 API 파드 2개가 아티팩트 경로 점검에 실패했습니다 — 그 파드로 간 조회는 작업 출력이 보이지 않을 수 있습니다");
  // 대기 수를 모르면(null = 읽기 실패) 이전 문구 그대로 -- 0 으로 뭉개 「롤아웃 멈춤」이라 하지 않는다
  expect(artifactBaseWarning("dms-api", { ...ok, api_pods_failing: 2, api_pods_failing_waiting: null }))
    .toBe("다른 API 파드 2개가 아티팩트 경로 점검에 실패했습니다 — 그 파드로 간 조회는 작업 출력이 보이지 않을 수 있습니다");
  expect(artifactBaseWarning("dms-agent", { ...ok, api: { ...ok.api, ok: false } })).toBeNull();
});

test("컴포넌트 카드: API·컨트롤러 점검 실패면 그 행 아래 경고 줄 + 아티팩트 경로 링크", async () => {
  renderDash({ infra: { ...WITH_CTRL, artifact_base: {
    api: { ok: false, reason: "artifact_base_jobs_missing", node_name: "s-ssc-dms-26001", instance: "dms-api-x" },
    api_pods_failing: 0,
    controller: { pending: false, ok: false, reason: "artifact_base_missing", node_name: "ion2110" } } } });
  const api = await screen.findByText(/^API 파드\(노드 s-ssc-dms-26001\)가 아티팩트 경로를 읽지 못합니다/);
  expect(api.closest("li")).toHaveTextContent("dms-api");
  expect(api).toHaveTextContent("최근 작업의 기록 디렉터리가 이 경로에 없습니다");
  expect(within(api).getByRole("link", { name: "점검 결과 보기" })).toHaveAttribute("href", "/admin/artifact-base");
  const ctrl = screen.getByText(/^컨트롤러\(노드 ion2110\)가 아티팩트 경로 점검에 실패했습니다 — 경로가 존재하지 않습니다/);
  expect(ctrl.closest("li")).toHaveTextContent("dms-controller");
});

test("컴포넌트 카드: 점검이 정상이거나 옛 서버(필드 없음)면 경고 줄이 없다", async () => {
  renderDash({ infra: { ...WITH_CTRL, artifact_base: {
    api: { ok: true, reason: null, node_name: "n1", instance: "a" }, api_pods_failing: 0,
    controller: { pending: false, ok: true, reason: null, node_name: "n2" } } } });
  await screen.findByText("dms-controller");
  expect(screen.queryByRole("link", { name: "점검 결과 보기" })).toBeNull();
});
