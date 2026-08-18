import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { AuditLog, auditDiff } from "./AuditLog";
import type { AuditEntry } from "../../lib/types";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

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