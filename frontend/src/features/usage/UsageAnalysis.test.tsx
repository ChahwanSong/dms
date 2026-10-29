import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import { setupServer } from "msw/node";
import { delay, http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest";
import { UsageAnalysis, hotRatio, pointEpoch, stackLayout, staleClass } from "./UsageAnalysis";
import type { HistogramBucket, Storage, UnregisteredStorage, UsageFacets, UsageFiltersEcho, UsagePoint, UsagePv,
              UsageStorageSummary, UsageStorageTotals } from "../../lib/types";
import { downloadCsv } from "../../lib/csvExport";
import { USAGE_API_UNKNOWN, USAGE_FILTER_MISMATCH } from "./useUsage";

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

// ---- 서버 흉내(2026-10-29 필터): /targets·/facets 는 픽스처 행의 pv·_requesters(테스트 전용 -- 그 경로를 scan 한 요청자들)로
// 대상 단위 필터·선택지를 계산한다(서버 usage_attribution 의 결합 규칙: PV 축은 같은 정체 하나가 모두 만족, PV 축이 있으면 구분은
// pv). pv 가 없는 행은 PV 밖으로 센다(filesystem 탭).
type Row = { target: string; pv?: UsagePv; _requesters?: string[] } & Record<string, unknown>;
const ATTR = ["cluster", "namespace", "pvc", "storage_class", "user", "scope", "requester"] as const;
const PV_AXES = ["cluster", "namespace", "pvc", "storage_class", "user"] as const;
const echoOf = (u: URL, withPath = true): UsageFiltersEcho => ({
  storage: u.searchParams.get("storage"), ...(withPath ? { path: u.searchParams.get("path") } : {}),
  ...Object.fromEntries(ATTR.map((k) => [k, u.searchParams.get(k)])) });
const hasAttrParams = (u: URL) => ATTR.some((k) => u.searchParams.has(k));
function serverFilter(rows: Row[], u: URL): Row[] {
  const g = (k: string) => u.searchParams.get(k);
  const axes = PV_AXES.filter((k) => g(k) !== null);
  const scope = g("scope") ?? (axes.length > 0 ? "pv" : null);
  return rows.filter((r) => {
    const req = g("requester");
    if (req !== null && !(r._requesters ?? []).includes(req)) return false;
    const src = r.pv?.source ?? "outside";
    const inPv = src === "inventory" || src === "recorded";
    if (scope !== null && (scope === "pv" ? !inPv : src !== scope)) return false;
    if (axes.length === 0) return true;
    return inPv && r.pv!.volumes.some((v) => (g("cluster") === null || v.cluster === g("cluster"))
      && (g("namespace") === null || v.namespace === g("namespace")) && (g("pvc") === null || v.pvc === g("pvc"))
      && (g("storage_class") === null || v.storage_class === g("storage_class"))
      && (g("user") === null || r.pv!.users.includes(g("user")!)));
  });
}
function facetsFrom(name: string, rows: Row[]): UsageFacets {
  const scope = { inventory: 0, recorded: 0, outside: 0, unknown: 0 };
  type Tally = Map<string, { e: Record<string, unknown>; t: Set<string> }>;
  const lists = { clusters: new Map(), namespaces: new Map(), pvcs: new Map(), storage_classes: new Map(),
                  users: new Map(), requesters: new Map() } as Record<string, Tally>;
  const add = (l: string, k: string, e: Record<string, unknown>, target: string) => {
    const m = lists[l]; if (!m.has(k)) m.set(k, { e, t: new Set() }); m.get(k)!.t.add(target);
  };
  for (const r of rows) {
    const src = r.pv?.source ?? "outside";
    scope[src] += 1;
    if (src === "inventory" || src === "recorded") {
      for (const v of r.pv!.volumes) {
        add("clusters", v.cluster, { cluster: v.cluster }, r.target);
        add("namespaces", `${v.cluster}/${v.namespace}`, { cluster: v.cluster, namespace: v.namespace }, r.target);
        add("pvcs", `${v.cluster}/${v.namespace}/${v.pvc}`,
            { cluster: v.cluster, namespace: v.namespace, pvc: v.pvc, storage_class: v.storage_class }, r.target);
        if (v.storage_class) add("storage_classes", v.storage_class, { storage_class: v.storage_class }, r.target);
      }
      for (const u of r.pv!.users) add("users", u, { dms_user: u }, r.target);
    }
    for (const q of r._requesters ?? []) add("requesters", q, { requester_id: q }, r.target);
  }
  const out = (l: string) => [...lists[l].values()].map((x) => ({ ...x.e, target_count: x.t.size }));
  return { storage_name: name, computed_at: "", window_limit: 10000, window_truncated: false, target_count: rows.length,
           scope, clusters: out("clusters"), namespaces: out("namespaces"), pvcs: out("pvcs"),
           storage_classes: out("storage_classes"), users: out("users"), requesters: out("requesters"),
           lists_truncated: {} } as UsageFacets;
}
const strip = ({ _requesters, ...r }: Row) => r;   // eslint-disable-line @typescript-eslint/no-unused-vars

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
  slowWhen?: (u: URL) => boolean;    // 이 조건의 목록 응답을 늦춘다(자리표시 검사)
  filterPath?: boolean;              // path 파라미터로 행을 거른다(부분 문자열)
  // 봉투의 matched_count(기본 = 거른 행 수) · window_truncated · 메아리 덮어쓰기 · 봉투 대신 줄 응답(배포 틈)
  matched?: number | null | ((u: URL, rows: Row[]) => number | null);
  windowTruncated?: boolean;
  echo?: (u: URL) => unknown;
  targetsRaw?: () => Response;
  // 선택지: 기본은 픽스처 행에서 계산(facetsFrom). "error" = 500.
  facets?: "error";
  onFacets?: (u: URL) => void;
  history?: unknown;
  onHistory?: (u: URL) => void;
  onExport?: (u: URL) => unknown;
  // 스토리지 합계(storage 파라미터로 고른다 -- 없으면 대상 0 응답). "error" = 500, "html" = 옛 API 의 SPA 200.
  totals?: Record<string, UsageStorageTotals | "error" | "html">
    | ((storage: string, call: number) => UsageStorageTotals | "error" | "html");
  onTotals?: (u: URL) => void;
  totalsEcho?: (u: URL) => unknown;    // 필터 합계의 메아리 덮어쓰기
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
    http.get("/api/admin/usage/targets", async ({ request }) => {
      const u = new URL(request.url);
      o.onTargets?.(u);
      const s = u.searchParams.get("storage") ?? "";
      if (o.slowTargets === s || o.slowWhen?.(u)) await delay("infinite");
      if (o.targetsRaw) return o.targetsRaw();
      let rows = ((o.targets ?? { "cephfs-dms": TARGETS })[s] ?? []) as Row[];
      const q = u.searchParams.get("path");
      if (o.filterPath && q) rows = rows.filter((r) => r.target.includes(q));
      rows = serverFilter(rows, u);
      const matched = typeof o.matched === "function" ? o.matched(u, rows) : o.matched !== undefined ? o.matched : rows.length;
      return HttpResponse.json({
        rows: rows.slice(0, Number(u.searchParams.get("limit") ?? 100)).map(strip), matched_count: matched,
        window_limit: o.windowLimit ?? 10000, window_truncated: o.windowTruncated ?? false,
        filters: o.echo ? o.echo(u) : echoOf(u) });
    }),
    http.get("/api/admin/usage/facets", ({ request }) => {
      const u = new URL(request.url);
      o.onFacets?.(u);
      if (o.facets === "error") return HttpResponse.json({ detail: "internal" }, { status: 500 });
      const s = u.searchParams.get("storage") ?? "";
      return HttpResponse.json(facetsFrom(s, ((o.targets ?? { "cephfs-dms": TARGETS })[s] ?? []) as Row[]));
    }),
    http.get("/api/admin/usage/scan-history", ({ request }) => {
      o.onHistory?.(new URL(request.url));
      return HttpResponse.json(o.history ?? HISTORY);
    }),
    // 새 내보내기(/targets/export): 픽스처 본문에 메아리·matched 가 없으면 보낸 값 그대로 채운다(본문이 filters 를 실으면 그 값).
    http.get("/api/admin/usage/targets/export", ({ request }) => {
      const u = new URL(request.url);
      const body = (o.onExport?.(u) ?? { generated_at: "", count: 0, truncated: false, rows: [] }) as { rows: unknown[] };
      return HttpResponse.json({ matched_count: body.rows.length, filters: echoOf(u), ...body });
    }),
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
      // 필터 합계(필터 파라미터가 있을 때)는 메아리를 싣는다(경로 검색은 받지 않는다 -- 메아리에도 없다).
      return HttpResponse.json(hasAttrParams(u)
        ? { ...t, filesystem: null, filters: o.totalsEcho ? o.totalsEcho(u) : echoOf(u, false) } : t);
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
  expect(await screen.findByText(/지금 목록\(검색·필터·상위 100개\)에 없어 아래에 표시합니다/)).toBeInTheDocument();
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

test("건수 문구: 「N개」(봉투의 matched_count) · 검색 중 「필터에 맞는 N개」", async () => {
  list(LIST);
  await screen.findByRole("button", { name: "fresh" });
  expect(within(screen.getByRole("tabpanel")).getByText("3개")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("경로 검색"), "fr");
  expect(await screen.findByText("필터에 맞는 3개")).toBeInTheDocument();
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

test("지표 정렬 창 안내: 맞는 대상 수(봉투 matched_count)가 서버 window_limit 을 넘을 때만(숫자는 서버 값)", async () => {
  list(LIST, { summary: [sum("cephfs-dms", { target_count: 12345 })], windowLimit: 10000, matched: 12345 });
  await screen.findByRole("button", { name: "fresh" });
  expect(screen.queryByText(/개 안에서 정렬/)).toBeNull();          // 최근 스캔 정렬(SQL)엔 창이 없다
  await userEvent.click(screen.getByRole("button", { name: "hot 비율 정렬" }));
  expect(await screen.findByText("최근 스캔된 10,000개 안에서 정렬")).toBeInTheDocument();
});

test("지표 정렬 창 안내 + 경로 검색: 맞는 수(정확)가 창 이하면 안내 없음, 넘으면 「필터에 맞는 대상 중」 -- 조건부 추정은 없다", async () => {
  const many = Array.from({ length: 100 }, (_, i) => ({ ...(LIST[0] as object), target: `t${i}` }));
  // 서버 흉내: 검색 없음·「t」는 1만 넘게 맞고, 「solo」는 하나
  list([...many, { ...(LIST[0] as object), target: "solo" }],
       { summary: [sum("cephfs-dms", { target_count: 12345 })], windowLimit: 10000, filterPath: true,
         matched: (u, rows) => (u.searchParams.get("path") === "solo" ? rows.length : 12345) });
  await userEvent.click(await screen.findByRole("button", { name: "hot 비율 정렬" }));
  expect(await screen.findByText("최근 스캔된 10,000개 안에서 정렬")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("경로 검색"), "solo");
  expect(await screen.findByRole("button", { name: "solo" })).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByText(/개 안에서 정렬/)).toBeNull());
  await userEvent.clear(screen.getByLabelText("경로 검색"));
  await userEvent.type(screen.getByLabelText("경로 검색"), "t");
  expect(await screen.findByText("필터에 맞는 대상 중 최근 스캔된 10,000개 안에서 정렬")).toBeInTheDocument();
  expect(screen.getByText("필터에 맞는 12,345개 · 상위 100개 표시")).toBeInTheDocument();
  expect(screen.queryByText(/넘으면/)).toBeNull();
});

test("목록이 상한(100)을 채우면 「상위 100개 표시 · 전체 N개」(전체 = 봉투의 matched_count -- 탭 배지의 수가 아니다)", async () => {
  const many = Array.from({ length: 100 }, (_, i) => ({ ...(LIST[0] as object), target: `t${i}` }));
  list(many, { summary: [sum("cephfs-dms", { target_count: 99999 })], matched: 12345 });
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

test("CSV 내보내기: 서버 상한을 넘으면 잘렸다고 말한다(경로 검색·필터로 좁히라고)", async () => {
  list(LIST, { onExport: () => ({ generated_at: "", count: 10000, truncated: true, matched_count: 12345, rows: [LIST[0]] }) });
  await screen.findByRole("button", { name: "fresh" });
  await userEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
  expect(await screen.findByRole("status")).toHaveTextContent(
    "상한 10,000개까지만 내보냈습니다 — 경로 검색·필터로 범위를 좁히세요");
});

test("CSV 내보내기는 지금 열 정렬(sort·order)과 경로 검색을 그대로 싣는다", async () => {
  const exportUrls: URL[] = [];
  list(LIST, { onExport: (u) => { exportUrls.push(u); return undefined; } });
  await screen.findByRole("button", { name: "fresh" });
  await userEvent.click(screen.getByRole("button", { name: "hot 비율 정렬" }));
  await userEvent.type(screen.getByLabelText("경로 검색"), "fr");
  await screen.findByText("필터에 맞는 3개");
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

// ---- 2026-10-26 dscan 포크 ae781bd: 실 사용량 정확값(summary.total_bytes)·잘못된 인코딩 이름(카운터만) ----

test("펼친 상세: 최신 실 사용량·이력 표의 용량은 title 에 정확한 바이트, 잘못된 인코딩 이름은 1개 이상일 때만 한 줄", async () => {
  setup({ targets: { "cephfs-dms": [
            { ...TARGETS[0], latest: point({ total_bytes: 3072, invalid_name_entries: 15 }) },
            { ...TARGETS[1], latest: point({ invalid_name_entries: 0 }) }] } });
  await userEvent.click(await screen.findByRole("button", { name: "artifacts" }));
  const region = await detailOf("artifacts");
  const latest = (await within(region).findByText("최신 실 사용량", { selector: "dt" })).nextElementSibling;
  expect(latest).toHaveTextContent("3.0 KiB");
  expect(latest).toHaveAttribute("title", "3,072 B");
  expect(within(region).getByText("최신 scan: 잘못된 인코딩 이름 15개")).toHaveClass("text-attn");
  expect(within(region).getByRole("button", { name: "잘못된 인코딩 이름 설명" })).toBeInTheDocument();
  const details = within(region).getByText("스캔 이력 3건").closest("details")!;
  expect(within(details).getByText("3.0 KiB").closest("td")).toHaveAttribute("title", "3,072 B");
  // 0 은 말하지 않는다(펼침마다 줄이 늘지 않게 -- 전수 값은 CSV)
  await userEvent.click(screen.getByRole("button", { name: "team" }));
  const team = await detailOf("team");
  await within(team).findByText("최신 실 사용량", { selector: "dt" });
  expect(within(team).queryByText(/잘못된 인코딩 이름/)).toBeNull();
});

test("펼친 상세: 잘못된 인코딩 이름을 모르면(구형 리포트·구형 서버) 아무 말도 하지 않는다 -- 0 으로 지어내지 않는다", async () => {
  setup({ targets: { "cephfs-dms": [{ ...TARGETS[0], latest: point({ invalid_name_entries: null }) }, TARGETS[1]] } });
  await userEvent.click(await screen.findByRole("button", { name: "artifacts" }));
  const region = await detailOf("artifacts");
  await within(region).findByText("최신 실 사용량", { selector: "dt" });
  expect(within(region).queryByText(/잘못된 인코딩 이름/)).toBeNull();
});

test("이 스토리지 합계: 잘못된 인코딩 이름 합은 1개 이상일 때만 각주 한 조각(주의색) -- 모르는 대상이 있으면 「이상」", async () => {
  setup({ totals: { "cephfs-dms": totalsOf("cephfs-dms", { invalid_name_entries: m(15, 3, 1) }) } });
  const box = await totalsBox();
  const part = await within(box).findByText("잘못된 인코딩 이름 15개 이상");
  expect(part).toHaveClass("text-attn");
  expect(box).not.toHaveTextContent("파손");                                       // 파손 경로에 더하지 않는다
  cleanup();
  setup({ totals: { "cephfs-dms": totalsOf("cephfs-dms", { invalid_name_entries: m(0, 4) }) } });
  const zero = await totalsBox();
  await within(zero).findByText("17.0 MiB");
  expect(zero).not.toHaveTextContent("잘못된 인코딩 이름");
  cleanup();
  setup();                                                                           // 구형 서버(키 없음)
  const old = await totalsBox();
  await within(old).findByText("17.0 MiB");
  expect(old).not.toHaveTextContent("잘못된 인코딩 이름");
});

// ---- 필터(2026-10-29 「사용자·PV 이름·namespace 등으로 필터」) ----
// 픽스처 = 테스트베드 모양: k8s-pv-nfs 가 두 클러스터(dmsk8s 의 tb-nfs, dmsk8s2 의 c2-nfs)의 PV 를 담고, 지운 PVC 의 기록 하나,
// 직접 경로 scan 하나(PV 밖). cephfs-dms 는 filesystem(전부 PV 밖) -- 요청자만 둘.

const NFS = "k8s-pv-nfs";
const vol = (cluster: string, namespace: string, pvc: string, volume: string, sc: string) =>
  ({ cluster, namespace, pvc, volume, storage_class: sc });
const inv = (vs: UsagePv["volumes"], users: string[], root: string): UsagePv => ({ source: "inventory", root, volumes: vs, users });
const OUTSIDE: UsagePv = { source: "outside", root: null, volumes: [], users: [] };
const nrow = (target: string, pv: UsagePv, requesters: string[], days = 1) =>
  ({ storage_name: NFS, target, scan_count: requesters.length, last_scan_at: isoAgo(days), pv, _requesters: requesters });
const DATASETS = vol("dmsk8s", "team-a", "datasets", "pvc-aaa", "tb-nfs");
const NFS_ROWS = [
  nrow("team-a/datasets/dms-proof", inv([DATASETS], ["alice", "junwoo.song"], "team-a/datasets"), ["alice", "mason"]),
  nrow("team-a/datasets/proof-scan", inv([DATASETS], ["alice", "junwoo.song"], "team-a/datasets"), ["mason"], 2),
  nrow("team-b/models", inv([vol("dmsk8s", "team-b", "models", "pvc-bbb", "tb-nfs")], [], "team-b/models"), ["mason"], 3),
  nrow("c2/lab-y/weights", inv([vol("dmsk8s2", "lab-y", "weights", "pvc-ccc", "c2-nfs")], ["cocoa.song"], "c2/lab-y/weights"),
       ["mason"], 4),
  nrow("old/gone", { source: "recorded", root: "old/gone", volumes: [vol("dmsk8s", "team-a", "gone", "pvc-ddd", "tb-nfs")],
                     users: ["alice"] }, ["alice"], 5),
  nrow("proof-d9/alice/latest", OUTSIDE, ["mason"], 6),
];
const FS_ROWS = TARGETS.map((r, i) => ({ ...r, pv: OUTSIDE, _requesters: i === 0 ? ["alice", "mason"] : ["mason"] }));
const pvSetup = (o: Setup = {}) => setup({
  storages: [st("cephfs-dms"), st(NFS, { backend_type: "nfs" })],
  summary: [sum("cephfs-dms"), sum(NFS, { target_count: NFS_ROWS.length })],
  targets: { "cephfs-dms": FS_ROWS, [NFS]: NFS_ROWS }, ...o });

const filterGroup = () => screen.findByRole("group", { name: "사용량 필터" });
const select = (label: string) => within(screen.getByRole("group", { name: "사용량 필터" })).getByRole("combobox", { name: label });
const optionTexts = (el: HTMLElement) => within(el).getAllByRole("option").map((o) => o.textContent);

test("필터 줄: PV 탭은 7개 select(라벨·순서·옵션 = 선택지와 대상 수), filesystem 탭은 요청자만", async () => {
  pvSetup({ path: `/admin/usage?storage=${NFS}` });
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  const group = await filterGroup();
  await waitFor(() => expect(within(group).getAllByRole("combobox")).toHaveLength(7));
  expect(within(group).getAllByRole("combobox").map((s) => s.getAttribute("aria-label"))).toEqual(
    ["클러스터 필터", "namespace 필터", "PV 이름 필터", "StorageClass 필터", "PV 구분 필터", "권한 사용자 필터", "요청자 필터"]);
  expect(optionTexts(select("클러스터 필터"))).toEqual(["클러스터 전체", "dmsk8s · 4개", "dmsk8s2 · 1개"]);
  // 클러스터가 둘이라 namespace 옵션에 클러스터를 붙인다(namespace 는 클러스터마다만 유일)
  expect(optionTexts(select("namespace 필터"))).toEqual(
    ["namespace 전체", "team-a · dmsk8s · 3개", "team-b · dmsk8s · 1개", "lab-y · dmsk8s2 · 1개"]);
  expect(optionTexts(select("PV 이름 필터"))).toEqual(["PV 이름 전체", "datasets — team-a (tb-nfs) · dmsk8s · 2개",
    "models — team-b (tb-nfs) · dmsk8s · 1개", "weights — lab-y (c2-nfs) · dmsk8s2 · 1개", "gone — team-a (tb-nfs) · dmsk8s · 1개"]);
  expect(optionTexts(select("StorageClass 필터"))).toEqual(["StorageClass 전체", "tb-nfs · 4개", "c2-nfs · 1개"]);
  expect(optionTexts(select("PV 구분 필터"))).toEqual(["구분 전체", "PV 안 · 5개", "PV 밖(직접 경로) · 1개"]);  // 0 범주는 없다
  expect(optionTexts(select("권한 사용자 필터"))).toEqual(
    ["권한 사용자 전체", "alice · 3개", "junwoo.song · 2개", "cocoa.song · 1개"]);
  expect(optionTexts(select("요청자 필터"))).toEqual(["요청자 전체", "alice · 2개", "mason · 5개"]);
  expect(within(group).getByRole("button", { name: "필터 설명" })).toHaveAccessibleDescription(
    /PV 필터는 각 경로가 지금 어느 PV\(=PVC\) 아래에 있는지로 거릅니다/);
  expect(within(group).queryByRole("button", { name: "필터 지우기" })).toBeNull();     // 켜진 필터가 없다
  // filesystem 탭: PV 안 대상이 0 이라 PV 축·구분(범주 하나)은 숨고 요청자만
  await userEvent.click(screen.getByRole("tab", { name: /^cephfs-dms/ }));
  await screen.findByRole("button", { name: "artifacts" });
  await waitFor(() => expect(within(screen.getByRole("group", { name: "사용량 필터" })).getAllByRole("combobox")
    .map((s) => s.getAttribute("aria-label"))).toEqual(["요청자 필터"]));
});

test("딥링크 ?storage=k8s-pv-nfs&cluster=dmsk8s2&namespace=lab-y: 요청에 실리고 select 가 그 값, 목록·건수·필터 합계가 그 결과", async () => {
  const seen: URL[] = [], totals: string[] = [];
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=dmsk8s2&namespace=lab-y`, onTargets: (u) => seen.push(u),
            totals: { [NFS]: totalsOf(NFS) }, onTotals: (u) => totals.push(u.search) });
  expect(await screen.findByRole("button", { name: "c2/lab-y/weights" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "team-a/datasets/dms-proof" })).toBeNull();
  for (const u of seen) {
    expect(u.pathname).toBe("/api/admin/usage/targets");
    expect([u.searchParams.get("cluster"), u.searchParams.get("namespace"), u.searchParams.get("pvc")])
      .toEqual(["dmsk8s2", "lab-y", null]);
  }
  await filterGroup();
  await waitFor(() => expect(select("클러스터 필터")).toHaveValue("dmsk8s2"));
  expect(select("namespace 필터")).toHaveDisplayValue("lab-y · dmsk8s2 · 1개");
  expect(screen.getByText("필터에 맞는 1개")).toBeInTheDocument();
  expect(within(screen.getByRole("group", { name: "사용량 필터" })).getByRole("button", { name: "필터 지우기" }))
    .toBeInTheDocument();
  // 필터 합계 카드(스토리지 합계 아래): 제목·적용 필터 한 줄·df 없음, 요청은 필터만(경로 검색 없음)
  const box = await screen.findByRole("region", { name: "필터 합계" });
  expect(box).toHaveTextContent("적용 필터: 클러스터 dmsk8s2 · namespace lab-y");
  expect(within(box).queryByText("파일시스템 사용(df)")).toBeNull();
  expect(await screen.findByRole("region", { name: "이 스토리지 합계" })).toBeInTheDocument();
  await waitFor(() => expect([...totals].sort()).toEqual(
    [`?storage=${NFS}`, `?storage=${NFS}&cluster=dmsk8s2&namespace=lab-y`]));
});

test("위계: namespace 를 고르면 클러스터가 함께 실리고, 클러스터를 바꾸면 namespace·PV 이름이 풀린다 · 「PV 밖」은 PV 축을, PV 축은 「PV 밖」을 푼다", async () => {
  pvSetup({ path: `/admin/usage?storage=${NFS}` });
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  await filterGroup();
  await waitFor(() => expect(select("PV 이름 필터")).toBeInTheDocument());
  await userEvent.selectOptions(select("PV 이름 필터"), "datasets — team-a (tb-nfs) · dmsk8s · 2개");
  await waitFor(() => expect([loc().get("cluster"), loc().get("namespace"), loc().get("pvc")])
    .toEqual(["dmsk8s", "team-a", "datasets"]));
  expect(await screen.findByRole("button", { name: "team-a/datasets/proof-scan" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "old/gone" })).toBeNull();          // 같은 namespace 의 다른 PV 이름
  // PV 이름 선택지는 위 단계(클러스터·namespace)로 좁혀진다
  expect(optionTexts(select("PV 이름 필터"))).toEqual(
    ["PV 이름 전체", "datasets — team-a (tb-nfs) · dmsk8s · 2개", "gone — team-a (tb-nfs) · dmsk8s · 1개"]);
  await userEvent.selectOptions(select("클러스터 필터"), "dmsk8s2 · 1개");
  await waitFor(() => expect([loc().get("cluster"), loc().get("namespace"), loc().get("pvc")])
    .toEqual(["dmsk8s2", null, null]));
  expect(await screen.findByRole("button", { name: "c2/lab-y/weights" })).toBeInTheDocument();
  // 구분 「PV 밖」 → PV 축이 풀린다(함께 걸면 늘 0건)
  await userEvent.selectOptions(select("PV 구분 필터"), "PV 밖(직접 경로) · 1개");
  await waitFor(() => expect(loc().get("scope")).toBe("outside"));
  expect(loc().get("cluster")).toBeNull();
  expect(await screen.findByRole("button", { name: "proof-d9/alice/latest" })).toBeInTheDocument();
  // PV 축(권한 사용자)을 고르면 「PV 밖」이 풀린다
  await userEvent.selectOptions(select("권한 사용자 필터"), "cocoa.song · 1개");
  await waitFor(() => expect(loc().get("user")).toBe("cocoa.song"));
  expect(loc().get("scope")).toBeNull();
  expect(await screen.findByRole("button", { name: "c2/lab-y/weights" })).toBeInTheDocument();
  // namespace 를 지우면 PV 이름도 풀린다(클러스터는 남는다)
  await userEvent.selectOptions(select("namespace 필터"), "team-a · dmsk8s · 3개");
  await waitFor(() => expect([loc().get("cluster"), loc().get("namespace")]).toEqual(["dmsk8s", "team-a"]));
  await userEvent.selectOptions(select("PV 이름 필터"), "gone — team-a (tb-nfs) · dmsk8s · 1개");
  await waitFor(() => expect(loc().get("pvc")).toBe("gone"));
  await userEvent.selectOptions(select("namespace 필터"), "namespace 전체");
  await waitFor(() => expect([loc().get("cluster"), loc().get("namespace"), loc().get("pvc")]).toEqual(["dmsk8s", null, null]));
});

test("URL 보존: 필터는 탭 전환·행 펼침/접기에 남는다 · 잘못된 storage 정리(replace)도 필터를 남긴다", async () => {
  let last: URL | null = null;
  pvSetup({ path: `/admin/usage?storage=${NFS}&requester=mason`, onTargets: (u) => { last = u; } });
  await userEvent.click(await screen.findByRole("button", { name: "team-b/models" }));
  expect([loc().get("target"), loc().get("requester")]).toEqual(["team-b/models", "mason"]);
  await userEvent.click(screen.getByRole("button", { name: "team-b/models" }));
  expect([loc().get("storage"), loc().get("target"), loc().get("requester")]).toEqual([NFS, null, "mason"]);
  await userEvent.click(screen.getByRole("tab", { name: /^cephfs-dms/ }));
  expect([loc().get("storage"), loc().get("requester")]).toEqual(["cephfs-dms", "mason"]);
  await waitFor(() => expect(last!.searchParams.get("storage")).toBe("cephfs-dms"));
  expect(last!.searchParams.get("requester")).toBe("mason");
  expect(await screen.findByRole("button", { name: "team" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "artifacts" })).not.toBeNull();          // artifacts 도 mason 이 scan 했다
  await waitFor(() => expect(select("요청자 필터")).toHaveValue("mason"));
  cleanup();
  pvSetup({ path: "/admin/usage?storage=typo-name&target=x&requester=alice" });
  expect(await screen.findByText("‘typo-name’ 스토리지의 사용량 기록이 없습니다 — 기본 탭을 보여 줍니다")).toBeInTheDocument();
  await waitFor(() => expect(loc().get("storage")).toBeNull());
  expect([loc().get("target"), loc().get("requester")]).toEqual([null, "alice"]);
});

test("그 스토리지에 없는 값은 「(이 스토리지에 없음)」 옵션 · 필터 0건이면 툴바·필터 줄이 남고 「필터에 맞는 대상이 없습니다」 → 지우기", async () => {
  pvSetup({ path: "/admin/usage?storage=cephfs-dms&cluster=dmsk8s" });
  expect(await screen.findByText("필터에 맞는 대상이 없습니다")).toBeInTheDocument();
  expect(screen.queryByText("이 스토리지에는 아직 성공한 scan 이 없습니다")).toBeNull();
  expect(screen.getByLabelText("경로 검색")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "CSV 내보내기" })).toBeInTheDocument();
  await filterGroup();
  // filesystem 탭이라도 켜진 PV 축은 보인다(풀 수 있게) -- 값은 이 스토리지에 없다고
  await waitFor(() => expect(select("클러스터 필터")).toHaveDisplayValue("dmsk8s (이 스토리지에 없음)"));
  expect(screen.getByText("필터에 맞는 0개")).toBeInTheDocument();
  const clears = screen.getAllByRole("button", { name: "필터 지우기" });
  expect(clears).toHaveLength(2);                                                       // 필터 줄 + 빈 상태
  await userEvent.click(clears[1]);
  await waitFor(() => expect(loc().get("cluster")).toBeNull());
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "필터 지우기" })).toBeNull();
  // 빈 상태의 버튼도 사라진다 -- 포커스는 필터 줄로(<body> 가 아니다)
  expect(document.activeElement).toBe(screen.getByRole("group", { name: "사용량 필터" }));
});

test("건수·각주: 「필터에 맞는 N개 · 상위 100개 표시」 · matched 모름(PV 판정 창 넘침)이면 그 사실을 한 문단 + 지표 정렬 각주", async () => {
  const many = Array.from({ length: 100 }, (_, i) => ({ ...TARGETS[0], target: `t${i}`, pv: OUTSIDE, _requesters: ["mason"] }));
  pvSetup({ path: "/admin/usage?storage=cephfs-dms&requester=mason", targets: { "cephfs-dms": many, [NFS]: NFS_ROWS },
            matched: 12345 });
  expect(await screen.findByText("필터에 맞는 12,345개 · 상위 100개 표시")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "hot 비율 정렬" }));
  expect(await screen.findByText("필터에 맞는 대상 중 최근 스캔된 10,000개 안에서 정렬")).toBeInTheDocument();
  cleanup();
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=dmsk8s`, matched: null, windowTruncated: true });
  expect(await screen.findByText("최근 스캔된 10,000개 대상 안에서 4개 맞음 — 대상이 10,000개를 넘는 스토리지라 PV 필터는 "
    + "이 범위에서만 거릅니다. 경로 검색으로 범위를 좁히세요")).toBeInTheDocument();
  expect(screen.queryByText(/필터에 맞는 \d/)).toBeNull();                              // 모르는 수를 지어내지 않는다
  await userEvent.click(screen.getByRole("button", { name: "파일 수 정렬" }));
  // PV 경로는 창 → 필터 순서다(2026-10-29 리뷰: 「필터에 맞는 대상 중 최근 N개」는 거꾸로라 위 문단과 모순됐다)
  expect(await screen.findByText("최근 스캔된 10,000개 대상 안에서 PV 필터에 맞는 것끼리 정렬")).toBeInTheDocument();
  expect(screen.queryByText("필터에 맞는 대상 중 최근 스캔된 10,000개 안에서 정렬")).toBeNull();
});

test("PV 판정 창은 정렬과 무관하게 최근 스캔 순(서버 규칙) -- 오래된 순·스캔 횟수에서도 같은 범위, 요청자·경로 검색이 먼저 좁힌 것을 밝힌다, 0건은 「그 범위 안에는」", async () => {
  // 2026-10-29 리뷰: 서버가 요청한 정렬로 창을 자르던 때 이 문단은 오래된 순에서 거짓이었다 -- 이제 서버 창이 늘 최근 스캔 순이라
  // 문단은 정렬로 바뀌지 않는다. 대신 요청자·경로 검색(SQL)이 창보다 먼저 걸린다는 사실은 말한다.
  const seen: URL[] = [];
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=dmsk8s&requester=mason`, matched: null, windowTruncated: true,
            onTargets: (u) => seen.push(u) });
  const note = "요청자에 맞는 대상 중 최근 스캔된 10,000개 안에서 3개 맞음 — 요청자에 맞는 대상이 10,000개를 넘어 PV 필터는 "
    + "이 범위에서만 거릅니다. 경로 검색으로 범위를 좁히세요";
  expect(await screen.findByText(note)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /^최근 스캔 정렬/ }));
  await waitFor(() => expect(seen[seen.length - 1].searchParams.get("order")).toBe("asc"));
  await userEvent.click(screen.getByRole("button", { name: "스캔 횟수 정렬" }));
  await waitFor(() => expect(seen[seen.length - 1].searchParams.get("sort")).toBe("scans"));
  expect(await screen.findByText(note)).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("경로 검색"), "team");
  expect(await screen.findByText(/^경로 검색·요청자에 맞는 대상 중 최근 스캔된 10,000개 안에서 \d+개 맞음 — 경로 검색·요청자에 맞는 대상이/))
    .toBeInTheDocument();
  cleanup();
  // 판정 창 안에 맞는 대상이 0개 -- 「없다」를 단정하지 않는다
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=nowhere`, matched: null, windowTruncated: true });
  expect(await screen.findByText("최근 스캔된 10,000개 대상 안에는 필터에 맞는 대상이 없습니다")).toBeInTheDocument();
  expect(screen.queryByText("필터에 맞는 대상이 없습니다")).toBeNull();
});

test("포커스: 「필터 지우기」(필터 줄·빈 상태)와 값 때문에만 보이던 칸이 사라져도 포커스가 <body> 로 떨어지지 않고 필터 줄로 간다", async () => {
  // 2026-10-29 리뷰(JobsList 와 같은 결함 유형): 키보드로 지우면 버튼이 언마운트돼 화면 낭독기가 자리를 잃었다.
  pvSetup({ path: `/admin/usage?storage=${NFS}&requester=mason` });
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  const group = await filterGroup();
  const clear = within(group).getByRole("button", { name: "필터 지우기" });
  clear.focus();
  await userEvent.keyboard("{Enter}");
  await waitFor(() => expect(loc().get("requester")).toBeNull());
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("group", { name: "사용량 필터" })));
  cleanup();
  // 단일 클러스터 탭: namespace 를 고르면 나타나는 클러스터 select -- 「클러스터 전체」로 바꾸면 그 칸이 사라진다
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=dmsk8s&namespace=team-a`,
            targets: { "cephfs-dms": FS_ROWS, [NFS]: NFS_ROWS.filter((r) => r.target !== "c2/lab-y/weights") } });
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  await filterGroup();
  const cluster = await waitFor(() => select("클러스터 필터"));
  cluster.focus();
  await userEvent.selectOptions(cluster, "클러스터 전체");
  await waitFor(() => expect([loc().get("cluster"), loc().get("namespace")]).toEqual([null, null]));
  await waitFor(() => expect(within(screen.getByRole("group", { name: "사용량 필터" }))
    .queryByRole("combobox", { name: "클러스터 필터" })).toBeNull());
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("group", { name: "사용량 필터" })));
  // 살아 있는 칸에서 고르면 포커스를 건드리지 않는다
  const ns = select("namespace 필터");
  ns.focus();
  await userEvent.selectOptions(ns, "team-b · 1개");
  await waitFor(() => expect(loc().get("namespace")).toBe("team-b"));
  expect(document.activeElement).toBe(select("namespace 필터"));
});

test("포커스: 기록 0 탭에서 필터를 지우면 필터 줄·툴바가 함께 사라진다 -- 포커스는 <body> 가 아니라 패널에 남는다", async () => {
  // 2026-10-29 2차 리뷰: 필터를 지우면 필터 줄이 사라지고(선택지 없음) 새 목록이 오면 「기록 없음」이라 툴바도 사라진다 -- 툴바로
  // 옮긴 포커스가 다시 <body> 로 떨어졌다. 빈 상태의 「필터 지우기」와 필터 줄의 것 둘 다.
  const empty = {
    storages: [st("cephfs-dms"), st("empty-store")],
    summary: [sum("cephfs-dms"), sum("empty-store", { target_count: 0, succeeded_scan_count: 0, last_scan_at: null })],
    targets: { "cephfs-dms": TARGETS, "empty-store": [] },
    path: "/admin/usage?storage=empty-store&requester=mason",
  };
  for (const which of ["empty", "group"] as const) {
    setup(empty);
    expect(await screen.findByText("필터에 맞는 대상이 없습니다")).toBeInTheDocument();
    const group = await filterGroup();
    const clears = screen.getAllByRole("button", { name: "필터 지우기" });
    expect(clears).toHaveLength(2);
    const btn = which === "group" ? within(group).getByRole("button", { name: "필터 지우기" }) : clears[1];
    btn.focus();
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(loc().get("requester")).toBeNull());
    expect(await screen.findByText("이 스토리지에는 아직 성공한 scan 이 없습니다")).toBeInTheDocument();
    expect(screen.queryByLabelText("경로 검색")).toBeNull();                      // 툴바도 사라졌다
    await waitFor(() => expect(document.activeElement).not.toBe(document.body));
    expect(screen.getByRole("tabpanel").contains(document.activeElement)).toBe(true);
    cleanup();
  }
});

test("자리표시(경로 검색 응답 대기) 동안 PV 창 문단·지표 정렬 각주를 말하지 않는다 -- 옛 결과의 수를 새 검색의 범위로 이름 붙이지 않는다", async () => {
  // 2026-10-29 2차 리뷰: 건수 자리는 「검색 중…」인데 문단은 이전 결과의 「4개 맞음」을 지금 입력의 범위(「경로 검색에 맞는 대상 중」)로
  // 말했다(아무도 확인하지 않은 수와 이유). 건수와 같은 규칙 -- 자리표시 동안 말하지 않는다.
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=dmsk8s`, matched: null, windowTruncated: true, filterPath: true,
            slowWhen: (u) => u.searchParams.get("path") !== null });
  expect(await screen.findByText(/^최근 스캔된 10,000개 대상 안에서 4개 맞음 — /)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "파일 수 정렬" }));
  expect(await screen.findByText("최근 스캔된 10,000개 대상 안에서 PV 필터에 맞는 것끼리 정렬")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("경로 검색"), "zzz");
  expect(await screen.findByText("검색 중…")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "team-a/datasets/dms-proof" })).toBeInTheDocument();   // 옛 행은 자리표시로 남는다
  expect(screen.queryByText(/맞음 — /)).toBeNull();
  expect(screen.queryByText(/PV 필터에 맞는 것끼리 정렬/)).toBeNull();
  cleanup();
  // 빈 결과의 자리표시: 제목·보조 줄은 그 결과를 낸 검색(봉투 메아리)으로 말한다 -- 지금 입력이 아니다
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=nowhere`, matched: null, windowTruncated: true, filterPath: true,
            slowWhen: (u) => u.searchParams.get("path") !== null });
  const title = "최근 스캔된 10,000개 대상 안에는 필터에 맞는 대상이 없습니다";
  expect(await screen.findByText(title)).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("경로 검색"), "zzz");
  expect(await screen.findByText("검색 중…")).toBeInTheDocument();
  expect(screen.getByText(title)).toBeInTheDocument();
  expect(screen.queryByText(/경로 검색에 맞는 대상 중/)).toBeNull();
  expect(screen.queryByText(/경로 검색 ‘zzz’ 도 함께/)).toBeNull();
});

test("배포 틈: /targets 가 봉투가 아니면(배열·HTML·404) 「새 사용량 API 를 모릅니다」, 메아리가 다르면 「필터가 화면과 다릅니다」 -- 둘 다 CSV 를 막는다", async () => {
  const cases: (() => Response)[] = [
    () => HttpResponse.json(TARGETS),                                                  // 옛 scan-targets 모양(배열)
    () => new HttpResponse("<!doctype html><html></html>", { status: 200, headers: { "content-type": "text/html" } }),
    () => HttpResponse.json({ detail: "Not Found" }, { status: 404 }),
  ];
  for (const raw of cases) {
    setup({ targetsRaw: raw });
    expect(await screen.findByText(USAGE_API_UNKNOWN)).toBeInTheDocument();
    expect(screen.queryByText(/Unexpected token|JSON/)).toBeNull();
    expect(screen.getByRole("button", { name: "CSV 내보내기" })).toBeDisabled();
    cleanup();
  }
  // 옛 서버는 모르는 파라미터를 조용히 무시한다 -- 메아리에 요청자가 없으면 거른 척한 전체 목록이다
  pvSetup({ path: `/admin/usage?storage=${NFS}&requester=alice`,
            echo: (u) => ({ ...echoOf(u), requester: null }) });
  expect(await screen.findByText(USAGE_FILTER_MISMATCH)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "team-b/models" })).toBeNull();
  expect(screen.getByRole("button", { name: "CSV 내보내기" })).toBeDisabled();
});

test("자리표시: 필터가 바뀌면 이전 필터의 행을 남기지 않는다(같은 필터 안의 정렬·검색만 남긴다)", async () => {
  pvSetup({ path: `/admin/usage?storage=${NFS}`, slowWhen: (u) => u.searchParams.get("requester") === "alice" });
  await screen.findByRole("button", { name: "team-b/models" });
  await filterGroup();
  await waitFor(() => expect(select("요청자 필터")).toBeInTheDocument());
  await userEvent.selectOptions(select("요청자 필터"), "alice · 2개");
  await waitFor(() => expect(screen.queryByRole("button", { name: "team-b/models" })).toBeNull());
  expect(within(screen.getByRole("tabpanel")).getByText("불러오는 중…")).toBeInTheDocument();
});

test("표: 6열 그대로 + 경로 칸 둘째 줄(PV 이름 — namespace (SC) · 클러스터 · 외 n개 · 기록 · PV 밖 · PV 모름), filesystem 탭엔 없음", async () => {
  const both = nrow("shared/root", inv([vol("dmsk8s", "team-a", "a1", "pvc-x", "tb-nfs"), vol("dmsk8s2", "team-a", "a1", "pvc-y", "c2-nfs")],
                                        ["alice"], "shared/root"), ["mason"]);
  const unknown = nrow("weird/x", { source: "unknown", root: null, volumes: [], users: [] }, ["mason"]);
  pvSetup({ path: `/admin/usage?storage=${NFS}`, targets: { "cephfs-dms": FS_ROWS, [NFS]: [...NFS_ROWS, both, unknown] } });
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  expect(within(screen.getByRole("table")).getAllByRole("columnheader")).toHaveLength(6);
  const rowOf = (t: string) => screen.getByRole("button", { name: t }).closest("td")!;
  await waitFor(() => expect(rowOf("team-a/datasets/dms-proof")).toHaveTextContent("datasets — team-a (tb-nfs) · dmsk8s"));
  expect(within(rowOf("team-a/datasets/dms-proof")).getByText("datasets — team-a (tb-nfs) · dmsk8s"))
    .toHaveAttribute("title", "클러스터 dmsk8s · 볼륨 pvc-aaa\nPV 루트 team-a/datasets");
  expect(rowOf("shared/root")).toHaveTextContent("a1 — team-a (tb-nfs) · dmsk8s 외 1개");
  expect(within(rowOf("old/gone")).getByText("기록")).toHaveAttribute("title",
    "지금 인벤토리에 없는 PV — 이 경로를 PV 주소로 scan 한 기록 기준");
  expect(within(rowOf("proof-d9/alice/latest")).getByText("PV 밖")).toHaveAttribute("title",
    "지금 어떤 PV 루트에도 속하지 않는 경로(직접 경로 scan·PV 상위 디렉터리)");
  expect(within(rowOf("weird/x")).getByText("PV 모름")).toHaveClass("italic");
  // 버튼 이름은 경로 그대로(둘째 줄은 버튼 밖)
  expect(screen.getByRole("button", { name: "old/gone" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: /^cephfs-dms/ }));
  await screen.findByRole("button", { name: "artifacts" });
  expect(within(screen.getByRole("table")).queryByText("PV 밖")).toBeNull();
  expect(within(screen.getByRole("table")).getAllByRole("columnheader")).toHaveLength(6);
});

test("펼친 상세 머리: PV 소속(정체 전부)·권한 사용자(없으면 관리자·특권 운영자만) · 요청자 필터면 「이력은 전체」", async () => {
  pvSetup({ path: `/admin/usage?storage=${NFS}&target=team-a/datasets/dms-proof&requester=alice` });
  const region = await screen.findByRole("region", { name: `${NFS}:team-a/datasets/dms-proof 상세` });
  const dl = await within(region).findByLabelText("PV 소속");
  expect(within(dl).getByText("PV 소속", { selector: "dt" }).nextElementSibling)
    .toHaveTextContent("datasets — team-a (tb-nfs) · dmsk8s");
  expect(within(dl).getByText("권한 사용자", { selector: "dt" }).nextElementSibling).toHaveTextContent(/^alice, junwoo\.song$/);
  expect(within(region).getByText("이력은 요청자와 무관한 이 경로의 전체 이력입니다")).toBeInTheDocument();
  cleanup();
  pvSetup({ path: `/admin/usage?storage=${NFS}&target=team-b/models` });
  const r2 = await screen.findByRole("region", { name: `${NFS}:team-b/models 상세` });
  await waitFor(() => expect(within(r2).getByLabelText("PV 소속")).toHaveTextContent("models — team-b (tb-nfs)"));
  expect(within(within(r2).getByLabelText("PV 소속")).getByText("권한 사용자", { selector: "dt" }).nextElementSibling)
    .toHaveTextContent("없음 — 관리자·특권 운영자만 접근");
  expect(within(r2).queryByText(/이력은 요청자와 무관한/)).toBeNull();
});

test("합계: 무필터 요청은 정확히 ?storage=X(스토리지별 비교와 한 벌) · 필터 합계는 필터만 싣고 경로 검색은 싣지 않는다 · 메아리가 다르면 오류", async () => {
  const seen: string[] = [];
  let last: URL | null = null;
  pvSetup({ path: "/admin/usage?storage=cephfs-dms&requester=mason", totals: { "cephfs-dms": totalsOf("cephfs-dms") },
            onTotals: (u) => seen.push(u.search), onTargets: (u) => { last = u; }, filterPath: true });
  const box = await screen.findByRole("region", { name: "필터 합계" });
  expect(await within(box).findByText("17.0 MiB")).toBeInTheDocument();
  expect(box).toHaveTextContent("적용 필터: 요청자 mason");
  expect(within(box).queryByText("파일시스템 사용(df)")).toBeNull();
  expect(within(box).getByRole("button", { name: "필터 합계 설명" })).toHaveAccessibleDescription(
    /경로 검색은 반영하지 않습니다\. 필터에 맞는 경로 중 최상위만 더합니다/);
  expect(within(await totalsBox()).getByText("파일시스템 사용(df)")).toBeInTheDocument();   // 스토리지 합계는 그대로
  await userEvent.type(screen.getByLabelText("경로 검색"), "art");
  await waitFor(() => expect(last!.searchParams.get("path")).toBe("art"));          // 목록은 검색을 싣는다
  expect(await screen.findByText("필터에 맞는 1개")).toBeInTheDocument();
  expect([...new Set(seen)].sort()).toEqual(["?storage=cephfs-dms", "?storage=cephfs-dms&requester=mason"]);
  expect(seen.some((s) => s.includes("path"))).toBe(false);
  cleanup();
  pvSetup({ path: "/admin/usage?storage=cephfs-dms&requester=mason", totals: { "cephfs-dms": totalsOf("cephfs-dms") },
            totalsEcho: (u) => ({ ...echoOf(u, false), requester: null }) });
  const bad = await screen.findByRole("region", { name: "필터 합계" });
  expect(await within(bad).findByText(`합계를 불러오지 못했습니다 — ${USAGE_FILTER_MISMATCH}`)).toBeInTheDocument();
});

test("CSV(필터): /targets/export 에 필터를 싣고 파일 이름은 -filtered, k8s 열 · PV 판정 창 넘침·메아리 없음을 말한다", async () => {
  vi.mocked(downloadCsv).mockClear();
  let exportUrl: URL | null = null;
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=dmsk8s&namespace=team-a`,
            onExport: (u) => { exportUrl = u;
              return { generated_at: "", count: 1, truncated: false, rows: [{ ...strip(NFS_ROWS[0]), latest: null, previous: null }] }; } });
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  await userEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
  await waitFor(() => expect(downloadCsv).toHaveBeenCalledTimes(1));
  expect(exportUrl!.pathname).toBe("/api/admin/usage/targets/export");
  expect([exportUrl!.searchParams.get("cluster"), exportUrl!.searchParams.get("namespace")]).toEqual(["dmsk8s", "team-a"]);
  const [filename, text] = vi.mocked(downloadCsv).mock.calls[0];
  expect(filename).toMatch(new RegExp(`^dms-usage-${NFS}-filtered-\\d{8}-\\d{4}\\.csv$`));
  const [header, line] = (text as string).split("\r\n");
  const cols = header.split(",");
  const val = (c: string) => line.split(",")[cols.indexOf(c)];
  expect([val("k8s_source"), val("k8s_cluster"), val("k8s_namespace"), val("k8s_pvc"), val("k8s_volume"),
          val("k8s_storage_class"), val("k8s_pv_root"), val("k8s_access_users")])
    .toEqual(["inventory", "dmsk8s", "team-a", "datasets", "pvc-aaa", "tb-nfs", "team-a/datasets", "alice;junwoo.song"]);
  expect(await screen.findByRole("status")).toHaveTextContent("1개 항목을 내보냈습니다");
  cleanup();
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=dmsk8s`,
            onExport: () => ({ generated_at: "", count: 4, truncated: true, matched_count: null, rows: [] }) });
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  await userEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
  expect(await screen.findByRole("status")).toHaveTextContent(
    "대상이 10,000개를 넘는 스토리지라 PV 필터는 최근 스캔된 10,000개 대상 안에서만 걸렀습니다 — CSV 가 전부가 아닐 수 있습니다");
  cleanup();
  vi.mocked(downloadCsv).mockClear();
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=dmsk8s`,
            onExport: () => ({ generated_at: "", count: 0, truncated: false, rows: [], filters: null }) });
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  await userEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(USAGE_FILTER_MISMATCH);
  expect(downloadCsv).not.toHaveBeenCalled();
});

test("선택지(facets) 실패: 필터 줄을 그리지 않고, 필터가 켜져 있으면 「필터 지우기」만 남는다(URL 의 필터는 그대로 걸린다)", async () => {
  let last: URL | null = null;
  pvSetup({ path: "/admin/usage?storage=cephfs-dms&requester=mason", facets: "error", onTargets: (u) => { last = u; } });
  await screen.findByRole("button", { name: "team" });
  expect(last!.searchParams.get("requester")).toBe("mason");
  expect(await screen.findByRole("button", { name: "필터 지우기" })).toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "사용량 필터" })).toBeNull();
  expect(screen.queryAllByRole("combobox")).toHaveLength(0);
  await userEvent.click(screen.getByRole("button", { name: "필터 지우기" }));
  await waitFor(() => expect(loc().get("requester")).toBeNull());
  expect(screen.queryByRole("button", { name: "필터 지우기" })).toBeNull();
});
