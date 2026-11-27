// 마운트 현황 테스트 픽스처(테스트 전용 -- 앱 코드는 import 하지 않는다; k8s/k8sFixtures 선례). LIVE 는 2026-11-23 dmsk8s 라이브
// 모양(k8s-w1·k8s-w2 × 스토리지 6, w1/cephfs-secondary·w2/cephfs-third 경로 없음, 공용 /dms_shared nfs4)이고 k8s 덧붙임 키가
// 없다(공용 코어 = dms-ssc 모양). VARIETY 는 칸·공용·꼬리표의 모든 갈래를 한 화면에 모은다. NOWHERE(2026-11-27)는 LIVE 위에 끊긴
// 마운트 칸과 어느 노드에도 마운트되지 않은 스토리지 열을 얹는다. 문제 수(problem_count·totals)는 서버 규칙(mount_status 「문제 있는
// 노드」)으로 손으로 센 값이다 -- 칸을 바꾸면 수도 같이 맞춘다(화면이 칸에서 다시 재는 열 재료와 어긋나면 표시가 서버와 두 말을 한다).
import { cellProblem, columnFacts } from "./mountTerms";
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
/** 끊긴 마운트(서버 classify_cell -- Missing + missing_mount_path 인데 호스트 mountinfo 엔 마운트가 있다: 죽은 FUSE·낡은 NFS). */
export const deadMount = (path: string): MountCellData => ({
  state: "broken", last: null, pending: null, status: "Missing", reason: "missing_mount_path", reported_path: path, exists: false,
  is_mountpoint: true, readable: false, writable: false, disk: null,
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

// NOWHERE: LIVE + 스토리지 cephfs-typo(두 노드 모두 경로 없음 -- mount_path 오타 모양, 어디에도 마운트되지 않아 그 없음은 문제) +
// k8s-w2 의 cephfs-dms 끊긴 마운트. 문제 = w1 1(typo) · w2 2(typo + 끊긴 cephfs-dms), 문제 있는 스토리지 = cephfs-dms·cephfs-typo.
export const NOWHERE: MountStatusResponse = {
  ...LIVE,
  storages: [
    ...LIVE.storages.map((c) => (c.storage_name === "cephfs-dms"
      ? column(c.storage_name, c.mount_path, { ready: 1, total: 2, status: "Degraded", detail: "ready_nodes=1/2", broken: 1 })
      : c)),
    column("cephfs-typo", "/cephfs-typo",
           { ready: 0, total: 2, status: "Degraded", detail: "no_ready_mounts (nodes=2)", absent: 2 }),
  ],
  nodes: LIVE.nodes.map((r) => (r.node_name === "k8s-w1"
    ? { ...r, cells: { ...r.cells, "cephfs-typo": absent("/cephfs-typo") }, problem: true, problem_count: 1 }
    : { ...r, cells: { ...r.cells, "cephfs-dms": deadMount("/cephfs"), "cephfs-typo": absent("/cephfs-typo") }, problem: true,
        problem_count: 2 })),
  totals: { ...TOTALS, ready: 9, absent: 4, broken: 1, problem_nodes: 2 },
};

// VARIETY: 스토리지 둘(ceph·sec) × 노드 다섯. sec 은 n-pend 만 붙어 있다(정상·쓰기 여부 모름) -- 그래서 n-build 의 sec 없음은 안 붙인
// 노드(문제 아님, 2026-11-27 열 규칙). n-pend 의 ceph 는 옛 경로로 잰 결과(확인 대기·경로 바뀜).
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
    row("n-pend", { ceph: { ...ready("/old-cephfs"), state: "pending", pending: "path_changed" },
                    sec: ready(S, { writable: null }) },
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

// wide(2026-11-25 「스토리지가 100개가 될 수도 있어」): 열이 많은 표의 결정적 합성 응답 -- e2e/helpers/mountWide.ts 와 같은 규칙(그쪽은
// k8s 덧붙임 키·긴 노드 이름·리포트 지연 노드까지 싣는다). 스토리지 i(1부터): 이름 stor-NNN(+ i%7==0 이면 -gpfs-project-archive-cold
// -- 100열에 14개, 아니고 i%5==0 이면 -nfs-scratch, 아니고 i%11==0 이면 -weka-research-01 -- 25자: 자연 줄바꿈이면 세 줄인 하이픈
// 이름), 경로 i%3: 0 = 60자 /mgmt_storage/very/long/…/NNN · 1 = /mgmt_storage/<gpfs|onefs|nfs>/projNNN(같은 긴 접두 -- 프로덕션
// 모양) · 2 = /mnt/sN, 칸(노드 j, 0부터) i%10==3 → j%4==1 마운트 문제 · 6 → j%3==0 읽기 전용(나머지
// 정상 = 문제) · 9 → 모두 읽기 전용(문제 아님) · 1·4 → j%2==0 없음 · 8 → j%5==0 확인 대기 · 나머지 정상. 문제 있는 스토리지 =
// i%10∈{3,6}(100열에 20개), archive ∩ 문제 = 56·63. 경로에는 「archive」·「stor-」가 없다(찾기는 경로도 본다). summary·totals·
// problem_count 는 칸에서 센다(서버 규칙의 거울 -- mountTerms.columnFacts·cellProblem; 노드가 1대면 i%10∈{1,4} 열이 어디에도 없는
// 스토리지가 돼 그 없음도 센다).
const pad = (i: number, n: number) => String(i).padStart(n, "0");
export const wideName = (i: number): string => `stor-${pad(i, 3)}${
  i % 7 === 0 ? "-gpfs-project-archive-cold" : i % 5 === 0 ? "-nfs-scratch" : i % 11 === 0 ? "-weka-research-01" : ""}`;
const WIDE_FS = ["gpfs", "onefs", "nfs"];
const widePath = (i: number) =>
  (i % 3 === 0 ? `/mgmt_storage/very/long/mount/path/for/testing/truncation/${pad(i, 3)}`
    : i % 3 === 1 ? `/mgmt_storage/${WIDE_FS[Math.floor(i / 3) % 3]}/proj${pad(i, 3)}` : `/mnt/s${i}`);
export const WIDE_REASONS = ["not_a_mountpoint", "propagation_stale", "not_readable", "host_root_missing",
                             "weird_new_reason_code_from_newer_agent"];

function wideCell(i: number, j: number, path: string): MountCellData {
  const ro = () => ready(path, { state: "read_only", writable: false });
  switch (i % 10) {
    case 3: return j % 4 === 1 ? broken(path, WIDE_REASONS[(i + j) % WIDE_REASONS.length]) : ready(path);
    case 6: return j % 3 === 0 ? ro() : ready(path);
    case 9: return ro();
    case 1: case 4: return j % 2 === 0 ? absent(path) : ready(path);
    case 8: return j % 5 === 0
      ? { ...ready(path), state: "pending", pending: "not_reported", status: null, reported_path: null, exists: null,
          is_mountpoint: null, readable: null, writable: null }
      : ready(path);
    default: return ready(path, { disk: { total_bytes: 1000, used_bytes: 100 + ((i * 37 + j * 11) % 800) } });
  }
}

export function wide(n: number, nodes: number): MountStatusResponse {
  const ids = Array.from({ length: n }, (_, k) => k + 1);
  const rows: MountRow[] = Array.from({ length: nodes }, (_, j) =>
    row(`node-${pad(j, 2)}`, Object.fromEntries(ids.map((i) => [wideName(i), wideCell(i, j, widePath(i))]))));
  const facts = columnFacts(rows);
  for (const r of rows) {
    const count = Object.entries(r.cells).filter(([name, c]) => cellProblem(name, c, facts)).length;
    r.problem_count = count;
    r.problem = count > 0;
  }
  const storages = ids.map((i) => {
    const name = wideName(i);
    const states = rows.map((r) => r.cells[name].state);
    const of = (s: string) => states.filter((x) => x === s).length;
    const readyN = of("ready") + of("read_only");
    const total = states.length - of("pending");
    return column(name, widePath(i), {
      ready: readyN, total, status: readyN === total ? "Ready" : "Degraded", detail: `ready_nodes=${readyN}/${total}`,
      broken: of("broken"), read_only: of("read_only"), absent: of("absent"), pending: of("pending"),
    });
  });
  const all = rows.flatMap((r) => Object.values(r.cells).map((c) => c.state));
  const cnt = (s: string) => all.filter((x) => x === s).length;
  return {
    ...LIVE, storages, nodes: rows,
    totals: { nodes, fresh_nodes: nodes, stale_nodes: 0, ready: cnt("ready"), read_only: cnt("read_only"), absent: cnt("absent"),
              broken: cnt("broken"), pending: cnt("pending"), shared_ok: nodes, shared_unverified: 0, shared_problem: 0,
              shared_pending: 0, problem_nodes: rows.filter((r) => r.problem).length },
  };
}
