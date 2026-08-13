import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { Batch, BatchDetail } from "../../lib/types";

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
export const useConfirmBatch = (id: string) => _action(id, "confirm");
export const useRerunFailed = (id: string) => _action(id, "rerun-failed");
export const useCancelBatch = (id: string) => _action(id, "cancel");
export const useRescanBatch = (id: string) => _action(id, "rescan");
