import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { AccountsList } from "./AccountsList";

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const ACCOUNTS = [
  { username: "admin", role: "admin", email: "admin@example.com", disabled: 0, created_at: "2026-08-05T00:00:00Z" },
  { username: "alice", role: "user", email: null, disabled: 1, created_at: "2026-08-05T00:00:00Z" },
];

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><AccountsList /></QueryClientProvider>);
}

function stubMe(actor: string) {
  server.use(http.get("/api/auth/me", () => HttpResponse.json({ actor, role: "admin" })));
}

test("lists accounts with username, role, and status", async () => {
  stubMe("admin");
  server.use(http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)));
  wrap();
  expect(await screen.findByText("admin", { selector: "td" })).toBeInTheDocument();
  expect(screen.getByText("alice")).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "admin 역할" })).toHaveValue("admin");
  expect(screen.getByRole("combobox", { name: "alice 역할" })).toHaveValue("user");
  expect(screen.getByText("활성")).toBeInTheDocument();
  expect(screen.getByText("비활성")).toBeInTheDocument();
});

test("changing a role sends the correct PUT body", async () => {
  stubMe("admin");
  let capturedBody: unknown;
  server.use(
    http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)),
    http.put("/api/admin/accounts/alice/role", async ({ request }) => {
      capturedBody = await request.json();
      return HttpResponse.json({ ...ACCOUNTS[1], role: "admin" });
    }));
  wrap();
  const select = await screen.findByRole("combobox", { name: "alice 역할" });
  await userEvent.selectOptions(select, "admin");
  expect(capturedBody).toEqual({ role: "admin" });
});

test("toggling status sends the correct PUT body", async () => {
  stubMe("root"); // neither row is the current user, so both toggle buttons stay enabled
  let capturedBody: unknown;
  server.use(
    http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)),
    http.put("/api/admin/accounts/admin/disabled", async ({ request }) => {
      capturedBody = await request.json();
      return HttpResponse.json({ ...ACCOUNTS[0], disabled: 1 });
    }));
  wrap();
  const row = (await screen.findByText("admin", { selector: "td" })).closest("tr")!;
  await userEvent.click(within(row).getByRole("button", { name: "비활성화" }));
  expect(capturedBody).toEqual({ disabled: true });
});

test("the current user's own row has disabled controls", async () => {
  stubMe("alice");
  server.use(http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)));
  wrap();
  const selfRow = (await screen.findByText("alice")).closest("tr")!;
  expect(within(selfRow).getByRole("combobox", { name: "alice 역할" })).toBeDisabled();
  expect(within(selfRow).getByRole("button")).toBeDisabled();
  expect(within(selfRow).getByText("자기 계정은 변경할 수 없습니다")).toBeInTheDocument();

  const otherRow = screen.getByText("admin", { selector: "td" }).closest("tr")!;
  expect(within(otherRow).getByRole("combobox", { name: "admin 역할" })).not.toBeDisabled();
  expect(within(otherRow).getByRole("button")).not.toBeDisabled();
});

test("shows the Korean message when a mutation returns 409 cannot_lock_self", async () => {
  stubMe("admin");
  server.use(
    http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)),
    http.put("/api/admin/accounts/alice/disabled", () =>
      HttpResponse.json({ detail: "cannot_lock_self" }, { status: 409 })));
  wrap();
  const row = (await screen.findByText("alice")).closest("tr")!;
  await userEvent.click(within(row).getByRole("button", { name: "활성화" }));
  expect(await screen.findByText("자기 계정의 역할 변경·비활성화는 할 수 없습니다")).toBeInTheDocument();
});
