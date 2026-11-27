// 단일 작업(SubmitJob) 단계별 등록의 게이트·이유 문구(dms-k8s 219e8870 이식, 2026-11-27 -- 그 라인의 사용자 결정 「초안 1 상단 스테퍼」).
//
// **새 판정은 하나도 없다.** 화면이 지금처럼 잎 값(불리언·열거)을 계산해 넘기면, 이 함수는 옛 한 장 시트의 제출 게이트(blocked)와
// 이유 목록(hints)을 **같은 식·같은 문장·같은 순서**로 만들고, 각 이유에 「어느 단계에서 고치나」 태그만 붙인다. 컴포넌트 밖 순수 함수로
// 뗀 이유: 검토 단계에서는 게이트가 참인 상태가 UI 로 거의 관찰되지 않는다(앞 단계가 막히면 검토에 못 닿는다) -- 잎 조합 전수로
// 성질을 시험한다(submitGates.test.ts).
//
// 성질(시험이 잎 조합 전수로 본다):
//   ① 합집합 불변식: gate.kind || gate.target || gate.options === 옛 blocked 식에서 isPending 항만 뺀 것
//      (storagesError || (sync && pairsError) || optionsInvalid || targetInvalid). 검토(최종) 게이트 = blocked -- 제출 버튼은 지금처럼
//      disabled={blocked}.
//   ② 막힌 단계의 바에는 늘 이유가 있다: gate[s] 면 step === s 인 항목이 하나 이상.
//   ③ 검토 목록(hints) = 옛 hints 배열(순서·문장 그대로).
//
// dms-k8s 와 다른 잎(dms-ssc 는 LDAP 신원 + 스토리지 직접 고르기 + 사용자 sync 허용 쌍이다 -- PV·권한 기준 사용자·스토리지 기본 소유가
// 없다): 사용자 sync 허용 쌍의 조회 실패(pairsError)·허용 조합 없음(noPairs)·허용 밖 조합(pairBlocked)이 대상 단계를 막는다. 허용 쌍
// **조회 중**(pairsPending)은 잎이 아니다 -- 그동안 sync 스토리지 선택지가 비어 있고 고르기 칸이 「불러오는 중…」으로 잠겨 있어
// targetMissing 이 늘 참이다(첫 조회 전엔 고른 값이 있을 수 없다: 값은 빈 문자열로 시작하고 선택지가 비어 있다). 그래서 대상 단계는
// targetMissing 으로 막히고 이 함수의 식은 옛 게이트와 글자 그대로 같다(조회 중을 잎으로 더하면 「옛 게이트와 같다」를 잎 조합 전수로
// 못 지킨다).
// ① 작업 종류(연산 + 관리자의 실행 신원·root 실행)에는 화면이 막는 입력이 없다 -- gate.kind 는 늘 거짓이다(실행 신원은 서버가
// 판정한다: 403 privileged_not_authorized·LDAP 조회 실패). 칸이 생기면 여기에 잎을 더하고 시험의 기준 구현도 같이 바꾼다.

export type SubmitStep = "kind" | "target" | "options" | "review";
export type GatedStep = Exclude<SubmitStep, "review">;
export type SubmitOperation = "sync" | "scan" | "rm";

/** 화면이 계산하는 잎 값(SubmitJob 의 파생값 그대로 -- 식은 화면에 있고 여기는 조합만 한다). */
export interface SubmitGateInput {
  isPending: boolean;
  storagesError: boolean;          // 스토리지 목록 조회 실패(storagesQ.isError -- 연산과 무관하게 막는다, 옛 식 그대로)
  op: SubmitOperation;
  pairsError: boolean;             // 사용자 sync 허용 쌍 조회 실패(관리자는 조회하지 않아 늘 거짓) -- sync 일 때만 막는다
  noPairs: boolean;                // [문장] 허용 쌍 제한이 있고 허용 조합이 0개 -- sync 일 때만 쓰인다(막힘은 targetMissing·pairBlocked 몫)
  pairBlocked: boolean;            // 고른 소스·목적지가 허용 밖(목록 재조회로 해제된 경우 포함) -- sync 일 때만 막는다
  targetMissing: boolean;
  pathInvalid: boolean;
  optionsInvalid: boolean;
}

/** 이유 한 줄과 그것을 고칠 단계. */
export interface HintItem {
  step: SubmitStep;
  text: string;
}

export interface SubmitGates {
  gate: Record<GatedStep, boolean>;
  blocked: boolean;
  hintItems: HintItem[];
  /** 검토 목록(SubmitHints) = 옛 hints 배열과 같다. */
  hints: string[];
  /** 단계 바의 첫 이유(그 단계 태그). 검토 단계는 검토 목록의 첫 줄. */
  barReason: (s: SubmitStep) => string | null;
}

export function submitGates(i: SubmitGateInput): SubmitGates {
  const sync = i.op === "sync";
  // 옛 SubmitJob 의 targetInvalid·blocked 식 그대로(제출 게이트는 이 하나다).
  const targetInvalid = i.targetMissing || i.pathInvalid || (sync && i.pairBlocked);
  const gate: Record<GatedStep, boolean> = {
    kind: false,
    target: i.storagesError || (sync && i.pairsError) || targetInvalid,
    options: i.optionsInvalid,
  };
  const blocked = i.isPending || i.storagesError || (sync && i.pairsError) || i.optionsInvalid || targetInvalid;

  // 이유 목록 -- 옛 hints 의 순서·문장 그대로, 단계 태그만 붙인다. 필드 옆 빨간 문구와 **다른 문장**으로 짧게(SubmitHints 계약).
  // 대상 미입력 안내만은 원문이 여기 한 곳에 산다.
  const items: HintItem[] = [];
  const add = (step: SubmitStep, text: string) => items.push({ step, text });
  if (i.isPending) add("review", "제출 중…");
  if (i.storagesError) add("target", "스토리지 목록 조회 실패 — 새로고침 후 다시 시도하세요");
  if (sync && i.pairsError) add("target", "허용 조합 조회 실패 — 새로고침 후 다시 시도하세요");
  if (sync && i.noPairs) add("target", "허용된 sync 조합이 없어 지금은 제출할 수 없습니다");
  else if (sync && i.pairBlocked) add("target", "허용된 조합으로 소스·목적지를 다시 고르세요");
  else if (i.targetMissing)
    add("target", sync ? "소스·목적지 스토리지와 경로를 모두 입력하세요" : "스토리지와 대상 경로를 입력하세요");
  if (i.pathInvalid) add("target", "경로를 관리 디렉토리 아래 상대경로로 고치세요");
  if (i.optionsInvalid) add("options", "빨간 안내가 붙은 옵션 값을 고치세요");

  const hints = items.map((h) => h.text);
  const barReason = (s: SubmitStep): string | null => {
    if (s === "review") return hints[0] ?? null;
    return items.find((h) => h.step === s)?.text ?? null;
  };
  return { gate, blocked, hintItems: items, hints, barReason };
}
