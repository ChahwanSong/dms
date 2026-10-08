import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { ScanTargetRow, UsageExport, UsageHistory } from "../../lib/types";

// 타깃 필터(2026-10-02): storage = 스토리지 정확히 일치, path = 경로 부분 문자열 -- 둘이 함께 걸린다.
// order = 최근 스캔 정렬(서버가 limit 전에 정렬 -- 화면이 뒤집으면 "최근 N개 중 오래된 것"이 된다).
export interface TargetFilter { storage: string; path: string; order: "desc" | "asc" }

export function targetQuery(f: TargetFilter): string {
  const p = new URLSearchParams();
  if (f.storage) p.set("storage", f.storage);
  if (f.path) p.set("path", f.path);
  p.set("order", f.order);
  return p.toString();
}

// 타깃 목록: 필터가 쿼리키에 들어가 조합별로 캐시된다. 4s 폴링은 불필요 --
// scan 이 끝나야 목록이 변하는 저빈도 데이터라 staleTime 30s 로 왕복을 줄인다.
export const useScanTargets = (f: TargetFilter) =>
  useQuery({
    queryKey: ["usage-targets", f.storage, f.path, f.order],
    queryFn: () => apiGet<ScanTargetRow[]>(`/api/admin/usage/scan-targets?${targetQuery(f)}`),
    staleTime: 30_000,
  });

// 스토리지 필터 선택지 보강: 성공 scan 기록이 있는 스토리지 이름(등록이 지워진 이름 포함 -- 화면이 등록 목록과 합친다).
export const useScanStorages = () =>
  useQuery({ queryKey: ["usage-scan-storages"],
             queryFn: () => apiGet<string[]>("/api/admin/usage/scan-storages"), staleTime: 60_000 });

// 전체 내보내기(CSV 버튼): 화면 상태가 아니라 버튼을 누를 때 한 번 -- 필터·정렬은 목록과 같다.
export const fetchUsageExport = (f: TargetFilter) =>
  apiGet<UsageExport>(`/api/admin/usage/export?${targetQuery(f)}`);

// 이력: 선택된 타깃에서만 나간다(enabled). 아티팩트 읽기(포인트당 최대 256KiB
// I/O)가 뒤에 있으므로 staleTime 을 길게 -- 성공 종단 잡의 리포트는 **삭제되기 전까지** 불변이고,
// 새 스캔이 끝나 목록의 last_scan_at 이 변하면 사용자가 타깃을 다시 고르는
// 동선에서 자연히 재조회된다. 관리자가 작업(요청)을 지우면 그 지점이 사라지므로 useDeleteRequests 가
// usage-targets·usage-scan-storages·usage-history 를 무효화한다(2026-10-08). limit(표시 창)은 쿼리키에 들어가
// 창별로 캐시된다 -- 30→60→30 왕복이 재조회 없이 즉시다.
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
