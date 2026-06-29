import type { AtimeBucket } from "../../../api";
import { fmtBytes } from "./helpers";

// dscan's atime histogram has 9 fixed buckets in report order index 0
// (most recent / hot) … 8 (oldest / cold). Each bucket carries the total file
// CAPACITY (bytes) of files in that access-age band. Color them hot→cold.
const HOT_COLD = [
  "#ef4444", // ≤1d   hot
  "#f97316", // ≤7d
  "#f59e0b", // ≤30d
  "#eab308", // ≤90d
  "#84cc16", // ≤180d
  "#22c55e", // ≤1y
  "#14b8a6", // ≤3y
  "#0ea5e9", // ≤10y
  "#6366f1", // 10y+  cold
];

// Friendly Korean label for a bucket, derived from its day bounds.
const DAY_LABEL: Record<number, string> = {
  1: "≤1일",
  7: "≤7일",
  30: "≤30일",
  90: "≤90일",
  180: "≤180일",
  365: "≤1년",
  1095: "≤3년",
  3650: "≤10년",
};
function bucketLabel(b: AtimeBucket): string {
  if (b.max_age_days == null) return "10년+"; // open-ended oldest bucket
  return DAY_LABEL[b.max_age_days] ?? `≤${b.max_age_days}일`;
}

const totalBytes = (h: AtimeBucket[]) => h.reduce((s, b) => s + (b.bytes ?? 0), 0);

// Compact stacked hot→cold capacity bar for a table row — segment widths ∝ bytes
// per access-age band, each a tooltip. "—" when there's no histogram (older jobs)
// or no file capacity.
export function ScanHistBar({ hist }: { hist?: AtimeBucket[] | null }) {
  if (!hist || hist.length === 0) return <span className="muted small">—</span>;
  const sum = totalBytes(hist);
  if (!sum) return <span className="muted small">—</span>;
  return (
    <div className="ahist-bar" title="atime 데이터 온도 (용량) — hot(최근 접근) → cold(오래 미접근)">
      {hist.map((b, i) =>
        b.bytes ? (
          <span
            key={i}
            className="ahist-seg"
            style={{ width: `${((b.bytes ?? 0) / sum) * 100}%`, background: HOT_COLD[i] ?? "#64748b" }}
            title={`${bucketLabel(b)}: ${fmtBytes(b.bytes)}`}
          />
        ) : null,
      )}
    </div>
  );
}

// Full labeled histogram for the expanded detail: one row per bucket with a
// hot→cold bar normalized to the largest bucket + that band's capacity.
export function ScanHistFull({ hist }: { hist?: AtimeBucket[] | null }) {
  if (!hist || hist.length === 0 || !totalBytes(hist)) return null;
  const max = Math.max(1, ...hist.map((b) => b.bytes ?? 0));
  return (
    <div className="ahist-full">
      {hist.map((b, i) => (
        <div className="ahist-row" key={i}>
          <span className="ahist-label">{bucketLabel(b)}</span>
          <span className="ahist-track">
            <span
              className="ahist-fill"
              style={{ width: `${((b.bytes ?? 0) / max) * 100}%`, background: HOT_COLD[i] ?? "#64748b" }}
            />
          </span>
          <span className={`ahist-count${b.bytes ? "" : " muted"}`}>{fmtBytes(b.bytes)}</span>
        </div>
      ))}
    </div>
  );
}
