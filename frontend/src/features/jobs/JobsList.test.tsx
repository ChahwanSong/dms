import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { JobsList } from "./JobsList";
import type { Me } from "../../lib/types";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const meAdmin: Me = { actor: "root", role: "admin" };
const meUser: Me = { actor: "alice", role: "user" };

function row(id: string, over: Record<string, unknown> = {}) {
  return {
    request_id: id, operation: "sync", state: "Succeeded", priority: "mid",
    requester_id: "alice", resource_key: "k", commit_order: Number(id.slice(1)) || 1,
    created_at: "2026-08-22T00:00:00Z", updated_at: "2026-08-22T00:01:00Z",
    payload: { source_storage: "s1", source: "a", destination_storage: "s1", destination: "b" },
    ...over,
  };
}

function wrap(me: Me = meAdmin) {
  server.use(http.get("/api/auth/me", () => HttpResponse.json(me)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter><JobsList /></MemoryRouter></QueryClientProvider>);
}

test("전체 작업: 요청자·대상 요약 등 풍부한 정보를 표시한다", async () => {
  server.use(http.get("/api/user/requests", () => HttpResponse.json([row("r1")])));
  wrap();
  expect(await screen.findByRole("heading", { name: "전체 작업" })).toBeInTheDocument();
  const r = (await screen.findByText("r1", { exact: false })).closest("tr")!;
  expect(within(r).getByText("alice")).toBeInTheDocument();          // 요청자 필수
  expect(within(r).getByText("Succeeded")).toBeInTheDocument();
  expect(within(r).getByText("s1:a → s1:b")).toBeInTheDocument();    // 대상 요약(pathSummary)
});

test("요청자 필터는 운영자에게만 보인다", async () => {
  server.use(http.get("/api/user/requests", () => HttpResponse.json([row("r1")])));
  const { unmount } = wrap(meUser);
  await screen.findByText("r1", { exact: false });
  expect(screen.queryByLabelText("요청자 필터")).not.toBeInTheDocument();
  expect(screen.getByLabelText("연산 필터")).toBeInTheDocument();     // 연산·상태는 공용
  unmount();
  wrap(meAdmin);
  expect(await screen.findByLabelText("요청자 필터")).toBeInTheDocument();
});

test("필터를 바꾸면 서버 쿼리에 operation·state·requester 가 실린다", async () => {
  const urls: string[] = [];
  server.use(http.get("/api/user/requests", ({ request }) => {
    urls.push(request.url); return HttpResponse.json([row("r1")]);
  }));
  wrap(meAdmin);
  await screen.findByText("r1", { exact: false });
  await userEvent.selectOptions(screen.getByLabelText("연산 필터"), "rm");
  await userEvent.selectOptions(screen.getByLabelText("상태 필터"), "Failed");
  await userEvent.type(screen.getByLabelText("요청자 필터"), "bob");
  // 마지막 요청이 세 필터를 모두 반영한다.
  await screen.findByText(/조건에 맞는 작업이 없습니다|r1/);
  const last = urls[urls.length - 1];
  expect(last).toContain("operation=rm");
  expect(last).toContain("state=Failed");
  expect(last).toContain("requester=bob");
});

test("빈 결과는 전용 문구를 보인다", async () => {
  server.use(http.get("/api/user/requests", () => HttpResponse.json([])));
  wrap();
  expect(await screen.findByText("조건에 맞는 작업이 없습니다")).toBeInTheDocument();
});
