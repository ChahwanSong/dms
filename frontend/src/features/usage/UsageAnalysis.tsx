import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { HardDrive, Info } from "lucide-react";
import { Card } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import { Tabs, type TabItem } from "../../components/ui/Tabs";
import type { ApiError } from "../../lib/api";
import { useStorages } from "../storages/useStorages";
import type { UsageAttrFilter } from "../../lib/types";
import {
  ATTR_KEYS, cleanAttr, usePurgeUnregisteredStorage, useScanStorages, useUnregisteredStorages, useUsageStorages,
  type UsageSort,
} from "./useUsage";
import { buildStorageTabs, defaultStorage, storageLabel, type StorageTab } from "./usageTabs";
import { StoragePanel } from "./StoragePanel";
import { UsageOverview } from "./UsageOverview";
import { PurgeResult } from "./UnregisteredStorages";

// 순수 규칙은 usageStats(화면·CSV 공용 한 벌, 2026-10-02 분리) -- 기존 import 경로 호환을 위해 다시 내보낸다.
export { HOT_AGE_MAX_DAYS, hotRatio, pointEpoch, stackLayout } from "./usageStats";
export { staleClass } from "./TargetTable";

// 사용량 분석(2026-10-20 스토리지별 분할, 사용자 요청: 「각 스토리지 별로 처음부터 나뉘어져서 보이게끔 하고 필터 및 서버의
// 스캔도 각 스토리지 별로」 + 「깔끔하고 심플하고 직관적으로」). 예전엔 첫 화면이 「전체 스토리지」 한 목록이라 정렬·검색이
// 전 스토리지의 최근 스캔 5천 개 창에서 돌았다. 이제 처음부터 스토리지 탭 하나를 보고, 목록·정렬·CSV 요청에 늘 storage 가
// 실린다(서버의 지표 정렬 창도 그 스토리지 안에서 -- routes_usage). 「전체」 탭은 없다.
//
// 구성: 머리말 → 접힌 「스토리지별 비교」(2026-10-21, UsageOverview) → (정리 직후) 결과 줄 → 스토리지 탭(usageTabs: 등록
// 이름순 | 미등록 이름순) → 탭 패널 = 카드 하나(StoragePanel -- 맨 위에 「이 스토리지 합계」). 미등록 스토리지는 따로 뜨던 칸
// 대신 「미등록」 표시 탭이 되고, 정리 버튼은 그 탭의 경고 띠에 있다.
//
// URL 이 진실이다: ?storage= = 탭, &target= = 그 탭 안에서 펼친 행 -- 새로고침·딥링크에서 같은 화면이 나온다(옛 링크
// ?storage=&target= 의 뜻 그대로). storage 가 없으면 기본 탭(usageTabs.defaultStorage)을 **파생**한다 -- URL 에 쓰지 않아
// effect 도 히스토리 항목도 없다. 한 번 고른 기본 탭은 그 탭이 남아 있는 동안 붙잡는다(리뷰: 요약 재조회로 대상 수가 바뀌면
// 다시 계산된 기본 탭이 보던 스토리지를 바꿔 버렸다). 탭을 바꾸면 target 은 버린다(다른 스토리지로 펼침이 따라가지 않게).
// 행을 접어도 storage 는 남긴다(빈 URL 로 돌리면 기본 탭으로 튄다).
//
// 필터(2026-10-29 -- 클러스터·namespace·PV 이름·StorageClass·권한 사용자·구분·요청자)도 URL 에 산다(키 = 서버 쿼리 이름 = PV
// 탐색 화면의 키, useUsage.ATTR_KEYS): 새로고침·링크 공유에 남는다. URL 쓰기는 늘 지금 파라미터에서 바꿀 키만 고친다(동기화 이력
// 화면 관용 -- 통째로 덮으면 탭 전환·행 펼침이 필터를 조용히 지웠다). 필터는 탭을 바꿔도 남고(같은 사용자·namespace 를 스토리지
// 마다 비교), 그 스토리지에 없는 값은 필터 줄이 「(이 스토리지에 없음)」으로 보여 풀 수 있게 한다. 필터 변경은 replace(뒤로 가기가
// 선택 하나하나를 되짚지 않게), 탭 전환은 push 그대로. 경로 검색·정렬은 여전히 페이지 상태다.

const PANEL_ID_PREFIX = "usage-storage";

function TabLabel({ tab, selected }: { tab: StorageTab; selected: boolean }) {
  const n = tab.targetCount;
  // 대상 수 필은 요약이 왔을 때만(모름 ≠ 0). 기록 0개는 흐리게. 숫자 필은 보조기기에서 숨기고 「대상 N개」를 sr-only 한
  // 덩어리로 읽힌다 -- 숫자와 「개」를 다른 노드로 쪼개면 이름 계산이 「2 개」로 띄운다. aria-label 로 탭 전체를 덮지 않는다
  // (보이는 이름 · 숫자가 낭독 이름 안에 그대로 있다). 낱말 사이 공백은 글자 노드로(인라인 span 은 이름 계산에서 붙는다).
  const pill = n === null ? null
    : selected ? "bg-infobg text-accent"
    : n === 0 ? "bg-line/40 text-muted"
    : "bg-line/70 text-ink/70";
  return (
    <>
      <span className={`max-w-[14rem] truncate ${tab.name === "" ? "italic" : ""}`} title={storageLabel(tab.name)}>
        {storageLabel(tab.name)}</span>
      {!tab.registered && (<>{" "}
        <span className="rounded border border-attn/30 bg-attnbg px-1 text-[11px] leading-4 text-attn">미등록</span>
      </>)}
      {n !== null && (<>{" "}
        <span aria-hidden className={`min-w-[1.25rem] rounded-full px-1.5 text-center text-xs leading-5 tabular-nums ${pill}`}>
          {n.toLocaleString("ko-KR")}</span>
        <span className="sr-only">{`대상 ${n.toLocaleString("ko-KR")}개`}</span>
      </>)}
    </>
  );
}

export function UsageAnalysis() {
  const [params, setParams] = useSearchParams();
  const storageParam = params.get("storage");
  const targetParam = params.get("target");
  // 필터: 값이 있는 키만(빈 값·모르는 구분 값은 없는 것으로 -- 서버가 422 로 거절할 값을 싣지 않는다). 쿼리키는 직렬화한
  // 문자열(useUsage.attrKey)이라 객체를 렌더마다 새로 만들어도 재조회가 없다.
  const attr = cleanAttr(Object.fromEntries(ATTR_KEYS.map((k) => [k, params.get(k)])));

  // 경로 검색(부분 문자열, 300ms 디바운스 -- 타이핑마다 GROUP BY 를 쏘지 않는다)과 정렬은 페이지 상태 -- 탭을 바꿔도 남는다.
  // 정렬(2026-10-02 최근 스캔 → 2026-10-19 열 정렬): 기본 최근 스캔 내림차순. 서버가 limit 전에 정렬한다(useUsage 주석).
  // 같은 열을 다시 누르면 방향만 바뀌고, 다른 열은 내림차순(큰·많은·최근 것 위)부터 시작한다.
  const [input, setInput] = useState("");
  const [pathQ, setPathQ] = useState("");
  const [sort, setSort] = useState<UsageSort>("last_scan");
  const [order, setOrder] = useState<"desc" | "asc">("desc");
  const sortBy = (col: UsageSort) => {
    if (col === sort) setOrder((o) => (o === "desc" ? "asc" : "desc"));
    else { setSort(col); setOrder("desc"); }
  };
  useEffect(() => {
    const t = setTimeout(() => setPathQ(input.trim()), 300);
    return () => clearTimeout(t);
  }, [input]);

  // 탭 원천 셋(usageTabs 머리 주석). 요약을 한 번도 못 받았으면 scan 기록 이름으로 탭만 그린다(대상 수 모름). 받아 둔 요약이
  // 있으면 재조회가 실패해도 그것을 쓴다 -- isError 만 보면 백그라운드 재조회 한 번의 실패에 패널이 자리표시로 돌아갔다(리뷰).
  const storagesQ = useStorages();
  const summaryQ = useUsageStorages();
  const unregQ = useUnregisteredStorages();
  const summaryMissing = summaryQ.isError && summaryQ.data === undefined;
  const scanQ = useScanStorages(summaryMissing);
  const tabs = buildStorageTabs({
    registered: storagesQ.data, summary: summaryQ.data?.storages, unregistered: unregQ.data,
    scanNames: summaryMissing ? scanQ.data : undefined,
  });
  // 기본 탭은 「기록이 있는가」를 알아야 고를 수 있다 -- 요약(또는 그 대체 원천)이 올 때까지 패널은 자리표시다.
  const ready = summaryQ.data !== undefined || (summaryMissing && !scanQ.isPending && !storagesQ.isPending);
  // URL 의 storage 가 탭에 없다는 판정은 미등록 목록까지 온 뒤에만(실패 기록만 남은 미등록 이름은 거기에만 있다) --
  // 로딩 중의 빈 목록으로 딥링크 탭에서 튕겨 나가지 않게.
  const judgeable = ready && !unregQ.isPending;
  const inTabs = storageParam !== null && tabs.some((t) => t.name === storageParam);
  // 붙잡은 기본 탭(머리 주석): 탭 목록에 남아 있으면 그대로, 사라졌으면(정리·삭제) 다시 고른다. 렌더 중 상태 조정이다 --
  // effect 로 하면 다른 탭이 한 번 그려진 뒤 바뀐다.
  const [heldDefault, setHeldDefault] = useState<string | null>(null);
  const fallbackTab = !ready ? null
    : heldDefault !== null && tabs.some((t) => t.name === heldDefault) ? heldDefault
    : defaultStorage(tabs);
  if (fallbackTab !== null && fallbackTab !== heldDefault) setHeldDefault(fallbackTab);
  const active = storageParam !== null && (inTabs || !judgeable) ? storageParam : fallbackTab;
  const activeTab: StorageTab | null = active === null ? null
    : tabs.find((t) => t.name === active)
      // 딥링크가 원천보다 먼저 왔다 -- 목록 요청은 바로 병렬로 보낸다(메타 줄·띠는 원천이 오면 채워진다).
      ?? { name: active, registered: true, targetCount: null, hasTargets: null, summary: null, unregistered: null,
           info: null };
  const activeTarget = storageParam !== null && storageParam === active ? targetParam : null;

  // 정리(미등록 스토리지 기록) mutation 은 페이지가 소유한다(UnregisteredStorages 머리 주석) -- 다 지우면 그 탭과 띠가 사라진다.
  const purge = usePurgeUnregisteredStorage();
  const [progress, setProgress] = useState<number | null>(null);
  const resultRef = useRef<HTMLDivElement>(null);
  const focusResult = () => resultRef.current?.focus();
  // 다 지워 그 탭(과 열린 확인 창)이 재조회로 사라지면 포커스가 <body> 로 떨어진다 -- 결과 줄로 옮긴다. 창이 살아 있으면
  // (일부 제외 등) 창이 닫히며 onCloseAutoFocus 가 같은 일을 한다. **정리 한 번에 한 번만**(submittedAt): 뒤의 재조회(창
  // 포커스·다른 관리자의 변경)마다 결과 줄로 끌어올려 읽던 표에서 화면이 튀지 않게(리뷰).
  const focusedFor = useRef<number | null>(null);
  useEffect(() => {
    if (!purge.isSuccess || focusedFor.current === purge.submittedAt) return;
    focusedFor.current = purge.submittedAt;
    if (document.activeElement === null || document.activeElement === document.body) focusResult();
  }, [purge.isSuccess, purge.submittedAt]);  // eslint-disable-line react-hooks/exhaustive-deps
  // 첫 회차 전체 실패(409 storage_registered·503 등)인데 그 탭이 재조회로 이미 사라졌으면 창 안 오류가 보일 자리가 없다 --
  // 페이지가 말한다(탭이 있으면 창이 말한다). 예: 다른 관리자가 같은 이름을 다시 등록해 미등록 목록에서 빠졌다.
  const failedName = purge.isError ? purge.variables?.storage_name ?? null : null;
  const orphanError = failedName !== null && !(unregQ.data ?? []).some((r) => r.storage_name === failedName);

  // URL 의 storage 가 탭에 없으면(오타·정리됨·삭제됨) 기본 탭으로 돌리고 흐린 한 줄로 알린다(URL 은 replace 로 정리). 방금
  // 정리해 사라진 탭이면 알리지 않는다 -- 결과 줄이 이미 말한다. 정리 mutation 은 무효화(재조회)가 끝나야 성공이 되므로 탭은
  // 아직 진행 중(isPending)일 때 사라진다 -- 진행 중도 「방금 정리」로 본다.
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    if (storageParam === null || !judgeable || inTabs) return;
    const purged = (purge.isPending || purge.isSuccess) && purge.variables?.storage_name === storageParam;
    setNotice(purged ? null : storageParam);
    // storage·target 만 지운다 -- 필터는 기본 탭에 그대로 걸린다(필터 줄에서 풀 수 있다).
    const next = new URLSearchParams(params);
    next.delete("storage"); next.delete("target");
    setParams(next, { replace: true });
  }, [storageParam, judgeable, inTabs]);  // eslint-disable-line react-hooks/exhaustive-deps

  const selectTab = (name: string) => {
    // 기본 탭(URL 에 없음)을 눌렀으면 URL 에 적는다 -- 그 뒤로는 그 탭이 링크·새로고침에도 남는다.
    if (name === active && storageParam !== null) return;
    setNotice(null);
    const next = new URLSearchParams(params);
    next.set("storage", name); next.delete("target");
    setParams(next);
  };
  // 펼침은 한 번에 하나(사용자 요청 2026-09-14): 같은 행 재클릭 = 접기(target 만 제거), 다른 행 클릭 = 펼침 이동.
  const toggle = (t: string) => {
    if (active === null) return;
    const next = new URLSearchParams(params);
    next.set("storage", active);
    if (t === activeTarget) next.delete("target"); else next.set("target", t);
    setParams(next);
  };
  // 필터 변경: 필터 키만 다시 쓴다(replace). 펼친 행은 남긴다 -- 필터에 걸리지 않으면 표 아래 폴백 칸이 보인다.
  const setAttr = (a: UsageAttrFilter) => {
    const next = new URLSearchParams(params);
    for (const k of ATTR_KEYS) { const v = a[k]; if (v !== undefined && v !== "") next.set(k, v); else next.delete(k); }
    setParams(next, { replace: true });
  };

  const now = Date.now();
  const items: TabItem[] = tabs.map((t, i) => ({
    id: t.name,
    separatorBefore: !t.registered && i > 0 && tabs[i - 1].registered,
    content: <TabLabel tab={t} selected={t.name === active} />,
  }));
  const sourcesPending = storagesQ.isPending && summaryQ.isPending;
  const noStorages = tabs.length === 0 && ready && !unregQ.isPending && !storagesQ.isPending;

  return (
    <section className="space-y-5">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold">사용량 분석</h1>
        <p className="text-sm text-muted break-keep">
          스토리지별 디렉터리 scan 이력 — 실 사용량과 데이터 온도(요청자와 무관한 모든 성공 scan).
        </p>
      </header>

      {/* 스토리지별 비교(2026-10-21) -- 접힌 한 줄, 열면 스토리지마다의 합계 표(패널 합계와 같은 쿼리). 행 = 그 탭 선택. */}
      {ready && <UsageOverview tabs={tabs} active={active} onSelect={selectTab} now={now}
                              windowLimit={summaryQ.data?.window_limit ?? null} />}

      {(purge.data || orphanError || summaryQ.isError || unregQ.isError) && (
        <div className="space-y-2">
          {purge.data && <PurgeResult r={purge.data} innerRef={resultRef} onDismiss={() => purge.reset()} />}
          {orphanError && (
            <p role="alert" className="text-sm text-bad [overflow-wrap:anywhere]">
              {`${failedName}: ${(purge.error as ApiError).message}`}</p>
          )}
          {/* 요약을 못 받아도 탭은 대체 원천(scan 기록 이름)으로 그려진다 -- 그 동안엔 흐린 한 줄로 「건수 모름」만 말한다(배포
              중 옛 API 는 이 경로에 SPA index.html 을 200 으로 줘서, 원문 오류가 「Unexpected token '<'」 같은 파서 문장이었다).
              대체 원천까지 실패하면 그때 오류다. 받아 둔 요약이 있는데 재조회만 실패했으면 그 사실만. */}
          {summaryMissing && !scanQ.isError && (
            <p className="flex items-center gap-1.5 text-sm text-muted">
              <Info aria-hidden className="h-4 w-4 shrink-0" />스토리지별 대상 수를 불러오지 못해 건수 없이 표시합니다</p>
          )}
          {summaryMissing && scanQ.isError && (
            <p className="text-sm text-bad">
              {`스토리지별 요약을 불러오지 못했습니다 — ${(scanQ.error as ApiError).message}`}</p>
          )}
          {summaryQ.isError && !summaryMissing && (
            <p className="text-sm text-bad">
              {`스토리지별 요약을 새로 고치지 못했습니다 — ${(summaryQ.error as ApiError).message}`}</p>
          )}
          {unregQ.isError && (
            <p className="text-sm text-bad">
              {`미등록 스토리지 목록을 ${unregQ.data === undefined ? "불러오지" : "새로 고치지"} 못했습니다 — `
                + (unregQ.error as ApiError).message}</p>
          )}
        </div>
      )}

      {sourcesPending ? (
        <div className="space-y-4">
          <div className="flex gap-3 py-2">
            <span className="sr-only">스토리지 목록 불러오는 중</span>
            {[0, 1, 2].map((i) => <Skeleton key={i} className="h-6 w-28" />)}
          </div>
          <Card><div className="space-y-2.5">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-5" />)}</div></Card>
        </div>
      ) : noStorages ? (
        <Card>
          <div className="flex flex-col items-center gap-2 py-8 text-center">
            <span aria-hidden className="grid h-10 w-10 place-items-center rounded-full bg-panel text-muted">
              <HardDrive className="h-5 w-5" /></span>
            <p className="text-sm font-medium text-ink">등록된 스토리지가 없습니다</p>
            <p className="text-xs text-muted">스토리지를 등록하고 scan 을 실행하면 여기에 스토리지별 사용량이 쌓입니다.</p>
            <Link to="/admin/storages" className="mt-1 text-sm text-accent hover:underline">스토리지 화면으로</Link>
          </div>
        </Card>
      ) : (
        <Tabs label="스토리지" items={items} value={active} onSelect={selectTab} idPrefix={PANEL_ID_PREFIX}
              panelClassName="mt-4 space-y-3">
          {notice !== null && (
            <p role="status" className="flex items-center gap-1.5 text-sm text-muted [overflow-wrap:anywhere]">
              <Info aria-hidden className="h-4 w-4 shrink-0" />
              {`‘${storageLabel(notice)}’ 스토리지의 사용량 기록이 없습니다 — 기본 탭을 보여 줍니다`}
            </p>
          )}
          <Card>
            {activeTab === null ? (
              <div className="space-y-2.5">
                <span className="sr-only">불러오는 중…</span>
                {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-5" />)}
              </div>
            ) : (
              <StoragePanel key={activeTab.name} tab={activeTab}
                            windowLimit={summaryQ.data?.window_limit ?? null}
                            input={input} setInput={setInput} pathQ={pathQ}
                            sort={sort} order={order} onSort={sortBy} attr={attr} onAttrChange={setAttr}
                            activeTarget={activeTarget} onToggle={toggle} storages={storagesQ.data}
                            purge={purge} progress={progress} setProgress={setProgress}
                            onResultFocus={focusResult} now={now} />
            )}
          </Card>
        </Tabs>
      )}
    </section>
  );
}
