import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { DenyEntry } from "../../lib/types";
export const useDenylist = () =>
  useQuery({ queryKey: ["denylist"], queryFn: () => apiGet<DenyEntry[]>("/api/admin/identity-denylist") });
export const useDeny = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (v: { subject_type: string; subject: string; reason: string | null }) =>
    apiSend("PUT", `/api/admin/identity-denylist/${v.subject_type}/${v.subject}`, { reason: v.reason }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["denylist"] }) });
};
export const useAllow = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (v: { subject_type: string; subject: string }) =>
    apiSend("DELETE", `/api/admin/identity-denylist/${v.subject_type}/${v.subject}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["denylist"] }) });
};
