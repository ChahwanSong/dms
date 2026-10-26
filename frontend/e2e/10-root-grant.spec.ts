import { expect, test } from "@playwright/test";
import { BASE_URL } from "./harness/env";
import { apiLogin } from "./helpers/session";
import { assertLayoutSane } from "./helpers/layout";

// E11 — 「전체 PV·직접 경로 권한」 부여(2026-10-12 사용자 보고: 「관리 -> 계정 에서 일반 사용자명에게 admin 역할을 줬어. 근데
// root로 실행 체크 옵션이 작업 등록할때 안뜨네」 → 2026-10-26 LDAP 정리로 k8s 신원 모드 전용이 된 뒤의 의미). 모든 작업이 root
// 라 이 자격(서버 can_run_as_root -- 계정 화면의 같은 PUT)이 주는 것은 k8s 권한 맵핑 면제다: 직접 경로·전체 PV·「권한 기준
// 사용자」·scan. 단위 테스트가 못 보는 것: 실 서버의 세션 두 개 -- 특권 관리자(admin, 하네스 기본 특권 목록)가 계정 화면에서
// 새 admin 계정에 「자격 주기」를 누르면, **그 계정의 다른 세션**이 다시 로그인하지 않고도 단일 작업 화면에서 면제자 화면
// (권한 기준 사용자 칸·scan 연산)을 보고, 「자격 빼기」 뒤엔 다시 맵핑 범위 안내만 보는가. 계정 표가 새 열을 단 채로
// 1280·375 레이아웃 불변식(L1~L4)을 지키는가.

const RUN = Date.now().toString(36);
const NEWADM = `e11adm${RUN}`;
const PW = "e11-admin-pw";
const LABEL = "전체 PV·직접 경로 권한";

test.describe("E11 — 전체 PV·직접 경로 권한 부여", () => {
  test("admin 역할만으론 면제자 화면이 아니다 → 특권 관리자가 권한을 주면 생기고, 빼면 사라진다", async ({ page, browser }) => {
    await apiLogin(page);
    const created = await page.request.post("/api/admin/accounts",
      { data: { username: NEWADM, password: PW, role: "admin" } });
    expect(created.status(), await created.text()).toBe(201);

    // 새 admin 의 세션(따로 로그인 -- 다시 로그인하지 않아도 바뀌는지 본다)
    const other = await browser.newContext({ baseURL: BASE_URL });
    const page2 = await other.newPage();
    const login = await page2.request.post("/api/auth/login", { data: { username: NEWADM, password: PW } });
    expect(login.status(), await login.text()).toBe(200);
    const scopeBox = page2.getByLabel("권한 기준 사용자", { exact: true });
    const scanOption = page2.getByLabel("연산", { exact: true }).locator('option[value="scan"]');
    await page2.goto("/jobs/new");
    await expect(page2.getByRole("heading", { name: "단일 작업" })).toBeVisible();
    // 목록 밖 관리자: 맵핑 범위 안내 한 줄, 권한 기준 사용자 칸·scan 없음(scan 은 특권 운영자만 -- 서버 me.user_operations)
    await expect(page2.getByText(/직접 경로·사용자 지정은 계정 화면의 「전체 PV·직접 경로 권한」이 필요합니다/)).toBeVisible();
    await expect(scopeBox).toHaveCount(0);
    await expect(scanOption).toHaveCount(0);
    // 옛 ldap 모드의 root 체크박스는 어디에도 없다
    await expect(page2.getByLabel("root 권한으로 실행", { exact: true })).toHaveCount(0);

    // 특권 관리자: 계정 화면 「전체 PV·직접 경로 권한」 열 → 자격 주기(확인 창)
    await page.goto("/admin/accounts");
    await expect(page.getByRole("columnheader", { name: LABEL })).toBeVisible();
    const row = page.locator("main table tbody tr").filter({ hasText: NEWADM });
    await expect(row).toContainText("없음");
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 900 });
      await assertLayoutSane(page, { sidebarFixed: width >= 768, minTableCells: 8 });
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    await row.getByRole("button", { name: `${NEWADM} ${LABEL} 주기` }).click();
    const dlg = page.getByRole("dialog", { name: `${LABEL} 주기` });
    await expect(dlg).toContainText(`${NEWADM} 에게 ${LABEL}을 줍니다.`);
    await dlg.getByRole("button", { name: "자격 주기", exact: true }).click();
    await expect(dlg).toHaveCount(0);
    await expect(row).toContainText("있음 · 부여");

    // 새 admin: 다시 로그인하지 않고 화면만 다시 열면 면제자 화면(권한 기준 사용자 칸·scan)
    await page2.goto("/jobs/new");
    await expect(scopeBox).toBeVisible();
    await expect(scanOption).toHaveCount(1);
    await expect(page2.getByText(/모든 작업은 root 로 실행됩니다 — 전체 PV·직접 경로 권한이 있어/)).toBeVisible();
    const me = await (await page2.request.get("/api/auth/me")).json() as { can_run_as_root: boolean; unrestricted: boolean };
    expect(me.can_run_as_root).toBe(true);
    expect(me.unrestricted).toBe(true);

    // 빼기 → 다시 맵핑 범위 안내만
    await row.getByRole("button", { name: `${NEWADM} ${LABEL} 빼기` }).click();
    await expect(row).toContainText("없음");
    await page2.goto("/jobs/new");
    await expect(page2.getByText(/직접 경로·사용자 지정은 계정 화면의 「전체 PV·직접 경로 권한」이 필요합니다/)).toBeVisible();
    await expect(scopeBox).toHaveCount(0);
    await other.close();
  });
});
