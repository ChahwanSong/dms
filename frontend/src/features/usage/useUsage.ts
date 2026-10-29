import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiGet, apiSend } from "../../lib/api";
import type { PurgeStorageHistoryResult, UnregisteredStorage, UsageAttrFilter, UsageExport, UsageFacets,
              UsageFiltersEcho, UsageHistory, UsageScope, UsageStorageTotals, UsageStorages,
              UsageTargetsPage } from "../../lib/types";
import { DELETE_REQUESTS_INVALIDATES } from "../jobs/useJobs";

// 타깃 필터(2026-10-02): storage = 스토리지 정확히 일치, q = 검색 -- 둘이 함께 걸린다. q(2026-10-29 사용자 요청 「경로로 검색할 뿐
// 아니라 pv 이름도 검색 … 일부 단어만 포함해도」 -- 예전 path(경로 부분 문자열 하나)를 대신한다): 서버 search_tokens 의 토큰마다
// 대소문자 무시 부분 일치, 토큰 사이 AND, 토큰마다 그 대상의 경로 또는 소속 PV 이름(PVC) 어느 쪽이든(작업 목록 검색과 같은 뜻).
// PV 이름이 생길 수 있는 스토리지에선 서버가 PV 판정 창(최근 스캔 순) 안에서 거른다 -- 넘치면 matched_count 모름(StoragePanel 이
// 말한다). 옛 path 파라미터는 서버에 남아 있지만(옛 탭 호환) 이 포탈은 보내지 않는다.
// sort·order = 정렬 열과 방향(서버가 limit 전에 정렬 -- 화면이 받은 묶음만 뒤집거나 정렬하면 "최근 N개 안의 순위"가 된다).
// sort(2026-10-19 사용자 요청 「실사용량, 파일수, hot비율 등을 오름/내림차순 정렬」): last_scan(최근 스캔, 기본)·scans(스캔
// 횟수)·bytes(실 사용량)·files(파일 수)·hot(hot 비율) -- 서버 routes_usage.scan_targets·usage_metrics.
// storage 는 2026-10-20 부터 **늘 실린다**(화면이 처음부터 스토리지 탭 -- 사용자 요청 「필터 및 서버의 스캔도 각 스토리지
// 별로」): 빠지면 서버는 옛 탭 호환으로 전 스토리지를 보고, 지표 정렬 창(최근 스캔 순 상한)도 전 스토리지에서 만든다.
// 빈 문자열도 이름이다(변조·구형 행의 빈 이름 탭 -- 서버도 실린 storage 는 빈 값이라도 정확히 일치로 본다). 탭을 모르는
// 동안엔 패널(StoragePanel)이 아예 그려지지 않으니 「아직 모름」 표시를 이 값에 싣지 않는다.
export type UsageSort = "last_scan" | "scans" | "bytes" | "files" | "hot";
export const USAGE_SORTS: readonly UsageSort[] = ["last_scan", "scans", "bytes", "files", "hot"];
// attr(2026-10-29 사용자 요청 「사용자·PV 이름·namespace 등으로 필터」): 클러스터·namespace·PV 이름(pvc)·StorageClass·권한 사용자·
// 구분(scope)·요청자. 모두 **대상 단위**다(서버 routes_usage -- 한 경로의 scan 이력이 통째로 걸리거나 빠진다: 스캔 횟수·최근
// scan·이력은 늘 그 경로의 전체 기록). 2026-10-29 부터 검색(q)·정렬(sort·order)도 URL 에 실린다(UsageAnalysis).
export interface TargetFilter {
  storage: string; q: string; order: "desc" | "asc"; sort: UsageSort; attr: UsageAttrFilter;
}

// 목록 한 번에 받는 행 수(서버 기본 100과 같다) -- 다 차면 화면이 「상위 N개 표시 · 전체 M개」라고 말한다.
export const LIST_LIMIT = 100;

// 필터 키의 고정 순서 -- 쿼리 문자열·쿼리키·URL 이 같은 순서라 같은 조합이 늘 같은 문자열이다. 이름은 서버 쿼리 파라미터 =
// URL 키 = PV 탐색 화면(VolumesPage)의 키(후속 딥링크가 그대로 걸리게).
export const ATTR_KEYS = ["cluster", "namespace", "pvc", "storage_class", "user", "scope", "requester"] as const;
export type AttrKey = (typeof ATTR_KEYS)[number];
export const SCOPES: readonly UsageScope[] = ["pv", "outside", "unknown"];
// PV 축(같은 PVC 정체 하나가 모두 만족해야 한다 -- 서버 결합 규칙). 구분 「PV 밖」·「모름」과는 함께 걸 수 없다(늘 0건).
export const PV_AXES = ["cluster", "namespace", "pvc", "storage_class", "user"] as const;

// 값이 있는 키만(빈 문자열은 「없음」 -- 서버는 빈 값을 422 로 거절한다). scope 는 아는 값만 -- 손으로 친 URL 의 엉뚱한 값을
// 서버에 실어 422 를 만들지 않는다.
export function cleanAttr(a: Partial<Record<AttrKey, string | null | undefined>>): UsageAttrFilter {
  const out: UsageAttrFilter = {};
  for (const k of ATTR_KEYS) {
    const v = a[k];
    if (typeof v !== "string" || v === "") continue;
    if (k === "scope") { if ((SCOPES as readonly string[]).includes(v)) out.scope = v as UsageScope; }
    else out[k] = v;
  }
  return out;
}
export const hasAttr = (a: UsageAttrFilter): boolean => ATTR_KEYS.some((k) => a[k] !== undefined);
export const hasPvAxis = (a: UsageAttrFilter): boolean => PV_AXES.some((k) => a[k] !== undefined);

function appendAttr(p: URLSearchParams, a: UsageAttrFilter): void {
  for (const k of ATTR_KEYS) { const v = a[k]; if (v !== undefined && v !== "") p.set(k, v); }
}
// 쿼리키 조각: 고정 순서 직렬화, 필터가 없으면 "".
export function attrKey(a: UsageAttrFilter): string {
  const p = new URLSearchParams();
  appendAttr(p, a);
  return p.toString();
}

export function targetQuery(f: TargetFilter): string {
  const p = new URLSearchParams();
  p.set("storage", f.storage);
  if (f.q) p.set("q", f.q);
  p.set("order", f.order);
  p.set("sort", f.sort);
  appendAttr(p, f.attr);
  return p.toString();
}

// 배포 틈의 두 모양(2026-10-29): 새 포탈이 옛 API 를 부르면 (1) 새 경로가 없다 -- 404·405 또는 SPA index.html 200(JSON 파서
// 오류), 봉투가 아닌 배열, (2) 모르는 쿼리 파라미터를 FastAPI 가 조용히 무시해 「필터를 건 척한 전체 목록」이 온다 -- 서버가
// 되돌려 준 적용 필터(filters)가 보낸 값과 다르다. 둘 다 오류로 말한다(조용한 무필터가 가장 나쁜 거짓이다). 메시지는 화면 문구
// 그대로라 StoragePanel·StorageTotals 가 .message 를 그대로 보인다. 서버 사유 코드가 아니다(reasonCodes 무관 -- 포탈 판정).
export const USAGE_API_UNKNOWN = "서버가 새 사용량 API 를 아직 모릅니다(배포 중일 수 있음) — 잠시 뒤 새로고침하세요";
export const USAGE_FILTER_MISMATCH = "서버가 적용한 필터가 화면과 다릅니다 — 새로고침하세요";
export class UsageContractError extends Error {
  constructor(public kind: "unknown_api" | "filter_mismatch") {
    super(kind === "unknown_api" ? USAGE_API_UNKNOWN : USAGE_FILTER_MISMATCH);
  }
}

const isObj = (x: unknown): x is Record<string, unknown> => typeof x === "object" && x !== null && !Array.isArray(x);

async function getNew(url: string): Promise<unknown> {
  try {
    return await apiGet<unknown>(url);
  } catch (e) {
    if (e instanceof SyntaxError || (e instanceof ApiError && (e.status === 404 || e.status === 405))) {
      throw new UsageContractError("unknown_api");
    }
    throw e;
  }
}

// 메아리 대조: 보낸 필터와 서버가 적용한 필터가 키마다 같아야 한다(메아리의 null·부재 = 그 필터 없음). q 는 목록·내보내기만
// (합계는 검색을 받지 않는다 -- 메아리에 없어도 된다). storage 는 메아리에 있으면 같아야 한다. q 를 모르는 옛 서버는 검색을 조용히
// 무시하고 메아리에 q 가 없다 -- 검색이 있으면 filter_mismatch(검색한 척한 전체 목록을 보이지 않는다, 2026-10-29).
function echoMatches(echo: unknown, storage: string, attr: UsageAttrFilter, q?: string): boolean {
  if (!isObj(echo)) return false;
  const e = echo as UsageFiltersEcho;
  if (e.storage !== undefined && e.storage !== storage) return false;
  if (q !== undefined && (e.q ?? "") !== q) return false;
  return ATTR_KEYS.every((k) => (e[k] ?? null) === (attr[k] ?? null));
}

// 타깃 목록(2026-10-29 새 봉투 엔드포인트 /targets -- 옛 scan-targets 는 옛 탭 호환으로 동결): 필터가 쿼리키에 들어가 조합별로
// 캐시된다. 4s 폴링은 불필요 -- scan 이 끝나야 목록이 변하는 저빈도 데이터라 staleTime 30s 로 왕복을 줄인다. 쿼리키의 storage 는
// 늘 인덱스 1, 필터 조각은 끝(attrKey) -- 접두 ["usage-targets"] 무효화(작업 삭제 등)가 그대로 걸린다.
// 같은 스토리지·같은 필터 안에서 정렬·검색을 바꿀 때만 이전 행을 자리표시로 남긴다(표가 「불러오는 중」으로 깜빡이지 않게) --
// 다른 스토리지·다른 필터의 행을 잠깐이라도 보이면 「이 스토리지(이 필터)에 그 경로가 있다」는 거짓이 된다.
export const useScanTargets = (f: TargetFilter) => {
  const ak = attrKey(f.attr);
  return useQuery({
    queryKey: ["usage-targets", f.storage, f.q, f.order, f.sort, ak],
    queryFn: async (): Promise<UsageTargetsPage> => {
      const body = await getNew(`/api/admin/usage/targets?${targetQuery(f)}&limit=${LIST_LIMIT}`);
      if (!isObj(body) || !Array.isArray(body.rows) || !isObj(body.filters)) throw new UsageContractError("unknown_api");
      if (!echoMatches(body.filters, f.storage, f.attr, f.q)) throw new UsageContractError("filter_mismatch");
      const page = body as unknown as UsageTargetsPage;
      // 모르는 수는 null(모름 ≠ 0) -- 구형·부분 응답의 부재를 0 으로 접지 않는다.
      return { ...page, matched_count: typeof page.matched_count === "number" ? page.matched_count : null,
               window_truncated: page.window_truncated === true };
    },
    placeholderData: (prev, prevQuery) =>
      (prevQuery?.queryKey[1] === f.storage && prevQuery?.queryKey[5] === ak ? prev : undefined),
    staleTime: 30_000,
  });
};

// 필터 선택지(2026-10-29): 옵션마다 대상 수. ["usage-storages"] 접두 아래라 작업 삭제·스토리지 변경 무효화에 함께 걸린다.
// 실패하면 필터 줄을 그리지 않는다(StoragePanel) -- 자동 재시도로 매번 기다리게 하지 않는다(retry false).
export const useUsageFacets = (storage: string, enabled = true) =>
  useQuery({
    queryKey: ["usage-storages", "facets", storage],
    queryFn: async (): Promise<UsageFacets> => {
      const body = await getNew(`/api/admin/usage/facets?storage=${encodeURIComponent(storage)}`);
      if (!isObj(body) || !isObj(body.scope)) throw new UsageContractError("unknown_api");
      const arr = (x: unknown) => (Array.isArray(x) ? x.filter(isObj) : []);
      const num = (x: unknown) => (typeof x === "number" && Number.isFinite(x) && x > 0 ? x : 0);
      const s = body.scope as Record<string, unknown>;
      const f = body as unknown as UsageFacets;
      return {
        ...f,
        scope: { inventory: num(s.inventory), recorded: num(s.recorded), outside: num(s.outside), unknown: num(s.unknown) },
        clusters: arr(body.clusters) as UsageFacets["clusters"], namespaces: arr(body.namespaces) as UsageFacets["namespaces"],
        pvcs: arr(body.pvcs) as UsageFacets["pvcs"], storage_classes: arr(body.storage_classes) as UsageFacets["storage_classes"],
        users: arr(body.users) as UsageFacets["users"], requesters: arr(body.requesters) as UsageFacets["requesters"],
        lists_truncated: isObj(body.lists_truncated) ? (body.lists_truncated as Record<string, boolean>) : {},
        // PV 선택지가 최근 스캔된 창 안의 것뿐인가(UsageFilters 가 말한다) -- 아는 참만 참이다.
        window_truncated: body.window_truncated === true,
      };
    },
    enabled,
    staleTime: 30_000,
    retry: false,
  });

// 스토리지 탭 요약(2026-10-20): 탭 집합·대상 수·메타 줄·지표 정렬 창 상한의 원천 하나(서버 집계 한 번). 스토리지
// 생성·수정·삭제(useStorages)와 작업 삭제·미등록 정리(DELETE_REQUESTS_INVALIDATES)가 이 키를 무효화한다.
export const useUsageStorages = () =>
  useQuery({ queryKey: ["usage-storages"],
             queryFn: () => apiGet<UsageStorages>("/api/admin/usage/storages"), staleTime: 30_000 });

// 스토리지 합계(2026-10-21 사용자 요청 「스토리지별 사용량 및 정보들의 총합」): 스토리지 하나씩(서버가 그 스토리지 대상들의 요약을
// 읽는다 -- 전 스토리지를 한 번에 부르면 탭을 열 때마다 모든 대상을 읽는다). 경로별의 합계 줄(TotalsLine)과 「스토리지별」 화면의
// 비교 표(UsageOverview.CompareTable -- 열 정렬 때문에 useQueries 로 한 번에)가 **같은 키**라 숫자 한 벌을 본다. 키를 ["usage-storages"] 아래에 두는 이유: 작업 삭제·미등록 정리(DELETE_REQUESTS_INVALIDATES)와
// 스토리지 생성·수정·삭제(useStorages)가 그 접두를 무효화한다 -- 무효화 목록을 따로 고치지 않아도 합계가 함께 새로 고쳐진다.
// 차가운 캐시: 서버는 한 호출에 요약을 정해진 수만큼만 읽고 나머지를 pending 으로 센다 -- 이번 호출이 진전했으면(progressed)
// 1초 뒤 다시 불러 이어 읽게 하고(서버는 못 읽은 리포트를 잠시 기억해 다음 호출이 그 뒤를 연다), 진전이 없으면 멈춘다(같은
// 실패를 1초마다 되풀이하지 않게). 오류 중엔 멈춘다 -- 그때 패널은 「읽는 중」이라 말하지 않는다(StorageTotals.reading).
// 빈 이름도 이름이다(encodeURIComponent("") = "" -- 서버는 실린 storage 를 빈 값이라도 정확 일치로 본다).
// 필터 합계(2026-10-29, attr 이 있을 때만): 키 끝에 필터 조각, URL 에 필터(검색은 싣지 않는다 -- 합계는 검색과 무관,
// 2026-10-21 규칙 유지). 필터가 없으면 키·URL 이 그 전과 같다(스토리지별 비교 표와 숫자 한 벌). 필터 합계는 서버 메아리(filters)가
// 없거나 다르면 오류다(옛 서버가 필터를 무시하고 스토리지 전체 합계를 「필터 합계」로 보이지 않게).
export const storageTotalsOptions = (storage: string, attr?: UsageAttrFilter) => {
  const a = attr ?? {};
  const filtered = hasAttr(a);
  const ak = filtered ? attrKey(a) : "";
  return queryOptions({
    queryKey: filtered ? ["usage-storages", "totals", storage, ak] : ["usage-storages", "totals", storage],
    queryFn: async (): Promise<UsageStorageTotals> => {
      if (!filtered) {
        return apiGet<UsageStorageTotals>(`/api/admin/usage/storage-totals?storage=${encodeURIComponent(storage)}`);
      }
      const p = new URLSearchParams();
      p.set("storage", storage);
      appendAttr(p, a);
      const body = await getNew(`/api/admin/usage/storage-totals?${p.toString()}`);
      if (!isObj(body)) throw new UsageContractError("unknown_api");
      if (!echoMatches(body.filters, storage, a)) throw new UsageContractError("filter_mismatch");
      return body as unknown as UsageStorageTotals;
    },
    staleTime: 30_000,
    refetchInterval: (q) => {
      const d = q.state.data;
      return q.state.status !== "error" && d !== undefined && d.pending_count > 0 && d.progressed === true ? 1_000 : false;
    },
  });
};
export const useStorageTotals = (storage: string, attr?: UsageAttrFilter) => useQuery(storageTotalsOptions(storage, attr));

// 성공 scan 기록이 있는 스토리지 이름 -- 요약(useUsageStorages)을 못 받았을 때만 쓰는 대체 원천(enabled): 탭은 등록
// 목록 ∪ 이 이름들로 그리고 대상 수는 모름으로 둔다.
export const useScanStorages = (enabled = true) =>
  useQuery({ queryKey: ["usage-scan-storages"],
             queryFn: () => apiGet<string[]>("/api/admin/usage/scan-storages"), staleTime: 60_000, enabled });

// 전체 내보내기(CSV 버튼): 화면 상태가 아니라 버튼을 누를 때 한 번 -- 필터·정렬은 목록과 같다(지금 탭의 스토리지만). 새
// 엔드포인트(/targets/export)는 적용 필터 메아리가 없거나 다르면 실패다(목록과 같은 배포 틈 판정 -- CSV 가 조용히 무필터가
// 되지 않게).
export async function fetchUsageExport(f: TargetFilter): Promise<UsageExport> {
  const body = await getNew(`/api/admin/usage/targets/export?${targetQuery(f)}`);
  if (!isObj(body) || !Array.isArray(body.rows)) throw new UsageContractError("unknown_api");
  if (!echoMatches(body.filters, f.storage, f.attr, f.q)) throw new UsageContractError("filter_mismatch");
  const out = body as unknown as UsageExport;
  return { ...out, matched_count: typeof out.matched_count === "number" ? out.matched_count : null, capped: out.capped === true };
}

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
