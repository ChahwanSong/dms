import { createElement } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, beforeEach, afterAll, afterEach, test, expect } from "vitest";
import { useLogin, useLogout } from "./useAuth";
import { forgetTransportKey } from "../../lib/passwordTransport";
import { NAV_COLLAPSED_KEY } from "../../lib/navState";
import { makeServerKey, transportKeyHandler, type TestServerKey } from "../../test/transportKey";

const server = setupServer();
let serverKey: TestServerKey;
beforeAll(async () => { server.listen(); serverKey = await makeServerKey(); });
beforeEach(() => { forgetTransportKey(); server.use(transportKeyHandler(serverKey)); });
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

// 사이드바 접힘(2026-09-30 사용자 결정): 로그인할 때 전부 펼침으로 리셋 -- 이전 로그인(같은
// 브라우저의 다른 사용자 포함)이 접어 둔 상태를 끌고 오지 않는다. 로그아웃도 비운다.
test("login success resets the saved sidebar collapse state (menu starts fully expanded)", async () => {
  localStorage.setItem(NAV_COLLAPSED_KEY, JSON.stringify({ "DMS:관리": true }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  server.use(http.post("/api/auth/login",
    () => HttpResponse.json({ actor: "bob", role: "user" })));
  const { result } = renderHook(() => useLogin(), { wrapper: wrapperFor(qc) });
  result.current.mutate({ username: "bob", password: "pw" });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(localStorage.getItem(NAV_COLLAPSED_KEY)).toBeNull();
});

test("a failed login keeps the saved collapse state; logout clears it", async () => {
  localStorage.setItem(NAV_COLLAPSED_KEY, JSON.stringify({ "DMS:관리": true }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  server.use(
    http.post("/api/auth/login", () => HttpResponse.json({ detail: "invalid_credentials" }, { status: 401 })),
    http.post("/api/auth/logout", () => HttpResponse.json({ status: "ok" })));
  const login = renderHook(() => useLogin(), { wrapper: wrapperFor(qc) });
  login.result.current.mutate({ username: "bob", password: "bad" });
  await waitFor(() => expect(login.result.current.isError).toBe(true));
  expect(localStorage.getItem(NAV_COLLAPSED_KEY)).not.toBeNull();
  const logout = renderHook(() => useLogout(), { wrapper: wrapperFor(qc) });
  logout.result.current.mutate();
  await waitFor(() => expect(logout.result.current.isSuccess).toBe(true));
  expect(localStorage.getItem(NAV_COLLAPSED_KEY)).toBeNull();
});
