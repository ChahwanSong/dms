// 사용자 데이터 Sync helpers: job-state labels/tones + formatters. Mirrors the
// operator sync helpers (kept separate so the user interface stays self-contained).
import { ApiError } from "../../../api";

export const SYNC_STATE_LABEL: Record<string, string> = {
  registered: "대기",
  preview_pending: "프리뷰 중",
  preview_ready: "확인 대기",
  preview_failed: "프리뷰 실패",
  running: "실행 중",
  succeeded: "성공",
  failed: "실패",
  cancelled: "취소",
};

export const SYNC_TERMINAL = new Set([
  "succeeded",
  "failed",
  "preview_failed",
  "cancelled",
]);

export function syncStateTone(s: string): string {
  if (s === "succeeded") return "is-ok";
  if (s === "failed" || s === "preview_failed") return "is-err";
  if (s === "preview_ready") return "is-warn";
  if (s === "running" || s === "preview_pending" || s === "registered") return "is-info";
  return "is-neutral"; // cancelled
}

export function fmtBytes(n?: number | null): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB", "PB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 || Number.isInteger(v) ? 0 : 1)} ${units[i]}`;
}

// Turn an ApiError into a friendly Korean line (mirrors the operator errMsg).
export function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    const base = typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail);
    if (e.status === 409) return `충돌 (409): ${base}`;
    if (e.status === 422) return base; // validation detail is already user-facing
    if (e.status === 404) return `대상을 찾을 수 없음 (404): ${base}`;
    if (e.status === 503) return `DMS 미연동 (503): ${base}`;
    return `오류 ${e.status}: ${base}`;
  }
  return e instanceof Error ? e.message : "알 수 없는 오류";
}

export { fmtTime, fmtAgo } from "../../../lib/format";
