import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { NodesList, toolStatusText, ageText, clockSkewSeconds, cpuLoadText, renameHint } from "./NodesList";
import { nodeInUseText, parseNodeInUse } from "./ForgetNodeDialog";
import { reasonText } from "../../lib/api";

// 상세 창의 마운트 표는 등록 스토리지(GET /api/admin/storages)를 맞붙여 「관리 디렉토리」를 그린다(2026-11-25) -- 기본 핸들러로
// 둬서(resetHandlers 뒤에도 남는다) 상세를 여는 모든 테스트가 실제 네트워크로 새지 않게 한다. managed_root 는 mount_path 와
// 다르게 둔다(getByText 다중 일치 회피).
const STORAGES = [
  { storage_name: "vol1", mount_path: "/mnt/vol1", managed_root: "/mnt/vol1/dms", backend_type: "nfs", enabled: 1 },
];
const server = setupServer(http.get("/api/admin/storages", () => HttpResponse.json(STORAGES)));
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


// ---- 2026-09-30 사용자 요청: 상세는 창(모달), 마지막 리포트는 맨 끝 열, 오래되면 빨강 --------

test("마지막 리포트는 맨 끝 열이고, 상세는 표 아래가 아니라 모달 창으로 뜬다", async () => {
  server.use(http.get("/api/admin/nodes", () => HttpResponse.json(NODES)));
  wrap();
  await screen.findByText("node-a");
  const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
  expect(headers[headers.length - 1]).toBe("마지막 리포트");
  expect(headers).toEqual(["노드", "상태", "배치", "마운트", "도구", "CPU 사용률 · 부하", "메모리", "상세", "마지막 리포트"]);
  const rowA = screen.getByText("node-a").closest("tr")!;
  const listTable = rowA.closest("table")!;
  await userEvent.click(within(rowA).getByRole("button", { name: "상세" }));
  const dialog = await screen.findByRole("dialog", { name: "node-a 상세" });
  expect(within(dialog).getByText("/mnt/vol1")).toBeInTheDocument();
  // 목록 표 안에는 상세 내용이 끼어들지 않는다 -- 모달(포털)에만 있다.
  expect(within(listTable).queryByText("/mnt/vol1")).toBeNull();
});

test("리포트가 지연된 노드는 마지막 리포트가 빨간 경고로 보이고 상단에 지연 대수가 뜬다", async () => {
  server.use(http.get("/api/admin/nodes", () => HttpResponse.json(NODES)));
  wrap();
  const rowB = (await screen.findByText("node-b")).closest("tr")!;
  const last = within(rowB).getByText(/리포트 지연/);
  expect(last.closest("div")!.parentElement!.className).toContain("text-bad");
  expect(screen.getByText("리포트 지연 1대 / 전체 2대")).toBeInTheDocument();
});

test("노드 시각과 서버 수신 시각이 2분 넘게 다르면 fresh 라도 빨간 경고 -- 음수는 원인을 단정하지 않는다", async () => {
  // probed_at 은 프로브 시작 전에 찍혀 음수 차이는 노드 시계가 늦거나 프로브가 오래 걸린 것(리뷰) --
  // 양수(노드 시계가 빠름)는 프로브 지연으로 생기지 않아 "노드 시계" 로 단정한다.
  const behind = { ...NODES[0], node_name: "node-behind", reported_at: "2026-08-06T00:10:00Z",
                   report: { ...NODES[0].report, probed_at: "2026-08-06T00:00:00Z" } };
  const ahead = { ...NODES[0], node_name: "node-ahead", reported_at: "2026-08-06T00:00:00Z",
                  report: { ...NODES[0].report, probed_at: "2026-08-06T00:05:00Z" } };
  server.use(http.get("/api/admin/nodes", () => HttpResponse.json([behind, ahead])));
  wrap();
  const b = (await screen.findByText("node-behind")).closest("tr")!;
  expect(within(b).getByText(/시각 차이 -10분\(시계 또는 프로브 지연\)/)).toBeInTheDocument();
  const a = screen.getByText("node-ahead").closest("tr")!;
  expect(within(a).getByText(/노드 시계 \+5분/)).toBeInTheDocument();
});

test("ageText·clockSkewSeconds: 상대시각과 시계 차이 계산", () => {
  const now = Date.parse("2026-08-06T00:10:00Z");
  expect(ageText("2026-08-06T00:09:55Z", now)).toBe("방금");
  expect(ageText("2026-08-06T00:09:00Z", now)).toBe("1분 전");
  expect(ageText("2026-08-05T23:10:00Z", now)).toBe("1시간 전");
  expect(ageText("2026-08-06T00:20:00Z", now)).toBe("방금");      // 브라우저 시계가 늦음 -- 음수 나이 없음
  expect(ageText("garbage", now)).toBeNull();
  expect(clockSkewSeconds({ reported_at: "2026-08-06T00:00:30Z",
                            report: { probed_at: "2026-08-06T00:00:00Z" } })).toBe(-30);
  expect(clockSkewSeconds({ reported_at: "2026-08-06T00:00:30Z", report: {} })).toBeNull();
});


// ---- 노드 배치 제외·다시 포함·cordon(2026-10-02) ----

function renderPlacement(nodes: unknown[], on?: { put?: (body: unknown, name: string) => void; del?: (name: string) => void }) {
  server.use(
    http.get("/api/admin/nodes", () => HttpResponse.json(nodes)),
    http.put("/api/admin/nodes/:name/exclusion", async ({ request, params }) => {
      const body = await request.json();
      on?.put?.(body, String(params.name));
      return HttpResponse.json({ node_name: params.name, reason: (body as { reason: string | null }).reason,
                                 created_by: "admin", created_at: "2026-10-02T00:00:00Z" });
    }),
    http.delete("/api/admin/nodes/:name/exclusion", ({ params }) => {
      on?.del?.(String(params.name));
      return HttpResponse.json({ included: params.name });
    }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><NodesList /></QueryClientProvider>);
}

test("배치 제외: 사유를 적어 제외하면 PUT 바디에 사유가 실린다", async () => {
  const puts: [unknown, string][] = [];
  renderPlacement(NODES, { put: (b, n) => puts.push([b, n]) });
  await userEvent.click(await screen.findByRole("button", { name: "node-a 배치 제외" }));
  const dlg = await screen.findByRole("dialog", { name: "node-a 배치 제외" });
  expect(dlg).toHaveTextContent("실행 중인 작업은 건드리지 않습니다");
  await userEvent.type(within(dlg).getByLabelText("제외 사유"), "디스크 오류");
  await userEvent.click(within(dlg).getByRole("button", { name: "배치에서 제외" }));
  await waitFor(() => expect(puts).toEqual([[{ reason: "디스크 오류" }, "node-a"]]));
});

test("배치 제외: 빈 사유는 null 로 보낸다", async () => {
  const puts: unknown[] = [];
  renderPlacement(NODES, { put: (b) => puts.push(b) });
  await userEvent.click(await screen.findByRole("button", { name: "node-b 배치 제외" }));
  await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "배치에서 제외" }));
  await waitFor(() => expect(puts).toEqual([{ reason: null }]));
});

test("제외된 노드: 배지(사유 툴팁) + 다시 포함 -> DELETE, 머리글에 대수", async () => {
  const dels: string[] = [];
  const excluded = [{ ...NODES[0], exclusion: { node_name: "node-a", reason: "점검", created_by: "ops",
                                                 created_at: "2026-10-02T00:00:00Z" } }, NODES[1]];
  renderPlacement(excluded, { del: (n) => dels.push(n) });
  const badge = await screen.findByText("배치 제외", { selector: "span" });
  expect(badge).toHaveAttribute("title", expect.stringContaining("사유: 점검 · ops"));
  expect(screen.getByText(/배치 제외·cordon 1대/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "node-a 다시 포함" }));
  const dlg = await screen.findByRole("dialog", { name: "node-a 다시 포함" });
  expect(dlg).toHaveTextContent("제외 사유: 점검");
  await userEvent.click(within(dlg).getByRole("button", { name: "다시 포함" }));
  await waitFor(() => expect(dels).toEqual(["node-a"]));
  // 제외되지 않은 노드엔 다시 포함 버튼이 없다
  expect(screen.queryByRole("button", { name: "node-b 다시 포함" })).toBeNull();
});

test("cordon 된 노드: k8s 보고(schedulable=false)면 cordon 배지(사유 툴팁), 포탈 해제 버튼은 없다", async () => {
  const cordoned = [{ ...NODES[0], report: { ...NODES[0].report,
                                             k8s_node: { schedulable: false, reason: "cordoned" } } }];
  renderPlacement(cordoned);
  const badge = await screen.findByText("cordon");
  expect(badge).toHaveAttribute("title", expect.stringContaining("cordoned"));
  expect(badge).toHaveAttribute("title", expect.stringContaining("kubectl uncordon"));
  expect(screen.getByRole("button", { name: "node-a 배치 제외" })).toBeInTheDocument();
});


test("일시 조건(압박 등)은 cordon 이 아니라 '일시 불가' 배지 -- 머리글 대수에도 안 센다", async () => {
  const pressured = [{ ...NODES[0], report: { ...NODES[0].report, k8s_node: {
    schedulable: false, reason: "taint node.kubernetes.io/disk-pressure:NoSchedule", transient: true } } }];
  renderPlacement(pressured);
  const badge = await screen.findByText("일시 불가");
  expect(badge).toHaveAttribute("title", expect.stringContaining("이미 계획된 작업은 상태가 풀릴 때까지 기다립니다"));
  expect(screen.queryByText("cordon")).toBeNull();
  expect(screen.queryByText(/배치 제외·cordon/)).toBeNull();
});

test("cordon 된 노드를 다시 포함할 때는 uncordon 전까지 배치되지 않는다고 말한다", async () => {
  const both = [{ ...NODES[0], report: { ...NODES[0].report, k8s_node: { schedulable: false, reason: "cordoned" } },
                  exclusion: { node_name: "node-a", reason: null, created_by: "ops", created_at: "2026-10-02T00:00:00Z" } }];
  renderPlacement(both);
  await userEvent.click(await screen.findByRole("button", { name: "node-a 다시 포함" }));
  expect(await screen.findByRole("dialog", { name: "node-a 다시 포함" })).toHaveTextContent("uncordon 전까지는");
});


// ---- 2026-10-16 사용자 보고 「대시보드의 cpu 사용량은 어떻게 집계하는거야? 코어수가 128이어도 200이 넘어가고」 ----
// 예전 칸 "128코어 · 213.45" 는 load1 이 사용률처럼 읽혔다 -- 사용률(%)과 부하(load / 코어)를 나눠 이름 붙인다.

test("cpuLoadText: 사용률과 부하를 따로 이름 붙이고, 모르면 0 이 아니라 —", () => {
  expect(cpuLoadText({ cpu_used_pct: 42.3, load1: 213.45, cpu_count: 128 }))
    .toEqual({ usage: "42.3%", load: "부하 213.45 / 128코어" });
  // 구형 에이전트(키 없음): 사용률 —, 부하는 그대로
  expect(cpuLoadText({ load1: 213.45, cpu_count: 128 })).toEqual({ usage: "—", load: "부하 213.45 / 128코어" });
  // 재시작 직후 첫 보고(null)·범위 밖·문자열 오염도 모름
  for (const bad of [null, 120, -1, "42"])
    expect(cpuLoadText({ cpu_used_pct: bad, load1: 1 }).usage).toBe("—");
  // 0 은 유휴 노드의 정상값
  expect(cpuLoadText({ cpu_used_pct: 0 }).usage).toBe("0.0%");
  expect(cpuLoadText({})).toEqual({ usage: "—", load: "부하 —" });
});

const CPU_NODES = [
  { ...NODES[0], node_name: "busy",
    report: { ...NODES[0].report,
              os: { ...NODES[0].report.os, cpu_used_pct: 42.3, cpu_iowait_pct: 3.1, cpu_count: 128,
                    load1: 213.45, load5: 200.1, load15: 190.0 } } },
  { ...NODES[0], node_name: "old-agent",
    report: { ...NODES[0].report,
              os: { ...NODES[0].report.os, cpu_count: 128, load1: 213.45 } } },
];

test("목록 칸: 사용률 % 와 부하 / 코어가 분리돼 보이고, 구형 에이전트는 사용률 —", async () => {
  server.use(http.get("/api/admin/nodes", () => HttpResponse.json(CPU_NODES)));
  wrap();
  const busy = (await screen.findByText("busy")).closest("tr")!;
  expect(within(busy).getByText("42.3%", { exact: false })).toBeInTheDocument();
  expect(within(busy).getByText("· 부하 213.45 / 128코어", { exact: false })).toBeInTheDocument();
  const old = screen.getByText("old-agent").closest("tr")!;
  const cell = within(old).getByText(/부하 213\.45 \/ 128코어/).closest("td")!;
  expect(cell.textContent).toBe("— · 부하 213.45 / 128코어");
  expect(cell.textContent).not.toMatch(/0(\.0)?%/);
});

test("상세 타일: CPU 사용률(I/O 대기·코어)과 부하(load)는 다른 타일, 부하는 사용률이 아니라는 설명", async () => {
  server.use(http.get("/api/admin/nodes", () => HttpResponse.json(CPU_NODES)));
  wrap();
  const busy = (await screen.findByText("busy")).closest("tr")!;
  await userEvent.click(within(busy).getByRole("button", { name: "상세" }));
  const dialog = await screen.findByRole("dialog", { name: "busy 상세" });
  const cpuTile = within(dialog).getByText("CPU 사용률").parentElement!;
  expect(within(cpuTile).getByText("42.3%")).toBeInTheDocument();
  expect(within(cpuTile).getByText("I/O 대기 3.1% · 128코어")).toBeInTheDocument();
  const loadTile = within(dialog).getByText("부하(load 1·5·15분)").parentElement!;
  expect(within(loadTile).getByText("213.45")).toBeInTheDocument();
  expect(within(dialog).getByText(/코어 수를 넘을 수 있으며 CPU 사용률이 아닙니다/)).toBeInTheDocument();
});

test("상세 타일: 구형 에이전트는 CPU 사용률 — (0% 아님), 보조 줄은 아는 것(코어)만", async () => {
  server.use(http.get("/api/admin/nodes", () => HttpResponse.json(CPU_NODES)));
  wrap();
  const old = (await screen.findByText("old-agent")).closest("tr")!;
  await userEvent.click(within(old).getByRole("button", { name: "상세" }));
  const dialog = await screen.findByRole("dialog", { name: "old-agent 상세" });
  const cpuTile = within(dialog).getByText("CPU 사용률").parentElement!;
  expect(within(cpuTile).getByText("—")).toBeInTheDocument();
  expect(within(cpuTile).getByText("128코어")).toBeInTheDocument();
  expect(within(cpuTile).queryByText(/I\/O 대기/)).toBeNull();
});


// ---- 2026-11-25 사용자 요청 1: 노드 상세 마운트 표에 「관리 디렉토리」 열 ----

const cellTexts = (tr: HTMLElement) => [...tr.querySelectorAll("td")].map((td) => td.textContent);

test("상세 마운트 표: 마운트 경로 바로 뒤에 관리 디렉토리(등록 스토리지 값), 등록에 없는 스토리지는 —", async () => {
  server.use(http.get("/api/admin/nodes", () => HttpResponse.json(NODES)));
  wrap();
  const rowA = (await screen.findByText("node-a")).closest("tr")!;
  await userEvent.click(within(rowA).getByRole("button", { name: "상세" }));
  const dialog = await screen.findByRole("dialog", { name: "node-a 상세" });
  const vol1Row = (await within(dialog).findByText("/mnt/vol1/dms")).closest("tr")!;
  const mountTable = vol1Row.closest("table")!;
  expect(within(mountTable).getAllByRole("columnheader").map((h) => h.textContent))
    .toEqual(["스토리지", "마운트 경로", "관리 디렉토리", "상태", "쓰기", "사유"]);
  expect(cellTexts(vol1Row)).toEqual(["vol1", "/mnt/vol1", "/mnt/vol1/dms", "Ready", "—", "—"]);
  const vol2Row = within(mountTable).getByText("/mnt/vol2").closest("tr")!;
  expect(cellTexts(vol2Row)).toEqual(["vol2", "/mnt/vol2", "—", "Error", "—", "not mounted"]);
  expect(within(dialog).getByText(/관리 디렉토리는 지금 등록된 스토리지 값입니다/)).toBeInTheDocument();
});

test("상세 마운트 표: 스토리지 목록을 못 읽으면 관리 디렉토리는 — (경로를 지어내지 않는다)", async () => {
  let storageCalls = 0;
  server.use(
    http.get("/api/admin/nodes", () => HttpResponse.json(NODES)),
    http.get("/api/admin/storages", () => {
      storageCalls += 1;
      return HttpResponse.json({ detail: "boom" }, { status: 500 });
    }),
  );
  wrap();
  const rowA = (await screen.findByText("node-a")).closest("tr")!;
  await userEvent.click(within(rowA).getByRole("button", { name: "상세" }));
  const dialog = await screen.findByRole("dialog", { name: "node-a 상세" });
  await waitFor(() => expect(storageCalls).toBe(1));
  await new Promise((r) => setTimeout(r, 10));
  const vol1Row = within(dialog).getByText("/mnt/vol1").closest("tr")!;
  expect(cellTexts(vol1Row)[2]).toBe("—");
  expect(within(dialog).queryByText("/mnt/vol1/dms")).toBeNull();
});

test("목록 화면은 스토리지 목록을 부르지 않는다 -- 상세를 열 때만", async () => {
  let storageCalls = 0;
  server.use(
    http.get("/api/admin/nodes", () => HttpResponse.json(NODES)),
    http.get("/api/admin/storages", () => { storageCalls += 1; return HttpResponse.json(STORAGES); }),
  );
  wrap();
  await screen.findByText("node-a");
  await new Promise((r) => setTimeout(r, 10));
  expect(storageCalls).toBe(0);
});


// ---- 2026-11-25 OA 사이트: 사라지지 않는 리포트 지연 노드의 「기록 삭제」 ----

const EXCLUSION = { node_name: "node-b", reason: "교체", created_by: "ops", created_at: "2026-10-02T00:00:00Z" };

/** 노드 목록 + DELETE /api/admin/nodes/:name. respond 가 없으면 성공(그 노드를 목록에서 뺀다). */
function renderForget(initial: unknown[], opts: {
  respond?: () => Response | Promise<Response>;
  onDelete?: (pathname: string) => void;
} = {}) {
  let nodes = initial as { node_name: string; reported_at: string; exclusion?: unknown }[];
  let gets = 0;
  server.use(
    http.get("/api/admin/nodes", () => { gets += 1; return HttpResponse.json(nodes); }),
    http.delete("/api/admin/nodes/:name", async ({ request, params }) => {
      opts.onDelete?.(new URL(request.url).pathname);
      if (opts.respond) return opts.respond();
      const name = String(params.name);
      const before = nodes.find((n) => n.node_name === name)!;
      nodes = nodes.filter((n) => n.node_name !== name);
      return HttpResponse.json({ deleted: name, reported_at: before.reported_at, reports_deleted: 3,
                                 exclusion_deleted: !!before.exclusion });
    }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // 라우터: node_in_use 거절의 막는 요청은 요청 상세 링크(<Link>)로 그린다.
  render(<MemoryRouter><QueryClientProvider client={qc}><NodesList /></QueryClientProvider></MemoryRouter>);
  return { gets: () => gets };
}

test("「기록 삭제」는 리포트 지연 행에만 있다(nowrap) -- 정상 행엔 없고 표는 9열 그대로", async () => {
  renderForget(NODES);
  const rowB = (await screen.findByText("node-b")).closest("tr")!;
  const btn = within(rowB).getByRole("button", { name: "node-b 기록 삭제" });
  expect(btn).toHaveTextContent("기록 삭제");
  expect(btn).toHaveClass("whitespace-nowrap");
  // 상태 칸의 stale 표기는 그대로(text-bad)
  expect(within(rowB).getByText("stale")).toHaveClass("text-bad");
  const rowA = screen.getByText("node-a").closest("tr")!;
  expect(within(rowA).queryByRole("button", { name: /기록 삭제/ })).toBeNull();
  expect(screen.getAllByRole("columnheader")).toHaveLength(9);
  expect(screen.getByText(/리포트 지연 노드의 「기록 삭제」 = 이름이 바뀌었거나 클러스터에서 뺀 노드의 옛 행 정리/))
    .toBeInTheDocument();
});

test("확인 창: 되돌릴 수 없음·같은 이름이 다시 보고하면 새로 생김·마지막 보고, 배치 제외가 있으면 함께 지워진다고 말한다", async () => {
  renderForget([NODES[0], { ...NODES[1], exclusion: EXCLUSION }]);
  await userEvent.click(await screen.findByRole("button", { name: "node-b 기록 삭제" }));
  const dlg = await screen.findByRole("dialog", { name: "node-b 기록 삭제" });
  expect(dlg).toHaveTextContent("최신 보고·보고 이력·배치 제외");
  expect(dlg).toHaveTextContent("되돌릴 수 없습니다");
  expect(dlg).toHaveTextContent("같은 이름의 노드가 다시 보고하면 새 행으로 다시 생깁니다");
  expect(dlg).toHaveTextContent("배치 제외도 함께 지워집니다");
  expect(dlg).toHaveTextContent("빌드 노드로 지정돼 있거나");
  // 이 라인엔 k8s 연동 호출 노드 설정이 없다 -- 창이 없는 설정을 안내하지 않는다.
  expect(dlg).not.toHaveTextContent("k8s");
  expect(dlg).toHaveTextContent("마지막 보고: 2026-08-05 09:00:00 KST");
  expect(within(dlg).getByRole("button", { name: "기록 삭제" })).toBeInTheDocument();
});

test("확인 창: 배치 제외가 없으면 그 문장이 없다", async () => {
  renderForget(NODES);
  await userEvent.click(await screen.findByRole("button", { name: "node-b 기록 삭제" }));
  const dlg = await screen.findByRole("dialog", { name: "node-b 기록 삭제" });
  expect(dlg).toHaveTextContent("최신 보고·보고 이력)");
  expect(dlg).not.toHaveTextContent("배치 제외도 함께");
});

test("기록 삭제 확인 → DELETE /api/admin/nodes/node-b, 성공하면 결과 줄(포커스)·목록 재조회로 행이 사라진다", async () => {
  const paths: string[] = [];
  const r = renderForget([NODES[0], { ...NODES[1], exclusion: EXCLUSION }], { onDelete: (p) => paths.push(p) });
  await userEvent.click(await screen.findByRole("button", { name: "node-b 기록 삭제" }));
  const dlg = await screen.findByRole("dialog", { name: "node-b 기록 삭제" });
  const getsBefore = r.gets();
  await userEvent.click(within(dlg).getByRole("button", { name: "기록 삭제" }));
  await waitFor(() => expect(paths).toEqual(["/api/admin/nodes/node-b"]));
  const status = await screen.findByText("node-b 기록을 지웠습니다(보고 이력 3건·배치 제외).");
  expect(status).toHaveAttribute("role", "status");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await waitFor(() => expect(status).toHaveFocus());
  expect(r.gets()).toBeGreaterThan(getsBefore);
  expect(screen.queryByText("node-b")).toBeNull();
  expect(screen.getByText("node-a")).toBeInTheDocument();
});

test("마지막 노드를 지워 행이 모두 사라져도 결과 줄은 남는다", async () => {
  renderForget([NODES[1]]);
  await userEvent.click(await screen.findByRole("button", { name: "node-b 기록 삭제" }));
  await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "기록 삭제" }));
  const status = await screen.findByText("node-b 기록을 지웠습니다(보고 이력 3건).");
  await waitFor(() => expect(screen.queryByText("node-b")).toBeNull());
  expect(status).toBeInTheDocument();
  expect(status).toHaveAttribute("role", "status");
});

test.each([
  ["node_fresh", 409, [reasonText("node_fresh")]],
  ["node_in_use:build_node,active_jobs", 409, [reasonText("node_in_use"), "막는 것: 빌드 노드 · 진행 중 작업"]],
  ["node_not_found", 404, [reasonText("node_not_found")]],
  ["admin_session_required", 403, [reasonText("admin_session_required")]],
])("거절 %s(%i)은 창 안 alert 로 보이고 창은 열린 채다", async (detail, status, expected) => {
  renderForget(NODES, { respond: () => HttpResponse.json({ detail }, { status }) });
  await userEvent.click(await screen.findByRole("button", { name: "node-b 기록 삭제" }));
  const dlg = await screen.findByRole("dialog", { name: "node-b 기록 삭제" });
  await userEvent.click(within(dlg).getByRole("button", { name: "기록 삭제" }));
  const alert = await within(dlg).findByRole("alert");
  for (const text of expected) expect(alert).toHaveTextContent(text);
  expect(screen.getByRole("dialog", { name: "node-b 기록 삭제" })).toBeInTheDocument();
});

test("node_in_use 의 막는 요청(;requests=)은 창 안에서 요청 상세 링크로 보인다", async () => {
  const rid = "0123456789abcdef0123456789abcdef";
  renderForget(NODES, { respond: () => HttpResponse.json({ detail: `node_in_use:active_jobs;requests=${rid},req-2` },
                                                           { status: 409 }) });
  await userEvent.click(await screen.findByRole("button", { name: "node-b 기록 삭제" }));
  const dlg = await screen.findByRole("dialog", { name: "node-b 기록 삭제" });
  await userEvent.click(within(dlg).getByRole("button", { name: "기록 삭제" }));
  const alert = await within(dlg).findByRole("alert");
  expect(alert).toHaveTextContent("막는 것: 진행 중 작업");
  expect(alert).not.toHaveTextContent("requests=");
  const link = within(alert).getByRole("link", { name: rid.slice(0, 12) });
  expect(link).toHaveAttribute("href", `/jobs/${rid}`);
  expect(link).toHaveAttribute("title", rid);
  expect(within(alert).getByRole("link", { name: "req-2" })).toHaveAttribute("href", "/jobs/req-2");
  expect(alert).toHaveTextContent("요청 상세에서 취소하거나 끝나기를 기다린 뒤 다시 시도하세요");
});

test("node_in_use 에 요청이 없으면(빌드 노드만) 링크 줄이 없다", async () => {
  renderForget(NODES, { respond: () => HttpResponse.json({ detail: "node_in_use:build_node" }, { status: 409 }) });
  await userEvent.click(await screen.findByRole("button", { name: "node-b 기록 삭제" }));
  const dlg = await screen.findByRole("dialog", { name: "node-b 기록 삭제" });
  await userEvent.click(within(dlg).getByRole("button", { name: "기록 삭제" }));
  const alert = await within(dlg).findByRole("alert");
  expect(alert).toHaveTextContent("막는 것: 빌드 노드");
  expect(within(alert).queryByRole("link")).toBeNull();
});

test("거절 뒤 취소로 닫으면 포커스는 연 버튼으로 돌아가고, 다시 열면 옛 오류가 없다", async () => {
  renderForget(NODES, { respond: () => HttpResponse.json({ detail: "node_fresh" }, { status: 409 }) });
  await userEvent.click(await screen.findByRole("button", { name: "node-b 기록 삭제" }));
  let dlg = await screen.findByRole("dialog", { name: "node-b 기록 삭제" });
  await userEvent.click(within(dlg).getByRole("button", { name: "기록 삭제" }));
  await within(dlg).findByRole("alert");
  await userEvent.click(within(dlg).getByRole("button", { name: "취소" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await waitFor(() => expect(screen.getByRole("button", { name: "node-b 기록 삭제" })).toHaveFocus());
  await userEvent.click(screen.getByRole("button", { name: "node-b 기록 삭제" }));
  dlg = await screen.findByRole("dialog", { name: "node-b 기록 삭제" });
  expect(within(dlg).queryByRole("alert")).toBeNull();
});

test("보내는 동안은 Esc 로 닫히지 않고 버튼이 잠긴다 -- 응답이 오면 닫힌다", async () => {
  let release!: () => void;
  const gate = new Promise<void>((r) => { release = r; });
  renderForget(NODES, { respond: async () => {
    await gate;
    return HttpResponse.json({ deleted: "node-b", reported_at: "2026-08-05T00:00:00Z", reports_deleted: 0,
                               exclusion_deleted: false });
  } });
  await userEvent.click(await screen.findByRole("button", { name: "node-b 기록 삭제" }));
  const dlg = await screen.findByRole("dialog", { name: "node-b 기록 삭제" });
  await userEvent.click(within(dlg).getByRole("button", { name: "기록 삭제" }));
  expect(await within(dlg).findByRole("status")).toHaveTextContent("지우는 중");
  await userEvent.keyboard("{Escape}");
  expect(screen.getByRole("dialog", { name: "node-b 기록 삭제" })).toBeInTheDocument();
  expect(within(dlg).getByRole("button", { name: "기록 삭제" })).toBeDisabled();
  expect(within(dlg).getByRole("button", { name: "취소" })).toBeDisabled();
  release();
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(await screen.findByText("node-b 기록을 지웠습니다(보고 이력 0건).")).toBeInTheDocument();
});

test("새 이름 힌트: 리포트 지연 행의 renamed_to 만 그린다(정상 행·null 은 없음), 확인 창·상세에도 보인다", async () => {
  const nodes = [
    { ...NODES[0], renamed_to: "should-not-show" },
    { ...NODES[1], renamed_to: "node-c" },
    { ...NODES[1], node_name: "node-d", renamed_to: null },
  ];
  renderForget(nodes);
  const rowB = (await screen.findByText("node-b")).closest("tr")!;
  expect(within(rowB).getByText("새 이름: node-c")).toBeInTheDocument();
  const rowA = screen.getByText("node-a").closest("tr")!;
  expect(within(rowA).queryByText(/새 이름/)).toBeNull();
  const rowD = screen.getByText("node-d").closest("tr")!;
  expect(within(rowD).queryByText(/새 이름/)).toBeNull();
  // 확인 창(연 순간의 스냅숏)
  await userEvent.click(within(rowB).getByRole("button", { name: "node-b 기록 삭제" }));
  const dlg = await screen.findByRole("dialog", { name: "node-b 기록 삭제" });
  expect(dlg).toHaveTextContent("같은 장비로 보이는 새 이름: node-c");
  await userEvent.click(within(dlg).getByRole("button", { name: "취소" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  // 상세 상태 줄
  await userEvent.click(within(rowB).getByRole("button", { name: "상세" }));
  const detail = await screen.findByRole("dialog", { name: "node-b 상세" });
  expect(within(detail).getByText("새 이름: node-c")).toBeInTheDocument();
});

test("renameHint·nodeInUseText: 표시 조건과 근거 토큰 번역(모르는 토큰은 원문)", () => {
  expect(renameHint({ fresh: false, renamed_to: "x" })).toBe("x");
  expect(renameHint({ fresh: true, renamed_to: "x" })).toBeNull();
  expect(renameHint({ fresh: false, renamed_to: null })).toBeNull();
  expect(renameHint({ fresh: false, renamed_to: "" })).toBeNull();
  expect(renameHint({ fresh: false })).toBeNull();
  const base = reasonText("node_in_use");
  expect(nodeInUseText("node_in_use:build_node,active_build,active_jobs"))
    .toBe(`${base} (막는 것: 빌드 노드 · 진행 중 빌드 · 진행 중 작업)`);
  expect(nodeInUseText("node_in_use:future_ref")).toBe(`${base} (막는 것: future_ref)`);
  // dms-k8s 라인의 호출 노드 토큰은 이 라인 서버가 보내지 않는다 -- 와도 모르는 토큰처럼 원문으로 보인다.
  expect(nodeInUseText("node_in_use:k8s_primary")).toBe(`${base} (막는 것: k8s_primary)`);
  // 사유 문구도 이 라인에 없는 설정(k8s 연동 호출 노드)을 안내하지 않는다.
  expect(base).not.toMatch(/k8s/);
  expect(base).toContain("컨트롤 상태의 빌드 노드");
  expect(nodeInUseText("node_in_use")).toBe(base);
  expect(nodeInUseText("node_in_use:")).toBe(base);
  // 막는 요청 id 는 문구에 섞지 않는다(창이 링크로 그린다)
  expect(nodeInUseText("node_in_use:build_node,active_jobs;requests=r1,r2"))
    .toBe(`${base} (막는 것: 빌드 노드 · 진행 중 작업)`);
  expect(parseNodeInUse("node_in_use:build_node,active_jobs;requests=r1, r2"))
    .toEqual({ refs: ["build_node", "active_jobs"], requests: ["r1", "r2"] });
  expect(parseNodeInUse("node_in_use:active_jobs")).toEqual({ refs: ["active_jobs"], requests: [] });
  expect(parseNodeInUse("node_in_use:active_jobs;future=x")).toEqual({ refs: ["active_jobs"], requests: [] });
  expect(parseNodeInUse("node_in_use")).toEqual({ refs: [], requests: [] });
  // reasonText 의 접두 규칙으로도 번역된다(다른 화면이 이 코드를 받아도 영문 원문이 아니다)
  expect(reasonText("node_in_use:build_node")).toBe(`${base} (build_node)`);
});
