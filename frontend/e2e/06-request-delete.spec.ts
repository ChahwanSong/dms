import { expect, test, type Page } from "@playwright/test";
import { SHARED_TOKEN, STORAGE_NAME } from "./harness/env";
import { apiLogin } from "./helpers/session";
import { assertLayoutSane } from "./helpers/layout";

// E7 — 작업(요청) 선택 삭제(2026-10-08, 관리자 전용). 단위 테스트가 못 보는 것을 여기서 본다: **실 서버의 한
// 트랜잭션 삭제 + 컨트롤러 정리 루프의 수렴**이 화면까지 이어지는가. 목록에서 체크 → 확인 창(성공 scan 의 사용량
// 경고·되돌릴 수 없음 확인) → POST /api/admin/requests:delete(세션 관리자) → 행이 사라지고 「1개 삭제됨」 →
// 상세는 「없는 요청」 → GET /api/admin/request-purges 의 대기가 0 으로(k8s → files → purging → 완료, stub 러너).
//
// 하네스 전제(global-setup): api 조용한 창 0초(방금 끝난 요청도 바로 지운다), controller request-purge 1초 틱,
// api·controller 공통 artifact base = tmp 아래 실디렉터리(정리 루프가 실제로 연다).

// run_as_root: LDAP 없는 하네스에서 잡이 끝까지 가려면 root 여야 한다(E5/E6 관례 -- 서버는 명시 true 만 root).
// target 은 시나리오마다 다르다(같은 resource_key 의 활성 요청이 있으면 Conflict).
// rm 은 options.recursive=true 가 필수다(domain rm_recursive_required).
async function submit(page: Page, operation: "scan" | "rm", target: string): Promise<string> {
  const options = operation === "rm" ? { recursive: true } : {};
  const response = await page.request.post("/api/user/requests", {
    data: { operation, storage: STORAGE_NAME, target, options, priority: "mid", run_as_root: true },
  });
  expect(response.status(), await response.text()).toBe(202);
  const rid = ((await response.json()) as { request_id?: string }).request_id;
  expect(typeof rid, "202 응답에 request_id 가 없다").toBe("string");
  return rid!;
}

async function requestState(page: Page, rid: string): Promise<string> {
  const response = await page.request.get(`/api/user/requests/${rid}`);
  if (response.status() !== 200) return `HTTP ${response.status()}`;
  return ((await response.json()) as { state: string }).state;
}

// 정리 대기(앞 시나리오의 삭제)가 0 이 될 때까지 -- 툴바의 「결과 파일·파드 정리 중 N건」이 재는 도중 나타났다
// 사라지면 기하 비교가 흔들린다.
async function purgeIdle(page: Page): Promise<void> {
  await expect.poll(async () => {
    const response = await page.request.get("/api/admin/request-purges");
    return ((await response.json()) as { pending: number }).pending;
  }, { timeout: 30_000, intervals: [500], message: "정리 대기가 30s 안에 0 이 되지 않았다" }).toBe(0);
}

test.describe("E7 작업 삭제", () => {
  test("끝난 scan 을 목록에서 골라 지우면 행·상세가 사라지고 정리 대기가 0 으로 수렴한다", async ({ page }) => {
    // 종단 대기 30s + rm 미리보기 대기 30s + 정리 수렴 30s 의 상한 합. 실측은 수 초다.
    test.setTimeout(120_000);
    await apiLogin(page);

    // 1) 지울 대상: 성공 scan(사용량 분석 지점이 되는 종류 -- 확인 창 경고 대상).
    const rid = await submit(page, "scan", "e7-scan");
    await expect.poll(() => requestState(page, rid), {
      timeout: 30_000, intervals: [500],
      message: `요청 ${rid} 가 30s 안에 Succeeded 가 되지 않았다 -- 삭제 이전(제출·플래너·스테퍼)의 문제다`,
    }).toBe("Succeeded");

    // 비종단 픽스처: rm 은 미리보기 뒤 ConfirmPending(컨펌 대기)에서 멈춘다 -- stub 러너에서도 사람이 컨펌하기 전엔
    // 끝나지 않는 안정된 비종단이다(미리보기 TTL 24h).
    const liveRid = await submit(page, "rm", "e7-rm-live");
    await expect.poll(async () => {
      const response = await page.request.get(`/api/user/requests/${liveRid}/jobs`);
      if (response.status() !== 200) return `HTTP ${response.status()}`;
      return ((await response.json()) as { state: string }[]).map((j) => j.state).join(",");
    }, { timeout: 30_000, intervals: [500],
         message: `rm 요청 ${liveRid} 가 ConfirmPending 에 닿지 않았다` }).toBe("ConfirmPending");

    // 2) 목록에서 행 체크 → 「선택 삭제」 → 확인 창.
    await page.goto("/jobs");
    await expect(page.getByRole("heading", { name: "전체 작업", level: 1 })).toBeVisible();
    const toolbar = page.getByRole("toolbar", { name: "작업 일괄 처리" });
    const status = toolbar.getByRole("status");
    const row = page.getByRole("row").filter({ hasText: rid });
    await expect(row, `요청 ${rid} 행이 목록에 정확히 1개여야 한다`).toHaveCount(1);
    await row.getByRole("checkbox").check();
    await expect(status).toHaveText("1개 선택됨(성공 scan 1개)");

    // 비종단 행의 체크박스는 잠겨 있다(표시 게이트 -- 진짜 차단은 서버).
    const liveRow = page.getByRole("row").filter({ hasText: liveRid });
    await expect(liveRow).toHaveCount(1);
    await expect(liveRow.getByRole("checkbox")).toBeDisabled();

    // 체크 열이 생긴 표에서도 기하 불변식(L1~L4): td 안 체크박스는 래퍼 없이 단독이라 table-cell 이 유지된다.
    // 셀 하한 = th 9(선택 1 + 8) + 행마다 td 9 -- 지금 행이 여럿이라 18 이상은 늘 성립한다.
    await assertLayoutSane(page, { minTableCells: 18 });

    await toolbar.getByRole("button", { name: "선택 삭제" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("선택한 작업 1개를 영구 삭제합니다. 되돌릴 수 없습니다.");
    await expect(dialog.getByRole("note")).toContainText("성공한 scan 1개 포함 — 사용량 분석");
    await expect(dialog).toContainText("스토리지의 실제 데이터(복사·삭제된 파일)는 건드리지 않습니다");
    const go = dialog.getByRole("button", { name: "1개 영구 삭제" });
    await expect(go).toBeDisabled();
    await dialog.getByLabel("되돌릴 수 없음을 확인했습니다").check();
    await go.click();

    // 3) 창이 닫히고, 행이 사라지고, 결과가 툴바에(결과 문구 시점엔 목록 재조회가 이미 끝났다).
    await expect(dialog).toBeHidden();
    await expect(status).toHaveText("1개 삭제됨");
    // 창이 닫히면 포커스는 결과 줄로 -- 「선택 삭제」는 선택이 비어 잠겨 Radix 기본 복귀가 <body> 로 떨어진다(실 브라우저).
    await expect(status).toBeFocused();
    await expect(row).toHaveCount(0);
    await expect(liveRow).toHaveCount(1);              // 이웃(비종단)은 그대로

    // 4) 상세는 「없는 요청」 화면(재시도 없음). API 도 404 다.
    await page.goto(`/jobs/${rid}`);
    await expect(page.getByText("요청을 찾을 수 없습니다 — 삭제됐거나 볼 수 없는 요청입니다")).toBeVisible();
    await expect(page.getByRole("button", { name: "다시 시도" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "전체 작업으로" })).toBeVisible();
    for (const path of [`/api/user/requests/${rid}`, `/api/user/requests/${rid}/jobs`]) {
      expect((await page.request.get(path)).status(), path).toBe(404);
    }

    // 5) 결과 파일·파드 정리(컨트롤러 request-purge)가 대기 0 으로 수렴한다. 지연(stalled)이 남으면 사유를 보인다.
    await expect.poll(async () => {
      const response = await page.request.get("/api/admin/request-purges");
      expect(response.status(), await response.text()).toBe(200);
      const body = (await response.json()) as { pending: number; items: { last_error: string | null }[] };
      return body.pending === 0 ? 0 : `pending ${body.pending} ${JSON.stringify(body.items)}`;
    }, { timeout: 30_000, intervals: [500],
         message: "정리 대기가 30s 안에 0 이 되지 않았다 -- controller.log 의 request-purge 를 보라" }).toBe(0);

    // 6) 뒷정리: 비종단 픽스처를 취소해 이후 스펙이 활성 요청을 물려받지 않게 한다.
    const cancel = await page.request.post(`/api/user/requests/${liveRid}:cancel`);
    expect(cancel.status(), await cancel.text()).toBe(200);
  });

  test("부분 성공: 서버가 일부를 제외하면 결과 요약이 남고 제외된 행은 선택에서 빠진다(목록 재조회가 늦어도)", async ({ page }) => {
    // 2026-10-09 검증 지적(실 브라우저에서만 재현 -- jsdom 은 렌더 lane 순서가 달라 통과한다): 삭제 결과 처리의
    // setSelected([]) 보다 목록 재조회 결과가 먼저 렌더되면 유령 선택 정리가 [] 를 옛 선택으로 덮어, 상태 줄이 「2개
    // 선택됨 · 1개는 목록에서 사라져 선택에서 뺐습니다」가 되고 서버가 제외한 행이 체크된 채 남았다(재조회 지연
    // 5~300ms 에서 6/6 재현). 하네스 조용한 창이 0초라 서버가 실제로 제외할 일이 없어, 삭제 POST 는 실서버에 첫 id
    // 만 보내고 나머지를 「제외」로 덧붙인 응답으로 돌려준다(목록·재조회·삭제 트랜잭션은 진짜다).
    test.setTimeout(120_000);
    await apiLogin(page);
    const ids: string[] = [];
    for (const t of ["e7-partial-a", "e7-partial-b", "e7-partial-c"]) ids.push(await submit(page, "scan", t));
    for (const rid of ids) {
      await expect.poll(() => requestState(page, rid), { timeout: 30_000, intervals: [500] }).toBe("Succeeded");
    }
    const [a, b, c] = ids;
    await page.route(/\/api\/user\/requests\?/, async (route) => {
      await new Promise((r) => setTimeout(r, 30));          // 목록 재조회를 늦춘다(경합 창을 연다)
      await route.continue();
    });
    await page.route("**/api/admin/requests:delete", async (route) => {
      const sent = JSON.parse(route.request().postData() ?? "{}") as { request_ids: string[] };
      expect(sent.request_ids).toEqual([a, b, c]);
      const real = await route.fetch({ postData: JSON.stringify({ request_ids: [a] }) });
      const body = (await real.json()) as { deleted: unknown[]; purge_pending: number };
      await route.fulfill({ response: real, json: {
        ...body, skipped: [{ request_id: b, reason: "request_recently_finished" },
                           { request_id: c, reason: "request_job_active" }] } });
    });

    await page.goto("/jobs");
    const toolbar = page.getByRole("toolbar", { name: "작업 일괄 처리" });
    const status = toolbar.getByRole("status");
    const rowOf = (rid: string) => page.getByRole("row").filter({ hasText: rid });
    for (const rid of ids) await rowOf(rid).getByRole("checkbox").check();
    await expect(status).toHaveText("3개 선택됨(성공 scan 3개)");
    await toolbar.getByRole("button", { name: "선택 삭제" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("되돌릴 수 없음을 확인했습니다").check();
    await dialog.getByRole("button", { name: "3개 영구 삭제" }).click();
    await expect(dialog).toBeHidden();

    await expect(status).toHaveText("1개 삭제됨 · 2개 제외");
    await expect(rowOf(a)).toHaveCount(0);
    await page.waitForTimeout(1_000);                     // 늦은 재조회 렌더까지 흘려보낸 뒤에도
    await expect(status).toHaveText("1개 삭제됨 · 2개 제외");
    for (const rid of [b, c]) await expect(rowOf(rid).getByRole("checkbox")).not.toBeChecked();
    await expect(page.getByText(new RegExp(`방금 끝난 작업입니다 — 잠시 뒤에 삭제할 수 있습니다 \\(\\d+개: .*${b.slice(0, 12)}`))).toBeVisible();
    await page.unrouteAll({ behavior: "ignoreErrors" });
  });

  test("툴바 높이는 첫 체크에도 그대로다(414·768·1280) · 375 에서 대상 열이 짓눌리지 않는다", async ({ page }) => {
    // 2026-10-09 검증 지적: 414~768px 에서 긴 미선택 안내만 두 줄로 접혀 첫 체크 순간 툴바가 104→56px 로 줄고 표가
    // 포인터 밑에서 48px 튀었다. 375px 에선 체크 열이 더해져 「대상」 열이 두 글자 폭이 됐다.
    await apiLogin(page);
    const rid = await submit(page, "scan", "e7-toolbar-height");
    await expect.poll(() => requestState(page, rid), { timeout: 30_000, intervals: [500] }).toBe("Succeeded");
    await purgeIdle(page);
    for (const width of [414, 768, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/jobs");
      const toolbar = page.getByRole("toolbar", { name: "작업 일괄 처리" });
      const box = page.getByRole("row").filter({ hasText: rid }).getByRole("checkbox");
      await expect(box).toBeEnabled();
      // 문서 좌표(뷰포트 y + 스크롤) -- check() 가 행을 화면 안으로 스크롤해도 비교가 흔들리지 않게.
      const docY = () => box.evaluate((el) => el.getBoundingClientRect().top + window.scrollY);
      const idle = (await toolbar.boundingBox())!.height;
      const rowY = await docY();
      await box.check();
      await expect(toolbar.getByRole("status")).toHaveText(/^1개 선택됨/);
      expect((await toolbar.boundingBox())!.height, `w=${width} 툴바 높이`).toBe(idle);
      expect(await docY(), `w=${width} 행이 포인터 밑에서 움직였다`).toBe(rowY);
      await box.uncheck();
      expect((await toolbar.boundingBox())!.height, `w=${width} 해제 뒤 툴바 높이`).toBe(idle);
    }
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto("/jobs");
    const target = page.getByRole("row").filter({ hasText: rid }).locator("td").nth(4);   // 선택·요청·요청자·작업·대상
    await expect(target).toContainText("e7-toolbar-height");
    expect((await target.boundingBox())!.width, "375 에서 대상 열 최소 폭(8rem)").toBeGreaterThanOrEqual(127);
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 18 });
    await page.setViewportSize({ width: 1280, height: 800 });
  });

  test("정리 대기가 생기고 사라져도 툴바 높이가 그대로다(360·375·393·1280) — 두 버튼은 한 행", async ({ page }) => {
    // 2026-10-09 검증 지적: 360~393px 에서 삭제마다 나타나는 「결과 파일·파드 정리 중 N건」이 버튼 행에 끼어 「선택
    // 해제」가 다음 줄로 밀렸다(툴바 128→174px). 백그라운드 정리가 끝나면 다음 폴링에 줄어 표가 손가락 밑에서 46px 튀었다.
    // 정리 현황만 흉내 낸다(대기 0·1·200건 -- 정리 루프의 실제 수렴은 첫 시나리오가 본다).
    await apiLogin(page);
    let pending = 0;
    await page.route("**/api/admin/request-purges", (route) => route.fulfill({ json: {
      pending, stalled: 0, oldest_requested_at: pending > 0 ? "2026-10-08T00:00:00Z" : null, items: [] } }));
    for (const width of [360, 375, 393, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      const heights: number[] = [];
      for (const p of [0, 1, 200]) {
        pending = p;
        await page.goto("/jobs");
        const toolbar = page.getByRole("toolbar", { name: "작업 일괄 처리" });
        if (p > 0) await expect(toolbar).toContainText(`결과 파일·파드 정리 중 ${p}건`);
        else await expect(toolbar.getByRole("status")).toBeVisible();
        heights.push((await toolbar.boundingBox())!.height);
        const del = (await toolbar.getByRole("button", { name: "선택 삭제" }).boundingBox())!;
        const clear = (await toolbar.getByRole("button", { name: "선택 해제" }).boundingBox())!;
        expect(clear.y, `w=${width} 대기 ${p}건: 두 버튼이 한 행`).toBe(del.y);
      }
      expect(heights, `w=${width} 툴바 높이(대기 0·1·200건)`).toEqual([heights[0], heights[0], heights[0]]);
    }
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.unrouteAll({ behavior: "ignoreErrors" });
  });

  test("공유 Bearer 토큰으로는 삭제할 수 없다(세션 관리자 전용) — 아무 것도 지워지지 않는다", async ({ page, request }) => {
    await apiLogin(page);
    const rid = await submit(page, "scan", "e7-scan-token");
    await expect.poll(() => requestState(page, rid), { timeout: 30_000, intervals: [500] }).toBe("Succeeded");
    // `request` 픽스처는 브라우저 컨텍스트와 쿠키를 공유하지 않는다 -- 세션 없이 Bearer 공유 토큰만 싣는다
    // (모든 노드 에이전트가 가진 토큰: role admin, auth=token).
    const response = await request.post("/api/admin/requests:delete", {
      headers: { authorization: `Bearer ${SHARED_TOKEN}` },
      data: { request_ids: [rid] },
    });
    expect(response.status(), await response.text()).toBe(403);
    expect(((await response.json()) as { detail: string }).detail).toBe("admin_session_required");
    expect(await requestState(page, rid)).toBe("Succeeded");
  });
});
