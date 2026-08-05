import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { ScanPaths } from "./ScanPaths";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><ScanPaths /></QueryClientProvider>);
}

const STORAGES = [{ storage_name: "s1", backend_type: "cephfs", status: "Ready" }];

test("renders the list of registered scan paths", async () => {
  server.use(
    http.get("/api/user/storages", () => HttpResponse.json(STORAGES)),
    http.get("/api/user/scan-paths", () => HttpResponse.json([
      { id: 1, storage_name: "s1", path: "/s1/alice", created_at: "2026-08-01T00:00:00Z" },
    ])),
  );
  wrap();
  expect(await screen.findByText("/s1/alice")).toBeInTheDocument();
  expect(screen.getByText("s1")).toBeInTheDocument();
});

test("registering a scan path POSTs the correct body and refreshes the list", async () => {
  let capturedBody: unknown = null;
  let listCallCount = 0;
  server.use(
    http.get("/api/user/storages", () => HttpResponse.json(STORAGES)),
    http.get("/api/user/scan-paths", () => {
      listCallCount += 1;
      return HttpResponse.json(
        listCallCount === 1
          ? []
          : [{ id: 2, storage_name: "s1", path: "/s1/alice/new", created_at: "2026-08-05T00:00:00Z" }],
      );
    }),
    http.post("/api/user/scan-paths", async ({ request }) => {
      capturedBody = await request.json();
      return HttpResponse.json(
        { id: 2, storage_name: "s1", path: "/s1/alice/new", created_at: "2026-08-05T00:00:00Z" },
        { status: 201 },
      );
    }),
  );
  wrap();
  await waitFor(() => expect(screen.getByLabelText("스토리지")).not.toBeDisabled());
  await userEvent.selectOptions(screen.getByLabelText("스토리지"), "s1");
  await userEvent.type(screen.getByLabelText("경로"), "/s1/alice/new");
  await userEvent.click(screen.getByRole("button", { name: "등록" }));

  await waitFor(() => expect(capturedBody).toEqual({ storage_name: "s1", path: "/s1/alice/new" }));
  expect(await screen.findByText("/s1/alice/new")).toBeInTheDocument();
});

test("no stats request is sent before '통계 보기' is clicked", async () => {
  let statsRequestCount = 0;
  server.use(
    http.get("/api/user/storages", () => HttpResponse.json(STORAGES)),
    http.get("/api/user/scan-paths", () => HttpResponse.json([
      { id: 1, storage_name: "s1", path: "/s1/alice", created_at: "2026-08-01T00:00:00Z" },
    ])),
    http.get("/api/user/scan-paths/1/stats", () => {
      statsRequestCount += 1;
      return HttpResponse.json({
        covered_by: { target: "/s1/alice", exact: true },
        generated_at_epoch: 1754400000,
        summary: {}, file_size_histogram: [], time_histograms: {},
      });
    }),
  );
  wrap();
  await screen.findByText("/s1/alice");
  // Give any accidental in-flight request a tick to land before asserting.
  await new Promise((r) => setTimeout(r, 10));
  expect(statsRequestCount).toBe(0);
});

test("clicking '통계 보기' shows summary/histograms and the not-exact upstream notice", async () => {
  server.use(
    http.get("/api/user/storages", () => HttpResponse.json(STORAGES)),
    http.get("/api/user/scan-paths", () => HttpResponse.json([
      { id: 1, storage_name: "s1", path: "/s1/alice/sub", created_at: "2026-08-01T00:00:00Z" },
    ])),
    http.get("/api/user/scan-paths/1/stats", () => HttpResponse.json({
      covered_by: { target: "/s1/alice", exact: false },
      generated_at_epoch: 1754400000,
      summary: { total_files: 42 },
      file_size_histogram: [{ bucket: "0-1KB", count: 5 }],
      time_histograms: { mtime: [{ bucket: "0-7d", bytes: 1000 }] },
    })),
  );
  wrap();
  await userEvent.click(await screen.findByRole("button", { name: "통계 보기" }));

  expect(await screen.findByText("42")).toBeInTheDocument();
  expect(screen.getByText("0-1KB")).toBeInTheDocument();
  expect(screen.getByText("1000")).toBeInTheDocument();
  expect(screen.getByText(
    "상위 경로 /s1/alice 기준 집계입니다 — 이 경로만의 통계가 아닙니다",
  )).toBeInTheDocument();
});

test("shows the Korean no_covering_scan message on 404 with an admin-scan hint", async () => {
  server.use(
    http.get("/api/user/storages", () => HttpResponse.json(STORAGES)),
    http.get("/api/user/scan-paths", () => HttpResponse.json([
      { id: 1, storage_name: "s1", path: "/s1/alice", created_at: "2026-08-01T00:00:00Z" },
    ])),
    http.get("/api/user/scan-paths/1/stats", () =>
      HttpResponse.json({ detail: "no_covering_scan" }, { status: 404 })),
  );
  wrap();
  await userEvent.click(await screen.findByRole("button", { name: "통계 보기" }));

  expect(await screen.findByText("아직 이 경로를 커버하는 scan 결과가 없습니다")).toBeInTheDocument();
  expect(screen.getByText(/scan을 실행하면/)).toBeInTheDocument();
});
