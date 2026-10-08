import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect, vi } from "vitest";
import { RequestDetail } from "./RequestDetail";

// 요청 상세 재설계(2026-10-08) 추가 테스트(스펙 §11.1 추가 1~18). 기존 RequestDetail.test.tsx(42건)는 그대로 통과해야
// 한다는 것이 이 재설계의 계약이라(예외는 d165 의 bytes 괄호 중복 제거 1건 — 의도한 표기 변경), 추가분은 같은 MSW 하네스를
// 복제한 이 파일에 둔다.

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

test("7 단건 컨펌 대기: 「작업 컨펌」 버튼은 정확히 1개(관문 줄), 배너 「컨펌하러 가기」는 그 버튼으로 포커스, 「미리보기 결과 보기」는 ①의 제목으로 포커스", async () => {
  serve({ ...REQ, state: "Planned" }, [{ ...JOB, state: "ConfirmPending", result_summary: null,
    preview_fingerprint: "sha256:fp", preview_expires_at: "2099-01-01T00:00:00Z",
    preview_summary: { files: 1204, bytes: 3435973837, returncode: 0 }, phase_refs: { preflight: "a", preview: "b" },
    transitions: SYNC_OK_TR.slice(0, 4) }]);
  renderAt();
  await screen.findByRole("heading", { level: 2, name: "컨펌을 기다리고 있습니다" });
  const confirmBtns = screen.getAllByRole("button", { name: "작업 컨펌" });
  expect(confirmBtns).toHaveLength(1);
  // 리뷰 N5: 창은 배너가 아니라 잡 카드의 관문 줄에 산다(「데이터 작업」 구획 안)
  expect(confirmBtns[0].closest('section[aria-labelledby="jobs-h"]')).not.toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "컨펌하러 가기" }));
  await waitFor(() => expect(confirmBtns[0]).toHaveFocus());
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
  // KPI 칸으로 집는다 -- 1 KiB 미만이면 아래 「실행 결과」 bytes 타일도 같은 「456 B」다.
  expect(screen.getByText("복사한 크기").nextElementSibling?.textContent).toBe("456 B");
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

test("N5 열어 둔 컨펌 창은 폴링으로 다른 잡이 실패해 배너 초점이 바뀌어도 닫히지 않는다(창은 관문 줄에만)", async () => {
  // 리뷰 N5(fixcheck/v2flip.test.tsx): 예전엔 단건 초점이면 창이 배너에 있다가, 다른 잡이 실패해 초점이 옮겨 가면
  // 배너의 창이 언마운트되고 관문 줄에 닫힌 새 창이 생겼다 -- 컨펌하던 사람의 창이 눈앞에서 사라졌다.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const TO_CP = SYNC_OK_TR.slice(0, 4);
  const cp = { ...JOB, job_id: "jCP", state: "ConfirmPending", result_summary: null, preview_fingerprint: "fp",
    preview_expires_at: "2099-01-01T00:00:00Z", phase_refs: { preflight: "a", preview: "b" }, transitions: TO_CP };
  const pre = { ...JOB, job_id: "jPF", state: "Preflight", result_summary: null, preview_summary: null,
    phase_refs: { preflight: "c" }, transitions: [tr(null, "Pending", 0), tr("Pending", "Preflight", 2)] };
  const preFailed = { ...pre, state: "Rejected", reason_code: "destination_not_writable",
    transitions: [...pre.transitions, tr("Preflight", "Rejected", 30)] };
  let jobs: object[] = [pre, cp];
  server.use(http.get("/api/user/jobs/:jid/logs", () => HttpResponse.json({ source: "archived", entries: [] })));
  serve({ ...REQ, state: "Planned", transitions: [tr(null, "Pending", 0), tr("Pending", "Planned", 2)] }, () => jobs);
  renderAt();
  await screen.findByText("jCP");
  const btns = screen.getAllByRole("button", { name: "작업 컨펌" });
  expect(btns).toHaveLength(1);
  await userEvent.click(btns[0]);
  expect(await screen.findByRole("dialog", { name: "sync 작업 컨펌" })).toBeInTheDocument();
  jobs = [preFailed, cp];                                   // 다음 2초 폴링에서 다른 잡이 거부된다 → 배너 초점이 그 잡으로
  await act(async () => { await vi.advanceTimersByTimeAsync(2200); });
  await waitFor(() => expect(screen.getByText(/다른 작업 1개가 컨펌을 기다립니다/)).toBeInTheDocument());
  expect(screen.getByRole("dialog", { name: "sync 작업 컨펌" })).toBeInTheDocument();
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
});

test("N2 잡 조회 실패가 캐시에 남은 채 다시 들어오면: 재조회 동안 붉은 경보 대신 중립 골격, 다시 실패하면 그 문구로 경보", async () => {
  // 리뷰 N2(fixcheck/logs/v1edge.test.tsx G): 새 인스턴스는 오류 문구를 기억하지 못하고 재조회가 error 를 비워, 붉은
  // role=alert 상자가 「작업 정보를 다시 불러오는 중…」을 경보로 읽혔다.
  let jobCalls = 0;
  let hold: (() => void) | null = null;
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json({ ...REQ, state: "Planned",
      transitions: [tr(null, "Pending", 0), tr("Pending", "Planned", 2)] })),
    http.get("/api/user/requests/r1/jobs", async () => {
      jobCalls += 1;
      if (jobCalls >= 2) await new Promise<void>((r) => { hold = r; });
      return HttpResponse.json({ detail: "http_500" }, { status: 500 });
    }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = (show: boolean) => (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/jobs/r1"]}>
        <Routes><Route path="/jobs/:requestId" element={show ? <RequestDetail /> : <p>다른 화면</p>} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
  const view = render(tree(true));
  expect(await screen.findByRole("alert")).toHaveTextContent("서버 오류가 발생했습니다");
  view.rerender(tree(false));
  await screen.findByText("다른 화면");
  view.rerender(tree(true));                               // gcTime 안에 돌아온다 -- 캐시엔 데이터 없는 실패가 남아 있다
  await waitFor(() => expect(jobCalls).toBe(2));
  expect(screen.getByRole("heading", { level: 1, name: "sync 요청" })).toBeInTheDocument();   // 페이지 골격 아님
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByText("작업 정보를 불러오는 중…")).toHaveAttribute("role", "status");
  // 리뷰 3차(round3/query/n2.test.tsx N2-2): 배너도 같은 이야기를 한다 -- 한 화면이 「불러오지 못해」와 「불러오는 중」을
  // 동시에 말하지 않는다.
  expect(screen.getByText("작업 정보를 불러오는 중입니다")).toBeInTheDocument();
  expect(screen.queryByText("작업 정보를 불러오지 못해 단계별 결과를 보일 수 없습니다")).toBeNull();
  await act(async () => { hold?.(); });
  expect(await screen.findByRole("alert")).toHaveTextContent("서버 오류가 발생했습니다");
  expect(screen.queryByText("작업 정보를 불러오는 중…")).toBeNull();
  expect(screen.getByText("작업 정보를 불러오지 못해 단계별 결과를 보일 수 없습니다")).toBeInTheDocument();
  expect(screen.queryByText("작업 정보를 불러오는 중입니다")).toBeNull();
});

test("3차 N3 그룹 잡의 base 거부를 잡 폴링이 먼저 보면(관문 이벤트 전): 사전 점검 로그를 열지 않고, 이벤트가 오면 관문으로", async () => {
  // round3/stage/skew.test.tsx: stepper 는 이벤트 → 잡·요청 종단을 한 틱에 남기지만 화면은 잡(2초)·요청(3초)을 따로 읽는다.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const asked: string[] = [];
  server.use(http.get("/api/user/jobs/j1/logs", ({ request }) => {
    asked.push(new URL(request.url).searchParams.get("phase") ?? "");
    return HttpResponse.json({ phase: "preflight", ref: "pod/a", source: "live", entries: [{ pod: "p", log: "ok\n" }] });
  }));
  const PRE = [tr(null, "Pending", 0), tr("Pending", "Preflight", 2)];
  const running = { ...JOB, state: "Preflight", result_summary: null, preview_summary: null, phase_refs: { preflight: "pod/a" },
    transitions: PRE, worker_pool: { identity: { username: "alice", uid: 1000, gid: 1000, supplementary_gids: [10010],
      supplementary_gids_status: "applied" } } };
  const gated = { ...running, state: "Rejected", reason_code: "artifact_base_not_traversable",
    transitions: [...PRE, tr("Preflight", "Rejected", 30)] };
  const reqRow = (state: string, events: object[]) => ({ ...REQ, state, events,
    transitions: [tr(null, "Pending", 0), tr("Pending", "Planned", 1)] });
  const GATE_EV = { id: 7, component: "stepper", severity: "error", event_type: "artifact_base_unsafe_at_step",
    message: "artifact_base_not_traversable job=j1", at: at(30), payload: { job_id: "j1", problem: "artifact_base_not_traversable" } };
  let job: object = running;
  let req: object = reqRow("Planned", []);
  server.use(
    http.get("/api/user/requests/r1", () => HttpResponse.json(req)),
    http.get("/api/user/requests/r1/jobs", () => HttpResponse.json([job])),
  );
  renderAt();
  expect(await screen.findByRole("heading", { level: 2, name: "사전 점검 중입니다" })).toBeInTheDocument();
  job = gated;                                               // 잡 폴링(2초)이 먼저 종단을 본다
  await act(async () => { await vi.advanceTimersByTimeAsync(2200); });
  expect(await screen.findByRole("heading", { level: 2, name: "작업이 거부되었습니다" })).toBeInTheDocument();
  const { pre } = await regions();
  expect(within(pre).getByText("위치 추정")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "실패 지점 로그 보기" })).toBeNull();
  req = reqRow("Rejected", [GATE_EV]);                       // 요청 폴링(3초)이 이벤트를 가져온다
  await act(async () => { await vi.advanceTimersByTimeAsync(3200); });
  expect(await screen.findByRole("heading", { level: 2, name: "미리보기를 시작하기 전에 거부되었습니다" })).toBeInTheDocument();
  const rows = within(within(pre).getByRole("list", { name: "사전 점검·미리보기 단계" })).getAllByRole("listitem");
  expect(rows[0]).toHaveTextContent("사전 점검: 완료");
  expect(pre.querySelector("[id^='viewer-j1-']")).toBeNull();      // 통과한 단계 로그가 열린 채 남지 않는다
  expect(asked).toEqual([]);                                 // 한 번도 자동으로 열지 않았다
});

test("3차 라이브 로그의 마지막 읽기: 첫 조회 중에 단계가 끝나도 뷰어 본문이 빈 채로 커밋되지 않는다(「불러오는 중」 → 마지막 내용)", async () => {
  // round3/query: 취소의 동기 되돌림(pending/idle) 뒤 `.then(refetch)` 를 기다리는 사이, 같은 flush 의 다른 setState(구획
  // 상태 낭독 announce)가 그린 렌더에서 뷰어 본문이 비었다(isLoading false · 데이터 없음). 취소 바로 뒤 같은 틱에 다시 읽는다.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let logCalls = 0;
  const holds: (() => void)[] = [];
  server.use(http.get("/api/user/jobs/j1/logs", async () => {
    const n = ++logCalls;
    if (n === 1) await new Promise<void>((r) => { holds.push(r); });
    return HttpResponse.json({ phase: "preflight", ref: "pod/a", source: n === 1 ? "live" : "archived",
      entries: [{ pod: "p", log: n === 1 ? "낡은 라이브 조각" : "DMS_PREFLIGHT_REASON=destination_not_writable" }] });
  }));
  const PRE = [tr(null, "Pending", 0), tr("Pending", "Preflight", 2)];
  const running = { ...JOB, state: "Preflight", result_summary: null, preview_summary: null, phase_refs: { preflight: "pod/a" },
    transitions: PRE };
  let job: object = running;
  serve({ ...REQ, state: "Planned", transitions: [tr(null, "Pending", 0), tr("Pending", "Planned", 1)] }, () => [job]);
  renderAt();
  const { pre } = await regions();
  await userEvent.click(within(pre).getByRole("button", { name: "preflight 로그" }));
  const viewer = await within(pre).findByRole("region", { name: "preflight 로그 내용" });
  expect(within(viewer).getByText("내용을 불러오는 중…")).toBeInTheDocument();
  await waitFor(() => expect(logCalls).toBe(1));
  const blanks: string[] = [];
  const mo = new MutationObserver(() => {
    const v = document.getElementById("viewer-j1-pre");
    const t = v?.textContent ?? "";
    if (v && !t.includes("내용을 불러오는 중…") && !t.includes("DMS_PREFLIGHT_REASON") && !t.includes("낡은 라이브 조각")) blanks.push(t);
  });
  mo.observe(document.body, { subtree: true, childList: true, characterData: true });
  job = { ...running, state: "Rejected", reason_code: "destination_not_writable",
    transitions: [...PRE, tr("Preflight", "Rejected", 5)] };
  await act(async () => { await vi.advanceTimersByTimeAsync(2200); });
  expect(await within(pre).findByText("DMS_PREFLIGHT_REASON=destination_not_writable")).toBeInTheDocument();
  await act(async () => { holds[0]?.(); await vi.advanceTimersByTimeAsync(50); });
  mo.disconnect();
  expect(blanks).toEqual([]);
  expect(logCalls).toBe(2);
  expect(within(pre).queryByText("낡은 라이브 조각")).toBeNull();   // 취소된 첫 조회의 낡은 응답은 버려진다
});

test("3차 대비: 잡 카드·결과 타일·컨펌 창의 의미 있는 글자는 text-muted(#888, 3.54:1)를 쓰지 않는다(장식 코드 라벨만 예외)", async () => {
  // round3/a11y/contrast-sweep.mjs: ToolLabel·「보조 그룹(gid)」 dt·스토리지 주의문·「실행 결과」 원 키 dt·컨펌 창 본문이
  // 흰 카드 위 3.54:1 이었다(스펙 §9: muted 는 장식 전용). 요청 pill(StatusPill neutral)은 이 화면 범위 밖이라 보지 않는다.
  const ident = { username: "alice", uid: 1000, gid: 1000, supplementary_gids: [10010], supplementary_gids_status: "applied" };
  const done = { ...JOB, tool: "dsync", worker_pool: { node_count: 4, identity: ident }, source_storage: "s1",
    destination_storage: "s2" };
  const cp = { ...done, job_id: "j2", state: "ConfirmPending", result_summary: null, preview_fingerprint: "sha256:abc",
    preview_expires_at: "2099-01-01T00:00:00Z", phase_refs: { preflight: "a", preview: "b" }, transitions: SYNC_OK_TR.slice(0, 4) };
  serve({ ...REQ, state: "Planned", transitions: [tr(null, "Pending", 0), tr("Pending", "Planned", 2)] }, [done, cp]);
  renderAt();
  const jobs = await screen.findByRole("region", { name: /^데이터 작업/ });
  await within(jobs).findAllByText("dsync · 4 노드");
  await userEvent.click(within(jobs).getByRole("button", { name: "작업 컨펌" }));
  const dlg = await screen.findByRole("dialog", { name: "sync 작업 컨펌" });
  const offenders = [jobs, dlg].flatMap((root) => Array.from(root.querySelectorAll<HTMLElement>(".text-muted")))
    .filter((el) => !(el.classList.contains("hidden") && el.classList.contains("sm:inline")))   // 장식 코드 라벨(§9)
    .filter((el) => (el.textContent ?? "").trim() !== "")
    .map((el) => el.textContent);
  expect(offenders).toEqual([]);
  expect(within(jobs).getAllByText("dsync · 4 노드")[0]).toHaveClass("text-ink/70");
  expect(within(jobs).getAllByText("보조 그룹(gid)")[0]).toHaveClass("text-ink/70");
  expect(within(jobs).getByText("bytes")).toHaveClass("text-ink/70");
  expect(within(dlg).getByText(/^지문\(fingerprint\)/)).toHaveClass("text-ink/70");
  expect(within(dlg).getByText(/^만료: /)).toHaveClass("text-ink/70");
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

test("리뷰 4차: 그룹 잡의 artifact_base_* 마커 거부는 종단 요청 응답이면 사전 점검 실패로 확정하고 그 로그를 연다", async () => {
  // requestTerminal 이 RequestDetail → JobCard → JobStages 까지 같은 값으로 흘러야 배너와 단계 카드가 같은 말을 한다.
  const asked: string[] = [];
  server.use(http.get("/api/user/jobs/j1/logs", ({ request }) => {
    asked.push(new URL(request.url).searchParams.get("phase") ?? "");
    return HttpResponse.json({ phase: "preflight", ref: "pod/a", source: "archived",
      entries: [{ pod: "p-preflight", log: "DMS_PREFLIGHT_REASON=artifact_base_group_writable" }] });
  }));
  const groupJob = { ...JOB, state: "Rejected", reason_code: "artifact_base_group_writable", result_summary: null,
    preview_summary: null, phase_refs: { preflight: "pod/a" },
    worker_pool: { identity: { username: "alice", uid: 10001, gid: 10000, supplementary_gids: [10010],
      supplementary_gids_status: "applied" } },
    transitions: [tr(null, "Pending", 0), tr("Pending", "Preflight", 2), tr("Preflight", "Rejected", 9)] };
  const events = [{ id: 1, component: "stepper", severity: "info", event_type: "identity_groups_checked", message: null,
    at: at(2), payload: { job_id: "j1", gids: [10010], phase: "preflight" } }];
  // 요청 응답이 아직 비종단(잡 폴링이 먼저 종단을 봤다) -- 추정, 로그를 열지 않는다
  serve({ ...REQ, state: "Planned", events }, [groupJob]);
  const first = renderAt();
  expect(await screen.findByRole("heading", { level: 2, name: "작업이 거부되었습니다" })).toBeInTheDocument();
  await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  expect(asked).toEqual([]);
  first.unmount();
  // 종단 응답 -- 확정
  serve({ ...REQ, state: "Rejected", reason_code: "artifact_base_group_writable", events }, [groupJob]);
  renderAt();
  expect(await screen.findByText("DMS_PREFLIGHT_REASON=artifact_base_group_writable")).toBeInTheDocument();
  expect(screen.getByRole("heading", { level: 2, name: "사전 점검 단계에서 거부되었습니다" })).toBeInTheDocument();
  expect(asked).toEqual(["preflight"]);
});
