import { createElement } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { useLogin, useLogout } from "./useAuth";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrapperFor(qc: QueryClient) {
  return ({ children }: { children: React.ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children);
}

test("logout clears the entire cache even when the request fails", async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(["requests"], [{}]);
  server.use(http.post("/api/auth/logout",
    () => HttpResponse.json({ detail: "server_error" }, { status: 500 })));

  const { result } = renderHook(() => useLogout(), { wrapper: wrapperFor(qc) });
  result.current.mutate();

  await waitFor(() => expect(result.current.isError).toBe(true));
  expect(qc.getQueryData(["requests"])).toBeUndefined();
});

test("login clears the previous user's cached data on success", async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(["requests"], [{}]);
  server.use(http.post("/api/auth/login",
    () => HttpResponse.json({ actor: "bob", role: "user" })));

  const { result } = renderHook(() => useLogin(), { wrapper: wrapperFor(qc) });
  result.current.mutate({ username: "bob", password: "pw" });

  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(qc.getQueryData(["requests"])).toBeUndefined();
});
