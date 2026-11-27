import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect, vi } from "vitest";
import contract from "../../lib/progressContract.json";
import {
  ARTIFACTS_SETTLE_MS, PROGRESS_POLL_MS, PROGRESS_SLOW_AFTER_MISSES, PROGRESS_SLOW_POLL_MS, jobProgressKey, progressPollMs,
  useArtifacts, useArtifactFile, useJobLogs, useJobProgress,
} from "./useArtifacts";

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

test("useArtifacts: refreshKey 가 바뀌면 목록을 다시 읽는다(단계가 끝나면 새 출력 파일이 생긴다)", async () => {
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

// --- 2026-11-27 dms-k8s d49 실증 C1: 종단 전이 뒤 지연 재조회 --------------------------------------------------------------------------
// 취소는 vcjob 을 지워 Cancelled 전이가 러너의 유예 쓰기(stderr.log 꼬리·summary.json·progress.json ended)보다 먼저 오거나 같은 순간이고,
// NFS base 는 다른 노드에서 새 파일이 늦게 보인다 -- 전이 때 한 번만 읽으면 칩이 낡은 채 남았다(새로고침해야 summary.json 이 보였다).

function artifactsCounter(reply: (n: number) => unknown = () => ({ entries: [], truncated: false })) {
  const seen = { calls: 0 };
  server.use(http.get("/api/user/jobs/j1/artifacts", () => {
    seen.calls += 1;
    return HttpResponse.json(reply(seen.calls) as Record<string, unknown>);
  }));
  return seen;
}

test("useArtifacts: 종단 전이를 보면 ARTIFACTS_SETTLE_MS 뒤마다 한 번씩 더 읽고 그 뒤로는 읽지 않는다 -- 늦게 쓴 summary.json 이 칩에 나타난다", () => withFakeTimers(async () => {
  expect(ARTIFACTS_SETTLE_MS).toEqual([3000, 10000, 30000]);
  const late = { entries: [{ phase: "execution", name: "summary.json", size: 160, modified_at: 2 }], truncated: false };
  let written = false;
  const seen = artifactsCounter(() => (written ? late : { entries: [], truncated: false }));
  const { result, rerender } = renderHook(({ k, t }) => useArtifacts("j1", k, t),
    { wrapper: stableWrapper(), initialProps: { k: "Executing|execution", t: false } });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(seen.calls).toBe(1);
  rerender({ k: "Cancelled|execution", t: true });
  await waitFor(() => expect(seen.calls).toBe(2));                     // 전이 즉시 한 번(지금까지와 같다)
  expect(result.current.data).toEqual({ entries: [], truncated: false });
  written = true;                                                       // 러너가 유예 안에 summary.json 을 썼다(전이 뒤)
  await tick(ARTIFACTS_SETTLE_MS[0] - 300);
  expect(seen.calls).toBe(2);
  await tick(400);
  await waitFor(() => expect(seen.calls).toBe(3));
  await waitFor(() => expect(result.current.data).toEqual(late));
  for (let i = 1; i < ARTIFACTS_SETTLE_MS.length; i++) {
    await tick(ARTIFACTS_SETTLE_MS[i] - ARTIFACTS_SETTLE_MS[i - 1]);
    await waitFor(() => expect(seen.calls).toBe(3 + i));
  }
  await tick(120_000);
  expect(seen.calls).toBe(2 + ARTIFACTS_SETTLE_MS.length);              // 종단 뒤 주기 폴링은 없다
}));

test("useArtifacts: 비종단 전이·이미 종단인 잡의 마운트는 지연 재조회를 예약하지 않는다", () => withFakeTimers(async () => {
  const last = ARTIFACTS_SETTLE_MS[ARTIFACTS_SETTLE_MS.length - 1];
  const seen = artifactsCounter();
  const { result, rerender } = renderHook(({ k, t }) => useArtifacts("j1", k, t),
    { wrapper: stableWrapper(), initialProps: { k: "ConfirmPending|preflight,preview", t: false } });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  rerender({ k: "Executing|execution,preflight,preview", t: false });
  await waitFor(() => expect(seen.calls).toBe(2));
  await tick(last + 5000);
  expect(seen.calls).toBe(2);
  const done = artifactsCounter();
  const mounted = renderHook(() => useArtifacts("j1", "Cancelled|execution", true), { wrapper: stableWrapper() });
  await waitFor(() => expect(mounted.result.current.isSuccess).toBe(true));
  await tick(last + 5000);
  expect(done.calls).toBe(1);
}));

test("useArtifacts: 종단 전이 뒤 언마운트하면 남은 지연 재조회를 지운다", () => withFakeTimers(async () => {
  const seen = artifactsCounter();
  const { result, rerender, unmount } = renderHook(({ k, t }) => useArtifacts("j1", k, t),
    { wrapper: stableWrapper(), initialProps: { k: "Executing|execution", t: false } });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  rerender({ k: "Cancelled|execution", t: true });
  await waitFor(() => expect(seen.calls).toBe(2));
  unmount();
  await tick(ARTIFACTS_SETTLE_MS[ARTIFACTS_SETTLE_MS.length - 1] + 5000);
  expect(seen.calls).toBe(2);
}));

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

// --- 2026-11-26 sync 진행률: 진행 중인 행에서만 5초, available:false·오류가 연속 3회면 15초, 숨긴 탭은 멈춘다 ----------------------
// 응답은 계약 파일(lib/progressContract.json examples.responses)을 그대로 돌려준다(손으로 쓴 모의 모양 금지).

const RESP = contract.examples.responses;
type Reply = { status: number; body: unknown };
// urls.at = 요청마다 (가짜) 시계 -- 간격 시험은 이 시각 차로 단언한다(shouldAdvanceTime 의 실시간 흐름·waitFor 대기가 tick 경계를
// 밀어도 흔들리지 않게).
function progressServer(reply: () => Reply) {
  const urls = Object.assign([] as string[], { at: [] as number[] });
  server.use(http.get("/api/user/jobs/j1/progress", ({ request }) => {
    urls.push(request.url);
    urls.at.push(Date.now());
    const r = reply();
    return HttpResponse.json(r.body as Record<string, unknown>, { status: r.status });
  }));
  return urls;
}
const errorReply = (detail: string): Reply => {
  const e = RESP.errors.find((x) => x.body.detail === detail)!;
  return { status: e.status, body: e.body };
};

async function withFakeTimers(fn: () => Promise<void>) {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try { await fn(); } finally { vi.useRealTimers(); focusManager.setFocused(undefined); }
}
const tick = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
// 공용 wrapper 는 그릴 때마다 QueryClient 를 새로 만든다 -- rerender(live 끄기) 뒤에도 같은 캐시를 보도록 고정한다.
function stableWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

test("useJobProgress: 폴링 간격은 계약 poll_ms 와 같고, 쿼리 키는 잡·단계마다 하나", () => {
  expect(PROGRESS_POLL_MS).toBe(contract.poll_ms.normal);
  expect(PROGRESS_SLOW_POLL_MS).toBe(contract.poll_ms.slow);
  expect(jobProgressKey("j1", "preview")).toEqual(["jobprogress", "j1", "preview"]);
});

test("useJobProgress: 느린 간격은 헛걸음(available:false·오류)이 연속 3회부터 -- 그 전엔 5초", () => {
  expect(PROGRESS_SLOW_AFTER_MISSES).toBe(3);
  expect([0, 1, 2, 3, 4, 50].map(progressPollMs)).toEqual([
    PROGRESS_POLL_MS, PROGRESS_POLL_MS, PROGRESS_POLL_MS, PROGRESS_SLOW_POLL_MS, PROGRESS_SLOW_POLL_MS, PROGRESS_SLOW_POLL_MS]);
});

test("useJobProgress live: 5초마다 다시 읽고(단계는 phase 질의로), live 가 꺼지면 멈춘다", () => withFakeTimers(async () => {
  const urls = progressServer(() => ({ status: 200, body: RESP.available }));
  const { result, rerender } = renderHook(({ live }) => useJobProgress("j1", "execution", live),
    { wrapper: stableWrapper(), initialProps: { live: true } });
  await waitFor(() => expect(result.current.data).toEqual(RESP.available));
  expect(urls).toHaveLength(1);
  expect(new URL(urls[0]).searchParams.get("phase")).toBe("execution");
  await tick(PROGRESS_POLL_MS - 200);
  expect(urls).toHaveLength(1);                     // 간격 전에는 읽지 않는다
  await tick(300);
  await waitFor(() => expect(urls).toHaveLength(2));
  await tick(PROGRESS_POLL_MS + 100);
  await waitFor(() => expect(urls).toHaveLength(3));
  rerender({ live: false });
  await tick(PROGRESS_SLOW_POLL_MS * 2);
  expect(urls).toHaveLength(3);
}));

// 요청 사이 간격이 want 와 같다(간격 타이머는 응답 처리 뒤에 다시 걸리므로 그만큼의 여유 500ms). 1초씩 나눠 흘려 응답 처리가 끼어들게 한다.
async function runFor(ms: number) {
  for (let t = 0; t < ms; t += 1000) await tick(1000);
}
function expectGaps(at: number[], want: number[]) {
  const gaps = at.slice(1).map((t, i) => t - at[i]);
  expect(gaps, `요청 간격 ${gaps.join(", ")}`).toHaveLength(want.length);
  gaps.forEach((g, i) => {
    expect(g, `${i + 1}번째 간격 ${g}`).toBeGreaterThanOrEqual(want[i] - 50);
    expect(g, `${i + 1}번째 간격 ${g}`).toBeLessThan(want[i] + 500);
  });
}
const N = PROGRESS_POLL_MS;
const S = PROGRESS_SLOW_POLL_MS;

test("useJobProgress: available:false 는 연속 3회까지 5초 간격(짧은 미리보기도 수치를 볼 기회), 그 뒤 15초(서버가 매번 base 점검을 돈다)", () => withFakeTimers(async () => {
  const urls = progressServer(() => ({ status: 200, body: RESP.unavailable }));
  const { result } = renderHook(() => useJobProgress("j1", "preview", true), { wrapper: stableWrapper() });
  await waitFor(() => expect(result.current.data).toEqual(RESP.unavailable));
  expect(new URL(urls[0]).searchParams.get("phase")).toBe("preview");
  await runFor(N + N + S + S + 1000);
  expectGaps(urls.at, [N, N, S, S]);                // 0·5·10초(헛걸음 1·2·3회) → 25·40초
}));

test("useJobProgress: 2~10초짜리 미리보기 -- 첫 응답이 available:false 여도 5초 뒤 다시 읽어 그 사이 생긴 스냅숏을 보인다", () => withFakeTimers(async () => {
  let n = 0;
  const urls = progressServer(() => (++n === 1 ? { status: 200, body: RESP.unavailable } : { status: 200, body: RESP.available_preview }));
  const { result } = renderHook(() => useJobProgress("j1", "preview", true), { wrapper: stableWrapper() });
  await waitFor(() => expect(result.current.data).toEqual(RESP.unavailable));
  await tick(N + 100);
  await waitFor(() => expect(result.current.data).toEqual(RESP.available_preview));
  expect(urls).toHaveLength(2);
}));

test("useJobProgress: 성공 한 번이면 헛걸음을 다시 센다 -- 헛걸음·헛걸음·성공 뒤 헛걸음이 다시 연속 3회가 되어야 15초", () => withFakeTimers(async () => {
  const seq = [RESP.unavailable, RESP.unavailable, RESP.available];      // 그 뒤로는 내내 available:false
  let n = 0;
  const urls = progressServer(() => ({ status: 200, body: seq[n++] ?? RESP.unavailable }));
  const { result } = renderHook(() => useJobProgress("j1", "execution", true), { wrapper: stableWrapper() });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  await runFor(5 * N + S + 1000);
  expectGaps(urls.at, [N, N, N, N, N, S]);          // 헛걸음 2 · 성공 · 헛걸음 1·2·3(6번째 응답) → 그 뒤 15초
}));

test("useJobProgress: 503 오류도 연속 3회부터 15초 간격이고, 회복하면 5초로 돌아온다", () => withFakeTimers(async () => {
  let n = 0;
  const urls = progressServer(() => (++n <= 3 ? errorReply("artifact_base_unavailable") : { status: 200, body: RESP.available }));
  const { result } = renderHook(() => useJobProgress("j1", "execution", true), { wrapper: stableWrapper() });
  await waitFor(() => expect(result.current.isError).toBe(true));
  await runFor(N + N + S + N + N + 1000);
  expectGaps(urls.at, [N, N, S, N, N]);             // 오류 1·2·3 → 15초 뒤 회복 → 5초
  await waitFor(() => expect(result.current.data).toEqual(RESP.available));
}));

test("useJobProgress: live false(enabled false)면 요청 0", async () => {
  const urls = progressServer(() => ({ status: 200, body: RESP.available }));
  const { result } = renderHook(() => useJobProgress("j1", "execution", false), { wrapper: stableWrapper() });
  await new Promise((r) => setTimeout(r, 20));
  expect(urls).toHaveLength(0);
  expect(result.current.fetchStatus).toBe("idle");
});

test("useJobProgress: 창을 숨기면 멈추고, 다시 보이면 곧바로 한 번 읽는다", () => withFakeTimers(async () => {
  const urls = progressServer(() => ({ status: 200, body: RESP.available }));
  const { result } = renderHook(() => useJobProgress("j1", "execution", true), { wrapper: stableWrapper() });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  act(() => { focusManager.setFocused(false); });
  await tick(PROGRESS_POLL_MS * 4);
  expect(urls).toHaveLength(1);
  act(() => { focusManager.setFocused(true); });
  await waitFor(() => expect(urls).toHaveLength(2));
}));
