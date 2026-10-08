import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect, vi } from "vitest";
import { RequestDetail } from "./RequestDetail";

// 요청 상세 재설계(2026-10-08) 추가 테스트(스펙 §11.1 추가 1~18). 기존 RequestDetail.test.tsx(42건)는 한 글자도
// 바꾸지 않고 그대로 통과해야 한다는 것이 이 재설계의 계약이라, 추가분은 같은 MSW 하네스를 복제한 이 파일에 둔다.

const server = setupServer(
  http.get("/api/user/storages", () => HttpResponse.json([])),
  // 단계 구획은 잡마다 아티팩트 목록을 읽는다 -- 기본은 빈 목록(시나리오가 필요하면 덮어쓴다).
  http.get("/api/user/jobs/:jid/artifacts", () => HttpResponse.json({ entries: [], truncated: false })),
);
beforeAll(() => server.listen());
afterEach(() => { server.resetHandlers(); vi.useRealTimers(); });
afterAll(() => server.close());

const at = (sec: number) => `2026-10-08T03:15:${String(sec).padStart(2, "0")}Z`;
const tr = (from: string | null, to: string, sec: number, extra: object = {}) =>
  ({ from_state: from, to_state: to, at: at(sec), ...extra });

const REQ = {
  request_id: "r1", operation: "sync", requester_id: "alice", resource_key: "k", priority: "mid", state: "Succeeded",
  created_at: at(0), updated_at: at(40), payload: {},
  transitions: [tr(null, "Pending", 0), tr("Pending", "Planned", 2), tr("Planned", "Succeeded", 40)],
};
const SYNC_OK_TR = [
  tr(null, "Pending", 0), tr("Pending", "Preflight", 2), tr("Preflight", "PreviewRunning", 7),
  tr("PreviewRunning", "ConfirmPending", 17), tr("ConfirmPending", "Executing", 18, { actor: "alice" }),
  tr("Executing", "Executing", 27), tr("Executing", "Succeeded", 37),
];
const ALL_REFS = { preflight: "pod/a", preview: "pod/b", exec_preflight: "pod/c", execution: "vcjob/j1" };
const JOB = {
  job_id: "j1", request_id: "r1", operation: "sync", state: "Succeeded", reason_code: null,
  preview_fingerprint: null, preview_expires_at: null, result_summary: { files: 2, bytes: 10, returncode: 0 },
  preview_summary: { files: 2, bytes: 10, returncode: 0 },
  transitions: SYNC_OK_TR, artifact_uri: "file:///a/j1", phase_refs: ALL_REFS,
};

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
function serve(req: object, jobs: object[] | (() => object[])) {
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json(req)),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json(typeof jobs === "function" ? jobs() : jobs)),
  );
}
const regions = async () => ({
  pre: await screen.findByRole("region", { name: "사전 점검·미리보기" }),
  exec: screen.getByRole("region", { name: "실행" }),
});

test("1·2 두 단계 구획이 이름 있는 region 이고, 출력은 자기 구획에만 놓인다", async () => {
  server.use(http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json({ entries: [
    { phase: "preview", name: "stdout.log", size: 5, modified_at: 1 },
    { phase: "execution", name: "stdout.log", size: 6, modified_at: 2 },
  ], truncated: false })));
  serve(REQ, [JOB]);
  renderAt();
  const { pre, exec } = await regions();
  expect(await within(pre).findByRole("button", { name: "preview/stdout.log" })).toBeInTheDocument();
  expect(within(exec).getByRole("button", { name: "execution 로그" })).toBeInTheDocument();
  expect(within(exec).getByRole("button", { name: "execution/stdout.log" })).toBeInTheDocument();
  expect(within(pre).queryByRole("button", { name: /^execution/ })).toBeNull();
  expect(within(exec).queryByRole("button", { name: /^preview/ })).toBeNull();
});

test("3 scan 은 관문 줄·재점검 행이 없고 연결 문구가 있다", async () => {
  serve({ ...REQ, operation: "scan" }, [{ ...JOB, operation: "scan", phase_refs: { preflight: "pod/a", execution: "vcjob/j1" },
    transitions: [tr(null, "Pending", 0), tr("Pending", "Preflight", 1), tr("Preflight", "Running", 5), tr("Running", "Succeeded", 30)] }]);
  renderAt();
  expect(await screen.findByRole("region", { name: "사전 점검" })).toBeInTheDocument();
  expect(screen.getByText("미리보기·컨펌 없이 바로 실행됩니다")).toBeInTheDocument();
  expect(screen.queryByText(/^작업 컨펌 · /)).toBeNull();
  expect(screen.queryByText("실행 직전 재점검")).toBeNull();
});

test("4 사전 점검 거부: preflight 로그 1건만 자동 조회, 본문 조회 0건, ② 안심 문구, 배너 제목", async () => {
  const asked: string[] = [];
  let bodies = 0;
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json({ entries: [
      { phase: "preflight", name: "stdout.log", size: 9, modified_at: 1 }], truncated: false })),
    http.get("/api/user/jobs/j1/artifacts/:phase/:name", () => { bodies += 1; return HttpResponse.json({}); }),
    http.get("/api/user/jobs/j1/logs", ({ request }) => {
      asked.push(new URL(request.url).searchParams.get("phase") ?? "");
      return HttpResponse.json({ phase: "preflight", ref: "pod/a", source: "archived",
        entries: [{ pod: "p-preflight", log: "DMS_PREFLIGHT_REASON=destination_not_writable" }] });
    }),
  );
  serve({ ...REQ, state: "Rejected", reason_code: "destination_not_writable" }, [{ ...JOB, state: "Rejected",
    reason_code: "destination_not_writable", result_summary: null, preview_summary: null, phase_refs: { preflight: "pod/a" },
    transitions: [tr(null, "Pending", 0), tr("Pending", "Preflight", 2), tr("Preflight", "Rejected", 5)] }]);
  renderAt();
  expect(await screen.findByText("DMS_PREFLIGHT_REASON=destination_not_writable")).toBeInTheDocument();
  expect(asked).toEqual(["preflight"]);
  expect(bodies).toBe(0);
  const { exec } = await regions();
  expect(within(exec).getByText("실행 단계가 시작되지 않아 데이터는 변경되지 않았습니다.")).toBeInTheDocument();
  expect(screen.getByRole("heading", { level: 2, name: "사전 점검 단계에서 거부되었습니다" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "실패 지점 로그 보기" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "새 작업 제출" })).toHaveAttribute("href", "/jobs/new");
});

test("5 재점검 거부(마커 사유, execution ref 없음): ② 재점검 행 실패, 실행 행 「실행 안 됨」", async () => {
  serve({ ...REQ, state: "Rejected" }, [{ ...JOB, state: "Rejected", reason_code: "destination_not_writable",
    result_summary: null, phase_refs: { preflight: "a", preview: "b", exec_preflight: "c" },
    transitions: [...SYNC_OK_TR.slice(0, 5), tr("Executing", "Rejected", 22)] }]);
  renderAt();
  const { exec } = await regions();
  const rows = within(within(exec).getByRole("list", { name: "실행 단계" })).getAllByRole("listitem");
  expect(rows[0]).toHaveTextContent("실행 직전 재점검: 거부됨");
  expect(rows[1]).toHaveTextContent("실행: 실행 안 됨");
  expect(screen.getByRole("heading", { level: 2, name: "실행 직전 재점검 단계에서 거부되었습니다" })).toBeInTheDocument();
});

test("6 미리보기 실패의 result_summary 는 ①의 「미리보기 결과 (실패 시점)」에만 -- ②에 「실행 결과」 없음", async () => {
  serve({ ...REQ, state: "Failed" }, [{ ...JOB, state: "Failed", reason_code: "preview_failed:rc2",
    result_summary: { files: 3, bytes: 9, returncode: 2 }, preview_summary: null,
    phase_refs: { preflight: "a", preview: "b" },
    transitions: [tr(null, "Pending", 0), tr("Pending", "Preflight", 1), tr("Preflight", "PreviewRunning", 3), tr("PreviewRunning", "Failed", 9)] }]);
  renderAt();
  const { pre, exec } = await regions();
  expect(within(pre).getByText("미리보기 결과 (실패 시점)")).toBeInTheDocument();
  expect(within(exec).queryByText("실행 결과")).toBeNull();
  expect(screen.queryByText("returncode")).toBeNull();     // 원 키 타일(실행 결과)로 그리지 않았다
});

test("7 단건 컨펌 대기: 「작업 컨펌」 버튼은 정확히 1개, 「미리보기 결과 보기」는 ①의 제목으로 포커스", async () => {
  serve({ ...REQ, state: "Planned" }, [{ ...JOB, state: "ConfirmPending", result_summary: null,
    preview_fingerprint: "sha256:fp", preview_expires_at: "2099-01-01T00:00:00Z",
    preview_summary: { files: 1204, bytes: 3435973837, returncode: 0 }, phase_refs: { preflight: "a", preview: "b" },
    transitions: SYNC_OK_TR.slice(0, 4) }]);
  renderAt();
  await screen.findByRole("heading", { level: 2, name: "컨펌을 기다리고 있습니다" });
  expect(screen.getAllByRole("button", { name: "작업 컨펌" })).toHaveLength(1);
  await userEvent.click(screen.getByRole("button", { name: "미리보기 결과 보기" }));
  await waitFor(() => expect(screen.getByText("미리보기 결과")).toHaveFocus());
  expect(screen.getByText(/^유효기간 2099-01-01 09:00:00 KST · /)).toBeInTheDocument();
});

test("8 E6 거울: Succeeded 요청 + 잡 2개 = 정확 일치 「Succeeded」 3개, job_id 는 각 1개", async () => {
  serve(REQ, [JOB, { ...JOB, job_id: "j2" }]);
  renderAt();
  await screen.findByText("j2");
  expect(screen.getAllByText("Succeeded", { exact: true })).toHaveLength(3);
  expect(screen.getAllByText("j1", { exact: true })).toHaveLength(1);
  expect(screen.getAllByText("j2", { exact: true })).toHaveLength(1);
});

test("9 「전이 이력」을 이름에 품은 heading 은 하나뿐(e2e 04 부분 일치)", async () => {
  serve(REQ, [JOB]);
  renderAt();
  await regions();
  expect(screen.getAllByRole("heading", { name: /전이 이력/ })).toHaveLength(1);
});

test("10 잡 transitions null · phase_refs 문자열이어도 죽지 않고 「상태 전이 0건」은 비활성", async () => {
  serve({ ...REQ, state: "Failed" }, [{ ...JOB, state: "Failed", transitions: null, phase_refs: "oops" }]);
  renderAt();
  expect(await screen.findByRole("button", { name: "상태 전이 0건" })).toBeDisabled();
  expect(screen.queryAllByText("전이 이력이 없습니다")).toHaveLength(0);   // 요청 전이는 있다 -- 잡 쪽 빈 문구도 없다
});

test("11 TimedOut 잡: 종단이라 「취소」 버튼이 없고 pill 은 bad 색", async () => {
  serve({ ...REQ, state: "Failed" }, [{ ...JOB, state: "TimedOut", reason_code: "execution_failed:deadline",
    transitions: [...SYNC_OK_TR.slice(0, 6), tr("Executing", "TimedOut", 50)] }]);
  renderAt();
  const pill = await screen.findByText("TimedOut", { exact: true });
  expect(pill.className).toContain("text-bad");
  expect(screen.queryByRole("button", { name: "취소" })).toBeNull();
  expect(screen.queryByText("자동 갱신 중")).toBeNull();
});

test("12 데이터가 있는 상태에서 재조회가 실패하면 띠만 뜨고 화면(잡 카드)은 남는다", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let fail = false;
  server.use(
    http.get("/api/user/requests/r1", () => (fail
      ? HttpResponse.json({ detail: "http_500" }, { status: 500 })
      : HttpResponse.json({ ...REQ, state: "Planned" }))),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json([{ ...JOB, state: "Executing", result_summary: null,
      transitions: SYNC_OK_TR.slice(0, 6) }])),
  );
  renderAt();
  expect(await screen.findByText("j1")).toBeInTheDocument();
  expect(screen.getByText("자동 갱신 중")).toBeInTheDocument();
  fail = true;
  await act(async () => { await vi.advanceTimersByTimeAsync(3200); });
  expect(await screen.findByText(/^자동 갱신에 실패했습니다 — .* 기준 화면입니다\.$/)).toBeInTheDocument();
  expect(screen.getByText("j1")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).toBeNull();          // 화면 전체 오류로 바꾸지 않는다
});

test("13 잡 0개 + 요청 Pending: 2초 뒤 잡을 다시 읽는다(빈 배열 함정), 요청이 Rejected 면 다시 읽지 않는다", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let jobCalls = 0;
  const count = () => { jobCalls += 1; return []; };
  serve({ ...REQ, state: "Pending", transitions: [tr(null, "Pending", 0)] }, count);
  const view = renderAt();
  expect(await screen.findByText(/^작업을 계획하는 중입니다/)).toBeInTheDocument();
  const first = jobCalls;
  await act(async () => { await vi.advanceTimersByTimeAsync(2200); });
  await waitFor(() => expect(jobCalls).toBeGreaterThan(first));
  view.unmount();

  jobCalls = 0;
  serve({ ...REQ, state: "Rejected", reason_code: "ldap_identity_not_found" }, count);
  renderAt();
  expect(await screen.findByText(/^만들어진 작업이 없습니다/)).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(4500); });
  expect(jobCalls).toBe(1);
});

test("14 요청이 비종단이면 요청 자신도 3초마다 다시 읽고, 종단이면 읽지 않는다", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let reqCalls = 0;
  let state = "Planned";
  server.use(
    http.get("/api/user/requests/r1", () => { reqCalls += 1; return HttpResponse.json({ ...REQ, state }); }),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json([{ ...JOB, state: "Executing", transitions: SYNC_OK_TR.slice(0, 6) }])),
  );
  renderAt();
  await screen.findByText("j1");
  expect(reqCalls).toBe(1);
  state = "Succeeded";
  await act(async () => { await vi.advanceTimersByTimeAsync(3200); });
  await waitFor(() => expect(reqCalls).toBe(2));
  await act(async () => { await vi.advanceTimersByTimeAsync(7000); });
  expect(reqCalls).toBe(2);   // 종단 응답을 받은 뒤로는 멈춘다
});

test("15 잡 상태가 바뀌면 아티팩트 목록을 다시 읽는다(새 출력 파일)", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let listCalls = 0;
  let jobState = "Executing";
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json({ ...REQ, state: "Planned" })),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json([{ ...JOB, state: jobState,
      transitions: jobState === "Succeeded" ? SYNC_OK_TR : SYNC_OK_TR.slice(0, 6) }])),
    http.get("/api/user/jobs/j1/artifacts", () => { listCalls += 1; return HttpResponse.json({ entries: [], truncated: false }); }),
  );
  renderAt();
  await screen.findByText("j1");
  await waitFor(() => expect(listCalls).toBe(1));
  jobState = "Succeeded";
  await act(async () => { await vi.advanceTimersByTimeAsync(2200); });
  await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
});

test("16 주석: 이 잡의 phase 이벤트는 ① 미리보기 행에, 다른 잡 것은 안 붙고, 정보뿐인 진단 카드는 접힌다", async () => {
  serve({ ...REQ, events: [
    { id: 1, component: "stepper", severity: "info", event_type: "identity_groups_checked",
      message: "보조 그룹 재확인 통과 preview gids=[10010]", payload: { job_id: "j1", phase: "preview", gids: [10010] }, at: at(7) },
    { id: 2, component: "stepper", severity: "info", event_type: "identity_groups_checked",
      message: "남의 잡", payload: { job_id: "zz", phase: "preview" }, at: at(8) },
  ] }, [JOB]);
  renderAt();
  const { pre } = await regions();
  const rows = within(within(pre).getByRole("list", { name: "사전 점검·미리보기 단계" })).getAllByRole("listitem");
  expect(within(rows[1]).getByText(/^보조 그룹 재확인 통과 preview gids=\[10010\] · /)).toBeInTheDocument();
  expect(within(pre).queryByText(/^남의 잡 · /)).toBeNull();
  const toggle = screen.getByRole("button", { name: /진단 이벤트/ });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByText("진단 이벤트")).toBeInTheDocument();
  await userEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
});

test("17 KPI: 결과 {files:120, bytes:456} → 「120개」·「456 B」, rm bytes null 은 크기 칸 없음, 0 은 「0개」", async () => {
  serve({ ...REQ, state: "Failed" }, [{ ...JOB, state: "Succeeded", result_summary: { files: 120, bytes: 456 } }]);
  const v1 = renderAt();
  expect(await screen.findByText("120개")).toBeInTheDocument();
  expect(screen.getByText("456 B")).toBeInTheDocument();
  v1.unmount();
  // 미리보기 타일에도 「크기」가 있으니 미리보기 요약을 비워 KPI 칸만 본다.
  serve({ ...REQ, operation: "rm" }, [{ ...JOB, operation: "rm", result_summary: { files: 0, bytes: null }, preview_summary: null }]);
  renderAt();
  expect(await screen.findByText("0개")).toBeInTheDocument();
  expect(screen.getByText("삭제한 항목")).toBeInTheDocument();
  expect(screen.queryByText("크기")).toBeNull();
});

test("V1 잡 첫 조회 실패(요청 비종단): 화면이 2초마다 로딩 골격으로 흔들리지 않고, 「다시 시도」 중에도 상자·문구가 남는다", async () => {
  // 리뷰 exp5 이식: 데이터 없는 잡 쿼리가 2초마다 다시 돌며 pending 으로 돌아가 h1·배너·오류 상자가 통째로 사라졌다.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let jobCalls = 0;
  let release: (() => void) | null = null;
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json({ ...REQ, state: "Planned",
      transitions: [tr(null, "Pending", 0), tr("Pending", "Planned", 2)] })),
    http.get("/api/user/requests/r1/jobs", async () => {
      jobCalls += 1;
      if (jobCalls >= 2) await new Promise<void>((r) => { release = r; });   // 재조회를 잠깐 붙잡아 둔다
      return HttpResponse.json({ detail: "http_500" }, { status: 500 });
    }),
  );
  renderAt();
  expect(await screen.findByRole("heading", { level: 1, name: "sync 요청" })).toBeInTheDocument();
  expect(screen.getByText("서버 오류가 발생했습니다")).toBeInTheDocument();
  // 데이터 없이 실패한 잡 쿼리는 2초 폴링을 멈춘다(스펙 §5.2 잡 모름 = 요청 3s 만)
  await act(async () => { await vi.advanceTimersByTimeAsync(4500); });
  expect(jobCalls).toBe(1);
  expect(screen.getByRole("heading", { level: 1, name: "sync 요청" })).toBeInTheDocument();
  // 상자의 「다시 시도」: 다시 읽는 동안에도 골격으로 바뀌지 않고 마지막 오류 문구가 남는다
  await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));
  await waitFor(() => expect(jobCalls).toBe(2));
  expect(screen.queryByRole("heading", { level: 1, name: "요청 상세" })).toBeNull();
  expect(screen.getByRole("heading", { level: 1, name: "sync 요청" })).toBeInTheDocument();
  expect(screen.getByText("서버 오류가 발생했습니다")).toBeInTheDocument();
  await act(async () => { release?.(); });
  expect(await screen.findByText("서버 오류가 발생했습니다")).toBeInTheDocument();
});

test("V2 잡 2개(거부 + 비배치 컨펌 대기): 실패 잡이 배너 초점이어도 「작업 컨펌」 버튼은 정확히 1개(관문 줄)", async () => {
  // 리뷰 exp1 이식: 예전엔 초점(실패)의 갈래가 컨펌 소유를 'none' 으로 정해 컨펌 대기 잡을 이 화면에서 컨펌할 수 없었다.
  const failed = { ...JOB, job_id: "jA", state: "Rejected", reason_code: "destination_not_writable", result_summary: null,
    preview_summary: null, phase_refs: { preflight: "pod/a" },
    transitions: [tr(null, "Pending", 0), tr("Pending", "Preflight", 2), tr("Preflight", "Rejected", 5)] };
  const confirm = { ...JOB, job_id: "jB", state: "ConfirmPending", result_summary: null, preview_fingerprint: "fp",
    preview_expires_at: "2099-01-01T00:00:00Z", phase_refs: { preflight: "pod/b", preview: "pod/c" },
    transitions: SYNC_OK_TR.slice(0, 4) };
  server.use(http.get("/api/user/jobs/:jid/logs", () => HttpResponse.json({ source: "archived", entries: [] })));
  serve({ ...REQ, state: "Planned" }, [failed, confirm]);
  renderAt();
  await screen.findByText("jB");
  expect(screen.getAllByRole("button", { name: "작업 컨펌" })).toHaveLength(1);
  expect(screen.getByText(/다른 작업 1개가 컨펌을 기다립니다/)).toBeInTheDocument();
});

test("V6 관문 거부(미리보기 제출 전): 배너는 다음 단계를 말하고 「실패 지점 로그 보기」·로그 자동 조회가 없다", async () => {
  const asked: string[] = [];
  server.use(http.get("/api/user/jobs/j1/logs", ({ request }) => {
    asked.push(new URL(request.url).searchParams.get("phase") ?? "");
    return HttpResponse.json({ phase: "preflight", ref: "pod/a", source: "live", entries: [] });
  }));
  serve({ ...REQ, state: "Rejected", reason_code: "identity_changed_at_step", events: [
    { id: 1, component: "stepper", severity: "warning", event_type: "identity_changed_at_step",
      message: "LDAP 변경으로 중단 missing=[10010] job=j1", at: at(30),
      payload: { job_id: "j1", phase: "preview", queued: false, missing_gids: [10010] } },
  ] }, [{ ...JOB, state: "Rejected", reason_code: "identity_changed_at_step", result_summary: null, preview_summary: null,
    phase_refs: { preflight: "pod/a" }, transitions: [tr(null, "Pending", 0), tr("Pending", "Preflight", 2), tr("Preflight", "Rejected", 30)] }]);
  renderAt();
  expect(await screen.findByRole("heading", { level: 2, name: "미리보기를 시작하기 전에 거부되었습니다" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "실패 지점 로그 보기" })).toBeNull();
  const { pre } = await regions();
  const rows = within(within(pre).getByRole("list", { name: "사전 점검·미리보기 단계" })).getAllByRole("listitem");
  expect(rows[0]).toHaveTextContent("사전 점검: 완료");
  // 백엔드 이벤트가 붙는 행 = 거부로 칠한 행
  expect(within(rows[1]).getByText(/^LDAP 변경으로 중단 missing=\[10010\] job=j1 · /)).toBeInTheDocument();
  await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  expect(asked).toEqual([]);
});

test("18 배너 제목 표", async () => {
  const CP = { ...JOB, state: "ConfirmPending", result_summary: null, transitions: SYNC_OK_TR.slice(0, 4),
    phase_refs: { preflight: "a", preview: "b" } };
  const cases: [object, object[], string][] = [
    [REQ, [JOB], "작업이 완료되었습니다"],
    [{ ...REQ, state: "Failed" }, [{ ...JOB, state: "Failed", reason_code: "execution_failed:rc1",
      transitions: [...SYNC_OK_TR.slice(0, 6), tr("Executing", "Failed", 30)] }], "실행 단계에서 실패했습니다"],
    [{ ...REQ, state: "Rejected" }, [{ ...JOB, state: "Rejected", reason_code: "destination_not_writable", result_summary: null,
      phase_refs: { preflight: "a" }, transitions: [tr(null, "Pending", 0), tr("Pending", "Preflight", 2), tr("Preflight", "Rejected", 5)] }],
      "사전 점검 단계에서 거부되었습니다"],
    [{ ...REQ, state: "Planned" }, [CP], "컨펌을 기다리고 있습니다"],
    [{ ...REQ, state: "Failed" }, [{ ...CP, state: "PreviewExpired", transitions: [...SYNC_OK_TR.slice(0, 4), tr("ConfirmPending", "PreviewExpired", 40)] }],
      "미리보기가 만료되어 실행되지 않았습니다"],
    [{ ...REQ, state: "Cancelled" }, [{ ...CP, state: "Cancelled", transitions: [...SYNC_OK_TR.slice(0, 4), tr("ConfirmPending", "Cancelled", 30)] }],
      "컨펌 전에 취소되었습니다"],
    [{ ...REQ, state: "Rejected", reason_code: "ldap_identity_not_found" }, [], "계획 단계에서 거부되었습니다"],
  ];
  for (const [req, jobs, title] of cases) {
    serve(req, jobs);
    const view = renderAt();
    expect(await screen.findByRole("heading", { level: 2, name: title })).toBeInTheDocument();
    view.unmount();
  }
});
