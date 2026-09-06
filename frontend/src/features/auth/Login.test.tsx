import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, beforeEach, afterAll, afterEach, test, expect } from "vitest";
import { Login } from "./Login";
import { forgetTransportKey } from "../../lib/passwordTransport";
import { isSealed, makeServerKey, openSealed, transportKeyHandler, type TestServerKey } from "../../test/transportKey";

const server = setupServer();
// 비밀번호를 보내는 폼은 전부 먼저 서버 키를 받아 봉인한다(2026-09-07) -- 핸들러가
// 없으면 키 조회가 네트워크 오류로 죽어 모든 제출 테스트가 "네트워크" 문구로 빗나간다.
let serverKey: TestServerKey;
beforeAll(async () => { server.listen(); serverKey = await makeServerKey(); });
beforeEach(() => { forgetTransportKey(); server.use(transportKeyHandler(serverKey)); });
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderLogin() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><Login /></MemoryRouter>
    </QueryClientProvider>,
  );
}

test("submits credentials and shows error on 401", async () => {
  server.use(http.post("/api/auth/login",
    () => HttpResponse.json({ detail: "invalid_credentials" }, { status: 401 })));
  renderLogin();
  await userEvent.type(screen.getByLabelText("사용자명"), "alice");
  await userEvent.type(screen.getByLabelText("비밀번호"), "bad");
  await userEvent.click(screen.getByRole("button", { name: "로그인" }));
  expect(await screen.findByText("사용자명 또는 비밀번호가 올바르지 않습니다")).toBeInTheDocument();
});

test("네트워크 단절은 일반 한국어 문구를 보인다 -- 영어 원문 노출 금지", async () => {
  // fetch 는 네트워크 단절 시 TypeError("Failed to fetch") 로 reject 된다 --
  // ApiError 무가드 캐스트는 그 영어 원문을 사용자에게 그대로 노출했다.
  server.use(http.post("/api/auth/login", () => HttpResponse.error()));
  renderLogin();
  await userEvent.type(screen.getByLabelText("사용자명"), "alice");
  await userEvent.type(screen.getByLabelText("비밀번호"), "pw");
  await userEvent.click(screen.getByRole("button", { name: "로그인" }));
  expect(await screen.findByText("로그인 요청에 실패했습니다 — 네트워크 상태를 확인하세요"))
    .toBeInTheDocument();
  expect(screen.queryByText(/Failed to fetch/i)).not.toBeInTheDocument();
});

test("타이틀은 「로그인」이고 탭으로 계정 생성·비밀번호 변경을 오간다", async () => {
  renderLogin();
  // 구 "DMS 로그인" 아님(사용자 결정 2026-08-20)
  expect(screen.getByRole("heading", { name: "로그인" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: "계정 생성" }));
  expect(screen.getByRole("heading", { name: "계정 생성" })).toBeInTheDocument();
  expect(screen.getByLabelText("회사 아이디")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: "비밀번호 변경" }));
  expect(screen.getByRole("heading", { name: "비밀번호 변경" })).toBeInTheDocument();
  expect(screen.getByLabelText("새 비밀번호")).toBeInTheDocument();
});

test("계정 생성 흐름: 인증번호 발급(stub 에코 안내) → 생성 → 로그인 탭 복귀", async () => {
  const bodies: unknown[] = [];
  server.use(
    http.post("/api/auth/verification-codes", async ({ request }) => {
      bodies.push(await request.json());
      return HttpResponse.json({ email: "cocoa.song@samsung.com",
                                 expires_in_seconds: 300, stub_code: "1234" });
    }),
    http.post("/api/auth/signup", async ({ request }) => {
      bodies.push(await request.json());
      return HttpResponse.json({ username: "cocoa.song" }, { status: 201 });
    }),
  );
  renderLogin();
  await userEvent.click(screen.getByRole("tab", { name: "계정 생성" }));
  await userEvent.type(screen.getByLabelText("회사 아이디"), "cocoa.song");
  // 파생 이메일을 입력 중에도 미리 보여준다
  expect(screen.getByText(/cocoa\.song@samsung\.com 로 전송됩니다/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "인증번호 받기" }));
  expect(await screen.findByText(/인증번호를 보냈습니다 \(유효 5분\)/)).toBeInTheDocument();
  // stub 메일러 안내(사내 메일 연동 전 임시)
  expect(screen.getByText(/개발용 안내: 인증번호 1234/)).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("인증번호"), "1234");
  await userEvent.type(screen.getByLabelText("비밀번호"), "pw1");
  await userEvent.click(screen.getByRole("button", { name: "계정 생성" }));
  // 성공 시 로그인 탭으로 복귀 + 안내
  expect(await screen.findByText("계정이 생성됐습니다 — 로그인하세요")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "로그인" })).toBeInTheDocument();
  expect(bodies[0]).toEqual({ username: "cocoa.song", purpose: "signup" });
  // 비밀번호는 봉인돼 실린다 -- 평문 password 필드는 와이어에 없다.
  const signupBody = bodies[1] as { username: string; code: string; password?: string; password_enc: unknown };
  expect(signupBody.username).toBe("cocoa.song");
  expect(signupBody.code).toBe("1234");
  expect(signupBody.password).toBeUndefined();
  expect(isSealed(signupBody.password_enc)).toBe(true);
  expect(await openSealed(serverKey, signupBody.password_enc as never, "signup", "cocoa.song")).toBe("pw1");
});

test("로그인 429(감속)는 한국어 문구로 보인다", async () => {
  server.use(http.post("/api/auth/login",
    () => HttpResponse.json({ detail: "login_rate_limited" }, { status: 429, headers: { "Retry-After": "42" } })));
  renderLogin();
  await userEvent.type(screen.getByLabelText("사용자명"), "alice");
  await userEvent.type(screen.getByLabelText("비밀번호"), "pw");
  await userEvent.click(screen.getByRole("button", { name: "로그인" }));
  expect(await screen.findByText("로그인 시도가 너무 많습니다 — 1분 뒤 다시 시도하세요")).toBeInTheDocument();
});

test("로그인 본문은 봉인된 비밀번호를 싣는다", async () => {
  let body: { username: string; password?: string; password_enc: unknown } | null = null;
  server.use(http.post("/api/auth/login", async ({ request }) => {
    body = await request.json() as typeof body;
    return HttpResponse.json({ actor: "alice", role: "user" });
  }));
  renderLogin();
  await userEvent.type(screen.getByLabelText("사용자명"), "alice");
  await userEvent.type(screen.getByLabelText("비밀번호"), "s3cret");
  await userEvent.click(screen.getByRole("button", { name: "로그인" }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body!.password).toBeUndefined();
  expect(await openSealed(serverKey, body!.password_enc as never, "login", "alice")).toBe("s3cret");
});

test("비밀번호 변경: 잘못된 인증번호는 한국어 사유로 거부된다", async () => {
  server.use(
    http.post("/api/auth/verification-codes", () =>
      HttpResponse.json({ email: "a.b@samsung.com", expires_in_seconds: 300, stub_code: "1234" })),
    http.post("/api/auth/password-reset", () =>
      HttpResponse.json({ detail: "verification_invalid" }, { status: 422 })),
  );
  renderLogin();
  await userEvent.click(screen.getByRole("tab", { name: "비밀번호 변경" }));
  await userEvent.type(screen.getByLabelText("회사 아이디"), "a.b");
  await userEvent.click(screen.getByRole("button", { name: "인증번호 받기" }));
  await screen.findByText(/인증번호를 보냈습니다/);
  await userEvent.type(screen.getByLabelText("인증번호"), "9999");
  await userEvent.type(screen.getByLabelText("새 비밀번호"), "new");
  await userEvent.click(screen.getByRole("button", { name: "비밀번호 변경" }));
  expect(await screen.findByText("인증번호가 일치하지 않습니다")).toBeInTheDocument();
});

test("navigates to / on successful login", async () => {
  server.use(http.post("/api/auth/login",
    () => HttpResponse.json({ actor: "alice", role: "user" })));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/login"]}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<h1>홈</h1>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await userEvent.type(screen.getByLabelText("사용자명"), "alice");
  await userEvent.type(screen.getByLabelText("비밀번호"), "good");
  await userEvent.click(screen.getByRole("button", { name: "로그인" }));
  expect(await screen.findByRole("heading", { name: "홈" })).toBeInTheDocument();
});
