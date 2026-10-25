// k8s 연동 화면 테스트 공용 픽스처(설계 §8 테스트베드 모양 — dmsk8s, team-a·team-b, junwoo.song·alice·cocoa.song).
// 앱 코드는 이 파일을 가져오지 않는다(테스트 전용 — 번들에 들어가지 않는다).
import type {
  AccessResponse, ClusterSummary, K8sOverview, StorageClassRow, SyncRun, VolumeRow, VolumesResponse,
} from "./types";

export const NOW = "2026-10-25T03:00:00Z";
const minutesAgo = (m: number) => new Date(Date.parse(NOW) - m * 60_000).toISOString();

export const CLUSTER_OK: ClusterSummary = {
  name: "dmsk8s", api_server: "https://10.10.10.20:6443", tls_server_name: null, tls_mode: "ca", token: "set",
  token_expires_at: null, enabled: true, status: "ok", last_attempt_at: minutesAgo(3), last_success_at: minutesAgo(3),
  last_reason: null, last_error: null, consecutive_failures: 0, server_version: "v1.34.6", fingerprint: "uid-1",
  duplicate_of: null, interval_seconds: 300,
  counts: { storage_classes: 3, pvs: 7, pvcs: 7, pvcs_bound: 6, namespaces: 3, bindings: 9, recognized_bindings: 5, users: 0 },
  last_run: { run_id: "r-10", node_name: "k8s-w1", role: "primary", trigger: "schedule", status: "applied",
              duration_ms: 1234, reason: null, warnings: null },
};

export const CLUSTER_FAILING: ClusterSummary = {
  ...CLUSTER_OK, name: "dmsk8s-dns", api_server: "https://k8s-cp1:6443", tls_server_name: "dms-k8s.local",
  status: "failing", last_success_at: null, last_reason: "k8s_sync_tls_failed",
  last_error: "certificate verify failed: Hostname mismatch", consecutive_failures: 2, server_version: null,
  counts: null, duplicate_of: "dmsk8s",
  last_run: { run_id: "r-11", node_name: "k8s-w1", role: "primary", trigger: "schedule", status: "failed",
              duration_ms: 80, reason: "k8s_sync_tls_failed", warnings: null },
};

export function overview(over: Partial<K8sOverview> = {}): K8sOverview {
  return {
    now: NOW,
    settings: { primary_node: "k8s-w1", secondary_node: "k8s-w2", interval_seconds: 300, poll_seconds: 15 },
    nodes: [
      { node_name: "k8s-w1", role: "primary", fresh: true, capable: true, reported_at: minutesAgo(0),
        internal_ip: "10.10.10.21", last_claim_at: minutesAgo(3), last_ok_at: minutesAgo(3), last_fail_at: null,
        last_reason: null, serving: ["dmsk8s"] },
      { node_name: "k8s-w2", role: "secondary", fresh: true, capable: true, reported_at: minutesAgo(0),
        internal_ip: "10.10.10.22", last_claim_at: null, last_ok_at: null, last_fail_at: null,
        last_reason: null, serving: [] },
    ],
    clusters: [CLUSTER_OK, CLUSTER_FAILING],
    totals: { clusters: 2, namespaces: 3, pvcs: 7, bindings: 9, users: 0 },
    ...over,
  };
}

export const SC_ROWS: StorageClassRow[] = [
  { cluster: "dmsk8s", name: "tb-gpfs", provisioner: "nfs.csi.k8s.io",
    parameters: { server: "10.10.10.1", share: "/data/k8s_pv/gpfs", subDir: "${pv.metadata.name}/${pv.metadata.name}-data" },
    reclaim_policy: "Retain", pvc_count: 2, storage_name: "k8s-pv-gpfs", storage_kind: "both", path_template: null,
    effective_template: "{volume}/{volume}-data", template_source: "preset",
    sample: { namespace: "team-a", pvc: "models", volume: "pvc-5c25f53f-aaaa", root: "pvc-5c25f53f-aaaa/pvc-5c25f53f-aaaa-data",
              abs_root: "/k8s_pv/gpfs/pvc-5c25f53f-aaaa/pvc-5c25f53f-aaaa-data", problem: null },
    duplicate_in: [] },
  { cluster: "dmsk8s", name: "tb-nfs", provisioner: "nfs.csi.k8s.io", parameters: { subDir: "${pvc.metadata.namespace}/${pvc.metadata.name}" },
    reclaim_policy: "Retain", pvc_count: 3, storage_name: null, storage_kind: null, path_template: null,
    effective_template: null, template_source: null, sample: null, duplicate_in: ["dmsk8s-dns"] },
];

export const VOLUME_MODELS: VolumeRow = {
  cluster: "dmsk8s", namespace: "team-a", storage_class: "tb-gpfs", pvc: "models", volume: "pvc-5c25f53f-aaaa",
  phase: "Bound", capacity: "1Gi", access_modes: ["ReadWriteMany"], created_at: "2026-10-25T01:00:00Z",
  storage_name: "k8s-pv-gpfs", root: "pvc-5c25f53f-aaaa/pvc-5c25f53f-aaaa-data",
  abs_root: "/k8s_pv/gpfs/pvc-5c25f53f-aaaa/pvc-5c25f53f-aaaa-data", selectable: true, reason: null, access: null,
  users: ["alice", "junwoo.song"],
};
export const VOLUME_PENDING: VolumeRow = {
  ...VOLUME_MODELS, namespace: "team-c", storage_class: "missing-sc", pvc: "pending-claim", volume: null, phase: "Pending",
  capacity: null, storage_name: null, root: null, abs_root: null, selectable: false, reason: "pv_not_bound", users: [],
};

export function volumes(over: Partial<VolumesResponse> = {}): VolumesResponse {
  return {
    clusters: [{ name: "dmsk8s", synced_at: minutesAgo(3), stale: false, status: "ok" }],
    truncated: false, volumes: [VOLUME_MODELS, VOLUME_PENDING], ...over,
  };
}

export const ACCESS: AccessResponse = {
  clusters: [{ name: "dmsk8s", synced_at: minutesAgo(3), stale: false, status: "ok" }],
  users: [
    { dms_user: "alice", account_exists: true, volume_count: 4, namespaces: [
      { cluster: "dmsk8s", namespace: "team-a", access: "edit", binding: "alice-clusterrole-edit", subject_mismatch: false },
      { cluster: "dmsk8s", namespace: "team-b", access: "admin", binding: "alice-clusterrole-admin", subject_mismatch: null },
    ] },
    { dms_user: "junwoo.song", account_exists: true, volume_count: 3, namespaces: [
      { cluster: "dmsk8s", namespace: "team-a", access: "admin", subject_mismatch: false,
        binding: { name: "junwoo-song-clusterrole-admin", role_kind: "ClusterRole", role_name: "kubeflow-admin",
                   subjects: [{ kind: "User", name: "junwoo.song@example.com" }] } },
    ] },
    { dms_user: "user.yo.hong.example.com", account_exists: false, volume_count: 3, namespaces: [
      { cluster: "dmsk8s", namespace: "team-a", access: "edit", subject_mismatch: true,
        binding: { name: "user-yo-hong-example-com-clusterrole-edit", role_kind: "ClusterRole", role_name: "kubeflow-edit",
                   subjects: [{ kind: "User", name: "yo.hong@example.com" }] } },
    ] },
  ],
  unrecognized: [
    { cluster: "dmsk8s", namespace: "team-a", name: "namespaceAdmin", role_kind: "ClusterRole", role_name: "kubeflow-admin",
      subjects: [{ kind: "User", name: "owner@example.com" }] },
    { cluster: "dmsk8s", namespace: "team-a", name: "bob-clusterrole-edit", role_kind: "ClusterRole", role_name: "edit",
      subjects: [{ kind: "User", name: "bob" }] },
  ],
};

export function run(seq: number, over: Partial<SyncRun> = {}): SyncRun {
  return {
    seq, run_id: `r-${seq}`, cluster_name: "dmsk8s", node_name: "k8s-w1", role: "primary", trigger: "schedule",
    status: "applied", claimed_at: minutesAgo(seq), finished_at: minutesAgo(seq), applied_at: minutesAgo(seq),
    duration_ms: 900, reason: null, phase: null, http_status: null, error: null, server_version: "v1.34.6",
    counts: { storage_classes: 3, pvcs: 7, pvs: 7, role_bindings: 9 }, warnings: null,
    changes: { pvcs: { added: 0, removed: 0, names: [] }, bindings: { added: 0, removed: 0, names: [] } },
    ...over,
  };
}
