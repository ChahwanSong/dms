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
    await page.getByRole("button", { name: "로그인" }).click();

    // Home 이 admin 을 /admin/dashboard 로 보낸다(router.tsx Home). URL 단언이라
    // 셀렉터가 아무것도 못 찾고도 통과하는 공허한 초록이 원리상 불가능하다.
    await expect(page).toHaveURL(/\/admin\/dashboard$/);

    // 실쿠키의 직접 증거. msw 로는 여기까지 못 온다.
    const cookies = await page.context().cookies();
    expect(cookies.map((c) => c.name)).toContain("dms_session");

    // 로그아웃 왕복이 실제로 200 으로 끝난 것을 재료로 삼는다.
    //
    // 여기서 「클릭 직후 /login 으로 튄다」를 단언하지 않는 이유는 실측이다: 현 앱은
    // 로그아웃 후에도 화면이 /admin/dashboard 에 그대로 남는다(30s 관찰). useLogout 의
    // onSettled 가 qc.clear() 로 me 쿼리를 **제거**하는데, 제거된 쿼리의 관찰자는
    // 마지막 결과를 그대로 들고 있고 재조회도 폴링도 다시 걸리지 않아 RequireRole 이
    // 401 을 볼 기회 자체가 없다. 즉 화면 전환은 다음 내비게이션/요청 때 일어난다.
    // 이 사실을 단언으로 굳히지도(버그를 계약으로 만드는 짓) 없는 동작을 기대하지도
    // 않는다 -- e2e 가 지켜야 할 계약은 **서버 세션이 죽었다**는 쪽이다.
    const logoutDone = page.waitForResponse(
      (r) => r.url().endsWith("/api/auth/logout") && r.request().method() === "POST");
    await page.getByRole("button", { name: "로그아웃" }).click();
    expect((await logoutDone).status()).toBe(200);

    // 세션이 클라이언트 캐시에서만 지워진 게 아니라 **서버에서** 죽었는지 본다 --
    // 새 하드 내비게이션이라 앞선 응답 캐시가 개입할 여지가 없다. msw 로는 절대
    // 도달할 수 없는 단언이다(핸들러는 언제나 200 을 준다).
    await page.goto("/admin/accounts");
    await expect(page).toHaveURL(/\/login$/);
    expect((await page.context().cookies()).map((c) => c.name))
      .not.toContain("dms_session");
  });
});
