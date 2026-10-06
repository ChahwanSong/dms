import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { Policy } from "../../lib/types";
// enabled: 관리자 전용 API 라 비관리자 화면(단일 작업의 사용자 폼)에선 끈다 -- 켜 두면 사용자가 화면을
// 열 때마다 403 이 콘솔·서버 로그에 남는다(표시는 같다: 미조회 = "(정책 기본)").
export const usePolicies = (enabled = true) =>
  useQuery({ queryKey: ["policies"], queryFn: () => apiGet<Policy[]>("/api/admin/policies"), enabled });
export interface PolicyBody {
  max_nodes: number; procs_per_node: number; queue: string;
  default_priority: string; max_priority: string;
  preview_timeout_seconds: number | null; execution_timeout_seconds: number;
  enabled: boolean;
}
export const useUpsertPolicy = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (v: { tool: string; body: PolicyBody }) =>
    apiSend("PUT", `/api/admin/policies/${v.tool}`, v.body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["policies"] }) });
};
