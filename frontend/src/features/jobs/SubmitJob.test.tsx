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
// pristine sync 폼이 항상 싣는 옵션(2026-08-22): open_noatime 기본 ON + 숫자 프리필.
const SYNC_DEFAULT_OPTS = { open_noatime: true, ...SYNC_NUM_DEFAULTS };

// 기본 me = admin(2026-08-20): rm·scan·고급옵션·우선순위·실행신원은 운영자
// 전용이라, 그 기능들을 다루는 대다수 테스트는 admin 컨텍스트여야 한다. 사용자
// 제약(sync 만·옵션 단순화)은 meUser 를 명시한 테스트가 따로 고정한다.
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
  // 옵션 텍스트는 이름만(상태 접미 제거, 2026-08-22) -- 정확 매칭으로 기다린다
  // (cephfs 는 cephfs-secondary 의 부분 문자열이라 exact 필수).
  await within(sourceSelect).findByRole("option", { name: "cephfs" });
  await userEvent.selectOptions(sourceSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("소스 경로"), "a/b");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "cephfs-secondary");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "c/d");
}

// 연산 스텝(초기)에서 rm 을 고르고 → 대상 스텝에서 스토리지·경로를 채운다.
// rm 은 이제 admin 전용이라 me 로드 후에야 옵션이 생긴다 -- 옵션을 기다린 뒤 고른다.
async function fillRmTarget() {
  await screen.findByRole("option", { name: "rm" });
  await userEvent.selectOptions(screen.getByLabelText("연산"), "rm");
  await clickNext();
  const storageSelect = screen.getByLabelText("스토리지");
  await within(storageSelect).findByRole("option", { name: "cephfs" });
  await userEvent.selectOptions(storageSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("대상 경로"), "a/b");
}

async function goToOptions() { await clickNext(); }  // 대상 → 옵션
async function goToConfirm() { await clickNext(); }  // 옵션 → 확인·제출

test("대상 sanity: sync 경로가 비면 다음이 잠기고 문구가 뜬다(운영자/사용자 공통)", async () => {
  // 사용자 결정(2026-08-22): 스토리지·경로 미입력이면 다음으로 못 넘어간다.
  renderPage();
  await screen.findByLabelText("연산");
  await clickNext();                                  // → 대상 스텝
  await screen.findByLabelText("소스 스토리지");
  // 아무것도 안 채운 상태: 다음 비활성 + 안내
  expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
  expect(screen.getByText("소스·목적지 스토리지와 경로를 모두 입력하세요")).toBeInTheDocument();
  // 스토리지만 고르고 경로는 비움 → 여전히 잠김
  const srcSel = screen.getByLabelText("소스 스토리지");
  await within(srcSel).findByRole("option", { name: "cephfs" });   // 목록 로드 대기(스코프)
  await userEvent.selectOptions(srcSel, "cephfs");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "cephfs-secondary");
  expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
  // 경로까지 채우면 풀린다
  await userEvent.type(screen.getByLabelText("소스 경로"), "a");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "b");
  expect(screen.getByRole("button", { name: "다음" })).toBeEnabled();
});

test("대상 sanity: rm 은 대상 경로가 비면 다음이 잠긴다", async () => {
  renderPage();
  await screen.findByRole("option", { name: "rm" });
  await userEvent.selectOptions(screen.getByLabelText("연산"), "rm");
  await clickNext();
  await userEvent.selectOptions(screen.getByLabelText("스토리지"), "cephfs");
  expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
  expect(screen.getByText("스토리지와 대상 경로를 입력하세요")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("대상 경로"), "x");
  expect(screen.getByRole("button", { name: "다음" })).toBeEnabled();
});

test("스토리지 드롭다운이 API 목록으로 채워진다", async () => {
  renderPage();
  await screen.findByLabelText("연산");
  await clickNext();
  const sourceSelect = await screen.findByLabelText("소스 스토리지");
  // 이름만(상태 접미 없음). 정확 매칭으로 두 옵션을 구분.
  expect(await within(sourceSelect).findByRole("option", { name: "cephfs" })).toBeInTheDocument();
  expect(within(sourceSelect).getByRole("option", { name: "cephfs-secondary" })).toBeInTheDocument();
  // 상태 접미(Ready/Degraded)는 더 이상 노출되지 않는다.
  expect(within(sourceSelect).queryByText(/\(Ready\)|\(Degraded\)/)).not.toBeInTheDocument();
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
  await screen.findByRole("option", { name: "rm" });   // rm 은 admin 로드 후 등장
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
    // 계약: batch_files·bufsize 프리필 + open_noatime 기본 ON(2026-08-22) 이
    // 항상 실린다. 숫자는 지우면 빠지고(아래 테스트), open_noatime 은 해제하면 빠진다.
    options: SYNC_DEFAULT_OPTS,
    // priority 생략 = (정책 기본) — 슬라이스 37, resolve_priority 가 정책값 해석
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
    // priority 생략 = (정책 기본) — 슬라이스 37, resolve_priority 가 정책값 해석
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
  await fillSyncTarget();   // 대상 sanity: 경로를 채워야 옵션 스텝으로 넘어간다
  await goToOptions();
  await screen.findByLabelText("delete");   // 사용자 옵션 스텝의 앵커(우선순위는 숨김)
  expect(screen.queryByLabelText("실행 신원(선택)")).not.toBeInTheDocument();
  unmount();

  server.use(http.get("/api/auth/me", () => HttpResponse.json(meAdmin)));
  renderPage();
  await fillSyncTarget();
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
  // 스토리지를 못 고르니 대상 sanity(스토리지·경로 필수)가 대상 스텝에서 다음을
  // 잠근다 -- 목록 실패가 곧 진행 불가로 이어진다(2026-08-22 sanity 게이트).
  expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
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
  // 빈 문자열은 "미입력"이라 batch_files·bufsize 는 통째로 생략된다. open_noatime
  // 은 기본 ON(2026-08-22)이라 손대지 않으면 남는다 -- 숫자 생략과 독립.
  expect(captured.body.options).toEqual({ open_noatime: true });
});

test("open_noatime 은 기본 ON 이라 손대지 않으면 true 로 실리고, 해제하면 빠진다", async () => {
  // 사용자 결정(2026-08-22): 단건 sync 도 open_noatime 기본 ON. 운영자는 고급
  // 옵션에서 끌 수 있고, 끄면 checkedOptions 가 false 를 생략한다.
  const captured = captureSubmit();
  renderPage();
  await goToOptionsAndOpenAdvanced();
  expect(screen.getByLabelText("open_noatime")).toBeChecked();
  await userEvent.click(screen.getByLabelText("open_noatime"));  // 해제
  await goToConfirm();
  await userEvent.click(screen.getByRole("button", { name: "제출" }));
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(captured.body.options).toEqual(SYNC_NUM_DEFAULTS);   // open_noatime 빠짐
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
    { ...SYNC_DEFAULT_OPTS, chmod: "D770,F660", chown: "alice:proj" });
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
  expect(captured.body.options).toEqual({ ...SYNC_DEFAULT_OPTS, chown: "10003:10000" });
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
  expect(captured.body.options).toEqual({ open_noatime: true, batch_files: 1000, bufsize: 4096 });
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

// ---- 슬라이스 37: scan 연산(운영자 전용) ------------------------------------

test("비관리자에겐 sync 만 — scan·rm 은 숨김(표시 게이트 · 서버 403 이 진짜 차단)", async () => {
  // 사용자 연산 allowlist(2026-08-20, 사용자 결정): 사용자는 sync 만. scan·rm 은
  // 운영자 전용이라 드롭다운에서 빠진다.
  server.use(http.get("/api/auth/me", () => HttpResponse.json(meUser)));
  renderPage();
  const select = (await screen.findByLabelText("연산")) as HTMLSelectElement;
  expect(Array.from(select.options).map((o) => o.value)).toEqual(["sync"]);
});

test("비관리자 sync 옵션은 delete·contents 만 — direct·quiet·고급·우선순위 숨김", async () => {
  // 사용자 결정(2026-08-20): 사용자 단일작업 sync 폼은 최소만 노출한다.
  server.use(http.get("/api/auth/me", () => HttpResponse.json(meUser)));
  renderPage();
  await fillSyncTarget();   // 대상 sanity 통과 후 옵션 스텝
  await goToOptions();
  expect(screen.getByLabelText("delete")).toBeInTheDocument();
  expect(screen.getByLabelText("contents")).toBeInTheDocument();
  expect(screen.queryByLabelText("direct")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("quiet")).not.toBeInTheDocument();
  expect(screen.queryByText("고급 옵션")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("우선순위")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("실행 신원(선택)")).not.toBeInTheDocument();
});

test("사용자 sync 제출은 open_noatime 을 싣지 않는다(기본 OFF — 운영자만 ON)", async () => {
  // 사용자 결정(2026-08-22): 사용자 요청은 open_noatime 기본 OFF(비특권 실행의
  // EPERM 회피). 사용자 폼엔 이 옵션이 숨겨져 있고 제출 시 isAdmin 으로 끊긴다.
  server.use(http.get("/api/auth/me", () => HttpResponse.json(meUser)));
  const captured = captureSubmit();
  renderPage();
  await fillSyncTarget();
  await goToOptions();
  await goToConfirm();
  await userEvent.click(screen.getByRole("button", { name: "제출" }));
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  // 숫자 프리필(batch_files·bufsize)은 실리되 open_noatime 은 빠진다.
  expect(captured.body.options).toEqual(SYNC_NUM_DEFAULTS);
  expect(captured.body.options.open_noatime).toBeUndefined();
});

test("admin scan 제출 바디가 정확하다(옵션 생략 = 도구 기본)", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json(meAdmin)));
  let posted: unknown = null;
  server.use(http.post("/api/user/requests", async ({ request }) => {
    posted = await request.json();
    return HttpResponse.json({ request_id: "r1", state: "Pending" }, { status: 202 });
  }));
  renderPage();
  const select = (await screen.findByLabelText("연산")) as HTMLSelectElement;
  // admin 은 세 연산 전부 -- scan 은 sync 와 rm 사이.
  await screen.findByRole("option", { name: "scan" });
  expect(Array.from(select.options).map((o) => o.value)).toEqual(["sync", "scan", "rm"]);
  await userEvent.selectOptions(select, "scan");
  await userEvent.click(screen.getByRole("button", { name: "다음" }));
  await userEvent.selectOptions(screen.getByLabelText("스토리지"), "cephfs");
  await userEvent.type(screen.getByLabelText("대상 경로"), "team/data");
  await userEvent.click(screen.getByRole("button", { name: "다음" }));
  await userEvent.type(screen.getByLabelText("broken_limit"), "500");
  await userEvent.click(screen.getByRole("button", { name: "다음" }));
  await userEvent.click(screen.getByRole("button", { name: "제출" }));
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(posted).toEqual({
    operation: "scan", storage: "cephfs", target: "team/data",
    options: { broken_limit: 500 },
  });
});

test("scan 의 verbose·quiet 동시는 다음을 잠근다", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json(meAdmin)));
  renderPage();
  await screen.findByRole("option", { name: "scan" });
  await userEvent.selectOptions(screen.getByLabelText("연산"), "scan");
  await userEvent.click(screen.getByRole("button", { name: "다음" }));
  await userEvent.selectOptions(screen.getByLabelText("스토리지"), "cephfs");
  await userEvent.type(screen.getByLabelText("대상 경로"), "a");
  await userEvent.click(screen.getByRole("button", { name: "다음" }));
  await userEvent.click(screen.getByLabelText("verbose"));
  await userEvent.click(screen.getByLabelText("quiet"));
  expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
  expect(screen.getByText("verbose와 quiet는 함께 쓸 수 없습니다")).toBeInTheDocument();
});
