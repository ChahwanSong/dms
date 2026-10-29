import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { Info } from "lucide-react";
import { Skeleton } from "../../components/ui/Skeleton";
import type { ApiError } from "../../lib/api";
import type { UsageAttrFilter } from "../../lib/types";
import { ATTR_KEYS, USAGE_SORTS, cleanAttr, usePurgeUnregisteredStorage, type UsageSort } from "./useUsage";
import { useActiveStorage } from "./useActiveStorage";
import { storageLabel } from "./usageTabs";
import { rememberPaths } from "./usageMemory";
import { UsageHeader } from "./UsageHeader";
import { ScopeBar } from "./StorageSelect";
import { StoragePanel } from "./StoragePanel";
import { PurgeResult } from "./UnregisteredStorages";
import { NoStorages, sourceNotices } from "./UsageParts";

// 순수 규칙은 usageStats(화면·CSV 공용 한 벌, 2026-10-02 분리) -- 기존 import 경로 호환을 위해 다시 내보낸다.
export { HOT_AGE_MAX_DAYS, hotRatio, pointEpoch, stackLayout } from "./usageStats";
export { staleClass } from "./TargetTable";

// 사용량 분석 「경로별」(2026-10-20 스토리지별 분할 -- 사용자 요청: 「각 스토리지 별로 처음부터 나뉘어져서 보이게끔 하고 필터 및
// 서버의 스캔도 각 스토리지 별로」 + 「깔끔하고 심플하고 직관적으로」 → 2026-10-29 하위 메뉴, 사용자 결정 「권장사항대로」). 처음부터
// 스토리지 하나를 본다: 목록·정렬·CSV 요청에 늘 storage 가 실린다(서버의 지표 정렬 창도 그 스토리지 안에서 -- routes_usage).
// 사이드바 「사용량 분석」으로 들어오면 여기다(매일 쓰는 표 -- 열 때 요청이 예전과 같다). 스토리지끼리의 비교는 「스토리지별」 탭
// (StoragesPage)이다.
//
// 구성: 머리(h1 · 설명 · 하위 탭 UsageTabs) → 알림 자리(있을 때만: 정리 결과 · 정리 실패 · 원천 실패 · 없는 storage 안내) → 범위 줄
// (스토리지 select + 메타 줄, StorageSelect) → 패널(StoragePanel: 미등록 경고 띠 · 도구 줄 · 필터 줄 · 합계 줄 · 표 · 각주 · 상세 시트).
// 바깥 카드는 없다(카드 안에 카드를 넣지 않는다 -- 합계 줄·표가 각자 1px 테두리).
//
// URL 이 진실이다: ?storage= = 보는 스토리지(useActiveStorage -- 없으면 기본 스토리지를 파생·붙잡기, URL 에 쓰지 않는다), 필터 일곱
// (키 = 서버 쿼리 이름 = PV 탐색 화면의 키, useUsage.ATTR_KEYS), q = 검색(경로·PV 이름 -- 2026-10-29 전엔 경로만이었다, 같은 키라 옛
// 링크의 q 가 그대로 걸린다), sort·order = 열 정렬(기본값 last_scan·desc 는 생략),
// &target= = 그 위에 열린 경로 상세 시트. 새로고침·딥링크·링크 공유에서 같은 화면이 나온다(옛 링크 ?storage=&target= 은 같은
// 라우트라 리다이렉트 없이 시트가 열린 채로). URL 쓰기는 늘 지금 파라미터에서 바꿀 키만 고친다(동기화 이력 화면 관용 -- 통째로 덮으면
// 스토리지 전환·시트 열기가 필터를 조용히 지웠다). 기록 방식: 스토리지 = push(select 300ms 디바운스), 필터·검색·정렬 = replace(뒤로
// 가기가 선택·타이핑 하나하나를 되짚지 않게), 시트 = 아래 sheet 주석. 지금 쿼리(target 제외)는 sessionStorage 에도 적는다(usageMemory
// -- 「경로별」 탭 링크가 다른 탭에서 돌아올 때 복원한다).

// 시트를 이 세션에서 push 로 열었다는 히스토리 항목 표지(location.state 의 키) -- 닫기가 뒤로 갈지 replace 할지 가른다.
const SHEET_STATE = "usageSheet";
const isSort = (x: string | null): x is UsageSort => x !== null && (USAGE_SORTS as readonly string[]).includes(x);
const withQ = (p: URLSearchParams, v: string) => {
  const next = new URLSearchParams(p);
  if (v === "") next.delete("q"); else next.set("q", v);
  return next;
};

export function UsageAnalysis() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const targetParam = params.get("target");
  // 필터: 값이 있는 키만(빈 값·모르는 구분 값은 없는 것으로 -- 서버가 422 로 거절할 값을 싣지 않는다). 쿼리키는 직렬화한
  // 문자열(useUsage.attrKey)이라 객체를 렌더마다 새로 만들어도 재조회가 없다.
  const attr = cleanAttr(Object.fromEntries(ATTR_KEYS.map((k) => [k, params.get(k)])));
  // 디바운스 타이머에서 부르는 URL 쓰기는 그때의 URL 위에서(마지막 렌더의 파라미터·setParams·state) -- 친 순간의 파라미터를 붙잡으면
  // 그 사이 바뀐 다른 키(정렬·스토리지 select 의 디바운스 등)를 되돌리고, 그 사이 열린 시트 항목의 표지(state)를 지운다.
  const latest = useRef({ params, setParams, state: location.state as unknown });
  latest.current = { params, setParams, state: location.state };

  // 정렬(2026-10-02 최근 스캔 → 2026-10-19 열 정렬 → 2026-10-29 URL): 기본 최근 스캔 내림차순(그 값은 URL 에서 생략). 서버가
  // limit 전에 정렬한다(useUsage 주석). 같은 열을 다시 누르면 방향만 바뀌고, 다른 열은 내림차순(큰·많은·최근 것 위)부터 시작한다.
  const sortParam = params.get("sort");
  const sort: UsageSort = isSort(sortParam) ? sortParam : "last_scan";
  const order: "desc" | "asc" = params.get("order") === "asc" ? "asc" : "desc";
  const sortBy = (col: UsageSort) => {
    const nextOrder = col === sort && order === "desc" ? "asc" : "desc";
    const next = new URLSearchParams(params);
    if (col === "last_scan") next.delete("sort"); else next.set("sort", col);
    if (nextOrder === "desc") next.delete("order"); else next.set("order", nextOrder);
    setParams(next, { replace: true });
  };

  // 검색(경로·PV 이름, 토큰마다 부분 일치 -- useUsage.TargetFilter. 300ms 디바운스 -- 타이핑마다 GROUP BY 를 쏘지 않는다): 입력칸은
  // 이 페이지 상태, 디바운스한 값은 URL 의 q(replace). URL 이 밖에서 바뀌면(뒤로 가기·탭 링크 복원) 입력칸을 따라 맞춘다 -- 우리가
  // 방금 쓴 값의 메아리(wrote)는 건너뛴다(그 사이 더 친 글자를 지우지 않게).
  const searchQ = (params.get("q") ?? "").trim();
  const [input, setInput] = useState(searchQ);
  const wrote = useRef<string | null>(null);
  useEffect(() => {
    if (wrote.current === searchQ) { wrote.current = null; return; }
    if (input.trim() !== searchQ) setInput(searchQ);
  }, [searchQ]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const v = input.trim();
    if (v === searchQ) return;
    const t = setTimeout(() => {
      const { params: cur, setParams: set, state } = latest.current;
      if ((cur.get("q") ?? "").trim() === v) return;           // 이미 실렸다(시트를 열며 함께 실었다 -- sheet.open)
      wrote.current = v;
      set(withQ(cur, v), { replace: true, state });
    }, 300);
    return () => clearTimeout(t);
  }, [input]);  // eslint-disable-line react-hooks/exhaustive-deps

  // 정리(미등록 스토리지 기록) mutation 은 페이지가 소유한다(UnregisteredStorages 머리 주석) -- 다 지우면 그 스토리지와 띠가 사라진다.
  // 결과 줄·정리 실패는 알림 자리에 남는다(같은 pathname 이라 다시 마운트되지 않는다).
  const purge = usePurgeUnregisteredStorage();
  const purgedName = purge.isPending || purge.isSuccess ? purge.variables?.storage_name ?? null : null;
  const s = useActiveStorage({ purged: purgedName });
  const { tabs, active, activeTab } = s;
  const [progress, setProgress] = useState<number | null>(null);
  const resultRef = useRef<HTMLDivElement>(null);
  const focusResult = () => resultRef.current?.focus();
  // 다 지워 그 스토리지(와 열린 확인 창)가 재조회로 사라지면 포커스가 <body> 로 떨어진다 -- 결과 줄로 옮긴다. 창이 살아 있으면
  // (일부 제외 등) 창이 닫히며 onCloseAutoFocus 가 같은 일을 한다. **정리 한 번에 한 번만**(submittedAt): 뒤의 재조회(창
  // 포커스·다른 관리자의 변경)마다 결과 줄로 끌어올려 읽던 표에서 화면이 튀지 않게(리뷰).
  const focusedFor = useRef<number | null>(null);
  useEffect(() => {
    if (!purge.isSuccess || focusedFor.current === purge.submittedAt) return;
    focusedFor.current = purge.submittedAt;
    if (document.activeElement === null || document.activeElement === document.body) focusResult();
  }, [purge.isSuccess, purge.submittedAt]);  // eslint-disable-line react-hooks/exhaustive-deps
  // 첫 회차 전체 실패(409 storage_registered·503 등)인데 그 스토리지가 재조회로 이미 사라졌으면 창 안 오류가 보일 자리가 없다 --
  // 페이지가 말한다(스토리지가 있으면 창이 말한다). 예: 다른 관리자가 같은 이름을 다시 등록해 미등록 목록에서 빠졌다.
  const failedName = purge.isError ? purge.variables?.storage_name ?? null : null;
  const orphanError = failedName !== null && !(s.unregQ.data ?? []).some((r) => r.storage_name === failedName);

  // 경로별의 마지막 쿼리(target 제외)와 보는 스토리지를 적어 둔다(usageMemory) -- 탭 링크·스토리지별 표의 현재 행.
  useEffect(() => { rememberPaths(location.search, active); }, [location.search, active]);

  // 필터 변경: 필터 키만 다시 쓴다(replace). 열린 시트는 없다(모달 -- 필터 줄은 시트가 닫혀 있을 때만 만질 수 있다).
  const attrParams = (a: UsageAttrFilter) => {
    const next = new URLSearchParams(params);
    for (const k of ATTR_KEYS) { const v = a[k]; if (v !== undefined && v !== "") next.set(k, v); else next.delete(k); }
    return next;
  };
  const setAttr = (a: UsageAttrFilter) => setParams(attrParams(a), { replace: true });
  // 경로 상세 시트(2026-10-29 -- 행 아래 펼침 대신, 한 번에 하나는 그대로): 열기는 push(뒤로 가기 = 닫기, 안드로이드 뒤로 포함)이고
  // 그 항목에 「이 세션에서 push 로 열었다」 표지(location.state)를 단다. ↑↓ 이웃 이동은 replace(표지 유지 -- 이동마다 히스토리를
  // 쌓지 않는다). 닫기: 표지가 있으면 뒤로(navigate(-1) -- 모달이라 열린 동안 뒤 화면의 필터·검색·정렬은 바뀔 수 없어 잃는 상태가
  // 없고, 「늘 replace 로 닫기」는 뒤로 가기에 죽은 한 칸을 남긴다), 없으면(옛 딥링크·새로고침한 다른 탭) target 만 지우는
  // replace. 기본 스토리지에서 열면 storage 도 URL 에 적는다(딥링크 닫기가 남기는 URL 이 기본 스토리지로 튀지 않게 -- 뒤로 닫으면
  // 열기 전 URL 이고, 그 기본 스토리지는 붙잡혀 있어 같은 스토리지다).
  const pushedSheet = (location.state as { [SHEET_STATE]?: boolean } | null)?.[SHEET_STATE] === true;
  const sheet = {
    open: (t: string) => {
      if (active === null) return;
      // 아직 디바운스 중인 검색은 지금 싣는다(지금 항목 replace → 시트 push) -- 열린 동안 뒤 화면 상태가 바뀌지 않아야 뒤로
      // (닫기)가 잃는 것이 없고, 닫고 돌아온 항목에도 그 검색이 남는다.
      const v = input.trim();
      const base = v === searchQ ? params : withQ(params, v);
      if (v !== searchQ) { wrote.current = v; setParams(base, { replace: true, state: location.state }); }
      const next = new URLSearchParams(base);
      next.set("storage", active); next.set("target", t);
      setParams(next, { state: { [SHEET_STATE]: true } });
    },
    move: (t: string) => {
      const next = new URLSearchParams(params);
      next.set("target", t);
      setParams(next, { replace: true, state: location.state });
    },
    close: () => {
      if (pushedSheet) { navigate(-1); return; }
      const next = new URLSearchParams(params);
      next.delete("target");
      setParams(next, { replace: true });
    },
    // 시트 안 소속 글자(PV 이름·권한 사용자): 필터 설정 + 시트 닫기를 한 번의 replace 로(필터 변경 관용과 같은 replace).
    filter: (a: UsageAttrFilter) => {
      const next = attrParams(a);
      next.delete("target");
      setParams(next, { replace: true });
    },
  };
  const activeTarget = s.storageParam !== null && s.storageParam === active ? targetParam : null;

  const now = Date.now();
  const notices = [
    purge.data && <PurgeResult key="purge" r={purge.data} innerRef={resultRef} onDismiss={() => purge.reset()} />,
    orphanError && (
      <p key="orphan" role="alert" className="text-sm text-bad [overflow-wrap:anywhere]">
        {`${failedName}: ${(purge.error as ApiError).message}`}</p>
    ),
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
      <UsageHeader title="사용량 분석"
                   description="고른 스토리지의 경로마다 scan 이력 — 실 사용량과 데이터 온도(요청자와 무관한 모든 성공 scan)." />

      {notices.length > 0 && <div className="space-y-2">{notices}</div>}

      {s.sourcesPending ? (
        <div className="space-y-4">
          <span className="sr-only">스토리지 목록 불러오는 중</span>
          <Skeleton className="h-9 w-full sm:w-72" />
          <div className="space-y-2.5">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-5" />)}</div>
        </div>
      ) : s.noStorages ? <NoStorages /> : (<>
        <ScopeBar tabs={tabs} active={active} activeTab={activeTab} onSelect={s.select} now={now} />
        {activeTab === null ? (
          <div className="space-y-2.5">
            <span className="sr-only">불러오는 중…</span>
            {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-5" />)}
          </div>
        ) : (
          <StoragePanel key={activeTab.name} tab={activeTab} windowLimit={s.windowLimit}
                        input={input} setInput={setInput} searchQ={searchQ}
                        sort={sort} order={order} onSort={sortBy} attr={attr} onAttrChange={setAttr}
                        activeTarget={activeTarget} sheet={sheet} storages={s.storagesQ.data}
                        purge={purge} progress={progress} setProgress={setProgress}
                        onResultFocus={focusResult} now={now} />
        )}
      </>)}
    </section>
  );
}
