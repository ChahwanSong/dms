// Canonical Korean label + tone for DMS *lifecycle* states (request / plan / run /
// transition — CamelCase enum from domain.py). The 요청(액티비티) table, its filter,
// and the request-detail timeline render these SHORT pill labels. The 조치 필요 패널
// (AttentionPanel) intentionally keeps LONGER, action-oriented titles for the subset
// of stuck states it can remediate — those are worded to AGREE (in meaning) with the
// short labels here, not contradict them. (The backup/scan/sync/rm tabs localize
// their own separate, lowercase portal-job states in their own helpers.)

export type Tone = "ok" | "warn" | "err" | "info" | "neutral";

// needs-action (amber) — the operator can act on these in 조치 필요.
const STUCK = new Set([
  "Blocked", "Conflict", "StaleClaim", "RecoveryNeeded",
  "UnknownAfterSideEffect", "BackendApplyFailed", "VerificationFailed",
]);
// terminal failures (red).
const FAIL = new Set([
  "Failed", "Rejected", "TimedOut", "AuthenticationRejected", "AuthorizationFailed",
]);
// in-flight (blue). NOTE: kept byte-identical to the original RequestsTable set so
// tone is preserved (label-only change) — "Planned" is intentionally NOT here (it
// stayed neutral before; changing it would be a tone regression, not a localization).
const ACTIVE = new Set([
  "Running", "Applying", "Verifying", "Claimed", "Pending", "PreflightRunning",
  "PreviewRunning", "ConfirmPending", "Confirmed", "Scheduled", "PreviewReady",
]);

export function isStuck(s?: string | null): boolean {
  return !!s && STUCK.has(s);
}

// lifecycle state → semantic tone (drives the .is-* pill + text classes).
export function lifecycleTone(s: string): Tone {
  if (s === "Succeeded") return "ok";
  if (FAIL.has(s)) return "err";
  if (STUCK.has(s)) return "warn";
  if (ACTIVE.has(s)) return "info";
  return "neutral";
}

// text-color class used in the detail view (dt/dd, result, transition timeline).
export function lifecycleTextClass(s: string): string {
  if (s === "Succeeded") return "ok-num";
  if (FAIL.has(s)) return "err-num";
  if (STUCK.has(s)) return "tone-warn-text";
  return "";
}

const LABEL: Record<string, string> = {
  // terminal
  Succeeded: "성공", Failed: "실패", Cancelled: "취소됨", Rejected: "거부됨",
  TimedOut: "시간 초과", AuthenticationRejected: "인증 거부", AuthorizationFailed: "인가 실패",
  // early lifecycle (neutral) — these head EVERY request's transition timeline.
  Received: "접수됨", Persisted: "저장됨", Planning: "계획 중", Planned: "계획됨",
  // in-flight
  Running: "실행 중", Applying: "적용 중", Verifying: "검증 중", Claimed: "점유됨",
  Pending: "대기", PreflightRunning: "사전점검 중",
  PreviewRunning: "프리뷰 중", PreviewReady: "프리뷰 완료", ConfirmPending: "확인 중",
  Confirmed: "확인됨", Scheduled: "예약됨",
  // needs-action / stuck — short labels that AGREE in meaning with AttentionPanel's
  // longer REQ_STATUS_LABEL titles (e.g. Blocked "차단됨" ~ "요청 차단됨").
  Blocked: "차단됨", Conflict: "충돌", StaleClaim: "리스 만료",
  RecoveryNeeded: "복구 필요", VerificationFailed: "검증 실패",
  UnknownAfterSideEffect: "결과 불명", BackendApplyFailed: "적용 실패",
};

// Korean label for a lifecycle state; passes UNKNOWN/empty values through unchanged
// (so a new backend state degrades to its raw name rather than vanishing).
export function lifecycleLabel(s?: string | null): string {
  if (!s) return s || "";
  return LABEL[s] ?? s;
}
