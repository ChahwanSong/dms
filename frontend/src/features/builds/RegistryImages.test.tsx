import { describe, it, expect, beforeAll, afterEach, afterAll } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { RegistryImages } from "./RegistryImages";

const IMAGES = {
  registry: "pkg-01:5000",
  repositories: [
    { repository: "dms-mpifileutils", reachable: true, tags: [
      { tag: "b99d97238", in_use: false }, { tag: "d53", in_use: true }] },
    { repository: "dms", reachable: true, tags: [
      { tag: "b99d97238", in_use: false }, { tag: "d74", in_use: true }] },
    { repository: "dms-agent", reachable: false, tags: [] },
  ],
};

const server = setupServer(
  http.get("/api/admin/registry/images", () => HttpResponse.json(IMAGES)),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const wrap = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/admin/builds/images"]}>
        <RegistryImages />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

describe("RegistryImages — 이미지 관리", () => {
  it("리포별로 태그를 나열하고 사용 중을 표시한다", async () => {
    wrap();
    expect(await screen.findByText("pkg-01:5000")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "dms" })).toBeInTheDocument();
    // 사용 중 태그 배지.
    expect(screen.getAllByText("사용 중").length).toBeGreaterThan(0);
  });

  it("조회 실패 저장소는 '조회 실패'로 알린다(빈 목록과 구분)", async () => {
    wrap();
    await screen.findByText("pkg-01:5000");
    expect(screen.getByText(/이 저장소를 조회할 수 없습니다/)).toBeInTheDocument();
  });

  it("사용 중 태그 체크박스는 잠겨 있다", async () => {
    wrap();
    const box = await screen.findByLabelText("dms:d74 선택");
    expect(box).toBeDisabled();
    expect(screen.getByLabelText("dms:b99d97238 선택")).toBeEnabled();
  });

  it("미사용 태그를 2단 확인으로 삭제하고 목록을 재조회한다", async () => {
    let deleted: string | null = null;
    server.use(
      http.delete("/api/admin/registry/images/dms/b99d97238", () => {
        deleted = "dms/b99d97238"; return HttpResponse.json({ deleted: "dms:b99d97238" });
      }),
    );
    wrap();
    await userEvent.click(await screen.findByLabelText("dms:b99d97238 선택"));
    await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
    await userEvent.click(screen.getByRole("button", { name: "1개 삭제 확인" }));
    await waitFor(() => expect(deleted).toBe("dms/b99d97238"));
  });

  it("삭제 실패(사용 중 등)는 사유를 바 아래에 남긴다", async () => {
    server.use(http.delete("/api/admin/registry/images/dms/b99d97238", () =>
      HttpResponse.json({ detail: "registry_delete_disabled" }, { status: 409 })));
    wrap();
    await userEvent.click(await screen.findByLabelText("dms:b99d97238 선택"));
    await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
    await userEvent.click(screen.getByRole("button", { name: "1개 삭제 확인" }));
    expect(await screen.findByText(/레지스트리 삭제가 비활성화/)).toBeInTheDocument();
  });

  it("삭제·블롭 회수·노드 캐시의 경계를 안내한다", async () => {
    wrap();
    await screen.findByText("pkg-01:5000");
    expect(screen.getByText(/블롭 회수와 노드의/)).toBeInTheDocument();
  });

  it("이미지 관리 탭을 단다", async () => {
    wrap();
    await screen.findByText("pkg-01:5000");
    const tabs = screen.getByRole("navigation", { name: "빌드 하위 메뉴" });
    expect(within(tabs).getByRole("link", { name: "이미지 관리" }))
      .toHaveAttribute("href", "/admin/builds/images");
  });
});
