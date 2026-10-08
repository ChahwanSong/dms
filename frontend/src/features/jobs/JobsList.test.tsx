import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse, delay } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { JobsList } from "./JobsList";
import type { Me } from "../../lib/types";

// 관리자 화면은 정리 현황(usePurgeStatus)도 부른다 -- 기본은 대기 0건(MSW 미처리 경고 방지).
const NO_PURGE = { pending: 0, stalled: 0, oldest_requested_at: null, items: [] };
const server = setupServer(http.get("/api/admin/request-purges", () => HttpResponse.json(NO_PURGE)));
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const meAdmin: Me = { actor: "root", role: "admin" };
const meUser: Me = { actor: "alice", role: "user" };

function row(id: string, over: Record<string, unknown> = {}) {
  return {
    request_id: id, operation: "sync", state: "Succeeded", priority: "mid",
    requester_id: "alice", resource_key: "k", commit_order: Number(id.slice(1)) || 1,
    created_at: "2026-08-22T00:00:00Z", updated_at: "2026-08-22T00:01:00Z",
    payload: { source_storage: "s1", source: "a", destination_storage: "s1", destination: "b" },
    ...over,
  };
}

function wrap(me: Me = meAdmin) {
  server.use(http.get("/api/auth/me", () => HttpResponse.json(me)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter><JobsList /></MemoryRouter></QueryClientProvider>);
}

test("전체 작업: 요청자·대상 요약 등 풍부한 정보를 표시한다", async () => {
  server.use(http.get("/api/user/requests", () => HttpResponse.json([row("r1")])));
  wrap();
  expect(await screen.findByRole("heading", { name: "전체 작업" })).toBeInTheDocument();
  const r = (await screen.findByText("r1", { exact: false })).closest("tr")!;
  expect(within(r).getByText("alice")).toBeInTheDocument();          // 요청자 필수
  expect(within(r).getByText("Succeeded")).toBeInTheDocument();
  expect(within(r).getByText("s1:a → s1:b")).toBeInTheDocument();    // 대상 요약(pathSummary)
});

test("요청자 필터는 운영자에게만 보인다", async () => {
  server.use(http.get("/api/user/requests", () => HttpResponse.json([row("r1")])));
  const { unmount } = wrap(meUser);
  await screen.findByText("r1", { exact: false });
  expect(screen.queryByLabelText("요청자 필터")).not.toBeInTheDocument();
  expect(screen.getByLabelText("연산 필터")).toBeInTheDocument();     // 연산·상태는 공용
  unmount();
  wrap(meAdmin);
  expect(await screen.findByLabelText("요청자 필터")).toBeInTheDocument();
});

test("필터를 바꾸면 서버 쿼리에 operation·state·requester 가 실린다", async () => {
  const urls: string[] = [];
  server.use(http.get("/api/user/requests", ({ request }) => {
    urls.push(request.url); return HttpResponse.json([row("r1")]);
  }));
  wrap(meAdmin);
  await screen.findByText("r1", { exact: false });
  await userEvent.selectOptions(screen.getByLabelText("연산 필터"), "rm");
  await userEvent.selectOptions(screen.getByLabelText("상태 필터"), "Failed");
  await userEvent.type(screen.getByLabelText("요청자 필터"), "bob");
  // 마지막 요청이 세 필터를 모두 반영한다.
  await screen.findByText(/조건에 맞는 작업이 없습니다|r1/);
  const last = urls[urls.length - 1];
  expect(last).toContain("operation=rm");
  expect(last).toContain("state=Failed");
  expect(last).toContain("requester=bob");
});

test("빈 결과는 전용 문구를 보인다", async () => {
  server.use(http.get("/api/user/requests", () => HttpResponse.json([])));
  wrap();
  expect(await screen.findByText("조건에 맞는 작업이 없습니다")).toBeInTheDocument();
});

// --- 작업(요청) 선택 삭제(2026-10-08, 관리자 전용) -------------------------------------------------------------
// id 는 32-hex(서버 형식) -- 앞 12자가 서로 달라야 체크박스 aria-label 이 구분된다.
const hex = (n: number) => `${String(n).padStart(4, "0")}${"ab".repeat(14)}`;
const A = hex(1), B = hex(2), C = hex(3), D = hex(4), E = hex(5);
const box = (id: string) => screen.getByLabelText(`작업 ${id.slice(0, 12)} 선택`) as HTMLInputElement;
const allBox = () => screen.getByLabelText("불러온 작업 중 삭제 가능한 것 전체 선택") as HTMLInputElement;
const bar = () => screen.getByRole("toolbar", { name: "작업 일괄 처리" });
const status = () => within(bar()).getByRole("status");
const IDLE = "끝난 작업을 선택해 삭제할 수 있습니다";
const RULE = "진행 중·배치 항목의 작업은 선택할 수 없습니다";

/** 종단 단건 3건(Succeeded scan·Failed sync·Cancelled) + 비종단 1건(Running) + 배치 자식 1건(종단). */
const mixed = () => [
  row(A, { operation: "scan", state: "Succeeded", payload: { storage: "s1", target: "t1" } }),
  row(B, { state: "Failed" }),
  row(C, { state: "Running" }),
  row(D, { state: "Succeeded", batch_id: "b".repeat(32) }),
  row(E, { state: "Cancelled" }),
];

function wrapRows(rows: { request_id: string }[] | (() => { request_id: string }[]),
                  me: Me = meAdmin, purge: object | (() => object) = NO_PURGE) {
  server.use(
    http.get("/api/user/requests", () => HttpResponse.json(typeof rows === "function" ? rows() : rows)),
    http.get("/api/admin/request-purges", () => HttpResponse.json(typeof purge === "function" ? purge() : purge)),
    http.get("/api/auth/me", () => HttpResponse.json(me)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const r = render(<QueryClientProvider client={qc}><MemoryRouter><JobsList /></MemoryRouter></QueryClientProvider>);
  return { ...r, qc };
}

/** 관리자 판정(me)이 도착해 체크 열이 생길 때까지 기다린다. */
async function ready(id: string) {
  await screen.findByText(id);
  await waitFor(() => expect(box(id)).toBeInTheDocument());
}

test("비관리자: 체크 열·일괄 툴바가 없고 정리 현황도 부르지 않는다", async () => {
  let purgeCalls = 0;
  server.use(http.get("/api/admin/request-purges", () => { purgeCalls += 1; return HttpResponse.json(NO_PURGE); }));
  wrapRows(mixed(), meUser);
  await screen.findByText(A);
  await screen.findByLabelText("연산 필터");
  expect(screen.queryByRole("toolbar")).toBeNull();
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(purgeCalls).toBe(0);
});

test("관리자: 종단 단건만 선택 가능 — 비종단·배치 자식은 disabled + 사유 title", async () => {
  wrapRows(mixed());
  await ready(A);
  expect(box(A)).toBeEnabled();
  expect(box(B)).toBeEnabled();
  expect(box(E)).toBeEnabled();
  expect(box(C)).toBeDisabled();
  expect(box(C).title).toBe("진행 중인 작업은 삭제할 수 없습니다 — 끝난 뒤에 삭제하세요(필요하면 상세에서 먼저 취소)");
  expect(box(D)).toBeDisabled();       // 종단이어도 배치 자식은 개별 삭제 불가
  // 배치 화면으로 보내지 않는다 -- 배치를 지워도 자식 작업 기록은 남고 개별 삭제 경로가 없다(2026-10-09 검증 지적).
  expect(box(D).title).toBe("배치 항목의 작업은 삭제할 수 없습니다 — 배치를 지워도 작업 기록은 남습니다");
  expect(box(A).title).toBe("");
  // 툴바는 미선택에도 자리를 지킨다(안내 + disabled 버튼). 선택 규칙은 늘 있는 별도 줄이고(title 은 hover 전용 --
  // 키보드·터치에선 이 줄이 유일한 설명), 읽혀야 하는 글자라 text-muted(3.54:1)가 아니라 ink/70 이다.
  expect(status()).toHaveTextContent(IDLE);
  expect(status().className).toContain("text-ink/70");
  expect(status().className).not.toContain("text-muted");
  const rule = within(bar()).getByText(RULE);
  expect(rule.className).toContain("text-ink/70");
  expect(screen.getByRole("button", { name: "선택 삭제" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "선택 해제" })).toBeDisabled();
});

test("batch_id 가 빈 문자열 같은 이상값이면 선택 불가(fail-closed)", async () => {
  wrapRows([row(A, { state: "Succeeded", batch_id: "" })]);
  await ready(A);
  expect(box(A)).toBeDisabled();
});

test("전체 선택은 삭제 가능한 행만 담고 indeterminate 가 맞다 — 선택 중 문구에 성공 scan 수", async () => {
  wrapRows(mixed());
  await ready(A);
  expect(allBox().indeterminate).toBe(false);
  await userEvent.click(box(B));
  expect(allBox().indeterminate).toBe(true);
  expect(bar()).toHaveTextContent("1개 선택됨");
  expect(bar()).not.toHaveTextContent("성공 scan");     // 실패 sync 는 사용량과 무관 -- 0 이면 말하지 않는다
  await userEvent.click(allBox());
  expect(box(A)).toBeChecked();
  expect(box(B)).toBeChecked();
  expect(box(E)).toBeChecked();
  expect(box(C)).not.toBeChecked();
  expect(box(D)).not.toBeChecked();
  expect(allBox().indeterminate).toBe(false);
  expect(allBox()).toBeChecked();
  expect(bar()).toHaveTextContent("3개 선택됨(성공 scan 1개)");
  await userEvent.click(allBox());
  expect(status()).toHaveTextContent(IDLE);
  expect(bar()).toHaveTextContent(RULE);              // 규칙 줄은 선택과 무관하게 늘 있다(높이 고정)
});

test("상태 줄은 lg 미만에서 늘 자기 행 + 두 줄 높이 예약 — 문구가 바뀌어도 툴바 높이가 그대로다", async () => {
  // 2026-10-09 검증 지적: 414~768px 에서 긴 미선택 안내만 두 줄로 접혀 첫 체크 순간 툴바가 104→56px 로 줄었다.
  // jsdom 은 배치를 계산하지 않으므로 구조(클래스)를 고정한다 -- 실측은 e2e(06-request-delete).
  wrapRows(mixed());
  await ready(A);
  for (const c of ["basis-full", "min-h-[2.5rem]", "lg:basis-auto", "lg:min-h-0"]) expect(status().className).toContain(c);
  await userEvent.click(box(A));
  for (const c of ["basis-full", "min-h-[2.5rem]"]) expect(status().className).toContain(c);
});

test("필터를 바꾸면 선택이 비워진다(안 보이는 행을 지우는 사고 방지)", async () => {
  wrapRows(mixed());
  await ready(B);
  await userEvent.click(box(B));
  expect(bar()).toHaveTextContent("1개 선택됨");
  await userEvent.selectOptions(screen.getByLabelText("연산 필터"), "sync");
  await waitFor(() => expect(bar()).toHaveTextContent(IDLE));
});

test("상태 필터는 요청 상태만 — 잡 상태(Previewing·ConfirmPending)는 고르면 늘 0건이라 없다", async () => {
  wrapRows(mixed());
  await screen.findByText(A);
  const opts = Array.from((screen.getByLabelText("상태 필터") as HTMLSelectElement).options).map((o) => o.value);
  expect(opts).toEqual(["", "Pending", "Planned", "Running", "Succeeded", "Failed", "Rejected", "Cancelled", "Conflict"]);
});

test("폴링으로 사라진 행은 선택에서 자동 제거(유령 선택 방지)", async () => {
  let rows = mixed();
  const { qc } = wrapRows(() => rows);
  await ready(A);
  await userEvent.click(box(A));
  await userEvent.click(box(B));
  expect(bar()).toHaveTextContent("2개 선택됨(성공 scan 1개)");
  rows = rows.filter((r) => r.request_id !== A);
  await act(async () => { await qc.refetchQueries({ queryKey: ["requests"] }); });
  // 말없이 줄지 않는다(2026-10-09 검증 지적) -- 뺀 수를 알린다.
  await waitFor(() => expect(bar()).toHaveTextContent("1개 선택됨 · 1개는 목록에서 사라져 선택에서 뺐습니다"));
  expect(bar()).not.toHaveTextContent("성공 scan");
  // 남은 하나도 사라지면 선택이 비어도 알림이 남는다(안내 문구로 덮지 않는다).
  rows = rows.filter((r) => r.request_id !== B);
  await act(async () => { await qc.refetchQueries({ queryKey: ["requests"] }); });
  await waitFor(() => expect(bar()).toHaveTextContent("선택한 작업 중 2개는 목록에서 사라져 선택에서 뺐습니다"));
  // 다시 고르기 시작하면 알림은 지워진다.
  await userEvent.click(box(E));
  expect(bar()).toHaveTextContent("1개 선택됨");
  expect(bar()).not.toHaveTextContent("사라져");
});

test("폴링 오류로 표가 내려갔다 돌아와도 전체 선택 칸의 indeterminate 가 살아 있다", async () => {
  let fail = false;
  server.use(
    http.get("/api/user/requests", () => (fail ? HttpResponse.json({ detail: "boom" }, { status: 500 })
                                               : HttpResponse.json(mixed()))),
    http.get("/api/auth/me", () => HttpResponse.json(meAdmin)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><JobsList /></MemoryRouter></QueryClientProvider>);
  await ready(A);
  await userEvent.click(box(B));
  expect(allBox().indeterminate).toBe(true);
  fail = true;
  await act(async () => { await qc.refetchQueries({ queryKey: ["requests"] }); });
  await waitFor(() => expect(screen.queryByLabelText("불러온 작업 중 삭제 가능한 것 전체 선택")).toBeNull());
  fail = false;
  await act(async () => { await qc.refetchQueries({ queryKey: ["requests"] }); });
  await waitFor(() => expect(allBox()).toBeInTheDocument());
  expect(bar()).toHaveTextContent("1개 선택됨");
  expect(allBox().indeterminate).toBe(true);           // 새로 마운트된 input 에도 다시 걸린다(콜백 ref)
});

test("200개를 넘게 고르면 삭제 버튼이 잠기고 「한 번에 200개까지」를 말한다", async () => {
  const many = Array.from({ length: 201 }, (_, i) => row(hex(i + 1), { state: "Succeeded" }));
  wrapRows(many);
  await ready(hex(1));
  await userEvent.click(allBox());
  expect(bar()).toHaveTextContent("201개 선택됨 — 한 번에 200개까지 삭제할 수 있습니다");
  expect(screen.getByRole("button", { name: "선택 삭제" })).toBeDisabled();
  await userEvent.click(box(hex(1)));          // 200개로 줄이면 풀린다
  expect(bar()).toHaveTextContent("200개 선택됨");
  expect(screen.getByRole("button", { name: "선택 삭제" })).toBeEnabled();
});

test("삭제: 확인 창 → POST 한 번 → 「N개 삭제됨 · M개 제외」 + 사유, 결과가 뜰 때 지운 행은 이미 없다", async () => {
  const state = { rows: mixed() as { request_id: string }[] };
  let gets = 0;
  let body: unknown = null;
  server.use(
    http.get("/api/user/requests", async () => {
      gets += 1;
      if (gets > 1) await delay(200);              // 삭제 뒤 재조회만 느리게 -- 결과 문구가 먼저 뜨는 창을 연다
      return HttpResponse.json(state.rows);
    }),
    http.get("/api/auth/me", () => HttpResponse.json(meAdmin)),
    http.post("/api/admin/requests:delete", async ({ request }) => {
      body = await request.json();
      state.rows = state.rows.filter((r) => r.request_id !== A);
      return HttpResponse.json({ deleted: [{ request_id: A, job_ids: ["c".repeat(32)] }],
                                 skipped: [{ request_id: B, reason: "request_recently_finished" }],
                                 purge_pending: 1 });
    }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><JobsList /></MemoryRouter></QueryClientProvider>);
  await ready(A);
  await userEvent.click(box(A));
  await userEvent.click(box(B));
  const before = bar();
  await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
  const dlg = await screen.findByRole("dialog");
  await userEvent.click(within(dlg).getByLabelText("되돌릴 수 없음을 확인했습니다"));
  await userEvent.click(within(dlg).getByRole("button", { name: "2개 영구 삭제" }));
  const result = await screen.findByText("1개 삭제됨 · 1개 제외");
  expect(body).toEqual({ request_ids: [A, B] });
  expect(screen.queryByText(A)).toBeNull();      // 결과 문구 시점엔 행이 이미 사라졌다(onSettled 프라미스 대기)
  expect(screen.getByText(B)).toBeInTheDocument();
  // 조용한 창은 설정값이라 숫자를 말하지 않는다(2026-10-09 검증 지적).
  expect(screen.getByText(`방금 끝난 작업입니다 — 잠시 뒤에 삭제할 수 있습니다 (1개: ${B.slice(0, 12)})`)).toBeInTheDocument();
  expect(box(B)).not.toBeChecked();                  // 제외된 행은 목록에 남지만 선택은 비워졌다
  expect(result.className).toContain("text-bad");
  expect(screen.queryByRole("dialog")).toBeNull();   // 성공(부분 포함)이면 창은 닫히고 결과는 툴바에
  expect(bar()).toBe(before);                        // 툴바는 사라지지도 새로 생기지도 않았다
  // 새로 고르기 시작하면 직전 결과가 지워진다
  await userEvent.click(box(B));
  expect(bar()).toHaveTextContent("1개 선택됨");
  expect(screen.queryByText(/방금 끝난 작업입니다/)).toBeNull();
});

test("부분 성공: 목록 재조회가 늦게 와도 결과 요약이 남고 제외된 행은 선택에서 빠진다(지운 행을 「사라져 뺐다」로 세지 않는다)", async () => {
  // 2026-10-09 검증 지적: 삭제 결과 처리(onDeleted 의 setSelected([]))와 목록 재조회의 렌더 순서에 따라 유령 정리
  // effect 가 [] 를 옛 선택으로 덮어, 상태 줄이 「2개 선택됨 · 1개는 목록에서 사라져 선택에서 뺐습니다」가 되고 제외된
  // 행이 체크된 채 남았다. 재조회 지연을 여러 값으로 흔든다(실 브라우저 재현은 e2e 06 이 한다).
  for (const ms of [0, 5, 30]) {
    const state = { rows: mixed() as { request_id: string }[] };
    let gets = 0;
    server.use(
      http.get("/api/user/requests", async () => {
        gets += 1;
        if (gets > 1) await delay(ms);
        return HttpResponse.json(state.rows);
      }),
      http.get("/api/auth/me", () => HttpResponse.json(meAdmin)),
      http.post("/api/admin/requests:delete", async () => {
        state.rows = state.rows.filter((r) => r.request_id !== A);
        return HttpResponse.json({ deleted: [{ request_id: A, job_ids: [] }],
                                   skipped: [{ request_id: B, reason: "request_recently_finished" },
                                             { request_id: E, reason: "request_job_active" }],
                                   purge_pending: 1 });
      }));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(<QueryClientProvider client={qc}><MemoryRouter><JobsList /></MemoryRouter></QueryClientProvider>);
    await ready(A);
    for (const id of [A, B, E]) await userEvent.click(box(id));
    await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
    const dlg = await screen.findByRole("dialog");
    await userEvent.click(within(dlg).getByLabelText("되돌릴 수 없음을 확인했습니다"));
    await userEvent.click(within(dlg).getByRole("button", { name: "3개 영구 삭제" }));
    await waitFor(() => expect(status()).toHaveTextContent("1개 삭제됨 · 2개 제외"));
    await act(async () => { await delay(ms + 50); });          // 늦은 렌더까지 흘려보낸 뒤에도
    expect(status()).toHaveTextContent("1개 삭제됨 · 2개 제외");
    expect(status()).not.toHaveTextContent("사라져");
    expect(box(B)).not.toBeChecked();
    expect(box(E)).not.toBeChecked();
    view.unmount();
  }
});

test("하나도 지워지지 않으면 「삭제된 작업 없음 · N개 제외」(「0개 삭제됨」이라 하지 않는다)", async () => {
  server.use(http.post("/api/admin/requests:delete", () => HttpResponse.json(
    { deleted: [], skipped: [{ request_id: B, reason: "request_recently_finished" }], purge_pending: 0 })));
  wrapRows(mixed());
  await ready(B);
  await userEvent.click(box(B));
  await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
  const dlg = await screen.findByRole("dialog");
  await userEvent.click(within(dlg).getByLabelText("되돌릴 수 없음을 확인했습니다"));
  await userEvent.click(within(dlg).getByRole("button", { name: "1개 영구 삭제" }));
  const line = await screen.findByText("삭제된 작업 없음 · 1개 제외");
  expect(line.className).toContain("text-bad");
  expect(screen.queryByText(/0개 삭제됨/)).toBeNull();
});

test("전부 삭제되면 「N개 삭제됨」(text-ok)", async () => {
  server.use(http.post("/api/admin/requests:delete", () => HttpResponse.json(
    { deleted: [{ request_id: B, job_ids: [] }], skipped: [], purge_pending: 1 })));
  wrapRows(mixed());
  await ready(B);
  await userEvent.click(box(B));
  await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
  const dlg = await screen.findByRole("dialog");
  await userEvent.click(within(dlg).getByLabelText("되돌릴 수 없음을 확인했습니다"));
  await userEvent.click(within(dlg).getByRole("button", { name: "1개 영구 삭제" }));
  const done = await screen.findByText("1개 삭제됨");
  expect(done.className).toContain("text-ok");
  // 창이 닫히면 포커스는 <body> 가 아니라 결과 줄로 -- 「선택 삭제」는 선택이 비어 잠겼다(2026-10-09 검증 지적).
  await waitFor(() => expect(done).toHaveFocus());
  expect(screen.getByRole("button", { name: "선택 삭제" })).toBeDisabled();
  await userEvent.tab();                                // 다음 Tab 은 표로 이어진다(사이드바로 튀지 않는다)
  expect(allBox()).toHaveFocus();
});

test("창이 열린 동안 선택한 행이 목록에서 사라져 닫으면 포커스는 결과 줄(「…사라져 선택에서 뺐습니다」)로 간다", async () => {
  // 2026-10-09 검증 지적: 폴링이 선택을 비워 「선택 삭제」가 잠긴 뒤 닫기로 닫으면 포커스가 <body> 로 떨어져, 키보드·
  // 화면 낭독기 사용자는 다음 Tab 을 페이지 맨 위(긴 사이드바)부터 다시 시작했다. 들어야 할 것은 바로 그 결과 줄이다.
  let rows = mixed();
  const { qc } = wrapRows(() => rows);
  await ready(A);
  await userEvent.click(box(A));
  await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
  const dlg = await screen.findByRole("dialog");
  rows = rows.filter((r) => r.request_id !== A);         // 다른 관리자가 지웠다
  await act(async () => { await qc.refetchQueries({ queryKey: ["requests"] }); });
  // 모달이 열린 동안 바깥은 aria-hidden 이다(hidden: true 로 찾는다).
  await waitFor(() => expect(screen.getByRole("button", { name: "선택 삭제", hidden: true })).toBeDisabled());
  await userEvent.click(within(dlg).getByRole("button", { name: "닫기" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await waitFor(() => expect(status()).toHaveFocus());
  expect(status()).toHaveTextContent("선택한 작업 중 1개는 목록에서 사라져 선택에서 뺐습니다");
  await userEvent.tab();                                // 다음 Tab 은 표로 이어진다
  expect(allBox()).toHaveFocus();
});

test("정리 자리는 늘 렌더된다 — 모든 폭에서 자기 행(한 줄 예약), 두 버튼은 한 덩어리(정리 대기가 생기고 사라져도 툴바가 그대로)", async () => {
  // 2026-10-09 검증 지적: 360~393px 에서 정리 대기 문구가 버튼 행에 끼어 「선택 해제」가 다음 줄로 밀렸다 -- 삭제마다
  // 툴바가 46px 늘고 백그라운드 정리가 끝나면 줄어 표가 손가락 밑에서 튀었다. 1024~1150px 에서도 선택 문구와 한 행을
  // 나누면 두 행으로 접혔다 -- 그래서 lg 이상도 자기 행이다. jsdom 은 배치를 계산하지 않으므로 구조(클래스)를 고정한다
  // -- 실측은 e2e(06-request-delete).
  let purge: object = NO_PURGE;
  const { qc } = wrapRows(mixed(), meAdmin, () => purge);
  await ready(A);
  const slot = () => bar().querySelector<HTMLElement>("span.order-last")!;
  for (const c of ["basis-full", "min-h-[1rem]"]) expect(slot().className).toContain(c);
  for (const c of ["lg:basis-auto", "lg:min-h-0", "lg:order-none"]) expect(slot().className).not.toContain(c);
  expect(slot()).toHaveTextContent(/^$/);                // 대기 0건 -- 글자는 없고 자리만
  const del = screen.getByRole("button", { name: "선택 삭제" });
  const clear = screen.getByRole("button", { name: "선택 해제" });
  expect(del.parentElement).toBe(clear.parentElement);
  expect(del.parentElement!.className).toContain("shrink-0");
  purge = { pending: 1, stalled: 0, oldest_requested_at: "2026-10-08T00:00:00Z", items: [] };
  await act(async () => { await qc.refetchQueries({ queryKey: ["request-purges"] }); });
  await waitFor(() => expect(slot()).toHaveTextContent("결과 파일·파드 정리 중 1건"));
});

test("정리 상태 줄: 대기 N건 + 지연 M건 — 지연된 행의 사유(reasonText)", async () => {
  wrapRows(mixed(), meAdmin, {
    pending: 2, stalled: 1, oldest_requested_at: "2026-10-08T00:00:00Z",
    items: [
      { request_id: A, stage: "k8s", attempts: 0, last_error: null, requested_at: "2026-10-08T00:00:00Z",
        requested_by: "admin", next_attempt_at: "2026-10-08T00:00:15Z" },
      { request_id: B, stage: "purging", attempts: 3, last_error: "purge_no_node",
        requested_at: "2026-10-08T00:00:01Z", requested_by: "admin", next_attempt_at: "2026-10-08T00:02:00Z" },
    ],
  });
  await ready(A);
  await waitFor(() => expect(bar()).toHaveTextContent("결과 파일·파드 정리 중 2건 · 지연 1건"));
  // 바 안에는 건수만(넓은 화면에서 툴바가 두 줄로 접히지 않게) -- 긴 사유 문장은 바 아래 한 줄로. 사유는 items[0](정상
  // 대기)이 아니라 **지연된** 행의 것이다.
  expect(bar()).not.toHaveTextContent("노드가 없습니다");
  const line = screen.getByText("정리 지연: 결과 파일을 지울 노드가 없습니다 — 에이전트 보고·결과 폴더 마운트를 확인하세요");
  expect(line.className).toContain("break-keep");
});

test("정리 대기 0건이면 정리 상태를 그리지 않는다", async () => {
  wrapRows(mixed());
  await ready(A);
  expect(screen.queryByText(/정리 중/)).toBeNull();
});


test("제외 사유는 사유별 한 줄(개수 + 앞 3개 id) -- 대부분 제외돼도 표를 밀지 않는다", async () => {
  const { skippedGroups } = await import("./JobsList");
  const ids = ["a", "b", "c", "d", "e"].map((x) => x.repeat(32));
  const groups = skippedGroups([
    ...ids.map((request_id) => ({ request_id, reason: "request_recently_finished" })),
    { request_id: "f".repeat(32), reason: "request_not_deletable" },
  ]);
  expect(groups.map(([r, l]) => [r, l.length])).toEqual([["request_recently_finished", 5], ["request_not_deletable", 1]]);
});
