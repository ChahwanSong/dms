import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { BatchSchedulesPage } from "./BatchSchedulesPage";
import type { BatchSchedule, BatchScheduleRun } from "./batchSchedule";

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const row = (over: Partial<BatchSchedule> = {}): BatchSchedule => ({
  schedule_id: "s1", batch_id: "bscan00000000aaa", kind: "daily", spec: { hour: 2, minute: 0 }, enabled: true,
  next_run_at: "2026-11-17T17:00:00Z", armed_by: "ops", created_by: "kim", created_at: "", updated_at: "",
  last_run_at: null, last_outcome: null, last_reason_code: null, paused_reason_code: null,
  acknowledged_irreversible: false, destructive: false,
  batch: { batch_id: "bscan00000000aaa", name: "야간 스캔", operation: "scan", status: "Completed", item_count: 2,
           storage: "ceph", auto_confirm: false, options: {} },
  child_count: 4, child_count_capped: false, child_count_limit: 2000, confirm_wait: null,
  ...over,
});

interface Call { method: string; path: string; body?: unknown }
function serve(rows: BatchSchedule[] | (() => Response), opts: { canArm?: boolean; toggle?: (path: string) => Response;
                                                                  runs?: BatchScheduleRun[] } = {}) {
  const calls: Call[] = [];
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "ops", role: "admin", can_run_as_root: opts.canArm ?? true })),
    http.get("/api/admin/batch-schedules", () => (typeof rows === "function" ? rows() : HttpResponse.json(rows))),
    http.get("/api/admin/batches", () => HttpResponse.json([])),
    http.get("/api/admin/batch-schedules/:id/runs", ({ params }) => {
      calls.push({ method: "RUNS", path: String(params.id) });
      return HttpResponse.json(opts.runs ?? []);
    }),
    http.post(/\/api\/admin\/batch-schedules\/[^/]+:(enable|disable)$/, async ({ request }) => {
      const path = new URL(request.url).pathname;
      calls.push({ method: "POST", path, body: await request.json().catch(() => null) });
      return opts.toggle ? opts.toggle(path) : HttpResponse.json(row());
    }),
    http.delete("/api/admin/batch-schedules/:id", ({ params }) => {
      calls.push({ method: "DELETE", path: String(params.id) });
      return HttpResponse.json({ deleted: params.id });
    }),
    http.post(/\/api\/admin\/batch-schedules:preview$/, () => HttpResponse.json({ next_runs: [], confirm_wait: null })),
  );
  return calls;
}

function renderPage(entry = "/admin/batch-schedules") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}><BatchSchedulesPage /></MemoryRouter>
    </QueryClientProvider>);
}

test("행: 배치 링크·작업·대상 스토리지·주기·다음 실행(KST)·마지막 결과(없음 ≠ 실패)·켠 사람", async () => {
  serve([row()]);
  renderPage();
  expect(await screen.findByRole("heading", { name: "배치 스케줄러" })).toBeInTheDocument();
  const link = await screen.findByRole("link", { name: "야간 스캔" });
  expect(link).toHaveAttribute("href", "/admin/batches/bscan00000000aaa");
  const tr = link.closest("tr")!;
  expect(within(tr).getByText("scan")).toBeInTheDocument();
  expect(within(tr).getByText("ceph")).toBeInTheDocument();
  expect(within(tr).getByText("매일 02:00 (KST)")).toBeInTheDocument();
  expect(within(tr).getByText("2026-11-18")).toBeInTheDocument();          // 17:00Z = 다음날 02:00 KST
  expect(within(tr).getByText("아직 없음")).toBeInTheDocument();
  expect(within(tr).getByText("ops")).toBeInTheDocument();
  expect(within(tr).getByRole("checkbox", { name: "야간 스캔 사용" })).toBeChecked();
});

test("꺼진 스케줄은 다음 실행 「—」, 자동 일시정지면 사유를 보인다", async () => {
  serve([row({ enabled: 0, next_run_at: null, paused_reason_code: "batch_schedule_batch_changed",
               last_outcome: "paused", last_reason_code: "batch_schedule_batch_changed" })]);
  renderPage();
  const box = await screen.findByRole("checkbox", { name: "야간 스캔 사용" });
  expect(box).not.toBeChecked();
  const tr = box.closest("tr")!;
  expect(within(tr).getByText("—")).toBeInTheDocument();
  expect(within(tr).getByText(/자동 일시정지/)).toBeInTheDocument();
  expect(within(tr).getByText("일시정지")).toBeInTheDocument();
});

test("끄기는 :disable 을 바로 부른다", async () => {
  const calls = serve([row()]);
  renderPage();
  await userEvent.click(await screen.findByRole("checkbox", { name: "야간 스캔 사용" }));
  await waitFor(() => expect(calls.filter((c) => c.method === "POST").map((c) => c.path))
    .toEqual(["/api/admin/batch-schedules/s1:disable"]));
});

test("파괴적 스케줄 켜기는 동의 창을 거쳐 명시 true 로 :enable", async () => {
  const calls = serve([row({ enabled: false, next_run_at: null, destructive: true,
                             batch: { ...row().batch!, operation: "rm", options: { recursive: true } } })]);
  renderPage();
  await userEvent.click(await screen.findByRole("checkbox", { name: "야간 스캔 사용" }));
  const dlg = await screen.findByRole("dialog", { name: "스케줄 켜기" });
  expect(calls.filter((c) => c.method === "POST")).toEqual([]);           // 동의 전엔 아무것도 보내지 않는다
  const on = within(dlg).getByRole("button", { name: "켜기" });
  expect(on).toBeDisabled();
  await userEvent.click(within(dlg).getByRole("checkbox", { name: /되돌릴 수 없는 삭제/ }));
  await userEvent.click(on);
  await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toEqual([
    { method: "POST", path: "/api/admin/batch-schedules/s1:enable", body: { acknowledge_irreversible: true } }]));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "스케줄 켜기" })).toBeNull());
});

test("화면이 파괴적인 줄 몰랐던 스케줄 — 서버 batch_schedule_ack_required 면 동의 창으로 넘긴다", async () => {
  serve([row({ enabled: false, next_run_at: null, destructive: undefined })], {
    toggle: () => HttpResponse.json({ detail: "batch_schedule_ack_required" }, { status: 422 }) });
  renderPage();
  await userEvent.click(await screen.findByRole("checkbox", { name: "야간 스캔 사용" }));
  expect(await screen.findByRole("dialog", { name: "스케줄 켜기" })).toBeInTheDocument();
});

test("root 실행 자격이 없으면 추가·수정·켜기를 잠그고 끄기는 열어 둔다", async () => {
  serve([row(), row({ schedule_id: "s2", batch_id: "boff000000000bbb", enabled: false, next_run_at: null,
                      batch: { ...row().batch!, batch_id: "boff000000000bbb", name: "꺼진 배치" } })], { canArm: false });
  renderPage();
  expect(await screen.findByText(/끄기는 모든 관리자가 할 수 있습니다/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "스케줄 추가" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "야간 스캔 수정" })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: "야간 스캔 사용" })).toBeEnabled();      // 켜진 행 — 끄기 가능
  expect(screen.getByRole("checkbox", { name: "꺼진 배치 사용" })).toBeDisabled();     // 꺼진 행 — 켜기 잠금
});

test("삭제는 2단 확인 뒤 DELETE", async () => {
  const calls = serve([row()]);
  renderPage();
  await userEvent.click(await screen.findByRole("button", { name: "야간 스캔 삭제" }));
  expect(calls.filter((c) => c.method === "DELETE")).toEqual([]);
  await userEvent.click(screen.getByRole("button", { name: "야간 스캔 삭제 확인" }));
  await waitFor(() => expect(calls.filter((c) => c.method === "DELETE")).toEqual([{ method: "DELETE", path: "s1" }]));
});

test("자식 요청이 상한의 80% 를 넘으면 배치 단위 삭제가 곧 막힌다고 경고한다(자동 정리 없음)", async () => {
  serve([row({ child_count: 1700 }), row({ schedule_id: "s2", batch_id: "bcap000000000ccc", child_count: 2001,
                                          child_count_capped: true,
                                          batch: { ...row().batch!, batch_id: "bcap000000000ccc", name: "큰 배치" } })]);
  renderPage();
  expect(await screen.findByText(/자식 요청 1,700개 — 2,000개를 넘으면 배치 단위로 지울 수 없게 됩니다/)).toBeInTheDocument();
  expect(screen.getByText(/2,000개를 넘어 이 배치는 배치 단위로 지울 수 없습니다/)).toBeInTheDocument();
});

test("자동 확인 꺼진 sync 의 짧은 주기는 목록에서도 「확인 없으면 2일에 한 번」을 보인다", async () => {
  serve([row({ batch: { ...row().batch!, operation: "sync", source_storage: "a", destination_storage: "b" },
               confirm_wait: { period_seconds: 86400, preview_ttl_seconds: 86400, unconfirmed_every_seconds: 172800 } })]);
  renderPage();
  const note = await screen.findByText("확인 없으면 2일에 한 번");
  expect(note.getAttribute("title")).toContain("2일에 한 번만 미리보기를 만듭니다");
  expect(screen.getByText("a → b")).toBeInTheDocument();
});

test("조회 실패는 빈 목록이 아니라 오류로 보인다", async () => {
  serve(() => HttpResponse.json({ detail: "internal_error" }, { status: 500 }));
  renderPage();
  await waitFor(() => expect(screen.queryByText("불러오는 중…")).toBeNull());
  expect(screen.queryByText(/스케줄이 없습니다/)).toBeNull();
});

test("빈 목록 안내", async () => {
  serve([]);
  renderPage();
  expect(await screen.findByText(/스케줄이 없습니다/)).toBeInTheDocument();
});

test("?batch= 는 그 배치를 고른 추가 창을 연다(배치 상세의 「스케줄 추가」)", async () => {
  serve([]);
  renderPage("/admin/batch-schedules?batch=bnew000000000ddd");
  expect(await screen.findByRole("dialog", { name: "스케줄 추가" })).toBeInTheDocument();
});

test("?batch= 가 이미 스케줄 있는 배치면 그 스케줄의 수정 창", async () => {
  serve([row()]);
  renderPage("/admin/batch-schedules?batch=bscan00000000aaa");
  expect(await screen.findByRole("dialog", { name: "스케줄 수정" })).toBeInTheDocument();
});

test("기록 창: 결과·사유·직전 회차(0 은 「0개」, null 은 표시 안 함)", async () => {
  const runs: BatchScheduleRun[] = [
    { run_id: 3, schedule_id: "s1", batch_id: "b", scheduled_for: "2026-11-16T17:00:00Z", at: "2026-11-16T17:00:05Z",
      outcome: "fired", reason_code: null, batch_status: "Completed", requeued: 0, prev_status: "Completed",
      prev_item_count: 2, prev_succeeded: 2, prev_failed: 0 },
    { run_id: 2, schedule_id: "s1", batch_id: "b", scheduled_for: "2026-11-15T17:00:00Z", at: "2026-11-15T17:00:05Z",
      outcome: "skipped", reason_code: "batch_schedule_awaiting_confirm", batch_status: "PreviewReady", requeued: null,
      prev_status: null, prev_item_count: null, prev_succeeded: null, prev_failed: null },
  ];
  const calls = serve([row()], { runs });
  renderPage();
  await userEvent.click(await screen.findByRole("button", { name: "야간 스캔 기록" }));
  const dlg = await screen.findByRole("dialog", { name: "실행 기록 — 야간 스캔" });
  expect(await within(dlg).findByText("시작함")).toBeInTheDocument();
  expect(within(dlg).getByText(/항목 0개 다시 실행 · 직전 회차 Completed · 성공 2 · 실패 0 · 전체 2/)).toBeInTheDocument();
  expect(within(dlg).getByText("건너뜀")).toBeInTheDocument();
  expect(within(dlg).getByText("배치 상태 확인 대기")).toBeInTheDocument();
  expect(calls.filter((c) => c.method === "RUNS")).toEqual([{ method: "RUNS", path: "s1" }]);
});
