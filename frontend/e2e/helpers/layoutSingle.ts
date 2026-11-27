import { expect, type Page } from "@playwright/test";
import { assertLayoutSane, type LayoutOptions } from "./layout";
import { chooseOperation, expectStep, toReview, wizardNext } from "./wizard";
import { STORAGE_NAME } from "../harness/env";

// E3(03-layout)의 단일 작업 화면 흐름. 단일·배치 흐름을 따로 둔 것은 두 제출 화면의 흐름이 바뀔 때 03-layout.spec.ts 를 같이
// 고치지 않게 하려는 것이다(dms-k8s fe87a767 의 분리를 이식) -- spec 은 singleLayoutFlow·batchLayoutFlow 만 부르고, 흐름이 바뀌면 이
// 파일을 고친다. 2026-11-27 단계별 등록(dms-k8s 219e8870·798480ab·b1eddc3c 이식): 단일 작업은 네 단계(작업 종류 → 스캔 대상 → 옵션·
// 실행 설정 → 검토·제출) + 하단 고정 바다. 관리자(하네스 admin)는 ① 에 실행 신원·root 실행이 있다. 단계마다 L1·L3·L4 를 재고, 긴
// 무공백 경로는 ②(입력 아래 「지정 경로」 줄)와 ④(검토의 dd)에서 따로 잰다.

async function visit(page: Page, path: string, heading: string): Promise<void> {
  await page.goto(path);
  await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
}

// ① scan → ② 스토리지(드롭다운 -- 단일 작업은 StoragePicker 그대로) + 200자 무공백 경로(제출하지 않음 -- resource_key·검증과 무관).
// 긴 토큰이 1fr 열을 밀어내던 유형(6bc2ecb)의 재현 재료다: minmax(0,1fr) 이 빠지면 문서가 넘치고(L1),
// 검토 dd·"지정 경로" 줄(2026-10-27 d192 에 「실제 경로」에서 개명 -- 괴리 [C2])의 overflow-wrap 이 빠지면 그 칸만
// 넘친다 -- 문서 L1 엔 안 보일 수 있으므로 아래 assertPathLines·assertReviewCells 가 칸 단위로 잰다.
async function fillLongScanTarget(page: Page, layout: LayoutOptions): Promise<void> {
  await chooseOperation(page, "scan");
  await wizardNext(page, "스캔 대상");
  await assertLayoutSane(page, layout);                                     // ② 빈 폼
  const storage = page.getByLabel("스토리지", { exact: true });
  await expect(storage.locator(`option[value="${STORAGE_NAME}"]`)).toHaveCount(1);
  await storage.selectOption(STORAGE_NAME);
  await page.getByLabel("대상 경로", { exact: true }).fill("x".repeat(200));
}

// 경로 줄 접두는 화면 라벨 그대로(SubmitJob PathFeedback). 2026-10-27 d192 에 「실제 경로」가 「지정 경로」로 바뀌었는데 이
// 셀렉터는 옛 라벨을 찾아 아무것도 못 잡고 조용히 통과했다(2026-10-28 리뷰 contract-36) -- 그래서 경로 줄을 하나 이상 찾았는지도
// 함께 단언한다(라벨이 또 바뀌면 여기서 빨간불).
const PATH_LINE_PREFIX = "지정 경로: ";
async function assertPathLines(page: Page): Promise<void> {
  const { leaks, pathLines } = await page.evaluate((prefix) => {
    const lines = Array.from(document.querySelectorAll("main p")).filter(
      (p) => (p.textContent ?? "").startsWith(prefix));
    return {
      pathLines: lines.length,
      leaks: lines.filter((el) => el.scrollWidth > el.clientWidth + 1)
        .map((el) => `${el.tagName} ${el.scrollWidth}>${el.clientWidth}: ${(el.textContent ?? "").slice(0, 40)}`),
    };
  }, PATH_LINE_PREFIX);
  expect(pathLines, `「${PATH_LINE_PREFIX}」 줄을 못 찾았다 -- 라벨이 바뀌었거나 스토리지 뿌리를 몰라 줄이 없다(검사한 게 없다)`)
    .toBeGreaterThan(0);
  expect(leaks, "긴 무공백 경로가 지정 경로 줄을 넘쳤다(overflow-wrap 회귀)").toEqual([]);
}

// 검토 단계: 「지정 경로: 」 줄은 없다(값은 요약 dd 에 있다) -- dd·dl 상자가 하나 이상 있고 아무것도 넘치지 않아야 한다.
async function assertReviewCells(page: Page): Promise<void> {
  const { cells, leaks } = await page.evaluate(() => {
    const dds = Array.from(document.querySelectorAll("main dd"));
    const els = [...dds, ...Array.from(document.querySelectorAll("main dl")).map((dl) => dl.parentElement!)];
    return {
      cells: dds.length,
      leaks: els.filter((el) => el.scrollWidth > el.clientWidth + 1)
        .map((el) => `${el.tagName} ${el.scrollWidth}>${el.clientWidth}: ${(el.textContent ?? "").slice(0, 40)}`),
    };
  });
  expect(cells, "검토 단계에 요약 dd 가 없다(검사한 게 없다)").toBeGreaterThan(0);
  expect(leaks, "긴 무공백 경로가 검토 요약 칸을 넘쳤다(overflow-wrap/minmax(0,1fr) 회귀)").toEqual([]);
}

// 375: ② 입력에 Tab 으로 포커스가 가면 그 칸이 하단 고정 바에 가리지 않아야 한다(scroll-margin-bottom + 셸 onFocusCapture 보정).
async function assertFocusAboveBar(page: Page): Promise<void> {
  const path = page.getByLabel("대상 경로", { exact: true });
  await page.getByLabel("스토리지", { exact: true }).focus();
  await page.keyboard.press("Tab");
  await expect(path).toBeFocused();
  const { bottom, barTop } = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement;
    const bar = document.querySelector("[data-wizard-bar] > div:last-child") as HTMLElement;
    return { bottom: el.getBoundingClientRect().bottom, barTop: bar.getBoundingClientRect().top };
  });
  expect(bottom, "포커스한 입력이 하단 고정 바에 가렸다").toBeLessThanOrEqual(barTop + 0.5);
}

// 768(본문 약 488px)의 ②: 「옵션 기본값으로 검토」 지름길은 좁은 폭(<sm)에서만 숨기므로(dms-k8s b1eddc3c -- 태블릿 폭에서도 ③ 을
// 건너뛸 수 있다) 여기서 보이고, 그 버튼까지 서도 바의 상태 줄이 꺾이지 않아야 한다(지름길을 lg 아래에서 숨기던 때의 그 라인 실
// Chrome 결함: 상태 줄이 한 글자씩 꺾였다 -- 바가 flex-wrap + 상태 칸 기준 폭 12rem 으로 버튼 무리를 다음 줄로 내린다,
// components/form/Wizard WizardActionBar). 상태 줄 높이가 두 줄을 넘으면 꺾인 것이다. 끝나면 1280 으로 돌린다.
async function assertTargetBarAt768(page: Page): Promise<void> {
  await page.setViewportSize({ width: 768, height: 900 });
  await expect(page.getByRole("button", { name: "옵션 기본값으로 검토", exact: true })).toBeVisible();
  await assertLayoutSane(page, { sidebarFixed: true, minTableCells: 0 });
  const { height, width, lineHeight } = await page.evaluate(() => {
    const p = document.querySelector("[data-wizard-reason]") as HTMLElement;
    const r = p.getBoundingClientRect();
    return { height: r.height, width: r.width, lineHeight: parseFloat(getComputedStyle(p).lineHeight) || 20 };
  });
  expect(height, `768 ② 바 상태 줄이 꺾였다(폭 ${width}px · 높이 ${height}px)`).toBeLessThanOrEqual(lineHeight * 2 + 1);
  await page.setViewportSize({ width: 1280, height: 800 });
}

/**
 * 제출 화면(단일 작업): 표가 없으므로 하한 0 -- L1(가로 넘침)·L3(사이드바 240, 카드·바가 <aside> 가 아님)·L4 가 진다. 폭(width)은
 * spec 이 이미 맞춘 뷰포트 폭이고(1280·375), md(768) 아래면 사이드바가 전폭 블록이라 L3 를 뺀다. 단계마다(①②③④) 잰다. ① 은
 * 관리자라 실행 신원·root 실행 구획까지 그려진 채다. 1280 흐름은 ②(지름길이 선 바 -- assertTargetBarAt768)와 끝의 검토(④)를
 * 768×900 으로 줄여 한 번 더 잰다(스테퍼·바가 본문 약 488px 를 넘지 않는지 -- 1280·375 만으론 그 폭을 안 잰다).
 */
export async function singleLayoutFlow(page: Page, width: number): Promise<void> {
  const layout: LayoutOptions = width < 768 ? { sidebarFixed: false, minTableCells: 0 } : { minTableCells: 0 };
  await visit(page, "/jobs/new", "단일 작업");
  await expectStep(page, "작업 종류");
  await expect(page.getByLabel("root 권한으로 실행", { exact: true })).toBeVisible();   // 관리자 ① = 연산 + 실행 신원·권한
  await assertLayoutSane(page, layout);                                     // ①
  await fillLongScanTarget(page, layout);
  await assertLayoutSane(page, layout);                                     // ② 값이 찬 채
  await assertPathLines(page);
  if (width < 768) await assertFocusAboveBar(page);
  if (width >= 1280) await assertTargetBarAt768(page);
  await wizardNext(page, "옵션·실행 설정");
  await assertLayoutSane(page, layout);                                     // ③
  await toReview(page, "검토·제출");
  await assertLayoutSane(page, layout);                                     // ④
  await assertReviewCells(page);
  if (width >= 1280) {
    await page.setViewportSize({ width: 768, height: 900 });
    await assertLayoutSane(page, { sidebarFixed: true, minTableCells: 0 });
    await assertReviewCells(page);
    await page.setViewportSize({ width: 1280, height: 800 });
  }
}
