// k8s 연동 화면의 와이어 타입(dms-k8s 설계 2026-10-25 §5 — routes_k8s). 이 갈래의 타입은 lib/types.ts 가 아니라 여기에 둔다
// (공용 타입 파일은 다른 화면들이 함께 고치는 자리라, k8s 전용 모양이 거기 섞이면 브랜치 사이 cherry-pick 이 서로 걸린다).
//
// 용어(설계 §1): 코드의 pvc = k8s PVC metadata.name(화면 「PV 이름」), volume = PV metadata.name(화면 「볼륨」 — 경로를 만드는 값).
// 서버가 「모름」을 null 로 주는 칸(개수·시각·IP)은 전부 `| null` 이다 — 화면은 null 을 0 이나 실패로 뭉개지 않는다(CLAUDE.md
// 「null(모름) ≠ 실패, 0 은 정상값」).

export type ClusterStatus =
  | "ok" | "syncing" | "delayed" | "failing" | "never" | "no_node" | "disabled" | "token_missing" | "token_unreadable";
export type NodeRole = "primary" | "secondary";
export type RunStatus = "claimed" | "received" | "applied" | "failed" | "expired" | "superseded" | "discarded";
export type RunTrigger = "schedule" | "manual" | "failover";
export type K8sAccess = "admin" | "edit";
export type TlsMode = "ca" | "insecure" | "default";
export type TokenState = "set" | "missing" | "unreadable";
/** 스토리지 종류(설계 D11). 서버 응답에 없거나 null 이면 filesystem 이다. */
export type StorageKind = "filesystem" | "pv" | "both";

export interface K8sSettings {
  primary_node: string | null; secondary_node: string | null;
  interval_seconds: number; poll_seconds: number;
}
export interface K8sSettingsBody { primary_node: string | null; secondary_node: string | null; interval_seconds: number }

/** 개요의 호출 노드 한 줄 — 에이전트 보고(능력 선언 k8s_sync)와 k8s_sync_runs 집계. */
export interface CallerNode {
  node_name: string; role: NodeRole | null; fresh: boolean; capable: boolean;
  reported_at: string | null; internal_ip: string | null;
  last_claim_at: string | null; last_ok_at: string | null; last_fail_at: string | null; last_reason: string | null;
  serving: string[];
}

export interface ClusterCounts {
  storage_classes: number | null; pvs: number | null; pvcs: number | null; pvcs_bound: number | null;
  namespaces: number | null; bindings: number | null; recognized_bindings: number | null; users: number | null;
}

/** run 경고: 서버는 사유 코드 문자열을 주지만, 목록 이름 등을 담은 객체가 와도 화면이 죽지 않게 넓게 받는다. */
export type RunWarning = string | Record<string, unknown>;

export interface ClusterLastRun {
  run_id: string; node_name: string | null; role: NodeRole | null; trigger: string; status: string;
  duration_ms: number | null; reason: string | null; warnings: RunWarning[] | null;
}

export interface ClusterSummary {
  name: string; api_server: string; tls_server_name: string | null; tls_mode: TlsMode;
  token: TokenState; token_expires_at: string | null; enabled: boolean; status: ClusterStatus | string;
  last_attempt_at: string | null; last_success_at: string | null; last_reason: string | null; last_error: string | null;
  consecutive_failures: number; server_version: string | null; fingerprint: string | null; duplicate_of: string | null;
  interval_seconds: number; counts: ClusterCounts | null; last_run: ClusterLastRun | null;
}

export interface K8sTotals {
  clusters: number | null; namespaces: number | null; pvcs: number | null; bindings: number | null; users: number | null;
}

export interface K8sOverview {
  now: string; settings: K8sSettings; nodes: CallerNode[]; clusters: ClusterSummary[]; totals: K8sTotals;
}

/** 클러스터 등록·편집 바디(토큰은 여기 없다 — 봉인해 token_enc 로만 싣는다, useK8s.useSaveCluster).
 *  ca_pem: 생략 = 저장된 값 유지(편집), null = 지움(시스템 기본 신뢰·검증 안 함), 문자열 = PEM 또는 base64 PEM. */
export interface ClusterBody {
  name: string; api_server: string; tls_server_name: string | null; ca_pem?: string | null;
  insecure_skip_tls_verify: boolean; enabled: boolean;
}

/** run 변경 요약 한 목록(pvcs·bindings …): 개수 + 이름 상위 50. names 는 목록 하나이거나 추가·삭제로 나뉜 객체다. */
export interface ChangeEntry {
  added: number | null; removed: number | null;
  names?: string[] | { added?: string[] | null; removed?: string[] | null } | null;
}

export interface SyncRun {
  seq: number; run_id: string; cluster_name: string; node_name: string | null; role: NodeRole | null;
  trigger: string; status: string; claimed_at: string | null; finished_at: string | null; applied_at: string | null;
  duration_ms: number | null; reason: string | null; phase: string | null; http_status: number | null;
  error: string | null; server_version: string | null;
  counts: Record<string, number | null> | null; warnings: RunWarning[] | null;
  changes: Record<string, ChangeEntry> | null;
}

export interface ScSample {
  namespace: string; pvc: string; volume: string | null;
  root: string | null; abs_root: string | null; problem: string | null;
}

export interface StorageClassRow {
  cluster: string; name: string; provisioner: string | null; parameters: Record<string, string> | null;
  reclaim_policy: string | null; pvc_count: number | null;
  storage_name: string | null; storage_kind: StorageKind | null;
  path_template: string | null; effective_template: string | null; template_source: "storageclass" | "preset" | null;
  sample: ScSample | null; duplicate_in: string[];
}

export interface ScMapBody { storage_name: string | null; path_template: string | null }

export interface ClusterFreshness { name: string; synced_at: string | null; stale: boolean; status: ClusterStatus | string }

export interface VolumeRow {
  cluster: string; namespace: string; storage_class: string | null; pvc: string; volume: string | null;
  phase: string | null; capacity: string | null; access_modes: string[] | null; created_at: string | null;
  storage_name: string | null; root: string | null; abs_root: string | null;
  selectable: boolean; reason: string | null; access: K8sAccess | null;
  users?: string[];
  // 1단계 백엔드가 더 싣는 칸(routes_k8s → repositories.k8s.resolve_volume). 옵션 = 옛 응답·픽스처.
  uid?: string | null; storage_kind?: StorageKind | null; template?: string | null;
  template_source?: "storageclass" | "preset" | null; problem?: string | null;
}

export interface VolumesResponse { clusters: ClusterFreshness[]; truncated: boolean; volumes: VolumeRow[] }

/** 권한 namespace 한 줄(사용자 응답) -- PVC 가 0개여도 실린다(「권한 namespace 없음」과 「PV 없음」을 가른다). */
export interface UserNamespace { cluster: string; namespace: string; access: K8sAccess | string | null }

/** GET /api/user/k8s/volumes(routes_k8s.user_volumes). unrestricted = 맵핑 면제(관리자 + 전체 PV 권한 + 세션) -- 면제자면
 *  namespaces 는 null(전체), volumes 는 전체. clusters 의 synced_at null = 아직 동기화 안 됨(권한 없음과 다르다). */
export interface UserVolumesResponse {
  clusters: ClusterFreshness[]; unrestricted: boolean; namespaces: UserNamespace[] | null;
  truncated: boolean; volumes: VolumeRow[];
}

/** 제출 바디의 PV 주소(설계 §6): pv | source_pv | destination_pv = {cluster, namespace, pvc}. PV 안 하위경로는 같은 쪽의
 *  target | source | destination 에 싣는다('' = PV 전체 — rm 은 서버가 pv_root_rm_forbidden). 같은 쪽에 storage 와 함께 주면 422. */
export interface PvRef { cluster: string; namespace: string; pvc: string }

export interface VolumesQuery {
  cluster?: string; namespace?: string; storage_class?: string; q?: string; user?: string; limit?: number;
}

export interface BindingSubject { kind: string; name: string; namespace?: string | null }

/** 근거 바인딩: 서버가 이름만 주거나 원문(roleRef·subjects)을 함께 줄 수 있다 — 화면은 둘 다 그린다. */
export interface BindingRef {
  name: string; role_kind?: string | null; role_name?: string | null; subjects?: BindingSubject[] | null;
}

export interface AccessGrant {
  cluster: string; namespace: string; access: K8sAccess | string; binding: string | BindingRef;
  subject_mismatch: boolean | null;
}

export interface AccessUser {
  dms_user: string; account_exists: boolean; namespaces: AccessGrant[]; volume_count: number | null;
}

export interface UnrecognizedBinding {
  cluster: string; namespace: string; name: string; role_kind: string | null; role_name: string | null;
  subjects: BindingSubject[] | null;
}

export interface AccessResponse {
  clusters: ClusterFreshness[]; users: AccessUser[]; unrecognized: UnrecognizedBinding[];
}

export interface RunsFilter { cluster?: string; node?: string }
