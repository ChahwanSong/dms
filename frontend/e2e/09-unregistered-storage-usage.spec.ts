import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { expect, test, type Page } from "@playwright/test";
import { BASE_URL, STORAGE_NAME, backendEnv, fakeBinDir, findDmsBin } from "./harness/env";
import { apiLogin } from "./helpers/session";
import { assertLayoutSane } from "./helpers/layout";

// E10 — 사용량 분석의 미등록 스토리지(2026-10-12 사용자 요청 두 가지 → 2026-10-20 스토리지 탭). 단위 테스트가 못 보는 것:
// 실 서버·실 컨트롤러로 끝까지 간 scan(단건 + 배치)이 **스토리지를 지운 뒤** 사용량 화면에 「미등록」 탭으로 남고 —
//   1) 그 탭의 CSV 내보내기 행들이 감사 기록의 마지막 설정으로 backend_type·mount_path·managed_root·abs_path 를 채우는가
//      (예전엔 지금 등록된 목록에서만 찾아 미등록 행의 그 열이 비었다 — 사용자 보고 「두번째 행부터 누락」). CSV 는 탭
//      하나씩이라 시드 행(등록)은 시드 탭에서 따로 받는다,
//   2) 그 탭 경고 띠의 「기록 정리」가 실 서버 한 회차 정리(단건 delete_terminal + 배치째 delete_batch)로 그 스토리지의
//      scan 기록을 지우고, 탭이 사라져 기본 탭(시드)으로 돌아가며, 시드 스토리지의 기록은 남는가,
//   3) 띠·표가 보이는 채로 1280·375 레이아웃 불변식(L1~L4)이 지켜지고, 375 에서 펼친 상세가 표 래퍼의 보이는 폭 안에
//      들어오는가(표 폭 771px 을 따라가 가로로 밀어야 보이던 결함의 그물).
// 하네스 전제(global-setup): 조용한 창 0초, 시드 스토리지의 mount 는 가짜 mountinfo 의 tmpfs. 에이전트는 부팅 때 한 번만
// 보고하므로(스토리지별 마운트 증거) 새 스토리지는 그대로면 Unknown 이다 — 같은 노드 이름·같은 마운트·같은 가짜 도구로
// `dms agent --once` 를 한 번 더 돌려 Ready 증거를 싣는다(global-setup 4) 와 같은 호출).

const RUN = Date.now().toString(36);
const OLD = `e2e-old-${RUN}`;

async function seedMount(page: Page): Promise<string> {
  const rows = (await (await page.request.get("/api/admin/storages")).json()) as
    { storage_name: string; mount_path: string }[];
  const seed = rows.find((r) => r.storage_name === STORAGE_NAME);
  expect(seed, "시드 스토리지가 없다").toBeTruthy();
  return seed!.mount_path;
}

function refreshAgent(mount: string): void {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dms-e2e-agent-"));
  const mountinfo = path.join(dir, "mountinfo");
  fs.writeFileSync(mountinfo, `36 25 0:32 / ${mount} rw,relatime shared:1 - tmpfs tmpfs rw\n`);
  const agent = spawnSync(findDmsBin(), ["agent", "--once"], {
    env: backendEnv(path.join(dir, "unused.db"), {
      DMS_AGENT_API_URL: BASE_URL, DMS_AGENT_NODE_NAME: "e2e-node", DMS_AGENT_MOUNTINFO_PATH: mountinfo,
      PATH: `${fakeBinDir}${path.delimiter}${process.env.PATH ?? ""}`,
    }),
    encoding: "utf8",
  });
  expect(agent.status, agent.stderr || agent.stdout).toBe(0);
  expect(agent.stderr || "").not.toContain("agent report failed");
}

async function requestState(page: Page, rid: string): Promise<string> {
  const response = await page.request.get(`/api/user/requests/${rid}`);
  if (response.status() !== 200) return `HTTP ${response.status()}`;
  return ((await response.json()) as { state: string }).state;
}

async function scan(page: Page, storage: string, target: string): Promise<string> {
  const response = await page.request.post("/api/user/requests", {
    data: { operation: "scan", storage, target, options: {}, priority: "mid", run_as_root: true } });
  expect(response.status(), await response.text()).toBe(202);
  const rid = ((await response.json()) as { request_id: string }).request_id;
  await expect.poll(() => requestState(page, rid), { timeout: 60_000, intervals: [500],
    message: `scan ${target} 이 60s 안에 끝나지 않았다` }).toBe("Succeeded");
  return rid;
}

test.describe("E10 — 사용량 분석 미등록 스토리지", () => {
  test.beforeEach(async ({ page }) => {
    await apiLogin(page);
  });

  test("지운 스토리지의 scan: CSV 는 마지막 설정으로 채우고, 「기록 정리」가 그 스토리지 기록만 지운다", async ({ page }) => {
    // 1) 시드와 같은 마운트로 새 스토리지를 등록하고 Ready 를 기다린다.
    const mount = await seedMount(page);
    const created = await page.request.post("/api/admin/storages", {
      data: { storage_name: OLD, mount_path: mount, managed_root: mount, backend_type: "gpfs" } });
    expect(created.status(), await created.text()).toBe(201);
    refreshAgent(mount);
    await expect.poll(async () => {
      const rows = (await (await page.request.get("/api/user/storages")).json()) as { storage_name: string; status: string }[];
      return rows.find((r) => r.storage_name === OLD)?.status;
    }, { timeout: 30_000, intervals: [500], message: `${OLD} 가 Ready 가 되지 않았다` }).toBe("Ready");

    // 2) 새 스토리지에 단건 scan 둘 + 배치 하나(자식 1), 시드 스토리지에 단건 하나(남아야 한다).
    await scan(page, OLD, `e10-a-${RUN}`);
    await scan(page, OLD, `e10-b-${RUN}`);
    await scan(page, STORAGE_NAME, `e10-keep-${RUN}`);
    const batch = await page.request.post("/api/admin/batches", {
      data: { operation: "scan", max_concurrency: 1, options: {}, name: `e10-${RUN}`,
              items: [{ storage: OLD, target: `e10-c-${RUN}` }] } });
    expect(batch.status(), await batch.text()).toBe(202);
    const bid = ((await batch.json()) as { batch_id: string }).batch_id;
    await expect.poll(async () => ((await (await page.request.get(`/api/admin/batches/${bid}`)).json()) as
      { status: string }).status, { timeout: 60_000, intervals: [500] }).toBe("Completed");

    // 3) 스토리지 삭제 → 그 이름의 기록은 「미등록」.
    const del = await page.request.delete(`/api/admin/storages/${OLD}`);
    expect(del.status(), await del.text()).toBe(200);

    // 4) 사용량 화면: 「미등록」 탭(구분선 뒤)과 그 패널 맨 위 경고 띠, 탭 안의 행. 띠·표가 보이는 채로 레이아웃 불변식.
    await page.goto("/admin/usage");
    const oldTab = page.getByRole("tab", { name: new RegExp(`^${OLD} 미등록`) });
    await oldTab.click();
    await expect(oldTab).toHaveAttribute("aria-selected", "true");
    await expect(oldTab).toHaveAccessibleName(`${OLD} 미등록 대상 3개`);
    const panel = page.getByRole("tabpanel");
    const note = panel.getByRole("note", { name: "미등록 스토리지" });
    await expect(note).toContainText("대상 3개 · 성공 scan 3개 · 정리할 작업 3개(배치 1개 포함)");
    await expect(note).toContainText(`마지막 설정 gpfs · ${mount}`);
    await expect(panel.locator("table tbody tr").filter({ hasText: `e10-a-${RUN}` })).toHaveCount(1);
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 900 });
      await assertLayoutSane(page, { sidebarFixed: width >= 768, minTableCells: 4 });
    }
    // 375: 펼친 상세는 표 폭이 아니라 래퍼의 보이는 폭(clientWidth)에 맞는다 -- 표를 가로로 밀지 않아도 한눈에.
    await panel.getByRole("button", { name: `e10-a-${RUN}` }).click();
    const detail = page.getByRole("region", { name: `${OLD}:e10-a-${RUN} 상세` });
    // 하네스의 가짜 dscan 리포트는 읽히지 않아(행은 DB 용량) 상세는 「표시할 이력 없음」 한 줄일 수 있다 -- 폭은 상세 칸 자체를
    // 잰다(고정 칸의 폭이 래퍼에 맞춰지는지가 검사 대상이다).
    await expect(detail.getByText(/최신 실 사용량|표시할 이력이 없습니다|성공 scan 이 없습니다/).first()).toBeVisible();
    const fit = await page.evaluate(() => {
      const wrap = document.querySelector("[role=tabpanel] table")!.parentElement!;
      const d = document.getElementById("usage-target-detail")!;
      return { wrap: wrap.clientWidth, table: wrap.scrollWidth, detail: d.getBoundingClientRect().width };
    });
    expect(fit.table, "375 에서 표가 래퍼보다 넓지 않다 -- 이 검사의 전제(넘칠 때만 결함이 보인다)").toBeGreaterThan(fit.wrap);
    expect(fit.detail, `펼친 상세(${fit.detail}px)가 래퍼의 보이는 폭(${fit.wrap}px)을 넘는다`).toBeLessThanOrEqual(fit.wrap + 1);
    await panel.getByRole("button", { name: `e10-a-${RUN}` }).click();
    await expect(detail).toHaveCount(0);
    await page.setViewportSize({ width: 1280, height: 900 });

    // 5) CSV(탭 하나씩): 미등록 탭의 행은 마지막 설정으로 채워진다(출처 last_known). 파일 이름에 스토리지.
    const exportCsv = async () => {
      const [download] = await Promise.all([
        page.waitForEvent("download"), page.getByRole("tabpanel").getByRole("button", { name: "CSV 내보내기" }).click()]);
      const text = fs.readFileSync((await download.path())!, "utf8").replace(/^\uFEFF/, "");
      const [header, ...lines] = text.split("\r\n").filter(Boolean);
      return { name: download.suggestedFilename(), cols: header.split(","), lines };
    };
    const oldCsv = await exportCsv();
    expect(oldCsv.name).toMatch(new RegExp(`^dms-usage-${OLD}-\\d{8}-\\d{4}\\.csv$`));
    const cell = (cols: string[], line: string, c: string) => line.split(",")[cols.indexOf(c)];
    expect(oldCsv.lines).toHaveLength(3);
    for (const l of oldCsv.lines) {
      expect(cell(oldCsv.cols, l, "storage_name")).toBe(OLD);
      expect(cell(oldCsv.cols, l, "storage_registered")).toBe("false");
      expect(cell(oldCsv.cols, l, "storage_info_source")).toBe("last_known");
      expect(cell(oldCsv.cols, l, "backend_type")).toBe("gpfs");
      expect(cell(oldCsv.cols, l, "mount_path")).toBe(mount);
      expect(cell(oldCsv.cols, l, "abs_path")).toBe(`${mount}/${cell(oldCsv.cols, l, "target")}`);
    }
    // 시드 행(등록)은 시드 탭의 CSV 에 -- 한 파일에 스토리지가 섞이지 않는다.
    await page.getByRole("tab", { name: new RegExp(`^${STORAGE_NAME}`) }).click();
    await expect(page.getByRole("tabpanel").locator("table tbody tr").filter({ hasText: `e10-keep-${RUN}` })).toHaveCount(1);
    const seedCsv = await exportCsv();
    expect(seedCsv.lines.every((l) => cell(seedCsv.cols, l, "storage_name") === STORAGE_NAME)).toBe(true);
    const keep = seedCsv.lines.find((l) => cell(seedCsv.cols, l, "target") === `e10-keep-${RUN}`)!;
    expect(cell(seedCsv.cols, keep, "storage_info_source")).toBe("registered");

    // 6) 미등록 탭 띠의 「기록 정리」 → 확인 창 → 3개 영구 삭제 → 결과 줄(탭 줄 위), 그 탭이 사라지고 기본 탭(시드)의
    //    행은 남는다.
    await oldTab.click();
    await note.getByRole("button", { name: `${OLD} 기록 정리` }).click();
    const dialog = page.getByRole("dialog", { name: "미등록 스토리지 기록 정리" });
    await expect(dialog).toContainText(`${OLD} 의 scan 작업 기록 3개를 영구 삭제합니다`);
    await dialog.getByLabel("되돌릴 수 없음을 확인했습니다").check();
    await dialog.getByRole("button", { name: "3개 영구 삭제" }).click();
    await expect(page.getByRole("status", { name: "정리 결과" })).toContainText(
      `${OLD}: 작업 기록 3개 삭제됨(배치 1개 포함)`);
    await expect(page.getByRole("status", { name: "정리 결과" })).toBeFocused();
    await expect(page.getByRole("tab", { name: new RegExp(`^${OLD}`) })).toHaveCount(0);
    await expect(page.getByRole("tab", { name: new RegExp(`^${STORAGE_NAME}`) })).toHaveAttribute("aria-selected", "true");
    // 방금 정리해 사라진 탭이라 「기록이 없습니다」 안내는 띄우지 않는다(결과 줄이 말한다) -- 탭은 정리가 끝나기 전(무효화
    // 재조회 중)에 사라지므로 실 브라우저의 순서로 확인한다.
    await expect(page.getByText(/스토리지의 사용량 기록이 없습니다/)).toHaveCount(0);
    await expect(page.getByRole("tabpanel").locator("table tbody tr").filter({ hasText: `e10-keep-${RUN}` })).toHaveCount(1);
    expect((await page.request.get(`/api/admin/batches/${bid}`)).status()).toBe(404);
    // 정리 대기가 비어야 뒤 시나리오가 깨끗하다(결과 파일·파드 정리, 컨트롤러 1초 틱).
    await expect.poll(async () => ((await (await page.request.get("/api/admin/request-purges")).json()) as
      { pending: number }).pending, { timeout: 30_000, intervals: [500] }).toBe(0);
  });
});
