import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { ArtifactList, ArtifactFile, JobLogs } from "../../lib/types";

// refreshKey(요청 상세: `${job.state}|${phase_refs 키}`)가 바뀌면 목록을 다시 읽는다 -- 러너는 phase 가 끝날 때
// 파일을 쓰므로, 한 번 읽고 끝이면 진행 중에 연 상세는 실행이 끝나도 새 출력 파일을 영영 못 본다. 목록 자체를
// 폴링하지는 않는다(잡 상태 전이가 곧 갱신 신호다). keepPreviousData: 키가 바뀌는 순간 칩이 사라졌다 나타나지 않게.
export const useArtifacts = (jobId: string, refreshKey = "") =>
  useQuery({ queryKey: ["artifacts", jobId, refreshKey],
             queryFn: () => apiGet<ArtifactList>(`/api/user/jobs/${jobId}/artifacts`),
             placeholderData: keepPreviousData });

export const useArtifactFile = (jobId: string, phase: string, name: string, enabled: boolean) =>
  useQuery({
    queryKey: ["artifact", jobId, phase, name],
    queryFn: () => apiGet<ArtifactFile>(`/api/user/jobs/${jobId}/artifacts/${phase}/${name}`),
    enabled,
  });

// live: 진행 중 단계의 로그를 **열어 둔 동안만** 3초마다 다시 읽는다(k8s apiserver 라이브 조회라 열어 둔 사람마다
// 부하가 생긴다 -- 그래서 진행 중 로그는 자동으로 열지 않는다). 뷰어를 닫으면 옵저버가 사라져 멈추고, 단계가 끝나
// live 가 false 가 되면 interval 도 꺼진다. 백그라운드 탭은 TanStack 기본값대로 멈춘다.
export const useJobLogs = (jobId: string, phase: string, enabled: boolean, opts?: { live?: boolean }) =>
  useQuery({
    queryKey: ["joblogs", jobId, phase],
    queryFn: () => apiGet<JobLogs>(`/api/user/jobs/${jobId}/logs?phase=${phase}`),
    enabled,
    refetchInterval: opts?.live ? 3000 : false,
  });
