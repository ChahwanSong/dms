import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { ScanTargetRow, UsageHistory } from "../../lib/types";

// 타깃 목록: 검색어가 쿼리키에 들어가 검색어별로 캐시된다. 4s 폴링은 불필요 --
// scan 이 끝나야 목록이 변하는 저빈도 데이터라 staleTime 30s 로 왕복을 줄인다.
export const useScanTargets = (q: string) =>
  useQuery({
    queryKey: ["usage-targets", q],
    queryFn: () => apiGet<ScanTargetRow[]>(
      `/api/admin/usage/scan-targets${q ? `?q=${encodeURIComponent(q)}` : ""}`),
    staleTime: 30_000,
  });

// 이력: 선택된 타깃에서만 나간다(enabled). 아티팩트 읽기(포인트당 최대 256KiB
// I/O)가 뒤에 있으므로 staleTime 을 길게 -- 성공 종단 잡의 리포트는 불변이고,
// 새 스캔이 끝나 목록의 last_scan_at 이 변하면 사용자가 타깃을 다시 고르는
// 동선에서 자연히 재조회된다. limit(표시 창)은 쿼리키에 들어가 창별로 캐시된다
// -- 30→60→30 왕복이 재조회 없이 즉시다.
export const useScanHistory = (storage: string | null, target: string | null,
                               limit = 30) =>
  useQuery({
    queryKey: ["usage-history", storage, target, limit],
    queryFn: () => apiGet<UsageHistory>(
      `/api/admin/usage/scan-history?storage=${encodeURIComponent(storage as string)}`
      + `&target=${encodeURIComponent(target as string)}&limit=${limit}`),
    enabled: storage !== null && target !== null,
    staleTime: 60_000,
  });
