import { describe, it, expect, beforeAll, afterEach, afterAll } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { BuildsPage } from "./BuildsPage";

const BUILD = {
  build_id: "0123456789abcdef0123456789abcdef", repo_url: "u", git_ref: "main",
  commit_sha: "deadbeef", images: ["dms"], node_name: "dms-w1",
  state: "Succeeded", reason_code: null, tag: "b01234567",
  created_at: "2026-08-06T00:00:00Z", finished_at: "2026-08-06T00:10:00Z",
};

const server = setupServer(
  http.get("/api/admin/control-state", () =>
    HttpResponse.json({ maintenance: 0, drain: 0, reason: null,
                        build_node_name: "dms-w1", changed_by: "ops",
                        changed_at: "2026-08-06T00:00:00Z" })),
  http.get("/api/admin/builds", () => HttpResponse.json([BUILD])),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
};

describe("BuildsPage", () => {
  it("빌드 목록을 렌더한다", async () => {
    wrap(<BuildsPage />);
    expect(await screen.findByText("b01234567")).toBeInTheDocument();
    expect(screen.getByText("dms-w1")).toBeInTheDocument();
  });

  it("빌드 노드가 없으면 제출을 막고 안내한다", async () => {
    server.use(http.get("/api/admin/control-state", () =>
      HttpResponse.json({ maintenance: 0, drain: 0, reason: null,
                          build_node_name: null, changed_by: null, changed_at: null })));
    wrap(<BuildsPage />);
    expect(await screen.findByText(/빌드 노드가 지정되지 않았습니다/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "빌드 시작" })).toBeDisabled();
  });

  it("이미지를 하나도 고르지 않으면 제출 버튼이 비활성이다", async () => {
    wrap(<BuildsPage />);
    await screen.findByText("b01234567");
    await userEvent.click(screen.getByLabelText("dms"));   // 기본 체크를 끈다
    expect(screen.getByRole("button", { name: "빌드 시작" })).toBeDisabled();
  });

  it("제출하면 POST 하고 목록을 다시 읽는다", async () => {
    let posted: unknown = null;
    server.use(http.post("/api/admin/builds", async ({ request }) => {
      posted = await request.json();
      return HttpResponse.json({ build_id: "x", state: "Pending" }, { status: 202 });
    }));
    wrap(<BuildsPage />);
    await screen.findByText("b01234567");
    await userEvent.click(screen.getByRole("button", { name: "빌드 시작" }));
    await waitFor(() => expect(posted).toEqual({ git_ref: "main", images: ["dms"] }));
  });

  it("서버 오류를 한국어 메시지로 보여준다", async () => {
    server.use(http.post("/api/admin/builds", () =>
      HttpResponse.json({ detail: "build_in_progress" }, { status: 409 })));
    wrap(<BuildsPage />);
    await screen.findByText("b01234567");
    await userEvent.click(screen.getByRole("button", { name: "빌드 시작" }));
    expect(await screen.findByText("이미 진행 중인 빌드가 있습니다")).toBeInTheDocument();
  });

  it("목록이 배열이 아니어도 흰 화면이 되지 않는다", async () => {
    server.use(http.get("/api/admin/builds", () => HttpResponse.json({ oops: true })));
    wrap(<BuildsPage />);
    expect(await screen.findByRole("heading", { name: "빌드" })).toBeInTheDocument();
  });
});
