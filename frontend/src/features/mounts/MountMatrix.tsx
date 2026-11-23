import { useState } from "react";
import type { ReactNode } from "react";
import { HoverTip, useHoverAnchor } from "../../components/ui/HoverTip";
import { agoText, kstStamp } from "../../lib/datetime";
import { useNow } from "../../lib/useNow";
import { Chip, MountCellChip, MountTipBody, NodeTags, ProblemTag, SharedCellChip, SharedTipBody } from "./MountCell";
import { cellAriaLabel, cellLabel, podFailedOn, podFailedRoles, podsOn, sharedDisplay } from "./mountTerms";
import type { MountRow, MountStatusResponse, StorageColumn } from "./types";

// 노드 × 스토리지 표(2026-11-23). 노드 = 행(수십 대까지 늘어난다), 열 = [보고] + [DMS 공용] + 활성 스토리지(응답 순서).
//
// 고정 머리·첫 열: 래퍼 하나가 가로·세로 스크롤을 다 맡는다(공용 Table 의 래퍼는 overflow-x 만이라 머리 고정이 안 된다). 래퍼의
// `isolate` 가 고정 칸의 z 를 래퍼 안에 가둬, body 끝 포털(Dialog 덮개·HoverTip z-50)을 뚫지 않는다(사용량 TargetTable 의 z-index
// 사고 원인 해소 -- e2e E19 가 실 브라우저로 잰다). 고정 칸은 모두 불투명 배경이고 opacity 를 쓰지 않는다(쌓임 맥락이 생긴다) --
// 흐림은 글자 알파(text-ink/70 -- mountTerms TONE_CLASS). 리포트 지연 행도 노드 이름은 흐리지 않는다(지연은 「리포트 지연」 칩과
// 행 머리의 「지연」 꼬리표가 말한다). border-separate(collapse 면 고정 칸 경계선이 스크롤에 사라진다).
//
// 머리 배경은 bg-surface -- bg-panel 이면 같은 면색의 중립 꼬리표(「마운트 3/5」·「파일시스템」·「PV n개」)가 맨 글자처럼 보인다.
// 스토리지·공용 열은 머리도 가운데 정렬(칸 칩이 가운데라 같은 열에서 어긋나지 않게), 노드·보고 열은 왼쪽.
//
// 칸은 탭 정지로 두지 않는다(수백 칸 × Tab 폭증). 키보드 진입점은 행 머리의 노드 버튼 → 상세 시트(같은 내용). 마우스는 칸에 올리면
// 즉시 툴팁(HoverTip -- 스크롤하면 닫힌다). 행 순서는 응답 순서(이름순) 고정 -- 10초 폴링마다 행이 뛰지 않게 다시 정렬하지 않는다.
// 대신 문제 노드는 고정 첫 열의 꼬리표(ProblemTag)로 드러난다 -- 가로로 민 표에서 화면 밖 열의 문제도 그 행에서 보인다.

// z 는 칸마다 하나만 준다(같은 칸에 z 클래스 둘이면 어느 쪽이 이길지 CSS 순서에 달린다): 모서리 3 > 머리 2 > 행 머리 1.
const HEAD_BASE = "sticky top-0 border-b border-line px-3 py-2 align-bottom font-medium";
const HEAD = `${HEAD_BASE} bg-surface text-left`;
const HEAD_CENTER = `${HEAD_BASE} bg-surface text-center`;
// 툴팁 앵커 키 = 노드 + SEP + 스토리지 이름(또는 공용 디렉터리 표지). 이름에는 제어문자가 없다.
const SEP = "\u0000";
const SHARED_KEY = "\u0001shared";
const tipKey = (node: string, column: string) => `${node}${SEP}${column}`;

export function MountMatrix({ data, rows, renderStorageMeta, roleLabels, onOpenNode, registerButton }: {
  data: MountStatusResponse;
  rows: MountRow[];
  renderStorageMeta?: (col: StorageColumn) => ReactNode;
  roleLabels?: Record<string, string>;
  onOpenNode: (name: string) => void;
  registerButton: (name: string) => (el: HTMLButtonElement | null) => void;
}) {
  const nowMs = useNow(15_000, true);
  const [scrolled, setScrolled] = useState(false);
  const { active, bind, close } = useHoverAnchor<string>();
  const shared = data.shared_dir;
  const cols = data.storages;

  const tip = (() => {
    if (active === null) return null;
    const [nodeName, key] = active.key.split(SEP);
    const row = rows.find((r) => r.node_name === nodeName);
    if (row === undefined) return null;
    if (key === SHARED_KEY) {
      if (row.shared_dir === null || shared === null) return null;
      return <SharedTipBody nodeName={row.node_name} path={shared.path} cell={row.shared_dir} majority={shared.majority}
                            reportedAt={row.reported_at} nowMs={nowMs} failedRoles={podFailedRoles(data, row.node_name)} />;
    }
    const col = cols.find((c) => c.storage_name === key);
    const cell = row.cells[key];
    if (col === undefined || cell === undefined) return null;
    return <MountTipBody nodeName={row.node_name} col={col} cell={cell} reportedAt={row.reported_at} nowMs={nowMs} />;
  })();

  return (
    <div onScroll={(e) => setScrolled(e.currentTarget.scrollLeft > 0)}
         className="isolate max-h-[min(72vh,52rem)] overflow-auto rounded-card border border-line bg-surface">
      <table className="min-w-full border-separate border-spacing-0 text-sm">
        <caption className="sr-only">노드별 스토리지 마운트 현황</caption>
        <thead>
          <tr>
            <th scope="col" className={`${HEAD} left-0 z-[3] border-r`}>노드</th>
            <th scope="col" className={`${HEAD} z-[2]`}>보고</th>
            {shared !== null && (
              <th scope="col" className={`${HEAD_BASE} z-[2] border-r-2 bg-panel text-center`}
                  title={`아티팩트 경로 ${shared.path} — 잡 결과·로그가 쓰이는 공용 디렉터리입니다. 모든 노드의 같은 경로에 같은 공유 `
                    + "스토리지가 있어야 합니다."
                    + (shared.majority ? ` 대부분 노드: ${shared.majority.mount_point ?? "—"}`
                      + `${shared.majority.fstype ? ` (${shared.majority.fstype})` : ""}` : "")}>
                <div className="whitespace-nowrap">DMS 공용</div>
                {shared.state_root !== null && (
                  <div className="mx-auto max-w-[11rem] truncate font-mono text-[11px] font-normal text-muted">{shared.state_root}</div>
                )}
              </th>
            )}
            {cols.map((col) => (
              <th key={col.storage_name} scope="col" className={`${HEAD_CENTER} z-[2] min-w-[8rem]`}
                  title={`기준: 신선한 보고만 — 스토리지 목록의 상태(${col.summary.detail})와 같은 규칙, 읽기 전용도 마운트로 셉니다`}>
                <div className="whitespace-nowrap font-mono text-[13px]">{col.storage_name}</div>
                <div className="mx-auto max-w-[12rem] truncate font-mono text-[11px] font-normal text-muted" title={col.mount_path}>
                  {col.mount_path}
                </div>
                {renderStorageMeta && (
                  <div className="mt-1 flex flex-wrap justify-center gap-1 font-normal">{renderStorageMeta(col)}</div>
                )}
                <div className="mt-1 flex flex-wrap items-center justify-center gap-1 font-normal">
                  <ColumnSummaryChips col={col} />
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const dim = row.fresh ? "" : "text-ink/70";
            const cellCls = `border-b border-line px-2 py-2 text-center align-middle group-hover:bg-panel ${dim}`;
            return (
              <tr key={row.node_name} className="group">
                <th scope="row"
                    className={`sticky left-0 z-[1] min-w-[11rem] border-b border-r border-line bg-surface px-3 py-2 text-left
                                align-top font-normal group-hover:bg-panel ${
                      scrolled ? "shadow-[1px_0_0_theme(colors.line)]" : ""}`}>
                  <div className="flex max-w-[14rem] flex-col items-start gap-1">
                    <div className="flex max-w-full items-center gap-1.5">
                      <button type="button" ref={registerButton(row.node_name)} onClick={() => onOpenNode(row.node_name)}
                              title={`${row.node_name} 마운트 상세`}
                              className="min-w-0 truncate whitespace-nowrap rounded text-left font-mono text-[13px] font-medium
                                         hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                        {row.node_name}
                      </button>
                      <ProblemTag row={row} />
                    </div>
                    <NodeTags row={row} roleLabels={roleLabels} pods={podsOn(data, row.node_name)} />
                  </div>
                </th>
                <td className="whitespace-nowrap border-b border-line px-3 py-2 align-middle group-hover:bg-panel"
                    title={kstStamp(row.reported_at)}>
                  {row.fresh
                    ? <span className="text-muted">{agoText(row.reported_at, nowMs)}</span>
                    : <Chip tone="bad">{`리포트 지연 · ${agoText(row.reported_at, nowMs)}`}</Chip>}
                </td>
                {shared !== null && (
                  <td className={`${cellCls} border-r-2 bg-panel/60`}>
                    {row.shared_dir !== null && (
                      <SharedCellChip cell={row.shared_dir} podFailed={podFailedOn(data, row.node_name)}
                                      {...bind(tipKey(row.node_name, SHARED_KEY))}
                                      aria-label={cellAriaLabel(row.node_name, "DMS 공용",
                                        sharedDisplay(row.shared_dir, podFailedOn(data, row.node_name)).meta.label)} />
                    )}
                  </td>
                )}
                {cols.map((col) => {
                  const cell = row.cells[col.storage_name];
                  return (
                    <td key={col.storage_name} className={cellCls}>
                      {cell !== undefined && (
                        <MountCellChip cell={cell} {...bind(tipKey(row.node_name, col.storage_name))}
                                       aria-label={cellAriaLabel(row.node_name, col.storage_name, cellLabel(cell))} />
                      )}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
      {active !== null && tip !== null && <HoverTip anchor={active.rect} onClose={close}>{tip}</HoverTip>}
    </div>
  );
}

/** 열 머리 요약 줄: 「마운트 r/t」(reconciler 와 같은 수 -- 읽기 전용도 센다) + 문제·읽기 전용 수. 부분 마운트는 정상 구성일 수
 *  있어 중립색이다. */
export function ColumnSummaryChips({ col }: { col: StorageColumn }) {
  const s = col.summary;
  return (
    <>
      {s.total > 0
        ? <Chip tone={s.ready === s.total ? "ok" : "neutral"}>{`마운트 ${s.ready}/${s.total}`}</Chip>
        : <span className="whitespace-nowrap text-[11px] text-muted">보고 없음</span>}
      {s.broken > 0 && <Chip tone="bad">{`문제 ${s.broken}`}</Chip>}
      {s.read_only > 0 && <Chip tone="warn">{`읽기 전용 ${s.read_only}`}</Chip>}
    </>
  );
}
