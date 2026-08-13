// 슬라이스 32: 배치 생성 위저드(4스텝) — 배치 레벨 스토리지·입력 4방식·실행 제어.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { BatchCreate } from "./BatchCreate";

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const STORAGES = [
  { storage_name: "s1", backend_type: "cephfs", status: "Ready" },
  { storage_name: "s2", backend_type: "gpfs", status: "Ready" },
];

// 도구별로 값을 다르게 — 캡션이 "그 정책의 실값"을 읽는지 구분하기 위해.
const policyRow = (tool: string, max_nodes: number, procs_per_node: number) => ({
  tool, max_nodes, procs_per_node, queue: "dms-data",
  default_priority: "mid", max_priority: "high",
  preview_timeout_seconds: null, execution_timeout_seconds: 3600,
  enabled: 1, updated_at: "2026-08-05T00:00:00Z", updated_by: "admin",
});
const POLICIES = [
  policyRow("scan", 4, 8), policyRow("dsync", 6, 4), policyRow("nsync", 2, 2),
];

function renderPage(policies: object[] = POLICIES) {
  server.use(http.get("/api/user/storages", () => HttpResponse.json(STORAGES)));
  server.use(http.get("/api/admin/policies", () => HttpResponse.json(policies)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/admin/batches/new"]}>
        <Routes>
          <Route path="/admin/batches/new" element={<BatchCreate />} />
          <Route path="/admin/batches/:id" element={<h1>배치 b9</h1>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function captureCreate() {
  const captured: { body: any } = { body: null };
  server.use(http.post("/api/admin/batches", async ({ request }) => {
    captured.body = await request.json();
    return HttpResponse.json({ batch_id: "b9", status: "Running" }, { status: 202 });
  }));
  return captured;
}

const next = () => screen.getByRole("button", { name: "다음" });

test("scan: 테이블 2행 + 스토리지 → 제출 바디 조립·미지정 키 부재·내비게이션", async () => {
  const captured = captureCreate();
  renderPage();
  await userEvent.click(next());                              // 연산(scan 기본) → 대상·항목
  await userEvent.selectOptions(await screen.findByLabelText("스토리지"), "s1");
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  await userEvent.click(screen.getByRole("button", { name: "행 추가" }));
  await userEvent.type(screen.getByLabelText("2행 경로"), "b");
  await userEvent.click(next());                              // → 실행 제어
  await userEvent.click(next());                              // → 확인·제출
  await userEvent.click(screen.getByRole("button", { name: "배치 생성" }));
  expect(await screen.findByRole("heading", { name: "배치 b9" })).toBeInTheDocument();
  // 정확 일치: priority/node_count 미지정 = 키 부재(생략 계약, null≠0)
  expect(captured.body).toEqual({
    operation: "scan", max_concurrency: 2, options: {}, note: null,
    items: [{ storage: "s1", target: "a" }, { storage: "s1", target: "b" }],
  });
});

test("scan 옵션: top_k·quiet·우선순위·노드 수가 바디에 실린다", async () => {
  const captured = captureCreate();
  renderPage();
  await userEvent.click(next());
  await userEvent.selectOptions(await screen.findByLabelText("스토리지"), "s1");
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  await userEvent.click(next());
  await userEvent.type(screen.getByLabelText("top_k"), "100");
  await userEvent.click(screen.getByLabelText("quiet"));
  await userEvent.selectOptions(screen.getByLabelText("우선순위"), "high");
  await userEvent.type(screen.getByLabelText("노드 수"), "4");
  await userEvent.click(next());
  await userEvent.click(screen.getByRole("button", { name: "배치 생성" }));
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body).toMatchObject({
    options: { top_k: 100, quiet: true }, priority: "high", node_count: 4,
  });
});

test("sync: CSV 붙여넣기 반영 → 소스/목적지 짝 조립", async () => {
  const captured = captureCreate();
  renderPage();
  await userEvent.selectOptions(screen.getByLabelText("연산"), "sync");
  await userEvent.click(next());
  await userEvent.selectOptions(await screen.findByLabelText("소스 스토리지"), "s1");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "s2");
  await userEvent.click(screen.getByRole("button", { name: "CSV 붙여넣기" }));
  await userEvent.type(screen.getByLabelText("CSV"), "source,destination\na,b");
  await userEvent.click(screen.getByRole("button", { name: "테이블에 반영" }));
  await userEvent.click(next());
  await userEvent.click(next());
  await userEvent.click(screen.getByRole("button", { name: "배치 생성" }));
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body.items).toEqual([{
    source_storage: "s1", source: "a", destination_storage: "s2", destination: "b",
  }]);
});

test("CSV 오류 행이 있으면 다음 비활성 + 행 번호 문구", async () => {
  renderPage();
  await userEvent.selectOptions(screen.getByLabelText("연산"), "sync");
  await userEvent.click(next());
  await userEvent.selectOptions(await screen.findByLabelText("소스 스토리지"), "s1");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "s2");
  await userEvent.click(screen.getByRole("button", { name: "CSV 붙여넣기" }));
  await userEvent.type(screen.getByLabelText("CSV"), "a,b\nc");
  await userEvent.click(screen.getByRole("button", { name: "테이블에 반영" }));
  expect(await screen.findByText(/2행:/)).toBeInTheDocument();
  expect(next()).toBeDisabled();
});

test("verbose+quiet 상충: 즉답 문구 + 다음 비활성", async () => {
  renderPage();
  await userEvent.click(next());
  await userEvent.selectOptions(await screen.findByLabelText("스토리지"), "s1");
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  await userEvent.click(next());
  await userEvent.click(screen.getByLabelText("verbose"));
  await userEvent.click(screen.getByLabelText("quiet"));
  expect(screen.getByText("verbose와 quiet은 함께 쓸 수 없습니다")).toBeInTheDocument();
  expect(next()).toBeDisabled();
});

test("실행 제어 스텝: 특권 실행 고정 안내문 + 소유자 기록 입력이 바디·요약에 실린다", async () => {
  const captured = captureCreate();
  renderPage();
  await userEvent.click(next());
  await userEvent.selectOptions(await screen.findByLabelText("스토리지"), "s1");
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  await userEvent.click(next());                              // → 실행 제어
  // 통일 특권 게이트(routes_batches): 배치는 전부 관리자 특권(root) 실행 — 고정 안내
  expect(screen.getByText("이 배치는 관리자 특권(root)으로 실행됩니다.")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("소유자 기록(선택)"), "alice");
  await userEvent.click(next());                              // → 확인·제출
  // 확인 스텝 요약 = 제출 바디 파생 + 특권 실행 표시(고정 행)
  expect(screen.getByText("실행 권한")).toBeInTheDocument();
  expect(screen.getByText("관리자 특권(root)")).toBeInTheDocument();
  expect(screen.getByText("소유자 기록")).toBeInTheDocument();
  expect(screen.getByText("alice")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "배치 생성" }));
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body).toMatchObject({ owner_username: "alice" });
});

test("소유자 기록이 빈값이면 owner_username 키 부재 — 특권 실행 표시는 항상", async () => {
  const captured = captureCreate();
  renderPage();
  await userEvent.click(next());
  await userEvent.selectOptions(await screen.findByLabelText("스토리지"), "s1");
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  await userEvent.click(next());
  await userEvent.click(next());
  expect(screen.queryByText("소유자 기록")).toBeNull();       // 빈값 = 요약 행 부재(기본: 생성자)
  expect(screen.getByText("관리자 특권(root)")).toBeInTheDocument();  // 특권 표시는 고정
  await userEvent.click(screen.getByRole("button", { name: "배치 생성" }));
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body).not.toHaveProperty("owner_username");
});

// placeholder 계약(희미한 힌트): 각 입력에 실값과 혼동되지 않는 예시가 떠야 한다.
// 경로 예시는 스토리지 기준 상대경로(선행 슬래시 없음) 형태 — 도메인 계약의 가시화.
test("placeholder 힌트(scan): 경로·CSV·실행 제어 필드", async () => {
  renderPage();
  await userEvent.click(next());                              // → 대상·항목
  await userEvent.selectOptions(await screen.findByLabelText("스토리지"), "s1");
  expect(screen.getByLabelText("1행 경로")).toHaveAttribute("placeholder", "예: team/projects");
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  await userEvent.click(screen.getByRole("button", { name: "CSV 붙여넣기" }));
  expect(screen.getByLabelText("CSV")).toHaveAttribute("placeholder", "team\nprojects/alpha");
  await userEvent.click(next());                              // → 실행 제어
  expect(screen.getByLabelText("top_k")).toHaveAttribute("placeholder", "예: 100");
  expect(screen.getByLabelText("소유자 기록(선택)")).toHaveAttribute("placeholder", "예: cocoa.song");
  expect(screen.getByLabelText("노드 수")).toHaveAttribute("placeholder", "비우면 정책 기본");
  expect(screen.getByLabelText("동시 실행 상한")).toHaveAttribute("placeholder", "예: 2");
  expect(screen.getByLabelText("메모")).toHaveAttribute("placeholder", "예: 8월 정기 스캔");
});

test("placeholder 힌트(sync): 소스·목적지·CSV(2열 멀티라인)·고급 옵션", async () => {
  renderPage();
  await userEvent.selectOptions(screen.getByLabelText("연산"), "sync");
  await userEvent.click(next());                              // → 대상·항목
  await userEvent.selectOptions(await screen.findByLabelText("소스 스토리지"), "s1");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "s2");
  expect(screen.getByLabelText("1행 소스")).toHaveAttribute("placeholder", "예: team/dataset");
  expect(screen.getByLabelText("1행 목적지")).toHaveAttribute("placeholder", "예: backup/dataset");
  await userEvent.type(screen.getByLabelText("1행 소스"), "a");
  await userEvent.type(screen.getByLabelText("1행 목적지"), "b");
  await userEvent.click(screen.getByRole("button", { name: "CSV 붙여넣기" }));
  expect(screen.getByLabelText("CSV")).toHaveAttribute(
    "placeholder", "team/dataset,backup/dataset\nprojects/alpha,backup/alpha");
  await userEvent.click(next());                              // → 실행 제어
  expect(screen.getByLabelText("batch_files")).toHaveAttribute("placeholder", "예: 1000");
  expect(screen.getByLabelText("bufsize")).toHaveAttribute("placeholder", "예: 1048576");
  expect(screen.getByLabelText("chmod")).toHaveAttribute("placeholder", "예: D770,F660");
  expect(screen.getByLabelText("chown")).toHaveAttribute(
    "placeholder", "예: 10003:10000 또는 cocoa.song:mig");
});

// 정책 기본값 실값 캡션(실행 제어): 도구→정책 키는 placement.py TOOL_TO_POLICY 의
// 미러 — scan→"scan", sync→공존 노드면 "dsync", 없으면 "nsync" 폴백. 실값이 떠야
// "빈값 = 정책 기본"이 값 있는 말이 된다. 미조회·비활성은 숨기지 않는다(null≠0).
async function toScanControls() {
  await userEvent.click(next());                              // 연산(scan) → 대상·항목
  await userEvent.selectOptions(await screen.findByLabelText("스토리지"), "s1");
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  await userEvent.click(next());                              // → 실행 제어
}

test("정책 캡션(scan): scan 정책 실값이 노드 수 아래에 뜬다", async () => {
  renderPage();
  await toScanControls();
  expect(await screen.findByText(
    "정책 기본: 최대 4노드 · 노드당 8프로세스")).toBeInTheDocument();
});

test("정책 캡션(sync): dsync 실값 + nsync 폴백 실값 병기", async () => {
  renderPage();
  await userEvent.selectOptions(screen.getByLabelText("연산"), "sync");
  await userEvent.click(next());
  await userEvent.selectOptions(await screen.findByLabelText("소스 스토리지"), "s1");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "s2");
  await userEvent.type(screen.getByLabelText("1행 소스"), "a");
  await userEvent.type(screen.getByLabelText("1행 목적지"), "b");
  await userEvent.click(next());                              // → 실행 제어
  expect(await screen.findByText(
    "정책 기본(dsync): 최대 6노드 · 노드당 4프로세스 — 공존 노드가 없으면 "
    + "nsync 정책(최대 2노드 · 노드당 2프로세스)이 적용됩니다")).toBeInTheDocument();
});

test("정책 행이 없으면 생략·'—' 대신 정직한 미조회 문구", async () => {
  renderPage([]);
  await toScanControls();
  expect(await screen.findByText(
    "정책 미조회 — scan 정책 행이 없습니다")).toBeInTheDocument();
});

test("정책 disabled 는 캡션에 그 사실이 표기된다", async () => {
  renderPage([{ ...POLICIES[0], enabled: 0 }]);
  await toScanControls();
  expect(await screen.findByText(
    "정책 기본: 최대 4노드 · 노드당 8프로세스 · 비활성(잡 배치 거부)")).toBeInTheDocument();
});

test("동시 실행 상한 캡션: 배치 항목(잡) 수 의미 — 노드 수와 무관", async () => {
  renderPage();
  await toScanControls();
  expect(screen.getByText(
    "동시에 실행할 배치 항목(잡) 수 — 잡 하나가 쓰는 노드 수와 무관합니다.")).toBeInTheDocument();
});

test("파일 업로드: 로컬 FileReader 로 파싱해 테이블에 반영", async () => {
  renderPage();
  await userEvent.click(next());
  await userEvent.selectOptions(await screen.findByLabelText("스토리지"), "s1");
  await userEvent.click(screen.getByRole("button", { name: "파일 업로드" }));
  const file = new File(["target\nx/y"], "items.csv", { type: "text/csv" });
  await userEvent.upload(screen.getByLabelText("CSV 파일"), file);
  // 파싱 성공 → 테이블 편집 탭으로 복귀, 행이 반영돼 있다
  expect(await screen.findByDisplayValue("x/y")).toBeInTheDocument();
});
