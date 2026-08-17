import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { ControlStatePage } from "./ControlStatePage";

const server = setupServer(
  // 컨트롤 상태 화면의 빌드 노드는 이제 select다 -- 옵션 목록을 이 엔드포인트에서
  // 읽는다(useNodes). 아래 CS가 가리키는 "dms-w1"이 항상 옵션에 있도록 기본으로 둔다.
  http.get("/api/admin/nodes", () => HttpResponse.json([
    { node_name: "dms-w1", reported_at: "2026-08-05T00:00:00Z", fresh: true, report: {} },
  ])),
);
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const CS = { maintenance: 1, drain: 0, reason: "점검", build_node_name: "dms-w1", build_source_path: "/home/mason/dms-dev/dms", changed_by: "ops", changed_at: "2026-08-05T00:00:00Z" };

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><ControlStatePage /></QueryClientProvider>);
}

test("shows the maintenance banner and changed_by, but no drain banner", async () => {
  server.use(http.get("/api/admin/control-state", () => HttpResponse.json(CS)));
  wrap();
  expect(await screen.findByText((_, node) =>
    Boolean(node?.textContent?.startsWith("유지보수 중") && node.textContent.includes("드레인도 함께 켜세요")))).toBeInTheDocument();
  expect(screen.getByText("ops")).toBeInTheDocument();
  expect(screen.queryByText("드레인 중 — 진행 중인 작업이 더 전진하지 않습니다")).not.toBeInTheDocument();
});

test("toggling drain and saving sends the correct PUT body", async () => {
  let body: any = null;
  server.use(
    http.get("/api/admin/control-state", () => HttpResponse.json(CS)),
    http.put("/api/admin/control-state", async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ ...CS, drain: 1 });
    }));
  wrap();
  await screen.findByLabelText("유지보수");
  await userEvent.click(screen.getByLabelText("드레인"));
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(body).toEqual({ maintenance: true, drain: true, reason: "점검",
                         build_node_name: "dms-w1",
                         build_source_path: "/home/mason/dms-dev/dms" });
});

test("빌드 노드는 자유 입력이 아니라 보고된 노드 중에서 고른다(select)", async () => {
  server.use(
    http.get("/api/admin/control-state", () => HttpResponse.json(CS)),
    http.get("/api/admin/nodes", () => HttpResponse.json([
      { node_name: "dms-w1", reported_at: "t", fresh: true, report: {} },
      { node_name: "dms-w2", reported_at: "t", fresh: true, report: {} },
    ])),
  );
  wrap();
  const select = (await screen.findByLabelText("빌드 노드")) as HTMLSelectElement;
  const optionValues = Array.from(select.options).map((o) => o.value);
  // "지정 안 함"(빈 값) + 보고된 두 노드만 있고, 자유 입력(텍스트 박스)이 아니다.
  expect(optionValues).toEqual(["", "dms-w1", "dms-w2"]);
  expect(select.tagName).toBe("SELECT");
});

test("빌드 노드를 서버가 거절하면(unknown_build_node) 한글 메시지를 보여준다", async () => {
  server.use(
    http.get("/api/admin/control-state", () => HttpResponse.json(CS)),
    http.put("/api/admin/control-state", () =>
      HttpResponse.json({ detail: "unknown_build_node" }, { status: 422 })),
  );
  wrap();
  await screen.findByLabelText("유지보수");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(await screen.findByText("등록된 에이전트 노드 중에서 선택해 주세요")).toBeInTheDocument();
});

test("renders no warning banners when both flags are off", async () => {
  server.use(http.get("/api/admin/control-state", () =>
    HttpResponse.json({ maintenance: 0, drain: 0, reason: null, build_node_name: null, changed_by: null, changed_at: null })));
  wrap();
  await screen.findByLabelText("유지보수");
  expect(screen.queryByText((_, node) => Boolean(node?.textContent?.startsWith("유지보수 중")))).not.toBeInTheDocument();
  expect(screen.queryByText("드레인 중 — 진행 중인 작업이 더 전진하지 않습니다")).not.toBeInTheDocument();
});
