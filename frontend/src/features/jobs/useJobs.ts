import { useQuery, useMutation, useQueryClient, useInfiniteQuery } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import { isTerminal } from "../../lib/jobState";
import type { RequestRow, RequestDetail, DataJob } from "../../lib/types";

export const useRequests = () =>
  useQuery({ queryKey: ["requests"], queryFn: () => apiGet<RequestRow[]>("/api/user/requests"),
            refetchInterval: 3000 });

// 전체 작업 화면(슬라이스 39): 커서 무한 스크롤 + 필터. 한 쪽 PAGE_SIZE 건,
// 다음 쪽은 마지막 행의 commit_order 를 before 로 넘긴다. refetchInterval 3s 는
// 구 useRequests 의 목록 폴링 계약을 잇는다(e2e E5) -- 첫 쪽이 재조회되어 새
// 제출이 위에 나타난다. 필터는 쿼리 키에 들어가 바뀌면 캐시가 갈린다.
export interface RequestFilters { operation?: string; state?: string; requester?: string }
export const REQUESTS_PAGE_SIZE = 50;

function requestsUrl(f: RequestFilters, before?: number): string {
  const p = new URLSearchParams({ limit: String(REQUESTS_PAGE_SIZE) });
  if (f.operation) p.set("operation", f.operation);
  if (f.state) p.set("state", f.state);
  if (f.requester && f.requester.trim() !== "") p.set("requester", f.requester.trim());
  if (before !== undefined) p.set("before", String(before));
  return `/api/user/requests?${p.toString()}`;
}

export const useInfiniteRequests = (filters: RequestFilters) =>
  useInfiniteQuery({
    queryKey: ["requests", "infinite", filters],
    queryFn: ({ pageParam }) =>
      apiGet<RequestRow[]>(requestsUrl(filters, pageParam as number | undefined)),
    initialPageParam: undefined as number | undefined,
    // 마지막 쪽이 꽉 찼을 때만 다음 커서가 있다 -- 덜 찼으면 끝(undefined).
    getNextPageParam: (lastPage) =>
      lastPage.length === REQUESTS_PAGE_SIZE
        ? lastPage[lastPage.length - 1].commit_order : undefined,
    refetchInterval: 3000,
  });

// 「최근 작업」(대시보드) 전용이던 useRecentRequests 는 그 카드와 함께 제거됐다
// (2026-08-23 사용자 결정 -- 전체 작업 화면과 중복).

export const useRequest = (id: string) =>
  useQuery({ queryKey: ["request", id], queryFn: () => apiGet<RequestDetail>(`/api/user/requests/${id}`) });

// enabled 기본 true(요청 상세는 늘 조회한다). 문을 연 이유: 배치 항목 펼침이
// 실행 도구를 이 응답에서 읽는데(배치 API 에는 tool 이 없다) 펼치기 전엔 부르지
// 않아야 한다(lazy — ItemScanStats 와 같은 계약). 쿼리키를 공유하므로 항목에서
// 요청 상세로 이동하면 캐시가 이미 따뜻하다.
export const useRequestJobs = (id: string, enabled = true) =>
  useQuery({
    queryKey: ["request", id, "jobs"],
    queryFn: () => apiGet<DataJob[]>(`/api/user/requests/${id}/jobs`),
    enabled,
    refetchInterval: (q) => {
      const jobs = q.state.data as DataJob[] | undefined;
      return jobs && jobs.some((j) => !isTerminal(j.state)) ? 2000 : false;
    },
  });

export interface SubmitBody {
  operation: "sync" | "rm" | "scan";
  storage?: string; target?: string;
  source_storage?: string; source?: string;
  destination_storage?: string; destination?: string;
  // string 개방이 chmod/chown 을 싣는 유일한 관문이다(고급 sync 옵션 — 슬라이스 26).
  options: Record<string, boolean | number | string>;
  // 생략 = (정책 기본) — resolve_priority 가 정책 default_priority 로 해석(슬라이스 37).
  priority?: string;
  owner_username?: string;
}
export const useSubmitRequest = () =>
  useMutation({
    mutationFn: (b: SubmitBody) =>
      apiSend<{ request_id: string; state: string }>("POST", "/api/user/requests", b),
  });

export function useConfirmJob(requestId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { jobId: string; fingerprint: string }) =>
      apiSend("POST", `/api/user/jobs/${v.jobId}:confirm`, { fingerprint: v.fingerprint }),
    // ["request", id] 무효화가 접두 매칭으로 ["request", id, "jobs"] 쿼리를 이미 포함한다 — tanstack 기본 partial matching.
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["request", requestId] });
    },
  });
}
export function useCancelJob(requestId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => apiSend("POST", `/api/user/jobs/${jobId}:cancel`),
    // ["request", id] 무효화가 접두 매칭으로 ["request", id, "jobs"] 쿼리를 이미 포함한다 — tanstack 기본 partial matching.
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["request", requestId] });
    },
  });
}

export function useCancelRequest(requestId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend("POST", `/api/user/requests/${requestId}:cancel`),
    // ["request", id] 무효화가 접두 매칭으로 ["request", id, "jobs"] 쿼리를 이미 포함한다 — tanstack 기본 partial matching.
    // ["requests"](목록)는 ["request", id] 와 다른 키라 별도 무효화가 필요하다.
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["request", requestId] });
      qc.invalidateQueries({ queryKey: ["requests"] });
    },
  });
}
