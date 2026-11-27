import { expect, test } from "vitest";
import { submitGates } from "./submitGates";
import type { GatedStep, SubmitGateInput, SubmitOperation } from "./submitGates";

// 단일 작업 단계별 등록의 게이트 순수 함수(dms-k8s 219e8870 이식, 2026-11-27). 잎 조합 **전수**로 세 성질을 본다 -- 검토 단계에서는
// 게이트가 참인 상태가 UI 로 거의 관찰되지 않아(앞 단계가 막히면 검토에 못 닿는다) 화면 시험만으로는 「합집합 = 옛 제출 게이트」를
// 지킬 수 없다.

// 옛 SubmitJob(07519d57 :185-221·:317-328)의 blocked·hints 를 글자 그대로 옮긴 기준 구현 -- 새 함수가 이것과 갈라지면 빨갛다.
// 잎 이름만 바꿨다: storagesQ.isError → storagesError, `f.operation === "sync"` → op === "sync",
// `allowedPairs !== null && allowedPairs.length === 0` → noPairs.
function legacy(i: SubmitGateInput): { blocked: boolean; hints: string[] } {
  const targetInvalid = i.targetMissing || i.pathInvalid || (i.op === "sync" && i.pairBlocked);
  const blocked = i.isPending || i.storagesError
    || (i.op === "sync" && i.pairsError)
    || i.optionsInvalid || targetInvalid;
  const hints: string[] = [];
  if (i.isPending) hints.push("제출 중…");
  if (i.storagesError) hints.push("스토리지 목록 조회 실패 — 새로고침 후 다시 시도하세요");
  if (i.op === "sync" && i.pairsError) hints.push("허용 조합 조회 실패 — 새로고침 후 다시 시도하세요");
  if (i.op === "sync" && i.noPairs)
    hints.push("허용된 sync 조합이 없어 지금은 제출할 수 없습니다");
  else if (i.op === "sync" && i.pairBlocked) hints.push("허용된 조합으로 소스·목적지를 다시 고르세요");
  else if (i.targetMissing)
    hints.push(i.op === "sync" ? "소스·목적지 스토리지와 경로를 모두 입력하세요"
      : "스토리지와 대상 경로를 입력하세요");
  if (i.pathInvalid) hints.push("경로를 관리 디렉토리 아래 상대경로로 고치세요");
  if (i.optionsInvalid) hints.push("빨간 안내가 붙은 옵션 값을 고치세요");
  return { blocked, hints };
}

const BOOL_LEAVES = [
  "isPending", "storagesError", "pairsError", "noPairs", "pairBlocked", "targetMissing", "pathInvalid", "optionsInvalid",
] as const;
const OPS: SubmitOperation[] = ["sync", "scan", "rm"];

function* allInputs(): Generator<SubmitGateInput> {
  for (let mask = 0; mask < 1 << BOOL_LEAVES.length; mask++) {
    const b = Object.fromEntries(BOOL_LEAVES.map((k, n) => [k, (mask & (1 << n)) !== 0])) as Record<typeof BOOL_LEAVES[number], boolean>;
    for (const op of OPS) yield { ...b, op };
  }
}

test("잎 조합 전수: 합집합 불변식 · 막힌 단계엔 바 이유 · 검토 목록 = 옛 hints · 최종 게이트 = 옛 blocked", () => {
  const failures: string[] = [];
  let n = 0;
  for (const i of allInputs()) {
    n += 1;
    const g = submitGates(i);
    const old = legacy(i);
    const sync = i.op === "sync";
    const targetInvalid = i.targetMissing || i.pathInvalid || (sync && i.pairBlocked);
    // ① 옛 blocked 식에서 isPending 항만 뺀 것
    const union = g.gate.kind || g.gate.target || g.gate.options;
    if (union !== (i.storagesError || (sync && i.pairsError) || i.optionsInvalid || targetInvalid))
      failures.push(`union ${JSON.stringify(i)}`);
    if (g.blocked !== old.blocked) failures.push(`blocked ${JSON.stringify(i)}`);
    if (g.blocked !== (i.isPending || union)) failures.push(`blocked=pending||union ${JSON.stringify(i)}`);
    // ② 막힌 단계의 바에는 늘 이유가 있다
    for (const s of ["kind", "target", "options"] as GatedStep[]) {
      if (!g.gate[s]) continue;
      if (!g.hintItems.some((h) => h.step === s)) failures.push(`no bar reason ${s} ${JSON.stringify(i)}`);
      if (g.barReason(s) === null) failures.push(`barReason null ${s} ${JSON.stringify(i)}`);
    }
    // ③ 검토 목록 = 옛 hints(순서·문장)
    if (JSON.stringify(g.hints) !== JSON.stringify(old.hints)) failures.push(`hints ${JSON.stringify(i)}`);
    // 검토 단계 바 이유 = 검토 목록의 첫 줄(없으면 null)
    if (g.barReason("review") !== (old.hints[0] ?? null)) failures.push(`review reason ${JSON.stringify(i)}`);
    // ① 작업 종류에는 막는 입력이 없다(실행 신원은 서버가 판정)
    if (g.gate.kind) failures.push(`kind gated ${JSON.stringify(i)}`);
    // 허용 쌍 잎은 sync 에서만 뜻이 있다 -- scan·rm 에서 바뀌어도 게이트·문장이 그대로
    if (!sync) {
      const flipped = submitGates({ ...i, pairsError: !i.pairsError, noPairs: !i.noPairs, pairBlocked: !i.pairBlocked });
      if (flipped.blocked !== g.blocked || JSON.stringify(flipped.hints) !== JSON.stringify(g.hints))
        failures.push(`pair leaves leak into ${i.op} ${JSON.stringify(i)}`);
    }
    if (failures.length > 20) break;
  }
  expect(failures).toEqual([]);
  expect(n).toBe((1 << BOOL_LEAVES.length) * OPS.length);
});

const base: SubmitGateInput = {
  isPending: false, storagesError: false, op: "sync", pairsError: false, noPairs: false, pairBlocked: false,
  targetMissing: false, pathInvalid: false, optionsInvalid: false,
};

test("단계 태그: 목록·허용 쌍·대상 문장 → 대상, 옵션 → 옵션, 「제출 중…」 → 검토", () => {
  const g = submitGates({ ...base, isPending: true, storagesError: true, pairsError: true, targetMissing: true, pathInvalid: true,
                          optionsInvalid: true });
  expect(g.hintItems.map((h) => [h.step, h.text])).toEqual([
    ["review", "제출 중…"],
    ["target", "스토리지 목록 조회 실패 — 새로고침 후 다시 시도하세요"],
    ["target", "허용 조합 조회 실패 — 새로고침 후 다시 시도하세요"],
    ["target", "소스·목적지 스토리지와 경로를 모두 입력하세요"],
    ["target", "경로를 관리 디렉토리 아래 상대경로로 고치세요"],
    ["options", "빨간 안내가 붙은 옵션 값을 고치세요"],
  ]);
  expect(g.gate).toEqual({ kind: false, target: true, options: true });
  expect(g.barReason("kind")).toBeNull();
  expect(g.barReason("target")).toBe("스토리지 목록 조회 실패 — 새로고침 후 다시 시도하세요");
  expect(g.barReason("options")).toBe("빨간 안내가 붙은 옵션 값을 고치세요");
  expect(g.barReason("review")).toBe("제출 중…");
});

test("허용 조합: 없음이 해제·미입력 문장보다 앞서고, 허용 밖 조합만으로도 대상 단계가 막힌다(sync 만)", () => {
  // 허용 쌍이 하나도 없으면(선택지가 비어 대상 미입력) 바는 「없다」를 말한다 -- 「입력하세요」는 할 수 없는 일을 시킨다.
  const none = submitGates({ ...base, noPairs: true, targetMissing: true });
  expect(none.gate.target).toBe(true);
  expect(none.barReason("target")).toBe("허용된 sync 조합이 없어 지금은 제출할 수 없습니다");
  expect(none.hints).toEqual(["허용된 sync 조합이 없어 지금은 제출할 수 없습니다"]);
  // 화면에 있는 동안 허용이 해제됨(재조회): 소스·목적지는 다 골랐지만 허용 밖 -- 대상 단계가 막히고 다시 고르라고 한다.
  const revoked = submitGates({ ...base, pairBlocked: true });
  expect(revoked.gate).toEqual({ kind: false, target: true, options: false });
  expect(revoked.blocked).toBe(true);
  expect(revoked.barReason("target")).toBe("허용된 조합으로 소스·목적지를 다시 고르세요");
  // 허용 쌍은 sync 의 제약이다 -- rm 으로 바꾸면 남은 잎이 막지 않는다(옛 식의 `f.operation === "sync" &&`)
  expect(submitGates({ ...base, op: "rm", pairBlocked: true, pairsError: true }).blocked).toBe(false);
  expect(submitGates({ ...base, op: "rm", targetMissing: true }).barReason("target")).toBe("스토리지와 대상 경로를 입력하세요");
});

test("아무것도 막지 않으면 게이트 셋 다 거짓·이유 없음 -- 검토 바는 doneHint 를 쓴다", () => {
  const g = submitGates(base);
  expect(g.gate).toEqual({ kind: false, target: false, options: false });
  expect(g.blocked).toBe(false);
  expect(g.hints).toEqual([]);
  for (const s of ["kind", "target", "options", "review"] as const) expect(g.barReason(s)).toBeNull();
});
