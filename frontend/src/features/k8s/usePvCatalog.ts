import { useK8sVolumes, useUserK8sVolumes } from "./useK8s";
import { buildGroups, sameRef } from "./pvLocation";
import type { PvGroup } from "./pvLocation";
import type { ClusterFreshness, PvRef, UserNamespace, VolumeRow } from "./types";

// 단일 작업 화면의 PV 목록 출처(dms-k8s 설계 §7) -- 역할마다 다르다:
//   일반 사용자·목록 밖 관리자(맵핑 범위)       GET /api/user/k8s/volumes  -- 자기 rolebinding namespace 의 PVC + 권한 namespace
//   면제 관리자 + 「권한 기준 사용자」 지정        GET /api/admin/k8s/volumes?user=<id> -- 그 사용자 범위(같은 고르기 흐름)
//   면제 관리자 + 비움                           검색(쪽마다 GET /api/admin/k8s/volumes?q=&limit=50) -- 여기서는 목록을 받지 않는다
// 권한 판정은 서버가 제출 때 다시 한다(k8s_access_denied·raw_path_not_allowed) -- 이 목록은 고르기 편의다.

/** 「권한 기준 사용자」 범위 조회 상한(서버 VOLUME_LIMIT_MAX) -- 넘으면 truncated 로 말한다. */
export const SCOPED_LIMIT = 2000;
/** 면제 관리자의 전체 PV 검색 결과 상한(설계 §7). */
export const SEARCH_LIMIT = 50;

export type PvCatalog =
  | { status: "off" }                                   // ldap 모드 또는 검색 모드(목록을 쓰지 않는다)
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; rows: VolumeRow[]; groups: PvGroup[]; clusters: ClusterFreshness[];
      /** 권한 namespace(사용자 응답만) -- null = 묻지 않음(관리자 범위 조회). */
      namespaces: UserNamespace[] | null; truncated: boolean };

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function usePvCatalog({ k8s, unrestricted, scopeUser }: {
  k8s: boolean; unrestricted: boolean; scopeUser: string;
}): { catalog: PvCatalog; search: boolean } {
  const search = k8s && unrestricted && scopeUser === "";
  const scoped = k8s && unrestricted && scopeUser !== "";
  const own = k8s && !unrestricted;
  const userQ = useUserK8sVolumes(own);
  const scopedQ = useK8sVolumes({ user: scopeUser, limit: SCOPED_LIMIT }, scoped);
  if (!k8s || search) return { catalog: { status: "off" }, search };
  if (own) {
    if (userQ.data !== undefined) {
      const d = userQ.data;
      const rows = Array.isArray(d.volumes) ? d.volumes : [];
      const namespaces = Array.isArray(d.namespaces) ? d.namespaces : null;
      return { catalog: { status: "ready", rows, groups: buildGroups(rows, namespaces),
                          clusters: Array.isArray(d.clusters) ? d.clusters : [], namespaces,
                          truncated: d.truncated === true }, search };
    }
    if (userQ.isError) return { catalog: { status: "error", message: errText(userQ.error) }, search };
    return { catalog: { status: "loading" }, search };
  }
  // 범위 지정 중 사용자를 바꾸면 placeholderData 가 이전 사용자 목록을 잠깐 보인다 -- 그 사이는 「불러오는 중」으로 친다
  // (다른 사람 범위의 PV 를 이 사용자 것처럼 고르지 않게).
  if (scopedQ.data !== undefined && !scopedQ.isPlaceholderData) {
    const d = scopedQ.data;
    const rows = Array.isArray(d.volumes) ? d.volumes : [];
    return { catalog: { status: "ready", rows, groups: buildGroups(rows, null),
                        clusters: Array.isArray(d.clusters) ? d.clusters : [], namespaces: null,
                        truncated: d.truncated === true }, search };
  }
  if (scopedQ.isError) return { catalog: { status: "error", message: errText(scopedQ.error) }, search };
  return { catalog: { status: "loading" }, search };
}

export type Resolved =
  | { status: "none" }                       // PV 를 고르지 않았다(또는 직접 경로 쪽)
  | { status: "loading" }
  | { status: "found"; row: VolumeRow; clusters: ClusterFreshness[] }
  | { status: "missing" }                    // 목록(권한 범위·검색)에서 찾지 못했다 -- 지워졌거나 권한이 회수됐거나 ?pv= 오타
  | { status: "error"; message: string };

/** 고른 PV 한 건의 행(실제 경로·용량·상태). 목록 모드는 목록에서, 검색 모드는 그 PV 만 좁혀 다시 묻는다(검색어가 바뀌어 결과
 *  목록에서 빠져도, ?pv= 로 들어와 아직 검색하지 않았어도 같은 행을 보인다). 훅 개수는 고정(쪽마다 한 번). */
export function useResolvedPv({ active, ref, catalog, search }: {
  active: boolean; ref: PvRef | null; catalog: PvCatalog; search: boolean;
}): Resolved {
  const searching = active && search && ref !== null;
  const q = useK8sVolumes(ref === null ? {} : { cluster: ref.cluster, namespace: ref.namespace, q: ref.pvc,
                                                limit: SEARCH_LIMIT }, searching);
  if (!active || ref === null) return { status: "none" };
  if (search) {
    if (q.data !== undefined && !q.isPlaceholderData) {
      const row = (Array.isArray(q.data.volumes) ? q.data.volumes : []).find((r) => sameRef(r, ref));
      return row === undefined ? { status: "missing" }
        : { status: "found", row, clusters: Array.isArray(q.data.clusters) ? q.data.clusters : [] };
    }
    if (q.isError) return { status: "error", message: errText(q.error) };
    return { status: "loading" };
  }
  if (catalog.status === "ready") {
    const row = catalog.rows.find((r) => sameRef(r, ref));
    return row === undefined ? { status: "missing" } : { status: "found", row, clusters: catalog.clusters };
  }
  if (catalog.status === "error") return { status: "error", message: catalog.message };
  return { status: "loading" };
}
