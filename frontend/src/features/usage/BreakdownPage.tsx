import { Fragment, useEffect, useId, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Info, ScanSearch } from "lucide-react";
import { Button } from "../../components/ui/Button";
import { HelpTip } from "../../components/ui/HelpTip";
import { Skeleton } from "../../components/ui/Skeleton";
import { ApiError } from "../../lib/api";
import type { UsageBreakdownBy, UsageScope } from "../../lib/types";
import { SELECT_FIELD } from "../k8s/parts";
import {
  BREAKDOWN_API_UNKNOWN, BREAKDOWN_BYS, PV_BYS, UsageContractError, isBreakdownBy, useUsageBreakdown,
} from "./useUsage";
import { useActiveStorage } from "./useActiveStorage";
import { storageLabel, type StorageTab } from "./usageTabs";
import { UsageHeader } from "./UsageHeader";
import { ScopeBar } from "./StorageSelect";
import { BY_LABEL, GroupTable } from "./GroupTable";
import { SCOPE_LABEL } from "./UsageFilters";
import { count, partialSum, sumOf } from "./StorageTotals";
import { humanBytes } from "./usageStats";
import { Empty, NoStorages, sourceNotices } from "./UsageParts";

// 사용량 분석 「소속별」(2026-10-30 2단계, 사용자 결정 「그래 구현시작해.」 -- 설계 ia-final §3.4). 한 스토리지를 누가(권한 사용자·
// 요청자) 어느 namespace·PV 이름·StorageClass·클러스터가 얼마나 쓰는지 **실 사용량 순위**로 본다(서버 /breakdown -- 묶음마다 경로별
// 필터 합계와 같은 정의·같은 값). 행을 누르면 경로별로 가면서 그 묶음의 필터가 걸린다(GroupTable).
//
// 구성: 머리(h1 = 탭·크럼 라벨) → 알림 자리(원천 실패 · 없는 storage 안내) → 범위 줄(경로별과 같은 StorageSelect + 메타 줄) → 패널
// (BreakdownPanel: 기준 줄 · 순위 표 · 각주). 바깥 카드는 없다(경로별·스토리지별과 같다).
//
// URL 이 진실이다: ?storage= = 보는 스토리지(useActiveStorage -- 경로별과 같은 규칙: 기본 스토리지 파생·붙잡기, 없는 storage 는
// replace 로 정리하고 안내, select 300ms 디바운스 push), ?by= = 기준(replace -- 뒤로 가기가 기준 바꿈을 하나하나 되짚지 않게,
// 「권한 사용자」(기본)는 생략). 스토리지를 바꿔도 by 는 남는다(같은 기준으로 스토리지끼리 비교). 기준은 필터가 아니다 -- 숫자를
// 거르지 않고 묶는 방법만 바꾼다(필터는 경로별에만, ia-final §4.2).
//
// 보이는 기준 = 서버가 적용한 기준(응답 by): PV 안 대상이 없는 스토리지(filesystem·미등록)에선 PV 축을 골라도 서버가 요청자로 적용하고,
// 화면은 PV 축 다섯을 disabled 로 두고 그 사실을 한 문장으로 말한다(URL 은 건드리지 않는다 -- PV 스토리지로 바꾸면 그 기준이 다시
// 걸린다). 응답 전엔 URL 값(없으면 권한 사용자).

// 기준 설명(FILTERS_HELP 와 같은 용어 -- 권한 사용자 = RoleBinding, 요청자 = scan 제출 계정, PV 이름 = PVC).
export const BREAKDOWN_HELP = "각 경로가 지금 속한 PV(=PVC)·namespace·StorageClass·클러스터, 그 namespace 의 권한 사용자, 그 경로를 "
  + "scan 한 요청자로 묶습니다. 지금 인벤토리에 없는 PV 는 그 경로를 PV 주소로 scan 한 기록으로 봅니다. 권한 사용자는 지금 "
  + "RoleBinding 으로 그 namespace 에 접근하는 사용자이며 관리자·특권 운영자의 전체 접근은 포함하지 않습니다. 요청자는 그 경로를 "
  + "scan 한 적이 있는 제출 계정입니다(작업 목록의 요청자와 같은 기준). 한 경로가 여러 묶음에 들 수 있어 행끼리 더하지 않습니다.";
export const NO_PV_TEXT = "이 스토리지에는 PV 안 대상이 없습니다 — 요청자 기준만 볼 수 있습니다";
// 처음에 그리는 순위 행 수 -- 나머지는 「모두 보기」(서버 상한까지 받아 두었다).
export const SHOW_FIRST = 100;

const n = (x: number) => x.toLocaleString("ko-KR");
// 「·」 앞은 줄바꿈 없는 공백(StoragePanel·StorageTotals 각주 관용 -- 좁은 폭에서 접힌 줄이 「·」로 시작하지 않게).
const SEP = "\u00a0· ";

// 재조회 단추(TotalsLine 의 Retry 관용 -- 성공하면 단추가 사라지므로 BreakdownPanel 이 포커스를 구역으로 옮긴다).
function Retry({ onRetry }: { onRetry: () => void }) {
  return (
    <button type="button" onClick={onRetry}
            className="rounded text-xs text-accent hover:underline focus-visible:outline-none focus-visible:ring-2
                       focus-visible:ring-accent">다시 시도</button>
  );
}

/** 「PV 안 3 · PV 밖(직접 경로) 2 · 모름 0」 -- 조각마다 경로별의 그 구분 필터로(push). 0 인 조각은 갈 곳이 빈 목록이라 글자만. */
function ScopeLine({ storage, scope }: { storage: string; scope: { inventory: number; recorded: number; outside: number; unknown: number } }) {
  const parts: [UsageScope, number][] = [["pv", scope.inventory + scope.recorded], ["outside", scope.outside],
                                         ["unknown", scope.unknown]];
  return (
    <p className="text-sm text-muted">
      {parts.map(([k, c], i) => {
        const text = `${SCOPE_LABEL[k]} ${n(c)}`;
        const href = `/admin/usage?${new URLSearchParams({ storage, scope: k }).toString()}`;
        return (
          <Fragment key={k}>
            {i > 0 && SEP}
            {c > 0 ? (
              <Link to={href} className="whitespace-nowrap rounded hover:text-accent hover:underline focus-visible:outline-none
                                          focus-visible:ring-2 focus-visible:ring-accent">{text}</Link>
            ) : <span className="whitespace-nowrap">{text}</span>}
          </Fragment>
        );
      })}
    </p>
  );
}

function BreakdownPanel({ tab, by, onBy }: {
  tab: StorageTab; by: UsageBreakdownBy | null; onBy: (b: UsageBreakdownBy) => void;
}) {
  const storage = tab.name;
  const selectId = useId();
  const noPvId = useId();
  const q = useUsageBreakdown(storage, by);
  // [다시 시도] 포커스 지킴(TotalsLine 의 refocus 관용, 2026-10-30 리뷰): 재조회가 성공하면 단추가 오류 문구째 사라져 포커스가
  // <body> 로 떨어졌다 -- 그러면 이 구역(tabIndex -1, 이름 = 스토리지)으로 옮긴다(스크롤 없이). 재조회가 끝났는데 포커스가 살아
  // 있으면(다시 실패해 단추가 남았다·다른 곳으로 옮겼다) 건드리지 않고 잊는다.
  const sectionRef = useRef<HTMLElement>(null);
  const refocus = useRef(false);
  const retry = () => { refocus.current = true; void q.refetch(); };
  useEffect(() => {
    if (!refocus.current) return;
    const a = document.activeElement;
    if (a === null || a === document.body) { refocus.current = false; sectionRef.current?.focus({ preventScroll: true }); return; }
    if (!q.isFetching) refocus.current = false;
  });
  // data = 지금 기준의 응답, 또는 같은 스토리지에서 기준만 바꾸는 동안의 이전 응답(자리표시 -- 스토리지 단위인 PV 안 수·구분 줄만 쓴다).
  const data = q.data;
  const fresh = q.isPlaceholderData ? undefined : data;
  const pvIn = data === undefined ? null : data.scope.inventory + data.scope.recorded;
  const noPv = pvIn === 0;
  const shownBy: UsageBreakdownBy = fresh !== undefined ? fresh.by : noPv ? "requester" : (by ?? "user");
  const hasTargets = data === undefined ? null : typeof data.target_count === "number" ? data.target_count > 0 : null;
  const empty = fresh !== undefined && fresh.target_count === 0;
  // PV 없음 문장이 보일 때만 select 가 그 문장을 설명으로 잇는다(낭독기는 흐린 옵션만 듣고 이유를 못 들었다 -- 2026-10-30 리뷰).
  const showNoPv = data !== undefined && hasTargets === true && noPv;
  // 실패 기록만 남은 미등록 스토리지(성공 scan 0) -- 빈 상태가 「기록 정리」까지 말한다.
  const failedOnly = tab.unregistered !== null && tab.unregistered.target_count === 0;

  // 「모두 보기」: 이 기준에서 한 번 펼치면 그 기준 동안 남는다(기준을 바꾸면 다시 100행). 펼치면 단추가 사라진다 -- 포커스를 새로 보인
  // 첫 행의 링크로 옮긴다(<body> 로 떨어지지 않게).
  const [expandedBy, setExpandedBy] = useState<UsageBreakdownBy | null>(null);
  // 다른 기준의 응답이 오면 펼침을 잊는다(렌더 중 상태 조정 -- 돌아와도 다시 100행부터).
  if (fresh !== undefined && expandedBy !== null && expandedBy !== fresh.by) setExpandedBy(null);
  const expanded = fresh !== undefined && expandedBy === fresh.by;
  const tableRef = useRef<HTMLDivElement>(null);
  const focusAfterExpand = useRef(false);
  useEffect(() => {
    if (!expanded || !focusAfterExpand.current) return;
    focusAfterExpand.current = false;
    const el = tableRef.current?.querySelector<HTMLElement>(`[data-rank="${SHOW_FIRST}"] a`) ?? tableRef.current;
    el?.focus({ preventScroll: true });
  }, [expanded]);

  const reading = fresh !== undefined && count(fresh.pending_count) > 0 && fresh.progressed && !q.isError;
  const unknownApi = q.error instanceof UsageContractError && q.error.kind === "unknown_api";
  const errText = q.error instanceof UsageContractError || q.error instanceof ApiError ? ` — ${q.error.message}` : "";

  // 기준 줄: 「기준」 select(모든 폭 -- 좁은 폭에선 줄 전체) + 설명 · 구분 수(PV 안이 있을 때) 또는 PV 없음 문장 · 읽는 중.
  const controls = !empty && (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <div className="flex w-full min-w-0 items-center gap-2 sm:w-auto">
        <label htmlFor={selectId} className="shrink-0 text-sm text-ink/70">기준</label>
        <select id={selectId} value={shownBy} onChange={(e) => { if (isBreakdownBy(e.target.value)) onBy(e.target.value); }}
                aria-describedby={showNoPv ? noPvId : undefined}
                className={`${SELECT_FIELD} min-w-0 flex-1 max-sm:max-w-none sm:flex-none`}>
          {BREAKDOWN_BYS.map((b) => (
            <option key={b} value={b} disabled={noPv && PV_BYS.includes(b)}>{BY_LABEL[b]}</option>
          ))}
        </select>
        <HelpTip label="기준 설명" head="기준" help={BREAKDOWN_HELP} />
      </div>
      {data !== undefined && hasTargets === true && (noPv
        ? <p id={noPvId} className="flex items-center gap-1.5 text-sm text-muted break-keep">
            <Info aria-hidden className="h-4 w-4 shrink-0" />{NO_PV_TEXT}</p>
        : <ScopeLine storage={storage} scope={data.scope} />)}
      {reading && <span className="whitespace-nowrap text-xs text-muted">{`요약 읽는 중 ${n(count(fresh!.pending_count))}개`}</span>}
    </div>
  );

  let body: React.ReactNode;
  if (fresh === undefined) {
    if (q.isError && !q.isPlaceholderData) {
      body = unknownApi
        ? <p className="flex items-center gap-1.5 text-sm text-muted break-keep">
            <Info aria-hidden className="h-4 w-4 shrink-0" />{BREAKDOWN_API_UNKNOWN}</p>
        : <p className="inline-flex flex-wrap items-baseline gap-x-2 text-sm">
            <span className="text-bad [overflow-wrap:anywhere]">{`소속별 순위를 불러오지 못했습니다${errText}`}</span>
            <Retry onRetry={retry} /></p>;
    } else {
      body = (
        <div className="space-y-2.5 py-1">
          <span className="sr-only">불러오는 중…</span>
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-6" />)}
        </div>
      );
    }
  } else if (empty) {
    body = failedOnly
      ? <Empty icon={<ScanSearch className="h-5 w-5" />} title="성공한 scan 기록이 없습니다"
               hint="실패·취소된 작업 기록만 남아 있습니다 — 경로별의 「기록 정리」로 지울 수 있습니다." />
      : <Empty icon={<ScanSearch className="h-5 w-5" />} title="이 스토리지에는 아직 성공한 scan 이 없습니다"
               hint={<>scan 작업이 끝나면 묶음별 실 사용량이 여기에 쌓입니다.{" "}
                 <Link to="/jobs/new" className="text-accent hover:underline">단일 작업</Link></>} />;
  } else {
    const d = fresh;
    const whole = d.storage_total === null ? null : sumOf(d.storage_total.total_bytes);
    // 분모가 부분 합(값 모름을 뺀 합·창으로 잘린 합)이면 「X 이상 대비」 -- 막대 % 는 근사다(2026-10-30 리뷰: 합계 줄·스토리지별은 같은
    // 경우 「이상」을 붙이는데 분모만 정확값처럼 보였다).
    const wholePartial = d.storage_total !== null && partialSum({ truncated: d.window_truncated }, d.storage_total.total_bytes, whole);
    const shown = expanded ? d.rows : d.rows.slice(0, SHOW_FIRST);
    const hasExtras = d.extras.outside !== null || d.extras.unknown !== null || d.extras.none !== null;
    const anyUnknown = [...d.rows, d.extras.outside, d.extras.unknown, d.extras.none]
      .some((r) => r !== null && (count(r.total_bytes?.unknown) > 0 || count(r.total_files?.unknown) > 0));
    body = d.rows.length === 0 && !hasExtras ? (
      <Empty icon={<ScanSearch className="h-5 w-5" />} title="이 기준으로 묶인 대상이 없습니다" />
    ) : (<>
      <GroupTable ref={tableRef} by={d.by} storage={storage} rows={shown} extras={d.extras} whole={whole}
                  wholePartial={wholePartial} truncated={d.window_truncated} windowLimit={d.window_limit}
                  multiCluster={d.cluster_count >= 2} />
      {!expanded && d.rows.length > SHOW_FIRST && (
        <Button type="button" variant="ghost" className="py-1.5"
                onClick={() => { focusAfterExpand.current = true; setExpandedBy(d.by); }}>
          {`모두 보기 (${n(d.rows.length)}개)`}</Button>
      )}
      {d.rows_truncated && (
        <p className="text-xs text-attn break-keep">{`묶음이 많아 실 사용량 상위 ${n(d.rows.length)}개만 보입니다`}</p>
      )}
      {/* 각주: 행의 뜻(경로별 필터 합계와 같은 정의 · 행끼리 더하지 않음) + 용어 + 막대의 분모 + (넘쳤으면) 판정 창 + (있으면) 「이상」. */}
      <p className="text-xs text-muted break-keep">
        행끼리 더하지 않습니다 — 한 경로가 여러 사용자·요청자에 속합니다{SEP}묶음 합계 = 그 묶음에 맞는 경로 중 최상위 합(경로별 필터
        합계와 같은 정의){SEP}권한 사용자 = RoleBinding(관리자·특권 운영자의 전체 접근 제외){SEP}요청자 = 그 경로를 한 번이라도
        scan 한 계정
        {whole !== null && whole > 0
          && `${SEP}막대 = 이 스토리지 합계 ${humanBytes(whole)}${wholePartial ? " 이상 대비(근사)" : " 대비"}`}
        {/* 창 크기를 모르면(구형·부분 응답 -- 정규화가 0 으로 둔다) 수 없이 말한다. */}
        {d.window_truncated
          && `${SEP}${d.window_limit > 0 ? `최근 스캔된 ${n(d.window_limit)}개 기준` : "최근 스캔된 것 기준"}`}
        {anyUnknown && `${SEP}「이상」 = 값을 모르는 대상(요약 읽는 중 포함)을 뺀 합`}
      </p>
    </>);
  }

  return (
    <section ref={sectionRef} tabIndex={-1} aria-label={storageLabel(storage)} className="space-y-4 outline-none">
      {/* 미등록 스토리지: 범위 줄은 메타 줄을 숨긴다(경로별에선 패널의 경고 띠가 그 자리다) -- 여기엔 정리 단추가 없으니 흐린 한 줄로
          무엇의 기록인지와 정리할 곳만 말한다(2026-10-30 리뷰: 미등록이라는 사실이 select 글자에만 남았다). 실패 기록만 남은 빈 상태는
          빈 상태 안내가 같은 말을 한다. */}
      {!tab.registered && !(empty && failedOnly) && (
        <p className="flex items-center gap-1.5 text-sm text-muted break-keep">
          <Info aria-hidden className="h-4 w-4 shrink-0" />
          <span>
            미등록 스토리지 — 스토리지 목록에서 삭제된 스토리지의 기록입니다(기록 정리는{" "}
            <Link to={`/admin/usage?${new URLSearchParams({ storage }).toString()}`}
                  className="rounded text-accent hover:underline focus-visible:outline-none focus-visible:ring-2
                             focus-visible:ring-accent">경로별</Link>에서)
          </span>
        </p>
      )}
      {controls}
      {q.isError && fresh !== undefined && (
        <p className="inline-flex flex-wrap items-baseline gap-x-2 text-xs text-muted">
          순위를 새로 고치지 못했습니다{errText}<Retry onRetry={retry} /></p>
      )}
      {body}
    </section>
  );
}

export function BreakdownPage() {
  const [params, setParams] = useSearchParams();
  const s = useActiveStorage({ purged: null });
  const { tabs, active, activeTab } = s;
  // 「권한 사용자」(기본)와 모르는 값은 생략과 같다 -- 요청·캐시를 한 벌로(서버 규칙도 같다: 생략 = PV 안이 있으면 user).
  const byParam = params.get("by");
  const by: UsageBreakdownBy | null = isBreakdownBy(byParam) && byParam !== "user" ? byParam : null;
  const setBy = (b: UsageBreakdownBy) => {
    const next = new URLSearchParams(params);
    if (b === "user") next.delete("by"); else next.set("by", b);
    setParams(next, { replace: true });
  };
  const now = Date.now();
  const notices = [
    ...sourceNotices(s),
    s.notice !== null && (
      <p key="missing" role="status" className="flex items-center gap-1.5 text-sm text-muted [overflow-wrap:anywhere]">
        <Info aria-hidden className="h-4 w-4 shrink-0" />
        {`‘${storageLabel(s.notice)}’ 스토리지의 사용량 기록이 없습니다 — 기본 스토리지를 보여 줍니다`}
      </p>
    ),
  ].filter(Boolean);

  return (
    <section className="space-y-4">
      <UsageHeader title="소속별"
                   description="이 스토리지를 어느 권한 사용자·요청자·namespace·PV 가 얼마나 쓰는지 — 행을 누르면 그 경로 목록으로." />
      {notices.length > 0 && <div className="space-y-2">{notices}</div>}
      {s.sourcesPending ? (
        <div className="space-y-4">
          <span className="sr-only">스토리지 목록 불러오는 중</span>
          <Skeleton className="h-9 w-full sm:w-72" />
          <div className="space-y-2.5">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-6" />)}</div>
        </div>
      ) : s.noStorages ? <NoStorages /> : (<>
        <ScopeBar tabs={tabs} active={active} activeTab={activeTab} onSelect={s.select} now={now} />
        {activeTab === null ? (
          <div className="space-y-2.5">
            <span className="sr-only">불러오는 중…</span>
            {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-6" />)}
          </div>
        ) : <BreakdownPanel key={activeTab.name} tab={activeTab} by={by} onBy={setBy} />}
      </>)}
    </section>
  );
}
