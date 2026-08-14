import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { useBatches, useUpdateBatch } from "./useBatches";
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

test("useBatches fetches list", async () => {
  server.use(http.get("/api/admin/batches", () => HttpResponse.json([{batch_id:"b1",operation:"scan",
    status:"Running",max_concurrency:2,item_count:3,succeeded_count:1,failed_count:0,note:null,created_at:""}])));
  const qc = new QueryClient({ defaultOptions:{ queries:{ retry:false }}});
  const { result } = renderHook(() => useBatches(), { wrapper: ({children}) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  await waitFor(() => expect(result.current.data?.[0].batch_id).toBe("b1"));
});
