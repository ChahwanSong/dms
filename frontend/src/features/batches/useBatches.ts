import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient,
         type QueryClient } from "@tanstack/react-query";
import { ApiError, apiGet, apiSend } from "../../lib/api";
import type { Batch, BatchDetail, BatchSummary, RequestScanStats } from "../../lib/types";
import { BATCH_ITEMS_PAGE_SIZE, headerOf, pageOf, payloadsOf, type BatchHeader,
         type BatchItemsParams } from "./batchView";

// 서버 routes_batches._TERMINAL_BATCH 의 거울. 상세 폴링 중지와 목록의 "삭제 가능"
// 판정이 같은 한 벌을 쓴다 — 두 벌로 갈라지면 화면이 지우지 못할 배치를 지울 수
// 있는 척한다(진짜 차단은 서버 409 batch_not_deletable).
const BATCH_TERMINAL = new Set(["Completed", "Cancelled"]);
export const isBatchTerminal = (status: string) => BATCH_TERMINAL.has(status);
// 배치 연산(2026-10-21 rm 추가). 「미리보기 → 「배치 확인」」을 거치는 연산 = 서버 domain.confirm_gated(scan 밖 전부)의
// 화면 쪽 거울. 화면은 **명시 집합**으로 본다: 모르는 연산 문자열(DB 신뢰 경계)을 확인 흐름이라고 추정해 「확인하면
// 삭제됩니다」 같은 문장을 지어내지 않는다(서버는 그런 행을 fail-closed 로 게이트에 묶는다 — 화면은 말을 아낀다).
export type BatchOp = "scan" | "sync" | "rm";
const CONFIRM_OPS: ReadonlySet<string> = new Set(["sync", "rm"]);
export const needsBatchConfirm = (op: string | undefined): boolean => op !== undefined && CONFIRM_OPS.has(op);
// 자동 확인(미리보기 후 자동 실행)을 켤 수 있는 연산 = 서버 domain.auto_confirmable 의 거울(sync 2026-10-11, rm 2026-10-21).
// 지금은 확인 흐름 집합과 같지만 따로 둔다 — 서버도 두 술어(confirm_gated·auto_confirmable)를 따로 두고, 다음 확인 흐름
// 연산이 자동 확인까지 저절로 얻지 않게(켜는 체크박스·「자동 실행 끄기」·자동 안내문이 이 술어 하나를 본다).
const AUTO_CONFIRM_OPS: ReadonlySet<string> = new Set(["sync", "rm"]);
export const autoConfirmable = (op: string | undefined): boolean => op !== undefined && AUTO_CONFIRM_OPS.has(op);
// 배치 목록(최근 100건). enabled = false 면 조회도 폴링도 없다 — 배치 스케줄러의 추가 창이 열렸을 때만 같은 키로 읽는다(배치
// 선택지 — 캐시·무효화를 목록 화면과 나눠 쓴다).
export const useBatches = ({ enabled = true }: { enabled?: boolean } = {}) =>
  useQuery({ queryKey: ["batches"], queryFn: () => apiGet<Batch[]>("/api/admin/batches"),
            enabled, refetchInterval: 4000 });
// 상세 폴링 규칙(머리·항목 쪽 공용): 종단이면 멈추고, 모르면(로딩) 돈다.
const pollWhile = (status: string | undefined) => (BATCH_TERMINAL.has(status ?? "") ? false : 2500);
// 상세 머리(2026-11-16 ?view=summary -- 배치 행 + 항목 집계, items 없음). 옛 파드는 view 를 무시하고 옛 전체 응답을 주므로
// headerOf 가 그 items 에서 같은 모양을 파생한다(batchView 머리 주석). 키는 그대로 ["batch", id] -- 무효화·제거가 이 접두로
// 걸리고(_refresh·useDeleteBatch·useJobs·useUsage), 항목 쪽·payloads 키가 그 아래에 있어 함께 걸린다.
export const useBatch = (id: string) =>
  useQuery({ queryKey: ["batch", id],
    queryFn: async (): Promise<BatchHeader> =>
      headerOf(await apiGet<BatchSummary>(`/api/admin/batches/${id}?view=summary`)),
    refetchInterval: (q) => pollWhile((q.state.data as BatchHeader | undefined)?.status) });
// 항목 한 쪽(?view=items). 키는 ["batch", id, "items", {page,q,status}] -- 접두 ["batch", id] 유지(위 주석). 쪽·검색·상태를
// 바꾸는 동안 앞 쪽을 자리표시로 둔다(같은 배치일 때만 -- 다른 배치의 항목이 이 배치 머리 밑에 비치면 안 된다). 폴링은 머리와
// 같은 규칙인데 항목 응답엔 배치 상태가 없어 호출측이 머리의 상태를 넘긴다. 옛 파드(전체 배치 응답)면 pageOf 가 화면에서
// 같은 규칙으로 거르고 자른다.
// 머리가 종단으로 넘어가는 순간 항목 쪽을 **한 번 더** 받는다: 머리와 항목은 따로 도는 두 폴링이라 항목의 마지막 조회가 머리가
// 종단을 본 시점보다 앞설 수 있고(최대 한 주기), 종단이면 둘 다 멈추므로 마지막 항목들의 끝 상태(성공·실패)를 영영 못 본 채
// 「Completed 배치 + Materialized 항목」으로 굳는다(예전엔 한 응답이라 이 어긋남이 없었다). 처음 불러올 때(앞 상태 모름)는
// 두 조회가 같은 순간에 떠서 받지 않는다 -- 쪽 하나를 두 번 받지 않게.
export const useBatchItems = (id: string, params: BatchItemsParams, batchStatus: string | undefined) => {
  const qc = useQueryClient();
  const seen = useRef(batchStatus);
  useEffect(() => {
    const was = seen.current;
    seen.current = batchStatus;
    if (was !== undefined && was !== batchStatus && BATCH_TERMINAL.has(batchStatus ?? "")) {
      void qc.invalidateQueries({ queryKey: ["batch", id, "items"] });
    }
  }, [qc, id, batchStatus]);
  return useQuery({ queryKey: ["batch", id, "items", params],
    queryFn: async () => {
      const p = new URLSearchParams({ view: "items", offset: String((params.page - 1) * BATCH_ITEMS_PAGE_SIZE),
                                      limit: String(BATCH_ITEMS_PAGE_SIZE) });
      if (params.q !== "") p.set("q", params.q);
      if (params.status !== null) p.set("status", params.status);
      return pageOf(await apiGet<unknown>(`/api/admin/batches/${id}?${p}`), params);
    },
    placeholderData: (prev, prevQuery) => (prevQuery?.queryKey[1] === id ? prev : undefined),
    refetchInterval: pollWhile(batchStatus) });
};
// 가벼운 전 항목(?view=payloads -- seq·payload·status): 현재 항목 CSV·rm 항목 추가의 겹침 즉답이 **창을 열었을 때만** 한 번
// 받는다(enabled = 열림). 폴링하지 않고, 열 때마다 다시 받는다(staleTime 0). gcTime 0 이라 구독자가 없어지면(현재 항목 CSV 창
// 본문은 열린 동안만 마운트) 캐시에서 버린다 -- 단 rm 항목 추가 창은 트리거와 함께 늘 마운트돼(비활성 구독) 지난 목록이 캐시에
// 남는다: 그래서 그 창은 다시 받는 동안(isFetching) 낡은 목록으로 즉답하지 않고 「항목 추가」를 잠근다(AddItemDialog).
export const useBatchPayloads = (id: string, enabled: boolean) =>
  useQuery({ queryKey: ["batch", id, "payloads"],
    queryFn: async () => payloadsOf(await apiGet<unknown>(`/api/admin/batches/${id}?view=payloads`)),
    enabled, gcTime: 0, refetchOnWindowFocus: false });
// 배치 확인 창의 전체 조회(?view=all -- 옛 전체 응답과 같은 모양: 배치 + 조인된 전 항목). 창을 열 때 **한 번**만 받고, 그
// 응답의 preview_round 를 확인 회차로 고정한다 -- 회차와 화면의 항목이 같은 응답에서 와야 본 적 없는 항목이 확인에 묻지
// 않는다(서버는 배치 행을 먼저 읽고 항목을 읽는다; 그 뒤 바뀌면 confirm CAS 가 거부한다). 그래서 키를 ["batch", id] 접두
// **밖**에 둔다: 열린 동안 다른 변이의 무효화가 항목만 새로 받아 회차와 어긋나게 하지 않도록. nonce = 연 횟수 -- 다시 열면
// 새 키라 반드시 새로 받는다(gcTime 0 + staleTime Infinity: 열린 동안 재조회 없음, 닫으면 버린다).
export const useBatchAll = (id: string, nonce: number) =>
  useQuery({ queryKey: ["batch-confirm", id, nonce],
    queryFn: () => apiGet<BatchDetail>(`/api/admin/batches/${id}?view=all`),
    staleTime: Infinity, gcTime: 0, refetchInterval: false,
    refetchOnWindowFocus: false, refetchOnReconnect: false });
export interface CreateBatchBody {
  operation: string; max_concurrency: number; options: Record<string, unknown>;
  note: string | null; items: Record<string, string>[];
  // 배치 이름: 빈값은 키 생략 = 서버 NULL(이름 없음) — priority 생략 계약의 미러.
  name?: string;
  // 실행 제어(슬라이스 32): 미지정은 키 생략 = 서버 NULL(정책 기본) — null≠0.
  priority?: string; node_count?: number;
  // 노드당 프로세스 수 override: node_count 와 같은 생략 계약의 미러.
  procs_per_node?: number;
  // 실행 신원(기록용 이름): 빈값은 키 생략 = 서버 NULL(생성자 본인). 특권 스위치가 아니다 --
  // 배치는 통일 게이트(routes_batches)로 항상 특권(root) 실행이다.
  owner_username?: string;
  // 자동 확인(sync 2026-10-11, rm 2026-10-21 — 기본 꺼짐): 켰을 때만 true 를 싣는다 -- 서버는 명시 true 만 켠다(생략 = 사람이 확인).
  auto_confirm?: true;
  // rm 자동 확인의 「되돌릴 수 없음」 동의 — rm 에서 자동 확인을 켜고 동의 체크를 켰을 때만 싣는다(없으면 서버 422
  // batch_rm_auto_confirm_ack_required).
  acknowledge_irreversible?: true;
}
export const useCreateBatch = () =>
  useMutation({ mutationFn: (b: CreateBatchBody) =>
    apiSend<{ batch_id: string; status: string }>("POST", "/api/admin/batches", b) });
// 배치 mutation 뒤 화면 갱신 계약(사용자 보고 2026-08-15: "삭제 후 새로고침이
// 안되고 화면에 남아있다"). 실측 결론: 무효화 **키**는 네 삭제 경로 모두 맞았고
// (상세 ["batch", id] + 목록 ["batches"]) 어긋난 건 **시점**이다 — 콜백이
// invalidate 의 프라미스를 돌려주지 않으면 mutation 은 재조회가 끝나기 전에 완료를
// 선언한다. 그래서 2단 확인 버튼이 「삭제」로 되돌아오고 팝업이 닫히고 목록으로
// 이동한 뒤에도 지운 것이 화면에 그대로 남고, 그동안 화면엔 아무 진행 표시도 없다
// (재조회가 느릴수록 창이 길어진다 — 실배포는 DB 조인 + 네트워크다).
//
// 프라미스를 돌려주면 mutation 이 재조회 착지까지 isPending 을 유지한다: "끝났다고
// 말한 시점 = 화면이 갱신된 시점". 낙관적 제거(캐시에서 행을 지우기)는 쓰지 않는다
// — 서버 재조회 결과만 화면의 진실이다(부분 실패·경합에서 거짓말하지 않는다).
const _refresh = (qc: QueryClient, id: string) =>
  Promise.all([qc.invalidateQueries({ queryKey: ["batch", id] }),
               qc.invalidateQueries({ queryKey: ["batches"] })]);

function _action<B = void>(id: string, verb: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: B) => apiSend("POST", `/api/admin/batches/${id}:${verb}`, body),
    onSettled: () => _refresh(qc, id),
  });
}
// 항목별 데이터 온도(요청 단위 scan 리포트 통계): 항목을 펼쳤고(enabled 로 호출측
// 이 전달) 성공 scan 요청일 때만 나간다 — 리포트가 없는 게 확실한 조회는 요청
// 자체를 만들지 않는 게 정직하다(lazy 계약). staleTime Infinity: 성공 종단 요청의
// 리포트는 불변 산출물이라 재펼침마다 재조회하는 건 공유 스토리지 I/O 낭비다.
export const useRequestScanStats = (requestId: string | null, enabled: boolean) =>
  useQuery({
    queryKey: ["request-scan-stats", requestId],
    queryFn: () =>
      apiGet<RequestScanStats>(`/api/admin/requests/${requestId}/scan-stats`),
    enabled: enabled && requestId !== null,
    staleTime: Infinity,
  });
// 메타데이터 수정(name/note): 서버가 빈 문자열을 NULL(지움)로 접는다 — 값은 늘
// 두 키를 실어 보내는 단순 계약(부분 갱신 판별을 화면이 흉내 내지 않는다).
export interface UpdateBatchBody { name?: string; note?: string }
export const useUpdateBatch = (id: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: UpdateBatchBody) =>
      apiSend<Batch>("PATCH", `/api/admin/batches/${id}`, b),
    onSettled: () => _refresh(qc, id),
  });
};
// 실행 설정 변경(종단 배치 한정 — 서버 409 batch_settings_locked 가 진짜 차단). 화면은 늘 다섯 키를 다 싣는다
// (지금 원하는 전체 상태): null = 정책 기본(priority/node_count/procs_per_node), options 는 통째 교체.
export interface BatchExecutionBody {
  max_concurrency: number; priority: string | null; node_count: number | null;
  procs_per_node: number | null; options: Record<string, unknown>;
  // sync·rm 만, 바꿀 때만 싣는다(서버: 켜면 이 관리자가 켠 사람으로 남고, 이미 켜져 있으면 처음 켠 사람 그대로).
  auto_confirm?: boolean;
  // rm 을 꺼짐 → 켜짐으로 바꿀 때만(동의 체크를 켰을 때) 싣는다 — 서버 422 batch_rm_auto_confirm_ack_required 의 짝.
  acknowledge_irreversible?: true;
}
export const useUpdateBatchExecution = (id: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: BatchExecutionBody) =>
      apiSend<Batch>("PATCH", `/api/admin/batches/${id}/execution`, b),
    onSettled: () => _refresh(qc, id),
  });
};
// 항목 편집 3종(수정·삭제·추가): 화면의 버튼 노출은 표시 게이트일 뿐이고 진짜
// 차단은 서버다(활성 배치 Queued 원자 가드 409·동질성 422). 갱신은 _action 과 같은
// 계약(_refresh — 상세+목록, 재조회 착지까지 대기) — 편집 결과·재활성화 상태를
// 폴링 전에 반영한다.
// R 을 열어 둔 이유(기본값 unknown = 기존 호출자 무변경): 선택 재실행만 응답
// **본문**이 결과다(부분 성공을 데이터로 돌려준다) — 화면이 타입 없이 읽으면
// requeued/skipped 가 any 로 새어 오탈자를 못 잡는다.
function _itemMutation<A, R = unknown>(id: string, fn: (a: A) => Promise<R>) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: fn, onSettled: () => _refresh(qc, id) });
}
export const useUpdateBatchItem = (id: string) =>
  _itemMutation(id, ({ seq, item }: { seq: number; item: Record<string, unknown> }) =>
    apiSend("PUT", `/api/admin/batches/${id}/items/${seq}`, item));
const _deleteItem = (id: string, seq: number) =>
  apiSend("DELETE", `/api/admin/batches/${id}/items/${seq}`);
export const useDeleteBatchItem = (id: string) =>
  _itemMutation(id, (seq: number) => _deleteItem(id, seq));
// 항목 다중 선택 삭제. 목록의 useDeleteBatches 와 같은 계약이다 — 요청 함수만
// 공유하고(URL·메서드 중복 없음), Promise.allSettled 로 **부분 실패를 데이터로**
// 돌려준다(isError 가 아니라 data.failed 가 실패의 자리). 한 건이 409
// (그 사이 materialize 된 항목)여도 나머지 삭제는 이미 일어났고 유효하다 — 첫
// 거절에서 throw 하면 화면이 몇 건이 지워졌는지 못 말한다. seq 는 재번호되지
// 않아(서버 계약) 구멍이 생기는 건 정상이고, 병렬 삭제도 서로를 밀지 않는다.
export interface BulkDeleteItemsResult {
  ok: number[]; failed: { seq: number; message: string }[];
}
export const useDeleteBatchItems = (id: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (seqs: number[]): Promise<BulkDeleteItemsResult> => {
      const settled = await Promise.allSettled(seqs.map((s) => _deleteItem(id, s)));
      const r: BulkDeleteItemsResult = { ok: [], failed: [] };
      settled.forEach((s, i) => {
        if (s.status === "fulfilled") r.ok.push(seqs[i]);
        // ApiError.message 는 이미 reasonText(사유 코드) 를 거친 문구다.
        else r.failed.push({ seq: seqs[i], message: s.reason instanceof ApiError
                             ? s.reason.message : String(s.reason) });
      });
      return r;
    },
    onSettled: () => _refresh(qc, id),
  });
};
// 항목 다중 선택 **재실행**. 삭제(useDeleteBatchItems)와 달리 Promise.allSettled
// 로 N 요청을 병렬로 쏘지 않는다: 서버가 컬렉션 엔드포인트 하나로 부분 성공을
// 계산해 준다(한 트랜잭션 + 카운터 절대값 재계산 — 클라이언트가 N 번 나눠 쏘면
// 그 사이 카운터가 N 번 흔들린다). 부분 실패를 **데이터로** 돌려준다는 계약은
// 같다 — isError 가 아니라 data.skipped 가 "못 한 것"의 자리다. 서버가 없는
// seq 도 skipped 로 돌려주므로 화면이 미리 걸러 조용히 성공한 척하지 않는다.
export interface RerunItemsResult {
  requeued: number; skipped: { seq: number; reason: string }[]; status: string;
}
export const useRerunBatchItems = (id: string) =>
  _itemMutation(id, (seqs: number[]) =>
    apiSend<RerunItemsResult>("POST", `/api/admin/batches/${id}/items:rerun`, { seqs }));
export const useAddBatchItem = (id: string) =>
  _itemMutation(id, (item: Record<string, unknown>) =>
    apiSend("POST", `/api/admin/batches/${id}/items`, item));
// 항목 전체 교체(CSV): 컬렉션 PUT — 종단 배치만(서버 batch_items_not_replaceable
// 가드). 교체는 실행이 아니다 — 배치는 종단 유지, 재실행은 :rescan 몫.
export const useReplaceBatchItems = (id: string) =>
  _itemMutation(id, (items: Record<string, unknown>[]) =>
    apiSend("PUT", `/api/admin/batches/${id}/items`, { items }));
// 배치 삭제(종단 배치만 — 서버 batch_not_deletable 가드). 성공 시 상세 쿼리는
// invalidate 가 아니라 **제거**한다: 삭제된 배치의 상세를 다시 조회하면 404 를
// 새로 받아 오류 화면이 되는데, 화면은 목록으로 떠난 뒤다(리페치가 소음).
//
// 목록은 이 경로만 invalidate 가 아니라 **refetch 를 기다린다**: 지우는 시점에
// 화면은 상세라 목록 쿼리가 **비활성**이고, invalidate 는 활성 쿼리만 재조회한다
// (비활성은 stale 표시만). 그대로 이동하면 목록은 캐시에 남은 지워진 배치를 먼저
// 그리고 — 데이터가 있으니 isLoading 은 false 라 로딩 표시조차 없다 — 마운트 후
// 재조회가 착지해야 사라진다. 훅 onSuccess 는 호출측 onSuccess(목록으로 이동)보다
// 먼저 await 되므로, 여기서 기다리면 이동한 목록은 이미 갱신된 목록이다.
//
// 배치 스케줄 목록(["batch-schedules"], 2026-11-16)도 무효화한다: 배치 삭제가 그 배치의 스케줄·회차 이력을 같은 트랜잭션에서
// 지운다(repositories/batches.delete) — 스케줄러 화면이 지운 배치의 스케줄을 「기록 없음」으로 남겨 보이지 않게.
const _deleteBatch = (id: string) => apiSend("DELETE", `/api/admin/batches/${id}`);
export const useDeleteBatch = (id: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => _deleteBatch(id),
    onSuccess: async () => { qc.removeQueries({ queryKey: ["batch", id] });
                             await Promise.all([qc.refetchQueries({ queryKey: ["batches"] }),
                                                qc.invalidateQueries({ queryKey: ["batch-schedules"] })]); },
    // 실패(409 = 그 사이 재실행돼 활성이 됐다)는 화면이 낡았다는 뜻이라 양쪽을
    // 다시 맞춘다 — 상세는 살아 있으므로 제거가 아니라 재조회다.
    onError: () => _refresh(qc, id),
  });
};
// 목록의 다중 선택 삭제. 단건 useDeleteBatch 를 재사용할 수 없는 이유는 훅이라서다
// (id 를 훅 인자로 받아 루프에 넣을 수 없다) — 그래서 **요청 함수 _deleteBatch 를
// 공유하는 래퍼**만 둔다(URL·메서드 중복 없음).
//
// Promise.allSettled 인 이유: 일괄 삭제의 부분 실패는 전체 실패가 아니다. 한 건이
// 409(그 사이 재실행돼 활성이 된 배치)여도 나머지 삭제는 이미 일어났고 유효하다 —
// 첫 거절에서 throw 하면 화면이 "실패"라고 거짓말하면서 몇 건이 지워졌는지 못
// 말한다. 그래서 mutation 은 늘 성공하고 성공/실패 내역을 **데이터로** 돌려준다
// (isError 가 아니라 data.failed 가 실패의 자리다).
export interface BulkDeleteResult { ok: string[]; failed: { id: string; message: string }[] }
export const useDeleteBatches = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (ids: string[]): Promise<BulkDeleteResult> => {
      const settled = await Promise.allSettled(ids.map(_deleteBatch));
      const r: BulkDeleteResult = { ok: [], failed: [] };
      settled.forEach((s, i) => {
        if (s.status === "fulfilled") r.ok.push(ids[i]);
        // ApiError.message 는 이미 reasonText(사유 코드) 를 거친 문구다.
        else r.failed.push({ id: ids[i], message: s.reason instanceof ApiError
                             ? s.reason.message : String(s.reason) });
      });
      return r;
    },
    // 지워진 배치의 상세 캐시만 제거(단건과 같은 이유 — 404 리페치 소음 방지).
    // 실패한 id 는 남긴다: 그 배치는 아직 살아 있다.
    onSuccess: (r) => { for (const id of r.ok) qc.removeQueries({ queryKey: ["batch", id] }); },
    // 목록 화면에서 쏘는 삭제라 목록 쿼리는 **활성**이다 — invalidate 로 재조회가
    // 돌고, 프라미스를 돌려줘 그 착지까지 기다린다(_refresh 와 같은 시점 계약):
    // 결과 문구("N개 삭제됨")가 뜨는 순간엔 표에서 그 행이 이미 사라져 있다.
    // 스케줄 목록은 단건 삭제와 같은 이유(지운 배치의 스케줄도 지워졌다)로 함께.
    onSettled: () => Promise.all([qc.invalidateQueries({ queryKey: ["batches"] }),
                                  qc.invalidateQueries({ queryKey: ["batch-schedules"] })]),
  });
};
// 확인은 대화상자를 연 회차(preview_round)를 싣는다 -- 서버가 다른 회차면 409 batch_preview_changed(ABA 방지).
// rm 은 「되돌릴 수 없음」 체크를 켰을 때만 acknowledge_irreversible: true 를 함께 싣는다(2026-10-21 -- 없으면 서버 422
// batch_rm_confirm_ack_required: 그 체크가 없는 옛 포탈 탭의 확인을 서버가 막는다). sync 는 싣지 않는다.
export const useConfirmBatch = (id: string) =>
  _action<{ preview_round: number; acknowledge_irreversible?: true }>(id, "confirm");
export const useRerunFailed = (id: string) => _action(id, "rerun-failed");
export const useCancelBatch = (id: string) => _action(id, "cancel");
// 자동 확인(sync·rm) 끄기(어느 상태에서나 -- 진행 중 브레이크). 켜기는 생성·실행 설정 변경(특권 게이트)뿐이다.
export const useDisableAutoConfirm = (id: string) => _action(id, "auto-confirm-off");
export const useRescanBatch = (id: string) => _action(id, "rescan");
