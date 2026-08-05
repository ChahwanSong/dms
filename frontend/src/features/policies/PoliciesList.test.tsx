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

test("shows an inline message when the PUT returns 422 invalid_priority", async () => {
  server.use(
    http.get("/api/admin/policies", () => HttpResponse.json(POLICIES)),
    http.put("/api/admin/policies/:tool", () => HttpResponse.json({ detail: "invalid_priority" }, { status: 422 })));
  wrap();
  const row = (await screen.findByText("scan")).closest("tr")!;
  await userEvent.click(within(row).getByRole("button", { name: "수정" }));
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(await screen.findByText("invalid_priority")).toBeInTheDocument();
});
