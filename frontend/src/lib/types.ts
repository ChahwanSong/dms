export type Role = "user" | "admin";
export interface Me { actor: string; role: Role }
export interface Transition {
  from_state: string | null; to_state: string;
  reason_code?: string | null; actor?: string; at: string;
}
export interface RequestRow {
  request_id: string; operation: string; requester_id: string; resource_key: string;
  priority: string; state: string; created_at: string; updated_at: string; payload: Record<string, unknown>;
}
// events는 state_transitions가 담지 못하는 것 -- 일어나지 않은 전이 -- 를 담는
// 진단 이벤트다(plan_error/step_error/terminate_failed/terminal_guard_skip/summary_unreadable).
export interface DiagEvent {
  id: number; component: string; severity: string; event_type: string;
  message: string | null; payload: unknown; at: string;
}
export interface RequestDetail extends RequestRow {
  transitions: Transition[];
  // 서버는 표시 상한(100건)보다 하나 더 가져와 잘림 여부를 판별한다 -- 조용한
  // 절단을 피하기 위함(routes_requests.py 참고). 두 필드 다 백엔드 응답이 배열이
  // 아니거나 필드 자체가 없을 수 있어 프론트는 방어적으로 정규화해야 한다.
  events?: DiagEvent[];
  events_truncated?: boolean;
}
export interface DataJob {
  job_id: string; request_id: string; operation: string; state: string;
  reason_code: string | null; preview_fingerprint: string | null;
  preview_expires_at: string | null; result_summary: unknown;
  transitions: Transition[]; artifact_uri: string | null;
  // phase -> 실행 ref("pod/<name>" 등). 로그를 실제로 조회할 수 있는 phase가 정확히
  // 이 키들이다 — 뷰어는 하드코딩된 "preflight"가 아니라 여기에 맞춰 탭을 만든다.
  phase_refs?: Record<string, string> | null;
}
export interface ArtifactEntry { phase: string; name: string; size: number; modified_at: number }
// 목록은 상한(MAX_ENTRIES)이 있어 배열이 아니라 truncated 플래그를 동반한 객체다.
export interface ArtifactList { entries: ArtifactEntry[]; truncated: boolean }
export interface ArtifactFile {
  phase: string; name: string; size: number; truncated: boolean; content: string;
}
export interface JobLogs {
  phase: string; ref: string; entries: { pod: string; log: string | null }[];
}
export interface Storage {
  storage_name: string; mount_path: string; managed_root: string; backend_type: string;
  enabled: number; status: string; status_detail: string | null;
}
export interface AuditEntry {
  id: number; mutation_class: string; operation: string; target_key: string;
  actor: string; before_state: string | null; after_state: string | null; at: string;
}
export interface Node { node_name: string; reported_at: string; fresh: boolean; report: unknown }
export interface Account {
  username: string; role: string; email: string | null;
  disabled: number; created_at: string;
}
export interface NodeMount {
  storage_name: string; mount_path: string; status: string;
  exists?: boolean; is_mountpoint?: boolean; readable?: boolean; reason?: string | null;
}
export interface NodeTool {
  name: string; status: string; path?: string; version?: string; reason?: string | null;
}
export interface NodeDisk { storage_name: string; total_bytes: number; used_bytes: number }
export interface NodeReportBody {
  node_name?: string; probed_at?: string;
  mounts?: NodeMount[]; tools?: NodeTool[];
  os?: { disks?: NodeDisk[] } & Record<string, unknown>;
  identities?: unknown[];
}
export interface NodeInfo {
  node_name: string; reported_at: string; fresh: boolean; report: NodeReportBody;
}
export interface NodeReport { reported_at: string; report: NodeReportBody }
export interface Batch {
  batch_id: string; operation: string; status: string; max_concurrency: number;
  item_count: number; succeeded_count: number; failed_count: number;
  note: string | null; created_at: string;
}
export interface BatchItem {
  seq: number; payload: Record<string, unknown>; status: string;
  request_id: string | null; reason_code: string | null;
}
export interface BatchDetail extends Batch { items: BatchItem[] }
export interface Policy {
  tool: string;
  max_nodes: number;
  procs_per_node: number;
  queue: string;
  default_priority: string;
  max_priority: string;
  preview_timeout_seconds: number | null;
  execution_timeout_seconds: number;
  enabled: number;
  updated_at: string;
  updated_by: string;
}

export interface DenyEntry {
  subject_type: string;
  subject: string;
  reason: string | null;
}

export interface UserStorage { storage_name: string; backend_type: string; status: string }

export interface ControlState {
  maintenance: number;
  drain: number;
  reason: string | null;
  build_node_name: string | null;
  changed_by: string | null;
  changed_at: string | null;
}

export interface Build {
  build_id: string; repo_url: string; git_ref: string; commit_sha: string | null;
  images: string[]; node_name: string; state: string; reason_code: string | null;
  tag: string; created_at: string; finished_at: string | null;
}

export interface ScanPath {
  id: number; username?: string; storage_name: string; path: string; created_at: string;
}
export interface HistogramBucket {
  // 구간 라벨은 서버가 모양 검사를 통과한 것만 넘긴다 — 경로처럼 생긴 라벨은 빠진다.
  bucket?: string; count?: number; bytes?: number;
  lower_inclusive?: number; upper_inclusive?: number;
  min_age_days?: number; max_age_days?: number;
}
export interface ScanPathStats {
  covered_by: { target: string; exact: boolean };
  // 리포트에 생성 시각이 없거나 수치가 아니면 null이다(경로가 섞여 들어오는 걸 막는다).
  generated_at_epoch: number | null;
  summary: Record<string, number>;
  file_size_histogram: HistogramBucket[];
  time_histograms: Record<string, HistogramBucket[]>;
}

// 릴리스(롤아웃). 상태는 Pending → Applying → Applied/Failed 이고 잡/빌드의 종단
// 집합(Succeeded/Failed/...)과 겹치지 않는다 -- isTerminal을 그대로 쓰면 Applied가
// 비종단으로 읽힌다(useReleases.ts의 RELEASE_ACTIVE_STATES 주석 참고).
export interface Release {
  id: number; component: string; image: string; tag: string;
  digest: string | null; state: string; reason_code: string | null;
  // seq(배치 안 적용 순서)는 서버가 응답에서 뺀다 -- 내부 정렬용 컬럼이고, 화면은
  // 이미 서버가 ROLLOUT_ORDER로 정렬해 준 목록을 그대로 그린다.
  actor: string; applied_at: string;
}
export interface ReleaseTarget {
  component: string; kind: string; workload: string; container: string;
  repository: string;
  // 워크로드 읽기(observe)가 실패하면 서버가 null을 준다 -- 화면 전체를 죽이지
  // 않는 강등이므로 프론트도 "—"로 살려 보여준다.
  current_image: string | null;
  tags: string[];
}
// registry_ok=false면 tags가 전부 비어 있고 서버의 태그 존재 검증도 꺼진 상태다.
export interface ReleaseTargets { targets: ReleaseTarget[]; registry_ok: boolean }
export interface Releases { current: Record<string, Release>; history: Release[] }
