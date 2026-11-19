// 배치 상세의 백그라운드 배치 단위 삭제(2026-11-19 사용자 결정 「상한 기본 50000」 -- 묘비 Deleting)와 「배치 삭제」(기록만) 창의 상한
// 정보 줄(같은 날 사용자 요청 「상한 값도 삭제 시 보여지고」). 서버 상세의 deleting·미리보기 POST /api/admin/requests:delete-preview 가 재료다.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";
import { BatchDetail, recordDeleteInfo } from "./BatchDetail";
import { REASON_MESSAGES } from "../../lib/api";
import type { BatchItem } from "../../lib/types";

const server = setupServer(
  http.get("/api/user/storages", () => HttpResponse.json([])),
  http.get("/api/user/requests/:rid/jobs", () => HttpResponse.json([])));
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const ITEM: BatchItem = { seq: 0, payload: { storage: "s1", target: "proj/a" }, status: "Succeeded", request_id: "r0",
                          reason_code: null };
const LIMITS = { requests_per_call: 200, batches_per_call: 10, batch_request_count: 50000, batch_item_count: 10000,
                 call_request_count: 100000, sync_request_count: 500, list_count_cap: 5000 };
const head = (over: Record<string, unknown> = {}) => ({
  batch_id: "b1", operation: "scan", status: "Completed", max_concurrency: 2, item_count: 1, succeeded_count: 1,
  failed_count: 0, note: null, created_at: "", options: {}, storage: "s1", schedule: null, ...over });

function renderAt(h: Record<string, unknown>) {
  server.use(http.get("/api/admin/batches/b1", ({ request }) => {
    const view = new URL(request.url).searchParams.get("view");
    if (view === "summary") {
      return HttpResponse.json({ ...h, item_total: 1, status_counts: { Succeeded: 1 }, first_payload: ITEM.payload,
                                 executing_count: null, direct_tools: null });
    }
    if (view === "items") return HttpResponse.json({ total: 1, offset: 0, limit: 100, items: [ITEM], filters: {} });
    return HttpResponse.json({ ...h, items: [ITEM] });
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/admin/batches/b1"]}>
    <Routes><Route path="/admin/batches/:batchId" element={<BatchDetail />} /></Routes>
  </MemoryRouter></QueryClientProvider>);
}

test("Deleting: 알약 「삭제 중」 + 진행 카드(지운 수·시작) · 바꾸는 버튼이 없다", async () => {
  renderAt(head({ status: "Deleting", deleting: {
    batch_id: "b1", dangling: false, requested_by: "root", requested_at: "2026-11-19T01:00:00Z",
    expected_request_count: 48210, deleted_request_count: 12000, last_error: null, blocked_request_id: null,
    updated_at: "2026-11-19T01:00:30Z" } }));
  const card = await screen.findByRole("status", { name: "배치 삭제 진행" });
  expect(card).toHaveTextContent("작업 12,000 / 48,210개 삭제됨 · 2026-11-19 10:00:00에 root 시작");
  expect(screen.getAllByText("삭제 중").length).toBeGreaterThan(0);
  for (const name of ["편집", "전체 재실행", "배치 삭제", "실패분 재실행"]) {
    expect(screen.queryByRole("button", { name })).toBeNull();
  }
  // 이 라인의 「스케줄 추가」는 머리 링크다(ScheduleLink) -- schedule null 이어도 묘비 뒤엔 그리지 않는다.
  expect(screen.queryByRole("link", { name: "스케줄 추가" })).toBeNull();
});

test("Deleting + 지연: 사유와 문제 작업 링크", async () => {
  renderAt(head({ status: "Deleting", deleting: {
    batch_id: "b1", dangling: false, requested_by: "root", requested_at: "2026-11-19T01:00:00Z",
    expected_request_count: 10, deleted_request_count: 3, last_error: "request_job_active",
    blocked_request_id: "f".repeat(32), updated_at: "2026-11-19T01:00:30Z" } }));
  const card = await screen.findByRole("status", { name: "배치 삭제 진행" });
  expect(card).toHaveTextContent(`지연: ${REASON_MESSAGES.request_job_active}`);
  expect(within(card).getByRole("link", { name: "ffffffffffff" })).toHaveAttribute("href", `/jobs/${"f".repeat(32)}`);
});

test("기록 삭제 창: 미리보기로 이 배치의 작업·항목 수와 상한 -- 넘어도 제출은 막지 않는다", async () => {
  server.use(http.post("/api/admin/requests:delete-preview", () => HttpResponse.json({ limits: LIMITS, batches: [
    { batch_id: "b1", exists: true, name: null, status: "Completed", operation: "scan", request_count: 50001,
      request_count_capped: true, max_commit_order: null, item_count: 2, item_count_capped: false, verdict: "blocked",
      reason: "batch_delete_too_large", request_id: null, limit: 50000, count: 61000, count_at_least: null,
      deleting: null, schedule: null }] })));
  renderAt(head());
  await userEvent.click(await screen.findByRole("button", { name: "배치 삭제" }));
  const dlg = await screen.findByRole("dialog");
  const note = await within(dlg).findByRole("note", { name: "배치 단위 삭제 상한" });
  expect(note).toHaveTextContent(
    "작업까지 지우는 배치 단위 삭제는 삭제 불가 — 작업 61,000개가 한 번에 지울 수 있는 상한 50,000개를 넘습니다(기록 삭제는 상한과 관계없이 됩니다)");
  expect(note.className).toContain("text-attn");
  expect(within(dlg).getByRole("button", { name: "삭제 확인" })).toBeEnabled();
});

test("recordDeleteInfo: 상한 안이면 수와 상한, 모르면 null", () => {
  const row = { batch_id: "b1", exists: true, name: null, status: "Completed", operation: "scan", request_count: 1234,
                request_count_capped: false, max_commit_order: 9, item_count: 12, item_count_capped: false,
                verdict: "async" as const, reason: null, request_id: null, limit: null, count: null, count_at_least: null,
                deleting: null };
  expect(recordDeleteInfo({ limits: LIMITS, batches: [row] })).toEqual({
    text: "작업까지 지우는 배치 단위 삭제: 이 배치 작업 1,234개·항목 12개 — 상한 작업 50,000개·항목 10,000개", over: false });
  expect(recordDeleteInfo(null)).toBeNull();
  expect(recordDeleteInfo({ limits: {}, batches: [row] })).toBeNull();
});
