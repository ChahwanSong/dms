import { useInfiniteQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { AuditEntry } from "../../lib/types";

// 감사 로그 무한 스크롤(2026-09-30 사용자 요청: "수십 개만 보인다 -- 스크롤하면 계속 더"). 한 쪽
// AUDIT_PAGE_SIZE 건, 다음 쪽은 마지막 행의 id 를 before 로 넘긴다(서버 키셋 커서 -- 보는 동안 새
// 기록이 위에 쌓여도 쪽 경계가 밀리지 않는다). 전체 작업 화면(useInfiniteRequests)과 같은 모양이다.
export const AUDIT_PAGE_SIZE = 50;

export function auditUrl(before?: number): string {
  const p = new URLSearchParams({ limit: String(AUDIT_PAGE_SIZE) });
  if (before !== undefined) p.set("before", String(before));
  return `/api/admin/audit-log?${p.toString()}`;
}

export const useInfiniteAuditLog = () =>
  useInfiniteQuery({
    queryKey: ["audit", "infinite"],
    queryFn: ({ pageParam }) => apiGet<AuditEntry[]>(auditUrl(pageParam)),
    initialPageParam: undefined as number | undefined,
    // 마지막 쪽이 꽉 찼을 때만 다음 커서가 있다 -- 덜 찼으면 끝(undefined).
    getNextPageParam: (lastPage) =>
      lastPage.length === AUDIT_PAGE_SIZE ? lastPage[lastPage.length - 1].id : undefined,
  });
