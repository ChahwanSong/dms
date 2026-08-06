import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import { isTerminal } from "../../lib/jobState";
import type { Build } from "../../lib/types";

// 빌드 상태는 Pending/Running/Succeeded/Failed 뿐이라 jobState.ts의 종단 집합
// (Succeeded/Failed/...)과 그대로 맞아떨어진다 — useJobs.ts의 useRequestJobs와
// 같은 관용구를 재사용한다.
//
// 백엔드는 한 번에 하나의 빌드만 진행되게 막는다(build_in_progress 가드) — 목록에
// 진행 중인 항목이 하나도 없으면 상태가 더 바뀔 일이 없어 폴링을 멈춘다. 새 빌드를
// 제출하면 useSubmitBuild가 이 쿼리를 무효화해 즉시 다시 읽고, 그 결과에 진행 중인
// 항목이 생기면 폴링이 자동으로 재개된다.
export const useBuilds = () =>
  useQuery({
    queryKey: ["builds"],
    queryFn: () => apiGet<Build[]>("/api/admin/builds"),
    refetchInterval: (q) => {
      const builds = q.state.data as Build[] | undefined;
      return Array.isArray(builds) && builds.some((b) => !isTerminal(b.state)) ? 5000 : false;
    },
  });

export const useBuild = (id: string | null) =>
  useQuery({
    queryKey: ["builds", id],
    queryFn: () => apiGet<Build>(`/api/admin/builds/${id}`),
    enabled: id !== null,
    // 이 빌드 자체가 종단이면 더 이상 상태가 바뀌지 않는다.
    refetchInterval: (q) => {
      const build = q.state.data as Build | undefined;
      return build && !isTerminal(build.state) ? 3000 : false;
    },
  });

export interface BuildLog { build_id: string; log: string | null; }

// active는 호출자가 빌드의 state(Pending/Running)로 판단해 넘긴다. 종단 빌드는
// 로그가 더 바뀌지 않으므로(서버가 저장된 log_text로 답한다) 폴링을 멈춘다.
export const useBuildLog = (id: string, active: boolean) =>
  useQuery({
    queryKey: ["build-log", id],
    queryFn: () => apiGet<BuildLog>(`/api/admin/builds/${id}/log`),
    refetchInterval: active ? 3000 : false,
  });

export interface SubmitBuildBody { git_ref: string; images: string[]; }

export const useSubmitBuild = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: SubmitBuildBody) => apiSend("POST", "/api/admin/builds", b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["builds"] }),
  });
};
