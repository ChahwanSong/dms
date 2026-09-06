import { expect, test } from "@playwright/test";
import { ADMIN } from "./harness/env";

// E1 — 부팅 + 세션. msw 단위 테스트가 구조적으로 못 보는 것을 본다: 실제 HTTP 왕복과
// 서버가 굽는 dms_session 쿠키다. 단위 테스트의 msw 는 쿠키를 흉내조차 내지 않고,
// 로그아웃이 서버 세션을 정말 죽였는지도 알 수 없다(핸들러가 늘 200 을 준다).
// 단언 재료는 URL·역할·쿠키 존재로 제한한다(설계 §2.1) -- 문구는 vitest 영토다.
test.describe("E1 부팅+세션", () => {
  test("미인증이면 보호 라우트가 로그인으로 차단된다", async ({ page }) => {
    await page.goto("/admin/accounts");
    // 하드 내비게이션 -> 서버 index.html -> RequireRole 이 /api/auth/me 401 을 보고
    // /login 으로 돌린다. 실 401 왕복이 이 단언의 재료다.
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByLabel("사용자명")).toBeVisible();
  });

  test("로그인->관리자 홈->로그아웃->재차단", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("사용자명").fill(ADMIN.username);
    await page.getByLabel("비밀번호").fill(ADMIN.password);
    // 비밀번호 전송 봉인(2026-09-07)의 유일한 실브라우저 증거: 실제 Chrome 의
    // WebCrypto 가 봉인한 본문을 실제 파이썬 서버가 열어 200 을 준다. 와이어에
    // 평문 password 가 없다는 것도 여기서 본다(msw 단위 테스트는 서버를 흉내낼 뿐).
    const loginReq = page.waitForRequest(
      (r) => r.url().endsWith("/api/auth/login") && r.method() === "POST");
    await page.getByRole("button", { name: "로그인" }).click();
    const loginBody = (await loginReq).postDataJSON() as
      { username: string; password?: string; password_enc?: { version: number; kid: string } };
    expect(loginBody.username).toBe(ADMIN.username);
    expect(loginBody.password).toBeUndefined();
    expect(loginBody.password_enc?.version).toBe(1);
    expect((await loginReq).postData()).not.toContain(ADMIN.password);

    // Home 이 admin 을 /admin/dashboard 로 보낸다(router.tsx Home). URL 단언이라
    // 셀렉터가 아무것도 못 찾고도 통과하는 공허한 초록이 원리상 불가능하다.
    await expect(page).toHaveURL(/\/admin\/dashboard$/);

    // 실쿠키의 직접 증거. msw 로는 여기까지 못 온다.
    const cookies = await page.context().cookies();
    expect(cookies.map((c) => c.name)).toContain("dms_session");

    // 로그아웃 왕복이 실제로 200 으로 끝난 것을 재료로 삼는다.
    //
    // 슬라이스 29 가 「클릭 즉시 /login 도달」을 계약으로 만들었다(AppShell 의
    // 명시 nav -- qc.clear() 는 관찰자 재조회·폴링까지 멈춰 자동 전환 통로가
    // 없다는 실측이 근거). 아래 URL 단언이 그 계약이고, 서버 세션 파기는 그
    // 다음의 하드 내비게이션 단언이 별도로 지킨다 -- 두 계약은 독립이다.
    const logoutDone = page.waitForResponse(
      (r) => r.url().endsWith("/api/auth/logout") && r.request().method() === "POST");
    await page.getByRole("button", { name: "로그아웃" }).click();
    expect((await logoutDone).status()).toBe(200);
    await expect(page).toHaveURL(/\/login$/);

    // 세션이 클라이언트 캐시에서만 지워진 게 아니라 **서버에서** 죽었는지 본다 --
    // 새 하드 내비게이션이라 앞선 응답 캐시가 개입할 여지가 없다. msw 로는 절대
    // 도달할 수 없는 단언이다(핸들러는 언제나 200 을 준다).
    await page.goto("/admin/accounts");
    await expect(page).toHaveURL(/\/login$/);
    expect((await page.context().cookies()).map((c) => c.name))
      .not.toContain("dms_session");
  });
});
