import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";
import contract from "../../lib/progressContract.json";
import { reasonText } from "../../lib/api";
import type { DataJob, JobProgressResponse, ProgressSnapshot } from "../../lib/types";
import { JobProgress } from "./JobProgress";
import { BASIS, NOTE } from "./progressModel";
import type { StepModel } from "./stageModel";

// 응답·스냅숏은 계약 파일(lib/progressContract.json examples)에서 그대로 -- 손으로 쓴 모의 모양 금지(설계 §11.2).
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

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
  submitFailed: false, notStarted: false, queued: false, schedWaitSec: id === "execution" ? 2 : null, ...over,
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
  const facts = ["전체 40,000 / 100,105 항목", "현재 단계 73%", "복사 약 1.0 GiB", "평균 51.3 MiB/s"];
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
  const bar = screen.getByRole("progressbar", { name: `실행 진행률(${BASIS.batch})` });
  expect(bar).toHaveAttribute("aria-valuenow", "0");                 // 끝난 배치 0 / 1 -- 0 은 정상값
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
