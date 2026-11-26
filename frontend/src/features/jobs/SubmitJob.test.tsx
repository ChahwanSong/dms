import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route, useLocation, useNavigate } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { SubmitJob } from "./SubmitJob";
import { DIRECT_HELP, OPEN_NOATIME_HELP, QUIET_HELP, SCAN_INT_FIELDS, SYNC_INT_FIELDS, SYNC_MAX_ERRORS_NOTE }
  from "./optionRules";
import { REASON_MESSAGES } from "../../lib/api";
import { NODE_CPU_CAP_NOTE } from "../../lib/jobTool";
import type { UserStorage } from "../../lib/types";
import type { Me } from "../../lib/types";
import {
  buttonsWithoutType, goStep, next, nextButton, prevButton, reasonLine, stepCard, stepHeading, toReview,
} from "../../test/wizard";

// 2026-10-06: SubmitJob 이 4스텝 위저드에서 한 장짜리 시트(번호 구획) + 오른쪽 제출 요약이 됐다.
// 2026-11-26: 단계별 등록(사용자 결정 「초안 1 상단 스테퍼」) -- 작업 종류 → 대상 → 옵션 → 검토·제출 네 단계 + 하단 고정 바. 값·검증·
// 바디는 그대로다: 바디 toEqual 단언은 한 글자도 바꾸지 않았다(그것이 「바이트 단위로 같다」의 그물이다). 단계 이동은 test/wizard 헬퍼.
// 2026-10-26 LDAP 정리: 이 라인은 k8s 신원 모드 전용이다 -- 옛 ldap 모드 화면(스토리지 직접 고르기·실행 신원·root
// 체크박스·sync 허용 쌍 필터)은 지웠다. 여기서는 **면제 관리자(전체 PV·직접 경로 권한)의 직접 경로 흐름**으로 경로 즉답·
// 옵션·제출 바디 계약을 고정한다(모든 바디에 run_as_root: true). 역할별 PV 주소 흐름(일반 사용자·목록 밖 관리자·면제
// 관리자의 PV 검색·권한 기준 사용자)은 SubmitJob.k8s.test.tsx 가 맡는다.

const storageRows: UserStorage[] = [
  { storage_name: "cephfs", backend_type: "cephfs", status: "Ready" },
  { storage_name: "cephfs-secondary", backend_type: "cephfs", status: "Ready" },
];
// 서버 /me 그대로(identity_mode 는 상수 "k8s" -- 화면은 그 값으로 가르지 않는다). 연산이 sync 하나뿐(사이트 기본) -- ②에서 시작한다.
const meUser: Me = { actor: "alice", role: "user", identity_mode: "k8s", unrestricted: false, user_operations: ["sync"] };
// 면제 관리자 = 관리자 + 특권 목록/계정 부여 + 세션(me.can_run_as_root = me.unrestricted).
const meAdmin: Me = { actor: "root", role: "admin", can_run_as_root: true, identity_mode: "k8s", unrestricted: true,
                      user_operations: ["scan", "sync", "rm"] };

// sync 고급 숫자 옵션의 프리필 기본값(사용자 조정 2026-08-16) — 폼이 값을 미리
// 채우므로 바디에 **항상** 실린다. 리터럴이 아니라 단일 출처를 읽어 폼과 테스트가
// 같은 값을 보게 한다(사본이면 프리필을 바꿀 때 테스트가 조용히 낡는다).
const SYNC_NUM_DEFAULTS = {
  batch_files: Number(SYNC_INT_FIELDS.batch_files.prefill),
  bufsize: Number(SYNC_INT_FIELDS.bufsize.prefill),
};
// pristine sync 폼이 싣는 옵션: open_noatime(기본 ON -- 모든 작업이 root) + 숫자 프리필.
const SYNC_DEFAULT_OPTS = { open_noatime: true, ...SYNC_NUM_DEFAULTS };

let pairsAsked = 0;
let posts = 0;
const server = setupServer(
  http.get("/api/auth/me", () => HttpResponse.json(meAdmin)),
  http.get("/api/user/storages", () => HttpResponse.json(storageRows)),
  http.get("/api/user/sync-pairs", () => { pairsAsked += 1; return HttpResponse.json({ restricted: false, pairs: [] }); }),
  http.get("/api/user/k8s/volumes", () => HttpResponse.json({
    clusters: [], unrestricted: false, truncated: false, namespaces: [], volumes: [] })),
  http.get("/api/admin/k8s/volumes", () => HttpResponse.json({ clusters: [], truncated: false, volumes: [] })),
  http.get("/api/admin/k8s/access", () => HttpResponse.json({ clusters: [], unrecognized: [], users: [] })),
);
beforeAll(() => server.listen());
afterEach(() => { server.resetHandlers(); pairsAsked = 0; posts = 0; });
afterAll(() => server.close());

// 지금 URL 의 검색 문자열(위저드의 ?step= 거울)과 뒤로·앞으로 가기(숨은 버튼 -- 역할 질의에 안 잡힌다).
function Probe() {
  const loc = useLocation();
  const navigate = useNavigate();
  return (
    <div hidden>
      <span data-testid="search">{loc.search}</span>
      <button type="button" data-testid="back" onClick={() => navigate(-1)} />
      <button type="button" data-testid="forward" onClick={() => navigate(1)} />
    </div>
  );
}
const search = () => new URLSearchParams(screen.getByTestId("search").textContent ?? "");

function renderPage(entries: string[] = ["/jobs/new"]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
        <Routes>
          <Route path="/jobs/new" element={<><SubmitJob /><Probe /></>} />
          <Route path="/jobs/:id" element={<h1>요청 상세</h1>} />
          <Route path="/away" element={<h1>다른 화면</h1>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, qc };
}

// ---- 입력 헬퍼 ---------------------------------------------------------------
// 면제 관리자는 연산이 셋이라 ① 작업 종류에서 시작한다. 쪽은 기본이 「PV」(전체 PV 검색)라, 직접 경로 칸을 쓰려면 그 쪽의 「지정
// 방식」을 「직접 경로」로 바꾼다(me 로드 뒤에야 전환이 보인다). 스토리지 목록 로드는 option 등장으로 기다린다. 끝 상태는 「그 단계에
// 머문다」 -- 제출 바디 시험은 그 뒤 toReview() → 제출한다.

const submitButton = () => screen.getByRole("button", { name: "제출" });
const REVIEW = "검토·제출";
const OPTIONS = "옵션·실행 설정";      // 관리자 ③ 제목

// ① 연산 고르기(단계는 그대로).
async function pickOp(op: "sync" | "scan" | "rm") {
  await screen.findByRole("option", { name: op });
  await userEvent.selectOptions(screen.getByLabelText("연산"), op);
}

async function rawMode(side: "소스" | "목적지" | "대상") {
  const group = await screen.findByRole("group", { name: `${side} 지정 방식` });
  await userEvent.click(within(group).getByRole("button", { name: "직접 경로" }));
}

// ① → ②(sync). me(admin) 로드를 기다린다.
async function toTarget() {
  await screen.findByRole("option", { name: "rm" });
  await next("소스와 목적지");
}

// sync(초기 연산)의 소스·목적지를 직접 경로로 채운다(② 에 머문다).
async function fillSyncTarget() {
  await toTarget();
  await rawMode("소스");
  await rawMode("목적지");
  const sourceSelect = screen.getByLabelText("소스 스토리지");
  // 옵션 텍스트는 이름만 -- 정확 매칭으로 기다린다(cephfs 는 cephfs-secondary 의 부분 문자열이라 exact 필수).
  await within(sourceSelect).findByRole("option", { name: "cephfs" });
  await userEvent.selectOptions(sourceSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("소스 경로"), "a/b");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "cephfs-secondary");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "c/d");
}

// 연산을 바꾸고 ②로 가서 대상을 직접 경로로 바꾼 뒤 스토리지 목록 로드를 기다린다(② 에 머문다).
async function chooseOperation(op: "scan" | "rm") {
  await pickOp(op);
  await next(op === "scan" ? "스캔 대상" : "삭제 대상");
  await rawMode("대상");
  const storageSelect = screen.getByLabelText("스토리지");
  await within(storageSelect).findByRole("option", { name: "cephfs" });
  return storageSelect;
}

// rm 을 고르고 스토리지·경로를 채운다(② 에 머문다).
async function fillRmTarget() {
  const storageSelect = await chooseOperation("rm");
  await userEvent.selectOptions(storageSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("대상 경로"), "a/b");
}

// 검토까지 가서 제출하고 요청 상세를 기다린다.
async function submitFromHere() {
  await toReview(REVIEW);
  await userEvent.click(submitButton());
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
}

// 입력 칸 하나의 묶음(라벨 + 그 아래 오류) -- 같은 문장이 소유권 안내 카드에도 나오는 오류를
// 그 칸으로 좁혀 단언할 때 쓴다(예: chown 이름 오류는 카드의 소유권 문장에도 들어간다).
const fieldBox = (label: string) =>
  screen.getByLabelText(label).closest("label")!.parentElement as HTMLElement;

function captureSubmit() {
  const captured: { body: any } = { body: null };
  server.use(http.post("/api/user/requests", async ({ request }) => {
    posts += 1;
    captured.body = await request.json();
    return HttpResponse.json({ request_id: "rX", state: "Pending" }, { status: 202 });
  }));
  return captured;
}

// 막힌 「다음」: aria-disabled + 이유 줄 + 눌러도 단계 그대로.
async function expectNextBlocked(reason: string) {
  expect(nextButton()).toHaveAttribute("aria-disabled", "true");
  expect(reasonLine()).toHaveTextContent(reason);
  expect(nextButton()).toHaveAccessibleDescription(reason);
  const title = stepHeading().textContent;
  await userEvent.click(nextButton());
  expect(stepHeading()).toHaveTextContent(title ?? "");
}
const expectNextOpen = () => expect(nextButton()).not.toHaveAttribute("aria-disabled");

// ---- 단계 구조 ----------------------------------------------------------------

test("단계 넷(작업 종류 → 소스와 목적지 → 옵션·실행 설정 → 검토·제출) — 지금 단계 본문만 그리고 오른쪽 제출 요약은 없다", async () => {
  const { unmount } = renderPage();
  await screen.findByRole("option", { name: "rm" });              // me(admin) 로드
  const nav = screen.getByRole("navigation", { name: "진행 단계" });
  expect(within(nav).getAllByRole("button").map((b) => b.getAttribute("title")))
    .toEqual(["작업 종류", "소스와 목적지", OPTIONS, REVIEW]);
  expect(stepHeading()).toHaveTextContent("작업 종류");
  expect(screen.queryByRole("heading", { level: 2, name: "제출 요약" })).toBeNull();
  expect(screen.getByRole("button", { name: "다음" })).toBeInTheDocument();
  // ① 의 칸만 있다(연산·권한 기준 사용자) -- 대상·옵션 칸은 그 단계에서만 그린다
  expect(screen.getByLabelText("연산")).toBeInTheDocument();
  expect(screen.getByLabelText("권한 기준 사용자")).toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "소스 지정 방식" })).toBeNull();
  expect(screen.queryByLabelText("delete")).toBeNull();
  await next("소스와 목적지");
  expect(screen.getByRole("group", { name: "소스 지정 방식" })).toBeInTheDocument();
  expect(screen.queryByLabelText("연산")).toBeNull();
  unmount();

  // 사용자(연산 sync 하나뿐): ③ 제목은 「옵션」 -- 실행 설정(우선순위)은 관리자 전용
  server.use(http.get("/api/auth/me", () => HttpResponse.json(meUser)));
  renderPage();
  await screen.findByText(/권한 있는 PV 에서 sync 을 실행합니다/);   // me(user) 로드
  expect(within(screen.getByRole("navigation", { name: "진행 단계" })).getByRole("button", { name: /^옵션/ }))
    .toHaveAttribute("title", "옵션");
});

test("연산을 고르면 옆에 무엇이 일어나는지 한 줄로 말하고, ② 제목이 바뀐다", async () => {
  renderPage();
  expect(await screen.findByText("소스 디렉토리의 내용을 목적지로 복사·동기화합니다.")).toBeInTheDocument();
  await pickOp("scan");
  expect(screen.getByText("대상 경로의 파일 수·용량·데이터 온도 통계를 수집합니다.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^스캔 대상/ })).toBeInTheDocument();
  await pickOp("rm");
  expect(screen.getByText("대상 경로와 그 아래를 모두 삭제합니다.")).toBeInTheDocument();
  await next("삭제 대상");
});

test("사용자 화면은 관리자 전용 정책 API 를 부르지 않는다(403 잡음 없음)", async () => {
  let asked = 0;
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json(meUser)),
    http.get("/api/admin/policies", () => { asked += 1; return HttpResponse.json({ detail: "forbidden" }, { status: 403 }); }));
  renderPage();
  await screen.findByText(/권한 있는 PV 에서 sync 을 실행합니다/);
  await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  expect(asked).toBe(0);
});

test("관리자 화면은 정책을 조회해 ③ 실행 설정에 우선순위 기본값 실값을 보인다", async () => {
  let asked = 0;
  server.use(http.get("/api/admin/policies", () => {
    asked += 1;
    return HttpResponse.json([{ tool: "dsync", max_nodes: 6, procs_per_node: 4, queue: "dms-data",
      default_priority: "high", max_priority: "high", preview_timeout_seconds: null,
      execution_timeout_seconds: 3600, enabled: 1, updated_at: "2026-08-05T00:00:00Z", updated_by: "admin" }]);
  }));
  renderPage();
  await fillSyncTarget();
  await next(OPTIONS);
  expect(await screen.findByRole("option", { name: "(정책 기본: high)" })).toBeInTheDocument();
  expect(asked).toBeGreaterThan(0);
  // 실행 설정 캡션의 노드 CPU 상한 한 줄(2026-10-16, 배치 생성과 같은 문장)
  expect(screen.getByText(NODE_CPU_CAP_NOTE)).toBeInTheDocument();
});

test("실행 신원 칸·root 체크박스가 없다 -- 모든 작업은 root, 사용자 지정은 면제자의 「권한 기준 사용자」뿐", async () => {
  // 2026-10-26 LDAP 정리: 옛 ldap 모드의 「실행 신원(선택)」·「root 권한으로 실행」은 지웠다(서버가 run_as_root 를 강제).
  const { unmount } = renderPage();
  expect(await screen.findByText(/모든 작업은 root 로 실행됩니다 — 전체 PV·직접 경로 권한이 있어/)).toBeInTheDocument();
  await fillSyncTarget();
  expect(screen.queryByLabelText("실행 신원(선택)")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("root 권한으로 실행")).not.toBeInTheDocument();
  await toReview(REVIEW);
  const priv = screen.getByText(/^root\(특권\) — 권한 검사 우회, sync 는 목적지 소유·권한을 소스에 맞춤\(chown·chmod 지정 시 그 값\)$/);
  expect(priv).toHaveClass("text-bad");
  unmount();

  server.use(http.get("/api/auth/me", () => HttpResponse.json(meUser)));
  renderPage();
  await screen.findByText(/권한 있는 PV 에서 sync 을 실행합니다/);
  await goStep("작업 종류");
  expect(screen.queryByLabelText("실행 신원(선택)")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("권한 기준 사용자")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("root 권한으로 실행")).not.toBeInTheDocument();
});

test("sync 허용 쌍을 조회하지 않는다(설계 D14 -- 양쪽이 PV 주소인 sync 는 PV 권한이 판정, 직접 경로는 면제자만)", async () => {
  const { unmount } = renderPage();
  await fillSyncTarget();
  expect(screen.queryByRole("note", { name: "허용된 스토리지 조합" })).not.toBeInTheDocument();
  unmount();
  server.use(http.get("/api/auth/me", () => HttpResponse.json(meUser)));
  renderPage();
  await screen.findByText(/권한 있는 PV 에서 sync 을 실행합니다/);
  await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  expect(pairsAsked).toBe(0);
});

// ---- Enter·제출 방어(design-wizard §1-6) ----------------------------------------------------------------------------

test("Enter: 유효하면 다음 단계일 뿐 절대 제출하지 않는다 — 막혔으면 그대로, 제안 목록 칸(권한 기준 사용자)은 단계 그대로", async () => {
  // 모든 작업이 root 이고 rm 도 있다 -- 경로 입력 중 Enter 한 번에 요청이 나가면 안 된다.
  const captured = captureSubmit();
  renderPage();
  const scope = await screen.findByLabelText("권한 기준 사용자");
  // list 속성(datalist 제안)이 있는 칸: Enter 는 제안 고르기 -- 단계·포커스를 옮기지 않는다
  scope.focus();
  await userEvent.keyboard("{Enter}");
  expect(stepHeading()).toHaveTextContent("작업 종류");
  expect(scope).toHaveFocus();
  await next("소스와 목적지");
  await rawMode("소스");
  await rawMode("목적지");
  // 막힌 단계의 Enter: 단계 그대로 · 이유 줄은 그대로 · 포커스도 그대로(aria-invalid 칸이 없다)
  const srcPath = screen.getByLabelText("소스 경로");
  await userEvent.type(srcPath, "a/b{Enter}");
  expect(stepHeading()).toHaveTextContent("소스와 목적지");
  expect(srcPath).toHaveFocus();
  const sourceSelect = screen.getByLabelText("소스 스토리지");
  await within(sourceSelect).findByRole("option", { name: "cephfs" });
  await userEvent.selectOptions(sourceSelect, "cephfs");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "cephfs-secondary");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "c/d{Enter}");
  // 유효해졌으니 Enter = 다음 단계(제목으로 포커스)
  await waitFor(() => expect(stepHeading()).toHaveTextContent(OPTIONS));
  expect(stepHeading()).toHaveFocus();
  // 체크박스의 Enter 는 막기만(브라우저는 체크박스 Enter 로도 form 을 제출한다)
  const del = screen.getByLabelText("delete");
  del.focus();
  const ev = new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
  del.dispatchEvent(ev);
  expect(ev.defaultPrevented).toBe(true);
  expect(stepHeading()).toHaveTextContent(OPTIONS);
  expect(del).not.toBeChecked();
  // 숫자 칸 Enter → 검토. 검토 단계에 칸이 생겨도 Enter 는 제출하지 않는다(규칙 ⑦ -- 마지막 단계는 아무것도 안 한다)
  await userEvent.click(screen.getByText("고급 옵션"));
  await userEvent.type(screen.getByLabelText("batch_files"), "{Enter}");
  await waitFor(() => expect(stepHeading()).toHaveTextContent(REVIEW));
  await new Promise((r) => setTimeout(r, 150));
  expect(captured.body).toBeNull();
  expect(screen.queryByRole("heading", { name: "요청 상세" })).not.toBeInTheDocument();
});

test("조합 중 Enter(keyCode 229 — 한글 조합 확정)는 무시한다", async () => {
  renderPage();
  await fillSyncTarget();
  const dst = screen.getByLabelText("목적지 경로");
  const ev = new KeyboardEvent("keydown", { key: "Enter", keyCode: 229, bubbles: true, cancelable: true });
  dst.dispatchEvent(ev);
  expect(ev.defaultPrevented).toBe(false);
  expect(stepHeading()).toHaveTextContent("소스와 목적지");
});

test("type 불변식: 어느 단계에도 type 없는 버튼이 없다(form 안 type 없는 버튼 = submit 버튼)", async () => {
  const captured = captureSubmit();
  const { container } = renderPage();
  await screen.findByRole("option", { name: "rm" });
  expect(buttonsWithoutType(container)).toHaveLength(0);                       // ①
  await fillSyncTarget();
  expect(buttonsWithoutType(container)).toHaveLength(0);                       // ② (sync, 직접 경로)
  await next(OPTIONS);
  await userEvent.click(screen.getByText("고급 옵션"));
  expect(buttonsWithoutType(container)).toHaveLength(0);                       // ③ (고급 옵션 펼침)
  await next(REVIEW);
  expect(buttonsWithoutType(container)).toHaveLength(0);                       // ④
  // 스테퍼로 ② 에 돌아가도(검토 「수정」과 같은 go) 요청은 나가지 않는다
  await goStep("소스와 목적지");
  expect(stepHeading()).toHaveTextContent("소스와 목적지");
  await goStep("작업 종류");
  await pickOp("rm");
  await next("삭제 대상");
  await rawMode("대상");
  expect(buttonsWithoutType(container)).toHaveLength(0);                       // ② (rm)
  await new Promise((r) => setTimeout(r, 100));
  expect(captured.body).toBeNull();
});

test("암묵 제출 방어: 대상이 유효한 ②에서 Enter·강제 submit 은 요청을 내지 않고, ③ 「다음」은 검토를 보인다", async () => {
  const captured = captureSubmit();
  const { container } = renderPage();
  await fillRmTarget();
  fireEvent.submit(container.querySelector("form")!);                         // 단계 가드(current !== "review")
  await new Promise((r) => setTimeout(r, 100));
  expect(captured.body).toBeNull();
  await userEvent.type(screen.getByLabelText("대상 경로"), "{Enter}");
  await waitFor(() => expect(stepHeading()).toHaveTextContent(OPTIONS));
  fireEvent.submit(container.querySelector("form")!);
  await userEvent.click(nextButton());
  expect(stepHeading()).toHaveTextContent(REVIEW);
  await new Promise((r) => setTimeout(r, 100));
  expect(captured.body).toBeNull();
  expect(posts).toBe(0);
});

test("더블클릭 방어: ③에서 「다음」을 두 번 눌러도 제출되지 않고(두 번째 클릭은 같은 노드의 「제출」), 새로 누르면 한 번 제출된다", async () => {
  const captured = captureSubmit();
  renderPage();
  await fillSyncTarget();
  await next(OPTIONS);
  const btn = nextButton();
  await userEvent.dblClick(btn);
  expect(stepHeading()).toHaveTextContent(REVIEW);
  expect(btn).toBe(submitButton());                // 두 번째 클릭이 실제로 제출 노드에 떨어졌다(시험이 헛돌지 않는다)
  await new Promise((r) => setTimeout(r, 100));
  expect(posts).toBe(0);
  await userEvent.click(submitButton());
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(posts).toBe(1);
  expect(captured.body.operation).toBe("sync");
});

test("비동기 막힘 뒤 강제 submit: 검토에서 스토리지 목록 재조회가 실패하면 검토에 머물고 제출이 잠기며 blocked 가드가 막는다", async () => {
  // react-query 는 창 포커스 때 다시 조회하고 데이터가 있어도 재조회 실패면 isError -- 그때 검토에서 ②로 끌어내리지 않는다.
  const captured = captureSubmit();
  const { container, qc } = renderPage();
  await fillSyncTarget();
  await toReview(REVIEW);
  expect(submitButton()).toBeEnabled();
  server.use(http.get("/api/user/storages", () => HttpResponse.json({ detail: "storage_list_failed" }, { status: 500 })));
  await act(async () => { await qc.invalidateQueries({ queryKey: ["user-storages"] }); });
  await waitFor(() => expect(submitButton()).toBeDisabled());
  expect(stepHeading()).toHaveTextContent(REVIEW);
  // 바의 첫 이유 + 검토 목록(SubmitHints) 둘 다 같은 문장 -- 목록으로 좁혀 본다
  expect(reasonLine()).toHaveTextContent("스토리지 목록 조회 실패 — 새로고침 후 다시 시도하세요");
  expect(within(document.getElementById("submit-job-hints")!).getByText("스토리지 목록 조회 실패 — 새로고침 후 다시 시도하세요"))
    .toBeInTheDocument();
  expect(submitButton()).toHaveAccessibleDescription(/스토리지 목록 조회 실패/);
  expect(screen.getByRole("button", { name: /^2단계에서 고치기/ })).toBeInTheDocument();
  // 단계 가드는 통과(검토)하고 blocked 가드가 막는다 -- 옛 「강제 submit 도 차단」의 뮤테이션 표적 역할
  fireEvent.submit(container.querySelector("form")!);
  await new Promise((r) => setTimeout(r, 150));
  expect(captured.body).toBeNull();
  // 「이전」·「다음」은 현재 단계 게이트만 본다(③ 은 통과) -- ② 에는 빨간 점(고칠 것이 있음)
  await userEvent.click(prevButton());
  expect(stepHeading()).toHaveTextContent(OPTIONS);
  expect(screen.getByRole("button", { name: /^소스와 목적지.*\(고칠 것이 있음\)$/ })).toBeInTheDocument();
  await userEvent.click(nextButton());
  expect(stepHeading()).toHaveTextContent(REVIEW);
  await userEvent.click(screen.getByRole("button", { name: /^2단계에서 고치기/ }));
  expect(stepHeading()).toHaveTextContent("소스와 목적지");
});

test("「옵션 기본값으로 검토」: ②에서 바로 검토로 — 바디는 ③을 거친 것과 같고, ③을 거친 뒤엔 「검토로 건너뛰기」", async () => {
  const captured = captureSubmit();
  renderPage();
  await fillSyncTarget();
  const skip = screen.getByRole("button", { name: "옵션 기본값으로 검토" });
  await userEvent.click(skip);
  expect(stepHeading()).toHaveTextContent(REVIEW);
  expect(stepHeading()).toHaveFocus();
  // ③을 들렀다 ② 로 돌아오면 이름이 바뀐다(「검토로 돌아가기」가 바에 대신 선다 -- 검토를 이미 봤다)
  await goStep(OPTIONS);
  await goStep("소스와 목적지");
  expect(screen.queryByRole("button", { name: "옵션 기본값으로 검토" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "검토로 돌아가기" }));
  expect(stepHeading()).toHaveTextContent(REVIEW);
  await userEvent.click(submitButton());
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(captured.body).toEqual({
    operation: "sync", source_storage: "cephfs", source: "a/b", destination_storage: "cephfs-secondary", destination: "c/d",
    options: SYNC_DEFAULT_OPTS, run_as_root: true,
  });
});

test("제출 바디 동일: ③에서 값을 바꿨다 되돌리고 ①↔④ 를 오가도 바디는 기본 바디와 같다", async () => {
  const captured = captureSubmit();
  renderPage();
  await fillSyncTarget();
  await next(OPTIONS);
  await userEvent.click(screen.getByLabelText("delete"));
  await userEvent.click(screen.getByLabelText("delete"));
  await next(REVIEW);
  await goStep("작업 종류");
  await toReview(REVIEW);
  await userEvent.click(submitButton());
  expect(await screen.findByRole("heading", { name: "요청 상세" })).toBeInTheDocument();
  expect(captured.body).toEqual({
    operation: "sync", source_storage: "cephfs", source: "a/b", destination_storage: "cephfs-secondary", destination: "c/d",
    options: SYNC_DEFAULT_OPTS, run_as_root: true,
  });
});

test("언마운트 상태 보존: ②의 직접 경로·③의 옵션·고급 옵션 펼침이 ②→③→② 뒤에도 그대로다", async () => {
  renderPage();
  await fillSyncTarget();
  await next(OPTIONS);
  await userEvent.click(screen.getByLabelText("contents"));
  await userEvent.click(screen.getByText("고급 옵션"));
  await goStep("소스와 목적지");
  expect(screen.getByLabelText("소스 경로")).toHaveValue("a/b");
  expect(screen.getByLabelText("목적지 스토리지")).toHaveValue("cephfs-secondary");
  await next(OPTIONS);
  expect(screen.getByLabelText("contents")).toBeChecked();
  expect(screen.getByText("고급 옵션").closest("details")!.open).toBe(true);
});

// ---- URL(?step=) -------------------------------------------------------------------------------------------------

test("URL: 「다음」마다 ?step= 를 push 하고 뒤로·앞으로 가기가 단계를 오간다, 새로고침한 ?step=review 는 빈 폼이라 ②로 맞춘다", async () => {
  const view = renderPage();
  await toTarget();
  expect(search().get("step")).toBe("target");
  fireEvent.click(screen.getByTestId("back"));
  await waitFor(() => expect(stepHeading()).toHaveTextContent("작업 종류"));
  fireEvent.click(screen.getByTestId("forward"));
  await waitFor(() => expect(stepHeading()).toHaveTextContent("소스와 목적지"));
  view.unmount();

  // 값이 사라진 다시 마운트 -- 「값 없이 검토 화면」을 보이지 않는다(replace)
  renderPage(["/jobs/new?step=review"]);
  await screen.findByRole("group", { name: "소스 지정 방식" });
  expect(stepHeading()).toHaveTextContent("소스와 목적지");
  await waitFor(() => expect(search().get("step")).toBe("target"));
});

test("연산이 하나뿐이면(sync) ①을 완료로 두고 ②에서 시작한다 — root 사실은 ② 머리, ①은 다시 열 수 있고 URL 은 replace", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json(meUser)));
  renderPage(["/away", "/jobs/new"]);
  await screen.findByText(/권한 있는 PV 에서 sync 을 실행합니다/);
  await waitFor(() => expect(stepHeading()).toHaveTextContent("소스와 목적지"));
  expect(screen.getByRole("button", { name: /^작업 종류.*\(완료\)$/ })).toBeInTheDocument();
  expect(within(stepCard()).getByText(/모든 작업은 root 로 실행됩니다 — 고를 수 있는 PV 는 내 namespace 권한/)).toBeInTheDocument();
  expect(stepHeading()).not.toHaveFocus();                 // jump 는 포커스를 옮기지 않는다
  await waitFor(() => expect(search().get("step")).toBe("target"));
  // 연산 선택지는 me.user_operations 그대로(표시 게이트 · 서버 403 이 진짜 차단)
  await goStep("작업 종류");
  const select = screen.getByLabelText("연산") as HTMLSelectElement;
  expect(Array.from(select.options).map((o) => o.value)).toEqual(["sync"]);
  await next("소스와 목적지");
  // replace 였으니 ①로 가는 히스토리 항목이 없다 -- 뒤로 두 번(②←① push 하나 + 시작)이면 화면을 떠난다
  fireEvent.click(screen.getByTestId("back"));
  await waitFor(() => expect(stepHeading()).toHaveTextContent("작업 종류"));
  fireEvent.click(screen.getByTestId("back"));
  await waitFor(() => expect(stepHeading()).toHaveTextContent("소스와 목적지"));
  fireEvent.click(screen.getByTestId("back"));
  expect(await screen.findByRole("heading", { name: "다른 화면" })).toBeInTheDocument();
});

// ---- 경로 즉답 ---------------------------------------------------------------

test("입력한 상대경로를 관리 디렉토리에 붙인 「지정 경로」를 보여 준다(모르면 생략)", async () => {
  // d192 이식(괴리 [C2]): 글자 그대로 붙인 경로라 링크를 풀지 않는다 -- 「실제 경로」라 부르지 않는다.
  server.use(http.get("/api/user/storages", () => HttpResponse.json([
    { storage_name: "cephfs", backend_type: "cephfs", status: "Ready", managed_root: "/cephfs/managed" },
    { storage_name: "cephfs-secondary", backend_type: "cephfs", status: "Ready" }])));   // 뿌리 모름
  renderPage();
  await fillSyncTarget();
  expect(screen.getByText("지정 경로: /cephfs/managed/a/b")).toBeInTheDocument();
  // 목적지 스토리지는 managed_root 가 없다 -- 거짓 경로를 지어내지 않는다.
  expect(screen.queryByText(/지정 경로: .*c\/d/)).toBeNull();
});

test("서버가 거부할 경로(/ 시작·..·관리 디렉토리 자신)는 지정 경로 대신 이유를 보이고 「다음」을 잠근다", async () => {
  // domain.validate_relative_path(422 unsafe_path)의 즉답 미러. 예전엔 다듬은 값으로 절대경로를 지어
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
  expectNextOpen();
  await userEvent.clear(screen.getByLabelText("소스 경로"));
  await userEvent.type(screen.getByLabelText("소스 경로"), "/a/b");
  expect(screen.getByText(/"\/" 로 시작할 수 없습니다/)).toBeInTheDocument();
  expect(screen.queryByText(/^지정 경로: /)).toBeNull();
  await expectNextBlocked("경로를 관리 디렉토리 아래 상대경로로 고치세요");
  fireEvent.submit(container.querySelector("form")!);
  await new Promise((r) => setTimeout(r, 150));
  expect(received).toBeNull();
  await userEvent.clear(screen.getByLabelText("소스 경로"));
  await userEvent.type(screen.getByLabelText("소스 경로"), "a/../b");
  expect(screen.getByText(/"\.\." 은 쓸 수 없습니다/)).toBeInTheDocument();
  expect(nextButton()).toHaveAttribute("aria-disabled", "true");
  await userEvent.clear(screen.getByLabelText("소스 경로"));
  await userEvent.type(screen.getByLabelText("소스 경로"), "a/b");
  expect(screen.getByText("지정 경로: /cephfs/managed/a/b")).toBeInTheDocument();
  expectNextOpen();
  expect(reasonLine()).toHaveTextContent(`다음: ${OPTIONS}`);
});

test("rm 대상이 관리 디렉토리 자신(.)이면 「다음」이 잠긴다", async () => {
  renderPage();
  const storageSelect = await chooseOperation("rm");
  await userEvent.selectOptions(storageSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("대상 경로"), "./");
  expect(screen.getByText(/관리 디렉토리 자신\(\.\)은 지정할 수 없습니다/)).toBeInTheDocument();
  await expectNextBlocked("경로를 관리 디렉토리 아래 상대경로로 고치세요");
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
    && el.textContent === "지정 경로: /cephfs/managed/a/b ")).toBeInTheDocument();
  await submitFromHere();
  expect(captured.body.source).toBe("a/b ");
});

test("② 바 이유: 빈 폼에선 남은 입력을 말하고 「다음」이 잠기며, 다 채우면 풀리고 검토에 값·옵션 토큰이 보인다", async () => {
  renderPage();
  await toTarget();
  await expectNextBlocked("소스·목적지 PV 를 모두 고르세요");
  await rawMode("소스");
  await rawMode("목적지");
  expect(reasonLine()).toHaveTextContent("소스·목적지를 모두 고르고 경로를 입력하세요");
  const sourceSelect = screen.getByLabelText("소스 스토리지");
  await within(sourceSelect).findByRole("option", { name: "cephfs" });
  await userEvent.selectOptions(sourceSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("소스 경로"), "a/b");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "cephfs-secondary");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "c/d");
  expect(reasonLine()).toHaveTextContent(`다음: ${OPTIONS}`);
  await toReview(REVIEW);
  const card = stepCard();
  expect(within(card).getByText("cephfs:a/b")).toBeInTheDocument();
  expect(within(card).getByText("cephfs-secondary:c/d")).toBeInTheDocument();
  // 옵션은 JSON 한 줄이 아니라 값 토큰(제출 바디와 같은 함수에서)
  for (const t of ["open_noatime", "batch_files=1000000", "bufsize=4194304"])
    expect(within(card).getByText(t)).toBeInTheDocument();
  expect(submitButton()).toBeEnabled();
  expect(document.getElementById("submit-job-hints")).toBeNull();          // 막힌 이유가 없으면 목록도 없다
});

// ---- 대상 sanity -------------------------------------------------------------

test("대상 sanity: sync 경로가 비면 「다음」이 잠기고 문구가 뜬다", async () => {
  // 사용자 결정(2026-08-22): 스토리지·경로 미입력이면 제출할 수 없다.
  renderPage();
  await toTarget();
  await rawMode("소스");
  await rawMode("목적지");
  // 아무것도 안 채운 상태: 잠김 + 안내
  await expectNextBlocked("소스·목적지를 모두 고르고 경로를 입력하세요");
  // 스토리지만 고르고 경로는 비움 → 여전히 잠김
  const srcSel = screen.getByLabelText("소스 스토리지");
  await within(srcSel).findByRole("option", { name: "cephfs" });   // 목록 로드 대기(스코프)
  await userEvent.selectOptions(srcSel, "cephfs");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "cephfs-secondary");
  expect(nextButton()).toHaveAttribute("aria-disabled", "true");
  // 경로까지 채우면 풀린다(기본 옵션은 유효하다)
  await userEvent.type(screen.getByLabelText("소스 경로"), "a");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "b");
  expectNextOpen();
});

test("대상 sanity: rm 은 대상 경로가 비면 「다음」이 잠긴다", async () => {
  renderPage();
  const storageSelect = await chooseOperation("rm");
  await userEvent.selectOptions(storageSelect, "cephfs");
  await expectNextBlocked("스토리지와 대상 경로를 입력하세요");
  await userEvent.type(screen.getByLabelText("대상 경로"), "x");
  expectNextOpen();
});

test("스토리지 드롭다운이 API 목록으로 채워진다", async () => {
  renderPage();
  await toTarget();
  await rawMode("소스");
  const sourceSelect = screen.getByLabelText("소스 스토리지");
  // 이름만(상태 접미 없음). 정확 매칭으로 두 옵션을 구분.
  expect(await within(sourceSelect).findByRole("option", { name: "cephfs" })).toBeInTheDocument();
  expect(within(sourceSelect).getByRole("option", { name: "cephfs-secondary" })).toBeInTheDocument();
  // 상태 접미(Ready/Degraded)는 더 이상 노출되지 않는다.
  expect(within(sourceSelect).queryByText(/\(Ready\)|\(Degraded\)/)).not.toBeInTheDocument();
});

test("관리자 전용 스토리지는 관리자 피커에 (관리자 전용) 으로 구분된다", async () => {
  // 2026-09-30 사용 범위: 비관리자 응답엔 아예 없고(서버), 관리자에겐 admin_only 표식이 온다.
  server.use(http.get("/api/user/storages", () => HttpResponse.json([
    ...storageRows, { storage_name: "adm-only", backend_type: "cephfs", status: "Ready",
                      admin_only: true }])));
  renderPage();
  await toTarget();
  await rawMode("소스");
  const sourceSelect = screen.getByLabelText("소스 스토리지");
  expect(await within(sourceSelect).findByRole("option", { name: "adm-only (관리자 전용)" }))
    .toBeInTheDocument();
  expect(within(sourceSelect).getByRole("option", { name: "cephfs" })).toBeInTheDocument();
});

test("연산을 rm으로 바꾸면 대상 필드 구성이 바뀌고, 검토 단계에 rm 경고가 한 번 뜬다", async () => {
  renderPage();
  await toTarget();
  await rawMode("소스");
  await rawMode("목적지");
  expect(screen.getByLabelText("목적지 스토리지")).toBeInTheDocument();
  expect(screen.getByLabelText("목적지 경로")).toBeInTheDocument();
  expect(screen.queryByLabelText("대상 경로")).not.toBeInTheDocument();

  await goStep("작업 종류");
  await pickOp("rm");
  await next("삭제 대상");
  expect(screen.queryByLabelText("목적지 스토리지")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("목적지 경로")).not.toBeInTheDocument();
  await rawMode("대상");
  expect(screen.getByLabelText("대상 경로")).toBeInTheDocument();
  const storageSelect = screen.getByLabelText("스토리지");
  await within(storageSelect).findByRole("option", { name: "cephfs" });
  await userEvent.selectOptions(storageSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("대상 경로"), "a/b");
  // 삭제가 root 로 수행된다는 사실(③ 실행 설정)
  await next(OPTIONS);
  expect(screen.getByText("삭제가 root 권한으로 수행됩니다(모든 작업은 root)")).toBeInTheDocument();
  // 경고는 한 곳(검토 단계 제출 버튼 위) -- getByText 는 둘 이상이면 던진다
  await next(REVIEW);
  expect(screen.getByText(
    "삭제는 되돌릴 수 없습니다. 미리보기에서 대상을 확인한 뒤 확인해야 실행됩니다.",
  )).toBeInTheDocument();
});

test("sync 제출 바디가 정확하다", async () => {
  const captured = captureSubmit();
  renderPage();
  await fillSyncTarget();
  await submitFromHere();

  expect(captured.body).toEqual({
    operation: "sync",
    source_storage: "cephfs", source: "a/b",
    destination_storage: "cephfs-secondary", destination: "c/d",
    // 계약: batch_files·bufsize 프리필이 항상 실린다(지우면 빠진다 -- 아래 테스트). open_noatime 기본 ON(root).
    options: SYNC_DEFAULT_OPTS,
    // priority 생략 = (정책 기본) — 슬라이스 37, resolve_priority 가 정책값 해석
    // run_as_root: 늘 명시 true(설계 D2 -- 서버는 생략도 root 로 읽고 명시 false 는 422 run_as_root_required).
    run_as_root: true,
  });
});

test("rm 제출 바디에 options.recursive가 true로 들어간다", async () => {
  const captured = captureSubmit();
  renderPage();
  await fillRmTarget();
  await submitFromHere();

  expect(captured.body.options.recursive).toBe(true);
  expect(captured.body).toEqual({
    operation: "rm",
    storage: "cephfs", target: "a/b",
    options: { recursive: true },
    run_as_root: true,
  });
});

// 강제 submit 이벤트: 제출 버튼은 type=button 이라 정상 동선으론 form submit 이 안 나지만, 가드는 Enter 유출·미래 회귀에 대한
// 이중 방어다 -- 이벤트를 직접 쏴서 막히는지 관측한다. 옵션이 깨진 채로는 도달 규칙상 ④에 못 닿으므로 ③에서 쏘면 단계 가드가 먼저
// 막는다(blocked 가드 자체의 관측은 「비동기 막힘 뒤 강제 submit」 시험이 맡는다).
async function forceSubmitAndSettle(form: HTMLFormElement) {
  fireEvent.submit(form);
  // mutate → msw 왕복이 비동기라, 잘못 전송됐다면 캡처가 도착할 시간을 준다.
  await new Promise((r) => setTimeout(r, 150));
}

test("recursive를 해제하면 ③ 「다음」이 잠기고 강제 submit도 차단된다", async () => {
  const captured = captureSubmit();
  const { container } = renderPage();
  await fillRmTarget();
  await next(OPTIONS);
  expectNextOpen();

  await userEvent.click(screen.getByLabelText("재귀 삭제(필수)"));

  expect(screen.getByText("재귀 옵션이 필요합니다")).toBeInTheDocument();
  await expectNextBlocked("빨간 안내가 붙은 옵션 값을 고치세요");
  // 검토는 잠긴다(현재 단계가 막혀 그 뒤는 도달 불가)
  expect(screen.getByRole("button", { name: /^검토·제출.*\(앞 단계를 먼저 마치세요\)$/ })).toHaveAttribute("aria-disabled", "true");

  await forceSubmitAndSettle(container.querySelector("form")!);
  expect(captured.body).toBeNull();
  expect(screen.queryByRole("heading", { name: "요청 상세" })).not.toBeInTheDocument();
});

test("stat과 lite를 동시에 체크하면 ③ 「다음」이 잠기고 강제 submit도 차단된다", async () => {
  const captured = captureSubmit();
  const { container } = renderPage();
  await fillRmTarget();
  await next(OPTIONS);
  await userEvent.click(screen.getByLabelText("stat"));
  await userEvent.click(screen.getByLabelText("lite"));

  expect(screen.getByText("stat과 lite는 함께 쓸 수 없습니다")).toBeInTheDocument();
  // 바의 잠김 이유는 필드 옆 문구와 다른 문장(같은 문장을 두 번 그리지 않는다)
  await expectNextBlocked("빨간 안내가 붙은 옵션 값을 고치세요");

  await forceSubmitAndSettle(container.querySelector("form")!);
  expect(captured.body).toBeNull();
  expect(screen.queryByRole("heading", { name: "요청 상세" })).not.toBeInTheDocument();
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

test("스토리지 목록 로드가 실패하면 직접 경로를 고른 동안 ②에 오류가 뜨고 「다음」이 잠긴다", async () => {
  server.use(http.get("/api/user/storages", () =>
    HttpResponse.json({ detail: "storage_list_failed" }, { status: 500 })));
  renderPage();
  await toTarget();
  await rawMode("소스");
  expect(await screen.findByText("storage_list_failed")).toBeInTheDocument();
  // 스토리지를 못 고르니 대상 sanity(스토리지·경로 필수)도 잠그지만, 바의 첫 이유는 목록 실패다
  await expectNextBlocked("스토리지 목록 조회 실패 — 새로고침 후 다시 시도하세요");
});

test("검토 단계에 rm 경고와 대상이 노출된다", async () => {
  renderPage();
  await fillRmTarget();
  await toReview(REVIEW);
  expect(screen.getByText(
    "삭제는 되돌릴 수 없습니다. 미리보기에서 대상을 확인한 뒤 확인해야 실행됩니다.",
  )).toBeInTheDocument();
  // 요약이 실제 입력값의 함수임을 확인(빈 껍데기 요약 방지).
  expect(within(stepCard()).getByText("cephfs:a/b")).toBeInTheDocument();
  expect(submitButton()).toBeInTheDocument();
});

// ---- 고급 sync 옵션(슬라이스 26 Task 5) ---------------------------------------
// 서버(domain.py _OPTION_SPECS SYNC)가 최종 심판이고, 폼은 노출+즉답 미러만 한다.
// msw 로 request body 를 캡처해 "무엇이 실제로 전송되는가"를 단언한다 — 빈 값
// 생략(truthy 검사 금지)과 number 변환이 계약이다.

// sync 대상을 채우고 ③ 으로 가서 <details> 고급 옵션을 펼친다(기본 접힘 확인 겸).
async function fillAndOpenAdvanced() {
  await fillSyncTarget();
  await next(OPTIONS);
  const details = screen.getByText("고급 옵션").closest("details")!;
  expect(details.open).toBe(false);
  await userEvent.click(screen.getByText("고급 옵션"));
}

// 프리필 계약(사용자 조정 2026-08-16): batch_files·bufsize 는 placeholder 가 아니라
// **실제 값**으로 미리 채워져 있고, 그래서 손대지 않아도 바디에 실린다.
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
  // d192 이식: bufsize 설명에 direct 조건이 붙는다(괴리 [8]) -- 프리필 문장은 그대로 앞에 있다.
  expect(screen.getByText(
    "미리 채운 4194304 = 4 MiB(서버 기본). 비워도 같은 값이 적용됩니다. direct 를 켜면 4096·8192·…·4194304(4 KiB~4 MiB 의 "
    + "2의 거듭제곱)만 쓸 수 있습니다.",
  )).toBeInTheDocument();
});

test("고급 숫자 옵션을 지우면 그 키가 바디에서 빠진다(도구 기본으로 복귀)", async () => {
  const captured = captureSubmit();
  renderPage();
  await fillAndOpenAdvanced();
  await userEvent.clear(screen.getByLabelText("batch_files"));
  await userEvent.clear(screen.getByLabelText("bufsize"));
  await submitFromHere();
  // 빈 문자열은 "미입력"이라 batch_files·bufsize 는 통째로 생략된다. open_noatime 은 root 기본이라 남는다.
  expect(captured.body.options).toEqual({ open_noatime: true });
});

test("open_noatime 은 늘 켤 수 있다(모든 작업이 root) — 기본 ON 으로 실리고, 해제하면 빠진다", async () => {
  // 사용자 결정(2026-08-22)의 root 실행 기본 ON. 끄면 checkedOptions 가 false 를 생략한다.
  const rootOn = captureSubmit();
  const first = renderPage();
  await fillAndOpenAdvanced();
  expect(screen.getByLabelText("open_noatime")).toBeEnabled();
  expect(screen.getByLabelText("open_noatime")).toBeChecked();
  expect(screen.queryByText(/open_noatime 은 root 실행에서만 적용됩니다/)).toBeNull();
  // 안내(d190 이식): 기본 켬 + root 라 모든 파일에 적용 -- 비 root 의 「실행 신원 소유가 아닌 파일」 조건은 이 라인에 없다.
  expect(screen.getByText(OPEN_NOATIME_HELP)).toBeInTheDocument();
  expect(screen.queryByText(/실행 신원 소유가 아닌/)).toBeNull();
  // relatime(리눅스 기본)은 「차이 없음」 쪽이 아니다(d192 이식, 리뷰 portal-batch-27): 하루 넘게 안 읽혔거나 수정 뒤 처음
  // 읽는 파일은 갱신한다 -- 차이가 없는 곳은 noatime 마운트·일부 CephFS 뿐이다.
  expect(OPEN_NOATIME_HELP).toContain("리눅스 기본 relatime 도 마지막 접근이 하루를 넘었거나 수정 뒤 처음 읽는 파일은 갱신하므로");
  expect(OPEN_NOATIME_HELP).toContain("noatime 마운트나 일부 CephFS 처럼 읽기가 atime 을 바꾸지 않는 곳에선 차이가 없습니다");
  expect(OPEN_NOATIME_HELP).not.toContain("relatime·noatime");
  // chown 캡션: 직접 경로 목적지는 root 라 소스 소유 보존
  expect(screen.getByText(/비우면 원래\(소스\) 소유권을 보존합니다\(root 실행\)/)).toBeInTheDocument();
  await submitFromHere();
  expect(rootOn.body.options).toEqual({ open_noatime: true, ...SYNC_NUM_DEFAULTS });
  expect(rootOn.body.run_as_root).toBe(true);
  first.unmount();

  const captured = captureSubmit();
  renderPage();
  await fillAndOpenAdvanced();
  await userEvent.click(screen.getByLabelText("open_noatime"));  // 해제
  await submitFromHere();
  expect(captured.body.options).toEqual(SYNC_NUM_DEFAULTS);   // open_noatime 빠짐
});

test("chmod·chown 문자열이 그대로 전송된다", async () => {
  const captured = captureSubmit();
  renderPage();
  await fillAndOpenAdvanced();
  await userEvent.type(screen.getByLabelText("chmod"), "D770,F660");
  await userEvent.type(screen.getByLabelText("chown"), "10003:10000");
  await submitFromHere();
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
  await submitFromHere();
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
  await submitFromHere();
  expect(captured.body.options).toEqual({ open_noatime: true, batch_files: 1000, bufsize: 4096 });
});

test("batch_files 상한은 1,000만 — 그 값은 통과, 넘으면 즉답 문구 + 「다음」 잠김", async () => {
  // 서버 _OPTION_SPECS[SYNC] 상한(사용자 조정 2026-08-16: 100만 → 1,000만)의 미러.
  renderPage();
  await fillAndOpenAdvanced();
  await userEvent.clear(screen.getByLabelText("batch_files"));
  await userEvent.type(screen.getByLabelText("batch_files"), "10000000");
  expect(screen.queryByText(/batch_files는/)).toBeNull();
  expectNextOpen();
  await userEvent.type(screen.getByLabelText("batch_files"), "0");   // → 1,000만 초과
  expect(screen.getByText("batch_files는 0..10000000 범위의 정수여야 합니다"))
    .toBeInTheDocument();
  expect(nextButton()).toHaveAttribute("aria-disabled", "true");
});

test("잘못된 chmod는 「다음」을 잠그고 필드별 문구를 띄운다 — 막힌 「다음」을 누르면 그 칸으로 포커스", async () => {
  renderPage();
  await fillAndOpenAdvanced();
  await userEvent.type(screen.getByLabelText("chmod"), "999x");
  expect(screen.getByText("chmod 형식이 올바르지 않습니다 (예: D770,F660)")).toBeInTheDocument();
  expect(screen.getByLabelText("chmod")).toHaveAttribute("aria-invalid", "true");
  await expectNextBlocked("빨간 안내가 붙은 옵션 값을 고치세요");
  expect(screen.getByLabelText("chmod")).toHaveFocus();
});

test("범위 밖 bufsize는 「다음」을 잠근다", async () => {
  renderPage();
  await fillAndOpenAdvanced();
  await userEvent.clear(screen.getByLabelText("bufsize"));
  await userEvent.type(screen.getByLabelText("bufsize"), "100");
  expect(nextButton()).toHaveAttribute("aria-disabled", "true");
  expect(screen.getByText("bufsize는 4096..1073741824 범위의 정수여야 합니다")).toBeInTheDocument();
  // 범위 오류가 먼저다 -- direct 를 켜도 같은 칸에 두 문장을 겹쳐 그리지 않는다(d192 이식).
  await userEvent.click(screen.getByLabelText("direct"));
  expect(screen.queryByText(REASON_MESSAGES.direct_bufsize_misaligned)).toBeNull();
});

// ---- 슬라이스 37: scan 연산 ----------------------------------------------------

test("direct·quiet 설명이 칸 옆에 보인다 — quiet 는 실패 원인까지 지운다, direct 는 nsync 무시 + bufsize 조건(d190·d192 이식)", async () => {
  // 예전엔 title 툴팁(+ sr-only)뿐이었고 quiet 는 「로그를 줄인다」고만 했다(괴리 [9]·[13]·[18]·[29]). 포크 db1e33c: nsync 는
  // --direct 를 받기만 한다.
  const view = renderPage();
  await fillSyncTarget();
  await next(OPTIONS);
  expect(screen.getByText(DIRECT_HELP)).toBeInTheDocument();
  expect(screen.getByLabelText("direct").closest("label")).not.toHaveAttribute("title");
  expect(DIRECT_HELP).toMatch(/dsync 로 실행될 때만 적용되고, .*nsync 로 실행되면 무시됩니다/);
  expect(DIRECT_HELP).toContain("bufsize 는 4 KiB~4 MiB 의 2의 거듭제곱");
  expect(screen.getByText(QUIET_HELP.sync)).toBeInTheDocument();
  expect(QUIET_HELP.sync).toContain("실패한 항목 목록과 오류 줄도 남지 않아");
  expect(screen.queryByText(/실행 로그를 줄입니다/)).toBeNull();
  // nsync 실패 한도(괴리 [21] -- 화면 어디에도 없어 멈춘 이유를 알 수 없었다)는 sync 「실행 옵션」 머리
  expect(screen.getByText(SYNC_MAX_ERRORS_NOTE)).toBeInTheDocument();
  expect(SYNC_MAX_ERRORS_NOTE).toContain("기본 65,000건");
  view.unmount();
  renderPage();
  await fillRmTarget();
  await next(OPTIONS);
  expect(screen.getByText(QUIET_HELP.rm)).toBeInTheDocument();
  expect(QUIET_HELP.rm).toContain("지우지 못한 항목 목록과 오류 줄도 남지 않아");
  expect(screen.queryByText(SYNC_MAX_ERRORS_NOTE)).toBeNull();
});

test("경로 조건에 심볼릭 링크 규칙이 있다 — sync 는 검토의 목적지 조건 카드(어느 단계든), rm 은 ② 대상 아래(중간 단계만, 끝 링크는 링크만)", async () => {
  // d192 이식(괴리 [14]): 링크로 된 목적지가 「디렉토리여야 합니다」를 만족한다고 보고 낸 요청이 거부됐다. dms-k8s 는 2026-10-28
  // 통일 봉쇄라 관리 디렉토리·PV 둘 다, rm 은 끝 링크 허용(사용자 결정) -- dms-ssc 의 「대상 자신이 링크여도 거부」를 쓰면 틀린다.
  const view = renderPage();
  await fillSyncTarget();
  expect(screen.queryByRole("note", { name: "삭제 경로 조건" })).toBeNull();
  await toReview(REVIEW);
  expect(screen.getByLabelText("목적지 조건과 소유권")).toHaveTextContent(
    "소스·목적지 경로가 관리 디렉토리·PV 아래 어느 단계든(마지막 이름 포함) 심볼릭 링크를 거치면 거부됩니다 — 링크가 가리키는 "
    + "곳이 아니라 링크를 거치지 않는 경로로 요청하세요.");
  view.unmount();
  renderPage();
  await chooseOperation("rm");
  const note = screen.getByRole("note", { name: "삭제 경로 조건" });
  expect(note).toHaveTextContent("삭제할 경로의 중간 단계(관리 디렉토리·PV 아래)가 심볼릭 링크면 거부됩니다. 대상 자신이 심볼릭 "
    + "링크면 링크만 지우고, 링크가 가리키는 곳은 그대로 둡니다.");
  expect(note).not.toHaveTextContent("대상 자신 포함");
});

test("direct 를 켜면 bufsize 는 4096·2^k ≤ 4 MiB 만 — 서버 422 direct_bufsize_misaligned 와 같은 문장으로 즉답·잠금", async () => {
  // d192 이식(괴리 [8]): dsync 는 O_DIRECT 로 4 MiB 조각을 나눠 읽어, 그 약수가 아닌 버퍼면 큰 파일에서 MPI 중단했다.
  const captured = captureSubmit();
  renderPage();
  await fillAndOpenAdvanced();
  await userEvent.clear(screen.getByLabelText("bufsize"));
  await userEvent.type(screen.getByLabelText("bufsize"), "8388608");        // 8 MiB -- direct 없이는 정상
  expect(screen.queryByText(REASON_MESSAGES.direct_bufsize_misaligned)).toBeNull();
  expectNextOpen();
  await userEvent.click(screen.getByLabelText("direct"));
  expect(screen.getByText(REASON_MESSAGES.direct_bufsize_misaligned)).toBeInTheDocument();
  expect(nextButton()).toHaveAttribute("aria-disabled", "true");
  // 고급 옵션이 접히지 않는다(다음 단계로 못 가는 이유가 숨지 않게 -- advancedError)
  await userEvent.click(screen.getByText("고급 옵션"));
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });  // toggle 이벤트는 비동기
  expect(screen.getByText("고급 옵션").closest("details")!.open).toBe(true);
  for (const bad of ["1000000", "6144", "2048"]) {                         // 2의 거듭제곱 아님·4 KiB 정렬 아님·4 KiB 미만(범위 오류)
    await userEvent.clear(screen.getByLabelText("bufsize"));
    await userEvent.type(screen.getByLabelText("bufsize"), bad);
    expect(nextButton()).toHaveAttribute("aria-disabled", "true");
  }
  await userEvent.clear(screen.getByLabelText("bufsize"));
  await userEvent.type(screen.getByLabelText("bufsize"), "65536");          // 64 KiB = 4096·2^4
  expect(screen.queryByText(REASON_MESSAGES.direct_bufsize_misaligned)).toBeNull();
  await submitFromHere();
  expect(captured.body.options).toEqual({ ...SYNC_DEFAULT_OPTS, direct: true, bufsize: 65536 });
});

test("admin scan 제출 바디가 정확하다(프리필 batch_files + 입력 broken_limit)", async () => {
  const captured = captureSubmit();
  renderPage();
  const select = (await screen.findByLabelText("연산")) as HTMLSelectElement;
  // 면제 관리자는 세 연산 전부 -- scan 은 sync 와 rm 사이(서버 user_operations 순서와 무관하게 화면 순서).
  await screen.findByRole("option", { name: "scan" });
  expect(Array.from(select.options).map((o) => o.value)).toEqual(["sync", "scan", "rm"]);
  const storageSelect = await chooseOperation("scan");
  await userEvent.selectOptions(storageSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("대상 경로"), "team/data");
  await next(OPTIONS);
  await userEvent.clear(screen.getByLabelText("broken_limit"));
  await userEvent.type(screen.getByLabelText("broken_limit"), "500");
  await submitFromHere();
  expect(captured.body).toEqual({
    operation: "scan", storage: "cephfs", target: "team/data",
    options: { batch_files: 1000000, broken_limit: 500 },   // batch_files 는 프리필(서버 기본과 같은 값)
    run_as_root: true,
  });
});

test("scan 의 verbose·quiet 동시는 「다음」을 잠근다", async () => {
  renderPage();
  const storageSelect = await chooseOperation("scan");
  await userEvent.selectOptions(storageSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("대상 경로"), "a");
  await next(OPTIONS);
  expectNextOpen();
  await userEvent.click(screen.getByLabelText("verbose"));
  await userEvent.click(screen.getByLabelText("quiet"));
  expect(screen.getByText("verbose와 quiet는 함께 쓸 수 없습니다")).toBeInTheDocument();
  await expectNextBlocked("빨간 안내가 붙은 옵션 값을 고치세요");
});

// scan 프리필 계약(사용자 결정 2026-09-17): batch_files 1,000,000·broken_limit 100 이
// 미리 채워져 있고 손대지 않으면 그대로 전송된다 — 같은 값이 서버 기본
// (domain._OPTION_DEFAULTS)이라 비워도 결과는 같지만, 요청 상세에 명시적으로 남는다.
test("scan 옵션은 batch_files 1,000,000·broken_limit 100 이 프리필돼 그대로 전송된다", async () => {
  const captured = captureSubmit();
  renderPage();
  const storageSelect = await chooseOperation("scan");
  await userEvent.selectOptions(storageSelect, "cephfs");
  await userEvent.type(screen.getByLabelText("대상 경로"), "team/data");
  await next(OPTIONS);
  expect(screen.getByLabelText("batch_files")).toHaveValue(SCAN_INT_FIELDS.batch_files.prefill);
  expect(screen.getByLabelText("broken_limit")).toHaveValue(SCAN_INT_FIELDS.broken_limit.prefill);
  await submitFromHere();
  expect(captured.body.options).toEqual({ batch_files: 1000000, broken_limit: 100 });
});

// ---- 목적지 소유권 안내(2026-10-01): root 실행 기준 ---------------------------------

const ownershipCard = () => screen.getByLabelText("목적지 조건과 소유권");

test("직접 경로 목적지(root 실행): 소유권은 소스 그대로 -- 상위 디렉토리는 그래도 있어야 한다(② 한 줄 + ④ 전문)", async () => {
  server.use(http.get("/api/user/storages", () => HttpResponse.json([
    { storage_name: "cephfs", backend_type: "cephfs", status: "Ready", managed_root: "/cephfs/managed" },
    { storage_name: "cephfs-secondary", backend_type: "cephfs", status: "Ready", managed_root: "/cephfs2/managed" }])));
  renderPage();
  await fillSyncTarget();
  // ② 목적지 한 줄: 요약 행 원문 둘 + 「전문은 ④ 에서」
  expect(screen.getByText("상위 디렉토리 /cephfs2/managed/c 가 있어야 함(root 실행 — 권한 검사 우회). 목적지가 없으면 새로 만듦"))
    .toBeInTheDocument();
  expect(screen.getByText("전문은 ④ 검토·제출에서 봅니다.")).toBeInTheDocument();
  expect(screen.queryByLabelText("목적지 조건과 소유권")).toBeNull();
  await toReview(REVIEW);
  expect(ownershipCard()).toHaveAttribute("role", "note");
  expect(ownershipCard()).toHaveTextContent("목적지 조건과 소유권 — root 실행 기준");
  expect(ownershipCard()).toHaveTextContent("목적지가 없는 경우: sync 가 목적지 디렉토리를 새로 만듭니다");
  expect(ownershipCard()).toHaveTextContent("중간 디렉토리는 만들지 않습니다");
  expect(ownershipCard()).toHaveTextContent(
    "소유권: root 실행이라 목적지와 복사된 파일·디렉토리는 소스의 소유자·그룹을 그대로 유지합니다");
  // root 실행엔 "쓰기 권한이 필요" 가 아니라 "이미 있어야" -- 요약 문구와 같은 사실
  expect(ownershipCard()).toHaveTextContent("상위 디렉토리는 이미 있어야 합니다(root 실행 — 쓰기 권한 검사 우회)");
  expect(ownershipCard()).toHaveTextContent("/cephfs2/managed/c 가 이미 있어야 합니다");
  expect(ownershipCard()).not.toHaveTextContent(/쓰기 권한(이|도)[^.]*필요/);
  // root 는 최상위뿐 아니라 이미 있던 같은 경로 항목 전부를 소스 소유로(dsync 기본 비교) -- 범위를 축소하지 않는다
  expect(ownershipCard()).toHaveTextContent("그 안의 같은 경로 항목은 소스의 소유·권한·시각으로 다시 맞춰집니다(chown·chmod 를 지정하면 그 값)");
  expect(ownershipCard()).toHaveTextContent("목적지에 이미 있던 같은 경로의 항목(최상위 디렉토리 포함)도 소유자·그룹·권한·시각이 소스 것으로");
  // 옛 ldap 모드의 비 root·LDAP 보조 그룹 문단은 없다
  expect(ownershipCard()).not.toHaveTextContent("LDAP");
  expect(ownershipCard()).not.toHaveTextContent("보조 그룹");
  expect(ownershipCard()).toHaveTextContent("조건이 맞지 않으면 미리보기 전에 거부되고 사유가 표시됩니다");
  expect(screen.getByText("소스의 소유자·그룹 그대로(root 실행)")).toBeInTheDocument();
  expect(screen.getByText(/상위 디렉토리 .* 가 있어야 함\(root 실행 — 권한 검사 우회\)/)).toBeInTheDocument();
});

test("chown 을 지정하면 소유권 안내가 그 값으로 바뀐다", async () => {
  renderPage();
  await fillAndOpenAdvanced();
  await userEvent.type(screen.getByLabelText("chown"), "10003:10000");
  await toReview(REVIEW);
  expect(screen.getByText("chown 지정값 10003:10000")).toBeInTheDocument();
});

test("고급 옵션에 오류가 있으면 펼쳐진 채 접히지 않는다(잠긴 「다음」의 이유가 숨지 않게)", async () => {
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
  expect(nextButton()).toHaveAttribute("aria-disabled", "true");
  // 오류를 고치면 다시 접을 수 있다
  await userEvent.clear(screen.getByLabelText("chown"));
  await userEvent.click(screen.getByText("고급 옵션"));
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  expect(details.open).toBe(false);
});

test("chown 에 이름을 쓰면 오류로 막는다(잡 컨테이너엔 디렉터리 NSS 가 없다 -- 서버 chown_name_not_supported)", async () => {
  renderPage();
  await fillAndOpenAdvanced();
  await userEvent.type(screen.getByLabelText("chown"), "cocoa.song:mig");
  expect(within(fieldBox("chown")).getByText(/chown 은 숫자 uid:gid 만 지정할 수 있습니다/)).toBeInTheDocument();
  expect(nextButton()).toHaveAttribute("aria-disabled", "true");
});
