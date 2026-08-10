import { render, screen } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse, delay } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { QueueSection, waitText } from "./QueueSection";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderQueue(body: Record<string, unknown>, delayMs = 0) {
  server.use(http.get("/api/admin/metrics/queue", async () => {
    if (delayMs) await delay(delayMs);
    return HttpResponse.json(body);
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><QueueSection /></QueryClientProvider>);
}

test("Open 배지·phase 카운트·대기 표를 그리고 Running은 표에서 뺀다", async () => {
  renderQueue({
    queue: { name: "dms-data", state: "Open" },
    podgroups: [
      { name: "dms-sync-abc-uid1", phase: "Pending", min_member: 3,
        created_at: "2026-08-10T00:00:00Z", wait_seconds: 125 },
      { name: "dms-scan-def-uid2", phase: "Running", min_member: 1,
        created_at: "2026-08-10T00:01:00Z", wait_seconds: 60 },
    ],
  });
  expect(await screen.findByText("Open")).toBeInTheDocument();
  expect(screen.getByText("Pending 1")).toBeInTheDocument();
  expect(screen.getByText("Running 1")).toBeInTheDocument();
  expect(screen.getByText("dms-sync-abc-uid1")).toBeInTheDocument();
  expect(screen.getByText("2분 5초")).toBeInTheDocument();   // wait_seconds 125
  // Running 은 카운트에는 있지만 「지금 큐에서 대기 중」 표에는 없다 --
  // 그 나이(now-creation)는 수명이지 대기가 아니다
  expect(screen.queryByText("dms-scan-def-uid2")).toBeNull();
});

test("null(알 수 없음)은 빈 큐로 렌더되지 않는다", async () => {
  // 403/CRD 부재 -- []로 접으면 권한 누락이 "큐가 한가함"으로 보인다(설계 §4)
  renderQueue({ queue: null, podgroups: null });
  expect(await screen.findByText(/알 수 없습니다/)).toBeInTheDocument();
  expect(screen.queryByText(/대기 중인 잡이 없습니다/)).toBeNull();
  expect(screen.queryByText("Open")).toBeNull();             // 상태 추측 금지(설계 §3)
  // 카운트 0 도 내지 않는다 -- "Pending 0"은 "아무것도 안 기다린다"는 주장이고,
  // 우리는 그걸 모른다. 모르는 축은 숫자를 만들지 않는다.
  expect(screen.queryByText(/Pending 0/)).toBeNull();
});

test("빈 배열(비었음)은 알 수 없음과 다르게 렌더된다", async () => {
  renderQueue({ queue: { name: "dms-data", state: "Closed" }, podgroups: [] });
  expect(await screen.findByText("Closed")).toBeInTheDocument();
  expect(screen.getByText(/대기 중인 잡이 없습니다/)).toBeInTheDocument();
  expect(screen.queryByText(/알 수 없습니다/)).toBeNull();
  // 읽었고 0 이었다 -- 그래서 0 을 낼 자격이 있다(위 null 테스트의 정확한 반대)
  expect(screen.getByText("Pending 0")).toBeInTheDocument();
});

test("queue 축만 null 이면 배지만 빠지고 대기 표는 그대로 뜬다", async () => {
  // 축별 독립 fail-soft(라우트 docstring): ClusterRole 누락은 queue 만 죽인다.
  // podgroups 는 읽었으므로 "알 수 없음"으로 통째 강등하면 정보를 버리는 것이다.
  renderQueue({
    queue: null,
    podgroups: [{ name: "dms-sync-ghi-uid3", phase: "Inqueue", min_member: 2,
                  created_at: "2026-08-09T23:00:00Z", wait_seconds: 3660 }],
  });
  expect(await screen.findByText("dms-sync-ghi-uid3")).toBeInTheDocument();
  expect(screen.getByText("Inqueue 1")).toBeInTheDocument();
  expect(screen.getByText("1시간 1분")).toBeInTheDocument();
  expect(screen.queryByText("Open")).toBeNull();             // 상태는 여전히 추측 금지
  expect(screen.queryByText(/알 수 없습니다/)).toBeNull();   // 대기 축은 알고 있다
});

test("podgroups 축만 null 이면 배지는 뜨지만 대기는 알 수 없음이다", async () => {
  // Role 누락은 podgroups 만 죽인다 -- 큐가 Open 이라고 해서 대기가 0 은 아니다.
  renderQueue({ queue: { name: "dms-data", state: "Open" }, podgroups: null });
  expect(await screen.findByText("Open")).toBeInTheDocument();
  expect(screen.getByText(/알 수 없습니다/)).toBeInTheDocument();
  expect(screen.queryByText(/대기 중인 잡이 없습니다/)).toBeNull();
  expect(screen.queryByText(/Pending 0/)).toBeNull();
});

test("로딩 중에는 알 수 없음(RBAC 확인) 문구를 내지 않는다", async () => {
  // 응답 전 data 는 undefined 라 pods===null 과 모양이 같다 -- 구분하지 않으면
  // 매 폴링 첫 페인트가 "권한을 확인하세요"로 깜빡이며 없는 원인을 가리킨다.
  renderQueue({ queue: { name: "dms-data", state: "Open" }, podgroups: [] }, 40);
  expect(await screen.findByText("불러오는 중…")).toBeInTheDocument();
  expect(screen.queryByText(/알 수 없습니다/)).toBeNull();
  expect(await screen.findByText(/대기 중인 잡이 없습니다/)).toBeInTheDocument();
});

test("waitText는 초를 사람이 읽는 단위로 접는다", () => {
  expect(waitText(null)).toBe("—");
  expect(waitText(42)).toBe("42초");
  expect(waitText(125)).toBe("2분 5초");
  expect(waitText(3660)).toBe("1시간 1분");
});
