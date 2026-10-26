import { expect, test, type Page } from "@playwright/test";
import { STORAGE_NAME } from "./harness/env";
import { apiLogin } from "./helpers/session";

// E13 — 전체 작업 검색(2026-10-25, dms-k8s). 사용자 요청: 「요청자 아이디로만 필터를 걸수 있는데 배치 이름, 대상 (경로)
// 등으로도 검색 및 필터링 … 일부 단어라도 포함하거나 매치되면 그것도 보여주도록 (exact match 완화)」.
//
// 단위 테스트가 못 보는 것을 여기서 본다: **실 서버 SQL(sqlite)** 의 부분 일치(대소문자 무시·띄어 쓴 단어 AND·scan 키의
// 접두/지문 제외), 실 오케스트레이터가 만든 배치 자식의 이름·id 앞부분 검색, 그리고 화면이 글자마다가 아니라 타이핑이
// 멈춘 뒤 한 번 묻는가(디바운스). 같은 DB 를 다른 시나리오와 나눠 쓰므로 대상·배치 이름에 실행마다 다른 꼬리표를 단다.

async function submitScan(page: Page, target: string): Promise<string> {
  // run_as_root: 모든 단건 작업이 root(k8s 신원 모드 -- E5/E6/E7 관례대로 포탈과 같은 명시 true 바디).
  const response = await page.request.post("/api/user/requests", {
    data: { operation: "scan", storage: STORAGE_NAME, target, options: {}, priority: "mid", run_as_root: true },
  });
  expect(response.status(), await response.text()).toBe(202);
  return ((await response.json()) as { request_id: string }).request_id;
}

async function createScanBatch(page: Page, name: string, targets: string[]): Promise<string> {
  const response = await page.request.post("/api/admin/batches", {
    data: { operation: "scan", max_concurrency: 1, options: {}, name,
            items: targets.map((target) => ({ storage: STORAGE_NAME, target })) },
  });
  expect(response.status(), await response.text()).toBe(202);
  return ((await response.json()) as { batch_id: string }).batch_id;
}

async function batchChildren(page: Page, bid: string): Promise<string[]> {
  const response = await page.request.get(`/api/user/requests?batch_id=${bid}`);
  if (response.status() !== 200) return [];
  return ((await response.json()) as { request_id: string }[]).map((r) => r.request_id);
}

test("E13 전체 작업 검색: 대상 경로·배치 이름·요청자를 부분 일치로 거르고, 타이핑이 멈춘 뒤 한 번 묻는다", async ({ page }) => {
  test.setTimeout(60_000);
  await apiLogin(page);
  const tag = `srch${Date.now().toString(36)}`;
  const growth = await submitScan(page, `${tag}/Growth-Alpha`);
  const other = await submitScan(page, `${tag}/beta`);
  const bid = await createScanBatch(page, `${tag} 아카이브 이관(1차)`, [`${tag}/batch-a`]);
  // 오케스트레이터(하네스 1초 틱)가 항목의 자식 요청을 만들 때까지.
  await expect.poll(() => batchChildren(page, bid).then((c) => c.length), { timeout: 20_000 }).toBeGreaterThan(0);
  const [child] = await batchChildren(page, bid);

  const pathQueries: string[] = [];
  page.on("request", (req) => {
    const u = new URL(req.url());
    if (u.pathname === "/api/user/requests" && u.searchParams.has("path")) pathQueries.push(u.searchParams.get("path")!);
  });
  await page.goto("/jobs");
  const rowOf = (rid: string) => page.getByRole("row").filter({ hasText: rid });
  await expect(rowOf(growth)).toHaveCount(1);
  await expect(rowOf(other)).toHaveCount(1);

  // 대상 경로: 대소문자를 가리지 않고, 띄어 쓴 단어는 순서와 무관하게 모두 들어 있으면.
  const typed = `GROWTH ${tag.toUpperCase()}`;
  await page.getByLabel("대상 경로 필터").pressSequentially(typed, { delay: 30 });
  await expect(rowOf(growth)).toHaveCount(1);
  await expect(rowOf(other)).toHaveCount(0);
  await expect(rowOf(child)).toHaveCount(0);
  // 글자마다(G·GR·GRO…) 묻지 않았다 -- 서버에 간 path 는 다 친 값 하나뿐이다(10초 폴링이 같은 값으로 다시 물을 수는 있다).
  expect(pathQueries.length).toBeGreaterThan(0);
  expect(new Set(pathQueries)).toEqual(new Set([typed]));
  // scan 키의 접두(data.scan:)·옵션 지문은 화면에 없는 글자라 걸리지 않는다.
  await page.getByLabel("대상 경로 필터").fill(`data.scan ${tag}`);
  await expect(page.getByText("조건에 맞는 작업이 없습니다")).toBeVisible();
  await page.getByLabel("대상 경로 필터").fill("");
  await expect(rowOf(other)).toHaveCount(1);

  // 배치 이름: 일부 단어(순서 무관) -- 배치 작업만 남는다.
  await page.getByLabel("배치 이름 필터").fill(`이관 ${tag}`);
  await expect(rowOf(child)).toHaveCount(1);
  await expect(rowOf(growth)).toHaveCount(0);
  // 배치 ID 앞부분(화면이 이름 없는 배치·기록 없는 묶음을 id 앞 12자로 보인다). 그 검색어가 실린 **응답을 기다린 뒤**
  // 단언한다 -- 바로 앞 결과(child 만)가 이미 같은 모양이라, 디바운스 창 안에서 단언하면 아무것도 검증하지 않는다(리뷰).
  const idPrefix = bid.slice(0, 12).toUpperCase();
  const idResp = page.waitForResponse((r) => new URL(r.url()).searchParams.get("batch_name") === idPrefix);
  await page.getByLabel("배치 이름 필터").fill(idPrefix);
  await idResp;
  await expect(rowOf(child)).toHaveCount(1);
  await expect(rowOf(growth)).toHaveCount(0);
  await page.getByLabel("배치 이름 필터").fill("");

  // 요청자: 관리자 아이디(admin)의 일부, 대소문자 무시 -- 예전엔 정확히 같은 아이디만 맞았다. 세 칸의 디바운스가 따로
  // 적용되므로 요청자·경로가 함께 실리고 배치 칸은 빈 **최종 키의 응답**을 기다린 뒤 단언한다(리뷰 -- 중간 키의 결과로
  // 통과하지 않게).
  const dmiResp = page.waitForResponse((r) => {
    const p = new URL(r.url()).searchParams;
    return p.get("requester") === "DMI" && p.get("path") === tag && !p.has("batch_name");
  });
  await page.getByLabel("대상 경로 필터").fill(tag);
  await page.getByLabel("요청자 필터").fill("DMI");
  await dmiResp;
  await expect(rowOf(growth)).toHaveCount(1);
  await expect(rowOf(child)).toHaveCount(1);
  await page.getByLabel("요청자 필터").fill("zz-nobody");
  await expect(page.getByText("조건에 맞는 작업이 없습니다")).toBeVisible();
});
