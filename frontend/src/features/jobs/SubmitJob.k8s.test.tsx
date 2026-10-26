import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, beforeEach, expect, test } from "vitest";
import { SubmitJob } from "./SubmitJob";
import { SYNC_INT_FIELDS } from "./optionRules";
import { PRIV_ROOT_SYNC_DIRECT_BOTH, PRIV_ROOT_SYNC_PV, PRIV_ROOT_SYNC_PV_ROOT } from "./optionRules";
import type { Me, UserStorage } from "../../lib/types";
import type { ClusterFreshness, UserVolumesResponse, VolumeRow, VolumesResponse } from "../k8s/types";

// dms-k8s 설계 §7(2026-10-25): k8s 모드 단일 작업 -- 세 역할(일반 사용자 · 목록 밖 관리자 · 면제 관리자) × sync/scan/rm 의 제출
// 바디, 빈 상태 문구 셋, PV 루트 rm 차단, ?pv= 프리필, 사용자 지정 범위 전환. 서버는 MSW 모의(me 의 identity_mode·unrestricted·
// user_operations 는 다른 갈래가 같은 때 만든다 -- 계약은 설계 §2 D4·D15).

const NOW = Date.now();
const ago = (m: number) => new Date(NOW - m * 60_000).toISOString();
const GPFS_A = "pvc-52c6132d-bce6-4cbe-842a-dbe35245ab2c";
const GPFS_B = "pvc-b0b6e65b-f74a-4998-8f84-e1ae6047deb8";

function vol(over: Partial<VolumeRow>): VolumeRow {
  return {
    cluster: "dmsk8s", namespace: "team-a", storage_class: "tb-gpfs", pvc: "models", volume: GPFS_A, phase: "Bound",
    capacity: "1Gi", access_modes: ["ReadWriteMany"], created_at: "2026-10-25T01:00:00Z", storage_name: "k8s-pv-gpfs",
    root: `${GPFS_A}/${GPFS_A}-data`, abs_root: `/k8s_pv/gpfs/${GPFS_A}/${GPFS_A}-data`, selectable: true, reason: null,
    // 목적지 스토리지 기본 소유자(2026-10-26 -- 서버 storage_default_owner). k8s-pv-gpfs 는 기본 0:0, k8s-pv-nfs 는 1500:1500.
    access: "admin", storage_owner: "0:0", ...over,
  };
}
const A_MODELS = vol({});
const A_DATASETS = vol({ pvc: "datasets", storage_class: "tb-nfs", volume: "pvc-814fe02b", capacity: "2Gi",
                         storage_name: "k8s-pv-nfs", root: "team-a/datasets", abs_root: "/k8s_pv/nfs/team-a/datasets",
                         storage_owner: "1500:1500" });
const A_PENDING = vol({ pvc: "checkpoints", storage_class: "tb-missing", volume: null, phase: "Pending", capacity: null,
                        storage_name: null, root: null, abs_root: null, selectable: false, reason: "pv_not_bound",
                        storage_owner: null });
const B_SCRATCH = vol({ namespace: "team-b", pvc: "scratch", storage_class: "tb-nfs", volume: "pvc-aa3d0bd2",
                        capacity: "5Gi", storage_name: "k8s-pv-nfs", root: "team-b/scratch",
                        abs_root: "/k8s_pv/nfs/team-b/scratch", access: "edit", storage_owner: "1500:1500" });
const B_MODELS = vol({ namespace: "team-b", volume: GPFS_B, root: `${GPFS_B}/${GPFS_B}-data`,
                       abs_root: `/k8s_pv/gpfs/${GPFS_B}/${GPFS_B}-data`, access: "edit" });
const C_ARCHIVE = vol({ namespace: "team-c", pvc: "archive", storage_class: "tb-nfs", volume: "pvc-5e5969a1",
                        storage_name: "k8s-pv-nfs", root: "team-c/archive", abs_root: "/k8s_pv/nfs/team-c/archive",
                        access: null, storage_owner: "1500:1500" });

const FRESH: ClusterFreshness[] = [{ name: "dmsk8s", synced_at: ago(3), stale: false, status: "ok" }];

function userVolumes(over: Partial<UserVolumesResponse> = {}): UserVolumesResponse {
  return {
    clusters: FRESH, unrestricted: false, truncated: false,
    namespaces: [{ cluster: "dmsk8s", namespace: "team-a", access: "admin" },
                 { cluster: "dmsk8s", namespace: "team-b", access: "edit" }],
    volumes: [A_DATASETS, A_MODELS, A_PENDING, B_MODELS, B_SCRATCH], ...over,
  };
}

const STORAGES = [
  { storage_name: "cephfs-dms", backend_type: "cephfs", status: "Ready", managed_root: "/cephfs/managed", kind: "filesystem" },
  { storage_name: "k8s-pv-gpfs", backend_type: "gpfs", status: "Ready", managed_root: "/k8s_pv/gpfs", kind: "both" },
  { storage_name: "k8s-pv-nfs", backend_type: "nfs", status: "Ready", managed_root: "/k8s_pv/nfs", kind: "pv" },
] as unknown as UserStorage[];

// 서버 /me 의 user_operations 그대로: scan 은 특권 운영자(면제자)만(2026-10-26 (g) -- k8s_access.scan_allowed).
const OPS = ["sync", "rm"];
const OPS_EXEMPT = ["scan", "sync", "rm"];
const meUser: Me = { actor: "junwoo.song", role: "user", identity_mode: "k8s", unrestricted: false, user_operations: OPS };
const meAdminListed: Me = { actor: "ops2", role: "admin", can_run_as_root: false, identity_mode: "k8s", unrestricted: false,
                            user_operations: OPS };
const meExempt: Me = { actor: "root", role: "admin", can_run_as_root: true, identity_mode: "k8s", unrestricted: true,
                       user_operations: OPS_EXEMPT };

const SYNC_DEFAULTS = { open_noatime: true, batch_files: Number(SYNC_INT_FIELDS.batch_files.prefill),
                        bufsize: Number(SYNC_INT_FIELDS.bufsize.prefill) };

let posted: unknown[] = [];
let adminVolumeUrls: string[] = [];

function adminVolumes(url: URL): VolumesResponse {
  const user = url.searchParams.get("user");
  const q = (url.searchParams.get("q") ?? "").toLowerCase();
  const cluster = url.searchParams.get("cluster");
  const namespace = url.searchParams.get("namespace");
  const pvc = url.searchParams.get("pvc");                     // 이름 정확 일치(서버 repositories.k8s.volumes 의 pvc)
  let rows = [A_DATASETS, A_MODELS, A_PENDING, B_MODELS, B_SCRATCH, C_ARCHIVE];
  if (user === "alice") rows = rows.filter((r) => r.namespace === "team-a" || r.namespace === "team-b");
  else if (user === "cocoa.song") rows = rows.filter((r) => r.namespace === "team-b");
  else if (user !== null) rows = [];
  if (cluster) rows = rows.filter((r) => r.cluster === cluster);
  if (namespace) rows = rows.filter((r) => r.namespace === namespace);
  if (pvc) rows = rows.filter((r) => r.pvc === pvc);
  if (q) rows = rows.filter((r) => [r.pvc, r.namespace, r.volume ?? "", r.storage_class ?? ""].some((v) => v.includes(q)));
  return { clusters: FRESH, truncated: false, volumes: rows.map((r) => ({ ...r, users: ["alice"] })) };
}

const server = setupServer();
beforeAll(() => server.listen());
beforeEach(() => {
  posted = [];
  adminVolumeUrls = [];
  try { localStorage.clear(); } catch { /* 없음 */ }
  server.use(
    http.get("/api/user/storages", () => HttpResponse.json(STORAGES)),
    http.get("/api/user/k8s/volumes", () => HttpResponse.json(userVolumes())),
    http.get("/api/admin/k8s/volumes", ({ request }) => {
      adminVolumeUrls.push(request.url);
      return HttpResponse.json(adminVolumes(new URL(request.url)));
    }),
    http.get("/api/admin/k8s/access", () => HttpResponse.json({
      clusters: FRESH, unrecognized: [],
      users: [{ dms_user: "alice", account_exists: true, volume_count: 4, namespaces: [] },
              { dms_user: "cocoa.song", account_exists: true, volume_count: 2, namespaces: [] }],
    })),
    http.get("/api/admin/policies", () => HttpResponse.json([])),
    http.post("/api/user/requests", async ({ request }) => {
      posted.push(await request.json());
      return HttpResponse.json({ request_id: "r-1", state: "Submitted" }, { status: 201 });
    }),
  );
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderAs(me: Me, entry = "/jobs/new") {
  server.use(http.get("/api/auth/me", () => HttpResponse.json(me)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/jobs/new" element={<SubmitJob />} />
          <Route path="/jobs/:id" element={<h1>요청 상세</h1>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const submitButton = () => screen.getByRole("button", { name: "제출" });
const hints = () => document.getElementById("submit-job-hints");

async function pick(groupLabel: string, pvLabel: string, group: string, pv: string) {
  const g = await screen.findByLabelText(groupLabel);
  await within(g).findByRole("option", { name: group });
  await userEvent.selectOptions(g, group);
  await userEvent.selectOptions(screen.getByLabelText(pvLabel), within(screen.getByLabelText(pvLabel))
    .getByRole("option", { name: pv }));
}

async function choose(op: "scan" | "rm") {
  await screen.findByRole("option", { name: op });
  await userEvent.selectOptions(screen.getByLabelText("연산"), op);
}

async function submitAndBody() {
  await waitFor(() => expect(submitButton()).toBeEnabled());
  await userEvent.click(submitButton());
  await screen.findByRole("heading", { name: "요청 상세" });
  return posted[0];
}

// ---- 일반 사용자 ---------------------------------------------------------------------------------------------

test("사용자 sync: namespace (StorageClass) → PV 이름 → PV 안 경로, 바디는 PV 주소 + run_as_root:true(쌍 필터 없음)", async () => {
  renderAs(meUser);
  expect(await screen.findByText(/모든 작업은 root 로 실행됩니다/)).toBeInTheDocument();
  await pick("소스 namespace (StorageClass)", "소스 PV 이름", "team-a (tb-gpfs)", "models — 1Gi · Bound");
  await userEvent.type(screen.getByLabelText("소스 PV 안 경로"), "sample");
  // 「실제 경로」 = 서버가 해석한 abs_root + 하위경로, 「n분 전 기준」 = 그 클러스터의 마지막 성공 동기화.
  expect(screen.getByText(`실제 경로: /k8s_pv/gpfs/${GPFS_A}/${GPFS_A}-data/sample`)).toBeInTheDocument();
  expect(screen.getAllByText(/3분 전/).length).toBeGreaterThan(0);
  await pick("목적지 namespace (StorageClass)", "목적지 PV 이름", "team-b (tb-nfs)", "scratch — 5Gi · Bound");
  await userEvent.type(screen.getByLabelText("목적지 PV 안 경로"), "in");
  // 요약: 사람이 읽는 표기 + 실제 경로 + 실행 권한 root
  expect(screen.getByText("models — team-a (tb-gpfs) / sample")).toBeInTheDocument();
  expect(screen.getByText("scratch — team-b (tb-nfs) / in")).toBeInTheDocument();
  // PV 목적지 root sync: 결과 소유 = 목적지 스토리지 기본 소유자(2026-10-26 사용자 결정 -- 사용자는 늘 자동) -- 요약·소유권 안내
  // 둘 다 그 값(서버가 해석한 VolumeRow.storage_owner)을 말하고, 사용자에겐 결과 소유 조작이 없다.
  expect(screen.getByText(PRIV_ROOT_SYNC_PV)).toBeInTheDocument();
  expect(screen.getByRole("note", { name: "목적지 조건과 소유권" }).textContent)
    .toContain("결과는 목적지 스토리지 기본 소유자 1500:1500 로 맞춰집니다");
  expect(screen.getByText("목적지 스토리지 기본 소유자 1500:1500", { selector: "dd" })).toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "결과 소유" })).toBeNull();
  // LDAP·root 선택 흔적이 없다
  expect(screen.queryByLabelText("root 권한으로 실행")).toBeNull();
  expect(screen.queryByText(/LDAP 보조 그룹/)).toBeNull();
  expect(screen.queryByLabelText("허용된 스토리지 조합")).toBeNull();
  expect(screen.queryByRole("group", { name: "소스 지정 방식" })).toBeNull();     // 직접 경로 전환은 면제자만
  expect(await submitAndBody()).toEqual({
    operation: "sync",
    source_pv: { cluster: "dmsk8s", namespace: "team-a", pvc: "models" }, source: "sample",
    destination_pv: { cluster: "dmsk8s", namespace: "team-b", pvc: "scratch" }, destination: "in",
    options: SYNC_DEFAULTS, run_as_root: true,
  });
});

test("사용자 연산 선택지는 me.user_operations -- scan 은 특권 운영자만이라 보이지 않는다(2026-10-26 (g))", async () => {
  renderAs(meUser);
  await screen.findByRole("option", { name: "rm" });
  const options = within(screen.getByLabelText("연산")).getAllByRole("option").map((o) => o.textContent);
  expect(options).toEqual(["sync", "rm"]);
});

test("사용자 rm: 하위경로를 적어야 제출된다 -- 바디는 pv + target + run_as_root", async () => {
  renderAs(meUser);
  await choose("rm");
  await pick("namespace (StorageClass)", "PV 이름", "team-b (tb-nfs)", "scratch — 5Gi · Bound");
  await userEvent.type(screen.getByLabelText("PV 안 경로"), "old/run1");
  expect(screen.getByText("실제 경로: /k8s_pv/nfs/team-b/scratch/old/run1")).toBeInTheDocument();
  expect(await submitAndBody()).toEqual({
    operation: "rm", pv: { cluster: "dmsk8s", namespace: "team-b", pvc: "scratch" }, target: "old/run1",
    options: { recursive: true }, run_as_root: true,
  });
});

test("PV 루트 rm 차단: '', '.', './' 는 PV 루트라 잠기고 이유를 보인다(서버 pv_root_rm_forbidden 미러)", async () => {
  renderAs(meUser);
  await choose("rm");
  await pick("namespace (StorageClass)", "PV 이름", "team-a (tb-gpfs)", "models — 1Gi · Bound");
  expect(submitButton()).toBeDisabled();
  expect(screen.getByText("rm 은 PV 안의 하위 경로가 필요합니다 — PV 전체(루트)는 삭제할 수 없습니다.")).toBeInTheDocument();
  expect(hints()).toHaveTextContent("삭제할 PV 안 경로를 입력하세요 — PV 전체는 삭제할 수 없습니다");
  const sub = screen.getByLabelText("PV 안 경로");
  for (const v of [".", "./", ".//."]) {
    await userEvent.clear(sub);
    await userEvent.type(sub, v);
    expect(screen.getByText("PV 전체(루트)는 삭제할 수 없습니다 — PV 안의 하위 경로를 적으세요")).toBeInTheDocument();
    expect(submitButton()).toBeDisabled();
    expect(screen.queryByText(/^실제 경로:/)).toBeNull();
  }
  await userEvent.clear(sub);
  await userEvent.type(sub, "../x");
  expect(screen.getByText("\"..\" 은 쓸 수 없습니다 — PV 밖을 가리킬 수 없습니다")).toBeInTheDocument();
  expect(submitButton()).toBeDisabled();
  await userEvent.clear(sub);
  await userEvent.type(sub, "./old");
  expect(submitButton()).toBeEnabled();
});

test("동기화가 실패 중이면 고른 PV 줄에 경고만 띄우고 제출은 막지 않는다(2026-10-26 (c))", async () => {
  server.use(http.get("/api/user/k8s/volumes", () => HttpResponse.json(userVolumes({
    clusters: [{ name: "dmsk8s", synced_at: ago(40), stale: false, status: "failing" }],
  }))));
  renderAs(meUser);
  await choose("rm");
  await pick("namespace (StorageClass)", "PV 이름", "team-a (tb-nfs)", "datasets — 2Gi · Bound");
  await userEvent.type(screen.getByLabelText("PV 안 경로"), "old");
  expect(screen.getByText(/목록이 오래됐을 수 있습니다\(동기화 실패 중\)/)).toBeInTheDocument();
  await waitFor(() => expect(submitButton()).toBeEnabled());
});

test("Bound 아닌 PV 는 고를 수 없다(「용량 · 상태」 + 이유), namespace 가 하나면 자동으로 골라진다", async () => {
  server.use(http.get("/api/user/k8s/volumes", () => HttpResponse.json(userVolumes({
    namespaces: [{ cluster: "dmsk8s", namespace: "team-a", access: "admin" }],
    volumes: [A_MODELS, { ...A_PENDING, storage_class: "tb-gpfs" }],
  }))));
  renderAs(meUser);
  await choose("rm");
  const group = await screen.findByLabelText("namespace (StorageClass)");
  await waitFor(() => expect(group).toHaveValue(JSON.stringify(["dmsk8s", "team-a", "tb-gpfs"])));
  const pv = screen.getByLabelText("PV 이름");
  expect(within(pv).getByRole("option", { name: "checkpoints — 용량 모름 · Pending (Bound 아님)" })).toBeDisabled();
  expect(within(pv).getByRole("option", { name: "models — 1Gi · Bound" })).toBeEnabled();
});

test("빈 상태 1 -- 권한 namespace 0개: rolebinding <ID>-clusterrole-edit|admin 안내, 제출 잠금", async () => {
  server.use(http.get("/api/user/k8s/volumes", () => HttpResponse.json(userVolumes({ namespaces: [], volumes: [] }))));
  renderAs(meUser);
  expect(await screen.findByText(
    "rolebinding junwoo-song-clusterrole-edit|admin(또는 namespace 소유자 namespaceAdmin)이 있는 namespace 가 없습니다 — 관리자에게 요청하세요"))
    .toBeInTheDocument();
  expect(screen.queryByText(/아직 k8s 권한·PV 목록이 동기화되지 않았습니다/)).toBeNull();
  expect(screen.queryByText(/PV 목록을 불러오지 못했습니다/)).toBeNull();
  expect(screen.getByLabelText("소스 namespace (StorageClass)")).toBeDisabled();
  expect(submitButton()).toBeDisabled();
  expect(hints()).toHaveTextContent("권한 있는 namespace 가 없어 지금은 PV 를 고를 수 없습니다");
});

test("빈 상태 2 -- 아직 동기화 안 됨(synced_at null): 0개와 다른 문구(모름 ≠ 없음)", async () => {
  server.use(http.get("/api/user/k8s/volumes", () => HttpResponse.json(userVolumes({
    clusters: [{ name: "dmsk8s", synced_at: null, stale: false, status: "never" }], namespaces: [], volumes: [],
  }))));
  renderAs(meUser);
  expect(await screen.findByText(
    "아직 k8s 권한·PV 목록이 동기화되지 않았습니다 — 관리자가 클러스터 동기화를 마치면 PV 를 고를 수 있습니다.")).toBeInTheDocument();
  expect(screen.queryByText(/이 있는 namespace 가 없습니다/)).toBeNull();
  expect(hints()).toHaveTextContent("k8s 목록이 아직 동기화되지 않아 PV 를 고를 수 없습니다");
});

test("빈 상태 3 -- 조회 실패: 실패 문구(권한 없음·미동기화와 다르다)", async () => {
  server.use(http.get("/api/user/k8s/volumes", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
  renderAs(meUser);
  expect(await screen.findByText(/^PV 목록을 불러오지 못했습니다: /)).toBeInTheDocument();
  expect(screen.queryByText(/이 있는 namespace 가 없습니다/)).toBeNull();
  expect(screen.queryByText(/동기화되지 않았습니다/)).toBeNull();
  expect(submitButton()).toBeDisabled();
  expect(hints()).toHaveTextContent("PV 목록 조회 실패 — 새로고침 후 다시 시도하세요");
});

test("?pv=<cluster>/<ns>/<pvc> 프리필: 소스와 대상(rm)에 그 PV 가 골라져 있다", async () => {
  renderAs(meUser, "/jobs/new?pv=dmsk8s/team-b/scratch");
  const pv = await screen.findByLabelText("소스 PV 이름");
  await waitFor(() => expect((pv as HTMLSelectElement).selectedOptions[0]?.textContent).toBe("scratch — 5Gi · Bound"));
  expect(screen.getByLabelText("소스 namespace (StorageClass)")).toHaveValue(
    JSON.stringify(["dmsk8s", "team-b", "tb-nfs"]));
  expect(screen.getByText("실제 경로: /k8s_pv/nfs/team-b/scratch (PV 전체)")).toBeInTheDocument();
  await choose("rm");
  await waitFor(() => expect((screen.getByLabelText("PV 이름") as HTMLSelectElement).selectedOptions[0]?.textContent)
    .toBe("scratch — 5Gi · Bound"));
});

test("?pv= 가 권한 목록 밖이면 고른 것처럼 보이지 않고 이유를 말한다", async () => {
  renderAs(meUser, "/jobs/new?pv=dmsk8s/team-c/archive");
  await choose("rm");
  expect(await screen.findByText(/고른 PV\(team-c\/archive · dmsk8s\)를 목록에서 찾을 수 없습니다/)).toBeInTheDocument();
  expect(submitButton()).toBeDisabled();
  expect(hints()).toHaveTextContent("고른 PV 를 쓸 수 없습니다 — 다시 고르세요");
});

// ---- 목록 밖 관리자(맵핑 범위 + 관리자 옵션) --------------------------------------------------------------------

test("목록 밖 관리자: 사용자 흐름 + 안내 한 줄, 직접 경로·권한 기준 사용자 없음, sync/scan/rm 바디", async () => {
  renderAs(meAdminListed);
  expect(await screen.findByText(/직접 경로·사용자 지정은 계정 화면의 「전체 PV·직접 경로 권한」이 필요합니다/))
    .toBeInTheDocument();
  expect(screen.queryByLabelText("권한 기준 사용자")).toBeNull();
  expect(screen.queryByRole("group", { name: "소스 지정 방식" })).toBeNull();
  expect(screen.queryByLabelText("실행 신원(선택)")).toBeNull();
  expect(screen.queryByLabelText("root 권한으로 실행")).toBeNull();
  await pick("소스 namespace (StorageClass)", "소스 PV 이름", "team-a (tb-gpfs)", "models — 1Gi · Bound");
  await pick("목적지 namespace (StorageClass)", "목적지 PV 이름", "team-b (tb-gpfs)", "models — 1Gi · Bound");
  await userEvent.type(screen.getByLabelText("목적지 PV 안 경로"), "copy");
  expect(await submitAndBody()).toEqual({
    operation: "sync",
    source_pv: { cluster: "dmsk8s", namespace: "team-a", pvc: "models" }, source: "",
    destination_pv: { cluster: "dmsk8s", namespace: "team-b", pvc: "models" }, destination: "copy",
    options: SYNC_DEFAULTS, run_as_root: true,
  });
});

test("목록 밖 관리자 rm 바디(우선순위 지정 포함) -- scan 은 선택지에 없다(특권 운영자만)", async () => {
  const probe = renderAs(meAdminListed);
  await screen.findByRole("option", { name: "rm" });
  expect(within(screen.getByLabelText("연산")).queryByRole("option", { name: "scan" })).toBeNull();
  probe.unmount();
  for (const op of ["rm"] as const) {
    posted = [];
    const view = renderAs(meAdminListed);
    await choose(op);
    await pick("namespace (StorageClass)", "PV 이름", "team-a (tb-nfs)", "datasets — 2Gi · Bound");
    await userEvent.type(screen.getByLabelText("PV 안 경로"), "x");
    await userEvent.selectOptions(screen.getByLabelText("우선순위"), "high");
    const body = await submitAndBody();
    expect(body).toEqual({
      operation: op, pv: { cluster: "dmsk8s", namespace: "team-a", pvc: "datasets" }, target: "x",
      options: { recursive: true },
      priority: "high", run_as_root: true,
    });
    view.unmount();
  }
});

// ---- 면제 관리자(전체 PV·직접 경로 권한) --------------------------------------------------------------------------

test("면제 관리자: 권한 기준 사용자 비움 = 전체 PV 검색(상한 50), 목적지는 직접 경로(파일시스템·둘 다만) -- 혼합 바디", async () => {
  renderAs(meExempt);
  const search = await screen.findByLabelText("소스 PV 검색");
  await userEvent.type(search, "archive");
  const results = await screen.findByRole("radiogroup", { name: "소스 검색 결과 목록" });
  await waitFor(() => expect(within(results).getAllByRole("radio")).toHaveLength(1));
  await userEvent.click(within(results).getByRole("radio"));
  // 고른 PV 재해석은 이름 정확 일치(pvc=) — 부분 일치 q 는 같은 namespace 에 겹치는 이름이 많으면 상한에 잘렸다(2026-10-26).
  await waitFor(() => expect(adminVolumeUrls.some((u) => u.includes("namespace=team-c") && u.includes("pvc=archive")))
    .toBe(true));
  expect(await screen.findByText("실제 경로: /k8s_pv/nfs/team-c/archive (PV 전체)")).toBeInTheDocument();
  const mode = screen.getByRole("group", { name: "목적지 지정 방식" });
  await userEvent.click(within(mode).getByRole("button", { name: "직접 경로" }));
  const storage = screen.getByLabelText("목적지 스토리지");
  await within(storage).findByRole("option", { name: "cephfs-dms" });
  // PV 전용 스토리지(k8s-pv-nfs)는 직접 경로 선택지에 없다(설계 D11) -- 둘 다(k8s-pv-gpfs)는 있다.
  expect(within(storage).queryByRole("option", { name: "k8s-pv-nfs" })).toBeNull();
  expect(within(storage).getByRole("option", { name: "k8s-pv-gpfs" })).toBeInTheDocument();
  await userEvent.selectOptions(storage, "cephfs-dms");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "backup/archive");
  expect(screen.getByText("실제 경로: /cephfs/managed/backup/archive")).toBeInTheDocument();
  expect(await submitAndBody()).toEqual({
    operation: "sync",
    source_pv: { cluster: "dmsk8s", namespace: "team-c", pvc: "archive" }, source: "",
    destination_storage: "cephfs-dms", destination: "backup/archive",
    options: SYNC_DEFAULTS, run_as_root: true,
  });
});

test("면제 관리자: 권한 기준 사용자를 채우면 그 사용자 범위(?user=)의 고르기 흐름으로 바뀌고 owner_username 이 실린다", async () => {
  renderAs(meExempt);
  await choose("scan");
  expect(await screen.findByLabelText("PV 검색")).toBeInTheDocument();
  expect(screen.queryByLabelText("namespace (StorageClass)")).toBeNull();
  const scope = screen.getByLabelText("권한 기준 사용자");
  await userEvent.type(scope, "cocoa.song");
  // 범위 전환: 검색칸이 사라지고 namespace → PV 흐름(그 사용자 범위만)
  const group = await screen.findByLabelText("namespace (StorageClass)");
  expect(screen.queryByLabelText("PV 검색")).toBeNull();
  await waitFor(() => expect(adminVolumeUrls.some((u) => u.includes("user=cocoa.song"))).toBe(true));
  await waitFor(() => expect(within(group).getAllByRole("option").map((o) => o.textContent))
    .toEqual(["선택하세요", "team-b (tb-gpfs)", "team-b (tb-nfs)"]));
  await pick("namespace (StorageClass)", "PV 이름", "team-b (tb-nfs)", "scratch — 5Gi · Bound");
  expect(screen.getByText("cocoa.song", { selector: "dd" })).toBeInTheDocument();       // 요약 「대상 사용자」
  expect(await submitAndBody()).toEqual({
    operation: "scan", pv: { cluster: "dmsk8s", namespace: "team-b", pvc: "scratch" }, target: "",
    options: { batch_files: 1000000, broken_limit: 100 }, owner_username: "cocoa.song", run_as_root: true,
  });
});

test("면제 관리자: 권한 기준 사용자 형식이 틀리면 잠근다, rm 은 직접 경로로도 된다", async () => {
  renderAs(meExempt);
  await choose("rm");
  await userEvent.type(await screen.findByLabelText("권한 기준 사용자"), "Bad User");
  expect(screen.getByText(/계정 아이디 형식이 아닙니다/)).toBeInTheDocument();
  await userEvent.clear(screen.getByLabelText("권한 기준 사용자"));
  const mode = screen.getByRole("group", { name: "대상 지정 방식" });
  await userEvent.click(within(mode).getByRole("button", { name: "직접 경로" }));
  const storage = screen.getByLabelText("스토리지");
  await within(storage).findByRole("option", { name: "cephfs-dms" });
  await userEvent.selectOptions(storage, "cephfs-dms");
  await userEvent.type(screen.getByLabelText("대상 경로"), "tmp/old");
  expect(await submitAndBody()).toEqual({
    operation: "rm", storage: "cephfs-dms", target: "tmp/old", options: { recursive: true }, run_as_root: true,
  });
});

test("면제 관리자 검색 결과가 상한을 넘으면 좁히라고 말한다", async () => {
  server.use(http.get("/api/admin/k8s/volumes", () => HttpResponse.json({
    clusters: FRESH, truncated: true, volumes: [A_MODELS, A_DATASETS],
  })));
  renderAs(meExempt);
  await choose("scan");
  expect(await screen.findByText("결과가 50개를 넘어 앞의 50개만 보입니다 — 검색어를 좁히세요.")).toBeInTheDocument();
});

// ---- 결과 소유(2026-10-26 사용자 결정: 스토리지 기본 uid:gid -- 사용자는 늘 자동, 운영자는 옵션으로 PV 루트 소유자) ----------

const ownerCard = () => screen.getByRole("note", { name: "목적지 조건과 소유권" });
const summaryValue = (label: string) => screen.getByText(label, { selector: "dt" }).nextElementSibling as HTMLElement;

test("사용자: 기본 소유 0:0 은 「0:0(root)」로 실제 값을 말하고, PV 루트 목적지면 PV 루트 자체가 그 소유로 chown 된다고 경고한다", async () => {
  renderAs(meUser);
  await pick("소스 namespace (StorageClass)", "소스 PV 이름", "team-a (tb-nfs)", "datasets — 2Gi · Bound");
  await pick("목적지 namespace (StorageClass)", "목적지 PV 이름", "team-b (tb-gpfs)", "models — 1Gi · Bound");
  // 하위경로를 비우면 PV 루트 -- 막지 않고 경고만(제출은 된다)
  expect(screen.getByText(
    "목적지가 PV 전체(루트)라 PV 루트 디렉터리 자체가 스토리지 기본 소유자 0:0(root) 로 chown 되고, 권한·시각은 소스에 맞춰 바뀝니다 — 하위 디렉터리로 받으려면 경로를 적으세요."))
    .toBeInTheDocument();
  expect(summaryValue("목적지 소유")).toHaveTextContent("목적지 스토리지 기본 소유자 0:0(root)");
  expect(ownerCard()).toHaveTextContent("결과는 목적지 스토리지 기본 소유자 0:0(root) 로 맞춰집니다");
  expect(ownerCard()).toHaveTextContent("그 안의 같은 경로 항목은 목적지 스토리지 기본 소유자와 소스의 권한·시각으로 다시 맞춰집니다");
  expect(screen.queryByRole("group", { name: "결과 소유" })).toBeNull();
  // 기본 바디는 이 기능 전과 같다 -- pv_owner 를 싣지 않는다(서버가 owner_rule:"storage" 를 박는다)
  expect(await submitAndBody()).toEqual({
    operation: "sync",
    source_pv: { cluster: "dmsk8s", namespace: "team-a", pvc: "datasets" }, source: "",
    destination_pv: { cluster: "dmsk8s", namespace: "team-b", pvc: "models" }, destination: "",
    options: SYNC_DEFAULTS, run_as_root: true,
  });
});

test("목적지 스토리지의 기본 소유 값이 올바르지 않으면(storage_owner null) 보내기 전에 잠근다(서버 409 invalid_storage_owner)", async () => {
  server.use(http.get("/api/user/k8s/volumes", () => HttpResponse.json(userVolumes({
    volumes: [A_DATASETS, A_MODELS, { ...B_SCRATCH, storage_owner: null }],
  }))));
  renderAs(meUser);
  await pick("소스 namespace (StorageClass)", "소스 PV 이름", "team-a (tb-gpfs)", "models — 1Gi · Bound");
  await pick("목적지 namespace (StorageClass)", "목적지 PV 이름", "team-b (tb-nfs)", "scratch — 5Gi · Bound");
  await userEvent.type(screen.getByLabelText("목적지 PV 안 경로"), "in");
  expect(submitButton()).toBeDisabled();
  expect(hints()).toHaveTextContent("목적지 스토리지의 기본 소유 값이 올바르지 않습니다 — 관리자에게 스토리지 설정 확인을 요청하세요");
  expect(ownerCard()).toHaveTextContent("이 PV 의 스토리지에 저장된 기본 소유 값이 올바르지 않아 제출할 수 없습니다");
  expect(summaryValue("목적지 소유")).toHaveTextContent("목적지 스토리지 기본 소유자(uid:gid)");
});

async function exemptPick(side: "소스" | "목적지", q: string) {
  await userEvent.type(await screen.findByLabelText(`${side} PV 검색`), q);
  const results = await screen.findByRole("radiogroup", { name: `${side} 검색 결과 목록` });
  await waitFor(() => expect(within(results).getAllByRole("radio")).toHaveLength(1));
  await userEvent.click(within(results).getByRole("radio"));
}

test("운영자(면제자): 「결과 소유」 기본은 스토리지 기본 -- 「PV 루트 소유자」를 고르면 문구·요약·경고가 바뀌고 바디에 pv_owner:\"pv_root\"", async () => {
  renderAs(meExempt);
  await exemptPick("소스", "archive");
  await exemptPick("목적지", "scratch");
  const group = await screen.findByRole("group", { name: "결과 소유" });
  const storageBtn = await within(group).findByRole("button", { name: "스토리지 기본" });
  // 값은 버튼이 아니라 도움말에(Segmented 는 한 줄 -- 값이 길면 반폭 열을 넘쳐 도움말과 겹쳤다, 리뷰)
  expect(screen.getByText(/기본은 목적지 스토리지 기본 소유자 1500:1500입니다/)).toBeInTheDocument();
  const rootBtn = within(group).getByRole("button", { name: "PV 루트 소유자" });
  expect(storageBtn).toHaveAttribute("aria-pressed", "true");
  expect(summaryValue("목적지 소유")).toHaveTextContent("목적지 스토리지 기본 소유자 1500:1500");
  expect(screen.getByText(PRIV_ROOT_SYNC_PV)).toBeInTheDocument();
  await userEvent.click(rootBtn);
  expect(rootBtn).toHaveAttribute("aria-pressed", "true");
  expect(summaryValue("목적지 소유")).toHaveTextContent("목적지 PV 루트의 소유자·그룹(점검 뒤 확정 — 운영자 지정)");
  expect(screen.getByText(PRIV_ROOT_SYNC_PV_ROOT)).toBeInTheDocument();
  expect(ownerCard()).toHaveTextContent("결과 소유를 PV 루트 소유자로 골랐습니다(특권 운영자 옵션 — 기본은 목적지 스토리지 기본 소유자)");
  expect(ownerCard()).toHaveTextContent("그 안의 같은 경로 항목은 목적지 PV 루트의 소유자·그룹과 소스의 권한·시각으로 다시 맞춰집니다");
  // PV 루트 목적지(하위경로 비움) 경고도 그 규칙: PV 루트 소유자는 그대로
  expect(screen.getByText(/PV 루트 디렉터리의 권한·시각이 소스에 맞춰 바뀝니다\(소유자는 PV 루트 소유자 그대로\)/)).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("목적지 PV 안 경로"), "in");
  expect(await submitAndBody()).toEqual({
    operation: "sync",
    source_pv: { cluster: "dmsk8s", namespace: "team-c", pvc: "archive" }, source: "",
    destination_pv: { cluster: "dmsk8s", namespace: "team-b", pvc: "scratch" }, destination: "in",
    options: SYNC_DEFAULTS, pv_owner: "pv_root", run_as_root: true,
  });
});

test("운영자: 스토리지 기본을 그대로 두면 바디는 기본 바디와 같다(pv_owner 없음)", async () => {
  renderAs(meExempt);
  await exemptPick("소스", "archive");
  await exemptPick("목적지", "scratch");
  await screen.findByRole("group", { name: "결과 소유" });
  await userEvent.type(screen.getByLabelText("목적지 PV 안 경로"), "in");
  const body = await submitAndBody() as Record<string, unknown>;
  expect(body).not.toHaveProperty("pv_owner");
  expect(body.destination_pv).toEqual({ cluster: "dmsk8s", namespace: "team-b", pvc: "scratch" });
});

test("운영자: chown 을 지정하면 결과 소유 선택이 잠기고 이유를 말하며, PV 루트를 골라 뒀어도 pv_owner 를 싣지 않는다", async () => {
  renderAs(meExempt);
  await exemptPick("소스", "archive");
  await exemptPick("목적지", "scratch");
  const group = await screen.findByRole("group", { name: "결과 소유" });
  await userEvent.click(within(group).getByRole("button", { name: "PV 루트 소유자" }));
  await userEvent.type(screen.getByLabelText("chown"), "7:7");
  for (const b of within(group).getAllByRole("button")) expect(b).toBeDisabled();
  expect(screen.getByText("chown 을 지정해 결과는 chown 지정값(7:7)으로 셋업됩니다 — 결과 소유 선택은 쓰이지 않습니다.")).toBeInTheDocument();
  expect(summaryValue("목적지 소유")).toHaveTextContent("chown 지정값 7:7");
  // PV 루트 목적지 경고도 chown 값으로
  expect(screen.getByText(/PV 루트 디렉터리 자체가 chown 지정값 7:7 소유로 바뀌고/)).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("목적지 PV 안 경로"), "in");
  const body = await submitAndBody() as Record<string, unknown>;
  expect(body).not.toHaveProperty("pv_owner");
  expect(body.options).toEqual({ ...SYNC_DEFAULTS, chown: "7:7" });
});

test("운영자: 목적지가 직접 경로면 결과 소유 선택이 없다(소스 소유 보존 -- 범위 밖)", async () => {
  renderAs(meExempt);
  await exemptPick("소스", "archive");
  expect(await screen.findByRole("group", { name: "결과 소유" })).toBeInTheDocument();   // PV 목적지(기본)면 보인다
  const mode = screen.getByRole("group", { name: "목적지 지정 방식" });
  await userEvent.click(within(mode).getByRole("button", { name: "직접 경로" }));
  expect(screen.queryByRole("group", { name: "결과 소유" })).toBeNull();
  expect(summaryValue("목적지 소유")).toHaveTextContent("소스의 소유자·그룹 그대로(root 실행)");
});

// 2026-10-26 사용자 결정 (4): 직접 경로 목적지의 스토리지가 PV 를 담는 종류면 그 스토리지 기본 소유자(서버 k8s_access.direct_dest_owner
// -- both 는 경로가 그 스토리지의 PV 루트 아래일 때만, 화면은 PV 루트를 모른다). 기본 소유는 관리자 응답의 default_owner.
test("운영자: 직접 경로 목적지가 PV·파일시스템 겸용 스토리지면 「PV 루트 아래면 스토리지 기본 소유자」, 파일시스템이면 소스 그대로", async () => {
  server.use(http.get("/api/user/storages", () => HttpResponse.json(STORAGES.map((s) => ({
    ...s, default_owner: s.storage_name === "k8s-pv-gpfs" ? "2500:2600" : "0:0" })))));
  renderAs(meExempt);
  await exemptPick("소스", "archive");
  const mode = screen.getByRole("group", { name: "목적지 지정 방식" });
  await userEvent.click(within(mode).getByRole("button", { name: "직접 경로" }));
  const storage = screen.getByLabelText("목적지 스토리지");
  await within(storage).findByRole("option", { name: "k8s-pv-gpfs" });
  await userEvent.selectOptions(storage, "k8s-pv-gpfs");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "backup/x");
  expect(summaryValue("목적지 소유"))
    .toHaveTextContent("PV 루트 아래면 목적지 스토리지 기본 소유자 2500:2600, 아니면 소스 그대로");
  expect(summaryValue("실행 권한")).toHaveTextContent(PRIV_ROOT_SYNC_DIRECT_BOTH);
  expect(ownerCard()).toHaveTextContent("어느 쪽인지는 제출 때 서버가 정해 요청 상세의 「목적지 소유」에 보입니다");
  expect(ownerCard()).toHaveTextContent("결과 소유(PV 루트 아래면 목적지 스토리지 기본 소유자, 아니면 소스 소유)로 다시 맞춰집니다");
  // 파일시스템 스토리지로 바꾸면 종전(소스 소유 보존)
  await userEvent.selectOptions(storage, "cephfs-dms");
  expect(summaryValue("목적지 소유")).toHaveTextContent("소스의 소유자·그룹 그대로(root 실행)");
  expect(summaryValue("실행 권한")).not.toHaveTextContent("PV 루트 아래면");
  // 바디는 그대로다(판정은 서버가 제출 때 한다)
  expect(await submitAndBody()).toEqual({
    operation: "sync", source_pv: { cluster: "dmsk8s", namespace: "team-c", pvc: "archive" }, source: "",
    destination_storage: "cephfs-dms", destination: "backup/x", options: SYNC_DEFAULTS, run_as_root: true,
  });
});

test("운영자: 겸용 스토리지의 기본 소유 값이 올바르지 않아도 직접 경로는 잠그지 않는다(PV 루트 밖이면 서버가 받는다), 문구는 값 없이", async () => {
  server.use(http.get("/api/user/storages", () => HttpResponse.json(STORAGES.map((s) => ({ ...s, default_owner: null })))));
  renderAs(meExempt);
  await exemptPick("소스", "archive");
  await userEvent.click(within(screen.getByRole("group", { name: "목적지 지정 방식" })).getByRole("button", { name: "직접 경로" }));
  const storage = screen.getByLabelText("목적지 스토리지");
  await within(storage).findByRole("option", { name: "k8s-pv-gpfs" });
  await userEvent.selectOptions(storage, "k8s-pv-gpfs");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "backup/x");
  expect(summaryValue("목적지 소유"))
    .toHaveTextContent("PV 루트 아래면 목적지 스토리지 기본 소유자(uid:gid), 아니면 소스 그대로");
  await waitFor(() => expect(submitButton()).toBeEnabled());
});
