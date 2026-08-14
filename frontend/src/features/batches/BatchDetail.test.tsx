import { render, screen, waitFor, within } from "@testing-library/react";
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
// --- 항목별 데이터 온도(hot/cold): expand 패널 안에서 그 항목의 리포트만 ---
const requestStats = (over: any = {}) => ({
  generated_at_epoch: 1785805962,
  summary: { total_files: 12, total_entries: 20 },
  file_size_histogram: [{ bucket: "[0,4096]", count: 11 }],
  time_histograms: {
    atime: [{ bucket: "[0d,1d]", bytes: 2048 }, { bucket: "[1d,7d]", bytes: 0 }],
    mtime: [{ bucket: "[0d,1d]", bytes: 4096 }],
    ctime: [] },
  broken_paths_total: 4, broken_paths_limit: 100, ...over });

// 요청 단위 엔드포인트 계약: 항목의 자식 요청(request_id) 별로 나간다.
function statsHandler(rid: string, counter: { calls: number },
                      stats: object = requestStats()) {
  return http.get(`/api/admin/requests/${rid}/scan-stats`, () => {
    counter.calls += 1; return HttpResponse.json(stats); });
}

test("펼침 전에는 조회가 없고(lazy) 배치 레벨 온도 섹션도 없다", async () => {
  const c = { calls: 0 };
  server.use(statsHandler("r1", c));
  renderDetailed();
  await screen.findByText("scan · s1:team");
  expect(c.calls).toBe(0);
  expect(screen.queryByText("데이터 온도(hot/cold)")).toBeNull();
});

test("성공 scan 항목 펼침: 조회 발사 + 온도 섹션(사람 표기·온도 색·캡션·크기 분포·요약·파손)", async () => {
  const c = { calls: 0 };
  server.use(statsHandler("r1", c));
  renderDetailed();
  await userEvent.click(await screen.findByRole("button", { name: "항목 0 상세" }));
  expect(await screen.findByText("데이터 온도(hot/cold)")).toBeInTheDocument();
  expect(c.calls).toBe(1);
  // atime 기준 bytes 히스토그램이 기본 — 값은 사람 표기(humanBytes)
  const chart = screen.getByRole("img", { name: "데이터 온도(atime) 히스토그램" });
  expect(screen.getByText("2.0 KiB")).toBeInTheDocument();
  // 온도 색: 첫 막대(hot)=빨강, 끝 막대(cold)=파랑 — 막대 수와 무관한 비례 사상
  const fills = chart.getElementsByClassName("rounded-t");
  expect(fills[0]).toHaveStyle({ backgroundColor: "#dc2626" });
  expect(fills[1]).toHaveStyle({ backgroundColor: "#6366f1" });
  // 캡션이 색의 의미를 말한다
  expect(screen.getByText(/왼쪽\(빨강\)=hot·최근 접근, 오른쪽\(파랑\)=cold/)).toBeInTheDocument();
  expect(screen.getByText(/relatime\/open_noatime 환경에선 근사/)).toBeInTheDocument();
  // 파일 크기 분포(count)는 온도가 아니다 — 기본 accent 유지
  const sizeChart = screen.getByRole("img", { name: "파일 크기 분포" });
  expect(sizeChart.getElementsByClassName("bg-accent")).toHaveLength(1);
  // 요약(그 항목의 것)·파손 경로 수·리포트 생성 시각
  expect(screen.getByText("total_files")).toBeInTheDocument();
  expect(screen.getByText("12")).toBeInTheDocument();
  expect(screen.getByText(/파손 경로 4건/)).toBeInTheDocument();
  expect(screen.getByText("scan 리포트 생성: 2026-08-04 01:12:42 UTC")).toBeInTheDocument();
});

test("mtime 토글: 펼친 항목의 atime 차트가 mtime 으로 바뀐다", async () => {
  server.use(statsHandler("r1", { calls: 0 }));
  renderDetailed();
  await userEvent.click(await screen.findByRole("button", { name: "항목 0 상세" }));
  await screen.findByText("데이터 온도(hot/cold)");
  await userEvent.click(screen.getByRole("button", { name: "mtime" }));
  expect(screen.getByRole("img", { name: "데이터 온도(mtime) 히스토그램" })).toBeInTheDocument();
  expect(screen.queryByRole("img", { name: "데이터 온도(atime) 히스토그램" })).toBeNull();
  expect(screen.getByText("4.0 KiB")).toBeInTheDocument();
});

test("온도 차트에 누적 오버레이(선+값) + 캡션 총 용량 — 크기 분포엔 없다", async () => {
  server.use(statsHandler("r1", { calls: 0 }));
  renderDetailed();
  await userEvent.click(await screen.findByRole("button", { name: "항목 0 상세" }));
  await screen.findByText("데이터 온도(hot/cold)");
  const chart = screen.getByRole("img", { name: "데이터 온도(atime) 히스토그램" });
  // atime [2048, 0]: 첫 버킷에서 이미 총합 → 누적 100%·100% (running sum)
  expect(chart.querySelector("polyline")).not.toBeNull();
  const labels = within(chart).getAllByText("100%");
  expect(labels).toHaveLength(2);
  expect(labels[0].getAttribute("title")).toBe("누적 2.0 KiB (100%)");
  // 캡션: 선의 의미 한 줄 + 총 용량 값
  expect(screen.getByText("선 = hot쪽부터의 누적 용량 비중 · 총 2.0 KiB"))
    .toBeInTheDocument();
  // 파일 크기 분포(개수)는 범위 밖 — 오버레이 없음
  expect(screen.getByRole("img", { name: "파일 크기 분포" }).querySelector("svg"))
    .toBeNull();
});

test("실패 항목 펼침: 조회 없이 '리포트 없음' — 성공 요청만 리포트를 가진다", async () => {
  const c = { calls: 0 };
  server.use(statsHandler("r2", c));
  renderDetailed();
  await userEvent.click(await screen.findByRole("button", { name: "항목 1 상세" }));
  expect(await screen.findByText("리포트 없음")).toBeInTheDocument();
  expect(c.calls).toBe(0);
  expect(screen.queryByText("데이터 온도(hot/cold)")).toBeNull();
});

test("404 no_scan_report 도 '리포트 없음' — 성공 항목인데 리포트가 사라진 경우", async () => {
  server.use(http.get("/api/admin/requests/r1/scan-stats", () =>
    HttpResponse.json({ detail: "no_scan_report" }, { status: 404 })));
  renderDetailed();
  await userEvent.click(await screen.findByRole("button", { name: "항목 0 상세" }));
  expect(await screen.findByText("리포트 없음")).toBeInTheDocument();
});

test("sync 배치 항목 펼침: 온도 섹션 자체가 없다(조회 없음)", async () => {
  const c = { calls: 0 };
  server.use(statsHandler("r1", c));
  renderAt("Completed");                        // 기본 fixture 는 sync 배치
  await userEvent.click(await screen.findByRole("button", { name: "항목 0 상세" }));
  expect(screen.getByText("payload")).toBeInTheDocument();   // 펼침 완료
  expect(c.calls).toBe(0);
  expect(screen.queryByText("데이터 온도(hot/cold)")).toBeNull();
  expect(screen.queryByText("리포트 없음")).toBeNull();
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
// --- 항목 편집(수정·삭제·추가): 종단 배치는 전 항목, 활성 배치는 Queued 만 ---

function renderBatch(over: any) {
  server.use(http.get("/api/admin/batches/b1", () => HttpResponse.json(batch(over))));
  const qc = new QueryClient({ defaultOptions:{ queries:{ retry:false }}});
  return render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/admin/batches/b1"]}>
    <Routes><Route path="/admin/batches/:batchId" element={<BatchDetail/>} /></Routes>
  </MemoryRouter></QueryClientProvider>);
}

test("종단 배치: 종단 항목 펼침에 수정·삭제 버튼 — 수정 저장이 PUT 을 쏜다", async () => {
  let sent: any = null;
  server.use(http.put("/api/admin/batches/b1/items/0", async ({ request }) => {
    sent = await request.json();
    return HttpResponse.json({ seq: 0, status: "Queued" });
  }));
  renderDetailed();                              // Completed scan 배치, 항목 0 = Succeeded
  await userEvent.click(await screen.findByRole("button", { name: "항목 0 상세" }));
  await userEvent.click(screen.getByRole("button", { name: "수정" }));
  const input = screen.getByLabelText("대상 경로");
  expect(input).toHaveValue("team");             // 현재 경로가 드래프트 초기값
  await userEvent.clear(input);
  await userEvent.type(input, "edited");
  await userEvent.click(screen.getByRole("button", { name: "항목 저장" }));
  // 스토리지는 배치 동질성 계약 — 기존 payload 의 storage 를 그대로 싣는다
  await waitFor(() => expect(sent).toEqual({ storage: "s1", target: "edited" }));
  await waitFor(() => expect(screen.queryByLabelText("대상 경로")).toBeNull());
});

test("종단 배치: 삭제 버튼이 DELETE 를 쏜다", async () => {
  let deleted = false;
  server.use(http.delete("/api/admin/batches/b1/items/1", () => {
    deleted = true; return HttpResponse.json({ deleted: 1 });
  }));
  renderDetailed();
  await userEvent.click(await screen.findByRole("button", { name: "항목 1 상세" }));
  await userEvent.click(screen.getByRole("button", { name: "삭제" }));
  await waitFor(() => expect(deleted).toBe(true));
});

test("활성 배치: Materialized 항목엔 수정·삭제 버튼이 없다", async () => {
  renderAt("Running");                           // 항목 0 = Materialized
  await userEvent.click(await screen.findByRole("button", { name: "항목 0 상세" }));
  expect(screen.getByText("payload")).toBeInTheDocument();   // 펼침 완료
  expect(screen.queryByRole("button", { name: "수정" })).toBeNull();
  expect(screen.queryByRole("button", { name: "삭제" })).toBeNull();
});

test("활성 배치: Queued 항목엔 수정·삭제 버튼이 있다", async () => {
  renderBatch({ operation: "scan", status: "Running", items: [
    { seq: 0, payload: { storage: "s1", target: "a" }, status: "Queued",
      request_id: null, reason_code: null }] });
  await userEvent.click(await screen.findByRole("button", { name: "항목 0 상세" }));
  expect(screen.getByRole("button", { name: "수정" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "삭제" })).toBeInTheDocument();
});

test("항목 추가: 배치 스토리지를 물려받아 POST — 성공 시 입력 초기화", async () => {
  let sent: any = null;
  server.use(http.post("/api/admin/batches/b1/items", async ({ request }) => {
    sent = await request.json();
    return HttpResponse.json({ seq: 2, status: "Running" }, { status: 202 });
  }));
  renderDetailed();                              // scan 배치, storage s1
  const input = await screen.findByLabelText("추가할 대상 경로");
  await userEvent.type(input, "newpath");
  await userEvent.click(screen.getByRole("button", { name: "항목 추가" }));
  await waitFor(() => expect(sent).toEqual({ storage: "s1", target: "newpath" }));
  await waitFor(() => expect(screen.getByLabelText("추가할 대상 경로")).toHaveValue(""));
});

test("sync 배치 항목 수정: 소스·목적지 경로 두 입력 — 4필드 payload 로 PUT", async () => {
  let sent: any = null;
  server.use(http.put("/api/admin/batches/b1/items/0", async ({ request }) => {
    sent = await request.json();
    return HttpResponse.json({ seq: 0, status: "Queued" });
  }));
  renderBatch({ operation: "sync", status: "Completed", items: [
    { seq: 0, payload: { source_storage: "s1", source: "a",
        destination_storage: "s2", destination: "b" }, status: "Succeeded",
      request_id: "r1", reason_code: null }] });
  await userEvent.click(await screen.findByRole("button", { name: "항목 0 상세" }));
  await userEvent.click(screen.getByRole("button", { name: "수정" }));
  const dst = screen.getByLabelText("목적지 경로");
  expect(screen.getByLabelText("소스 경로")).toHaveValue("a");
  await userEvent.clear(dst);
  await userEvent.type(dst, "b2");
  await userEvent.click(screen.getByRole("button", { name: "항목 저장" }));
  await waitFor(() => expect(sent).toEqual({ source_storage: "s1", source: "a",
    destination_storage: "s2", destination: "b2" }));
});

test("항목이 없으면 추가 폼 대신 안내 — 스토리지를 물려받을 항목이 없다", async () => {
  renderBatch({ operation: "scan", status: "Completed", items: [] });
  await screen.findByText(/항목이 없어/);
  expect(screen.queryByRole("button", { name: "항목 추가" })).toBeNull();
});

// --- 배치 삭제: 종단 배치만 — 확인 다이얼로그 필수, 성공 시 목록으로 ---

function renderWithList(over: any) {
  server.use(http.get("/api/admin/batches/b1", () => HttpResponse.json(batch(over))));
  const qc = new QueryClient({ defaultOptions:{ queries:{ retry:false }}});
  return render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/admin/batches/b1"]}>
    <Routes>
      <Route path="/admin/batches" element={<p>배치 목록 화면</p>} />
      <Route path="/admin/batches/:batchId" element={<BatchDetail/>} />
    </Routes>
  </MemoryRouter></QueryClientProvider>);
}

test("종단 배치: 배치 삭제 버튼 → 확인 다이얼로그 → DELETE + 목록으로 이동", async () => {
  let deleted = false;
  server.use(http.delete("/api/admin/batches/b1", () => {
    deleted = true; return HttpResponse.json({ deleted: "b1" });
  }));
  renderWithList({ status: "Completed" });
  await userEvent.click(await screen.findByRole("button", { name: "배치 삭제" }));
  // 트리거만으로는 안 쏜다 — 다이얼로그의 확인이 실제 발사다
  expect(deleted).toBe(false);
  await userEvent.click(await screen.findByRole("button", { name: "삭제 확인" }));
  await waitFor(() => expect(deleted).toBe(true));
  // 삭제된 배치 상세는 404 화면이 될 뿐 — 목록으로 보낸다
  expect(await screen.findByText("배치 목록 화면")).toBeInTheDocument();
});

test("활성 배치: 배치 삭제 버튼 부재 — 취소 먼저가 동선", async () => {
  renderAt("Running");
  await screen.findByText("Materialized");       // 렌더 완료 대기 후 부재 단언
  expect(screen.queryByRole("button", { name: "배치 삭제" })).toBeNull();
});

// --- 실행 제어 설정 표시(읽기 전용): 생성 시 고른 값이 상세에서 보인다 ---

test("실행 설정: 지정값 표시 + 옵션 키=값 요약 + 소유자 기록", async () => {
  renderBatch({ operation: "scan", status: "Completed", priority: "high",
    node_count: 4, procs_per_node: 2, max_concurrency: 8,
    options: { batch_files: 1000 }, owner_username: "alice",
    items: [{ seq: 0, payload: { storage: "s1", target: "a" }, status: "Succeeded",
      request_id: "r1", reason_code: null }] });
  expect(await screen.findByText("실행 설정")).toBeInTheDocument();
  const dd = (label: string) =>
    (screen.getByText(label).nextElementSibling as HTMLElement).textContent;
  expect(dd("우선순위")).toBe("high");
  expect(dd("노드 수")).toBe("4");
  expect(dd("노드당 프로세스")).toBe("2");
  expect(dd("동시 실행 상한")).toBe("8");
  expect(dd("옵션")).toBe("batch_files=1000");
  expect(dd("소유자 기록")).toBe("alice");
});

test("실행 설정: 미지정(null)은 '정책 기본' — 0·빈값으로 뭉개지 않는다(null≠0)", async () => {
  renderBatch({ operation: "scan", status: "Completed", priority: null,
    node_count: null, procs_per_node: null, options: {},
    items: [{ seq: 0, payload: { storage: "s1", target: "a" }, status: "Succeeded",
      request_id: "r1", reason_code: null }] });
  await screen.findByText("실행 설정");
  // priority/node_count/procs_per_node 세 항목 모두 "정책 기본"
  expect(screen.getAllByText("정책 기본")).toHaveLength(3);
  expect((screen.getByText("옵션").nextElementSibling as HTMLElement).textContent)
    .toBe("없음");
  // 소유자 기록은 있을 때만 — 없으면 행 자체가 없다
  expect(screen.queryByText("소유자 기록")).toBeNull();
});

test("PreviewReady also shows cancel button and posts cancel", async () => {
  let cancelled = false;
  server.use(http.post("/api/admin/batches/b1:cancel", () => { cancelled = true; return HttpResponse.json({status:"Cancelled"}); }));
  renderAt("PreviewReady");
  await userEvent.click(await screen.findByRole("button", { name: "취소" }));
  // userEvent.click 은 fetch 착지를 보장하지 않는다 -- 단언을 waitFor 로 감싸 플레이키를 없앤다.
  await waitFor(() => expect(cancelled).toBe(true));
});
