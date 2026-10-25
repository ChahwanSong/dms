import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, beforeEach, expect, test } from "vitest";
import type { ReactNode } from "react";
import { forgetTransportKey } from "../../lib/passwordTransport";
import { makeServerKey, openSealed, transportKeyHandler, type TestServerKey } from "../../test/transportKey";
import {
  K8S_TOKEN_PURPOSE, OVERVIEW_POLL_MS, OVERVIEW_SYNCING_POLL_MS, overviewPollMs, runsUrl, useSaveCluster, useSyncCluster,
  volumesUrl,
} from "./useK8s";
import { CLUSTER_OK, overview } from "./k8sFixtures";

// 훅의 와이어 계약: 폴링 간격(평소 10초·동기화 중 3초), 쿼리 문자열(빈 값은 싣지 않음·limit), 토큰 봉인 용도·주제,
// 이름이 경로에 들어갈 때의 인코딩(「:sync」 접미).

let serverKey: TestServerKey;
const server = setupServer();
beforeAll(async () => { server.listen(); serverKey = await makeServerKey(); });
beforeEach(() => { forgetTransportKey(); server.use(transportKeyHandler(serverKey)); });
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

test("개요 폴링: 동기화 중인 클러스터가 있으면 3초, 아니면 10초", () => {
  expect(overviewPollMs(undefined)).toBe(OVERVIEW_POLL_MS);
  expect(overviewPollMs(overview())).toBe(10_000);
  expect(overviewPollMs(overview({ clusters: [{ ...CLUSTER_OK, status: "syncing" }] }))).toBe(OVERVIEW_SYNCING_POLL_MS);
  expect(OVERVIEW_SYNCING_POLL_MS).toBe(3_000);
});

test("volumesUrl·runsUrl: 빈 값은 싣지 않고 limit 은 늘", () => {
  expect(volumesUrl({})).toBe("/api/admin/k8s/volumes?limit=200");
  expect(volumesUrl({ cluster: "dmsk8s", namespace: "", q: "모델 a", user: "alice" }))
    .toBe("/api/admin/k8s/volumes?cluster=dmsk8s&q=%EB%AA%A8%EB%8D%B8+a&user=alice&limit=200");
  expect(runsUrl({})).toBe("/api/admin/k8s/runs?limit=50");
  expect(runsUrl({ cluster: "c", node: "n" }, 42)).toBe("/api/admin/k8s/runs?cluster=c&node=n&before=42&limit=50");
});

test("useSaveCluster: 토큰이 있으면 token_enc 봉인(용도 k8s_cluster_token · 주제 = 클러스터 이름), 없으면 평문 필드 없음", async () => {
  const bodies: Record<string, unknown>[] = [];
  server.use(
    http.post("/api/admin/k8s/clusters", async ({ request }) => {
      bodies.push(await request.json() as Record<string, unknown>);
      return HttpResponse.json(CLUSTER_OK);
    }),
    http.put("/api/admin/k8s/clusters/:name", async ({ request }) => {
      bodies.push(await request.json() as Record<string, unknown>);
      return HttpResponse.json(CLUSTER_OK);
    }),
  );
  const { result } = renderHook(() => useSaveCluster(), { wrapper: wrapper() });
  const body = { name: "dmsk8s", api_server: "https://10.10.10.20:6443", tls_server_name: null,
                 insecure_skip_tls_verify: false, enabled: true };
  await result.current.mutateAsync({ mode: "create", body, token: "  tok-1  " });
  expect(K8S_TOKEN_PURPOSE).toBe("k8s_cluster_token");
  expect(await openSealed(serverKey, bodies[0].token_enc as never, "k8s_cluster_token", "dmsk8s")).toBe("tok-1");
  expect(JSON.stringify(bodies[0])).not.toContain("tok-1");
  await result.current.mutateAsync({ mode: "edit", original: "dmsk8s", body, token: "   " });
  expect("token_enc" in bodies[1]).toBe(false);
  expect("token" in bodies[1]).toBe(false);
});

test("useSyncCluster: 이름을 인코딩하고 「:sync」 접미를 붙인다", async () => {
  const paths: string[] = [];
  server.use(http.post(/\/api\/admin\/k8s\/clusters\/.+:sync$/, ({ request }) => {
    paths.push(new URL(request.url).pathname);
    return HttpResponse.json({});
  }));
  const { result } = renderHook(() => useSyncCluster(), { wrapper: wrapper() });
  result.current.mutate("prod a");
  await waitFor(() => expect(paths).toEqual(["/api/admin/k8s/clusters/prod%20a:sync"]));
});
