import { render, screen } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { BatchesList } from "./BatchesList";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const row = (over: object = {}) => ({ batch_id: "b1234567890abcdef", operation: "scan",
  status: "Running", max_concurrency: 2, item_count: 3, succeeded_count: 1,
  failed_count: 0, note: null, created_at: "", ...over });

function renderList(rows: object[]) {
  server.use(http.get("/api/admin/batches", () => HttpResponse.json(rows)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><BatchesList /></MemoryRouter>
    </QueryClientProvider>);
}

test("상태 pill 은 batchPillVariant 색 — 상세 헤더와 동일 계약(Completed=ok·Running=busy)", async () => {
  renderList([row({ status: "Completed" }),
              row({ batch_id: "b2222222222222", status: "Running" })]);
  expect((await screen.findByText("Completed")).className).toContain("text-ok");
  expect(screen.getByText("Running").className).toContain("text-busy");
});

test("이름 컬럼: 있으면 이름, 없으면 — (빈칸으로 뭉개지 않는다)", async () => {
  renderList([row({ name: "8월 정기 스캔 1차" }),
              row({ batch_id: "b2222222222222", name: null })]);
  expect(await screen.findByText("8월 정기 스캔 1차")).toBeInTheDocument();
  expect(screen.getByText("이름")).toBeInTheDocument();       // 헤더
  expect(screen.getByText("—")).toBeInTheDocument();          // 이름 없음
});
