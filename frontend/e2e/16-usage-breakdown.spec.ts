import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { BASE_URL, STORAGE_NAME } from "./harness/env";
import { apiLogin } from "./helpers/session";
import { assertLayoutSane } from "./helpers/layout";

// E16 — 사용량 분석 「소속별」(2026-10-30 2단계, 서버 /breakdown). 단위 테스트(msw)가 못 보는 것: 실 서버가 실 scan 기록으로 요청자
// 묶음을 세고(하네스엔 k8s 클러스터가 없어 시드 스토리지는 filesystem -- PV 축을 요청해도 서버가 요청자로 적용하고 화면은 PV 축을
// disabled 로 둔다), 행을 누르면 그 요청자 필터가 걸린 경로별 목록에 그 요청자의 경로만 남는가(행 값 = 경로별 필터 합계 -- 오라클
// 불변식의 수치 대조는 백엔드 테스트 test_api_usage_breakdown 이 한다). 390 에서 순위 행이 두 줄(이름 · 실 사용량 / 비중 · 대상 · 파일)로
// 접혀 문서가 가로로 넘치지 않는가(L1). 순위 표는 ARIA 격자라 td·th 가 없다(L2 하한 0 -- 칸을 두 줄로 접으려면 table-cell 일 수 없다).
// 요청자가 둘이어야 묶음이 둘이므로 E15 처럼 두 번째 특권 관리자(계정 + 자격 부여 -- scan 은 특권 운영자만 낸다)가 scan 하나를 낸다.

const RUN = Date.now().toString(36);
const OTHER = `e16req${RUN}`;
const PW = "e16-req-pw";
const MINE = `e16-admin-${RUN}`;
const THEIRS = `e16-other-${RUN}`;

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

// 경로별 패널 = 스토리지 이름의 구역(E15 와 같다).
const pathsPanel = (page: Page) => page.getByRole("region", { name: STORAGE_NAME, exact: true });
function pathRows(page: Page, target: string) {
  return pathsPanel(page).locator("table tbody tr").filter({ hasText: target });
}

test.describe("E16 — 사용량 분석 소속별", () => {
  test("요청자 순위(PV 축은 서버가 요청자로 적용) → 행 = 그 요청자 필터의 경로별 · 390 두 줄 행 · 뒤로 가기", async ({
    page, browser }) => {
    await apiLogin(page);
    // 1) 두 번째 특권 관리자(E15 와 같은 경로): 계정 + 「전체 PV·직접 경로 권한」
    const created = await page.request.post("/api/admin/accounts", { data: { username: OTHER, password: PW, role: "admin" } });
    expect(created.status(), await created.text()).toBe(201);
    const granted = await page.request.put(`/api/admin/accounts/${OTHER}/root`, { data: { granted: true } });
    expect(granted.status(), await granted.text()).toBe(200);
    const other = await browser.newContext({ baseURL: BASE_URL });
    const login = await other.request.post("/api/auth/login", { data: { username: OTHER, password: PW } });
    expect(login.status(), await login.text()).toBe(200);

    // 2) 시드 스토리지에 scan 둘 -- 요청자가 다르다(요청자 = 제출 계정).
    await scan(page.request, MINE);
    await scan(other.request, THEIRS);
    await other.close();

    // 3) 소속별: PV 축(namespace)을 딥링크로 요청해도 filesystem 스토리지라 서버는 요청자로 적용한다 -- 화면은 적용한 기준을 보이고
    //    PV 축 다섯을 disabled 로, 그 사실을 한 문장으로 말한다. URL 의 by 는 건드리지 않는다.
    await page.goto(`/admin/usage/breakdown?storage=${STORAGE_NAME}&by=namespace`);
    await expect(page.getByRole("heading", { name: "소속별", level: 1 })).toBeVisible();
    const nav = page.getByRole("navigation", { name: "사용량 분석 하위 메뉴" });
    await expect(nav.getByRole("link", { name: "소속별", exact: true })).toHaveAttribute("aria-current", "page");
    const by = page.getByRole("combobox", { name: "기준", exact: true });
    await expect(by).toHaveValue("requester");
    await expect(page.getByText("이 스토리지에는 PV 안 대상이 없습니다 — 요청자 기준만 볼 수 있습니다")).toBeVisible();
    for (const v of ["user", "namespace", "pvc", "storage_class", "cluster"]) {
      await expect(by.locator(`option[value="${v}"]`)).toBeDisabled();
    }
    await expect(page).toHaveURL(/[?&]by=namespace(&|$)/);
    const table = page.getByRole("table", { name: "요청자별 실 사용량 순위" });
    const row = table.getByRole("row").filter({ has: page.getByRole("link", { name: OTHER, exact: true }) });
    await expect(row).toHaveCount(1);
    // 대상 칸(좁은 폭의 「대상 」·「개」는 xl 에서 숨긴 글자라 textContent 엔 늘 있다)
    await expect(row.getByRole("cell").nth(1)).toHaveText("대상 1개");

    // 4) 1280(xl 이상): 한 줄 표 -- 대상 칸이 이름 칸과 같은 줄. 390: 두 줄 행 -- 대상 칸이 이름 칸 아래 줄, 문서 가로 넘침 0(L1).
    const box = async (i: number) => (await row.getByRole("cell").nth(i).boundingBox())!;
    expect(Math.abs((await box(1)).y - (await box(0)).y)).toBeLessThan(8);
    await page.setViewportSize({ width: 390, height: 844 });
    await assertLayoutSane(page, { sidebarFixed: false });
    const [name390, targets390] = [await box(0), await box(1)];
    expect(targets390.y).toBeGreaterThanOrEqual(name390.y + name390.height - 1);
    const rowBox = (await row.boundingBox())!;
    expect(rowBox.x + rowBox.width).toBeLessThanOrEqual(390);
    await page.setViewportSize({ width: 1280, height: 800 });

    // 5) 행(이름 링크) = 그 요청자 필터의 경로별(push, 다른 쿼리 없이) -- 목록엔 그 요청자의 경로만
    await row.getByRole("link", { name: OTHER, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/admin/usage\\?storage=${STORAGE_NAME}&requester=${OTHER}$`));
    await expect(page.getByRole("heading", { name: "사용량 분석", level: 1 })).toBeVisible();
    await expect(pathsPanel(page).getByRole("combobox", { name: "요청자 필터" })).toHaveValue(OTHER);
    await expect(pathRows(page, THEIRS)).toHaveCount(1);
    await expect(pathRows(page, MINE)).toHaveCount(0);
    await expect(pathsPanel(page).getByText("필터에 맞는 1개", { exact: true })).toBeVisible();

    // 6) 뒤로 가기 = 소속별(같은 기준·같은 스토리지)
    await page.goBack();
    await expect(page.getByRole("heading", { name: "소속별", level: 1 })).toBeVisible();
    await expect(page).toHaveURL(/[?&]by=namespace(&|$)/);
    await expect(row).toHaveCount(1);
  });
});
