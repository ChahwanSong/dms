import { Fragment, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronRight } from "lucide-react";
import { Table } from "../../components/ui/Table";
import { HelpTip } from "../../components/ui/HelpTip";
import type { ScanTargetRow } from "../../lib/types";
import { agoText, ageDays, kstStampOrDash } from "../../lib/datetime";
import type { UsageSort } from "./useUsage";
import { HOT_AGE_MAX_DAYS, hotRatio, humanBytes } from "./usageStats";
import { TargetDetail } from "./TargetDetail";

// 한 스토리지의 타깃 표(2026-10-20 UsageAnalysis 에서 분리). 6열 -- 「스토리지」 열과 행의 「미등록」 배지는 없앴다: 탭이 그
// 정보를 말한다(옛 화면은 모든 행에 같은 스토리지 이름을 되풀이했다).
//
// 375 폭: 표는 래퍼 안에서 가로로 넘치고(Table 의 overflow-x-auto), 경로 열은 고정(sticky left-0)이라 숫자 열을 넘겨도 어느
// 경로의 값인지 보인다. 고정 칸·선택 행 바탕은 **불투명** 토큰이다(bg-surface·bg-infobg) -- 반투명이면 고정 칸 뒤로 지나가는
// 숫자가 비친다. 펼친 상세는 표 폭(771px)이 아니라 래퍼의 **보이는 폭**(clientWidth, ResizeObserver)에 맞춘 고정 칸이다:
// 예전엔 colSpan 셀을 따라 표 폭이 되어, 375 에서 요약 4칸 중 2칸·차트 오른쪽 절반을 보려면 표 전체를 가로로 밀어야 했다.
// 셀을 display:none 으로 숨겨 좁히지 않는다(e2e L2 -- 셀은 table-cell 이어야 한다). 반응형 숨김은 셀 안 span 에만.
// 고정 칸에 z-index 를 주지 않는다: 위치 지정(sticky)만으로 흐름 속 숫자 칸 위에 그려지고, z-index 를 주면 z 없는 확인 창
// (Dialog 의 fixed 오버레이)보다 위로 올라와 창을 뚫고 보였다(실 Chrome 확인).

export const DETAIL_ID = "usage-target-detail";

// 최근 스캔 경과 색(2026-10-02 사용자 결정): 30일 지나면 주의색(attn), 90일 지나면 빨강. 모름은 기본색.
export function staleClass(days: number | null): string {
  if (days === null) return "text-muted";
  if (days >= 90) return "text-bad font-medium";
  if (days >= 30) return "text-attn font-medium";
  return "text-ink";
}

// 정렬 머리글(2026-10-19). 방향 이름은 열 뜻으로(크기·개수·비율·시각) -- 「오름차순」보다 무엇이 위인지 바로 읽힌다.
// aria-sort 는 지금 정렬 중인 열에만(나머지는 생략 -- 보조기기가 「정렬 안 됨」 열을 하나하나 읽지 않게).
const SORT_WORDS: Record<UsageSort, { desc: string; asc: string }> = {
  bytes: { desc: "큰 순", asc: "작은 순" },
  files: { desc: "많은 순", asc: "적은 순" },
  hot: { desc: "높은 순", asc: "낮은 순" },
  scans: { desc: "많은 순", asc: "적은 순" },
  last_scan: { desc: "최신순", asc: "오래된순" },
};

const TH = "px-3 py-2 text-xs font-medium text-muted whitespace-nowrap";

function SortHeader({ col, label, sort, order, onSort, align = "right", help }: {
  col: UsageSort; label: string; sort: UsageSort; order: "desc" | "asc"; onSort: (c: UsageSort) => void;
  align?: "left" | "right"; help?: string;
}) {
  const active = sort === col;
  return (
    <th className={`${TH} ${align === "right" ? "text-right" : "pl-6"}`}
        aria-sort={active ? (order === "desc" ? "descending" : "ascending") : undefined}>
      <span className="inline-flex items-center gap-0.5">
        <button type="button" onClick={() => onSort(col)}
                aria-label={active ? `${label} 정렬: ${SORT_WORDS[col][order]}` : `${label} 정렬`}
                className={`inline-flex items-center gap-1 rounded hover:text-ink focus-visible:outline-none
                            focus-visible:ring-2 focus-visible:ring-accent ${active ? "text-ink" : ""}`}>
          {label}
          {active
            ? (order === "desc" ? <ArrowDown aria-hidden className="h-3.5 w-3.5" />
                                : <ArrowUp aria-hidden className="h-3.5 w-3.5" />)
            : <ArrowUpDown aria-hidden className="h-3.5 w-3.5 opacity-40" />}
        </button>
        {help && <HelpTip label={`${label} 설명`} head={label} help={help} className="-my-1" />}
      </span>
    </th>
  );
}

// 래퍼의 보이는 폭(clientWidth)과 가로 스크롤 여부. jsdom(ResizeObserver 없음·폭 0)에선 null -- 상세는 셀 폭 그대로.
function useWrapMetrics() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState<number | null>(null);
  const [scrolled, setScrolled] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setWidth(el.clientWidth > 0 ? el.clientWidth : null);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onScroll = () => setScrolled(el.scrollLeft > 0);
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);
  return { ref, width, scrolled };
}

export function TargetTable({ storage, rows, activeTarget, onToggle, sort, order, onSort, now }: {
  storage: string; rows: ScanTargetRow[]; activeTarget: string | null; onToggle: (target: string) => void;
  sort: UsageSort; order: "desc" | "asc"; onSort: (c: UsageSort) => void; now: number;
}) {
  const wrap = useWrapMetrics();
  // 고정 경로 칸의 오른쪽 그늘은 표를 옆으로 밀었을 때만 -- 넘치지 않는 넓은 화면에선 칸 사이에 선이 없다.
  const stickyEdge = wrap.scrolled ? "shadow-[1px_0_0_theme(colors.line)]" : "";
  return (
    <Table wrapRef={wrap.ref}>
      <thead><tr>
        <th className={`${TH} sticky left-0 bg-surface pl-2 ${stickyEdge}`}>경로</th>
        {/* 2026-10-02 목록 컬럼: 최신 성공 scan 기준(서버 latest). 모름은 "—"(null≠0). 2026-10-19: 머리글이 정렬
            버튼(서버 정렬 -- 받은 100개만 정렬하지 않는다). */}
        <SortHeader col="bytes" label="실 사용량" sort={sort} order={order} onSort={onSort} />
        <SortHeader col="files" label="파일 수" sort={sort} order={order} onSort={onSort} />
        <SortHeader col="hot" label="hot 비율" sort={sort} order={order} onSort={onSort}
                    help={`atime 기준 ${HOT_AGE_MAX_DAYS}일 이내 용량 비중 · 최근 성공 scan`} />
        <SortHeader col="scans" label="스캔 횟수" sort={sort} order={order} onSort={onSort} />
        {/* pl-6: 바로 왼쪽 「스캔 횟수」가 오른쪽 정렬이라 여백 없이 붙으면 "4"+"1시간 전"이 "41시간 전"으로
            읽혔다(실 Chrome 확인 d156). 서버가 정렬하므로 limit 밖의 오래된 타깃도 오름차순에서 보인다. */}
        <SortHeader col="last_scan" label="최근 스캔" sort={sort} order={order} onSort={onSort} align="left" />
      </tr></thead>
      <tbody>
        {rows.map((r) => {
          const active = r.target === activeTarget;
          const p = r.latest ?? null;
          const files = p?.summary?.["total_files"];
          const hot = hotRatio(p?.time_histograms?.["atime"]);
          const age = ageDays(r.last_scan_at, now);
          const bg = active ? "bg-infobg" : "bg-surface";
          return (
            <Fragment key={r.target}>
              <tr className={`border-t border-line ${bg}`}>
                {/* 선택은 버튼이다 -- 행 onClick 만 두면 키보드로 못 고른다. aria-expanded/controls 로 "펼침 버튼"임을
                    보조기기에 알린다. 경로는 한 줄(whitespace-nowrap, 2026-10-12 -- 좁은 폭에서 하이픈마다 쪼개졌다, e2e
                    E10 L4)이고, 좁은 폭에선 칸 상한까지만 보이고 말줄임(전체는 title·버튼 이름·상세 이름에). */}
                <td className={`sticky left-0 py-2 pl-2 pr-3 ${bg} ${stickyEdge}`}>
                  <button type="button"
                          aria-expanded={active}
                          aria-controls={active ? DETAIL_ID : undefined}
                          title={r.target}
                          className={`inline-flex max-w-[11rem] items-center gap-1 whitespace-nowrap rounded text-left
                                      text-accent hover:underline focus-visible:outline-none focus-visible:ring-2
                                      focus-visible:ring-accent sm:max-w-[22rem] lg:max-w-[36rem] ${
                                        active ? "font-medium" : ""}`}
                          onClick={() => onToggle(r.target)}>
                    <ChevronRight aria-hidden="true"
                                  className={`h-3.5 w-3.5 shrink-0 transition-transform ${active ? "rotate-90" : ""}`} />
                    <span className="min-w-0 truncate">{r.target}</span>
                  </button>
                </td>
                <td className="whitespace-nowrap px-3 text-right tabular-nums"
                    title={typeof p?.total_bytes === "number"
                      ? `${p.total_bytes.toLocaleString("ko-KR")} B` : undefined}>
                  {typeof p?.total_bytes === "number" ? humanBytes(p.total_bytes) : "—"}
                  {/* 리포트를 못 읽은 최신 스캔: 용량은 러너가 DB 에 남긴 값(같은 규칙)이고 파일 수·hot 은 모름이다.
                      펼친 상세의 "최신" 수치는 읽을 수 있는 리포트 기준이라 다를 수 있다 -- 그 사실을 표시한다(리뷰). */}
                  {p?.report_readable === false && typeof p.total_bytes === "number" && (
                    <span className="ml-1 text-[10px] text-muted"
                          title="최신 스캔 리포트를 읽지 못해 잡 기록(DB)의 용량을 표시합니다">(DB)</span>
                  )}
                </td>
                <td className="whitespace-nowrap px-3 text-right tabular-nums">
                  {typeof files === "number" ? files.toLocaleString("ko-KR") : "—"}
                </td>
                <td className="whitespace-nowrap px-3 text-right tabular-nums">
                  {hot === null ? "—" : `${Math.round(hot * 100)}%`}
                </td>
                <td className="px-3 text-right tabular-nums">{r.scan_count}</td>
                {/* 경과(2026-10-02): 지금부터 얼마나 전인지 + 30일 주의색 · 90일 빨강. 정확한 시각은 넓은 화면(xl)에서만 옆에
                    -- 좁은 화면에선 가장 넓은 열이 됐다(title 로는 늘 볼 수 있다). */}
                <td className="whitespace-nowrap pl-6 pr-3" title={kstStampOrDash(r.last_scan_at)}>
                  <span className={staleClass(age)} aria-label="최근 스캔 경과">
                    {agoText(r.last_scan_at, now)}</span>
                  <span className="ml-2 hidden text-xs text-muted xl:inline">{kstStampOrDash(r.last_scan_at)}</span>
                </td>
              </tr>
              {active && (
                // 펼침 행: 선택 행 바로 아래. 안쪽 칸은 보이는 폭에 고정(sticky left-0 + 래퍼 폭) -- 위 머리 주석.
                // 왼쪽 강조선이 "이 행에 속한 상세"임을 묶는다.
                <tr>
                  <td colSpan={6} className="p-0">
                    <div id={DETAIL_ID} role="region" aria-label={`${storage}:${r.target} 상세`}
                         className="sticky left-0 border-l-2 border-accent bg-surface px-4 pb-5 pt-4"
                         style={wrap.width !== null ? { width: wrap.width } : undefined}>
                      <TargetDetail storage={storage} target={r.target} showTitle={false} />
                    </div>
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </Table>
  );
}
