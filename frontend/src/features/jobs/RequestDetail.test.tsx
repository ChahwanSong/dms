import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { RequestDetail } from "./RequestDetail";

// 기본 스토리지 목록은 빈 배열(= 뿌리 모름 = 절대경로 표시 없음) — 화면이
// useStorageRoots 로 이 목록을 부르므로 기본이 없으면 MSW 미처리 경고가 난다.
const server = setupServer(
  http.get("/api/user/storages", () => HttpResponse.json([])));
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

test("renders a null result_summary value as — instead of the literal \"null\"", async () => {
  // scan/rm 잡은 설계상 바이트를 보고하지 않아 result_summary에 늘 bytes: null이
  // 온다 -- String(null)이 "null"로 새면 그 문자열이 사용자 화면에 그대로 뜬다.
  const jobs = [{ ...JOBS[0], result_summary: { files: 10, bytes: null } }];
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json(REQUEST)),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json(jobs)),
  );
  renderAt();
  const dt = await screen.findByText("bytes");
  expect(dt.nextElementSibling).toHaveTextContent("—");
  expect(screen.queryByText("null")).not.toBeInTheDocument();
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

test("제출 대기를 첫 비-Pending 전이에서 유도해 보여준다", async () => {
  // 슬라이스 17이 슬라이스 14의 「큐 대기」 라벨을 정정했다(설계 §2.4): 이 값은
  // 플래너 픽업 지연이지 Volcano 큐 대기가 아니다 -- 옛 라벨은 사용자가 "Volcano
  // 큐에서 기다린 시간"으로 읽는다.
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
  expect(await screen.findByText("제출 대기")).toBeInTheDocument();
  expect(screen.getByText("1분 30초")).toBeInTheDocument();
  expect(screen.queryByText("큐 대기")).toBeNull();   // 옛 라벨이 되살아나면 회귀
});

test("잡 취소 실패(409 cancel_failed) 문구는 해당 잡 카드에만 뜬다", async () => {
  // §2.4-4: 취소 실패가 화면에 안 보이면 사용자는 버튼이 무시됐다고 여기고 잡은
  // 계속 돈다. useCancelJob 은 요청 단위 훅 하나를 모든 잡 카드가 공유하므로,
  // cancel.variables(마지막 mutate 인자 = jobId) 한정이 없으면 오류가 모든 카드에
  // 도배된다 -- 다른 카드 부재까지 단언한다.
  const MSG = "취소에 실패했습니다 — 실행 중인 작업을 종료하지 못했습니다";
  const twoJobs = [
    { ...JOBS[0], state: "Executing", reason_code: null },
    { ...JOBS[0], job_id: "j2", state: "Executing", reason_code: null },
  ];
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json({ ...REQUEST, state: "Executing" })),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json(twoJobs)),
    http.post("/api/user/jobs/j1:cancel", () =>
      HttpResponse.json({ detail: "cancel_failed" }, { status: 409 })),
  );
  renderAt();
  const j1card = (await screen.findByText("j1")).closest(".bg-surface") as HTMLElement;
  await userEvent.click(within(j1card).getByRole("button", { name: "취소" }));
  expect(await within(j1card).findByText(MSG)).toBeInTheDocument();
  const j2card = screen.getByText("j2").closest(".bg-surface") as HTMLElement;
  expect(within(j2card).queryByText(MSG)).toBeNull();
});

test("잡 취소 성공 후 잡 목록이 즉시 갱신된다(무효화 접두 매칭의 실증 그물)", async () => {
  // useCancelJob 의 onSettled 는 ["request", id] 무효화 하나로 잡 목록(["request",
  // id, "jobs"])까지 접두 매칭으로 갱신해야 한다. 이 단언의 타임아웃(기본 1초)은
  // useRequestJobs 의 2초 폴링보다 짧다 -- 폴링이 아니라 무효화가 갱신을 실어
  // 나른다는 것을 시간축으로 구분한다(무효화를 전부 지우는 회귀가 여기서 빨개진다).
  let cancelled = false;
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json({ ...REQUEST, state: "Executing" })),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json([
      { ...JOBS[0], state: cancelled ? "Cancelled" : "Executing", reason_code: null },
    ])),
    http.post("/api/user/jobs/j1:cancel", () => {
      cancelled = true;
      return HttpResponse.json({ state: "Cancelling" });
    }),
  );
  renderAt();
  await userEvent.click(await screen.findByRole("button", { name: "취소" }));
  expect(await screen.findByText("Cancelled")).toBeInTheDocument();
});

// --- 대상·절대경로(사용자 보고 2026-08-15): "완료된 작업을 볼 때도 관리 디렉토리가
// 안 보여서 정확한 path 를 알 수 없다". payload 는 상대경로만 담으므로(서버 계약
// 무변경) 화면이 **지금의** managed_root 로 조합해 보여준다. 뿌리는 관리자 응답에만
// 실려 오므로(routes_storages) 비관리자에겐 그 줄이 아예 없다.
const SYNC_PAYLOAD = { source_storage: "cephfs", source: "team",
                       destination_storage: "gpfs", destination: "backup" };
function renderWithStorages(rows: object[], payload: object = SYNC_PAYLOAD) {
  server.use(
    http.get("/api/user/storages", () => HttpResponse.json(rows)),
    http.get("/api/user/requests/r1", () => HttpResponse.json({ ...REQUEST, payload })),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json([])));
  return renderAt();
}

test("대상과 절대경로: 상대경로 표기 옆에 지금의 managed_root 로 조합한 경로", async () => {
  renderWithStorages([
    { storage_name: "cephfs", backend_type: "cephfs", status: "Ready", managed_root: "/cephfs/dms" },
    { storage_name: "gpfs", backend_type: "gpfs", status: "Ready", managed_root: "/gpfs/dms" }]);
  expect(await screen.findByText("cephfs:team → gpfs:backup")).toBeInTheDocument();
  expect(await screen.findByText("/cephfs/dms/team → /gpfs/dms/backup")).toBeInTheDocument();
});

test("managed_root 를 못 읽으면(비관리자) 절대경로 줄 자체가 없다 — 거짓 경로 금지", async () => {
  renderWithStorages([
    { storage_name: "cephfs", backend_type: "cephfs", status: "Ready" },
    { storage_name: "gpfs", backend_type: "gpfs", status: "Ready" }]);
  expect(await screen.findByText("cephfs:team → gpfs:backup")).toBeInTheDocument();
  expect(screen.queryByText("절대경로")).toBeNull();
  expect(screen.queryByText(/\/cephfs\/dms/)).toBeNull();
});

test("한쪽 스토리지의 뿌리만 알면 sync 절대경로는 생략(반쪽 진실 금지)", async () => {
  renderWithStorages([
    { storage_name: "cephfs", backend_type: "cephfs", status: "Ready", managed_root: "/cephfs/dms" },
    { storage_name: "gpfs", backend_type: "gpfs", status: "Ready" }]);
  expect(await screen.findByText("cephfs:team → gpfs:backup")).toBeInTheDocument();
  expect(screen.queryByText("절대경로")).toBeNull();
});

test("실행 전이가 아직 없으면 제출 대기는 —", async () => {
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
  const dt = await screen.findByText("제출 대기");
  expect(dt.nextElementSibling).toHaveTextContent("—");
});
