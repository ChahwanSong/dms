import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";
import { UsageAnalysis } from "./UsageAnalysis";
import type { UnregisteredStorage, UsageStorageSummary } from "../../lib/types";

// 사용량 분석 미등록 스토리지 정리(2026-10-12 사용자 요청: 「미등록 스토리지 항목들은 스토리지 항목별로 전체 clean up」).
// 2026-10-20: 따로 뜨던 칸 대신 「미등록」 탭의 경고 띠 -- 페이지를 그리고 그 탭을 눌러 들어간다. 정리 결과·고아 오류·목록
// 오류는 페이지(탭 줄 위)가 말한다.
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const LIST_URL = "/api/admin/usage/unregistered-storages";
const PURGE_URL = "/api/admin/usage/unregistered-storages:purge";
const RID = (n: number) => `${String(n).padStart(2, "0")}${"a".repeat(30)}`;
const BID = "b".repeat(32);

const OLD: UnregisteredStorage = {
  storage_name: "old-gpfs", target_count: 3, succeeded_scan_count: 5, last_scan_at: "2026-01-01T00:00:00Z",
  single_count: 4, batches: [{ batch_id: BID, request_count: 2 }], request_count: 6, mixed_batches: [],
  last_known: { backend_type: "gpfs", mount_path: "/old", managed_root: "/old/managed" },
};
const GHOST: UnregisteredStorage = { ...OLD, storage_name: "ghost", batches: [], single_count: 1, request_count: 1,
                                     target_count: 1, succeeded_scan_count: 1, last_known: null };

const REGISTERED: UsageStorageSummary = {
  storage_name: "cephfs-dms", registered: true, source: "registered", backend_type: "cephfs", mount_path: "/cephfs",
  target_count: 0, succeeded_scan_count: 0, last_scan_at: null };

// 요약은 미등록 목록에서 만든다(성공 scan 이 있는 미등록 이름만 -- 서버 규칙) -- 정리로 목록이 비면 탭도 사라진다.
function renderPage(getList: () => UnregisteredStorage[] | Response) {
  server.use(
    http.get("/api/admin/storages", () => HttpResponse.json([{ storage_name: "cephfs-dms", mount_path: "/cephfs",
      managed_root: "/cephfs/dms", backend_type: "cephfs", enabled: 1, status: "Ready", status_detail: null }])),
    http.get("/api/admin/usage/storages", () => {
      const l = getList();
      const rows = Array.isArray(l) ? l : [];
      return HttpResponse.json({ window_limit: 10000, storages: [REGISTERED, ...rows.filter((r) => r.target_count > 0)
        .map((r): UsageStorageSummary => ({
          storage_name: r.storage_name, registered: false, source: r.last_known ? "last_known" : null,
          backend_type: r.last_known?.backend_type ?? null, mount_path: r.last_known?.mount_path ?? null,
          target_count: r.target_count, succeeded_scan_count: r.succeeded_scan_count, last_scan_at: r.last_scan_at }))] });
    }),
    http.get(LIST_URL, () => { const l = getList(); return Array.isArray(l) ? HttpResponse.json(l) : l; }),
    http.get("/api/admin/usage/scan-targets", () => HttpResponse.json([])),
    // 스토리지 합계(2026-10-21) -- 이 파일은 정리 흐름만 본다: 대상 0 응답이면 패널의 합계 블록이 없다(합계 화면은
    // UsageAnalysis.test 몫).
    http.get("/api/admin/usage/storage-totals", ({ request }) => HttpResponse.json({
      storage_name: new URL(request.url).searchParams.get("storage") ?? "", target_count: 0, summed_target_count: 0,
      pending_count: 0, progressed: false, filesystem: null })),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/admin/usage"]}>
    <UsageAnalysis /></MemoryRouter></QueryClientProvider>);
}

async function openTab(name: string) {
  await userEvent.click(await screen.findByRole("tab", { name: new RegExp(`^${name} 미등록`) }));
  return screen.findByRole("note", { name: "미등록 스토리지" });
}

const empty = { deleted: [], deleted_batches: [], skipped: [], skipped_batches: [], purge_pending: 0 };

test("미등록 목록이 비면 미등록 탭이 없다", async () => {
  let called = false;
  renderPage(() => { called = true; return []; });
  await screen.findByRole("tab", { name: /^cephfs-dms/ });
  await waitFor(() => expect(called).toBe(true));
  expect(screen.queryByRole("tab", { name: /미등록/ })).toBeNull();
  expect(screen.queryByRole("note", { name: "미등록 스토리지" })).toBeNull();
});

test("미등록 탭의 띠: 마지막 설정·규모·빨강 「기록 정리」(이름이 붙은 접근 이름)", async () => {
  renderPage(() => [OLD, GHOST]);
  const note = await openTab("old-gpfs");
  expect(note).toHaveTextContent("스토리지 목록에서 삭제된 스토리지의 기록입니다");
  expect(note).toHaveTextContent("마지막 설정 gpfs · /old");
  expect(note).toHaveTextContent("대상 3개 · 성공 scan 5개 · 정리할 작업 6개(배치 1개 포함)");
  const btn = within(note).getByRole("button", { name: "old-gpfs 기록 정리" });
  expect(btn.className).toContain("border-bad");
  const ghost = await openTab("ghost");
  expect(ghost).toHaveTextContent("마지막 설정 기록 없음");
  expect(within(ghost).getByRole("button", { name: "ghost 기록 정리" })).toBeEnabled();
});

test("확인 창: 수·범위·데이터 불변 안내, 체크 전엔 잠김 -- 회차를 이어 부르고(건너뛴 항목은 제외로) 결과를 말한다", async () => {
  let list = [OLD];
  const bodies: unknown[] = [];
  server.use(
    http.post(PURGE_URL, async ({ request }) => {
      const body = await request.json();
      bodies.push(body);
      if (bodies.length === 1) {
        return HttpResponse.json({ ...empty, storage_name: "old-gpfs",
          deleted: [{ request_id: RID(1), job_ids: ["j1"] }, { request_id: RID(2), job_ids: ["j2"] }],
          skipped: [{ request_id: RID(3), reason: "request_recently_finished" }],
          skipped_batches: [{ batch_id: BID, reason: "storage_history_batch_mixed", request_id: RID(9) }],
          remaining: { requests: 2, batches: 1 }, remaining_untried: { requests: 1, batches: 0 } });
      }
      list = [];
      return HttpResponse.json({ ...empty, storage_name: "old-gpfs",
        deleted: [{ request_id: RID(4), job_ids: ["j4"] }], remaining: { requests: 1, batches: 1 },
        remaining_untried: { requests: 0, batches: 0 } });
    }),
  );
  renderPage(() => list);
  const note = await openTab("old-gpfs");
  await userEvent.click(within(note).getByRole("button", { name: "old-gpfs 기록 정리" }));
  const dlg = await screen.findByRole("dialog", { name: "미등록 스토리지 기록 정리" });
  expect(dlg).toHaveTextContent("old-gpfs 의 scan 작업 기록 6개를 영구 삭제합니다. 되돌릴 수 없습니다.");
  expect(dlg).toHaveTextContent("단일 작업 4개 · 배치 1개(작업 2개) — 성공·실패·취소된 scan 을 모두 지웁니다.");
  expect(dlg).toHaveTextContent("대상 3개(성공 scan 5개)가 사라집니다");
  expect(dlg).toHaveTextContent("스토리지의 실제 데이터는 건드리지 않습니다");
  const go = within(dlg).getByRole("button", { name: "6개 영구 삭제" });
  expect(go).toBeDisabled();
  await userEvent.click(within(dlg).getByLabelText("되돌릴 수 없음을 확인했습니다"));
  await userEvent.click(go);
  const result = await screen.findByRole("status", { name: "정리 결과" });
  expect(bodies).toEqual([
    { storage_name: "old-gpfs", exclude_request_ids: [], exclude_batch_ids: [] },
    { storage_name: "old-gpfs", exclude_request_ids: [RID(3)], exclude_batch_ids: [BID] },
  ]);
  expect(result).toHaveTextContent("old-gpfs: 작업 기록 3개 삭제됨 · 2건 제외");
  expect(result).toHaveTextContent("방금 끝난 작업입니다");
  expect(result).toHaveTextContent("다른 스토리지 항목이나 다른 작업이 섞인 배치라");
  expect(result).toHaveTextContent(`(작업 ${RID(9).slice(0, 12)})`);
  // 남은 것 = 건너뛴 것뿐(서버가 센 untried 0) → 「아직 정리하지 않은 기록」 안내는 없다
  expect(result).not.toHaveTextContent("아직 정리하지 않은 기록");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  // 탭이 사라져도 결과 줄은 남고(탭 줄 위), 포커스가 그리로 간다. 보던 탭이 사라졌으니 기본 탭으로 -- 안내 문구는 없다.
  await waitFor(() => expect(screen.queryByRole("tab", { name: /^old-gpfs/ })).toBeNull());
  await waitFor(() => expect(result).toHaveFocus());
  await waitFor(() => expect(screen.getByRole("tab", { name: /^cephfs-dms/ })).toHaveAttribute("aria-selected", "true"));
  expect(screen.queryByText(/사용량 기록이 없습니다/)).toBeNull();
  // 다 읽은 결과 줄은 닫을 수 있다
  await userEvent.click(within(result).getByRole("button", { name: "정리 결과 닫기" }));
  expect(screen.queryByRole("status", { name: "정리 결과" })).toBeNull();
});

test("전체 실패(409 storage_registered)는 창 안에서 말하고 창을 닫지 않는다", async () => {
  server.use(http.post(PURGE_URL, () => HttpResponse.json({ detail: "storage_registered" }, { status: 409 })));
  renderPage(() => [GHOST]);
  const note = await openTab("ghost");
  await userEvent.click(within(note).getByRole("button", { name: "ghost 기록 정리" }));
  const dlg = await screen.findByRole("dialog");
  await userEvent.click(within(dlg).getByLabelText("되돌릴 수 없음을 확인했습니다"));
  await userEvent.click(within(dlg).getByRole("button", { name: "1개 영구 삭제" }));
  expect(await within(dlg).findByRole("alert")).toHaveTextContent("이 스토리지는 지금 등록돼 있어");
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

test("둘째 회차 오류: 앞 회차에서 지운 수를 숨기지 않고 중간에 멈췄다고 말한다", async () => {
  let n = 0;
  server.use(http.post(PURGE_URL, () => {
    n += 1;
    if (n === 1) {
      return HttpResponse.json({ ...empty, storage_name: "old-gpfs", deleted: [{ request_id: RID(1), job_ids: [] }],
                                 remaining: { requests: 5, batches: 1 }, remaining_untried: { requests: 5, batches: 1 } });
    }
    return HttpResponse.json({ detail: "maintenance_mode" }, { status: 503 });
  }));
  renderPage(() => [OLD]);
  const note = await openTab("old-gpfs");
  await userEvent.click(within(note).getByRole("button", { name: "old-gpfs 기록 정리" }));
  const dlg = await screen.findByRole("dialog");
  await userEvent.click(within(dlg).getByLabelText("되돌릴 수 없음을 확인했습니다"));
  await userEvent.click(within(dlg).getByRole("button", { name: "6개 영구 삭제" }));
  const result = await screen.findByRole("status", { name: "정리 결과" });
  expect(result).toHaveTextContent("old-gpfs: 작업 기록 1개 삭제됨");
  expect(result).toHaveTextContent("중간에 멈췄습니다");
});

test("목록을 못 불러오면 그렇게 말한다(조용히 숨기지 않는다)", async () => {
  renderPage(() => HttpResponse.json({ detail: "admin_required" }, { status: 403 }));
  expect(await screen.findByText(/미등록 스토리지 목록을 불러오지 못했습니다/)).toBeInTheDocument();
});

test("섞인 배치: 띠·확인 창이 따로 말하고 지울 수에 넣지 않는다 -- 지울 것이 없으면 버튼이 잠긴다", async () => {
  const MIXED: UnregisteredStorage = { ...OLD, storage_name: "mix", single_count: 0, batches: [], request_count: 0,
                                       mixed_batches: [{ batch_id: BID, request_count: 2 }] };
  renderPage(() => [{ ...OLD, mixed_batches: MIXED.mixed_batches }, MIXED]);
  const mixNote = await openTab("mix");
  expect(within(mixNote).getByRole("button", { name: "mix 기록 정리" })).toBeDisabled();
  const oldNote = await openTab("old-gpfs");
  expect(oldNote).toHaveTextContent("정리할 작업 6개(배치 1개 포함) · 섞인 배치 1개는 정리하지 않음");
  await userEvent.click(within(oldNote).getByRole("button", { name: "old-gpfs 기록 정리" }));
  const dlg = await screen.findByRole("dialog");
  expect(dlg).toHaveTextContent("섞인 배치 1개(작업 2개)는 지우지 않습니다");
  expect(within(dlg).getByRole("button", { name: "6개 영구 삭제" })).toBeInTheDocument();
});

test("회차를 이어 부르는 기준은 서버의 remaining_untried -- 건너뛴 id 개수와 비교하지 않는다", async () => {
  // 첫 회차: 1개 지움, 1개 건너뜀(그 뒤 다른 곳에서 지워져 remaining 엔 없다), 시도 안 한 1개가 남았다
  // → remaining 1 == 건너뛴 수 1 이지만 untried 1 이라 다음 회차를 부른다.
  let n = 0;
  server.use(http.post(PURGE_URL, () => {
    n += 1;
    return HttpResponse.json(n === 1
      ? { ...empty, storage_name: "old-gpfs", deleted: [{ request_id: RID(1), job_ids: [] }],
          skipped: [{ request_id: RID(2), reason: "request_not_found" }],
          remaining: { requests: 1, batches: 0 }, remaining_untried: { requests: 1, batches: 0 } }
      : { ...empty, storage_name: "old-gpfs", deleted: [{ request_id: RID(3), job_ids: [] }],
          remaining: { requests: 0, batches: 0 }, remaining_untried: { requests: 0, batches: 0 } });
  }));
  renderPage(() => [OLD]);
  const note = await openTab("old-gpfs");
  await userEvent.click(within(note).getByRole("button", { name: "old-gpfs 기록 정리" }));
  const dlg = await screen.findByRole("dialog");
  await userEvent.click(within(dlg).getByLabelText("되돌릴 수 없음을 확인했습니다"));
  await userEvent.click(within(dlg).getByRole("button", { name: "6개 영구 삭제" }));
  expect(await screen.findByRole("status", { name: "정리 결과" })).toHaveTextContent("작업 기록 2개 삭제됨 · 1건 제외");
  expect(n).toBe(2);
});

test("첫 회차 전체 실패 뒤 재조회로 그 탭이 사라져도 페이지가 오류를 말한다(조용히 성공처럼 보이지 않는다)", async () => {
  let list = [GHOST];
  server.use(http.post(PURGE_URL, () => {
    list = [];                                   // 다른 관리자가 같은 이름을 다시 등록 -- 미등록 목록에서 빠진다
    return HttpResponse.json({ detail: "storage_registered" }, { status: 409 });
  }));
  renderPage(() => list);
  const note = await openTab("ghost");
  await userEvent.click(within(note).getByRole("button", { name: "ghost 기록 정리" }));
  const dlg = await screen.findByRole("dialog");
  await userEvent.click(within(dlg).getByLabelText("되돌릴 수 없음을 확인했습니다"));
  await userEvent.click(within(dlg).getByRole("button", { name: "1개 영구 삭제" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(await screen.findByRole("alert")).toHaveTextContent("ghost: 이 스토리지는 지금 등록돼 있어");
});

test("받은 뒤의 재조회 실패는 탭·결과를 지우지 않는다", async () => {
  let fail = false;
  server.use(http.post(PURGE_URL, () => {
    fail = true;
    return HttpResponse.json({ ...empty, storage_name: "old-gpfs", deleted: [{ request_id: RID(1), job_ids: [] }],
                               remaining: { requests: 5, batches: 1 }, remaining_untried: { requests: 0, batches: 0 } });
  }));
  renderPage(() => (fail ? HttpResponse.json({ detail: "x" }, { status: 500 }) : [OLD]));
  const note = await openTab("old-gpfs");
  await userEvent.click(within(note).getByRole("button", { name: "old-gpfs 기록 정리" }));
  const dlg = await screen.findByRole("dialog");
  await userEvent.click(within(dlg).getByLabelText("되돌릴 수 없음을 확인했습니다"));
  await userEvent.click(within(dlg).getByRole("button", { name: "6개 영구 삭제" }));
  expect(await screen.findByRole("status", { name: "정리 결과" })).toHaveTextContent("작업 기록 1개 삭제됨");
  expect(await screen.findByText(/미등록 스토리지 목록을 새로 고치지 못했습니다/)).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /^old-gpfs 미등록/ })).toBeInTheDocument();
  expect(screen.getByRole("note", { name: "미등록 스토리지" })).toBeInTheDocument();
});
