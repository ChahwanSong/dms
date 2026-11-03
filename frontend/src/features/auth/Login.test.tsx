import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { delay, http, HttpResponse } from "msw";
import { beforeAll, beforeEach, afterAll, afterEach, test, expect } from "vitest";
import { Login } from "./Login";
import { forgetTransportKey } from "../../lib/passwordTransport";
import { AUTH_NOTICE_KEY } from "../../lib/api";
import { isSealed, makeServerKey, openSealed, transportKeyHandler, type TestServerKey } from "../../test/transportKey";

const server = setupServer();
// 비밀번호를 보내는 폼은 전부 먼저 서버 키를 받아 봉인한다(2026-09-07) -- 핸들러가
// 없으면 키 조회가 네트워크 오류로 죽어 모든 제출 테스트가 "네트워크" 문구로 빗나간다.
let serverKey: TestServerKey;
beforeAll(async () => { server.listen(); serverKey = await makeServerKey(); });
beforeEach(() => {
  forgetTransportKey();
  // 안내 문구의 도메인은 서버 설정(GET /api/auth/mail-info, 2026-10-01)에서 온다.
  server.use(transportKeyHandler(serverKey),
             http.get("/api/auth/mail-info", () => HttpResponse.json({ email_domain: "samsung.com", delivery: "stub" })));
});
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
                                 expires_in_seconds: 300, stub_code: "123456" });
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
  expect(screen.getByText(/개발용 안내: 인증번호 123456/)).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("인증번호"), "123456");
  await userEvent.type(screen.getByLabelText("비밀번호"), "pw1");
  await userEvent.click(screen.getByRole("button", { name: "계정 생성" }));
  // 성공 시 로그인 탭으로 복귀 + 안내
  expect(await screen.findByText("계정이 생성됐습니다 — 로그인하세요")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "로그인" })).toBeInTheDocument();
  expect(bodies[0]).toEqual({ username: "cocoa.song", purpose: "signup" });
  // 비밀번호는 봉인돼 실린다 -- 평문 password 필드는 와이어에 없다.
  const signupBody = bodies[1] as { username: string; code: string; password?: string; password_enc: unknown };
  expect(signupBody.username).toBe("cocoa.song");
  expect(signupBody.code).toBe("123456");
  expect(signupBody.password).toBeUndefined();
  expect(isSealed(signupBody.password_enc)).toBe(true);
  expect(await openSealed(serverKey, signupBody.password_enc as never, "signup", "cocoa.song")).toBe("pw1");
});

test("로그인 429(아이디 감속)는 한국어 문구 + Retry-After 의 남은 시간으로 보인다", async () => {
  server.use(http.post("/api/auth/login",
    () => HttpResponse.json({ detail: "login_rate_limited" }, { status: 429, headers: { "Retry-After": "42" } })));
  renderLogin();
  await userEvent.type(screen.getByLabelText("사용자명"), "alice");
  await userEvent.type(screen.getByLabelText("비밀번호"), "pw");
  await userEvent.click(screen.getByRole("button", { name: "로그인" }));
  expect(await screen.findByRole("alert"))
    .toHaveTextContent("이 아이디의 로그인 시도가 너무 많습니다 — 잠시 뒤 다시 시도하세요 (다시 시도까지 약 42초)");
});

test("로그인 503 login_busy(과부하)는 문구 + 남은 시간 -- 차단이 아니라 「잠시 뒤」다", async () => {
  server.use(http.post("/api/auth/login",
    () => HttpResponse.json({ detail: "login_busy" }, { status: 503, headers: { "Retry-After": "5" } })));
  renderLogin();
  await userEvent.type(screen.getByLabelText("사용자명"), "alice");
  await userEvent.type(screen.getByLabelText("비밀번호"), "pw");
  await userEvent.click(screen.getByRole("button", { name: "로그인" }));
  expect(await screen.findByRole("alert"))
    .toHaveTextContent("로그인 요청이 몰려 지금은 처리하지 못했습니다 — 잠시 뒤 다시 시도하세요 (다시 시도까지 약 5초)");
  expect(screen.getByRole("button", { name: "로그인" })).toBeEnabled();
});

test("로그인이 2초를 넘기면 대기 안내(role=status)와 「로그인 중…」을 보인다", async () => {
  server.use(http.post("/api/auth/login", async () => {
    await delay(2600);
    return HttpResponse.json({ actor: "alice", role: "user" });
  }));
  renderLogin();
  await userEvent.type(screen.getByLabelText("사용자명"), "alice");
  await userEvent.type(screen.getByLabelText("비밀번호"), "pw");
  await userEvent.click(screen.getByRole("button", { name: "로그인" }));
  expect(await screen.findByRole("button", { name: "로그인 중…" })).toBeDisabled();
  // 2초 전에는 안내가 없다(빠른 로그인마다 깜빡이지 않게)
  expect(screen.queryByText(/순서를 기다리고 있습니다/)).not.toBeInTheDocument();
  expect(await screen.findByRole("status", {}, { timeout: 3000 }))
    .toHaveTextContent("로그인 요청이 많아 순서를 기다리고 있습니다 — 창을 닫지 말고 잠시 기다려 주세요");
}, 10_000);

test("비밀번호가 바뀌어 끝난 세션(session_revoked)이면 로그인 화면이 한 번 알리고 키를 지운다", async () => {
  sessionStorage.setItem(AUTH_NOTICE_KEY, "session_revoked");
  renderLogin();
  expect(screen.getByRole("status")).toHaveTextContent("비밀번호가 바뀌어 로그인이 끝났습니다 — 새 비밀번호로 다시 로그인하세요");
  await waitFor(() => expect(sessionStorage.getItem(AUTH_NOTICE_KEY)).toBeNull());
  // 탭을 바꾸면 사라진다(성공 안내와 같은 규칙)
  await userEvent.click(screen.getByRole("tab", { name: "계정 생성" }));
  expect(screen.queryByText(/비밀번호가 바뀌어 로그인이 끝났습니다/)).not.toBeInTheDocument();
});

test("안내 키가 없거나 다른 값이면 세션 안내는 없다", async () => {
  sessionStorage.setItem(AUTH_NOTICE_KEY, "something_else");
  renderLogin();
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  sessionStorage.removeItem(AUTH_NOTICE_KEY);
});

test("인증번호는 6자리 숫자만 -- 숫자 아닌 글자는 지우고, 6자리 전에는 제출 버튼이 잠긴다", async () => {
  server.use(http.post("/api/auth/verification-codes", () =>
    HttpResponse.json({ email: "a.b@samsung.com", expires_in_seconds: 300 })));
  renderLogin();
  await userEvent.click(screen.getByRole("tab", { name: "계정 생성" }));
  expect(screen.getByText("(숫자 6자리)")).toBeInTheDocument();
  const code = screen.getByLabelText("인증번호");
  // 자릿수는 보이는 라벨에만 있으면 aria-label 에 덮여 화면낭독기가 못 듣는다 -- 설명으로 잇는다(2026-11-03 리뷰).
  expect(code).toHaveAccessibleName("인증번호");
  expect(code).toHaveAccessibleDescription("(숫자 6자리)");
  expect(code).toHaveAttribute("inputmode", "numeric");
  expect(code).toHaveAttribute("autocomplete", "one-time-code");
  expect(code).toHaveAttribute("maxlength", "6");
  await userEvent.type(screen.getByLabelText("회사 아이디"), "a.b");
  await userEvent.type(screen.getByLabelText("비밀번호"), "pw");
  await userEvent.type(code, "12a3-4b5");
  expect(code).toHaveValue("12345");
  const submit = screen.getByRole("button", { name: "계정 생성" });
  expect(submit).toBeDisabled();
  await userEvent.type(code, "6");
  expect(code).toHaveValue("123456");
  expect(submit).toBeEnabled();
});

test("인증번호 붙여넣기: 메일의 「123 456」도 숫자 6자리로 들어간다(maxLength 에 먼저 잘리지 않음)", async () => {
  renderLogin();
  await userEvent.click(screen.getByRole("tab", { name: "비밀번호 변경" }));
  const code = screen.getByLabelText("인증번호");
  await userEvent.click(code);
  await userEvent.paste("123 456");
  expect(code).toHaveValue("123456");
  await userEvent.clear(code);
  await userEvent.paste(" 98-76-54-32 ");
  expect(code).toHaveValue("987654");
});

test("인증번호 전체 오답 상한(429 verification_global_locked)은 관리자 구제 안내 + 남은 시간으로 보인다", async () => {
  server.use(http.post("/api/auth/verification-codes", () =>
    HttpResponse.json({ detail: "verification_global_locked" }, { status: 429, headers: { "Retry-After": "1800" } })));
  renderLogin();
  await userEvent.click(screen.getByRole("tab", { name: "비밀번호 변경" }));
  await userEvent.type(screen.getByLabelText("회사 아이디"), "a.b");
  await userEvent.click(screen.getByRole("button", { name: "인증번호 받기" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/인증번호 오답이 전체적으로 너무 많아 계정 생성·비밀번호 변경이 잠시 중단됐습니다/);
  expect(alert).toHaveTextContent(/관리자는 계정 화면에서 바로 계정을 만들거나 비밀번호를 재설정할 수 있습니다/);
  expect(alert).toHaveTextContent(/\(다시 시도까지 약 30분\)$/);
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
      HttpResponse.json({ email: "a.b@samsung.com", expires_in_seconds: 300, stub_code: "123456" })),
    http.post("/api/auth/password-reset", () =>
      HttpResponse.json({ detail: "verification_invalid" }, { status: 422 })),
  );
  renderLogin();
  await userEvent.click(screen.getByRole("tab", { name: "비밀번호 변경" }));
  await userEvent.type(screen.getByLabelText("회사 아이디"), "a.b");
  await userEvent.click(screen.getByRole("button", { name: "인증번호 받기" }));
  await screen.findByText(/인증번호를 보냈습니다/);
  await userEvent.type(screen.getByLabelText("인증번호"), "999999");
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

test("인증번호 안내의 도메인은 서버 설정을 따른다(하드코딩 아님) -- Knox 메일이면 개발용 코드 안내가 없다", async () => {
  server.use(
    http.get("/api/auth/mail-info", () => HttpResponse.json({ email_domain: "corp.example", delivery: "knox_relay" })),
    http.post("/api/auth/verification-codes", () =>
      HttpResponse.json({ email: "lee.k@corp.example", expires_in_seconds: 300 })));
  renderLogin();
  await userEvent.click(screen.getByRole("tab", { name: "계정 생성" }));
  await userEvent.type(screen.getByLabelText("회사 아이디"), "lee.k");
  expect(await screen.findByText(/lee\.k@corp\.example 로 전송됩니다/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "인증번호 받기" }));
  expect(await screen.findByText(/lee\.k@corp\.example 로 인증번호를 보냈습니다/)).toBeInTheDocument();
  expect(screen.queryByText(/개발용 안내/)).not.toBeInTheDocument();
});

test("발송 확인이 늦으면(delivery_uncertain) 그렇게 알리고 인증번호 입력은 그대로", async () => {
  server.use(http.post("/api/auth/verification-codes", () =>
    HttpResponse.json({ email: "a.b@samsung.com", expires_in_seconds: 300, delivery_uncertain: true })));
  renderLogin();
  await userEvent.click(screen.getByRole("tab", { name: "계정 생성" }));
  await userEvent.type(screen.getByLabelText("회사 아이디"), "a.b");
  await userEvent.click(screen.getByRole("button", { name: "인증번호 받기" }));
  expect(await screen.findByText(/발송 확인이 늦어지고 있습니다/)).toBeInTheDocument();
  expect(screen.getByLabelText("인증번호")).toBeEnabled();
});

test("누적 실패 잠금은 429 verification_locked 의 한국어 사유로 보인다", async () => {
  server.use(http.post("/api/auth/verification-codes", () =>
    HttpResponse.json({ detail: "verification_locked" }, { status: 429, headers: { "Retry-After": "86000" } })));
  renderLogin();
  await userEvent.click(screen.getByRole("tab", { name: "계정 생성" }));
  await userEvent.type(screen.getByLabelText("회사 아이디"), "a.b");
  await userEvent.click(screen.getByRole("button", { name: "인증번호 받기" }));
  expect(await screen.findByText(/인증번호를 여러 번 틀려 이 아이디의 인증이 잠겼습니다/)).toBeInTheDocument();
  // 남은 시간은 Retry-After(86000초 → 약 24시간)로 말하고, 관리자 해제 길도 알린다(2026-11-03)
  expect(screen.getByRole("alert")).toHaveTextContent(/관리자에게 잠금 해제를 요청하세요.*\(다시 시도까지 약 24시간\)/);
});

test("인증 메일 실패·상한은 한국어 사유로 보인다", async () => {
  server.use(http.post("/api/auth/verification-codes", () =>
    HttpResponse.json({ detail: "verification_rate_limited" }, { status: 429, headers: { "Retry-After": "120" } })));
  renderLogin();
  await userEvent.click(screen.getByRole("tab", { name: "계정 생성" }));
  await userEvent.type(screen.getByLabelText("회사 아이디"), "a.b");
  await userEvent.click(screen.getByRole("button", { name: "인증번호 받기" }));
  expect(await screen.findByText(/인증 메일 요청이 너무 잦거나 발송이 몰렸습니다/)).toBeInTheDocument();
  server.use(http.post("/api/auth/verification-codes", () =>
    HttpResponse.json({ detail: "verification_email_failed" }, { status: 502 })));
  await userEvent.click(screen.getByRole("button", { name: "인증번호 받기" }));
  expect(await screen.findByText(/인증 메일을 보내지 못했습니다/)).toBeInTheDocument();
});


test("로그인 화면 브랜드에 서브네임이 붙는다(AI Storage Portal - SSC)", async () => {
  server.use(http.get("/api/portal-info", () => HttpResponse.json({ subtitle: "SSC" })));
  renderLogin();
  expect(await screen.findByText("AI Storage Portal - SSC")).toBeInTheDocument();
  await waitFor(() => expect(document.title).toBe("AI Storage Portal - SSC"));
});
