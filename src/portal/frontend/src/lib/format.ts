// Shared date/time formatting for the operator UI. ONE source so every screen
// renders timestamps identically — Korean locale for absolute times, and relative
// times that roll up minutes → hours → DAYS (not "108시간 전"). Previously this was
// copy-pasted ~7 times with two different fmtAgo behaviors (some rolled to days,
// the dashboard/activity one didn't) and en-US `toLocaleString()` everywhere.

// Absolute timestamp in Korean locale (e.g. "2026. 7. 8. 오전 7:41:15"). Returns
// "—" for empty and passes an unparseable value through unchanged.
export function fmtTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("ko-KR");
}

// Relative "N초/분/시간/일 전", rolling up so a multi-day-old item reads "5일 전"
// instead of "108시간 전". "—" for empty; passes an unparseable value through.
export function fmtAgo(iso?: string | null): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return typeof iso === "string" ? iso : "—";
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return `${s}초 전`;
  if (s < 3600) return `${Math.round(s / 60)}분 전`;
  if (s < 86400) return `${Math.round(s / 3600)}시간 전`;
  return `${Math.round(s / 86400)}일 전`;
}
