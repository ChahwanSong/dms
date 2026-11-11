import { createElement } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, beforeEach, afterAll, afterEach, test, expect } from "vitest";
import { ME_RETRY_LIMIT, meRetry, meRetryDelay, useLogin, useLogout } from "./useAuth";
import { ApiError } from "../../lib/api";
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

// /me 재시도 정책(2026-11-04 기능 점검 #2): 5xx·네트워크(fetch 거부 = ApiError 아님)만 ME_RETRY_LIMIT 회까지, 4xx 는 한 번도 -- 401
// 은 진짜 로그인 필요라 재시도하면 dms:unauthorized → me 무효화와 겹쳐 요청만 는다.
test("meRetry: 5xx·네트워크 오류만 짧게 재시도하고 4xx(특히 401)는 재시도하지 않는다", () => {
  for (const status of [500, 502, 503, 504]) {
    const e = new ApiError(status, `http_${status}`, "x");
    for (let n = 0; n < ME_RETRY_LIMIT; n += 1) expect(meRetry(n, e)).toBe(true);
    expect(meRetry(ME_RETRY_LIMIT, e)).toBe(false);
  }
  const network = new TypeError("Failed to fetch");
  expect(meRetry(0, network)).toBe(true);
  expect(meRetry(ME_RETRY_LIMIT, network)).toBe(false);
  // 2026-11-11: request() 는 fetch 거부를 ApiError(0, network_error)로 올린다 -- status 0 도 네트워크로 재시도해야 한다.
  const wrapped = new ApiError(0, "network_error", "x");
  expect(meRetry(0, wrapped)).toBe(true);
  expect(meRetry(ME_RETRY_LIMIT, wrapped)).toBe(false);
  for (const [status, code] of [[401, "not_authenticated"], [401, "session_revoked"], [403, "forbidden"], [404, "http_404"]] as const) {
    expect(meRetry(0, new ApiError(status, code, "x"))).toBe(false);
  }
  // 대기는 짧게 -- 다 써도 약 1.5초 안에 오류 화면(다시 시도)으로 넘어간다.
  expect([0, 1].map(meRetryDelay)).toEqual([500, 1000]);
  expect(ME_RETRY_LIMIT).toBe(2);
});
