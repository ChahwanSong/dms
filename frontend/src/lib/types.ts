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
  transitions: Transition[];
}
export interface Storage {
  storage_name: string; mount_path: string; backend_type: string;
  enabled: number; status: string; status_detail: string | null;
}
export interface Node { node_name: string; reported_at: string; fresh: boolean; report: unknown }
