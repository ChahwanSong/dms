import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { useCreateStorage, useDeleteStorage, useUpdateStorage } from "./useStorages";
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
  const { result } = renderHook(() => useDeleteStorage(), { wrapper: ({ children }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  result.current.mutate("s1");
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  for (const key of [["storages"], ["sync-pairs"]])
    expect(qc.getQueryState(key)?.isInvalidated).toBe(true);
});

// 2026-11-11: 경로별 행(["usage-targets"])의 PV 판정은 스토리지 행·종류로 서버가 낸다 -- 등록·수정·삭제 뒤 다시 읽어야 facets 와
// 행이 어긋나지 않는다. 삭제는 StorageClass 매핑도 함께 지우므로 k8s 조회(접두 ["k8s"])도 무효화한다.
const USAGE_TARGETS_KEY = ["usage-targets", "s1", "", "path", "asc", "all"];
function seeded() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(["storages"], []);
  qc.setQueryData(USAGE_TARGETS_KEY, { rows: [] });
  qc.setQueryData(["k8s", "storage-classes"], []);
  const wrapper = ({ children }: { children: React.ReactNode }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  return { qc, wrapper };
}

test("등록·수정·삭제는 사용량 경로별 행(usage-targets)을 무효화하고, 삭제는 k8s 조회도 무효화한다", async () => {
  server.use(
    http.post("/api/admin/storages", () => HttpResponse.json({}, { status: 201 })),
    http.put("/api/admin/storages/:name", () => HttpResponse.json({})),
    http.delete("/api/admin/storages/:name", () => HttpResponse.json({ deleted: true })));
  {
    const { qc, wrapper } = seeded();
    const { result } = renderHook(() => useCreateStorage(), { wrapper });
    result.current.mutate({ storage_name: "s1", mount_path: "/s1", managed_root: "/s1/dms", backend_type: "cephfs" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(qc.getQueryState(USAGE_TARGETS_KEY)?.isInvalidated).toBe(true);
  }
  {
    const { qc, wrapper } = seeded();
    const { result } = renderHook(() => useUpdateStorage(), { wrapper });
    result.current.mutate({ name: "s1", body: { mount_path: "/s1", managed_root: "/s1/dms", backend_type: "cephfs",
                                               enabled: true, kind: "pv" } });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(qc.getQueryState(USAGE_TARGETS_KEY)?.isInvalidated).toBe(true);
  }
  {
    const { qc, wrapper } = seeded();
    const { result } = renderHook(() => useDeleteStorage(), { wrapper });
    result.current.mutate("s1");
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(qc.getQueryState(USAGE_TARGETS_KEY)?.isInvalidated).toBe(true);
    expect(qc.getQueryState(["k8s", "storage-classes"])?.isInvalidated).toBe(true);
  }
});
