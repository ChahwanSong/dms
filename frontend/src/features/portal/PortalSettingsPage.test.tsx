import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { PortalSettingsPage } from "./PortalSettingsPage";
import { portalTitle } from "./usePortal";

// 포탈 설정(2026-10-02): 서브네임 저장·지우기·미리보기·검증, 서버 422 사유 표시.
const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderPage(subtitle: string | null = null) {
  server.use(http.get("/api/portal-info", () => HttpResponse.json({ subtitle })));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><PortalSettingsPage /></QueryClientProvider>);
}

test("portalTitle: 서브네임이 있으면 '메인 - 서브', 없으면 메인만", () => {
  expect(portalTitle("SSC")).toBe("AI Storage Portal - SSC");
  expect(portalTitle(null)).toBe("AI Storage Portal");
  expect(portalTitle("")).toBe("AI Storage Portal");
});

test("서브네임 저장: 미리보기가 따라오고, 앞뒤 공백을 뺀 값을 보낸다", async () => {
  let sent: unknown = undefined;
  server.use(http.put("/api/admin/portal-settings", async ({ request }) => {
    sent = await request.json();
    return HttpResponse.json({ subtitle: "DAI-OA" });
  }));
  renderPage();
  const input = await screen.findByLabelText("서브네임");
  expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();          // 바뀐 게 없다
  await userEvent.type(input, "  DAI-OA ");
  expect(screen.getByLabelText("표시 이름 미리보기")).toHaveTextContent("AI Storage Portal - DAI-OA");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await waitFor(() => expect(sent).toEqual({ subtitle: "DAI-OA" }));
  expect(await screen.findByText("저장했습니다")).toBeInTheDocument();
  expect(input).toHaveValue("DAI-OA");
});

test("서브네임 지우기는 null 을 보낸다", async () => {
  let sent: unknown = undefined;
  server.use(http.put("/api/admin/portal-settings", async ({ request }) => {
    sent = await request.json();
    return HttpResponse.json({ subtitle: null });
  }));
  renderPage("SSC");
  expect(await screen.findByLabelText("서브네임")).toHaveValue("SSC");
  await userEvent.click(screen.getByRole("button", { name: "서브네임 지우기" }));
  await waitFor(() => expect(sent).toEqual({ subtitle: null }));
  expect(await screen.findByLabelText("서브네임")).toHaveValue("");
});

test("40자 초과는 저장을 막고 칸에 오류를 잇는다, 서버 422 는 사유 문구로", async () => {
  server.use(http.put("/api/admin/portal-settings", () =>
    HttpResponse.json({ detail: "invalid_portal_subtitle" }, { status: 422 })));
  renderPage();
  const input = await screen.findByLabelText("서브네임");
  await userEvent.type(input, "x".repeat(41));
  expect(screen.getByRole("alert")).toHaveTextContent("40자 이하");
  expect(input).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();
  await userEvent.clear(input);
  await userEvent.type(input, "A B");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(await screen.findByText(/40자 이하의 한 줄 문자열/)).toBeInTheDocument();
});

test("탭 아이콘 미리보기는 번들된 /favicon.svg 다(외부 주소 아님)", async () => {
  renderPage();
  expect(await screen.findByAltText("현재 탭 아이콘")).toHaveAttribute("src", "/favicon.svg");
});
