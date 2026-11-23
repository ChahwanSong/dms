import type { HTMLAttributes, ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { TriangleAlert } from "lucide-react";
import { agoText, kstStamp } from "../../lib/datetime";
import { PlacementBadges } from "../nodes/NodePlacement";
import {
  CELL_META, SHARED_META, TONE_CLASS, cellHelp, cellIcon, cellLabel, cellMounted, cellSub, coverText, isPodRole, podTag,
  roleLabel, sharedHelp, sharedDisplay, sharedSub, usageDetail, writableText, yesNo,
} from "./mountTerms";
import type { PodTagView, Tone } from "./mountTerms";
import type { ControlPod, MountCellData, MountRow, SharedCellData, SharedMajority, StorageColumn } from "./types";

// 마운트 현황의 잎 부품(공용 코어 -- features/k8s 를 import 하지 않는다: dms-ssc 에는 그 디렉터리가 없다). 칸 칩·꼬리표·툴팁 내용.

export type ChipTone = "neutral" | "info" | "warn" | "bad" | "ok";
const CHIP: Record<ChipTone, string> = {
  neutral: "bg-panel text-ink/80", info: "bg-infobg text-accent", warn: "bg-attnbg text-attn",
  bad: "bg-badbg text-bad", ok: "bg-okbg text-ok",
};

/** 작은 꼬리표(행 역할·열 요약). 점 없이 글자와 면 색만 -- k8s/parts 의 Tag 와 같은 모양을 코어에 따로 둔다. */
export function Chip({ tone = "neutral", title, children }: { tone?: ChipTone; title?: string; children: ReactNode }) {
  return (
    <span title={title}
          className={`inline-flex items-center whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-medium ${CHIP[tone]}`}>
      {children}
    </span>
  );
}

/** 상태 칩(아이콘 + 글자 + 면색). 색만으로 구분하지 않는다. */
export function StateChip({ Icon, label, tone }: { Icon: LucideIcon; label: string; tone: Tone }) {
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold ${
      TONE_CLASS[tone]}`}>
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
      {label}
    </span>
  );
}

const SUB = "text-[11px] leading-tight text-muted tabular-nums whitespace-nowrap";

type SpanProps = HTMLAttributes<HTMLSpanElement>;

/** 스토리지 칸: 칩 + 보조 줄(사용률·경로 바뀜·지연). 나머지 props(툴팁 바인딩·aria-label)는 바깥 span 에. */
export function MountCellChip({ cell, className = "", ...rest }: { cell: MountCellData } & SpanProps) {
  const sub = cellSub(cell);
  return (
    <span className={`inline-flex flex-col items-center gap-0.5 ${className}`} {...rest}>
      <StateChip Icon={cellIcon(cell)} label={cellLabel(cell)} tone={metaTone(cell)} />
      {sub !== null && <span className={SUB}>{sub}</span>}
    </span>
  );
}

const metaTone = (cell: MountCellData): Tone => CELL_META[cell.state].tone;

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

/** 문제 노드 꼬리표(행 머리의 노드 이름 옆 -- 표의 고정 첫 열이라 가로로 민 표에서도, 「문제 있는 노드」 필터를 켜지 않아도 보인다).
 *  수 = 서버 problem_count(마운트 문제 + 다른 노드와 다른 읽기 전용 + 공용 디렉터리 문제 -- 이 노드 DMS 파드의 경로 점검 실패도
 *  공용 디렉터리 한 칸으로 센다, 서버 mount_status_pods), 리포트 지연은 「지연」(지금 상태 모름 --
 *  null ≠ 0). 아이콘 + 글자(색만으로 구분하지 않는다). showStale=false 면 지연 행에는 그리지 않는다(같은 머리에 「리포트 지연」 칩이
 *  이미 있는 노드별 카드). */
export function ProblemTag({ row, showStale = true }: { row: MountRow; showStale?: boolean }) {
  if (!row.problem) return null;
  const count = row.problem_count;
  if (count === null && !showStale) return null;
  return (
    <span title={count === null ? "리포트 지연 — 지금 마운트 상태를 모릅니다"
                 : `문제 ${count}건 — 마운트 문제·다른 노드와 다른 읽기 전용·DMS 공용 디렉터리 문제(이 노드의 dms-api·컨트롤러가 `
                   + "경로를 못 읽는 것 포함)"}
          className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md bg-badbg px-1.5 py-0.5 text-[11px]
                     font-semibold text-bad">
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

/** 행 꼬리표: 배치 제외·cordon·일시 불가(노드 화면과 같은 배지) → control-plane → 역할 → 클러스터에 없음. 없으면 null.
 *  pods = 이 노드에 뜬 DMS 파드(mountTerms.podsOn) -- api_pod·controller_pod 꼬리표의 경고·문장 재료. */
export function NodeTags({ row, roleLabels, pods = [] }: {
  row: MountRow; roleLabels?: Record<string, string>; pods?: ControlPod[];
}) {
  const node = { report: { k8s_node: row.k8s_node }, exclusion: row.exclusion };
  const any = row.exclusion !== null || row.k8s_node !== null || row.control_plane === true || row.roles.length > 0
    || row.in_cluster === false;
  if (!any) return null;
  return (
    <span className="flex flex-wrap items-center gap-1">
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

/** 스토리지 칸 툴팁 내용. */
export function MountTipBody({ nodeName, col, cell, reportedAt, nowMs }: {
  nodeName: string; col: StorageColumn; cell: MountCellData; reportedAt: string; nowMs: number;
}) {
  const ago = agoText(reportedAt, nowMs);
  const usage = usageDetail(cell.disk);
  return (
    <div className="max-w-xs space-y-1.5">
      <div className="font-semibold [overflow-wrap:anywhere]">{`${nodeName} · ${col.storage_name}`}</div>
      <StateChip Icon={cellIcon(cell)} label={cellLabel(cell)} tone={metaTone(cell)} />
      <p>{cellHelp(cell, { mountPath: col.mount_path, ago })}</p>
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
export function SharedTipBody({ nodeName, path, cell, majority, reportedAt, nowMs, podFailed = false }: {
  nodeName: string; path: string; cell: SharedCellData; majority: SharedMajority | null; reportedAt: string; nowMs: number;
  podFailed?: boolean;
}) {
  const { meta, overridden } = sharedDisplay(cell, podFailed);
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
