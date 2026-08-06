import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect, vi } from "vitest";

// M5: 이 두 테스트는 원래 router.test.tsx에 있었고, RequestDetail이
// `transitions[transitions.length - 1]`에 무방어로 접근하는 것을 크래시 재료로
// 삼아 안쪽(AppShell) ErrorBoundary를 시험했다. M5에서 그 자리에 방어 코드를
// 넣으면(널 병합) 그 크래시 재료가 사라진다 -- 그래서 RequestDetail 자체를
// vi.mock으로 "던지는 컴포넌트"로 바꿔치기해, 경계 배선만 독립적으로 검증하도록
// 옮겼다. router.test.tsx와 분리한 이유는 outerBoundary 테스트와 같다: vi.mock은
// 파일 전체에 적용되므로 실제 RequestDetail을 쓰는 다른 라우팅 테스트와 같은
// 파일에 두면 서로 간섭한다.
vi.mock("../features/jobs/RequestDetail", () => ({
  RequestDetail: () => {
    throw new Error("boom");
  },
}));

import { AppRouter } from "./router";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}><AppRouter /></MemoryRouter>
    </QueryClientProvider>,
  );
}

test("기능 화면이 크래시해도 사이드바가 살아 있다", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  server.use(http.get("/api/auth/me", () => HttpResponse.json({ actor: "alice", role: "user" })));
  renderAt("/jobs/r1");
  expect(await screen.findByText("화면을 표시하지 못했습니다")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "내 작업" })).toBeInTheDocument();
});

// 이 테스트가 이 태스크의 요점이다 -- AppShell이 key 없이 ErrorBoundary를 마운트하면
// 모든 보호 라우트가 같은 컴포넌트 타입・같은 트리 위치라 한 번 에러 상태에 빠진 뒤
// 다른 화면으로 이동해도 React가 같은 인스턴스를 재사용해서 영원히 갇힌다.
// key={pathname}이 있어야 경로가 바뀔 때 경계가 스스로 초기화된다.
test("다른 경로로 이동하면 안쪽 경계가 스스로 풀린다", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "alice", role: "user" })),
    http.get("/api/user/requests", () => HttpResponse.json([])),
  );
  renderAt("/jobs/r1");
  expect(await screen.findByText("화면을 표시하지 못했습니다")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("link", { name: "내 작업" }));

  expect(await screen.findByRole("heading", { name: "내 작업" })).toBeInTheDocument();
  expect(screen.queryByText("화면을 표시하지 못했습니다")).not.toBeInTheDocument();
});
