import fs from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { STORAGE_NAME } from "./harness/env";
import { apiLogin } from "./helpers/session";

// E13 — rm 배치 자동 확인(2026-10-21 사용자 요청 「Rm 배치작업에서 sync와 마찬가지로 사용자 확인 버튼 없이 바로 rm 작업 수행하도록
// (자동 확인) 하는 옵션 추가해줘」). 단위 테스트가 못 보는 것을 여기서 본다: 생성 화면의 자동 실행 체크 → 빨간 경고·**필수** 동의
// 체크(체크 전엔 생성이 잠긴다) → 실 서버(acknowledge_irreversible 검사) → **실 오케스트레이터가 PreviewReady 에서 사람 대신
// 확인**(「배치 확인」을 누르지 않는다) → 확인 게이트 Running 분기가 도장 자식만 실행 → **하네스 스토리지의 그 경로가 실제로
// 지워지고** 목록 밖 형제는 남는다. 그리고 자동 실행을 켜지 않은 rm 배치는 여전히 확인 대기에서 멈춘다(그동안 파일 그대로).
//
// 하네스 전제(global-setup): 컨트롤러 진입점은 e2e_controller.py — drm 잡만 가짜 drm(fixtures/bin/drm, 하네스 tmp 스토리지
// 아래만)으로 실제로 돌린다(E12 와 같은 배선). 오케스트레이터·스테퍼 1초 틱, admin 은 특권 목록 안(배치 생성의 3중 게이트).

interface BatchRow { status: string; operation: string; auto_confirm?: boolean; auto_confirm_by?: string | null;
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

test.describe("E13 — rm 배치 자동 확인", () => {
  test.beforeEach(async ({ page }) => {
    await apiLogin(page);
  });

  test("생성 화면에서 경고를 읽고 동의해 자동 실행을 켠 rm 배치는 「배치 확인」 없이 그 경로만 실제로 지운다", async ({ page }) => {
    const root = storageDir();
    const base = `e13-${Date.now().toString(36)}`;
    // a = 디렉터리 + 파일 2 + 하위 디렉터리 + 파일 1 = walk 5, b = 디렉터리 + 파일 1 = 2, keep 은 목록 밖(남아야 한다)
    for (const f of ["a/f1", "a/f2", "a/sub/f3", "b/f4", "keep/f5"]) plant(root, `${base}/${f}`);
    const storages = await (await page.request.get("/api/user/storages")).json() as
      { storage_name: string; managed_root?: string }[];
    expect(storages.find((s) => s.storage_name === STORAGE_NAME)?.managed_root).toBe(root);

    await page.goto("/admin/batches/new");
    await page.getByLabel("연산").selectOption("rm");
    const st = page.getByLabel("스토리지");
    await expect(st.getByRole("option", { name: STORAGE_NAME })).toBeAttached();
    await st.selectOption(STORAGE_NAME);
    await page.getByLabel("1행 경로").fill(`${base}/a`);
    await page.getByRole("button", { name: "행 추가" }).click();
    await page.getByLabel("2행 경로").fill(`${base}/b`);
    const create = page.getByRole("button", { name: "배치 생성" });
    await expect(create).toBeEnabled();
    const auto = page.getByLabel("미리보기 후 자동 실행");
    await expect(auto).not.toBeChecked();                                   // 기본 꺼짐
    await auto.check();
    // 켜는 순간 빨간 경고 + 필수 동의 — 체크 전엔 만들 수 없다(서버도 acknowledge_irreversible 을 본다)
    const warn = page.getByRole("note", { name: "자동 삭제 경고" });
    await expect(warn).toContainText("미리보기가 끝나면 사람 확인 없이 root 권한으로 삭제됩니다 — 되돌릴 수 없습니다");
    await expect(create).toBeDisabled();
    await page.getByLabel("확인 없이 삭제되는 것에 동의").check();
    await expect(create).toBeEnabled();
    await create.click();
    await page.waitForURL(/\/admin\/batches\/[0-9a-f]{32}$/);
    const bid = page.url().split("/").pop()!;
    const created = await batchOf(page, bid);
    expect(created.operation).toBe("rm");
    expect(created.auto_confirm).toBe(true);
    expect(created.auto_confirm_by).toBe("admin");
    await expect(page.getByText("자동 실행 — 미리보기가 끝나면 확인 없이 삭제 (켠 사람 admin)")).toBeVisible();

    // 「배치 확인」을 누르지 않는다 -- 오케스트레이터가 미리보기 → 확인 대기 → (자동 확인) → 삭제 → 완료로 끌고 간다.
    await expect.poll(async () => (await batchOf(page, bid)).status, {
      timeout: 60_000, intervals: [500],
      message: `자동 확인 rm 배치 ${bid} 가 60s 안에 Completed 가 되지 않았다 -- controller.log(batch_auto_confirm*·e2e drm)를 보라`,
    }).toBe("Completed");
    // 목록의 경로만 실제로 지워졌다 -- 목록 밖 형제(keep)와 부모(base)는 남는다
    expect(fs.existsSync(path.join(root, base, "a")), "a 가 남아 있다").toBe(false);
    expect(fs.existsSync(path.join(root, base, "b")), "b 가 남아 있다").toBe(false);
    expect(fs.existsSync(path.join(root, base, "keep", "f5")), "목록 밖 keep 이 지워졌다").toBe(true);
    const done = await batchOf(page, bid);
    expect(done.items.map((it) => [it.status, it.files_count])).toEqual([["Succeeded", 5], ["Succeeded", 2]]);

    // 375 폭: 끝난 rm 자동 배치 = 헤더가 가장 넓은 경우(버튼 넷 + 빨간 「자동 실행 — … 삭제 (켠 사람)」) -- 넘치지 않는다.
    await page.setViewportSize({ width: 375, height: 800 });
    await page.reload();
    await expect(page.getByText("자동 실행 — 미리보기가 끝나면 확인 없이 삭제 (켠 사람 admin)")).toBeVisible();
    const [scroll, client] = await page.evaluate(() =>
      [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
    expect(scroll, `375 에서 문서가 가로로 넘친다(${scroll} > ${client})`).toBeLessThanOrEqual(client);
    fs.rmSync(path.join(root, base), { recursive: true, force: true });
  });

  test("동의 없이 자동 실행을 켜는 요청은 서버가 거부하고(422), 끈 채 만든 rm 배치는 확인 대기에서 멈춘다(파일 그대로)", async ({ page }) => {
    const root = storageDir();
    const base = `e13m-${Date.now().toString(36)}`;
    plant(root, `${base}/x/f1`);
    const body = { operation: "rm", max_concurrency: 1, options: { recursive: true }, name: `rm-manual-${base}`,
                   items: [{ storage: STORAGE_NAME, target: `${base}/x` }] };
    // 체크가 없는 옛 탭·스크립트의 켜기 — 아무것도 만들지 않는다
    const refused = await page.request.post("/api/admin/batches", { data: { ...body, auto_confirm: true } });
    expect(refused.status(), await refused.text()).toBe(422);
    expect((await refused.json()).detail).toBe("batch_rm_auto_confirm_ack_required");

    const response = await page.request.post("/api/admin/batches", { data: body });
    expect(response.status(), await response.text()).toBe(202);
    const bid = ((await response.json()) as { batch_id: string }).batch_id;
    await expect.poll(async () => (await batchOf(page, bid)).status, {
      timeout: 60_000, intervals: [500], message: `rm 배치 ${bid} 가 확인 대기가 되지 않았다`,
    }).toBe("PreviewReady");
    await page.waitForTimeout(3_000);                                   // 틱을 여러 번 지나도 자동 확인 없음
    expect((await batchOf(page, bid)).status).toBe("PreviewReady");
    expect(fs.existsSync(path.join(root, base, "x", "f1")), "확인 전에 지워졌다").toBe(true);
    await page.goto(`/admin/batches/${bid}`);
    await expect(page.getByRole("status")).toContainText("확인 전에는 아무것도 지워지지 않습니다");
    await expect(page.getByRole("button", { name: "자동 실행 끄기" })).toHaveCount(0);
    // 정리: 취소(확인 대기 → Cancelled) 뒤 트리를 지운다 — 다음 스펙에 살아 있는 rm 자식을 남기지 않는다.
    const cancel = await page.request.post(`/api/admin/batches/${bid}:cancel`);
    expect(cancel.status(), await cancel.text()).toBe(200);
    await expect.poll(async () => (await batchOf(page, bid)).status, { timeout: 30_000, intervals: [500] })
      .toBe("Cancelled");
    expect(fs.existsSync(path.join(root, base, "x", "f1")), "취소했는데 지워졌다").toBe(true);
    fs.rmSync(path.join(root, base), { recursive: true, force: true });
  });
});
