import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { BatchDetail } from "./BatchDetail";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());
const batch = (over: any = {}) => ({ batch_id:"b1", operation:"sync", status:"PreviewReady",
  max_concurrency:2, item_count:1, succeeded_count:0, failed_count:0, note:null, created_at:"",
  items:[{seq:0, payload:{source:"a"}, status:"Materialized", request_id:"r1", reason_code:null}], ...over });

function renderAt(state: string) {
  server.use(http.get("/api/admin/batches/b1", () => HttpResponse.json(batch({status:state}))));
  const qc = new QueryClient({ defaultOptions:{ queries:{ retry:false }}});
  return render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/admin/batches/b1"]}>
    <Routes><Route path="/admin/batches/:batchId" element={<BatchDetail/>} /></Routes>
  </MemoryRouter></QueryClientProvider>);
}

test("PreviewReady shows confirm button and posts confirm", async () => {
  let confirmed = false;
  server.use(http.post("/api/admin/batches/b1:confirm", () => { confirmed = true; return HttpResponse.json({status:"Running"}); }));
  renderAt("PreviewReady");
  await userEvent.click(await screen.findByRole("button", { name: "배치 확인" }));
  // userEvent.click 은 fetch 착지를 보장하지 않는다 -- 단언을 waitFor 로 감싸 플레이키를 없앤다.
  await waitFor(() => expect(confirmed).toBe(true));
});
test("renders items list with status", async () => {
  renderAt("Running");
  expect(await screen.findByText("Materialized")).toBeInTheDocument();
});

// --- 결과 항목 상세화: 접힘 기본 → 펼침 상세 → 재클릭 접힘 ---
const detailedBatch = () => batch({ operation: "scan", status: "Completed", items: [
  { seq: 0, payload: { storage: "s1", target: "team" }, status: "Succeeded",
    request_id: "r1", reason_code: null, request_state: "Succeeded",
    files_count: 42, completed_at: "2026-08-14T01:00:00Z" },
  { seq: 1, payload: { storage: "s1", target: "proj" }, status: "Failed",
    request_id: "r2", reason_code: "execution_failed", request_state: "Failed",
    files_count: null, completed_at: "2026-08-14T02:00:00Z" },
]});

function renderDetailed() {
  server.use(http.get("/api/admin/batches/b1", () => HttpResponse.json(detailedBatch())));
  const qc = new QueryClient({ defaultOptions:{ queries:{ retry:false }}});
  return render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/admin/batches/b1"]}>
    <Routes><Route path="/admin/batches/:batchId" element={<BatchDetail/>} /></Routes>
  </MemoryRouter></QueryClientProvider>);
}

test("항목 행은 접힘 기본: 순번·대상 요약·상태만 — 상세 미렌더", async () => {
  renderDetailed();
  // 대상 요약은 대시보드 summarize 관례 미러(scan: storage:target)
  expect(await screen.findByText("scan · s1:team")).toBeInTheDocument();
  expect(screen.getByText("Succeeded")).toBeInTheDocument();
  expect(screen.queryByText("파일 수")).toBeNull();
  expect(screen.queryByText("42")).toBeNull();
});

test("펼침: aria-expanded 토글 + 상세(사유·파일 수·완료 시각·요청 링크) → 재클릭 접힘", async () => {
  renderDetailed();
  const toggle = await screen.findByRole("button", { name: "항목 0 상세" });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  await userEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByText("파일 수")).toBeInTheDocument();
  expect(screen.getByText("42")).toBeInTheDocument();
  expect(screen.getByText("2026-08-14T01:00:00Z")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "요청 상세" }))
    .toHaveAttribute("href", "/jobs/r1");
  await userEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("파일 수")).toBeNull();
});

test("실패 항목 펼침: 사유는 reasonText 한글 문구, 파일 수 null 은 — (null≠0)", async () => {
  renderDetailed();
  await userEvent.click(await screen.findByRole("button", { name: "항목 1 상세" }));
  expect(screen.getByText("실행에 실패했습니다")).toBeInTheDocument();
  expect(screen.getByText("파일 수")).toBeInTheDocument();
  const dd = screen.getByText("파일 수").nextElementSibling as HTMLElement;
  expect(dd.textContent).toBe("—");
});
test("Completed 는 전체 재실행 버튼 노출 + :rescan 발사", async () => {
  let rescanned = false;
  server.use(http.post("/api/admin/batches/b1:rescan", () => { rescanned = true; return HttpResponse.json({status:"Running", requeued:1}); }));
  renderAt("Completed");
  await userEvent.click(await screen.findByRole("button", { name: "전체 재실행" }));
  // userEvent.click 은 fetch 착지를 보장하지 않는다 -- 단언을 waitFor 로 감싸 플레이키를 없앤다.
  await waitFor(() => expect(rescanned).toBe(true));
});
test("Cancelled 도 전체 재실행 버튼 노출", async () => {
  renderAt("Cancelled");
  expect(await screen.findByRole("button", { name: "전체 재실행" })).toBeInTheDocument();
});
test("Running 에선 전체 재실행 버튼 부재", async () => {
  renderAt("Running");
  await screen.findByText("Materialized");   // 렌더 완료 대기 후 부재 단언
  expect(screen.queryByRole("button", { name: "전체 재실행" })).toBeNull();
});
test("owner_username 이 있으면 소유자 표시 + 특권 실행 문구", async () => {
  server.use(http.get("/api/admin/batches/b1",
    () => HttpResponse.json(batch({ owner_username: "alice" }))));
  const qc = new QueryClient({ defaultOptions:{ queries:{ retry:false }}});
  render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/admin/batches/b1"]}>
    <Routes><Route path="/admin/batches/:batchId" element={<BatchDetail/>} /></Routes>
  </MemoryRouter></QueryClientProvider>);
  expect(await screen.findByText("소유자 alice")).toBeInTheDocument();
  expect(screen.getByText("특권 실행(root)")).toBeInTheDocument();
});
test("owner_username 이 없어도 특권 실행 문구는 항상 — 소유자 행만 부재", async () => {
  // 통일 게이트 후 배치는 전부 특권 실행이다. 행별 auth_method/owner 로 재판정하지
  // 않는다(프론트는 allowlist 를 모른다 — 판정 흉내가 더 큰 거짓말).
  renderAt("Running");
  await screen.findByText("Materialized");   // 렌더 완료 대기 후 부재 단언
  expect(screen.getByText("특권 실행(root)")).toBeInTheDocument();
  expect(screen.queryByText(/소유자/)).toBeNull();
});
// --- 데이터 온도(hot/cold): scan 배치 + 집계 ≥1 일 때만 ---
const scanStats = (over: any = {}) => ({ aggregated: 2, skipped: 1,
  summary: { total_files: 12, total_entries: 20 },
  file_size_histogram: [{ bucket: "[0,4096]", count: 11 }],
  time_histograms: {
    atime: [{ bucket: "[0d,1d]", bytes: 2048 }, { bucket: "[1d,7d]", bytes: 0 }],
    mtime: [{ bucket: "[0d,1d]", bytes: 4096 }],
    ctime: [] },
  broken_paths_total: 4, ...over });

function renderScanWithStats(stats: object = scanStats()) {
  server.use(
    http.get("/api/admin/batches/b1",
      () => HttpResponse.json(batch({ operation: "scan", status: "Completed" }))),
    http.get("/api/admin/batches/b1/scan-stats", () => HttpResponse.json(stats)));
  const qc = new QueryClient({ defaultOptions:{ queries:{ retry:false }}});
  return render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/admin/batches/b1"]}>
    <Routes><Route path="/admin/batches/:batchId" element={<BatchDetail/>} /></Routes>
  </MemoryRouter></QueryClientProvider>);
}

test("scan 배치: 데이터 온도 섹션 — atime bytes 기본·사람 표기·정직 카운트·캡션", async () => {
  renderScanWithStats();
  expect(await screen.findByText("데이터 온도(hot/cold)")).toBeInTheDocument();
  // atime 기준 bytes 히스토그램이 기본 — 값은 사람 표기(humanBytes)
  expect(screen.getByRole("img", { name: "데이터 온도(atime) 히스토그램" })).toBeInTheDocument();
  expect(screen.getByText("2.0 KiB")).toBeInTheDocument();
  expect(screen.getByText(/최근 접근\(atime\) 기준 용량 비중/)).toBeInTheDocument();
  expect(screen.getByText(/relatime\/open_noatime 환경에선 근사/)).toBeInTheDocument();
  // 정직 카운트: 합산·제외가 그대로 보인다(제외를 숨기면 조용한 절단)
  expect(screen.getByText("합산 리포트 2건 · 제외 1건")).toBeInTheDocument();
  // 파일 크기 분포(count)·summary 합계·파손 경로 합계도 같은 섹션에
  expect(screen.getByRole("img", { name: "파일 크기 분포" })).toBeInTheDocument();
  expect(screen.getByText("total_files")).toBeInTheDocument();
  expect(screen.getByText("12")).toBeInTheDocument();
  expect(screen.getByText(/파손 경로 합계 4건/)).toBeInTheDocument();
});

test("mtime 토글: atime 차트가 mtime 으로 바뀐다", async () => {
  renderScanWithStats();
  await screen.findByText("데이터 온도(hot/cold)");
  await userEvent.click(screen.getByRole("button", { name: "mtime" }));
  expect(screen.getByRole("img", { name: "데이터 온도(mtime) 히스토그램" })).toBeInTheDocument();
  expect(screen.queryByRole("img", { name: "데이터 온도(atime) 히스토그램" })).toBeNull();
  expect(screen.getByText("4.0 KiB")).toBeInTheDocument();
});

test("집계 0 이면 데이터 온도 섹션 부재", async () => {
  renderScanWithStats(scanStats({ aggregated: 0, skipped: 3,
    summary: {}, file_size_histogram: [], time_histograms: {},
    broken_paths_total: null }));
  await screen.findByText("Materialized");
  expect(screen.queryByText("데이터 온도(hot/cold)")).toBeNull();
});

test("sync 배치는 scan-stats 요청 자체가 없다", async () => {
  let statsCalls = 0;
  server.use(http.get("/api/admin/batches/b1/scan-stats", () => {
    statsCalls += 1; return HttpResponse.json(scanStats()); }));
  renderAt("Running");                          // 기본 fixture 는 sync 배치
  await screen.findByText("Materialized");
  expect(statsCalls).toBe(0);
  expect(screen.queryByText("데이터 온도(hot/cold)")).toBeNull();
});

test("이름이 있으면 헤더는 이름, 축약 batch_id 는 병기", async () => {
  server.use(http.get("/api/admin/batches/b1",
    () => HttpResponse.json(batch({ name: "8월 정기 스캔 1차" }))));
  const qc = new QueryClient({ defaultOptions:{ queries:{ retry:false }}});
  render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/admin/batches/b1"]}>
    <Routes><Route path="/admin/batches/:batchId" element={<BatchDetail/>} /></Routes>
  </MemoryRouter></QueryClientProvider>);
  expect(await screen.findByRole("heading", { name: "8월 정기 스캔 1차" })).toBeInTheDocument();
  expect(screen.getByText("b1")).toBeInTheDocument();   // 식별자는 사라지지 않는다
});
test("이름이 없으면 기존 축약 batch_id 헤더 유지", async () => {
  renderAt("Running");
  expect(await screen.findByRole("heading", { name: "배치 b1" })).toBeInTheDocument();
});
test("이름·메모 인라인 편집: 저장이 PATCH 를 쏘고 편집을 닫는다", async () => {
  let patched: any = null;
  server.use(http.patch("/api/admin/batches/b1", async ({ request }) => {
    patched = await request.json();
    return HttpResponse.json(batch({ name: "새 이름", note: "새 메모" }));
  }));
  renderAt("Running");
  await userEvent.click(await screen.findByRole("button", { name: "이름·메모 편집" }));
  await userEvent.type(screen.getByLabelText("배치 이름"), "새 이름");
  await userEvent.type(screen.getByLabelText("메모"), "새 메모");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await waitFor(() => expect(patched).toEqual({ name: "새 이름", note: "새 메모" }));
  // 저장 성공 후 편집 종료(입력 부재)
  await waitFor(() => expect(screen.queryByLabelText("배치 이름")).toBeNull());
});
test("편집 취소는 PATCH 없이 닫힌다", async () => {
  let patchCalls = 0;
  server.use(http.patch("/api/admin/batches/b1", () => { patchCalls += 1;
    return HttpResponse.json(batch()); }));
  renderAt("Running");
  await userEvent.click(await screen.findByRole("button", { name: "이름·메모 편집" }));
  // 배치 취소 버튼("취소")과 겹치지 않는 라벨 — 편집 취소는 별개 동작이다
  await userEvent.click(screen.getByRole("button", { name: "편집 취소" }));
  expect(screen.queryByLabelText("배치 이름")).toBeNull();
  expect(patchCalls).toBe(0);
});
test("PreviewReady also shows cancel button and posts cancel", async () => {
  let cancelled = false;
  server.use(http.post("/api/admin/batches/b1:cancel", () => { cancelled = true; return HttpResponse.json({status:"Cancelled"}); }));
  renderAt("PreviewReady");
  await userEvent.click(await screen.findByRole("button", { name: "취소" }));
  // userEvent.click 은 fetch 착지를 보장하지 않는다 -- 단언을 waitFor 로 감싸 플레이키를 없앤다.
  await waitFor(() => expect(cancelled).toBe(true));
});
