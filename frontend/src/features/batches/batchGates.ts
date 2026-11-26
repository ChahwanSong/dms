// 배치 생성(BatchCreate) 단계별 등록의 게이트·이유 문구(2026-11-26 -- 사용자 결정 「초안 1 상단 스테퍼」, design-wizard §1-4).
//
// **새 판정은 하나도 없다.** 화면이 지금처럼 잎 값(불리언·개수)을 계산해 넘기면, 이 함수는 옛 한 장 시트의 생성 게이트(blocked)와
// 이유 목록(hints)을 **같은 식·같은 문장·같은 순서**로 만들고, 각 이유에 「어느 단계에서 고치나」 태그만 붙인다. 단일 작업의
// features/jobs/submitGates 와 같은 모양이고 같은 이유로 컴포넌트 밖 순수 함수다 -- 검토 단계에서는 게이트가 참인 상태가 UI 로 거의
// 관찰되지 않는다(앞 단계가 막히면 검토에 못 닿는다). 성질은 잎 조합 전수로 시험한다(batchGates.test.ts).
//
// 성질:
//   ① 합집합 불변식: gate.storage || gate.items || gate.controls === blocked 식에서 createPending 항만 뺀 것
//      (parsePending || !itemsValid || controlsInvalid || storagesError || rmAutoAckMissing). 스토리지 항
//      (!storageChosen && !storageFromCsv)은 !storageChosen ⇒ !itemsValid 라 흡수된다 -- itemsValid 를 이 함수 안에서 옛 식으로
//      만드는 이유다(잎을 전수로 돌려도 「스토리지는 비었는데 항목은 유효」 같은 모순 조합이 생기지 않는다).
//   ② 막힌 단계의 바에는 늘 이유가 있다: gate[s] 면 step === s 이고 scope !== "review" 인 항목이 하나 이상.
//   ③ 검토 목록(hints = scope !== "bar" 의 text) = 옛 hints 배열(순서·문장 그대로).
//
// 「스토리지를 CSV 로 정하기」(storageFromCsv -- 화면 상태, 바디에 안 실린다): 켜면 스토리지 단계는 고르지 않아도 통과한다(CSV 의
// 스토리지 열이 채울 몫). 그래도 생성 게이트는 그대로다 -- 스토리지가 끝내 비면 항목 단계(itemsValid)가 막는다. 그때 항목 단계 바
// 이유는 바 전용 한 줄(「스토리지가 비어 있습니다 …」)이고, 탈출구를 안 켰는데 비었으면(스토리지 단계를 지난 뒤 비동기로 비는 일은
// 없지만 식은 닫아 둔다) 「② 스토리지를 먼저 고르세요」다. 검토 목록은 원문 「스토리지를 고르세요」 그대로(스토리지 태그).

export type BatchStep = "kind" | "storage" | "items" | "controls" | "review";
export type BatchGatedStep = Exclude<BatchStep, "review">;
export type BatchOperation = "scan" | "sync" | "rm";

/** 화면이 계산하는 잎 값(BatchCreate 의 파생값 그대로 -- 식은 화면에 있고 여기는 조합만 한다). */
export interface BatchGateInput {
  createPending: boolean;
  parsePending: boolean;           // CSV 서버 해석 중
  storagesError: boolean;          // 스토리지 목록 조회 실패
  storageChosen: boolean;          // scan·rm: 스토리지 하나, sync: 소스·목적지 둘 다
  storageFromCsv: boolean;         // 「스토리지를 CSV 로 정하기」 탈출구
  rowsCount: number;
  incompleteRows: number;          // 경로가 빈 행 수
  csvErrorCount: number;
  rmPathErrorCount: number;        // rm 행 즉답(관리 디렉토리 자신·「..」·「/」) -- rm 이 아니면 화면이 0 을 넘긴다
  rmOverlapCount: number;          // rm 한 배치 안 겹침 -- rm 이 아니면 0
  controlsInvalid: boolean;        // 옵션·실행 제어 즉답 오류
  rmAutoAckMissing: boolean;       // rm 자동 실행의 「되돌릴 수 없음」 동의 누락
  op: BatchOperation;
}

/** 이유 한 줄. scope: both(기본) · bar(그 단계 바 전용 -- 검토 목록에 안 나옴) · review(검토 목록 전용). */
export interface BatchHintItem {
  step: BatchStep;
  text: string;
  scope: "both" | "bar" | "review";
}

export interface BatchGates {
  gate: Record<BatchGatedStep, boolean>;
  blocked: boolean;
  itemsValid: boolean;
  hintItems: BatchHintItem[];
  /** 검토 목록(SubmitHints) = scope !== "bar" 의 text -- 옛 hints 배열과 같다. */
  hints: string[];
  /** 단계 바의 첫 이유(그 단계 태그 && scope !== "review"). 검토 단계는 검토 목록의 첫 줄. */
  barReason: (s: BatchStep) => string | null;
}

export const STORAGE_EMPTY_CSV_BAR =
  "스토리지가 비어 있습니다 — 스토리지 열이 있는 CSV 를 반영하거나 ② 스토리지에서 고르세요";
export const STORAGE_FIRST_BAR = "② 스토리지를 먼저 고르세요";

export function batchGates(i: BatchGateInput): BatchGates {
  // 옛 BatchCreate 의 rowsComplete·itemsValid·blocked 식 그대로(생성 게이트는 이 하나다).
  const rowsComplete = i.rowsCount >= 1 && i.incompleteRows === 0;
  const itemsValid = i.storageChosen && rowsComplete && i.csvErrorCount === 0
    && i.rmPathErrorCount === 0 && i.rmOverlapCount === 0;
  const gate: Record<BatchGatedStep, boolean> = {
    kind: false,
    storage: i.storagesError || (!i.storageChosen && !i.storageFromCsv),
    items: !itemsValid || i.parsePending,
    controls: i.controlsInvalid || i.rmAutoAckMissing,
  };
  const blocked = i.createPending || i.parsePending || !itemsValid || i.controlsInvalid || i.storagesError
    || i.rmAutoAckMissing;

  // 이유 목록 -- 옛 hints 의 순서·문장 그대로, 단계 태그만 붙인다. 필드 옆 빨간 문구와 **다른 문장**으로 짧게(SubmitHints 계약).
  const items: BatchHintItem[] = [];
  const add = (step: BatchStep, text: string, scope: BatchHintItem["scope"] = "both") => items.push({ step, text, scope });
  if (i.createPending) add("review", "생성 중…");
  if (i.storagesError) add("storage", "스토리지 목록 조회 실패 — 새로고침 후 다시 시도하세요");
  if (!i.storageChosen) {
    add("storage", i.op === "sync" ? "소스·목적지 스토리지를 고르세요" : "스토리지를 고르세요");
    add("items", i.storageFromCsv ? STORAGE_EMPTY_CSV_BAR : STORAGE_FIRST_BAR, "bar");
  }
  if (i.rowsCount === 0) add("items", "항목을 1개 이상 추가하세요");
  else if (i.incompleteRows > 0) add("items", `경로가 빈 항목 ${i.incompleteRows}개를 채우거나 지우세요`);
  if (i.parsePending) add("items", "CSV 검사 중…");
  if (i.csvErrorCount > 0) add("items", "CSV 오류를 고친 뒤 다시 반영하세요");
  if (i.rmPathErrorCount > 0) add("items", `삭제할 수 없는 경로 ${i.rmPathErrorCount}개를 고치세요`);
  if (i.rmOverlapCount > 0) add("items", `겹치는 삭제 경로 ${i.rmOverlapCount}개를 정리하세요`);
  if (i.controlsInvalid) add("controls", "빨간 안내가 붙은 옵션·실행 제어 값을 고치세요");
  if (i.rmAutoAckMissing) add("controls", "자동 실행으로 삭제하려면 경고를 읽고 동의를 체크하세요");

  const hints = items.filter((h) => h.scope !== "bar").map((h) => h.text);
  const barReason = (s: BatchStep): string | null => {
    if (s === "review") return hints[0] ?? null;
    return items.find((h) => h.step === s && h.scope !== "review")?.text ?? null;
  };
  return { gate, blocked, itemsValid, hintItems: items, hints, barReason };
}
