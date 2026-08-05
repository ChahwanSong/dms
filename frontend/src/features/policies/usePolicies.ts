import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { Policy } from "../../lib/types";
export const usePolicies = () =>
  useQuery({ queryKey: ["policies"], queryFn: () => apiGet<Policy[]>("/api/admin/policies") });
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
