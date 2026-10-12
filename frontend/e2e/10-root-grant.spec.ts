import { expect, test } from "@playwright/test";
import { BASE_URL } from "./harness/env";
import { apiLogin } from "./helpers/session";
import { assertLayoutSane } from "./helpers/layout";

// E11 — root 실행 자격 부여(2026-10-12 사용자 보고: 「관리 -> 계정 에서 일반 사용자명에게 admin 역할을 줬어. 근데 root로 실행
// 체크 옵션이 작업 등록할때 안뜨네」). 단위 테스트가 못 보는 것: 실 서버의 세션 두 개 -- 특권 관리자(admin, 하네스 기본 특권
// 목록)가 계정 화면에서 새 admin 계정에 「자격 주기」를 누르면, **그 계정의 다른 세션**이 다시 로그인하지 않고도 작업 등록에서
// 「root 권한으로 실행」을 보고(기본 켜짐), 「자격 빼기」 뒤엔 다시 안내만 보는가. 계정 표가 새 열을 단 채로 1280·375 레이아웃
// 불변식(L1~L4)을 지키는가.

const RUN = Date.now().toString(36);
const NEWADM = `e11adm${RUN}`;
const PW = "e11-admin-pw";

test.describe("E11 — root 실행 자격 부여", () => {
  test("admin 역할만으론 체크박스가 없다 → 특권 관리자가 자격을 주면 생기고, 빼면 사라진다", async ({ page, browser }) => {
    await apiLogin(page);
    const created = await page.request.post("/api/admin/accounts",
      { data: { username: NEWADM, password: PW, role: "admin" } });
    expect(created.status(), await created.text()).toBe(201);

    // 새 admin 의 세션(따로 로그인 -- 다시 로그인하지 않아도 바뀌는지 본다)
    const other = await browser.newContext({ baseURL: BASE_URL });
    const page2 = await other.newPage();
    const login = await page2.request.post("/api/auth/login", { data: { username: NEWADM, password: PW } });
    expect(login.status(), await login.text()).toBe(200);
    const rootBox = page2.getByLabel("root 권한으로 실행", { exact: true });
    await page2.goto("/jobs/new");
    await expect(page2.getByRole("heading", { name: "단일 작업" })).toBeVisible();
    await expect(page2.getByText(/「관리 → 계정」의 root 실행에서 줄 수 있습니다/)).toBeVisible();
    await expect(rootBox).toHaveCount(0);

    // 특권 관리자: 계정 화면 「root 실행」 열 → 자격 주기(확인 창)
    await page.goto("/admin/accounts");
    const row = page.locator("main table tbody tr").filter({ hasText: NEWADM });
    await expect(row).toContainText("없음");
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 900 });
      await assertLayoutSane(page, { sidebarFixed: width >= 768, minTableCells: 8 });
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    await row.getByRole("button", { name: `${NEWADM} root 실행 자격 주기` }).click();
    const dlg = page.getByRole("dialog", { name: "root 실행 자격 주기" });
    await expect(dlg).toContainText(`${NEWADM} 에게 root 실행 자격을 줍니다.`);
    await dlg.getByRole("button", { name: "자격 주기", exact: true }).click();
    await expect(dlg).toHaveCount(0);
    await expect(row).toContainText("있음 · 부여");

    // 새 admin: 다시 로그인하지 않고 화면만 다시 열면 체크박스가 있고 기본 켜짐(자격 있는 관리자의 기본 root)
    await page2.goto("/jobs/new");
    await expect(rootBox).toBeVisible();
    await expect(rootBox).toBeChecked();
    const me = await (await page2.request.get("/api/auth/me")).json() as { can_run_as_root: boolean };
    expect(me.can_run_as_root).toBe(true);

    // 빼기 → 다시 안내만
    await row.getByRole("button", { name: `${NEWADM} root 실행 자격 빼기` }).click();
    await expect(row).toContainText("없음");
    await page2.goto("/jobs/new");
    await expect(page2.getByText(/「관리 → 계정」의 root 실행에서 줄 수 있습니다/)).toBeVisible();
    await expect(rootBox).toHaveCount(0);
    await other.close();
  });
});
