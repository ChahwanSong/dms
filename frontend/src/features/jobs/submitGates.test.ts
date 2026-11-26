import { expect, test } from "vitest";
import {
  STORAGE_OWNER_BAD_HINT, STORAGE_OWNER_BAD_OPERATOR_DIRECT, STORAGE_OWNER_BAD_OPERATOR_PV, submitGates,
} from "./submitGates";
import type { GatedStep, ScopeProblem, SubmitGateInput, SubmitOperation } from "./submitGates";

// 단일 작업 단계별 등록의 게이트 순수 함수(2026-11-26, design-wizard §1-4·§6-3). 잎 조합 **전수**로 세 성질을 본다 -- 검토 단계에서는
// 게이트가 참인 상태가 UI 로 거의 관찰되지 않아(앞 단계가 막히면 검토에 못 닿는다) 화면 시험만으로는 「합집합 = 옛 제출 게이트」를
// 지킬 수 없다.

// 옛 SubmitJob(a13ebab9 :298-305·:429-451)의 blocked·hints 를 글자 그대로 옮긴 기준 구현 -- 새 함수가 이것과 갈라지면 빨갛다.
function legacy(i: SubmitGateInput): { blocked: boolean; hints: string[] } {
  const scopeInvalid = i.scopeProblem !== null;
  const k8sPvBad = i.pvBad || i.pvLoading;
  const targetInvalid = i.targetMissing || i.pathInvalid
    || k8sPvBad || i.k8sSubBad || i.k8sRmRoot || i.k8sCatalogError || scopeInvalid || i.storageOwnerBad;
  const blocked = i.isPending || i.storagesBlock || i.optionsInvalid || targetInvalid;
  const hints: string[] = [];
  if (i.isPending) hints.push("제출 중…");
  if (i.storagesBlock) hints.push("스토리지 목록 조회 실패 — 새로고침 후 다시 시도하세요");
  if (i.scopeProblem === "reserved") hints.push("권한 기준 사용자에 시스템 계정 이름이 아닌 계정 아이디를 적으세요");
  else if (i.scopeProblem === "numeric_only") hints.push("권한 기준 사용자에 숫자만이 아닌 계정 아이디를 적으세요");
  else if (i.scopeProblem === "format") hints.push("권한 기준 사용자를 계정 아이디 형식으로 고치세요");
  if (i.k8sCatalogError) hints.push("PV 목록 조회 실패 — 새로고침 후 다시 시도하세요");
  else if (i.catalogUnsynced) hints.push("k8s 목록이 아직 동기화되지 않아 PV 를 고를 수 없습니다");
  else if (i.catalogNoNs) hints.push("권한 있는 namespace 가 없어 지금은 PV 를 고를 수 없습니다");
  if (i.targetMissing)
    hints.push(i.op === "sync"
      ? (i.anyRaw ? "소스·목적지를 모두 고르고 경로를 입력하세요" : "소스·목적지 PV 를 모두 고르세요")
      : i.anyRaw ? "스토리지와 대상 경로를 입력하세요" : "대상 PV 를 고르세요");
  if (i.pvBad) hints.push("고른 PV 를 쓸 수 없습니다 — 다시 고르세요");
  else if (i.pvLoading) hints.push("고른 PV 정보를 불러오는 중…");
  if (i.k8sRmRoot) hints.push("삭제할 PV 안 경로를 입력하세요 — PV 전체는 삭제할 수 없습니다");
  if (i.k8sSubBad) hints.push("PV 안 경로를 PV 아래 상대경로로 고치세요");
  if (i.storageOwnerBad) hints.push("목적지 스토리지의 기본 소유 값이 올바르지 않습니다 — 관리자에게 스토리지 설정 확인을 요청하세요");
  if (i.pathInvalid) hints.push("경로를 관리 디렉토리 아래 상대경로로 고치세요");
  if (i.optionsInvalid) hints.push("빨간 안내가 붙은 옵션 값을 고치세요");
  return { blocked, hints };
}

const BOOL_LEAVES = [
  "isPending", "storagesBlock", "k8sCatalogError", "catalogUnsynced", "catalogNoNs", "targetMissing", "anyRaw", "pvBad",
  "pvLoading", "k8sRmRoot", "k8sSubBad", "storageOwnerBad", "ownerModeEditable", "pvDest", "pathInvalid", "optionsInvalid",
] as const;
const SCOPES: ScopeProblem[] = [null, "reserved", "numeric_only", "format"];
const OPS: SubmitOperation[] = ["sync", "scan", "rm"];

function* allInputs(): Generator<SubmitGateInput> {
  for (let mask = 0; mask < 1 << BOOL_LEAVES.length; mask++) {
    const b = Object.fromEntries(BOOL_LEAVES.map((k, n) => [k, (mask & (1 << n)) !== 0])) as Record<typeof BOOL_LEAVES[number], boolean>;
    for (const scopeProblem of SCOPES) for (const op of OPS) yield { ...b, scopeProblem, op };
  }
}

test("잎 조합 전수: 합집합 불변식 · 막힌 단계엔 바 이유 · 검토 목록 = 옛 hints · 최종 게이트 = 옛 blocked", () => {
  const failures: string[] = [];
  let n = 0;
  for (const i of allInputs()) {
    n += 1;
    const g = submitGates(i);
    const old = legacy(i);
    const scopeInvalid = i.scopeProblem !== null;
    const targetInvalid = i.targetMissing || i.pathInvalid || i.pvBad || i.pvLoading || i.k8sSubBad || i.k8sRmRoot
      || i.k8sCatalogError || scopeInvalid || i.storageOwnerBad;
    // ① blocked 식에서 isPending 항만 뺀 것
    const union = g.gate.kind || g.gate.target || g.gate.options;
    if (union !== (i.storagesBlock || i.optionsInvalid || targetInvalid)) failures.push(`union ${JSON.stringify(i)}`);
    if (g.blocked !== old.blocked) failures.push(`blocked ${JSON.stringify(i)}`);
    if (g.blocked !== (i.isPending || union)) failures.push(`blocked=pending||union ${JSON.stringify(i)}`);
    // ② 막힌 단계의 바에는 늘 이유가 있다
    for (const s of ["kind", "target", "options"] as GatedStep[]) {
      if (!g.gate[s]) continue;
      if (!g.hintItems.some((h) => h.step === s && h.scope !== "review")) failures.push(`no bar reason ${s} ${JSON.stringify(i)}`);
      if (g.barReason(s) === null) failures.push(`barReason null ${s} ${JSON.stringify(i)}`);
    }
    // ③ 검토 목록 = 옛 hints(순서·문장)
    if (JSON.stringify(g.hints) !== JSON.stringify(old.hints)) failures.push(`hints ${JSON.stringify(i)}`);
    // 검토 단계 바 이유 = 검토 목록의 첫 줄(없으면 null)
    if (g.barReason("review") !== (old.hints[0] ?? null)) failures.push(`review reason ${JSON.stringify(i)}`);
    if (failures.length > 20) break;
  }
  expect(failures).toEqual([]);
  expect(n).toBe((1 << BOOL_LEAVES.length) * SCOPES.length * OPS.length);
});

const base: SubmitGateInput = {
  isPending: false, storagesBlock: false, scopeProblem: null, k8sCatalogError: false, catalogUnsynced: false, catalogNoNs: false,
  targetMissing: false, op: "sync", anyRaw: false, pvBad: false, pvLoading: false, k8sRmRoot: false, k8sSubBad: false,
  storageOwnerBad: false, ownerModeEditable: false, pvDest: false, pathInvalid: false, optionsInvalid: false,
};

test("단계 태그: 범위 사용자 → 작업 종류, 옵션 → 옵션, 대상 문장 → 대상, 「제출 중…」 → 검토", () => {
  const g = submitGates({ ...base, isPending: true, scopeProblem: "format", targetMissing: true, optionsInvalid: true });
  expect(g.hintItems.map((h) => [h.step, h.text])).toEqual([
    ["review", "제출 중…"],
    ["kind", "권한 기준 사용자를 계정 아이디 형식으로 고치세요"],
    ["target", "소스·목적지 PV 를 모두 고르세요"],
    ["options", "빨간 안내가 붙은 옵션 값을 고치세요"],
  ]);
  expect(g.gate).toEqual({ kind: true, target: true, options: true });
  expect(g.barReason("kind")).toBe("권한 기준 사용자를 계정 아이디 형식으로 고치세요");
  expect(g.barReason("target")).toBe("소스·목적지 PV 를 모두 고르세요");
  expect(g.barReason("options")).toBe("빨간 안내가 붙은 옵션 값을 고치세요");
  expect(g.barReason("review")).toBe("제출 중…");
});

test("storageOwnerBad: 사용자·목록 밖 관리자는 대상 단계(원문), 특권 운영자는 옵션 단계(바 = 운영자 문장, 검토 목록 = 원문)", () => {
  const user = submitGates({ ...base, storageOwnerBad: true, pvDest: true });
  expect(user.gate).toEqual({ kind: false, target: true, options: false });
  expect(user.barReason("target")).toBe(STORAGE_OWNER_BAD_HINT);
  expect(user.hints).toEqual([STORAGE_OWNER_BAD_HINT]);

  const opPv = submitGates({ ...base, storageOwnerBad: true, ownerModeEditable: true, pvDest: true });
  expect(opPv.gate).toEqual({ kind: false, target: false, options: true });
  expect(opPv.barReason("target")).toBeNull();
  expect(opPv.barReason("options")).toBe(STORAGE_OWNER_BAD_OPERATOR_PV);
  expect(opPv.hints).toEqual([STORAGE_OWNER_BAD_HINT]);           // 검토 목록은 원문 그대로
  expect(opPv.blocked).toBe(true);

  const opDirect = submitGates({ ...base, storageOwnerBad: true, ownerModeEditable: true, pvDest: false });
  expect(opDirect.barReason("options")).toBe(STORAGE_OWNER_BAD_OPERATOR_DIRECT);
  // 앞부분이 원문과 같다 -- 「목적지 스토리지의 기본 소유 값이 올바르지 않습니다」 단언이 두 자리 모두에서 산다
  for (const t of [STORAGE_OWNER_BAD_OPERATOR_PV, STORAGE_OWNER_BAD_OPERATOR_DIRECT])
    expect(t.startsWith("목적지 스토리지의 기본 소유 값이 올바르지 않습니다 — ")).toBe(true);
});

test("대상 바 이유는 옛 순서의 첫 대상 문장 -- 정보 문장(미동기화)이 미입력보다 앞선다", () => {
  const g = submitGates({ ...base, catalogUnsynced: true, targetMissing: true, op: "rm" });
  expect(g.barReason("target")).toBe("k8s 목록이 아직 동기화되지 않아 PV 를 고를 수 없습니다");
  expect(g.hints).toEqual(["k8s 목록이 아직 동기화되지 않아 PV 를 고를 수 없습니다", "대상 PV 를 고르세요"]);
});
