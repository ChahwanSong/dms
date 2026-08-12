export const TERMINAL_STATES = new Set([
  "Succeeded", "Failed", "Rejected", "Cancelled", "PreviewExpired",
]);
export const isTerminal = (s: string) => TERMINAL_STATES.has(s);

export type PillVariant = "ok" | "bad" | "busy" | "neutral";
export function pillVariant(state: string): PillVariant {
  if (state === "Succeeded") return "ok";
  if (["Failed", "Rejected", "Cancelled", "PreviewExpired"].includes(state)) return "bad";
  if (["Executing", "ConfirmPending", "Planning", "Scheduled"].includes(state)) return "busy";
  return "neutral";
}

// M5: 빌드 상태(Pending/Running/Succeeded/Failed)만을 위한 별도 매핑이다. 빌드의
// "Pending"/"Running"은 잡/요청의 동명 상태와 문자열이 같지만(도메인의 StrEnum 값이
// 겹친다), 공유 pillVariant를 고치면 잡/요청 화면의 Pending/Running 배지(테스트로
// neutral이 고정돼 있다)까지 바뀐다 -- 그래서 빌드 전용 함수를 따로 둔다.
export function buildPillVariant(state: string): PillVariant {
  if (state === "Succeeded") return "ok";
  if (state === "Failed") return "bad";
  if (state === "Pending" || state === "Running") return "busy";
  return "neutral";
}

// 슬라이스 26: 스토리지 상태(Ready/Degraded/Unknown — reconciler.py:20-27)만을 위한
// 별도 매핑이다. 공유 pillVariant를 고치면 잡/요청 배지까지 바뀌므로 건드리지 않는다
// (buildPillVariant 와 같은 M5 관례). Degraded 를 bad(적색)가 아니라 busy(황색 주의)로
// 두는 이유: planner 는 Degraded 스토리지에도 잡을 보낸다(planner.py:149) --
// "죽음"이 아니라 "주의"가 정직하다. Unknown(증거 없음)은 그 외와 함께 neutral.
export function storagePillVariant(status: string): PillVariant {
  if (status === "Ready") return "ok";
  if (status === "Degraded") return "busy";
  return "neutral";
}
