import { expect, type Page } from "@playwright/test";
import { assertLayoutSane, type LayoutOptions } from "./layout";

// E3(03-layout)의 배치 생성 화면 흐름(2026-11-26 단계별 등록 준비 -- spec 에서 동작 그대로 옮겼다). 단일 흐름(layoutSingle)과 따로 둔
// 이유는 그 파일 머리 주석 -- 03-layout.spec.ts 는 batchLayoutFlow 만 부르고, 흐름이 바뀌면 이 파일을 고친다.

async function visit(page: Page, path: string, heading: string): Promise<void> {
  await page.goto(path);
  await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
}

/**
 * 제출 화면(배치 생성): 표가 없으므로 하한 0. 폭(width)은 spec 이 이미 맞춘 뷰포트 폭이고(1280·375), md(768) 아래면 L3 를 빼고 입력 방식
 * 세그먼트 한 줄 검사를 더한다.
 */
export async function batchLayoutFlow(page: Page, width: number): Promise<void> {
  const narrow = width < 768;
  const layout: LayoutOptions = narrow ? { sidebarFixed: false, minTableCells: 0 } : { minTableCells: 0 };
  await visit(page, "/admin/batches/new", "배치 생성");
  await assertLayoutSane(page, layout);
  // rm(2026-10-21): 「삭제 범위」 안내·rm 옵션 줄·요약 위 경고가 넘치지 않는다.
  await page.getByLabel("연산").selectOption("rm");
  await assertLayoutSane(page, layout);
  // rm 자동 실행(2026-10-21): 켜면 빨간 경고 + 동의 체크가 실행 제어 안에 붙는다 — 그 상자도 넘치지 않는다.
  await page.getByLabel("미리보기 후 자동 실행").check();
  await expect(page.getByRole("note", { name: "자동 삭제 경고" })).toBeVisible();
  await assertLayoutSane(page, layout);
  if (!narrow) return;
  await page.getByLabel("연산").selectOption("scan");
  // 입력 방식 세그먼트는 좁은 폭에서도 라벨이 음절 중간에서 접히지 않는다(통째로 다음 줄로).
  // L4 는 aside a·table button 만 보므로 여기서 직접 잰다(한 줄 = 높이 < 줄 높이 2배).
  const wrapped = await page.locator("button[aria-pressed]").evaluateAll((bs) => bs
    .filter((b) => b.getBoundingClientRect().height
      >= 2 * parseFloat(getComputedStyle(b).lineHeight) + 1)
    .map((b) => b.textContent));
  expect(wrapped, "입력 방식 세그먼트 라벨이 두 줄로 접혔다").toEqual([]);
}
