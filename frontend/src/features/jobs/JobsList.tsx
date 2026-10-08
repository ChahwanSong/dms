import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useDeleteRequests, useInfiniteRequests, usePurgeStatus } from "./useJobs";
import type { RequestFilters } from "./useJobs";
import { DeleteRequestsDialog, isSucceededScan } from "./DeleteRequestsDialog";
import { useMe } from "../auth/useAuth";
import { Table } from "../../components/ui/Table";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";
import { ApiError, reasonText } from "../../lib/api";
import { kstStamp } from "../../lib/datetime";
import { REQUEST_TERMINAL_STATES } from "../../lib/jobState";
import { pathSummary } from "../../lib/storagePaths";
import type { PurgeStatus, RequestRow } from "../../lib/types";

const field = "rounded-lg border border-line px-3 py-2 text-sm";

// 필터 select 값(빈 문자열 = 전체). 연산·상태는 도메인 열거의 미러 -- 서버는
// 미지 값에 0건을 주므로 여기 목록이 곧 사용자에게 유효한 값 집합이다. 상태는 **요청** 상태(domain.RequestState
// 8종)다 -- 잡 상태인 Previewing·ConfirmPending 이 섞여 있어 고르면 늘 0건이던 결함을 2026-10-08 에 뺐다.
const OPERATIONS = ["sync", "scan", "rm"];
const STATES = ["Pending", "Planned", "Running", "Succeeded", "Failed", "Rejected", "Cancelled", "Conflict"];

// 작업(요청) 선택 삭제(2026-10-08, 관리자 전용 -- 일반 사용자에게는 체크 열·툴바를 그리지 않는다).
// 한 번에 지울 수 있는 수 = 서버 MAX_DELETE(목록 API 상한 le=200 과 같다).
const MAX_DELETE = 200;
// 미선택 안내(상태 줄)는 짧게, 선택 규칙은 **늘 있는** 별도 줄(RULE_HINT)로 -- 선택 불가 사유는 비활성 체크박스의
// title(마우스 hover)뿐이라 키보드(disabled 는 Tab 이 건너뛴다)·터치(375px) 사용자에겐 이 줄이 유일한 설명이다
// (2026-10-09 검증 지적). 규칙을 상태 줄에 붙여 두면 좁은 폭에서 미선택 안내만 두 줄로 접혀 첫 체크 순간 툴바 높이가
// 바뀌고 표가 포인터 밑에서 튄다(2026-10-09 검증 지적) -- 바뀌지 않는 줄은 높이도 바뀌지 않는다.
const IDLE_HINT = "끝난 작업을 선택해 삭제할 수 있습니다";
const RULE_HINT = "진행 중·배치 항목의 작업은 선택할 수 없습니다";
// 선택 불가 사유(title). 배치 자식이 먼저다 -- 끝나도 영영 개별 삭제가 안 되는 쪽이 더 정확한 안내다. 「배치 화면에서
// 관리」로 보내지 않는다 -- 배치를 지워도 자식 작업 기록은 남고 그 뒤로도 개별 삭제 경로가 없다(2026-10-09 검증 지적).
const ACTIVE_HINT = "진행 중인 작업은 삭제할 수 없습니다 — 끝난 뒤에 삭제하세요(필요하면 상세에서 먼저 취소)";
const BATCH_HINT = reasonText("batch_child_not_deletable");
// 체크박스 표적(BatchesList 와 같은 20px). **td 안 래퍼 금지**(e2e L2)라 크기는 input 자신에게 준다.
const BOX = "h-5 w-5 cursor-pointer align-middle";

/** 표시용 삭제 가능 판정(서버가 재판정한다 -- 화면은 3초 낡은 스냅숏). 요청 종단 ∧ 배치 자식 아님. batch_id 는
 *  `== null`(null·부재)만 단건이다 -- 빈 문자열 같은 이상값은 배치 자식으로 보고 선택 불가(fail-closed). */
/** 제외 목록 → [사유, request_id 목록] (사유가 처음 나온 순서). 결과 요약 아래 사유별 한 줄용. */
export function skippedGroups(skipped: { request_id: string; reason: string }[]): [string, string[]][] {
  const groups = new Map<string, string[]>();
  for (const s of skipped) {
    const ids = groups.get(s.reason);
    if (ids) ids.push(s.request_id); else groups.set(s.reason, [s.request_id]);
  }
  return [...groups];
}

export function deletable(r: Pick<RequestRow, "state" | "batch_id">): boolean {
  return REQUEST_TERMINAL_STATES.has(r.state) && r.batch_id == null;
}

export function JobsList() {
  const me = useMe();
  const isAdmin = me.data?.role === "admin";
  const [operation, setOperation] = useState("");
  const [state, setState] = useState("");
  const [requester, setRequester] = useState("");
  // 필터 객체는 쿼리 키라 -- 매 렌더 새 객체면 키가 흔들려 재조회가 폭주한다.
  // 값이 실제로 바뀔 때만 새 객체를 만든다(useMemo).
  const filters: RequestFilters = useMemo(
    () => ({ operation: operation || undefined, state: state || undefined,
             requester: requester || undefined }),
    [operation, state, requester]);
  const q = useInfiniteRequests(filters);
  // q.data 를 그대로 dep 에 쓴다(react-query 구조적 공유 -- 내용이 같은 리페치는 같은 참조). 매 렌더 새 배열이면
  // 아래 유령 선택 정리 effect 가 무한 루프가 된다(BatchesList 관례).
  const rows = useMemo(() => q.data?.pages.flat() ?? [], [q.data]);

  // --- 선택 삭제(관리자) ---
  const del = useDeleteRequests();
  const purge = usePurgeStatus(isAdmin);
  const [selected, setSelected] = useState<string[]>([]);
  // 유령 정리로 선택에서 빠진 수(사용자가 선택을 다시 만지면 0) -- 말없이 줄지 않게 상태 줄이 알린다(아래).
  const [dropped, setDropped] = useState(0);
  // 전체 선택 범위 = **불러온 모든 쪽**(무한 스크롤에는 쪽이 없다)의 삭제 가능 행.
  const deletableIds = useMemo(() => rows.filter(deletable).map((r) => r.request_id), [rows]);
  // 유령 선택 정리: 3초 폴링 리페치로 사라졌거나(다른 관리자가 지움, 새 제출에 밀려 불러온 범위 밖으로 나감) 삭제
  // 불가가 된 행을 선택에서 뺀다 -- 안 그러면 화면에 없는 id 를 보내 「제외」로 보고하거나 **보이지 않는 것을 지운다**
  // (필터 변경 시 선택을 비우는 것과 같은 원칙). 다만 관리자가 명시적으로 고른 것을 **말없이** 줄이지 않는다 -- 뺀 수를
  // 상태 줄에 알린다(2026-10-09 검증 지적: 전체 선택 50개가 새 제출 5건에 45개로 조용히 줄었다). 삭제 진행 중의 정리는
  // 알리지 않는다(onSettled 의 목록 재조회로 방금 지운 행이 빠지는 것 -- 곧 선택 전체가 비워진다). 부분집합이라 길이
  // 비교로 동일성을 판정해 재렌더 루프를 끊는다(BatchesList 관례).
  //
  // 갱신은 **함수형**이다(2026-10-09 검증 지적): 삭제 성공의 onDeleted(setSelected([]))는 기본 lane 으로 예약되는데
  // 목록 재조회 결과(useSyncExternalStore, SyncLane)가 먼저 렌더되면 이 effect 는 새 rows·옛 selected 를 본다 --
  // 값으로 setSelected(next) 하면 예약된 [] 를 덮어 서버가 제외한 행이 체크된 채 남고, 방금 지운 행을 「목록에서
  // 사라져 뺐다」로 잘못 세어 결과 요약(「N개 삭제됨 · M개 제외」)을 가렸다. 함수형이면 [] 위에 적용돼 [] 로 남는다.
  // 삭제 결과가 떠 있는 동안(isSuccess -- 새로 고르면 disarm 이 지운다)도 세지 않는다: 그 사라짐은 방금 지운 것이다.
  useEffect(() => {
    const ok = new Set(deletableIds);
    const next = selected.filter((id) => ok.has(id));
    if (next.length === selected.length) return;
    setSelected((p) => p.filter((id) => ok.has(id)));
    if (!del.isPending && !del.isSuccess) setDropped((d) => d + selected.length - next.length);
  }, [deletableIds, selected, del.isPending, del.isSuccess]);
  const selectedRows = useMemo(() => {
    const byId = new Map(rows.map((r) => [r.request_id, r]));
    return selected.flatMap((id) => { const r = byId.get(id); return r ? [r] : []; });
  }, [rows, selected]);
  const selSet = new Set(selected);
  const allChecked = deletableIds.length > 0 && deletableIds.every((id) => selSet.has(id));
  const someChecked = selected.length > 0 && !allChecked;
  // indeterminate 는 속성이 아니라 DOM 프로퍼티라 ref 로만 설정된다. **콜백 ref** 로 매 렌더 다시 건다 -- effect 로 걸면
  // 폴링 오류 동안 표가 오류 문구로 바뀌어 헤더 input 이 새로 마운트돼도 dep(someChecked)가 그대로라 다시 걸리지 않아,
  // 복구 뒤 일부 선택인데 빈 칸으로 보였다(2026-10-09 검증 지적).
  const setAllIndeterminate = (el: HTMLInputElement | null) => { if (el) el.indeterminate = someChecked; };
  // 성공해 확인 창이 닫히면 포커스를 둘 자리 -- 「선택 삭제」는 선택이 비어 잠기므로(Radix 기본 복귀가 <body> 로
  // 떨어진다) 늘 있는 결과 줄로 옮긴다. 다음 Tab 은 표(전체 선택)로 이어진다.
  const statusRef = useRef<HTMLSpanElement>(null);
  // 선택이 바뀌면 직전 결과 문구를 지운다. 진행 중에는 reset 하지 않는다(비행 중 mutation 의 결과를 삼킨다).
  const disarm = () => { if (!del.isPending) del.reset(); };
  const toggleRow = (id: string) => {
    disarm();
    setDropped(0);
    setSelected((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  };
  const toggleAll = () => { disarm(); setDropped(0); setSelected(allChecked ? [] : deletableIds); };
  // 필터를 바꾸면 선택을 비운다 -- 이전 필터에서 고른(이제 안 보이는) 행이 선택에 남으면 보이지 않는 것을 지운다.
  const onFilter = (set: (v: string) => void, v: string) => { set(v); setSelected([]); setDropped(0); disarm(); };

  // 무한 스크롤: 표 끝 감시 노드가 보이면 다음 쪽을 당긴다. IntersectionObserver
  // 는 스크롤 이벤트보다 싸고(오버헤드 최소화 요청), 화면 밖이면 발화하지 않는다.
  const sentinel = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting && q.hasNextPage && !q.isFetchingNextPage)
        q.fetchNextPage();
    }, { rootMargin: "200px" });   // 바닥 200px 전에 미리 당겨 끊김을 줄인다
    io.observe(el);
    return () => io.disconnect();
  }, [q.hasNextPage, q.isFetchingNextPage, q]);

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">전체 작업</h1>
        <Link to="/jobs/new"><Button>작업 제출</Button></Link>
      </div>

      {/* 필터: 연산·상태는 누구나, 요청자는 운영자만(사용자는 서버가 자기 것으로
          강제하므로 요청자 필터가 무의미하다). */}
      <div className="flex flex-wrap items-center gap-2">
        <select aria-label="연산 필터" className={field} value={operation}
                onChange={(e) => onFilter(setOperation, e.target.value)}>
          <option value="">연산 전체</option>
          {OPERATIONS.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        <select aria-label="상태 필터" className={field} value={state}
                onChange={(e) => onFilter(setState, e.target.value)}>
          <option value="">상태 전체</option>
          {STATES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        {isAdmin && (
          <input aria-label="요청자 필터" className={field} placeholder="요청자 아이디"
                 value={requester} onChange={(e) => onFilter(setRequester, e.target.value)} />
        )}
      </div>

      {isAdmin && (
        <DeleteToolbar selectedRows={selectedRows} del={del} purge={purge.data} dropped={dropped}
                       statusRef={statusRef}
                       onDeleted={() => { setSelected([]); setDropped(0); }}
                       onClear={() => { setSelected([]); setDropped(0); disarm(); }} />
      )}

      <Card>
        {q.isError ? <p className="text-bad">{(q.error as ApiError).message}</p> : (
          <>
          {/* 표 헤더는 항상 렌더한다 -- 빈 결과에도 헤더가 남아야 화면 구조가
              유지되고, e2e L2(표 셀 하한)도 지킨다. 로딩·빈 문구는 표 아래로. */}
          <Table>
            <thead>
              <tr className="text-muted whitespace-nowrap">
                {/* w-12 = 체크박스 20px + px-3 좌우(BatchesList 와 같은 열). 전체 선택은 **삭제 가능한 행만** 토글한다
                    -- 진행 중·배치 자식까지 켜면 지울 수 없는 것을 골라 둔 셈이 되어 매번 「제외」를 만든다. */}
                {isAdmin && (
                  <th className="px-3 py-2 w-12"><input ref={setAllIndeterminate} type="checkbox" className={BOX}
                      aria-label="불러온 작업 중 삭제 가능한 것 전체 선택"
                      checked={allChecked} disabled={deletableIds.length === 0 || del.isPending}
                      onChange={toggleAll} /></th>
                )}
                <th className="py-2">요청</th><th>요청자</th><th>작업</th>
                <th>대상</th><th>우선순위</th><th>상태</th><th>생성</th><th>갱신</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.request_id} className="border-t border-black/5 align-top">
                  {/* 행 td 에는 <input> 하나만(래퍼 금지 -- e2e L2: td 의 computed display=table-cell). 선택 불가는
                      disabled 로 끝내지 않고 title 로 이유와 동선을 남긴다(진짜 차단은 서버 판정). */}
                  {isAdmin && (
                    <td className="px-3 py-2"><input type="checkbox" className={BOX}
                         aria-label={`작업 ${r.request_id.slice(0, 12)} 선택`}
                         checked={selSet.has(r.request_id)}
                         disabled={!deletable(r) || del.isPending}
                         title={r.batch_id != null ? BATCH_HINT : deletable(r) ? undefined : ACTIVE_HINT}
                         onChange={() => toggleRow(r.request_id)} /></td>
                  )}
                  <td className="py-2">
                    {/* 전체 request_id 를 DOM 에 둔다(textContent) -- e2e 가 전체 id 로
                        행을 찾고, 표시는 CSS truncate 로만 줄인다(잘라 렌더하면 e2e
                        의 hasText 전체-id 매칭이 깨진다). */}
                    <Link className="text-accent font-mono text-xs block max-w-[10rem] truncate"
                          title={r.request_id} to={`/jobs/${r.request_id}`}>
                      {r.request_id}
                    </Link>
                  </td>
                  <td className="whitespace-nowrap">{r.requester_id}</td>
                  <td>{r.operation}</td>
                  {/* 대상 요약: sync 는 src→dst, scan/rm 은 storage:target (배치 항목
                      요약과 같은 규칙 -- pathSummary 공용). break-all 로 길어도 접는다. */}
                  {/* min-w: 좁은 폭(375px)에서 체크 열이 더해지면 이 열이 두어 글자 폭으로 짓눌려 행 하나가 화면 높이를
                      넘었다(2026-10-09 검증 지적) -- 최소 폭을 지키고 넘치는 만큼은 표의 가로 스크롤(Table)에 맡긴다. */}
                  <td className="text-muted break-all min-w-[8rem]">{pathSummary(r.operation, r.payload)}</td>
                  <td className="text-muted">{r.priority}</td>
                  <td><StatusPill state={r.state} /></td>
                  <td className="text-muted whitespace-nowrap">{kstStamp(r.created_at)}</td>
                  <td className="text-muted whitespace-nowrap">{kstStamp(r.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </Table>
          {q.isLoading ? <p className="text-muted pt-3">불러오는 중…</p>
           : rows.length === 0 ? <p className="text-muted pt-3">조건에 맞는 작업이 없습니다</p> : (
            /* 무한 스크롤 감시 노드 + 상태 문구(데이터 있을 때만). */
            <div ref={sentinel} className="pt-3 text-center text-xs text-muted">
              {q.isFetchingNextPage ? "더 불러오는 중…"
               : q.hasNextPage ? "스크롤하면 더 불러옵니다" : "마지막입니다"}
            </div>
          )}
          </>
        )}
      </Card>
    </section>
  );
}

// 일괄 작업 툴바(관리자). **늘 렌더된다**(자리 예약 -- BatchesList 관례): 조건부로 나타나면 체크 한 번에 표가
// 밀려 방금 조준한 행이 커서 밑에서 도망간다. 버튼도 늘 렌더해 높이를 구조로 고정하고, 미선택이면 disabled.
// 표 **밖**이다 -- td 안에 버튼·flex 를 넣으면 e2e L2 가 문다.
function DeleteToolbar({ selectedRows, del, purge, dropped, statusRef, onDeleted, onClear }: {
  selectedRows: RequestRow[];
  del: ReturnType<typeof useDeleteRequests>;
  purge: PurgeStatus | undefined;
  /** 유령 정리로 선택에서 빠진 수(알림용). */
  dropped: number;
  statusRef: React.RefObject<HTMLSpanElement>;
  onDeleted: () => void;
  onClear: () => void;
}) {
  const n = selectedRows.length;
  const none = n === 0;
  const over = n > MAX_DELETE;
  const scans = selectedRows.filter(isSucceededScan).length;
  const r = del.data;
  const droppedNote = dropped > 0 ? `${dropped}개는 목록에서 사라져 선택에서 뺐습니다` : "";
  // 한 줄은 넷 중 하나다(선택 > 직전 결과 > 선택에서 빠짐 알림 > 안내). 새로 고르기 시작하면 disarm() 이 결과를 지우므로
  // 실제로는 배타적이다. 성공 scan 수는 확인 창 경고의 예고다(사용량 분석 지점이 사라진다 -- 0 이면 소음이라 생략).
  const barText = !none
    ? `${n}개 선택됨${scans > 0 ? `(성공 scan ${scans}개)` : ""}`
      + (droppedNote ? ` · ${droppedNote}` : "")
      + (over ? ` — ${reasonText("delete_selection_too_large")}` : "")
    : r ? (r.skipped.length === 0 ? `${r.deleted.length}개 삭제됨`
      : r.deleted.length === 0 ? `삭제된 작업 없음 · ${r.skipped.length}개 제외`
      : `${r.deleted.length}개 삭제됨 · ${r.skipped.length}개 제외`)
    : droppedNote ? `선택한 작업 중 ${droppedNote}`
    : IDLE_HINT;
  // 안내도 읽혀야 하는 글자다 -- text-muted(#888, 흰 3.54:1)는 장식 전용이라 ink/70(4.94:1, AA)(2026-10-09 검증 지적).
  const barTone = !none ? (over ? "text-bad" : droppedNote ? "text-attn" : "")
    : r ? (r.skipped.length > 0 ? "text-bad" : "text-ok")
    : droppedNote ? "text-attn" : "text-ink/70";
  // 정리 상태(결과 파일·파드 -- 컨트롤러가 비동기로). 바 안에는 **짧은 건수만**(「결과 파일·파드 정리 중 N건 · 지연
  // M건」) -- 넓은 화면에서 툴바가 두 줄로 접히지 않게. 지연 사유는 긴 문장이라 바 **아래** 한 줄로(지연은 운영자 확인이
  // 필요한 드문 상태라 나타날 때 한 번 늘어나는 것은 알림으로 받아들인다 -- 제외 사유 줄과 같은 규칙). 사유는 **지연된**
  // 첫 행(오래된 순)의 것이다 -- items[0] 은 정상 대기일 수 있다. 대기 0건이면 글자가 없다(평소 화면에 소음 0) -- 자리는
  // 남는다(아래 정리 자리 주석).
  const stalledItem = purge?.items.find((i) => i.last_error !== null);
  const pendingNow = purge !== undefined && purge.pending > 0;
  return (
    <>
      <div role="toolbar" aria-label="작업 일괄 처리"
           className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-sm">
        {/* role="status" 는 **늘 있는** 노드에 -- 라이브 리전은 내용이 바뀌기 전부터 DOM 에 있어야 읽힌다. tabIndex -1:
            삭제가 끝나 확인 창이 닫히면 포커스가 여기로 온다(결과를 읽고 다음 Tab 은 표로).
            높이 고정(자리 예약, 2026-10-09 검증 지적): lg 미만에선 상태 줄이 **늘 자기 행**(basis-full)이고 두 줄 높이를
            예약한다 -- 문구 길이에 따라 버튼과 한 행/두 행을 오가면 체크 한 번에 툴바가 48px 줄어 표가 포인터 밑에서
            튀었다(414~768px). lg 이상은 한 행에 다 들어간다(문구가 짧다). */}
        <span role="status" ref={statusRef} tabIndex={-1}
              className={`mr-auto flex min-h-[2.5rem] basis-full items-center break-keep rounded lg:min-h-0 lg:basis-auto ${barTone} focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent`}>
          {barText}
        </span>
        {/* 정리 자리(**늘 렌더** -- 자리 예약, 2026-10-09 검증 지적). 정리 대기는 누가 지웠든 삭제마다 생기고 컨트롤러가
            끝내면 다음 폴링에 사라진다(전역). lg 미만에서 이 글자가 버튼 행에 끼면 360~393px 에서 「선택 해제」가 다음
            줄로 밀려 툴바가 46px 늘었다 줄었다 했고, 다음 행을 고르던 손가락 밑에서 표가 튀었다. 넓은 화면(1024~1150px)도
            선택 문구와 정리 문구가 한 행을 나누면 두 행으로 접혀 28px 튀었다 -- 그래서 **모든 폭에서** 자기 행(맨 아래 --
            order-last, 비어 있어도 한 줄 높이)이다. */}
        <span className="order-last min-h-[1rem] basis-full break-keep text-xs text-ink/70">
          {pendingNow && `결과 파일·파드 정리 중 ${purge.pending}건`}
          {pendingNow && purge.stalled > 0 && <span className="text-attn">{` · 지연 ${purge.stalled}건`}</span>}
        </span>
        {/* 두 버튼은 한 덩어리(줄바꿈 없음) -- 좁은 폭에서도 「선택 삭제」와 「선택 해제」가 두 행으로 갈라지지 않는다. */}
        <div className="flex shrink-0 items-center gap-2">
          <DeleteRequestsDialog rows={selectedRows} disabled={none || over || del.isPending} del={del}
                                onDeleted={onDeleted} focusAfterDelete={() => statusRef.current?.focus()} />
          <Button variant="ghost" disabled={none || del.isPending} onClick={onClear}>선택 해제</Button>
        </div>
        {/* 선택 규칙(늘 있다 -- 위 IDLE_HINT 주석). 자기 행이라 상태 문구가 바뀌어도 높이가 그대로다. */}
        <p className="basis-full break-keep text-xs text-ink/70">{RULE_HINT}</p>
      </div>
      {purge !== undefined && purge.stalled > 0 && stalledItem && (
        <p className="break-keep text-sm text-attn">{`정리 지연: ${reasonText(stalledItem.last_error)}`}</p>
      )}
      {/* 제외 사유는 바 **아래**다(부분 실패를 뭉개지 않는다 -- 어느 작업이 왜). 줄 수가 건수만큼 달라 예약 높이
          안에 넣을 수 없지만, 사용자가 삭제를 **명시적으로 실행한** 뒤 한 번만 늘고 그때 행도 함께 사라진다. */}
      {r && r.skipped.length > 0 && (
        // 사유별로 묶는다(사유 한 줄 + 개수 + 앞 3개 id) -- 대부분 제외되면 건마다 한 줄이 표를 한두 화면 아래로 밀었다.
        <div className="text-sm break-keep">
          {skippedGroups(r.skipped).map(([reason, ids]) => (
            <p key={reason} className="text-ink/70">
              {`${reasonText(reason)} (${ids.length}개: ${ids.slice(0, 3).map((i) => i.slice(0, 12)).join(", ")}`
                + `${ids.length > 3 ? ` 외 ${ids.length - 3}개` : ""})`}
            </p>
          ))}
        </div>
      )}
    </>
  );
}
