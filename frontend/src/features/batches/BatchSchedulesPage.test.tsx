import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { BatchSchedulesPage } from "./BatchSchedulesPage";
import { reasonText } from "../../lib/api";
import type { BatchSchedule, BatchScheduleRun } from "../../lib/types";

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const ID1 = "b111111111111aaa";
const ID2 = "b222222222222bbb";

const sched = (over: Partial<BatchSchedule> = {}): BatchSchedule => ({
  schedule_id: "s1", batch_id: ID1, kind: "daily", spec: { hour: 2, minute: 0 }, enabled: 1,
  next_run_at: "2026-11-16T17:00:00Z", armed_by: "root-admin", created_by: "maker",
  created_at: "2026-11-16T00:00:00Z", updated_at: "2026-11-16T00:00:00Z",
  last_run_at: null, last_outcome: null, last_reason_code: null, paused_reason_code: null,
  batch: { name: "정기 스캔", operation: "scan", status: "Completed", item_count: 3, auto_confirm: false, storage: "ceph" },
  ...over,
});

interface Call { method: string; path: string; body: unknown }

// 스케줄 API 를 한 벌로 흉내 낸다 — 토글·삭제는 서버 상태(rows)를 바꾸고, 무효화 뒤 재조회가 그 상태를 읽는다(화면의
// 진실 = 서버 재조회). 콜론 경로(:enable 등)는 정규식으로 받는다(문자열 경로의 ':' 는 msw 에서 경로 변수다).
function serve(initial: BatchSchedule[], opts: {
  me?: object; toggle?: (id: string, verb: string, body: unknown) => Response | undefined;
  runs?: BatchScheduleRun[] | (() => Response); batches?: object[];
} = {}) {
  let rows = initial;
  const calls: Call[] = [];
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json(opts.me ?? { actor: "root-admin", role: "admin", can_run_as_root: true })),
    http.get("/api/admin/batch-schedules", () => HttpResponse.json(rows)),
    http.get("/api/admin/batches", () => HttpResponse.json(opts.batches ?? [])),
    http.get(/\/api\/admin\/batch-schedules\/[^/]+\/runs/, () => {
      const r = opts.runs ?? [];
      return typeof r === "function" ? r() : HttpResponse.json(r);
    }),
    http.post(/\/api\/admin\/batch-schedules:preview$/, () =>
      HttpResponse.json({ next_runs: ["2026-11-16T17:00:00Z"] })),
    http.post(/\/api\/admin\/batch-schedules\/([^/]+):(enable|disable)$/, async ({ request }) => {
      const m = new URL(request.url).pathname.match(/batch-schedules\/([^/]+):(enable|disable)$/)!;
      const body = await request.json();
      calls.push({ method: "POST", path: `${m[1]}:${m[2]}`, body });
      const override = opts.toggle?.(m[1], m[2], body);
      if (override) return override;
      rows = rows.map((s) => s.schedule_id === m[1]
        ? { ...s, enabled: m[2] === "enable" ? 1 : 0, paused_reason_code: null,
            next_run_at: m[2] === "enable" ? "2026-11-16T17:00:00Z" : null } : s);
      return HttpResponse.json(rows.find((s) => s.schedule_id === m[1]));
    }),
    http.delete("/api/admin/batch-schedules/:id", ({ params }) => {
      calls.push({ method: "DELETE", path: String(params.id), body: null });
      rows = rows.filter((s) => s.schedule_id !== params.id);
      return new HttpResponse(null, { status: 204 });
    }),
  );
  return calls;
}

function Where() { const l = useLocation(); return <span data-testid="where">{l.pathname + l.search}</span>; }

function renderPage(at = "/admin/batch-schedules") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[at]}>
        <Routes><Route path="/admin/batch-schedules" element={<><BatchSchedulesPage /><Where /></>} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>);
}

const rowOf = (text: string) => screen.getByText(text).closest("tr") as HTMLElement;

test("목록 열: 배치 링크·작업·대상 스토리지·주기·다음 실행(KST)·마지막 결과·소유", async () => {
  serve([sched()]);
  renderPage();
  const link = await screen.findByRole("link", { name: "정기 스캔" });
  expect(link).toHaveAttribute("href", `/admin/batches/${ID1}`);
  const tr = rowOf("정기 스캔");
  expect(within(tr).getByText("scan")).toBeInTheDocument();
  expect(within(tr).getByText("ceph")).toBeInTheDocument();
  expect(within(tr).getByText("매일 02:00 (KST)")).toBeInTheDocument();
  // UTC 17:00 = KST 다음날 02:00 — 날짜 / 시각·남은 시간 두 줄(회차는 분 단위라 초를 던다)
  expect(within(tr).getByText("2026-11-17")).toBeInTheDocument();
  expect(within(tr).getByText(/^02:00 · /)).toBeInTheDocument();
  expect(within(tr).getByText("아직 없음")).toBeInTheDocument();                  // null ≠ 실패
  expect(within(tr).getByText("root-admin")).toHaveAttribute("title", "만든 사람 maker");
  // 머리줄 순서(열이 빠지거나 뒤바뀌면 문다)
  expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(
    ["배치", "작업", "대상 스토리지", "주기", "다음 실행(KST)", "마지막 결과", "사용", "소유", "관리"]);
});

test("sync 는 src → dst, 자동 실행 표식, 이름 없는 배치는 id 12자", async () => {
  serve([sched({ batch: { name: null, operation: "sync", status: "Completed", item_count: 1, auto_confirm: true,
                          source_storage: "src-a", destination_storage: "dst-b" } })]);
  renderPage();
  const link = await screen.findByRole("link", { name: ID1.slice(0, 12) });
  const tr = link.closest("tr") as HTMLElement;
  expect(within(tr).getByText("src-a → dst-b")).toBeInTheDocument();
  expect(within(tr).getByText("자동")).toBeInTheDocument();
});

test("꺼진 스케줄의 다음 실행은 —, 자동 일시정지는 사유와 함께 보인다", async () => {
  serve([sched({ enabled: 0, next_run_at: null, paused_reason_code: "batch_schedule_owner_not_privileged",
                 last_outcome: "paused", last_reason_code: "batch_schedule_owner_not_privileged",
                 last_run_at: "2026-11-15T17:00:00Z" })]);
  renderPage();
  const tr = (await screen.findByRole("link", { name: "정기 스캔" })).closest("tr") as HTMLElement;
  expect(within(tr).getByText("—")).toBeInTheDocument();
  expect(within(tr).getByText("자동 일시정지")).toBeInTheDocument();
  expect(within(tr).getByText("일시정지")).toBeInTheDocument();
  expect(within(tr).getByText(reasonText("batch_schedule_owner_not_privileged"))).toBeInTheDocument();
  expect(within(tr).getByText("2026-11-16 02:00:00")).toBeInTheDocument();
  expect(within(tr).getByRole("checkbox", { name: "정기 스캔 사용" })).not.toBeChecked();
});

test("다음 실행 값이 깨졌으면 원문 그대로(시각을 지어내지 않는다)", async () => {
  serve([sched({ next_run_at: "garbage" })]);
  renderPage();
  const tr = (await screen.findByRole("link", { name: "정기 스캔" })).closest("tr") as HTMLElement;
  expect(within(tr).getByText("garbage")).toBeInTheDocument();
  expect(within(tr).getAllByText("—").length).toBeGreaterThan(0);              // untilText 도 모름
});

test("배치 행이 없는 스케줄은 「기록 없음 id12」(링크 없음)", async () => {
  serve([sched({ batch: null })]);
  renderPage();
  expect(await screen.findByText(`기록 없음 ${ID1.slice(0, 12)}`)).toBeInTheDocument();
  expect(screen.queryByRole("link")).toBeNull();
});

test("조회 실패는 빈 상태로 접지 않는다", async () => {
  serve([]);
  server.use(http.get("/api/admin/batch-schedules", () => HttpResponse.json({ detail: "batch_not_found" }, { status: 500 })));
  renderPage();
  expect(await screen.findByText(reasonText("batch_not_found"))).toBeInTheDocument();
  expect(screen.queryByText(/스케줄이 없습니다/)).toBeNull();
});

test("빈 목록은 안내 문구(헤더만 남은 표 없음)", async () => {
  serve([]);
  renderPage();
  expect(await screen.findByText(/스케줄이 없습니다/)).toBeInTheDocument();
  expect(screen.queryByRole("table")).toBeNull();
});

test("사용 끄기 → :disable, 서버 재조회 뒤 다음 실행이 — 가 된다", async () => {
  const calls = serve([sched()]);
  renderPage();
  const box = await screen.findByRole("checkbox", { name: "정기 스캔 사용" });
  expect(box).toBeChecked();
  await userEvent.click(box);
  await waitFor(() => expect(calls).toEqual([{ method: "POST", path: "s1:disable", body: {} }]));
  await waitFor(() => expect(screen.getByRole("checkbox", { name: "정기 스캔 사용" })).not.toBeChecked());
  expect(within(rowOf("정기 스캔")).getByText("—")).toBeInTheDocument();
});

test("비파괴 스케줄 켜기 → :enable(동의 없이)", async () => {
  const calls = serve([sched({ enabled: 0, next_run_at: null })]);
  renderPage();
  await userEvent.click(await screen.findByRole("checkbox", { name: "정기 스캔 사용" }));
  await waitFor(() => expect(calls).toEqual([{ method: "POST", path: "s1:enable", body: {} }]));
  expect(screen.queryByRole("note", { name: "반복 삭제 경고" })).toBeNull();
});

test("rm 스케줄 켜기는 동의 창을 거친다 — 체크 전엔 켜기 잠금, 동의했을 때만 acknowledge_irreversible:true", async () => {
  const calls = serve([sched({ enabled: 0, next_run_at: null,
    batch: { name: "정리", operation: "rm", status: "Completed", item_count: 2, storage: "ceph" } })]);
  renderPage();
  await userEvent.click(await screen.findByRole("checkbox", { name: "정리 사용" }));
  const dlg = await screen.findByRole("dialog", { name: "스케줄 켜기" });
  expect(within(dlg).getByRole("note", { name: "반복 삭제 경고" })).toBeInTheDocument();
  const on = within(dlg).getByRole("button", { name: "켜기" });
  expect(on).toBeDisabled();
  expect(calls).toEqual([]);                                   // 창만 열었다 — 아직 아무것도 안 쐈다
  await userEvent.click(within(dlg).getByRole("checkbox"));
  await userEvent.click(on);
  await waitFor(() => expect(calls).toEqual([{ method: "POST", path: "s1:enable", body: { acknowledge_irreversible: true } }]));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "스케줄 켜기" })).toBeNull());
});

test("동의 창은 다시 열면 동의가 풀려 있다", async () => {
  serve([sched({ enabled: 0, next_run_at: null,
    batch: { name: "정리", operation: "rm", status: "Completed", item_count: 2 } })]);
  renderPage();
  await userEvent.click(await screen.findByRole("checkbox", { name: "정리 사용" }));
  let dlg = await screen.findByRole("dialog", { name: "스케줄 켜기" });
  await userEvent.click(within(dlg).getByRole("checkbox"));
  await userEvent.click(within(dlg).getByRole("button", { name: "취소" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "스케줄 켜기" })).toBeNull());
  await userEvent.click(screen.getByRole("checkbox", { name: "정리 사용" }));
  dlg = await screen.findByRole("dialog", { name: "스케줄 켜기" });
  expect(within(dlg).getByRole("checkbox")).not.toBeChecked();
});

test("동의 창을 닫으면(취소·켜기) 포커스가 연 행의 「사용」 체크박스로 돌아온다 — <body> 로 떨어지지 않는다", async () => {
  // 트리거가 포커스를 받을 수 없는 자리표시 span 이라 Radix 의 기본 복귀가 <body> 였다(2026-11-16 리뷰). 같은 이름의 행이
  // 둘이어도 연 행으로 돌아간다(id 로 찾는다).
  const rmBatch = { name: "정리", operation: "rm", status: "Completed", item_count: 2, storage: "ceph" };
  serve([sched({ schedule_id: "s1", enabled: 0, next_run_at: null, batch: rmBatch }),
         sched({ schedule_id: "s2", batch_id: ID2, enabled: 0, next_run_at: null, batch: rmBatch })]);
  renderPage();
  await screen.findAllByRole("checkbox", { name: "정리 사용" });
  const second = () => screen.getAllByRole("checkbox", { name: "정리 사용" })[1];
  await userEvent.click(second());
  let dlg = await screen.findByRole("dialog", { name: "스케줄 켜기" });
  await userEvent.click(within(dlg).getByRole("button", { name: "취소" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "스케줄 켜기" })).toBeNull());
  await waitFor(() => expect(second()).toHaveFocus());
  await userEvent.click(second());
  dlg = await screen.findByRole("dialog", { name: "스케줄 켜기" });
  await userEvent.click(within(dlg).getByRole("checkbox"));
  await userEvent.click(within(dlg).getByRole("button", { name: "켜기" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "스케줄 켜기" })).toBeNull());
  await waitFor(() => expect(second()).toHaveFocus());
  expect(document.body).not.toHaveFocus();
});

test("서버 판정은 행의 최상위 destructive — 옵션을 몰라도 그 판정으로 동의 창을 먼저 연다", async () => {
  // 실서버 응답 모양(routes_batch_schedules._out): destructive 는 batch 요약 안이 아니라 행의 최상위에 실린다.
  const calls = serve([sched({ enabled: 0, next_run_at: null, destructive: true,
    batch: { name: "미러", operation: "sync", status: "Completed", item_count: 1, options: null } })]);
  renderPage();
  await userEvent.click(await screen.findByRole("checkbox", { name: "미러 사용" }));
  const dlg = await screen.findByRole("dialog", { name: "스케줄 켜기" });
  expect(within(dlg).getByRole("note", { name: "반복 삭제 경고" })).toBeInTheDocument();
  expect(calls).toEqual([]);                                   // 동의 없는 :enable 을 먼저 쏘지 않았다
});

test("화면이 파괴성을 몰랐어도 서버가 동의를 요구하면(422 batch_schedule_ack_required) 동의 창으로 넘긴다", async () => {
  serve([sched({ enabled: 0, next_run_at: null,
    batch: { name: "미러", operation: "sync", status: "Completed", item_count: 1 } })], {
    toggle: (_id, verb, body) => (verb === "enable" && (body as Record<string, unknown>).acknowledge_irreversible !== true
      ? HttpResponse.json({ detail: "batch_schedule_ack_required" }, { status: 422 }) : undefined),
  });
  renderPage();
  await userEvent.click(await screen.findByRole("checkbox", { name: "미러 사용" }));
  expect(await screen.findByRole("dialog", { name: "스케줄 켜기" })).toBeInTheDocument();
  expect(screen.queryByRole("alert")).toBeNull();               // 실패 문구가 아니라 동의 창
});

test("토글 실패는 어느 스케줄의 무엇이 왜 실패했는지 말한다", async () => {
  serve([sched({ enabled: 0, next_run_at: null })], {
    toggle: () => HttpResponse.json({ detail: "privileged_not_authorized" }, { status: 403 }),
  });
  renderPage();
  await userEvent.click(await screen.findByRole("checkbox", { name: "정기 스캔 사용" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(`정기 스캔 켜기 실패: ${reasonText("privileged_not_authorized")}`);
  expect(screen.getByRole("checkbox", { name: "정기 스캔 사용" })).not.toBeChecked();   // 서버 진실로 돌아온다
});

test("삭제는 2단 확인 — 1단은 무장만, 2단이 DELETE", async () => {
  const calls = serve([sched()]);
  renderPage();
  await userEvent.click(await screen.findByRole("button", { name: "정기 스캔 삭제" }));
  expect(calls).toEqual([]);
  await userEvent.click(screen.getByRole("button", { name: "정기 스캔 삭제 확인" }));
  await waitFor(() => expect(calls).toEqual([{ method: "DELETE", path: "s1", body: null }]));
  expect(await screen.findByText(/스케줄이 없습니다/)).toBeInTheDocument();
});

test("root 자격이 없는 관리자: 추가·수정·켜기는 잠기고 끄기·삭제·기록은 된다", async () => {
  serve([sched({ schedule_id: "s1" }),
         sched({ schedule_id: "s2", batch_id: ID2, enabled: 0, next_run_at: null,
                 batch: { name: "꺼진 것", operation: "scan", status: "Completed", item_count: 1 } })],
        { me: { actor: "plain-admin", role: "admin", can_run_as_root: false } });
  renderPage();
  await screen.findByRole("link", { name: "정기 스캔" });
  await waitFor(() => expect(screen.getByRole("button", { name: "스케줄 추가" })).toBeDisabled());
  expect(screen.getByRole("button", { name: "정기 스캔 수정" })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: "꺼진 것 사용" })).toBeDisabled();     // 켜기 = 특권 게이트
  expect(screen.getByRole("checkbox", { name: "정기 스캔 사용" })).toBeEnabled();    // 끄기 = 관리자면 된다
  expect(screen.getByRole("button", { name: "정기 스캔 삭제" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "정기 스캔 기록" })).toBeEnabled();
  expect(screen.getByText(/끄기·삭제는 모든 관리자가 할 수 있습니다/)).toBeInTheDocument();
});

test("실행 기록 창: 결과·사유·세부(0 은 「0개」, null 은 표시 안 함)", async () => {
  const run = (over: Partial<BatchScheduleRun>): BatchScheduleRun => ({
    run_id: 1, schedule_id: "s1", batch_id: ID1, scheduled_for: "2026-11-15T17:00:00Z", at: "2026-11-15T17:00:05Z",
    outcome: "fired", reason_code: null, batch_status: "Completed", requeued: 3,
    prev_status: "Completed", prev_item_count: 3, prev_succeeded: 2, prev_failed: 1, ...over });
  serve([sched()], { runs: [
    run({ run_id: 3 }),
    run({ run_id: 2, scheduled_for: "2026-11-14T17:00:00Z", outcome: "skipped", reason_code: "maintenance_mode",
          requeued: null, prev_status: null, prev_item_count: null, prev_succeeded: null, prev_failed: null,
          batch_status: "Running" }),
    run({ run_id: 1, scheduled_for: "2026-11-13T17:00:00Z", requeued: 0, prev_status: null, prev_item_count: null,
          prev_succeeded: null, prev_failed: null }),
  ] });
  renderPage();
  await userEvent.click(await screen.findByRole("button", { name: "정기 스캔 기록" }));
  const dlg = await screen.findByRole("dialog", { name: "실행 기록 — 정기 스캔" });
  await within(dlg).findByText("2026-11-16 02:00:00");
  expect(within(dlg).getByText("항목 3개 다시 실행 · 직전 회차 Completed · 성공 2 · 실패 1 · 전체 3")).toBeInTheDocument();
  expect(within(dlg).getByText("건너뜀")).toBeInTheDocument();
  expect(within(dlg).getByText(reasonText("maintenance_mode"))).toBeInTheDocument();
  expect(within(dlg).getByText("배치 상태 Running")).toBeInTheDocument();
  expect(within(dlg).getByText("항목 0개 다시 실행")).toBeInTheDocument();             // 0 은 정상값
});

test("실행 기록이 없으면 안내 문구, 조회 실패는 사유", async () => {
  serve([sched()], { runs: () => HttpResponse.json({ detail: "batch_schedule_not_found" }, { status: 404 }) });
  renderPage();
  await userEvent.click(await screen.findByRole("button", { name: "정기 스캔 기록" }));
  const dlg = await screen.findByRole("dialog");
  expect(await within(dlg).findByText(reasonText("batch_schedule_not_found"))).toBeInTheDocument();
  expect(within(dlg).queryByText("아직 판정한 회차가 없습니다")).toBeNull();
});

test("?batch=<id>(스케줄 없는 배치) → 그 배치를 고른 추가 창이 열리고 파라미터는 지워진다", async () => {
  serve([], { batches: [{ batch_id: ID2, operation: "scan", status: "Completed", max_concurrency: 1, item_count: 4,
                          succeeded_count: 0, failed_count: 0, note: null, created_at: "", name: "새 배치", options: {} }] });
  renderPage(`/admin/batch-schedules?batch=${ID2}`);
  const dlg = await screen.findByRole("dialog", { name: "스케줄 추가" });
  await waitFor(() => expect(within(dlg).getByRole("combobox", { name: "배치" })).toHaveValue(ID2));
  expect(screen.getByTestId("where")).toHaveTextContent(/^\/admin\/batch-schedules$/);
});

test("?batch=<id>(이미 스케줄 있음) → 그 스케줄의 수정 창", async () => {
  serve([sched()]);
  renderPage(`/admin/batch-schedules?batch=${ID1}`);
  const dlg = await screen.findByRole("dialog", { name: "스케줄 수정" });
  expect(within(dlg).getByRole("combobox", { name: "배치" })).toBeDisabled();
});

test("?batch= 도 자격이 없으면 창을 열지 않는다(버튼과 같은 잠금)", async () => {
  serve([], { me: { actor: "plain-admin", role: "admin", can_run_as_root: false } });
  renderPage(`/admin/batch-schedules?batch=${ID2}`);
  await screen.findByText(/스케줄이 없습니다/);
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/admin\/batch-schedules$/));
  expect(screen.queryByRole("dialog")).toBeNull();
});
