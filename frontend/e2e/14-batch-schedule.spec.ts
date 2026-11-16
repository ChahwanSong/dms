import { expect, test, type Page } from "@playwright/test";
import { STORAGE_NAME } from "./harness/env";
import { apiLogin } from "./helpers/session";
import { assertLayoutSane } from "./helpers/layout";

// E15 — 배치 스케줄러(2026-11-16 사용자 결정: 같은 배치 「전체 재실행」을 KST 프리셋 주기로, 작업 > 배치 스케줄러). 단위
// 테스트(msw)가 못 보는 것을 여기서 본다: 메뉴 → 실 서버의 스케줄 생성(특권 3중 게이트 — 하네스 admin 은 특권 목록 안의 세션)
// → 서버가 계산한 다음 5회 미리보기(:preview)와 목록의 「다음 실행」 → 배치 목록·상세에 실린 스케줄 요약(통합 단계의
// schedule 필드) → 끄기(다음 실행 「—」) → 삭제, 그리고 rm 배치는 「되돌릴 수 없음」 동의 전엔 저장이 잠긴다는 것. 회차가
// 실제로 쏘이는 것(컨트롤러 batch-scheduler 루프)은 기다리지 않는다 — 시각 규칙·발사·일시정지는 pytest 몫이다. 시각은 지금으로부터
// 약 12시간 뒤(KST)로 골라 이 시나리오 도중에 회차가 오지 않게 한다.

async function createBatch(page: Page, body: Record<string, unknown>): Promise<string> {
  const response = await page.request.post("/api/admin/batches", { data: { max_concurrency: 1, ...body } });
  expect(response.status(), await response.text()).toBe(202);
  const bid = ((await response.json()) as { batch_id?: string }).batch_id;
  expect(bid, "202 응답에 batch_id 가 없다").toMatch(/^[0-9a-f]{32}$/);
  return bid!;
}

async function batchStatus(page: Page, bid: string): Promise<string> {
  const response = await page.request.get(`/api/admin/batches/${bid}?view=summary`);
  return response.status() === 200 ? ((await response.json()) as { status: string }).status : `HTTP ${response.status()}`;
}

const pad2 = (n: number) => String(n).padStart(2, "0");
// 화면 글자(「매일 03:30 (KST)」의 괄호 등)를 정규식 안에 글자 그대로 넣는다.
const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

test.describe("E15 배치 스케줄러", () => {
  test.beforeEach(async ({ page }) => {
    await apiLogin(page);
  });

  test("scan 배치에 매일 스케줄을 추가하면 목록에 주기·다음 실행이 보이고(배치 목록·상세에도 실린다), 끄면 다음 실행 「—」, "
       + "삭제하면 사라진다", async ({ page }) => {
    const name = `e15-scan-${Date.now().toString(36)}`;
    const bid = await createBatch(page, { operation: "scan", options: {}, name,
                                          items: [{ storage: STORAGE_NAME, target: `${name}/a` }] });
    const time = `${pad2((new Date().getUTCHours() + 9 + 12) % 24)}:30`;
    const summary = `매일 ${time} (KST)`;

    // 메뉴(작업 > 배치 스케줄러)로 들어간다.
    await page.goto("/admin/batches");
    await page.getByRole("link", { name: "배치 스케줄러" }).click();
    await expect(page.getByRole("heading", { name: "배치 스케줄러", level: 1 })).toBeVisible();

    await page.getByRole("button", { name: "스케줄 추가" }).click();
    const dialog = page.getByRole("dialog", { name: "스케줄 추가" });
    const select = dialog.getByLabel("배치", { exact: true });
    await expect(select.locator(`option[value="${bid}"]`)).toHaveCount(1);
    await select.selectOption(bid);
    await dialog.getByRole("button", { name: "매일" }).click();
    await dialog.getByLabel("실행 시각(KST)").fill(time);
    await expect(dialog.getByText(summary, { exact: true })).toBeVisible();
    // 다음 5회는 서버가 계산한다 — 모두 그 시각(KST)이다(기본값 02:00 의 앞선 미리보기가 잠깐 남아 있을 수 있어 다섯 줄 모두를
    // 재시도 단언으로 본다).
    const preview = dialog.getByLabel("다음 실행 미리보기").getByRole("listitem");
    await expect(preview).toHaveText(Array.from({ length: 5 }, () => new RegExp(`^\\d{4}-\\d\\d-\\d\\d ${time}:00 KST$`)));
    await expect(dialog.getByText("회차마다 자식 요청 1개가 새로 생깁니다.")).toBeVisible();
    await dialog.getByRole("button", { name: "추가", exact: true }).click();
    await expect(dialog).toHaveCount(0);

    const row = page.locator("tbody tr", { has: page.getByRole("link", { name, exact: true }) });
    await expect(row).toHaveCount(1);
    await expect(row).toContainText(summary);
    await expect(row.locator("td").nth(2)).toHaveText(STORAGE_NAME);                       // 대상 스토리지
    const next = row.locator("td").nth(4);                                                 // 다음 실행(KST)
    await expect(next).toHaveText(new RegExp(`^\\d{4}-\\d\\d-\\d\\d${time} · \\d+시간 후$`));
    await expect(row.locator("td").nth(5)).toHaveText("아직 없음");                          // 마지막 결과 -- 판정 전
    await expect(row.getByLabel(`${name} 사용`)).toBeChecked();
    // 행이 있는 표의 불변식(td = table-cell, 표 안 버튼 한 줄, 문서 가로 넘침 없음): th 9 + td 9.
    await assertLayoutSane(page, { minTableCells: 18 });
    await page.setViewportSize({ width: 375, height: 667 });
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 18 });
    await page.setViewportSize({ width: 1280, height: 800 });

    // 배치 쪽 응답에도 스케줄 요약이 실린다 — 목록 「반복」 표식과 상세 머리 링크.
    await page.goto("/admin/batches");
    const batchRow = page.locator("tbody tr", { has: page.getByText(name, { exact: true }) });
    await expect(batchRow.getByText("반복", { exact: true })).toHaveAttribute("title", new RegExp(`^${esc(summary)} · 다음 `));
    await page.goto(`/admin/batches/${bid}`);
    const link = page.getByRole("link", { name: new RegExp(`^반복 실행: ${esc(summary)} · 다음 `) });
    await expect(link).toHaveAttribute("href", `/admin/batch-schedules?batch=${bid}`);
    // 머리 줄에 붙은 긴 「반복 실행: …」 링크도 375 에서 문서를 넓히지 않는다(머리 줄은 flex-wrap -- 줄바꿈으로 접힌다).
    await page.setViewportSize({ width: 375, height: 667 });
    await expect(link).toBeVisible();
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 0 });
    await page.setViewportSize({ width: 1280, height: 800 });

    // 끄기 → 다음 실행 「—」(재조회 뒤의 서버 값).
    await page.goto("/admin/batch-schedules");
    await row.getByLabel(`${name} 사용`).uncheck();
    await expect(row.getByLabel(`${name} 사용`)).not.toBeChecked();
    await expect(next).toHaveText("—");
    // 삭제(2단 확인) → 행이 사라진다.
    await row.getByRole("button", { name: `${name} 삭제`, exact: true }).click();
    await row.getByRole("button", { name: `${name} 삭제 확인` }).click();
    await expect(row).toHaveCount(0);
    await page.goto(`/admin/batches/${bid}`);
    await expect(page.getByRole("link", { name: "스케줄 추가" })).toHaveAttribute("href", `/admin/batch-schedules?batch=${bid}`);
  });

  test("rm 배치의 스케줄은 「되돌릴 수 없음」 동의 전엔 저장할 수 없다(저장은 하지 않는다)", async ({ page }) => {
    const name = `e15-rm-${Date.now().toString(36)}`;
    // 없는 경로 -- 이 시나리오는 저장하지 않지만, 배치 자체가 미리보기를 도는 동안 무엇도 지우지 않게.
    const bid = await createBatch(page, { operation: "rm", options: { recursive: true }, name,
                                          items: [{ storage: STORAGE_NAME, target: `${name}/nothing` }] });
    // 상세의 「스케줄 추가」 링크가 그 배치를 고른 추가 창을 연다(?batch=).
    await page.goto(`/admin/batches/${bid}`);
    await page.getByRole("link", { name: "스케줄 추가" }).click();
    const dialog = page.getByRole("dialog", { name: "스케줄 추가" });
    await expect(dialog.getByLabel("배치", { exact: true })).toHaveValue(bid);
    await expect(dialog.getByRole("note", { name: "반복 삭제 경고" })).toBeVisible();
    const save = dialog.getByRole("button", { name: "추가", exact: true });
    const ack = dialog.getByLabel("반복 실행이 되돌릴 수 없는 삭제를 만든다는 것을 이해했습니다(필수)");
    await expect(save).toBeDisabled();
    await ack.check();
    await expect(save).toBeEnabled();
    await ack.uncheck();
    await expect(save).toBeDisabled();
    // 375 에서 경고 상자를 품은 창도 문서를 넓히지 않는다.
    await page.setViewportSize({ width: 375, height: 667 });
    const [scroll, client] = await page.evaluate(() =>
      [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
    expect(scroll, `375 에서 문서가 가로로 넘친다(${scroll} > ${client})`).toBeLessThanOrEqual(client);
    // exact: 창 머리의 아이콘 버튼(aria-label 「창 닫기」)도 이름에 「닫기」를 품는다 -- 본문 아래 「닫기」 버튼을 고른다.
    await dialog.getByRole("button", { name: "닫기", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    expect((await (await page.request.get("/api/admin/batch-schedules")).json() as { batch_id: string }[])
      .some((s) => s.batch_id === bid), "저장하지 않았는데 스케줄이 생겼다").toBe(false);

    // 정리: 살아 있는 rm 배치를 남기지 않는다(이미 끝났으면 취소는 409 -- 끝난 상태면 된다).
    const cancel = await page.request.post(`/api/admin/batches/${bid}:cancel`);
    expect([200, 409], await cancel.text()).toContain(cancel.status());
    await expect.poll(() => batchStatus(page, bid), { timeout: 30_000, intervals: [500] })
      .toMatch(/^(Cancelled|Completed)$/);
  });
});
