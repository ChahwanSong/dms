import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { SubmitJob } from "./SubmitJob";
import { SYNC_INT_FIELDS } from "./optionRules";
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

// sync 고급 숫자 옵션의 프리필 기본값(사용자 조정 2026-08-16) — 폼이 값을 미리
// 채우므로 바디에 **항상** 실린다. 리터럴이 아니라 단일 출처를 읽어 폼과 테스트가
// 같은 값을 보게 한다(사본이면 프리필을 바꿀 때 테스트가 조용히 낡는다).
const SYNC_NUM_DEFAULTS = {
  batch_files: Number(SYNC_INT_FIELDS.batch_files.prefill),
  bufsize: Number(SYNC_INT_FIELDS.bufsize.prefill),
};

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
    // 계약 변경(사용자 조정 2026-08-16): batch_files·bufsize 는 폼이 **프리필**해
    // 항상 명시 전송된다. 예전엔 빈값이라 키가 통째로 빠졌다 — 지우면 다시 빠진다
    // (아래 "고급 숫자 옵션을 지우면…" 테스트가 그 성질을 지킨다).
    options: SYNC_NUM_DEFAULTS,
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

// 라벨 정정(사용자 결정 2026-08-16): 이 값(owner_username)은 아티팩트 소유자가
// 아니라 **잡의 실행 신원**을 정한다(identity.resolve_job_identity — owner =
// owner_username or requester_id). 필드는 그대로, 라벨·캡션만 사실에 맞춘다.
test("실행 신원 필드는 옵션 스텝에서 관리자에게만 보인다", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json(meUser)));
  const { unmount } = renderPage();
  await screen.findByLabelText("연산");
  await clickNext();
  await goToOptions();
  await screen.findByLabelText("우선순위");
  expect(screen.queryByLabelText("실행 신원(선택)")).not.toBeInTheDocument();
  unmount();

  server.use(http.get("/api/auth/me", () => HttpResponse.json(meAdmin)));
  renderPage();
  await screen.findByLabelText("연산");
  await clickNext();
  await goToOptions();
  expect(await screen.findByLabelText("실행 신원(선택)")).toBeInTheDocument();
  // 캡션은 "소유자 기록"이 아니라 실행 신원을 말한다 — 비우면 요청자 본인.
  expect(screen.getByText(
    "비우면 요청자 본인으로 실행됩니다. 다른 사용자를 지정하려면 특권 요청자여야 하며, "
    + "그때 잡은 root 로 실행되고 지정한 사용자 신원으로 파일을 다룹니다"
    + "(LDAP 에 없는 계정도 지정할 수 있습니다).",
  )).toBeInTheDocument();
});

// 선택 필드 표기 통일(사용자 지시 2026-08-16): 비워도 되는 입력은 라벨에 (선택).
test("비워도 되는 sync 옵션 입력은 라벨에 (선택) 이 붙는다", async () => {
  renderPage();
  await goToOptionsAndOpenAdvanced();
  expect(screen.getByText("batch_files (선택 · 1..10,000,000)")).toBeInTheDocument();
  expect(screen.getByText("bufsize (선택 · 바이트, 4096..1,073,741,824)")).toBeInTheDocument();
  expect(screen.getByText(
    "chmod (선택 · 예: D770,F660 — 콤마 구분, D=디렉터리 F=파일)")).toBeInTheDocument();
  expect(screen.getByText("chown (선택 · user:group 또는 uid:gid)")).toBeInTheDocument();
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

// 프리필 계약(사용자 조정 2026-08-16): batch_files·bufsize 는 placeholder 가 아니라
// **실제 값**으로 미리 채워져 있고, 그래서 손대지 않아도 바디에 실린다.
// batch_files 1,000,000 은 도구 기본(0 = 배칭 안 함)과 **다른 동작**이라 이건
// 의도된 정책이고, bufsize 4194304 는 도구 기본(4 MiB)과 같은 값의 명시다.
test("고급 숫자 옵션은 실제 값으로 프리필돼 있다(placeholder 아님)", async () => {
  renderPage();
  await goToOptionsAndOpenAdvanced();
  expect(screen.getByLabelText("batch_files")).toHaveValue(
    SYNC_INT_FIELDS.batch_files.prefill);
  expect(screen.getByLabelText("bufsize")).toHaveValue(
    SYNC_INT_FIELDS.bufsize.prefill);
  // 비웠을 때 무슨 일이 나는지는 placeholder·캡션이 말한다(빈값 = 도구 기본).
  expect(screen.getByLabelText("batch_files"))
    .toHaveAttribute("placeholder", "비우면 배칭 안 함(도구 기본)");
  expect(screen.getByLabelText("bufsize"))
    .toHaveAttribute("placeholder", "비우면 4 MiB(도구 기본)");
  expect(screen.getByText(
    "미리 채운 1,000,000 = 기본 배치 사이즈 100만. 비우면 배칭 안 함(도구 기본).",
  )).toBeInTheDocument();
  expect(screen.getByText(
    "미리 채운 4194304 = 4 MiB. 비우면 4 MiB(도구 기본).",
  )).toBeInTheDocument();
});

test("고급 숫자 옵션을 지우면 그 키가 바디에서 빠진다(도구 기본으로 복귀)", async () => {
  const captured = captureSubmit();
  renderPage();
  await goToOptionsAndOpenAdvanced();
  await userEvent.clear(screen.getByLabelText("batch_files"));
  await userEvent.clear(screen.getByLabelText("bufsize"));
  await goToConfirm();
  await userEvent.click(screen.getByRole("button", { name: "제출" }));
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  // 빈 문자열은 "미입력"이라 통째로 생략된다 — 프리필이 생겨도 이 성질은 남는다
  // (사용자가 배칭을 끄는 유일한 표현이다). bool 4종 checkedOptions 회귀 겸.
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
  expect(captured.body.options).toEqual({ ...SYNC_NUM_DEFAULTS, open_noatime: true });
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
  expect(captured.body.options).toEqual(
    { ...SYNC_NUM_DEFAULTS, chmod: "D770,F660", chown: "alice:proj" });
});

test("숫자 uid:gid chown 이 즉답 오류 없이 그대로 전송된다", async () => {
  // 서버 _CHOWN_RE 숫자 확장(domain.py)의 미러 검증 — optionRules CHOWN_RE 발산 금지.
  const captured = captureSubmit();
  renderPage();
  await goToOptionsAndOpenAdvanced();
  expect(screen.getByLabelText("chown")).toHaveAttribute(
    "placeholder", "예: 10003:10000 또는 cocoa.song:mig");
  await userEvent.type(screen.getByLabelText("chown"), "10003:10000");
  expect(screen.queryByText(/chown 형식이 올바르지 않습니다/)).toBeNull();
  await goToConfirm();
  await userEvent.click(screen.getByRole("button", { name: "제출" }));
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(captured.body.options).toEqual({ ...SYNC_NUM_DEFAULTS, chown: "10003:10000" });
});

test("batch_files·bufsize 숫자 입력은 number 로 전송된다", async () => {
  const captured = captureSubmit();
  renderPage();
  await goToOptionsAndOpenAdvanced();
  // 프리필 값을 지우고 사용자가 직접 넣는다 — 프리필은 기본이지 잠금이 아니다.
  await userEvent.clear(screen.getByLabelText("batch_files"));
  await userEvent.type(screen.getByLabelText("batch_files"), "1000");
  await userEvent.clear(screen.getByLabelText("bufsize"));
  await userEvent.type(screen.getByLabelText("bufsize"), "4096");
  await goToConfirm();
  await userEvent.click(screen.getByRole("button", { name: "제출" }));
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(captured.body.options).toEqual({ batch_files: 1000, bufsize: 4096 });
});

test("batch_files 상한은 1,000만 — 그 값은 통과, 넘으면 즉답 문구 + 다음 비활성", async () => {
  // 서버 _OPTION_SPECS[SYNC] 상한(사용자 조정 2026-08-16: 100만 → 1,000만)의 미러.
  renderPage();
  await goToOptionsAndOpenAdvanced();
  await userEvent.clear(screen.getByLabelText("batch_files"));
  await userEvent.type(screen.getByLabelText("batch_files"), "10000000");
  expect(screen.queryByText(/batch_files는/)).toBeNull();
  await userEvent.type(screen.getByLabelText("batch_files"), "0");   // → 1,000만 초과
  expect(screen.getByText("batch_files는 1..10000000 범위의 정수여야 합니다"))
    .toBeInTheDocument();
  expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
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
  await userEvent.clear(screen.getByLabelText("bufsize"));
  await userEvent.type(screen.getByLabelText("bufsize"), "100");
  expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
  expect(screen.getByText("bufsize는 4096..1073741824 범위의 정수여야 합니다")).toBeInTheDocument();
});
