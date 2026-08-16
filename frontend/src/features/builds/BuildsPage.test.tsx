import { describe, it, expect, beforeAll, afterEach, afterAll } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { reasonText } from "../../lib/api";
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
  // DS Cloud 재설계: 의존성 안내는 상시 캡션이 아니라 **dms-agent 를 고른 사람에게만**
  // 나타나는 한 줄이다. 안 고른 사람에게는 아무 의미 없는 줄이라 밀도만 올린다.
  it("이미지 의존 관계는 dms-agent 를 골랐을 때만 한 줄로 알린다", async () => {
    wrap(<BuildsPage />);
    await screen.findByText("b01234567");
    expect(screen.queryByText(/dms-agent 는 dms·dms-mpifileutils 를 같은 태그로 FROM/))
      .not.toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("dms-agent"));
    await userEvent.click(screen.getByLabelText("dms-mpifileutils"));
    expect(screen.getByText(/dms-agent 는 dms·dms-mpifileutils 를 같은 태그로 FROM/))
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

  it("빌드 노드를 확인 박스에서 보여준다", async () => {
    wrap(<BuildsPage />);
    // control-state 가 도착하기 전에는 미설정 안내가 서 있다 -- 노드 이름을 기다려야
    // 확인 박스의 "설정됨" 경로를 실제로 지나간다.
    expect(await screen.findByText("dms-w1")).toBeInTheDocument();
    expect(screen.getByText(/빌드 노드/)).toBeInTheDocument();
  });

  it("빌드 노드가 없으면 컨트롤 상태 화면으로 가는 링크를 준다", async () => {
    server.use(http.get("/api/admin/control-state", () =>
      HttpResponse.json({ maintenance: 0, drain: 0, reason: null,
                          build_node_name: null, changed_by: null, changed_at: null })));
    wrap(<BuildsPage />);
    const link = await screen.findByRole("link", { name: /컨트롤 상태/ });
    expect(link).toHaveAttribute("href", "/admin/control");
  });

  it("확인 박스가 커밋 SHA 불가를 알린다", async () => {
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
    await userEvent.click(screen.getByRole("button", { name: "실패" }));
    expect(screen.getByText("bfail0001")).toBeInTheDocument();
    expect(screen.queryByText("bok000001")).not.toBeInTheDocument();
  });

  // 다른 화면(BatchDetail·JobViewer)은 전부 버튼 그룹이다 -- 이 화면만 select 였다.
  it("상태 필터는 select 가 아니라 버튼 그룹이고 누른 것을 aria-pressed 로 알린다", async () => {
    wrap(<BuildsPage />);
    await screen.findByText("b01234567");
    expect(screen.queryByRole("combobox", { name: "상태 필터" })).not.toBeInTheDocument();
    const group = screen.getByRole("group", { name: "상태 필터" });
    expect(group).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "전체" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "성공" }));
    expect(screen.getByRole("button", { name: "성공" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "전체" })).toHaveAttribute("aria-pressed", "false");
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

  // ── P4: DS Cloud 재설계 — "평소엔 최소, 원하면 클릭해서 전문" ────────────────
  it("빌드 절차는 평소 2줄이고, 안내 카드를 눌러야 전문이 팝업으로 열린다", async () => {
    wrap(<BuildsPage />);
    await screen.findByText("b01234567");
    // 평소 화면에는 요약만 -- 전문(배포 분리·프리플라이트 3검사)은 없다.
    expect(screen.getByText(/프리플라이트 → 빌드 → push/)).toBeInTheDocument();
    expect(screen.queryByText(/레지스트리 push 까지만/)).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /빌드 절차 안내/ }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/레지스트리 push 까지만/)).toBeInTheDocument();   // 배포는 별도
    expect(screen.getByText(/egress/)).toBeInTheDocument();                   // 프리플라이트 3검사
    expect(screen.getByText(/인터넷이 필요합니다/)).toBeInTheDocument();
  });

  it("목록의 사유는 한 줄로 자르고 전문은 title 에 담는다", async () => {
    server.use(http.get("/api/admin/builds", () => HttpResponse.json([
      { ...BUILD, state: "Failed", reason_code: "build_node_disk_low", finished_at: null },
    ])));
    wrap(<BuildsPage />);
    const cell = await screen.findByText(/디스크 여유가 부족/);
    // truncate = 한 줄 고정(행 높이가 사유 길이에 따라 들쭉날쭉해지지 않는다).
    expect(cell.className).toContain("truncate");
    expect(cell).toHaveAttribute("title", reasonText("build_node_disk_low"));
  });

  it("목록에서 commit·노드 열을 빼 상세로 민다", async () => {
    wrap(<BuildsPage />);
    await screen.findByText("b01234567");
    expect(screen.queryByRole("columnheader", { name: "commit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "노드" })).not.toBeInTheDocument();
    expect(screen.queryByText("deadbeef")).not.toBeInTheDocument();
    // 노드 이름은 확인 박스에만 남는다(표에서 매 행 반복하지 않는다).
    expect(screen.getAllByText("dms-w1")).toHaveLength(1);
    // e2e L2 하한(8셀)을 지키는 열 수는 유지한다.
    expect(screen.getAllByRole("columnheader")).toHaveLength(8);
  });

  it("취소는 폼을 기본값으로 되돌린다", async () => {
    wrap(<BuildsPage />);
    await screen.findByText("b01234567");
    await userEvent.clear(screen.getByLabelText("git ref"));
    await userEvent.type(screen.getByLabelText("git ref"), "feat/x");
    await userEvent.click(screen.getByLabelText("dms-agent"));
    await userEvent.click(screen.getByRole("button", { name: "취소" }));
    expect(screen.getByLabelText("git ref")).toHaveValue("main");
    expect(screen.getByLabelText("dms-agent")).not.toBeChecked();
    expect(screen.getByLabelText("dms")).toBeChecked();
  });
});
