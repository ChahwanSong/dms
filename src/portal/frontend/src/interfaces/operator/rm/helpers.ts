// 데이터 삭제 탭 helpers: job-state labels/tones + small formatters. Reuses the generic
// `.reqa-badge is-*` pill tones from styles.css (activity view). Mirrors sync/helpers.

export const RM_STATE_LABEL: Record<string, string> = {
  registered: "대기",
  preview_pending: "프리뷰 중",
  preview_ready: "확인 대기",
  preview_failed: "프리뷰 실패",
  running: "삭제 중",
  succeeded: "삭제 완료",
  failed: "실패",
  cancelled: "취소",
};

// terminal states (no more transitions; UI stops polling).
export const RM_TERMINAL = new Set([
  "succeeded",
  "failed",
  "preview_failed",
  "cancelled",
]);

export function rmStateTone(s: string): string {
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

export function fmtTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function fmtAgo(iso?: string | null): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return `${s}초 전`;
  if (s < 3600) return `${Math.round(s / 60)}분 전`;
  if (s < 86400) return `${Math.round(s / 3600)}시간 전`;
  return `${Math.round(s / 86400)}일 전`;
}
