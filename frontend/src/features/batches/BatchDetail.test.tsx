import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse, delay } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { BatchDetail } from "./BatchDetail";
import { BatchesList } from "./BatchesList";
import { parseItemsCsv } from "../../lib/csv";
// 기본 스토리지 목록은 **빈 배열**이다: 화면이 절대경로 조합용으로 이 목록을
// 부르는데(useStorageRoots), 기본이 없으면 모든 테스트가 MSW 미처리 경고를 뿜는다.
// 빈 배열 = 뿌리 모름 = 절대경로 표시 없음이라 기존 단언에는 영향이 없다.
const server = setupServer(
  http.get("/api/user/storages", () => HttpResponse.json([])));
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

// 항목 편집 도구 3종은 버튼+팝업이다 — 버튼을 눌러 모달을 열고 그 안에서 조작한다.
// 팝업 안 요소는 within(dialog) 로만 집는다: 트리거 버튼과 팝업 안 실행 버튼이 같은
// 이름을 쓸 수 있어(예 「항목 추가」) screen 전역 질의는 모호해질 수 있다.
async function openDialog(name: string) {
  await userEvent.click(await screen.findByRole("button", { name }));
  return within(await screen.findByRole("dialog"));
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
test("owner_username 이 있으면 실행 신원 표시 + 특권 실행 문구", async () => {
  server.use(http.get("/api/admin/batches/b1",
    () => HttpResponse.json(batch({ owner_username: "alice" }))));
  const qc = new QueryClient({ defaultOptions:{ queries:{ retry:false }}});
  render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/admin/batches/b1"]}>
    <Routes><Route path="/admin/batches/:batchId" element={<BatchDetail/>} /></Routes>
  </MemoryRouter></QueryClientProvider>);
  expect(await screen.findByText("실행 신원 alice")).toBeInTheDocument();
  expect(screen.getByText("특권 실행(root)")).toBeInTheDocument();
});
test("owner_username 이 없어도 특권 실행 문구는 항상 — 실행 신원 행만 부재", async () => {
  // 통일 게이트 후 배치는 전부 특권 실행이다. 행별 auth_method/owner 로 재판정하지
  // 않는다(프론트는 allowlist 를 모른다 — 판정 흉내가 더 큰 거짓말).
  renderAt("Running");
  await screen.findByText("Materialized");   // 렌더 완료 대기 후 부재 단언
  expect(screen.getByText("특권 실행(root)")).toBeInTheDocument();
  expect(screen.queryByText(/실행 신원/)).toBeNull();
});
// --- 항목별 데이터 온도(hot/cold): expand 패널 안에서 그 항목의 리포트만 ---
// 크기 버킷 fixture 는 **라이브 dscan 리포트 실측**(d63)이다: 버킷 **10개**,
// 구간은 [직전 상한+1, 상한](첫 버킷만 하한 0), 마지막은 상한 없음(열린 구간).
// 서버 투영(routes_scan_paths._buckets)이 구간 라벨 + lower/upper_inclusive 를
// 그대로 넘긴다. 10 이라는 수 자체가 회귀 못이다 — BarChart 저밀도 상한이 9 였을
// 때 이 차트만 고밀도로 떨어져 값 라벨·누적 오버레이가 통째로 사라졌다.
const SIZE_BUCKETS: [number, number | null, number][] = [
  [0, 4096, 3], [4097, 65536, 1], [65537, 1048576, 1],
  [1048577, 16777216, 1], [16777217, 268435456, 1],
  [268435457, 1073741824, 0], [1073741825, 17179869184, 0],
  [17179869185, 274877906944, 0], [274877906945, 4398046511104, 0],
  [4398046511105, null, 0]];
// 축약 라벨 기대값(축 순서대로). 하한이 "상한+1"(4097 = 4K+1)이라 소수로 흘러
// ".0" 이 붙기 쉬운데, 그 자리는 반올림해 붙지 않는다("4K~64K").
const SIZE_LABELS = ["0~4K", "4K~64K", "64K~1M", "1M~16M", "16M~256M",
                     "256M~1G", "1G~16G", "16G~256G", "256G~4T", "4T~"];
const sizeHistogram = () => SIZE_BUCKETS.map(([lo, hi, count]) => ({
  bucket: hi === null ? `[${lo},)` : `[${lo},${hi}]`,
  lower_inclusive: lo, count,
  ...(hi === null ? {} : { upper_inclusive: hi }) }));

const requestStats = (over: any = {}) => ({
  generated_at_epoch: 1785805962,
  summary: { total_files: 12, total_entries: 20 },
  file_size_histogram: sizeHistogram(),
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
  // 파일 크기 분포(count)는 온도가 아니다 — 온도 그라디언트 대신 단색 accent 유지
  // (크기는 hot/cold 축이 아니라 같은 색을 쓰면 "작은 파일=hot"이라는 거짓 의미가 된다)
  const sizeChart = screen.getByRole("img", { name: "파일 크기 분포" });
  expect(sizeChart.getElementsByClassName("bg-accent")).toHaveLength(10);
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

test("온도 차트에 누적 오버레이(선+값) + 캡션 총 용량", async () => {
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
});

// --- 파일 크기 분포: 전 버킷 축약 라벨 + 개수 + 누적 % (온도 차트와 같은 문법) ---

test("크기 분포(10버킷): 축약 라벨이 하나도 안 솎이고 전부 보인다", async () => {
  server.use(statsHandler("r1", { calls: 0 }));
  renderDetailed();
  await userEvent.click(await screen.findByRole("button", { name: "항목 0 상세" }));
  const el = await screen.findByRole("img", { name: "파일 크기 분포" });
  const chart = within(el);
  // 원본 라벨("[0,4096]")은 열 폭(max-w-16)에서 잘린다 — 축약 표기가 전 버킷에 보인다.
  // 10버킷이 저밀도 상한(SPARSE_MAX) 안이라 라벨 솎기(labelStep)가 아예 없다.
  for (const label of SIZE_LABELS) expect(chart.getByText(label)).toBeInTheDocument();
  expect(screen.queryByText("[0,4096]")).toBeNull();
  // 상한+1 하한(4097 = 4K+1)이 ".0" 으로 새지 않는다 — 축 라벨이 넓어질 뿐이다
  expect(chart.queryByText(/\.0[KMGT]/)).toBeNull();
  // 단위 문법(K=KiB, 1024단위)은 화면이 스스로 말한다 — 안 그러면 K 가 1000 으로 읽힌다
  expect(screen.getByText(/K=KiB·M=MiB·G=GiB/)).toBeInTheDocument();
});

test("크기 분포(10버킷): 값=파일 개수 + 누적 % 오버레이(온도 차트와 같은 옵션)", async () => {
  server.use(statsHandler("r1", { calls: 0 }));
  renderDetailed();
  await userEvent.click(await screen.findByRole("button", { name: "항목 0 상세" }));
  const el = await screen.findByRole("img", { name: "파일 크기 분포" });
  const chart = within(el);
  // 값 라벨 = 개수(정수 그대로 — 용량이 아니다). 3·1×4·0×5 = 10버킷 전부 표기.
  expect(chart.getByText("3")).toBeInTheDocument();
  expect(chart.getAllByText("1")).toHaveLength(4);
  expect(chart.getAllByText("0")).toHaveLength(5);       // 0 은 정상값(빈 버킷)
  // 누적 오버레이: 3·1·1·1·1·0… → 43%·57%·71%·86%·100%(이후 100% 유지)
  expect(el.querySelector("polyline")).not.toBeNull();
  expect(chart.getAllByText(/%$/).map((n) => n.textContent))
    .toEqual(["43%", "57%", "71%", "86%", "100%",
              "100%", "100%", "100%", "100%", "100%"]);
  // 툴팁의 누적값은 개수 표기(바이트가 아니다)
  expect(chart.getAllByText("100%")[0].getAttribute("title"))
    .toBe("누적 7개 (100%)");
  // 캡션: 선의 의미 + 총 개수(온도 차트 캡션과 같은 문법, 단위만 개수)
  expect(screen.getByText("선 = 작은 파일부터의 누적 개수 비중 · 총 7개"))
    .toBeInTheDocument();
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
  // 버튼 라벨은 「편집」 하나로 통일(사용자 조정) — 폼이 이름·메모를 함께 고치므로
  // "메모 편집"은 어색했다. PATCH {name, note} 계약은 무변경.
  await userEvent.click(await screen.findByRole("button", { name: "편집" }));
  await userEvent.type(screen.getByLabelText("배치 이름"), "새 이름");
  await userEvent.type(screen.getByLabelText("메모"), "새 메모");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await waitFor(() => expect(patched).toEqual({ name: "새 이름", note: "새 메모" }));
  // 저장 성공 후 편집 종료(입력 부재)
  await waitFor(() => expect(screen.queryByLabelText("배치 이름")).toBeNull());
});
test("메모는 라벨 없이 내용만 — '메모 ' 접두는 붙지 않는다", async () => {
  // 헤더 카드의 메모 줄은 문맥상 메모임이 자명하다(사용자 조정) — "메모 {내용}"은
  // 내용 앞에 군더더기를 붙여 읽기를 방해했다.
  renderBatch({ status: "Running", note: "8월 정기 스캔" });
  expect(await screen.findByText("8월 정기 스캔")).toBeInTheDocument();
  expect(screen.queryByText("메모 8월 정기 스캔")).toBeNull();
});

test("편집 취소는 PATCH 없이 닫힌다", async () => {
  let patchCalls = 0;
  server.use(http.patch("/api/admin/batches/b1", () => { patchCalls += 1;
    return HttpResponse.json(batch()); }));
  renderAt("Running");
  await userEvent.click(await screen.findByRole("button", { name: "편집" }));
  // 배치 취소 버튼("취소")과 겹치지 않는 라벨 — 편집 취소는 별개 동작이다.
  // 이름 매칭은 완전 일치라 「편집」이 「편집 취소」를 집지 않는다(양쪽 공존 계약).
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

test("종단 배치: 행의 수정 버튼(펼치지 않아도) — 수정 저장이 PUT 을 쏜다", async () => {
  let sent: any = null;
  server.use(http.put("/api/admin/batches/b1/items/0", async ({ request }) => {
    sent = await request.json();
    return HttpResponse.json({ seq: 0, status: "Queued" });
  }));
  renderDetailed();                              // Completed scan 배치, 항목 0 = Succeeded
  // 수정은 행 버튼이다(삭제와 같은 자리) — 펼치지 않아도 폼이 행 밑에 열린다
  await userEvent.click(await screen.findByRole("button", { name: "항목 0 수정" }));
  const input = screen.getByLabelText("대상 경로");
  expect(input).toHaveValue("team");             // 현재 경로가 드래프트 초기값
  await userEvent.clear(input);
  await userEvent.type(input, "edited");
  await userEvent.click(screen.getByRole("button", { name: "항목 저장" }));
  // 스토리지는 배치 동질성 계약 — 기존 payload 의 storage 를 그대로 싣는다
  await waitFor(() => expect(sent).toEqual({ storage: "s1", target: "edited" }));
  await waitFor(() => expect(screen.queryByLabelText("대상 경로")).toBeNull());
});

test("종단 배치: 펼치지 않은 행의 삭제 버튼 → 확인 클릭이 DELETE 를 쏘고 재조회한다", async () => {
  let deleted = false;
  let gets = 0;
  server.use(
    http.get("/api/admin/batches/b1", () => { gets += 1;
      return HttpResponse.json(detailedBatch()); }),
    http.delete("/api/admin/batches/b1/items/1", () => {
      deleted = true; return HttpResponse.json({ deleted: 1 });
    }));
  const qc = new QueryClient({ defaultOptions:{ queries:{ retry:false }}});
  render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/admin/batches/b1"]}>
    <Routes><Route path="/admin/batches/:batchId" element={<BatchDetail/>} /></Routes>
  </MemoryRouter></QueryClientProvider>);
  // 펼치지 않아도 행 자체에 삭제 버튼이 보인다(이동 확정)
  await userEvent.click(await screen.findByRole("button", { name: "항목 1 삭제" }));
  expect(deleted).toBe(false);         // 1단 클릭만으로는 안 쏜다(오삭제 방지 2단 확인)
  const getsBefore = gets;
  await userEvent.click(screen.getByRole("button", { name: "항목 1 삭제 확인" }));
  await waitFor(() => expect(deleted).toBe(true));
  // 삭제 후 invalidate — 상세를 재조회해 목록이 갱신된다(종단 배치는 폴링 없음)
  await waitFor(() => expect(gets).toBeGreaterThan(getsBefore));
});

test("펼침 패널 안엔 수정·삭제 버튼이 없다 — 둘 다 행으로 이동 확정", async () => {
  renderDetailed();                              // 종단 배치 — 전 항목 편집 가능
  await userEvent.click(await screen.findByRole("button", { name: "항목 0 상세" }));
  expect(screen.getByText("payload")).toBeInTheDocument();   // 펼침 완료
  // 행 버튼(항목 N 수정·삭제)만 있고, 펼침 안의 무접두 버튼은 없다
  expect(screen.queryByRole("button", { name: "삭제" })).toBeNull();
  expect(screen.queryByRole("button", { name: "수정" })).toBeNull();
  expect(screen.getByRole("button", { name: "항목 0 삭제" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "항목 0 수정" })).toBeInTheDocument();
});

test("행 버튼 순서: 상세 토글 → 수정 → 삭제(수정이 삭제 왼쪽)", async () => {
  renderDetailed();
  const toggle = await screen.findByRole("button", { name: "항목 0 상세" });
  const row = toggle.parentElement as HTMLElement;
  expect(within(row).getAllByRole("button")
    .map((btn) => btn.getAttribute("aria-label") ?? btn.textContent))
    .toEqual(["항목 0 상세", "항목 0 수정", "항목 0 삭제"]);
});

test("활성 배치: Materialized 항목엔 행 삭제·수정 버튼이 없다", async () => {
  renderAt("Running");                           // 항목 0 = Materialized
  await screen.findByText("Materialized");       // 렌더 완료 대기 후 부재 단언
  expect(screen.queryByRole("button", { name: "항목 0 삭제" })).toBeNull();
  expect(screen.queryByRole("button", { name: "항목 0 수정" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "항목 0 상세" }));
  expect(screen.getByText("payload")).toBeInTheDocument();   // 펼침 완료
  expect(screen.queryByRole("button", { name: "수정" })).toBeNull();
});

test("활성 배치: Queued 항목엔 행 삭제·수정 버튼", async () => {
  renderBatch({ operation: "scan", status: "Running", items: [
    { seq: 0, payload: { storage: "s1", target: "a" }, status: "Queued",
      request_id: null, reason_code: null }] });
  expect(await screen.findByRole("button", { name: "항목 0 삭제" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "항목 0 수정" })).toBeInTheDocument();
});

test("항목 제목은 주변 글자(text-sm)와 같은 크기 — 체크박스보다 작아 보이지 않는다", async () => {
  renderDetailed();
  const title = await screen.findByText("scan · s1:team");
  expect(title.className).toMatch(/\btext-sm\b/);
  expect(title.className).not.toMatch(/\btext-xs\b/);
});

test("펼침 패널: 섹션마다 구분선·여백 — 정보 덩어리가 뭉쳐 보이지 않는다", async () => {
  server.use(statsHandler("r1", { calls: 0 }));
  renderDetailed();
  await userEvent.click(await screen.findByRole("button", { name: "항목 0 상세" }));
  await screen.findByText("데이터 온도(hot/cold)");
  // 네 덩어리(요청 정보 dl · 온도 · 크기 분포 · 요약)가 각각 제 섹션에 산다
  for (const title of ["요청 정보", "데이터 온도(hot/cold)",
                       "파일 크기 분포(개수)", "요약"]) {
    const heading = screen.getByText(title);
    const section = heading.closest("section");
    expect(section, `${title} 섹션`).not.toBeNull();
    expect(section!.className).toMatch(/border-t/);   // 구분선
    expect(section!.className).toMatch(/\bpt-/);      // 선 아래 여백
    expect(section!.className).toMatch(/\bmt-/);      // 선 위 여백
    expect(heading.className).toMatch(/font-semibold/); // 소제목 강조
  }
});

test("항목 추가 팝업: 배치 스토리지를 물려받아 POST — 성공 시 팝업이 닫힌다", async () => {
  let sent: any = null;
  server.use(http.post("/api/admin/batches/b1/items", async ({ request }) => {
    sent = await request.json();
    return HttpResponse.json({ seq: 2, status: "Running" }, { status: 202 });
  }));
  renderDetailed();                              // scan 배치, storage s1
  const d = await openDialog("항목 추가");
  await userEvent.type(d.getByLabelText("추가할 대상 경로"), "newpath");
  await userEvent.click(d.getByRole("button", { name: "항목 추가" }));
  await waitFor(() => expect(sent).toEqual({ storage: "s1", target: "newpath" }));
  // 성공 = 팝업 닫힘. 드래프트는 닫힐 때 비워지므로 재오픈이 빈 입력이다.
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  const again = await openDialog("항목 추가");
  expect(again.getByLabelText("추가할 대상 경로")).toHaveValue("");
});

test("sync 배치 항목 추가 팝업: 소스·목적지 두 입력", async () => {
  renderBatch({ operation: "sync", status: "Completed", items: [
    { seq: 0, payload: { source_storage: "s1", source: "a",
        destination_storage: "s2", destination: "b" }, status: "Succeeded",
      request_id: "r1", reason_code: null }] });
  const d = await openDialog("항목 추가");
  expect(d.getByLabelText("추가할 소스 경로")).toBeInTheDocument();
  expect(d.getByLabelText("추가할 목적지 경로")).toBeInTheDocument();
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
  await userEvent.click(await screen.findByRole("button", { name: "항목 0 수정" }));
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

// 배치 자체 삭제는 앞의 셋과 조건이 다르다: 지운 뒤 이동할 **목록 쿼리는 그 시점에
// 비활성**(상세 화면에 있으니까)이라 invalidate 는 재조회를 트리거하지 않는다 —
// 표시만 stale 로 바뀐다. 그대로 이동하면 목록은 캐시에 남은 지워진 배치를 먼저
// 그리고(로딩 표시도 없다 — 데이터가 있으니 isLoading 은 false 다) 마운트 후
// 재조회가 착지해야 사라진다. 그래서 이 경로만 invalidate 가 아니라 refetch 를
// **기다린 뒤** 이동한다.
test("배치 삭제: 목록 화면에 도착한 순간 지워진 배치가 이미 없다(캐시 잔상 금지)", async () => {
  const listRow = (id: string, name: string) => ({ batch_id: id, operation: "scan",
    status: "Completed", max_concurrency: 2, item_count: 1, succeeded_count: 1,
    failed_count: 0, note: null, created_at: "", updated_at: "2026-08-15T00:00:00Z",
    name });
  const state = { rows: [listRow("b1", "지울 배치"), listRow("b9", "남을 배치")] };
  let listGets = 0;
  server.use(
    http.get("/api/admin/batches", async () => { listGets += 1;
      if (listGets > 1) await delay(200);          // 삭제 후 재조회만 느리게
      return HttpResponse.json(state.rows); }),
    http.get("/api/admin/batches/b1", () => HttpResponse.json(batch({ status: "Completed" }))),
    http.delete("/api/admin/batches/b1", () => {
      state.rows = state.rows.filter((r) => r.batch_id !== "b1");
      return HttpResponse.json({ deleted: "b1" }); }));
  const qc = new QueryClient({ defaultOptions:{ queries:{ retry:false }}});
  render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/admin/batches"]}>
    <Routes>
      <Route path="/admin/batches" element={<BatchesList/>} />
      <Route path="/admin/batches/:batchId" element={<BatchDetail/>} />
    </Routes>
  </MemoryRouter></QueryClientProvider>);
  // 실제 동선: 목록에서 들어간다 = 목록 캐시가 이미 채워진 상태로 삭제한다
  await screen.findByText("지울 배치");
  await userEvent.click(screen.getByRole("link", { name: "b1" }));
  await userEvent.click(await screen.findByRole("button", { name: "배치 삭제" }));
  await userEvent.click(await screen.findByRole("button", { name: "삭제 확인" }));
  await screen.findByText("배치 작업");            // 목록 화면 도착
  expect(screen.queryByText("지울 배치")).toBeNull();
  expect(screen.getByText("남을 배치")).toBeInTheDocument();
});

test("활성 배치: 배치 삭제 버튼 부재 — 취소 먼저가 동선", async () => {
  renderAt("Running");
  await screen.findByText("Materialized");       // 렌더 완료 대기 후 부재 단언
  expect(screen.queryByRole("button", { name: "배치 삭제" })).toBeNull();
});

// --- CSV 전체 교체: 종단 배치만 — 붙여넣기 → 파스 미리보기(행 수·오류) → 교체 → PUT ---
// 파일 업로드가 아니라 textarea 붙여넣기다: 운영 환경 브라우저는 파일 업로드가
// 불가하다(환경 제약) — 생성 위저드의 CSV 붙여넣기 패턴을 미러한다.

test("종단 배치: CSV 붙여넣기 → 행 수 미리보기 → 교체가 PUT(스토리지 상속)을 쏘고 팝업이 닫힌다", async () => {
  let sent: any = null;
  server.use(http.put("/api/admin/batches/b1/items", async ({ request }) => {
    sent = await request.json();
    return HttpResponse.json({ replaced: 2 });
  }));
  renderDetailed();                              // Completed scan 배치, storage s1
  const d = await openDialog("CSV로 전체 교체");
  const ta = d.getByLabelText("교체 CSV");
  await userEvent.click(ta);
  await userEvent.paste("target\nx/y\nz");
  // 파스 미리보기가 먼저 — 교체는 미리보기를 본 뒤의 확인 클릭이다
  expect(await screen.findByText("2행 파싱됨")).toBeInTheDocument();
  expect(sent).toBeNull();
  await userEvent.click(d.getByRole("button", { name: "교체" }));
  // 배치 레벨 스토리지는 첫 항목 payload 에서 상속(동질성 계약)
  await waitFor(() => expect(sent).toEqual({ items: [
    { storage: "s1", target: "x/y" }, { storage: "s1", target: "z" }] }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

test("sync 배치 교체: source,destination 2열 — 4필드 payload 로 PUT", async () => {
  let sent: any = null;
  server.use(http.put("/api/admin/batches/b1/items", async ({ request }) => {
    sent = await request.json(); return HttpResponse.json({ replaced: 1 });
  }));
  renderBatch({ operation: "sync", status: "Completed", items: [
    { seq: 0, payload: { source_storage: "s1", source: "a",
        destination_storage: "s2", destination: "b" }, status: "Succeeded",
      request_id: "r1", reason_code: null }] });
  const d = await openDialog("CSV로 전체 교체");
  const ta = d.getByLabelText("교체 CSV");
  await userEvent.click(ta);
  await userEvent.paste("source,destination\nc,d");
  await screen.findByText("1행 파싱됨");
  await userEvent.click(d.getByRole("button", { name: "교체" }));
  await waitFor(() => expect(sent).toEqual({ items: [
    { source_storage: "s1", source: "c",
      destination_storage: "s2", destination: "d" }] }));
});

test("파스 오류가 있으면 교체 버튼 잠금 + 오류 나열", async () => {
  renderDetailed();
  const d = await openDialog("CSV로 전체 교체");
  const ta = d.getByLabelText("교체 CSV");
  await userEvent.click(ta);
  await userEvent.paste("a,b\nokpath");
  expect(await screen.findByText(/1행: 경로 1개/)).toBeInTheDocument();
  expect(d.getByRole("button", { name: "교체" })).toBeDisabled();
});

test("빈 입력(초기 상태)엔 미리보기·교체 버튼 없음 — 파일 input 도 없다", async () => {
  renderDetailed();
  const d = await openDialog("CSV로 전체 교체");
  expect(d.queryByText(/행 파싱됨/)).toBeNull();
  expect(d.queryByRole("button", { name: "교체" })).toBeNull();
  // 파일 업로드 경로는 제거됐다(운영 브라우저 제약) — 회귀 못
  expect(d.queryByLabelText("교체 CSV 파일")).toBeNull();
});

test("활성 배치: CSV 전체 교체 버튼 부재 — 종단 배치만", async () => {
  renderAt("Running");
  await screen.findByText("Materialized");       // 렌더 완료 대기 후 부재 단언
  expect(screen.queryByRole("button", { name: "CSV로 전체 교체" })).toBeNull();
  expect(screen.queryByLabelText("교체 CSV")).toBeNull();
});

// --- 현재 항목 CSV(읽기 전용): 항목을 CSV 텍스트로 노출 — 직접 선택·복사 ---
// clipboard API 는 안 쓴다: 운영 포탈은 http 비보안 컨텍스트라 navigator.clipboard
// 가 부재한다(BatchCreate downloadCsv 와 같은 제약).

test("현재 항목 CSV 팝업: serializeItemsCsv 텍스트를 읽기 전용으로 렌더 — 교체 파서와 왕복", async () => {
  renderDetailed();                              // scan 배치, 항목 team·proj
  const d = await openDialog("현재 항목 CSV");
  const ta = d.getByLabelText("현재 항목 CSV");
  expect(ta).toHaveValue("target\nteam\nproj");
  expect(ta).toHaveAttribute("readonly");
  // 왕복 계약: 이 텍스트를 교체 textarea 에 넣으면 같은 항목이 복원된다
  expect(parseItemsCsv("scan", "target\nteam\nproj").rows)
    .toEqual([{ target: "team" }, { target: "proj" }]);
});

test("sync 배치의 현재 항목 CSV: source,destination 2열 — 왕복 계약", async () => {
  renderBatch({ operation: "sync", status: "Completed", items: [
    { seq: 0, payload: { source_storage: "s1", source: "a",
        destination_storage: "s2", destination: "b" }, status: "Succeeded",
      request_id: "r1", reason_code: null }] });
  const d = await openDialog("현재 항목 CSV");
  expect(d.getByLabelText("현재 항목 CSV")).toHaveValue("source,destination\na,b");
  expect(parseItemsCsv("sync", "source,destination\na,b").rows)
    .toEqual([{ source: "a", destination: "b" }]);
});

// --- 도구 3종의 버튼 행: 인라인 영역은 팝업으로 이사했다(잔여물 없음) ---

test("항목 카드에 도구 버튼 3종 — 열기 전엔 입력·textarea 가 화면에 없다", async () => {
  renderDetailed();                              // 종단 scan 배치 + 항목 2개
  expect(await screen.findByRole("button", { name: "현재 항목 CSV" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "항목 추가" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "CSV로 전체 교체" })).toBeInTheDocument();
  // 팝업 밖 잔여물 부재 — 세 도구의 조작면은 전부 모달 안이다
  expect(screen.queryByLabelText("현재 항목 CSV")).toBeNull();
  expect(screen.queryByLabelText("추가할 대상 경로")).toBeNull();
  expect(screen.queryByLabelText("교체 CSV")).toBeNull();
});

test("활성 배치에도 현재 항목 CSV·항목 추가 버튼은 그대로(조건부 규칙 보존)", async () => {
  renderAt("Running");
  expect(await screen.findByRole("button", { name: "현재 항목 CSV" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "항목 추가" })).toBeInTheDocument();
});

test("항목이 없으면 도구 버튼도 없다 — 물려받을 스토리지가 없다", async () => {
  renderBatch({ operation: "scan", status: "Completed", items: [] });
  await screen.findByText(/항목이 없어/);
  expect(screen.queryByRole("button", { name: "현재 항목 CSV" })).toBeNull();
  expect(screen.queryByRole("button", { name: "CSV로 전체 교체" })).toBeNull();
});

// --- 실행 제어 설정 표시(읽기 전용): 생성 시 고른 값이 상세에서 보인다 ---

test("실행 설정: 지정값 표시 + 옵션 키=값 요약 + 실행 신원", async () => {
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
  expect(dd("실행 신원")).toBe("alice");
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
  // 실행 신원은 있을 때만 — 없으면 행 자체가 없다
  expect(screen.queryByText("실행 신원")).toBeNull();
});

// --- 배치 상태 색: 헤더 pill 은 batchPillVariant, 항목 pill 은 공유 pillVariant ---
// 항목 상태(Queued/Materialized/Succeeded/Failed/Cancelled — 요청/잡 판정 축)는
// 배치 상태와 다른 도메인이라 각자 맞는 매핑을 쓴다.

test("배치 헤더 pill: Completed=ok(초록) — 항목 pill(Materialized)은 공유 매핑 유지", async () => {
  renderAt("Completed");
  expect((await screen.findByText("Completed")).className).toContain("text-ok");
  expect(screen.getByText("Materialized").className).toContain("text-muted");
});

test("배치 헤더 pill: Running=busy — 공유 pillVariant(neutral)와 다르다", async () => {
  renderAt("Running");
  expect((await screen.findByText("Running")).className).toContain("text-busy");
});

test("배치 헤더 pill: Cancelled 는 neutral — 취소는 실패가 아니다", async () => {
  renderAt("Cancelled");
  const pill = await screen.findByText("Cancelled");
  expect(pill.className).toContain("text-muted");
  expect(pill.className).not.toContain("text-bad");
});

// --- 항목 다중 선택 삭제: 목록 화면의 일괄 삭제와 같은 UX 언어(2단 확인·부분 실패) ---

test("행 체크박스: 삭제 가능한 항목만 선택 가능 — 불가 항목은 disabled + 이유", async () => {
  renderBatch({ operation: "scan", status: "Running", items: [
    { seq: 0, payload: { storage: "s1", target: "a" }, status: "Queued",
      request_id: null, reason_code: null },
    { seq: 1, payload: { storage: "s1", target: "b" }, status: "Materialized",
      request_id: "r2", reason_code: null }] });
  const cb0 = await screen.findByLabelText("항목 0 선택");
  expect(cb0).toBeEnabled();
  const cb1 = screen.getByLabelText("항목 1 선택");
  expect(cb1).toBeDisabled();
  // disabled 로 끝내지 않고 이유·동선을 남긴다(목록 화면 ACTIVE_HINT 관례)
  expect(cb1).toHaveAttribute("title", expect.stringContaining("Queued"));
});

test("전체 선택: 선택 가능한 항목만 켠다 — 불가 항목은 그대로", async () => {
  renderBatch({ operation: "scan", status: "Running", items: [
    { seq: 0, payload: { storage: "s1", target: "a" }, status: "Queued",
      request_id: null, reason_code: null },
    { seq: 1, payload: { storage: "s1", target: "b" }, status: "Materialized",
      request_id: "r2", reason_code: null }] });
  await userEvent.click(await screen.findByLabelText("전체 선택"));
  expect(screen.getByLabelText("항목 0 선택")).toBeChecked();
  expect(screen.getByLabelText("항목 1 선택")).not.toBeChecked();
  expect(screen.getByText("1개 선택됨")).toBeInTheDocument();
});

test("액션 바는 자리를 예약한다 — 선택 전에도 컨테이너가 있어 레이아웃이 안 밀린다", async () => {
  renderDetailed();
  expect(await screen.findByText("항목을 선택하면 한 번에 삭제·재실행할 수 있습니다"))
    .toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "선택 삭제" })).toBeNull();
  expect(screen.queryByRole("button", { name: "선택 재실행" })).toBeNull();
});

test("선택 삭제: 2단 확인 후 선택 수만큼 DELETE — 부분 실패를 정직하게 말한다", async () => {
  const hit: number[] = [];
  server.use(
    http.delete("/api/admin/batches/b1/items/0", () => {
      hit.push(0); return HttpResponse.json({ deleted: 1 }); }),
    http.delete("/api/admin/batches/b1/items/1", () => {
      hit.push(1);
      return HttpResponse.json({ detail: "batch_item_not_editable" }, { status: 409 }); }));
  renderDetailed();                              // 종단 배치 — 두 항목 다 선택 가능
  await userEvent.click(await screen.findByLabelText("항목 0 선택"));
  await userEvent.click(screen.getByLabelText("항목 1 선택"));
  expect(screen.getByText("2개 선택됨")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
  expect(hit).toEqual([]);                       // 1단 클릭만으로는 안 쏜다
  await userEvent.click(screen.getByRole("button", { name: "2개 삭제 확인" }));
  await waitFor(() => expect([...hit].sort()).toEqual([0, 1]));
  // 부분 실패: 성공 수·실패 수·항목별 사유를 각각 말한다(전체 실패로 뭉개지 않는다)
  expect(await screen.findByText("1개 삭제됨 · 1개 실패")).toBeInTheDocument();
  expect(screen.getByText(/항목 1: 수정할 수 없는 항목입니다/)).toBeInTheDocument();
  // 완료 후 선택 초기화 — 액션 바는 자리만 남는다
  await waitFor(() => expect(screen.queryByText(/개 선택됨/)).toBeNull());
});

test("체크박스 클릭은 행 펼침을 토글하지 않는다", async () => {
  renderDetailed();
  const toggle = await screen.findByRole("button", { name: "항목 0 상세" });
  await userEvent.click(screen.getByLabelText("항목 0 선택"));
  expect(screen.getByLabelText("항목 0 선택")).toBeChecked();
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("파일 수")).toBeNull();
});

test("리페치로 사라진 항목은 선택에서 자동 제거된다(유령 선택 방지)", async () => {
  let deleted = false;
  server.use(
    http.get("/api/admin/batches/b1", () => {
      const d = detailedBatch();
      return HttpResponse.json(deleted
        ? { ...d, items: d.items.filter((it: any) => it.seq !== 1) } : d); }),
    http.delete("/api/admin/batches/b1/items/1", () => {
      deleted = true; return HttpResponse.json({ deleted: 1 }); }));
  const qc = new QueryClient({ defaultOptions:{ queries:{ retry:false }}});
  render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/admin/batches/b1"]}>
    <Routes><Route path="/admin/batches/:batchId" element={<BatchDetail/>} /></Routes>
  </MemoryRouter></QueryClientProvider>);
  await userEvent.click(await screen.findByLabelText("항목 1 선택"));
  expect(screen.getByText("1개 선택됨")).toBeInTheDocument();
  // 단건 행 삭제로 그 항목이 사라지면 선택도 함께 사라져야 한다
  await userEvent.click(screen.getByRole("button", { name: "항목 1 삭제" }));
  await userEvent.click(screen.getByRole("button", { name: "항목 1 삭제 확인" }));
  await waitFor(() => expect(screen.queryByText(/개 선택됨/)).toBeNull());
});

// --- 항목 선택 재실행: 액션 바의 두 번째 일괄 동작(삭제와 같은 2단 확인 UX) ---

test("선택 재실행: 2단 확인 후 POST {seqs} — 부분 결과를 항목별 사유로 말한다", async () => {
  let sent: any = null;
  server.use(http.post("/api/admin/batches/b1/items:rerun", async ({ request }) => {
    sent = await request.json();
    return HttpResponse.json({ requeued: 1, skipped: [
      { seq: 1, reason: "batch_item_not_rerunnable" }], status: "Running" });
  }));
  renderDetailed();                              // 종단 배치 — 두 항목 다 종단
  await userEvent.click(await screen.findByLabelText("항목 0 선택"));
  await userEvent.click(screen.getByLabelText("항목 1 선택"));
  await userEvent.click(screen.getByRole("button", { name: "선택 재실행" }));
  expect(sent).toBeNull();                       // 1단 클릭만으로는 안 쏜다
  await userEvent.click(screen.getByRole("button", { name: "2개 재실행 확인" }));
  await waitFor(() => expect(sent).toEqual({ seqs: [0, 1] }));
  // 부분 결과: 요청 수·제외 수·항목별 사유를 각각 말한다(조용한 성공 금지)
  expect(await screen.findByText("1개 재실행 요청됨 · 1개 제외")).toBeInTheDocument();
  expect(screen.getByText(/항목 1: 재실행할 수 없는 항목입니다/)).toBeInTheDocument();
  // 완료 후 선택 초기화 — 액션 바는 자리만 남는다(삭제 경로와 같은 계약)
  await waitFor(() => expect(screen.queryByText(/개 선택됨/)).toBeNull());
});

test("전부 재큐잉되면 제외 문구 없이 요청 수만 말한다", async () => {
  server.use(http.post("/api/admin/batches/b1/items:rerun", () =>
    HttpResponse.json({ requeued: 1, skipped: [], status: "Running" })));
  renderDetailed();
  await userEvent.click(await screen.findByLabelText("항목 0 선택"));
  await userEvent.click(screen.getByRole("button", { name: "선택 재실행" }));
  await userEvent.click(screen.getByRole("button", { name: "1개 재실행 확인" }));
  expect(await screen.findByText("1개 재실행 요청됨")).toBeInTheDocument();
});

test("활성 배치: 고를 수 있는 건 Queued 뿐이라 재실행 버튼은 disabled + 이유", async () => {
  renderBatch({ operation: "scan", status: "Running", items: [
    { seq: 0, payload: { storage: "s1", target: "a" }, status: "Queued",
      request_id: null, reason_code: null },
    { seq: 1, payload: { storage: "s1", target: "b" }, status: "Materialized",
      request_id: "r2", reason_code: null }] });
  await userEvent.click(await screen.findByLabelText("항목 0 선택"));
  // 삭제는 되지만(Queued) 재실행은 종단 항목만 — 눌러도 전부 skipped 인 버튼은
  // 누르게 두지 않는다. disabled 로 끝내지 않고 title 로 이유를 남긴다.
  expect(screen.getByRole("button", { name: "선택 삭제" })).toBeEnabled();
  const rerun = screen.getByRole("button", { name: "선택 재실행" });
  expect(rerun).toBeDisabled();
  expect(rerun).toHaveAttribute("title", expect.stringContaining("재실행"));
});

test("선택 재실행: 결과 문구가 뜨는 시점엔 이미 재조회가 착지해 있다", async () => {
  // 삭제 경로와 같은 시점 계약(직전 커밋의 결함): onSettled 가 invalidate 프라미스를
  // 돌려주지 않으면 "끝났다"고 말한 뒤에도 화면은 옛 상태를 그린다.
  const state = { items: detailedBatch().items as any[] };
  let gets = 0;
  server.use(
    http.get("/api/admin/batches/b1", async () => {
      gets += 1;
      if (gets > 1) await delay(200);
      return HttpResponse.json({ ...detailedBatch(), items: state.items }); }),
    http.post("/api/admin/batches/b1/items:rerun", () => {
      state.items = state.items.map((it) =>
        it.seq === 0 ? { ...it, status: "Queued", reason_code: null } : it);
      return HttpResponse.json({ requeued: 1, skipped: [], status: "Running" }); }));
  const qc = new QueryClient({ defaultOptions:{ queries:{ retry:false }}});
  render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/admin/batches/b1"]}>
    <Routes><Route path="/admin/batches/:batchId" element={<BatchDetail/>} /></Routes>
  </MemoryRouter></QueryClientProvider>);
  await screen.findByText("Succeeded");
  await userEvent.click(screen.getByLabelText("항목 0 선택"));
  await userEvent.click(screen.getByRole("button", { name: "선택 재실행" }));
  await userEvent.click(screen.getByRole("button", { name: "1개 재실행 확인" }));
  expect(await screen.findByText("1개 재실행 요청됨")).toBeInTheDocument();
  expect(screen.queryByText("Succeeded")).toBeNull();     // 이미 Queued 로 갱신됨
});

// --- 절대경로 표시(사용자 보고 2026-08-15: "완료된 작업을 볼 때도 관리 디렉토리가
// 안 보여서 정확한 path 를 알 수 없다"). payload 는 상대경로만 담고(서버 계약
// 무변경), 화면이 **지금의** managed_root 로 조합한다. 뿌리는 관리자 응답에만
// 실려 오므로 비관리자에겐 아무것도 안 뜬다.
function storagesHandler(rows: object[]) {
  return http.get("/api/user/storages", () => HttpResponse.json(rows));
}
const ROOTED = [{ storage_name: "s1", backend_type: "cephfs", status: "Ready",
                  managed_root: "/cephfs/dms" }];

test("항목 펼침: payload 아래 절대경로 행 + 행 요약 title 에도 절대경로", async () => {
  server.use(storagesHandler(ROOTED), statsHandler("r1", { calls: 0 }));
  renderDetailed();
  // 접힌 행에서도 title 로 절대경로를 준다(본문 길이는 그대로 — 레이아웃 불변)
  const summary = await screen.findByText("scan · s1:team");
  await waitFor(() => expect(summary).toHaveAttribute("title", "/cephfs/dms/team"));
  await userEvent.click(screen.getByRole("button", { name: "항목 0 상세" }));
  expect(screen.getByText("절대경로")).toBeInTheDocument();
  expect(screen.getByText("/cephfs/dms/team")).toBeInTheDocument();
});

test("managed_root 를 못 읽으면 절대경로 행도 title 도 없다", async () => {
  server.use(storagesHandler([{ storage_name: "s1", backend_type: "cephfs", status: "Ready" }]),
             statsHandler("r1", { calls: 0 }));
  renderDetailed();
  const summary = await screen.findByText("scan · s1:team");
  await userEvent.click(screen.getByRole("button", { name: "항목 0 상세" }));
  expect(screen.getByText("payload")).toBeInTheDocument();      // 펼침 완료
  expect(screen.queryByText("절대경로")).toBeNull();
  expect(summary).not.toHaveAttribute("title");
});

// --- 삭제 후 화면 갱신(사용자 보고 2026-08-15: "삭제 후 새로고침이 안되고 화면에
// 남아있다") -------------------------------------------------------------------
// 실측 결론: 무효화 키는 네 삭제 경로 모두 맞다(상세 ["batch", id] + 목록
// ["batches"]). 어긋난 건 **시점**이다 — onSettled 가 invalidate 의 프라미스를
// 돌려주지 않아 mutation 이 재조회가 끝나기 전에 "완료"를 선언했다. 그래서 확인
// 버튼이 되돌아오고 팝업이 닫히고 목록으로 이동한 뒤에도 지운 것이 화면에 그대로
// 남아 있고, 그동안 화면엔 아무 표시도 없다(재조회가 느릴수록 길어진다 —
// 실배포는 DB 조인 + 네트워크다). 아래 테스트들은 **재조회를 느리게** 해서 그
// 창을 열어 놓고, "끝났다고 말한 시점 = 화면이 갱신된 시점"을 못 박는다.
// 낙관적 제거는 쓰지 않는다: 서버 재조회 결과만 화면의 진실이다.

// 삭제 후 GET 만 느린 상세 렌더(첫 조회는 즉답 — 렌더 대기를 늘리지 않는다).
// renderDetailed 를 쓰지 않는 이유: 그 헬퍼가 정적 GET 핸들러를 다시 등록해
// (msw 는 나중 등록이 우선) 여기서 심은 상태 있는 핸들러를 덮는다.
function renderSlowRefetch(deletedSeq: number) {
  const state = { items: detailedBatch().items as any[] };
  let gets = 0;
  server.use(
    http.get("/api/admin/batches/b1", async () => {
      gets += 1;
      if (gets > 1) await delay(200);
      return HttpResponse.json({ ...detailedBatch(), items: state.items,
                                 item_count: state.items.length }); }),
    http.delete(`/api/admin/batches/b1/items/${deletedSeq}`, () => {
      state.items = state.items.filter((it) => it.seq !== deletedSeq);
      return HttpResponse.json({ deleted: deletedSeq }); }));
  const qc = new QueryClient({ defaultOptions:{ queries:{ retry:false }}});
  return render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/admin/batches/b1"]}>
    <Routes><Route path="/admin/batches/:batchId" element={<BatchDetail/>} /></Routes>
  </MemoryRouter></QueryClientProvider>);
}

test("단건 항목 삭제: 삭제가 끝났다고 말하는 시점엔 이미 행이 사라져 있다", async () => {
  renderSlowRefetch(1);
  await screen.findByText("scan · s1:proj");
  await userEvent.click(screen.getByRole("button", { name: "항목 1 삭제" }));
  await userEvent.click(screen.getByRole("button", { name: "항목 1 삭제 확인" }));
  // 「삭제 확인」이 「삭제」로 되돌아온 순간 = 화면이 mutation 종료를 말한 순간
  await waitFor(() => expect(
    screen.queryByRole("button", { name: "항목 1 삭제 확인" })).toBeNull());
  expect(screen.queryByText("scan · s1:proj")).toBeNull();
});

test("다중 선택 항목 삭제: 결과 문구가 뜨는 시점엔 이미 행이 사라져 있다", async () => {
  renderSlowRefetch(1);
  await screen.findByText("scan · s1:proj");
  await userEvent.click(screen.getByLabelText("항목 1 선택"));
  await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
  await userEvent.click(screen.getByRole("button", { name: "1개 삭제 확인" }));
  expect(await screen.findByText("1개 삭제됨")).toBeInTheDocument();
  expect(screen.queryByText("scan · s1:proj")).toBeNull();
});

// --- 펼침 패널 dl 정렬(사용자 지시 2026-08-15): 「요약」도 히스토그램 섹션들과
// 같은 왼쪽 정렬 기준을 쓴다. 이전 요약 dl 은 grid-cols-2(50/50 분할) + max-w-md
// 라 값 열이 라벨에서 14rem 떨어진 화면 중앙에서 시작했고, 같은 패널의 「요청
// 정보」(고정폭 라벨 열)와 눈으로 어긋났다. 두 dl 이 **같은 클래스 문자열**을
// 쓰는지로 못 박는다 — 한쪽만 고치면 다시 갈라진다.
test("펼침 패널의 요약·요청 정보 dl 은 같은 정렬 기준(고정폭 라벨 열·왼쪽 값)", async () => {
  server.use(statsHandler("r1", { calls: 0 }));
  renderDetailed();
  await userEvent.click(await screen.findByRole("button", { name: "항목 0 상세" }));
  await screen.findByText("요약");
  const dlOf = (title: string) =>
    screen.getByText(title).closest("section")!.querySelector("dl")!;
  const summary = dlOf("요약");
  expect(summary.className).toBe(dlOf("요청 정보").className);
  expect(summary.className).toContain("grid-cols-[9rem_1fr]");
  // 50/50 분할·최대폭 제한은 값을 중앙으로 밀어낸 원인이라 되돌아오면 안 된다
  expect(summary.className).not.toContain("grid-cols-2");
  expect(summary.className).not.toContain("max-w-md");
});

test("PreviewReady also shows cancel button and posts cancel", async () => {
  let cancelled = false;
  server.use(http.post("/api/admin/batches/b1:cancel", () => { cancelled = true; return HttpResponse.json({status:"Cancelled"}); }));
  renderAt("PreviewReady");
  await userEvent.click(await screen.findByRole("button", { name: "취소" }));
  // userEvent.click 은 fetch 착지를 보장하지 않는다 -- 단언을 waitFor 로 감싸 플레이키를 없앤다.
  await waitFor(() => expect(cancelled).toBe(true));
});
