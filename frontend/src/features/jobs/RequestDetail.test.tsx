import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { RequestDetail } from "./RequestDetail";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const REQUEST = {
  request_id: "r1", operation: "sync", requester_id: "alice", resource_key: "storageA:/data",
  priority: "mid", state: "Failed",
  created_at: "2026-08-05T00:00:00Z", updated_at: "2026-08-05T00:01:30Z",
  payload: {},
  transitions: [
    { from_state: null, to_state: "Queued", at: "2026-08-05T00:00:00Z" },
    { from_state: "Queued", to_state: "Executing", at: "2026-08-05T00:00:10Z" },
    { from_state: "Executing", to_state: "Failed", reason_code: "no_eligible_nodes", at: "2026-08-05T00:01:30Z" },
  ],
};

const JOBS = [
  { job_id: "j1", request_id: "r1", operation: "sync", state: "Failed",
    reason_code: "no_eligible_nodes", preview_fingerprint: null, preview_expires_at: null,
    result_summary: { files: 120, bytes: 456 },
    transitions: [
      { from_state: null, to_state: "Executing", at: "2026-08-05T00:00:10Z" },
      { from_state: "Executing", to_state: "Failed", reason_code: "no_eligible_nodes", at: "2026-08-05T00:01:30Z" },
    ],
    artifact_uri: "file:///cephfs/dms/artifacts/j1", phase_refs: { preflight: "pod/p1" } },
];

function renderAt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/jobs/r1"]}>
        <Routes><Route path="/jobs/:requestId" element={<RequestDetail />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("shows a loading state before the request and jobs load", () => {
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json(REQUEST)),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json(JOBS)),
  );
  renderAt();
  expect(screen.getByText("불러오는 중…")).toBeInTheDocument();
});

test("renders the request transition timeline in order with the failed transition's reason_code", async () => {
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json(REQUEST)),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json(JOBS)),
  );
  renderAt();
  const heading = await screen.findByText("전이 이력");
  const card = heading.closest("div")!;
  const items = within(card).getAllByRole("listitem");
  expect(items).toHaveLength(3);
  expect(items[0]).toHaveTextContent("Queued");
  expect(items[1]).toHaveTextContent("Executing");
  expect(items[2]).toHaveTextContent("Failed");
  expect(items[2]).toHaveTextContent("실행 가능한 노드가 없습니다");
});

test("computes and renders the request duration from created_at to the last transition", async () => {
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json(REQUEST)),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json(JOBS)),
  );
  renderAt();
  expect(await screen.findByText("1분 30초")).toBeInTheDocument();
});

test("renders the job's result_summary as key/value pairs", async () => {
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json(REQUEST)),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json(JOBS)),
  );
  renderAt();
  expect(await screen.findByText("files")).toBeInTheDocument();
  expect(screen.getByText("120")).toBeInTheDocument();
  expect(screen.getByText("bytes")).toBeInTheDocument();
  expect(screen.getByText("456")).toBeInTheDocument();
});

test("shows an inline error message when the request fails to load", async () => {
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json({ detail: "http_404" }, { status: 404 })),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json([])),
  );
  renderAt();
  expect(await screen.findByText("http_404")).toBeInTheDocument();
});

test("shows an inline error message when the jobs list fails to load", async () => {
  // 삼키면 "잡이 0개"와 화면상 구별되지 않는다 — 실패한 조회를 성공으로 오인하게 된다.
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json(REQUEST)),
    http.get("/api/user/requests/r1/jobs", () =>
      HttpResponse.json({ detail: "http_500" }, { status: 500 })),
  );
  renderAt();
  // http_500은 이제 REASON_MESSAGES에 매핑돼 있으므로 원시 코드가 아니라 번역된
  // 메시지가 보여야 한다 (원시 코드 노출은 이번 수정으로 고친 버그였다).
  expect(await screen.findByText("서버 오류가 발생했습니다")).toBeInTheDocument();
});

test("shows the job's artifact_uri on the job card", async () => {
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json(REQUEST)),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json(JOBS)),
  );
  renderAt();
  expect(
    await screen.findByText("아티팩트 file:///cephfs/dms/artifacts/j1"),
  ).toBeInTheDocument();
});

test("shows a request-cancel button for a job-less non-terminal request and posts to the request cancel path", async () => {
  const PENDING_REQUEST = { ...REQUEST, state: "Pending", transitions: [
    { from_state: null, to_state: "Pending", at: "2026-08-05T00:00:00Z" },
  ] };
  let cancelCalled = false;
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json(PENDING_REQUEST)),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json([])),
    http.post("/api/user/requests/r1:cancel", () => {
      cancelCalled = true;
      return HttpResponse.json({ state: "Cancelled" });
    }),
  );
  renderAt();
  const button = await screen.findByRole("button", { name: "요청 취소" });
  await userEvent.click(button);
  expect(cancelCalled).toBe(true);
});

test("hides the request-cancel button when the request already has jobs", async () => {
  const PENDING_REQUEST = { ...REQUEST, state: "Pending", transitions: [
    { from_state: null, to_state: "Pending", at: "2026-08-05T00:00:00Z" },
  ] };
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json(PENDING_REQUEST)),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json(JOBS)),
  );
  renderAt();
  await screen.findByText("전이 이력");
  expect(screen.queryByRole("button", { name: "요청 취소" })).not.toBeInTheDocument();
});

test("hides the request-cancel button when the request is terminal", async () => {
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json(REQUEST)), // state: "Failed"
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json([])),
  );
  renderAt();
  await screen.findByText("전이 이력");
  expect(screen.queryByRole("button", { name: "요청 취소" })).not.toBeInTheDocument();
});

test("shows diagnostic events below the timeline", async () => {
  // events는 transitions와 달리 "일어나지 않은 전이"를 담는다 -- 이것이 안 보이면
  // 운영자는 stderr를 뒤져야 한다
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json({
      ...REQUEST,
      events: [{ id: 1, component: "planner", severity: "error",
                 event_type: "plan_error", message: "boom",
                 payload: null, at: "2026-08-06T00:00:00Z" }],
    })),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json(JOBS)),
  );
  renderAt();
  expect(await screen.findByText("plan_error")).toBeInTheDocument();
  expect(screen.getByText("boom")).toBeInTheDocument();
});

test("renders no diagnostic-events card when there are no events", async () => {
  // 정상 요청에 빈 카드가 뜨면 노이즈다 -- 카드 자체를 그리지 않아야 한다.
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json({ ...REQUEST, events: [] })),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json(JOBS)),
  );
  renderAt();
  await screen.findByText("전이 이력");
  expect(screen.queryByText("진단 이벤트")).not.toBeInTheDocument();
});

test("renders no diagnostic-events card when the backend omits or malforms the events field", async () => {
  // 방어적 정규화: 배열이 아닌 페이로드(또는 필드 누락) 하나가 SPA 전체를 흰 화면으로
  // 만든 사고가 있었다 -- Array.isArray로 방어한다.
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json({ ...REQUEST, events: { oops: true } })),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json(JOBS)),
  );
  renderAt();
  await screen.findByText("전이 이력");
  expect(screen.queryByText("진단 이벤트")).not.toBeInTheDocument();
});

test("shows the diagnostic event's payload alongside the message", async () => {
  // I3: stepper.py의 summary_unreadable/terminate_failed는 message가 없거나
  // event_type과 중복되고, 운영자가 알아야 하는 ref는 payload에만 있다.
  // payload를 렌더하지 않으면 화면에는 event_type 딱 하나만 뜨고 단서가 없다.
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json({
      ...REQUEST,
      events: [{ id: 1, component: "stepper", severity: "warning",
                 event_type: "summary_unreadable", message: null,
                 payload: { ref: "pod/p9" }, at: "2026-08-06T00:00:00Z" }],
    })),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json(JOBS)),
  );
  renderAt();
  expect(await screen.findByText("summary_unreadable")).toBeInTheDocument();
  expect(screen.getByText(/pod\/p9/)).toBeInTheDocument();
});

test("does not crash when an event's payload is an array or other unexpected shape", async () => {
  // 방어적 정규화: payload는 백엔드가 dict로 쓰지만, 뭐가 오든(배열, 문자열, 숫자)
  // 렌더가 죽으면 안 된다 -- events 필드 자체의 방어(위 테스트들)와 같은 이유.
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json({
      ...REQUEST,
      events: [{ id: 1, component: "stepper", severity: "warning",
                 event_type: "weird_payload", message: null,
                 payload: [1, 2, 3], at: "2026-08-06T00:00:00Z" }],
    })),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json(JOBS)),
  );
  renderAt();
  expect(await screen.findByText("weird_payload")).toBeInTheDocument();
});

test("M5: does not crash when transitions is null (malformed backend payload)", async () => {
  // 회귀 가드: transitions[transitions.length - 1]가 무방어였다 -- null이면 TypeError로
  // 렌더가 죽는다(router.innerBoundary.test.tsx가 이 크래시를 안쪽 경계 시험 재료로
  // 썼었는데, 이 방어를 넣으면서 그 재료를 던지는 컴포넌트로 바꿔치기했다). 정상적인
  // 배열이 아닌 페이로드가 와도 화면 자체는 죽지 않아야 한다.
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json({ ...REQUEST, transitions: null })),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json(JOBS)),
  );
  renderAt();
  expect(await screen.findByText("전이 이력")).toBeInTheDocument();
  expect(screen.getByText("전이 이력이 없습니다")).toBeInTheDocument();
});

test("shows a truncation notice when events_truncated is true", async () => {
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json({
      ...REQUEST,
      events: [{ id: 1, component: "planner", severity: "error",
                 event_type: "plan_error", message: "boom",
                 payload: null, at: "2026-08-06T00:00:00Z" }],
      events_truncated: true,
    })),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json(JOBS)),
  );
  renderAt();
  const heading = await screen.findByText("진단 이벤트");
  const card = heading.closest("div")!;
  expect(within(card).getByText(/100건/)).toBeInTheDocument();
});

test("큐 대기를 첫 비-Pending 전이에서 유도해 보여준다", async () => {
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json({
      ...REQUEST,
      transitions: [
        { from_state: null, to_state: "Pending", at: "2026-08-05T00:00:00Z" },
        { from_state: "Pending", to_state: "Planned", at: "2026-08-05T00:01:30Z" },
        { from_state: "Planned", to_state: "Failed",
          reason_code: "no_eligible_nodes", at: "2026-08-05T00:02:00Z" },
      ],
    })),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json(JOBS)),
  );
  renderAt();
  expect(await screen.findByText("큐 대기")).toBeInTheDocument();
  expect(screen.getByText("1분 30초")).toBeInTheDocument();
});

test("실행 전이가 아직 없으면 큐 대기는 —", async () => {
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json({
      ...REQUEST, state: "Pending",
      transitions: [
        { from_state: null, to_state: "Pending", at: "2026-08-05T00:00:00Z" },
      ],
    })),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json([])),
  );
  renderAt();
  const dt = await screen.findByText("큐 대기");
  expect(dt.nextElementSibling).toHaveTextContent("—");
});
