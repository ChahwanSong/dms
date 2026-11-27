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
// 없음」보다 진단에 쓸모 있다(reasonText 와 같은 태도).
const SHORT: Record<string, string> = {
  missing_mount_path: "경로 없음",
  not_a_mountpoint: "마운트 아님",
  propagation_stale: "전파 끊김",
  not_readable: "읽기 불가",
  host_root_missing: "호스트 루트 없음",
};
const LONG: Record<string, string> = {
  missing_mount_path: "마운트 경로가 노드에 없습니다",
  not_a_mountpoint: "경로는 있지만 마운트포인트가 아닙니다 — 공유 스토리지가 마운트되지 않은 빈 디렉터리일 수 있습니다",
  propagation_stale: "호스트에는 마운트됐지만 에이전트에 전파되지 않았습니다(마운트 전파 끊김 — 에이전트 재시작 필요)",
  not_readable: "마운트는 됐지만 읽을 수 없습니다(권한 또는 끊긴 마운트)",
  host_root_missing: "에이전트의 호스트 루트 바인드가 없어 마운트를 확인할 수 없습니다",
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

// 셀 상태의 표 글자. 문제 = 활성 스토리지의 없음·읽기 전용(서버 summary.problem_cells 와 같은 정의).
export const CELL_LABEL: Record<MountCell["status"], string> = {
  ready: "마운트됨", readonly: "읽기 전용", missing: "없음", unreported: "보고 없음", disabled: "비활성",
};
export const BASE_LABEL: Record<MountBaseCell["status"], string> = {
  ready: "정상", readonly: "쓰기 불가", missing: "없음", unreported: "보고 없음",
};
const STATUSES = new Set(["ready", "readonly", "missing", "unreported", "disabled"]);

// 셀 하나(활성 여부는 머리 열이 진실 -- 서버가 disabled 를 안 실은 옛 응답이어도 비활성 열은 비활성으로 그린다).
export function cellOf(node: MountNode, col: StorageCol): MountCell {
  if (!col.enabled) return { status: "disabled", reason: null };
  const raw = (node.cells !== null && typeof node.cells === "object" ? node.cells : {})[col.storage_name];
  if (!raw || typeof raw !== "object" || !STATUSES.has(raw.status)) return { status: "unreported", reason: null };
  return raw;
}
export const cellProblem = (c: MountCell) => c.status === "missing" || c.status === "readonly";

export function baseOf(node: MountNode): MountBaseCell | null {
  const b = node.artifact_base;
  if (!b || typeof b !== "object") return null;
  return { ...b, status: STATUSES.has(b.status) && b.status !== ("disabled" as string) ? b.status : "unreported",
           api: arr<MountBaseCell["api"][number]>(b.api), controller: b.controller ?? null };
}
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
export const baseProblem = (b: MountBaseCell | null) =>
  b !== null && (b.status === "missing" || b.status === "readonly" || baseChecksFailed(b));

export const isStale = (n: MountNode) => n.has_agent === true && n.fresh !== true;
export const rolesOf = (n: MountNode) => arr<string>(n.roles);

// 「문제만 보기」의 행 판정: 보고 끊김, 활성 스토리지의 없음·읽기 전용(보고가 살아 있을 때만 -- 끊긴 보고의 칸은 옛 사실이다),
// 아티팩트 경로 문제(자체 점검 실패 포함).
export function nodeHasProblem(n: MountNode, cols: StorageCol[]): boolean {
  if (isStale(n)) return true;
  if (baseProblem(baseOf(n))) return true;
  if (n.fresh !== true) return false;
  return cols.some((c) => cellProblem(cellOf(n, c)));
}

export interface MountsSummary {
  nodeCount: number; freshCount: number; staleCount: number; storageCount: number; problemCells: number; problemNodes: number;
}
// 요약 줄: 서버 값이 우선이고, 없거나 모양이 틀리면 표와 같은 규칙으로 센다(옛 서버·부분 응답).
export function summaryOf(d: MountsOverview | undefined, nodes: MountNode[], cols: StorageCol[]): MountsSummary {
  const s = (d?.summary ?? null) as Partial<MountsOverview["summary"]> | null;
  const fresh = nodes.filter((n) => n.fresh === true);
  const cellsOf = (n: MountNode) => cols.filter((c) => cellProblem(cellOf(n, c))).length
    + (baseProblem(baseOf(n)) ? 1 : 0);
  return {
    nodeCount: count(s?.node_count) ?? nodes.length,
    freshCount: count(s?.fresh_node_count) ?? fresh.length,
    staleCount: count(s?.stale_node_count) ?? nodes.filter(isStale).length,
    storageCount: count(s?.storage_count) ?? cols.length,
    problemCells: count(s?.problem_cells) ?? fresh.reduce((a, n) => a + cellsOf(n), 0),
    problemNodes: count(s?.problem_nodes) ?? fresh.filter((n) => cellsOf(n) > 0).length,
  };
}

// 셀 설명(title·낭독). 상태 + 사유 + 에이전트 관찰값.
export function cellDetail(col: StorageCol, c: MountCell, node: MountNode): string {
  const head = `${node.node_name} · ${col.storage_name}`;
  switch (c.status) {
    case "ready": return `${head}: 마운트됨(읽기·쓰기)`;
    case "readonly": return `${head}: 마운트됐지만 쓰기 불가(읽기 전용)`;
    case "missing": return `${head}: ${reasonLong(c.reason) ?? "마운트 없음"}`;
    case "disabled": return `${head}: 비활성 스토리지(점검 대상 아님)`;
    default:
      return node.has_agent === false
        ? `${head}: 이 노드엔 에이전트가 없어 마운트를 보고하지 않습니다`
        : `${head}: 보고에 이 스토리지가 없습니다(옛 에이전트이거나 마운트 경로가 바뀐 뒤 아직 보고 전)`;
  }
}

// 아티팩트 경로 칸 설명(title·낭독). 머리가 「점검 실패」로 덮이면 실패한 역할부터 말한다 -- 「에이전트가 경로를 확인했습니다」만
// 남기면 빨간 머리와 설명이 두 말을 한다(dms-k8s e25a2dfb 의 공용 칸 역할 이름 이식). 에이전트 관찰은 뒤에 덧붙인다.
export function baseDetail(n: MountNode, b: MountBaseCell): string {
  const head = `${n.node_name} · 아티팩트 경로`;
  const mismatch = b.agent?.path_matches === false ? " (에이전트가 본 경로가 지금 설정과 다릅니다)" : "";
  if (baseHeadOverridden(b)) {
    const agent = b.status === "ready" ? "에이전트는 경로를 확인했습니다(존재·쓰기)"
      : n.has_agent === false ? "에이전트가 없어 노드 쪽 경로는 확인되지 않았습니다" : "에이전트 보고에는 경로 확인이 없습니다";
    return `${head}: 이 노드의 ${baseFailedRoles(b).join("·")} 파드가 아티팩트 경로 점검에 실패했습니다 — ${agent}${mismatch}. `
      + "파드의 hostPath·마운트 전파와 노드의 공유 스토리지 마운트를 확인하세요(아티팩트 경로 화면에 파드별 사유)";
  }
  switch (b.status) {
    case "ready": return `${head}: 에이전트가 경로를 확인했습니다(존재·쓰기)${mismatch}`;
    case "readonly": return `${head}: 경로는 있지만 쓰기 불가입니다${mismatch}`;
    case "missing": return `${head}: ${reasonLong(b.reason) ?? "경로가 없습니다"}${mismatch}`;
    default: return n.has_agent === false
      ? `${head}: 에이전트가 없어 경로를 보고하지 않습니다` : `${head}: 에이전트 보고에 경로 확인이 없습니다${mismatch}`;
  }
}
