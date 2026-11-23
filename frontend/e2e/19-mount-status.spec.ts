import { expect, test, type Page } from "@playwright/test";
import { STORAGE_NAME } from "./harness/env";
import { apiLogin } from "./helpers/session";

// E19 — 스토리지 > 마운트 현황(2026-11-23 사용자 요청 「노드들의 스토리지 마운트 현황을 한눈에」). 단위 테스트(jsdom)가 못 보는
// 것을 여기서 본다: 실 서버 GET /api/admin/mount-status 의 칸이 표에 그려지는가, 그리고 고정 머리·첫 열(sticky)의 z 가 래퍼의
// `isolate` 쌓임 맥락에 갇혀 body 끝 포털(측면 시트·덮개)을 뚫지 않는가 -- 사용량 TargetTable 의 z-index 사고(고정 칸이 Dialog
// 덮개 위로 올라왔다)의 재발 그물이다. jsdom 은 쌓임 맥락·elementFromPoint 를 계산하지 않는다.
//
// 하네스 전제(global-setup): 노드 e2e-node(agent --once, direct 모드 -- 공용 디렉터리의 덮는 마운트를 모르므로 공용 칸 글자는
// 「있음」이고 단언하지 않는다), 스토리지 e2e-store(가짜 mountinfo 로 마운트 지점 = Ready), DMS_ARTIFACT_BASE_URI 설정.

const PATH = "/admin/storages/mounts?view=table";

async function visitMatrix(page: Page): Promise<void> {
  await page.goto(PATH);
  await expect(page.getByRole("heading", { name: "마운트 현황", level: 1 })).toBeVisible();
  await expect(page.getByRole("table").getByRole("button", { name: "e2e-node", exact: true })).toBeVisible();
}

/** 고정 칸(모서리 「노드」 머리·첫 스토리지 열 머리·노드 행 머리) 가운데 좌표에서 맨 위에 **그려진** 요소가 매트릭스(main 안의
 *  표) 밖(시트·덮개)인가 -- 표 안이면 그 좌표를 돌려준다. 모달이 열리면 Radix 가 뒤 화면 전체에 pointer-events: none 을 걸어
 *  elementFromPoint 가 표를 아예 건너뛴다(그대로 재면 쌓임 순서와 무관하게 늘 초록 -- isolate 를 지운 뮤테이션이 살아남았다).
 *  잴 때만 표에 pointer-events 를 되돌려 「누가 위에 그려졌나」를 잰다. */
async function topmostOutsideTable(page: Page, points: { x: number; y: number }[]): Promise<string[]> {
  return page.evaluate((pts) => {
    const style = document.createElement("style");
    style.textContent = "main table, main table * { pointer-events: auto !important; }";
    document.head.appendChild(style);
    try {
      return pts.flatMap(({ x, y }) => {
        const el = document.elementFromPoint(x, y);
        if (el === null) return [`(${x},${y}): 요소 없음`];
        // 시트는 body 끝 포털(main 밖)이다 -- 시트 안의 자기 표(스토리지별 마운트)는 매트릭스가 아니다.
        return el.closest("main table") !== null ? [`(${x},${y}): ${el.tagName} ${(el.textContent ?? "").slice(0, 30)}`] : [];
      });
    } finally {
      style.remove();
    }
  }, points);
}

async function stickyPoints(page: Page): Promise<{ x: number; y: number }[]> {
  const table = page.getByRole("table");
  const boxes = await Promise.all([
    table.getByRole("columnheader", { name: "노드", exact: true }).boundingBox(),
    table.getByRole("columnheader", { name: new RegExp(`^${STORAGE_NAME}`) }).boundingBox(),
    table.getByRole("rowheader").first().boundingBox(),
  ]);
  const width = page.viewportSize()?.width ?? 1280;
  return boxes.map((b) => {
    expect(b, "고정 칸의 좌표를 못 쟀다 -- 셀렉터가 화면과 어긋났다").not.toBeNull();
    // 가로로 민 표에서 열 머리가 화면 오른쪽 밖일 수 있다 -- 화면 안으로 접는다(밖이면 elementFromPoint 가 null)
    return { x: Math.min(b!.x + Math.min(b!.width / 2, 20), width - 4), y: b!.y + Math.min(b!.height / 2, 10) };
  });
}

test.describe("E19 마운트 현황", () => {
  test("실 서버 칸이 그려지고, 측면 시트가 고정 칸 위에 온다(1280)", async ({ page }) => {
    await apiLogin(page);
    await visitMatrix(page);
    await expect(page.getByLabel(`e2e-node · ${STORAGE_NAME}: 정상`, { exact: true })).toBeVisible();
    await expect(page.getByRole("table").getByRole("columnheader", { name: /DMS 공용/ })).toBeVisible();

    const points = await stickyPoints(page);
    await page.getByRole("table").getByRole("button", { name: "e2e-node", exact: true }).click();
    const sheet = page.getByRole("dialog");
    await expect(sheet.getByRole("heading", { name: "e2e-node" })).toBeVisible();
    expect(await topmostOutsideTable(page, points), "고정 칸이 시트·덮개를 뚫고 위에 그려졌다(isolate 회귀)").toEqual([]);
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();
    // 닫으면 그 노드 버튼으로 포커스가 돌아온다(키보드 진입점)
    await expect(page.getByRole("table").getByRole("button", { name: "e2e-node", exact: true })).toBeFocused();
  });

  test("가로로 민 표에서도 시트가 고정 칸 위에 온다(375)", async ({ page }) => {
    await apiLogin(page);
    await page.setViewportSize({ width: 375, height: 667 });
    await visitMatrix(page);
    const wrapper = page.locator("main table").locator("xpath=..");
    // 375 에서 표는 타일·도구 줄 아래(첫 화면 밖)다 -- 표를 화면에 들인 뒤 민다.
    await wrapper.scrollIntoViewIfNeeded();
    const scrolled = await wrapper.evaluate((el) => { el.scrollLeft = 80; return el.scrollLeft; });
    expect(scrolled, "375 에서 표 래퍼가 가로로 넘치지 않는다 -- 이 검사의 전제(민 상태)가 없다").toBeGreaterThan(0);
    // 민 상태에서도 첫 열은 고정이라 노드 버튼이 래퍼 왼쪽 끝에 그대로 있다(80px 밀려 잘리지 않는다)
    const button = page.getByRole("table").getByRole("button", { name: "e2e-node", exact: true });
    await expect(button).toBeInViewport();
    const [buttonBox, wrapperBox] = await Promise.all([button.boundingBox(), wrapper.boundingBox()]);
    expect(buttonBox!.x, "첫 열이 고정되지 않았다 -- 민 만큼 노드 이름이 왼쪽으로 사라졌다").toBeGreaterThanOrEqual(wrapperBox!.x);
    const points = await stickyPoints(page);
    await page.getByRole("table").getByRole("button", { name: "e2e-node", exact: true }).click();
    await expect(page.getByRole("dialog").getByRole("heading", { name: "e2e-node" })).toBeVisible();
    expect(await topmostOutsideTable(page, points), "고정 칸이 시트를 뚫고 위에 그려졌다(isolate 회귀)").toEqual([]);
  });
});
