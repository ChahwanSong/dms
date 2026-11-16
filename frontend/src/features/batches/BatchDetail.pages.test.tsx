// 배치 상세의 쪽 조회(2026-11-16 -- 머리 ?view=summary · 항목 한 쪽 ?view=items · 확인 창 ?view=all · 현재 항목 CSV ?view=payloads).
// 기존 BatchDetail.test.tsx 의 fixture 는 view 를 무시하는 핸들러(= 옛 파드의 전체 응답)라 물러서기 경로를 잰다 -- 이 파일은 새
// 파드의 응답(view 마다 다른 모양)으로 쪽 넘김·검색·상태 칩·선택 범위·대화상자의 1회 조회를 고정한다.
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";
import { BatchDetail } from "./BatchDetail";
import type { BatchItem } from "../../lib/types";

const server = setupServer(
  http.get("/api/user/storages", () => HttpResponse.json([])),
  http.get("/api/user/requests/:rid/jobs", () => HttpResponse.json([])));
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const STATUSES = ["Queued", "Materialized", "Succeeded", "Failed", "Rejected", "Cancelled"];

interface World {
  head: Record<string, unknown>;
  items: BatchItem[];
  calls: string[];
}

// 새 파드 흉내: view 마다 서버 routes_batches.get_batch 와 같은 모양을 낸다(q 는 대소문자 무시 토큰 AND -- 경로만).
function serve(w: World) {
  server.use(http.get("/api/admin/batches/b1", ({ request }) => {
    const u = new URL(request.url);
    const view = u.searchParams.get("view");
    w.calls.push(`${view}${u.searchParams.has("offset") ? `@${u.searchParams.get("offset")}` : ""}`
                 + `${u.searchParams.get("q") ? `?q=${u.searchParams.get("q")}` : ""}`
                 + `${u.searchParams.get("status") ? `&status=${u.searchParams.get("status")}` : ""}`);
    if (view === "summary") {
      const counts: Record<string, number> = Object.fromEntries(STATUSES.map((s) => [s, 0]));
      for (const it of w.items) counts[it.status] = (counts[it.status] ?? 0) + 1;
      return HttpResponse.json({ ...w.head, item_total: w.items.length, status_counts: counts,
                                 first_payload: w.items[0]?.payload ?? null, executing_count: null, direct_tools: null });
    }
    if (view === "items") {
      const q = (u.searchParams.get("q") ?? "").toLowerCase().split(/\s+/).filter(Boolean);
      const st = u.searchParams.get("status");
      const off = Number(u.searchParams.get("offset") ?? 0), lim = Number(u.searchParams.get("limit") ?? 100);
      const hits = w.items.filter((it) => (st === null || it.status === st)
        && q.every((t) => String(it.payload.target ?? "").toLowerCase().includes(t)));
      return HttpResponse.json({ total: hits.length, offset: off, limit: lim, items: hits.slice(off, off + lim),
                                 filters: { q: u.searchParams.get("q"), status: st } });
    }
    if (view === "payloads") {
      return HttpResponse.json({ items: w.items.map((it) => ({ seq: it.seq, payload: it.payload, status: it.status })) });
    }
    return HttpResponse.json({ ...w.head, items: w.items });          // view=all · 없음
  }));
}

const scanItem = (seq: number, status = "Queued"): BatchItem => ({
  seq, payload: { storage: "s1", target: `proj/T${seq}` }, status, request_id: status === "Queued" ? null : `r${seq}`,
  reason_code: null });
const head = (over: Record<string, unknown> = {}) => ({
  batch_id: "b1", operation: "scan", status: "Completed", max_concurrency: 2, item_count: 0, succeeded_count: 0,
  failed_count: 0, note: null, created_at: "", options: {}, storage: "s1", ...over });

function Loc() {
  const l = useLocation();
  return <p data-testid="loc">{l.search}</p>;
}
function renderAt(w: World, path = "/admin/batches/b1") {
  serve(w);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const r = render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={[path]}>
    <Routes><Route path="/admin/batches/:batchId" element={<><BatchDetail /><Loc /></>} /></Routes>
  </MemoryRouter></QueryClientProvider>);
  return { ...r, qc };
}
const loc = () => screen.getByTestId("loc").textContent ?? "";

test("새 파드: 지금 쪽(100행)만 그리고 Pager 로 다음 쪽을 받는다 -- 쪽은 주소(?page=)", async () => {
  const w: World = { head: head(), items: Array.from({ length: 250 }, (_, i) => scanItem(i, "Succeeded")), calls: [] };
  renderAt(w);
  expect(await screen.findByRole("button", { name: "항목 0 상세" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "항목 99 상세" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "항목 100 상세" })).toBeNull();
  const pager = screen.getByRole("navigation", { name: "항목 쪽" });
  expect(pager).toHaveTextContent("1 / 3쪽");
  await userEvent.click(within(pager).getByRole("button", { name: "다음" }));
  expect(await screen.findByRole("button", { name: "항목 100 상세" })).toBeInTheDocument();
  expect(loc()).toBe("?page=2");
  expect(w.calls).toContain("items@100");
  // 머리는 항목을 싣지 않는 요약, 큰 조회(all·payloads)는 열지 않았으니 없다
  expect(w.calls.filter((c) => c === "all" || c === "payloads")).toEqual([]);
  expect(w.calls).toContain("summary");
});

test("주소의 쪽이 범위를 넘으면 마지막 쪽으로 당긴다(빈 쪽에 「결과 없음」을 보이지 않는다)", async () => {
  const w: World = { head: head(), items: Array.from({ length: 250 }, (_, i) => scanItem(i, "Succeeded")), calls: [] };
  renderAt(w, "/admin/batches/b1?page=9");
  await waitFor(() => expect(loc()).toBe("?page=3"));
  expect(await screen.findByRole("button", { name: "항목 249 상세" })).toBeInTheDocument();
  expect(screen.queryByText("검색 결과가 없습니다")).toBeNull();
});

test("리뷰: 손으로 친 거대한 ?page= 도 상한으로 접어 정수 offset 으로 묻고 마지막 쪽으로 당긴다(지수 표기 offset → 422 로 굳지 않는다)", async () => {
  const w: World = { head: head(), items: Array.from({ length: 250 }, (_, i) => scanItem(i, "Succeeded")), calls: [] };
  renderAt(w, "/admin/batches/b1?page=10000000000000000000");
  await waitFor(() => expect(loc()).toBe("?page=3"));
  expect(await screen.findByRole("button", { name: "항목 249 상세" })).toBeInTheDocument();
  const offsets = w.calls.filter((c) => c.startsWith("items@")).map((c) => c.slice("items@".length));
  expect(offsets.length).toBeGreaterThan(0);
  for (const o of offsets) expect(o).toMatch(/^\d+$/);
});

test("검색: 멈춘 뒤 q 로 서버에 묻고 1쪽으로 · 결과 수·강조 · 0건은 「검색 결과가 없습니다」(항목 도구는 그대로)", async () => {
  const w: World = { head: head(), items: Array.from({ length: 250 }, (_, i) => scanItem(i, "Succeeded")), calls: [] };
  renderAt(w, "/admin/batches/b1?page=2");
  await screen.findByRole("button", { name: "항목 100 상세" });
  const box = screen.getByPlaceholderText("경로·PV 이름 검색…");
  await userEvent.type(box, "t12");
  await waitFor(() => expect(w.calls).toContain("items@0?q=t12"));
  await waitFor(() => expect(loc()).toBe("?q=t12"));                  // 검색이 바뀌면 page 를 지운다
  expect(await screen.findByText("검색 결과 11개")).toBeInTheDocument(); // T12, T120..T129
  const row = screen.getByRole("button", { name: "항목 12 상세" });
  expect(within(row).getByText("T12", { selector: "mark" })).toBeInTheDocument();
  await userEvent.clear(box);
  await userEvent.type(box, "zzz");
  expect(await screen.findByText("검색 결과가 없습니다")).toBeInTheDocument();
  expect(screen.queryByText(/항목이 없어 스토리지를 물려받을 수 없습니다/)).toBeNull();
  expect(screen.getByRole("button", { name: "현재 항목 CSV" })).toBeInTheDocument();   // 배치엔 항목이 있다
  await userEvent.click(screen.getByRole("button", { name: "검색 지우기" }));
  expect(await screen.findByRole("button", { name: "항목 0 상세" })).toBeInTheDocument();
  expect(loc()).toBe("");
});

test("상태 칩: 수는 머리 집계(0 도 보인다) · 누르면 status 로 묻고 1쪽으로 · 주소의 모르는 상태는 전체", async () => {
  const items = [scanItem(0, "Succeeded"), scanItem(1, "Failed"), scanItem(2, "Failed")];
  const w: World = { head: head(), items, calls: [] };
  renderAt(w, "/admin/batches/b1?status=Bogus");
  const chips = await screen.findByRole("group", { name: "항목 상태" });
  await waitFor(() => expect(within(chips).getByRole("button", { name: "전체 3" })).toHaveAttribute("aria-pressed", "true"));
  expect(within(chips).getByRole("button", { name: "Rejected 0" })).toBeInTheDocument();
  await userEvent.click(within(chips).getByRole("button", { name: "Failed 2" }));
  await waitFor(() => expect(w.calls).toContain("items@0&status=Failed"));
  expect(loc()).toBe("?status=Failed");
  await waitFor(() => expect(screen.queryByRole("button", { name: "항목 0 상세" })).toBeNull());
  expect(screen.getByRole("button", { name: "항목 1 상세" })).toBeInTheDocument();
  expect(screen.getByText("검색 결과 2개")).toBeInTheDocument();
});

test("선택은 쪽 단위: 쪽을 넘기면 비워지고, 「이 쪽 전체 선택」은 이 쪽의 고를 수 있는 행만 담는다", async () => {
  const w: World = { head: head(), items: Array.from({ length: 150 }, (_, i) => scanItem(i, "Succeeded")), calls: [] };
  renderAt(w);
  await userEvent.click(await screen.findByLabelText("이 쪽 전체 선택(100개)"));
  expect(screen.getByText("100개 선택됨")).toBeInTheDocument();
  await userEvent.click(within(screen.getByRole("navigation", { name: "항목 쪽" })).getByRole("button", { name: "다음" }));
  expect(await screen.findByRole("button", { name: "항목 149 상세" })).toBeInTheDocument();
  expect(screen.queryByText(/개 선택됨/)).toBeNull();
  expect(screen.getByLabelText("이 쪽 전체 선택(50개)")).not.toBeChecked();
});

test("설계 비판 #3: 상태 칩을 건 채 폴링으로 쪽에서 빠진 항목은 선택에서도 빠지고, 일괄 삭제는 보이는 선택만 보낸다", async () => {
  const items = [scanItem(0), scanItem(1), scanItem(2)];
  const w: World = { head: head({ status: "Running" }), items, calls: [] };
  const deleted: number[] = [];
  server.use(http.delete("/api/admin/batches/b1/items/:seq", ({ params }) => {
    deleted.push(Number(params.seq)); return HttpResponse.json({ deleted: Number(params.seq) }); }));
  const { qc } = renderAt(w, "/admin/batches/b1?status=Queued");
  await userEvent.click(await screen.findByLabelText("항목 0 선택"));
  await userEvent.click(screen.getByLabelText("항목 1 선택"));
  await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
  expect(screen.getByRole("button", { name: "2개 삭제 확인" })).toBeInTheDocument();
  // orchestrator 가 #0 을 집었다(Queued → Materialized) -- 파라미터는 그대로인데 Queued 칩의 쪽에서 #0 이 빠진다
  w.items = [scanItem(0, "Materialized"), scanItem(1), scanItem(2)];
  await act(async () => { await qc.refetchQueries({ queryKey: ["batch", "b1"] }); });
  await waitFor(() => expect(screen.queryByRole("button", { name: "항목 0 상세" })).toBeNull());
  expect(await screen.findByText("1개 선택됨")).toBeInTheDocument();
  // 확인한 집합이 줄었으니 무장도 풀렸다 -- 다시 확인해야 쏜다
  expect(screen.queryByRole("button", { name: /개 삭제 확인/ })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
  await userEvent.click(screen.getByRole("button", { name: "1개 삭제 확인" }));
  await waitFor(() => expect(deleted).toEqual([1]));
});

test("배치 확인 창: 열 때 view=all 을 한 번만 받고(쪽이 아닌 전체로 센다) 그 응답의 회차로 확인한다", async () => {
  const items: BatchItem[] = Array.from({ length: 150 }, (_, i) => ({
    seq: i, payload: { source_storage: "s1", source: `a${i}`, destination_storage: "s2", destination: `b${i}` },
    status: "Materialized", request_id: `r${i}`, reason_code: null, job_state: "ConfirmPending",
    preview_summary: { files: 1, bytes: null, returncode: 0 } }));
  const w: World = { head: head({ operation: "sync", status: "PreviewReady", preview_round: 3, storage: null,
                                  source_storage: "s1", destination_storage: "s2" }), items, calls: [] };
  let sent: unknown = null;
  server.use(http.post("/api/admin/batches/b1:confirm", async ({ request }) => {
    sent = await request.json(); return HttpResponse.json({ status: "Running" }); }));
  renderAt(w);
  await screen.findByRole("button", { name: "항목 0 상세" });
  expect(w.calls.filter((c) => c === "all")).toHaveLength(0);
  await userEvent.click(screen.getByRole("button", { name: "배치 확인" }));
  const dlg = await screen.findByRole("dialog", { name: "배치 확인" });
  expect(await within(dlg).findByText("150 / 전체 150")).toBeInTheDocument();     // 지금 쪽(100)이 아니다
  expect(w.calls.filter((c) => c === "all")).toHaveLength(1);
  await userEvent.click(within(dlg).getByRole("button", { name: "실행 확인" }));
  await waitFor(() => expect(sent).toEqual({ preview_round: 3 }));
});

test("현재 항목 CSV: 창을 열 때만 view=payloads 로 전 항목을 받는다(쪽·검색과 무관)", async () => {
  const w: World = { head: head(), items: Array.from({ length: 150 }, (_, i) => scanItem(i, "Succeeded")), calls: [] };
  renderAt(w, "/admin/batches/b1?q=T1");
  await screen.findByRole("button", { name: "항목 1 상세" });
  expect(w.calls.filter((c) => c === "payloads")).toHaveLength(0);
  await userEvent.click(screen.getByRole("button", { name: "현재 항목 CSV" }));
  const dlg = await screen.findByRole("dialog", { name: "현재 항목 CSV" });
  const text = (await within(dlg).findByLabelText("현재 항목 CSV") as HTMLTextAreaElement).value;
  expect(text.trim().split("\n")).toHaveLength(151);                                   // 헤더 + 전 항목
  expect(text).toContain("proj/T149");
  expect(w.calls.filter((c) => c === "payloads")).toHaveLength(1);
});

test("리뷰: 항목이 바뀐 뒤 현재 항목 CSV 를 다시 열면 새 목록을 받기 전에는 옛 CSV 를 그리지 않는다(복사 → 전체 교체 유실 방지)", async () => {
  // 항목 추가 창(AddItemDialog)이 같은 키에 관찰자를 붙여 두어 닫아도 지난 목록이 캐시에 남는다 -- 그 상태를 그대로 만든다
  const w: World = { head: head(), items: [scanItem(0, "Succeeded")], calls: [] };
  renderAt(w);
  await screen.findByRole("button", { name: "항목 0 상세" });
  await userEvent.click(screen.getByRole("button", { name: "현재 항목 CSV" }));
  let dlg = await screen.findByRole("dialog", { name: "현재 항목 CSV" });
  expect(await within(dlg).findByLabelText("현재 항목 CSV")).toHaveValue("target\nproj/T0");
  await userEvent.click(within(dlg).getByRole("button", { name: "닫기" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "현재 항목 CSV" })).toBeNull());
  // 그 사이 항목이 늘었고, 다시 연 창의 새 목록 응답은 느리다(붙잡아 둔다)
  w.items = [scanItem(0, "Succeeded"), scanItem(1)];
  let release!: () => void;
  const gate = new Promise<void>((r) => { release = r; });
  server.use(http.get("/api/admin/batches/b1", async ({ request }) => {
    if (new URL(request.url).searchParams.get("view") !== "payloads") return undefined;   // 나머지 view 는 serve 로
    await gate;
    return HttpResponse.json({ items: w.items.map((it) => ({ seq: it.seq, payload: it.payload, status: it.status })) });
  }));
  await userEvent.click(screen.getByRole("button", { name: "현재 항목 CSV" }));
  dlg = await screen.findByRole("dialog", { name: "현재 항목 CSV" });
  expect(within(dlg).getByText("불러오는 중…")).toBeInTheDocument();
  expect(within(dlg).queryByLabelText("현재 항목 CSV")).toBeNull();                   // 옛 「target\nproj/T0」 가 보이지 않는다
  release();
  expect(await within(dlg).findByLabelText("현재 항목 CSV")).toHaveValue("target\nproj/T0\nproj/T1");
});

test("항목 펼침의 온도·크기 차트는 사용량과 공용 -- 값이 빠진 히스토그램은 막대 대신 「알 수 없음」(null≠0 교정)", async () => {
  const w: World = { head: head(), items: [{ ...scanItem(0, "Succeeded"), request_state: "Succeeded" }], calls: [] };
  server.use(http.get("/api/admin/requests/r0/scan-stats", () => HttpResponse.json({
    generated_at_epoch: 1785805962, summary: {}, total_bytes: null, broken_paths_total: 0,
    file_size_histogram: [{ bucket: "[0,4096]", lower_inclusive: 0, upper_inclusive: 4096, count: 3 },
                          { bucket: "[4097,65536]", lower_inclusive: 4097, upper_inclusive: 65536 }],
    time_histograms: { atime: [{ bucket: "[0d,1d]", bytes: 10 }, { bucket: "[1d,7d]" }] } })));
  renderAt(w);
  await userEvent.click(await screen.findByRole("button", { name: "항목 0 상세" }));
  expect(await screen.findByText("이 축의 온도 분포를 알 수 없습니다(리포트 값 누락)")).toBeInTheDocument();
  expect(screen.getByText("크기 분포를 알 수 없습니다(리포트 값 누락)")).toBeInTheDocument();
  expect(screen.getByText("파일 크기 분포(개수)")).toBeInTheDocument();       // 부분합을 총계라 하지 않는다
});
