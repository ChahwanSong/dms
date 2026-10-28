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
/** check = 보조 연결 확인(2026-10-28 -- secondary 전용 1회 run, 연결 실패는 클러스터 상태를 바꾸지 않음). */
export type RunTrigger = "schedule" | "manual" | "failover" | "check";
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

/** 호출 노드 × 클러스터 한 칸(repositories.k8s.node_run_stats) — 그 노드가 그 클러스터의 지금 설정(등록분·config_rev — 주소·
 *  TLS·토큰을 바꾸면 새 세대)에 낸 마지막 run 의 연결 판정(적용 실패는 노드가 닿았으니 ok). last_status null = 호출 기록 없음
 *  (부른 적 없거나, 설정을 바꾼 뒤 아직이거나, 보존 정리가 지웠다 — 모름, 실패가 아니다). */
export type CallerLinkStatus = "ok" | "failed" | "running";
export interface CallerClusterLink {
  cluster: string; last_ok_at: string | null; last_fail_at: string | null; last_reason: string | null;
  last_trigger: RunTrigger | string | null; last_status: CallerLinkStatus | string | null;
}

/** 개요의 호출 노드 한 줄 — 에이전트 보고(능력 선언 k8s_sync)와 k8s_sync_runs 집계. */
export interface CallerNode {
  node_name: string; role: NodeRole | null; fresh: boolean; capable: boolean;
  reported_at: string | null; internal_ip: string | null;
  last_claim_at: string | null; last_ok_at: string | null; last_fail_at: string | null; last_reason: string | null;
  serving: string[];
  /** 에이전트 보고가 한 번이라도 있나(지정만 하고 보고 없는 노드는 false). 옵션 = 픽스처·옛 응답. */
  reported?: boolean;
  // 2026-10-28 -- 옵션 = 옛 응답(이 칸들 이전 API). 마지막 실패의 클러스터·계기, 클러스터별 연결, 보조 연결 확인 대기.
  last_fail_cluster?: string | null; last_fail_trigger?: RunTrigger | string | null;
  /** 이 노드가 마지막 실패의 클러스터에 낸 가장 최근 성공(설정 세대 무관 — 「마지막 실패」가 그 뒤 해결됐나를 재는 값;
   *  k8sTerms.failureResolved). null = 그 클러스터에 성공한 기록 없음. */
  last_fail_cluster_ok_at?: string | null;
  clusters?: CallerClusterLink[] | null;
  /** 이 노드가 secondary 일 때 아직 가져가지 않은 보조 연결 확인 요청의 클러스터(없으면 []). */
  check_pending?: string[] | null;
}

export interface ClusterCounts {
  storage_classes: number | null; pvs: number | null; pvcs: number | null; pvcs_bound: number | null;
  namespaces: number | null; bindings: number | null; recognized_bindings: number | null; users: number | null;
}

/** run 경고: 서버(routes_agent._warnings)는 {phase, reason, http_status} — phase 는 목록의 화면 이름(storageclasses·
 *  persistentvolumes …). 사유 코드 문자열·다른 객체가 와도 화면이 죽지 않게 넓게 받는다. */
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
  // 보조 연결 확인 요청 시각(2026-10-28 -- secondary 가 가져가면 null). 옵션 = 옛 응답.
  check_requested_at?: string | null;
}

export interface K8sTotals {
  clusters: number | null; namespaces: number | null; pvcs: number | null; bindings: number | null; users: number | null;
}

/** 가입이 신원을 증명하는가(routes_k8s._signup_guard): mail = 인증번호 메일 실제 발송, stub = 코드 에코(개발·테스트베드),
 *  open = 인증 꺼짐. k8s 모드는 계정 아이디가 곧 RoleBinding 의 ID 라 stub·open 이면 남의 ID 선점이 가능하다. */
export type SignupGuard = "mail" | "stub" | "open";

export interface K8sOverview {
  now: string; settings: K8sSettings; nodes: CallerNode[]; clusters: ClusterSummary[]; totals: K8sTotals;
  // 옵션 = 옛 응답(k3 이전 API)
  signup_guard?: SignupGuard | string;
}

/** 클러스터 등록·편집 바디(토큰은 여기 없다 — 봉인해 token_enc 로만 싣는다, useK8s.useSaveCluster).
 *  ca_pem: 생략 = 저장된 값 유지(편집), null = 지움(시스템 기본 신뢰·검증 안 함), 문자열 = PEM 또는 base64 PEM. */
export interface ClusterBody {
  name: string; api_server: string; tls_server_name: string | null; ca_pem?: string | null;
  insecure_skip_tls_verify: boolean; enabled: boolean;
}

/** run 변경 요약 한 목록(pvcs·bindings …): 개수 + 이름 상위 50(repositories.k8s._diff_apply). changed = 키는 같고 내용만 바뀐
 *  행(PVC Pending→Bound·재생성, 바인딩 subjects 등). names 는 목록 하나이거나 추가·삭제·바뀜으로 나뉜 객체다. */
export interface ChangeEntry {
  added: number | null; removed: number | null; changed?: number | null;
  names?: string[] | { added?: string[] | null; removed?: string[] | null; changed?: string[] | null } | null;
}

export interface SyncRun {
  seq: number; run_id: string; cluster_name: string; node_name: string | null; role: NodeRole | null;
  trigger: string; status: string; claimed_at: string | null; finished_at: string | null; applied_at: string | null;
  duration_ms: number | null; reason: string | null; phase: string | null; http_status: number | null;
  error: string | null; server_version: string | null;
  counts: Record<string, number | null> | null; warnings: RunWarning[] | null;
  // 목록 값 null = 이번 run 이 그 선택 목록(SC·PV)을 받지 못함(403 등 — 이전 값 유지).
  changes: Record<string, ChangeEntry | null> | null;
}

/** StorageClass 예시 경로(routes_k8s._sample_view): reason = 사유 코드(화면 문구), problem = 영문 진단(보조 표시). */
export interface ScSample {
  namespace: string; pvc: string; volume: string | null;
  root: string | null; abs_root: string | null; reason?: string | null; problem: string | null;
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
  // 목적지 스토리지 기본 소유자 "uid:gid"(2026-10-26 -- repositories.k8s.storage_default_owner). PV 목적지 sync 의 결과 소유
  // (사용자는 늘 이 값). null = 스토리지 없음·PV 종류 아님·저장값이 올바르지 않음(제출하면 409 invalid_storage_owner), 생략 = 옛 응답.
  storage_owner?: string | null;
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

/** pvc = PVC 이름 정확 일치(고른 PV 재해석 — 부분 일치 q 는 상한에 잘릴 수 있다). */
export interface VolumesQuery {
  cluster?: string; namespace?: string; storage_class?: string; q?: string; pvc?: string; user?: string; limit?: number;
}

export interface BindingSubject { kind: string; name: string; namespace?: string | null }

/** 근거 바인딩: 서버가 이름만 주거나 원문(roleRef·subjects)을 함께 줄 수 있다 — 화면은 둘 다 그린다. */
export interface BindingRef {
  name: string; role_kind?: string | null; role_name?: string | null; subjects?: BindingSubject[] | null;
}

/** 서버(routes_k8s.admin_access)는 binding 을 이름 문자열로, 역할 이름·subjects 를 grant 바로 아래에 싣는다. */
export interface AccessGrant {
  cluster: string; namespace: string; access: K8sAccess | string; binding: string | BindingRef;
  role_name?: string | null; subjects?: BindingSubject[] | null;
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
