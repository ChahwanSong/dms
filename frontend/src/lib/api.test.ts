import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect, vi } from "vitest";
import { apiGet, apiSend, ApiError } from "./api";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("apiGet returns json", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json({ actor: "alice", role: "user" })));
  await expect(apiGet("/api/auth/me")).resolves.toEqual({ actor: "alice", role: "user" });
});

test("error maps reason_code to korean message", async () => {
  server.use(http.post("/api/user/jobs/j1:confirm",
    () => HttpResponse.json({ detail: "fingerprint_mismatch" }, { status: 409 })));
  await expect(apiSend("POST", "/api/user/jobs/j1:confirm", { fingerprint: "x" }))
    .rejects.toMatchObject({ status: 409, code: "fingerprint_mismatch",
      message: "미리보기가 변경되었습니다. 다시 확인해 주세요" });
});

test("unknown reason_code falls back to raw code", async () => {
  server.use(http.get("/api/x", () => HttpResponse.json({ detail: "weird_thing" }, { status: 400 })));
  await expect(apiGet("/api/x")).rejects.toMatchObject({ code: "weird_thing", message: "weird_thing" });
});

test("401 dispatches auth-expired event", async () => {
  const spy = vi.fn();
  window.addEventListener("dms:unauthorized", spy);
  server.use(http.get("/api/auth/me", () => HttpResponse.json({ detail: "x" }, { status: 401 })));
  await expect(apiGet("/api/auth/me")).rejects.toBeInstanceOf(ApiError);
  expect(spy).toHaveBeenCalledOnce();
});

test("401 with unmapped reason_code falls back to raw code, not a hardcoded password message", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json({ detail: "session_expired" }, { status: 401 })));
  await expect(apiGet("/api/auth/me")).rejects.toMatchObject({ status: 401, code: "session_expired",
    message: "session_expired" });
});

test("401 with invalid_credentials still maps to the korean login message", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json({ detail: "invalid_credentials" }, { status: 401 })));
  await expect(apiGet("/api/auth/me")).rejects.toMatchObject({ status: 401, code: "invalid_credentials",
    message: "사용자명 또는 비밀번호가 올바르지 않습니다" });
});

test("422 with FastAPI field-validation detail array maps to a korean message, not [object Object]", async () => {
  server.use(http.post("/api/admin/policies", () => HttpResponse.json(
    { detail: [{ loc: ["body", "max_nodes"], msg: "ensure this value is greater than or equal to 1" }] },
    { status: 422 })));
  await expect(apiSend("POST", "/api/admin/policies", { max_nodes: 0 }))
    .rejects.toMatchObject({ status: 422, code: "http_422", message: "입력값이 올바르지 않습니다" });
});
