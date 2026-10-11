import { expect, test, type Page } from "@playwright/test";
import { STORAGE_NAME } from "./harness/env";
import { apiLogin } from "./helpers/session";

// E8 — sync 배치 자동 확인(2026-10-11 사용자 결정: 단건 sync 는 사람이 승인, 배치 sync 는 자동 승인을 옵션으로 —
// 기본 꺼짐). 단위 테스트가 못 보는 것을 여기서 본다: 생성 화면의 체크박스 → 실 서버 저장 → **실 오케스트레이터가
// PreviewReady 에서 사람 대신 확인** → stub 러너로 실행까지 끝나는가, 그리고 끈 배치는 확인 대기에서 멈추는가.
//
// 하네스 전제(global-setup): 오케스트레이터·스테퍼 1초 틱, admin 은 특권 목록(배치 생성의 3중 게이트) 안.

async function batchOf(page: Page, bid: string): Promise<{ status: string; auto_confirm?: boolean;
                                                         auto_confirm_by?: string | null }> {
  const response = await page.request.get(`/api/admin/batches/${bid}`);
  expect(response.status(), await response.text()).toBe(200);
  return response.json();
}

test.describe("E8 — sync 배치 자동 확인", () => {
  test.beforeEach(async ({ page }) => {
    await apiLogin(page);
  });

  test("생성 화면에서 「미리보기 후 자동 실행」을 켠 sync 배치는 「배치 확인」 없이 끝까지 실행된다", async ({ page }) => {
    const stamp = Date.now().toString(36);
    await page.goto("/admin/batches/new");
    await page.getByLabel("연산").selectOption("sync");
    const src = page.getByLabel("소스 스토리지");
    await expect(src.getByRole("option", { name: STORAGE_NAME })).toBeAttached();
    await src.selectOption(STORAGE_NAME);
    await page.getByLabel("목적지 스토리지").selectOption(STORAGE_NAME);
    await page.getByLabel("1행 소스").fill(`auto-src-${stamp}`);
    await page.getByLabel("1행 목적지").fill(`auto-dst-${stamp}`);
    const auto = page.getByLabel("미리보기 후 자동 실행");
    await expect(auto).not.toBeChecked();                                   // 기본 꺼짐
    await auto.check();
    await page.getByRole("button", { name: "배치 생성" }).click();
    await page.waitForURL(/\/admin\/batches\/[0-9a-f]{32}$/);
    const bid = page.url().split("/").pop()!;
    const created = await batchOf(page, bid);
    expect(created.auto_confirm).toBe(true);
    expect(created.auto_confirm_by).toBe("admin");
    // 「배치 확인」을 누르지 않는다 -- 오케스트레이터가 미리보기 → 확인 대기 → (자동 확인) → 실행 → 완료로 끌고 간다.
    await expect.poll(async () => (await batchOf(page, bid)).status, {
      timeout: 60_000, intervals: [500],
      message: `자동 확인 배치 ${bid} 가 60s 안에 Completed 가 되지 않았다 -- controller.log(batch_auto_confirm*)를 보라`,
    }).toBe("Completed");
    await page.reload();
    await page.getByText("실행 설정", { exact: true }).click();
    await expect(page.getByText("자동 — 미리보기가 끝나면 바로 실행 (켠 사람 admin)")).toBeVisible();
    // 375 폭: 끝난 자동 배치 = 헤더가 가장 넓은 경우(버튼 넷 + 「자동 실행 (켠 사람)」) -- 헤더 줄이 줄바꿈되지 않으면
    // 문서가 가로로 넘쳤다(테스트베드 실측 455/375, 자동 아닌 끝난 배치도 387~394).
    await page.setViewportSize({ width: 375, height: 800 });
    await page.reload();
    await expect(page.getByText("자동 실행 (켠 사람 admin)")).toBeVisible();
    const [scroll, client] = await page.evaluate(() =>
      [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
    expect(scroll, `375 에서 문서가 가로로 넘친다(${scroll} > ${client})`).toBeLessThanOrEqual(client);
  });

  test("끈 채로 만든 sync 배치는 확인 대기에서 멈춘다 — 사람이 「배치 확인」을 눌러야 실행된다", async ({ page }) => {
    const stamp = Date.now().toString(36);
    const response = await page.request.post("/api/admin/batches", {
      data: { operation: "sync", max_concurrency: 1, options: {}, name: `manual-${stamp}`,
              items: [{ source_storage: STORAGE_NAME, source: `man-src-${stamp}`,
                        destination_storage: STORAGE_NAME, destination: `man-dst-${stamp}` }] },
    });
    expect(response.status(), await response.text()).toBe(202);
    const bid = ((await response.json()) as { batch_id: string }).batch_id;
    await expect.poll(async () => (await batchOf(page, bid)).status, {
      timeout: 60_000, intervals: [500], message: `배치 ${bid} 가 확인 대기가 되지 않았다`,
    }).toBe("PreviewReady");
    // 오케스트레이터 틱(1초)을 여러 번 지나도 그대로다.
    await page.waitForTimeout(3_000);
    expect((await batchOf(page, bid)).status).toBe("PreviewReady");
    await page.goto(`/admin/batches/${bid}`);
    await expect(page.getByRole("status")).toContainText("「배치 확인」을 눌러야 실행됩니다");
    await expect(page.getByRole("button", { name: "자동 실행 끄기" })).toHaveCount(0);
    await page.getByRole("button", { name: "배치 확인" }).click();
    await page.getByRole("dialog", { name: "배치 확인" }).getByRole("button", { name: "실행 확인" }).click();
    await expect.poll(async () => (await batchOf(page, bid)).status, {
      timeout: 60_000, intervals: [500], message: `확인한 배치 ${bid} 가 60s 안에 끝나지 않았다`,
    }).toBe("Completed");
  });
});
