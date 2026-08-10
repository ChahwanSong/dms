import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { ArtifactBaseInfo } from "../../lib/types";

// (b)(c) 홉은 저장 "후" 수렴한다(에이전트 보고 60s·컨트롤러 30s 주기) -- 화면이
// 폴링으로 따라간다(설계 §2.4). 노드 화면과 같은 10s.
export const useArtifactBase = () =>
  useQuery({ queryKey: ["artifact-base"],
             queryFn: () => apiGet<ArtifactBaseInfo>("/api/admin/artifact-base"),
             refetchInterval: 10000 });

export interface ArtifactBaseBody { uri: string; force: boolean }

export const useValidateArtifactBase = () =>
  useMutation({ mutationFn: (b: { uri: string }) =>
    apiSend<{ normalized: string; ok: boolean }>(
      "POST", "/api/admin/artifact-base/validate", b) });

export const useSetArtifactBase = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (b: ArtifactBaseBody) =>
    apiSend<ArtifactBaseInfo>("PUT", "/api/admin/artifact-base", b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["artifact-base"] }) });
};
