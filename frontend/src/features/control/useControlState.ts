import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { ControlState } from "../../lib/types";
export const useControlState = () =>
  useQuery({ queryKey: ["control-state"], queryFn: () => apiGet<ControlState>("/api/admin/control-state") });
export interface ControlStateBody {
  maintenance: boolean; drain: boolean; reason: string | null;
  build_node_name: string | null;
  build_source_path: string | null;
}
export const useSetControlState = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (b: ControlStateBody) =>
    apiSend("PUT", "/api/admin/control-state", b),
    // 이력도 함께 무효화 -- 저장 직후 방금 변경이 이력 표 맨 위에 서야 한다.
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["control-state"] });
                       qc.invalidateQueries({ queryKey: ["control-history"] }); } });
};

// 변경 이력(슬라이스 36): 감사 로그의 control_state before/after 스냅샷.
// diff 문구는 화면(ControlStatePage.diffText)이 계산한다.
export interface ControlHistoryEntry {
  at: string; actor: string | null;
  before: Partial<ControlState> | null; after: Partial<ControlState> | null;
}
export const useControlHistory = () =>
  useQuery({ queryKey: ["control-history"],
    queryFn: () => apiGet<ControlHistoryEntry[]>("/api/admin/control-state/history") });
