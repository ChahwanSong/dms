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

function wrap(jobId = "j1") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <JobViewer jobId={jobId} />
    </QueryClientProvider>,
  );
}

test("renders a tab for each artifact entry plus the preflight log tab", async () => {
  server.use(http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(ARTIFACTS)));
  wrap();
  expect(await screen.findByRole("button", { name: "execution/stdout.log" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "execution/stderr.log" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "preflight 로그" })).toBeInTheDocument();
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
