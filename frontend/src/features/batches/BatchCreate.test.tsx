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

function renderPage() {
  server.use(http.get("/api/user/storages", () => HttpResponse.json(STORAGES)));
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
