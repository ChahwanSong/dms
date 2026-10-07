export const TERMINAL_STATES = new Set([
  "Succeeded", "Failed", "Rejected", "Cancelled", "PreviewExpired",
]);
export const isTerminal = (s: string) => TERMINAL_STATES.has(s);

// 요청 전용 종단 셋(대시보드 「완료시간」 판정): 요청에는 잡에 없는 종단 Conflict 가
// 있다(domain.TERMINAL_REQUEST_STATES). 공유 TERMINAL_STATES 에 Conflict 를 넣으면
// isTerminal 을 쓰는 잡 화면의 판정(상세 폴링 중지 등)까지 바뀌므로 buildPillVariant
// 와 같은 이유(M5 관례)로 별도 셋을 둔다. PreviewExpired 는 요청 상태엔 없지만
// 합집합에 남아도 요청 상태 문자열과 겹치지 않아 무해하다.
export const REQUEST_TERMINAL_STATES = new Set([...TERMINAL_STATES, "Conflict"]);

// action = 운영자 행동이 필요한 상태(주의색 앰버 -- busy 의 연파랑·버튼의 accent 와 구별된다). 지금은 배치 「확인 대기」 하나.
export type PillVariant = "ok" | "bad" | "busy" | "neutral" | "action";
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

// 배치 상태(Running/Previewing/PreviewReady/Completed/Cancelled)만을 위한 별도
// 매핑이다. Running·Cancelled 는 잡/요청 상태와 문자열이 겹친다 — 공유 pillVariant
// 를 고치면 잡/요청 배지까지 바뀌므로 별도 함수(buildPillVariant 와 같은 M5 관례).
// Cancelled 를 bad(적색)가 아니라 neutral 로 두는 이유: 배치 취소는 운영자의
// 의도된 중지지 실패가 아니다 — 적색은 "실패"라는 거짓말이 되고, 실제 실패 수는
// 헤더의 성공/실패 카운터가 따로 말한다. 항목 상태(Queued/Materialized/Succeeded/
// Failed/Cancelled — 요청/잡 판정 축)는 이 함수의 도메인이 아니다: 그쪽은 공유
// pillVariant 를 그대로 쓴다(항목 Cancelled 는 실행이 끊긴 것이라 bad 가 정직).
// PreviewReady 는 action(2026-10-07): 운영자가 「배치 확인」을 눌러야만 실행되는 유일한 상태인데, Running 과 같은
// busy 연파랑에 영문 그대로라 "이미 도는 중"으로 읽혔다(적대적 조사). 라벨도 batchStatusLabel 이 한글로 바꾼다.
export function batchPillVariant(status: string): PillVariant {
  if (status === "Completed") return "ok";
  if (status === "PreviewReady") return "action";
  if (["Running", "Previewing"].includes(status)) return "busy";
  return "neutral";
}

// 배치 상태 표시 문자열. 행동이 필요한 PreviewReady 만 「확인 대기」로 바꾸고, 나머지는 서버 상태 그대로다(다른 화면의
// 영문 상태 표기와 같은 축 -- 원문은 StatusPill 의 title 로 남는다).
export function batchStatusLabel(status: string): string {
  return status === "PreviewReady" ? "확인 대기" : status;
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
