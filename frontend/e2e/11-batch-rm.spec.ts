import fs from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { STORAGE_NAME } from "./harness/env";
import { apiLogin } from "./helpers/session";

// E12 — rm 배치(2026-10-21 사용자 요청 「배치작업에 rm도 할수있도록」). 단위 테스트가 못 보는 것을 여기서 본다:
// 생성 화면 → 실 서버(rm 허용·겹침 422 미러) → 실 오케스트레이터가 미리보기 뒤 **확인 대기에서 멈추고**(자동 실행은 기본
// 꺼짐 — 그동안 파일은 그대로; 켠 rm 배치는 E13) → 확인 대화상자가 실 조인(job_state·preview_summary)으로 항목별 삭제 대상을 그리고 →
// 「되돌릴 수 없음」 체크 뒤에만 확인이 나가 → 실 오케스트레이터 Running 분기(확인 게이트)가 도장 자식만 실행해 →
// **하네스 스토리지의 그 경로들이 실제로 지워진다**(목록에 없는 형제는 남는다).
//
// 하네스 전제(global-setup): 컨트롤러 진입점은 e2e_controller.py — drm 잡만 가짜 drm(fixtures/bin/drm, 하네스 tmp 스토리지
// 아래만)으로 실제로 돌리고 출력은 러너 파서로 읽는다(미리보기 = walk 수: 대상 자신 + 그 아래 전부, 0 = 대상 없음).
// 오케스트레이터·스테퍼 1초 틱, admin 은 특권 목록 안(배치 생성의 3중 게이트).

interface BatchRow { status: string; operation: string; options: Record<string, unknown>; auto_confirm?: boolean;
                     items: { seq: number; status: string; files_count: number | null }[] }

async function batchOf(page: Page, bid: string): Promise<BatchRow> {
  const response = await page.request.get(`/api/admin/batches/${bid}`);
  expect(response.status(), await response.text()).toBe(200);
  return response.json();
}

// 지울 트리를 하네스 스토리지에 심는다. 하네스가 알려 준 실디렉터리가 없으면 실패다(skip 은 「안 봤다」를 초록으로 숨긴다).
function storageDir(): string {
  const dir = process.env.DMS_E2E_STORAGE_DIR;
  if (!dir || !fs.existsSync(dir)) throw new Error(`DMS_E2E_STORAGE_DIR(${dir}) 가 없다 -- global-setup 이 내보내야 한다`);
  return dir;
}

function plant(root: string, rel: string) {
  const p = path.join(root, rel);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, `e2e ${rel}\n`);
}

test.describe("E12 — rm 배치", () => {
  test.beforeEach(async ({ page }) => {
    await apiLogin(page);
  });

  test("생성 화면에서 만든 rm 배치는 확인 대기에서 멈추고(그동안 아무것도 안 지운다), 삭제 검토·「되돌릴 수 없음」 확인 뒤 "
       + "「삭제 실행 확인」으로만 실제로 지운다", async ({ page }) => {
    const root = storageDir();
    const base = `e12-${Date.now().toString(36)}`;
    // a = 디렉터리 + 파일 2 + 하위 디렉터리 + 파일 1 = walk 5, b = 디렉터리 + 파일 1 = 2, keep 은 목록 밖(남아야 한다)
    for (const f of ["a/f1", "a/f2", "a/sub/f3", "b/f4", "keep/f5"]) plant(root, `${base}/${f}`);
    // 하네스가 등록한 스토리지의 managed_root 가 이 디렉터리다 -- 아니면 아래 「지워졌다」 단언이 엉뚱한 곳을 본다.
    const storages = await (await page.request.get("/api/user/storages")).json() as
      { storage_name: string; managed_root?: string }[];
    expect(storages.find((s) => s.storage_name === STORAGE_NAME)?.managed_root).toBe(root);

    await page.goto("/admin/batches/new");
    await page.getByLabel("연산").selectOption("rm");
    await expect(page.getByLabel("미리보기 후 자동 실행")).not.toBeChecked();         // 기본 꺼짐(켠 rm 은 E13)
    await expect(page.getByRole("note", { name: "삭제 범위" })).toBeVisible();
    const st = page.getByLabel("스토리지");
    await expect(st.getByRole("option", { name: STORAGE_NAME })).toBeAttached();
    await st.selectOption(STORAGE_NAME);
    await expect(page.getByLabel("재귀 삭제(필수)")).toBeChecked();
    const create = page.getByRole("button", { name: "배치 생성" });
    // 한 배치 안 겹침은 제출 전에 줄끼리 말하고 잠근다 -- 2026-10-26 부터 CSV 는 서버가 해석하고(POST :parse-csv, 생성과 같은
    // 판정) 오류가 있으면 표에 반영하지 않는다(물리 줄 3 = 데이터 2행, 상대 줄 2).
    await page.getByRole("button", { name: "CSV 붙여넣기" }).click();
    await page.getByLabel("CSV", { exact: true }).fill(`target\n${base}/a\n${base}/a/sub`);
    await page.getByRole("button", { name: "테이블에 반영" }).click();
    const csvErrs = page.getByRole("list", { name: "반영할 수 없는 줄" });
    await expect(csvErrs).toContainText("3번째 줄: 한 rm 배치에 같은 경로가 두 번 있거나");
    await expect(csvErrs).toContainText("(2번째 줄과 겹침)");
    await expect(create).toBeDisabled();
    await page.getByRole("button", { name: "CSV 붙여넣기" }).click();
    await page.getByLabel("CSV", { exact: true }).fill(`target\n${base}/a\n${base}/b\n${base}/typo`);
    await page.getByRole("button", { name: "테이블에 반영" }).click();
    await expect(page.getByLabel("1행 경로")).toHaveValue(`${base}/a`);           // 서버 해석 → 표 반영
    await expect(page.getByRole("list", { name: "반영할 수 없는 줄" })).toHaveCount(0);
    await expect(page.getByRole("list", { name: "겹치는 삭제 경로" })).toHaveCount(0);
    await create.click();
    await page.waitForURL(/\/admin\/batches\/[0-9a-f]{32}$/);
    const bid = page.url().split("/").pop()!;
    const created = await batchOf(page, bid);
    expect(created.operation).toBe("rm");
    expect(created.options).toEqual({ recursive: true });
    expect(created.auto_confirm).toBe(false);

    await expect.poll(async () => (await batchOf(page, bid)).status, {
      timeout: 60_000, intervals: [500], message: `rm 배치 ${bid} 가 확인 대기가 되지 않았다 -- controller.log(e2e drm)를 보라`,
    }).toBe("PreviewReady");
    await page.waitForTimeout(3_000);                                   // 틱을 여러 번 지나도 자동 확인 없음
    expect((await batchOf(page, bid)).status).toBe("PreviewReady");
    for (const rel of ["a/f1", "a/sub/f3", "b/f4"])                       // 미리보기는 세기만 한다
      expect(fs.existsSync(path.join(root, base, rel)), `확인 전에 ${rel} 이 지워졌다`).toBe(true);

    await page.reload();
    await expect(page.getByRole("status")).toContainText("확인 전에는 아무것도 지워지지 않습니다");
    await page.getByRole("button", { name: "배치 확인" }).click();
    const dlg = page.getByRole("dialog", { name: "배치 확인" });
    await expect(dlg.getByRole("note", { name: "삭제 경고" })).toBeVisible();
    await expect(dlg.getByRole("note", { name: "목적지 소유" })).toHaveCount(0);
    // 실 조인: 가짜 drm 의 walk 수가 러너 파서·stepper·list_items_detail 을 거쳐 그대로 온다
    await expect(dlg).toContainText("7개(대상 경로 포함 파일·디렉터리, 항목별 미리보기 합계)");
    const items = dlg.getByRole("list", { name: "항목별 삭제 대상" }).getByRole("listitem");
    await expect(items).toHaveCount(3);
    await expect(items.nth(0)).toContainText(`#0 ${STORAGE_NAME}:${base}/a — 5개`);
    await expect(items.nth(0)).toContainText(`실제 경로: ${root}/${base}/a`);
    await expect(items.nth(1)).toContainText(`#1 ${STORAGE_NAME}:${base}/b — 2개`);
    await expect(items.nth(2)).toContainText("0개(대상 경로를 찾지 못함)");
    await expect(dlg).toContainText("1개 항목 — 미리보기에서 찾지 못했습니다");
    // 375 에서 긴 실제 경로가 대화상자를 가로로 넘기지 않는다
    await page.setViewportSize({ width: 375, height: 800 });
    const [sw, cw] = await dlg.evaluate((el) => [el.scrollWidth, el.clientWidth]);
    expect(sw, `375 에서 확인 대화상자가 가로로 넘친다(${sw} > ${cw})`).toBeLessThanOrEqual(cw);
    const go = dlg.getByRole("button", { name: "삭제 실행 확인" });
    await expect(go).toBeDisabled();
    await dlg.getByLabel("되돌릴 수 없는 삭제임을 확인했습니다").check();
    await go.click();

    await expect.poll(async () => (await batchOf(page, bid)).status, {
      timeout: 60_000, intervals: [500], message: `확인한 rm 배치 ${bid} 가 60s 안에 끝나지 않았다`,
    }).toBe("Completed");
    // 확인한 경로만 실제로 지워졌다 -- 목록 밖 형제(keep)와 부모(base)는 남는다
    expect(fs.existsSync(path.join(root, base, "a")), "a 가 남아 있다").toBe(false);
    expect(fs.existsSync(path.join(root, base, "b")), "b 가 남아 있다").toBe(false);
    expect(fs.existsSync(path.join(root, base, "keep", "f5")), "목록 밖 keep 이 지워졌다").toBe(true);
    const done = await batchOf(page, bid);
    expect(done.items.map((it) => [it.status, it.files_count])).toEqual([
      ["Succeeded", 5], ["Succeeded", 2], ["Succeeded", 0]]);
    const reqs = await (await page.request.get(`/api/user/requests?batch_id=${bid}`)).json() as { operation: string }[];
    expect(reqs.map((r) => r.operation)).toEqual(["rm", "rm", "rm"]);
    fs.rmSync(path.join(root, base), { recursive: true, force: true });
  });
});
