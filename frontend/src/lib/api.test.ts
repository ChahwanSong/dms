import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect, vi, describe, it } from "vitest";
import { apiGet, apiSend, ApiError, reasonText } from "./api";

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

test("401 + JSON detail: 파싱된 코드로 ApiError, 이벤트는 정확히 1회", async () => {
  const spy = vi.fn();
  window.addEventListener("dms:unauthorized", spy);
  try {
    server.use(http.get("/api/auth/me",
      () => HttpResponse.json({ detail: "not_authenticated" }, { status: 401 })));
    await expect(apiGet("/api/auth/me")).rejects.toMatchObject({
      status: 401, code: "not_authenticated", message: "로그인이 필요합니다" });
    expect(spy).toHaveBeenCalledOnce();
  } finally {
    window.removeEventListener("dms:unauthorized", spy);
  }
});

test("인그레스 401(비 JSON 본문): http_401 합성 + 이벤트 발화", async () => {
  // 인그레스/프록시가 백엔드 앞에서 만든 401 은 본문이 HTML 이라 detail 이 없다 --
  // request() 가 http_401 을 합성하는 계약(api.ts REASON_MESSAGES 의 http_401 주석).
  const spy = vi.fn();
  window.addEventListener("dms:unauthorized", spy);
  try {
    server.use(http.get("/api/auth/me", () => new HttpResponse("<html>401</html>",
      { status: 401, headers: { "Content-Type": "text/html" } })));
    await expect(apiGet("/api/auth/me")).rejects.toMatchObject({
      status: 401, code: "http_401", message: "인증이 필요합니다" });
    expect(spy).toHaveBeenCalledOnce();
  } finally {
    window.removeEventListener("dms:unauthorized", spy);
  }
});

test("403 은 dms:unauthorized 를 발화하지 않는다 -- 이벤트는 401 전용 계약", async () => {
  // 401 분기를 !res.ok 로 통합할 때 조건이 소실되면 권한 없는 화면(403)마다
  // AuthContext 가 me 를 무효화해 로그인으로 튕긴다 -- 그 오작동을 막는 못.
  const spy = vi.fn();
  window.addEventListener("dms:unauthorized", spy);
  try {
    server.use(http.get("/api/admin/x",
      () => HttpResponse.json({ detail: "admin_required" }, { status: 403 })));
    await expect(apiGet("/api/admin/x")).rejects.toMatchObject({
      status: 403, code: "admin_required", message: "관리자 권한이 필요합니다" });
    expect(spy).not.toHaveBeenCalled();
  } finally {
    window.removeEventListener("dms:unauthorized", spy);
  }
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

describe("reasonText", () => {
  it("빈 값은 빈 문자열", () => {
    expect(reasonText(null)).toBe("");
    expect(reasonText(undefined)).toBe("");
    expect(reasonText("")).toBe("");
  });

  it("매핑된 코드는 한국어", () => {
    expect(reasonText("identity_denied")).toBe("차단 목록에 있는 신원입니다");
  });

  it("매핑 없는 코드는 원시 코드 그대로", () => {
    // api.test.ts 가 이미 단언하는 폴백 규칙 -- 일반 문구로 바꾸지 않는다
    expect(reasonText("totally_unknown_code")).toBe("totally_unknown_code");
  });

  it("복합 코드는 접두를 번역하고 접미를 병기한다", () => {
    // stepper.py 가 f"preflight_submit_failed:{exc.reason_code}" 를 만든다 --
    // 정확 일치 조회로는 영원히 매핑되지 않는다
    expect(reasonText("preflight_submit_failed:submit_failed"))
      .toBe("사전 점검을 시작하지 못했습니다 (submit_failed)");
  });

  it("접두도 매핑 없으면 원시 전체", () => {
    expect(reasonText("nope:whatever")).toBe("nope:whatever");
  });
});
