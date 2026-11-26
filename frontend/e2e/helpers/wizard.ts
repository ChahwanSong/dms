import { expect, type Locator, type Page } from "@playwright/test";

// 제출 위저드(components/form/Wizard -- 단일 작업·배치 생성, 2026-11-26 단계별 등록) e2e 헬퍼. 이름 매칭은 **모두 exact: true**
// (또는 앞부분 고정 정규식)다 -- Playwright 의 기본은 부분 일치라 radiogroup 「스토리지」가 「소스 스토리지」·「목적지 스토리지」에,
// 「다음」이 다른 「다음…」 이름에 함께 걸린다. 숨긴 라디오(sr-only)는 check() 대신 카드 <label> 을 눌러 고르고 toBeChecked 로
// 단언한다(숨긴 요소의 행동성 검사가 흔들린다).

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** 진행 단계 카드(PV 시트의 Dialog 제목도 h2 라 표지로 고른다). */
export const stepCard = (page: Page): Locator => page.locator("[data-wizard-card]");
const stepTitle = (page: Page): Locator => stepCard(page).getByRole("heading", { level: 2 });
const reasonText = async (page: Page): Promise<string> =>
  ((await page.locator("[data-wizard-reason]").textContent()) ?? "").trim();

/** 지금 단계 카드의 제목이 title 인가(보이는가). */
export async function expectStep(page: Page, title: string): Promise<void> {
  await expect(stepCard(page).getByRole("heading", { level: 2, name: title, exact: true })).toBeVisible();
}

/** 연산 카드 고르기 -- radiogroup 「연산」 → radio op 의 카드 label 을 눌러 고르고 toBeChecked. */
export async function chooseOperation(page: Page, op: "sync" | "scan" | "rm"): Promise<void> {
  const group = page.getByRole("radiogroup", { name: "연산", exact: true });
  const radio = group.getByRole("radio", { name: op, exact: true });
  await group.locator("label", { has: page.getByRole("radio", { name: op, exact: true }) }).click();
  await expect(radio).toBeChecked();
}

/** 「다음」. 막혀 있으면(aria-disabled) 바 이유 줄을 메시지에 싣고 실패한다. expectTitle 이 없으면 제목이 바뀔 때까지 기다린다. */
export async function wizardNext(page: Page, expectTitle?: string): Promise<void> {
  const next = page.getByRole("button", { name: "다음", exact: true });
  const before = ((await stepTitle(page).textContent()) ?? "").trim();
  await expect(next, `「다음」이 막혀 있다 — ${await reasonText(page)}`).not.toHaveAttribute("aria-disabled", "true");
  await next.click();
  if (expectTitle !== undefined) await expectStep(page, expectTitle);
  else await expect(stepTitle(page)).not.toHaveText(before);
}

/** 스테퍼에서 단계로 간다(접근 이름 = 「이름 + 값 + 상태 꼬리」라 앞부분 고정 정규식). 잠긴 단계면 제목이 안 바뀌어 실패한다. */
export async function wizardGo(page: Page, name: string): Promise<void> {
  const nav = page.getByRole("navigation", { name: "진행 단계", exact: true });
  await nav.getByRole("button", { name: new RegExp(`^${escapeRe(name)}`) }).click();
  await expect(stepTitle(page)).toHaveText(new RegExp(`^${escapeRe(name)}`));
}

/** 검토 단계까지 「다음」(상한 = 배치 5단계). 닿지 못하면 바 이유 줄을 메시지에 싣는다. */
export async function toReview(page: Page, title: "검토·제출" | "이름·검토", maxSteps = 5): Promise<void> {
  for (let i = 0; i < maxSteps; i++) {
    if (((await stepTitle(page).textContent()) ?? "").trim() === title) return;
    await wizardNext(page);
  }
  await expect(stepTitle(page), `검토에 닿지 못함 — ${await reasonText(page)}`).toHaveText(title);
}

/** 배치 스토리지 카드 고르기 -- 라디오 접근 이름은 storageOptionText(이름 + 꼬리)라 이름 앞부분 정규식으로 찾는다. */
export async function pickStorageCard(page: Page, label: string, name: string): Promise<void> {
  const re = new RegExp(`^${escapeRe(name)}( |$)`);
  const group = page.getByRole("radiogroup", { name: label, exact: true });
  const radio = group.getByRole("radio", { name: re });
  await group.locator("label", { has: page.getByRole("radio", { name: re }) }).click();
  await expect(radio).toBeChecked();
}
