import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { AccountsList } from "./AccountsList";

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

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
  expect(within(otherRow).getByRole("button")).not.toBeDisabled();
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
  expect(within(dialog).getByText(/아이디@samsung\.com 으로 자동 저장/)).toBeInTheDocument();
  await userEvent.type(within(dialog).getByLabelText("회사 아이디"), "new.user");
  await userEvent.type(within(dialog).getByLabelText("비밀번호"), "pw1");
  await userEvent.selectOptions(within(dialog).getByLabelText("역할"), "admin");
  await userEvent.click(within(dialog).getByRole("button", { name: "생성" }));
  await waitFor(() => expect(captured).toEqual(
    { username: "new.user", password: "pw1", role: "admin" }));
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
