import { render, screen, waitFor } from "@testing-library/react";
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

test("hotRatio: ≤7일 버킷 비중 / 전체 0·bytes 결측은 null(모름 ≠ 0)", () => {
  // 나이 0-1d(hot) 30 + 1-2d(hot) 30 + 8-9d ... max_age 1,2 는 ≤7 -- 60/100
  const b = [{ bucket: "a", min_age_days: 0, max_age_days: 1, bytes: 30 },
             { bucket: "b", min_age_days: 2, max_age_days: 7, bytes: 30 },
             { bucket: "c", min_age_days: 8, max_age_days: 30, bytes: 40 }];
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
  expect(await tile("hot 비율(atime ≤7d)")).toHaveTextContent("100%");
  expect(await tile("스캔 이력 수")).toHaveTextContent("3");
  // 차트: 용량 아는 2점만(j2 는 미상), 고지 문구가 사실을 말한다
  expect(screen.getByRole("group", { name: "실 사용량 추이" })).toBeInTheDocument();
  expect(screen.getByText(/용량 미상 1건은 차트에서 제외/)).toBeInTheDocument();
  expect(screen.getByText(/리포트를 읽지 못한 스캔 1건 제외/)).toBeInTheDocument();
  // 이력 표: 최신이 위(reverse), 요청 상세 링크
  expect(screen.getByRole("link", { name: "r3" })).toHaveAttribute("href", "/jobs/r3");
  // 요청자는 포인트 3건 모두 alice — 이력 표에 행 수만큼 나온다
  expect(screen.getAllByText("alice")).toHaveLength(HISTORY.points.length);
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

test("창이 꽉 차면(window_full) 최근 N건 창 고지가 뜬다", async () => {
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
  expect(await screen.findByText(/최근 30건 창 기준/)).toBeInTheDocument();
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
