import { act, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";
import {
  HISTORY, TARGETS, echoOf, installServer, isoAgo, m, point, server, setup, sheetOf, st, sum, totalsOf,
} from "./usageTestKit";

// 작업 기록 저장소(artifact base) 접근 불가(2026-11-23 사고 -- dms-ssc 4add57bf 이식, dms-k8s 화면 모양으로 옮겼다: 경로 상세는 시트,
// 목록은 /targets 봉투, 합계는 한 줄 + [자세히], 스토리지별·소속별은 따로 화면). 서버는 base 수준 점검이 실패한 요청에서만
// unreadable_reason 을 싣는다. 화면은 그때만 원인을 덧붙이고(없으면 기존 정확 문자열 -- 다른 테스트 파일들이 고정한다), 데이터 쿼리를
// 폴링하지 않고 health 만 보다가 ok 로 바뀌면 사용량 쿼리를 한 번 새로 부른다.

installServer();

const BASE_DOWN = "작업 기록 저장소(공유 스토리지)에 접근할 수 없습니다(일시 장애일 수 있음)";
const DOWN = "artifact_base_unavailable";
const SHEET = "/admin/usage?storage=cephfs-dms&target=artifacts";

test("이력 0건 + base 접근 불가: 시트에 기존 문장 뒤 원인과 점검 화면 링크(실패색)", async () => {
  setup({ path: SHEET, history: { ...HISTORY, points: [], skipped_unreadable: 2, unreadable_reason: DOWN } });
  const sheet = await sheetOf("artifacts");
  const p = await within(sheet).findByText(`성공 scan 2건의 리포트를 읽지 못해 표시할 이력이 없습니다 — ${BASE_DOWN}`);
  expect(p.className).toContain("text-bad");
  expect(within(p).getByRole("link", { name: "아티팩트 경로 점검 보기" })).toHaveAttribute("href", "/admin/artifact-base");
});

test("이력 있음 + 빠진 scan + base 접근 불가: 요약 위 주의 줄(role=status) -- 사유가 없으면 그 줄은 없다", async () => {
  setup({ path: SHEET, history: { ...HISTORY, skipped_unreadable: 1, unreadable_reason: DOWN } });
  const sheet = await sheetOf("artifacts");
  const note = await within(sheet).findByText(
    `최근 성공 scan 1건의 리포트를 읽지 못해 이력·최신 수치에서 빠졌습니다 — ${BASE_DOWN}`, { exact: false });
  const status = note.closest("[role=status]")!;
  expect(status).not.toBeNull();
  // 요약(dl)보다 앞 -- 남은 점이 「최신」처럼 읽히기 전에 말한다
  expect(status.compareDocumentPosition(within(sheet).getByLabelText("최신 scan 요약"))
    & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  cleanup();
  setup({ path: SHEET, history: HISTORY });                         // skipped 1, 사유 없음
  const plain = await sheetOf("artifacts");
  await within(plain).findByText("최신 실 사용량");
  expect(within(plain).queryByText(/이력·최신 수치에서 빠졌습니다/)).toBeNull();
});

test("최신 리포트를 못 읽은 행 + base 접근 불가: (DB) title 과 시트의 분포 문장에 원인, 패널 위 안내 한 줄", async () => {
  setup({ targets: { "cephfs-dms": [{ storage_name: "cephfs-dms", target: "dbonly", scan_count: 1, last_scan_at: isoAgo(1),
          latest: point({ total_bytes: 2048, summary: {}, time_histograms: {}, file_size_histogram: [], report_readable: false,
                          unreadable_reason: DOWN }) }] } });
  const btn = await screen.findByRole("button", { name: "dbonly" });
  const row = btn.closest("tr")!;
  expect(within(row).getByText("(DB)")).toHaveAttribute(
    "title", "최신 스캔 리포트를 읽지 못해 잡 기록(DB)의 용량을 표시합니다 (공유 스토리지 접근 불가)");
  // 행 키만으로도 패널이 한 번 말한다(최상위 사유가 없는 응답이어도 행 키가 진실)
  expect(screen.getByText(`일부 대상의 최신 scan 리포트를 읽지 못했습니다 — ${BASE_DOWN}`)).toBeInTheDocument();
  await userEvent.click(btn);
  const sheet = await sheetOf("dbonly");
  expect(await within(sheet).findByText(
    `최신 scan 의 리포트를 읽지 못해 온도 분포를 알 수 없습니다 — ${BASE_DOWN}`)).toBeInTheDocument();
  expect(within(sheet).getByText(`최신 scan 의 리포트를 읽지 못해 크기 분포를 알 수 없습니다 — ${BASE_DOWN}`))
    .toBeInTheDocument();
});

test("목록 최상위 unreadable_reason(/targets 봉투)이 있으면 표 위에 안내 한 줄 -- 없으면 없다", async () => {
  setup();
  await screen.findByRole("button", { name: "artifacts" });
  expect(screen.queryByText(/일부 대상의 최신 scan 리포트를 읽지 못했습니다/)).toBeNull();
  cleanup();
  setup();
  // setup 이 붙인 목록 핸들러를 덮는다(나중에 붙은 핸들러가 먼저 문다)
  server.use(http.get("/api/admin/usage/targets", ({ request }) => HttpResponse.json({
    rows: TARGETS, matched_count: 2, window_limit: 10000, window_truncated: false, filters: echoOf(new URL(request.url)),
    offset: 0, has_more: false, pageable_count: 2, unreadable_reason: DOWN })));
  expect(await screen.findByText(`일부 대상의 최신 scan 리포트를 읽지 못했습니다 — ${BASE_DOWN}`)).toBeInTheDocument();
});

test("합계가 base 접근 불가로 멈추면: 「읽는 중」이 아니라 「값 모름 N개(공유 스토리지 접근 불가)」, 1초 이어 읽기도 없다", async () => {
  const calls: number[] = [];
  setup({ totals: (s, call) => {
    calls.push(call);
    return totalsOf(s, { pending_count: 2, progressed: true, total_files: m(600, 2, 2), unreadable_reason: DOWN });
  } });
  const region = await screen.findByRole("region", { name: "이 스토리지 합계" });
  // 합계 줄: 「읽는 중」 대신 원인 한 조각
  expect(await within(region).findByText("일부 값 모름 (공유 스토리지 접근 불가)")).toBeInTheDocument();
  expect(region).not.toHaveTextContent("요약 읽는 중");
  // 1초 이어 읽기가 없다(progressed 가 참이어도 -- 다음 호출은 같은 벽에 부딪힌다). 첫 응답 뒤 [자세히] 안의 합계 블록이 새 관찰자로
  // 붙어 한 번 더 물을 수 있다(사유가 있는 캐시는 staleTime 0 -- 다음 마운트에 다시 묻는다, 폴링이 아니다) -- 그 뒤로는 늘지 않는다.
  await new Promise((r) => setTimeout(r, 300));
  const first = calls.length;
  expect(first).toBeLessThanOrEqual(2);
  await new Promise((r) => setTimeout(r, 1300));
  expect(calls.length).toBe(first);
  // [자세히] 의 각주
  const details = await waitFor(() => {
    const d = region.querySelector("details");
    if (d === null) throw new Error("합계가 아직 오지 않았다");
    return d;
  });
  if (!details.open) await userEvent.click(within(details).getByText("자세히"));
  expect(await within(details).findByText(/값 모름 2개\(공유 스토리지 접근 불가\)/)).toBeInTheDocument();
  expect(details).not.toHaveTextContent("요약 읽는 중");
  const settled = calls.length;
  await new Promise((r) => setTimeout(r, 1300));
  expect(calls.length).toBe(settled);
});

test("스토리지별 비교: 합계가 base 접근 불가로 멈춘 행은 이름 옆 표식(낭독 문장·title 에 원인)", async () => {
  setup({ path: "/admin/usage/storages", storages: [st("cephfs-dms"), st("weka")], summary: [sum("cephfs-dms"), sum("weka")],
          totals: { "cephfs-dms": totalsOf("cephfs-dms"),
                    weka: totalsOf("weka", { total_bytes: m(1024, 1, 1), unreadable_reason: DOWN }) } });
  const rowOf = (name: string) => within(screen.getByRole("table")).getByRole("link", { name }).closest("tr")!;
  await waitFor(() => expect(rowOf("weka")).toHaveTextContent("1.0 KiB"));
  expect(within(rowOf("weka")).getByText("(공유 스토리지 접근 불가 — 합계가 일부만 읽혔습니다)")).toBeInTheDocument();
  expect(within(rowOf("weka")).getAllByRole("cell")[1].getAttribute("title")).toContain(BASE_DOWN);
  expect(within(rowOf("cephfs-dms")).queryByText(/공유 스토리지 접근 불가/)).toBeNull();
});

test("소속별: 응답이 base 접근 불가로 멈추면 「읽는 중」 대신 원인 줄(점검 링크), 1초 이어 읽기도 없다", async () => {
  const calls: string[] = [];
  const body = (u: URL) => ({
    storage_name: "cephfs-dms", computed_at: isoAgo(0), by: "requester", requested_by: u.searchParams.get("by"),
    window_limit: 10000, window_truncated: false, target_count: 2, scope: { inventory: 0, recorded: 0, outside: 2, unknown: 0 },
    cluster_count: 0, storage_total: { total_bytes: m(1024, 1, 1), summed_target_count: 2 },
    rows: [{ requester_id: "mason", target_count: 2, summed_target_count: 2, nested_target_count: 0,
             total_bytes: m(1024, 1, 1), total_files: m(3, 1, 1),
             hot_ratio: { value: 1, hot_bytes: 1024, total_bytes: 1024, known: 1, unknown: 1 },
             oldest_scan_at: isoAgo(5), newest_scan_at: isoAgo(1), filter: { requester: "mason" } }],
    rows_truncated: false, extras: { outside: null, unknown: null, none: null },
    pending_count: 1, unreadable_count: 0, progressed: true, unreadable_reason: DOWN });
  setup({ path: "/admin/usage/breakdown?storage=cephfs-dms",
          breakdown: (u) => { calls.push(u.toString()); return body(u); } });
  const note = await screen.findByText(`일부 대상의 최신 scan 리포트를 읽지 못해 값을 모릅니다 — ${BASE_DOWN}`, { exact: false });
  expect(within(note.closest("[role=status]") as HTMLElement).getByRole("link", { name: "아티팩트 경로 점검 보기" }))
    .toHaveAttribute("href", "/admin/artifact-base");
  expect(screen.queryByText(/^요약 읽는 중 \d+개$/)).toBeNull();
  await new Promise((r) => setTimeout(r, 300));
  const first = calls.length;
  await new Promise((r) => setTimeout(r, 1300));
  expect(calls.length).toBe(first);
});

test("복구 감시: 사유가 없으면 health 를 묻지 않는다", async () => {
  let health = 0;
  setup({ path: SHEET });
  server.use(http.get("/api/admin/artifact-base/health", () => { health += 1; return HttpResponse.json({ ok: true }); }));
  const sheet = await sheetOf("artifacts");
  await within(sheet).findByText("최신 실 사용량");
  await new Promise((r) => setTimeout(r, 200));
  expect(health).toBe(0);
});

test("복구 감시: 사유가 있는 동안 health 만 15초마다 보고(데이터 쿼리 폴링 없음), ok 로 바뀌면 사용량 쿼리를 한 번 새로 부른다", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    let healthOk = false, healthCalls = 0, historyCalls = 0;
    setup({ path: SHEET });
    server.use(
      http.get("/api/admin/artifact-base/health", () => {
        healthCalls += 1;
        return HttpResponse.json({ ok: healthOk, reason: healthOk ? null : "artifact_base_jobs_missing",
                                   checked_at: null, instance: "dms-api-1", node_name: "n1" });
      }),
      http.get("/api/admin/usage/scan-history", () => {
        historyCalls += 1;
        return HttpResponse.json(healthOk ? HISTORY
          : { ...HISTORY, points: [], skipped_unreadable: 3, unreadable_reason: DOWN });
      }),
    );
    expect(await screen.findByText(`성공 scan 3건의 리포트를 읽지 못해 표시할 이력이 없습니다 — ${BASE_DOWN}`)).toBeInTheDocument();
    await waitFor(() => expect(healthCalls).toBe(1));
    await act(async () => { await vi.advanceTimersByTimeAsync(15_100); });
    await waitFor(() => expect(healthCalls).toBe(2));
    expect(historyCalls).toBe(1);                       // 실패 중엔 데이터 쿼리를 다시 부르지 않는다
    healthOk = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(15_100); });
    await waitFor(() => expect(historyCalls).toBe(2));  // ok 로 바뀐 순간 한 번
    const sheet = await sheetOf("artifacts");
    expect(await within(sheet).findByText("최신 실 사용량")).toBeInTheDocument();
    // 사유가 사라졌으니 감시도 꺼진다
    const settled = healthCalls;
    await act(async () => { await vi.advanceTimersByTimeAsync(15_100); });
    expect(healthCalls).toBe(settled);
  } finally {
    vi.useRealTimers();
  }
});
