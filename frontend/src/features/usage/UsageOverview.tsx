import { Fragment, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQueries, type UseQueryResult } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { Skeleton } from "../../components/ui/Skeleton";
import { Table } from "../../components/ui/Table";
import { agoText, ageDays, kstStampOrDash } from "../../lib/datetime";
import { storageTotalsOptions } from "./useUsage";
import { humanBytes } from "./usageStats";
import { storageLabel, type StorageTab } from "./usageTabs";
import type { UsageStorageTotals } from "../../lib/types";
import { FilesystemUse, fsShape, hotText, partialSum, sumOf } from "./StorageTotals";
import { staleClass } from "./TargetTable";

// 스토리지별 비교 표(2026-10-21 사용자 요청 「스토리지별 사용량 및 정보들의 총합」의 전체 개요 → 2026-10-29 「스토리지별」 탭의 본문,
// StoragesPage). 예전엔 경로별 머리말 아래 접힌 한 줄(<details>)이었다 -- 이제 탭을 열 때 그린다(요청 시점이 「펼칠 때」에서 「탭을
// 열 때」로 바뀌었을 뿐 비용은 같다). 스토리지마다 합계를 부른다 -- 경로별 합계 줄과 **같은 쿼리 키**라 숫자 한 벌이고(staleTime
// 30s -- 경로별과 오가도 다시 부르지 않는다), 열 정렬 때문에 표가 한 번에 부른다(useQueries -- 행마다 부르면 표가 값을 모른다).
//
// 열: 스토리지(고정 칸 -- 이름 링크 · 흐린 타입 · 미등록 배지) · 실 사용량 · 파일 · 디렉터리 · hot 비율 · 파일시스템(df, 글자 + 짧은
// 막대 + %) · 대상 · 최근 스캔. 이름(또는 행 아무 곳)을 누르면 그 스토리지의 경로별(/admin/usage?storage=X, push). 경로별에서 마지막으로
// 본 스토리지 행(current -- usageMemory)은 bg-infobg + 링크 aria-current.
// 정렬(페이지 상태 -- URL 에 싣지 않는다): 실 사용량 · df 사용률 · 대상 · 최근 스캔 머리. 기본은 등록 이름순 → 구분선 → 미등록
// 이름순(예전 탭과 같은 순서 -- 「스토리지」 머리가 그 순서로 되돌린다). 정렬 중엔 구분선 없이 미등록 배지로만 구별한다. 값을 모르는
// 행(읽는 중·실패·기록 없음·df 없음)은 방향과 무관하게 맨 뒤(경로 표의 「값 모름(—)은 맨 뒤」와 같은 규칙).
//
// 스토리지끼리 더한 총계 행은 두지 않는다: 여러 스토리지가 한 파일시스템을 쓰거나(테스트베드 cephfs 셋이 같은 df) 관리 경로가
// 겹칠 수 있어 합이 이중 계산이 된다 -- 각주 한 줄이 그 이유를 말한다. 미등록 스토리지도 행이다(옛 기록의 합계).
//
// 375 폭: 표는 래퍼 안에서만 가로로 넘치고(Table overflow-x-auto) 이름 칸은 고정(sticky left-0, 불투명 바탕, z-index 없음 --
// TargetTable 머리 주석의 결정 그대로). 카드 목록을 따로 두지 않는다(두 벌 DOM).
//
// 부분 합은 숫자 옆에서 말한다(2026-10-21 리뷰: 1만 넘는 스토리지의 행이 「최근 스캔된 1만 개」만 더한 값을 전체처럼 보였고, 대상 칸은
// 창으로 잘린 10,000 이라 탭 배지(12,345)와 달랐다). 실 사용량·파일·디렉터리는 모름을 뺀 합(unknown > 0 -- 요약 읽는 중인 대상도
// 서버가 리포트 값 모름으로 센다)이거나 창을 넘어 잘린 합(truncated)이면 하한이라 「이상」, 표 각주가 두 뜻을 말한다. hot 은 비율이라
// 붙이지 않는다(합계 줄과 같은 규칙). 대상 칸은 select 옵션과 같은 원천(요약 /storages 의 target_count -- 창으로 자르지 않은 수)이고,
// 요약이 없으면 합계의 수(잘렸으면 「+」)다.

const TH = "px-3 py-2 text-xs font-medium text-muted whitespace-nowrap";
const TD = "whitespace-nowrap px-3 py-2 text-right tabular-nums";
const SEP = " · ";
const MORE = <span className="ml-1 text-[11px] text-muted">이상</span>;
const COLS = 8;

const isCount = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x) && x >= 0;

export type CompareSortKey = "bytes" | "df" | "targets" | "last_scan";
export type CompareSort = { key: CompareSortKey; order: "desc" | "asc" } | null;
const SORT_WORDS: Record<CompareSortKey, { desc: string; asc: string }> = {
  bytes: { desc: "큰 순", asc: "작은 순" },
  df: { desc: "사용률 높은 순", asc: "사용률 낮은 순" },
  targets: { desc: "많은 순", asc: "적은 순" },
  last_scan: { desc: "최신순", asc: "오래된순" },
};

interface RowModel {
  tab: StorageTab; q: UseQueryResult<UsageStorageTotals>; index: number;
  bytes: number | null; dfPct: number | null; targets: number | null; last: string | null;
}

function rowModel(tab: StorageTab, q: UseQueryResult<UsageStorageTotals>, index: number): RowModel {
  const t = q.data;
  const fs = fsShape(t?.filesystem);
  return {
    tab, q, index,
    bytes: t === undefined ? null : sumOf(t.total_bytes),
    dfPct: fs === null ? null : fs.used_bytes / fs.total_bytes,
    // 대상 칸: select 옵션과 같은 수(요약) -- 합계가 아직·실패여도 그 수는 안다. 요약이 없을 때만 합계의 수.
    targets: tab.targetCount ?? (isCount(t?.target_count) ? t.target_count : null),
    last: tab.summary?.last_scan_at ?? tab.unregistered?.last_scan_at ?? null,
  };
}

function sortValue(r: RowModel, key: CompareSortKey): number | null {
  if (key === "bytes") return r.bytes;
  if (key === "df") return r.dfPct;
  if (key === "targets") return r.targets;
  const e = r.last === null ? NaN : Date.parse(r.last);
  return Number.isFinite(e) ? e : null;
}

function sortRows(rows: RowModel[], sort: CompareSort): RowModel[] {
  if (sort === null) return rows;
  return [...rows].sort((a, b) => {
    const x = sortValue(a, sort.key), y = sortValue(b, sort.key);
    if (x === null || y === null) return x === null && y === null ? a.index - b.index : x === null ? 1 : -1;
    return (sort.order === "asc" ? x - y : y - x) || a.index - b.index;
  });
}

function SortTh({ label, col, sort, onSort, align = "right" }: {
  label: string; col: CompareSortKey; sort: CompareSort; onSort: (c: CompareSortKey) => void; align?: "left" | "right";
}) {
  const active = sort !== null && sort.key === col;
  return (
    <th className={`${TH} ${align === "right" ? "text-right" : ""}`}
        aria-sort={active ? (sort.order === "desc" ? "descending" : "ascending") : undefined}>
      <button type="button" onClick={() => onSort(col)}
              aria-label={active ? `${label} 정렬: ${SORT_WORDS[col][sort.order]}` : `${label} 정렬`}
              className={`inline-flex items-center gap-1 rounded hover:text-ink focus-visible:outline-none focus-visible:ring-2
                          focus-visible:ring-accent ${active ? "text-ink" : ""}`}>
        {label}
        {active
          ? (sort.order === "desc" ? <ArrowDown aria-hidden className="h-3.5 w-3.5" /> : <ArrowUp aria-hidden className="h-3.5 w-3.5" />)
          // 흐림은 색의 알파로(opacity 는 쌓임 맥락을 만들어 고정 칸 위로 올라온다 -- TargetTable 머리 주석).
          : <ArrowUpDown aria-hidden className="h-3.5 w-3.5 text-muted/50" />}
      </button>
    </th>
  );
}

// 대상 칸: 요약의 수(창으로 자르지 않은 수), 요약이 없으면 합계의 수(창으로 잘렸으면 「+」). 0 은 흐리게(기록 없음 -- 진짜 0).
function TargetsCell({ tab, t }: { tab: StorageTab; t: UsageStorageTotals | undefined }) {
  const truncated = t?.truncated === true;
  const fallback = isCount(t?.target_count) ? t.target_count : null;
  const n = tab.targetCount ?? fallback;
  const text = n === null ? "—" : `${n.toLocaleString("ko-KR")}${tab.targetCount === null && truncated ? "+" : ""}`;
  const title = [
    truncated && isCount(t?.window_limit) && `합계는 최근 스캔된 ${t.window_limit.toLocaleString("ko-KR")}개 대상 기준 부분 합`,
    isCount(t?.summed_target_count) && `최상위 ${t.summed_target_count.toLocaleString("ko-KR")}개 합계`,
  ].filter(Boolean).join(" · ");
  return <td className={`${TD} ${n === null || n === 0 ? "text-muted" : ""}`} title={title === "" ? undefined : title}>{text}</td>;
}

function CompareRow({ r, current, now }: { r: RowModel; current: boolean; now: number }) {
  const { tab, q } = r;
  const navigate = useNavigate();
  const t = q.data;
  const label = storageLabel(tab.name);
  const href = `/admin/usage?${new URLSearchParams({ storage: tab.name }).toString()}`;
  const type = tab.summary?.backend_type ?? tab.info?.backend_type ?? null;
  const bg = current ? "bg-infobg" : "bg-surface group-hover:bg-panel";
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
    const none = isCount(t.target_count) && t.target_count === 0;
    return [
      <td key="b" className={`${TD} ${bytes === null ? "text-muted" : ""}`}
          title={bytes !== null ? `${bytes.toLocaleString("ko-KR")} B` : none ? "아직 성공한 scan 없음" : undefined}>
        {bytes === null ? "—" : humanBytes(bytes)}{partialSum(t, t.total_bytes, bytes) && MORE}</td>,
      <td key="f" className={TD}>{num(files)}{partialSum(t, t.total_files, files) && MORE}</td>,
      <td key="d" className={TD}>{num(dirs)}{partialSum(t, t.total_directories, dirs) && MORE}</td>,
      <td key="h" className={TD}>{hotText(t)}</td>,
      <td key="fs" className="whitespace-nowrap px-3 py-2">
        {fs === null ? <span className="text-muted">—</span> : <FilesystemUse fs={fs} now={now} compact />}</td>,
    ];
  })();
  return (
    // 행 전체가 누르는 자리(마우스), 키보드는 이름 링크 -- 링크 클릭이 행으로 올라가 두 번 이동하지 않게 멈춘다.
    <tr className={`group cursor-pointer border-t border-line ${current ? "bg-infobg" : "hover:bg-panel"}`}
        onClick={() => navigate(href)}>
      <td className={`sticky left-0 py-2 pl-2 pr-3 ${bg}`}>
        <span className="inline-flex max-w-[12rem] items-center gap-1.5 whitespace-nowrap sm:max-w-[18rem]">
          <Link to={href} title={label} aria-current={current ? "true" : undefined} onClick={(e) => e.stopPropagation()}
                className={`min-w-0 truncate rounded text-accent hover:underline focus-visible:outline-none focus-visible:ring-2
                            focus-visible:ring-accent ${current ? "font-medium" : ""} ${tab.name === "" ? "italic" : ""}`}>
            {label}
          </Link>
          {type !== null && <span className="shrink-0 text-xs text-muted">{type}</span>}
          {!tab.registered && (
            <span className="shrink-0 rounded border border-attn/30 bg-attnbg px-1 text-[11px] leading-4 text-attn">미등록</span>
          )}
        </span>
      </td>
      {cells}
      <TargetsCell tab={tab} t={t} />
      <td className="whitespace-nowrap px-3 py-2" title={r.last === null ? undefined : kstStampOrDash(r.last)}>
        {r.last === null ? <span className="text-muted">—</span>
          : <span className={staleClass(ageDays(r.last, now))}>{agoText(r.last, now)}</span>}
      </td>
    </tr>
  );
}

export function CompareTable({ tabs, current, now, windowLimit }: {
  tabs: StorageTab[];
  current: string | null;          // 경로별에서 마지막으로 본 스토리지(usageMemory) -- 모르면 null
  now: number;
  windowLimit: number | null;      // 요약(/storages)의 window_limit -- 각주의 창 크기(서버 값, 하드코딩하지 않는다)
}) {
  const [sort, setSort] = useState<CompareSort>(null);
  const qs = useQueries({ queries: tabs.map((t) => storageTotalsOptions(t.name)) });
  const rows = sortRows(tabs.map((t, i) => rowModel(t, qs[i], i)), sort);
  const onSort = (key: CompareSortKey) =>
    setSort((s) => (s !== null && s.key === key ? { key, order: s.order === "desc" ? "asc" : "desc" } : { key, order: "desc" }));
  const windowText = windowLimit === null ? "창 안의" : `${windowLimit.toLocaleString("ko-KR")}개`;
  return (
    <div className="space-y-3">
      <Table framed>
        <thead><tr>
          <th className={`${TH} sticky left-0 bg-surface pl-2`}>
            {/* 기본 순서(등록 이름순 → 미등록 이름순)로 되돌리는 머리 -- 정렬 중일 때만 누를 거리가 있다. 아이콘은 SortTh 문법
                (2026-10-29 2차 리뷰 -- 글자색만으로는 정렬 안 되는 머리(파일·디렉터리)와 구별되지 않았다): 기본 순서면 지금 기준(이름
                오름차순 ↑), 다른 열로 정렬 중이면 흐린 ↕(눌러 되돌릴 수 있다). */}
            <button type="button" onClick={() => setSort(null)}
                    aria-label={sort === null ? "스토리지 정렬: 이름순(등록 → 미등록)" : "스토리지 정렬"}
                    className={`inline-flex items-center gap-1 rounded hover:text-ink focus-visible:outline-none
                                focus-visible:ring-2 focus-visible:ring-accent ${sort === null ? "text-ink" : ""}`}>
              스토리지
              {sort === null
                ? <ArrowUp aria-hidden className="h-3.5 w-3.5" />
                : <ArrowUpDown aria-hidden className="h-3.5 w-3.5 text-muted/50" />}
            </button>
          </th>
          <SortTh label="실 사용량" col="bytes" sort={sort} onSort={onSort} />
          <th className={`${TH} text-right`}>파일</th>
          <th className={`${TH} text-right`}>디렉터리</th>
          <th className={`${TH} text-right`}>hot 비율</th>
          <SortTh label="파일시스템(df)" col="df" sort={sort} onSort={onSort} align="left" />
          <SortTh label="대상" col="targets" sort={sort} onSort={onSort} />
          <SortTh label="최근 스캔" col="last_scan" sort={sort} onSort={onSort} align="left" />
        </tr></thead>
        <tbody>
          {rows.map((r, i) => {
            // 기본 순서의 등록 | 미등록 경계: 구분선 한 줄(묶음 이름은 select 의 optgroup 과 같다). 보조기기엔 숨긴다 -- 행마다의
            // 「미등록」 배지가 같은 것을 말하고, 행 수가 스토리지 수와 같게.
            const boundary = sort === null && !r.tab.registered && i > 0 && rows[i - 1].tab.registered;
            return (
              <Fragment key={r.tab.name}>
                {boundary && (
                  <tr aria-hidden className="border-t border-line bg-panel">
                    <td colSpan={COLS} className="px-2 py-1 text-xs text-muted">
                      <span className="sticky left-2">미등록 — 기록만 남음</span></td>
                  </tr>
                )}
                <CompareRow r={r} current={r.tab.name === current} now={now} />
              </Fragment>
            );
          })}
        </tbody>
      </Table>
      <p className="text-xs text-muted break-keep">
        스토리지마다 최상위 대상의 최근 성공 scan 합계{SEP}스토리지끼리는 더하지 않습니다 — 여러 스토리지가 한 파일시스템을
        쓰거나(같은 df) 관리 경로가 겹칠 수 있습니다{SEP}「이상」 = 값을 모르는 대상(요약 읽는 중 포함)을 뺀 합이거나 대상이 많아
        최근 스캔된 {windowText} 대상만 더한 부분 합{SEP}df = 노드가 잰 파일시스템 전체 사용량(* 노드마다 다르거나 오래된 보고)
        {/* 「최근 스캔」 칸의 경과 색 범례(글자색이 곧 범례 -- 경로별 각주와 같은 조각, 2026-10-29 2차 리뷰) */}
        {SEP}최근 스캔 <span className="font-medium text-attn">30일↑</span>{SEP}<span className="font-medium text-bad">90일↑</span>
      </p>
    </div>
  );
}
