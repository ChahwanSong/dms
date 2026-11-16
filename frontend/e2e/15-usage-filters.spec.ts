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

  // 2026-11-16 사용자 요청 「상위 100개만이 아니라 전체 목록(몇백~몇천 개여도 버벅이지 않게)」 + 「작업 화면의 데이터 온도 히스토그램·파일
  // 크기 분포를 사용량에서도 최신 scan 기준으로 같은 모양으로」. 실 scan 101회는 비용이 커서 목록 봉투(/targets)만 page.route 로 합성한다
  // (실 scan 하나의 실제 행을 250개로 복제 -- 실 행은 2쪽 첫 줄, 쪽마다 offset 메아리·has_more·pageable_count). 합계 등 나머지 API 는
  // 실 하네스. 다만 하네스의 가짜 dscan 리포트는 읽히지 않는다(E10 주석 -- 행은 DB 용량, 이력은 skipped_unreadable 로 비어 시트는
  // 「표시할 이력 없음」 한 줄). 그래서 두 차트를 실 브라우저 폭(390)으로 재려고, 그 실 scan 의 포인트(같은 job_id·request_id)를 읽힌
  // 리포트 모양(온도 9구간 × atime·mtime·ctime, 크기 10구간 -- 라이브 dscan 과 같은 구간 수)으로 바꿔 목록 행의 latest 와 이력
  // (scan-history) 양쪽에 싣는다. 단위 테스트(TargetSheet.test)가 못 보는 것은 값이 아니라 실 폭의 넘침·축 토글이다.
  test("쪽 넘김(합성 250행): Pager 375 한 줄·?page= · 2쪽의 실 경로 시트에 최신 scan 온도·크기 차트(390 가로 넘침 없음) · 쪽 경계 이전", async ({
    page }) => {
    await apiLogin(page);
    const real = `e15-page-${RUN}`;
    await scan(page.request, real);
    const requested: number[] = [];
    const AGES: [number, number | null][] = [[0, 1], [1, 7], [7, 30], [30, 90], [90, 180], [180, 365], [365, 730], [730, 1825],
                                             [1825, null]];
    const temp = AGES.map(([lo, hi], i) => ({ bucket: hi === null ? `[${lo}d,)` : `[${lo}d,${hi}d]`, min_age_days: lo,
                                              ...(hi === null ? {} : { max_age_days: hi }), count: i + 1, bytes: (i + 1) * 1_048_576 }));
    // 크기 구간 = 라이브 dscan 실측(ScanHistograms.test LIVE_SIZE 와 같은 경계 -- 0~4K … 4T~, 마지막은 상한 없음)
    const BOUNDS: [number, number | null][] = [[0, 4096], [4097, 65536], [65537, 1048576], [1048577, 16777216],
      [16777217, 268435456], [268435457, 1073741824], [1073741825, 17179869184], [17179869185, 274877906944],
      [274877906945, 4398046511104], [4398046511105, null]];
    const size = BOUNDS.map(([lo, hi], i) => ({ bucket: hi === null ? `[${lo},)` : `[${lo},${hi}]`, lower_inclusive: lo,
                                                ...(hi === null ? {} : { upper_inclusive: hi }), count: 10 - i }));
    const totalBytes = temp.reduce((s, b) => s + b.bytes, 0);
    // 실 scan 포인트(못 읽음) → 같은 잡의 읽힌 포인트
    const readable = (p: Record<string, unknown>) => ({
      ...p, report_readable: true, generated_at_epoch: Math.floor(Date.now() / 1000), total_bytes: totalBytes,
      summary: { total_bytes: totalBytes, total_files: 55, total_dirs: 3 },
      time_histograms: { atime: temp, mtime: temp, ctime: temp }, file_size_histogram: size,
      broken_paths_total: 0, broken_paths_limit: 100, invalid_name_entries: 0 });
    let realLatest: Record<string, unknown> | null = null;
    await page.route(/\/api\/admin\/usage\/scan-history\?/, async (route) => {
      const url = new URL(route.request().url());
      if (url.searchParams.get("target") !== real) return route.continue();
      const upstream = await route.fetch();
      const body = (await upstream.json()) as { latest_job_id?: string | null; points: unknown[] };
      expect(realLatest, "목록 행보다 이력을 먼저 불렀다").not.toBeNull();
      expect(body.latest_job_id, "이력의 최신 scan 이 목록 행의 latest 와 다르다").toBe(realLatest!.job_id);
      await route.fulfill({ response: upstream, json: { ...body, points: [readable(realLatest!)], skipped_unreadable: 0 } });
    });
    await page.route(/\/api\/admin\/usage\/targets\?/, async (route) => {
      const url = new URL(route.request().url());
      const offset = Number(url.searchParams.get("offset") ?? "0");
      const limit = Number(url.searchParams.get("limit") ?? "100");
      requested.push(offset);
      // 실 서버에서 실 행(그 경로만 걸러)과 메아리를 받아 쓴다 -- 검색 없이 보낸 것으로(합성 행의 이름이 검색과 무관하게)
      url.searchParams.set("q", real); url.searchParams.set("offset", "0");
      const upstream = await route.fetch({ url: url.toString() });
      const body = (await upstream.json()) as { rows: { target: string; latest?: Record<string, unknown> | null }[];
                                               filters: Record<string, unknown> };
      const found = body.rows.find((r) => r.target === real);
      expect(found, "실 scan 행이 목록에 없다").toBeTruthy();
      expect(found!.latest, "실 scan 행에 latest 가 없다").toBeTruthy();
      realLatest = found!.latest!;
      const realRow = { ...found!, latest: readable(realLatest) };
      const all = Array.from({ length: 250 }, (_, i) => (i === 100 ? realRow : { ...realRow, target: `e15-syn-${String(i).padStart(3, "0")}` }));
      await route.fulfill({ response: upstream, json: {
        ...body, rows: all.slice(offset, offset + limit), matched_count: 250, window_truncated: false,
        filters: { ...body.filters, q: null }, offset, has_more: offset + limit < 250, pageable_count: 250 } });
    });
    await page.goto(`/admin/usage?storage=${STORAGE_NAME}`);
    const panel = panelOf(page);
    await expect(rowsOf(page, "e15-syn-000")).toHaveCount(1);
    await expect(panel.getByText("1–100 / 전체 250개", { exact: true })).toBeVisible();
    const pager = panel.getByRole("navigation", { name: "경로 목록 쪽" });
    await expect(pager).toContainText("1 / 3쪽");
    await expect(panel.locator("table tbody tr")).toHaveCount(100);
    // 375: 표(가로 스크롤 래퍼) 밖 가운데 한 줄 -- 높이가 버튼 한 줄, 문서 가로 넘침 없음
    await page.setViewportSize({ width: 375, height: 667 });
    await expect(pager).toBeVisible();
    const box = (await pager.boundingBox())!;
    expect(box.height, "Pager 가 375 에서 두 줄로 꺾였다").toBeLessThan(48);
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(375);
    expect(await pager.evaluate((el) => el.closest(".overflow-x-auto") === null)).toBe(true);
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 12 });
    await page.setViewportSize({ width: 1280, height: 800 });
    // 다음 쪽 → ?page=2, 실 행이 첫 줄
    await pager.getByRole("button", { name: "다음" }).click();
    await expect(page).toHaveURL(/[?&]page=2(&|$)/);
    await expect(rowsOf(page, real)).toHaveCount(1);
    await expect(panel.getByText("101–200 / 전체 250개", { exact: true })).toBeVisible();
    expect(requested).toContain(100);
    // 390: 실 경로의 시트(md 미만 전체 화면) -- 최신 scan 의 온도 히스토그램·크기 분포(배치 scan 항목과 같은 차트), 가로 넘침 없음
    await page.setViewportSize({ width: 390, height: 844 });
    await panel.getByRole("button", { name: real, exact: true }).click();
    const sheet = page.getByRole("dialog", { name: real });
    await expect(sheet).toBeVisible();
    await expect(sheet.getByText(/목록 101\/250번째/)).toBeVisible();
    await expect(sheet.getByRole("img", { name: /^데이터 온도\((atime|mtime|ctime)\) 히스토그램$/ })).toBeVisible();
    await expect(sheet.getByRole("img", { name: "파일 크기 분포" })).toBeVisible();
    const overflow = await page.evaluate(() => {
      const body = document.querySelector<HTMLElement>("[data-sheet-body]");
      return { doc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
               sheet: body === null ? 0 : body.scrollWidth - body.clientWidth };
    });
    expect(overflow, "390 시트에 가로 넘침").toEqual({ doc: 0, sheet: 0 });
    // 쪽의 첫 행에서 이전 경로 = 1쪽 끝 행(쪽과 경로를 한 번에)
    await sheet.getByRole("button", { name: "이전 경로" }).click();
    await expect(page.getByRole("dialog", { name: "e15-syn-099" })).toBeVisible();
    await expect(page).not.toHaveURL(/[?&]page=/);
    await expect(page).toHaveURL(/[?&]target=e15-syn-099(&|$)/);
    await page.setViewportSize({ width: 1280, height: 800 });
  });
});
