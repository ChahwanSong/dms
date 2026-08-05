import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { usePolicies, useUpsertPolicy } from "./usePolicies";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());
test("usePolicies returns the list", async () => {
  const rows = [{
    tool: "scan", max_nodes: 4, procs_per_node: 8, queue: "default",
    default_priority: "normal", max_priority: "high",
    preview_timeout_seconds: 300, execution_timeout_seconds: 3600,
    enabled: 1, updated_at: "2026-08-05T00:00:00Z", updated_by: "admin",
  }];
  server.use(http.get("/api/admin/policies", () => HttpResponse.json(rows)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => usePolicies(), { wrapper: ({ children }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  await waitFor(() => expect(result.current.data).toEqual(rows));
});
test("useUpsertPolicy puts body unchanged to /api/admin/policies/scan", async () => {
  let body: any = null;
  server.use(http.put("/api/admin/policies/scan", async ({ request }) => {
    body = await request.json(); return HttpResponse.json(body); }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => useUpsertPolicy(), { wrapper: ({ children }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  const reqBody = {
    max_nodes: 4, procs_per_node: 8, queue: "default",
    default_priority: "normal", max_priority: "high",
    preview_timeout_seconds: 300, execution_timeout_seconds: 3600,
    enabled: true,
  };
  result.current.mutate({ tool: "scan", body: reqBody });
  await waitFor(() => expect(body).toEqual(reqBody));
});
