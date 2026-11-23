import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { ArtifactBaseHealth, ArtifactBaseInfo } from "../../lib/types";

// (b)(c) 홉은 저장 "후" 수렴한다(에이전트 보고 60s·컨트롤러 30s 주기) -- 화면이
// 폴링으로 따라간다(설계 §2.4). 노드 화면과 같은 10s.
export const useArtifactBase = () =>
  useQuery({ queryKey: ["artifact-base"],
             queryFn: () => apiGet<ArtifactBaseInfo>("/api/admin/artifact-base"),
             refetchInterval: 10000 });

// 변경 이력(슬라이스 38): 감사 로그의 artifact_base before/after. before 는 변경
// 전 control_state 행(uri 가 null 이면 당시 env 유효), after 는 새 uri + 강제
// 여부·영향 잡 수. 문구는 화면(histText)이 계산한다 -- control-history 와 같은 계약.
export interface ArtifactBaseHistoryEntry {
  at: string; actor: string | null;
  before: { artifact_base_uri?: string | null } | null;
  after: { artifact_base_uri?: string; forced?: boolean; affected_jobs?: number } | null;
}
export const useArtifactBaseHistory = () =>
  useQuery({ queryKey: ["artifact-base-history"],
    queryFn: () => apiGet<ArtifactBaseHistoryEntry[]>("/api/admin/artifact-base/history") });

export interface ArtifactBaseBody { uri: string; force: boolean }

export const useValidateArtifactBase = () =>
  useMutation({ mutationFn: (b: { uri: string }) =>
    apiSend<{ normalized: string; ok: boolean }>(
      "POST", "/api/admin/artifact-base/validate", b) });

// 「지금 다시 점검」(2026-11-23): 이 API 파드가 즉시 한 회차를 돌고(서버 ≤12초 대기) GET 과 같은 모양을 돌려준다 -- 응답을
// 그대로 캐시에 넣어 10초 폴링을 기다리지 않는다. 설정 변경이 아니라 관리자 세션 게이트가 아니다(서버 require_admin).
export const useRecheckArtifactBase = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: () => apiSend<ArtifactBaseInfo>("POST", "/api/admin/artifact-base:recheck"),
    onSuccess: (data) => { qc.setQueryData(["artifact-base"], data); } });
};

// API 파드의 메모리 스냅숏(가벼움 -- FS·DB 를 건드리지 않는다). 사용량 화면이 base 접근 불가 사유를 들고 있는 동안만
// 켠다(useUsage.useUsageBaseRecovery) -- 15초.
export const useArtifactBaseHealth = (enabled: boolean) =>
  useQuery({ queryKey: ["artifact-base-health"],
             queryFn: () => apiGet<ArtifactBaseHealth>("/api/admin/artifact-base/health"),
             enabled, refetchInterval: enabled ? 15_000 : false, staleTime: 0 });

export const useSetArtifactBase = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (b: ArtifactBaseBody) =>
    apiSend<ArtifactBaseInfo>("PUT", "/api/admin/artifact-base", b),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["artifact-base"] });
      qc.invalidateQueries({ queryKey: ["artifact-base-history"] });
    } });
};
