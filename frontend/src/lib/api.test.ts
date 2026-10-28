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

  it("poll_failed 는 빌드 전용 문구가 아니다 -- 잡 로그 조회 409 도 같은 코드를 재사용한다", () => {
    // 슬라이스 25 가 vcjob 로그 list 실패(execution_volcano.py -> routes_artifacts.py
    // 409)에 poll_failed 를 재사용했다("사유 코드 신설 0" 방침) -- 문구에 "빌드"가
    // 들어가면 잡 로그 탭에서 거짓말이 된다. 공유 코드의 문구는 문맥 중립이어야 한다.
    expect(reasonText("poll_failed")).toBe("상태를 확인하지 못했습니다");
    expect(reasonText("poll_failed")).not.toMatch(/빌드/);
  });

  it("rm 링크 사유(d194): target_path_redirected 는 중간 단계 링크만, 대상 자신이 링크면 링크만 지운다 -- 옛 「대상 자신이 링크여도 거부」 아님", () => {
    // 2026-10-28 사용자 결정으로 10-27 「rm 도 똑같이 거부」를 뒤집었다 -- 옛 문장은 이제 거짓이다(대상 자신은 링크만 지워진다).
    expect(reasonText("target_path_redirected")).toBe("삭제 대상 경로의 중간 단계에 심볼릭 링크가 있어 삭제하지 않았습니다(도구는 "
      + "시작되지 않았습니다) — 관리 디렉토리 아래 경로의 중간 단계가 링크면 거부합니다(대상 자신이 링크면 링크만 지웁니다). 링크를 "
      + "거치지 않는 경로로 다시 요청하세요");
    expect(reasonText("target_path_redirected")).not.toMatch(/대상 자신이 링크여도/);
    // sync 의 두 코드는 그대로(sync 는 마지막 이름까지 링크면 거부)
    expect(reasonText("destination_path_redirected")).toMatch(/어느 단계든 링크면 거부합니다/);
    // 미리보기-실행 종류 묶기 거부: 도구 전이라 데이터는 그대로, 새 미리보기를 보라
    expect(reasonText("target_changed_since_preview")).toBe("미리보기 이후 삭제 대상의 종류가 바뀌었거나 대상이 없어져 실행하지 "
      + "않았습니다(예: 링크였던 경로가 디렉토리로 바뀜) — 데이터는 바뀌지 않았습니다. 지금 경로를 확인하고 다시 요청해 새 미리보기를 보세요");
  });
});
