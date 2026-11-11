import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, beforeEach, afterAll, afterEach, test, expect } from "vitest";
import { AccountsList } from "./AccountsList";
import { forgetTransportKey } from "../../lib/passwordTransport";
import { reasonText } from "../../lib/api";
import { isSealed, makeServerKey, openSealed, transportKeyHandler, type TestServerKey } from "../../test/transportKey";

const server = setupServer();
// 계정 생성 다이얼로그는 비밀번호를 봉인해 보낸다(2026-09-07) -- 서버 키 핸들러 필수.
let serverKey: TestServerKey;
beforeAll(async () => { server.listen(); serverKey = await makeServerKey(); });
beforeEach(() => {
  forgetTransportKey();
  // 받는 도메인은 서버 설정(mail-info)에서 -- 하드코딩된 samsung.com 이 아님을 보이려 다른 도메인을 준다.
  server.use(transportKeyHandler(serverKey),
             http.get("/api/auth/mail-info", () => HttpResponse.json({ email_domain: "corp.example", delivery: "stub" })));
});
afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const ACCOUNTS = [
  { username: "admin", role: "admin", email: "admin@example.com", disabled: 0, created_at: "2026-08-05T00:00:00Z" },
  { username: "alice", role: "user", email: null, disabled: 1, created_at: "2026-08-05T00:00:00Z" },
];

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><AccountsList /></QueryClientProvider>);
}

function stubMe(actor: string) {
  server.use(http.get("/api/auth/me", () => HttpResponse.json({ actor, role: "admin" })));
}

test("lists accounts with username, role, and status", async () => {
  stubMe("admin");
  server.use(http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)));
  wrap();
  expect(await screen.findByText("admin", { selector: "td" })).toBeInTheDocument();
  expect(screen.getByText("alice")).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "admin 역할" })).toHaveValue("admin");
  expect(screen.getByRole("combobox", { name: "alice 역할" })).toHaveValue("user");
  expect(screen.getByText("활성")).toBeInTheDocument();
  expect(screen.getByText("비활성")).toBeInTheDocument();
});

test("changing a role sends the correct PUT body", async () => {
  stubMe("admin");
  let capturedBody: unknown;
  server.use(
    http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)),
    http.put("/api/admin/accounts/alice/role", async ({ request }) => {
      capturedBody = await request.json();
      return HttpResponse.json({ ...ACCOUNTS[1], role: "admin" });
    }));
  wrap();
  const select = await screen.findByRole("combobox", { name: "alice 역할" });
  await userEvent.selectOptions(select, "admin");
  expect(capturedBody).toEqual({ role: "admin" });
});

test("toggling status sends the correct PUT body", async () => {
  stubMe("root"); // neither row is the current user, so both toggle buttons stay enabled
  let capturedBody: unknown;
  server.use(
    http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)),
    http.put("/api/admin/accounts/admin/disabled", async ({ request }) => {
      capturedBody = await request.json();
      return HttpResponse.json({ ...ACCOUNTS[0], disabled: 1 });
    }));
  wrap();
  const row = (await screen.findByText("admin", { selector: "td" })).closest("tr")!;
  await userEvent.click(within(row).getByRole("button", { name: "비활성화" }));
  expect(capturedBody).toEqual({ disabled: true });
});

test("the current user's own row has disabled controls", async () => {
  stubMe("alice");
  server.use(http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)));
  wrap();
  const selfRow = (await screen.findByText("alice")).closest("tr")!;
  expect(within(selfRow).getByRole("combobox", { name: "alice 역할" })).toBeDisabled();
  expect(within(selfRow).getByRole("button")).toBeDisabled();
  expect(within(selfRow).getByText("자기 계정은 변경할 수 없습니다")).toBeInTheDocument();

  const otherRow = screen.getByText("admin", { selector: "td" }).closest("tr")!;
  expect(within(otherRow).getByRole("combobox", { name: "admin 역할" })).not.toBeDisabled();
  // 남의 행엔 비밀번호 재설정 버튼이 더 있다(2026-11-03) -- 상태 토글을 이름으로 집는다.
  expect(within(otherRow).getByRole("button", { name: "비활성화" })).not.toBeDisabled();
});

test("shows the Korean message when a mutation returns 409 cannot_lock_self", async () => {
  stubMe("admin");
  server.use(
    http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)),
    http.put("/api/admin/accounts/alice/disabled", () =>
      HttpResponse.json({ detail: "cannot_lock_self" }, { status: 409 })));
  wrap();
  const row = (await screen.findByText("alice")).closest("tr")!;
  await userEvent.click(within(row).getByRole("button", { name: "활성화" }));
  expect(await screen.findByText("자기 계정의 역할 변경·비활성화는 할 수 없습니다")).toBeInTheDocument();
});

test("표 아래 오류 줄은 마지막 동작의 결과만 -- 역할 변경 실패 뒤 다른 동작이 성공하면 지워지고, 실패하면 새 사유로 바뀐다(2026-11-11)", async () => {
  stubMe("root");
  const roleMsg = reasonText("privileged_account_protected");
  const toggleMsg = reasonText("cannot_lock_self");
  server.use(
    http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)),
    http.put("/api/admin/accounts/admin/role", () =>
      HttpResponse.json({ detail: "privileged_account_protected" }, { status: 403 })),
    http.put("/api/admin/accounts/alice/disabled", () => HttpResponse.json({ ...ACCOUNTS[1], disabled: 0 })));
  wrap();
  const aliceRow = (await screen.findByText("alice")).closest("tr")!;
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "admin 역할" }), "user");
  expect(await screen.findByText(roleMsg)).toBeInTheDocument();
  // 다른 계정의 상태 변경이 성공하면 낡은 역할 실패 문구는 사라진다(예전엔 남아 방금 동작이 실패한 것처럼 보였다)
  await userEvent.click(within(aliceRow).getByRole("button", { name: "활성화" }));
  await waitFor(() => expect(screen.queryByText(roleMsg)).not.toBeInTheDocument());
  // 다시 역할 실패 → 이어진 상태 변경 실패: 새 사유가 보이고 낡은 역할 문구는 가리지 않는다
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "admin 역할" }), "user");
  expect(await screen.findByText(roleMsg)).toBeInTheDocument();
  server.use(http.put("/api/admin/accounts/alice/disabled", () =>
    HttpResponse.json({ detail: "cannot_lock_self" }, { status: 409 })));
  await userEvent.click(within(aliceRow).getByRole("button", { name: "활성화" }));
  expect(await screen.findByText(toggleMsg)).toBeInTheDocument();
  expect(screen.queryByText(roleMsg)).not.toBeInTheDocument();
});

test("자기 계정은 삭제 버튼 대신 비활성 사유를 보여준다", async () => {
  stubMe("alice");
  server.use(http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)));
  wrap();
  const selfRow = (await screen.findByText("alice")).closest("tr")!;
  expect(within(selfRow).getByText("자기 계정은 삭제할 수 없습니다")).toBeInTheDocument();
  expect(within(selfRow).queryByRole("button", { name: "삭제" })).toBeNull();
});

test("마지막 활성 관리자는 삭제 버튼 대신 비활성 사유를 보여준다", async () => {
  stubMe("root");   // 어느 행도 자기 자신이 아니다
  server.use(http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)));
  wrap();
  const adminRow = (await screen.findByText("admin", { selector: "td" })).closest("tr")!;
  expect(within(adminRow).getByText("마지막 관리자는 삭제할 수 없습니다")).toBeInTheDocument();
  expect(within(adminRow).queryByRole("button", { name: "삭제" })).toBeNull();
});

test("관리자가 둘이면 삭제 버튼이 뜬다 (대조)", async () => {
  stubMe("root");
  const two = [
    { username: "admin", role: "admin", email: null, disabled: 0, created_at: "2026-08-05T00:00:00Z" },
    { username: "admin2", role: "admin", email: null, disabled: 0, created_at: "2026-08-05T00:00:00Z" },
  ];
  server.use(http.get("/api/admin/accounts", () => HttpResponse.json(two)));
  wrap();
  const adminRow = (await screen.findByText("admin", { selector: "td" })).closest("tr")!;
  expect(within(adminRow).getByRole("button", { name: "삭제" })).toBeInTheDocument();
});

test("사용자명 재입력이 일치해야 삭제가 전송된다", async () => {
  stubMe("root");
  let deleted = false;
  server.use(
    http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)),
    http.delete("/api/admin/accounts/alice", () => {
      deleted = true; return new HttpResponse(null, { status: 204 }); }));
  wrap();
  const aliceRow = (await screen.findByText("alice")).closest("tr")!;
  await userEvent.click(within(aliceRow).getByRole("button", { name: "삭제" }));
  const dialog = await screen.findByRole("dialog");
  const confirm = within(dialog).getByRole("button", { name: "계정 삭제" });
  const input = within(dialog).getByRole("textbox", { name: "삭제 확인 사용자명 재입력" });
  // 불일치면 확인 버튼이 비활성 -- 눌러도 삭제가 안 나간다.
  await userEvent.type(input, "wrong");
  expect(confirm).toBeDisabled();
  await userEvent.clear(input);
  await userEvent.type(input, "alice");
  expect(confirm).toBeEnabled();
  await userEvent.click(confirm);
  await waitFor(() => expect(deleted).toBe(true));
});

test("운영자 계정 생성: 다이얼로그에서 아이디·비밀번호·역할을 POST 한다", async () => {
  stubMe("admin");
  let captured: unknown;
  server.use(
    http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)),
    http.post("/api/admin/accounts", async ({ request }) => {
      captured = await request.json();
      return HttpResponse.json({ username: "new.user", role: "user" }, { status: 201 });
    }),
  );
  wrap();
  await screen.findByText("alice");
  await userEvent.click(screen.getByRole("button", { name: "계정 생성" }));
  const dialog = await screen.findByRole("dialog");
  // 파생 이메일 규칙이 화면에 보인다
  expect(await within(dialog).findByText(/아이디@corp\.example 으로 자동 저장/)).toBeInTheDocument();
  await userEvent.type(within(dialog).getByLabelText("회사 아이디"), "new.user");
  await userEvent.type(within(dialog).getByLabelText("비밀번호"), "pw1");
  await userEvent.selectOptions(within(dialog).getByLabelText("역할"), "admin");
  await userEvent.click(within(dialog).getByRole("button", { name: "생성" }));
  await waitFor(() => expect(captured).not.toBeUndefined());
  const body = captured as { username: string; role: string; password?: string; password_enc: unknown };
  expect({ username: body.username, role: body.role }).toEqual({ username: "new.user", role: "admin" });
  // 평문 password 는 와이어에 없다 -- 봉인을 열면 입력한 값이다.
  expect(body.password).toBeUndefined();
  expect(isSealed(body.password_enc)).toBe(true);
  expect(await openSealed(serverKey, body.password_enc as never, "admin_create", "new.user")).toBe("pw1");
});

test("운영자 계정 생성: 중복 409 는 다이얼로그 안 한국어 사유로 남는다", async () => {
  stubMe("admin");
  server.use(
    http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)),
    http.post("/api/admin/accounts", () =>
      HttpResponse.json({ detail: "account_exists" }, { status: 409 })),
  );
  wrap();
  await screen.findByText("alice");
  await userEvent.click(screen.getByRole("button", { name: "계정 생성" }));
  const dialog = await screen.findByRole("dialog");
  await userEvent.type(within(dialog).getByLabelText("회사 아이디"), "alice");
  await userEvent.type(within(dialog).getByLabelText("비밀번호"), "pw");
  await userEvent.click(within(dialog).getByRole("button", { name: "생성" }));
  expect(await within(dialog).findByText("이미 존재하는 계정입니다")).toBeInTheDocument();
});

test("아이디 검색: 대소문자 무시 부분 일치로 좁히고, 지우면 전체 복원", async () => {
  stubMe("admin");
  server.use(http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)));
  wrap();
  await screen.findByText("alice");
  const search = screen.getByLabelText("아이디 검색");
  await userEvent.type(search, "ALI");
  expect(screen.getByText("alice")).toBeInTheDocument();
  expect(screen.queryByText("admin", { selector: "td" })).not.toBeInTheDocument();
  await userEvent.clear(search);
  expect(screen.getByText("admin", { selector: "td" })).toBeInTheDocument();
  expect(screen.getByText("alice")).toBeInTheDocument();
});

test("아이디 검색: 무일치는 검색어를 밝힌 전용 문구 — 계정 없음과 구분", async () => {
  stubMe("admin");
  server.use(http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)));
  wrap();
  await screen.findByText("alice");
  await userEvent.type(screen.getByLabelText("아이디 검색"), "zzz");
  expect(screen.getByText("'zzz' 와 일치하는 계정이 없습니다")).toBeInTheDocument();
});

// ---- root 실행 자격(2026-10-12 사용자 보고: 「일반 사용자명에게 admin 역할을 줬어. 근데 root로 실행 체크 옵션이 작업 등록할때
// 안뜨네」) -- admin 역할 ≠ root 자격. 이미 root 자격이 있는 관리자만 admin 계정에 준다(서버가 다시 강제). ----

const ROOT_ROWS = [
  { username: "admin", role: "admin", email: null, disabled: 0, created_at: "2026-08-05T00:00:00Z",
    root_source: "config", root_eligible: true },
  { username: "newadm", role: "admin", email: null, disabled: 0, created_at: "2026-08-05T00:00:00Z",
    root_source: null, root_eligible: false },
  { username: "plain", role: "user", email: null, disabled: 0, created_at: "2026-08-05T00:00:00Z",
    root_source: null, root_eligible: false },
];

function stubMeRoot(actor: string, canRoot: boolean) {
  server.use(http.get("/api/auth/me", () => HttpResponse.json({ actor, role: "admin", can_run_as_root: canRoot })));
}

const rowOf = (name: string) => screen.getByText(name, { selector: "td" }).closest("tr") as HTMLElement;

test("root 실행 열: 출처·효력을 보이고, root 자격 있는 관리자만 admin 계정에 「자격 주기」(확인 창 → PUT)", async () => {
  stubMeRoot("admin", true);
  let rows = ROOT_ROWS.map((r) => ({ ...r }));
  const puts: { u: string; body: unknown }[] = [];
  server.use(
    http.get("/api/admin/accounts", () => HttpResponse.json(rows)),
    http.put("/api/admin/accounts/:u/root", async ({ params, request }) => {
      const body = await request.json() as { granted: boolean };
      puts.push({ u: params.u as string, body });
      rows = rows.map((r) => (r.username === params.u
        ? { ...r, root_source: body.granted ? "granted" : null, root_eligible: body.granted, root_granted: body.granted }
        : r));
      return HttpResponse.json(rows.find((r) => r.username === params.u));
    }),
  );
  wrap();
  await screen.findByText("newadm", { selector: "td" });
  expect(screen.getByRole("columnheader", { name: "전체 PV·직접 경로 권한" })).toBeInTheDocument();
  expect(rowOf("admin")).toHaveTextContent("있음 · 배포 설정");
  expect(within(rowOf("admin")).queryByRole("button", { name: /자격/ })).toBeNull();     // 자기 자신·배포 설정
  expect(within(rowOf("plain")).queryByRole("button", { name: /자격/ })).toBeNull();     // user 역할엔 줄 수 없다
  expect(rowOf("newadm")).toHaveTextContent("없음");
  await userEvent.click(within(rowOf("newadm")).getByRole("button", { name: "newadm 전체 PV·직접 경로 권한 주기" }));
  const dlg = await screen.findByRole("dialog", { name: "전체 PV·직접 경로 권한 주기" });
  expect(dlg).toHaveTextContent("newadm 에게 전체 PV·직접 경로 권한을 줍니다.");
  expect(dlg).toHaveTextContent("이 권한이 있는 관리자만 역할 변경·비활성화·삭제할 수 있고");
  expect(dlg).toHaveTextContent("이 계정의 비밀번호를 아는 사람은 누구나 모든 PV 와 직접 경로를 읽고 쓰고 지울 수 있게 됩니다");
  await userEvent.click(within(dlg).getByRole("button", { name: "자격 주기" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(puts).toEqual([{ u: "newadm", body: { granted: true } }]);
  await waitFor(() => expect(rowOf("newadm")).toHaveTextContent("있음 · 부여"));
  // 누른 자리(주기 버튼)가 빼기 버튼으로 바뀌었다 -- 포커스는 <body> 가 아니라 그 행의 상태 글자(리뷰)
  await waitFor(() => expect(within(rowOf("newadm")).getByText("있음 · 부여")).toHaveFocus());
  // 빼기는 바로(권한을 줄이기만 한다)
  await userEvent.click(within(rowOf("newadm")).getByRole("button", { name: "newadm 전체 PV·직접 경로 권한 빼기" }));
  await waitFor(() => expect(puts[1]).toEqual({ u: "newadm", body: { granted: false } }));
  await waitFor(() => expect(rowOf("newadm")).toHaveTextContent("없음"));
  await waitFor(() => expect(within(rowOf("newadm")).getByText("없음")).toHaveFocus());
});

test("root 자격 없는 관리자는 상태만 본다 -- 버튼 없음 + 누가 줄 수 있는지 안내", async () => {
  stubMeRoot("ops2", false);
  server.use(http.get("/api/admin/accounts", () => HttpResponse.json(ROOT_ROWS)));
  wrap();
  await screen.findByText("newadm", { selector: "td" });
  expect(screen.queryByRole("button", { name: /전체 PV·직접 경로 권한/ })).toBeNull();
  expect(screen.getByText(/이 권한이 있는 관리자만 주거나 뺄 수 있습니다/)).toBeInTheDocument();
});

test("자격 주기 실패(403)는 확인 창 안에서 말하고, 효력 없는 부여는 이유를 보인다", async () => {
  stubMeRoot("admin", true);
  server.use(
    http.get("/api/admin/accounts", () => HttpResponse.json([...ROOT_ROWS,
      { username: "sleepy", role: "admin", email: null, disabled: 1, created_at: "2026-08-05T00:00:00Z",
        root_source: "granted", root_eligible: false }])),
    http.put("/api/admin/accounts/:u/root", () => HttpResponse.json({ detail: "root_grant_privileged_only" }, { status: 403 })),
  );
  wrap();
  await screen.findByText("sleepy", { selector: "td" });
  const status = within(rowOf("sleepy")).getByText("부여됨 · 지금 효력 없음");
  expect(status).toHaveAttribute("title", "비활성 계정이라 효력이 없습니다(다시 켜면 돌아옵니다)");
  await userEvent.click(within(rowOf("newadm")).getByRole("button", { name: "newadm 전체 PV·직접 경로 권한 주기" }));
  const dlg = await screen.findByRole("dialog");
  await userEvent.click(within(dlg).getByRole("button", { name: "자격 주기" }));
  expect(await within(dlg).findByRole("alert")).toHaveTextContent("이미 root 실행 자격이 있는 관리자만");
});

test("배포 설정 이름에 저장된 부여가 겹치면 보이고 뺄 수 있다(env 에서 빠지면 부여로 효력이 남는다)", async () => {
  stubMeRoot("admin", true);
  server.use(http.get("/api/admin/accounts", () => HttpResponse.json([...ROOT_ROWS,
    { username: "cfgadm", role: "admin", email: null, disabled: 0, created_at: "2026-08-05T00:00:00Z",
      root_source: "config", root_eligible: true, root_granted: true }])));
  wrap();
  await screen.findByText("cfgadm", { selector: "td" });
  expect(rowOf("cfgadm")).toHaveTextContent("있음 · 배포 설정 + 부여");
  expect(within(rowOf("cfgadm")).getByRole("button", { name: "cfgadm 전체 PV·직접 경로 권한 빼기" })).toBeInTheDocument();
  expect(within(rowOf("cfgadm")).queryByRole("button", { name: /자격 주기/ })).toBeNull();
});


// ---- dms-k8s 설계 D4·D5 ------------------------------------------------------------------------------------------

test("열·확인 창 문구는 「전체 PV·직접 경로 권한」이다(같은 서버 경로 -- 옛 「root 실행」 문구 없음)", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin", can_run_as_root: true,
                                                       identity_mode: "k8s", unrestricted: true })),
    http.get("/api/admin/accounts", () => HttpResponse.json(ROOT_ROWS)),
  );
  wrap();
  await screen.findByText("newadm", { selector: "td" });
  expect(await screen.findByRole("columnheader", { name: "전체 PV·직접 경로 권한" })).toBeInTheDocument();
  expect(screen.queryByRole("columnheader", { name: "root 실행" })).toBeNull();
  await userEvent.click(within(rowOf("newadm")).getByRole("button", { name: "newadm 전체 PV·직접 경로 권한 주기" }));
  const dlg = await screen.findByRole("dialog", { name: "전체 PV·직접 경로 권한 주기" });
  expect(dlg).toHaveTextContent("newadm 에게 전체 PV·직접 경로 권한을 줍니다.");
  expect(dlg).toHaveTextContent("k8s 권한(namespace 의 rolebinding)과 관계없이 모든 클러스터의 PV 와 직접 경로");
  expect(dlg).toHaveTextContent("이 계정의 비밀번호를 아는 사람은 누구나 모든 PV 와 직접 경로를 읽고 쓰고 지울 수 있게 됩니다");
  expect(dlg).not.toHaveTextContent("root 권한으로 실행");
  expect(screen.getByText(/전체 PV·직접 경로 권한은 admin 역할과 따로입니다/)).toBeInTheDocument();
});

function LocationProbe() {
  const loc = useLocation();
  return <output aria-label="현재 주소">{`${loc.pathname}${loc.search}`}</output>;
}

test("?create=<id>(k8s 연동 「사용자 권한」의 「계정 만들기」): 그 아이디를 채운 생성 창이 열리고 주소에서 create 를 지운다", async () => {
  stubMe("admin");
  server.use(http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/admin/accounts?create=user.yo.hong.example.com"]}>
        <Routes><Route path="/admin/accounts" element={<><AccountsList /><LocationProbe /></>} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const dlg = await screen.findByRole("dialog", { name: "계정 생성" });
  expect(within(dlg).getByLabelText("회사 아이디")).toHaveValue("user.yo.hong.example.com");
  expect(within(dlg).getByLabelText("역할")).toHaveValue("user");
  await waitFor(() => expect(screen.getByLabelText("현재 주소")).toHaveTextContent(/^\/admin\/accounts$/));
  // 닫았다 다시 열면 빈 창(프리셋은 한 번만)
  await userEvent.click(within(dlg).getByRole("button", { name: "취소" }));
  await userEvent.click(screen.getByRole("button", { name: "계정 생성" }));
  expect(within(await screen.findByRole("dialog", { name: "계정 생성" })).getByLabelText("회사 아이디")).toHaveValue("");
});


// ---- 관리자 비밀번호 재설정(2026-11-03) -- 인증번호 전체 잠금 중에도 쓰는 구제 경로 ----------------------------------------

const RESET_ROWS = [
  { username: "admin", role: "admin", email: null, disabled: 0, created_at: "2026-08-05T00:00:00Z" },
  { username: "bob", role: "user", email: "bob@corp.example", disabled: 0, created_at: "2026-08-05T00:00:00Z" },
];

async function openReset(name: string) {
  await screen.findByText(name, { selector: "td" });
  await userEvent.click(within(rowOf(name)).getByRole("button", { name: `${name} 비밀번호 재설정` }));
  return screen.findByRole("dialog", { name: "비밀번호 재설정" });
}

test("비밀번호 재설정: 남의 행에만 버튼이 있다(자기 계정은 셀프 재설정)", async () => {
  stubMe("admin");
  server.use(http.get("/api/admin/accounts", () => HttpResponse.json(RESET_ROWS)));
  wrap();
  await screen.findByText("bob", { selector: "td" });
  expect(within(rowOf("bob")).getByRole("button", { name: "bob 비밀번호 재설정" })).toHaveTextContent("비밀번호 재설정");
  expect(within(rowOf("admin")).queryByRole("button", { name: /비밀번호 재설정/ })).toBeNull();
  // 보안 이벤트 화면 안내(라우터 밖 맨 렌더라 링크 없이 같은 문장)
  expect(screen.getByText(/인증번호 잠금 해제와 로그인 보안 기록은/)).toHaveTextContent(
    "인증번호 잠금 해제와 로그인 보안 기록은 「보안 이벤트」 화면에서 봅니다.");
});

test("비밀번호 재설정: 두 칸이 다르면 「두 비밀번호가 다릅니다」 + 재설정 잠김, 같아지면 열린다", async () => {
  stubMe("admin");
  server.use(http.get("/api/admin/accounts", () => HttpResponse.json(RESET_ROWS)));
  wrap();
  const dlg = await openReset("bob");
  expect(dlg).toHaveTextContent("bob 의 비밀번호를 새로 정합니다. 인증번호 없이 바로 바뀌고, 이 계정으로 로그인해 있던 모든 브라우저는 다시 로그인해야 합니다.");
  expect(dlg).toHaveTextContent("새 비밀번호는 사용자에게 직접 전달하세요.");
  const submit = within(dlg).getByRole("button", { name: "재설정" });
  expect(submit).toBeDisabled();
  const pw = within(dlg).getByLabelText("새 비밀번호");
  const confirm = within(dlg).getByLabelText("새 비밀번호 확인");
  expect(pw).toHaveAttribute("type", "password");
  expect(pw).toHaveAttribute("autocomplete", "new-password");
  await userEvent.type(pw, "n3w-pass");
  expect(within(dlg).queryByText("두 비밀번호가 다릅니다")).toBeNull();   // 확인 칸이 비면 아직 말하지 않는다
  await userEvent.type(confirm, "n3w-pas");
  const msg = within(dlg).getByText("두 비밀번호가 다릅니다");
  expect(confirm).toHaveAttribute("aria-describedby", msg.id);
  expect(confirm).toHaveAttribute("aria-invalid", "true");
  expect(submit).toBeDisabled();
  await userEvent.type(confirm, "s");
  expect(within(dlg).queryByText("두 비밀번호가 다릅니다")).toBeNull();
  expect(submit).toBeEnabled();
});

test.each([
  ["queued", "bob 의 비밀번호를 재설정했습니다 — 변경 안내 메일 발송을 예약했습니다(발송 결과는 보안 이벤트 화면에서 확인)"],
  ["stub", "bob 의 비밀번호를 재설정했습니다 — 메일 발송 방식이 stub 이라 안내 메일은 보내지 않았습니다"],
  ["unavailable", "bob 의 비밀번호를 재설정했습니다 — 메일 설정 오류로 안내 메일은 보내지 않았습니다"],
])("비밀번호 재설정: 봉인해 보내고(평문 password 없음) 결과 안내 메일 %s 를 표 위 결과 줄에 말한다", async (mail, text) => {
  stubMe("admin");
  let captured: Record<string, unknown> | null = null;
  server.use(
    http.get("/api/admin/accounts", () => HttpResponse.json(RESET_ROWS)),
    http.post("/api/admin/accounts/bob/password-reset", async ({ request }) => {
      captured = await request.json() as Record<string, unknown>;
      return HttpResponse.json({ username: "bob", sessions_revoked: true, notice_mail: mail });
    }),
  );
  wrap();
  await screen.findByText("bob", { selector: "td" });
  // 결과 줄은 늘 있는 라이브 리전이다(내용을 가진 채 새로 생긴 리전은 화면낭독기가 흔히 읽지 않는다) -- 처음엔 비어 있다.
  const note = screen.getByRole("status");
  expect(note).toHaveTextContent(/^$/);
  // 긴 목록에서도 보이게 표 **위**에 있다(2026-11-03 리뷰 -- 표 아래면 계정이 수십 개일 때 화면 밖).
  expect(note.compareDocumentPosition(screen.getByRole("table")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const dlg = await openReset("bob");
  await userEvent.type(within(dlg).getByLabelText("새 비밀번호"), "Tmp-2026!");
  await userEvent.type(within(dlg).getByLabelText("새 비밀번호 확인"), "Tmp-2026!");
  await userEvent.click(within(dlg).getByRole("button", { name: "재설정" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByRole("status")).toBe(note);
  expect(note).toHaveTextContent(text);
  // 창이 닫히면 포커스는 트리거가 아니라 결과 줄로 간다(그 자리로 스크롤되고 읽힌다)
  await waitFor(() => expect(note).toHaveFocus());
  const body = captured as unknown as { username: string; password?: string; password_enc: unknown };
  expect(body.username).toBe("bob");
  expect(body.password).toBeUndefined();
  expect(isSealed(body.password_enc)).toBe(true);
  // 용도 admin_password_reset + AAD 사용자 자리 = 대상 아이디(다른 용도·다른 아이디로는 열리지 않는다)
  expect(await openSealed(serverKey, body.password_enc as never, "admin_password_reset", "bob")).toBe("Tmp-2026!");
  await expect(openSealed(serverKey, body.password_enc as never, "admin_create", "bob")).rejects.toThrow();
  await expect(openSealed(serverKey, body.password_enc as never, "admin_password_reset", "admin")).rejects.toThrow();
});

test("비밀번호 재설정: 특권 계정 403 은 창 안 alert 로 남고, 다시 열면 입력·오류가 비워져 있다", async () => {
  stubMe("admin");
  server.use(
    http.get("/api/admin/accounts", () => HttpResponse.json(RESET_ROWS)),
    http.post("/api/admin/accounts/bob/password-reset", () =>
      HttpResponse.json({ detail: "privileged_account_protected" }, { status: 403 })),
  );
  wrap();
  const dlg = await openReset("bob");
  await userEvent.type(within(dlg).getByLabelText("새 비밀번호"), "x");
  await userEvent.type(within(dlg).getByLabelText("새 비밀번호 확인"), "x");
  await userEvent.click(within(dlg).getByRole("button", { name: "재설정" }));
  expect(await within(dlg).findByRole("alert")).toHaveTextContent("특권 실행 목록에 있는 이름의 계정은 특권 실행 자격이 있는 관리자만 관리합니다");
  // 결과 줄은 비어 있다(모달이 뒤 화면을 aria-hidden 으로 가리므로 hidden 까지 찾는다)
  expect(screen.getByRole("status", { hidden: true })).toHaveTextContent(/^$/);
  await userEvent.click(within(dlg).getByRole("button", { name: "취소" }));
  // 실패 뒤 「취소」는 평소대로 트리거로 포커스를 돌려준다(결과 줄은 성공일 때만)
  await waitFor(() => expect(within(rowOf("bob")).getByRole("button", { name: "bob 비밀번호 재설정" })).toHaveFocus());
  const again = await openReset("bob");
  expect(within(again).getByLabelText("새 비밀번호")).toHaveValue("");
  expect(within(again).getByLabelText("새 비밀번호 확인")).toHaveValue("");
  expect(within(again).queryByRole("alert")).toBeNull();
});

test("비밀번호 재설정: 토큰 관리자(403 accounts_session_required)는 세션 관리자 전용 사유를 본다", async () => {
  stubMe("admin");
  server.use(
    http.get("/api/admin/accounts", () => HttpResponse.json(RESET_ROWS)),
    http.post("/api/admin/accounts/bob/password-reset", () =>
      HttpResponse.json({ detail: "accounts_session_required" }, { status: 403 })),
  );
  wrap();
  const dlg = await openReset("bob");
  await userEvent.type(within(dlg).getByLabelText("새 비밀번호"), "x");
  await userEvent.type(within(dlg).getByLabelText("새 비밀번호 확인"), "x");
  await userEvent.click(within(dlg).getByRole("button", { name: "재설정" }));
  expect(await within(dlg).findByRole("alert"))
    .toHaveTextContent("계정 생성·삭제·역할·비활성화·비밀번호 재설정·인증 잠금 해제는 포탈에 로그인한 관리자만 할 수 있습니다(토큰 인증 불가)");
});

test("비밀번호 재설정: 보내는 동안엔 Esc·창 닫기·「취소」로 닫히지 않고(바깥 클릭도 같은 onOpenChange), 응답이 오면 결과를 말한다", async () => {
  // 닫히면 effect 의 reset.reset() 이 진행 중인 mutation 에서 observer 를 떼어 onSuccess(결과 줄)가 사라지는데, 서버는 이미
  // 비밀번호를 바꿨다(2026-11-03 리뷰) -- 관리자가 「취소했다」고 믿지 않게 보내는 동안은 창을 붙잡는다.
  stubMe("admin");
  let release!: () => void;
  const gate = new Promise<void>((r) => { release = r; });
  server.use(
    http.get("/api/admin/accounts", () => HttpResponse.json(RESET_ROWS)),
    http.post("/api/admin/accounts/bob/password-reset", async () => {
      await gate;
      return HttpResponse.json({ username: "bob", sessions_revoked: true, notice_mail: "stub" });
    }),
  );
  wrap();
  const dlg = await openReset("bob");
  await userEvent.type(within(dlg).getByLabelText("새 비밀번호"), "x");
  await userEvent.type(within(dlg).getByLabelText("새 비밀번호 확인"), "x");
  await userEvent.click(within(dlg).getByRole("button", { name: "재설정" }));
  expect(await within(dlg).findByRole("button", { name: "재설정 중…" })).toBeDisabled();
  expect(dlg).toHaveAttribute("aria-busy", "true");
  const cancel = within(dlg).getByRole("button", { name: "취소" });
  expect(cancel).toBeDisabled();
  await userEvent.keyboard("{Escape}");
  await userEvent.click(within(dlg).getByRole("button", { name: "창 닫기" }));
  expect(screen.getByRole("dialog", { name: "비밀번호 재설정" })).toBe(dlg);
  release();
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByRole("status")).toHaveTextContent(
    "bob 의 비밀번호를 재설정했습니다 — 메일 발송 방식이 stub 이라 안내 메일은 보내지 않았습니다");
});

test("비밀번호 재설정: 「재설정」 직후 다시 그리기 전의 Esc·「취소」·두 번째 제출도 막는다 -- 한 번만 보내고 결과를 말한다", async () => {
  // isPending 은 TanStack 알림(setTimeout 0) 뒤 다음 렌더에야 참이다 -- 그 틈에 들어온 Esc·「취소」가 창을 닫아 결과가 사라지고,
  // 두 번째 Enter 가 한 번 더 보냈다(실 Chrome 5회 중 4회 재현, 2026-11-03). 같은 틱의 동기 이벤트로 그 틈을 만든다.
  stubMe("admin");
  let posts = 0;
  let release!: () => void;
  const gate = new Promise<void>((r) => { release = r; });
  server.use(
    http.get("/api/admin/accounts", () => HttpResponse.json(RESET_ROWS)),
    http.post("/api/admin/accounts/bob/password-reset", async () => {
      posts += 1;
      await gate;
      return HttpResponse.json({ username: "bob", sessions_revoked: true, notice_mail: "stub" });
    }),
  );
  wrap();
  const dlg = await openReset("bob");
  await userEvent.type(within(dlg).getByLabelText("새 비밀번호"), "x");
  await userEvent.type(within(dlg).getByLabelText("새 비밀번호 확인"), "x");
  const form = dlg.querySelector("form")!;
  fireEvent.submit(form);
  fireEvent.submit(form);
  fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
  fireEvent.click(within(dlg).getByRole("button", { name: "취소" }));
  fireEvent.click(within(dlg).getByRole("button", { name: "창 닫기" }));
  expect(screen.getByRole("dialog", { name: "비밀번호 재설정" })).toBe(dlg);
  await waitFor(() => expect(posts).toBe(1));
  release();
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(posts).toBe(1);
  expect(screen.getByRole("status")).toHaveTextContent(
    "bob 의 비밀번호를 재설정했습니다 — 메일 발송 방식이 stub 이라 안내 메일은 보내지 않았습니다");
});

test("비밀번호 재설정: 숨은 username 칸이 자격을 대상 아이디에 묶는다(비밀번호 관리자가 관리자 본인 자격을 덮지 않게)", async () => {
  stubMe("admin");
  server.use(http.get("/api/admin/accounts", () => HttpResponse.json(RESET_ROWS)));
  wrap();
  const dlg = await openReset("bob");
  const form = dlg.querySelector("form")!;
  const user = form.querySelector('input[autocomplete="username"]') as HTMLInputElement;
  expect(user).not.toBeNull();
  expect(user.value).toBe("bob");
  expect(user).toHaveAttribute("readonly");
  expect(user).not.toBeVisible();
  // 새 비밀번호 칸보다 앞(브라우저는 비밀번호 칸 앞의 username 칸을 그 자격의 아이디로 읽는다)
  expect(user.compareDocumentPosition(within(dlg).getByLabelText("새 비밀번호")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  // 보이는 입력 칸은 두 비밀번호뿐(숨은 칸은 접근성 트리 밖)
  expect(within(dlg).queryAllByRole("textbox")).toHaveLength(0);
});

test("보안 이벤트 안내는 라우터 안에서 /admin/security 링크다", async () => {
  stubMe("admin");
  server.use(http.get("/api/admin/accounts", () => HttpResponse.json(RESET_ROWS)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/admin/accounts"]}>
        <Routes><Route path="/admin/accounts" element={<AccountsList />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByText("bob", { selector: "td" });
  expect(screen.getByRole("link", { name: "「보안 이벤트」 화면" })).toHaveAttribute("href", "/admin/security");
});
