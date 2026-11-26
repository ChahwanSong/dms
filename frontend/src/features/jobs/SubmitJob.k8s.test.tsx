import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, beforeEach, expect, test } from "vitest";
import { SubmitJob } from "./SubmitJob";
import { HELP_SUMMARY, SYNC_INT_FIELDS, SYNC_OPTION_HELP } from "./optionRules";
import { PRIV_ROOT_HEAD, PRIV_ROOT_SYNC_DIRECT_BOTH, PRIV_ROOT_SYNC_PV, PRIV_ROOT_SYNC_PV_ROOT } from "./optionRules";
import { REASON_MESSAGES } from "../../lib/api";
import type { Me, UserStorage } from "../../lib/types";
import { chooseOp, goStep, next, nextButton, reasonLine, stepCard, stepHeading, toReview } from "../../test/wizard";
import type { ClusterFreshness, UserVolumesResponse, VolumeRow, VolumesResponse } from "../k8s/types";

// dms-k8s 설계 §7(2026-10-25): k8s 모드 단일 작업 -- 세 역할(일반 사용자 · 목록 밖 관리자 · 면제 관리자) × sync/scan/rm 의 제출
// 바디, 빈 상태 문구 셋, PV 루트 rm 차단, ?pv= 프리필, 사용자 지정 범위 전환. 서버는 MSW 모의(me 의 identity_mode·unrestricted·
// user_operations 는 다른 갈래가 같은 때 만든다 -- 계약은 설계 §2 D4·D15).
// 2026-11-26 단계별 등록: 연산 sync·rm 이상이면 ① 작업 종류에서 시작한다. 단계 안의 잠김 이유는 바 이유 줄(reasonLine)이, 검토의
// 전체 목록은 hints()(검토 단계에만 그려진다)가 말한다. 바디 toEqual 은 그대로다.

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
const REVIEW = "검토·제출";

async function pick(groupLabel: string, pvLabel: string, group: string, pv: string) {
  const g = await screen.findByLabelText(groupLabel);
  await within(g).findByRole("option", { name: group });
  await userEvent.selectOptions(g, group);
  await userEvent.selectOptions(screen.getByLabelText(pvLabel), within(screen.getByLabelText(pvLabel))
    .getByRole("option", { name: pv }));
}

// ① 연산 카드만 고른다(단계는 그대로). 라디오 이름 = 연산 이름.
async function pickOp(op: "sync" | "scan" | "rm") {
  await chooseOp(op);
}
const opNames = () => within(screen.getByRole("radiogroup", { name: "연산" })).getAllByRole("radio")
  .map((r) => r.getAttribute("aria-label"));

// ① 연산을 고르고 ② 대상으로 간다.
async function choose(op: "scan" | "rm") {
  await pickOp(op);
  await next(op === "scan" ? "스캔 대상" : "삭제 대상");
}

// ① → ② sync(연산 기본). me 로드를 기다린다.
async function toSync() {
  await screen.findByRole("radio", { name: "rm" });
  await next("소스와 목적지");
}

// 검토까지 가서 제출하고 바디를 돌려준다.
async function submitAndBody() {
  if (stepHeading().textContent !== REVIEW)
    await waitFor(() => expect(nextButton()).not.toHaveAttribute("aria-disabled"));   // 고른 PV 해석(비동기)을 기다린다
  await toReview(REVIEW);
  await waitFor(() => expect(submitButton()).toBeEnabled());
  await userEvent.click(submitButton());
  await screen.findByRole("heading", { name: "요청 상세" });
  return posted[0];
}

// 막힌 「다음」(aria-disabled + 바 이유 줄).
function expectBlocked(reason: string) {
  expect(nextButton()).toHaveAttribute("aria-disabled", "true");
  expect(reasonLine()).toHaveTextContent(reason);
}

// ---- 일반 사용자 ---------------------------------------------------------------------------------------------

test("사용자 sync: namespace (StorageClass) → PV 이름 → PV 안 경로, 바디는 PV 주소 + run_as_root:true(쌍 필터 없음)", async () => {
  renderAs(meUser);
  expect(await screen.findByText(/모든 작업은 root 로 실행됩니다/)).toBeInTheDocument();
  await toSync();
  await pick("소스 namespace (StorageClass)", "소스 PV 이름", "team-a (tb-gpfs)", "models — 1Gi · Bound");
  await userEvent.type(screen.getByLabelText("소스 PV 안 경로"), "sample");
  // 「지정 경로」 = 서버가 해석한 abs_root + 하위경로(글자 그대로 -- d192 이식 때 「실제 경로」에서 개명), 「n분 전 기준」 = 그 클러스터의 마지막 성공 동기화.
  expect(screen.getByText(`지정 경로: /k8s_pv/gpfs/${GPFS_A}/${GPFS_A}-data/sample`)).toBeInTheDocument();
  expect(screen.getAllByText(/3분 전/).length).toBeGreaterThan(0);
  await pick("목적지 namespace (StorageClass)", "목적지 PV 이름", "team-b (tb-nfs)", "scratch — 5Gi · Bound");
  await userEvent.type(screen.getByLabelText("목적지 PV 안 경로"), "in");
  // ② 목적지 한 줄: 결과 소유 = 목적지 스토리지 기본 소유자(2026-10-26 사용자 결정 -- 사용자는 늘 자동), 사용자에겐 조작이 없다.
  expect(summaryValue("목적지 소유")).toHaveTextContent("목적지 스토리지 기본 소유자 1500:1500");
  expect(screen.queryByRole("group", { name: "결과 소유" })).toBeNull();
  // LDAP·root 선택 흔적이 없다
  expect(screen.queryByLabelText("root 권한으로 실행")).toBeNull();
  expect(screen.queryByText(/LDAP 보조 그룹/)).toBeNull();
  expect(screen.queryByLabelText("허용된 스토리지 조합")).toBeNull();
  expect(screen.queryByRole("group", { name: "소스 지정 방식" })).toBeNull();     // 직접 경로 전환은 면제자만
  // ③ 사용자 옵션 -- 실행 설정(우선순위)은 관리자 전용
  await next("옵션");
  expect(screen.queryByLabelText("우선순위")).toBeNull();
  // ④ 요약: 사람이 읽는 표기 + 지정 경로 + 실행 권한 root + 우선순위는 정책 기본(사용자는 정책 API 를 부르지 않는다)
  await next(REVIEW);
  expect(screen.getByText("models — team-a (tb-gpfs) / sample")).toBeInTheDocument();
  expect(screen.getByText("scratch — team-b (tb-nfs) / in")).toBeInTheDocument();
  expect(screen.getByText(PRIV_ROOT_SYNC_PV)).toBeInTheDocument();
  expect(screen.getByRole("note", { name: "목적지 조건과 소유권" }).textContent)
    .toContain("결과는 목적지 스토리지 기본 소유자 1500:1500 로 맞춰집니다");
  expect(screen.getByText("목적지 스토리지 기본 소유자 1500:1500", { selector: "dd" })).toBeInTheDocument();
  expect(summaryValue("우선순위")).toHaveTextContent("(정책 기본)");
  expect(await submitAndBody()).toEqual({
    operation: "sync",
    source_pv: { cluster: "dmsk8s", namespace: "team-a", pvc: "models" }, source: "sample",
    destination_pv: { cluster: "dmsk8s", namespace: "team-b", pvc: "scratch" }, destination: "in",
    options: SYNC_DEFAULTS, run_as_root: true,
  });
});

test("사용자 연산 선택지는 me.user_operations -- scan 은 특권 운영자만이라 보이지 않는다(2026-10-26 (g)), 연산이 둘이면 ①에서 시작", async () => {
  renderAs(meUser);
  await screen.findByRole("radio", { name: "rm" });
  expect(stepHeading()).toHaveTextContent("작업 종류");
  expect(opNames()).toEqual(["sync", "rm"]);
});

test("비관리자 sync 옵션은 delete·contents 만 — direct·quiet·고급·우선순위 숨김", async () => {
  // 사용자 결정(2026-08-20): 사용자 단일작업 sync 폼은 최소만 노출한다.
  renderAs(meUser);
  await toSync();
  await pick("소스 namespace (StorageClass)", "소스 PV 이름", "team-a (tb-gpfs)", "models — 1Gi · Bound");
  await pick("목적지 namespace (StorageClass)", "목적지 PV 이름", "team-b (tb-nfs)", "scratch — 5Gi · Bound");
  await next("옵션");
  expect(screen.getByLabelText("delete")).toBeInTheDocument();
  // 토글 카드: 요약(원문 첫 문장)은 늘 보이고 전문은 접혀 있다가 켜면 펼쳐진다. delete 전문은 특수 파일의 같은 이름 목적지 항목을
  // 도구별로 말한다(d192 이식, 리뷰 portal-batch-26·contract-39 -- 사용자 폼도 한 노드(dsync)로 돌 수 있고, dsync 는 종류가 다른 그
  // 항목을 delete 와 무관하게 지운다)
  expect(screen.getByText(HELP_SUMMARY.delete)).toBeVisible();
  expect(SYNC_OPTION_HELP.delete.startsWith(HELP_SUMMARY.delete)).toBe(true);
  const deleteHelp = screen.getByText(SYNC_OPTION_HELP.delete);
  expect(deleteHelp).not.toBeVisible();
  await userEvent.click(screen.getByLabelText("delete"));
  expect(deleteHelp).toBeVisible();
  expect(deleteHelp).toHaveTextContent("노드 간 실행(nsync)은 대상의 같은 이름 항목을 그대로 두고, 한 노드 실행(dsync)은 그 "
    + "이름의 대상 항목이 다른 종류면 이 옵션과 무관하게 지운 뒤 아무것도 만들지 않아 그 이름이 대상에서 사라집니다");
  // contents 는 root 전용 어휘(「실행 신원이 읽을 수 없는」 아님 -- 모든 작업이 root)
  expect(screen.getByText(SYNC_OPTION_HELP.contents)).toHaveTextContent("읽을 수 없는 그런 파일(예: NFS root_squash)");
  expect(screen.getByText(HELP_SUMMARY.contents)).toBeVisible();
  expect(SYNC_OPTION_HELP.contents).not.toContain("실행 신원");
  expect(screen.getByLabelText("contents")).toBeInTheDocument();
  // 숨은 기본 옵션은 토큰으로 보인다(바디와 같은 syncOptions() -- 보이는 것 = 보내는 것)
  const auto = screen.getByText("기본으로 함께 실립니다:").parentElement!;
  for (const t of ["open_noatime", "batch_files=1000000", "bufsize=4194304"]) expect(within(auto).getByText(t)).toBeVisible();
  expect(within(auto).queryByText("delete")).toBeNull();
  expect(screen.queryByLabelText("direct")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("quiet")).not.toBeInTheDocument();
  expect(screen.queryByText("고급 옵션")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("우선순위")).not.toBeInTheDocument();
});

test("사용자 rm: 하위경로를 적어야 제출된다 -- 바디는 pv + target + run_as_root", async () => {
  renderAs(meUser);
  await choose("rm");
  await pick("namespace (StorageClass)", "PV 이름", "team-b (tb-nfs)", "scratch — 5Gi · Bound");
  await userEvent.type(screen.getByLabelText("PV 안 경로"), "old/run1");
  expect(screen.getByText("지정 경로: /k8s_pv/nfs/team-b/scratch/old/run1")).toBeInTheDocument();
  expect(await submitAndBody()).toEqual({
    operation: "rm", pv: { cluster: "dmsk8s", namespace: "team-b", pvc: "scratch" }, target: "old/run1",
    options: { recursive: true }, run_as_root: true,
  });
});

test("PV 루트 rm 차단: '', '.', './' 는 PV 루트라 잠기고 이유를 보인다(서버 pv_root_rm_forbidden 미러)", async () => {
  renderAs(meUser);
  await choose("rm");
  await pick("namespace (StorageClass)", "PV 이름", "team-a (tb-gpfs)", "models — 1Gi · Bound");
  expect(screen.getByText("rm 은 PV 안의 하위 경로가 필요합니다 — PV 전체(루트)는 삭제할 수 없습니다.")).toBeInTheDocument();
  expectBlocked("삭제할 PV 안 경로를 입력하세요 — PV 전체는 삭제할 수 없습니다");
  const sub = screen.getByLabelText("PV 안 경로");
  expect(sub).not.toHaveAttribute("aria-invalid");          // 빈 칸은 오류가 아니라 안내(아직 안 적었다)
  // 그래도 rm 의 가장 흔한 막힘이라 막힌 「다음」은 이 칸으로 포커스를 보낸다(빨강 없는 표지 data-wizard-nudge -- Wizard blockedNudge)
  expect(sub).toHaveAttribute("data-wizard-nudge", "true");
  await userEvent.click(nextButton());
  expect(stepHeading()).toHaveTextContent("삭제 대상");
  expect(sub).toHaveFocus();
  for (const v of [".", "./", ".//."]) {
    await userEvent.clear(sub);
    await userEvent.type(sub, v);
    expect(screen.getByText("PV 전체(루트)는 삭제할 수 없습니다 — PV 안의 하위 경로를 적으세요")).toBeInTheDocument();
    expect(nextButton()).toHaveAttribute("aria-disabled", "true");
    expect(sub).toHaveAttribute("aria-invalid", "true");
    expect(screen.queryByText(/^지정 경로:/)).toBeNull();
  }
  await userEvent.clear(sub);
  await userEvent.type(sub, "../x");
  expect(screen.getByText("\"..\" 은 쓸 수 없습니다 — PV 밖을 가리킬 수 없습니다")).toBeInTheDocument();
  expect(nextButton()).toHaveAttribute("aria-disabled", "true");
  await userEvent.clear(sub);
  await userEvent.type(sub, "./old");
  expect(nextButton()).not.toHaveAttribute("aria-disabled");
});

test("동기화가 실패 중이면 고른 PV 줄에 경고만 띄우고 진행은 막지 않는다(2026-10-26 (c))", async () => {
  server.use(http.get("/api/user/k8s/volumes", () => HttpResponse.json(userVolumes({
    clusters: [{ name: "dmsk8s", synced_at: ago(40), stale: false, status: "failing" }],
  }))));
  renderAs(meUser);
  await choose("rm");
  await pick("namespace (StorageClass)", "PV 이름", "team-a (tb-nfs)", "datasets — 2Gi · Bound");
  await userEvent.type(screen.getByLabelText("PV 안 경로"), "old");
  expect(screen.getByText(/목록이 오래됐을 수 있습니다\(동기화 실패 중\)/)).toBeInTheDocument();
  await waitFor(() => expect(nextButton()).not.toHaveAttribute("aria-disabled"));
  await toReview(REVIEW);
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

test("빈 상태 1 -- 권한 namespace 0개: rolebinding <ID>-clusterrole-edit|admin 안내, ② 잠금", async () => {
  server.use(http.get("/api/user/k8s/volumes", () => HttpResponse.json(userVolumes({ namespaces: [], volumes: [] }))));
  renderAs(meUser);
  await toSync();
  expect(await screen.findByText(
    "rolebinding junwoo-song-clusterrole-edit|admin(또는 namespace 소유자 namespaceAdmin)이 있는 namespace 가 없습니다 — 관리자에게 요청하세요"))
    .toBeInTheDocument();
  expect(screen.queryByText(/아직 k8s 권한·PV 목록이 동기화되지 않았습니다/)).toBeNull();
  expect(screen.queryByText(/PV 목록을 불러오지 못했습니다/)).toBeNull();
  expect(screen.getByLabelText("소스 namespace (StorageClass)")).toBeDisabled();
  expectBlocked("권한 있는 namespace 가 없어 지금은 PV 를 고를 수 없습니다");
});

test("빈 상태 2 -- 아직 동기화 안 됨(synced_at null): 0개와 다른 문구(모름 ≠ 없음)", async () => {
  server.use(http.get("/api/user/k8s/volumes", () => HttpResponse.json(userVolumes({
    clusters: [{ name: "dmsk8s", synced_at: null, stale: false, status: "never" }], namespaces: [], volumes: [],
  }))));
  renderAs(meUser);
  await toSync();
  expect(await screen.findByText(
    "아직 k8s 권한·PV 목록이 동기화되지 않았습니다 — 관리자가 클러스터 동기화를 마치면 PV 를 고를 수 있습니다.")).toBeInTheDocument();
  expect(screen.queryByText(/이 있는 namespace 가 없습니다/)).toBeNull();
  expectBlocked("k8s 목록이 아직 동기화되지 않아 PV 를 고를 수 없습니다");
});

test("빈 상태 3 -- 조회 실패: 실패 문구(권한 없음·미동기화와 다르다)", async () => {
  server.use(http.get("/api/user/k8s/volumes", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
  renderAs(meUser);
  await toSync();
  expect(await screen.findByText(/^PV 목록을 불러오지 못했습니다: /)).toBeInTheDocument();
  expect(screen.queryByText(/이 있는 namespace 가 없습니다/)).toBeNull();
  expect(screen.queryByText(/동기화되지 않았습니다/)).toBeNull();
  expectBlocked("PV 목록 조회 실패 — 새로고침 후 다시 시도하세요");
});

test("?pv=<cluster>/<ns>/<pvc> 프리필: ② 에서 시작하고(①은 완료), 소스와 대상(rm)에 그 PV 가 골라져 있다", async () => {
  renderAs(meUser, "/jobs/new?pv=dmsk8s/team-b/scratch");
  const pv = await screen.findByLabelText("소스 PV 이름");
  expect(stepHeading()).toHaveTextContent("소스와 목적지");
  expect(screen.getByRole("button", { name: /^작업 종류.*\(완료\)$/ })).toBeInTheDocument();
  await waitFor(() => expect((pv as HTMLSelectElement).selectedOptions[0]?.textContent).toBe("scratch — 5Gi · Bound"));
  expect(screen.getByLabelText("소스 namespace (StorageClass)")).toHaveValue(
    JSON.stringify(["dmsk8s", "team-b", "tb-nfs"]));
  expect(screen.getByText("지정 경로: /k8s_pv/nfs/team-b/scratch (PV 전체)")).toBeInTheDocument();
  await goStep("작업 종류");
  await choose("rm");
  await waitFor(() => expect((screen.getByLabelText("PV 이름") as HTMLSelectElement).selectedOptions[0]?.textContent)
    .toBe("scratch — 5Gi · Bound"));
});

test("?pv= 가 권한 목록 밖이면 고른 것처럼 보이지 않고 이유를 말한다", async () => {
  renderAs(meUser, "/jobs/new?pv=dmsk8s/team-c/archive");
  await screen.findByLabelText("소스 PV 이름");
  await goStep("작업 종류");
  await choose("rm");
  expect(await screen.findByText(/고른 PV\(team-c\/archive · dmsk8s\)를 목록에서 찾을 수 없습니다/)).toBeInTheDocument();
  expectBlocked("고른 PV 를 쓸 수 없습니다 — 다시 고르세요");
});

// ---- 목록 밖 관리자(맵핑 범위 + 관리자 옵션) --------------------------------------------------------------------

test("목록 밖 관리자: 사용자 흐름 + 안내 한 줄, 직접 경로·권한 기준 사용자 없음, sync/scan/rm 바디", async () => {
  renderAs(meAdminListed);
  expect(await screen.findByText(/직접 경로·사용자 지정은 계정 화면의 「전체 PV·직접 경로 권한」이 필요합니다/))
    .toBeInTheDocument();
  expect(screen.queryByLabelText("권한 기준 사용자")).toBeNull();
  expect(screen.queryByLabelText("실행 신원(선택)")).toBeNull();
  expect(screen.queryByLabelText("root 권한으로 실행")).toBeNull();
  await toSync();
  expect(screen.queryByRole("group", { name: "소스 지정 방식" })).toBeNull();
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
  await screen.findByRole("radio", { name: "rm" });
  expect(opNames()).toEqual(["sync", "rm"]);
  expect(screen.queryByRole("radio", { name: "scan" })).toBeNull();
  probe.unmount();
  for (const op of ["rm"] as const) {
    posted = [];
    const view = renderAs(meAdminListed);
    await choose(op);
    await pick("namespace (StorageClass)", "PV 이름", "team-a (tb-nfs)", "datasets — 2Gi · Bound");
    await userEvent.type(screen.getByLabelText("PV 안 경로"), "x");
    await next("옵션·실행 설정");
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
  await toSync();
  const search = await screen.findByLabelText("소스 PV 검색");
  await userEvent.type(search, "archive");
  const results = await screen.findByRole("radiogroup", { name: "소스 검색 결과 목록" });
  await waitFor(() => expect(within(results).getAllByRole("radio")).toHaveLength(1));
  await userEvent.click(within(results).getByRole("radio"));
  // 고른 PV 재해석은 이름 정확 일치(pvc=) — 부분 일치 q 는 같은 namespace 에 겹치는 이름이 많으면 상한에 잘렸다(2026-10-26).
  await waitFor(() => expect(adminVolumeUrls.some((u) => u.includes("namespace=team-c") && u.includes("pvc=archive")))
    .toBe(true));
  expect(await screen.findByText("지정 경로: /k8s_pv/nfs/team-c/archive (PV 전체)")).toBeInTheDocument();
  const mode = screen.getByRole("group", { name: "목적지 지정 방식" });
  await userEvent.click(within(mode).getByRole("button", { name: "직접 경로" }));
  const storage = screen.getByLabelText("목적지 스토리지");
  await within(storage).findByRole("option", { name: "cephfs-dms" });
  // PV 전용 스토리지(k8s-pv-nfs)는 직접 경로 선택지에 없다(설계 D11) -- 둘 다(k8s-pv-gpfs)는 있다.
  expect(within(storage).queryByRole("option", { name: "k8s-pv-nfs" })).toBeNull();
  expect(within(storage).getByRole("option", { name: "k8s-pv-gpfs" })).toBeInTheDocument();
  await userEvent.selectOptions(storage, "cephfs-dms");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "backup/archive");
  expect(screen.getByText("지정 경로: /cephfs/managed/backup/archive")).toBeInTheDocument();
  expect(await submitAndBody()).toEqual({
    operation: "sync",
    source_pv: { cluster: "dmsk8s", namespace: "team-c", pvc: "archive" }, source: "",
    destination_storage: "cephfs-dms", destination: "backup/archive",
    options: SYNC_DEFAULTS, run_as_root: true,
  });
});

test("면제 관리자: PV 검색어·고른 PV·하위경로가 ②→③→② 뒤에도 그대로다(검색칸 상태를 화면 최상위가 쥔다)", async () => {
  renderAs(meExempt);
  await toSync();
  await userEvent.type(await screen.findByLabelText("소스 PV 검색"), "archive");
  const results = await screen.findByRole("radiogroup", { name: "소스 검색 결과 목록" });
  await waitFor(() => expect(within(results).getAllByRole("radio")).toHaveLength(1));
  await userEvent.click(within(results).getByRole("radio"));
  await userEvent.type(screen.getByLabelText("소스 PV 안 경로"), "sub");
  await exemptPick("목적지", "scratch");
  await next("옵션·실행 설정");
  await goStep("소스와 목적지");
  expect(screen.getByLabelText("소스 PV 검색")).toHaveValue("archive");
  const again = await screen.findByRole("radiogroup", { name: "소스 검색 결과 목록" });
  expect(within(again).getByRole("radio")).toBeChecked();
  expect(screen.getByLabelText("소스 PV 안 경로")).toHaveValue("sub");
  expect(screen.getByLabelText("목적지 PV 검색")).toHaveValue("scratch");
});

test("PV 검색칸 Enter 는 단계를 옮기지 않는다 — PV 를 이미 고른 유효 상태에서도(옛 PV 를 든 채 넘어가지 않게)", async () => {
  renderAs(meExempt);
  await choose("scan");
  const search = await screen.findByLabelText("PV 검색");
  await userEvent.type(search, "archive");
  const results = await screen.findByRole("radiogroup", { name: "대상 검색 결과 목록" });
  await waitFor(() => expect(within(results).getAllByRole("radio")).toHaveLength(1));
  await userEvent.click(within(results).getByRole("radio"));
  await waitFor(() => expect(nextButton()).not.toHaveAttribute("aria-disabled"));
  await userEvent.clear(search);
  await userEvent.type(search, "scratch{Enter}");
  expect(stepHeading()).toHaveTextContent("스캔 대상");
  expect(search).toHaveFocus();
});

test("면제 관리자: 권한 기준 사용자를 채우면 그 사용자 범위(?user=)의 고르기 흐름으로 바뀌고 owner_username 이 실린다", async () => {
  renderAs(meExempt);
  await choose("scan");
  expect(await screen.findByLabelText("PV 검색")).toBeInTheDocument();
  expect(screen.queryByLabelText("namespace (StorageClass)")).toBeNull();
  await goStep("작업 종류");
  const scope = screen.getByLabelText("권한 기준 사용자");
  await userEvent.type(scope, "cocoa.song");
  await next("스캔 대상");
  // 범위 전환: 검색칸이 사라지고 namespace → PV 흐름(그 사용자 범위만)
  const group = await screen.findByLabelText("namespace (StorageClass)");
  expect(screen.queryByLabelText("PV 검색")).toBeNull();
  await waitFor(() => expect(adminVolumeUrls.some((u) => u.includes("user=cocoa.song"))).toBe(true));
  await waitFor(() => expect(within(group).getAllByRole("option").map((o) => o.textContent))
    .toEqual(["선택하세요", "team-b (tb-gpfs)", "team-b (tb-nfs)"]));
  await pick("namespace (StorageClass)", "PV 이름", "team-b (tb-nfs)", "scratch — 5Gi · Bound");
  await toReview(REVIEW);
  expect(screen.getByText("cocoa.song", { selector: "dd" })).toBeInTheDocument();       // 요약 「대상 사용자」
  expect(await submitAndBody()).toEqual({
    operation: "scan", pv: { cluster: "dmsk8s", namespace: "team-b", pvc: "scratch" }, target: "",
    options: { batch_files: 1000000, broken_limit: 100 }, owner_username: "cocoa.song", run_as_root: true,
  });
});

test("면제 관리자: 권한 기준 사용자 형식이 틀리면 ①을 잠근다, rm 은 직접 경로로도 된다", async () => {
  renderAs(meExempt);
  await pickOp("rm");
  await userEvent.type(await screen.findByLabelText("권한 기준 사용자"), "Bad User");
  expect(screen.getByText(/계정 아이디 형식이 아닙니다/)).toBeInTheDocument();
  expect(nextButton()).toHaveAttribute("aria-disabled", "true");
  // 막힌 「다음」을 누르면 틀린 칸(aria-invalid)으로 포커스
  screen.getByRole("button", { name: "이전" }).focus();
  await userEvent.click(nextButton());
  expect(screen.getByLabelText("권한 기준 사용자")).toHaveFocus();
  await userEvent.clear(screen.getByLabelText("권한 기준 사용자"));
  await next("삭제 대상");
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
  await toSync();
  await pick("소스 namespace (StorageClass)", "소스 PV 이름", "team-a (tb-nfs)", "datasets — 2Gi · Bound");
  await pick("목적지 namespace (StorageClass)", "목적지 PV 이름", "team-b (tb-gpfs)", "models — 1Gi · Bound");
  // 하위경로를 비우면 PV 루트 -- 막지 않고 경고만(제출은 된다)
  expect(screen.getByText(
    "목적지가 PV 전체(루트)라 PV 루트 디렉터리 자체가 스토리지 기본 소유자 0:0(root) 로 chown 되고, 권한·시각은 소스에 맞춰 바뀝니다 — 하위 디렉터리로 받으려면 경로를 적으세요."))
    .toBeInTheDocument();
  expect(summaryValue("목적지 소유")).toHaveTextContent("목적지 스토리지 기본 소유자 0:0(root)");
  expect(screen.queryByRole("group", { name: "결과 소유" })).toBeNull();
  await toReview(REVIEW);
  expect(summaryValue("목적지 소유")).toHaveTextContent("목적지 스토리지 기본 소유자 0:0(root)");
  expect(ownerCard()).toHaveTextContent("결과는 목적지 스토리지 기본 소유자 0:0(root) 로 맞춰집니다");
  expect(ownerCard()).toHaveTextContent("그 안의 같은 경로 항목은 목적지 스토리지 기본 소유자와 소스의 권한·시각으로 다시 맞춰집니다");
  // 기본 바디는 이 기능 전과 같다 -- pv_owner 를 싣지 않는다(서버가 owner_rule:"storage" 를 박는다)
  expect(await submitAndBody()).toEqual({
    operation: "sync",
    source_pv: { cluster: "dmsk8s", namespace: "team-a", pvc: "datasets" }, source: "",
    destination_pv: { cluster: "dmsk8s", namespace: "team-b", pvc: "models" }, destination: "",
    options: SYNC_DEFAULTS, run_as_root: true,
  });
});

test("목적지 스토리지의 기본 소유 값이 올바르지 않으면(storage_owner null) 사용자는 ②에서 잠긴다(서버 409 invalid_storage_owner)", async () => {
  server.use(http.get("/api/user/k8s/volumes", () => HttpResponse.json(userVolumes({
    volumes: [A_DATASETS, A_MODELS, { ...B_SCRATCH, storage_owner: null }],
  }))));
  renderAs(meUser);
  await toSync();
  await pick("소스 namespace (StorageClass)", "소스 PV 이름", "team-a (tb-gpfs)", "models — 1Gi · Bound");
  await pick("목적지 namespace (StorageClass)", "목적지 PV 이름", "team-b (tb-nfs)", "scratch — 5Gi · Bound");
  await userEvent.type(screen.getByLabelText("목적지 PV 안 경로"), "in");
  // 사용자는 chown 우회로가 없다 -- 대상 단계에서 원문으로 막는다
  expectBlocked("목적지 스토리지의 기본 소유 값이 올바르지 않습니다 — 관리자에게 스토리지 설정 확인을 요청하세요");
  expect(screen.getByText(/이 PV 의 스토리지에 저장된 기본 소유 값이 올바르지 않아 제출할 수 없습니다/)).toBeInTheDocument();
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
  await toSync();
  await exemptPick("소스", "archive");
  await exemptPick("목적지", "scratch");
  const group = await screen.findByRole("group", { name: "결과 소유" });
  const storageBtn = await within(group).findByRole("button", { name: "스토리지 기본" });
  // 값은 버튼이 아니라 도움말에(Segmented 는 한 줄 -- 값이 길면 반폭 열을 넘쳐 도움말과 겹쳤다, 리뷰)
  expect(screen.getByText(/기본은 목적지 스토리지 기본 소유자 1500:1500입니다/)).toBeInTheDocument();
  expect(storageBtn).toHaveAttribute("aria-pressed", "true");
  expect(summaryValue("목적지 소유")).toHaveTextContent("목적지 스토리지 기본 소유자 1500:1500");
  await toReview(REVIEW);
  expect(screen.getByText(PRIV_ROOT_SYNC_PV)).toBeInTheDocument();
  await goStep("소스와 목적지");
  const rootBtn = within(screen.getByRole("group", { name: "결과 소유" })).getByRole("button", { name: "PV 루트 소유자" });
  await userEvent.click(rootBtn);
  expect(rootBtn).toHaveAttribute("aria-pressed", "true");
  expect(summaryValue("목적지 소유")).toHaveTextContent("목적지 PV 루트의 소유자·그룹(점검 뒤 확정 — 운영자 지정)");
  // PV 루트 목적지(하위경로 비움) 경고도 그 규칙: PV 루트 소유자는 그대로
  expect(screen.getByText(/PV 루트 디렉터리의 권한·시각이 소스에 맞춰 바뀝니다\(소유자는 PV 루트 소유자 그대로\)/)).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("목적지 PV 안 경로"), "in");
  await userEvent.click(screen.getByRole("button", { name: "검토로 돌아가기" }));
  expect(stepHeading()).toHaveTextContent(REVIEW);
  expect(summaryValue("목적지 소유")).toHaveTextContent("목적지 PV 루트의 소유자·그룹(점검 뒤 확정 — 운영자 지정)");
  expect(screen.getByText(PRIV_ROOT_SYNC_PV_ROOT)).toBeInTheDocument();
  expect(ownerCard()).toHaveTextContent("결과 소유를 PV 루트 소유자로 골랐습니다(특권 운영자 옵션 — 기본은 목적지 스토리지 기본 소유자)");
  expect(ownerCard()).toHaveTextContent("그 안의 같은 경로 항목은 목적지 PV 루트의 소유자·그룹과 소스의 권한·시각으로 다시 맞춰집니다");
  expect(await submitAndBody()).toEqual({
    operation: "sync",
    source_pv: { cluster: "dmsk8s", namespace: "team-c", pvc: "archive" }, source: "",
    destination_pv: { cluster: "dmsk8s", namespace: "team-b", pvc: "scratch" }, destination: "in",
    options: SYNC_DEFAULTS, pv_owner: "pv_root", run_as_root: true,
  });
});

test("운영자: 스토리지 기본을 그대로 두면 바디는 기본 바디와 같다(pv_owner 없음)", async () => {
  renderAs(meExempt);
  await toSync();
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
  await toSync();
  await exemptPick("소스", "archive");
  await exemptPick("목적지", "scratch");
  const group = await screen.findByRole("group", { name: "결과 소유" });
  await userEvent.click(within(group).getByRole("button", { name: "PV 루트 소유자" }));
  await next("옵션·실행 설정");
  await userEvent.type(screen.getByLabelText("chown"), "7:7");
  await goStep("소스와 목적지");
  for (const b of within(screen.getByRole("group", { name: "결과 소유" })).getAllByRole("button")) expect(b).toBeDisabled();
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
  await toSync();
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
  await toSync();
  await exemptPick("소스", "archive");
  const mode = screen.getByRole("group", { name: "목적지 지정 방식" });
  await userEvent.click(within(mode).getByRole("button", { name: "직접 경로" }));
  const storage = screen.getByLabelText("목적지 스토리지");
  await within(storage).findByRole("option", { name: "k8s-pv-gpfs" });
  await userEvent.selectOptions(storage, "k8s-pv-gpfs");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "backup/x");
  expect(summaryValue("목적지 소유"))
    .toHaveTextContent("PV 루트 아래면 목적지 스토리지 기본 소유자 2500:2600, 아니면 소스 그대로");
  await toReview(REVIEW);
  expect(summaryValue("실행 권한")).toHaveTextContent(PRIV_ROOT_SYNC_DIRECT_BOTH);
  expect(ownerCard()).toHaveTextContent("어느 쪽인지는 제출 때 서버가 정해 요청 상세의 「목적지 소유」에 보입니다");
  expect(ownerCard()).toHaveTextContent("결과 소유(PV 루트 아래면 목적지 스토리지 기본 소유자, 아니면 소스 소유)로 다시 맞춰집니다");
  // 파일시스템 스토리지로 바꾸면 종전(소스 소유 보존)
  await goStep("소스와 목적지");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "cephfs-dms");
  expect(summaryValue("목적지 소유")).toHaveTextContent("소스의 소유자·그룹 그대로(root 실행)");
  await userEvent.click(screen.getByRole("button", { name: "검토로 돌아가기" }));
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
  await toSync();
  await exemptPick("소스", "archive");
  await userEvent.click(within(screen.getByRole("group", { name: "목적지 지정 방식" })).getByRole("button", { name: "직접 경로" }));
  const storage = screen.getByLabelText("목적지 스토리지");
  await within(storage).findByRole("option", { name: "k8s-pv-gpfs" });
  await userEvent.selectOptions(storage, "k8s-pv-gpfs");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "backup/x");
  expect(summaryValue("목적지 소유"))
    .toHaveTextContent("PV 루트 아래면 목적지 스토리지 기본 소유자(uid:gid), 아니면 소스 그대로");
  await toReview(REVIEW);
  await waitFor(() => expect(submitButton()).toBeEnabled());
});

// ---- 2026-11-11 포탈 전수조사 회귀(jobs-submit) -----------------------------------------------------------------

test("사용자: rm 에서 켠 quiet 는 sync 로 돌아오면 바디에 실리지 않는다(sync 화면엔 quiet 칸이 없어 끌 수 없다)", async () => {
  renderAs(meUser);
  await choose("rm");
  await pick("namespace (StorageClass)", "PV 이름", "team-b (tb-nfs)", "scratch — 5Gi · Bound");
  await userEvent.type(screen.getByLabelText("PV 안 경로"), "old");
  await next("옵션");
  await userEvent.click(screen.getByLabelText("quiet"));
  await goStep("작업 종류");
  await pickOp("sync");
  await next("소스와 목적지");
  await pick("소스 namespace (StorageClass)", "소스 PV 이름", "team-a (tb-gpfs)", "models — 1Gi · Bound");
  await pick("목적지 namespace (StorageClass)", "목적지 PV 이름", "team-b (tb-nfs)", "scratch — 5Gi · Bound");
  await userEvent.type(screen.getByLabelText("목적지 PV 안 경로"), "in");
  await next("옵션");
  expect(screen.queryByLabelText("quiet")).toBeNull();
  await next(REVIEW);
  expect(summaryValue("옵션")).not.toHaveTextContent("quiet");
  const body = await submitAndBody() as Record<string, unknown>;
  expect(body.options).toEqual(SYNC_DEFAULTS);
});

test("면제자: 권한 기준 사용자의 범위 목록 조회가 실패해도 양쪽이 직접 경로면 잠그지 않는다(PV 쪽이 있으면 잠근다)", async () => {
  server.use(http.get("/api/admin/k8s/volumes", ({ request }) => {
    adminVolumeUrls.push(request.url);
    const url = new URL(request.url);
    return url.searchParams.get("user") !== null
      ? HttpResponse.json({ detail: "boom" }, { status: 500 })
      : HttpResponse.json(adminVolumes(url));
  }));
  renderAs(meExempt);
  await userEvent.type(await screen.findByLabelText("권한 기준 사용자"), "alice");
  await next("소스와 목적지");
  expect(await screen.findByText(/^PV 목록을 불러오지 못했습니다: /)).toBeInTheDocument();
  // 양쪽이 PV(기본)인 동안은 잠그고 그 이유를 말한다
  expectBlocked("PV 목록 조회 실패 — 새로고침 후 다시 시도하세요");
  for (const side of ["소스", "목적지"] as const)
    await userEvent.click(within(screen.getByRole("group", { name: `${side} 지정 방식` }))
      .getByRole("button", { name: "직접 경로" }));
  const src = screen.getByLabelText("소스 스토리지");
  await within(src).findByRole("option", { name: "cephfs-dms" });
  await userEvent.selectOptions(src, "cephfs-dms");
  await userEvent.type(screen.getByLabelText("소스 경로"), "a/b");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "cephfs-dms");
  await userEvent.type(screen.getByLabelText("목적지 경로"), "c/d");
  expect(reasonLine()?.textContent ?? "").not.toContain("PV 목록 조회 실패");
  expect(await submitAndBody()).toEqual({
    operation: "sync", source_storage: "cephfs-dms", source: "a/b", destination_storage: "cephfs-dms", destination: "c/d",
    options: SYNC_DEFAULTS, owner_username: "alice", run_as_root: true,
  });
});

test("운영자: PV 목적지의 스토리지 기본 소유 값이 무효면 ②는 통과하고 ③이 막힌다 — 고급 옵션을 펼치고 chown 을 적으면 풀린다", async () => {
  // 2026-11-11: 서버는 chown 이면 스토리지 값을 안 본다 -- 운영자의 자연스러운 우회로(chown)를 대상 단계에서 막으면 그 우회로가 다시
  // 막힌다. 바 문장은 운영자용(고칠 곳을 말한다), 검토 목록은 원문(submitGates).
  server.use(http.get("/api/admin/k8s/volumes", ({ request }) => {
    const r = adminVolumes(new URL(request.url));
    return HttpResponse.json({ ...r, volumes: r.volumes.map((v) => (v.pvc === "scratch" ? { ...v, storage_owner: null } : v)) });
  }));
  renderAs(meExempt);
  await toSync();
  await exemptPick("소스", "archive");
  await exemptPick("목적지", "scratch");
  await userEvent.type(screen.getByLabelText("목적지 PV 안 경로"), "in");
  await waitFor(() => expect(nextButton()).not.toHaveAttribute("aria-disabled"));
  await next("옵션·실행 설정");
  expectBlocked("목적지 스토리지의 기본 소유 값이 올바르지 않습니다 — 고급 옵션의 chown 에 숫자 uid:gid 를 지정하거나 ② 결과 소유를 「PV 루트 소유자」로 고르세요");
  const details = screen.getByText("고급 옵션").closest("details")!;
  expect(details.open).toBe(true);                       // 안내가 가리키는 chown 칸이 보인다
  await userEvent.click(screen.getByText("고급 옵션"));     // 접으려 한다
  await new Promise((r) => setTimeout(r, 0));
  expect(details.open).toBe(true);
  await userEvent.type(screen.getByLabelText("chown"), "7:7");
  expect(nextButton()).not.toHaveAttribute("aria-disabled");
  expect(details.open).toBe(true);                       // 적는 도중 접히지 않는다
  await toReview(REVIEW);
  expect(document.getElementById("submit-job-hints")).toBeNull();
  expect(ownerCard()).not.toHaveTextContent("저장된 기본 소유 값이 올바르지 않아 제출할 수 없습니다");
  const body = await submitAndBody() as Record<string, unknown>;
  expect(body.options).toEqual({ ...SYNC_DEFAULTS, chown: "7:7" });
});

test("목적지 경로가 서버가 거부할 모양이면 「상위 디렉토리」를 지어내지 않는다(PV '../x', 직접 경로 '/abs/x'), 직접 경로는 입력 그대로", async () => {
  renderAs(meExempt);
  await toSync();
  await exemptPick("목적지", "scratch");
  const sub = screen.getByLabelText("목적지 PV 안 경로");
  await userEvent.type(sub, "in/run");
  expect(summaryValue("목적지 조건")).toHaveTextContent("상위 디렉토리 /k8s_pv/nfs/team-b/scratch/in 가 있어야 함");
  await userEvent.clear(sub);
  await userEvent.type(screen.getByLabelText("목적지 PV 안 경로"), "../x");
  expect(summaryValue("목적지 조건")).toHaveTextContent("상위 디렉토리 (목적지 경로의 상위) 가 있어야 함");
  await userEvent.click(within(screen.getByRole("group", { name: "목적지 지정 방식" })).getByRole("button", { name: "직접 경로" }));
  const storage = screen.getByLabelText("목적지 스토리지");
  await within(storage).findByRole("option", { name: "cephfs-dms" });
  await userEvent.selectOptions(storage, "cephfs-dms");
  const path = screen.getByLabelText("목적지 경로");
  await userEvent.type(path, "/abs/x");
  expect(summaryValue("목적지 조건")).toHaveTextContent("상위 디렉토리 (목적지 경로의 상위) 가 있어야 함");
  // 앞 공백도 이름의 일부(서버는 다듬지 않는다) -- 「/cephfs/managed/a」로 지어 보이지 않는다
  await userEvent.clear(path);
  await userEvent.type(path, " a/b");
  expect(summaryValue("목적지 조건").textContent).toContain("상위 디렉토리 /cephfs/managed/ a 가 있어야 함");
  // 검토 카드 전문도 같은 값(지어내지 않은 상위 디렉토리)
  await exemptPickSourceAndReview();
  expect(ownerCard()).toHaveTextContent("이 요청에서는 /cephfs/managed/ a 가 이미 있어야 합니다");
});

// 소스를 고르고 검토로(목적지가 이미 찬 상태에서).
async function exemptPickSourceAndReview() {
  await exemptPick("소스", "archive");
  await toReview(REVIEW);
}

test("면제자: 권한 기준 사용자 거부 이유를 예약어·숫자만·형식으로 나눠 말한다(서버 username_reserved 와 같은 문구)", async () => {
  renderAs(meExempt);
  const scope = await screen.findByLabelText("권한 기준 사용자");
  await userEvent.type(scope, "nobody");
  expect(screen.getByText(REASON_MESSAGES.username_reserved)).toBeInTheDocument();
  expect(screen.queryByText(/계정 아이디 형식이 아닙니다/)).toBeNull();
  expectBlocked("권한 기준 사용자에 시스템 계정 이름이 아닌 계정 아이디를 적으세요");
  await userEvent.clear(scope);
  await userEvent.type(scope, "1234");
  expect(screen.getByText(REASON_MESSAGES.username_numeric_only)).toBeInTheDocument();
  expect(reasonLine()).toHaveTextContent("권한 기준 사용자에 숫자만이 아닌 계정 아이디를 적으세요");
  await userEvent.clear(scope);
  await userEvent.type(scope, "Bad");
  expect(screen.getByText(/계정 아이디 형식이 아닙니다/)).toBeInTheDocument();
  expect(reasonLine()).toHaveTextContent("권한 기준 사용자를 계정 아이디 형식으로 고치세요");
});

test("머리말: 목록 밖 관리자에겐 쓸 수 없는 scan·배치 작업을 안내하지 않는다 -- 특권 운영자만 배치 안내", async () => {
  const view = renderAs(meAdminListed);
  expect(await screen.findByText(/^권한 있는 PV 에서 sync·rm 을 실행합니다/)).toBeInTheDocument();
  expect(screen.queryByText(/배치 작업으로 묶어 요청하세요/)).toBeNull();
  view.unmount();
  renderAs(meExempt);
  expect(await screen.findByText(
    "경로 하나에 sync·scan·rm 을 실행합니다 — 여러 경로의 sync·scan·rm 은 배치 작업으로 묶어 요청하세요.")).toBeInTheDocument();
});

test("rm·scan 검토의 「실행 권한」은 머리만(PRIV_ROOT_HEAD) -- sync 소유 결과 꼬리를 붙이지 않는다", async () => {
  renderAs(meUser);
  await choose("rm");
  await pick("namespace (StorageClass)", "PV 이름", "team-b (tb-nfs)", "scratch — 5Gi · Bound");
  await userEvent.type(screen.getByLabelText("PV 안 경로"), "old");
  await toReview(REVIEW);
  expect(summaryValue("실행 권한").textContent).toBe(PRIV_ROOT_HEAD);
  await goStep("작업 종류");
  await pickOp("sync");
  await next("소스와 목적지");
  await pick("소스 namespace (StorageClass)", "소스 PV 이름", "team-a (tb-gpfs)", "models — 1Gi · Bound");
  await pick("목적지 namespace (StorageClass)", "목적지 PV 이름", "team-b (tb-nfs)", "scratch — 5Gi · Bound");
  await toReview(REVIEW);
  expect(summaryValue("실행 권한").textContent).toBe(PRIV_ROOT_SYNC_PV);
});

test("관리자: 정책 상한을 넘는 우선순위는 칸 아래와 검토에서 「상한으로 조정」을 미리 말한다(배치 생성과 같은 계산)", async () => {
  const policy = (tool: string, def: string, cap: string) => ({
    tool, max_nodes: 4, procs_per_node: 2, queue: "dms-data", default_priority: def, max_priority: cap,
    preview_timeout_seconds: null, execution_timeout_seconds: 3600, enabled: 1, updated_at: "2026-11-11T00:00:00Z",
    updated_by: "admin" });
  server.use(http.get("/api/admin/policies", () => HttpResponse.json([policy("rm", "low", "mid")])));
  renderAs(meAdminListed);
  await choose("rm");
  await pick("namespace (StorageClass)", "PV 이름", "team-a (tb-nfs)", "datasets — 2Gi · Bound");
  await userEvent.type(screen.getByLabelText("PV 안 경로"), "x");
  await next("옵션·실행 설정");
  expect(await screen.findByText("정책 상한: mid — 상한을 넘는 선택은 배치 시 상한으로 조정됩니다")).toBeInTheDocument();
  await next(REVIEW);
  expect(summaryValue("우선순위").textContent).toBe("(정책 기본: low)");
  await goStep("옵션·실행 설정");
  await userEvent.selectOptions(screen.getByLabelText("우선순위"), "high");
  expect(screen.getByText("정책 상한: mid — 선택한 high는 상한 mid로 조정됩니다")).toBeInTheDocument();
  await next(REVIEW);
  expect(summaryValue("우선순위").textContent).toBe("high — 선택한 high는 상한 mid로 조정됩니다");
  await goStep("옵션·실행 설정");
  await userEvent.selectOptions(screen.getByLabelText("우선순위"), "mid");
  await next(REVIEW);
  expect(summaryValue("우선순위").textContent).toBe("mid");
});

test("관리자 sync 우선순위 캡션: nsync 는 상한만 -- 쓰이지 않는 nsync 기본값을 찍지 않는다(정책 화면 「dsync 따름」과 같은 말)", async () => {
  const policy = (tool: string, def: string, cap: string) => ({
    tool, max_nodes: 4, procs_per_node: 2, queue: "dms-data", default_priority: def, max_priority: cap,
    preview_timeout_seconds: null, execution_timeout_seconds: 3600, enabled: 1, updated_at: "2026-11-11T00:00:00Z",
    updated_by: "admin" });
  // 예전에 nsync 기본을 dsync 와 다르게 바꿔 둔 사이트(지금은 UI 로 못 고친다) -- 상한이 같으면 한 묶음, 기본은 dsync 값.
  server.use(http.get("/api/admin/policies", () => HttpResponse.json([policy("dsync", "mid", "high"), policy("nsync", "low", "high")])));
  renderAs(meAdminListed);
  await toSync();
  await pick("소스 namespace (StorageClass)", "소스 PV 이름", "team-a (tb-gpfs)", "models — 1Gi · Bound");
  await pick("목적지 namespace (StorageClass)", "목적지 PV 이름", "team-b (tb-nfs)", "scratch — 5Gi · Bound");
  await next("옵션·실행 설정");
  expect(await screen.findByText("정책 — dsync·nsync: 기본 mid · 상한 high. "
    + "기본값 적용은 dsync 기준(제출 시점 도구 미정), 상한은 실행 도구 기준으로 조정됩니다")).toBeInTheDocument();
  expect(screen.queryByText(/nsync: 기본 low/)).toBeNull();
  await next(REVIEW);
  expect(summaryValue("우선순위").textContent).toBe("(정책 기본: mid)");
});

test("제출 거부 문구는 폼을 고치면 지운다(옛 PV 의 거부가 새로 고른 PV 이야기처럼 남지 않게)", async () => {
  server.use(http.post("/api/user/requests", () => HttpResponse.json({ detail: "k8s_access_denied" }, { status: 403 })));
  renderAs(meUser);
  await choose("rm");
  await pick("namespace (StorageClass)", "PV 이름", "team-b (tb-nfs)", "scratch — 5Gi · Bound");
  await userEvent.type(screen.getByLabelText("PV 안 경로"), "old");
  await toReview(REVIEW);
  await waitFor(() => expect(submitButton()).toBeEnabled());
  await userEvent.click(submitButton());
  expect(await screen.findByText(REASON_MESSAGES.k8s_access_denied)).toBeInTheDocument();
  expect(stepHeading()).toHaveTextContent(REVIEW);                 // 거부면 검토에 머문다
  await goStep("삭제 대상");
  await userEvent.type(screen.getByLabelText("PV 안 경로"), "2");
  await userEvent.click(screen.getByRole("button", { name: "검토로 돌아가기" }));
  expect(within(stepCard()).getByText("scratch — team-b (tb-nfs) / old2")).toBeInTheDocument();
  expect(screen.queryByText(REASON_MESSAGES.k8s_access_denied)).toBeNull();
});
