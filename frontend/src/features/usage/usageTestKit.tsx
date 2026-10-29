import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation, useNavigate, useNavigationType } from "react-router-dom";
import { setupServer } from "msw/node";
import { delay, http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect } from "vitest";
import { UsageAnalysis } from "./UsageAnalysis";
import { StoragesPage } from "./StoragesPage";
import type { HistogramBucket, Storage, UnregisteredStorage, UsageFacets, UsageFiltersEcho, UsagePoint, UsagePv,
              UsageStorageSummary, UsageStorageTotals } from "../../lib/types";

// 사용량 분석 화면 테스트의 공용 픽스처(2026-10-29 -- 1,700줄짜리 UsageAnalysis.test 를 화면별로 나눴다: 경로별 = UsageAnalysis.test,
// 경로 상세 시트 = TargetSheet.test, 스토리지별 = StoragesPage.test). msw 서버는 이 모듈의 것 하나 -- 테스트 파일마다 모듈이 따로
// 올라오므로(vitest 격리) 파일마다 installServer() 로 수명 훅을 건다. sessionStorage(경로별의 마지막 쿼리 -- usageMemory)도 jsdom 에선
// 파일 안 테스트끼리 이어지므로 테스트마다 비운다.
export const server = setupServer();
export function installServer() {
  beforeAll(() => server.listen());
  afterEach(() => { server.resetHandlers(); sessionStorage.clear(); });
  afterAll(() => server.close());
}

export const buckets = (vals: (number | undefined)[], ages = true) =>
  vals.map((b, i) => ({ bucket: `[${i}d,${i + 1}d]`,
                        ...(ages ? { min_age_days: i, max_age_days: i + 1 } : {}),
                        ...(b === undefined ? {} : { bytes: b }) }));

// ---- 화면 픽스처 ----

export const DAY = 86_400_000;
export const isoAgo = (days: number) => new Date(Date.now() - days * DAY).toISOString().replace(/\.\d{3}Z$/, "Z");

export const point = (over: Partial<UsagePoint>): UsagePoint => ({
  job_id: "j1", request_id: "r1", finished_at: "2026-08-23T13:00:00Z",
  generated_at_epoch: 1787500000, total_bytes: 1024,
  summary: { total_files: 7 },
  time_histograms: { atime: buckets([1024, 0]) as never,
                     mtime: buckets([512, 512]) as never },
  requester: "alice", ...over });

export const TARGETS = [
  { storage_name: "cephfs-dms", target: "artifacts", scan_count: 3, last_scan_at: "2026-08-23T13:00:00Z" },
  { storage_name: "cephfs-dms", target: "team", scan_count: 1, last_scan_at: "2026-08-22T13:00:00Z" },
];

export const HISTORY = {
  storage_name: "cephfs-dms", target: "artifacts",
  points: [
    point({ job_id: "j1", request_id: "r1", generated_at_epoch: 1787400000, total_bytes: 1024 }),
    point({ job_id: "j2", request_id: "r2", generated_at_epoch: 1787450000,
            total_bytes: null }),               // 용량 미상 -- 차트 제외 대상
    point({ job_id: "j3", request_id: "r3", generated_at_epoch: 1787500000, total_bytes: 3072 }),
  ],
  skipped_unreadable: 1,
};

export const st = (name: string, over: Partial<Storage> = {}): Storage => ({
  storage_name: name, mount_path: `/${name}`, managed_root: `/${name}/dms`, backend_type: "cephfs", enabled: 1,
  status: "Ready", status_detail: null, ...over });
export const STORAGES = [st("cephfs-dms", { mount_path: "/cephfs", managed_root: "/cephfs/dms" })];

export const sum = (name: string, over: Partial<UsageStorageSummary> = {}): UsageStorageSummary => ({
  storage_name: name, registered: true, source: "registered", backend_type: "cephfs", mount_path: `/${name}`,
  target_count: 2, succeeded_scan_count: 4, last_scan_at: isoAgo(3), ...over });
export const SUMMARY = [sum("cephfs-dms", { mount_path: "/cephfs" })];

export const unreg = (name: string, over: Partial<UnregisteredStorage> = {}): UnregisteredStorage => ({
  storage_name: name, target_count: 1, succeeded_scan_count: 1, last_scan_at: isoAgo(200), single_count: 1,
  batches: [], request_count: 1, mixed_batches: [],
  last_known: { backend_type: "gpfs", mount_path: "/old", managed_root: "/old/m" }, ...over });

// 파일 크기 구간(dscan 실측 10구간 모양 -- 마지막 구간은 upper_inclusive 키가 없다).
export const SIZE_LIMITS = [4096, 65536, 1048576, 16777216, 268435456, 1073741824, 17179869184, 274877906944, 4398046511104];
export const sizeHist = (counts: number[]): HistogramBucket[] => counts.map((c, i) => {
  const lo = i === 0 ? 0 : SIZE_LIMITS[i - 1] + 1, hi = SIZE_LIMITS[i];
  return { bucket: hi === undefined ? `[${lo},INF]` : `[${lo},${hi}]`, lower_inclusive: lo,
           ...(hi === undefined ? {} : { upper_inclusive: hi }), count: c };
});
export const pad10 = (head: number[]) => [...head, ...Array(10 - head.length).fill(0)] as number[];

// 스토리지 합계 응답(design-1021 storage-totals 계약). 기본값 = 테스트베드 cephfs-dms 실측(최상위 4개 · 하위 5개).
export const m = (sum: number | null, known: number, unknown = 0) => ({ sum, known, unknown });
export const totalsOf = (name: string, over: Partial<UsageStorageTotals> = {}): UsageStorageTotals => ({
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
export const emptyTotals = (name: string): UsageStorageTotals => totalsOf(name, {
  target_count: 0, summed_target_count: 0, nested_target_count: 0,
  total_bytes: m(null, 0), total_files: m(null, 0), total_directories: m(null, 0), total_symlinks: m(null, 0),
  total_other: m(null, 0), total_entries: m(null, 0), scan_errors: m(null, 0), broken_paths_total: m(null, 0),
  hot_ratio: { value: null, hot_bytes: null, total_bytes: null, known: 0, unknown: 0 },
  file_size_histogram: { buckets: [], known: 0, unknown: 0 }, oldest_scan_at: null, newest_scan_at: null,
  filesystem: null });

// ---- 서버 흉내(2026-10-29 필터): /targets·/facets 는 픽스처 행의 pv·_requesters(테스트 전용 -- 그 경로를 scan 한 요청자들)로
// 대상 단위 필터·선택지를 계산한다(서버 usage_attribution 의 결합 규칙: PV 축은 같은 정체 하나가 모두 만족, PV 축이 있으면 구분은
// pv). pv 가 없는 행은 PV 밖으로 센다(filesystem 탭).
export type Row = { target: string; pv?: UsagePv; _requesters?: string[] } & Record<string, unknown>;
export const ATTR = ["cluster", "namespace", "pvc", "storage_class", "user", "scope", "requester"] as const;
export const PV_AXES = ["cluster", "namespace", "pvc", "storage_class", "user"] as const;
// 메아리: 목록·내보내기는 검색(q -- 원문)과 옛 path 를 함께 싣는다(합계는 둘 다 받지 않는다 -- withQ false).
export const echoOf = (u: URL, withQ = true): UsageFiltersEcho => ({
  storage: u.searchParams.get("storage"),
  ...(withQ ? { path: u.searchParams.get("path"), q: u.searchParams.get("q") } : {}),
  ...Object.fromEntries(ATTR.map((k) => [k, u.searchParams.get(k)])) });
export const hasAttrParams = (u: URL) => ATTR.some((k) => u.searchParams.has(k));
// 검색(q) 흉내(서버 usage_attribution.search_matches): 공백으로 나눈 토큰마다 대소문자 무시 부분 일치, 토큰 사이 AND, 토큰마다 경로
// 또는 소속 PV 이름(PVC -- inventory·recorded) 어느 쪽이든.
export function searchHit(r: Row, q: string): boolean {
  const hay = [r.target, ...(r.pv?.volumes ?? []).map((v) => v.pvc)].map((s) => s.toLowerCase());
  return q.split(/\s+/).filter(Boolean).every((t) => hay.some((h) => h.includes(t.toLowerCase())));
}
export function serverFilter(rows: Row[], u: URL): Row[] {
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
export function facetsFrom(name: string, rows: Row[]): UsageFacets {
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
export const strip = ({ _requesters, ...r }: Row) => r;   // eslint-disable-line @typescript-eslint/no-unused-vars

// 지금 URL(경로·검색 문자열)과 마지막 이동 종류(PUSH·REPLACE·POP)를 화면에 꺼내 둔다 -- 스토리지 전환·탭 이동·시트 열기/닫기가
// URL 에 무엇을 어떻게(push 인가 replace 인가 뒤로인가) 남기는지 단언한다. 시트가 열린 동안 뒤 화면은 aria-hidden 이지만 testid 는
// 읽힌다.
// 브라우저 뒤로·앞으로 가기는 숨은 버튼(back·forward -- fireEvent 로 누른다, 역할 질의에는 잡히지 않는다)으로 흉내 낸다.
export function LocationProbe() {
  const loc = useLocation();
  const nav = useNavigationType();
  const navigate = useNavigate();
  return <><div data-testid="path">{loc.pathname}</div><div data-testid="loc">{loc.search}</div>
    <div data-testid="nav">{nav}</div><button type="button" hidden data-testid="back" onClick={() => navigate(-1)} />
    <button type="button" hidden data-testid="forward" onClick={() => navigate(1)} /></>;
}
export const loc = () => new URLSearchParams(screen.getByTestId("loc").textContent ?? "");
export const pathname = () => screen.getByTestId("path").textContent;
export const navType = () => screen.getByTestId("nav").textContent;

export interface Setup {
  path?: string;
  entries?: string[];                // 히스토리 항목 여럿(path 대신) -- 지금 항목은 마지막(뒤로·앞으로 가기 검사)
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
  filterQ?: boolean;                 // q 파라미터로 행을 거른다(searchHit -- 서버 검색 흉내)
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

export function setup(o: Setup = {}) {
  const summary = o.summary ?? SUMMARY;
  const totalsCalls: Record<string, number> = {};
  // 기본 합계는 setup 때 한 번 만든다 -- 요청을 받을 때 만들면 scan 시점(isoAgo)이 화면의 now(그보다 먼저 그린 렌더)보다 늦어
  // 「65일 전」이 「64일 전」으로 읽히는 경합이 생긴다(전체 실행의 부하에서 드물게 실패했다).
  const defaultTotals: Record<string, UsageStorageTotals> = { "cephfs-dms": totalsOf("cephfs-dms") };
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
      const q = u.searchParams.get("q");
      if (o.filterQ && q) rows = rows.filter((r) => searchHit(r, q));
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
        : (o.totals ?? defaultTotals)[s] ?? emptyTotals(s);
      if (t === "error") return HttpResponse.json({ detail: "internal" }, { status: 500 });
      if (t === "html") {
        return new HttpResponse("<!doctype html><html></html>", { status: 200, headers: { "content-type": "text/html" } });
      }
      // 필터 합계(필터 파라미터가 있을 때)는 메아리를 싣는다(검색은 받지 않는다 -- 메아리에도 없다).
      return HttpResponse.json(hasAttrParams(u)
        ? { ...t, filesystem: null, filters: o.totalsEcho ? o.totalsEcho(u) : echoOf(u, false) } : t);
    }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={o.entries ?? [o.path ?? "/admin/usage"]}>
        {/* 하위 탭 둘(평평한 라우트 -- router.tsx 와 같은 모양). 그 밖(PV 탐색·단일 작업 링크)은 빈 화면. */}
        <Routes>
          <Route path="/admin/usage" element={<UsageAnalysis />} />
          <Route path="/admin/usage/storages" element={<StoragesPage />} />
          <Route path="*" element={null} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>);
  return qc;
}

// 경로 상세 시트(2026-10-29): 이름 = 경로(머리 h2). 모달이라 열린 동안 뒤 화면(표·탭·필터)은 aria-hidden -- role 로 못 찾는다.
// 스토리지 select(2026-10-29 -- 예전 스토리지 탭 줄). 옵션 값은 「s:이름」(StorageSelect). 고르면 300ms 디바운스 뒤 push 라 URL 이
// 바뀔 때까지 기다린다(기본 스토리지를 다시 고르면 URL 에 적힌다).
export const storageSelect = () => screen.getByRole("combobox", { name: "스토리지" });
export async function chooseStorage(name: string) {
  await userEvent.selectOptions(storageSelect(), `s:${name}`);
  await waitFor(() => expect(loc().get("storage")).toBe(name));
}
// 패널 = 스토리지 이름의 구역(예전 탭 패널).
export const panelOf = (name: string) => screen.getByRole("region", { name });

export const sheetOf = (target: string) => screen.findByRole("dialog", { name: target });
export const closeSheet = async () => {
  await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "닫기" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
};

// ---- 필터(2026-10-29 「사용자·PV 이름·namespace 등으로 필터」) ----
// 픽스처 = 테스트베드 모양: k8s-pv-nfs 가 두 클러스터(dmsk8s 의 tb-nfs, dmsk8s2 의 c2-nfs)의 PV 를 담고, 지운 PVC 의 기록 하나,
// 직접 경로 scan 하나(PV 밖). cephfs-dms 는 filesystem(전부 PV 밖) -- 요청자만 둘.

export const NFS = "k8s-pv-nfs";
export const vol = (cluster: string, namespace: string, pvc: string, volume: string, sc: string) =>
  ({ cluster, namespace, pvc, volume, storage_class: sc });
export const inv = (vs: UsagePv["volumes"], users: string[], root: string): UsagePv => ({ source: "inventory", root, volumes: vs, users });
export const OUTSIDE: UsagePv = { source: "outside", root: null, volumes: [], users: [] };
export const nrow = (target: string, pv: UsagePv, requesters: string[], days = 1) =>
  ({ storage_name: NFS, target, scan_count: requesters.length, last_scan_at: isoAgo(days), pv, _requesters: requesters });
export const DATASETS = vol("dmsk8s", "team-a", "datasets", "pvc-aaa", "tb-nfs");
export const NFS_ROWS = [
  nrow("team-a/datasets/dms-proof", inv([DATASETS], ["alice", "junwoo.song"], "team-a/datasets"), ["alice", "mason"]),
  nrow("team-a/datasets/proof-scan", inv([DATASETS], ["alice", "junwoo.song"], "team-a/datasets"), ["mason"], 2),
  nrow("team-b/models", inv([vol("dmsk8s", "team-b", "models", "pvc-bbb", "tb-nfs")], [], "team-b/models"), ["mason"], 3),
  nrow("c2/lab-y/weights", inv([vol("dmsk8s2", "lab-y", "weights", "pvc-ccc", "c2-nfs")], ["cocoa.song"], "c2/lab-y/weights"),
       ["mason"], 4),
  nrow("old/gone", { source: "recorded", root: "old/gone", volumes: [vol("dmsk8s", "team-a", "gone", "pvc-ddd", "tb-nfs")],
                     users: ["alice"] }, ["alice"], 5),
  nrow("proof-d9/alice/latest", OUTSIDE, ["mason"], 6),
];
export const FS_ROWS = TARGETS.map((r, i) => ({ ...r, pv: OUTSIDE, _requesters: i === 0 ? ["alice", "mason"] : ["mason"] }));
export const pvSetup = (o: Setup = {}) => setup({
  storages: [st("cephfs-dms"), st(NFS, { backend_type: "nfs" })],
  summary: [sum("cephfs-dms"), sum(NFS, { target_count: NFS_ROWS.length })],
  targets: { "cephfs-dms": FS_ROWS, [NFS]: NFS_ROWS }, ...o });

export const filterGroup = () => screen.findByRole("group", { name: "사용량 필터" });
export const select = (label: string) => within(screen.getByRole("group", { name: "사용량 필터" })).getByRole("combobox", { name: label });
export const optionTexts = (el: HTMLElement) => within(el).getAllByRole("option").map((o) => o.textContent);
