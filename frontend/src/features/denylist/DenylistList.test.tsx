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
    http.put("/api/admin/identity-denylist/owner/wheel", async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ subject_type: "owner", subject: "wheel", reason: "spam" });
    }));
  wrap();
  await screen.findByText("등재된 대상이 없습니다");
  await userEvent.click(screen.getByRole("button", { name: "대상 추가" }));

  await userEvent.selectOptions(screen.getByRole("combobox", { name: "대상 유형" }), "owner");
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
  // 미인코딩이면 fetch 가 "#1" 을 fragment 로 버려 ".../owner/grp" 에 PUT 된다
  // (wrong-target). 공백·비ASCII 는 URL 파서가 자동 인코딩해 재료가 못 된다 --
  // # 만이 현행과 수정본을 가른다.
  let seenSubject = "";
  let seenPath = "";
  server.use(
    http.get("/api/admin/identity-denylist", () => HttpResponse.json([])),
    http.put("/api/admin/identity-denylist/:type/:subject", ({ params, request }) => {
      seenSubject = String(params.subject);
      seenPath = new URL(request.url).pathname;
      return HttpResponse.json({ subject_type: "owner", subject: "grp#1", reason: null });
    }));
  wrap();
  await screen.findByText("등재된 대상이 없습니다");
  await userEvent.click(screen.getByRole("button", { name: "대상 추가" }));
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "대상 유형" }), "owner");
  await userEvent.type(screen.getByRole("textbox", { name: "대상" }), "grp#1");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await waitFor(() => expect(seenSubject).toBe("grp#1"));
  expect(seenPath).toBe("/api/admin/identity-denylist/owner/grp%231");
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

test("유형 의미 설명: 목록 상단 패널에 2유형(requester·owner) + kill-switch 특성, LDAP 그룹 유형 없음", async () => {
  server.use(http.get("/api/admin/identity-denylist", () => HttpResponse.json([])));
  wrap();
  await screen.findByText("등재된 대상이 없습니다");
  // 판정 시점·우선순위(identity.resolve_job_identity 의 미러)
  expect(screen.getByText(/제출 시점에 판정하며, 특권 실행 경로보다 먼저/)).toBeInTheDocument();
  // 두 유형의 구분(제출 계정 vs 실행 신원)이 화면에 있다 -- group(LDAP) 유형은 2026-10-26 에 없어졌다
  expect(screen.getByText(/작업을 제출한 계정 기준/)).toBeInTheDocument();
  expect(screen.getByText(/실행 신원\(권한 기준 사용자\) 기준/)).toBeInTheDocument();
  expect(screen.queryByText(/LDAP 그룹 기준/)).toBeNull();
});

test("옛 group 행은 「평가 안 됨」으로 남아 해제할 수 있고, 추가 다이얼로그엔 group 유형이 없다", async () => {
  server.use(http.get("/api/admin/identity-denylist", () => HttpResponse.json(
    [{ subject_type: "group", subject: "wheel", reason: null }])));
  wrap();
  const row = (await screen.findByText("wheel")).closest("tr")!;
  expect(within(row).getByText(/평가 안 됨 — 이전 버전의 LDAP 그룹 규칙/)).toBeInTheDocument();
  expect(within(row).getByRole("button", { name: "해제" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "대상 추가" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(within(dialog).getByLabelText("대상 유형")).queryByRole("option", { name: "group" })).toBeNull();
});

test("추가 다이얼로그: 유형 선택을 바꾸면 설명 캡션이 따라 바뀐다", async () => {
  server.use(http.get("/api/admin/identity-denylist", () => HttpResponse.json([])));
  wrap();
  await screen.findByText("등재된 대상이 없습니다");
  await userEvent.click(screen.getByRole("button", { name: "대상 추가" }));
  const dialog = await screen.findByRole("dialog");
  // 기본 requester 의 설명이 떠 있다
  expect(within(dialog).getByText(/작업을 제출한 계정 기준/)).toBeInTheDocument();
  await userEvent.selectOptions(within(dialog).getByLabelText("대상 유형"), "owner");
  expect(within(dialog).getByText(/실행 신원\(권한 기준 사용자\) 기준/)).toBeInTheDocument();
  expect(within(dialog).queryByText(/작업을 제출한 계정 기준/)).not.toBeInTheDocument();
});
