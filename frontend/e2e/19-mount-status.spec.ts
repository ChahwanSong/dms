import { expect, test, type Locator, type Page } from "@playwright/test";
import { STORAGE_NAME } from "./harness/env";
import { assertLayoutSane } from "./helpers/layout";
import {
  WIDE_FRESH_NODES, WIDE_HYPHEN, WIDE_LONG, WIDE_ROWS, WIDE_STORAGES, WIDE_UNKNOWN_REASON, wideMountStatus,
} from "./helpers/mountWide";
import { apiLogin } from "./helpers/session";

// E19 — 스토리지 > 마운트 현황(2026-11-23 사용자 요청 「노드들의 스토리지 마운트 현황을 한눈에」). 단위 테스트(jsdom)가 못 보는
// 것을 여기서 본다: 실 서버 GET /api/admin/mount-status 의 칸이 표에 그려지는가, 그리고 고정 머리·첫 열(sticky)의 z 가 래퍼의
// `isolate` 쌓임 맥락에 갇혀 body 끝 포털(측면 시트·덮개)을 뚫지 않는가 -- 사용량 TargetTable 의 z-index 사고(고정 칸이 Dialog
// 덮개 위로 올라왔다)의 재발 그물이다. jsdom 은 쌓임 맥락·elementFromPoint 를 계산하지 않는다.
//
// 100열(2026-11-25 사용자 요청 「테이블 칸 좌우가 너무 길어 … 스토리지가 100개가 될 수도 있어」): 합성 응답(helpers/mountWide --
// page.route 로 통째로, route.fetch 금지)으로 열 폭 고정(스토리지 6.5rem -- 넓은 화면에서도 늘지 않는다 = 옛 min-w-full 회귀 그물)·
// 페이지 가로 넘침 0·래퍼 안 가로·세로 스크롤과 래퍼 바닥(가로 스크롤바)이 첫 화면 안·고정 모서리/첫 열/머리와 민 방향 그늘·
// 머리 이름(26자 이하는 두 줄 안에 다)·경로(같은 /mgmt_storage 접두도 끝 조각으로 열마다 다르게)·키보드 스크롤·칸/열 머리 툴팁·열
// 따라가기 막대(PV 꼬리표 면을 지우지 않는다)·URL 열 거르기(?q=·?cols=problems)를 1280·1920 에서 잰다. jsdom 은 폭·글꼴·sticky 를
// 모른다.
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

/** 스토리지 열 머리 폭(px) -- 6.5rem(104) + 오른쪽 구분선 1px. 화면 폭과 상관없이 같아야 한다. */
async function storageHeadWidths(page: Page): Promise<number[]> {
  return page.locator("main thead th[data-col]").evaluateAll((ths) => ths.map((th) => th.getBoundingClientRect().width));
}
const offWidth = (ws: number[]) => ws.filter((w) => w < 103.5 || w > 105.5);

/** 올리면 툴팁이 뜨는가 -- 스크롤 이벤트가 막 연 툴팁을 닫을 수 있어(HoverTip 은 스크롤에 닫힌다) 다시 올려 본다. */
async function expectTip(page: Page, target: Locator, texts: string[]): Promise<void> {
  const tip = page.getByRole("tooltip");
  await expect(async () => {
    await page.mouse.move(0, 0);
    await target.hover();
    for (const t of texts) await expect(tip).toContainText(t, { timeout: 1_000 });
  }).toPass({ timeout: 10_000 });
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

  test("100열(합성 응답): 열 폭 고정·페이지 넘침 0·래퍼 안 스크롤·고정 머리/첫 열·그늘·열 거르기(1280·1920)", async ({ page }) => {
    await apiLogin(page);
    await page.route(/\/api\/admin\/mount-status$/, (route) => route.fulfill({ json: wideMountStatus() }));
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto(PATH);
    const table = page.getByRole("table");
    const heads = table.getByRole("columnheader");
    await expect(heads).toHaveCount(WIDE_STORAGES + 2);                    // 노드 + DMS 공용 + 100
    // ① 페이지 가로 넘침 0(L1)·셀 display(L2)·노드 버튼 한 줄(L4) -- 셀 = 머리 102 + 행 24 × (행 머리 + 공용 + 100)
    await assertLayoutSane(page, { minTableCells: (WIDE_STORAGES + 2) * (WIDE_ROWS + 1) });

    // ② 넘침은 래퍼 안에서만, 래퍼 바닥(가로 스크롤바)은 첫 화면 안(래퍼 높이 상한 = 화면에 남은 높이)
    const wrapper = page.locator("main table").locator("xpath=..");
    const m = await wrapper.evaluate((el) => ({ sw: el.scrollWidth, cw: el.clientWidth, sh: el.scrollHeight, ch: el.clientHeight }));
    expect(m.sw, "100열 표가 래퍼 안에서 가로로 넘치지 않는다 -- 전제가 없다").toBeGreaterThan(m.cw * 5);
    expect(m.sh, "24행 표가 래퍼 안에서 세로로 넘치지 않는다 -- 래퍼 높이 상한이 없다").toBeGreaterThan(m.ch);
    await expect.poll(() => wrapper.evaluate((el) => el.getBoundingClientRect().bottom),
                      { message: "래퍼 바닥(가로 스크롤바)이 첫 화면(900) 밖이다" }).toBeLessThanOrEqual(900);

    // ③ 스토리지 열 폭 고정(6.5rem + 구분선), 머리 줄 높이 상한
    const widths = await storageHeadWidths(page);
    expect(widths).toHaveLength(WIDE_STORAGES);
    expect(offWidth(widths), "스토리지 열 폭이 6.5rem 이 아니다(긴 이름·칩이 열을 넓혔다)").toEqual([]);
    expect(await page.locator("main thead tr").evaluate((tr) => tr.getBoundingClientRect().height)).toBeLessThanOrEqual(140);

    // ④ 긴 이름(26자 초과): 첫 줄은 말줄임(앞 조각), 둘째 줄은 끝 12자 -- 보이는 두 줄은 aria-hidden, 낭독은 sr-only 전체
    const longHead = page.locator(`main thead th[data-col="${WIDE_LONG}"]`);
    const nameLines = longHead.locator("div.h-8 > span[aria-hidden]");
    await expect(nameLines).toHaveCount(2);
    expect(await nameLines.first().evaluate((el) => el.scrollWidth > el.clientWidth), "긴 이름 첫 줄이 말줄임되지 않았다").toBe(true);
    await expect(nameLines.nth(1)).toHaveText(`…${WIDE_LONG.slice(-12)}`);
    // ④' 26자 이하 이름은 두 줄 안에 다 보인다(하이픈 이름 포함 -- 자연 줄바꿈이면 세 줄이라 끝이 잘렸다), 경로는 머리 상자를 넘지 않고
    //    보이는 글자가 열마다 다르다(같은 /mgmt_storage 접두를 끝 조각으로 가른다 -- 앞 조각이면 모두 「/mgmt_storage…」)
    const shown = await page.locator("main thead th[data-col]").evaluateAll((ths) => ths.map((th) => {
      const box = th.querySelector(":scope > div")!;
      const [nameBox, pathBox] = [box.children[0] as HTMLElement, box.children[1] as HTMLElement];
      const lines = [...nameBox.children].filter((el) => !el.classList.contains("sr-only")) as HTMLElement[];
      return {
        name: th.getAttribute("data-col")!,
        lines: lines.map((el) => el.textContent ?? ""),
        clipped: lines.some((el) => el.scrollWidth > el.clientWidth),
        path: [...pathBox.childNodes].filter((n) => !(n instanceof Element && n.classList.contains("sr-only")))
          .map((n) => n.textContent ?? "").join(""),
        pathClipped: pathBox.scrollWidth > pathBox.clientWidth,
      };
    }));
    expect(shown).toHaveLength(WIDE_STORAGES);
    expect(shown.filter((h) => [...h.name].length <= 26 && (h.clipped || h.lines.join("") !== h.name)).map((h) => h.name),
           "26자 이하 이름이 머리 두 줄 안에 다 보이지 않는다").toEqual([]);
    expect(shown.find((h) => h.name === WIDE_HYPHEN)!.lines, "하이픈 이름이 두 줄로 나뉘지 않았다").toHaveLength(2);
    expect(shown.filter((h) => h.pathClipped).map((h) => h.path), "경로 줄이 머리 상자(96px)를 넘쳤다 -- 끝 조각이 잘린다").toEqual([]);
    expect(shown.filter((h) => h.path.startsWith("/mgmt_storage")).map((h) => h.path), "긴 경로가 앞 조각으로 보인다").toEqual([]);
    expect(new Set(shown.map((h) => h.path)).size, "보이는 경로 글자가 겹치는 열이 있다").toBe(WIDE_STORAGES);

    // ⑤ 모르는 원문 사유 칩도 칩 상한(6rem) 안 -- 말줄임
    const unknown = page.locator(`main td [aria-label$=": ${WIDE_UNKNOWN_REASON}"]`).first();
    const chip = await unknown.evaluate((el) => [el.getBoundingClientRect().width,
                                                 el.firstElementChild!.getBoundingClientRect().width]);
    expect(Math.max(...chip), "모르는 사유 칩이 칩 상한(96px)을 넘었다").toBeLessThanOrEqual(96.5);

    // ⑥ 고정 모서리·첫 열·머리 + 민 방향 그늘(래퍼 data-scroll)
    const rowHead = page.locator("main tbody th[scope=row]").first();
    expect(await rowHead.evaluate((el) => getComputedStyle(el).boxShadow), "밀지 않았는데 그늘이 있다").toBe("none");
    await wrapper.evaluate((el) => { el.scrollLeft = 5000; el.scrollTop = 300; });
    await expect(wrapper).toHaveAttribute("data-scroll", "xy");
    const pinned = await wrapper.evaluate((w) => {
      const r = w.getBoundingClientRect();
      const innerLeft = r.left + w.clientLeft;
      const innerTop = r.top + w.clientTop;
      const corner = w.querySelector("thead th")!;
      const c = corner.getBoundingClientRect();
      const rowTh = document.elementFromPoint(innerLeft + 20, c.bottom + 10)?.closest("th[scope=row]") ?? null;
      const headTh = document.elementFromPoint(c.right + 30, innerTop + 10)?.closest("thead th") ?? null;
      const shadow = (el: Element | null) => (el === null ? "(없음)" : getComputedStyle(el).boxShadow);
      return { dx: c.left - innerLeft, dy: c.top - innerTop, rowTh: rowTh !== null, headTh: headTh !== null,
               shadows: [shadow(corner), shadow(rowTh), shadow(headTh)] };
    });
    expect(Math.abs(pinned.dx), "모서리 머리가 래퍼 왼쪽에 고정되지 않았다").toBeLessThanOrEqual(1);
    expect(Math.abs(pinned.dy), "모서리 머리가 래퍼 위쪽에 고정되지 않았다").toBeLessThanOrEqual(1);
    expect(pinned.rowTh, "민 상태에서 래퍼 왼쪽 끝에 노드 행 머리가 없다(첫 열 고정 회귀)").toBe(true);
    expect(pinned.headTh, "민 상태에서 래퍼 위쪽 끝에 열 머리가 없다(머리 고정 회귀)").toBe(true);
    expect(pinned.shadows.filter((s) => s === "none" || s === "(없음)"), `고정 칸 그늘이 없다: ${pinned.shadows.join(" | ")}`)
      .toEqual([]);

    // ⑦ 래퍼는 초점을 받아 화살표 키로 민다(마우스 없는 사용자)
    await wrapper.evaluate((el) => { el.scrollLeft = 0; el.scrollTop = 0; });
    await wrapper.focus();
    await page.keyboard.press("ArrowRight");
    await expect.poll(() => wrapper.evaluate((el) => el.scrollLeft), { message: "화살표 키로 표가 밀리지 않는다" })
      .toBeGreaterThan(0);
    await wrapper.evaluate((el) => { el.scrollLeft = 0; });

    // ⑧ 칸·열 머리 툴팁(래퍼 위임) + 열 따라가기 막대: 칸에 올리면 그 열 머리에 data-hot -- 면색은 그대로(bg-surface)이고 바닥 막대는
    //    배경 그림이다(예전 bg-infobg 는 PV 종류 꼬리표 Tag info 의 면과 같아 꼬리표가 맨 글자처럼 보였다). stor-002 = PV·파일시스템.
    const node = WIDE_FRESH_NODES[1];
    const cell002 = page.getByLabel(`${node} · stor-002: 정상`, { exact: true });
    await expectTip(page, cell002, [`${node} · stor-002`]);
    const head002 = page.locator('main thead th[data-col="stor-002"]');
    await cell002.hover();
    await expect(head002).toHaveAttribute("data-hot", "");
    const hot = await head002.evaluate((th) => ({
      image: getComputedStyle(th).backgroundImage, bg: getComputedStyle(th).backgroundColor,
      tagBg: getComputedStyle(th.querySelector(".bg-infobg")!).backgroundColor,
    }));
    expect(hot.image, "열 따라가기 막대(배경 그림)가 없다").toContain("linear-gradient");
    expect(hot.bg, "열 따라가기가 머리 면색을 PV 꼬리표 면색으로 바꿨다(꼬리표가 사라진다)").not.toBe(hot.tagBg);
    await longHead.evaluate((th) => th.scrollIntoView({ block: "nearest", inline: "nearest" }));
    await expectTip(page, longHead, [WIDE_LONG, "종류", "선택 가능한 PV"]);
    await page.mouse.move(0, 0);

    // ⑨ 열 거르기는 URL 에 남는다: 찾기 「archive」 14열 → 문제 있는 스토리지만 2열 → 찾기 지움 20열
    const box = page.getByRole("searchbox", { name: "스토리지 찾기" });
    await box.fill("archive");
    await expect(page).toHaveURL(/[?&]q=archive/);
    await expect(heads).toHaveCount(14 + 2);
    await page.getByRole("button", { name: "문제 있는 스토리지 20", exact: true }).click();
    await expect(page).toHaveURL(/[?&]cols=problems/);
    await expect(heads).toHaveCount(2 + 2);
    await box.fill("");
    await expect(heads).toHaveCount(20 + 2);
    await expect(page).not.toHaveURL(/[?&]q=/);

    // ⑩ 1920: 열이 늘어나지 않고(옛 min-w-full 은 남는 폭을 모든 열에 나눴다) 래퍼가 표 폭에 맞춰 좁아진다, 바닥은 첫 화면 안
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/admin/storages/mounts?view=table&q=stor-00");
    await expect(heads).toHaveCount(9 + 2);
    expect(offWidth(await storageHeadWidths(page)), "1920 에서 스토리지 열 폭이 늘었다(min-w-full 회귀)").toEqual([]);
    const fit = await wrapper.evaluate((w) => ({
      ww: w.getBoundingClientRect().width, tw: w.querySelector("table")!.getBoundingClientRect().width,
      corner: w.querySelector("thead th")!.getBoundingClientRect().width,
    }));
    expect(Math.abs(fit.ww - (fit.tw + 2)), `래퍼(${fit.ww})가 표(${fit.tw}) 폭에 맞지 않는다`).toBeLessThanOrEqual(1);
    expect(Math.abs(fit.corner - 193), `노드 열 폭 ${fit.corner} -- 넓은 화면에서 늘었다`).toBeLessThanOrEqual(1.5);
    await expect.poll(() => wrapper.evaluate((el) => el.getBoundingClientRect().bottom),
                      { message: "1920×1080 에서 래퍼 바닥이 첫 화면 밖이다" }).toBeLessThanOrEqual(1080);
  });
});
