import { useMutation, useQuery, useQueryClient,
         type QueryClient } from "@tanstack/react-query";
import { ApiError, apiGet, apiSend } from "../../lib/api";
import type { Batch, BatchDetail, RequestScanStats } from "../../lib/types";

// 서버 routes_batches._TERMINAL_BATCH 의 거울. 상세 폴링 중지와 목록의 "삭제 가능"
// 판정이 같은 한 벌을 쓴다 — 두 벌로 갈라지면 화면이 지우지 못할 배치를 지울 수
// 있는 척한다(진짜 차단은 서버 409 batch_not_deletable).
const BATCH_TERMINAL = new Set(["Completed", "Cancelled"]);
export const isBatchTerminal = (status: string) => BATCH_TERMINAL.has(status);
export const useBatches = () =>
  useQuery({ queryKey: ["batches"], queryFn: () => apiGet<Batch[]>("/api/admin/batches"),
            refetchInterval: 4000 });
export const useBatch = (id: string) =>
  useQuery({ queryKey: ["batch", id], queryFn: () => apiGet<BatchDetail>(`/api/admin/batches/${id}`),
    refetchInterval: (q) => (BATCH_TERMINAL.has((q.state.data as BatchDetail | undefined)?.status ?? "") ? false : 2500) });
export interface CreateBatchBody {
  operation: string; max_concurrency: number; options: Record<string, unknown>;
  note: string | null; items: Record<string, string>[];
  // 배치 이름: 빈값은 키 생략 = 서버 NULL(이름 없음) — priority 생략 계약의 미러.
  name?: string;
  // 실행 제어(슬라이스 32): 미지정은 키 생략 = 서버 NULL(정책 기본) — null≠0.
  priority?: string; node_count?: number;
  // 노드당 프로세스 수 override: node_count 와 같은 생략 계약의 미러.
  procs_per_node?: number;
  // 배치 특권 실행: 빈값은 키 생략 = 비특권 현행(단건 SubmitJob 관례 미러).
  owner_username?: string;
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

function _action(id: string, verb: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend("POST", `/api/admin/batches/${id}:${verb}`),
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
// 항목 편집 3종(수정·삭제·추가): 화면의 버튼 노출은 표시 게이트일 뿐이고 진짜
// 차단은 서버다(활성 배치 Queued 원자 가드 409·동질성 422). 갱신은 _action 과 같은
// 계약(_refresh — 상세+목록, 재조회 착지까지 대기) — 편집 결과·재활성화 상태를
// 폴링 전에 반영한다.
function _itemMutation<A>(id: string, fn: (a: A) => Promise<unknown>) {
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
const _deleteBatch = (id: string) => apiSend("DELETE", `/api/admin/batches/${id}`);
export const useDeleteBatch = (id: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => _deleteBatch(id),
    onSuccess: async () => { qc.removeQueries({ queryKey: ["batch", id] });
                             await qc.refetchQueries({ queryKey: ["batches"] }); },
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
    onSettled: () => qc.invalidateQueries({ queryKey: ["batches"] }),
  });
};
export const useConfirmBatch = (id: string) => _action(id, "confirm");
export const useRerunFailed = (id: string) => _action(id, "rerun-failed");
export const useCancelBatch = (id: string) => _action(id, "cancel");
export const useRescanBatch = (id: string) => _action(id, "rescan");
