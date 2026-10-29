import { useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { Table } from "../../components/ui/Table";
import { HelpTip } from "../../components/ui/HelpTip";
import type { ScanTargetRow } from "../../lib/types";
import { agoText, ageDays, kstStampOrDash } from "../../lib/datetime";
import type { UsageSort } from "./useUsage";
import { ATIME_READ_RULE, HOT_AGE_MAX_DAYS, hotRatio, humanBytes, usagePvOf } from "./usageStats";
import { PvLine } from "./UsageParts";

// 한 스토리지의 타깃 표(2026-10-20 UsageAnalysis 에서 분리). 6열 -- 「스토리지」 열과 행의 「미등록」 배지는 없앴다: 스토리지
// select·경고 띠가 그 정보를 말한다(옛 화면은 모든 행에 같은 스토리지 이름을 되풀이했다). 정렬은 URL(sort·order)이다.
//
// 행 = 상세 시트 열기(2026-10-29 -- 예전의 행 아래 colSpan 펼침 행 대신 오른쪽 시트 TargetSheet). 경로 버튼이 키보드 자리이고
// 마우스는 행 아무 곳이나 같다(CompareRow 관용 -- 버튼 클릭은 행으로 올라가 두 번 열지 않게 멈춘다). 시트가 연 행은 bg-infobg.
// 시트를 닫으면 포커스가 그 행의 경로 버튼으로 돌아온다 -- 버튼은 buttonRefs(경로 → 버튼)로 패널에 알린다.
//
// 375 폭: 표는 래퍼 안에서 가로로 넘치고(Table 의 overflow-x-auto), 경로 열은 고정(sticky left-0)이라 숫자 열을 넘겨도 어느
// 경로의 값인지 보인다. 고정 칸·선택 행 바탕은 **불투명** 토큰이다(bg-surface·bg-infobg·bg-panel) -- 반투명이면 고정 칸 뒤로
// 지나가는 숫자가 비친다. 셀을 display:none 으로 숨겨 좁히지 않는다(e2e L2 -- 셀은 table-cell 이어야 한다). 반응형 숨김은 셀
// 안 span 에만.
// 고정 칸에 z-index 를 주지 않는다: 위치 지정(sticky)만으로 흐름 속 숫자 칸 위에 그려지고, z-index 를 주면 z 없는 확인 창
// (Dialog 의 fixed 오버레이)보다 위로 올라와 창을 뚫고 보였다(실 Chrome 확인). 그래서 숫자 칸 쪽에 쌓임 맥락(opacity·transform
// 등)을 만들지 않는다 -- 만들면 그 칸이 DOM 순서상 뒤라 고정 칸 위로 올라온다(흐린 정렬 아이콘을 opacity 로 흐렸을 때 밀린
// 열의 ⇅ 가 경로 머리 위에 보였다, 리뷰). 흐림은 색의 알파로.
//
// 넓은 화면(lg+)에선 경로 칸이 **남는 폭만** 가진다(w-full + max-w-0 -- 표 칸 말줄임의 표준 기법): 긴 경로 하나가 칸 상한까지
// 표를 넓혀 기본 정렬 열(최근 스캔)을 화면 밖으로 밀었다(리뷰, 1280 실측). 좁은 화면은 그대로 칸 상한 + 가로 스크롤.

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
            : <ArrowUpDown aria-hidden className="h-3.5 w-3.5 text-muted/50" />}
        </button>
        {help && <HelpTip label={`${label} 설명`} head={label} help={help} className="-my-1" />}
      </span>
    </th>
  );
}

// 래퍼의 가로 스크롤 여부(고정 경로 칸의 오른쪽 그늘). 2026-10-29 펼침 행이 시트로 옮겨 가며 래퍼 폭 재기(ResizeObserver --
// 펼친 상세를 보이는 폭에 맞추던 것)는 없앴다.
function useScrolledEdge() {
  const ref = useRef<HTMLDivElement>(null);
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onScroll = () => setScrolled(el.scrollLeft > 0);
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);
  return { ref, scrolled };
}

// PV 표시(2026-10-29): pvLine = 이 탭이 PV 를 담는가(필터 선택지의 PV 안 대상 수 > 0 -- filesystem·미등록 탭은 둘째 줄이 없다),
// multiCluster = 탭의 클러스터가 둘 이상(PV 표기 끝에 클러스터), requesterFiltered = 요청자 필터가 켜졌다(상세가 「이력은 전체」라고
// 말한다).
export interface PvDisplay { pvLine: boolean; multiCluster: boolean; requesterFiltered: boolean }
export const NO_PV_DISPLAY: PvDisplay = { pvLine: false, multiCluster: false, requesterFiltered: false };

export function TargetTable({ rows, activeTarget, onOpen, buttonRefs, sort, order, onSort, now, pvDisplay = NO_PV_DISPLAY }: {
  rows: ScanTargetRow[]; activeTarget: string | null; onOpen: (target: string) => void;
  buttonRefs?: React.MutableRefObject<Map<string, HTMLButtonElement>>;
  sort: UsageSort; order: "desc" | "asc"; onSort: (c: UsageSort) => void; now: number; pvDisplay?: PvDisplay;
}) {
  const wrap = useScrolledEdge();
  // 고정 경로 칸의 오른쪽 그늘은 표를 옆으로 밀었을 때만 -- 넘치지 않는 넓은 화면에선 칸 사이에 선이 없다.
  const stickyEdge = wrap.scrolled ? "shadow-[1px_0_0_theme(colors.line)]" : "";
  return (
    <Table framed wrapRef={wrap.ref}>
      <thead><tr>
        <th className={`${TH} sticky left-0 bg-surface pl-2 ${stickyEdge}`}>경로</th>
        {/* 2026-10-02 목록 컬럼: 최신 성공 scan 기준(서버 latest). 모름은 "—"(null≠0). 2026-10-19: 머리글이 정렬
            버튼(서버 정렬 -- 받은 100개만 정렬하지 않는다). */}
        <SortHeader col="bytes" label="실 사용량" sort={sort} order={order} onSort={onSort} />
        <SortHeader col="files" label="파일 수" sort={sort} order={order} onSort={onSort} />
        {/* hot 도움말의 atime 규칙은 usageStats ATIME_READ_RULE 한 곳(2026-10-28 리뷰 portal-batch-27 -- relatime 은 읽기를
            하루 단위로 반영하므로 「바꾸지 않는 스토리지」 목록에 넣지 않는다). */}
        <SortHeader col="hot" label="hot 비율" sort={sort} order={order} onSort={onSort}
                    help={`atime 기준 ${HOT_AGE_MAX_DAYS}일 이내 용량 비중 · 최근 성공 scan — ${ATIME_READ_RULE}. 읽기가 `
                      + "atime 을 바꾸지 않는 스토리지(noatime 마운트, 일부 CephFS 등)에선 최근에 쓴 데이터 비중에 가까운 근사치. "
                      // 2026-10-29: 「차가운(정리 후보) 데이터는 어디인가」를 따로 프리셋 없이 열 정렬로 답한다.
                      + "낮은 순으로 두면 오래 안 쓴 데이터가 위로 — 정리 후보"} />
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
          // 고정 칸 바탕은 행 바탕과 같이 바뀐다(불투명 -- 위 머리 주석). 행 hover 는 group 으로 고정 칸까지.
          const bg = active ? "bg-infobg" : "bg-surface group-hover:bg-panel";
          const pv = pvDisplay.pvLine ? usagePvOf(r.pv) : null;
          return (
            <tr key={r.target} className={`group cursor-pointer border-t border-line ${active ? "bg-infobg" : "hover:bg-panel"}`}
                onClick={() => onOpen(r.target)}>
              {/* 여는 자리는 버튼이다 -- 행 onClick 만 두면 키보드로 못 연다. aria-haspopup 으로 「창을 여는 버튼」임을 보조기기에
                  알린다. 경로는 한 줄(whitespace-nowrap, 2026-10-12 -- 좁은 폭에서 하이픈마다 쪼개졌다, e2e E10 L4)이고, 좁은
                  폭에선 칸 상한까지만 보이고 말줄임(전체는 title·버튼 이름·시트 제목에). */}
              <td className={`sticky left-0 py-2 pl-2 pr-3 lg:w-full lg:max-w-0 ${bg} ${stickyEdge}`}>
                <button type="button" aria-haspopup="dialog" title={r.target}
                        // 지운 행의 버튼은 지도에 남을 수 있다 -- 쓰는 쪽(StoragePanel)이 isConnected 로 거른다.
                        ref={(el) => { if (el !== null) buttonRefs?.current.set(r.target, el); }}
                        className={`inline-flex max-w-[11rem] items-center whitespace-nowrap rounded text-left
                                    text-accent hover:underline focus-visible:outline-none focus-visible:ring-2
                                    focus-visible:ring-accent sm:max-w-[22rem] lg:max-w-full ${active ? "font-medium" : ""}`}
                        onClick={(e) => { e.stopPropagation(); onOpen(r.target); }}>
                  <span className="min-w-0 truncate">{r.target}</span>
                </button>
                {/* PV 소속 둘째 줄(2026-10-29): 버튼 밖 -- 버튼 이름(=경로)·한 줄 불변식(e2e L4)을 바꾸지 않는다. 버튼과 같은 폭
                    상한·말줄임이라 경로 글자 아래에 선다. */}
                {pv !== null && (
                  <div className="max-w-[11rem] truncate text-xs text-muted sm:max-w-[22rem] lg:max-w-full">
                    <PvLine pv={pv} multiCluster={pvDisplay.multiCluster} />
                  </div>
                )}
              </td>
              <td className="whitespace-nowrap px-3 text-right tabular-nums"
                  title={typeof p?.total_bytes === "number"
                    ? `${p.total_bytes.toLocaleString("ko-KR")} B` : undefined}>
                {typeof p?.total_bytes === "number" ? humanBytes(p.total_bytes) : "—"}
                {/* 리포트를 못 읽은 최신 스캔: 용량은 러너가 DB 에 남긴 값(같은 규칙)이고 파일 수·hot 은 모름이다.
                    상세 시트의 "최신" 수치는 읽을 수 있는 리포트 기준이라 다를 수 있다 -- 그 사실을 표시한다(리뷰). */}
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
          );
        })}
      </tbody>
    </Table>
  );
}
