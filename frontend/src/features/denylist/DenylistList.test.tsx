import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { DenylistList } from "./DenylistList";

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const ENTRIES = [
  { subject_type: "requester", subject: "alice", reason: "abuse" },
  { subject_type: "owner", subject: "bob", reason: null },
];

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><DenylistList /></QueryClientProvider>);
}

test("lists denylist entries and shows the add button", async () => {
  server.use(http.get("/api/admin/identity-denylist", () => HttpResponse.json(ENTRIES)));
  wrap();
  expect(await screen.findByText("alice")).toBeInTheDocument();
  expect(screen.getByText("bob")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "대상 추가" })).toBeInTheDocument();
});

test("adding an entry sends the correct PUT body", async () => {
  let body: any = null;
  server.use(
    http.get("/api/admin/identity-denylist", () => HttpResponse.json([])),
    http.put("/api/admin/identity-denylist/group/wheel", async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ subject_type: "group", subject: "wheel", reason: "spam" });
    }));
  wrap();
  await screen.findByText("등재된 대상이 없습니다");
  await userEvent.click(screen.getByRole("button", { name: "대상 추가" }));

  await userEvent.selectOptions(screen.getByRole("combobox", { name: "대상 유형" }), "group");
  await userEvent.type(screen.getByRole("textbox", { name: "대상" }), "wheel");
  await userEvent.type(screen.getByRole("textbox", { name: "사유" }), "spam");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));

  expect(body).toEqual({ reason: "spam" });
});

test("releasing an entry issues DELETE for the right subject", async () => {
  let released = false;
  server.use(
    http.get("/api/admin/identity-denylist", () => HttpResponse.json(ENTRIES)),
    http.delete("/api/admin/identity-denylist/requester/alice", () => {
      released = true;
      return new HttpResponse(null, { status: 204 });
    }));
  wrap();
  await screen.findByText("alice");
  const row = screen.getByText("alice").closest("tr")!;
  await userEvent.click(within(row).getByRole("button", { name: "해제" }));
  await userEvent.click(await screen.findByRole("button", { name: "해제 확인" }));

  expect(released).toBe(true);
});

test("subject 의 # 는 인코딩되어 정확한 대상에 PUT 된다 -- fragment 절단 금지", async () => {
  // 미인코딩이면 fetch 가 "#1" 을 fragment 로 버려 ".../group/grp" 에 PUT 된다
  // (wrong-target). 공백·비ASCII 는 URL 파서가 자동 인코딩해 재료가 못 된다 --
  // # 만이 현행과 수정본을 가른다.
  let seenSubject = "";
  let seenPath = "";
  server.use(
    http.get("/api/admin/identity-denylist", () => HttpResponse.json([])),
    http.put("/api/admin/identity-denylist/:type/:subject", ({ params, request }) => {
      seenSubject = String(params.subject);
      seenPath = new URL(request.url).pathname;
      return HttpResponse.json({ subject_type: "group", subject: "grp#1", reason: null });
    }));
  wrap();
  await screen.findByText("등재된 대상이 없습니다");
  await userEvent.click(screen.getByRole("button", { name: "대상 추가" }));
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "대상 유형" }), "group");
  await userEvent.type(screen.getByRole("textbox", { name: "대상" }), "grp#1");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await waitFor(() => expect(seenSubject).toBe("grp#1"));
  expect(seenPath).toBe("/api/admin/identity-denylist/group/grp%231");
});

test("subject 의 ? 는 인코딩되어 정확한 대상에 DELETE 된다 -- 쿼리 흡수 금지", async () => {
  // 미인코딩이면 "?y" 가 쿼리로 흡수돼 ".../requester/x" 가 지워진다 --
  // 해제(DELETE)의 wrong-target 은 엉뚱한 차단을 푸는 실사고다.
  let seenSubject = "";
  server.use(
    http.get("/api/admin/identity-denylist", () => HttpResponse.json(
      [{ subject_type: "requester", subject: "x?y", reason: null }])),
    http.delete("/api/admin/identity-denylist/:type/:subject", ({ params }) => {
      seenSubject = String(params.subject);
      return new HttpResponse(null, { status: 204 });
    }));
  wrap();
  await screen.findByText("x?y");
  const row = screen.getByText("x?y").closest("tr")!;
  await userEvent.click(within(row).getByRole("button", { name: "해제" }));
  await userEvent.click(await screen.findByRole("button", { name: "해제 확인" }));
  await waitFor(() => expect(seenSubject).toBe("x?y"));
});
