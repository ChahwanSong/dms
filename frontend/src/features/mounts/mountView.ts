import { reasonText } from "../../lib/api";
import type { MountBaseCell, MountCell, MountNode, MountsOverview } from "../../lib/types";

// 마운트 현황의 순수 규칙(화면·테스트 공용). 응답은 DB(에이전트 보고)를 거친 값이라 모양을 다시 본다 -- 배열이어야 할 것이
// 아니면 빈 배열, 셀이 없으면 「보고 없음」으로 읽는다(옛 서버·부분 응답이어도 화면이 죽지 않게).

export type StorageCol = { storage_name: string; mount_path: string; enabled: boolean };

// DB 정수(1/0/NULL)로 올 수 있는 참/거짓 -- 셋으로만 읽는다(null = 모름).
export const tri = (v: unknown): boolean | null =>
  (v === true || v === 1 ? true : v === false || v === 0 ? false : null);
const arr = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
const count = (v: unknown): number | null => (typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : null);

export function storagesOf(d: MountsOverview | undefined): StorageCol[] {
  return arr<MountsOverview["storages"][number]>(d?.storages)
    .filter((s) => s !== null && typeof s === "object" && typeof s.storage_name === "string")
    .map((s) => ({ storage_name: s.storage_name, mount_path: typeof s.mount_path === "string" ? s.mount_path : "",
                   enabled: tri(s.enabled) !== false }));
}
export function nodesOf(d: MountsOverview | undefined): MountNode[] {
  return arr<MountNode>(d?.nodes).filter((n) => n !== null && typeof n === "object" && typeof n.node_name === "string");
}

// 에이전트 마운트 사유(agent/probes.probe_mounts) → 짧은 표 글자 / 긴 설명(title·낭독). 모르는 코드는 원문 그대로 -- 「알 수
// 없음」보다 진단에 쓸모 있다(reasonText 와 같은 태도). artifact_base_local_dir 는 아티팩트 경로 칸 전용(서버 routes_mounts --
// 에이전트가 본 덮는 마운트가 노드 루트 '/', 2026-11-27 dms-k8s d14ee402 의 「로컬 디렉터리」 판정 이식).
const SHORT: Record<string, string> = {
  missing_mount_path: "경로 없음",
  not_a_mountpoint: "마운트 아님",
  propagation_stale: "전파 끊김",
  not_readable: "읽기 불가",
  host_root_missing: "호스트 루트 없음",
  artifact_base_local_dir: "로컬 디렉터리",
};
const LONG: Record<string, string> = {
  missing_mount_path: "마운트 경로가 노드에 없습니다",
  not_a_mountpoint: "경로는 있지만 마운트포인트가 아닙니다 — 공유 스토리지가 마운트되지 않은 빈 디렉터리일 수 있습니다",
  propagation_stale: "호스트에는 마운트됐지만 에이전트에 전파되지 않았습니다(마운트 전파 끊김 — 에이전트 재시작 필요)",
  not_readable: "마운트는 됐지만 읽을 수 없습니다(권한 또는 끊긴 마운트)",
  host_root_missing: "에이전트의 호스트 루트 바인드가 없어 마운트를 확인할 수 없습니다",
  artifact_base_local_dir: "공유 스토리지가 마운트되지 않아 아티팩트 경로가 노드 루트 파일시스템의 로컬 디렉터리입니다",
};
const COVER = "covering_storage_missing:";

export function reasonShort(reason: string | null | undefined): string | null {
  if (!reason) return null;
  if (reason.startsWith(COVER)) return `${reason.slice(COVER.length) || "덮는 스토리지"} 미마운트`;
  return SHORT[reason] ?? reasonText(reason);
}
export function reasonLong(reason: string | null | undefined): string | null {
  if (!reason) return null;
  if (reason.startsWith(COVER)) {
    const name = reason.slice(COVER.length);
    return `아티팩트 경로를 덮는 스토리지${name ? ` ${name}` : ""}가 이 노드에 마운트돼 있지 않습니다`;
  }
  return LONG[reason] ?? reasonText(reason);
}

// 셀 상태의 표 글자. 문제 판정은 cellProblem(서버 problem 이 진실, 옛 서버면 같은 규칙의 로컬 판정).
export const CELL_LABEL: Record<MountCell["status"], string> = {
  ready: "마운트됨", readonly: "읽기 전용", missing: "없음", absent: "경로 없음", unreported: "보고 없음", disabled: "비활성",
};
export const BASE_LABEL: Record<MountBaseCell["status"], string> = {
  ready: "정상", readonly: "쓰기 불가", missing: "없음", unreported: "보고 없음",
};
const STATUSES = new Set(["ready", "readonly", "missing", "absent", "unreported", "disabled"]);
const BASE_STATUSES = new Set(["ready", "readonly", "missing", "unreported"]);

// 셀 하나(활성 여부는 머리 열이 진실 -- 서버가 disabled 를 안 실은 옛 응답이어도 비활성 열은 비활성으로 그린다).
export function cellOf(node: MountNode, col: StorageCol): MountCell {
  if (!col.enabled) return { status: "disabled", reason: null };
  const raw = (node.cells !== null && typeof node.cells === "object" ? node.cells : {})[col.storage_name];
  if (!raw || typeof raw !== "object" || !STATUSES.has(raw.status)) return { status: "unreported", reason: null };
  return raw;
}

export function baseOf(node: MountNode): MountBaseCell | null {
  const b = node.artifact_base;
  if (!b || typeof b !== "object") return null;
  return { ...b, status: BASE_STATUSES.has(b.status) ? b.status : "unreported",
           api: arr<MountBaseCell["api"][number]>(b.api), controller: b.controller ?? null };
}

// 문제 판정(2026-11-27 dms-k8s 판정 기준 이식): 서버가 칸마다 problem 을 실으면 그것이 진실이다(요약 수와 같은 출처). 옛 서버(키
// 없음)면 같은 규칙으로 로컬 판정한다(서버 routes_mounts 모듈 docstring 「문제 판정」) -- 없음 = 문제, 경로 없음(absent -- 그 스토리지를
// 제공하지 않는 노드) = 같은 열에 그 스토리지를 붙인 **흔적**(attachedCell)이 하나라도 있으면 문제 아님(하나도 없으면 그 스토리지는
// 어디에도 마운트돼 있지 않다 -- mount_path 오타·마운트 배포 전, 문제), 읽기 전용 = 같은 열에 쓰기 가능한(ready) 신선한 노드가 하나라도
// 있을 때만 문제(모든 노드가 ro 로 붙인 스토리지는 의도된 구성). 그 열 재료는 한 번에 모은다(peersOf). 칸 판정은 부르는 쪽이 신선한
// 행으로 막는다(끊긴 보고의 칸은 문제로 세지 않는다). ready·readonly·absent 의 구분(쓰기 가능 여부·호스트 마운트 유무) 자체는 서버가
// 가른다 -- 화면은 상태를 다시 분류하지 않는다.
//
// 흔적(2026-11-27 dms-k8s 210cd166 mount_status._attached 이식): 정상·읽기 전용 칸, 그리고 host_root_missing(그 노드의 마운트를 점검하지
// 못했다)이 아닌 없음 칸(끊긴 마운트·마운트 아님·전파 끊김·읽기 불가·모르는 사유 -- 호스트에 그 스토리지의 마운트나 디렉터리가 있다;
// 그 칸 자체가 이미 문제라 열은 문제 열로 남는다). 보고가 끊긴 노드의 칸도 마지막 보고로 같은 규칙을 본다. 흔적을 신선한 정상·읽기
// 전용으로만 세면 그 스토리지를 붙인 노드가 하나뿐일 때(소스·목적지 노드 분리) 그 노드 하나의 보고 끊김·끊긴 마운트가 다른 모든 노드의
// 「경로 없음」을 문제로 퍼뜨렸다(dms-k8s 실측: 노드 6대에서 문제 노드 1 → 6). 흔적이 아닌 것: 경로 없음·보고 없음·비활성·
// host_root_missing. 알려진 한계: 새로 등록한 스토리지를 붙인 노드가 아직 보고 전이면 다른 노드의 경로 없음이 한 보고 주기 동안 문제로
// 보인다. 아티팩트 경로의 쓰기 불가는 이웃과 상관없이 문제다(baseProblem) -- 그래서 아티팩트 경로 이웃 재료는 없다.
export interface MountPeers { writableCols: ReadonlySet<string>; mountedCols: ReadonlySet<string> }
const HOST_ROOT_MISSING = "host_root_missing";
export const attachedCell = (c: MountCell): boolean =>
  c.status === "ready" || c.status === "readonly" || (c.status === "missing" && c.reason !== HOST_ROOT_MISSING);
export function peersOf(nodes: MountNode[], cols: StorageCol[]): MountPeers {
  const writableCols = new Set<string>();
  const mountedCols = new Set<string>();
  for (const n of nodes) {
    // 에이전트가 없는 노드의 칸은 보고 없음뿐이다(흔적이 될 수 없다). 끊긴 보고의 칸은 마지막 보고 그대로 흔적을 본다.
    if (n.has_agent === false) continue;
    for (const c of cols) {
      const cell = cellOf(n, c);
      // 쓰기 가능한 이웃은 신선한 행만(끊긴 보고의 「쓰기 가능」은 지금도 그런지 모른다 -- 읽기 전용 바깥값 판정).
      if (n.fresh === true && cell.status === "ready") writableCols.add(c.storage_name);
      if (attachedCell(cell)) mountedCols.add(c.storage_name);
    }
  }
  return { writableCols, mountedCols };
}
export function cellProblem(c: MountCell, writablePeer: boolean, mountedPeer: boolean): boolean {
  if (typeof c.problem === "boolean") return c.problem;
  return c.status === "missing" || (c.status === "readonly" && writablePeer) || (c.status === "absent" && !mountedPeer);
}
export const cellProblemIn = (n: MountNode, col: StorageCol, peers: MountPeers) =>
  cellProblem(cellOf(n, col), peers.writableCols.has(col.storage_name), peers.mountedCols.has(col.storage_name));

// 이 노드에서 자체 점검이 실패한 역할(아래 줄 CheckLine 과 같은 이름 「API」·「컨트롤러」). 점검 전·모름은 실패가 아니다.
export function baseFailedRoles(b: MountBaseCell): string[] {
  return [...(b.api.some((a) => tri(a.ok) === false) ? ["API"] : []),
          ...(tri(b.controller?.ok) === false ? ["컨트롤러"] : [])];
}
// API·컨트롤러 자체 점검이 이 노드에서 실패했다.
export const baseChecksFailed = (b: MountBaseCell) => baseFailedRoles(b).length > 0;
// 에이전트는 정상(또는 보고 없음)인데 자체 점검이 실패하면 칸 머리를 「점검 실패」로 덮는다 -- 머리(BaseCell)와 설명(baseDetail)이
// 같은 판정을 쓰게 한 곳에 둔다.
export const baseHeadOverridden = (b: MountBaseCell) =>
  baseChecksFailed(b) && (b.status === "ready" || b.status === "unreported");
// 아티팩트 경로 칸 문제: 서버 problem 이 있으면 그것(신선 ∧ (없음 ∨ 쓰기 불가) ∨ 자체 점검 실패), 옛 서버면 같은 규칙(신선 조건은
// 부르는 쪽이 건다 -- 끊긴 행은 행 자체가 문제이고 요약은 신선한 행만 센다). 쓰기 불가는 이웃과 상관없이 문제다(2026-11-27 dms-k8s 와
// 맞춤 -- 잡이 작업 기록을 쓰는 곳이라 모든 노드에서 쓸 수 있어야 하고, 「쓸 수 있는 이웃이 있을 때만」 규칙은 모든 노드가 쓰기 불가인
// 장애를 숨겼다).
export const baseProblem = (b: MountBaseCell | null) =>
  b !== null && (typeof b.problem === "boolean" ? b.problem
    : b.status === "missing" || b.status === "readonly" || baseChecksFailed(b));

export const isStale = (n: MountNode) => n.has_agent === true && n.fresh !== true;
export const rolesOf = (n: MountNode) => arr<string>(n.roles);

// 「문제만 보기」의 행 판정: 보고 끊김, 문제 칸(보고가 살아 있을 때만 -- 끊긴 보고의 칸은 옛 사실이다), 아티팩트 경로 문제(자체
// 점검 실패 포함).
export function nodeHasProblem(n: MountNode, cols: StorageCol[], peers: MountPeers): boolean {
  if (isStale(n)) return true;
  if (baseProblem(baseOf(n))) return true;
  if (n.fresh !== true) return false;
  return cols.some((c) => cellProblemIn(n, c, peers));
}

// 문제 있는 스토리지 열(?cols=problems): 신선한 노드 중 하나라도 그 열의 칸이 문제다(요약·행과 같은 cellProblem).
export function columnHasProblem(col: StorageCol, nodes: MountNode[], peers: MountPeers): boolean {
  return nodes.some((n) => n.fresh === true && cellProblemIn(n, col, peers));
}
// 열 거르기(2026-11-27 dms-k8s 7a172d1d 이식 -- 스토리지가 100개여도 볼 열만): q = 이름·경로 부분 일치(대소문자 무시), problemsOnly =
// 문제 있는 열만. 아티팩트 경로 열은 거르지 않는다(부르는 쪽이 늘 그린다).
export function filterColumns(cols: StorageCol[], nodes: MountNode[], peers: MountPeers,
                              f: { q: string; problemsOnly: boolean }): StorageCol[] {
  const q = f.q.trim().toLowerCase();
  if (q === "" && !f.problemsOnly) return cols;
  return cols.filter((c) =>
    (q === "" || c.storage_name.toLowerCase().includes(q) || c.mount_path.toLowerCase().includes(q))
    && (!f.problemsOnly || columnHasProblem(c, nodes, peers)));
}

// 열 머리 고정 폭 상자(6.5rem 열 = 안쪽 w-24 96px)에 보이는 줄(dms-k8s 7a172d1d mountTerms.columnNameLines·columnPathTail 이식).
// 머리를 truncate 하면 **앞**이 보여 프로덕션처럼 경로 앞부분이 같으면(/mgmt_storage/…) 모든 열이 똑같이 보였다 -- 구별되는
// **끝**을 보인다. 이름은 [a-z0-9-](저장소 storages._NAME_RE), 12px 등폭 글자 한 줄 13자:
// - 13자 이하: 한 줄. 26자 이하: 두 조각이 모두 13자 이하가 되는 가장 뒤의 하이픈 뒤에서(없으면 13자에서) 직접 나눈다(브라우저
//   자연 줄바꿈은 「-01」 앞을 못 꺾어 세 줄이 되고 두 줄 칸에서 끝이 잘린다). 26자 초과: [전체(앞 조각 -- 화면이 말줄임), 「…」+끝 12자].
export const COLUMN_NAME_LINE = 13;
export function columnNameLines(name: string): string[] {
  const chars = [...name];
  const n = chars.length;
  const per = COLUMN_NAME_LINE;
  if (n <= per) return [name];
  if (n > per * 2) return [name, `…${chars.slice(-(per - 1)).join("")}`];
  let k = per;
  for (let i = per; i >= n - per; i--) {
    if (chars[i - 1] === "-") { k = i; break; }
  }
  return [chars.slice(0, k).join(""), chars.slice(k).join("")];
}
// 경로: max 자(11px 등폭 -- 스토리지 열 14자, 아티팩트 경로 열 28자) 이하면 null(전체를 그대로), 넘치면 끝 (max-1)자 -- 그 안에
// 「/」가 있고 거기서 끝까지 8자 이상이면 그 「/」부터(경로 조각 경계). 화면은 「…」 + 이 조각, 낭독·title 은 전체.
export const COLUMN_PATH_LINE = 14;
export const BASE_PATH_LINE = 28;
export function pathTail(path: string, max: number = COLUMN_PATH_LINE): string | null {
  const chars = [...path];
  if (chars.length <= max) return null;
  const tail = chars.slice(-(max - 1));
  const slash = tail.indexOf("/");
  return slash >= 0 && tail.length - slash >= 8 ? tail.slice(slash).join("") : tail.join("");
}

export interface MountsSummary {
  nodeCount: number; freshCount: number; staleCount: number; storageCount: number; problemCells: number; problemNodes: number;
}
// 서버가 문제 플래그를 실었는가(칸이든 아티팩트 경로든 boolean problem 이 하나라도) -- 없으면 옛 서버다.
function hasServerFlags(nodes: MountNode[]): boolean {
  return nodes.some((n) =>
    Object.values(n.cells !== null && typeof n.cells === "object" ? n.cells : {})
      .some((c) => c !== null && typeof c === "object" && typeof c.problem === "boolean")
    || typeof baseOf(n)?.problem === "boolean");
}
// 한 노드의 문제 칸 수 -- 서버 routes_mounts 와 같은 셈: 스토리지 칸은 신선한 행만, 아티팩트 경로 칸(한 칸)은 신선한 행의 문제이거나
// 그 노드의 API·컨트롤러 자체 점검 실패(에이전트 없는 노드·끊긴 노드도 센다).
function problemCellsOf(n: MountNode, cols: StorageCol[], peers: MountPeers): number {
  const b = baseOf(n);
  const base = b !== null && baseProblem(b) && (n.fresh === true || baseChecksFailed(b));
  return (n.fresh === true ? cols.filter((c) => cellProblemIn(n, c, peers)).length : 0) + (base ? 1 : 0);
}
// 요약 줄: 서버가 칸 problem 을 실었으면 서버 요약 수가 우선이다(그 플래그만 센다 -- 단일 출처, 모양이 틀린 값만 같은 규칙으로 센다).
// 플래그가 하나도 없으면(옛 서버 -- 예전 규칙으로 읽기 전용을 전부 센 요약) 표·「문제만 보기」·열 거르기와 같은 로컬 규칙으로 다시
// 센다(2026-11-27 리뷰 -- 롤링 릴리스 중 옛 API 파드가 답하면 요약 「문제 N칸」과 바탕 없는 칸이 두 말을 했다).
export function summaryOf(d: MountsOverview | undefined, nodes: MountNode[], cols: StorageCol[],
                          peers: MountPeers): MountsSummary {
  const s = (d?.summary ?? null) as Partial<MountsOverview["summary"]> | null;
  const fresh = nodes.filter((n) => n.fresh === true);
  const server = hasServerFlags(nodes);
  const per = nodes.map((n) => problemCellsOf(n, cols, peers));
  return {
    nodeCount: count(s?.node_count) ?? nodes.length,
    freshCount: count(s?.fresh_node_count) ?? fresh.length,
    staleCount: count(s?.stale_node_count) ?? nodes.filter(isStale).length,
    storageCount: count(s?.storage_count) ?? cols.length,
    problemCells: (server ? count(s?.problem_cells) : null) ?? per.reduce((a, k) => a + k, 0),
    problemNodes: (server ? count(s?.problem_nodes) : null) ?? per.filter((k) => k > 0).length,
  };
}

// 셀 설명(title·낭독). 상태 + 사유 + 에이전트 관찰값. problem = 그 칸의 문제 판정(cellProblem) -- 읽기 전용·경로 없음이 왜 문제인지·
// 아닌지를 말한다(끊긴 보고의 칸은 판정하지 않는다). 경로 없음의 문구는 규칙만큼만 말한다 -- 「흔적이 없다」이지 「마운트한 노드가
// 없다」가 아니다(붙인 노드가 아직 보고 전일 수 있다). 흔적은 이 표의 칸 글자(CELL_LABEL)와 attachedCell 의 예외(호스트 루트 없음)로
// 적는다 -- 표에 없는 낱말(「마운트 문제」 -- k8s 타일 용어)을 쓰면 운영자가 칸에 대응시키지 못한다.
export function cellDetail(col: StorageCol, c: MountCell, node: MountNode, problem: boolean): string {
  const head = `${node.node_name} · ${col.storage_name}`;
  switch (c.status) {
    case "ready":
      // 쓰기 가능 여부를 보고하지 않은 에이전트(옛 에이전트 -- 서버는 placement 처럼 쓸 수 있는 것으로 보고 ready 로 둔다).
      return c.writable === null
        ? `${head}: 마운트됨 — 에이전트가 쓰기 가능 여부를 보고하지 않아(옛 에이전트) 작업 배치처럼 쓸 수 있는 것으로 봅니다`
        : `${head}: 마운트됨(읽기·쓰기)`;
    case "readonly": return `${head}: 마운트됐지만 쓰기 불가(읽기 전용)${node.fresh !== true ? ""
      : problem ? " — 같은 스토리지를 쓰기 가능으로 붙인 노드가 있어 문제로 셉니다"
      : " — 쓰기 가능으로 붙인 노드가 없어 의도된 읽기 전용 구성으로 보고 문제로 세지 않습니다"}`;
    case "missing": return `${head}: ${reasonLong(c.reason) ?? "마운트 없음"}`;
    case "absent":
      if (node.fresh !== true) return `${head}: 이 노드엔 그 스토리지 경로가 없습니다(제공 안 함)`;
      return problem
        ? `${head}: 이 노드엔 그 스토리지 경로가 없습니다 — 어느 노드에도 이 스토리지의 마운트 흔적이 없어(마운트됨 · 읽기 전용 · `
          + "없음[호스트 루트 없음 제외] 칸인 노드가 없음 · 보고 끊긴 노드는 마지막 보고 기준) 문제로 셉니다. 마운트 경로 "
          + "오타이거나 아직 어느 노드에도 마운트하지 않은 스토리지일 수 있고, 방금 등록한 스토리지라면 붙인 노드가 아직 보고 전일 수 "
          + "있습니다"
        : `${head}: 이 노드엔 그 스토리지 경로가 없습니다(제공 안 함) — 다른 노드에 이 스토리지의 마운트 흔적이 있어, 이 스토리지를 `
          + "붙이지 않는 노드라면 정상이라 문제로 세지 않습니다(그 스토리지 작업은 마운트한 노드에만 배치됩니다)";
    case "disabled": return `${head}: 비활성 스토리지(점검 대상 아님)`;
    default:
      return node.has_agent === false
        ? `${head}: 이 노드엔 에이전트가 없어 마운트를 보고하지 않습니다`
        : `${head}: 보고에 이 스토리지가 없습니다(옛 에이전트이거나 마운트 경로가 바뀐 뒤 아직 보고 전)`;
  }
}

// 에이전트가 본 덮는 마운트(2026-11-27~ 새 에이전트, dms-k8s d14ee402 이식) -- 「/cephfs (ceph)」. 모르면(옛 에이전트·못 찾음)
// null. 에이전트가 본 경로가 지금 설정과 다르면 옛 경로의 마운트라 말하지 않는다.
export function coverMountText(b: MountBaseCell): string | null {
  const a = b.agent;
  if (a === null || a === undefined || a.path_matches === false) return null;
  const mp = typeof a.mount_point === "string" && a.mount_point !== "" ? a.mount_point : null;
  if (mp === null) return null;
  return typeof a.fstype === "string" && a.fstype !== "" ? `${mp} (${a.fstype})` : mp;
}

// 쓰기 불가 아티팩트 경로 칸이 왜 문제인지·아닌지(baseDetail). 쓰기 불가는 이웃과 상관없이 문제다(baseProblem) -- 「다른 노드는
// 쓸 수 있어」 같은 이웃 의존 꼬리는 달지 않는다. 자체 점검도 실패했으면 실패한 역할을 함께 말한다. problem 거짓은 그 판정을 내린
// 서버(이웃 규칙의 옛 서버)의 말을 그대로 옮길 뿐이다.
const BASE_WRITABLE_RULE = "아티팩트 경로는 잡이 작업 기록을 쓰는 곳이라 모든 노드에서 쓸 수 있어야 해 문제로 셉니다";
function readonlyWhy(b: MountBaseCell, problem: boolean): string {
  if (!problem) return "(문제로 세지 않습니다)";
  const roles = baseFailedRoles(b);
  return `(${BASE_WRITABLE_RULE}${roles.length > 0 ? ` — 이 노드의 ${roles.join("·")} 파드도 아티팩트 경로 점검에 실패했습니다` : ""})`;
}
// 아티팩트 경로 칸 설명(title·낭독). 머리가 「점검 실패」로 덮이면 실패한 역할부터 말한다 -- 「에이전트가 경로를 확인했습니다」만
// 남기면 빨간 머리와 설명이 두 말을 한다(dms-k8s e25a2dfb 의 공용 칸 역할 이름 이식). 에이전트 관찰(덮는 마운트 포함)은 뒤에
// 덧붙인다. problem = 그 칸의 문제 판정(baseProblem) -- 쓰기 불가가 왜 문제인지·아닌지를 원인별로 말한다(readonlyWhy).
export function baseDetail(n: MountNode, b: MountBaseCell, problem: boolean): string {
  const head = `${n.node_name} · 아티팩트 경로`;
  const mismatch = b.agent?.path_matches === false ? " (에이전트가 본 경로가 지금 설정과 다릅니다)" : "";
  const cover = coverMountText(b);
  const mount = cover !== null ? ` · 덮는 마운트 ${cover}` : "";
  // 에이전트가 쓰기 가능 여부를 보고하지 않았으면(모름) 「쓰기」를 확인했다고 말하지 않는다 -- 서버는 모름을 쓸 수 있는 것으로 보고
  // 정상(ready)으로 둔다(스토리지 칸·placement 와 같은 태도).
  const checked = b.agent?.writable === null ? "존재 · 쓰기 가능 여부는 보고되지 않음" : "존재·쓰기";
  if (baseHeadOverridden(b)) {
    const agent = b.status === "ready" ? `에이전트는 경로를 확인했습니다(${checked})${mount}`
      : n.has_agent === false ? "에이전트가 없어 노드 쪽 경로는 확인되지 않았습니다" : "에이전트 보고에는 경로 확인이 없습니다";
    return `${head}: 이 노드의 ${baseFailedRoles(b).join("·")} 파드가 아티팩트 경로 점검에 실패했습니다 — ${agent}${mismatch}. `
      + "파드의 hostPath·마운트 전파와 노드의 공유 스토리지 마운트를 확인하세요(아티팩트 경로 화면에 파드별 사유)";
  }
  switch (b.status) {
    case "ready": return `${head}: 에이전트가 경로를 확인했습니다(${checked})${mount}${mismatch}`;
    case "readonly": return `${head}: 경로는 있지만 쓰기 불가입니다${n.fresh !== true ? "" : readonlyWhy(b, problem)}`
      + `${mount}${mismatch}`;
    case "missing": return `${head}: ${reasonLong(b.reason) ?? "경로가 없습니다"}${mount}${mismatch}`;
    default: return n.has_agent === false
      ? `${head}: 에이전트가 없어 경로를 보고하지 않습니다` : `${head}: 에이전트 보고에 경로 확인이 없습니다${mismatch}`;
  }
}
