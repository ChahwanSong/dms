import type { AtimeBucket } from "../../../api";

// dscan's atime histogram has 9 fixed buckets, oldest→ in report order index 0
// (most recent / hot) … 8 (oldest / cold). Color them on a hot→cold ramp.
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

const nf = (n?: number | null) => (n ?? 0).toLocaleString();
const total = (h: AtimeBucket[]) => h.reduce((s, b) => s + (b.count ?? 0), 0);

// Compact stacked hot→cold bar for a table row — segment widths ∝ file counts,
// each segment a tooltip. Renders "—" when there's no histogram (older jobs).
export function ScanHistBar({ hist }: { hist?: AtimeBucket[] | null }) {
  if (!hist || hist.length === 0) return <span className="muted small">—</span>;
  const sum = total(hist);
  if (!sum) return <span className="muted small">—</span>;
  return (
    <div className="ahist-bar" title="atime 데이터 온도 — hot(최근 접근) → cold(오래 미접근)">
      {hist.map((b, i) =>
        b.count ? (
          <span
            key={i}
            className="ahist-seg"
            style={{ width: `${((b.count ?? 0) / sum) * 100}%`, background: HOT_COLD[i] ?? "#64748b" }}
            title={`${bucketLabel(b)}: ${nf(b.count)}개`}
          />
        ) : null,
      )}
    </div>
  );
}

// Full labeled histogram for the expanded detail: one row per (non-empty-overall)
// bucket with a hot→cold bar normalized to the largest bucket + the count.
export function ScanHistFull({ hist }: { hist?: AtimeBucket[] | null }) {
  if (!hist || hist.length === 0 || !total(hist)) return null;
  const max = Math.max(1, ...hist.map((b) => b.count ?? 0));
  return (
    <div className="ahist-full">
      {hist.map((b, i) => (
        <div className="ahist-row" key={i}>
          <span className="ahist-label">{bucketLabel(b)}</span>
          <span className="ahist-track">
            <span
              className="ahist-fill"
              style={{ width: `${((b.count ?? 0) / max) * 100}%`, background: HOT_COLD[i] ?? "#64748b" }}
            />
          </span>
          <span className={`ahist-count${b.count ? "" : " muted"}`}>{nf(b.count)}</span>
        </div>
      ))}
    </div>
  );
}
