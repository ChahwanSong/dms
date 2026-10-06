import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient, focusManager } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { SubmitJob } from "./SubmitJob";
import { SCAN_INT_FIELDS, SYNC_INT_FIELDS } from "./optionRules";
import type { UserStorage } from "../../lib/types";
import type { Me } from "../../lib/types";

// 2026-10-06: SubmitJob 이 4스텝 위저드에서 한 장짜리 시트(번호 구획) + 오른쪽 제출 요약이 됐다.
// "다음"·"이전" 동선은 사라지고, 스텝별 '다음' 잠금 단언은 **제출 버튼** 잠금으로 옮겼다. 제출 바디
// toEqual 단언은 원문 보존 -- 레이아웃 개편이 전송 계약(서버·e2e 접점)을 안 건드렸다는 증거다.

const storageRows: UserStorage[] = [
  { storage_name: "cephfs", backend_type: "cephfs", status: "Ready" },
  { storage_name: "cephfs-secondary", backend_type: "cephfs", status: "Ready" },
];
const meUser: Me = { actor: "alice", role: "user" };
// 관리자 기본 = root 자격 있음(특권 목록 + 세션) -- 서버 me.can_run_as_root. 자격 없는 관리자는
// meAdminNoRoot 로 따로 고정한다.
const meAdmin: Me = { actor: "root", role: "admin", can_run_as_root: true };
const meAdminNoRoot: Me = { actor: "ops2", role: "admin", can_run_as_root: false };

// sync 고급 숫자 옵션의 프리필 기본값(사용자 조정 2026-08-16) — 폼이 값을 미리
// 채우므로 바디에 **항상** 실린다. 리터럴이 아니라 단일 출처를 읽어 폼과 테스트가
// 같은 값을 보게 한다(사본이면 프리필을 바꿀 때 테스트가 조용히 낡는다).
const SYNC_NUM_DEFAULTS = {
  batch_files: Number(SYNC_INT_FIELDS.batch_files.prefill),
  bufsize: Number(SYNC_INT_FIELDS.bufsize.prefill),
};
// pristine 관리자 sync 폼이 싣는 옵션: open_noatime(기본 ON) + 숫자 프리필. open_noatime 은
// root 실행일 때만 실리는데 관리자 기본이 root 다(2026-09-30 사용자 결정) -- 비 root 실행의
// O_NOATIME 은 타인 소유 파일에서 EPERM 이라 root 를 끄면 빠진다(아래 open_noatime 테스트).
const SYNC_DEFAULT_OPTS = { open_noatime: true, ...SYNC_NUM_DEFAULTS };

// 기본 me = admin(2026-08-20): rm·scan·고급옵션·우선순위·실행신원은 운영자
// 전용이라, 그 기능들을 다루는 대다수 테스트는 admin 컨텍스트여야 한다. 사용자
// 제약(sync 만·옵션 단순화)은 meUser 를 명시한 테스트가 따로 고정한다.
// 사용자 sync 허용 쌍(2026-09-30, 기본 전부 불가): 사용자 테스트의 fillSyncTarget 조합
// (cephfs → cephfs-secondary)을 허용해 둔다. 관리자는 조회하지 않는다(제한 없음).
const userPairs = { restricted: true, pairs: [
  { source_storage: "cephfs", destination_storage: "cephfs-secondary" }] };
const server = setupServer(
  http.get("/api/auth/me", () => HttpResponse.json(meAdmin)),
  http.get("/api/user/storages", () => HttpResponse.json(storageRows)),
  http.get("/api/user/sync-pairs", () => HttpResponse.json(userPairs)),
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

// ---- 입력 헬퍼 ---------------------------------------------------------------
// 한 화면이라 이동 단계가 없다. 대신 스토리지 목록 로드를 option 등장으로 기다린다 -- 위저드의
// "다음" 클릭이 벌어 주던 시간이 사라져, 기다리지 않고 selectOptions 하면 user-event 가
// "Value not found in options" 로 던진다.

const submitButton = () => screen.getByRole("button", { name: "제출" });

// sync(초기 연산)의 소스·목적지 4필드를 채운다.
async function fillSyncTarget() {
  await screen.findByLabelText("연산");
  const sourceSelect = await screen.findByLabelText("소스 스토리지");
  // 옵션 텍스트는 이름만(상태 접미 제거, 2026-08-22) -- 정확 매칭으로 기다린다
  // (cephfs 는 cephfs-secondary 의 부분 문자열이라 exact 필수).
  await within(sourceSelect).findByRole("option", { name: "cephfs" });
  await userEvent.selectOptions(sourceSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("소스 경로"), "a/b");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "cephfs-secondary");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "c/d");
}

// 연산을 바꾸고(scan·rm 은 admin 전용이라 me 로드 후에야 옵션이 생긴다) 스토리지 목록 로드를 기다린다.
async function chooseOperation(op: "scan" | "rm") {
  await screen.findByRole("option", { name: op });
  await userEvent.selectOptions(screen.getByLabelText("연산"), op);
  const storageSelect = screen.getByLabelText("스토리지");
  await within(storageSelect).findByRole("option", { name: "cephfs" });
  return storageSelect;
}

// rm 을 고르고 스토리지·경로를 채운다.
async function fillRmTarget() {
  const storageSelect = await chooseOperation("rm");
  await userEvent.selectOptions(storageSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("대상 경로"), "a/b");
}

// 입력 칸 하나의 묶음(라벨 + 그 아래 오류) -- 같은 문장이 소유권 안내 카드에도 나오는 오류를
// 그 칸으로 좁혀 단언할 때 쓴다(예: chown 이름 오류는 카드의 소유권 문장에도 들어간다).
const fieldBox = (label: string) =>
  screen.getByLabelText(label).closest("label")!.parentElement as HTMLElement;

// ---- 한 화면 구조 -------------------------------------------------------------

test("한 화면에 구획이 모두 보이고 위저드 동선(다음·이전)이 없다 — 관리자 4구획·사용자 3구획", async () => {
  const { unmount } = renderPage();
  await screen.findByRole("option", { name: "rm" });              // me(admin) 로드
  for (const name of ["작업 종류", "소스와 목적지", "실행 옵션", "실행 설정"])
    expect(screen.getByRole("heading", { level: 2, name })).toBeInTheDocument();
  expect(screen.getByRole("heading", { level: 2, name: "제출 요약" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "다음" })).toBeNull();
  expect(screen.queryByRole("button", { name: "이전" })).toBeNull();
  // 입력 필드가 한 화면에 동시에 있다(연산·대상·옵션·실행 신원)
  expect(screen.getByLabelText("연산")).toBeInTheDocument();
  expect(screen.getByLabelText("소스 경로")).toBeInTheDocument();
  expect(screen.getByLabelText("delete")).toBeInTheDocument();
  expect(screen.getByLabelText("실행 신원(선택)")).toBeInTheDocument();
  unmount();

  server.use(http.get("/api/auth/me", () => HttpResponse.json(meUser)));
  renderPage();
  await screen.findByText("스토리지 사이에서 디렉토리 하나를 sync 합니다.");   // me(user) 로드
  expect(screen.queryByRole("heading", { level: 2, name: "실행 설정" })).toBeNull();
  expect(screen.getByRole("heading", { level: 2, name: "실행 옵션" })).toBeInTheDocument();
});

test("연산을 고르면 옆에 무엇이 일어나는지 한 줄로 말하고, 대상 구획 제목이 바뀐다", async () => {
  renderPage();
  expect(await screen.findByText("소스 디렉토리의 내용을 목적지로 복사·동기화합니다.")).toBeInTheDocument();
  await chooseOperation("scan");
  expect(screen.getByText("대상 경로의 파일 수·용량·데이터 온도 통계를 수집합니다.")).toBeInTheDocument();
  expect(screen.getByRole("heading", { level: 2, name: "스캔 대상" })).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("연산"), "rm");
  expect(screen.getByText("대상 경로와 그 아래를 모두 삭제합니다.")).toBeInTheDocument();
  expect(screen.getByRole("heading", { level: 2, name: "삭제 대상" })).toBeInTheDocument();
});

test("사용자 화면은 관리자 전용 정책 API 를 부르지 않는다(403 잡음 없음) — 우선순위 요약은 정책 기본", async () => {
  let asked = 0;
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json(meUser)),
    http.get("/api/admin/policies", () => { asked += 1; return HttpResponse.json({ detail: "forbidden" }, { status: 403 }); }));
  renderPage();
  await fillSyncTarget();
  expect(screen.getByText("우선순위").closest("div")).toHaveTextContent("(정책 기본)");
  expect(asked).toBe(0);
});

test("관리자 화면은 정책을 조회해 우선순위 기본값 실값을 보인다", async () => {
  let asked = 0;
  server.use(http.get("/api/admin/policies", () => {
    asked += 1;
    return HttpResponse.json([{ tool: "dsync", max_nodes: 6, procs_per_node: 4, queue: "dms-data",
      default_priority: "high", max_priority: "high", preview_timeout_seconds: null,
      execution_timeout_seconds: 3600, enabled: 1, updated_at: "2026-08-05T00:00:00Z", updated_by: "admin" }]);
  }));
  renderPage();
  expect(await screen.findByRole("option", { name: "(정책 기본: high)" })).toBeInTheDocument();
  expect(asked).toBeGreaterThan(0);
});

test("입력 칸에서 Enter 를 눌러도 제출되지 않는다(요약을 보기 전 조기 제출 방지)", async () => {
  // 관리자 기본이 root 이고 rm 도 있다 -- 경로 입력 중 Enter 한 번에 요청이 나가면 안 된다.
  // 제출 버튼은 type="button" + onClick 이라 form 에 submit 버튼이 없다.
  let received: any = null;
  server.use(http.post("/api/user/requests", async ({ request }) => {
    received = await request.json();
    return HttpResponse.json({ request_id: "rE", state: "Pending" }, { status: 202 });
  }));
  renderPage();
  await fillSyncTarget();
  expect(submitButton()).toBeEnabled();
  expect(submitButton()).toHaveAttribute("type", "button");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "{enter}");
  await userEvent.type(screen.getByLabelText("실행 신원(선택)"), "{enter}");
  await new Promise((r) => setTimeout(r, 150));
  expect(received).toBeNull();
  expect(screen.queryByRole("heading", { name: "요청 상세" })).not.toBeInTheDocument();
});

test("입력한 상대경로의 실제 절대경로를 관리 디렉토리 기준으로 보여 준다(모르면 생략)", async () => {
  server.use(http.get("/api/user/storages", () => HttpResponse.json([
    { storage_name: "cephfs", backend_type: "cephfs", status: "Ready", managed_root: "/cephfs/managed" },
    { storage_name: "cephfs-secondary", backend_type: "cephfs", status: "Ready" }])));   // 뿌리 모름
  renderPage();
  await fillSyncTarget();
  expect(screen.getByText("실제 경로: /cephfs/managed/a/b")).toBeInTheDocument();
  // 목적지 스토리지는 managed_root 가 없다 -- 거짓 경로를 지어내지 않는다.
  expect(screen.queryByText(/실제 경로: .*c\/d/)).toBeNull();
});

test("서버가 거부할 경로(/ 시작·..·관리 디렉토리 자신)는 실제 경로 대신 이유를 보이고 제출을 잠근다", async () => {
  // domain.validate_relative_path(422 unsafe_path)의 즉답 미러. 예전엔 다듬은 값으로 "실제 경로"를 지어
  // 보여 "/team/data" 에도 그럴듯한 절대경로가 떴다(리뷰 2026-10-06).
  let received: any = null;
  server.use(
    http.get("/api/user/storages", () => HttpResponse.json([
      { storage_name: "cephfs", backend_type: "cephfs", status: "Ready", managed_root: "/cephfs/managed" },
      { storage_name: "cephfs-secondary", backend_type: "cephfs", status: "Ready" }])),
    http.post("/api/user/requests", async ({ request }) => {
      received = await request.json();
      return HttpResponse.json({ request_id: "rP", state: "Pending" }, { status: 202 });
    }));
  const { container } = renderPage();
  await fillSyncTarget();
  expect(submitButton()).toBeEnabled();
  await userEvent.clear(screen.getByLabelText("소스 경로"));
  await userEvent.type(screen.getByLabelText("소스 경로"), "/a/b");
  expect(screen.getByText(/"\/" 로 시작할 수 없습니다/)).toBeInTheDocument();
  expect(screen.queryByText(/^실제 경로: /)).toBeNull();
  expect(submitButton()).toBeDisabled();
  expect(screen.getByText("경로를 관리 디렉토리 아래 상대경로로 고치세요")).toBeInTheDocument();
  fireEvent.submit(container.querySelector("form")!);
  await new Promise((r) => setTimeout(r, 150));
  expect(received).toBeNull();
  await userEvent.clear(screen.getByLabelText("소스 경로"));
  await userEvent.type(screen.getByLabelText("소스 경로"), "a/../b");
  expect(screen.getByText(/"\.\." 은 쓸 수 없습니다/)).toBeInTheDocument();
  expect(submitButton()).toBeDisabled();
  await userEvent.clear(screen.getByLabelText("소스 경로"));
  await userEvent.type(screen.getByLabelText("소스 경로"), "a/b");
  expect(screen.getByText("실제 경로: /cephfs/managed/a/b")).toBeInTheDocument();
  expect(submitButton()).toBeEnabled();
});

test("rm 대상이 관리 디렉토리 자신(.)이면 제출이 잠긴다", async () => {
  renderPage();
  const storageSelect = await chooseOperation("rm");
  await userEvent.selectOptions(storageSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("대상 경로"), "./");
  expect(screen.getByText(/관리 디렉토리 자신\(\.\)은 지정할 수 없습니다/)).toBeInTheDocument();
  expect(submitButton()).toBeDisabled();
});

test("앞뒤 공백은 다듬지 않고 그대로 보낸다 — 화면도 그 사실을 말한다", async () => {
  // 서버는 경로를 다듬지 않는다(공백도 이름) -- 화면이 다듬은 값으로 경로를 지어 보이면 거짓이 된다.
  server.use(http.get("/api/user/storages", () => HttpResponse.json([
    { storage_name: "cephfs", backend_type: "cephfs", status: "Ready", managed_root: "/cephfs/managed" },
    { storage_name: "cephfs-secondary", backend_type: "cephfs", status: "Ready" }])));
  const captured = captureSubmit();
  renderPage();
  await fillSyncTarget();
  await userEvent.type(screen.getByLabelText("소스 경로"), " ");
  expect(screen.getByText("앞뒤 공백도 경로 이름에 그대로 들어갑니다.")).toBeInTheDocument();
  expect(screen.getByText((_, el) => el?.tagName === "P"
    && el.textContent === "실제 경로: /cephfs/managed/a/b ")).toBeInTheDocument();
  await userEvent.click(submitButton());
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(captured.body.source).toBe("a/b ");
});

test("제출 요약은 빈 폼에선 '—' 와 남은 입력을, 다 채우면 값을 보이고 버튼이 풀린다", async () => {
  renderPage();
  await screen.findByLabelText("소스 스토리지");
  const summary = screen.getByRole("heading", { level: 2, name: "제출 요약" }).parentElement!;
  expect(within(summary).getAllByText("—")).toHaveLength(2);                // 소스·목적지
  expect(within(summary).getByText("소스·목적지 스토리지와 경로를 모두 입력하세요")).toBeInTheDocument();
  expect(submitButton()).toBeDisabled();
  expect(submitButton()).toHaveAccessibleDescription("소스·목적지 스토리지와 경로를 모두 입력하세요");
  await fillSyncTarget();
  expect(within(summary).getByText("cephfs:a/b")).toBeInTheDocument();
  expect(within(summary).getByText("cephfs-secondary:c/d")).toBeInTheDocument();
  expect(within(summary).queryByText("소스·목적지 스토리지와 경로를 모두 입력하세요")).toBeNull();
  // 옵션은 JSON 한 줄이 아니라 값 토큰(제출 바디와 같은 함수에서)
  for (const t of ["open_noatime", "batch_files=1000000", "bufsize=4194304"])
    expect(within(summary).getByText(t)).toBeInTheDocument();
  expect(submitButton()).toBeEnabled();
});

// ---- 대상 sanity -------------------------------------------------------------

test("대상 sanity: sync 경로가 비면 제출이 잠기고 문구가 뜬다(운영자/사용자 공통)", async () => {
  // 사용자 결정(2026-08-22): 스토리지·경로 미입력이면 제출할 수 없다.
  renderPage();
  await screen.findByLabelText("소스 스토리지");
  // 아무것도 안 채운 상태: 제출 비활성 + 안내
  expect(submitButton()).toBeDisabled();
  expect(screen.getByText("소스·목적지 스토리지와 경로를 모두 입력하세요")).toBeInTheDocument();
  // 스토리지만 고르고 경로는 비움 → 여전히 잠김
  const srcSel = screen.getByLabelText("소스 스토리지");
  await within(srcSel).findByRole("option", { name: "cephfs" });   // 목록 로드 대기(스코프)
  await userEvent.selectOptions(srcSel, "cephfs");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "cephfs-secondary");
  expect(submitButton()).toBeDisabled();
  // 경로까지 채우면 풀린다(관리자 기본 옵션은 유효하다)
  await userEvent.type(screen.getByLabelText("소스 경로"), "a");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "b");
  expect(submitButton()).toBeEnabled();
});

test("대상 sanity: rm 은 대상 경로가 비면 제출이 잠긴다", async () => {
  renderPage();
  const storageSelect = await chooseOperation("rm");
  await userEvent.selectOptions(storageSelect, "cephfs");
  expect(submitButton()).toBeDisabled();
  expect(screen.getByText("스토리지와 대상 경로를 입력하세요")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("대상 경로"), "x");
  expect(submitButton()).toBeEnabled();
});

test("스토리지 드롭다운이 API 목록으로 채워진다", async () => {
  renderPage();
  const sourceSelect = await screen.findByLabelText("소스 스토리지");
  // 이름만(상태 접미 없음). 정확 매칭으로 두 옵션을 구분.
  expect(await within(sourceSelect).findByRole("option", { name: "cephfs" })).toBeInTheDocument();
  expect(within(sourceSelect).getByRole("option", { name: "cephfs-secondary" })).toBeInTheDocument();
  // 상태 접미(Ready/Degraded)는 더 이상 노출되지 않는다.
  expect(within(sourceSelect).queryByText(/\(Ready\)|\(Degraded\)/)).not.toBeInTheDocument();
});

test("sync 는 목적지 조건(없는 경우·있는 경우)과 소유권을 보이고, 제출 요약이 요점을 함께 보인다(사용자)", async () => {
  // 2026-09-30 사용자 요청: "목적지의 상위 디렉토리에 쓰기 권한이 있어야 하는 조건을 분명히".
  // 2026-10-01 사용자 요청: "목적지가 없는 경우" + "기본적으로 목적지는 요청자 본인 uid:gid 로 셋업".
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json(meUser)),
    http.get("/api/user/storages", () => HttpResponse.json([
      { storage_name: "cephfs", backend_type: "cephfs", status: "Ready", managed_root: "/cephfs/managed" },
      { storage_name: "cephfs-secondary", backend_type: "cephfs", status: "Ready",
        managed_root: "/cephfs2/managed" }])));
  renderPage();
  await fillSyncTarget();                     // 목적지 cephfs-secondary : c/d
  const card = screen.getByLabelText("목적지 조건과 소유권");
  expect(card).toHaveAttribute("role", "note");
  expect(card).toHaveTextContent("목적지가 없는 경우: sync 가 목적지 디렉토리를 새로 만듭니다");
  expect(card).toHaveTextContent("상위 디렉토리는 이미 있고 그 디렉토리에 쓰기 권한");
  expect(card).toHaveTextContent("중간 디렉토리는 만들지 않습니다");
  expect(card).toHaveTextContent("목적지가 이미 있는 경우: 디렉토리여야 합니다(파일이면 거부). 쓰기·진입할 수 있어야 하며");
  expect(card).toHaveTextContent("목적지 조건과 소유권 — 요청자 본인 계정(uid/gid) 기준");
  expect(card).toHaveTextContent("요청자 본인 소유여야 합니다");
  expect(card).toHaveTextContent("권한은 본인 계정의 uid·LDAP 주 그룹");
  expect(card).toHaveTextContent("보조 그룹으로 받은 권한은 인정되지 않습니다");
  expect(card).toHaveTextContent("권한 비트·수정 시각은 소스 그대로라 소스의 그룹 권한이 이 주 그룹에 적용");
  // 검증 2차(2026-10-01): 이미 있던 남의 소유 항목은 못 바꾼다 -- dsync 실패 / nsync 건너뜀(도구별로 다르다)
  expect(card).toHaveTextContent("그 안에 다른 사용자 소유 항목이 있으면 작업이 실패하거나 일부 항목의 소유·권한이 바뀌지 않을 수 있습니다");
  expect(card).not.toHaveTextContent("실행 신원");                             // 사용자에겐 없는 개념
  expect(card).toHaveTextContent("상위 디렉토리 쓰기 권한도 마찬가지로 필요");
  expect(within(card).getByText("/cephfs2/managed/c")).toBeInTheDocument();   // c/d 의 상위
  // 소유권: 사용자는 언제나 요청자 본인 uid:gid(_auto_chown) -- 소스 소유자와 무관
  expect(card).toHaveTextContent(
    "소유권: 기본적으로 목적지(새로 만드는 경우 포함)와 복사된 파일·디렉토리는 요청자 본인(alice)의 uid:gid");
  expect(card).toHaveTextContent("소스 소유자와 관계없습니다");
  expect(card).not.toHaveTextContent("root");                                  // 사용자에겐 root 안내 없음
  expect(screen.getByText("상위 디렉토리 /cephfs2/managed/c 가 있어야 하고 요청자 본인의 쓰기 권한 필요. "
    + "목적지가 없으면 새로 만들고, 이미 있으면 요청자 본인 소유·쓰기 가능해야 함")).toBeInTheDocument();
  expect(screen.getByText("요청자 본인(alice)의 uid:gid(주 그룹)")).toBeInTheDocument();
});

test("관리자 전용 스토리지는 관리자 피커에 (관리자 전용) 으로 구분된다", async () => {
  // 2026-09-30 사용 범위: 비관리자 응답엔 아예 없고(서버), 관리자에겐 admin_only 표식이 온다.
  server.use(http.get("/api/user/storages", () => HttpResponse.json([
    ...storageRows, { storage_name: "adm-only", backend_type: "cephfs", status: "Ready",
                      admin_only: true }])));
  renderPage();
  const sourceSelect = await screen.findByLabelText("소스 스토리지");
  expect(await within(sourceSelect).findByRole("option", { name: "adm-only (관리자 전용)" }))
    .toBeInTheDocument();
  expect(within(sourceSelect).getByRole("option", { name: "cephfs" })).toBeInTheDocument();
});

test("연산을 rm으로 바꾸면 대상 필드 구성이 바뀌고 rm 경고가 한 번 뜬다", async () => {
  renderPage();
  expect(await screen.findByLabelText("목적지 스토리지")).toBeInTheDocument();
  expect(screen.getByLabelText("목적지 경로")).toBeInTheDocument();
  expect(screen.queryByLabelText("대상 경로")).not.toBeInTheDocument();

  // 값 상태는 단일 useState 라 전환 정책(현행 유지 -- 초기화하지 않음)이 그대로 적용된다.
  await screen.findByRole("option", { name: "rm" });   // rm 은 admin 로드 후 등장
  await userEvent.selectOptions(screen.getByLabelText("연산"), "rm");
  // 경고는 한 곳(제출 버튼 위) -- getByText 는 둘 이상이면 던진다
  expect(screen.getByText(
    "삭제는 되돌릴 수 없습니다. 미리보기에서 대상을 확인한 뒤 확인해야 실행됩니다.",
  )).toBeInTheDocument();
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
  await userEvent.click(submitButton());

  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(received).toEqual({
    operation: "sync",
    source_storage: "cephfs", source: "a/b",
    destination_storage: "cephfs-secondary", destination: "c/d",
    // 계약: batch_files·bufsize 프리필이 항상 실린다(지우면 빠진다 -- 아래 테스트).
    // open_noatime 은 root 실행에서만 실린다(관리자 기본 root, 2026-09-30).
    options: SYNC_DEFAULT_OPTS,
    // priority 생략 = (정책 기본) — 슬라이스 37, resolve_priority 가 정책값 해석
    // run_as_root: 자격 있는 관리자는 확정값을 명시로 싣는다(기본 root).
    run_as_root: true,
  });
});

test("사용자 sync 제출 바디 전체 모양 — run_as_root·priority·owner_username 키가 없다", async () => {
  // 서버는 생략 = 비 root 다. 사용자 바디에 run_as_root 를 실으면(false 라도) 계약이 흔들린다.
  server.use(http.get("/api/auth/me", () => HttpResponse.json(meUser)));
  const captured = captureSubmit();
  renderPage();
  await fillSyncTarget();
  await userEvent.click(submitButton());
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(captured.body).toEqual({
    operation: "sync",
    source_storage: "cephfs", source: "a/b",
    destination_storage: "cephfs-secondary", destination: "c/d",
    options: SYNC_NUM_DEFAULTS,
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
  await userEvent.click(submitButton());

  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(received.options.recursive).toBe(true);
  expect(received).toEqual({
    operation: "rm",
    storage: "cephfs", target: "a/b",
    options: { recursive: true },
    // priority 생략 = (정책 기본) — 슬라이스 37, resolve_priority 가 정책값 해석
    run_as_root: true,   // 관리자 기본 root(2026-09-30) — 요약·체크박스가 경고한다
  });
});

// 강제 submit 이벤트: 제출 버튼은 type=button 이라 정상 동선으론 form submit 이 안 나지만,
// 가드(if (blocked) return)는 Enter 유출·미래 회귀에 대한 이중 방어다 -- 이벤트를 직접
// 쏴서 가드가 살아 있음을 관측한다(뮤테이션 표적).
async function forceSubmitAndSettle(form: HTMLFormElement) {
  fireEvent.submit(form);
  // mutate → msw 왕복이 비동기라, 잘못 전송됐다면 캡처가 도착할 시간을 준다.
  await new Promise((r) => setTimeout(r, 150));
}

test("recursive를 해제하면 제출이 비활성이고 강제 submit도 차단된다", async () => {
  let received: any = null;
  server.use(http.post("/api/user/requests", async ({ request }) => {
    received = await request.json();
    return HttpResponse.json({ request_id: "rG", state: "Pending" }, { status: 202 });
  }));
  const { container } = renderPage();
  await fillRmTarget();
  expect(submitButton()).toBeEnabled();

  await userEvent.click(screen.getByLabelText("재귀 삭제(필수)"));

  expect(submitButton()).toBeDisabled();
  expect(screen.getByText("재귀 옵션이 필요합니다")).toBeInTheDocument();

  await forceSubmitAndSettle(container.querySelector("form")!);
  expect(received).toBeNull();
  expect(screen.queryByRole("heading", { name: "요청 상세" })).not.toBeInTheDocument();
});

test("stat과 lite를 동시에 체크하면 제출이 비활성이고 강제 submit도 차단된다", async () => {
  let received: any = null;
  server.use(http.post("/api/user/requests", async ({ request }) => {
    received = await request.json();
    return HttpResponse.json({ request_id: "rG", state: "Pending" }, { status: 202 });
  }));
  const { container } = renderPage();
  await fillRmTarget();
  await userEvent.click(screen.getByLabelText("stat"));
  await userEvent.click(screen.getByLabelText("lite"));

  expect(submitButton()).toBeDisabled();
  expect(screen.getByText("stat과 lite는 함께 쓸 수 없습니다")).toBeInTheDocument();
  // 요약의 잠김 이유는 필드 옆 문구와 다른 문장(같은 문장을 두 번 그리지 않는다)
  expect(screen.getByText("빨간 안내가 붙은 옵션 값을 고치세요")).toBeInTheDocument();

  await forceSubmitAndSettle(container.querySelector("form")!);
  expect(received).toBeNull();
  expect(screen.queryByRole("heading", { name: "요청 상세" })).not.toBeInTheDocument();
});

// 라벨 정정(사용자 결정 2026-08-16): 이 값(owner_username)은 아티팩트 소유자가
// 아니라 **잡의 실행 신원**을 정한다(identity.resolve_job_identity — owner =
// owner_username or requester_id). 필드는 그대로, 라벨·캡션만 사실에 맞춘다.
test("실행 신원 필드는 실행 설정 구획에서 관리자에게만 보인다", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json(meUser)));
  const { unmount } = renderPage();
  await fillSyncTarget();
  await screen.findByLabelText("delete");   // 사용자 옵션의 앵커(우선순위는 숨김)
  expect(screen.queryByLabelText("실행 신원(선택)")).not.toBeInTheDocument();
  unmount();

  server.use(http.get("/api/auth/me", () => HttpResponse.json(meAdmin)));
  renderPage();
  await fillSyncTarget();
  expect(await screen.findByLabelText("실행 신원(선택)")).toBeInTheDocument();
  // 캡션은 "소유자 기록"이 아니라 실행 신원을 말한다 — 비우면 root(자격 있는 관리자 기본),
  // 지정하면 기본은 그 사용자의 uid/gid(2026-09-30 정정: 예전 "root 로 실행되고 지정한 사용자
  // 신원으로 파일을 다룹니다" 는 사실이 아니었다 -- 실제론 무조건 root 였다).
  expect(screen.getByText((_, el) => el?.tagName === "P"
    && (el.textContent ?? "").startsWith("비우면 root 로 실행됩니다(관리자 기본")
    && (el.textContent ?? "").includes("기본은 그 사용자의 uid/gid 로 실행되어"))).toBeInTheDocument();
});

// 선택 필드 표기 통일(사용자 지시 2026-08-16): 비워도 되는 입력은 라벨에 (선택).
test("비워도 되는 sync 옵션 입력은 라벨에 (선택) 이 붙는다", async () => {
  renderPage();
  await fillAndOpenAdvanced();
  expect(screen.getByText("batch_files (선택 · 0..10,000,000)")).toBeInTheDocument();
  expect(screen.getByText("bufsize (선택 · 바이트, 4096..1,073,741,824)")).toBeInTheDocument();
  expect(screen.getByText(
    "chmod (선택 · 예: D770,F660 — 콤마 구분, D=디렉터리 F=파일)")).toBeInTheDocument();
  expect(screen.getByText("chown (선택 · 숫자 uid:gid)")).toBeInTheDocument();
});

test("스토리지 목록 로드가 실패하면 대상 구획에 오류가 뜨고 제출이 비활성이다", async () => {
  server.use(http.get("/api/user/storages", () =>
    HttpResponse.json({ detail: "storage_list_failed" }, { status: 500 })));
  renderPage();
  await screen.findByLabelText("소스 스토리지");
  expect(await screen.findByText("storage_list_failed")).toBeInTheDocument();
  // 스토리지를 못 고르니 대상 sanity(스토리지·경로 필수)가 제출을 잠근다 -- 목록 실패가 곧 진행 불가
  expect(submitButton()).toBeDisabled();
  expect(screen.getByText("스토리지 목록 조회 실패 — 새로고침 후 다시 시도하세요")).toBeInTheDocument();
});

test("제출 요약에 rm 경고와 대상이 노출된다", async () => {
  renderPage();
  await fillRmTarget();
  expect(screen.getByText(
    "삭제는 되돌릴 수 없습니다. 미리보기에서 대상을 확인한 뒤 확인해야 실행됩니다.",
  )).toBeInTheDocument();
  // 요약이 실제 입력값의 함수임을 확인(빈 껍데기 요약 방지).
  expect(screen.getByText("cephfs:a/b")).toBeInTheDocument();
  expect(submitButton()).toBeInTheDocument();
});

// ---- 고급 sync 옵션(슬라이스 26 Task 5) ---------------------------------------
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

// sync 대상을 채우고 <details> 고급 옵션을 펼친다(기본 접힘 확인 겸).
async function fillAndOpenAdvanced() {
  await fillSyncTarget();
  const details = screen.getByText("고급 옵션").closest("details")!;
  expect(details.open).toBe(false);
  await userEvent.click(screen.getByText("고급 옵션"));
}

// 프리필 계약(사용자 조정 2026-08-16): batch_files·bufsize 는 placeholder 가 아니라
// **실제 값**으로 미리 채워져 있고, 그래서 손대지 않아도 바디에 실린다.
// batch_files 1,000,000 은 도구 기본(0 = 배칭 안 함)과 **다른 동작**이라 이건
// 의도된 정책이고, bufsize 4194304 는 도구 기본(4 MiB)과 같은 값의 명시다.
test("고급 숫자 옵션은 실제 값으로 프리필돼 있다(placeholder 아님)", async () => {
  renderPage();
  await fillAndOpenAdvanced();
  expect(screen.getByLabelText("batch_files")).toHaveValue(
    SYNC_INT_FIELDS.batch_files.prefill);
  expect(screen.getByLabelText("bufsize")).toHaveValue(
    SYNC_INT_FIELDS.bufsize.prefill);
  // 비웠을 때 무슨 일이 나는지는 placeholder·캡션이 말한다(빈값 = 서버 기본, 2026-09-17).
  expect(screen.getByLabelText("batch_files"))
    .toHaveAttribute("placeholder", "비우면 기본 1,000,000 적용 · 0 = 배칭 끔");
  expect(screen.getByLabelText("bufsize"))
    .toHaveAttribute("placeholder", "비우면 기본 4 MiB 적용");
  expect(screen.getByText(
    "미리 채운 1,000,000 = 서버 기본 배치 사이즈. 비워도 같은 값이 적용되며, 배칭을 끄려면 0 을 입력하세요.",
  )).toBeInTheDocument();
  expect(screen.getByText(
    "미리 채운 4194304 = 4 MiB(서버 기본). 비워도 같은 값이 적용됩니다.",
  )).toBeInTheDocument();
});

test("고급 숫자 옵션을 지우면 그 키가 바디에서 빠진다(도구 기본으로 복귀)", async () => {
  const captured = captureSubmit();
  renderPage();
  await fillAndOpenAdvanced();
  await userEvent.clear(screen.getByLabelText("batch_files"));
  await userEvent.clear(screen.getByLabelText("bufsize"));
  await userEvent.click(submitButton());
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  // 빈 문자열은 "미입력"이라 batch_files·bufsize 는 통째로 생략된다. open_noatime
  // 은 관리자 기본 root 라 남는다 -- 숫자 생략과 독립.
  expect(captured.body.options).toEqual({ open_noatime: true });
});

test("open_noatime 은 root 실행 전용 — root 를 끄면 잠기고 싣지 않는다", async () => {
  // 비 root 의 O_NOATIME 은 타인 소유 파일에서 EPERM(mpifileutils 폴백 없음 → 부분 복사 뒤
  // Failed)이라 잠근다. 관리자 기본은 root 라 여기선 명시로 끈다.
  const captured = captureSubmit();
  renderPage();
  await fillAndOpenAdvanced();
  await userEvent.click(screen.getByLabelText("root 권한으로 실행"));   // 끔
  expect(screen.getByLabelText("open_noatime")).toBeDisabled();
  expect(screen.getByLabelText("open_noatime")).not.toBeChecked();
  expect(screen.getByText(/open_noatime 은 root 실행에서만 적용됩니다/)).toBeInTheDocument();
  // chown 캡션도 지금 모드를 말한다(root 아님 → 실행 신원 소유로 자동 chown).
  expect(screen.getByText(/비우면 실행 신원의 uid:gid 소유로 자동 chown 됩니다\(지금 root 아님\)/))
    .toBeInTheDocument();
  await userEvent.click(submitButton());
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(captured.body.options).toEqual(SYNC_NUM_DEFAULTS);
  expect(captured.body.run_as_root).toBe(false);
});

test("root 실행(관리자 기본)이면 open_noatime 이 기본 ON 으로 실리고, 해제하면 빠진다", async () => {
  // 사용자 결정(2026-08-22)의 root 실행 기본 ON 은 유지 — 끄면 checkedOptions 가
  // false 를 생략한다.
  const rootOn = captureSubmit();
  const first = renderPage();
  await fillAndOpenAdvanced();
  expect(screen.getByLabelText("root 권한으로 실행")).toBeChecked();
  expect(screen.getByLabelText("open_noatime")).toBeEnabled();
  expect(screen.getByLabelText("open_noatime")).toBeChecked();
  expect(screen.getByText(/비우면 원래\(소스\) 소유권을 보존합니다\(지금 root 실행\)/)).toBeInTheDocument();
  await userEvent.click(submitButton());
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(rootOn.body.options).toEqual({ open_noatime: true, ...SYNC_NUM_DEFAULTS });
  expect(rootOn.body.run_as_root).toBe(true);
  first.unmount();

  const captured = captureSubmit();
  renderPage();
  await fillAndOpenAdvanced();
  await userEvent.click(screen.getByLabelText("open_noatime"));  // 해제
  await userEvent.click(submitButton());
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(captured.body.options).toEqual(SYNC_NUM_DEFAULTS);   // open_noatime 빠짐
});

test("chmod·chown 문자열이 그대로 전송된다", async () => {
  const captured = captureSubmit();
  renderPage();
  await fillAndOpenAdvanced();
  await userEvent.type(screen.getByLabelText("chmod"), "D770,F660");
  await userEvent.type(screen.getByLabelText("chown"), "10003:10000");
  await userEvent.click(submitButton());
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(captured.body.options).toEqual(
    { ...SYNC_DEFAULT_OPTS, chmod: "D770,F660", chown: "10003:10000" });
});

test("숫자 uid:gid chown 이 즉답 오류 없이 그대로 전송된다", async () => {
  // 서버 _CHOWN_RE 숫자 확장(domain.py)의 미러 검증 — optionRules CHOWN_RE 발산 금지.
  const captured = captureSubmit();
  renderPage();
  await fillAndOpenAdvanced();
  expect(screen.getByLabelText("chown")).toHaveAttribute("placeholder", "예: 10003:10000");
  await userEvent.type(screen.getByLabelText("chown"), "10003:10000");
  expect(screen.queryByText(/chown 형식이 올바르지 않습니다/)).toBeNull();
  await userEvent.click(submitButton());
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(captured.body.options).toEqual({ ...SYNC_DEFAULT_OPTS, chown: "10003:10000" });
});

test("batch_files·bufsize 숫자 입력은 number 로 전송된다", async () => {
  const captured = captureSubmit();
  renderPage();
  await fillAndOpenAdvanced();
  // 프리필 값을 지우고 사용자가 직접 넣는다 — 프리필은 기본이지 잠금이 아니다.
  await userEvent.clear(screen.getByLabelText("batch_files"));
  await userEvent.type(screen.getByLabelText("batch_files"), "1000");
  await userEvent.clear(screen.getByLabelText("bufsize"));
  await userEvent.type(screen.getByLabelText("bufsize"), "4096");
  await userEvent.click(submitButton());
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(captured.body.options).toEqual({ open_noatime: true, batch_files: 1000, bufsize: 4096 });
});

test("batch_files 상한은 1,000만 — 그 값은 통과, 넘으면 즉답 문구 + 제출 비활성", async () => {
  // 서버 _OPTION_SPECS[SYNC] 상한(사용자 조정 2026-08-16: 100만 → 1,000만)의 미러.
  renderPage();
  await fillAndOpenAdvanced();
  await userEvent.clear(screen.getByLabelText("batch_files"));
  await userEvent.type(screen.getByLabelText("batch_files"), "10000000");
  expect(screen.queryByText(/batch_files는/)).toBeNull();
  expect(submitButton()).toBeEnabled();
  await userEvent.type(screen.getByLabelText("batch_files"), "0");   // → 1,000만 초과
  expect(screen.getByText("batch_files는 0..10000000 범위의 정수여야 합니다"))
    .toBeInTheDocument();
  expect(submitButton()).toBeDisabled();
});

test("잘못된 chmod는 제출을 비활성으로 막고 필드별 문구를 띄운다", async () => {
  renderPage();
  await fillAndOpenAdvanced();
  await userEvent.type(screen.getByLabelText("chmod"), "999x");
  expect(submitButton()).toBeDisabled();
  expect(screen.getByText("chmod 형식이 올바르지 않습니다 (예: D770,F660)")).toBeInTheDocument();
});

test("범위 밖 bufsize는 제출을 비활성으로 막는다", async () => {
  renderPage();
  await fillAndOpenAdvanced();
  await userEvent.clear(screen.getByLabelText("bufsize"));
  await userEvent.type(screen.getByLabelText("bufsize"), "100");
  expect(submitButton()).toBeDisabled();
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
  await fillSyncTarget();
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
  await userEvent.click(submitButton());
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  // 숫자 프리필(batch_files·bufsize)은 실리되 open_noatime 은 빠진다.
  expect(captured.body.options).toEqual(SYNC_NUM_DEFAULTS);
  expect(captured.body.options.open_noatime).toBeUndefined();
});

test("admin scan 제출 바디가 정확하다(프리필 batch_files + 입력 broken_limit)", async () => {
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
  const storageSelect = await chooseOperation("scan");
  await userEvent.selectOptions(storageSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("대상 경로"), "team/data");
  await userEvent.clear(screen.getByLabelText("broken_limit"));
  await userEvent.type(screen.getByLabelText("broken_limit"), "500");
  await userEvent.click(submitButton());
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(posted).toEqual({
    operation: "scan", storage: "cephfs", target: "team/data",
    options: { batch_files: 1000000, broken_limit: 500 },   // batch_files 는 프리필(서버 기본과 같은 값)
    run_as_root: true,   // 관리자 기본 root(2026-09-30)
  });
});

test("scan 의 verbose·quiet 동시는 제출을 잠근다", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json(meAdmin)));
  renderPage();
  const storageSelect = await chooseOperation("scan");
  await userEvent.selectOptions(storageSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("대상 경로"), "a");
  expect(submitButton()).toBeEnabled();
  await userEvent.click(screen.getByLabelText("verbose"));
  await userEvent.click(screen.getByLabelText("quiet"));
  expect(submitButton()).toBeDisabled();
  expect(screen.getByText("verbose와 quiet는 함께 쓸 수 없습니다")).toBeInTheDocument();
});


// scan 프리필 계약(사용자 결정 2026-09-17): batch_files 1,000,000·broken_limit 100 이
// 미리 채워져 있고 손대지 않으면 그대로 전송된다 — 같은 값이 서버 기본
// (domain._OPTION_DEFAULTS)이라 비워도 결과는 같지만, 요청 상세에 명시적으로 남는다.
test("scan 옵션은 batch_files 1,000,000·broken_limit 100 이 프리필돼 그대로 전송된다", async () => {
  let posted: any = null;
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json(meAdmin)),
    http.post("/api/user/requests", async ({ request }) => {
      posted = await request.json();
      return HttpResponse.json({ request_id: "r-scan-prefill", state: "Pending" }, { status: 202 });
    }),
  );
  renderPage();
  const storageSelect = await chooseOperation("scan");
  await userEvent.selectOptions(storageSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("대상 경로"), "team/data");
  expect(screen.getByLabelText("batch_files")).toHaveValue(SCAN_INT_FIELDS.batch_files.prefill);
  expect(screen.getByLabelText("broken_limit")).toHaveValue(SCAN_INT_FIELDS.broken_limit.prefill);
  await userEvent.click(submitButton());
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(posted.options).toEqual({ batch_files: 1000000, broken_limit: 100 });
});


// ---- root 실행 체크박스(2026-09-30) ------------------------------------------------
// 프로덕션 사고: 관리자 계정이 "실행 신원 = 일반 사용자" 로 낸 sync 가 무조건 root 로 돌아, 그
// 사용자가 쓸 수 없는 남의 700 목적지를 소스 소유로 덮어쓰며 성공했다. 사용자 결정(같은 날):
// 관리자는 **기본 root**. 단 실행 신원에 다른 사용자를 적으면 기본이 그 사용자 권한(사고 경로를
// 기본값으로 되살리지 않는다). 자격 있으면 확정값을 run_as_root 로 명시해 싣는다.

test("root 체크박스는 관리자에게 기본 켜짐 — run_as_root: true, 요약이 경고한다", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json(meUser)));
  const { unmount } = renderPage();
  await fillSyncTarget();
  await screen.findByLabelText("delete");
  expect(screen.queryByLabelText("root 권한으로 실행")).not.toBeInTheDocument();
  unmount();

  server.use(http.get("/api/auth/me", () => HttpResponse.json(meAdmin)));
  const captured = captureSubmit();
  renderPage();
  await fillSyncTarget();
  const box = await screen.findByLabelText("root 권한으로 실행") as HTMLInputElement;
  expect(box.checked).toBe(true);
  expect(screen.getByText(/^root\(특권\) — 권한 검사 우회, sync 는 목적지 소유·권한을 소스에 맞춤\(chown·chmod 지정 시 그 값\)$/))
    .toBeInTheDocument();
  await userEvent.click(submitButton());
  await screen.findByRole("heading", { name: "요청 상세" }).catch(() => null);
  expect(captured.body.run_as_root).toBe(true);
});

test("실행 신원에 다른 사용자를 적으면 root 가 기본으로 꺼진다(사고 경로) — 다시 켤 수 있다", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json(meAdmin)));
  const captured = captureSubmit();
  const first = renderPage();
  await fillSyncTarget();
  await userEvent.type(screen.getByLabelText("실행 신원(선택)"), "alice");
  expect(screen.getByLabelText("root 권한으로 실행")).not.toBeChecked();
  expect(screen.getByText("실행 신원의 uid/gid(권한 그대로 적용)")).toBeInTheDocument();
  await userEvent.click(submitButton());
  await screen.findByRole("heading", { name: "요청 상세" }).catch(() => null);
  expect(captured.body.owner_username).toBe("alice");
  expect(captured.body.run_as_root).toBe(false);
  expect(captured.body.options.open_noatime).toBeUndefined();
  first.unmount();

  // 본인 이름을 적는 건 생략과 같다(root 기본 유지).
  const second = renderPage();
  await fillSyncTarget();
  await userEvent.type(screen.getByLabelText("실행 신원(선택)"), "root");
  expect(screen.getByLabelText("root 권한으로 실행")).toBeChecked();
  second.unmount();

  // 명시로 다시 켜면 다른 실행 신원이어도 root(그 이름은 기록용).
  const again = captureSubmit();
  renderPage();
  await fillSyncTarget();
  await userEvent.type(screen.getByLabelText("실행 신원(선택)"), "alice");
  await userEvent.click(screen.getByLabelText("root 권한으로 실행"));
  await userEvent.click(submitButton());
  await screen.findByRole("heading", { name: "요청 상세" }).catch(() => null);
  expect(again.body.run_as_root).toBe(true);
});

test("root 자격 없는 관리자에겐 체크박스 대신 안내 — run_as_root: false 를 명시한다", async () => {
  // 특권 목록 밖 관리자에게 기본 root 를 켜 두면 제출이 403 privileged_not_authorized 가 된다.
  server.use(http.get("/api/auth/me", () => HttpResponse.json(meAdminNoRoot)));
  const captured = captureSubmit();
  renderPage();
  await fillSyncTarget();
  expect(await screen.findByText(/이 계정은 root 실행 자격이 없습니다/)).toBeInTheDocument();
  expect(screen.queryByLabelText("root 권한으로 실행")).not.toBeInTheDocument();
  expect(screen.getByText("실행 신원의 uid/gid(권한 그대로 적용)")).toBeInTheDocument();
  await userEvent.click(submitButton());
  await screen.findByRole("heading", { name: "요청 상세" }).catch(() => null);
  // 관리자는 확정값을 항상 명시 -- 서버의 생략 기본값(비 root)이나 me 판정 시점에 기대지 않는다.
  expect(captured.body.run_as_root).toBe(false);
  expect(captured.body.options.open_noatime).toBeUndefined();
});

// ---- 사용자 sync 허용 쌍(2026-09-30): 소스·목적지 상호 필터 ------------------------------

const threeStorages: UserStorage[] = [
  { storage_name: "ceph-a", backend_type: "cephfs", status: "Ready" },
  { storage_name: "ceph-b", backend_type: "cephfs", status: "Ready" },
  { storage_name: "ceph-c", backend_type: "cephfs", status: "Ready" },
];
const optionNames = (label: string) =>
  Array.from((screen.getByLabelText(label) as HTMLSelectElement).options).map((o) => o.value);

async function openUserTarget(pairs: { source_storage: string; destination_storage: string }[]) {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json(meUser)),
    http.get("/api/user/storages", () => HttpResponse.json(threeStorages)),
    http.get("/api/user/sync-pairs", () => HttpResponse.json({ restricted: true, pairs })),
  );
  renderPage();
  await screen.findByRole("note", { name: "허용된 스토리지 조합" });
}

test("사용자: 한쪽을 고르면 다른 쪽 선택지에서 허용되지 않은 스토리지가 빠진다(양방향)", async () => {
  await openUserTarget([
    { source_storage: "ceph-a", destination_storage: "ceph-b" },
    { source_storage: "ceph-a", destination_storage: "ceph-c" },
    { source_storage: "ceph-c", destination_storage: "ceph-c" },
  ]);
  // 아무것도 안 골랐을 때: 쌍에 나오는 스토리지만(ceph-b 는 소스로 허용된 적이 없다)
  expect(optionNames("소스 스토리지")).toEqual(["", "ceph-a", "ceph-c"]);
  expect(optionNames("목적지 스토리지")).toEqual(["", "ceph-b", "ceph-c"]);
  expect(screen.getByRole("note", { name: "허용된 스토리지 조합" })).toHaveTextContent("(3개)");
  // 소스 ceph-c → 목적지는 ceph-c 만
  await userEvent.selectOptions(screen.getByLabelText("소스 스토리지"), "ceph-c");
  expect(optionNames("목적지 스토리지")).toEqual(["", "ceph-c"]);
  // 소스를 비우고 목적지 ceph-b → 소스는 ceph-a 만
  await userEvent.selectOptions(screen.getByLabelText("소스 스토리지"), "");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "ceph-b");
  expect(optionNames("소스 스토리지")).toEqual(["", "ceph-a"]);
});

test("사용자: 허용 쌍이 없으면 선택지가 비고 안내가 뜨며 제출이 잠긴다", async () => {
  await openUserTarget([]);
  expect(screen.getByRole("note", { name: "허용된 스토리지 조합" }))
    .toHaveTextContent("관리자가 허용한 sync 스토리지 조합이 없어");
  expect(optionNames("소스 스토리지")).toEqual([""]);
  expect(optionNames("목적지 스토리지")).toEqual([""]);
  expect(submitButton()).toBeDisabled();
  expect(screen.getByText("허용된 sync 조합이 없어 지금은 제출할 수 없습니다")).toBeInTheDocument();
});

test("사용자: 허용 목록에 없는 조합으로 남으면(일부 해제) 다시 고르라고 한다", async () => {
  let pairs = [{ source_storage: "ceph-b", destination_storage: "ceph-a" },
               { source_storage: "ceph-c", destination_storage: "ceph-c" }];
  await openUserTarget(pairs);
  server.use(http.get("/api/user/sync-pairs", () => HttpResponse.json({ restricted: true, pairs })));
  await userEvent.selectOptions(screen.getByLabelText("소스 스토리지"), "ceph-b");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "ceph-a");
  pairs = [{ source_storage: "ceph-c", destination_storage: "ceph-c" }];
  act(() => { focusManager.setFocused(false); focusManager.setFocused(true); });
  expect(await screen.findByRole("alert")).toHaveTextContent("허용되지 않은 소스 → 목적지 조합입니다");
  expect(screen.getByText("허용된 조합으로 소스·목적지를 다시 고르세요")).toBeInTheDocument();
  expect(submitButton()).toBeDisabled();
  act(() => { focusManager.setFocused(undefined); });
});

test("사용자: 허용 조합을 고르면 제출 바디가 그대로 나간다", async () => {
  const captured = captureSubmit();
  await openUserTarget([{ source_storage: "ceph-b", destination_storage: "ceph-a" }]);
  await userEvent.selectOptions(screen.getByLabelText("소스 스토리지"), "ceph-b");
  await userEvent.type(screen.getByLabelText("소스 경로"), "x");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "ceph-a");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "y");
  await userEvent.click(submitButton());
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(captured.body).toMatchObject({ operation: "sync", source_storage: "ceph-b",
                                        destination_storage: "ceph-a" });
});

test("사용자: 허용 목록 조회가 실패하면 sync 선택지를 비우고 사유를 보인다", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json(meUser)),
    http.get("/api/user/sync-pairs", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
  );
  renderPage();
  expect(await screen.findByText(/허용된 스토리지 조합을 불러오지 못했습니다/)).toBeInTheDocument();
  expect(optionNames("소스 스토리지")).toEqual([""]);
  expect(submitButton()).toBeDisabled();
});

test("관리자는 허용 쌍과 무관하게 모든 스토리지가 양쪽에 보인다(조회도 하지 않는다)", async () => {
  let asked = false;
  server.use(http.get("/api/user/sync-pairs", () => { asked = true; return HttpResponse.json(userPairs); }));
  renderPage();
  const src = await screen.findByLabelText("소스 스토리지");
  await within(src).findByRole("option", { name: "cephfs" });
  await userEvent.selectOptions(src, "cephfs");
  expect(optionNames("목적지 스토리지")).toEqual(["", "cephfs", "cephfs-secondary"]);
  expect(screen.queryByRole("note", { name: "허용된 스토리지 조합" })).not.toBeInTheDocument();
  expect(asked).toBe(false);
});

test("사용자: 화면에 있는 동안 허용이 해제되면 그 자리에서 이유를 보이고 제출이 잠긴다", async () => {
  let pairs = [{ source_storage: "ceph-b", destination_storage: "ceph-a" }];
  server.use(http.get("/api/user/sync-pairs", () => HttpResponse.json({ restricted: true, pairs })));
  await openUserTarget(pairs);
  server.use(http.get("/api/user/sync-pairs", () => HttpResponse.json({ restricted: true, pairs })));
  await userEvent.selectOptions(screen.getByLabelText("소스 스토리지"), "ceph-b");
  await userEvent.type(screen.getByLabelText("소스 경로"), "x");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "ceph-a");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "y");
  expect(submitButton()).toBeEnabled();
  expect(screen.queryByRole("alert")).toBeNull();
  // 관리자가 쌍을 해제 -- 창 포커스 재조회(react-query refetchOnWindowFocus)로 도착한다
  pairs = [];
  act(() => { focusManager.setFocused(false); focusManager.setFocused(true); });
  // 경보는 페이지에 하나(대상 구획) -- findByRole 은 둘 이상이면 던진다
  expect(await screen.findByRole("alert")).toHaveTextContent("허용되지 않은 소스 → 목적지 조합입니다");
  expect(submitButton()).toBeDisabled();
  // 허용이 전부 사라졌으니 요약의 잠김 이유도 "조합이 없다" 쪽이다
  expect(screen.getByText("허용된 sync 조합이 없어 지금은 제출할 수 없습니다")).toBeInTheDocument();
  act(() => { focusManager.setFocused(undefined); });
});

// ---- 목적지 소유권 안내(2026-10-01): 관리자는 실제 설정(root·실행 신원·chown)대로 ---------------

const ownershipCard = () => screen.getByLabelText("목적지 조건과 소유권");

test("관리자 기본(root 실행): 소유권은 소스 그대로 -- 상위 디렉토리는 그래도 있어야 한다", async () => {
  server.use(http.get("/api/user/storages", () => HttpResponse.json([
    { storage_name: "cephfs", backend_type: "cephfs", status: "Ready", managed_root: "/cephfs/managed" },
    { storage_name: "cephfs-secondary", backend_type: "cephfs", status: "Ready", managed_root: "/cephfs2/managed" }])));
  renderPage();
  await fillSyncTarget();
  expect(ownershipCard()).toHaveTextContent(
    "소유권: root 실행이라 목적지와 복사된 파일·디렉토리는 소스의 소유자·그룹을 그대로 유지합니다");
  expect(ownershipCard()).toHaveTextContent("상위 디렉토리는 그래도 이미 있어야 합니다");
  // root 실행엔 "쓰기 권한이 필요" 가 아니라 "이미 있어야" -- 요약 문구와 같은 사실
  expect(ownershipCard()).toHaveTextContent("상위 디렉토리는 이미 있어야 합니다(root 실행 — 쓰기 권한 검사 우회)");
  expect(ownershipCard()).toHaveTextContent("/cephfs2/managed/c 가 이미 있어야 합니다");
  expect(ownershipCard()).not.toHaveTextContent(/쓰기 권한(이|도)[^.]*필요/);
  // root 는 최상위뿐 아니라 이미 있던 같은 경로 항목 전부를 소스 소유로(dsync 기본 비교) -- 범위를 축소하지 않는다
  expect(ownershipCard()).toHaveTextContent("그 안의 같은 경로 항목은 소스의 소유·권한·시각으로 다시 맞춰집니다(chown·chmod 를 지정하면 그 값)");
  expect(ownershipCard()).toHaveTextContent("목적지에 이미 있던 같은 경로의 항목(최상위 디렉토리 포함)도 소유자·그룹·권한·시각이 소스 것으로");
  expect(ownershipCard()).toHaveTextContent("root 가 아닌 실행에서 권한은 실행 신원의 uid·LDAP 주 그룹");
  expect(ownershipCard()).toHaveTextContent("root 권한으로 실행하면 권한·소유 검사는 우회됩니다(아래 실행 설정에서 선택)");
  expect(screen.getByText("소스의 소유자·그룹 그대로(root 실행)")).toBeInTheDocument();
  expect(screen.getByText(/상위 디렉토리 .* 가 있어야 함\(root 실행 — 권한 검사 우회\)/)).toBeInTheDocument();
});

test("관리자가 root 를 끄면 카드가 그 자리에서 비 root 기준으로 바뀌고, 다른 실행 신원은 그 사용자 uid:gid", async () => {
  const first = renderPage();
  await fillSyncTarget();
  await userEvent.click(screen.getByLabelText("root 권한으로 실행"));          // 끔
  // 같은 화면의 카드가 즉시 비 root 기준(쓰기 권한·실행 신원 소유)으로 바뀐다
  expect(ownershipCard()).toHaveTextContent("root 실행이 아니면 실행 신원 소유여야 합니다");
  expect(ownershipCard()).toHaveTextContent(/쓰기 권한이 있어야 합니다/);
  expect(ownershipCard()).toHaveTextContent("root 가 아닌 실행에서 권한은 실행 신원의 uid·LDAP 주 그룹");
  expect(screen.getByText("요청자 본인(root)의 uid:gid(주 그룹)")).toBeInTheDocument();   // meAdmin.actor
  first.unmount();

  renderPage();
  await fillSyncTarget();
  await userEvent.type(screen.getByLabelText("실행 신원(선택)"), "cocoa.song");  // root 기본 꺼짐
  expect(screen.getByText("실행 신원 cocoa.song의 uid:gid(주 그룹)")).toBeInTheDocument();
});

test("chown 을 지정하면 소유권 안내가 그 값으로 바뀐다", async () => {
  renderPage();
  await fillAndOpenAdvanced();
  await userEvent.type(screen.getByLabelText("chown"), "10003:10000");
  expect(screen.getByText("chown 지정값 10003:10000")).toBeInTheDocument();
});

test("root 자격 없는 관리자: root 언급 없이 실행 신원 기준, 소유는 본인 uid:gid", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json(meAdminNoRoot)));
  renderPage();
  await fillSyncTarget();
  expect(ownershipCard()).toHaveTextContent("목적지 조건과 소유권 — 실행 신원(uid/gid) 기준");
  expect(ownershipCard()).toHaveTextContent("실행 신원 소유여야 합니다");
  expect(ownershipCard()).not.toHaveTextContent("root");
  expect(screen.getByText("요청자 본인(ops2)의 uid:gid(주 그룹)")).toBeInTheDocument();
});

test("비 root 에서 chown 을 지정하면 본인 uid:gid 가 아니면 실패한다고 경고한다", async () => {
  renderPage();
  await fillAndOpenAdvanced();
  await userEvent.click(screen.getByLabelText("root 권한으로 실행"));          // 끔
  await userEvent.type(screen.getByLabelText("chown"), "10003:10000");
  expect(screen.getByText("chown 지정값 10003:10000 — 비 root: 본인 uid:gid 가 아니면 적용 안 됨(dsync 는 실패)"))
    .toBeInTheDocument();
});

test("고급 옵션에 오류가 있으면 펼쳐진 채 접히지 않는다(잠긴 제출의 이유가 숨지 않게)", async () => {
  // 위저드 시절엔 스텝 재마운트가 가려 주던 함정: open prop 이 true→true 면 React 가 DOM 을 다시
  // 열어 주지 않아, 사용자가 summary 를 눌러 접으면 오류가 닫힌 패널 속에 숨었다.
  renderPage();
  await fillAndOpenAdvanced();
  await userEvent.type(screen.getByLabelText("chown"), "alice:users");
  const details = screen.getByText("고급 옵션").closest("details")!;
  await userEvent.click(screen.getByText("고급 옵션"));                     // 접으려 한다
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });  // toggle 이벤트는 비동기
  expect(details.open).toBe(true);
  expect(within(fieldBox("chown")).getByText(/chown 은 숫자 uid:gid 만 지정할 수 있습니다/)).toBeVisible();
  expect(submitButton()).toBeDisabled();
  // 오류를 고치면 다시 접을 수 있다
  await userEvent.clear(screen.getByLabelText("chown"));
  await userEvent.click(screen.getByText("고급 옵션"));
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  expect(details.open).toBe(false);
});

test("chown 에 이름을 쓰면 오류로 막는다(잡 컨테이너엔 LDAP 이 없다 -- 서버 chown_name_not_supported)", async () => {
  renderPage();
  await fillAndOpenAdvanced();
  await userEvent.type(screen.getByLabelText("chown"), "cocoa.song:mig");
  // 같은 문장이 소유권 카드에도 나온다(입력값 안내) -- 오류는 chown 칸으로 좁혀 단언한다
  expect(within(fieldBox("chown")).getByText(/chown 은 숫자 uid:gid 만 지정할 수 있습니다/)).toBeInTheDocument();
  expect(submitButton()).toBeDisabled();
});
