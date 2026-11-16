import { useState } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { BatchScheduleDialog } from "./BatchScheduleDialog";
import type { BatchSchedule } from "./batchSchedule";

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const SCAN = "bscan00000000aaa";
const RM = "brm0000000000bbb";
const SYNC = "bsync00000000ccc";
const SYNC_DEL = "bsyncdel00000ddd";
const AUTO = "bauto00000000ggg";
const OTHER = "bother0000000eee";
const TAKEN = "btaken0000000fff";

const batch = (id: string, over: object = {}) => ({
  batch_id: id, operation: "scan", status: "Completed", max_concurrency: 1, item_count: 3, succeeded_count: 0,
  failed_count: 0, note: null, created_at: "", name: `배치-${id.slice(0, 5)}`, options: {}, requester_id: "ops", ...over });

const BATCHES = [
  batch(SCAN, { storage: "ceph" }),
  batch(RM, { operation: "rm", options: { recursive: true }, storage: "ceph" }),
  batch(SYNC, { operation: "sync", source_storage: "a", destination_storage: "b", item_count: 5 }),
  batch(SYNC_DEL, { operation: "sync", options: { delete: true }, item_count: 0 }),
  batch(AUTO, { operation: "sync", auto_confirm: true, auto_confirm_by: "lee" }),
  batch(OTHER, { operation: "chmod" }),
  batch(TAKEN),
];
const RUNS = ["2026-11-16T17:00:00Z", "2026-11-17T17:00:00Z", "2026-11-18T17:00:00Z", "2026-11-19T17:00:00Z",
              "2026-11-20T17:00:00Z"];

interface Call { method: string; path: string; body: unknown; url?: string }
function serve(opts: { create?: () => Response; preview?: (body: Record<string, unknown>) => Response;
                       batches?: object[] } = {}) {
  const calls: Call[] = [];
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "ops", role: "admin", can_run_as_root: true })),
    http.get("/api/admin/batches", ({ request }) => {
      calls.push({ method: "LIST", path: "", body: null, url: request.url });
      return HttpResponse.json(opts.batches ?? BATCHES);
    }),
    http.post(/\/api\/admin\/batch-schedules:preview$/, async ({ request }) => {
      const body = await request.json() as Record<string, unknown>;
      calls.push({ method: "PREVIEW", path: "", body });
      return opts.preview ? opts.preview(body) : HttpResponse.json({ next_runs: RUNS, confirm_wait: null });
    }),
    http.post("/api/admin/batch-schedules", async ({ request }) => {
      calls.push({ method: "POST", path: "", body: await request.json() });
      return opts.create ? opts.create() : HttpResponse.json({ schedule_id: "s-new" }, { status: 201 });
    }),
    http.patch("/api/admin/batch-schedules/:id", async ({ request, params }) => {
      calls.push({ method: "PATCH", path: String(params.id), body: await request.json() });
      return HttpResponse.json({ schedule_id: params.id });
    }),
  );
  return calls;
}

// 창의 열림은 호출측이 쥔다(목록 화면) — 테스트도 같은 모양의 작은 껍데기로 연다.
function Harness({ schedule, initialBatchId }: { schedule?: BatchSchedule; initialBatchId?: string }) {
  const [open, setOpen] = useState(false);
  return <BatchScheduleDialog open={open} onOpenChange={setOpen} schedule={schedule} initialBatchId={initialBatchId}
                              scheduledBatchIds={new Set([TAKEN])} trigger={<button type="button">열기</button>} />;
}
async function openDialog(props: { schedule?: BatchSchedule; initialBatchId?: string } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><Harness {...props} /></QueryClientProvider>);
  await userEvent.click(screen.getByRole("button", { name: "열기" }));
  return screen.findByRole("dialog");
}
async function pick(dlg: HTMLElement, id: string) {
  const sel = within(dlg).getByRole("combobox", { name: "배치" });
  await waitFor(() => expect(within(sel).getAllByRole("option").length).toBeGreaterThan(1));
  await userEvent.selectOptions(sel, id);
}
const posts = (calls: Call[]) => calls.filter((c) => c.method === "POST" || c.method === "PATCH");

test("배치 선택기는 최근 101건을 읽는다(배치 목록 화면과 같은 창)", async () => {
  const calls = serve();
  const dlg = await openDialog();
  await pick(dlg, SCAN);
  expect(calls.find((c) => c.method === "LIST")?.url).toContain("limit=101");
});

test("매주 스케줄 추가: 요일 정렬·중복 없음, 동의 키 없음, 저장하면 창이 닫힌다", async () => {
  const calls = serve();
  const dlg = await openDialog();
  await pick(dlg, SCAN);
  await userEvent.click(within(dlg).getByRole("button", { name: "매주" }));
  expect(within(dlg).getByRole("checkbox", { name: "월" })).toBeChecked();     // 기본 월요일
  await userEvent.click(within(dlg).getByRole("checkbox", { name: "금" }));
  await userEvent.click(within(dlg).getByRole("checkbox", { name: "수" }));
  fireEvent.change(within(dlg).getByLabelText("실행 시각(KST)"), { target: { value: "03:15" } });
  expect(within(dlg).getByText("매주 월·수·금 03:15 (KST)")).toBeInTheDocument();
  await userEvent.click(within(dlg).getByRole("button", { name: "추가" }));
  await waitFor(() => expect(posts(calls)).toEqual([{ method: "POST", path: "", body: {
    batch_id: SCAN, kind: "weekly", spec: { weekdays: [1, 3, 5], hour: 3, minute: 15 }, enabled: true } }]));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

test("요일을 모두 끄면 오류와 함께 저장 잠금, 시각이 비면 잠금", async () => {
  serve();
  const dlg = await openDialog();
  await pick(dlg, SCAN);
  const add = within(dlg).getByRole("button", { name: "추가" });
  expect(add).toBeEnabled();
  await userEvent.click(within(dlg).getByRole("button", { name: "매주" }));
  await userEvent.click(within(dlg).getByRole("checkbox", { name: "월" }));
  expect(within(dlg).getByText("요일을 하나 이상 고르세요")).toBeInTheDocument();
  expect(add).toBeDisabled();
  await userEvent.click(within(dlg).getByRole("button", { name: "매일" }));     // 매일엔 요일을 안 본다
  expect(add).toBeEnabled();
  fireEvent.change(within(dlg).getByLabelText("실행 시각(KST)"), { target: { value: "" } });
  expect(within(dlg).getByText(/HH:MM/)).toBeInTheDocument();
  expect(add).toBeDisabled();
});

test("rm 배치는 「되돌릴 수 없음」 체크 전까지 저장 비활성, 체크하면 명시 true 를 싣는다", async () => {
  const calls = serve();
  const dlg = await openDialog();
  await pick(dlg, RM);
  const add = within(dlg).getByRole("button", { name: "추가" });
  expect(within(dlg).getByRole("note", { name: "반복 삭제 경고" })).toHaveTextContent("대상 전체를 root 권한으로 다시 삭제");
  expect(add).toBeDisabled();
  await userEvent.click(within(dlg).getByRole("checkbox", { name: /되돌릴 수 없는 삭제/ }));
  expect(add).toBeEnabled();
  await userEvent.click(add);
  await waitFor(() => expect(posts(calls)[0]?.body).toMatchObject({ batch_id: RM, acknowledge_irreversible: true }));
});

test("delete 를 켠 sync 도 파괴적 — 배치를 바꾸면 동의가 풀린다", async () => {
  serve();
  const dlg = await openDialog();
  await pick(dlg, SYNC_DEL);
  await userEvent.click(within(dlg).getByRole("checkbox", { name: /되돌릴 수 없는 삭제/ }));
  await pick(dlg, RM);
  expect(within(dlg).getByRole("checkbox", { name: /되돌릴 수 없는 삭제/ })).not.toBeChecked();
  expect(within(dlg).getByRole("button", { name: "추가" })).toBeDisabled();
});

test("미리보기는 서버 :preview 를 디바운스로 부르고 배치를 함께 싣는다 — 화면은 시각을 계산하지 않는다", async () => {
  const calls = serve();
  const dlg = await openDialog();
  await pick(dlg, SCAN);
  await waitFor(() => expect(within(dlg).getByText("2026-11-17 02:00:00 KST")).toBeInTheDocument());
  const previews = calls.filter((c) => c.method === "PREVIEW");
  expect(previews[previews.length - 1]?.body).toEqual({ kind: "daily", spec: { hour: 2, minute: 0 }, batch_id: SCAN });
});

test("자동 확인 꺼진 sync 의 짧은 주기: 서버 confirm_wait 경고(사실상 격일)를 그대로 보인다", async () => {
  serve({ preview: (body) => HttpResponse.json({ next_runs: RUNS, confirm_wait: body.batch_id === SYNC
    ? { period_seconds: 86400, preview_ttl_seconds: 86400, unconfirmed_every_seconds: 172800 } : null }) });
  const dlg = await openDialog();
  await pick(dlg, SYNC);
  const note = await within(dlg).findByRole("note", { name: "확인 대기 경고" });
  expect(note).toHaveTextContent("2일에 한 번만 미리보기를 만듭니다");
  expect(within(dlg).getByText(/사람의 「배치 확인」을 기다립니다/)).toBeInTheDocument();
  // 경고는 안내일 뿐 — 저장은 막지 않는다(판정은 서버가 건너뜀으로 기록)
  expect(within(dlg).getByRole("button", { name: "추가" })).toBeEnabled();
});

test("회차 보존 안내: 서버 keep_rounds·prune_preview 로 「곧 정리될 작업」과 「항목 × K > 상한」 예측을 보인다 — 저장은 막지 않는다", async () => {
  // 2026-11-16 사용자 결정(최근 5회차만 보존). prune_preview 는 실제 정리와 같은 경계 계산(서버 _round_cut)이다.
  serve({ preview: (body) => HttpResponse.json({
    next_runs: RUNS, confirm_wait: null, keep_rounds: 5, child_count_limit: 10,
    ...(body.batch_id ? { prune_preview: { rounds_recorded: 6, would_prune: body.batch_id === SCAN ? 8 : 0 } } : {}) }) });
  const dlg = await openDialog();
  await pick(dlg, SCAN);
  const note = await within(dlg).findByRole("note", { name: "회차 보존 안내" });
  await waitFor(() => expect(note).toHaveTextContent("저장하면 기록된 회차 6개 중 오래된 회차의 작업 약 8개가 곧 정리됩니다"));
  expect(note).toHaveTextContent("스케줄이 있는 배치는 최근 5회차만 보존합니다");
  // 항목 3 × 5 = 15 > 상한 10(테스트용 작은 상한)
  expect(note).toHaveTextContent("항목 3개 × 5회차 = 작업 최대 15개 — 정리 뒤에도 배치 단위 삭제 상한(10개)을 넘어");
  expect(within(dlg).getByText(/회차마다 자식 요청 3개가 새로 생깁니다\(최근 5회차만 보존 — 오래된 회차는 자동으로 정리합니다\)/))
    .toBeInTheDocument();
  expect(within(dlg).getByRole("button", { name: "추가" })).toBeEnabled();
});

test("옛 서버(keep_rounds 없음)면 보존 안내를 지어내지 않는다", async () => {
  serve();
  const dlg = await openDialog();
  await pick(dlg, SCAN);
  await waitFor(() => expect(within(dlg).getByText("2026-11-17 02:00:00 KST")).toBeInTheDocument());
  expect(within(dlg).queryByRole("note", { name: "회차 보존 안내" })).toBeNull();
  expect(within(dlg).getByText("회차마다 자식 요청 3개가 새로 생깁니다.")).toBeInTheDocument();
});

test("자동 확인 배치는 켠 사람 둘의 자격을 각각 다시 본다고 말한다", async () => {
  serve();
  const dlg = await openDialog();
  await pick(dlg, AUTO);
  expect(await within(dlg).findByText(/자동 확인을 켠 사람: lee · 스케줄을 켠 사람: ops/)).toBeInTheDocument();
  expect(within(dlg).getByText(/배치를 만든 사람\(ops\)의 root 실행 자격/)).toBeInTheDocument();
});

test("스케줄 있는 배치·반복할 수 없는 연산은 고를 수 없다(선택지 잠금)", async () => {
  serve();
  const dlg = await openDialog();
  const sel = within(dlg).getByRole("combobox", { name: "배치" });
  await waitFor(() => expect(within(sel).getAllByRole("option").length).toBeGreaterThan(1));
  const opt = (id: string) => within(sel).getAllByRole("option").find((o) => (o as HTMLOptionElement).value === id)!;
  expect(opt(TAKEN)).toBeDisabled();
  expect(opt(TAKEN).textContent).toContain("(스케줄 있음)");
  expect(opt(OTHER)).toBeDisabled();
  expect(opt(SCAN)).toBeEnabled();
});

test("항목 0개 배치는 회차가 건너뛰어진다고 미리 말한다(0 은 정상값)", async () => {
  serve();
  const dlg = await openDialog();
  await pick(dlg, SYNC_DEL);
  expect(within(dlg).getByText(/항목이 없는 배치라 회차가 건너뛰어집니다/)).toBeInTheDocument();
});

test("서버 거부는 창 안에 문구로 남고 창은 열려 있다", async () => {
  serve({ create: () => HttpResponse.json({ detail: "batch_schedule_requester_not_privileged" }, { status: 422 }) });
  const dlg = await openDialog();
  await pick(dlg, SCAN);
  await userEvent.click(within(dlg).getByRole("button", { name: "추가" }));
  expect(await within(dlg).findByRole("alert")).toBeInTheDocument();
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

const SCHEDULE: BatchSchedule = {
  schedule_id: "s1", batch_id: RM, kind: "monthly", spec: { day: 31, hour: 5, minute: 0 }, enabled: true,
  next_run_at: "2026-11-29T20:00:00Z", armed_by: "ops", created_by: "ops", created_at: "", updated_at: "",
  last_run_at: null, last_outcome: null, last_reason_code: null, paused_reason_code: null,
  acknowledged_irreversible: true, destructive: true,
  batch: { batch_id: RM, name: "정리", operation: "rm", status: "Completed", item_count: 2, options: { recursive: true },
           storage: "ceph", auto_confirm: false },
};

test("수정 창: 저장된 규칙으로 채우고, 바꾸지 않으면 「변경 없음」, 바꾸면 다시 동의해야 PATCH", async () => {
  const calls = serve();
  const dlg = await openDialog({ schedule: SCHEDULE });
  expect(within(dlg).getByRole("combobox", { name: "배치" })).toBeDisabled();
  expect(within(dlg).getByRole("combobox", { name: "날짜" })).toHaveValue("31");
  expect(within(dlg).getByText("29~31일은 그 날이 없는 달에는 그 달 말일에 실행합니다.")).toBeInTheDocument();
  expect(within(dlg).getByRole("button", { name: "변경 없음" })).toBeDisabled();
  await userEvent.selectOptions(within(dlg).getByRole("combobox", { name: "날짜" }), "15");
  const save = within(dlg).getByRole("button", { name: "저장" });
  expect(save).toBeDisabled();                                    // 파괴적 — 다시 동의
  await userEvent.click(within(dlg).getByRole("checkbox", { name: /되돌릴 수 없는 삭제/ }));
  await userEvent.click(save);
  await waitFor(() => expect(posts(calls)).toEqual([{ method: "PATCH", path: "s1", body: {
    kind: "monthly", spec: { day: 15, hour: 5, minute: 0 }, acknowledge_irreversible: true } }]));
});
