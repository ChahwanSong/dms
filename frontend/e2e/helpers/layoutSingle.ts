import { expect, type Page } from "@playwright/test";
import { assertLayoutSane, type LayoutOptions } from "./layout";
import { STORAGE_NAME } from "../harness/env";

// E3(03-layout)의 단일 작업 화면 흐름(2026-11-26 단계별 등록 준비 -- spec 에서 동작 그대로 옮겼다). 단일·배치 흐름을 따로 둔 것은
// 단일(레인 S)·배치(레인 B) 위저드 작업이 같은 spec 파일을 고치지 않게 하려는 것이다 -- 03-layout.spec.ts 는 singleLayoutFlow·
// batchLayoutFlow 만 부르고, 흐름이 바뀌면 이 파일을 고친다.

async function visit(page: Page, path: string, heading: string): Promise<void> {
  await page.goto(path);
  await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
}

// 단일 작업 화면에 200자 무공백 경로를 넣는다(제출하지 않음 -- resource_key·검증과 무관).
// 긴 토큰이 1fr 열을 밀어내던 유형(6bc2ecb)의 재현 재료다: minmax(0,1fr) 이 빠지면 문서가 넘치고(L1),
// 요약 dd·"지정 경로" 줄(2026-10-27 d192 에 「실제 경로」에서 개명 -- 괴리 [C2])의 overflow-wrap 이 빠지면 그 칸만
// 넘친다 -- xl 의 요약 본문은 자기 스크롤 상자라 문서 L1 엔 안 보이므로 아래 assertTextContained 가 칸 단위로 잰다.
async function fillLongScanTarget(page: Page): Promise<void> {
  await page.getByLabel("연산", { exact: true }).selectOption("scan");
  // 면제자(하네스 admin)의 대상은 기본이 「PV」라 「직접 경로」로 바꾼다(2026-10-26 -- k8s 신원 모드 전용 화면).
  await page.getByRole("group", { name: "대상 지정 방식" }).getByRole("button", { name: "직접 경로", exact: true }).click();
  const storage = page.getByLabel("스토리지", { exact: true });
  await expect(storage.locator(`option[value="${STORAGE_NAME}"]`)).toHaveCount(1);
  await storage.selectOption(STORAGE_NAME);
  await page.getByLabel("대상 경로", { exact: true }).fill("x".repeat(200));
}

// 경로 줄 접두는 화면 라벨 그대로(SubmitJob PathFeedback). 2026-10-27 d192 에 「실제 경로」가 「지정 경로」로 바뀌었는데 이
// 셀렉터는 옛 라벨을 찾아 아무것도 못 잡고 조용히 통과했다(2026-10-28 리뷰 contract-36) -- 그래서 경로 줄을 하나 이상 찾았는지도
// 함께 단언한다(라벨이 또 바뀌면 여기서 빨간불).
const PATH_LINE_PREFIX = "지정 경로: ";
async function assertTextContained(page: Page): Promise<void> {
  const { leaks, pathLines } = await page.evaluate((prefix) => {
    const lines = Array.from(document.querySelectorAll("main p")).filter(
      (p) => (p.textContent ?? "").startsWith(prefix));
    const els = [
      ...Array.from(document.querySelectorAll("main dd")),
      ...Array.from(document.querySelectorAll("main dl")).map((dl) => dl.parentElement!),
      ...lines,
    ];
    return {
      pathLines: lines.length,
      leaks: els.filter((el) => el.scrollWidth > el.clientWidth + 1)
        .map((el) => `${el.tagName} ${el.scrollWidth}>${el.clientWidth}: ${(el.textContent ?? "").slice(0, 40)}`),
    };
  }, PATH_LINE_PREFIX);
  expect(pathLines, `「${PATH_LINE_PREFIX}」 줄을 못 찾았다 -- 라벨이 바뀌었거나 스토리지 뿌리를 몰라 줄이 없다(검사한 게 없다)`)
    .toBeGreaterThan(0);
  expect(leaks, "긴 무공백 경로가 요약 칸·지정 경로 줄을 넘쳤다(overflow-wrap/minmax(0,1fr) 회귀)").toEqual([]);
}

/**
 * 제출 화면(단일 작업): 표가 없으므로 하한 0 -- L1(가로 넘침)·L3(사이드바 240, 요약이 <aside> 가 아님)·L4 가 진다. 폭(width)은 spec 이
 * 이미 맞춘 뷰포트 폭이고(1280·375), md(768) 아래면 사이드바가 전폭 블록이라 L3 를 뺀다. 1280: 시트 + 오른쪽 sticky 요약 2열(xl 부터),
 * 375: 1열(시트 → 요약) -- sync 경로 줄·항목 표가 375 에서 넘치지 않아야 한다.
 */
export async function singleLayoutFlow(page: Page, width: number): Promise<void> {
  const layout: LayoutOptions = width < 768 ? { sidebarFixed: false, minTableCells: 0 } : { minTableCells: 0 };
  await visit(page, "/jobs/new", "단일 작업");
  await assertLayoutSane(page, layout);
  await fillLongScanTarget(page);
  await assertLayoutSane(page, layout);
  await assertTextContained(page);
}
