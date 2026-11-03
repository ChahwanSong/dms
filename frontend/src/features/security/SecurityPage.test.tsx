import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest";
import { SecurityPage, windowText } from "./SecurityPage";
import type { SecurityEvent, SecurityStatus, VerificationLock } from "../../lib/types";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

type DeepPartial<T> = { [K in keyof T]?: T[K] extends object | null ? DeepPartial<T[K]> : T[K] };

function status(o: DeepPartial<SecurityStatus> = {}): SecurityStatus {
  const base: SecurityStatus = {
    started_at: "2026-11-03T01:00:00Z",
    login: { id_failure_limit: 10, id_failure_window_seconds: 60, id_attempt_limit: 60, id_attempt_window_seconds: 60,
             scrypt_slots: 2, scrypt_max_wait_seconds: 5, scrypt_in_use: 1, scrypt_waiting: 0,
             pacing: { enabled: false, limit: 0, window_seconds: 60, max_wait_seconds: 15, tokens: null, waiting: 0 } },
    alarms: { window_seconds: 600, failures: 30, attempts: 300, source_failures: 20, recent_failures: 3, recent_attempts: 12 },
    verification: { code_digits: 6, code_ttl_seconds: 300, per_code_attempts: 5, per_id_failure_limit: 10,
                    per_id_failure_window_seconds: 86400, global_guess_limit: 1000, global_guess_window_seconds: 3600,
                    global_guess_failures: 12, global_locked: false, global_retry_after_seconds: null },
  };
  return {
    ...base, ...o,
    login: { ...base.login, ...o.login, pacing: { ...base.login.pacing, ...o.login?.pacing } },
    alarms: { ...base.alarms, ...o.alarms },
    verification: { ...base.verification, ...o.verification },
  } as SecurityStatus;
}

const LOCKS: VerificationLock[] = [
  { username: "alice", purpose: "password_reset", failures: 10, limit: 10, window_start: "2026-11-03T01:02:03Z",
    locked: true, retry_after_seconds: 81234 },
  { username: "bob", purpose: "signup", failures: 3, limit: 10, window_start: "2026-11-03T02:00:00Z",
    locked: false, retry_after_seconds: null },
];

const EVENTS: SecurityEvent[] = [
  { id: 9, at: "2026-11-03T03:00:00Z", component: "auth", severity: "warning", event_type: "login_source_alarm",
    message: "login failures from one source", payload: { count: 20, threshold: 20, window_seconds: 600,
    last_username: "carol", client_ip: "203.0.113.7", peer: "10.10.10.1" } },
  { id: 8, at: "2026-11-03T02:00:00Z", component: "accounts", severity: "info", event_type: "verification_unlocked",
    message: null, payload: { username: "alice", purpose: null, cleared: 1, actor: "admin",
    client_ip: "10.0.0.5", peer: "10.0.0.5" } },
  { id: 7, at: "2026-11-03T01:00:00Z", component: "mailer", severity: "error", event_type: "account_notice_failed",
    message: "relay down", payload: null },
  { id: 6, at: "2026-11-03T00:30:00Z", component: "auth", severity: "warning", event_type: "brand_new_kind",
    message: "x", payload: {} },
  // 변조된 DB 행 흉내 -- Object.prototype 이름이 라벨·배지·payload 이름으로 함수를 집지 않는다
  { id: 5, at: "2026-11-03T00:20:00Z", component: "auth", severity: "constructor", event_type: "toString",
    message: "y", payload: { constructor: "c", cause: "toString" } },
];

interface Handlers {
  st?: SecurityStatus; locks?: VerificationLock[]; truncated?: boolean; events?: SecurityEvent[];
  eventsStatus?: number;
}
const eventUrls: URLSearchParams[] = [];
const lastUrl = () => eventUrls[eventUrls.length - 1];
function stub(h: Handlers = {}) {
  eventUrls.length = 0;
  server.use(
    http.get("/api/admin/security/status", () => HttpResponse.json(h.st ?? status())),
    http.get("/api/admin/security/verification-locks", () =>
      HttpResponse.json({ items: h.locks ?? LOCKS, truncated: h.truncated ?? false, limit: 200 })),
    http.get("/api/admin/security/events", ({ request }) => {
      eventUrls.push(new URL(request.url).searchParams);
      if (h.eventsStatus) return HttpResponse.json({ detail: "invalid_event_filter" }, { status: h.eventsStatus });
      return HttpResponse.json(h.events ?? EVENTS);
    }),
  );
}

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><SecurityPage /></QueryClientProvider>);
}

const card = (heading: string) => screen.getByRole("heading", { name: heading, level: 2 }).closest("div.bg-surface") as HTMLElement;
// 종류 라벨은 필터 select 의 option 에도 있다 -- 표 칸의 라벨(span)로 행을 찾는다.
const eventRow = async (label: string) => (await screen.findByText(label, { selector: "td span" })).closest("tr")!;
// 상태 카드의 전체 해제 결과 줄 -- 늘 그려 두는 라이브 영역이라(비어 있어도 있다) 글자가 찰 때까지 기다린다.
const globalDoneLine = () => within(card("보호 장치 상태")).getByRole("status");
const expectGlobalDone = (text: string) => waitFor(() => expect(globalDoneLine()).toHaveTextContent(text));
const dd = (label: string) => {
  const dt = screen.getAllByText(label, { selector: "dt" })[0];
  return dt.nextElementSibling as HTMLElement;
};

test("windowText: 나눠떨어지는 가장 큰 단위", () => {
  expect(windowText(3600)).toBe("1시간");
  expect(windowText(86400)).toBe("24시간");
  expect(windowText(600)).toBe("10분");
  expect(windowText(90)).toBe("90초");
  expect(windowText(45)).toBe("45초");
});

test("제목·설명과 보호 장치 상태(정상): 값·배지·시작 시각 문구", async () => {
  stub();
  wrap();
  expect(screen.getByRole("heading", { name: "보안 이벤트", level: 1 })).toBeInTheDocument();
  expect(screen.getByText("로그인·인증번호 보호 장치의 현재 상태와 최근 보안 이벤트입니다. 계정 비밀번호 재설정은 계정 화면에서 합니다."))
    .toBeInTheDocument();
  await screen.findByText("12 / 1000회 (최근 1시간)");
  expect(within(dd("인증번호 전체 오답")).getByText("정상")).toBeInTheDocument();
  expect(dd("인증번호(아이디별)")).toHaveTextContent("6자리 · 유효 5분 · 코드당 5회 · 틀린 횟수 10회/24시간");
  expect(dd("로그인(아이디별)")).toHaveTextContent("실패 10회/60초 · 시도(성공 포함) 60회/60초");
  expect(dd("비밀번호 검증 슬롯")).toHaveTextContent("2개 (사용 중 1 · 대기 0) · 최대 대기 5초");
  expect(dd("대기형 전체 스위치")).toHaveTextContent(/^꺼짐$/);
  expect(dd("경보 임계")).toHaveTextContent("실패 30회 · 시도 300회 · 출발지별 실패 20회 / 10분");
  expect(dd("경보 임계")).toHaveTextContent("최근 10분: 실패 3회 · 시도 12회");
  expect(screen.getByText("카운터는 dms-api 프로세스 메모리에 있습니다 — 2026-11-03 10:00:00 KST 시작 이후 값이며 재시작하면 비워집니다."))
    .toBeInTheDocument();
  // 설명 툴팁 버튼(키보드로 닿는다)
  expect(screen.getByRole("button", { name: "보호 장치 상태 설명" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "대기형 전체 스위치 설명" })).toBeInTheDocument();
});

test("전체 오답 잠김이면 빨간 배지에 풀리기까지 남은 시간, 스위치가 켜지면 속도·대기 수", async () => {
  stub({ st: status({
    verification: { global_guess_failures: 1000, global_locked: true, global_retry_after_seconds: 1800 },
    login: { pacing: { enabled: true, limit: 60, window_seconds: 60, max_wait_seconds: 15, tokens: 12.5, waiting: 3 } },
  }) });
  wrap();
  expect(await screen.findByText("잠김 — 약 30분 뒤 풀림")).toBeInTheDocument();
  expect(screen.getByText("1000 / 1000회 (최근 1시간)")).toBeInTheDocument();
  const sw = dd("대기형 전체 스위치");
  expect(within(sw).getByText("켜짐")).toBeInTheDocument();
  expect(sw).toHaveTextContent("실패 60회/60초 속도로 늦춤 · 최대 대기 15초 · 대기 중 3");
});

test("전체 오답 0 이고 잠기지 않았으면 되돌릴 것이 없다 -- 버튼이 없다", async () => {
  stub({ st: status({ verification: { global_guess_failures: 0 } }) });
  wrap();
  await screen.findByText("0 / 1000회 (최근 1시간)");
  expect(within(dd("인증번호 전체 오답")).getByText("정상")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "전체 잠금 해제" })).toBeNull();
  expect(screen.queryByRole("button", { name: "오답 합계 초기화" })).toBeNull();
});

test("0 은 「꺼짐」이다(0회로 보이지 않는다) -- 전체 상한이 꺼지면 해제 버튼도 없다", async () => {
  stub({ st: status({
    login: { id_attempt_limit: 0, id_failure_limit: 0 },
    alarms: { failures: 0, source_failures: 0 },
    verification: { global_guess_limit: 0, global_guess_failures: 0 },
  }) });
  wrap();
  await screen.findByText("카운터는", { exact: false });
  expect(dd("인증번호 전체 오답")).toHaveTextContent(/^꺼짐$/);
  expect(screen.queryByRole("button", { name: "전체 잠금 해제" })).toBeNull();
  expect(dd("로그인(아이디별)")).toHaveTextContent("실패 꺼짐 · 시도(성공 포함) 꺼짐");
  expect(dd("경보 임계")).toHaveTextContent("실패 꺼짐 · 시도 300회 · 출발지별 실패 꺼짐 / 10분");
});

const LOCKED_GLOBAL = () => status({
  verification: { global_guess_failures: 1000, global_locked: true, global_retry_after_seconds: 1800 },
});

test("전체 잠금 해제: 잠겼을 때 확인 창(오답 수) → POST {} → 창을 닫고 카드에 결과", async () => {
  stub({ st: LOCKED_GLOBAL() });
  let body: unknown = "none";
  server.use(http.post("/api/admin/security/verification-global-unlock", async ({ request }) => {
    body = await request.json();
    return HttpResponse.json({ cleared_failures: 1000 });
  }));
  wrap();
  await screen.findByText("1000 / 1000회 (최근 1시간)");
  expect(screen.queryByRole("button", { name: "오답 합계 초기화" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "전체 잠금 해제" }));
  const dlg = await screen.findByRole("dialog", { name: "인증번호 전체 잠금 해제" });
  expect(dlg).toHaveTextContent("모든 아이디의 인증번호 오답 합계(1000회)를 0으로 되돌려 계정 생성·비밀번호 변경을 바로 다시 열어 줍니다.");
  expect(dlg).toHaveTextContent("아래 이벤트에서 출발지를 먼저 확인하세요.");
  await userEvent.click(within(dlg).getByRole("button", { name: "잠금 해제" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(body).toEqual({});
  await expectGlobalDone("전체 잠금을 풀었습니다(오답 1000회 초기화)");
});

test.each([0, 300])("전체 해제 뒤 상태를 다시 읽어 버튼이 사라져도 결과를 말하고 포커스는 결과 줄로 간다(이벤트 재조회 %ims 지연)", async (eventsDelay) => {
  // 2026-11-03 리뷰: 실제 서버는 해제 뒤 오답 0·잠김 false 를 돌려준다 -- 버튼·창이 통째로 언마운트돼 Radix 가 포커스를 돌려줄
  // 트리거가 없었다. 결과 줄은 미리 있던 라이브 영역이다(해제 전에도 비어 있는 채로 있다).
  // 지연 300ms(실 Chrome 에서 본 순서): 무효화는 상태·잠금·이벤트 셋이 다 끝나야 mutation 이 성공을 알린다 -- 먼저 온 상태(오답
  // 0)가 그려지며 컴포넌트가 빠지면 mutate 콜백이 불리지 않아 결과 문구조차 없었다.
  let unlocked = false;
  server.use(
    http.get("/api/admin/security/status", () => HttpResponse.json(unlocked
      ? status({ verification: { global_guess_failures: 0, global_locked: false, global_retry_after_seconds: null } })
      : LOCKED_GLOBAL())),
    http.get("/api/admin/security/verification-locks", () => HttpResponse.json({ items: [], truncated: false, limit: 200 })),
    http.get("/api/admin/security/events", async () => {
      if (unlocked && eventsDelay > 0) await new Promise((r) => setTimeout(r, eventsDelay));
      return HttpResponse.json([]);
    }),
    http.post("/api/admin/security/verification-global-unlock", () => {
      unlocked = true;
      return HttpResponse.json({ cleared_failures: 1000 });
    }),
  );
  wrap();
  await screen.findByText("1000 / 1000회 (최근 1시간)");
  const line = globalDoneLine();
  expect(line).toHaveTextContent(/^$/);
  expect(line).toHaveAttribute("tabindex", "-1");
  await userEvent.click(screen.getByRole("button", { name: "전체 잠금 해제" }));
  const dlg = await screen.findByRole("dialog", { name: "인증번호 전체 잠금 해제" });
  await userEvent.click(within(dlg).getByRole("button", { name: "잠금 해제" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull(), { timeout: 2000 });
  await screen.findByText("0 / 1000회 (최근 1시간)");
  expect(screen.queryByRole("button", { name: /전체 잠금 해제|오답 합계 초기화/ })).toBeNull();
  expect(globalDoneLine()).toBe(line);                     // 같은 요소(새로 끼워 넣지 않았다)
  await waitFor(() => expect(line).toHaveTextContent("전체 잠금을 풀었습니다(오답 1000회 초기화)"), { timeout: 2000 });
  await waitFor(() => expect(line).toHaveFocus());
  expect(document.activeElement).not.toBe(document.body);
});

test("「취소」로 닫으면 포커스는 트리거로 돌아간다(결과 줄로 옮기지 않는다)", async () => {
  stub({ st: LOCKED_GLOBAL() });
  wrap();
  await screen.findByText("1000 / 1000회 (최근 1시간)");
  const trigger = screen.getByRole("button", { name: "전체 잠금 해제" });
  await userEvent.click(trigger);
  const dlg = await screen.findByRole("dialog", { name: "인증번호 전체 잠금 해제" });
  await userEvent.click(within(dlg).getByRole("button", { name: "취소" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await waitFor(() => expect(trigger).toHaveFocus());
  expect(globalDoneLine()).toHaveTextContent(/^$/);
});

test("잠기지 않았는데 오답이 쌓였으면 「오답 합계 초기화」 -- 잠금을 풀었다고 말하지 않는다", async () => {
  // 2026-11-03 리뷰: 정상 상태에서도 「전체 잠금 해제」·「다시 열어 줍니다」·「잠금을 풀었습니다」였다(같은 POST, 틀린 말).
  stub();
  let posted = 0;
  server.use(http.post("/api/admin/security/verification-global-unlock", () => {
    posted += 1;
    return HttpResponse.json({ cleared_failures: 12 });
  }));
  wrap();
  await screen.findByText("12 / 1000회 (최근 1시간)");
  expect(screen.queryByRole("button", { name: "전체 잠금 해제" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "오답 합계 초기화" }));
  const dlg = await screen.findByRole("dialog", { name: "인증번호 오답 합계 초기화" });
  expect(dlg).toHaveTextContent("모든 아이디의 인증번호 오답 합계(12회)를 0으로 되돌립니다. 지금은 잠겨 있지 않습니다 — 상한 1000회까지 남은 여유를 다시 채울 뿐이고, 계정 생성·비밀번호 변경은 지금도 됩니다.");
  expect(dlg).not.toHaveTextContent("다시 열어 줍니다");
  await userEvent.click(within(dlg).getByRole("button", { name: "초기화" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(posted).toBe(1);
  await expectGlobalDone("인증번호 오답 합계를 0으로 되돌렸습니다(12회 초기화)");
  expect(globalDoneLine()).not.toHaveTextContent("잠금");
});

test("전체 해제를 보내는 동안엔 Esc·창 닫기·「취소」로 닫히지 않고, 응답이 오면 결과를 말한다", async () => {
  // 닫히면 effect 의 unlock.reset() 이 진행 중인 mutation 에서 observer 를 떼어 결과 문구가 사라진다(2026-11-03 리뷰).
  stub({ st: LOCKED_GLOBAL() });
  let release!: () => void;
  const gate = new Promise<void>((r) => { release = r; });
  server.use(http.post("/api/admin/security/verification-global-unlock", async () => {
    await gate;
    return HttpResponse.json({ cleared_failures: 1000 });
  }));
  wrap();
  await screen.findByText("1000 / 1000회 (최근 1시간)");
  await userEvent.click(screen.getByRole("button", { name: "전체 잠금 해제" }));
  const dlg = await screen.findByRole("dialog", { name: "인증번호 전체 잠금 해제" });
  await userEvent.click(within(dlg).getByRole("button", { name: "잠금 해제" }));
  await waitFor(() => expect(dlg).toHaveAttribute("aria-busy", "true"));
  expect(within(dlg).getByRole("button", { name: "취소" })).toBeDisabled();
  await userEvent.keyboard("{Escape}");
  await userEvent.click(within(dlg).getByRole("button", { name: "창 닫기" }));
  expect(screen.getByRole("dialog", { name: "인증번호 전체 잠금 해제" })).toBe(dlg);
  release();
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await expectGlobalDone("전체 잠금을 풀었습니다(오답 1000회 초기화)");
});

test("전체 해제: 누른 직후 다시 그리기 전의 Esc·「취소」·두 번째 클릭도 막는다 -- 한 번만 보낸다", async () => {
  // isPending 은 다음 렌더에야 참이다(TanStack 알림 setTimeout 0) -- 같은 틱의 동기 이벤트로 그 틈을 만든다(2026-11-03).
  stub({ st: LOCKED_GLOBAL() });
  let posts = 0;
  let release!: () => void;
  const gate = new Promise<void>((r) => { release = r; });
  server.use(http.post("/api/admin/security/verification-global-unlock", async () => {
    posts += 1;
    await gate;
    return HttpResponse.json({ cleared_failures: 1000 });
  }));
  wrap();
  await screen.findByText("1000 / 1000회 (최근 1시간)");
  await userEvent.click(screen.getByRole("button", { name: "전체 잠금 해제" }));
  const dlg = await screen.findByRole("dialog", { name: "인증번호 전체 잠금 해제" });
  const go = within(dlg).getByRole("button", { name: "잠금 해제" });
  fireEvent.click(go);
  fireEvent.click(go);
  fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
  fireEvent.click(within(dlg).getByRole("button", { name: "취소" }));
  expect(screen.getByRole("dialog", { name: "인증번호 전체 잠금 해제" })).toBe(dlg);
  await waitFor(() => expect(posts).toBe(1));
  release();
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(posts).toBe(1);
  await expectGlobalDone("전체 잠금을 풀었습니다(오답 1000회 초기화)");
});

test("전체 잠금 해제 실패(토큰 403)는 창 안 alert 로 남는다", async () => {
  stub({ st: LOCKED_GLOBAL() });
  server.use(http.post("/api/admin/security/verification-global-unlock", () =>
    HttpResponse.json({ detail: "accounts_session_required" }, { status: 403 })));
  wrap();
  await screen.findByText("1000 / 1000회 (최근 1시간)");
  await userEvent.click(screen.getByRole("button", { name: "전체 잠금 해제" }));
  const dlg = await screen.findByRole("dialog");
  await userEvent.click(within(dlg).getByRole("button", { name: "잠금 해제" }));
  expect(await within(dlg).findByRole("alert")).toHaveTextContent("포탈에 로그인한 관리자만 할 수 있습니다(토큰 인증 불가)");
});

test("인증번호 잠금 표: 용도 한국어·틀린 횟수·첫 실패(KST)·상태 배지", async () => {
  stub();
  wrap();
  const alice = (await screen.findByText("alice", { selector: "td" })).closest("tr")!;
  expect(alice).toHaveTextContent("비밀번호 변경");
  expect(alice).toHaveTextContent("10 / 10");
  expect(alice).toHaveTextContent("2026-11-03 10:02:03 KST");
  expect(within(alice).getByText("잠김 — 약 23시간 뒤 풀림")).toBeInTheDocument();
  const bob = screen.getByText("bob", { selector: "td" }).closest("tr")!;
  expect(bob).toHaveTextContent("계정 생성");
  // 화면이 지은 배지에는 영어 툴팁(title)이 없다(2026-11-03 리뷰 -- 'counting'·'locked' 가 마우스 툴팁에 떴다)
  expect(within(bob).getByText("누적 중")).not.toHaveAttribute("title");
  expect(within(alice).getByText("잠김 — 약 23시간 뒤 풀림")).not.toHaveAttribute("title");
  // 잠긴 행은 「잠금 해제」, 누적 중인 행은 「기록 지우기」(같은 POST -- 잠기지 않은 행에 「잠금 해제」는 틀린 말)
  expect(within(alice).getByRole("button", { name: "alice 비밀번호 변경 잠금 해제" })).toHaveTextContent(/^잠금 해제$/);
  expect(within(bob).getByRole("button", { name: "bob 계정 생성 실패 기록 지우기" })).toHaveTextContent(/^기록 지우기$/);
  expect(within(bob).queryByRole("button", { name: /잠금 해제/ })).toBeNull();
});

test("행 잠금 해제: POST {username, purpose} → 결과 문구(지운 행 있음/없음), 실패는 alert", async () => {
  stub();
  const bodies: unknown[] = [];
  let cleared = 1;
  let bobDenied = true;
  server.use(http.post("/api/admin/security/verification-unlock", async ({ request }) => {
    const b = await request.json() as { username: string; purpose: string };
    bodies.push(b);
    if (b.username === "bob" && bobDenied) return HttpResponse.json({ detail: "privileged_account_protected" }, { status: 403 });
    return HttpResponse.json({ username: b.username, purpose: b.purpose, cleared });
  }));
  wrap();
  await screen.findByText("alice", { selector: "td" });
  await userEvent.click(screen.getByRole("button", { name: "alice 비밀번호 변경 잠금 해제" }));
  const done = await screen.findByText("alice(비밀번호 변경) 의 인증 잠금을 풀었습니다");
  expect(done).toHaveAttribute("role", "status");
  // 누른 행이 사라져도 포커스가 <body> 로 떨어지지 않게 결과 문구로 옮긴다
  await waitFor(() => expect(done).toHaveFocus());
  expect(bodies[0]).toEqual({ username: "alice", purpose: "password_reset" });
  cleared = 0;
  await userEvent.click(screen.getByRole("button", { name: "alice 비밀번호 변경 잠금 해제" }));
  expect(await screen.findByText("alice(비밀번호 변경) 는 풀 잠금이 없었습니다(그 사이 풀렸거나 성공했습니다)")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "bob 계정 생성 실패 기록 지우기" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("특권 실행 목록에 있는 이름의 계정은");
  // 누적 중(잠기지 않은) 행은 「잠금을 풀었다」가 아니라 실패 기록을 지웠다고 말한다(2026-11-03 리뷰)
  bobDenied = false;
  cleared = 1;
  await userEvent.click(screen.getByRole("button", { name: "bob 계정 생성 실패 기록 지우기" }));
  expect(await screen.findByText("bob(계정 생성) 의 인증번호 실패 기록(3회)을 지웠습니다")).toHaveAttribute("role", "status");
  cleared = 0;
  await userEvent.click(screen.getByRole("button", { name: "bob 계정 생성 실패 기록 지우기" }));
  expect(await screen.findByText("bob(계정 생성) 는 지울 실패 기록이 없었습니다(그 사이 창이 지났거나 성공했습니다)"))
    .toBeInTheDocument();
});

test("행 해제는 한 번에 하나: 보내는 동안 모든 행 버튼이 잠기고, 같은 틱의 두 번째 클릭도 보내지 않는다", async () => {
  // 2026-11-03 리뷰: 모든 행이 mutation 하나를 함께 써서, 앞 행을 보내는 동안 다른 행을 누르면 앞 행의 결과·오류가 영영 보이지
  // 않았고(TanStack 이 mutate 콜백을 뒤 것으로 바꿈) 앞 행 버튼이 다시 눌렸다.
  stub();
  const posted: string[] = [];
  let release!: () => void;
  const gate = new Promise<void>((r) => { release = r; });
  server.use(http.post("/api/admin/security/verification-unlock", async ({ request }) => {
    const b = await request.json() as { username: string; purpose: string };
    posted.push(b.username);
    if (b.username === "alice") await gate;
    return HttpResponse.json({ username: b.username, purpose: b.purpose, cleared: 1 });
  }));
  wrap();
  await screen.findByText("alice", { selector: "td" });
  const aliceBtn = screen.getByRole("button", { name: "alice 비밀번호 변경 잠금 해제" });
  const bobBtn = screen.getByRole("button", { name: "bob 계정 생성 실패 기록 지우기" });
  fireEvent.click(aliceBtn);
  fireEvent.click(bobBtn);                                 // 다시 그리기 전(isPending 이 아직 거짓) -- sending 이 막는다
  await waitFor(() => expect(posted).toEqual(["alice"]));
  await waitFor(() => expect(bobBtn).toBeDisabled());
  expect(aliceBtn).toBeDisabled();
  await userEvent.click(bobBtn);                           // 잠긴 버튼은 눌리지 않는다
  release();
  expect(await screen.findByText("alice(비밀번호 변경) 의 인증 잠금을 풀었습니다")).toHaveAttribute("role", "status");
  expect(posted).toEqual(["alice"]);
  await waitFor(() => expect(bobBtn).toBeEnabled());
  await userEvent.click(bobBtn);
  expect(await screen.findByText("bob(계정 생성) 의 인증번호 실패 기록(3회)을 지웠습니다")).toBeInTheDocument();
  expect(posted).toEqual(["alice", "bob"]);
});

test("행 해제 결과 줄은 표 위에 늘 있고(비어 있어도), 결과가 생기면 그 줄로 포커스가 간다", async () => {
  // 2026-11-03 리뷰: 표 아래 + preventScroll 이면 잠금 행이 수십 개일 때 결과가 화면 밖이었다(AccountsList 와 같은 자리로).
  stub();
  server.use(http.post("/api/admin/security/verification-unlock", async ({ request }) => {
    const b = await request.json() as { username: string; purpose: string };
    return HttpResponse.json({ username: b.username, purpose: b.purpose, cleared: 1 });
  }));
  wrap();
  await screen.findByText("alice", { selector: "td" });
  const locks = card("인증번호 잠금 (아이디별)");
  const line = within(locks).getByRole("status");
  expect(line).toHaveTextContent(/^$/);
  const table = within(locks).getByRole("table");
  // DOCUMENT_POSITION_FOLLOWING(4): 표가 결과 줄 뒤에 온다
  expect(line.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const spy = vi.spyOn(line, "focus");
  await userEvent.click(screen.getByRole("button", { name: "alice 비밀번호 변경 잠금 해제" }));
  await waitFor(() => expect(line).toHaveTextContent("alice(비밀번호 변경) 의 인증 잠금을 풀었습니다"));
  await waitFor(() => expect(line).toHaveFocus());
  expect(spy).toHaveBeenCalledWith();                      // preventScroll 없이 -- 화면 밖이면 브라우저가 결과 줄로 스크롤한다
  spy.mockRestore();
});

test("잠금 목록이 비면 안내 문구, 잘렸으면 상위 N건 안내", async () => {
  stub({ locks: [] });
  wrap();
  expect(await screen.findByText("잠기거나 실패가 쌓인 아이디가 없습니다")).toBeInTheDocument();
  server.resetHandlers();
  stub({ truncated: true });
  wrap();
  expect(await screen.findByText("상위 200건만 보입니다")).toBeInTheDocument();
});

test("이벤트 표: 라벨 + 코드, 심각도 배지, 출발지와 (다르면) 피어, 모르는 종류는 원문", async () => {
  stub();
  wrap();
  const r9 = await eventRow("경보: 한 출발지의 로그인 실패");
  expect(within(r9).getByText("login_source_alarm")).toBeInTheDocument();
  // 심각도는 서버 원문이라 StatusPill 관례대로 원문을 title 로 남긴다(화면이 지은 배지와 다르다)
  expect(within(r9).getByText("경고")).toHaveAttribute("title", "warning");
  // 「서버 메시지」 열은 서버 기록 원문(영문)이다 -- 열 이름·검색칸 안내가 그 사실을 말한다(2026-11-03 리뷰)
  expect(screen.getByRole("columnheader", { name: "서버 메시지" })).toBeInTheDocument();
  expect(screen.queryByRole("columnheader", { name: "내용" })).toBeNull();
  expect(screen.getByRole("textbox", { name: "이벤트 검색" })).toHaveAttribute("placeholder", "아이디·IP·서버 메시지");
  expect(r9).toHaveTextContent("203.0.113.7");
  expect(within(r9).getByText("피어 10.10.10.1")).toBeInTheDocument();
  expect(r9).toHaveTextContent("2026-11-03 12:00:00 KST");
  const r8 = await eventRow("인증 잠금 해제(아이디)");
  expect(within(r8).getByText("정보")).toBeInTheDocument();
  expect(within(r8).queryByText(/^피어/)).toBeNull();     // 같은 값이면 피어 줄을 생략
  expect(r8).toHaveTextContent("—");                       // message null
  const r7 = await eventRow("완료 안내 메일 실패");
  expect(within(r7).getByText("오류")).toBeInTheDocument();
  expect(r7).toHaveTextContent("relay down");
  expect(screen.getAllByText("brand_new_kind", { selector: "td span" })).toHaveLength(2);   // 라벨 자리·코드 자리 모두 원문
  const r5 = (await screen.findAllByText("toString", { selector: "td span" }))[0].closest("tr")!;   // 라벨·코드 두 자리 모두 원문
  expect(within(r5).getByText("constructor")).toBeInTheDocument();      // 모르는 심각도는 원문 배지
  await userEvent.click(within(r5).getByRole("button", { name: "펼치기" }));
  const d5 = r5.nextElementSibling as HTMLElement;
  expect(d5).toHaveTextContent(/^constructorc원인causetoString$/);
  expect(screen.getByText("마지막 기록입니다 — 전체 5건")).toBeInTheDocument();
});

test("펼치기: payload 키·값(한국어 이름 + 원문 키), payload null 이면 「추가 기록이 없습니다」", async () => {
  stub();
  wrap();
  const r8 = await eventRow("인증 잠금 해제(아이디)");
  const toggle = within(r8).getByRole("button", { name: "펼치기" });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  await userEvent.click(toggle);
  expect(within(r8).getByRole("button", { name: "닫기" })).toHaveAttribute("aria-expanded", "true");
  const detail = r8.nextElementSibling as HTMLElement;
  expect(detail).toHaveTextContent("아이디username");
  expect(detail).toHaveTextContent("null (두 용도 모두)");
  expect(detail).toHaveTextContent("실행자actor");
  const r7 = await eventRow("완료 안내 메일 실패");
  await userEvent.click(within(r7).getByRole("button", { name: "펼치기" }));
  expect(screen.getByText("추가 기록이 없습니다")).toBeInTheDocument();
  // 한 번에 하나만 펼친다
  expect(within(r8).getByRole("button", { name: "펼치기" })).toBeInTheDocument();
});

test("필터: 종류·심각도·검색어가 쿼리 문자열에 실리고, 빈 값은 빠진다", async () => {
  stub();
  wrap();
  await eventRow("경보: 한 출발지의 로그인 실패");
  expect([...eventUrls[0].keys()].sort()).toEqual(["limit"]);
  expect(eventUrls[0].get("limit")).toBe("50");
  // 종류 select 는 묶음(optgroup)으로 고른다
  const typeSel = screen.getByRole("combobox", { name: "이벤트 종류" });
  expect(within(typeSel).getByRole("group", { name: "관리자 조치" })).toBeInTheDocument();
  await userEvent.selectOptions(typeSel, "login_busy");
  await waitFor(() => expect(lastUrl().get("type")).toBe("login_busy"));
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "심각도" }), "warning");
  await waitFor(() => expect(lastUrl().get("severity")).toBe("warning"));
  await userEvent.type(screen.getByRole("textbox", { name: "이벤트 검색" }), " 203.0.113.7 ");
  await waitFor(() => expect(lastUrl().get("q")).toBe("203.0.113.7"));
  // 되돌리면 그 키가 빠진다
  await userEvent.selectOptions(typeSel, "");
  await waitFor(() => expect(lastUrl().has("type")).toBe(false));
  expect(lastUrl().get("severity")).toBe("warning");
});

test("빈 목록 문구는 필터 유무로 가르고, 오류는 한국어 사유", async () => {
  stub({ events: [] });
  wrap();
  expect(await screen.findByText("기록된 보안 이벤트가 없습니다")).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "심각도" }), "error");
  expect(await screen.findByText("조건에 맞는 이벤트가 없습니다")).toBeInTheDocument();
  server.resetHandlers();
  stub({ eventsStatus: 422 });
  wrap();
  expect(await screen.findByText("이벤트 필터 값이 올바르지 않습니다 — 목록에 있는 종류·심각도만 고를 수 있습니다"))
    .toBeInTheDocument();
});

test("새로고침은 이벤트를 다시 읽는다", async () => {
  stub();
  wrap();
  await eventRow("경보: 한 출발지의 로그인 실패");
  const before = eventUrls.length;
  // 저장소 전체 표기(「새로고침」)와 같다(2026-11-03 리뷰 -- 이 화면만 「새로 고침」이었다)
  await userEvent.click(screen.getByRole("button", { name: "새로고침" }));
  await waitFor(() => expect(eventUrls.length).toBe(before + 1));
});
