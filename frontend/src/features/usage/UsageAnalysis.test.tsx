import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";
import { UsageAnalysis, hotRatio, pointEpoch, stackLayout } from "./UsageAnalysis";
import type { UsagePoint } from "../../lib/types";

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

// ---- 화면 ----

const TARGETS = [
  { storage_name: "cephfs-dms", target: "artifacts", scan_count: 3,
    last_scan_at: "2026-08-23T13:00:00Z" },
  { storage_name: "cephfs-dms", target: "team", scan_count: 1,
    last_scan_at: "2026-08-22T13:00:00Z" },
];

const point = (over: Partial<UsagePoint>): UsagePoint => ({
  job_id: "j1", request_id: "r1", finished_at: "2026-08-23T13:00:00Z",
  generated_at_epoch: 1787500000, total_bytes: 1024,
  summary: { total_files: 7 },
  time_histograms: { atime: buckets([1024, 0]) as never,
                     mtime: buckets([512, 512]) as never },
  requester: "alice", ...over });

const HISTORY = {
  storage_name: "cephfs-dms", target: "artifacts",
  points: [
    point({ job_id: "j1", request_id: "r1", generated_at_epoch: 1787400000,
            total_bytes: 1024 }),
    point({ job_id: "j2", request_id: "r2", generated_at_epoch: 1787450000,
            total_bytes: null }),               // 용량 미상 -- 차트 제외 대상
    point({ job_id: "j3", request_id: "r3", generated_at_epoch: 1787500000,
            total_bytes: 3072 }),
  ],
  skipped_unreadable: 1,
};

function renderAt(path = "/admin/usage") {
  server.use(
    http.get("/api/admin/usage/scan-targets", () => HttpResponse.json(TARGETS)),
    http.get("/api/admin/usage/scan-history", () => HttpResponse.json(HISTORY)),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}><UsageAnalysis /></MemoryRouter>
    </QueryClientProvider>);
}

test("타깃 목록: 스토리지·경로·스캔 횟수·최근 스캔", async () => {
  renderAt();
  expect(await screen.findByText("artifacts")).toBeInTheDocument();
  expect(screen.getByText("team")).toBeInTheDocument();
  expect(screen.getByText("3")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "사용량 분석" })).toBeInTheDocument();
});

test("타깃 선택 → 요약 타일·추이 차트·미상 고지·이력 표", async () => {
  renderAt();
  await userEvent.click(await screen.findByRole("button", { name: "artifacts" }));
  // 요약 타일: 최신 3.0 KiB, 직전(1.0 KiB) 대비 +2.0 KiB, hot 100%(atime 첫 버킷 전부).
  // 값은 이력 표·차트 라벨에도 나오므로 타일(라벨 다음 형제) 범위로 좁힌다.
  const tile = async (label: string) =>
    (await screen.findByText(label)).nextElementSibling;
  expect(await tile("최신 실 사용량")).toHaveTextContent("3.0 KiB");
  expect(await tile("직전 스캔 대비")).toHaveTextContent("+2.0 KiB");
  expect(await tile("hot 비율(atime ≤180d)")).toHaveTextContent("100%");
  expect(await tile("스캔 이력 수")).toHaveTextContent("3");
  // 차트: 용량 아는 2점만(j2 는 미상), 고지 문구가 사실을 말한다
  expect(screen.getByRole("group", { name: "실 사용량 추이" })).toBeInTheDocument();
  expect(screen.getByText(/용량 미상 1건은 차트에서 제외/)).toBeInTheDocument();
  expect(screen.getByText(/리포트를 읽지 못한 스캔 1건 제외/)).toBeInTheDocument();
  // 이력 표: 최신이 위(reverse), 요청 상세 링크
  expect(screen.getByRole("link", { name: "r3" })).toHaveAttribute("href", "/jobs/r3");
  // 요청자는 포인트 3건 모두 alice — 이력 표에 행 수만큼 나온다
  expect(screen.getAllByText("alice")).toHaveLength(HISTORY.points.length);
  // 상세는 별도 카드가 아니라 **선택한 행 바로 아래 펼침 행**에 있다(2026-09-14)
  expect(screen.getByRole("button", { name: "artifacts" })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("region", { name: "cephfs-dms:artifacts 상세" })).toBeInTheDocument();
});

test("펼침은 한 번에 하나: 재클릭 = 접기, 다른 행 클릭 = 펼침 이동, 행 바로 아래에 렌더", async () => {
  renderAt();
  const artifacts = await screen.findByRole("button", { name: "artifacts" });
  const team = screen.getByRole("button", { name: "team" });
  expect(artifacts).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("region")).toBeNull();
  await userEvent.click(artifacts);
  const region = await screen.findByRole("region", { name: "cephfs-dms:artifacts 상세" });
  // 펼침 행은 선택 행의 **다음 형제 행**이다(목록 아래 별도 카드가 아님)
  const row = artifacts.closest("tr")!;
  expect(row.nextElementSibling).toContainElement(region);
  expect(team).toHaveAttribute("aria-expanded", "false");
  // 다른 행 클릭 -> 펼침이 옮겨 가고 region 은 하나뿐
  await userEvent.click(team);
  await screen.findByRole("region", { name: "cephfs-dms:team 상세" });
  expect(screen.getAllByRole("region")).toHaveLength(1);
  expect(screen.getByRole("button", { name: "artifacts" })).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByRole("button", { name: "team" })).toHaveAttribute("aria-expanded", "true");
  // 같은 행 재클릭 -> 접힘(URL 파라미터 제거)
  await userEvent.click(screen.getByRole("button", { name: "team" }));
  expect(screen.queryByRole("region")).toBeNull();
  expect(screen.getByRole("button", { name: "team" })).toHaveAttribute("aria-expanded", "false");
});

test("딥링크 타깃이 현재 목록에 없으면 표 아래 폴백 카드로 표시한다", async () => {
  server.use(
    http.get("/api/admin/usage/scan-targets", () => HttpResponse.json([TARGETS[1]])),  // team 만
    http.get("/api/admin/usage/scan-history", () => HttpResponse.json(HISTORY)),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/admin/usage?storage=cephfs-dms&target=artifacts"]}>
        <UsageAnalysis />
      </MemoryRouter>
    </QueryClientProvider>);
  expect(await screen.findByText(/현재 목록\(검색 결과\)에 없어/)).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "cephfs-dms:artifacts 상세" })).toBeInTheDocument();
  expect(await screen.findByText("최신 실 사용량")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /cephfs-dms.*artifacts/ })).toBeInTheDocument();
});

test("URL 파라미터 딥링크로 바로 상세가 열린다", async () => {
  renderAt("/admin/usage?storage=cephfs-dms&target=artifacts");
  expect(await screen.findByText("최신 실 사용량")).toBeInTheDocument();
});

test("온도 추이: atime 기본 + mtime 토글", async () => {
  renderAt("/admin/usage?storage=cephfs-dms&target=artifacts");
  expect(await screen.findByRole("img", { name: "데이터 온도 추이(atime)" }))
    .toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "mtime" }));
  expect(screen.getByRole("img", { name: "데이터 온도 추이(mtime)" }))
    .toBeInTheDocument();
});

test("온도 추이 열 호버 즉시 툴팁(시간·요청자·용량)", async () => {
  renderAt("/admin/usage?storage=cephfs-dms&target=artifacts");
  const chart = await screen.findByRole("img", { name: "데이터 온도 추이(atime)" });
  expect(screen.queryByRole("tooltip")).toBeNull();     // 호버 전엔 없음
  // 첫 열 버튼(차트 내부)에 호버 -> 즉시 툴팁
  const col = within(chart).getAllByRole("button")[0];
  await userEvent.hover(col);
  const tip = screen.getByRole("tooltip");
  expect(tip).toHaveTextContent("시간");
  expect(tip).toHaveTextContent("요청자");
  expect(tip).toHaveTextContent("실 사용량");
  await userEvent.unhover(col);
  expect(screen.queryByRole("tooltip")).toBeNull();
});

test("atime 이 없는 이력은 첫 가용 축(mtime)으로 자동 대체 -- '분포 없음' 거짓 방지", async () => {
  const mtimeOnly = {
    ...HISTORY,
    points: HISTORY.points.map((p) => ({
      ...p, time_histograms: { mtime: p.time_histograms.mtime } })),
  };
  server.use(
    http.get("/api/admin/usage/scan-targets", () => HttpResponse.json(TARGETS)),
    http.get("/api/admin/usage/scan-history", () => HttpResponse.json(mtimeOnly)),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/admin/usage?storage=cephfs-dms&target=artifacts"]}>
        <UsageAnalysis />
      </MemoryRouter>
    </QueryClientProvider>);
  expect(await screen.findByRole("img", { name: "데이터 온도 추이(mtime)" }))
    .toBeInTheDocument();
  expect(screen.queryByText("이 축의 온도 분포가 있는 스캔이 없습니다")).toBeNull();
});

test("창 안내는 상시 + window_full 이면 그 이전 이력 존재까지 말한다", async () => {
  server.use(
    http.get("/api/admin/usage/scan-targets", () => HttpResponse.json(TARGETS)),
    http.get("/api/admin/usage/scan-history", () =>
      HttpResponse.json({ ...HISTORY, window_limit: 30, window_full: true })),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/admin/usage?storage=cephfs-dms&target=artifacts"]}>
        <UsageAnalysis />
      </MemoryRouter>
    </QueryClientProvider>);
  // 안내는 데이터 도착 전에도(폴백 30) 뜨므로, 접미문 자체를 기다린다.
  const note = await screen.findByText(/그 이전 스캔 이력도 있습니다/);
  expect(note).toHaveTextContent("최근 30건까지만 표시합니다");
});

test("표시 창 선택: 최근 60건 클릭 -> limit=60 으로 재조회", async () => {
  const limits: (string | null)[] = [];
  server.use(
    http.get("/api/admin/usage/scan-targets", () => HttpResponse.json(TARGETS)),
    http.get("/api/admin/usage/scan-history", ({ request }) => {
      limits.push(new URL(request.url).searchParams.get("limit"));
      return HttpResponse.json({ ...HISTORY,
                                 window_limit: Number(limits[limits.length - 1]),
                                 window_full: false });
    }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/admin/usage?storage=cephfs-dms&target=artifacts"]}>
        <UsageAnalysis />
      </MemoryRouter>
    </QueryClientProvider>);
  // 기본 30 으로 첫 조회(데이터 도착까지 대기) + 상시 안내(접미문 없음)
  await screen.findByText("최신 실 사용량");
  expect(limits).toEqual(["30"]);
  const note = screen.getByText(/최근 30건까지만 표시합니다/);
  expect(note).not.toHaveTextContent("그 이전 스캔 이력도");
  await userEvent.click(screen.getByRole("button", { name: "최근 60건" }));
  await screen.findByText(/최근 60건까지만 표시합니다/);
  await waitFor(() => expect(limits).toEqual(["30", "60"]));
});

test("검색어가 쿼리로 나간다(디바운스 후)", async () => {
  let lastQ: string | null = null;
  server.use(
    http.get("/api/admin/usage/scan-targets", ({ request }) => {
      lastQ = new URL(request.url).searchParams.get("q");
      return HttpResponse.json([]);
    }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><UsageAnalysis /></MemoryRouter>
    </QueryClientProvider>);
  await userEvent.type(screen.getByLabelText("경로 검색"), "artif");
  await waitFor(() => expect(lastQ).toBe("artif"));
  expect(screen.getByText("검색과 일치하는 scan 타깃이 없습니다")).toBeInTheDocument();
});
