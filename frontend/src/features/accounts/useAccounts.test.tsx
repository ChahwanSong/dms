import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { useAccounts, useSetRole, useSetDisabled } from "./useAccounts";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());
test("useAccounts returns the list", async () => {
  const rows = [
    { username: "admin", role: "admin", email: "admin@example.com", disabled: 0, created_at: "2026-08-05T00:00:00Z" },
    { username: "alice", role: "user", email: null, disabled: 1, created_at: "2026-08-05T00:00:00Z" },
  ];
  server.use(http.get("/api/admin/accounts", () => HttpResponse.json(rows)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => useAccounts(), { wrapper: ({ children }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  await waitFor(() => expect(result.current.data).toEqual(rows));
});
test("useSetRole puts {role} to /api/admin/accounts/:username/role", async () => {
  let body: any = null;
  server.use(http.put("/api/admin/accounts/alice/role", async ({ request }) => {
    body = await request.json(); return HttpResponse.json({ ok: true }); }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => useSetRole(), { wrapper: ({ children }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  result.current.mutate({ username: "alice", role: "admin" });
  await waitFor(() => expect(body).toEqual({ role: "admin" }));
});
test("useSetDisabled puts {disabled} to /api/admin/accounts/:username/disabled", async () => {
  let body: any = null;
  server.use(http.put("/api/admin/accounts/alice/disabled", async ({ request }) => {
    body = await request.json(); return HttpResponse.json({ ok: true }); }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => useSetDisabled(), { wrapper: ({ children }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  result.current.mutate({ username: "alice", disabled: true });
  await waitFor(() => expect(body).toEqual({ disabled: true }));
});
