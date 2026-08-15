import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { useBatches, useUpdateBatch, useUpdateBatchItem,
         useReplaceBatchItems, useRerunBatchItems } from "./useBatches";
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
