// 상태 → 라벨 + 색(class). 기존 san-* / ok-num / err-num 재사용.
export const RUN_STATE: Record<string, string> = {
  Claimed: "san-degraded", Running: "san-degraded", Applying: "san-degraded",
  Verifying: "san-degraded", Blocked: "san-failed", StaleClaim: "san-failed",
  RecoveryNeeded: "san-failed", Succeeded: "san-ready", Failed: "san-failed",
};
export const REQUEST_STATE: Record<string, string> = {
  Pending: "san-unknown", PreflightRunning: "san-degraded",
  PreviewRunning: "san-degraded", ConfirmPending: "san-degraded",
  Confirmed: "san-degraded", Scheduled: "san-degraded", Running: "san-degraded",
  Succeeded: "san-ready", Failed: "san-failed", Cancelled: "san-failed",
  TimedOut: "san-failed", PreflightFailed: "san-failed",
  PreviewExpired: "san-failed", AuthorizationFailed: "san-failed",
};

export function stateCls(map: Record<string, string>, s?: string): string {
  return map[s || ""] || "san-unknown";
}

export function fmtTime(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function fmtAgo(iso?: string): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return `${s}초 전`;
  if (s < 3600) return `${Math.round(s / 60)}분 전`;
  return `${Math.round(s / 3600)}시간 전`;
}

export function summarize(list?: string[], max = 3): string {
  if (!list || list.length === 0) return "—";
  return list.length <= max
    ? list.join(", ")
    : `${list.slice(0, max).join(", ")} +${list.length - max}`;
}
