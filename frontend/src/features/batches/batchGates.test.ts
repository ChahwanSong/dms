import { expect, test } from "vitest";
import { batchGates, STORAGE_EMPTY_CSV_BAR, STORAGE_FIRST_BAR } from "./batchGates";
import type { BatchGatedStep, BatchGateInput, BatchOperation } from "./batchGates";

// 배치 생성 단계별 등록의 게이트 순수 함수(2026-11-26, design-wizard §1-4·§6-3). 잎 조합 **전수**로 세 성질을 본다 -- 검토 단계에서는
// 게이트가 참인 상태가 UI 로 거의 관찰되지 않아(앞 단계가 막히면 검토에 못 닿는다) 화면 시험만으로는 「합집합 = 옛 생성 게이트」를
// 지킬 수 없다.

// 옛 BatchCreate(a13ebab9 :146-204·:405-416)의 itemsValid·blocked·hints 를 글자 그대로 옮긴 기준 구현 -- 새 함수가 이것과 갈라지면
// 빨갛다.
function legacy(i: BatchGateInput): { blocked: boolean; hints: string[]; itemsValid: boolean } {
  const rowsComplete = i.rowsCount >= 1 && i.incompleteRows === 0;
  const itemsValid = i.storageChosen && rowsComplete && i.csvErrorCount === 0
    && i.rmPathErrorCount === 0 && i.rmOverlapCount === 0;
  const blocked = i.createPending || i.parsePending || !itemsValid || i.controlsInvalid || i.storagesError
    || i.rmAutoAckMissing;
  const hints: string[] = [];
  if (i.createPending) hints.push("생성 중…");
  if (i.storagesError) hints.push("스토리지 목록 조회 실패 — 새로고침 후 다시 시도하세요");
  if (!i.storageChosen) hints.push(i.op === "sync" ? "소스·목적지 스토리지를 고르세요" : "스토리지를 고르세요");
  if (i.rowsCount === 0) hints.push("항목을 1개 이상 추가하세요");
  else if (i.incompleteRows > 0) hints.push(`경로가 빈 항목 ${i.incompleteRows}개를 채우거나 지우세요`);
  if (i.parsePending) hints.push("CSV 검사 중…");
  if (i.csvErrorCount > 0) hints.push("CSV 오류를 고친 뒤 다시 반영하세요");
  if (i.rmPathErrorCount > 0) hints.push(`삭제할 수 없는 경로 ${i.rmPathErrorCount}개를 고치세요`);
  if (i.rmOverlapCount > 0) hints.push(`겹치는 삭제 경로 ${i.rmOverlapCount}개를 정리하세요`);
  if (i.controlsInvalid) hints.push("빨간 안내가 붙은 옵션·실행 제어 값을 고치세요");
  if (i.rmAutoAckMissing) hints.push("자동 실행으로 삭제하려면 경고를 읽고 동의를 체크하세요");
  return { blocked, hints, itemsValid };
}

const BOOL_LEAVES = [
  "createPending", "parsePending", "storagesError", "storageChosen", "storageFromCsv", "controlsInvalid", "rmAutoAckMissing",
] as const;
const COUNT_LEAVES = ["rowsCount", "incompleteRows", "csvErrorCount", "rmPathErrorCount", "rmOverlapCount"] as const;
const OPS: BatchOperation[] = ["scan", "sync", "rm"];

function* allInputs(): Generator<BatchGateInput> {
  const leaves = [...BOOL_LEAVES, ...COUNT_LEAVES];
  for (let mask = 0; mask < 1 << leaves.length; mask++) {
    const b = Object.fromEntries(BOOL_LEAVES.map((k, n) => [k, (mask & (1 << n)) !== 0]));
    const c = Object.fromEntries(COUNT_LEAVES.map((k, n) => [k, (mask & (1 << (n + BOOL_LEAVES.length))) !== 0 ? 1 : 0]));
    for (const op of OPS) yield { ...b, ...c, op } as BatchGateInput;
  }
}

test("잎 조합 전수: 합집합 불변식 · 막힌 단계엔 바 이유 · 검토 목록 = 옛 hints · 최종 게이트 = 옛 blocked", () => {
  const failures: string[] = [];
  let n = 0;
  for (const i of allInputs()) {
    n += 1;
    const g = batchGates(i);
    const old = legacy(i);
    // ① blocked 식에서 createPending 항만 뺀 것
    const union = g.gate.storage || g.gate.items || g.gate.controls;
    if (union !== (i.parsePending || !old.itemsValid || i.controlsInvalid || i.storagesError || i.rmAutoAckMissing))
      failures.push(`union ${JSON.stringify(i)}`);
    if (g.gate.kind) failures.push(`kind ${JSON.stringify(i)}`);
    if (g.blocked !== old.blocked) failures.push(`blocked ${JSON.stringify(i)}`);
    if (g.blocked !== (i.createPending || union)) failures.push(`blocked=pending||union ${JSON.stringify(i)}`);
    if (g.itemsValid !== old.itemsValid) failures.push(`itemsValid ${JSON.stringify(i)}`);
    // ② 막힌 단계의 바에는 늘 이유가 있다
    for (const s of ["kind", "storage", "items", "controls"] as BatchGatedStep[]) {
      if (!g.gate[s]) continue;
      if (!g.hintItems.some((h) => h.step === s && h.scope !== "review")) failures.push(`no bar reason ${s} ${JSON.stringify(i)}`);
      if (g.barReason(s) === null) failures.push(`barReason null ${s} ${JSON.stringify(i)}`);
    }
    // ③ 검토 목록 = 옛 hints(순서·문장)
    if (JSON.stringify(g.hints) !== JSON.stringify(old.hints)) failures.push(`hints ${JSON.stringify(i)}`);
    if (g.barReason("review") !== (old.hints[0] ?? null)) failures.push(`review reason ${JSON.stringify(i)}`);
    if (failures.length > 20) break;
  }
  expect(failures).toEqual([]);
  expect(n).toBe((1 << (BOOL_LEAVES.length + COUNT_LEAVES.length)) * OPS.length);
});

const base: BatchGateInput = {
  createPending: false, parsePending: false, storagesError: false, storageChosen: true, storageFromCsv: false, rowsCount: 1,
  incompleteRows: 0, csvErrorCount: 0, rmPathErrorCount: 0, rmOverlapCount: 0, controlsInvalid: false, rmAutoAckMissing: false,
  op: "scan",
};

test("단계 태그: 스토리지 → 스토리지, 행·CSV·rm 경로 → 항목, 옵션·동의 → 옵션·실행 제어, 「생성 중…」 → 검토", () => {
  const g = batchGates({ ...base, createPending: true, storageChosen: false, incompleteRows: 1, csvErrorCount: 1,
                         controlsInvalid: true, rmAutoAckMissing: true, op: "rm" });
  expect(g.hintItems.map((h) => [h.step, h.scope, h.text])).toEqual([
    ["review", "both", "생성 중…"],
    ["storage", "both", "스토리지를 고르세요"],
    ["items", "bar", STORAGE_FIRST_BAR],
    ["items", "both", "경로가 빈 항목 1개를 채우거나 지우세요"],
    ["items", "both", "CSV 오류를 고친 뒤 다시 반영하세요"],
    ["controls", "both", "빨간 안내가 붙은 옵션·실행 제어 값을 고치세요"],
    ["controls", "both", "자동 실행으로 삭제하려면 경고를 읽고 동의를 체크하세요"],
  ]);
  expect(g.gate).toEqual({ kind: false, storage: true, items: true, controls: true });
  expect(g.barReason("storage")).toBe("스토리지를 고르세요");
  expect(g.barReason("items")).toBe(STORAGE_FIRST_BAR);
  expect(g.barReason("controls")).toBe("빨간 안내가 붙은 옵션·실행 제어 값을 고치세요");
  expect(g.barReason("review")).toBe("생성 중…");
  // 바 전용 줄은 검토 목록에 없다(옛 hints 그대로)
  expect(g.hints).not.toContain(STORAGE_FIRST_BAR);
});

test("「스토리지를 CSV 로 정하기」: 스토리지 단계는 통과, 항목 단계 바는 「스토리지가 비어 있습니다 …」, 생성은 그대로 막힌다", () => {
  const g = batchGates({ ...base, storageChosen: false, storageFromCsv: true, op: "sync" });
  expect(g.gate.storage).toBe(false);
  expect(g.gate.items).toBe(true);
  expect(g.blocked).toBe(true);
  expect(g.barReason("items")).toBe(STORAGE_EMPTY_CSV_BAR);
  expect(g.hints).toEqual(["소스·목적지 스토리지를 고르세요"]);
  // 조회 실패는 탈출구와 무관하게 스토리지 단계를 막는다
  expect(batchGates({ ...base, storageChosen: false, storageFromCsv: true, storagesError: true }).gate.storage).toBe(true);
});

test("다 채우면 어느 단계도 막히지 않고 이유가 없다", () => {
  const g = batchGates(base);
  expect(g.gate).toEqual({ kind: false, storage: false, items: false, controls: false });
  expect(g.blocked).toBe(false);
  expect(g.hints).toEqual([]);
  expect(g.barReason("review")).toBeNull();
});
