import { expect, test } from "@playwright/test";
import { BASE_URL } from "./harness/env";
import { apiLogin } from "./helpers/session";
import { assertLayoutSane } from "./helpers/layout";

// E17 — 관리자 비밀번호 재설정과 세션 무효화(2026-11-03, 설계 v6 §5 「운영자 구제」·선택 4종). 단위 테스트가 못 보는 것: 실
// 서버의 세션 두 개 -- 세션 관리자(admin)가 계정 화면의 「비밀번호 재설정」 창으로 새 비밀번호를 **봉인해** 보내면(실 브라우저
// WebCrypto ↔ 파이썬 서버, 용도 admin_password_reset), 그 계정으로 로그인해 둔 **다른 브라우저**의 다음 요청이 401
// session_revoked 가 되어 로그인 화면으로 가고 「비밀번호가 바뀌어 로그인이 끝났습니다」를 한 번 보이는가. 옛 비밀번호는 401,
// 새 비밀번호는 200 인가. 그 조치가 보안 이벤트 화면에 남고, 그 화면이 1280·375 레이아웃 불변식(L1~L4)을 지키는가.

const RUN = Date.now().toString(36);
const USER = `e17u${RUN}`;
const OLD_PW = "e17-old-pw";
const NEW_PW = "e17-new-pw!";

test.describe("E17 — 관리자 비밀번호 재설정·세션 무효화", () => {
  test("재설정하면 그 계정의 다른 브라우저는 로그인 화면에서 이유를 보고, 새 비밀번호로만 들어온다", async ({ page, browser }) => {
    await apiLogin(page);
    const created = await page.request.post("/api/admin/accounts",
      { data: { username: USER, password: OLD_PW, role: "user" } });
    expect(created.status(), await created.text()).toBe(201);

    // 대상 계정의 다른 브라우저(세션 쿠키)
    const other = await browser.newContext({ baseURL: BASE_URL });
    const page2 = await other.newPage();
    const login = await page2.request.post("/api/auth/login", { data: { username: USER, password: OLD_PW } });
    expect(login.status(), await login.text()).toBe(200);
    await page2.goto("/jobs");
    await expect(page2.getByRole("heading", { name: "전체 작업", level: 1 })).toBeVisible();
    // 탭은 닫고 쿠키(컨텍스트)만 남긴다 -- 열린 탭의 3초 폴링이 재설정 직후 401 을 먼저 받아 안내를 소비하면 아래 단언이 경합한다.
    await page2.close();

    // 관리자: 계정 화면 → 그 행의 「비밀번호 재설정」(자기 행엔 없다)
    await page.goto("/admin/accounts");
    const row = page.locator("main table tbody tr").filter({ hasText: USER });
    await expect(row).toHaveCount(1);
    const selfRow = page.locator("main table tbody tr").filter({ has: page.getByText("자기 계정은 변경할 수 없습니다") });
    await expect(selfRow).toHaveCount(1);
    await expect(selfRow.getByRole("button", { name: /비밀번호 재설정$/ })).toHaveCount(0);
    await row.getByRole("button", { name: `${USER} 비밀번호 재설정` }).click();
    const dlg = page.getByRole("dialog", { name: "비밀번호 재설정" });
    await dlg.getByLabel("새 비밀번호", { exact: true }).fill(NEW_PW);
    await dlg.getByLabel("새 비밀번호 확인", { exact: true }).fill(NEW_PW);
    await dlg.getByRole("button", { name: "재설정", exact: true }).click();
    await expect(dlg).toHaveCount(0);
    await expect(page.getByRole("status").filter({ hasText: `${USER} 의 비밀번호를 재설정했습니다 — ` })).toBeVisible();

    // 대상의 다른 브라우저(같은 쿠키의 새 탭): 첫 요청(me)이 401 session_revoked → 로그인 화면 + 한 번의 안내
    const page3 = await other.newPage();
    await page3.goto("/jobs");
    await expect(page3.getByRole("heading", { name: "로그인", level: 1 })).toBeVisible();
    await expect(page3.getByRole("status").filter({ hasText: "비밀번호가 바뀌어 로그인이 끝났습니다 — 새 비밀번호로 다시 로그인하세요" }))
      .toBeVisible();
    // 한 번만 -- 다시 열면 안내가 없다(sessionStorage 키를 읽고 지웠다, 서버는 그 쿠키를 비웠다 → not_authenticated)
    await page3.reload();
    await expect(page3.getByRole("heading", { name: "로그인", level: 1 })).toBeVisible();
    await expect(page3.getByText("비밀번호가 바뀌어 로그인이 끝났습니다", { exact: false })).toHaveCount(0);

    // 옛 비밀번호 401, 새 비밀번호 200(봉인한 값이 그대로 저장됐다)
    const oldLogin = await page3.request.post("/api/auth/login", { data: { username: USER, password: OLD_PW } });
    expect(oldLogin.status(), await oldLogin.text()).toBe(401);
    const newLogin = await page3.request.post("/api/auth/login", { data: { username: USER, password: NEW_PW } });
    expect(newLogin.status(), await newLogin.text()).toBe(200);
    await other.close();

    // 관리자 세션은 그대로이고, 조치가 보안 이벤트에 남는다(종류 코드 칸으로 찾는다 -- 라벨은 필터 select 에도 있다)
    await page.goto("/admin/security");
    await expect(page.getByRole("heading", { name: "보안 이벤트", level: 1 })).toBeVisible();
    await expect(page.getByText("불러오는 중…", { exact: true })).toHaveCount(0);
    // 종류 코드 칸이 **정확히** password_admin_reset 인 행 -- 부분 일치(hasText)는 같은 조치의 완료 안내 메일 이벤트(메시지에
    // 「(password_admin_reset)」이 들어 있고 더 나중이라 맨 위)를 먼저 잡았다(2026-11-03 첫 실행).
    const ev = page.locator("main table tbody tr")
      .filter({ has: page.getByText("password_admin_reset", { exact: true }) }).first();
    await expect(ev).toBeVisible();
    await expect(ev).toContainText("관리자 비밀번호 재설정");
    // 이벤트 표(th 6 + 행마다 td 6) -- 375 에서 표는 래퍼 안에서만 넘치고, 상태 dl 은 1열로 접힌다.
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 900 });
      await assertLayoutSane(page, { sidebarFixed: width >= 768, minTableCells: 12 });
    }
  });
});
