import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";
import { MountsPage } from "./MountsPage";
import { nodeHasProblem, reasonLong, reasonShort, storagesOf, summaryOf } from "./mountView";
import type { MountNode, MountsOverview } from "../../lib/types";

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

function renderPage(body: unknown = OVERVIEW, status = 200) {
  server.use(http.get("/api/admin/mounts", () => HttpResponse.json(body as object, { status })));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter><MountsPage /></MemoryRouter></QueryClientProvider>);
}
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

test("표: 머리 = 노드 · 스토리지(이름 + mount_path, 비활성 표시) · 아티팩트 경로(경로 + 덮는 스토리지)", async () => {
  renderPage();
  const table = await screen.findByRole("table");
  const heads = within(table).getAllByRole("columnheader");
  expect(heads.map((h) => h.textContent)).toEqual([
    "노드", "cephfs(아티팩트 경로를 덮는 스토리지)/mgmt_storage", "gpfs/gpfs/fs1", "old-nfs· 비활성/nfs/old",
    "아티팩트 경로/mgmt_storage/dms/artifacts덮는 스토리지 cephfs",
  ]);
  expect(within(heads[3]).getByText("old-nfs").className).toContain("text-muted");
  expect(within(heads[1]).getByText("/mgmt_storage").className).toContain("font-mono");
  // 첫 열 고정(sticky) + 가로 넘침은 래퍼 안에서만
  expect(heads[0].className).toContain("sticky");
  expect(cellsOf("ion2110")[0].className).toContain("sticky");
  expect(table.parentElement!.className).toContain("overflow-x-auto");
});

test("칸: 상태 점 + 짧은 글자, 없음은 짧은 사유를 붙이고 긴 설명은 title·낭독 문장에 -- 문제 칸은 옅은 바탕", async () => {
  renderPage();
  await screen.findByRole("table");
  const [, ceph, gpfs, nfs, base] = cellsOf("s-ssc-dms-26001");
  expect(ceph).toHaveTextContent("없음· 마운트 아님");
  const mark = within(ceph).getByTitle(/s-ssc-dms-26001 · cephfs: 경로는 있지만 마운트포인트가 아닙니다/);
  expect(within(mark).getByText(/^\(s-ssc-dms-26001 · cephfs: 경로는 있지만 마운트포인트가 아닙니다/).className).toContain("sr-only");
  expect(ceph.className).toContain("bg-badbg/60");
  expect(gpfs).toHaveTextContent("마운트됨");
  expect(gpfs.className).not.toContain("bg-badbg");
  expect(nfs).toHaveTextContent("비활성");
  // 아티팩트 경로: 덮는 스토리지 미마운트 + 그 노드의 API 자체 점검 실패
  expect(base).toHaveTextContent("없음· cephfs 미마운트");
  expect(within(base).getByText("API 실패")).toHaveAttribute(
    "title", expect.stringMatching(/^API dms-api-7c9d-xyz12: 최근 작업의 기록 디렉터리가 이 경로에 없습니다/));
  const [, , w4gpfs] = cellsOf("w4");
  expect(w4gpfs).toHaveTextContent("읽기 전용");
  expect(w4gpfs.className).toContain("bg-attnbg/60");
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
  expect(gpfs).toHaveTextContent("없음· 경로 없음");
  expect(gpfs.className).not.toContain("bg-badbg");
  expect(gpfs.querySelector("[aria-hidden].rounded-full")!.className).toContain("bg-bad/40");
  // 살아 있는 행의 점은 원색
  expect(cellsOf("s-ssc-dms-26001")[1].querySelector("[aria-hidden].rounded-full")!.className).toMatch(/\bbg-bad\b/);
});

test("「문제만 보기」: 문제 행(없음·읽기 전용·아티팩트 경로·보고 끊김)만 -- 다시 누르면 전체", async () => {
  renderPage();
  await screen.findByRole("table");
  const toggle = screen.getByRole("button", { name: /문제만 보기/ });
  expect(toggle).toHaveAttribute("aria-pressed", "false");
  expect(toggle).toHaveTextContent("문제만 보기3");
  await userEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-pressed", "true");
  const names = within(screen.getByRole("table")).getAllByRole("row").slice(1)
    .map((r) => within(r).getAllByRole("link")[0].textContent);
  expect(names).toEqual(["s-ssc-dms-26001", "w3", "w4"]);
  await userEvent.click(toggle);
  expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(6);
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
  expect(cellsOf("b")[1]).toHaveTextContent("없음· 전파 끊김");
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

test("범례는 다섯 상태를 다 말한다", async () => {
  renderPage();
  const legend = await screen.findByRole("list", { name: "범례" });
  expect(within(legend).getAllByRole("listitem").map((li) => li.textContent))
    .toEqual(["마운트됨", "읽기 전용", "없음(+사유)", "보고 없음", "비활성"]);
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
  expect(NODES.map((n) => nodeHasProblem(n, cols))).toEqual([false, true, true, true, false]);
  const disabledOnly = node("x", { cells: { cephfs: ready, gpfs: ready, "old-nfs": { status: "missing", reason: "missing_mount_path" } } });
  expect(nodeHasProblem(disabledOnly, cols)).toBe(false);
  // 서버 요약이 없으면 같은 규칙으로 센다: s-ssc(cephfs + base) 2칸 + w4 1칸, 끊긴 w3 의 gpfs 는 빼고
  expect(summaryOf({ ...OVERVIEW, summary: undefined as never }, NODES, cols))
    .toEqual({ nodeCount: 5, freshCount: 3, staleCount: 1, storageCount: 3, problemCells: 3, problemNodes: 2 });
});
