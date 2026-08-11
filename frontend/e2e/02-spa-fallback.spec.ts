import { expect, test } from "@playwright/test";
import { apiLogin } from "./helpers/session";

// E2 — SPA fallback. 단위 테스트가 구조적으로 못 보는 것을 본다: 딥링크는 **서버**가
// 먼저 답한다. vitest 는 MemoryRouter 위에서 라우팅만 보고, `npm run dev` 는 vite dev
// 서버가 자체 fallback 으로 index.html 을 주므로 app.py 의 spa_fallback 코드가 아예
// 안 돈다 -- 즉 "배포되는 것"(dist 를 uvicorn 이 서빙)의 이 경로는 지금까지 어떤
// 테스트도 밟은 적이 없다. 하네스가 dist 서빙으로 띄우기 때문에 여기서 처음 밟힌다.
//
// 단언 재료는 HTTP 상태·URL·랜드마크 하나로 제한한다(설계 §2.1) -- 문구는 vitest 영토다.
test.describe("E2 SPA fallback", () => {
  test("딥링크 하드 내비게이션이 서버 fallback 으로 복원된다", async ({ page }) => {
    await apiLogin(page);

    // 라우터가 아니라 **서버**가 답하는 요청이다(주소창에 붙여넣기·새로고침·북마크와
    // 같은 경로). 디스크에 /admin/accounts 라는 파일은 없으므로 spa_fallback 만이
    // 200 을 만들 수 있다 -- 이 줄이 200 이 아니면 포탈은 새로고침으로 깨진다.
    const response = await page.goto("/admin/accounts");
    expect(response, "page.goto 가 응답을 주지 않았다").not.toBeNull();
    expect(response!.status(), await response!.text()).toBe(200);

    // 서버가 리다이렉트로 눙친 게 아니라 요청한 그 경로 그대로 살아 있어야 한다.
    await expect(page).toHaveURL(/\/admin\/accounts$/);

    // 200 만으로는 부족하다: 엉뚱한 200(빈 문서·JSON)도 상태만은 200 이다. 받은 것이
    // 정말 index.html 이고 클라이언트 라우터가 그 위에서 화면을 복원했다는 증거로
    // 랜드마크 하나를 본다. 셀렉터가 못 찾으면 그대로 빨개진다(공허한 초록 불가).
    await expect(page.getByRole("heading", { name: "계정" })).toBeVisible();
  });

  test("미지 경로는 역할 홈으로 수렴한다", async ({ page }) => {
    await apiLogin(page);

    // 존재하지 않는 경로도 서버는 index.html 로 받아 넘기고(그래야 아래 체인이
    // 시작된다), 그 다음은 클라이언트 몫이다: `*` -> `/` -> Home -> admin 이므로
    // /admin/dashboard. 서버가 404 를 주면 체인 자체가 시작되지 않아 URL 이
    // /no/such/route 에 멈춘다 -- 이 단언이 서버·클라이언트 양쪽을 함께 잡는다.
    await page.goto("/no/such/route");
    await expect(page).toHaveURL(/\/admin\/dashboard$/);
  });
});
