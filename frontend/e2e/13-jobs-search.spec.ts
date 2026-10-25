import { expect, test } from "@playwright/test";
import { STORAGE_NAME } from "./harness/env";
import { apiLogin } from "./helpers/session";
import { assertLayoutSane } from "./helpers/layout";

// E14 — 전체 작업 검색(2026-10-25 사용자 요청: 배치 이름·경로 등으로 찾기, 일부 단어만 맞아도 보이기). 단위 테스트(jsdom)가
// 못 보는 것을 여기서 본다: 실 브라우저의 키 입력·디바운스 타이머가 낳는 **실 HTTP 주소**(q, trim, 글자마다 묻지 않음),
// 검색 칸·빈 결과 안내가 좁은 폭(375)·넓은 폭(1280)에서 문서를 넓히지 않는가(L1), 「검색 지우기」가 목록을 되돌리는가.
// 서버 검색의 의미(부분 일치·대소문자·낱말 OR·비관리자 격리)는 pytest 몫이라 목록 응답만 흉내 낸다(인증·나머지는 진짜):
// 흉내 서버는 q 의 낱말 중 하나라도 대상 경로에 들어 있으면 그 행을 준다.

test.describe("E14 전체 작업 검색", () => {
  test("검색어는 멈춘 뒤 q 로 실리고 목록이 좁혀진다 · 결과가 없으면 「검색 지우기」로 되돌린다 · 375·1280 에서 넘치지 않는다", async ({ page }) => {
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

    await page.goto("/jobs");
    for (const n of [1, 2, 3]) await expect(rowOf(n)).toHaveCount(1);
    await expect(search).toHaveAttribute("placeholder", "요청자·배치 이름·경로·요청 ID 검색");      // 관리자 안내
    expect(qsSent(), "검색 전 요청에 q 가 실렸다").toEqual([]);

    // 1) 실제 키 입력(글자마다 input 이벤트) -- 멈춘 뒤 한 번, 앞뒤 공백을 뗀 q 로 묻는다.
    const asked = page.waitForRequest((r) => new URL(r.url()).searchParams.get("q") === "alpha");
    await search.pressSequentially("  alpha ");
    await asked;
    await expect(rowOf(3)).toHaveCount(1);
    await expect(rowOf(2)).toHaveCount(0);
    await expect(rowOf(1)).toHaveCount(0);
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
    await expect(page.getByText(`‘${long}’에 맞는 작업이 없습니다`)).toBeVisible();
    await expect(page.getByText("조건에 맞는 작업이 없습니다", { exact: true })).toHaveCount(0);
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 10 });
    await page.getByRole("button", { name: "검색 지우기" }).click();
    for (const n of [1, 2, 3]) await expect(rowOf(n)).toHaveCount(1);
    await expect(search).toHaveValue("");
    await expect(search).toBeFocused();                       // 사라진 버튼 대신 검색 칸(<body> 아님)
    // 캐시된 검색 없는 목록이 먼저 보이고 재조회가 뒤따른다 -- 그 요청에 q 가 없다.
    await expect.poll(() => seen[seen.length - 1].searchParams.has("q"), { message: "검색을 지운 뒤에도 q 가 실렸다" })
      .toBe(false);
    await expect(page).toHaveURL(/\/jobs$/);

    await page.setViewportSize({ width: 1280, height: 800 });
    await page.unrouteAll({ behavior: "ignoreErrors" });
  });
});
