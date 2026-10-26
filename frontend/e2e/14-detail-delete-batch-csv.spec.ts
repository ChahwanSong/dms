import fs from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import { SHARED_TOKEN, STORAGE_NAME } from "./harness/env";
import { apiLogin } from "./helpers/session";

// E14 — 2026-10-26 BACKLOG C 묶음: 요청 상세의 단건 삭제 · 배치 CSV 백엔드화(템플릿·서버 해석·내보내기) · 배치 기록 삭제의
// 세션 경계·감사 · 배치 생성의 스토리지 드롭다운. 단위 테스트가 못 보는 것을 본다: 실 서버의 삭제 트랜잭션과 화면 이동(상세 →
// 목록 + 알림), 실 다운로드(Content-Disposition·BOM), 서버 해석 → 표 → 생성 → 내보내기가 같은 항목으로 왕복하는가.

async function submitScan(page: Page, target: string): Promise<string> {
  // run_as_root: 모든 단건 작업이 root(k8s 신원 모드 -- E7 관례대로 포탈과 같은 명시 true 바디).
  const response = await page.request.post("/api/user/requests", {
    data: { operation: "scan", storage: STORAGE_NAME, target, options: {}, priority: "mid", run_as_root: true },
  });
  expect(response.status(), await response.text()).toBe(202);
  return ((await response.json()) as { request_id: string }).request_id;
}

async function requestState(page: Page, rid: string): Promise<string> {
  const response = await page.request.get(`/api/user/requests/${rid}`);
  if (response.status() !== 200) return `HTTP ${response.status()}`;
  return ((await response.json()) as { state: string }).state;
}

async function batchOf(page: Page, bid: string): Promise<{ status: string; items: { payload: Record<string, string> }[] }> {
  const response = await page.request.get(`/api/admin/batches/${bid}`);
  expect(response.status(), await response.text()).toBe(200);
  return response.json();
}

test.describe("E14 상세 삭제 · 배치 CSV", () => {
  test("끝난 scan 을 요청 상세에서 지우면 목록으로 옮겨 알리고, 상세·API 는 404 다", async ({ page }) => {
    test.setTimeout(90_000);
    await apiLogin(page);
    const rid = await submitScan(page, `e14-${Date.now().toString(36)}`);
    await expect.poll(() => requestState(page, rid), { timeout: 30_000, intervals: [500] }).toBe("Succeeded");
    await page.goto(`/jobs/${rid}`);
    await page.getByRole("button", { name: "작업 삭제" }).click();
    const dialog = page.getByRole("dialog", { name: "작업 영구 삭제" });
    await expect(dialog).toContainText("선택한 작업 1개를 영구 삭제합니다. 되돌릴 수 없습니다.");
    await expect(dialog.getByRole("note")).toContainText("성공한 scan 1개 포함");
    await dialog.getByLabel("되돌릴 수 없음을 확인했습니다").check();
    await dialog.getByRole("button", { name: "1개 영구 삭제" }).click();
    await page.waitForURL(/\/jobs$/);
    await expect(page.getByRole("status").filter({ hasText: "삭제했습니다" }))
      .toContainText(`작업 ${rid.slice(0, 12)}을(를) 삭제했습니다`);
    expect((await page.request.get(`/api/user/requests/${rid}`)).status()).toBe(404);
  });

  test("배치 CSV: 템플릿 다운로드 → 붙여넣기(서버 해석) → 생성 → 「CSV 내보내기」가 같은 항목으로 왕복한다", async ({ page }) => {
    test.setTimeout(90_000);
    await apiLogin(page);
    const tag = `e14csv${Date.now().toString(36)}`;
    await page.goto("/admin/batches/new");
    // 스토리지 드롭다운: 종류별 묶음 + 「이름 (백엔드)」
    const st = page.getByLabel("스토리지", { exact: true });
    await expect(st.getByRole("option", { name: STORAGE_NAME })).toBeAttached();

    // 템플릿(서버) -- BOM + 헤더 + 예시 한 줄, 파일 이름은 연산을 담는다
    const [tpl] = await Promise.all([page.waitForEvent("download"), page.getByRole("link", { name: "템플릿 받기" }).click()]);
    expect(tpl.suggestedFilename()).toBe("dms-batch-template-scan.csv");
    expect(fs.readFileSync(await tpl.path(), "utf8")).toBe("﻿target\r\nteam/projects\r\n");

    // 서버 해석: 잘못된 줄은 줄 번호로 말하고 표를 덮지 않는다 → 고치면 반영 + 스토리지 선택을 CSV 의 스토리지 열로 채운다
    await page.getByRole("button", { name: "CSV 붙여넣기" }).click();
    await page.getByLabel("CSV", { exact: true }).fill(`storage,target\n${STORAGE_NAME},${tag}/a\n${STORAGE_NAME},/abs`);
    await page.getByRole("button", { name: "테이블에 반영" }).click();
    await expect(page.getByRole("list", { name: "반영할 수 없는 줄" })).toContainText("3번째 줄: 경로가 올바르지 않습니다");
    await page.getByLabel("CSV", { exact: true })
      .fill(`﻿storage,target\r\n${STORAGE_NAME},${tag}/a\r\n${STORAGE_NAME},"${tag}/b,c"\r\n`);
    await page.getByRole("button", { name: "테이블에 반영" }).click();
    await expect(page.getByLabel("2행 경로")).toHaveValue(`${tag}/b,c`);
    await expect(st).toHaveValue(STORAGE_NAME);
    await page.getByLabel("배치 이름").fill(tag);
    await page.getByRole("button", { name: "배치 생성" }).click();
    await page.waitForURL(/\/admin\/batches\/[0-9a-f]{32}$/);
    const bid = page.url().split("/").pop()!;
    expect((await batchOf(page, bid)).items.map((i) => i.payload.target)).toEqual([`${tag}/a`, `${tag}/b,c`]);

    // 내보내기(서버) -- 경로·스토리지 + 상태 열, 다시 해석하면 같은 항목
    const [exp] = await Promise.all([page.waitForEvent("download"), page.getByRole("link", { name: "CSV 내보내기" }).click()]);
    expect(exp.suggestedFilename()).toBe(`dms-batch-${bid.slice(0, 12)}-items.csv`);
    const text = fs.readFileSync(await exp.path(), "utf8");
    expect(text.startsWith("﻿seq,storage,target,status,request_id")).toBe(true);
    const back = await page.request.post("/api/admin/batches:parse-csv", { data: { operation: "scan", csv: text } });
    expect(back.status()).toBe(200);
    expect(((await back.json()) as { items: unknown[] }).items)
      .toEqual([{ storage: STORAGE_NAME, target: `${tag}/a` }, { storage: STORAGE_NAME, target: `${tag}/b,c` }]);

    // 배치 기록 삭제: 공유 토큰은 403(세션 관리자 전용), 끝난 뒤 세션은 지우고 감사 행이 남는다
    await expect.poll(async () => (await batchOf(page, bid)).status, { timeout: 60_000, intervals: [500] })
      .toBe("Completed");
    const byToken = await page.request.delete(`/api/admin/batches/${bid}`, {
      headers: { Authorization: `Bearer ${SHARED_TOKEN}` } });
    expect(byToken.status()).toBe(403);
    expect(((await byToken.json()) as { detail: string }).detail).toBe("admin_session_required");
    expect((await page.request.delete(`/api/admin/batches/${bid}`)).status()).toBe(200);
    const audit = (await (await page.request.get("/api/admin/audit-log?limit=50")).json()) as
      { mutation_class: string; operation: string; target_key: string }[];
    expect(audit.some((a) => a.mutation_class === "batch" && a.operation === "delete_record" && a.target_key === bid))
      .toBe(true);
  });
});
