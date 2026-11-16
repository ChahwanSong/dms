import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { useBatches, useUpdateBatch, useUpdateBatchItem, useDeleteBatch, useDeleteBatches,
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

test("needsBatchConfirm: 확인 흐름은 sync·rm 명시 집합 — scan·모르는 연산·부재는 아니다(DB 신뢰 경계)", () => {
  expect(needsBatchConfirm("sync")).toBe(true);
  expect(needsBatchConfirm("rm")).toBe(true);
  for (const op of ["scan", "bogus", "", undefined]) expect(needsBatchConfirm(op)).toBe(false);
});

test("useBatches({enabled:false}) 는 조회하지 않는다 — 배치 스케줄러 추가 창이 열렸을 때만 같은 키로 읽는다", async () => {
  let hits = 0;
  server.use(http.get("/api/admin/batches", () => { hits += 1; return HttpResponse.json([]); }));
  const qc = new QueryClient({ defaultOptions:{ queries:{ retry:false }}});
  const wrapper = ({children}: {children: React.ReactNode}) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  const { result, rerender } = renderHook(({ on }) => useBatches({ enabled: on }), { wrapper, initialProps: { on: false } });
  await new Promise((r) => setTimeout(r, 50));
  expect(hits).toBe(0);
  expect(result.current.fetchStatus).toBe("idle");
  rerender({ on: true });
  await waitFor(() => expect(result.current.data).toEqual([]));
  expect(hits).toBe(1);
  // 같은 키(["batches"]) -- 배치 목록 화면과 캐시를 나눠 쓴다
  expect(qc.getQueryData(["batches"])).toEqual([]);
});

// 배치 삭제는 그 배치의 스케줄·회차 기록도 지운다(서버 같은 트랜잭션) -- 스케줄러 화면이 지운 배치의 스케줄을 남겨 보이지 않게
// 두 삭제 훅 모두 ["batch-schedules"] 를 무효화한다(2026-11-16).
function spyInvalidate(qc: QueryClient) {
  const calls: unknown[] = [];
  const orig = qc.invalidateQueries.bind(qc);
  qc.invalidateQueries = ((opts: unknown) => { calls.push(opts); return orig(opts as never); }) as never;
  return calls;
}

test("useDeleteBatch 는 성공하면 배치 스케줄 목록도 무효화한다", async () => {
  server.use(http.delete("/api/admin/batches/b1", () => HttpResponse.json({ deleted: "b1" })),
             http.get("/api/admin/batches", () => HttpResponse.json([])));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const calls = spyInvalidate(qc);
  const { result } = renderHook(() => useDeleteBatch("b1"), { wrapper: ({children}) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  result.current.mutate();
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(calls).toEqual(expect.arrayContaining([expect.objectContaining({ queryKey: ["batch-schedules"] })]));
});

test("useDeleteBatches(목록 일괄 삭제)도 배치 스케줄 목록을 무효화한다", async () => {
  server.use(http.delete("/api/admin/batches/:id", () => HttpResponse.json({ deleted: "x" })));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const calls = spyInvalidate(qc);
  const { result } = renderHook(() => useDeleteBatches(), { wrapper: ({children}) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  result.current.mutate(["b1", "b2"]);
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(calls).toEqual(expect.arrayContaining([
    expect.objectContaining({ queryKey: ["batches"] }),
    expect.objectContaining({ queryKey: ["batch-schedules"] }),
  ]));
});
