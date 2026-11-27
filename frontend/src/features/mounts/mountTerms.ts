import type { LucideIcon } from "lucide-react";
import { CircleCheck, CircleQuestionMark, CircleX, Hourglass, Lock, Minus, TriangleAlert, Unplug } from "lucide-react";
import { reasonText } from "../../lib/api";
import type {
  CellState, ColumnSummary, ControlPod, MountCellData, MountDisk, MountRow, MountStatusResponse, SharedCellData, SharedMajority,
  SharedState, StorageColumn,
} from "./types";

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

// 「없음」 설명 셋(2026-11-27 -- 서버 mount_status 「문제 있는 노드」의 열 규칙): 같은 열에 그 스토리지를 붙인 흔적(정상·읽기 전용·점검
// 불가가 아닌 마운트 문제, 리포트 지연 노드는 마지막 값 -- columnFacts)이 있으면 안 붙인 노드라 문제 아님, 하나도 없으면 어디에도
// 마운트되지 않은 스토리지라 문제. 문구는 규칙만큼만 말한다(흔적이 없다 ≠ 단정) -- 열 재료를 모르는 곳(옛 호출)은 규칙을 함께 말하는
// ABSENT_HELP.
const ABSENT_HELP = "이 노드에 마운트 경로가 없습니다 — 이 스토리지를 붙이지 않은 노드라면 정상입니다(소스·목적지 노드를 나눈 "
  + "구성). 작업은 이 스토리지를 마운트한 노드에서만 돕니다. 단, 어느 노드에도 마운트 흔적이 없는 스토리지면 문제로 셉니다.";
const ABSENT_ELSEWHERE_HELP = "이 노드에 마운트 경로가 없습니다 — 다른 노드에 이 스토리지의 마운트 흔적이 있어(마운트 문제·리포트 지연 "
  + "노드의 마지막 값 포함), 이 스토리지를 붙이지 않은 노드라면 정상입니다(소스·목적지 노드를 나눈 구성). 작업은 이 스토리지를 "
  + "마운트한 노드에서만 돕니다.";
// (「읽기 전용」이라는 낱말은 쓰지 않는다 -- 붙지 않은 칸의 시트에 그 낱말이 보이면 ro 마운트를 의심하게 만든다: MountCell.test 그물)
const ABSENT_NOWHERE_HELP = "이 노드에 마운트 경로가 없고, 어느 노드에도 이 스토리지의 마운트 흔적이 없습니다(마운트돼 있거나 마운트 문제로 "
  + "보고한 노드가 하나도 없고, 리포트 지연 노드의 마지막 값도 그렇습니다) — 마운트 경로 오타이거나 아직 어느 노드에도 마운트하지 않은 "
  + "스토리지일 수 있어 문제로 셉니다. 방금 등록한 스토리지라면 아직 점검하지 않은 노드(확인 대기)가 있을 수 있습니다 — 다음 보고 뒤 "
  + "다시 보세요.";
// 끊긴 마운트(2026-11-27 -- dms-ssc d207 리뷰 이식): 에이전트는 Missing + missing_mount_path 로 보고하지만 호스트 mountinfo 엔 마운트가
// 있다(is_mountpoint 참). 서버는 이 칸만 broken 으로 둔다(missing_mount_path 인 나머지는 absent) -- 그래서 broken 칸의 이 사유는 늘 끊긴
// 마운트다(REASON_TEXT).
const DEAD_MOUNT_HELP = "끊긴 마운트 — 호스트에는 마운트가 있는데 경로에 접근할 수 없습니다(죽은 FUSE·낡은 NFS 핸들·마운트 전파 끊김). "
  + "노드에서 그 마운트를 다시 붙이세요(죽은 FUSE·낡은 NFS 는 lazy umount 뒤 다시 마운트) — 호스트에서는 경로가 보이면 dms-agent "
  + "파드를 다시 시작하세요.";
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

/** 에이전트 마운트 사유(agent.probes.probe_mounts) → **마운트 문제(broken) 칸의** 글자(short)·설명(long). missing_mount_path 는
 *  broken 칸에서만 여기 온다 = 호스트엔 마운트가 있는 끊긴 마운트(서버 classify_cell -- 호스트에도 없으면 「없음」 absent 칸이고
 *  CELL_META.absent 를 쓴다). */
export const REASON_TEXT: Record<string, { short: string; long: string; Icon?: LucideIcon }> = {
  missing_mount_path: { short: "끊긴 마운트", long: DEAD_MOUNT_HELP, Icon: Unplug },
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

// 열 단위 문제 판정의 거울(2026-11-27 -- 서버 mount_status._ro_outlier_columns·_mounted_columns·_attached·_cell_problem). ready 는 신선한
// 행만 본다(리포트 지연 행의 정상은 지금 쓰기 가능한지 모른다). mounted 는 리포트 지연 행도 마지막 값으로 본다 -- 그 스토리지를 붙인 노드가
// 하나뿐일 때 그 노드의 지연·끊긴 마운트를 다른 모든 노드의 「없음」 문제로 퍼뜨리지 않게(같은 날 이식 리뷰). 교차 픽스처
// mountProblemCases.json 을 서버 시험(tests/test_mount_status.py)과 이 파일의 시험이 함께 읽는다 -- 서버 규칙이 바뀌면 둘이 같이 빨개진다.
export interface ColumnFacts {
  /** 신선한 정상(쓰기 가능) 칸이 있는 열 -- 그 열의 읽기 전용은 바깥값(문제). */
  ready: ReadonlySet<string>;
  /** 그 스토리지를 붙인 흔적(attachedCell)이 있는 열 -- 그 열의 「없음」은 안 붙인 노드(문제 아님), 없으면 어디에도 없는 스토리지(문제). */
  mounted: ReadonlySet<string>;
}

/** 칸이 그 스토리지를 이 노드에 붙인(붙이려 한) 흔적인가(서버 _attached): 정상·읽기 전용, 또는 점검 불가(host_root_missing)가 아닌
 *  마운트 문제(끊긴 마운트·마운트 빠짐·전파 안 됨·읽기 불가 -- 호스트에 마운트나 디렉터리가 있다). 리포트 지연 칸은 마지막 값으로. */
export function attachedCell(cell: Pick<MountCellData, "state" | "last" | "reason">): boolean {
  const s = cell.state === "stale" ? cell.last : cell.state;
  return s === "ready" || s === "read_only" || (s === "broken" && cell.reason !== "host_root_missing");
}

/** 응답의 **모든** 행(행 필터 전)에서 열 재료를 모은다. */
export function columnFacts(rows: readonly MountRow[]): ColumnFacts {
  const ready = new Set<string>();
  const mounted = new Set<string>();
  for (const r of rows) {
    for (const [name, c] of Object.entries(r.cells)) {
      if (r.fresh && c.state === "ready") ready.add(name);
      if (attachedCell(c)) mounted.add(name);
    }
  }
  return { ready, mounted };
}

/** 두 열 재료가 같은 열 집합인가(폴링마다 새 객체여도 행 memo 를 깨지 않게 -- MountMatrix). */
export function sameColumnFacts(a: ColumnFacts, b: ColumnFacts): boolean {
  const same = (x: ReadonlySet<string>, y: ReadonlySet<string>) => x.size === y.size && [...x].every((v) => y.has(v));
  return a === b || (same(a.ready, b.ready) && same(a.mounted, b.mounted));
}

/** 신선한 행의 칸 하나가 문제인가(서버 _cell_problem): 마운트 문제 · 정상 노드가 있는 열의 읽기 전용 · 어디에도 없는 열의 「없음」. */
export function cellProblem(name: string, cell: Pick<MountCellData, "state">, facts: ColumnFacts): boolean {
  return cell.state === "broken" || (cell.state === "read_only" && facts.ready.has(name))
    || (cell.state === "absent" && !facts.mounted.has(name));
}

/** 어디에도 마운트되지 않은 열의 「없음」 칸인가(그 칸은 문제다 -- 표시·도움말이 갈린다). */
export const absentNowhere = (name: string, cell: Pick<MountCellData, "state">, facts: ColumnFacts): boolean =>
  cell.state === "absent" && !facts.mounted.has(name);

/** 어디에도 마운트되지 않은 열의 「없음」 칸 표시: 글자는 「없음」 그대로, 아이콘·면색이 문제를 말하고(색만으로 구분하지 않는다 --
 *  아이콘이 다르다) 낭독 글자가 뜻을 함께 말한다(범례도 이 칩을 그린다 -- MountStatusPage). */
export const ABSENT_NOWHERE_META: StateMeta = { label: "없음", Icon: TriangleAlert, tone: "bad", help: ABSENT_NOWHERE_HELP };
const ABSENT_NOWHERE_ARIA = "없음(어느 노드에도 마운트 흔적 없음)";

export interface CellView { Icon: LucideIcon; label: string; tone: Tone; aria: string }

/** 칸 칩의 표시(아이콘·글자·면색 + 낭독 글자). nowhere = absentNowhere(부르는 쪽이 응답의 모든 행으로 잰 columnFacts). */
export function cellView(cell: CellLike, nowhere = false): CellView {
  if (nowhere && cell.state === "absent") {
    const m = ABSENT_NOWHERE_META;
    return { Icon: m.Icon, label: m.label, tone: m.tone, aria: ABSENT_NOWHERE_ARIA };
  }
  const label = cellLabel(cell);
  return { Icon: cellIcon(cell), label, tone: CELL_META[cell.state].tone, aria: label };
}

/** stale 칸의 「마지막 값」 글자 -- last 판정에 원문 사유를 얹어 cellLabel 로. */
function lastLabel(cell: MountCellData): string {
  return cell.last === null ? "—" : cellLabel({ state: cell.last, reason: cell.reason, status: cell.status });
}

/** 칸의 긴 설명(툴팁·시트). ago = 마지막 보고 경과(「12분 전」), mountPath = 지금 스토리지 마운트 경로, nowhere = 이 칸의 열에 그
 *  스토리지를 붙인 흔적이 하나도 없다(!columnFacts.mounted.has -- 「없음」 칸의 설명이 갈린다; 모르면 생략 = 규칙을 함께 말하는 설명). */
export function cellHelp(cell: MountCellData, ctx: { mountPath: string; ago: string; nowhere?: boolean }): string {
  switch (cell.state) {
    case "ready":
      return cell.writable === null
        ? `${CELL_META.ready.help} 쓰기 가능 여부는 이 에이전트가 보고하지 않습니다(옛 에이전트).`
        : CELL_META.ready.help;
    case "absent":
      return ctx.nowhere === true ? ABSENT_NOWHERE_HELP : ctx.nowhere === false ? ABSENT_ELSEWHERE_HELP : ABSENT_HELP;
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
 *  `exists and …`) -- 그 칸을 읽기 전용이라 말하면 ro 마운트 옵션을 의심하게 만든다(진짜 원인은 빠진 마운트). 붙지 않았으면 「—」. */
export function writableText(v: boolean | null, mounted: boolean): string {
  // 붙지 않은 대상은 쓰기 여부를 따질 수 없다 -- 「불가」 대신 「—」(2026-11-23 d38 실증: 경로가 없는 칸이 「쓰기 불가」로 읽혔다).
  if (!mounted) return "—";
  if (v === true) return "가능";
  if (v === false) return "불가(읽기 전용)";
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

// DMS 파드 꼬리표(2026-11-23 -- 서버 mount_status_pods). 두 라인 공용 재료라 공용 코어에 둔다(k8s 덧붙임 아님).
export const POD_ROLE_LABELS: Record<string, string> = { api_pod: "dms-api", controller_pod: "컨트롤러" };
export const CORE_ROLE_LABELS: Record<string, string> = { build: "빌드 노드", ...POD_ROLE_LABELS };
export const isPodRole = (role: string): boolean => Object.prototype.hasOwnProperty.call(POD_ROLE_LABELS, role);

/** 행을 「문제 있는 노드」로 세는 공용 디렉터리 상태(서버 mount_status.SHARED_PROBLEM_STATES 와 같다). */
export const SHARED_PROBLEM_STATES: readonly SharedState[] = ["local", "differs", "missing", "read_only"];

/** 이 노드의 dms-api·컨트롤러 파드가 공용 디렉터리(아티팩트 경로) 점검에 실패했는데 에이전트는 정상으로 보는 칸(2026-11-23 — dms-ssc
 *  68dcbcdb 와 같은 원칙: 「문제 있는 노드」로 세면서 칸은 초록이면 모순이다). 파드 hostPath·마운트 전파·옛 에이전트일 수 있다. */
export const POD_FAILED_META: StateMeta = {
  label: "파드 점검 실패", Icon: CircleX, tone: "bad",
  help: podFailedHelp([]),
};

/** 공용 칸 「파드 점검 실패」 도움말 — 실패한 역할을 이름으로(2026-11-23 d40 실증: 고정 문구 「dms-api·컨트롤러」는 dms-api 만 실패해도
 *  둘 다로 읽혔다). roles 가 비면(모름) 둘 다. */
export function podFailedHelp(roles: readonly string[]): string {
  const who = roles.length > 0 ? roles.join("·") : "dms-api·컨트롤러";
  return `이 노드의 ${who} 파드가 아티팩트 경로를 점검하지 못했습니다 — 에이전트는 공용 디렉터리를 정상으로 봅니다. 파드의 `
    + "hostPath·마운트 전파·노드의 공유 스토리지 마운트를 확인하세요(아티팩트 경로 화면에 파드별 사유).";
}

/** 공용 디렉터리 칸의 표시. 에이전트 판정이 이미 문제거나 리포트 지연이면 그대로, 아니면 파드 점검 실패(podFailed)가 칸을 덮는다
 *  (에이전트 판정은 보조 줄). podFailed = 이 노드 파드 중 read_failed 가 있다(podsOn — 서버 mount_status_pods 와 같은 가름). */
export function sharedDisplay(cell: SharedCellData, podFailed: boolean): { meta: StateMeta; sub: string | null; overridden: boolean } {
  const base = SHARED_META[cell.state];
  if (podFailed && cell.state !== "stale" && !SHARED_PROBLEM_STATES.includes(cell.state))
    return { meta: POD_FAILED_META, sub: `에이전트: ${base.label}`, overridden: true };
  return { meta: base, sub: sharedSub(cell), overridden: false };
}

/** 이 노드에 뜬 DMS 파드(응답의 control_pods -- 덧붙임이 없는 응답이면 빈 목록). */
export function podsOn(data: Pick<MountStatusResponse, "control_pods">, node: string): ControlPod[] {
  return (data.control_pods ?? []).filter((p) => p.node_name === node);
}

/** 이 노드의 DMS 파드 중 아티팩트 경로 점검이 실패한 것이 있나(공용 칸 표시 — sharedDisplay). */
export function podFailedOn(data: Pick<MountStatusResponse, "control_pods">, node: string): boolean {
  return podsOn(data, node).some((p) => p.read_failed);
}

/** 이 노드에서 경로 점검이 실패한 DMS 파드의 역할 라벨(중복 없이 — 「dms-api」·「컨트롤러」). */
export function podFailedRoles(data: Pick<MountStatusResponse, "control_pods">, node: string): string[] {
  return [...new Set(podsOn(data, node).filter((p) => p.read_failed).map((p) => roleLabel(p.role)))];
}

/** 파드 자신의 아티팩트 경로 점검 한 마디. 권한(posture) 사유만인 실패는 경로 점검 실패와 섞지 않는다(read_failed -- 서버 판정).
 *  read_failed 는 「읽기」만이 아니다 -- 컨트롤러 점검의 쓰기 왕복 실패(artifact_base_not_writable)도 든다 -- 그래서 글자는 중립인
 *  「점검 실패」이고 무엇이 실패했는지는 사유 문장이 말한다(2026-11-23 이식 리뷰). */
export function podCheckText(pod: ControlPod): string {
  if (pod.ok === true) return "아티팩트 경로 점검 정상";
  if (pod.ok === false) {
    return `아티팩트 경로 ${pod.read_failed ? "점검 실패" : "권한 문제"} — ${reasonText(pod.reason) || "사유 모름"}`;
  }
  return "아티팩트 경로 점검 결과 모름";
}

// 시작 점검(게이트) 중 사람이 볼 상태만(아티팩트 경로 화면 ArtifactBaseChecks.gateText 의 attn 둘과 같은 뜻 -- 그 모듈은 대시보드까지
// 끌고 와 여기서 import 하지 않는다).
const GATE_ATTN: Record<string, string> = {
  waiting: "시작 점검에 막혀 Ready 대기 중(서비스 밖)",
  started_degraded: "형제 파드가 없어 점검 실패인 채 서비스 시작",
};

const podName = (p: ControlPod) => p.instance ?? POD_ROLE_LABELS[p.role] ?? p.role;

/** 파드 한 줄(툴팁·시트): 「dms-api-7f9c: 아티팩트 경로 점검 실패 — … · 시작 점검에 막혀 Ready 대기 중(서비스 밖)」 */
export function podLine(pod: ControlPod): string {
  const gate = pod.gate_state !== null ? GATE_ATTN[pod.gate_state] : undefined;
  return `${podName(pod)}: ${podCheckText(pod)}${gate ? ` · ${gate}` : ""}`;
}

const POD_IMPACT: Record<string, { sure: string; maybe: string }> = {
  api_pod: { sure: "여기 뜬 dms-api 는 다른 노드가 쓴 아티팩트·scan 리포트를 읽지 못합니다(포탈에 「공유 스토리지 접근 불가」).",
             maybe: "여기 뜬 dms-api 가 다른 노드가 쓴 아티팩트·scan 리포트를 못 볼 수 있습니다." },
  controller_pod: { sure: "여기 뜬 컨트롤러는 작업 결과 요약을 읽지 못합니다(요약 확정이 미뤄집니다).",
                    maybe: "여기 뜬 컨트롤러가 작업 결과 요약을 못 볼 수 있습니다." },
};

function sharedImpact(role: string, state: SharedState): string {
  if (state === "read_only") return SHARED_META.read_only.help;
  const impact = POD_IMPACT[role];
  if (impact === undefined) return SHARED_META[state].help;
  return state === "differs" ? impact.maybe : impact.sure;
}

export interface PodTagView { role: string; label: string; alert: boolean; lines: string[] }

/** 행의 파드 꼬리표 하나(역할별 -- 같은 노드의 API 파드 둘은 「dms-api ×2」). alert = 이 노드의 공용 디렉터리 칸이 문제(신선한 행
 *  -- 리포트 지연이면 지금 상태를 모른다)이거나 이 역할 파드의 경로 점검이 실패했다(read_failed) -- 「공용 디렉터리가 문제인 노드 + 거기
 *  뜬 dms-api」가 2026-11-23 사고의 서명이다. lines = 툴팁·시트 문장(공용 디렉터리 영향 → 파드별 점검). */
export function podTag(row: MountRow, role: string, pods: ControlPod[]): PodTagView {
  const mine = pods.filter((p) => p.role === role);
  const name = POD_ROLE_LABELS[role] ?? role;
  const shared = row.fresh && row.shared_dir !== null && SHARED_PROBLEM_STATES.includes(row.shared_dir.state)
    ? row.shared_dir.state : null;
  const lines: string[] = [];
  if (shared !== null) lines.push(`이 노드의 DMS 공용 디렉터리: ${SHARED_META[shared].label} — ${sharedImpact(role, shared)}`);
  lines.push(...mine.map(podLine));
  return { role, label: mine.length > 1 ? `${name} ×${mine.length}` : name,
           alert: shared !== null || mine.some((p) => p.read_failed), lines };
}

/** 행 꼬리표 글자 -- 사이트 덧붙임(extra) > 공용 > 원문. */
export function roleLabel(role: string, extra?: Record<string, string>): string {
  return extra?.[role] ?? CORE_ROLE_LABELS[role] ?? role;
}

/** 칸의 접근성 이름 「k8s-w1 · cephfs-secondary: 없음」(label = cellView(...).aria) */
export function cellAriaLabel(node: string, column: string, label: string): string {
  return `${node} · ${column}: ${label}`;
}

// 열(스토리지) 쪽 표기·거르기(2026-11-25 사용자 요청 「스토리지가 100개가 될 수도 있어」 -- 표 머리 압축·열 거르기).

/** 「문제 있는 스토리지」 열(열 거르기 ?cols=problems) -- 서버 「문제 있는 노드」 칸 판정(_cell_problem)의 열 쪽 거울: 신선한 행에
 *  문제 칸(cellProblem)이 하나라도 있는 열 = 마운트 문제(끊긴 마운트 포함 -- summary.broken > 0), 신선한 ready 가 있는 열의 읽기
 *  전용, 붙인 흔적(attachedCell)이 하나도 없는 열의 「없음」(어디에도 마운트되지 않은 스토리지 -- 2026-11-27). 모든 노드가 ro 인
 *  열(의도된 구성)·다른 노드에 흔적이 있는 열의 없음·확인 대기·리포트 지연은 문제로 세지 않는다. rows = 응답의 모든 행(행 필터 전),
 *  facts = 같은 rows 의 columnFacts(열마다 다시 재지 않게 부르는 쪽이 한 번 넘겨도 된다). */
export function columnProblem(col: StorageColumn, rows: readonly MountRow[], facts: ColumnFacts = columnFacts(rows)): boolean {
  if (col.summary.broken > 0) return true;
  const name = col.storage_name;
  return rows.some((r) => {
    const cell = r.cells[name];
    return r.fresh && cell !== undefined && cellProblem(name, cell, facts);
  });
}

/** 신선한 행에 어디에도 마운트되지 않은 열의 「없음」 칸(absentNowhere)이 있는 스토리지 수 -- 「마운트 문제」 타일(서버 totals.broken 은
 *  이 칸을 세지 않는다: 칸 상태는 absent 그대로). rows = 응답의 모든 행, facts = 같은 rows 의 columnFacts. */
export function nowhereColumnCount(cols: readonly StorageColumn[], rows: readonly MountRow[],
                                   facts: ColumnFacts = columnFacts(rows)): number {
  return cols.filter((col) => rows.some((r) => {
    const cell = r.cells[col.storage_name];
    return r.fresh && cell !== undefined && absentNowhere(col.storage_name, cell, facts);
  })).length;
}

/** q(앞뒤 공백 떼고 대소문자 무시)가 storage_name 또는 mount_path 에 들어 있는 열 ∩ (problemsOnly 면 columnProblem). 응답 순서
 *  유지. 조건이 없으면 **같은 배열**을 돌려준다(부르는 쪽 memo 가 깨지지 않게). */
export function filterColumns(cols: StorageColumn[], rows: readonly MountRow[],
                              f: { q: string; problemsOnly: boolean }): StorageColumn[] {
  const q = f.q.trim().toLowerCase();
  if (q === "" && !f.problemsOnly) return cols;
  const facts = f.problemsOnly ? columnFacts(rows) : undefined;
  return cols.filter((c) =>
    (q === "" || c.storage_name.toLowerCase().includes(q) || c.mount_path.toLowerCase().includes(q))
    && (facts === undefined || columnProblem(c, rows, facts)));
}

/** 열 머리 툴팁의 요약 문장 「마운트 3/4대 · 마운트 문제 1칸 · 읽기 전용 1칸 · 없음 1칸 · 확인 대기 1칸」(0 은 뺀다 -- 신선한 칸만
 *  센 수). 보고가 하나도 없으면(total 0) 앞머리가 「보고 없음」. */
export function columnSummaryText(s: ColumnSummary): string {
  const parts = [s.total > 0 ? `마운트 ${s.ready}/${s.total}대` : "보고 없음"];
  const counted: [number, string][] = [
    [s.broken, CELL_META.broken.label], [s.read_only, CELL_META.read_only.label],
    [s.absent, CELL_META.absent.label], [s.pending, CELL_META.pending.label],
  ];
  for (const [n, label] of counted) if (n > 0) parts.push(`${label} ${n}칸`);
  return parts.join(" · ");
}

/** 열 머리 툴팁의 기준 문장(예전 th title -- 내장 툴팁은 열 머리 HoverTip 과 겹쳐 뺐다). */
export const columnRuleText = (detail: string): string =>
  `기준: 신선한 보고만 — 스토리지 목록의 상태(${detail})와 같은 규칙, 읽기 전용도 마운트로 셉니다`;

/** 열 머리 이름 칸의 보이는 줄(고정 폭 6.5rem 머리의 이름 칸은 두 줄). 머리 상자(w-24 = 96px)에 12px 등폭 글자가 한 줄 13자
 *  들어간다(스토리지 이름은 [a-z0-9-] -- 저장소 storages._NAME_RE, 등폭 글꼴 advance 약 0.6em = 7.2px).
 *  - 13자 이하: 한 줄.
 *  - 26자(두 줄) 이하: **줄을 직접 나눈다** -- 두 조각이 모두 13자 이하가 되는 가장 뒤의 하이픈 뒤(「cephfs-」/「secondary」), 없으면
 *    13자에서. 브라우저 자연 줄바꿈(하이픈 뒤에서 먼저 꺾고 「-01」 앞은 못 꺾는다)에 맡기면 24자 이름도 세 줄이 돼 두 줄 칸에서
 *    끝(구별하는 접미)이 잘렸다(2026-11-25 리뷰 실측: 「weka-research-archive-01」 → 「weka-」/「research-…」).
 *  - 26자 초과: [이름 전체(앞 조각 -- 화면이 말줄임), 「…」 + 끝 12자] -- 같은 접두로 시작하는 스토리지들을 끝으로 가른다(전체 작업
 *    경로 열의 「끝을 보인다」 선례). */
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

/** 열 머리 경로 줄의 끝 조각(2026-11-25 리뷰) -- 11px 등폭 글자가 머리 상자에 14자 들어간다. 14자 이하면 null(전체를 그린다).
 *  넘으면 끝 13자 안에서 경로 조각 경계(「/」)로 시작하는 가장 긴 끝(8자 이상일 때 -- 「/gpfs/proj01」), 아니면 그냥 끝 13자 --
 *  화면은 앞에 「…」를 붙인다(낭독은 전체 경로). 프로덕션 mount_path 는 /mgmt_storage/… 처럼 앞이 길게 같아 앞 조각을 보이면(말줄임)
 *  모든 열이 「/mgmt_storage…」로 똑같이 보였다. */
export const COLUMN_PATH_LINE = 14;
export function columnPathTail(path: string): string | null {
  const chars = [...path];
  if (chars.length <= COLUMN_PATH_LINE) return null;
  const tail = chars.slice(-(COLUMN_PATH_LINE - 1));
  const slash = tail.indexOf("/");
  return slash >= 0 && tail.length - slash >= 8 ? tail.slice(slash).join("") : tail.join("");
}
