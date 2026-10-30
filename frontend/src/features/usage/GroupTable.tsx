import { forwardRef, memo, useImperativeHandle, useLayoutEffect, useRef } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { HelpTip } from "../../components/ui/HelpTip";
import type { UsageAttrFilter, UsageBreakdownBy, UsageGroupRow } from "../../lib/types";
import { pctText } from "../../lib/sizeBuckets";
import { TERMS, nsLabel } from "../k8s/k8sTerms";
import { ATTR_KEYS, cleanAttr, hasAttr } from "./useUsage";
import { hotText, partialSum, sumOf } from "./StorageTotals";
import { humanBytes } from "./usageStats";
import { PV_OUTSIDE_TITLE, PV_UNKNOWN_TITLE } from "./UsageParts";
import { SCOPE_LABEL } from "./UsageFilters";

// 소속별 순위 표(2026-10-30 「소속별」 탭 -- BreakdownPage 가 그린다). 한 행 = 한 묶음(권한 사용자·요청자·namespace·PV 이름·
// StorageClass·클러스터 하나), 서버(/breakdown)가 실 사용량 내림차순으로 준 순서 그대로다(화면이 다시 정렬하지 않는다 -- 상한에서
// 잘린 목록을 화면이 정렬하면 「잘린 것 안의 순위」가 된다).
//
// 열: 이름(+ 부속 흐린 글자) · 대상 · 실 사용량 + 비중 막대 · 파일 · hot · ›. 값의 모름·부분 표기는 스토리지별 비교 표(CompareTable)·
// 합계 줄의 관용 그대로다: 모름 「—」, 부분 합(값 모름을 뺀 합·판정 창으로 잘린 합 -- StorageTotals.partialSum)은 값 뒤 흐린 「이상」,
// hot 은 비율이라 붙이지 않는다. 창이 넘친 응답의 대상 수는 창 안에서 센 하한이라 「+」(CompareTable 의 TargetsCell 관용 -- title 에
// 창 기준). 「이상」은 말줄임 밖이다(2026-10-30 리뷰: 좁은 폭의 파일 칸이 「파일 9,876,54…」로 잘리며 「이상」까지 지워 하한이 잘린
// 정확값처럼 보였다 -- 수만 줄고 표식은 남는다, title 에도 「이상」). 비중 막대 = 묶음 bytes / 이 스토리지 합계(응답 storage_total --
// 무필터 합계와 같은 정의)이고 100% 정규화가 아니다(한 경로가 여러 묶음에 들어 행끼리 더하면 100% 를 넘는다). 반올림이 100% 인데
// 같지 않으면 「≈100%」, 0.1% 미만은 「<0.1%」(pctText), 분모를 모르거나 0 이면 막대가 없다. 분모가 부분 합이면 막대 title·각주가
// 「X 이상 대비」와 근사라고 말한다(분모가 하한이면 % 는 과대다). 대상마다 scan 시점이 달라 묶음이 스토리지 합계보다 클 수도 있다
// (막대는 꽉 찬 채 % 가 100 을 넘는다 -- 숨기지 않는다).
//
// 행 = 경로별로 가는 링크(그 묶음의 필터가 걸린 목록, push -- 다른 쿼리 없이). 이름 칸의 <Link> 가 키보드 자리이고 마우스는 행 아무
// 곳이나 같다(CompareRow 관용 -- 링크 클릭은 행으로 올라가 두 번 이동하지 않게 멈춘다). 서버 필터에 이 포탈이 모르는 키가 있거나
// 걸 것이 없으면 링크를 만들지 않는다(조용히 필터를 덜 건 목록으로 보내지 않는다). 부속이 있는 링크는 이름에 부속을 싣는다(같은
// PV 이름이 다른 namespace 에·같은 namespace 가 다른 클러스터에 있으면 보조기기의 링크 목록에 같은 이름이 여럿 생겼다 -- 2026-10-30
// 리뷰; 부속 글자는 그 이름에 들었으니 보조기기에선 숨긴다).
// 행 key = 축의 묶음 키(서버의 묶음 키 필드). 요약 이어 읽기(1초 재조회)로 순위가 바뀌면 배열 인덱스 key 는 포커스 든 링크를 같은
// DOM 그대로 다른 묶음의 링크로 바꿔 놓았다(리뷰 -- 모르는 채 다른 묶음으로 간다). 묶음 키면 행이 함께 옮겨 가고, React 가 포커스 든
// 행을 옮기며(insertBefore) 브라우저가 포커스를 <body> 로 떨어뜨리면 그 링크로 되돌린다(GroupTable 의 layout effect).
// 끝의 덧붙임 행(흐림·기울임): 「PV 밖(직접 경로)」(→ scope=outside) · 「모름」(→ scope=unknown) · 그 축 값이 없는 대상(권한 사용자
// 없음 · StorageClass 없음 · 요청자 모름 -- 경로별에 그 필터가 없어 링크가 아니다, 설명은 HelpTip).
//
// 한 벌 DOM 의 반응형 표(2026-10-30): <table> 이 아니라 ARIA table(role=table·row·columnheader·cell)인 격자다 -- xl(1280) 이상은
// 열이 맞는 표(행마다 따로 격자지만 열 폭이 내용과 무관한 고정 트랙이라 열이 맞는다), 그 아래는 두 줄 행(이름 · 실 사용량 / 비중
// 막대 · 대상 · 파일 [· hot] -- 머리 줄은 보조기기에만)이라 390 에서 가로로 넘치지 않는다. hot 칸은 lg(1024)~xl 에선 둘째 줄 끝(실
// 사용량 아래)에 보이고, lg 아래에서만 보조기기에만 있다(2026-10-30 리뷰: xl 전환과 겹쳐 1024~1279 의 흔한 노트북 폭에서 hot 이
// 사라졌다 -- 그 폭은 파일 칸이 약 500px 라 자리가 있다). <table> 이면 칸을 두 줄로
// 접을 수 없고(칸은 table-cell 이어야 한다 -- e2e L2) 카드 목록을 따로 두면 DOM 이 두 벌이 된다(경로별 결정). 표로 바뀌는 폭이 lg 가
// 아니라 xl 인 이유: 사이드바(240px)를 뺀 1024 의 본문(약 744px)에선 고정 숫자 열이 이름 칸을 약 110px 로 눌러 PV 이름이 서너 글자만
// 보였다(실 Chrome 확인) -- 그 폭은 두 줄 행이 더 많이 보인다.

export const BY_LABEL: Record<UsageBreakdownBy, string> = {
  user: "권한 사용자", requester: "요청자", namespace: TERMS.namespace, pvc: TERMS.pvc,
  storage_class: TERMS.storageClass, cluster: TERMS.cluster,
};
// 「그 축 값이 없는 대상」 덧붙임 행의 이름과 설명(user·storage_class·requester 축만 -- 서버 extras.none).
const NONE_LABEL: Partial<Record<UsageBreakdownBy, { label: string; help: string }>> = {
  user: { label: "권한 사용자 없음",
          help: "RoleBinding 으로 접근하는 사용자가 없는 namespace 의 PV 안 경로입니다(관리자·특권 운영자만 접근). 경로별에는 이 "
            + "필터가 없습니다." },
  storage_class: { label: "StorageClass 없음",
                   help: "StorageClass 가 없는 PV(정적 바인딩 등)의 경로입니다. 경로별에는 이 필터가 없습니다." },
  requester: { label: "요청자 모름",
               help: "요청 기록이 남지 않은 scan 만 있는 경로입니다. 경로별에는 이 필터가 없습니다." },
};

const n = (x: number) => x.toLocaleString("ko-KR");
const isCount = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x) && x >= 0;
// 부분 합 표식 -- CompareTable 의 MORE 와 같은 모양(shrink-0: 파일 칸에서 수가 말줄임돼도 표식은 줄지 않는다).
const MORE = <span className="ml-1 shrink-0 text-[11px] text-muted">이상</span>;

/** 경로별로 가는 주소: 지금 스토리지 + 그 묶음의 필터(경로별 URL 키 순서 그대로). 걸 것이 없거나 모르는 키가 있으면 null. */
export function pathsHref(storage: string, filter: UsageAttrFilter | null | undefined): string | null {
  if (filter === null || typeof filter !== "object" || Array.isArray(filter)) return null;
  const known = new Set<string>(ATTR_KEYS);
  if (Object.entries(filter).some(([k, v]) => !known.has(k) && v !== null && v !== undefined)) return null;
  const a = cleanAttr(filter);
  if (!hasAttr(a)) return null;
  const p = new URLSearchParams();
  p.set("storage", storage);
  for (const k of ATTR_KEYS) { const v = a[k]; if (v !== undefined) p.set(k, v); }
  return `/admin/usage?${p.toString()}`;
}

/** 비중 글자: 묶음 bytes / 스토리지 합계. 분모를 모르거나 0 이하·묶음 값 모름이면 null(막대 없음). */
export function shareText(part: number | null, whole: number | null): string | null {
  if (part === null || whole === null || !(whole > 0) || part < 0) return null;
  const t = pctText((part / whole) * 100);
  return t === "100%" && part !== whole ? "≈100%" : t;
}

const str = (x: unknown): string | null => (typeof x === "string" ? x : null);
/** 묶음 이름과 부속(흐린 글자). PV 이름의 부속은 경로별 「PV 이름」 칸의 둘째 줄과 같은 표기(「namespace (StorageClass)」 + 클러스터가
 *  여럿이면 「 · 클러스터」 -- PvName), namespace 는 클러스터가 여럿일 때만 클러스터. */
export function groupName(by: UsageBreakdownBy, r: UsageGroupRow, multiCluster: boolean): { name: string | null; sub: string | null } {
  const cluster = multiCluster ? str(r.cluster) : null;
  switch (by) {
    case "user": return { name: str(r.dms_user), sub: null };
    case "requester": return { name: str(r.requester_id), sub: null };
    case "namespace": return { name: str(r.namespace), sub: cluster };
    case "pvc": {
      const ns = str(r.namespace);
      return { name: str(r.pvc),
               sub: ns === null ? cluster : `${nsLabel(ns, r.storage_class)}${cluster !== null ? ` · ${cluster}` : ""}` };
    }
    case "storage_class": return { name: str(r.storage_class), sub: null };
    case "cluster": return { name: str(r.cluster), sub: null };
  }
}

// 격자(머리·행 공용). xl 미만 두 줄: [비중|대상|파일 …|실 사용량|›] -- 이름은 1~3열, 실 사용량은 4열(1줄), › 는 5열(두 줄).
// lg~xl 은 둘째 줄 4열(실 사용량 아래)에 hot 이 보이고 파일은 3열만 쓴다. xl 이상: 이름(남는 폭) · 대상 · 실 사용량 · 비중 · 파일 ·
// hot · ›(고정 폭 -- 행마다 열이 맞는다). 파일 열은 10자리 개수 + 「이상」이 한 줄에 들어가는 폭(8.5rem).
const GRID = "grid items-center gap-x-2 px-3 grid-cols-[auto_auto_minmax(0,1fr)_auto_auto] "
  + "xl:gap-x-3 xl:grid-cols-[minmax(0,1fr)_4.5rem_6.5rem_8.5rem_8.5rem_4.5rem_1rem]";
const C = {
  name: "col-start-1 col-end-4 row-start-1 min-w-0 xl:col-end-2",
  targets: "col-start-2 row-start-2 xl:col-start-2 xl:row-start-1",
  bytes: "col-start-4 row-start-1 xl:col-start-3",
  share: "col-start-1 row-start-2 xl:col-start-4 xl:row-start-1",
  files: "col-start-3 col-end-5 row-start-2 min-w-0 lg:col-end-4 xl:col-start-5 xl:col-end-6 xl:row-start-1",
  hot: "max-lg:sr-only lg:col-start-4 lg:row-start-2 lg:text-right xl:col-start-6 xl:row-start-1",
  chev: "col-start-5 row-start-1 row-span-2 xl:col-start-7 xl:row-span-1",
};
const NUM = "whitespace-nowrap tabular-nums xl:text-right";

function ShareBar({ bytes, whole, partial, wholePartial }: {
  bytes: number | null; whole: number | null; partial: boolean; wholePartial: boolean;
}) {
  const text = shareText(bytes, whole);
  if (text === null || bytes === null || whole === null) return <div role="cell" className={C.share} />;
  const pct = (bytes / whole) * 100;
  const width = Math.min(100, Math.max(pct, pct > 0 ? 1 : 0));
  // 분모가 부분 합(하한)이면 「X 이상 대비」 + 근사(% 는 과대일 수 있다) -- 묶음 값만 부분 합이면 예전처럼 「(부분 합)」.
  const note = wholePartial ? "(부분 합끼리라 근사)" : partial ? "(부분 합)" : "";
  return (
    <div role="cell" className={`${C.share} flex items-center gap-1.5 whitespace-nowrap`}
         title={`이 스토리지 합계 ${humanBytes(whole)}${wholePartial ? " 이상" : ""} 대비${note}`}>
      <span aria-hidden className="h-1.5 w-12 overflow-hidden rounded-full bg-line/70 xl:w-auto xl:flex-1">
        <span className="block h-full rounded-full bg-ink/40" style={{ width: `${width}%` }} /></span>
      <span className="text-xs tabular-nums text-muted xl:w-11 xl:text-right">{text}</span>
    </div>
  );
}

type Kind = "rank" | "outside" | "unknown" | "none";

interface RowProps {
  r: UsageGroupRow; kind: Kind; by: UsageBreakdownBy; storage: string; whole: number | null; wholePartial: boolean;
  truncated: boolean; windowLimit: number; multiCluster: boolean; rank?: number;
}

// 메모: 이어 읽기(1초 재조회) 동안 바뀌지 않은 행은 다시 그리지 않는다(react-query 의 구조 공유가 같은 행 객체를 남긴다 -- 「모두
// 보기」로 2,000행을 펼친 채 1초마다 전부 다시 그리지 않게).
const GroupRow = memo(function GroupRow({
  r, kind, by, storage, whole, wholePartial, truncated, windowLimit, multiCluster, rank,
}: RowProps) {
  const navigate = useNavigate();
  const href = pathsHref(storage, r.filter);
  const bytes = sumOf(r.total_bytes), files = sumOf(r.total_files);
  const t = { truncated };
  const bytesPartial = partialSum(t, r.total_bytes, bytes), filesPartial = partialSum(t, r.total_files, files);
  const targets = isCount(r.target_count) ? r.target_count : null;
  // 창이 넘쳤으면 대상 수는 창 안에서 센 하한 -- 「+」와 title 의 창 기준(CompareTable TargetsCell 과 같은 문구).
  const targetsMore = truncated && targets !== null;
  const targetsTitle = [truncated && (windowLimit > 0 ? `합계는 최근 스캔된 ${n(windowLimit)}개 대상 기준 부분 합`
                                                        : "합계는 최근 스캔된 대상 기준 부분 합"),
                        isCount(r.summed_target_count) && `최상위 ${n(r.summed_target_count)}개 합계`,
                        isCount(r.nested_target_count) && r.nested_target_count > 0 && `하위 ${n(r.nested_target_count)}개 제외`]
    .filter(Boolean).join(" · ");
  const extra = kind !== "rank";
  // 이름 칸: 순위 행은 묶음 이름(+ 부속), 덧붙임은 고정 라벨(흐림·기울임).
  const { name, sub } = extra ? { name: null, sub: null } : groupName(by, r, multiCluster);
  const none = kind === "none" ? NONE_LABEL[by] : undefined;
  const label = kind === "outside" ? SCOPE_LABEL.outside : kind === "unknown" ? SCOPE_LABEL.unknown
    : none !== undefined ? none.label : name === null ? "—" : name === "" ? "(이름 없음)" : name;
  // 부속이 있으면 「이름 — 부속」이 링크 이름·title 이다(머리 주석 -- 같은 이름의 링크가 여럿이어도 목적지를 가린다).
  const full = sub !== null ? `${label} — ${sub}` : label;
  const title = kind === "outside" ? PV_OUTSIDE_TITLE : kind === "unknown" ? PV_UNKNOWN_TITLE : full;
  const nameCls = extra || name === "" ? "italic" : "";
  const textCls = extra ? "text-muted" : "text-ink";
  return (
    <div role="row" data-rank={rank} onClick={href !== null ? () => navigate(href) : undefined}
         className={`${GRID} gap-y-1 border-t border-line py-2 ${href !== null ? "group cursor-pointer hover:bg-panel" : ""}
                     ${extra ? "text-muted" : ""}`}>
      {/* 이름이 먼저다: 이름은 줄지 않고(칸 폭까지만 -- 넘으면 말줄임) 부속이 먼저 줄어든다(390 에서 긴 namespace 가 PV 이름을
          서너 글자로 누르지 않게, 실 Chrome 확인). 전체는 title. */}
      <div role="cell" className={`${C.name} flex items-center gap-1.5`}>
        {href !== null ? (
          <Link to={href} title={title} aria-label={sub !== null ? full : undefined} onClick={(e) => e.stopPropagation()}
                className={`max-w-full shrink-0 truncate rounded focus-visible:outline-none focus-visible:ring-2
                            focus-visible:ring-accent ${extra ? "text-muted hover:underline" : "text-accent hover:underline"}
                            ${nameCls}`}>
            {label}
          </Link>
        ) : (
          <span className={`max-w-full shrink-0 truncate ${textCls} ${nameCls}`} title={title}>{label}</span>
        )}
        {none !== undefined && <HelpTip label={`${none.label} 설명`} head={none.label} help={none.help} className="-my-1" />}
        {sub !== null && (
          <span aria-hidden={href !== null ? true : undefined} className="min-w-0 truncate text-xs text-muted" title={sub}>{sub}</span>
        )}
      </div>
      <div role="cell" className={`${C.targets} ${NUM} ${targets === null ? "text-muted" : ""}`}
           title={targetsTitle === "" ? undefined : targetsTitle}>
        <span className="xl:hidden">대상 </span>{targets === null ? "—" : n(targets)}{targetsMore && "+"}
        {targets !== null && <span className="xl:hidden">개</span>}
      </div>
      <div role="cell" className={`${C.bytes} ${NUM} text-right ${bytes === null ? "text-muted" : extra ? "" : "font-medium text-ink"}`}
           title={bytes === null ? undefined : `${n(bytes)} B`}>
        {bytes === null ? "—" : humanBytes(bytes)}{bytesPartial && MORE}
      </div>
      <ShareBar bytes={bytes} whole={whole} partial={bytesPartial} wholePartial={wholePartial} />
      {/* 파일 칸: 수만 말줄임(좁은 폭의 큰 수)이고 「이상」은 그 밖이라 늘 보인다 -- title 에도 「이상」(머리 주석). */}
      <div role="cell" className={`${C.files} flex items-baseline ${NUM} xl:justify-end`}
           title={files === null ? undefined : `${n(files)}개${filesPartial ? " 이상" : ""}`}>
        <span className="min-w-0 truncate">
          <span className="xl:hidden">파일 </span>{files === null ? "—" : n(files)}
          {files !== null && <span className="xl:hidden">개</span>}
        </span>
        {filesPartial && MORE}
      </div>
      <div role="cell" className={`${C.hot} ${NUM}`}>
        <span className="xl:hidden">hot </span>{hotText(r)}
      </div>
      {href !== null && <ChevronRight aria-hidden className={`${C.chev} h-4 w-4 justify-self-end text-muted/70 group-hover:text-ink`} />}
    </div>
  );
});

// 축의 묶음 키 필드(서버 breakdown_groups 의 키 -- 행 key 로 쓴다, 머리 주석).
const KEY_FIELDS: Record<UsageBreakdownBy, (keyof UsageGroupRow)[]> = {
  user: ["dms_user"], requester: ["requester_id"], namespace: ["cluster", "namespace"], pvc: ["cluster", "namespace", "pvc"],
  storage_class: ["storage_class"], cluster: ["cluster"],
};
/** 순위 행의 React key: 축의 묶음 키. 키 필드가 빠진 행(구형·부분 응답)끼리 겹치면 차례를 붙여 겹치지 않게 한다. */
export function rowKeys(by: UsageBreakdownBy, rows: UsageGroupRow[]): string[] {
  const seen = new Map<string, number>();
  return rows.map((r) => {
    const base = JSON.stringify(KEY_FIELDS[by].map((k) => r[k] ?? null));
    const c = seen.get(base) ?? 0;
    seen.set(base, c + 1);
    return c === 0 ? base : `${base}#${c}`;
  });
}

export interface GroupTableProps {
  by: UsageBreakdownBy; storage: string;
  rows: UsageGroupRow[];                       // 그릴 순위 행(호출측이 「모두 보기」 전 100행으로 자른다)
  extras: { outside: UsageGroupRow | null; unknown: UsageGroupRow | null; none: UsageGroupRow | null };
  whole: number | null;                        // 비중 막대의 분모(storage_total.total_bytes.sum)
  wholePartial: boolean;                       // 분모가 부분 합(값 모름·창 넘침 -- 「X 이상 대비」, 근사)
  truncated: boolean;                          // 판정 창이 넘쳤다(값이 부분 합 -- 「이상」, 대상 수 「+」)
  windowLimit: number;                         // 판정 창 크기(0 = 모름 -- 대상 칸 title 의 창 기준)
  multiCluster: boolean;                       // 창의 클러스터가 둘 이상(부속에 클러스터)
}

export const GroupTable = forwardRef<HTMLDivElement, GroupTableProps>(function GroupTable(
  { by, storage, rows, extras, whole, wholePartial, truncated, windowLimit, multiCluster }, ref) {
  const common = { by, storage, whole, wholePartial, truncated, windowLimit, multiCluster };
  const tableRef = useRef<HTMLDivElement>(null);
  useImperativeHandle(ref, () => tableRef.current!, []);
  // 순위가 바뀌어 React 가 포커스 든 행을 옮기면(insertBefore) 브라우저가 포커스를 <body> 로 떨어뜨린다(머리 주석) -- 이 렌더 직전에
  // 표 안에 포커스가 있었고 커밋 뒤 <body> 로 떨어졌으면 그 요소로 되돌린다. 사용자가 다른 곳으로 옮긴 포커스는 건드리지 않는다.
  const focusedBefore = typeof document !== "undefined" && tableRef.current !== null
    && document.activeElement instanceof HTMLElement && tableRef.current.contains(document.activeElement)
    ? document.activeElement : null;
  useLayoutEffect(() => {
    if (focusedBefore === null || !focusedBefore.isConnected) return;
    const a = document.activeElement;
    if (a === null || a === document.body) focusedBefore.focus({ preventScroll: true });
  });
  const keys = rowKeys(by, rows);
  return (
    // tabIndex -1: 「모두 보기」 뒤 새로 보인 첫 행에 링크가 없으면(필터 없는 행) 포커스가 표 자체로 온다(BreakdownPage).
    <div ref={tableRef} role="table" tabIndex={-1} aria-label={`${BY_LABEL[by]}별 실 사용량 순위`}
         className="rounded-card border border-line bg-surface text-sm outline-none">
      <div role="rowgroup">
        <div role="row" className={`${GRID} py-2 text-xs font-medium text-muted max-xl:sr-only`}>
          <div role="columnheader" className="xl:col-start-1">{BY_LABEL[by]}</div>
          <div role="columnheader" className="whitespace-nowrap xl:col-start-2 xl:text-right">대상</div>
          {/* 실 사용량 머리는 값 칸과 비중 막대 칸 둘의 머리다(막대는 그 값의 비중 -- aria-colspan). 글자는 숫자 위(오른쪽 맞춤). */}
          <div role="columnheader" aria-colspan={2} className="whitespace-nowrap xl:col-start-3 xl:text-right">실 사용량</div>
          <div role="columnheader" className="whitespace-nowrap xl:col-start-5 xl:text-right">파일</div>
          <div role="columnheader" className="whitespace-nowrap xl:col-start-6 xl:text-right">hot 비율</div>
        </div>
      </div>
      <div role="rowgroup">
        {rows.map((r, i) => <GroupRow key={keys[i]} r={r} kind="rank" rank={i} {...common} />)}
        {extras.outside !== null && <GroupRow r={extras.outside} kind="outside" {...common} />}
        {extras.unknown !== null && <GroupRow r={extras.unknown} kind="unknown" {...common} />}
        {extras.none !== null && NONE_LABEL[by] !== undefined && <GroupRow r={extras.none} kind="none" {...common} />}
      </div>
    </div>
  );
});
