import { describe, expect, test } from "vitest";
import { BATCH_ITEMS_PAGE_MAX, BATCH_ITEMS_PAGE_SIZE, batchStorageText, chipStatus, directToolsOf, headerOf, itemMatches,
         itemPageCount, pageOf, parseItemsPage, payloadsOf } from "./batchView";
import type { BatchItem } from "../../lib/types";

const it_ = (seq: number, over: Partial<BatchItem> = {}): BatchItem => ({
  seq, payload: { storage: "s1", target: `team/T${seq}` }, status: "Queued", request_id: null, reason_code: null, ...over });
const row = { batch_id: "b1", operation: "scan", status: "Running", max_concurrency: 1, item_count: 3,
              succeeded_count: 0, failed_count: 0, note: null, created_at: "" };

describe("headerOf -- 새 파드(view=summary)는 그대로, 옛 파드(전체 응답)는 items 에서 같은 모양을 파생한다", () => {
  test("새 파드 요약: 집계 키를 그대로 읽고 items 는 버린다 · null 은 0 으로 접지 않는다", () => {
    const h = headerOf({ ...row, item_total: 2, status_counts: { Queued: 2, Failed: 0 }, first_payload: { storage: "s1" },
                         executing_count: null, direct_tools: { tools_known: 1, nsync: 1, undecided: 0 },
                         storage: "s1" });
    expect(h.item_total).toBe(2);
    expect(h.status_counts).toEqual({ Queued: 2, Failed: 0 });
    expect(h.executing_count).toBeNull();                       // 세지 않았다(확인 단계 아님) -- 0 이 아니다
    expect(h.direct_tools).toEqual({ tools_known: 1, nsync: 1, undecided: 0 });
    expect(h.storage).toBe("s1");
    expect("items" in h).toBe(false);
  });
  test("새 파드 요약의 깨진 모양(변조): 집계는 모름으로 접는다", () => {
    const h = headerOf({ ...row, item_total: 0, status_counts: null as never, first_payload: "x" as never,
                         executing_count: -1, direct_tools: { tools_known: "1" } as never });
    expect(h.item_total).toBe(0);                               // 0 은 정상값(비운 배치)
    expect(h.status_counts).toEqual({});
    expect(h.first_payload).toBeNull();
    expect(h.executing_count).toBeNull();
    expect(h.direct_tools).toBeNull();
  });
  test("옛 파드: items 에서 수·상태별 수·첫 payload·실행 중·direct 를 센다", () => {
    const items = [it_(4, { status: "Materialized", job_state: "Executing", job_tool: "nsync" }),
                   it_(5, { status: "Rejected" }), it_(6)];
    const h = headerOf({ ...row, options: { direct: true }, items });
    expect(h.item_total).toBe(3);
    expect(h.status_counts).toEqual({ Materialized: 1, Rejected: 1, Queued: 1 });
    expect(h.first_payload).toEqual({ storage: "s1", target: "team/T4" });
    expect(h.executing_count).toBe(1);
    expect(h.direct_tools).toEqual({ tools_known: 1, nsync: 1, undecided: 1 });   // 거부로 끝난 #5 는 미정이 아니다
  });
  test("옛 파드인데 items 마저 없으면 항목 수는 배치 행의 카운터(0 을 지어내지 않는다)", () => {
    expect(headerOf({ ...row, item_count: 7 }).item_total).toBe(7);
  });
});

describe("pageOf -- 새 봉투는 그대로, 옛 전체 응답은 같은 규칙으로 거르고 자른다", () => {
  test("새 봉투(total·items·filters)", () => {
    const env = { total: 250, offset: 100, limit: 100, items: [it_(100)], filters: { q: null, status: null } };
    expect(pageOf(env, { page: 2, q: "", status: null })).toBe(env);
  });
  test("옛 전체 응답: 토큰 AND·대소문자 무시(경로만) + 상태 정확 일치 + 쪽 자르기", () => {
    const items = Array.from({ length: 250 }, (_, i) => it_(i, { status: i % 2 === 0 ? "Queued" : "Failed" }));
    const all = { ...row, items };
    const p2 = pageOf(all, { page: 2, q: "", status: null });
    expect(p2.total).toBe(250);
    expect(p2.items.map((x) => x.seq)).toEqual(Array.from({ length: 100 }, (_, i) => 100 + i));
    const hit = pageOf(all, { page: 1, q: "TEAM t1 →", status: "Failed" });
    expect(hit.items.every((x) => x.status === "Failed" && String(x.payload.target).includes("T1"))).toBe(true);
    expect(hit.total).toBe(hit.items.length);
  });
  test("목록이 아닌 응답은 오류(빈 쪽을 지어내지 않는다)", () => {
    expect(() => pageOf({ total: "x" }, { page: 1, q: "", status: null })).toThrow();
  });
});

test("payloadsOf: 목록이 아니면 오류 -- 「항목 없음」·「겹침 없음」을 지어내지 않는다", () => {
  expect(payloadsOf({ items: [{ seq: 0, payload: {}, status: "Queued" }] })).toHaveLength(1);
  expect(() => payloadsOf({})).toThrow();
});

test("itemMatches: sync 는 출발·도착 경로 어느 쪽이든, 문자열 아닌 경로는 안 맞는다", () => {
  const s = it_(0, { payload: { source_storage: "a", source: "x/models", destination_storage: "b", destination: "y/run" } });
  expect(itemMatches(s, "MODELS run", null)).toBe(true);
  expect(itemMatches(s, "a", null)).toBe(false);                  // 스토리지 이름은 칸이 아니다
  expect(itemMatches(it_(1, { payload: { target: 7 } }), "7", null)).toBe(false);
});

test("directToolsOf: direct 가 명시 true 일 때만 센다", () => {
  expect(directToolsOf({}, [it_(0)])).toBeNull();
  expect(directToolsOf({ direct: "yes" }, [it_(0)])).toBeNull();
  expect(directToolsOf({ direct: true }, [it_(0, { preview_summary: { tool: "dsync" } as never })]))
    .toEqual({ tools_known: 1, nsync: 0, undecided: 0 });
});

test("batchStorageText: scan·rm 은 이름, sync 는 화살표, 모름은 null, 빈 이름은 (이름 없음)", () => {
  expect(batchStorageText({ operation: "scan", storage: "s1" })).toBe("s1");
  expect(batchStorageText({ operation: "rm", storage: null })).toBeNull();
  expect(batchStorageText({ operation: "rm", storage: "" })).toBe("(이름 없음)");
  expect(batchStorageText({ operation: "sync", source_storage: "a", destination_storage: "a" })).toBe("a → a");
  expect(batchStorageText({ operation: "sync", source_storage: "a", destination_storage: null })).toBe("a → —");
  expect(batchStorageText({ operation: "sync" })).toBeNull();
});

test("chipStatus·itemPageCount: 주소의 모르는 상태는 전체로, 쪽 수는 total 을 모르면 null", () => {
  expect(chipStatus("Failed")).toBe("Failed");
  expect(chipStatus("bogus")).toBeNull();
  expect(chipStatus(null)).toBeNull();
  expect(itemPageCount(undefined)).toBeNull();
  expect(itemPageCount(0)).toBe(1);
  expect(itemPageCount(100)).toBe(1);
  expect(itemPageCount(101)).toBe(2);
});

test("parseItemsPage: 1 이상 정수만, 상한 위는 상한으로 접는다 -- offset 이 지수 표기(1e+21)가 돼 서버 422 로 굳지 않게", () => {
  expect(parseItemsPage(null)).toBe(1);
  expect(parseItemsPage("3")).toBe(3);
  for (const bad of ["0", "-2", "abc", "1.5", "", "1e3"]) expect(parseItemsPage(bad)).toBe(1);
  expect(parseItemsPage("10000000000000000000")).toBe(BATCH_ITEMS_PAGE_MAX);
  expect(parseItemsPage("9".repeat(400))).toBe(BATCH_ITEMS_PAGE_MAX);              // Number → Infinity 도 상한
  // 상한의 offset 은 지수 표기 없이 정수 문자열이다(서버 offset: int 가 받는다)
  expect(String((BATCH_ITEMS_PAGE_MAX - 1) * BATCH_ITEMS_PAGE_SIZE)).toMatch(/^\d+$/);
});
