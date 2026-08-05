import { render, screen } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { AuditLog } from "./AuditLog";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());
test("renders audit entries", async () => {
  server.use(http.get("/api/admin/audit-log", () => HttpResponse.json([
    { id:2, mutation_class:"storage", operation:"create", target_key:"cephfs",
      actor:"admin", before_state:null, after_state:"{}", at:"2026-08-05T00:00:00Z" }])));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><AuditLog /></QueryClientProvider>);
  expect(await screen.findByText("cephfs")).toBeInTheDocument();
  expect(screen.getByText("create")).toBeInTheDocument();
  expect(screen.getByText("storage")).toBeInTheDocument();
});
