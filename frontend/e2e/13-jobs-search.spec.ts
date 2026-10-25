import { expect, test, type Page } from "@playwright/test";
import { STORAGE_NAME } from "./harness/env";
import { apiLogin } from "./helpers/session";
import { assertLayoutSane } from "./helpers/layout";

// E14 — 전체 작업 검색(2026-10-25 사용자 요청: 배치 이름·경로 등으로 찾기, 일부 단어만 맞아도 보이기). 단위 테스트(jsdom)가
// 못 보는 것을 여기서 본다: 실 브라우저의 키 입력·디바운스 타이머가 낳는 **실 HTTP 주소**(q, trim, 글자마다 묻지 않음),
// 검색 칸·빈 결과 안내가 좁은 폭(375)·넓은 폭(1280)에서 문서를 넓히지 않는가(L1), 필터 칸 높이·안내 글 잘림(실 글꼴),
// 「검색 지우기」가 목록을 되돌리는가. 첫 시나리오는 기하·디바운스를 보려고 목록 응답만 흉내 낸다(인증·나머지는 진짜 --
// 흉내 서버는 q 의 낱말 중 하나라도 대상 경로에 들어 있으면 그 행을 준다). 둘째 시나리오는 흉내 없이 **실 서버**의 q 가
// 화면까지 이어지는가를 본다(2026-10-25 리뷰: 흉내만 있으면 파라미터 이름·의미가 어느 쪽에서 바뀌어도 e2e 가 모른다 --
// 의미의 세부(이스케이프·비관리자 격리·절대경로 펼침)는 pytest 몫).

/** 필터 줄 기하 -- 칸 높이(검색·연산·상태·요청자)와 검색 칸 안내 글(placeholder)이 칸 안쪽 폭에 들어가는가(실 글꼴로 잰
 *  글자 폭 ≤ 칸 폭 - 좌우 여백). 2026-10-25 리뷰 실측: 글 칸 38px · select 40px 로 한 줄의 높이가 어긋났고, 관리자 안내
 *  「요청자·배치 이름·경로·요청 ID 검색」(214px)은 768 에서 칸 안쪽 208px 를 넘어 끝 글자가 잘렸다. 칸 폭의 바닥은 검색 칸의
 *  고유 폭(flex-basis)이라 ~754px 에서 200px 이 가장 좁다(340~1600px 2px 간격 실측). */
async function filterGeometry(page: Page) {
  return page.evaluate(() => {
    const el = <T extends HTMLElement>(sel: string) => document.querySelector<T>(sel)!;
    const search = el<HTMLInputElement>('input[aria-label="작업 검색"]');
    const height = (sel: string) => el(sel).getBoundingClientRect().height;
    const cs = getComputedStyle(search);
    const ctx = document.createElement("canvas").getContext("2d")!;
    ctx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    return {
      heights: [height('input[aria-label="작업 검색"]'), height('select[aria-label="연산 필터"]'),
                height('select[aria-label="상태 필터"]'), height('input[aria-label="요청자 필터"]')],
      placeholderNeed: ctx.measureText(search.placeholder).width,
      placeholderRoom: search.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight),
    };
  });
}

// --- 실 서버 시나리오 픽스처(06 과 같은 모양 -- 스펙마다 자기 것을 둔다) ---
// run_as_root: LDAP 없는 하네스에서 잡이 끝까지 가려면 root 여야 한다(서버는 명시 true 만 root).
async function submitScan(page: Page, target: string): Promise<string> {
  const response = await page.request.post("/api/user/requests", {
    data: { operation: "scan", storage: STORAGE_NAME, target, options: {}, priority: "mid", run_as_root: true },
  });
  expect(response.status(), await response.text()).toBe(202);
  return ((await response.json()) as { request_id: string }).request_id;
}

// scan 배치는 만들면 곧 Running 이고 오케스트레이터(하네스 1초 틱)가 항목마다 자식 요청을 만든다(특권 게이트 --
// apiLogin 의 admin 세션).
async function createScanBatch(page: Page, name: string, targets: string[]): Promise<string> {
  const response = await page.request.post("/api/admin/batches", {
    data: { operation: "scan", max_concurrency: 2, options: {}, name,
            items: targets.map((target) => ({ storage: STORAGE_NAME, target })) },
  });
  expect(response.status(), await response.text()).toBe(202);
  const bid = ((await response.json()) as { batch_id?: string }).batch_id;
  expect(bid, "202 응답에 batch_id 가 없다").toMatch(/^[0-9a-f]{32}$/);
  return bid!;
}

async function batchCompleted(page: Page, bid: string): Promise<void> {
  await expect.poll(async () => {
    const response = await page.request.get(`/api/admin/batches/${bid}`);
    return response.status() === 200 ? ((await response.json()) as { status: string }).status : `HTTP ${response.status()}`;
  }, { timeout: 60_000, intervals: [500],
       message: `배치 ${bid} 가 60s 안에 Completed 가 되지 않았다 -- 오케스트레이터·플래너·스테퍼(controller.log)를 보라`,
  }).toBe("Completed");
}

/** 검색 없는 목록 API 로 본 그 배치의 자식 요청 id. */
async function batchChildren(page: Page, bid: string): Promise<string[]> {
  const response = await page.request.get("/api/user/requests?limit=200");
  expect(response.status(), await response.text()).toBe(200);
  return ((await response.json()) as { request_id: string; batch_id?: string | null }[])
    .filter((r) => r.batch_id === bid).map((r) => r.request_id);
}

test.describe("E14 전체 작업 검색", () => {
  test("검색어는 멈춘 뒤 q 로 실리고 목록이 좁혀진다 · 결과가 없으면 「검색 지우기」로 되돌린다 · 375·1280 에서 넘치지 않는다 · 칸 높이가 같고 안내 글이 잘리지 않는다", async ({ page }) => {
    await apiLogin(page);
    const hex = (n: number) => `${n.toString(16).padStart(4, "0")}${"cd".repeat(14)}`;
    const row = (n: number, target: string) => ({
      request_id: hex(n), operation: "scan", state: "Succeeded", priority: "mid", requester_id: "e13", actor: "e13",
      resource_key: `data.scan:${STORAGE_NAME}:${target}:${"0".repeat(64)}`, commit_order: n,
      created_at: "2026-10-25T03:04:05Z", updated_at: "2026-10-25T03:05:06Z",
      payload: { storage: STORAGE_NAME, target }, batch_id: null,
    });
    const ROWS = [row(3, "/e13/alpha-proj"), row(2, "/e13/beta"), row(1, "/e13/gamma")];
    const seen: URL[] = [];
    await page.route(/\/api\/user\/requests\?/, (route) => {
      const url = new URL(route.request().url());
      seen.push(url);
      const q = url.searchParams.get("q");
      if (q === null) return route.fulfill({ json: ROWS });
      const words = q.split(/\s+/);
      return route.fulfill({ json: ROWS.filter((r) => words.some((w) => r.payload.target.includes(w))) });
    });
    const qsSent = () => seen.flatMap((u) => { const q = u.searchParams.get("q"); return q === null ? [] : [q]; });
    const rowOf = (n: number) => page.getByRole("row").filter({ hasText: hex(n) });
    const search = page.getByRole("searchbox", { name: "작업 검색" });
    // 검색 결과 알림(숨은 라이브 리전 -- 필터 줄 search 랜드마크 안). 보이는 빈 결과 안내와 같은 글자를 말하므로 보이는 쪽은
    // 라이브 리전이 아닌 노드로 찾는다.
    const announce = page.getByRole("search", { name: "작업 검색·필터" }).getByRole("status");
    const emptyNote = (term: string) => page.getByText(`‘${term}’에 맞는 작업이 없습니다`, { exact: true })
      .and(page.locator(":not([role=status])"));

    await page.goto("/jobs");
    for (const n of [1, 2, 3]) await expect(rowOf(n)).toHaveCount(1);
    await expect(search).toHaveAttribute("placeholder", "요청자·배치 이름·경로·ID 검색");      // 관리자 안내
    expect(qsSent(), "검색 전 요청에 q 가 실렸다").toEqual([]);
    await expect(announce).toBeEmpty();                        // 늘 있다(검색 전엔 빈 줄)

    // 0) 칸 높이·안내 글 잘림: 검색 칸·요청자 칸이 select 와 같은 높이, 안내 글이 칸 안에 다 들어간다(754 = 가장 좁은 칸).
    for (const width of [375, 754, 768, 1000, 1280]) {
      await page.setViewportSize({ width, height: 800 });
      const g = await filterGeometry(page);
      expect(new Set(g.heights).size, `w=${width} 필터 칸 높이가 어긋났다 ${JSON.stringify(g.heights)}`).toBe(1);
      expect(g.placeholderNeed, `w=${width} 검색 안내 글이 잘렸다(필요 ${g.placeholderNeed} > 칸 ${g.placeholderRoom})`)
        .toBeLessThanOrEqual(g.placeholderRoom);
    }
    await page.setViewportSize({ width: 1280, height: 800 });

    // 1) 실제 키 입력(글자마다 input 이벤트) -- 멈춘 뒤 한 번, 앞뒤 공백을 뗀 q 로 묻는다.
    const asked = page.waitForRequest((r) => new URL(r.url()).searchParams.get("q") === "alpha");
    await search.pressSequentially("  alpha ");
    await asked;
    await expect(rowOf(3)).toHaveCount(1);
    await expect(rowOf(2)).toHaveCount(0);
    await expect(rowOf(1)).toHaveCount(0);
    await expect(announce).toHaveText("‘alpha’ 검색 결과 1건");
    expect(new Set(qsSent()), "글자마다(a·al·…) 묻지 않는다 -- 디바운스").toEqual(new Set(["alpha"]));
    // 검색은 화면 상태다 -- 주소는 그대로(?batch= 링크·주소 계약을 건드리지 않는다).
    await expect(page).toHaveURL(/\/jobs$/);

    // 2) 여러 낱말은 그대로 한 q 로 간다(낱말 OR 은 서버가 -- 화면이 쪼개거나 거르지 않는다).
    await search.fill("beta gamma");
    await expect(rowOf(1)).toHaveCount(1);
    await expect(rowOf(2)).toHaveCount(1);
    await expect(rowOf(3)).toHaveCount(0);
    const sent = qsSent();
    expect(sent[sent.length - 1]).toBe("beta gamma");

    // 3) L1: 검색 칸이 채워진 채로 375·1280 에서 문서가 가로로 넘치지 않는다(검색 칸은 필터 줄에서 접힌다).
    //    셀 = 머리 10(선택·요청·배치·요청자·작업·대상·우선순위·상태·생성·갱신) + 2행 × 10.
    for (const width of [375, 1280]) {
      await page.setViewportSize({ width, height: 800 });
      await expect(rowOf(1)).toHaveCount(1);
      const box = (await search.boundingBox())!;
      expect(box.x + box.width, `w=${width} 검색 칸이 화면 밖으로 나갔다`).toBeLessThanOrEqual(width);
      await assertLayoutSane(page, { sidebarFixed: width >= 768, minTableCells: 30 });
    }

    // 4) 결과가 없는 검색 → 무엇으로 찾았는지 + 「검색 지우기」. 공백 없는 긴 낱말(경로)도 375 를 뚫지 않는다.
    const long = `/e13/${"x".repeat(150)}`;
    await page.setViewportSize({ width: 375, height: 800 });
    await search.fill(long);
    await expect(emptyNote(long)).toBeVisible();
    await expect(announce).toHaveText(`‘${long}’에 맞는 작업이 없습니다`);
    await expect(page.getByText("조건에 맞는 작업이 없습니다", { exact: true })).toHaveCount(0);
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 10 });
    await page.getByRole("button", { name: "검색 지우기" }).click();
    for (const n of [1, 2, 3]) await expect(rowOf(n)).toHaveCount(1);
    await expect(search).toHaveValue("");
    await expect(search).toBeFocused();                       // 사라진 버튼 대신 검색 칸(<body> 아님)
    await expect(announce).toBeEmpty();
    // 캐시된 검색 없는 목록이 먼저 보이고 재조회가 뒤따른다 -- 그 요청에 q 가 없다.
    await expect.poll(() => seen[seen.length - 1].searchParams.has("q"), { message: "검색을 지운 뒤에도 q 가 실렸다" })
      .toBe(false);
    await expect(page).toHaveURL(/\/jobs$/);

    await page.setViewportSize({ width: 1280, height: 800 });
    await page.unrouteAll({ behavior: "ignoreErrors" });
  });

  test("실 서버(흉내 없음): 배치 이름의 일부를 소문자로 치면 그 배치의 작업만 · 낱말 OR · 요청 id 앞부분 · 결과가 없으면 「검색 지우기」로 전체 목록", async ({ page }) => {
    // 배치 완료 대기 60s 의 상한 + 여유. 실측은 수 초다.
    test.setTimeout(120_000);
    await apiLogin(page);
    // 맞을 것: 배치 이름(「Alpha」 -- 대문자, 경로·요청자에는 없는 낱말). 맞지 않을 것: 같은 시각의 단일 scan. 앞 스펙들이
    // 남긴 행(대상·배치 이름)에도 alpha 는 없다(낱말이 겹치면 이 단언이 남의 행을 센다).
    const solo = await submitScan(page, "e14-real-solo");
    const bid = await createScanBatch(page, "E14 Alpha 배치", ["e14-real-a", "e14-real-b"]);
    await batchCompleted(page, bid);
    const children = await batchChildren(page, bid);
    expect(children, "배치 자식이 항목 수만큼 있어야 한다").toHaveLength(2);

    const listQs: (string | null)[] = [];
    page.on("request", (r) => {
      const u = new URL(r.url());
      if (u.pathname === "/api/user/requests") listQs.push(u.searchParams.get("q"));
    });
    const search = page.getByRole("searchbox", { name: "작업 검색" });
    const announce = page.getByRole("search", { name: "작업 검색·필터" }).getByRole("status");
    const rowOf = (rid: string) => page.getByRole("row").filter({ hasText: rid });
    const bodyRows = page.locator("table tbody tr");
    const only = async (ids: string[], absent: string[]) => {
      await expect(bodyRows).toHaveCount(ids.length);
      for (const rid of ids) await expect(rowOf(rid), `${rid} 가 목록에 없다`).toHaveCount(1);
      for (const rid of absent) await expect(rowOf(rid), `${rid} 가 걸러지지 않았다`).toHaveCount(0);
    };

    await page.goto("/jobs");
    for (const rid of [solo, ...children]) await expect(rowOf(rid)).toHaveCount(1);

    // 1) 배치 이름 부분 일치 · 대소문자 무시: 'alpha' → 「E14 Alpha 배치」의 자식만. 실제로 q=alpha 가 실렸다.
    const asked = page.waitForRequest((r) => {
      const u = new URL(r.url());
      return u.pathname === "/api/user/requests" && u.searchParams.get("q") === "alpha";
    });
    await search.fill("alpha");
    await asked;
    await only(children, [solo]);
    await expect(announce).toHaveText(`‘alpha’ 검색 결과 ${children.length}건`);
    // 배치 칸이 맞은 이유(이름)를 보인다.
    await expect(rowOf(children[0]).getByRole("link", { name: "E14 Alpha 배치" })).toBeVisible();

    // 2) 낱말 OR(이 라인의 의미 -- 일부 낱말만 맞아도 보인다): 배치 이름 낱말 + 단일 작업의 대상 경로 조각.
    await search.fill("alpha e14-real-solo");
    await only([solo, ...children], []);

    // 3) 요청 id 앞부분(16진 8자) → 그 작업만.
    await search.fill(solo.slice(0, 8));
    await only([solo], children);

    // 4) 아무것도 맞지 않으면 실 서버의 빈 결과 → 안내 + 「검색 지우기」 → 전체 목록(q 없는 재조회).
    const none = "e14-없는-낱말";
    await search.fill(none);
    await expect(page.getByText(`‘${none}’에 맞는 작업이 없습니다`, { exact: true })
      .and(page.locator(":not([role=status])"))).toBeVisible();
    await expect(announce).toHaveText(`‘${none}’에 맞는 작업이 없습니다`);
    await expect(bodyRows).toHaveCount(0);
    await page.getByRole("button", { name: "검색 지우기" }).click();
    for (const rid of [solo, ...children]) await expect(rowOf(rid)).toHaveCount(1);
    await expect(search).toHaveValue("");
    await expect(announce).toBeEmpty();
    await expect.poll(() => listQs[listQs.length - 1], { message: "검색을 지운 뒤에도 q 가 실렸다" }).toBeNull();
    // 확정된 검색어만 묻는다(디바운스 -- fill 은 한 번의 입력이지만 중간 글자가 새면 여기 낀다). 같은 q 의 폴링(10초)은 하나로.
    expect([...new Set(listQs.filter((q) => q !== null))], "확정된 검색어만 묻는다")
      .toEqual(["alpha", "alpha e14-real-solo", solo.slice(0, 8), none]);
  });
});
