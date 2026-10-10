import { expect, test, type Page } from "@playwright/test";
import { SHARED_TOKEN, STORAGE_NAME } from "./harness/env";
import { apiLogin } from "./helpers/session";
import { assertLayoutSane } from "./helpers/layout";

// E7 — 작업(요청) 선택 삭제(2026-10-08, 관리자 전용). 단위 테스트가 못 보는 것을 여기서 본다: **실 서버의 한
// 트랜잭션 삭제 + 컨트롤러 정리 루프의 수렴**이 화면까지 이어지는가. 목록에서 체크 → 확인 창(성공 scan 의 사용량
// 경고·되돌릴 수 없음 확인) → POST /api/admin/requests:delete(세션 관리자) → 행이 사라지고 「1개 삭제됨」 →
// 상세는 「없는 요청」 → GET /api/admin/request-purges 의 대기가 0 으로(k8s → files → purging → 완료, stub 러너).
//
// 배치 단위 삭제(2026-10-10): 실 오케스트레이터가 만든 배치 자식(재스캔 이력 포함)을 목록의 배치 열에서 한 행 체크로
// 묶음째 고르고, 서버가 센 수로 확인한 뒤 한 트랜잭션으로 배치·자식이 함께 사라지는가 · 배치 기록만 지운 묶음(「기록
// 없음」)도 같은 길로 지워지는가 · 배치 상세·「기록 없음」 칸에서 그 배치만 거른 목록(?batch=)으로 가는가(2026-10-11) ·
// 배치 행이 있는 표가 1280 에서 넘치지 않는가(배치 열 폭 예산).
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

// --- 배치 픽스처(배치 단위 삭제, 2026-10-10) ---
// scan 배치는 만들면 곧 Running 이고 오케스트레이터(하네스 1초 틱)가 항목마다 자식 요청을 만든다. 배치 생성·재스캔은
// 특권 3중 게이트(admin 세션 + allowlist)라 apiLogin 의 admin 세션으로 부른다. target 은 시나리오마다 다르다.
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

async function batchStatus(page: Page, bid: string): Promise<string> {
  const response = await page.request.get(`/api/admin/batches/${bid}`);
  if (response.status() !== 200) return `HTTP ${response.status()}`;
  return ((await response.json()) as { status: string }).status;
}

async function batchCompleted(page: Page, bid: string): Promise<void> {
  await expect.poll(() => batchStatus(page, bid), {
    timeout: 60_000, intervals: [500],
    message: `배치 ${bid} 가 60s 안에 Completed 가 되지 않았다 -- 오케스트레이터·플래너·스테퍼(controller.log)를 보라`,
  }).toBe("Completed");
}

/** 목록 API 로 본 그 배치의 자식 요청 id(재실행 이력 포함 -- requests.batch_id). */
async function batchChildren(page: Page, bid: string): Promise<string[]> {
  const response = await page.request.get("/api/user/requests?limit=200");
  expect(response.status(), await response.text()).toBe(200);
  return ((await response.json()) as { request_id: string; batch_id?: string | null }[])
    .filter((r) => r.batch_id === bid).map((r) => r.request_id);
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
    // 셀 하한 = th 10(선택 1 + 배치 1 + 8) + 행마다 td 10 -- 지금 행이 여럿이라 18 이상은 늘 성립한다.
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
    const target = page.getByRole("row").filter({ hasText: rid }).locator("td").nth(5);   // 선택·요청·배치·요청자·작업·대상
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

  test("배치 칸 기하: 배치가 끝나거나 다시 돌아도 행 높이·아래 행 위치가 그대로 · 배치 열 폭은 배치 행 유무와 무관 · 규칙 줄", async ({ page }) => {
    // 2026-10-11 검증 지적 셋 -- 실 브라우저에서만 보이는 기하라 여기서 잰다(목록 응답만 흉내 낸다, 인증·나머지는 진짜):
    //  - 막힌 배치 칸에 사유 줄을 **덧붙이자** 긴 이름 배치가 세 줄(59px) ↔ 두 줄(43px)을 오가, Running → Completed(관리자가
    //    지우려고 기다리는 순간) 폴링 한 번에 그 배치의 모든 행이 줄어 표가 포인터 밑에서 최대 수백 px 움직였다. 이제 사유
    //    줄이 뒤 조각·id 줄을 **대신**한다(칸은 늘 두 줄 이하).
    //  - 배치 머리칸 예약 폭(6.5rem = 104px)이 링크 칸(UA 왼쪽 여백 1px + 96 + pr-2 8 = 105px)보다 1px 짧아 첫 배치 행이
    //    들어오는 순간 열이 넓어지고 경계 길이의 대상이 한 줄 늘었다(예전 단언 「≤ 1px」은 바로 그 1px 을 통과시켰다).
    //  - 늘 있는 규칙 줄이 375 에서 세 줄로 접혀 툴바가 HEAD 보다 32px 높았다.
    test.setTimeout(90_000);
    await apiLogin(page);
    const hex = (n: number) => `${n.toString(16).padStart(4, "0")}${"ab".repeat(14)}`;
    const LONG = "2026-10 프로젝트 A 아카이브 이관(1차)";
    const BA = "a".repeat(32), BG = "e".repeat(32);
    let phase: "none" | "blocked" | "open" = "none";
    const single = (n: number, over: Record<string, unknown> = {}) => ({
      request_id: hex(n), operation: "scan", state: "Succeeded", priority: "mid", requester_id: "e7", actor: "e7",
      resource_key: `k${n}`, commit_order: n, created_at: "2026-10-09T03:04:05Z", updated_at: "2026-10-09T03:05:06Z",
      payload: { storage: STORAGE_NAME, target: `/e7-geom/p${n}` }, batch_id: null, ...over,
    });
    const kid = (n: number, bid: string, over: Record<string, unknown>) => single(n, {
      batch_id: bid, batch_exists: true, batch_name: LONG, batch_status: "Completed", batch_request_count: 10,
      batch_request_count_capped: false, batch_live_request_count: 0, batch_succeeded_scan_count: 0, batch_item_count: 10,
      ...over,
    });
    const rows = () => [
      single(100),
      ...(phase === "none" ? [] : [
        // 긴 이름 배치(진행 중 ↔ 완료) 6행 + 기록 없는 묶음(미완료 작업 있음 ↔ 고를 수 있음) 2행.
        ...Array.from({ length: 6 }, (_, i) => kid(90 - i, BA, phase === "blocked" ? { batch_status: "Running" } : {})),
        ...Array.from({ length: 2 }, (_, i) => kid(80 - i, BG, {
          batch_exists: false, batch_name: null, batch_status: null, batch_item_count: null,
          batch_live_request_count: phase === "blocked" ? 1 : 0,
        })),
      ]),
      ...Array.from({ length: 4 }, (_, i) => single(50 - i)),
    ];
    await page.route(/\/api\/user\/requests\?/, (route) => route.fulfill({ json: rows() }));
    const rowOf = (n: number) => page.getByRole("row").filter({ hasText: hex(n) });
    const batchTh = page.getByRole("columnheader", { name: "배치", exact: true });
    /** 문서 좌표의 기하(뷰포트 y + 스크롤): 긴 이름 배치 행·기록 없는 묶음 행의 높이, 6번째 배치 체크박스 y, 아래 단일 행 y. */
    const geometry = () => page.evaluate((ids) => {
      const trOf = (id: string) => [...document.querySelectorAll("main tbody tr")]
        .find((tr) => tr.textContent!.includes(id)) as HTMLTableRowElement;
      const top = (el: Element) => Math.round(el.getBoundingClientRect().top + window.scrollY);
      return {
        batchRow: Math.round(trOf(ids.batch).getBoundingClientRect().height),
        goneRow: Math.round(trOf(ids.gone).getBoundingClientRect().height),
        sixthBox: top(trOf(ids.sixth).querySelector("input")!),
        below: top(trOf(ids.below)),
      };
    }, { batch: hex(88), gone: hex(80), sixth: hex(85), below: hex(50) });

    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 900 });
      phase = "none";
      await page.goto("/jobs");
      await expect(rowOf(100).getByRole("checkbox")).toBeEnabled();
      const thNone = await batchTh.evaluate((el) => el.getBoundingClientRect().width);
      phase = "blocked";
      await expect(rowOf(85).getByRole("checkbox")).toBeDisabled({ timeout: 10_000 });
      await expect(rowOf(85)).toContainText("배치 진행 중");
      await expect(rowOf(80)).toContainText("미완료 작업 있음");
      const thBatch = await batchTh.evaluate((el) => el.getBoundingClientRect().width);
      expect(thBatch, `w=${width} 배치 열 폭이 배치 행 유무로 바뀌었다(표 재배치)`).toBe(thNone);
      // 사유 줄은 6rem 한 줄에 다 보인다(말줄임으로 잘리지 않는다).
      for (const why of await page.locator("main tbody [id^='batch-why-']").all()) {
        const [sw, cw] = await why.evaluate((el) => [el.scrollWidth, el.clientWidth]);
        expect(sw, `w=${width} 사유 줄이 잘렸다`).toBeLessThanOrEqual(cw);
      }
      const blocked = await geometry();
      phase = "open";
      await expect(rowOf(85).getByRole("checkbox")).toBeEnabled({ timeout: 10_000 });
      await expect(rowOf(80).getByRole("checkbox")).toBeEnabled();
      await expect(rowOf(85)).toContainText("이관(1차)");
      expect(await geometry(), `w=${width} 배치가 끝나자(Running → Completed) 표가 움직였다`).toEqual(blocked);
      phase = "blocked";
      await expect(rowOf(85).getByRole("checkbox")).toBeDisabled({ timeout: 10_000 });
      expect(await geometry(), `w=${width} 배치가 다시 돌자 표가 움직였다`).toEqual(blocked);
      if (width === 1280) {
        // 1280 에서 배치 칸 두 줄(20 + 16px)은 체크박스 행(39px) 안이다 -- 단일 행과 같은 밀도.
        expect(blocked.batchRow, "긴 이름 배치 행이 체크박스 행보다 높다").toBeLessThanOrEqual(
          Math.round((await rowOf(100).boundingBox())!.height));
      }
    }

    // 규칙 줄(늘 있다 -- 줄 수가 곧 표가 밀리는 높이): 375 에서 두 줄 이하, 768 에선 한 줄(HEAD 와 같은 툴바 높이).
    const ruleLines = () => page.getByRole("toolbar", { name: "작업 일괄 처리" }).locator("p").first().evaluate((el) => {
      const range = document.createRange();
      range.selectNodeContents(el);
      return new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size;
    });
    for (const [width, max] of [[375, 2], [768, 1]] as const) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/jobs");
      await expect(page.getByRole("toolbar", { name: "작업 일괄 처리" }).locator("p").first())
        .toContainText("진행 중인 작업은 선택할 수 없습니다");
      expect(await ruleLines(), `w=${width} 규칙 줄 수`).toBeLessThanOrEqual(max);
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

  test("배치 단위 삭제: 배치 열 · 한 행 체크 = 배치 전체(재스캔 이력 포함 4개) · 확인 창 · 배치와 자식이 함께 사라진다", async ({ page, request }) => {
    // 생성·완료(수 초) + 재스캔·완료(수 초) + 정리 수렴. 상한 합은 넉넉히.
    test.setTimeout(180_000);
    await apiLogin(page);
    await purgeIdle(page);

    // 1) 항목 2개짜리 scan 배치 → Completed → 재스캔 → Completed. 자식 4개 중 2개는 이제 어떤 항목도 가리키지 않는다
    //    (재실행 이력) -- 배치 단위 삭제의 자식 집합은 requests.batch_id 라 그것까지 지운다.
    const bid = await createScanBatch(page, "e7 배치", ["e7-batch-a", "e7-batch-b"]);
    await batchCompleted(page, bid);
    const rescan = await page.request.post(`/api/admin/batches/${bid}:rescan`);
    expect(rescan.status(), await rescan.text()).toBe(200);
    await batchCompleted(page, bid);
    const children = await batchChildren(page, bid);
    expect(children, "재스캔 뒤 자식은 4개(항목 2 × 2회)").toHaveLength(4);

    // 2) 배치 상세의 「전체 작업에서 이 배치의 작업 보기」 → 그 배치만 거른 목록(2026-10-11 검증 지적: 오래된 배치의 작업은
    //    무한 스크롤 아래라 찾을 길이 없었다). 거른 목록에도 재실행 이력까지 4개가 다 있다.
    await page.goto(`/admin/batches/${bid}`);
    await page.getByRole("link", { name: "전체 작업에서 이 배치의 작업 보기" }).click();
    await expect(page).toHaveURL(new RegExp(`/jobs\\?batch=${bid}$`));
    await expect(page.getByText("배치 e7 배치의 작업만")).toBeVisible();
    await expect(page.getByRole("row").filter({ has: page.getByRole("link", { name: "e7 배치", exact: true }) }))
      .toHaveCount(4);
    await page.getByRole("button", { name: "배치 필터 해제" }).click();
    await expect(page).toHaveURL(/\/jobs$/);
    // 목록: 배치 열에 「e7 배치」 링크(상세로), 한 행을 체크하면 같은 배치의 행이 모두 체크된다.
    await expect(page.getByRole("heading", { name: "전체 작업", level: 1 })).toBeVisible();
    const toolbar = page.getByRole("toolbar", { name: "작업 일괄 처리" });
    const status = toolbar.getByRole("status");
    const batchLink = page.getByRole("link", { name: "e7 배치", exact: true });
    const batchRows = page.getByRole("row").filter({ has: batchLink });
    await expect(batchRows).toHaveCount(4);
    await expect(batchLink.first()).toHaveAttribute("href", `/admin/batches/${bid}`);
    await expect(batchRows.first().getByRole("checkbox")).toBeEnabled();

    // 배치 행이 있는 표의 기하(L1~L4) -- 1280 에서는 표 래퍼 자체도 넘치지 않아야 한다(L1 은 래퍼 안 가로 스크롤을
    // 허용하므로 열 폭 회귀를 못 잡는다 -- 배치 열은 요청 열 6.5rem·시각 칸 「 KST」 제거로 폭을 마련했다).
    await assertLayoutSane(page, { minTableCells: 18 });
    const wrapper = page.locator("main .overflow-x-auto").filter({ has: page.locator("table") }).first();
    const [scrollW, clientW] = await wrapper.evaluate((el) => [el.scrollWidth, el.clientWidth]);
    expect(scrollW, `1280 에서 작업 표가 래퍼를 넘친다(${scrollW} > ${clientW}) -- 배치 열 폭 예산 회귀`)
      .toBeLessThanOrEqual(clientW);
    // 배치 열 폭은 머리칸이 예약한다(2026-10-11 검증 지적: 배치 행이 없으면 「—」 폭으로 줄었다가 폴링으로 첫 배치 행이
    // 들어오는 순간 표 전체가 다시 배치돼 단일 작업 행이 포인터 밑에서 ~240px 내려갔다). 6.5rem + 1px(105px -- UA 왼쪽 여백
    // 1px 까지, 1px 을 빼먹은 104px 은 첫 배치 행이 열을 넓혔다 -- 2026-10-11 검증 지적)이고 넓은 화면에서도
    // 넓어지지 않는다(1440px 이상의 12rem 은 짧은 이름에도 대상 열을 HEAD 보다 120px 깎았다). 대상 열의 절대 폭은 이 DB 의
    // 내용(짧은 대상·긴 상태값)에 따라 auto 표 배치가 정하므로 여기서 재지 않는다 -- HEAD 대비 밀도는 검증 스크립트가 같은
    // 내용으로 쟀다(CHANGELOG 검증 3차).
    const thWidth = (name: string) => page.getByRole("columnheader", { name, exact: true })
      .evaluate((el) => el.getBoundingClientRect().width);
    const batchCol = await thWidth("배치");
    expect(batchCol, "배치 열은 6.5rem + 1px(105px) 예약").toBeGreaterThanOrEqual(104.5);
    expect(batchCol).toBeLessThanOrEqual(105.5);
    await page.setViewportSize({ width: 1920, height: 900 });
    expect(await thWidth("배치"), "넓은 화면에서 배치 열이 넓어졌다").toBe(batchCol);
    await page.setViewportSize({ width: 1280, height: 800 });
    // 이 배치의 행이 없는 화면(연산 rm 으로 거른 목록 -- 이 배치는 scan)에서도 배치 열 폭이 같다.
    await page.getByLabel("연산 필터").selectOption("rm");
    await expect(batchRows).toHaveCount(0);
    expect(await thWidth("배치"), "배치 행 유무로 배치 열 폭이 바뀌었다(표 재배치)").toBe(batchCol);
    await page.getByLabel("연산 필터").selectOption("");
    await expect(batchRows).toHaveCount(4);
    await page.setViewportSize({ width: 375, height: 667 });
    await expect(batchRows).toHaveCount(4);
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 18 });
    await page.setViewportSize({ width: 1280, height: 800 });

    // 공유 Bearer 토큰으로는 배치 단위로도 지울 수 없다(세션 관리자 전용) -- 아무 것도 지워지지 않는다.
    const denied = await request.post("/api/admin/requests:delete", {
      headers: { authorization: `Bearer ${SHARED_TOKEN}` },
      data: { batches: [{ batch_id: bid, expected_request_count: 4 }] },
    });
    expect(denied.status(), await denied.text()).toBe(403);
    expect(((await denied.json()) as { detail: string }).detail).toBe("admin_session_required");
    expect(await batchStatus(page, bid)).toBe("Completed");

    await batchRows.nth(1).getByRole("checkbox").check();
    for (let i = 0; i < 4; i += 1) await expect(batchRows.nth(i).getByRole("checkbox")).toBeChecked();
    await expect(status).toHaveText("배치 1개 선택됨(작업 4개 · 성공 scan 4개)");

    // 3) 확인 창: 서버가 센 수 · 배치 목록 · 배치 안내 두 줄 · 사용량 경고.
    await toolbar.getByRole("button", { name: "선택 삭제" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("선택한 배치 1개의 작업 4개를 영구 삭제합니다. 되돌릴 수 없습니다.");
    const list = dialog.getByRole("list", { name: "삭제할 배치" });
    await expect(list).toContainText("e7 배치");
    await expect(list).toContainText("작업 4개");
    await expect(dialog).toContainText("배치의 작업은 목록에 보이는 행·필터와 관계없이 전부(재실행 이력 포함) 지워집니다.");
    await expect(dialog).toContainText("배치 기록(항목 목록·이름·메모·실행 설정)도 함께 지워져");
    await expect(dialog.getByRole("note")).toContainText("성공한 scan 4개 포함 — 사용량 분석");
    await dialog.getByLabel("되돌릴 수 없음을 확인했습니다").check();
    await dialog.getByRole("button", { name: "4개 영구 삭제" }).click();

    // 4) 결과 · 행이 사라짐 · 배치 상세 404 · 자식 상세 404 · 정리 수렴.
    await expect(dialog).toBeHidden();
    await expect(status).toHaveText("배치 1개(작업 4개) 삭제됨");
    await expect(batchRows).toHaveCount(0);
    const gone = await page.request.get(`/api/admin/batches/${bid}`);
    expect(gone.status()).toBe(404);
    expect(((await gone.json()) as { detail: string }).detail).toBe("batch_not_found");
    for (const rid of children) expect((await page.request.get(`/api/user/requests/${rid}`)).status(), rid).toBe(404);
    expect(await batchChildren(page, bid)).toEqual([]);
    await purgeIdle(page);
  });

  test("배치 기록만 지운 묶음(배치 화면 「배치 삭제」 뒤)은 「기록 없음 xxxxxxxxxxxx」로 보이고 그 묶음만 거른 목록에서 배치 단위로 지운다", async ({ page }) => {
    test.setTimeout(120_000);
    await apiLogin(page);
    await purgeIdle(page);
    const bid = await createScanBatch(page, "e7 기록만", ["e7-batch-dangling"]);
    await batchCompleted(page, bid);
    const children = await batchChildren(page, bid);
    expect(children).toHaveLength(1);
    // 배치 화면의 「배치 삭제」 = 배치 기록만(자식 작업은 남는다).
    const recordOnly = await page.request.delete(`/api/admin/batches/${bid}`);
    expect(recordOnly.status(), await recordOnly.text()).toBe(200);
    expect(await requestState(page, children[0])).toBe("Succeeded");

    await page.goto("/jobs");
    const toolbar = page.getByRole("toolbar", { name: "작업 일괄 처리" });
    const status = toolbar.getByRole("status");
    const row = page.getByRole("row").filter({ hasText: children[0] });
    await expect(row).toHaveCount(1);
    // 지워진 배치의 상세는 404 라 그리로 보내지 않는다 -- 「기록 없음 + id 12자」는 그 묶음의 작업만 거른 이 목록으로 가는
    // 링크다(2026-10-11 검증 지적: 「삭제됨」은 작업 자체가 지워졌다는 말로 읽혔고, 묶음의 작업을 한자리에서 볼 길이
    // 없었다). id 는 확인 창·결과 줄과 같은 12자이고 6rem 칸에 **다 보인다**(가로로 잘리지 않는다).
    const gone = row.getByRole("link", { name: `기록 없음 ${bid.slice(0, 12)}` });
    await expect(gone).toBeVisible();
    await expect(gone).toHaveAttribute("href", `/jobs?batch=${bid}`);
    const [scrollW, clientW] = await gone.evaluate((el) => [el.scrollWidth, el.clientWidth]);
    expect(scrollW, "「기록 없음 + id 12자」가 가로로 잘리지 않는다").toBeLessThanOrEqual(clientW);
    await gone.click();
    await expect(page).toHaveURL(new RegExp(`/jobs\\?batch=${bid}$`));
    await expect(page.getByText(`배치 ${bid.slice(0, 12)}의 작업만`)).toBeVisible();
    await expect(page.getByRole("row").filter({ has: page.getByRole("link", { name: /^기록 없음 / }) })).toHaveCount(1);
    await row.getByRole("checkbox").check();
    await expect(status).toHaveText("배치 1개 선택됨(작업 1개 · 성공 scan 1개)");
    await toolbar.getByRole("button", { name: "선택 삭제" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("list", { name: "삭제할 배치" })).toContainText("배치 기록 없음");
    await dialog.getByLabel("되돌릴 수 없음을 확인했습니다").check();
    await dialog.getByRole("button", { name: "1개 영구 삭제" }).click();
    await expect(dialog).toBeHidden();
    await expect(status).toHaveText("배치 1개(작업 1개) 삭제됨");
    await expect(row).toHaveCount(0);
    expect((await page.request.get(`/api/user/requests/${children[0]}`)).status()).toBe(404);
    await purgeIdle(page);
  });
});
