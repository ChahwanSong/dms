// 마운트 현황 테스트 픽스처(테스트 전용 -- 앱 코드는 import 하지 않는다; k8s/k8sFixtures 선례). LIVE 는 2026-11-23 dmsk8s 라이브
// 모양(k8s-w1·k8s-w2 × 스토리지 6, w1/cephfs-secondary·w2/cephfs-third 경로 없음, 공용 /dms_shared nfs4)이고 k8s 덧붙임 키가
// 없다(공용 코어 = dms-ssc 모양). VARIETY 는 칸·공용·꼬리표의 모든 갈래를 한 화면에 모은다.
import type { ControlPod, MountCellData, MountRow, MountStatusResponse, SharedCellData, StorageColumn } from "./types";

export const NOW_ISO = new Date().toISOString();

export const STORAGE_PATHS: [string, string][] = [
  ["cephfs-dms", "/cephfs"], ["cephfs-secondary", "/cephfs-secondary"], ["cephfs-third", "/cephfs-third"],
  ["k8s-pv-gpfs", "/k8s_pv/gpfs"], ["k8s-pv-nfs", "/k8s_pv/nfs"], ["k8s-pv-onefs", "/k8s_pv/onefs"],
];

export const ready = (path: string, over: Partial<MountCellData> = {}): MountCellData => ({
  state: "ready", last: null, pending: null, status: "Ready", reason: null, reported_path: path, exists: true,
  is_mountpoint: true, readable: true, writable: true, disk: null, ...over,
});
export const absent = (path: string): MountCellData => ({
  state: "absent", last: null, pending: null, status: "Missing", reason: "missing_mount_path", reported_path: path,
  exists: false, is_mountpoint: false, readable: false, writable: false, disk: null,
});
export const broken = (path: string, reason: string): MountCellData => ({
  state: "broken", last: null, pending: null, status: "Missing", reason, reported_path: path, exists: true,
  is_mountpoint: false, readable: true, writable: false, disk: null,
});
export const sharedOk = (over: Partial<SharedCellData> = {}): SharedCellData => ({
  state: "ok", last: null, reported_path: "/dms_shared/dms/artifacts", exists: true, writable: true,
  mount_point: "/dms_shared", fstype: "nfs4", ...over,
});

export const column = (name: string, path: string, over: Partial<StorageColumn["summary"]> = {}): StorageColumn => ({
  storage_name: name, mount_path: path, backend_type: "cephfs", user_enabled: true,
  summary: { ready: 2, total: 2, status: "Ready", detail: "ready_nodes=2", broken: 0, read_only: 0, absent: 0, pending: 0,
             ...over },
});

export const row = (name: string, cells: Record<string, MountCellData>, over: Partial<MountRow> = {}): MountRow => ({
  node_name: name, reported_at: NOW_ISO, fresh: true, probe_mode: "host_root", exclusion: null,
  k8s_node: { schedulable: true, reason: null, transient: false }, roles: [], control_plane: false, in_cluster: true,
  problem: false, problem_count: 0, cells, shared_dir: sharedOk(), ...over,
});

const TOTALS = {
  nodes: 2, fresh_nodes: 2, stale_nodes: 0, ready: 10, read_only: 0, absent: 2, broken: 0, pending: 0,
  shared_ok: 2, shared_unverified: 0, shared_problem: 0, shared_pending: 0, problem_nodes: 0,
};

function liveCells(missing: string): Record<string, MountCellData> {
  return Object.fromEntries(STORAGE_PATHS.map(([n, p]) => [n, n === missing ? absent(p) : ready(p)]));
}

export const LIVE: MountStatusResponse = {
  generated_at: NOW_ISO, stale_seconds: 300, report_interval_seconds: 60,
  shared_dir: { path: "/dms_shared/dms/artifacts", state_root: "/dms_shared/dms",
                majority: { mount_point: "/dms_shared", fstype: "nfs4", nodes: 2 } },
  storages: STORAGE_PATHS.map(([n, p]) =>
    n === "cephfs-secondary" || n === "cephfs-third"
      ? column(n, p, { ready: 1, total: 2, status: "Degraded", detail: "ready_nodes=1/2", absent: 1 })
      : column(n, p)),
  disabled_storages: [],
  nodes: [
    row("k8s-w1", liveCells("cephfs-secondary"), { roles: ["k8s_primary"] }),
    row("k8s-w2", liveCells("cephfs-third"), { roles: ["k8s_secondary"] }),
  ],
  totals: TOTALS,
  cluster: { known: true, reason: null, observed_at: NOW_ISO,
             agentless_nodes: [{ node_name: "k8s-cp1", control_plane: true }] },
};

const OLD_ISO = new Date(Date.now() - 12 * 60_000).toISOString();

// VARIETY: 스토리지 둘(ceph·sec) × 노드 다섯.
const C = "/cephfs";
const S = "/sec";
export const VARIETY: MountStatusResponse = {
  generated_at: NOW_ISO, stale_seconds: 300, report_interval_seconds: 60,
  shared_dir: { path: "/dms_shared/dms/artifacts", state_root: "/dms_shared/dms",
                majority: { mount_point: "/dms_shared", fstype: "nfs4", nodes: 2 } },
  storages: [
    column("ceph", C, { ready: 3, total: 4, status: "Degraded", detail: "ready_nodes=3/4", read_only: 1, broken: 1 }),
    column("sec", S, { ready: 1, total: 3, status: "Degraded", detail: "ready_nodes=1/3", broken: 1, pending: 1, absent: 0 }),
  ],
  disabled_storages: ["old-nas"],
  nodes: [
    row("n-build", { ceph: ready(C, { disk: { total_bytes: 1000, used_bytes: 410 } }), sec: absent(S) },
        { roles: ["build"], control_plane: true }),
    row("n-broken", { ceph: broken(C, "not_a_mountpoint"), sec: broken(S, "propagation_stale") },
        { problem: true, problem_count: 3, shared_dir: sharedOk({ mount_point: "/", fstype: "ext4", state: "local" }),
          exclusion: { node_name: "n-broken", reason: "디스크 점검", created_by: "ops", created_at: NOW_ISO },
          k8s_node: { schedulable: false, reason: "cordoned", transient: false } }),
    row("n-ro", { ceph: ready(C, { state: "read_only", writable: false }), sec: broken(S, "host_root_missing") },
        { problem: true, problem_count: 3, shared_dir: sharedOk({ state: "differs", mount_point: "/data", fstype: "xfs" }) }),
    row("n-pend", { ceph: ready(C, { writable: null }),
                    sec: { ...ready("/old-sec"), state: "pending", pending: "path_changed" } },
        { in_cluster: false, control_plane: null,
          shared_dir: sharedOk({ state: "ok_unverified", mount_point: null, fstype: null }) }),
    row("n-stale", { ceph: { ...ready(C), state: "stale", last: "ready" },
                     sec: { ...broken(S, "not_a_mountpoint"), state: "stale", last: "broken" } },
        { fresh: false, reported_at: OLD_ISO, problem: true, problem_count: null,
          shared_dir: { ...sharedOk(), state: "stale", last: "ok" } }),
  ],
  totals: {
    nodes: 5, fresh_nodes: 4, stale_nodes: 1, ready: 2, read_only: 1, absent: 1, broken: 3, pending: 1,
    shared_ok: 2, shared_unverified: 1, shared_problem: 2, shared_pending: 0, problem_nodes: 3,
  },
  cluster: { known: false, reason: "list_failed", observed_at: null, agentless_nodes: [] },
};

export const EMPTY_NODES: MountStatusResponse = {
  ...LIVE, nodes: [], storages: [], shared_dir: null,
  totals: { ...TOTALS, nodes: 0, fresh_nodes: 0, ready: 0, absent: 0, shared_ok: 0 },
};

export const NO_STORAGES: MountStatusResponse = {
  ...LIVE, storages: [],
  nodes: LIVE.nodes.map((r) => ({ ...r, cells: {} })),
  totals: { ...TOTALS, ready: 0, absent: 0 },
};

export const pod = (role: string, node: string, over: Partial<ControlPod> = {}): ControlPod => ({
  role, instance: role === "api_pod" ? `dms-api-${node}` : null, node_name: node, ok: true, reason: null, read_failed: false,
  checked_at: NOW_ISO, gate_state: role === "api_pod" ? "passed" : null, in_table: true, ...over,
});

// WITH_PODS: VARIETY 위에 DMS 파드 덧붙임(서버 mount_status_pods). 사고 서명 = n-broken(공용 디렉터리 로컬) 위의 dms-api(롤링 중 새 파드 --
// 시작 점검에 막힘), n-pend 는 에이전트가 공용 디렉터리를 「있음」으로 보는데 dms-api 가 못 읽는다, 컨트롤러는 정상 노드 n-build, 표에
// 없는 마스터 cp1 의 dms-api 는 정상·cp2 의 dms-api 는 실패.
export const WITH_PODS: MountStatusResponse = {
  ...VARIETY,
  nodes: VARIETY.nodes.map((r) =>
    r.node_name === "n-build" ? { ...r, roles: ["build", "controller_pod"] }
    : r.node_name === "n-broken" ? { ...r, roles: ["api_pod"] }
    : r.node_name === "n-pend" ? { ...r, roles: ["api_pod"], problem: true, problem_count: 1 }
    : r),
  totals: { ...VARIETY.totals, problem_nodes: 4 },
  control_pods: [
    pod("api_pod", "cp1", { in_table: false }),
    pod("api_pod", "cp2", { in_table: false, ok: false, reason: "artifact_base_check_timeout", read_failed: true }),
    pod("api_pod", "n-broken", { ok: false, reason: "artifact_base_jobs_missing", read_failed: true, gate_state: "waiting" }),
    pod("api_pod", "n-pend", { ok: false, reason: "artifact_base_jobs_missing", read_failed: true }),
    pod("controller_pod", "n-build"),
  ],
};
