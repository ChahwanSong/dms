import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { CircleCheck, Search, TriangleAlert } from "lucide-react";
import { InfoPanel } from "../../components/ui/InfoPanel";
import { Segmented } from "../../components/ui/Segmented";
import { ApiError } from "../../lib/api";
import { useNow } from "../../lib/useNow";
import { useSearchField } from "../jobs/searchField";
import { StateChip } from "./MountCell";
import { MountMatrix } from "./MountMatrix";
import { NodeMountCards } from "./NodeMountCards";
import { NodeMountSheet } from "./NodeMountSheet";
import {
  ABSENT_NOWHERE_META, CELL_META, POD_ROLE_LABELS, columnFacts, columnProblem, filterColumns, nowhereColumnCount, podCheckText,
} from "./mountTerms";
import type { CellState, MountRow, MountStatusResponse, MountTotals, StorageColumn } from "./types";
import { useMountStatus } from "./useMountStatus";

// 스토리지 > 마운트 현황(2026-11-23 사용자 요청 「노드들의 스토리지 마운트 현황을 한눈에 보고 싶어」). 공용 코어 페이지 -- dms-ssc 와
// 같은 화면이고, 사이트 덧붙임(dms-k8s 의 스토리지 종류·PV 수·k8s 호출 노드 꼬리표·PV 안내)은 슬롯 넷으로만 받는다
// (renderStorageMeta·renderStorageTip·roleLabels·footer -- k8sMountParts.K8sMountStatusPage; renderStorageTip 은 열 머리 툴팁과 노드
// 시트 스토리지 줄 둘에 -- 툴팁은 마우스 전용). 이 파일은 features/k8s 를 import 하지 않는다(이식 경계 그물 mountTerms.test).
// **이식 의존**: 스토리지 찾기칸은 `../jobs/searchField`(useSearchField -- react 만 쓰는 훅 하나)를 쓰는데 그 파일은 dms-ssc 에 없다
// (2026-10-25 dms-k8s 「전체 작업」 검색칸) -- dms-ssc 로 옮길 때 features/jobs/searchField.ts 를 함께 가져간다(mountTerms.test 의
// 이식 의존 그물이 코어의 바깥 import 를 목록으로 묶는다).
//
// 같은 날 사고(공용 스토리지가 마운트되지 않은 노드에 dms-api 가 떠 아티팩트를 못 읽음)의 그런 노드를 찾는 도구이기도 하다 -- 「DMS
// 공용」 열이 노드마다 공용 디렉터리를 덮는 마운트를 보고, 로컬 디렉터리·다른 노드와 다름을 문제로 센다. dms-api·컨트롤러 파드가 뜬
// 노드는 행 꼬리표(roles 의 api_pod·controller_pod -- 서버 mount_status_pods, 재료는 각 파드의 아티팩트 경로 점검 기록)로 보이고,
// 공용 디렉터리 칸이 문제인 노드거나 그 파드가 경로를 못 읽으면 꼬리표가 경고(빨강 + 아이콘)가 된다 -- 그 조합이 사고의 서명이다.
// 표에 없는 노드(에이전트 없음 -- 예: prod 의 control-plane 마스터)에 뜬 파드는 각주가 따로 말한다.
//
// 보기 상태는 URL 에 남긴다(공유·새로고침에 유지, replace): ?only=problems(문제 있는 노드만) · ?view=table|cards · ?q=(스토리지 이름·
// 경로 찾기 -- 「전체 작업」 검색칸과 같은 300ms 디바운스·한글 조합 처리, 주소로 들어온 값은 첫 렌더부터) · ?cols=problems(문제 있는
// 스토리지만 -- mountTerms.columnProblem). 열 거르기(2026-11-25 「스토리지가 100개가 될 수도 있어」)는 표와 카드에 걸고, 노드 시트는
// 거르지 않는다(노드 상세는 모든 스토리지). 표와 카드는 조건부로 그린다(CSS 로 숨기지 않는다 -- 숨은 표가 DOM 에 남으면 e2e 칸 수
// 측정이 공허해진다). view 가 없으면 첫 렌더의 화면 폭(640 미만 = 카드)으로 고른다.

export interface MountStatusPageProps {
  renderStorageMeta?: (col: StorageColumn) => ReactNode;
  /** 스토리지 열 머리 툴팁의 덧붙임(머리에 다 싣지 못하는 사이트 정보 -- dms-k8s 의 PV 수·클러스터·StorageClass). */
  renderStorageTip?: (col: StorageColumn) => ReactNode;
  roleLabels?: Record<string, string>;
  footer?: (data: MountStatusResponse) => ReactNode;
}

type View = "table" | "cards";
const NARROW = "(max-width: 639px)";

// 범례: 칸과 같은 칩 + 개수(신선한 칸은 「칸」, 모름만 리포트 지연 노드 「대」 -- 단위를 붙여 노드 수와 헷갈리지 않게) + 짧은 뜻.
// 없음의 뜻은 서버 「문제 있는 노드」 열 규칙과 같다(어느 노드에도 마운트 흔적이 없는 스토리지의 없음은 문제 -- 칸은 경고 아이콘·빨강,
// 범례 줄에도 그 칩을 함께 그린다: 표의 칩 모양과 1:1).
const LEGEND: { state: CellState; text: string; count: (t: MountTotals) => string }[] = [
  { state: "ready", text: "마운트·읽기 가능", count: (t) => `${t.ready}칸` },
  { state: "read_only", text: "sync 목적지·rm 제외", count: (t) => `${t.read_only}칸` },
  { state: "absent", text: "경로 없음(안 붙인 노드면 정상 · 경고 아이콘·빨강 = 어느 노드에도 마운트 흔적이 없어 문제)", count: (t) => `${t.absent}칸` },
  { state: "broken", text: "마운트 빠짐·끊긴 마운트·전파 안 됨·읽기 불가·점검 불가", count: (t) => `${t.broken}칸` },
  { state: "pending", text: "다음 보고에 반영", count: (t) => `${t.pending}칸` },
  { state: "stale", text: "리포트 지연", count: (t) => `${t.stale_nodes}대` },
];

const NO_COLUMNS: StorageColumn[] = [];
const NO_ROWS: MountRow[] = [];

function UpdatedAgo({ at }: { at: number }) {
  const now = useNow(5_000, at > 0);
  if (at <= 0) return null;
  const sec = Math.max(0, Math.round((now - at) / 1000));
  return (
    <span className="whitespace-nowrap text-xs text-muted">
      {`갱신 ${sec < 60 ? `${sec}초` : `${Math.floor(sec / 60)}분`} 전`}
    </span>
  );
}

function Tile({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="min-w-0 rounded-card border border-line bg-surface p-3">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-2xl font-semibold tabular-nums">{value}</div>
      {sub !== undefined && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
    </div>
  );
}

/** 원소가 모두 같은(===) 새 배열이면 앞 배열을 돌려준다 -- 걸러 낸 열 목록이 폴링마다 새 배열이어도 표 머리·행 memo 가 깨지지 않게. */
function useStableList<T>(list: T[]): T[] {
  const ref = useRef(list);
  const prev = ref.current;
  if (prev !== list && (prev.length !== list.length || prev.some((x, i) => x !== list[i]))) ref.current = list;
  return ref.current;
}

export function MountStatusPage({ renderStorageMeta, renderStorageTip, roleLabels, footer }: MountStatusPageProps) {
  const q = useMountStatus();
  const data = q.data;
  const [params, setParams] = useSearchParams();
  const onlyProblems = params.get("only") === "problems";
  const problemCols = params.get("cols") === "problems";
  const [initialView] = useState<View>(() =>
    (typeof window !== "undefined" && window.matchMedia?.(NARROW)?.matches ? "cards" : "table"));
  const viewParam = params.get("view");
  const view: View = viewParam === "cards" || viewParam === "table" ? viewParam : initialView;
  const [openNode, setOpenNode] = useState<string | null>(null);
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const registerButton = useCallback((name: string) => (el: HTMLButtonElement | null) => {
    if (el) buttons.current.set(name, el); else buttons.current.delete(name);
  }, []);
  const closedNode = useRef<string | null>(null);

  const setParam = (key: string, value: string | null) => setParams((p) => {
    const next = new URLSearchParams(p);
    if (value === null) next.delete(key); else next.set(key, value);
    return next;
  }, { replace: true });

  // 스토리지 찾기(?q=): 적용값 → 주소, 밖에서 주소가 바뀌면(사이드바 링크·뒤로 가기) 칸을 주소에 맞춘다. lastUrlQ = 이 화면이 마지막으로
  // 본(또는 쓴) 주소 값 -- 자기가 쓴 주소 변화에는 칸을 되돌리지 않고, 적용값이 그 값과 같으면 주소를 다시 쓰지 않는다(주소 갱신은
  // 라우터 전환이라 적용값보다 한 박자 늦게 온다 -- 그 사이의 옛 주소와 비교하면 옛 주소로 덮어쓴다: 「조건 지우기」가 cols 를
  // 되살렸다).
  const urlQ = params.get("q") ?? "";
  const search = useSearchField(urlQ);
  const lastUrlQ = useRef(urlQ.trim());
  useEffect(() => {
    if (urlQ.trim() === lastUrlQ.current) return;
    lastUrlQ.current = urlQ.trim();
    if (urlQ.trim() !== search.applied) search.set(urlQ);
  }, [urlQ]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const want = search.applied;
    if (want === lastUrlQ.current) return;
    lastUrlQ.current = want;
    setParam("q", want === "" ? null : want);
  }, [search.applied]); // eslint-disable-line react-hooks/exhaustive-deps
  const clearColumnFilter = () => {
    lastUrlQ.current = "";
    search.set("");
    setParams((p) => {
      const next = new URLSearchParams(p);
      next.delete("q");
      next.delete("cols");
      return next;
    }, { replace: true });
  };

  const t = data?.totals;
  const allCols = data?.storages ?? NO_COLUMNS;
  const allRows = data?.nodes ?? NO_ROWS;
  const rows = onlyProblems ? allRows.filter((r) => r.problem) : allRows;
  // nowhereCols = 어느 노드에도 마운트 흔적이 없는 스토리지 수 -- 그 칸은 상태가 absent 라 서버 totals.broken 에 없지만 문제다(빨간
  // 「없음」 칩). 「마운트 문제」 타일이 broken 만 보면 그 칸·「문제 n」 꼬리표 옆에서 초록 「없음」이라고 말한다(같은 날 이식 리뷰).
  const { problemColCount, nowhereCols } = useMemo(() => {
    const facts = columnFacts(allRows);
    return { problemColCount: allCols.filter((c) => columnProblem(c, allRows, facts)).length,
             nowhereCols: nowhereColumnCount(allCols, allRows, facts) };
  }, [allCols, allRows]);
  const visibleCols = useStableList(useMemo(
    () => filterColumns(allCols, allRows, { q: search.applied, problemsOnly: problemCols }),
    [allCols, allRows, search.applied, problemCols]));
  const colFiltered = search.applied !== "" || problemCols;
  const openRow = data && openNode !== null ? data.nodes.find((r) => r.node_name === openNode) ?? null : null;
  const dash = "–";

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-2xl font-bold">마운트 현황</h1>
        <UpdatedAgo at={q.dataUpdatedAt} />
      </div>
      <p className="text-sm text-muted break-keep">
        노드마다 스토리지와 DMS 공용 디렉터리가 마운트돼 있는지 봅니다 — 각 노드 dms-agent 보고(약
        {` ${data?.report_interval_seconds ?? 60}`}초 주기) 기준이며 10초마다 갱신합니다.
      </p>
      {q.isError && data !== undefined && (
        <p role="status" className="rounded-card border border-attn/40 bg-attnbg px-3 py-2 text-sm text-attn">
          최근 갱신 실패 — 마지막으로 받은 값을 보여 줍니다
        </p>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile label="노드" value={t ? `${t.nodes}대` : dash}
              sub={t === undefined ? undefined : t.stale_nodes > 0
                ? <span className="font-medium text-bad">{`리포트 지연 ${t.stale_nodes}대`}</span>
                : t.nodes === 0 ? "보고한 노드 없음" : "모두 정상 보고"} />
        <Tile label="마운트 문제"
              value={t === undefined ? dash : t.broken === 0 && nowhereCols === 0
                ? <><CircleCheck className="h-5 w-5 text-ok" aria-hidden /><span className="text-ok">없음</span></>
                : (
                  <>
                    <TriangleAlert className="h-5 w-5 text-bad" aria-hidden />
                    {t.broken > 0 && <span className="text-bad">{`${t.broken}칸`}</span>}
                    {nowhereCols > 0 && (
                      // 좁은 화면 2열 타일(약 165px)에 한 줄로 안 들어간다 -- nowrap 이면 타일 밖으로 넘친다, 낱말 단위로 꺾는다
                      <span className="min-w-0 break-keep text-sm font-semibold text-bad">
                        {`마운트 흔적 없는 스토리지 ${nowhereCols}개`}
                      </span>
                    )}
                  </>
                )}
              sub="마운트 빠짐·끊긴 마운트·전파 안 됨·읽기 불가·점검 불가" />
        <Tile label="읽기 전용" value={t ? `${t.read_only}칸` : dash} sub="sync 목적지·rm 배치에서 빠짐" />
        <Tile label="공용 디렉터리"
              value={t === undefined || data === undefined || data.shared_dir === null ? dash : (
                <>
                  <span className="whitespace-nowrap">{`정상 ${t.shared_ok}/${t.fresh_nodes}대`}</span>
                  {t.shared_problem > 0 && (
                    <span className="whitespace-nowrap text-sm font-semibold text-bad">{`문제 ${t.shared_problem}대`}</span>
                  )}
                </>
              )}
              sub={data?.shared_dir
                ? <span className="block truncate font-mono" title={data.shared_dir.path}>{data.shared_dir.state_root ?? data.shared_dir.path}</span>
                : data ? "아티팩트 경로 없음" : undefined} />
      </div>

      {q.isLoading ? (
        <p className="text-muted">불러오는 중…</p>
      ) : data === undefined ? (
        <p className="text-bad">{q.error instanceof ApiError || q.error instanceof Error ? q.error.message : "불러오지 못했습니다"}</p>
      ) : data.nodes.length === 0 ? (
        <InfoPanel className="break-keep">
          보고한 노드가 없습니다 — 에이전트(DaemonSet dms-agent)가 떠 있는지 확인하세요.{" "}
          <Link to="/admin/nodes" className="text-accent hover:underline">노드 화면</Link>
        </InfoPanel>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Segmented label="노드 범위" value={onlyProblems ? "problems" : "all"}
                       onChange={(v) => setParam("only", v === "problems" ? "problems" : null)}
                       options={[{ value: "all", label: `전체 노드 ${data.nodes.length}` },
                                 { value: "problems", label: `문제 있는 노드 ${t!.problem_nodes}` }]} />
            {data.storages.length > 0 && (
              <>
                <Segmented label="스토리지 범위" value={problemCols ? "problems" : "all"}
                           onChange={(v) => setParam("cols", v === "problems" ? "problems" : null)}
                           options={[{ value: "all", label: `전체 스토리지 ${data.storages.length}` },
                                     { value: "problems", label: `문제 있는 스토리지 ${problemColCount}` }]} />
                <label className="relative w-full sm:w-56">
                  <span className="sr-only">스토리지 찾기</span>
                  <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
                  <input {...search.bind} type="search" maxLength={128} placeholder="스토리지 이름·경로 찾기"
                         className="w-full rounded-lg border border-line bg-surface py-1.5 pl-8 pr-3 text-sm placeholder:text-muted
                                    focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20" />
                </label>
                {colFiltered && (
                  <span className="whitespace-nowrap text-xs tabular-nums text-muted">
                    {`스토리지 ${visibleCols.length}/${data.storages.length}`}
                  </span>
                )}
              </>
            )}
            <Segmented label="보기 방식" value={view} onChange={(v) => setParam("view", v)}
                       options={[{ value: "table", label: "표" }, { value: "cards", label: "노드별" }]} />
          </div>
          {colFiltered && (
            // 열 거르기는 표·카드의 칸만 줄인다 -- 서버 수(타일·범례·노드 범위·행의 「문제 n」 = problem_count)는 숨긴 스토리지까지 센다.
            // 이 줄이 없으면 「문제 3」 꼬리표 옆에 정상 칸만 보이는 행(?only=problems&q=…)이 모순처럼 읽혔다(2026-11-25 리뷰).
            <p className="text-xs text-muted break-keep">
              스토리지를 거른 보기입니다 — 표·카드의 칸만 거른 스토리지로 그리고, 타일·범례·노드 범위 수와 노드의 「문제 n」 꼬리표는
              숨긴 스토리지까지 센 전체 기준입니다.
            </p>
          )}
          <ul aria-label="범례" className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
            {LEGEND.map(({ state, text, count: countOf }) => {
              const meta = CELL_META[state];
              const count = countOf(t!);
              return (
                <li key={state} className="flex items-center gap-1.5">
                  <StateChip Icon={meta.Icon} label={meta.label} tone={meta.tone} />
                  {state === "absent" && (
                    <StateChip Icon={ABSENT_NOWHERE_META.Icon} label={ABSENT_NOWHERE_META.label} tone={ABSENT_NOWHERE_META.tone} />
                  )}
                  <span className="tabular-nums font-medium">{count}</span>
                  <span className="text-muted">{text}</span>
                </li>
              );
            })}
          </ul>

          {data.storages.length === 0 && (
            <InfoPanel className="break-keep">
              점검할(활성) 스토리지가 없습니다 — <Link to="/admin/storages" className="text-accent hover:underline">스토리지
              화면</Link>에서 등록하거나 켜세요.
            </InfoPanel>
          )}
          {data.storages.length > 0 && colFiltered && visibleCols.length === 0 && (
            // 열이 0개여도 표·카드는 공용 열과 행으로 계속 그린다(공용 디렉터리 문제는 열 거르기와 무관하게 보인다).
            <InfoPanel className="flex flex-wrap items-center gap-x-3 gap-y-1 break-keep">
              <span>
                {"조건에 맞는 스토리지가 없습니다 — "
                  + [search.applied !== "" ? `「${search.applied}」` : null, problemCols ? "문제 있는 스토리지만" : null]
                    .filter((x) => x !== null).join(" · ")}
              </span>
              <button type="button" onClick={clearColumnFilter} className="text-accent hover:underline">조건 지우기</button>
            </InfoPanel>
          )}

          {rows.length === 0 ? (
            <InfoPanel className="flex items-start gap-2 break-keep">
              {t!.stale_nodes === 0 ? (
                <>
                  <CircleCheck className="mt-0.5 h-4 w-4 shrink-0 text-ok" aria-hidden />
                  {t!.read_only > 0
                    ? "문제 있는 노드가 없습니다 — 신선한 보고 기준으로 모든 칸이 정상·없음·확인 대기·읽기 전용입니다(읽기 전용은 "
                      + "같은 스토리지를 쓰기 가능으로 붙인 노드가 있을 때만 문제로 셉니다 — 모든 노드에 ro 로 붙인 스토리지는 의도된 "
                      + "구성)."
                    : "문제 있는 노드가 없습니다 — 신선한 보고 기준으로 모든 칸이 정상·없음·확인 대기입니다."}
                </>
              ) : "보기 범위에 맞는 노드가 없습니다."}
            </InfoPanel>
          ) : view === "table" ? (
            <MountMatrix data={data} rows={rows} cols={visibleCols} renderStorageMeta={renderStorageMeta}
                         renderStorageTip={renderStorageTip} roleLabels={roleLabels} onOpenNode={setOpenNode}
                         registerButton={registerButton} />
          ) : (
            <NodeMountCards data={data} rows={rows} cols={visibleCols} roleLabels={roleLabels} onOpenNode={setOpenNode}
                            registerButton={registerButton} />
          )}

          <Footnotes data={data} />
          {footer?.(data)}
          <NodeMountSheet row={openRow} data={data} roleLabels={roleLabels} renderStorageTip={renderStorageTip}
                          onClose={() => { closedNode.current = openNode; setOpenNode(null); }}
                          onCloseAutoFocus={(e) => {
                            const name = closedNode.current;
                            const el = name !== null ? buttons.current.get(name) : undefined;
                            if (el) { e.preventDefault(); el.focus(); }
                          }} />
        </>
      )}
    </section>
  );
}

function Footnotes({ data }: { data: MountStatusResponse }) {
  const notes: ReactNode[] = [];
  if (data.disabled_storages.length > 0)
    notes.push(`비활성 스토리지 ${data.disabled_storages.length}개(${data.disabled_storages.join(", ")})는 에이전트가 점검하지 않아 `
      + "표에서 뺐습니다.");
  if (data.cluster.known && data.cluster.agentless_nodes.length > 0)
    notes.push(`에이전트가 없는 k8s 노드 ${data.cluster.agentless_nodes.length}대: `
      + data.cluster.agentless_nodes.map((n) => `${n.node_name}${n.control_plane ? "(control-plane)" : ""}`).join(", ")
      + " — 이 노드의 마운트는 DMS 가 모르고 DMS 작업도 배치되지 않습니다. control-plane taint 처럼 DaemonSet 이 뜨지 않는 노드는 "
      + "정상이고, taint 가 없는데 여기 있으면 dms-agent 파드를 확인하세요.");
  if (!data.cluster.known)
    notes.push("k8s 노드 목록을 읽지 못해 에이전트 없는 노드·control-plane 여부를 표시하지 않습니다.");
  // 표 밖 노드의 DMS 파드: 행이 없으니 꼬리표를 달 곳이 없다(prod 는 제어면을 control-plane 마스터에 두고 에이전트는 그 taint 를
  // 견디지 않는다). 그 노드의 마운트는 모르고, 파드 자신의 점검만 안다 -- 못 읽는 파드는 빨갛게.
  const outside = (data.control_pods ?? []).filter((p) => !p.in_table);
  if (outside.length > 0)
    notes.push(<>
      {"표에 없는 노드(에이전트 보고 없음)에 뜬 DMS 파드: "}
      {outside.map((p, i) => (
        <span key={`${p.role}-${p.instance ?? ""}-${p.node_name}`}>
          {i > 0 && ", "}
          <span className={p.read_failed ? "font-medium text-bad" : undefined}>
            {`${POD_ROLE_LABELS[p.role] ?? p.role} ${p.node_name}(${podCheckText(p)})`}
          </span>
        </span>
      ))}
      {" — 이 노드의 마운트는 DMS 가 모릅니다(파드 자신의 점검은 "}
      <Link to="/admin/artifact-base" className="text-accent hover:underline">아티팩트 경로</Link>
      {" 화면)."}
    </>);
  if (data.totals.shared_unverified > 0)
    notes.push("일부 노드는 공용 디렉터리의 마운트 지점을 보고하지 않습니다(에이전트 업데이트 전) — 같은 경로의 로컬 디렉터리도 "
      + "「있음」으로 보일 수 있습니다.");
  notes.push(<>마운트가 정상이어도 도구·배치 제외·cordon·CPU 때문에 작업이 배치되지 않을 수 있습니다 —{" "}
    <Link to="/admin/nodes" className="text-accent hover:underline">노드 화면</Link>에서 확인하세요.</>);
  return (
    <InfoPanel className="space-y-1 text-xs text-muted break-keep">
      {notes.map((n, i) => <p key={i}>{n}</p>)}
    </InfoPanel>
  );
}
