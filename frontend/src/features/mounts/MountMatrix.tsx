import { forwardRef, memo, useCallback, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, MouseEvent, ReactNode, RefObject, UIEvent } from "react";
import { HoverTip } from "../../components/ui/HoverTip";
import type { Rect } from "../../components/ui/HoverTip";
import { agoText, kstStamp } from "../../lib/datetime";
import { useNow } from "../../lib/useNow";
import {
  Chip, MountCellChip, MountTipBody, NodeTags, ProblemTag, SharedCellChip, SharedHeadTipBody, SharedTipBody, StorageTipBody,
} from "./MountCell";
import {
  CELL_META, absentNowhere, cellAriaLabel, cellView, columnFacts, columnNameLines, columnPathTail, podFailedRoles, sharedDisplay,
} from "./mountTerms";
import type { ColumnFacts } from "./mountTerms";
import type { ControlPod, MountRow, MountStatusResponse, StorageColumn } from "./types";

// 노드 × 스토리지 표(2026-11-23, 압축 레이아웃 2026-11-25). 노드 = 행(수십 대까지 늘어난다), 열 = [DMS 공용] + 활성 스토리지
// (응답 순서 -- 부르는 쪽이 거른 cols).
//
// 고정 머리·첫 열: 래퍼 하나가 가로·세로 스크롤을 다 맡는다(공용 Table 의 래퍼는 overflow-x 만이라 머리 고정이 안 된다). 표는 래퍼의
// **바로 아래 자식**이다(e2e E19 가 `main table` 의 부모로 래퍼를 찾는다). 래퍼의 `isolate` 가 고정 칸의 z 를 래퍼 안에 가둬, body 끝
// 포털(Dialog 덮개·HoverTip z-50)을 뚫지 않는다(사용량 TargetTable 의 z-index 사고 원인 해소 -- e2e E19 가 실 브라우저로 잰다). 고정
// 칸은 모두 불투명 배경이고 opacity 를 쓰지 않는다(쌓임 맥락이 생긴다) -- 흐림은 글자 알파(text-ink/70 -- mountTerms TONE_CLASS).
// 리포트 지연 행도 노드 이름은 흐리지 않는다(지연은 행 머리의 붉은 보고 시각과 「지연」 꼬리표가 말한다). border-separate(collapse 면
// 고정 칸 경계선이 스크롤에 사라진다).
//
// 열 폭은 고정이다(2026-11-25 사용자 요청 「테이블 칸 좌우가 너무 길어 … 스토리지가 100개가 될 수도 있어」): 스토리지 열 6.5rem
// (= 칩 상한 6rem + 좌우 px-1), DMS 공용 8rem, 노드 열 안쪽 11rem. auto 표에서 퍼센트 max-w·truncate 는 열을 못 묶으므로 머리 내용은
// 절대폭 상자(w-24)로, 칸 칩은 절대 상한(max-w-24)으로 묶는다(table-layout: fixed 는 width:max-content 와 함께 브라우저마다 달라 쓰지
// 않는다). 표는 내용 폭(w-max)이고 래퍼는 w-fit max-w-full -- 넓은 화면에서 열이 늘어나지 않고(예전 min-w-full 은 남는 폭을 모든
// 열에 나눠 칸마다 빈 폭이 100px 넘게 생겼다), 열이 적으면 카드가 표 폭에 맞춰 좁아진다.
//
// 「보고」 열은 없다 -- 보고 시각은 고정 첫 열의 노드 이름 줄 오른쪽(<time>, 가로로 민 표에서도 신선도가 보인다). 문제 꼬리표
// (ProblemTag)는 꼬리표 줄 맨 앞(NodeTags lead) -- 행 순서는 응답 순서(이름순) 고정이라(10초 폴링마다 행이 뛰지 않게) 문제 노드는
// 고정 첫 열의 꼬리표로 드러난다.
//
// 머리 배경은 bg-surface -- bg-panel 이면 같은 면색의 중립 꼬리표(「마운트 3/5」·「파일시스템」)가 맨 글자처럼 보인다. 같은 이유로
// 열 따라가기(마우스가 있는 칸의 열 머리 data-hot)는 면색을 바꾸지 않고 머리 바닥의 accent 막대(배경 그림 -- 불투명 bg-surface 위)로
// 그린다: 예전 hot 면색(연파랑 infobg)은 PV 종류 꼬리표(Tag info = bg-infobg)의 면을 지웠다(2026-11-25 리뷰). box-shadow 는 고정 칸
// 그늘(shadow-pin-*)이 쓰고 있어 겹치지 않게 쓰지 않는다. 스토리지·공용 머리는 위쪽 정렬(이름·경로·종류 줄이 모든 열에서 같은 높이
// -- 줄 수가 달라지는 요약은 맨 아래), 모서리는 아래쪽 정렬.
//
// 이름·경로는 고정 폭 상자에서 **구별되는 끝**이 보이게 줄을 직접 만든다(mountTerms.columnNameLines·columnPathTail -- 26자 이하
// 이름은 두 줄 안에 다, 긴 이름·경로는 「…」 + 끝 조각). 보이는 조각은 aria-hidden, 낭독은 sr-only 전체 한 번.
//
// 칸은 탭 정지로 두지 않는다(수백 칸 × Tab 폭증). 키보드 진입점은 행 머리의 노드 버튼 → 상세 시트(같은 내용)이고, 래퍼 자체는
// 초점을 받아(tabIndex 0, role=region) 화살표 키로 민다. 마우스는 칸·열 머리에 올리면 즉시 툴팁(HoverTip -- 스크롤하면 닫힌다).
// 툴팁은 래퍼 한 곳의 mouseover/mouseout 위임(칸마다 핸들러 없음)과 따로 그리는 층(MatrixTip)이 맡는다 -- 마우스를 움직여도 표
// 전체를 다시 그리지 않는다(100열 × 23행 = 2천여 칸, CPU 4x 감속에서 hover 한 번에 약 85ms 였다). 행·머리는 memo, 스크롤 그늘은
// 래퍼의 data-scroll 속성(React 상태 없음), 보고 시각의 15초 틱은 행마다 자기 <time> 칸만 다시 그린다.

// z 는 칸마다 하나만 준다(같은 칸에 z 클래스 둘이면 어느 쪽이 이길지 CSS 순서에 달린다): 모서리 3 > 머리 2 > 행 머리 1.
// 그늘(shadow-pin-*): 래퍼의 data-scroll 이 x(가로로 밀림)·y(세로로 밀림)·xy 일 때만 민 방향으로. Tailwind JIT 가 읽게 리터럴로 둔다.
const PIN_X = "group-data-[scroll=x]/mx:shadow-pin-x group-data-[scroll=xy]/mx:shadow-pin-x";
const PIN_Y = "group-data-[scroll=y]/mx:shadow-pin-y group-data-[scroll=xy]/mx:shadow-pin-y";
const PIN_XY = "group-data-[scroll=x]/mx:shadow-pin-x group-data-[scroll=y]/mx:shadow-pin-y group-data-[scroll=xy]/mx:shadow-pin-xy";
const WRAP = "group/mx isolate w-fit max-w-full max-h-[min(72vh,52rem)] overflow-auto overscroll-x-contain rounded-card border "
  + "border-line bg-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";
const TABLE = "w-max border-separate border-spacing-0 text-sm [&>tbody>tr:last-child>*]:border-b-0";
const CORNER = "sticky left-0 top-0 z-[3] border-b border-r border-line bg-surface px-2 pb-1.5 align-bottom text-left text-xs "
  + `font-semibold ${PIN_XY}`;
const ST_HEAD = "sticky top-0 z-[2] w-[6.5rem] min-w-[6.5rem] border-b border-r border-line border-r-line/60 bg-surface px-1 "
  + `pb-1.5 pt-2 align-top text-center font-medium last:border-r-0 data-[hot]:bg-[linear-gradient(to_top,theme(colors.accent)_2px,transparent_2px)] ${PIN_Y}`;
const SH_HEAD = "sticky top-0 z-[2] w-[8rem] min-w-[8rem] border-b border-r-2 border-line bg-panel px-1 pb-1.5 pt-2 align-top "
  + `text-center font-medium ${PIN_Y}`;
const ROW_HEAD = "sticky left-0 z-[1] border-b border-r border-line bg-surface px-2 py-1.5 text-left align-top font-normal "
  + `group-hover:bg-panel ${PIN_X}`;
const CELL = "border-b border-r border-line border-r-line/60 px-1 py-1.5 text-center align-middle last:border-r-0 "
  + "group-hover:bg-panel";
const SH_CELL = "border-b border-r-2 border-line bg-panel/60 px-1 py-1.5 text-center align-middle group-hover:bg-panel";

const NO_PODS: ControlPod[] = [];

type TipKind = "cell" | "shared" | "head" | "shared-head";
interface TipTarget { kind: TipKind; node: string | undefined; col: string | undefined; rect: Rect }
interface TipHandle { open: (t: TipTarget) => void; close: () => void }

export function MountMatrix({ data, rows, cols, renderStorageMeta, renderStorageTip, roleLabels, onOpenNode, registerButton }: {
  data: MountStatusResponse;
  rows: MountRow[];
  cols: StorageColumn[];
  renderStorageMeta?: (col: StorageColumn) => ReactNode;
  renderStorageTip?: (col: StorageColumn) => ReactNode;
  roleLabels?: Record<string, string>;
  onOpenNode: (name: string) => void;
  registerButton: (name: string) => (el: HTMLButtonElement | null) => void;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const tipRef = useRef<TipHandle>(null);
  const maxHeight = useFitMaxHeight(wrapRef);
  const shared = data.shared_dir;
  // 열 재료(어디에도 마운트되지 않은 열의 「없음」 표시 -- mountTerms.columnFacts)는 행 필터 전 **모든** 행으로 잰다(서버 규칙과 같게).
  // nodes 가 그대로면 같은 객체라 행 memo 가 깨지지 않는다.
  const facts = useMemo(() => columnFacts(data.nodes), [data.nodes]);
  // 행 memo 가 깨지지 않게 노드별 파드 배열을 한 번만 만든다(control_pods 가 그대로면 react-query 구조 공유로 같은 배열).
  const podsByNode = useMemo(() => {
    const m = new Map<string, ControlPod[]>();
    for (const p of data.control_pods ?? []) {
      const list = m.get(p.node_name);
      if (list) list.push(p); else m.set(p.node_name, [p]);
    }
    return m;
  }, [data.control_pods]);

  // 위임: 마우스가 올라간 [data-tip] 요소(칸·열 머리) 하나를 기억하고, 그 열 머리에 data-hot(열 따라가기 막대)을 단다.
  // data-hot·data-scroll 은 React 가 관리하지 않는 속성이다(다시 그려도 지우지 않는다).
  const cur = useRef<HTMLElement | null>(null);
  const hot = useRef<Element | null>(null);
  const setHot = useCallback((ci?: string) => {
    hot.current?.removeAttribute("data-hot");
    hot.current = null;
    if (ci === undefined) return;
    const th = wrapRef.current?.querySelector(`thead th[data-ci="${ci}"]`) ?? null;
    th?.setAttribute("data-hot", "");
    hot.current = th;
  }, []);
  const onMouseOver = (e: MouseEvent<HTMLDivElement>) => {
    const el = e.target instanceof Element ? e.target.closest<HTMLElement>("[data-tip]") : null;
    if (el === null || el === cur.current || !e.currentTarget.contains(el)) return;
    cur.current = el;
    setHot(el.dataset.ci);
    tipRef.current?.open({ kind: el.dataset.tip as TipKind, node: el.closest("tr")?.dataset.node, col: el.dataset.col,
                           rect: el.getBoundingClientRect() });
  };
  const onMouseOut = (e: MouseEvent<HTMLDivElement>) => {
    const c = cur.current;
    if (c === null) return;
    const to = e.relatedTarget instanceof Element ? e.relatedTarget : null;
    if (to !== null && c.contains(to)) return;                         // 칸 안의 자식으로 옮겼다
    const next = to?.closest("[data-tip]") ?? null;
    if (next !== null && e.currentTarget.contains(next)) return;       // 다른 칸으로 -- 이어지는 mouseover 가 바꾼다(깜빡임 없이)
    cur.current = null;
    setHot();
    tipRef.current?.close();
  };
  const onTipClosed = useCallback(() => { cur.current = null; setHot(); }, [setHot]);
  // Home/End = 가로 처음·끝(2026-11-25 d46 실증: 화살표 한 번에 40px 라 100열 끝까지 약 246번이었고, Home/End 는 페이지를 세로로
  // 움직였다). 래퍼 자신에 초점이 있을 때만 -- 안쪽 노드 버튼의 키는 건드리지 않는다.
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget || e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.key === "Home") { e.currentTarget.scrollLeft = 0; e.preventDefault(); }
    else if (e.key === "End") { e.currentTarget.scrollLeft = e.currentTarget.scrollWidth; e.preventDefault(); }
  };
  const onScroll = (e: UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const x = el.scrollLeft > 0;
    const y = el.scrollTop > 0;
    const v = x && y ? "xy" : x ? "x" : y ? "y" : null;
    if (v === null) delete el.dataset.scroll;
    else if (el.dataset.scroll !== v) el.dataset.scroll = v;
  };

  return (
    <div ref={wrapRef} role="region" aria-label="노드 × 스토리지 표 — 가로·세로로 스크롤(Home·End 로 처음·끝)" tabIndex={0}
         onScroll={onScroll} onKeyDown={onKeyDown} onMouseOver={onMouseOver} onMouseOut={onMouseOut}
         className={WRAP} style={maxHeight === null ? undefined : { maxHeight }}>
      <table className={TABLE}>
        <caption className="sr-only">노드별 스토리지 마운트 현황</caption>
        <MatrixHead cols={cols} shared={shared} renderStorageMeta={renderStorageMeta} />
        <tbody>
          {rows.map((row) => (
            <MatrixRow key={row.node_name} row={row} cols={cols} hasShared={shared !== null} facts={facts}
                       pods={podsByNode.get(row.node_name) ?? NO_PODS} roleLabels={roleLabels} onOpenNode={onOpenNode}
                       registerButton={registerButton} />
          ))}
        </tbody>
      </table>
      <MatrixTip ref={tipRef} data={data} rows={rows} facts={facts} renderStorageTip={renderStorageTip} onClosed={onTipClosed} />
    </div>
  );
}

const MatrixHead = memo(function MatrixHead({ cols, shared, renderStorageMeta }: {
  cols: StorageColumn[];
  shared: MountStatusResponse["shared_dir"];
  renderStorageMeta?: (col: StorageColumn) => ReactNode;
}) {
  return (
    <thead>
      <tr>
        <th scope="col" className={CORNER}>
          <div className="flex w-44 items-end justify-between gap-2">
            <span>노드</span>
            <span aria-hidden className="text-[11px] font-normal leading-4 text-muted">보고</span>
          </div>
        </th>
        {shared !== null && (
          <th scope="col" data-tip="shared-head" className={SH_HEAD}>
            <div className="mx-auto w-[7.5rem]">
              {/* 스토리지 열 머리와 같은 두 줄 고정 높이(아래 붙임) -- 「DMS 공용」이 스토리지 이름 끝 줄과 같은 자리에 온다(d46 실증: 16px 위였다). */}
              <div className="flex h-8 flex-col justify-end whitespace-nowrap text-xs font-semibold leading-4">DMS 공용</div>
              {shared.state_root !== null && (
                <div className="truncate font-mono text-[11px] font-normal leading-4 text-muted">{shared.state_root}</div>
              )}
            </div>
          </th>
        )}
        {cols.map((col, i) => {
          const nameLines = columnNameLines(col.storage_name);
          const pathTail = columnPathTail(col.mount_path);
          return (
            <th key={col.storage_name} scope="col" data-tip="head" data-col={col.storage_name} data-ci={i} className={ST_HEAD}>
              <div className="mx-auto w-24">
                {/* 이름 칸 = 두 줄 고정 높이(아래 붙임) -- 이름 줄이 모든 열에서 같은 자리다. */}
                <div className="flex h-8 flex-col justify-end font-mono text-[12px] font-semibold leading-4">
                  {nameLines.length === 1 ? (
                    <span className="truncate">{col.storage_name}</span>
                  ) : (
                    <>
                      <span className="sr-only">{col.storage_name}</span>
                      {nameLines.map((line, k) => <span key={k} aria-hidden className="truncate">{line}</span>)}
                    </>
                  )}
                </div>
                <div className="truncate font-mono text-[11px] font-normal leading-4 text-muted">
                  {pathTail === null ? col.mount_path : (
                    <><span className="sr-only">{col.mount_path}</span><span aria-hidden>{`…${pathTail}`}</span></>
                  )}
                </div>
                {renderStorageMeta && (
                  <div className="mt-1 flex flex-wrap justify-center gap-0.5 font-normal leading-4">{renderStorageMeta(col)}</div>
                )}
                <div className="mt-1 flex flex-wrap items-center justify-center gap-0.5 font-normal leading-4">
                  <ColumnSummaryChips col={col} />
                </div>
              </div>
            </th>
          );
        })}
      </tr>
    </thead>
  );
});

const MatrixRow = memo(function MatrixRow({ row, cols, hasShared, facts, pods, roleLabels, onOpenNode, registerButton }: {
  row: MountRow;
  cols: StorageColumn[];
  hasShared: boolean;
  facts: ColumnFacts;
  pods: ControlPod[];
  roleLabels?: Record<string, string>;
  onOpenNode: (name: string) => void;
  registerButton: (name: string) => (el: HTMLButtonElement | null) => void;
}) {
  const dim = row.fresh ? "" : "text-ink/70";
  const podFailed = pods.some((p) => p.read_failed);
  return (
    <tr className="group" data-node={row.node_name}>
      <th scope="row" className={ROW_HEAD}>
        <div className="flex w-44 flex-col items-start gap-1">
          <div className="flex w-full items-baseline gap-2">
            <button type="button" ref={registerButton(row.node_name)} onClick={() => onOpenNode(row.node_name)}
                    title={`${row.node_name} 마운트 상세`}
                    className="min-w-0 truncate whitespace-nowrap rounded text-left font-mono text-[13px] font-medium
                               hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
              {row.node_name}
            </button>
            <RowAge row={row} />
          </div>
          <NodeTags row={row} roleLabels={roleLabels} pods={pods}
                    lead={row.problem ? <ProblemTag row={row} /> : undefined} />
        </div>
      </th>
      {hasShared && (
        <td data-tip={row.shared_dir !== null ? "shared" : undefined} className={`${SH_CELL} ${dim}`}>
          {row.shared_dir !== null && (
            <SharedCellChip cell={row.shared_dir} podFailed={podFailed} className="max-w-[7.5rem]"
                            aria-label={cellAriaLabel(row.node_name, "DMS 공용", sharedDisplay(row.shared_dir, podFailed).meta.label)} />
          )}
        </td>
      )}
      {cols.map((col, i) => {
        const cell = row.cells[col.storage_name];
        const nowhere = cell !== undefined && absentNowhere(col.storage_name, cell, facts);
        return (
          <td key={col.storage_name} data-tip={cell !== undefined ? "cell" : undefined} data-col={col.storage_name} data-ci={i}
              className={`${CELL} ${dim}`}>
            {cell !== undefined && (
              <MountCellChip cell={cell} nowhere={nowhere} className="max-w-24"
                             aria-label={cellAriaLabel(row.node_name, col.storage_name, cellView(cell, nowhere).aria)} />
            )}
          </td>
        );
      })}
    </tr>
  );
});

/** 행 머리의 보고 시각 -- 자기 15초 틱으로 이 칸만 다시 그린다. 리포트 지연이면 붉은 글자 + 낭독용 「리포트 지연 · 」(같은 행의
 *  「⚠ 지연」 꼬리표가 아이콘·글자로 말하므로 색만으로 구분하지 않는다). 절대 시각(KST)은 title. */
function RowAge({ row }: { row: MountRow }) {
  const nowMs = useNow(15_000, true);
  return (
    <time dateTime={row.reported_at} title={kstStamp(row.reported_at)}
          className={`ml-auto shrink-0 whitespace-nowrap text-[11px] leading-4 tabular-nums ${
            row.fresh ? "text-muted" : "font-semibold text-bad"}`}>
      {!row.fresh && <span className="sr-only">리포트 지연 · </span>}
      {agoText(row.reported_at, nowMs)}
    </time>
  );
}

/** 툴팁 층: 활성 대상과 시계를 혼자 가진다(열고 닫아도 표는 다시 그리지 않는다). 폴링 뒤 그 행·열이 사라지면 그리지 않는다. */
const MatrixTip = forwardRef<TipHandle, {
  data: MountStatusResponse;
  rows: MountRow[];
  facts: ColumnFacts;
  renderStorageTip?: (col: StorageColumn) => ReactNode;
  onClosed: () => void;
}>(function MatrixTip({ data, rows, facts, renderStorageTip, onClosed }, ref) {
  const [active, setActive] = useState<TipTarget | null>(null);
  useImperativeHandle(ref, () => ({ open: setActive, close: () => setActive(null) }), []);
  const nowMs = useNow(15_000, active !== null);
  // HoverTip 의 스크롤 닫기(onClose)는 위임 쪽 기억(cur·data-hot)도 지운다 -- 같은 칸에 다시 올리면 다시 열린다.
  const close = useCallback(() => { setActive(null); onClosed(); }, [onClosed]);
  if (active === null) return null;
  const body = tipBody(active, data, rows, facts, nowMs, renderStorageTip);
  if (body === null) return null;
  return <HoverTip anchor={active.rect} onClose={close}>{body}</HoverTip>;
});

function tipBody(t: TipTarget, data: MountStatusResponse, rows: MountRow[], facts: ColumnFacts, nowMs: number,
                 renderStorageTip?: (col: StorageColumn) => ReactNode): ReactNode {
  const shared = data.shared_dir;
  if (t.kind === "shared-head") return shared === null ? null : <SharedHeadTipBody shared={shared} />;
  if (t.kind === "head") {
    const col = data.storages.find((c) => c.storage_name === t.col);
    return col === undefined ? null : <StorageTipBody col={col} extra={renderStorageTip?.(col)} />;
  }
  const row = rows.find((r) => r.node_name === t.node);
  if (row === undefined) return null;
  if (t.kind === "shared") {
    if (row.shared_dir === null || shared === null) return null;
    return <SharedTipBody nodeName={row.node_name} path={shared.path} cell={row.shared_dir} majority={shared.majority}
                          reportedAt={row.reported_at} nowMs={nowMs} failedRoles={podFailedRoles(data, row.node_name)} />;
  }
  const col = data.storages.find((c) => c.storage_name === t.col);
  const cell = t.col !== undefined ? row.cells[t.col] : undefined;
  if (col === undefined || cell === undefined) return null;
  return <MountTipBody nodeName={row.node_name} col={col} cell={cell} reportedAt={row.reported_at} nowMs={nowMs}
                       nowhere={!facts.mounted.has(col.storage_name)} />;
}

/** 래퍼 높이 상한 = 화면에 남은 높이(창 높이 - 래퍼의 문서 위치 - gap, 최소 min). 노드가 많아도 가로 스크롤바(래퍼 바닥)가 첫
 *  화면 안에 온다(예전 72vh 상한은 23노드 1280×900 에서 바닥이 1020px -- 마우스만 쓰는 사용자는 페이지를 내려야 스크롤바를 찾았다).
 *  창 크기·위쪽 높이(실패 띠·범례 줄바꿈)가 바뀌면 다시 잰다(값이 같으면 상태를 바꾸지 않아 되먹임 고리가 없다). 첫 그림은 CSS
 *  기본값(max-h-[min(72vh,52rem)]). 행이 적으면 상한일 뿐이라 내용 높이만큼만 차지한다. */
function useFitMaxHeight(ref: RefObject<HTMLElement>, min = 352, gap = 16): number | null {
  const [h, setH] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el === null) return;
    const measure = () => {
      const top = el.getBoundingClientRect().top + window.scrollY;
      const next = Math.max(min, Math.floor(window.innerHeight - top - gap));
      setH((prev) => (prev === next ? prev : next));
    };
    measure();
    window.addEventListener("resize", measure);
    const ro = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    ro?.observe(document.body);
    return () => {
      window.removeEventListener("resize", measure);
      ro?.disconnect();
    };
  }, [ref, min, gap]);
  return h;
}

/** 열 머리 요약 줄: 「마운트 r/t」(reconciler 와 같은 수 -- 읽기 전용도 센다) + 문제·읽기 전용은 아이콘 + 수(낭독용 낱말은 sr-only,
 *  아이콘은 범례·칸과 같은 CELL_META). 부분 마운트는 정상 구성일 수 있어 중립색이다. 문장 요약은 열 머리 툴팁(columnSummaryText). */
export function ColumnSummaryChips({ col }: { col: StorageColumn }) {
  const s = col.summary;
  const BrokenIcon = CELL_META.broken.Icon;
  const RoIcon = CELL_META.read_only.Icon;
  return (
    <>
      {s.total > 0
        ? <Chip tone={s.ready === s.total ? "ok" : "neutral"}>{`마운트 ${s.ready}/${s.total}`}</Chip>
        : <span className="whitespace-nowrap text-[11px] leading-4 text-muted">보고 없음</span>}
      {s.broken > 0 && (
        <Chip tone="bad" compact>
          <BrokenIcon className="mr-0.5 h-3 w-3 shrink-0" aria-hidden />
          <span className="sr-only">문제 </span>{s.broken}
        </Chip>
      )}
      {s.read_only > 0 && (
        <Chip tone="warn" compact>
          <RoIcon className="mr-0.5 h-3 w-3 shrink-0" aria-hidden />
          <span className="sr-only">읽기 전용 </span>{s.read_only}
        </Chip>
      )}
    </>
  );
}
