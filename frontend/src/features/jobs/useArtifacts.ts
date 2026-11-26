import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { ArtifactList, ArtifactFile, JobLogs, JobProgressResponse } from "../../lib/types";

// refreshKey(요청 상세: `${job.state}|${phase_refs 키}`)가 바뀌면 목록을 다시 읽는다 -- 한 번 읽고 끝이면 진행 중에 연
// 상세는 실행이 끝나도 새 출력 파일(summary.json 등)을 영영 못 본다. 러너는 2026-11-26 부터 mpirun 출력을 도는 동안 이어 쓰므로
// (stdout.log 가 실행 중에 자란다) 목록의 크기는 다음 전이까지 그 순간 값에 멈춰 있다 -- 실행 중 진행은 진행 칸(useJobProgress 의
// progress.json 폴링)의 몫이고, 목록 자체를 주기적으로 폴링하지는 않는다(열어 둔 사람마다 디렉터리 읽기 부하 -- 잡 상태 전이가 곧
// 갱신 신호다).
//
// 종단 전이를 본 뒤에는 ARTIFACTS_SETTLE_MS 뒤에 몇 번 더 읽는다(2026-11-27 d49 실증 C1): 취소·시간초과는 vcjob 을 지워 Cancelled
// 전이가 러너의 유예 쓰기(stderr.log 꼬리·summary.json·progress.json ended)와 같은 순간이거나 그보다 먼저 오고, 공유 base 가 NFS 면
// 다른 노드(API)에서 새 파일·크기가 보이기까지 수 초~약 30초가 걸렸다(stdout.log 가시 지연 최대 약 30초 실측) -- 전이 때 한 번만
// 읽으면 칩이 낡은 채 남았다(stderr.log 78 B·summary 없음, 새로고침하면 672 B·summary 160 B). 종단은 다음 전이가 없으므로 이 몇 번이
// 마지막 갱신이다. 마운트(이미 종단인 잡을 연 경우)는 전이가 아니라 예약하지 않고, 다음 전이·언마운트 때 남은 예약을 지운다.
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
export const ARTIFACTS_SETTLE_MS: readonly number[] = [3000, 10000, 30000];
export const useArtifacts = (jobId: string, refreshKey = "", terminal = false) => {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["artifacts", jobId],
                       queryFn: () => apiGet<ArtifactList>(`/api/user/jobs/${jobId}/artifacts`) });
  const prev = useRef(refreshKey);
  // 종단 뒤 다시 읽기 예약 -- effect 정리 함수가 아니라 ref 로 쥔다: 의존성(refetch 등)만 바뀐 재실행이 예약을 지우고 아래 조기 반환으로
  // 다시 걸지 못하는 일이 없게. 지우는 곳은 다음 전이와 언마운트 둘뿐이다.
  const settle = useRef<ReturnType<typeof setTimeout>[]>([]);
  const { refetch } = q;
  useEffect(() => () => {
    settle.current.forEach(clearTimeout);
    settle.current = [];
  }, []);
  useEffect(() => {
    if (prev.current === refreshKey) return;   // 마운트는 건너뛴다(쿼리가 이미 한 번 읽는다)
    prev.current = refreshKey;
    settle.current.forEach(clearTimeout);
    const reread = () => {
      void qc.cancelQueries({ queryKey: ["artifacts", jobId], exact: true });
      void refetch();
    };
    reread();
    settle.current = terminal ? ARTIFACTS_SETTLE_MS.map((ms) => setTimeout(reread, ms)) : [];
  }, [refreshKey, terminal, refetch, qc, jobId]);
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

// sync 진행률(2026-11-26, 계약 lib/progressContract.json): 요청 상세 잡 카드의 「진행 중」 실행·미리보기 행(JobProgress)만 부른다
// -- 잡 목록·배치 화면은 부르지 않는다(사용자 결정: 각 작업 항목에 들어갔을 때만). live(그 행이 진행 중)인 동안만 5초마다
// 읽고, available:false(아직 없음·이전 잡 이미지)나 오류가 **연속 PROGRESS_SLOW_AFTER_MISSES(3)회**가 되면 15초로 늘린다 -- 그 경로는
// 서버가 매번 base 점검(불변식 16 -- 최근 잡 DB 조회 + fstatat 몇 번)을 돌리므로 성공 경로보다 비싸다. 첫 한 번에 바로 늘리던 때
// (2026-11-27 d49 실증)는 2~10초짜리 미리보기가 첫 응답(아직 파일 없음) 뒤 15초를 기다리는 사이에 끝나 진행 칸이 끝까지 「진행
// 정보를 기다리는 중…」이었다 -- 연속 3회 문턱의 추가 비용은 첫 15초 동안 점검 2회다. 성공(available:true) 한 번이면 다시 센다.
// 숨긴 탭은 TanStack 기본값(refetchIntervalInBackground false)대로 멈추고, 다시 보이면 refetchOnWindowFocus 가 곧바로 한 번 읽는다.
// 간격 값은 계약 poll_ms 와 시험이 잇는다(런타임 번들에 계약 파일을 싣지 않는다 -- reasonCodes.json 과 같은 방식). staleTime 0: 행이
// 다시 진행 중이 되어 다시 마운트되면 곧바로 읽는다.
export const PROGRESS_POLL_MS = 5000;
export const PROGRESS_SLOW_POLL_MS = 15000;
export const PROGRESS_SLOW_AFTER_MISSES = 3;
/** 연속 헛걸음(available:false·오류) 수 → 다음 폴링 간격. */
export const progressPollMs = (misses: number): number =>
  misses >= PROGRESS_SLOW_AFTER_MISSES ? PROGRESS_SLOW_POLL_MS : PROGRESS_POLL_MS;
export type ProgressPhase = "preview" | "execution";
export const jobProgressKey = (jobId: string, phase: ProgressPhase) => ["jobprogress", jobId, phase] as const;
export const useJobProgress = (jobId: string, phase: ProgressPhase, live: boolean) => {
  // 연속 헛걸음 수 -- 쿼리 상태에는 누적 횟수(errorUpdateCount 등)뿐이라 「연속」을 세지 못해 queryFn 이 응답마다 센다. queryFn 이 끝난
  // 뒤에 TanStack 이 refetchInterval 을 다시 계산하므로 그 계산은 늘 방금 응답까지 센 값을 본다. 행(JobProgress)마다 하나다.
  const misses = useRef(0);
  return useQuery({
    queryKey: jobProgressKey(jobId, phase),
    queryFn: async () => {
      try {
        const r = await apiGet<JobProgressResponse>(`/api/user/jobs/${jobId}/progress?phase=${phase}`);
        misses.current = r.available === false ? misses.current + 1 : 0;
        return r;
      } catch (e) {
        misses.current += 1;
        throw e;
      }
    },
    enabled: live,
    refetchInterval: () => (live ? progressPollMs(misses.current) : false),
    staleTime: 0,
  });
};
