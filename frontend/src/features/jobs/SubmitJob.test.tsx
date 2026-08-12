import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { SubmitJob } from "./SubmitJob";
import type { UserStorage } from "../../lib/types";
import type { Me } from "../../lib/types";

const storageRows: UserStorage[] = [
  { storage_name: "cephfs", backend_type: "cephfs", status: "Ready" },
  { storage_name: "cephfs-secondary", backend_type: "cephfs", status: "Ready" },
];
const meUser: Me = { actor: "alice", role: "user" };
const meAdmin: Me = { actor: "root", role: "admin" };

const server = setupServer(
  http.get("/api/auth/me", () => HttpResponse.json(meUser)),
  http.get("/api/user/storages", () => HttpResponse.json(storageRows)),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/jobs/new"]}>
        <Routes>
          <Route path="/jobs/new" element={<SubmitJob />} />
          <Route path="/jobs/:id" element={<h1>요청 상세</h1>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("스토리지 드롭다운이 API 목록으로 채워진다", async () => {
  renderPage();
  const sourceSelect = await screen.findByLabelText("소스 스토리지");
  expect(await within(sourceSelect).findByText("cephfs (Ready)")).toBeInTheDocument();
  expect(within(sourceSelect).getByText("cephfs-secondary (Ready)")).toBeInTheDocument();
});

test("연산을 rm으로 바꾸면 목적지 필드가 사라지고 대상 경로가 나타난다", async () => {
  renderPage();
  await screen.findByLabelText("소스 스토리지");
  expect(screen.getByLabelText("목적지 스토리지")).toBeInTheDocument();
  expect(screen.getByLabelText("목적지 경로")).toBeInTheDocument();
  expect(screen.queryByLabelText("대상 경로")).not.toBeInTheDocument();

  await userEvent.selectOptions(screen.getByLabelText("연산"), "rm");

  expect(screen.queryByLabelText("목적지 스토리지")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("목적지 경로")).not.toBeInTheDocument();
  expect(screen.getByLabelText("대상 경로")).toBeInTheDocument();
});

test("sync 제출 바디가 정확하다", async () => {
  let received: any = null;
  server.use(http.post("/api/user/requests", async ({ request }) => {
    received = await request.json();
    return HttpResponse.json({ request_id: "r1", state: "Pending" }, { status: 202 });
  }));
  renderPage();
  const sourceSelect = await screen.findByLabelText("소스 스토리지");
  await within(sourceSelect).findByText("cephfs (Ready)");
  await userEvent.selectOptions(sourceSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("소스 경로"), "a/b");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "cephfs-secondary");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "c/d");
  await userEvent.click(screen.getByRole("button", { name: "제출" }));

  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(received).toEqual({
    operation: "sync",
    source_storage: "cephfs", source: "a/b",
    destination_storage: "cephfs-secondary", destination: "c/d",
    options: {},
    priority: "mid",
  });
});

test("rm 제출 바디에 options.recursive가 true로 들어간다", async () => {
  let received: any = null;
  server.use(http.post("/api/user/requests", async ({ request }) => {
    received = await request.json();
    return HttpResponse.json({ request_id: "r2", state: "Pending" }, { status: 202 });
  }));
  renderPage();
  await userEvent.selectOptions(await screen.findByLabelText("연산"), "rm");
  const storageSelect = screen.getByLabelText("스토리지");
  await within(storageSelect).findByText("cephfs (Ready)");
  await userEvent.selectOptions(storageSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("대상 경로"), "a/b");
  await userEvent.click(screen.getByRole("button", { name: "제출" }));

  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(received.options.recursive).toBe(true);
  expect(received).toEqual({
    operation: "rm",
    storage: "cephfs", target: "a/b",
    options: { recursive: true },
    priority: "mid",
  });
});

test("recursive를 해제하면 제출 버튼이 비활성이다", async () => {
  renderPage();
  await userEvent.selectOptions(await screen.findByLabelText("연산"), "rm");
  expect(screen.getByRole("button", { name: "제출" })).toBeEnabled();

  await userEvent.click(screen.getByLabelText("재귀 삭제(필수)"));

  expect(screen.getByRole("button", { name: "제출" })).toBeDisabled();
  expect(screen.getByText("재귀 옵션이 필요합니다")).toBeInTheDocument();
});

test("stat과 lite를 동시에 체크하면 제출 버튼이 비활성이다", async () => {
  renderPage();
  await userEvent.selectOptions(await screen.findByLabelText("연산"), "rm");
  await userEvent.click(screen.getByLabelText("stat"));
  await userEvent.click(screen.getByLabelText("lite"));

  expect(screen.getByRole("button", { name: "제출" })).toBeDisabled();
  expect(screen.getByText("stat과 lite는 함께 쓸 수 없습니다")).toBeInTheDocument();
});

test("특권 필드는 관리자에게만 보인다", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json(meUser)));
  const { unmount } = renderPage();
  await screen.findByLabelText("소스 스토리지");
  expect(screen.queryByLabelText("관리자 특권 실행(root)")).not.toBeInTheDocument();
  unmount();

  server.use(http.get("/api/auth/me", () => HttpResponse.json(meAdmin)));
  renderPage();
  expect(await screen.findByLabelText("관리자 특권 실행(root)")).toBeInTheDocument();
});

test("스토리지 목록 로드가 실패하면 오류 메시지가 뜨고 제출 버튼이 비활성화된다", async () => {
  server.use(http.get("/api/user/storages", () =>
    HttpResponse.json({ detail: "storage_list_failed" }, { status: 500 })));
  renderPage();
  await screen.findByLabelText("소스 스토리지");
  expect(await screen.findByText("storage_list_failed")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "제출" })).toBeDisabled();
});

// ---- 고급 sync 옵션(슬라이스 26 Task 5) ----------------------------------
// 서버(domain.py _OPTION_SPECS SYNC)가 최종 심판이고, 폼은 노출+즉답 미러만 한다.
// msw 로 request body 를 캡처해 "무엇이 실제로 전송되는가"를 단언한다 — 빈 값
// 생략(truthy 검사 금지)과 number 변환이 계약이다.

function captureSubmit() {
  const captured: { body: any } = { body: null };
  server.use(http.post("/api/user/requests", async ({ request }) => {
    captured.body = await request.json();
    return HttpResponse.json({ request_id: "rX", state: "Pending" }, { status: 202 });
  }));
  return captured;
}

// sync 필수 필드를 채우고 <details> 고급 옵션을 펼친다(기본 접힘 확인 겸).
async function fillSyncFormAndOpenAdvanced() {
  const sourceSelect = await screen.findByLabelText("소스 스토리지");
  await within(sourceSelect).findByText("cephfs (Ready)");
  await userEvent.selectOptions(sourceSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("소스 경로"), "a/b");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "cephfs-secondary");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "c/d");
  await userEvent.click(screen.getByText("고급 옵션"));
}

test("고급 옵션 전부 미입력 제출이면 options에 고급 키 5종이 실리지 않는다", async () => {
  const captured = captureSubmit();
  renderPage();
  await fillSyncFormAndOpenAdvanced();
  await userEvent.click(screen.getByRole("button", { name: "제출" }));
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  // 빈 문자열은 "미입력"이라 통째로 생략된다 — 기존 checkedOptions(bool 4종) 회귀 겸.
  expect(captured.body.options).toEqual({});
});

test("open_noatime 체크는 options.open_noatime === true 로 전송된다", async () => {
  const captured = captureSubmit();
  renderPage();
  await fillSyncFormAndOpenAdvanced();
  await userEvent.click(screen.getByLabelText("open_noatime"));
  await userEvent.click(screen.getByRole("button", { name: "제출" }));
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(captured.body.options).toEqual({ open_noatime: true });
});

test("chmod·chown 문자열이 그대로 전송된다", async () => {
  const captured = captureSubmit();
  renderPage();
  await fillSyncFormAndOpenAdvanced();
  await userEvent.type(screen.getByLabelText("chmod"), "D770,F660");
  await userEvent.type(screen.getByLabelText("chown"), "alice:proj");
  await userEvent.click(screen.getByRole("button", { name: "제출" }));
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(captured.body.options).toEqual({ chmod: "D770,F660", chown: "alice:proj" });
});

test("batch_files·bufsize 숫자 입력은 number 로 전송된다", async () => {
  const captured = captureSubmit();
  renderPage();
  await fillSyncFormAndOpenAdvanced();
  await userEvent.type(screen.getByLabelText("batch_files"), "1000");
  await userEvent.type(screen.getByLabelText("bufsize"), "4096");
  await userEvent.click(screen.getByRole("button", { name: "제출" }));
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(captured.body.options).toEqual({ batch_files: 1000, bufsize: 4096 });
});

test("잘못된 chmod는 제출을 차단하고 필드별 문구를 띄운다", async () => {
  renderPage();
  await fillSyncFormAndOpenAdvanced();
  await userEvent.type(screen.getByLabelText("chmod"), "999x");
  expect(screen.getByRole("button", { name: "제출" })).toBeDisabled();
  expect(screen.getByText("chmod 형식이 올바르지 않습니다 (예: D770,F660)")).toBeInTheDocument();
});

test("범위 밖 bufsize는 제출을 차단한다", async () => {
  renderPage();
  await fillSyncFormAndOpenAdvanced();
  await userEvent.type(screen.getByLabelText("bufsize"), "100");
  expect(screen.getByRole("button", { name: "제출" })).toBeDisabled();
  expect(screen.getByText("bufsize는 4096..1073741824 범위의 정수여야 합니다")).toBeInTheDocument();
});
