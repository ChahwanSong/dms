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
    expect(await screen.findByText(/레지스트리가 지금 삭제를 받지 않습니다/)).toBeInTheDocument();
  });

  it("삭제·블롭 회수·노드 캐시의 경계를 안내한다", async () => {
    wrap();
    await screen.findByText("pkg-01:5000");
    expect(screen.getByText(/블롭 회수와 노드의/)).toBeInTheDocument();
    // 2026-11-11 B28: 삭제는 digest 단위 -- 같은 이미지를 가리키는 다른 태그도 함께 사라진다는 것을 말한다.
    expect(screen.getByText(/같은 이미지\(digest\)를 가리키는 다른 태그도 함께 사라집니다/)).toBeInTheDocument();
  });

  it("같은 digest 의 별칭 태그를 함께 지워 한쪽이 404(이미 없음)여도 실패로 세지 않는다(2026-11-11 B28)", async () => {
    // 먼저 처리된 쪽이 digest 를 지워 다른 쪽은 registry_tag_not_found 404 -- 실제로는 둘 다 지워졌다.
    server.use(
      http.delete("/api/admin/registry/images/dms/b99d97238", () =>
        HttpResponse.json({ deleted: "dms:b99d97238" })),
      http.delete("/api/admin/registry/images/dms-mpifileutils/b99d97238", () =>
        HttpResponse.json({ detail: "registry_tag_not_found" }, { status: 404 })),
    );
    wrap();
    await userEvent.click(await screen.findByLabelText("dms:b99d97238 선택"));
    await userEvent.click(screen.getByLabelText("dms-mpifileutils:b99d97238 선택"));
    await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
    await userEvent.click(screen.getByRole("button", { name: "2개 삭제 확인" }));
    const bar = await screen.findByText("1개 삭제됨 · 1개 이미 없음");
    expect(bar).toHaveClass("text-ok");
    expect(screen.queryByText(/실패/, { selector: "[role=status]" })).toBeNull();
    expect(screen.getByText(/dms-mpifileutils:b99d97238 — 이미 레지스트리에 없습니다/)).toBeInTheDocument();
    expect(screen.queryByText(/해당 태그를 찾을 수 없습니다/)).toBeNull();
  });

  it("하나도 지우지 않고 전부 「이미 없음」이면 초록(성공)이 아니라 중립 톤이다(2026-11-11 리뷰)", async () => {
    server.use(
      http.delete("/api/admin/registry/images/dms/b99d97238", () =>
        HttpResponse.json({ detail: "registry_tag_not_found" }, { status: 404 })),
    );
    wrap();
    await userEvent.click(await screen.findByLabelText("dms:b99d97238 선택"));
    await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
    await userEvent.click(screen.getByRole("button", { name: "1개 삭제 확인" }));
    const bar = await screen.findByText("0개 삭제됨 · 1개 이미 없음");
    expect(bar).toHaveClass("text-muted");
    expect(bar).not.toHaveClass("text-ok");
  });

  it("레지스트리 무응답(502 registry_error)은 「이미 없음」이 아니라 실패로 센다(2026-11-11 리뷰)", async () => {
    // 서버는 digest 조회 무응답을 404 가 아니라 502 로 낸다(registry.manifest_digest_lookup) -- 지웠는지 모르는 것을 성공
    // 쪽에 세지 않는다.
    server.use(
      http.delete("/api/admin/registry/images/dms/b99d97238", () =>
        HttpResponse.json({ detail: "registry_error" }, { status: 502 })),
    );
    wrap();
    await userEvent.click(await screen.findByLabelText("dms:b99d97238 선택"));
    await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
    await userEvent.click(screen.getByRole("button", { name: "1개 삭제 확인" }));
    const bar = await screen.findByText("0개 삭제됨 · 1개 실패");
    expect(bar).toHaveClass("text-bad");
    expect(screen.queryByText(/이미 레지스트리에 없습니다/)).toBeNull();
    expect(screen.getByText(/dms:b99d97238: 레지스트리 응답에 문제가 있습니다/)).toBeInTheDocument();
  });

  it("이미지 관리 탭을 단다", async () => {
    wrap();
    await screen.findByText("pkg-01:5000");
    const tabs = screen.getByRole("navigation", { name: "빌드 하위 메뉴" });
    expect(within(tabs).getByRole("link", { name: "이미지 관리" }))
      .toHaveAttribute("href", "/admin/builds/images");
  });
});
