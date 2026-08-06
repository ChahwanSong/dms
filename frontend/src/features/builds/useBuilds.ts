import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { Build } from "../../lib/types";

export const useBuilds = () =>
  useQuery({
    queryKey: ["builds"],
    queryFn: () => apiGet<Build[]>("/api/admin/builds"),
    // 진행 중인 빌드는 몇 분씩 걸린다 — 주기적으로 다시 읽어 상태를 따라간다.
    refetchInterval: 5000,
  });

export const useBuild = (id: string | null) =>
  useQuery({
    queryKey: ["builds", id],
    queryFn: () => apiGet<Build>(`/api/admin/builds/${id}`),
    enabled: id !== null,
  });

export interface SubmitBuildBody { git_ref: string; images: string[]; }

export const useSubmitBuild = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: SubmitBuildBody) => apiSend("POST", "/api/admin/builds", b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["builds"] }),
  });
};
