import { render, screen } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect, vi } from "vitest";
import { BuildDetail } from "./BuildDetail";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => { server.resetHandlers(); vi.useRealTimers(); });
afterAll(() => server.close());

const buildRow = (over: Record<string, unknown> = {}) => ({
  build_id: "b1", repo_url: "u", git_ref: "main", commit_sha: "deadbeefcafebabe",
  images: ["dms"], node_name: "dms-w1", state: "Succeeded", reason_code: null,
  tag: "b01234567", created_at: "2026-08-06T00:00:00Z", finished_at: "2026-08-06T00:10:00Z",
  ...over,
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/admin/builds/b1"]}>
        <Routes><Route path="/admin/builds/:buildId" element={<BuildDetail />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("저장소 URL을 보여준다", async () => {
  // I3: repo_url을 화면에 안 보여주면 commit SHA만으로는 어느 저장소의 커밋인지
  // 알 수 없다 -- admin이 임의 저장소를 제출해도 운영자가 알아챌 방법이 없다.
  server.use(
    http.get("/api/admin/builds/b1", () =>
      HttpResponse.json(buildRow({ state: "Succeeded", repo_url: "https://example/r.git" }))),
    http.get("/api/admin/builds/b1/log", () => HttpResponse.json({ build_id: "b1", log: "ok\n" })),
  );
  renderPage();
  expect(await screen.findByText("https://example/r.git")).toBeInTheDocument();
});

test("로그가 렌더된다", async () => {
  server.use(
    http.get("/api/admin/builds/b1", () => HttpResponse.json(buildRow({ state: "Succeeded" }))),
    http.get("/api/admin/builds/b1/log", () => HttpResponse.json({ build_id: "b1", log: "hello build log\n" })),
  );
  renderPage();
  expect(await screen.findByText(/hello build log/)).toBeInTheDocument();
});

test("M5: 진행 중(Running) 빌드는 busy 배지로 보인다(회색이 아니다)", async () => {
  server.use(
    http.get("/api/admin/builds/b1", () => HttpResponse.json(buildRow({ state: "Running" }))),
    http.get("/api/admin/builds/b1/log", () => HttpResponse.json({ build_id: "b1", log: "building\n" })),
  );
  const { container } = renderPage();
  await screen.findByText(/building/);
  expect(screen.getByText("Running")).toBeInTheDocument();
  expect(container.querySelector(".text-busy")).not.toBeNull();
});

test("log가 null이면 흰 화면 대신 안내 문구를 보여준다", async () => {
  server.use(
    http.get("/api/admin/builds/b1", () => HttpResponse.json(buildRow({ state: "Running" }))),
    http.get("/api/admin/builds/b1/log", () => HttpResponse.json({ build_id: "b1", log: null })),
  );
  renderPage();
  expect(await screen.findByRole("heading", { name: "빌드 b1" })).toBeInTheDocument();
  expect(await screen.findByText("로그가 아직 없습니다")).toBeInTheDocument();
});

test("사유 코드를 한글 메시지로 보여주고 원시 코드는 노출하지 않는다", async () => {
  server.use(
    http.get("/api/admin/builds/b1", () => HttpResponse.json(buildRow({ state: "Failed", reason_code: "build_failed" }))),
    http.get("/api/admin/builds/b1/log", () => HttpResponse.json({ build_id: "b1", log: "boom\n" })),
  );
  renderPage();
  expect(await screen.findByText("빌드가 실패했습니다 — 로그를 확인하세요")).toBeInTheDocument();
  expect(screen.queryByText("build_failed")).not.toBeInTheDocument();
});

test("T6: images가 null이어도 흰 화면이 되지 않는다", async () => {
  server.use(
    http.get("/api/admin/builds/b1", () => HttpResponse.json(buildRow({ images: null }))),
    http.get("/api/admin/builds/b1/log", () => HttpResponse.json({ build_id: "b1", log: "ok\n" })),
  );
  renderPage();
  // h1은 로딩 중에도 이미 있으므로, 데이터가 실제로 로드된 뒤에만 나타나는
  // 노드 이름까지 기다려야 images 렌더 경로(크래시 지점)를 실제로 지나간다.
  expect(await screen.findByText("dms-w1")).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "빌드 b1" })).toBeInTheDocument();
});

test("Pending 빌드는 프리플라이트 캡션을 보여준다", async () => {
  // 슬라이스 21 §3: Pending 은 이제 "제출 대기"가 아니라 적합성 확인(프로브,
  // 최대 180s)을 포함한다 -- 별도 상태 기계 없이 캡션 한 줄이 그 사실을 알린다.
  server.use(
    http.get("/api/admin/builds/b1", () =>
      HttpResponse.json(buildRow({ state: "Pending", commit_sha: null, finished_at: null }))),
    http.get("/api/admin/builds/b1/log", () => HttpResponse.json({ build_id: "b1", log: null })),
  );
  renderPage();
  expect(await screen.findByText("적합성 확인(프리플라이트) 포함 — 최대 약 3분")).toBeInTheDocument();
});

test("종단 빌드에는 프리플라이트 캡션이 없다", async () => {
  server.use(
    http.get("/api/admin/builds/b1", () => HttpResponse.json(buildRow({ state: "Succeeded" }))),
    http.get("/api/admin/builds/b1/log", () => HttpResponse.json({ build_id: "b1", log: "ok\n" })),
  );
  renderPage();
  await screen.findByText("dms-w1");   // 데이터 로드 완료를 기다린 뒤 부재를 단언
  expect(screen.queryByText(/프리플라이트/)).not.toBeInTheDocument();
});

test("egress 실패 사유가 '인터넷을 아직 열지 않았을 수 있습니다' 문구로 보인다", async () => {
  // 설계 §3 의 핵심 문구 -- "운영자가 인터넷을 안 열었다를 즉시 안다"의 실체가
  // 이 한 줄이다. 원시 코드는 노출하지 않는다(reasonText 매핑 경로).
  server.use(
    http.get("/api/admin/builds/b1", () =>
      HttpResponse.json(buildRow({ state: "Failed", reason_code: "build_node_no_egress" }))),
    http.get("/api/admin/builds/b1/log", () =>
      HttpResponse.json({ build_id: "b1", log: "unreachable_443=github.com\n" })),
  );
  renderPage();
  expect(await screen.findByText(/인터넷을 아직 열지 않았을 수 있습니다/)).toBeInTheDocument();
  expect(screen.queryByText("build_node_no_egress")).not.toBeInTheDocument();
});

test("종단 빌드는 소요 시간을 보여준다", async () => {
  server.use(
    http.get("/api/admin/builds/b1", () => HttpResponse.json(buildRow({ state: "Succeeded" }))),
    http.get("/api/admin/builds/b1/log", () => HttpResponse.json({ build_id: "b1", log: "ok\n" })),
  );
  renderPage();
  expect(await screen.findByText("10분 0초")).toBeInTheDocument();
  expect(screen.getByText(/소요 시간/)).toBeInTheDocument();
});

test("진행 중 빌드는 경과 시간을 보여준다(소요를 지어내지 않는다)", async () => {
  const started = new Date(Date.now() - 192_500).toISOString();   // 3분 12초 전
  server.use(
    http.get("/api/admin/builds/b1", () =>
      HttpResponse.json(buildRow({ state: "Running", created_at: started, finished_at: null }))),
    http.get("/api/admin/builds/b1/log", () => HttpResponse.json({ build_id: "b1", log: "building\n" })),
  );
  renderPage();
  expect(await screen.findByText("3분 12초")).toBeInTheDocument();
  expect(screen.getByText(/경과 시간/)).toBeInTheDocument();
  expect(screen.queryByText(/소요/)).not.toBeInTheDocument();
});

test("종단 빌드(Succeeded)는 로그를 더 폴링하지 않는다", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let logCalls = 0;
  server.use(
    http.get("/api/admin/builds/b1", () => HttpResponse.json(buildRow({ state: "Succeeded" }))),
    http.get("/api/admin/builds/b1/log", () => {
      logCalls += 1;
      return HttpResponse.json({ build_id: "b1", log: "done\n" });
    }),
  );
  renderPage();
  await screen.findByText(/done/);
  const callsAfterInitialLoad = logCalls;
  // 로그 폴링 주기(3000ms)의 3배를 넘겨도 추가 호출이 없어야 한다.
  await vi.advanceTimersByTimeAsync(9000);
  expect(logCalls).toBe(callsAfterInitialLoad);
});
