// Thin fetch wrapper for the BFF. credentials:"include" so the session cookie
// rides along (same-origin in prod; proxied same-origin in dev).

export type Role = "user" | "operator";

export interface User {
  username: string;
  role: Role;
  method: "local" | "ad";
  dummy?: boolean;
}

export interface OverviewSection {
  key: string;
  title: string;
  status: string;
}

export interface Overview {
  role: Role;
  username: string;
  sections: OverviewSection[];
}

// --- storage mapping (storage inventory) -------------------------------

export interface Readiness {
  resource_management?: string;
  data_management?: string;
  inventory?: string;
  // CSI (k8s namespace-quota) mappings: ResourceQuota mutation transport+permission
  // axis (Ready/Failed/Unknown), replacing RM/DM agent evidence for those mappings.
  kubernetes_mutation?: string;
}

// CSI mutation transport probe result (kubectl/ssh-kubectl reachability + can-i).
export interface MutationObserved {
  mode?: string | null;
  control_host?: string | null;
  reachable?: boolean;
  permissions?: {
    create?: boolean | null;
    patch?: boolean | null;
    delete?: boolean | null;
  };
  can_mutate?: boolean;
  detail?: string | null;
}

export interface SanityCheck {
  name: string;
  status: string;
}
export interface SanityCode {
  code: string;
  message: string;
}

export interface SanityResult {
  status?: string;
  checked_at?: string;
  checks?: SanityCheck[];
  errors?: SanityCode[];
  warnings?: SanityCode[];
  readiness?: Readiness;
  kubernetes_observed?: {
    cluster_name?: string | null;
    provisioner?: string | null;
    storage_class_exists?: boolean;
    storage_class_name?: string | null;
  };
  agent_observed?: {
    fresh_reports?: number;
    stale_reports?: number;
    rm_readiness?: string;
    dm_readiness?: string;
    rm_candidates?: unknown[];
    dm_candidates?: unknown[];
  };
  mutation_observed?: MutationObserved;
  [k: string]: unknown;
}

export type SanityStatus = "Ready" | "Degraded" | "Unknown" | "Failed" | string;

export interface StorageMapping {
  storage_name: string;
  backend_template: Record<string, unknown>;
  cluster_name: string | null;
  storage_class_name: string | null;
  version: number;
  sanity_status: SanityStatus;
  sanity_result?: SanityResult;
  sanity_checked_at?: string | null;
  readiness?: Readiness;
  disabled_at?: string | null;
  disabled_reason?: string | null;
  updated_by?: string | null;
  updated_at?: string | null;
}

// Writable subset (mirrors DMS StorageMappingInput minus server-computed fields).
export interface StorageMappingPayload {
  storage_name: string;
  backend_template: Record<string, unknown>;
  cluster_name: string | null;
  storage_class_name: string | null;
  version: number;
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      const body = await res.json();
      detail = body?.detail ?? body;
    } catch {
      /* ignore non-JSON bodies */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const auth = {
  me: () => request<{ user: User }>("/api/auth/me"),
  login: (username: string, password: string) =>
    request<{ user: User }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  loginAd: () =>
    request<{ user: User }>("/api/auth/login/ad", { method: "POST" }),
  logout: () => request<{ status: string }>("/api/auth/logout", { method: "POST" }),
};

export const userApi = {
  overview: () => request<Overview>("/api/user/overview"),
};

// --- data backup (DM sync batches) -------------------------------------

export interface BackupBatch {
  id: string;
  name: string;
  status: string; // draft|previewing|previewed|running|done|cancelled
  delete_enabled: boolean;
  options: Record<string, unknown>;
  requester_id: string;
  created_by?: string | null;
  note?: string | null;
  created_at?: string;
  updated_at?: string;
  request_count?: number;
  succeeded_count?: number;
  failed_count?: number;
  state_counts?: Record<string, number>;
  preview_totals?: { files: number; bytes: number };
}

export interface BackupPreview {
  files?: number | null;
  dirs?: number | null;
  bytes?: number | null;
  errors?: number | null;
  tool?: string | null;
}

export interface BackupRequest {
  id: number;
  batch_id: string;
  src_storage: string;
  src_path: string;
  dst_storage: string;
  dst_path: string;
  state: string;
  dms_job_id?: string | null;
  fingerprint?: string | null;
  preview?: BackupPreview | null;
  error?: string | null;
  updated_at?: string;
}

export interface BackupRequestInput {
  src_storage: string;
  src_path: string;
  dst_storage: string;
  dst_path: string;
}

export interface BatchCreateInput {
  name: string;
  delete_enabled: boolean;
  options?: Record<string, unknown>;
  note?: string | null;
  requests?: BackupRequestInput[];
}

// --- dashboard ---------------------------------------------------------

export interface Section<T> { data: T | null; error: string | null; }

export interface DashboardSummary {
  control_state: Section<{
    maintenance_mode: boolean; drain_mode: boolean;
    scheduling_blocked: boolean; reason: string; changed_at?: string;
  }>;
  work_summary: Section<{
    plans: { total_active: number; by_status: Record<string, number> };
    runs: {
      total_active: number; by_state: Record<string, number>;
      by_worker_id: Record<string, number>;
      lease_expiring_soon: number; stale_or_recovery: number;
    };
    requests: { action_required: number };
  }>;
  data_jobs: Section<{
    total: number; active_total: number;
    by_state: Record<string, number>; by_operation: Record<string, number>;
  }>;
  nodes: Section<{
    fresh: number; stale: number;
    by_role: Record<string, { fresh: number; stale: number }>;
  }>;
}

export interface AgentReport {
  report_id: string; cluster_name: string; node_name: string;
  worker_role: string; freshness_status: string; reported_at?: string;
  capability_summary?: {
    mounts?: string[]; tools?: string[]; csi_drivers?: string[];
    credential_count?: number;
  };
  os_metrics?: {
    cpu?: { percent?: number; cores?: number };
    memory?: { total_kb?: number; available_kb?: number; used_pct?: number | null };
    load?: { load1?: number; load5?: number; load15?: number };
    disk?: { path?: string; total_gb?: number; used_pct?: number | null };
  };
}

export interface RunRow {
  run_id: string; worker_id?: string; worker_role?: string; state: string;
  lease_seconds_remaining?: number; lease_expiring_soon?: boolean;
  resource_key?: string;
}

export interface DashRequest {
  job_id: string; operation: string; storage_name: string; state: string;
  selected_tool?: string | null; updated_at?: string;
}

export interface AttentionItem { issue_type: string; [k: string]: unknown; }

export interface ControlHost {
  storage_name: string;
  cluster_name: string | null;
  backend_type: string;
  sanity_status?: string;
  mode?: string | null;
  control_host?: string | null;
  reachable?: boolean;
  can_mutate?: boolean;
  permissions?: { create?: boolean | null; patch?: boolean | null; delete?: boolean | null };
  detail?: string | null;
}

const SM = "/api/operator/storage-mappings";
const BK = "/api/operator/backup/batches";

export const operatorApi = {
  overview: () => request<Overview>("/api/operator/overview"),
  storage: {
    list: (clusterName?: string) =>
      request<StorageMapping[]>(
        clusterName ? `${SM}?cluster_name=${encodeURIComponent(clusterName)}` : SM,
      ),
    get: (name: string) =>
      request<StorageMapping>(`${SM}/${encodeURIComponent(name)}`),
    create: (payload: StorageMappingPayload) =>
      request<{ storage_name: string; status: string; mapping: StorageMapping }>(
        SM,
        { method: "POST", body: JSON.stringify(payload) },
      ),
    update: (name: string, payload: StorageMappingPayload) =>
      request<{ storage_name: string; status: string; mapping: StorageMapping }>(
        `${SM}/${encodeURIComponent(name)}`,
        { method: "PATCH", body: JSON.stringify(payload) },
      ),
    check: (name: string) =>
      request<{ storage_name: string; status: string; mapping: StorageMapping }>(
        `${SM}/${encodeURIComponent(name)}/check`,
        { method: "POST" },
      ),
    remove: (name: string) =>
      request<{ storage_name: string; deleted: boolean }>(
        `${SM}/${encodeURIComponent(name)}`,
        { method: "DELETE" },
      ),
  },
  backup: {
    list: () => request<BackupBatch[]>(BK),
    get: (id: string) => request<BackupBatch>(`${BK}/${encodeURIComponent(id)}`),
    create: (payload: BatchCreateInput) =>
      request<{ id: string; added: number }>(BK, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    remove: (id: string) =>
      request<{ id: string; deleted: boolean }>(
        `${BK}/${encodeURIComponent(id)}`,
        { method: "DELETE" },
      ),
    addRequests: (id: string, requests: BackupRequestInput[]) =>
      request<{ id: string; added: number }>(
        `${BK}/${encodeURIComponent(id)}/requests`,
        { method: "POST", body: JSON.stringify(requests) },
      ),
    requests: (id: string, opts?: { state?: string; limit?: number; offset?: number }) => {
      const q = new URLSearchParams();
      if (opts?.state) q.set("state", opts.state);
      if (opts?.limit != null) q.set("limit", String(opts.limit));
      if (opts?.offset != null) q.set("offset", String(opts.offset));
      const qs = q.toString();
      return request<BackupRequest[]>(
        `${BK}/${encodeURIComponent(id)}/requests${qs ? `?${qs}` : ""}`,
      );
    },
    preview: (id: string) =>
      request<{ id: string; status: string }>(
        `${BK}/${encodeURIComponent(id)}:preview`,
        { method: "POST" },
      ),
    approve: (id: string) =>
      request<{ id: string; status: string; to_run: number }>(
        `${BK}/${encodeURIComponent(id)}:approve`,
        { method: "POST" },
      ),
    cancel: (id: string) =>
      request<{ id: string; status: string; dms_cancelled: number }>(
        `${BK}/${encodeURIComponent(id)}:cancel`,
        { method: "POST" },
      ),
  },
  dashboard: {
    summary: () => request<DashboardSummary>("/api/operator/dashboard/summary"),
    nodes: (freshness?: string) =>
      request<AgentReport[]>(
        `/api/operator/dashboard/nodes${freshness ? `?freshness=${encodeURIComponent(freshness)}` : ""}`,
      ),
    runs: () =>
      request<{ active: Section<RunRow[]>; stale: Section<RunRow[]> }>(
        "/api/operator/dashboard/runs",
      ),
    requests: (opts?: { state?: string; operation?: string; storage_name?: string; limit?: number }) => {
      const q = new URLSearchParams();
      if (opts?.state) q.set("state", opts.state);
      if (opts?.operation) q.set("operation", opts.operation);
      if (opts?.storage_name) q.set("storage_name", opts.storage_name);
      if (opts?.limit != null) q.set("limit", String(opts.limit));
      const qs = q.toString();
      return request<DashRequest[]>(`/api/operator/dashboard/requests${qs ? `?${qs}` : ""}`);
    },
    attention: () =>
      request<AttentionItem[]>("/api/operator/dashboard/attention"),
    controlHosts: () =>
      request<ControlHost[]>("/api/operator/dashboard/control-hosts"),
  },
};
