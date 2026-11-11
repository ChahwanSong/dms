import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { apiGet, ApiError } from "../../lib/api";
import type { InfraMetrics, JobMetrics, NodeMetrics, QueueMetrics } from "../../lib/types";

// 쿼리 키는 ["metrics", ...]로 묶는다 -- 기존 ["nodes"]는 dashboard/useDashboard.ts와
// nodes/useNodes.ts가 /api/admin/nodes 캐시로 공유 중이라 절대 겹치면 안 된다.

// 대시보드 카드의 조회 실패 한 줄(2026-11-11). queryClient 가 retry:false 라 첫 응답 하나가 실패하면 data 없이 착지한다 --
// 예전엔 카드마다 그 상태를 「0건·빈 목록」으로 그리거나(KPI·잡 통계) 엉뚱한 원인(큐 카드의 「권한(RBAC)」)을 가리켰다.
// 이미 받은 data 가 있으면 react-query 가 그 값을 유지하므로 카드는 `isError && !data` 일 때만 이 문구를 낸다.
// what 은 조사까지 붙인 목적어(「큐 현황을」) -- 「을(를)」 같은 겹조사를 화면에 남기지 않는다.
export const loadErrorText = (what: string, e: unknown): string =>
  `${what} 불러오지 못했습니다 — ${e instanceof ApiError ? e.message : "잠시 뒤 다시 시도하세요"}`;

export const useNodeMetrics = (windowH: number) =>
  useQuery({
    queryKey: ["metrics", "nodes", windowH],
    // 시계열은 기간 재조회 위주(설계 §4) -- 짧은 폴링을 걸지 않는다
    queryFn: () => apiGet<NodeMetrics>(`/api/admin/metrics/nodes?window=${windowH}`),
  });

export const useJobMetrics = (windowH: number) =>
  useQuery({
    queryKey: ["metrics", "jobs", windowH],
    queryFn: () => apiGet<JobMetrics>(`/api/admin/metrics/jobs?window=${windowH}`),
    refetchInterval: 5000,   // 개요 KPI가 이 쿼리를 그대로 쓴다(설계 §4: 개요만 짧게)
    // 기간을 바꾸면 새 키라 응답 전까지 data 가 비어 잡 통계가 「집계 0건」으로 깜빡였다 -- 응답이 올 때까지 앞 기간 값을
    // 둔다(2026-11-11). 그동안 isPlaceholderData 가 참이라 화면은 「불러오는 중…」을 함께 내고 창 클램프 판정을 미룬다.
    placeholderData: keepPreviousData,
  });

export const useInfraMetrics = () =>
  useQuery({
    queryKey: ["metrics", "infra"],
    queryFn: () => apiGet<InfraMetrics>("/api/admin/metrics/infra"),
    refetchInterval: 5000,
  });

export const useQueueMetrics = () =>
  useQuery({
    queryKey: ["metrics", "queue"],
    queryFn: () => apiGet<QueueMetrics>("/api/admin/metrics/queue"),
    refetchInterval: 5000,   // infra 와 같은 개요 폴링 -- PodGroup 은 잡과 함께
                             // 사라지므로(설계 §1-1) 짧게 봐야 대기가 보인다
  });
