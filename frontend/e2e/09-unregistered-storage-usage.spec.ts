import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { expect, test, type Page } from "@playwright/test";
import { BASE_URL, STORAGE_NAME, backendEnv, fakeBinDir, findDmsBin } from "./harness/env";
import { apiLogin } from "./helpers/session";
import { assertLayoutSane } from "./helpers/layout";

// E10 — 사용량 분석의 미등록 스토리지(2026-10-12 사용자 요청 두 가지 → 2026-10-20 스토리지 탭 → 2026-10-29 하위 탭 「경로별 ·
// 스토리지별」 + 스토리지 select). 단위 테스트가 못 보는 것: 실 서버·실 컨트롤러로 끝까지 간 scan(단건 + 배치)이 **스토리지를 지운
// 뒤** 사용량 화면에 「미등록」으로 남고(스토리지별 표의 미등록 행 · 경로별 select 의 「미등록」 묶음) —
//   1) 그 스토리지의 CSV 내보내기 행들이 감사 기록의 마지막 설정으로 backend_type·mount_path·managed_root·abs_path 를 채우는가
//      (예전엔 지금 등록된 목록에서만 찾아 미등록 행의 그 열이 비었다 — 사용자 보고 「두번째 행부터 누락」). CSV 는 스토리지
//      하나씩이라 시드 행(등록)은 시드 스토리지에서 따로 받는다,
//   2) 그 스토리지 경고 띠의 「기록 정리」가 실 서버 한 회차 정리(단건 delete_terminal + 배치째 delete_batch)로 그 스토리지의
//      scan 기록을 지우고, select 옵션이 사라져 기본 스토리지(시드)로 돌아가며, 시드 스토리지의 기록은 남는가,
//   3) 띠·표가 보이는 채로 1280·375 레이아웃 불변식(L1~L4)이 지켜지고(스토리지별 표의 미등록 행·구분선 포함), 경로 상세
//      시트(2026-10-29 -- 예전 행 아래 펼침)가 1280 에선 오른쪽 640px, 375 에선 전체 화면으로 뷰포트 안에 들어오며 시트 안에
//      가로 넘침이 없는가(예전 펼침이 표 폭 771px 을 따라가 가로로 밀어야 보이던 결함의 그물을 시트에 옮겼다).
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

    // 4) 스토리지별(2026-10-29): 미등록 행(구분선 뒤 · 배지) -- 표가 보이는 채로 레이아웃 불변식, 이름을 누르면 그 스토리지의
    //    경로별(push). 경로별 select 에선 「미등록 — 기록만 남음」 묶음의 옵션(대상 수).
    await page.goto("/admin/usage/storages");
    await expect(page.getByRole("heading", { name: "스토리지별", level: 1 })).toBeVisible();
    const oldLink = page.getByRole("table").getByRole("link", { name: OLD, exact: true });
    // filter({ has }) 의 안쪽 locator 는 바깥(행)에서부터 찾는다 -- page.getByRole("table") 로 시작하면 「행 안의 표」를 찾아 늘 0건.
    const oldRow = page.getByRole("table").getByRole("row").filter({ has: page.getByRole("link", { name: OLD, exact: true }) });
    await expect(oldRow).toContainText("미등록");
    await expect(page.locator("table tr[aria-hidden]")).toContainText("미등록 — 기록만 남음");
    // 합계 칸 자리표시(td 바로 아래 Skeleton)가 걷힌 뒤에 잰다 -- 자리표시도 td 수가 같아 셀 하한을 채우므로, 기다리지 않으면 실제
    // 값 칸(df 막대·「이상」·긴 바이트)의 넘침을 재지 못한다(03 visitUsageStorages 와 같은 기다림).
    await expect(page.locator("table td > div[aria-hidden]")).toHaveCount(0);
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 900 });
      await assertLayoutSane(page, { sidebarFixed: width >= 768, minTableCells: 16 });
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    await oldLink.click();
    await expect(page).toHaveURL(new RegExp(`/admin/usage\\?storage=${OLD}$`));
    const storage = page.getByRole("combobox", { name: "스토리지", exact: true });
    await expect(storage).toHaveValue(`s:${OLD}`);
    await expect(storage.locator(`optgroup[label="미등록 — 기록만 남음"] option[value="s:${OLD}"]`))
      .toHaveText(`${OLD} · 미등록 · 대상 3개`);
    // 옵션 값은 「s:이름」, 고르면 300ms 뒤 push -- URL 의 storage 가 바뀔 때까지 기다린다.
    const choose = async (name: string) => {
      await storage.selectOption(`s:${name}`);
      await expect(page).toHaveURL(new RegExp(`[?&]storage=${name}(&|$)`));
    };
    // 경로별: 그 패널(스토리지 이름의 구역) 맨 위 경고 띠와 행. 띠·표가 보이는 채로 레이아웃 불변식.
    const panelOf = (name: string) => page.getByRole("region", { name, exact: true });
    const panel = panelOf(OLD);
    const note = panel.getByRole("note", { name: "미등록 스토리지" });
    await expect(note).toContainText("대상 3개 · 성공 scan 3개 · 정리할 작업 3개(배치 1개 포함)");
    await expect(note).toContainText(`마지막 설정 gpfs · ${mount}`);
    await expect(panel.locator("table tbody tr").filter({ hasText: `e10-a-${RUN}` })).toHaveCount(1);
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 900 });
      await assertLayoutSane(page, { sidebarFixed: width >= 768, minTableCells: 4 });
    }
    // 경로 상세 시트(2026-10-29): 375 는 전체 화면, 1280 은 오른쪽 640px -- 둘 다 뷰포트 안이고 시트 본문에 가로 넘침이 없다
    // (표는 375 에서 래퍼 안으로 넘치지만 시트는 표 폭을 따라가지 않는다). 하네스의 가짜 dscan 리포트는 읽히지 않아(행은 DB
    // 용량) 상세는 「표시할 이력 없음」 한 줄일 수 있다 -- 폭은 시트 자체를 잰다.
    for (const width of [375, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      await panel.getByRole("button", { name: `e10-a-${RUN}` }).click();
      const sheet = page.getByRole("dialog", { name: `e10-a-${RUN}` });
      await expect(sheet.getByText(/최신 실 사용량|표시할 이력이 없습니다|성공 scan 이 없습니다/).first()).toBeVisible();
      await expect(page).toHaveURL(new RegExp(`[?&]target=e10-a-${RUN}(&|$)`));
      // 여는 움직임(150ms translateX)이 끝난 뒤에 잰다 -- 도중엔 시트가 오른쪽 밖에 걸쳐 있다.
      await sheet.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)));
      const fit = await page.evaluate(() => {
        const d = document.querySelector("[role=dialog]")!;
        const body = d.querySelector("[data-sheet-body]")!;
        const r = d.getBoundingClientRect();
        return { left: r.left, right: r.right, width: r.width, vw: document.documentElement.clientWidth,
                 bodyScroll: body.scrollWidth, bodyClient: body.clientWidth };
      });
      expect(fit.right, `시트 오른쪽(${fit.right})이 뷰포트(${fit.vw}) 밖`).toBeLessThanOrEqual(fit.vw + 1);
      expect(fit.left, "시트 왼쪽이 뷰포트 밖").toBeGreaterThanOrEqual(-1);
      if (width === 375) expect(fit.width, "375 시트는 전체 화면").toBeGreaterThanOrEqual(fit.vw - 1);
      else expect(fit.width, "1280 시트는 640px").toBeLessThanOrEqual(641);
      expect(fit.bodyScroll, `시트 본문 가로 넘침(${fit.bodyScroll}>${fit.bodyClient})`).toBeLessThanOrEqual(fit.bodyClient + 1);
      // 닫기(✕): push 로 열었으니 뒤로 -- target 이 빠지고 포커스는 그 행의 경로 버튼으로
      await sheet.getByRole("button", { name: "닫기" }).click();
      await expect(sheet).toHaveCount(0);
      await expect(page).not.toHaveURL(/[?&]target=/);
      await expect(panel.getByRole("button", { name: `e10-a-${RUN}` })).toBeFocused();
    }
    await page.setViewportSize({ width: 1280, height: 900 });

    // 5) CSV(스토리지 하나씩): 미등록 스토리지의 행은 마지막 설정으로 채워진다(출처 last_known). 파일 이름에 스토리지.
    const exportCsv = async (name: string) => {
      const [download] = await Promise.all([
        page.waitForEvent("download"), panelOf(name).getByRole("button", { name: "CSV 내보내기" }).click()]);
      const text = fs.readFileSync((await download.path())!, "utf8").replace(/^\uFEFF/, "");
      const [header, ...lines] = text.split("\r\n").filter(Boolean);
      return { name: download.suggestedFilename(), cols: header.split(","), lines };
    };
    const oldCsv = await exportCsv(OLD);
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
    // 시드 행(등록)은 시드 스토리지의 CSV 에 -- 한 파일에 스토리지가 섞이지 않는다.
    await choose(STORAGE_NAME);
    await expect(panelOf(STORAGE_NAME).locator("table tbody tr").filter({ hasText: `e10-keep-${RUN}` })).toHaveCount(1);
    const seedCsv = await exportCsv(STORAGE_NAME);
    expect(seedCsv.lines.every((l) => cell(seedCsv.cols, l, "storage_name") === STORAGE_NAME)).toBe(true);
    const keep = seedCsv.lines.find((l) => cell(seedCsv.cols, l, "target") === `e10-keep-${RUN}`)!;
    expect(cell(seedCsv.cols, keep, "storage_info_source")).toBe("registered");

    // 6) 미등록 스토리지 띠의 「기록 정리」 → 확인 창 → 3개 영구 삭제 → 결과 줄(알림 자리 -- 하위 탭 아래), 그 옵션이 사라지고
    //    기본 스토리지(시드)의 행은 남는다.
    await choose(OLD);
    await note.getByRole("button", { name: `${OLD} 기록 정리` }).click();
    const dialog = page.getByRole("dialog", { name: "미등록 스토리지 기록 정리" });
    await expect(dialog).toContainText(`${OLD} 의 scan 작업 기록 3개를 영구 삭제합니다`);
    await dialog.getByLabel("되돌릴 수 없음을 확인했습니다").check();
    await dialog.getByRole("button", { name: "3개 영구 삭제" }).click();
    await expect(page.getByRole("status", { name: "정리 결과" })).toContainText(
      `${OLD}: 작업 기록 3개 삭제됨(배치 1개 포함)`);
    await expect(page.getByRole("status", { name: "정리 결과" })).toBeFocused();
    await expect(storage.locator(`option[value="s:${OLD}"]`)).toHaveCount(0);
    await expect(storage).toHaveValue(`s:${STORAGE_NAME}`);
    // 방금 정리해 사라진 스토리지라 「기록이 없습니다」 안내는 띄우지 않는다(결과 줄이 말한다) -- 옵션은 정리가 끝나기 전(무효화
    // 재조회 중)에 사라지므로 실 브라우저의 순서로 확인한다.
    await expect(page.getByText(/스토리지의 사용량 기록이 없습니다/)).toHaveCount(0);
    await expect(panelOf(STORAGE_NAME).locator("table tbody tr").filter({ hasText: `e10-keep-${RUN}` })).toHaveCount(1);
    expect((await page.request.get(`/api/admin/batches/${bid}`)).status()).toBe(404);
    // 정리 대기가 비어야 뒤 시나리오가 깨끗하다(결과 파일·파드 정리, 컨트롤러 1초 틱).
    await expect.poll(async () => ((await (await page.request.get("/api/admin/request-purges")).json()) as
      { pending: number }).pending, { timeout: 30_000, intervals: [500] }).toBe(0);
  });
});
