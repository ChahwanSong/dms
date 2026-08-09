import { describe, it, expect, beforeAll, afterEach, afterAll } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { ReleasesPage } from "./ReleasesPage";

const TARGETS = {
  registry_ok: true,
  targets: [
    { component: "dms-agent", kind: "DaemonSet", workload: "dms-agent",
      container: "agent", repository: "dms-agent",
      current_image: "pkg-01:5000/dms-agent:dev5", tags: ["dev5", "dev6"] },
    { component: "dms-api", kind: "Deployment", workload: "dms-api",
      container: "api", repository: "dms",
      current_image: "pkg-01:5000/dms:d22", tags: ["d22", "d23"] },
    { component: "dms-controller", kind: "Deployment", workload: "dms-controller",
      container: "controller", repository: "dms",
      current_image: "pkg-01:5000/dms:d22", tags: ["d22", "d23"] },
  ],
};
const HISTORY = {
  current: {
    "dms-api": { id: 1, component: "dms-api", image: "pkg-01:5000/dms:d22",
                 tag: "d22", digest: null, state: "Applied", reason_code: null,
                 actor: "ops", applied_at: "2026-08-06T00:00:00Z" },
  },
  history: [
    { id: 1, component: "dms-api", image: "pkg-01:5000/dms:d22", tag: "d22",
      digest: null, state: "Applied", reason_code: null, actor: "ops",
      applied_at: "2026-08-06T00:00:00Z" },
    { id: 2, component: "dms-agent", image: "pkg-01:5000/dms-agent:dev4",
      tag: "dev4", digest: null, state: "Failed",
      reason_code: "rollout_timeout", actor: "ops",
      applied_at: "2026-08-05T00:00:00Z" },
  ],
};

const server = setupServer(
  http.get("/api/admin/releases/targets", () => HttpResponse.json(TARGETS)),
  http.get("/api/admin/releases", () => HttpResponse.json(HISTORY)),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
};

describe("ReleasesPage", () => {
  it("세 컴포넌트와 현재 이미지를 렌더한다", async () => {
    wrap(<ReleasesPage />);
    expect(await screen.findByRole("heading", { name: "릴리스" })).toBeInTheDocument();
    expect(screen.getByText("pkg-01:5000/dms-agent:dev5")).toBeInTheDocument();
    expect(screen.getByLabelText("dms-controller")).toBeInTheDocument();
  });

  it("컨트롤러 재시작 경고를 항상 보여준다", async () => {
    wrap(<ReleasesPage />);
    expect(await screen.findByText(/컨트롤러가 재시작되어 롤아웃 추적이 잠시 끊깁니다/))
      .toBeInTheDocument();
  });

  it("선택이 없으면 제출이 비활성이고, 선택한 것만 한 배치로 보낸다", async () => {
    let posted: unknown = null;
    server.use(http.post("/api/admin/releases", async ({ request }) => {
      posted = await request.json();
      return HttpResponse.json({ items: [] }, { status: 202 });
    }));
    wrap(<ReleasesPage />);
    await screen.findByRole("heading", { name: "릴리스" });
    expect(screen.getByRole("button", { name: "롤아웃 시작" })).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("dms-agent"), "dev6");
    await userEvent.click(screen.getByRole("button", { name: "롤아웃 시작" }));
    await waitFor(() => expect(posted).toEqual({
      items: [{ component: "dms-agent", tag: "dev6" }] }));
  });

  it("서버 거절을 한국어로 보여준다", async () => {
    server.use(http.post("/api/admin/releases", () =>
      HttpResponse.json({ detail: "same_tag" }, { status: 422 })));
    wrap(<ReleasesPage />);
    await screen.findByRole("heading", { name: "릴리스" });
    await userEvent.selectOptions(screen.getByLabelText("dms-agent"), "dev5");
    await userEvent.click(screen.getByRole("button", { name: "롤아웃 시작" }));
    expect(await screen.findByText(/현재 태그와 같습니다/)).toBeInTheDocument();
  });

  it("이력의 사유 코드를 번역한다", async () => {
    wrap(<ReleasesPage />);
    expect(await screen.findByText("롤아웃이 제한 시간을 넘겨 실패했습니다"))
      .toBeInTheDocument();
  });

  it("진행 중이면 배지를 보여준다", async () => {
    server.use(http.get("/api/admin/releases", () => HttpResponse.json({
      current: {},
      history: [{ id: 3, component: "dms-agent", image: "i", tag: "t",
                  digest: null, state: "Applying", reason_code: null,
                  actor: "ops", applied_at: "2026-08-06T01:00:00Z" }] })));
    wrap(<ReleasesPage />);
    expect(await screen.findByText("진행 중")).toBeInTheDocument();
  });

  it("레지스트리가 죽어도 화면이 산다", async () => {
    server.use(http.get("/api/admin/releases/targets", () =>
      HttpResponse.json({ registry_ok: false,
                          targets: TARGETS.targets.map(t => ({ ...t, tags: [] })) })));
    wrap(<ReleasesPage />);
    expect(await screen.findByText(/레지스트리에 연결할 수 없어/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "릴리스" })).toBeInTheDocument();
  });

  it("응답이 배열이 아니어도 흰 화면이 되지 않는다", async () => {
    server.use(
      http.get("/api/admin/releases/targets", () => HttpResponse.json({ oops: 1 })),
      http.get("/api/admin/releases", () => HttpResponse.json(null)),
    );
    wrap(<ReleasesPage />);
    expect(await screen.findByRole("heading", { name: "릴리스" })).toBeInTheDocument();
  });
});
