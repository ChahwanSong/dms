import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { SubmitJob } from "./SubmitJob";
import type { UserStorage } from "../../lib/types";
import type { Me } from "../../lib/types";

// 슬라이스 31 T4: SubmitJob 이 4스텝 위저드(연산→대상→옵션→확인·제출)가 됐다.
// 동선은 "다음" 클릭으로 다시 썼지만 제출 바디 toEqual 단언은 원문 보존 --
// 리디자인이 전송 계약(서버·e2e 접점)을 안 건드렸다는 증거다.

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

// ---- 위저드 동선 헬퍼 ------------------------------------------------------
// 각 헬퍼는 "직전 스텝에 서 있다"를 전제로 한 스텝만 전진한다 -- 여러 스텝을
// 한 번에 건너뛰는 헬퍼를 만들면 스텝 국소 검증(canNext)이 어디서 걸렸는지
// 실패 메시지로 구분할 수 없게 된다.

async function clickNext() {
  await userEvent.click(screen.getByRole("button", { name: "다음" }));
}

// 연산 스텝(초기, sync 기본값) → 대상 스텝으로 가서 sync 4필드를 채운다.
async function fillSyncTarget() {
  await screen.findByLabelText("연산");
  await clickNext();
  const sourceSelect = await screen.findByLabelText("소스 스토리지");
  await within(sourceSelect).findByText("cephfs (Ready)");
  await userEvent.selectOptions(sourceSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("소스 경로"), "a/b");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "cephfs-secondary");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "c/d");
}

// 연산 스텝(초기)에서 rm 을 고르고 → 대상 스텝에서 스토리지·경로를 채운다.
async function fillRmTarget() {
  await userEvent.selectOptions(await screen.findByLabelText("연산"), "rm");
  await clickNext();
  const storageSelect = screen.getByLabelText("스토리지");
  await within(storageSelect).findByText("cephfs (Ready)");
  await userEvent.selectOptions(storageSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("대상 경로"), "a/b");
}

async function goToOptions() { await clickNext(); }  // 대상 → 옵션
async function goToConfirm() { await clickNext(); }  // 옵션 → 확인·제출

test("스토리지 드롭다운이 API 목록으로 채워진다", async () => {
  renderPage();
  await screen.findByLabelText("연산");
  await clickNext();
  const sourceSelect = await screen.findByLabelText("소스 스토리지");
  expect(await within(sourceSelect).findByText("cephfs (Ready)")).toBeInTheDocument();
  expect(within(sourceSelect).getByText("cephfs-secondary (Ready)")).toBeInTheDocument();
});

test("연산을 rm으로 바꾸면 대상 스텝의 필드 구성이 바뀐다", async () => {
  renderPage();
  await screen.findByLabelText("연산");
  await clickNext();
  expect(await screen.findByLabelText("목적지 스토리지")).toBeInTheDocument();
  expect(screen.getByLabelText("목적지 경로")).toBeInTheDocument();
  expect(screen.queryByLabelText("대상 경로")).not.toBeInTheDocument();

  // "이전"으로 연산 스텝에 돌아가 rm 전환 -- 위저드에서도 값 상태는 스텝 밖
  // 단일 useState 라 전환 정책(현행 유지)이 그대로 적용된다.
  await userEvent.click(screen.getByRole("button", { name: "이전" }));
  await userEvent.selectOptions(screen.getByLabelText("연산"), "rm");
  expect(screen.getByText(
    "삭제는 되돌릴 수 없습니다. 미리보기에서 대상을 확인한 뒤 확인해야 실행됩니다.",
  )).toBeInTheDocument();

  await clickNext();
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
  await fillSyncTarget();
  await goToOptions();
  await goToConfirm();
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
  await fillRmTarget();
  await goToOptions();
  await goToConfirm();
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

// 강제 submit 이벤트: 위저드 버튼은 전부 type=button 이라 정상 동선으론 form
// submit 이 안 나지만, 가드(if (blocked) return)는 Enter 유출·미래 회귀에 대한
// 이중 방어다 -- 이벤트를 직접 쏴서 가드가 살아 있음을 관측한다(뮤테이션 표적).
async function forceSubmitAndSettle(form: HTMLFormElement) {
  fireEvent.submit(form);
  // mutate → msw 왕복이 비동기라, 잘못 전송됐다면 캡처가 도착할 시간을 준다.
  await new Promise((r) => setTimeout(r, 150));
}

test("recursive를 해제하면 다음이 비활성이고 강제 submit도 차단된다", async () => {
  let received: any = null;
  server.use(http.post("/api/user/requests", async ({ request }) => {
    received = await request.json();
    return HttpResponse.json({ request_id: "rG", state: "Pending" }, { status: 202 });
  }));
  const { container } = renderPage();
  await fillRmTarget();
  await goToOptions();
  expect(screen.getByRole("button", { name: "다음" })).toBeEnabled();

  await userEvent.click(screen.getByLabelText("재귀 삭제(필수)"));

  expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
  expect(screen.getByText("재귀 옵션이 필요합니다")).toBeInTheDocument();

  await forceSubmitAndSettle(container.querySelector("form")!);
  expect(received).toBeNull();
  expect(screen.queryByRole("heading", { name: "요청 상세" })).not.toBeInTheDocument();
});

test("stat과 lite를 동시에 체크하면 다음이 비활성이고 강제 submit도 차단된다", async () => {
  let received: any = null;
  server.use(http.post("/api/user/requests", async ({ request }) => {
    received = await request.json();
    return HttpResponse.json({ request_id: "rG", state: "Pending" }, { status: 202 });
  }));
  const { container } = renderPage();
  await fillRmTarget();
  await goToOptions();
  await userEvent.click(screen.getByLabelText("stat"));
  await userEvent.click(screen.getByLabelText("lite"));

  expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
  expect(screen.getByText("stat과 lite는 함께 쓸 수 없습니다")).toBeInTheDocument();

  await forceSubmitAndSettle(container.querySelector("form")!);
  expect(received).toBeNull();
  expect(screen.queryByRole("heading", { name: "요청 상세" })).not.toBeInTheDocument();
});

test("특권 필드는 옵션 스텝에서 관리자에게만 보인다", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json(meUser)));
  const { unmount } = renderPage();
  await screen.findByLabelText("연산");
  await clickNext();
  await goToOptions();
  await screen.findByLabelText("우선순위");
  expect(screen.queryByLabelText("관리자 특권 실행(root)")).not.toBeInTheDocument();
  unmount();

  server.use(http.get("/api/auth/me", () => HttpResponse.json(meAdmin)));
  renderPage();
  await screen.findByLabelText("연산");
  await clickNext();
  await goToOptions();
  expect(await screen.findByLabelText("관리자 특권 실행(root)")).toBeInTheDocument();
});

test("스토리지 목록 로드가 실패하면 대상 스텝에 오류가 뜨고 제출이 비활성이다", async () => {
  server.use(http.get("/api/user/storages", () =>
    HttpResponse.json({ detail: "storage_list_failed" }, { status: 500 })));
  renderPage();
  await screen.findByLabelText("연산");
  await clickNext();
  await screen.findByLabelText("소스 스토리지");
  expect(await screen.findByText("storage_list_failed")).toBeInTheDocument();
  // 대상·옵션 스텝은 통과 가능(차단은 기존 blocked 그대로 제출 지점에서).
  await goToOptions();
  await goToConfirm();
  expect(screen.getByRole("button", { name: "제출" })).toBeDisabled();
});

test("확인 스텝 요약에 rm 경고와 대상이 노출된다", async () => {
  renderPage();
  await fillRmTarget();
  await goToOptions();
  await goToConfirm();
  expect(screen.getByText(
    "삭제는 되돌릴 수 없습니다. 미리보기에서 대상을 확인한 뒤 확인해야 실행됩니다.",
  )).toBeInTheDocument();
  // 요약이 실제 입력값의 함수임을 확인(빈 껍데기 요약 방지).
  expect(screen.getByText("cephfs:a/b")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "제출" })).toBeInTheDocument();
});

// ---- 고급 sync 옵션(슬라이스 26 Task 5 → T4 위저드 동선) --------------------
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

// sync 대상을 채우고 옵션 스텝에서 <details> 고급 옵션을 펼친다(기본 접힘 확인 겸).
async function goToOptionsAndOpenAdvanced() {
  await fillSyncTarget();
  await goToOptions();
  await userEvent.click(screen.getByText("고급 옵션"));
}

test("고급 옵션 전부 미입력 제출이면 options에 고급 키 5종이 실리지 않는다", async () => {
  const captured = captureSubmit();
  renderPage();
  await goToOptionsAndOpenAdvanced();
  await goToConfirm();
  await userEvent.click(screen.getByRole("button", { name: "제출" }));
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  // 빈 문자열은 "미입력"이라 통째로 생략된다 — 기존 checkedOptions(bool 4종) 회귀 겸.
  expect(captured.body.options).toEqual({});
});

test("open_noatime 체크는 options.open_noatime === true 로 전송된다", async () => {
  const captured = captureSubmit();
  renderPage();
  await goToOptionsAndOpenAdvanced();
  await userEvent.click(screen.getByLabelText("open_noatime"));
  await goToConfirm();
  await userEvent.click(screen.getByRole("button", { name: "제출" }));
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(captured.body.options).toEqual({ open_noatime: true });
});

test("chmod·chown 문자열이 그대로 전송된다", async () => {
  const captured = captureSubmit();
  renderPage();
  await goToOptionsAndOpenAdvanced();
  await userEvent.type(screen.getByLabelText("chmod"), "D770,F660");
  await userEvent.type(screen.getByLabelText("chown"), "alice:proj");
  await goToConfirm();
  await userEvent.click(screen.getByRole("button", { name: "제출" }));
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(captured.body.options).toEqual({ chmod: "D770,F660", chown: "alice:proj" });
});

test("batch_files·bufsize 숫자 입력은 number 로 전송된다", async () => {
  const captured = captureSubmit();
  renderPage();
  await goToOptionsAndOpenAdvanced();
  await userEvent.type(screen.getByLabelText("batch_files"), "1000");
  await userEvent.type(screen.getByLabelText("bufsize"), "4096");
  await goToConfirm();
  await userEvent.click(screen.getByRole("button", { name: "제출" }));
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(captured.body.options).toEqual({ batch_files: 1000, bufsize: 4096 });
});

test("잘못된 chmod는 다음을 비활성으로 막고 필드별 문구를 띄운다", async () => {
  renderPage();
  await goToOptionsAndOpenAdvanced();
  await userEvent.type(screen.getByLabelText("chmod"), "999x");
  expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
  expect(screen.getByText("chmod 형식이 올바르지 않습니다 (예: D770,F660)")).toBeInTheDocument();
});

test("범위 밖 bufsize는 다음을 비활성으로 막는다", async () => {
  renderPage();
  await goToOptionsAndOpenAdvanced();
  await userEvent.type(screen.getByLabelText("bufsize"), "100");
  expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
  expect(screen.getByText("bufsize는 4096..1073741824 범위의 정수여야 합니다")).toBeInTheDocument();
});
