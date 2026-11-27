import { act, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest";
import contract from "../../lib/progressContract.json";
import { reasonText } from "../../lib/api";
import type { DataJob, JobProgressResponse, ProgressSnapshot } from "../../lib/types";
import { JobProgress } from "./JobProgress";
import { BASIS, CAPTION, NOTE } from "./progressModel";
import type { StepModel } from "./stageModel";

// 응답·스냅숏은 계약 파일(lib/progressContract.json examples)에서 그대로 -- 손으로 쓴 모의 모양 금지(설계 §11.2).
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => { server.resetHandlers(); vi.useRealTimers(); }); afterAll(() => server.close());

const SNAP: Record<string, ProgressSnapshot> = contract.examples.snapshots;
const RESP = contract.examples.responses;

const JOB: DataJob = {
  job_id: "j1", request_id: "r1", operation: "sync", state: "Executing", reason_code: null, preview_fingerprint: null,
  preview_expires_at: null, result_summary: null, transitions: [], artifact_uri: null, tool: "dsync",
  phase_refs: { preflight: "pod/a", preview: "vcjob/b", exec_preflight: "pod/c", execution: "vcjob/d" },
};
const step = (id: "execution" | "preview", over: Partial<StepModel> = {}): StepModel => ({
  id, stage: id === "execution" ? "exec" : "pre", phase: id, status: "running",
  start: new Date(Date.now() - 600_000).toISOString(), end: null, actor: null, logAvailable: true,
  submitFailed: false, notStarted: false, queued: false, held: null, schedWaitSec: id === "execution" ? 2 : null, ...over,
});

function reply(body: unknown, status = 200) {
  const asked: string[] = [];
  server.use(http.get("/api/user/jobs/j1/progress", ({ request }) => {
    asked.push(new URL(request.url).searchParams.get("phase") ?? "");
    return HttpResponse.json(body as Record<string, unknown>, { status });
  }));
  return asked;
}
function renderProgress(job: Partial<DataJob> = {}, s: StepModel = step("execution")) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><JobProgress job={{ ...JOB, ...job }} step={s} /></QueryClientProvider>);
}

test("머리 「진행 · 단계」·신선도 → 막대(기준 문구·aria) → 사실 줄", async () => {
  const asked = reply(RESP.available);
  renderProgress();
  const g = await screen.findByRole("group", { name: "실행 진행" });
  expect(await within(g).findByText("진행 · 데이터 복사")).toBeInTheDocument();
  expect(within(g).getByText("갱신 4초 전")).toBeInTheDocument();
  const bar = within(g).getByRole("progressbar", { name: `실행 진행률(${BASIS.overall})` });
  expect(bar).toHaveAttribute("aria-valuenow", "39.958");
  expect(bar).toHaveAttribute("aria-valuemin", "0");
  expect(bar).toHaveAttribute("aria-valuemax", "100");
  expect(bar).toHaveAttribute("aria-valuetext", "39.9%");
  expect(within(g).getByText("39.9%")).toBeInTheDocument();
  expect(within(g).getByText(BASIS.overall)).toBeInTheDocument();
  // 사실 조각은 하나씩(줄이 모자라면 조각 단위로 넘긴다), 사이는 「 · 」
  const facts = ["전체 40,000 / 100,105 항목", "현재 단계 73%", "이번 배치 복사 약 1.0 GiB", "평균 51.3 MiB/s",
    "소스 탐색 100,105 항목", "목적지 탐색 0 항목"];
  for (const f of facts) expect(within(g).getByText(f)).toHaveClass("inline-block");
  expect(within(g).getByText(facts[0]).parentElement).toHaveTextContent(facts.join(" · "));
  expect(asked).toEqual(["execution"]);
  // 5초마다 읽어 주면 소음이다 -- 진행 영역 어디에도 aria-live 가 없다
  expect(g.closest("[aria-live]")).toBeNull();
  expect(g.querySelector("[aria-live]")).toBeNull();
});

test("근거가 없으면 막대를 그리지 않는다(수치만) -- 미리보기 행은 phase=preview 로 묻는다", async () => {
  const asked = reply({ phase: "preview", available: true, age_seconds: 0, progress: SNAP.dsync_walk_src });
  renderProgress({}, step("preview"));
  const g = await screen.findByRole("group", { name: "미리보기 진행" });
  expect(await within(g).findByText("진행 · 소스 탐색")).toBeInTheDocument();
  expect(within(g).queryByRole("progressbar")).toBeNull();
  expect(within(g).getByText("탐색 100,105 항목")).toBeInTheDocument();
  expect(asked).toEqual(["preview"]);
});

test("복사 단계 % 막대는 기준 설명(HelpTip)이 붙는다 -- 바이트 기준·배치 안", async () => {
  reply({ phase: "execution", available: true, age_seconds: 1,
          progress: { ...SNAP.dsync_copy_overall, overall: undefined } });
  renderProgress();
  const g = await screen.findByRole("group", { name: "실행 진행" });
  const bar = await within(g).findByRole("progressbar", { name: `실행 진행률(${BASIS.step})` });
  expect(bar).toHaveAttribute("aria-valuenow", "73");
  const tip = within(g).getByRole("button", { name: "진행률 기준 설명" });
  expect(tip).toHaveAccessibleDescription(/바이트 기준이고 배치 안의 값/);
});

test("available:false 는 단정하지 않는 안내(info) -- 「주의」가 아니라 「안내」", async () => {
  reply(RESP.unavailable);
  renderProgress();
  const g = await screen.findByRole("group", { name: "실행 진행" });
  expect(await within(g).findByText(NOTE.noneRunning)).toBeInTheDocument();
  expect(within(g).getByText("진행")).toBeInTheDocument();          // 단계를 모르면 머리는 「진행」만
  expect(within(g).getByText("안내")).toHaveClass("sr-only");
  expect(within(g).queryByText("주의")).toBeNull();
  expect(within(g).queryByRole("progressbar")).toBeNull();
});

// 2026-11-27 dms-k8s d50 실증 F3(net.jsonl 18:16:44.846Z): 실행 중(copy 85%) 응답 하나만 available:false 였는데 화면이 스냅숏을 버리고 5초 동안
// 「진행 정보가 없습니다 — … 이전 잡 이미지로 실행 중이거나…」를 보였다. 한 번 받은 스냅숏은 같은 잡·단계에서 놓지 않는다.
test("실행 중 available:false 한 번 -- 마지막 스냅숏을 그대로 두고(나이는 그 스냅숏 기준), 「이전 잡 이미지」 문구를 내지 않는다", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const seq: unknown[] = [RESP.available, RESP.unavailable];
  let n = 0;
  const asked: string[] = [];
  server.use(http.get("/api/user/jobs/j1/progress", () => {
    asked.push("x");
    return HttpResponse.json((seq[n++] ?? RESP.unavailable) as Record<string, unknown>);
  }));
  renderProgress();
  const g = await screen.findByRole("group", { name: "실행 진행" });
  expect(await within(g).findByText("진행 · 데이터 복사")).toBeInTheDocument();
  expect(within(g).getByText("갱신 4초 전")).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
  await waitFor(() => expect(asked).toHaveLength(2));
  await act(async () => { await vi.advanceTimersByTimeAsync(50); });
  expect(within(g).getByText("진행 · 데이터 복사")).toBeInTheDocument();
  expect(within(g).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "39.958");
  expect(within(g).getByText(/^갱신 (9|10)초 전$/)).toBeInTheDocument();         // 그 스냅숏의 나이 4초 + 받은 뒤 흐른 약 5초
  expect(within(g).queryByText(NOTE.noneRunning)).toBeNull();
  expect(g.textContent).not.toMatch(/진행 정보가|이전 잡 이미지/);
});

test("503 은 사유 문구(주의)", async () => {
  const e = RESP.errors.find((x) => x.status === 503)!;
  reply(e.body, e.status);
  renderProgress();
  const g = await screen.findByRole("group", { name: "실행 진행" });
  expect(await within(g).findByText(reasonText("artifact_base_unavailable"))).toBeInTheDocument();
  expect(within(g).getByText("주의")).toHaveClass("sr-only");
});

test("워커 대기·quiet·nsync 배치 1개 안내", async () => {
  reply({ phase: "execution", available: true, age_seconds: 200, progress: SNAP.workers });
  const v1 = renderProgress();
  expect(await screen.findByText(NOTE.workers)).toBeInTheDocument();
  expect(screen.queryByText("주의")).toBeNull();                     // 200초여도 정체 경고 없음
  v1.unmount();
  reply({ phase: "execution", available: true, age_seconds: 0,
          progress: { v: 1, tool: "nsync", phase: "batches", since_line_s: 1, batch: { done: 0, count: 1 } } });
  renderProgress({ tool: "nsync" });
  expect(await screen.findByText(NOTE.nsyncSingleBatch)).toBeInTheDocument();
  // 배치 1개는 끝날 때까지 0% 막대가 멈춘 것처럼 보인다 -- 막대 없이 수치(0 은 정상값이라 그대로)·안내만
  expect(screen.queryByRole("progressbar")).toBeNull();
  expect(screen.getByText("끝난 배치 0 / 1")).toBeInTheDocument();
});

test("nsync 배치 여럿: 「nsync 는 배치가 끝날 때마다 갱신됩니다」가 늘 작은 회색 캡션(아이콘·「안내」 표지 없음) -- 정체 경고와 따로 선다", async () => {
  reply({ phase: "execution", available: true, age_seconds: 0, progress: SNAP.nsync_batches_exec });
  const v1 = renderProgress({ tool: "nsync" });
  const g = await screen.findByRole("group", { name: "실행 진행" });
  const cap = await within(g).findByText(CAPTION.nsyncPerBatch);
  expect(cap).toHaveAttribute("data-progress-caption");
  expect(cap).toHaveClass("text-ink/70");
  expect(cap.querySelector("svg")).toBeNull();
  expect(within(g).queryByText("안내")).toBeNull();                 // 상태 안내가 아니다(note 없음)
  v1.unmount();
  // 90초 넘게 낡으면 정체 경고(주의)가 따로 한 줄, 캡션은 그대로 회색
  reply({ phase: "execution", available: true, age_seconds: 95, progress: SNAP.nsync_batches_exec });
  renderProgress({ tool: "nsync" });
  const g2 = await screen.findByRole("group", { name: "실행 진행" });
  const warn = await within(g2).findByText(/동안 갱신되지 않았습니다$/);
  const cap2 = within(g2).getByText(CAPTION.nsyncPerBatch);
  expect(warn.closest("p")).toHaveClass("text-attn");
  expect(cap2).not.toHaveClass("text-attn");
  expect(warn.closest("p")).not.toBe(cap2);
});

test("계약 예 스냅숏 전부가 「남은」·「left」 없이 그려진다", async () => {
  for (const [name, snap] of Object.entries(SNAP)) {
    const body: JobProgressResponse = { phase: snap.dryrun ? "preview" : "execution", available: true, age_seconds: 95,
      progress: snap };
    reply(body);
    const view = renderProgress({ tool: snap.tool }, step(snap.dryrun ? "preview" : "execution"));
    const g = await screen.findByRole("group");
    await waitFor(() => expect(g.textContent).toMatch(/^진행 · /));
    for (const bad of contract.ui_forbidden_substrings) expect(g.textContent, name).not.toContain(bad);
    // HelpTip 설명(숨은 노드)도 출력이다
    for (const el of Array.from(g.querySelectorAll("[hidden]"))) {
      for (const bad of contract.ui_forbidden_substrings) expect(el.textContent, name).not.toContain(bad);
    }
    view.unmount();
  }
});
