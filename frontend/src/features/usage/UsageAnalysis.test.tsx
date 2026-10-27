import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import { setupServer } from "msw/node";
import { delay, http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest";
import { UsageAnalysis, hotRatio, pointEpoch, stackLayout, staleClass } from "./UsageAnalysis";
import type { HistogramBucket, Storage, UnregisteredStorage, UsagePoint, UsageStorageSummary,
              UsageStorageTotals } from "../../lib/types";
import { downloadCsv } from "../../lib/csvExport";

// 다운로드(Blob·a.click)는 jsdom 밖 동작이라 가로챈다 -- 직렬화(toCsv)는 진짜를 쓴다.
vi.mock("../../lib/csvExport", async (orig) => ({
  ...(await orig<typeof import("../../lib/csvExport")>()), downloadCsv: vi.fn() }));

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

// ---- 순수 헬퍼 ----

const buckets = (vals: (number | undefined)[], ages = true) =>
  vals.map((b, i) => ({ bucket: `[${i}d,${i + 1}d]`,
                        ...(ages ? { min_age_days: i, max_age_days: i + 1 } : {}),
                        ...(b === undefined ? {} : { bytes: b }) }));

test("hotRatio: ≤180일(HOT_AGE_MAX_DAYS) 버킷 비중 / 전체 0·bytes 결측은 null(모름 ≠ 0)", () => {
  // 기준 180일(2026-08-24 사용자 결정, 7일에서 상향): [91,180] 까지 hot(30+30),
  // [181,365] 는 cold(40) -- 60/100. 경계값 180 자체가 hot 에 포함됨을 고정한다.
  const b = [{ bucket: "a", min_age_days: 0, max_age_days: 7, bytes: 30 },
             { bucket: "b", min_age_days: 91, max_age_days: 180, bytes: 30 },
             { bucket: "c", min_age_days: 181, max_age_days: 365, bytes: 40 }];
  expect(hotRatio(b)).toBeCloseTo(0.6);
  expect(hotRatio(buckets([0, 0, 0]))).toBeNull();       // 전체 0: 비율 정의 불가
  expect(hotRatio(buckets([10, undefined]))).toBeNull(); // bytes 결측: 모름
  // bytes 가 실렸는데 나이 필드가 없으면 hot/cold 판정 근거가 없다 -- cold 로
  // 접으면 비율이 조용히 내려간다(리뷰 확인). 0 바이트 무나이 버킷은 무해.
  expect(hotRatio(buckets([10], false))).toBeNull();
  expect(hotRatio([{ bucket: "z", bytes: 0 } as never,
                   ...buckets([10])])).toBeCloseTo(1);
  expect(hotRatio([])).toBeNull();
  expect(hotRatio(undefined)).toBeNull();
});

test("stackLayout: 비중 % / 전체 0·결측은 null", () => {
  expect(stackLayout(buckets([30, 70]))!.map((s) => s.pct)).toEqual([30, 70]);
  expect(stackLayout(buckets([0, 0]))).toBeNull();
  expect(stackLayout(buckets([1, undefined]))).toBeNull();
});

test("pointEpoch: 리포트 생성 시각 우선, 결측이면 완료 시각, 둘 다 없으면 null", () => {
  const base = { job_id: "j", request_id: "r", summary: {}, time_histograms: {},
                 requester: null, total_bytes: null } as Partial<UsagePoint>;
  expect(pointEpoch({ ...base, generated_at_epoch: 5,
                      finished_at: "2026-08-23T00:00:00Z" } as UsagePoint)).toBe(5);
  expect(pointEpoch({ ...base, generated_at_epoch: null,
                      finished_at: "1970-01-01T00:01:40Z" } as UsagePoint)).toBe(100);
  expect(pointEpoch({ ...base, generated_at_epoch: null,
                      finished_at: null } as UsagePoint)).toBeNull();
});

// ---- 화면 픽스처 ----

const DAY = 86_400_000;
const isoAgo = (days: number) => new Date(Date.now() - days * DAY).toISOString().replace(/\.\d{3}Z$/, "Z");

const point = (over: Partial<UsagePoint>): UsagePoint => ({
  job_id: "j1", request_id: "r1", finished_at: "2026-08-23T13:00:00Z",
  generated_at_epoch: 1787500000, total_bytes: 1024,
  summary: { total_files: 7 },
  time_histograms: { atime: buckets([1024, 0]) as never,
                     mtime: buckets([512, 512]) as never },
  requester: "alice", ...over });

const TARGETS = [
  { storage_name: "cephfs-dms", target: "artifacts", scan_count: 3, last_scan_at: "2026-08-23T13:00:00Z" },
  { storage_name: "cephfs-dms", target: "team", scan_count: 1, last_scan_at: "2026-08-22T13:00:00Z" },
];

const HISTORY = {
  storage_name: "cephfs-dms", target: "artifacts",
  points: [
    point({ job_id: "j1", request_id: "r1", generated_at_epoch: 1787400000, total_bytes: 1024 }),
    point({ job_id: "j2", request_id: "r2", generated_at_epoch: 1787450000,
            total_bytes: null }),               // 용량 미상 -- 차트 제외 대상
    point({ job_id: "j3", request_id: "r3", generated_at_epoch: 1787500000, total_bytes: 3072 }),
  ],
  skipped_unreadable: 1,
};

const st = (name: string, over: Partial<Storage> = {}): Storage => ({
  storage_name: name, mount_path: `/${name}`, managed_root: `/${name}/dms`, backend_type: "cephfs", enabled: 1,
  status: "Ready", status_detail: null, ...over });
const STORAGES = [st("cephfs-dms", { mount_path: "/cephfs", managed_root: "/cephfs/dms" })];

const sum = (name: string, over: Partial<UsageStorageSummary> = {}): UsageStorageSummary => ({
  storage_name: name, registered: true, source: "registered", backend_type: "cephfs", mount_path: `/${name}`,
  target_count: 2, succeeded_scan_count: 4, last_scan_at: isoAgo(3), ...over });
const SUMMARY = [sum("cephfs-dms", { mount_path: "/cephfs" })];

const unreg = (name: string, over: Partial<UnregisteredStorage> = {}): UnregisteredStorage => ({
  storage_name: name, target_count: 1, succeeded_scan_count: 1, last_scan_at: isoAgo(200), single_count: 1,
  batches: [], request_count: 1, mixed_batches: [],
  last_known: { backend_type: "gpfs", mount_path: "/old", managed_root: "/old/m" }, ...over });

// 파일 크기 구간(dscan 실측 10구간 모양 -- 마지막 구간은 upper_inclusive 키가 없다).
const SIZE_LIMITS = [4096, 65536, 1048576, 16777216, 268435456, 1073741824, 17179869184, 274877906944, 4398046511104];
const sizeHist = (counts: number[]): HistogramBucket[] => counts.map((c, i) => {
  const lo = i === 0 ? 0 : SIZE_LIMITS[i - 1] + 1, hi = SIZE_LIMITS[i];
  return { bucket: hi === undefined ? `[${lo},INF]` : `[${lo},${hi}]`, lower_inclusive: lo,
           ...(hi === undefined ? {} : { upper_inclusive: hi }), count: c };
});
const pad10 = (head: number[]) => [...head, ...Array(10 - head.length).fill(0)] as number[];

// 스토리지 합계 응답(design-1021 storage-totals 계약). 기본값 = 테스트베드 cephfs-dms 실측(최상위 4개 · 하위 5개).
const m = (sum: number | null, known: number, unknown = 0) => ({ sum, known, unknown });
const totalsOf = (name: string, over: Partial<UsageStorageTotals> = {}): UsageStorageTotals => ({
  storage_name: name, computed_at: isoAgo(0), truncated: false, window_limit: 10000,
  target_count: 9, summed_target_count: 4, nested_target_count: 5, invalid_target_count: 0,
  unreadable_count: 0, pending_count: 0, progressed: false,
  total_bytes: m(17786075, 4), total_files: m(662, 4), total_directories: m(243, 4), total_symlinks: m(0, 4),
  total_other: m(0, 4), total_entries: m(905, 4), scan_errors: m(0, 4), broken_paths_total: m(0, 4),
  hot_ratio: { value: 1, hot_bytes: 17786075, total_bytes: 17786075, known: 4, unknown: 0 },
  file_size_histogram: { buckets: sizeHist(pad10([590, 66, 2, 4])), known: 4, unknown: 0 },
  oldest_scan_at: isoAgo(65), newest_scan_at: isoAgo(18),
  filesystem: { total_bytes: 24498929664, used_bytes: 37748736, node_name: "dms-w2", reported_at: isoAgo(0),
                fresh: true, node_count: 5, consistent: true },
  ...over });
// 기록 없는 스토리지(대상 0) -- 값은 전부 모름(null), 히스토그램 빈 배열(「0 B」가 아니다).
const emptyTotals = (name: string): UsageStorageTotals => totalsOf(name, {
  target_count: 0, summed_target_count: 0, nested_target_count: 0,
  total_bytes: m(null, 0), total_files: m(null, 0), total_directories: m(null, 0), total_symlinks: m(null, 0),
  total_other: m(null, 0), total_entries: m(null, 0), scan_errors: m(null, 0), broken_paths_total: m(null, 0),
  hot_ratio: { value: null, hot_bytes: null, total_bytes: null, known: 0, unknown: 0 },
  file_size_histogram: { buckets: [], known: 0, unknown: 0 }, oldest_scan_at: null, newest_scan_at: null,
  filesystem: null });

// 지금 URL(검색 문자열)을 화면에 꺼내 둔다 -- 탭 전환·행 접기가 URL 에 무엇을 남기는지 단언한다.
function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.search}</div>;
}
const loc = () => new URLSearchParams(screen.getByTestId("loc").textContent ?? "");

interface Setup {
  path?: string;
  storages?: Storage[];
  summary?: UsageStorageSummary[] | "error" | "slow" | "html";
  windowLimit?: number;
  unregistered?: UnregisteredStorage[];
  scanStorages?: string[];
  // 스토리지별 목록(storage 파라미터로 고른다) 또는 함수
  targets?: Record<string, unknown[]>;
  onTargets?: (u: URL) => void;
  slowTargets?: string;              // 이 스토리지의 목록 응답을 늦춘다
  filterPath?: boolean;              // path 파라미터로 행을 거른다(부분 문자열)
  history?: unknown;
  onHistory?: (u: URL) => void;
  onExport?: (u: URL) => unknown;
  // 스토리지 합계(storage 파라미터로 고른다 -- 없으면 대상 0 응답). "error" = 500, "html" = 옛 API 의 SPA 200.
  totals?: Record<string, UsageStorageTotals | "error" | "html">
    | ((storage: string, call: number) => UsageStorageTotals | "error" | "html");
  onTotals?: (u: URL) => void;
}

function setup(o: Setup = {}) {
  const summary = o.summary ?? SUMMARY;
  const totalsCalls: Record<string, number> = {};
  server.use(
    http.get("/api/admin/storages", () => HttpResponse.json(o.storages ?? STORAGES)),
    http.get("/api/admin/usage/storages", async () => {
      if (summary === "error") return HttpResponse.json({ detail: "internal" }, { status: 500 });
      if (summary === "slow") { await delay("infinite"); return HttpResponse.json({}); }
      // 옛 API 파드(경로 없음)는 SPA 폴백으로 index.html 을 200 으로 준다
      if (summary === "html") {
        return new HttpResponse("<!doctype html><html></html>", { status: 200, headers: { "content-type": "text/html" } });
      }
      return HttpResponse.json({ window_limit: o.windowLimit ?? 10000, storages: summary });
    }),
    http.get("/api/admin/usage/unregistered-storages", () => HttpResponse.json(o.unregistered ?? [])),
    http.get("/api/admin/usage/scan-storages", () => HttpResponse.json(o.scanStorages ?? ["cephfs-dms"])),
    http.get("/api/admin/usage/scan-targets", async ({ request }) => {
      const u = new URL(request.url);
      o.onTargets?.(u);
      const s = u.searchParams.get("storage") ?? "";
      if (o.slowTargets === s) await delay("infinite");
      const rows = ((o.targets ?? { "cephfs-dms": TARGETS })[s] ?? []) as { target: string }[];
      const q = u.searchParams.get("path");
      return HttpResponse.json(o.filterPath && q ? rows.filter((r) => r.target.includes(q)) : rows);
    }),
    http.get("/api/admin/usage/scan-history", ({ request }) => {
      o.onHistory?.(new URL(request.url));
      return HttpResponse.json(o.history ?? HISTORY);
    }),
    http.get("/api/admin/usage/export", ({ request }) =>
      HttpResponse.json(o.onExport?.(new URL(request.url)) ?? { generated_at: "", count: 0, truncated: false, rows: [] })),
    http.get("/api/admin/usage/storage-totals", ({ request }) => {
      const u = new URL(request.url);
      o.onTotals?.(u);
      const s = u.searchParams.get("storage") ?? "";
      totalsCalls[s] = (totalsCalls[s] ?? 0) + 1;
      const t = typeof o.totals === "function" ? o.totals(s, totalsCalls[s])
        : (o.totals ?? { "cephfs-dms": totalsOf("cephfs-dms") })[s] ?? emptyTotals(s);
      if (t === "error") return HttpResponse.json({ detail: "internal" }, { status: 500 });
      if (t === "html") {
        return new HttpResponse("<!doctype html><html></html>", { status: 200, headers: { "content-type": "text/html" } });
      }
      return HttpResponse.json(t);
    }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[o.path ?? "/admin/usage"]}>
        <UsageAnalysis /><LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>);
  return qc;
}

// 탭 이름(보이는 글자 + 낭독용 「대상 N개」)을 순서대로 단언한다.
const expectTabNames = (names: string[]) => {
  const tabs = screen.getAllByRole("tab");
  expect(tabs).toHaveLength(names.length);
  names.forEach((n, i) => expect(tabs[i]).toHaveAccessibleName(n));
};
const tab = (name: RegExp | string) => screen.findByRole("tab", { name: typeof name === "string" ? new RegExp(`^${name}`) : name });

// ---- 탭 ----

test("처음부터 스토리지 탭: 기본 탭이 골라지고 목록 요청에 늘 storage 가 실린다(「전체」 탭·스토리지 열 없음)", async () => {
  const seen: URL[] = [];
  setup({ onTargets: (u) => seen.push(u) });
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
  expect(await tab("cephfs-dms")).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: "cephfs-dms 대상 2개" })).toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: /전체/ })).toBeNull();
  expect(screen.getByRole("heading", { name: "사용량 분석" })).toBeInTheDocument();
  expect(seen.length).toBeGreaterThan(0);
  for (const u of seen) expect(u.searchParams.get("storage")).toBe("cephfs-dms");
  // 표: 6열(경로·실 사용량·파일 수·hot 비율·스캔 횟수·최근 스캔) -- 스토리지 열은 탭이 말한다
  const heads = within(screen.getByRole("table")).getAllByRole("columnheader");
  expect(heads).toHaveLength(6);
  ["경로", "실 사용량", "파일 수", "hot 비율", "스캔 횟수", "최근 스캔"].forEach((label, i) =>
    expect(heads[i].textContent).toMatch(new RegExp(`^${label}`)));
  // 패널: tabpanel 이 선택 탭으로 이름 붙는다 + 메타 한 줄(마운트 · 타입 · 대상 수 · 최근 스캔)
  const panel = screen.getByRole("tabpanel", { name: /cephfs-dms/ });
  expect(panel).toHaveTextContent("cephfs-dms/cephfscephfs대상 2개최근 스캔 3일 전");
  // 기본 탭은 파생값이다 -- URL 에 쓰지 않는다
  expect(loc().get("storage")).toBeNull();
});

test("기본 탭: 기록이 있는 등록 스토리지 중 이름순 첫째(기록 0 은 건너뜀) → 없으면 기록 있는 미등록 → 첫 탭", async () => {
  setup({
    storages: [st("a-empty"), st("b-full"), st("c-full")],
    summary: [sum("a-empty", { target_count: 0, succeeded_scan_count: 0, last_scan_at: null }),
              sum("b-full"), sum("c-full"), sum("old", { registered: false, source: "last_known" })],
    unregistered: [unreg("old")],
    targets: { "b-full": [{ ...TARGETS[0], storage_name: "b-full" }] },
  });
  expect(await tab("b-full")).toHaveAttribute("aria-selected", "true");
  // 기록 0 탭은 흐린 「0」(진짜 0 -- 요약이 왔다)
  expect(screen.getByRole("tab", { name: "a-empty 대상 0개" })).toHaveAttribute("aria-selected", "false");
  // 순서: 등록 이름순 → 미등록
  expectTabNames(["a-empty 대상 0개", "b-full 대상 2개", "c-full 대상 2개", "old 미등록 대상 2개"]);   // 수는 요약이 우선
});

test("기본 탭: 기록 있는 등록 스토리지가 없으면 기록 있는 미등록 탭", async () => {
  setup({
    storages: [st("a-empty")],
    summary: [sum("a-empty", { target_count: 0, succeeded_scan_count: 0, last_scan_at: null }),
              sum("old", { registered: false, source: "last_known" })],
    unregistered: [unreg("old")],
    targets: { old: [{ ...TARGETS[0], storage_name: "old", target: "legacy" }] },
  });
  expect(await tab("old")).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByRole("button", { name: "legacy" })).toBeInTheDocument();
});

test("탭 집합 = 등록 ∪ 요약 ∪ 미등록(실패 기록만 남은 이름 포함) -- 미등록 탭은 구분선 뒤 「미등록」 표시", async () => {
  setup({
    storages: [st("cephfs-dms"), st("zz-new")],
    summary: [sum("cephfs-dms"), sum("zz-new", { target_count: 0, succeeded_scan_count: 0, last_scan_at: null }),
              sum("old-gpfs", { registered: false, source: "last_known", target_count: 3 })],
    unregistered: [unreg("old-gpfs", { target_count: 3 }),
                   unreg("ghost", { target_count: 0, succeeded_scan_count: 0, last_scan_at: null })],
  });
  await screen.findByRole("tab", { name: "old-gpfs 미등록 대상 3개" });
  expectTabNames(["cephfs-dms 대상 2개", "zz-new 대상 0개", "ghost 미등록 대상 0개", "old-gpfs 미등록 대상 3개"]);
  expect(screen.getByRole("tab", { name: "ghost 미등록 대상 0개" })).toBeInTheDocument();
  // 구분선은 하나(등록 | 미등록), 보조기기엔 숨김
  const sep = screen.getByRole("tablist").querySelectorAll("span[aria-hidden].w-px");
  expect(sep).toHaveLength(1);
  expect(sep[0].nextElementSibling).toBe(screen.getByRole("tab", { name: /^ghost/ }));
});

test("요약 도착 전엔 대상 수를 그리지 않는다(모름 ≠ 0) -- 등록 탭은 먼저 그려지고 패널은 자리표시", async () => {
  setup({ summary: "slow" });
  const t = await tab("cephfs-dms");
  expect(t).toHaveAccessibleName("cephfs-dms");                 // 「대상 0개」가 아니다
  expect(t).toHaveAttribute("aria-selected", "false");          // 기본 탭은 기록 여부를 알아야 고른다
  expect(screen.queryByRole("table")).toBeNull();
  expect(within(screen.getByRole("tabpanel")).getByText("불러오는 중…")).toBeInTheDocument();
});

test("요약을 못 받으면 등록 목록 ∪ scan 기록 이름으로 탭만 그린다(대상 수 모름) + 한 줄 알림", async () => {
  setup({ summary: "error", storages: [st("a-empty"), st("cephfs-dms")], scanStorages: ["cephfs-dms", "old-gpfs"],
          unregistered: [unreg("old-gpfs")] });
  // 대체 원천으로 탭이 그려지면 흐린 한 줄로 「건수 모름」만 -- 원문 오류(옛 API 의 SPA index.html 이면 파서 문장)는 안 보인다
  expect(await screen.findByText("스토리지별 대상 수를 불러오지 못해 건수 없이 표시합니다")).toBeInTheDocument();
  expect(screen.queryByText(/internal/)).toBeNull();
  // 기록 있는 등록 탭이 기본(scan 기록 이름으로 판정)
  await waitFor(() => expect(screen.getByRole("tab", { name: "cephfs-dms" })).toHaveAttribute("aria-selected", "true"));
  expect(screen.getByRole("tab", { name: "a-empty" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "old-gpfs 미등록 대상 1개" })).toBeInTheDocument();  // 미등록 목록은 규모를 안다
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
});

test("옛 API(요약 경로가 SPA index.html 200)여도 대체 탭 + 흐린 한 줄 -- 파서 문장을 그대로 띄우지 않는다", async () => {
  setup({ summary: "html", storages: [st("cephfs-dms")], scanStorages: ["cephfs-dms"] });
  expect(await screen.findByText("스토리지별 대상 수를 불러오지 못해 건수 없이 표시합니다")).toBeInTheDocument();
  expect(screen.queryByText(/Unexpected token|JSON/)).toBeNull();
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
});

test("요약 재조회로 대상 수가 바뀌어도 보던 기본 탭은 그대로(붙잡은 기본 탭)", async () => {
  const qc = setup({ storages: [st("a"), st("b")],
                     summary: [sum("a", { target_count: 0 }), sum("b")],
                     targets: { b: [{ ...TARGETS[0], storage_name: "b", target: "b-row" }] } });
  expect(await screen.findByRole("button", { name: "b-row" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /^b/ })).toHaveAttribute("aria-selected", "true");
  // 이제 a 에도 기록이 생겼다 -- 다시 계산하면 이름순 첫째(a)가 기본이지만 보던 b 를 바꾸지 않는다
  server.use(http.get("/api/admin/usage/storages", () =>
    HttpResponse.json({ window_limit: 10000, storages: [sum("a", { target_count: 3 }), sum("b")] })));
  await qc.invalidateQueries({ queryKey: ["usage-storages"] });
  await waitFor(() => expect(screen.getByRole("tab", { name: "a 대상 3개" })).toBeInTheDocument());
  expect(screen.getByRole("tab", { name: /^b/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("button", { name: "b-row" })).toBeInTheDocument();
});

test("받아 둔 요약이 있으면 재조회 실패에도 패널을 그대로 두고 그 사실만 말한다", async () => {
  const qc = setup();
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
  server.use(http.get("/api/admin/usage/storages", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
  await qc.invalidateQueries({ queryKey: ["usage-storages"] });
  expect(await screen.findByText(/스토리지별 요약을 새로 고치지 못했습니다/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "artifacts" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "cephfs-dms 대상 2개" })).toHaveAttribute("aria-selected", "true");
});

test("빈 이름(변조·구형 행) 탭: 「(이름 없음)」으로 보이고, 목록 요청은 storage= (빈 이름)로 나간다", async () => {
  const seen: (string | null)[] = [];
  setup({ storages: [st("cephfs-dms")],
          summary: [sum("cephfs-dms"), sum("", { registered: false, source: null, backend_type: null, mount_path: null,
                                                 target_count: 1 })],
          targets: { "cephfs-dms": TARGETS, "": [{ ...TARGETS[0], storage_name: "", target: "blank-row" }] },
          onTargets: (u) => seen.push(u.searchParams.get("storage")) });
  await userEvent.click(await screen.findByRole("tab", { name: "(이름 없음) 미등록 대상 1개" }));
  expect(await screen.findByRole("button", { name: "blank-row" })).toBeInTheDocument();
  expect(seen).toContain("");
  expect(loc().get("storage")).toBe("");
});

test("?storage 딥링크는 그 탭을 고르고 목록을 바로 그 스토리지로 부른다", async () => {
  const seen: (string | null)[] = [];
  setup({ path: "/admin/usage?storage=cephfs-b",
          storages: [st("cephfs-a"), st("cephfs-b")], summary: [sum("cephfs-a"), sum("cephfs-b")],
          targets: { "cephfs-b": [{ ...TARGETS[0], storage_name: "cephfs-b", target: "b-only" }] },
          onTargets: (u) => seen.push(u.searchParams.get("storage")) });
  expect(await screen.findByRole("button", { name: "b-only" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /^cephfs-b/ })).toHaveAttribute("aria-selected", "true");
  expect(seen).not.toContain("cephfs-a");
});

test("탭 전환: URL 은 {storage}(push) -- 펼친 target 은 버린다. 같은 탭 재클릭은 아무것도 바꾸지 않는다", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts",
          storages: [st("cephfs-dms"), st("other")], summary: [sum("cephfs-dms"), sum("other")],
          targets: { "cephfs-dms": TARGETS, other: [{ ...TARGETS[0], storage_name: "other", target: "artifacts" }] } });
  await screen.findByRole("region", { name: "cephfs-dms:artifacts 상세" });
  await userEvent.click(screen.getByRole("tab", { name: /^cephfs-dms/ }));
  expect(loc().get("target")).toBe("artifacts");
  await userEvent.click(screen.getByRole("tab", { name: /^other/ }));
  expect(loc().get("storage")).toBe("other");
  expect(loc().get("target")).toBeNull();
  // 같은 경로 이름이 다른 스토리지에 있어도 펼침이 따라가지 않는다
  const btn = await screen.findByRole("button", { name: "artifacts" });
  expect(btn).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("region", { name: /상세/ })).toBeNull();
});

test("행 접기는 storage 를 남긴다(기본 탭으로 튀지 않게) -- 기본 탭에서 펼치면 storage 가 URL 에 실린다", async () => {
  setup({ storages: [st("a-first"), st("cephfs-dms")],
          summary: [sum("a-first"), sum("cephfs-dms")],
          targets: { "a-first": [{ ...TARGETS[0], storage_name: "a-first", target: "x" }], "cephfs-dms": TARGETS } });
  await userEvent.click(await tab("cephfs-dms"));
  await userEvent.click(await screen.findByRole("button", { name: "team" }));
  expect(loc().get("storage")).toBe("cephfs-dms");
  expect(loc().get("target")).toBe("team");
  await userEvent.click(screen.getByRole("button", { name: "team" }));
  expect(loc().get("storage")).toBe("cephfs-dms");
  expect(loc().get("target")).toBeNull();
  expect(screen.getByRole("tab", { name: /^cephfs-dms/ })).toHaveAttribute("aria-selected", "true");
});

test("URL 의 storage 가 탭에 없으면(데이터 도착 후) 기본 탭 + 흐린 안내 한 줄, URL 은 replace 로 정리", async () => {
  setup({ path: "/admin/usage?storage=typo-name&target=x", storages: [st("cephfs-dms"), st("other")],
          summary: [sum("cephfs-dms"), sum("other")] });
  expect(await screen.findByText("‘typo-name’ 스토리지의 사용량 기록이 없습니다 — 기본 탭을 보여 줍니다"))
    .toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /^cephfs-dms/ })).toHaveAttribute("aria-selected", "true");
  await waitFor(() => expect(loc().get("storage")).toBeNull());
  expect(loc().get("target")).toBeNull();
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
  // 기본 탭(URL 에 없음)을 누르면 URL 에 적힌다 -- 고른 것이니 안내도 거둔다
  await userEvent.click(screen.getByRole("tab", { name: /^cephfs-dms/ }));
  expect(loc().get("storage")).toBe("cephfs-dms");
  expect(screen.queryByText(/typo-name/)).toBeNull();
});

test("키보드(수동 활성): ←/→/Home/End 는 포커스만, Enter 가 고른다 -- Tab 으로 닿는 탭은 하나", async () => {
  const seen: (string | null)[] = [];
  setup({ storages: [st("a"), st("b"), st("c")], summary: [sum("a"), sum("b"), sum("c")],
          onTargets: (u) => seen.push(u.searchParams.get("storage")) });
  const a = await tab("a");
  await waitFor(() => expect(a).toHaveAttribute("aria-selected", "true"));
  const tabs = screen.getAllByRole("tab");
  expect(tabs.map((t) => t.tabIndex)).toEqual([0, -1, -1]);
  a.focus();
  await userEvent.keyboard("{ArrowRight}");
  expect(tabs[1]).toHaveFocus();
  expect(tabs[1]).toHaveAttribute("aria-selected", "false");     // 포커스만 -- 요청도 없다
  expect(seen).not.toContain("b");
  await userEvent.keyboard("{End}");
  expect(tabs[2]).toHaveFocus();
  await userEvent.keyboard("{ArrowRight}");                       // 끝에서 순환
  expect(tabs[0]).toHaveFocus();
  await userEvent.keyboard("{ArrowLeft}");
  expect(tabs[2]).toHaveFocus();
  await userEvent.keyboard("{Home}{ArrowRight}{Enter}");
  expect(tabs[1]).toHaveAttribute("aria-selected", "true");
  expect(loc().get("storage")).toBe("b");
  await waitFor(() => expect(seen).toContain("b"));
  expect(screen.getAllByRole("tab").map((t) => t.tabIndex)).toEqual([-1, 0, -1]);
});

test("탭을 바꾸는 동안 이전 스토리지의 행을 새 탭 아래 보이지 않는다(자리표시는 같은 스토리지 안에서만)", async () => {
  setup({ storages: [st("cephfs-dms"), st("slow")], summary: [sum("cephfs-dms"), sum("slow")], slowTargets: "slow" });
  await screen.findByRole("button", { name: "artifacts" });
  await userEvent.click(screen.getByRole("tab", { name: /^slow/ }));
  expect(screen.queryByRole("button", { name: "artifacts" })).toBeNull();
  expect(within(screen.getByRole("tabpanel")).getByText("불러오는 중…")).toBeInTheDocument();
});

test("기록 0개 등록 스토리지: 빈 상태 안내", async () => {
  setup({ path: "/admin/usage?storage=fresh", storages: [st("cephfs-dms"), st("fresh")],
          summary: [sum("cephfs-dms"), sum("fresh", { target_count: 0, succeeded_scan_count: 0, last_scan_at: null })] });
  expect(await screen.findByText("이 스토리지에는 아직 성공한 scan 이 없습니다")).toBeInTheDocument();
  expect(screen.getByRole("tabpanel")).toHaveTextContent("대상 0개");
  expect(screen.queryByRole("table")).toBeNull();
  // 기록이 없으면 검색·CSV 는 할 일이 없다 -- 빈 상태 안내만
  expect(screen.queryByLabelText("경로 검색")).toBeNull();
  expect(screen.queryByRole("button", { name: "CSV 내보내기" })).toBeNull();
});

test("스토리지도 기록도 없으면 등록 안내", async () => {
  setup({ storages: [], summary: [] });
  expect(await screen.findByText("등록된 스토리지가 없습니다")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "스토리지 화면으로" })).toHaveAttribute("href", "/admin/storages");
});

// ---- 미등록 탭 ----

test("미등록 탭: 패널 맨 위 경고 띠(마지막 설정·규모·「기록 정리」) + 같은 표, 메타 줄 대신", async () => {
  setup({ path: "/admin/usage?storage=old-gpfs",
          summary: [sum("cephfs-dms"), sum("old-gpfs", { registered: false, source: "last_known", target_count: 3 })],
          unregistered: [unreg("old-gpfs", { target_count: 3, succeeded_scan_count: 5, single_count: 4, request_count: 6,
                                            batches: [{ batch_id: "b".repeat(32), request_count: 2 }] })],
          targets: { "old-gpfs": [{ ...TARGETS[0], storage_name: "old-gpfs", target: "proj" }] } });
  const note = await screen.findByRole("note", { name: "미등록 스토리지" });
  expect(note).toHaveTextContent("스토리지 목록에서 삭제된 스토리지의 기록입니다");
  expect(note).toHaveTextContent("마지막 설정 gpfs · /old");
  expect(note).toHaveTextContent("대상 3개 · 성공 scan 5개 · 정리할 작업 6개(배치 1개 포함) · 최근 스캔 200일 전");
  expect(within(note).getByRole("button", { name: "old-gpfs 기록 정리" })).toBeEnabled();
  expect(await screen.findByRole("button", { name: "proj" })).toBeInTheDocument();
  // 행마다 붙던 「미등록」 배지는 없다 -- 탭이 말한다
  expect(within(screen.getByRole("table")).queryByText("미등록")).toBeNull();
});

test("미등록 탭: 실패·취소 기록만 남은 이름은 띠 + 「성공한 scan 기록이 없습니다」", async () => {
  setup({ path: "/admin/usage?storage=ghost",
          unregistered: [unreg("ghost", { target_count: 0, succeeded_scan_count: 0, last_scan_at: null,
                                          request_count: 2, single_count: 2 })] });
  expect(await screen.findByText("성공한 scan 기록이 없습니다")).toBeInTheDocument();
  expect(screen.getByRole("note", { name: "미등록 스토리지" })).toHaveTextContent("정리할 작업 2개");
});

// ---- 펼침 상세 ----

test("타깃 선택 → 한 줄 수치·추이 차트·미상 고지·접힌 스캔 이력", async () => {
  setup();
  await userEvent.click(await screen.findByRole("button", { name: "artifacts" }));
  // 수치 줄: 최신 3.0 KiB, 직전(1.0 KiB) 대비 +2.0 KiB, hot 100%(atime 첫 버킷 전부). 값은 dt 의 다음 형제 dd.
  const region = await screen.findByRole("region", { name: "cephfs-dms:artifacts 상세" });
  const stat = async (label: string) => (await within(region).findByText(label, { selector: "dt" })).nextElementSibling;
  expect(await stat("최신 실 사용량")).toHaveTextContent("3.0 KiB");
  expect(await stat("직전 스캔 대비")).toHaveTextContent("+2.0 KiB");
  expect(await stat("hot 비율(180일)")).toHaveTextContent("100%");
  expect(await stat("스캔 이력")).toHaveTextContent("3건");
  // 차트: 용량 아는 2점만(j2 는 미상), 고지 문구가 사실을 말한다
  expect(screen.getByRole("group", { name: "실 사용량 추이" })).toBeInTheDocument();
  expect(screen.getByText(/용량 미상 1건은 차트에서 제외/)).toBeInTheDocument();
  expect(screen.getByText(/리포트를 읽지 못한 스캔 1건 제외/)).toBeInTheDocument();
  // 스캔 이력 표는 <details> 기본 닫힘 -- 요약 「스캔 이력 3건」, 열면 최신이 위(reverse)·요청 상세 링크
  const summary = screen.getByText("스캔 이력 3건");
  const details = summary.closest("details")!;
  expect(details.open).toBe(false);
  await userEvent.click(summary);
  expect(details.open).toBe(true);
  expect(within(details).getByRole("link", { name: "r3" })).toHaveAttribute("href", "/jobs/r3");
  expect(within(details).getAllByText("alice")).toHaveLength(HISTORY.points.length);
  // 상세는 **선택한 행 바로 아래 펼침 행**에 있다(2026-09-14)
  expect(screen.getByRole("button", { name: "artifacts" })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("region", { name: "cephfs-dms:artifacts 상세" })).toBeInTheDocument();
});

test("펼침은 한 번에 하나: 재클릭 = 접기, 다른 행 클릭 = 펼침 이동, 행 바로 아래에 렌더", async () => {
  setup();
  const artifacts = await screen.findByRole("button", { name: "artifacts" });
  const team = screen.getByRole("button", { name: "team" });
  expect(artifacts).toHaveAttribute("aria-expanded", "false");
  // 펼친 상세 region 만 센다 -- 패널 맨 위의 「이 스토리지 합계」(2026-10-21)도 이름 붙은 구역이다.
  const DETAIL = { name: / 상세$/ };
  expect(screen.queryByRole("region", DETAIL)).toBeNull();
  await userEvent.click(artifacts);
  const region = await screen.findByRole("region", { name: "cephfs-dms:artifacts 상세" });
  expect(artifacts.closest("tr")!.nextElementSibling).toContainElement(region);
  expect(team).toHaveAttribute("aria-expanded", "false");
  await userEvent.click(team);
  await screen.findByRole("region", { name: "cephfs-dms:team 상세" });
  expect(screen.getAllByRole("region", DETAIL)).toHaveLength(1);
  expect(screen.getByRole("button", { name: "artifacts" })).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByRole("button", { name: "team" })).toHaveAttribute("aria-expanded", "true");
  await userEvent.click(screen.getByRole("button", { name: "team" }));
  expect(screen.queryByRole("region", DETAIL)).toBeNull();
  expect(screen.getByRole("button", { name: "team" })).toHaveAttribute("aria-expanded", "false");
});

test("리포트를 못 읽은 스캔만 있으면 「scan 이 없다」가 아니라 읽지 못했다고 말한다", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts",
          history: { ...HISTORY, points: [], skipped_unreadable: 2 } });
  expect(await screen.findByText("성공 scan 2건의 리포트를 읽지 못해 표시할 이력이 없습니다")).toBeInTheDocument();
});

test("딥링크 타깃이 지금 목록에 없으면 패널 안 표 아래에 표시한다", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts", targets: { "cephfs-dms": [TARGETS[1]] } });
  expect(await screen.findByText(/지금 목록\(검색·상위 100개\)에 없어 아래에 표시합니다/)).toBeInTheDocument();
  const region = screen.getByRole("region", { name: "cephfs-dms:artifacts 상세" });
  expect(screen.getByRole("tabpanel")).toContainElement(region);
  expect(await within(region).findByText("최신 실 사용량")).toBeInTheDocument();
  expect(within(region).getByRole("heading", { name: "artifacts" })).toBeInTheDocument();
});

test("온도 추이: atime 기본 + mtime 세그먼트(aria-pressed)", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts" });
  expect(await screen.findByRole("img", { name: "데이터 온도 추이(atime)" })).toBeInTheDocument();
  const axis = screen.getByRole("group", { name: "온도 축" });
  expect(within(axis).getByRole("button", { name: "atime" })).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(within(axis).getByRole("button", { name: "mtime" }));
  expect(screen.getByRole("img", { name: "데이터 온도 추이(mtime)" })).toBeInTheDocument();
  expect(within(axis).getByRole("button", { name: "mtime" })).toHaveAttribute("aria-pressed", "true");
  // 긴 캡션은 제목 옆 설명(HelpTip) -- 본문에 상시로 깔지 않는다
  expect(screen.getByRole("button", { name: "데이터 온도 추이 설명" })).toBeInTheDocument();
  // atime 축의 근사 이유는 스토리지·마운트(2026-10-27 d192, 괴리 [C3] -- 테스트베드 CephFS 는 읽어도 atime 이 그대로였다)
  await userEvent.click(within(axis).getByRole("button", { name: "atime" }));
  const help = screen.getByRole("button", { name: "데이터 온도 추이 설명" });
  expect(help).toHaveAccessibleDescription(/읽기가 atime 에 반영되는지는 마운트·스토리지에 따라 달라 근사치입니다/);
  expect(help).toHaveAccessibleDescription(/반영하지 않는 곳에선 최근에 쓴 데이터가 hot 으로 보입니다/);
  // relatime 은 읽기를 하루 단위로 반영한다 -- 「반영 안 하는」 쪽(noatime·일부 CephFS)에 넣지 않는다(2026-10-28 리뷰
  // portal-batch-27: 예전 「(relatime·noatime, CephFS 등)」은 relatime 스토리지에서 open_noatime 을 꺼도 된다고 읽혔다).
  const rule = "리눅스 기본 relatime 은 읽기를 파일마다 많아야 하루에 한 번 반영하고, noatime 마운트나 일부 CephFS 는 반영하지 않습니다";
  expect(help).toHaveAccessibleDescription(new RegExp(rule));
  const hotHelp = screen.getByRole("button", { name: "hot 비율 설명" });
  expect(hotHelp).toHaveAccessibleDescription(new RegExp(rule));
  expect(hotHelp).toHaveAccessibleDescription(
    /읽기가 atime 을 바꾸지 않는 스토리지\(noatime 마운트, 일부 CephFS 등\)에선 최근에 쓴 데이터 비중에 가까운 근사치/);
  for (const el of [help, hotHelp]) expect(el).not.toHaveAccessibleDescription(/relatime·noatime/);
});

test("온도 추이 열 호버 즉시 툴팁(시간·요청자·용량)", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts" });
  const chart = await screen.findByRole("img", { name: "데이터 온도 추이(atime)" });
  expect(screen.queryByRole("tooltip")).toBeNull();
  const col = within(chart).getAllByRole("button")[0];
  await userEvent.hover(col);
  const tip = screen.getByRole("tooltip");
  expect(tip).toHaveTextContent("2026-08-22 21:00:00 KST");     // j1 generated_at_epoch 1787400000
  expect(tip).toHaveTextContent("요청자alice");
  expect(tip).toHaveTextContent("실 사용량1.0 KiB");
  expect(tip).toHaveTextContent("파일 수7개");
  expect(tip).toHaveTextContent("hot 비율(atime 180일)100%");
  const legend = within(tip).getByLabelText("구간별 용량");
  expect(legend).toHaveTextContent("1일 이내1.0 KiB100%");
  expect(legend).toHaveTextContent("1~2일0 B0%");
  expect(tip).toHaveTextContent("빨강 → 파랑 = 최근 → 오래됨");
  await userEvent.unhover(col);
  expect(screen.queryByRole("tooltip")).toBeNull();
});

test("온도 툴팁: mtime 축이면 그 축의 180일 이내 비중과 atime hot 비율을 따로 말한다", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts" });
  await screen.findByRole("img", { name: "데이터 온도 추이(atime)" });
  await userEvent.click(screen.getByRole("button", { name: "mtime" }));
  const chart = await screen.findByRole("img", { name: "데이터 온도 추이(mtime)" });
  await userEvent.hover(within(chart).getAllByRole("button")[0]);
  const tip = screen.getByRole("tooltip");
  expect(tip).toHaveTextContent("hot 비율(atime 180일)100%");
  expect(tip).toHaveTextContent("mtime 180일 이내100%");
});

test("atime 이 없는 이력은 첫 가용 축(mtime)으로 자동 대체 -- '분포 없음' 거짓 방지", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts",
          history: { ...HISTORY, points: HISTORY.points.map((p) => ({ ...p, time_histograms: { mtime: p.time_histograms.mtime } })) } });
  expect(await screen.findByRole("img", { name: "데이터 온도 추이(mtime)" })).toBeInTheDocument();
  expect(screen.queryByText("이 축의 온도 분포가 있는 스캔이 없습니다")).toBeNull();
});

test("표시 창: 「최근 30건」이 늘 눌려 있고(상시 창 안내), window_full 이면 「이전 이력 있음」", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts",
          history: { ...HISTORY, window_limit: 30, window_full: true } });
  expect(await screen.findByText("이전 이력 있음")).toBeInTheDocument();
  const win = screen.getByRole("group", { name: "이력 표시 창" });
  expect(within(win).getByRole("button", { name: "최근 30건" })).toHaveAttribute("aria-pressed", "true");
});

test("표시 창 선택: 최근 60건 클릭 -> limit=60 으로 재조회", async () => {
  const limits: (string | null)[] = [];
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts", history: { ...HISTORY, window_full: false },
          onHistory: (u) => limits.push(u.searchParams.get("limit")) });
  await screen.findByText("최신 실 사용량");
  expect(limits).toEqual(["30"]);
  expect(screen.queryByText("이전 이력 있음")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "최근 60건" }));
  await waitFor(() => expect(limits).toEqual(["30", "60"]));
  expect(screen.getByRole("button", { name: "최근 60건" })).toHaveAttribute("aria-pressed", "true");
});

// ---- 목록 ----

const LIST = [
  { storage_name: "cephfs-dms", target: "fresh", scan_count: 2, last_scan_at: isoAgo(2),
    latest: point({ total_bytes: 3 * 1024 * 1024, summary: { total_files: 12345 },
                    time_histograms: { atime: [
                      { bucket: "[0d,1d]", min_age_days: 0, max_age_days: 1, bytes: 30 },
                      { bucket: "[181d,365d]", min_age_days: 181, max_age_days: 365, bytes: 70 }] } }) },
  { storage_name: "cephfs-dms", target: "stale", scan_count: 1, last_scan_at: isoAgo(45), latest: null },
  { storage_name: "cephfs-dms", target: "ancient", scan_count: 1, last_scan_at: isoAgo(100),
    latest: point({ total_bytes: null, summary: {}, time_histograms: {}, report_readable: false }) },
];
const list = (rows: unknown[], o: Setup = {}) => setup({ targets: { "cephfs-dms": rows }, ...o });

test("경로 검색: storage 와 path 가 함께 나가고(디바운스 후), 없으면 「검색 지우기」", async () => {
  let last: URL | null = null;
  setup({ onTargets: (u) => { last = u; }, filterPath: true });
  await screen.findByRole("button", { name: "artifacts" });
  await userEvent.type(screen.getByLabelText("경로 검색"), "zzz");
  await waitFor(() => expect(last!.searchParams.get("path")).toBe("zzz"));
  expect(last!.searchParams.get("storage")).toBe("cephfs-dms");
  expect(await screen.findByText("‘zzz’ 검색 결과가 없습니다")).toBeInTheDocument();
  // 검색 중의 빈 결과는 검색창을 남긴다(기록이 없는 스토리지의 빈 상태와 다르다)
  expect(screen.getByLabelText("경로 검색")).toHaveValue("zzz");
  await userEvent.click(screen.getByRole("button", { name: "검색 지우기" }));
  expect(screen.getByLabelText("경로 검색")).toHaveValue("");
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
});

test("검색·정렬은 탭을 바꿔도 남고, 새 탭에서도 그 스토리지 안에서만 걸린다", async () => {
  let last: URL | null = null;
  setup({ storages: [st("cephfs-dms"), st("mirror")], summary: [sum("cephfs-dms"), sum("mirror")],
          targets: { "cephfs-dms": LIST, mirror: LIST.map((r) => ({ ...r, storage_name: "mirror" })) },
          onTargets: (u) => { last = u; } });
  await screen.findByRole("button", { name: "fresh" });
  await userEvent.type(screen.getByLabelText("경로 검색"), "proj");
  await userEvent.click(screen.getByRole("button", { name: "실 사용량 정렬" }));
  await waitFor(() => expect(last!.searchParams.get("path")).toBe("proj"));
  await userEvent.click(screen.getByRole("tab", { name: /^mirror/ }));
  await waitFor(() => expect(last!.searchParams.get("storage")).toBe("mirror"));
  expect(last!.searchParams.get("path")).toBe("proj");
  expect(last!.searchParams.get("sort")).toBe("bytes");
  expect(screen.getByLabelText("경로 검색")).toHaveValue("proj");
});

test("건수 문구: 「N개」 · 검색 중 「‘q’ N개」", async () => {
  list(LIST);
  await screen.findByRole("button", { name: "fresh" });
  expect(within(screen.getByRole("tabpanel")).getByText("3개")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("경로 검색"), "fr");
  expect(await screen.findByText("‘fr’ 3개")).toBeInTheDocument();
});

test("목록 컬럼: 실 사용량·파일 수·hot 비율(최신 scan) -- 모름은 —", async () => {
  list(LIST);
  const row = (await screen.findByRole("button", { name: "fresh" })).closest("tr")!;
  expect(row).toHaveTextContent("3.0 MiB");
  expect(row).toHaveTextContent("12,345");
  expect(row).toHaveTextContent("30%");                         // atime ≤180일 30/(30+70)
  for (const t of ["stale", "ancient"]) {
    const r = screen.getByRole("button", { name: t }).closest("tr")!;
    expect(within(r).getAllByText("—")).toHaveLength(3);         // 용량·파일 수·hot 모두 모름(0 아님)
  }
  // 머리글은 정렬 버튼이다(2026-10-19). hot 비율의 정의는 옆 설명(HelpTip).
  expect(screen.getByRole("button", { name: "hot 비율 정렬" }).closest("th")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "hot 비율 설명" })).toBeInTheDocument();
});

test("최근 스캔 경과: N일 전 + 30일 주의색(attn) · 90일 빨강", async () => {
  list(LIST);
  await screen.findByRole("button", { name: "fresh" });
  const ago = (t: string) =>
    within(screen.getByRole("button", { name: t }).closest("tr")!).getByLabelText("최근 스캔 경과");
  expect(ago("fresh")).toHaveTextContent("2일 전");
  expect(ago("fresh").className).not.toMatch(/text-attn|text-bad/);
  expect(ago("stale")).toHaveTextContent("45일 전");
  expect(ago("stale").className).toMatch(/text-attn/);
  expect(ago("ancient")).toHaveTextContent("100일 전");
  expect(ago("ancient").className).toMatch(/text-bad/);
  expect(staleClass(29.9)).not.toMatch(/attn/);
  expect(staleClass(30)).toMatch(/text-attn/);
  expect(staleClass(90)).toMatch(/text-bad/);
  expect(staleClass(null)).toBe("text-muted");
});

test("최근 스캔 정렬 토글: 서버 order 파라미터(desc ↔ asc) + aria-sort", async () => {
  const orders: (string | null)[] = [];
  list(LIST, { onTargets: (u) => orders.push(u.searchParams.get("order")) });
  const sortBtn = await screen.findByRole("button", { name: "최근 스캔 정렬: 최신순" });
  expect(sortBtn.closest("th")).toHaveAttribute("aria-sort", "descending");
  await userEvent.click(sortBtn);
  await waitFor(() => expect(orders[orders.length - 1]).toBe("asc"));
  expect(screen.getByRole("button", { name: "최근 스캔 정렬: 오래된순" }).closest("th"))
    .toHaveAttribute("aria-sort", "ascending");
  expect(orders[0]).toBe("desc");
});

test("열 정렬(2026-10-19): 머리글 -- 서버 sort·order(storage 함께), 같은 열 재클릭 = 방향 전환, 지표 정렬이면 모름 안내", async () => {
  const seen: { storage: string | null; sort: string | null; order: string | null; limit: string | null }[] = [];
  list(LIST, { onTargets: (u) => seen.push({ storage: u.searchParams.get("storage"), sort: u.searchParams.get("sort"),
                                             order: u.searchParams.get("order"), limit: u.searchParams.get("limit") }) });
  await screen.findByRole("button", { name: "최근 스캔 정렬: 최신순" });
  expect(seen[0]).toEqual({ storage: "cephfs-dms", sort: "last_scan", order: "desc", limit: "100" });
  expect(screen.queryByText("값 모름(—)은 맨 뒤")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "실 사용량 정렬" }));
  await waitFor(() => expect(seen[seen.length - 1]).toMatchObject({ storage: "cephfs-dms", sort: "bytes", order: "desc" }));
  const bytesBtn = screen.getByRole("button", { name: "실 사용량 정렬: 큰 순" });
  expect(bytesBtn.closest("th")).toHaveAttribute("aria-sort", "descending");
  expect(screen.getByRole("button", { name: "최근 스캔 정렬" }).closest("th")).not.toHaveAttribute("aria-sort");
  expect(screen.getByText("값 모름(—)은 맨 뒤")).toBeInTheDocument();
  // 대상 수(2)가 창(10,000)을 넘지 않으면 창 안내는 없다
  expect(screen.queryByText(/개 안에서 정렬/)).toBeNull();
  await userEvent.click(bytesBtn);
  await waitFor(() => expect(seen[seen.length - 1]).toMatchObject({ sort: "bytes", order: "asc" }));
  expect(screen.getByRole("button", { name: "실 사용량 정렬: 작은 순" }).closest("th"))
    .toHaveAttribute("aria-sort", "ascending");
  for (const [label, sort, word] of [["파일 수", "files", "많은 순"], ["hot 비율", "hot", "높은 순"],
                                     ["스캔 횟수", "scans", "많은 순"]] as const) {
    await userEvent.click(screen.getByRole("button", { name: `${label} 정렬` }));
    await waitFor(() => expect(seen[seen.length - 1]).toMatchObject({ sort, order: "desc" }));
    expect(screen.getByRole("button", { name: `${label} 정렬: ${word}` })).toBeInTheDocument();
  }
  await userEvent.click(screen.getByRole("button", { name: "최근 스캔 정렬" }));
  expect(await screen.findByRole("button", { name: "최근 스캔 정렬: 최신순" })).toBeInTheDocument();
  expect(screen.queryByText("값 모름(—)은 맨 뒤")).toBeNull();
});

test("지표 정렬 창 안내: 그 스토리지의 대상 수가 서버 window_limit 을 넘을 때만(숫자는 서버 값)", async () => {
  list(LIST, { summary: [sum("cephfs-dms", { target_count: 12345 })], windowLimit: 10000 });
  await screen.findByRole("button", { name: "fresh" });
  expect(screen.queryByText(/개 안에서 정렬/)).toBeNull();          // 최근 스캔 정렬(SQL)엔 창이 없다
  await userEvent.click(screen.getByRole("button", { name: "hot 비율 정렬" }));
  expect(await screen.findByText("최근 스캔된 10,000개 안에서 정렬")).toBeInTheDocument();
});

test("지표 정렬 창 안내 + 경로 검색: 결과가 목록 상한보다 적으면 안내 없음, 많으면 조건부로 말한다", async () => {
  const many = Array.from({ length: 100 }, (_, i) => ({ ...(LIST[0] as object), target: `t${i}` }));
  list([...many, { ...(LIST[0] as object), target: "solo" }],
       { summary: [sum("cephfs-dms", { target_count: 12345 })], windowLimit: 10000, filterPath: true });
  await userEvent.click(await screen.findByRole("button", { name: "hot 비율 정렬" }));
  expect(await screen.findByText("최근 스캔된 10,000개 안에서 정렬")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("경로 검색"), "solo");
  expect(await screen.findByRole("button", { name: "solo" })).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByText(/개 안에서 정렬/)).toBeNull());
  await userEvent.clear(screen.getByLabelText("경로 검색"));
  await userEvent.type(screen.getByLabelText("경로 검색"), "t");
  expect(await screen.findByText("검색 결과가 10,000개를 넘으면 최근 스캔된 10,000개 안에서 정렬")).toBeInTheDocument();
});

test("목록이 상한(100)을 채우면 「상위 100개 표시 · 전체 N개」(전체 = 요약의 대상 수)", async () => {
  const many = Array.from({ length: 100 }, (_, i) => ({ ...(LIST[0] as object), target: `t${i}` }));
  list(many, { summary: [sum("cephfs-dms", { target_count: 12345 })] });
  expect(await screen.findByText("상위 100개 표시 · 전체 12,345개")).toBeInTheDocument();
});

test("최신 리포트를 못 읽은 행: 용량은 DB 기록값임을 표시, 파일 수·hot 은 —", async () => {
  list([{ storage_name: "cephfs-dms", target: "dbonly", scan_count: 1, last_scan_at: isoAgo(1),
          latest: point({ total_bytes: 2048, summary: {}, time_histograms: {}, report_readable: false }) }]);
  const row = (await screen.findByRole("button", { name: "dbonly" })).closest("tr")!;
  expect(row).toHaveTextContent("2.0 KiB(DB)");
  expect(within(row).getAllByText("—")).toHaveLength(2);
});

// ---- CSV ----

test("CSV 내보내기: 지금 탭의 스토리지·정렬로 export 를 받아 항목당 한 줄, 파일 이름에 스토리지", async () => {
  vi.mocked(downloadCsv).mockClear();
  let exportUrl: URL | null = null;
  const exportBody = {
    generated_at: "2026-10-02T00:00:00Z", count: 1, truncated: false,
    rows: [{ ...LIST[0], first_scan_at: isoAgo(10),
             previous: point({ job_id: "j0", total_bytes: 2 * 1024 * 1024 }) }],
  };
  list(LIST, { onExport: (u) => { exportUrl = u; return exportBody; } });
  await screen.findByRole("button", { name: "fresh" });
  await userEvent.click(screen.getByRole("button", { name: /최근 스캔 정렬/ }));       // asc 로 바꾼 뒤 내보내기
  await userEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
  await waitFor(() => expect(downloadCsv).toHaveBeenCalledTimes(1));
  expect(exportUrl!.searchParams.get("storage")).toBe("cephfs-dms");
  expect(exportUrl!.searchParams.get("order")).toBe("asc");
  const [filename, text] = vi.mocked(downloadCsv).mock.calls[0];
  expect(filename).toMatch(/^dms-usage-cephfs-dms-\d{8}-\d{4}\.csv$/);
  const [header, line] = (text as string).split("\r\n");
  const cols = header.split(",");
  const val = (c: string) => line.split(",")[cols.indexOf(c)];
  expect(val("abs_path")).toBe("/cephfs/dms/fresh");
  expect(val("total_bytes")).toBe(String(3 * 1024 * 1024));
  expect(val("delta_bytes_vs_previous")).toBe(String(1024 * 1024));
  expect(val("hot_ratio_atime_180d_pct")).toBe("30");
  expect(val("total_files")).toBe("12345");
  expect(val("atime_bytes_181d-365d")).toBe("70");
  expect(await screen.findByRole("status")).toHaveTextContent("1개 항목을 내보냈습니다");
});

test("CSV 내보내기: 서버 상한을 넘으면 잘렸다고 말한다(경로 검색으로 좁히라고)", async () => {
  list(LIST, { onExport: () => ({ generated_at: "", count: 10000, truncated: true, rows: [LIST[0]] }) });
  await screen.findByRole("button", { name: "fresh" });
  await userEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
  expect(await screen.findByRole("status")).toHaveTextContent(
    "상한 10,000개까지만 내보냈습니다 — 경로 검색으로 범위를 좁히세요");
});

test("CSV 내보내기는 지금 열 정렬(sort·order)과 경로 검색을 그대로 싣는다", async () => {
  const exportUrls: URL[] = [];
  list(LIST, { onExport: (u) => { exportUrls.push(u); return undefined; } });
  await screen.findByRole("button", { name: "fresh" });
  await userEvent.click(screen.getByRole("button", { name: "hot 비율 정렬" }));
  await userEvent.type(screen.getByLabelText("경로 검색"), "fr");
  await screen.findByText("‘fr’ 3개");
  await userEvent.click(screen.getByRole("button", { name: /CSV 내보내기/ }));
  await waitFor(() => expect(exportUrls).toHaveLength(1));
  expect(exportUrls[0].searchParams.get("storage")).toBe("cephfs-dms");
  expect(exportUrls[0].searchParams.get("path")).toBe("fr");
  expect(exportUrls[0].searchParams.get("sort")).toBe("hot");
  expect(exportUrls[0].searchParams.get("order")).toBe("desc");
});

// ---- 파일 크기 분포(2026-10-21 「최근 스캔 기준 파일의 사이즈별 정보」) ----

const detailOf = (target: string) => screen.findByRole("region", { name: `cephfs-dms:${target} 상세` });
// 상세의 분포: 이력의 다른 포인트(이전 scan)에 다른 분포를 심어, 기준이 무엇인지 가려낸다.
const OLDER_SIZES = { ...HISTORY, points: HISTORY.points.map((p) => ({ ...p, file_size_histogram: sizeHist(pad10([1])) })) };

test("펼친 상세: 「최신 scan」 블록 = 수치 줄 | 파일 크기 분포 -- 기준은 목록 행의 최신 scan(이력의 다른 포인트가 아님)", async () => {
  setup({ targets: { "cephfs-dms": [{ ...TARGETS[0], latest: point({ file_size_histogram: sizeHist(pad10([22, 0, 0, 4])) }) },
                                    TARGETS[1]] },
          history: OLDER_SIZES });
  await userEvent.click(await screen.findByRole("button", { name: "artifacts" }));
  const region = await detailOf("artifacts");
  const dist = await within(region).findByRole("list", { name: "파일 크기 분포" });
  expect(within(dist).getAllByRole("listitem")).toHaveLength(10);              // 0개 구간도 줄로 남는다
  expect(within(dist).getByText("4 KiB 이하 22개 85%")).toBeInTheDocument();
  expect(within(dist).getByText("1–16 MiB 4개 15%")).toBeInTheDocument();
  expect(within(dist).getByText("4 TiB 초과 0개 0%")).toBeInTheDocument();
  expect(within(dist).queryByText(/4 KiB 이하 1개/)).toBeNull();               // 이력(이전 scan)의 분포가 아니다
  expect(within(region).getByText("최신 scan · 파일 26개")).toBeInTheDocument();
  expect(within(region).getByRole("button", { name: "파일 크기 분포 설명" })).toBeInTheDocument();
  // 수치 줄은 그대로 같은 블록에 있다
  expect(within(region).getByRole("list", { name: "파일 크기 분포" })).toBeInTheDocument();
  expect(within(region).getByText("최신 실 사용량", { selector: "dt" })).toBeInTheDocument();
});

test("목록 행에 최신 정보가 없으면(딥링크 폴백 포함) 이력에서 시각이 가장 늦은 포인트의 분포(서버 순서가 아니다)", async () => {
  const pts = [
    point({ job_id: "j1", request_id: "r1", generated_at_epoch: 1787400000, file_size_histogram: sizeHist(pad10([1])) }),
    point({ job_id: "j3", request_id: "r3", generated_at_epoch: 1787500000, file_size_histogram: sizeHist(pad10([5, 5])) }),
    point({ job_id: "j2", request_id: "r2", generated_at_epoch: 1787450000, file_size_histogram: sizeHist(pad10([9])) }),
  ];
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts", history: { ...HISTORY, points: pts } });
  const region = await detailOf("artifacts");
  expect(await within(region).findByText("4 KiB 이하 5개 50%")).toBeInTheDocument();
  expect(within(region).getByText("최신 scan · 파일 10개")).toBeInTheDocument();
});

test("분포 모름: 최신 scan 리포트를 못 읽었으면 그렇게 말하고(옛 scan 분포로 대신하지 않는다), 구형 리포트면 「기록 없음」", async () => {
  setup({ targets: { "cephfs-dms": [
            { ...TARGETS[0], latest: point({ report_readable: false, summary: {}, time_histograms: {}, file_size_histogram: [] }) },
            { ...TARGETS[1], latest: point({ file_size_histogram: [] }) }] },
          history: OLDER_SIZES });
  await userEvent.click(await screen.findByRole("button", { name: "artifacts" }));
  const region = await detailOf("artifacts");
  expect(await within(region).findByText("최신 scan 의 리포트를 읽지 못해 크기 분포를 알 수 없습니다")).toBeInTheDocument();
  expect(within(region).queryByRole("list", { name: "파일 크기 분포" })).toBeNull();
  expect(within(region).queryByText(/최신 scan · 파일/)).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "team" }));
  const team = await detailOf("team");
  expect(await within(team).findByText("크기 분포 기록이 없습니다(구형 리포트)")).toBeInTheDocument();
  expect(within(team).queryByRole("list", { name: "파일 크기 분포" })).toBeNull();     // 0 막대 10줄로 그리지 않는다
});

// ---- 이 스토리지 합계(2026-10-21 「스토리지별 사용량 및 정보들의 총합」) ----

const totalsBox = () => screen.findByRole("region", { name: "이 스토리지 합계" });
const valueOf = (box: HTMLElement, label: string) => within(box).getByText(label, { selector: "dt" }).nextElementSibling;

test("이 스토리지 합계: 실 사용량·파일·디렉터리·hot·df + 크기 분포 + 각주(최상위 기준 · 하위 제외 · scan 시점 범위)", async () => {
  const seen: URL[] = [];
  setup({ onTotals: (u) => seen.push(u) });
  const box = await totalsBox();
  expect(await within(box).findByText("17.0 MiB")).toBeInTheDocument();
  expect(valueOf(box, "실 사용량")).toHaveTextContent(/^17\.0 MiB$/);
  expect(valueOf(box, "실 사용량")).toHaveAttribute("title", "17,786,075 B");
  expect(valueOf(box, "파일")).toHaveTextContent(/^662개$/);
  expect(valueOf(box, "디렉터리")).toHaveTextContent(/^243개$/);
  expect(valueOf(box, "hot 비율(180일)")).toHaveTextContent(/^100%$/);
  expect(valueOf(box, "파일시스템 사용(df)")).toHaveTextContent("36.0 MiB/ 22.8 GiB0.2%");
  const dist = within(box).getByRole("list", { name: "파일 크기 분포" });
  expect(within(dist).getByText("4 KiB 이하 590개 89%")).toBeInTheDocument();
  expect(within(dist).getByText("4–64 KiB 66개 10.0%")).toBeInTheDocument();
  expect(box).toHaveTextContent("최상위 대상 4개 기준 · 하위 대상 5개 제외 · scan 시점 65일 전 ~ 18일 전");
  expect(within(box).getByText("65일 전")).toHaveClass("text-attn");            // 오래된 쪽은 표의 경과 색(30일↑ 주의)
  // 표·검색은 합계 아래에서 그대로
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
  // 요청은 storage 하나만(경로 검색·정렬과 무관)
  expect(seen.map((u) => u.search)).toEqual(["?storage=cephfs-dms"]);
});

test("합계 null ≠ 0: 모르는 값은 —, 0 은 「0개」, 모름을 뺀 합은 값 뒤 「이상」(hot 은 비율이라 붙이지 않는다) · df 모름이면 칸 없음", async () => {
  setup({ totals: { "cephfs-dms": totalsOf("cephfs-dms", {
    total_bytes: m(null, 0, 4), total_files: m(0, 4), total_directories: m(243, 3, 1),
    hot_ratio: { value: 0.5, hot_bytes: 1, total_bytes: 2, known: 3, unknown: 1 }, filesystem: null }) } });
  const box = await totalsBox();
  await within(box).findByText("243개");
  expect(valueOf(box, "실 사용량")).toHaveTextContent(/^—$/);
  expect(valueOf(box, "파일")).toHaveTextContent(/^0개$/);
  expect(valueOf(box, "디렉터리")).toHaveTextContent(/^243개 이상$/);
  expect(valueOf(box, "hot 비율(180일)")).toHaveTextContent(/^50%$/);
  expect(within(box).queryByText("파일시스템 사용(df)")).toBeNull();
  expect(box).toHaveTextContent("값 모름 4개");
});

test("각주 조각은 해당할 때만: 형식 오류·값 모름·창(1만) -- 하위 0 이면 없음, 같은 날 scan 시점은 하나", async () => {
  setup({ totals: { "cephfs-dms": totalsOf("cephfs-dms", {
    nested_target_count: 0, invalid_target_count: 2, truncated: true, total_files: m(600, 1, 3),
    oldest_scan_at: isoAgo(5), newest_scan_at: isoAgo(5) }) } });
  const box = await totalsBox();
  expect(await within(box).findByText(/형식이 잘못된 대상 2개 제외/)).toBeInTheDocument();
  expect(box).toHaveTextContent(
    "최상위 대상 4개 기준 · 형식이 잘못된 대상 2개 제외 · 값 모름 3개 · 최근 스캔된 10,000개 대상 기준 · scan 시점 5일 전");
  expect(box).not.toHaveTextContent("하위 대상");
  expect(box).not.toHaveTextContent("요약 읽는 중");
  expect(box).not.toHaveTextContent("~");
});

test("요약 읽는 중(pending > 0 · progressed): 1초 뒤 다시 불러 이어 읽고, 다 읽으면 멈춘다", async () => {
  const calls: number[] = [];
  setup({ totals: (s, call) => {
    calls.push(call);
    return call === 1 ? totalsOf(s, { pending_count: 2, progressed: true, total_files: m(600, 2, 2) }) : totalsOf(s);
  } });
  const box = await totalsBox();
  expect(await within(box).findByText(/요약 읽는 중 2개/)).toBeInTheDocument();
  expect(box).not.toHaveTextContent("값 모름");                                // 읽는 중인 것은 따로 말한다
  expect(valueOf(box, "파일")).toHaveTextContent(/^600개 이상$/);
  await waitFor(() => expect(valueOf(box, "파일")).toHaveTextContent(/^662개$/), { timeout: 3000 });
  expect(box).not.toHaveTextContent("요약 읽는 중");
  await new Promise((r) => setTimeout(r, 1300));
  expect(calls).toEqual([1, 2]);
});

test("진전 없는 pending(progressed false)은 다시 부르지 않고 「값 모름」으로 말한다", async () => {
  const calls: number[] = [];
  setup({ totals: (s, call) => {
    calls.push(call);
    return totalsOf(s, { pending_count: 2, progressed: false, total_files: m(600, 2, 2) });
  } });
  const box = await totalsBox();
  expect(await within(box).findByText(/값 모름 2개/)).toBeInTheDocument();
  expect(box).not.toHaveTextContent("요약 읽는 중");
  await new Promise((r) => setTimeout(r, 1300));
  expect(calls).toEqual([1]);
});

test("재조회 실패로 이어 읽기가 멈추면 「읽는 중」이라 말하지 않는다 -- 남은 대상은 값 모름, 「다시 시도」로 이어 읽는다", async () => {
  // 리뷰: 오류 중엔 폴링이 멈추는데(useStorageTotals) 캐시에 남은 응답이 pending·progressed 라 「요약 읽는 중 240개」와
  // 「요약을 읽는 중입니다…」가 아무것도 읽지 않는 동안 남았고, 값 모름은 pending 만큼 적게 셌다.
  const calls: number[] = [];
  let failing = true;
  setup({ totals: (s, call) => {
    calls.push(call);
    if (call === 1) {
      return totalsOf(s, { target_count: 300, summed_target_count: 300, pending_count: 240, progressed: true,
                           total_files: m(null, 0, 300), file_size_histogram: { buckets: [], known: 0, unknown: 300 } });
    }
    return failing ? "error" : totalsOf(s);
  } });
  const box = await totalsBox();
  expect(await within(box).findByText(/요약 읽는 중 240개/)).toBeInTheDocument();
  expect(box).toHaveTextContent("값 모름 60개");
  expect(within(box).getByText("요약을 읽는 중입니다…")).toBeInTheDocument();
  expect(await within(box).findByText(/합계를 새로 고치지 못했습니다/, undefined, { timeout: 3000 })).toBeInTheDocument();
  expect(box).not.toHaveTextContent("요약 읽는 중");
  expect(box).not.toHaveTextContent("요약을 읽는 중입니다");
  expect(within(box).getByText("크기 분포를 아는 대상이 없습니다")).toBeInTheDocument();
  expect(box).toHaveTextContent("값 모름 300개");
  await new Promise((r) => setTimeout(r, 1300));
  expect(calls).toEqual([1, 2]);                                       // 오류 중엔 다시 부르지 않는다
  failing = false;
  await userEvent.click(within(box).getByRole("button", { name: "다시 시도" }));
  await waitFor(() => expect(valueOf(box, "파일")).toHaveTextContent(/^662개$/));
  expect(box).not.toHaveTextContent("새로 고치지 못했습니다");
  expect(calls).toEqual([1, 2, 3]);
});

test("합계 개수 값은 title 에 정확한 값(칸이 좁아 말줄임돼도 읽힌다)", async () => {
  setup({ totals: { "cephfs-dms": totalsOf("cephfs-dms", { total_files: m(1234567890, 3, 1) }) } });
  const box = await totalsBox();
  await within(box).findByText("1,234,567,890개");
  expect(valueOf(box, "파일")).toHaveAttribute("title", "1,234,567,890개");
  expect(valueOf(box, "파일")).toHaveTextContent(/^1,234,567,890개 이상$/);
  expect(valueOf(box, "디렉터리")).toHaveAttribute("title", "243개");
});

test("합계 실패여도 표는 그려진다 -- 서버 사유는 덧붙이고, 옛 API(SPA index.html 200)의 파서 문장은 띄우지 않는다", async () => {
  setup({ totals: { "cephfs-dms": "error" } });
  expect(await screen.findByText("합계를 불러오지 못했습니다 — internal")).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
  cleanup();
  setup({ totals: { "cephfs-dms": "html" } });
  expect(await screen.findByText("합계를 불러오지 못했습니다")).toBeInTheDocument();
  expect(screen.queryByText(/Unexpected token|JSON/)).toBeNull();
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
});

test("기록 0개 탭(등록 빈 스토리지·실패 기록만 남은 미등록)엔 합계 블록도 요청도 없다", async () => {
  const seen: string[] = [];
  setup({ storages: [st("a-empty")],
          summary: [sum("a-empty", { target_count: 0, succeeded_scan_count: 0, last_scan_at: null })],
          unregistered: [unreg("ghost", { target_count: 0, succeeded_scan_count: 0, last_scan_at: null })],
          onTotals: (u) => seen.push(u.searchParams.get("storage") ?? "") });
  expect(await screen.findByText("이 스토리지에는 아직 성공한 scan 이 없습니다")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: /^ghost/ }));
  expect(await screen.findByText("성공한 scan 기록이 없습니다")).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "이 스토리지 합계" })).toBeNull();
  expect(seen).toEqual([]);
});

test("합계는 경로 검색과 무관하고, 탭을 오가도 스토리지마다 한 번(같은 키 캐시)", async () => {
  const seen: string[] = [];
  setup({ storages: [st("cephfs-dms"), st("mirror")], summary: [sum("cephfs-dms"), sum("mirror")],
          targets: { "cephfs-dms": TARGETS, mirror: TARGETS.map((r) => ({ ...r, storage_name: "mirror" })) },
          totals: { "cephfs-dms": totalsOf("cephfs-dms"), mirror: totalsOf("mirror", { total_bytes: m(2048, 4) }) },
          onTotals: (u) => seen.push(u.search) });
  await within(await totalsBox()).findByText("17.0 MiB");
  await userEvent.type(screen.getByLabelText("경로 검색"), "team");
  await userEvent.click(screen.getByRole("tab", { name: /^mirror/ }));
  expect(await within(await totalsBox()).findByText("2.0 KiB")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: /^cephfs-dms/ }));
  expect(await within(await totalsBox()).findByText("17.0 MiB")).toBeInTheDocument();
  expect(seen).toEqual(["?storage=cephfs-dms", "?storage=mirror"]);
});

test("빈 이름 탭의 합계는 storage= (빈 이름)로 부른다", async () => {
  const seen: (string | null)[] = [];
  setup({ storages: [st("cephfs-dms")],
          summary: [sum("cephfs-dms"), sum("", { registered: false, source: null, backend_type: null, mount_path: null,
                                                 target_count: 1 })],
          targets: { "cephfs-dms": TARGETS, "": [{ ...TARGETS[0], storage_name: "", target: "blank-row" }] },
          totals: { "cephfs-dms": totalsOf("cephfs-dms"), "": totalsOf("", { total_bytes: m(4096, 4) }) },
          onTotals: (u) => seen.push(u.searchParams.get("storage")) });
  await userEvent.click(await screen.findByRole("tab", { name: "(이름 없음) 미등록 대상 1개" }));
  expect(await within(await totalsBox()).findByText("4.0 KiB")).toBeInTheDocument();
  expect(seen).toContain("");
});

// ---- 스토리지별 비교(2026-10-21) ----

test("스토리지별 비교: 스토리지가 하나면 없다", async () => {
  setup();
  await totalsBox();
  expect(screen.queryByText("스토리지별 비교")).toBeNull();
});

test("스토리지별 비교: 기본 닫힘(그 동안 합계 요청은 지금 탭뿐) -- 열면 탭마다 같은 쿼리, 기록 없음·실패·미등록 행, 총계 행 없음, 행 = 그 탭", async () => {
  const seen: string[] = [];
  setup({ storages: [st("cephfs-dms"), st("empty"), st("zz-broken")],
          summary: [sum("cephfs-dms"), sum("empty", { target_count: 0, succeeded_scan_count: 0, last_scan_at: null }),
                    sum("zz-broken"), sum("old", { registered: false, source: "last_known" })],
          unregistered: [unreg("old")],
          totals: { "cephfs-dms": totalsOf("cephfs-dms"), "zz-broken": "error",
                    old: totalsOf("old", { total_bytes: m(734003200, 4), filesystem: null }) },
          onTotals: (u) => seen.push(u.searchParams.get("storage") ?? "") });
  await within(await totalsBox()).findByText("17.0 MiB");
  expect(seen).toEqual(["cephfs-dms"]);
  const toggle = screen.getByText("스토리지별 비교");
  const details = toggle.closest("details")!;
  expect(details.open).toBe(false);
  await userEvent.click(toggle);
  expect(details.open).toBe(true);
  const table = await within(details).findByRole("table");
  const rowOf = (name: string) => within(table).getByRole("button", { name: `${name} 탭 열기` }).closest("tr")!;
  expect(within(table).getAllByRole("columnheader").map((h) => h.textContent))
    .toEqual(["스토리지", "실 사용량", "파일", "디렉터리", "hot 비율", "파일시스템(df)", "대상"]);
  await waitFor(() => expect(rowOf("old")).toHaveTextContent("700.0 MiB"));
  expect(rowOf("cephfs-dms")).toHaveTextContent("17.0 MiB");
  expect(rowOf("cephfs-dms")).toHaveTextContent("36.0 MiB / 22.8 GiB");
  expect(rowOf("cephfs-dms")).toHaveTextContent("662");
  expect(rowOf("old")).toHaveTextContent("미등록");
  await waitFor(() => expect(rowOf("zz-broken")).toHaveTextContent("불러오지 못함"));
  await waitFor(() => expect(within(rowOf("empty")).getAllByText("—")).toHaveLength(5));   // 기록 없음 ≠ 0 B
  // 행 = 탭(순서 그대로), 스토리지끼리 더한 총계 행은 없다
  expect(within(table).getAllByRole("row")).toHaveLength(1 + 4);
  expect(within(table).getAllByRole("button").map((b) => b.getAttribute("aria-label")))
    .toEqual(["cephfs-dms 탭 열기", "empty 탭 열기", "zz-broken 탭 열기", "old 탭 열기"]);
  expect(details).toHaveTextContent("스토리지끼리는 더하지 않습니다");
  // 지금 탭(패널)과 같은 쿼리 -- cephfs-dms 는 다시 부르지 않는다
  expect(seen.filter((x) => x === "cephfs-dms")).toHaveLength(1);
  expect(new Set(seen)).toEqual(new Set(["cephfs-dms", "empty", "zz-broken", "old"]));
  expect(within(rowOf("cephfs-dms")).getByRole("button")).toHaveAttribute("aria-current", "true");
  // 행을 누르면 그 탭(비교 표는 열린 채)
  await userEvent.click(rowOf("old"));
  expect(loc().get("storage")).toBe("old");
  expect(screen.getByRole("tab", { name: /^old/ })).toHaveAttribute("aria-selected", "true");
  expect(details.open).toBe(true);
  // 키보드: 이름 버튼(Enter)
  within(table).getByRole("button", { name: "empty 탭 열기" }).focus();
  await userEvent.keyboard("{Enter}");
  expect(loc().get("storage")).toBe("empty");
});

test("스토리지별 비교: 창을 넘어 잘린 합은 「이상」(부분 합) · 대상 칸은 탭 배지와 같은 수 · 각주가 두 뜻을 말한다", async () => {
  // 리뷰: 1만 넘는 스토리지의 행이 최근 스캔된 1만 개만 더한 값을 표식 없이 보였고, 대상 칸은 잘린 10,000 이라 탭(12,345)과 달랐다.
  setup({ storages: [st("cephfs-dms"), st("weka")], summary: [sum("cephfs-dms"), sum("weka", { target_count: 12345 })],
          windowLimit: 10000,
          totals: { "cephfs-dms": totalsOf("cephfs-dms"),
                    weka: totalsOf("weka", { truncated: true, target_count: 10000, summed_target_count: 9876,
                                             total_bytes: m(1024 ** 4, 9876), total_files: m(3551020, 9876),
                                             total_directories: m(98113, 9876),
                                             hot_ratio: { value: 0.91, hot_bytes: 91, total_bytes: 100, known: 9876,
                                                          unknown: 0 } }) } });
  await within(await totalsBox()).findByText("17.0 MiB");
  expect(screen.getByRole("tab", { name: /^weka/ })).toHaveAccessibleName("weka 대상 12,345개");
  const toggle = screen.getByText("스토리지별 비교");
  const details = toggle.closest("details")!;
  await userEvent.click(toggle);
  const table = await within(details).findByRole("table");
  const rowOf = (name: string) => within(table).getByRole("button", { name: `${name} 탭 열기` }).closest("tr")!;
  await waitFor(() => expect(rowOf("weka")).toHaveTextContent("1.0 TiB이상"));
  const weka = within(rowOf("weka")).getAllByRole("cell").map((c) => c.textContent);
  expect([weka[1], weka[2], weka[3], weka[4], weka[6]]).toEqual(["1.0 TiB이상", "3,551,020이상", "98,113이상", "91%", "12,345"]);
  expect(within(rowOf("weka")).getAllByRole("cell")[6]).toHaveAttribute(
    "title", "합계는 최근 스캔된 10,000개 대상 기준 부분 합 · 최상위 9,876개 합계");
  // 잘리지 않고 모름도 없으면 표식 없음 -- 대상 칸은 탭 배지(요약)와 같은 수
  await waitFor(() => expect(rowOf("cephfs-dms")).toHaveTextContent("17.0 MiB"));
  expect(rowOf("cephfs-dms")).not.toHaveTextContent("이상");
  expect(within(rowOf("cephfs-dms")).getAllByRole("cell")[6]).toHaveTextContent(/^2$/);
  expect(within(rowOf("cephfs-dms")).getAllByRole("cell")[6]).toHaveAttribute("title", "최상위 4개 합계");
  expect(details).toHaveTextContent(
    "「이상」 = 값을 모르는 대상(요약 읽는 중 포함)을 뺀 합이거나 대상이 많아 최근 스캔된 10,000개 대상만 더한 부분 합");
});

test("스토리지별 비교: 요약이 없으면 대상 칸은 합계의 수 -- 잘렸으면 「+」, 각주는 창 크기를 지어내지 않는다", async () => {
  setup({ storages: [st("cephfs-dms"), st("weka")], summary: "error", scanStorages: ["cephfs-dms", "weka"],
          totals: { "cephfs-dms": totalsOf("cephfs-dms"),
                    weka: totalsOf("weka", { truncated: true, target_count: 10000, summed_target_count: 9876 }) } });
  await within(await totalsBox()).findByText("17.0 MiB");
  const toggle = screen.getByText("스토리지별 비교");
  await userEvent.click(toggle);
  const table = await within(toggle.closest("details")!).findByRole("table");
  const rowOf = (name: string) => within(table).getByRole("button", { name: `${name} 탭 열기` }).closest("tr")!;
  await waitFor(() => expect(within(rowOf("weka")).getAllByRole("cell")[6]).toHaveTextContent(/^10,000\+$/));
  expect(within(rowOf("cephfs-dms")).getAllByRole("cell")[6]).toHaveTextContent(/^9$/);
  expect(toggle.closest("details")!).toHaveTextContent("최근 스캔된 창 안의 대상만 더한 부분 합");
});
