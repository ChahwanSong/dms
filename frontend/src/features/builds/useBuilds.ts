import { useEffect, useRef } from "react";
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

export const useBuild = (id: string | null) => {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["builds", id],
    queryFn: () => apiGet<Build>(`/api/admin/builds/${id}`),
    enabled: id !== null,
    // 이 빌드 자체가 종단이면 더 이상 상태가 바뀌지 않는다.
    refetchInterval: (q) => {
      const build = q.state.data as Build | undefined;
      return build && !isTerminal(build.state) ? 3000 : false;
    },
  });

  // M4: 빌드가 종단으로 바뀌는 순간 useBuildLog의 active가 꺼져(isTerminal 기준)
  // 로그 폴링이 멈춘다 -- 그 직전에 파드가 남긴 마지막 몇 초 분량("=== pushed ===",
  // DMS_BUILD_OK)이 화면에 반영되기 전에 폴링이 끊기면 로그가 중간에 멈춘 것처럼
  // 보인 채 고정된다. 여기서 상태가 non-terminal -> terminal로 바뀌는 순간에만
  // ["build-log", id]를 한 번 무효화해 마지막 로그를 강제로 한 번 더 읽는다.
  const prev = useRef<{ id: string | null; wasTerminal: boolean }>({ id: null, wasTerminal: false });
  useEffect(() => {
    const state = q.data?.state;
    if (id === null || state === undefined) return;
    if (prev.current.id !== id) {
      // 다른 빌드로 전환(또는 최초 마운트) -- 이미 종단이어도 아직 이 빌드의
      // 로그를 한 번도 못 봤으니 여기선 무효화하지 않는다(다음 폴링이 담당).
      prev.current = { id, wasTerminal: isTerminal(state) };
      return;
    }
    const terminalNow = isTerminal(state);
    if (terminalNow && !prev.current.wasTerminal) {
      qc.invalidateQueries({ queryKey: ["build-log", id] });
    }
    prev.current.wasTerminal = terminalNow;
  }, [id, q.data?.state, qc]);

  return q;
};

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
