import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { ForgetNodeResult, NodeExclusion, NodeInfo, NodeReport } from "../../lib/types";
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
// 노드 기록 삭제(2026-11-25, DELETE /api/admin/nodes/{name} -- 서버 node_forget·AgentsRepository.forget_node): 리포트 지연 노드의
// 옛 행(최신 보고·보고 이력·배치 제외)을 지운다. 세션 관리자만(공유 토큰은 403 admin_session_required). 노드 행을 읽는 다른 화면
// (마운트 현황 ["mounts"]·대시보드 노드 지표·아티팩트 경로의 노드 점검)도 다시 읽게 표시하고, 완료는 ["nodes"] 재조회가 끝난
// 시점이다(위 관례). 지운 노드의 보고 이력 캐시는 버린다 -- 같은 이름이 다시 보고하면 새 행이라 옛 이력을 보여 줄 이유가 없다.
export const useForgetNode = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      apiSend<ForgetNodeResult>("DELETE", `/api/admin/nodes/${encodeURIComponent(name)}`),
    onSuccess: (_r, name) => { qc.removeQueries({ queryKey: ["node-reports", name] }); },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["mounts"] });
      void qc.invalidateQueries({ queryKey: ["metrics", "nodes"] });
      void qc.invalidateQueries({ queryKey: ["artifact-base"] });
      return qc.invalidateQueries({ queryKey: ["nodes"] });
    },
  });
};
