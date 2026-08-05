import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { SubmitScan } from "./SubmitScan";
import type { UserStorage } from "../../lib/types";
import type { Me } from "../../lib/types";

const storageRows: UserStorage[] = [
  { storage_name: "cephfs", backend_type: "cephfs", status: "ready" },
  { storage_name: "cephfs-secondary", backend_type: "cephfs", status: "ready" },
];
const meAdmin: Me = { actor: "root", role: "admin" };

const server = setupServer(
  http.get("/api/auth/me", () => HttpResponse.json(meAdmin)),
  http.get("/api/user/storages", () => HttpResponse.json(storageRows)),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/admin/scan"]}>
        <Routes>
          <Route path="/admin/scan" element={<SubmitScan />} />
          <Route path="/jobs/:id" element={<h1>요청 상세</h1>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("스토리지 드롭다운이 API 목록으로 채워진다", async () => {
  renderPage();
  const storageSelect = await screen.findByLabelText("스토리지");
  expect(await within(storageSelect).findByText("cephfs (ready)")).toBeInTheDocument();
  expect(within(storageSelect).getByText("cephfs-secondary (ready)")).toBeInTheDocument();
});

test("top_k를 비우면 제출 바디에서 옵션이 빠진다", async () => {
  let received: any = null;
  server.use(http.post("/api/user/requests", async ({ request }) => {
    received = await request.json();
    return HttpResponse.json({ request_id: "r1", state: "Pending" }, { status: 202 });
  }));
  renderPage();
  const storageSelect = await screen.findByLabelText("스토리지");
  await within(storageSelect).findByText("cephfs (ready)");
  await userEvent.selectOptions(storageSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("대상 경로"), "a/b");
  await userEvent.click(screen.getByRole("button", { name: "제출" }));

  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(received).toEqual({
    operation: "scan",
    storage: "cephfs", target: "a/b",
    options: {},
    priority: "mid",
  });
});

test("top_k에 5를 넣으면 옵션에 숫자 5로 들어간다", async () => {
  let received: any = null;
  server.use(http.post("/api/user/requests", async ({ request }) => {
    received = await request.json();
    return HttpResponse.json({ request_id: "r2", state: "Pending" }, { status: 202 });
  }));
  renderPage();
  const storageSelect = await screen.findByLabelText("스토리지");
  await within(storageSelect).findByText("cephfs (ready)");
  await userEvent.selectOptions(storageSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("대상 경로"), "a/b");
  await userEvent.type(screen.getByLabelText("top_k"), "5");
  await userEvent.click(screen.getByRole("button", { name: "제출" }));

  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(received.options.top_k).toBe(5);
  expect(received).toEqual({
    operation: "scan",
    storage: "cephfs", target: "a/b",
    options: { top_k: 5 },
    priority: "mid",
  });
});

test("verbose와 quiet을 동시에 체크하면 제출 버튼이 비활성이다", async () => {
  renderPage();
  await screen.findByLabelText("스토리지");
  expect(screen.getByRole("button", { name: "제출" })).toBeEnabled();

  await userEvent.click(screen.getByLabelText("verbose"));
  await userEvent.click(screen.getByLabelText("quiet"));

  expect(screen.getByRole("button", { name: "제출" })).toBeDisabled();
  expect(screen.getByText("verbose와 quiet은 함께 쓸 수 없습니다")).toBeInTheDocument();
});
