import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect, vi } from "vitest";

// I4: router.test.tsx의 기존 두 ErrorBoundary 테스트는 둘 다 /jobs/r1(RequireRole +
// AppShell을 거치는 보호 라우트)을 크래시시킨다 -- 즉 AppShell의 "안쪽" 경계만
// 검증한다. router.tsx의 "바깥" 경계(AuthProvider 안, <Routes> 바로 밖)는 /login과
// "/"(Home)처럼 AppShell 밖에 있는 라우트에서만 실제로 시험된다. 이 파일은 그
// 바깥 경계 전용이다 -- router.test.tsx에 같이 두지 않고 별도 파일로 분리한 이유는
// Login을 던지는 컴포넌트로 vi.mock 교체해야 하는데, vi.mock은 파일 전체에 적용돼
// router.test.tsx의 "unauthenticated shows login" 등 실제 Login을 렌더하는 기존
// 테스트를 깨기 때문이다.
vi.mock("../features/auth/Login", () => ({
  Login: () => {
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

test("바깥 ErrorBoundary가 AppShell 밖(/login) 크래시를 잡는다", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  renderAt("/login");
  expect(await screen.findByText("화면을 표시하지 못했습니다")).toBeInTheDocument();
  // 바깥 경계에는 AppShell(사이드바)이 없다 -- 안쪽 경계와 구분되는 지점이다.
  expect(screen.queryByRole("link", { name: "내 작업" })).not.toBeInTheDocument();
});

// M6: 바깥 경계에 fallback UI 안에는 내비게이션(사이드바)이 없으므로 "다시 시도"가
// 유일한 버튼이다. 그런데 실제 브라우저에서는 주소창 편집이나 뒤로가기로 location이
// 바뀔 수 있다 -- key가 없으면 ErrorBoundary는 한 번 갇힌 뒤 location이 바뀌어도
// state.error가 그대로라 계속 같은 폴백만 보여준다("다시 시도"를 눌러야만 그 시점의
// 현재 children을 다시 평가한다). 여기서는 앱 내부 링크가 없는 조건을 그대로
// 재현하기 위해 테스트 트리 바깥에 useNavigate를 쓰는 버튼을 하나 심어 location만
// 바꾸고, "다시 시도" 클릭 없이도 경계가 스스로 풀리는지 확인한다 -- 안쪽 경계의
// key={pathname}과 대칭이다.
function GoTo({ to }: { to: string }) {
  const nav = useNavigate();
  return <button onClick={() => nav(to)}>이동</button>;
}

function renderWithExternalNav(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <GoTo to="/" />
        <AppRouter />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("경로가 바뀌면 바깥 경계도 '다시 시도' 없이 스스로 풀린다", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "alice", role: "user" })),
    http.get("/api/user/requests", () => HttpResponse.json([])),
  );
  renderWithExternalNav("/login");
  expect(await screen.findByText("화면을 표시하지 못했습니다")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "이동" }));

  expect(await screen.findByRole("heading", { name: "내 작업" })).toBeInTheDocument();
  expect(screen.queryByText("화면을 표시하지 못했습니다")).not.toBeInTheDocument();
});
