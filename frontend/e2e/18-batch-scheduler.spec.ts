import { expect, test, type Page } from "@playwright/test";
import { STORAGE_NAME } from "./harness/env";
import { apiLogin } from "./helpers/session";

// E18 — 배치 스케줄러(2026-11-16 사용자 요청 「작업 메뉴에 배치 스케줄러 — 같은 배치를 주기마다 다시 실행」). 단위 테스트가
// 못 보는 것을 여기서 본다: 실 서버의 특권 게이트·KST 다음 실행 계산(:preview)·파괴적 스케줄 동의(422 ↔ 체크)·끄기·삭제가
// 화면과 같은 값으로 이어지는가. 실제 회차 발사는 하네스 시계를 앞당길 수 없어 백엔드 고정 시각 테스트
// (tests/test_repo_batch_schedules.py)와 라이브 실증이 맡는다 — 여기서 만드는 스케줄은 테스트 동안 회차가 돌면 안 된다(돌면
// 「마지막 결과: 아직 없음」·기록 창 단언이 깨지고, 끝난 배치는 실제로 전체 재실행된다). 그래서 모든 스케줄의 시각을 farSlot()
// (지금 KST + 12시간의 「시」)으로 잡는다: 매일·매주·매월은 하루에 그 시각 한 번뿐이라 다음 슬롯이 최소 11시간 뒤다. N시간마다
// (interval)는 하루에 슬롯이 여럿이라 이 규칙으로 피할 수 없어 쓰지 않는다(그 요약 문구는 vitest 가 본다).
//
// 하네스 전제(global-setup): admin 은 특권 목록 안(배치 생성·스케줄 무장의 3중 게이트), 세션 로그인.

interface ScheduleRow {
  schedule_id: string; batch_id: string; enabled: boolean; next_run_at: string | null;
  acknowledged_irreversible?: boolean; destructive?: boolean | null; [k: string]: unknown;
}

async function createBatch(page: Page, data: Record<string, unknown>): Promise<string> {
  const response = await page.request.post("/api/admin/batches", { data: { max_concurrency: 1, ...data } });
  expect(response.status(), await response.text()).toBe(202);
  return ((await response.json()) as { batch_id: string }).batch_id;
}

async function scheduleOf(page: Page, bid: string): Promise<ScheduleRow | undefined> {
  const response = await page.request.get("/api/admin/batch-schedules");
  expect(response.status(), await response.text()).toBe(200);
  return ((await response.json()) as ScheduleRow[]).find((s) => s.batch_id === bid);
}

// UTC …Z → KST 날짜(YYYY-MM-DD) — 목록의 「다음 실행」 첫 줄(kstStampBare 의 날짜 부분)과 대조한다.
const kstDate = (iso: string) => new Date(Date.parse(iso) + 9 * 3600_000).toISOString().slice(0, 10);

// 테스트 동안 오지 않는 슬롯의 「시」 — 지금 KST 의 시 + 12(03-layout 배치 스케줄러 시나리오와 같은 규칙). 분이 :00 이면 다음
// 슬롯까지 11~12시간, :30 이면 11.5~12.5시간이다(서버 next_run_after 는 지금보다 엄격히 뒤의 첫 슬롯을 고른다). hh = 두 자리.
function farSlot(): { hour: number; hh: string } {
  const hour = (new Date(Date.now() + 9 * 3600_000).getUTCHours() + 12) % 24;
  return { hour, hh: String(hour).padStart(2, "0") };
}

async function pickBatch(page: Page, bid: string) {
  const dlg = page.getByRole("dialog", { name: "스케줄 추가" });
  const sel = dlg.getByRole("combobox", { name: "배치" });
  await expect(sel.locator(`option[value="${bid}"]`)).toBeAttached();
  await sel.selectOption(bid);
  return dlg;
}

test.describe("E18 — 배치 스케줄러", () => {
  test.beforeEach(async ({ page }) => {
    await apiLogin(page);
  });

  test("scan 배치에 매주 스케줄을 UI 로 만들면 목록에 서버가 계산한 다음 실행이 보이고, 끄기·삭제가 된다", async ({ page }) => {
    const stamp = Date.now().toString(36);
    const name = `sched-scan-${stamp}`;
    const bid = await createBatch(page, { operation: "scan", options: {}, name,
                                          items: [{ storage: STORAGE_NAME, target: `sched-${stamp}` }] });
    await page.goto("/admin/batch-schedules");
    await expect(page.getByRole("heading", { name: "배치 스케줄러", level: 1 })).toBeVisible();
    await expect(page.getByRole("link", { name: "배치 스케줄러" })).toBeVisible();          // 작업 메뉴(사이드바)
    await page.getByRole("button", { name: "스케줄 추가" }).click();
    const dlg = await pickBatch(page, bid);
    const { hh } = farSlot();
    const rule = `매주 월·수 ${hh}:30 (KST)`;
    await dlg.getByRole("button", { name: "매주" }).click();
    await dlg.getByRole("checkbox", { name: "수" }).check();
    await dlg.getByLabel("실행 시각(KST)").fill(`${hh}:30`);
    await expect(dlg.getByText(rule)).toBeVisible();
    // 다음 5회는 서버 계산(:preview) — 화면은 시각을 계산하지 않는다
    await expect(dlg.getByLabel("다음 실행 미리보기").getByRole("listitem")).toHaveCount(5);
    await expect(dlg.getByLabel("다음 실행 미리보기").getByRole("listitem").first()).toContainText(`${hh}:30:00 KST`);
    // 회차 보존 안내(2026-11-16 — 서버 :preview 의 keep_rounds, 기본 10(같은 날 5 → 10)). 정리 자체는 컨트롤러 루프라 e2e 밖이다.
    await expect(dlg.getByRole("note", { name: "회차 보존 안내" })).toContainText("최근 10회차만 보존합니다");
    await dlg.getByRole("button", { name: "추가" }).click();
    await expect(page.getByRole("dialog", { name: "스케줄 추가" })).toHaveCount(0);
    await expect(page.getByText(/스케줄이 있는 배치는 최근 10회차만 보존합니다/).first()).toBeVisible();

    const created = await scheduleOf(page, bid);
    expect(created, "만든 스케줄이 목록 API 에 없다").toBeDefined();
    expect(created!.enabled).toBe(true);
    expect(created!.next_run_at).not.toBeNull();
    expect(Object.keys(created!)).not.toContain("ack_fingerprint");                      // 지문 원문은 싣지 않는다
    const tr = page.getByRole("row").filter({ has: page.getByRole("link", { name }) });
    await expect(tr.getByText(rule)).toBeVisible();
    await expect(tr.getByText(kstDate(created!.next_run_at!))).toBeVisible();
    await expect(tr.getByText("아직 없음")).toBeVisible();
    await expect(tr.getByText("아직 정리 없음")).toBeVisible();                           // 정리 열(null ≠ 0)

    // 끄기 — 다음 실행이 비고 서버도 꺼짐
    await tr.getByRole("checkbox", { name: `${name} 사용` }).uncheck();
    await expect.poll(async () => (await scheduleOf(page, bid))?.enabled).toBe(false);
    await expect(tr.getByRole("checkbox", { name: `${name} 사용` })).not.toBeChecked();
    expect((await scheduleOf(page, bid))!.next_run_at).toBeNull();

    // 기록 창(아직 판정한 회차 없음)
    await tr.getByRole("button", { name: `${name} 기록` }).click();
    await expect(page.getByRole("dialog", { name: `실행 기록 — ${name}` }).getByText("아직 판정한 회차가 없습니다")).toBeVisible();
    await page.getByRole("dialog", { name: `실행 기록 — ${name}` }).getByRole("button", { name: "닫기", exact: true }).click();

    // 삭제(2단 확인)
    await tr.getByRole("button", { name: `${name} 삭제`, exact: true }).click();
    await tr.getByRole("button", { name: `${name} 삭제 확인` }).click();
    await expect(page.getByRole("link", { name })).toHaveCount(0);
    expect(await scheduleOf(page, bid)).toBeUndefined();
  });

  test("rm 배치 스케줄은 「되돌릴 수 없음」 동의 전까지 저장할 수 없다 — 동의하면 만들어진다", async ({ page }) => {
    const stamp = Date.now().toString(36);
    const name = `sched-rm-${stamp}`;
    const bid = await createBatch(page, { operation: "rm", options: { recursive: true }, name,
                                          items: [{ storage: STORAGE_NAME, target: `sched-rm-${stamp}` }] });
    const { hour, hh } = farSlot();
    try {
      // 서버도 동의 없는 무장을 거부한다(화면의 잠금은 표시일 뿐). 시각도 farSlot — 거부가 회귀해 만들어져 버려도 돌지 않게.
      const refused = await page.request.post("/api/admin/batch-schedules", {
        data: { batch_id: bid, kind: "daily", spec: { hour, minute: 0 } } });
      expect(refused.status()).toBe(422);
      expect(((await refused.json()) as { detail: string }).detail).toBe("batch_schedule_ack_required");

      await page.goto("/admin/batch-schedules");
      await page.getByRole("button", { name: "스케줄 추가" }).click();
      const dlg = await pickBatch(page, bid);
      // 창 기본값(매일 02:00)은 테스트 시각에 따라 곧 돌 수 있다 — 시각을 farSlot 으로 바꾼다.
      await dlg.getByLabel("실행 시각(KST)").fill(`${hh}:00`);
      await expect(dlg.getByText(`매일 ${hh}:00 (KST)`)).toBeVisible();
      await expect(dlg.getByRole("note", { name: "반복 삭제 경고" })).toContainText("되돌릴 수 없습니다");
      const add = dlg.getByRole("button", { name: "추가" });
      await expect(add).toBeDisabled();
      await dlg.getByRole("checkbox", { name: /되돌릴 수 없는 삭제/ }).check();
      await expect(add).toBeEnabled();
      await add.click();
      await expect(page.getByRole("dialog", { name: "스케줄 추가" })).toHaveCount(0);
      const created = await scheduleOf(page, bid);
      expect(created?.acknowledged_irreversible).toBe(true);
      expect(created?.destructive).toBe(true);
      expect(created?.spec).toEqual({ hour, minute: 0 });
      await expect(page.getByRole("row").filter({ has: page.getByRole("link", { name }) })
        .getByText("rm", { exact: true })).toBeVisible();
    } finally {
      // 정리: 스케줄(세션 삭제) → rm 배치 취소(미리보기만 돈 상태 -- 아무것도 지우지 않았다)
      const s = await scheduleOf(page, bid);
      if (s) await page.request.delete(`/api/admin/batch-schedules/${s.schedule_id}`);
      await page.request.post(`/api/admin/batches/${bid}:cancel`);
    }
  });

  test("375 폭에서 스케줄 화면이 가로로 넘치지 않는다(표는 래퍼 안에서만 스크롤)", async ({ page }) => {
    const stamp = Date.now().toString(36);
    const bid = await createBatch(page, { operation: "scan", options: {}, name: `sched-narrow-${stamp}`,
                                          items: [{ storage: STORAGE_NAME, target: `narrow-${stamp}` }] });
    // 매월 31일 + farSlot: 가장 긴 요약 꼴(「… (KST, 없는 달은 말일)」)이라 375 넘침을 보기에도 맞다. 이 배치 행만 집어 본다.
    const { hour, hh } = farSlot();
    const made = await page.request.post("/api/admin/batch-schedules", {
      data: { batch_id: bid, kind: "monthly", spec: { day: 31, hour, minute: 0 } } });
    expect(made.status(), await made.text()).toBe(201);
    const sid = ((await made.json()) as ScheduleRow).schedule_id;
    // 「정리」 칸의 채워진 모양(누적 / 「마지막 <시각>」)은 컨트롤러 정리 루프가 돌아야 생겨 e2e 로 만들 수 없다 — 이 스케줄 행만
    // 목록 응답에서 pruned_total·pruned_at 을 합성한다(나머지는 실 서버 값, 15-usage-filters 와 같은 page.route 수법).
    await page.route(/\/api\/admin\/batch-schedules(\?.*)?$/, async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      const upstream = await route.fetch();
      const rows = (await upstream.json()) as ScheduleRow[];
      await route.fulfill({ response: upstream, json: rows.map((s) => s.schedule_id === sid
        ? { ...s, pruned_total: 1234, pruned_at: "2026-11-16T12:42:00Z" } : s) });
    });
    try {
      await page.setViewportSize({ width: 375, height: 800 });
      await page.goto("/admin/batch-schedules");
      const tr = page.getByRole("row").filter({ has: page.getByRole("link", { name: `sched-narrow-${stamp}` }) });
      await expect(tr.getByText(`매월 31일 ${hh}:00 (KST, 없는 달은 말일)`)).toBeVisible();
      const [scroll, client] = await page.evaluate(() =>
        [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
      expect(scroll, `375 에서 문서가 가로로 넘친다(${scroll} > ${client})`).toBeLessThanOrEqual(client);
      // 「정리」 칸은 두 줄 — 둘째 줄 「마지막 2026-11-16 21:42:00」이 한 덩어리라 하이픈(d29)·「마지막」 뒤 공백(d30 리뷰: 390 에서
      // 「마지막」만 따로 떨어져 세 줄)에서 꺾이지 않는다. 「마지막」 글자와 시각 조각의 줄(top)이 같고 시각 조각이 한 줄인지 본다.
      await expect(tr.getByText("정리 누적 1,234건")).toBeVisible();
      const last = tr.getByText("마지막 2026-11-16 21:42:00", { exact: true });
      await expect(last).toBeVisible();
      const geo = await last.evaluate((el) => {
        const label = document.createRange();
        label.selectNodeContents(el.firstChild!);           // 「마지막 」 글자 노드
        const stampRects = el.querySelector("span")!.getClientRects();
        return { labelTop: label.getClientRects()[0].top, stampTops: Array.from(stampRects, (r) => r.top) };
      });
      expect(geo.stampTops, "시각이 여러 줄로 꺾였다").toHaveLength(1);
      expect(Math.abs(geo.stampTops[0] - geo.labelTop), "「마지막」과 시각이 다른 줄이다").toBeLessThan(2);
    } finally {
      await page.request.delete(`/api/admin/batch-schedules/${sid}`);
    }
  });
});
