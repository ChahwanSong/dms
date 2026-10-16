import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse, delay } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { QueueSection, waitText, waitTone } from "./QueueSection";
import type { QueuePipeline, QueueWaitEntry } from "../../lib/types";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderQueue(body: Record<string, unknown>, delayMs = 0) {
  server.use(http.get("/api/admin/metrics/queue", async () => {
    if (delayMs) await delay(delayMs);
    return HttpResponse.json(body);
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><QueueSection /></MemoryRouter>
    </QueryClientProvider>);
}

const zero = { count: 0, oldest_seconds: null };
const PIPE: QueuePipeline = {
  planning: { count: 1, oldest_seconds: 2000 },          // 33분 -- 기계 단계 경고(≥30분)
  pickup: zero,
  preflight: { count: 1, oldest_seconds: 400 },           // 6분 40초 -- 주의(≥5분)
  volcano_wait: { count: 1, oldest_seconds: 12 },
  running: { count: 3, oldest_seconds: 5400, includes_volcano_wait: false },
  confirm: { count: 4, oldest_seconds: 7200, soonest_expiry: "2026-10-17T00:00:00Z",
             soonest_expiry_seconds: 3000 },               // 2시간 -- 확인 대기 문턱(4h) 아래라 무색
  batch: { count: 30, oldest_seconds: 900, batches: 1, preview_ready_batches: 1 },
};
const entry = (over: Partial<QueueWaitEntry>): QueueWaitEntry => ({
  stage: "planning", request_id: null, job_id: null, operation: "sync", requester: "alice",
  priority: "mid", batch_id: null, since: "2026-10-16T00:00:00Z", wait_seconds: 60, detail: {},
  ...over,
});
const WAITING: QueueWaitEntry[] = [
  entry({ stage: "confirm", request_id: "req-confirm-0000001", job_id: "j1", wait_seconds: 7200,
          detail: { expires_at: "2026-10-17T00:00:00Z", expires_in_seconds: 9000 } }),
  entry({ stage: "confirm", batch_id: "batch-0000000000aa", operation: "sync", requester: "ops",
          priority: null, wait_seconds: 5000,
          detail: { jobs: 12, batch_status: "PreviewReady", batch_name: "월간 이관",
                    expires_at: "2026-10-16T13:00:00Z", expires_in_seconds: 3000 } }),
  entry({ stage: "planning", request_id: "req-plan-00000000002", wait_seconds: 2000,
          detail: { note: "identity_propagating", message: "identity not ready on: n1" } }),
  entry({ stage: "batch", batch_id: "batch-0000000000bb", operation: "scan", priority: null,
          wait_seconds: 900,
          detail: { queued: 30, max_concurrency: 4, batch_status: "Running", batch_name: null } }),
  entry({ stage: "preflight", request_id: "req-pre-000000000003", job_id: "j3", wait_seconds: 400 }),
  entry({ stage: "volcano_wait", request_id: null, job_id: null, priority: "high", operation: null,
          requester: null, wait_seconds: 12,
          detail: { podgroup: "foreign-pg-uid", phase: "Pending", min_member: 3,
                    reason: "NotEnoughResources", message: "3/3 tasks in gang unschedulable" } }),
];
const FULL = {
  queue: { name: "dms-data", state: "Open" },
  podgroups: [
    { name: "foreign-pg-uid", phase: "Pending", min_member: 3, created_at: "2026-10-16T00:00:00Z",
      wait_seconds: 12 },
    { name: "dms-scan-execution-x-uid", phase: "Running", min_member: 1,
      created_at: "2026-10-16T00:00:00Z", wait_seconds: 600 },
  ],
  control: { drain: false, maintenance: false, reason: null },
  pipeline: PIPE, waiting: WAITING, waiting_truncated: [],
};

test("띠: 단계마다 건수와 가장 오래 기다린 시간, 문턱 넘은 칸에 색을 낸다", async () => {
  renderQueue(FULL);
  const planning = await screen.findByTestId("queue-stage-planning");
  expect(within(planning).getByText("계획 대기")).toBeInTheDocument();
  expect(within(planning).getByText("1")).toBeInTheDocument();
  expect(within(planning).getByText("가장 오래 33분 20초")).toBeInTheDocument();
  expect(planning.dataset.tone).toBe("bad");                         // ≥30분
  expect(screen.getByTestId("queue-stage-preflight").dataset.tone).toBe("attn");   // ≥5분
  expect(screen.getByTestId("queue-stage-volcano_wait").dataset.tone).toBe("none");
  // 0 건은 「대기 없음」 -- 숫자 0 을 낸다(읽었고 0 이었다)
  const pickup = screen.getByTestId("queue-stage-pickup");
  expect(within(pickup).getByText("0")).toBeInTheDocument();
  expect(within(pickup).getByText("대기 없음")).toBeInTheDocument();
  // 확인 대기는 사람이 누르는 단계라 문턱이 길다 -- 2시간은 아직 무색. 가장 빠른 만료를 함께 보인다
  const confirm = screen.getByTestId("queue-stage-confirm");
  expect(confirm.dataset.tone).toBe("none");
  expect(within(confirm).getByText("다음 만료 50분 후")).toBeInTheDocument();
  // 실행 중은 대기가 아니다 -- 「가장 긴」, 색 없음
  const running = screen.getByTestId("queue-stage-running");
  expect(within(running).getByText("가장 긴 1시간 30분")).toBeInTheDocument();
  expect(running.dataset.tone).toBe("none");
  expect(within(screen.getByTestId("queue-stage-batch")).getByText("확인 대기 배치 1")).toBeInTheDocument();
  // PodGroup phase 카운터는 띠 아래 한 줄로 남는다
  expect(screen.getByText("Volcano PodGroup — Pending 1 · Inqueue 0 · Running 1")).toBeInTheDocument();
});

test("waitTone: 확인 대기는 긴 문턱, 배치 대기는 적색 없음, 실행 중은 무색", () => {
  expect(waitTone("planning", 299)).toBe("none");
  expect(waitTone("planning", 300)).toBe("attn");
  expect(waitTone("planning", 1800)).toBe("bad");
  expect(waitTone("confirm", 3 * 3600)).toBe("none");
  expect(waitTone("confirm", 4 * 3600)).toBe("attn");
  expect(waitTone("confirm", 12 * 3600)).toBe("bad");
  expect(waitTone("batch", 10 * 3600)).toBe("attn");             // 큰 배치는 정상적으로 오래 돈다
  expect(waitTone("running", 10 * 3600)).toBe("none");
  expect(waitTone("planning", null)).toBe("none");                 // 모름은 색을 지어내지 않는다
});

test("대기 표: 요청 상세·배치 링크, 만료 카운트다운, 계획 유예 사유, PodGroup 사유", async () => {
  renderQueue(FULL);
  const link = await screen.findByRole("link", { name: "req-confirm-" });
  expect(link).toHaveAttribute("href", "/jobs/req-confirm-0000001");
  expect(screen.getByText("만료까지 2시간 30분")).toBeInTheDocument();
  // 배치 묶음: 요청이 아니라 배치로 링크, 자식 수·남은 시간(1시간 이하는 적색)
  const batchLink = screen.getByRole("link", { name: "배치 월간 이관" });
  expect(batchLink).toHaveAttribute("href", "/admin/batches/batch-0000000000aa");
  const groupNote = screen.getByText("배치 확인 대기 · 자식 12건 · 만료까지 50분");
  expect(groupNote).toHaveClass("text-bad");
  // 이름 없는 배치는 id 앞 12자
  expect(screen.getByRole("link", { name: "배치 batch-000000" })).toHaveAttribute(
    "href", "/admin/batches/batch-0000000000bb");
  expect(screen.getByText("대기 항목 30건 · 동시 실행 상한 4")).toBeInTheDocument();
  expect(screen.getAllByText("정책 기본")).toHaveLength(2);      // 배치의 우선순위 null = 정책 기본
  expect(screen.getByText("노드 신원 전파 대기")).toBeInTheDocument();
  expect(screen.getByText("identity not ready on: n1")).toBeInTheDocument();
  // 대조되지 않은 PodGroup -- 요청을 지어내지 않고 PodGroup 이름을 그대로
  expect(screen.getByText("foreign-pg-uid")).toBeInTheDocument();
  expect(screen.getByText("Pending · gang 3 · NotEnoughResources")).toBeInTheDocument();
  expect(screen.getByText("3/3 tasks in gang unschedulable")).toBeInTheDocument();
  // 서버 순서(오래 기다린 순) 그대로 그린다
  const firstCells = screen.getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[0].textContent);
  expect(firstCells).toEqual(["확인 대기", "확인 대기", "계획 대기", "배치 대기", "사전 점검", "Volcano 대기"]);
});

test("단계 필터: 칩이 그 단계 행만 남기고 aria-pressed 로 선택을 알린다", async () => {
  const user = userEvent.setup();
  renderQueue(FULL);
  const all = await screen.findByRole("button", { name: "전체 6" });
  expect(all).toHaveAttribute("aria-pressed", "true");
  const confirmChip = screen.getByRole("button", { name: "확인 대기 2" });
  await user.click(confirmChip);
  expect(confirmChip).toHaveAttribute("aria-pressed", "true");
  expect(all).toHaveAttribute("aria-pressed", "false");
  expect(screen.getAllByRole("row")).toHaveLength(1 + 2);
  expect(screen.queryByText("노드 신원 전파 대기")).toBeNull();
  // 읽었지만 0 건인 단계 -- 「없음」 문구(모름과 다르다)
  await user.click(screen.getByRole("button", { name: "잡 픽업 0" }));
  expect(screen.getByText("잡 픽업 단계에 대기 중인 항목이 없습니다.")).toBeInTheDocument();
  await user.click(all);
  expect(screen.getAllByRole("row")).toHaveLength(1 + 6);
});

test("드레인·유지보수가 켜지면 머리 배지를 내고, 드레인은 픽업·사전 점검 행의 사유가 된다", async () => {
  renderQueue({ ...FULL, control: { drain: true, maintenance: true, reason: "스토리지 점검" } });
  const drain = await screen.findByText("드레인 — 잡 진행 멈춤");
  expect(drain.parentElement).toHaveAttribute("title", "스토리지 점검");
  expect(screen.getByText("유지보수 — 신규 제출 차단")).toBeInTheDocument();
  expect(screen.getByText("드레인 중 — 잡이 진행하지 않습니다")).toBeInTheDocument();   // preflight 행
});

test("드레인·유지보수가 꺼져 있으면(또는 모르면) 배지를 내지 않는다", async () => {
  renderQueue({ ...FULL, control: null });
  expect(await screen.findByText("Open")).toBeInTheDocument();
  expect(screen.queryByText(/드레인/)).toBeNull();
  expect(screen.queryByText(/유지보수/)).toBeNull();
});

test("null(알 수 없음)은 빈 큐로 렌더되지 않는다", async () => {
  // 403/CRD 부재 + 새 키 부재(옛 응답 모양) -- []로 접으면 권한 누락이 "큐가 한가함"으로 보인다(설계 §4)
  renderQueue({ queue: null, podgroups: null });
  expect(await screen.findByText(/대기 정보를 알 수 없습니다/)).toBeInTheDocument();
  expect(screen.queryByText(/대기 중인 잡이 없습니다/)).toBeNull();
  expect(screen.queryByText("Open")).toBeNull();             // 상태 추측 금지(설계 §3)
  // 숫자 0 도 내지 않는다 -- 모르는 단계는 「—」·「알 수 없음」이다
  const planning = screen.getByTestId("queue-stage-planning");
  expect(within(planning).getByText("—")).toBeInTheDocument();
  expect(within(planning).getByText("알 수 없음")).toBeInTheDocument();
  expect(screen.queryByText(/Pending 0/)).toBeNull();
  expect(screen.queryByRole("group", { name: "단계 필터" })).toBeNull();
});

test("빈 배열(비었음)은 알 수 없음과 다르게 렌더된다", async () => {
  const empty = Object.fromEntries(Object.keys(PIPE).map((k) => [k, { ...zero }]));
  renderQueue({ queue: { name: "dms-data", state: "Closed" }, podgroups: [],
                control: { drain: false, maintenance: false, reason: null },
                pipeline: { ...empty, running: { ...zero, includes_volcano_wait: false },
                            confirm: { ...zero, soonest_expiry: null, soonest_expiry_seconds: null },
                            batch: { ...zero, batches: 0, preview_ready_batches: 0 } },
                waiting: [], waiting_truncated: [] });
  expect(await screen.findByText("Closed")).toBeInTheDocument();
  expect(screen.getByText(/대기 중인 잡이 없습니다/)).toBeInTheDocument();
  expect(screen.queryByText(/알 수 없/)).toBeNull();
  // 읽었고 0 이었다 -- 그래서 0 을 낼 자격이 있다(위 null 테스트의 정확한 반대)
  expect(screen.getByText("Volcano PodGroup — Pending 0 · Inqueue 0 · Running 0")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "전체 0" })).toBeInTheDocument();
});

test("podgroups 축만 null 이면 Volcano 대기만 알 수 없음이고 DMS 단계는 그대로 뜬다", async () => {
  // Role 누락은 podgroups 만 죽인다 -- 나머지 단계를 통째로 「알 수 없음」으로 강등하면 정보를 버린다.
  renderQueue({ ...FULL, podgroups: null,
                pipeline: { ...PIPE, volcano_wait: null, running: { ...PIPE.running!, includes_volcano_wait: true } },
                waiting: WAITING.filter((e) => e.stage !== "volcano_wait") });
  expect(await screen.findByText("Open")).toBeInTheDocument();
  expect(screen.getByText(/알 수 없는 단계: Volcano 대기/)).toHaveTextContent(/권한\(RBAC\)/);
  expect(screen.getByRole("button", { name: "Volcano 대기 —" })).toBeInTheDocument();
  expect(screen.getByText("노드 신원 전파 대기")).toBeInTheDocument();
  expect(within(screen.getByTestId("queue-stage-running")).getByText("Volcano 대기 포함")).toBeInTheDocument();
  expect(screen.queryByText(/Volcano PodGroup —/)).toBeNull();   // 모르는 카운터는 줄째 숨긴다
  expect(screen.queryByText(/대기 정보를 알 수 없습니다/)).toBeNull();
});

test("queue 축만 null 이면 배지만 빠지고 대기 표는 그대로 뜬다", async () => {
  // 축별 독립 fail-soft(라우트 docstring): ClusterRole 누락은 queue 만 죽인다.
  renderQueue({ ...FULL, queue: null });
  expect(await screen.findByText("foreign-pg-uid")).toBeInTheDocument();
  expect(screen.queryByText("Open")).toBeNull();             // 상태는 여전히 추측 금지
  expect(screen.queryByText(/알 수 없/)).toBeNull();          // 대기 축은 알고 있다
});

test("단계마다 상한에 잘린 단계를 알리고 칩에 + 를 붙인다", async () => {
  renderQueue({ ...FULL, waiting_truncated: ["confirm"] });
  expect(await screen.findByText("단계마다 가장 오래 기다린 20건만 보입니다: 확인 대기")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "확인 대기 2+" })).toBeInTheDocument();
});

test("로딩 중에는 알 수 없음(RBAC 확인) 문구를 내지 않는다", async () => {
  // 응답 전 data 는 undefined 라 「알 수 없음」과 모양이 같다 -- 구분하지 않으면
  // 매 폴링 첫 페인트가 "권한을 확인하세요"로 깜빡이며 없는 원인을 가리킨다.
  renderQueue({ ...FULL, waiting: [] }, 40);
  expect(await screen.findByText("불러오는 중…")).toBeInTheDocument();
  expect(screen.queryByText(/알 수 없/)).toBeNull();
  expect(await screen.findByText(/대기 중인 잡이 없습니다/)).toBeInTheDocument();
});

test("waitText는 초를 사람이 읽는 단위로 접는다", () => {
  expect(waitText(null)).toBe("—");
  expect(waitText(0)).toBe("0초");                            // 0 은 정상값(방금 들어옴)
  expect(waitText(42)).toBe("42초");
  expect(waitText(125)).toBe("2분 5초");
  expect(waitText(3660)).toBe("1시간 1분");
});
