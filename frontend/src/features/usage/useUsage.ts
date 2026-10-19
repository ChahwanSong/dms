import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { PurgeStorageHistoryResult, ScanTargetRow, UnregisteredStorage, UsageExport,
              UsageHistory, UsageStorages } from "../../lib/types";
import { DELETE_REQUESTS_INVALIDATES } from "../jobs/useJobs";

// 타깃 필터(2026-10-02): storage = 스토리지 정확히 일치, path = 경로 부분 문자열 -- 둘이 함께 걸린다.
// sort·order = 정렬 열과 방향(서버가 limit 전에 정렬 -- 화면이 받은 묶음만 뒤집거나 정렬하면 "최근 N개 안의 순위"가 된다).
// sort(2026-10-19 사용자 요청 「실사용량, 파일수, hot비율 등을 오름/내림차순 정렬」): last_scan(최근 스캔, 기본)·scans(스캔
// 횟수)·bytes(실 사용량)·files(파일 수)·hot(hot 비율) -- 서버 routes_usage.scan_targets·usage_metrics.
// storage 는 2026-10-20 부터 **늘 실린다**(화면이 처음부터 스토리지 탭 -- 사용자 요청 「필터 및 서버의 스캔도 각 스토리지
// 별로」): 빠지면 서버는 옛 탭 호환으로 전 스토리지를 보고, 지표 정렬 창(최근 스캔 순 상한)도 전 스토리지에서 만든다.
// 빈 문자열 = 아직 탭을 모름 -- 요청을 보내지 않는다(useScanTargets enabled).
export type UsageSort = "last_scan" | "scans" | "bytes" | "files" | "hot";
export interface TargetFilter { storage: string; path: string; order: "desc" | "asc"; sort: UsageSort }

// 목록 한 번에 받는 행 수(서버 기본 100과 같다) -- 다 차면 화면이 「상위 N개 표시 · 전체 M개」라고 말한다.
export const LIST_LIMIT = 100;

export function targetQuery(f: TargetFilter): string {
  const p = new URLSearchParams();
  p.set("storage", f.storage);
  if (f.path) p.set("path", f.path);
  p.set("order", f.order);
  p.set("sort", f.sort);
  return p.toString();
}

// 타깃 목록: 필터가 쿼리키에 들어가 조합별로 캐시된다. 4s 폴링은 불필요 --
// scan 이 끝나야 목록이 변하는 저빈도 데이터라 staleTime 30s 로 왕복을 줄인다.
// 같은 스토리지 안에서 정렬·검색을 바꿀 때만 이전 행을 자리표시로 남긴다(표가 「불러오는 중」으로 깜빡이지 않게) --
// 다른 스토리지의 행을 새 탭 아래 잠깐이라도 보이면 「이 스토리지에 그 경로가 있다」는 거짓이 된다.
export const useScanTargets = (f: TargetFilter) =>
  useQuery({
    queryKey: ["usage-targets", f.storage, f.path, f.order, f.sort],
    queryFn: () => apiGet<ScanTargetRow[]>(`/api/admin/usage/scan-targets?${targetQuery(f)}&limit=${LIST_LIMIT}`),
    enabled: f.storage !== "",
    placeholderData: (prev, prevQuery) => (prevQuery?.queryKey[1] === f.storage ? prev : undefined),
    staleTime: 30_000,
  });

// 스토리지 탭 요약(2026-10-20): 탭 집합·대상 수·메타 줄·지표 정렬 창 상한의 원천 하나(서버 집계 한 번). 스토리지
// 생성·수정·삭제(useStorages)와 작업 삭제·미등록 정리(DELETE_REQUESTS_INVALIDATES)가 이 키를 무효화한다.
export const useUsageStorages = () =>
  useQuery({ queryKey: ["usage-storages"],
             queryFn: () => apiGet<UsageStorages>("/api/admin/usage/storages"), staleTime: 30_000 });

// 성공 scan 기록이 있는 스토리지 이름 -- 요약(useUsageStorages)을 못 받았을 때만 쓰는 대체 원천(enabled): 탭은 등록
// 목록 ∪ 이 이름들로 그리고 대상 수는 모름으로 둔다.
export const useScanStorages = (enabled = true) =>
  useQuery({ queryKey: ["usage-scan-storages"],
             queryFn: () => apiGet<string[]>("/api/admin/usage/scan-storages"), staleTime: 60_000, enabled });

// 전체 내보내기(CSV 버튼): 화면 상태가 아니라 버튼을 누를 때 한 번 -- 필터·정렬은 목록과 같다(지금 탭의 스토리지만).
export const fetchUsageExport = (f: TargetFilter) =>
  apiGet<UsageExport>(`/api/admin/usage/export?${targetQuery(f)}`);

// 이력: 선택된 타깃에서만 나간다(enabled). 아티팩트 읽기(포인트당 최대 256KiB
// I/O)가 뒤에 있으므로 staleTime 을 길게 -- 성공 종단 잡의 리포트는 **삭제되기 전까지** 불변이고,
// 새 스캔이 끝나 목록의 last_scan_at 이 변하면 사용자가 타깃을 다시 고르는
// 동선에서 자연히 재조회된다. 관리자가 작업(요청)을 지우면 그 지점이 사라지므로 useDeleteRequests 가
// usage-targets·usage-storages·usage-history 를 무효화한다(2026-10-08). limit(표시 창)은 쿼리키에 들어가
// 창별로 캐시된다 -- 30→60→30 왕복이 재조회 없이 즉시다. 창을 처음 넓힐 때는 같은 타깃의 이전 창을 자리표시로 남긴다
// (2026-10-20 -- 상세 전체가 「불러오는 중」 한 줄로 접혔다 펴지며 화면이 튀지 않게). 다른 타깃의 이력은 남기지 않는다.
export const useScanHistory = (storage: string | null, target: string | null,
                               limit = 30) =>
  useQuery({
    queryKey: ["usage-history", storage, target, limit],
    queryFn: () => apiGet<UsageHistory>(
      `/api/admin/usage/scan-history?storage=${encodeURIComponent(storage as string)}`
      + `&target=${encodeURIComponent(target as string)}&limit=${limit}`),
    enabled: storage !== null && target !== null,
    placeholderData: (prev, prevQuery) =>
      (prevQuery?.queryKey[1] === storage && prevQuery?.queryKey[2] === target ? prev : undefined),
    staleTime: 60_000,
  });

// 미등록 스토리지(2026-10-12 사용자 요청: 「미등록 스토리지 항목들은 스토리지 항목별로 전체 clean up」). 판정·규모는 서버가
// 한다(등록 목록 로딩 여부와 무관). 목록과 같은 저빈도 데이터라 staleTime 30s.
export const useUnregisteredStorages = () =>
  useQuery({ queryKey: ["usage-unregistered"],
             queryFn: () => apiGet<UnregisteredStorage[]>("/api/admin/usage/unregistered-storages"),
             staleTime: 30_000 });

// 정리 한 번 = 서버 회차(배치 10·단건 200)를 시도할 것이 남는 동안 반복. 건너뛴 항목은 다음 회차의 exclude_* 로 보내
// 빠지게 한다(서버 라우트 주석 -- 앞쪽의 영구 거부가 회차 상한을 매번 차지하지 않게). 멈춤: 이번 회차에 시도한 것이
// 없거나, 서버가 센 「다음 회차가 시도할 것」(remaining_untried)이 0 이거나 모름(null)이거나, 회차 상한. 건너뛴 id 의
// **개수**와 remaining 을 비교하지 않는다 -- 건너뛴 뒤 다른 곳에서 지워진 id 만큼 시도하지 않은 기록을 남긴 채 멈췄다(리뷰).
// 회차 상한 x 회차 단건 상한(200) = 서버 exclude_request_ids 상한(20000) -- 같이 바꾼다.
export const PURGE_MAX_ROUNDS = 100;
export interface StorageHistoryPurgeSummary {
  storage_name: string;
  deletedRequests: number;          // 단건 + 배치 자식
  deletedBatches: number;
  skipped: { id: string; kind: "request" | "batch"; reason: string; request_id: string | null }[];
  remaining: { requests: number; batches: number } | null;
  // 멈춘 때 아직 시도하지 않은 수(회차 상한·중간 오류로 멈췄을 때만 0 이 아니다). null = 모름.
  untried: number | null;
  requestIds: string[]; jobIds: string[]; batchIds: string[];
  // 첫 회차 뒤에 난 오류(앞 회차의 삭제는 이미 커밋됐다 -- 그 수를 오류 뒤에 숨기지 않는다). 첫 회차 오류는 그대로 던진다.
  error: string | null;
}
export async function purgeStorageHistory(storage_name: string,
                                          onProgress?: (deleted: number) => void): Promise<StorageHistoryPurgeSummary> {
  const out: StorageHistoryPurgeSummary = { storage_name, deletedRequests: 0, deletedBatches: 0, skipped: [],
                                            remaining: null, untried: null, requestIds: [], jobIds: [], batchIds: [],
                                            error: null };
  const exR = new Set<string>(), exB = new Set<string>();
  for (let round = 0; round < PURGE_MAX_ROUNDS; round++) {
    let r: PurgeStorageHistoryResult;
    try {
      r = await apiSend<PurgeStorageHistoryResult>("POST", "/api/admin/usage/unregistered-storages:purge",
        { storage_name, exclude_request_ids: [...exR], exclude_batch_ids: [...exB] });
    } catch (e) {
      if (round === 0) throw e;
      out.error = (e as Error).message ?? "정리 중 오류가 났습니다";
      break;
    }
    for (const d of r.deleted) { out.requestIds.push(d.request_id); out.jobIds.push(...d.job_ids); }
    for (const b of r.deleted_batches) {
      out.batchIds.push(b.batch_id); out.requestIds.push(...b.request_ids); out.jobIds.push(...b.job_ids);
    }
    out.deletedRequests += r.deleted.length + r.deleted_batches.reduce((a, b) => a + b.request_ids.length, 0);
    out.deletedBatches += r.deleted_batches.length;
    for (const s of r.skipped) {
      exR.add(s.request_id);
      out.skipped.push({ id: s.request_id, kind: "request", reason: s.reason, request_id: s.request_id });
    }
    for (const s of r.skipped_batches) {
      exB.add(s.batch_id);
      out.skipped.push({ id: s.batch_id, kind: "batch", reason: s.reason, request_id: s.request_id });
    }
    out.remaining = r.remaining;
    // 구형 응답(키 없음)은 모름 -- 멈춘다.
    const u = r.remaining_untried ?? null;
    out.untried = u === null ? null : u.requests + u.batches;
    onProgress?.(out.deletedRequests);
    const tried = r.deleted.length + r.deleted_batches.length + r.skipped.length + r.skipped_batches.length;
    if (tried === 0 || out.untried === null || out.untried === 0) break;
  }
  return out;
}

export function usePurgeUnregisteredStorage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { storage_name: string; onProgress?: (n: number) => void }) =>
      purgeStorageHistory(v.storage_name, v.onProgress),
    // 지운 요청·잡·배치의 캐시 제거 + 작업 삭제와 같은 무효화(사용량·이 목록·대시보드·감사·정리 대기·배치).
    onSuccess: (r) => {
      for (const rid of r.requestIds) {
        qc.removeQueries({ queryKey: ["request", rid] });
        qc.removeQueries({ queryKey: ["request-scan-stats", rid] });
      }
      for (const jid of r.jobIds) for (const k of ["artifacts", "artifact", "joblogs"]) qc.removeQueries({ queryKey: [k, jid] });
      for (const bid of r.batchIds) qc.removeQueries({ queryKey: ["batch", bid] });
    },
    onSettled: () => Promise.all(DELETE_REQUESTS_INVALIDATES.map((k) => qc.invalidateQueries({ queryKey: [...k] }))),
  });
}
