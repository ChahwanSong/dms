import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { Batch, BatchDetail, RequestScanStats } from "../../lib/types";

const BATCH_TERMINAL = new Set(["Completed", "Cancelled"]);
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
function _action(id: string, verb: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend("POST", `/api/admin/batches/${id}:${verb}`),
    onSettled: () => { qc.invalidateQueries({ queryKey: ["batch", id] });
                       qc.invalidateQueries({ queryKey: ["batches"] }); },
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
    onSettled: () => { qc.invalidateQueries({ queryKey: ["batch", id] });
                       qc.invalidateQueries({ queryKey: ["batches"] }); },
  });
};
// 항목 편집 3종(수정·삭제·추가): 화면의 버튼 노출은 표시 게이트일 뿐이고 진짜
// 차단은 서버다(활성 배치 Queued 원자 가드 409·동질성 422). invalidate 는
// _action 과 같은 계약(상세+목록) — 편집 결과·재활성화 상태를 폴링 전에 반영한다.
function _itemMutation<A>(id: string, fn: (a: A) => Promise<unknown>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: () => { qc.invalidateQueries({ queryKey: ["batch", id] });
                       qc.invalidateQueries({ queryKey: ["batches"] }); },
  });
}
export const useUpdateBatchItem = (id: string) =>
  _itemMutation(id, ({ seq, item }: { seq: number; item: Record<string, unknown> }) =>
    apiSend("PUT", `/api/admin/batches/${id}/items/${seq}`, item));
export const useDeleteBatchItem = (id: string) =>
  _itemMutation(id, (seq: number) =>
    apiSend("DELETE", `/api/admin/batches/${id}/items/${seq}`));
export const useAddBatchItem = (id: string) =>
  _itemMutation(id, (item: Record<string, unknown>) =>
    apiSend("POST", `/api/admin/batches/${id}/items`, item));
export const useConfirmBatch = (id: string) => _action(id, "confirm");
export const useRerunFailed = (id: string) => _action(id, "rerun-failed");
export const useCancelBatch = (id: string) => _action(id, "cancel");
export const useRescanBatch = (id: string) => _action(id, "rescan");
