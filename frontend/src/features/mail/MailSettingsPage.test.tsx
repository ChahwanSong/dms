import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, beforeEach, afterAll, afterEach, test, expect } from "vitest";
import { MailSettingsPage, mailResultText } from "./MailSettingsPage";
import { forgetTransportKey } from "../../lib/passwordTransport";
import { isSealed, makeServerKey, openSealed, transportKeyHandler, type TestServerKey } from "../../test/transportKey";
import type { MailSettings } from "../../lib/types";

// 포탈 메일 설정(2026-10-01): 칸별 출처·placeholder(비우면 env), 저장 바디(빈 칸 = null), 인증 키는 봉인으로만
// 실리고 평문이 없다, 연결 확인·테스트 메일 결과의 사유별 조치 문구. 리뷰 보강: 조회 실패 화면, 키 지우기는
// 키만(편집 중 칸 보존), 주소 변경 시 키 재입력, stub 전환 확인, env 키 미사용 안내.

let serverKey: TestServerKey;
const server = setupServer();
beforeAll(async () => { server.listen(); serverKey = await makeServerKey(); });
beforeEach(() => {
  forgetTransportKey();
  server.use(transportKeyHandler(serverKey),
             http.get("/api/auth/me", () => HttpResponse.json({ actor: "mason", role: "admin" })));
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const noToken = { configured: false, source: null, unreadable: false, env_configured: false, env_unbound: false };
const base: MailSettings = {
  backend: "stub", relay_scheme: "http", relay_host: "", relay_port: 8025, relay_url: "",
  timeout_seconds: 20, service_name: "Supercom 포털",
  sources: { backend: "env", relay_scheme: "env", relay_host: "env", relay_port: "env",
             timeout_seconds: "env", service_name: "env" },
  portal: { backend: null, relay_scheme: null, relay_host: null, relay_port: null,
            timeout_seconds: null, service_name: null },
  env: { backend: "stub", relay_scheme: "http", relay_host: null, relay_port: 8025,
         timeout_seconds: 20, service_name: "Supercom 포털", relay_url: "" },
  token: noToken, endpoint_source: "env",
  email_domain: "samsung.com", backends: ["stub", "knox_relay"], updated_at: null, updated_by: null,
};
// 포탈에서 knox_relay + 주소 + 키까지 저장한 운영 모양.
const knox: MailSettings = {
  ...base, backend: "knox_relay", relay_host: "10.20.30.40", relay_url: "http://10.20.30.40:8025",
  sources: { ...base.sources, backend: "portal", relay_host: "portal" },
  portal: { ...base.portal, backend: "knox_relay", relay_host: "10.20.30.40" },
  token: { ...noToken, configured: true, source: "portal" }, endpoint_source: "portal",
  updated_at: "2026-10-01T00:00:00Z", updated_by: "mason",
};

function wrap(settings: MailSettings = base) {
  server.use(http.get("/api/admin/mail-settings", () => HttpResponse.json(settings)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MailSettingsPage /></QueryClientProvider>);
  return qc;
}

test("env 만 있는 첫 화면: 현재 적용값·출처·placeholder(비우면 이 값)·인증 키 미설정·stub 경고", async () => {
  wrap();
  expect(await screen.findByRole("heading", { name: "메일 설정" })).toBeInTheDocument();
  expect(screen.getByText(/개발용\(stub\) — 메일을 보내지/, { selector: "p" })).toBeInTheDocument();
  expect(screen.getByRole("alert", { name: "개발용 발송 경고" })).toHaveTextContent("운영 환경에서는 쓰지 마세요");
  expect(screen.getByLabelText("릴레이 포트")).toHaveAttribute("placeholder", "비우면 8025");
  expect(screen.getByLabelText("타임아웃(초)")).toHaveAttribute("placeholder", "비우면 20");
  expect(screen.getByLabelText("서비스명")).toHaveAttribute("placeholder", "비우면 Supercom 포털");
  expect(screen.getAllByText("환경변수·기본값").length).toBeGreaterThanOrEqual(6);
  expect(screen.getByRole("status", { name: "인증 키 상태" })).toHaveTextContent("미설정");
  expect(screen.getByLabelText("테스트 메일 받는 사람")).toHaveValue("mason@samsung.com");
});

test("조회 실패는 '불러오는 중'에 갇히지 않고 오류를 보여 준다", async () => {
  server.use(http.get("/api/admin/mail-settings", () =>
    HttpResponse.json({ detail: "admin_required" }, { status: 403 })));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MailSettingsPage /></QueryClientProvider>);
  expect(await screen.findByRole("alert")).not.toHaveTextContent("");
  expect(screen.queryByText("불러오는 중…")).not.toBeInTheDocument();
});

test("저장: 바꾼 칸만 보낸다 -- 인증 키는 봉인(mail_relay_token/mail_settings)으로만 실린다", async () => {
  let sent: Record<string, unknown> | null = null;
  server.use(http.put("/api/admin/mail-settings", async ({ request }) => {
    sent = await request.json() as Record<string, unknown>;
    return HttpResponse.json(knox);
  }));
  wrap();
  await userEvent.selectOptions(await screen.findByLabelText("발송 방식"), "knox_relay");
  await userEvent.type(screen.getByLabelText("릴레이 서버 IP"), "10.20.30.40");
  await userEvent.type(screen.getByLabelText("새 인증 키"), "s3cret-token");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await waitFor(() => expect(sent).not.toBeNull());
  const body = sent!;
  expect(body.backend).toBe("knox_relay");
  expect(body.relay_host).toBe("10.20.30.40");
  // 손대지 않은 칸은 싣지 않는다(다른 관리자의 변경을 이 화면의 옛 값으로 되돌리지 않게)
  for (const k of ["relay_scheme", "relay_port", "timeout_seconds", "service_name"]) expect(k in body).toBe(false);
  expect("relay_token" in body).toBe(false);
  expect("clear_relay_token" in body).toBe(false);
  expect(JSON.stringify(body)).not.toContain("s3cret-token");
  expect(body.seen_relay_url).toBe("");                 // 키를 실을 때 화면이 보던 적용 주소(그 사이 바뀌면 서버 409)
  expect(isSealed(body.relay_token_enc)).toBe(true);
  expect(await openSealed(serverKey, body.relay_token_enc as never, "mail_relay_token", "mail_settings"))
    .toBe("s3cret-token");
  expect(await screen.findByText("저장했습니다")).toBeInTheDocument();
  expect(screen.getByLabelText("새 인증 키")).toHaveValue("");             // 저장 뒤 입력칸을 비운다
  expect(screen.getByRole("status", { name: "인증 키 상태" })).toHaveTextContent("포탈에 암호화해 저장");
  expect(screen.queryByRole("alert", { name: "개발용 발송 경고" })).not.toBeInTheDocument();
});

test("포탈 키 지우기: 확인 한 번 더, clear_relay_token 만 보내고 편집 중인 칸은 저장도 덮어쓰기도 안 한다", async () => {
  let sent: Record<string, unknown> | null = null;
  server.use(http.put("/api/admin/mail-settings", async ({ request }) => {
    sent = await request.json() as Record<string, unknown>;
    return HttpResponse.json({ ...knox, token: noToken, updated_at: "2026-10-01T01:00:00Z" });
  }));
  wrap({ ...knox, token: { ...noToken, source: "portal", unreadable: true, env_configured: true } });
  expect(await screen.findByRole("status", { name: "인증 키 상태" })).toHaveTextContent("읽을 수 없습니다");
  await userEvent.type(screen.getByLabelText("서비스명"), "편집 중");
  await userEvent.click(screen.getByRole("button", { name: "포탈에 저장된 인증 키 지우기" }));
  // 포탈이 주소를 정했으니 env 키로 돌아가지 않는다 -- 그렇게 안내한다.
  expect(screen.getByText(/지우면 인증 키가 없어 Knox 메일 발송이 실패합니다/)).toBeInTheDocument();
  expect(sent).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "지우기 확인" }));
  await waitFor(() => expect(sent).not.toBeNull());
  expect(sent).toEqual({ clear_relay_token: true });
  expect(await screen.findByText("포탈 인증 키를 지웠습니다")).toBeInTheDocument();
  expect(screen.getByLabelText("서비스명")).toHaveValue("편집 중");
});

test("env 주소일 때 키 지우기는 환경변수 키로 돌아간다고 안내", async () => {
  wrap({ ...base, backend: "knox_relay", relay_url: "http://10.0.0.5:8025",
         token: { ...noToken, configured: true, source: "portal", env_configured: true } });
  await userEvent.click(await screen.findByRole("button", { name: "포탈에 저장된 인증 키 지우기" }));
  expect(screen.getByText(/환경변수 키\(DMS_MAIL_RELAY_TOKEN\)로 돌아갑니다/)).toBeInTheDocument();
});

test("포탈 키가 있으면 릴레이 주소를 바꿀 때 키를 다시 넣어야 저장된다(서버 mail_relay_token_required 와 같은 규칙)", async () => {
  wrap(knox);
  const host = await screen.findByLabelText("릴레이 서버 IP");
  await userEvent.clear(host);
  await userEvent.type(host, "10.99.99.99");
  expect(screen.getByRole("alert")).toHaveTextContent("인증 키를 다시 입력해야 합니다");
  expect(host).not.toHaveAttribute("aria-invalid", "true");
  expect(screen.getByLabelText("새 인증 키")).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();
  await userEvent.type(screen.getByLabelText("새 인증 키"), "new-key");
  expect(screen.getByRole("button", { name: "저장" })).toBeEnabled();
  // 주소 외 칸(서비스명)만 바꾸면 키 없이 저장된다.
  await userEvent.clear(screen.getByLabelText("새 인증 키"));
  await userEvent.clear(host);
  await userEvent.type(host, "10.20.30.40");
  await userEvent.type(screen.getByLabelText("서비스명"), "DMS");
  expect(screen.getByRole("button", { name: "저장" })).toBeEnabled();
});

test("knox_relay → stub 전환은 위험 확인 체크 뒤에만 저장된다", async () => {
  wrap(knox);
  await userEvent.selectOptions(await screen.findByLabelText("발송 방식"), "stub");
  expect(screen.getByText(/아이디만 알면 누구나 가입·비밀번호 재설정/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();
  await userEvent.click(screen.getByRole("checkbox"));
  expect(screen.getByRole("button", { name: "저장" })).toBeEnabled();
  // 환경변수 따름(env=stub)도 결과가 stub 이면 같은 확인이 필요하다.
  await userEvent.selectOptions(screen.getByLabelText("발송 방식"), "");
  expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();
});

test("env 키가 있지만 포탈 주소라 쓰지 않으면 그렇게 알린다", async () => {
  wrap({ ...knox, token: { ...noToken, env_configured: true, env_unbound: true } });
  expect(await screen.findByRole("status", { name: "인증 키 상태" }))
    .toHaveTextContent("환경변수 주소(DMS_MAIL_RELAY_URL)에만 쓰입니다");
});

test("잘못된 포트·타임아웃은 저장을 막고 칸에 오류를 잇는다", async () => {
  wrap();
  await userEvent.type(await screen.findByLabelText("릴레이 포트"), "70000");
  expect(screen.getByText("포트는 1~65535 사이의 정수여야 합니다")).toBeInTheDocument();
  expect(screen.getByLabelText("릴레이 포트")).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByLabelText("릴레이 포트")).toHaveAccessibleDescription("포트는 1~65535 사이의 정수여야 합니다");
  expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();
  await userEvent.clear(screen.getByLabelText("릴레이 포트"));
  await userEvent.type(screen.getByLabelText("타임아웃(초)"), "0");
  expect(screen.getByText("타임아웃은 1~120 사이여야 합니다")).toBeInTheDocument();
  expect(screen.getByLabelText("타임아웃(초)")).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();
});

test("연결 확인·테스트 메일: 성공 문구와 사유별 조치 안내, 저장 안 한 변경 경고", async () => {
  server.use(
    http.post("/api/admin/mail-settings/health-check", () =>
      HttpResponse.json({ ok: false, reason: "relay_unreachable", detail: "Connection refused", relay_url: "http://10.0.0.9:8025" })),
    http.post("/api/admin/mail-settings/test-mail", async ({ request }) => {
      const { to } = await request.json() as { to: string };
      return HttpResponse.json({ ok: true, to });
    }));
  wrap(knox);
  await userEvent.click(await screen.findByRole("button", { name: "연결 확인(/healthz)" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("systemctl status knox-mail-dms-certi");
  expect(alert).toHaveTextContent("상세: Connection refused");
  await userEvent.click(screen.getByRole("button", { name: "테스트 메일 보내기" }));
  expect(await screen.findByText(/테스트 메일을 보냈습니다 — mason@samsung.com/)).toBeInTheDocument();
  expect(screen.queryByText(/저장하지 않은 변경이 있습니다/)).not.toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("서비스명"), "X");
  expect(screen.getByText(/저장하지 않은 변경이 있습니다 — 아래 점검은 저장된 값\(http:\/\/10.20.30.40:8025\)/))
    .toBeInTheDocument();
});

test("칸을 비우면 null 로 보내 env·기본값으로 돌아간다, 바뀐 것이 없으면 저장이 꺼져 있다", async () => {
  let sent: Record<string, unknown> | null = null;
  server.use(http.put("/api/admin/mail-settings", async ({ request }) => {
    sent = await request.json() as Record<string, unknown>;
    return HttpResponse.json(knox);
  }));
  wrap({ ...knox, portal: { ...knox.portal, service_name: "옛 이름" } });
  expect(await screen.findByRole("button", { name: "저장" })).toBeDisabled();
  await userEvent.clear(screen.getByLabelText("서비스명"));
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await waitFor(() => expect(sent).not.toBeNull());
  expect(sent).toEqual({ service_name: null });
});

test("다른 관리자의 변경: 손대지 않은 칸은 새 서버 값을 따르고, 편집 중인 칸만 남는다", async () => {
  const qc = wrap(knox);
  await userEvent.type(await screen.findByLabelText("타임아웃(초)"), "30");
  // 그 사이 다른 관리자가 서비스명을 바꿔 저장했다
  server.use(http.get("/api/admin/mail-settings", () => HttpResponse.json({
    ...knox, service_name: "새 이름", portal: { ...knox.portal, service_name: "새 이름" },
    sources: { ...knox.sources, service_name: "portal" }, updated_at: "2026-10-01T02:00:00Z" })));
  await qc.invalidateQueries({ queryKey: ["mail-settings"] });
  await waitFor(() => expect(screen.getByLabelText("서비스명")).toHaveValue("새 이름"));
  expect(screen.getByLabelText("타임아웃(초)")).toHaveValue("30");
  expect(screen.getByLabelText("릴레이 서버 IP")).toHaveValue("10.20.30.40");
  expect(screen.queryByText(/인증 키를 다시 입력해야 합니다/)).not.toBeInTheDocument();
});

test("재조회 실패는 폼과 입력을 그대로 두고 위에 알린다", async () => {
  const qc = wrap(knox);
  await userEvent.type(await screen.findByLabelText("서비스명"), "편집 중");
  server.use(http.get("/api/admin/mail-settings", () => HttpResponse.json({ detail: "x" }, { status: 500 })));
  await qc.invalidateQueries({ queryKey: ["mail-settings"] });
  expect(await screen.findByText(/설정을 다시 불러오지 못했습니다/)).toBeInTheDocument();
  expect(screen.getByLabelText("서비스명")).toHaveValue("편집 중");
});

test("mailResultText: 알려진 사유·릴레이 HTTP 코드·리다이렉트·상한 재시도 초", () => {
  expect(mailResultText({ ok: false, reason: "unauthorized" })).toContain("RELAY_TOKEN 과 다릅니다");
  expect(mailResultText({ ok: false, reason: "client_not_allowed" })).toContain("RELAY_ALLOWED_CLIENTS");
  expect(mailResultText({ ok: false, reason: "relay_http_500" })).toContain("HTTP 500");
  expect(mailResultText({ ok: false, reason: "relay_http_302" })).toContain("따라가지 않습니다");
  expect(mailResultText({ ok: false, reason: "not_found" })).toContain("포트가 릴레이");
  expect(mailResultText({ ok: false, reason: "relay_no_response" })).toContain("메일이 갔을 수도 있습니다");
  expect(mailResultText({ ok: false, reason: "bad_request", detail: "to must be" })).toContain("상세: to must be");
  expect(mailResultText({ ok: false, reason: "rate_limited", retry_after: 42 })).toContain("42초 뒤 가능");
  expect(mailResultText({ ok: true })).toBe("");
});


test("포트를 비워도 적용 주소가 같으면(기본 8025) 키 없이 저장된다 -- 서버와 같은 '적용 주소' 비교", async () => {
  wrap({ ...knox, portal: { ...knox.portal, relay_port: 8025 }, sources: { ...knox.sources, relay_port: "portal" } });
  await userEvent.clear(await screen.findByLabelText("릴레이 포트"));
  expect(screen.queryByText(/인증 키를 다시 입력해야 합니다/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "저장" })).toBeEnabled();
  // 포트를 실제로 바꾸면 키가 필요하다
  await userEvent.type(screen.getByLabelText("릴레이 포트"), "9025");
  expect(screen.getByText(/인증 키를 다시 입력해야 합니다/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();
});

test("저장 응답을 기다리는 동안 고친 칸은 응답이 덮어쓰지 않는다", async () => {
  let release: () => void = () => {};
  const gate = new Promise<void>((r) => { release = r; });
  server.use(http.put("/api/admin/mail-settings", async () => {
    await gate;
    return HttpResponse.json({ ...knox, timeout_seconds: 30, portal: { ...knox.portal, timeout_seconds: 30 } });
  }));
  wrap(knox);
  await userEvent.type(await screen.findByLabelText("타임아웃(초)"), "30");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await userEvent.type(screen.getByLabelText("서비스명"), "새 이름");      // 저장 중 입력
  release();
  expect(await screen.findByText("저장했습니다")).toBeInTheDocument();
  expect(screen.getByLabelText("서비스명")).toHaveValue("새 이름");
  expect(screen.getByLabelText("타임아웃(초)")).toHaveValue("30");
});

test("다른 관리자가 주소를 바꿨으면(409 mail_settings_changed) 알리고 설정을 다시 불러온다", async () => {
  let gets = 0;
  server.use(
    http.get("/api/admin/mail-settings", () => { gets += 1; return HttpResponse.json(knox); }),
    http.put("/api/admin/mail-settings", () =>
      HttpResponse.json({ detail: "mail_settings_changed" }, { status: 409 })));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MailSettingsPage /></QueryClientProvider>);
  await userEvent.type(await screen.findByLabelText("새 인증 키"), "real-key");
  const before = gets;
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(await screen.findByText(/다른 관리자가 릴레이 주소를 바꿔 설정을 다시 불러왔습니다/)).toBeInTheDocument();
  await waitFor(() => expect(gets).toBeGreaterThan(before));
  expect(screen.getByLabelText("새 인증 키")).toHaveValue("real-key");     // 입력한 키는 남는다(다시 저장 가능)
});
