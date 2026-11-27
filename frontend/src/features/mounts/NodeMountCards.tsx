import { useMemo } from "react";
import { agoText, kstStamp } from "../../lib/datetime";
import { useNow } from "../../lib/useNow";
import { Chip, MountCellChip, NodeTags, ProblemTag, SharedCellChip } from "./MountCell";
import { CELL_META, absentNowhere, cellAriaLabel, cellView, columnFacts, podFailedOn, podsOn, sharedDisplay } from "./mountTerms";
import type { CellState, MountRow, MountStatusResponse, StorageColumn } from "./types";

// 좁은 화면(390·375)의 노드별 카드(2026-11-23). 표를 CSS 로 숨기지 않고 이 보기를 **조건부로** 그린다(MountStatusPage) -- 숨은 표가
// DOM 에 남으면 칸 수 측정(e2e)이 공허해진다. 카드는 정상이 아닌 칸만 먼저 보이고(문제 → 읽기 전용 → 확인 대기 → 모름 → 없음 --
// 어디에도 마운트 흔적이 없는 열의 없음은 문제라 마운트 문제와 같은 자리), 정상 칸은 접어 둔다. 리포트 지연 카드는 글자를 text-ink/70
// 으로 흐리되(opacity 금지 -- mountTerms TONE_CLASS) 노드 이름은 흐리지 않는다.

const ORDER: CellState[] = ["broken", "read_only", "pending", "stale", "absent"];
const LINE = "flex items-center justify-between gap-2 border-t border-line py-1.5";

export function NodeMountCards({ data, rows, cols, roleLabels, onOpenNode, registerButton }: {
  data: MountStatusResponse;
  rows: MountRow[];
  /** 그릴 스토리지(부르는 쪽이 거른 열 -- ?q=·?cols=problems). */
  cols: StorageColumn[];
  roleLabels?: Record<string, string>;
  onOpenNode: (name: string) => void;
  registerButton: (name: string) => (el: HTMLButtonElement | null) => void;
}) {
  const nowMs = useNow(15_000, true);
  // 거른 열이면 요약 줄이 「표시한 스토리지」 기준임을 말한다 -- 머리의 「문제 n」(서버 problem_count)은 전체 스토리지 기준이라 숫자가
  // 다를 수 있다(2026-11-25 리뷰: q=sec 에서 「문제 3」 카드의 요약 줄이 「마운트 문제 1」).
  const partial = cols.length < data.storages.length;
  const facts = useMemo(() => columnFacts(data.nodes), [data.nodes]);       // 행 필터 전 모든 행(서버 규칙과 같게)
  return (
    <ul aria-label="노드별 마운트 현황" className="space-y-3">
      {rows.map((row) => {
        const cells = cols
          .map((col) => ({ col, cell: row.cells[col.storage_name] }))
          .filter((x) => x.cell !== undefined)
          .map((x) => ({ ...x, nowhere: absentNowhere(x.col.storage_name, x.cell, facts) }));
        const rank = (x: (typeof cells)[number]) => ORDER.indexOf(x.nowhere ? "broken" : x.cell.state);
        const odd = cells.filter((x) => x.cell.state !== "ready").sort((a, b) => rank(a) - rank(b));
        const ready = cells.filter((x) => x.cell.state === "ready");
        const counts = new Map<CellState, number>();
        for (const x of cells) counts.set(x.cell.state, (counts.get(x.cell.state) ?? 0) + 1);
        const summary = (["ready", ...ORDER] as CellState[])
          .filter((s) => (counts.get(s) ?? 0) > 0)
          .map((s) => `${CELL_META[s].label} ${counts.get(s)}`).join(" · ");
        return (
          <li key={row.node_name}
              className={`rounded-card border border-line bg-surface p-3 ${row.fresh ? "" : "text-ink/70"}`}>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0 space-y-1">
                <div className="flex max-w-full items-center gap-1.5">
                  <button type="button" ref={registerButton(row.node_name)} onClick={() => onOpenNode(row.node_name)}
                          className="min-w-0 truncate whitespace-nowrap rounded text-left font-mono text-[13px] font-semibold
                                     text-ink hover:underline focus-visible:outline-none focus-visible:ring-2
                                     focus-visible:ring-accent">
                    {row.node_name}
                  </button>
                  <ProblemTag row={row} showStale={false} />
                </div>
                <NodeTags row={row} roleLabels={roleLabels} pods={podsOn(data, row.node_name)} />
              </div>
              <span className="whitespace-nowrap text-xs" title={kstStamp(row.reported_at)}>
                {row.fresh
                  ? <span className="text-muted">{agoText(row.reported_at, nowMs)}</span>
                  : <Chip tone="bad">{`리포트 지연 · ${agoText(row.reported_at, nowMs)}`}</Chip>}
              </span>
            </div>
            <div className="mt-2">
              {data.shared_dir !== null && row.shared_dir !== null && (
                <div className={LINE}>
                  <span className="text-xs text-muted">DMS 공용</span>
                  <SharedCellChip cell={row.shared_dir} podFailed={podFailedOn(data, row.node_name)}
                                  aria-label={cellAriaLabel(row.node_name, "DMS 공용",
                                    sharedDisplay(row.shared_dir, podFailedOn(data, row.node_name)).meta.label)} />
                </div>
              )}
              {odd.map(({ col, cell, nowhere }) => (
                <div key={col.storage_name} className={LINE}>
                  <span className="min-w-0 truncate font-mono text-xs" title={col.mount_path}>{col.storage_name}</span>
                  <MountCellChip cell={cell} nowhere={nowhere} className="!items-end"
                                 aria-label={cellAriaLabel(row.node_name, col.storage_name, cellView(cell, nowhere).aria)} />
                </div>
              ))}
              {summary !== "" && (
                <p className="border-t border-line pt-1.5 text-xs text-muted">
                  {partial ? `${summary} (표시한 스토리지 ${cols.length}개 기준)` : summary}
                </p>
              )}
              {ready.length > 0 && (
                <details className="mt-1 text-xs">
                  <summary className="cursor-pointer text-muted">{`정상 ${ready.length}개 보기`}</summary>
                  {ready.map(({ col, cell }) => (
                    <div key={col.storage_name} className={LINE}>
                      <span className="min-w-0 truncate font-mono" title={col.mount_path}>{col.storage_name}</span>
                      <MountCellChip cell={cell} className="!items-end"
                                     aria-label={cellAriaLabel(row.node_name, col.storage_name, cellView(cell).aria)} />
                    </div>
                  ))}
                </details>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
