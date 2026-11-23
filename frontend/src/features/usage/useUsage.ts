import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import { useMutation, useQuery, useQueryClient, type Query } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import { useArtifactBaseHealth } from "../control/useArtifactBase";
import type { PurgeStorageHistoryResult, ScanTargetRow, ScanTargetsPaged, UnregisteredStorage, UsageExport,
              UsageHistory, UsageStorageTotals, UsageStorages } from "../../lib/types";
import { DELETE_REQUESTS_INVALIDATES } from "../jobs/useJobs";

// 타깃 필터(2026-10-02): storage = 스토리지 정확히 일치, path = 경로 부분 문자열 -- 둘이 함께 걸린다.
// sort·order = 정렬 열과 방향(서버가 limit 전에 정렬 -- 화면이 받은 묶음만 뒤집거나 정렬하면 "최근 N개 안의 순위"가 된다).
// sort(2026-10-19 사용자 요청 「실사용량, 파일수, hot비율 등을 오름/내림차순 정렬」): last_scan(최근 스캔, 기본)·scans(스캔
// 횟수)·bytes(실 사용량)·files(파일 수)·hot(hot 비율) -- 서버 routes_usage.scan_targets·usage_metrics.
// storage 는 2026-10-20 부터 **늘 실린다**(화면이 처음부터 스토리지 탭 -- 사용자 요청 「필터 및 서버의 스캔도 각 스토리지
// 별로」): 빠지면 서버는 옛 탭 호환으로 전 스토리지를 보고, 지표 정렬 창(최근 스캔 순 상한)도 전 스토리지에서 만든다.
// 빈 문자열도 이름이다(변조·구형 행의 빈 이름 탭 -- 서버도 실린 storage 는 빈 값이라도 정확히 일치로 본다). 탭을 모르는
// 동안엔 패널(StoragePanel)이 아예 그려지지 않으니 「아직 모름」 표시를 이 값에 싣지 않는다.
export type UsageSort = "last_scan" | "scans" | "bytes" | "files" | "hot";
export interface TargetFilter { storage: string; path: string; order: "desc" | "asc"; sort: UsageSort }

// 한 쪽의 행 수(2026-11-16 쪽 나누기 -- 예전 「상위 100개」 상한과 같은 값, 서버 limit 상한 200 안). 행마다 최신 scan 요약
// (~3.6KB)이 실려 한 번에 수천 행이면 화면이 버벅인다 -- 전부는 쪽으로 넘겨 본다.
export const PAGE_SIZE = 100;

export function targetQuery(f: TargetFilter): string {
  const p = new URLSearchParams();
  p.set("storage", f.storage);
  if (f.path) p.set("path", f.path);
  p.set("order", f.order);
  p.set("sort", f.sort);
  return p.toString();
}

// 목록 한 쪽(화면이 읽는 모양). total = 필터에 맞는 대상 전체 수, windowed = 지표 정렬이 창(window_limit) 안에서만 돈다(창 밖
// 대상은 어느 쪽에도 없다) -- 둘 다 null = 모름: 롤링 배포 중 옛 api 파드는 offset 을 몰라 예전 배열(첫 100행)을 준다. 그때는
// 0 으로 접지 않고 「그 행들, 전체 수 모름」으로 받아(legacy) 화면이 탭의 대상 수 기반 옛 문구로 물러선다.
// unreadableReason(2026-11-23) = 이 요청에서 작업 기록 저장소(artifact base)에 접근하지 못해 리포트를 못 읽은 행이 있다(서버 최상위
// unreadable_reason -- 공개 코드 하나). 옛 배열 응답·정상이면 null.
export interface ScanTargetPage {
  rows: ScanTargetRow[]; total: number | null; windowed: boolean | null; windowLimit: number | null; legacy: boolean;
  unreadableReason: string | null;
}

// 서버 응답 -> 한 쪽. 응답은 DB 를 거친 값이라(신뢰 경계) 모양을 다시 본다 -- 배열이면 옛 파드, rows 배열이 든 객체면 쪽 응답,
// 그 밖은 오류로 던진다(빈 목록으로 접으면 「기록 없음」이라는 거짓이 된다).
export function toTargetPage(body: unknown): ScanTargetPage {
  if (Array.isArray(body)) {
    return { rows: body as ScanTargetRow[], total: null, windowed: null, windowLimit: null, legacy: true, unreadableReason: null };
  }
  const b = body as ScanTargetsPaged | null;
  if (b === null || typeof b !== "object" || !Array.isArray(b.rows)) throw new Error("목록 응답의 모양이 올바르지 않습니다");
  const count = (x: unknown) => (typeof x === "number" && Number.isInteger(x) && x >= 0 ? x : null);
  return { rows: b.rows, total: count(b.total), windowed: typeof b.windowed === "boolean" ? b.windowed : null,
           windowLimit: count(b.window_limit), legacy: false, unreadableReason: reasonOf(b.unreadable_reason) };
}

// ---- 작업 기록 저장소(artifact base) 접근 불가(2026-11-23 사고) ----
// 서버는 base 수준 점검이 실패한 요청에서만 unreadable_reason 을 싣는다(정상 경로 바이트 동일). 이 응답은 일시 장애의 산물이라
// 캐시에 굳히지 않는다: staleTime 은 사유가 있는 캐시만 0(다음 마운트·포커스에 다시 묻는다), 데이터 쿼리 자체는 폴링하지 않는다
// (스토리지마다·지표 정렬 창마다 서버가 DB 를 다시 돈다) -- 가벼운 health 만 보다가 복구되면 사용량 쿼리를 한 번 무효화한다
// (useUsageBaseRecovery).
export const reasonOf = (x: unknown): string | null => (typeof x === "string" && x !== "" ? x : null);
// 목록 한 쪽의 사유: 최상위 값, 없으면 행 latest 의 키(옛 서버가 최상위 없이 행에만 실을 일은 없지만 행 키가 진실이다).
export function pageReason(p: ScanTargetPage | undefined): string | null {
  if (p === undefined) return null;
  return p.unreadableReason ?? p.rows.reduce<string | null>((acc, r) => acc ?? reasonOf(r.latest?.unreadable_reason), null);
}
const freshFor = (ms: number, reason: string | null) => (reason !== null ? 0 : ms);
// 사용량 쿼리 키 접두 -- 복구 때 무효화하는 범위(작업 삭제의 DELETE_REQUESTS_INVALIDATES 와 같은 접두).
const USAGE_ROOTS = ["usage-targets", "usage-storages", "usage-history"] as const;
function cachedReason(q: Query): string | null {
  const d = q.state.data as unknown;
  if (d === undefined || d === null || typeof d !== "object") return null;
  switch (q.queryKey[0]) {
    case "usage-targets": return pageReason(d as ScanTargetPage);
    case "usage-history": return reasonOf((d as UsageHistory).unreadable_reason);
    case "usage-storages": return q.queryKey[1] === "totals" ? reasonOf((d as UsageStorageTotals).unreadable_reason) : null;
    default: return null;
  }
}

// 타깃 목록 한 쪽(page 는 1부터): 필터·쪽이 쿼리키에 들어가 조합별로 캐시된다. 4s 폴링은 불필요 --
// scan 이 끝나야 목록이 변하는 저빈도 데이터라 staleTime 30s 로 왕복을 줄인다. 키 접두 ["usage-targets"] 는 그대로다 --
// 작업 삭제·미등록 정리(DELETE_REQUESTS_INVALIDATES)가 그 접두로 모든 쪽을 무효화한다. 쪽 번호를 URL 에 두지 않는 것은 검색·
// 정렬과 같다(StoragePanel 상태).
// 같은 스토리지 안에서 정렬·검색·쪽을 바꿀 때만 이전 행을 자리표시로 남긴다(표가 「불러오는 중」으로 깜빡이지 않게) --
// 다른 스토리지의 행을 새 탭 아래 잠깐이라도 보이면 「이 스토리지에 그 경로가 있다」는 거짓이 된다.
export const useScanTargets = (f: TargetFilter, page: number) =>
  useQuery({
    queryKey: ["usage-targets", f.storage, f.path, f.order, f.sort, page],
    queryFn: async () => toTargetPage(await apiGet<unknown>(
      `/api/admin/usage/scan-targets?${targetQuery(f)}&offset=${(page - 1) * PAGE_SIZE}&limit=${PAGE_SIZE}`)),
    placeholderData: (prev, prevQuery) => (prevQuery?.queryKey[1] === f.storage ? prev : undefined),
    staleTime: (q) => freshFor(30_000, pageReason(q.state.data)),
  });

// 스토리지 탭 요약(2026-10-20): 탭 집합·대상 수·메타 줄·지표 정렬 창 상한의 원천 하나(서버 집계 한 번). 스토리지
// 생성·수정·삭제(useStorages)와 작업 삭제·미등록 정리(DELETE_REQUESTS_INVALIDATES)가 이 키를 무효화한다.
export const useUsageStorages = () =>
  useQuery({ queryKey: ["usage-storages"],
             queryFn: () => apiGet<UsageStorages>("/api/admin/usage/storages"), staleTime: 30_000 });

// 스토리지 합계(2026-10-21 사용자 요청 「스토리지별 사용량 및 정보들의 총합」): 스토리지 하나씩(서버가 그 스토리지 대상들의 요약을
// 읽는다 -- 전 스토리지를 한 번에 부르면 탭을 열 때마다 모든 대상을 읽는다). 패널(StorageTotals)과 「스토리지별 비교」(UsageOverview)가
// **같은 키**라 숫자 한 벌을 본다. 키를 ["usage-storages"] 아래에 두는 이유: 작업 삭제·미등록 정리(DELETE_REQUESTS_INVALIDATES)와
// 스토리지 생성·수정·삭제(useStorages)가 그 접두를 무효화한다 -- 무효화 목록을 따로 고치지 않아도 합계가 함께 새로 고쳐진다.
// 차가운 캐시: 서버는 한 호출에 요약을 정해진 수만큼만 읽고 나머지를 pending 으로 센다 -- 이번 호출이 진전했으면(progressed)
// 1초 뒤 다시 불러 이어 읽게 하고(서버는 못 읽은 리포트를 잠시 기억해 다음 호출이 그 뒤를 연다), 진전이 없으면 멈춘다(같은
// 실패를 1초마다 되풀이하지 않게). 오류 중엔 멈춘다 -- 그때 패널은 「읽는 중」이라 말하지 않는다(StorageTotals.reading).
// 빈 이름도 이름이다(encodeURIComponent("") = "" -- 서버는 실린 storage 를 빈 값이라도 정확 일치로 본다).
export const useStorageTotals = (storage: string) =>
  useQuery({
    queryKey: ["usage-storages", "totals", storage],
    queryFn: () => apiGet<UsageStorageTotals>(
      `/api/admin/usage/storage-totals?storage=${encodeURIComponent(storage)}`),
    staleTime: (q) => freshFor(30_000, reasonOf(q.state.data?.unreadable_reason)),
    // base 접근 불가로 멈춘 응답(unreadable_reason)은 이어 읽지 않는다 -- progressed 가 참이어도 다음 호출은 같은 벽에 부딪힌다.
    refetchInterval: (q) => {
      const d = q.state.data;
      return q.state.status !== "error" && d !== undefined && d.pending_count > 0 && d.progressed === true
        && reasonOf(d.unreadable_reason) === null ? 1_000 : false;
    },
  });

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
    staleTime: (q) => freshFor(60_000, reasonOf(q.state.data?.unreadable_reason)),
  });

// 복구 감시(2026-11-23): 사용량 캐시 중 하나라도 base 접근 불가 사유를 들고 있는 동안만 health(15초, 메모리 스냅숏)를 보고, ok 가
// 참으로 **바뀌는** 순간 사용량 쿼리를 한 번 무효화한다. 사용량 화면에서 한 번만 부른다(컴포넌트마다 부르면 관찰자마다 15초 타이머가
// 생긴다). 「바뀜」만 보는 이유: health 는 요청을 받은 파드의 스냅숏이라 데이터를 준 파드와 다를 수 있다 -- ok 인 동안 매번
// 무효화하면 사유가 남는 한 15초마다 데이터 쿼리를 다시 부르는 폴링이 된다. 사유가 사라지면 감시를 끄고 기억도 지운다(다음
// 장애에서 첫 ok 를 다시 「바뀜」으로 본다).
export function useUsageBaseRecovery(): boolean {
  const qc = useQueryClient();
  const cache = qc.getQueryCache();
  const subscribe = useCallback((cb: () => void) => cache.subscribe(cb), [cache]);
  const anyReason = useSyncExternalStore(subscribe, () =>
    cache.findAll({ predicate: (q) => (USAGE_ROOTS as readonly unknown[]).includes(q.queryKey[0]) })
      .some((q) => cachedReason(q) !== null));
  const health = useArtifactBaseHealth(anyReason);
  const prevOk = useRef<boolean | null>(null);
  // 사유가 나타난 시각 -- 그보다 오래된 health 캐시(지난 장애의 끝에 받은 ok:true)는 보지 않는다. 안 그러면 새 장애가
  // 시작되자마자 낡은 ok 를 「바뀜」으로 읽어 사용량 쿼리를 헛되이 한 번 다시 부른다(2026-11-23 리뷰).
  const since = useRef<number | null>(null);
  if (anyReason && since.current === null) since.current = Date.now();
  if (!anyReason) since.current = null;
  const stamp = health.dataUpdatedAt;
  const fresh = anyReason && since.current !== null && stamp >= since.current;
  const ok = fresh ? health.data?.ok ?? null : null;
  useEffect(() => {
    if (!anyReason) { prevOk.current = null; return; }
    if (ok === true && prevOk.current !== true) {
      for (const k of USAGE_ROOTS) void qc.invalidateQueries({ queryKey: [k] });
    }
    prevOk.current = ok === true ? true : ok === false ? false : prevOk.current;
  }, [anyReason, ok, stamp, qc]);
  return anyReason;
}

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
  skipped: { id: string; kind: "request" | "batch"; reason: string; request_id: string | null;
             limit?: number | null; count?: number | null; count_at_least?: number | null }[];
  // 백그라운드로 정리하기 시작한 배치(2026-11-19 -- 서버 deleting_batches, 이미 진행 중 포함). 자식 수 = 묘비가 센 수.
  deletingBatches: number; deletingRequests: number; alreadyDeletingBatches: number;
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
                                            deletingBatches: 0, deletingRequests: 0, alreadyDeletingBatches: 0,
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
      out.skipped.push({ id: s.batch_id, kind: "batch", reason: s.reason, request_id: s.request_id,
                         limit: s.limit, count: s.count, count_at_least: s.count_at_least });
    }
    // 백그라운드 몫(2026-11-19) -- 「시도함」으로 센다(서버 remaining_untried 도 같다 -- 회차 고리가 멈춘다). 옛 응답은 키가 없다.
    for (const d of r.deleting_batches ?? []) {
      exB.add(d.batch_id);
      // 이번에 시작한 것과 이미 삭제 중이던 것(already -- 이전 정리·다른 관리자)을 나눠 센다(작업 목록 결과 문구와 같은 축).
      if (d.already === true) {
        out.alreadyDeletingBatches += 1;
      } else {
        out.deletingBatches += 1;
        out.deletingRequests += d.request_count;
      }
    }
    out.remaining = r.remaining;
    // 구형 응답(키 없음)은 모름 -- 멈춘다.
    const u = r.remaining_untried ?? null;
    out.untried = u === null ? null : u.requests + u.batches;
    onProgress?.(out.deletedRequests);
    const tried = r.deleted.length + r.deleted_batches.length + r.skipped.length + r.skipped_batches.length
      + (r.deleting_batches?.length ?? 0);
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
