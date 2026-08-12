import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { JobViewer } from "./JobViewer";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const ARTIFACTS = {
  entries: [
    { phase: "execution", name: "stdout.log", size: 120, modified_at: 1754400000 },
    { phase: "execution", name: "stderr.log", size: 40, modified_at: 1754400001 },
  ],
  truncated: false,
};

const REFS = { preflight: "pod/p1" };

function wrap(phaseRefs: Record<string, string> | null = REFS, jobId = "j1") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <JobViewer jobId={jobId} phaseRefs={phaseRefs} />
    </QueryClientProvider>,
  );
}

test("renders a tab for each artifact entry plus a log tab per phase_refs key", async () => {
  server.use(http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)));
  wrap();
  expect(await screen.findByRole("button", { name: "execution/stdout.log" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "execution/stderr.log" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "preflight 로그" })).toBeInTheDocument();
});

test("renders one log tab per phase that actually has a ref, including exec_preflight", async () => {
  // confirm 후 재검증(exec_preflight)이 실패한 잡을 진단할 때, 하드코딩된 "preflight"
  // 탭은 *초기* preflight의 성공 로그를 보여줘 사람을 오도한다.
  server.use(http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)));
  wrap({ preflight: "pod/a", exec_preflight: "pod/b" });
  expect(await screen.findByRole("button", { name: "preflight 로그" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "exec_preflight 로그" })).toBeInTheDocument();
});

test("renders no log tab when the job has no phase_refs", async () => {
  server.use(http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)));
  wrap({});
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
  wrap({ preflight: "pod/a", exec_preflight: "pod/b" });
  await userEvent.click(await screen.findByRole("button", { name: "exec_preflight 로그" }));
  expect(await screen.findByText("log of exec_preflight")).toBeInTheDocument();
  expect(asked).toEqual(["exec_preflight"]);
});

test("does not request the artifact body before a tab is clicked", async () => {
  let bodyCalls = 0;
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)),
    http.get("/api/user/jobs/j1/artifacts/execution/stdout.log", () => {
      bodyCalls += 1;
      return HttpResponse.json({ phase: "execution", name: "stdout.log", size: 120, truncated: false, content: "hello" });
    }),
  );
  wrap();
  await screen.findByRole("button", { name: "execution/stdout.log" });
  // Give any accidental in-flight request a tick to land.
  await new Promise((r) => setTimeout(r, 10));
  expect(bodyCalls).toBe(0);
});

test("clicking an artifact tab shows its content and a truncated badge when truncated", async () => {
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)),
    http.get("/api/user/jobs/j1/artifacts/execution/stdout.log", () =>
      HttpResponse.json({ phase: "execution", name: "stdout.log", size: 999999, truncated: true, content: "tail content" })),
  );
  wrap();
  await userEvent.click(await screen.findByRole("button", { name: "execution/stdout.log" }));
  expect(await screen.findByText("tail content")).toBeInTheDocument();
  expect(screen.getByText("뒷부분만 표시")).toBeInTheDocument();
});

test("log tab shows the localized message on a 409 log_not_available error", async () => {
  server.use(
    http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)),
    http.get("/api/user/jobs/j1/logs", () => HttpResponse.json({ detail: "log_not_available" }, { status: 409 })),
  );
  wrap();
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
  wrap();
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
  wrap();
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
  wrap({ execution: "vcjob/j1" });
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
  wrap();
  await userEvent.click(await screen.findByRole("button", { name: "preflight 로그" }));
  expect(await screen.findByText("live tail")).toBeInTheDocument();
  expect(screen.queryByText("잡 종료 시점에 저장된 사본 — 파드당 마지막 16KB")).not.toBeInTheDocument();
  expect(screen.queryByText("뒷부분만 표시")).not.toBeInTheDocument();
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
  const { container } = wrap();
  await userEvent.click(await screen.findByRole("button", { name: "preflight 로그" }));
  await screen.findByText("p1");
  // 빈 문자열은 text 쿼리로 못 잡으니 <pre> 자체의 존재로 "내용으로 그렸다"를 고정한다.
  expect(container.querySelector("pre")).not.toBeNull();
  expect(screen.queryByText("파드 로그를 더 이상 조회할 수 없습니다")).not.toBeInTheDocument();
});
