import { describe, it, expect, beforeAll, afterEach, afterAll } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
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
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
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

  it("T6: 행의 images가 null이어도 흰 화면이 되지 않는다", async () => {
    server.use(http.get("/api/admin/builds", () =>
      HttpResponse.json([{ ...BUILD, images: null }])));
    wrap(<BuildsPage />);
    expect(await screen.findByText("b01234567")).toBeInTheDocument();
  });

  it("목록이 배열이 아니어도 흰 화면이 되지 않는다", async () => {
    server.use(http.get("/api/admin/builds", () => HttpResponse.json({ oops: true })));
    wrap(<BuildsPage />);
    expect(await screen.findByRole("heading", { name: "빌드" })).toBeInTheDocument();
  });

  // ── P1: 목록에서 바로 읽히는 것들 ───────────────────────────────────────────
  it("상태를 pill 로 보여준다 — 실패는 bad, 진행 중은 busy(전부 같은 검은 글씨가 아니다)", async () => {
    server.use(http.get("/api/admin/builds", () => HttpResponse.json([
      { ...BUILD, build_id: "f".repeat(32), tag: "bfailed01", state: "Failed" },
      { ...BUILD, build_id: "r".repeat(32), tag: "brunning1", state: "Running", finished_at: null },
    ])));
    wrap(<BuildsPage />);
    expect((await screen.findByText("Failed")).className).toContain("text-bad");
    expect(screen.getByText("Running").className).toContain("text-busy");
  });

  it("실패 사유를 목록에서 바로 보여준다(상세로 들어가지 않아도)", async () => {
    server.use(http.get("/api/admin/builds", () => HttpResponse.json([
      { ...BUILD, state: "Failed", reason_code: "build_node_disk_low", finished_at: null },
    ])));
    wrap(<BuildsPage />);
    expect(await screen.findByText(/디스크 여유가 부족/)).toBeInTheDocument();
    // 원시 코드는 노출하지 않는다(reasonText 경로).
    expect(screen.queryByText("build_node_disk_low")).not.toBeInTheDocument();
  });

  it("사유가 없으면 —(모름)이지 빈칸이 아니다", async () => {
    wrap(<BuildsPage />);
    await screen.findByText("b01234567");
    // 이 행은 commit·노드·태그·경과가 전부 있으므로 —는 사유 셀 하나뿐이다.
    expect(screen.getAllByText("—")).toHaveLength(1);
  });

  it("진행 중 빌드는 경과 시간을 보여준다", async () => {
    const started = new Date(Date.now() - 192_500).toISOString();  // 3분 12초 전
    server.use(http.get("/api/admin/builds", () => HttpResponse.json([
      { ...BUILD, state: "Running", created_at: started, finished_at: null },
    ])));
    wrap(<BuildsPage />);
    expect(await screen.findByText("3분 12초 경과")).toBeInTheDocument();
  });

  it("종단 빌드는 소요 시간을 보여준다 — finished_at 이 없으면 지어내지 않는다", async () => {
    server.use(http.get("/api/admin/builds", () => HttpResponse.json([
      { ...BUILD, tag: "bdone0001" },
      { ...BUILD, build_id: "e".repeat(32), tag: "bnofin001", state: "Failed", finished_at: null },
    ])));
    wrap(<BuildsPage />);
    expect(await screen.findByText("10분 0초 소요")).toBeInTheDocument();
    // finished_at 이 null 인 종단 행은 소요를 지어내지 않는다 -- 소요 표기는 하나뿐.
    expect(screen.getAllByText(/소요/)).toHaveLength(1);
  });

  it("Pending 은 적합성 확인(프리플라이트) 중임을 알린다", async () => {
    server.use(http.get("/api/admin/builds", () => HttpResponse.json([
      { ...BUILD, state: "Pending", commit_sha: null, finished_at: null },
    ])));
    wrap(<BuildsPage />);
    expect(await screen.findByText("적합성 확인 중")).toBeInTheDocument();
  });

  it("태그 셀은 클릭 한 번에 전체 선택되는 등폭 텍스트다(clipboard API 금지)", async () => {
    wrap(<BuildsPage />);
    const tag = await screen.findByText("b01234567");
    expect(tag.className).toContain("select-all");
    expect(tag.className).toContain("font-mono");
  });

  // ── P2: 제출 전에 미리 막아 주는 것들 ───────────────────────────────────────
  it("이미지 의존 관계를 캡션으로 알린다", async () => {
    wrap(<BuildsPage />);
    expect(await screen.findByText(/dms-agent 는 dms·dms-mpifileutils 를 같은 태그로 FROM/))
      .toBeInTheDocument();
  });

  it("dms-agent 만 고르면 경고하되 제출은 막지 않는다(레지스트리에 이미 있을 수 있다)", async () => {
    wrap(<BuildsPage />);
    await screen.findByText("b01234567");
    await userEvent.click(screen.getByLabelText("dms"));        // 기본 체크 해제
    await userEvent.click(screen.getByLabelText("dms-agent"));
    expect(screen.getByText(/이번 빌드에 없습니다/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "빌드 시작" })).toBeEnabled();
  });

  it("dms-agent 를 의존 이미지와 함께 고르면 경고가 사라진다", async () => {
    wrap(<BuildsPage />);
    await screen.findByText("b01234567");
    await userEvent.click(screen.getByLabelText("dms-agent"));
    await userEvent.click(screen.getByLabelText("dms-mpifileutils"));
    expect(screen.queryByText(/이번 빌드에 없습니다/)).not.toBeInTheDocument();
  });

  it("빌드 노드가 지정돼 있으면 폼에서 보여준다", async () => {
    wrap(<BuildsPage />);
    expect(await screen.findByText(/빌드 노드 dms-w1/)).toBeInTheDocument();
  });

  it("빌드 노드가 없으면 컨트롤 상태 화면으로 가는 링크를 준다", async () => {
    server.use(http.get("/api/admin/control-state", () =>
      HttpResponse.json({ maintenance: 0, drain: 0, reason: null,
                          build_node_name: null, changed_by: null, changed_at: null })));
    wrap(<BuildsPage />);
    const link = await screen.findByRole("link", { name: /컨트롤 상태/ });
    expect(link).toHaveAttribute("href", "/admin/control");
  });

  it("git ref 캡션이 커밋 SHA 불가를 알린다", async () => {
    wrap(<BuildsPage />);
    expect(await screen.findByText(/커밋 SHA 불가/)).toBeInTheDocument();
  });

  it("최근 빌드에서 쓴 ref 를 빠른 선택 버튼으로 준다(최대 3개, 중복 제거)", async () => {
    server.use(http.get("/api/admin/builds", () => HttpResponse.json([
      { ...BUILD, build_id: "1".repeat(32), tag: "b11111111", git_ref: "feat/x" },
      { ...BUILD, build_id: "2".repeat(32), tag: "b22222222", git_ref: "main" },
      { ...BUILD, build_id: "3".repeat(32), tag: "b33333333", git_ref: "feat/x" },
      { ...BUILD, build_id: "4".repeat(32), tag: "b44444444", git_ref: "v1.2.3" },
      { ...BUILD, build_id: "5".repeat(32), tag: "b55555555", git_ref: "old" },
    ])));
    wrap(<BuildsPage />);
    await screen.findByText("b11111111");
    expect(screen.queryByRole("button", { name: "old" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "v1.2.3" }));
    expect(screen.getByLabelText("git ref")).toHaveValue("v1.2.3");
  });

  it("프리플라이트가 먼저 돈다는 것을 폼에서 알린다", async () => {
    wrap(<BuildsPage />);
    expect(await screen.findByText(/적합성 프리플라이트/)).toBeInTheDocument();
  });

  // ── P3: 목록 운영 ───────────────────────────────────────────────────────────
  it("상태 필터로 실패만 볼 수 있다", async () => {
    server.use(http.get("/api/admin/builds", () => HttpResponse.json([
      { ...BUILD, build_id: "a".repeat(32), tag: "bok000001" },
      { ...BUILD, build_id: "b".repeat(32), tag: "bfail0001", state: "Failed",
        reason_code: "build_node_disk_low" },
    ])));
    wrap(<BuildsPage />);
    await screen.findByText("bok000001");
    await userEvent.selectOptions(screen.getByLabelText("상태 필터"), "failed");
    expect(screen.getByText("bfail0001")).toBeInTheDocument();
    expect(screen.queryByText("bok000001")).not.toBeInTheDocument();
  });

  it("20건 넘으면 페이지를 나눈다", async () => {
    const rows = Array.from({ length: 25 }, (_, i) => ({
      ...BUILD, build_id: String(i).padStart(32, "0"),
      tag: `b${String(i).padStart(8, "0")}`,
    }));
    server.use(http.get("/api/admin/builds", () => HttpResponse.json(rows)));
    wrap(<BuildsPage />);
    await screen.findByText("b00000000");
    expect(screen.queryByText("b00000020")).not.toBeInTheDocument();
    expect(screen.getByText("1 / 2 페이지")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "다음" }));
    expect(screen.getByText("b00000020")).toBeInTheDocument();
  });
});
