import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { useCreateStorage, useDeleteStorage } from "./useStorages";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());
test("useCreateStorage posts body", async () => {
  let body: any = null;
  server.use(http.post("/api/admin/storages", async ({ request }) => {
    body = await request.json(); return HttpResponse.json(body, { status: 201 }); }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => useCreateStorage(), { wrapper: ({ children }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  result.current.mutate({ storage_name: "s1", mount_path: "/s1", managed_root: "/s1/dms", backend_type: "cephfs" });
  await waitFor(() => expect(body).toMatchObject({ storage_name: "s1", backend_type: "cephfs" }));
});

test("useDeleteStorage 는 sync 허용 쌍 캐시도 무효화한다(서버가 그 스토리지의 쌍을 함께 지운다)", async () => {
  server.use(http.delete("/api/admin/storages/:name", () => HttpResponse.json({ deleted: true })));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(["storages"], []);
  qc.setQueryData(["sync-pairs"], [{ source_storage: "s1", destination_storage: "s1" }]);
  qc.setQueryData(["user-sync-pairs"], { restricted: true, pairs: [] });
  const { result } = renderHook(() => useDeleteStorage(), { wrapper: ({ children }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  result.current.mutate("s1");
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  for (const key of [["storages"], ["sync-pairs"], ["user-sync-pairs"]])
    expect(qc.getQueryState(key)?.isInvalidated).toBe(true);
});
