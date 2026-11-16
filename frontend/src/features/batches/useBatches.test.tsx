import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { useBatch, useBatchItems, useBatches, useUpdateBatch, useUpdateBatchItem,
         useReplaceBatchItems, useRerunBatchItems, needsBatchConfirm } from "./useBatches";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());
test("useUpdateBatch PATCHes and invalidates batch queries", async () => {
  let patched: any = null;
  server.use(http.patch("/api/admin/batches/b1", async ({ request }) => {
    patched = await request.json();
    return HttpResponse.json({ batch_id: "b1", name: "n" });
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = { calls: [] as unknown[] };
  const origInvalidate = qc.invalidateQueries.bind(qc);
  qc.invalidateQueries = ((opts: unknown) => { spy.calls.push(opts); return origInvalidate(opts as never); }) as never;
  const { result } = renderHook(() => useUpdateBatch("b1"), { wrapper: ({children}) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  result.current.mutate({ name: "n", note: "m" });
  await waitFor(() => expect(patched).toEqual({ name: "n", note: "m" }));
  // 저장 후 상세·목록 쿼리 무효화(onSettled) — 화면이 옛 이름을 계속 그리면 안 된다
  await waitFor(() => expect(spy.calls).toEqual(expect.arrayContaining([
    expect.objectContaining({ queryKey: ["batch", "b1"] }),
    expect.objectContaining({ queryKey: ["batches"] }),
  ])));
});

test("useUpdateBatchItem PUTs item payload and invalidates batch queries", async () => {
  let sent: any = null;
  server.use(http.put("/api/admin/batches/b1/items/2", async ({ request }) => {
    sent = await request.json();
    return HttpResponse.json({ seq: 2, status: "Queued" });
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = { calls: [] as unknown[] };
  const origInvalidate = qc.invalidateQueries.bind(qc);
  qc.invalidateQueries = ((opts: unknown) => { spy.calls.push(opts); return origInvalidate(opts as never); }) as never;
  const { result } = renderHook(() => useUpdateBatchItem("b1"), { wrapper: ({children}) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  result.current.mutate({ seq: 2, item: { storage: "s1", target: "edited" } });
  await waitFor(() => expect(sent).toEqual({ storage: "s1", target: "edited" }));
  // 저장 후 상세·목록 쿼리 무효화(onSettled) — 화면이 옛 payload 를 계속 그리면 안 된다
  await waitFor(() => expect(spy.calls).toEqual(expect.arrayContaining([
    expect.objectContaining({ queryKey: ["batch", "b1"] }),
    expect.objectContaining({ queryKey: ["batches"] }),
  ])));
});

test("useReplaceBatchItems PUTs {items} 컬렉션 and invalidates batch queries", async () => {
  let sent: any = null;
  server.use(http.put("/api/admin/batches/b1/items", async ({ request }) => {
    sent = await request.json();
    return HttpResponse.json({ replaced: 2 });
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = { calls: [] as unknown[] };
  const origInvalidate = qc.invalidateQueries.bind(qc);
  qc.invalidateQueries = ((opts: unknown) => { spy.calls.push(opts); return origInvalidate(opts as never); }) as never;
  const { result } = renderHook(() => useReplaceBatchItems("b1"), { wrapper: ({children}) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  result.current.mutate([{ storage: "s1", target: "x" }, { storage: "s1", target: "y" }]);
  await waitFor(() => expect(sent).toEqual({ items: [
    { storage: "s1", target: "x" }, { storage: "s1", target: "y" }] }));
  // 교체 후 상세·목록 쿼리 무효화(onSettled) — 화면이 옛 항목을 계속 그리면 안 된다
  await waitFor(() => expect(spy.calls).toEqual(expect.arrayContaining([
    expect.objectContaining({ queryKey: ["batch", "b1"] }),
    expect.objectContaining({ queryKey: ["batches"] }),
  ])));
});

test("useRerunBatchItems POSTs {seqs} and invalidates batch queries", async () => {
  let sent: any = null;
  server.use(http.post("/api/admin/batches/b1/items:rerun", async ({ request }) => {
    sent = await request.json();
    return HttpResponse.json({ requeued: 1, skipped: [
      { seq: 5, reason: "batch_item_not_rerunnable" }], status: "Running" });
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = { calls: [] as unknown[] };
  const origInvalidate = qc.invalidateQueries.bind(qc);
  qc.invalidateQueries = ((opts: unknown) => { spy.calls.push(opts); return origInvalidate(opts as never); }) as never;
  const { result } = renderHook(() => useRerunBatchItems("b1"), { wrapper: ({children}) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  result.current.mutate([0, 5]);
  await waitFor(() => expect(sent).toEqual({ seqs: [0, 5] }));
  // 부분 성공은 isError 가 아니라 **데이터**다 — 서버가 requeued/skipped 로 말한다
  await waitFor(() => expect(result.current.data).toEqual({ requeued: 1,
    skipped: [{ seq: 5, reason: "batch_item_not_rerunnable" }], status: "Running" }));
  // 재실행 후 상세·목록 무효화(onSettled) — 화면이 옛 항목 상태를 계속 그리면 안 된다
  await waitFor(() => expect(spy.calls).toEqual(expect.arrayContaining([
    expect.objectContaining({ queryKey: ["batch", "b1"] }),
    expect.objectContaining({ queryKey: ["batches"] }),
  ])));
});

test("useBatches fetches list", async () => {
  server.use(http.get("/api/admin/batches", () => HttpResponse.json([{batch_id:"b1",operation:"scan",
    status:"Running",max_concurrency:2,item_count:3,succeeded_count:1,failed_count:0,note:null,created_at:""}])));
  const qc = new QueryClient({ defaultOptions:{ queries:{ retry:false }}});
  const { result } = renderHook(() => useBatches(), { wrapper: ({children}) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  await waitFor(() => expect(result.current.data?.[0].batch_id).toBe("b1"));
});

// --- 상세 쪽 조회(2026-11-16): 머리 ?view=summary · 항목 ?view=items -- 옛 파드(view 무시 → 전체 응답)는 같은 모양으로 물러선다 ---
const fullBatch = { batch_id: "b1", operation: "scan", status: "Running", max_concurrency: 1, item_count: 2,
  succeeded_count: 0, failed_count: 0, note: null, created_at: "", options: {},
  items: [{ seq: 3, payload: { storage: "s1", target: "a" }, status: "Queued", request_id: null, reason_code: null },
          { seq: 4, payload: { storage: "s1", target: "b" }, status: "Failed", request_id: null, reason_code: "x" }] };
const wrap = (qc: QueryClient) => ({ wrapper: ({ children }: { children: React.ReactNode }) =>
  <QueryClientProvider client={qc}>{children}</QueryClientProvider> });

test("useBatch: ?view=summary 로 묻고, 옛 파드의 전체 응답이면 items 에서 머리를 파생한다(items 는 버린다)", async () => {
  const seen: string[] = [];
  server.use(http.get("/api/admin/batches/b1", ({ request }) => {
    seen.push(new URL(request.url).search); return HttpResponse.json(fullBatch); }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => useBatch("b1"), wrap(qc));
  await waitFor(() => expect(result.current.data?.item_total).toBe(2));
  expect(seen[0]).toBe("?view=summary");
  expect(result.current.data?.status_counts).toEqual({ Queued: 1, Failed: 1 });
  expect(result.current.data?.first_payload).toEqual({ storage: "s1", target: "a" });
  expect("items" in (result.current.data ?? {})).toBe(false);
});

test("useBatchItems: 쪽·검색·상태를 쿼리로 싣고, 옛 파드의 전체 응답은 화면에서 거르고 자른다", async () => {
  const seen: URLSearchParams[] = [];
  server.use(http.get("/api/admin/batches/b1", ({ request }) => {
    seen.push(new URL(request.url).searchParams); return HttpResponse.json(fullBatch); }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => useBatchItems("b1", { page: 1, q: "B", status: "Failed" }, "Running"), wrap(qc));
  await waitFor(() => expect(result.current.data?.total).toBe(1));
  expect(result.current.data?.items.map((it) => it.seq)).toEqual([4]);
  const p = seen[0];
  expect([p.get("view"), p.get("offset"), p.get("limit"), p.get("q"), p.get("status")])
    .toEqual(["items", "0", "100", "B", "Failed"]);
});

test("needsBatchConfirm: 확인 흐름은 sync·rm 명시 집합 — scan·모르는 연산·부재는 아니다(DB 신뢰 경계)", () => {
  expect(needsBatchConfirm("sync")).toBe(true);
  expect(needsBatchConfirm("rm")).toBe(true);
  for (const op of ["scan", "bogus", "", undefined]) expect(needsBatchConfirm(op)).toBe(false);
});
