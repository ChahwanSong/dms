/**
 * E19 100열 시나리오(2026-11-25 사용자 요청 「스토리지가 100개가 될 수도 있어」)의 **완전 합성** 마운트 현황 응답.
 *
 * `page.route(/\/api\/admin\/mount-status$/, (r) => r.fulfill({ json: wideMountStatus() }))` 로 GET 을 통째로 대신한다 --
 * `route.fetch` 는 쓰지 않는다(Node 쪽 요청이라 실패하면 Playwright 호출 로그가 요청 헤더·세션 쿠키를 그대로 찍는다). 응답 모양은
 * 서버 계약(src/dms/mount_status.py 모듈 docstring) + dms-k8s 덧붙임(kind·pv -- mount_status_k8s)이고, 앱 타입을 import 하지 않는다
 * (e2e 타입 환경은 src 밖).
 *
 * 규칙(결정적 -- src/features/mounts/mountFixtures.ts 의 wide() 와 같다):
 *   - 스토리지 i = 1..100: 이름 stor-NNN(+ i%7==0 이면 -gpfs-project-archive-cold -- 14개, 아니고 i%5==0 이면 -nfs-scratch, 아니고
 *     i%11==0 이면 -weka-research-01 -- 25자: 자연 줄바꿈이면 세 줄인 하이픈 이름, 7개). 경로 i%3: 0 = 60자 /mgmt_storage/very/long/…/NNN
 *     · 1 = /mgmt_storage/<gpfs|onefs|nfs>/projNNN(같은 긴 접두 -- 프로덕션 모양, 머리는 끝 조각을 보여야 열이 갈린다) · 2 = /mnt/sN.
 *     경로에는 「archive」·「stor-」를 넣지 않는다(찾기는 경로도 본다).
 *     종류 i%3: 0 파일시스템 · 1 PV · 2 PV·파일시스템, PV 류는 i%11==0 이면 StorageClass 매핑 없음.
 *   - 노드: 신선한 23대(긴 호스트 이름 1대 포함) + 리포트 지연 1대. 칸(신선한 노드 j, 0부터):
 *     i%10==3 → j%4==1 마운트 문제(사유 다섯 가지를 돈다 -- 모르는 사유 코드 하나 포함) · 6 → j%3==0 읽기 전용(나머지 정상 = 문제)
 *     · 9 → 모두 읽기 전용(의도된 구성 = 문제 아님) · 1·4 → j%2==0 없음 · 8 → j%5==0 확인 대기 · 나머지 정상(사용률).
 *   - 문제 있는 스토리지 = i%10∈{3,6} → 20개, archive ∩ 문제 = 56·63 → 2개.
 */

const pad = (i: number, n: number) => String(i).padStart(n, "0");

export const WIDE_STORAGES = 100;
export const wideName = (i: number): string => `stor-${pad(i, 3)}${
  i % 7 === 0 ? "-gpfs-project-archive-cold" : i % 5 === 0 ? "-nfs-scratch" : i % 11 === 0 ? "-weka-research-01" : ""}`;
const WIDE_FS = ["gpfs", "onefs", "nfs"];
export const widePath = (i: number): string =>
  (i % 3 === 0 ? `/mgmt_storage/very/long/mount/path/for/testing/truncation/${pad(i, 3)}`
    : i % 3 === 1 ? `/mgmt_storage/${WIDE_FS[Math.floor(i / 3) % 3]}/proj${pad(i, 3)}` : `/mnt/s${i}`);
/** 하이픈 이름(25자 -- 줄을 직접 나눠 두 줄 안에 다 보여야 한다, 머리 검사 대상). */
export const WIDE_HYPHEN = wideName(11);
/** 긴 이름 열(머리 말줄임 + 끝 12자 검사 대상, 종류 PV). */
export const WIDE_LONG = wideName(7);
export const WIDE_UNKNOWN_REASON = "weird_new_reason_code_from_newer_agent";
const REASONS = ["not_a_mountpoint", "propagation_stale", "not_readable", "host_root_missing", WIDE_UNKNOWN_REASON];

export const WIDE_LONG_NODE = "h-aii-dms-26001-gpu-a100-rack07-slot3.example.internal";
export const WIDE_FRESH_NODES: string[] = [WIDE_LONG_NODE, ...Array.from({ length: 22 }, (_, k) => `k8s-w${pad(k + 1, 2)}`)];
export const WIDE_STALE_NODE = "k8s-w99-stale";
/** 표의 행 수(신선한 노드 + 리포트 지연). */
export const WIDE_ROWS = WIDE_FRESH_NODES.length + 1;

type Cell = Record<string, unknown> & { state: string };

const base = (path: string): Cell => ({
  state: "ready", last: null, pending: null, status: "Ready", reason: null, reported_path: path, exists: true,
  is_mountpoint: true, readable: true, writable: true, disk: null,
});

function cellOf(i: number, j: number, path: string): Cell {
  const ready = base(path);
  const ro = { ...ready, state: "read_only", writable: false };
  switch (i % 10) {
    case 3:
      return j % 4 === 1
        ? { ...ready, state: "broken", status: "Missing", reason: REASONS[(i + j) % REASONS.length], is_mountpoint: false,
            writable: false }
        : ready;
    case 6: return j % 3 === 0 ? ro : ready;
    case 9: return ro;
    case 1: case 4:
      return j % 2 === 0
        ? { ...ready, state: "absent", status: "Missing", reason: "missing_mount_path", exists: false, is_mountpoint: false,
            readable: false, writable: false }
        : ready;
    case 8:
      return j % 5 === 0
        ? { ...ready, state: "pending", pending: "not_reported", status: null, reported_path: null, exists: null,
            is_mountpoint: null, readable: null, writable: null }
        : ready;
    default:
      return { ...ready, disk: { total_bytes: 1000, used_bytes: 100 + ((i * 37 + j * 11) % 800) } };
  }
}

export function wideMountStatus(nowMs = Date.now()): Record<string, unknown> {
  const now = new Date(nowMs).toISOString();
  const old = new Date(nowMs - 12 * 60_000).toISOString();
  const ids = Array.from({ length: WIDE_STORAGES }, (_, k) => k + 1);
  const sharedOk = { state: "ok", last: null, reported_path: "/dms_shared/dms/artifacts", exists: true, writable: true,
                     mount_point: "/dms_shared", fstype: "nfs4" };
  const fresh = WIDE_FRESH_NODES.map((name, j) => ({
    name, cells: Object.fromEntries(ids.map((i) => [wideName(i), cellOf(i, j, widePath(i))])) as Record<string, Cell>,
  }));
  // 「문제 있는 노드」 수 = 서버 mount_status._cell_problem 의 거울(앱 코드를 import 하지 않아 여기 따로 둔다): 마운트 문제 · 정상
  // 노드가 있는 열의 읽기 전용 · 마운트 흔적(_attached -- 정상·읽기 전용·점검 불가가 아닌 마운트 문제, 리포트 지연 행은 마지막 값)이
  // 하나도 없는 열의 없음(2026-11-27 -- 23노드라 이 합성 응답에는 없다; 지연 행의 마지막 값은 노드 0 의 칸과 같아 fresh 만 보면 된다).
  const attached = (c: Cell) => c.state === "ready" || c.state === "read_only"
    || (c.state === "broken" && c.reason !== "host_root_missing");
  const roOutliers = new Set(ids.map(wideName).filter((n) => fresh.some((r) => r.cells[n].state === "ready")));
  const mounted = new Set(ids.map(wideName).filter((n) => fresh.some((r) => attached(r.cells[n]))));
  const row = (name: string, cells: Record<string, Cell>, over: Record<string, unknown> & { problem: boolean }) => ({
    node_name: name, reported_at: now, fresh: true, probe_mode: "host_root", exclusion: null,
    k8s_node: { schedulable: true, reason: null, transient: false }, roles: [] as string[], control_plane: false,
    in_cluster: true, cells, shared_dir: sharedOk as Record<string, unknown>, ...over,
  });
  const nodes = fresh.map(({ name, cells }, j) => {
    const count = Object.entries(cells)
      .filter(([n, c]) => c.state === "broken" || (c.state === "read_only" && roOutliers.has(n))
        || (c.state === "absent" && !mounted.has(n))).length;
    const roles = j === 1 ? ["build", "k8s_primary"] : j === 2 ? ["k8s_secondary"] : [];
    return row(name, cells, { roles, problem: count > 0, problem_count: count });
  });
  nodes.push(row(WIDE_STALE_NODE, Object.fromEntries(ids.map((i) => {
    const last = cellOf(i, 0, widePath(i));
    return [wideName(i), { ...last, state: "stale", last: last.state }];
  })), { reported_at: old, fresh: false, problem: true, problem_count: null,
         shared_dir: { ...sharedOk, state: "stale", last: "ok" } }));

  const storages = ids.map((i) => {
    const name = wideName(i);
    const states = fresh.map((r) => r.cells[name].state);
    const of = (s: string) => states.filter((x) => x === s).length;
    const ready = of("ready") + of("read_only");
    const total = states.length - of("pending");
    const kind = ["filesystem", "pv", "both"][i % 3];
    return {
      storage_name: name, mount_path: widePath(i), backend_type: "gpfs", user_enabled: true,
      summary: { ready, total, status: ready === total ? "Ready" : "Degraded", detail: `ready_nodes=${ready}/${total}`,
                 broken: of("broken"), read_only: of("read_only"), absent: of("absent"), pending: of("pending") },
      kind,
      pv: kind === "filesystem" ? null : {
        count: i % 4 === 0 ? null : i, clusters: i % 2 === 1 ? ["dmsk8s", "dmsk8s2"] : ["dmsk8s"],
        storage_classes: i % 11 === 0 ? [] : [`sc-${pad(i, 3)}`],
      },
    };
  });
  const all = fresh.flatMap((r) => Object.values(r.cells).map((c) => c.state));
  const cnt = (s: string) => all.filter((x) => x === s).length;
  return {
    generated_at: now, stale_seconds: 300, report_interval_seconds: 60,
    shared_dir: { path: "/dms_shared/dms/artifacts", state_root: "/dms_shared/dms",
                  majority: { mount_point: "/dms_shared", fstype: "nfs4", nodes: fresh.length } },
    storages, disabled_storages: [], nodes,
    totals: {
      nodes: nodes.length, fresh_nodes: fresh.length, stale_nodes: 1, ready: cnt("ready"), read_only: cnt("read_only"),
      absent: cnt("absent"), broken: cnt("broken"), pending: cnt("pending"), shared_ok: fresh.length, shared_unverified: 0,
      shared_problem: 0, shared_pending: 0, problem_nodes: nodes.filter((r) => r.problem).length,
    },
    cluster: { known: true, reason: null, observed_at: now, agentless_nodes: [] },
    control_pods: [],
  };
}
