import fs from "node:fs";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { BASE_URL, STORAGE_NAME } from "./harness/env";
import { apiLogin } from "./helpers/session";
import { assertLayoutSane } from "./helpers/layout";

// E15 — 사용량 분석 필터(2026-10-29 사용자 요청 「사용자·PV 이름·namespace 등으로 필터」). 단위 테스트(msw)가 못 보는 것: 실 서버의
// /targets·/facets·/targets/export 가 실 scan 기록으로 요청자 필터를 **대상 단위**로 걸고, 화면이 그 선택을 URL 에 실어 새로고침에도
// 같은 결과를 그리며, CSV 가 같은 필터로 「-filtered」 파일 이름을 쓰는가. 필터 줄이 보이는 채로 375 레이아웃 불변식(L1~L4). 하위
// 탭 왕복(2026-10-29)에 필터가 남는가(경로별 마지막 쿼리 복원). 검색칸(2026-10-29 경로·PV 이름)이 실 서버에서 여러 단어 AND·대소문자
// 무시로 걸리고 맞은 글자를 강조하는가.
// 하네스엔 k8s 클러스터가 없어 PV 축(클러스터·namespace·PV 이름 …)은 고를 수 없다 -- 요청자 축만 실데이터로 잰다(PV 축의 필터 줄
// 375 는 E3(03-layout)의 딥링크가 잰다). 요청자 축의 선택지가 둘 이상이어야 select 가 보이므로, 두 번째 특권 관리자(계정 + 자격 부여
// -- scan 은 특권 운영자만 낸다, E11 과 같은 경로)가 scan 하나를 낸다.

const RUN = Date.now().toString(36);
const OTHER = `e15req${RUN}`;
const PW = "e15-req-pw";
const MINE = `e15-admin-${RUN}`;
const THEIRS = `e15-other-${RUN}`;

async function requestState(request: APIRequestContext, rid: string): Promise<string> {
  const response = await request.get(`/api/user/requests/${rid}`);
  if (response.status() !== 200) return `HTTP ${response.status()}`;
  return ((await response.json()) as { state: string }).state;
}

async function scan(request: APIRequestContext, target: string): Promise<void> {
  const response = await request.post("/api/user/requests", {
    data: { operation: "scan", storage: STORAGE_NAME, target, options: {}, priority: "mid", run_as_root: true } });
  expect(response.status(), await response.text()).toBe(202);
  const rid = ((await response.json()) as { request_id: string }).request_id;
  await expect.poll(() => requestState(request, rid), { timeout: 60_000, intervals: [500],
    message: `scan ${target} 이 60s 안에 끝나지 않았다` }).toBe("Succeeded");
}

// 경로별 패널 = 스토리지 이름의 구역(2026-10-29 -- 예전 탭 패널).
const panelOf = (page: Page) => page.getByRole("region", { name: STORAGE_NAME, exact: true });
function rowsOf(page: Page, target: string) {
  return panelOf(page).locator("table tbody tr").filter({ hasText: target });
}

test.describe("E15 — 사용량 분석 필터", () => {
  test("요청자 필터: 고르면 URL 에 실리고 새로고침에도 남으며, CSV 는 같은 필터·-filtered 이름 · 375 필터 줄 · 지우기", async ({
    page, browser }) => {
    await apiLogin(page);
    // 1) 두 번째 특권 관리자: 계정 + 「전체 PV·직접 경로 권한」(특권 세션 관리자만 줄 수 있다 -- 하네스 admin)
    const created = await page.request.post("/api/admin/accounts", { data: { username: OTHER, password: PW, role: "admin" } });
    expect(created.status(), await created.text()).toBe(201);
    const granted = await page.request.put(`/api/admin/accounts/${OTHER}/root`, { data: { granted: true } });
    expect(granted.status(), await granted.text()).toBe(200);
    const other = await browser.newContext({ baseURL: BASE_URL });
    const login = await other.request.post("/api/auth/login", { data: { username: OTHER, password: PW } });
    expect(login.status(), await login.text()).toBe(200);

    // 2) 시드 스토리지에 scan 둘 -- 요청자가 다르다(요청자 = 제출 계정, requests.requester_id).
    await scan(page.request, MINE);
    await scan(other.request, THEIRS);
    await other.close();

    // 3) 사용량 화면: 요청자 select(선택지 = 이 스토리지를 scan 한 계정들, 대상 수) → 고르면 URL·목록·건수.
    await page.goto(`/admin/usage?storage=${STORAGE_NAME}`);
    const panel = panelOf(page);
    await expect(rowsOf(page, MINE)).toHaveCount(1);
    const filters = panel.getByRole("group", { name: "사용량 필터" });
    const requester = filters.getByRole("combobox", { name: "요청자 필터" });
    await expect(requester).toBeVisible();
    await requester.selectOption({ label: `${OTHER} · 1개` });
    await expect(page).toHaveURL(new RegExp(`[?&]requester=${OTHER}(&|$)`));
    await expect(rowsOf(page, THEIRS)).toHaveCount(1);
    await expect(rowsOf(page, MINE)).toHaveCount(0);
    await expect(panel.getByText("필터에 맞는 1개", { exact: true })).toBeVisible();
    // 합계 줄(2026-10-29 한 줄 + [자세히]): 필터가 켜지면 같은 자리가 「필터 합계」 한 줄이 된다(두 벌이 아니다). 적용 필터
    // 문장은 [자세히] 안 -- 열면 보인다(기본 닫힘).
    const totals = panel.getByRole("region", { name: "필터 합계" });
    await expect(totals).toBeVisible();
    await expect(panel.getByRole("region", { name: "이 스토리지 합계" })).toHaveCount(0);
    const applied = totals.getByText(`적용 필터: 요청자 ${OTHER}`);
    await expect(applied).toBeHidden();
    await totals.getByText("자세히", { exact: true }).click();
    await expect(applied).toBeVisible();

    // 4) 새로고침: URL 이 진실 -- 같은 선택·같은 목록
    await page.reload();
    await expect(requester).toHaveValue(OTHER);
    await expect(rowsOf(page, THEIRS)).toHaveCount(1);
    await expect(rowsOf(page, MINE)).toHaveCount(0);

    // 5) CSV: 같은 필터로 받고 파일 이름에 -filtered, 행은 걸러진 것만
    const [download] = await Promise.all([
      page.waitForEvent("download"), panel.getByRole("button", { name: "CSV 내보내기" }).click()]);
    expect(download.suggestedFilename()).toMatch(new RegExp(`^dms-usage-${STORAGE_NAME}-filtered-\\d{8}-\\d{4}\\.csv$`));
    const text = fs.readFileSync((await download.path())!, "utf8").replace(/^﻿/, "");
    const [header, ...lines] = text.split("\r\n").filter(Boolean);
    const cols = header.split(",");
    expect(cols.slice(-8)).toEqual(["k8s_source", "k8s_cluster", "k8s_namespace", "k8s_pvc", "k8s_volume",
                                    "k8s_storage_class", "k8s_pv_root", "k8s_access_users"]);
    expect(lines.map((l) => l.split(",")[cols.indexOf("target")])).toEqual([THEIRS]);
    // filesystem 스토리지라 PV 밖(직접 경로)
    expect(lines[0].split(",")[cols.indexOf("k8s_source")]).toBe("outside");

    // 6) 375: 필터 줄이 보이는 채로 레이아웃 불변식(표 th 6 + 1행 td 6 -- 시드 스토리지는 filesystem 이라 「PV 이름」 열이 없다,
    //    2026-10-29)
    await page.setViewportSize({ width: 375, height: 667 });
    await expect(filters).toBeVisible();
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 12 });
    await page.setViewportSize({ width: 1280, height: 800 });

    // 7) 탭 왕복(2026-10-29): 스토리지별로 갔다가 「경로별」 탭으로 돌아오면 마지막 쿼리(필터)가 복원된다(sessionStorage).
    await page.getByRole("navigation", { name: "사용량 분석 하위 메뉴" }).getByRole("link", { name: "스토리지별" }).click();
    await expect(page.getByRole("heading", { name: "스토리지별", level: 1 })).toBeVisible();
    await page.getByRole("navigation", { name: "사용량 분석 하위 메뉴" }).getByRole("link", { name: "경로별" }).click();
    await expect(page).toHaveURL(new RegExp(`[?&]requester=${OTHER}(&|$)`));
    await expect(requester).toHaveValue(OTHER);
    await expect(rowsOf(page, THEIRS)).toHaveCount(1);
    await expect(rowsOf(page, MINE)).toHaveCount(0);

    // 8) 필터 지우기 → URL 에서 빠지고 전체 목록
    await filters.getByRole("button", { name: "필터 지우기" }).click();
    await expect(page).not.toHaveURL(/requester=/);
    await expect(rowsOf(page, MINE)).toHaveCount(1);
    await expect(rowsOf(page, THEIRS)).toHaveCount(1);

    // 9) 검색(2026-10-29 사용자 요청 「경로로 검색할 뿐 아니라 pv 이름도 … 일부 단어만 포함해도」): 실 서버의 q 가 토큰마다 대소문자
    //    무시 부분 일치·토큰 사이 AND 로 걸고(작업 목록 검색과 같은 뜻), 맞은 글자가 경로 칸에서 <mark> 로 강조된다. 시드 스토리지는
    //    filesystem 이라 서버는 SQL 경로(정확)로 걸고 「PV 이름」 열은 없다. 버튼 이름은 강조와 무관하게 경로 그대로다.
    const search = panel.getByRole("searchbox", { name: "경로·PV 이름 검색" });
    await search.fill(`ADMIN ${RUN.toUpperCase()}`);
    await expect(page).toHaveURL(/[?&]q=ADMIN(\+|%20)/);
    await expect(rowsOf(page, THEIRS)).toHaveCount(0);
    await expect(rowsOf(page, MINE)).toHaveCount(1);
    await expect(rowsOf(page, MINE).locator("mark")).toHaveText(["admin", RUN]);
    await expect(panel.getByRole("button", { name: MINE, exact: true })).toBeVisible();
    await expect(panel.getByRole("columnheader", { name: "PV 이름" })).toHaveCount(0);
    await search.fill("");
    await expect(page).not.toHaveURL(/[?&]q=/);
    await expect(rowsOf(page, THEIRS)).toHaveCount(1);
    await expect(rowsOf(page, MINE).locator("mark")).toHaveCount(0);
  });
});
