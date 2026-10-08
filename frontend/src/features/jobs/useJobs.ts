import { useQuery, useMutation, useQueryClient, useInfiniteQuery } from "@tanstack/react-query";
import { ApiError, apiGet, apiSend } from "../../lib/api";
import { REQUEST_TERMINAL_STATES, isTerminal } from "../../lib/jobState";
import type { RequestRow, RequestDetail, DataJob, DeleteRequestsResult, PurgeStatus } from "../../lib/types";

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

// 요청 자신도 비종단인 동안 3초마다 다시 읽는다(2026-10-08) -- 예전엔 한 번 읽고 끝이라, 잡은 2초 폴링으로
// 끝났는데 요청 pill·전이 이력·사유는 들어올 때 모습 그대로 낡아 있었다. 종단이면 멈춘다(요청 종단 집합 --
// Conflict 포함). 이 키(["request", id])의 폴링은 잡 키(["request", id, "jobs"])를 건드리지 않는다(e2e E6:
// 종단 뒤 /jobs 요청 0건).
//
// 404(삭제됐거나 볼 수 없는 요청, 2026-10-08 작업 삭제)를 본 뒤에는 다시 읽지 않는다 -- 폴링·창 포커스·재연결 모두.
// 지워진 요청은 돌아오지 않고, 데이터 없는 쿼리의 재조회는 status 를 pending 으로 되돌려 「없는 요청」 화면이 로딩
// 골격으로 깜빡이고 그 틈에 잡 조회가 다시 켜진다(RequestDetail 은 404 동안 잡 조회를 끈다).
export const requestGone = (error: unknown): boolean => error instanceof ApiError && error.status === 404;
const goneQuery = (q: { state: { error: unknown } }) => requestGone(q.state.error);
export const useRequest = (id: string) =>
  useQuery({
    queryKey: ["request", id],
    queryFn: () => apiGet<RequestDetail>(`/api/user/requests/${id}`),
    refetchInterval: (q) => {
      if (goneQuery(q)) return false;
      const r = q.state.data as RequestDetail | undefined;
      return r && !REQUEST_TERMINAL_STATES.has(r.state) ? 3000 : false;
    },
    refetchOnWindowFocus: (q) => !goneQuery(q),
    refetchOnReconnect: (q) => !goneQuery(q),
  });

// enabled 기본 true(요청 상세는 늘 조회한다). 문을 연 이유: 배치 항목 펼침이
// 실행 도구를 이 응답에서 읽는데(배치 API 에는 tool 이 없다) 펼치기 전엔 부르지
// 않아야 한다(lazy — ItemScanStats 와 같은 계약). 쿼리키를 공유하므로 항목에서
// 요청 상세로 이동하면 캐시가 이미 따뜻하다.
//
// requestTerminal(요청 상세만 넘긴다): 잡이 0개여도 요청이 비종단이면 계속 돈다. `[].some()` 은 false 라, 제출
// 직후(플래너가 잡을 만들기 전) 들어온 상세는 잡이 영영 안 나타났다. 모르면(undefined -- 요청이 아직 안 왔거나
// 배치 호출부) 예전 규칙(비종단 잡이 있을 때만)이다.
//
// 데이터 없이 실패한 상태(잡 모름)면 2초 폴링을 멈춘다(2026-10-08 리뷰 V1, 스펙 §5.2 「잡 모름」 = 요청 3s 만) --
// 데이터 없는 쿼리는 재조회마다 pending 으로 돌아가 화면이 2초마다 로딩으로 흔들리고, 고장 난 행(500)을 2초마다
// 두드려도 나아지지 않는다. 다시 읽는 길은 오류 상자의 「다시 시도」다(옵저버가 상태가 바뀔 때마다 interval 을 다시
// 계산하므로 재시도가 pending 으로 바꾸면 폴링이 돌아오고, 또 실패하면 다시 멈춘다).
export const useRequestJobs = (id: string, enabled = true, opts?: { requestTerminal?: boolean }) =>
  useQuery({
    queryKey: ["request", id, "jobs"],
    queryFn: () => apiGet<DataJob[]>(`/api/user/requests/${id}/jobs`),
    enabled,
    refetchInterval: (q) => {
      const jobs = q.state.data as DataJob[] | undefined;
      if (jobs === undefined && q.state.status === "error") return false;
      const anyLive = Array.isArray(jobs) && jobs.some((j) => !isTerminal(j.state));
      return opts?.requestTerminal === false || anyLive ? 2000 : false;
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
  // root 실행(2026-09-30). 서버는 true 만 root(자격 없으면 403), false/생략 = 실행 신원의
  // uid/gid. "관리자 기본 root" 는 SubmitJob 이 정해 관리자에겐 항상 확정값을 싣는다.
  run_as_root?: boolean;
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

// 작업(요청) 선택 삭제(2026-10-08, 관리자 전용 -- 서버는 세션 관리자만: 공유 토큰 403 admin_session_required).
// 일괄 1회 POST 의 **부분 성공** 모델(items:rerun 관례): 전체 실패(403·422·503)는 isError, 항목별 제외는
// data.skipped(사유 코드)다 -- 판정은 서버만 정확히 한다(화면은 3초 낡은 스냅숏을 본다).
//
// 성공 시 지운 요청·잡의 캐시는 invalidate 가 아니라 **제거**한다(useDeleteBatches 와 같은 이유): 다시 읽으면
// 404 만 새로 받는다. ["request", id] 는 접두 매칭이라 ["request", id, "jobs"] 도 함께 지운다.
//
// onSettled 는 무효화 프라미스를 **돌려준다** -- 훅의 onSettled 가 끝나야 mutation 이 성공으로 바뀌므로, 결과 문구
// (「N개 삭제됨」)가 뜰 때 표에서 그 행은 이미 사라져 있다(useDeleteBatches 관례, 2026-08-15 사용자 보고).
// 사용량 분석·대시보드 잡 통계·감사 로그는 지운 행에서 계산되므로 함께 무효화한다(지운 scan 의 사용량 지점이
// 빠지고 기간 통계가 소급해 줄며 감사에 삭제 행이 생긴다). artifact-base 의 잠금 건수(잡 + 정리 대기)도 바뀐다.
export const DELETE_REQUESTS_INVALIDATES: readonly (readonly string[])[] = [
  ["requests"], ["request-purges"], ["usage-targets"], ["usage-scan-storages"], ["usage-history"],
  ["metrics", "jobs"], ["audit"], ["artifact-base"],
];
export function useDeleteRequests() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ids: string[]) =>
      apiSend<DeleteRequestsResult>("POST", "/api/admin/requests:delete", { request_ids: ids }),
    onSuccess: (r) => {
      for (const d of r.deleted) {
        qc.removeQueries({ queryKey: ["request", d.request_id] });
        qc.removeQueries({ queryKey: ["request-scan-stats", d.request_id] });
        for (const jid of d.job_ids) {
          for (const k of ["artifacts", "artifact", "joblogs"]) qc.removeQueries({ queryKey: [k, jid] });
        }
      }
    },
    onSettled: () => Promise.all(
      DELETE_REQUESTS_INVALIDATES.map((k) => qc.invalidateQueries({ queryKey: [...k] }))),
  });
}

// 정리 대기 현황(결과 파일·파드 정리, 컨트롤러 request-purge 루프). 대기가 있으면 5초, 없으면 30초 -- 삭제 직후의
// 진행을 따라가되 평소엔 거의 쉰다. enabled = 관리자 화면에서만(일반 사용자는 403 이라 부르지 않는다).
export const usePurgeStatus = (enabled: boolean) =>
  useQuery({
    queryKey: ["request-purges"],
    queryFn: () => apiGet<PurgeStatus>("/api/admin/request-purges"),
    enabled,
    refetchInterval: (q) => ((q.state.data as PurgeStatus | undefined)?.pending ?? 0) > 0 ? 5000 : 30000,
  });
