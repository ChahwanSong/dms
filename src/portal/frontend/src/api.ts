// Thin fetch wrapper for the BFF. credentials:"include" so the session cookie
// rides along (same-origin in prod; proxied same-origin in dev).

export type Role = "user" | "operator";

export interface User {
  username: string;
  role: Role;
  // Both roles log in with an id/password now; the role comes from which account
  // store matched, never from this field.
  method: "local";
  posix_username?: string;
}

// Public config for the end-user login screen. Lets the SPA render the
// "<id>@<domain> 으로 인증메일이 발송됩니다" hint without hard-coding the domain,
// so switching gmail.com -> the company domain is a config change, not a rebuild.
export interface UserAuthConfig {
  available: boolean;
  email_domain: string;
  code_length: number;
  code_ttl_seconds: number;
  resend_cooldown_seconds: number;
  min_password_len: number;
  signup_enabled: boolean;
  // Which delivery provider the BFF is using (mailer.DELIVERY_*). Never credentials.
  email_delivery: "none" | "log" | "company";
}

export type VerifyPurpose = "register" | "reset";

// Durations are RELATIVE seconds, not absolute timestamps, so a skewed client
// clock cannot make a live code look expired (or vice versa).
export interface RequestCodeResp {
  requested: boolean;
  expires_in: number;
  resend_after: number;
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
  logout: () => request<{ status: string }>("/api/auth/logout", { method: "POST" }),
  // whether the login-screen 계정 만들기 / 비밀번호 재설정 flows are available
  // (PORTAL_ADMIN_TOKEN configured). Public — used to show/hide those tabs.
  accountTokenRequired: () =>
    request<{ available: boolean }>("/api/auth/account-token-required"),
  // create an operator account (login screen, gated by the shared secret token).
  register: (username: string, password: string, token: string) =>
    request<{ registered: string }>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password, token }),
    }),
  // reset ('찾기'/변경) an operator's password (login screen, token-gated).
  resetPassword: (username: string, new_password: string, token: string) =>
    request<{ reset: string }>("/api/auth/reset-password", {
      method: "POST",
      body: JSON.stringify({ username, new_password, token }),
    }),

  // --- end user (self-service, email-verified) ---
  userAuthConfig: () => request<UserAuthConfig>("/api/auth/user/config"),
  loginUser: (username: string, password: string) =>
    request<{ user: User }>("/api/auth/user/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  // No password here on purpose — the server must not hold a credential chosen by
  // whoever requested the code. It is sent only with the code, below.
  requestCode: (purpose: VerifyPurpose, username: string) =>
    request<RequestCodeResp>("/api/auth/user/request-code", {
      method: "POST",
      body: JSON.stringify({ username, purpose }),
    }),
  registerUser: (username: string, password: string, code: string) =>
    request<{ registered: string }>("/api/auth/user/register", {
      method: "POST",
      body: JSON.stringify({ username, password, code }),
    }),
  resetUserPassword: (username: string, new_password: string, code: string) =>
    request<{ reset: string }>("/api/auth/user/reset-password", {
      method: "POST",
      body: JSON.stringify({ username, new_password, code }),
    }),
};

export const userApi = {
  overview: () => request<Overview>("/api/user/overview"),
};

// --- end-user self-service (데이터 Sync · 데이터 스캔) --------------------
// Filesystem-backend storage usable as a user sync/scan endpoint (non-secret).
export interface UserStorage {
  storage_name: string;
  backend_type: string | null;
  filesystem_subtype: "fs-native" | "pv";
  managed_root: string | null;
}

// A registered scan-lookup item + the operator's latest pulled scan result (or
// none — has_result=false → the UI tells the user to ask an operator).
export interface UserScanItem {
  id: number;
  username: string;
  storage: string;
  path: string;
  memo?: string | null;
  created_at?: string;
  updated_at?: string;
  // 'succeeded' | 'failed' | 'running' | 'none' — the latest operator scan's outcome
  // for this (storage, path). has_result is true only when status === 'succeeded'.
  status?: "succeeded" | "failed" | "running" | "none";
  state?: string | null; // raw DMS DataJobState of the latest scan
  error?: string | null; // failure reason when status === 'failed'
  has_result: boolean;
  result: ScanResult | null;
  dms_job_id?: string | null;
  scanned_at?: string | null;
}

export interface UserSyncCreateInput {
  src_storage: string;
  src_path: string;
  dst_storage: string;
  dst_path: string;
  delete_enabled: boolean;
  contents: boolean;
  memo?: string | null;
}

const USY = "/api/user/sync-jobs";
const USC = "/api/user/scan-items";

// User Data Sync — restricted single-item copy (FS↔FS or PVC↔PVC only). Same
// preview→승인→실행 flow as the operator tab; the BFF forces the fixed options.
export const userSyncApi = {
  storages: () => request<UserStorage[]>("/api/user/storages"),
  list: (p: { offset: number; limit: number }) =>
    request<SyncJobListResp>(`${USY}?offset=${p.offset}&limit=${p.limit}`),
  get: (id: number) => request<SyncJob>(`${USY}/${id}`),
  create: (body: UserSyncCreateInput) =>
    request<SyncJob>(USY, { method: "POST", body: JSON.stringify(body) }),
  approve: (id: number) =>
    request<{ id: number; approved: boolean }>(`${USY}/${id}:approve`, { method: "POST" }),
  cancel: (id: number) =>
    request<{ id: number; state: string }>(`${USY}/${id}:cancel`, { method: "POST" }),
  remove: (id: number) =>
    request<{ deleted: number }>(`${USY}/${id}`, { method: "DELETE" }),
  job: (id: number) => request<JobDetail>(`${USY}/${id}/job`),
  logs: (id: number, tail = 400) => request<JobLogs>(`${USY}/${id}/logs?tail=${tail}`),
};

// --- dms-voc (사용자 문의/요청 → 운영자 처리) ----------------------------
export interface Voc {
  id: number;
  username: string;
  category?: string | null;
  title: string;
  body: string;
  status: "open" | "resolved";
  answer?: string | null;
  resolved_by?: string | null;
  resolved_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

const UVOC = "/api/user/voc";
const OVOC = "/api/operator/voc";

// 사용자: VOC 등록/내 목록/미처리 건 회수.
export const userVocApi = {
  categories: () => request<{ categories: string[] }>(`${UVOC}/categories`),
  create: (body: { category?: string | null; title: string; body: string }) =>
    request<Voc>(UVOC, { method: "POST", body: JSON.stringify(body) }),
  list: () => request<{ items: Voc[] }>(UVOC),
  remove: (id: number) =>
    request<{ deleted: number }>(`${UVOC}/${id}`, { method: "DELETE" }),
};

export type VocPeriod = "1m" | "1y" | "all";

// 운영자: 미처리/처리완료 서브탭 목록(기간·정렬·검색·keyset 커서 페이징) +
// 처리(:resolve)/복귀(:reopen)/삭제.
export const opVocApi = {
  list: (p: {
    status: "open" | "resolved";
    period?: VocPeriod;
    order?: "asc" | "desc";
    search?: string;
    // keyset 커서(직전 페이지 마지막 id) — offset 드리프트 없는 무한스크롤.
    cursor?: number | null;
    limit?: number;
  }) => {
    const q = new URLSearchParams({
      status: p.status,
      period: p.period ?? "all",
      order: p.order ?? "desc",
      limit: String(p.limit ?? 100),
    });
    if (p.cursor != null) q.set("cursor", String(p.cursor));
    if (p.search?.trim()) q.set("search", p.search.trim());
    return request<{
      items: Voc[];
      counts: { open: number; resolved: number };
      next_cursor: number | null;
    }>(`${OVOC}?${q.toString()}`);
  },
  resolve: (id: number, answer: string | null) =>
    request<Voc>(`${OVOC}/${id}:resolve`, {
      method: "POST",
      body: JSON.stringify({ answer }),
    }),
  reopen: (id: number) => request<Voc>(`${OVOC}/${id}:reopen`, { method: "POST" }),
  remove: (id: number) =>
    request<{ deleted: number }>(`${OVOC}/${id}`, { method: "DELETE" }),
};

// User Data Scan — READ-ONLY: register (storage,path) items; results are pulled
// from the operator's completed DMS scans (auto-reflected).
export const userScanApi = {
  storages: () => request<UserStorage[]>("/api/user/storages"),
  list: () => request<{ items: UserScanItem[] }>(USC),
  get: (id: number) => request<UserScanItem>(`${USC}/${id}`),
  create: (body: { storage: string; path: string; memo?: string | null }) =>
    request<UserScanItem>(USC, { method: "POST", body: JSON.stringify(body) }),
  updateMemo: (id: number, memo: string | null) =>
    request<UserScanItem>(`${USC}/${id}`, { method: "PATCH", body: JSON.stringify({ memo }) }),
  remove: (id: number) =>
    request<{ deleted: number }>(`${USC}/${id}`, { method: "DELETE" }),
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

// --- data sync (데이터 Sync 탭: one-shot data.sync) --------------------
// preview summary captured at ConfirmPending (files/bytes to copy).
export interface SyncPreview {
  files?: number | null;
  dirs?: number | null;
  bytes?: number | null;
  errors?: number | null;
  tool?: string | null;
}
// A one-shot sync job — carries its own config + DMS tracking (no batch).
export interface SyncJob {
  id: number;
  src_storage: string;
  src_path: string;
  dst_storage: string;
  dst_path: string;
  requester_id: string;
  owner_username?: string | null;
  options?: Record<string, unknown>;
  delete_enabled: boolean;
  priority: string;
  node_count?: number | null;
  memo?: string | null;
  state: string;
  approved?: boolean;
  dms_request_id?: string | null;
  dms_job_id?: string | null;
  fingerprint?: string | null;
  preview?: SyncPreview | null;
  result?: Record<string, unknown> | null;
  error?: string | null;
  created_by?: string | null;
  origin?: string; // 'operator' | 'user' — who submitted it (shared sync_jobs table)
  created_at?: string;
  updated_at?: string;
}
export interface SyncJobListResp {
  items: SyncJob[];
  total: number | null; // grand count on the first page only (infinite scroll)
}
export interface SyncJobCreateInput {
  src_storage: string;
  src_path: string;
  dst_storage: string;
  dst_path: string;
  delete_enabled: boolean;
  options?: Record<string, unknown>;
  priority?: string;
  node_count?: number | null; // null = 자동 (DMS policy default)
  memo?: string | null;
}

// --- data rm (데이터 삭제 탭: one-shot data.rm) ------------------------
// preview summary captured at ConfirmPending (files/bytes that WILL be deleted).
// target_absent = the target path doesn't exist (nothing to delete).
export interface RmPreview {
  files?: number | null;
  dirs?: number | null;
  bytes?: number | null;
  errors?: number | null;
  target_absent?: boolean | null;
  tool?: string | null;
}
// A one-shot rm job — a SINGLE target (no source/destination), no delete flag.
export interface RmJob {
  id: number;
  target_storage: string;
  target_path: string;
  requester_id: string;
  owner_username?: string | null;
  options?: Record<string, unknown>;
  priority: string;
  node_count?: number | null;
  memo?: string | null;
  state: string;
  approved?: boolean;
  dms_request_id?: string | null;
  dms_job_id?: string | null;
  fingerprint?: string | null;
  preview?: RmPreview | null;
  result?: Record<string, unknown> | null;
  error?: string | null;
  created_by?: string | null;
  created_at?: string;
  updated_at?: string;
}
export interface RmJobListResp {
  items: RmJob[];
  total: number | null; // grand count on the first page only (infinite scroll)
}
export interface RmJobCreateInput {
  target_storage: string;
  target_path: string;
  options?: Record<string, unknown>;
  priority?: string;
  node_count?: number | null; // null = 자동 (DMS policy default)
  memo?: string | null;
}
// drm worker-node policy default (what "자동" resolves to).
export interface RmNodePolicyResp {
  drm: NodePolicy | null;
}

// --- data scan (DM scan batches) ---------------------------------------

// One bucket of the dscan atime time-histogram (data temperature). Buckets run
// hot (recently accessed, [0d,1d]) → cold (long untouched, [3651d,INF]); each
// carries the total file CAPACITY (bytes) of files in that access-age band.
export interface AtimeBucket {
  bucket?: string | null; // e.g. "[31d,90d]"
  min_age_days?: number | null;
  max_age_days?: number | null; // null = open-ended (oldest bucket)
  bytes?: number | null; // total file capacity in this band
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
  run_id: string; plan_id?: string; request_id?: string;
  worker_id?: string; executor_id?: string; worker_role?: string; state: string;
  lease_seconds_remaining?: number; lease_expiring_soon?: boolean;
  lease_expires_at?: string; heartbeat_at?: string; started_at?: string;
  // enriched (JOIN plans/requests): what the run is actually doing
  operation_kind?: string; resource_kind?: string; resource_key?: string;
  requester_id?: string; request_status?: string;
  // true if this run's request was hidden via the 조치 필요 dismiss/ack layer.
  _hidden?: boolean;
}

// Runs are single-fetched with a high cap; `truncated` is set only if that cap was
// actually hit (in-flight/attention runs are bounded, so this should never trip).
export interface RunsSection {
  data: RunRow[] | null;
  error: string | null;
  truncated?: boolean;
}
export interface DashRunsResp {
  active: RunsSection;
  stale: RunsSection;
}

export interface DashRequest {
  job_id: string; request_id?: string | null; operation: string; storage_name: string; state: string;
  selected_tool?: string | null; updated_at?: string;
}

// Data-jobs table payload. `jobs` is a CAPPED page (history grows ~10k/day);
// `total` is the exact count from the uncapped COUNT summary (null when the active
// filter combo can't be derived); `truncated` means more rows exist than shown.
export interface DashRequestsResp {
  jobs: DashRequest[];
  total: number | null;
  truncated: boolean;
}

// A full request (any operation/resource_kind) for the 액티비티 요청 뷰.
export interface RequestActivity {
  request_id: string;
  operation: string;
  resource_kind?: string | null;
  resource_key?: string | null;
  status: string;
  requester_id?: string | null;
  actor?: string | null;
  requested_at?: string | null;
  payload_summary?: Record<string, unknown> | null;
  // true if this request was hidden via the 조치 필요 dismiss/ack layer.
  _hidden?: boolean;
}
export interface RequestActivityResp {
  requests: RequestActivity[];
  truncated: boolean;
  hidden_count?: number;
}
// full lifecycle detail for one request (activity row expand).
export interface RequestTransition {
  from_state?: string | null;
  to_state?: string | null;
  reason?: string | null;
  actor?: string | null;
  created_at?: string | null;
}
export interface RequestDetail {
  request?: Record<string, unknown> | null;
  plan?: Record<string, unknown> | null;
  results?: Record<string, unknown>[] | null;
  transitions?: RequestTransition[] | null;
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
  // "ack" = 운영자가 확인·수동 처리함, "dismissed" = 해당없음/숨김
  kind?: "ack" | "dismissed";
  // for an ack: whether it's reflected in DMS server-side (all clients) or is a
  // legacy portal-only ack from before the server-side wiring.
  in_dms?: boolean;
  // captured at dismiss time so a hidden item can still be acted on from the list
  job_id?: string | null;
  request_id?: string | null;
  status?: string | null;
  // the action-required item's own report/updated time (so 처리 내역 shows the
  // report time like 현재 조치/과거 이력, not the admin's dismiss time)
  item_at?: string | null;
  dismissed_by?: string;
  dismissed_at?: string;
}

// Deep-link target: navigate to a section AND focus a specific item (open its
// detail / highlight it). Used by 조치 필요 "상세" → the owning view.
export interface FocusTarget {
  kind: "storage" | "request";
  value: string;
}

export interface DismissPayloadItem {
  fingerprint: string;
  issue_type?: string;
  label?: string;
  reason?: string;
  kind?: "ack" | "dismissed";
  job_id?: string | null;
  request_id?: string | null;
  status?: string | null;
  item_at?: string | null;
}

// Worker-node × filesystem-storage mount-readiness matrix (CSI excluded).
export interface SnmCell {
  status?: string;            // Ready | Missing | Degraded | ...
  mount_path?: string | null;
  readable?: boolean | null;
  writable?: boolean | null;
  is_mountpoint?: boolean | null;
  filesystem_type?: string | null;
  reason?: string | null;
}
export interface SnmStorage {
  storage_name: string;
  backend_type: string;
}
export interface SnmNode {
  node_name: string;
  roles: string[];            // ["RM","DM"]
  freshness?: string | null;  // Fresh | Stale
  reported_at?: string | null;
  cells: Record<string, SnmCell>;  // storage_name -> mount status (absent = 미구성)
}
export interface SnmCluster {
  cluster_name: string;
  storages: SnmStorage[];
  nodes: SnmNode[];
}
export interface StorageNodeMatrix {
  clusters: SnmCluster[];
  errors?: { reports?: string | null; mappings?: string | null };
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
// CSI control hosts are derived from the bounded storage-mapping set; `truncated`
// is set only if that single fetch hit the high cap.
export interface ControlHostsResp {
  items: ControlHost[];
  truncated: boolean;
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

// Request / in-progress-job trend (BFF-sampled). requests = 활성 plan, jobs = 활성
// run, data_jobs = 진행중 데이터잡. Each is a {t, v} series over the window.
export interface TimeSeriesResp {
  requests: NodeMetricPoint[];
  jobs: NodeMetricPoint[];
  data_jobs: NodeMetricPoint[];
  points: number;
  window_seconds: number;
}

// Per-node CPU/mem/load/disk trend (BFF-sampled, 1h–30d) for the 워커 노드 detail tab.
export interface NodeSeries {
  cluster_name: string;
  node_name: string;
  current: {
    cpu_percent?: number | null;
    mem_used_pct?: number | null;
    load1?: number | null;
    disk_used_pct?: number | null;
    rx_rate?: number | null; // bytes/s
    tx_rate?: number | null; // bytes/s
  };
  cpu_series: NodeMetricPoint[];
  mem_series: NodeMetricPoint[];
  load_series: NodeMetricPoint[];
  disk_series: NodeMetricPoint[];
  rx_series: NodeMetricPoint[]; // bandwidth rate bytes/s
  tx_series: NodeMetricPoint[];
}
export interface NodeTimeSeriesResp {
  nodes: NodeSeries[];
  window_seconds: number;
}

// --- per-request live job detail + log tail (backup + scan) ------------

// Live DMS data-job detail (fuller than the portal-stored subset). The BFF
// returns the raw DMS job dict when a job exists, or {available:false, note}
// when the request has no DMS job yet. result_summary nesting varies by tool, so
// it stays an open record the modal renders defensively (incl file_size_histogram).
export interface JobDetail {
  available?: boolean; // false ONLY when there is no DMS job yet
  note?: string;
  state?: string | null;
  selected_tool?: string | null;
  // DMS returns a STRUCTURED ref ({adapter, job_ref}), not a bare string. Keep
  // the union so the modal coerces it instead of rendering an object (React #31).
  volcano_job_ref?: string | { adapter?: string; job_ref?: string } | null;
  artifact_uri?: string | null;
  log_uri?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  preflight_result?: Record<string, unknown> | null;
  result_summary?: Record<string, unknown> | null;
  [k: string]: unknown;
}

// One launcher/worker pod backing a data-job (for the log tail panel).
export interface JobLogPod {
  name?: string;
  node_name?: string | null;
  role?: string | null;
  phase?: string | null;
}

// Tailed launcher-pod logs. available=false (with a note) when there is no pod
// yet (pending), it was GC'd (terminal), or kubectl errored — never an error.
export interface JobLogs {
  job_id?: string;
  available: boolean;
  pods?: JobLogPod[];
  logs?: string;
  note?: string;
}

const SM = "/api/operator/storage-mappings";
const BK = "/api/operator/backup/batches";
const SC = "/api/operator/scan/batches";
const SY = "/api/operator/sync-jobs";
const RM = "/api/operator/rm-jobs";

export interface AgentDaemonSetStatus {
  name: string;
  desired: number;
  updated: number;
  ready: number;
  available: number;
  unavailable: number;
  rolling: boolean;
  restarted_at?: string | null;
  error?: string;
}
export interface AgentRolloutStatus {
  namespace: string;
  daemonsets: AgentDaemonSetStatus[];
}
export interface AgentRolloutResult {
  namespace: string;
  restarted_at: string;
  restarted: string[];
  errors: Record<string, string>;
}

export const operatorApi = {
  // RM·DM agent DaemonSet rollout (restart to re-read storages after a change)
  agents: {
    rolloutStatus: () =>
      request<AgentRolloutStatus>("/api/operator/agents/rollout-status"),
    rolloutRestart: () =>
      request<AgentRolloutResult>("/api/operator/agents/rollout-restart", {
        method: "POST",
      }),
  },
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
    // DMS control cluster name — default for a filesystem mapping's agent cluster.
    controlClusterName: () =>
      request<{ control_cluster_name: string | null }>(
        "/api/operator/control-cluster",
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
    // Bulk-delete batches (collection-level). The server skips active batches
    // (previewing/running) and reports them in `skipped`.
    deleteBatches: (ids: string[]) =>
      request<{ deleted: string[]; skipped: { id: string; reason: string }[] }>(
        `${BK}:delete`,
        { method: "POST", body: JSON.stringify({ batch_ids: ids }) },
      ),
    // Bulk-cancel batches (collection-level). Cancels in-flight DMS requests too;
    // non-cancelable batches are reported in `skipped`.
    cancelBatches: (ids: string[]) =>
      request<{ cancelled: string[]; dms_cancelled: number; skipped: { id: string; reason: string }[] }>(
        `${BK}:cancel`,
        { method: "POST", body: JSON.stringify({ batch_ids: ids }) },
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
    // Re-run a finished batch from scratch: reset ALL terminal requests (incl
    // succeeded) to 'registered' and move the batch to 'previewing'. Sync is
    // destructive, so this re-previews (NOT auto-runs) — the operator re-approves.
    rerun: (id: string) =>
      request<{ id: string; status: string; reset: number }>(
        `${BK}/${encodeURIComponent(id)}:rerun`,
        { method: "POST" },
      ),
    // Live DMS sync-job detail for one request (read-only). {available:false} when
    // the request has no DMS job yet.
    job: (id: string, rid: number) =>
      request<JobDetail>(`${BK}/${encodeURIComponent(id)}/requests/${rid}/job`),
    // Tail the launcher pod logs of a request's DMS job (read-only).
    logs: (id: string, rid: number, tail = 400) =>
      request<JobLogs>(
        `${BK}/${encodeURIComponent(id)}/requests/${rid}/logs?tail=${tail}`,
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
    // Bulk-delete batches (collection-level). The server skips active (scanning)
    // batches and reports them in `skipped`.
    deleteBatches: (ids: string[]) =>
      request<{ deleted: string[]; skipped: { id: string; reason: string }[] }>(
        `${SC}:delete`,
        { method: "POST", body: JSON.stringify({ batch_ids: ids }) },
      ),
    // Bulk-cancel batches (collection-level). Cancels in-flight DMS scan requests
    // too; non-cancelable batches are reported in `skipped`.
    cancelBatches: (ids: string[]) =>
      request<{ cancelled: string[]; dms_cancelled: number; skipped: { id: string; reason: string }[] }>(
        `${SC}:cancel`,
        { method: "POST", body: JSON.stringify({ batch_ids: ids }) },
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
    // Live DMS scan-job detail for one request (read-only). {available:false} when
    // the request has no DMS job yet.
    job: (id: string, rid: number) =>
      request<JobDetail>(`${SC}/${encodeURIComponent(id)}/requests/${rid}/job`),
    // Tail the launcher pod logs of a request's DMS job (read-only).
    logs: (id: string, rid: number, tail = 400) =>
      request<JobLogs>(
        `${SC}/${encodeURIComponent(id)}/requests/${rid}/logs?tail=${tail}`,
      ),
  },
  // Data sync (데이터 Sync 탭). ONE-SHOT data.sync: each job IS the top-level unit
  // (no batch, no re-run). Mutating, so preview -> 승인(approve/confirm) -> execute,
  // driven server-side. The list is newest-first, paginated (infinite scroll).
  sync: {
    // Worker-node policy defaults for dsync/nsync, to show what "자동" resolves to.
    nodePolicy: () => request<NodePolicyResp>(`${SY}/node-policy`),
    list: (opts?: { offset?: number; limit?: number }) => {
      const q = new URLSearchParams();
      if (opts?.offset != null) q.set("offset", String(opts.offset));
      if (opts?.limit != null) q.set("limit", String(opts.limit));
      const qs = q.toString();
      return request<SyncJobListResp>(`${SY}${qs ? `?${qs}` : ""}`);
    },
    get: (id: number) => request<SyncJob>(`${SY}/${id}`),
    create: (payload: SyncJobCreateInput) =>
      request<SyncJob>(SY, { method: "POST", body: JSON.stringify(payload) }),
    // Approve a preview_ready job (the orchestrator then confirms + runs it).
    approve: (id: number) =>
      request<{ id: number; approved: boolean }>(`${SY}/${id}:approve`, { method: "POST" }),
    cancel: (id: number) =>
      request<{ id: number; state: string }>(`${SY}/${id}:cancel`, { method: "POST" }),
    remove: (id: number) =>
      request<{ deleted: number }>(`${SY}/${id}`, { method: "DELETE" }),
    // Live DMS sync-job detail / launcher-pod logs (read-only) for the 상세 modal.
    job: (id: number) => request<JobDetail>(`${SY}/${id}/job`),
    logs: (id: number, tail = 400) =>
      request<JobLogs>(`${SY}/${id}/logs?tail=${tail}`),
  },
  rm: {
    // Worker-node policy default for drm, to show what "자동" resolves to.
    nodePolicy: () => request<RmNodePolicyResp>(`${RM}/node-policy`),
    list: (opts?: { offset?: number; limit?: number }) => {
      const q = new URLSearchParams();
      if (opts?.offset != null) q.set("offset", String(opts.offset));
      if (opts?.limit != null) q.set("limit", String(opts.limit));
      const qs = q.toString();
      return request<RmJobListResp>(`${RM}${qs ? `?${qs}` : ""}`);
    },
    get: (id: number) => request<RmJob>(`${RM}/${id}`),
    create: (payload: RmJobCreateInput) =>
      request<RmJob>(RM, { method: "POST", body: JSON.stringify(payload) }),
    // Approve a preview_ready job (the orchestrator then confirms + runs the delete).
    approve: (id: number) =>
      request<{ id: number; approved: boolean }>(`${RM}/${id}:approve`, { method: "POST" }),
    cancel: (id: number) =>
      request<{ id: number; state: string }>(`${RM}/${id}:cancel`, { method: "POST" }),
    remove: (id: number) =>
      request<{ deleted: number }>(`${RM}/${id}`, { method: "DELETE" }),
    // Live DMS rm-job detail / launcher-pod logs (read-only) for the 상세 modal.
    job: (id: number) => request<JobDetail>(`${RM}/${id}/job`),
    logs: (id: number, tail = 400) =>
      request<JobLogs>(`${RM}/${id}/logs?tail=${tail}`),
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
    timeseries: (sinceSeconds = 86400) =>
      request<TimeSeriesResp>(
        `/api/operator/dashboard/timeseries?since_seconds=${sinceSeconds}`,
      ),
    nodeTimeseries: (sinceSeconds = 21600) =>
      request<NodeTimeSeriesResp>(
        `/api/operator/dashboard/node-timeseries?since_seconds=${sinceSeconds}`,
      ),
    runs: () =>
      request<DashRunsResp>("/api/operator/dashboard/runs"),
    requests: (opts?: { state?: string; operation?: string; storage_name?: string; limit?: number }) => {
      const q = new URLSearchParams();
      if (opts?.state) q.set("state", opts.state);
      if (opts?.operation) q.set("operation", opts.operation);
      if (opts?.storage_name) q.set("storage_name", opts.storage_name);
      if (opts?.limit != null) q.set("limit", String(opts.limit));
      const qs = q.toString();
      return request<DashRequestsResp>(`/api/operator/dashboard/requests${qs ? `?${qs}` : ""}`);
    },
    requestActivity: (opts?: {
      operation?: string; resource_kind?: string; status?: string;
      requester_id?: string; search?: string; limit?: number; offset?: number;
    }) => {
      const q = new URLSearchParams();
      if (opts?.operation) q.set("operation", opts.operation);
      if (opts?.resource_kind) q.set("resource_kind", opts.resource_kind);
      if (opts?.status) q.set("status", opts.status);
      if (opts?.requester_id) q.set("requester_id", opts.requester_id);
      if (opts?.search) q.set("search", opts.search);
      if (opts?.limit != null) q.set("limit", String(opts.limit));
      if (opts?.offset) q.set("offset", String(opts.offset));
      const qs = q.toString();
      return request<RequestActivityResp>(`/api/operator/dashboard/request-activity${qs ? `?${qs}` : ""}`);
    },
    requestDetail: (requestId: string) =>
      request<RequestDetail>(`/api/operator/dashboard/requests/${encodeURIComponent(requestId)}`),
    attention: () =>
      request<AttentionItem[]>("/api/operator/dashboard/attention"),
    // 처리 내역: 화면에 펼친 페이지만 로딩(offset/limit). {items,extra,total} — items는
    // 페이지 단위 포탈 행, extra는 DMS-only ack(완전성·첫 페이지에만), total은 grand
    // 카운트(배지). limit=0 → total만(행 미전송).
    dismissedAttention: (offset = 0, limit = 50, order: "desc" | "asc" = "desc") =>
      request<{ items: DismissedItem[]; extra: DismissedItem[]; total: number }>(
        `/api/operator/dashboard/attention/dismissed?offset=${offset}&limit=${limit}&order=${order}`,
      ),
    dismissedAttentionCount: () =>
      request<{ items: DismissedItem[]; extra: DismissedItem[]; total: number }>(
        "/api/operator/dashboard/attention/dismissed?limit=0",
      ).then((r) => r.total),
    dismissAttention: (items: DismissPayloadItem[]) =>
      request<{ dismissed: number }>("/api/operator/dashboard/attention/dismiss", {
        method: "POST",
        body: JSON.stringify({ items }),
      }),
    undismissAttention: (fingerprints: string[]) =>
      request<{ undismissed: number }>("/api/operator/dashboard/attention/undismiss", {
        method: "POST",
        body: JSON.stringify({ fingerprints }),
      }),
    // 모두 복원 / 이전 영구숨김: whole-list bulk ops — resolved server-side over the
    // ENTIRE 처리 내역 (not just the loaded page), so pagination never truncates them.
    undismissAllAttention: () =>
      request<{ undismissed: number }>("/api/operator/dashboard/attention/undismiss-all", {
        method: "POST",
      }),
    archiveAttentionBefore: (before: string) =>
      request<{ archived: number }>("/api/operator/dashboard/attention/archive-before", {
        method: "POST",
        body: JSON.stringify({ before }),
      }),
    archiveAttention: (fingerprints: string[]) =>
      request<{ archived: number }>("/api/operator/dashboard/attention/archive", {
        method: "POST",
        body: JSON.stringify({ fingerprints }),
      }),
    // 영구숨김 목록: 화면에 펼친 페이지만 로딩(offset/limit). {items,total} 반환 —
    // total은 배지/빈상태·'더 보기' 판정용(누적돼도 COUNT 1개). limit=0 → total만.
    archivedAttention: (offset = 0, limit = 50, order: "desc" | "asc" = "desc") =>
      request<{ items: DismissedItem[]; total: number }>(
        `/api/operator/dashboard/attention/archived?offset=${offset}&limit=${limit}&order=${order}`,
      ),
    archivedAttentionCount: () =>
      request<{ items: DismissedItem[]; total: number }>(
        "/api/operator/dashboard/attention/archived?limit=0",
      ).then((r) => r.total),
    unarchiveAttention: (fingerprints: string[]) =>
      request<{ restored: number }>("/api/operator/dashboard/attention/unarchive", {
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
      request<ControlHostsResp>("/api/operator/dashboard/control-hosts"),
    storageNodeMatrix: () =>
      request<StorageNodeMatrix>("/api/operator/dashboard/storage-node-matrix"),
    volcano: () =>
      request<VolcanoStatus>("/api/operator/dashboard/volcano"),
    volcanoMetrics: () =>
      request<VolcanoMetrics>("/api/operator/dashboard/volcano-metrics"),
  },
};
