import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { MountStatusResponse } from "./types";

// 마운트 현황(2026-11-23). 자기 쿼리 키 -- ["nodes"] 는 대시보드(Node[], 5초)와 노드 화면(NodeInfo[], 10초)이 같이 쓰는 캐시라
// 모양이 다른 이 응답을 거기 넣지 않는다. 10초 폴링(노드 화면과 같은 주기 -- 에이전트 보고는 약 60초마다라 더 잦을 이유가 없다).
export const MOUNT_STATUS_KEY = ["mount-status"] as const;

export const useMountStatus = () =>
  useQuery({
    queryKey: MOUNT_STATUS_KEY,
    queryFn: () => apiGet<MountStatusResponse>("/api/admin/mount-status"),
    refetchInterval: 10_000,
  });
