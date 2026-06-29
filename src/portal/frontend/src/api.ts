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
  priority?: string; // High | Mid | Low (Volcano scheduling)
  node_count?: number | null; // null = 자동 (DMS policy default)
  created_by?: string | null;
  note?: string | null;
  created_at?: string;
  updated_at?: string;
  request_count?: number;
  succeeded_count?: number;
  failed_count?: number;
  cancelled_count?: number;
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

// The metrics block of an execution (DMS result_summary.execution.summary).
// dsync reports file/dir/byte counts; nsync reports process/pod/node counts.
export interface BackupExecSummary {
  file_count?: number | null;
  directory_count?: number | null;
  total_bytes?: number | null;
  error_count?: number | null;
  selected_tool?: string | null;
  process_count?: number | null;
  worker_pod_count?: number | null;
  processes_per_node?: number | null;
  source_node_count?: number | null;
  destination_node_count?: number | null;
  operation?: string | null;
  dry_run?: boolean | null;
  [k: string]: unknown;
}

// What the orchestrator stores on a succeeded request (the DMS
// result_summary.execution block) — metrics are nested under `summary`.
export interface BackupResult {
  state?: string | null;
  summary?: BackupExecSummary | null;
  [k: string]: unknown;
}

export interface BackupRequest {
  id: number;
  batch_id: string;
  src_storage: string;
  src_path: string;
  dst_storage: string;
  dst_path: string;
  state: string;
  dms_request_id?: string | null;
  dms_job_id?: string | null;
  fingerprint?: string | null;
  preview?: BackupPreview | null;
  result?: BackupResult | null;
  error?: string | null;
  updated_at?: string;
}

export interface BackupRequestInput {
  src_storage: string;
  src_path: string;
  dst_storage: string;
  dst_path: string;
}

// DMS worker-node policy defaults (what "자동" resolves to). dsync = same-storage
// backup, nsync = cross-storage backup.
export interface NodePolicy {
  default_worker_nodes: number | null;
  max_worker_nodes: number | null;
}
export interface NodePolicyResp {
  dsync: NodePolicy | null;
  nsync: NodePolicy | null;
}

export interface BatchCreateInput {
  name: string;
  delete_enabled: boolean;
  options?: Record<string, unknown>;
  note?: string | null;
  priority?: string;
  node_count?: number | null; // null = 자동 (DMS policy default)
  requests?: BackupRequestInput[];
}

// Partial edit of a draft batch (only the provided fields change).
export interface BatchUpdateInput {
  name?: string;
  delete_enabled?: boolean;
  options?: Record<string, unknown>;
  note?: string | null;
  priority?: string;
  node_count?: number | null; // null = 자동 (DMS policy default)
}

// --- data scan (DM scan batches) ---------------------------------------

// One bucket of the dscan atime time-histogram (data temperature). Buckets run
// hot (recently accessed, [0d,1d]) → cold (long untouched, [3651d,INF]).
export interface AtimeBucket {
  bucket?: string | null; // e.g. "[31d,90d]"
  min_age_days?: number | null;
  max_age_days?: number | null; // null = open-ended (oldest bucket)
  count?: number | null;
}

// Flat scan result the orchestrator stores on a succeeded request (DMS scan is
// read-only: file/dir/byte/error counts + the resolved scan_root and tool, plus
// the atime data-temperature histogram lifted from the dscan report).
export interface ScanResult {
  file_count?: number | null;
  directory_count?: number | null;
  total_bytes?: number | null;
  error_count?: number | null;
  scan_root?: string | null;
  tool?: string | null;
  atime_histogram?: AtimeBucket[] | null;
  [k: string]: unknown;
}

export interface ScanBatch {
  id: string;
  name: string;
  status: string; // draft|scanning|done|cancelled
  options: Record<string, unknown>;
  requester_id: string;
  priority?: string; // High | Mid | Low (Volcano scheduling)
  node_count?: number | null; // null = 자동 (DMS policy default)
  created_by?: string | null;
  note?: string | null;
  created_at?: string;
  updated_at?: string;
  request_count?: number;
  succeeded_count?: number;
  failed_count?: number;
  cancelled_count?: number;
  state_counts?: Record<string, number>;
  // Sum of the succeeded requests' results (present on GET /batches/{id}).
  result_totals?: {
    file_count: number;
    directory_count: number;
    total_bytes: number;
    error_count: number;
  };
}

export interface ScanRequest {
  id: number;
  batch_id: string;
  storage: string;
  path: string;
  state: string; // registered|held|running|succeeded|failed|cancelled
  dms_request_id?: string | null;
  dms_job_id?: string | null;
  result?: ScanResult | null;
  error?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface ScanRequestInput {
  storage: string;
  path: string;
}

export interface ScanBatchCreateInput {
  name: string;
  options?: Record<string, unknown>;
  note?: string | null;
  priority?: string;
  node_count?: number | null; // null = 자동 (DMS policy default)
  requests?: ScanRequestInput[];
}

// Partial edit of a draft/done batch (only the provided fields change).
export interface ScanBatchUpdateInput {
  name?: string;
  options?: Record<string, unknown>;
  note?: string | null;
  priority?: string;
  node_count?: number | null; // null = 자동 (DMS policy default)
}

// Worker-node policy default for the scan tool (dscan). null = no dscan policy.
export interface ScanNodePolicyResp {
  dscan: NodePolicy | null;
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
  control_hosts: Section<{ total: number; reachable: number; can_mutate: number }>;
  volcano: Section<{
    queues: number; queues_open: number;
    jobs_active: number; jobs_total: number;
    ready: number; total: number;
    components: Record<string, { ready: number; total: number }>;
    has_errors: boolean;
  } | null>;
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

export interface AttentionItem {
  issue_type: string;
  severity?: string;
  category?: "live" | "history";
  fingerprint?: string;
  [k: string]: unknown;
}
export interface DismissedItem {
  fingerprint: string;
  issue_type?: string;
  label?: string;
  reason?: string;
  dismissed_by?: string;
  dismissed_at?: string;
}

export interface VolcanoStatus {
  queues: { name: string; state?: string; running?: number; pending?: number; inqueue?: number }[];
  jobs: {
    name: string; namespace?: string; queue?: string; phase?: string;
    running?: number; pending?: number; succeeded?: number; failed?: number; min_available?: number;
  }[];
  scheduler: { name: string; phase?: string; ready?: boolean | null; restarts?: number }[];
  errors: { queues?: string | null; jobs?: string | null; scheduler?: string | null };
}

// Volcano per-job metrics: windowed throughput/latency + top offenders.
export interface VolStageStat {
  mean: number | null;
  p50: number | null;
  p95: number | null;
  p99: number | null;
  n: number;
}
export interface VolWindow {
  throughput: { completed: number; succeeded: number; failed: number };
  latency: {
    job_to_pod_s: VolStageStat; pod_to_sched_s: VolStageStat;
    sched_to_start_s: VolStageStat; run_s: VolStageStat;
  };
}
// Shared detail for an offender job (shown when a row is expanded).
export interface VolJobCard {
  name: string;
  queue?: string | null;
  phase?: string | null;
  phase_kind?: string | null;
  tool?: string | null;
  requester?: string | null;
  request_id?: string | null;
  data_job_id?: string | null;
  src_storage?: string | null; dst_storage?: string | null;
  src_path?: string | null; dst_path?: string | null;
  scan_storage?: string | null; scan_path?: string | null;
  rm_storage?: string | null; rm_path?: string | null;
  req_pods?: number | null; req_cpu_cores?: number | null; req_mem_bytes?: number | null;
  created_at?: string | null; started_at?: string | null; finished_at?: string | null;
}
export interface VolcanoMetrics {
  windows: Record<string, VolWindow>;
  top: {
    longest_pending: (VolJobCard & { pending_s: number })[];
    longest_running: (VolJobCard & { running_s: number; active: boolean })[];
  };
  error?: string | null;
}

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

// Per-node OS-metric time-series for the worker-node workload graphs.
export interface NodeMetricPoint {
  t?: string;
  v?: number | null;
}
export interface NodeMetrics {
  cluster_name: string;
  node_name: string;
  current: {
    cpu_percent?: number | null;
    cpu_cores?: number | null;
    mem_used_pct?: number | null;
    mem_total_kb?: number | null;
    load1?: number | null;
    disk_used_pct?: number | null;
    reported_at?: string;
  };
  cpu_series: NodeMetricPoint[];
  mem_series: NodeMetricPoint[];
}
export interface NodeMetricsResp {
  nodes: NodeMetrics[];
  window_seconds: number;
}

const SM = "/api/operator/storage-mappings";
const BK = "/api/operator/backup/batches";
const SC = "/api/operator/scan/batches";

export const operatorApi = {
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
    // Worker-node policy defaults, to show what "자동" resolves to in the form.
    nodePolicy: () => request<NodePolicyResp>("/api/operator/backup/node-policy"),
    get: (id: string) => request<BackupBatch>(`${BK}/${encodeURIComponent(id)}`),
    create: (payload: BatchCreateInput) =>
      request<{ id: string; added: number }>(BK, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    update: (id: string, payload: BatchUpdateInput) =>
      request<BackupBatch>(`${BK}/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
    remove: (id: string) =>
      request<{ id: string; deleted: boolean }>(
        `${BK}/${encodeURIComponent(id)}`,
        { method: "DELETE" },
      ),
    // Replace the whole request set (CSV upload / inline editor). Allowed on any
    // non-in-flight batch; the new set is all 'registered' (needs preview).
    replaceRequests: (id: string, requests: BackupRequestInput[]) =>
      request<{ id: string; count: number }>(
        `${BK}/${encodeURIComponent(id)}/requests`,
        { method: "PUT", body: JSON.stringify(requests) },
      ),
    // Append requests (registered) to a non-in-flight batch.
    addRequests: (id: string, requests: BackupRequestInput[]) =>
      request<{ id: string; added: number }>(
        `${BK}/${encodeURIComponent(id)}/requests:add`,
        { method: "POST", body: JSON.stringify(requests) },
      ),
    // Bulk-delete the given requests from a non-in-flight batch (in-flight skipped).
    deleteRequests: (id: string, request_ids: number[]) =>
      request<{ id: string; deleted: number }>(
        `${BK}/${encodeURIComponent(id)}/requests:delete`,
        { method: "POST", body: JSON.stringify({ request_ids }) },
      ),
    // Bulk-cancel the given (non-terminal) requests.
    cancelRequests: (id: string, request_ids: number[]) =>
      request<{ id: string; cancelled: boolean; dms_cancelled: number }>(
        `${BK}/${encodeURIComponent(id)}/requests:cancel`,
        { method: "POST", body: JSON.stringify({ request_ids }) },
      ),
    updateRequest: (id: string, rid: number, req: BackupRequestInput) =>
      request<BackupRequest>(
        `${BK}/${encodeURIComponent(id)}/requests/${rid}`,
        { method: "PATCH", body: JSON.stringify(req) },
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
    // Preview registered requests. Pass request_ids to preview only those (the
    // rest are parked and restored afterwards); omit to preview the whole batch.
    preview: (id: string, opts?: { request_ids?: number[] }) =>
      request<{ id: string; status: string; scoped?: boolean }>(
        `${BK}/${encodeURIComponent(id)}:preview`,
        {
          method: "POST",
          body: opts?.request_ids ? JSON.stringify({ request_ids: opts.request_ids }) : undefined,
        },
      ),
    // Selective approval: pass request_ids to approve a subset (staged); omit to
    // approve all preview_ready. Approves only preview_ready among the given ids.
    approve: (id: string, opts?: { request_ids?: number[] }) =>
      request<{ id: string; status: string; approved: number }>(
        `${BK}/${encodeURIComponent(id)}:approve`,
        { method: "POST", body: opts ? JSON.stringify(opts) : undefined },
      ),
    // Finish a batch: drop undecided preview_ready, complete when nothing pending.
    close: (id: string) =>
      request<{ id: string; status: string; excluded: number }>(
        `${BK}/${encodeURIComponent(id)}:close`,
        { method: "POST" },
      ),
    cancel: (id: string) =>
      request<{ id: string; status: string; dms_cancelled: number }>(
        `${BK}/${encodeURIComponent(id)}:cancel`,
        { method: "POST" },
      ),
    cancelRequest: (id: string, rid: number) =>
      request<{ id: string; request_id: number; cancelled: boolean; dms_cancelled: number }>(
        `${BK}/${encodeURIComponent(id)}/requests/${rid}:cancel`,
        { method: "POST" },
      ),
    // Reset fixable requests to 'registered' for re-preview (retry). Pass
    // failed_only to reset all failed/preview_failed, or request_ids for specific ones.
    resetRequests: (id: string, opts: { request_ids?: number[]; failed_only?: boolean }) =>
      request<{ id: string; reset: number }>(
        `${BK}/${encodeURIComponent(id)}/requests:reset`,
        { method: "POST", body: JSON.stringify(opts) },
      ),
  },
  // Data scan (DMS DM scan). Scan is READ-ONLY: no preview/approve/confirm — a
  // batch goes draft -> scanning -> done via a single :run (or :rescan to re-run
  // everything). Otherwise this mirrors the backup namespace.
  scan: {
    list: () => request<ScanBatch[]>(SC),
    // Worker-node policy default for dscan, to show what "자동" resolves to.
    nodePolicy: () => request<ScanNodePolicyResp>("/api/operator/scan/node-policy"),
    get: (id: string) => request<ScanBatch>(`${SC}/${encodeURIComponent(id)}`),
    create: (payload: ScanBatchCreateInput) =>
      request<{ id: string; added: number }>(SC, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    update: (id: string, payload: ScanBatchUpdateInput) =>
      request<ScanBatch>(`${SC}/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
    remove: (id: string) =>
      request<{ id: string; deleted: boolean }>(
        `${SC}/${encodeURIComponent(id)}`,
        { method: "DELETE" },
      ),
    // Replace the whole request set (CSV upload / inline editor). Allowed on any
    // non-scanning batch; the new set is all 'registered'.
    replaceRequests: (id: string, requests: ScanRequestInput[]) =>
      request<{ id: string; count: number }>(
        `${SC}/${encodeURIComponent(id)}/requests`,
        { method: "PUT", body: JSON.stringify(requests) },
      ),
    // Append requests (registered) to a non-scanning batch.
    addRequests: (id: string, requests: ScanRequestInput[]) =>
      request<{ id: string; added: number }>(
        `${SC}/${encodeURIComponent(id)}/requests:add`,
        { method: "POST", body: JSON.stringify(requests) },
      ),
    // Bulk-delete the given requests from a non-scanning batch (in-flight skipped).
    deleteRequests: (id: string, request_ids: number[]) =>
      request<{ id: string; deleted: number }>(
        `${SC}/${encodeURIComponent(id)}/requests:delete`,
        { method: "POST", body: JSON.stringify({ request_ids }) },
      ),
    // Bulk-cancel the given (non-terminal) requests.
    cancelRequests: (id: string, request_ids: number[]) =>
      request<{ id: string; cancelled: boolean; dms_cancelled: number }>(
        `${SC}/${encodeURIComponent(id)}/requests:cancel`,
        { method: "POST", body: JSON.stringify({ request_ids }) },
      ),
    updateRequest: (id: string, rid: number, req: ScanRequestInput) =>
      request<ScanRequest>(
        `${SC}/${encodeURIComponent(id)}/requests/${rid}`,
        { method: "PATCH", body: JSON.stringify(req) },
      ),
    requests: (id: string, opts?: { state?: string; limit?: number; offset?: number }) => {
      const q = new URLSearchParams();
      if (opts?.state) q.set("state", opts.state);
      if (opts?.limit != null) q.set("limit", String(opts.limit));
      if (opts?.offset != null) q.set("offset", String(opts.offset));
      const qs = q.toString();
      return request<ScanRequest[]>(
        `${SC}/${encodeURIComponent(id)}/requests${qs ? `?${qs}` : ""}`,
      );
    },
    cancelRequest: (id: string, rid: number) =>
      request<{ id: string; request_id: number; cancelled: boolean; dms_cancelled: number }>(
        `${SC}/${encodeURIComponent(id)}/requests/${rid}:cancel`,
        { method: "POST" },
      ),
    // Reset fixable requests to 'registered' for re-run (retry). Pass failed_only
    // to reset all failed, or request_ids for specific ones.
    resetRequests: (id: string, opts: { request_ids?: number[]; failed_only?: boolean }) =>
      request<{ id: string; reset: number }>(
        `${SC}/${encodeURIComponent(id)}/requests:reset`,
        { method: "POST", body: JSON.stringify(opts) },
      ),
    // Run the batch (draft|done -> scanning). Pass request_ids to run only those
    // (the rest are parked as 'held'); omit to run the whole batch.
    run: (id: string, opts?: { request_ids?: number[] }) =>
      request<{ id: string; status: string; scoped: boolean }>(
        `${SC}/${encodeURIComponent(id)}:run`,
        {
          method: "POST",
          body: opts?.request_ids ? JSON.stringify({ request_ids: opts.request_ids }) : undefined,
        },
      ),
    // Re-scan a completed batch: reset ALL terminal requests to 'registered' then
    // run everything (monitoring-growth use case).
    rescan: (id: string) =>
      request<{ id: string; status: string; reset: number }>(
        `${SC}/${encodeURIComponent(id)}:rescan`,
        { method: "POST" },
      ),
    cancel: (id: string) =>
      request<{ id: string; status: string; dms_cancelled: number }>(
        `${SC}/${encodeURIComponent(id)}:cancel`,
        { method: "POST" },
      ),
  },
  dashboard: {
    summary: () => request<DashboardSummary>("/api/operator/dashboard/summary"),
    nodes: (freshness?: string) =>
      request<AgentReport[]>(
        `/api/operator/dashboard/nodes${freshness ? `?freshness=${encodeURIComponent(freshness)}` : ""}`,
      ),
    nodeMetrics: (sinceSeconds?: number) =>
      request<NodeMetricsResp>(
        `/api/operator/dashboard/node-metrics${sinceSeconds ? `?since_seconds=${sinceSeconds}` : ""}`,
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
    dismissedAttention: () =>
      request<DismissedItem[]>("/api/operator/dashboard/attention/dismissed"),
    dismissAttention: (items: { fingerprint: string; issue_type?: string; label?: string; reason?: string }[]) =>
      request<{ dismissed: number }>("/api/operator/dashboard/attention/dismiss", {
        method: "POST",
        body: JSON.stringify({ items }),
      }),
    undismissAttention: (fingerprints: string[]) =>
      request<{ undismissed: number }>("/api/operator/dashboard/attention/undismiss", {
        method: "POST",
        body: JSON.stringify({ fingerprints }),
      }),
    resolveRequest: (requestId: string, resolution: "abandon" | "succeeded", reason: string) =>
      request<{ request_id: string; resolved_to: string }>(
        `/api/operator/dashboard/requests/${encodeURIComponent(requestId)}/resolve`,
        { method: "POST", body: JSON.stringify({ resolution, reason }) },
      ),
    deleteDataJob: (jobId: string) =>
      request<{ job_id: string; status: string }>(
        `/api/operator/dashboard/data-jobs/${encodeURIComponent(jobId)}`,
        { method: "DELETE" },
      ),
    controlHosts: () =>
      request<ControlHost[]>("/api/operator/dashboard/control-hosts"),
    volcano: () =>
      request<VolcanoStatus>("/api/operator/dashboard/volcano"),
    volcanoMetrics: () =>
      request<VolcanoMetrics>("/api/operator/dashboard/volcano-metrics"),
  },
};
