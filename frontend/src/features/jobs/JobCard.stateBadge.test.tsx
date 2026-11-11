// 잡 카드 상태 배지 색(2026-11-11): scan 잡의 실행 단계 Running 은 sync·rm 의 Executing 과 같은 busy 다 -- 공유 pillVariant
// (요청 배지와 문자열이 겹쳐 neutral 고정)를 그대로 쓰면 Preflight(파랑) → Running(회색)으로 「대기로 돌아간」 것처럼 읽혔다.
// 같은 화면의 요청 배지(OutcomeCard -- 요청 Running)는 공유 매핑 그대로 neutral 이어야 한다(M5 관례).
import { render, screen, within } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { RequestDetail } from "./RequestDetail";

const server = setupServer(
  http.get("/api/user/storages", () => HttpResponse.json([])),
  http.get("/api/user/jobs/:jid/artifacts", () => HttpResponse.json({ entries: [], truncated: false })),
  http.get("/api/auth/me", () => HttpResponse.json({ actor: "alice", role: "user" })));
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const AT = "2026-11-11T03:00:00Z";
const RID = "a".repeat(32);
const REQ = {
  request_id: RID, operation: "scan", requester_id: "alice", resource_key: "k", priority: "mid",
  state: "Running", created_at: AT, updated_at: AT, payload: { storage: "s1", target: "team" },
  transitions: [{ from_state: null, to_state: "Pending", at: AT }, { from_state: "Pending", to_state: "Running", at: AT }],
};
const JOB = {
  job_id: "j1", request_id: RID, operation: "scan", state: "Running", reason_code: null,
  preview_fingerprint: null, preview_expires_at: null, result_summary: null,
  transitions: [], artifact_uri: null, phase_refs: {},
};

function renderAt(jobs: object[]) {
  server.use(
    http.get(`/api/user/requests/${RID}`, () => HttpResponse.json(REQ)),
    http.get(`/api/user/requests/${RID}/jobs`, () => HttpResponse.json(jobs)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/jobs/${RID}`]}>
        <Routes><Route path="/jobs/:requestId" element={<RequestDetail />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const pillsIn = (root: HTMLElement, text: string) =>
  within(root).getAllByText(text, { exact: true }).filter((el) => el.classList.contains("rounded-full"));

test("scan 잡 Running 배지는 busy(파랑), 같은 화면의 요청 Running 배지는 공유 매핑 그대로 neutral", async () => {
  renderAt([JOB]);
  const jobs = await screen.findByRole("region", { name: /^데이터 작업/ });
  await within(jobs).findByText("j1");
  const jobPill = pillsIn(jobs, "Running");
  expect(jobPill).toHaveLength(1);
  expect(jobPill[0]).toHaveClass("bg-busybg");
  expect(jobPill[0]).not.toHaveClass("bg-canvas");
  // 요청 배지(잡 구획 밖) -- 요청 Running 은 neutral 고정(공유 pillVariant 불변)
  const reqPill = pillsIn(document.body, "Running").filter((el) => !jobs.contains(el));
  expect(reqPill.length).toBeGreaterThan(0);
  for (const el of reqPill) expect(el).toHaveClass("bg-canvas");
});

test("잡 Pending(진짜 대기) 배지는 neutral 그대로", async () => {
  renderAt([{ ...JOB, state: "Pending" }]);
  const jobs = await screen.findByRole("region", { name: /^데이터 작업/ });
  await within(jobs).findByText("j1");
  const jobPill = pillsIn(jobs, "Pending");
  expect(jobPill).toHaveLength(1);
  expect(jobPill[0]).toHaveClass("bg-canvas");
});
