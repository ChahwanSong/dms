import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect, vi } from "vitest";
import { JobStages, StageBadge, expiryText } from "./JobStages";
import type { DataJob } from "../../lib/types";
import contract from "../../lib/progressContract.json";
import { reasonText } from "../../lib/api";

// 옛 JobViewer.test(17건)를 하네스만 바꿔 옮겼다(2026-10-08 요청 상세 재설계). 칩의 접근성 이름은 옛 탭 이름 그대로
// (「execution/stdout.log」·「preflight 로그」)라 이름·문구·href 단언은 전부 그대로다. 기본 상태가 Succeeded 라 자동
// 열림(종단 실패의 실패 단계 로그)이 끼어들지 않는다 -- 「클릭 전 본문 미조회」·「선택한 phase 만 조회」가 그대로
// 성립한다. 의미가 바뀐 것은 ④ 하나(제출 실패 잡은 로그가 자동으로 열린다).

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => { server.resetHandlers(); vi.useRealTimers(); });
afterAll(() => server.close());

const ARTIFACTS = {
  entries: [
    { phase: "execution", name: "stdout.log", size: 120, modified_at: 1754400000 },
    { phase: "execution", name: "stderr.log", size: 40, modified_at: 1754400001 },
  ],
  truncated: false,
};

const BASE: DataJob = {
  job_id: "j1", request_id: "r1", operation: "sync", state: "Succeeded", reason_code: null,
  preview_fingerprint: null, preview_expires_at: null, result_summary: null,
  transitions: [], artifact_uri: null, phase_refs: { preflight: "pod/p1" },
};

function renderStages(over: Partial<DataJob> = {}, props: { events?: unknown; jobCount?: number } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = (o: Partial<DataJob>) => (
    <QueryClientProvider client={qc}>
      <JobStages job={{ ...BASE, ...o }} events={props.events} jobCount={props.jobCount} />
    </QueryClientProvider>
  );
  const view = render(ui(over));
  return { ...view, rerenderJob: (o: Partial<DataJob>) => view.rerender(ui(o)) };
}

test("renders a tab for each artifact entry plus a log tab per phase_refs key", async () => {
  server.use(http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)));
  renderStages();
  expect(await screen.findByRole("button", { name: "execution/stdout.log" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "execution/stderr.log" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "preflight 로그" })).toBeInTheDocument();
});

test("renders one log tab per phase that actually has a ref, including exec_preflight", async () => {
  // confirm 후 재검증(exec_preflight)이 실패한 잡을 진단할 때, 하드코딩된 "preflight"
  // 탭은 *초기* preflight의 성공 로그를 보여줘 사람을 오도한다.
  server.use(http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)));
  renderStages({ phase_refs: { preflight: "pod/a", exec_preflight: "pod/b" } });
  expect(await screen.findByRole("button", { name: "preflight 로그" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "exec_preflight 로그" })).toBeInTheDocument();
});

test("renders no log tab when the job has no phase_refs", async () => {
  server.use(http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)));
  renderStages({ phase_refs: {} });
  await screen.findByRole("button", { name: "execution/stdout.log" });
  expect(screen.queryByRole("button", { name: /로그$/ })).not.toBeInTheDocument();
});

test("submit-failed job (no phase_refs) gets a log tab from reason_code and shows the archived detail", async () => {
  // 2026-09-15 프로덕션 사고: preflight 제출이 apiserver 422 로 실패하면 파드가 없어
  // phase_refs 가 비고, 포탈은 코드만 보여줬다. stepper 가 박제한 원문을 여기서 연다.
  // 재설계(2026-10-08): 종단 실패 잡은 실패 단계 로그를 **자동으로** 연다(스펙 §4.9) -- 이어지는 칩 클릭은 이미
  // 눌린 칩이라 아무 일도 없고, 조회는 여전히 그 phase 한 번뿐이다.
  const asked: string[] = [];
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json({ entries: [], truncated: false })),
    http.get("/api/user/jobs/j1/logs", ({ request }) => {
      asked.push(new URL(request.url).searchParams.get("phase") ?? "");
      return HttpResponse.json({
        phase: "preflight", ref: null, source: "archived",
        entries: [{ pod: "submit:preflight",
                    log: 'submit_failed: 422: spec.volumes[0].name: Invalid value: "mgmt_storage"',
                    truncated: false }],
      });
    }),
  );
  renderStages({ phase_refs: {}, reason_code: "preflight_submit_failed:submit_failed", state: "Rejected" });
  await userEvent.click(await screen.findByRole("button", { name: "preflight 로그" }));
  expect(await screen.findByText(/Invalid value: "mgmt_storage"/)).toBeInTheDocument();
  expect(screen.getByText("submit:preflight")).toBeInTheDocument();
  expect(asked).toEqual(["preflight"]);
});

test("reason_code without a submit_failed prefix adds no log tab", async () => {
  // 실패 지점은 preflight(사유 접두)지만 로그를 얻을 길(ref·제출 실패 박제)이 없다 -- 칩도 자동 열림도 없다.
  server.use(http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)));
  renderStages({ phase_refs: {}, reason_code: "preflight_failed:dst_not_writable", state: "Rejected" });
  await screen.findByRole("button", { name: "execution/stdout.log" });
  expect(screen.queryByRole("button", { name: /로그$/ })).not.toBeInTheDocument();
});

test("requests the log of the selected phase, not a hardcoded preflight", async () => {
  const asked: string[] = [];
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)),
    http.get("/api/user/jobs/j1/logs", ({ request }) => {
      const phase = new URL(request.url).searchParams.get("phase") ?? "";
      asked.push(phase);
      return HttpResponse.json({
        phase, ref: "pod/b", entries: [{ pod: "b", log: `log of ${phase}` }],
      });
    }),
  );
  renderStages({ phase_refs: { preflight: "pod/a", exec_preflight: "pod/b" } });
  await userEvent.click(await screen.findByRole("button", { name: "exec_preflight 로그" }));
  expect(await screen.findByText("log of exec_preflight")).toBeInTheDocument();
  expect(asked).toEqual(["exec_preflight"]);
});

test("does not request the artifact body before a tab is clicked", async () => {
  let bodyCalls = 0;
  let logCalls = 0;
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)),
    http.get("/api/user/jobs/j1/artifacts/execution/stdout.log", () => {
      bodyCalls += 1;
      return HttpResponse.json({ phase: "execution", name: "stdout.log", size: 120, truncated: false, content: "hello" });
    }),
    http.get("/api/user/jobs/j1/logs", () => {
      logCalls += 1;
      return HttpResponse.json({ phase: "preflight", ref: "pod/p1", entries: [] });
    }),
  );
  renderStages();
  await screen.findByRole("button", { name: "execution/stdout.log" });
  // Give any accidental in-flight request a tick to land.
  await new Promise((r) => setTimeout(r, 10));
  expect(bodyCalls).toBe(0);
  expect(logCalls).toBe(0);   // 성공 잡은 아무 로그도 자동으로 열지 않는다
});

test("clicking an artifact tab shows its content and a truncated badge when truncated", async () => {
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)),
    http.get("/api/user/jobs/j1/artifacts/execution/stdout.log", () =>
      HttpResponse.json({ phase: "execution", name: "stdout.log", size: 999999, truncated: true, content: "tail content" })),
  );
  renderStages();
  await userEvent.click(await screen.findByRole("button", { name: "execution/stdout.log" }));
  expect(await screen.findByText("tail content")).toBeInTheDocument();
  expect(screen.getByText("뒷부분만 표시")).toBeInTheDocument();
});

test("log tab shows the localized message on a 409 log_not_available error", async () => {
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)),
    http.get("/api/user/jobs/j1/logs", () => HttpResponse.json({ detail: "log_not_available" }, { status: 409 })),
  );
  renderStages();
  await userEvent.click(await screen.findByRole("button", { name: "preflight 로그" }));
  expect(
    await screen.findByText("이 단계는 파드 로그를 제공하지 않습니다 — 아티팩트를 확인하세요"),
  ).toBeInTheDocument();
});

test("log tab shows the pod-gone message when an entry's log is null", async () => {
  // 이 payload 에는 source 가 없다 -- 구형 응답 형태에도 죽지 않는다는 회귀 가드를 겸한다.
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)),
    http.get("/api/user/jobs/j1/logs", () =>
      HttpResponse.json({ phase: "preflight", ref: "pod/p1", entries: [{ pod: "p1", log: null }] })),
  );
  renderStages();
  await userEvent.click(await screen.findByRole("button", { name: "preflight 로그" }));
  expect(await screen.findByText("파드 로그를 더 이상 조회할 수 없습니다")).toBeInTheDocument();
  expect(screen.getByText("p1")).toBeInTheDocument();
});

test("log tab pairs a null log with its waiting_reason when present", async () => {
  // null(로그 없음)과 "왜 없는지"(waiting_reason)는 별 채널이다 -- 병기는 하되
  // null 을 합성 문자열로 뭉개지 않는다(백엔드 계약). ImagePullBackOff 파드는
  // 로그가 생기기 전에 죽는 대표 사례다.
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)),
    http.get("/api/user/jobs/j1/logs", () =>
      HttpResponse.json({ phase: "preflight", ref: "pod/p1", source: "live",
        entries: [{ pod: "p1", log: null, waiting_reason: "ImagePullBackOff" }] })),
  );
  renderStages();
  await userEvent.click(await screen.findByRole("button", { name: "preflight 로그" }));
  expect(await screen.findByText("파드 로그 없음 — ImagePullBackOff")).toBeInTheDocument();
});

test("archived response shows the caption and per-entry truncation badge", async () => {
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)),
    http.get("/api/user/jobs/j1/logs", () =>
      HttpResponse.json({ phase: "execution", ref: "vcjob/j1", source: "archived",
        entries: [{ pod: "j-launcher-0", log: "Traceback tail", truncated: true }] })),
  );
  renderStages({ phase_refs: { execution: "vcjob/j1" } });
  await userEvent.click(await screen.findByRole("button", { name: "execution 로그" }));
  expect(await screen.findByText("Traceback tail")).toBeInTheDocument();
  expect(screen.getByText("잡 종료 시점에 저장된 사본 — 파드당 마지막 16KB")).toBeInTheDocument();
  expect(screen.getByText("뒷부분만 표시")).toBeInTheDocument();
});

test("a live response shows neither the archived caption nor a truncation badge", async () => {
  // 캡션·배지가 조건과 무관하게 늘 붙어 있으면 위 테스트는 "검사하는 척"이 된다.
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)),
    http.get("/api/user/jobs/j1/logs", () =>
      HttpResponse.json({ phase: "preflight", ref: "pod/p1", source: "live",
        entries: [{ pod: "p1", log: "live tail", waiting_reason: null }] })),
  );
  renderStages();
  await userEvent.click(await screen.findByRole("button", { name: "preflight 로그" }));
  expect(await screen.findByText("live tail")).toBeInTheDocument();
  expect(screen.queryByText("잡 종료 시점에 저장된 사본 — 파드당 마지막 16KB")).not.toBeInTheDocument();
  expect(screen.queryByText("뒷부분만 표시")).not.toBeInTheDocument();
});

test("artifact tab shows a download link pointing at the /download stream route", async () => {
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)),
    http.get("/api/user/jobs/j1/artifacts/execution/stdout.log", () =>
      HttpResponse.json({ phase: "execution", name: "stdout.log", size: 120, truncated: false, content: "hello" })),
  );
  renderStages();
  await userEvent.click(await screen.findByRole("button", { name: "execution/stdout.log" }));
  const link = await screen.findByRole("link", { name: "다운로드 (120 B)" });
  // 뷰 라우트(256KB 꼬리 JSON)가 아니라 /download 접미의 스트림 라우트여야 한다 --
  // 정확값 단언이라야 "뷰 JSON 을 다운로드로 내미는" 회귀를 잡는다.
  expect(link).toHaveAttribute("href", "/api/user/jobs/j1/artifacts/execution/stdout.log/download");
  expect(link).toHaveAttribute("download");
  // truncated 가 아니면 전체 다운로드 안내는 붙지 않는다(늘 붙으면 검사하는 척).
  expect(screen.queryByText("전체는 다운로드로 받으세요")).not.toBeInTheDocument();
});

test("download label humanizes the list size — 0 bytes is '0 B', not a dash", async () => {
  // 크기는 목록 entries 의 값이다(본문 응답의 size 가 아니다). 0 바이트는 정상값
  // ("0 B") -- truthy 검사로 "—"(null 전용)에 뭉개지면 빈 파일이 결측으로 둔갑한다.
  const artifacts = {
    entries: [
      { phase: "execution", name: "big.bin", size: 2048, modified_at: 1754400000 },
      { phase: "execution", name: "empty.txt", size: 0, modified_at: 1754400001 },
    ],
    truncated: false,
  };
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(artifacts)),
    http.get("/api/user/jobs/j1/artifacts/execution/:name", ({ params }) =>
      HttpResponse.json({ phase: "execution", name: params.name, size: 0, truncated: false, content: "" })),
  );
  renderStages();
  await userEvent.click(await screen.findByRole("button", { name: "execution/big.bin" }));
  expect(await screen.findByRole("link", { name: "다운로드 (2.0 KiB)" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "execution/empty.txt" }));
  expect(await screen.findByRole("link", { name: "다운로드 (0 B)" })).toBeInTheDocument();
});

test("a truncated artifact pairs the badge with the full-download notice", async () => {
  // 256KB 꼬리와 전체 파일의 관계를 화면이 말해야 한다 -- 배지만으로는 "전체를
  // 얻을 수단이 있다"는 사실이 전달되지 않는다.
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)),
    http.get("/api/user/jobs/j1/artifacts/execution/stdout.log", () =>
      HttpResponse.json({ phase: "execution", name: "stdout.log", size: 999999, truncated: true, content: "tail" })),
  );
  renderStages();
  await userEvent.click(await screen.findByRole("button", { name: "execution/stdout.log" }));
  expect(await screen.findByText("전체는 다운로드로 받으세요")).toBeInTheDocument();
  expect(screen.getByText("뒷부분만 표시")).toBeInTheDocument();
});

test("an empty-string log renders as content, not as the pod-gone message", async () => {
  // 빈 로그는 정상값이다(launcher 는 대개 비어 있다) -- truthy 검사로 null 문구를
  // 내면 "로그가 없다"와 "빈 로그"가 뭉개진다.
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)),
    http.get("/api/user/jobs/j1/logs", () =>
      HttpResponse.json({ phase: "preflight", ref: "pod/p1", source: "live",
        entries: [{ pod: "p1", log: "", waiting_reason: null }] })),
  );
  renderStages();
  await userEvent.click(await screen.findByRole("button", { name: "preflight 로그" }));
  await screen.findByText("p1");
  // 빈 문자열은 text 쿼리로 못 잡으니 <pre> 자체의 존재로 "내용으로 그렸다"를 고정한다. 그 뷰어 안의 pre 로 좁힌다
  // (다른 pre 때문에 거짓 초록이 나지 않게).
  const viewer = screen.getByRole("region", { name: "preflight 로그 내용" });
  expect(viewer.querySelector("pre")).not.toBeNull();
  expect(screen.queryByText("파드 로그를 더 이상 조회할 수 없습니다")).not.toBeInTheDocument();
});

// ---- 재설계 신규: 구획 배치·구획별 선택·목록 실패·닫기·자동 열림·라이브·주석 ----------------------------------

const ALL_REFS = { preflight: "pod/a", preview: "pod/b", exec_preflight: "pod/c", execution: "vcjob/j1" };
const STAGED = {
  entries: [
    { phase: "preview", name: "stdout.log", size: 10, modified_at: 1 },
    { phase: "execution", name: "stdout.log", size: 20, modified_at: 2 },
    { phase: "foo", name: "x.txt", size: 3, modified_at: 3 },
  ],
  truncated: false,
};

test("출력은 자기 단계 구획에만 놓인다 -- ① preflight·preview, ② exec_preflight·execution, 밖의 phase 는 기타 출력", async () => {
  server.use(http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(STAGED)));
  renderStages({ phase_refs: ALL_REFS });
  const pre = screen.getByRole("region", { name: "사전 점검·미리보기" });
  const exec = screen.getByRole("region", { name: "실행" });
  expect(await within(pre).findByRole("button", { name: "preview/stdout.log" })).toBeInTheDocument();
  expect(within(pre).getByRole("button", { name: "preflight 로그" })).toBeInTheDocument();
  expect(within(pre).getByRole("button", { name: "preview 로그" })).toBeInTheDocument();
  expect(within(pre).queryByRole("button", { name: /^exec/ })).toBeNull();
  expect(within(exec).getByRole("button", { name: "execution/stdout.log" })).toBeInTheDocument();
  expect(within(exec).getByRole("button", { name: "exec_preflight 로그" })).toBeInTheDocument();
  expect(within(exec).getByRole("button", { name: "execution 로그" })).toBeInTheDocument();
  expect(within(exec).queryByRole("button", { name: /^pre/ })).toBeNull();
  // 조용히 버리지 않는다
  expect(screen.getByText("기타 출력 · foo")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "foo/x.txt" })).toBeInTheDocument();
});

test("구획마다 선택이 따로다 -- 미리보기 stdout 과 실행 stdout 을 동시에 펼친다", async () => {
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(STAGED)),
    http.get("/api/user/jobs/j1/artifacts/:phase/:name", ({ params }) =>
      HttpResponse.json({ phase: params.phase, name: params.name, size: 1, truncated: false, content: `body of ${params.phase}` })),
  );
  renderStages({ phase_refs: ALL_REFS });
  await userEvent.click(await screen.findByRole("button", { name: "preview/stdout.log" }));
  await userEvent.click(screen.getByRole("button", { name: "execution/stdout.log" }));
  expect(await screen.findByText("body of preview")).toBeInTheDocument();
  expect(await screen.findByText("body of execution")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "preview/stdout.log 내용" })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "execution/stdout.log 내용" })).toBeInTheDocument();
  // 눌린 칩은 aria-pressed
  expect(screen.getByRole("button", { name: "preview/stdout.log" })).toHaveAttribute("aria-pressed", "true");
});

test("아티팩트 목록이 실패해도(422) 로그 칩은 쓸 수 있고, 실패 문구와 「다시 시도」가 있다", async () => {
  let listCalls = 0;
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => {
      listCalls += 1;
      return HttpResponse.json({ detail: "artifact_base_unavailable" }, { status: 422 });
    }),
    http.get("/api/user/jobs/j1/logs", () =>
      HttpResponse.json({ phase: "preflight", ref: "pod/p1", source: "live", entries: [{ pod: "p1", log: "pf ok" }] })),
  );
  renderStages();
  expect(await screen.findByText(/^출력 파일 목록을 불러오지 못했습니다 — /)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "preflight 로그" }));
  expect(await screen.findByText("pf ok")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));
  await waitFor(() => expect(listCalls).toBe(2));
});

// 2026-11-23: 목록이 503(작업 기록 저장소 접근 불가)이면 종료 단계마다 「출력 없음」을 말하지 않는다 -- 아래 「불러오지
// 못했습니다」와 모순(모름 ≠ 없음). 목록을 다시 받으면(빈 목록) 그때는 「출력 없음」이 정직하다.
test("목록 503 이면 단계별 「출력 없음」을 숨기고 사유 문구를 보인다 -- 다시 받은 빈 목록에선 「출력 없음」", async () => {
  let calls = 0;
  server.use(http.get("/api/user/jobs/j1/artifacts", () => {
    calls += 1;
    return calls === 1
      ? HttpResponse.json({ detail: "artifact_base_unavailable" }, { status: 503 })
      : HttpResponse.json({ entries: [], truncated: false });
  }));
  renderStages({ phase_refs: {} });
  expect(await screen.findByText(
    /^출력 파일 목록을 불러오지 못했습니다 — 작업 기록이 저장된 공유 스토리지에 지금 접근할 수 없습니다/)).toBeInTheDocument();
  expect(screen.queryByText("출력 없음")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));
  await waitFor(() => expect(screen.queryByText(/^출력 파일 목록을 불러오지 못했습니다/)).toBeNull());
  expect((await screen.findAllByText("출력 없음")).length).toBeGreaterThan(0);
});

test("「출력 닫기」 = 뷰어 사라짐 + 포커스는 칩으로, 그 뒤 실패로 바뀌어도 자동으로 다시 열지 않는다(touched)", async () => {
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json({ entries: [], truncated: false })),
    http.get("/api/user/jobs/j1/logs", () =>
      HttpResponse.json({ phase: "preflight", ref: "pod/p1", source: "archived", entries: [{ pod: "p1", log: "x" }] })),
  );
  const { rerenderJob } = renderStages();
  const chip = screen.getByRole("button", { name: "preflight 로그" });
  await userEvent.click(chip);
  expect(await screen.findByRole("region", { name: "preflight 로그 내용" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "출력 닫기" }));
  expect(screen.queryByRole("region", { name: "preflight 로그 내용" })).toBeNull();
  expect(chip).toHaveFocus();
  rerenderJob({ state: "Failed", reason_code: "preflight_failed:x",
    transitions: [{ from_state: "Pending", to_state: "Preflight", at: "2026-10-08T00:00:00Z" },
                  { from_state: "Preflight", to_state: "Failed", at: "2026-10-08T00:00:05Z" }] });
  await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  expect(screen.queryByRole("region", { name: "preflight 로그 내용" })).toBeNull();
});

test("손대지 않은 채 폴링으로 실패가 되면 실패 단계 로그가 자동으로 열린다", async () => {
  const asked: string[] = [];
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json({ entries: [], truncated: false })),
    http.get("/api/user/jobs/j1/logs", ({ request }) => {
      asked.push(new URL(request.url).searchParams.get("phase") ?? "");
      return HttpResponse.json({ phase: "preflight", ref: "pod/p1", source: "archived",
        entries: [{ pod: "p1", log: "DMS_PREFLIGHT_REASON=destination_not_writable" }] });
    }),
  );
  const { rerenderJob } = renderStages({ state: "Preflight",
    transitions: [{ from_state: "Pending", to_state: "Preflight", at: "2026-10-08T00:00:00Z" }] });
  await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  expect(asked).toEqual([]);   // 진행 중 로그는 자동으로 열지 않는다(라이브 조회 부하)
  rerenderJob({ state: "Rejected", reason_code: "destination_not_writable",
    transitions: [{ from_state: "Pending", to_state: "Preflight", at: "2026-10-08T00:00:00Z" },
                  { from_state: "Preflight", to_state: "Rejected", at: "2026-10-08T00:00:05Z" }] });
  expect(await screen.findByText("DMS_PREFLIGHT_REASON=destination_not_writable")).toBeInTheDocument();
  expect(asked).toEqual(["preflight"]);
  expect(screen.getByRole("button", { name: "preflight 로그" })).toHaveAttribute("aria-pressed", "true");
});

test("3차 자동 열림은 손대지 않은 동안 모델을 따른다: 판정이 고쳐져 자동 열림이 거둬지면 자동으로 연 뷰어도 닫힌다", async () => {
  // round3/stage/skew.test.tsx: 잡 판정이 바뀌어 자동 열림이 거둬진 뒤에도 자동으로 연 뷰어가 「완료」 행 아래 남았다. 판정을
  // 바꾸는 입력은 잡 행이다(옛 계기였던 base 관문 이벤트는 2026-10-26 LDAP 정리로 사라졌다) -- 같은 잡이 마커 사유(사전 점검
  // 실패, 로그 자동 열림)에서 제출 직전 관문 사유(미리보기 시작 전 거부, 자동 열림 없음)로 바뀌는 렌더로 고정한다.
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json({ entries: [], truncated: false })),
    http.get("/api/user/jobs/j1/logs", () => HttpResponse.json({ phase: "preflight", ref: "pod/p1", source: "archived",
      entries: [{ pod: "p1", log: "preflight ok" }] })),
  );
  const marker: DataJob = { ...BASE, state: "Rejected", reason_code: "artifact_base_not_traversable", phase_refs: { preflight: "pod/p1" },
    transitions: [{ from_state: "Pending", to_state: "Preflight", at: "2026-10-08T00:00:00Z" },
                  { from_state: "Preflight", to_state: "Rejected", at: "2026-10-08T00:00:30Z" }] };
  const gate: DataJob = { ...marker, reason_code: "node_cpu_insufficient_at_step" };
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = (job: DataJob) => (
    <QueryClientProvider client={qc}><JobStages job={job} events={[]} /></QueryClientProvider>
  );
  const view = render(ui(marker));
  expect(await screen.findByText("preflight ok")).toBeInTheDocument();          // 마커 판정 → 자동 열림
  const chip = screen.getByRole("button", { name: "preflight 로그" });
  expect(chip).toHaveAttribute("aria-pressed", "true");
  view.rerender(ui(gate));                                                     // 판정 수정(관문) → 자동 열림 거둠
  await waitFor(() => expect(screen.queryByText("preflight ok")).toBeNull());
  expect(screen.getByRole("button", { name: "preflight 로그" })).toHaveAttribute("aria-pressed", "false");
  expect(screen.queryByRole("region", { name: "preflight 로그 내용" })).toBeNull();
  view.unmount();

  // 사용자가 직접 연 것은 판정이 바뀌어도 닫지 않는다(touched)
  const view2 = render(ui(marker));
  expect(await screen.findByText("preflight ok")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "출력 닫기" }));
  await userEvent.click(screen.getByRole("button", { name: "preflight 로그" }));
  expect(await screen.findByText("preflight ok")).toBeInTheDocument();
  view2.rerender(ui(gate));
  await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  expect(screen.getByText("preflight ok")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "preflight 로그" })).toHaveAttribute("aria-pressed", "true");
});

test("3차 목록 재조회: 첫 목록 조회 중 실패로 바뀌어 자동 열림이 같은 커밋에 끼어도 「불러오는 중」 골격이 한 번도 끊기지 않는다", async () => {
  // round3/query/n0h.test.tsx: 취소의 동기 되돌림(pending/idle) 뒤 `.then(refetch)` 를 기다리는 사이 커밋된 렌더가 골격을
  // 떨어뜨렸다(같은 flush 의 자동 열림 setState, act 안). 취소 바로 뒤 같은 틱에서 다시 읽으면 틈이 없다.
  let lists = 0;
  const holds: (() => void)[] = [];
  server.use(
    http.get("/api/user/jobs/j1/artifacts", async () => {
      lists += 1;
      await new Promise<void>((r) => { holds.push(r); });
      return HttpResponse.json({ entries: [], truncated: false });
    }),
    http.get("/api/user/jobs/j1/logs", () => HttpResponse.json({ phase: "preflight", ref: "pod/p1", source: "archived",
      entries: [{ pod: "p1", log: "DMS_PREFLIGHT_REASON=destination_not_writable" }] })),
  );
  const PRE = [{ from_state: "Pending", to_state: "Preflight", at: "2026-10-08T00:00:00Z" }];
  const { rerenderJob, container } = renderStages({ state: "Preflight", transitions: PRE });
  await waitFor(() => expect(lists).toBe(1));
  const seen: string[] = [];
  const mo = new MutationObserver(() => {
    const t = container.textContent ?? "";
    seen.push(`${t.includes("출력 목록을 불러오는 중…") ? "SKEL" : "NOSKEL"}${t.includes("출력 없음") ? "+출력없음" : ""}`);
  });
  mo.observe(container, { subtree: true, childList: true, characterData: true });
  rerenderJob({ state: "Rejected", reason_code: "destination_not_writable",
    transitions: [...PRE, { from_state: "Preflight", to_state: "Rejected", at: "2026-10-08T00:00:05Z" }] });
  await waitFor(() => expect(lists).toBe(2));
  expect(await screen.findByText("DMS_PREFLIGHT_REASON=destination_not_writable")).toBeInTheDocument();
  await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
  const before = seen.slice();
  await act(async () => { holds[1]?.(); holds[0]?.(); await new Promise((r) => setTimeout(r, 50)); });
  mo.disconnect();
  expect(before.filter((s) => s.startsWith("NOSKEL"))).toEqual([]);
  expect(lists).toBe(2);
});

test("라이브: 진행 중 단계의 로그를 열면 3초마다 다시 읽고, 끝나면 마지막으로 한 번 더 읽은 뒤 멈춘다", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let calls = 0;
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json({ entries: [], truncated: false })),
    http.get("/api/user/jobs/j1/logs", () => {
      calls += 1;
      return HttpResponse.json({ phase: "preflight", ref: "pod/p1", source: "live", entries: [{ pod: "p1", log: `tick ${calls}` }] });
    }),
  );
  const { rerenderJob } = renderStages({ state: "Preflight",
    transitions: [{ from_state: "Pending", to_state: "Preflight", at: "2026-10-08T00:00:00Z" }] });
  fireEvent.click(screen.getByRole("button", { name: "preflight 로그" }));
  await waitFor(() => expect(calls).toBe(1));
  expect(await screen.findByText("실시간")).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
  await waitFor(() => expect(calls).toBe(2));
  rerenderJob({ state: "Succeeded",
    transitions: [{ from_state: "Pending", to_state: "Preflight", at: "2026-10-08T00:00:00Z" },
                  { from_state: "Preflight", to_state: "Succeeded", at: "2026-10-08T00:00:09Z" }] });
  // 단계가 끝나는 순간 한 번(마지막 꼬리·박제 사본), 그 뒤로는 멈춘다(리뷰 V0 -- 예전 단언 2 는 그 결함을 고정했다).
  await waitFor(() => expect(calls).toBe(3));
  await act(async () => { await vi.advanceTimersByTimeAsync(7000); });
  expect(calls).toBe(3);
  expect(screen.queryByText("실시간")).toBeNull();
});

test("V0 진행 중 로그를 연 채 단계가 끝나면(Preflight→Rejected) 마지막 마커 줄과 박제 캡션까지 다시 읽는다", async () => {
  // 리뷰 exp2 이식: 쿼리 키가 그대로라 예전엔 끝나기 직전 스냅숏에 멈춰 DMS_PREFLIGHT_REASON 줄·「저장된 사본」이 안 왔다.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let n = 0;
  let finished = false;
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json({ entries: [], truncated: false })),
    http.get("/api/user/jobs/j1/logs", () => {
      n += 1;
      return HttpResponse.json(finished
        ? { phase: "preflight", ref: "pod/p1", source: "archived",
            entries: [{ pod: "p1", log: "line1\nDMS_PREFLIGHT_REASON=destination_not_writable", truncated: false }] }
        : { phase: "preflight", ref: "pod/p1", source: "live", entries: [{ pod: "p1", log: "line1", truncated: false }] });
    }),
  );
  const PREFLIGHT_TR = [{ from_state: null, to_state: "Pending", at: "2026-10-08T03:15:00Z" },
    { from_state: "Pending", to_state: "Preflight", at: "2026-10-08T03:15:02Z" }];
  const { rerenderJob } = renderStages({ state: "Preflight", transitions: PREFLIGHT_TR as never });
  fireEvent.click(screen.getByRole("button", { name: "preflight 로그" }));
  expect(await screen.findByText("line1")).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
  await waitFor(() => expect(n).toBeGreaterThanOrEqual(2));
  finished = true;
  const before = n;
  rerenderJob({ state: "Rejected", reason_code: "destination_not_writable",
    transitions: [...PREFLIGHT_TR, { from_state: "Preflight", to_state: "Rejected", at: "2026-10-08T03:15:05Z" }] as never });
  expect(await screen.findByText("DMS_PREFLIGHT_REASON=destination_not_writable")).toBeInTheDocument();
  expect(screen.getByText("잡 종료 시점에 저장된 사본 — 파드당 마지막 16KB")).toBeInTheDocument();
  expect(n).toBe(before + 1);
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(n).toBe(before + 1);                          // 그 뒤로는 폴링이 멈춘다
});

test("V3 라이브 로그 재조회 한 번 실패해도 읽던 로그는 남고 작은 안내가 붙으며, 다음 폴링이 성공하면 안내가 사라진다", async () => {
  // 리뷰 exp3 이식: 예전엔 502 한 번에 본문이 오류 상자로 바뀌어(스크롤·「모두 표시」 초기화) 'important line' 이 사라졌다.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let n = 0;
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json({ entries: [], truncated: false })),
    http.get("/api/user/jobs/j1/logs", () => {
      n += 1;
      if (n === 2) return HttpResponse.json({ detail: "http_502" }, { status: 502 });
      return HttpResponse.json({ phase: "preflight", ref: "pod/p1", source: "live", entries: [{ pod: "p1", log: "important line" }] });
    }),
  );
  renderStages({ state: "Preflight", transitions: [{ from_state: "Pending", to_state: "Preflight", at: "2026-10-08T00:00:00Z" }] });
  fireEvent.click(screen.getByRole("button", { name: "preflight 로그" }));
  expect(await screen.findByText("important line")).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
  await waitFor(() => expect(n).toBe(2));
  expect(await screen.findByText(/^내용 갱신에 실패했습니다 — .* 기준입니다\.$/)).toBeInTheDocument();
  expect(screen.getByText("important line")).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
  await waitFor(() => expect(n).toBe(3));
  await waitFor(() => expect(screen.queryByText(/^내용 갱신에 실패했습니다/)).toBeNull());
  expect(screen.getByText("important line")).toBeInTheDocument();
});

test("V4 상태 전이 뒤 목록 재조회가 실패해도 보이던 파일 칩·열어 둔 뷰어는 남고 실패 줄이 붙는다, 「다시 시도」로 회복", async () => {
  // 리뷰 exp4 이식: refreshKey 가 쿼리 키에 있던 시절엔 새 키의 첫 조회 실패로 data 가 없어져 칩·뷰어가 통째로 사라졌다.
  let n = 0;
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => {
      n += 1;
      if (n === 2) return HttpResponse.json({ detail: "http_502" }, { status: 502 });
      return HttpResponse.json({ entries: [{ phase: "preview", name: "stdout.log", size: 5, modified_at: 1 }], truncated: false });
    }),
    http.get("/api/user/jobs/j1/artifacts/preview/stdout.log", () =>
      HttpResponse.json({ phase: "preview", name: "stdout.log", size: 5, truncated: false, content: "dry-run plan" })),
  );
  const CP = { state: "ConfirmPending", preview_fingerprint: "fp", preview_expires_at: "2099-01-01T00:00:00Z",
    phase_refs: { preflight: "pod/a", preview: "pod/b" } };
  const { rerenderJob } = renderStages(CP);
  await userEvent.click(await screen.findByRole("button", { name: "preview/stdout.log" }));
  expect(await screen.findByText("dry-run plan")).toBeInTheDocument();
  rerenderJob({ ...CP, state: "Executing", phase_refs: { preflight: "pod/a", preview: "pod/b", exec_preflight: "pod/c" } });
  await waitFor(() => expect(n).toBe(2));
  expect(await screen.findByText(/^출력 파일 목록을 불러오지 못했습니다 — /)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "preview/stdout.log" })).toBeInTheDocument();
  expect(screen.getByText("dry-run plan")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));
  await waitFor(() => expect(n).toBe(3));
  await waitFor(() => expect(screen.queryByText(/^출력 파일 목록을 불러오지 못했습니다/)).toBeNull());
  expect(screen.getByRole("button", { name: "preview/stdout.log" })).toBeInTheDocument();
});

test("V5 흐린 상태(대기·실행 안 됨·모름) 배지 글자는 ink/70(AA), 아이콘은 ink/60 그대로", () => {
  for (const status of ["waiting", "skipped", "unknown"] as const) {
    const { container, unmount } = render(<StageBadge status={status} />);
    const badge = container.firstElementChild as HTMLElement;
    expect(badge.className).toContain("text-ink/70");
    expect(badge.className).not.toContain("text-ink/60");
    expect(badge.querySelector("svg")?.getAttribute("class")).toContain("text-ink/60");
    unmount();
  }
});

test("N7 출력 칩의 파일 크기 글자는 ink/70(AA -- 흰·hover panel·눌림 infobg 모두 4.5:1 이상), ink/60 아님", async () => {
  server.use(http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)));
  renderStages();
  const chip = await screen.findByRole("button", { name: "execution/stdout.log" });
  const size = within(chip).getByText("120 B");
  expect(size.className).toContain("text-ink/70");
  expect(size.className).not.toContain("text-ink/60");
});

test("V6 관문 거부(앞 파드 통과 뒤 미리보기 제출 전): 사전 점검은 완료, 미리보기 행이 「시작 전 거부됨」 + 사유, 로그 자동 조회 없음", async () => {
  const asked: string[] = [];
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json({ entries: [], truncated: false })),
    http.get("/api/user/jobs/j1/logs", ({ request }) => {
      asked.push(new URL(request.url).searchParams.get("phase") ?? "");
      return HttpResponse.json({ phase: "preflight", ref: "pod/p1", source: "live", entries: [] });
    }),
  );
  renderStages({ state: "Rejected", reason_code: "identity_changed_at_step",
    transitions: [{ from_state: null, to_state: "Pending", at: "2026-10-08T03:15:00Z" },
      { from_state: "Pending", to_state: "Preflight", at: "2026-10-08T03:15:02Z" },
      { from_state: "Preflight", to_state: "Rejected", at: "2026-10-08T03:15:30Z" }] as never });
  const pre = screen.getByRole("region", { name: "사전 점검·미리보기" });
  const rows = within(within(pre).getByRole("list", { name: "사전 점검·미리보기 단계" })).getAllByRole("listitem");
  expect(rows[0]).toHaveTextContent("사전 점검: 완료");
  expect(rows[0]).not.toHaveTextContent("소요");
  expect(rows[1]).toHaveTextContent("미리보기: 거부됨");
  expect(rows[1]).toHaveTextContent(/시작 전 거부됨 · \d{2}:\d{2}:\d{2}/);
  expect(rows[1]).not.toHaveTextContent("소요");
  await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  expect(asked).toEqual([]);
  expect(screen.getByRole("button", { name: "preflight 로그" })).toHaveAttribute("aria-pressed", "false");
});

test("V8 스케줄 대기 중인 실행: 실행 행은 「진행 중」이 아니라 「제출 · 스케줄 대기 중」, 라이브 점 없음", () => {
  server.use(http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json({ entries: [], truncated: false })));
  renderStages({ state: "Executing", phase_refs: ALL_REFS, exec_submitted_at: "2026-10-08T03:15:27Z", sched_wait_seconds: null,
    transitions: [{ from_state: "ConfirmPending", to_state: "Executing", at: "2026-10-08T03:15:18Z" },
      { from_state: "Executing", to_state: "Executing", at: "2026-10-08T03:15:27Z" }] as never });
  const exec = screen.getByRole("region", { name: "실행" });
  const rows = within(within(exec).getByRole("list", { name: "실행 단계" })).getAllByRole("listitem");
  expect(rows[1]).toHaveTextContent("실행: 대기");
  expect(within(rows[1]).getByText(/제출 · 스케줄 대기 중/)).toBeInTheDocument();
  expect(rows[1]).not.toHaveTextContent("시작 ·");
});

test("B48 실행 중 「시작」은 제출 + 스케줄 대기, 끝난 행의 소요는 대기 「포함」을 글로 밝힌다(2026-11-11)", () => {
  server.use(http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json({ entries: [], truncated: false })));
  const tr = [{ from_state: "ConfirmPending", to_state: "Executing", at: "2026-10-08T03:15:18Z" },
    { from_state: "Executing", to_state: "Executing", at: "2026-10-08T03:15:27Z" }];
  // 제출 03:15:27Z(KST 12:15:27) + 대기 5초 → 시작 12:15:32
  const view = renderStages({ state: "Executing", phase_refs: ALL_REFS, exec_submitted_at: "2026-10-08T03:15:27Z",
    sched_wait_seconds: 5, transitions: tr as never });
  let rows = within(within(screen.getByRole("region", { name: "실행" })).getByRole("list", { name: "실행 단계" })).getAllByRole("listitem");
  expect(rows[1]).toHaveTextContent("12:15:32 시작 · ");
  expect(rows[1]).not.toHaveTextContent("12:15:27 시작");
  expect(rows[1]).toHaveTextContent("스케줄 대기 5초");
  view.unmount();
  // 끝남: 제출 03:15:27 → 종단 03:15:38 = 소요 11초(대기 5초 포함) -- 「소요 11초 · 스케줄 대기 5초」로 나란히 두지 않는다
  renderStages({ state: "Succeeded", phase_refs: ALL_REFS, exec_submitted_at: "2026-10-08T03:15:27Z", sched_wait_seconds: 5,
    transitions: [...tr, { from_state: "Executing", to_state: "Succeeded", at: "2026-10-08T03:15:38Z" }] as never });
  rows = within(within(screen.getByRole("region", { name: "실행" })).getByRole("list", { name: "실행 단계" })).getAllByRole("listitem");
  expect(rows[1]).toHaveTextContent("소요 11초(스케줄 대기 5초 포함)");
  expect(rows[1]).not.toHaveTextContent("소요 11초 · 스케줄 대기");
});

test("주석: 이 잡의 phase 이벤트만 그 단계 행에(행마다 3건 + 「외 N건」), job_id 없는 이벤트는 잡이 1개일 때만", async () => {
  server.use(http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json({ entries: [], truncated: false })));
  const ev = (id: number, payload: unknown, message: string) => ({
    id, component: "stepper", severity: "info", event_type: "node_cpu_clamped", message, payload,
    at: "2026-10-08T00:00:0" + (id % 10) + "Z",
  });
  const events = [
    ev(1, { job_id: "j1", phase: "preview" }, "p1 msg"),
    ev(2, { job_id: "j1", phase: "preview" }, "p2 msg"),
    ev(3, { job_id: "j1", phase: "preview" }, "p3 msg"),
    ev(4, { job_id: "j1", phase: "preview" }, "p4 msg"),
    ev(5, { job_id: "j2", phase: "preflight" }, "other job msg"),
    ev(6, { phase: "exec_preflight" }, "no job id msg"),
    ev(7, { ref: "pod/p9" }, "no phase msg"),
  ];
  const view = renderStages({ phase_refs: ALL_REFS }, { events, jobCount: 1 });
  const pre = screen.getByRole("region", { name: "사전 점검·미리보기" });
  expect(within(pre).getByText(/^p1 msg · /)).toBeInTheDocument();
  expect(within(pre).getByText(/^p3 msg · /)).toBeInTheDocument();
  expect(within(pre).queryByText(/^p4 msg/)).toBeNull();
  expect(within(pre).getByText("외 1건 — 아래 「진단 이벤트」")).toBeInTheDocument();
  expect(screen.queryByText(/other job msg/)).toBeNull();
  expect(screen.queryByText(/no phase msg/)).toBeNull();
  const exec = screen.getByRole("region", { name: "실행" });
  expect(within(exec).getByText(/^no job id msg · /)).toBeInTheDocument();
  view.unmount();
  // 잡이 둘이면 어느 잡의 일인지 모르는 이벤트는 붙이지 않는다.
  renderStages({ phase_refs: ALL_REFS }, { events, jobCount: 2 });
  expect(screen.queryByText(/no job id msg/)).toBeNull();
  expect(screen.getByText(/^p1 msg · /)).toBeInTheDocument();
});

// ---- 결과 파일 자동 만료(2026-10-26) ----

test("expiryText: 보존일이 있으면 「보존 N일, 날짜(KST)」, 없으면 날짜만", () => {
  // 15:30Z + 9h = 다음 날 00:30 KST -- 날짜는 KST 기준이다.
  expect(expiryText("2030-12-03T15:30:00Z", 1500)).toBe("결과 파일 만료됨(보존 1500일, 2030-12-04)");
  expect(expiryText("2030-12-03T15:30:00Z", null)).toBe("결과 파일 만료됨(2030-12-04)");
  expect(expiryText("2030-12-03T15:30:00Z", 0)).toBe("결과 파일 만료됨(2030-12-04)");
  expect(expiryText("garbage", 30)).toBe("결과 파일 만료됨(보존 30일, garbage)");     // 못 읽으면 원문 -- 지어내지 않는다
});

test("만료된 잡: 출력 자리에 「결과 파일 만료됨(보존 N일, 날짜)」, 빈 단계는 「출력 없음」이 아니다, 로그 칩·단계는 그대로", async () => {
  server.use(http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json({
    entries: [], truncated: false, expired_at: "2030-12-03T15:30:00Z", retention_days: 1500 })));
  renderStages({ artifacts_expired_at: "2030-12-03T15:30:00Z" });
  expect(await screen.findByText("결과 파일 만료됨(보존 1500일, 2030-12-04)")).toBeInTheDocument();
  expect(screen.getByText(/작업 기록과 결과 수치는 그대로 남아 있습니다/)).toBeInTheDocument();
  expect(screen.queryByText("출력 없음")).toBeNull();
  expect(screen.getAllByText("결과 파일 만료됨").length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: "preflight 로그" })).toBeInTheDocument();
  expect(screen.queryByText(/출력 파일 목록을 불러오지 못했습니다/)).toBeNull();
});

test("잡 행에만 만료 표시가 있고 목록이 보존일을 싣지 않으면(구버전·꺼짐) 날짜만", async () => {
  server.use(http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json({ entries: [], truncated: false })));
  renderStages({ artifacts_expired_at: "2030-12-03T15:30:00Z" });
  expect(await screen.findByText("결과 파일 만료됨(2030-12-04)")).toBeInTheDocument();
});

test("만료 전 잡은 만료 문구가 없고 빈 단계는 여전히 「출력 없음」", async () => {
  server.use(http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json({ entries: [], truncated: false })));
  renderStages({ artifacts_expired_at: null });
  expect((await screen.findAllByText("출력 없음")).length).toBeGreaterThan(0);
  expect(screen.queryByText(/결과 파일 만료됨/)).toBeNull();
});

// d192: 결과의 「실패 항목」에서 그 단계 stdout.log 를 연다 -- 출력 목록에 그 파일이 있을 때만(없는 파일을 가리키는 버튼 금지).
const DRM_PARTIAL = { detail_version: 2, returncode: 1, files: 4, bytes: null, tool: "drm", removed: 4, remove_failed: 5,
  failure_samples: [{ op: "unlink", path: "/t/root/r1", errno: 13 }], reason: "rm_items_failed" };
const at = (s: number) => `2026-10-08T03:15:${String(s).padStart(2, "0")}Z`;
const RM_FAIL_TR = [
  { from_state: null, to_state: "Pending", at: at(0) }, { from_state: "Pending", to_state: "Preflight", at: at(1) },
  { from_state: "Preflight", to_state: "PreviewRunning", at: at(3) }, { from_state: "PreviewRunning", to_state: "ConfirmPending", at: at(9) },
  { from_state: "ConfirmPending", to_state: "Executing", at: at(10) }, { from_state: "Executing", to_state: "Executing", at: at(12) },
  { from_state: "Executing", to_state: "Failed", at: at(20) },
];

test("d192 실패 항목의 「stdout.log 열기」: 실행 stdout.log 가 목록에 있을 때만, 누르면 그 칩이 눌린 상태", async () => {
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json({ entries: [
      { phase: "execution", name: "stderr.log", size: 40, modified_at: 1 }], truncated: false })),
    http.get("/api/user/jobs/j1/logs", () => HttpResponse.json({ phase: "execution", ref: "vcjob/j1", source: "archived", entries: [] })),
  );
  const view = renderStages({ operation: "rm", tool: "drm", state: "Failed", reason_code: "rm_items_failed", phase_refs: ALL_REFS,
    transitions: RM_FAIL_TR, result_summary: DRM_PARTIAL });
  const exec = screen.getByRole("region", { name: "실행" });
  await within(exec).findByRole("button", { name: "execution/stderr.log" });
  const box = within(exec).getByRole("region", { name: "실패 항목" });
  expect(within(box).queryByRole("button", { name: "stdout.log 열기" })).toBeNull();
  view.unmount();

  server.use(http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json({ entries: [
    { phase: "execution", name: "stdout.log", size: 1839, modified_at: 1 }], truncated: false })));
  server.use(http.get("/api/user/jobs/j1/artifacts/:phase/:name", () => HttpResponse.json({ phase: "execution", name: "stdout.log",
    size: 1839, truncated: false, content: "ERROR: Remove failed for 5 of 9 items\n" })));
  renderStages({ operation: "rm", tool: "drm", state: "Failed", reason_code: "rm_items_failed", phase_refs: ALL_REFS,
    transitions: RM_FAIL_TR, result_summary: DRM_PARTIAL });
  const exec2 = screen.getByRole("region", { name: "실행" });
  const btn = await within(within(exec2).getByRole("region", { name: "실패 항목" })).findByRole("button", { name: "stdout.log 열기" });
  await userEvent.click(btn);
  expect(await within(exec2).findByRole("button", { name: "execution/stdout.log", pressed: true })).toBeInTheDocument();
});

test("k8s 결과 파일 만료 + 실패 항목: 「stdout.log 열기」 없이 만료 사실(목록이 아직 파일을 싣고 있어도 잡 행 만료가 이긴다)", async () => {
  // 폴링 사이: 목록 응답은 만료 전 것(stdout.log 있음)인데 잡 행에 만료 표시가 붙었다 -- 버튼을 누르면 410 artifact_expired 로
  // 끝난다. 그 길을 만들지 않는다.
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json({ entries: [
      { phase: "execution", name: "stdout.log", size: 1839, modified_at: 1 }], truncated: false })),
    http.get("/api/user/jobs/j1/logs", () => HttpResponse.json({ phase: "execution", ref: "vcjob/j1", source: "archived", entries: [] })),
  );
  renderStages({ operation: "rm", tool: "drm", state: "Failed", reason_code: "rm_items_failed", phase_refs: ALL_REFS,
    transitions: RM_FAIL_TR, result_summary: DRM_PARTIAL, artifacts_expired_at: "2030-12-03T15:30:00Z" });
  const exec = screen.getByRole("region", { name: "실행" });
  const box = within(exec).getByRole("region", { name: "실패 항목" });
  expect(within(box).getByText(/전체 목록과 원문이 있던 실행 출력\(stdout\.log\)은 보존 기간이 지나 만료됐습니다/))
    .toBeInTheDocument();
  await within(exec).findByRole("button", { name: "execution/stdout.log" });
  expect(within(box).queryByRole("button", { name: "stdout.log 열기" })).toBeNull();
});

// --- 2026-11-26 sync 진행률: 진행 중인 실행·미리보기 행(dsync·nsync)에만 진행 칸 -----------------------------------------------
// 응답은 계약 파일(lib/progressContract.json examples.responses) 그대로. 진행 요청은 단계(phase 질의)별로 센다.

const PRESP = contract.examples.responses;
const EXEC_TR = [
  { from_state: "Preflight", to_state: "PreviewRunning", at: "2026-11-26T08:20:00Z" },
  { from_state: "PreviewRunning", to_state: "ConfirmPending", at: "2026-11-26T08:25:00Z" },
  { from_state: "ConfirmPending", to_state: "Executing", at: "2026-11-26T08:28:40Z" },
  { from_state: "Executing", to_state: "Executing", at: "2026-11-26T08:28:56Z" },
];
const RUNNING_DSYNC: Partial<DataJob> = {
  operation: "sync", tool: "dsync", state: "Executing", phase_refs: ALL_REFS, transitions: EXEC_TR as never,
  exec_submitted_at: "2026-11-26T08:28:56Z", sched_wait_seconds: 2,
};
function progressCounter(reply: () => { status: number; body: unknown } = () => ({ status: 200, body: PRESP.available })) {
  const asked: string[] = [];
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json({ entries: [], truncated: false })),
    http.get("/api/user/jobs/:jid/progress", ({ request }) => {
      asked.push(new URL(request.url).searchParams.get("phase") ?? "");
      const r = reply();
      return HttpResponse.json(r.body as Record<string, unknown>, { status: r.status });
    }),
  );
  return asked;
}
// 진행 칸이 없다는 단언은 조회가 착지할 틈을 준 뒤에 한다(마운트 직후 0 은 아무것도 증명하지 않는다).
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 30)); });

test("진행률: Executing + dsync + 실행 ref + 스케줄 대기 관측 → 실행 행 머리 아래 진행 칸, 막대 값은 응답 %", async () => {
  const asked = progressCounter();
  renderStages(RUNNING_DSYNC);
  const exec = screen.getByRole("region", { name: "실행" });
  const g = await within(exec).findByRole("group", { name: "실행 진행" });
  const bar = await within(g).findByRole("progressbar");
  expect(bar).toHaveAttribute("aria-valuenow", String(PRESP.available.progress.overall.percent));
  expect(within(g).getByText("진행 · 데이터 복사")).toBeInTheDocument();
  expect(asked).toEqual(["execution"]);
  // 미리보기 구획에는 없다(끝난 단계)
  expect(within(screen.getByRole("region", { name: "사전 점검·미리보기" })).queryByRole("group", { name: /진행$/ })).toBeNull();
  // 칸은 「실행」 행(실행 단계 목록의 마지막 행) 안에 있다 -- 실행 직전 재점검 행이 아니다
  const rows = within(within(exec).getByRole("list", { name: "실행 단계" })).getAllByRole("listitem");
  expect(within(rows[rows.length - 1]).getByRole("group", { name: "실행 진행" })).toBe(g);
});

test("진행률: 스케줄 대기 중(RUNNING 관측 전)인 실행 행은 진행 칸도 요청도 없다", async () => {
  const asked = progressCounter();
  renderStages({ ...RUNNING_DSYNC, sched_wait_seconds: null });
  await screen.findByText(/스케줄 대기 중/);
  await settle();
  expect(screen.queryByRole("group", { name: /진행$/ })).toBeNull();
  expect(asked).toEqual([]);
});

test("진행률: 실행 직전 재점검 중(실행 ref 전)이면 없다", async () => {
  const asked = progressCounter();
  renderStages({ ...RUNNING_DSYNC, phase_refs: { preflight: "pod/a", preview: "pod/b", exec_preflight: "pod/c" },
    exec_submitted_at: null, sched_wait_seconds: null });
  await settle();
  expect(screen.queryByRole("group", { name: /진행$/ })).toBeNull();
  expect(asked).toEqual([]);
});

test("진행률: 끝나면(Succeeded) 진행 칸이 사라지고 더 묻지 않는다 -- 처음부터 끝난 잡은 요청 0", async () => {
  const asked = progressCounter();
  const view = renderStages(RUNNING_DSYNC);
  await screen.findByRole("group", { name: "실행 진행" });
  expect(asked).toEqual(["execution"]);
  view.rerenderJob({ ...RUNNING_DSYNC, state: "Succeeded",
    transitions: [...EXEC_TR, { from_state: "Executing", to_state: "Succeeded", at: "2026-11-26T08:51:51Z" }] as never });
  await waitFor(() => expect(screen.queryByRole("group", { name: "실행 진행" })).toBeNull());
  view.unmount();

  const asked2 = progressCounter();
  renderStages({ ...RUNNING_DSYNC, state: "Succeeded" });
  await settle();
  expect(screen.queryByRole("group", { name: /진행$/ })).toBeNull();
  expect(asked2).toEqual([]);
});

test("진행률: scan(dscan)·rm(drm) 진행 중 행에는 없다(sync 도구만)", async () => {
  const asked = progressCounter();
  const scan = renderStages({ operation: "scan", tool: "dscan", state: "Running",
    phase_refs: { preflight: "pod/a", execution: "vcjob/j1" } });
  await settle();
  expect(screen.queryByRole("group", { name: /진행$/ })).toBeNull();
  scan.unmount();
  renderStages({ ...RUNNING_DSYNC, operation: "rm", tool: "drm" });
  await settle();
  expect(screen.queryByRole("group", { name: /진행$/ })).toBeNull();
  expect(asked).toEqual([]);
});

test("진행률: PreviewRunning + nsync → 미리보기 행에 보인다(phase=preview)", async () => {
  const asked = progressCounter(() => ({ status: 200, body: PRESP.available_preview }));
  renderStages({ operation: "sync", tool: "nsync", state: "PreviewRunning",
    phase_refs: { preflight: "pod/a", preview: "vcjob/p1" },
    transitions: [{ from_state: "Preflight", to_state: "PreviewRunning", at: "2026-11-26T08:20:00Z" }] as never });
  const pre = screen.getByRole("region", { name: "사전 점검·미리보기" });
  const g = await within(pre).findByRole("group", { name: "미리보기 진행" });
  expect(await within(g).findByText("진행 · 배치 계획")).toBeInTheDocument();
  expect(within(g).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50");
  expect(asked).toEqual(["preview"]);
  expect(within(screen.getByRole("region", { name: "실행" })).queryByRole("group", { name: /진행$/ })).toBeNull();
});

// 2026-11-27 d50 실증 F2·F3(p-vp-06): 미리보기가 진행 중인데 1단계 카드 맨 위에 「이 잡은 미리보기 요약이 저장되지 않았습니다」가 떠 실패처럼
// 읽혔다(2026-10-08 요청 상세 재설계 3f8f515c 부터 -- 미리보기 vcjob 제출(ref)·PreviewRunning 전이만 보고 끝났는지는 보지 않았다).
const NO_SUMMARY = "이 잡은 미리보기 요약이 저장되지 않았습니다";
const PREVIEWING: Partial<DataJob> = {
  operation: "sync", tool: "dsync", state: "PreviewRunning", phase_refs: { preflight: "pod/a", preview: "vcjob/p1" },
  transitions: [{ from_state: "Pending", to_state: "Preflight", at: "2026-11-26T08:19:50Z" },
                { from_state: "Preflight", to_state: "PreviewRunning", at: "2026-11-26T08:20:00Z" }] as never,
};
test("미리보기 진행 중(PreviewRunning)엔 「미리보기 요약이 저장되지 않았습니다」가 없다 -- 끝났는데 요약이 없을 때만", async () => {
  progressCounter(() => ({ status: 200, body: PRESP.unavailable }));
  const view = renderStages(PREVIEWING);
  const pre = screen.getByRole("region", { name: "사전 점검·미리보기" });
  await within(pre).findByRole("group", { name: "미리보기 진행" });
  await settle();
  expect(screen.queryByText(NO_SUMMARY)).toBeNull();
  // 끝났는데(ConfirmPending) 요약이 없으면 그 사실을 말한다(배너 「미리보기 결과 보기」의 포커스 대상이기도 하다)
  view.rerenderJob({ ...PREVIEWING, state: "ConfirmPending",
    transitions: [...(PREVIEWING.transitions as never[]),
                  { from_state: "PreviewRunning", to_state: "ConfirmPending", at: "2026-11-26T08:20:05Z" }] as never });
  expect(await screen.findByText(NO_SUMMARY)).toHaveAttribute("tabindex", "-1");
});

test("미리보기 중 취소돼 요약이 없으면 「저장되지 않았습니다」 -- 끝난 미리보기다", async () => {
  progressCounter();
  renderStages({ ...PREVIEWING, state: "Cancelled",
    transitions: [...(PREVIEWING.transitions as never[]),
                  { from_state: "PreviewRunning", to_state: "Cancelled", at: "2026-11-26T08:20:05Z" }] as never });
  expect(await screen.findByText(NO_SUMMARY)).toBeInTheDocument();
});

test("진행률: 503 이면 사유 문구", async () => {
  const e = PRESP.errors.find((x) => x.status === 503)!;
  progressCounter(() => ({ status: e.status, body: e.body }));
  renderStages(RUNNING_DSYNC);
  const g = await screen.findByRole("group", { name: "실행 진행" });
  expect(await within(g).findByText(reasonText("artifact_base_unavailable"))).toBeInTheDocument();
});

// 2026-11-27 d49 실증 C1: 취소는 vcjob 을 지워 Cancelled 전이가 러너의 유예 쓰기(stderr.log 꼬리·summary.json)보다 먼저 오거나 같은
// 순간이고 NFS base 는 다른 노드에서 새 파일이 늦게 보인다 -- 전이 때 한 번 읽은 목록(stderr.log 78 B·summary 없음)이 새로고침 전까지
// 남았다. 종단 전이 뒤엔 useArtifacts 가 몇 초 뒤 다시 읽는다(JobStages 가 model.terminal 을 넘긴다).
test("취소 직후: Cancelled 전이 뒤 러너가 늦게 쓴 summary.json·자란 stderr.log 칩이 몇 초 뒤 새로고침 없이 나타난다", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let written = false;
  const early = { entries: [{ phase: "execution", name: "stderr.log", size: 78, modified_at: 1 }], truncated: false };
  const late = { entries: [{ phase: "execution", name: "stderr.log", size: 672, modified_at: 2 },
                           { phase: "execution", name: "summary.json", size: 160, modified_at: 2 }], truncated: false };
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(written ? late : early)),
    http.get("/api/user/jobs/:jid/progress", () => HttpResponse.json(PRESP.available)),
    http.get("/api/user/jobs/j1/logs", () => HttpResponse.json({ phase: "execution", ref: "vcjob/e", source: "archived", entries: [] })),
  );
  const view = renderStages(RUNNING_DSYNC);
  await screen.findByRole("button", { name: "execution/stderr.log" });
  view.rerenderJob({ ...RUNNING_DSYNC, state: "Cancelled",
    transitions: [...EXEC_TR, { from_state: "Executing", to_state: "Cancelled", at: "2026-11-26T08:30:00Z" }] as never });
  await act(async () => { await vi.advanceTimersByTimeAsync(200); });
  expect(screen.queryByRole("button", { name: "execution/summary.json" })).toBeNull();   // 전이 때 읽은 목록엔 아직 없다
  written = true;                                                                          // 러너의 유예 쓰기가 이제 보인다
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(await screen.findByRole("button", { name: "execution/summary.json" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "execution/stderr.log" })).toHaveTextContent("672 B");
});

test("결과 카드: 출력이 넘친 요약(output_truncated)은 모르는 수치가 있다고 한 줄로 말한다", async () => {
  progressCounter();
  renderStages({ ...RUNNING_DSYNC, state: "Succeeded", result_summary: { detail_version: 2, returncode: 0, files: null,
    bytes: 1073741824, tool: "dsync", dryrun: false, copy_failed: 0, create_failed: 0, tool_errors: 0,
    output_truncated: true } });
  const exec = screen.getByRole("region", { name: "실행" });
  expect(within(exec).getByText(
    "도구 출력이 너무 커서 일부 수치(반영 항목 수 등)는 모릅니다 — stdout.log 는 앞부분과 끝부분만 남았습니다"))
    .toBeInTheDocument();
});
