import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import { sendWithSealedSecret } from "../../lib/passwordTransport";
import type {
  AccessResponse, ClusterBody, ClusterSummary, K8sOverview, K8sSettings, K8sSettingsBody, RunsFilter, ScMapBody,
  StorageClassRow, SyncRun, UserVolumesResponse, VolumesQuery, VolumesResponse,
} from "./types";

// k8s 연동 관리자 화면의 react-query 훅(설계 §5). 조회는 require_admin, 등록·토큰·주소·호출 노드·주기 변경은 세션 관리자
// 전용(require_session_admin — 공유 Bearer 토큰으로는 403)이고 `:sync` 는 스크립트에서도 부를 수 있다. 권한 차단은 서버 몫이고
// 화면은 서버 사유 코드를 reasonText 로 보일 뿐이다.

// 클러스터 토큰은 비밀번호와 같은 봉인 통로로만 보낸다(CLAUDE.md 「비밀번호는 평문으로 저장·전송·에코하지 않는다」). 용도는 서버
// password_transport.PURPOSES 의 "k8s_cluster_token", AAD 의 사용자 자리는 **클러스터 이름**(다른 클러스터 등록으로의 재사용 차단).
export const K8S_TOKEN_PURPOSE = "k8s_cluster_token" as const;
export const K8S_TOKEN_FIELD = "token_enc";

export const K8S_KEY = ["k8s"] as const;
export const OVERVIEW_KEY = ["k8s", "overview"] as const;

/** 개요 폴링 간격: 평소 10초, 동기화 중인 클러스터가 있으면 3초(눌러 놓고 결과를 기다리는 동안 화면이 따라오게). */
export const OVERVIEW_POLL_MS = 10_000;
export const OVERVIEW_SYNCING_POLL_MS = 3_000;
export function overviewPollMs(data: K8sOverview | undefined): number {
  const clusters = Array.isArray(data?.clusters) ? data!.clusters : [];
  return clusters.some((c) => c.status === "syncing") ? OVERVIEW_SYNCING_POLL_MS : OVERVIEW_POLL_MS;
}

export const useK8sOverview = () =>
  useQuery({
    queryKey: OVERVIEW_KEY,
    queryFn: () => apiGet<K8sOverview>("/api/admin/k8s/overview"),
    refetchInterval: (q) => overviewPollMs(q.state.data as K8sOverview | undefined),
  });

/** 필터용 클러스터 목록(폴링 없음 — 동기화 이력의 클러스터 선택지). */
export const useK8sClusters = () =>
  useQuery({ queryKey: ["k8s", "clusters"], queryFn: () => apiGet<ClusterSummary[]>("/api/admin/k8s/clusters") });

// 클러스터·설정이 바뀌면 k8s 화면의 모든 조회(개요·StorageClass 예시·PV 탐색·권한·이력)가 낡는다 -- 접두 무효화.
const invalidateAll = (qc: ReturnType<typeof useQueryClient>) => qc.invalidateQueries({ queryKey: K8S_KEY });

export const useSaveK8sSettings = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: K8sSettingsBody) => apiSend<K8sSettings>("PUT", "/api/admin/k8s/settings", b),
    onSuccess: () => invalidateAll(qc),
  });
};

export interface SaveClusterArgs {
  mode: "create" | "edit";
  /** 편집 대상의 원래 이름(경로) — 이름은 바꾸지 않는다. */
  original?: string;
  body: ClusterBody;
  /** 새 토큰 평문(쓰기 전용). 비면 토큰을 싣지 않는다(편집 = 지금 토큰 유지). 와이어에는 봉인(token_enc)으로만 실린다. */
  token: string;
}

export function clusterPath(name: string): string {
  return `/api/admin/k8s/clusters/${encodeURIComponent(name)}`;
}

export const useSaveCluster = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ mode, original, body, token }: SaveClusterArgs) => {
      const method = mode === "create" ? "POST" : "PUT";
      const path = mode === "create" ? "/api/admin/k8s/clusters" : clusterPath(original ?? body.name);
      const t = token.trim();
      if (t !== "") {
        return sendWithSealedSecret<ClusterSummary>(method, path, K8S_TOKEN_PURPOSE, body.name, K8S_TOKEN_FIELD, t,
                                                    body as unknown as Record<string, unknown>);
      }
      return apiSend<ClusterSummary>(method, path, body);
    },
    onSuccess: () => invalidateAll(qc),
  });
};

export const useDeleteCluster = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => apiSend<void>("DELETE", clusterPath(name)),
    onSuccess: () => invalidateAll(qc),
  });
};

/** 「지금 동기화」 — sync_requested_at = now. 다음 claim 에서 호출 노드가 가져간다(개요는 3초 폴링으로 따라간다). */
export const useSyncCluster = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => apiSend<unknown>("POST", `${clusterPath(name)}:sync`),
    onSettled: () => qc.invalidateQueries({ queryKey: OVERVIEW_KEY }),
  });
};

/** 「모두 지금 동기화」 — 고른 클러스터들을 차례로 요청한다(하나가 실패해도 나머지는 계속, 실패한 이름을 돌려준다). */
export const useSyncAllClusters = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (names: string[]) => {
      const failed: string[] = [];
      for (const n of names) {
        try { await apiSend<unknown>("POST", `${clusterPath(n)}:sync`); } catch { failed.push(n); }
      }
      return { requested: names.length - failed.length, failed };
    },
    onSettled: () => qc.invalidateQueries({ queryKey: OVERVIEW_KEY }),
  });
};

/** enabled = false 면 묻지 않는다(스토리지 수정 창의 「연결된 StorageClass」 — 창을 열었을 때만). */
export const useStorageClasses = (enabled = true) =>
  useQuery({ queryKey: ["k8s", "storage-classes"], enabled,
             queryFn: () => apiGet<StorageClassRow[]>("/api/admin/k8s/storage-classes") });

export const useSaveScMap = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ storageClass, body }: { storageClass: string; body: ScMapBody }) =>
      apiSend<unknown>("PUT", `/api/admin/k8s/storage-classes/${encodeURIComponent(storageClass)}`, body),
    onSuccess: () => invalidateAll(qc),
  });
};

/** PV 탐색 한 번에 받는 상한 — 넘으면 서버가 truncated 를 켠다(화면은 「필터를 좁히세요」). */
export const VOLUMES_LIMIT = 200;

export function volumesUrl(q: VolumesQuery): string {
  const p = new URLSearchParams();
  for (const k of ["cluster", "namespace", "storage_class", "q", "pvc", "user"] as const) {
    const v = q[k];
    if (typeof v === "string" && v !== "") p.set(k, v);
  }
  p.set("limit", String(q.limit ?? VOLUMES_LIMIT));
  return `/api/admin/k8s/volumes?${p.toString()}`;
}

/** enabled = false 면 묻지 않는다(단일 작업 화면 -- 면제 관리자의 검색·사용자 지정에서만 부른다). */
export const useK8sVolumes = (q: VolumesQuery, enabled = true) =>
  useQuery({ queryKey: ["k8s", "volumes", q], queryFn: () => apiGet<VolumesResponse>(volumesUrl(q)),
             placeholderData: (prev) => prev, enabled });

export const useK8sAccess = (q: string, enabled = true) =>
  useQuery({
    queryKey: ["k8s", "access", q], enabled,
    queryFn: () => apiGet<AccessResponse>(
      q === "" ? "/api/admin/k8s/access" : `/api/admin/k8s/access?${new URLSearchParams({ q }).toString()}`),
    placeholderData: (prev) => prev,
  });

/** 단일 작업 화면의 PV 고르기(설계 §5 GET /api/user/k8s/volumes, require_user) -- 자기 맵핑 범위(면제자는 전체). 서버가
 *  namespaces(권한 namespace, PVC 0개 포함)를 함께 줘서 「권한 namespace 없음」과 「PV 없음」을 가른다. */
export const USER_VOLUMES_KEY = ["k8s", "user-volumes"] as const;
export const useUserK8sVolumes = (enabled: boolean) =>
  useQuery({ queryKey: USER_VOLUMES_KEY, enabled,
             queryFn: () => apiGet<UserVolumesResponse>("/api/user/k8s/volumes") });

/** 동기화 이력 한 쪽 — 다음 쪽은 마지막 행의 seq 를 before 로(키셋 커서: 보는 동안 새 run 이 위에 쌓여도 경계가 밀리지 않는다). */
export const RUNS_PAGE_SIZE = 50;

export function runsUrl(f: RunsFilter, before?: number): string {
  const p = new URLSearchParams();
  if (f.cluster) p.set("cluster", f.cluster);
  if (f.node) p.set("node", f.node);
  if (before !== undefined) p.set("before", String(before));
  p.set("limit", String(RUNS_PAGE_SIZE));
  return `/api/admin/k8s/runs?${p.toString()}`;
}

// 진행 중 run(받는 중·적용 대기)이 첫 쪽에 있으면 5초로 다시 읽는다 -- 끝나면 멈춘다(빌드 이력 관례).
const ACTIVE_RUN = new Set(["claimed", "received"]);

export const useInfiniteSyncRuns = (f: RunsFilter) =>
  useInfiniteQuery({
    queryKey: ["k8s", "runs", f],
    queryFn: ({ pageParam }) => apiGet<SyncRun[]>(runsUrl(f, pageParam)),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => (Array.isArray(last) && last.length === RUNS_PAGE_SIZE
      ? last[last.length - 1].seq : undefined),
    refetchInterval: (q) => {
      const first = q.state.data?.pages[0];
      return Array.isArray(first) && first.some((r) => ACTIVE_RUN.has(r.status)) ? 5000 : false;
    },
  });
