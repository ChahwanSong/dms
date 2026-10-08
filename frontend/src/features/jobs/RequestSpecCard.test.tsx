import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { RequestDetail } from "./RequestDetail";
import { PRIV_ROOT_HEAD, PRIV_USER } from "./optionRules";

// 요청 상세 전용 open_noatime 주의 줄(제출 폼의 「root 실행에서만 적용됩니다」 안내와 다른, 이미 실린 기록에 대한 문장).
const NOATIME_WARN = /^open_noatime 이 비 root 실행에 실렸습니다 — /;

// 요청 상세 「요청 내용」 카드 + 머리말 root 배지(2026-10-08) 화면 테스트. 하네스는 RequestDetail.redesign.test 의 복제.

const server = setupServer(
  http.get("/api/user/storages", () => HttpResponse.json([])),
  http.get("/api/user/jobs/:jid/artifacts", () => HttpResponse.json({ entries: [], truncated: false })),
  http.get("/api/user/jobs/:jid/logs", () => HttpResponse.json({ source: "archived", entries: [] })),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const at = (sec: number) => `2026-10-08T03:15:${String(sec).padStart(2, "0")}Z`;
const tr = (from: string | null, to: string, sec: number, extra: object = {}) =>
  ({ from_state: from, to_state: to, at: at(sec), ...extra });
const SYNC_TR = [
  tr(null, "Pending", 0), tr("Pending", "Preflight", 2), tr("Preflight", "PreviewRunning", 7),
  tr("PreviewRunning", "ConfirmPending", 17), tr("ConfirmPending", "Executing", 18, { actor: "alice" }),
  tr("Executing", "Executing", 27), tr("Executing", "Succeeded", 37),
];
// 테스트베드 표본 그대로(alice sync, 비 root, 보조 그룹 적용).
const PAYLOAD_A = { source_storage: "cephfs-dms", source: "ldap-e2e/proj-shared", destination_storage: "cephfs-dms",
  destination: "dms_test/suppgrp/rw/alice-out-1008a", options: { batch_files: 1000000, bufsize: 4194304 }, owner_username: null };
const ALICE = { username: "alice", uid: 10001, gid: 10000, groups: ["dmsproj", "dmsusers"], privileged: false,
  supplementary_gids: [10010], supplementary_gids_status: "applied", supplementary_gids_excluded: [], supplementary_gids_found: 1 };
const WP_A = { tool: "dsync", node_count: 4, process_count: 8, queue: "dms-data", priority_class: "dms-mid",
  candidates: { primary: ["dms-w2", "dms-w3", "dms-w4", "dms-w5"] }, identity: ALICE };
const REQ = {
  request_id: "r1", operation: "sync", requester_id: "alice", resource_key: "k", priority: "mid", state: "Succeeded",
  created_at: at(0), updated_at: at(40), payload: PAYLOAD_A, auth_method: "session", batch_id: null,
  transitions: [tr(null, "Pending", 0), tr("Pending", "Planned", 2), tr("Planned", "Succeeded", 40)],
};
const JOB = {
  job_id: "j1", request_id: "r1", operation: "sync", state: "Succeeded", reason_code: null, tool: "dsync",
  preview_fingerprint: null, preview_expires_at: null, result_summary: { files: 2, bytes: 10, returncode: 0 },
  preview_summary: { files: 2, bytes: 10, returncode: 0 }, transitions: SYNC_TR, artifact_uri: "/cephfs/dms/artifacts/j1",
  phase_refs: { preflight: "a", preview: "b", exec_preflight: "c", execution: "vcjob/j1" },
  source_storage: "cephfs-dms", destination_storage: "cephfs-dms", storage_name: null, options: PAYLOAD_A.options,
  worker_pool: WP_A,
};
const ROOT_IDENT = { username: "mason", uid: 0, gid: 0, groups: [], privileged: true, supplementary_gids: [],
  supplementary_gids_status: "privileged", supplementary_gids_excluded: [], supplementary_gids_found: null };
const GENERIC_CAVEAT = "실제 인정 여부는 스토리지 설정에 따라 다를 수 있습니다(등록된 스토리지 종류 기준 안내).";

function renderAt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/jobs/r1"]}>
        <Routes><Route path="/jobs/:requestId" element={<RequestDetail />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
function serve(req: object, jobs: object[], storages: object[] = []) {
  server.use(
    http.get("/api/user/storages", () => HttpResponse.json(storages)),
    http.get("/api/user/requests/r1", () => HttpResponse.json(req)),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json(jobs)),
  );
}
const specRegion = () => screen.findByRole("region", { name: "요청 내용" });
// dt 다음 형제 dd(행 구조 계약).
const cell = (region: HTMLElement, label: string) =>
  within(region).getByText(label, { selector: "dt" }).nextElementSibling as HTMLElement;
// 경로 칸은 화살표 앞 줄바꿈(whitespace-pre-line)을 쓴다 -- 공백 정규화 후 비교(getByText 기본 정규화와 같다).
const norm = (s: string | null) => (s ?? "").replace(/\s+/g, " ").trim();
const USER_HEAD = `root 아님 — ${PRIV_USER}`;

test("a root 잡: 머리말 배지 「root(특권) 실행」(h1 밖), 실행 권한 빨강, 보조 그룹 행 없음", async () => {
  serve({ ...REQ, requester_id: "mason", payload: { ...PAYLOAD_A, owner_username: "mason", run_as_root: true } },
    [{ ...JOB, worker_pool: { ...WP_A, identity: ROOT_IDENT } }]);
  renderAt();
  const region = await specRegion();
  expect(screen.getByRole("heading", { level: 1, name: "sync 요청" })).toBeInTheDocument();
  const badge = screen.getByText("root(특권) 실행");
  expect(badge.closest("h1")).toBeNull();
  expect(badge).toHaveClass("text-bad");
  const priv = cell(region, "실행 권한");
  // 실행 권한 칸은 root 라는 사실만(sync 의 소유 결과는 바로 아래 「목적지 소유」 행 -- 같은 사실을 두 번 말하지 않는다).
  expect(priv.firstElementChild?.textContent).toBe(PRIV_ROOT_HEAD);
  const lead = priv.firstElementChild?.firstElementChild as HTMLElement;
  expect(lead.textContent).toBe("root(특권)");
  expect(lead).toHaveClass("font-medium", "text-bad");
  expect(lead.nextElementSibling).toHaveClass("text-bad");
  expect(norm(cell(region, "실행 신원").textContent)).toBe("mason (요청자 본인) · uid 0 · gid 0");
  expect(cell(region, "목적지 소유").textContent).toBe("소스의 소유자·그룹 그대로(root 실행)");
  expect(screen.queryByText("보조 그룹(gid)")).toBeNull();
});

test("b 시나리오 A(테스트베드 표본): 카드의 각 칸 문구, 머리말의 우선순위·배너의 대상은 없다", async () => {
  serve(REQ, [JOB], [{ storage_name: "cephfs-dms", backend_type: "cephfs", status: "Ready", managed_root: "/cephfs/managed" }]);
  renderAt();
  const region = await specRegion();
  expect(within(region).getByRole("heading", { level: 2, name: "요청 내용" })).toBeInTheDocument();
  expect(within(region).getByRole("heading", { level: 3, name: "대상·옵션" })).toBeInTheDocument();
  expect(within(region).getByRole("heading", { level: 3, name: "실행 권한·자원" })).toBeInTheDocument();
  const target = cell(region, "대상");
  expect(norm(target.textContent)).toBe("cephfs-dms:ldap-e2e/proj-shared → cephfs-dms:dms_test/suppgrp/rw/alice-out-1008a");
  expect(target.childNodes).toHaveLength(1);   // 텍스트 노드 하나(출발 줄 + 「→ 도착」 줄)
  expect(target.textContent).toContain("\n→\u00a0");
  expect(target).toHaveClass("whitespace-pre-line");
  await waitFor(() => expect(norm(cell(region, "절대경로").textContent))
    .toBe("/cephfs/managed/ldap-e2e/proj-shared → /cephfs/managed/dms_test/suppgrp/rw/alice-out-1008a"));
  expect(cell(region, "옵션").textContent).toBe("지정한 옵션 없음기본값: batch_files=1000000 · bufsize=4194304(4.0 MiB)");
  expect(cell(region, "우선순위").textContent).toBe("mid");
  // 비 root 는 배지 없이 카드 문장이 「root 아님」으로 확정한다(사용자 보고: root 실행 여부가 화면에 없다).
  expect(cell(region, "실행 권한").textContent).toBe(USER_HEAD);
  expect(within(cell(region, "실행 권한")).getByText("root 아님")).toHaveClass("font-medium");
  const who = cell(region, "실행 신원");
  expect(norm(who.textContent)).toBe("alice (요청자 본인) · uid 10001 · gid 10000(주 그룹)");
  expect(who.textContent).toContain("gid\u00a010000(주\u00a0그룹)");   // 고정 단위는 줄바꿈 없이
  expect(cell(region, "보조 그룹(gid)").textContent).toBe(`10010${GENERIC_CAVEAT}`);
  // 「실행 신원」 행의 이름을 되풀이하지 않고 결과 숫자만
  expect(norm(cell(region, "목적지 소유").textContent)).toBe("실행 신원 소유 — 10001:10000(주 그룹)");
  expect(cell(region, "실행 도구").textContent)
    .toBe("dsync · 4 노드 · 프로세스 8개(노드당 2)실행 노드 dms-w2, dms-w3, dms-w4, dms-w5");
  expect(within(region).getByText("dsync · 4 노드")).toBeInTheDocument();   // 도구 요약은 자기만의 span
  // 노드 이름 하나는 줄바꿈 없이(「dms-」/「w5」로 갈리지 않게)
  expect(within(region).getByText("dms-w5")).toHaveClass("whitespace-nowrap");
  expect(screen.queryByText("root(특권) 실행")).toBeNull();
  expect(screen.queryByText("root(특권) 요청")).toBeNull();
  expect(screen.queryByText(/^우선순위 /)).toBeNull();
  const banner = screen.getByRole("heading", { level: 2, name: "작업이 완료되었습니다" }).closest(".bg-surface") as HTMLElement;
  expect(within(banner).queryByText("대상")).toBeNull();
  expect(within(banner).queryByText("절대경로")).toBeNull();
  // 성공 부제는 끝난 시각만(단계 나열은 단계 구획이 말한다)
  expect(within(banner).getByText(/^2026-10-08 12:15:\d\d KST 완료$/)).toBeInTheDocument();
});

test("c-B 관리자가 alice 신원으로 낸 scan(비 root): 대리 문형, 목적지 소유 행 없음", async () => {
  serve({ ...REQ, operation: "scan", requester_id: "mason",
    payload: { storage: "cephfs-dms", target: "dms_test", owner_username: "alice", options: { batch_files: 1000000, broken_limit: 100 } } },
  [{ ...JOB, operation: "scan", tool: "dscan", storage_name: "cephfs-dms", source_storage: null, destination_storage: null,
    options: undefined, phase_refs: { preflight: "a", execution: "vcjob/j1" },
    transitions: [tr(null, "Pending", 0), tr("Pending", "Preflight", 1), tr("Preflight", "Running", 5), tr("Running", "Succeeded", 30)] }]);
  renderAt();
  const region = await specRegion();
  expect(cell(region, "실행 권한").textContent).toBe(USER_HEAD);
  expect(norm(cell(region, "실행 신원").textContent)).toBe("alice (관리자가 지정한 다른 사용자) · uid 10001 · gid 10000(주 그룹)");
  expect(cell(region, "옵션").textContent).toBe("지정한 옵션 없음기본값: batch_files=1000000 · broken_limit=100");
  expect(within(region).queryByText("목적지 소유")).toBeNull();
  expect(screen.queryByText(/^root\(특권\)/)).toBeNull();
  expect(screen.getAllByText(/root/).length).toBeGreaterThan(0);   // 「root」라는 글자가 화면에 있다(비 root 확정)
});

test("c-D 관리자 root rm(stat): 빨간 보조 줄, 옵션 칩 stat, 기본값 줄 없음", async () => {
  serve({ ...REQ, operation: "rm", requester_id: "mason",
    payload: { storage: "cephfs-dms", target: "old", run_as_root: true, owner_username: "mason", options: { recursive: true, stat: true } } },
  [{ ...JOB, operation: "rm", tool: "drm", storage_name: "cephfs-dms", worker_pool: { ...WP_A, tool: "drm", identity: ROOT_IDENT } }]);
  renderAt();
  const region = await specRegion();
  expect(screen.getByText("root(특권) 실행")).toBeInTheDocument();
  const priv = cell(region, "실행 권한");
  expect(priv.firstElementChild?.textContent).toBe(PRIV_ROOT_HEAD);
  expect(within(priv).getByText("root(특권)")).toHaveClass("text-bad");
  expect(within(priv).getByText("삭제가 root 권한으로 수행됩니다")).toHaveClass("text-bad");
  const opts = cell(region, "옵션");
  expect(within(opts).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["stat"]);
  expect(within(opts).queryByText(/^기본값/)).toBeNull();
});

test("c-I root 요청이 계획 단계에서 거부(잡 0개): 배지 「root(특권) 요청」 + 「요청됨」 시제, 실행 도구 행 없음", async () => {
  serve({ ...REQ, state: "Rejected", reason_code: "privileged_not_authorized", requester_id: "mason",
    payload: { ...PAYLOAD_A, run_as_root: true, owner_username: "mason" },
    transitions: [tr(null, "Pending", 0), tr("Pending", "Rejected", 2, { reason_code: "privileged_not_authorized" })] }, []);
  renderAt();
  const region = await specRegion();
  expect(screen.getByText("root(특권) 요청")).toBeInTheDocument();
  expect(screen.queryByText("root(특권) 실행")).toBeNull();
  const priv = cell(region, "실행 권한");
  expect(priv.textContent).toBe("root(특권) 요청됨 — 작업이 만들어지지 않아 실행되지 않았습니다");
  // lead 만 빨강 -- 「실행되지 않았습니다」(안심 절반)는 경보색이 아니다
  expect(within(priv).getByText("root(특권) 요청됨")).toHaveClass("text-bad");
  expect(within(priv).getByText("— 작업이 만들어지지 않아 실행되지 않았습니다", { exact: false })).toHaveClass("text-ink/70");
  expect(norm(cell(region, "실행 신원").textContent)).toBe("mason (요청자 본인) — 이름만 기록되며 root(uid 0)로 요청됨");
  expect(within(region).queryByText("실행 도구")).toBeNull();
  // 돌지 않은 root 요청에 「(root 실행)」 소유를 단정하지 않는다
  expect(within(region).queryByText("목적지 소유")).toBeNull();
});

test("c-F 배치 자식 계획 전: 배지 없음 + 배치 문구 + 노드 지정", async () => {
  serve({ ...REQ, state: "Pending", requester_id: "mason", batch_id: "b77",
    payload: { ...PAYLOAD_A, owner_username: undefined, node_count: 4, procs_per_node: 8 },
    transitions: [tr(null, "Pending", 0)] }, []);
  renderAt();
  const region = await specRegion();
  expect(screen.queryByText(/^root\(특권\) (실행|요청)$/)).toBeNull();
  expect(cell(region, "실행 권한").textContent)
    .toBe("배치 항목 — 배치를 만든 관리자에게 root 자격이 있으면 root(특권), 없으면 실행 신원 권한으로 실행됩니다(계획 단계에서 확정)");
  expect(cell(region, "실행 신원").textContent).toBe("mason (요청자 본인) · uid/gid 는 계획 단계에서 정해집니다");
  // 계획 전이라 도구(dsync/nsync)를 모른다 -- nsync 면 면당 상한이라는 사실을 조건문으로
  expect(cell(region, "노드 지정").textContent).toBe("노드 4(nsync 로 돌면 출발·목적지 각각) · 노드당 프로세스 8 — 정책 상한까지만 적용");
});

test("c-H 배치 자식 강등(token 배치): 실행 신원 권한 + 배치·토큰 보조 줄", async () => {
  serve({ ...REQ, requester_id: "alice", batch_id: "b77", auth_method: "token" }, [JOB]);
  renderAt();
  const region = await specRegion();
  const priv = cell(region, "실행 권한");
  expect(priv.firstElementChild?.textContent).toBe(USER_HEAD);
  expect(within(priv).getByText("배치 항목 — 배치를 만든 관리자에게 root 자격이 없어 실행 신원 권한이 적용됐습니다.")).toHaveClass("text-ink/70");
  expect(within(priv).getByText("공유 토큰(API)으로 제출 — root 실행 불가")).toBeInTheDocument();
  expect(screen.queryByText("root(특권) 실행")).toBeNull();
});

test("c 배치 자식 root(테스트베드 표본: identity 에 보조 그룹 키 없음, 5노드 10프로세스)", async () => {
  serve({ ...REQ, requester_id: "mason", batch_id: "b77", payload: { ...PAYLOAD_A, owner_username: undefined, options: { open_noatime: true } } },
    [{ ...JOB, worker_pool: { tool: "dsync", node_count: 5, process_count: 10, priority_class: "dms-mid",
      identity: { gid: 0, groups: [], privileged: true, uid: 0, username: "mason" } } }]);
  renderAt();
  const region = await specRegion();
  expect(screen.getByText("root(특권) 실행")).toBeInTheDocument();
  expect(within(cell(region, "실행 권한")).getByText("배치 항목 — 배치를 만든 관리자의 root 자격이 적용됐습니다.")).toBeInTheDocument();
  expect(cell(region, "실행 도구").textContent).toBe("dsync · 5 노드 · 프로세스 10개(노드당 2)");
  expect(within(region).queryByText("보조 그룹(gid)")).toBeNull();
  expect(within(region).queryByText(NOATIME_WARN)).toBeNull();   // root 라 경고 없음
});

test("d 잡 1개: 도구 라벨·보조 그룹은 잡 카드에 없고, 아티팩트 줄은 「상태 전이」 버튼과 같은 줄 컨테이너에", async () => {
  serve(REQ, [JOB]);
  renderAt();
  await specRegion();
  const card = screen.getByText("j1").closest(".bg-surface") as HTMLElement;
  expect(within(card).queryByText("dsync · 4 노드")).toBeNull();
  expect(within(card).queryByText("보조 그룹(gid)")).toBeNull();
  const art = within(card).getByText(/^아티팩트 /);
  expect(art.textContent).toBe("아티팩트 /cephfs/dms/artifacts/j1");
  const btn = within(card).getByRole("button", { name: /^상태 전이 \d+건$/ });
  expect(art.parentElement).toBe(btn.parentElement);
  // 「데이터 작업」 제목의 개수는 여럿일 때만
  expect(screen.getByRole("heading", { level: 2, name: /^데이터 작업/ }).textContent).toBe("데이터 작업");
});

test("e 잡 2개: 각 잡 카드에 도구 라벨, 카드엔 실행 도구 행 대신 여러 작업 안내, 「2개」", async () => {
  serve({ ...REQ, state: "Planned" }, [JOB, { ...JOB, job_id: "j2" }]);
  renderAt();
  const region = await specRegion();
  for (const id of ["j1", "j2"]) {
    const card = screen.getByText(id).closest(".bg-surface") as HTMLElement;
    expect(within(card).getByText("dsync · 4 노드")).toBeInTheDocument();
  }
  expect(within(region).queryByText("실행 도구")).toBeNull();
  expect(within(region).getByText("작업이 여러 개입니다 — 실행 권한·신원·보조 그룹은 가장 최근 작업 기준이고, 실행 도구는 각 작업 카드에 있습니다."))
    .toBeInTheDocument();
  expect(screen.getByRole("heading", { level: 2, name: /^데이터 작업/ }).textContent).toBe("데이터 작업2개");
});

test("f 대비: 요청 내용 카드와 머리말 배지에 text-muted 글자가 없다", async () => {
  serve({ ...REQ, requester_id: "mason", payload: { ...PAYLOAD_A, run_as_root: true, options: { delete: true, bufsize: 1048576, foo: 1 } } },
    [{ ...JOB, worker_pool: { ...WP_A, identity: ROOT_IDENT } }]);
  renderAt();
  const region = await specRegion();
  const badge = screen.getByText("root(특권) 실행");
  const offenders = [region, badge].flatMap((root) => [root, ...Array.from(root.querySelectorAll<HTMLElement>("*"))])
    .filter((el) => el.classList.contains("text-muted") && (el.textContent ?? "").trim() !== "");
  expect(offenders).toEqual([]);
  // 설명·기본값·원문 줄은 ink/70
  expect(within(region).getByText(/^그 밖에 기록된 값/)).toHaveClass("text-ink/70");
  expect(within(region).getByText("1.0 MiB", { exact: false })).toHaveClass("text-ink/70");
});

test("g 변조 기록(payload 문자열·worker_pool 문자열·options 배열): 죽지 않고 「옵션 기록을 읽을 수 없습니다」", async () => {
  serve({ ...REQ, state: "Failed", payload: { ...PAYLOAD_A, options: [1] } },
    [{ ...JOB, state: "Failed", worker_pool: "oops", options: [1] }]);
  const first = renderAt();
  let region = await specRegion();
  expect(screen.getByRole("heading", { level: 1, name: "sync 요청" })).toBeInTheDocument();
  expect(cell(region, "옵션").textContent).toBe("옵션 기록을 읽을 수 없습니다");
  expect(cell(region, "실행 신원").textContent).toBe("alice (요청자 본인) · uid/gid 기록 없음");
  first.unmount();
  serve({ ...REQ, state: "Failed", payload: "x" }, [{ ...JOB, state: "Failed", worker_pool: { identity: [], candidates: "x" } }]);
  // payload 가 문자열이어도 잡 쪽 options(계획 때 복사본)로 읽는다
  renderAt();
  region = await specRegion();
  expect(norm(cell(region, "대상").textContent)).toBe("—:— → —:—");
  expect(cell(region, "대상")).not.toHaveClass("whitespace-pre-line");   // 모르는 조각뿐이면 대시 두 줄로 나누지 않는다
  expect(cell(region, "옵션").textContent).toBe("지정한 옵션 없음기본값: batch_files=1000000 · bufsize=4194304(4.0 MiB)");
});

test("h open_noatime 비 root: 실패한 요청에만 주의 줄", async () => {
  const req = { ...REQ, payload: { ...PAYLOAD_A, options: { open_noatime: true } } };
  serve({ ...req, state: "Failed" }, [{ ...JOB, state: "Failed", reason_code: "execution_failed:rc1" }]);
  const first = renderAt();
  const region = await specRegion();
  expect(within(region).getByText(NOATIME_WARN)).toHaveClass("text-attn");
  first.unmount();
  serve(req, [JOB]);
  renderAt();
  await specRegion();
  expect(screen.queryByText(NOATIME_WARN)).toBeNull();
});

test("i 2026-09-30 이전 관리자 행(run_as_root 없이 root 로 계획, Succeeded): 배지 「실행」 + 설명 줄, 「비정상 상태」·「거부」 단정 없음", async () => {
  serve({ ...REQ, requester_id: "mason", created_at: "2026-09-20T01:00:00Z", payload: { ...PAYLOAD_A, owner_username: "alice" } },
    [{ ...JOB, worker_pool: { ...WP_A, identity: { username: "alice", uid: 0, gid: 0, groups: [], privileged: true } } }]);
  renderAt();
  const region = await specRegion();
  expect(screen.getByText("root(특권) 실행")).toBeInTheDocument();
  const priv = cell(region, "실행 권한");
  const note = within(priv).getByText(/^요청 기록에는 root 지정이 없습니다/);
  expect(note.textContent).toBe("요청 기록에는 root 지정이 없습니다 — 2026-09-30 규칙 변경(root 는 명시 요청만) 전에 계획된 작업이거나 비정상 기록입니다.");
  expect(note).toHaveClass("text-ink/70");
  expect(priv.textContent).not.toMatch(/비정상 상태|거부/);
});

test("j 경로 칸: 출발·도착 사이에서만 줄을 바꾸고(구조), 경로 이름의 「 → 」·줄바꿈은 갈림이 되지 않는다", async () => {
  serve({ ...REQ, payload: { ...PAYLOAD_A, source_storage: "a", source: "dir → x", destination_storage: "b", destination: "line1\nline2" } },
    [JOB], [{ storage_name: "a", backend_type: "cephfs", status: "Ready", managed_root: "/A" },
      { storage_name: "b", backend_type: "cephfs", status: "Ready", managed_root: "/B" }]);
  renderAt();
  const region = await specRegion();
  const target = cell(region, "대상");
  expect(target.textContent).toBe("a:dir → x\n→ b:line1⏎line2");
  expect(target.childNodes).toHaveLength(1);
  await waitFor(() => expect(cell(region, "절대경로").textContent).toBe("/A/dir → x\n→ /B/line1⏎line2"));
});

test("k scan 대상은 한 조각 -- 줄바꿈 장치 없음", async () => {
  serve({ ...REQ, operation: "scan", payload: { storage: "s", target: "a → b", options: { batch_files: 1000000, broken_limit: 100 } } },
    [{ ...JOB, operation: "scan", tool: "dscan" }]);
  renderAt();
  const region = await specRegion();
  const target = cell(region, "대상");
  expect(target.textContent).toBe("s:a → b");
  expect(target).not.toHaveClass("whitespace-pre-line");
});

test("l 옵션: 설명 붙은 옵션은 한 줄씩, 설명 없는 칩은 한 줄에 이어 둔다(한 목록)", async () => {
  serve({ ...REQ, requester_id: "mason", payload: { ...PAYLOAD_A, run_as_root: true,
    options: { delete: true, open_noatime: true, chmod: "D2775,F664", batch_files: 1000000, bufsize: 4194304 } } },
  [{ ...JOB, worker_pool: { ...WP_A, identity: ROOT_IDENT } }]);
  renderAt();
  const region = await specRegion();
  const items = within(cell(region, "옵션")).getAllByRole("listitem");
  expect(items.map((li) => li.textContent)).toEqual([
    "delete 원본에 없는 파일을 대상에서도 삭제해 완전히 동일하게 맞춥니다(미러 동기화).", "open_noatime", "chmod=D2775,F664"]);
  expect(items.map((li) => li.classList.contains("basis-full"))).toEqual([true, false, false]);
  expect(items[0].parentElement).toHaveClass("flex-wrap");
});

test("m 두 묶음 2열은 xl(1280)부터 -- 사이드바가 폭을 먹는 1024–1279 는 1열", async () => {
  serve(REQ, [JOB]);
  renderAt();
  const region = await specRegion();
  const grid = within(region).getByRole("heading", { level: 3, name: "대상·옵션" }).closest(".grid") as HTMLElement;
  expect(grid).toHaveClass("xl:grid-cols-2");
  expect(grid.className).not.toMatch(/\blg:grid-cols-2\b/);
});

test("n 공유 토큰 배치 자식, 계획 전: 비 root 확정 -- 「자격이 있으면 root」로 얼버무리지 않는다", async () => {
  serve({ ...REQ, state: "Pending", requester_id: "mason", batch_id: "b77", auth_method: "token",
    payload: { ...PAYLOAD_A, owner_username: undefined }, transitions: [tr(null, "Pending", 0)] }, []);
  renderAt();
  const region = await specRegion();
  const priv = cell(region, "실행 권한");
  expect(priv.firstElementChild?.textContent).toBe(USER_HEAD);
  expect(within(priv).getByText("배치 항목 — 공유 토큰(API)으로 만든 배치라 root 자격이 없어 실행 신원 권한으로 실행됩니다."))
    .toHaveClass("text-ink/70");
  expect(priv.textContent).not.toMatch(/자격이 있으면/);
  expect(screen.queryByText(/^root\(특권\) (실행|요청)$/)).toBeNull();
});
