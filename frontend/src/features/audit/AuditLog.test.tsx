import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient, focusManager } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect, vi } from "vitest";
import { AuditLog, auditDiff } from "./AuditLog";
import type { AuditEntry } from "../../lib/types";
const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => { server.resetHandlers(); vi.unstubAllGlobals(); observers.length = 0; });
afterAll(() => server.close());

// 무한 스크롤 감시 노드를 테스트가 직접 "보이게" 만드는 IntersectionObserver(전역 폴리필은 아무것도
// 발화하지 않는다 -- test/setup.ts). 가장 최근에 붙은 관찰자에게 교차를 알린다.
const observers: { cb: IntersectionObserverCallback }[] = [];
class ControlledIO {
  constructor(public cb: IntersectionObserverCallback) { observers.push(this); }
  observe() {} unobserve() {} disconnect() {} takeRecords() { return []; }
}
const scrollToBottom = () => act(() => {
  observers[observers.length - 1].cb(
    [{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver);
});
// id 가 from..to(내림차순)인 기록들.
const entries = (from: number, to: number): AuditEntry[] =>
  Array.from({ length: from - to + 1 }, (_, i) => ({
    id: from - i, mutation_class: "sync_pair", operation: "add", target_key: `k${from - i}`,
    actor: "mason", before_state: null, after_state: null, at: "2026-09-30T00:00:00Z" }));

const ENTRIES = [
  { id: 3, mutation_class: "policy", operation: "upsert", target_key: "scan",
    actor: "mason",
    before_state: JSON.stringify({ max_nodes: 4, procs_per_node: 2, queue: "dms-data" }),
    after_state: JSON.stringify({ max_nodes: 8, procs_per_node: 2, queue: "dms-data" }),
    at: "2026-08-19T00:00:00Z" },
  { id: 2, mutation_class: "storage", operation: "create", target_key: "cephfs",
    actor: "admin", before_state: null,
    after_state: JSON.stringify({ storage_name: "cephfs" }), at: "2026-08-05T00:00:00Z" },
];

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><AuditLog /></QueryClientProvider>);
}

test("renders audit entries", async () => {
  server.use(http.get("/api/admin/audit-log", () => HttpResponse.json(ENTRIES)));
  wrap();
  expect(await screen.findByText("cephfs")).toBeInTheDocument();
  expect(screen.getByText("create")).toBeInTheDocument();
  expect(screen.getByText("storage")).toBeInTheDocument();
});

test("펼치기: 변경된 필드만 이전→이후로 보이고, 닫기·단일 펼침 규칙을 지킨다", async () => {
  server.use(http.get("/api/admin/audit-log", () => HttpResponse.json(ENTRIES)));
  wrap();
  await screen.findByText("scan");
  // 접힘 기본: diff 는 렌더되지 않는다
  expect(screen.queryByText("max_nodes")).not.toBeInTheDocument();
  const buttons = screen.getAllByRole("button", { name: "펼치기" });
  await userEvent.click(buttons[0]);           // policy 행
  // 변경된 필드(max_nodes)만 -- 동일 값(procs_per_node·queue)은 소음이라 안 그린다
  expect(screen.getByText("max_nodes")).toBeInTheDocument();
  expect(screen.queryByText("procs_per_node")).not.toBeInTheDocument();
  // 한 번에 하나만: 다른 행을 펼치면 이전 diff 는 닫힌다
  await userEvent.click(screen.getAllByRole("button", { name: "펼치기" })[0]);
  expect(screen.queryByText("max_nodes")).not.toBeInTheDocument();
  expect(screen.getByText("storage_name")).toBeInTheDocument();
  // 닫기 버튼이 diff 를 거둔다
  await userEvent.click(screen.getByRole("button", { name: "닫기" }));
  expect(screen.queryByText("storage_name")).not.toBeInTheDocument();
});

test("auditDiff: 생성(before null)은 새 값 전부, 동일 저장은 빈 배열, 스냅샷 없음은 null", () => {
  const base = { id: 1, mutation_class: "x", operation: "y", target_key: "z",
                 actor: "a", at: "t" };
  const created: AuditEntry = { ...base, before_state: null,
    after_state: JSON.stringify({ name: "n1" }) };
  expect(auditDiff(created)).toEqual([{ field: "name", from: "—", to: "n1" }]);
  const same: AuditEntry = { ...base,
    before_state: JSON.stringify({ a: 1 }), after_state: JSON.stringify({ a: 1 }) };
  expect(auditDiff(same)).toEqual([]);
  const none: AuditEntry = { ...base, before_state: null, after_state: null };
  expect(auditDiff(none)).toBeNull();
});
// ---- 무한 스크롤(2026-09-30 사용자 요청: "수십 개만 보인다 -- 스크롤하면 계속 더") -------------

test("스크롤이 바닥에 닿으면 마지막 id 를 before 로 다음 쪽을 불러와 이어 붙이고, 덜 찬 쪽에서 끝난다", async () => {
  vi.stubGlobal("IntersectionObserver", ControlledIO);
  const asked: string[] = [];
  server.use(http.get("/api/admin/audit-log", ({ request }) => {
    const u = new URL(request.url);
    asked.push(u.search);
    return HttpResponse.json(u.searchParams.get("before") === null ? entries(150, 101) : entries(100, 71));
  }));
  wrap();
  expect(await screen.findByText("k150")).toBeInTheDocument();
  expect(screen.getByText("50건 표시 — 스크롤하면 더 불러옵니다")).toBeInTheDocument();
  expect(screen.queryByText("k100")).not.toBeInTheDocument();
  scrollToBottom();
  expect(await screen.findByText("k100")).toBeInTheDocument();
  expect(screen.getByText("k71")).toBeInTheDocument();
  expect(asked).toEqual(["?limit=50", "?limit=50&before=101"]);
  // 30건(덜 찬 쪽) = 끝 -- 더 스크롤해도 요청하지 않는다
  expect(await screen.findByText("마지막 기록입니다 — 전체 80건")).toBeInTheDocument();
  scrollToBottom();
  expect(asked).toHaveLength(2);
});

test("다음 쪽이 실패하면 알리고 자동으로 다시 당기지 않는다 -- 다시 시도 버튼으로만", async () => {
  vi.stubGlobal("IntersectionObserver", ControlledIO);
  let failNext = true;
  let nextCalls = 0;
  server.use(http.get("/api/admin/audit-log", ({ request }) => {
    if (new URL(request.url).searchParams.get("before") === null) return HttpResponse.json(entries(150, 101));
    nextCalls += 1;
    return failNext ? HttpResponse.json({ detail: "boom" }, { status: 500 }) : HttpResponse.json(entries(100, 91));
  }));
  wrap();
  await screen.findByText("k150");
  scrollToBottom();
  expect(await screen.findByText(/이전 기록을 더 불러오지 못했습니다/)).toBeInTheDocument();
  expect(screen.getByText("k150")).toBeInTheDocument();          // 받아 둔 기록은 그대로
  scrollToBottom();                                                // 감시 노드가 계속 보여도
  expect(nextCalls).toBe(1);                                       // 실패 요청을 연달아 내지 않는다
  failNext = false;
  await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));
  expect(await screen.findByText("k91")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("마지막 기록입니다 — 전체 60건")).toBeInTheDocument());
});

test("첫 쪽이 실패하면 표 대신 사유를 보인다", async () => {
  server.use(http.get("/api/admin/audit-log", () =>
    HttpResponse.json({ detail: "boom" }, { status: 500 })));
  wrap();
  expect(await screen.findByText(/boom|500/)).toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});

test("창 포커스 재조회가 실패해도 받아 둔 목록은 남고 경고만 뜬다", async () => {
  let fail = false;
  server.use(http.get("/api/admin/audit-log", () =>
    fail ? HttpResponse.json({ detail: "boom" }, { status: 500 }) : HttpResponse.json(ENTRIES)));
  wrap();
  await screen.findByText("cephfs");
  fail = true;
  act(() => { focusManager.setFocused(false); focusManager.setFocused(true); });
  expect(await screen.findByText(/최신 기록을 다시 읽지 못했습니다/)).toBeInTheDocument();
  expect(screen.getByText("cephfs")).toBeInTheDocument();
  act(() => { focusManager.setFocused(undefined); });
});
