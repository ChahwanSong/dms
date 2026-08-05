import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { useArtifacts, useArtifactFile } from "./useArtifacts";

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

test("useArtifacts returns the list", async () => {
  const rows = [{ phase: "preflight", name: "stdout.log", size: 128, modified_at: 1754400000 }];
  server.use(http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(rows)));
  const { result } = renderHook(() => useArtifacts("j1"), { wrapper });
  await waitFor(() => expect(result.current.data).toEqual(rows));
});

test("useArtifactFile with enabled: false sends no request", async () => {
  let requestCount = 0;
  server.use(http.get("/api/user/jobs/j1/artifacts/preflight/stdout.log", () => {
    requestCount += 1;
    return HttpResponse.json({ phase: "preflight", name: "stdout.log", size: 4, truncated: false, content: "ok\n" });
  }));
  const { result } = renderHook(() => useArtifactFile("j1", "preflight", "stdout.log", false), { wrapper });
  // Give any accidental in-flight request a tick to land.
  await new Promise((r) => setTimeout(r, 10));
  expect(requestCount).toBe(0);
  expect(result.current.data).toBeUndefined();
  expect(result.current.fetchStatus).toBe("idle");
});

test("useArtifactFile with enabled: true requests the correct URL and returns the body", async () => {
  const file = { phase: "preflight", name: "stdout.log", size: 4, truncated: false, content: "ok\n" };
  let requestedUrl: string | null = null;
  server.use(http.get("/api/user/jobs/j1/artifacts/preflight/stdout.log", ({ request }) => {
    requestedUrl = request.url;
    return HttpResponse.json(file);
  }));
  const { result } = renderHook(() => useArtifactFile("j1", "preflight", "stdout.log", true), { wrapper });
  await waitFor(() => expect(result.current.data).toEqual(file));
  expect(requestedUrl).toContain("/api/user/jobs/j1/artifacts/preflight/stdout.log");
});
