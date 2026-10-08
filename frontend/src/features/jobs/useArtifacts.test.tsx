import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect, vi } from "vitest";
import { useArtifacts, useArtifactFile, useJobLogs } from "./useArtifacts";

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

test("useArtifacts returns the entries and the truncated flag", async () => {
  const body = {
    entries: [{ phase: "preflight", name: "stdout.log", size: 128, modified_at: 1754400000 }],
    truncated: false,
  };
  server.use(http.get("/api/user/jobs/j1/artifacts", () => HttpResponse.json(body)));
  const { result } = renderHook(() => useArtifacts("j1"), { wrapper });
  await waitFor(() => expect(result.current.data).toEqual(body));
  expect(result.current.data?.entries).toHaveLength(1);
});

test("useArtifactFile with enabled: false sends no request", async () => {
  let requestCount = 0;
  server.use(http.get("/api/user/jobs/j1/artifacts/preflight/stdout.log", () => {
    requestCount += 1;
    return HttpResponse.json({ phase: "preflight", name: "stdout.log", size: 4, truncated: false, content: "ok\n" });
  }));
  const { result } = renderHook(() => useArtifactFile("j1", "preflight", "stdout.log", false), { wrapper });
  // Give any accidental in-flight request a tick to land.
  await new Promise((r) => setTimeout(r, 10));
  expect(requestCount).toBe(0);
  expect(result.current.data).toBeUndefined();
  expect(result.current.fetchStatus).toBe("idle");
});

test("useArtifactFile with enabled: true requests the correct URL and returns the body", async () => {
  const file = { phase: "preflight", name: "stdout.log", size: 4, truncated: false, content: "ok\n" };
  let requestedUrl: string | null = null;
  server.use(http.get("/api/user/jobs/j1/artifacts/preflight/stdout.log", ({ request }) => {
    requestedUrl = request.url;
    return HttpResponse.json(file);
  }));
  const { result } = renderHook(() => useArtifactFile("j1", "preflight", "stdout.log", true), { wrapper });
  await waitFor(() => expect(result.current.data).toEqual(file));
  expect(requestedUrl).toContain("/api/user/jobs/j1/artifacts/preflight/stdout.log");
});

// --- 2026-10-08 요청 상세 재설계: 상태 전이 때 목록 다시 읽기 + 진행 중 로그 라이브 ----------------------------------

test("useArtifacts: refreshKey 가 바뀌면 목록을 다시 읽는다(러너는 phase 가 끝날 때 파일을 쓴다)", async () => {
  let calls = 0;
  server.use(http.get("/api/user/jobs/j1/artifacts", () => {
    calls += 1;
    return HttpResponse.json({ entries: [], truncated: false });
  }));
  const { result, rerender } = renderHook(({ k }) => useArtifacts("j1", k), { wrapper, initialProps: { k: "Executing|preflight" } });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(calls).toBe(1);
  rerender({ k: "Succeeded|execution,preflight" });
  await waitFor(() => expect(calls).toBe(2));
  // 쿼리 키가 고정이라 다시 읽는 동안에도 이전 목록이 남는다(칩이 깜빡이지 않게)
  expect(result.current.data).toEqual({ entries: [], truncated: false });
  // 같은 refreshKey 로 다시 그려도 더 읽지 않는다(상태 전이가 곧 갱신 신호다 -- 폴링 아님)
  rerender({ k: "Succeeded|execution,preflight" });
  await new Promise((r) => setTimeout(r, 20));
  expect(calls).toBe(2);
});

test("useArtifacts: refreshKey 재조회가 실패해도 마지막 성공 목록은 남고 isError 만 켜진다(리뷰 V4)", async () => {
  let calls = 0;
  const ok = { entries: [{ phase: "preview", name: "stdout.log", size: 5, modified_at: 1 }], truncated: false };
  server.use(http.get("/api/user/jobs/j1/artifacts", () => {
    calls += 1;
    return calls === 2 ? HttpResponse.json({ detail: "http_502" }, { status: 502 }) : HttpResponse.json(ok);
  }));
  const { result, rerender } = renderHook(({ k }) => useArtifacts("j1", k), { wrapper, initialProps: { k: "ConfirmPending|preflight,preview" } });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  rerender({ k: "Executing|exec_preflight,preflight,preview" });
  await waitFor(() => expect(result.current.isError).toBe(true));
  expect(result.current.data).toEqual(ok);
  expect(result.current.isLoading).toBe(false);          // 화면의 「불러오는 중」 골격도 다시 뜨지 않는다
});

test("N0 useArtifacts: 첫 조회(데이터 없음)가 진행 중일 때 refreshKey 가 바뀌면 그 조회에 합류하지 않고 새로 읽는다", async () => {
  // TanStack 5 의 refetch 는 데이터가 없으면 진행 중 조회에 합류한다 -- 전이 전에 떠난 요청의 낡은 목록(새 파일 없음)이
  // 마지막 목록이 됐다(리뷰 N0, fixcheck/logs/edge.test.tsx H). 여기 qc 는 고정이다(공용 wrapper 는 그릴 때마다 새로 만든다).
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const stable = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  let calls = 0;
  let written = false;
  let releaseFirst: (() => void) | null = null;
  const fresh = { entries: [{ phase: "preview", name: "stdout.log", size: 5, modified_at: 1 }], truncated: false };
  server.use(http.get("/api/user/jobs/j1/artifacts", async () => {
    calls += 1;
    const snap = written;
    if (calls === 1) await new Promise<void>((r) => { releaseFirst = r; });   // 전이 전에 떠난 첫 조회를 붙잡아 둔다
    return HttpResponse.json(snap ? fresh : { entries: [], truncated: false });
  }));
  const { result, rerender } = renderHook(({ k }) => useArtifacts("j1", k),
    { wrapper: stable, initialProps: { k: "PreviewRunning|preflight,preview" } });
  // 결과는 추적(tracked) 객체라 data 를 한 번 읽어 두어야 그 변화로 다시 그린다(화면 컴포넌트는 렌더 중에 읽는다).
  expect(result.current.data).toBeUndefined();
  await waitFor(() => expect(calls).toBe(1));
  written = true;                                   // 러너가 파일을 쓰고 잡이 전이했다
  rerender({ k: "ConfirmPending|preflight,preview" });
  await waitFor(() => expect(calls).toBe(2));       // 합류했다면 1 에 머문다
  await waitFor(() => expect(result.current.data).toEqual(fresh));
  await act(async () => { releaseFirst?.(); await new Promise((r) => setTimeout(r, 20)); });
  expect(result.current.data).toEqual(fresh);       // 취소된 첫 조회의 낡은 응답이 덮어쓰지 않는다
  expect(calls).toBe(2);
});

test("useJobLogs live: 3초마다 다시 읽고, live 가 꺼지면 멈춘다", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    let calls = 0;
    server.use(http.get("/api/user/jobs/j1/logs", () => {
      calls += 1;
      return HttpResponse.json({ phase: "preflight", ref: "pod/p1", source: "live", entries: [] });
    }));
    const { rerender } = renderHook(({ live }) => useJobLogs("j1", "preflight", true, { live }),
      { wrapper, initialProps: { live: true } });
    await waitFor(() => expect(calls).toBe(1));
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    await waitFor(() => expect(calls).toBe(2));
    rerender({ live: false });
    await act(async () => { await vi.advanceTimersByTimeAsync(7000); });
    expect(calls).toBe(2);
  } finally {
    vi.useRealTimers();
  }
});
