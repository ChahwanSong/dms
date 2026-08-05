import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { useUserStorages } from "./useUserStorages";
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
