import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse, delay } from "msw";
import { beforeAll, afterAll, afterEach, test, expect, vi } from "vitest";
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
const ALL = "불러온 단일 작업 중 삭제 가능한 것 전체 선택(배치 제외)";
const allBox = () => screen.getByLabelText(ALL) as HTMLInputElement;
/** 툴바 아래 제외 사유 줄(<p>) -- id 가 상세 링크라 글자가 여러 노드로 나뉜다. textContent 전체로 찾는다. */
const skipLine = (text: string) =>
  screen.getByText((_, el) => el?.tagName === "P" && el.textContent === text) as HTMLParagraphElement;
const bar = () => screen.getByRole("toolbar", { name: "작업 일괄 처리" });
const status = () => within(bar()).getByRole("status");
const IDLE = "끝난 작업을 선택해 삭제할 수 있습니다";
const RULE = "진행 중인 작업은 선택할 수 없습니다 · 배치는 배치 단위로 선택(못 고르면 배치 칸에 이유)";

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
  // 요약이 없는 배치 자식(구형 응답)은 배치 단위로도 고를 수 없다 -- 「배치 단위로 선택해 삭제하세요」로 보내지 않고 고를
  // 수 없는 이유를 그대로 말한다(2026-10-10 검증 지적).
  expect(box(D).title).toBe("배치 정보를 확인할 수 없어 선택할 수 없습니다");
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
  await waitFor(() => expect(screen.queryByLabelText(ALL)).toBeNull());
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
  expect(body).toEqual({ request_ids: [A, B], batches: [] });   // 단건만이면 배치는 빈 목록
  expect(screen.queryByText(A)).toBeNull();      // 결과 문구 시점엔 행이 이미 사라졌다(onSettled 프라미스 대기)
  expect(screen.getByText(B)).toBeInTheDocument();
  // 조용한 창은 설정값이라 숫자를 말하지 않는다(2026-10-09 검증 지적).
  const line = skipLine(`방금 끝난 작업입니다 — 잠시 뒤에 삭제할 수 있습니다 (1개: ${B.slice(0, 12)})`);
  // id 는 상세 링크다(2026-10-10 검증 지적 -- 12자로는 상세 URL 을 만들 수 없고 필터를 바꾸면 이 줄이 지워진다).
  expect(within(line).getByRole("link", { name: B.slice(0, 12) })).toHaveAttribute("href", `/jobs/${B}`);
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

// --- 배치 단위 삭제 + 「배치」 열(2026-10-10, 관리자 전용) ---------------------------------------------------------
// 배치 자식 행에는 목록 API 가 서버 요약(batch_*)을 붙인다 -- 수는 서버가 센 값(목록 한 쪽엔 자식이 일부만 있다).
const BX = "b".repeat(32), BY = "c".repeat(32), BZ = "d".repeat(32);
const F = hex(6), G = hex(7), H = hex(8), J = hex(9);
/** 배치 자식 행. 기본 = 종단 sync 배치(Completed · 이름 「e7 배치」 · 서버 수 7 · 끝나지 않은 자식 0 · 성공 scan 0). */
function child(id: string, bid: string, over: Record<string, unknown> = {}) {
  return row(id, { batch_id: bid, state: "Succeeded", batch_exists: true, batch_name: "e7 배치",
                   batch_status: "Completed", batch_request_count: 7, batch_live_request_count: 0,
                   batch_succeeded_scan_count: 0, ...over });
}
/** 체크박스(배치 묶음 행은 aria-label 이 「… 선택 — 배치 X 전체(작업 N개)」로 길다 -- 앞부분으로 찾는다). */
const anyBox = (id: string) =>
  screen.getByLabelText(new RegExp(`^작업 ${id.slice(0, 12)} 선택`)) as HTMLInputElement;
const rowOf = (id: string) => screen.getByText(id).closest("tr")!;
/** ready 의 배치 묶음판(관리자 판정이 도착해 체크 열이 생길 때까지). */
async function readyAny(id: string) {
  await screen.findByText(id);
  await waitFor(() => expect(anyBox(id)).toBeInTheDocument());
}

test("배치 열(관리자): 이름 링크·이름 없으면 id 12자 링크·기록 없는 묶음은 그 묶음만 거른 목록 링크, 단일 작업 「—」, 이상값", async () => {
  wrapRows([
    row(A),
    child(B, BX),
    child(C, BY, { batch_name: null }),
    child(D, BZ, { batch_exists: false, batch_name: null, batch_status: null }),
    row(E, { batch_id: "" }),
  ]);
  await ready(A);
  const heads = within(screen.getByRole("table")).getAllByRole("columnheader").map((th) => th.textContent);
  // 「요청」 바로 다음이 「배치」, 시각 열은 머리줄이 시간대를 말한다.
  expect(heads.slice(1, 4)).toEqual(["요청", "배치", "요청자"]);
  expect(heads.slice(-2)).toEqual(["생성(KST)", "갱신(KST)"]);
  const named = within(rowOf(B)).getByRole("link", { name: "e7 배치" });
  expect(named).toHaveAttribute("href", `/admin/batches/${BX}`);
  expect(named.title).toBe(`e7 배치 · ${BX}`);
  // 폭: 모든 폭에서 6rem 고정(1280 예산 -- 1440px 이상의 12rem 은 대상 열을 HEAD 보다 120px 깎았다, 2026-10-11 검증
  // 지적). 짧은 이름은 한 줄.
  for (const cls of ["w-[6rem]", "min-w-[6rem]", "truncate"]) expect(named.className).toContain(cls);
  expect(named.className).not.toMatch(/min-\[1440px\]/);
  // 열 폭은 머리칸이 예약한다(내용과 무관 -- 첫 배치 행이 폴링으로 들어와도 표가 다시 배치되지 않는다). 예약 폭 =
  // 칸 내용 6rem + pr-2 + UA 왼쪽 여백 1px(2026-10-11 검증 지적: 1px 을 빼먹은 6.5rem 은 링크 칸보다 짧아 첫 배치 행이
  // 열을 1px 넓혔다 -- 실폭 동일성은 e2e E7 이 잰다).
  const th = within(screen.getByRole("table")).getAllByRole("columnheader")[2];
  for (const cls of ["w-[calc(6.5rem_+_1px)]", "min-w-[calc(6.5rem_+_1px)]", "pr-2"]) expect(th.className).toContain(cls);
  const unnamed = within(rowOf(C)).getByRole("link", { name: BY.slice(0, 12) });
  expect(unnamed).toHaveAttribute("href", `/admin/batches/${BY}`);
  expect(unnamed.className).toContain("font-mono");
  expect(unnamed.title).toBe(`이름 없음 · ${BY}`);
  // 기록 없는 묶음: 상세(404) 대신 그 묶음의 작업만 거른 이 목록으로. 「삭제됨」은 좁은 칸에서 작업 자체가 지워졌다는
  // 말로 읽혔다(2026-10-11 검증 지적) -- 「기록 없음」. id 는 확인 창·결과 줄과 같은 12자.
  const gone = within(rowOf(D)).getByRole("link", { name: `기록 없음 ${BZ.slice(0, 12)}` });
  expect(gone).toHaveAttribute("href", `/jobs?batch=${BZ}`);
  expect(gone.title).toBe(`배치 기록 없음 · ${BZ} — 이 묶음의 작업만 보기`);
  expect(rowOf(D)).not.toHaveTextContent("삭제됨");
  expect(within(rowOf(A)).getByText("—")).toBeInTheDocument();
  expect(within(rowOf(E)).getByText("배치(이상값)")).toBeInTheDocument();
  // 시각 칸에는 「 KST」 접미사가 없다(머리줄로 옮겼다). 그 접미사가 하던 구분자 노릇은 생성 칸의 오른쪽 여백이 한다
  // (2026-10-11 검증 지적: 두 시각이 2px 로 붙어 한 문자열로 읽혔다). 8px(pr-2) -- 12px 는 1280 에서 대상 열을 더 깎았다.
  const created = within(rowOf(A)).getByText("2026-08-22 09:00:00");
  expect(created.className).toContain("pr-2");
  expect(rowOf(A)).not.toHaveTextContent("KST");
});

test("긴 배치 이름은 끝(가르는 부분)을 늘 보인다 -- 앞 조각 말줄임 + 뒤 조각의 두 줄(모든 폭)", async () => {
  // 2026-10-11 검증 지적: 두 줄 line-clamp 는 「2026-10 프로젝트 A 아카이브 이관(1차)」·「…(2차)」를 둘 다 「2026-10 /
  // 프로젝트 A…」로 보여 구별하지 못했다(그 열의 목적이 식별이다). 넓은 화면의 한 줄(12rem flex)은 대상 열을 깎아 단일
  // 작업 행을 HEAD 보다 높였다(같은 날 검증 지적) -- 모든 폭에서 6rem 두 줄이다.
  wrapRows([row(A), child(B, BX, { batch_name: "2026-10 프로젝트 A 아카이브 이관(1차)" }),
            child(C, BY, { batch_name: "2026-10 프로젝트 A 아카이브 이관(2차)" })]);
  await ready(A);
  const first = within(rowOf(B)).getByRole("link", { name: "2026-10 프로젝트 A 아카이브 이관(1차)" });
  const second = within(rowOf(C)).getByRole("link", { name: "2026-10 프로젝트 A 아카이브 이관(2차)" });
  expect(within(first).getByText("이관(1차)")).toBeInTheDocument();
  expect(within(second).getByText("이관(2차)")).toBeInTheDocument();
  const head = within(first).getByText("2026-10 프로젝트 A 아카이브");
  const tail = within(first).getByText("이관(1차)");
  for (const part of [head, tail]) expect(part.className).toContain("block");
  expect(head.className).toContain("truncate");
  // 뒤 조각은 text-xs -- 칸이 두 줄(20 + 16px)로 체크박스 행(39px) 안에 들어간다(사유 줄과 같은 높이라 상태가 바뀌어도
  // 행 높이가 그대로다 -- 아래 「두 줄 이하」 테스트).
  expect(tail.className).toContain("text-xs");
  expect(head.className).not.toContain("text-xs");
  expect(first.className).not.toContain("flex");
});

test("배치 칸은 상태와 무관하게 두 줄 이하 -- 막히면 사유 줄이 뒤 조각·id 줄을 **대신**한다(배치가 끝나거나 다시 돌아도 행 높이 그대로)", async () => {
  // 2026-10-11 검증 지적: 사유 줄을 덧붙이자 긴 이름 배치가 막혔을 때 세 줄(59px), 고를 수 있을 때 두 줄(43px)이 돼
  // Running → Completed(관리자가 지우려고 기다리는 순간) 폴링 한 번에 그 배치의 모든 행이 줄어 표가 포인터 밑에서 최대
  // 수백 px 움직였다. 실 기하(행 높이 동일)는 e2e E7 이 잰다 -- 여기선 줄 구조를 고정한다.
  const LONG = "2026-10 프로젝트 A 아카이브 이관(1차)";
  let rows = [
    row(A),
    child(B, BX, { batch_name: LONG, batch_status: "Running" }),
    child(C, BZ, { batch_exists: false, batch_name: null, batch_status: null, batch_live_request_count: 1 }),
    child(D, BY, { batch_name: null, batch_status: "Running" }),
  ];
  const { qc } = wrapRows(() => rows);
  await ready(A);
  const cell = (id: string) => rowOf(id).querySelectorAll("td")[2] as HTMLTableCellElement;
  /** 칸의 보이는 줄(블록 자식 -- 링크 안의 블록 조각 또는 링크 자체 + 사유 줄). sr-only 는 흐름 밖이라 세지 않는다. */
  const lines = (id: string) => {
    const link = cell(id).querySelector("a")!;
    const inner = [...link.children].filter((el) => el.className.includes("block"));
    const linkLines = inner.length > 0 ? inner.map((el) => el.textContent)
      : link.className.includes("truncate") ? [link.textContent] : null;
    const why = document.getElementById(`batch-why-${id}`);
    const whyLine = why === null ? [] : [why.childNodes[0].textContent];
    return { linkLines, whyLine };
  };
  // 막힌 동안: 긴 이름 = 앞 조각 + 사유(뒤 조각은 가려지고 링크 이름·title 에 남는다).
  expect(lines(B)).toEqual({ linkLines: ["2026-10 프로젝트 A 아카이브"], whyLine: ["배치 진행 중"] });
  expect(within(rowOf(B)).getByRole("link", { name: LONG }).title).toBe(`${LONG} · ${BX}`);
  expect(within(rowOf(B)).queryByText("이관(1차)")).toBeNull();
  // 기록 없는 묶음 = 「기록 없음」 + 사유(id 는 링크 이름·title 에 남는다).
  const gone = within(rowOf(C)).getByRole("link", { name: `기록 없음 ${BZ.slice(0, 12)}` });
  expect(gone.textContent).toBe("기록 없음");
  expect(document.getElementById(`batch-why-${C}`)!.childNodes[0].textContent).toBe("미완료 작업 있음");
  // 이름 없는 배치 = id 한 줄 + 사유.
  expect(lines(D)).toEqual({ linkLines: [BY.slice(0, 12)], whyLine: ["배치 진행 중"] });
  for (const id of [B, C, D]) expect(document.getElementById(`batch-why-${id}`)!.className).toContain("truncate");

  // 폴링으로 전부 고를 수 있게 되면: 사유 줄 자리에 뒤 조각·id 가 선다(줄 수는 그대로 둘).
  rows = [
    row(A),
    child(B, BX, { batch_name: LONG }),
    child(C, BZ, { batch_exists: false, batch_name: null, batch_status: null }),
    child(D, BY, { batch_name: null }),
  ];
  await act(async () => { await qc.refetchQueries({ queryKey: ["requests"] }); });
  await waitFor(() => expect(anyBox(B)).toBeEnabled());
  expect(lines(B)).toEqual({ linkLines: ["2026-10 프로젝트 A 아카이브", "이관(1차)"], whyLine: [] });
  expect(within(rowOf(C)).getByRole("link", { name: `기록 없음 ${BZ.slice(0, 12)}` }).textContent)
    .toBe(`기록 없음 ${BZ.slice(0, 12)}`);
  expect(lines(D)).toEqual({ linkLines: [BY.slice(0, 12)], whyLine: [] });
});

test("고를 수 없는 배치는 배치 칸 둘째 줄에 사유가 **보이고** 체크박스가 그 줄을 가리킨다(aria-describedby)", async () => {
  // 2026-10-11 검증 지적: 사유가 비활성 체크박스의 title(마우스 hover)뿐이라 키보드(disabled 는 Tab 이 건너뛴다)·터치
  // 사용자는 Succeeded 행의 체크박스가 왜 잠겼는지 알 수 없었다(1000개 초과·다른 쪽에 Pending 자식이 남은 Cancelled).
  wrapRows([
    child(A, BX, { batch_status: "Running" }),
    child(B, BY, { batch_status: "Cancelled", batch_live_request_count: 1 }),
    child(C, BZ, { batch_request_count: 1001, batch_request_count_capped: true, batch_live_request_count: null,
                   batch_succeeded_scan_count: null }),
    child(D, "e".repeat(32)),
    // 자식은 1개뿐인데 항목이 상한(10000)을 넘는 배치(큰 CSV 를 일찍 취소) -- 서버만 거부하던 것을 미리 잠근다(2026-10-11).
    child(E, "f".repeat(32), { batch_status: "Cancelled", batch_request_count: 1, batch_item_count: 10001 }),
  ]);
  await ready(A);
  const why = (id: string) => document.getElementById(`batch-why-${id}`)!;
  /** 보이는 짧은 사유(줄의 첫 글자 노드 -- 뒤의 sr-only 처방은 빼고). */
  const label = (id: string) => why(id).childNodes[0].textContent;
  // live 는 「미완료 작업 있음」 -- 「작업 진행 중」은 같은 줄의 초록 Succeeded 와 모순돼 「이 작업이 진행 중」으로 읽혔다
  // (2026-10-11 검증 지적). 상한 초과는 자식·항목 어느 쪽이든 「삭제 상한 초과」(긴 말은 title).
  expect([A, B, C, E].map(label)).toEqual(["배치 진행 중", "미완료 작업 있음", "삭제 상한 초과", "삭제 상한 초과"]);
  expect(why(E).title).toBe("작업이 1000개 또는 항목이 10000개를 넘는 배치는 아직 삭제할 수 없습니다");
  for (const id of [A, B, C, E]) {
    expect(box(id)).toBeDisabled();
    expect(rowOf(id)).toContainElement(why(id));
    expect(why(id).className).toContain("text-ink/70");
    // 체크박스 설명 = 짧은 사유 + 처방(긴 말). describedby 가 있으면 title 은 설명이 아니라서, 예전엔 「삭제 상한 초과」만
    // 들리고 어떤 상한인지는 마우스 전용이었다(2026-10-11 검증 지적). 처방은 화면 낭독기 전용 글자(sr-only -- 보이는 줄의
    // 높이는 그대로).
    expect(box(id)).toHaveAccessibleDescription(`${label(id)} — ${why(id).title}`);
    expect(why(id).querySelector(".sr-only")).toHaveTextContent(why(id).title);
  }
  expect(box(C)).toHaveAccessibleDescription(
    "삭제 상한 초과 — 작업이 1000개 또는 항목이 10000개를 넘는 배치는 아직 삭제할 수 없습니다");
  expect(box(A)).toHaveAccessibleDescription(
    "배치 진행 중 — 진행 중인 배치입니다 — 배치가 끝난 뒤에(필요하면 배치 상세에서 먼저 취소) 배치 단위로 삭제하세요");
  expect(why(C).title).toBe("작업이 1000개 또는 항목이 10000개를 넘는 배치는 아직 삭제할 수 없습니다");
  // 고를 수 있는 배치엔 사유 줄이 없다.
  expect(document.getElementById(`batch-why-${D}`)).toBeNull();
  expect(anyBox(D)).toBeEnabled();
});

test("비관리자: 배치 열이 없다(머리줄 8칸)", async () => {
  wrapRows([row(A), child(B, BX)], meUser);
  await screen.findByText(A);
  await screen.findByLabelText("연산 필터");
  const heads = within(screen.getByRole("table")).getAllByRole("columnheader").map((th) => th.textContent);
  expect(heads).toEqual(["요청", "요청자", "작업", "대상", "우선순위", "상태", "생성(KST)", "갱신(KST)"]);
  expect(screen.queryByRole("link", { name: "e7 배치" })).toBeNull();
  // 요청 id 칸은 배치 열에 2rem 을 내준 관리자만 8rem -- 배치 열이 없는 일반 사용자는 예전 10rem(2026-10-11 검증 지적).
  expect(screen.getByRole("link", { name: A }).className).toContain("max-w-[10rem]");
});

test("관리자의 요청 id 칸은 6.5rem(배치 열 자리 -- 1280 에서 대상 열이 HEAD 이상)", async () => {
  wrapRows([row(A)]);
  await ready(A);
  expect(screen.getByRole("link", { name: A }).className).toContain("max-w-[6.5rem]");
  // 배치 행이 없어도 배치 열 폭은 예약돼 있다(머리칸 고정 폭).
  const th = within(screen.getByRole("table")).getAllByRole("columnheader")[2];
  expect(th).toHaveTextContent("배치");
  expect(th.className).toContain("w-[calc(6.5rem_+_1px)]");
});

test("?batch=<id> 이면 그 배치의 작업만 서버에 묻고, 무엇으로 걸렀는지·푸는 버튼을 보인다 -- 바뀌면 선택을 비운다", async () => {
  // 2026-10-11 검증 지적: 배치 상세가 「전체 작업에서 이 배치의 작업을 골라」라고 보내는데 목록엔 배치 필터·링크가 없어
  // 오래된 배치의 작업(무한 스크롤 수십 쪽 아래)을 찾을 길이 없었다.
  const urls: string[] = [];
  server.use(
    http.get("/api/user/requests", ({ request }) => {
      urls.push(request.url);
      const b = new URL(request.url).searchParams.get("batch_id");
      return HttpResponse.json(b === BX ? [child(B, BX), child(C, BX)]
        : [row(A), child(B, BX), child(C, BX), child(D, BZ, { batch_exists: false, batch_name: null, batch_status: null })]);
    }),
    http.get("/api/auth/me", () => HttpResponse.json(meAdmin)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={[`/jobs?batch=${BX}`]}>
    <JobsList /></MemoryRouter></QueryClientProvider>);
  await readyAny(B);
  expect(new URL(urls[urls.length - 1]).searchParams.get("batch_id")).toBe(BX);
  expect(screen.queryByText(A)).toBeNull();
  expect(screen.getByText("배치 e7 배치의 작업만")).toBeInTheDocument();
  await userEvent.click(anyBox(B));
  expect(status()).toHaveTextContent("배치 1개 선택됨");
  // 칩의 잘린 이름은 hover 로 전체를 볼 수 있다(title = 이름 + id -- 2026-10-11 검증 지적: id 만이었다).
  expect(screen.getByText("배치 e7 배치의 작업만").title).toBe(`e7 배치 · ${BX}`);
  // 키보드로 누르면(click detail 0) 칩째 사라진 버튼의 포커스는 <body> 가 아니라 늘 있는 바로 앞 필터로 간다(2026-10-11
  // 검증 지적 -- 키보드·화면 낭독기 사용자가 자리를 잃었다).
  screen.getByRole("button", { name: "배치 필터 해제" }).focus();
  await userEvent.keyboard("{Enter}");
  expect(screen.queryByRole("button", { name: "배치 필터 해제" })).toBeNull();
  expect(document.activeElement).toBe(screen.getByLabelText("상태 필터"));
  await screen.findByText(A);
  expect(new URL(urls[urls.length - 1]).searchParams.get("batch_id")).toBeNull();
  expect(screen.queryByText("배치 e7 배치의 작업만")).toBeNull();
  await waitFor(() => expect(status()).toHaveTextContent(IDLE));                  // 필터가 바뀌면 선택을 비운다
  // 기록 없는 묶음 칸은 그 묶음만 거른 목록으로 간다(같은 화면 -- 주소만 바뀐다).
  await userEvent.click(anyBox(B));
  await userEvent.click(within(rowOf(D)).getByRole("link", { name: `기록 없음 ${BZ.slice(0, 12)}` }));
  await waitFor(() => expect(new URL(urls[urls.length - 1]).searchParams.get("batch_id")).toBe(BZ));
  await waitFor(() => expect(status()).toHaveTextContent(IDLE));
  expect(screen.getByText(`배치 ${BZ.slice(0, 12)}의 작업만`)).toBeInTheDocument();
  expect(screen.getByText(`배치 ${BZ.slice(0, 12)}의 작업만`).title).toBe(`${BZ.slice(0, 12)} · ${BZ}`);
  // 마우스로 누르면(click detail ≥ 1) 포커스를 옮기지 않는다 -- Chrome 은 프로그램 포커스를 받은 select 에 직전 입력과
  // 무관하게 :focus-visible 링을 그려, 마우스 사용자에게도 상태 필터에 굵은 링이 남았다(2026-10-11 검증 지적).
  const stateFocus = vi.spyOn(screen.getByLabelText("상태 필터"), "focus");
  await userEvent.click(screen.getByRole("button", { name: "배치 필터 해제" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "배치 필터 해제" })).toBeNull());
  expect(document.activeElement).not.toBe(screen.getByLabelText("상태 필터"));
  expect(stateFocus).not.toHaveBeenCalled();
});

test("키보드로 고른 배치가 폴링으로 잠기면 포커스는 <body> 가 아니라 그 행의 요청 링크로 -- 스크롤하지 않는다", async () => {
  // 2026-10-11 검증 지적(두 번): 포커스를 쥔 체크박스가 disabled 가 되면 브라우저가 포커스를 <body> 로 떨어뜨려 화면
  // 낭독기가 자리를 잃었다. 그 고침으로 상태 줄(툴바 -- 맨 위)로 focus() 하자 표 40번째 행을 읽던 화면이 맨 위로 튀었다.
  // 행이 남아 있으면 그 행의 요청 링크로(표 안의 자리), preventScroll. 무슨 일이 있었는지는 상태 줄 라이브 리전이 말한다.
  const focus = vi.spyOn(HTMLElement.prototype, "focus");
  try {
    let rows = [row(A), child(B, BX), child(C, BX)];
    const { qc } = wrapRows(() => rows);
    await ready(A);
    anyBox(B).focus();
    await userEvent.keyboard(" ");
    expect(anyBox(B)).toBeChecked();
    focus.mockClear();
    rows = [row(A), child(B, BX, { batch_status: "Running" }), child(C, BX, { batch_status: "Running" })];
    await act(async () => { await qc.refetchQueries({ queryKey: ["requests"] }); });
    const link = within(rowOf(B)).getByRole("link", { name: B });
    await waitFor(() => expect(document.activeElement).toBe(link));
    expect(focus).toHaveBeenCalledWith({ preventScroll: true });
    expect(focus.mock.calls.every(([opts]) => (opts as FocusOptions | undefined)?.preventScroll === true)).toBe(true);
    expect(status()).toHaveTextContent("배치 1개 빠짐(삭제 불가)");
  } finally {
    focus.mockRestore();
  }
});

test("고른 배치가 폴링으로 목록에서 사라지면(다른 관리자가 지움) 포커스는 상태 줄로 -- 스크롤하지 않는다", async () => {
  const focus = vi.spyOn(HTMLElement.prototype, "focus");
  try {
    let rows = [row(A), child(B, BX)];
    const { qc } = wrapRows(() => rows);
    await ready(A);
    anyBox(B).focus();
    await userEvent.keyboard(" ");
    focus.mockClear();
    rows = [row(A)];
    await act(async () => { await qc.refetchQueries({ queryKey: ["requests"] }); });
    await waitFor(() => expect(document.activeElement).toBe(status()));
    expect(focus).toHaveBeenCalledWith({ preventScroll: true });
    expect(status()).toHaveTextContent("배치 1개 빠짐(목록에서 사라짐)");
  } finally {
    focus.mockRestore();
  }
});

test("배치 체크박스를 누른 뒤 빈 곳을 눌러 포커스를 놓았으면(relatedTarget 없는 blur) 잠겨도 포커스를 옮기지 않는다", async () => {
  // 2026-10-11 검증 지적: 「빈 곳 클릭」의 blur 도 relatedTarget 이 null 이라 「disabled 로 빠짐」으로 오인해, 스크롤해
  // 내려가 읽던 화면을 폴링이 맨 위로 끌어올렸다. 사용자가 일으킨 blur 면 기억을 지운다.
  const focus = vi.spyOn(HTMLElement.prototype, "focus");
  try {
    let rows = [row(A), child(B, BX)];
    const { qc } = wrapRows(() => rows);
    await ready(A);
    await userEvent.click(anyBox(B));
    expect(document.activeElement).toBe(anyBox(B));
    anyBox(B).blur();                                  // 빈 곳 클릭 -- relatedTarget null
    expect(document.activeElement).toBe(document.body);
    focus.mockClear();
    rows = [row(A), child(B, BX, { batch_status: "Running" })];
    await act(async () => { await qc.refetchQueries({ queryKey: ["requests"] }); });
    await waitFor(() => expect(box(B)).toBeDisabled());
    expect(document.activeElement).toBe(document.body);
    expect(focus).not.toHaveBeenCalled();
  } finally {
    focus.mockRestore();
  }
});

test("포커스가 이미 다른 칸으로 옮겨 갔으면 배치가 잠겨도 포커스를 빼앗지 않는다", async () => {
  let rows = [row(A), child(B, BX)];
  const { qc } = wrapRows(() => rows);
  await ready(A);
  anyBox(B).focus();
  await userEvent.keyboard(" ");
  box(A).focus();                                    // 다른 칸으로(relatedTarget 이 있는 blur)
  rows = [row(A), child(B, BX, { batch_status: "Running" })];
  await act(async () => { await qc.refetchQueries({ queryKey: ["requests"] }); });
  await waitFor(() => expect(box(B)).toBeDisabled());
  expect(document.activeElement).toBe(box(A));
});

test("배치 묶음 선택: 한 행을 체크하면 같은 배치의 불러온 행이 모두 체크되고, 툴바는 서버가 센 수(7 ≠ 불러온 3)를 말한다", async () => {
  wrapRows([row(A), child(B, BX), child(C, BX), child(D, BX), child(E, BY, { batch_name: "다른 배치" })]);
  await ready(A);
  expect(anyBox(B)).toBeEnabled();
  expect(anyBox(B)).toHaveAccessibleName(`작업 ${B.slice(0, 12)} 선택 — 배치 e7 배치 전체(작업 7개)`);
  expect(anyBox(B).title).toBe("배치 단위로 선택됩니다 — 이 배치의 작업 7개(재실행 이력 포함)와 배치 기록이 함께 삭제됩니다");
  await userEvent.click(anyBox(C));
  for (const id of [B, C, D]) expect(anyBox(id)).toBeChecked();
  expect(anyBox(E)).not.toBeChecked();                      // 다른 배치는 그대로
  expect(box(A)).not.toBeChecked();
  for (const id of [B, C, D]) expect(rowOf(id).className).toContain("bg-accent/5");   // 묶음 선택의 시각 신호
  expect(rowOf(E).className).not.toContain("bg-accent/5");
  expect(status()).toHaveTextContent("배치 1개 선택됨(작업 7개)");
  expect(screen.getByRole("button", { name: "선택 삭제" })).toBeEnabled();
  // 섞임: 단건 1 + 배치 2
  await userEvent.click(box(A));
  await userEvent.click(anyBox(E));
  expect(status()).toHaveTextContent("1개 + 배치 2개(작업 14개) 선택됨");
  // 같은 배치의 다른 행으로 다시 누르면 묶음째 풀린다.
  await userEvent.click(anyBox(D));
  for (const id of [B, C, D]) expect(anyBox(id)).not.toBeChecked();
  expect(status()).toHaveTextContent("1개 + 배치 1개(작업 7개) 선택됨");
});

test("배치 묶음의 성공 scan 수(서버 수)가 툴바 문구에 더해진다", async () => {
  wrapRows([row(A, { operation: "scan", payload: { storage: "s1", target: "t1" } }),
            child(B, BX, { operation: "scan", payload: { storage: "s1", target: "t2" }, batch_request_count: 4,
                           batch_succeeded_scan_count: 4 })]);
  await ready(A);
  await userEvent.click(anyBox(B));
  expect(status()).toHaveTextContent("배치 1개 선택됨(작업 4개 · 성공 scan 4개)");
  await userEvent.click(box(A));
  expect(status()).toHaveTextContent("1개 + 배치 1개(작업 4개) 선택됨 · 성공 scan 5개");
});

test("고를 수 없는 배치 묶음: 진행 중·끝나지 않은 작업·1000개 초과·요약 없음 -- disabled + 상태별 사유 title", async () => {
  wrapRows([
    child(A, BX, { batch_status: "Running" }),
    child(B, BY, { batch_status: "Cancelled", batch_live_request_count: 1 }),     // 취소 경합으로 남은 Pending 자식
    child(C, BZ, { batch_request_count: 1001 }),
    row(D, { batch_id: "e".repeat(32) }),                                         // 요약 없음(구형 응답)
    child(E, "f".repeat(32), { batch_status: "PreviewReady" }),
  ]);
  await ready(A);
  for (const id of [A, B, C, D, E]) {
    expect(box(id)).toBeDisabled();                       // 묶음 이름이 붙지 않는다(고를 수 없는 행)
    expect(box(id)).not.toBeChecked();
  }
  expect(box(A).title).toBe("진행 중인 배치입니다 — 배치가 끝난 뒤에(필요하면 배치 상세에서 먼저 취소) 배치 단위로 삭제하세요");
  // 확인 대기(PreviewReady)는 스스로 끝나지 않는다 -- 「끝난 뒤에」가 아니라 할 일(확인·취소)을 말하고, 짧은 사유는 배치
  // 화면의 「확인 대기」와 같은 말이다(2026-10-11 검증 지적: 「배치 진행 중」으로 보였다).
  expect(box(E).title).toBe("확인 대기 중인 배치입니다 — 배치 상세에서 확인하거나 취소한 뒤 끝나면 배치 단위로 삭제하세요");
  expect(document.getElementById(`batch-why-${E}`)!.childNodes[0].textContent).toBe("배치 확인 대기");
  expect(document.getElementById(`batch-why-${A}`)!.childNodes[0].textContent).toBe("배치 진행 중");
  expect(box(B).title).toBe("끝나지 않은 작업이 남은 배치입니다 — 모두 끝난 뒤 배치 단위로 삭제하세요");
  expect(box(C).title).toBe("작업이 1000개 또는 항목이 10000개를 넘는 배치는 아직 삭제할 수 없습니다");
  expect(box(D).title).toBe("배치 정보를 확인할 수 없어 선택할 수 없습니다");
});

test("요약은 「끝나지 않은 작업 0」인데 불러온 행이 Running 이면 묶음을 고를 수 없다(행 상태로 한 번 더)", async () => {
  // 2026-10-10 검증 지적: 목록 API 는 행 SELECT 와 요약 집계를 한 트랜잭션으로 묶지 않아, 그 사이 자식이 끝나면 같은
  // 응답 안에서 행은 Running 인데 요약은 0 이 된다 -- Running 행의 체크박스가 켜져 「진행 중인 작업은 선택할 수 없습니다」
  // 와 모순됐다(다음 폴링까지).
  wrapRows([child(A, BX, { state: "Running" }), child(B, BX), row(C)]);
  await ready(C);
  for (const id of [A, B]) {
    expect(box(id)).toBeDisabled();
    expect(box(id).title).toBe("끝나지 않은 작업이 남은 배치입니다 — 모두 끝난 뒤 배치 단위로 삭제하세요");
  }
});

test("배치 기록만 지워진 묶음(배치 행 없음)도 묶음으로 고른다 — 상태가 없어도 끝난 작업뿐이면 된다", async () => {
  const gone = { batch_exists: false, batch_name: null, batch_status: null, batch_request_count: 2 };
  wrapRows([child(A, BX, gone), child(B, BX, gone)]);
  await readyAny(A);
  expect(anyBox(A)).toBeEnabled();
  expect(anyBox(A).title)
    .toBe("배치 단위로 선택됩니다 — 배치 기록이 없는 묶음의 작업 2개(재실행 이력 포함)가 함께 삭제됩니다");
  await userEvent.click(anyBox(A));
  expect(anyBox(B)).toBeChecked();
  expect(status()).toHaveTextContent("배치 1개 선택됨(작업 2개)");
});

test("헤더 전체 선택은 단건만 — 배치만 고르면 헤더는 indeterminate, 전체 선택을 눌러도 배치 선택은 그대로", async () => {
  wrapRows([row(A), row(B, { state: "Failed" }), child(C, BX), child(D, BX)]);
  await ready(A);
  await userEvent.click(anyBox(C));
  expect(allBox().indeterminate).toBe(true);
  expect(allBox()).not.toBeChecked();
  await userEvent.click(allBox());
  expect(box(A)).toBeChecked();
  expect(box(B)).toBeChecked();
  expect(anyBox(C)).toBeChecked();                         // 묶음은 건드리지 않는다
  expect(allBox().indeterminate).toBe(false);
  expect(status()).toHaveTextContent("2개 + 배치 1개(작업 7개) 선택됨");
  await userEvent.click(allBox());                         // 단건만 풀린다
  expect(box(A)).not.toBeChecked();
  expect(anyBox(D)).toBeChecked();
  expect(status()).toHaveTextContent("배치 1개 선택됨(작업 7개)");
});

test("폴링으로 배치가 다시 활성(재실행)이 되면 선택에서 빠지고 그 사실을 알린다", async () => {
  let rows = [row(A), child(B, BX), child(C, BX)];
  const { qc } = wrapRows(() => rows);
  await ready(A);
  await userEvent.click(anyBox(B));
  expect(status()).toHaveTextContent("배치 1개 선택됨(작업 7개)");
  rows = [row(A), child(B, BX, { batch_status: "Running" }), child(C, BX, { batch_status: "Running" })];
  await act(async () => { await qc.refetchQueries({ queryKey: ["requests"] }); });
  await waitFor(() => expect(status()).toHaveTextContent("배치 1개 빠짐(삭제 불가)"));
  expect(box(B)).toBeDisabled();
  expect(box(B)).not.toBeChecked();
  expect(screen.getByRole("button", { name: "선택 삭제" })).toBeDisabled();
  // 다시 고르기 시작하면 알림은 지워진다.
  await userEvent.click(box(A));
  expect(status()).toHaveTextContent("1개 선택됨");
  expect(status()).not.toHaveTextContent("뺐습니다");
});

test("고른 배치가 목록에서 밀려나기만 했으면 「목록에서 사라져」 -- 「삭제할 수 없게 돼」가 아니다", async () => {
  // 2026-10-10 검증 지적: 새 제출에 밀려 불러온 범위 밖으로 나간 배치(여전히 지울 수 있다)를 「삭제할 수 없게 돼」로 알려
  // 관리자가 배치가 다시 돌기 시작했다고 오해했다. 두 갈래를 나눠 센다.
  let rows = [row(A), child(B, BX), child(C, BY, { batch_name: "둘째" })];
  const { qc } = wrapRows(() => rows);
  await ready(A);
  await userEvent.click(anyBox(B));
  await userEvent.click(anyBox(C));
  await userEvent.click(box(A));
  expect(status()).toHaveTextContent("1개 + 배치 2개(작업 14개) 선택됨");
  rows = [row(A), child(C, BY, { batch_name: "둘째", batch_status: "Running" })];     // BX 는 밀려남, BY 는 다시 돎
  await act(async () => { await qc.refetchQueries({ queryKey: ["requests"] }); });
  await waitFor(() => expect(status()).toHaveTextContent(
    "1개 선택됨 · 배치 1개 빠짐(목록에서 사라짐) · 배치 1개 빠짐(삭제 불가)"));
  await userEvent.click(screen.getByRole("button", { name: "선택 해제" }));
  rows = [row(A), child(D, BZ)];
  await act(async () => { await qc.refetchQueries({ queryKey: ["requests"] }); });
  await readyAny(D);
  await userEvent.click(anyBox(D));
  expect(status()).toHaveTextContent("배치 1개 선택됨");
  rows = [row(A)];                                                                     // 다른 관리자가 지웠다
  await act(async () => { await qc.refetchQueries({ queryKey: ["requests"] }); });
  await waitFor(() => expect(status()).toHaveTextContent("배치 1개 빠짐(목록에서 사라짐)"));
  expect(status()).not.toHaveTextContent("삭제할 수 없게 돼");
});

test("헤더 전체 선택의 이름·title 이 단건만 고른다는 것을 말한다", async () => {
  // 2026-10-10 검증 지적: 「삭제 가능한 것 전체」는 고를 수 있는 배치까지 켜지는 것처럼 읽혔다(헤더가 checked 여도 배치는
  // 남는다).
  wrapRows([row(A), child(B, BX)]);
  await ready(A);
  expect(allBox()).toHaveAccessibleName("불러온 단일 작업 중 삭제 가능한 것 전체 선택(배치 제외)");
  expect(allBox().title).toBe("불러온 단일 작업 중 삭제 가능한 것을 모두 선택합니다 — 배치는 행의 체크박스로 배치 단위로 고릅니다");
});

test("필터를 바꾸면 배치 선택도 비워진다", async () => {
  wrapRows([child(A, BX), child(B, BX)]);
  await readyAny(A);
  await userEvent.click(anyBox(A));
  expect(status()).toHaveTextContent("배치 1개 선택됨");
  await userEvent.selectOptions(screen.getByLabelText("연산 필터"), "sync");
  await waitFor(() => expect(status()).toHaveTextContent(IDLE));
  expect(anyBox(A)).not.toBeChecked();
});

test("배치 11개를 고르면 삭제 버튼이 잠기고 「배치는 10개까지」를 말한다", async () => {
  // 짧은 꼬리말(2026-10-10 검증 지적): 서버 문구 「한 번에 배치 10개까지 삭제할 수 있습니다」를 붙이면 넓은 선택 문구와
  // 함께 1024px 에서 툴바가 두 줄로, 375px 에서 세 줄로 늘어 방금 누른 행이 포인터 밑에서 밀렸다.
  const bids = Array.from({ length: 11 }, (_, i) => `${String(i).padStart(2, "0")}${"a".repeat(30)}`);
  wrapRows(bids.map((bid, i) => child(hex(i + 1), bid, { batch_name: `배치${i}`, batch_request_count: 1 })));
  await readyAny(hex(1));
  for (let i = 0; i < 11; i += 1) await userEvent.click(anyBox(hex(i + 1)));
  expect(status()).toHaveTextContent("배치 11개 선택됨(작업 11개) — 배치는 10개까지");
  expect(status()).not.toHaveTextContent("삭제할 수 있습니다");
  expect(status().className).toContain("text-bad");
  expect(screen.getByRole("button", { name: "선택 삭제" })).toBeDisabled();
  await userEvent.click(anyBox(hex(1)));
  expect(screen.getByRole("button", { name: "선택 삭제" })).toBeEnabled();
});

test("배치 삭제: POST 본문에 batches(서버 수 = expected_request_count) → 「배치 1개(작업 7개) 삭제됨」, 묶음 행이 사라진다", async () => {
  const state = { rows: [row(A), child(B, BX), child(C, BX)] as { request_id: string }[] };
  let body: unknown = null;
  server.use(
    http.get("/api/user/requests", () => HttpResponse.json(state.rows)),
    http.get("/api/auth/me", () => HttpResponse.json(meAdmin)),
    http.post("/api/admin/requests:delete", async ({ request }) => {
      body = await request.json();
      state.rows = state.rows.filter((r) => r.request_id === A);
      return HttpResponse.json({ deleted: [], skipped: [], purge_pending: 7,
        deleted_batches: [{ batch_id: BX, request_ids: [B, C, F, G, H, J, hex(10)], job_ids: [], dangling: false }],
        skipped_batches: [] });
    }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><JobsList /></MemoryRouter></QueryClientProvider>);
  await ready(A);
  await userEvent.click(anyBox(B));
  await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
  const dlg = await screen.findByRole("dialog");
  expect(within(dlg).getByText("선택한 배치 1개의 작업 7개를 영구 삭제합니다. 되돌릴 수 없습니다.")).toBeInTheDocument();
  await userEvent.click(within(dlg).getByLabelText("되돌릴 수 없음을 확인했습니다"));
  await userEvent.click(within(dlg).getByRole("button", { name: "7개 영구 삭제" }));
  const done = await screen.findByText("배치 1개(작업 7개) 삭제됨");
  expect(done.className).toContain("text-ok");
  expect(body).toEqual({ request_ids: [], batches: [{ batch_id: BX, expected_request_count: 7 }] });
  expect(screen.queryByText(B)).toBeNull();
  expect(screen.queryByText(C)).toBeNull();
  expect(screen.getByText(A)).toBeInTheDocument();
});

test("섞인 삭제 결과: 「1개 · 배치 1개(작업 2개) 삭제됨 · 배치 2개 제외」 + 배치 제외 줄(문제가 된 작업 id)", async () => {
  server.use(http.post("/api/admin/requests:delete", () => HttpResponse.json({
    deleted: [{ request_id: A, job_ids: [] }], skipped: [], purge_pending: 3,
    deleted_batches: [{ batch_id: BX, request_ids: [B, C], job_ids: [], dangling: true }],
    skipped_batches: [{ batch_id: BY, reason: "request_recently_finished", request_id: D },
                      { batch_id: BZ, reason: "batch_changed", request_id: null }] })));
  wrapRows([row(A), child(B, BX, { batch_request_count: 2 }), child(C, BX, { batch_request_count: 2 }),
            child(D, BY, { batch_name: "둘째", batch_request_count: 1 }),
            child(E, BZ, { batch_name: null, batch_request_count: 3 })]);
  await ready(A);
  await userEvent.click(box(A));
  await userEvent.click(anyBox(B));
  await userEvent.click(anyBox(D));
  await userEvent.click(anyBox(E));
  expect(status()).toHaveTextContent("1개 + 배치 3개(작업 6개) 선택됨");
  await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
  const dlg = await screen.findByRole("dialog");
  await userEvent.click(within(dlg).getByLabelText("되돌릴 수 없음을 확인했습니다"));
  await userEvent.click(within(dlg).getByRole("button", { name: "7개 영구 삭제" }));
  const line = await screen.findByText("1개 · 배치 1개(작업 2개) 삭제됨 · 배치 2개 제외");
  expect(line.className).toContain("text-bad");
  const recent = skipLine(`배치 둘째: 방금 끝난 작업입니다 — 잠시 뒤에 삭제할 수 있습니다 (작업 ${D.slice(0, 12)})`);
  // 문제가 된 자식 id 는 상세 링크다 -- 그 자식은 대개 불러온 범위 밖이고, 찾으려고 필터를 바꾸면 이 줄이 지워진다.
  const link = within(recent).getByRole("link", { name: D.slice(0, 12) });
  expect(link).toHaveAttribute("href", `/jobs/${D}`);
  expect(link.title).toBe(D);
  const changed = skipLine(
    `배치 ${BZ.slice(0, 12)}: 확인 창을 연 뒤 배치의 작업이 바뀌었습니다(재실행 등) — 목록을 다시 보고 다시 시도하세요`);
  expect(within(changed).queryByRole("link")).toBeNull();                // 문제 자식이 없으면 링크도 없다
});

test("단건 제외 줄의 id 도 상세 링크다 -- 없는 요청(request_not_found)만 글자로", async () => {
  server.use(http.post("/api/admin/requests:delete", () => HttpResponse.json({
    deleted: [], purge_pending: 0, deleted_batches: [], skipped_batches: [],
    skipped: [{ request_id: A, reason: "request_job_active" }, { request_id: B, reason: "request_not_found" }] })));
  wrapRows([row(A), row(B, { state: "Failed" })]);
  await ready(A);
  await userEvent.click(allBox());
  await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
  const dlg = await screen.findByRole("dialog");
  await userEvent.click(within(dlg).getByLabelText("되돌릴 수 없음을 확인했습니다"));
  await userEvent.click(within(dlg).getByRole("button", { name: "2개 영구 삭제" }));
  await screen.findByText("삭제된 작업 없음 · 2개 제외");
  const active = skipLine(
    `요청은 끝났지만 아직 끝나지 않은 잡이 있어 삭제할 수 없습니다 — 잠시 뒤 다시 시도하세요 (1개: ${A.slice(0, 12)})`);
  expect(within(active).getByRole("link", { name: A.slice(0, 12) })).toHaveAttribute("href", `/jobs/${A}`);
  const gone = skipLine(`요청을 찾을 수 없습니다 — 삭제됐거나 볼 수 없는 요청입니다 (1개: ${B.slice(0, 12)})`);
  expect(within(gone).queryByRole("link")).toBeNull();
});

test("결과 문구 조합 — 단건만 보낸 응답은 기존 세 문구와 글자 단위로 같다", async () => {
  const { deleteResultText } = await import("./JobsList");
  const d = (n: number) => Array.from({ length: n }, (_, i) => ({ request_id: hex(i + 1), job_ids: [] }));
  const s = (n: number) => Array.from({ length: n }, (_, i) => ({ request_id: hex(i + 50), reason: "request_not_found" }));
  expect(deleteResultText({ deleted: d(2), skipped: [], purge_pending: 0 })).toBe("2개 삭제됨");
  expect(deleteResultText({ deleted: [], skipped: s(3), purge_pending: 0 })).toBe("삭제된 작업 없음 · 3개 제외");
  expect(deleteResultText({ deleted: d(1), skipped: s(2), purge_pending: 0 })).toBe("1개 삭제됨 · 2개 제외");
  // 배치 키가 빈 목록이어도 같다(포탈은 늘 batches 를 싣는다).
  expect(deleteResultText({ deleted: d(2), skipped: [], purge_pending: 0, deleted_batches: [], skipped_batches: [] }))
    .toBe("2개 삭제됨");
  const bd = { batch_id: BX, request_ids: [A, B, C], job_ids: [], dangling: false };
  expect(deleteResultText({ deleted: [], skipped: [], purge_pending: 0, deleted_batches: [bd], skipped_batches: [] }))
    .toBe("배치 1개(작업 3개) 삭제됨");
  expect(deleteResultText({ deleted: [], skipped: [], purge_pending: 0, deleted_batches: [],
                            skipped_batches: [{ batch_id: BX, reason: "batch_changed", request_id: null }] }))
    .toBe("삭제된 작업 없음 · 배치 1개 제외");
  expect(deleteResultText({ deleted: d(1), skipped: s(1), purge_pending: 0, deleted_batches: [bd],
                            skipped_batches: [{ batch_id: BY, reason: "batch_changed", request_id: null }] }))
    .toBe("1개 · 배치 1개(작업 3개) 삭제됨 · 1개 제외 · 배치 1개 제외");
});
