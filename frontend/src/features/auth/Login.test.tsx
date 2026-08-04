import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { Login } from "./Login";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderLogin() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><Login /></MemoryRouter>
    </QueryClientProvider>,
  );
}

test("submits credentials and shows error on 401", async () => {
  server.use(http.post("/api/auth/login",
    () => HttpResponse.json({ detail: "invalid_credentials" }, { status: 401 })));
  renderLogin();
  await userEvent.type(screen.getByLabelText("사용자명"), "alice");
  await userEvent.type(screen.getByLabelText("비밀번호"), "bad");
  await userEvent.click(screen.getByRole("button", { name: "로그인" }));
  expect(await screen.findByText("사용자명 또는 비밀번호가 올바르지 않습니다")).toBeInTheDocument();
});

test("navigates to / on successful login", async () => {
  server.use(http.post("/api/auth/login",
    () => HttpResponse.json({ actor: "alice", role: "user" })));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/login"]}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<h1>홈</h1>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await userEvent.type(screen.getByLabelText("사용자명"), "alice");
  await userEvent.type(screen.getByLabelText("비밀번호"), "good");
  await userEvent.click(screen.getByRole("button", { name: "로그인" }));
  expect(await screen.findByRole("heading", { name: "홈" })).toBeInTheDocument();
});
