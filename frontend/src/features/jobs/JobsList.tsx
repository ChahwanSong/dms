import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { batchJobsPath, useDeleteRequests, useInfiniteRequests, usePurgeStatus } from "./useJobs";
import type { RequestFilters } from "./useJobs";
import { DeleteRequestsDialog, isSucceededScan } from "./DeleteRequestsDialog";
import { useMe } from "../auth/useAuth";
import { Table } from "../../components/ui/Table";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";
import { ApiError, reasonText } from "../../lib/api";
import { kstStampBare } from "../../lib/datetime";
import { REQUEST_TERMINAL_STATES } from "../../lib/jobState";
import { pathSummary } from "../../lib/storagePaths";
import type { DeleteRequestsResult, PurgeStatus, RequestRow } from "../../lib/types";
import { BATCH_BLOCK_LABEL, BATCH_ID_RE, MAX_DELETE_BATCHES, groupBatches, splitBatchName } from "./batchGroups";
import type { BatchGroup, BatchSummary } from "./batchGroups";

const field = "rounded-lg border border-line px-3 py-2 text-sm";

// 필터 select 값(빈 문자열 = 전체). 연산·상태는 도메인 열거의 미러 -- 서버는
// 미지 값에 0건을 주므로 여기 목록이 곧 사용자에게 유효한 값 집합이다. 상태는 **요청** 상태(domain.RequestState
// 8종)다 -- 잡 상태인 Previewing·ConfirmPending 이 섞여 있어 고르면 늘 0건이던 결함을 2026-10-08 에 뺐다.
const OPERATIONS = ["sync", "scan", "rm"];
const STATES = ["Pending", "Planned", "Running", "Succeeded", "Failed", "Rejected", "Cancelled", "Conflict"];

// 작업(요청) 선택 삭제(2026-10-08, 관리자 전용 -- 일반 사용자에게는 체크 열·툴바를 그리지 않는다).
// 한 번에 지울 수 있는 수 = 서버 MAX_DELETE(목록 API 상한 le=200 과 같다).
//
// 배치 단위 삭제(2026-10-10): 배치 자식은 여전히 **개별로는** 지우지 않는다(서버 batch_child_not_deletable -- 살아 있는
// 배치의 자식 하나를 지우면 오케스트레이터 집계가 영구 정체한다). 대신 배치 자식 행의 체크박스가 「배치 전체」 토글이다
// -- 같은 배치의 불러온 행이 함께 체크되고, 툴바·확인 창은 **서버가 센** 그 배치의 자식 수(재실행 이력 포함)를 보인다
// (batchGroups.ts). 배치 화면의 「배치 삭제」는 배치 기록만 지우고 작업은 남긴다 -- 그렇게 남은 묶음(배치 행 없음)도
// 여기서 「기록 없음 xxxxxxxxxxxx」로 보이고 같은 방식으로 지운다. 헤더 「전체 선택」은 단일 작업만 고른다(체크 하나에
// 화면 밖 수천 건이 걸리는 묶음을 일괄로 켜지 않는다 -- 그 사실을 헤더 체크박스 이름이 말한다).
//
// 한 배치의 작업만 보기(2026-10-11): 주소의 ?batch=<id> 가 서버 batch_id 필터가 된다(useJobs batchJobsPath) -- 배치
// 상세의 「전체 작업에서 이 배치의 작업 보기」와 「기록 없음」 칸이 여기로 온다. 오래된 배치의 작업은 무한 스크롤 수십 쪽
// 아래라 이 길이 없으면 배치 단위 삭제를 시작할 행을 찾을 수 없었다(검증 지적).
const MAX_DELETE = 200;
// 미선택 안내(상태 줄)는 짧게, 선택 규칙은 **늘 있는** 별도 줄(RULE_HINT)로 -- 키보드(disabled 는 Tab 이 건너뛴다)·
// 터치(375px) 사용자는 비활성 체크박스의 title(마우스 hover)을 볼 수 없다(2026-10-09 검증 지적). 규칙을 상태 줄에 붙여
// 두면 좁은 폭에서 미선택 안내만 두 줄로 접혀 첫 체크 순간 툴바 높이가 바뀌고 표가 포인터 밑에서 튄다(2026-10-09 검증
// 지적) -- 바뀌지 않는 줄은 높이도 바뀌지 않는다. 고를 수 없는 **배치**는 사유가 상태 열에 보이지 않으므로(행은
// Succeeded 인데 묶음은 진행 중·삭제 상한 초과일 수 있다) 배치 칸 둘째 줄에 사유를 보이고(BATCH_BLOCK_LABEL, 체크박스의
// aria-describedby) 이 줄이 그 자리를 가리킨다 -- 예전 문구(「배치가 끝난 뒤 배치 단위로 선택」)는 끝난 것처럼 보이는
// 상한 초과 배치·다른 쪽에 Pending 자식이 남은 Cancelled 배치에서 사실과 반대였다(2026-10-11 검증 지적). 짧게 둔다:
// 늘 있는 줄이라 그 줄 수가 곧 표가 밀리는 높이다 -- 긴 문장(「…배치 항목은 배치 단위로 선택·삭제되고, 고를 수 없는
// 배치는 배치 칸에 이유가 보입니다」)은 375px 에서 세 줄로 접혀 툴바를 HEAD 보다 32px 키웠다(2026-10-11 검증 지적).
// lg 미만 모든 폭에서 두 줄 이하이고 768 에선 한 줄(HEAD 와 같은 툴바 높이 -- e2e E7 이 375·768 에서 잰다. 한 줄 폭
// ~446px, 768 의 줄 폭 462px).
const IDLE_HINT = "끝난 작업을 선택해 삭제할 수 있습니다";
const RULE_HINT = "진행 중인 작업은 선택할 수 없습니다 · 배치는 배치 단위로 선택(못 고르면 배치 칸에 이유)";
// 선택 불가 사유(title). 배치 자식은 배치 묶음의 판정(batchBlock)이 사유를 고른다. 「배치 화면에서 관리」로 보내지
// 않는다 -- 배치 화면의 「배치 삭제」는 배치 기록만 지우고 작업은 남긴다(작업까지 지우는 길은 여기 배치 단위뿐).
const ACTIVE_HINT = "진행 중인 작업은 삭제할 수 없습니다 — 끝난 뒤에 삭제하세요(필요하면 상세에서 먼저 취소)";
// 요약을 모르는 배치 자식(구형 응답·id 형식 이상) -- 묶음으로도 고를 수 없다(fail-closed). 「배치 단위로 선택해
// 삭제하세요」(batch_child_not_deletable)로 안내하면 그 행은 배치 단위로도 고를 수 없는데 그렇게 하라는 말이 된다
// (2026-10-10 검증 지적) -- 고를 수 없는 이유를 그대로 말한다.
const BATCH_UNKNOWN_HINT = "배치 정보를 확인할 수 없어 선택할 수 없습니다";
const BATCH_ACTIVE_HINT = "진행 중인 배치입니다 — 배치가 끝난 뒤에(필요하면 배치 상세에서 먼저 취소) 배치 단위로 삭제하세요";
// 확인 대기(PreviewReady)는 스스로 끝나지 않는다 -- 「끝난 뒤에」를 기다리라고 하지 않고 할 일(확인·취소)을 말한다.
const BATCH_AWAITING_HINT = "확인 대기 중인 배치입니다 — 배치 상세에서 확인하거나 취소한 뒤 끝나면 배치 단위로 삭제하세요";
const BATCH_LIVE_HINT = "끝나지 않은 작업이 남은 배치입니다 — 모두 끝난 뒤 배치 단위로 삭제하세요";
// 체크박스 표적(BatchesList 와 같은 20px). **td 안 래퍼 금지**(e2e L2)라 크기는 input 자신에게 준다.
const BOX = "h-5 w-5 cursor-pointer align-middle";

/** 배치 묶음의 선택 불가 사유(배치 칸 둘째 줄의 짧은 사유 BATCH_BLOCK_LABEL 의 긴 말 -- title(마우스)과 그 줄의 화면
 *  낭독기 전용 글자(체크박스 설명)). ok 면 undefined. */
function batchBlockHint(g: BatchGroup): string | undefined {
  switch (g.block) {
    case "ok": return undefined;
    case "active": return BATCH_ACTIVE_HINT;
    case "awaiting": return BATCH_AWAITING_HINT;
    case "live": return BATCH_LIVE_HINT;
    case "too_large": return reasonText("batch_delete_too_large");
    default: return BATCH_UNKNOWN_HINT;
  }
}
// 헤더 「전체 선택」은 단일 작업만 고른다(배치 묶음은 행의 체크박스로) -- 이름이 「삭제 가능한 것 전체」면 고를 수 있는
// 배치까지 켜지는 것처럼 읽히는데 헤더가 checked 로 보여도 배치는 남는다(2026-10-10 검증 지적). 이름(보조기기)과
// title(마우스)이 그 범위를 말한다. 「단일 작업」은 메뉴·제출 화면과 같은 말이다(「단건」은 화면 어디에도 없었다 --
// 2026-10-11 검증 지적).
const ALL_LABEL = "불러온 단일 작업 중 삭제 가능한 것 전체 선택(배치 제외)";
const ALL_TITLE = "불러온 단일 작업 중 삭제 가능한 것을 모두 선택합니다 — 배치는 행의 체크박스로 배치 단위로 고릅니다";
// 고른 배치 수 상한을 넘었을 때 상태 줄에 붙이는 말. 짧게 둔다 -- 서버 문구(delete_batch_selection_too_large, 「한 번에
// 배치 10개까지 삭제할 수 있습니다」)를 붙이면 넓은 선택 문구와 함께 1024px 에서 툴바가 두 줄로, 375px 에서 세 줄로 늘어
// 방금 누른 행이 포인터 밑에서 밀렸다(2026-10-10 검증 지적 -- 2026-10-09 자리 예약이 막으려던 패턴). 잠긴 「선택 삭제」와
// text-bad 가 나머지를 말한다.
const OVER_BATCHES = `배치는 ${MAX_DELETE_BATCHES}개까지`;
/** 고를 수 있는 배치 묶음의 안내(title) -- 체크 하나가 무엇을 지우는지(화면 밖 자식까지)를 미리 말한다. */
function batchPickHint(g: BatchSummary): string {
  return g.exists
    ? `배치 단위로 선택됩니다 — 이 배치의 작업 ${g.request_count}개(재실행 이력 포함)와 배치 기록이 함께 삭제됩니다`
    : `배치 단위로 선택됩니다 — 배치 기록이 없는 묶음의 작업 ${g.request_count}개(재실행 이력 포함)가 함께 삭제됩니다`;
}

/** 제외 목록 → [사유, request_id 목록] (사유가 처음 나온 순서). 결과 요약 아래 사유별 한 줄용. */
export function skippedGroups(skipped: { request_id: string; reason: string }[]): [string, string[]][] {
  const groups = new Map<string, string[]>();
  for (const s of skipped) {
    const ids = groups.get(s.reason);
    if (ids) ids.push(s.request_id); else groups.set(s.reason, [s.request_id]);
  }
  return [...groups];
}

/** 유령 정리로 선택에서 빠진 배치 수 -- gone = 목록(불러온 범위)에서 사라짐, blocked = 아직 보이지만 고를 수 없게 됨. */
interface DroppedBatches { gone: number; blocked: number }
const NO_DROPPED_BATCHES: DroppedBatches = { gone: 0, blocked: 0 };

/** 빠짐 알림 한 줄(단건 · 배치 사라짐 · 배치 고를 수 없게 됨, 있는 것만 「 · 」로). */
export function droppedText(rows: number, batches: DroppedBatches): string {
  return [
    rows > 0 ? `${rows}개는 목록에서 사라져 선택에서 뺐습니다` : "",
    // 배치 알림은 짧게(2026-10-11 검증 지적): 선택 문구 뒤에 붙으면 넓은 화면(1024~1440)에서도 툴바가 두 줄로 커져 표가
    // 포인터 밑에서 밀렸다. 자세한 이유는 배치 칸의 사유 줄·title 이 말한다.
    batches.gone > 0 ? `배치 ${batches.gone}개 빠짐(목록에서 사라짐)` : "",
    batches.blocked > 0 ? `배치 ${batches.blocked}개 빠짐(삭제 불가)` : "",
  ].filter(Boolean).join(" · ");
}

/** 표시용 삭제 가능 판정(서버가 재판정한다 -- 화면은 3초 낡은 스냅숏). 요청 종단 ∧ 배치 자식 아님. batch_id 는
 *  `== null`(null·부재)만 단건이다 -- 빈 문자열 같은 이상값은 배치 자식으로 보고 선택 불가(fail-closed). 배치 자식은
 *  이 판정이 아니라 배치 묶음(batchBlock)으로 고른다. */
export function deletable(r: Pick<RequestRow, "state" | "batch_id">): boolean {
  return REQUEST_TERMINAL_STATES.has(r.state) && r.batch_id == null;
}

export function JobsList() {
  const me = useMe();
  const isAdmin = me.data?.role === "admin";
  const [operation, setOperation] = useState("");
  const [state, setState] = useState("");
  const [requester, setRequester] = useState("");
  // 한 배치의 작업만(?batch=<id>) -- 주소가 상태다(배치 상세·「기록 없음」 칸의 링크가 그대로 들어온다).
  const [params, setParams] = useSearchParams();
  const batch = params.get("batch") ?? "";
  // 필터 객체는 쿼리 키라 -- 매 렌더 새 객체면 키가 흔들려 재조회가 폭주한다.
  // 값이 실제로 바뀔 때만 새 객체를 만든다(useMemo).
  const filters: RequestFilters = useMemo(
    () => ({ operation: operation || undefined, state: state || undefined,
             requester: requester || undefined, batch: batch || undefined }),
    [operation, state, requester, batch]);
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

  // --- 배치 단위 선택(2026-10-10) ---
  // 선택 단위는 batch_id 다(행이 아니다). 묶음의 수·상태는 서버 요약(처음 나온 행의 값)이다.
  const [selectedBatches, setSelectedBatches] = useState<string[]>([]);
  // 유령 정리로 선택에서 빠진 배치 수(알림용 -- 단건 dropped 와 같은 규칙). 두 갈래를 따로 센다(아래 effect).
  const [droppedBatches, setDroppedBatches] = useState<DroppedBatches>(NO_DROPPED_BATCHES);
  const batchGroups = useMemo(() => groupBatches(rows), [rows]);
  const deletableBatchIds = useMemo(
    () => [...batchGroups.values()].filter((g) => g.block === "ok").map((g) => g.batch_id), [batchGroups]);
  // 유령 배치 정리: 단건 effect 와 같은 패턴(함수형 갱신 · 삭제 진행 중·결과가 떠 있는 동안은 세지 않음). 배치가 목록
  // 에서 사라졌거나(새 제출에 밀려 불러온 범위 밖으로 나감·다른 관리자가 지움) 고를 수 없게 됐으면(재실행으로 다시 활성화
  // -- 확인 창이 보인 수와 달라진다) 선택에서 뺀다 -- 보지 않은 상태의 배치를 지우지 않는다. 알림은 두 갈래를 나눈다:
  // 목록에서 사라졌을 뿐인 배치를 「삭제할 수 없게 돼」로 말하면 관리자가 배치가 다시 돌기 시작했다고 오해해 원인을
  // 찾는다(2026-10-10 검증 지적 -- 단건 알림은 처음부터 「목록에서 사라져」였다).
  useEffect(() => {
    const ok = new Set(deletableBatchIds);
    const lost = selectedBatches.filter((id) => !ok.has(id));
    if (lost.length === 0) return;
    setSelectedBatches((p) => p.filter((id) => ok.has(id)));
    if (!del.isPending && !del.isSuccess) {
      const blocked = lost.filter((id) => batchGroups.has(id)).length;
      setDroppedBatches((d) => ({ gone: d.gone + lost.length - blocked, blocked: d.blocked + blocked }));
    }
  }, [deletableBatchIds, batchGroups, selectedBatches, del.isPending, del.isSuccess]);
  // 고를 수 있는 묶음만(유령 정리 effect 가 곧 나머지를 뺀다 -- 그 사이 한 번의 렌더도 수를 모르는 묶음을 싣지 않는다).
  const selectedBatchGroups = useMemo(
    () => selectedBatches.flatMap((id) => {
      const g = batchGroups.get(id);
      return g && g.block === "ok" ? [g] : [];
    }), [batchGroups, selectedBatches]);
  const batchSel = new Set(selectedBatches);
  // 키보드 포커스를 쥔 배치 체크박스가 폴링으로 잠기면(다른 관리자가 재실행 → 배치 Running) 브라우저가 disabled 가 된
  // 요소에서 포커스를 빼 <body> 로 떨어뜨린다 -- 화면 낭독기는 자리를 잃는다(2026-10-11 검증 지적. 단일 작업 행은 종단이
  // 다시 비종단이 되지 않아 해당 없다). 그 묶음이 잠기거나 사라지면 포커스를 **같은 행의 요청 링크**로(행이 남아 있으면 --
  // 표 안의 자리를 지킨다, 다음 Tab 은 그 행의 다음 칸), 행이 사라졌으면 툴바 상태 줄로 옮긴다. 어느 쪽이든 스크롤하지
  // 않는다(preventScroll): 예전엔 상태 줄로 focus() 해 표 40번째 행을 읽던 화면이 맨 위로 튀었다(2026-10-11 검증 지적).
  // 무슨 일이 있었는지(「배치 1개 빠짐(삭제 불가)」)는 상태 줄의 라이브 리전이 포커스와 무관하게
  // 읽어 준다. **사용자가 일으킨 blur**(Tab·다른 칸 클릭·빈 곳 클릭 -- 빈 곳 클릭은 relatedTarget 이 null 이라 예전
  // 판정으로는 「disabled 로 빠짐」과 구별되지 않았다)면 기억을 지운다 -- 그 뒤의 잠김은 포커스를 건드리지 않는다.
  // 기억하는 것은 disabled 가 돼서 빠진 blur(브라우저에 따라 blur 자체가 없거나, 있으면 disabled 가 이미 참)뿐이다.
  const focusedBatch = useRef<{ batch: string; row: string } | null>(null);
  // 행별 요청 링크(위 포커스 복귀 자리) -- 같은 href 의 링크가 툴바 제외 줄에도 있어 href 로 찾지 않는다.
  const rowLinks = useRef(new Map<string, HTMLAnchorElement>());

  const selSet = new Set(selected);
  const allChecked = deletableIds.length > 0 && deletableIds.every((id) => selSet.has(id));
  // 배치만 골라도 헤더는 「일부 선택」이다(전체 선택은 단건만 다루지만 무엇인가 골라져 있다는 신호는 같다).
  const someChecked = (selected.length + selectedBatches.length) > 0 && !allChecked;
  // indeterminate 는 속성이 아니라 DOM 프로퍼티라 ref 로만 설정된다. **콜백 ref** 로 매 렌더 다시 건다 -- effect 로 걸면
  // 폴링 오류 동안 표가 오류 문구로 바뀌어 헤더 input 이 새로 마운트돼도 dep(someChecked)가 그대로라 다시 걸리지 않아,
  // 복구 뒤 일부 선택인데 빈 칸으로 보였다(2026-10-09 검증 지적).
  const setAllIndeterminate = (el: HTMLInputElement | null) => { if (el) el.indeterminate = someChecked; };
  // 성공해 확인 창이 닫히면 포커스를 둘 자리 -- 「선택 삭제」는 선택이 비어 잠기므로(Radix 기본 복귀가 <body> 로
  // 떨어진다) 늘 있는 결과 줄로 옮긴다. 다음 Tab 은 표(전체 선택)로 이어진다.
  const statusRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const fb = focusedBatch.current;
    if (fb === null) return;
    const g = batchGroups.get(fb.batch);
    if (g !== undefined && g.block === "ok") return;
    focusedBatch.current = null;
    const ae = document.activeElement;
    if (ae === null || ae === document.body || (ae instanceof HTMLElement && ae.dataset.batchId === fb.batch)) {
      const link = rowLinks.current.get(fb.row);
      (link !== undefined && link.isConnected ? link : statusRef.current)?.focus({ preventScroll: true });
    }
  }, [batchGroups]);
  // 선택이 바뀌면 직전 결과 문구를 지운다. 진행 중에는 reset 하지 않는다(비행 중 mutation 의 결과를 삼킨다).
  const disarm = () => { if (!del.isPending) del.reset(); };
  // 사용자가 선택을 다시 만지면 빠짐 알림(단건·배치 둘 다)은 낡은 말이다 -- 지운다.
  const touch = () => { disarm(); setDropped(0); setDroppedBatches(NO_DROPPED_BATCHES); };
  const toggleRow = (id: string) => {
    touch();
    setSelected((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  };
  // 배치 묶음 토글(배치 자식 행의 체크박스) -- 같은 배치의 불러온 행이 함께 체크된다.
  const toggleBatch = (bid: string) => {
    touch();
    setSelectedBatches((p) => (p.includes(bid) ? p.filter((x) => x !== bid) : [...p, bid]));
  };
  // 헤더 전체 선택은 **단건만** 다룬다 -- 배치 선택은 그대로 둔다.
  const toggleAll = () => { touch(); setSelected(allChecked ? [] : deletableIds); };
  const clearAll = () => {
    setSelected([]); setDropped(0); setSelectedBatches([]); setDroppedBatches(NO_DROPPED_BATCHES);
  };
  // 필터를 바꾸면 선택을 비운다 -- 이전 필터에서 고른(이제 안 보이는) 행이 선택에 남으면 보이지 않는 것을 지운다.
  // 배치 선택도 함께 비운다(필터 밖으로 나간 묶음을 보지 않고 지우지 않게).
  const onFilter = (set: (v: string) => void, v: string) => { set(v); clearAll(); disarm(); };
  // 배치 필터는 주소가 바꾼다(링크·뒤로 가기 포함) -- 바뀌면 같은 이유로 선택을 비운다.
  const seenBatch = useRef(batch);
  useEffect(() => {
    if (seenBatch.current === batch) return;
    seenBatch.current = batch;
    clearAll();
    disarm();
  }, [batch]);  // eslint-disable-line react-hooks/exhaustive-deps
  // 「필터 해제」는 자기가 든 칩째 사라진다 -- 그대로 두면 포커스가 <body> 로 떨어져 키보드·화면 낭독기 사용자가 자리를
  // 잃었다(다음 Tab 이 우연히 표 머리 체크박스로 -- 2026-10-11 검증 지적). **키보드로 눌렀을 때만**(click 의 detail 0 --
  // Enter·Space, 보조기기의 기본 동작) 늘 있는 바로 앞 필터(상태 필터)로 옮긴다. 마우스로 누른 경우는 옮기지 않는다:
  // Chrome 은 프로그램 포커스를 받은 select 에 직전 입력과 무관하게 :focus-visible 링을 그려, 마우스 사용자에게도 상태
  // 필터에 굵은 링이 남았다(2026-10-11 검증 지적 -- 「select 는 키보드로 왔을 때만 링」이라던 예전 주석은 사실과 달랐다).
  // 마우스면 사라지는 버튼에서 <body> 로 떨어져도 다음 클릭이 자리를 정하므로 해가 없다.
  const stateFilterRef = useRef<HTMLSelectElement>(null);
  const clearBatchFilter = (byKeyboard: boolean) => {
    setParams((p) => {
      const next = new URLSearchParams(p);
      next.delete("batch");
      return next;
    });
    if (byKeyboard) stateFilterRef.current?.focus({ preventScroll: true });
  };
  const batchFilterLabel = batch === "" ? null : (batchGroups.get(batch)?.label ?? batch.slice(0, 12));

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
        <select aria-label="상태 필터" ref={stateFilterRef} className={field} value={state}
                onChange={(e) => onFilter(setState, e.target.value)}>
          <option value="">상태 전체</option>
          {STATES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        {isAdmin && (
          <input aria-label="요청자 필터" className={field} placeholder="요청자 아이디"
                 value={requester} onChange={(e) => onFilter(setRequester, e.target.value)} />
        )}
        {/* 한 배치의 작업만 보는 중(주소 ?batch=) -- 무엇으로 걸렀는지와 푸는 길을 늘 보인다(필터가 숨어 있으면 「작업이
            사라졌다」로 읽힌다). 이름은 불러온 행의 배치 이름, 없으면 id 앞 12자. 좁은 폭에선 이름이 말줄임으로 잘리므로
            title 은 전체 이름 + id 다(id 만이면 hover 로도 이름을 볼 수 없었다 -- 2026-10-11 검증 지적). */}
        {batchFilterLabel !== null && (
          <span className="inline-flex max-w-full items-center gap-2 rounded-lg border border-accent/40 bg-accent/5 px-3 py-1.5 text-sm">
            <span className="min-w-0 truncate" title={`${batchFilterLabel} · ${batch}`}>{`배치 ${batchFilterLabel}의 작업만`}</span>
            <button type="button" className="shrink-0 text-accent underline"
                    aria-label="배치 필터 해제" onClick={(e) => clearBatchFilter(e.detail === 0)}>필터 해제</button>
          </span>
        )}
      </div>

      {isAdmin && (
        <DeleteToolbar selectedRows={selectedRows} selectedBatches={selectedBatchGroups} del={del} purge={purge.data}
                       dropped={dropped} droppedBatches={droppedBatches} statusRef={statusRef}
                       batchLabel={(bid) => batchGroups.get(bid)?.label ?? bid.slice(0, 12)}
                       onDeleted={clearAll}
                       onClear={() => { clearAll(); disarm(); }} />
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
                      aria-label={ALL_LABEL} title={ALL_TITLE}
                      checked={allChecked} disabled={deletableIds.length === 0 || del.isPending}
                      onChange={toggleAll} /></th>
                )}
                <th className="py-2">요청</th>
                {/* 배치 열(관리자만 -- 비관리자 목록엔 배치 자식이 사실상 나타나지 않고 배치 상세는 관리자 화면이다).
                    폭은 머리칸이 **예약**한다(BATCH_TH -- 내용과 무관): 예전엔 배치 행이 없으면 「—」 폭(35px)으로 줄어
                    폴링으로 첫 배치 자식이 들어오는 순간 표 전체가 다시 배치돼 단일 작업 행이 포인터 밑에서 ~240px
                    내려갔다(2026-10-11 검증 지적). */}
                {isAdmin && <th className={BATCH_TH}>배치</th>}
                <th>요청자</th><th>작업</th>
                <th>대상</th><th>우선순위</th><th>상태</th>
                {/* 시간대는 머리줄이 한 번 말한다 -- 칸마다 붙던 「 KST」 4글자를 배치 열 폭으로 돌려줬다(2026-10-10).
                    이 표엔 칸 여백이 없어 그 접미사가 두 시각 사이의 구분자 노릇을 했다 -- 빼고 나니 생성·갱신이 2px 로
                    붙어 한 문자열로 읽혔다(2026-10-11 검증 지적). 생성 칸 오른쪽 여백(pr-2, 8px)이 그 자리다(12px 는 1280
                    에서 대상 열 폭을 더 깎았다 -- 2026-10-11 검증 지적). */}
                <th className="pr-2">생성(KST)</th><th>갱신(KST)</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const group = r.batch_id != null ? batchGroups.get(r.batch_id) : undefined;
                const pickable = group !== undefined && group.block === "ok" ? group : null;
                const groupOn = group !== undefined && batchSel.has(group.batch_id);
                return (
                <tr key={r.request_id}
                    className={`border-t border-black/5 align-top ${groupOn ? "bg-accent/5" : ""}`}>
                  {/* 행 td 에는 <input> 하나만(래퍼 금지 -- e2e L2: td 의 computed display=table-cell). 선택 불가는
                      disabled 로 끝내지 않고 title 로 이유와 동선을 남긴다(진짜 차단은 서버 판정). 배치 자식 행은 「배치
                      전체」 토글이다 -- 체크 하나가 그 배치의 작업 전부(화면 밖 포함)를 고른다는 것을 이름·title 이 말하고,
                      같은 배치의 행이 함께 체크되고 옅은 배경이 깔리는 것이 묶음 선택의 시각 신호다. */}
                  {isAdmin && (group === undefined ? (
                    <td className="px-3 py-2"><input type="checkbox" className={BOX}
                         aria-label={`작업 ${r.request_id.slice(0, 12)} 선택`}
                         checked={selSet.has(r.request_id)}
                         disabled={!deletable(r) || del.isPending}
                         title={deletable(r) ? undefined : ACTIVE_HINT}
                         onChange={() => toggleRow(r.request_id)} /></td>
                  ) : pickable !== null ? (
                    <td className="px-3 py-2"><input type="checkbox" className={BOX}
                         aria-label={`작업 ${r.request_id.slice(0, 12)} 선택 — 배치 ${pickable.label} 전체(작업 ${pickable.request_count}개)`}
                         checked={groupOn}
                         disabled={del.isPending}
                         title={batchPickHint(pickable)}
                         data-batch-id={pickable.batch_id}
                         onFocus={() => { focusedBatch.current = { batch: pickable.batch_id, row: r.request_id }; }}
                         onBlur={(e) => {
                           // disabled 가 돼서 빠진 blur 만 기억에 남긴다(위 focusedBatch 주석) -- 떼어진 노드의 blur 도 같다.
                           if (!e.currentTarget.disabled && e.currentTarget.isConnected) focusedBatch.current = null;
                         }}
                         onChange={() => toggleBatch(pickable.batch_id)} /></td>
                  ) : (
                    // 고를 수 없는 묶음: 사유는 배치 칸 둘째 줄에 **보인다**(aria-describedby -- title 은 마우스 전용).
                    <td className="px-3 py-2"><input type="checkbox" className={BOX}
                         aria-label={`작업 ${r.request_id.slice(0, 12)} 선택`}
                         aria-describedby={batchWhyId(r.request_id)}
                         checked={false} disabled title={batchBlockHint(group)} data-batch-id={group.batch_id}
                         onChange={() => {}} /></td>
                  ))}
                  <td className="py-2">
                    {/* 전체 request_id 를 DOM 에 둔다(textContent) -- e2e 가 전체 id 로
                        행을 찾고, 표시는 CSS truncate 로만 줄인다(잘라 렌더하면 e2e
                        의 hasText 전체-id 매칭이 깨진다). 관리자는 6.5rem(앞 13자 + 말줄임 -- 확인 창·결과 줄이 쓰는
                        12자는 늘 보인다): 배치 열에 자리를 내줬다(1280 에서 대상 열이 HEAD(배치 열 없음)의 208px 이상 --
                        단일 sync 행이 HEAD 보다 높아지지 않는다, 2026-10-11 실측. 아래 BATCH_W 주석).
                        배치 열이 없는 일반 사용자는 예전 10rem 그대로(2026-10-11 검증 지적 -- 이유 없이 줄었다). */}
                    <Link className={`text-accent font-mono text-xs block ${isAdmin ? "max-w-[6.5rem]" : "max-w-[10rem]"} truncate`}
                          ref={(el) => {
                            if (el !== null) rowLinks.current.set(r.request_id, el);
                            else rowLinks.current.delete(r.request_id);
                          }}
                          title={r.request_id} to={`/jobs/${r.request_id}`}>
                      {r.request_id}
                    </Link>
                  </td>
                  {isAdmin && <BatchCell row={r} group={group} />}
                  <td className="whitespace-nowrap">{r.requester_id}</td>
                  <td>{r.operation}</td>
                  {/* 대상 요약: sync 는 src→dst, scan/rm 은 storage:target (배치 항목
                      요약과 같은 규칙 -- pathSummary 공용). break-all 로 길어도 접는다. */}
                  {/* min-w: 좁은 폭(375px)에서 체크 열이 더해지면 이 열이 두어 글자 폭으로 짓눌려 행 하나가 화면 높이를
                      넘었다(2026-10-09 검증 지적) -- 최소 폭을 지키고 넘치는 만큼은 표의 가로 스크롤(Table)에 맡긴다. */}
                  <td className="text-muted break-all min-w-[8rem]">{pathSummary(r.operation, r.payload)}</td>
                  <td className="text-muted">{r.priority}</td>
                  <td><StatusPill state={r.state} /></td>
                  <td className="text-muted whitespace-nowrap pr-2">{kstStampBare(r.created_at)}</td>
                  <td className="text-muted whitespace-nowrap">{kstStampBare(r.updated_at)}</td>
                </tr>
                );
              })}
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

// 「배치」 칸(관리자). 이 행이 어느 배치의 작업인지 -- 단일 작업은 「—」, 살아 있는 배치는 상세로 가는 링크(이름, 없으면
// id 앞 12자), 배치 기록만 지워진 묶음은 「기록 없음 xxxxxxxxxxxx」(상세는 404 라 보내지 않는다 -- 대신 그 묶음의 작업만
// 거른 이 목록으로 간다), id 형식 이상값은 그 사실을 그대로. 버튼 금지(e2e L4 표 버튼 한 줄 -- 링크·글자만).
//
// 폭(실측 예산, 2026-10-11 다시 맞춤): **모든 폭에서 6rem 고정**이고 그 폭은 머리칸(BATCH_TH)이 예약한다 -- 배치 행이
// 있든 없든 열 폭이 같아 폴링으로 첫 배치 행이 들어오거나 마지막 배치를 지워도 표가 다시 배치되지 않는다. 1280 예산:
// 요청 열 6.5rem·시각 칸 「 KST」 제거·생성 칸 여백 8px 과 한 묶음으로 대상 열이 1280~1920 모든 폭에서 HEAD(배치 열 없음)
// 이상이다(1280: 208 → 210px -- 단일 행이 HEAD 보다 높아지는 폭이 없다, 2026-10-11 실측). 1440px 이상에 두던 12rem 은
// 짧은 이름(「e7 배치」)에도 열을 201px 로 넓혀 대상 열을 HEAD 보다 120px 깎았다(모든 sync 행 43 → 63px -- 2026-10-11
// 검증 지적) -- 그래서 넓은 화면도 6rem 이고 긴 이름은 두 줄을 받아들인다. 칸 오른쪽 여백(pr-2)은 이름 끝이 다음 칸(요청자)에 붙어 「이관(1차)mason」으로 읽히지 않게 한다.
// 이름 표시(2026-10-11 검증 지적): 예전의 「두 줄 line-clamp」는 이름 **앞** ~12자만 보여 접두가 같은 배치(「…이관(1차)」·
// 「…(2차)」, 「d161 … 1007c」)를 구별하지 못했다 -- 가르는 것은 대개 끝이다. 그래서 긴 이름은 앞 조각(text-sm 한 줄 말줄임)
// + 뒤 조각(이름의 끝, splitBatchName -- text-xs)의 두 줄로 나눠 **끝을 보인다**. 전체 이름은 title(마우스)과 링크
// 이름(aria-label). 고를 수 없는 묶음이면 **둘째 줄에 사유**(BATCH_BLOCK_LABEL -- 「배치 진행 중」·「배치 확인 대기」·「미완료
// 작업 있음」·「삭제 상한 초과」)를 보인다: 묶음의 상태·수는 행의 상태 열에 없어 Succeeded 행의 잠긴 체크박스가 왜
// 잠겼는지 키보드·터치로는 알 길이 없었다(2026-10-11 검증 지적). 체크박스가 aria-describedby 로 이 줄을 가리키고, 줄
// 안의 화면 낭독기 전용 글자(sr-only)가 처방(긴 말)까지 읽힌다 -- describedby 가 있으면 title 은 설명으로 쓰이지 않아 「삭제
// 상한 초과」만 들리고 어떤 상한인지(작업 1000·항목 10000)는 마우스 전용이었다(2026-10-11 검증 지적). 읽혀야 하는 글자라
// ink/70(AA).
//
// **칸은 상태와 무관하게 두 줄 이하**(2026-10-11 검증 지적): 첫 줄 = 이름(짧으면 전체, 길면 앞 조각)·「기록 없음」, 둘째
// 줄(text-xs) = 사유 **또는** 뒤 조각·id. 사유 줄을 덧붙이면(세 줄) 긴 이름 배치가 끝나거나(Running → Completed -- 관리자가
// 지우려고 기다리는 바로 그 순간) 다시 돌 때 그 배치의 모든 행이 59 ↔ 43px 로 바뀌어 표가 포인터 밑에서 수백 px 움직였다.
// 두 줄(text-sm 20 + text-xs 16)은 체크박스 행 높이(39px) 안이라 상태가 바뀌어도 행 높이가 그대로다(e2e E7 이 잰다).
// 막힌 동안 가려지는 뒤 조각·id 는 title·링크 이름에 그대로 있다.
const BATCH_W = "w-[6rem] min-w-[6rem]";
/** 배치 머리칸 -- 칸 내용 6rem(BATCH_W) + 오른쪽 여백 pr-2(0.5rem) + **UA 기본 왼쪽 여백 1px**(boxed 아닌 표의 td·th 는
 *  padding 1px 이 남는다)을 border-box 폭으로 예약한다(위 주석). 1px 을 빼먹은 6.5rem(104px)은 링크 칸(1 + 96 + 8 = 105px)
 *  보다 짧아, 첫 배치 행이 들어오는 순간 열이 104 → 105px 로 넓어지고 대상 열이 줄어 경계 길이의 대상이 한 줄 늘었다
 *  (2026-10-11 검증 지적 -- 「표가 다시 배치되지 않는다」가 1px 차이로 거짓이었다). */
const BATCH_TH = "pr-2 w-[calc(6.5rem_+_1px)] min-w-[calc(6.5rem_+_1px)]";
/** 배치 칸 사유 줄의 id(행마다 하나 -- 체크박스의 aria-describedby). */
const batchWhyId = (requestId: string) => `batch-why-${requestId}`;
/** 사유 줄 안의 화면 낭독기 전용 처방(위 주석) -- 보이는 줄의 높이는 그대로다(sr-only 는 흐름 밖). */
function WhyHint({ hint }: { hint: string | undefined }) {
  return hint === undefined ? null : <span className="sr-only">{` — ${hint}`}</span>;
}
function BatchCell({ row, group }: { row: RequestRow; group: BatchGroup | undefined }) {
  const bid = row.batch_id;
  if (bid == null) return <td className="pr-2 text-muted">—</td>;
  const whyId = batchWhyId(row.request_id);
  if (!BATCH_ID_RE.test(bid)) {
    return (
      <td className="pr-2">
        <span id={whyId} className="whitespace-nowrap text-attn" title={`배치 id 형식 이상: ${JSON.stringify(bid)}`}>
          배치(이상값)<WhyHint hint={group !== undefined ? batchBlockHint(group) : BATCH_UNKNOWN_HINT} />
        </span>
      </td>
    );
  }
  const blocked = group !== undefined && group.block !== "ok" ? group : null;
  // 사유 줄은 한 줄(truncate -- 짧은 말이라 실제로는 잘리지 않지만 두 줄로 접혀 행이 커지는 일은 구조로 막는다).
  const why = blocked !== null ? (
    <span id={whyId} className="block truncate text-xs text-ink/70" title={batchBlockHint(blocked)}>
      {BATCH_BLOCK_LABEL[blocked.block]}<WhyHint hint={batchBlockHint(blocked)} />
    </span>
  ) : null;
  if (row.batch_exists === false) {
    // 기록이 없는 묶음 -- 상세(404) 대신 그 묶음의 작업만 거른 목록으로(재실행 이력까지 한자리에서 보고 고른다). id 는
    // 확인 창·결과 줄과 같은 12자(「기록 없음」 다음 줄로 접힌다). 「삭제됨」은 좁은 칸에서 작업 자체가 지워졌다는 말로
    // 읽혔다(2026-10-11 검증 지적). 막혔으면 id 줄 자리에 사유가 선다(링크 이름은 id 를 그대로 품는다).
    const short = bid.slice(0, 12);
    return (
      <td className="pr-2">
        <Link to={batchJobsPath(bid)} title={`배치 기록 없음 · ${bid} — 이 묶음의 작업만 보기`}
              aria-label={blocked !== null ? `기록 없음 ${short}` : undefined}
              className={`block ${BATCH_W} break-keep text-xs text-accent`}>
          {blocked !== null ? "기록 없음" : <>기록 없음 <span className="font-mono">{short}</span></>}
        </Link>
        {why}
      </td>
    );
  }
  // 살아 있는 배치, 또는 요약이 없는 구형 응답(있는지 모른다 -- 링크를 두고 상세가 판정한다).
  const name = typeof row.batch_name === "string" && row.batch_name.trim() !== "" ? row.batch_name : null;
  const parts = name !== null ? splitBatchName(name) : null;
  return (
    <td className="pr-2">
      <Link to={`/admin/batches/${bid}`} title={`${name ?? "이름 없음"} · ${bid}`}
            aria-label={parts !== null ? (name as string) : undefined}
            className={`block ${BATCH_W} text-accent ${parts !== null ? ""
              : `truncate ${name !== null ? "" : "font-mono text-xs"}`}`}>
        {parts === null ? (name ?? bid.slice(0, 12)) : (
          <>
            <span className="block truncate">{parts.head}</span>
            {blocked === null && <span className="block truncate text-xs">{parts.tail}</span>}
          </>
        )}
      </Link>
      {why}
    </td>
  );
}

/** 제외 사유 줄의 작업 id -- 앞 12자를 보이는 상세 링크(전체 id 는 title). 표 **밖**(툴바 아래 문단)이라 e2e L2·L4 와
 *  무관하다. */
function RequestLink({ id }: { id: string }) {
  return (
    <Link to={`/jobs/${id}`} title={id} className="font-mono text-xs text-accent hover:underline">{id.slice(0, 12)}</Link>
  );
}

// 일괄 작업 툴바(관리자). **늘 렌더된다**(자리 예약 -- BatchesList 관례): 조건부로 나타나면 체크 한 번에 표가
// 밀려 방금 조준한 행이 커서 밑에서 도망간다. 버튼도 늘 렌더해 높이를 구조로 고정하고, 미선택이면 disabled.
// 표 **밖**이다 -- td 안에 버튼·flex 를 넣으면 e2e L2 가 문다.
/** 선택 중 문구. n = 단건 수, batches = 배치 묶음 수, children = 배치 자식 수 합(서버 수), scans = 성공 scan 수
 *  (단건 + 배치 자식). 단건만이면 기존 문구 그대로(e2e 06). */
export function selectionText(n: number, batches: number, children: number, scans: number): string {
  if (batches === 0) return `${n}개 선택됨${scans > 0 ? `(성공 scan ${scans}개)` : ""}`;
  if (n === 0) return `배치 ${batches}개 선택됨(작업 ${children}개${scans > 0 ? ` · 성공 scan ${scans}개` : ""})`;
  return `${n}개 + 배치 ${batches}개(작업 ${children}개) 선택됨${scans > 0 ? ` · 성공 scan ${scans}개` : ""}`;
}

/** 직전 삭제 결과 한 줄. 단건만 보낸 응답이면 기존 세 문구(「N개 삭제됨」「삭제된 작업 없음 · N개 제외」「N개 삭제됨 ·
 *  M개 제외」)와 글자 단위로 같다(e2e 06). 배치는 「배치 B개(작업 M개)」로 지운 자식 수까지 말한다. */
export function deleteResultText(r: DeleteRequestsResult): string {
  const d = r.deleted.length, s = r.skipped.length;
  const bd = r.deleted_batches ?? [], bs = r.skipped_batches ?? [];
  const children = bd.reduce((a, x) => a + x.request_ids.length, 0);
  const done = [d > 0 ? `${d}개` : "", bd.length > 0 ? `배치 ${bd.length}개(작업 ${children}개)` : ""].filter(Boolean);
  const head = done.length > 0 ? `${done.join(" · ")} 삭제됨` : "삭제된 작업 없음";
  const tail = [s > 0 ? `${s}개 제외` : "", bs.length > 0 ? `배치 ${bs.length}개 제외` : ""].filter(Boolean);
  return [head, ...tail].join(" · ");
}

function DeleteToolbar({ selectedRows, selectedBatches, del, purge, dropped, droppedBatches, statusRef, batchLabel,
                         onDeleted, onClear }: {
  selectedRows: RequestRow[];
  /** 고른 배치 묶음(전부 고를 수 있는 것 -- 유령 정리가 보장). */
  selectedBatches: BatchSummary[];
  del: ReturnType<typeof useDeleteRequests>;
  purge: PurgeStatus | undefined;
  /** 유령 정리로 선택에서 빠진 수(알림용). */
  dropped: number;
  /** 유령 정리로 선택에서 빠진 배치 수(알림용 -- 사라짐·고를 수 없게 됨을 나눈다). */
  droppedBatches: DroppedBatches;
  statusRef: React.RefObject<HTMLSpanElement>;
  /** 배치 제외 줄의 이름(지금 목록의 묶음 이름, 없으면 id 앞 12자). */
  batchLabel: (batchId: string) => string;
  onDeleted: () => void;
  onClear: () => void;
}) {
  const n = selectedRows.length;
  const nb = selectedBatches.length;
  const none = n === 0 && nb === 0;
  const over = n > MAX_DELETE;
  const overBatches = nb > MAX_DELETE_BATCHES;
  const children = selectedBatches.reduce((a, b) => a + b.request_count, 0);
  const scans = selectedRows.filter(isSucceededScan).length + selectedBatches.reduce((a, b) => a + b.scans, 0);
  const r = del.data;
  const droppedNote = droppedText(dropped, droppedBatches);
  // 한 줄은 넷 중 하나다(선택 > 직전 결과 > 선택에서 빠짐 알림 > 안내). 새로 고르기 시작하면 disarm() 이 결과를 지우므로
  // 실제로는 배타적이다. 성공 scan 수는 확인 창 경고의 예고다(사용량 분석 지점이 사라진다 -- 0 이면 소음이라 생략).
  // 배치는 서버가 센 자식 수(재실행 이력 포함)를 말한다 -- 체크 하나에 화면 밖 작업까지 걸린다는 것이 여기서 보인다.
  const barText = !none
    ? selectionText(n, nb, children, scans)
      + (droppedNote ? ` · ${droppedNote}` : "")
      + (over ? ` — ${reasonText("delete_selection_too_large")}` : "")
      + (overBatches ? ` — ${OVER_BATCHES}` : "")
    : r ? deleteResultText(r)
    : droppedNote ? (dropped > 0 ? `선택한 작업 중 ${droppedNote}` : droppedNote)
    : IDLE_HINT;
  // 안내도 읽혀야 하는 글자다 -- text-muted(#888, 흰 3.54:1)는 장식 전용이라 ink/70(4.94:1, AA)(2026-10-09 검증 지적).
  const skippedBatches = r?.skipped_batches ?? [];
  const barTone = !none ? (over || overBatches ? "text-bad" : droppedNote ? "text-attn" : "")
    : r ? (r.skipped.length + skippedBatches.length > 0 ? "text-bad" : "text-ok")
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
          <DeleteRequestsDialog rows={selectedRows} batches={selectedBatches}
                                disabled={none || over || overBatches || del.isPending} del={del}
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
      {r && r.skipped.length + skippedBatches.length > 0 && (
        // 사유별로 묶는다(사유 한 줄 + 개수 + 앞 3개 id) -- 대부분 제외되면 건마다 한 줄이 표를 한두 화면 아래로 밀었다.
        // 배치는 배치마다 한 줄(최대 10개 -- 서버 상한): 「배치 X: 사유 (작업 abc…)」. 자식 게이트 사유(끝나지 않은 작업·
        // 방금 끝남 등)면 문제가 된 자식 id 를 붙인다 -- 그 작업의 상세에서 취소하거나 잠시 뒤 다시 시도하면 된다.
        // id 는 상세 **링크**다(2026-10-10 검증 지적): 상세 URL 엔 전체 id 가 필요한데 보이는 건 12자이고, 그 자식은 대개
        // 불러온 범위 밖이며, 찾으려고 필터를 바꾸면 이 줄이 지워진다. 없는 요청(request_not_found)은 링크가 죽은 상세라
        // 글자로만 둔다.
        <div className="text-sm break-keep">
          {skippedGroups(r.skipped).map(([reason, ids]) => (
            <p key={reason} className="text-ink/70">
              {`${reasonText(reason)} (${ids.length}개: `}
              {ids.slice(0, 3).map((i, n) => (
                <span key={i}>
                  {n > 0 && ", "}
                  {reason === "request_not_found" ? i.slice(0, 12) : <RequestLink id={i} />}
                </span>
              ))}
              {`${ids.length > 3 ? ` 외 ${ids.length - 3}개` : ""})`}
            </p>
          ))}
          {skippedBatches.map((s) => (
            <p key={`batch-${s.batch_id}`} className="text-ink/70">
              {`배치 ${batchLabel(s.batch_id)}: ${reasonText(s.reason)}`}
              {typeof s.request_id === "string" && <>{" (작업 "}<RequestLink id={s.request_id} />{")"}</>}
            </p>
          ))}
        </div>
      )}
    </>
  );
}
