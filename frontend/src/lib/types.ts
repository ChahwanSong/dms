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
export interface RequestDetail extends RequestRow { transitions: Transition[] }
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
  changed_by: string | null;
  changed_at: string | null;
}

export interface ScanPath {
  id: number; username?: string; storage_name: string; path: string; created_at: string;
}
export interface HistogramBucket {
  bucket: string; count?: number; bytes?: number;
  lower_inclusive?: number; upper_inclusive?: number;
  min_age_days?: number; max_age_days?: number;
}
export interface ScanPathStats {
  covered_by: { target: string; exact: boolean };
  generated_at_epoch: number;
  summary: Record<string, number>;
  file_size_histogram: HistogramBucket[];
  time_histograms: Record<string, HistogramBucket[]>;
}
