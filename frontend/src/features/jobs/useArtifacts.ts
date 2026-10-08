import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { ArtifactList, ArtifactFile, JobLogs } from "../../lib/types";

// refreshKey(요청 상세: `${job.state}|${phase_refs 키}`)가 바뀌면 목록을 다시 읽는다 -- 러너는 phase 가 끝날 때
// 파일을 쓰므로, 한 번 읽고 끝이면 진행 중에 연 상세는 실행이 끝나도 새 출력 파일을 영영 못 본다. 목록 자체를
// 폴링하지는 않는다(잡 상태 전이가 곧 갱신 신호다).
//
// 쿼리 키는 잡 하나에 하나로 고정하고 refreshKey 가 바뀔 때 refetch 한다(2026-10-08 리뷰 V4). refreshKey 를 키에
// 넣으면 새 키의 첫 조회가 실패하는 순간 data 가 없어(keepPreviousData 는 성공한 새 데이터가 올 때까지만 버틴다)
// 보이던 파일 칩과 열어 둔 뷰어가 통째로 사라졌다. 같은 키의 재조회 실패는 마지막 성공 목록을 그대로 두고
// isError 만 켠다 -- 화면은 목록 옆에 「출력 파일 목록을 불러오지 못했습니다 — … 다시 시도」 줄을 더한다.
export const useArtifacts = (jobId: string, refreshKey = "") => {
  const q = useQuery({ queryKey: ["artifacts", jobId],
                       queryFn: () => apiGet<ArtifactList>(`/api/user/jobs/${jobId}/artifacts`) });
  const prev = useRef(refreshKey);
  const { refetch } = q;
  useEffect(() => {
    if (prev.current === refreshKey) return;   // 마운트는 건너뛴다(쿼리가 이미 한 번 읽는다)
    prev.current = refreshKey;
    void refetch();
  }, [refreshKey, refetch]);
  return q;
};

export const useArtifactFile = (jobId: string, phase: string, name: string, enabled: boolean) =>
  useQuery({
    queryKey: ["artifact", jobId, phase, name],
    queryFn: () => apiGet<ArtifactFile>(`/api/user/jobs/${jobId}/artifacts/${phase}/${name}`),
    enabled,
  });

// live: 진행 중 단계의 로그를 **열어 둔 동안만** 3초마다 다시 읽는다(k8s apiserver 라이브 조회라 열어 둔 사람마다
// 부하가 생긴다 -- 그래서 진행 중 로그는 자동으로 열지 않는다). 뷰어를 닫으면 옵저버가 사라져 멈추고, 단계가 끝나
// live 가 false 가 되면 interval 도 꺼진다(꺼지는 순간의 마지막 한 번은 OutputViewer 가 읽는다 -- 꼬리·박제 사본).
// 백그라운드 탭은 TanStack 기본값대로 멈춘다.
export const useJobLogs = (jobId: string, phase: string, enabled: boolean, opts?: { live?: boolean }) =>
  useQuery({
    queryKey: ["joblogs", jobId, phase],
    queryFn: () => apiGet<JobLogs>(`/api/user/jobs/${jobId}/logs?phase=${phase}`),
    enabled,
    refetchInterval: opts?.live ? 3000 : false,
  });
