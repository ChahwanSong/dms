import { describe, it, expect, beforeAll, afterEach, afterAll } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { reasonText } from "../../lib/api";
import { BuildHistory } from "./BuildHistory";

const BUILD = {
  build_id: "0123456789abcdef0123456789abcdef", repo_url: "u", git_ref: "main",
  commit_sha: "deadbeef", images: ["dms"], node_name: "dms-w1",
  state: "Succeeded", reason_code: null, tag: "b01234567",
  created_at: "2026-08-06T00:00:00Z", finished_at: "2026-08-06T00:10:00Z",
};

const server = setupServer(
  http.get("/api/admin/builds", () => HttpResponse.json([BUILD])),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const wrap = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/admin/builds/history"]}><BuildHistory /></MemoryRouter>
    </QueryClientProvider>,
  );
};

describe("BuildHistory — 빌드 이력", () => {
  it("빌드 목록을 렌더한다", async () => {
    wrap();
    expect(await screen.findByText("b01234567")).toBeInTheDocument();
    expect(screen.getByText("main")).toBeInTheDocument();
  });

  it("T6: 행의 images가 null이어도 흰 화면이 되지 않는다", async () => {
    server.use(http.get("/api/admin/builds", () =>
      HttpResponse.json([{ ...BUILD, images: null }])));
    wrap();
    expect(await screen.findByText("b01234567")).toBeInTheDocument();
  });

  it("목록이 배열이 아니어도 흰 화면이 되지 않는다", async () => {
    server.use(http.get("/api/admin/builds", () => HttpResponse.json({ oops: true })));
    wrap();
    expect(await screen.findByRole("heading", { name: "빌드 이력" })).toBeInTheDocument();
  });

  it("조회 오류를 한국어 메시지로 보여준다", async () => {
    server.use(http.get("/api/admin/builds", () =>
      HttpResponse.json({ detail: "admin_required" }, { status: 403 })));
    wrap();
    expect(await screen.findByText("관리자 권한이 필요합니다")).toBeInTheDocument();
  });

  // ── P1: 목록에서 바로 읽히는 것들 ───────────────────────────────────────────
  it("상태를 pill 로 보여준다 — 실패는 bad, 진행 중은 busy(전부 같은 검은 글씨가 아니다)", async () => {
    server.use(http.get("/api/admin/builds", () => HttpResponse.json([
      { ...BUILD, build_id: "f".repeat(32), tag: "bfailed01", state: "Failed" },
      { ...BUILD, build_id: "r".repeat(32), tag: "brunning1", state: "Running", finished_at: null },
    ])));
    wrap();
    expect((await screen.findByText("Failed")).className).toContain("text-bad");
    expect(screen.getByText("Running").className).toContain("text-busy");
  });

  it("실패 사유를 목록에서 바로 보여준다(상세로 들어가지 않아도)", async () => {
    server.use(http.get("/api/admin/builds", () => HttpResponse.json([
      { ...BUILD, state: "Failed", reason_code: "build_node_disk_low", finished_at: null },
    ])));
    wrap();
    expect(await screen.findByText(/디스크 여유가 부족/)).toBeInTheDocument();
    // 원시 코드는 노출하지 않는다(reasonText 경로).
    expect(screen.queryByText("build_node_disk_low")).not.toBeInTheDocument();
  });

  it("사유가 없으면 —(모름)이지 빈칸이 아니다", async () => {
    wrap();
    await screen.findByText("b01234567");
    // 이 행은 태그·경과가 전부 있으므로 —는 사유 셀 하나뿐이다.
    expect(screen.getAllByText("—")).toHaveLength(1);
  });

  it("진행 중 빌드는 경과 시간을 보여준다", async () => {
    const started = new Date(Date.now() - 192_500).toISOString();  // 3분 12초 전
    server.use(http.get("/api/admin/builds", () => HttpResponse.json([
      { ...BUILD, state: "Running", created_at: started, finished_at: null },
    ])));
    wrap();
    expect(await screen.findByText("3분 12초 경과")).toBeInTheDocument();
  });

  it("종단 빌드는 소요 시간을 보여준다 — finished_at 이 없으면 지어내지 않는다", async () => {
    server.use(http.get("/api/admin/builds", () => HttpResponse.json([
      { ...BUILD, tag: "bdone0001" },
      { ...BUILD, build_id: "e".repeat(32), tag: "bnofin001", state: "Failed", finished_at: null },
    ])));
    wrap();
    expect(await screen.findByText("10분 0초 소요")).toBeInTheDocument();
    // finished_at 이 null 인 종단 행은 소요를 지어내지 않는다 -- 소요 표기는 하나뿐.
    expect(screen.getAllByText(/소요/)).toHaveLength(1);
  });

  it("Pending 은 적합성 확인(프리플라이트) 중임을 알린다", async () => {
    server.use(http.get("/api/admin/builds", () => HttpResponse.json([
      { ...BUILD, state: "Pending", commit_sha: null, finished_at: null },
    ])));
    wrap();
    expect(await screen.findByText("적합성 확인 중")).toBeInTheDocument();
  });

  it("태그 셀은 클릭 한 번에 전체 선택되는 등폭 텍스트다(clipboard API 금지)", async () => {
    wrap();
    const tag = await screen.findByText("b01234567");
    expect(tag.className).toContain("select-all");
    expect(tag.className).toContain("font-mono");
  });

  it("행마다 상세로 가는 링크를 준다", async () => {
    wrap();
    const link = await screen.findByRole("link", { name: "상세" });
    expect(link).toHaveAttribute("href", `/admin/builds/${BUILD.build_id}`);
  });

  // ── P3: 목록 운영 ───────────────────────────────────────────────────────────
  it("상태 필터로 실패만 볼 수 있다", async () => {
    server.use(http.get("/api/admin/builds", () => HttpResponse.json([
      { ...BUILD, build_id: "a".repeat(32), tag: "bok000001" },
      { ...BUILD, build_id: "b".repeat(32), tag: "bfail0001", state: "Failed",
        reason_code: "build_node_disk_low" },
    ])));
    wrap();
    await screen.findByText("bok000001");
    await userEvent.click(screen.getByRole("button", { name: "실패" }));
    expect(screen.getByText("bfail0001")).toBeInTheDocument();
    expect(screen.queryByText("bok000001")).not.toBeInTheDocument();
  });

  // 다른 화면(BatchDetail·JobViewer)은 전부 버튼 그룹이다 -- 이 화면만 select 였다.
  it("상태 필터는 select 가 아니라 버튼 그룹이고 누른 것을 aria-pressed 로 알린다", async () => {
    wrap();
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
    wrap();
    await screen.findByText("b00000000");
    expect(screen.queryByText("b00000020")).not.toBeInTheDocument();
    expect(screen.getByText("1 / 2 페이지")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "다음" }));
    expect(screen.getByText("b00000020")).toBeInTheDocument();
  });

  it("건수를 함께 보여준다", async () => {
    wrap();
    expect(await screen.findByText("1건")).toBeInTheDocument();
  });

  // ── P4: DS Cloud 재설계 계약(밀도) ──────────────────────────────────────────
  it("목록의 사유는 한 줄로 자르고 전문은 title 에 담는다", async () => {
    server.use(http.get("/api/admin/builds", () => HttpResponse.json([
      { ...BUILD, state: "Failed", reason_code: "build_node_disk_low", finished_at: null },
    ])));
    wrap();
    const cell = await screen.findByText(/디스크 여유가 부족/);
    // truncate = 한 줄 고정(행 높이가 사유 길이에 따라 들쭉날쭉해지지 않는다).
    expect(cell.className).toContain("truncate");
    expect(cell).toHaveAttribute("title", reasonText("build_node_disk_low"));
  });

  it("목록에서 commit·노드 열을 빼 상세로 민다", async () => {
    wrap();
    await screen.findByText("b01234567");
    expect(screen.queryByRole("columnheader", { name: "commit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "노드" })).not.toBeInTheDocument();
    expect(screen.queryByText("deadbeef")).not.toBeInTheDocument();
    // 노드 이름은 빌드하기 화면의 확인 박스에만 남는다(표에서 매 행 반복하지 않는다).
    expect(screen.queryByText("dms-w1")).not.toBeInTheDocument();
    // 체크박스 열 + 데이터 8열 = 9. e2e L2 하한(8셀) 이상을 유지한다.
    expect(screen.getAllByRole("columnheader")).toHaveLength(9);
  });

  // ── 다중 선택 삭제(슬라이스 34) ──────────────────────────────────────────
  it("종단 빌드를 선택해 2단 확인으로 삭제하고 목록을 재조회한다", async () => {
    let deleted: string | null = null;
    server.use(http.delete("/api/admin/builds/:id", ({ params }) => {
      deleted = params.id as string; return HttpResponse.json({ deleted: params.id });
    }));
    wrap();
    await userEvent.click(await screen.findByLabelText("빌드 0123456789ab 선택"));
    await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
    await userEvent.click(screen.getByRole("button", { name: "1개 삭제 확인" }));
    await waitFor(() => expect(deleted).toBe(BUILD.build_id));
  });

  it("진행 중 빌드는 체크박스가 잠겨 있다", async () => {
    server.use(http.get("/api/admin/builds", () =>
      HttpResponse.json([{ ...BUILD, state: "Running", finished_at: null }])));
    wrap();
    const box = await screen.findByLabelText("빌드 0123456789ab 선택");
    expect(box).toBeDisabled();
  });

  it("삭제 실패는 사유를 바 아래에 남긴다", async () => {
    server.use(http.delete("/api/admin/builds/:id", () =>
      HttpResponse.json({ detail: "build_not_deletable" }, { status: 409 })));
    wrap();
    await userEvent.click(await screen.findByLabelText("빌드 0123456789ab 선택"));
    await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
    await userEvent.click(screen.getByRole("button", { name: "1개 삭제 확인" }));
    expect(await screen.findByText(/진행 중인 빌드는 삭제할 수 없습니다/)).toBeInTheDocument();
  });

  // ── 분리 계약: 폼은 이 화면의 것이 아니다 ───────────────────────────────────
  it("제출 폼은 여기 없다 — 빌드하기 탭으로 나갔다", async () => {
    wrap();
    await screen.findByText("b01234567");
    expect(screen.queryByRole("button", { name: "빌드 시작" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("git ref")).not.toBeInTheDocument();
  });

  it("두 하위 페이지를 잇는 탭을 단다", async () => {
    wrap();
    await screen.findByText("b01234567");
    expect(screen.getByRole("link", { name: "빌드하기" }))
      .toHaveAttribute("href", "/admin/builds");
  });

  // ── 정렬: 목록은 폭 제한 없는 전폭(BatchesList·JobsList 관례) ────────────────
  it("목록은 가운데로 모으지 않고 전폭 왼쪽 기준선에 선다", async () => {
    const { container } = wrap();
    await screen.findByText("b01234567");
    expect(container.querySelector(".mx-auto")).toBeNull();
    expect(container.querySelector("section")?.className ?? "").not.toMatch(/max-w-/);
  });
});
