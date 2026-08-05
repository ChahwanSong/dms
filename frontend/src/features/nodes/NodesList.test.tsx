import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { NodesList } from "./NodesList";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const NODES = [
  {
    node_name: "node-a",
    reported_at: "2026-08-06T00:00:00Z",
    fresh: true,
    report: {
      node_name: "node-a",
      probed_at: "2026-08-06T00:00:00Z",
      mounts: [
        { storage_name: "vol1", mount_path: "/mnt/vol1", status: "Ready", exists: true, is_mountpoint: true, readable: true, reason: null },
        { storage_name: "vol2", mount_path: "/mnt/vol2", status: "Error", exists: false, is_mountpoint: false, readable: false, reason: "not mounted" },
      ],
      tools: [
        { name: "rsync", status: "Ready", path: "/usr/bin/rsync", version: "3.2.7", reason: null },
      ],
      os: { disks: [{ storage_name: "vol1", total_bytes: 1024 ** 4, used_bytes: 512 * 1024 ** 3 }] },
      identities: [],
    },
  },
  {
    node_name: "node-b",
    reported_at: "2026-08-05T00:00:00Z",
    fresh: false,
    report: {
      mounts: [],
      tools: [],
      os: { disks: [] },
      identities: [],
    },
  },
];

const REPORTS = [
  { reported_at: "2026-08-06T10:00:00Z", report: {} },
  { reported_at: "2026-08-05T22:00:00Z", report: {} },
];

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><NodesList /></QueryClientProvider>);
}

test("lists nodes with a Ready n/m mount summary", async () => {
  server.use(http.get("/api/admin/nodes", () => HttpResponse.json(NODES)));
  wrap();
  expect(await screen.findByText("node-a")).toBeInTheDocument();
  expect(screen.getByText("node-b")).toBeInTheDocument();
  const rowA = screen.getByText("node-a").closest("tr")!;
  expect(within(rowA).getByText("Ready 1/2")).toBeInTheDocument();
});

test("a stale node is shown as stale in text-bad", async () => {
  server.use(http.get("/api/admin/nodes", () => HttpResponse.json(NODES)));
  wrap();
  const rowB = (await screen.findByText("node-b")).closest("tr")!;
  const stale = within(rowB).getByText("stale");
  expect(stale).toHaveClass("text-bad");
  const rowA = screen.getByText("node-a").closest("tr")!;
  expect(within(rowA).getByText("fresh")).toBeInTheDocument();
});

test("clicking 상세 reveals mounts, tools and disk tables", async () => {
  server.use(http.get("/api/admin/nodes", () => HttpResponse.json(NODES)));
  wrap();
  const rowA = (await screen.findByText("node-a")).closest("tr")!;
  await userEvent.click(within(rowA).getByRole("button", { name: "상세" }));

  expect(await screen.findByText("/mnt/vol1")).toBeInTheDocument();
  expect(screen.getByText("not mounted")).toBeInTheDocument();
  expect(screen.getByText("rsync")).toBeInTheDocument();
  expect(screen.getByText("3.2.7")).toBeInTheDocument();
  // 512 GiB used / 1 TiB total = 50.0%
  expect(screen.getByText("512.0 GiB")).toBeInTheDocument();
  expect(screen.getByText("1.0 TiB")).toBeInTheDocument();
  expect(screen.getByText("50.0%")).toBeInTheDocument();
});

test("no reports request goes out before 최근 리포트 is clicked", async () => {
  let reportsCalls = 0;
  server.use(
    http.get("/api/admin/nodes", () => HttpResponse.json(NODES)),
    http.get("/api/admin/nodes/:name/reports", () => {
      reportsCalls += 1;
      return HttpResponse.json(REPORTS);
    }),
  );
  wrap();
  const rowA = (await screen.findByText("node-a")).closest("tr")!;
  await userEvent.click(within(rowA).getByRole("button", { name: "상세" }));
  await screen.findByText("/mnt/vol1");
  // Give any accidental in-flight request a tick to land.
  await new Promise((r) => setTimeout(r, 10));
  expect(reportsCalls).toBe(0);
});

test("clicking 최근 리포트 loads and shows report history", async () => {
  let reportsCalls = 0;
  server.use(
    http.get("/api/admin/nodes", () => HttpResponse.json(NODES)),
    http.get("/api/admin/nodes/:name/reports", () => {
      reportsCalls += 1;
      return HttpResponse.json(REPORTS);
    }),
  );
  wrap();
  const rowA = (await screen.findByText("node-a")).closest("tr")!;
  await userEvent.click(within(rowA).getByRole("button", { name: "상세" }));
  await screen.findByText("/mnt/vol1");

  await userEvent.click(screen.getByRole("button", { name: "최근 리포트" }));

  expect(await screen.findByText("2026-08-06T10:00:00Z")).toBeInTheDocument();
  expect(screen.getByText("2026-08-05T22:00:00Z")).toBeInTheDocument();
  expect(reportsCalls).toBe(1);
});
