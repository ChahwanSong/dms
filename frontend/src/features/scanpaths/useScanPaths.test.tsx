import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { useScanPaths, useAddScanPath, useScanPathStats } from "./useScanPaths";

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

test("useScanPaths returns the registered scan paths", async () => {
  const body = [
    { id: 1, username: "alice", storage_name: "s1", path: "/s1/alice", created_at: "2026-08-01T00:00:00Z" },
  ];
  server.use(http.get("/api/user/scan-paths", () => HttpResponse.json(body)));
  const { result } = renderHook(() => useScanPaths(), { wrapper });
  await waitFor(() => expect(result.current.data).toEqual(body));
});

test("useAddScanPath posts the correct body", async () => {
  let body: unknown = null;
  server.use(http.post("/api/user/scan-paths", async ({ request }) => {
    body = await request.json();
    return HttpResponse.json(
      { id: 2, username: "alice", storage_name: "s1", path: "/s1/alice/data", created_at: "2026-08-05T00:00:00Z" },
      { status: 201 },
    );
  }));
  const { result } = renderHook(() => useAddScanPath(), { wrapper });
  result.current.mutate({ storage_name: "s1", path: "/s1/alice/data" });
  await waitFor(() => expect(body).toEqual({ storage_name: "s1", path: "/s1/alice/data" }));
});

test("useScanPathStats with enabled: false sends no request", async () => {
  let requestCount = 0;
  server.use(http.get("/api/user/scan-paths/1/stats", () => {
    requestCount += 1;
    return HttpResponse.json({
      covered_by: { target: "/s1/alice", exact: true },
      generated_at_epoch: 1754400000,
      summary: {},
      file_size_histogram: [],
      time_histograms: {},
    });
  }));
  const { result } = renderHook(() => useScanPathStats(1, false), { wrapper });
  // Give any accidental in-flight request a tick to land.
  await new Promise((r) => setTimeout(r, 10));
  expect(requestCount).toBe(0);
  expect(result.current.data).toBeUndefined();
  expect(result.current.fetchStatus).toBe("idle");
});

test("useScanPathStats with enabled: true requests the correct URL and returns the body", async () => {
  const stats = {
    covered_by: { target: "/s1/alice", exact: true },
    generated_at_epoch: 1754400000,
    summary: { total_files: 10 },
    file_size_histogram: [{ bucket: "0-1KB", count: 5 }],
    time_histograms: { mtime: [{ bucket: "0-7d", count: 3 }] },
  };
  let requestedUrl: string | null = null;
  server.use(http.get("/api/user/scan-paths/1/stats", ({ request }) => {
    requestedUrl = request.url;
    return HttpResponse.json(stats);
  }));
  const { result } = renderHook(() => useScanPathStats(1, true), { wrapper });
  await waitFor(() => expect(result.current.data).toEqual(stats));
  expect(requestedUrl).toContain("/api/user/scan-paths/1/stats");
});
