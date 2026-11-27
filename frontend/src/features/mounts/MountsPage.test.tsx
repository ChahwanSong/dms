import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";
import { MountsPage } from "./MountsPage";
import {
  baseDetail, baseOf, baseProblem, cellProblem, columnNameLines, coverMountText, filterColumns, nodeHasProblem, nodesOf, pathTail,
  peersOf, reasonLong, reasonShort, storagesOf, summaryOf,
} from "./mountView";
import type { MountBaseCell, MountNode, MountsOverview } from "../../lib/types";

// 마운트 현황(2026-11-23 사용자 요청 「스토리지 하위 마운트 현황 메뉴 -- 노드 × 스토리지 표」). 서버 계약 = 설계 apibase v2 §G.
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const ago = (s: number) => new Date(Date.now() - s * 1000).toISOString();
const STORAGES = [
  { storage_name: "cephfs", mount_path: "/mgmt_storage", enabled: true },
  { storage_name: "gpfs", mount_path: "/gpfs/fs1", enabled: true },
  { storage_name: "old-nfs", mount_path: "/nfs/old", enabled: false },
];
const ready = { status: "ready", reason: null, exists: true, is_mountpoint: true, readable: true, writable: true } as const;
const baseOk = { status: "ready", reason: null, agent: { exists: true, writable: true, path_matches: true }, api: [], controller: null };
const node = (name: string, over: Partial<MountNode> = {}): MountNode => ({
  node_name: name, has_agent: true, fresh: true, reported_at: ago(20), probe_mode: "host_root",
  schedulable: true, schedulable_reason: null, excluded: false, roles: [],
  cells: { cephfs: ready, gpfs: ready, "old-nfs": { status: "disabled", reason: null } },
  artifact_base: baseOk as MountNode["artifact_base"], ...over });

// 사고 모양: API 파드가 뜬 s-ssc-dms-26001 에 cephfs 가 빠졌다(빈 로컬 디렉터리 = 마운트 아님). 컨트롤러 노드는 정상.
const NODES: MountNode[] = [
  node("ion2110", { roles: ["controller"],
    artifact_base: { ...baseOk, controller: { ok: true, reason: null, checked_at: ago(10) } } as MountNode["artifact_base"] }),
  node("s-ssc-dms-26001", { roles: ["api"],
    cells: { cephfs: { status: "missing", reason: "not_a_mountpoint", exists: true, is_mountpoint: false, readable: true,
                       writable: true },
             gpfs: ready, "old-nfs": { status: "disabled", reason: null } },
    artifact_base: { status: "missing", reason: "covering_storage_missing:cephfs",
                     agent: { exists: true, writable: true, path_matches: true },
                     api: [{ instance: "dms-api-7c9d-xyz12", ok: false, reason: "artifact_base_jobs_missing",
                             checked_at: ago(30), gate_state: "started_degraded" }], controller: null } }),
  node("w3", { fresh: false, reported_at: ago(900), excluded: true, schedulable: false, schedulable_reason: "cordon",
    cells: { cephfs: ready, gpfs: { status: "missing", reason: "missing_mount_path" }, "old-nfs": { status: "disabled", reason: null } } }),
  node("w4", { cells: { cephfs: ready, gpfs: { ...ready, status: "readonly", writable: false },
                        "old-nfs": { status: "disabled", reason: null } } }),
  node("cp1", { has_agent: false, fresh: false, reported_at: null, probe_mode: null, roles: ["api"], cells: {},
    artifact_base: { status: "unreported", reason: null, agent: null,
                     api: [{ instance: "dms-api-7c9d-abcde", ok: true, reason: null, checked_at: ago(5), gate_state: "passed" }],
                     controller: null } }),
];
const OVERVIEW: MountsOverview = {
  generated_at: ago(1),
  artifact_base: { uri: "file:///mgmt_storage/dms/artifacts", path: "/mgmt_storage/dms/artifacts", covering_storage: "cephfs" },
  storages: STORAGES, nodes: NODES,
  summary: { node_count: 5, fresh_node_count: 3, storage_count: 3, problem_cells: 3, problem_nodes: 2, stale_node_count: 1 },
};

// 보기 상태(?only·?cols·?q)는 주소에 산다 -- 지금 주소의 search 를 화면 밖 칸에 비춰 단언한다.
function LocationProbe() {
  return <span data-testid="loc">{useLocation().search}</span>;
}
function renderPage(body: unknown = OVERVIEW, status = 200, url = "/admin/mounts") {
  server.use(http.get("/api/admin/mounts", () => HttpResponse.json(body as object, { status })));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[url]}><MountsPage /><LocationProbe /></MemoryRouter>
    </QueryClientProvider>);
}
const loc = () => screen.getByTestId("loc").textContent;
const headTexts = () => within(screen.getByRole("table")).getAllByRole("columnheader").map((h) => h.textContent);
const rowOf = (name: string) => screen.getByRole("link", { name }).closest("tr")!;
const cellsOf = (name: string) => within(rowOf(name)).getAllByRole("cell");

test("h1 은 사이드바 라벨과 같은 「마운트 현황」, 요약 줄은 노드·보고 끊김·스토리지·문제 칸(서버 값)", async () => {
  renderPage();
  expect(await screen.findByRole("heading", { level: 1, name: "마운트 현황" })).toBeInTheDocument();
  expect(await screen.findByText("노드 5대")).toBeInTheDocument();
  expect(screen.getByText("보고 끊김 1대")).toBeInTheDocument();
  expect(screen.getByText("스토리지 3개")).toBeInTheDocument();
  expect(screen.getByText("문제 3칸 · 노드 2대")).toBeInTheDocument();
  expect(screen.queryByText(/정상 마운트하고 있습니다/)).toBeNull();
});

test("표: 머리 = 노드 · 스토리지(이름 · mount_path · 꼬리표 -- 덮는 스토리지·비활성) · 아티팩트 경로(경로 + 덮는 스토리지)", async () => {
  renderPage();
  const table = await screen.findByRole("table");
  const heads = within(table).getAllByRole("columnheader");
  expect(heads.map((h) => h.textContent)).toEqual([
    "노드", "cephfs/mgmt_storage(아티팩트 경로를 덮는 스토리지)", "gpfs/gpfs/fs1", "old-nfs/nfs/old비활성",
    "아티팩트 경로/mgmt_storage/dms/artifacts덮는 스토리지 cephfs",
  ]);
  expect(within(heads[3]).getByText("old-nfs").className).toContain("text-muted");
  expect(within(heads[1]).getByText("/mgmt_storage").className).toContain("font-mono");
  // 첫 열·머리 줄 고정(sticky) -- 모서리 z 3 > 머리 z 2 > 노드 칸 z 1, 가로·세로 넘침은 래퍼 안에서만(isolate 가 z 를 가둔다)
  expect(heads[0].className).toMatch(/\bsticky\b.*\bleft-0\b.*\btop-0\b.*z-\[3\]/);
  for (const h of heads.slice(1)) expect(h.className).toMatch(/\bsticky\b.*\btop-0\b.*z-\[2\]/);
  expect(cellsOf("ion2110")[0].className).toMatch(/\bsticky\b.*\bleft-0\b.*z-\[1\]/);
  const wrap = table.parentElement!;
  expect(wrap.className).toMatch(/\boverflow-auto\b/);
  expect(wrap.className).toContain("isolate");
});

test("칸: 상태 점 + 짧은 글자, 없음은 짧은 사유를 붙이고 긴 설명은 title·낭독 문장에 -- 문제 칸은 옅은 바탕", async () => {
  renderPage();
  await screen.findByRole("table");
  const [, ceph, gpfs, nfs, base] = cellsOf("s-ssc-dms-26001");
  expect(ceph).toHaveTextContent("없음마운트 아님");                   // 짧은 사유는 둘째 줄(고정 폭 칸)
  const mark = within(ceph).getByTitle(/s-ssc-dms-26001 · cephfs: 경로는 있지만 마운트포인트가 아닙니다/);
  expect(within(mark).getByText(/^\(s-ssc-dms-26001 · cephfs: 경로는 있지만 마운트포인트가 아닙니다/).className).toContain("sr-only");
  expect(ceph.className).toContain("bg-badbg/60");
  expect(gpfs).toHaveTextContent("마운트됨");
  expect(gpfs.className).not.toContain("bg-badbg");
  expect(nfs).toHaveTextContent("비활성");
  // 아티팩트 경로: 덮는 스토리지 미마운트 + 그 노드의 API 자체 점검 실패
  expect(base).toHaveTextContent("없음cephfs 미마운트");
  expect(within(base).getByText("API 실패")).toHaveAttribute(
    "title", expect.stringMatching(/^API dms-api-7c9d-xyz12: 최근 작업의 기록 디렉터리가 이 경로에 없습니다/));
  const [, , w4gpfs] = cellsOf("w4");
  expect(w4gpfs).toHaveTextContent("읽기 전용");
  // 같은 열에 쓰기 가능한 신선한 노드(ion2110·s-ssc 의 gpfs)가 있어 문제 -- 주의 바탕·글자, 설명이 이유를 말한다
  expect(w4gpfs.className).toContain("bg-attnbg/60");
  expect(within(w4gpfs).getByText("읽기 전용").className).toContain("text-attn");
  expect(within(w4gpfs).getByTitle(/w4 · gpfs: 마운트됐지만 쓰기 불가\(읽기 전용\) — 같은 스토리지를 쓰기 가능으로 붙인 노드가 있어 문제로 셉니다$/))
    .toBeInTheDocument();
  // 컨트롤러 노드의 아티팩트 경로 칸은 컨트롤러 점검을 겸한다
  expect(within(cellsOf("ion2110")[4]).getByText("컨트롤러 정상")).toBeInTheDocument();
  // 에이전트 없는 제어면 노드: 칸은 「보고 없음」, API 점검은 그대로
  const cp = cellsOf("cp1");
  expect(cp[1]).toHaveTextContent("보고 없음");
  expect(within(cp[1]).getByTitle(/에이전트가 없어 마운트를 보고하지 않습니다/)).toBeInTheDocument();
  expect(within(cp[4]).getByText("API 정상")).toBeInTheDocument();
});

test("노드 배지: API·컨트롤러·배치 제외·스케줄 불가·보고 끊김·에이전트 없음 -- 노드 이름은 노드 화면 링크", async () => {
  renderPage();
  await screen.findByRole("table");
  expect(within(cellsOf("s-ssc-dms-26001")[0]).getByText("API")).toBeInTheDocument();
  expect(within(cellsOf("ion2110")[0]).getByText("컨트롤러")).toBeInTheDocument();
  const w3 = within(cellsOf("w3")[0]);
  expect(w3.getByText("보고 끊김")).toBeInTheDocument();
  expect(w3.getByText("배치 제외")).toBeInTheDocument();
  expect(w3.getByText("스케줄 불가")).toHaveAttribute("title", "k8s 스케줄 불가(cordon)");
  const cp = within(cellsOf("cp1")[0]);
  expect(cp.getByText("에이전트 없음")).toBeInTheDocument();
  expect(cp.getByText("API")).toBeInTheDocument();
  expect(cp.queryByText("보고 끊김")).toBeNull();                       // 에이전트가 없는 것은 끊긴 것이 아니다
  expect(within(cellsOf("w4")[0]).queryByText(/API|컨트롤러|보고 끊김|배치 제외|스케줄 불가|에이전트 없음/)).toBeNull();
  expect(screen.getByRole("link", { name: "w4" })).toHaveAttribute("href", "/admin/nodes");
});

test("보고 끊김 행은 흐린 색(opacity 아님 -- 고정 칸이 비치지 않게), 문제 칸 바탕도 칠하지 않는다", async () => {
  renderPage();
  await screen.findByRole("table");
  const row = rowOf("w3");
  expect(row.className).not.toContain("opacity");
  expect(screen.getByRole("link", { name: "w3" }).className).toContain("text-muted");
  const gpfs = cellsOf("w3")[2];
  expect(gpfs).toHaveTextContent("없음경로 없음");                     // 옛 서버 모양(missing + missing_mount_path)은 그대로
  expect(gpfs.className).not.toContain("bg-badbg");
  expect(gpfs.querySelector("[aria-hidden].rounded-full")!.className).toContain("bg-bad/40");
  // 살아 있는 행의 점은 원색
  expect(cellsOf("s-ssc-dms-26001")[1].querySelector("[aria-hidden].rounded-full")!.className).toMatch(/\bbg-bad\b/);
});

test("「문제만 보기」: 문제 행(없음·읽기 전용·아티팩트 경로·보고 끊김)만 -- 상태는 주소(?only=problems), 다시 누르면 전체", async () => {
  renderPage();
  await screen.findByRole("table");
  const toggle = screen.getByRole("button", { name: /문제만 보기/ });
  expect(toggle).toHaveAttribute("aria-pressed", "false");
  expect(toggle).toHaveTextContent("문제만 보기3");
  await userEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-pressed", "true");
  expect(loc()).toBe("?only=problems");
  const names = within(screen.getByRole("table")).getAllByRole("row").slice(1)
    .map((r) => within(r).getAllByRole("link")[0].textContent);
  expect(names).toEqual(["s-ssc-dms-26001", "w3", "w4"]);
  await userEvent.click(toggle);
  expect(loc()).toBe("");
  expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(6);
});

test("주소로 들어온 ?only=problems 는 첫 렌더부터 문제 행만(공유·새로 고침에 유지)", async () => {
  renderPage(OVERVIEW, 200, "/admin/mounts?only=problems");
  await screen.findByRole("table");
  expect(screen.getByRole("button", { name: /문제만 보기/ })).toHaveAttribute("aria-pressed", "true");
  expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(4);
});

test("문제 0: 요약은 「모든 노드가 … 정상 마운트」, 문제만 보기는 빈 상태 + 「전체 보기」", async () => {
  renderPage({ ...OVERVIEW, nodes: [NODES[0], node("w4")],
               summary: { node_count: 2, fresh_node_count: 2, storage_count: 3, problem_cells: 0, problem_nodes: 0,
                          stale_node_count: 0 } });
  expect(await screen.findByText("모든 노드가 등록된 스토리지를 정상 마운트하고 있습니다")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /문제만 보기/ }));
  expect(screen.getByText("문제가 있는 노드가 없습니다")).toBeInTheDocument();
  expect(screen.queryByRole("table")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "전체 보기" }));
  expect(screen.getByRole("table")).toBeInTheDocument();
  expect(loc()).toBe("");
});

test("문제 0 이어도 보고가 끊긴 노드가 있으면 「모든 노드」라고 단정하지 않는다", async () => {
  renderPage({ ...OVERVIEW, nodes: [NODES[0], node("w9", { fresh: false })],
               summary: { node_count: 2, fresh_node_count: 1, storage_count: 3, problem_cells: 0, problem_nodes: 0,
                          stale_node_count: 1 } });
  expect(await screen.findByText("보고 중인 노드는 모두 등록된 스토리지를 정상 마운트하고 있습니다")).toBeInTheDocument();
  expect(screen.getByText("보고 끊김 1대")).toBeInTheDocument();
});

test("에이전트는 정상인데 그 노드의 API 점검이 실패하면 아티팩트 경로 칸은 「점검 실패」(빨간 바탕) -- 초록 「정상」으로 두지 않는다", async () => {
  // 2026-11-23 라이브: 롤링 중 Ready 를 기다리는 새 API 파드가 실패하는데 칸 머리가 초록 「정상」이라 한 칸이 두 말을 했다.
  const failing = node("w5", { roles: ["api"], artifact_base: { ...baseOk,
    api: [{ instance: "dms-api-new", ok: false, reason: "artifact_base_missing", checked_at: ago(5), gate_state: "waiting" }],
  } as MountNode["artifact_base"] });
  renderPage({ ...OVERVIEW, nodes: [failing], summary: undefined as never });
  const row = (await screen.findByRole("link", { name: "w5" })).closest("tr")!;
  const cells = within(row).getAllByRole("cell");
  const baseCell = cells[cells.length - 1];
  expect(within(baseCell).getByText("점검 실패")).toBeInTheDocument();
  expect(within(baseCell).queryByText("정상")).toBeNull();
  expect(baseCell.className).toContain("bg-badbg");
  // 설명(title·낭독)도 덮인 머리와 같은 말 -- 실패한 역할부터, 에이전트 관찰은 뒤에(dms-k8s e25a2dfb 역할 이름 이식).
  const detail = "w5 · 아티팩트 경로: 이 노드의 API 파드가 아티팩트 경로 점검에 실패했습니다 — 에이전트는 경로를 확인했습니다(존재·쓰기). "
    + "파드의 hostPath·마운트 전파와 노드의 공유 스토리지 마운트를 확인하세요(아티팩트 경로 화면에 파드별 사유)";
  expect(within(baseCell).getByTitle(detail)).toBeInTheDocument();
  expect(within(baseCell).getByText(`(${detail})`)).toHaveClass("sr-only");
  expect(within(baseCell).queryByTitle(/에이전트가 경로를 확인했습니다/)).toBeNull();
});

test("「점검 실패」 설명은 실패한 역할만 이름으로 -- 컨트롤러만·둘 다, 에이전트 없는 노드는 에이전트 확인 없음을 말한다", async () => {
  const ctrlOnly = node("w6", { roles: ["api", "controller"], artifact_base: { ...baseOk,
    api: [{ instance: "dms-api-a", ok: true, reason: null, checked_at: ago(5), gate_state: "passed" }],
    controller: { ok: false, reason: "artifact_base_not_writable", checked_at: ago(5) },
  } as MountNode["artifact_base"] });
  const both = node("cp2", { has_agent: false, fresh: false, reported_at: null, probe_mode: null, roles: ["api", "controller"],
    cells: {}, artifact_base: { status: "unreported", reason: null, agent: null,
      api: [{ instance: "dms-api-b", ok: 0, reason: "artifact_base_missing", checked_at: ago(5), gate_state: "waiting" }],
      controller: { ok: false, reason: "artifact_base_missing", checked_at: ago(5) } } as unknown as MountNode["artifact_base"] });
  // 에이전트가 이미 문제(없음)면 머리를 덮지 않고 설명도 에이전트 사유 그대로
  const agentBad = node("w7", { roles: ["api"], artifact_base: { status: "missing", reason: "not_a_mountpoint",
    agent: { exists: true, writable: true, path_matches: true },
    api: [{ instance: "dms-api-c", ok: false, reason: "artifact_base_jobs_missing", checked_at: ago(5), gate_state: "passed" }],
    controller: null } as MountNode["artifact_base"] });
  renderPage({ ...OVERVIEW, nodes: [ctrlOnly, both, agentBad], summary: undefined as never });
  const baseCellOf = async (name: string) => {
    const cells = within((await screen.findByRole("link", { name })).closest("tr")!).getAllByRole("cell");
    return cells[cells.length - 1];
  };
  expect(within(await baseCellOf("w6")).getByTitle(
    /^w6 · 아티팩트 경로: 이 노드의 컨트롤러 파드가 아티팩트 경로 점검에 실패했습니다 — 에이전트는 경로를 확인했습니다/))
    .toBeInTheDocument();
  const cp2 = await baseCellOf("cp2");
  expect(within(cp2).getByText("점검 실패")).toBeInTheDocument();
  expect(within(cp2).getByTitle(
    /^cp2 · 아티팩트 경로: 이 노드의 API·컨트롤러 파드가 아티팩트 경로 점검에 실패했습니다 — 에이전트가 없어 노드 쪽 경로는 확인되지 않았습니다\. /))
    .toBeInTheDocument();
  const w7 = await baseCellOf("w7");
  expect(within(w7).queryByText("점검 실패")).toBeNull();
  expect(within(w7).getByTitle(/^w7 · 아티팩트 경로: 경로는 있지만 마운트포인트가 아닙니다/)).toBeInTheDocument();
});

test("보고 중인 노드가 하나도 없으면 「정상」이라고 말하지 않는다(새 설치·에이전트 전체 장애)", async () => {
  renderPage({ ...OVERVIEW, nodes: [node("w9", { fresh: false })],
               summary: { node_count: 1, fresh_node_count: 0, storage_count: 3, problem_cells: 0, problem_nodes: 0,
                          stale_node_count: 1 } });
  expect(await screen.findByText("보고 중인 노드가 없어 마운트 상태를 알 수 없습니다")).toBeInTheDocument();
  expect(screen.queryByText(/정상 마운트하고 있습니다/)).toBeNull();
});

test("옛·부분 응답: summary·cells·roles 없음, enabled 정수, artifact_base null 이어도 그린다(요약은 표와 같은 규칙으로 센다)", async () => {
  renderPage({
    generated_at: ago(1), artifact_base: null,
    storages: [{ storage_name: "cephfs", mount_path: "/mgmt_storage", enabled: 1 },
               { storage_name: "off", mount_path: "/off", enabled: 0 }],
    nodes: [
      { node_name: "a", has_agent: true, fresh: true, reported_at: ago(5), artifact_base: null },
      { node_name: "b", has_agent: true, fresh: true, reported_at: ago(5),
        cells: { cephfs: { status: "missing", reason: "propagation_stale" }, off: { status: "ready", reason: null } } },
      "garbage",
    ],
  });
  await screen.findByRole("table");
  expect(screen.getByText("노드 2대")).toBeInTheDocument();
  expect(screen.getByText("스토리지 2개")).toBeInTheDocument();
  expect(screen.getByText("문제 1칸 · 노드 1대")).toBeInTheDocument();
  const a = cellsOf("a");
  expect(a[1]).toHaveTextContent("보고 없음");
  expect(a[2]).toHaveTextContent("비활성");                             // 머리 열(enabled 0)이 진실
  expect(a[3]).toHaveTextContent("—");
  expect(cellsOf("b")[1]).toHaveTextContent("없음전파 끊김");
  expect(cellsOf("b")[2]).toHaveTextContent("비활성");                  // 셀이 ready 라고 해도 비활성 열
  expect(screen.getByRole("columnheader", { name: /^아티팩트 경로/ })).toHaveTextContent("덮는 등록 스토리지 없음");
});

test("노드가 없으면 빈 상태, 스토리지가 없으면 등록 안내 -- 오류는 서버 사유", async () => {
  const view = renderPage({ ...OVERVIEW, nodes: [], summary: { ...OVERVIEW.summary, node_count: 0, problem_cells: 0 } });
  expect(await screen.findByText("보고한 노드가 없습니다")).toBeInTheDocument();
  view.unmount();
  const v2 = renderPage({ ...OVERVIEW, storages: [], nodes: [node("a", { cells: {} })], summary: undefined });
  expect(await screen.findByText(/등록된 스토리지가 없습니다/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "스토리지" })).toHaveAttribute("href", "/admin/storages");
  v2.unmount();
  renderPage({ detail: "forbidden" }, 403);
  expect(await screen.findByText("forbidden")).toBeInTheDocument();
});

test("범례는 여섯 상태를 다 말한다(경로 없음 = 제공 안 함 포함)", async () => {
  renderPage();
  const legend = await screen.findByRole("list", { name: "범례" });
  expect(within(legend).getAllByRole("listitem").map((li) => li.textContent))
    .toEqual(["마운트됨", "읽기 전용", "없음(+사유)", "경로 없음(제공 안 함)", "보고 없음", "비활성"]);
});

// ---- 순수 규칙 ----

test("에이전트 사유 짧은 글자·긴 설명: 다섯 코드 + 덮는 스토리지 + 모르는 코드는 원문", () => {
  expect(["missing_mount_path", "not_a_mountpoint", "propagation_stale", "not_readable", "host_root_missing"].map(reasonShort))
    .toEqual(["경로 없음", "마운트 아님", "전파 끊김", "읽기 불가", "호스트 루트 없음"]);
  expect(reasonShort("covering_storage_missing:cephfs")).toBe("cephfs 미마운트");
  expect(reasonLong("covering_storage_missing:cephfs")).toBe("아티팩트 경로를 덮는 스토리지 cephfs가 이 노드에 마운트돼 있지 않습니다");
  expect(reasonShort("weird_code")).toBe("weird_code");
  expect(reasonShort(null)).toBeNull();
});

test("nodeHasProblem·summaryOf: 비활성 열의 셀은 문제가 아니고, 끊긴 보고의 칸은 문제 칸으로 세지 않는다(행은 문제)", () => {
  const cols = storagesOf(OVERVIEW);
  const peers = peersOf(NODES, cols);
  expect(NODES.map((n) => nodeHasProblem(n, cols, peers))).toEqual([false, true, true, true, false]);
  const disabledOnly = node("x", { cells: { cephfs: ready, gpfs: ready, "old-nfs": { status: "missing", reason: "missing_mount_path" } } });
  expect(nodeHasProblem(disabledOnly, cols, peers)).toBe(false);
  // 서버 요약이 없으면 같은 규칙으로 센다: s-ssc(cephfs + base) 2칸 + w4 1칸, 끊긴 w3 의 gpfs 는 빼고
  expect(summaryOf({ ...OVERVIEW, summary: undefined as never }, NODES, cols, peers))
    .toEqual({ nodeCount: 5, freshCount: 3, staleCount: 1, storageCount: 3, problemCells: 3, problemNodes: 2 });
});

// ---- 2026-11-27 dms-k8s 판정 기준·표 압축 이식(d14ee402·7a172d1d·b8d4395f) ----

const roCell = { ...ready, status: "readonly", writable: false } as const;
const disabledCell = { status: "disabled", reason: null } as const;
const absentCell = { status: "absent", reason: "missing_mount_path", exists: false, is_mountpoint: null, readable: null,
                     writable: null, problem: false } as const;
const cellOfRow = (name: string, i: number) => cellsOf(name)[i];

test("경로 없음(absent -- 그 스토리지를 제공하지 않는 노드)은 흐린 글자·막대 점·바탕 없음이고 문제로 세지 않는다", async () => {
  const nodes = [
    node("src1", { cells: { cephfs: { ...ready, problem: false }, gpfs: absentCell, "old-nfs": disabledCell } }),
    node("dst1", { cells: { cephfs: { ...ready, problem: false }, gpfs: { ...ready, problem: false }, "old-nfs": disabledCell } }),
  ];
  renderPage({ ...OVERVIEW, nodes, summary: { node_count: 2, fresh_node_count: 2, storage_count: 3, problem_cells: 0,
                                              problem_nodes: 0, stale_node_count: 0 } });
  // 노드마다 붙인 스토리지가 다르면 「등록된 스토리지를 모두」가 아니다 -- 「제공하는」 스토리지 기준이라고 말한다
  expect(await screen.findByText("모든 노드가 제공하는 스토리지를 정상 마운트하고 있습니다")).toBeInTheDocument();
  const gpfs = cellOfRow("src1", 2);
  expect(gpfs).toHaveTextContent("경로 없음");
  expect(gpfs).not.toHaveTextContent(/없음경로 없음/);                // 사유 줄을 겹쳐 쓰지 않는다
  expect(gpfs.className).not.toMatch(/bg-(bad|attn)bg/);
  expect(within(gpfs).getByText("경로 없음").className).toContain("text-muted");
  const dot = gpfs.querySelector("[aria-hidden].rounded-full")!;
  expect(dot.className).toContain("h-0.5");                           // 색을 못 가려도 모양(가로 막대)으로 구별
  expect(within(gpfs).getByTitle(/^src1 · gpfs: 이 노드엔 그 스토리지 경로가 없습니다\(제공 안 함\) — /)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /문제만 보기/ })).toHaveTextContent("문제만 보기0");
  expect(screen.getByRole("button", { name: /문제 있는 스토리지만/ })).toHaveTextContent("문제 있는 스토리지만0");
});

test("읽기 전용: 같은 열에 쓰기 가능한 신선한 노드가 없으면(모든 노드 ro -- 의도된 구성) 문제가 아니다(옛 서버도 로컬 규칙으로)", async () => {
  const nodes = [
    node("r1", { cells: { cephfs: ready, gpfs: roCell, "old-nfs": disabledCell } }),
    node("r2", { cells: { cephfs: ready, gpfs: roCell, "old-nfs": disabledCell } }),
    // 끊긴 보고의 「쓰기 가능」은 옛 사실이라 이웃으로 치지 않는다
    node("r3", { fresh: false, reported_at: ago(900), cells: { cephfs: ready, gpfs: ready, "old-nfs": disabledCell } }),
  ];
  renderPage({ ...OVERVIEW, nodes, summary: undefined });
  expect(await screen.findByText("보고 중인 노드는 모두 등록된 스토리지를 정상 마운트하고 있습니다")).toBeInTheDocument();
  const gpfs = cellOfRow("r1", 2);
  expect(gpfs).toHaveTextContent("읽기 전용");
  expect(gpfs.className).not.toContain("bg-attnbg");
  expect(within(gpfs).getByText("읽기 전용").className).toContain("text-ink");
  expect(within(gpfs).getByTitle(/r1 · gpfs: 마운트됐지만 쓰기 불가\(읽기 전용\) — 쓰기 가능으로 붙인 노드가 없어 의도된 읽기 전용 구성으로 보고 문제로 세지 않습니다$/))
    .toBeInTheDocument();
  expect(screen.getByRole("button", { name: /문제만 보기/ })).toHaveTextContent("문제만 보기1");   // r3(보고 끊김)만
});

test("서버 problem 이 있으면 그것이 진실: 읽기 전용 problem=true 는 이웃이 없어도 문제, 없음 problem=false 는 문제 아님", async () => {
  const nodes = [
    node("s1", { cells: { cephfs: { ...roCell, problem: true }, gpfs: { status: "missing", reason: "not_a_mountpoint", problem: false },
                          "old-nfs": disabledCell },
                 artifact_base: { ...baseOk, status: "readonly", agent: { exists: true, writable: false, path_matches: true },
                                  problem: true } as MountNode["artifact_base"] }),
    node("s2", { cells: { cephfs: { ...roCell, problem: false }, gpfs: { ...ready, problem: false }, "old-nfs": disabledCell },
                 artifact_base: { ...baseOk, status: "readonly", agent: { exists: true, writable: false, path_matches: true },
                                  problem: false } as MountNode["artifact_base"] }),
  ];
  renderPage({ ...OVERVIEW, nodes, summary: { node_count: 2, fresh_node_count: 2, storage_count: 3, problem_cells: 2,
                                              problem_nodes: 1, stale_node_count: 0 } });
  await screen.findByRole("table");
  expect(screen.getByText("문제 2칸 · 노드 1대")).toBeInTheDocument();
  const [, s1ceph, s1gpfs, , s1base] = cellsOf("s1");
  expect(s1ceph.className).toContain("bg-attnbg/60");
  expect(s1gpfs.className).not.toContain("bg-badbg");
  expect(s1base.className).toContain("bg-attnbg/60");
  // 쓸 수 있는 이웃이 없으니 「다른 노드는 쓸 수 있어」라고 지어내지 않는다(원인을 모르면 판정만 말한다)
  expect(within(s1base).getByTitle(/^s1 · 아티팩트 경로: 경로는 있지만 쓰기 불가입니다\(문제로 셉니다\)$/)).toBeInTheDocument();
  const [, s2ceph, , , s2base] = cellsOf("s2");
  expect(s2ceph.className).not.toContain("bg-attnbg");
  expect(s2base.className).not.toContain("bg-attnbg");
  expect(within(s2base).getByText("쓰기 불가").className).toContain("text-ink");
  expect(within(s2base).getByTitle(/\(쓸 수 있는 노드가 없어 문제로 세지 않습니다\)$/)).toBeInTheDocument();
  // 문제만 보기 = 서버 판정의 문제 행(s1)만, 문제 있는 스토리지 = cephfs 하나(gpfs 의 없음은 서버가 문제 아님이라 했다)
  expect(screen.getByRole("button", { name: /문제만 보기/ })).toHaveTextContent("문제만 보기1");
  expect(screen.getByRole("button", { name: /문제 있는 스토리지만/ })).toHaveTextContent("문제 있는 스토리지만1");
});

test("아티팩트 경로 「로컬 디렉터리」(덮는 마운트가 노드 루트 '/') + 덮는 마운트 설명, 경로가 다르면 덮는 마운트를 말하지 않는다", async () => {
  const local = node("lx1", { roles: ["api"], artifact_base: { status: "missing", reason: "artifact_base_local_dir",
    agent: { exists: true, writable: true, path_matches: true, mount_point: "/", fstype: "ext4" }, api: [], controller: null,
    problem: true } });
  const okCeph = node("ok1", { artifact_base: { ...baseOk,
    agent: { exists: true, writable: true, path_matches: true, mount_point: "/mgmt_storage", fstype: "ceph" },
    problem: false } as MountNode["artifact_base"] });
  const moved = node("mv1", { artifact_base: { status: "unreported", reason: null,
    agent: { exists: true, writable: true, path_matches: false, mount_point: "/old", fstype: "nfs" }, api: [], controller: null } });
  renderPage({ ...OVERVIEW, nodes: [local, okCeph, moved], summary: undefined });
  await screen.findByRole("table");
  const baseOfRow = (name: string) => { const c = cellsOf(name); return c[c.length - 1]; };
  const lx = baseOfRow("lx1");
  expect(lx).toHaveTextContent("없음로컬 디렉터리");
  expect(lx.className).toContain("bg-badbg/60");
  expect(within(lx).getByTitle("lx1 · 아티팩트 경로: 공유 스토리지가 마운트되지 않아 아티팩트 경로가 노드 루트 파일시스템의 "
    + "로컬 디렉터리입니다 · 덮는 마운트 / (ext4)")).toBeInTheDocument();
  expect(within(baseOfRow("ok1")).getByTitle(
    "ok1 · 아티팩트 경로: 에이전트가 경로를 확인했습니다(존재·쓰기) · 덮는 마운트 /mgmt_storage (ceph)")).toBeInTheDocument();
  expect(within(baseOfRow("mv1")).getByTitle(/^mv1 · 아티팩트 경로: /).getAttribute("title")).not.toContain("덮는 마운트");
  // 순수 규칙
  expect(reasonShort("artifact_base_local_dir")).toBe("로컬 디렉터리");
  expect(reasonLong("artifact_base_local_dir")).toBe("공유 스토리지가 마운트되지 않아 아티팩트 경로가 노드 루트 파일시스템의 로컬 디렉터리입니다");
  expect(coverMountText(baseOf(okCeph)!)).toBe("/mgmt_storage (ceph)");
  expect(coverMountText(baseOf(node("o", { artifact_base: { ...baseOk,
    agent: { exists: true, writable: true, path_matches: true, mount_point: "/x", fstype: null } } as MountNode["artifact_base"] }))!))
    .toBe("/x");
  expect(coverMountText(baseOf(NODES[0])!)).toBeNull();               // 옛 에이전트(키 없음)는 모름
});

test("표 압축: 스토리지 열 6.5rem 고정 · 표 w-max · 래퍼 w-fit/남은 화면 높이 · 긴 이름 두 줄 · 긴 경로는 끝 조각(낭독은 전체)", async () => {
  const storages = [
    { storage_name: "weka-research-archive-01", mount_path: "/mgmt_storage/projects/alpha-2024", enabled: true },
    { storage_name: "cephfs", mount_path: "/mgmt_storage", enabled: true },
  ];
  renderPage({ ...OVERVIEW, storages, nodes: [node("n1", { cells: {} })], summary: undefined });
  const table = await screen.findByRole("table");
  expect(table.className).toContain("w-max");
  const wrap = screen.getByRole("region", { name: /노드별 마운트 표/ });
  expect(wrap).toBe(table.parentElement);
  expect(wrap).toHaveAttribute("tabindex", "0");
  expect(wrap.className).toMatch(/\bw-fit\b.*\bmax-w-full\b/);
  expect(wrap.style.maxHeight).toMatch(/^\d+px$/);
  const heads = within(table).getAllByRole("columnheader");
  const weka = heads[1];
  expect(weka.className).toContain("w-[6.5rem]");
  expect(within(weka).getByText("weka-research-archive-01")).toHaveClass("sr-only");
  expect([...weka.querySelectorAll("[aria-hidden]")].map((e) => e.textContent).filter((t) => t !== ""))
    .toEqual(["weka-research", "-archive-01", "…/alpha-2024"]);
  expect(within(weka).getByText("/mgmt_storage/projects/alpha-2024")).toHaveClass("sr-only");
  // 머리 줄 맞춤: 스토리지·아티팩트 경로 머리 모두 [h-8 이름(아래 붙임) · h-4 경로 · h-4 꼬리표] 고정 높이 블록
  for (const h of heads.slice(1)) {
    const blocks = [...h.firstElementChild!.children].map((e) => e.className);
    expect(blocks).toHaveLength(3);
    expect(blocks[0]).toMatch(/\bh-8\b.*\bjustify-end\b/);
    expect(blocks[1]).toMatch(/\bh-4\b/);
    expect(blocks[2]).toMatch(/\bh-4\b/);
  }
  // 칸 내용도 같은 고정 폭 상자(w-24) -- 칸이 열을 넓히지 않는다
  expect(cellOfRow("n1", 1).querySelector("[title]")!.className).toContain("w-24");
});

test("Home/End: 래퍼에 초점이 있을 때 가로 처음·끝 -- 안쪽 노드 링크의 키·조합 키는 건드리지 않는다", async () => {
  renderPage();
  const wrap = await screen.findByRole("region", { name: /Home·End/ });
  let left = 120;
  Object.defineProperty(wrap, "scrollWidth", { configurable: true, value: 4000 });
  Object.defineProperty(wrap, "scrollLeft", { configurable: true, get: () => left, set: (v: number) => { left = v; } });
  expect(fireEvent.keyDown(wrap, { key: "End" })).toBe(false);       // preventDefault -- 페이지를 세로로 움직이지 않는다
  expect(left).toBe(4000);
  expect(fireEvent.keyDown(wrap, { key: "Home" })).toBe(false);
  expect(left).toBe(0);
  left = 50;
  fireEvent.keyDown(screen.getByRole("link", { name: "w4" }), { key: "End" });
  fireEvent.keyDown(wrap, { key: "End", ctrlKey: true });
  expect(left).toBe(50);
});

test("열 거르기 ?q=: 이름·경로 부분 일치(대소문자 무시)로 칸은 바로, 주소는 잠시 뒤 -- 거른 보기 안내와 n/전체", async () => {
  renderPage(OVERVIEW, 200, "/admin/mounts?q=GP");
  await screen.findByRole("table");
  const box = screen.getByRole("searchbox", { name: "스토리지 찾기" });
  expect(box).toHaveValue("GP");
  expect(headTexts()).toEqual(["노드", "gpfs/gpfs/fs1", "아티팩트 경로/mgmt_storage/dms/artifacts덮는 스토리지 cephfs"]);
  expect(screen.getByText("스토리지 1/3")).toBeInTheDocument();
  expect(screen.getByText(/^스토리지를 거른 보기입니다/)).toBeInTheDocument();
  await userEvent.clear(box);
  await userEvent.type(box, "nfs");                                   // 이름 old-nfs · 경로 /nfs/old
  expect(headTexts()).toEqual(["노드", "old-nfs/nfs/old비활성", "아티팩트 경로/mgmt_storage/dms/artifacts덮는 스토리지 cephfs"]);
  await waitFor(() => expect(loc()).toBe("?q=nfs"));
  // 요약 수는 숨긴 스토리지까지 센 전체 기준 그대로
  expect(screen.getByText("문제 3칸 · 노드 2대")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /문제만 보기/ })).toHaveTextContent("문제만 보기3");
});

test("열 거르기 ?cols=problems: 신선한 노드에 문제 칸이 있는 스토리지만 -- 누르면 주소에서 빠지고 전체 열", async () => {
  renderPage({ ...OVERVIEW, nodes: [NODES[0], NODES[1]], summary: undefined }, 200, "/admin/mounts?cols=problems");
  await screen.findByRole("table");
  const toggle = screen.getByRole("button", { name: /문제 있는 스토리지만/ });
  expect(toggle).toHaveAttribute("aria-pressed", "true");
  expect(toggle).toHaveTextContent("문제 있는 스토리지만1");
  expect(headTexts().map((t) => t!.split("/")[0])).toEqual(["노드", "cephfs", "아티팩트 경로"]);
  await userEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-pressed", "false");
  expect(loc()).toBe("");
  expect(headTexts()).toHaveLength(5);
});

test("맞는 열이 없으면 노드·아티팩트 경로 열로 표를 그리고 「조건 지우기」가 q·cols 를 함께 지운다(행 거르기는 남긴다)", async () => {
  renderPage(OVERVIEW, 200, "/admin/mounts?q=zzz&cols=problems&only=problems");
  await screen.findByRole("table");
  expect(screen.getByText("조건에 맞는 스토리지가 없습니다 — 「zzz」 · 문제 있는 스토리지만")).toBeInTheDocument();
  expect(headTexts()).toEqual(["노드", "아티팩트 경로/mgmt_storage/dms/artifacts덮는 스토리지 cephfs"]);
  await userEvent.click(screen.getByRole("button", { name: "조건 지우기" }));
  expect(loc()).toBe("?only=problems");
  expect(screen.getByRole("searchbox", { name: "스토리지 찾기" })).toHaveValue("");
  expect(headTexts()).toHaveLength(5);
  // 지운 뒤 주소를 다시 쓰지 않는다(디바운스가 옛 값을 되살리지 않는다)
  await new Promise((r) => setTimeout(r, 400));
  expect(loc()).toBe("?only=problems");
});

test("순수 규칙: cellProblem·baseProblem(서버 problem 우선, 없으면 로컬) · filterColumns · 이름 줄 · 경로 끝 조각", () => {
  expect(cellProblem({ status: "missing", reason: "not_a_mountpoint" }, false, false)).toBe(true);
  expect(cellProblem({ status: "missing", reason: null, problem: false }, false, false)).toBe(false);
  expect(cellProblem({ status: "absent", reason: "missing_mount_path" }, true, true)).toBe(false);
  expect(cellProblem({ status: "absent", reason: "missing_mount_path" }, false, true)).toBe(false);   // ro 로라도 붙인 노드
  expect(cellProblem({ status: "absent", reason: "missing_mount_path" }, false, false)).toBe(true);   // 어디에도 없다
  expect(cellProblem({ status: "absent", reason: "missing_mount_path", problem: false }, false, false)).toBe(false);
  expect(cellProblem({ status: "readonly", reason: null }, false, true)).toBe(false);
  expect(cellProblem({ status: "readonly", reason: null }, true, true)).toBe(true);
  expect(cellProblem({ status: "readonly", reason: null, problem: true }, false, true)).toBe(true);
  expect(cellProblem({ status: "unreported", reason: null }, true, false)).toBe(false);
  const ro = { ...baseOk, status: "readonly" } as unknown as MountBaseCell;
  expect(baseProblem(ro, false)).toBe(false);
  expect(baseProblem(ro, true)).toBe(true);
  const failing = { ...baseOk, api: [{ instance: "a", ok: false, reason: null, checked_at: null, gate_state: null }] } as
    unknown as MountBaseCell;
  expect(baseProblem(failing, false)).toBe(true);
  expect(baseProblem({ ...failing, problem: false }, false)).toBe(false);     // 서버가 진실
  expect(baseProblem(null, true)).toBe(false);
  // 열 거르기
  const cols = storagesOf(OVERVIEW);
  const nodes = nodesOf(OVERVIEW);
  const peers = peersOf(nodes, cols);
  expect(filterColumns(cols, nodes, peers, { q: " /GPFS ", problemsOnly: false }).map((c) => c.storage_name)).toEqual(["gpfs"]);
  expect(filterColumns(cols, nodes, peers, { q: "", problemsOnly: true }).map((c) => c.storage_name)).toEqual(["cephfs", "gpfs"]);
  expect(filterColumns(cols, nodes, peers, { q: "", problemsOnly: false })).toBe(cols);
  // 이름 두 줄(13자) · 경로 끝 조각(14자 / 아티팩트 경로 28자)
  expect(columnNameLines("cephfs")).toEqual(["cephfs"]);
  expect(columnNameLines("cephfs-secondary")).toEqual(["cephfs-", "secondary"]);
  expect(columnNameLines("weka-research-archive-01")).toEqual(["weka-research", "-archive-01"]);
  expect(columnNameLines("abcdefghijklmnopqrstuvwxyz0123")).toEqual(["abcdefghijklmnopqrstuvwxyz0123", "…stuvwxyz0123"]);
  expect(pathTail("/mgmt_storage")).toBeNull();
  expect(pathTail("/mgmt_storage/projects/alpha-2024")).toBe("/alpha-2024");
  expect(pathTail("/mgmt_storage/a")).toBe("gmt_storage/a");             // 「/」 뒤가 8자 미만이면 자른 끝 그대로
  expect(pathTail("/mgmt_storage/dms/artifacts", 28)).toBeNull();
});

// ---- 2026-11-27 리뷰 후속 ----

test("경로 없음이 신선한 모든 노드에서면(그 스토리지를 마운트한 노드가 하나도 없음 -- mount_path 오타 등) 문제다(옛 서버 로컬 규칙)", async () => {
  const absentLocal = { status: "absent", reason: "missing_mount_path", exists: false, is_mountpoint: false, readable: false,
                        writable: false } as const;
  const nodes = [
    node("a1", { cells: { cephfs: absentLocal, gpfs: absentLocal, "old-nfs": disabledCell } }),
    node("a2", { cells: { cephfs: roCell, gpfs: absentLocal, "old-nfs": disabledCell } }),
    // 끊긴 노드의 마운트는 「어딘가엔 마운트」로 치지 않는다
    node("a3", { fresh: false, reported_at: ago(900), cells: { cephfs: ready, gpfs: ready, "old-nfs": disabledCell } }),
  ];
  renderPage({ ...OVERVIEW, nodes, summary: undefined });
  await screen.findByRole("table");
  // gpfs: a1·a2 둘 다 경로 없음이고 신선한 마운트가 없다 -- 2칸·2대. cephfs: a2 가 ro 로 붙였다 -- a1 의 경로 없음은 문제 아님
  expect(screen.getByText("문제 2칸 · 노드 2대")).toBeInTheDocument();
  const gpfs = cellOfRow("a1", 2);
  expect(gpfs.className).toContain("bg-badbg/60");
  expect(within(gpfs).getByText("경로 없음").className).toContain("text-bad");
  expect(within(gpfs).getByTitle(/^a1 · gpfs: 이 노드엔 그 스토리지 경로가 없습니다 — 이 스토리지를 마운트한 노드가 하나도 없어 문제로 셉니다/))
    .toBeInTheDocument();
  const ceph = cellOfRow("a1", 1);
  expect(ceph.className).not.toMatch(/bg-(bad|attn)bg/);
  expect(within(ceph).getByTitle(/\(제공 안 함\)/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /문제만 보기/ })).toHaveTextContent("문제만 보기3");     // a1·a2 + 끊긴 a3
  expect(screen.getByRole("button", { name: /문제 있는 스토리지만/ })).toHaveTextContent("문제 있는 스토리지만1");
});

test("옛 서버(칸 problem 없음)의 요약 수는 쓰지 않고 표와 같은 로컬 규칙으로 다시 센다 -- 플래그가 있으면 서버 수 그대로", async () => {
  // 옛 서버는 읽기 전용을 전부 문제로 센 요약(2칸·2대)을 보낸다 -- 모든 노드가 ro 인 열은 표에선 문제가 아니다
  const nodes = [
    node("r1", { cells: { cephfs: ready, gpfs: roCell, "old-nfs": disabledCell } }),
    node("r2", { cells: { cephfs: ready, gpfs: roCell, "old-nfs": disabledCell } }),
  ];
  const oldSummary = { node_count: 2, fresh_node_count: 2, storage_count: 3, problem_cells: 2, problem_nodes: 2, stale_node_count: 0 };
  const view = renderPage({ ...OVERVIEW, nodes, summary: oldSummary });
  expect(await screen.findByText("모든 노드가 등록된 스토리지를 정상 마운트하고 있습니다")).toBeInTheDocument();
  expect(screen.queryByText(/문제 2칸/)).toBeNull();
  view.unmount();
  // 순수 규칙: 에이전트 없는 노드의 API 점검 실패도 서버처럼 한 칸으로 센다(신선하지 않아도), 끊긴 노드의 칸은 세지 않는다
  const cols = storagesOf(OVERVIEW);
  const noAgent = node("cp", { has_agent: false, fresh: false, reported_at: null, cells: {},
    artifact_base: { status: "unreported", reason: null, agent: null, controller: null,
                     api: [{ instance: "api-1", ok: false, reason: "artifact_base_jobs_missing", checked_at: ago(5), gate_state: null }] } });
  const stale = node("st", { fresh: false, reported_at: ago(900),
    cells: { cephfs: { status: "missing", reason: "not_a_mountpoint" }, gpfs: ready, "old-nfs": disabledCell } });
  const all = [...nodes, noAgent, stale];
  expect(summaryOf({ ...OVERVIEW, nodes: all, summary: oldSummary }, all, cols, peersOf(all, cols)))
    .toMatchObject({ problemCells: 1, problemNodes: 1 });
  // 플래그가 있으면(새 서버) 서버 요약이 단일 출처다
  const flagged = nodes.map((n) => ({ ...n, cells: { ...n.cells, gpfs: { ...roCell, problem: true } } }));
  expect(summaryOf({ ...OVERVIEW, nodes: flagged, summary: oldSummary }, flagged, cols, peersOf(flagged, cols)))
    .toMatchObject({ problemCells: 2, problemNodes: 2 });
});

test("쓰기 불가 아티팩트 경로의 설명은 원인별: 자체 점검 실패면 실패한 역할, 「다른 노드는 쓸 수 있어」는 그런 이웃이 있을 때만", () => {
  const ro = { ...baseOk, status: "readonly", agent: { exists: true, writable: false, path_matches: true } } as unknown as MountBaseCell;
  const failing = { ...ro, controller: { ok: false, reason: "artifact_base_not_writable", checked_at: ago(5) } } as MountBaseCell;
  const solo = node("solo", { roles: ["controller"], artifact_base: failing });
  // 단일 노드(쓸 수 있는 이웃 없음) + 컨트롤러 점검 실패 -- 문제의 원인은 컨트롤러 점검이다
  expect(baseProblem(failing, false)).toBe(true);
  expect(baseDetail(solo, failing, true, false))
    .toBe("solo · 아티팩트 경로: 경로는 있지만 쓰기 불가입니다(이 노드의 컨트롤러 파드가 아티팩트 경로 점검에 실패해 문제로 셉니다)");
  expect(baseDetail(solo, failing, true, true)).toBe("solo · 아티팩트 경로: 경로는 있지만 쓰기 불가입니다(이 노드의 컨트롤러 파드가 "
    + "아티팩트 경로 점검에 실패해 문제로 셉니다 — 다른 노드는 이 경로에 쓸 수 있습니다)");
  const plain = node("p", { artifact_base: ro });
  expect(baseDetail(plain, ro, true, true)).toBe("p · 아티팩트 경로: 경로는 있지만 쓰기 불가입니다(다른 노드는 이 경로에 쓸 수 있어 문제로 셉니다)");
  expect(baseDetail(plain, ro, false, false)).toBe("p · 아티팩트 경로: 경로는 있지만 쓰기 불가입니다(쓸 수 있는 노드가 없어 문제로 세지 않습니다)");
});
