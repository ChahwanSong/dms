import { expect, test } from "vitest";
import { blockText, fallbackLimitsLine, limitsLine, parseLimits } from "./deleteLimits";
import { REASON_MESSAGES } from "./api";

// 2026-11-19 사용자 요청 「포탈에서 상한 값도 삭제 시 보여지고 값을 넘어서는 요청에 대해서 삭제 불가 메시지」 -- 숫자는 서버 값,
// 미리 판정과 서버 거절이 같은 포매터.
const L = { requests_per_call: 200, batches_per_call: 10, batch_request_count: 50000, batch_item_count: 10000,
            call_request_count: 100000, sync_request_count: 500, list_count_cap: 5000 };

test("parseLimits: 숫자 키가 다 있어야 한다 -- 하나라도 없거나 숫자가 아니면 null(모름, 0 으로 짓지 않는다)", () => {
  expect(parseLimits(L)).toEqual(L);
  expect(parseLimits({ ...L, extra: "x" })).toEqual(L);
  expect(parseLimits(undefined)).toBeNull();
  expect(parseLimits(null)).toBeNull();
  expect(parseLimits({ ...L, call_request_count: "100000" })).toBeNull();
  const { sync_request_count: _, ...missing } = L;
  expect(parseLimits(missing)).toBeNull();
  expect(parseLimits({ ...L, sync_request_count: 0 })).toEqual({ ...L, sync_request_count: 0 });   // 0 은 정상값
  expect(parseLimits({ ...L, batch_request_count: Number.NaN })).toBeNull();
});

test("limitsLine: 천 단위 구분 · 서버 값 그대로", () => {
  expect(limitsLine(L)).toBe("한 번에 작업 200개·배치 10개 · 배치당 작업 50,000개·항목 10,000개 · 합계 작업 100,000개");
  expect(limitsLine({ ...L, batch_request_count: 20 })).toContain("배치당 작업 20개");
  expect(fallbackLimitsLine(200, 10, 5000, 10000)).toBe("한 번에 작업 200개·배치 10개 · 배치당 작업 5,000개·항목 10,000개");
  expect(fallbackLimitsLine(200, 10)).toBe("한 번에 작업 200개·배치 10개까지(배치당 상한은 서버가 판정합니다)");
});

test("blockText: 상한 사유는 숫자를 끼운다 -- count 를 알면 정확히, 모르면 하한(이상)", () => {
  expect(blockText("batch_delete_too_large", { limit: 50000, count: 60000 }))
    .toBe("삭제 불가 — 작업 60,000개가 한 번에 지울 수 있는 상한 50,000개를 넘습니다");
  expect(blockText("batch_delete_too_large", { limit: 50000, count: null, count_at_least: 50001 }))
    .toBe("삭제 불가 — 작업이 한 번에 지울 수 있는 상한 50,000개를 넘습니다(50,001개 이상)");
  expect(blockText("batch_delete_too_many_items", { limit: 10000, count: 10001 }))
    .toBe("삭제 불가 — 항목 10,001개가 한 번에 지울 수 있는 상한 10,000개를 넘습니다");
  expect(blockText("batch_delete_call_budget", { limit: 100000, count: 120000 }))
    .toBe("삭제 불가 — 고른 배치의 작업 합계 120,000개가 한 번에 지울 수 있는 상한 100,000개를 넘습니다 — 나눠서 지우세요");
  expect(blockText("batch_delete_call_budget", { limit: 100000, count: 120000 }, { server: true }))
    .toBe("삭제 불가 — 고른 배치의 작업 합계 120,000개가 한 번에 지울 수 있는 상한 100,000개를 넘어 이 배치는 이번에 지우지 않았습니다 — 다시 삭제하세요");
  expect(blockText("delete_selection_too_large", { limit: 200, count: 201 }))
    .toBe("삭제 불가 — 단일 작업 201개를 골랐습니다 · 한 번에 200개까지 지울 수 있습니다");
  expect(blockText("delete_batch_selection_too_large", { limit: 10, count: 11 }))
    .toBe("삭제 불가 — 배치 11개를 골랐습니다 · 한 번에 10개까지 지울 수 있습니다");
});

test("blockText: 숫자가 없으면(옛 서버) 정적 문구 · 상한이 아닌 사유도 같은 머리말", () => {
  for (const r of ["batch_delete_too_large", "batch_delete_too_many_items", "batch_delete_call_budget",
                   "delete_selection_too_large", "delete_batch_selection_too_large", "request_not_deletable"]) {
    expect(blockText(r)).toBe(`삭제 불가 — ${REASON_MESSAGES[r]}`);
    expect(blockText(r, { limit: null, count: null })).toBe(`삭제 불가 — ${REASON_MESSAGES[r]}`);
  }
  expect(blockText("batch_deleting")).toBe(`삭제 불가 — ${REASON_MESSAGES.batch_deleting}`);
});
