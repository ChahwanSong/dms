// 복구 감시(useUsageBaseRecovery)의 낡은 health 무시(2026-11-23 리뷰): 지난 장애가 끝날 때 받은 health {ok:true} 가 캐시에
// 남아 있으면, 새 장애가 시작되는 첫 렌더에서 그 낡은 ok 를 「바뀜」으로 읽어 사용량 쿼리를 헛되이 한 번 다시 불렀다. 사유가
// 나타난 시각보다 오래된 health 는 보지 않는다.
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest";
import { useUsageBaseRecovery } from "./useUsage";

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

test("새 장애 시작 때 캐시에 남은 지난 장애의 ok:true 로는 무효화하지 않는다 -- 새로 받은 health 만 본다", async () => {
  let healthCalls = 0;
  server.use(http.get("/api/admin/artifact-base/health", () => {
    healthCalls += 1;
    return HttpResponse.json({ ok: false, reason: "artifact_base_jobs_missing", checked_at: null,
                               instance: "dms-api-1", node_name: "n1" });
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // 지난 장애의 끝에 받은 health(10분 전) + 지금 막 사유를 들고 온 사용량 응답
  qc.setQueryData(["artifact-base-health"], { ok: true, reason: null }, { updatedAt: Date.now() - 600_000 });
  qc.setQueryData(["usage-history", "s1", "t", 30],
                  { storage_name: "s1", target: "t", points: [], skipped_unreadable: 2,
                    unreadable_reason: "artifact_base_unavailable", window_limit: 30, window_full: false });
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  const wrapper = ({ children }: { children: React.ReactNode }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  const { result } = renderHook(() => useUsageBaseRecovery(), { wrapper });
  expect(result.current).toBe(true);
  await waitFor(() => expect(healthCalls).toBe(1));
  await waitFor(() => expect(qc.getQueryData<{ ok: boolean }>(["artifact-base-health"])?.ok).toBe(false));
  expect(invalidate).not.toHaveBeenCalled();
});
