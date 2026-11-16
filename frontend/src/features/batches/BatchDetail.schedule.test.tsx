// 배치 상세의 배치 스케줄러 자리(2026-11-16): 머리의 스케줄 배지(켜짐·일시정지·꺼짐 — 스케줄러 화면 링크) · 「스케줄 추가」
// (스케줄러 화면과 같은 창, 이 배치를 고른 채) · 취소 창의 「이 배치의 스케줄도 끄기」(취소와 같은 요청 — disable_schedule) ·
// 배치 삭제 창의 「스케줄도 함께 삭제」. 서버 상세 요약(view=summary)의 schedule 이 재료다(null = 없음, 부재 = 옛 서버 = 모름).
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";
import { BatchDetail } from "./BatchDetail";
import { REASON_MESSAGES } from "../../lib/api";
import type { BatchItem } from "../../lib/types";

const server = setupServer(
  http.get("/api/user/storages", () => HttpResponse.json([])),
  http.get("/api/user/requests/:rid/jobs", () => HttpResponse.json([])));
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const ITEM: BatchItem = { seq: 0, payload: { storage: "s1", target: "proj/a" }, status: "Succeeded", request_id: "r0",
                          reason_code: null };
const DAILY = { schedule_id: "sch1", kind: "daily", spec: { hour: 2, minute: 0 }, enabled: true,
                next_run_at: "2026-11-16T17:00:00Z", last_outcome: null, last_reason_code: null, paused_reason_code: null };
const head = (over: Record<string, unknown> = {}) => ({
  batch_id: "b1", operation: "scan", status: "Completed", max_concurrency: 2, item_count: 1, succeeded_count: 1,
  failed_count: 0, note: null, created_at: "", options: {}, storage: "s1", ...over });

// 새 파드 흉내(view 마다 서버 routes_batches.get_batch 와 같은 모양). schedule 은 summary 에만 싣는다(서버와 같다).
function serve(h: Record<string, unknown>) {
  server.use(http.get("/api/admin/batches/b1", ({ request }) => {
    const view = new URL(request.url).searchParams.get("view");
    if (view === "summary") {
      return HttpResponse.json({ ...h, item_total: 1, status_counts: { Succeeded: 1 }, first_payload: ITEM.payload,
                                 executing_count: null, direct_tools: null });
    }
    if (view === "items") return HttpResponse.json({ total: 1, offset: 0, limit: 100, items: [ITEM], filters: {} });
    const { schedule: _omit, ...rest } = h;
    return HttpResponse.json({ ...rest, items: [ITEM] });
  }));
}

function renderAt(h: Record<string, unknown>) {
  serve(h);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/admin/batches/b1"]}>
    <Routes><Route path="/admin/batches/:batchId" element={<BatchDetail />} /></Routes>
  </MemoryRouter></QueryClientProvider>);
}

test("켜진 스케줄: 머리에 주기·다음 실행(KST, 분 단위) 배지 -- 스케줄러 화면의 그 배치로 가는 링크", async () => {
  renderAt(head({ schedule: DAILY }));
  const link = await screen.findByRole("link", { name: "스케줄 매일 02:00 (KST) · 다음 2026-11-17 02:00" });
  expect(link).toHaveAttribute("href", "/admin/batch-schedules?batch=b1");
  expect(link.className).toContain("text-accent");
  // 스케줄이 있으면 「스케줄 추가」는 없다(배치당 하나 -- 배지가 링크다)
  expect(screen.queryByRole("button", { name: "스케줄 추가" })).toBeNull();
});

test("배지 아래 회차 보존 안내 -- 서버 keep_rounds 가 숫자일 때만(옛 서버면 말하지 않는다)", async () => {
  const { unmount } = renderAt(head({ schedule: { ...DAILY, keep_rounds: 5, pruned_total: 3,
                                                  pruned_at: "2026-11-16T09:00:00Z" } }));
  expect(await screen.findByText("최근 5회차만 보존 — 전체 재실행하면 가장 오래된 회차가 정리될 수 있습니다"))
    .toBeInTheDocument();
  unmount();
  renderAt(head({ schedule: DAILY }));
  await screen.findByRole("link", { name: "스케줄 매일 02:00 (KST) · 다음 2026-11-17 02:00" });
  expect(screen.queryByText(/회차만 보존/)).toBeNull();
});

test("자동 일시정지는 사유를, 사람이 끈 스케줄은 「꺼짐」을 말한다", async () => {
  const { unmount } = renderAt(head({ schedule: { ...DAILY, enabled: false, next_run_at: null,
                                                  paused_reason_code: "batch_schedule_batch_changed" } }));
  const paused = await screen.findByRole("link", {
    name: `스케줄 일시정지 — ${REASON_MESSAGES.batch_schedule_batch_changed}` });
  expect(paused.className).toContain("text-bad");
  unmount();
  renderAt(head({ schedule: { ...DAILY, enabled: 0, next_run_at: null } }));
  expect(await screen.findByRole("link", { name: "스케줄 꺼짐 — 매일 02:00 (KST)" })).toBeInTheDocument();
});

test("스케줄 없음(null): 자격 있는 관리자에게 「스케줄 추가」 -- 이 배치를 고른 스케줄 창이 열린다", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "ops", role: "admin", can_run_as_root: true })),
    http.get("/api/admin/batches", () => HttpResponse.json([{ ...head(), schedule: null }])),
    http.post("/api/admin/batch-schedules:preview", () =>
      HttpResponse.json({ next_runs: ["2026-11-16T17:00:00Z"], confirm_wait: null })));
  renderAt(head({ schedule: null }));
  const add = await screen.findByRole("button", { name: "스케줄 추가" });
  await waitFor(() => expect(add).toBeEnabled());
  await userEvent.click(add);
  const dialog = await screen.findByRole("dialog", { name: "스케줄 추가" });
  await waitFor(() => expect(within(dialog).getByRole("combobox", { name: "배치" })).toHaveValue("b1"));
});

test("자격 없는 관리자는 「스케줄 추가」가 잠기고, 옛 서버(schedule 부재)·스케줄 불가 연산엔 아무것도 없다", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json({ actor: "bob", role: "admin", can_run_as_root: false })));
  const { unmount } = renderAt(head({ schedule: null }));
  const add = await screen.findByRole("button", { name: "스케줄 추가" });
  await waitFor(() => expect(add).toHaveAttribute("title", "root 실행 자격이 있는 관리자만 할 수 있습니다"));
  expect(add).toBeDisabled();
  unmount();
  const old = renderAt(head());                                   // schedule 키 없음 = 모름
  expect(await screen.findByRole("button", { name: "전체 재실행" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "스케줄 추가" })).toBeNull();
  expect(screen.queryByRole("link", { name: /^스케줄/ })).toBeNull();
  old.unmount();
  renderAt(head({ operation: "bogus", schedule: null }));
  expect(await screen.findByRole("button", { name: "전체 재실행" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "스케줄 추가" })).toBeNull();
});

test("켜진 스케줄이 있는 배치의 취소: 창이 「스케줄도 끄기」를 묻고(기본 체크) 취소와 같은 요청으로 끈다", async () => {
  const bodies: string[] = [];
  server.use(http.post("/api/admin/batches/b1:cancel", async ({ request }) => {
    bodies.push(await request.text());
    return HttpResponse.json({ status: "Cancelled", schedule_disabled: "sch1" });
  }));
  renderAt(head({ status: "Running", schedule: DAILY }));
  await userEvent.click(await screen.findByRole("button", { name: "취소" }));
  const dialog = await screen.findByRole("dialog", { name: "배치 취소" });
  const box = within(dialog).getByRole("checkbox", { name: "이 배치의 스케줄도 끄기" });
  expect(box).toBeChecked();
  expect(within(dialog).getByText(/스케줄을 끄지 않으면 다음 예정 시각에 다시 실행됩니다/)).toBeInTheDocument();
  expect(bodies).toEqual([]);                                     // 창을 연 것만으로는 아무것도 보내지 않는다
  await userEvent.click(within(dialog).getByRole("button", { name: "취소하고 스케줄 끄기" }));
  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(JSON.parse(bodies[0])).toEqual({ disable_schedule: true });   // 한 요청(:disable 을 따로 부르지 않는다)
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "배치 취소" })).toBeNull());
});

test("체크를 풀면 「취소만」 -- 본문 없이 취소한다(스케줄은 그대로)", async () => {
  const bodies: string[] = [];
  server.use(http.post("/api/admin/batches/b1:cancel", async ({ request }) => {
    bodies.push(await request.text());
    return HttpResponse.json({ status: "Cancelled" });
  }));
  renderAt(head({ status: "PreviewReady", operation: "sync", schedule: DAILY,
                  storage: null, source_storage: "s1", destination_storage: "s2" }));
  await userEvent.click(await screen.findByRole("button", { name: "취소" }));
  const dialog = await screen.findByRole("dialog", { name: "배치 취소" });
  await userEvent.click(within(dialog).getByRole("checkbox", { name: "이 배치의 스케줄도 끄기" }));
  await userEvent.click(within(dialog).getByRole("button", { name: "취소만" }));
  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(bodies[0]).toBe("");
});

test("취소가 거부되면 창에 사유를 보이고 닫지 않는다", async () => {
  server.use(http.post("/api/admin/batches/b1:cancel", () =>
    HttpResponse.json({ detail: "batch_not_cancelable" }, { status: 409 })));
  renderAt(head({ status: "Running", schedule: DAILY }));
  await userEvent.click(await screen.findByRole("button", { name: "취소" }));
  const dialog = await screen.findByRole("dialog", { name: "배치 취소" });
  await userEvent.click(within(dialog).getByRole("button", { name: "취소하고 스케줄 끄기" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(REASON_MESSAGES.batch_not_cancelable);
  expect(screen.getByRole("dialog", { name: "배치 취소" })).toBeInTheDocument();
});

test("꺼진 스케줄·스케줄 없음이면 취소는 지금처럼 바로 보낸다(창 없음)", async () => {
  let n = 0;
  server.use(http.post("/api/admin/batches/b1:cancel", () => { n += 1; return HttpResponse.json({ status: "Cancelled" }); }));
  renderAt(head({ status: "Running", schedule: { ...DAILY, enabled: false, next_run_at: null } }));
  await userEvent.click(await screen.findByRole("button", { name: "취소" }));
  await waitFor(() => expect(n).toBe(1));
  expect(screen.queryByRole("dialog", { name: "배치 취소" })).toBeNull();
});

test("배치 삭제 창: 스케줄이 있으면 스케줄·실행 기록·보존 회차 기록도 함께 지워진다고 말한다", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json({ actor: "ops", role: "admin", can_run_as_root: true })));
  const { unmount } = renderAt(head({ schedule: DAILY }));
  await userEvent.click(await screen.findByRole("button", { name: "배치 삭제" }));
  const dialog = await screen.findByRole("dialog", { name: "배치 삭제" });
  expect(within(dialog).getByRole("note", { name: "스케줄도 함께 삭제" }))
    .toHaveTextContent("이 배치의 스케줄(매일 02:00 (KST) · 사용 중)과 실행 기록·보존 회차 기록도 함께 삭제됩니다(감사 기록은 남습니다).");
  unmount();
  renderAt(head({ schedule: null }));
  await userEvent.click(await screen.findByRole("button", { name: "배치 삭제" }));
  const plain = await screen.findByRole("dialog", { name: "배치 삭제" });
  expect(within(plain).queryByRole("note", { name: "스케줄도 함께 삭제" })).toBeNull();
});
