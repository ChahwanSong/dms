// 단일 작업(SubmitJob) 단계별 등록의 게이트·이유 문구(2026-11-26 -- 사용자 결정 「초안 1 상단 스테퍼」, design-wizard §1-4).
//
// **새 판정은 하나도 없다.** 화면이 지금처럼 잎 값(불리언·열거)을 계산해 넘기면, 이 함수는 옛 한 장 시트의 제출 게이트(blocked)와
// 이유 목록(hints)을 **같은 식·같은 문장·같은 순서**로 만들고, 각 이유에 「어느 단계에서 고치나」 태그만 붙인다. 컴포넌트 밖 순수 함수로
// 뗀 이유: 검토 단계에서는 게이트가 참인 상태가 UI 로 거의 관찰되지 않는다(앞 단계가 막히면 검토에 못 닿는다) -- 잎 조합 전수로
// 성질을 시험한다(submitGates.test.ts).
//
// 성질(시험이 잎 조합 전수로 본다):
//   ① 합집합 불변식: gate.kind || gate.target || gate.options === blocked 식에서 isPending 항만 뺀 것
//      (storagesBlock || optionsInvalid || targetInvalid). 검토(최종) 게이트 = blocked -- 제출 버튼은 지금처럼 disabled={blocked}.
//   ② 막힌 단계의 바에는 늘 이유가 있다: gate[s] 면 step === s 이고 scope !== "review" 인 항목이 하나 이상.
//   ③ 검토 목록(hints = scope !== "bar" 의 text) = 옛 hints 배열(순서·문장 그대로).
//
// storageOwnerBad(목적지 스토리지 기본 소유 값이 무효)는 「고칠 수 있는 곳」에 붙인다: chown 을 쓸 수 있는 특권 운영자
// (ownerModeEditable = canRoot)는 옵션 단계 고급 옵션의 chown 이 우회로다(SubmitJob 의 2026-11-11 주석 -- 대상 단계에서 막으면 그
// 우회로가 다시 막힌다). 그 밖의 역할은 대상 단계. 운영자 갈래만 바 문장이 다르다 -- 원문(「관리자에게 … 요청하세요」)은 운영자 본인이
// 관리자라 왜·어디서 풀지 말하지 못한다. 원문은 scope "review" 로 검토 목록에 그대로 남기고, 바에는 scope "bar" 로 운영자 문장을 쓴다
// (앞부분을 원문과 같게 둬 「목적지 스토리지의 기본 소유 값이 올바르지 않습니다」 단언이 두 자리 모두에서 산다).

export type SubmitStep = "kind" | "target" | "options" | "review";
export type GatedStep = Exclude<SubmitStep, "review">;
export type ScopeProblem = null | "reserved" | "numeric_only" | "format";
export type SubmitOperation = "sync" | "scan" | "rm";

/** 화면이 계산하는 잎 값(SubmitJob 의 파생값 그대로 -- 식은 화면에 있고 여기는 조합만 한다). */
export interface SubmitGateInput {
  isPending: boolean;
  storagesBlock: boolean;          // 스토리지 목록 조회 실패 && 직접 경로 쪽이 있음
  scopeProblem: ScopeProblem;      // 면제자 「권한 기준 사용자」 거부 이유
  k8sCatalogError: boolean;        // PV 목록 조회 실패 && PV 쪽이 있음
  catalogUnsynced: boolean;        // [정보] 목록 준비됨 && 모든 클러스터 미동기화 && PV 쪽이 있음
  catalogNoNs: boolean;            // [정보] 목록 준비됨 && 권한 namespace 0개 && PV 쪽이 있음
  targetMissing: boolean;
  op: SubmitOperation;
  anyRaw: boolean;                 // 직접 경로 쪽이 있음(대상 미입력 문장 갈래)
  pvBad: boolean;
  pvLoading: boolean;
  k8sRmRoot: boolean;
  k8sSubBad: boolean;
  storageOwnerBad: boolean;
  ownerModeEditable: boolean;      // 특권 운영자(chown 을 쓸 수 있다)
  pvDest: boolean;                 // PV 목적지 sync(운영자 바 문장의 「PV 루트 소유자」 갈래)
  pathInvalid: boolean;
  optionsInvalid: boolean;
}

/** 이유 한 줄. scope: both(기본) · bar(그 단계 바 전용 -- 검토 목록에 안 나옴) · review(검토 목록 전용). */
export interface HintItem {
  step: SubmitStep;
  text: string;
  scope: "both" | "bar" | "review";
}

export interface SubmitGates {
  gate: Record<GatedStep, boolean>;
  blocked: boolean;
  hintItems: HintItem[];
  /** 검토 목록(SubmitHints) = scope !== "bar" 의 text -- 옛 hints 배열과 같다. */
  hints: string[];
  /** 단계 바의 첫 이유(그 단계 태그 && scope !== "review"). 검토 단계는 검토 목록의 첫 줄. */
  barReason: (s: SubmitStep) => string | null;
}

export const STORAGE_OWNER_BAD_HINT =
  "목적지 스토리지의 기본 소유 값이 올바르지 않습니다 — 관리자에게 스토리지 설정 확인을 요청하세요";
// 운영자 바 문장(위 머리 주석). PV 목적지면 우회로가 둘(chown·② 결과 소유 「PV 루트 소유자」), 직접 경로 목적지는 chown 하나(그쪽엔
// 「PV 루트 소유자」 선택이 없다).
export const STORAGE_OWNER_BAD_OPERATOR_PV =
  "목적지 스토리지의 기본 소유 값이 올바르지 않습니다 — 고급 옵션의 chown 에 숫자 uid:gid 를 지정하거나 ② 결과 소유를 「PV 루트 소유자」로 고르세요";
export const STORAGE_OWNER_BAD_OPERATOR_DIRECT =
  "목적지 스토리지의 기본 소유 값이 올바르지 않습니다 — 고급 옵션의 chown 에 숫자 uid:gid 를 지정하세요";

export function submitGates(i: SubmitGateInput): SubmitGates {
  const ownerBadAt: GatedStep = i.ownerModeEditable ? "options" : "target";
  const scopeInvalid = i.scopeProblem !== null;
  const k8sPvBad = i.pvBad || i.pvLoading;
  const gate: Record<GatedStep, boolean> = {
    kind: scopeInvalid,
    target: i.storagesBlock || i.targetMissing || i.pathInvalid || k8sPvBad || i.k8sSubBad || i.k8sRmRoot
      || i.k8sCatalogError || (i.storageOwnerBad && ownerBadAt === "target"),
    options: i.optionsInvalid || (i.storageOwnerBad && ownerBadAt === "options"),
  };
  // 옛 SubmitJob 의 targetInvalid·blocked 식 그대로(제출 게이트는 이 하나다).
  const targetInvalid = i.targetMissing || i.pathInvalid || k8sPvBad || i.k8sSubBad || i.k8sRmRoot || i.k8sCatalogError
    || scopeInvalid || i.storageOwnerBad;
  const blocked = i.isPending || i.storagesBlock || i.optionsInvalid || targetInvalid;

  // 이유 목록 -- 옛 hints 의 순서·문장 그대로, 단계 태그만 붙인다. 필드 옆 빨간 문구와 **다른 문장**으로 짧게(SubmitHints 계약).
  const items: HintItem[] = [];
  const add = (step: SubmitStep, text: string, scope: HintItem["scope"] = "both") => items.push({ step, text, scope });
  if (i.isPending) add("review", "제출 중…");
  if (i.storagesBlock) add("target", "스토리지 목록 조회 실패 — 새로고침 후 다시 시도하세요");
  if (i.scopeProblem === "reserved") add("kind", "권한 기준 사용자에 시스템 계정 이름이 아닌 계정 아이디를 적으세요");
  else if (i.scopeProblem === "numeric_only") add("kind", "권한 기준 사용자에 숫자만이 아닌 계정 아이디를 적으세요");
  else if (i.scopeProblem === "format") add("kind", "권한 기준 사용자를 계정 아이디 형식으로 고치세요");
  if (i.k8sCatalogError) add("target", "PV 목록 조회 실패 — 새로고침 후 다시 시도하세요");
  else if (i.catalogUnsynced) add("target", "k8s 목록이 아직 동기화되지 않아 PV 를 고를 수 없습니다");
  else if (i.catalogNoNs) add("target", "권한 있는 namespace 가 없어 지금은 PV 를 고를 수 없습니다");
  if (i.targetMissing)
    add("target", i.op === "sync"
      ? (i.anyRaw ? "소스·목적지를 모두 고르고 경로를 입력하세요" : "소스·목적지 PV 를 모두 고르세요")
      : i.anyRaw ? "스토리지와 대상 경로를 입력하세요" : "대상 PV 를 고르세요");
  if (i.pvBad) add("target", "고른 PV 를 쓸 수 없습니다 — 다시 고르세요");
  else if (i.pvLoading) add("target", "고른 PV 정보를 불러오는 중…");
  if (i.k8sRmRoot) add("target", "삭제할 PV 안 경로를 입력하세요 — PV 전체는 삭제할 수 없습니다");
  if (i.k8sSubBad) add("target", "PV 안 경로를 PV 아래 상대경로로 고치세요");
  if (i.storageOwnerBad) {
    if (ownerBadAt === "options") {
      add("options", STORAGE_OWNER_BAD_HINT, "review");
      add("options", i.pvDest ? STORAGE_OWNER_BAD_OPERATOR_PV : STORAGE_OWNER_BAD_OPERATOR_DIRECT, "bar");
    } else {
      add("target", STORAGE_OWNER_BAD_HINT);
    }
  }
  if (i.pathInvalid) add("target", "경로를 관리 디렉토리 아래 상대경로로 고치세요");
  if (i.optionsInvalid) add("options", "빨간 안내가 붙은 옵션 값을 고치세요");

  const hints = items.filter((h) => h.scope !== "bar").map((h) => h.text);
  const barReason = (s: SubmitStep): string | null => {
    if (s === "review") return hints[0] ?? null;
    return items.find((h) => h.step === s && h.scope !== "review")?.text ?? null;
  };
  return { gate, blocked, hintItems: items, hints, barReason };
}
