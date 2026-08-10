import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { InfraMetrics, JobMetrics, NodeMetrics, QueueMetrics } from "../../lib/types";

// 쿼리 키는 ["metrics", ...]로 묶는다 -- 기존 ["nodes"]는 dashboard/useDashboard.ts와
// nodes/useNodes.ts가 /api/admin/nodes 캐시로 공유 중이라 절대 겹치면 안 된다.

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
