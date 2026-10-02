import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { NodeExclusion, NodeInfo, NodeReport } from "../../lib/types";
export const useNodes = () =>
  useQuery({ queryKey: ["nodes"], queryFn: () => apiGet<NodeInfo[]>("/api/admin/nodes"),
             refetchInterval: 10000 });
export const useNodeReports = (name: string, enabled: boolean) =>
  useQuery({ queryKey: ["node-reports", name],
             queryFn: () => apiGet<NodeReport[]>(`/api/admin/nodes/${name}/reports`),
             enabled });
// 노드 배치 제외·다시 포함(2026-10-02, 서버 repositories/node_exclusions.py). 성공 뒤 노드 목록을 다시 읽는다 --
// 재조회가 끝나야 완료(배치 mutation 의 _refresh 관례: 화면이 갱신된 시점 = 끝났다고 말한 시점).
export const useExcludeNode = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, reason }: { name: string; reason: string }) =>
      apiSend<NodeExclusion>("PUT", `/api/admin/nodes/${encodeURIComponent(name)}/exclusion`,
                             { reason: reason.trim() === "" ? null : reason.trim() }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["nodes"] }),
  });
};
export const useIncludeNode = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      apiSend("DELETE", `/api/admin/nodes/${encodeURIComponent(name)}/exclusion`),
    onSettled: () => qc.invalidateQueries({ queryKey: ["nodes"] }),
  });
};
