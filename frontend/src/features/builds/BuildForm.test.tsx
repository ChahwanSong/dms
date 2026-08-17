import { describe, it, expect, beforeAll, afterEach, afterAll } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { BuildForm } from "./BuildForm";

const SRC = "/home/mason/dms-dev/dms";

const BUILD = {
  build_id: "0123456789abcdef0123456789abcdef", source_path: SRC, git_ref: "local",
  commit_sha: "deadbeef", images: ["dms"], node_name: "dms-w1",
  state: "Succeeded", reason_code: null, tag: "b01234567",
  created_at: "2026-08-06T00:00:00Z", finished_at: "2026-08-06T00:10:00Z",
};

const server = setupServer(
  http.get("/api/admin/control-state", () =>
    HttpResponse.json({ maintenance: 0, drain: 0, reason: null,
                        build_node_name: "dms-w1", build_source_path: SRC,
                        changed_by: "ops",
                        changed_at: "2026-08-06T00:00:00Z" })),
  http.get("/api/admin/builds", () => HttpResponse.json([BUILD])),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// 이력 화면은 여기서 스텁이다 -- 이 파일이 재는 것은 "제출 뒤 어디로 가는가"이지
// 이력 화면의 내용이 아니다(그건 BuildHistory.test.tsx 의 영토).
const wrap = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/admin/builds"]}>
        <Routes>
          <Route path="/admin/builds" element={<BuildForm />} />
          <Route path="/admin/builds/history" element={<h1>빌드 이력</h1>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

/** 폼이 데이터를 받은 뒤(확인 박스에 노드 이름이 선 뒤)를 기다린다. */
const ready = () => screen.findByText("dms-w1");

describe("BuildForm — 빌드하기(기본 하위 페이지)", () => {
  it("빌드 노드가 없으면 제출을 막고 안내한다", async () => {
    server.use(http.get("/api/admin/control-state", () =>
      HttpResponse.json({ maintenance: 0, drain: 0, reason: null,
                          build_node_name: null, build_source_path: SRC,
                          changed_by: null, changed_at: null })));
    wrap();
    expect(await screen.findByText(/빌드 노드가 지정되지 않았습니다/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "빌드 시작" })).toBeDisabled();
  });

  it("소스 경로가 없으면 제출을 막고 컨트롤 상태로 안내한다", async () => {
    server.use(http.get("/api/admin/control-state", () =>
      HttpResponse.json({ maintenance: 0, drain: 0, reason: null,
                          build_node_name: "dms-w1", build_source_path: null,
                          changed_by: null, changed_at: null })));
    wrap();
    expect(await screen.findByText(/빌드 소스 경로가 지정되지 않았습니다/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "빌드 시작" })).toBeDisabled();
  });

  it("이미지를 하나도 고르지 않으면 제출 버튼이 비활성이다", async () => {
    wrap();
    await ready();
    await userEvent.click(screen.getByLabelText("dms"));   // 기본 체크를 끈다
    expect(screen.getByRole("button", { name: "빌드 시작" })).toBeDisabled();
  });

  it("제출하면 POST 한다", async () => {
    let posted: unknown = null;
    server.use(http.post("/api/admin/builds", async ({ request }) => {
      posted = await request.json();
      return HttpResponse.json({ build_id: "x", state: "Pending" }, { status: 202 });
    }));
    wrap();
    await ready();
    await userEvent.click(screen.getByRole("button", { name: "빌드 시작" }));
    await waitFor(() => expect(posted).toEqual({ images: ["dms"], tag: null }));
  });

  // 목록이 다른 화면으로 나간 뒤의 핵심 계약: 제출 직후 알고 싶은 것은 "지금 어떻게
  // 되고 있나"인데, 폼에 남으면 방금 만든 빌드가 어디에도 보이지 않는다.
  it("제출에 성공하면 빌드 이력으로 이동한다", async () => {
    server.use(http.post("/api/admin/builds", () =>
      HttpResponse.json({ build_id: "x", state: "Pending" }, { status: 202 })));
    wrap();
    await ready();
    await userEvent.click(screen.getByRole("button", { name: "빌드 시작" }));
    expect(await screen.findByRole("heading", { name: "빌드 이력" })).toBeInTheDocument();
  });

  it("제출이 실패하면 이동하지 않고 한국어 오류를 폼 옆에 남긴다", async () => {
    server.use(http.post("/api/admin/builds", () =>
      HttpResponse.json({ detail: "build_in_progress" }, { status: 409 })));
    wrap();
    await ready();
    await userEvent.click(screen.getByRole("button", { name: "빌드 시작" }));
    expect(await screen.findByText("이미 진행 중인 빌드가 있습니다")).toBeInTheDocument();
    // 고칠 수 있는 화면(폼)에 남아 있어야 오류가 의미를 가진다.
    expect(screen.getByRole("button", { name: "빌드 시작" })).toBeInTheDocument();
  });

  // ── 진행 중 배너: 백엔드가 동시 1건만 허용하므로 제출 전에 알린다 ─────────────
  it("진행 중인 빌드가 있으면 배너로 알리고 이력으로 보낸다", async () => {
    server.use(http.get("/api/admin/builds", () => HttpResponse.json([
      { ...BUILD, state: "Running", finished_at: null },
    ])));
    wrap();
    expect(await screen.findByText(/진행 중인 빌드가 있습니다/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /이력에서 보기/ }))
      .toHaveAttribute("href", "/admin/builds/history");
  });

  it("진행 중인 빌드가 없으면 배너를 띄우지 않는다", async () => {
    wrap();
    await ready();
    expect(screen.queryByText(/진행 중인 빌드가 있습니다/)).not.toBeInTheDocument();
  });

  // ── 제출 전에 미리 막아 주는 것들(구 BuildsPage 계약 이관) ───────────────────
  it("이미지 의존 관계는 dms-agent 를 골랐을 때만 한 줄로 알린다", async () => {
    wrap();
    await ready();
    expect(screen.queryByText(/dms-agent 는 dms·dms-mpifileutils 를 같은 태그로 FROM/))
      .not.toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("dms-agent"));
    await userEvent.click(screen.getByLabelText("dms-mpifileutils"));
    expect(screen.getByText(/dms-agent 는 dms·dms-mpifileutils 를 같은 태그로 FROM/))
      .toBeInTheDocument();
  });

  it("dms-agent 만 고르면 경고하되 제출은 막지 않는다(레지스트리에 이미 있을 수 있다)", async () => {
    wrap();
    await ready();
    await userEvent.click(screen.getByLabelText("dms"));        // 기본 체크 해제
    await userEvent.click(screen.getByLabelText("dms-agent"));
    expect(screen.getByText(/이번 빌드에 없습니다/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "빌드 시작" })).toBeEnabled();
  });

  it("dms-agent 를 의존 이미지와 함께 고르면 경고가 사라진다", async () => {
    wrap();
    await ready();
    await userEvent.click(screen.getByLabelText("dms-agent"));
    await userEvent.click(screen.getByLabelText("dms-mpifileutils"));
    expect(screen.queryByText(/이번 빌드에 없습니다/)).not.toBeInTheDocument();
  });

  it("빌드 노드를 확인 박스에서 보여준다", async () => {
    wrap();
    // control-state 가 도착하기 전에는 미설정 안내가 서 있다 -- 노드 이름을 기다려야
    // 확인 박스의 "설정됨" 경로를 실제로 지나간다.
    expect(await ready()).toBeInTheDocument();
    // "빌드 노드" 문구는 안내 카드에도 나온다 -- 확인 박스의 것 하나만 고집하지
    // 않고 존재만 단언한다(노드 이름 자체는 ready() 가 이미 확인했다).
    expect(screen.getAllByText(/빌드 노드/).length).toBeGreaterThan(0);
  });

  it("빌드 노드가 없으면 컨트롤 상태 화면으로 가는 링크를 준다", async () => {
    server.use(http.get("/api/admin/control-state", () =>
      HttpResponse.json({ maintenance: 0, drain: 0, reason: null,
                          build_node_name: null, build_source_path: SRC,
                          changed_by: null, changed_at: null })));
    wrap();
    const link = await screen.findByRole("link", { name: /컨트롤 상태/ });
    expect(link).toHaveAttribute("href", "/admin/control");
  });

  it("확인 박스가 로컬 소스 경로와 미커밋 포함을 알린다", async () => {
    wrap();
    await ready();
    expect(screen.getByText(SRC)).toBeInTheDocument();
    expect(screen.getByText(/미커밋 변경 포함/)).toBeInTheDocument();
  });

  it("태그를 지정하면 본문에 실려 간다", async () => {
    let posted: unknown = null;
    server.use(http.post("/api/admin/builds", async ({ request }) => {
      posted = await request.json();
      return HttpResponse.json({ build_id: "x", state: "Pending" }, { status: 202 });
    }));
    wrap();
    await ready();
    await userEvent.type(screen.getByLabelText("태그"), "d73");
    await userEvent.click(screen.getByRole("button", { name: "빌드 시작" }));
    await waitFor(() => expect(posted).toEqual({ images: ["dms"], tag: "d73" }));
  });

  it("프리플라이트가 먼저 돈다는 것을 폼에서 알린다", async () => {
    wrap();
    expect(await screen.findByText(/적합성 프리플라이트/)).toBeInTheDocument();
  });

  it("빌드 절차는 평소 2줄이고, 안내 카드를 눌러야 전문이 팝업으로 열린다", async () => {
    wrap();
    await ready();
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

  it("취소는 화면을 떠나지 않고 폼을 기본값으로 되돌린다", async () => {
    wrap();
    await ready();
    await userEvent.type(screen.getByLabelText("태그"), "d73");
    await userEvent.click(screen.getByLabelText("dms-agent"));
    await userEvent.click(screen.getByRole("button", { name: "취소" }));
    expect(screen.getByLabelText("태그")).toHaveValue("");
    expect(screen.getByLabelText("dms-agent")).not.toBeChecked();
    expect(screen.getByLabelText("dms")).toBeChecked();
  });

  it("목록 응답이 배열이 아니어도 흰 화면이 되지 않는다", async () => {
    // 빠른 ref·진행 중 배너가 목록에서 파생되므로 여기도 방어가 필요하다.
    server.use(http.get("/api/admin/builds", () => HttpResponse.json({ oops: true })));
    wrap();
    expect(await screen.findByRole("heading", { name: "빌드" })).toBeInTheDocument();
  });

  // ── 분리 계약: 목록은 이 화면의 것이 아니다 ─────────────────────────────────
  it("표(빌드 목록)는 여기 없다 — 이력 탭으로 나갔다", async () => {
    wrap();
    await ready();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "상태 필터" })).not.toBeInTheDocument();
    expect(screen.queryByText("b01234567")).not.toBeInTheDocument();
  });

  it("두 하위 페이지를 잇는 탭을 단다", async () => {
    wrap();
    await ready();
    expect(screen.getByRole("link", { name: "빌드 이력" }))
      .toHaveAttribute("href", "/admin/builds/history");
  });

  // ── 정렬: 가운데가 아니라 왼쪽 기준선(BatchCreate·SubmitJob 관례) ────────────
  it("폼은 가운데 정렬이 아니다 — 다른 제출 화면과 같은 왼쪽 기준선에 선다", async () => {
    const { container } = wrap();
    await ready();
    expect(container.querySelector(".mx-auto")).toBeNull();
    // 폭은 여전히 제한한다(글줄이 너무 길면 폼이 읽기 어렵다) -- 정렬만 왼쪽이다.
    expect(container.querySelector("section")?.className).toContain("max-w-2xl");
  });
});
