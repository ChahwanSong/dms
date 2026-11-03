import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { SecurityEvent, SecurityStatus, VerificationLocks } from "../../lib/types";

// 관리 → 보안 이벤트(2026-11-03, 서버 routes_security). 상태·잠금 목록은 15초마다 다시 읽는다 -- 공격 중에 운영자가 화면을 열어
// 둔 채 카운터가 움직이는지 본다. 이벤트 목록은 감사 로그와 같은 키셋 무한 스크롤(before = 마지막 행 id)이고 자동 갱신은 하지
// 않는다(읽는 동안 행이 밀리지 않게 -- 「새로고침」 버튼과 해제 동작 뒤 무효화로만).
export const SECURITY_POLL_MS = 15_000;
export const SECURITY_EVENTS_PAGE_SIZE = 50;

export type VerificationPurpose = "signup" | "password_reset";
export interface SecurityEventFilter { type?: string; severity?: string; q?: string }

/** 빈 필터 값은 뺀 정규형 -- 쿼리 키와 쿼리 문자열이 「빈 문자열 필터」로 갈라지지 않게. */
export function normalizeFilter(f: SecurityEventFilter): SecurityEventFilter {
  const out: SecurityEventFilter = {};
  if (f.type) out.type = f.type;
  if (f.severity) out.severity = f.severity;
  const q = f.q?.trim();
  if (q) out.q = q;
  return out;
}

export function securityEventsUrl(f: SecurityEventFilter, before?: number): string {
  const n = normalizeFilter(f);
  const p = new URLSearchParams({ limit: String(SECURITY_EVENTS_PAGE_SIZE) });
  if (n.type) p.set("type", n.type);
  if (n.severity) p.set("severity", n.severity);
  if (n.q) p.set("q", n.q);
  if (before !== undefined) p.set("before", String(before));
  return `/api/admin/security/events?${p.toString()}`;
}

export const useSecurityStatus = () =>
  useQuery({ queryKey: ["security", "status"], refetchInterval: SECURITY_POLL_MS,
             queryFn: () => apiGet<SecurityStatus>("/api/admin/security/status") });

export const useVerificationLocks = () =>
  useQuery({ queryKey: ["security", "locks"], refetchInterval: SECURITY_POLL_MS,
             queryFn: () => apiGet<VerificationLocks>("/api/admin/security/verification-locks") });

export function useInfiniteSecurityEvents(f: SecurityEventFilter) {
  const filter = normalizeFilter(f);
  return useInfiniteQuery({
    queryKey: ["security", "events", filter],
    queryFn: ({ pageParam }) => apiGet<SecurityEvent[]>(securityEventsUrl(filter, pageParam)),
    initialPageParam: undefined as number | undefined,
    // 마지막 쪽이 꽉 찼을 때만 다음 커서가 있다(감사 로그와 같은 규칙).
    getNextPageParam: (lastPage) =>
      lastPage.length === SECURITY_EVENTS_PAGE_SIZE ? lastPage[lastPage.length - 1].id : undefined,
  });
}

// 해제 둘 다 세션 관리자만(서버 403 accounts_session_required) -- 토큰 관리자는 화면을 볼 수 있지만 누르면 그 사유가 보인다.
// 성공하면 보안 화면 전체(상태·잠금·이벤트)를 다시 읽는다 -- 해제 자체가 이벤트(verification_unlocked 등)를 남긴다.
export interface UnlockResult { username: string; purpose: VerificationPurpose | null; cleared: number }
export const useUnlockVerification = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { username: string; purpose: VerificationPurpose | null }) =>
      apiSend<UnlockResult>("POST", "/api/admin/security/verification-unlock", v),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["security"] }),
  });
};

export const useGlobalUnlock = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend<{ cleared_failures: number }>("POST", "/api/admin/security/verification-global-unlock", {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["security"] }),
  });
};
