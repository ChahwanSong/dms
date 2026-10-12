import { expect, test, type Page } from "@playwright/test";
import { STORAGE_NAME } from "./harness/env";
import { apiLogin } from "./helpers/session";
import { assertLayoutSane } from "./helpers/layout";

// E9 — 컨펌 대기 취소·단계 설명 툴팁(2026-10-12 사용자 요청: 「Pending 과 confirm pending 에 취소 버튼」, 「작업 단계 설명
// 툴팁」). 단위 테스트가 못 보는 것: 실 서버·실 컨트롤러로 rm 이 컨펌 대기까지 간 뒤 배너의 「컨펌하지 않고 취소」가 잡·요청을
// 끝내는가(리로드 없이 화면이 따라오는가), 설명 툴팁이 열린 채로도 1280·375 레이아웃 불변식(L1~L4)이 지켜지는가.

async function submitRm(page: Page, target: string): Promise<string> {
  const response = await page.request.post("/api/user/requests", {
    data: { operation: "rm", storage: STORAGE_NAME, target, options: { recursive: true }, priority: "mid",
            run_as_root: true },
  });
  expect(response.status(), await response.text()).toBe(202);
  return ((await response.json()) as { request_id: string }).request_id;
}

async function jobState(page: Page, rid: string): Promise<string> {
  const response = await page.request.get(`/api/user/requests/${rid}/jobs`);
  const jobs = (await response.json()) as { state: string }[];
  return jobs[0]?.state ?? "(no job)";
}

test.describe("E9 — 컨펌 대기 취소 · 상태 설명", () => {
  test.beforeEach(async ({ page }) => {
    await apiLogin(page);
  });

  test("컨펌 대기 rm 을 배너 「컨펌하지 않고 취소」로 끝낸다 — 잡·요청 Cancelled, 화면이 따라온다", async ({ page }) => {
    const rid = await submitRm(page, `e9-cancel-${Date.now().toString(36)}`);
    await expect.poll(() => jobState(page, rid), { timeout: 60_000, intervals: [500],
      message: "rm 이 컨펌 대기까지 가지 않았다" }).toBe("ConfirmPending");
    await page.goto(`/jobs/${rid}`);
    // 잡 카드에도 「취소」가 있다(예전엔 컨펌 대기에서 숨겼다)
    await expect(page.getByRole("button", { name: "취소", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "컨펌하지 않고 취소" }).click();
    await expect.poll(() => jobState(page, rid), { timeout: 15_000, intervals: [300] }).toBe("Cancelled");
    await expect(page.getByText("Cancelled", { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "컨펌하지 않고 취소" })).toHaveCount(0);
  });

  test("상태 배지·단계 설명 툴팁: 포커스·마우스로 열리고 Esc 로 닫힌다, 열린 채로 1280·375 레이아웃 불변식", async ({ page }) => {
    const rid = await submitRm(page, `e9-help-${Date.now().toString(36)}`);
    await expect.poll(() => jobState(page, rid), { timeout: 60_000, intervals: [500] }).toBe("ConfirmPending");
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/jobs");
      const pill = page.locator("main table tbody tr").first().locator("td span[tabindex='0']").first();
      await pill.hover();
      await expect(page.getByRole("tooltip")).toContainText("상태 코드");
      await assertLayoutSane(page, { sidebarFixed: width >= 768, minTableCells: 4 });
      await page.mouse.move(0, 0);
      await page.goto(`/jobs/${rid}`);
      const help = page.getByRole("button", { name: "작업 컨펌 단계 설명" });
      await help.focus();
      await expect(page.getByRole("tooltip")).toContainText("관문");
      await page.keyboard.press("Escape");
      await expect(page.getByRole("tooltip")).toHaveCount(0);
      const [scroll, client] = await page.evaluate(() =>
        [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
      expect(scroll, `${width}: 문서가 가로로 넘친다`).toBeLessThanOrEqual(client);
    }
    // 정리: 컨펌 대기 잡을 남기지 않는다
    const jobs = (await (await page.request.get(`/api/user/requests/${rid}/jobs`)).json()) as { job_id: string }[];
    await page.request.post(`/api/user/jobs/${jobs[0].job_id}:cancel`);
  });
});
