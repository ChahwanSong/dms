export type Role = "user" | "admin";
export interface Me { actor: string; role: Role }
export interface RequestRow {
  request_id: string; operation: string; requester_id: string; resource_key: string;
  priority: string; state: string; created_at: string; updated_at: string; payload: string;
}
export interface RequestDetail extends RequestRow { transitions: Array<{ state: string; at: string; reason_code?: string | null }> }
export interface DataJob {
  job_id: string; request_id: string; operation: string; state: string;
  reason_code: string | null; preview_fingerprint: string | null;
  preview_expires_at: string | null; result_summary: string | null;
  transitions: Array<{ state: string; at: string; reason_code?: string | null }>;
}
export interface Storage {
  storage_name: string; mount_path: string; backend_type: string;
  enabled: number; status: string; status_detail: string | null;
}
export interface Node { node_name: string; reported_at: string; stale: boolean; report: unknown }
