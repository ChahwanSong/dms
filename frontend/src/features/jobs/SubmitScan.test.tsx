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
  { storage_name: "cephfs", backend_type: "cephfs", status: "Ready" },
  { storage_name: "cephfs-secondary", backend_type: "cephfs", status: "Ready" },
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
  expect(await within(storageSelect).findByText("cephfs (Ready)")).toBeInTheDocument();
  expect(within(storageSelect).getByText("cephfs-secondary (Ready)")).toBeInTheDocument();
});

test("batch_files·broken_limit을 비우면 제출 바디에서 옵션이 빠진다(도구 기본)", async () => {
  let received: any = null;
  server.use(http.post("/api/user/requests", async ({ request }) => {
    received = await request.json();
    return HttpResponse.json({ request_id: "r1", state: "Pending" }, { status: 202 });
  }));
  renderPage();
  const storageSelect = await screen.findByLabelText("스토리지");
  await within(storageSelect).findByText("cephfs (Ready)");
  await userEvent.selectOptions(storageSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("대상 경로"), "a/b");
  // top_k는 신 dscan(1b93d54)에서 기능 삭제 — 입력 자체가 없어야 한다.
  expect(screen.queryByLabelText("top_k")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "제출" }));

  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(received).toEqual({
    operation: "scan",
    storage: "cephfs", target: "a/b",
    options: {},
    priority: "mid",
  });
});

test("batch_files 0(배칭 끔)·broken_limit 500이 옵션에 숫자로 들어간다", async () => {
  let received: any = null;
  server.use(http.post("/api/user/requests", async ({ request }) => {
    received = await request.json();
    return HttpResponse.json({ request_id: "r2", state: "Pending" }, { status: 202 });
  }));
  renderPage();
  const storageSelect = await screen.findByLabelText("스토리지");
  await within(storageSelect).findByText("cephfs (Ready)");
  await userEvent.selectOptions(storageSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("대상 경로"), "a/b");
  await userEvent.type(screen.getByLabelText("batch_files"), "0");
  await userEvent.type(screen.getByLabelText("broken_limit"), "500");
  await userEvent.click(screen.getByRole("button", { name: "제출" }));

  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(received).toEqual({
    operation: "scan",
    storage: "cephfs", target: "a/b",
    options: { batch_files: 0, broken_limit: 500 },
    priority: "mid",
  });
});

test("placeholder에 도구 기본값이 명시된다 — 빈값 = 플래그 생략 = 도구 기본", async () => {
  renderPage();
  await screen.findByLabelText("스토리지");
  expect(screen.getByLabelText("batch_files"))
    .toHaveAttribute("placeholder", "기본 1000000 · 0 = 배칭 끔");
  expect(screen.getByLabelText("broken_limit"))
    .toHaveAttribute("placeholder", "기본 100");
});

test("broken_limit 범위 밖이면 즉답 문구 + 제출 비활성", async () => {
  renderPage();
  await screen.findByLabelText("스토리지");
  await userEvent.type(screen.getByLabelText("broken_limit"), "10001");
  expect(screen.getByText("broken_limit는 0..10000 범위의 정수여야 합니다")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "제출" })).toBeDisabled();
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

// --- 관리 디렉토리 캡션(사용자 보고 2026-08-15): 이름만 보이고 뿌리가 안 보여
// 「정확한 path 를 알 수가 없다」던 문제. 서버가 managed_root 를 **관리자 응답에만**
// 싣기 때문에(routes_storages), 없으면 캡션도 없다 — 화면이 역할을 흉내 내 판정하지
// 않는다.
test("스토리지를 고르면 관리 디렉토리와 「이 아래 상대경로」 안내가 뜬다", async () => {
  server.use(http.get("/api/user/storages", () => HttpResponse.json([
    { storage_name: "cephfs", backend_type: "cephfs", status: "Ready",
      managed_root: "/cephfs/dms" }])));
  renderPage();
  const storageSelect = await screen.findByLabelText("스토리지");
  await within(storageSelect).findByText("cephfs (Ready)");
  // 고르기 전에는 캡션이 없다 — 어느 뿌리인지 아직 정해지지 않았다
  expect(screen.queryByText(/관리 디렉토리/)).toBeNull();
  await userEvent.selectOptions(storageSelect, "cephfs");
  expect(screen.getByText(
    "관리 디렉토리: /cephfs/dms — 입력 경로는 이 아래 상대경로입니다"))
    .toBeInTheDocument();
});

test("managed_root 가 없으면(비관리자 응답) 캡션도 없다 — 경로가 새지 않는다", async () => {
  renderPage();                                  // 기본 fixture 에는 managed_root 가 없다
  const storageSelect = await screen.findByLabelText("스토리지");
  await within(storageSelect).findByText("cephfs (Ready)");
  await userEvent.selectOptions(storageSelect, "cephfs");
  expect(screen.queryByText(/관리 디렉토리/)).toBeNull();
});

test("스토리지 목록 로드가 실패하면 오류 메시지가 뜨고 제출 버튼이 비활성화된다", async () => {
  server.use(http.get("/api/user/storages", () =>
    HttpResponse.json({ detail: "storage_list_failed" }, { status: 500 })));
  renderPage();
  await screen.findByLabelText("스토리지");
  expect(await screen.findByText("storage_list_failed")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "제출" })).toBeDisabled();
});
