import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { useCreateStorage } from "./useStorages";
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
