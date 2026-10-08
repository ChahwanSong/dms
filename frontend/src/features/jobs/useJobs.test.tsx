import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { DELETE_REQUESTS_INVALIDATES, useDeleteRequests, usePurgeStatus } from "./useJobs";

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const RID = "a".repeat(32), JID = "b".repeat(32), OTHER = "c".repeat(32), OTHER_JOB = "d".repeat(32);

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidated: unknown[] = [];
  const orig = qc.invalidateQueries.bind(qc);
  qc.invalidateQueries = ((opts: { queryKey: unknown[] }) => {
    invalidated.push(opts.queryKey);
    return orig(opts as never);
  }) as never;
  const wrapper = ({ children }: { children: React.ReactNode }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  return { qc, invalidated, wrapper };
}

test("useDeleteRequests: POST {request_ids} — 지운 요청·잡의 캐시는 제거, 이웃은 남기고, 목록·사용량·통계·감사를 무효화", async () => {
  let body: unknown = null;
  server.use(http.post("/api/admin/requests:delete", async ({ request }) => {
    body = await request.json();
    return HttpResponse.json({ deleted: [{ request_id: RID, job_ids: [JID] }],
                               skipped: [{ request_id: OTHER, reason: "request_job_active" }], purge_pending: 1 });
  }));
  const { qc, invalidated, wrapper } = setup();
  // 지울 요청·잡의 캐시(상세·잡 목록·scan 통계·아티팩트 목록·파일·로그)와 남을 이웃의 캐시.
  const gone = [["request", RID], ["request", RID, "jobs"], ["request-scan-stats", RID],
                ["artifacts", JID], ["artifact", JID, "preview", "stdout.log"], ["joblogs", JID, "execution"]];
  const kept = [["request", OTHER], ["request", OTHER, "jobs"], ["artifacts", OTHER_JOB], ["joblogs", OTHER_JOB, "preview"]];
  for (const k of [...gone, ...kept]) qc.setQueryData(k, { x: 1 });
  const { result } = renderHook(() => useDeleteRequests(), { wrapper });
  result.current.mutate([RID, OTHER]);
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(body).toEqual({ request_ids: [RID, OTHER] });
  for (const k of gone) expect(qc.getQueryData(k), JSON.stringify(k)).toBeUndefined();
  for (const k of kept) expect(qc.getQueryData(k), JSON.stringify(k)).toEqual({ x: 1 });
  expect(invalidated).toEqual(DELETE_REQUESTS_INVALIDATES.map((k) => [...k]));
  for (const k of [["requests"], ["request-purges"], ["usage-targets"], ["usage-scan-storages"], ["usage-history"],
                   ["metrics", "jobs"], ["audit"]]) {
    expect(invalidated).toContainEqual(k);
  }
  expect(result.current.data?.skipped).toEqual([{ request_id: OTHER, reason: "request_job_active" }]);
});

test("useDeleteRequests: 전체 실패(403)는 isError — 캐시는 지우지 않고 목록은 다시 맞춘다", async () => {
  server.use(http.post("/api/admin/requests:delete",
    () => HttpResponse.json({ detail: "admin_session_required" }, { status: 403 })));
  const { qc, invalidated, wrapper } = setup();
  qc.setQueryData(["request", RID], { x: 1 });
  const { result } = renderHook(() => useDeleteRequests(), { wrapper });
  result.current.mutate([RID]);
  await waitFor(() => expect(result.current.isError).toBe(true));
  expect(qc.getQueryData(["request", RID])).toEqual({ x: 1 });
  expect(invalidated).toContainEqual(["requests"]);
});

test("usePurgeStatus: enabled=false 면 부르지 않는다(일반 사용자 403 방지), true 면 부른다", async () => {
  let calls = 0;
  server.use(http.get("/api/admin/request-purges", () => {
    calls += 1;
    return HttpResponse.json({ pending: 0, stalled: 0, oldest_requested_at: null, items: [] });
  }));
  const off = setup();
  renderHook(() => usePurgeStatus(false), { wrapper: off.wrapper });
  await new Promise((r) => setTimeout(r, 30));
  expect(calls).toBe(0);
  const on = setup();
  const { result } = renderHook(() => usePurgeStatus(true), { wrapper: on.wrapper });
  await waitFor(() => expect(result.current.data?.pending).toBe(0));
  expect(calls).toBe(1);
});
