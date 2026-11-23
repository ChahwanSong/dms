import type { LucideIcon } from "lucide-react";
import { CircleCheck, CircleQuestionMark, CircleX, Hourglass, Lock, Minus, TriangleAlert } from "lucide-react";
import type { CellState, MountCellData, MountDisk, SharedCellData, SharedMajority, SharedState } from "./types";

// 마운트 현황(2026-11-23)의 용어·표기 **단일 출처**(공용 코어 -- k8s 를 모른다). 칸은 색만으로 구분하지 않는다: 아이콘 + 글자 +
// 면색. 에이전트 원문(Ready/Missing·사유 코드)은 칸에 쓰지 않고 여기서 한국어로 바꾼다 -- 원문 사유 코드는 툴팁·시트 끝에만.
// 마운트 사유는 에이전트 보고의 dict 값이라 reasonCodes.json(HTTP detail 계약)·stateHelp.json(서버 상태기계) 밖이다 -- 이 로컬
// 맵에만 둔다(k8sTerms SYNC_REASON_SHORT 선례). 모르는 사유·역할은 원문 그대로 보인다(지어내지 않는다).

export type Tone = "ok" | "attn" | "muted" | "bad" | "busy" | "stale";

/** 칸 칩의 글자·면 색(토큰만 -- 화면은 hex 를 모른다). 흐림은 opacity 가 아니라 글자 알파(text-ink/70 -- 흰 바탕 약 4.9:1, AA) --
 *  opacity 는 쌓임 맥락을 만들어 고정 칸 위로 겹친다. /50 은 2.8:1 이라 리포트 지연 노드를 가장 읽기 어렵게 만들었다. */
export const TONE_CLASS: Record<Tone, string> = {
  ok: "text-ok bg-okbg",
  attn: "text-attn bg-attnbg",
  muted: "text-muted",
  bad: "text-bad bg-badbg",
  busy: "text-busy bg-busybg",
  stale: "text-ink/70 bg-canvas",
};

export interface StateMeta { label: string; Icon: LucideIcon; tone: Tone; help: string }

const ABSENT_HELP = "이 노드에 마운트 경로가 없습니다 — 이 스토리지를 붙이지 않은 노드라면 정상입니다(소스·목적지 노드를 나눈 "
  + "구성). 작업은 이 스토리지를 마운트한 노드에서만 돕니다.";
const UNKNOWN_REASON_HELP = "알 수 없는 사유 코드입니다(에이전트 버전 차이일 수 있음).";

export const CELL_META: Record<CellState, StateMeta> = {
  ready: { label: "정상", Icon: CircleCheck, tone: "ok", help: "마운트돼 있고 읽을 수 있습니다." },
  read_only: { label: "읽기 전용", Icon: Lock, tone: "attn",
               help: "읽기 전용(ro) 마운트입니다 — sync 목적지·rm 작업은 이 노드에 배치되지 않습니다(소스·scan 은 됩니다)." },
  absent: { label: "없음", Icon: Minus, tone: "muted", help: ABSENT_HELP },
  broken: { label: "마운트 문제", Icon: CircleX, tone: "bad", help: UNKNOWN_REASON_HELP },
  pending: { label: "확인 대기", Icon: Hourglass, tone: "busy",
             help: "이 노드가 아직 이 스토리지를 점검해 보고하지 않았습니다 — 스토리지를 추가·활성화한 직후라면 다음 보고(약 1~2분)에 "
               + "반영됩니다." },
  stale: { label: "모름", Icon: CircleQuestionMark, tone: "stale", help: "리포트 지연 — 지금 상태는 모릅니다." },
};

/** 에이전트 마운트 사유(agent.probes.probe_mounts) → 칸 글자(short)·설명(long). */
export const REASON_TEXT: Record<string, { short: string; long: string; Icon?: LucideIcon }> = {
  missing_mount_path: { short: "없음", long: ABSENT_HELP },
  not_a_mountpoint: {
    short: "마운트 빠짐",
    long: "디렉터리는 있지만 마운트 지점이 아닙니다 — 마운트가 풀렸거나 부팅 때 붙지 않아 빈 로컬 디렉터리만 남았습니다.",
  },
  propagation_stale: {
    short: "전파 안 됨",
    long: "호스트에는 마운트돼 있지만 에이전트 파드에서 보이지 않습니다(마운트 전파) — dms-agent 파드를 다시 시작하세요.",
  },
  not_readable: { short: "읽기 불가", long: "마운트는 있지만 읽거나 들어갈 수 없습니다 — 권한·스토리지 상태를 확인하세요." },
  host_root_missing: {
    short: "점검 불가", Icon: TriangleAlert,
    long: "에이전트 파드에 호스트 루트(/host/root)가 없어 이 노드의 마운트를 점검할 수 없습니다 — dms-agent DaemonSet 설정을 "
      + "확인하세요.",
  },
};

export const SHARED_META: Record<SharedState, StateMeta> = {
  ok: { label: "정상", Icon: CircleCheck, tone: "ok", help: "공유 마운트 위에 있고 쓸 수 있습니다." },
  ok_unverified: { label: "있음", Icon: CircleCheck, tone: "ok",
                   help: "디렉터리가 있고 쓸 수 있습니다 — 공유 스토리지 마운트 여부는 이 에이전트가 보고하지 않습니다(옛 에이전트)." },
  local: { label: "로컬 디렉터리", Icon: TriangleAlert, tone: "bad",
           help: "공용 디렉터리가 루트 파일시스템(/) 위의 로컬 디렉터리입니다 — 공유 스토리지가 이 노드에 마운트되지 않았습니다. 이 "
             + "노드에 뜬 dms-api·작업 파드는 다른 노드가 쓴 결과를 보지 못합니다." },
  differs: { label: "다른 노드와 다름", Icon: TriangleAlert, tone: "attn",
             help: "이 노드의 공용 디렉터리 마운트가 대부분 노드와 다릅니다 — 같은 공유 스토리지인지 확인하세요." },
  missing: { label: "없음", Icon: CircleX, tone: "bad",
             help: "이 노드에 공용 디렉터리가 없습니다 — 작업 파드·dms-api 파드가 이 노드에서 뜨지 못합니다(hostPath type: Directory)." },
  read_only: { label: "쓰기 불가", Icon: Lock, tone: "attn", help: "공용 디렉터리에 쓸 수 없습니다 — 작업 결과 파일을 남길 수 없습니다." },
  pending: { label: "확인 대기", Icon: Hourglass, tone: "busy", help: "이 노드가 아직 현재 공용 디렉터리 경로를 점검하지 않았습니다." },
  stale: { label: "모름", Icon: CircleQuestionMark, tone: "stale", help: "리포트 지연 — 지금 상태는 모릅니다." },
};

type CellLike = Pick<MountCellData, "state" | "reason" | "status">;

/** 칸 글자. 마운트 문제는 사유별 짧은 글자(모르는 사유는 원문 사유, 그것도 없으면 원문 status). */
export function cellLabel(cell: CellLike): string {
  if (cell.state === "broken") {
    const known = cell.reason !== null ? REASON_TEXT[cell.reason] : undefined;
    return known?.short ?? cell.reason ?? cell.status ?? CELL_META.broken.label;
  }
  return CELL_META[cell.state].label;
}

export function cellIcon(cell: CellLike): LucideIcon {
  if (cell.state === "broken" && cell.reason !== null) return REASON_TEXT[cell.reason]?.Icon ?? CELL_META.broken.Icon;
  return CELL_META[cell.state].Icon;
}

/** stale 칸의 「마지막 값」 글자 -- last 판정에 원문 사유를 얹어 cellLabel 로. */
function lastLabel(cell: MountCellData): string {
  return cell.last === null ? "—" : cellLabel({ state: cell.last, reason: cell.reason, status: cell.status });
}

/** 칸의 긴 설명(툴팁·시트). ago = 마지막 보고 경과(「12분 전」), mountPath = 지금 스토리지 마운트 경로. */
export function cellHelp(cell: MountCellData, ctx: { mountPath: string; ago: string }): string {
  switch (cell.state) {
    case "ready":
      return cell.writable === null
        ? `${CELL_META.ready.help} 쓰기 가능 여부는 이 에이전트가 보고하지 않습니다(옛 에이전트).`
        : CELL_META.ready.help;
    case "broken":
      return (cell.reason !== null ? REASON_TEXT[cell.reason]?.long : undefined) ?? UNKNOWN_REASON_HELP;
    case "pending":
      return cell.pending === "path_changed"
        ? `에이전트가 옛 마운트 경로(${cell.reported_path ?? "—"})로 점검한 결과입니다 — 다음 보고에 현재 경로(${ctx.mountPath})로 `
          + "다시 점검합니다."
        : CELL_META.pending.help;
    case "stale":
      return `리포트 지연(마지막 보고 ${ctx.ago}) — 마지막 값: ${lastLabel(cell)}. 지금 상태는 모르며 스토리지 상태 집계에서 뺍니다.`;
    default:
      return CELL_META[cell.state].help;
  }
}

/** 칩 아래 보조 줄(없으면 null). */
export function cellSub(cell: MountCellData): string | null {
  if (cell.state === "ready" || cell.state === "read_only") return usageText(cell.disk);
  if (cell.state === "pending" && cell.pending === "path_changed") return "경로 바뀜";
  if (cell.state === "stale") return "지연";
  return null;
}

export function sharedSub(cell: SharedCellData): string | null {
  if (cell.state === "ok" || cell.state === "differs") return cell.fstype;
  if (cell.state === "ok_unverified") return "공유 여부 모름";
  if (cell.state === "stale") return "지연";
  return null;
}

const mountText = (mountPoint: string | null, fstype: string | null) =>
  mountPoint === null ? "모름" : fstype === null ? mountPoint : `${mountPoint} ${fstype}`;

export function sharedHelp(cell: SharedCellData, majority: SharedMajority | null, ago: string): string {
  switch (cell.state) {
    case "ok":
      return `공유 마운트 ${cell.fstype ?? "(종류 모름)"} · ${cell.mount_point ?? "—"} 에 있고 `
        + (cell.writable === true ? "쓸 수 있습니다." : "쓰기 가능 여부는 모릅니다.");
    case "differs":
      return `이 노드의 공용 디렉터리 마운트(${mountText(cell.mount_point, cell.fstype)})가 대부분 노드`
        + `(${majority ? mountText(majority.mount_point, majority.fstype) : "—"})와 다릅니다 — 같은 공유 스토리지인지 확인하세요.`;
    case "stale":
      return `리포트 지연(마지막 보고 ${ago}) — 마지막 값: ${cell.last === null ? "—" : SHARED_META[cell.last].label}. `
        + "지금 상태는 모릅니다.";
    default:
      return SHARED_META[cell.state].help;
  }
}

export function coverText(cell: Pick<SharedCellData, "mount_point" | "fstype">): string {
  if (cell.mount_point === null) return "모름";
  return cell.fstype === null ? cell.mount_point : `${cell.mount_point} (${cell.fstype})`;
}

/** 「사용 41%」 -- 전체 0·모름이면 null(0% 로 지어내지 않는다). */
export function usageText(disk: MountDisk | null): string | null {
  if (disk === null || !(disk.total_bytes > 0)) return null;
  return `사용 ${Math.round((disk.used_bytes / disk.total_bytes) * 100)}%`;
}

const BYTE_UNITS: [string, number][] = [["PiB", 1024 ** 5], ["TiB", 1024 ** 4], ["GiB", 1024 ** 3], ["MiB", 1024 ** 2]];
export function bytesText(n: number): string {
  for (const [unit, size] of BYTE_UNITS) if (n >= size) return `${(n / size).toFixed(1)} ${unit}`;
  return `${n} B`;
}

/** 「사용 41% · 1.2 TiB / 2.9 TiB」(툴팁·시트) -- 모르면 null. */
export function usageDetail(disk: MountDisk | null): string | null {
  const pct = usageText(disk);
  return pct === null || disk === null ? null : `${pct} · ${bytesText(disk.used_bytes)} / ${bytesText(disk.total_bytes)}`;
}

/** 쓰기 여부 글자. 「(읽기 전용)」은 대상이 실제로 붙어 있을 때(mounted -- 칸은 cellMounted, 공용 디렉터리는 exists === true)만
 *  붙인다: 에이전트는 경로가 없거나 마운트 지점이 아니어도 writable=false 를 보고한다(probe_mounts·probe_artifact_base 의
 *  `exists and …`) -- 그 칸을 읽기 전용이라 말하면 ro 마운트 옵션을 의심하게 만든다(진짜 원인은 빠진 마운트). */
export function writableText(v: boolean | null, mounted: boolean): string {
  if (v === true) return "가능";
  if (v === false) return mounted ? "불가(읽기 전용)" : "불가";
  return "모름";
}

/** 스토리지가 이 노드에 붙어 있는 칸인가(정상·읽기 전용 -- 리포트 지연이면 마지막 값 기준). */
export function cellMounted(cell: Pick<MountCellData, "state" | "last">): boolean {
  const s = cell.state === "stale" ? cell.last : cell.state;
  return s === "ready" || s === "read_only";
}

export function yesNo(v: boolean | null): string {
  return v === true ? "예" : v === false ? "아니오" : "—";
}

export function probeModeText(mode: string | null): string {
  if (mode === null) return "—";
  return mode === "host_root" ? "호스트 마운트 표" : mode;
}

export const CORE_ROLE_LABELS: Record<string, string> = { build: "빌드 노드" };

/** 행 꼬리표 글자 -- 사이트 덧붙임(extra) > 공용 > 원문. */
export function roleLabel(role: string, extra?: Record<string, string>): string {
  return extra?.[role] ?? CORE_ROLE_LABELS[role] ?? role;
}

/** 칸의 접근성 이름 「k8s-w1 · cephfs-secondary: 없음」 */
export function cellAriaLabel(node: string, column: string, label: string): string {
  return `${node} · ${column}: ${label}`;
}
