import { absolutePath, destinationParent } from "../../lib/storagePaths";
import { nsLabel } from "./k8sTerms";
import type { ClusterFreshness, PvRef, UserNamespace, VolumeRow } from "./types";

// 단일 작업 화면의 PV 주소 고르기(dms-k8s 설계 §6·§7) -- 순수 함수만 둔다. 화면(LocationField)과 제출 바디·요약(SubmitJob)이
// 같은 규칙을 읽어 「보이는 것과 보내는 것」이 갈리지 않게 한다.
//
// 흐름: namespace (StorageClass) → PV 이름 → PV 안 경로. 서버가 PV 를 (스토리지, root) 로 해석하고(경로 규칙 = 서버 pv_paths)
// 화면은 그 결과(abs_root)를 「실제 경로」로 보일 뿐이다 -- 벤더 경로 템플릿을 여기서 흉내 내지 않는다(발산 금지).

export type Side = "source" | "destination" | "target";
export type LocMode = "pv" | "path";

/** 한 쪽(소스·목적지·대상)의 위치. PV 와 직접 경로 값을 둘 다 들고 있어 「PV | 직접 경로」를 오가도 입력이 남는다.
 *  group = 고른 「namespace (StorageClass)」(""= 아직 안 고름 -- 하나뿐이거나 PV 가 정해지면 화면이 유도한다). */
export interface Loc {
  mode: LocMode;
  group: string;
  ref: PvRef | null;
  sub: string;
  storage: string;
  path: string;
}

export const emptyLoc = (ref: PvRef | null = null): Loc =>
  ({ mode: "pv", group: "", ref, sub: "", storage: "", path: "" });

/** 쪽별 라벨 -- 한 화면 안에서 서로 부분 문자열이 되지 않게 짓는다(e2e getByLabel 은 부분 일치). sync 화면은 소스·목적지만,
 *  scan·rm 화면은 대상만 그려진다. 직접 경로 라벨(「소스 스토리지」·「대상 경로」 등)은 e2e 접점이라 바꾸지 않는다. */
export const SIDE_LABELS: Record<Side, {
  title: string; group: string; pvc: string; sub: string; search: string; mode: string; storage: string; path: string;
}> = {
  source: { title: "소스", group: "소스 namespace (StorageClass)", pvc: "소스 PV 이름", sub: "소스 PV 안 경로",
            search: "소스 PV 검색", mode: "소스 지정 방식", storage: "소스 스토리지", path: "소스 경로" },
  destination: { title: "목적지", group: "목적지 namespace (StorageClass)", pvc: "목적지 PV 이름",
                 sub: "목적지 PV 안 경로", search: "목적지 PV 검색", mode: "목적지 지정 방식",
                 storage: "목적지 스토리지", path: "목적지 경로" },
  target: { title: "대상", group: "namespace (StorageClass)", pvc: "PV 이름", sub: "PV 안 경로", search: "PV 검색",
            mode: "대상 지정 방식", storage: "스토리지", path: "대상 경로" },
};

export const sameRef = (a: PvRef | null, b: PvRef | null) =>
  a !== null && b !== null && a.cluster === b.cluster && a.namespace === b.namespace && a.pvc === b.pvc;

export const refOf = (r: Pick<VolumeRow, "cluster" | "namespace" | "pvc">): PvRef =>
  ({ cluster: r.cluster, namespace: r.namespace, pvc: r.pvc });

export const rowKey = (r: Pick<VolumeRow, "cluster" | "namespace" | "pvc">) =>
  JSON.stringify([r.cluster, r.namespace, r.pvc]);

/** 「namespace (StorageClass)」 묶음 키 -- 클러스터·namespace·SC 셋(namespace 는 클러스터마다만 유일, SC 는 전 클러스터 유일). */
export const groupKey = (cluster: string, namespace: string, sc: string | null | undefined) =>
  JSON.stringify([cluster, namespace, sc ?? ""]);

export interface PvGroup {
  key: string; cluster: string; namespace: string; sc: string | null; label: string;
  rows: VolumeRow[];
  /** 권한 namespace 인데 PV(PVC)가 하나도 없다 -- 선택지에 보이되 고를 수 없다. */
  empty: boolean;
}

/** volumes(+ 권한 namespace) → 묶음 목록. 클러스터가 여럿이면 라벨 뒤에 「 · 클러스터」를 붙인다(같은 namespace·SC 이름이 두
 *  클러스터에 있을 수 있다). 정렬은 클러스터 → namespace → SC. */
export function buildGroups(volumes: VolumeRow[], namespaces: UserNamespace[] | null): PvGroup[] {
  const clusters = new Set<string>([...volumes.map((v) => v.cluster), ...(namespaces ?? []).map((n) => n.cluster)]);
  const multi = clusters.size > 1;
  const byKey = new Map<string, PvGroup>();
  for (const v of volumes) {
    const key = groupKey(v.cluster, v.namespace, v.storage_class);
    let g = byKey.get(key);
    if (g === undefined) {
      g = { key, cluster: v.cluster, namespace: v.namespace, sc: v.storage_class ?? null,
            label: `${nsLabel(v.namespace, v.storage_class)}${multi ? ` · ${v.cluster}` : ""}`, rows: [], empty: false };
      byKey.set(key, g);
    }
    g.rows.push(v);
  }
  const withRows = new Set(volumes.map((v) => JSON.stringify([v.cluster, v.namespace])));
  for (const n of namespaces ?? []) {
    if (withRows.has(JSON.stringify([n.cluster, n.namespace]))) continue;
    const key = groupKey(n.cluster, n.namespace, null);
    if (byKey.has(key)) continue;
    byKey.set(key, { key, cluster: n.cluster, namespace: n.namespace, sc: null,
                     label: `${n.namespace} — PV 없음${multi ? ` · ${n.cluster}` : ""}`, rows: [], empty: true });
  }
  const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
  return [...byKey.values()].sort((a, b) =>
    cmp(a.cluster, b.cluster) || cmp(a.namespace, b.namespace) || cmp(a.sc ?? "", b.sc ?? ""));
}

/** 고를 수 없는 PV 의 짧은 이유(선택지 꼬리). 판정은 서버(resolve_volume.reason) -- 여기는 말로 옮길 뿐이다. */
export function unselectableText(r: Pick<VolumeRow, "reason" | "phase">): string {
  if (r.reason === "pv_not_bound") return "Bound 아님";
  if (r.reason === "pv_storageclass_unmapped") return "StorageClass 매핑 없음";
  if (r.reason === "pv_path_unresolved") return "경로를 알 수 없음";
  // 매핑 스토리지의 사용 범위(routes_k8s._mark_unusable_storages — 직접 경로 피커와 같은 술어).
  if (r.reason === "storage_disabled") return "비활성 스토리지";
  if (r.reason === "storage_admin_only") return "관리자 전용 스토리지";
  return "고를 수 없음";
}

/** 「용량 · 상태」(+ 고를 수 없으면 「(이유)」). 용량·상태를 모르면 그렇게 쓴다(null ≠ 0). */
export function pvMetaText(r: VolumeRow): string {
  const cap = typeof r.capacity === "string" && r.capacity !== "" ? r.capacity : "용량 모름";
  const phase = typeof r.phase === "string" && r.phase !== "" ? r.phase : "상태 모름";
  return `${cap} · ${phase}${r.selectable ? "" : ` (${unselectableText(r)})`}`;
}

/** PV 이름 선택지 글자: 「models — 1Gi · Bound」. */
export function pvOptionText(r: VolumeRow): string {
  return `${r.pvc} — ${pvMetaText(r)}`;
}

/** PV 안 하위경로를 서버 정규화(posixpath.normpath)처럼 접는다: 빈 성분·'.' 을 지운다. '' = PV 루트 자신. '..' 은 지우지 않는다
 *  (pvSubProblem 이 거부한다). 공백은 그대로(이름의 일부). */
export function normalizeSub(sub: string): string {
  return sub.split("/").filter((p) => p !== "" && p !== ".").join("/");
}

/** 서버가 거부할 하위경로(설계 D9·D12 미러 -- 표시·제출 잠금용, 최종 판정은 서버). '' 는 PV 전체(정상값) -- 단 rm 은 PV 루트
 *  삭제 금지라 '', '.', './' 처럼 정규화해 루트가 되면 막는다(pv_root_rm_forbidden). */
export function pvSubProblem(sub: string, operation: string): string | null {
  if (sub.startsWith("/")) return "\"/\" 로 시작할 수 없습니다 — PV 안의 상대경로로 적으세요";
  if (sub.split("/").some((p) => p === "..")) return "\"..\" 은 쓸 수 없습니다 — PV 밖을 가리킬 수 없습니다";
  if (operation === "rm" && normalizeSub(sub) === "")
    return "PV 전체(루트)는 삭제할 수 없습니다 — PV 안의 하위 경로를 적으세요";
  return null;
}

/** 「실제 경로」 -- 서버가 준 abs_root + 정규화한 하위경로. abs_root 를 모르면 null(지어내지 않는다). */
export function pvAbsPath(r: Pick<VolumeRow, "abs_root"> | null | undefined, sub: string): string | null {
  if (r === null || r === undefined) return null;
  return absolutePath(r.abs_root, normalizeSub(sub));
}

/** sync 목적지가 PV 일 때 「상위 디렉토리」(있어야 하는 곳) -- 하위경로가 비면 PV 루트의 부모다. */
export function pvDestParent(r: Pick<VolumeRow, "abs_root"> | null | undefined, sub: string): string | null {
  if (r === null || r === undefined) return null;
  return destinationParent(r.abs_root, normalizeSub(sub));
}

/** 요약·바디의 사람이 읽는 표기 -- lib/storagePaths.pvLocationText(요청 상세·목록)와 같은 문법: 「PV 이름 — ns (sc) / 하위경로」. */
export function pvSummaryText(ref: PvRef, row: VolumeRow | null | undefined, sub: string): string {
  const s = normalizeSub(sub);
  const sc = row === null || row === undefined ? "?" : row.storage_class;
  return `${ref.pvc} — ${sc === "?" ? `${ref.namespace} (?)` : nsLabel(ref.namespace, sc)} / ${s === "" ? "(PV 전체)" : s}`;
}

/** k8s 이름 규칙의 rolebinding 이름(설계 D5 역방향) -- DMS ID 의 '.' 를 '-' 로. 권한이 없을 때 「무엇을 요청할지」를 말한다. */
export function bindingNameFor(actor: string): string {
  return `${actor.replace(/\./g, "-")}-clusterrole-edit|admin`;
}

/** 계정 아이디 규칙(설계 §6 -- k8s 모드 owner_username 검증과 같다). 숫자만은 거부(2026-10-26 -- 잡 셸이 uid 로 읽는다). */
export const ACCOUNT_ID_RE = /^(?![0-9]+$)[a-z0-9][a-z0-9._-]{0,63}$/;

/** 잡 이미지 시스템 계정 이름(서버 domain.RESERVED_ACCOUNT_IDS 미러 -- 대조 테스트 test_k8s_submit). 실행 신원 별칭과 충돌한다
 *  (root 는 이미지에서도 0:0 이라 충돌하지 않아 제외). */
export const RESERVED_ACCOUNT_IDS: ReadonlySet<string> = new Set([
  "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news", "uucp", "proxy", "www-data",
  "backup", "list", "irc", "nobody", "sshd",
]);

/** 계정 아이디로 쓸 수 있나(형식 + 예약어 제외 -- 서버 domain.valid_owner_username 과 같은 판정). */
export function isAccountId(name: string): boolean {
  return ACCOUNT_ID_RE.test(name) && !RESERVED_ACCOUNT_IDS.has(name);
}

/** ?pv=<cluster>/<ns>/<pvc>(관리자 PV 탐색 「이 PV로 단일 작업」, k8sTerms.pvJobLink) → PvRef. 모양이 틀리면 null. */
export function parsePvParam(raw: string | null): PvRef | null {
  if (raw === null) return null;
  const parts = raw.split("/");
  if (parts.length !== 3) return null;
  try {
    const [cluster, namespace, pvc] = parts.map((p) => decodeURIComponent(p));
    if (cluster === "" || namespace === "" || pvc === "") return null;
    return { cluster, namespace, pvc };
  } catch {
    return null;
  }
}

/** 클러스터 신선도 찾기 -- 「n분 전 기준」. 없으면 null(모름). */
export function freshnessOf(clusters: ClusterFreshness[] | undefined, name: string): ClusterFreshness | null {
  return (clusters ?? []).find((c) => c.name === name) ?? null;
}

/** 스냅숏이 오래됐을 수 있어 경고할 클러스터인가(2026-10-26 사용자 결정 (c) 「막지 않고 경고만」) -- 마지막 성공이 2×주기보다
 *  오래됐거나(stale) 상태가 지연·실패. 한 번도 성공하지 않은 클러스터(synced_at null)는 「모름」이라 서버가 409 로 막는 다른 경우다. */
export function freshnessWarns(c: ClusterFreshness | null): boolean {
  return c !== null && c.synced_at !== null && (c.stale === true || c.status === "failing" || c.status === "delayed");
}

// ---- 최근 고른 namespace (StorageClass) 기억(브라우저별 편의 -- 실패해도 화면은 그대로) ----------------------------------
const LAST_GROUP_KEY = (side: Side) => `dms.submit.k8s.group.${side}`;

export function readLastGroup(side: Side): string | null {
  try {
    return globalThis.localStorage?.getItem(LAST_GROUP_KEY(side)) ?? null;
  } catch {
    return null;
  }
}

export function writeLastGroup(side: Side, key: string): void {
  try {
    if (key === "") globalThis.localStorage?.removeItem(LAST_GROUP_KEY(side));
    else globalThis.localStorage?.setItem(LAST_GROUP_KEY(side), key);
  } catch {
    // 사생활 보호 모드·차단된 저장소 -- 기억만 못 할 뿐이다.
  }
}

/** 지금 화면에 걸린 묶음: 고른 값 → 고른 PV 의 묶음 → (PV 가 있는 묶음이 하나뿐이면) 그것 → 최근 고른 묶음(목록에 있을 때만). */
export function effectiveGroup(loc: Loc, groups: PvGroup[], row: VolumeRow | null | undefined, last: string | null): string {
  if (loc.group !== "" && groups.some((g) => g.key === loc.group)) return loc.group;
  if (row !== null && row !== undefined) return groupKey(row.cluster, row.namespace, row.storage_class);
  const withRows = groups.filter((g) => !g.empty);
  if (withRows.length === 1) return withRows[0].key;
  if (last !== null && withRows.some((g) => g.key === last)) return last;
  return "";
}
