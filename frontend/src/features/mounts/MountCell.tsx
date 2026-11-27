import type { HTMLAttributes, ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { TriangleAlert } from "lucide-react";
import { agoText, kstStamp } from "../../lib/datetime";
import { PlacementBadges } from "../nodes/NodePlacement";
import {
  SHARED_META, TONE_CLASS, cellHelp, cellMounted, cellSub, cellView, columnRuleText, columnSummaryText,
  coverText, isPodRole, podTag, roleLabel, sharedHelp, podFailedHelp, sharedDisplay, usageDetail, writableText, yesNo,
} from "./mountTerms";
import type { PodTagView, Tone } from "./mountTerms";
import type { ControlPod, MountCellData, MountRow, MountStatusResponse, SharedCellData, SharedMajority, StorageColumn } from "./types";

// 마운트 현황의 잎 부품(공용 코어 -- features/k8s 를 import 하지 않는다: dms-ssc 에는 그 디렉터리가 없다). 칸 칩·꼬리표·툴팁 내용.

export type ChipTone = "neutral" | "info" | "warn" | "bad" | "ok";
const CHIP: Record<ChipTone, string> = {
  neutral: "bg-panel text-ink/80", info: "bg-infobg text-accent", warn: "bg-attnbg text-attn",
  bad: "bg-badbg text-bad", ok: "bg-okbg text-ok",
};

/** 작은 꼬리표(행 역할·열 요약). 점 없이 글자와 면 색만 -- k8s/parts 의 Tag 와 같은 모양을 코어에 따로 둔다. 줄높이는 leading-4
 *  로 못 박는다(2026-11-25 -- 표의 text-sm 줄높이 20px 를 물려받아 칩이 24px 로 부풀었다, 진단 원인 5). compact = 좌우 여백을
 *  줄인 숫자 꼬리표(열 머리의 「⊗ 2」). */
export function Chip({ tone = "neutral", title, compact = false, children }: {
  tone?: ChipTone; title?: string; compact?: boolean; children: ReactNode;
}) {
  return (
    <span title={title}
          className={`inline-flex items-center whitespace-nowrap rounded-md ${compact ? "px-1" : "px-1.5"} py-0.5 text-[11px]
                      font-medium leading-4 ${CHIP[tone]}`}>
      {children}
    </span>
  );
}

/** 상태 칩(아이콘 + 글자 + 면색). 색만으로 구분하지 않는다. 폭은 부모를 넘지 않는다(max-w-full -- 표 칸은 MountCellChip 의 절대
 *  상한 max-w-24 가 묶는다): 모르는 원문 사유(새 에이전트의 사유 코드)는 칩 안에서 말줄임되고 전문은 aria-label·툴팁·시트에 있다. */
export function StateChip({ Icon, label, tone }: { Icon: LucideIcon; label: string; tone: Tone }) {
  return (
    <span className={`inline-flex max-w-full items-center gap-1 whitespace-nowrap rounded-full px-1.5 py-0.5 text-xs
                      font-semibold ${TONE_CLASS[tone]}`}>
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
      <span className="min-w-0 truncate">{label}</span>
    </span>
  );
}

const SUB = "max-w-full truncate text-[11px] leading-tight text-muted tabular-nums whitespace-nowrap";

type SpanProps = HTMLAttributes<HTMLSpanElement>;

/** 스토리지 칸: 칩 + 보조 줄(사용률·경로 바뀜·지연). 나머지 props(aria-label·폭 상한 className)는 바깥 span 에 -- 표의 칸 툴팁은
 *  칸(td)에 위임으로 건다(MountMatrix). nowhere = 어디에도 마운트 흔적이 없는 열의 「없음」(mountTerms.absentNowhere -- 문제로 센다;
 *  aria-label 은 부르는 쪽이 cellView(cell, nowhere).aria 로). */
export function MountCellChip({ cell, nowhere = false, className = "", ...rest }: { cell: MountCellData; nowhere?: boolean } & SpanProps) {
  const sub = cellSub(cell);
  const view = cellView(cell, nowhere);
  return (
    <span className={`inline-flex flex-col items-center gap-0.5 ${className}`} {...rest}>
      <StateChip Icon={view.Icon} label={view.label} tone={view.tone} />
      {sub !== null && <span className={SUB}>{sub}</span>}
    </span>
  );
}

/** DMS 공용 디렉터리 칸. */
export function SharedCellChip({ cell, podFailed = false, className = "", ...rest }: { cell: SharedCellData; podFailed?: boolean } & SpanProps) {
  const { meta, sub } = sharedDisplay(cell, podFailed);
  return (
    <span className={`inline-flex flex-col items-center gap-0.5 ${className}`} {...rest}>
      <StateChip Icon={meta.Icon} label={meta.label} tone={meta.tone} />
      {sub !== null && <span className={SUB}>{sub}</span>}
    </span>
  );
}

/** 문제 노드 꼬리표(표는 행 머리 꼬리표 줄 맨 앞 -- NodeTags lead, 노드별 카드는 이름 옆. 표의 고정 첫 열이라 가로로 민 표에서도,
 *  「문제 있는 노드」 필터를 켜지 않아도 보인다).
 *  수 = 서버 problem_count(마운트 문제(끊긴 마운트 포함) + 다른 노드와 다른 읽기 전용 + 어디에도 마운트 흔적이 없는 스토리지의 없음 +
 *  공용 디렉터리 문제 -- 이 노드 DMS 파드의 경로 점검 실패도 공용 디렉터리 한 칸으로 센다, 서버 mount_status_pods), 리포트 지연은
 *  「지연」(지금 상태 모름 --
 *  null ≠ 0). 아이콘 + 글자(색만으로 구분하지 않는다). showStale=false 면 지연 행에는 그리지 않는다(같은 머리에 「리포트 지연」 칩이
 *  이미 있는 노드별 카드). */
export function ProblemTag({ row, showStale = true }: { row: MountRow; showStale?: boolean }) {
  if (!row.problem) return null;
  const count = row.problem_count;
  if (count === null && !showStale) return null;
  return (
    <span title={count === null ? "리포트 지연 — 지금 마운트 상태를 모릅니다"
                 : `문제 ${count}건 — 마운트 문제(끊긴 마운트 포함)·다른 노드와 다른 읽기 전용·어느 노드에도 마운트 흔적이 없는 `
                   + "스토리지의 없음·DMS 공용 디렉터리 문제(이 노드의 dms-api·컨트롤러가 경로를 못 읽는 것 포함)"}
          className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md bg-badbg px-1.5 py-0.5 text-[11px]
                     font-semibold leading-4 text-bad">
      <TriangleAlert className="h-3 w-3 shrink-0" aria-hidden />
      {count === null ? "지연" : `문제 ${count}`}
    </span>
  );
}

/** DMS 파드 꼬리표(dms-api·컨트롤러). 경고(공용 디렉터리가 문제인 노드 또는 파드가 경로를 못 읽음)면 빨간 면 + 아이콘 + 화면 낭독용
 *  「경고」 -- 색만으로 구분하지 않는다. 문장은 title(마우스)과 노드 시트(키보드)에 같은 것을 싣는다(mountTerms.podTag). */
export function PodTag({ view }: { view: PodTagView }) {
  const title = view.lines.join("\n") || undefined;
  if (!view.alert) return <Chip tone="info" title={title}>{view.label}</Chip>;
  return (
    <Chip tone="bad" title={title}>
      <TriangleAlert className="mr-0.5 h-3 w-3 shrink-0" aria-hidden />
      <span className="sr-only">경고: </span>
      {view.label}
    </Chip>
  );
}

/** 행 꼬리표: (lead) → 배치 제외·cordon·일시 불가(노드 화면과 같은 배지) → control-plane → 역할 → 클러스터에 없음. 없으면 null.
 *  pods = 이 노드에 뜬 DMS 파드(mountTerms.podsOn) -- api_pod·controller_pod 꼬리표의 경고·문장 재료. lead = 맨 앞에 그릴 것(표의
 *  행 머리는 문제 꼬리표를 여기 둔다 -- 노드 이름이 고정 폭 첫 열을 다 쓰게, 2026-11-25). 줄높이 leading-4 로 배지·칩 높이를 맞춘다. */
export function NodeTags({ row, roleLabels, pods = [], lead, className = "" }: {
  row: MountRow; roleLabels?: Record<string, string>; pods?: ControlPod[]; lead?: ReactNode; className?: string;
}) {
  const node = { report: { k8s_node: row.k8s_node }, exclusion: row.exclusion };
  const any = row.exclusion !== null || row.k8s_node !== null || row.control_plane === true || row.roles.length > 0
    || row.in_cluster === false;
  if (!any && lead == null) return null;
  return (
    <span className={`flex flex-wrap items-center gap-1 leading-4 ${className}`}>
      {lead}
      <PlacementBadges node={node} />
      {row.control_plane === true && <Chip title="k8s control-plane 노드">control-plane</Chip>}
      {row.roles.map((role) => (isPodRole(role)
        ? <PodTag key={role} view={podTag(row, role, pods)} />
        : <Chip key={role} tone={roleLabels?.[role] !== undefined ? "info" : "neutral"}>{roleLabel(role, roleLabels)}</Chip>
      ))}
      {row.in_cluster === false && (
        <Chip tone="warn" title="k8s 노드 목록에 없습니다 — 퇴역한 노드의 옛 보고일 수 있습니다">클러스터에 없음</Chip>
      )}
    </span>
  );
}

function Dl({ children }: { children: ReactNode }) {
  return <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-0.5">{children}</dl>;
}
const Dt = ({ children }: { children: ReactNode }) => <dt className="whitespace-nowrap text-muted">{children}</dt>;

/** 스토리지 칸 툴팁 내용. nowhere = 이 칸의 열에 그 스토리지를 붙인 흔적이 하나도 없다(!columnFacts.mounted.has -- 「없음」 칸의 표시·
 *  설명이 갈린다; 모르면 생략). */
export function MountTipBody({ nodeName, col, cell, reportedAt, nowMs, nowhere }: {
  nodeName: string; col: StorageColumn; cell: MountCellData; reportedAt: string; nowMs: number; nowhere?: boolean;
}) {
  const ago = agoText(reportedAt, nowMs);
  const usage = usageDetail(cell.disk);
  const view = cellView(cell, nowhere === true);
  return (
    <div className="max-w-xs space-y-1.5">
      <div className="font-semibold [overflow-wrap:anywhere]">{`${nodeName} · ${col.storage_name}`}</div>
      <StateChip Icon={view.Icon} label={view.label} tone={view.tone} />
      <p>{cellHelp(cell, { mountPath: col.mount_path, ago, nowhere })}</p>
      <Dl>
        <Dt>마운트 경로</Dt><dd className="font-mono break-all">{col.mount_path}</dd>
        {cell.reported_path !== null && cell.reported_path !== col.mount_path && (
          <><Dt>보고된 경로</Dt><dd className="font-mono break-all">{cell.reported_path}</dd></>
        )}
        {cell.status !== null && (
          <><Dt>점검</Dt>
            <dd>{`존재 ${yesNo(cell.exists)} · 마운트 지점 ${yesNo(cell.is_mountpoint)} · 읽기 ${yesNo(cell.readable)} · `
              + `쓰기 ${writableText(cell.writable, cellMounted(cell))}`}</dd></>
        )}
        {usage !== null && <><Dt>용량</Dt><dd className="tabular-nums">{usage}</dd></>}
        <Dt>마지막 보고</Dt><dd>{`${ago} (${kstStamp(reportedAt)})`}</dd>
      </Dl>
      {cell.reason !== null && <p className="font-mono text-muted">{`사유 코드 ${cell.reason}`}</p>}
    </div>
  );
}

/** 공용 디렉터리 칸 툴팁 내용. */
export function SharedTipBody({ nodeName, path, cell, majority, reportedAt, nowMs, failedRoles = [] }: {
  nodeName: string; path: string; cell: SharedCellData; majority: SharedMajority | null; reportedAt: string; nowMs: number;
  failedRoles?: readonly string[];
}) {
  const { meta: base, overridden } = sharedDisplay(cell, failedRoles.length > 0);
  const meta = overridden ? { ...base, help: podFailedHelp(failedRoles) } : base;
  const ago = agoText(reportedAt, nowMs);
  return (
    <div className="max-w-xs space-y-1.5">
      <div className="font-semibold [overflow-wrap:anywhere]">{`${nodeName} · DMS 공용 디렉터리`}</div>
      <StateChip Icon={meta.Icon} label={meta.label} tone={meta.tone} />
      {overridden && <p>{meta.help}</p>}
      <p>{overridden ? `에이전트 보고: ${sharedHelp(cell, majority, ago)}` : sharedHelp(cell, majority, ago)}</p>
      <Dl>
        <Dt>경로</Dt><dd className="font-mono break-all">{path}</dd>
        {cell.reported_path !== null && cell.reported_path !== path && (
          <><Dt>보고된 경로</Dt><dd className="font-mono break-all">{cell.reported_path}</dd></>
        )}
        <Dt>존재 · 쓰기</Dt><dd>{`${yesNo(cell.exists)} · ${writableText(cell.writable, cell.exists === true)}`}</dd>
        <Dt>덮는 마운트</Dt><dd className="font-mono break-all">{coverText(cell)}</dd>
        <Dt>마지막 보고</Dt><dd>{`${ago} (${kstStamp(reportedAt)})`}</dd>
      </Dl>
    </div>
  );
}

/** 스토리지 열 머리 툴팁 내용(2026-11-25 -- 고정 폭 머리에서 잘리는 전체 이름·경로와 요약 문장, 사이트 덧붙임 extra). */
export function StorageTipBody({ col, extra }: { col: StorageColumn; extra?: ReactNode }) {
  return (
    <div className="max-w-xs space-y-1.5">
      <div className="font-mono font-semibold [overflow-wrap:anywhere]">{col.storage_name}</div>
      <div className="font-mono text-muted break-all">{col.mount_path}</div>
      <p>{columnSummaryText(col.summary)}</p>
      {extra}
      <p className="text-muted">{columnRuleText(col.summary.detail)}</p>
    </div>
  );
}

/** DMS 공용 열 머리 툴팁 내용(예전 th title). */
export function SharedHeadTipBody({ shared }: { shared: NonNullable<MountStatusResponse["shared_dir"]> }) {
  const m = shared.majority;
  return (
    <div className="max-w-xs space-y-1.5">
      <div className="font-semibold">DMS 공용 디렉터리</div>
      <div className="font-mono break-all">{shared.path}</div>
      <p>아티팩트 경로 — 잡 결과·로그가 쓰이는 공용 디렉터리입니다. 모든 노드의 같은 경로에 같은 공유 스토리지가 있어야 합니다.</p>
      {m !== null && (
        <p className="text-muted">{`대부분 노드: ${m.mount_point ?? "—"}${m.fstype ? ` (${m.fstype})` : ""}`}</p>
      )}
    </div>
  );
}
