import { render, screen, within } from "@testing-library/react";
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
