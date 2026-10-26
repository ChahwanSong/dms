import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, test } from "vitest";
import { ActivitySection, conflictRequestId } from "./RequestActivity";
import { Timeline } from "./Timeline";
import { REASON_MESSAGES } from "../../lib/api";
import type { RequestDetail } from "../../lib/types";

// 경로 겹침(path_conflict, 2026-10-27): planner 가 Conflict 와 함께 남긴 path_conflict 이벤트의 conflict_request_id 로 전이
// 이력의 그 줄에 상대 요청 링크를 붙인다(전이 행엔 상세 칸이 없다).

const OTHER = "0123456789abcdef0123456789abcdef";
const ev = (event_type: string, payload: unknown, id = 1) =>
  ({ id, component: "planner", severity: "warning", event_type, message: "m", payload, at: "2026-10-27T01:00:00Z" });
const TR = [
  { from_state: null, to_state: "Pending", at: "2026-10-27T00:59:58Z", actor: "ops" },
  { from_state: "Pending", to_state: "Conflict", at: "2026-10-27T01:00:00Z", actor: "planner", reason_code: "path_conflict" },
];

function req(events: unknown, transitions: unknown = TR): RequestDetail {
  return { request_id: "r1", state: "Conflict", transitions, events } as unknown as RequestDetail;
}

describe("conflictRequestId", () => {
  test("가장 최근 path_conflict 이벤트의 상대 id", () => {
    expect(conflictRequestId([ev("path_conflict", { conflict_request_id: "a".repeat(32) }, 1),
                              ev("plan_error", { conflict_request_id: "x" }, 2),
                              ev("path_conflict", { conflict_request_id: OTHER }, 3)])).toBe(OTHER);
  });
  test.each([
    ["배열 아님", { not: "array" }],
    ["이벤트 없음", []],
    ["다른 종류만", [ev("path_overlap_hold", { conflict_request_id: OTHER })]],
    ["payload 없음", [ev("path_conflict", null)]],
    ["id 가 문자열 아님", [ev("path_conflict", { conflict_request_id: 42 })]],
    ["빈 id", [ev("path_conflict", { conflict_request_id: "" })]],
    ["지나치게 긴 id", [ev("path_conflict", { conflict_request_id: "a".repeat(129) })]],
    ["항목이 객체 아님", [null, "x", 3]],
  ])("모양이 틀리면 null — %s", (_name, events) => {
    expect(conflictRequestId(events)).toBeNull();
  });
});

describe("전이 이력의 겹친 요청 링크", () => {
  test("path_conflict 전이 줄에 상대 요청 링크(앞 8자리)와 사유 문구", () => {
    render(<MemoryRouter><ActivitySection req={req([ev("path_conflict", { conflict_request_id: OTHER })])} /></MemoryRouter>);
    const card = screen.getByRole("heading", { name: "전이 이력" }).closest("div")!;
    expect(within(card).getAllByRole("listitem")).toHaveLength(2);
    const link = within(card).getByRole("link", { name: `겹친 요청 ${OTHER.slice(0, 8)}` });
    expect(link.getAttribute("href")).toBe(`/jobs/${OTHER}`);
    expect(within(card).getByText(REASON_MESSAGES.path_conflict)).toBeTruthy();
  });

  test("이벤트가 없으면(기록 실패) 사유만 보이고 링크는 없다", () => {
    render(<MemoryRouter><ActivitySection req={req([])} /></MemoryRouter>);
    expect(screen.getByText(REASON_MESSAGES.path_conflict)).toBeTruthy();
    expect(screen.queryByRole("link", { name: /겹친 요청/ })).toBeNull();
  });

  test("다른 사유의 전이 줄엔 링크를 붙이지 않는다", () => {
    const tr = [{ from_state: "Pending", to_state: "Conflict", at: "2026-10-27T01:00:00Z", reason_code: "resource_conflict" }];
    render(<MemoryRouter><Timeline transitions={tr} conflictRequestId={OTHER} /></MemoryRouter>);
    expect(screen.queryByRole("link")).toBeNull();
  });

  test("링크 경로는 인코딩한다(DB 가 신뢰 경계)", () => {
    render(<MemoryRouter><Timeline transitions={TR} conflictRequestId="a/../b?c" /></MemoryRouter>);
    expect(screen.getByRole("link").getAttribute("href")).toBe(`/jobs/${encodeURIComponent("a/../b?c")}`);
  });

  test("conflictRequestId 없이도(잡 카드 전이) 라우터 없이 그린다", () => {
    render(<Timeline transitions={TR} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });
});

test("path_conflict 는 사유 문구 목록에 있다", () => {
  expect(REASON_MESSAGES.path_conflict).toMatch(/경로가 겹칩니다/);
});
