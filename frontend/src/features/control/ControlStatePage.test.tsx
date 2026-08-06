import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { ControlStatePage } from "./ControlStatePage";

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const CS = { maintenance: 1, drain: 0, reason: "점검", build_node_name: "dms-w1", changed_by: "ops", changed_at: "2026-08-05T00:00:00Z" };

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><ControlStatePage /></QueryClientProvider>);
}

test("shows the maintenance banner and changed_by, but no drain banner", async () => {
  server.use(http.get("/api/admin/control-state", () => HttpResponse.json(CS)));
  wrap();
  expect(await screen.findByText((_, node) =>
    Boolean(node?.textContent?.startsWith("유지보수 중") && node.textContent.includes("드레인도 함께 켜세요")))).toBeInTheDocument();
  expect(screen.getByText("ops")).toBeInTheDocument();
  expect(screen.queryByText("드레인 중 — 진행 중인 작업이 더 전진하지 않습니다")).not.toBeInTheDocument();
});

test("toggling drain and saving sends the correct PUT body", async () => {
  let body: any = null;
  server.use(
    http.get("/api/admin/control-state", () => HttpResponse.json(CS)),
    http.put("/api/admin/control-state", async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ ...CS, drain: 1 });
    }));
  wrap();
  await screen.findByLabelText("유지보수");
  await userEvent.click(screen.getByLabelText("드레인"));
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(body).toEqual({ maintenance: true, drain: true, reason: "점검", build_node_name: "dms-w1" });
});

test("renders no warning banners when both flags are off", async () => {
  server.use(http.get("/api/admin/control-state", () =>
    HttpResponse.json({ maintenance: 0, drain: 0, reason: null, build_node_name: null, changed_by: null, changed_at: null })));
  wrap();
  await screen.findByLabelText("유지보수");
  expect(screen.queryByText((_, node) => Boolean(node?.textContent?.startsWith("유지보수 중")))).not.toBeInTheDocument();
  expect(screen.queryByText("드레인 중 — 진행 중인 작업이 더 전진하지 않습니다")).not.toBeInTheDocument();
});
