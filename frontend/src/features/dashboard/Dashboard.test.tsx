import { render, screen } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { Dashboard } from "./Dashboard";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("aggregates request metrics and lists nodes", async () => {
  server.use(
    http.get("/api/user/requests", () => HttpResponse.json([
      { request_id: "r1", operation: "sync", state: "Executing", priority: "mid",
        created_at: "", updated_at: "", requester_id: "a", resource_key: "k", payload: {} },
      { request_id: "r2", operation: "sync", state: "Succeeded", priority: "mid",
        created_at: "", updated_at: "", requester_id: "a", resource_key: "k", payload: {} },
    ])),
    http.get("/api/admin/nodes", () => HttpResponse.json([
      { node_name: "w1", reported_at: "2026-08-04T00:00:00Z", fresh: true, report: {} },
    ])),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><Dashboard /></MemoryRouter></QueryClientProvider>);
  expect(await screen.findByText("w1")).toBeInTheDocument();
  // 실행 중 타일 값 1
  const running = screen.getByText("실행 중").parentElement!;
  expect(running).toHaveTextContent("1");
});
