import { expect, type Page } from "@playwright/test";
import { assertLayoutSane, type LayoutOptions } from "./layout";
import { chooseOperation, expectStep, pickStorageCard, wizardGo, wizardNext } from "./wizard";
import { STORAGE_NAME } from "../harness/env";

// E3(03-layout)의 배치 생성 화면 흐름. 단일 흐름(layoutSingle)과 따로 둔 이유는 그 파일 머리 주석 -- 03-layout.spec.ts 는
// batchLayoutFlow 만 부르고, 흐름이 바뀌면 이 파일을 고친다. 2026-11-26 단계별 등록: 배치는 다섯 단계(작업 종류 → 스토리지 → 항목 →
// 옵션·실행 제어 → 이름·검토) + 하단 고정 바다. rm 으로 단계마다 L1·L3·L4 를 잰다(rm 이 안내 상자가 가장 많다 -- 삭제 범위 요약·자동
// 삭제 경고·검토의 삭제 범위 전문·바 위 빨간 줄).

async function visit(page: Page, path: string, heading: string): Promise<void> {
  await page.goto(path);
  await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
}

// 입력 방식 세그먼트는 좁은 폭에서도 라벨이 음절 중간에서 접히지 않는다(통째로 다음 줄로). L4 는 aside a·table button 만 보므로
// 여기서 직접 잰다(한 줄 = 높이 < 줄 높이 2배). 세그먼트가 안 보이는 단계에서 재면 `button[aria-pressed]` 가 0개라 빈 배열로
// 초록이 된다 -- 「CSV 붙여넣기」가 보이는지와 개수(3)를 먼저 단언한다.
async function assertSegmentsOneLine(page: Page): Promise<void> {
  await expect(page.getByRole("button", { name: "CSV 붙여넣기", exact: true })).toBeVisible();
  const segs = page.locator("button[aria-pressed]");
  await expect(segs).toHaveCount(3);
  const wrapped = await segs.evaluateAll((bs) => bs
    .filter((b) => b.getBoundingClientRect().height
      >= 2 * parseFloat(getComputedStyle(b).lineHeight) + 1)
    .map((b) => b.textContent));
  expect(wrapped, "입력 방식 세그먼트 라벨이 두 줄로 접혔다").toEqual([]);
}

// ③ 「PV 에서 경로 찾기」 시트(모달)를 열면 하단 고정 바가 덮개 **아래**에 있어야 한다 -- 바에 z-index 를 주면 z 없는 Radix 덮개
// 위로 올라온다(usage/TargetTable 교훈, Wizard.tsx 머리 주석). 바의 왼쪽 안쪽 점에서 맨 위 요소가 바 안이면 실패다. 시트를 닫으면
// 연 버튼으로 포커스가 돌아온다.
async function assertSheetCoversBar(page: Page, layout: LayoutOptions): Promise<void> {
  const opener = page.getByRole("button", { name: "PV 에서 경로 찾기", exact: true });
  await opener.click();
  const dlg = page.getByRole("dialog", { name: "PV 에서 경로 찾기", exact: true });
  await expect(dlg).toBeVisible();
  await assertLayoutSane(page, layout);
  const barOnTop = await page.evaluate(() => {
    const bar = document.querySelector("[data-wizard-bar]") as HTMLElement;
    const r = bar.getBoundingClientRect();
    const el = document.elementFromPoint(r.left + 16, r.top + r.height / 2);
    return el !== null && bar.contains(el);
  });
  expect(barOnTop, "시트(모달)를 열었는데 하단 바가 덮개 위에 있다(바에 z-index?)").toBe(false);
  await dlg.getByRole("button", { name: "닫기", exact: true }).click();
  await expect(dlg).toHaveCount(0);
  await expect(opener).toBeFocused();
}

/**
 * 제출 화면(배치 생성): 표가 없으므로 하한 0. 폭(width)은 spec 이 이미 맞춘 뷰포트 폭이고(1280·375), md(768) 아래면 L3 를 빼고 입력
 * 방식 세그먼트 한 줄 검사를 더한다. 1280 흐름은 끝에 768×900 으로 줄여 ⑤ 와 ① 을 한 번 더 잰다(스테퍼 다섯 단계·바가 본문 약
 * 488px 를 넘지 않는지).
 */
export async function batchLayoutFlow(page: Page, width: number): Promise<void> {
  const narrow = width < 768;
  const layout: LayoutOptions = narrow ? { sidebarFixed: false, minTableCells: 0 } : { minTableCells: 0 };
  await visit(page, "/admin/batches/new", "배치 생성");
  await expectStep(page, "작업 종류");
  await assertLayoutSane(page, layout);                                       // ①
  await chooseOperation(page, "rm");
  await assertLayoutSane(page, layout);
  await wizardNext(page, "스토리지");
  // rm(2026-10-21): 「삭제 범위 요약」 안내가 넘치지 않는다.
  await expect(page.getByRole("note", { name: "삭제 범위 요약", exact: true })).toBeVisible();
  await pickStorageCard(page, "스토리지", STORAGE_NAME);
  await assertLayoutSane(page, layout);                                       // ②(카드 묶음)
  await wizardNext(page, "항목");
  await page.getByLabel("1행 경로", { exact: true }).fill("layout/a");
  await assertLayoutSane(page, layout);                                       // ③
  if (narrow) await assertSegmentsOneLine(page);
  await assertSheetCoversBar(page, layout);
  await wizardNext(page, "옵션·실행 제어");
  // rm 자동 실행(2026-10-21): 켜면 빨간 경고 + 동의 체크가 실행 제어 안에 붙는다 — 그 상자도 넘치지 않는다.
  await page.getByLabel("미리보기 후 자동 실행", { exact: true }).check();
  await expect(page.getByRole("note", { name: "자동 삭제 경고", exact: true })).toBeVisible();
  await assertLayoutSane(page, layout);                                       // ④
  await page.getByLabel(/되돌릴 수 없다는 것을 이해했습니다/).check();
  await wizardNext(page, "이름·검토");
  await expect(page.getByRole("note", { name: "삭제 범위", exact: true })).toBeVisible();
  await assertLayoutSane(page, layout);                                       // ⑤(검토 카드·바 위 빨간 줄)
  if (width >= 1280) {
    await page.setViewportSize({ width: 768, height: 900 });
    await assertLayoutSane(page, { sidebarFixed: true, minTableCells: 0 });   // ⑤ @768
    await wizardGo(page, "작업 종류");
    await assertLayoutSane(page, { sidebarFixed: true, minTableCells: 0 });   // ① @768
    await page.setViewportSize({ width: 1280, height: 800 });
  }
}
