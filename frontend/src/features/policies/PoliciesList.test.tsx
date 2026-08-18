import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { PoliciesList } from "./PoliciesList";

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const POLICIES = [
  { tool: "scan", max_nodes: 4, procs_per_node: 8, queue: "dms-data",
    default_priority: "mid", max_priority: "high",
    preview_timeout_seconds: null, execution_timeout_seconds: 3600,
    enabled: 1, updated_at: "2026-08-05T00:00:00Z", updated_by: "admin" },
  { tool: "dsync", max_nodes: 8, procs_per_node: 8, queue: "dms-data",
    default_priority: "mid", max_priority: "high",
    preview_timeout_seconds: 3600, execution_timeout_seconds: 259200,
    enabled: 1, updated_at: "2026-08-05T00:00:00Z", updated_by: "admin" },
  { tool: "nsync", max_nodes: 8, procs_per_node: 8, queue: "dms-data",
    default_priority: "mid", max_priority: "high",
    preview_timeout_seconds: 3600, execution_timeout_seconds: 259200,
    enabled: 1, updated_at: "2026-08-05T00:00:00Z", updated_by: "admin" },
  { tool: "rm", max_nodes: 4, procs_per_node: 8, queue: "dms-data",
    default_priority: "mid", max_priority: "high",
    preview_timeout_seconds: 1800, execution_timeout_seconds: 3600,
    enabled: 1, updated_at: "2026-08-05T00:00:00Z", updated_by: "admin" },
];

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><PoliciesList /></QueryClientProvider>);
}

test("lists the four tool policies with human-readable timeouts", async () => {
  server.use(http.get("/api/admin/policies", () => HttpResponse.json(POLICIES)));
  wrap();
  expect(await screen.findByText("scan")).toBeInTheDocument();
  expect(screen.getByText("dsync")).toBeInTheDocument();
  expect(screen.getAllByText("3600s (1h)").length).toBeGreaterThan(0);
  expect(screen.getAllByText("259200s (3d)").length).toBeGreaterThan(0);
});

test("editing a policy sends the correct PUT body, including null preview timeout when cleared", async () => {
  let capturedBody: unknown;
  server.use(
    http.get("/api/admin/policies", () => HttpResponse.json(POLICIES)),
    http.put("/api/admin/policies/:tool", async ({ request }) => {
      capturedBody = await request.json();
      return HttpResponse.json({ ...POLICIES[1], max_nodes: 16, preview_timeout_seconds: null });
    }));
  wrap();
  const row = (await screen.findByText("dsync")).closest("tr")!;
  await userEvent.click(within(row).getByRole("button", { name: "수정" }));

  const maxNodes = await screen.findByRole("spinbutton", { name: "최대 노드" });
  await userEvent.clear(maxNodes);
  await userEvent.type(maxNodes, "16");

  const previewTimeout = screen.getByRole("spinbutton", { name: "미리보기 타임아웃(초)" });
  await userEvent.clear(previewTimeout);

  await userEvent.click(screen.getByRole("button", { name: "저장" }));

  expect(capturedBody).toEqual({
    max_nodes: 16, procs_per_node: 8, queue: "dms-data",
    default_priority: "mid", max_priority: "high",
    preview_timeout_seconds: null, execution_timeout_seconds: 259200,
    enabled: true,
  });
});

test("숫자 필드를 지우면 빈 칸(0 이 아님) + 인라인 오류 + 저장 비활성", async () => {
  server.use(http.get("/api/admin/policies", () => HttpResponse.json(POLICIES)));
  wrap();
  const row = (await screen.findByText("scan")).closest("tr")!;
  await userEvent.click(within(row).getByRole("button", { name: "수정" }));
  const maxNodes = await screen.findByRole("spinbutton", { name: "최대 노드" });
  await userEvent.clear(maxNodes);
  // 결함 회귀 그물: number 상태 시절엔 Number("")=0 이 "0"으로 그려져 필드를
  // 비우는 것 자체가 불가능했다.
  expect(maxNodes).toHaveValue(null);
  expect(screen.getByText("최대 노드: 값을 입력하세요")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();
  // 다시 치면 친 그대로 보인다("08" 잔류 없음 — 문자열 상태라 표시 = 상태)
  await userEvent.type(maxNodes, "8");
  expect(maxNodes).toHaveValue(8);
  expect(screen.queryByText("최대 노드: 값을 입력하세요")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "저장" })).toBeEnabled();
});

test("0·음수는 서버까지 가기 전에 인라인 오류로 막는다(pydantic ge=1 미러)", async () => {
  const calls: string[] = [];
  server.use(
    http.get("/api/admin/policies", () => HttpResponse.json(POLICIES)),
    http.put("/api/admin/policies/:tool", () => { calls.push("put"); return HttpResponse.json(POLICIES[0]); }));
  wrap();
  const row = (await screen.findByText("scan")).closest("tr")!;
  await userEvent.click(within(row).getByRole("button", { name: "수정" }));
  const et = await screen.findByRole("spinbutton", { name: "실행 타임아웃(초)" });
  await userEvent.clear(et);
  await userEvent.type(et, "0");
  expect(screen.getByText("실행 타임아웃: 1 이상의 정수여야 합니다")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(calls).toEqual([]);   // 저장이 비활성이라 PUT 이 나가지 않았다
});

test("shows an inline message when the PUT returns 422 invalid_priority", async () => {
  server.use(
    http.get("/api/admin/policies", () => HttpResponse.json(POLICIES)),
    http.put("/api/admin/policies/:tool", () => HttpResponse.json({ detail: "invalid_priority" }, { status: 422 })));
  wrap();
  const row = (await screen.findByText("scan")).closest("tr")!;
  await userEvent.click(within(row).getByRole("button", { name: "수정" }));
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(await screen.findByText("우선순위 값이 올바르지 않습니다")).toBeInTheDocument();
});
