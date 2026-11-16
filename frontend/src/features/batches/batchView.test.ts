import { expect, test } from "vitest";
import { directToolsOf, headerOf, itemMatches, pageOf, payloadsOf } from "./batchView";
import type { BatchItem, BatchSummary } from "../../lib/types";

const row = { batch_id: "b1", operation: "sync", status: "PreviewReady", max_concurrency: 2, item_count: 9,
              succeeded_count: 0, failed_count: 0, note: null, created_at: "" };
const item = (seq: number, over: Partial<BatchItem> = {}): BatchItem => ({
  seq, payload: { source_storage: "s1", source: `src${seq}`, destination_storage: "s2", destination: `dst${seq}` },
  status: "Materialized", request_id: `r${seq}`, reason_code: null, ...over });

test("headerOf(새 서버): 서버 집계를 그대로 — executing_count null·status_counts 의 0 을 접지 않는다, items 는 떼어 낸다", () => {
  const h = headerOf({ ...row, item_total: 3, status_counts: { Queued: 0, Failed: 3 }, first_payload: { storage: "s1" },
                       executing_count: null, direct_tools: null, items: [item(0)] } as BatchSummary);
  expect(h.item_total).toBe(3);
  expect(h.status_counts).toEqual({ Queued: 0, Failed: 3 });
  expect(h.executing_count).toBeNull();
  expect(h.first_payload).toEqual({ storage: "s1" });
  expect("items" in h).toBe(false);
});

test("headerOf(새 서버): 항목 0개는 0(모름이 아님), 0 인 executing_count 도 0 — 모양이 틀린 direct_tools 는 null", () => {
  const h = headerOf({ ...row, item_total: 0, status_counts: {}, first_payload: null, executing_count: 0,
                       direct_tools: { tools_known: "2", nsync: 1, undecided: 0 } } as unknown as BatchSummary);
  expect(h.item_total).toBe(0);
  expect(h.executing_count).toBe(0);
  expect(h.direct_tools).toBeNull();
  expect(h.first_payload).toBeNull();
});

test("headerOf(옛 파드 = item_total 없음): items 에서 수·상태별 수·첫 payload·실행 중 수·direct 셈을 파생한다", () => {
  const h = headerOf({ ...row, options: { direct: true }, items: [
    item(3, { job_state: "Executing", job_tool: "nsync" }),
    item(5, { status: "Queued", request_id: null }),
    item(7, { status: "Rejected" }),
  ] } as BatchSummary);
  expect(h.item_total).toBe(3);
  expect(h.status_counts).toEqual({ Materialized: 1, Queued: 1, Rejected: 1 });
  expect(h.first_payload).toEqual(item(3).payload);
  expect(h.executing_count).toBe(1);
  // 도구가 확인된 1개(nsync) · 미정 1개(Queued) — 거부로 끝난 항목은 미정이 아니다
  expect(h.direct_tools).toEqual({ tools_known: 1, nsync: 1, undecided: 1 });
});

test("headerOf(옛 파드): items 마저 없는 모양이면 항목 수는 배치 행 카운터 — 0 을 지어내지 않는다", () => {
  const h = headerOf({ ...row } as BatchSummary);
  expect(h.item_total).toBe(9);
  expect(h.first_payload).toBeNull();
});

test("directToolsOf: direct 가 명시 true 가 아니면 null, 도구는 job_tool 먼저(없으면 미리보기 v2 의 tool)", () => {
  expect(directToolsOf({ direct: "true" }, [item(0, { job_tool: "nsync" })])).toBeNull();
  expect(directToolsOf(undefined, [item(0, { job_tool: "nsync" })])).toBeNull();
  expect(directToolsOf({ direct: true }, [
    item(0, { job_tool: "dsync", preview_summary: { tool: "nsync", detail_version: 2 } as never }),
    item(1, { preview_summary: { tool: "nsync", detail_version: 2 } as never }),
    item(2, { status: "Failed" }),
  ])).toEqual({ tools_known: 2, nsync: 1, undecided: 0 });
});

test("itemMatches: q 는 target·source·destination 부분 문자열(대소문자 구분), status 는 정확 일치, 문자열 아닌 경로는 안 맞는다", () => {
  const it = item(0);
  expect(itemMatches(it, "", null)).toBe(true);
  expect(itemMatches(it, "dst0", null)).toBe(true);
  expect(itemMatches(it, "SRC0", null)).toBe(false);
  expect(itemMatches(it, "s1", null)).toBe(false);                    // 스토리지 이름은 검색 대상이 아니다
  expect(itemMatches(it, "", "Materialized")).toBe(true);
  expect(itemMatches(it, "", "Unknown")).toBe(false);
  expect(itemMatches(item(1, { payload: { target: 7 } }), "7", null)).toBe(false);
});

test("pageOf: 새 서버의 쪽은 그대로, 옛 파드 전체 응답은 같은 규칙으로 거르고 100개씩 자른다", () => {
  const fresh = { total: 1, offset: 0, limit: 100, items: [item(0)] };
  expect(pageOf(fresh, { page: 1, q: "", status: null })).toBe(fresh);
  const old = { ...row, items: Array.from({ length: 250 }, (_, i) => item(i, { status: i % 2 ? "Failed" : "Succeeded" })) };
  const p2 = pageOf(old, { page: 2, q: "", status: null });
  expect(p2.total).toBe(250);
  expect(p2.items.map((it) => it.seq)).toEqual(Array.from({ length: 100 }, (_, i) => 100 + i));
  const failed = pageOf(old, { page: 1, q: "src1", status: "Failed" });
  // src1·src1x·src1xx 중 홀수(Failed)만
  expect(failed.items.every((it) => it.status === "Failed" && String(it.payload.source).includes("src1"))).toBe(true);
  expect(failed.total).toBe(failed.items.length);
  expect(failed.total).toBeGreaterThan(0);
  expect(pageOf(old, { page: 9, q: "", status: null }).items).toEqual([]);   // 범위 밖 쪽 = 빈 쪽(total 은 그대로)
  expect(() => pageOf({ detail: "x" }, { page: 1, q: "", status: null })).toThrow();
});

test("payloadsOf: items 목록을 읽고, 목록이 아니면 오류 — 빈 목록으로 접어 「항목 없음」을 지어내지 않는다", () => {
  expect(payloadsOf({ items: [{ seq: 0, payload: { target: "a" }, status: "Queued" }] })).toHaveLength(1);
  expect(payloadsOf({ ...row, items: [item(0)] })).toHaveLength(1);           // 옛 파드 전체 응답도 같은 자리
  expect(() => payloadsOf({ items: null })).toThrow();
  expect(() => payloadsOf(null)).toThrow();
});
