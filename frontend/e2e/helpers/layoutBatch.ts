import { expect, type Page } from "@playwright/test";
import { assertLayoutSane, type LayoutOptions } from "./layout";
import { chooseOperation, expectStep, pickStorageCard, wizardGo, wizardNext } from "./wizard";
import { STORAGE_NAME } from "../harness/env";

// E3(03-layout)의 배치 생성 화면 흐름. 단일 흐름(layoutSingle)과 따로 둔 이유는 그 파일 머리 주석 -- 03-layout.spec.ts 는
// batchLayoutFlow 만 부르고, 흐름이 바뀌면 이 파일을 고친다. 2026-11-27 단계별 등록(dms-k8s 46caffe3·7bfa2be3·d7a228f2 이식): 배치는
// 다섯 단계(작업 종류 → 스토리지 → 항목 → 옵션·실행 제어 → 이름·검토) + 하단 고정 바다. rm 으로 단계마다 L1·L3·L4 를 잰다(rm 이 안내
// 상자가 가장 많다 -- 삭제 범위 요약·자동 삭제 경고·검토의 삭제 범위 전문·바 위 빨간 줄). dms-k8s 의 ③ 「PV 에서 경로 찾기」 시트(모달이
// 하단 바를 덮는가) 검사는 없다 -- dms-ssc 배치 생성엔 PV 도, 단계 안 모달도 없다.

async function visit(page: Page, path: string, heading: string): Promise<void> {
  await page.goto(path);
  await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
}

// 입력 방식 세그먼트는 좁은 폭에서도 라벨이 음절 중간에서 접히지 않는다(통째로 다음 줄로). L4 는 aside a·table button 만 보므로
// 여기서 직접 잰다(한 줄 = 높이 < 줄 높이 2배). 세그먼트가 안 보이는 단계에서 재면 `button[aria-pressed]` 가 0개라 빈 배열로
// 초록이 된다 -- 「CSV 붙여넣기」가 보이는지와 개수(3 -- 테이블 편집·CSV 붙여넣기·파일 업로드)를 먼저 단언한다.
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

// ④ rm 자동 실행을 켜고 동의 전에 막힌 「다음」을 누르면 동의 체크로 포커스·스크롤하고(data-wizard-nudge -- Wizard blockedNudge, dms-k8s
// d7a228f2), 그 칸이 하단 고정 바에 가리지 않는다(그 라인 d49 실증: 1440×900 에서 체크 top 989 > 창 900 인데 포커스가 「다음」에
// 남았다). 맨 위로 올린 뒤 누른다 -- 체크가 처음부터 바 위에 보이면 스크롤을 잰 것이 아니므로 그 전제를 단언한다. dms-ssc 의 실행
// 제어 칸 수가 그 라인과 달라 창 높이에서 체크가 이미 보일 수 있으면 창 높이를 체크 위쪽까지 줄여 전제를 만든 뒤 재고 되돌린다(폭은
// 그대로 -- 레이아웃이 아니라 「보이는 범위」만 바꾼다). 「다음」은 aria-disabled 라 Playwright 행동성 검사(enabled)를 건너뛰는 force
// 클릭이다(같은 마우스 이벤트 -- 막힌 버튼을 누르는 사람의 동작 그대로).
async function assertBlockedNextFocusesAck(page: Page): Promise<void> {
  const ack = page.getByLabel(/되돌릴 수 없다는 것을 이해했습니다/);
  const next = page.getByRole("button", { name: "다음", exact: true });
  await expect(next).toHaveAttribute("aria-disabled", "true");
  const toTop = () => page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  const pos = () => ack.evaluate((el) => {
    const bar = document.querySelector("[data-wizard-bar]") as HTMLElement;
    const r = el.getBoundingClientRect();
    return { top: r.top, bottom: r.bottom, barTop: bar.getBoundingClientRect().top };
  });
  const viewport = page.viewportSize();
  await toTop();
  let before = await pos();
  if (before.bottom <= before.barTop && viewport !== null) {
    await page.setViewportSize({ width: viewport.width, height: Math.max(320, Math.floor(before.top)) });
    await toTop();
    before = await pos();
  }
  try {
    expect(before.bottom, `동의 체크가 처음부터 바 위에 보인다(체크 bottom ${before.bottom} · 바 top ${before.barTop}) -- 스크롤을 재지 못한다`)
      .toBeGreaterThan(before.barTop);
    await next.click({ force: true });
    await expectStep(page, "옵션·실행 제어");
    await expect(ack, "막힌 「다음」이 동의 체크로 포커스를 보내지 않았다(data-wizard-nudge?)").toBeFocused();
    await expect.poll(async () => {
      const p = await pos();
      return p.top >= 0 && p.bottom <= p.barTop + 0.5 ? "ok" : `체크 top ${p.top} · bottom ${p.bottom} · 바 top ${p.barTop}`;
    }, { message: "포커스한 동의 체크가 화면 밖이거나 하단 고정 바에 가렸다", timeout: 5000 }).toBe("ok");
  } finally {
    if (viewport !== null) await page.setViewportSize(viewport);
  }
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
  await assertLayoutSane(page, layout);                                       // ②(카드 격자)
  await wizardNext(page, "항목");
  await page.getByLabel("1행 경로", { exact: true }).fill("layout/a");
  await assertLayoutSane(page, layout);                                       // ③
  if (narrow) await assertSegmentsOneLine(page);
  await wizardNext(page, "옵션·실행 제어");
  // rm 자동 실행(2026-10-21): 켜면 빨간 경고 + 동의 체크가 실행 제어 안에 붙는다 — 그 상자도 넘치지 않는다.
  await page.getByLabel("미리보기 후 자동 실행", { exact: true }).check();
  await expect(page.getByRole("note", { name: "자동 삭제 경고", exact: true })).toBeVisible();
  await assertLayoutSane(page, layout);                                       // ④
  await assertBlockedNextFocusesAck(page);
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
