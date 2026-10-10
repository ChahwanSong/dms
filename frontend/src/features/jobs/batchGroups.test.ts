import { expect, test } from "vitest";
import { BATCH_BLOCK_LABEL, batchBlock, groupBatches, MAX_BATCH_CHILDREN, MAX_BATCH_ITEMS, splitBatchName } from "./batchGroups";
import type { RequestRow } from "../../lib/types";

const BID = "a".repeat(32);
function r(id: string, over: Partial<RequestRow> = {}): RequestRow {
  return {
    request_id: id, operation: "scan", requester_id: "admin", resource_key: "k", priority: "mid", state: "Succeeded",
    created_at: "2026-10-10T00:00:00Z", updated_at: "2026-10-10T00:00:00Z", payload: {}, commit_order: 1,
    batch_id: BID, batch_exists: true, batch_name: "주간", batch_status: "Completed", batch_request_count: 3,
    batch_live_request_count: 0, batch_succeeded_scan_count: 2, ...over,
  };
}

test("batchBlock: 종단 배치 + 끝나지 않은 자식 0 + 상한 이하만 ok — 집합 밖 상태는 진행 중으로 본다(fail-closed)", () => {
  expect(batchBlock(r("1"))).toBe("ok");
  expect(batchBlock(r("1", { batch_status: "Cancelled" }))).toBe("ok");
  for (const s of ["Running", "Previewing", "Weird"]) expect(batchBlock(r("1", { batch_status: s }))).toBe("active");
  // 확인 대기(PreviewReady)는 스스로 끝나지 않는다 -- 따로 말한다(2026-10-11 검증 지적: 「배치 진행 중 · 끝난 뒤에」였다).
  expect(batchBlock(r("1", { batch_status: "PreviewReady" }))).toBe("awaiting");
  expect(batchBlock(r("1", { batch_status: "PreviewReady", batch_request_count: MAX_BATCH_CHILDREN + 1 }))).toBe("awaiting");
  expect(batchBlock(r("1", { batch_status: null }))).toBe("active");          // 행은 있다는데 상태 모름
  expect(batchBlock(r("1", { batch_live_request_count: 2 }))).toBe("live");
  expect(batchBlock(r("1", { batch_request_count: MAX_BATCH_CHILDREN }))).toBe("ok");
  expect(batchBlock(r("1", { batch_request_count: MAX_BATCH_CHILDREN + 1 }))).toBe("too_large");
  // 배치 행이 없는 묶음(기록만 지워짐)은 상태가 없어도 된다.
  expect(batchBlock(r("1", { batch_exists: false, batch_status: null, batch_name: null }))).toBe("ok");
});

test("batchBlock: 요약이 하나라도 없거나 id 형식이 이상하면 unknown(모르면 고르지 못한다)", () => {
  for (const k of ["batch_exists", "batch_request_count", "batch_live_request_count", "batch_succeeded_scan_count"] as const) {
    const row = r("1");
    delete row[k];
    expect(batchBlock(row), k).toBe("unknown");
  }
  for (const bid of ["", "A".repeat(32), "a".repeat(31), "g".repeat(32)]) expect(batchBlock(r("1", { batch_id: bid }))).toBe("unknown");
  expect(batchBlock(r("1", { batch_id: null }))).toBe("unknown");
});

test("groupBatches: batch_id 별 첫 행 값 · 단건 제외 · 이름 없으면 id 12자 · unknown 묶음엔 수가 없다(0 으로 짓지 않는다)", () => {
  const other = "b".repeat(32);
  const g = groupBatches([
    r("1"), r("2", { batch_request_count: 99 }), r("3", { batch_id: null }),
    r("4", { batch_id: other, batch_name: "  ", operation: "sync" }), r("5", { batch_id: "" }),
  ]);
  expect([...g.keys()]).toEqual([BID, other, ""]);
  const a = g.get(BID)!;
  expect(a.block).toBe("ok");
  if (a.block === "ok") expect([a.label, a.named, a.request_count, a.scans]).toEqual(["주간", true, 3, 2]);
  const b = g.get(other)!;
  expect([b.label, b.named, b.operation]).toEqual([other.slice(0, 12), false, "sync"]);
  const odd = g.get("")!;
  expect(odd.block).toBe("unknown");
  expect("request_count" in odd).toBe(false);
});

test("groupBatches: 불러온 행이 하나라도 끝나지 않았으면 서버 요약이 0 이어도 live(행 상태로 한 번 더) — active·unknown 은 그대로", () => {
  // 2026-10-10 검증 지적: 목록 API 의 행 SELECT 와 요약 집계는 한 트랜잭션이 아니라 같은 응답에서 행은 Running, 요약은 0
  // 일 수 있다 -- 그 묶음을 고를 수 있게 그리면 「진행 중인 작업은 선택할 수 없습니다」와 모순된다.
  const big = "c".repeat(32), act = "d".repeat(32), unk = "e".repeat(32);
  const g = groupBatches([
    r("1"), r("2", { state: "Running" }),                                       // 첫 행은 종단이어도 묶음 전체가 live
    r("3", { batch_id: big, batch_request_count: MAX_BATCH_CHILDREN + 1 }), r("4", { batch_id: big, state: "Pending" }),
    r("5", { batch_id: act, batch_status: "Running", state: "Running" }),
    r("6", { batch_id: unk, batch_exists: undefined, state: "Running" }),
    r("7", { batch_id: "f".repeat(32), state: "Conflict" }),                    // Conflict 는 요청 종단
  ]);
  expect(g.get(BID)!.block).toBe("live");
  // 1000개 초과가 끝나지 않은 작업보다 앞선 사유다 -- 서버(delete_batch)의 판정 순서와 같다(2026-10-11: 상한을 넘는 배치는
  // 서버가 세기를 멈춰 끝나지 않은 자식 수를 모른다).
  expect(g.get(big)!.block).toBe("too_large");
  expect(g.get(act)!.block).toBe("active");
  expect(g.get(unk)!.block).toBe("unknown");
  expect(g.get("f".repeat(32))!.block).toBe("ok");
});

test("batchBlock: 서버가 상한에서 세기를 멈춘 배치(capped)는 too_large -- 끝나지 않은 자식·성공 scan 수가 null(모름)이어도", () => {
  // 2026-10-11 검증 지적: 목록 요약이 폴링마다 큰 배치의 자식 전부를 셌다 -- 서버는 상한 + 1 에서 멈추고 나머지 수를 null 로.
  const capped = { batch_request_count: MAX_BATCH_CHILDREN + 1, batch_request_count_capped: true,
                   batch_live_request_count: null, batch_succeeded_scan_count: null };
  expect(batchBlock(r("1", capped))).toBe("too_large");
  expect(batchBlock(r("1", { ...capped, batch_status: "Running" }))).toBe("active");     // 진행 중이 더 앞선 사유
  // capped 가 아닌데 수가 null 이면 모른다(fail-closed).
  expect(batchBlock(r("1", { batch_live_request_count: null }))).toBe("unknown");
  const g = groupBatches([r("1", capped)]).get(BID)!;
  expect(g.block).toBe("too_large");
  expect("request_count" in g).toBe(false);                                    // 고를 수 없는 묶음엔 수를 싣지 않는다
});

test("batchBlock: 항목이 상한(10000)을 넘는 배치도 too_large -- 항목 수를 모르면(null·부재) 막지 않는다", () => {
  // 2026-10-11 검증 지적: 큰 CSV 배치를 일찍 취소한 모양(자식 1개·항목 10001개)이 선택 가능으로 보였다가 서버만
  // batch_delete_too_large 를 냈다 -- 목록 요약의 batch_item_count 로 미리 잠근다.
  expect(batchBlock(r("1", { batch_item_count: MAX_BATCH_ITEMS }))).toBe("ok");
  expect(batchBlock(r("1", { batch_item_count: MAX_BATCH_ITEMS + 1, batch_request_count: 1 }))).toBe("too_large");
  expect(batchBlock(r("1", { batch_item_count: MAX_BATCH_ITEMS + 1, batch_status: "Running" }))).toBe("active");
  expect(batchBlock(r("1", { batch_item_count: MAX_BATCH_ITEMS + 1, batch_live_request_count: 1 }))).toBe("too_large");
  expect(batchBlock(r("1", { batch_item_count: null }))).toBe("ok");          // 모름(배치 행 없는 묶음) -- 서버가 판정
  expect(batchBlock(r("1", { batch_item_count: 0 }))).toBe("ok");             // 0 은 정상값
});

test("BATCH_BLOCK_LABEL: live 는 그 배치의 다른 작업 얘기다 -- 「작업 진행 중」(같은 줄 Succeeded 와 모순)이 아니다", () => {
  expect(BATCH_BLOCK_LABEL.live).toBe("미완료 작업 있음");
  expect(BATCH_BLOCK_LABEL.too_large).toBe("삭제 상한 초과");               // 자식·항목 어느 상한이든
  expect(Object.values(BATCH_BLOCK_LABEL)).not.toContain("작업 진행 중");
  // 확인 대기는 배치 화면(batchStatusLabel)의 「확인 대기」와 같은 말.
  expect(BATCH_BLOCK_LABEL.awaiting).toBe("배치 확인 대기");
  expect(groupBatches([r("1", { batch_status: "PreviewReady" })]).get(BID)!.block).toBe("awaiting");
});

test("BATCH_BLOCK_LABEL: 모든 사유가 6rem·text-xs 한 줄에 든다(두 줄로 접히면 상태가 바뀔 때 행 높이가 바뀐다)", () => {
  // text-xs(12px)에서 한글 1자 ≈ 12px, 공백 ≈ 4px, 그 밖 ≈ 7px -- 96px 안. 실 기하(말줄임 없음)는 e2e E7 이 잰다.
  for (const v of Object.values(BATCH_BLOCK_LABEL)) {
    const px = [...v].reduce((n, ch) => n + (/[가-힣]/u.test(ch) ? 12 : /\s/u.test(ch) ? 4 : 7), 0);
    expect(px, v).toBeLessThanOrEqual(96);
  }
});

test("splitBatchName: 짧은 이름은 그대로, 긴 이름은 끝(가르는 부분)을 뒤 조각으로 -- 낱말 경계 우선", () => {
  expect(splitBatchName("e7 배치")).toBeNull();
  expect(splitBatchName("monthly")).toBeNull();
  // 접두가 같은 1차·2차 배치는 뒤 조각이 갈린다(2026-10-11 검증 지적: 두 줄 line-clamp 는 둘 다 「2026-10 / 프로젝트 A…」).
  const a = splitBatchName("2026-10 프로젝트 A 아카이브 이관(1차)")!;
  const b = splitBatchName("2026-10 프로젝트 A 아카이브 이관(2차)")!;
  expect([a.head, a.tail]).toEqual(["2026-10 프로젝트 A 아카이브", "이관(1차)"]);
  expect(b.tail).toBe("이관(2차)");
  expect(splitBatchName("d161 실행 중 추가 실증 1007c")!.tail).toBe("실증 1007c");
  // 한 줄 폭(한글 ~5자)을 넘으면 두 줄 -- 이름 전부가 앞·뒤 조각으로 보인다.
  expect(splitBatchName("성장 모니터링")).toEqual({ head: "성장", tail: "모니터링" });
  // 띄어쓰기 없는 긴 이름은 글자 경계에서 끝 10 폭.
  const solid = splitBatchName("monthly-report-2026-10-archive")!;
  expect(solid.tail).toBe("10-archive");
  expect(solid.head + solid.tail).toBe("monthly-report-2026-10-archive");
});
