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
