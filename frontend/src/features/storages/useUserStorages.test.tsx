import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { useStorageBackends, useUserStorages } from "./useUserStorages";
import { groupCaveatsFor } from "../../lib/groupCaveats";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());
test("useUserStorages returns the list", async () => {
  const rows = [
    { storage_name: "cephfs", backend_type: "cephfs", status: "ready" },
    { storage_name: "cephfs-secondary", backend_type: "cephfs", status: "ready" },
  ];
  server.use(http.get("/api/user/storages", () => HttpResponse.json(rows)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => useUserStorages(), { wrapper: ({ children }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  await waitFor(() => expect(result.current.data).toEqual(rows));
});

// 보조 그룹 주의문(2026-10-07 D15): 이름 → backend_type 맵. 같은 쿼리 키라 요청이 늘지 않는다.
function renderBackends(rows: object[]) {
  let calls = 0;
  server.use(http.get("/api/user/storages", () => { calls += 1; return HttpResponse.json(rows); }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const hook = renderHook(() => ({ backends: useStorageBackends(), list: useUserStorages() }), {
    wrapper: ({ children }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  return { ...hook, calls: () => calls };
}

test("useStorageBackends: 이름 → backend_type 맵(같은 쿼리 -- 요청 1번)", async () => {
  const { result, calls } = renderBackends([
    { storage_name: "ceph-a", backend_type: "cephfs", status: "Ready" },
    { storage_name: "nas", backend_type: "netapp", status: "Ready" },
    { storage_name: "exa", backend_type: "lustre", status: "Ready", admin_only: true }]);
  await waitFor(() => expect(result.current.backends).toEqual({ "ceph-a": "cephfs", nas: "netapp", exa: "lustre" }));
  expect(calls()).toBe(1);
});

test("useStorageBackends: 비관리자 응답(관리자 전용 없음)에 없는 이름은 일반 주의문으로 떨어진다", async () => {
  const { result } = renderBackends([{ storage_name: "nas", backend_type: "netapp", status: "Ready" }]);
  await waitFor(() => expect(result.current.backends).toEqual({ nas: "netapp" }));
  // 관리자 전용 lustre 스토리지 "exa" 는 응답에 없다 -- 모름이지 lustre 가 아니다
  expect(groupCaveatsFor(["exa"], result.current.backends))
    .toEqual(["실제 인정 여부는 스토리지 설정에 따라 다를 수 있습니다(등록된 스토리지 종류 기준 안내)."]);
  expect(groupCaveatsFor(["nas", "exa"], result.current.backends)).toHaveLength(2);
});

test("useStorageBackends: 조회 실패면 빈 맵(죽지 않는다)", async () => {
  server.use(http.get("/api/user/storages", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => ({ backends: useStorageBackends(), list: useUserStorages() }), {
    wrapper: ({ children }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  await waitFor(() => expect(result.current.list.isError).toBe(true));
  expect(result.current.backends).toEqual({});
});
