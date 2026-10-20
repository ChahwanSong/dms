import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { Card } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import { Table } from "../../components/ui/Table";
import { useStorageTotals } from "./useUsage";
import { humanBytes } from "./usageStats";
import { storageLabel, type StorageTab } from "./usageTabs";
import type { UsageStorageTotals, UsageTotalMetric } from "../../lib/types";
import { FilesystemUse, fsShape, hotText, lowerBound, sumOf } from "./StorageTotals";

// 스토리지별 비교(2026-10-21 사용자 요청 「스토리지별 사용량 및 정보들의 총합」의 전체 개요). 머리말 아래 접힌 한 줄(기본 닫힘 --
// 매일 쓰는 탭·표를 밀어내지 않는다, 열림 상태는 기억하지 않는다). 열면 탭의 스토리지마다 합계를 부른다 -- 패널의 「이 스토리지
// 합계」와 **같은 쿼리 키**라 숫자 한 벌이고, 열기 전엔 요청이 없다(스토리지마다 서버가 대상 요약을 읽는다). 행을 누르면 그 탭.
//
// 스토리지끼리 더한 총계 행은 두지 않는다: 여러 스토리지가 한 파일시스템을 쓰거나(테스트베드 cephfs 셋이 같은 df) 관리 경로가
// 겹칠 수 있어 합이 이중 계산이 된다 -- 각주 한 줄이 그 이유를 말한다. 미등록 탭도 행이다(옛 기록의 합계 -- 탭과 같은 집합·순서).
//
// 375 폭: 표는 래퍼 안에서만 가로로 넘치고(Table overflow-x-auto) 이름 칸은 고정(sticky left-0, 불투명 바탕, z-index 없음 --
// TargetTable 머리 주석의 결정 그대로). 카드 목록을 따로 두지 않는다(두 벌 DOM -- Tabs 머리 주석).
//
// 부분 합은 숫자 옆에서 말한다(2026-10-21 리뷰: 1만 넘는 스토리지의 행이 「최근 스캔된 1만 개」만 더한 값을 전체처럼 보였고, 대상 칸은
// 창으로 잘린 10,000 이라 탭 배지(12,345)와 달랐다). 실 사용량·파일·디렉터리는 모름을 뺀 합(unknown > 0 -- 요약 읽는 중인 대상도
// 서버가 리포트 값 모름으로 센다)이거나 창을 넘어 잘린 합(truncated)이면 하한이라 「이상」, 표 각주가 두 뜻을 말한다. hot 은 비율이라
// 붙이지 않는다(패널과 같은 규칙). 대상 칸은 탭 배지와 같은 원천(요약 /storages 의 target_count -- 창으로 자르지 않은 수)이고,
// 요약이 없으면 합계의 수(잘렸으면 「+」)다.

const TH = "px-3 py-2 text-xs font-medium text-muted whitespace-nowrap";
const TD = "whitespace-nowrap px-3 py-2 text-right tabular-nums";
const SEP = "\u00a0· ";
const MORE = <span className="ml-1 text-[11px] text-muted">이상</span>;

const isCount = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x) && x >= 0;

// 대상 칸: 탭 배지와 같은 수(요약) -- 합계가 아직·실패여도 그 수는 안다. 요약이 없을 때만 합계의 수(창으로 잘렸으면 「+」).
function TargetsCell({ tab, t }: { tab: StorageTab; t: UsageStorageTotals | undefined }) {
  const truncated = t?.truncated === true;
  const fallback = isCount(t?.target_count) ? t.target_count : null;
  const n = tab.targetCount ?? fallback;
  const text = n === null ? "—" : `${n.toLocaleString("ko-KR")}${tab.targetCount === null && truncated ? "+" : ""}`;
  const title = [
    truncated && isCount(t?.window_limit) && `합계는 최근 스캔된 ${t.window_limit.toLocaleString("ko-KR")}개 대상 기준 부분 합`,
    isCount(t?.summed_target_count) && `최상위 ${t.summed_target_count.toLocaleString("ko-KR")}개 합계`,
  ].filter(Boolean).join(" · ");
  return <td className={`${TD} ${n === null ? "text-muted" : ""}`} title={title === "" ? undefined : title}>{text}</td>;
}

function CompareRow({ tab, selected, onSelect, now }: {
  tab: StorageTab; selected: boolean; onSelect: (name: string) => void; now: number;
}) {
  const q = useStorageTotals(tab.name);
  const t = q.data;
  const label = storageLabel(tab.name);
  const bg = selected ? "bg-infobg" : "bg-surface group-hover:bg-panel";
  const num = (n: number | null) => (n === null ? "—" : n.toLocaleString("ko-KR"));
  const cells = (() => {
    if (t === undefined) {
      return q.isError
        ? [<td key="e" className={`${TD} text-xs text-bad`}>불러오지 못함</td>,
           ...[0, 1, 2].map((i) => <td key={i} className={`${TD} text-muted`}>—</td>),
           <td key="fs" className="px-3 py-2 text-muted">—</td>]
        : [0, 1, 2, 3, 4].map((i) => <td key={i} className="px-3 py-2"><Skeleton className="ml-auto h-4 w-14" /></td>);
    }
    const bytes = sumOf(t.total_bytes), files = sumOf(t.total_files), dirs = sumOf(t.total_directories);
    const fs = fsShape(t.filesystem);
    const partial = (m: UsageTotalMetric | null | undefined, sum: number | null) =>
      sum !== null && (t.truncated === true || lowerBound(m, sum));
    return [
      <td key="b" className={TD} title={bytes === null ? undefined : `${bytes.toLocaleString("ko-KR")} B`}>
        {bytes === null ? "—" : humanBytes(bytes)}{partial(t.total_bytes, bytes) && MORE}</td>,
      <td key="f" className={TD}>{num(files)}{partial(t.total_files, files) && MORE}</td>,
      <td key="d" className={TD}>{num(dirs)}{partial(t.total_directories, dirs) && MORE}</td>,
      <td key="h" className={TD}>{hotText(t)}</td>,
      <td key="fs" className="whitespace-nowrap px-3 py-2">
        {fs === null ? <span className="text-muted">—</span> : <FilesystemUse fs={fs} now={now} compact />}</td>,
    ];
  })();
  return (
    // 행 전체가 누르는 자리(마우스), 키보드는 이름 버튼 -- 버튼 클릭이 행으로 올라가 두 번 고르지 않게 멈춘다.
    <tr className={`group cursor-pointer border-t border-line ${selected ? "bg-infobg" : "hover:bg-panel"}`}
        onClick={() => onSelect(tab.name)}>
      <td className={`sticky left-0 py-2 pl-2 pr-3 ${bg}`}>
        <span className="inline-flex max-w-[11rem] items-center gap-1.5 whitespace-nowrap sm:max-w-[16rem]">
          <button type="button" aria-label={`${label} 탭 열기`} aria-current={selected ? "true" : undefined}
                  onClick={(e) => { e.stopPropagation(); onSelect(tab.name); }}
                  className={`min-w-0 truncate rounded text-left hover:underline focus-visible:outline-none
                              focus-visible:ring-2 focus-visible:ring-accent ${selected ? "font-medium text-ink" : "text-accent"}
                              ${tab.name === "" ? "italic" : ""}`}>
            {label}
          </button>
          {!tab.registered && (
            <span className="shrink-0 rounded border border-attn/30 bg-attnbg px-1 text-[11px] leading-4 text-attn">미등록</span>
          )}
        </span>
      </td>
      {cells}
      <TargetsCell tab={tab} t={t} />
    </tr>
  );
}

function CompareTable({ tabs, active, onSelect, now, windowLimit }: {
  tabs: StorageTab[]; active: string | null; onSelect: (name: string) => void; now: number; windowLimit: number | null;
}) {
  const windowText = windowLimit === null ? "창 안의" : `${windowLimit.toLocaleString("ko-KR")}개`;
  return (
    <div className="space-y-3">
      <Table>
        <thead><tr>
          <th className={`${TH} sticky left-0 bg-surface pl-2`}>스토리지</th>
          <th className={`${TH} text-right`}>실 사용량</th>
          <th className={`${TH} text-right`}>파일</th>
          <th className={`${TH} text-right`}>디렉터리</th>
          <th className={`${TH} text-right`}>hot 비율</th>
          <th className={TH}>파일시스템(df)</th>
          <th className={`${TH} text-right`}>대상</th>
        </tr></thead>
        <tbody>
          {tabs.map((t) => (
            <CompareRow key={t.name} tab={t} selected={t.name === active} onSelect={onSelect} now={now} />
          ))}
        </tbody>
      </Table>
      <p className="text-xs text-muted break-keep">
        스토리지마다 최상위 대상의 최근 성공 scan 합계{SEP}「이상」 = 값을 모르는 대상(요약 읽는 중 포함)을 뺀 합이거나 대상이 많아
        최근 스캔된 {windowText} 대상만 더한 부분 합{SEP}df = 노드가 잰 파일시스템 전체 사용량(* 노드마다 다르거나 오래된
        보고){SEP}스토리지끼리는 더하지 않습니다(한 파일시스템 공유·경로 중첩 가능)
      </p>
    </div>
  );
}

export function UsageOverview({ tabs, active, onSelect, now, windowLimit }: {
  tabs: StorageTab[]; active: string | null; onSelect: (name: string) => void; now: number;
  windowLimit: number | null;     // 요약(/storages)의 window_limit -- 각주의 창 크기(서버 값, 하드코딩하지 않는다)
}) {
  const [open, setOpen] = useState(false);
  // 스토리지가 하나뿐이면 비교할 것이 없다(패널 합계와 같은 숫자의 반복).
  if (tabs.length < 2) return null;
  return (
    <details className="group/ov" onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded text-sm font-medium text-ink
                          hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent
                          [&::-webkit-details-marker]:hidden">
        <ChevronRight aria-hidden className="h-4 w-4 text-muted transition-transform group-open/ov:rotate-90" />
        스토리지별 비교
      </summary>
      {/* 열려 있을 때만 그린다 -- 닫힌 동안 스토리지마다의 합계 요청이 나가지 않게. */}
      {open && (
        <Card className="mt-3">
          <CompareTable tabs={tabs} active={active} onSelect={onSelect} now={now} windowLimit={windowLimit} />
        </Card>
      )}
    </details>
  );
}
