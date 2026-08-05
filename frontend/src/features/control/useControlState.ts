import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { ControlState } from "../../lib/types";
export const useControlState = () =>
  useQuery({ queryKey: ["control-state"], queryFn: () => apiGet<ControlState>("/api/admin/control-state") });
export interface ControlStateBody { maintenance: boolean; drain: boolean; reason: string | null; }
export const useSetControlState = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (b: ControlStateBody) =>
    apiSend("PUT", "/api/admin/control-state", b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["control-state"] }) });
};
