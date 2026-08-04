import { render, screen } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { JobsList } from "./JobsList";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("lists requests with status pill", async () => {
  server.use(http.get("/api/user/requests", () => HttpResponse.json([
    { request_id: "r1", operation: "sync", state: "Succeeded", priority: "mid",
      created_at: "2026-08-04T00:00:00Z", updated_at: "", requester_id: "alice",
      resource_key: "k", payload: {} },
  ])));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><JobsList /></MemoryRouter></QueryClientProvider>);
  expect(await screen.findByText("r1")).toBeInTheDocument();
  expect(screen.getByText("Succeeded")).toBeInTheDocument();
});
