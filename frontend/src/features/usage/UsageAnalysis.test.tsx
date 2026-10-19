import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import { setupServer } from "msw/node";
import { delay, http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest";
import { UsageAnalysis, hotRatio, pointEpoch, stackLayout, staleClass } from "./UsageAnalysis";
import type { Storage, UnregisteredStorage, UsagePoint, UsageStorageSummary } from "../../lib/types";
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

// 지금 URL(검색 문자열)을 화면에 꺼내 둔다 -- 탭 전환·행 접기가 URL 에 무엇을 남기는지 단언한다.
function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.search}</div>;
}
const loc = () => new URLSearchParams(screen.getByTestId("loc").textContent ?? "");

interface Setup {
  path?: string;
  storages?: Storage[];
  summary?: UsageStorageSummary[] | "error" | "slow";
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
}

function setup(o: Setup = {}) {
  const summary = o.summary ?? SUMMARY;
  server.use(
    http.get("/api/admin/storages", () => HttpResponse.json(o.storages ?? STORAGES)),
    http.get("/api/admin/usage/storages", async () => {
      if (summary === "error") return HttpResponse.json({ detail: "internal" }, { status: 500 });
      if (summary === "slow") { await delay("infinite"); return HttpResponse.json({}); }
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
  expect(panel).toHaveTextContent("/cephfscephfs대상 2개최근 스캔 3일 전");
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
  expect(await screen.findByText(/스토리지별 요약을 불러오지 못했습니다/)).toBeInTheDocument();
  // 기록 있는 등록 탭이 기본(scan 기록 이름으로 판정)
  await waitFor(() => expect(screen.getByRole("tab", { name: "cephfs-dms" })).toHaveAttribute("aria-selected", "true"));
  expect(screen.getByRole("tab", { name: "a-empty" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "old-gpfs 미등록 대상 1개" })).toBeInTheDocument();  // 미등록 목록은 규모를 안다
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
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
  await userEvent.click(screen.getByRole("tab", { name: /^cephfs-dms/ }));
  expect(screen.getByText(/typo-name/)).toBeInTheDocument();      // 같은 탭 재클릭은 아무것도 안 한다
  // 다른 탭을 고르면 안내는 사라진다
  await userEvent.click(screen.getByRole("tab", { name: /^other/ }));
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
  const stat = async (label: string) => (await screen.findByText(label, { selector: "dt" })).nextElementSibling;
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
  expect(screen.queryByRole("region")).toBeNull();
  await userEvent.click(artifacts);
  const region = await screen.findByRole("region", { name: "cephfs-dms:artifacts 상세" });
  expect(artifacts.closest("tr")!.nextElementSibling).toContainElement(region);
  expect(team).toHaveAttribute("aria-expanded", "false");
  await userEvent.click(team);
  await screen.findByRole("region", { name: "cephfs-dms:team 상세" });
  expect(screen.getAllByRole("region")).toHaveLength(1);
  expect(screen.getByRole("button", { name: "artifacts" })).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByRole("button", { name: "team" })).toHaveAttribute("aria-expanded", "true");
  await userEvent.click(screen.getByRole("button", { name: "team" }));
  expect(screen.queryByRole("region")).toBeNull();
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
  expect(await screen.findByText("‘zzz’ 와 일치하는 경로가 없습니다")).toBeInTheDocument();
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
