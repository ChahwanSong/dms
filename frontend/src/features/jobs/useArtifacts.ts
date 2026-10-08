import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
//
// 다시 읽기 전에 진행 중인 조회를 취소한다(리뷰 N0): TanStack 5 의 refetch(cancelRefetch)는 **데이터가 이미 있을 때만**
// 진행 중 조회를 끊고 새로 읽는다. 데이터가 없으면(마운트 첫 조회·실패 뒤 「다시 시도」가 아직 진행 중) 그 조회에
// 합류해, 전이 전에 떠난 요청의 낡은 목록이 마지막 목록이 된다(종단 잡은 다음 전이가 없어 영영 갱신되지 않는다).
// 취소 **바로 뒤에 같은 틱에서** 다시 읽는다(리뷰 3차): 취소의 되돌림(revert)은 동기라 데이터 없는 쿼리는 그 순간
// pending/idle(isLoading false)이 된다 -- `.then(refetch)` 로 마이크로태스크를 기다리면 그 사이에 커밋된 렌더(같은 flush
// 의 다른 effect setState, act·DefaultLane 갱신)가 「출력 목록을 불러오는 중…」 골격을 한 번 떨어뜨리고 끝난 단계에
// 「출력 없음」을 비친다. 동기로 부르면 query.fetch 가 idle 을 보고 곧바로 새 조회를 시작한다(취소된 옛 조회의 응답은
// 옛 retryer 가 이미 거절돼 버려진다).
export const useArtifacts = (jobId: string, refreshKey = "") => {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["artifacts", jobId],
                       queryFn: () => apiGet<ArtifactList>(`/api/user/jobs/${jobId}/artifacts`) });
  const prev = useRef(refreshKey);
  const { refetch } = q;
  useEffect(() => {
    if (prev.current === refreshKey) return;   // 마운트는 건너뛴다(쿼리가 이미 한 번 읽는다)
    prev.current = refreshKey;
    void qc.cancelQueries({ queryKey: ["artifacts", jobId], exact: true });
    void refetch();
  }, [refreshKey, refetch, qc, jobId]);
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
// 백그라운드 탭은 TanStack 기본값대로 멈춘다. 쿼리 키(jobLogsKey)는 OutputViewer 의 마지막 읽기가 진행 중 조회를
// 취소할 때 같은 값을 쓴다.
export const jobLogsKey = (jobId: string, phase: string) => ["joblogs", jobId, phase] as const;
export const useJobLogs = (jobId: string, phase: string, enabled: boolean, opts?: { live?: boolean }) =>
  useQuery({
    queryKey: jobLogsKey(jobId, phase),
    queryFn: () => apiGet<JobLogs>(`/api/user/jobs/${jobId}/logs?phase=${phase}`),
    enabled,
    refetchInterval: opts?.live ? 3000 : false,
  });
