import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { NodesList, toolStatusText } from "./NodesList";

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
  // 도구는 존재 확인만 -- status 는 "설치됨"으로 표기(버전 컬럼 제거)
  expect(screen.getByText("설치됨")).toBeInTheDocument();
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

test("a malformed report shape does not crash the node list", async () => {
  // 스키마 검증 없이 저장되는 /api/agent/report 리포트가 배열이어야 할 필드에
  // 다른 타입을 담아 보내더라도(빈 report, report 자체 부재, non-array mounts)
  // 목록은 렌더링을 계속해야 한다 — 화이트스크린은 없어야 한다.
  const malformed = [
    { node_name: "bad-empty-report", reported_at: "2026-08-06T00:00:00Z", fresh: true, report: {} },
    { node_name: "bad-no-report", reported_at: "2026-08-06T00:00:00Z", fresh: true },
    { node_name: "bad-non-array-mounts", reported_at: "2026-08-06T00:00:00Z", fresh: true,
      report: { mounts: {}, tools: {} } },
  ];
  server.use(http.get("/api/admin/nodes", () => HttpResponse.json(malformed)));
  wrap();
  expect(await screen.findByText("bad-empty-report")).toBeInTheDocument();
  expect(screen.getByText("bad-no-report")).toBeInTheDocument();
  expect(screen.getByText("bad-non-array-mounts")).toBeInTheDocument();
  const badRow = screen.getByText("bad-non-array-mounts").closest("tr")!;
  expect(within(badRow).getAllByText("Ready 0/0")).toHaveLength(2); // mounts, tools
});

test("opening detail on a node with a malformed (non-array) report does not crash", async () => {
  const malformed = [
    { node_name: "bad-non-array-mounts", reported_at: "2026-08-06T00:00:00Z", fresh: true,
      report: { mounts: {}, tools: {}, os: { disks: {} }, identities: {} } },
  ];
  server.use(http.get("/api/admin/nodes", () => HttpResponse.json(malformed)));
  wrap();
  const row = (await screen.findByText("bad-non-array-mounts")).closest("tr")!;
  await userEvent.click(within(row).getByRole("button", { name: "상세" }));
  expect(await screen.findByText("bad-non-array-mounts 상세")).toBeInTheDocument();
});

test("disk row with missing byte fields renders a dash instead of undefined/NaN", async () => {
  const nodes = [
    { node_name: "node-disk", reported_at: "2026-08-06T00:00:00Z", fresh: true,
      report: { mounts: [], tools: [],
        os: { disks: [{ storage_name: "vol1", total_bytes: undefined, used_bytes: null }] },
        identities: [] } },
  ];
  server.use(http.get("/api/admin/nodes", () => HttpResponse.json(nodes)));
  wrap();
  const row = (await screen.findByText("node-disk")).closest("tr")!;
  await userEvent.click(within(row).getByRole("button", { name: "상세" }));
  expect(await screen.findByText("vol1")).toBeInTheDocument();
  const dashes = screen.getAllByText("—");
  expect(dashes.length).toBeGreaterThanOrEqual(3); // used, total, percentage
  expect(screen.queryByText(/undefined B/)).not.toBeInTheDocument();
  expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
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

  // KST(+9h): 10:00Z→19:00, 22:00Z→다음날 07:00(날짜 경계 넘어감)
  expect(await screen.findByText("2026-08-06 19:00:00 KST")).toBeInTheDocument();
  expect(screen.getByText("2026-08-06 07:00:00 KST")).toBeInTheDocument();
  expect(reportsCalls).toBe(1);
});

test("toolStatusText: 존재 확인만 -- Ready→설치됨, Missing→없음", () => {
  expect(toolStatusText("Ready")).toBe("설치됨");
  expect(toolStatusText("Missing")).toBe("없음");
  // 미지 값은 원문 그대로(지어내지 않음)
  expect(toolStatusText("weird")).toBe("weird");
});
