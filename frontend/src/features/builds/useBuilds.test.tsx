import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect, vi } from "vitest";
import { useBuild, useBuilds } from "./useBuilds";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// M4: 빌드가 종단으로 바뀌는 순간 로그 폴링이 멈춰서(useBuildLog의 active가
// isTerminal 기준으로 꺼진다) 마지막 몇 초 분량("=== pushed ===", DMS_BUILD_OK)이
// 화면에 반영되기 전에 폴링이 끊길 수 있다. useBuild는 종단으로 바뀌는 순간
// ["build-log", id] 쿼리를 정확히 한 번 무효화해 마지막 로그를 놓치지 않아야 한다.
test("빌드 상태가 종단으로 바뀌면 build-log 쿼리를 한 번 무효화한다", async () => {
  let state = "Running";
  server.use(http.get("/api/admin/builds/b1", () =>
    HttpResponse.json({ build_id: "b1", repo_url: "u", git_ref: "main", commit_sha: null,
      images: ["dms"], node_name: "dms-w1", state, reason_code: null, tag: "b01234567",
      created_at: "t", finished_at: null })));

  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );

  const { result } = renderHook(() => useBuild("b1"), { wrapper });
  await waitFor(() => expect(result.current.data?.state).toBe("Running"));
  expect(invalidateSpy).not.toHaveBeenCalledWith(
    expect.objectContaining({ queryKey: ["build-log", "b1"] }));

  state = "Succeeded";
  await qc.invalidateQueries({ queryKey: ["builds", "b1"] }); // build 쿼리 자체를 강제로 재조회
  await waitFor(() => expect(result.current.data?.state).toBe("Succeeded"));

  expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["build-log", "b1"] });
  const callsAfterTransition = invalidateSpy.mock.calls.filter(
    (c) => JSON.stringify(c[0]) === JSON.stringify({ queryKey: ["build-log", "b1"] })).length;

  // 이미 종단인 상태로 다시 리페치돼도(다음 폴링 등) 두 번째 무효화가 또 일어나면
  // 안 된다 -- 정확히 한 번이어야 한다.
  await qc.invalidateQueries({ queryKey: ["builds", "b1"] });
  await waitFor(() => expect(result.current.data?.state).toBe("Succeeded"));
  const callsAfterSecondRefetch = invalidateSpy.mock.calls.filter(
    (c) => JSON.stringify(c[0]) === JSON.stringify({ queryKey: ["build-log", "b1"] })).length;
  expect(callsAfterSecondRefetch).toBe(callsAfterTransition);
});

test("처음 로드될 때 이미 종단 상태면 무효화하지 않는다", async () => {
  // 최초 마운트 시점에 이미 Succeeded/Failed인 경우까지 무효화하면, 아직 한 번도
  // active였던 적 없는 로그 쿼리를 불필요하게 다시 부른다.
  server.use(http.get("/api/admin/builds/b1", () =>
    HttpResponse.json({ build_id: "b1", repo_url: "u", git_ref: "main", commit_sha: "c",
      images: ["dms"], node_name: "dms-w1", state: "Succeeded", reason_code: null,
      tag: "b01234567", created_at: "t", finished_at: "t2" })));

  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );

  const { result } = renderHook(() => useBuild("b1"), { wrapper });
  await waitFor(() => expect(result.current.data?.state).toBe("Succeeded"));
  expect(invalidateSpy).not.toHaveBeenCalledWith(
    expect.objectContaining({ queryKey: ["build-log", "b1"] }));
});

// 2026-10-08 리뷰 R3: 빌드가 성공하는 순간 새 태그가 레지스트리에 생긴다 -- 릴리스 화면의 태그 목록
// 쿼리를 무효화해야 열어 둔(또는 다음에 여는) 릴리스 화면이 빌드 전 목록을 들고 있지 않는다.
const BUILD = (state: string, id = "b1") => ({ build_id: id, repo_url: "u", git_ref: "main", commit_sha: null,
  images: ["dms"], node_name: "dms-w1", state, reason_code: null, tag: "d200", created_at: "t", finished_at: null });

function harness() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = vi.spyOn(qc, "invalidateQueries");
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  const targetCalls = () => spy.mock.calls.filter(([f]) => JSON.stringify(f?.queryKey) === '["release-targets"]').length;
  return { qc, wrapper, targetCalls };
}

test("useBuild: 진행 중 → Succeeded 면 릴리스 태그 목록을 무효화, Failed 면 하지 않는다", async () => {
  for (const [end, expected] of [["Succeeded", 1], ["Failed", 0]] as const) {
    let state = "Running";
    server.use(http.get("/api/admin/builds/b1", () => HttpResponse.json(BUILD(state))));
    const { qc, wrapper, targetCalls } = harness();
    const { result, unmount } = renderHook(() => useBuild("b1"), { wrapper });
    await waitFor(() => expect(result.current.data?.state).toBe("Running"));
    state = end;
    await qc.invalidateQueries({ queryKey: ["builds", "b1"] });
    await waitFor(() => expect(result.current.data?.state).toBe(end));
    expect(targetCalls()).toBe(expected);
    unmount();
  }
});

test("useBuilds: 목록에서 비종단이던 빌드가 Succeeded 가 되면 무효화(첫 로드의 기존 성공 빌드는 아님)", async () => {
  let rows = [BUILD("Succeeded", "old"), BUILD("Running", "b1")];
  server.use(http.get("/api/admin/builds", () => HttpResponse.json(rows)));
  const { qc, wrapper, targetCalls } = harness();
  const { result } = renderHook(() => useBuilds(), { wrapper });
  await waitFor(() => expect(result.current.data?.length).toBe(2));
  expect(targetCalls()).toBe(0);
  rows = [BUILD("Succeeded", "old"), BUILD("Succeeded", "b1")];
  await qc.invalidateQueries({ queryKey: ["builds"] });
  await waitFor(() => expect(result.current.data?.[1].state).toBe("Succeeded"));
  expect(targetCalls()).toBe(1);
});
