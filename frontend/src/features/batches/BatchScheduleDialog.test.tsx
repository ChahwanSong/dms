import { useState } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { BatchScheduleDialog } from "./BatchScheduleDialog";
import { REASON_MESSAGES, reasonText } from "../../lib/api";
import type { BatchSchedule } from "../../lib/types";

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const SCAN = "bscan00000000aaa";
const RM = "brm0000000000bbb";
const SYNC = "bsync00000000ccc";
const SYNC_DEL = "bsyncdel00000ddd";
const OTHER = "bother0000000eee";
const TAKEN = "btaken0000000fff";

const batch = (id: string, over: object = {}) => ({
  batch_id: id, operation: "scan", status: "Completed", max_concurrency: 1, item_count: 3, succeeded_count: 0,
  failed_count: 0, note: null, created_at: "", name: `배치-${id.slice(0, 5)}`, options: {}, ...over });

const BATCHES = [
  batch(SCAN, { storage: "ceph" }),
  batch(RM, { operation: "rm", options: { recursive: true }, storage: "ceph" }),
  batch(SYNC, { operation: "sync", source_storage: "a", destination_storage: "b", item_count: 5 }),
  batch(SYNC_DEL, { operation: "sync", options: { delete: true }, auto_confirm: true, item_count: 0 }),
  batch(OTHER, { operation: "chmod" }),
  batch(TAKEN),
];

interface Call { method: string; path: string; body: unknown }
const RUNS = ["2026-11-16T17:00:00Z", "2026-11-17T17:00:00Z", "2026-11-18T17:00:00Z", "2026-11-19T17:00:00Z",
              "2026-11-20T17:00:00Z"];
function serve(opts: {
  create?: () => Response; preview?: (body: Record<string, unknown>) => Response; batches?: object[];
} = {}) {
  const calls: Call[] = [];
  server.use(
    http.get("/api/admin/batches", () => HttpResponse.json(opts.batches ?? BATCHES)),
    http.get("/api/admin/batch-schedules", () => HttpResponse.json([])),
    http.post(/\/api\/admin\/batch-schedules:preview$/, async ({ request }) => {
      const body = await request.json() as Record<string, unknown>;
      calls.push({ method: "PREVIEW", path: "", body });
      return opts.preview ? opts.preview(body) : HttpResponse.json({ next_runs: RUNS });
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

test("배치를 고르기 전엔 저장 잠금 — 반복 불가 연산·이미 스케줄 있는 배치는 고를 수 없다", async () => {
  serve();
  const dlg = await openDialog();
  const sel = within(dlg).getByRole("combobox", { name: "배치" });
  await waitFor(() => expect(within(sel).getAllByRole("option").length).toBe(BATCHES.length + 1));
  expect(within(dlg).getByRole("button", { name: "추가" })).toBeDisabled();
  const opt = (id: string) => within(sel).getAllByRole("option").find((o) => (o as HTMLOptionElement).value === id)!;
  expect(opt(OTHER)).toBeDisabled();
  expect(opt(OTHER)).toHaveTextContent("(반복 실행할 수 없는 연산)");
  expect(opt(TAKEN)).toBeDisabled();
  expect(opt(TAKEN)).toHaveTextContent("(스케줄 있음)");
  expect(opt(SCAN)).toBeEnabled();
  // 옵션 문구 = 이름 · 연산 · 스토리지 · 상태(sync 는 src → dst)
  expect(opt(SYNC)).toHaveTextContent("배치-bsync · sync · a → b · Completed");
});

test("rm 배치: 빨간 경고 + 동의 체크 전엔 저장 잠금, 동의하면 acknowledge_irreversible:true 를 싣는다", async () => {
  const calls = serve();
  const dlg = await openDialog();
  await pick(dlg, RM);
  const note = within(dlg).getByRole("note", { name: "반복 삭제 경고" });
  expect(note).toHaveTextContent("대상 전체를 root 권한으로 다시 삭제");
  const add = within(dlg).getByRole("button", { name: "추가" });
  expect(add).toBeDisabled();
  await userEvent.click(within(note).getByRole("checkbox"));
  await userEvent.click(add);
  await waitFor(() => expect(posts(calls)).toEqual([{ method: "POST", path: "", body: {
    batch_id: RM, kind: "daily", spec: { hour: 2, minute: 0 }, enabled: true, acknowledge_irreversible: true } }]));
});

test("동의는 배치를 바꾸면 풀린다(다른 배치에 대한 동의가 아니다)", async () => {
  serve();
  const dlg = await openDialog();
  await pick(dlg, RM);
  await userEvent.click(within(within(dlg).getByRole("note", { name: "반복 삭제 경고" })).getByRole("checkbox"));
  await userEvent.selectOptions(within(dlg).getByRole("combobox", { name: "배치" }), SCAN);
  expect(within(dlg).queryByRole("note", { name: "반복 삭제 경고" })).toBeNull();
  await userEvent.selectOptions(within(dlg).getByRole("combobox", { name: "배치" }), RM);
  expect(within(within(dlg).getByRole("note", { name: "반복 삭제 경고" })).getByRole("checkbox")).not.toBeChecked();
});

test("동의는 창을 다시 열면 풀린다", async () => {
  serve();
  const dlg = await openDialog({ initialBatchId: RM });
  await waitFor(() => expect(within(dlg).getByRole("note", { name: "반복 삭제 경고" })).toBeInTheDocument());
  await userEvent.click(within(within(dlg).getByRole("note", { name: "반복 삭제 경고" })).getByRole("checkbox"));
  await userEvent.click(within(dlg).getByRole("button", { name: "닫기" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await userEvent.click(screen.getByRole("button", { name: "열기" }));
  const again = await screen.findByRole("dialog");
  await waitFor(() => expect(within(again).getByRole("note", { name: "반복 삭제 경고" })).toBeInTheDocument());
  expect(within(within(again).getByRole("note", { name: "반복 삭제 경고" })).getByRole("checkbox")).not.toBeChecked();
});

test("sync: delete 를 켠 배치만 경고, 확인 흐름 안내는 자동 실행 여부로 갈린다, 항목 0개는 건너뜀 안내", async () => {
  serve();
  const dlg = await openDialog();
  await pick(dlg, SYNC);
  expect(within(dlg).queryByRole("note", { name: "반복 삭제 경고" })).toBeNull();
  // 만료 시간은 사이트 설정(DMS_PREVIEW_TTL_SECONDS)이다 -- 고정 「24시간 뒤」를 약속하지 않는다(2026-11-16 리뷰).
  expect(within(dlg).getByText(/회차마다 사람이 확인해야 하며, 확인하지 않으면 미리보기 만료 시간\(사이트 설정, 기본 24시간\)이 지나/))
    .toBeInTheDocument();
  expect(within(dlg).queryByText(/24시간 뒤 만료/)).toBeNull();
  expect(REASON_MESSAGES.batch_schedule_awaiting_confirm).not.toMatch(/24시간 뒤/);
  expect(REASON_MESSAGES.batch_schedule_awaiting_confirm).toMatch(/미리보기 만료 시간\(사이트 설정, 기본 24시간\)/);
  expect(within(dlg).getByText("회차마다 자식 요청 5개가 새로 생깁니다.")).toBeInTheDocument();
  await userEvent.selectOptions(within(dlg).getByRole("combobox", { name: "배치" }), SYNC_DEL);
  expect(within(dlg).getByRole("note", { name: "반복 삭제 경고" })).toHaveTextContent("delete 옵션");
  expect(within(dlg).getByText(/사람 확인 없이 실행됩니다/)).toBeInTheDocument();
  expect(within(dlg).getByText(/항목이 없는 배치라 회차가 건너뛰어집니다/)).toBeInTheDocument();   // 0 은 정상값
});

test("scan 배치엔 확인 흐름 안내가 없다", async () => {
  serve();
  const dlg = await openDialog();
  await pick(dlg, SCAN);
  expect(within(dlg).getByText("회차마다 자식 요청 3개가 새로 생깁니다.")).toBeInTheDocument();
  expect(within(dlg).queryByText(/사람이 확인해야/)).toBeNull();
});

test("다음 5회 미리보기는 서버 :preview(디바운스) — KST 로 보인다", async () => {
  const calls = serve();
  const dlg = await openDialog();
  expect(await within(dlg).findByText("2026-11-17 02:00:00 KST")).toBeInTheDocument();
  expect(within(dlg).getByText("2026-11-21 02:00:00 KST")).toBeInTheDocument();
  const lastPreview = () => calls.filter((c) => c.method === "PREVIEW").slice(-1)[0]?.body;
  expect(lastPreview()).toEqual({ kind: "daily", spec: { hour: 2, minute: 0 } });
  await userEvent.click(within(dlg).getByRole("button", { name: "N시간마다" }));
  await waitFor(() => expect(lastPreview())
    .toEqual({ kind: "interval", spec: { every_hours: 6, hour: 2, minute: 0 } }));
});

test("배치를 고르면 미리보기에 batch_id 를 싣는다(서버가 그 배치의 회차 보존 예측을 같이 계산) — 고르기 전엔 규칙만", async () => {
  const calls = serve();
  const dlg = await openDialog();
  const lastPreview = () => calls.filter((c) => c.method === "PREVIEW").slice(-1)[0]?.body;
  await waitFor(() => expect(lastPreview()).toEqual({ kind: "daily", spec: { hour: 2, minute: 0 } }));
  await pick(dlg, SCAN);
  await waitFor(() => expect(lastPreview()).toEqual({ kind: "daily", spec: { hour: 2, minute: 0 }, batch_id: SCAN }));
});

test("회차 보존 안내: 서버 keep_rounds·prune_preview 로 「곧 정리될 작업」과 「항목 × K > 상한」 예측을 보인다 — 저장은 막지 않는다", async () => {
  // 2026-11-16 사용자 결정(최근 10회차만 누적). prune_preview 는 실제 정리와 같은 경계 계산(서버 _round_cut)이다. 상한은 테스트용
  // 작은 값(20) — 화면은 상수를 두지 않고 서버 child_count_limit 을 그대로 쓴다.
  serve({ preview: (body) => HttpResponse.json({
    next_runs: RUNS, keep_rounds: 10, child_count_limit: 20,
    ...(body.batch_id ? { prune_preview: { rounds_recorded: 11, would_prune: body.batch_id === SCAN ? 8 : 0 } } : {}) }) });
  const dlg = await openDialog();
  await pick(dlg, SCAN);
  const note = await within(dlg).findByRole("note", { name: "회차 보존 안내" });
  await waitFor(() => expect(note).toHaveTextContent("저장하면 기록된 회차 11개 중 오래된 회차의 작업 약 8개가 곧 정리됩니다"));
  expect(note).toHaveTextContent("스케줄이 있는 배치는 최근 10회차만 보존합니다");
  // 항목 3 × 10 = 30 > 상한 20
  expect(note).toHaveTextContent("항목 3개 × 10회차 = 작업 최대 30개 — 정리 뒤에도 배치 단위 삭제 상한(20개)을 넘어");
  expect(note.className).toContain("break-keep");
  expect(within(dlg).getByText("회차마다 자식 요청 3개가 새로 생깁니다(최근 10회차만 보존 — 오래된 회차는 자동으로 정리합니다)."))
    .toBeInTheDocument();
  expect(within(dlg).getByRole("button", { name: "추가" })).toBeEnabled();
  // 다른 배치로 바꾸면 그 배치의 예측(0 — 문구 없음)으로 바뀐다. 항목 5 × 10 = 50 > 20 은 그대로 경고
  await userEvent.selectOptions(within(dlg).getByRole("combobox", { name: "배치" }), SYNC);
  await waitFor(() => expect(within(dlg).getByRole("note", { name: "회차 보존 안내" })).not.toHaveTextContent(/곧 정리됩니다/));
  expect(within(dlg).getByRole("note", { name: "회차 보존 안내" })).toHaveTextContent("항목 5개 × 10회차 = 작업 최대 50개");
});

test("배치를 바꾼 뒤 새 미리보기가 오기 전엔 직전 배치의 「곧 정리될 작업 수」를 보이지 않는다(자리표시 데이터)", async () => {
  // keepPreviousData 라 키가 바뀌어도 직전 응답이 남는다 — 보존 수·상한(서버 설정)은 그대로 써도 되지만 정리 예측은 배치마다라
  // 지금 배치의 응답이 올 때까지 숨긴다(다른 배치의 수를 이 배치의 것처럼 말하지 않는다).
  let release: () => void = () => {};
  const gate = new Promise<void>((r) => { release = r; });
  serve({ preview: (body) => HttpResponse.json({
    next_runs: RUNS, keep_rounds: 10, child_count_limit: 1000,
    ...(body.batch_id ? { prune_preview: { rounds_recorded: 11, would_prune: body.batch_id === SCAN ? 8 : 4 } } : {}) }) });
  server.use(http.post(/\/api\/admin\/batch-schedules:preview$/, async ({ request }) => {
    // clone: 넘겨 줄 때 serve 의 처리기가 본문을 다시 읽는다
    const body = await request.clone().json() as Record<string, unknown>;
    if (body.batch_id !== SYNC) return undefined;               // 나머지는 serve 의 처리기로
    await gate;
    return HttpResponse.json({ next_runs: RUNS, keep_rounds: 10, child_count_limit: 1000,
                               prune_preview: { rounds_recorded: 11, would_prune: 4 } });
  }));
  const dlg = await openDialog();
  await pick(dlg, SCAN);
  const note = () => within(dlg).getByRole("note", { name: "회차 보존 안내" });
  await waitFor(() => expect(note()).toHaveTextContent("작업 약 8개가 곧 정리됩니다"));
  await userEvent.selectOptions(within(dlg).getByRole("combobox", { name: "배치" }), SYNC);
  await new Promise((r) => setTimeout(r, 500));                  // 디바운스(300ms)가 지나 SYNC 요청이 걸려 있는 동안
  expect(note()).not.toHaveTextContent(/곧 정리됩니다/);
  expect(note()).toHaveTextContent("스케줄이 있는 배치는 최근 10회차만 보존합니다");   // 서버 설정은 그대로
  release();
  await waitFor(() => expect(note()).toHaveTextContent("작업 약 4개가 곧 정리됩니다"));
});

test("항목 × K 가 상한 안이면 예측 경고는 없고 보존 안내만", async () => {
  serve({ preview: (body) => HttpResponse.json({
    next_runs: RUNS, keep_rounds: 10, child_count_limit: 1000,
    ...(body.batch_id ? { prune_preview: { rounds_recorded: 3, would_prune: 0 } } : {}) }) });
  const dlg = await openDialog();
  await pick(dlg, SCAN);
  const note = await within(dlg).findByRole("note", { name: "회차 보존 안내" });
  expect(note).toHaveTextContent("스케줄이 있는 배치는 최근 10회차만 보존합니다");
  expect(note).not.toHaveTextContent(/작업 최대/);
  expect(note).not.toHaveTextContent(/곧 정리됩니다/);
});

test("옛 서버(keep_rounds 없음)면 보존 안내를 지어내지 않는다", async () => {
  serve();
  const dlg = await openDialog();
  await pick(dlg, SCAN);
  await waitFor(() => expect(within(dlg).getByText("2026-11-17 02:00:00 KST")).toBeInTheDocument());
  expect(within(dlg).queryByRole("note", { name: "회차 보존 안내" })).toBeNull();
  expect(within(dlg).getByText("회차마다 자식 요청 3개가 새로 생깁니다.")).toBeInTheDocument();
});

test("미리보기 실패는 사유를 말한다(저장은 막지 않는다 — 최종 심판은 저장 응답)", async () => {
  serve({ preview: () => HttpResponse.json({ detail: "invalid_batch_schedule" }, { status: 422 }) });
  const dlg = await openDialog();
  expect(await within(dlg).findByText(`미리보기를 계산하지 못했습니다: ${reasonText("invalid_batch_schedule")}`))
    .toBeInTheDocument();
});

test("매월 29~31일은 말일 안내, N시간마다는 24 의 약수만", async () => {
  serve();
  const dlg = await openDialog();
  await userEvent.click(within(dlg).getByRole("button", { name: "매월" }));
  expect(within(dlg).queryByText(/그 달 말일에 실행/)).toBeNull();
  await userEvent.selectOptions(within(dlg).getByRole("combobox", { name: "날짜" }), "31");
  expect(within(dlg).getByText(/29~31일은 그 날이 없는 달에는 그 달 말일에 실행/)).toBeInTheDocument();
  expect(within(dlg).getByText("매월 31일 02:00 (KST, 없는 달은 말일)")).toBeInTheDocument();
  await userEvent.click(within(dlg).getByRole("button", { name: "N시간마다" }));
  const opts = within(within(dlg).getByRole("combobox", { name: "간격" })).getAllByRole("option");
  expect(opts.map((o) => (o as HTMLOptionElement).value)).toEqual(["1", "2", "3", "4", "6", "8", "12"]);
});

test("사용을 끄면 enabled:false 로 저장(꺼진 채 만들기)", async () => {
  const calls = serve();
  const dlg = await openDialog();
  await pick(dlg, SCAN);
  await userEvent.click(within(dlg).getByRole("checkbox", { name: /사용/ }));
  await userEvent.click(within(dlg).getByRole("button", { name: "추가" }));
  await waitFor(() => expect((posts(calls)[0]?.body as Record<string, unknown>)?.enabled).toBe(false));
});

test("서버 거부는 창 안에 사유로 보이고 창은 열린 채다", async () => {
  serve({ create: () => HttpResponse.json({ detail: "privileged_not_authorized" }, { status: 403 }) });
  const dlg = await openDialog();
  await pick(dlg, SCAN);
  await userEvent.click(within(dlg).getByRole("button", { name: "추가" }));
  expect(await within(dlg).findByRole("alert")).toHaveTextContent(reasonText("privileged_not_authorized"));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

test("?batch= 가 최근 100건 창 밖이면 단건 조회로 보충해 미리 고른다", async () => {
  const far = "bfar000000000ggg";
  serve({ batches: [batch(SCAN)] });
  let asked = "";
  server.use(http.get(`/api/admin/batches/${far}`, ({ request }) => {
    asked = new URL(request.url).search;
    return HttpResponse.json(batch(far, { name: "먼 배치" }));
  }));
  const dlg = await openDialog({ initialBatchId: far });
  const sel = within(dlg).getByRole("combobox", { name: "배치" });
  await waitFor(() => expect(sel).toHaveValue(far));
  expect(within(sel).getByRole("option", { name: /먼 배치/ })).toBeInTheDocument();
  expect(asked).toBe("?view=summary");                      // 항목 없는 가벼운 조회
  expect(within(dlg).getByRole("button", { name: "추가" })).toBeEnabled();
});

test("?batch= 의 배치를 보충 조회도 못 찾으면(지워진 배치의 낡은 링크) 저장이 잠긴 이유를 말한다", async () => {
  const gone = "bgone00000000hhh";
  serve({ batches: [batch(SCAN)] });
  server.use(http.get(`/api/admin/batches/${gone}`, () =>
    HttpResponse.json({ detail: "batch_not_found" }, { status: 404 })));
  const dlg = await openDialog({ initialBatchId: gone });
  expect(await within(dlg).findByText(new RegExp(`고른 배치를 불러오지 못했습니다: ${reasonText("batch_not_found")}`)))
    .toBeInTheDocument();
  expect(within(dlg).getByRole("button", { name: "추가" })).toBeDisabled();
});

const existing = (over: Partial<BatchSchedule> = {}): BatchSchedule => ({
  schedule_id: "s9", batch_id: SCAN, kind: "weekly", spec: { weekdays: [2, 4], hour: 5, minute: 0 }, enabled: 1,
  next_run_at: null, armed_by: "a", created_by: "a", created_at: "", updated_at: "",
  last_run_at: null, last_outcome: null, last_reason_code: null, paused_reason_code: null,
  batch: { name: "주간 스캔", operation: "scan", status: "Completed", item_count: 3 }, ...over });

test("수정: 저장된 값으로 채워지고 배치는 잠긴다 — 바뀐 게 없으면 「변경 없음」, 바꾸면 PATCH {kind, spec} 만", async () => {
  const calls = serve();
  const dlg = await openDialog({ schedule: existing() });
  expect(within(dlg).getByRole("combobox", { name: "배치" })).toBeDisabled();
  expect(within(dlg).getByRole("button", { name: "매주" })).toHaveAttribute("aria-pressed", "true");
  expect(within(dlg).getByRole("checkbox", { name: "화" })).toBeChecked();
  expect(within(dlg).getByRole("checkbox", { name: "목" })).toBeChecked();
  expect(within(dlg).getByLabelText("실행 시각(KST)")).toHaveValue("05:00");
  expect(within(dlg).getByRole("button", { name: "변경 없음" })).toBeDisabled();
  expect(within(dlg).queryByRole("checkbox", { name: /사용/ })).toBeNull();   // 켜기·끄기는 목록의 사용 칸
  await userEvent.click(within(dlg).getByRole("checkbox", { name: "토" }));
  await userEvent.click(within(dlg).getByRole("button", { name: "저장" }));
  await waitFor(() => expect(posts(calls)).toEqual([{ method: "PATCH", path: "s9", body: {
    kind: "weekly", spec: { weekdays: [2, 4, 6], hour: 5, minute: 0 } } }]));
});

test("수정: 파괴적 스케줄은 고칠 때도 동의가 필요하다(서버 판정 destructive 우선)", async () => {
  const calls = serve();
  // 요약의 옵션은 모르지만(null) 서버가 파괴적이라고 판정했다 — 화면은 서버 판정을 따른다.
  // 서버 응답 모양 그대로: destructive 는 행의 최상위 필드다(routes_batch_schedules._out).
  const dlg = await openDialog({ schedule: existing({ kind: "daily", spec: { hour: 1, minute: 0 }, destructive: true,
    batch: { name: "미러", operation: "sync", status: "Completed", item_count: 2, options: null } }) });
  fireEvent.change(within(dlg).getByLabelText("실행 시각(KST)"), { target: { value: "04:00" } });
  const save = within(dlg).getByRole("button", { name: "저장" });
  expect(save).toBeDisabled();
  await userEvent.click(within(within(dlg).getByRole("note", { name: "반복 삭제 경고" })).getByRole("checkbox"));
  await userEvent.click(save);
  await waitFor(() => expect(posts(calls)).toEqual([{ method: "PATCH", path: "s9", body: {
    kind: "daily", spec: { hour: 4, minute: 0 }, acknowledge_irreversible: true } }]));
});

test("수정: 항목 수를 모르면(null) 수를 지어내지 않는다 — 0 과 다르다", async () => {
  serve();
  const dlg = await openDialog({ schedule: existing({
    batch: { name: "주간 스캔", operation: "scan", status: "Completed", item_count: null } }) });
  expect(within(dlg).queryByText(/자식 요청/)).toBeNull();
  expect(within(dlg).queryByText(/항목이 없는 배치/)).toBeNull();
});

test("수정: 배치 기록이 없는 스케줄은 저장할 수 없다고 말한다", async () => {
  serve();
  const dlg = await openDialog({ schedule: existing({ batch: null }) });
  expect(within(dlg).getByText(/배치 기록이 없습니다/)).toBeInTheDocument();
  fireEvent.change(within(dlg).getByLabelText("실행 시각(KST)"), { target: { value: "04:00" } });
  expect(within(dlg).getByRole("button", { name: "저장" })).toBeDisabled();
});
