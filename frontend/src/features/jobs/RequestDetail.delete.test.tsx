// 요청 상세의 단건 삭제(2026-10-26 BACKLOG C): 관리자 · 끝난 요청 · 배치 작업 아님 — 목록과 같은 확인 창(DeleteRequestsDialog)
// 으로 같은 엔드포인트(POST /api/admin/requests:delete)에 id 1개를 싣고, 지웠으면 목록으로 옮기며 알린다.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { RequestDetail } from "./RequestDetail";
import { NavNotice } from "./NavNotice";

const ADMIN = { actor: "root", role: "admin" };
const server = setupServer(
  http.get("/api/user/storages", () => HttpResponse.json([])),
  http.get("/api/user/jobs/:jid/artifacts", () => HttpResponse.json({ entries: [], truncated: false })),
  http.get("/api/admin/request-purges", () => HttpResponse.json({ pending: 0, stalled: 0, oldest_requested_at: null, items: [] })),
  http.get("/api/auth/me", () => HttpResponse.json(ADMIN)));
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const AT = "2026-10-26T03:00:00Z";
const REQ = {
  request_id: "a".repeat(32), operation: "scan", requester_id: "alice", resource_key: "k", priority: "mid",
  state: "Succeeded", created_at: AT, updated_at: AT, payload: { storage: "s1", target: "team" },
  transitions: [{ from_state: null, to_state: "Pending", at: AT }, { from_state: "Pending", to_state: "Succeeded", at: AT }],
};
const JOB = {
  job_id: "j1", request_id: REQ.request_id, operation: "scan", state: "Succeeded", reason_code: null,
  preview_fingerprint: null, preview_expires_at: null, result_summary: null,
  transitions: [{ from_state: null, to_state: "Succeeded", at: AT }], artifact_uri: null, phase_refs: {},
};
const RID = REQ.request_id;

function renderAt(req: object = REQ, jobs: object[] = [JOB]) {
  server.use(
    http.get(`/api/user/requests/${RID}`, () => HttpResponse.json(req)),
    http.get(`/api/user/requests/${RID}/jobs`, () => HttpResponse.json(jobs)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/jobs/${RID}`]}>
        <Routes>
          <Route path="/jobs/:requestId" element={<RequestDetail />} />
          <Route path="/jobs" element={<><h1>전체 작업</h1><NavNotice /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const trigger = () => screen.findByRole("button", { name: "작업 삭제" });

test("관리자 · 끝난 단건 요청: 목록과 같은 확인 창 → id 1개 POST → 목록으로 이동하며 알린다", async () => {
  let sent: unknown = null;
  server.use(http.post("/api/admin/requests:delete", async ({ request }) => {
    sent = await request.json();
    return HttpResponse.json({ deleted: [{ request_id: RID, job_ids: ["j1"] }], skipped: [], purge_pending: 1 });
  }));
  renderAt();
  await userEvent.click(await trigger());
  const dlg = within(await screen.findByRole("dialog", { name: "작업 영구 삭제" }));
  expect(dlg.getByText("선택한 작업 1개를 영구 삭제합니다. 되돌릴 수 없습니다.")).toBeInTheDocument();
  // 성공 scan 잡 = 사용량 분석의 지점 -- 목록과 같은 경고(잡 상태로 판정)
  expect(dlg.getByRole("note")).toHaveTextContent("성공한 scan 1개 포함");
  expect(dlg.getByRole("button", { name: "1개 영구 삭제" })).toBeDisabled();     // 「되돌릴 수 없음」 체크 전
  await userEvent.click(dlg.getByRole("checkbox"));
  await userEvent.click(dlg.getByRole("button", { name: "1개 영구 삭제" }));
  await waitFor(() => expect(sent).toEqual({ request_ids: [RID], batches: [] }));
  expect(await screen.findByRole("heading", { name: "전체 작업" })).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent(`작업 ${RID.slice(0, 12)}을(를) 삭제했습니다`);
  // 「닫기」는 알림을 지운다(히스토리 state 를 비운다)
  await userEvent.click(screen.getByRole("button", { name: "닫기" }));
  expect(screen.queryByRole("status")).toBeNull();
});

test("서버가 제외하면(조용한 창 등) 머물러 사유를 말한다", async () => {
  server.use(http.post("/api/admin/requests:delete", () => HttpResponse.json({
    deleted: [], skipped: [{ request_id: RID, reason: "request_recently_finished" }], purge_pending: 0 })));
  renderAt();
  await userEvent.click(await trigger());
  const dlg = within(await screen.findByRole("dialog", { name: "작업 영구 삭제" }));
  await userEvent.click(dlg.getByRole("checkbox"));
  await userEvent.click(dlg.getByRole("button", { name: "1개 영구 삭제" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/^삭제하지 않았습니다 — /);
  expect(screen.getByRole("heading", { level: 1, name: "scan 요청" })).toBeInTheDocument();
});

test("배치 작업은 버튼을 잠그고 이유와 배치 단위로 지울 길(그 배치의 작업 목록)을 말한다", async () => {
  const bid = "b".repeat(32);
  renderAt({ ...REQ, batch_id: bid }, [JOB]);
  expect(await trigger()).toBeDisabled();
  expect(screen.getByText(/배치 작업은 하나씩 삭제하지 않습니다/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "이 배치의 작업 보기" })).toHaveAttribute("href", `/jobs?batch=${bid}`);
});

test("끝나지 않은 데이터 작업이 있으면 잠근다(요청은 종단이어도)", async () => {
  renderAt(REQ, [{ ...JOB, state: "Running" }]);
  expect(await trigger()).toBeDisabled();
  expect(screen.getByText(/아직 끝나지 않은 데이터 작업이 있습니다/)).toBeInTheDocument();
});

test("진행 중인 요청 · 일반 사용자에겐 버튼이 없다", async () => {
  renderAt({ ...REQ, state: "Running" }, [{ ...JOB, state: "Running" }]);
  await screen.findByRole("heading", { level: 1, name: "scan 요청" });
  await screen.findByText("자동 갱신 중");
  expect(screen.queryByRole("button", { name: "작업 삭제" })).toBeNull();
});

test("일반 사용자에겐 끝난 요청이어도 버튼이 없다", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json({ actor: "alice", role: "user" })));
  renderAt();
  await screen.findByRole("heading", { level: 1, name: "scan 요청" });
  await waitFor(() => expect(screen.queryByRole("button", { name: "작업 삭제" })).toBeNull());
});
